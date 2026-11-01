"""B11: the runner of the schedule and of the repeat (SPEC-node-score-schedule-share §2.5, §2.8, NS-S6, NS-S8): a fake
runner, a fake clock and a fake meter — the missed rows, ``due`` around midnight, the skips ``disabled``, ``running``,
``busy``, ``locked``, «quiet» by the window of 5 min, the marks of sched-state.json, the limits of the repeat."""

import calendar
import fcntl
import json
import os
import stat
from unittest import mock

from kaskad import paths, state, sysrun, tz
from kaskad.bench import machine, run, sched_run, sched_unit, spool
from kaskad.errors import KaskadError
from tests.helpers import RootCase

MOSCOW = tz.Zone("Europe/Moscow", types=[(10800, False)])
MID_RAW = "0123456789abcdef0123456789abcdef"
HERE = machine.mid_of(MID_RAW)


def utc(y, mo, d, h=0, mi=0):
    return calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0))


class FakeClock(object):
    def __init__(self, t):
        self.t = float(t)
        self.slept = 0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.slept += s


class FakeMeter(object):
    """People traffic ``mbps`` (rx, or tx with ``side``) and the CPU of others ``cpu`` %, from the clock."""

    def __init__(self, clock, mbps=0.0, cpu=0.0, side="rx", busy_until=None):
        self.clock, self.mbps, self.cpu, self.side, self.busy_until = clock, mbps, cpu, side, busy_until
        self.n, self.rx, self.tx, self.busy, self.total, self.last = 0, 0, 0, 0, 0, None

    def read(self):
        t = self.clock.now()
        if self.last is not None:
            dt = t - self.last
            rate = self.mbps if self.busy_until is None or t <= self.busy_until else 0.0
            b = rate * 1e6 / 8 * dt
            if self.side == "rx":
                self.rx += b
            else:
                self.tx += b
            self.total += dt * 100
            self.busy += dt * self.cpu
        self.last = t
        self.n += 1
        return {"t": t, "ifs": {"eth0": (int(self.rx), 0, int(self.tx), 0)}, "links": {"eth0": {"h": 14, "mtu": 1500}},
                "cpu": (int(self.busy), int(self.total)), "io": 0, "ncpu": 1, "unit": None, "dev": None,
                "family": None, "in_unit": False}


SINCE = utc(2026, 10, 18, 12, 0)                    # Sun 15:00 MSK: the entries and the arming


def ent(t, days, eid, mode="quick", **kw):
    e = {"id": eid, "time": t, "mode": mode, "days": list(days), "on": True, "quiet": True, "monthly": False,
         "since": SINCE}
    e.update(kw)
    return e


class Case(RootCase):
    def setUp(self):
        super().setUp()
        self.write("/etc/machine-id", MID_RAW + "\n", 0o444)
        self.save([ent("03:45", range(1, 8), "aaaaaa")])
        self.runner = sysrun.FakeRunner()
        self.runner.on(["systemctl", "list-units"], "")
        self.runner.on(["systemctl"], "")
        self.started = []
        p = mock.patch.object(run, "start", side_effect=self._start)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(sched_unit, "_zone", return_value=(MOSCOW, None))
        p.start()
        self.addCleanup(p.stop)

    def _start(self, mode, by="cli", sched_id=None, retry_of=None, runner=None, **kw):
        self.started.append((mode, by, sched_id, retry_of))
        return {"id": "%010x" % len(self.started)}

    def save(self, sched, armed=True, enabled=True, retry=True):
        st = state.empty()
        b = {"schedule": sched, "retry": retry}
        if armed:
            b.update(mid=HERE, first_manual_at=SINCE, pkgs="yes", armed_at=SINCE)
        st["gateway"] = {"hostmon": {"enabled": enabled, "bench": b}}
        os.makedirs(paths.etc(), 0o700, exist_ok=True)
        state.save(st)

    def fire(self, t, **kw):
        clock = kw.pop("clock", None) or FakeClock(t)
        meter = kw.pop("meter", None) or FakeMeter(clock)
        self.clock, self.meter = clock, meter
        return sched_run.scheduled(self.runner, clock, meter, here=HERE)

    def queue(self):
        out = []
        for path, _id in spool.scan()[0]:
            r, _t = spool.load_run(path)
            r = dict(r, why_args=json.loads(r["why_args"]) if isinstance(r.get("why_args"), str) else r.get("why_args"))
            out.append(r)
        return sorted(out, key=lambda r: r["ts"])


class DueTest(Case):
    def test_not_armed_does_nothing(self):
        self.save([ent("03:45", range(1, 8), "aaaaaa")], armed=False)
        out = self.fire(utc(2026, 10, 19, 0, 31))
        self.assertEqual((out["done"], self.started, self.queue()), (None, [], []))

    def test_the_event_runs_by_the_schedule_and_the_mark_is_0600(self):
        out = self.fire(utc(2026, 10, 19, 0, 31))                      # Mon 03:31 MSK, the line 03:30
        self.assertEqual(out["done"], "2026-10-19 03:45")
        self.assertEqual(self.started, [("quick", "schedule", "aaaaaa", None)])
        self.assertGreaterEqual(self.clock.now() - utc(2026, 10, 19, 0, 31), 300, "one reading decides nothing")
        p = sched_run.state_path()
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(json.load(open(p))["done"], {"aaaaaa": "2026-10-19 03:45"})
        self.fire(utc(2026, 10, 19, 0, 50))                            # a second firing of the same event
        self.assertEqual(len(self.started), 1)

    def test_midnight_keys(self):
        self.save([ent("00:05", [1], "aaaaaa"), ent("23:50", [1, 2], "bbbbbb")])
        self.assertEqual(self.fire(utc(2026, 10, 18, 20, 55))["done"], "2026-10-19 00:05")   # Sun 23:55
        self.assertEqual(self.fire(utc(2026, 10, 19, 21, 3))["done"], "2026-10-19 23:50")    # Tue 00:03
        self.assertEqual(self.fire(utc(2026, 10, 20, 20, 41))["done"], "2026-10-20 23:50")   # Tue 23:41

    def test_nothing_due_is_a_line_and_exit_0(self):
        out = self.fire(utc(2026, 10, 19, 9, 0))
        self.assertEqual((out["done"], self.started), (None, []))


class SkipTest(Case):
    def test_disabled(self):
        self.save([ent("03:45", range(1, 8), "aaaaaa")], enabled=False)
        self.assertEqual(self.fire(utc(2026, 10, 19, 0, 31))["skip"], "disabled")
        q = self.queue()
        self.assertEqual([(r["status"], r["why"], r["by"], r["sched_id"]) for r in q],
                         [("skipped", "disabled", "schedule", "aaaaaa")])
        self.assertEqual(q[0]["ts"], utc(2026, 10, 19, 0, 45), "the nominal moment of the event")

    def test_running(self):
        self.runner = sysrun.FakeRunner()
        self.runner.on(["systemctl", "list-units"], "kaskad-bench-0123456789.service loaded active running x\n")
        self.assertEqual(self.fire(utc(2026, 10, 19, 0, 31))["skip"], "running")

    def test_busy_60_min(self):
        """§2.5 п. 4, REV З-8: 12 decisions — at the 5th, 10th … 60th minute — over 60 min of waiting, then ``busy``."""
        from kaskad.bench import quiet
        clock = FakeClock(utc(2026, 10, 19, 0, 31))
        t0 = clock.now()
        at = []
        real = quiet.decide

        def decide(a, b):
            at.append(int(round(clock.now() - t0)) // 60)
            return real(a, b)
        with mock.patch.object(quiet, "decide", side_effect=decide):
            out = self.fire(None, clock=clock, meter=FakeMeter(clock, mbps=5.0))
        self.assertEqual(out["skip"], "busy")
        self.assertEqual(self.started, [])
        self.assertEqual(at, list(range(5, 61, 5)))
        self.assertEqual(clock.slept, 3600)
        r = self.queue()[0]
        self.assertEqual((r["why"], r["why_args"]["thr_mbps"], r["why_args"]["mbps"]), ("busy", 2.0, 5.0))
        self.assertEqual(sched_run.load_done(), {"aaaaaa": "2026-10-19 03:45"})

    def test_thresholds(self):
        for mbps, cpu, side, free in ((1.9, 19, "rx", True), (2.1, 0, "rx", False), (0, 21, "rx", False),
                                      (2.1, 0, "tx", False)):
            self.started, clock = [], FakeClock(utc(2026, 10, 19, 0, 31))
            meter = FakeMeter(clock, mbps=mbps, cpu=cpu, side=side)
            with mock.patch.object(sched_run, "WAIT_S", 300):
                ok, d = sched_run.wait_quiet(meter, clock, clock.now() + 300)
            self.assertEqual(ok, free, (mbps, cpu, side, d))

    def test_quiet_after_people_left(self):
        clock = FakeClock(utc(2026, 10, 19, 0, 31))
        meter = FakeMeter(clock, mbps=5.0, busy_until=clock.now() + 20 * 60)
        self.assertIsNone(self.fire(None, clock=clock, meter=meter)["skip"])
        self.assertGreaterEqual(clock.now() - utc(2026, 10, 19, 0, 31), 25 * 60)

    def test_quiet_off_starts_at_once(self):
        self.save([ent("03:45", range(1, 8), "aaaaaa", quiet=False)])
        clock = FakeClock(utc(2026, 10, 19, 0, 31))
        self.assertIsNone(self.fire(None, clock=clock, meter=FakeMeter(clock, mbps=50))["skip"])
        self.assertEqual(clock.slept, 0)

    def test_locked(self):
        util_dir = os.path.dirname(run.lock_path())
        os.makedirs(util_dir, exist_ok=True)
        fd = os.open(run.lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)                  # held by «the test of the exits»
        out = self.fire(utc(2026, 10, 19, 0, 31))
        self.assertEqual(out["skip"], "locked")
        self.assertEqual(self.started, [])
        self.assertEqual(self.queue()[0]["why"], "locked")
        self.assertEqual(self.clock.slept, 300 + 600, "the first decision at 5 min, then the lock 10 min — not 60")


class MissedTest(Case):
    def test_server_off_two_days(self):
        sched_run.save_done({"aaaaaa": "2026-10-19 03:45"})
        out = self.fire(utc(2026, 10, 22, 0, 31))                      # Thu: Tue and Wed missed, Thu runs
        self.assertEqual(out["missed"], 2)
        q = self.queue()
        self.assertEqual([(r["why"], r["ts"]) for r in q if r["why"] == "missed"],
                         [("missed", utc(2026, 10, 20, 0, 45)), ("missed", utc(2026, 10, 21, 0, 45))])
        self.assertEqual(len(self.started), 1)
        self.assertEqual(sched_run.load_done(), {"aaaaaa": "2026-10-22 03:45"})

    def test_weekly_missed_seen_by_the_daily_firing(self):
        self.save([ent("03:45", [1], "aaaaaa", since=1), ent("09:00", [2], "bbbbbb", since=1)])
        sched_run.save_done({"aaaaaa": "2026-10-12 03:45", "bbbbbb": "2026-10-13 09:00"})
        self.fire(utc(2026, 10, 20, 5, 46))                            # Tue 08:46: Mon 03:45 was missed
        self.assertIn(("missed", "aaaaaa"), [(r["why"], r["sched_id"]) for r in self.queue()])

    def test_more_than_7_one_row_with_n(self):
        self.save([ent("03:45", range(1, 8), "aaaaaa", since=1)])
        st = state.load()
        st["gateway"]["hostmon"]["bench"]["armed_at"] = 1
        state.save(st)
        sched_run.save_done({"aaaaaa": "2026-09-01 03:45"})
        self.fire(utc(2026, 10, 19, 9, 0))
        q = [r for r in self.queue() if r["why"] == "missed"]
        self.assertEqual(len(q), 8)
        self.assertEqual(q[0]["why_args"]["n"], 48 - 7)              # Sep 2 … Oct 19: 48 events, 7 rows + 1


class RetryTest(Case):
    def test_unit_name_only_from_an_id(self):
        for bad in ("a;rm", "../x", "000000000", None, "0123456789\n"):
            with self.assertRaises(KaskadError):
                sched_unit.retry_unit(bad)
        r = sysrun.FakeRunner()
        r.on(["systemd-run"], "")
        s = sched_unit.schedule_retry(r, "0123456789", links=["cpu"])
        self.assertTrue(3600 <= s <= 7200)
        argv = r.calls[0][0]
        self.assertIn("--unit=kaskad-bench-retry-0123456789", argv)
        self.assertEqual(argv[-5:], ["bench", "--retry", "0123456789", "--links", "cpu"])

    def limits(self, runs, run_doc, now, **m):
        mm = {"retry": True, "armed": True, "schedule": [ent("03:45", [3], "aaaaaa")]}
        mm.update(m)
        return sched_run.retry_blocked(mm, runs, run_doc, now, MOSCOW)

    def test_limits(self):
        now = utc(2026, 10, 19, 9, 0)                                  # Mon 12:00 MSK
        doc = {"id": "0000000001", "by": "panel", "ts": now}
        self.assertIsNone(self.limits([], doc, now))
        self.assertEqual(self.limits([], doc, now, retry=False), "off")
        self.assertEqual(self.limits([], doc, now, armed=False), "not_armed")
        self.assertEqual(self.limits([], dict(doc, by="auto"), now), "auto")
        self.assertEqual(self.limits([{"retry_of": "0000000001", "by": "auto", "ts": now}], doc, now), "has_one")
        two = [{"retry_of": "000000000%d" % i, "by": "auto", "ts": now - 3600 * i} for i in (2, 3)]
        self.assertEqual(self.limits(two, doc, now), "day")
        self.assertIsNone(self.limits(two[:1], doc, now))
        sched = [ent("14:30", [1], "bbbbbb")]                          # 90 min after the repeat (~13:30)
        self.assertEqual(self.limits([], doc, now, schedule=sched), "schedule")

    def test_busy_60_min_is_a_row_no_second_repeat(self):
        clock = FakeClock(utc(2026, 10, 19, 9, 0))
        with mock.patch.object(sched_run, "_source", return_value={"mode": "full"}):
            out = sched_run.retry("0123456789", self.runner, clock, FakeMeter(clock, mbps=9), links=["up"])
        self.assertEqual(out, {"skip": "busy"})
        r = self.queue()[0]
        self.assertEqual((r["by"], r["retry_of"], r["mode"], r["why"]), ("auto", "0123456789", "full", "busy"))

    def test_the_repeat_starts_as_auto(self):
        clock = FakeClock(utc(2026, 10, 19, 9, 0))
        with mock.patch.object(sched_run, "_source", return_value={"mode": "quick"}):
            sched_run.retry("0123456789", self.runner, clock, FakeMeter(clock), links=["up"])
        self.assertEqual(self.started, [("quick", "auto", None, "0123456789")])


SAMPLE = {"runs": [{"id": "0123456789", "ts": utc(2026, 10, 19, 8, 55)}], "epochs": []}


class RetryAfterTest(Case):
    """§2.8 п. 1, 3, 4: the hook at the end of a check — a good check whose links fell sets the transient timer; a
    repeat sets none; not armed — the line instead of the timer; the score failing never fails the check."""

    def doc(self, by="panel", status="ok"):
        return {"run": {"id": "0123456789", "by": by, "status": status, "end_ts": utc(2026, 10, 19, 9, 0)}}

    def test_fell_sets_the_timer(self):
        from kaskad import score
        r = sysrun.FakeRunner()
        r.on(["systemd-run"], "")
        with mock.patch("kaskad.bench.history.sample", return_value=SAMPLE), \
                mock.patch.object(score, "drop_events", return_value=([], {})), \
                mock.patch.object(score, "retry_wanted", return_value=["up"]), \
                mock.patch("kaskad.bench.machine.mid", return_value=HERE):
            res = run.retry_after(self.doc(), r)
        self.assertIn("set", res)
        self.assertEqual(len(r.commands(["systemd-run"])), 1)
        with mock.patch("kaskad.bench.history.sample", return_value=SAMPLE), \
                mock.patch.object(score, "drop_events", return_value=([], {})), \
                mock.patch.object(score, "retry_wanted", return_value=["up"]):
            self.assertIsNone(run.retry_after(self.doc(by="auto"), r))
            self.assertIsNone(run.retry_after(self.doc(status="skipped"), r))
        self.assertEqual(len(r.commands(["systemd-run"])), 1)

    def test_not_armed_is_a_line(self):
        from kaskad import score
        self.save([ent("03:45", [3], "aaaaaa")], armed=False)
        r = sysrun.FakeRunner()
        with mock.patch("kaskad.bench.history.sample", return_value=SAMPLE), \
                mock.patch.object(score, "drop_events", return_value=([], {})), \
                mock.patch.object(score, "retry_wanted", return_value=["down"]), \
                mock.patch("kaskad.bench.machine.mid", return_value=HERE):
            self.assertEqual(run.retry_after(self.doc(), r), {"blocked": "not_armed"})
        self.assertEqual(r.commands(["systemd-run"]), [])

    def test_a_failing_score_never_fails_the_check(self):
        from kaskad import score
        with mock.patch("kaskad.bench.history.sample", side_effect=RuntimeError("x")):
            self.assertIsNone(run.retry_after(self.doc(), sysrun.FakeRunner()))
        with mock.patch.object(score, "retry_wanted", return_value=[]):
            self.assertIsNone(run.retry_after(self.doc(), sysrun.FakeRunner()))


class GateUnitAliveTest(Case):
    """REV-ns-b11-2 П-1: the lock of the speed tests is free, but the unit of a check is still alive (it counts its
    drops after letting the lock go, or a manual one started during the wait for quiet) — the gate waits for it within
    the 10 min of the lock; it never ends as an «error» of ``run.start``."""

    def active(self, alive):
        calls = {"n": 0}

        def active(runner):
            calls["n"] += 1
            return [] if calls["n"] == 1 or calls["n"] > alive else ["0000000009"]
        return mock.patch.object(run, "active", side_effect=active)

    def test_the_gate_waits_for_the_unit(self):
        with self.active(alive=3):                     # the first look free, then alive at two tries of the lock
            out = self.fire(utc(2026, 10, 19, 0, 31))
        self.assertEqual((out["skip"], self.started), (None, [("quick", "schedule", "aaaaaa", None)]))

    def test_alive_for_the_whole_wait_is_running(self):
        with self.active(alive=10 ** 6):
            out = self.fire(utc(2026, 10, 19, 0, 31))
        self.assertEqual((out["skip"], self.started), ("running", []))
        self.assertEqual([(r["status"], r["why"]) for r in self.queue()], [("skipped", "running")])

    def test_a_start_refused_as_running_is_running(self):
        def start(mode, by="cli", runner=None, **kw):
            raise KaskadError("running", data={"kind": "running", "id": "0000000009"})
        with mock.patch.object(run, "start", side_effect=start):
            out = self.fire(utc(2026, 10, 19, 0, 31))
        self.assertEqual(out["skip"], "running")
        self.assertEqual([(r["why"], r["why_args"]) for r in self.queue()], [("running", None)])

    def test_another_failure_is_still_an_error(self):
        with mock.patch.object(run, "start", side_effect=KaskadError("no systemd-run")):
            out = self.fire(utc(2026, 10, 19, 0, 31))
        self.assertEqual(out["skip"], "error")
        self.assertEqual([r["why_args"] for r in self.queue()], [{"err": "KaskadError"}])


class ScheduleSkipTest(Case):
    def test_the_schedule_asks_for_no_8b_and_no_9(self):
        """NS-S5 (the join of B11 and B3): the start of the schedule says ``skip`` 8b and 9 itself (the runner adds
        them for a check not by a person too)."""
        got = []

        def start(mode, by="cli", **kw):
            got.append((by, tuple(kw.get("skip") or ()), kw.get("only")))
            return {"id": "0000000001"}
        with mock.patch.object(run, "start", side_effect=start):
            self.fire(utc(2026, 10, 19, 0, 31))
        self.assertEqual(got, [("schedule", ("8b", "9"), None)])
