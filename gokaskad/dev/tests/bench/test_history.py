"""The history of the checks — bench/history (SPEC-node-score-schedule-share.md §3.3, §4, NS-S9, NS-S17, §2.8 п. 6;
B12): the base and the queue together without twice, a node without the gateway (the queue only), reading while the
collector writes, a base of SCHEMA 2, the list and its filters, one check, comparing two, and the raw material of the
score in the shape of kaskad.score (the rules themselves are B4's — coordinator 19.10): the checks, the new bases from
the base and from the queue, the stored bench_drop events, the thin wrapper over score.view."""

import os
import sqlite3
from unittest import mock

from kaskad import paths
from kaskad.bench import history, spool
from kaskad.gw import metrics
from tests.gw.test_metrics_bench import INV, check, stage_a
from tests.helpers import RootCase

H = 3600
D = 86400
DAY0 = 1760043600                       # 2025-10-10 00:00 in Moscow (the default zone, UTC+3)
NOW = DAY0 + 40 * D + 12 * H            # 2025-11-19 12:00


def t(test, value, mode=None, **kw):
    r = {"test": test, "value": value}
    if mode:
        r["mode"] = mode
    r.update(kw)
    return r


class Case(RootCase):
    def base(self, *docs):
        w = metrics.open_writer()
        try:
            for d in docs:
                self.assertTrue(w.write_bench(d))
        finally:
            w.close()

    def queue(self, *docs):
        for d in docs:
            spool.put(d)


class BaseAndQueueTest(Case):
    def test_queue_only_a_node_without_the_gateway(self):
        self.queue(check("0000000001", NOW - 2 * D), check("0000000002", NOW - D, status="skipped", why="busy",
                                                           tests=[]))
        got = history.runs(now=NOW)
        self.assertEqual([r["id"] for r in got["runs"]], ["0000000002", "0000000001"])
        self.assertFalse(got["db"])
        self.assertFalse(got["more"])
        self.assertEqual(got["runs"][0]["status"], "skipped")
        # NS-D34: the stored score and links of a check (bench_run.score/links) are never shown
        self.assertFalse({"score", "links", "usual", "usual_n"} & set(got["runs"][1]))

    def test_base_and_queue_nothing_twice_the_base_wins(self):
        self.base(check("0000000001", NOW - 3 * D), check("0000000002", NOW - 2 * D))
        self.queue(check("0000000002", NOW - 2 * D, rx=1), check("0000000003", NOW - D))
        got = history.runs(now=NOW)
        self.assertTrue(got["db"])
        self.assertEqual([(r["id"], r["rx"]) for r in got["runs"]],
                         [("0000000003", 10 ** 8), ("0000000002", 10 ** 8), ("0000000001", 10 ** 8)])
        self.assertEqual(len(history.sample(NOW)["runs"]), 3)

    def test_reading_while_the_collector_writes(self):
        self.base(check("0000000001", NOW - 2 * D))
        w = metrics.open_writer()
        self.addCleanup(w.close)
        w.conn.execute("BEGIN")
        w.conn.execute("INSERT INTO bench_run (id, ts, mode, by, status) VALUES ('0000000002', ?, 'full', 'cli', 'ok')",
                       (NOW - D,))
        w.conn.execute("INSERT INTO bench (run, test, ts, value, ok) VALUES ('0000000002', 'net_down', ?, 1, 1)",
                       (NOW - D,))
        self.assertEqual([r["id"] for r in history.runs(now=NOW)["runs"]], ["0000000001"])   # not half a check
        w.conn.execute("COMMIT")
        got = history.run("0000000002")
        self.assertEqual([x["test"] for x in got["tests"]], ["net_down"])

    def test_the_collector_carries_a_check_between_the_two_reads(self):
        """З-1 круг 2: the queue is read first, the base second — a check the collector writes and removes in between
        is in the answer (from the base), never in neither; nothing twice."""
        self.base(check("0000000001", NOW - 2 * D))
        self.queue(check("0000000002", NOW - D))
        real = spool.read_all

        def read_then_drain():
            got = real()
            w = metrics.open_writer()
            try:
                spool.drain(w, NOW)
            finally:
                w.close()
            return got

        with mock.patch.object(spool, "read_all", side_effect=read_then_drain):
            got = history.runs(now=NOW)
        self.assertEqual([r["id"] for r in got["runs"]], ["0000000002", "0000000001"])
        self.assertEqual(os.listdir(spool.spool_dir()), [])           # it did move into the base meanwhile
        real_db = history._db

        def db_then_drain(*a, **kw):                                  # the other order would lose it
            got = real_db(*a, **kw)
            w = metrics.open_writer()
            try:
                spool.drain(w, NOW)
            finally:
                w.close()
            return got

        self.queue(check("0000000003", NOW - H))
        with mock.patch.object(history, "_db", side_effect=db_then_drain):
            ids = [r["id"] for r in history.runs(now=NOW)["runs"]]
        self.assertEqual(ids, ["0000000003", "0000000002", "0000000001"])

    def test_one_check_reads_its_own_file_only(self):
        """П-2 круг 2: run(id) opens <id>.json, not the whole queue."""
        for i in range(1, 6):
            self.queue(check("%010d" % i, NOW - i * H))
        seen = []
        real = spool.load

        def load(path):
            seen.append(os.path.basename(path))
            return real(path)

        with mock.patch.object(spool, "load", side_effect=load), \
                mock.patch.object(spool, "read_all", side_effect=AssertionError("the whole queue")):
            self.assertEqual(history.run("0000000003")["id"], "0000000003")
            self.assertIsNone(history.run("0000000009"))
        self.assertEqual(seen, ["0000000003.json", "0000000009.json"])      # only its own name (absent)

    def test_a_base_of_schema_2_reads_the_queue(self):
        w = stage_a().open_writer()
        w.write_host_minute(NOW, {"n": 6})
        w.close()
        self.queue(check("0000000001", NOW - D))
        got = history.runs(now=NOW)
        self.assertEqual(([r["id"] for r in got["runs"]], got["db"]), (["0000000001"], False))

    def test_a_broken_queue_file_is_skipped_by_the_reader_and_kept(self):
        self.queue(check("0000000001", NOW - D))
        with open(os.path.join(spool.spool_dir(), "0000000002.json"), "w") as f:
            f.write("{broken")
        self.assertEqual([r["id"] for r in history.runs(now=NOW)["runs"]], ["0000000001"])
        self.assertTrue(os.path.exists(os.path.join(spool.spool_dir(), "0000000002.json")))

    def test_no_sqlite_the_queue_still_reads(self):
        self.queue(check("0000000001", NOW - D))
        with mock.patch.object(metrics, "sqlite3", None):
            got = history.runs(now=NOW)
        self.assertEqual(([r["id"] for r in got["runs"]], got["db"]), (["0000000001"], False))


class ListTest(Case):
    def setUp(self):
        super().setUp()
        self.base(*[check("%010x" % i, NOW - (10 - i) * D, mode="quick" if i % 2 else "full",
                          by="schedule" if i % 3 else "panel", status="skipped" if i == 4 else "ok")
                    for i in range(10)])

    def test_filters(self):
        ids = lambda **kw: [r["id"] for r in history.runs(now=NOW, **kw)["runs"]]
        self.assertEqual(len(ids()), 10)
        self.assertEqual(len(ids(mode="quick")), 5)
        self.assertEqual(ids(by="panel"), ["%010x" % i for i in (9, 6, 3, 0)])
        self.assertEqual(ids(status="skipped"), ["0000000004"])
        self.assertEqual(ids(frm=NOW - 3 * D), ["%010x" % i for i in (9, 8, 7)])

    def test_days_by_the_zone_of_the_node_to_inclusive(self):
        day = "2025-11-15"                                          # the check of NOW − 4 D (15.11 12:00 MSK)
        self.assertEqual([r["id"] for r in history.runs(frm=day, to=day, now=NOW)["runs"]], ["0000000006"])
        self.assertEqual(len(history.runs(frm="2025-11-14", to="2025-11-15", now=NOW)["runs"]), 2)

    def test_pages(self):
        p1 = history.runs(now=NOW, limit=4)
        self.assertTrue(p1["more"])
        p2 = history.runs(now=NOW, limit=4, before=p1["runs"][-1]["ts"])
        p3 = history.runs(now=NOW, limit=4, before=p2["runs"][-1]["ts"])
        self.assertFalse(p3["more"])
        self.assertEqual([r["id"] for r in p1["runs"] + p2["runs"] + p3["runs"]], ["%010x" % i for i in range(9, -1, -1)])

    def test_the_default_is_30_days(self):
        self.base(check("00000000ff", NOW - 31 * D))
        self.assertNotIn("00000000ff", [r["id"] for r in history.runs(now=NOW)["runs"]])


class RunCompareTest(Case):
    def test_one_check(self):
        self.queue(check("0000000001", NOW - D, why="error", why_args={"n": 1}, retry_of="0000000000",
                         by="auto"))
        r = history.run("0000000001")
        self.assertEqual(r["by"], "auto")
        self.assertEqual(r["retry_of"], "0000000000")
        self.assertEqual([x["test"] for x in r["tests"]], ["cpu_1t", "net_down"])
        self.assertEqual(r["cost_real"], {"rx": 10 ** 8, "tx": 10 ** 7, "cpu_s": 31.0, "dur_s": 280.5, "estimate": None})
        self.assertEqual(history.run("0000000001", "ru")["why_text"], "Проверка прервалась ошибкой")
        self.assertEqual(history.run("0000000001", "en")["why_text"], "The check stopped on an error")
        for bad in ("../x", "000000000", "0000000002", None, 5):
            self.assertIsNone(history.run(bad))

    def test_why_text_in_the_language(self):
        """After the merge with B1 the text is B1's ``bench.run.why_text`` (one catalog ``bench_run.why.<code>``)."""
        from kaskad.bench import run as brun
        self.queue(check("0000000001", NOW - D, status="skipped", why="busy",
                         why_args={"mbps": 4.2, "thr_mbps": 2, "cpu": 31, "thr_cpu": 20}, tests=[]),
                   check("0000000002", NOW - D, status="skipped", why="busy", why_args={"mbps": 4.2}, tests=[]))
        self.assertEqual(history.run("0000000001", "ru")["why_text"],
                         "Сервер был занят: чужой трафик 4,2 Мбит/с при пороге 2 Мбит/с, процессор 31% при пороге 20%")
        self.assertEqual(history.run("0000000001", "en")["why_text"],
                         "The server was busy: other traffic 4.2 Mbit/s (limit 2 Mbit/s), CPU 31% (limit 20%)")
        for lang in ("ru", "en"):                          # only mbps: no raw {cpu}, {thr_mbps} — the text without them
            text = history.run("0000000002", lang)["why_text"]
            self.assertNotIn("{", text)
            self.assertEqual(text, brun.why_text("busy_plain", None, lang))
        with mock.patch.object(brun, "why_text", side_effect=lambda c, a, lang: "%s %s %s" % (c, sorted(a), lang)):
            self.assertEqual(history.run("0000000002", "en")["why_text"], "busy ['mbps'] en")

    def test_compare_the_raw_values_and_rel(self):
        """NS-D33: the raw values side by side — ``delta`` b − a in the unit of the test, ``delta_pct`` the same in % of
        a (none when a is 0 or either is missing); the stored scores of the tests never come out; ``better`` only by
        rel_total of both checks (each against «обычно» at it), else no such field."""
        self.base(check("0000000001", NOW - 2 * D, tests=[t("net_down", 700, score=668), t("dns", 40.0),
                                                          t("cpu_1t", 1000, score=500), t("mem_gb", 2.0),
                                                          t("disk_rw", 0.0)]),
                  check("0000000002", NOW - D, mode="quick", tests=[t("net_down", 300, score=500), t("dns", 20.0),
                                                                     t("cpu_1t", 1010, score=505), t("disk_rr", 9),
                                                                     t("disk_rw", 5.0)]))
        c = history.compare("0000000001", "0000000002")
        self.assertTrue(c["mode_differs"])
        rows = dict((r["test"], r) for r in c["rows"])
        self.assertEqual([r["test"] for r in c["rows"]], ["cpu_1t", "disk_rw", "dns", "mem_gb", "net_down", "disk_rr"])
        self.assertEqual({k for r in c["rows"] for k in r}, {"test", "a", "b", "delta", "delta_pct", "unit", "dir"})
        self.assertEqual((rows["net_down"]["dir"], rows["dns"]["dir"]), ("higher", "lower"))   # UI-C: ▲/▼ в панели
        self.assertEqual((rows["net_down"]["delta"], rows["net_down"]["delta_pct"]), (-400, -57.1))
        self.assertEqual((rows["dns"]["delta"], rows["dns"]["delta_pct"]), (-20.0, -50.0))
        self.assertEqual((rows["cpu_1t"]["delta"], rows["cpu_1t"]["delta_pct"]), (10, 1.0))
        self.assertEqual((rows["disk_rw"]["delta"], rows["disk_rw"]["delta_pct"]), (5.0, None))     # a is 0
        self.assertEqual((rows["mem_gb"]["delta"], rows["mem_gb"]["delta_pct"]), (None, None))
        self.assertEqual((rows["disk_rr"]["a"], rows["disk_rr"]["delta"]), (None, None))
        self.assertEqual((c["rel_a"], c["rel_b"]), (None, None))      # two checks: nothing to compare them with
        self.assertNotIn("better", c)
        self.assertNotIn("score", c["a"]["tests"][0])
        for rel, better in (((96, 104), "b"), ((104, 96), "a"), ((100, 100), "same")):
            got = history.compare("0000000001", "0000000002", rel=rel)
            self.assertEqual((got["rel_a"], got["rel_b"], got["better"]), rel + (better,))
        self.assertNotIn("better", history.compare("0000000001", "0000000002", rel=(96, None)))
        self.assertIsNone(history.compare("0000000001", "0000000009"))
        for gone in ("better_of", "_directions", "SAME", "usual_of", "USUAL_DAYS"):    # NS-D34: no rule of the scores
            self.assertFalse(hasattr(history, gone), gone)


DROP = {"link": "down", "cond": "rel", "day": "2025-11-18", "matched": ["rel"], "push": True, "auto": True,
        "now": 20, "test": "net_down", "value": 120.5, "usual": 600.0, "unit": "Мбит/с"}


class SampleTest(Case):
    def test_the_shape_of_kaskad_score(self):
        """Exactly the shape of score.py's «History» (круг 2, С-1): run keys and value keys of the docstring; what
        score reads besides the columns comes back from meta/extra."""
        self.queue(check("0000000001", NOW - 2 * D, under_load=None, meta={"engines": ["vless"], "cpu_flags": ["aes"]},
                         tests=[t("net_down", 420, "full", under_load=1, value_corr=600, people=180, ca="bbr",
                                  extra={"budget": True}),
                                t("cpu_1t", None, ok=0, why="no_pkgs"), t("dns", 20.0, unit="мс",
                                                                                        extra={"src": "kaskad"}),
                                t("loss", 0.5, extra={"n": 600, "lo": 0.08})]),
                   check("0000000002", NOW - D, by="auto", retry_of="0000000001", under_load=0, inv=INV, tests=[]))
        smp = history.sample(NOW)
        a, b = smp["runs"]
        self.assertEqual(set(a), {"id", "ts", "mode", "by", "retry_of", "status", "under_load", "vals"})
        self.assertEqual(set(b), {"id", "ts", "mode", "by", "retry_of", "status", "under_load", "vals", "inv"})
        self.assertEqual(b["inv"], dict((k, INV[k]) for k in history.REL_INV if k in INV))   # the inventory
        self.assertTrue(b["inv"])
        self.assertIsNone(a["under_load"])
        self.assertIs(b["under_load"], False)
        self.assertEqual((b["by"], b["retry_of"]), ("auto", "0000000001"))
        self.assertEqual(b["vals"], {"_engines": ["awg", "vless"]})   # no meta: the engines of the inventory
        self.assertEqual(a["vals"]["net_down"], {"v": 420.0, "mode": "full", "corr": 600.0, "people": 180.0,
                                                 "budget": True})
        self.assertEqual(a["vals"]["cpu_1t"], {"v": None, "mode": "full", "ok": False, "why": "no_pkgs"})
        self.assertEqual(a["vals"]["dns"], {"v": 20.0, "mode": "full", "src": "kaskad"})
        self.assertEqual(a["vals"]["loss"], {"v": 0.5, "mode": "full", "n": 600, "lo": 0.08})
        self.assertEqual((a["vals"]["_engines"], a["vals"]["_cpu_flags"]), (["vless"], ["aes"]))
        self.assertEqual(smp["events"], [])

    def test_no_rules_here(self):
        """The rules of the score live in kaskad.score only (coordinator 19.10)."""
        for name in ("fresh_values", "days_score", "checked", "flags", "score_inputs", "points", "base_since"):
            self.assertFalse(hasattr(history, name), name)


class NewBaseTest(Case):
    """NS-S17: the events of a new base — written by the collector (with the gateway), drawn from the inventories of
    the queue (without it), or half and half; the tariff from its file."""

    def checks(self):
        return [check("0000000001", NOW - 4 * D, inv=INV), check("0000000002", NOW - 3 * D, inv=INV),
                check("0000000003", NOW - 2 * D, inv=dict(INV, cpu="Intel Xeon")),
                check("0000000004", NOW - D, inv=dict(INV, cpu="Intel Xeon", engines=["vless"]))]

    def verify(self):
        smp = history.sample(NOW)
        self.assertEqual([(e["ts"], e["kind"]) for e in smp["epochs"]], [(NOW - 2 * D, "cpu"), (NOW - D, "engines")])

    def test_with_the_gateway(self):
        self.base(*self.checks())
        self.verify()

    def test_without_the_gateway(self):
        self.queue(*self.checks())
        self.verify()

    def test_half_in_the_base_half_in_the_queue(self):
        cs = self.checks()
        self.base(*cs[:2])
        self.queue(*cs[2:])
        self.verify()

    def test_the_tariff(self):
        spool.put_epoch(NOW - D - 60, {"mbps": 500})
        self.assertEqual(history.sample(NOW)["epochs"], [{"ts": NOW - D - 60, "kind": "tariff", "args": {"mbps": 500}}])


class DropEventsTest(Case):
    """§2.8 п. 6, coordinator 19.10: bench_drop is stored once (the runner works it out, the one writer writes it) and
    read back in the shape of score.drop_events — not replayed per request."""

    def doc(self, rid="0000000002", ts=NOW - D, **kw):
        d = check(rid, ts, by="auto", retry_of="0000000001", **kw)
        d["drops"] = [dict(DROP), {"link": "up", "cond": "rel", "day": "2025-11-18", "now": 45}]
        return d

    def want(self, rid="0000000002", ts=NOW - D):
        return [{"link": "down", "run": rid, "ts": ts, "day": "2025-11-18", "cond": "rel", "matched": ["rel"],
                 "push": True, "auto": True, "now": 20, "test": "net_down", "value": 120.5, "usual": 600.0,
                 "unit": "Мбит/с"},
                {"link": "up", "run": rid, "ts": ts, "day": "2025-11-18", "cond": "rel", "matched": ["rel"],
                 "push": False, "auto": False, "now": 45, "test": None, "value": None, "usual": None, "unit": None}]

    def test_from_the_base_and_from_the_queue_alike(self):
        self.base(self.doc())
        self.assertEqual(history.sample(NOW)["events"], self.want())
        self.queue(self.doc("0000000003", NOW - H))
        self.assertEqual(history.sample(NOW)["events"], self.want() + self.want("0000000003", NOW - H))

    def test_the_rows_of_the_list_carry_the_drops(self):
        """B15 (REV-design-2026-10-19-3 Н3-З4): a check that found a sharp drop says so in its row — the mark
        «резкое падение» and the filter «С проблемами» of the screen; a check without — an empty list. NS-D33: the
        rel of the link (``now`` of the event) and the raw numbers «… обычно …»."""
        self.base(check("0000000001", NOW - 2 * D), self.doc())
        rows = dict((r["id"], r["drops"]) for r in history.runs(now=NOW)["runs"])
        self.assertEqual(rows["0000000001"], [])
        self.assertEqual(rows["0000000002"], [{"link": "down", "rel": 20, "test": "net_down", "value": 120.5,
                                               "usual": 600.0, "unit": "Мбит/с"},
                                              {"link": "up", "rel": 45, "test": None, "value": None, "usual": None,
                                               "unit": None}])
        self.queue(self.doc("0000000003", NOW - H))
        self.assertEqual(history.run("0000000003")["drops"][0]["link"], "down")

    def test_rows_of_the_old_scale_are_not_events(self):
        """NS-D34: rows of bench_drop stored before the update (cond 100/300/"fall", now — a score 0…1000) are not
        events of now: not in sample()["events"] (the reasons of the days, «Проверю ещё раз»), not in the marks of the
        list (their now would read as rel)."""
        self.base(check("0000000001", NOW - 2 * D), self.doc())
        c = sqlite3.connect(paths.metrics_db())
        try:
            with c:
                c.executemany("INSERT INTO bench_drop (run, link, ts, day, cond, args) VALUES (?, ?, ?, ?, ?, ?)",
                              [("0000000001", "down", NOW - 2 * D, "2025-11-17", "300", '{"now": 420, "usual": 610}'),
                               ("0000000001", "cpu", NOW - 2 * D, "2025-11-17", "fall", '{"now": 380}'),
                               ("0000000001", "up", NOW - 2 * D, "2025-11-17", "100", '{"now": 95}')])
        finally:
            c.close()
        self.assertEqual(history.sample(NOW)["events"], self.want())
        rows = dict((r["id"], r["drops"]) for r in history.runs(now=NOW)["runs"])
        self.assertEqual(rows["0000000001"], [])
        self.assertEqual([x["link"] for x in rows["0000000002"]], ["down", "up"])
        self.assertEqual(history.run("0000000001")["drops"], [])

    def test_view_hands_the_stored_events_to_score_view(self):
        self.base(check("0000000001", NOW - 2 * D), self.doc())
        seen = {}

        class Score(object):
            @staticmethod
            def view(spec, runs, now, **kw):
                seen.update(spec=spec, ids=[r["id"] for r in runs], now=now, **kw)
                return {"summary": {"rel_total": 96}}

            @staticmethod
            def drop_events(*a, **kw):
                raise AssertionError("never replayed per request")

        with mock.patch.object(history, "_score", return_value=Score):
            self.assertEqual(history.view({"v": 1}, NOW, rule={"pct": 10}), {"summary": {"rel_total": 96}})
        self.assertEqual((seen["spec"], seen["ids"], seen["now"]), ({"v": 1}, ["0000000001", "0000000002"], NOW))
        self.assertEqual(seen["events"], self.want())
        self.assertEqual(seen["epochs"], [])
        self.assertEqual(seen["rule"], {"pct": 10})
        with mock.patch.object(history, "_score", return_value=None):
            self.assertIsNone(history.view({}, NOW))
