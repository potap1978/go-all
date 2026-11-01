"""A7 (k3/handoff/SPEC-node-score.md §3.8, §3.9 п. 4–5): the routes of kaskad.panel.host on the fake node.

GET /api/host/health — the three sources in order (a fresh live.json → a fresh host-health.json of the bot → the
panel's own snapshot), its cache of 30 s (REV-sec П-6), the hint of the bot on bash (REV-3 П-3), the reader's language;
GET /api/host/history — the ranges, 400 for another, ``charts: false`` without sqlite3. Both — login, GET only."""

import json
import time
from unittest import mock

from kaskad import i18n, snapshot, state
from kaskad.gw import hostprobe, live, metrics
from kaskad.panel import host as phost
from tests.gw.test_collect_host import HostWorld, Vfs
from tests.panel.panelcase import PanelCase


class HostPanelCase(PanelCase):
    """The panel on a node with a fake /proc (HostWorld.tree), the disk at ``self.disk`` %, no live.json."""

    def setUp(self):
        super().setUp()
        phost.reset()
        self.addCleanup(phost.reset)
        self.disk = 50.0
        HostWorld.tree(self)
        for p in (mock.patch.object(hostprobe, "_statvfs", lambda path: Vfs(self.disk)),
                  mock.patch.object(hostprobe, "clock", lambda: {"synced": True, "offset_s": 0.001}),
                  mock.patch.object(hostprobe, "_certs", lambda cfg: [])):
            p.start()
            self.addCleanup(p.stop)

    def live_json(self, health, age=1.0):
        doc = {"v": live.V, "ts": time.time() - age, "host": {"cpu": 3.0, "health": health}}
        self.write("/run/kaskad/live.json", json.dumps(doc), mode=0o644)
        snapshot.forget()

    def bot_file(self, health, age=10.0):
        at = time.time() - age
        self.write("/run/kaskad/host-health.json", json.dumps({"v": 1, "at": at, "health": dict(health, at=at)}))

    def gw_off(self, bot_impl=None):
        with state.locked():
            st = state.load()
            st["gateway"]["enabled"] = False
            state.save(st)
        if bot_impl:
            self.write("/etc/kaskad/config", 'BOT_IMPL="%s"\n' % bot_impl)

    def health(self, **kw):
        return self.ok("GET", "/api/host/health", **kw)


BOT = {"v": 1, "level": "warn", "since": None, "instant": True, "by": "bot", "hint": None, "probes": None,
       "top": [{"id": "disk", "level": "warn", "stale": False, "confirm_until": None, "text": "x", "advice": "y",
                "msg": {"key": "gw_host.find.disk.warn", "args": {"mount": "/", "gb": 8.0, "pct": 92}},
                "advice_msg": {"key": "gw_host.advice.disk", "args": {}}}],
       "info": [], "checks": {"disk": {"level": "warn", "value": 92.0, "shown": "warn"}}}


class HostHealthTest(HostPanelCase):
    def test_login_required_and_get_only(self):
        r = self.call("GET", "/api/host/health", tunnel=False)
        self.assertEqual(r.status, 401)
        r = self.call("GET", "/api/host/history", tunnel=False)
        self.assertEqual(r.status, 401)
        for path in ("/api/host/health", "/api/host/history"):
            for method in ("POST", "PUT", "DELETE"):
                self.assertEqual(self.call(method, path, {}).status, 405, (method, path))

    def test_fresh_live_json_is_the_collector(self):
        h = dict(BOT, by="collector", instant=False, level="ok", top=[], checks={"cpu": {"level": "ok"}})
        self.live_json(h)
        with mock.patch.object(hostprobe, "read", side_effect=AssertionError("no snapshot with a fresh live")):
            doc = self.health()
        self.assertEqual((doc["ok"], doc["by"], doc["instant"]), (True, "collector", False))
        self.assertEqual(doc["health"]["level"], "ok")
        self.assertIs(doc["live"]["fresh"], True)
        self.assertLess(doc["live"]["age"], 30)

    def test_stale_live_and_a_fresh_file_of_the_bot(self):
        self.live_json(dict(BOT, by="collector", instant=False, level="crit"), age=600)
        self.bot_file(BOT)
        with mock.patch.object(hostprobe, "read", side_effect=AssertionError("no snapshot with the bot's file")):
            doc = self.health()
        self.assertEqual((doc["by"], doc["instant"], doc["health"]["level"]), ("bot", True, "warn"))
        self.assertIs(doc["live"]["fresh"], False)
        f = doc["health"]["top"][0]
        self.assertNotEqual(f["text"], "x")                                  # said again by its msg
        self.assertIn("92", f["text"])
        en = self.health(headers={"X-Kaskad-Lang": "en"})["health"]["top"][0]   # the reader's language
        self.assertEqual(en["text"], "8 GB left on / (92% used)")
        self.assertNotEqual(en["text"], f["text"])

    def test_both_stale_the_panel_snapshot(self):
        self.live_json(dict(BOT, by="collector"), age=600)
        self.bot_file(BOT, age=400)                                          # older than HEALTH_FRESH (180 s)
        self.disk = 98.5
        doc = self.health()
        h = doc["health"]
        self.assertEqual((doc["by"], doc["instant"], h["by"], h["instant"]), ("panel", True, "panel", True))
        self.assertEqual(h["level"], "crit")
        self.assertEqual([f["id"] for f in h["top"]], ["disk"])
        self.assertTrue(h["top"][0]["text"])                                 # the words of the reader, not msgs only
        self.assertTrue(h["top"][0]["advice"])
        self.assertNotIn("cpu", h["checks"])                                 # the rules «instant» only
        self.assertIsNone(h["hint"])
        self.assertIsNone(h["hint_text"])

    def test_snapshot_read_once_in_30_s(self):
        self.gw_off()
        real = hostprobe.read
        with mock.patch.object(hostprobe, "read", side_effect=real) as rd:
            a = self.health()
            b = self.health(headers={"X-Kaskad-Lang": "en"})
            self.assertEqual(rd.call_count, 1)
            self.assertEqual(a["health"]["at"], b["health"]["at"])
            with mock.patch.object(phost.time, "monotonic", return_value=time.monotonic() + phost.SNAP_TTL + 1):
                self.health()
            self.assertEqual(rd.call_count, 2)

    def test_snapshot_said_in_each_readers_language(self):
        self.gw_off()
        self.disk = 98.5
        ru = self.health()["health"]["top"][0]
        en = self.health(headers={"X-Kaskad-Lang": "en"})["health"]["top"][0]
        self.assertNotEqual(ru["text"], en["text"])
        self.assertNotEqual(ru["advice"], en["advice"])

    def test_bot_on_bash_and_no_collector_hint(self):
        for case in ("gw_off", "engine"):
            with self.subTest(case=case):
                phost.reset()
                if case == "gw_off":
                    self.gw_off(bot_impl="bash")
                else:
                    with state.locked():
                        st = state.load()
                        st["gateway"]["enabled"] = True
                        st["engine"] = "legacy"
                        state.save(st)
                h = self.health()["health"]
                self.assertEqual(h["hint"], "bot_python")
                self.assertEqual(h["hint_text"], i18n.tr("gw_host.hint.bot_python", "ru"))
                en = self.health(headers={"X-Kaskad-Lang": "en"})["health"]
                self.assertEqual(en["hint_text"], i18n.tr("gw_host.hint.bot_python", "en"))

    def test_bot_on_python_no_hint(self):
        self.gw_off(bot_impl="python")
        self.assertIsNone(self.health()["health"]["hint"])

    def test_off_level(self):
        with state.locked():
            st = state.load()
            st["gateway"]["enabled"] = False
            st["gateway"]["hostmon"] = {"enabled": False}
            state.save(st)
        with mock.patch.object(hostprobe, "read", side_effect=AssertionError("off: nothing is read")):
            h = self.health()["health"]
        self.assertEqual(h["level"], "off")

    def test_way_of_a_point_said(self):
        h = dict(BOT, by="collector", instant=False,
                 probes={"on": True, "loss10": 0.0, "rtt10": 30.0, "norm": None,
                         "anchors": [{"addr": "1.1.1.1", "via": "icmp"}, {"addr": "8.8.8.8", "via": "dns"},
                                     {"addr": "9.9.9.9", "via": None}]})
        self.live_json(h)
        a = self.health()["health"]["probes"]["anchors"]
        self.assertEqual([x["via_text"] for x in a], [i18n.tr("gw_host.via.icmp", "ru"),
                                                      i18n.tr("gw_host.via.dns", "ru"), None])
        a = self.health(headers={"X-Kaskad-Lang": "en"})["health"]["probes"]["anchors"]
        self.assertEqual(a[0]["via_text"], i18n.tr("gw_host.via.icmp", "en"))


class HostHistoryTest(HostPanelCase):
    def test_ranges(self):
        for rng in ("1d", "7d", "90d"):
            doc = self.ok("GET", "/api/host/history?range=" + rng)
            self.assertIs(doc["ok"], True)
            self.assertEqual(set(doc), {"ok", "charts", "host", "ping"})
            self.assertEqual(doc["host"]["step"], metrics._span(rng, 0)[2])
            self.assertIn("anchors", doc["ping"])
        self.assertEqual(self.ok("GET", "/api/host/history")["host"]["step"], metrics.STEP["m1"])   # default 1d

    def test_another_range_is_400(self):
        for rng in ("2d", "1d;", "../1d", "1D"):
            doc = self.err(400, "GET", "/api/host/history?range=" + rng)
            self.assertEqual(doc["data"], {"field": "range"})

    def test_points_of_the_base(self):
        if not metrics.available():
            self.skipTest("no sqlite3")
        now = int(time.time())
        w = metrics.open_writer()
        self.assertIsNotNone(w)
        try:
            w.write_host_minute(now - 120, {"n": 6, "busy": 12.0, "disk": 50.0})
        finally:
            w.close()
        doc = self.ok("GET", "/api/host/history?range=1d")
        self.assertIs(doc["charts"], True)
        self.assertIs(doc["host"]["charts"], True)
        self.assertEqual(len(doc["host"]["points"]), 1)

    def test_no_sqlite3_charts_false(self):
        with mock.patch.object(metrics, "sqlite3", None):
            doc = self.ok("GET", "/api/host/history?range=7d")
        self.assertEqual(doc, {"ok": True, "charts": False})
