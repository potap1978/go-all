"""The API of the activity of devices (k3/handoff/SPEC-activity.md §5, §8.1; AC4): only the admin and only GET that
runs nothing; the summary of «Люди» (48 windows), its cache by the newest window and its ETag (304); the history of a
device and of a person; ``range`` 400, an unknown or deleted id 404 and never in the summary; the history off and no
sqlite3; the switch with its ver; the size on 150 devices; /p/ shows none of it (ACT-7)."""

import json
import os
import time
from unittest import mock

from kaskad import state
from kaskad.gw import metrics, model, ops, policy_ops
from kaskad.panel import routes, userpage
from tests.panel.panelcase import PanelCase

PATHS = ("/api/gw/activity", "/api/gw/devices/d-1a2b3c4d/activity", "/api/gw/users/u-1a2b3c4d/activity",
         "/api/gw/activity/settings")


def win(ts, rows):
    return {"ts": ts, "rounds": 30, "secs": 300, "rows": rows}


class ActApiCase(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway()
        self.d1 = ops.device_add(self.w.runner, self.uid, "Ноутбук", kind="laptop", platform="linux",
                                 access=["wg"])["device"]["id"]
        self.d2 = ops.device_add(self.w.runner, self.uid, "Телефон", kind="phone", platform="ios",
                                 access=["wg"])["device"]["id"]
        self.b = int(time.time()) // 300 * 300

    def put(self, *wins):
        w = metrics.open_writer()
        try:
            for x in wins:
                self.assertTrue(w.write_dev(x))
        finally:
            w.close()


class AccessTest(ActApiCase):
    def test_only_the_admin_and_only_a_get_that_runs_nothing(self):
        for p in PATHS:
            r, _m = routes.find("GET", p)
            self.assertIsInstance(r, routes.Route, p)
            self.assertEqual((r.auth, r.runs), ("admin", False), p)
            r = self.call("GET", p + ("?range=1d" if "/d-" in p or "/u-" in p else ""), tunnel=False)
            self.assertEqual(r.status, 401, p)                       # from the internet without a session
        for r in routes.ROUTES:
            if "activity" in r.pattern:
                self.assertEqual(r.auth, "admin", r.pattern)
                self.assertIn(r.method, ("GET", "PUT", "DELETE"), r.pattern)
                if r.method == "PUT":
                    self.assertEqual(r.pattern, r"/api/gw/activity/settings")
                    self.assertTrue(r.csrf)
                if r.method == "DELETE":                     # №41 «Удалить историю активности»
                    self.assertEqual(r.pattern, r"/api/gw/activity")
                    self.assertTrue(r.csrf)
        self.assertEqual(self.call("PUT", "/api/gw/activity/settings", {"keep": False, "ver": "x"}, csrf=False).status,
                         403)

    def test_the_page_of_a_person_shows_none_of_it(self):
        """ACT-7: /p/ — no route of the page reads the activity, its code knows nothing of it."""
        for r in routes.ROUTES:
            if r.auth == "page":
                self.assertNotIn("activity", r.pattern)
        with open(userpage.__file__, encoding="utf-8") as f:
            text = f.read()
        for word in ("activity", "dev_m5", "dev_summary", "dev_history"):
            self.assertNotIn(word, text)

    def test_the_session_says_the_server_has_it(self):
        tok = self.login_admin()
        self.assertIs(self.ok("GET", "/api/session", tunnel=False, cookie=tok)["has"]["activity"], True)

    def login_admin(self):
        from kaskad.panel import auth
        auth.set_password("правильный-пароль", "admin")
        return self.login("правильный-пароль")


class SummaryTest(ActApiCase):
    def test_the_summary_of_the_state_only(self):
        self.put(win(self.b - 600, {self.d1: [300, 120, 2 * 1024 * 1024, 1024]}),
                 win(self.b - 300, {self.d1: [300, 0, 1024, 1024], "d-0badbeef": [300, 300, 10 ** 9, 10 ** 9]}))
        doc = self.ok("GET", "/api/gw/activity")
        self.assertEqual((doc["ok"], doc["activity"], doc["step"], doc["n"]), (True, True, 1800, 48))
        self.assertEqual(list(doc["devices"]), [self.d1])            # neither the device unknown nor one without data
        d = doc["devices"][self.d1]
        self.assertEqual((d["rx"], d["tx"], d["on_s"], d["act_s"]), (2 * 1024 * 1024 + 1024, 2048, 600, 120))
        self.assertEqual(len(d["on"]), 48)
        self.assertEqual(len(doc["cover"]), 48)
        self.assertGreater(doc["cover"][-1], 0)
        self.assertEqual(doc["now"], self.b)
        text = json.dumps(doc)
        self.assertNotIn("Ноутбук", text)                             # ids and numbers, no names (ACT-6)
        self.assertNotIn("10.77.", text)

    def test_a_deleted_device_is_gone_at_once(self):
        self.put(win(self.b - 300, {self.d1: [300, 0, 5, 5], self.d2: [300, 0, 5, 5]}))
        self.assertEqual(sorted(self.ok("GET", "/api/gw/activity")["devices"]), sorted([self.d1, self.d2]))
        ops.device_del(self.w.runner, self.d2)
        doc = self.ok("GET", "/api/gw/activity")
        self.assertEqual(list(doc["devices"]), [self.d1])            # the rows are still in the base: not shown
        self.err(404, "GET", "/api/gw/devices/%s/activity" % self.d2)

    def test_etag_and_304(self):
        self.put(win(self.b - 300, {self.d1: [300, 0, 5, 5]}))
        r = self.call("GET", "/api/gw/activity")
        self.assertEqual(r.status, 200)
        tag = r.headers.get("etag")
        self.assertTrue(tag and tag.startswith('"act-'))
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        r2 = self.call("GET", "/api/gw/activity", headers={"If-None-Match": tag})
        self.assertEqual((r2.status, r2.body), (304, b""))
        self.assertEqual(r2.headers.get("etag"), tag)
        self.put(win(self.b, {self.d1: [300, 0, 5, 5]}))             # a new window: a new tag
        r3 = self.call("GET", "/api/gw/activity", headers={"If-None-Match": tag})
        self.assertEqual(r3.status, 200)
        self.assertNotEqual(r3.headers.get("etag"), tag)
        ops.device_del(self.w.runner, self.d2)                      # the ids of the state changed: a new tag too
        r4 = self.call("GET", "/api/gw/activity", headers={"If-None-Match": r3.headers.get("etag")})
        self.assertEqual(r4.status, 200)

    def test_one_recount_a_window(self):
        self.put(win(self.b - 300, {self.d1: [300, 0, 5, 5]}))
        real, n = metrics.dev_summary, []

        def counted(*a, **kw):
            n.append(1)
            return real(*a, **kw)
        with mock.patch.object(metrics, "dev_summary", counted):
            for _ in range(4):
                self.ok("GET", "/api/gw/activity")
            self.assertEqual(len(n), 1)
            self.put(win(self.b, {self.d1: [300, 0, 5, 5]}))
            self.ok("GET", "/api/gw/activity")
            self.assertEqual(len(n), 2)

    def test_150_devices(self):
        """§4: everybody online all day — the answer of the summary under 100 KB (84 KB in the spec)."""
        with state.locked():
            st = state.load()
            g = model.gw(st)
            for i in range(148):
                g["devices"].append({"id": "d-%08x" % (0x5a000000 + i), "user": self.uid, "name": "Устройство %d" % i,
                                     "kind": "phone", "platform": "ios", "access": [], "enabled": True})
            state.save(st)
        ids = [d["id"] for d in model.devices(state.load())]
        self.assertEqual(len(ids), 150)
        w = metrics.open_writer()
        try:
            c = w.conn
            for i in range(289):
                ts = self.b - 300 * (289 - i)
                c.executemany("INSERT INTO dev_m5 VALUES (?,?,?,?,?,?)", [(ts, d, 300, 200, 123456789, 23456789)
                                                                        for d in ids])
                c.execute("INSERT INTO dev_node VALUES (300,?,30,300)", (ts,))
            c.execute("INSERT OR REPLACE INTO meta VALUES ('dev_flush', ?)", (str(self.b - 300),))
            c.commit()
        finally:
            w.close()
        t = time.monotonic()
        r = self.call("GET", "/api/gw/activity")
        took = time.monotonic() - t
        self.assertEqual(r.status, 200)
        self.assertEqual(len(r.json()["devices"]), 150)
        self.assertLess(len(r.body), 100 * 1024, len(r.body))
        self.assertLess(took, 5.0)


class HistoryTest(ActApiCase):
    def test_a_device_and_a_person(self):
        self.put(win(self.b - 300, {self.d1: [300, 60, 1000, 10], self.d2: [100, 100, 7, 7]}))
        h = self.ok("GET", "/api/gw/devices/%s/activity?range=1d" % self.d1)
        self.assertEqual((h["step"], h["n"], len(h["rx"]), h["rx"][-1], h["activity"]), (300, 288, 288, 1000, True))
        for rng, n in (("7d", 168), ("30d", 720)):
            h = self.ok("GET", "/api/gw/devices/%s/activity?range=%s" % (self.d1, rng))
            self.assertEqual((h["step"], h["n"], len(h["cover"])), (3600, n, n))
        u = self.ok("GET", "/api/gw/users/%s/activity?range=1d" % self.uid)
        self.assertEqual((u["rx"][-1], u["tx"][-1], u["on_s"][-1], u["act_s"][-1]), (1007, 17, 300, 100))
        self.assertEqual(sorted(x["id"] for x in u["devices"]), sorted([self.d1, self.d2]))
        self.assertEqual(self.ok("GET", "/api/gw/devices/%s/activity" % self.d1)["step"], 300)     # 1d by default

    def test_bad_range_unknown_ids(self):
        doc = self.err(400, "GET", "/api/gw/devices/%s/activity?range=90d" % self.d1)
        self.assertEqual(doc["data"]["field"], "range")
        self.err(400, "GET", "/api/gw/users/%s/activity?range=1h" % self.uid)
        self.err(404, "GET", "/api/gw/devices/d-0badbeef/activity?range=1d")
        self.err(404, "GET", "/api/gw/users/u-0badbeef/activity?range=1d")
        self.assertEqual(self.call("GET", "/api/gw/devices/../activity").status, 404)


class OffTest(ActApiCase):
    def test_the_history_off(self):
        self.put(win(self.b - 300, {self.d1: [300, 0, 5, 5]}))
        ver = self.ok("GET", "/api/gw/activity/settings")["ver"]
        res = self.ok("PUT", "/api/gw/activity/settings", {"keep": False, "ver": ver})
        self.assertIs(res["keep"], False)
        for p in ("/api/gw/activity", "/api/gw/devices/%s/activity?range=7d" % self.d1,
                  "/api/gw/users/%s/activity" % self.uid):
            self.assertEqual(self.ok("GET", p), {"ok": True, "activity": False, "why": "off"}, p)
        self.assertFalse(model.activity_keep(state.load()))
        self.assertIn("GW activity: history off", self.read("/var/log/kaskad.log"))

    def test_no_sqlite(self):
        with mock.patch.object(metrics, "sqlite3", None):
            self.assertEqual(self.ok("GET", "/api/gw/activity"), {"ok": True, "activity": False, "why": "nosqlite"})
            self.assertEqual(self.ok("GET", "/api/gw/devices/%s/activity" % self.d1)["why"], "nosqlite")
            self.assertIs(self.ok("GET", "/api/gw/activity/settings")["db"], False)

    def test_no_base_yet(self):
        self.assertFalse(os.path.exists(self.path("/var/lib/kaskad/metrics.db")))
        doc = self.ok("GET", "/api/gw/activity")
        self.assertEqual((doc["activity"], doc["devices"], sum(doc["cover"])), (True, {}, 0))


class SwitchTest(ActApiCase):
    def test_get_and_put_with_ver(self):
        doc = self.ok("GET", "/api/gw/activity/settings")
        self.assertEqual((doc["keep"], doc["kept"]), (True, {"m5_hours": 25, "h1_days": 30, "d1_days": 400}))
        self.err(400, "PUT", "/api/gw/activity/settings", {"keep": False})                    # no ver
        self.err(409, "PUT", "/api/gw/activity/settings", {"keep": False, "ver": "000000000000"})
        bad = self.err(400, "PUT", "/api/gw/activity/settings", {"keep": "нет", "ver": doc["ver"]})
        self.assertEqual(bad["data"]["field"], "keep")
        self.err(400, "PUT", "/api/gw/activity/settings?ver=" + doc["ver"], [False])         # not an object: 400
        self.assertTrue(model.activity_keep(state.load()))
        res = self.ok("PUT", "/api/gw/activity/settings", {"keep": False, "ver": doc["ver"]})
        self.assertNotEqual(res["ver"], doc["ver"])
        self.err(409, "PUT", "/api/gw/activity/settings", {"keep": True, "ver": doc["ver"]})    # the old ver
        res = self.ok("PUT", "/api/gw/activity/settings", {"keep": True, "ver": res["ver"]})
        self.assertIs(res["keep"], True)
        self.assertEqual(policy_ops.activity_get()["ver"], res["ver"])
