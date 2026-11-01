"""B11: the timer of the schedule (SPEC-node-score-schedule-share §2.4, NS-S4, NS-S7): the text of the units (the lines
of OnCalendar only of numbers and the zone after fullmatch), the fallback UTC, no timer when nothing is on or not armed,
a failing systemctl, the update of Kaskad (unit_refresh), the mark of the first check arms it, ``gw purge`` leaves it."""

import os
from unittest import mock

from kaskad import state, sysrun, tz, unit_refresh
from kaskad.bench import marks, sched_run, sched_unit
from kaskad.gw import sys as gsys
from tests.bench.test_sched_run import HERE, Case, ent, utc

NOW = utc(2026, 10, 19, 9, 0)


class TextTest(Case):
    def test_lines_and_the_service(self):
        text = sched_unit.wanted(state.load(), NOW, here=HERE)[0]
        self.assertIn("OnCalendar=Mon,Tue,Wed,Thu,Fri,Sat,Sun *-*-* 03:30:00 Europe/Moscow\n", text)
        for line in ("RandomizedDelaySec=30min", "AccuracySec=1s", "Persistent=false",
                     "Unit=kaskad-bench-sched.service"):
            self.assertIn(line + "\n", text)
        svc = sched_unit.service_text()
        self.assertIn("Type=oneshot\n", svc)
        self.assertIn("TimeoutStartSec=90min\n", svc)
        self.assertTrue(svc.rstrip().endswith("host bench --scheduled"))

    def test_only_checked_lines_reach_the_file(self):
        bad = ["Mon *-*-* 03:30:00 Europe/Moscow\nExecStart=/bin/sh", "Mon *-*-* 03:30:00 Europe/../x",
               "Mon *-*-* 24:00:00 UTC", "Mon *-*-* 03:30:00 Europe/Moscow ", "", None, "2027-03-28 01:15:00 MSK"]
        for b in bad:
            self.assertFalse(sched_unit.line_ok(b), repr(b))
        self.assertIsNone(sched_unit.timer_text(bad))
        self.assertTrue(sched_unit.line_ok("Sun *-*-01..06 23:50:00 Europe/Moscow"))
        self.assertTrue(sched_unit.line_ok("2027-03-28 01:15:00 UTC"))

    def test_zone_with_a_newline_falls_back_to_utc(self):
        """§2.4: ``tz.NAME_RE`` lets «…\n» pass by «$» — the suffix is never written, the lines go in UTC + warn."""
        for name in ("Europe/Moscow\n", "../etc", "Nowhere/Zone"):
            with mock.patch.object(tz, "name", return_value=name), \
                    mock.patch.object(tz, "load", side_effect=lambda n: tz.Zone.fixed(n, tz.FALLBACK_OFFSET)) as ld:
                zone, warn = _REAL_ZONE()
            self.assertEqual((zone.ok, warn, zone.name), (False, "tz_fallback", "UTC"), repr(name))
            if name.endswith("\n") or ".." in name:
                ld.assert_not_called()

    def test_fallback_lines_are_utc(self):
        with mock.patch.object(sched_unit, "_zone", return_value=(tz.Zone.fixed("UTC", tz.FALLBACK_OFFSET),
                                                                  "tz_fallback")):
            self.save([ent("01:00", [1], "aaaaaa")])
            text, warn, lines = sched_unit.wanted(state.load(), NOW, here=HERE)
        self.assertEqual((lines, warn), (["Sun *-*-* 21:45:00 UTC"], "tz_fallback"))
        self.assertIn("OnCalendar=Sun *-*-* 21:45:00 UTC\n", text)


_REAL_ZONE = sched_unit._zone


def _real_zone():
    return _REAL_ZONE()


class ApplyTest(Case):
    def setUp(self):
        super().setUp()
        self.runner = sysrun.FakeRunner()
        self.runner.on(["systemctl", "is-active"], (3, "inactive\n", ""))
        self.runner.on(["systemctl"], "")

    def unit(self, name):
        p = gsys.unit_path(name)
        return open(p).read() if os.path.exists(p) else None

    def test_armed_writes_and_enables(self):
        r = sched_unit.apply(self.runner, state.load(), NOW, here=HERE)
        self.assertEqual((r["applied"], r["armed"]), (True, True))
        self.assertIn("OnCalendar=", self.unit(sched_unit.TIMER))
        self.assertIn("--scheduled", self.unit(sched_unit.SERVICE))
        self.assertTrue(any(c[0][:2] == ["systemctl", "enable"] for c in self.runner.calls))

    def test_not_armed_or_empty_removes(self):
        sched_unit.apply(self.runner, state.load(), NOW, here=HERE)
        for sched, armed in (([ent("03:45", [1], "aaaaaa")], False), ([], True),
                             ([ent("03:45", [1], "aaaaaa", on=False)], True)):
            self.save(sched, armed=armed)
            sched_unit.apply(self.runner, state.load(), NOW, here=HERE)
            self.assertIsNone(self.unit(sched_unit.TIMER))
            self.assertIsNone(self.unit(sched_unit.SERVICE))
        self.save([ent("03:45", [1], "aaaaaa")])
        r = sched_unit.apply(self.runner, state.load(), NOW, here="0" * 32)    # another machine: not armed
        self.assertEqual(r["armed"], False)

    def test_systemctl_fails_the_state_stays(self):
        bad = sysrun.FakeRunner()
        bad.on(["systemctl"], (1, "", "Failed to connect to bus\n"))
        before = state.load()
        r = sched_unit.apply(bad, before, NOW, here=HERE)
        self.assertFalse(r["applied"])
        self.assertIn("Failed to connect to bus", r["why"])
        self.assertEqual(state.load(), before)

    def test_unit_refresh_knows_the_timer(self):
        names = [n for n, _f, _k in unit_refresh.registry()]
        self.assertIn(sched_unit.TIMER, names)
        self.assertIn(sched_unit.SERVICE, names)
        fn = dict((n, f) for n, f, _k in unit_refresh.registry())[sched_unit.TIMER]
        with mock.patch("kaskad.bench.machine.mid", return_value=HERE):
            self.assertIn("OnCalendar=", fn())
        self.save([], armed=True)
        self.assertIsNone(fn(), "no timer wanted: the update removes the file (REV З-4)")

    def test_the_mark_arms_the_timer(self):
        self.assertIn(marks._timer, marks.AFTER)
        with mock.patch.object(sched_unit, "RUNNER", [self.runner]):
            sched_unit.after_mark(state.load())
        self.assertIsNotNone(self.unit(sched_unit.TIMER))

    def test_the_firing_redraws_drifted_lines(self):
        sched_unit.apply(self.runner, state.load(), NOW, here=HERE)
        self.save([ent("05:45", range(1, 8), "aaaaaa")])                 # the state moved, the file is old
        with mock.patch("kaskad.bench.machine.mid", return_value=HERE):
            sched_run._redraw(self.runner, NOW)
        self.assertIn("05:30:00", self.unit(sched_unit.TIMER))

    def test_gw_purge_leaves_the_timer(self):
        from kaskad.gw.probe import unit as probe_unit
        self.assertNotIn(sched_unit.TIMER, probe_unit.UNITS)
        src = open(os.path.join(os.path.dirname(gsys.__file__), "ops.py")).read()
        self.assertNotIn("kaskad-bench-sched", src)


class CallersTest(Case):
    """§2.4: who calls ``apply`` — the restore of a .kbk (marks of another machine dropped), the zone of the panel."""

    def setUp(self):
        super().setUp()
        self.runner = sysrun.FakeRunner()
        self.runner.on(["systemctl", "is-active"], (3, "inactive\n", ""))
        self.runner.on(["systemctl"], "")

    def test_restore_of_another_machine_drops_the_marks(self):
        from kaskad import backup
        g = {"hostmon": {"bench": {"mid": "f" * 32, "first_manual_at": 5, "pkgs": "yes", "armed_at": 5,
                                   "schedule": [ent("03:45", [1], "aaaaaa")]}}}
        with mock.patch("kaskad.bench.machine.mid", return_value=HERE):
            backup._bench_marks_of_this_machine(g)
        self.assertEqual(sorted(g["hostmon"]["bench"]), ["schedule"])
        own = {"hostmon": {"bench": {"mid": HERE, "first_manual_at": 5, "pkgs": "yes", "armed_at": 5}}}
        with mock.patch("kaskad.bench.machine.mid", return_value=HERE):
            backup._bench_marks_of_this_machine(own)
        self.assertEqual(own["hostmon"]["bench"]["pkgs"], "yes")

    def test_restore_applies_the_timer(self):
        from kaskad import backup

        class Ctx(object):
            runner, warnings = self.runner, []
        self.save([ent("03:45", [1], "aaaaaa")], armed=False)
        p = gsys.unit_path(sched_unit.TIMER)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("[Timer]\nOnCalendar=Mon *-*-* 03:30:00 Europe/Moscow\n")
        backup._bench_timer(Ctx)
        self.assertFalse(os.path.exists(p), "not armed after the restore: the timer is removed")
        self.assertEqual(Ctx.warnings, [])

    def test_the_zone_of_the_panel_applies(self):
        from kaskad.gw import policy_ops
        with mock.patch.object(sched_unit, "RUNNER", [self.runner]), \
                mock.patch.object(sched_unit, "apply", return_value={"applied": True}) as ap, \
                mock.patch.object(tz, "set_name"):
            policy_ops.tz_set("Europe/Berlin")
        ap.assert_called_once()

    def test_no_root_no_systemctl(self):
        with mock.patch.object(os, "geteuid", return_value=1000):
            self.assertIsNone(sched_unit.system_runner())
