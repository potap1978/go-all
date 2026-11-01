"""The activity of devices in metrics.db — SCHEMA 4 (k3/handoff/SPEC-activity.md §4, §8.1; ACT-2, ACT-3, ACT-12): the
way up from a base of 3.3.7.8 (SCHEMA 3) with its data whole, the way back to its code (dev_* never touched), the window
into four tables in one transaction, kept 25 h / 30 days / 400 days, the cap with its floors, «history off», the
devices of the state only, the summary of «Люди» and the history of a device and a person at the edges."""

import hashlib
import json
import os
import sqlite3
import types

from kaskad import tz
from kaskad.gw import metrics
from tests.helpers import RootCase

H = 3600
D = 86400
NOW = 1791190000 // H * H + 1500        # 25 min into an hour
B = NOW // 300 * 300                    # the window of now
STAGE_3 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "gw",
                       "metrics_schema3.py.txt")
STAGE_3_BLOB = "aca46788596f75f46d643acec886250cd440fcd4"   # git rev-parse abe39661:lib/kaskad/gw/metrics.py (3.3.7.8)
OLD_TABLES = ("m1", "m10", "h1", "speed", "svc", "geo", "traffic", "events") + metrics.HOST_TABLES + \
    metrics.PING_TABLES + ("bench_run", "bench", "bench_epoch", "bench_drop", "host_people_h1")
DEV = metrics.DEV_TABLES + ("dev_node",)


def stage_3():
    """The code of SCHEMA 3 as it is in 3.3.7.8 abe39661 — a module of its own next to kaskad.gw.metrics."""
    with open(STAGE_3, encoding="utf-8") as f:
        code = compile(f.read(), STAGE_3, "exec")
    mod = types.ModuleType("kaskad.gw._metrics_schema3")
    mod.__package__ = "kaskad.gw"
    mod.__file__ = STAGE_3
    exec(code, mod.__dict__)
    return mod


def tables(conn):
    return set(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"))


def dump(conn, names):
    have = tables(conn)
    return dict((t, sorted(conn.execute("SELECT * FROM %s" % t).fetchall(), key=repr)) for t in names if t in have)


def win(ts, rows, rounds=30, secs=300):
    return {"ts": ts, "rounds": rounds, "secs": secs, "rows": rows}


UTC = tz.load("UTC")


class FrozenStage3Test(RootCase):
    def test_the_copy_is_the_code_of_3_3_7_8(self):
        with open(STAGE_3, "rb") as f:
            data = f.read()
        self.assertEqual(hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), STAGE_3_BLOB)
        self.assertEqual(stage_3().SCHEMA, 3)


class WayUpTest(RootCase):
    """3 → 4: CREATE TABLE IF NOT EXISTS only, every row of 3.3.7.8 in place, the number 4."""

    def test_base_of_3_goes_up_whole(self):
        old = stage_3()
        w = old.open_writer()
        try:
            for i in range(90):
                ts = NOW - 2 * H + i * 60
                w.write_minute(ts, {"e-a": {"rtt_med": 10.0 + i, "n": 6, "ok": 6, "rx": 5, "state": "ok"}})
                w.write_host_minute(ts, {"n": 6, "busy": float(i % 7)})
            w.add_traffic("e-a", "2026-10-20", 10, 20, 1)
            w.write_people_hour(NOW - H, {"conns": 40, "rtt_med": 48.0})
            self.assertTrue(w.maintain(NOW))
            self.assertEqual(w.meta("schema"), "3")
            self.assertFalse(set(DEV) & tables(w.conn))
            before = dump(w.conn, OLD_TABLES + ("meta",))
        finally:
            w.close()
        w = metrics.open_writer()
        try:
            self.assertEqual(w.meta("schema"), "4")
            after = dump(w.conn, OLD_TABLES + ("meta",))
            for t in OLD_TABLES:
                self.assertEqual(after[t], before[t], t)
            self.assertEqual(dict(before["meta"]), dict(dict(after["meta"]), schema="3"))
            for t in metrics.DEV_TABLES:
                self.assertEqual([r[1] for r in w.conn.execute("PRAGMA table_info(%s)" % t)],
                                 ["ts", "dev", "on_s", "act_s", "rx", "tx"], t)
                self.assertIn("PRIMARY KEY (ts, dev)) WITHOUT ROWID",
                              w.conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (t,)).fetchone()[0])
            self.assertEqual([r[1] for r in w.conn.execute("PRAGMA table_info(dev_node)")],
                             ["step", "ts", "rounds", "secs"])
            # no second index (ACT-3): the key is the only one
            self.assertEqual(set(r for r in tables(w.conn) if r.startswith("dev_")), set(DEV))
            self.assertFalse(any(n.startswith("dev") for (n,) in w.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")))
            self.assertTrue(w.maintain(NOW))
            self.assertEqual(dump(w.conn, OLD_TABLES), dict((t, before[t]) for t in OLD_TABLES))
        finally:
            w.close()


class WayBackTest(RootCase):
    """4 → the code of 3.3.7.8: it opens SCHEMA 4, keeps the number, never reads or touches dev_* — its cap too."""

    def test_code_of_3_leaves_dev_alone(self):
        w = metrics.open_writer()
        try:
            self.assertTrue(w.write_dev(win(B - 600, {"d-1a2b3c4d": [300, 40, 10 ** 6, 10 ** 7]}), UTC))
            for i in range(1, 41):                               # 40 days of hours: above the floor of the cap
                w.conn.execute("INSERT INTO dev_h1 VALUES (?,?,?,?,?,?)", (NOW // H * H - i * D, "d-1a2b3c4d",
                                                                         3600, 10, 5, 5))
            w.conn.commit()
            rows = dump(w.conn, DEV)
        finally:
            w.close()
        old = stage_3()
        w = old.open_writer()
        self.assertIsNotNone(w)
        try:
            self.assertEqual(w.meta("schema"), "4")
            self.assertTrue(w.write_host_minute(NOW, {"n": 6}))
            self.assertTrue(w.maintain(NOW))
            w.maintain(NOW, max_mb=0.01)                         # far too small: the cap of 3.3.7.8 — not dev_*
            self.assertEqual(dump(w.conn, DEV), rows)
            self.assertEqual(w.meta("schema"), "4")
            self.assertEqual(w.meta("dev_flush"), str(B - 600))
        finally:
            w.close()
        w = metrics.open_writer()
        try:
            self.assertTrue(w.maintain(NOW))                     # up again: what is older than kept goes
            self.assertEqual(w.conn.execute("SELECT COUNT(*) FROM dev_h1").fetchone(), (30,))
            self.assertEqual(w.conn.execute("SELECT COUNT(*) FROM dev_m5").fetchone(), (1,))
        finally:
            w.close()


class DevCase(RootCase):
    def setUp(self):
        super().setUp()
        self.w = metrics.open_writer()
        self.assertIsNotNone(self.w)
        self.addCleanup(self.w.close)

    def q(self, sql, *a):
        return self.w.conn.execute(sql, a).fetchall()


class WriteDevTest(DevCase):
    def test_one_window_into_four_tables(self):
        self.assertTrue(self.w.write_dev(win(B - 300, {"d-1": [300.4, 50.2, 1000, 2000], "d-2": [10, 0, 0, 64]}), UTC))
        hour, day = (B - 300) // H * H, (B - 300) // D * D
        self.assertEqual(self.q("SELECT * FROM dev_m5"), [(B - 300, "d-1", 300, 50, 1000, 2000),
                                                          (B - 300, "d-2", 10, 0, 0, 64)])
        self.assertEqual(self.q("SELECT * FROM dev_h1"), [(hour, "d-1", 300, 50, 1000, 2000), (hour, "d-2", 10, 0, 0, 64)])
        self.assertEqual(self.q("SELECT * FROM dev_d1"), [(day, "d-1", 300, 50, 1000, 2000), (day, "d-2", 10, 0, 0, 64)])
        self.assertEqual(self.q("SELECT * FROM dev_node ORDER BY step"), [(300, B - 300, 30, 300), (3600, hour, 30, 300)])
        self.assertEqual((self.w.meta("dev_flush"), self.w.meta("dev_since")), (str(B - 300), str(B - 300)))

    def test_windows_add_up_in_the_hour_and_the_day(self):
        h0 = NOW // H * H
        for i in range(3):
            self.assertTrue(self.w.write_dev(win(h0 + 300 * i, {"d-1": [100, 10, 5, 7]}), UTC))
        self.assertEqual(self.q("SELECT ts, on_s, act_s, rx, tx FROM dev_h1"), [(h0, 300, 30, 15, 21)])
        self.assertEqual(self.q("SELECT step, rounds, secs FROM dev_node WHERE step = 3600"), [(3600, 90, 900)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM dev_m5"), [(3,)])
        self.assertTrue(self.w.write_dev(win(h0, {"d-1": [100, 10, 5, 7]}), UTC))   # a merged window, the same stamp
        self.assertEqual(self.q("SELECT on_s, rx FROM dev_m5 WHERE ts = ?", h0), [(200, 10)])

    def test_the_day_is_of_the_zone_of_the_node(self):
        """dev_d1 — a day of the zone of the node (the limits of traffic, decision 4): 23:55 by Moscow is the day before,
        00:00 a new one."""
        msk = tz.load("Europe/Moscow")
        d = 1791147600                                         # 2026-10-05 00:00 MSK = 21:00 UTC the day before
        self.assertEqual(time_str(d, msk), "2026-10-05 00:00")
        self.assertTrue(self.w.write_dev(win(d - 300, {"d-1": [1, 0, 1, 1]}), msk))
        self.assertTrue(self.w.write_dev(win(d, {"d-1": [1, 0, 2, 2]}), msk))
        self.assertEqual(self.q("SELECT ts, rx FROM dev_d1 ORDER BY ts"), [(d - D, 1), (d, 2)])

    def test_seconds_at_most_the_window_and_bad_rows_skipped(self):
        self.assertTrue(self.w.write_dev(win(B, {"d-1": [400, 900, 1, 1], "../x": [1, 1, 1, 1], "d-2": [1, 1, -1, 1],
                                                 "d-3": [1, 1, 2 ** 60, 1], "d-4": "x", "d-5": [1, 2, 3]},
                                             rounds=40, secs=420), UTC))
        self.assertEqual(self.q("SELECT dev, on_s, act_s FROM dev_m5"), [("d-1", 300, 300)])
        self.assertEqual(self.q("SELECT secs FROM dev_node WHERE step = 300"), [(300,)])

    def test_garbage_window_writes_nothing(self):
        for bad in (None, {}, win("soon", {}), win(5, {}), win(2 ** 40, {})):
            self.assertTrue(self.w.write_dev(bad, UTC))
        self.assertEqual(self.q("SELECT COUNT(*) FROM dev_node"), [(0,)])

    def test_a_base_that_fails_says_false(self):
        self.w.conn.execute("DROP TABLE dev_h1")
        self.assertFalse(self.w.write_dev(win(B, {"d-1": [1, 1, 1, 1]}), UTC))
        self.assertEqual(self.q("SELECT COUNT(*) FROM dev_m5"), [(0,)])    # one transaction: nothing of it
        self.assertIn("metrics.db: dev failed", self.read("/var/log/kaskad.log"))


def time_str(ts, z):
    import time
    return time.strftime("%Y-%m-%d %H:%M", tz.local(ts, z))


class KeepAndCapTest(DevCase):
    def fill(self, days_h1=40, days_d1=420):
        c = self.w.conn
        c.executemany("INSERT INTO dev_m5 VALUES (?,?,?,?,?,?)", [(B - 300 * i, "d-1", 300, 0, 1, 1) for i in range(400)])
        c.executemany("INSERT INTO dev_node VALUES (300,?,30,300)", [(B - 300 * i,) for i in range(400)])
        c.executemany("INSERT INTO dev_h1 VALUES (?,?,?,?,?,?)",
                      [(NOW // H * H - H * i, "d-%d" % k, 3600, 0, 1, 1) for i in range(days_h1 * 24) for k in range(3)])
        c.executemany("INSERT INTO dev_node VALUES (3600,?,360,3600)", [(NOW // H * H - H * i,) for i in range(days_h1 * 24)])
        c.executemany("INSERT INTO dev_d1 VALUES (?,?,?,?,?,?)",
                      [(NOW // D * D - D * i, "d-%d" % k, 3600, 0, 1, 1) for i in range(days_d1) for k in range(3)])
        c.commit()

    def test_kept_25_hours_30_days_400_days(self):
        self.fill()
        self.assertTrue(self.w.maintain(NOW))
        lo = lambda t, *a: self.q("SELECT MIN(ts) FROM %s%s" % (t, a[0] if a else ""))[0][0]   # noqa: E731
        self.assertGreaterEqual(lo("dev_m5"), NOW - 25 * H)
        self.assertGreaterEqual(lo("dev_node", " WHERE step = 300"), NOW - 25 * H)
        self.assertGreaterEqual(lo("dev_h1"), NOW - 30 * D)
        self.assertGreaterEqual(lo("dev_node", " WHERE step = 3600"), NOW - 30 * D)
        self.assertGreaterEqual(lo("dev_d1"), NOW - 400 * D)
        self.assertLess(lo("dev_d1"), NOW - 399 * D)
        self.assertEqual(self.q("SELECT COUNT(*) FROM dev_m5"), [(301,)])

    def test_the_cap_cuts_dev_h1_with_its_hours_of_the_node_to_7_days(self):
        self.fill(days_h1=30, days_d1=400)
        self.w.maintain(NOW, max_mb=0.01)
        h_lo = self.q("SELECT MIN(ts) FROM dev_h1")[0][0]
        n_lo = self.q("SELECT MIN(ts) FROM dev_node WHERE step = 3600")[0][0]
        self.assertGreaterEqual(h_lo, NOW - 7 * D - H)
        self.assertLess(h_lo, NOW - 6 * D)                       # the floor: never shorter than 7 days
        self.assertGreaterEqual(n_lo, h_lo - H)                  # the cut hours are «нет данных» again
        d_lo = self.q("SELECT MIN(ts) FROM dev_d1")[0][0]
        self.assertGreaterEqual(d_lo, NOW - 90 * D - D)
        self.assertLess(d_lo, NOW - 89 * D)


class DropForgetTest(DevCase):
    def test_drop_dev_takes_everything_of_the_activity(self):
        self.assertTrue(self.w.write_dev(win(B, {"d-1": [1, 1, 1, 1]}), UTC))
        self.w.write_minute(NOW, {"e-a": {"n": 1}})
        self.assertEqual(self.w.drop_dev(), 1 + 1 + 1 + 2)
        for t in DEV:
            self.assertEqual(self.q("SELECT COUNT(*) FROM %s" % t), [(0,)], t)
        self.assertIsNone(self.w.meta("dev_flush"))
        self.assertEqual(self.q("SELECT COUNT(*) FROM m1"), [(1,)])     # the history of exits stays
        self.assertEqual(self.w.drop_dev(), 0)

    def test_forget_dev_keeps_only_the_devices_of_the_state(self):
        self.assertTrue(self.w.write_dev(win(B, {"d-1": [1, 1, 1, 1], "d-2": [1, 1, 1, 1]}), UTC))
        self.assertEqual(self.w.forget_dev({"d-1"}), 3)
        for t in metrics.DEV_TABLES:
            self.assertEqual(self.q("SELECT dev FROM %s" % t), [("d-1",)], t)
        self.assertEqual(self.q("SELECT COUNT(*) FROM dev_node"), [(2,)])   # the windows of the node stay


class SummaryTest(DevCase):
    def test_48_windows_of_30_minutes(self):
        t0 = (B - 1) // 1800 * 1800 - 47 * 1800
        rows = {"d-1": [300, 120, 5 * 1024 * 1024, 1024 * 1024], "d-2": [60, 0, 1024, 0]}
        for i in range(12):                                     # the last hour: 12 windows, the collector worked
            self.assertTrue(self.w.write_dev(win(B - 300 * (12 - i), rows if i >= 6 else {}), UTC))
        s = metrics.dev_summary({"d-1", "d-2", "d-3"}, NOW)
        self.assertEqual((s["activity"], s["step"], s["n"], s["t0"], s["now"]), (True, 1800, 48, t0, B))
        self.assertEqual(len(s["cover"]), 48)
        self.assertEqual(s["cover"][:44], [0] * 44)              # no round of the collector there: «нет данных»
        i_last = (B - 1 - t0) // 1800
        self.assertEqual(i_last, 47)
        d1 = s["devices"]["d-1"]
        self.assertEqual(d1["rx"], 6 * 5 * 1024 * 1024)
        self.assertEqual(d1["tx"], 6 * 1024 * 1024)
        self.assertEqual(d1["on_s"], 6 * 300)
        self.assertEqual(d1["act_s"], 6 * 120)
        self.assertEqual(sum(d1["kb"]), 6 * 6 * 1024)
        self.assertEqual(d1["last"], B)
        self.assertTrue(all(0 <= x <= 10 for x in d1["on"] + s["cover"]))
        self.assertNotIn("d-3", s["devices"])                    # nothing in 30 days
        self.assertEqual(set(d1), {"on", "kb", "rx", "tx", "on_s", "act_s", "last"})
        self.assertEqual(s["since"], B - 3600)

    def test_never_an_id_outside_the_state(self):
        self.assertTrue(self.w.write_dev(win(B - 300, {"d-gone": [300, 0, 1, 1], "d-1": [300, 0, 1, 1]}), UTC))
        s = metrics.dev_summary({"d-1"}, NOW)
        self.assertEqual(list(s["devices"]), ["d-1"])

    def test_only_last_for_a_device_seen_days_ago(self):
        self.w.conn.execute("INSERT INTO dev_h1 VALUES (?,?,?,?,?,?)", (NOW // H * H - 3 * D, "d-1", 1200, 0, 5, 5))
        self.w.conn.execute("INSERT INTO dev_h1 VALUES (?,?,?,?,?,?)", (NOW // H * H - 40 * D, "d-2", 1200, 0, 5, 5))
        self.w.conn.commit()
        self.assertTrue(self.w.write_dev(win(B - 300, {}), UTC))
        s = metrics.dev_summary({"d-1", "d-2"}, NOW)
        self.assertEqual(s["devices"], {"d-1": {"last": NOW // H * H - 3 * D + H}})

    def test_the_anchor(self):
        """The collector writes — the end of its last window; it stopped (or no window yet) — the 5-minute mark of now,
        the windows after its last are «нет данных»; a stamp from the future (the clock went back) is not taken."""
        self.assertEqual(metrics.dev_anchor(B - 300, NOW), B)
        self.assertEqual(metrics.dev_anchor(B - 900, NOW), B - 600)
        self.assertEqual(metrics.dev_anchor(B - 2 * H, NOW), B)
        self.assertEqual(metrics.dev_anchor(None, NOW), B)
        self.assertEqual(metrics.dev_anchor(B + D, NOW), B)
        self.assertTrue(self.w.write_dev(win(B - 3 * H, {"d-1": [300, 0, 1, 1]}), UTC))
        s = metrics.dev_summary({"d-1"}, NOW)
        self.assertEqual(s["now"], B)
        self.assertEqual(s["cover"][-6:], [0] * 6)               # the last 3 hours — no collector

    def test_the_last_window_is_partial(self):
        """The last 30 min are covered only up to the anchor: 2 windows of 5 min out of 2 — full, not 2/6."""
        h = B // 1800 * 1800
        if B - h < 600:
            h -= 1800
        for ts in range(h, h + 600, 300):
            self.assertTrue(self.w.write_dev(win(ts, {"d-1": [300, 0, 1, 1]}), UTC))
        s = metrics.dev_summary({"d-1"}, h + 600 + 10)
        self.assertEqual(s["now"], h + 600)
        self.assertEqual((s["cover"][-1], s["devices"]["d-1"]["on"][-1]), (10, 10))

    def test_no_base_and_no_sqlite(self):
        s = metrics.dev_summary({"d-1"}, NOW)
        self.assertEqual((s["activity"], s["devices"], s["cover"]), (True, {}, [0] * 48))
        from unittest import mock
        with mock.patch.object(metrics, "sqlite3", None):
            self.assertEqual(metrics.dev_summary({"d-1"}, NOW), {"activity": False, "why": "nosqlite"})
            self.assertIsNone(metrics.dev_history({"d-1"}, "1d", NOW))


class HistoryTest(DevCase):
    def test_ranges_and_their_edges(self):
        for k in range(24):
            self.assertTrue(self.w.write_dev(win(B - 300 * (24 - k), {"d-1": [300, 60, 1000 * k, 10], "d-2": [100, 100, 7, 7]}),
                                             UTC))
        for rng, step, n in (("1d", 300, 288), ("7d", 3600, 168), ("30d", 3600, 720)):
            h = metrics.dev_history({"d-1"}, rng, NOW)
            self.assertEqual((h["step"], h["n"]), (step, n), rng)
            for k in ("on_s", "act_s", "rx", "tx", "cover"):
                self.assertEqual(len(h[k]), n, (rng, k))
            self.assertEqual(h["t0"] + (n - 1) * step, (B - 1) // step * step, rng)   # the last point holds now
            self.assertEqual(sum(h["rx"]), sum(1000 * k for k in range(24)), rng)
            self.assertEqual(sum(h["on_s"]), 24 * 300, rng)
            self.assertEqual(h["since"], (B - 300 * 24) // step * step, rng)
        h = metrics.dev_history({"d-1"}, "1d", NOW)
        self.assertEqual(h["rx"][-1], 23000)
        self.assertEqual(h["cover"][-24:], [10] * 24)
        self.assertEqual(h["cover"][-25], 0)

    def test_a_person_sums_bytes_and_takes_the_most_of_seconds(self):
        self.assertTrue(self.w.write_dev(win(B - 300, {"d-1": [300, 60, 1000, 10], "d-2": [100, 100, 7, 7],
                                                       "d-3": [300, 300, 5, 5]}), UTC))
        h = metrics.dev_history({"d-1", "d-2"}, "1d", NOW, per_device=True)
        self.assertEqual((h["rx"][-1], h["tx"][-1], h["on_s"][-1], h["act_s"][-1]), (1007, 17, 300, 100))
        self.assertEqual(sorted(h["devices"], key=lambda x: x["id"]),
                         [{"id": "d-1", "rx": 1000, "tx": 10, "on_s": 300, "act_s": 60},
                          {"id": "d-2", "rx": 7, "tx": 7, "on_s": 100, "act_s": 100}])
        empty = metrics.dev_history(set(), "1d", NOW, per_device=True)
        self.assertEqual((sum(empty["rx"]), empty["devices"]), (0, []))


class NodeDaysTest(DevCase):
    """Л3 (ACT-14, R-s1a5 Р-4/В-1): «30 дней» — сутки узла. Ответ 30d несёт метки дней узла (day_marks), панель
    складывает часы по ним, а не по часам браузера."""

    def test_marks_of_the_days_of_the_node(self):
        msk = tz.Zone.fixed("MSK", 3 * 3600)
        t0 = 1794268800                                   # 10.11.2026 00:00 UTC = 03:00 МСК
        m = metrics.day_marks(t0, 3600, 48, msk)
        self.assertEqual(m, [[t0, "2026-11-10"], [t0 + 21 * 3600, "2026-11-11"], [t0 + 45 * 3600, "2026-11-12"]])
        self.assertEqual(metrics.day_marks(t0, 3600, 48, UTC), [[t0, "2026-11-10"], [t0 + 24 * 3600, "2026-11-11"],
                                                              [t0 + 48 * 3600, "2026-11-12"]])   # точка n — на сдвиг

    def test_only_the_month_has_them(self):
        msk = tz.Zone.fixed("MSK", 3 * 3600)
        for rng in ("1d", "7d"):
            self.assertNotIn("days", metrics.dev_history({"d-1"}, rng, NOW, z=msk), rng)
        h = metrics.dev_history({"d-1"}, "30d", NOW, z=msk)
        self.assertEqual(h["days"], metrics.day_marks(h["t0"], h["step"], h["n"], msk))
        self.assertIn(len(h["days"]), (31, 32))
        self.assertEqual(h["days"][0][0], h["t0"])


class SizeTest(DevCase):
    def test_150_devices_worst_case(self):
        """§4: everybody online all the time — 25 h of 5 min, 30 days of hours, 400 days of days: the base stays small
        and the summary for «Люди» under 100 KB (84 KB measured in the spec)."""
        devs = ["d-%08x" % (0x1a2b0000 + i) for i in range(150)]
        c = self.w.conn
        for i in range(300):
            ts = B - 300 * (300 - i)
            c.executemany("INSERT INTO dev_m5 VALUES (?,?,?,?,?,?)", [(ts, d, 300, 200, 123456789, 23456789) for d in devs])
            c.execute("INSERT INTO dev_node VALUES (300,?,30,300)", (ts,))
        for i in range(720):
            ts = NOW // H * H - H * i
            c.executemany("INSERT INTO dev_h1 VALUES (?,?,?,?,?,?)", [(ts, d, 3600, 2400, 1234567890, 234567890)
                                                                    for d in devs])
            c.execute("INSERT INTO dev_node VALUES (3600,?,360,3600)", (ts,))
        for i in range(400):
            c.executemany("INSERT INTO dev_d1 VALUES (?,?,?,?,?,?)", [(NOW // D * D - D * i, d, 86400, 50000,
                                                                     12345678901, 2345678901) for d in devs])
        c.commit()
        c.execute("VACUUM")
        size = os.path.getsize(self.w.path)
        self.assertLess(size, 12 * 1024 * 1024, size)
        s = metrics.dev_summary(set(devs), NOW)
        body = json.dumps(dict(s, ok=True), ensure_ascii=False, sort_keys=True, separators=(",", ":"))   # as the API
        self.assertEqual(len(s["devices"]), 150)
        self.assertLess(len(body), 100 * 1024, len(body))
        info = metrics.dev_info()
        self.assertTrue(info["db"])
        self.assertGreater(info["bytes"], 0)
