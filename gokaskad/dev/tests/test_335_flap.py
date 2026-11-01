"""3.3.5 №3: «выход мигает в «Событиях»» (BACKLOG-3.3.5 №3, k3/stand/logs/INV-exit-flap.md). The rule of the owner
(15.10): an exit is down when at least half of the checks of the last 2 minutes failed (4 checks at the least), the same
for VLESS and AmneziaWG; back after 2 good checks in a row. One lost probe or a short run of them moves nobody (the
device stays in gw_live), a whole failure is told in about a minute; one event of a kind per exit in 15 minutes."""

import json
import os
from unittest import mock

from kaskad import paths, state
from kaskad.errors import KaskadError
from kaskad.gw import collect, exits, live, policy, xray
from tests.helpers import legacy_state
from tests.test_gw_ops import CliGwCase


class FlapCase(CliGwCase):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(live, "_now", lambda: self.now), mock.patch.object(collect, "LIVE", live.Live()),
                  mock.patch.object(live, "_mono", lambda: self.now)):        # stage 1: the windows are monotonic
            p.start()
            self.addCleanup(p.stop)
        self.enabled_world()
        st = legacy_state(state.load())             # stage 1 (§1.3): an updated node — the real «Как раньше» = 3.3.5:
        doc = policy.default_doc("compat")           # «снова работает» after two good checks, no pause (REV-s1-t4 З4)
        st["gateway"]["exit_policy"] = doc
        state.save(st)
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        self.w.sysd.units[xray.UNIT]["active"] = True
        self.w.curl.observatory = {"gx-" + self.vid: True}

    def round(self, dt=10):
        self.now += dt
        return collect.tick(self.w.runner)["exits"]

    def events(self):
        return [e for e in json.loads(self.read("/run/kaskad/live.json"))["events"] if e["kind"].startswith("exit_")]

    def mine(self, res, eid):
        return [c for c in res["changes"] if c["id"] == eid]


class AwgFlapTest(FlapCase):
    def setUp(self):
        super().setUp()
        self.ok("gw", "route", "exit:" + self.aid)                 # the device is pinned to the AmneziaWG exit (block)
        for _ in range(12):                                       # 2 minutes of good checks
            self.round()

    def test_one_failure_and_a_short_run_move_nobody(self):
        live_before = self.w.nft.elements("gw_live")
        self.assertIn("10.77.1.10", live_before)
        self.w.leg(2, probe=False)
        self.assertEqual(self.mine(self.round(5), self.aid), [])           # one lost probe
        self.assertEqual(self.w.nft.elements("gw_live"), live_before)      # the device keeps its traffic
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102})
        self.w.leg(2)
        self.round(5)
        self.w.leg(2, probe=False)
        for _ in range(2):                                                 # two failed within 10 s
            self.assertEqual(self.mine(self.round(5), self.aid), [])
            self.assertEqual(self.w.nft.elements("gw_live"), live_before)
        self.w.leg(2)
        for _ in range(3):
            self.assertEqual(self.mine(self.round(), self.aid), [])
        self.assertEqual(self.events(), [])
        self.assertNotIn("не работает", self.log())

    def test_a_whole_failure_is_told_in_about_a_minute(self):
        """A real failing round: the connections wait PROBE_TIMEOUT (4 s), the next round comes RECHECK (5 s) later —
        merges 9 s apart; each is a check of its own (P − 2 s, REV-335 №2), not every second one."""
        self.w.leg(2, probe=False)
        self.now += 4                                             # the next round: its probe waits 4 s
        t0 = self.now + 9
        for n in range(30):
            res = self.round(9)                                   # collect.loop: RECHECK s + the wait of the probe
            if self.mine(res, self.aid):
                break
            self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102}, n)
        else:
            self.fail("never down")
        took = self.now - t0
        self.assertTrue(45 <= took <= 62, took)                   # ~1 min (before the fix: 72 s)
        # stage 1 (SE §2.2, §1.3 difference 4): «exit:E» with E down goes through another fit exit of its group (the
        # VLESS one here, 0x301), never directly — the fail-closed path without one: test_collect_s1
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x301})   # no leak
        self.assertEqual([e["kind"] for e in self.events()], ["exit_dead"])


class RecheckTest(FlapCase):
    """The coordinator 15.10: one check per period in the window — a recheck of a failing leg 5 s later REPLACES the
    result of the last check (collect.RECHECK), it does not add one."""

    def setUp(self):
        super().setUp()
        self.ok("gw", "route", "exit:" + self.aid)
        for _ in range(3):                                        # a short history: 4 checks at most
            self.round()

    def test_a_probe_and_its_recheck_failed_then_good_is_not_down(self):
        tries = len(exits.load_snapshot()["exits"][self.aid]["tries"])
        self.w.leg(2, probe=False)
        res = self.round()                                        # the probe of the period fails …
        self.assertTrue(res["failing"])
        self.assertEqual(self.mine(self.round(5), self.aid), [])  # … and its recheck 5 s later: the same check
        rec = exits.load_snapshot()["exits"][self.aid]
        self.assertEqual((len(rec["tries"]), rec["fails"]), (tries + 1, 1))
        self.w.leg(2)
        for _ in range(3):
            self.assertEqual(self.mine(self.round(), self.aid), [])
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102})
        self.assertEqual(self.events(), [])

    def test_rechecks_count_one_per_10_s(self):
        ok, bad = {"alive": True, "detail": "отвечает"}, {"alive": False, "detail": "нет ответа"}
        snap = {"exits": {}}
        for i in range(12):                                       # 2 min of good checks, 10 s apart
            snap, _, _ = exits.merge(snap, {"e": ok}, ["e"], now=1000 + 10 * i)
        for i in range(6):                                        # 30 s of 5 s rounds: 3 failed checks, not 6
            snap, ch, _ = exits.merge(snap, {"e": bad}, ["e"], now=1120 + 5 * i)
            self.assertEqual(ch, [])
        rec = snap["exits"]["e"]
        self.assertEqual((rec["fails"], rec["checks"]), (3, 12))

    def test_a_failing_round_9_s_apart_is_a_check_of_its_own(self):
        ok, bad = {"alive": True, "detail": "отвечает"}, {"alive": False, "detail": "нет ответа"}
        snap = {"exits": {}}
        for i in range(12):
            snap, _, _ = exits.merge(snap, {"e": ok}, ["e"], now=1000 + 10 * i)
        for i in range(3):                                        # 4 s wait + 5 s pause: 9 s apart — 3 checks
            snap, _, _ = exits.merge(snap, {"e": bad}, ["e"], now=1124 + 9 * i)
        self.assertEqual(snap["exits"]["e"]["fails"], 3)
        snap, _, _ = exits.merge(snap, {"e": bad}, ["e"], now=1142 + 7)    # 7 s later: within P − 2 — replaces
        self.assertEqual(snap["exits"]["e"]["fails"], 3)


class VlessFlapTest(FlapCase):
    def setUp(self):
        super().setUp()
        self.ok("gw", "route", "exit:" + self.vid)
        for _ in range(12):
            self.round()

    def test_a_whole_failure_is_told_in_about_a_minute(self):
        self.w.curl.observatory = {"gx-" + self.vid: False}
        t0 = self.now
        for _ in range(30):
            res = self.round()
            if self.mine(res, self.vid):
                break
        else:
            self.fail("never down")
        self.assertEqual(self.mine(res, self.vid), [{"id": self.vid, "was": True, "alive": False}])
        self.assertTrue(40 <= self.now - t0 <= 70, self.now - t0)
        self.assertIn("(xray: задержка 5000 мс, ошибка: context deadline exceeded)", self.log())

    def test_xray_probing_every_12_s_read_every_10_s(self):
        """The fake's own cycle of xray (a probe every N s, read again between them): a whole failure in about a minute."""
        self.w.curl.every = 12
        for _ in range(12):
            self.round()
        self.w.curl.observatory = {"gx-" + self.vid: False}
        t0 = self.now
        for _ in range(30):
            if self.mine(self.round(), self.vid):
                break
        else:
            self.fail("never down")
        self.assertTrue(40 <= self.now - t0 <= 70, self.now - t0)

    def test_the_same_probe_read_within_a_minute_counts_once(self):
        self.w.curl.observatory = {"gx-" + self.vid: False}
        self.w.curl.try_at = int(self.now) + 1                    # xray probes once, then not again
        for _ in range(11):                                       # 55 s of 5 s rounds over one failed probe
            self.assertEqual(self.mine(self.round(5), self.vid), [])
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x300 + 1})

    def test_a_frozen_probe_is_silence_after_a_minute(self):
        """REV-335 №3 (the coordinator 15.10): the same probe of xray for more than 60 s — xray stopped probing the
        exit: down by the rule of silence, even when that probe said «alive»."""
        self.w.curl.try_at = int(self.now) + 1                    # frozen at a good probe
        downs = []
        for n in range(20):
            res = self.round(5)
            if self.mine(res, self.vid):
                downs.append(n)
                break
        self.assertEqual(downs, [12])                             # read first at +5 s (n 0), down 60 s later (n 12)
        # stage 1 (SE §2.2, difference 4): the other fit exit of the group (AmneziaWG, 0x102), never directly
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102})
        rc, doc, _ = self.run_json("gw", "exits")
        row = [r for r in doc["exits"] if r["id"] == self.vid][0]
        self.assertEqual((row["state"], row["detail_code"]), ("dead", "detail.xray_stale"))
        self.assertIn("xray давно не проверял выход", self.log())
        for _ in range(6):                                        # still frozen: stays down, nothing more to tell
            self.assertEqual(self.mine(self.round(5), self.vid), [])
        self.w.curl.try_at = None                                 # xray probes again: back after 2 good checks
        ups = [self.mine(self.round(), self.vid) for _ in range(2)]
        self.assertEqual(ups, [[], [{"id": self.vid, "was": False, "alive": True}]])


class EventsQuietTest(FlapCase):
    """B7 with the coordinator's correction (15.10): only repeats are held back; the feed never stays wrong — when the
    window (15 min from the last published event) ends and the state is not the one told, the state now is told with
    the drops of the window; a restarted collector tells the first transition at once; «Выходы» is never late."""

    def setUp(self):
        super().setUp()
        for _ in range(12):
            self.round()

    def down(self):
        self.w.leg(2, probe=False)
        for _ in range(40):
            if self.mine(self.round(5), self.aid):
                return
        self.fail("never down")

    def up(self):
        self.w.leg(2)
        for _ in range(2):
            self.round()

    def kinds(self):
        return [e["kind"] for e in self.events()]

    def state_now(self):
        rc, doc, _ = self.run_json("gw", "exits")
        return [r for r in doc["exits"] if r["id"] == self.aid][0]["state"]

    def test_the_trap_down_up_down_is_told_when_the_window_ends(self):
        """12:00 «упал», 12:01 «ожил», 12:05 «упал» held back — the feed must not say «ожил» for good."""
        self.down()
        self.up()
        self.assertEqual(self.kinds(), ["exit_dead", "exit_alive"])           # the first of each kind: at once
        t_alive = self.events()[-1]["ts"]
        for _ in range(2):                                                     # two more drops within the window,
            self.down()                                                        # then it stays down
            self.up()
        self.down()
        self.assertEqual(self.kinds(), ["exit_dead", "exit_alive"])           # repeats: held back
        self.assertEqual(self.state_now(), "dead")                            # «Выходы»: honest at once
        self.assertEqual(self.log().count("(Amsterdam): не работает"), 4)    # the journal tells every one
        while self.now - t_alive < live.EVENT_QUIET - 10:
            self.round()
            self.assertEqual(self.kinds(), ["exit_dead", "exit_alive"])
        self.round(10)
        self.round(10)
        evs = self.events()
        self.assertEqual([e["kind"] for e in evs], ["exit_dead", "exit_alive", "exit_dead"])
        self.assertIn("(обрывов за 15 мин: 3)", evs[2]["text"])
        self.assertIn("не работает", evs[2]["text"])
        self.assertIn("(drops in 15 min: 3)", live.event_text(evs[2], "en"))
        self.assertIn("is down", live.event_text(evs[2], "en"))
        ids = [e["id"] for e in json.loads(self.read("/run/kaskad/live.json"))["events"]]
        self.assertEqual(ids, sorted(ids))                    # born when told: the bot and SSE take it as new
        self.assertGreaterEqual(evs[2]["ts"], t_alive + live.EVENT_QUIET)

    def test_back_to_the_state_told_says_nothing_more(self):
        self.down()
        self.up()
        self.down()
        self.up()                                             # held back: «упал», «ожил» — the feed says «ожил»: right
        for _ in range(16 * 6):
            self.round()
        self.assertEqual(self.kinds(), ["exit_dead", "exit_alive"])
        self.down()                                           # a new window: told at once, only this drop
        evs = self.events()
        self.assertEqual([e["kind"] for e in evs], ["exit_dead", "exit_alive", "exit_dead"])
        self.assertNotIn("обрывов", evs[2]["text"])
        self.up()
        evs = self.events()
        self.assertEqual(evs[-1]["kind"], "exit_alive")
        self.assertIn("снова работает", evs[-1]["text"])
        self.assertIn("is working again", live.event_text(evs[-1], "en"))

    def test_a_restarted_collector_goes_on_with_its_window(self):
        """The feed is in /run (the coordinator 15.10): a restarted collector holds back repeats as before and tells
        the state when the window ends."""
        self.down()
        self.up()
        t_alive = self.events()[-1]["ts"]
        with mock.patch.object(collect, "LIVE", live.Live()):  # the collector starts again within the window
            self.down()
            self.assertEqual(self.kinds(), ["exit_dead", "exit_alive"])       # a repeat: held back
            for _ in range(200):
                if self.now - t_alive >= live.EVENT_QUIET + 20:
                    break
                self.round()
            evs = self.events()
            self.assertEqual([e["kind"] for e in evs], ["exit_dead", "exit_alive", "exit_dead"])
            self.assertIn("(обрывов за 15 мин: 1)", evs[2]["text"])

    def test_a_restart_with_a_held_event_tells_the_state_at_once(self):
        """REV-335 №1: «упал» → «ожил» told, «упал» held back, the collector restarts: the first round tells «не работает»
        — the feed must not say «снова работает» for good."""
        self.down()
        self.up()
        self.down()
        self.assertEqual(self.kinds(), ["exit_dead", "exit_alive"])
        with mock.patch.object(collect, "LIVE", live.Live()):
            self.round()
            self.assertEqual(self.kinds(), ["exit_dead", "exit_alive", "exit_dead"])
            for _ in range(6 * 30):                              # 30 min more of a dead exit: nothing else
                self.round()
            self.assertEqual(self.kinds(), ["exit_dead", "exit_alive", "exit_dead"])

    def test_a_reboot_with_a_held_event_tells_the_state_at_once(self):
        """/run is empty after a reboot: the last event told of each exit is on the disk (exits-told.json)."""
        self.down()
        self.up()
        self.down()
        told = json.loads(self.read("/etc/kaskad/gw/exits-told.json"))
        self.assertEqual(told[self.aid][1], "exit_alive")
        for f in ("live.json", "live-feed.json", "exits-window.json"):
            os.remove(paths.P("/run/kaskad/" + f))
        with mock.patch.object(collect, "LIVE", live.Live()):
            self.round()
            self.assertEqual(self.kinds(), ["exit_dead"])       # the feed after the reboot: the state now
            self.round()
            self.assertEqual(self.kinds(), ["exit_dead"])

    def test_a_restart_without_a_held_event_tells_nothing(self):
        self.down()
        self.up()
        with mock.patch.object(collect, "LIVE", live.Live()):
            for _ in range(3):
                self.round()
            self.assertEqual(self.kinds(), ["exit_dead", "exit_alive"])

    def test_a_broken_parser_keeps_the_held_event(self):
        """REV-335 №7: a round whose pool cannot be read is not «the exit left the pool»: the held event waits."""
        self.down()
        self.up()
        t_alive = self.events()[-1]["ts"]
        self.down()
        with mock.patch.object(exits, "pool", side_effect=KaskadError("parser")):
            for _ in range(200):
                if self.now - t_alive >= live.EVENT_QUIET + 20:
                    break
                self.round()
        self.assertEqual(self.kinds(), ["exit_dead", "exit_alive"])
        self.round()
        self.assertEqual(self.kinds(), ["exit_dead", "exit_alive", "exit_dead"])


class DropsCountTest(FlapCase):
    """REV-335 №6: «(обрывов за 15 мин: N)» counts the drop told when the window opened too."""

    def test_the_drop_told_counts(self):
        L = live.Live()
        L._loaded = True
        names = {"e": "X"}
        up, down = {"exits": {"e": {"alive": True}}}, {"exits": {"e": {"alive": False, "fails": 3}}}
        L._exit_event("e", "exit_dead", names, down, 0)        # told
        L._exit_event("e", "exit_alive", names, up, 600)       # told
        L._exit_event("e", "exit_dead", names, down, 1000)     # told (the last «не работает» 1000 s ago)
        L._exit_event("e", "exit_alive", names, up, 1030)      # held: «снова работает» 430 s ago
        L._exit_event("e", "exit_dead", names, down, 1100)     # held
        L._exit_event("e", "exit_alive", names, up, 1200)      # held
        L._window_end(names, up, 1000 + live.EVENT_QUIET)
        self.assertEqual([e["kind"] for e in L.events], ["exit_dead", "exit_alive", "exit_dead", "exit_alive"])
        self.assertEqual(L.events[-1]["args"].get("n"), 2)     # 1000 (told) and 1100 (held)
        self.assertIn("(обрывов за 15 мин: 2)", L.events[-1]["text"])
