"""REV-ns-b6 З-3, П-3: the rules of /api/host/score and /api/host/day/<date> the contract tests did not hold — the
numbers of links[].hint (▼ and «ждёт» day), today's partial day of the probes out of ▲/▼, the window of the reason
``offline``, the hour before midnight of the h1 series (zones +5:30), «Связь с людьми» over 24 h, the cache of the closed
days (a check added later, the rule ▲/▼)."""

import shutil
import time
from unittest import mock

from kaskad import score as S
from kaskad import tz
from kaskad.bench import hostscore as HS
from kaskad.bench import spool
from kaskad.gw import metrics
from tests.panel.test_panel_score import DAY, S4, SPEC, ScoreCase

UTC = tz.load("UTC")
H = 3600


class NoonCase(ScoreCase):
    """The clock at noon of today of the node (REV-ns-b14-2 З-2): the checks «a day minus an hour ago» stay in the days
    the test means at any real time — from 23:00 to 00:59 of the node they fell into other days."""

    def setUp(self):
        real = time.time
        lt = tz.local(real())
        off = tz.to_utc(lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 0) - real()
        p = mock.patch("time.time", new=lambda: real() + off)
        p.start()
        self.addCleanup(p.stop)
        super().setUp()


def link(doc, lid):
    return next(x for x in doc["links"] if x["id"] == lid)


class HintTest(NoonCase):
    """M1, M2: hint is a contract field with numbers, not only a key."""

    def series(self, last):
        for d in range(14, 2, -1):
            self.check(d * DAY)
        for i, k in enumerate(last):
            self.check((2 - i) * DAY - H, dict(S4, net_down=S4["net_down"] * k))

    def test_mark_down_has_the_two_days(self):
        self.series((0.5, 0.55))
        lk = link(self.score(), "down")
        self.assertEqual(lk["dev"], "down")
        h = lk["hint"]
        self.assertEqual(h["test"], "net_down")
        self.assertEqual([x[1] for x in h["days"]], [round(S4["net_down"] * 0.5, 3), round(S4["net_down"] * 0.55, 3)])
        self.assertEqual([x[0] for x in h["days"]], [tz.day(self.now - 2 * DAY - H), tz.day(self.now - DAY - H)])
        self.assertAlmostEqual(h["usual"], S4["net_down"], places=2)

    def test_pending_day_has_the_last_day(self):
        self.series((0.5,))
        lk = link(self.score(), "down")
        self.assertEqual((lk["dev"], lk["dev_pending"], lk["dev_pending_kind"]), (None, "down", "day"))
        self.assertEqual(lk["hint"]["days"], [[tz.day(self.now - 2 * DAY - H), round(S4["net_down"] * 0.5, 3)]])
        self.assertEqual(lk["hint"]["now"], round(S4["net_down"] * 0.5, 3))

    def test_no_mark_no_hint(self):
        self.series(())
        self.assertIsNone(link(self.score(), "down")["hint"])


class ProbeCase(ScoreCase):
    def ping(self, hours, lost=lambda ts: 0, anchors=("a", "b", "c")):
        w = metrics.open_writer()
        try:
            with w.conn:
                for ts in hours:
                    for a in anchors + ("*",):
                        w.conn.execute("INSERT OR REPLACE INTO host_ping_h1 (anchor, ts, n, lost, runs, rtt_med, "
                                       "rtt_p90, jit, jit_n) VALUES (?,?,?,?,?,?,?,?,?)",
                                       (a, ts, 1080, lost(ts), 1 if lost(ts) else 0, 30.0, 40.0, 1.5, 1000))
                    w.conn.execute("INSERT OR REPLACE INTO host_h1 (ts, n, busy, busy_max, steal, warn_min, alarm_min)"
                                   " VALUES (?,?,?,?,?,?,?)", (ts, 60, 10.0, 30.0, 0.2, 0, 0))
        finally:
            w.close()


class ProbeDayTest(ProbeCase):
    def test_todays_partial_day_is_not_in_the_marks(self):
        """M11: at 20:30 today has 20 h of rows (≥ WINDOW_HOURS_MIN) of a bad loss — it is not a day of ▲/▼ yet:
        the last whole day decides (no «ждёт подтверждения» from today)."""
        mid = (self.now // DAY - 1) * DAY                # a UTC midnight in the past
        now = mid + 20 * H + 1800
        # 20 % today, ~0.1–0.3 % before; the noise counted back from mid (the day before — the lightest): by the date
        # (ts // DAY) the day before was the worst every third day — ▼ two days running, not «ждёт» (REV-ns-b14-2 З-2)
        bad = lambda ts: 216 if ts >= mid else (1 + ((mid - 1 - ts) // DAY) % 3)
        self.ping(range(mid - 25 * DAY, mid + 20 * H, H), bad)
        self.assertGreaterEqual(20, S.WINDOW_HOURS_MIN)
        doc = HS.score_doc(SPEC, now, UTC)
        lk = link(doc, "loss")
        self.assertGreaterEqual(lk["usual_n"], S.N_BASE_MIN)
        self.assertEqual((lk["dev"], lk["dev_pending"]), (None, None))
        HS.reset()
        later = HS.score_doc(SPEC, mid + DAY + 60, UTC)      # the day closed: now it counts — «ждёт» by one day
        self.assertEqual(link(later, "loss")["dev_pending"], "down")


class DayPageTest(ProbeCase):
    def test_offline_window(self):
        """M12: the reason «offline» of a day carries its window."""
        day = tz.day(self.now - 3 * DAY, UTC)
        start, end = HS.day_span(day, UTC)
        self.ping([ts for ts in range(start, end, H) if not start + 3 * H <= ts < start + 5 * H])
        w = metrics.open_writer()
        try:
            w.add_event(start + 3 * H, "node_offline", None, {"since": start + 3 * H})
            w.add_event(start + 5 * H, "node_online", None, {"since": start + 3 * H, "until": start + 5 * H})
        finally:
            w.close()
        doc = HS.day_doc(SPEC, day, self.now, UTC)
        self.assertEqual(doc["reason"]["code"], "offline")
        self.assertEqual(doc["reason"]["window"], {"from": "03:00", "to": "05:00"})

    def test_the_hour_before_midnight_of_h1(self):
        """M14: in a zone +5:30 the h1 row from 23:30 (local) of the day before covers 00:00–00:30 of the day."""
        z = tz.load("Asia/Kolkata")
        day = tz.day(self.now - 20 * DAY, z)
        start, end = HS.day_span(day, z)
        self.ping(range(start - 1800, end, H), lambda ts: 108 if ts == start - 1800 else 0)
        doc = HS.day_doc(SPEC, day, self.now, z)
        loss = doc["series"]["loss"]
        self.assertEqual(loss[0], 10.0)
        self.assertEqual(loss[1], 10.0)
        self.assertEqual(loss[2], 0.0)


class PeopleTest(ScoreCase):
    def test_people_over_24_hours(self):
        """M15: an hour older than 24 h is not in «Связь с людьми»."""
        w = metrics.open_writer()
        try:
            w.write_people_hour((self.now // H - 30) * H, {
                "conns": 30, "rtt_med": 40.0, "rtt_p90": 60.0, "rtt_min_med": 30.0, "sent": 10 ** 8,
                "retr": 10 ** 6, "dsack": 0, "why": None, "inputs": {"vless": "ok"}, "partial": 0})
        finally:
            w.close()
        self.assertEqual(self.score()["rows"]["people"], HS.people_row([]))


class CacheTest(NoonCase):
    def day_entry(self, doc, day):
        return next(d for d in doc["days"] if d["day"] == day)

    def test_a_check_added_later_recolours_a_closed_day(self):
        """M6: a closed day is cached by its inputs — a check written later (a file of the queue) is one of them: a
        quick check marks the day «тест», a full one written after it — «• полный тест» (no score of a day, NS-D34)."""
        ago = 5 * DAY
        day = tz.day(self.now - ago)
        for d in range(20, 5, -1):
            self.check(d * DAY)
        before = self.day_entry(self.score(), day)
        self.assertFalse(before["test"])
        self.check(ago, mode="quick")
        after = self.day_entry(self.score(), day)
        self.assertEqual((after["test"], after["full"]), (True, False))
        self.check(ago - 60)
        again = self.day_entry(self.score(), day)
        self.assertEqual((again["test"], again["full"]), (True, True))          # the same «test», now a full one
        self.assertNotIn("score", again)

    def test_the_rule_is_in_the_key(self):
        """П-3: the colour of a closed day by ▼ depends on gateway.hostmon.dev — another rule, another entry."""
        for d in range(20, 3, -1):
            self.check(d * DAY)
        self.check(3 * DAY - H, dict(S4, net_down=S4["net_down"] * 0.85))
        self.check(2 * DAY - H, dict(S4, net_down=S4["net_down"] * 0.85))
        day = tz.day(self.now - 2 * DAY - H)
        a = self.day_entry(HS.score_doc(SPEC, self.now, rule={"pct": 10, "use_spread": False}), day)
        b = self.day_entry(HS.score_doc(SPEC, self.now, rule={"pct": 30, "use_spread": False}), day)
        self.assertEqual(a["reason"]["code"], "link_down")
        self.assertEqual(b["color"], "green")
        shutil.rmtree(spool.spool_dir(), ignore_errors=True)
