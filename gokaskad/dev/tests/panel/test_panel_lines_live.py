"""3.3.7.10 (владелец 24.10: «нет автоподгрузки — графики молчат»): what the panel needs to move the lines of
«Пользователи» and «Выходы» without loading the node. The line of «Выходы» for all exits in ONE answer
(``metrics.exit_summary``, ``GET /api/gw/exits/activity``) instead of the history of a day of every exit; kept in the
process by the minute of m1 the collector wrote (``metrics.ts`` of live.json) — one query a minute whatever the tabs,
ETag and 304; only the admin and a GET that runs nothing. GET overview carries the marks of the lines (the page without
SSE); the history of a device says where it ends (``now``) — the panel adds the open window after it."""

import json
import os
import time
from unittest import mock

from kaskad import paths, util
from kaskad.gw import live, metrics, ops
from kaskad.panel import api, routes, sse
from tests.panel.panelcase import PanelCase

E1, E2 = "e-0123456789", "e-abcdefabcd"


class ExitLineCase(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway()
        api._EXA.update(key=None, body=None)

    def minute(self, ts, rows):
        w = metrics.open_writer()
        try:
            self.assertTrue(w.write_minute(ts, rows))
        finally:
            w.close()

    def live(self, **kw):
        """A fresh live.json of the collector with the marks ``kw`` (activity, metrics)."""
        doc = {"v": 1, "ts": time.time(), "seq": 1, "gateway": {"enabled": True, "problems": []}, "inbounds": {},
               "exits": {}, "devices": {}, "forwards": {}, "host": {}, "events": []}
        doc.update(kw)
        util.atomic_write(paths.live_json(), json.dumps(doc), 0o600)


class SummaryTest(ExitLineCase):
    def test_windows_states_and_totals(self):
        now = 1791190260                                      # 16:11 of a day: the last window 16:00–16:30 up to now
        end = now // 1800 * 1800
        t0 = end - 47 * 1800
        self.minute(end + 60, {E1: {"rx": 3 * 1024 * 1024, "tx": 1024 * 1024, "state": "ok"},
                               E2: {"rx": None, "tx": None, "state": "off"}})
        self.minute(end + 120, {E1: {"rx": 1024, "tx": 0, "state": "degraded"}})
        self.minute(t0 + 30, {E1: {"rx": 2048, "tx": 2048, "state": "ok"}})          # the first window
        self.minute(t0 - 60, {E1: {"rx": 10 ** 9, "tx": 0, "state": "off"}})          # older than the day: not counted
        self.minute(now, {E1: {"rx": 10 ** 9, "tx": 0, "state": "ok"}})               # the minute of now is not written yet
        s = metrics.exit_summary(now)
        self.assertEqual((s["charts"], s["step"], s["n"], s["t0"], s["now"]), (True, 1800, 48, t0, now))
        e1, e2 = s["exits"][E1], s["exits"][E2]
        self.assertEqual((e1["kb"][47], e1["kb"][0], e1["kb"][1]), (4097, 4, 0))
        self.assertEqual((e1["nd"][47], e1["nd"][0], e1["nd"][1]), (0, 0, 1))         # no point — «нет данных»
        self.assertEqual((e1["st"][47], e1["st"][0]), (2, 1))                         # the worst of the window
        self.assertEqual((e1["rx"], e1["tx"], e1["off"], e1["deg"]), (3 * 1024 * 1024 + 1024 + 2048, 1024 * 1024 + 2048, 0, 60))
        self.assertEqual((e2["nd"][47], e2["st"][47], e2["off"], e2["kb"][47]), (1, 3, 60, 0))   # down, no counters
        self.assertEqual(sorted(e1), ["deg", "kb", "nd", "off", "rx", "st", "tx"])

    def test_no_base_no_charts(self):
        s = metrics.exit_summary(1791190260)
        self.assertEqual((s["charts"], s["exits"]), (False, {}))


class ApiTest(ExitLineCase):
    def test_only_the_admin_and_a_get_that_runs_nothing(self):
        r, _m = routes.find("GET", "/api/gw/exits/activity")
        self.assertEqual((r.auth, r.runs, r.pattern), ("admin", False, r"/api/gw/exits/activity"))
        self.assertEqual(self.call("GET", "/api/gw/exits/activity", tunnel=False).status, 401)

    def test_etag_304_and_one_query_a_minute_of_m1(self):
        now = int(time.time())
        self.minute(now - 60, {E1: {"rx": 5, "tx": 5, "state": "ok"}})
        self.live(metrics={"ts": now // 60 * 60 - 60})
        real, calls = metrics.exit_summary, []

        def counted(end=None):
            calls.append(end)
            return real(end)
        with mock.patch.object(metrics, "exit_summary", counted):
            r = self.call("GET", "/api/gw/exits/activity")
            self.assertEqual(r.status, 200)
            tag = r.headers.get("etag")
            self.assertTrue(tag and tag.startswith('"exa-'))
            self.assertEqual(r.headers.get("cache-control"), "no-store")
            doc = r.json()
            self.assertEqual((doc["ok"], doc["charts"], doc["now"]), (True, True, now // 60 * 60))
            self.assertIn(E1, doc["exits"])
            r2 = self.call("GET", "/api/gw/exits/activity", headers={"If-None-Match": tag})
            self.assertEqual((r2.status, r2.body, r2.headers.get("etag")), (304, b"", tag))
            for _ in range(3):                                        # three tabs: one query
                self.ok("GET", "/api/gw/exits/activity")
            self.assertEqual(len(calls), 1)
            self.live(metrics={"ts": now // 60 * 60})                 # the collector wrote the next minute
            r3 = self.call("GET", "/api/gw/exits/activity", headers={"If-None-Match": tag})
            self.assertEqual(r3.status, 200)
            self.assertNotEqual(r3.headers.get("etag"), tag)
            self.assertEqual(len(calls), 2)

    def test_without_the_mark_the_minute_of_the_clock(self):
        self.live()                                                   # an old collector: no metrics in live.json
        doc = self.ok("GET", "/api/gw/exits/activity")
        self.assertEqual(doc["now"] % 60, 0)
        self.assertLessEqual(abs(doc["now"] - time.time()), 61)
        self.assertEqual(api._exa_mark(int(time.time())), None)
        self.live(metrics={"ts": int(time.time()) - 3600})            # a mark an hour old: not the end shown
        self.assertEqual(api._exa_mark(int(time.time())), None)


class OverviewMarksTest(ExitLineCase):
    def test_the_marks_with_fresh_data_only(self):
        act = {"ts": 1791190200, "w": 1791190500, "at": 1791190510, "secs": 10, "cur": {"d-1a2b3c4d": [10, 10, 5, 5]}}
        self.live(activity=act, metrics={"ts": 1791190440})
        doc = self.ok("GET", "/api/overview")
        self.assertEqual((doc["activity"], doc["metrics"]), (act, {"ts": 1791190440}))
        with mock.patch.object(api.live, "fresh", lambda age: False):
            doc = self.ok("GET", "/api/overview")
        self.assertNotIn("activity", doc)
        self.assertNotIn("metrics", doc)


class PollViewerTest(ExitLineCase):
    """3.3.7.11 (владелец 27.10 «всё до 5 с»): the fallback poll without the stream (GET /api/overview) is a panel that
    watches too — the beacon live.want, so the collector keeps «Пользователи» and «Выходы» ≤ 5 s old for it; at most
    every WANT_EVERY s; a request without the login touches nothing."""

    def setUp(self):
        super().setUp()
        sse._POLL_WANT[0] = 0.0
        self.live()

    def test_the_poll_touches_the_beacon(self):
        self.assertFalse(live.want_fresh())
        self.ok("GET", "/api/overview")
        self.assertTrue(live.want_fresh())
        os.remove(paths.live_want())
        self.ok("GET", "/api/overview")                                  # within WANT_EVERY: not again
        self.assertFalse(os.path.exists(paths.live_want()))
        self.assertTrue(sse.poll_seen(now=time.time() + sse.WANT_EVERY + 1))
        self.assertTrue(live.want_fresh())

    def test_without_the_login_nothing(self):
        self.assertEqual(self.call("GET", "/api/overview", tunnel=False).status, 401)
        self.assertFalse(os.path.exists(paths.live_want()))


class HistoryEndTest(ExitLineCase):
    def test_the_history_of_a_device_says_where_it_ends(self):
        did = ops.device_add(self.w.runner, self.uid, "Телефон", kind="phone", platform="ios", access=["wg"])["device"]["id"]
        b = int(time.time()) // 300 * 300
        w = metrics.open_writer()
        try:
            self.assertTrue(w.write_dev({"ts": b - 300, "rounds": 30, "secs": 300, "rows": {did: [300, 0, 5, 5]}}))
        finally:
            w.close()
        for rng, step in (("1d", 300), ("7d", 3600)):
            h = self.ok("GET", "/api/gw/devices/%s/activity?range=%s" % (did, rng))
            self.assertEqual(h["now"], b, rng)                          # the end of the window written last
            self.assertTrue(h["t0"] + (h["n"] - 1) * step < h["now"] <= h["t0"] + h["n"] * step, rng)
