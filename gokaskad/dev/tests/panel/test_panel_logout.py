"""F19 (UI-1): «Выйти» ends the session on the server, not only on the page: the old cookie gets 401 everywhere, a
second tab of the same session loses access too — its open event stream ends at once, not after a heartbeat."""

import json
import threading
import time
from unittest import mock

from kaskad.panel import auth, sse
from tests.panel.panelcase import PanelCase
from tests.panel.test_panel_sse import SseClient, write_live


class LogoutTest(PanelCase):
    def setUp(self):
        super().setUp()
        auth.set_password("правильный-пароль", "admin")

    def login(self):
        r = self.call("POST", "/api/login", {"login": "admin", "password": "правильный-пароль"}, tunnel=False)
        self.assertEqual(r.status, 200, r)
        return self.cookie_of(r)

    def test_logout_old_cookie_401_on_every_route(self):
        tok = self.login()
        self.assertTrue(self.ok("GET", "/api/overview", tunnel=False, cookie=tok)["ok"])
        r = self.call("POST", "/api/logout", tunnel=False, cookie=tok)
        self.assertEqual(r.status, 200)
        self.assertIn("Max-Age=0", r.cookies[0])
        for path in ("/api/overview", "/api/panel", "/api/events"):
            self.err(401, "GET", path, tunnel=False, cookie=tok)
        self.assertFalse(self.ok("GET", "/api/session", tunnel=False, cookie=tok)["auth"])
        # the same cookie again: still nothing to end, still 401 after it
        self.assertEqual(self.call("POST", "/api/logout", tunnel=False, cookie=tok).status, 200)
        self.err(401, "GET", "/api/overview", tunnel=False, cookie=tok)

    def test_logout_of_a_link_session(self):
        r = self.call("GET", "/login?t=" + auth.issue_link("cli"), tunnel=False)
        tok = self.cookie_of(r)
        self.assertEqual(self.ok("GET", "/api/session", tunnel=False, cookie=tok)["via"], "link")
        self.assertEqual(self.call("POST", "/api/logout", tunnel=False, cookie=tok).status, 200)
        self.err(401, "GET", "/api/overview", tunnel=False, cookie=tok)

    def test_logout_in_one_tab_ends_the_stream_of_the_other(self):
        tok = self.login()
        other = SseClient(self.port, tunnel=False, cookie=tok)        # the second tab: the same cookie, live data open
        try:
            self.assertEqual(other.status, 200)
            self.assertEqual(other.next()[0], "live")
            started = time.time()
            self.assertEqual(self.call("POST", "/api/logout", tunnel=False, cookie=tok).status, 200)
            with self.assertRaises(EOFError):
                for _ in range(20):
                    other.next(timeout=3.0)
            self.assertLess(time.time() - started, sse.HEARTBEAT / 2)     # at once, not on the next heartbeat
        finally:
            other.close()
        self.err(401, "GET", "/api/events", tunnel=False, cookie=tok)      # its reconnect: 401 → the login screen

    def test_another_session_keeps_its_stream(self):
        tok, mine = self.login(), self.login()
        keep = SseClient(self.port, tunnel=False, cookie=mine)
        try:
            self.assertEqual(keep.next()[0], "live")
            self.assertEqual(self.call("POST", "/api/logout", tunnel=False, cookie=tok).status, 200)
            sse.HUB.kick()
            for _ in range(2):                  # its stream goes on: heartbeats, not the end (SEC-ui1-f19, finding 1)
                keep.next(timeout=3)
            self.assertTrue(auth.SESSIONS.alive(mine))
            self.assertTrue(self.ok("GET", "/api/overview", tunnel=False, cookie=mine)["ok"])
        finally:
            keep.close()

    def test_nothing_new_reaches_a_stream_after_its_session_is_gone(self):
        """New data and the end of the session come in the same wake: the stream ends, the data never leaves."""
        # the hub's poll lock re-entrant here: the test takes it before the condition (the hub's own order) and then
        # runs one poll itself — no poll of the hub thread in between, no lock taken in the other order
        p = mock.patch.object(sse.HUB, "_check_lock", threading.RLock())
        p.start()
        self.addCleanup(p.stop)
        self.gateway()
        self.collect()
        tok = self.login()
        other = SseClient(self.port, tunnel=False, cookie=tok)
        try:
            self.assertEqual(other.next()[0], "live")
            doc = json.loads(self.read("/run/kaskad/live.json"))
            doc["host"]["cpu"] = 77.7
            doc["ts"] = doc["ts"] + 2
            doc["seq"] += 1
            with sse.HUB._check_lock, sse.HUB.cond:    # the stream cannot wake until both are done
                write_live(doc)
                sse.HUB.check()                 # new data for every stream
                auth.SESSIONS.drop(tok)         # and «Выйти» (kick)
            got = []
            with self.assertRaises(EOFError):
                for _ in range(20):
                    got.append(other.next(timeout=3.0))
            self.assertFalse([g for g in got if g[0] != "comment"], got)
        finally:
            other.close()

    def test_alive_does_not_extend_the_session(self):
        tok = self.login()
        key = auth._hash(tok)
        seen = auth.SESSIONS._s[key]["seen"]
        auth.SESSIONS._s[key]["seen"] = seen - 100
        self.assertTrue(auth.SESSIONS.alive(tok))
        self.assertEqual(auth.SESSIONS._s[key]["seen"], seen - 100)
        auth.SESSIONS._s[key]["seen"] = seen - auth.IDLE - 1
        self.assertFalse(auth.SESSIONS.alive(tok))
        self.assertFalse(auth.SESSIONS.alive(""))

    def test_alive_knows_the_max_age(self):
        tok = self.login()
        key = auth._hash(tok)
        auth.SESSIONS._s[key]["created"] -= auth.MAX_AGE + 1
        self.assertFalse(auth.SESSIONS.alive(tok))
