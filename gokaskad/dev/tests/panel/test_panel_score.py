"""B6 (k3/handoff/SPEC-node-score.md §6.2, SPEC-node-score-schedule-share.md §4; the cut of 23.10, NS-D30–NS-D34):
GET /api/host/score and /api/host/day/<YYYY-MM-DD> on the fake node — the contract of the fields (``summary`` with
``rel_total``, the links with the raw numbers and ``rel``; no absolute score, band, capacity, «годится для…», budget of
the month, no /api/host/curves), an empty history, the node against its own history, the first day, stale, a new base, a
day without data, foreign input in the date, the login."""

import json
import statistics
import time
from unittest import mock

from kaskad import score as S
from kaskad import tz
from kaskad.bench import hostscore as HS
from kaskad.bench import spool
from kaskad.gw import metrics
from tests.panel.panelcase import PanelCase

DAY = 86400
SPEC = S.load_spec()
LINK_IDS = [b["id"] for b in SPEC["links"]]
# the raw values of the sample S4 (k3/handoff/data/node-score-samples; tests/fixtures/score_samples.json) — a whole
# check of a fast node, here only as the values of the tests (no score of them is counted any more)
S4 = {"aes_1t": 7000, "chacha_1t": 2400, "cpu_1t": 2700, "cpu_canary_cv": 1.5, "cpu_cv": 1.2, "cpu_mt": 10500,
      "cpu_sustain": 0.99, "disk_fsync": 0.15, "disk_rr": 130000, "disk_rw": 85000, "dns": 5,
      "jitter": {"n": 17280, "v": 0.25},
      "loss": {"lo": 0.0015905403461511298, "n": 25920.0, "v": 0.01,
               "points": [{"e": 0.864, "k": 0.864, "n": 8640.0}] * 3},
      "mem_bw": 20000, "mem_gb": 8, "net_cv": 4, "net_down": 1700, "net_up": 1300, "rtt": 2.5, "steal_p95": 0.1}
SUMMARY_KEYS = {"rel_total", "rel_n", "rel_need", "last_id", "last_at", "fresh_from", "first", "stale", "checked",
                "retry", "base_since"}
DOC_KEYS = {"ok", "charts", "summary", "dev_rule", "links", "provisional", "rows", "stab", "days", "series"}
LINK_KEYS = set(HS.LINK_FIELDS) | {"hint"}
GONE = {"score", "capacity", "fit_for", "offline_budget"}          # NS-D34: never in the answer


class ScoreCase(PanelCase):
    def setUp(self):
        super().setUp()
        HS.reset()
        self.addCleanup(HS.reset)
        if not metrics.available():
            self.skipTest("no sqlite3")
        w = metrics.open_writer()                    # the base of SCHEMA 3, as the collector opens it
        w.close()
        self.now = int(time.time())
        self.n = 0

    def check(self, ago, vals=None, inv=None, **head):
        self.n += 1
        h = {"id": "%010x" % self.n, "ts": self.now - int(ago), "mode": "full", "by": "panel", "status": "ok"}
        h.update(head)
        if inv is not None:
            h["inv"] = inv
        spool.put(spool.check_doc(h, S.with_mode(dict(S4 if vals is None else vals), "full")))
        return h["id"]

    def probes(self, hours=72, lost=0, anchors=("a", "b", "c")):
        """hours of host_ping_h1 back from now: the node row and three anchors; host_h1 with n."""
        w = metrics.open_writer()
        try:
            with w.conn:
                for i in range(1, hours + 1):
                    ts = (self.now // 3600 - i) * 3600
                    for a in anchors + ("*",):
                        w.conn.execute("INSERT OR REPLACE INTO host_ping_h1 (anchor, ts, n, lost, runs, rtt_med, rtt_p90,"
                                       " jit, jit_n) VALUES (?,?,?,?,?,?,?,?,?)", (a, ts, 1080, lost, 1 if lost else 0,
                                                                                  30.0, 40.0, 1.5, 1000))
                    w.conn.execute("INSERT OR REPLACE INTO host_h1 (ts, n, busy, busy_max, steal, warn_min, alarm_min)"
                                   " VALUES (?,?,?,?,?,?,?)", (ts, 60, 10.0, 30.0, 0.2, 0, 0))
        finally:
            w.close()

    def m10(self, hours, canary=lambda i: 50.0, steal=lambda i: 0.2, busy=10.0):
        """host_m10 back from now (6 rows an hour, i — the number of the row from the newest): the canary (None — not
        run), steal and busy of the same row (C6: the canary by the 10-min samples, REV-ns-c6 З-2)."""
        w = metrics.open_writer()
        try:
            with w.conn:
                for i in range(1, hours * 6 + 1):
                    w.conn.execute("INSERT OR REPLACE INTO host_m10 (ts, n, busy, steal, canary_ms) VALUES "
                                   "(?,?,?,?,?)", ((self.now // 600 - i) * 600, 10, busy, steal(i), canary(i)))
        finally:
            w.close()

    def score(self):
        return self.ok("GET", "/api/host/score")


class ScoreTest(ScoreCase):
    def test_login_and_get_only(self):
        for path in ("/api/host/score", "/api/host/day/2026-01-01"):
            self.assertEqual(self.call("GET", path, tunnel=False).status, 401, path)
            for method in ("POST", "PUT", "DELETE"):
                self.assertEqual(self.call(method, path, {}).status, 405, (method, path))

    def test_empty_history(self):
        doc = self.score()
        self.assertEqual(set(doc), DOC_KEYS)
        self.assertFalse(GONE & set(doc))                                             # NS-D34
        st = doc["stab"]                                                              # C6: nothing measured yet
        self.assertEqual((st["steal_p95_24h"], st["steal_p95_7d"], st["offline_min_24h"], st["offline_min_7d"]),
                         (None, None, 0, 0))
        self.assertEqual(st["tests"]["cpu_canary_cv"]["why"], "short_history")
        self.assertEqual(st["tests"]["cpu_canary_cv"]["why_text"], "ждёт истории: нужны сутки наблюдений")
        self.assertEqual(st["tests"]["net_cv"]["why"], "few_tests")
        sm = doc["summary"]
        self.assertEqual(set(sm), SUMMARY_KEYS)
        self.assertEqual((sm["rel_total"], sm["rel_n"], sm["rel_need"], sm["last_id"], sm["last_at"]),
                         (None, 0, 3, None, None))
        self.assertEqual([lk["id"] for lk in doc["links"]], LINK_IDS)               # the links of the catalogue
        for lk in doc["links"]:
            self.assertEqual(set(lk), LINK_KEYS, lk["id"])
            self.assertEqual((lk["rel"], lk["usual_n"]), (None, 0), lk["id"])
        self.assertEqual(doc["rows"], {"people": None, "load": None})
        self.assertEqual(doc["dev_rule"], S.DEV_RULE)
        self.assertEqual(len(doc["days"]), 30)
        self.assertEqual(doc["days"][-1]["day"], tz.day(self.now))
        self.assertTrue(all(d["color"] == "grey" and d["reason"]["code"] == "no_data" and d["reason"]["msg"]
                            for d in doc["days"]))
        self.assertTrue(all("score" not in d for d in doc["days"]))                   # days[].score is gone

    def test_one_check_has_nothing_to_compare_with(self):
        """NS-D30: one check — the raw values, no «обычно» (usual_n < 3), so no rel of any link and no rel_total."""
        rid = self.check(3600)
        doc = self.score()
        sm = doc["summary"]
        self.assertEqual((sm["last_id"], sm["rel_total"], sm["rel_n"]), (rid, None, 0))
        links = dict((lk["id"], lk) for lk in doc["links"])
        self.assertTrue(all(lk["rel"] is None for lk in links.values()))
        down = links["down"]
        self.assertEqual((down["test"], down["fresh_raw"], down["unit"], down["usual_raw"]),
                         ("net_down", 1700.0, "Мбит/с", None))
        self.assertIsNone(links["net_bw"]["test"])                                    # a group: no one test …
        self.assertEqual([(p["test"], p["fresh_raw"]) for p in links["net_bw"]["parts"]],   # … its parts
                         [("net_down", 1700.0), ("net_up", 1300.0)])

    def test_rel_against_its_own_history(self):
        """NS-D30, NS-D31: six usual checks, then three with half the download — «down» is 50 of usual (850 against
        1700), the group of the channel the median of its tests, the total the median of the links (100 — as usual)."""
        for d in range(10, 4, -1):
            self.check(d * DAY)
        for d in (3, 2, 1):
            self.check(d * DAY - 3600, dict(S4, net_down=850))
        doc = self.score()
        links = dict((lk["id"], lk) for lk in doc["links"])
        down = links["down"]
        self.assertEqual((down["rel"], down["fresh_raw"], down["usual_raw"], down["usual_n"], down["dev"]),
                         (50, 850.0, 1700.0, 6, "down"))
        self.assertEqual((links["up"]["rel"], links["net_bw"]["rel"]), (100, 75))
        self.assertEqual([p["rel"] for p in links["net_bw"]["parts"]], [50, 100])
        rels = [lk["rel"] for lk in doc["links"] if lk["rel"] is not None]
        self.assertEqual(doc["summary"]["rel_n"], len(rels))
        self.assertEqual(doc["summary"]["rel_total"], S.rel_total(rels))
        self.assertEqual(doc["summary"]["rel_total"], 100)
        self.assertEqual(json.loads(json.dumps(doc)), doc)

    def test_first_day_and_after(self):
        self.check(3 * 3600)
        self.assertIs(self.score()["summary"]["first"], True)
        self.n = 0
        import shutil
        shutil.rmtree(spool.spool_dir(), ignore_errors=True)
        rid = self.check(25 * 3600)
        doc = self.score()
        self.assertIs(doc["summary"]["first"], False)
        self.assertEqual(doc["summary"]["last_id"], rid)
        self.assertEqual(next(lk for lk in doc["links"] if lk["id"] == "down")["fresh_raw"], 1700.0)

    def test_stale(self):
        rid = self.check(46 * DAY)
        doc = self.score()
        self.assertIs(doc["summary"]["stale"], True)
        self.assertEqual(doc["summary"]["last_id"], rid)                              # «последняя проверка <дата>»
        self.assertIsNone(doc["summary"]["rel_total"])
        self.assertIsNone(next(lk for lk in doc["links"] if lk["id"] == "down")["fresh_raw"])   # not fresh (45 d)

    def test_new_base(self):
        inv = {"cpu": "AMD EPYC", "nproc": 2, "mem_gb": 2.0}
        self.check(3 * DAY, inv=inv)
        self.check(DAY, inv=dict(inv, cpu="Intel Xeon"))
        doc = self.score()
        self.assertEqual([b["kind"] for b in doc["summary"]["base_since"]], ["cpu"])
        self.assertEqual(doc["summary"]["base_since"][0]["day"], tz.day(self.now - DAY))

    def test_the_stability_of_the_history(self):
        """C6: steal p95 over 24 h / 7 d, the canary of host_m10, the spread of the channel of the checks, minutes
        without network of node_offline / node_online; a container — no steal; the strip for «Поделиться»."""
        for i, (ago, down) in enumerate(((3 * DAY, 300), (2 * DAY + 3 * 3600, 400), (DAY, 500))):
            self.check(ago, dict(S4, net_down=down))
        self.probes(hours=8 * 24)
        self.m10(8 * 24, canary=lambda i: 50.0 if i % 2 else 60.0,                    # the canary jumps 50 / 60 ms
                 steal=lambda i: 3.0 if 25 <= i <= 36 else 0.2)                       # 2 hours of steal 3 %
        w = metrics.open_writer()
        try:
            with w.conn:
                w.conn.execute("UPDATE host_h1 SET canary_ms = 1000")                 # hourly means never count
                for ts, key in ((self.now - 7200, "node_offline"), (self.now - 6600, "node_online"),
                                (self.now - 3 * DAY, "node_offline"), (self.now - 3 * DAY + 1200, "node_online")):
                    w.conn.execute("INSERT INTO events VALUES (?, ?, NULL, '{}')", (ts, key))
        finally:
            w.close()
        doc = self.score()
        st = doc["stab"]
        self.assertEqual((st["steal_p95_24h"], st["steal_p95_7d"]), (3.0, 0.2))
        self.assertEqual((st["offline_min_24h"], st["offline_min_7d"]), (10.0, 30.0))
        cv = st["tests"]["cpu_canary_cv"]
        self.assertAlmostEqual(cv["v"], 100 * statistics.stdev([50.0, 60.0] * 72) / 55.0, places=3)
        self.assertIsNone(cv["why_text"])
        self.assertEqual((st["tests"]["net_cv"]["v"], st["tests"]["net_cv"]["n"]), (25.0, 3))
        self.assertEqual(st["tests"]["steal_p95"]["why"], None)
        self.assertEqual(json.loads(json.dumps(doc["stab"])), doc["stab"])
        self.assertEqual(HS.share_days(doc["days"])[-1], {"day": doc["days"][-1]["day"],
                                                          "color": doc["days"][-1]["color"], "test": False,
                                                          "partial": doc["days"][-1]["partial"]})
        self.assertEqual(set(HS.share_days(doc["days"])[0]), {"day", "color", "test", "partial"})
        self.check(3600, dict(S4, net_down=450), inv={"virt": "lxc", "nproc": 2})       # now a container
        HS.reset()
        st = self.score()["stab"]
        self.assertEqual((st["steal_p95_24h"], st["steal_p95_7d"]), (None, None))
        self.assertEqual(st["tests"]["steal_p95"]["why"], "container")
        self.assertIn("контейнере", st["tests"]["steal_p95"]["why_text"])

    def steal_hidden(self, hours):
        """steal 0.1 % over ``hours`` while the canary jumps (CV ≈ 22 %) -> stab of /api/host/score."""
        self.probes(hours=hours)
        self.m10(hours, canary=lambda i: (40.0, 50.0, 60.0, 70.0)[i % 4], steal=lambda i: 0.1)
        return self.score()["stab"]

    def test_hidden_steal_through_the_panel(self):
        """REV-ns-c6 С-2, З-3 (M14, M15): steal 0.1 % over 8 d while the canary jumps → steal_p95 «hidden», the numbers
        of steal null."""
        st = self.steal_hidden(8 * 24)
        self.assertGreater(st["tests"]["cpu_canary_cv"]["v"], S.HIDDEN_CANARY_CV)
        self.assertEqual(st["tests"]["steal_p95"]["why"], "hidden")
        self.assertIn("скрывает", st["tests"]["steal_p95"]["why_text"])
        self.assertEqual((st["steal_p95_24h"], st["steal_p95_7d"]), (None, None))

    def test_no_hidden_steal_before_7_days(self):
        """С-2 (RS 3.4: 7 days): the same rows over 6 d — not «hidden», steal_p95_7d null (the rows are not 7 d)."""
        st = self.steal_hidden(6 * 24)
        self.assertGreater(st["tests"]["cpu_canary_cv"]["v"], S.HIDDEN_CANARY_CV)
        self.assertEqual(st["tests"]["steal_p95"]["why"], None)
        self.assertEqual((st["steal_p95_24h"], st["steal_p95_7d"]), (0.1, None))

    def test_no_hidden_steal_and_no_canary_after_12_hours(self):
        """С-2, З-1: 12 h of history — the canary waits for a day, steal stays."""
        st = self.steal_hidden(12)
        self.assertEqual(st["tests"]["cpu_canary_cv"]["why"], "short_history")
        self.assertIsNone(st["tests"]["cpu_canary_cv"]["v"])
        self.assertEqual(st["tests"]["steal_p95"]["why"], None)
        self.assertEqual((st["steal_p95_24h"], st["steal_p95_7d"]), (0.1, None))

    def test_the_canary_with_the_probes_off(self):
        """REV-ns-c6-2 З-1: the probes off (an updated node) — the canary of 3 days still has its value; the server
        busy (rows of host_m10 without a canary) — «не выполнялась», not «ждёт истории» for ever."""
        self.m10(3 * 24, canary=lambda i: 50.0 if i % 2 else 60.0)
        cv = self.score()["stab"]["tests"]["cpu_canary_cv"]
        self.assertAlmostEqual(cv["v"], 100 * statistics.stdev([50.0, 60.0] * 72) / 55.0, places=3)
        HS.reset()
        w = metrics.open_writer()
        try:
            with w.conn:
                w.conn.execute("UPDATE host_m10 SET canary_ms = NULL")
        finally:
            w.close()
        cv = self.score()["stab"]["tests"]["cpu_canary_cv"]
        self.assertEqual((cv["v"], cv["why"]), (None, "no_canary"))

    def test_rows_of_steal_without_a_value_are_not_7_days(self):
        """REV-ns-c6-2 П-1 (N10): rows of host_m10 with steal NULL older than 6 d do not stretch steal to «7 days»."""
        self.probes(hours=8 * 24)
        self.m10(8 * 24, canary=lambda i: (40.0, 50.0, 60.0, 70.0)[i % 4],
                 steal=lambda i: 0.1 if i <= 6 * 24 * 6 else None)
        st = self.score()["stab"]
        self.assertGreater(st["tests"]["cpu_canary_cv"]["v"], S.HIDDEN_CANARY_CV)
        self.assertEqual(st["tests"]["steal_p95"]["why"], None)
        self.assertIsNone(st["steal_p95_7d"])

    def test_new_base_through_an_unknown_model(self):
        """REV-ns-b6 З-2: the queue without the collector (bench.history) — A(EPYC) → B(no model) → C(Xeon) is «новая
        база» of the CPU at C."""
        inv = {"cpu": "AMD EPYC", "nproc": 2, "mem_gb": 2.0}
        self.check(4 * DAY, inv=inv)
        self.check(3 * DAY, inv={"nproc": 2, "mem_gb": 2.0})
        self.check(DAY, inv=dict(inv, cpu="Intel Xeon"))
        doc = self.score()
        self.assertEqual([(b["kind"], b["day"]) for b in doc["summary"]["base_since"]],
                         [("cpu", tz.day(self.now - DAY))])

    def test_probes_feed_the_links_of_the_probes(self):
        self.check(3600, {k: v for k, v in S4.items() if k not in ("loss", "rtt", "jitter", "steal_p95")})
        self.probes()
        doc = self.score()
        self.assertIs(doc["charts"], True)
        loss = next(lk for lk in doc["links"] if lk["id"] == "loss")
        self.assertEqual(loss["source"], "probes")
        self.assertIsNotNone(loss["fresh_raw"])
        self.assertEqual(loss["test"], "loss")
        self.assertEqual(len(doc["summary"]["fresh_from"]["days"]), 3)
        self.assertEqual(doc["days"][-2]["color"], "green")

    def test_people_row(self):
        w = metrics.open_writer()
        try:
            w.write_people_hour((self.now // 3600 - 1) * 3600, {
                "conns": 30, "rtt_med": 40.0, "rtt_p90": 60.0, "rtt_min_med": 30.0, "sent": 10 ** 8, "retr": 10 ** 6,
                "dsack": 0, "why": None, "inputs": {"vless": "ok"}, "partial": 3})
        finally:
            w.close()
        p = self.score()["rows"]["people"]
        self.assertEqual((p["conns"], p["partial"], p["inputs"]), (30, 1, {"vless": "ok"}))
        self.assertTrue(p["partial_text"])
        self.assertIsNone(p["why_text"])
        self.assertNotIn("61717", json.dumps(p))

    def test_the_curves_are_gone(self):
        """NS-D34: the scale score-curves.json and its route /api/host/curves are deleted — not hidden."""
        self.assertEqual(self.call("GET", "/api/host/curves").status, 404)
        self.assertFalse(hasattr(S, "load_curves"))


class DayTest(ScoreCase):
    def test_a_day_without_data(self):
        doc = self.ok("GET", "/api/host/day/" + tz.day(self.now - 2 * DAY))
        self.assertEqual(doc["color"], "grey")
        self.assertEqual(doc["reason"]["code"], "no_data")
        self.assertIsNone(doc["reason"]["window"])
        self.assertEqual(len(doc["series"]["loss"]), 96)
        self.assertTrue(all(x is None for x in doc["series"]["loss"] + doc["series"]["rtt"]))
        self.assertEqual([lk["id"] for lk in doc["links"]], LINK_IDS)
        self.assertEqual(set(doc["links"][0]), set(HS.DAY_LINK_FIELDS))
        self.assertNotIn("score", doc)
        self.assertIsNone(doc["metrics"]["loss_peak"])

    def test_no_base_no_checks(self):
        with mock.patch.object(metrics, "sqlite3", None):
            self.assertEqual(self.ok("GET", "/api/host/day/" + tz.day(self.now)), {"ok": True, "charts": False})

    def test_foreign_input_in_the_date(self):
        today = tz.day(self.now)
        future = tz.day(self.now + 2 * DAY)
        old = tz.day(self.now - 31 * DAY)
        for d in ("2026-13-01", "2026-02-30", "2026-1-01", "20260101", "..%2F..%2Fetc", "2026-01-01x", future, old,
                  "%D9%A2%D9%A0%D9%A2%D9%A6-01-01", "%00", "2026-01-01%0a", today + "%20"):
            r = self.call("GET", "/api/host/day/" + d)
            self.assertEqual(r.status, 404, d)
        self.assertEqual(self.call("GET", "/api/host/day/" + today).status, 200)
        self.assertEqual(self.call("GET", "/api/host/day/" + tz.day(self.now - 29 * DAY)).status, 200)

    def test_today_links_are_the_links_of_score(self):
        self.check(5 * 3600)
        self.check(3 * 3600)
        self.probes(hours=30)
        with mock.patch.object(time, "time", lambda: float(self.now)):
            a = HS.score_doc(SPEC, self.now)
            b = HS.day_doc(SPEC, tz.day(self.now), self.now)
        for la, lb in zip(a["links"], b["links"]):
            self.assertEqual({k: la[k] for k in HS.DAY_LINK_FIELDS}, lb, la["id"])
        same = next(d for d in a["days"] if d["day"] == b["day"])
        self.assertEqual((same["color"], same["partial"], same["test"]), (b["color"], b["partial"], b["test"]))
        self.assertNotIn("score", b)

    def test_a_day_with_a_full_check_is_marked(self):
        """6C (QUEUE 19.10 18:48): «• — день с полным тестом» — ``days[].full``: a good full check that day; a quick
        one gives ``test`` only, a failed full one — nothing; a repeat of a full one (``by: auto``) — not a full test
        (REV-ns-p4a П-1)."""
        self.check(4 * DAY, mode="full", by="auto")
        self.check(3 * DAY, mode="quick")
        self.check(2 * DAY, mode="full")
        self.check(1 * DAY, mode="full", status="failed")
        with mock.patch.object(time, "time", lambda: float(self.now)):
            days = {d["day"]: d for d in HS.score_doc(SPEC, self.now)["days"]}
        got = [(days[tz.day(self.now - n * DAY)]["test"], days[tz.day(self.now - n * DAY)]["full"]) for n in (4, 3, 2, 1)]
        self.assertEqual(got, [(True, False), (True, False), (True, True), (False, False)])

    def test_series_and_peak(self):
        self.probes(hours=48, lost=0)
        day = tz.day(self.now - DAY)
        start, end = HS.day_span(day)
        w = metrics.open_writer()
        try:
            with w.conn:
                for ts in range(start, end, 600):
                    w.conn.execute("INSERT OR REPLACE INTO host_ping_m10 (anchor, ts, n, lost, rtt_med) VALUES "
                                   "('*',?,?,?,?)", (ts, 180, 54 if ts == start + 3600 else 0, 30.0))
        finally:
            w.close()
        doc = self.ok("GET", "/api/host/day/" + day)
        s = doc["series"]
        self.assertEqual((s["step"], len(s["loss"])), (15, 96))
        self.assertEqual(doc["metrics"]["loss_peak_at"], "01:00")
        self.assertGreater(doc["metrics"]["loss_peak"], 10)


class UiCTest(ScoreCase):
    """UI-C (k3/scratchpad/uiC/API-CONTRACT.md §1): ``series`` — the line of 30 days of a link, ``days[].m`` — the
    numbers of the hint of a day, ``days[].checks`` — the checks of a day; ``share_days`` without them."""

    def doc(self):
        with mock.patch.object(time, "time", lambda: float(self.now)):
            return HS.score_doc(SPEC, self.now)

    def m10_ping(self, day, lost_at=None, lost=54):
        start, end = HS.day_span(day)
        w = metrics.open_writer()
        try:
            with w.conn:
                for ts in range(start, min(end, self.now - 600), 600):
                    w.conn.execute("INSERT OR REPLACE INTO host_ping_m10 (anchor, ts, n, lost, rtt_med, rtt_p90, jit,"
                                   " jit_n) VALUES ('*',?,?,?,?,?,?,?)", (ts, 180, lost if ts == lost_at else 0, 30.0,
                                                                          40.0, 1.5, 100))
        finally:
            w.close()

    def test_empty_history_has_every_series_empty(self):
        doc = self.score()
        self.assertEqual(doc["series"], dict((t, []) for t in HS.SERIES_TESTS))
        self.assertTrue(all(d["checks"] == [] for d in doc["days"]))

    def test_series_of_the_checks(self):
        """x — the ts of a good check (a repeat too), v — its raw value rounded to 3 decimals; a failed or skipped
        check, a failed test and a check before the 30 days are not points."""
        old = self.check(31 * DAY)                                                   # before the strip
        a = self.check(20 * DAY, dict(S4, net_down=1700.12345))
        bad = self.check(15 * DAY, status="failed")
        skip = self.check(14 * DAY, status="skipped", why="busy")
        part = self.check(10 * DAY, dict(S4, net_down={"v": 900, "ok": False}), status="partial")   # a failed test
        rep = self.check(10 * DAY - 600, {"net_down": 1650}, by="auto", retry_of=part)
        last = self.check(3600, dict(S4, net_up=1250))
        ts = dict((rid, self.now - ago) for rid, ago in ((a, 20 * DAY), (part, 10 * DAY), (rep, 10 * DAY - 600),
                                                           (last, 3600)))
        s = self.score()["series"]
        self.assertEqual(set(s), set(HS.SERIES_TESTS))
        self.assertEqual(s["net_down"], [[ts[a], 1700.123], [ts[rep], 1650.0], [ts[last], 1700.0]])
        self.assertEqual(s["net_up"], [[ts[a], 1300.0], [ts[part], 1300.0], [ts[last], 1250.0]])
        for t in ("chacha_1t", "aes_1t", "cpu_mt"):
            self.assertEqual([x for x, _v in s[t]], [ts[a], ts[part], ts[last]], t)
        self.assertEqual(s["cpu_mt"][0][1], 10500.0)
        self.assertTrue(all(isinstance(x, int) for x, _v in s["net_down"]))
        self.assertFalse({old, bad, skip} & set(ts))

    def test_series_of_the_probes_are_the_days_of_the_strip(self):
        self.probes(hours=32 * 24, lost=108)
        doc = self.doc()
        strip = HS.days_back(tz.day(self.now))
        have = sorted(set(tz.day((self.now // 3600 - i) * 3600) for i in range(1, 32 * 24 + 1)) & set(strip))
        for t, v in (("rtt", 30.0), ("jitter", 1.5), ("loss", 10.0)):
            self.assertEqual([x for x, _v in doc["series"][t]], have, t)               # today's part of a day too
            self.assertTrue(all(abs(y - v) < 1e-9 for _x, y in doc["series"][t]), (t, doc["series"][t][:2]))
        self.assertEqual(have[-1], tz.day(self.now - 3600))

    def test_days_m_are_the_metrics_of_the_day_page(self):
        self.probes(hours=4 * 24, lost=54)
        yday = tz.day(self.now - DAY)
        start, _end = HS.day_span(yday)
        self.m10_ping(yday, lost_at=start + 3600)
        days = dict((d["day"], d) for d in self.doc()["days"])
        for day in (yday, tz.day(self.now - 3 * DAY), tz.day(self.now - 10 * DAY)):
            with mock.patch.object(time, "time", lambda: float(self.now)):
                met = HS.day_doc(SPEC, day, self.now)["metrics"]
            want = {"rtt": met["rtt_med"], "rtt_p90": met["rtt_p90"], "loss": met["loss"],
                    "loss_peak": met["loss_peak"], "jit": met["jit"], "off": met["offline_min"]}
            self.assertEqual(days[day]["m"], want, day)
        m = days[yday]["m"]
        self.assertEqual((m["rtt"], m["rtt_p90"], m["loss"], m["jit"], m["off"]), (30.0, 40.0, 5.0, 1.5, 0))
        self.assertGreater(m["loss_peak"], 10)                                        # the peak of 15 min (m10)
        self.assertEqual(days[tz.day(self.now - 10 * DAY)]["m"], dict(dict.fromkeys(HS.DAY_M, None), off=0))

    def test_days_m_are_not_of_the_cache_of_a_closed_day(self):
        self.probes(hours=6 * 24)
        day = tz.day(self.now - 4 * DAY)
        self.assertEqual(dict((d["day"], d) for d in self.doc()["days"])[day]["m"]["rtt"], 30.0)
        start, end = HS.day_span(day)
        w = metrics.open_writer()
        try:
            with w.conn:
                w.conn.execute("UPDATE host_ping_h1 SET rtt_med = 60.0 WHERE anchor = '*' AND ts >= ? AND ts < ?",
                               (start, end))
        finally:
            w.close()
        self.assertEqual(dict((d["day"], d) for d in self.doc()["days"])[day]["m"]["rtt"], 60.0)

    def test_days_m_without_the_base(self):
        self.check(3600)
        with mock.patch.object(HS, "read", return_value={"ok": False, "ping": [], "ping_m10": [], "host": [],
                                                         "steal": [], "canary": [], "events": [], "people": [],
                                                         "first": None}):
            days = self.doc()["days"]
        self.assertEqual(len(days), 30)
        self.assertTrue(all(d["m"] == dict.fromkeys(HS.DAY_M, None) for d in days))
        self.assertEqual(len(days[-1]["checks"]) + len(days[-2]["checks"]), 1)

    def test_days_checks(self):
        """The checks of the day of the node, oldest first, the last 8; down/up — net_down/net_up (null — failed or
        not measured); every status."""
        day = tz.day(self.now - DAY)
        start, _end = HS.day_span(day)
        at = [start + 3600 + 1800 * i for i in range(10)]
        ids = [self.check(self.now - t, dict(S4, net_down=1000 + i)) for i, t in enumerate(at)]
        self.check(self.now - (start - 60))                                           # the day before
        self.check(self.now - at[7] - 60, {}, status="skipped", why="busy", mode="quick", by="schedule")
        self.check(self.now - at[8] - 60, dict(S4, net_down={"v": 5, "ok": False}, net_up=None), by="auto",
                   retry_of=ids[0])
        got = dict((d["day"], d) for d in self.doc()["days"])[day]["checks"]
        self.assertEqual(len(got), HS.DAY_CHECKS_MAX)
        self.assertEqual(set(got[0]), {"hhmm", "mode", "by", "status", "down", "up"})
        self.assertEqual([c["hhmm"] for c in got], [HS._hhmm(t) for t in sorted(at[4:] + [at[7] + 60, at[8] + 60])])
        sk = next(c for c in got if c["status"] == "skipped")
        self.assertEqual((sk["mode"], sk["by"], sk["down"], sk["up"]), ("quick", "schedule", None, None))
        au = next(c for c in got if c["by"] == "auto")
        self.assertEqual((au["down"], au["up"]), (None, None))
        self.assertEqual((got[-1]["down"], got[-1]["up"], got[-1]["mode"], got[-1]["status"]),
                         (1009.0, 1300.0, "full", "ok"))

    def test_share_days_keep_their_white_list(self):
        self.probes(hours=48)
        self.check(3600)
        doc = self.doc()
        self.assertTrue(all("m" in d and "checks" in d for d in doc["days"]))
        shared = HS.share_days(doc["days"])
        self.assertEqual(len(shared), 30)
        self.assertTrue(all(set(d) == {"day", "color", "test", "partial"} for d in shared))


class PureTest(ScoreCase):
    def test_series_weights(self):
        day = "2026-03-02"
        start, _ = HS.day_span(day, tz.load("UTC"))
        rows = [{"ts": start + 600, "n": 100, "lost": 10, "rtt_med": 20.0},   # 00:10–00:20: half in 0, half in 1
                {"ts": start + 1200, "n": 100, "lost": 0, "rtt_med": 40.0}]   # 00:20–00:30: in 1
        s = HS.day_series(rows, day, 10, tz.load("UTC"))
        self.assertEqual(s["loss"][0], 10.0)
        self.assertAlmostEqual(s["loss"][1], 100.0 * 5 / 150, places=3)
        self.assertAlmostEqual(s["rtt"][1], (50 * 20 + 100 * 40) / 150.0, places=2)
        self.assertIsNone(s["loss"][2])

    def test_spring_day_has_96_points(self):
        z = tz.load("Europe/Berlin")
        start, end = HS.day_span("2027-03-28", z)
        self.assertEqual(end - start, 23 * 3600)
        rows = [{"ts": t, "n": 10, "lost": 0, "rtt_med": 5.0} for t in range(start, end, 600)]
        s = HS.day_series(rows, "2027-03-28", 10, z)
        self.assertEqual(len(s["loss"]), 96)
        self.assertEqual(s["loss"][8:12], [None] * 4)                         # 02:00–02:45
        self.assertTrue(all(x is not None for x in s["loss"][:8] + s["loss"][12:]))

    def test_every_people_why_has_words(self):
        """Every why code the base keeps (metrics.PEOPLE_WHY) has its text in both languages — «few» too, whose key
        is score.people.why.too_few (a key ending in .few reads as a plural form to the catalog checks)."""
        from kaskad import i18n
        for lang in ("ru", "en"):
            seen = set()
            for why in metrics.PEOPLE_WHY:
                with mock.patch("kaskad.gw.hostpeople.summary", return_value={"why": why}), i18n.use_lang(lang):
                    text = HS.people_row([{}])["why_text"]
                self.assertTrue(text and not text.startswith("score."), (lang, why, text))
                seen.add(text)
            self.assertEqual(len(seen), len(metrics.PEOPLE_WHY), lang)

    def test_offline_spans(self):
        ev = [(100, "node_offline", {"since": 100}), (400, "node_online", {"since": 100, "until": 390}),
              (1000, "node_online", {"since": 900, "until": 950}), (2000, "node_offline", {})]
        self.assertEqual(HS.offline_spans(ev), [(100, 390), (900, 950), (2000, None)])

    def test_valid_day(self):
        self.assertEqual(HS.valid_day("2026-10-19", "2026-10-19"), "2026-10-19")
        self.assertEqual(HS.valid_day("2026-09-20", "2026-10-19"), "2026-09-20")
        arabic = "".join(chr(0x660 + int(c)) for c in "2026") + "-10-19"     # \\d would take these digits
        for d in ("2026-09-19", "2026-10-20", "2026-02-29", None, 20261019, "2026-10-19\n", arabic):
            self.assertIsNone(HS.valid_day(d, "2026-10-19"), d)
