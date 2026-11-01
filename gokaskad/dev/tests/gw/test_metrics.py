"""The history of exits — kaskad.gw.metrics, /var/lib/kaskad/metrics.db (docs/k3/SPEC-stage1-exits.md §3.5, §3.6,
§7.9 history/transitions, §11.1 «metrics.py»; stage 1, T3)."""

import os
import sqlite3
import time
from unittest import mock

from kaskad import paths
from kaskad.gw import metrics
from tests.helpers import RootCase

H = 3600
NOW = 1760000000 // H * H + 1800                  # half past an hour
DROPPED = "old rows of h1/events/host_h1/host_ping_h1 dropped"


def point(**kw):
    p = {"rtt_med": 50.0, "rtt_p90": 80.0, "loss": 0.0, "jitter": 5.0, "ok": 6, "n": 6, "rx": 100, "tx": 10,
         "devices": 2, "state": "ok"}
    p.update(kw)
    return p


class MetricsCase(RootCase):
    def setUp(self):
        super().setUp()
        self.w = metrics.open_writer()
        self.assertIsNotNone(self.w)
        self.addCleanup(self.w.close)

    def rows(self, q, *a):
        return self.w.conn.execute(q, a).fetchall()


class SchemaTest(MetricsCase):
    def test_schema_and_pragmas(self):
        names = set(r[0] for r in self.rows("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"))
        self.assertTrue({"meta", "m1", "m10", "h1", "speed", "svc", "geo", "traffic", "events", "events_ts"} <= names)
        self.assertEqual(self.w.meta("schema"), str(metrics.SCHEMA))
        self.assertEqual(self.w.meta("tz"), "Europe/Moscow")
        self.assertEqual(self.rows("PRAGMA journal_mode")[0][0], "wal")
        self.assertEqual(self.rows("PRAGMA synchronous")[0][0], 1)          # NORMAL
        self.assertEqual(self.rows("PRAGMA busy_timeout")[0][0], 2000)
        self.assertEqual(oct(os.stat(paths.metrics_db()).st_mode & 0o777), "0o600")
        for t in ("m1", "m10", "h1", "speed", "svc", "geo", "traffic"):
            sql = self.rows("SELECT sql FROM sqlite_master WHERE name = ?", t)[0][0]
            self.assertIn("WITHOUT ROWID", sql, t)

    def test_one_writer(self):
        self.assertIsNone(metrics.open_writer())                           # the collector twice: the second gets none
        self.w.close()
        w2 = metrics.open_writer()
        self.assertIsNotNone(w2)
        w2.close()

    def test_reset_of_the_collector_memory_closes_its_writer(self):
        """collect.MEM.reset (a restart of the process in the tests, R-s1-rel шаг 3): the writer of the dropped memory is
        closed — its flock let go (a new writer opens), no sqlite connection left to the GC (ResourceWarning on stderr)."""
        from kaskad.gw import collect
        self.w.close()
        collect.MEM.reset()
        w = collect._writer()
        self.assertTrue(w)
        collect.MEM.reset()
        self.assertIsNone(collect.MEM.metrics)
        w2 = metrics.open_writer()                                         # the flock of the old one is free
        self.assertIsNotNone(w2)
        w2.close()
        with self.assertRaises(Exception):
            w.conn.execute("SELECT 1")                                     # closed

    def test_readers_cannot_write(self):
        self.w.write_minute(NOW, {"e-a": point()})
        conn = metrics._reader()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM m1").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM m1")
        finally:
            conn.close()

    def test_minute_floored_and_replaced(self):
        self.assertTrue(self.w.write_minute(NOW + 17, {"e-a": point(rtt_med=10.0), "e-b": point(state="weird")}))
        self.assertTrue(self.w.write_minute(NOW + 42, {"e-a": point(rtt_med=20.0)}))
        self.assertEqual(self.rows("SELECT exit, ts, rtt_med, state FROM m1 ORDER BY exit"),
                         [("e-a", NOW // 60 * 60, 20.0, "ok"), ("e-b", NOW // 60 * 60, 50.0, None)])


class DeltaTest(RootCase):
    def test_rule_of_deltas(self):
        self.assertEqual(metrics.delta(1500, 1000), 500)
        self.assertEqual(metrics.delta(200, 1000), 200)                    # the counter started anew
        self.assertEqual(metrics.delta(None, 5), 0)


class ThinTest(MetricsCase):
    def fill(self, start, minutes, **kw):
        for i in range(minutes):
            self.w.write_minute(start + i * 60, {"e-a": point(**kw)})

    def test_m1_to_m10_to_h1(self):
        base = NOW // H * H - 2 * H                                      # two finished hours
        for i in range(120):
            st = "off" if i == 3 else ("degraded" if i == 70 else "ok")
            self.w.write_minute(base + i * 60, {"e-a": point(rtt_med=float(10 + i % 10), loss=0.5 if i == 3 else 0.0,
                                                             state=st, devices=i % 7)})
        self.assertTrue(self.w.maintain(NOW))
        m10 = self.rows("SELECT ts, rtt_med, loss, ok, n, rx, tx, devices, state FROM m10 WHERE exit = 'e-a' "
                        "AND ts < ? ORDER BY ts", base + 2 * H)
        self.assertEqual(len(m10), 12)
        ts, rtt, loss, ok, n, rx, tx, dev, st = m10[0]
        self.assertEqual((ts, rtt, ok, n, rx, tx, dev, st), (base, 14.5, 60, 60, 1000, 100, 6, "off"))
        self.assertAlmostEqual(loss, 0.05)
        self.assertEqual(m10[1][8], "ok")
        self.assertEqual(m10[7][8], "degraded")                          # minute 70 is in the 8th bucket
        h1 = self.rows("SELECT ts, ok, rx, state FROM h1 WHERE exit = 'e-a' ORDER BY ts")
        self.assertEqual(h1[:2], [(base, 360, 6000, "off"), (base + H, 360, 6000, "degraded")])
        before = self.rows("SELECT * FROM m10 ORDER BY ts") + self.rows("SELECT * FROM h1 ORDER BY ts")
        self.assertTrue(self.w.maintain(NOW))                             # again: the same, nothing doubled
        self.assertEqual(self.rows("SELECT * FROM m10 ORDER BY ts") + self.rows("SELECT * FROM h1 ORDER BY ts"),
                         before)

    def test_loss_weighted_by_n(self):
        """REV-s1-t3 П6: the loss of a coarser point is weighted by the probes of its minutes."""
        base = NOW // H * H - 2 * H
        self.w.write_minute(base, {"e-a": point(loss=0.5, n=10, ok=5)})
        self.w.write_minute(base + 60, {"e-a": point(loss=0.0, n=2, ok=2)})
        self.w.maintain(NOW)
        loss = self.rows("SELECT loss FROM m10 WHERE exit = 'e-a' AND ts = ?", base)[0][0]
        self.assertAlmostEqual(loss, 5.0 / 12)

    def test_unfinished_bucket_not_thinned(self):
        self.fill(NOW // 600 * 600, 3)
        self.w.maintain(NOW // 600 * 600 + 200)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM m10")[0][0], 0)

    def test_kept_times(self):
        d = 86400
        for t, ts in (("m1", NOW - 2 * d - 60), ("m10", NOW - 14 * d - 600), ("h1", NOW - 90 * d - H)):
            self.w.conn.execute("INSERT INTO %s (exit, ts) VALUES ('old', ?)" % t, (ts,))
            self.w.conn.execute("INSERT INTO %s (exit, ts) VALUES ('new', ?)" % t, (ts + 120 if t == "m1" else
                                                                                    ts + 2 * H,))
        self.w.conn.commit()
        self.w.add_speed("old", NOW - 91 * d, 10)
        self.w.add_speed("new", NOW - 89 * d, 10)
        self.w.add_svc("old", "yt", NOW - 91 * d, True)
        self.w.add_geo("old", NOW - 91 * d, "198.51.100.1", "DE")
        self.w.add_event(NOW - 91 * d, "exit_dead", "old")
        self.w.add_event(NOW - 89 * d, "exit_dead", "new")
        self.w.add_traffic("old", "2024-01-01", 1, 1)
        self.w.add_traffic("new", "2099-01-01", 1, 1)
        day = lambda k: time.strftime("%Y-%m-%d", time.gmtime(NOW - k * d))
        self.w.add_traffic("old", day(401), 1, 1)                     # 400 days kept, not 401 (REV-s1-t3 П6)
        self.w.add_traffic("new", day(400), 1, 1)
        self.w.maintain(NOW)
        for t in ("m1", "m10", "speed", "events", "traffic"):
            self.assertEqual(set(r[0] for r in self.rows("SELECT exit FROM %s" % t)), {"new"}, t)
        # h1: the old point of m10 was thinned into a recent hour before m10 dropped it — only the 90 days count
        self.assertEqual(self.rows("SELECT exit FROM h1 WHERE ts < ?", NOW - 90 * d), [])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM h1 WHERE exit = 'new' AND ts = ?", NOW - 90 * d + H)[0][0], 1)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM svc")[0][0] + self.rows("SELECT COUNT(*) FROM geo")[0][0], 0)

    def test_maintain_once_an_hour(self):
        mono = [100.0]
        with mock.patch.object(metrics, "_mono", lambda: mono[0]), \
                mock.patch.object(self.w, "maintain", return_value=True) as m:
            self.assertTrue(self.w.maybe_maintain(NOW))
            self.assertFalse(self.w.maybe_maintain(NOW))
            mono[0] += 3600
            self.assertTrue(self.w.maybe_maintain(NOW))
            self.assertEqual(m.call_count, 2)

    def test_traffic_adds_up(self):
        self.w.add_traffic("e-a", "2030-05-10", 10, 20, 1)
        self.w.add_traffic("e-a", "2030-05-10", 5, 5, 0)
        self.assertEqual(self.rows("SELECT rx, tx, probe FROM traffic"), [(15, 25, 1)])


class SizeTest(MetricsCase):
    def test_cap_cuts_oldest_h1_and_events(self):
        big = "x" * 400
        with self.w.conn:
            for i in range(3000):
                ts = NOW - 80 * 86400 + i * 600
                self.w.conn.execute("INSERT INTO h1 (exit, ts, state) VALUES ('e-a', ?, ?)", (ts, big))
                self.w.conn.execute("INSERT INTO events VALUES (?, 'exit_dead', 'e-a', ?)", (ts, big))
        self.assertGreater(self.w.used_bytes(), 1024 * 1024)
        self.w.maintain(NOW, max_mb=0.5)
        self.assertLessEqual(self.w.used_bytes(), 0.5 * 1024 * 1024)
        newest = self.rows("SELECT MAX(ts) FROM h1")[0][0]
        self.assertEqual(newest, NOW - 80 * 86400 + 2999 * 600)          # the newest stays, the oldest went
        self.assertGreater(self.rows("SELECT MIN(ts) FROM events")[0][0], NOW - 80 * 86400)
        self.assertIsNotNone(self.w.meta("vacuum_at"))
        self.assertLess(os.path.getsize(paths.metrics_db()), 1024 * 1024)   # VACUUM gave the file back
        self.assertIn("metrics.db: over 0.5 MB", self.read("/var/log/kaskad.log"))

    # REV-s1-t3 З2: the cap cuts the bigger table first and never below 7 days of h1 / 30 days of events
    def fill_90_days(self, h1_pad, ev_pad):
        with self.w.conn:
            for i in range(90 * 24):
                ts = NOW - 90 * 86400 + i * H + 60
                self.w.conn.execute("INSERT INTO h1 (exit, ts, state) VALUES ('e-a', ?, ?)", (ts, "x" * h1_pad))
                self.w.conn.execute("INSERT INTO events VALUES (?, 'transition', 'e-a', ?)", (ts, "y" * ev_pad))

    def test_cap_bigger_table_first(self):
        self.fill_90_days(10, 600)                                    # events are the big one
        h1 = self.rows("SELECT COUNT(*) FROM h1")[0][0]
        cap = self.w.used_bytes() * 0.7 / 1048576
        self.w.maintain(NOW, max_mb=cap)
        self.assertLessEqual(self.w.used_bytes(), cap * 1048576)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM h1")[0][0], h1)                  # h1 untouched
        self.assertGreater(self.rows("SELECT MIN(ts) FROM events")[0][0], NOW - 89 * 86400)
        self.assertNotIn("too small", self.read("/var/log/kaskad.log"))

    def test_cap_keeps_the_floors_and_warns(self):
        self.fill_90_days(300, 300)
        week = self.rows("SELECT COUNT(*) FROM h1 WHERE ts >= ?", NOW - 7 * 86400)[0][0]
        month = self.rows("SELECT COUNT(*) FROM events WHERE ts >= ?", NOW - 30 * 86400)[0][0]
        self.w.maintain(NOW, max_mb=0.05)                              # far too small
        self.assertEqual(self.rows("SELECT COUNT(*) FROM h1")[0][0], week)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM events")[0][0], month)
        log = self.read("/var/log/kaskad.log")
        self.assertIn("metrics.db: still over 0.05 MB", log)
        self.assertIn("too small", log)
        for h in (1, 2, 3):                                            # REV-s1-t3-2 П1, П2: three more hours
            self.w.maintain(NOW + h * H, max_mb=0.05)
        log = self.read("/var/log/kaskad.log")
        self.assertEqual(log.count("too small"), 1)                    # once a run
        self.assertEqual(log.count(DROPPED), 1)                        # at the floors: no line every hour
        # REV-s1-t3b Н-б: the limit raised (under it) -> lowered again: a new excess at the floors is told again
        self.w.maintain(NOW + 4 * H, max_mb=100)
        self.w.maintain(NOW + 5 * H, max_mb=0.05)
        log = self.read("/var/log/kaskad.log")
        self.assertEqual(log.count("too small"), 2)
        self.assertEqual(log.count(DROPPED), 2)

    def test_under_cap_nothing_cut(self):
        self.w.write_minute(NOW, {"e-a": point()})
        self.w.maintain(NOW)
        self.assertIsNone(self.w.meta("vacuum_at"))


class ReadTest(MetricsCase):
    def test_history_1d(self):
        for i in range(30):
            self.w.write_minute(NOW - 1800 + i * 60, {"e-a": point(state="off" if 10 <= i < 13 else "ok",
                                                                   rtt_med=float(i)), "e-b": point()})
        self.w.add_speed("e-a", NOW - 1800 + 5 * 60 + 7, mbps=88.5)
        h = metrics.history("e-a", "1d", NOW)
        self.assertTrue(h["charts"])
        self.assertEqual(h["step"], 60)
        self.assertEqual(h["cols"], ["rtt_med", "rtt_p90", "loss", "jitter", "rx", "tx", "devices", "mbps"])
        self.assertEqual(len(h["points"]), 30)
        self.assertEqual(h["points"][5][0], NOW - 1800 + 300)
        self.assertEqual(h["points"][5][1], 5.0)
        self.assertEqual(h["points"][5][8], 88.5)
        self.assertIsNone(h["points"][6][8])
        b = NOW - 1800
        self.assertEqual(h["states"], [[b, b + 600, "ok"], [b + 600, b + 780, "off"], [b + 780, b + 1800, "ok"]])

    def test_ranges_at_most_1440_points(self):
        with self.w.conn:
            for i in range(2200):
                self.w.conn.execute("INSERT INTO h1 (exit, ts, rx, state) VALUES ('e-a', ?, 1, 'ok')",
                                    (NOW // H * H - i * H,))
            for i in range(1100):
                self.w.conn.execute("INSERT INTO m10 (exit, ts, rx, state) VALUES ('e-a', ?, 1, 'ok')",
                                    (NOW // 600 * 600 - i * 600,))
        h = metrics.history("e-a", "90d", NOW)
        self.assertEqual(h["step"], 7200)
        self.assertLessEqual(len(h["points"]), 1440)
        self.assertGreater(len(h["points"]), 1000)
        self.assertEqual(h["points"][-1][5], 1)                          # rx of the last bucket (one hour in it)
        self.assertEqual(h["points"][-2][5], 2)
        h = metrics.history("e-a", "7d", NOW)
        self.assertEqual(h["step"], 600)
        self.assertLessEqual(len(h["points"]), 1440)
        self.assertEqual(len(h["points"]), 1009)

    def test_transitions_pages(self):
        for i in range(7):
            ts = NOW - 1000 + i * 100
            self.w.add_transition(ts, "e-a", "ok", "off", "dead", {"rule": None})
            if i == 3:                                                   # two in one second
                self.w.add_transition(ts, "e-a", "off", "ok", None)
        self.w.add_transition(NOW - 500, "e-b", "ok", "off", "dead")
        self.w.add_event(NOW - 400, "exit_dead", "e-a", {"id": "e-a"})   # published: not a transition
        self.w.add_transition(NOW - 10 * 86400, "e-a", "ok", "standby", "manual")
        p1 = metrics.transitions("e-a", days=7, limit=4, now=NOW)
        self.assertTrue(p1["db"])
        self.assertEqual([i["ts"] for i in p1["items"]], [NOW - 400, NOW - 500, NOW - 600])   # the second split off
        self.assertEqual(p1["items"][0], {"ts": NOW - 400, "from": "ok", "to": "off", "why": "dead",
                                          "args": {"rule": None}})
        self.assertEqual(p1["next"], NOW - 700 + 1)
        p2 = metrics.transitions("e-a", days=7, limit=4, before=p1["next"], now=NOW)
        self.assertEqual([i["ts"] for i in p2["items"]], [NOW - 700, NOW - 700, NOW - 800, NOW - 900])
        self.assertEqual([i["to"] for i in p2["items"][:2]], ["ok", "off"])                   # the newer first
        p3 = metrics.transitions("e-a", days=7, limit=4, before=p2["next"], now=NOW)
        self.assertEqual([i["ts"] for i in p3["items"]], [NOW - 1000])
        self.assertIsNone(p3["next"])
        allof = metrics.transitions("e-a", days=90, limit=500, now=NOW)
        self.assertEqual(len(allof["items"]), 9)
        self.assertEqual(metrics.transitions("e-a", days=0, limit=10 ** 6, now=NOW)["db"], True)  # clamped, no error

    def test_transitions_one_second_fills_a_page(self):
        """REV-s1-t3 П3: a page filled by one second takes the whole second — nothing lost between pages."""
        for i in range(3):
            self.w.add_transition(NOW - 100, "e-a", "ok", "off", "dead", {"i": i})
        self.w.add_transition(NOW - 200, "e-a", "off", "ok", None)
        seen, before = [], None
        for _ in range(5):
            p = metrics.transitions("e-a", days=7, limit=2, before=before, now=NOW)
            seen += [(i["ts"], i["args"].get("i")) for i in p["items"]]
            before = p["next"]
            if before is None:
                break
        self.assertEqual(seen, [(NOW - 100, 2), (NOW - 100, 1), (NOW - 100, 0), (NOW - 200, None)])
        p = metrics.transitions("e-a", days=7, limit=1, before=NOW - 99, now=NOW)
        self.assertEqual((len(p["items"]), p["next"]), (3, NOW - 100))
        p = metrics.transitions("e-a", days=7, limit=1, before=NOW - 150, now=NOW)
        self.assertEqual((len(p["items"]), p["next"]), (1, None))
        for i in range(3):                                             # REV-s1-t3-2 П1: nothing older — no next
            self.w.add_transition(NOW - 50, "e-b", "ok", "off", "dead", {"i": i})
        p = metrics.transitions("e-b", days=7, limit=2, now=NOW)
        self.assertEqual((len(p["items"]), p["next"]), (3, None))

    def test_feed_limit_clamped(self):
        """REV-s1-t3 П9: a negative or huge limit does not give every event of 90 days."""
        for i in range(3):
            self.w.add_event(NOW - 30 + i, "exit_dead", "e-a", {"i": i})
        self.assertEqual(len(metrics.feed(0, -1)), 1)
        self.assertEqual([e["args"]["i"] for e in metrics.feed(0, 0)], [2])
        self.assertEqual(len(metrics.feed(0, 10 ** 6)), 3)

    def test_feed_after_reboot(self):
        self.w.add_event(NOW - 30, "exit_dead", "e-a", {"id": "e-a", "name": "Вена"})
        self.w.add_transition(NOW - 20, "e-a", "ok", "off", "dead")
        self.w.add_event(NOW - 10, "node_offline", None, {"since": NOW - 10})
        f = metrics.feed(0)
        self.assertEqual([(e["key"], e["exit"]) for e in f], [("exit_dead", "e-a"), ("node_offline", None)])
        self.assertEqual(f[0]["args"]["name"], "Вена")
        self.assertEqual([e["key"] for e in metrics.feed(NOW - 30)], ["node_offline"])


class WriterSafetyTest(RootCase):
    """REV-s1-t3 П5, П8: the writer never stops a tick and leaves the mode of /var/lib/kaskad alone."""

    def test_directory_that_cannot_be_made(self):
        with mock.patch.object(metrics.util, "ensure_dir", side_effect=OSError("read-only")):
            self.assertIsNone(metrics.open_writer())
        self.assertIn("metrics.db: does not open", self.read("/var/log/kaskad.log"))

    def test_mode_of_an_existing_directory_kept(self):
        d = os.path.dirname(paths.metrics_db())
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o755)
        w = metrics.open_writer()
        self.addCleanup(w.close)
        self.assertEqual(oct(os.stat(d).st_mode & 0o777), "0o755")

    def test_bad_values_do_not_raise(self):
        w = metrics.open_writer()
        self.addCleanup(w.close)
        self.assertFalse(w.write_minute("soon", {"e-a": point()}))
        self.assertFalse(w.add_event(NOW, "exit_dead", "e-a", {"x": object()}))
        self.assertFalse(w.add_speed("e-a", None, 10))
        self.assertFalse(w.maybe_maintain("now"))
        self.assertTrue(w.write_minute(NOW, {"e-a": point()}))       # and it goes on


class NoSqliteTest(RootCase):
    def test_charts_off_without_sqlite(self):
        with mock.patch.object(metrics, "sqlite3", None):
            self.assertFalse(metrics.available())
            self.assertIsNone(metrics.open_writer())
            h = metrics.history("e-a", "1d", NOW)
            self.assertFalse(h["charts"])
            self.assertEqual(h["points"], [])
            self.assertEqual(metrics.transitions("e-a", now=NOW), {"db": False, "items": [], "next": None})
            self.assertIsNone(metrics.feed())
        self.assertFalse(os.path.exists(paths.metrics_db()))

    def test_no_base_yet(self):
        self.assertFalse(metrics.history("e-a", "7d", NOW)["charts"])
        self.assertFalse(metrics.transitions("e-a", now=NOW)["db"])

    def test_base_that_does_not_open(self):
        self.write("/var/lib/kaskad/metrics.db", "this is no sqlite file at all, really" * 100)
        self.assertIsNone(metrics.open_writer())
        self.assertFalse(metrics.history("e-a", "1d", NOW)["charts"])
        self.assertIn("metrics.db: does not open", self.read("/var/log/kaskad.log"))
