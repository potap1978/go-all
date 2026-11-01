"""The checks of the node in metrics.db — SCHEMA 3 (k3/handoff/SPEC-node-score-schedule-share.md §3.1, §3.2, NS-S9,
NS-S17; B12): the way up from a base of stage A (SCHEMA 2) with its data whole, the way back to the code of A, the
writers (a check, a new base, the hour of «Связь с людьми»), keeping 400/90 days, the cap with bench cut last."""

import hashlib
import json
import os
import sqlite3
import types
from unittest import mock

from kaskad.bench import spool
from kaskad.gw import metrics
from tests.helpers import RootCase

H = 3600
D = 86400
NOW = 1760000000 // H * H + 1800
STAGE_A = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "gw",
                       "metrics_schema2.py.txt")
STAGE_A_BLOB = "29752bbdfc01d1946d2650ce4c75fd9a3bc66220"   # git rev-parse c49e45db:lib/kaskad/gw/metrics.py (3.3.7)
A_TABLES = ("m1", "m10", "h1", "speed", "svc", "geo", "traffic", "events") + metrics.HOST_TABLES + metrics.PING_TABLES
NEW_TABLES = ("bench_run", "bench", "bench_epoch", "bench_drop", "host_people_h1")


def stage_a():
    """The code of stage A (SCHEMA 2) as it is in 3.3.7 c49e45db — a module of its own next to kaskad.gw.metrics."""
    with open(STAGE_A, encoding="utf-8") as f:
        code = compile(f.read(), STAGE_A, "exec")
    mod = types.ModuleType("kaskad.gw._metrics_schema2")
    mod.__package__ = "kaskad.gw"
    mod.__file__ = STAGE_A
    exec(code, mod.__dict__)
    return mod


def tables(conn):
    return set(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"))


def dump(conn, names):
    have = tables(conn)
    return dict((t, sorted(conn.execute("SELECT * FROM %s" % t).fetchall(), key=repr)) for t in names if t in have)


def check(rid="a1b2c3d4e5", ts=NOW - 600, status="ok", mode="full", tests=None, inv=None, **run):
    r = {"id": rid, "ts": ts, "end_ts": ts + 300, "mode": mode, "by": "panel", "status": status, "score": 700,
         "scale_version": "1.0", "links": {"down": 650, "cpu": 720}, "rx": 10 ** 8, "tx": 10 ** 7, "dur_s": 280.5,
         "cpu_s": 31.0, "under_load": 0, "pkgs": 1}
    if inv is not None:
        r["inv"] = inv
    r.update(run)
    if tests is None:
        tests = [{"test": "net_down", "value": 620.0, "unit": "Мбит/с", "score": 668},
                 {"test": "cpu_1t", "value": 1200.0, "unit": "событий/с", "score": 610}]
    return {"v": 1, "run": r, "tests": tests}


INV = {"cpu": "AMD EPYC 7763 64-Core Processor", "nproc": 2, "mem_gb": 3.8, "engines": ["vless", "awg"]}
# an inventory and a series of B1 as in life (договор 19.10: both kept in bench_run.meta): ~110 flags of /proc/cpuinfo
FULL_INV = dict(INV, flags=["fpu", "vme", "de", "pse", "tsc", "msr", "pae", "mce", "cx8", "apic", "sep", "mtrr", "pge",
                            "mca", "cmov", "pat", "pse36", "clflush", "mmx", "fxsr", "sse", "sse2", "ht", "syscall",
                            "nx", "mmxext", "fxsr_opt", "pdpe1gb", "rdtscp", "lm", "rep_good", "nopl", "cpuid",
                            "extd_apicid", "tsc_known_freq", "pni", "pclmulqdq", "ssse3", "fma", "cx16", "pcid",
                            "sse4_1", "sse4_2", "x2apic", "movbe", "popcnt", "aes", "xsave", "avx", "f16c", "rdrand",
                            "hypervisor", "lahf_lm", "cmp_legacy", "cr8_legacy", "abm", "sse4a", "misalignsse",
                            "3dnowprefetch", "osvw", "topoext", "perfctr_core", "invpcid_single", "ssbd", "ibrs",
                            "ibpb", "stibp", "vmmcall", "fsgsbase", "bmi1", "avx2", "smep", "bmi2", "erms", "invpcid",
                            "rdseed", "adx", "smap", "clflushopt", "clwb", "sha_ni", "xsaveopt", "xsavec", "xgetbv1",
                            "xsaves", "clzero", "xsaveerptr", "wbnoinvd", "arat", "npt", "nrip_save", "umip", "pku",
                            "ospke", "vaes", "vpclmulqdq", "rdpid", "fsrm", "arch_capabilities"] +
                ["x%02d" % i for i in range(15)],
                smt=1, disks=[{"rota": False, "size": 42949672960}], nic={"eth0": None}, virt="kvm",
                kernel="6.8.0-45-generic", os={"id": "ubuntu", "version_id": "24.04"}, openssl_ver="3.0.13")
SERIES = {"anchors": [{"addr": "192.0.2.%d" % i, "n": 21, "lost": 0, "rtt_med": 31.5, "na": None} for i in range(3)]}


def doc_drop():
    return {"link": "down", "cond": "rel", "day": "2030-01-02", "matched": ["rel"], "push": True, "now": 40}


# С-2 круг 2: every kind of a bad field of a check file — each makes the whole file bad
BAD_RUN = [dict(score=5000), dict(score="700"), dict(score=True), dict(rx=-1), dict(rx=1.5), dict(rx="1"),
           dict(dur_s=-1.0), dict(dur_s="280"), dict(under_load=7), dict(under_load="1"), dict(pkgs=2),
           dict(sched_id="x" * 40), dict(sched_id=5), dict(retry_of="zz"), dict(end_ts=10 ** 13), dict(end_ts="soon"),
           dict(links={"down": 2000}), dict(links={"Bad-Id": 3}), dict(links=[1]), dict(links={"down": "x"}),
           dict(why="Bench skip"), dict(why=5), dict(why_args={"err": "Errno 13: /etc/kaskad/x"}),
           dict(why_args={"a": {"b": {"c": 1}}}), dict(why_args={"Bad": 1}), dict(why_args="x"),
           dict(why_args={"x": [1]}), dict(why_args={"forced": {"err": "a b"}}), dict(inv={"flags": "aes"}),
           dict(inv={"disks": [{"rota": 2}]}), dict(inv={"disks": [{"size": -1}]}), dict(inv={"disks": "sda"}),
           dict(inv={"nic": {"eth0/x": 1}}), dict(inv={"os": {"id": "x\n"}}), dict(inv={"smt": 0}),
           dict(inv={"openssl_ver": 3}), dict(inv={"virt": "a b"}), dict(inv={"kernel": "k" * 65}),
           dict(scale_version="a b"), dict(inv="text"), dict(inv={"nproc": 0}),
           dict(inv={"mem_gb": -1}), dict(inv={"engines": ["A/B"]}), dict(inv={"engines": "awg"}),
           dict(inv={"crash_mb": -1}), dict(inv={"crash_mb": "256"}), dict(inv={"crash_mb": 2 ** 21}),
           dict(inv={"cpu": "x\nrm"}), dict(inv={"flags": ["f%031d" % i for i in range(140)]}),   # above 4 KB
           dict(meta=[]), dict(meta={"engines": ["Vless!"]}),
           dict(meta={"cpu_flags": "aes"}), dict(meta={"cpu_flags": ["f%d" % i for i in range(513)]}),
           dict(meta={"cpu_flags": ["f%031d" % i for i in range(140)]})]          # above 4 KB of JSON
BAD_TEST = [{"test": "net_up", "value": "fast"}, {"test": "net_up", "value": float("inf")},
            {"test": "net_up", "value": 10 ** 13}, {"test": "net_up", "unit": "x" * 17}, {"test": "net_up", "score": -1},
            {"test": "net_up", "ca": "BBR!"}, {"test": "net_up", "ok": 2}, {"test": "net_up", "mode": "slow"},
            {"test": "net_up", "ts": "now"}, {"test": "net_up", "value_corr": -1}, {"test": "net_up", "people": "x"},
            {"test": "net_up", "why": "a b"}, {"test": "net_up", "under_load": "yes"},
            {"test": "net_up", "extra": 1}, {"test": "net_up", "dns_src": "google"},
            {"test": "net_up", "extra": {"n": -1}}, {"test": "net_up", "extra": {"n": "600"}},
            {"test": "net_up", "extra": {"lo": float("nan")}}, {"test": "net_up", "extra": {"wait": 1}},
            {"test": "net_up", "extra": {"catalog": "yes"}}, {"test": "net_up", "extra": {"budget": None, "src": "a b"}},
            {"test": "net_up", "extra": {"points": {"n": 1}}}, {"test": "net_up", "extra": {"points": [{"n": -1}]}},
            {"test": "net_up", "extra": {"points": [{}] * 9}}, {"test": "net_up", "extra": {"src": "x" * 17}},
            {"test": "net_up", "extra": {"dns_src": "1.1.1.1"}}]
# договор с B1 (координатор 19.10): an unknown key is not kept and the file is NOT bad (a line once per key)
UNKNOWN_RUN = [dict(pad="z"), dict(inv={"ip": "1"}), dict(inv={"os": {"id": "x", "pretty": "y"}}), dict(meta={"x": 1})]
UNKNOWN_TEST = [{"test": "net_up", "pad": 1}, {"test": "net_up", "extra": {"x": 1}},
                {"test": "net_up", "extra": {"points": [{"x": 1}]}}, {"test": "net_up", "src": "system"},
                {"test": "net_up", "run": "anything"}]


class FrozenStageATest(RootCase):
    def test_the_copy_is_the_code_of_3_3_7(self):
        with open(STAGE_A, "rb") as f:
            data = f.read()
        self.assertEqual(hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), STAGE_A_BLOB)
        self.assertEqual(stage_a().SCHEMA, 2)


class WayUpTest(RootCase):
    """2 → 3: CREATE TABLE IF NOT EXISTS only, every row of stage A in place, the number 3."""

    def fill_a(self, a):
        w = a.open_writer()
        self.assertIsNotNone(w)
        try:
            for i in range(90):
                ts = NOW - 2 * H + i * 60
                w.write_minute(ts, {"e-a": {"rtt_med": 10.0 + i, "n": 6, "ok": 6, "rx": 5, "state": "ok"}})
                w.write_host_minute(ts, {"n": 6, "busy": float(i % 7), "mem_avail": 70.5})
                w.write_ping_minute(ts, {"*": {"via": "icmp", "n": 9, "lost": 0, "rtt_med": 12.5}})
            w.add_speed("e-a", NOW - 600, 88.5, 120.0, 30.0, 10 ** 6, "speed.example.com")
            w.add_event(NOW - 300, "host_cpu", None, {"level": 1})
            w.add_traffic("e-a", "2030-05-10", 10, 20, 1)
            self.assertTrue(w.maintain(NOW))
            self.assertEqual(w.meta("schema"), "2")
            self.assertFalse(set(NEW_TABLES) & tables(w.conn))
            return dump(w.conn, A_TABLES + ("meta",))
        finally:
            w.close()

    def test_base_of_stage_a_goes_up_whole(self):
        before = self.fill_a(stage_a())
        self.assertTrue(before["host_m10"] and before["host_ping_m10"] and before["m10"])
        w = metrics.open_writer()
        try:
            self.assertEqual(w.meta("schema"), str(metrics.SCHEMA))   # 3, and 4 since the activity (SPEC-activity §4)
            after = dump(w.conn, A_TABLES + ("meta",))
            for t in A_TABLES:
                self.assertEqual(after[t], before[t], t)          # every row in place, no ALTER
            self.assertEqual(dict(before["meta"]), dict(dict(after["meta"]), schema="2"))
            self.assertTrue(set(NEW_TABLES + ("bench_run_ts",)) <= tables(w.conn))
            cols = [r[1] for r in w.conn.execute("PRAGMA table_info(bench_run)")]
            self.assertEqual(cols, ["id", "ts", "end_ts", "mode", "by", "sched_id", "retry_of", "inv", "status", "why",
                                    "why_args", "rx", "tx", "dur_s", "cpu_s", "under_load", "pkgs", "score",
                                    "scale_version", "links", "meta"])
            cols = [r[1] for r in w.conn.execute("PRAGMA table_info(bench)")]
            self.assertEqual(cols, ["run", "test", "ts", "value", "unit", "scale_version", "mode", "under_load", "ok",
                                    "why", "score", "value_corr", "people", "ca", "extra"])
            self.assertEqual([r[1] for r in w.conn.execute("PRAGMA table_info(host_people_h1)")],
                             ["ts", "conns", "rtt_med", "rtt_p90", "rtt_min_med", "sent", "retr", "dsack", "why",
                              "inputs", "partial"])
            self.assertEqual([r[1] for r in w.conn.execute("PRAGMA table_info(bench_drop)")],
                             ["run", "link", "ts", "day", "cond", "args"])
            for t, pk in (("bench", "PRIMARY KEY (run, test)) WITHOUT ROWID"),
                          ("bench_drop", "PRIMARY KEY (run, link)) WITHOUT ROWID"),
                          ("bench_epoch", "PRIMARY KEY (ts, kind)) WITHOUT ROWID")):
                self.assertIn(pk, w.conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (t,)).fetchone()[0])
            self.assertTrue(w.write_bench(check()))
            self.assertTrue(w.maintain(NOW))                       # the new maintain keeps what A kept
            self.assertEqual(dump(w.conn, A_TABLES), dict((t, before[t]) for t in A_TABLES))
        finally:
            w.close()


class WayBackTest(RootCase):
    """3 → the code of A: it opens SCHEMA 3, keeps the number, never reads or touches bench*; up again — the old go."""

    def test_code_of_a_leaves_bench_alone(self):
        a = stage_a()
        w = metrics.open_writer()
        try:
            self.assertTrue(w.write_bench(check(inv=INV)))
            self.assertTrue(w.write_bench(check("0000000001", NOW - 500 * D)))     # older than kept: A keeps it
            self.assertTrue(w.add_epoch(NOW - 2 * D, "tariff", {"mbps": 1000}))
            self.assertTrue(w.write_people_hour(NOW - H, {"conns": 40, "rtt_med": 48.0}))
            rows = dump(w.conn, NEW_TABLES)
        finally:
            w.close()
        w = a.open_writer()
        self.assertIsNotNone(w)
        try:
            self.assertEqual(w.meta("schema"), str(metrics.SCHEMA))   # the number of the newer code stays
            self.assertTrue(w.write_host_minute(NOW, {"n": 6}))
            self.assertTrue(w.maintain(NOW))
            w.maintain(NOW, max_mb=0.01)                            # far too small: the cap of A — not bench
            self.assertEqual(dump(w.conn, NEW_TABLES), rows)
            self.assertEqual(w.meta("schema"), str(metrics.SCHEMA))
        finally:
            w.close()
        w = metrics.open_writer()
        try:
            self.assertTrue(w.maintain(NOW))
            self.assertEqual(w.conn.execute("SELECT id FROM bench_run").fetchall(), [("a1b2c3d4e5",)])
            self.assertEqual(w.conn.execute("SELECT COUNT(*) FROM bench WHERE run = '0000000001'").fetchone(), (0,))
        finally:
            w.close()


class BenchCase(RootCase):
    def setUp(self):
        super().setUp()
        self.w = metrics.open_writer()
        self.assertIsNotNone(self.w)
        self.addCleanup(self.w.close)

    def q(self, sql, *a):
        return self.w.conn.execute(sql, a).fetchall()


class WriteBenchTest(BenchCase):
    def test_a_check_and_its_tests(self):
        self.assertTrue(self.w.write_bench(check(inv=INV, why_args={"mbps": 4.2, "code": "a.b"})))
        r = self.q("SELECT id, mode, by, status, score, links, inv, why_args, rx, under_load FROM bench_run")
        self.assertEqual(len(r), 1)
        rid, mode, by, status, score, links, inv, why_args, rx, ul = r[0]
        self.assertEqual((rid, mode, by, status, score, rx, ul), ("a1b2c3d4e5", "full", "panel", "ok", 700, 10 ** 8, 0))
        self.assertEqual(json.loads(links), {"cpu": 720, "down": 650})
        self.assertEqual(json.loads(inv)["engines"], ["awg", "vless"])
        self.assertEqual(json.loads(why_args), {"code": "a.b", "mbps": 4.2})
        t = self.q("SELECT test, value, unit, mode, ok, score, under_load FROM bench ORDER BY test")
        self.assertEqual(t, [("cpu_1t", 1200.0, "событий/с", "full", 1, 610, 0),
                             ("net_down", 620.0, "Мбит/с", "full", 1, 668, 0)])

    def test_again_and_again_the_same_rows(self):
        doc = check()
        self.assertTrue(self.w.write_bench(doc))
        doc["tests"] = doc["tests"][:1]
        self.assertTrue(self.w.write_bench(doc))                     # the check is replaced whole
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench_run"), [(1,)])
        self.assertEqual(self.q("SELECT test FROM bench"), [("net_down",)])

    def test_a_bad_file_is_none_and_writes_nothing(self):
        for doc in ({"v": 2, "run": {}}, check(rid="../../x"), check(status="good"), check(mode="slow"),
                    dict(check(), run=dict(check()["run"], ts="soon")), check(tests=[{"value": 1}]),
                    check(tests=[{"test": "a"}, {"test": "a"}]),
                    check(tests="x"), check(tests=[{"test": "t%d" % i} for i in range(65)]), [], None):
            self.assertIsNone(self.w.write_bench(doc), doc)
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench_run"), [(0,)])
        self.assertIn("a bad check file", self.read("/var/log/kaskad.log"))

    def test_the_drops_of_a_check(self):
        """§2.8 п. 6 (coordinator 19.10; NS-D33): the events bench_drop of the check, written by the one writer with it,
        replaced with it; a bad drop makes the file bad. The one condition is «rel» (rel of the link <= 50): ``now`` —
        that rel (0…300), ``test``/``value``/``usual``/``unit`` — the raw numbers of «… обычно …»; the conditions of
        the absolute scale (100, 300, «fall») are bad now."""
        doc = check()
        doc["drops"] = [{"link": "down", "cond": "rel", "day": "2030-01-02", "matched": ["rel"], "push": True,
                         "now": 40, "test": "net_down", "value": 120.5, "usual": 600.0, "unit": "Мбит/с"}]
        self.assertTrue(self.w.write_bench(doc))
        (row,) = self.q("SELECT run, link, ts, day, cond, args FROM bench_drop")
        self.assertEqual(row[:5], ("a1b2c3d4e5", "down", NOW - 600, "2030-01-02", "rel"))
        self.assertEqual(json.loads(row[5]), {"matched": ["rel"], "push": True, "auto": False, "now": 40,
                                              "test": "net_down", "value": 120.5, "usual": 600.0, "unit": "Мбит/с"})
        doc["drops"] = []
        self.assertTrue(self.w.write_bench(doc))
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench_drop"), [(0,)])
        for bad in ([{"link": "Down!", "cond": "rel"}], [{"link": "down", "cond": "x"}],
                    [{"link": "down", "cond": 300.0}], [{"link": "down", "cond": True}],
                    [{"link": "down", "cond": "rel"}, {"link": "down", "cond": "rel"}],
                    "x", [{"link": "l%d" % i, "cond": "rel"} for i in range(17)],
                    [dict(doc_drop(), matched="rel")], [dict(doc_drop(), matched=[300])],
                    [dict(doc_drop(), matched=["rel", "rel"])],
                    [dict(doc_drop(), matched=True)], [dict(doc_drop(), push="yes")], [dict(doc_drop(), now=301)],
                    [dict(doc_drop(), now=-1)], [dict(doc_drop(), test="Net Down")], [dict(doc_drop(), usual="x")],
                    [dict(doc_drop(), value="fast")], [dict(doc_drop(), unit="a\nb")], [dict(doc_drop(), day="2030")],
                    [dict(doc_drop(), run="0000000001")], [dict(doc_drop(), cond=50)], ["down"]):
            doc["drops"] = bad
            self.assertIsNone(self.w.write_bench(dict(doc, run=dict(doc["run"], id="0000000009"))), bad)
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench_run WHERE id = '0000000009'"), [(0,)])
        # NS-D34: events of the removed scale (a file of before the update) are skipped, the check is written
        doc["drops"] = [{"link": "down", "cond": 300, "now": 420}, {"link": "up", "cond": "fall", "now": 380},
                        {"link": "cpu", "cond": 100, "now": 90}]
        self.assertTrue(self.w.write_bench(dict(doc, run=dict(doc["run"], id="0000000008"))))
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench_drop WHERE run = '0000000008'"), [(0,)])

    def test_any_bad_field_makes_the_file_bad(self):
        """С-2 круг 2 + договор с B1: a known field of a wrong type or out of its range — the whole file is bad (None,
        nothing written, a line in the journal), never a half-written check; an unknown key — not kept, the file is
        written, a line once per key; null is NULL."""
        spool._told_keys.clear()
        for kw in BAD_RUN:
            self.assertIsNone(self.w.write_bench(check(**kw)), kw)
        for t in BAD_TEST:
            self.assertIsNone(self.w.write_bench(check(tests=[t])), t)
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench_run"), [(0,)])
        for i, kw in enumerate(UNKNOWN_RUN):                                   # договор с B1: kept without the key
            self.assertTrue(self.w.write_bench(check("%010x" % (i + 1), **kw)), kw)
        for i, t in enumerate(UNKNOWN_TEST):
            self.assertTrue(self.w.write_bench(check("%010x" % (i + 100), tests=[t])), t)
        self.assertTrue(self.w.write_bench(dict(check("00000000ff"), extra=1)))
        dump_ = repr(self.q("SELECT * FROM bench_run")) + repr(self.q("SELECT * FROM bench"))
        for word in ('"pad"', '"ip"', "pretty", "anything"):
            self.assertNotIn(word, dump_)
        self.assertNotIn('"x"', repr(self.q("SELECT extra FROM bench")))
        log = self.read("/var/log/kaskad.log")
        self.assertEqual(log.count("an unknown field run.pad is not kept"), 1)
        self.assertIn("an unknown field test.src is not kept", log)
        self.assertIn("an unknown field run.inv.ip is not kept", log)
        self.w.write_bench(check("00000000fe", pad="z"))
        self.assertEqual(self.read("/var/log/kaskad.log").count("run.pad"), 1)    # once per key
        nulls = check(score=None, rx=None, dur_s=None, under_load=None, sched_id=None, retry_of=None, links=None,
                      end_ts=None, meta=None, inv=None, why_args=None)
        nulls["tests"] = [{"test": "net_up", "value": None, "unit": None, "score": None, "ca": None, "extra": None}]
        self.assertTrue(self.w.write_bench(nulls))
        self.assertEqual(self.q("SELECT score, rx, dur_s, under_load, sched_id, retry_of, links, end_ts, meta FROM "
                                "bench_run WHERE id = 'a1b2c3d4e5'"), [(None,) * 9])
        self.assertEqual(self.q("SELECT value, unit, score, ca, ok, extra FROM bench WHERE run = 'a1b2c3d4e5'"),
                         [(None, None, None, None, None, None)])                # no value, ok not said: NULL

    def test_meta_and_extra_are_kept(self):
        """С-1 круг 2: what score.py reads besides the columns — bench_run.meta and bench.extra (JSON)."""
        doc = check(meta={"engines": ["vless", "awg"], "cpu_flags": ["aes", "avx2"]})
        doc["tests"] = [{"test": "loss", "value": 0.5, "extra": {"n": 600, "lo": 0.08, "points": [
            {"n": 200, "k": 1, "e": 1}]}}, {"test": "rel_port", "value": 0.4, "extra": {"src": "user"}},
            {"test": "dns", "value": 12.0, "extra": {"src": "kaskad"}},
            {"test": "net_up", "value": 5.0, "mode": "quick", "extra": {"budget": True, "wait": False, "catalog": True}}]
        self.assertTrue(self.w.write_bench(doc))
        (m,) = self.q("SELECT meta FROM bench_run")
        self.assertEqual(json.loads(m[0]), {"cpu_flags": ["aes", "avx2"], "engines": ["awg", "vless"]})
        got = dict((t, json.loads(e)) for t, e in self.q("SELECT test, extra FROM bench"))
        self.assertEqual(got["loss"], {"lo": 0.08, "n": 600, "points": [{"e": 1, "k": 1, "n": 200}]})
        self.assertEqual(got["dns"], {"src": "kaskad"})
        self.assertEqual(got["net_up"], {"budget": True, "catalog": True, "wait": False})

    def test_the_new_base_against_the_previous_good_check(self):
        """NS-S17: cpu (model or nproc), mem (± 10 %), engines — at the moment of the check; a skipped or failed check
        in between is not «the previous good»; the tariff — its own event."""
        w = self.w
        self.assertTrue(w.write_bench(check("0000000001", NOW - 9 * D, inv=INV)))
        self.assertTrue(w.write_bench(check("0000000002", NOW - 8 * D, status="failed",
                                            inv=dict(INV, cpu="Intel Xeon"))))
        self.assertTrue(w.write_bench(check("0000000003", NOW - 7 * D, inv=dict(INV, mem_gb=3.8 * 1.05))))
        self.assertEqual(self.q("SELECT * FROM bench_epoch"), [])     # +5 %: no event; the failed one: not compared
        self.assertTrue(w.write_bench(check("0000000004", NOW - 6 * D, inv=dict(INV, mem_gb=3.8 * 1.05 * 1.15))))
        self.assertTrue(w.write_bench(check("0000000005", NOW - 5 * D, inv=dict(INV, mem_gb=3.8 * 1.05 * 1.15,
                                                                               cpu="Intel Xeon"))))
        self.assertTrue(w.write_bench(check("0000000006", NOW - 4 * D, inv=dict(INV, mem_gb=3.8 * 1.05 * 1.15,
                                                                               cpu="Intel Xeon", engines=["vless"]))))
        self.assertTrue(w.add_epoch(NOW - 3 * D, "tariff", {"mbps": 1000}))
        self.assertTrue(w.add_epoch(NOW - 3 * D, "tariff", {"mbps": 1000}))   # once
        self.assertFalse(w.add_epoch(NOW, "moon"))
        got = [(ts, k) for ts, k, _a in self.q("SELECT * FROM bench_epoch ORDER BY ts, kind")]
        self.assertEqual(got, [(NOW - 6 * D, "mem"), (NOW - 5 * D, "cpu"), (NOW - 4 * D, "engines"),
                               (NOW - 3 * D, "tariff")])
        self.assertTrue(w.write_bench(check("0000000005", NOW - 5 * D, inv=dict(INV, mem_gb=3.8 * 1.05 * 1.15,
                                                                               cpu="Intel Xeon"))))
        self.assertEqual(len(self.q("SELECT * FROM bench_epoch")), 4)   # written again: no second event

    def test_a_change_through_an_unknown_value(self):
        """REV-ns-b6 З-2 (NS-S17): A(EPYC) → B(model not read) → C(Xeon) — a change of the CPU at C: the last KNOWN value
        of every key, not the neighbour; B itself is no change (unknown ≠ changed); engines the same way."""
        w = self.w
        no_cpu = dict((k, v) for k, v in INV.items() if k not in ("cpu", "engines"))
        self.assertTrue(w.write_bench(check("0000000001", NOW - 9 * D, inv=INV)))
        self.assertTrue(w.write_bench(check("0000000002", NOW - 8 * D, inv=no_cpu)))
        self.assertEqual(self.q("SELECT * FROM bench_epoch"), [])
        self.assertTrue(w.write_bench(check("0000000003", NOW - 7 * D, inv=dict(INV, cpu="Intel Xeon",
                                                                               engines=["vless"]))))
        self.assertEqual([(ts, k) for ts, k, _a in self.q("SELECT * FROM bench_epoch ORDER BY ts, kind")],
                         [(NOW - 7 * D, "cpu"), (NOW - 7 * D, "engines")])
        self.assertTrue(w.write_bench(check("0000000004", NOW - 6 * D, inv=no_cpu)))
        self.assertTrue(w.write_bench(check("0000000005", NOW - 5 * D, inv=dict(INV, cpu="Intel Xeon",
                                                                               engines=["vless"]))))
        self.assertEqual(len(self.q("SELECT * FROM bench_epoch")), 2)   # Xeon → unknown → Xeon: no change

    def test_people_hour(self):
        self.assertTrue(self.w.write_people_hour(NOW, {"conns": 312, "rtt_med": 48.2, "rtt_p90": 120.0,
                                                     "rtt_min_med": 31.0, "sent": 10 ** 9, "retr": 7 * 10 ** 6,
                                                     "dsack": 10 ** 5, "why": None,
                                                     "inputs": {"vless-1": "ok", "pptp": "udp", "x": "203.0.113.5",
                                                                "198.51.100.7:443": "ok"}}))
        self.assertTrue(self.w.write_people_hour(NOW + 60, {"why": "few", "conns": "many"}))   # the same hour: replaced
        self.assertEqual(self.q("SELECT ts, conns, why, inputs FROM host_people_h1"),
                         [(NOW // H * H, None, "few", None)])
        self.assertTrue(self.w.write_people_hour(NOW - H, {"conns": 3, "why": "?", "inputs": {"vless-1": "ok",
                                                                                         "x": "203.0.113.5"}}))
        self.assertEqual(self.q("SELECT conns, why, inputs FROM host_people_h1 WHERE ts = ?", NOW // H * H - H),
                         [(3, None, '{"vless-1": "ok"}')])          # no address in a value or a key


class PeopleHourB17Test(BenchCase):
    """The contract of B17 (wt-ns-b17 e3ba274d, gw/collect.py ``_people_flush``: ``w.write_people_hour(r["ts"], r)``;
    the row of ``hostpeople.Hour.row()``): write, read back, the same hour replaced, the base failing raises."""

    def row(self, **kw):
        r = {"ts": NOW // H * H, "conns": 312, "rtt_med": 48.25, "rtt_p90": 120.0, "rtt_min_med": 31.0,
             "sent": 6 * 10 ** 9, "retr": 4 * 10 ** 7, "dsack": 10 ** 6, "why": None,
             "inputs": {"vless": "ok", "socks5": "few", "openvpn": "udp", "sstp": "shared_port", "ocserv": "no_data"}}
        r.update(kw)
        return r

    def test_write_read_and_the_same_hour(self):
        r = self.row()
        self.assertTrue(self.w.write_people_hour(r["ts"], r))
        got = self.q("SELECT ts, conns, rtt_med, rtt_p90, rtt_min_med, sent, retr, dsack, why, inputs "
                     "FROM host_people_h1")
        self.assertEqual(got[0][:9], (NOW // H * H, 312, 48.25, 120.0, 31.0, 6 * 10 ** 9, 4 * 10 ** 7, 10 ** 6, None))
        self.assertEqual(json.loads(got[0][9]), r["inputs"])
        r2 = self.row(conns=None, rtt_med=None, rtt_p90=None, rtt_min_med=None, sent=None, retr=None, dsack=None,
                      why="no_diag", inputs={"vless": "no_data"})
        self.assertTrue(self.w.write_people_hour(r2["ts"] + 1800, r2))     # inside the same hour: replaced
        self.assertEqual(self.q("SELECT conns, why, inputs FROM host_people_h1"),
                         [(None, "no_diag", '{"vless": "no_data"}')])
        for why in metrics.PEOPLE_WHY:
            self.assertTrue(self.w.write_people_hour(NOW - 5 * H, self.row(why=why)))
            self.assertEqual(self.q("SELECT why FROM host_people_h1 WHERE ts = ?", (NOW - 5 * H) // H * H), [(why,)])

    def test_the_base_failing_raises_so_the_hour_is_kept(self):
        self.w.conn.close()
        with self.assertRaises(RuntimeError) as e:
            self.w.write_people_hour(NOW, self.row())
        self.assertNotIsInstance(e.exception, metrics.PeopleRowInvalid)   # B17 keeps the hour, does not drop it

    def test_bad_data_raises_people_row_invalid(self):
        """Круг 2 (стык с B17): a row the base can never take — PeopleRowInvalid (B17 drops it and goes on), not the
        RuntimeError of the base failing (B17 would retry it every minute in front of the others)."""
        for ts in (None, "x", float("nan"), float("inf"), 2 ** 64, 5, -1):
            with self.assertRaises(metrics.PeopleRowInvalid, msg=ts):
                self.w.write_people_hour(ts, self.row())
        with self.assertRaises(metrics.PeopleRowInvalid):          # mixed keys of inputs: TypeError of the row
            self.w.write_people_hour(NOW, self.row(inputs={1: "ok", "vless": "ok"}))
        with mock.patch.object(metrics.Writer, "_people_row", side_effect=OverflowError("int too large")):
            with self.assertRaises(metrics.PeopleRowInvalid):
                self.w.write_people_hour(NOW, self.row())
        with mock.patch.object(metrics.Writer, "_people_row", side_effect=sqlite3.IntegrityError("x")):
            with self.assertRaises(metrics.PeopleRowInvalid):
                self.w.write_people_hour(NOW, self.row())
        with mock.patch.object(metrics.Writer, "_people_row", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(RuntimeError) as e:
                self.w.write_people_hour(NOW, self.row())
            self.assertNotIsInstance(e.exception, metrics.PeopleRowInvalid)
        self.assertTrue(issubclass(metrics.PeopleRowInvalid, ValueError))
        self.assertEqual(self.q("SELECT COUNT(*) FROM host_people_h1"), [(0,)])
        self.assertTrue(self.w.write_people_hour(NOW, self.row()))      # the writer goes on

    def test_partial_and_the_reasons_of_b17_round_2(self):
        """B17 круг 2 (a757c4f2, hostpeople.Hour.row()): ``partial`` — samples of the hour that did not see everything;
        the reasons shared_443 and no_cgroup are kept (not NULL)."""
        r = self.row(conns=None, rtt_med=None, rtt_p90=None, rtt_min_med=None, sent=None, retr=None, dsack=None,
                     why="shared_443", inputs={"vless": "shared_port"}, partial=0)
        self.assertTrue(self.w.write_people_hour(r["ts"], r))
        r2 = self.row(why=None, partial=3)
        self.assertTrue(self.w.write_people_hour(r2["ts"] - H, r2))
        r3 = self.row(conns=None, why="no_cgroup", inputs={"vless": "no_data"}, partial=1)
        self.assertTrue(self.w.write_people_hour(r3["ts"] - 2 * H, r3))
        self.assertEqual(self.q("SELECT ts, partial, why, conns FROM host_people_h1 ORDER BY ts"),
                         [(NOW // H * H - 2 * H, 1, "no_cgroup", None), (NOW // H * H - H, 3, None, 312),
                          (NOW // H * H, 0, "shared_443", None)])


class KeepCapTest(BenchCase):
    def test_kept_400_and_90_days(self):
        w = self.w
        for i, age in enumerate((401, 399)):
            self.assertTrue(w.write_bench(check("00000000%02d" % i, NOW - age * D)))
            self.assertTrue(w.add_epoch(NOW - age * D, "tariff"))
        for age in (91, 89):
            self.assertTrue(w.write_people_hour(NOW - age * D, {"conns": age}))
        with w.conn:                                                 # rows whose check is long gone
            w.conn.execute("INSERT INTO bench (run, test, ts) VALUES ('ffffffffff', 'x', ?)", (NOW - 402 * D,))
            w.conn.execute("INSERT INTO bench_drop (run, link, ts) VALUES ('ffffffffff', 'x', ?)", (NOW - 402 * D,))
            w.conn.execute("INSERT INTO bench_drop (run, link, ts) VALUES ('0000000000', 'down', ?)", (NOW - 401 * D,))
            w.conn.execute("INSERT INTO bench_drop (run, link, ts) VALUES ('0000000001', 'down', ?)", (NOW - 399 * D,))
        self.assertTrue(w.maintain(NOW))
        self.assertEqual(self.q("SELECT id FROM bench_run"), [("0000000001",)])
        self.assertEqual(self.q("SELECT DISTINCT run FROM bench"), [("0000000001",)])
        self.assertEqual(self.q("SELECT run FROM bench_drop"), [("0000000001",)])
        self.assertEqual(self.q("SELECT ts FROM bench_epoch"), [(NOW - 399 * D,)])
        self.assertEqual(self.q("SELECT conns FROM host_people_h1"), [(89,)])

    def test_cap_cuts_bench_last_and_keeps_90_days(self):
        w = self.w
        with w.conn:
            for i in range(90 * 24):                                 # h1 of an exit, fat rows
                w.conn.execute("INSERT INTO h1 (exit, ts, state) VALUES ('e-a', ?, ?)", (NOW - 90 * D + i * H, "x" * 300))
        for i in range(400):
            doc = check("%010x" % i, NOW - (400 - i) * D + 60)
            doc["drops"] = [{"link": "down", "cond": "rel", "day": "2030-01-02"}]
            self.assertTrue(w.write_bench(doc))
        runs = self.q("SELECT COUNT(*) FROM bench_run")[0][0]
        cap = (w.used_bytes() - 0.6 * w._size("h1")) / 1048576.0     # cutting h1 alone is enough
        w.maintain(NOW, max_mb=cap)
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench_run")[0][0], runs)   # h1 gave first
        w.maintain(NOW, max_mb=0.01)                                  # far too small: everything to its floor
        self.assertGreaterEqual(self.q("SELECT MIN(ts) FROM bench_run")[0][0], NOW - 90 * D)
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench_run")[0][0], 90)
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench WHERE run NOT IN (SELECT id FROM bench_run)"), [(0,)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench_drop WHERE run NOT IN (SELECT id FROM bench_run)"), [(0,)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench_drop"), [(90,)])
        self.assertIn("bench 90 days", self.read("/var/log/kaskad.log"))

    def test_cap_cuts_bench_last_even_when_it_is_the_biggest(self):
        """З-3 круг 2: bench_run (with bench) bigger than any other table, cutting h1 to its floor is enough — the
        history of the checks is not touched (a cap that cut the biggest first would take it)."""
        w = self.w
        with w.conn:
            for i in range(30 * 24):                                 # h1: 30 days, 23 of them above its floor of 7
                w.conn.execute("INSERT INTO h1 (exit, ts, state) VALUES ('e-a', ?, ?)", (NOW - 30 * D + i * H, "x" * 350))
        for i in range(400):
            self.assertTrue(w.write_bench(check("%010x" % i, NOW - (400 - i) * D + 60, inv=INV, tests=[
                {"test": "t%02d" % j, "value": 1.5 + j, "unit": "Мбит/с", "extra": {"n": 600, "lo": 0.08}}
                for j in range(15)])))
        self.assertGreater(w._size("bench_run"), 1.5 * w._size("h1"))
        runs = self.q("SELECT COUNT(*), (SELECT COUNT(*) FROM bench) FROM bench_run")
        cap = (w.used_bytes() - 0.6 * w._size("h1")) / 0.9               # the cap cuts down to 90 % of itself
        self.assertLess(cap, w.used_bytes())                              # over it: something must go
        w.maintain(NOW, max_mb=cap / 1048576.0)
        self.assertLess(self.q("SELECT COUNT(*) FROM h1")[0][0], 30 * 24)          # h1 gave
        self.assertEqual(self.q("SELECT COUNT(*), (SELECT COUNT(*) FROM bench) FROM bench_run"), runs)

    def test_volume_400_days(self):
        """SS §3.1 «Объём» (П-4 круг 2): 8 checks a day × 400 days × 15 tests with every field (extra of the loss and
        the jitter with their points, the channel's corr/people/budget; договор с B1: the whole inventory of ~110 cpu
        flags and the series in meta, why_args with forced) — 11.53 MB measured (6.15 without the inventory); ≲ 13 MB."""
        w = self.w
        n = 0
        names = ("net_down", "net_up", "rtt", "loss", "jitter", "dns", "cpu_1t", "cpu_mt", "chacha_1t", "aes_1t",
                 "steal_p95", "cpu_cv", "mem_bw", "disk_rr", "disk_fsync")
        for d in range(400):
            for k in range(8):
                tests = []
                for j, tid in enumerate(names):
                    x = {"test": tid, "value": 123.456 + j + d, "unit": "Мбит/с", "score": 500 + j, "ca": "bbr"}
                    if tid in ("loss", "jitter"):
                        x["extra"] = {"n": 25920.0, "lo": 1.7228374084401805,
                                      "points": [{"e": 169.344, "k": 172.8, "n": 8640.0}] * 3}
                    elif tid in ("net_down", "net_up"):
                        x.update(extra={"budget": False}, value_corr=600.5, people=12.5)
                    elif tid == "dns":
                        x["extra"] = {"src": "kaskad"}
                    tests.append(x)
                doc = check("%010x" % n, NOW - d * D - k * 3 * H, tests=tests, inv=FULL_INV,
                            why_args={"forced": {"mbps": 4.2, "thr_mbps": 2, "cpu": 31.0, "thr_cpu": 20, "io": None,
                                                 "thr_io": 20}, "people_max": 3.1, "cpu_max": 40.5})
                doc["series"] = SERIES
                self.assertTrue(w.write_bench(doc))
                n += 1
        self.assertEqual(n, 3200)
        self.assertLess(w.used_bytes(), 13 * 1048576)
        self.assertLess(abs(w._size("bench_run") - w.used_bytes()), 0.25 * w.used_bytes())   # the estimate of the cap


class ReadOnlyTest(RootCase):
    def test_reader_cannot_write(self):
        w = metrics.open_writer()
        w.close()
        conn = metrics.reader()
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO bench_epoch VALUES (1, 'cpu', '{}')")
