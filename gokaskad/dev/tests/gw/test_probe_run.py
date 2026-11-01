"""The scheduler of the unit of probes (docs/k3/SPEC-stage1-exits §4.3 «Страна», §5.3, §5.5–§5.7, §11.1
«probe/run.py»; stage 1, T6): the speed test by ``checks.speed`` of the group and ``exit_pref.speed_skip``, ± 20 %, «only
when quiet» and half the volume after 2 intervals, budgets of a day and a month, ``flock`` ``LOCK_NB``, the atomic
``probe-results.json``, ``geo.enabled = false``, the extra pass over every exit and the direct check, an exit off by its
quota."""

import copy
import json
import os
from unittest import mock

from kaskad import paths
from kaskad.gw.probe import fetch, geo, run, services, speed
from tests.helpers import RootCase, lock_held_elsewhere

A, B = "e-aaaaaaaaaa", "e-bbbbbbbbbb"
NOW = 1760000000.0
H = 3600


def group(gid, exits, **checks):
    c = {"speed": {"enabled": False, "every_h": 6, "url": "default"}, "services": [],
         "geo": {"enabled": True, "every_h": 24, "confirm": 2, "action": "push", "expect": None}}
    for k, v in checks.items():
        c[k] = dict(c[k], **v) if isinstance(c.get(k), dict) else v
    return {"id": gid, "role": "abroad", "tiers": [{"id": "t1", "exits": list(exits)}], "checks": c}


class RunCase(RootCase):
    def setUp(self):
        super().setUp()
        self.now = NOW
        self.mono = 5000.0
        self.factor = 1.0                                       # what _rand gives inside the ± 20 %
        self.rand_args = []
        self.st = {"gateway": {"enabled": True, "exits": {},
                               "exit_policy": {"version": 1, "probes": {"speed": {"max_mb": 10, "max_s": 8}},
                                               "groups": [group("main", [A]), group("g-111111", [B])]}}}
        self.pool = [{"id": A, "index": 1, "kind": "awg", "mark": 257, "role": "abroad"},
                     {"id": B, "index": 2, "kind": "xray", "role": "abroad"}]
        self.snap = {"at": 0, "pool": [], "exits": {}}
        self.locs = {}                                          # exit id (or "direct") -> loc
        self.calls = []                                         # (what, exit id, extra)

        def rand(a, b):
            self.rand_args.append((a, b))
            return self.factor
        self.patches = [
            mock.patch.object(run, "_now", lambda: self.now),
            mock.patch.object(run, "_mono", lambda: self.mono),
            mock.patch.object(run, "_rand", rand),
            mock.patch.object(run.state, "load", lambda *a, **k: copy.deepcopy(self.st)),
            mock.patch.object(run.exits, "pool", lambda *a, **k: copy.deepcopy(self.pool)),
            mock.patch.object(run.exits, "load_snapshot", lambda: copy.deepcopy(self.snap)),
            mock.patch.object(fetch, "guard_for", lambda *a, **k: fetch.Guard()),
            mock.patch.object(geo, "trace", self.fake_trace),
            mock.patch.object(speed, "run", self.fake_speed),
            mock.patch.object(services, "check", self.fake_svc),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def eid(self, via):
        if via.kind == "direct":
            return "direct"
        return A if (via.kind == "awg" and via.mark == 257) else B

    def fake_trace(self, via, guard=None, deadline=None):
        e = self.eid(via)
        self.calls.append(("geo", e, None))
        loc = self.locs.get(e, "DE")
        return {"ip": "203.0.113.%d" % len(self.calls), "loc": loc, "ok": True}

    def fake_speed(self, via, url, max_mb, max_s, guard=None, deadline=None):
        self.calls.append(("speed", self.eid(via), (url, max_mb, max_s)))
        return {"mbps": 50.0, "ttfb_ms": 30.0, "tls_ms": 20.0, "bytes": int(max_mb * 1000 * 1000),
                "host": fetch.shown(speed.target(url, max_mb * 1e6)), "ok": True, "why": None}

    def fake_svc(self, via, svc, guard=None, deadline=None):
        self.calls.append(("svc", self.eid(via), svc["id"]))
        return {"svc": svc["id"], "ok": True, "code": 204, "ms": 120.0, "host": fetch.shown(svc["url"])}

    def what(self, kind, eid=None):
        return [c for c in self.calls if c[0] == kind and (eid is None or c[1] == eid)]

    def tick(self, dt=0):
        self.now += dt
        self.mono += dt
        self.calls = []
        return run.tick(None)

    def results(self):
        return run.read_results()["results"]

    def checks(self, gid, **kw):
        for g in self.st["gateway"]["exit_policy"]["groups"]:
            if g["id"] == gid:
                for k, v in kw.items():
                    g["checks"][k] = dict(g["checks"][k], **v) if isinstance(g["checks"].get(k), dict) else v

    def pref(self, eid, **kw):
        self.st["gateway"]["exits"].setdefault(eid, {}).update(kw)

    def traffic(self, eid, total, mtime):
        p = paths.traffic_json()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump({"version": 1, "exits": {eid: {"day": "2025-10-09", "day_bytes": 0, "period_start": None,
                                                     "period_bytes": 0, "last": {"rx": total, "tx": 0},
                                                     "warned": {}}}}, f)
        os.utime(p, (mtime, mtime))


class SpeedTest(RunCase):
    def test_only_the_group_with_the_test_its_every_and_url(self):
        self.checks("main", speed={"enabled": True, "every_h": 3, "url": "https://my.example.com/f.bin"},
                    geo={"enabled": False})
        self.checks("g-111111", geo={"enabled": False})
        self.tick()
        self.assertEqual(self.what("speed"), [("speed", A, ("https://my.example.com/f.bin", 10.0, 8.0))])
        r = [x for x in self.results() if x["kind"] == "speed"]
        self.assertEqual((r[0]["exit"], r[0]["ok"], r[0]["mbps"], r[0]["host"]),
                         (A, True, 50.0, "my.example.com/f.bin"))
        self.tick(61)
        self.assertEqual(self.what("speed"), [])
        self.tick(3 * H - 61)                                   # due again after every_h (the factor 1.0)
        self.assertEqual(len(self.what("speed", A)), 1)

    def test_every_h_plus_minus_20_percent(self):
        self.checks("main", speed={"enabled": True, "every_h": 6}, geo={"enabled": False})
        self.checks("g-111111", geo={"enabled": False})
        self.factor = 1.2
        self.tick()
        st = run.read_state()["exits"][A]
        self.assertEqual(st["speed_due"], NOW + 6 * H * 1.2)
        self.assertIn((0.8, 1.2), self.rand_args)
        self.assertAlmostEqual(run.jitter(100), 120.0)

    def test_speed_skip_of_the_exit(self):
        self.checks("main", speed={"enabled": True})
        self.checks("g-111111", speed={"enabled": True})
        self.pref(A, speed_skip=True)
        self.tick()
        self.assertEqual([c[1] for c in self.what("speed")], [B])

    def test_speed_test_touches_its_lock_after(self):
        """SPEC-node-score §3.3 «Исключения»: the end of a test is the lock's mtime (the light probes read it)."""
        self.checks("main", speed={"enabled": True}, geo={"enabled": False})
        self.checks("g-111111", geo={"enabled": False})
        lp = run.speed_lock_path()
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        with open(lp, "w"):
            pass
        os.utime(lp, (1000, 1000))
        self.tick()
        self.assertEqual(len(self.what("speed")), 1)
        self.assertGreater(os.stat(lp).st_mtime, 1000)
        os.utime(lp, (1000, 1000))
        with lock_held_elsewhere(lp):
            self.tick(61)                                       # busy: no test — no touch
        self.assertEqual(os.stat(lp).st_mtime, 1000)

    def test_speed_lock_touched_while_held_not_on_budget_refusal(self):
        """REV-ns-a3 П-3, П-4: the touch comes before the unlock (no gap for the light probes); a test refused by the
        budget is no test — the lock's mtime stays."""
        import fcntl
        self.checks("main", speed={"enabled": True, "every_h": 1}, geo={"enabled": False})
        self.checks("g-111111", geo={"enabled": False})
        self.st["gateway"]["exit_policy"]["probes"]["speed"].update(budget_day_mb=15, budget_month_gb=1)
        lp = run.speed_lock_path()
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        with open(lp, "w"):
            pass
        held = []
        orig = run._touch

        def touch(path):
            fd = os.open(path, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                held.append(True)
            finally:
                os.close(fd)
            orig(path)
        with mock.patch.object(run, "_touch", touch):
            os.utime(lp, (1000, 1000))
            self.tick()
            self.assertEqual(len(self.what("speed")), 1)
            self.assertEqual(held, [True])                      # touched while the lock is still held
            os.utime(lp, (1000, 1000))
            self.tick(H + 1)                                    # 10 more MB do not fit the 15 MB of the day
            self.assertEqual(self.what("speed"), [])
            self.assertEqual(held, [True])
        self.assertEqual(os.stat(lp).st_mtime, 1000)

    def test_one_test_on_the_node_exits_60_s_apart(self):
        self.checks("main", speed={"enabled": True})
        self.checks("g-111111", speed={"enabled": True})
        self.tick()
        self.assertEqual(len(self.what("speed")), 1)
        self.tick(30)
        self.assertEqual(self.what("speed"), [])
        self.tick(31)
        self.assertEqual(len(self.what("speed")), 1)
        self.tick(61)
        self.assertEqual(self.what("speed"), [])               # both done

    def test_only_when_quiet_and_half_after_two_intervals(self):
        self.checks("main", speed={"enabled": True, "every_h": 1}, geo={"enabled": False})
        self.checks("g-111111", geo={"enabled": False})
        self.traffic(A, 0, NOW - 60)
        self.tick()                                            # no judgement yet (one reading): the test goes
        self.assertEqual(len(self.what("speed", A)), 1)
        busy = 60 * 1000 * 1000                                # 60 MB a minute: 8 Mbit/s — not quiet
        for i in range(1, 6):
            self.traffic(A, busy * i, NOW + 60 * i)
            self.tick(60 if i > 1 else H)
            self.assertEqual(self.what("speed"), [], i)
        self.now = NOW + 2 * H - 5                              # due at 1 h, busy since: not yet 2 intervals
        total = busy * 5 + busy // 60 * int(self.now - NOW - 300)
        self.traffic(A, total, self.now)
        self.tick()
        self.assertEqual(self.what("speed"), [])
        self.now = NOW + 3 * H + 5                              # no quiet till 2 intervals past due: half the volume
        total += busy // 60 * (3 * H + 5 - (2 * H - 5))
        self.traffic(A, total, self.now)
        self.tick()
        self.assertEqual([c[2][1] for c in self.what("speed", A)], [5.0])
        self.assertTrue([x for x in self.results() if x["kind"] == "speed"][-1]["half"])
        # quiet: < 1 Mbit/s for the last minute — the full volume
        self.traffic(A, total + 1000, self.now + H + 60)
        self.tick(H + 60)
        self.assertEqual([c[2][1] for c in self.what("speed", A)], [10.0])

    def test_budget_of_a_day_and_a_month(self):
        self.checks("main", speed={"enabled": True, "every_h": 1}, geo={"enabled": False})
        self.checks("g-111111", geo={"enabled": False})
        self.st["gateway"]["exit_policy"]["probes"]["speed"].update(budget_day_mb=15, budget_month_gb=1)
        self.tick()
        self.assertEqual(len(self.what("speed")), 1)           # 10 MB spent
        self.tick(H + 1)
        self.assertEqual(self.what("speed"), [])               # 10 more do not fit the 15 MB of the day
        ev = [x for x in self.results() if x.get("event") == "speedtest_budget"]
        self.assertEqual([(x["exit"], x["period"], x["why"]) for x in ev], [(A, "day", "budget_day")])
        self.tick(H + 1)
        self.assertEqual(len([x for x in self.results() if x.get("event")]), 1)    # once a period
        self.assertEqual(run.read_state()["exits"][A]["budget"]["day_b"], 10 * 1000 * 1000)
        # the month: 1 GB minus what was spent
        self.st["gateway"]["exit_policy"]["probes"]["speed"].update(budget_day_mb=10 ** 5, budget_month_gb=0.015)
        self.tick(H + 1)
        self.assertEqual(self.what("speed"), [])
        ev = [x for x in self.results() if x.get("event") == "speedtest_budget"]
        self.assertEqual([x["period"] for x in ev], ["day", "month"])

    def test_lock_busy_skips(self):
        self.checks("main", speed={"enabled": True}, geo={"enabled": False})
        self.checks("g-111111", geo={"enabled": False})
        with lock_held_elsewhere(run.speed_lock_path()):
            self.tick()
        self.assertEqual(self.what("speed"), [])
        self.assertEqual([x for x in self.results() if x["kind"] == "speed"], [])
        self.tick(61)                                           # still due: the next run takes it
        self.assertEqual(len(self.what("speed")), 1)

    def test_manual_test_budget_force_busy(self):
        self.checks("g-111111", geo={"enabled": False})
        self.checks("main", geo={"enabled": False})            # the group's test off: manual works anyway
        self.st["gateway"]["exit_policy"]["probes"]["speed"].update(budget_day_mb=15, budget_month_gb=0.025)
        r = run.manual_speed(None, A, now=self.now)
        self.assertEqual((r["exit"], r["ok"], r["manual"]), (A, True, True))
        r = run.manual_speed(None, A, now=self.now)
        self.assertEqual((r["ok"], r["why"]), (False, "budget_day"))
        r = run.manual_speed(None, A, force=True, now=self.now)
        self.assertTrue(r["ok"])                                # past the day's budget with the confirmation
        r = run.manual_speed(None, A, force=True, now=self.now)
        self.assertEqual(r["why"], "budget_month")             # the month is never passed
        self.st["gateway"]["exit_policy"]["probes"]["speed"].update(budget_day_mb=1000, budget_month_gb=10)
        with lock_held_elsewhere(run.speed_lock_path()):
            r = run.manual_speed(None, B, now=self.now)
        self.assertEqual(r["why"], "busy")
        self.assertEqual(run.manual_speed(None, "e-cccccccccc", now=self.now)["why"], "no_exit")


class QuotaTest(RunCase):
    def test_off_by_quota_no_speed_services_country(self):
        self.checks("main", speed={"enabled": True},
                    services=[{"id": "yt", "name": "YouTube", "url": "https://www.youtube.com/generate_204",
                               "expect": [204], "max_ms": 3000, "every_min": 30}])
        self.snap["exits"][A] = {"state": "off", "reasons": ["quota"]}
        self.locs[B] = "NL"                                     # B changes country: the pass must skip A
        self.pref(B, country={"expected": "FI"})
        self.tick()
        self.assertEqual([c for c in self.calls if c[1] == A], [])
        self.assertEqual(len(self.what("geo", "direct")), 1)   # the pass went (for B) without A
        # back (the owner's «Вернуть» — the snapshot says ok): probed again
        self.snap["exits"][A] = {"state": "ok", "reasons": []}
        self.tick(61)
        self.assertTrue([c for c in self.calls if c[1] == A])

    def test_without_states_the_counters_against_the_limit(self):
        t = {"version": 1, "exits": {A: {"day": "2025-10-09", "day_bytes": 0, "period_start": "2025-10-01",
                                         "period_bytes": 2 * 10 ** 9, "last": {"rx": 1, "tx": 1}, "warned": {}}}}
        q = {"period": "month", "limit_gb": 1, "reset_day": 1, "warn_pct": 80, "action": "remove"}
        self.assertTrue(run.quota_removed({"alive": True}, {"quota": q}, t, A, NOW))
        self.assertFalse(run.quota_removed({"alive": True}, {"quota": dict(q, action="standby")}, t, A, NOW))
        self.assertFalse(run.quota_removed({"alive": True}, {"quota": dict(q, limit_gb=5)}, t, A, NOW))
        self.assertFalse(run.quota_removed({"state": "ok", "reasons": []}, {"quota": q}, t, A, NOW))   # «Вернуть»


class GeoTest(RunCase):
    def test_geo_disabled_no_request_at_all(self):
        self.checks("main", geo={"enabled": False})
        self.checks("g-111111", geo={"enabled": False})
        self.snap["exits"][A] = {"alive": False}
        self.tick()
        self.snap["exits"][A] = {"alive": True}                 # back from «dead»
        self.tick(61)
        run.request("geo_pass", A, now=self.now)
        self.tick(H * 30)
        self.assertEqual(self.what("geo"), [])
        self.assertEqual([x for x in self.results() if x["kind"].startswith("geo")], [])

    def test_planned_one_per_run_and_after_return_from_dead(self):
        self.tick()
        self.assertEqual(len(self.what("geo")), 1)              # one planned check per run
        self.tick(61)
        self.assertEqual(len(self.what("geo")), 1)              # the other exit, ≥ 60 s later
        self.tick(61)
        self.assertEqual(self.what("geo"), [])
        self.snap["exits"][A] = {"state": "off", "reasons": ["dead"]}
        self.tick(61)
        self.snap["exits"][A] = {"state": "ok", "reasons": []}
        self.tick(5)
        self.assertEqual([c[1] for c in self.what("geo")], [A])
        self.assertEqual([x["how"] for x in self.results() if x["kind"] == "geo"][-1], "plan")

    def test_first_change_extra_pass_direct_check_and_confirmation(self):
        self.checks("main", geo={"expect": "DE"})
        self.pref(B, country={"expected": "FI"})
        self.locs = {A: "NL", B: "FI", "direct": "RU"}
        self.tick()
        got = sorted(c[1] for c in self.what("geo"))
        self.assertEqual(got, sorted([A, A, B, "direct"]))      # planned A, then the pass: A, B and the node itself
        res = self.results()
        p = [x for x in res if x["kind"] == "geo_pass"][0]
        self.assertEqual((p["exit"], p["direct_loc"], p["locs"], p["suspect"]),
                         (A, "RU", {A: "NL", B: "FI"}, None))
        self.assertEqual(sorted(x["how"] for x in res if x["kind"] == "geo"), ["pass", "pass", "plan"])
        self.assertEqual(run.read_state()["exits"][A]["confirm_at"], NOW + 300)
        self.tick(120)
        self.assertEqual(self.what("geo"), [])
        self.tick(181)
        self.assertEqual([c[1] for c in self.what("geo")], [A])  # the confirmation 5 min later, no second pass
        self.assertEqual([x for x in self.results() if x["kind"] == "geo"][-1]["how"], "confirm")
        self.tick(H)
        self.assertEqual(self.what("geo", A), [])

    def test_signs_of_a_broken_measure(self):
        self.checks("main", geo={"expect": "DE"})
        self.pref(B, country={"expected": "FI"})
        self.locs = {A: "NL", B: "NL", "direct": "RU"}          # every exit changed
        self.tick()
        p = [x for x in self.results() if x["kind"] == "geo_pass"][0]
        self.assertEqual(p["suspect"], "all_exits")
        self.assertNotIn("confirm_at", run.read_state()["exits"][A])
        self.assertEqual(geo.suspect(A, "RU", {A: "DE", B: "FI"}, {A: "RU", B: "FI"}, "RU"), "node_loc")
        self.assertIsNone(geo.suspect(A, "RU", {A: "RU", B: "FI"}, {A: "RU", B: "FI"}, "RU"))
        self.assertIsNone(geo.suspect(A, "NL", {A: "DE", B: "FI"}, {A: "NL", B: "FI"}, "RU"))

    def test_pass_skips_groups_without_geo(self):
        self.checks("main", geo={"expect": "DE"})
        self.checks("g-111111", geo={"enabled": False})
        self.locs = {A: "NL"}
        self.tick()
        self.assertEqual(sorted(c[1] for c in self.what("geo")), sorted([A, A, "direct"]))

    def test_requests_of_the_collector(self):
        self.tick()
        self.tick(61)
        run.request("geo_confirm", B, after_s=300, now=self.now)
        self.tick(100)
        self.assertEqual(self.what("geo"), [])
        self.tick(201)
        self.assertEqual([c[1] for c in self.what("geo")], [B])
        self.assertEqual([x for x in self.results() if x["kind"] == "geo"][-1]["how"], "confirm")
        run.request("geo_pass", B, now=self.now)
        self.tick(1)
        self.assertEqual(sorted(c[1] for c in self.what("geo")), sorted([A, B, "direct"]))


class ServicesTest(RunCase):
    def test_every_min_of_each_service(self):
        self.checks("main", geo={"enabled": False},
                    services=[{"id": "yt", "name": "YouTube", "url": "https://www.youtube.com/generate_204",
                               "expect": [204], "max_ms": 3000, "every_min": 5}])
        self.checks("g-111111", geo={"enabled": False})
        self.tick()
        self.assertEqual(self.what("svc"), [("svc", A, "yt")])
        r = [x for x in self.results() if x["kind"] == "svc"][0]
        self.assertEqual((r["exit"], r["svc"], r["ok"], r["code"], r["host"]),
                         (A, "yt", True, 204, "www.youtube.com/generate_204"))
        self.tick(200)
        self.assertEqual(self.what("svc"), [])
        self.tick(101)
        self.assertEqual(len(self.what("svc")), 1)


class FilesTest(RunCase):
    def test_results_atomic_seq_and_trim(self):
        self.tick()
        self.tick(61)
        doc = run.read_results()
        seqs = [x["seq"] for x in doc["results"]]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        self.assertEqual(doc["seq"], seqs[-1])
        d = os.path.dirname(paths.probe_results())
        self.assertEqual([f for f in os.listdir(d) if f.startswith(".tmp")], [])
        self.assertEqual(oct(os.stat(paths.probe_results()).st_mode & 0o777), "0o600")
        with mock.patch.object(run, "RESULTS_KEEP", 2):
            run.commit([{"exit": A, "kind": "svc", "ts": 1}, {"exit": A, "kind": "svc", "ts": 2},
                        {"exit": A, "kind": "svc", "ts": 3}])
        doc2 = run.read_results()
        self.assertEqual([x["seq"] for x in doc2["results"]], [doc["seq"] + 2, doc["seq"] + 3])
        self.assertEqual(doc2["seq"], doc["seq"] + 3)

    def test_gateway_off_nothing(self):
        self.st["gateway"]["enabled"] = False
        self.assertIsNone(self.tick())
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(paths.probe_results()))


class Killed(BaseException):
    """What systemd's SIGTERM / SIGKILL looks like to the code of a run."""


class FailureTest(RunCase):
    """REV-s1-t6 С1: one failing probe is a row with ok: false; the run writes what it gathered; requests stay."""

    def test_a_probe_that_raises_is_a_row_and_the_run_goes_on(self):
        self.checks("main", geo={"enabled": False},
                    services=[{"id": "bad", "name": "B", "url": "https://bad.example.com/", "expect": [204],
                               "max_ms": 3000, "every_min": 30},
                              {"id": "good", "name": "G", "url": "https://good.example.com/", "expect": [204],
                               "max_ms": 3000, "every_min": 30}])
        self.checks("g-111111", geo={"enabled": False})
        fake = self.fake_svc

        def svc(via, s, guard=None, deadline=None):
            if s["id"] == "bad":
                raise ValueError("Invalid IPv6 URL")
            return fake(via, s, guard, deadline)
        with mock.patch.object(services, "check", svc):
            self.assertEqual(self.tick()["ok"], True)
        rows = dict((x["svc"], x) for x in self.results() if x["kind"] == "svc")
        self.assertEqual((rows["bad"]["ok"], rows["bad"]["why"]), (False, "error"))
        self.assertTrue(rows["good"]["ok"])

    def test_a_reading_of_the_pass_that_raises(self):
        self.checks("main", geo={"expect": "DE"})
        self.locs = {A: "NL", B: "FI", "direct": "RU"}
        fake = self.fake_trace

        def trace(via, guard=None, deadline=None):
            if via.kind == "direct":
                raise ConnectionResetError(104, "reset")
            return fake(via, guard, deadline)
        with mock.patch.object(geo, "trace", trace):
            self.tick()
        p = [x for x in self.results() if x["kind"] == "geo_pass"]
        self.assertEqual(len(p), 1)
        self.assertIsNone(p[0]["direct_loc"])

    def test_a_broken_stage_keeps_the_requests_and_the_rest(self):
        self.checks("main", speed={"enabled": True})
        run.request("geo_pass", A, now=self.now)
        with mock.patch.object(run, "geo_pass", side_effect=RuntimeError("bug")):
            out = self.tick()
        self.assertFalse(out["ok"])
        self.assertEqual(len(self.what("speed", A)), 1)        # the speed test of the same run is written
        self.assertTrue([x for x in self.results() if x["kind"] == "speed"])
        with open(run.requests_path()) as f:
            self.assertEqual([r["kind"] for r in json.load(f)["requests"]], ["geo_pass"])
        self.tick(61)
        self.assertEqual(len(self.what("geo", "direct")), 1)   # the next run does the pass
        with open(run.requests_path()) as f:
            self.assertEqual(json.load(f)["requests"], [])

    def test_a_killed_run_keeps_the_requests(self):
        run.request("geo_confirm", A, now=self.now)
        with mock.patch.object(run, "_geo_one", side_effect=Killed()):
            with self.assertRaises(Killed):
                self.tick()
        with open(run.requests_path()) as f:
            self.assertEqual([r["kind"] for r in json.load(f)["requests"]], ["geo_confirm"])


class KilledSpeedTest(RunCase):
    """REV-s1-t6 С2: a run killed after or during the speed test never repeats it nor loses its budget."""

    def setUp(self):
        super().setUp()
        self.checks("main", speed={"enabled": True, "every_h": 6}, geo={"enabled": False},
                    services=[{"id": "x", "name": "X", "url": "https://slow.example.com/", "expect": [204],
                               "max_ms": 3000, "every_min": 30}])
        self.checks("g-111111", geo={"enabled": False})

    def test_sigterm_after_the_test(self):
        n = 0
        for i in range(5):
            with mock.patch.object(services, "check", side_effect=Killed()):
                with self.assertRaises(Killed):
                    self.tick(61 if i else 0)
            n += len(self.what("speed", A))
        self.assertEqual(n, 1)
        st = run.read_state()["exits"][A]
        self.assertEqual(st["budget"]["day_b"], 10 * 1000 * 1000)
        self.assertEqual(st["speed_due"], NOW + 6 * H)

    def test_sigkill_during_the_test(self):
        # nothing after the start of the test is written (the process is gone): the budget and the next due are
        with mock.patch.object(speed, "run", side_effect=Killed()), \
                mock.patch.object(run.Run, "commit", lambda *a, **k: []):
            with self.assertRaises(Killed):
                self.tick()
        st = run.read_state()
        self.assertEqual(st["exits"][A]["budget"]["day_b"], 10 * 1000 * 1000)
        self.assertEqual(st["exits"][A]["speed_due"], NOW + 6 * H)
        self.assertEqual(st["last_speed"], NOW)
        self.tick(61)
        self.assertEqual(self.what("speed"), [])

    def test_the_budget_is_corrected_by_the_bytes_received(self):
        fake = self.fake_speed

        def sp(*a, **k):
            r = fake(*a, **k)
            r.update(bytes=3 * 1000 * 1000, rx=3 * 1000 * 1000 + 2000)    # stopped early; DoH and headers too
            return r
        with mock.patch.object(speed, "run", sp):
            self.tick()
        self.assertEqual(run.read_state()["exits"][A]["budget"]["day_b"], 3 * 1000 * 1000 + 2000)


class TimeTest(RunCase):
    """REV-s1-t6 С2, З4: every probe is held by the run's deadline; nothing starts that would not fit it."""

    def test_probes_get_the_runs_deadline(self):
        self.checks("main", speed={"enabled": True},
                    services=[{"id": "yt", "name": "YouTube", "url": "https://www.youtube.com/generate_204",
                               "expect": [204], "max_ms": 3000, "every_min": 30}])
        got = []
        fs, fv, ft = self.fake_speed, self.fake_svc, self.fake_trace
        with mock.patch.object(speed, "run", lambda *a, **k: got.append(("speed", k.get("deadline"))) or fs(*a, **k)), \
                mock.patch.object(services, "check",
                                  lambda *a, **k: got.append(("svc", k.get("deadline"))) or fv(*a, **k)), \
                mock.patch.object(geo, "trace", lambda *a, **k: got.append(("geo", k.get("deadline"))) or ft(*a, **k)):
            self.tick()
        self.assertEqual(sorted(k for k, _ in got), ["geo", "speed", "svc"])
        for k, d in got:
            self.assertEqual(d, self.mono + run.RUN_BUDGET_S, k)

    def test_nothing_starts_that_would_not_fit(self):
        self.checks("main", speed={"enabled": True},
                    services=[{"id": "yt", "name": "YouTube", "url": "https://www.youtube.com/generate_204",
                               "expect": [204], "max_ms": 3000, "every_min": 30}])
        with mock.patch.object(run, "RUN_BUDGET_S", 20):        # the speed test (23 s), a service (21 s), geo (25 s)
            self.tick()
        self.assertEqual(self.calls, [])
        self.tick(61)
        self.assertEqual(sorted(c[0] for c in self.calls), ["geo", "speed", "svc"])

    def test_the_pass_waits_for_its_threads(self):
        import threading
        import time as _t
        self.checks("main", geo={"expect": "DE"})
        self.locs = {A: "NL", B: "FI", "direct": "RU"}
        fake = self.fake_trace
        running = []
        ends = []

        def slow(via, guard=None, deadline=None):
            if self.what("geo"):                                  # the readings of the pass, not the planned one
                running.append(threading.current_thread())
                ends.append(deadline)
                _t.sleep(0.3)
            return fake(via, guard, deadline)
        with mock.patch.object(geo, "trace", slow), mock.patch.object(run, "PASS_TIMEOUT_S", 0.05):
            self.tick()
        self.assertEqual(len(running), 3)
        self.assertFalse([t for t in running if t.is_alive()])  # none outlives the pass
        self.assertTrue(all(d <= self.mono + 1.0 + 1e-6 for d in ends))     # the pass's own timeout (≥ 1 s)
        locs = [x for x in self.results() if x["kind"] == "geo_pass"][0]["locs"]
        self.assertEqual(locs, {A: "NL", B: "FI"})


class ReviewGeoTest(RunCase):
    """REV-s1-t6 З2, З5, П8."""

    def test_rows_have_bytes(self):
        fake = self.fake_trace

        def tr(*a, **k):
            return dict(fake(*a, **k), bytes=1234)
        with mock.patch.object(geo, "trace", tr):
            self.tick()
        self.assertEqual([x["bytes"] for x in self.results() if x["kind"] == "geo"], [1234])

    def test_all_exits_counts_an_exit_without_the_expected_country(self):
        # as health._geo of T2: an exit without an expected country counts as changed
        self.assertEqual(geo.suspect(A, "NL", {A: "DE", B: None}, {A: "NL", B: "FI"}, "RU"), "all_exits")
        self.checks("main", geo={"expect": "DE"})
        self.locs = {A: "NL", B: "FI", "direct": "RU"}         # B has no expected country
        self.tick()
        p = [x for x in self.results() if x["kind"] == "geo_pass"][0]
        self.assertEqual(p["suspect"], "all_exits")

    def test_a_pass_asked_right_after_its_own_is_answered(self):
        self.checks("main", geo={"expect": "DE"})
        self.pref(B, country={"expected": "FI"})
        self.locs = {A: "NL", B: "FI", "direct": "RU"}
        self.tick()                                            # its own first sight: the pass
        n = len([x for x in self.results() if x["kind"] == "geo_pass"])
        run.request("geo_pass", A, now=self.now)               # health asks for the pass of the same sight
        self.tick(61)
        self.assertEqual(self.what("geo"), [])                 # no new requests through the exits…
        res = self.results()
        again = [x for x in res if x["kind"] == "geo_pass"][n:]
        self.assertEqual([(x["exit"], x["locs"], x["suspect"], x["again"]) for x in again],
                         [(A, {A: "NL", B: "FI"}, None, True)])  # …but the answer health waits for
        g = [x for x in res if x["kind"] == "geo"][-1]
        self.assertEqual((g["exit"], g["how"], g["loc"]), (A, "pass", "NL"))

    def test_the_check_after_a_return_is_not_lost(self):
        self.tick()
        self.tick(61)
        self.snap["exits"][A] = {"state": "off", "reasons": ["dead"]}
        self.tick(61)
        self.snap["exits"][A] = {"state": "ok", "reasons": []}
        with mock.patch.object(run, "RUN_BUDGET_S", 10):        # no time for a reading in this run
            self.tick(61)
        self.assertEqual(self.what("geo"), [])
        self.tick(61)
        self.assertEqual([c[1] for c in self.what("geo")], [A])
        self.tick(61)
        self.assertEqual(self.what("geo"), [])


class ReviewMinorTest(RunCase):
    """REV-s1-t6 З3, П2–П7."""

    def test_half_volume_only_after_two_intervals(self):
        self.checks("main", speed={"enabled": True, "every_h": 1}, geo={"enabled": False})
        self.checks("g-111111", geo={"enabled": False})
        self.traffic(A, 0, NOW - 60)
        self.tick()
        total = 0
        for t, add in ((H, 10 ** 9), (H + 60, 60 * 10 ** 6), (int(2.5 * H), 2 * 10 ** 9)):   # > 1 Mbit/s all along
            total += add                                        # due at 1 h; at 2.5 h — 1.5 intervals past due
            self.traffic(A, total, NOW + t)
            self.now = self.mono = NOW + t
            self.tick()
        self.assertEqual(self.what("speed"), [])

    def test_commit_waits_for_the_lock(self):
        import threading
        done = []
        with lock_held_elsewhere(run.lock_path()):
            t = threading.Thread(target=lambda: done.append(run.commit([{"exit": A, "kind": "svc", "ts": 1}])))
            t.start()
            t.join(0.5)
            self.assertEqual(done, [])
            self.assertFalse(os.path.exists(paths.probe_results()))
        t.join(10)
        self.assertEqual(len(done), 1)
        self.assertEqual(run.read_results()["seq"], 1)

    def test_the_budget_is_checked_against_the_disk(self):
        self.checks("main", speed={"enabled": True}, geo={"enabled": False})
        self.checks("g-111111", geo={"enabled": False})
        self.st["gateway"]["exit_policy"]["probes"]["speed"].update(budget_day_mb=15)
        ctx = run.context(None, now=self.now)                   # the run read the state: nothing spent
        run.manual_speed(None, A, now=self.now)                 # a manual test spends 10 MB meanwhile
        r = run.Run(ctx, self.now, self.mono + run.RUN_BUDGET_S)
        self.calls = []
        run.do_speed(r)
        r.commit()
        self.assertEqual(self.what("speed"), [])                # 10 more would pass the 15 MB of the day
        self.assertEqual(run.read_state()["exits"][A]["budget"]["day_b"], 10 * 1000 * 1000)

    def test_seq_goes_on_without_the_results_file(self):
        run.commit([{"exit": A, "kind": "svc", "ts": 1}, {"exit": A, "kind": "svc", "ts": 2}])
        os.remove(paths.probe_results())
        out = run.commit([{"exit": A, "kind": "svc", "ts": 3}])
        self.assertEqual(out[0]["seq"], 3)
        with open(paths.probe_results(), "w") as f:
            f.write("{broken")
        self.assertEqual(run.commit([{"exit": A, "kind": "svc", "ts": 4}])[0]["seq"], 4)

    def test_quota_standby_of_a_dead_exit_is_not_removed(self):
        rec = {"state": "off", "reasons": ["dead", "quota"], "quota": {"action": "standby"}}
        self.assertFalse(run.quota_removed(rec, {}, None, A, NOW))
        rec["quota"]["action"] = "remove"
        self.assertTrue(run.quota_removed(rec, {}, None, A, NOW))
        self.assertTrue(run.quota_removed({"state": "off", "reasons": ["quota"]}, {}, None, A, NOW))
        self.assertFalse(run.quota_removed({"state": "standby", "reasons": ["quota"]},
                                           {"quota": {"action": "standby"}}, None, A, NOW))

    def test_switched_off_exit_is_not_probed(self):
        self.checks("g-111111", speed={"enabled": True},
                    services=[{"id": "yt", "name": "YouTube", "url": "https://www.youtube.com/generate_204",
                               "expect": [204], "max_ms": 3000, "every_min": 30}])
        self.checks("main", geo={"enabled": False})
        self.tick()
        self.assertTrue([c for c in self.calls if c[1] == B])
        self.pref(B, enabled=False, country={"expected": "FI"})
        self.locs = {A: "NL", "direct": "RU"}
        self.checks("main", geo={"enabled": True, "expect": "DE"})
        for dt in (H * 7, 61, 61, 61):
            self.tick(dt)
            self.assertEqual([c for c in self.calls if c[1] == B], [])
        self.assertEqual(run.manual_speed(None, B, now=self.now)["why"], "disabled")
        self.assertIn(B, run.read_state()["exits"])             # its record (the budget) is kept

    def test_journal_in_russian_has_no_codes(self):
        lines = []
        self.checks("main", speed={"enabled": True, "every_h": 1}, geo={"expect": "DE"})
        self.checks("g-111111", geo={"enabled": False})
        self.st["gateway"]["exit_policy"]["probes"]["speed"].update(budget_day_mb=1)
        self.locs = {A: "NL", "direct": "NL"}
        fake = self.fake_speed
        with mock.patch("kaskad.i18n.saved_lang", lambda: "ru"), \
                mock.patch.object(run.util, "log_action", lambda text, *a, **k: lines.append(str(text))):
            self.tick()                                         # the pass: node_loc; the budget: day
            self.st["gateway"]["exit_policy"]["probes"]["speed"].update(budget_day_mb=100)

            def sp(*a, **k):
                return dict(fake(*a, **k), ok=False, mbps=None, why="socks", why_args={"rep": "no_inbound"})
            with mock.patch.object(speed, "run", sp):
                self.tick(H + 61)
        text = "\n".join(lines)
        self.assertIn("суточный", text)
        self.assertIn("совпала со страной сервера", text)
        self.assertIn("порт не слушает", text)
        for code in ("(day)", "node_loc", "all_exits", "no_inbound", "(socks)"):
            self.assertNotIn(code, text)


class ReviewT6SecondTest(RunCase):
    """REV-s1-t6-2: Н1 and П-a, П-b."""

    def test_sigterm_during_the_first_test_of_a_new_exit(self):
        # Н1: the commit of SIGTERM (finally of tick) must not put back «speed_due = now» queued before the test
        self.checks("main", speed={"enabled": True, "every_h": 6}, geo={"enabled": False})
        self.checks("g-111111", geo={"enabled": False})
        with mock.patch.object(speed, "run", side_effect=Killed()):
            with self.assertRaises(Killed):
                self.tick()
        st = run.read_state()
        self.assertEqual(st["exits"][A]["speed_due"], NOW + 6 * H)
        self.assertEqual(st["exits"][A]["budget"]["day_b"], 10 * 1000 * 1000)
        self.tick(61)
        self.assertEqual(self.what("speed"), [])                # the killed test is not repeated

    def test_sigterm_in_a_later_stage_keeps_what_was_gathered(self):
        # П-a 2: the commit of tick is in «finally» — a stage killed after the country keeps its rows
        self.checks("main", services=[{"id": "x", "name": "X", "url": "https://slow.example.com/", "expect": [204],
                                       "max_ms": 3000, "every_min": 30}])
        self.checks("g-111111", geo={"enabled": False})
        with mock.patch.object(services, "check", side_effect=Killed()):
            with self.assertRaises(Killed):
                self.tick()
        self.assertEqual([(x["exit"], x["how"]) for x in self.results() if x["kind"] == "geo"], [(A, "plan")])
        self.assertEqual(run.read_state()["last_geo"], NOW)

    def test_no_time_for_the_extra_pass_it_is_the_next_runs(self):
        # П-a 3: the first sight of another country with no time left for the pass — pass_due, the next run does it
        self.checks("main", geo={"expect": "DE"})
        self.checks("g-111111", geo={"enabled": False})
        self.locs = {A: "NL", "direct": "RU"}
        fake = self.fake_trace
        left = geo.WORST_S - 1

        def slow(via, guard=None, deadline=None):
            self.mono = deadline - left                         # the reading took the run's time
            return fake(via, guard, deadline)
        with mock.patch.object(geo, "trace", slow):
            self.tick()
        self.assertEqual([c[1] for c in self.what("geo")], [A])  # no pass in this run
        self.assertTrue(run.read_state()["exits"][A]["pass_due"])
        self.tick(61)
        self.assertEqual(sorted(c[1] for c in self.what("geo")), sorted([A, "direct"]))
        self.assertNotIn("pass_due", run.read_state()["exits"][A])

    def test_the_deadline_counts_from_the_start_of_the_run(self):
        # П-b: context() may run «--egress-ensure-parser» once (up to 60 s): the run's 100 s count from its start
        self.checks("main", speed={"enabled": True})
        self.checks("g-111111", geo={"enabled": False})
        pool = self.pool
        got = []

        def slow_pool(*a, **k):
            self.mono += 60
            return copy.deepcopy(pool)
        fs, ft = self.fake_speed, self.fake_trace
        with mock.patch.object(run.exits, "pool", slow_pool), \
                mock.patch.object(speed, "run", lambda *a, **k: got.append(k.get("deadline")) or fs(*a, **k)), \
                mock.patch.object(geo, "trace", lambda *a, **k: got.append(k.get("deadline")) or ft(*a, **k)):
            start = self.mono
            self.tick()
            self.assertEqual(got, [start + run.RUN_BUDGET_S] * 2)    # the country and the speed test
            got[:] = []
            start = self.mono
            run.manual_speed(None, A, now=self.now)
            self.assertEqual(got, [start + run.RUN_BUDGET_S])


class PolicyReadTest(RunCase):
    """T4: the unit reads the policy by T2's functions (policy.load with the pool, model.exit_pref), no copies."""

    C, R = "e-cccccccccc", "e-dddddddddd"

    def test_a_new_exit_is_read_in_the_group_it_will_be_placed_in(self):
        self.pool += [{"id": self.C, "index": 3, "kind": "xray", "role": "abroad"},
                      {"id": self.R, "index": 4, "kind": "xray", "role": "ru"}]
        self.pref(self.C, group="g-111111")                    # the hint of the key (SPEC-groups §2.4)
        self.checks("g-111111", speed={"enabled": True, "every_h": 3})
        xs = dict((x.id, x) for x in run.context(None, now=self.now)[0])
        self.assertEqual((xs[A].gid, xs[B].gid, xs[self.C].gid), ("main", "g-111111", "g-111111"))
        self.assertEqual(xs[self.C].checks["speed"]["every_h"], 3)
        self.assertIsNone(xs[self.R].gid)                       # Russian, no Russian group: out of the groups
        self.assertEqual(xs[self.R].checks, run.P.default_checks("ru"))
        self.assertEqual(xs[self.R].expected, "RU")
        self.assertEqual(xs[A].probes["speed"]["max_mb"], 10)   # exit_policy.probes with the defaults
        self.assertEqual(xs[A].probes["speed"]["budget_day_mb"], 200)

    def test_without_a_stored_policy(self):
        del self.st["gateway"]["exit_policy"]
        xs = dict((x.id, x) for x in run.context(None, now=self.now)[0])
        self.assertEqual((xs[A].gid, xs[B].gid), ("main", "main"))   # «main» in «compat»
        self.assertEqual(xs[A].probes, run.P.default_probes())

    def test_bytes_in_every_row_of_the_probes(self):
        # the bytes through the exit (DoH too: ``rx`` of safe_get) in the rows of the services, the country, the pass
        self.checks("main", geo={"expect": "DE"},
                    services=[{"id": "x", "name": "X", "url": "https://a.example.com/", "expect": [204],
                               "max_ms": 3000, "every_min": 30}])
        self.locs = {A: "NL", B: "DE", "direct": "RU"}
        ft, fv = self.fake_trace, self.fake_svc
        with mock.patch.object(geo, "trace", lambda *a, **k: dict(ft(*a, **k), bytes=700)), \
                mock.patch.object(services, "check", lambda *a, **k: dict(fv(*a, **k), bytes=900)):
            self.tick()
        rows = [(x["kind"], x.get("how"), x.get("bytes")) for x in self.results() if x["kind"] in ("geo", "svc")]
        self.assertEqual(sorted(rows, key=str), sorted([("geo", "plan", 700), ("geo", "pass", 700),
                                                        ("geo", "pass", 700), ("svc", None, 900)], key=str))
        with mock.patch.object(geo, "trace", side_effect=RuntimeError("x")):
            self.tick(25 * H)
        self.assertEqual([x["bytes"] for x in self.results() if x["kind"] == "geo"][-1], 0)
