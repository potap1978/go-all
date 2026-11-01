"""The way of metrics.db between the releases (the owner 24.10: «важно — мигрирование, чтобы не было косяков»;
k3/handoff/SPEC-activity.md §4, «Сверка 24.10»). A base lived by the code of 3.3.7.6 (SCHEMA 2) or of 3.3.7.8
(SCHEMA 3) — days of exits, of the node and of its probes, (3.3.7.8) the checks of the node and «Связь с людьми» —
goes up to SCHEMA 4 whole: every row in place, the charts and the check of the node read it as before, the new tables
are empty and fill, the cap counts them and cuts them to their floors only. The code of 3.3.7.8 (and of 3.3.7.6) opens
the base of 4 and works on it — its writer, its charts, the checks — never touching dev_*. A way up failed or killed
in the middle leaves the old base as it was, the next start does it. A new install is SCHEMA 4 at once."""

import os
import sqlite3
import subprocess
import sys
import time

from kaskad.bench import history as bhistory, hostscore
from kaskad.gw import metrics
from tests.gw.test_metrics_activity import B, DEV, NOW, OLD_TABLES, UTC, dump, stage_3, tables, win
from tests.gw.test_metrics_bench import INV, check, stage_a
from tests.helpers import RootCase

H = 3600
D = 86400
EXITS = ("e-1a2b3c4d5e", "e-6f7a8b9c0d")
DEVS = ("d-1a2b3c4d", "d-5e6f7a8b")
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
TABLES_2 = ("m1", "m10", "h1", "speed", "svc", "geo", "traffic", "events") + metrics.HOST_TABLES + metrics.PING_TABLES
TABLES_3 = ("bench_run", "bench", "bench_epoch", "bench_drop", "host_people_h1")


def live(mod, start, end, bench=False):
    """The collector of that code from ``start`` to ``end``: a minute of each exit, of the node and of its probes every
    2 minutes; every hour the maintenance, a speed test, the services, an event; the place and the traffic a day, a move
    of an exit now and then; with ``bench`` (SCHEMA 3) — a check of the node every 6 hours, «Связь с людьми» every hour
    and the epoch of the tariff. The rows are of the shape the collector writes."""
    w = mod.open_writer()
    assert w is not None
    try:
        k = 0
        for ts in range(start - start % 120, end, 120):        # on the grid: the hours and the days come
            k += 1
            w.write_minute(ts, dict((e, {"rtt_med": 40.0 + 30 * i + k % 17, "rtt_p90": 80.0 + k % 23,
                                         "loss": 2.5 if k % 50 == 0 else 0.0, "jitter": 3.0 + k % 5, "ok": 6, "n": 6,
                                         "rx": 10 ** 6 * (1 + k % 7), "tx": 10 ** 5 * (1 + k % 3), "devices": 1 + k % 4,
                                         "state": "degraded" if k % 97 == 0 else "ok"}) for i, e in enumerate(EXITS)))
            w.write_host_minute(ts, {"n": 6, "busy": float(k % 40), "busy_max": float(k % 40 + 10), "steal": 0.5,
                                     "iowait": 1.0, "load_core": 0.3, "mem_avail": 60.0 + k % 10, "mem_avail_min": 55.0,
                                     "disk": 40.0, "disk_free": 10 ** 10, "rx": 10 ** 7, "tx": 10 ** 6, "pkts": 9000,
                                     "clock_ok": 1, "clock_off": 0.01, "canary_ms": 3.0 if k % 5 == 0 else None,
                                     "level": 1 if k % 61 == 0 else 0})
            w.write_ping_minute(ts, {"*": {"via": "icmp", "n": 9, "lost": 1.0 if k % 31 == 0 else 0.0, "runs": 1,
                                           "rtt_med": 12.5 + k % 3, "rtt_p90": 20.0, "jit": 1.5, "jit_n": 8},
                                     "dns": {"via": "dns", "n": 3, "lost": 0.0, "runs": 1, "rtt_med": 30.0}})
            if ts % H == 0:
                for e in EXITS:
                    w.add_speed(e, ts, 80.0 + k % 9, 120.0, 30.0, 10 ** 6, "speed.example.com")
                    w.add_svc(e, "youtube", ts, k % 13 != 0, 200, 150.0)
                w.add_event(ts, "host_cpu" if k % 2 else "exit_slow", EXITS[k % 2], {"level": 1})
                if bench:
                    w.write_people_hour(ts - H, {"conns": 30 + k % 20, "rtt_med": 48.0, "rtt_p90": 120.0})
                    if ts % (6 * H) == 0:
                        doc = check("%010x" % ts, ts - 600, inv=INV)
                        if ts % D == 0:                      # a check a day with a drop of the channel down
                            doc["drops"] = [{"link": "down", "cond": "rel", "matched": ["rel"], "push": True, "now": 40,
                                             "day": time.strftime("%Y-%m-%d", time.gmtime(ts)), "test": "net_down",
                                             "value": 120.5, "usual": 600.0, "unit": "Мбит/с"}]
                        assert w.write_bench(doc)
                w.maintain(ts)
            if ts % D == 0:
                day = time.strftime("%Y-%m-%d", time.gmtime(ts))
                for e in EXITS:
                    w.add_geo(e, ts, "203.0.113.%d" % (10 + EXITS.index(e)), "FI")
                    w.add_traffic(e, day, 10 ** 9, 10 ** 8, 10 ** 6)
                w.add_transition(ts + 60, EXITS[0], "ok", "degraded", "loss", {"loss": 12.0})
                w.add_transition(ts + 600, EXITS[0], "degraded", "ok")
        if bench:
            assert w.add_epoch(start + H, "tariff", {"mbps": 1000})
        assert w.maintain(end)
    finally:
        w.close()


def n_checks(start, end):
    """How many checks ``live(…, bench=True)`` writes from ``start`` to ``end``."""
    return len([t for t in range(start - start % 120, end, 120) if t % (6 * H) == 0])


def reads(mod, now):
    """What the charts of the exits and of the node read (the panel, the CLI): by the code of ``mod``."""
    out = {}
    for e in EXITS:
        for rng in ("1d", "7d", "90d"):
            out["history", e, rng] = mod.history(e, rng, now=now)
        out["transitions", e] = mod.transitions(e, days=7, now=now)
    for rng in ("1d", "7d", "90d"):
        out["host", rng] = mod.host_history(rng, now=now)
        out["ping", rng] = mod.ping_history(rng, now=now)
    out["feed"] = mod.feed(0)
    return out


def checks(now):
    """What the check of the node reads (Обзор › Проверка сервера): the history of the checks, one check, the rows of
    the days (bench.hostscore.read — the same code in 3.3.7.8 and now: it reads through metrics.reader())."""
    bhistory.reset()
    hostscore.reset()
    runs = bhistory.runs(now=now, lang="ru")
    one = bhistory.run(runs["runs"][0]["id"], lang="ru") if runs.get("runs") else None
    return {"runs": runs, "one": one, "details": bhistory.details(now=now, lang="ru"),
            "read": hostscore.read(now - 3 * D, now)}


def schema(conn):
    return conn.execute("SELECT v FROM meta WHERE k = 'schema'").fetchone()[0]


def count(conn, t):
    return conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]


class UpCase(RootCase):
    def base(self):
        return self.path("/var/lib/kaskad/metrics.db")

    def raw(self):
        return sqlite3.connect(self.base())

    def snapshot(self, names):
        conn = self.raw()
        try:
            return dump(conn, names), dict(conn.execute("SELECT k, v FROM meta"))
        finally:
            conn.close()

    def assert_up_whole(self, names, rows, meta):
        """SCHEMA 4 now; every old row and every meta in place (the number aside); the new tables there and empty."""
        w = metrics.open_writer()
        self.assertIsNotNone(w)
        try:
            self.assertEqual(w.meta("schema"), "4")
            after = dump(w.conn, names)
            for t in names:
                self.assertEqual(after[t], rows[t], t)
            now_meta = dict(w.conn.execute("SELECT k, v FROM meta"))
            self.assertEqual(dict(now_meta, schema=meta["schema"]), dict(meta, tz=now_meta["tz"]))
            for t in DEV:
                self.assertIn(t, tables(w.conn))
                self.assertEqual(count(w.conn, t), 0, t)
        finally:
            w.close()


class From3378Test(UpCase):
    """3.3.7.8 → 3.3.7.9: SCHEMA 3 → 4."""

    def test_a_base_lived_by_3_3_7_8_goes_up_whole_and_reads_as_before(self):
        old = stage_3()
        live(old, NOW - 3 * D, NOW, bench=True)
        rows, meta = self.snapshot(OLD_TABLES)
        self.assertEqual(meta["schema"], "3")
        for t in OLD_TABLES:
            self.assertTrue(rows[t], t)                         # a base with a history in every table
        before, seen = reads(old, NOW), checks(NOW)
        self.assertEqual(len(seen["runs"]["runs"]), n_checks(NOW - 3 * D, NOW))
        self.assertTrue(seen["read"]["ok"] and seen["read"]["people"] and seen["read"]["host"])
        self.assertTrue(before["history", EXITS[0], "7d"] and before["host", "7d"] and before["feed"])

        self.assert_up_whole(OLD_TABLES, rows, meta)
        self.assertEqual(reads(metrics, NOW), before)          # the charts of the exits and of the node as before
        self.assertEqual(checks(NOW), seen)                    # the check of the node as before

        # the new tables fill, the old ones go on
        w = metrics.open_writer()
        try:
            for i in (2, 1):
                self.assertTrue(w.write_dev(win(B - 300 * i, {DEVS[0]: [300, 60, 10 ** 6, 10 ** 7]}), UTC))
            self.assertTrue(w.write_minute(NOW + 60, {EXITS[0]: {"rtt_med": 50.0, "n": 6, "ok": 6, "state": "ok"}}))
            self.assertTrue(w.write_bench(check("00000000ff", NOW - 60, inv=INV)))
            self.assertTrue(w.maintain(NOW + 120))
            self.assertEqual([count(w.conn, t) for t in DEV], [2, 1, 1, 3])
        finally:
            w.close()
        s = metrics.dev_summary(list(DEVS), now=B, flush=B - 300)
        self.assertEqual(s["devices"][DEVS[0]]["rx"], 2 * 10 ** 6)
        self.assertEqual(len(checks(NOW)["runs"]["runs"]), n_checks(NOW - 3 * D, NOW) + 1)

    def test_the_cap_counts_the_new_tables(self):
        """The new tables are in the size of the base; over the limit the cap cuts dev_h1 (with the hours of the node)
        to 7 days and dev_d1 to 90 — never the old history under its floors."""
        live(stage_3(), NOW - 3 * D, NOW, bench=True)
        w = metrics.open_writer()
        try:
            empty = w.used_bytes()
            devs = ["d-%08x" % i for i in range(60)]
            hours = [NOW // H * H - i * H for i in range(1, 30 * 24)]
            days = [NOW // D * D - i * D for i in range(1, 400)]
            with w.conn:
                w.conn.executemany("INSERT INTO dev_h1 VALUES (?,?,?,?,?,?)",
                                   [(ts, d, 3600, 600, 10 ** 8, 10 ** 7) for ts in hours for d in devs])
                w.conn.executemany("INSERT INTO dev_node VALUES (3600, ?, 360, 3600)", [(ts,) for ts in hours])
                w.conn.executemany("INSERT INTO dev_d1 VALUES (?,?,?,?,?,?)",
                                   [(ts, d, 86400, 7200, 10 ** 9, 10 ** 8) for ts in days for d in devs])
            full = w.used_bytes()
            self.assertGreater(full - empty, 1024 * 1024)      # the new tables count in the size of the base
            old_rows = dump(w.conn, OLD_TABLES)
            self.assertTrue(w.maintain(NOW, max_mb=0.01))       # far too small: everything to its floor
            lo_h, = w.conn.execute("SELECT MIN(ts) FROM dev_h1").fetchone()
            lo_n, = w.conn.execute("SELECT MIN(ts) FROM dev_node WHERE step = 3600").fetchone()
            lo_d, = w.conn.execute("SELECT MIN(ts) FROM dev_d1").fetchone()
            self.assertGreaterEqual(lo_h, NOW - 7 * D - H)
            self.assertGreaterEqual(lo_n, NOW - 7 * D - H)
            self.assertGreaterEqual(lo_d, NOW - 90 * D - D)
            self.assertLess(lo_d, NOW - 89 * D)                 # the floor holds: 90 days of totals stay
            self.assertLess(w.used_bytes(), full)
            self.assertEqual(dump(w.conn, OLD_TABLES), old_rows)  # 3 days of history: under every floor, whole
        finally:
            w.close()
        self.assertIn("dev_h1, dev_d1)", self.read("/var/log/kaskad.log"))


class From3376Test(UpCase):
    """3.3.7.6 → 3.3.7.9 over a release: SCHEMA 2 → 4 at once (the tables of 3 and of 4 together)."""

    def test_a_base_lived_by_3_3_7_6_goes_up_whole_and_reads_as_before(self):
        old = stage_a()
        live(old, NOW - 3 * D, NOW)
        rows, meta = self.snapshot(TABLES_2)
        self.assertEqual(meta["schema"], "2")
        self.assertFalse(set(TABLES_3 + DEV) & set(rows))
        before = reads(old, NOW)

        self.assert_up_whole(TABLES_2, rows, meta)
        self.assertEqual(reads(metrics, NOW), before)
        w = metrics.open_writer()
        try:
            for t in TABLES_3:
                self.assertEqual(count(w.conn, t), 0, t)       # the tables of 3.3.7.8 came too, empty
            self.assertTrue(w.write_bench(check("00000000aa", NOW - 600, inv=INV)))
            self.assertTrue(w.write_people_hour(NOW - H, {"conns": 40, "rtt_med": 48.0}))
            self.assertTrue(w.write_dev(win(B - 300, {DEVS[1]: [300, 300, 5, 5]}), UTC))
            self.assertTrue(w.maintain(NOW))
        finally:
            w.close()
        seen = checks(NOW)
        self.assertEqual([r["id"] for r in seen["runs"]["runs"]], ["00000000aa"])
        self.assertEqual(len(seen["read"]["people"]), 1)
        self.assertIn(DEVS[1], metrics.dev_summary([DEVS[1]], now=B, flush=B - 300)["devices"])


class BackTest(UpCase):
    """3.3.7.9 → 3.3.7.8 (and 3.3.7.6): the old code on a base of 4 — no error, its writer, charts and checks as on its
    own base, dev_* never touched (its cap too); up again — everything of both in place."""

    def test_the_code_of_3_3_7_8_on_a_base_of_4(self):
        old = stage_3()
        live(old, NOW - 2 * D, NOW - 2 * H, bench=True)        # 3.3.7.8 lives …
        live(metrics, NOW - 2 * H, NOW - H, bench=True)        # … 3.3.7.9 an hour, with its windows of the activity
        w = metrics.open_writer()
        try:
            for i in range(12, 0, -1):
                self.assertTrue(w.write_dev(win(B - H - 300 * i, dict((d, [300, 30, 10 ** 6, 10 ** 5]) for d in DEVS)),
                                            UTC))
            dev = dump(w.conn, DEV)
        finally:
            w.close()
        mine = reads(metrics, NOW - H)

        w = old.open_writer()                                 # back to 3.3.7.8
        self.assertIsNotNone(w)
        try:
            self.assertEqual(w.meta("schema"), "4")            # the number only grows
            self.assertEqual(reads(old, NOW - H), mine)        # its charts read the same
            self.assertTrue(checks(NOW - H)["read"]["ok"])
        finally:
            w.close()
        live(old, NOW - H, NOW, bench=True)                    # its collector an hour on the base of 4
        w = old.open_writer()
        try:
            self.assertTrue(w.maintain(NOW, max_mb=0.01))      # far too small: its cap — not dev_*
            self.assertEqual(dump(w.conn, DEV), dev)
            self.assertEqual(w.meta("schema"), "4")
            self.assertEqual(w.meta("dev_flush"), str(B - H - 300))
        finally:
            w.close()
        back = reads(old, NOW)
        self.assertEqual(len(checks(NOW)["runs"]["runs"]), n_checks(NOW - 2 * D, NOW))

        w = metrics.open_writer()                             # up again
        try:
            self.assertEqual(w.meta("schema"), "4")
            self.assertEqual(dump(w.conn, DEV), dev)
            self.assertTrue(w.write_dev(win(B, {DEVS[0]: [300, 0, 1, 1]}), UTC))
            self.assertTrue(w.maintain(NOW))
        finally:
            w.close()
        self.assertEqual(reads(metrics, NOW), back)            # what 3.3.7.8 wrote meanwhile is read
        self.assertIn(DEVS[1], metrics.dev_summary(list(DEVS), now=B + 300, flush=B)["devices"])

    def test_the_code_of_3_3_7_6_on_a_base_of_4(self):
        live(metrics, NOW - D, NOW - H, bench=True)
        w = metrics.open_writer()
        try:
            self.assertTrue(w.write_dev(win(B - H, {DEVS[0]: [300, 30, 10 ** 6, 10 ** 5]}), UTC))
            keep = dump(w.conn, TABLES_3 + DEV)
        finally:
            w.close()
        old = stage_a()
        mine = reads(metrics, NOW - H)
        self.assertEqual(reads(old, NOW - H), mine)
        live(old, NOW - H, NOW)
        w = old.open_writer()
        try:
            self.assertEqual(w.meta("schema"), "4")
            self.assertTrue(w.maintain(NOW, max_mb=0.01))
            self.assertEqual(dump(w.conn, TABLES_3 + DEV), keep)
        finally:
            w.close()


KILL = r"""
import os, sys
sys.path[:0] = [sys.argv[1]]
from kaskad.gw import metrics


class KillAfter(list):
    def __iter__(self):
        for q in list.__iter__(self):
            yield q
            if " dev_m5 " in q:                     # dev_m5 made, the rest not yet: the power goes
                os._exit(9)


metrics._DDL = KillAfter(metrics._DDL)
metrics.open_writer(sys.argv[2])
os._exit(0)
"""


class BrokenWayUpTest(UpCase):
    """The way up in ONE transaction: a failure or a kill in the middle — the old base as it was (the number, no part
    of the new tables), its code goes on, the next start does it all."""

    def setUp(self):
        super().setUp()
        live(stage_3(), NOW - 6 * H, NOW, bench=True)
        self.rows, self.meta = self.snapshot(OLD_TABLES)

    def assert_as_it_was(self):
        conn = self.raw()
        try:
            self.assertEqual(schema(conn), "3")
            self.assertFalse(set(DEV) & tables(conn))          # not dev_m5 alone
            self.assertEqual(dump(conn, OLD_TABLES), self.rows)
        finally:
            conn.close()
        w = stage_3().open_writer()                           # 3.3.7.8 goes on on it
        self.assertIsNotNone(w)
        try:
            self.assertEqual(w.meta("schema"), "3")
            self.assertTrue(w.write_host_minute(NOW, {"n": 6}))
            self.assertTrue(w.maintain(NOW))
        finally:
            w.close()
        self.rows, self.meta = self.snapshot(OLD_TABLES)

    def test_a_kill_in_the_middle(self):
        r = subprocess.run([sys.executable, "-c", KILL, LIB, self.base()], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=dict(os.environ))
        self.assertEqual(r.returncode, 9, r.stderr.decode("utf-8", "replace")[-2000:])
        self.assert_as_it_was()
        self.assert_up_whole(OLD_TABLES, self.rows, self.meta)  # the next start: all of it

    def test_a_failure_in_the_middle(self):
        bad = []
        for q in metrics._DDL:
            bad.append(q)
            if " dev_m5 " in q:
                bad.append("CREATE TABLE dev_h1 (")              # a broken statement after dev_m5
        real = metrics._DDL
        metrics._DDL = bad
        try:
            self.assertIsNone(metrics.open_writer())
        finally:
            metrics._DDL = real
        self.assertIn("GW metrics.db: does not open, charts off", self.read("/var/log/kaskad.log"))
        self.assert_as_it_was()
        self.assert_up_whole(OLD_TABLES, self.rows, self.meta)


class NewInstallTest(UpCase):
    def test_a_new_base_is_4_at_once(self):
        self.assertFalse(os.path.exists(self.base()))
        w = metrics.open_writer()
        try:
            self.assertEqual(w.meta("schema"), "4")
            self.assertTrue(set(OLD_TABLES + DEV + ("meta",)) <= tables(w.conn))
            for t in OLD_TABLES + DEV:
                self.assertEqual(count(w.conn, t), 0, t)
            self.assertIsNotNone(w.meta("created"))
        finally:
            w.close()
        self.assertEqual(oct(os.stat(self.base()).st_mode & 0o777), "0o600")
        s = metrics.dev_summary(list(DEVS), now=B)
        self.assertEqual((s["activity"], s["devices"], s["since"]), (True, {}, None))
        self.assertEqual(checks(NOW)["runs"]["runs"], [])
        w = stage_3().open_writer()                           # and 3.3.7.8 opens it too
        try:
            self.assertEqual(w.meta("schema"), "4")
        finally:
            w.close()
