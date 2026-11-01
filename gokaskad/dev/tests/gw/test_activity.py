"""The core of the activity of devices — ``kaskad.activity`` (k3/handoff/SPEC-activity.md §3.1, §8.1, ACT-4, ACT-6):
the deltas by session, the first sight counts 0, a counter that went down counts what it shows, a session of WireGuard
gone and back without loss, two sessions of a device without counting twice, «на связи» once a device, «активен» by
the threshold (keepalive is not), the time a round counts, the change of the window, the clock back, the ttl of the
past counters, the accumulator to JSON and back. No Kaskad around it: the block is portable (BLOCKS.md)."""

import json
import unittest

from kaskad import activity as A

T0 = 1791190200            # a 5-minute mark
MB = 1024 * 1024


def wg(dev, rx, tx, ip="10.77.1.10", kind="wg"):
    return {"device": dev, "kind": kind, "ip": ip, "since": T0 - 60, "rx": rx, "tx": tx}


def vless(dev, rx, tx):
    return {"device": dev, "kind": "vless", "ip": None, "since": T0 - 60, "rx": rx, "tx": tx}


def run(acc, rounds):
    """rounds: [(now, sessions)] -> the windows closed on the way."""
    out = []
    for now, ss in rounds:
        acc, w = A.step(acc, ss, now)
        if w is not None:
            out.append(w)
    return out


class FirstSightTest(unittest.TestCase):
    def test_the_first_sight_counts_nothing(self):
        """ACT-4: a collector with no past sees 40 GB on the counter of WireGuard — not a peak of 40 GB."""
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 40 * 1024 * MB, 2 * 1024 * MB)])])
        self.assertEqual(acc["cur"]["d-1"][2:], [0, 0])
        run(acc, [(T0 + 15, [wg("d-1", 40 * 1024 * MB + 5000, 2 * 1024 * MB + 7000)])])
        self.assertEqual(acc["cur"]["d-1"][2:], [5000, 7000])

    def test_a_counter_that_went_down_counts_what_it_shows(self):
        """xray restarted / the interface made anew: the counter starts from 0 — what it shows now is new traffic."""
        acc = A.new()
        run(acc, [(T0 + 5, [vless("d-1", 10 * MB, 50 * MB)]), (T0 + 15, [vless("d-1", 3000, 9000)])])
        self.assertEqual(acc["cur"]["d-1"][2:], [3000, 9000])

    def test_zeros_are_no_reading(self):
        """What only the hooks know comes with rx = tx = 0: the past counter stays, the next real one is a delta, not
        the whole counter again (a false peak)."""
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 100 * MB, 10 * MB, kind="l2tp")]), (T0 + 15, [wg("d-1", 0, 0, kind="l2tp")]),
                  (T0 + 25, [wg("d-1", 100 * MB + 4000, 10 * MB + 1000, kind="l2tp")])])
        self.assertEqual(acc["cur"]["d-1"][2:], [4000, 1000])
        self.assertEqual(acc["cur"]["d-1"][0], 20.0)          # yet «на связи» every round

    def test_a_counter_gone_wrong_is_no_traffic(self):
        """A jump far above 10 Gbit/s since the session was last seen — not traffic (no peak), the new counter is the base."""
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 1000, 1000)]), (T0 + 15, [wg("d-1", 2 ** 50, 1000)]),
                  (T0 + 25, [wg("d-1", 2 ** 50 + 100, 1100)])])
        self.assertEqual(acc["cur"]["d-1"][2:], [100, 100])


class SessionsTest(unittest.TestCase):
    def test_a_wireguard_peer_gone_and_back_loses_nothing(self):
        """A peer silent for 3 minutes leaves the sessions (handshake older than 180 s) — its counter is kept; when it
        is back the delta covers the pause."""
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 1000, 1000)]), (T0 + 15, []), (T0 + 25, []),
                  (T0 + 200, [wg("d-1", 1000 + 6000, 1000 + 2000)])])
        self.assertEqual(acc["cur"]["d-1"][2:], [6000, 2000])

    def test_two_sessions_of_a_device_and_one_goes(self):
        """WireGuard and VLESS of one device: the bytes of both, and when VLESS goes nothing of WireGuard counts again
        (the sum of a device would fall and «smaller → as it is» would count all of it)."""
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 1000, 1000), vless("d-1", 50, 50)]),
                  (T0 + 15, [wg("d-1", 2000, 3000), vless("d-1", 150, 250)]),
                  (T0 + 25, [wg("d-1", 2500, 3500)])])
        self.assertEqual(acc["cur"]["d-1"][2:], [1000 + 100 + 500, 2000 + 200 + 500])
        self.assertEqual(acc["cur"]["d-1"][0], 20.0)          # «на связи» once a round, not once a session

    def test_online_and_active(self):
        """«активен» — the bytes of the round reach 4 KB per 10 s; keepalive of WireGuard (≈ 150 B a round) is not."""
        acc = A.new()
        rounds = [(T0 + 5 + 10 * i, [wg("d-1", 1000 + 150 * i, 1000 + 150 * i), wg("d-2", 10 ** 6 * i + 1, 10 ** 5 * i + 1,
                                                                                     ip="10.77.1.11")])
                  for i in range(6)]
        run(acc, rounds)
        self.assertEqual(acc["cur"]["d-1"][:2], [50.0, 0.0])
        self.assertEqual(acc["cur"]["d-2"][:2], [50.0, 50.0])

    def test_a_skipped_round_does_not_invent_time(self):
        """gap_cap: a round 5 minutes after the last one counts 30 s, not 300."""
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 1, 1)]), (T0 + 15, [wg("d-1", 2, 2)]), (T0 + 290, [wg("d-1", 3, 3)])])
        self.assertEqual(acc["cur"]["d-1"][0], 10.0 + 30.0)
        self.assertEqual(acc["secs"], 40.0)

    def test_sessions_without_a_device_and_garbage(self):
        acc = A.new()
        run(acc, [(T0 + 5, [{"kind": "wg", "ip": "10.77.1.9", "rx": 5, "tx": 5}, None, "x",
                            {"device": "d-1", "rx": "много", "tx": -5}, {"device": True, "rx": 1, "tx": 1}])])
        self.assertEqual(list(acc["cur"]), ["d-1"])
        self.assertEqual(acc["cur"]["d-1"][2:], [0, 0])


class WindowTest(unittest.TestCase):
    def test_the_change_of_the_window_closes_it(self):
        acc = A.new()
        rounds = [(T0 + 5 + 10 * i, [wg("d-1", 1000 + 10000 * i, 1000 + 1000 * i)]) for i in range(31)]
        wins = run(acc, rounds)
        self.assertEqual(len(wins), 1)
        w = wins[0]
        self.assertEqual(w["ts"], T0)
        self.assertEqual(w["rounds"], 30)                     # the 31st round (T0 + 305) went to the next window
        self.assertEqual(w["rows"]["d-1"][2:], [290000, 29000])
        self.assertEqual(acc["bucket"], T0 + 300)
        self.assertEqual(acc["cur"]["d-1"][2:], [10000, 1000])   # the round that crossed — whole in the new one

    def test_a_window_with_rounds_and_nobody_online_is_kept(self):
        """ACT-12: the collector worked and nobody was online — a window with no rows, not «нет данных»."""
        acc = A.new()
        wins = run(acc, [(T0 + 5, []), (T0 + 15, []), (T0 + 305, [])])
        self.assertEqual(wins, [{"ts": T0, "rounds": 2, "secs": 10.0, "rows": {}}])

    def test_the_clock_back_a_little_waits(self):
        acc = A.new()
        run(acc, [(T0 + 295, [wg("d-1", 1, 1)]), (T0 + 305, [wg("d-1", 2, 2)])])
        self.assertEqual(acc["bucket"], T0 + 300)
        wins = run(acc, [(T0 + 290, [wg("d-1", 5002, 2)])])        # NTP stepped back 15 s
        self.assertEqual(wins, [])
        self.assertEqual(acc["bucket"], T0 + 300)                # the window waits
        self.assertEqual(acc["cur"]["d-1"][0], 10.0)             # no time counted back
        self.assertEqual(acc["cur"]["d-1"][2], 1 + 5000)         # the bytes are counted

    def test_the_clock_back_a_lot_drops_the_window(self):
        """A day back (the clock had run ahead): the open window had a wrong stamp — it goes; the counters stay, so
        nothing is counted twice afterwards."""
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 1, 1)]), (T0 + 15, [wg("d-1", 1001, 1)])])
        wins = run(acc, [(T0 - 86400, [wg("d-1", 1501, 1)])])
        self.assertEqual(wins, [])
        self.assertEqual(acc["bucket"], T0 - 86400)
        self.assertEqual(acc["cur"]["d-1"][2:], [500, 0])
        self.assertEqual(acc["cur"]["d-1"][0], 0.0)

    def test_the_ttl_of_the_past(self):
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 1000, 1000)]), (T0 + 5 + 86400 + 10, [])])
        self.assertEqual(acc["prev"], {})
        run(acc, [(T0 + 5 + 86400 + 20, [wg("d-1", 9000, 9000)])])
        self.assertEqual(acc["cur"]["d-1"][2:], [0, 0])          # gone longer than a day: a first sight again


class KeepTest(unittest.TestCase):
    def test_json_there_and_back(self):
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 1000, 1000)]), (T0 + 15, [wg("d-1", 3000, 1500), vless("d-2", 10, 10)])])
        A.hold(acc, {"ts": T0 - 300, "rounds": 30, "secs": 300.0, "rows": {"d-1": [300.0, 10.0, 5, 6]}})
        back = A.load(A.dump(acc), T0 + 20)
        self.assertEqual(back, json.loads(A.dump(acc)))
        self.assertEqual(back["cur"]["d-1"], [10.0, 0.0, 2000, 500])

    def test_a_restart_goes_on_without_a_peak(self):
        """§3.3: the accumulator from /run younger than 15 min — the counters go on from the saved ones (no first sight
        = 0, no whole counter): the bytes of the pause land in the window of the restart."""
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 10 ** 9, 10 ** 9)]), (T0 + 15, [wg("d-1", 10 ** 9 + 100, 10 ** 9 + 100)])])
        saved = A.dump(acc)
        back = A.load(saved, T0 + 75)
        run(back, [(T0 + 75, [wg("d-1", 10 ** 9 + 900, 10 ** 9 + 300)])])
        self.assertEqual(back["cur"]["d-1"][2:], [900, 300])
        self.assertEqual(back["cur"]["d-1"][0], 10.0 + 30.0)     # the restart counts gap_cap at most

    def test_old_or_bad_files_start_anew(self):
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 1, 1)])])
        for doc, now in ((A.dump(acc), T0 + 5 + 901), (A.dump(acc), T0 - 3600), ("{", T0), ("[]", T0), (None, T0),
                         (json.dumps(dict(json.loads(A.dump(acc)), v=2)), T0 + 10)):
            self.assertEqual(A.load(doc, now), A.new(), (doc, now))

    def test_garbage_inside_is_dropped(self):
        doc = {"v": 1, "at": T0, "bucket": T0 + 7, "rounds": "x", "secs": float("nan"),
               "prev": {"d-1|wg|-": [1, 2, T0], "d-2|wg|-": [1, "2", T0], 5: [1, 1, 1], "x" * 300: [1, 1, 1]},
               "cur": {"d-1": [1, 2, 3, 4], "d-2": [1, 2, 3], "d-3": [True, 0, 0, 0]},
               "wait": [{"ts": T0 - 300, "rounds": 1, "secs": 10, "rows": {"d-1": [1, 1, 1, 1], "d-9": "x"}}, "junk"]}
        acc = A.load(doc, T0 + 1)
        self.assertEqual(acc["bucket"], T0)
        self.assertEqual((acc["rounds"], acc["secs"]), (0, 0.0))
        self.assertEqual(list(acc["prev"]), ["d-1|wg|-"])
        self.assertEqual(list(acc["cur"]), ["d-1"])
        self.assertEqual([w["rows"] for w in acc["wait"]], [{"d-1": [1.0, 1.0, 1, 1]}])

    def test_after_flushed_never_writes_twice(self):
        """A crash between the write of a window and the save of /run: the window found again is not written again."""
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 1, 1)]), (T0 + 15, [wg("d-1", 900, 1)])])
        A.hold(acc, {"ts": T0 - 300, "rounds": 1, "secs": 10.0, "rows": {}})
        A.after_flushed(acc, T0)
        self.assertEqual((acc["cur"], acc["wait"], acc["bucket"]), ({}, [], None))
        self.assertIn("d-1|wg|10.77.1.10", acc["prev"])          # the counters stay
        acc2 = A.new()
        run(acc2, [(T0 + 5, [wg("d-1", 1, 1)])])
        A.after_flushed(acc2, T0 - 300)
        self.assertEqual(acc2["bucket"], T0)                      # an older mark keeps the open window


class WaitTest(unittest.TestCase):
    def test_the_oldest_are_merged_above_the_cap(self):
        acc = A.new()
        for i in range(14):
            A.hold(acc, {"ts": T0 + 300 * i, "rounds": 30, "secs": 300.0, "rows": {"d-1": [300.0, 10.0, 100, 10]}})
        self.assertEqual(len(acc["wait"]), A.WAIT_MAX)
        first = acc["wait"][0]
        self.assertEqual(first["ts"], T0 + 600)
        self.assertEqual(first["rows"]["d-1"][2:], [300, 30])       # the bytes stay
        self.assertEqual(sum(w["rows"]["d-1"][2] for w in acc["wait"]), 1400)
        self.assertEqual(len(A.take(acc)), A.WAIT_MAX)
        self.assertEqual(acc["wait"], [])

    def test_forget_the_deleted(self):
        acc = A.new()
        run(acc, [(T0 + 5, [wg("d-1", 1, 1), wg("d-2", 1, 1, ip="10.77.1.11")]),
                  (T0 + 15, [wg("d-1", 9, 9), wg("d-2", 9, 9, ip="10.77.1.11")])])
        A.hold(acc, {"ts": T0 - 300, "rounds": 1, "secs": 10.0, "rows": {"d-1": [1, 1, 1, 1], "d-2": [1, 1, 1, 1]}})
        A.forget(acc, {"d-1"})
        self.assertEqual(list(acc["cur"]), ["d-1"])
        self.assertEqual([k.split("|")[0] for k in acc["prev"]], ["d-1"])
        self.assertEqual(list(acc["wait"][0]["rows"]), ["d-1"])


class PrivacyTest(unittest.TestCase):
    def test_no_address_and_no_kind_in_a_window(self):
        """ACT-6: a window keeps the id of a device and numbers — no address, no protocol, no time of a connection."""
        acc = A.new()
        wins = run(acc, [(T0 + 5, [wg("d-1", 1, 1, ip="203.0.113.7")]), (T0 + 15, [wg("d-1", 9000, 9, ip="203.0.113.7")]),
                         (T0 + 305, [])])
        text = json.dumps(wins)
        self.assertNotIn("203.0.113.7", text)
        self.assertNotIn("wg", text)
        self.assertEqual(set(wins[0]), {"ts", "rounds", "secs", "rows"})


class SparkTest(unittest.TestCase):
    def test_one_scale_and_no_data(self):
        self.assertEqual(A.spark([0, 1000, 20 * 1024, 10 * MB, 500 * MB, 10 ** 12], [False] * 5 + [True]),
                         "▁▁▂▅█·")
        self.assertEqual(A.level(500 * MB * 2, step=3600), 7)
        self.assertEqual((A.level(100 * MB, step=1800), A.level(100 * MB, step=3600)), (6, 5))   # scaled to the step


if __name__ == "__main__":
    unittest.main()


class OpenViewTest(unittest.TestCase):
    """3.3.7.10: the open window as live.json carries it — the panel adds it to the summary between the windows written
    (the line and the bytes of the day move every round, not every 5 min). The same fields as a window (ACT-6)."""

    def test_the_open_window_with_rows_of_anything_only(self):
        acc = A.new(T0)
        self.assertIsNone(A.open_view(acc))                       # no round yet: no window to show
        acc, _ = A.step(acc, [wg("d-1", 1000, 1000), wg("d-2", 5, 5, ip="10.77.1.11")], T0 + 10)
        acc, _ = A.step(acc, [wg("d-1", 1000 + 3 * MB, 1000 + MB), wg("d-2", 5, 5, ip="10.77.1.11")], T0 + 20)
        v = A.open_view(acc)
        self.assertEqual((v["w"], v["at"], v["secs"]), (T0, T0 + 20, 10))
        self.assertEqual(v["cur"]["d-1"], [10, 10, 3 * MB, MB])
        self.assertEqual(v["cur"]["d-2"], [10, 0, 0, 0])              # on the line, no traffic: «на связи» only
        self.assertEqual(sorted(v), ["at", "cur", "secs", "w"])
        json.dumps(v)                                               # plain JSON for live.json

    def test_keep_and_the_cap(self):
        acc = A.new(T0)
        acc, _ = A.step(acc, [wg("d-1", 1, 1)], T0 + 10)
        acc, _ = A.step(acc, [wg("d-1", 2 * MB, 2 * MB)], T0 + 20)
        self.assertEqual(A.open_view(acc, keep={"d-2"})["cur"], {})   # a device not in the state never comes out
        acc["cur"] = dict(("d-%d" % i, [1.0, 0.0, i, 0]) for i in range(A.OPEN_MAX + 10))
        cur = A.open_view(acc)["cur"]
        self.assertEqual(len(cur), A.OPEN_MAX)
        self.assertNotIn("d-0", cur)                                  # the busiest stay
        self.assertIn("d-%d" % (A.OPEN_MAX + 9), cur)

    def test_a_new_window_starts_empty(self):
        acc = A.new(T0)
        acc, _ = A.step(acc, [wg("d-1", 1, 1)], T0 + 10)
        acc, _ = A.step(acc, [wg("d-1", MB, MB)], T0 + 290)
        acc, w = A.step(acc, [wg("d-1", MB, MB)], T0 + 300)
        self.assertIsNotNone(w)
        v = A.open_view(acc)
        self.assertEqual((v["w"], v["cur"]), (T0 + 300, {"d-1": [10, 0, 0, 0]}))
