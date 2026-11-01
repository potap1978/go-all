"""3.3.7.11 (владелец 27.10: «Да, всё до 5 с»): while a panel watches, what «Пользователи» and «Выходы» show moves at
least every ``collect.LIGHT_MAX`` s — the light round in the pause (the status of the inbounds: who is online, the
open window of activity; the counters of the exits: the open minute of «Выходы»); without a panel the pace is the one
before: a full round every 10 s, nothing between. No probes, no nft in a light round; values between the rounds are
not invented."""

import json
import os
import sqlite3
from unittest import mock

from kaskad import state
from kaskad.gw import collect, live, metrics
from tests.gw.test_collect_activity import ActWorld


class Clock(object):
    """The monotonic clock of the collector and its sleep, as one fake."""

    def __init__(self):
        self.t = 0.0

    def mono(self):
        return self.t

    def sleep(self, s):
        self.t += s


class CadenceTest(ActWorld):
    """The pause alone: the moments the pages get fresh data — the write of the full round, the light rounds of the
    pause, the write of the next full round — never further apart than LIGHT_MAX."""

    def pause(self, interval, lead, post, want=True, host=0.0):
        clk = Clock()
        clk.t = 100.0
        collect.MEM.shown_at, collect.MEM.shown_lead = clk.t, lead     # the full round wrote live.json now
        clk.t += post                                                    # … its last work, then the pause
        lights, fasts = [], []
        steps = [0]

        def light(runner):
            lights.append(clk.t)
            collect.MEM.shown_at = clk.t
            return True

        def hostp(room=None):
            steps[0] += 1
            if host and steps[0] == 2:                                   # a series of the light probes ≈ host s
                clk.t += host
                return host
            return 0.0
        with mock.patch.object(collect, "_mono", clk.mono), mock.patch.object(collect, "_sleep", clk.sleep), \
                mock.patch.object(collect, "_host_pause", hostp), mock.patch.object(live, "want_fresh", lambda: want), \
                mock.patch.object(collect, "_light_round", light), \
                mock.patch.object(collect.LIVE, "fast", lambda runner: fasts.append(clk.t)):
            start = clk.t
            collect.pause(self.w.runner, interval)
            self.assertAlmostEqual(clk.t - start, max(interval, host), places=6)   # the pause is never longer
        fresh = [100.0] + lights + [clk.t + lead]                       # + the write of the next full round
        return fresh, lights, fasts

    def test_with_a_viewer_never_older_than_5_s(self):
        for interval in (collect.INTERVAL, collect.RECHECK):
            for lead in (0.2, 1.5, 3.0, 4.6):
                for post in (0.05, 0.9):
                    for host in (0.0, 1.4):
                        fresh, lights, fasts = self.pause(interval, lead, post, host=host)
                        gaps = [b - a for a, b in zip(fresh, fresh[1:])]
                        self.assertLessEqual(max(gaps), collect.LIGHT_MAX + 1e-9,
                                             (interval, lead, post, host, fresh))
                        self.assertTrue(lights, (interval, lead, post, host))
                        self.assertLessEqual(len(lights), 3, (interval, lead, post, host, lights))   # not every step

    def test_a_usual_round_two_light_rounds_live_ones_between(self):
        fresh, lights, fasts = self.pause(collect.INTERVAL, 1.5, 0.3)
        self.assertEqual(len(lights), 2)                                   # 10 s: at ≈ 4 and ≈ 8 s of the pause
        self.assertEqual(len(fasts), 2)                                    # the 2-s live rounds go on between them
        self.assertFalse(set(lights) & set(fasts))                        # a light round instead of a live one

    def test_without_a_viewer_the_pace_before(self):
        fresh, lights, fasts = self.pause(collect.INTERVAL, 1.5, 0.3, want=False)
        self.assertEqual((lights, fasts), ([], []))

    def test_the_first_sight_of_a_viewer_at_once(self):
        clk = Clock()
        clk.t = 1000.0
        collect.MEM.shown_at = 10.0                                      # the last fresh data long ago (no viewer)
        lights = []
        with mock.patch.object(collect, "_mono", clk.mono), mock.patch.object(collect, "_sleep", clk.sleep), \
                mock.patch.object(collect, "_host_pause", lambda room=None: 0.0), \
                mock.patch.object(live, "want_fresh", lambda: True), \
                mock.patch.object(collect, "_light_round", lambda r: (lights.append(clk.t),
                                                                      setattr(collect.MEM, "shown_at", clk.t))), \
                mock.patch.object(collect.LIVE, "fast", lambda runner: None):
            collect.pause(self.w.runner, collect.INTERVAL)
        self.assertEqual(lights[0], 1000.0 + live.FAST)                  # the first step of the pause


class LightRoundTest(ActWorld):
    """The light round in the world of the collector: real ``tick``, real ``pause`` with the clock of the test."""

    def setUp(self):
        super().setUp()
        self.mono = [5000.0]
        self.writes = []
        real = live.write

        def wrote(doc):
            self.writes.append(json.loads(json.dumps(doc)))
            return real(doc)

        def sleep(s):
            self.now += s
            self.mono[0] += s
            for d in self.online:
                self.traffic(d, int(20000 * s), int(2000 * s))      # traffic goes on between the rounds
        for p in (mock.patch.object(collect, "_mono", lambda: self.mono[0]), mock.patch.object(collect, "_sleep", sleep),
                  mock.patch.object(live, "write", wrote)):
            p.start()
            self.addCleanup(p.stop)

    def round(self, dt=10):
        self.mono[0] += dt
        return super().round(dt)

    def cycle(self, want):
        """A full round, then its pause — as ``loop`` does. -> the documents written."""
        self.writes = []
        self.round()
        with mock.patch.object(live, "want_fresh", lambda: want):
            collect.pause(self.w.runner, collect.INTERVAL)
        return self.writes

    def test_the_open_window_moves_every_5_s_with_a_viewer(self):
        self.online.add(self.did)
        self.round()                                                      # the first sight counts 0
        self.cycle(True)
        docs = self.cycle(True)
        ats = [d["activity"]["at"] for d in docs]
        moved = sorted(set(ats))
        self.assertGreaterEqual(len(moved), 3, ats)                      # the full round + 2 light rounds
        self.assertLessEqual(max(b - a for a, b in zip(moved, moved[1:])), collect.LIGHT_MAX, moved)
        rx = [d["activity"]["cur"][self.did][2] for d in docs]
        self.assertEqual(rx, sorted(rx))
        self.assertGreater(rx[-1], rx[0])                               # the bytes of the pause, as they came
        # what is not measured is not invented: a live round keeps the open window of the round before it
        for a, b in zip(docs, docs[1:]):
            if a["activity"]["at"] == b["activity"]["at"]:
                self.assertEqual(a["activity"]["cur"], b["activity"]["cur"])

    def test_without_a_viewer_one_write_a_round(self):
        self.online.add(self.did)
        self.round()
        docs = self.cycle(False)
        self.assertEqual(len(docs), 1)                                   # the full round only, as before

    def test_a_light_round_runs_no_probe_and_no_nft(self):
        self.online.add(self.did)
        self.round()
        n = len(self.w.runner.calls)
        self.assertTrue(collect._light_round(self.w.runner))
        cmds = [c[0] for c in self.w.runner.calls[n:]]
        self.assertEqual([c for c in cmds if c[0] in ("nft", "ip", "conntrack", "sysctl", "systemctl")], [], cmds)
        self.assertLessEqual(len(cmds), 1 + len(collect.inbound_statuses(state.load(), self.w.runner)), cmds)
        self.assertEqual([c for c in cmds if c[0] == "curl"],
                         [["curl", "-s", "--max-time", "3", "http://127.0.0.1:10852/debug/vars"]])   # the VLESS exit

    def test_the_light_round_says_who_is_online(self):
        """``Live.light``: who is online and their counters from the status taken now; the rest of the full round."""
        self.round()
        before = self.read_live()
        st = state.load()
        stats = {"wg": {"sessions": [{"device": self.did, "ip": "10.77.1.10", "since": int(self.now) - 5, "rx": 10,
                                      "tx": 20}]}}
        doc = collect.LIVE.light(st, stats, now=self.now + 4)
        self.assertEqual(doc["devices"][self.did], {"online": True, "inbound": "wg", "addr": "10.77.1.10",
                                                    "since": int(self.now) - 5, "rx": 10, "tx": 20})
        for k in ("inbounds", "exits", "events", "gateway", "groups"):
            self.assertEqual(json.loads(json.dumps(doc[k])), before[k], k)
        self.assertGreater(doc["seq"], before["seq"])
        doc = collect.LIVE.light(st, {"wg": {"sessions": []}}, now=self.now + 8)
        self.assertFalse(doc["devices"][self.did]["online"])          # gone: «online since» starts again next time

    def read_live(self):
        return json.loads(self.read("/run/kaskad/live.json"))


class LightActivityTest(ActWorld):
    """REV-s1fin З-2: «активен» of the history does not depend on whether a panel watches. The light round stepped the
    accumulator itself: the same traffic cut into rounds of 2–5 s reached the threshold less often (a burst of 5 KB
    every 10 s — 16 s «активен» of 50 with a panel, 50 without). Now it looks at the open window on a copy; the
    accumulator, /run and the windows of the base go by the full rounds only. A light round writes nothing of the
    decision either: pins.json, the policy, the events."""

    BURST = 5000                                          # B 1 s after each full round: ≥ 4096 a round of 10 s

    def setUp(self):
        super().setUp()
        self.mono = [5000.0]
        self.since = 0.0                                  # s since the last full round

        def sleep(s):
            if self.since < 1 <= self.since + s:
                self.traffic(self.did, self.BURST, 0)
            self.since += s
            self.now += s
            self.mono[0] += s
        for p in (mock.patch.object(collect, "_mono", lambda: self.mono[0]), mock.patch.object(collect, "_sleep", sleep)):
            p.start()
            self.addCleanup(p.stop)
        self.online.add(self.did)

    def cycle(self, want):
        """A full round, then its pause of 10 s — as ``loop`` does."""
        self.since = 0.0
        self.round(0)
        with mock.patch.object(live, "want_fresh", lambda: want):
            collect.pause(self.w.runner, collect.INTERVAL)

    def written(self):
        try:
            return self.rows()
        except sqlite3.OperationalError:                  # no base before the first round
            return []

    def next_window(self, want):
        """Cycles until a window is written -> its row (on_s, act_s, rx, tx) in the base."""
        n = len(self.written())
        for _ in range(40):
            self.cycle(want)
            rows = self.written()
            if len(rows) > n:
                return rows[-1][2:]
        self.fail("no window")

    def test_the_same_traffic_the_same_history(self):
        self.next_window(False)                           # the first window: the first sight counts 0
        without = self.next_window(False)
        self.next_window(True)                            # the window of the change of the viewer
        lights = []
        real = collect._light_round
        with mock.patch.object(collect, "_light_round", lambda r: lights.append(1) or real(r)):
            watched = self.next_window(True)
        self.assertGreaterEqual(len(lights), 50)         # two light rounds a round: the panel did watch
        self.assertEqual(without, (300, 300, 30 * self.BURST, 0))
        self.assertEqual(watched, without)               # before the fix: «активен» 120 of 300

    def test_the_open_window_between_the_full_rounds(self):
        """What the panel sees between two full rounds: the bytes since the last one, the rule of the full round on
        the time since it; the next full round takes the same bytes — none counted twice, none lost."""
        self.next_window(False)                           # a few seconds into a new window
        self.round(0)
        full = json.loads(self.read("/run/kaskad/live.json"))["activity"]
        acc = json.dumps(collect.MEM.act["acc"], sort_keys=True)
        self.since = 0.0
        self.traffic(self.did, self.BURST, 0)
        self.now += 4
        self.mono[0] += 4
        self.assertTrue(collect._light_round(self.w.runner))
        got = json.loads(self.read("/run/kaskad/live.json"))["activity"]
        self.assertEqual(json.dumps(collect.MEM.act["acc"], sort_keys=True), acc)      # the accumulator untouched
        on, act, rx, tx = (got["cur"][self.did][i] - full["cur"][self.did][i] for i in range(4))
        self.assertEqual((got["at"] - full["at"], on, act, rx, tx), (4, 4, 4, self.BURST, 0))
        self.now += 6
        self.mono[0] += 6
        self.round(0)
        after = json.loads(self.read("/run/kaskad/live.json"))["activity"]
        self.assertEqual(after["cur"][self.did][2] - full["cur"][self.did][2], self.BURST)

    def test_across_the_border_the_open_window_of_the_full_round_stays(self):
        self.next_window(False)
        while (int(self.now) + 10) // 300 == int(self.now) // 300:
            self.cycle(False)
        self.round(0)                                     # the last full round of the window
        full = json.loads(self.read("/run/kaskad/live.json"))["activity"]
        self.now = (int(self.now) // 300 + 1) * 300 + 1   # a light round past the border: nothing to show yet
        self.mono[0] += 5
        self.assertTrue(collect._light_round(self.w.runner))
        got = json.loads(self.read("/run/kaskad/live.json"))["activity"]
        self.assertEqual((got["w"], got["at"], got["cur"]), (full["w"], full["at"], full["cur"]))

    def test_a_light_round_writes_nothing_of_the_decision(self):
        for _ in range(3):
            self.cycle(False)
        files = ("/var/lib/kaskad/gw/pins.json", "/etc/kaskad/state.json", "/run/kaskad/gw/activity.json")
        before = dict((f, (self.read(f), os.stat(self.path(f)).st_mtime_ns)) for f in files)
        events = json.loads(self.read("/run/kaskad/live.json"))["events"]
        conn = sqlite3.connect(self.path("/var/lib/kaskad/metrics.db"))
        try:
            n = conn.execute("SELECT count(*) FROM events").fetchone()[0]
        finally:
            conn.close()
        for _ in range(6):
            self.now += 2
            self.mono[0] += 2
            self.traffic(self.did, self.BURST, 0)
            self.assertTrue(collect._light_round(self.w.runner))
        self.assertEqual(dict((f, (self.read(f), os.stat(self.path(f)).st_mtime_ns)) for f in files), before)
        self.assertEqual(json.loads(self.read("/run/kaskad/live.json"))["events"], events)
        conn = sqlite3.connect(self.path("/var/lib/kaskad/metrics.db"))
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM events").fetchone()[0], n)
        finally:
            conn.close()


class OpenMinuteTest(ActWorld):
    """The open minute of «Выходы»: the bytes of an exit since the point of m1, by the rule of the next point."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(collect, "_clock_synced", lambda runner: True)
        p.start()
        self.addCleanup(p.stop)
        self.ctr = [10 ** 6, 2 * 10 ** 6]
        self.put()

    def put(self, drx=0, dtx=0):
        self.ctr[0] += drx
        self.ctr[1] += dtx
        self.write("/sys/class/net/kawg2/statistics/rx_bytes", "%d\n" % self.ctr[0])
        self.write("/sys/class/net/kawg2/statistics/tx_bytes", "%d\n" % self.ctr[1])

    def doc(self):
        return json.loads(self.read("/run/kaskad/live.json"))

    def m1(self, eid):
        conn = sqlite3.connect(self.path("/var/lib/kaskad/metrics.db"))
        try:
            return conn.execute("SELECT ts, rx, tx FROM m1 WHERE exit = ? ORDER BY ts", (eid,)).fetchall()
        finally:
            conn.close()

    def test_the_bytes_since_the_point_and_the_next_point_the_same(self):
        self.round()                                                     # the first point: the base of the counters
        m = self.doc()["metrics"]
        self.assertEqual(m["cur"].get(self.aid), [0, 0])
        self.put(1000, 3000)
        self.assertTrue(collect._light_round(self.w.runner))
        m = self.doc()["metrics"]
        self.assertEqual(m["cur"][self.aid], [1000, 3000])
        self.assertEqual(m["at"], int(self.now))
        self.put(500, 0)
        self.round(10)                                                   # a full round within the minute
        self.assertEqual(self.doc()["metrics"]["cur"][self.aid], [1500, 3000])
        self.put(7, 9)
        self.round(61)                                                   # the next point of m1: the same bytes
        rows = self.m1(self.aid)
        self.assertEqual(rows[-1][1:], (1507, 3009))
        m = self.doc()["metrics"]
        self.assertEqual((m["ts"], m["cur"][self.aid]), (rows[-1][0], [0, 0]))

    def test_no_open_minute_when_the_point_was_not_written(self):
        self.round()
        with mock.patch.object(metrics.Writer, "write_minute", lambda self, ts, rows: False):
            self.put(1000, 1000)
            self.round(61)
            self.assertNotIn("cur", self.doc()["metrics"])              # the minute is not in the base: nothing open
            self.put(1000, 1000)
            collect._light_round(self.w.runner)
            self.assertNotIn("cur", self.doc()["metrics"])

    def test_an_exit_not_read_is_not_there(self):
        self.round()
        os.remove(self.path("/sys/class/net/kawg2/statistics/rx_bytes"))
        collect._light_round(self.w.runner)
        self.assertNotIn(self.aid, self.doc()["metrics"]["cur"])          # unknown is not zero

    def test_live_rounds_keep_the_marks(self):
        self.round()
        self.put(100, 100)
        collect._light_round(self.w.runner)
        before = self.doc()["metrics"]
        collect._live_round(lambda: collect.LIVE.fast(self.w.runner))
        self.assertEqual(self.doc()["metrics"], before)
