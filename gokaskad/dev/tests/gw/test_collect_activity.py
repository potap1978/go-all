"""The activity of devices in the collector (k3/handoff/SPEC-activity.md §3.2, §3.3, §8.1; AC3): the block of the round
runs no command (ACT-1), its error never breaks the round, a window reaches metrics.db and live.json, /run once a
minute and after a window, a restart goes on without a peak (ACT-4), the clock not synchronised — the windows wait, a
deleted device leaves the base at the next window, the history off erases it (ACT-8)."""

import json
import os
import sqlite3
from unittest import mock

from kaskad import activity as A, state
from kaskad.gw import collect, hostprobe, live, metrics, model, policy_ops
from tests.gw.test_collect_s1 import S1World

MB = 1024 * 1024


class ActWorld(S1World):
    def setUp(self):
        super().setUp()
        self.ctr = {}                                      # device id -> [rx, tx] of its fake session
        self.online = set()
        p = mock.patch.object(collect, "sessions", self.fake_sessions)
        p.start()
        self.addCleanup(p.stop)
        self.did = self.dev()["id"]

    def fake_sessions(self, st, runner, stats=None):
        return [{"kind": "wg", "device": d, "ip": "10.77.1.%d" % (10 + i), "since": int(self.now) - 60,
                 "rx": self.ctr.setdefault(d, [10 ** 9, 10 ** 9])[0], "tx": self.ctr[d][1]}
                for i, d in enumerate(sorted(self.online))]

    def traffic(self, d, rx, tx):
        c = self.ctr.setdefault(d, [10 ** 9, 10 ** 9])     # a counter far from 0: a first sight must not count it
        c[0] += rx
        c[1] += tx

    def to_next_window(self, per_round=(0, 0), d=None):
        """Rounds of 10 s until a new 5-minute window starts; ``per_round`` bytes a round for ``d``."""
        start = int(self.now) // 300
        while int(self.now) // 300 == start:
            if d:
                self.traffic(d, *per_round)
            self.round()

    def rows(self, t="dev_m5"):
        conn = sqlite3.connect(self.path("/var/lib/kaskad/metrics.db"))
        try:
            return conn.execute("SELECT ts, dev, on_s, act_s, rx, tx FROM %s ORDER BY ts, dev" % t).fetchall()
        finally:
            conn.close()

    def live_act(self):
        return json.loads(self.read("/run/kaskad/live.json")).get("activity")


class RoundTest(ActWorld):
    def test_the_block_runs_no_command(self):
        """ACT-1: the activity takes the sessions the round has — not one call of the runner of its own."""
        real, seen = collect._activity_round, []

        def counted(st, ss, now=None):
            n = len(self.w.runner.calls)
            out = real(st, ss, now)
            seen.append(len(self.w.runner.calls) - n)
            return out
        self.online.add(self.did)
        with mock.patch.object(collect, "_activity_round", counted):
            self.to_next_window((50000, 5000), self.did)
            self.to_next_window((50000, 5000), self.did)
        self.assertGreater(len(seen), 30)
        self.assertEqual(set(seen), {0})
        self.assertTrue(self.rows())                         # and it did write its windows

    def test_an_error_of_the_block_does_not_break_the_round(self):
        with mock.patch.object(A, "step", side_effect=RuntimeError("boom")):
            res = self.round()
        self.assertTrue(res["enabled"])
        self.assertIn("sessions", res)
        self.assertTrue(os.path.exists(self.path("/run/kaskad/live.json")))
        self.assertIn("GW collect: activity — RuntimeError: boom", self.log())

    def test_a_window_reaches_the_base_and_live(self):
        self.online.add(self.did)
        self.to_next_window((40000, 4000), self.did)         # the first window: the first sight counts 0
        self.to_next_window((40000, 4000), self.did)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        ts, dev, on, act, rx, tx = rows[-1]
        self.assertEqual((dev, on, act), (self.did, 300, 300))
        self.assertEqual((rx, tx), (30 * 40000, 30 * 4000))
        live = self.live_act()                               # 3.3.7.10: the written window and the open one after it
        self.assertEqual((live["ts"], live["w"], live["secs"] >= 0), (ts, ts + 300, True))
        self.assertLessEqual(live["w"], live["at"])
        self.assertTrue(set(live["cur"]) <= {self.did})
        self.assertEqual(self.rows("dev_h1")[-1][1], self.did)
        self.assertEqual(len(self.rows("dev_d1")), 1)

    def test_the_run_file_once_a_minute_and_after_a_window(self):
        self.online.add(self.did)
        self.round()
        path = self.path("/run/kaskad/gw/activity.json")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        first = self.read("/run/kaskad/gw/activity.json")
        self.round()
        self.assertEqual(self.read("/run/kaskad/gw/activity.json"), first)       # 10 s later: not again
        for _ in range(6):
            self.round()
        self.assertNotEqual(self.read("/run/kaskad/gw/activity.json"), first)    # a minute later: saved
        doc = json.loads(self.read("/run/kaskad/gw/activity.json"))
        self.assertNotIn("10.77", json.dumps(doc["cur"]))                         # the open window: ids and numbers

    def test_a_restart_goes_on_without_a_peak(self):
        """§3.3: a new process takes the accumulator from /run — the counters go on, the window of the restart holds
        only the traffic of its rounds (not the gigabyte of the counter)."""
        self.online.add(self.did)
        self.to_next_window((1000, 1000), self.did)
        for _ in range(7):                                   # into the next window, past a save of /run
            self.traffic(self.did, 1000, 1000)
            self.round()
        collect.MEM.reset()                                  # a restart of the collector process
        with mock.patch.object(collect, "LIVE", live.Live()):
            self.to_next_window((1000, 1000), self.did)
        rx = self.rows()[-1][4]
        self.assertEqual(rx, 30 * 1000)                      # a whole window of 1000 a round, no peak, nothing lost

    def test_the_clock_not_synchronised_keeps_the_windows_waiting(self):
        self.online.add(self.did)
        with mock.patch.object(hostprobe, "clock", lambda: {"synced": False, "offset_s": 0.0}):
            for _ in range(3):
                self.to_next_window((1000, 0), self.did)
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(collect.MEM.act["acc"]["wait"]), 3)
        self.to_next_window((1000, 0), self.did)
        self.assertEqual(len(self.rows()), 4)                 # synchronised: the waiting ones go first, in order
        self.assertEqual(collect.MEM.act["acc"]["wait"], [])

    def test_a_deleted_device_leaves_the_base_at_the_next_window(self):
        self.ok("gw", "device", "add", "Мама", "B", "--access", "wg")
        other = [d["id"] for d in model.devices(state.load()) if d["id"] != self.did][0]
        self.online.update({self.did, other})
        self.to_next_window((5000, 5000), self.did)
        self.to_next_window((5000, 5000), other)
        self.assertEqual(sorted(set(r[1] for r in self.rows())), sorted([self.did, other]))
        self.ok("gw", "device", "del", other)
        self.online.discard(other)
        self.to_next_window((5000, 5000), self.did)
        for t in metrics.DEV_TABLES:
            self.assertEqual(set(r[1] for r in self.rows(t)), {self.did}, t)
        self.assertNotIn(other, json.dumps(collect.MEM.act["acc"]))


class KeepOffTest(ActWorld):
    def test_off_erases_and_stops(self):
        self.online.add(self.did)
        self.to_next_window((5000, 5000), self.did)
        self.to_next_window((5000, 5000), self.did)
        self.assertTrue(self.rows())
        ver = policy_ops.activity_get()["ver"]
        policy_ops.activity_set(False, want_ver=ver)
        self.round()
        for t in metrics.DEV_TABLES + ("dev_node",):
            self.assertEqual(self.rows(t) if t != "dev_node" else self.node_rows(), [], t)
        self.assertFalse(os.path.exists(self.path("/run/kaskad/gw/activity.json")))
        self.assertIsNone(self.live_act())
        self.assertIn("GW activity: the history of activity is off", self.log())
        self.to_next_window((5000, 5000), self.did)
        self.assertEqual(self.rows(), [])                    # nothing counted while off
        self.assertEqual(self.log().count("history of activity is off"), 1)    # once, not every round
        policy_ops.activity_set(True, want_ver=policy_ops.activity_get()["ver"])
        self.to_next_window((5000, 5000), self.did)
        self.to_next_window((5000, 5000), self.did)
        self.assertEqual(len(self.rows()), 2)                # on again: from a first sight (0), then counted
        self.assertEqual(self.rows()[-1][4], 30 * 5000)

    def test_off_with_the_base_not_open_erases_later(self):
        """The writer not there at the round of «off» (the base did not open): nothing is marked done — a later round
        with the base erases dev_* (ACT-8: the history never stays because the base was busy once)."""
        self.online.add(self.did)
        self.to_next_window((5000, 5000), self.did)
        self.to_next_window((5000, 5000), self.did)
        self.assertTrue(self.rows())
        policy_ops.activity_set(False, want_ver=policy_ops.activity_get()["ver"])
        with mock.patch.object(collect, "_writer", lambda: False):
            self.round()
        self.assertTrue(self.rows())                         # not yet: no base this round
        self.round()
        self.assertEqual(self.rows(), [])
        self.assertIn("GW activity: the history of activity is off", self.log())

    def node_rows(self):
        conn = sqlite3.connect(self.path("/var/lib/kaskad/metrics.db"))
        try:
            return conn.execute("SELECT * FROM dev_node").fetchall()
        finally:
            conn.close()

    def test_the_ver_guards_the_switch(self):
        from kaskad.errors import KaskadError
        with self.assertRaises(KaskadError) as c:
            policy_ops.activity_set(False, want_ver="000000000000")
        self.assertEqual(getattr(c.exception, "status", None), 409)                 # changed elsewhere
        self.assertTrue(model.activity_keep(state.load()))
        with self.assertRaises(KaskadError):
            policy_ops.activity_set("no", want_ver=policy_ops.activity_get()["ver"])


class OpenWindowLiveTest(ActWorld):
    """3.3.7.10: live.json carries the open window after every full round — the devices of the state only; the history
    off — no ``activity`` at all."""

    def test_the_open_window_grows_every_round(self):
        self.online.add(self.did)
        self.to_next_window((40000, 4000), self.did)          # the first sight counts 0
        self.traffic(self.did, 50000, 5000)
        self.round()
        a = self.live_act()
        got = a["cur"][self.did]
        self.traffic(self.did, 50000, 5000)
        self.round()
        b = self.live_act()
        self.assertEqual((b["w"], b["ts"]), (a["w"], a["ts"]))
        self.assertEqual((b["cur"][self.did][2] - got[2], b["cur"][self.did][3] - got[3]), (50000, 5000))
        self.assertGreater(b["at"], a["at"])

    def test_a_deleted_device_is_not_in_the_open_window(self):
        self.ok("gw", "device", "add", "Мама", "B", "--access", "wg")
        other = [d["id"] for d in model.devices(state.load()) if d["id"] != self.did][0]
        self.online.update({self.did, other})
        self.round()                                              # the first sight of both counts 0
        self.traffic(self.did, 50000, 5000)
        self.traffic(other, 50000, 5000)
        self.round()
        self.assertIn(other, self.live_act()["cur"])
        self.ok("gw", "device", "del", other)
        self.traffic(other, 50000, 5000)
        self.round()                                              # still in the accumulator until the window ends
        self.assertNotIn(other, self.live_act()["cur"])
        self.assertIn(self.did, self.live_act()["cur"])

    def test_off_no_activity_in_live(self):
        self.online.add(self.did)
        self.to_next_window((5000, 5000), self.did)
        policy_ops.activity_set(False, want_ver=policy_ops.activity_get()["ver"])
        self.round()
        self.assertIsNone(self.live_act())

