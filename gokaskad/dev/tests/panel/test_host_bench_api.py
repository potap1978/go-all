"""B13 (k3/handoff/SPEC-node-score-schedule-share.md §2.6, §3.4): the API of the schedule and of the history of the
checks in panel/host.py on the fake node — GET/preview/PUT of the schedule (the strict body, ``If-Match``, 400 of the
validator, 409 ``need_confirm`` and ``stale``, ``armed_why``, the preview saves nothing, the timer set again), the
history (the list, the detail, 404 of a foreign id, the comparison), the export CSV/JSON (a row per test, a row of a
skip, the guard against formulas, the name of the file without the node, no addresses), the login."""

import json
import os
import re
import threading
import time
from unittest import mock

from kaskad import host_bench_cli as HB
from kaskad import host_sched, paths, state, sysrun, tz
from kaskad.bench import history, machine, pkgs, run, sched_unit, spool
from kaskad.panel import host as H
from tests.panel.panelcase import PanelCase

MOSCOW = tz.Zone("Europe/Moscow", types=[(10800, False)])
DAY_S = 86400
MID_RAW = "0123456789abcdef0123456789abcdef"
HERE = machine.mid_of(MID_RAW)
WEEK = [1, 2, 3, 4, 5, 6, 7]


def ent(t, mode="quick", days=WEEK, **kw):
    e = {"time": t, "mode": mode, "days": list(days)}
    e.update(kw)
    return e


class BenchApiCase(PanelCase):
    def setUp(self):
        super().setUp()
        H.reset()
        self.addCleanup(H.reset)
        self.write("/etc/machine-id", MID_RAW + "\n", 0o444)
        p = mock.patch.object(sched_unit, "_zone", return_value=(MOSCOW, None))
        p.start()
        self.addCleanup(p.stop)
        self.runner = sysrun.FakeRunner()
        self.runner.on(["systemctl"], "")
        sched_unit.RUNNER[0] = self.runner
        self.addCleanup(lambda: sched_unit.RUNNER.__setitem__(0, None))
        self.now = int(time.time())

    def bench(self, sched=None, armed=True, mid=HERE, **more):
        with state.transaction() as st:
            g = st.setdefault("gateway", {})
            hm = g.setdefault("hostmon", {})
            b = {"retry": True}
            if sched is not None:
                b["schedule"] = sched
            if armed:
                b.update(mid=mid, first_manual_at=self.now - 3600, pkgs="yes", armed_at=self.now - 3600)
            b.update(more)
            hm["bench"] = b

    def saved(self):
        return state.load()["gateway"]["hostmon"]["bench"]

    def state_bytes(self):
        with open(paths.state(), "rb") as f:
            return f.read()

    def get(self):
        r = self.call("GET", "/api/host/bench/schedule")
        self.assertEqual(r.status, 200, r)
        return r

    def put(self, sched, etag=None, **more):
        body = dict(more, schedule=sched)
        if etag is None:
            etag = self.get().json()["etag"]
        h = {"If-Match": etag} if etag else {}
        return self.call("PUT", "/api/host/bench/schedule", body, headers=h)


class LoginTest(BenchApiCase):
    def test_every_route_wants_the_login(self):
        for method, path in (("GET", "/api/host/bench/schedule"), ("POST", "/api/host/bench/schedule/preview"),
                             ("PUT", "/api/host/bench/schedule"), ("GET", "/api/host/bench"),
                             ("GET", "/api/host/bench/0123456789"), ("GET", "/api/host/bench/compare?a=1&b=2"),
                             ("GET", "/api/host/bench/export?format=csv"), ("POST", "/api/host/bench")):
            r = self.call(method, path, {"schedule": []} if method != "GET" else None, tunnel=False)
            self.assertEqual(r.status, 401, (method, path, r))

    def test_changes_want_the_admin_header(self):
        self.bench([])
        for method, path in (("POST", "/api/host/bench/schedule/preview"), ("PUT", "/api/host/bench/schedule"),
                             ("POST", "/api/host/bench")):
            r = self.call(method, path, {"schedule": []}, csrf=False)
            self.assertEqual(r.status, 403, (method, path, r))
        self.assertEqual(self.saved().get("schedule"), [])


class ScheduleGetTest(BenchApiCase):
    def test_get_shape_etag_no_store(self):
        self.bench([dict(ent("03:45"), id="a1b2c3", on=True, quiet=True, monthly=False, since=self.now)])
        r = self.get()
        doc = r.json()
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        self.assertEqual(r.headers.get("etag"), doc["etag"])
        self.assertRegex(doc["etag"], r'^"[0-9a-f]{20}"$')
        for k in ("schedule", "retry", "armed", "armed_why", "pkgs", "tz", "next", "cost", "limits", "timer"):
            self.assertIn(k, doc)
        self.assertEqual(doc["limits"], {"per_day": 6, "full_per_day": 2, "gap_min": 120, "total": 12,
                                         "jitter_min": 15})
        self.assertIs(doc["armed"], True)
        self.assertIsNone(doc["armed_why"])
        self.assertEqual(doc["tz"], tz.name())
        self.assertEqual(len(doc["next"]), 5)
        self.assertEqual(doc["cost"]["basis"], "caps")
        self.assertEqual(doc["schedule"][0]["id"], "a1b2c3")
        self.assertEqual(self.get().json()["etag"], doc["etag"])        # the same state — the same version

    def test_armed_why(self):
        cases = (({"armed": False}, "no_manual"),
                 ({"armed": True, "pkgs": "no"}, "no_pkgs"),
                 ({"armed": True, "mid": "f" * 32}, "other_machine"))
        for kw, why in cases:
            self.bench([], **kw)
            doc = self.get().json()
            self.assertIs(doc["armed"], False, why)
            self.assertEqual(doc["armed_why"], why)
        os.remove(self.root + "/etc/machine-id")
        self.assertEqual(self.get().json()["armed_why"], "no_machine_id")

    def test_empty_list_has_the_proposal_with_its_price(self):
        """B15 (SS §1.3, NS-S18; REV-design-2026-10-19-2 Н2-П3): an empty list — ``propose``: a quick one weekly and a
        full one monthly, both in the same quiet hour and «only when idle», the price of both and of the quick one;
        the minutes are the node's own and the same at every GET; a list with entries — no proposal."""
        self.bench([])
        doc = self.get().json()
        pr = doc["propose"]
        q, f = pr["entries"]
        self.assertEqual((q["mode"], q["monthly"], f["mode"], f["monthly"]), ("quick", False, "full", True))
        self.assertEqual(q["time"], f["time"])
        self.assertTrue(q["quiet"] and f["quiet"] and q["on"] and f["on"])
        self.assertNotEqual(q["days"], f["days"])
        self.assertIn(pr["basis"], ("h1", "default"))
        self.assertIn("rx_mb", pr["cost"]["month"])
        self.assertLess(pr["cost_quick"]["month"]["rx_mb"], pr["cost"]["month"]["rx_mb"])
        self.assertEqual(self.get().json()["propose"], pr)
        self.bench([dict(ent("03:45"), id="a1b2c3", on=True, quiet=True, monthly=False, since=self.now)])
        self.assertNotIn("propose", self.get().json())

    def test_broken_saved_entry_is_saved_off(self):
        self.bench([dict(ent("25:00"), id="a1b2c3")])
        doc = self.get().json()
        self.assertIs(doc["schedule"][0]["on"], False)
        self.assertEqual(doc["schedule"][0]["off_why"], "time")
        self.assertEqual(self.saved()["schedule"][0]["off_why"], "time")

    def test_lock_busy_answers_in_a_second_unsaved(self):
        self.bench([dict(ent("25:00"), id="a1b2c3")])
        before = self.state_bytes()
        got = {}
        with state.locked():                           # another holder: the server thread waits ≤ 1 s
            t0 = time.monotonic()
            th = threading.Thread(target=lambda: got.update(r=self.call("GET", "/api/host/bench/schedule")))
            th.start()
            th.join(10)
            took = time.monotonic() - t0
        r = got["r"]
        self.assertEqual(r.status, 200, r)
        self.assertLess(took, 2.5)
        self.assertEqual(r.json()["schedule"][0]["off_why"], "time")
        self.assertEqual(self.state_bytes(), before)


class PreviewTest(BenchApiCase):
    def test_preview_saves_nothing(self):
        self.bench([])
        before = self.state_bytes()
        r = self.call("POST", "/api/host/bench/schedule/preview", {"schedule": [ent("03:45"), ent("12:00", "full")]})
        self.assertEqual(r.status, 200, r)
        doc = r.json()
        self.assertEqual(doc["errors"], [])
        self.assertEqual([e["id"] for e in doc["schedule"]], [None, None])   # the id is given by the save only
        self.assertEqual(doc["cost"]["week"], {"quick": 7.0, "full": 7.0})
        self.assertTrue(doc["next"])
        self.assertEqual(set(v["id"] for v in doc["next"]), {None})
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.runner.calls, [])

    def test_preview_tells_the_errors_with_texts(self):
        self.bench([])
        doc = self.ok("POST", "/api/host/bench/schedule/preview",
                      {"schedule": [ent("03:00"), ent("04:00")]}, headers={"X-Kaskad-Lang": "en"})
        codes = [e["code"] for e in doc["errors"]]
        self.assertIn("gap", codes)
        self.assertTrue(all(e["text"] for e in doc["errors"]))
        self.assertIs(doc["schedule"][1]["on"], False)

    def test_the_price_takes_the_tariff_and_the_node_in_russia(self):
        """Стык C1 × B13: the traffic of a month of the tariff (C1) — «≈ N % of the tariff» and the threshold 10 %; the
        node in Russia (the country its VLESS record keeps) — no upload; unknown country — a foreign node."""
        self.bench([])
        body = {"schedule": [ent("03:45")]}
        doc = self.ok("POST", "/api/host/bench/schedule/preview", body)
        codes = [n["code"] for n in doc["cost"]["notes"]]
        self.assertNotIn("tariff_share", codes)
        self.assertNotIn("no_upload_ru", codes)
        foreign_tx = doc["cost"]["month"]["tx_mb"]
        self.assertGreater(foreign_tx, 0)
        with state.transaction() as st:
            st["gateway"]["hostmon"]["tariff"] = {"v": 1, "vals": {"traffic_gb": {"v": 20, "src": "user"}}}
            st["gateway"]["inbounds"] = {"vless": {"country": {"code": "RU", "source": "ipinfo"}}}
        doc = self.ok("POST", "/api/host/bench/schedule/preview", body)
        codes = [n["code"] for n in doc["cost"]["notes"]]
        self.assertIn("tariff_share", codes)
        self.assertIn("heavy", codes)                      # ≈ 2 GB of 20 GB a month — above 10 % of the tariff
        self.assertIn("no_upload_ru", codes)
        self.assertEqual(doc["cost"]["month"]["tx_mb"], 0)
        self.assertIn("no_upload_ru", [n["code"] for n in self.get().json()["cost"]["notes"]])   # GET — the same
        with state.transaction() as st:                    # an unlimited tariff, a foreign node
            st["gateway"]["hostmon"]["tariff"] = {"v": 1, "vals": {"traffic_gb": {"v": "unlimited", "src": "user"}}}
            st["gateway"]["inbounds"]["vless"]["country"]["code"] = "FI"
        doc = self.ok("POST", "/api/host/bench/schedule/preview", body)
        codes = [n["code"] for n in doc["cost"]["notes"]]
        self.assertNotIn("tariff_share", codes)
        self.assertEqual(doc["cost"]["month"]["tx_mb"], foreign_tx)

    def test_the_proposal_is_priced_as_the_list_with_the_tariff_and_russia(self):
        """REV-ns-b15-2 Н-3: the price of ``propose`` — the same context as the list (C1 tariff, the node in Russia):
        the notes of the tariff and of «no upload», no upload traffic, and both prices equal the preview of the same
        entries (``cost`` — both, ``cost_quick`` — the quick one)."""
        self.bench([])
        with state.transaction() as st:
            st["gateway"]["hostmon"]["tariff"] = {"v": 1, "vals": {"traffic_gb": {"v": 20, "src": "user"}}}
            st["gateway"]["inbounds"] = {"vless": {"country": {"code": "RU", "source": "ipinfo"}}}
        pr = self.get().json()["propose"]
        codes = [n["code"] for n in pr["cost"]["notes"]]
        self.assertIn("no_upload_ru", codes)
        self.assertIn("tariff_share", codes)
        self.assertEqual(pr["cost"]["month"]["tx_mb"], 0)
        self.assertEqual(pr["cost_quick"]["month"]["tx_mb"], 0)
        both = self.ok("POST", "/api/host/bench/schedule/preview", {"schedule": pr["entries"]})["cost"]
        quick = self.ok("POST", "/api/host/bench/schedule/preview", {"schedule": pr["entries"][:1]})["cost"]
        self.assertEqual(pr["cost"], both)
        self.assertEqual(pr["cost_quick"], quick)

    def test_the_price_of_a_node_in_russia_takes_its_allowed_point(self):
        """REV-ns-join З-2 (M9): the node in Russia with the upload point the owner allowed (NS-S5) — the upload of a
        check is the cap of that point (``notes: upload_ru_point``), not 0 and not the cap of a foreign node; a point
        the channel would not use (not https) — no upload."""
        self.bench([])
        body = {"schedule": [ent("03:45")], "retry": False}
        with state.transaction() as st:
            st["gateway"]["hostmon"]["bench"]["ru"] = "yes"
            st["gateway"]["hostmon"]["bench"]["ru_point"] = {"url": "https://up.example.com/u", "quick": 100}
        doc = self.ok("POST", "/api/host/bench/schedule/preview", body)
        codes = [n["code"] for n in doc["cost"]["notes"]]
        self.assertIn("upload_ru_point", codes)
        self.assertNotIn("no_upload_ru", codes)
        self.assertEqual(doc["cost"]["per_run"]["quick"]["tx_mb"], 100)
        with state.transaction() as st:
            st["gateway"]["hostmon"]["bench"]["ru_point"]["url"] = "http://up.example.com/u"
        doc = self.ok("POST", "/api/host/bench/schedule/preview", body)
        codes = [n["code"] for n in doc["cost"]["notes"]]
        self.assertIn("no_upload_ru", codes)
        self.assertEqual(doc["cost"]["per_run"]["quick"]["tx_mb"], 0)

    def test_preview_retry_off_has_no_retry_price(self):
        self.bench([])
        doc = self.ok("POST", "/api/host/bench/schedule/preview", {"schedule": [ent("03:45")], "retry": False})
        self.assertIsNone(doc["cost"]["retry"])


class StrictBodyTest(BenchApiCase):
    def refused(self, body, code="invalid", status=400, path="/api/host/bench/schedule/preview", method="POST"):
        r = self.call(method, path, body, raw_body=None)
        self.assertEqual(r.status, status, (body, r))
        doc = r.json()
        self.assertEqual(doc["code"], code)
        self.assertTrue(doc["error"])
        return doc

    def test_bad_bodies(self):
        self.bench([])
        before = self.state_bytes()
        cases = [
            ({}, "body"), ({"entries": []}, "body"), ({"schedule": {}}, "list"),
            ({"schedule": [], "extra": 1}, "field"), ({"schedule": [], "confirm": "yes"}, "type"),
            ({"schedule": [], "retry": 1}, "type"), ({"schedule": [], "tz": "Europe/Moscow\n"}, "tz"),
            ({"schedule": [], "tz": "../../etc"}, "tz"),
            ({"schedule": [dict(ent("03:45"), note="x")]}, "field"),
            ({"schedule": [dict(ent("03:45"), mode="q" * 65)]}, "long"),
            ({"schedule": [dict(ent("03:45"), days=[1] * 8)]}, "long"),
            ({"schedule": [dict(ent("03:45"), since="1")]}, "type"),
            ({"schedule": [dict(ent("03:45"), since=True)]}, "type"),
            ({"schedule": [dict(ent("03:45"), off_why=5)]}, "type"),
            ({"schedule": [ent("%02d:00" % (2 * i)) for i in range(13)]}, "total"),
        ]
        for body, code in cases:
            for path, method in (("/api/host/bench/schedule/preview", "POST"), ("/api/host/bench/schedule", "PUT")):
                doc = self.refused(body, path=path, method=method)
                self.assertEqual(doc["errors"][0]["code"], code, (body, method))
                self.assertTrue(doc["errors"][0]["text"])
        self.assertEqual(self.state_bytes(), before)

    def test_unknown_key_is_never_echoed_as_it_came(self):
        self.bench([])
        doc = self.refused({"schedule": [], "<script>": 1})
        self.assertEqual(doc["errors"][0]["args"]["field"], "?")
        self.assertNotIn("<script>", json.dumps(doc))

    def test_not_json_and_too_big(self):
        self.bench([])
        r = self.call("POST", "/api/host/bench/schedule/preview", raw_body=b"[1,2]",
                      headers={"Content-Type": "application/json"})
        self.assertEqual(r.status, 400)
        big = {"schedule": [], "x": "a" * (H.BODY_MAX + 10)}
        self.refused(big, code="too_big", status=413)

    def test_types_of_the_fields_are_the_validators(self):
        self.bench([])
        doc = self.ok("POST", "/api/host/bench/schedule/preview",
                      {"schedule": [ent("03:45\n"), dict(ent("12:00"), days=[True]), dict(ent("18:00"), on="1")]})
        self.assertEqual(sorted(e["code"] for e in doc["errors"]), ["days", "on", "time"])


class SchedulePutTest(BenchApiCase):
    def test_put_saves_gives_ids_and_sets_the_timer(self):
        self.bench([])
        r = self.put([ent("03:45")])
        self.assertEqual(r.status, 200, r)
        doc = r.json()
        e = self.saved()["schedule"][0]
        self.assertRegex(e["id"], r"^[0-9a-f]{6}$")
        self.assertGreaterEqual(e["since"], self.now)
        self.assertEqual(doc["schedule"][0]["id"], e["id"])
        self.assertIs(doc["apply"]["applied"], True)
        self.assertIn("OnCalendar=Mon,Tue,Wed,Thu,Fri,Sat,Sun *-*-* 03:30:00 Europe/Moscow",
                      sched_unit.current_timer() or "")
        self.assertEqual(doc["timer"], {"active": True, "applied": True, "why": None})
        self.assertEqual(r.headers.get("etag"), doc["etag"])
        self.assertEqual(self.get().json()["etag"], doc["etag"])

    def test_if_match_missing_and_stale(self):
        self.bench([])
        r = self.put([ent("03:45")], etag="")
        self.assertEqual(r.status, 428, r)
        self.assertEqual(r.json()["code"], "need_etag")
        old = self.get().json()["etag"]
        self.assertEqual(self.put([ent("03:45")], etag=old).status, 200)
        r = self.put([ent("05:45")], etag=old)                 # the other window still has the old version
        self.assertEqual(r.status, 409, r)
        self.assertEqual(r.json()["code"], "stale")
        self.assertEqual(self.saved()["schedule"][0]["time"], "03:45")
        self.assertEqual(self.put([ent("05:45")], etag='W/' + old).status, 409)
        self.assertEqual(self.put([ent("05:45")], etag="*").status, 409)

    def test_validator_errors_400(self):
        self.bench([])
        before = self.state_bytes()
        r = self.put([ent("03:00"), ent("04:00")])
        self.assertEqual(r.status, 400, r)
        doc = r.json()
        self.assertEqual(doc["code"], "invalid")
        self.assertIn("gap", [e["code"] for e in doc["errors"]])
        self.assertEqual(self.state_bytes(), before)

    def test_unknown_id_400(self):
        self.bench([])
        r = self.put([dict(ent("03:45"), id="abcdef")])
        self.assertEqual(r.status, 400, r)
        self.assertEqual(r.json()["errors"][0]["code"], "id")

    def test_heavy_needs_confirm(self):
        self.bench([])
        heavy = [ent("03:00", "full"), ent("12:00", "full")]
        r = self.put(heavy)
        self.assertEqual(r.status, 409, r)
        doc = r.json()
        self.assertEqual(doc["code"], "need_confirm")
        self.assertIn("heavy", [n["code"] for n in doc["cost"]["notes"]])
        self.assertEqual(self.saved().get("schedule"), [])
        self.assertEqual(self.put(heavy, confirm=True).status, 200)
        self.assertEqual(len(self.saved()["schedule"]), 2)

    def test_tz_changed(self):
        self.bench([])
        r = self.put([ent("03:45")], tz="Asia/Tokyo")
        self.assertEqual(r.status, 409, r)
        self.assertEqual(r.json()["code"], "tz_changed")
        self.assertEqual(self.put([ent("03:45")], tz=tz.name()).status, 200)

    def test_echo_of_get_with_a_stable_id_is_accepted(self):
        """REV-ns-b10: the old list for ``prepare`` is taken after ``fix_in`` — an id GET gave by ``stable_id`` (the
        lock was busy, nothing saved) is a known one."""
        self.bench([ent("03:45")])                         # no id saved
        with mock.patch("kaskad.bench.fix_state", side_effect=lambda timeout=15.0: _unsaved()):
            got = self.get().json()
        sid = got["schedule"][0]["id"]
        self.assertNotIn("id", self.saved()["schedule"][0])
        r = self.put(got["schedule"], etag=got["etag"])
        self.assertEqual(r.status, 200, r)
        self.assertEqual(self.saved()["schedule"][0]["id"], sid)

    def test_off_why_and_since_of_the_request_are_not_taken(self):
        self.bench([])
        self.assertEqual(self.put([ent("03:45")]).status, 200)
        got = self.get().json()
        e = dict(got["schedule"][0], since=1, off_why="gap")
        self.assertEqual(self.put([e], etag=got["etag"]).status, 200)
        s = self.saved()["schedule"][0]
        self.assertGreaterEqual(s["since"], self.now)
        self.assertIsNone(s["off_why"])

    def test_retry_switch(self):
        self.bench([])
        self.assertEqual(self.put([], retry=False).status, 200)
        self.assertIs(self.saved()["retry"], False)
        self.assertIs(self.get().json()["retry"], False)

    def test_not_root_saves_and_says_the_timer_is_not_set(self):
        self.bench([])
        sched_unit.RUNNER[0] = None
        with mock.patch.object(sched_unit, "system_runner", return_value=None):
            r = self.put([ent("03:45")])
        self.assertEqual(r.status, 200, r)
        self.assertEqual(r.json()["apply"], {"applied": False, "why": "no_root"})
        self.assertEqual(len(self.saved()["schedule"]), 1)


    def test_tz_null_of_get_is_accepted_at_a_fallback(self):
        """REV-ns-b13 З-1: the zone not read — GET says ``tz: null``; the echo of it saves, a name — ``tz_changed``."""
        self.bench([])
        with mock.patch.object(sched_unit, "_zone", return_value=(MOSCOW, "tz_fallback")):
            got = self.get().json()
            self.assertIsNone(got["tz"])
            self.assertEqual(got["tz_warn"], "tz_fallback")
            r = self.put([ent("03:45")], etag=got["etag"], tz=None)
            self.assertEqual(r.status, 200, r)
            r = self.put([ent("05:45")], tz="Europe/Moscow")
            self.assertEqual(r.status, 409, r)
            self.assertEqual(r.json()["code"], "tz_changed")
        r = self.put([ent("05:45")], tz=None)                   # the zone is read again: null is not it
        self.assertEqual(r.status, 409, r)
        self.assertEqual(r.json()["code"], "tz_changed")

    def test_etag_holds_the_retry_switch(self):
        """REV-ns-b13 П-4 (M4): only ``retry`` changed in another window — the old version is stale."""
        self.bench([])
        old = self.get().json()["etag"]
        self.assertEqual(self.put([], etag=old, retry=False).status, 200)
        r = self.put([], etag=old, retry=False)
        self.assertEqual(r.status, 409, r)
        self.assertEqual(r.json()["code"], "stale")

    def test_etag_is_compared_under_the_lock(self):
        """REV-ns-b13 П-4 (M5): the state changes between the read of the version and the lock — ``stale``."""
        from kaskad.bench import fix_in as real_fix_in
        self.bench([])
        old = self.get().json()["etag"]
        calls = []

        def fix_in(st):                                  # another window saved meanwhile: the state of the lock has it
            if not calls:
                calls.append(1)
                st["gateway"]["hostmon"]["bench"]["retry"] = False
            return real_fix_in(st)
        with mock.patch("kaskad.bench.fix_in", side_effect=fix_in):
            r = self.put([ent("03:45")], etag=old)
        self.assertEqual(r.status, 409, r)
        self.assertEqual(r.json()["code"], "stale")

    def test_retry_off_stops_the_repeats_waiting(self):
        """REV-ns-b13: ``retry`` true → false — the repeats still waiting are stopped (no «I'll check again at …»)."""
        self.bench([])
        with mock.patch.object(sched_unit, "stop_retries") as stop:
            self.assertEqual(self.put([], retry=True).status, 200)
            stop.assert_not_called()
            self.assertEqual(self.put([], retry=False).status, 200)
            self.assertEqual(stop.call_count, 1)
            self.assertEqual(self.put([], retry=False).status, 200)      # already off: nothing to stop
            self.assertEqual(stop.call_count, 1)


def _unsaved():
    from kaskad.gw import model
    return dict(model.bench(state.load(), here=HERE), saved=False)


class HistoryCase(BenchApiCase):
    def setUp(self):
        super().setUp()
        self.bench([])
        self.n = 0

    def check(self, ago, status="ok", vals=None, **head):
        self.n += 1
        h = {"id": "%010x" % self.n, "ts": self.now - int(ago), "mode": "quick", "by": "panel", "status": status}
        h.update(head)
        if vals is None:
            vals = {"cpu_1t": {"v": 1234.5, "mode": "quick"}, "dns": {"v": 12.0, "mode": "quick"}}
        spool.put(spool.check_doc(h, vals))
        return h["id"]


class HistoryTest(HistoryCase):
    def test_list_detail_compare(self):
        a = self.check(3600)
        b = self.check(1800, by="schedule", mode="full")
        doc = self.ok("GET", "/api/host/bench")
        self.assertEqual([r["id"] for r in doc["runs"]], [b, a])
        self.assertIs(doc["more"], False)
        self.assertEqual(self.ok("GET", "/api/host/bench?by=schedule")["runs"][0]["id"], b)
        self.assertEqual(self.ok("GET", "/api/host/bench?limit=1")["more"], True)
        d = self.ok("GET", "/api/host/bench/" + a)
        self.assertEqual(d["id"], a)
        self.assertEqual(sorted(t["test"] for t in d["tests"]), ["cpu_1t", "dns"])
        c = self.ok("GET", "/api/host/bench/compare?a=%s&b=%s" % (a, b))
        self.assertIs(c["mode_differs"], True)
        self.assertEqual(len(c["rows"]), 2)
        # NS-D33: the raw values side by side; rel_total of each check — none with two checks, so no «better»
        self.assertEqual({k for r in c["rows"] for k in r}, {"test", "a", "b", "delta", "delta_pct", "unit", "dir"})
        # UI-C: the direction of a test — from the catalogue of the core (▲/▼ of the difference on the screen)
        self.assertEqual({r["test"]: r["dir"] for r in c["rows"]}, {"cpu_1t": "higher", "dns": "lower"})
        self.assertEqual((c["rel_a"], c["rel_b"]), (None, None))
        self.assertNotIn("better", c)

    def test_rows_have_who_started_and_no_score(self):
        """6C (BRIEF-P4-a): ``by_text`` — the one catalog host_sched.by.*; NS-D34: a row has no absolute score — not
        ``score``, ``links``, ``usual``/``usual_n`` of the scores — even when the base still keeps a score of an old
        check (the column of SS §3.1 stays, it is never shown)."""
        D = 86400
        for ago, sc in ((20 * D, 600), (10 * D, 640), (5 * D, 620)):
            self.check(ago, score=sc, links={"cpu": 720, "down": 650})
        self.check(3 * D, score=50, by="auto")
        self.check(2 * D + 60, status="skipped", why="busy")
        top = self.check(3600, score=700)
        rows = {r["id"]: r for r in self.ok("GET", "/api/host/bench?from=%s" % self.day(25 * D))["runs"]}
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[top]["by_text"], "из панели")
        auto = [r for r in rows.values() if r["by"] == "auto"][0]
        self.assertEqual(auto["by_text"], "повтор после резкого падения")
        for r in rows.values():
            self.assertFalse({"score", "links", "usual", "usual_n"} & set(r), r)
        self.assertNotIn("score", self.ok("GET", "/api/host/bench/" + top)["tests"][0])

    def day(self, ago):
        return tz.day(self.now - ago, MOSCOW)

    def test_foreign_ids_404(self):
        self.check(60)
        for rid in ("..%2F..%2Fetc", "aaaaaaaaaa%0A", "AAAAAAAAAA", "aaaaaaaaa", "aaaaaaaaaaa", "0000000099",
                    "%2E%2E", "aaaaaaaaaa%00"):
            r = self.call("GET", "/api/host/bench/" + rid)
            self.assertEqual(r.status, 404, (rid, r))
        r = self.call("GET", "/api/host/bench/../../etc/passwd")
        self.assertIn(r.status, (400, 404))
        for v in ("a" * 10 + "\n", "../" + "a" * 7, "a" * 10 + "\x00", None, 10):
            with self.assertRaises(H.HttpError) as c:
                H._rid(v)
            self.assertEqual(c.exception.status, 404)
        for q in ("a=../x&b=0000000001", "a=0000000001&b=aaaaaaaaaa%0A", "a=0000000001"):
            self.assertEqual(self.call("GET", "/api/host/bench/compare?" + q).status, 404, q)

    def test_strict_query(self):
        for q, field in (("from=2026-13-01", "from"), ("from=2026-1-01", "from"), ("from=2026-01-01%0A", "from"),
                         ("to=x", "to"), ("mode=fast", "mode"), ("by=button", "by"), ("status=done", "status"),
                         ("before=-1", "before"), ("limit=0", "limit"), ("limit=501", "limit"),
                         ("from=2020-01-01&to=2021-12-31", "from"), ("from=2026-02-02&to=2026-02-01", "from")):
            doc = self.err(400, "GET", "/api/host/bench?" + q)
            self.assertEqual(doc["data"]["field"], field, q)
        self.err(400, "GET", "/api/host/bench?evil=1")
        doc = self.err(400, "GET", "/api/host/bench?%3Cb%3E=1")
        self.assertNotIn("<b>", doc["error"])
        self.err(400, "GET", "/api/host/bench/0000000001?x=1")
        self.err(400, "GET", "/api/host/bench/export?format=xml")

    def test_the_period_of_400_days_passes(self):
        a = datetime_days_ago(self.now, 399)
        self.ok("GET", "/api/host/bench?from=%s&to=%s" % (a, tz.day(self.now)))
        self.ok("GET", "/api/host/bench?from=%s" % a)
        self.err(400, "GET", "/api/host/bench?from=%s" % datetime_days_ago(self.now, 400))

    def test_far_years_are_400_not_500(self):
        """REV-ns-b13 З-2: 9999-12-31 overflowed the day after it."""
        for q in ("/api/host/bench?to=9999-12-31", "/api/host/bench/export?to=9999-12-31",
                  "/api/host/bench?from=9999-12-30&to=9999-12-31", "/api/host/bench?from=0001-01-01",
                  "/api/host/bench?to=2101-01-01"):
            self.err(400, "GET", q)
        self.ok("GET", "/api/host/bench?to=2100-12-31")


class RelTest(HistoryCase):
    """UI-C (k3/scratchpad/uiC/API-CONTRACT.md §2–3): ``rel`` of every row of the list — rel_total of the check
    against «обычно» at it (as the comparison of two checks); the detail — also ``rel.tests`` of every test."""

    def vals(self, down=900.0):
        return {"net_down": {"v": down, "mode": "quick"}, "net_up": {"v": 400.0, "mode": "quick"},
                "cpu_1t": {"v": 1234.5, "mode": "quick"}}

    def test_rows_and_the_detail_have_rel(self):
        ids = [self.check(d * DAY_S, vals=self.vals()) for d in range(9, 2, -1)]
        bad = self.check(2 * DAY_S, status="failed", vals={})
        top = self.check(DAY_S, vals=self.vals(450.0))
        rows = dict((r["id"], r) for r in self.ok("GET", "/api/host/bench")["runs"])
        self.assertEqual(len(rows), 9)
        self.assertIsNone(rows[ids[0]]["rel"])                                         # no history before it
        self.assertIsNone(rows[bad]["rel"])                                            # a failed check
        self.assertEqual(rows[ids[-1]]["rel"], 100)                                     # as usual
        self.assertEqual(rows[top]["rel"], 88)              # down 50, up 100, net_bw 75, cpu 100 -> the median 87.5
        c = self.ok("GET", "/api/host/bench/compare?a=%s&b=%s" % (ids[-1], top))      # the same rel as compare
        self.assertEqual((c["rel_a"], c["rel_b"]), (rows[ids[-1]]["rel"], rows[top]["rel"]))
        d = self.ok("GET", "/api/host/bench/" + top)
        self.assertEqual(d["rel"]["rel_total"], 88)
        self.assertEqual(d["rel"]["tests"]["net_down"], {"usual": 900.0, "usual_n": 5, "rel": 50})
        self.assertEqual(d["rel"]["tests"]["net_up"], {"usual": 400.0, "usual_n": 5, "rel": 100})
        self.assertEqual(d["rel"]["tests"]["dns"], {"usual": None, "usual_n": 0, "rel": None})
        self.assertIsNone(self.ok("GET", "/api/host/bench/" + bad)["rel"])
        first = self.ok("GET", "/api/host/bench/" + ids[0])["rel"]
        self.assertEqual((first["rel_total"], first["tests"]["net_down"]), (None, {"usual": None, "usual_n": 0,
                                                                                   "rel": None}))

    def test_the_rows_count_rel_one_by_one(self):
        """One ``sample`` for the page, the series shared (score.rel_cx): the same numbers as check_rel of each."""
        for d in range(12, 0, -1):
            self.check(d * DAY_S - 600, vals=self.vals(900.0 - 30 * (d % 4)))
        doc = self.ok("GET", "/api/host/bench?limit=7")
        self.assertEqual(len(doc["runs"]), 7)
        for r in doc["runs"]:
            self.assertEqual(r["rel"], history.rel_pair(r["id"], r["id"])[0], r["id"])
        self.assertTrue(all(r["rel"] is not None for r in doc["runs"][:3]))

    def test_rel_is_kept_while_the_history_is_the_same(self):
        """The list asks the same page again: check_rel is not counted twice for the same history (the content of
        the sample — values too); another check — counted again."""
        from kaskad import score as S
        for d in range(8, 0, -1):
            self.check(d * DAY_S, vals=self.vals())
        with mock.patch.object(S, "check_rel", wraps=S.check_rel) as m:
            a = self.ok("GET", "/api/host/bench")["runs"]
            n = m.call_count
            self.assertEqual(n, 8)
            self.assertEqual(self.ok("GET", "/api/host/bench")["runs"], a)
            self.ok("GET", "/api/host/bench/" + a[0]["id"])
            self.assertEqual(m.call_count, n)
            self.check(600, vals=self.vals(450.0))
            b = self.ok("GET", "/api/host/bench")["runs"]
            self.assertEqual(m.call_count, n + 9)
        self.assertEqual([r["rel"] for r in b[1:]], [r["rel"] for r in a])
        self.assertLess(b[0]["rel"], 100)
        smp = history.sample(self.now)
        k = history._rel_key(S, smp)
        smp["runs"][-1]["vals"]["net_down"]["v"] += 1                                  # the same ids and times
        self.assertNotEqual(history._rel_key(S, smp), k)


class DevRuleTest(BenchApiCase):
    """UI-C §4: POST /api/host/dev-rule — the threshold of ▲/▼ from the panel as ``host set --dev-threshold``."""

    def rule(self):
        return (state.load().get("gateway") or {}).get("hostmon", {}).get("dev")

    def test_writes_the_threshold_and_keeps_the_spread(self):
        with state.transaction() as st:
            st.setdefault("gateway", {}).setdefault("hostmon", {})["dev"] = {"pct": 10, "use_spread": False}
        doc = self.ok("POST", "/api/host/dev-rule", {"pct": 15})
        self.assertEqual(doc, {"ok": True, "dev_rule": {"pct": 15, "use_spread": False}, "changed": True})
        self.assertEqual(self.rule(), {"pct": 15, "use_spread": False})
        self.assertEqual(self.ok("POST", "/api/host/dev-rule", {"pct": 15})["changed"], False)
        for pct in (5, 30):
            self.assertEqual(self.ok("POST", "/api/host/dev-rule", {"pct": pct})["dev_rule"]["pct"], pct)
        self.assertEqual(self.ok("GET", "/api/host/score")["dev_rule"], {"pct": 30, "use_spread": False})

    def test_a_default_rule_gets_the_threshold(self):
        doc = self.ok("POST", "/api/host/dev-rule", {"pct": 20})
        self.assertEqual(doc["dev_rule"], {"pct": 20, "use_spread": True})
        self.assertEqual(self.rule(), {"pct": 20, "use_spread": True})

    def test_bad_bodies_are_400_and_write_nothing(self):
        self.ok("POST", "/api/host/dev-rule", {"pct": 12})
        before = self.state_bytes()
        bodies = [b"[12]", b'"12"', b"12", b"null", b"{", b"\xff", b"", b"{}", b'{"pct": 12, "use_spread": true}',
                  b'{"pct": 12, "x": 1}', b'{"pct": true}', b'{"pct": false}', b'{"pct": 10.0}', b'{"pct": 12.5}',
                  b'{"pct": "10"}', b'{"pct": null}', b'{"pct": 4}', b'{"pct": 31}', b'{"pct": -10}',
                  b'{"pct": 100000000000000000000000000000}', b'{"pct": NaN}', b'{"pct": 1e400}', b'{"pct": [10]}',
                  b'{"pct": {"v": 10}}', b'{"Pct": 10}']
        for body in bodies:
            r = self.call("POST", "/api/host/dev-rule", raw_body=body, headers={"Content-Type": "application/json"})
            self.assertEqual(r.status, 400, (body, r))
            doc = r.json()
            self.assertEqual((doc["ok"], doc["data"]), (False, {"field": "pct"}), body)
            self.assertEqual(doc["error"], "Порог отметок ▲/▼: pct — целое число процентов от 5 до 30, других полей "
                                           "в запросе нет.")
        self.assertEqual(self.state_bytes(), before)
        r = self.call("POST", "/api/host/dev-rule", raw_body=b'{"pct": 12}' + b" " * 300,
                      headers={"Content-Type": "application/json"})
        self.assertEqual(r.status, 413, r)
        self.assertEqual(self.state_bytes(), before)
        r = self.call("POST", "/api/host/dev-rule", {"pct": 12}, headers={"X-Kaskad-Lang": "en"})
        self.assertEqual(r.status, 200, r)
        r = self.call("POST", "/api/host/dev-rule", {"pct": 3}, headers={"X-Kaskad-Lang": "en"})
        self.assertEqual(r.json()["error"], "The ▲/▼ threshold: pct is a whole percent from 5 to 30, and the request "
                                            "has no other fields.")

    def test_wants_the_login_and_the_admin_header(self):
        self.ok("POST", "/api/host/dev-rule", {"pct": 12})
        before = self.state_bytes()
        self.assertEqual(self.call("POST", "/api/host/dev-rule", {"pct": 20}, tunnel=False).status, 401)
        self.assertEqual(self.call("POST", "/api/host/dev-rule", {"pct": 20}, csrf=False).status, 403)
        self.assertEqual(self.call("GET", "/api/host/dev-rule").status, 405)
        self.assertEqual(self.state_bytes(), before)

    def test_an_error_of_the_core_is_its_answer(self):
        from kaskad import errors, host_cli
        from kaskad.errors import KaskadError
        with mock.patch.object(host_cli, "apply_set", side_effect=KaskadError("занято", code=errors.LOCKED)):
            r = self.call("POST", "/api/host/dev-rule", {"pct": 12})
        self.assertEqual(r.status, 409, r)


def datetime_days_ago(now, n):
    import datetime
    y, m, d = (int(x) for x in tz.day(now).split("-"))
    return (datetime.date(y, m, d) - datetime.timedelta(days=n)).isoformat()


class ExportTest(HistoryCase):
    def test_csv_rows_skip_and_name(self):
        a = self.check(7200, inv={"cpu": "AMD EPYC node-secret-name", "nproc": 2})
        s = self.check(3600, status="skipped", why="busy", vals={})
        r = self.call("GET", "/api/host/bench/export?format=csv")
        self.assertEqual(r.status, 200, r)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertTrue(r.headers["content-type"].startswith("text/csv"))
        cd = r.headers["content-disposition"]
        self.assertRegex(cd, r'^attachment; filename="kaskad-bench-\d{8}-\d{8}\.csv"$')
        lines = r.text.splitlines()
        self.assertEqual(lines[0], ",".join(history.CSV_COLS))
        self.assertNotIn("score", lines[0].split(","))                   # NS-D34: no column of the score
        rows = [l.split(",") for l in lines[1:]]
        self.assertEqual(len([x for x in rows if x[0] == a]), 2)          # a row per test
        skip = [x for x in rows if x[0] == s]
        self.assertEqual(len(skip), 1)
        self.assertEqual(skip[0][4:10], ["skipped", "busy", "", "", "", ""])
        self.assertRegex(rows[0][1], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertNotIn("node-secret-name", r.text)

    def test_json_has_no_addresses_and_only_known_fields(self):
        self.check(600, inv={"cpu": "AMD EPYC node-secret-name", "nproc": 2})
        r = self.call("GET", "/api/host/bench/export?format=json&from=%s" % tz.day(self.now - 86400))
        self.assertEqual(r.status, 200, r)
        self.assertRegex(r.headers["content-disposition"], r'filename="kaskad-bench-\d{8}-\d{8}\.json"$')
        doc = r.json()
        self.assertEqual(doc["v"], 1)
        self.assertEqual(doc["tz"], tz.name())
        self.assertEqual(len(doc["runs"]), 1)
        self.assertNotIn("node-secret-name", r.text)
        run_keys = {"id", "ts", "end_ts", "mode", "by", "sched_id", "retry_of", "status", "why", "why_text", "rx",
                    "tx", "dur_s", "cpu_s", "under_load", "drops", "tests", "cost_real"}     # no score, links (NS-D34)
        self.assertLessEqual(set(doc["runs"][0]), run_keys)
        for t in doc["runs"][0]["tests"]:
            # B3 (REV-ns-b3-2 З-1; the coordinator 19.10): the mark «страна не определена» of test 8 — its code alone,
            # never its text (SS §3.4)
            self.assertLessEqual(set(t), {"test", "ts", "value", "unit", "mode", "ok", "why", "under_load",
                                          "value_corr", "people", "ca", "dns_src", "geo"})
            self.assertIn(t.get("geo"), (None, "unknown"))

    def test_the_unknown_country_goes_out_as_its_code_only(self):
        rid = self.check(600)
        path = os.path.join(spool.spool_dir(), rid + ".json")
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        doc["tests"].append({"test": "net_down", "ts": doc["run"]["ts"], "value": 640.0, "unit": "Mbit/s", "ok": 1,
                             "mode": "quick", "extra": {"geo": "unknown"}})
        spool.put(doc)
        self.assertIn("geo_text", [t for t in self.ok("GET", "/api/host/bench/" + rid)["tests"]
                                   if t["test"] == "net_down"][0])          # the screen has its text
        r = self.call("GET", "/api/host/bench/export?format=json&from=%s" % tz.day(self.now - 86400))
        t = [t for t in r.json()["runs"][0]["tests"] if t["test"] == "net_down"][0]
        self.assertEqual(t["geo"], "unknown")
        self.assertNotIn("geo_text", t)
        self.assertNotIn("geo_text", r.text)
        self.assertNotIn("Страна сервера", r.text)

    def test_name_has_no_node(self):
        r = self.call("GET", "/api/host/bench/export?format=csv&from=2026-01-05&to=2026-01-20")
        self.assertEqual(r.status, 200, r)
        self.assertIn('filename="kaskad-bench-20260105-20260120.csv"', r.headers["content-disposition"])
        self.assertEqual(r.text.splitlines(), [",".join(history.CSV_COLS)])


class CsvGuardTest(HistoryCase):
    def test_formula_cells_get_a_quote(self):
        runs_ = [{"id": "0000000001", "ts": 0, "mode": "quick", "by": "panel", "status": "ok", "why": "=cmd|' /C calc'!A0",
                  "tests": [{"test": "+x", "value": -5.0, "unit": "@u", "score": 7}]}]
        text = history.to_csv(runs_, MOSCOW)
        row = text.splitlines()[1]
        self.assertIn("'=cmd", row)
        self.assertIn("'+x", row)
        self.assertIn("'-5.0", row)
        self.assertIn("'@u", row)
        self.assertNotRegex(row, r"(^|,)[=+\-@]")
        self.assertTrue(row.startswith("0000000001,1970-01-01T03:00:00+03:00,"))

    def test_tab_and_cr_cells_get_a_quote(self):
        """REV-ns-b13 П-4 (M3): a cell starting with TAB or CR is a formula for some programs too."""
        runs_ = [{"id": "0000000001", "ts": 0, "mode": "quick", "by": "panel", "status": "ok", "why": "\tx",
                  "tests": [{"test": "\rx", "value": 1.0, "unit": "u", "score": 7}]}]
        text = history.to_csv(runs_, MOSCOW)
        self.assertIn("'\tx", text)
        self.assertIn("'\rx", text)


if __name__ == "__main__":
    import unittest
    unittest.main()


class StartCase(HistoryCase):
    """The fakes of «Проверить сервер» (no tests of its own — StartTest and PriceReadTest each run theirs once):
    ``self.plan`` — what apt would install, ``self.busy`` — another check running, ``self.started`` — what started."""

    def setUp(self):
        super().setUp()
        self.plan = {"missing": [], "available": True, "install": [], "kib": 0, "why": None}
        self.started, self.busy = [], []
        for p in (mock.patch("kaskad.sysrun.default_runner", lambda: self.runner),
                  mock.patch.object(run, "active", lambda runner: list(self.busy)),
                  mock.patch.object(pkgs, "plan", self.fake_plan),
                  mock.patch.object(run, "start", self.start)):
            p.start()
            self.addCleanup(p.stop)

    def start(self, mode, by, install=None, runner=None, **kw):
        self.started.append((mode, by, install))
        self.kw = kw
        return {"id": "0123456789", "unit": run.unit_name("0123456789"), "missing": []}

    def fake_plan(self, runner, names=None, update=False, by="cli", iperf=False):
        """As ``pkgs.plan``: the iperf3 of ``self.plan`` only with ``iperf`` (С2 REV-score-rename: the fake ignored it)."""
        p = dict(self.plan)
        for k in ("missing", "install"):
            p[k] = [x for x in p.get(k) or () if iperf or x != "iperf3"]
        return p

    def post(self, body):
        return self.call("POST", "/api/host/bench", body)

    def price(self, **kw):
        return self.ok("GET", "/api/host/bench/price", **kw)

    def key(self, mode="quick"):
        r = self.post({"mode": mode})
        self.assertEqual(r.status, 409, r)
        return r.json()


class StartTest(StartCase):
    """6C «Проверить сервер» — POST /api/host/bench (BRIEF-P4-a): the price and the key of consent as MCP
    ``host_bench``, the limits of a day as the schedule; nothing starts or is installed without the key of THIS price."""

    def test_no_key_is_a_refusal_with_the_price(self):
        doc = self.key("full")
        self.assertEqual(doc["code"], "need_confirm")
        self.assertEqual(set(doc["price"]), {"rx_mb", "tx_mb", "cpu_min", "basis", "max_min", "confirm"})
        self.assertGreater(doc["price"]["rx_mb"], 1000)                  # a full one — gigabytes
        self.assertIn("missing", doc["pkgs"])
        self.assertTrue(doc["error"])
        self.assertEqual(self.started, [])

    def test_the_key_of_this_price_starts_it_from_the_panel(self):
        k = self.key()["price"]["confirm"]
        self.assertEqual(k, HB.price(["quick"])["modes"]["quick"]["confirm"])     # the same key as MCP host_bench
        doc = self.ok("POST", "/api/host/bench", {"mode": "quick", "confirm": k})
        self.assertEqual((doc["id"], doc["mode"], doc["price"]["confirm"]), ("0123456789", "quick", k))
        self.assertEqual(self.started, [("quick", "panel", None)])

    def test_the_upload_in_russia_is_on_by_default_in_a_full_check(self):
        """Владелец 29.10 (D-144): a full check of a node that may be in Russia measures the upload to the iperf3
        points of the Russian providers unless the tick is off (``iperf`` false); the quick one never; the window gets
        the question of 8b (``upload_ru``) with its line «точка увидит адрес» only for a node that may be in Russia, and
        iperf3 is among the programs of its price; abroad — no question, no iperf3."""
        for ru in (None, True):
            with mock.patch.object(HB, "country_offline", lambda: ru):
                doc = self.price()
                self.assertIn("увидит адрес этого сервера", doc["upload_ru"]["text"], ru)
                self.assertEqual([c[0] for c in doc["upload_ru"]["choices"]], [1, 2])
                full = doc["modes"]["full"]["confirm"]
                for body, on in (({}, True), ({"iperf": True}, True), ({"iperf": False}, False)):
                    self.started[:] = []
                    got = self.ok("POST", "/api/host/bench", dict(body, mode="full", confirm=full))
                    self.assertEqual((self.kw["iperf"], got["upload_ru"]), (on, on), (ru, body))   # без правки: KeyError
                quick = doc["modes"]["quick"]["confirm"]
                self.ok("POST", "/api/host/bench", {"mode": "quick", "confirm": quick, "iperf": True})
                self.assertFalse(self.kw["iperf"])                                # the quick kind never runs 8b
        seen = []
        with mock.patch.object(HB, "country_offline", lambda: False), \
                mock.patch.object(pkgs, "plan", lambda runner, names=None, update=False, by="cli", iperf=False:
                                  seen.append(iperf) or self.fake_plan(runner, iperf=iperf)):
            self.assertIsNone(self.price()["upload_ru"])                          # abroad: the upload goes over HTTP
        self.assertEqual(seen, [False])                                           # and iperf3 is not in the price
        with mock.patch.object(HB, "country_offline", lambda: None), \
                mock.patch.object(pkgs, "plan", lambda runner, names=None, update=False, by="cli", iperf=False:
                                  seen.append(iperf) or self.fake_plan(runner, iperf=iperf)):
            self.price()
        self.assertEqual(seen, [False, False, True])     # С2: the programs of the tests, then those of the upload on

    def test_iperf3_only_when_8b_goes(self):
        """С2 REV-score-rename: the window asks iperf3 only for a full check with the upload on (``upload_ru.pkgs``);
        the quick kind and the tick off start without ``install`` and never install it."""
        self.plan = {"missing": ["iperf3"], "available": True, "install": ["iperf3"], "kib": 330, "why": None}
        with mock.patch.object(HB, "country_offline", lambda: True):
            doc = self.price()
            self.assertEqual(doc["pkgs"]["missing"], [])
            self.assertEqual(doc["upload_ru"]["pkgs"]["missing"], ["iperf3"])
            quick, full = doc["modes"]["quick"]["confirm"], doc["modes"]["full"]["confirm"]
            self.ok("POST", "/api/host/bench", {"mode": "quick", "confirm": quick})   # без правки: 409 need_install
            self.assertEqual((self.started[-1], self.kw["with_iperf3"]), (("quick", "panel", None), False))
            self.ok("POST", "/api/host/bench", {"mode": "full", "confirm": full, "iperf": False})
            self.assertEqual((self.started[-1], self.kw["with_iperf3"]), (("full", "panel", None), False))
            r = self.post({"mode": "full", "confirm": full})
            self.assertEqual((r.status, r.json()["code"], r.json()["pkgs"]["missing"]), (409, "need_install", ["iperf3"]))
            self.ok("POST", "/api/host/bench", {"mode": "full", "confirm": full, "install": True})
            self.assertEqual((self.started[-1], self.kw["with_iperf3"]), (("full", "panel", True), True))

    def test_packages_of_the_key_and_the_answer_no(self):
        """З-1 REV-ns-p4a: the programs — a consent of their own: without ``install`` — 409 ``need_install`` with the
        question, nothing installed or started; true — installed; false — the answer «нет»."""
        self.plan = {"missing": ["sysbench", "fio"], "available": True, "install": [], "kib": 900, "why": None}
        k = self.key()["price"]["confirm"]
        r = self.post({"mode": "quick", "confirm": k})
        self.assertEqual((r.status, r.json()["code"]), (409, "need_install"))
        self.assertEqual(r.json()["pkgs"]["missing"], ["sysbench", "fio"])
        self.assertEqual(r.json()["price"]["confirm"], k)
        self.assertTrue(r.json()["error"])
        self.assertEqual(self.started, [])
        self.ok("POST", "/api/host/bench", {"mode": "quick", "confirm": k, "install": True})
        self.ok("POST", "/api/host/bench", {"mode": "quick", "confirm": k, "install": False})
        self.assertEqual(self.started, [("quick", "panel", True), ("quick", "panel", False)])

    def test_install_is_not_asked_when_nothing_to_install(self):
        """Nothing to install (or apt does not know them) — ``install`` is not needed and not passed on."""
        k = self.key()["price"]["confirm"]
        self.ok("POST", "/api/host/bench", {"mode": "quick", "confirm": k, "install": True})
        self.plan = {"missing": ["fio"], "available": False, "install": [], "kib": None, "why": "not_found"}
        k = self.key()["price"]["confirm"]
        self.ok("POST", "/api/host/bench", {"mode": "quick", "confirm": k})
        self.assertEqual(self.started, [("quick", "panel", None), ("quick", "panel", None)])

    def test_another_key_or_another_price_starts_nothing(self):
        k = self.key()["price"]["confirm"]
        full = self.key("full")["price"]["confirm"]
        r = self.post({"mode": "quick", "confirm": full})                # the key of another mode
        self.assertEqual((r.status, r.json()["code"]), (409, "confirm_changed"))
        self.assertEqual(r.json()["price"]["confirm"], k)               # the new price comes with it
        self.plan = {"missing": ["sysbench"], "available": True, "install": [], "kib": 500, "why": None}
        r = self.post({"mode": "quick", "confirm": k})                   # the packages to install changed
        self.assertEqual((r.status, r.json()["code"]), (409, "confirm_changed"))
        self.assertEqual(self.started, [])

    def test_strict_body(self):
        k = self.key()["price"]["confirm"]
        for body in ({"mode": "slow"}, {"mode": "quick", "confirm": k.upper()}, {"mode": "quick", "confirm": 12345678},
                     {"mode": "quick", "confirm": k, "install": "yes"}, {"mode": "quick", "confirm": k, "iperf": "yes"},
                     {"confirm": k}, ["quick"], {"mode": "quick", "<b>x</b>": 1}):
            r = self.post(body)
            self.assertEqual(r.status, 400, (body, r))
            self.assertNotIn("<b>", r.json()["error"])
        r = self.call("POST", "/api/host/bench", {"mode": "quick", "confirm": k, "pad": "x" * 2000})
        self.assertEqual(r.status, 413)
        self.assertEqual(self.started, [])

    def test_another_check_runs(self):
        self.busy = ["fedcba9876"]
        r = self.post({"mode": "quick", "confirm": "0" * 8})
        self.assertEqual((r.status, r.json()["code"], r.json()["id"]), (409, "running", "fedcba9876"))
        self.assertEqual(self.started, [])

    def test_the_limits_of_a_day(self):
        for i in range(HB.PANEL_FULL_PER_DAY):
            self.check(9 - i, mode="full", score=700)
        self.check(6, status="skipped", why="busy")                     # a skip is not a check
        self.check(5, by="schedule", mode="full", score=700)            # nor the schedule's
        r = self.post({"mode": "full"})                                   # the limit first: no price to look at
        self.assertEqual((r.status, r.json()["code"]), (429, "limit"))
        self.assertEqual(r.json()["today"], {"all": 2, "full": 2})
        self.assertIn("полных", r.json()["error"])
        self.ok("POST", "/api/host/bench", {"mode": "quick", "confirm": self.key()["price"]["confirm"]})
        for i in range(HB.PANEL_PER_DAY - HB.PANEL_FULL_PER_DAY):
            self.check(4, mode="quick", score=600)
        r = self.post({"mode": "quick"})
        self.assertEqual((r.status, r.json()["code"]), (429, "limit"))
        self.assertEqual(self.started, [("quick", "panel", None)])

    def test_the_day_of_the_limits_is_the_nodes_day(self):
        """З-2 REV-ns-p4a: the day of the limits — the day of the node (KASKAD_TZ), not UTC: at 00:30 by Moscow
        (21:30 UTC of the day before) the checks of 23:50 by Moscow are yesterday's, the ones of 00:10 — today's."""
        midnight = (self.now + 3 * 3600) // DAY_S * DAY_S - 3 * 3600 - DAY_S     # yesterday 00:00 by Moscow
        now = midnight + 30 * 60
        self.now = now
        for _ in range(4):
            self.check(40 * 60, mode="quick", score=600)                  # 23:50 by Moscow — yesterday
        for _ in range(2):
            self.check(20 * 60, mode="full", score=700)                   # 00:10 — today
        with mock.patch.object(tz, "zone", lambda: MOSCOW):
            with self.assertRaises(host_sched.Refused) as c:
                HB.panel_start({"mode": "full"}, runner=self.runner, now=now)
            self.assertEqual((c.exception.status, c.exception.key), (429, "limit"))
            self.assertEqual(c.exception.more["today"], {"all": 2, "full": 2})
            with self.assertRaises(host_sched.Refused) as c:             # a quick one: no limit, the price
                HB.panel_start({"mode": "quick"}, runner=self.runner, now=now)
            self.assertEqual((c.exception.status, c.exception.key), (409, "need_confirm"))
        self.assertEqual(self.started, [])


class PriceReadTest(StartCase):
    """T-3377 Н-15: the window of the price reads GET /api/host/bench/price — 200 with what POST without a key refused
    with (two 409 per opening were errors in the console): the price and the key of each mode (the same keys as POST
    and MCP), the packages, the limit of a day, another check running. Reads only: nothing starts, nothing is
    written; the login and no CSRF header (a GET)."""

    def price(self, **kw):
        return self.ok("GET", "/api/host/bench/price", **kw)

    def test_the_prices_and_the_keys(self):
        self.plan = {"missing": ["sysbench", "fio"], "available": True, "install": ["fio", "sysbench"], "kib": 900,
                     "why": None}
        before = self.state_bytes()
        doc = self.price()
        self.assertIsNone(doc["running"])
        self.assertEqual(doc["limit"], {})
        self.assertEqual(set(doc["modes"]), {"quick", "full"})
        for m in ("quick", "full"):
            self.assertEqual(doc["modes"][m]["confirm"], self.key(m)["price"]["confirm"])     # the key POST wants
        self.assertEqual(doc["pkgs"]["missing"], ["sysbench", "fio"])
        self.assertEqual(self.started, [])
        self.assertEqual(self.state_bytes(), before)                       # a read: nothing written
        k = doc["modes"]["quick"]["confirm"]
        self.ok("POST", "/api/host/bench", {"mode": "quick", "confirm": k, "install": True})
        self.assertEqual(self.started, [("quick", "panel", True)])

    def test_the_limit_of_a_day_and_a_running_check(self):
        for i in range(HB.PANEL_FULL_PER_DAY):
            self.check(9 - i, mode="full", score=700)
        doc = self.price()
        self.assertEqual(set(doc["modes"]), {"quick"})                    # over the limit — no price, as POST
        self.assertEqual(doc["limit"]["full"]["today"], {"all": 2, "full": 2})
        self.assertIn("полных", doc["limit"]["full"]["text"])
        self.busy = ["fedcba9876"]
        self.assertEqual(self.price(), {"ok": True, "running": "fedcba9876", "modes": {}, "limit": {}, "pkgs": None,
                                        "upload_ru": None})
        self.assertEqual(self.started, [])

    def test_login_strict_query_and_not_a_check_id(self):
        r = self.call("GET", "/api/host/bench/price", tunnel=False)
        self.assertEqual(r.status, 401, r)
        r = self.call("GET", "/api/host/bench/price", csrf=False)
        self.assertEqual(r.status, 200, r)                                 # a read: no X-Kaskad-Admin needed
        r = self.call("GET", "/api/host/bench/price?mode=full")
        self.assertEqual(r.status, 400, r)
        from kaskad.panel import routes
        self.assertIs(routes.find("GET", "/api/host/bench/price")[0].handler, H.bench_price)

    def counted(self):
        """HB.price counted: the list of the modes of each count (the commands of apt run inside it)."""
        calls, orig = [], HB.price

        def counting(modes, runner=None):
            calls.append(tuple(modes))
            return orig(modes, runner)
        p = mock.patch.object(HB, "price", counting)
        p.start()
        self.addCleanup(p.stop)
        return calls

    def test_a_foreign_page_is_refused(self):
        """REV-3377-stand-fix 1: the read runs commands (systemctl, apt-get -s, apt-cache) — over the SSH tunnel an
        admin needs no cookie, so a foreign page is refused as for a change: Sec-Fetch-Site cross-site, a foreign
        Origin, «null» (the panel's own page sends no Origin on a GET) -> 403, nothing counted; HEAD — the same."""
        calls = self.counted()
        own = "http://127.0.0.1:%d" % self.port
        for h in ({"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Referer": "https://evil.example/"},
                  {"Origin": "https://evil.example"}, {"Origin": "null"}, {"Origin": own + ".evil.example"},
                  {"Origin": own, "Sec-Fetch-Site": "cross-site"}):
            r = self.call("GET", "/api/host/bench/price", headers=h)
            self.assertEqual(r.status, 403, (h, r))
            self.assertIn("чужой страницы", r.json()["error"])
        self.assertEqual(self.call("HEAD", "/api/host/bench/price", headers={"Sec-Fetch-Site": "cross-site"}).status, 403)
        self.assertEqual(calls, [])
        for h in ({"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {"Sec-Fetch-Site": "same-origin", "Origin": own}, {}):
            self.assertEqual(self.call("GET", "/api/host/bench/price", headers=h).status, 200, h)
        self.assertEqual(calls, [("quick", "full")] * 4)
        r = self.call("POST", "/api/host/bench", {"mode": "quick"}, headers={"Origin": "null"})
        self.assertEqual(r.status, 409, r)            # a change keeps its rule: «null» passes, X-Kaskad-Admin guards it

    def flight(self, fail=None):
        """Two GET at once while apt «thinks»: the first counts, the second comes in while it counts (H._PRICE n=2),
        then apt answers. -> (the two answers, the counts)."""
        calls = self.counted()
        entered, release = threading.Event(), threading.Event()
        plan = dict(self.plan)

        def slow(runner, names=None, update=False, by="cli", iperf=False):
            entered.set()
            self.assertTrue(release.wait(10))
            if fail:
                raise fail
            return dict(plan)
        out = []
        with mock.patch.object(pkgs, "plan", slow):
            ts = [threading.Thread(target=lambda: out.append(self.call("GET", "/api/host/bench/price")))]
            ts[0].start()
            try:
                self.assertTrue(entered.wait(10))
                ts.append(threading.Thread(target=lambda: out.append(self.call("GET", "/api/host/bench/price"))))
                ts[1].start()
                deadline = time.monotonic() + 5
                while (H._PRICE["flight"] or {}).get("n", 0) < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)                     # a condition with a deadline, not a pause
                self.assertEqual((H._PRICE["flight"] or {}).get("n"), 2, "второй запрос не ждёт идущий расчёт")
            finally:
                release.set()
                for t in ts:
                    t.join(10)
        self.assertEqual(len(out), 2)
        self.assertIsNone(H._PRICE["flight"])
        return out, calls

    def test_one_count_at_a_time(self):
        """REV-3377-stand-fix 1: one count of the price at a time — a second request while it counts waits for it and
        gets its answer, no second round of commands; the next request after it counts anew."""
        self.plan = {"missing": ["fio"], "available": True, "install": ["fio"], "kib": 400, "why": None}
        out, calls = self.flight()
        self.assertEqual([r.status for r in out], [200, 200])
        self.assertEqual(out[0].json(), out[1].json())
        self.assertEqual(out[0].json()["pkgs"]["missing"], ["fio"])
        self.assertEqual(calls, [("quick", "full")])                     # one count for the two
        self.price()
        self.assertEqual(len(calls), 2)                                  # after it — a new count

    def test_an_error_of_the_count_is_both_answers(self):
        """The count fails — both requests get its error (500, details to the journal), the next one counts anew."""
        from kaskad.panel import server
        logged = []
        with mock.patch.object(server, "log_t", lambda key, **kw: logged.append(key)):
            out, calls = self.flight(fail=RuntimeError("apt"))
        self.assertEqual([r.status for r in out], [500, 500])
        self.assertEqual(logged, ["journal.srv.handler_error"] * 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(self.price()["modes"]), {"quick", "full"})
