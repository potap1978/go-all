"""B11 круг 2: the limits of the repeat are read again when it fires (SEC С-1, REV З-1…З-4) — the switch, the health,
the schedule armed on this machine, 2 a day with the repeats still waiting; the window of the schedule around the real
moment; the disarm stops the waiting repeats; a timer left over is removed from inside (only the timer) and by the
update; a mark of sched-state.json from the future is no mark (SEC П-2); the repeat is a oneshot (SEC П-3)."""

import json
import os
import time
import unittest
import warnings
from unittest import mock

from kaskad import state, sysrun, unit_refresh
from kaskad.bench import run as brun, sched_run, sched_unit, spool
from kaskad.gw import sys as gsys
from tests.bench.test_sched_run import HERE, MOSCOW, Case, FakeClock, FakeMeter, ent, utc

NOW = utc(2026, 10, 19, 9, 0)                        # Mon 12:00 MSK


class Rng(object):
    def __init__(self, s):
        self.s = s

    def randint(self, a, b):
        return self.s


class LimitsCase(Case):
    def setUp(self):
        super().setUp()
        self.save([ent("03:45", [3], "aaaaaa")])     # Wed only: no event near Mon
        self.runner.on(["systemd-run"], "")

    def plan(self, rid, now=NOW, s=3600):
        return sched_run.plan_retry(self.runner, {"id": rid, "by": "panel", "ts": now - 300}, ["up"], now=now,
                                    rng=Rng(s), here=HERE)

    def fire(self, rid="0123456789", t=NOW + 3600, meter_mbps=0.0):
        clock = FakeClock(t)
        with mock.patch.object(sched_run, "_source", return_value={"mode": "full"}):
            return sched_run.retry(rid, self.runner, clock, FakeMeter(clock, mbps=meter_mbps), here=HERE, links=["up"])

    def skips(self):
        return [(r["by"], r["retry_of"], r["why"]) for r in self.queue() if r["status"] == "skipped"]


class FireTest(LimitsCase):
    """SEC E1: the switches saved after the plan, then the repeat fires — no start, a row without a start."""

    def test_e1_each_switch_stops_the_repeat(self):
        for kw, why in (({"retry": False}, "retry_off"), ({"armed": False}, "not_armed"),
                        ({"enabled": False}, "disabled")):
            self.save([ent("03:45", [3], "aaaaaa")], **kw)
            self.started = []
            self.assertEqual(self.fire(), {"skip": why}, kw)
            self.assertEqual(self.started, [], kw)
        self.assertEqual(sorted(w for _b, _r, w in self.skips()), ["disabled", "not_armed", "retry_off"])

    def test_another_machine_is_not_armed(self):
        clock = FakeClock(NOW + 3600)
        with mock.patch.object(sched_run, "_source", return_value={"mode": "quick"}):
            out = sched_run.retry("0123456789", self.runner, clock, FakeMeter(clock), here="f" * 32, links=["up"])
        self.assertEqual((out, self.started), ({"skip": "not_armed"}, []))

    def test_switched_off_during_the_wait_for_quiet(self):
        """The health turned off while the repeat waited for quiet: read again after ``_gate``."""
        real = sched_run._gate

        def gate(*a, **kw):
            self.save([ent("03:45", [3], "aaaaaa")], enabled=False)
            return real(*a, **kw)
        with mock.patch.object(sched_run, "_gate", side_effect=gate):
            self.assertEqual(self.fire(), {"skip": "disabled"})
        self.assertEqual(self.started, [])

    def test_the_schedule_also_reads_again_after_the_wait(self):
        self.save([ent("03:45", range(1, 8), "aaaaaa")])
        real = sched_run._gate

        def gate(*a, **kw):
            self.save([ent("03:45", range(1, 8), "aaaaaa")], armed=False)
            return real(*a, **kw)
        with mock.patch.object(sched_run, "_gate", side_effect=gate):
            out = Case.fire(self, utc(2026, 10, 19, 0, 31))
        self.assertEqual((out["skip"], self.started), ("not_armed", []))

    def test_all_on_the_repeat_starts(self):
        self.assertIn("id", self.fire())
        self.assertEqual(self.started, [("full", "auto", None, "0123456789")])


class DayTest(LimitsCase):
    def test_e2_four_plans_in_an_hour_two_timers(self):
        got = [self.plan("000000000%d" % i, now=NOW + 600 * i) for i in range(1, 5)]
        self.assertEqual([("set" in g) for g in got], [True, True, False, False], got)
        self.assertEqual([g.get("blocked") for g in got[2:]], ["day", "day"])
        self.assertEqual(len(self.runner.commands(["systemd-run"])), 2)
        self.assertEqual(sorted(sched_run.load_pending(NOW)), ["0000000001", "0000000002"])

    @unittest.skipUnless(hasattr(os, "fork"), "fork")
    def test_four_plans_in_four_processes_two_timers(self):
        """REV-ns-b11-2 З-2 (M13): four checks plan at the same moment in four processes, ``systemd-run`` is slow —
        the flock of sched-state.json lets exactly two set a timer (without it 3–4 do, and the count knows of 2)."""
        r, w = os.pipe()
        kids = []
        real = sched_unit.schedule_retry

        def slow(runner, rid, **kw):
            time.sleep(0.3)
            return real(runner, rid, **kw)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)     # fork of a process with threads (3.12+)
            for i in range(4):
                pid = os.fork()
                if pid == 0:                                        # pragma: no cover — the child
                    code = 1
                    try:
                        with mock.patch.object(sched_unit, "schedule_retry", side_effect=slow):
                            res = self.plan("000000000%d" % (i + 1))
                        os.write(w, (json.dumps(res, sort_keys=True) + "\n").encode())
                        code = 0
                    except BaseException:                           # the child's own traceback, not just «256»
                        import traceback
                        traceback.print_exc()
                    finally:
                        os._exit(code)
                kids.append(pid)
        codes = [os.waitpid(p, 0)[1] for p in kids]
        os.close(w)
        with os.fdopen(r, "rb") as f:
            got = [json.loads(x) for x in f.read().decode().split("\n") if x]
        self.assertEqual(codes, [0, 0, 0, 0])
        self.assertEqual(sorted("set" in g for g in got), [False, False, True, True], got)
        self.assertEqual(sorted(g.get("blocked") for g in got if "blocked" in g), ["day", "day"])
        self.assertEqual(len(sched_run.load_pending(NOW)), 2)

    def test_the_plan_gives_the_timer_the_links_that_fell(self):
        """The join of B11 and B3: the repeat measures the links that fell alone — their names go with the timer."""
        self.assertIn("set", self.plan("0000000001"))
        self.assertEqual(self.runner.commands(["systemd-run"])[0][-4:], ["--retry", "0000000001", "--links", "up"])

    def test_one_per_check_a_waiting_one_too(self):
        self.assertIn("set", self.plan("0000000001"))
        self.assertEqual(self.plan("0000000001"), {"blocked": "has_one"})

    def test_the_waiting_ones_count_when_a_repeat_fires(self):
        self.plan("0000000001")
        self.plan("0000000002", s=3700)
        self.assertIn("id", self.fire("0000000001"), "the other waiting one is 1 < 2")
        st = sched_run._read()
        st["retries"]["0000000003"] = NOW + 4000                       # a third waiting (from before the fix)
        sched_run._write(st)
        self.assertEqual(self.fire("0000000002", t=NOW + 3700), {"skip": "retry_day"})

    def test_a_repeat_that_did_not_start_is_not_one_of_two(self):
        """REV-ns-b11-2 З-2 (M8): the row ``retry_off`` (at NOW + 3600) must be SEEN by the plan — so the plan is
        later than it (the history cuts what is later than ``now``); seen and not counted: two repeats still go."""
        self.save([ent("03:45", [3], "aaaaaa")], retry=False)
        self.fire("0000000009")                                         # retry_off: a row, no start
        self.save([ent("03:45", [3], "aaaaaa")])
        later = NOW + 7200
        self.assertEqual([r["why"] for r in sched_run._auto_runs(later)], ["retry_off"])
        self.assertIn("set", self.plan("0000000001", now=later))
        self.assertIn("set", self.plan("0000000002", now=later))
        self.assertEqual(self.plan("0000000003", now=later), {"blocked": "day"})

    def test_a_repeat_that_started_is_one_of_two(self):
        """The other side of the rule: a repeat that started (``ok``) counts — one more only."""
        self.fire("0000000009")                                         # all on: the repeat starts
        r = brun._run_doc("0000000077", "full", "auto", None, "0000000009", NOW + 3600)
        r.update(status="ok", end_ts=NOW + 3700)
        spool.put({"v": 1, "run": r, "tests": []})
        later = NOW + 7200
        self.assertIn("set", self.plan("0000000001", now=later))
        self.assertEqual(self.plan("0000000002", now=later), {"blocked": "day"})

    def test_the_limits_before_the_wait_cost_no_wait(self):
        """REV-ns-b11-2 З-2 (M17): the switch off or the day full — the repeat never waits for quiet first."""
        self.save([ent("03:45", [3], "aaaaaa")], retry=False)
        clock = FakeClock(NOW + 3600)
        with mock.patch.object(sched_run, "_source", return_value={"mode": "full"}):
            out = sched_run.retry("0000000005", self.runner, clock, FakeMeter(clock, mbps=50.0), here=HERE, links=["up"])
        self.assertEqual((out, clock.slept), ({"skip": "retry_off"}, 0))
        self.save([ent("03:45", [3], "aaaaaa")])
        st = sched_run._read()
        st["retries"] = {"0000000001": int(NOW + 3000), "0000000002": int(NOW + 3100)}
        sched_run._write(st)
        clock = FakeClock(NOW + 3600)
        with mock.patch.object(sched_run, "_source", return_value={"mode": "full"}):
            out = sched_run.retry("0000000005", self.runner, clock, FakeMeter(clock, mbps=50.0), here=HERE, links=["up"])
        self.assertEqual((out, clock.slept), ({"skip": "retry_day"}, 0))

    def test_the_day_of_the_real_moment(self):
        """Two repeats waiting for 21:30 MSK; a plan at 23:00 whose repeat fires after midnight — the next day's
        first; a plan at 22:00 whose repeat fires at 23:00 — the third of the 19th."""
        early = utc(2026, 10, 19, 17, 30)                               # 20:30 MSK
        self.plan("0000000001", now=early)
        self.plan("0000000002", now=early)
        late = utc(2026, 10, 19, 20, 0)                                 # 23:00 MSK
        self.assertIn("set", self.plan("0000000003", now=late, s=3700))
        self.assertEqual(self.plan("0000000004", now=late - 3600, s=3600), {"blocked": "day"})

    def test_a_failed_systemd_run_is_not_waiting(self):
        bad = sysrun.FakeRunner()
        bad.on(["systemd-run"], (1, "", "no bus\n"))
        self.runner = bad
        self.assertEqual(self.plan("0000000001"), {"blocked": "error"})
        self.assertEqual(sched_run.load_pending(NOW), {})


class WindowTest(LimitsCase):
    def test_the_window_is_around_the_real_moment(self):
        """REV З-1: the event at 12:40, the repeat at 13:00 (s = 3600) — inside [−30, +120] of 13:00: no repeat.
        (By the middle of 60–120 min — 13:30, the window 13:00…15:30 — the event was outside.)"""
        self.save([ent("12:40", [1], "bbbbbb")])
        self.assertEqual(self.plan("0000000001", s=3600), {"blocked": "schedule"})
        self.save([ent("12:40", [1], "bbbbbb")])
        self.assertIn("set", self.plan("0000000002", s=7200))          # 14:00: 12:40 is 80 min before — outside

    def test_the_health_off_plans_none(self):
        self.save([ent("03:45", [3], "aaaaaa")], enabled=False)
        self.assertEqual(self.plan("0000000001"), {"blocked": "disabled"})


class DisarmTest(LimitsCase):
    def setUp(self):
        super().setUp()
        self.runner.on(["systemctl", "is-active"], (3, "inactive\n", ""))

    def unit(self, name):
        p = gsys.unit_path(name)
        return open(p).read() if os.path.exists(p) else None

    def test_disarm_stops_the_waiting_repeats(self):
        self.plan("0000000001")
        self.save([ent("03:45", [3], "aaaaaa")], armed=False)
        sched_unit.apply(self.runner, state.load(), NOW, here=HERE)
        stops = [c[0] for c in self.runner.calls if c[0][:2] == ["systemctl", "stop"]]
        self.assertIn(["systemctl", "stop", "kaskad-bench-retry-*.timer", "kaskad-bench-retry-*.service"], stops)
        self.assertEqual(sched_run.load_pending(NOW), {})

    def test_armed_with_no_entry_keeps_the_repeats(self):
        self.plan("0000000001")
        self.save([], armed=True)
        sched_unit.apply(self.runner, state.load(), NOW, here=HERE)
        self.assertFalse([c for c in self.runner.calls if c[0][:2] == ["systemctl", "stop"]])
        self.assertEqual(list(sched_run.load_pending(NOW)), ["0000000001"])

    def test_not_armed_firing_removes_only_the_timer(self):
        """REV З-4 + SEC П-1: a timer left over (a clone of the disk) — the firing removes the timer, never stops
        its own service."""
        self.save([ent("03:45", range(1, 8), "aaaaaa")])
        sched_unit.apply(self.runner, state.load(), NOW, here=HERE)
        self.save([ent("03:45", range(1, 8), "aaaaaa")], armed=False)
        self.runner.calls[:] = []
        Case.fire(self, utc(2026, 10, 19, 0, 31))
        self.assertIsNone(self.unit(sched_unit.TIMER))
        self.assertIsNotNone(self.unit(sched_unit.SERVICE))
        self.assertNotIn(["systemctl", "disable", "--now", sched_unit.SERVICE], [c[0] for c in self.runner.calls])
        self.assertEqual(self.started, [])

    def test_the_update_removes_an_unwanted_timer(self):
        self.save([ent("03:45", range(1, 8), "aaaaaa")])
        sched_unit.apply(self.runner, state.load(), NOW, here=HERE)
        self.save([ent("03:45", range(1, 8), "aaaaaa")], armed=False)
        with mock.patch("kaskad.bench.machine.mid", return_value=HERE), \
                mock.patch.object(unit_refresh, "_gates", return_value=[]), \
                mock.patch("kaskad.rules.rfgate.migrate_node", return_value=None):
            res = unit_refresh.refresh(self.runner)
        self.assertIn(sched_unit.TIMER, res["removed"])
        self.assertIsNone(self.unit(sched_unit.TIMER))

    def test_a_state_read_error_keeps_the_timer_on_update(self):
        self.save([ent("03:45", range(1, 8), "aaaaaa")])
        sched_unit.apply(self.runner, state.load(), NOW, here=HERE)
        before = self.unit(sched_unit.TIMER)
        with mock.patch.object(state, "load", side_effect=ValueError("broken")):
            self.assertEqual(sched_unit.refresh_text(), before)


class MarksTest(Case):
    def test_a_mark_from_the_future_is_no_mark(self):
        """SEC П-2 (E4): ``done`` 2099 — the event runs, the mark is written back sane."""
        sched_run.save_done({"aaaaaa": "2099-01-01 03:45"})
        out = self.fire(utc(2026, 10, 19, 0, 31))
        self.assertEqual(out["done"], "2026-10-19 03:45")
        self.assertEqual(len(self.started), 1)
        self.assertEqual(sched_run.load_done(), {"aaaaaa": "2026-10-19 03:45"})

    def test_a_mark_of_tomorrow_stays(self):
        sched_run.save_done({"aaaaaa": "2026-10-20 03:45"})
        self.assertEqual(sched_run.load_done(utc(2026, 10, 19, 9, 0), MOSCOW), {"aaaaaa": "2026-10-20 03:45"})

    def test_the_save_of_done_keeps_the_waiting_repeats(self):
        with sched_run._locked():
            sched_run._pending_set("0000000001", NOW + 3600, NOW)
        sched_run.save_done({"aaaaaa": "2026-10-19 03:45"})
        self.assertEqual(sched_run.load_pending(NOW), {"0000000001": NOW + 3600})
        self.assertEqual(sched_run.load_pending(NOW + 3 * 86400), {}, "past the keep — forgotten")


class OneshotTest(Case):
    def test_the_repeat_is_a_oneshot(self):
        r = sysrun.FakeRunner()
        r.on(["systemd-run"], "")
        sched_unit.schedule_retry(r, "0123456789", s=4000, links=["up"])
        argv = r.calls[0][0]
        self.assertIn("--service-type=oneshot", argv)
        self.assertIn("--on-active=4000", argv)
        i = argv.index("TimeoutStartSec=90min")
        self.assertEqual(argv[i - 1], "-p")
        for bad in (10, 100000):
            with self.assertRaises(Exception):
                sched_unit.schedule_retry(r, "0123456789", s=bad, links=["up"])


class MarkAfterSaveTest(Case):
    def test_the_timer_is_applied_after_the_save_outside_the_lock(self):
        """SEC П-5: ``marks.AFTER`` sees the mark saved and the lock of state.json free (no systemctl under it)."""
        from kaskad.bench import machine, marks
        self.save([ent("03:45", [3], "aaaaaa")], armed=False)
        seen = []

        def after(st):
            with state.transaction(0.5) as cur:                    # the lock is free
                seen.append(machine.status(cur["gateway"]["hostmon"]["bench"], HERE)["first_manual_at"])
        with mock.patch.object(marks, "AFTER", [after]), mock.patch.object(machine, "mid", return_value=HERE):
            self.assertTrue(marks.record(first_manual=True, now=NOW))
        self.assertEqual(seen, [NOW])
