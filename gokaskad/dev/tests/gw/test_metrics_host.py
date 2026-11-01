"""The health of the node in metrics.db — SCHEMA 2 (k3/handoff/SPEC-node-score.md §3.4, §4 A4): the tables host_* and
host_ping_*, the way up from the base of stage 1 and back to its code, thinning, keeping, the cap, the reads."""

import hashlib
import json
import os
import random
import sqlite3
import types
from unittest import mock

from kaskad import paths
from kaskad.gw import metrics
from tests.helpers import RootCase

H = 3600
D = 86400
NOW = 1760000000 // H * H + 1800                  # half past an hour
STAGE1 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "gw",
                      "metrics_schema1.py.txt")
STAGE1_BLOB = "005bb2d8c3be4de5fb21ae74ab63b46492aee525"   # git rev-parse 122c90a:lib/kaskad/gw/metrics.py (= f0de781)
S1_TABLES = ("m1", "m10", "h1", "speed", "svc", "geo", "traffic", "events")


def stage1():
    """The code of stage 1 (SCHEMA 1) as it is in dev-s1 122c90a — a module of its own next to kaskad.gw.metrics."""
    with open(STAGE1, encoding="utf-8") as f:
        code = compile(f.read(), STAGE1, "exec")
    mod = types.ModuleType("kaskad.gw._metrics_schema1")
    mod.__package__ = "kaskad.gw"
    mod.__file__ = STAGE1
    exec(code, mod.__dict__)
    return mod


def host(**kw):
    r = {"n": 6, "busy": 10.0, "busy_max": 10.0, "steal": 0.0, "iowait": 1.0, "softirq": 0.5, "load_core": 0.5,
         "psi_cpu": 1.0, "psi_mem": 0.0, "psi_io": 0.0, "mem_avail": 70.0, "mem_avail_min": 60.0, "swap": None,
         "oom": 0, "disk": 40.0, "disk_free": 2000, "inodes": 10.0, "rx": 1000, "tx": 100, "rx_peak": 5.0,
         "tx_peak": 1.0, "pkts": 50, "drops": 0, "errs": 0, "softnet": 0, "retrans": 1, "out_segs": 100, "listen": 0,
         "conntrack": 3.0, "fd": 1.0, "clock_ok": 1, "clock_off": 0.0, "canary_ms": None, "level": 0, "warn_min": 0,
         "alarm_min": 0}
    r.update(kw)
    return r


def ping(**kw):
    r = {"via": "icmp", "n": 6, "lost": 0.0, "runs": 0, "rtt_med": 20.0, "rtt_p90": 25.0, "jit": 2.0, "jit_n": 4,
         "excl": 0, "outage": 0}
    r.update(kw)
    return r


def rand_host(rnd):
    """A minute of the node as a live one: fractional percents, scattered counters (REV-ns-a4 З-2)."""
    r = {}
    for n, typ, _ in metrics.HOST_COLS:
        if n in ("clock_ok", "warn_min", "alarm_min", "oom", "level"):
            r[n] = rnd.choice((0, 0, 0, 1))
        elif n == "n":
            r[n] = 6
        elif n == "canary_ms":
            r[n] = None
        elif n in ("rx", "tx"):
            r[n] = rnd.randint(10 ** 8, 10 ** 10)
        elif n == "disk_free":
            r[n] = rnd.randint(10 ** 9, 5 * 10 ** 10)
        elif typ == "INTEGER":
            r[n] = rnd.randint(0, 10 ** 5)
        else:
            r[n] = round(rnd.random() * 100, 3)
    return r


def rand_ping(rnd):
    return {"via": rnd.choice(("icmp", "dns")), "n": 6, "lost": float(rnd.randint(0, 2)), "runs": 0,
            "rtt_med": round(rnd.random() * 200, 3), "rtt_p90": round(rnd.random() * 250, 3),
            "jit": round(rnd.random() * 30, 3), "jit_n": rnd.randint(1, 4), "excl": 0, "outage": 0}


def dump(conn, tables):
    return dict((t, conn.execute("SELECT * FROM %s ORDER BY 1, 2" % t).fetchall()) for t in tables)


def tables(conn):
    return set(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"))


class FrozenStage1Test(RootCase):
    def test_the_copy_is_the_code_of_stage1(self):
        with open(STAGE1, "rb") as f:
            data = f.read()
        self.assertEqual(hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), STAGE1_BLOB)
        self.assertEqual(stage1().SCHEMA, 1)


class WayUpTest(RootCase):
    """§3.4 «Откуда берётся база», paths (1)–(3) (REV С-7)."""

    def open_new(self):
        w = metrics.open_writer()
        self.assertIsNotNone(w)
        self.addCleanup(w.close)
        return w

    def test_1_no_base_yet(self):
        w = self.open_new()
        self.assertEqual(w.meta("schema"), str(metrics.SCHEMA))
        self.assertTrue(set(S1_TABLES + metrics.HOST_TABLES + metrics.PING_TABLES + ("meta", "events_ts"))
                        <= tables(w.conn))
        for t in metrics.HOST_TABLES:
            cols = [r[1] for r in w.conn.execute("PRAGMA table_info(%s)" % t)]
            self.assertEqual(cols, ["ts"] + [c[0] for c in metrics.HOST_COLS])
            self.assertEqual(cols[-2:], ["warn_min", "alarm_min"])
        for t in metrics.PING_TABLES:
            sql = w.conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (t,)).fetchone()[0]
            self.assertIn("PRIMARY KEY (anchor, ts)) WITHOUT ROWID", sql)

    def fill_stage1(self, s1):
        w = s1.open_writer()
        self.assertIsNotNone(w)
        try:
            for i in range(90):
                w.write_minute(NOW - 2 * H + i * 60, {"e-a": {"rtt_med": 10.0 + i, "n": 6, "ok": 6, "rx": 5,
                                                               "state": "ok"}})
            w.add_speed("e-a", NOW - 600, 88.5, 120.0, 30.0, 10 ** 6, "speed.example.com")
            w.add_svc("e-a", "yt", NOW - 500, True, 200, 140.0)
            w.add_geo("e-a", NOW - 400, "198.51.100.1", "DE")
            w.add_traffic("e-a", "2030-05-10", 10, 20, 1)
            w.add_event(NOW - 300, "exit_dead", "e-a", {"id": "e-a", "name": "Вена"})
            w.add_transition(NOW - 200, "e-a", "ok", "off", "dead")
            self.assertTrue(w.maintain(NOW))                     # thinned: m10, h1 and the marks in meta
            self.assertEqual(w.meta("schema"), "1")
            self.assertFalse(set(metrics.HOST_TABLES + metrics.PING_TABLES) & tables(w.conn))
            return dump(w.conn, S1_TABLES + ("meta",))
        finally:
            w.close()

    def test_2_base_of_stage1_goes_up(self):
        before = self.fill_stage1(stage1())
        self.assertTrue(before["m10"] and before["h1"] and before["speed"] and before["events"])
        w = metrics.open_writer()
        try:
            self.assertEqual(w.meta("schema"), str(metrics.SCHEMA))
            after = dump(w.conn, S1_TABLES + ("meta",))
            for t in S1_TABLES:
                self.assertEqual(after[t], before[t], t)          # every row in place, no ALTER
            meta = dict(after["meta"])
            self.assertEqual(dict(before["meta"]), dict(meta, schema="1"))   # only the number moved (created kept)
            self.assertTrue(set(metrics.HOST_TABLES + metrics.PING_TABLES) <= tables(w.conn))
            self.assertTrue(w.write_host_minute(NOW, host()))
            self.assertTrue(w.write_ping_minute(NOW, {"*": ping()}))
            self.assertTrue(w.maintain(NOW))                       # the new maintain keeps what stage 1 kept
            self.assertEqual(dump(w.conn, S1_TABLES), dict((t, before[t]) for t in S1_TABLES))
            again = dump(w.conn, S1_TABLES + metrics.HOST_TABLES + metrics.PING_TABLES + ("meta",))
        finally:
            w.close()
        w = metrics.open_writer()                                   # opened again: nothing changes
        try:
            self.assertEqual(dump(w.conn, S1_TABLES + metrics.HOST_TABLES + metrics.PING_TABLES + ("meta",)), again)
        finally:
            w.close()

    def test_the_number_only_grows(self):
        cur = str(metrics.SCHEMA)                                   # 4 since the activity (SPEC-activity §4)
        for was, now in (("5", "5"), (cur, cur), ("3", cur), ("1", cur), ("x", cur), ("2", cur)):
            w = self.open_new()
            with w.conn:
                w.conn.execute("UPDATE meta SET v = ? WHERE k = 'schema'", (was,))
            w.close()
            w = metrics.open_writer()
            self.assertEqual(w.meta("schema"), now, was)
            w.close()

    def test_panel_of_a_new_code_reads_a_base_of_stage1(self):
        """The panel may be updated before the collector opens the base: no host tables yet — charts off, not an
        error; the history of exits still reads."""
        self.fill_stage1(stage1())
        self.assertEqual(metrics.host_history("1d", NOW)["charts"], False)
        self.assertEqual(metrics.ping_history("1d", NOW), {"charts": False, "step": 60, "anchors": {}})
        self.assertTrue(metrics.history("e-a", "1d", NOW)["charts"])


class WayBackTest(RootCase):
    """§3.4 path (3): back to the code of stage 1 — it opens SCHEMA 2, keeps the number, leaves host_* alone."""

    def fill_new(self):
        w = metrics.open_writer()
        try:
            for i in range(30):
                w.write_host_minute(NOW - 1800 + i * 60, host(busy=float(i)))
                w.write_ping_minute(NOW - 1800 + i * 60, {"*": ping(n=9), "192.0.2.1": ping()})
            w.write_minute(NOW - 60, {"e-a": {"rtt_med": 10.0, "state": "ok"}})
            with w.conn:                                            # older than kept: the new maintain drops them
                w.conn.execute("INSERT INTO host_m1 (ts, n) VALUES (?, 6)", (NOW - 3 * D,))
                w.conn.execute("INSERT INTO host_ping_h1 (anchor, ts, n) VALUES ('*', ?, 9)", (NOW - 91 * D,))
            self.assertTrue(w.maintain(NOW))
            with w.conn:
                w.conn.execute("INSERT INTO host_m1 (ts, n) VALUES (?, 6)", (NOW - 3 * D,))
                w.conn.execute("INSERT INTO host_ping_h1 (anchor, ts, n) VALUES ('*', ?, 9)", (NOW - 91 * D,))
            return dump(w.conn, metrics.HOST_TABLES + metrics.PING_TABLES)
        finally:
            w.close()

    def test_3_stage1_opens_schema2_and_leaves_host_alone(self):
        s1 = stage1()
        host_rows = self.fill_new()
        self.assertTrue(host_rows["host_m10"] and host_rows["host_ping_m10"])
        w = s1.open_writer()
        self.assertIsNotNone(w)
        try:
            self.assertEqual(w.meta("schema"), str(metrics.SCHEMA))  # INSERT OR IGNORE: the number stays
            self.assertTrue(w.write_minute(NOW + H, {"e-a": {"rtt_med": 11.0, "state": "ok"}}))
            self.assertTrue(w.maintain(NOW + H))
            self.assertTrue(w.maintain(NOW + 2 * H))
            self.assertEqual(dump(w.conn, metrics.HOST_TABLES + metrics.PING_TABLES), host_rows)
            self.assertEqual(w.meta("schema"), str(metrics.SCHEMA))
        finally:
            w.close()
        self.assertTrue(s1.history("e-a", "1d", NOW + H)["charts"])
        # up again: the number 2, the old rows go with the first maintain, the minute of stage 1 stays
        w = metrics.open_writer()
        try:
            self.assertEqual(w.meta("schema"), str(metrics.SCHEMA))
            self.assertTrue(w.maintain(NOW + 2 * H))
            q = lambda s, *a: w.conn.execute(s, a).fetchall()
            self.assertEqual(q("SELECT COUNT(*) FROM host_m1 WHERE ts < ?", NOW + 2 * H - 2 * D), [(0,)])
            self.assertEqual(q("SELECT COUNT(*) FROM host_ping_h1 WHERE ts < ?", NOW + 2 * H - 90 * D), [(0,)])
            self.assertEqual(q("SELECT COUNT(*) FROM host_m1 WHERE ts >= ?", NOW - 1800), [(30,)])
            self.assertEqual(q("SELECT rtt_med FROM m1 WHERE ts = ?", (NOW + H) // 60 * 60), [(11.0,)])
        finally:
            w.close()

    def test_3_cap_of_stage1_does_not_cut_host(self):
        s1 = stage1()
        host_rows = self.fill_new()
        w = s1.open_writer()
        try:
            with w.conn:
                for i in range(90 * 24):
                    ts = NOW - 90 * D + i * H + 60
                    w.conn.execute("INSERT INTO h1 (exit, ts, state) VALUES ('e-a', ?, ?)", (ts, "x" * 300))
            w.maintain(NOW, max_mb=0.05)                            # far too small: h1 to its floor
            self.assertEqual(w.conn.execute("SELECT COUNT(*) FROM h1 WHERE ts < ?", (NOW - 7 * D,)).fetchone()[0], 0)
            self.assertEqual(dump(w.conn, metrics.HOST_TABLES + metrics.PING_TABLES), host_rows)
        finally:
            w.close()


class HostCase(RootCase):
    def setUp(self):
        super().setUp()
        self.w = metrics.open_writer()
        self.assertIsNotNone(self.w)
        self.addCleanup(self.w.close)

    def rows(self, q, *a):
        return self.w.conn.execute(q, a).fetchall()

    def one(self, q, *a):
        return self.w.conn.execute(q, a).fetchone()


class WriteTest(HostCase):
    def test_minute_floored_and_replaced(self):
        self.assertTrue(self.w.write_host_minute(NOW + 17, host(busy=10.0)))
        self.assertTrue(self.w.write_host_minute(NOW + 42, {"busy": 20.0}))      # the same minute: replaced whole
        self.assertEqual(self.rows("SELECT ts, n, busy, rx FROM host_m1"), [(NOW // 60 * 60, None, 20.0, None)])

    def test_bad_values_are_null(self):
        bad = host(busy="much", steal=float("nan"), iowait=float("inf"), softirq=-float("inf"), rx=10 ** 30,
                   tx=float("inf"), pkts="12", oom=True, level=5, warn_min=3, alarm_min=True, clock_ok=False,
                   disk_free=1234.9, load_core="0.25")
        self.assertTrue(self.w.write_host_minute(NOW, bad))                         # the round goes on
        r = dict(zip([c[0] for c in metrics.HOST_COLS],
                     self.one("SELECT %s FROM host_m1" % ", ".join(c[0] for c in metrics.HOST_COLS))))
        for k in ("busy", "steal", "iowait", "softirq", "rx", "tx", "oom", "level"):
            self.assertIsNone(r[k], k)
        self.assertEqual((r["pkts"], r["warn_min"], r["alarm_min"], r["clock_ok"], r["disk_free"], r["load_core"]),
                         (12, 1, 1, 0, 1234, 0.25))
        for lvl in (0, 1, 2):
            self.w.write_host_minute(NOW, host(level=lvl))
            self.assertEqual(self.one("SELECT level FROM host_m1")[0], lvl)

    def test_values_have_a_limit(self):
        """REV-ns-a4 З-1, П-2: a counter above 2^53 or a REAL above 1e12 is garbage — NULL; so the sums of thinning
        stay in int64 and an average × n stays finite (1e308 read back as inf, Infinity in the JSON)."""
        lim, big = metrics._INT_MAX, metrics._REAL_MAX
        self.assertTrue(self.w.write_host_minute(NOW, host(rx=lim, tx=lim + 1, pkts=-lim - 1, disk_free=2 ** 62,
                                                           busy=big, steal=-big, iowait=1e308, clock_off=-1e13)))
        r = dict(zip(("rx", "tx", "pkts", "disk_free", "busy", "steal", "iowait", "clock_off"),
                     self.one("SELECT rx, tx, pkts, disk_free, busy, steal, iowait, clock_off FROM host_m1")))
        self.assertEqual(r, {"rx": lim, "tx": None, "pkts": None, "disk_free": None, "busy": big, "steal": -big,
                             "iowait": None, "clock_off": None})
        self.assertTrue(self.w.write_ping_minute(NOW, {"*": ping(n=2 ** 60, rtt_med=1e308, jit_n=lim)}))
        self.assertEqual(self.one("SELECT n, rtt_med, jit_n FROM host_ping_m1"), (None, None, lim))

    def test_huge_reals_read_back_finite(self):
        for i in range(2):
            self.w.write_host_minute(NOW - 120 + i * 60, host(busy=1e308, steal=1e12, n=2 ** 53))
        h = metrics.host_history("1d", NOW)
        json.dumps(h, allow_nan=False)                                 # no inf / nan for the panel
        p = dict(zip(["ts"] + h["cols"], h["points"][0]))
        self.assertEqual((p["busy"], p["steal"]), (None, 1e12))

    def test_exits_m1_have_the_same_limit(self):
        """«было до правки» (REV-ns-a4 З-1): two minutes of an exit with rx 2^62 made SUM() of m1 → m10 overflow and
        stopped maintain of stage 1; the same limit makes them NULL."""
        for i in range(2):
            self.assertTrue(self.w.write_minute(NOW - 1200 + i * 60, {"e-a": {"rx": 2 ** 62, "tx": 5, "n": 2 ** 60,
                                                                               "rtt_med": 1e308, "loss": 0.5}}))
        self.assertEqual(self.rows("SELECT rx, tx, n, rtt_med FROM m1 ORDER BY ts")[0], (None, 5, None, None))
        self.assertTrue(self.w.maintain(NOW))
        self.assertEqual(self.one("SELECT tx FROM m10 WHERE exit = 'e-a'"), (10,))

    def test_bad_minute_does_not_raise(self):
        self.assertFalse(self.w.write_host_minute("soon", host()))
        self.assertFalse(self.w.write_host_minute(float("nan"), host()))
        self.assertFalse(self.w.write_ping_minute(None, {"*": ping()}))
        self.assertTrue(self.w.write_host_minute(NOW, "not a row"))                # a row of NULLs, not an error
        self.assertTrue(self.w.write_host_minute(NOW, host()))
        log = self.read("/var/log/kaskad.log")
        self.assertEqual(log.count("metrics.db: host_m1 failed"), 1)                # one line a kind
        self.assertEqual(log.count("metrics.db: host_ping_m1 failed"), 1)

    def test_ping_rows_as_dict_or_list(self):
        self.assertTrue(self.w.write_ping_minute(NOW + 5, {"*": ping(n=9), "192.0.2.1": ping(via="dns", lost=1)}))
        self.assertTrue(self.w.write_ping_minute(NOW + 65, [
            dict(ping(via="raw-socket-of-a-very-long-name"), anchor="2001:db8::1"), {"anchor": "", "n": 3},
            {"anchor": None}, {"anchor": "x" * 65}, "junk", dict(ping(n="many", lost=float("nan")), anchor="*")]))
        self.assertTrue(self.w.write_ping_minute(NOW + 125, None))
        m = NOW // 60 * 60
        self.assertEqual(self.rows("SELECT anchor, ts, via, n, lost FROM host_ping_m1 ORDER BY ts, anchor"), [
            ("*", m, "icmp", 9, 0.0), ("192.0.2.1", m, "dns", 6, 1.0),
            ("*", m + 60, "icmp", None, None), ("2001:db8::1", m + 60, None, 6, 0.0)])


class ThinHostTest(HostCase):
    """§3.4 «Прореживание» on hand numbers."""

    def fill(self):
        base = NOW // H * H - 2 * H                                 # two finished hours
        for i in range(60):                                         # hour 1: 20 minutes of warn (A4: warn_min = 20)
            warn = 10 <= i < 30
            self.w.write_host_minute(base + i * 60, host(level=1 if warn else 0, warn_min=warn))
        detail = {                                                  # hour 2, its first ten minutes
            "n": lambda i: 6 if i % 2 == 0 else 3, "busy": lambda i: 10.0 if i % 2 == 0 else 40.0,
            "busy_max": float, "mem_avail_min": lambda i: 50.0 - i, "disk_free": lambda i: 1000 + i,
            "clock_ok": lambda i: 0 if i == 4 else 1, "clock_off": lambda i: {0: 0.1, 3: -0.5, 7: 0.3}.get(i, 0.0),
            "canary_ms": lambda i: 120.0 if i == 5 else None,
            "level": lambda i: None if i == 0 else (2 if i == 8 else 0), "warn_min": lambda i: 1 if i in (6, 7, 8) else 0,
            "alarm_min": lambda i: 1 if i == 8 else 0, "oom": lambda i: 1 if i == 2 else 0}
        for i in range(60):
            kw = dict((k, f(i)) for k, f in detail.items()) if i < 10 else {}
            self.w.write_host_minute(base + H + i * 60, host(**kw))
        return base

    def test_host_m1_to_m10_to_h1(self):
        base = self.fill()
        self.assertTrue(self.w.maintain(NOW))
        cols = [c[0] for c in metrics.HOST_COLS]
        get = lambda t, ts: dict(zip(cols, self.one("SELECT %s FROM %s WHERE ts = ?" % (", ".join(cols), t), ts)))
        self.assertEqual(self.one("SELECT COUNT(*) FROM host_m10")[0], 12)
        b = get("host_m10", base + H)
        self.assertEqual(b["n"], 45)
        self.assertAlmostEqual(b["busy"], 20.0)                     # weighted by n: (5·6·10 + 5·3·40) / 45, not 25
        self.assertEqual((b["busy_max"], b["mem_avail_min"], b["disk_free"], b["clock_ok"]), (9.0, 41.0, 1000, 0))
        self.assertEqual((b["clock_off"], b["canary_ms"], b["level"]), (-0.5, 120.0, 2))   # NULL canary not counted
        self.assertEqual((b["warn_min"], b["alarm_min"], b["oom"], b["rx"]), (3, 1, 1, 10000))
        self.assertIsNone(b["swap"])                                 # no point in the bucket — NULL, not 0
        self.assertIsNone(get("host_m10", base + H + 600)["canary_ms"])
        h1 = get("host_h1", base)
        self.assertEqual((h1["warn_min"], h1["alarm_min"], h1["level"], h1["n"]), (20, 0, 1, 360))
        h2 = get("host_h1", base + H)
        self.assertEqual((h2["n"], h2["warn_min"], h2["alarm_min"], h2["level"]), (345, 3, 1, 2))
        self.assertAlmostEqual(h2["busy"], 3900.0 / 345)              # the weights went on from host_m10
        self.assertEqual((h2["clock_off"], h2["mem_avail_min"], h2["disk_free"], h2["clock_ok"], h2["canary_ms"]),
                         (-0.5, 41.0, 1000, 0, 120.0))
        self.assertEqual((h2["rx"], h2["oom"], h2["busy_max"]), (60000, 1, 10.0))
        before = dump(self.w.conn, metrics.HOST_TABLES)
        self.assertTrue(self.w.maintain(NOW))                         # again: the same, nothing doubled
        self.assertEqual(dump(self.w.conn, metrics.HOST_TABLES), before)

    def test_ping_per_anchor(self):
        base = NOW // H * H - 2 * H
        for i in range(20):
            via = ("icmp" if i < 7 else "dns") if i < 10 else ("dns" if i < 15 else ("icmp" if i < 19 else None))
            a = ping(via=via, n=6 if i % 2 == 0 else 3, lost=1.0 if i == 2 else 0.0, runs=1 if i == 2 else 0,
                     rtt_med=20.0 if i % 2 == 0 else 50.0, rtt_p90=float(i), jit=2.0 if i % 2 == 0 else 8.0,
                     jit_n=4 if i % 2 == 0 else 1, excl=1 if i == 3 else 0, outage=1 if i == 11 else 0)
            self.w.write_ping_minute(base + i * 60, {"192.0.2.1": a, "*": ping(n=9)})
        self.assertTrue(self.w.maintain(NOW))
        q = "SELECT via, n, lost, runs, rtt_med, rtt_p90, jit, jit_n, excl, outage FROM %s WHERE anchor = ? AND ts = ?"
        via, n, lost, runs, rtt, p90, jit, jit_n, excl, outage = self.one(q % "host_ping_m10", "192.0.2.1", base)
        self.assertEqual((via, n, lost, runs, p90, jit_n, excl, outage), ("dns", 45, 1.0, 1, 9.0, 25, 1, 0))
        self.assertAlmostEqual(rtt, 30.0)                            # by n: (5·6·20 + 5·3·50) / 45
        self.assertAlmostEqual(jit, 3.2)                             # by jit_n: (5·4·2 + 5·1·8) / 25
        self.assertEqual(self.one(q % "host_ping_m10", "192.0.2.1", base + 600)[0], "icmp")   # the newest, not MAX
        self.assertEqual(self.one(q % "host_ping_m10", "*", base)[1], 90)
        h = self.one(q % "host_ping_h1", "192.0.2.1", base)
        self.assertEqual((h[0], h[1], h[2], h[8], h[9]), ("icmp", 90, 1.0, 1, 1))
        self.assertEqual(self.one("SELECT COUNT(*) FROM host_ping_h1")[0], 2)
        before = dump(self.w.conn, metrics.PING_TABLES)
        self.w.maintain(NOW)
        self.assertEqual(dump(self.w.conn, metrics.PING_TABLES), before)

    def test_weight_1_without_n(self):
        """Decision 5 of A4 (REV-ns-a4 П-1 б): a row with n NULL or 0 weighs 1 — its value is not lost."""
        b = NOW // H * H - H
        for i, (n, busy) in enumerate(((None, 10.0), (0, 40.0), (2, 70.0))):
            self.w.write_host_minute(b + i * 60, host(n=n, busy=busy))
        self.w.write_ping_minute(b, {"*": ping(n=None, jit_n=None, rtt_med=10.0, jit=4.0)})
        self.w.write_ping_minute(b + 60, {"*": ping(n=0, jit_n=0, rtt_med=30.0, jit=8.0)})
        self.assertTrue(self.w.maintain(NOW))
        self.assertAlmostEqual(self.one("SELECT busy FROM host_m10 WHERE ts = ?", b)[0], 47.5)   # (10 + 40 + 140) / 4
        self.assertEqual(self.one("SELECT rtt_med, jit FROM host_ping_m10 WHERE ts = ?", b), (20.0, 6.0))
        b2 = NOW // 7200 * 7200 - 10 * 7200                            # read: 90d groups two rows of host_h1
        with self.w.conn:
            self.w.conn.executemany("INSERT INTO host_h1 (ts, n, busy) VALUES (?, ?, ?)", [(b2, None, 10.0),
                                                                                           (b2 + H, 0, 30.0)])
        pts = [dict(zip(["ts"] + [c[0] for c in metrics.HOST_COLS], r)) for r in metrics.host_history("90d", NOW)["points"]]
        self.assertEqual([p["busy"] for p in pts if p["ts"] == b2], [20.0])

    def test_unfinished_bucket_not_thinned(self):
        for i in range(3):
            self.w.write_host_minute(NOW // 600 * 600 + i * 60, host())
            self.w.write_ping_minute(NOW // 600 * 600 + i * 60, {"*": ping()})
        self.w.maintain(NOW // 600 * 600 + 200)
        self.assertEqual(self.one("SELECT COUNT(*) FROM host_m10")[0] + self.one("SELECT COUNT(*) FROM host_ping_m10")[0],
                         0)


class FamiliesTest(HostCase):
    """REV-ns-a4 З-1: maintain thins and cleans each family — the exits of stage 1, the node, the probes — in its own
    transaction: garbage in one does not stop the others (and the cap)."""

    def fill(self, base):
        for i in range(20):
            ts = base + i * 60
            self.w.write_minute(ts, {"e-a": {"rx": 10, "tx": 1, "n": 6, "state": "ok"}})
            self.w.write_host_minute(ts, host())
            self.w.write_ping_minute(ts, {"*": ping()})

    def test_overflow_in_host_m1_does_not_stop_the_exits(self):
        base = NOW // H * H - 2 * H
        self.fill(base)
        with self.w.conn:                                              # garbage past the writer (an old base, a bug)
            self.w.conn.execute("UPDATE host_m1 SET rx = ? WHERE ts < ?", (2 ** 62, base + 120))
            self.w.conn.execute("INSERT INTO m1 (exit, ts, rx) VALUES ('e-old', ?, 1)", (NOW - 3 * D,))
        self.assertFalse(self.w.maintain(NOW))
        self.assertEqual(self.one("SELECT COUNT(*), SUM(rx) FROM m10 WHERE exit = 'e-a'"), (2, 200))
        self.assertEqual(self.one("SELECT SUM(rx) FROM h1 WHERE exit = 'e-a'"), (200,))
        self.assertEqual(self.one("SELECT COUNT(*) FROM m1 WHERE exit = 'e-old'"), (0,))      # KEEP of stage 1 went on
        self.assertEqual(self.w.meta("thin_m10"), str(NOW // 600 * 600))
        self.assertEqual(self.one("SELECT COUNT(*) FROM host_ping_m10"), (2,))                # the probes too
        self.assertEqual(self.one("SELECT COUNT(*) FROM host_m10"), (0,))                     # only its own stopped
        self.assertIsNone(self.w.meta("thin_host_m10"))
        self.assertFalse(self.w.maintain(NOW))
        log = self.read("/var/log/kaskad.log")
        self.assertEqual(log.count("metrics.db: maintain host failed"), 1)
        self.assertNotIn("metrics.db: maintain failed", log)
        with self.w.conn:                                              # the garbage gone: it catches up
            self.w.conn.execute("UPDATE host_m1 SET rx = 1000")
        self.assertTrue(self.w.maintain(NOW))
        self.assertEqual(self.one("SELECT COUNT(*), SUM(rx) FROM host_m10"), (2, 20000))

    def test_overflow_in_exits_m1_does_not_stop_the_node(self):
        base = NOW // H * H - 2 * H
        self.fill(base)
        with self.w.conn:
            self.w.conn.execute("UPDATE m1 SET rx = ? WHERE ts < ?", (2 ** 62, base + 120))
        self.assertFalse(self.w.maintain(NOW))
        self.assertEqual(self.one("SELECT COUNT(*) FROM m10"), (0,))
        self.assertEqual(self.one("SELECT COUNT(*), SUM(rx) FROM host_m10"), (2, 20000))
        self.assertEqual(self.one("SELECT COUNT(*) FROM host_ping_h1"), (1,))
        self.assertIn("metrics.db: maintain failed", self.read("/var/log/kaskad.log"))

    def test_the_cap_runs_when_a_family_fails(self):
        base = NOW // H * H - 2 * H
        self.fill(base)
        with self.w.conn:
            self.w.conn.execute("UPDATE host_m1 SET rx = ?", (2 ** 62,))
            self.w.conn.executemany("INSERT INTO host_ping_h1 (anchor, ts, n) VALUES ('*', ?, 1)",
                                    [(NOW - 90 * D + i * H,) for i in range(80 * 24)])
        with mock.patch.object(self.w, "_cap", wraps=self.w._cap) as cap:
            self.assertFalse(self.w.maintain(NOW, max_mb=0.05))
        cap.assert_called_once_with(NOW, 0.05)
        self.assertGreaterEqual(self.one("SELECT MIN(ts) FROM host_ping_h1")[0], NOW - 7 * D)


class KeepCapTest(HostCase):
    def test_kept_times(self):
        for pre in ("host_", "host_ping_"):
            for t, ts in (("m1", NOW - 2 * D - 60), ("m10", NOW - 14 * D - 600), ("h1", NOW - 90 * D - H)):
                new = ts + 120 if t == "m1" else ts + 2 * H
                with self.w.conn:
                    if pre == "host_":
                        self.w.conn.execute("INSERT INTO host_%s (ts, n) VALUES (?, 1), (?, 2)" % t, (ts, new))
                    else:
                        self.w.conn.execute("INSERT INTO host_ping_%s (anchor, ts, n) VALUES ('*', ?, 1), ('*', ?, 2)"
                                            % t, (ts, new))
        self.w.maintain(NOW)
        for pre in ("host_", "host_ping_"):
            for t, keep in (("m1", 2 * D), ("m10", 14 * D)):
                self.assertEqual(self.rows("SELECT n FROM %s%s WHERE ts < ?" % (pre, t), NOW - keep), [], pre + t)
                newest = NOW - keep + (120 - 60 if t == "m1" else 2 * H - 600)
                self.assertEqual(self.one("SELECT n FROM %s%s WHERE ts = ?" % (pre, t), newest), (2,), pre + t)
            # the old point of m10 was thinned into a recent hour before m10 dropped it — only the 90 days count
            self.assertEqual(self.rows("SELECT n FROM %sh1 WHERE ts < ?" % pre, NOW - 90 * D), [])
            self.assertEqual(self.one("SELECT COUNT(*) FROM %sh1 WHERE ts = ?" % pre, NOW - 90 * D + H)[0], 1)

    def fill_90_days(self, ping_too=True):
        with self.w.conn:
            for i in range(90 * 24):
                ts = NOW - 90 * D + i * H + 60
                r = host(n=360)
                self.w.conn.execute("INSERT INTO host_h1 (ts, %s) VALUES (?%s)" % (
                    ", ".join(r), ",?" * len(r)), [ts] + list(r.values()))
                if ping_too:
                    for a in ("*", "192.0.2.1", "198.51.100.7", "2001:db8::53"):
                        self.w.conn.execute("INSERT INTO host_ping_h1 (anchor, ts, via, n, lost) VALUES (?, ?, 'icmp', "
                                            "360, 0)", (a, ts))

    def test_cap_cuts_host_h1_from_the_old_end(self):
        self.fill_90_days(ping_too=False)
        self.w.add_event(NOW - 60, "exit_dead", "e-a")
        n = self.one("SELECT COUNT(*) FROM host_h1")[0]
        cap = self.w.used_bytes() * 0.7 / 1048576
        self.w.maintain(NOW, max_mb=cap)
        self.assertLessEqual(self.w.used_bytes(), cap * 1048576)
        lo, hi, left = self.one("SELECT MIN(ts), MAX(ts), COUNT(*) FROM host_h1")
        self.assertEqual(hi, NOW - 90 * D + (n - 1) * H + 60)          # the newest stays, the oldest went
        self.assertLess(left, n)
        self.assertGreater(lo, NOW - 89 * D)
        self.assertEqual(self.one("SELECT COUNT(*) FROM events")[0], 1)
        self.assertIn("host_ping_h1 dropped", self.read("/var/log/kaskad.log"))

    def test_cap_keeps_30_days_of_host_and_7_of_ping(self):
        """The floors (§3.4, after A4 round 2): host_h1 — 30 days (the band of 30 days, C6, stays coloured),
        host_ping_h1 — 7."""
        self.fill_90_days()
        month = self.one("SELECT COUNT(*) FROM host_h1 WHERE ts >= ?", NOW - 30 * D)[0]
        pweek = self.one("SELECT COUNT(*) FROM host_ping_h1 WHERE ts >= ?", NOW - 7 * D)[0]
        self.w.maintain(NOW, max_mb=0.05)                              # far too small
        self.assertEqual(metrics.CAP_FLOOR["host_h1"], 30 * D)
        self.assertEqual(self.one("SELECT COUNT(*) FROM host_h1")[0], month)
        self.assertGreaterEqual(self.one("SELECT MIN(ts) FROM host_h1")[0], NOW - 30 * D)
        self.assertEqual(self.one("SELECT COUNT(*) FROM host_ping_h1")[0], pweek)
        self.assertEqual((month, pweek), (30 * 24, 4 * 7 * 24))
        log = self.read("/var/log/kaskad.log")
        self.assertIn("too small", log)
        self.assertIn("host_h1 30 days", log)

    def test_volume_of_the_whole_history(self):
        """§3.4 «Объём» (REV-ns-a4 З-2): the whole history — 48 h of minutes written as the collector will, 14 days
        of host_m10, 90 of host_h1 — of the node and of the probes of 5 anchors (the most, §3.7) and the node, the
        values fractional and scattered as live ones (an integer REAL takes 1–2 bytes, a fractional 8): ≲ 4.5 MB
        (``used_bytes``). At the floors of the cap (host_h1 30 days, host_ping_h1 7) the history of the node is far
        under MAX_MB, and the rough size of host_h1 (``_CAP_SIZE``) is not below what it holds."""
        rnd = random.Random(7)
        anchors = ("*",) + tuple("192.0.2.%d" % i for i in range(1, 6))
        end = NOW // H * H
        start = end - 2 * D
        for i in range(2 * 1440):
            ts = start + i * 60
            self.w.write_host_minute(ts, rand_host(rnd))
            self.w.write_ping_minute(ts, dict((a, rand_ping(rnd)) for a in anchors))
        self.assertTrue(self.w.maintain(end))
        hcols = [c[0] for c in metrics.HOST_COLS]
        pcols = [c[0] for c in metrics.PING_COLS]
        with self.w.conn:
            for t, step, keep in (("m10", 600, 14 * D), ("h1", H, 90 * D)):
                lo = self.one("SELECT MIN(ts) FROM host_%s" % t)[0]
                tss = range(end - keep, lo, step)
                self.w.conn.executemany("INSERT INTO host_%s (ts, %s) VALUES (?%s)" % (
                    t, ", ".join(hcols), ",?" * len(hcols)), [[ts] + [rand_host(rnd)[c] for c in hcols] for ts in tss])
                self.w.conn.executemany("INSERT INTO host_ping_%s (anchor, ts, %s) VALUES (?,?%s)" % (
                    t, ", ".join(pcols), ",?" * len(pcols)),
                    [[a, ts] + [rand_ping(rnd)[c] for c in pcols] for ts in tss for a in anchors])
        count = lambda t: self.one("SELECT COUNT(*) FROM %s" % t)[0]
        self.assertEqual([count(t) for t in metrics.HOST_TABLES], [2880, 2016, 2160])
        self.assertEqual([count(t) for t in metrics.PING_TABLES], [6 * 2880, 6 * 2016, 6 * 2160])
        full = self.w.used_bytes()
        self.assertGreater(full, 3 * 1024 * 1024)                      # the old «≲ 3 MB» did not hold
        self.assertLessEqual(full, 4.5 * 1024 * 1024)
        try:                                                           # dbstat: what host_h1 really holds
            real = self.one("SELECT SUM(pgsize) FROM dbstat WHERE name = 'host_h1'")[0]
        except sqlite3.Error:
            real = None
        if real:
            self.assertGreaterEqual(self.w._size("host_h1"), real * 0.9)
            self.assertLessEqual(self.w._size("host_h1"), real * 1.5)    # REV-ns-a4-2 П-3: from above too
        self.w.maintain(end, max_mb=0.05)                              # down to the floors
        self.assertEqual(count("host_h1"), 30 * 24)
        self.assertEqual(count("host_ping_h1"), 6 * 7 * 24)
        self.assertLessEqual(self.w.used_bytes(), metrics.MAX_MB * 1024 * 1024 / 16)   # ≤ 4 MB of 64


class ReadTest(HostCase):
    def test_host_history_1d(self):
        for i in range(30):
            self.w.write_host_minute(NOW - 1800 + i * 60, host(busy=float(i), warn_min=i == 3))
        h = metrics.host_history("1d", NOW)
        self.assertEqual((h["charts"], h["step"]), (True, 60))
        self.assertEqual(h["cols"], [c[0] for c in metrics.HOST_COLS])
        self.assertEqual(len(h["points"]), 30)
        p = dict(zip(["ts"] + h["cols"], h["points"][3]))
        self.assertEqual((p["ts"], p["busy"], p["warn_min"], p["n"], p["swap"]), (NOW - 1800 + 180, 3.0, 1, 6, None))
        self.assertEqual(metrics.host_history("bogus", NOW)["step"], 60)

    def test_ranges_at_most_max_points(self):
        with self.w.conn:
            for i in range(2200):
                self.w.conn.execute("INSERT INTO host_h1 (ts, n, rx, warn_min, busy) VALUES (?, 360, 1, 2, ?)",
                                    (NOW // H * H - i * H, 10.0 if i % 2 else 20.0))
                self.w.conn.execute("INSERT INTO host_ping_h1 (anchor, ts, n, lost) VALUES ('*', ?, 1080, 1)",
                                    (NOW // H * H - i * H,))
            for i in range(1100):
                self.w.conn.execute("INSERT INTO host_m10 (ts, n) VALUES (?, 60)", (NOW // 600 * 600 - i * 600,))
        h = metrics.host_history("90d", NOW)
        self.assertEqual(h["step"], 7200)
        self.assertLessEqual(len(h["points"]), metrics.MAX_POINTS)
        self.assertGreater(len(h["points"]), 1000)
        last = dict(zip(["ts"] + h["cols"], h["points"][-2]))
        self.assertEqual((last["n"], last["rx"], last["warn_min"], last["busy"]), (720, 2, 4, 15.0))
        self.assertEqual(metrics.host_history("7d", NOW)["step"], 600)
        self.assertEqual(len(metrics.host_history("7d", NOW)["points"]), 1009)
        p = metrics.ping_history("90d", NOW)
        self.assertEqual((p["charts"], p["step"], list(p["anchors"])), (True, 7200, ["*"]))
        self.assertLessEqual(len(p["anchors"]["*"]), metrics.MAX_POINTS)
        self.assertEqual(p["anchors"]["*"][-2][1:3], [2160, 2.0])

    def test_ping_history_1d(self):
        for i in range(10):
            self.w.write_ping_minute(NOW - 600 + i * 60, {"*": ping(n=9), "192.0.2.1": ping(lost=float(i % 2),
                                                                                         rtt_med=20.0 + i)})
        p = metrics.ping_history("1d", NOW)
        self.assertEqual((p["charts"], p["step"], sorted(p["anchors"])), (True, 60, ["*", "192.0.2.1"]))
        self.assertEqual(p["anchors"]["192.0.2.1"][1], [NOW - 600 + 60, 6, 1.0, 21.0, 2.0])
        self.assertEqual(len(p["anchors"]["*"]), 10)

    def test_ping_history_weighs_jit_by_jit_n(self):
        """REV-ns-a4 П-1 а: the jitter of a point is weighed by jit_n (its samples), not by n — 90d groups 2 rows."""
        b = NOW // 7200 * 7200 - 7200
        with self.w.conn:
            self.w.conn.executemany("INSERT INTO host_ping_h1 (anchor, ts, n, lost, rtt_med, jit, jit_n) "
                                    "VALUES ('*', ?, ?, 0, ?, ?, ?)", [(b, 1, 10.0, 2.0, 4), (b + H, 10, 30.0, 8.0, 1)])
        pt = metrics.ping_history("90d", NOW)["anchors"]["*"][-1]
        self.assertEqual(pt[0], b)
        self.assertAlmostEqual(pt[3], 310.0 / 11, 3)                    # rtt_med by n: (10 + 300) / 11
        self.assertAlmostEqual(pt[4], 3.2, 3)                           # jit by jit_n: (4·2 + 1·8) / 5, not 82 / 11


class NoBaseTest(RootCase):
    def test_without_sqlite_charts_off(self):
        with mock.patch.object(metrics, "sqlite3", None):
            self.assertEqual(metrics.host_history("1d", NOW), {"charts": False, "step": 60,
                                                              "cols": [c[0] for c in metrics.HOST_COLS], "points": []})
            self.assertEqual(metrics.ping_history("7d", NOW), {"charts": False, "step": 600, "anchors": {}})
        self.assertFalse(os.path.exists(paths.metrics_db()))

    def test_no_base_yet(self):
        self.assertFalse(metrics.host_history("90d", NOW)["charts"])
        self.assertFalse(metrics.ping_history("90d", NOW)["charts"])

    def test_readers_cannot_write(self):
        w = metrics.open_writer()
        self.addCleanup(w.close)
        w.write_host_minute(NOW, host())
        conn = metrics._reader()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM host_m1")
        finally:
            conn.close()
