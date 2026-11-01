"""Login to the panel (WP-P1, spec §3.1): password, floor, KDF slots, tries per address, sessions and cookies,
the SSH tunnel, one-time links."""

import json
import os
import stat
import threading
import time
from unittest import mock

from kaskad.panel import auth, conf
from tests.helpers import RootCase
from tests.panel.panelcase import PanelCase


class PasswordTest(RootCase):
    def setUp(self):
        super().setUp()
        self._p = mock.patch.object(auth, "SCRYPT_N", 1 << 10)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    def test_set_and_verify_hash_only_0600(self):
        self.assertFalse(auth.password_set())
        res = auth.set_password("очень-секретный-1", "boss")
        self.assertEqual(res["login"], "boss")
        p = auth.password_path()
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        text = self.read("/etc/kaskad/panel/password.json")
        self.assertNotIn("очень-секретный-1", text)
        rec = json.loads(text)
        self.assertEqual((rec["algo"], rec["n"], rec["r"], rec["p"]), ("scrypt", 1 << 10, 8, 1))
        self.assertTrue(auth.verify("boss", "очень-секретный-1"))
        self.assertFalse(auth.verify("boss", "очень-секретный-2"))
        self.assertFalse(auth.verify("admin", "очень-секретный-1"))
        auth.set_password("другой-пароль-2")                       # the login stays
        self.assertTrue(auth.verify("boss", "другой-пароль-2"))

    def test_bad_new_passwords_and_records(self):
        for bad in ("short", " с пробелом в начале", "x" * 201, "line\nbreak-1234"):
            with self.assertRaises(auth.KaskadError):
                auth.set_password(bad)
        with self.assertRaises(auth.KaskadError):
            auth.set_password("хороший-пароль", "плохой логин")
        auth.set_password("хороший-пароль")
        rec = json.loads(self.read("/etc/kaskad/panel/password.json"))
        rec["n"] = 1 << 22                                           # absurd parameters are not believed
        self.write("/etc/kaskad/panel/password.json", json.dumps(rec))
        self.assertEqual(auth.record(), {})
        self.assertFalse(auth.verify("admin", "хороший-пароль"))

    def test_kdf_runs_for_an_unknown_login_and_without_a_password(self):
        calls = []
        real = auth._derive

        def spy(pw, rec):
            calls.append(rec.get("login"))
            return real(pw, rec)
        with mock.patch.object(auth, "_derive", spy):
            self.assertFalse(auth.verify("admin", "что-нибудь-длинное"))     # no password at all
            auth.set_password("хороший-пароль", "boss")
            calls[:] = []
            self.assertFalse(auth.verify("nobody", "хороший-пароль"))
            self.assertTrue(auth.verify("boss", "хороший-пароль"))
        self.assertEqual(calls, ["", "boss"])                               # the dummy, then the real record

    def test_rate_limit_slot_before_the_check(self):
        rl = auth.RateLimit(limit=5, window=900)
        got = [rl.begin("1.2.3.4", now=1000 + i) for i in range(5)]
        self.assertEqual([g[0] for g in got], [True] * 5)
        self.assertEqual(got[-1][2], 0)
        allowed, retry, _ = rl.begin("1.2.3.4", now=1010)
        self.assertFalse(allowed)
        self.assertEqual(retry, 900 - 10)
        self.assertTrue(rl.begin("1.2.3.5", now=1010)[0])                    # another address
        self.assertTrue(rl.begin("1.2.3.4", now=1000 + 901)[0])             # the first try left the window
        rl.clear("1.2.3.4")
        self.assertEqual(rl.begin("1.2.3.4", now=1950)[2], 4)

    def test_buckets_and_tunnel_rules(self):
        self.assertEqual(auth.bucket("203.0.113.5"), "203.0.113.5")
        self.assertEqual(auth.bucket("2001:db8:1:2:3:4:5:6"), "2001:db8:1:2::/64")
        self.assertEqual(auth.bucket("::ffff:127.0.0.1"), "127.0.0.1")
        self.assertTrue(auth.tunnel({}, "127.0.0.1", True))
        self.assertTrue(auth.tunnel({}, "::1", True))
        self.assertFalse(auth.tunnel({}, "127.0.0.1", False))                 # the public listener
        self.assertFalse(auth.tunnel({"x-forwarded-for": ""}, "127.0.0.1", True))
        self.assertFalse(auth.tunnel({"x-forwarded-host": "a"}, "127.0.0.1", True))
        self.assertFalse(auth.tunnel({"forwarded": "for=1.2.3.4"}, "127.0.0.1", True))
        self.assertFalse(auth.tunnel({"x-real-ip": "1.2.3.4"}, "127.0.0.1", True))
        self.assertFalse(auth.tunnel({}, "10.0.0.5", True))
        self.assertFalse(auth.tunnel({}, "127.0.0.1", True, public=True))
        self.assertFalse(auth.tunnel({}, "127.0.0.1", True, require_password=True))

    def test_links_are_stored_hashed_single_use_and_expire(self):
        now = [1790000000.0]
        with mock.patch.object(auth, "_now", lambda: now[0]):
            t1 = auth.issue_link("cli")
            t2 = auth.issue_link("bot", ttl=120)
            text = self.read("/etc/kaskad/panel/links.json")
            self.assertNotIn(t1, text)
            self.assertNotIn(t2, text)
            self.assertEqual(stat.S_IMODE(os.stat(auth.links_path()).st_mode), 0o600)
            self.assertTrue(auth.redeem_link(t1))
            self.assertFalse(auth.redeem_link(t1))                          # burnt
            now[0] += 121
            self.assertFalse(auth.redeem_link(t2))                          # expired
            self.assertFalse(auth.redeem_link("not a token"))

    def test_sessions_idle_and_password_change(self):
        now = [1790000000.0]
        with mock.patch.object(auth, "_now", lambda: now[0]):
            ss = auth.Sessions()
            seen = []
            ss.on_change = seen.append
            tok = ss.create("tunnel")
            self.assertEqual(ss.get(tok)["via"], "tunnel")
            now[0] += auth.IDLE - 10
            self.assertIsNotNone(ss.get(tok))                               # a request restarts the idle clock
            now[0] += auth.IDLE - 10
            self.assertIsNotNone(ss.get(tok))
            now[0] += auth.IDLE + 1
            self.assertIsNone(ss.get(tok))
            self.assertEqual(seen[0], 1)
            auth.set_password("первый-пароль")
            tok = ss.create("password")
            self.assertIsNotNone(ss.get(tok))
            time.sleep(0.01)
            auth.set_password("второй-пароль")
            self.assertIsNone(ss.get(tok))                                  # the password changed
            self.assertIsNone(ss.get("x" * 43))
            self.assertEqual(ss.count(), 0)

    def test_cookie_header_parsing(self):
        tok = "A" * 43
        self.assertEqual(auth.cookie_token({"cookie": "a=b; %s=%s; c=d" % (auth.COOKIE, tok)}), tok)
        self.assertEqual(auth.cookie_token({"cookie": "%s=bad token" % auth.COOKIE}), "")
        self.assertEqual(auth.set_cookie(tok, False), "%s=%s; HttpOnly; SameSite=Strict; Path=/" % (auth.COOKIE, tok))
        self.assertTrue(auth.set_cookie(tok, True).endswith("; Secure"))


class LoginHttpTest(PanelCase):
    def setUp(self):
        super().setUp()
        auth.set_password("правильный-пароль", "admin")

    def test_good_login_cookie_and_the_session(self):
        r = self.call("POST", "/api/login", {"login": "admin", "password": "правильный-пароль"}, tunnel=False)
        self.assertEqual(r.status, 200, r)
        self.assertEqual(r.json(), {"ok": True, "via": "password"})
        (c,) = r.cookies
        self.assertRegex(c, r"^kaskad_session=[A-Za-z0-9_-]{40,}; HttpOnly; SameSite=Strict; Path=/$")
        tok = self.cookie_of(r)
        self.assertEqual(self.err(401, "GET", "/api/overview", tunnel=False)["data"], {"auth": "required"})
        self.assertTrue(self.ok("GET", "/api/overview", tunnel=False, cookie=tok)["ok"])
        doc = self.ok("GET", "/api/session", tunnel=False, cookie=tok)
        self.assertEqual((doc["auth"], doc["via"], doc["login"]), (True, "password", "admin"))
        self.assertIn("вход по паролю", self.read("/var/log/kaskad.log"))
        self.assertNotIn("правильный-пароль", self.read("/var/log/kaskad.log"))
        r = self.call("POST", "/api/logout", tunnel=False, cookie=tok)
        self.assertEqual(r.status, 200)
        self.assertIn("Max-Age=0", r.cookies[0])
        self.err(401, "GET", "/api/overview", tunnel=False, cookie=tok)

    def test_the_session_says_what_the_server_has_only_to_the_admin(self):
        """T-3377 Н-2: «has» of /api/session — what the panel may ask for of its optional parts; activity is true only
        with the route (3.3.7.10), so the panel does not ask a server without it (no 404 in the console). Before the
        login the public answer has no «has»."""
        from kaskad.panel import routes
        doc = self.ok("GET", "/api/session", tunnel=False)
        self.assertEqual(doc["auth"], False)
        self.assertNotIn("has", doc)
        r = self.call("POST", "/api/login", {"login": "admin", "password": "правильный-пароль"}, tunnel=False)
        tok = self.cookie_of(r)
        doc = self.ok("GET", "/api/session", tunnel=False, cookie=tok)
        route = routes.find("GET", "/api/gw/activity")[0]
        self.assertEqual(doc["has"], {"activity": isinstance(route, routes.Route)})
        self.assertIs(doc["has"]["activity"], True)                          # 3.3.7.9: the route is there
        with mock.patch.object(routes, "ROUTES", [r for r in routes.ROUTES if r.pattern != r"/api/gw/activity"]):
            self.assertIs(self.ok("GET", "/api/session", tunnel=False, cookie=tok)["has"]["activity"], False)

    def test_login_needs_the_csrf_header_too(self):
        self.err(403, "POST", "/api/login", {"password": "правильный-пароль"}, csrf=False, tunnel=False)

    def test_bad_password_then_the_address_is_blocked(self):
        for left in (4, 3, 2, 1, 0):
            doc = self.err(401, "POST", "/api/login", {"login": "admin", "password": "неправильный-пароль"},
                           tunnel=False)
            self.assertEqual(doc["data"], {"left": left, "auth": "failed"})
            self.assertEqual(doc["error"], "Неверный логин или пароль.")
        r = self.call("POST", "/api/login", {"login": "admin", "password": "правильный-пароль"}, tunnel=False)
        self.assertEqual(r.status, 429)                                   # even the right one: blocked
        self.assertTrue(900 - 5 <= int(r.headers["retry-after"]) <= 900)
        self.assertEqual(r.json()["data"]["auth"], "limit")
        self.assertIn("5 неверных паролей с адреса 127.0.0.1", self.read("/var/log/kaskad.log"))

    def test_good_login_clears_the_tries(self):
        for _ in range(4):
            self.err(401, "POST", "/api/login", {"password": "неправильный-пароль"}, tunnel=False)
        self.login("правильный-пароль")
        for _ in range(4):
            self.err(401, "POST", "/api/login", {"password": "неправильный-пароль"}, tunnel=False)

    def test_parallel_burst_gets_no_more_than_five_tries_and_two_kdf_at_once(self):
        active, peak, lock = [0], [0], threading.Lock()
        real = auth._derive

        def slow(pw, rec):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.15)
            try:
                return real(pw, rec)
            finally:
                with lock:
                    active[0] -= 1
        codes = []
        with mock.patch.object(auth, "_derive", slow):
            def one():
                r = self.call("POST", "/api/login", {"password": "неправильный-пароль"}, tunnel=False)
                with lock:
                    codes.append(r.status)
            ts = [threading.Thread(target=one) for _ in range(8)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(20)
        self.assertEqual(sorted(codes), [401] * 5 + [429] * 3)
        self.assertEqual(peak[0], 2)

    def test_floor_of_the_answer(self):
        with mock.patch.object(auth, "FLOOR", 0.35):
            t = time.monotonic()
            self.err(401, "POST", "/api/login", {"login": "nobody", "password": "что-нибудь-длинное"}, tunnel=False)
            self.assertGreaterEqual(time.monotonic() - t, 0.34)

    def test_password_change_ends_the_other_sessions(self):
        tok = self.login("правильный-пароль")
        other = self.login("правильный-пароль")
        r = self.call("PUT", "/api/panel/password", {"current": "правильный-пароль", "password": "новый-пароль-123"},
                      tunnel=False, cookie=tok)
        self.assertEqual(r.status, 200, r)
        mine = self.cookie_of(r)
        self.assertTrue(mine and mine != tok)
        self.err(401, "GET", "/api/overview", tunnel=False, cookie=other)
        self.err(401, "GET", "/api/overview", tunnel=False, cookie=tok)
        self.assertTrue(self.ok("GET", "/api/overview", tunnel=False, cookie=mine)["ok"])
        self.err(403, "PUT", "/api/panel/password", {"current": "не-тот-пароль", "password": "ещё-один-пароль"},
                 tunnel=False, cookie=mine)


class TunnelTest(PanelCase):
    def test_loopback_without_proxy_headers_is_admin(self):
        doc = self.ok("GET", "/api/session")
        self.assertEqual((doc["auth"], doc["via"], doc["password"]), (True, "tunnel", False))
        self.assertTrue(self.ok("GET", "/api/overview")["ok"])
        self.err(401, "GET", "/api/overview", tunnel=False)
        self.err(401, "GET", "/api/overview", headers={"X-Forwarded-Proto": "https"})

    def test_require_password_and_open_mode_take_the_tunnel_away(self):
        self.err(400, "PUT", "/api/panel/settings", {"require_password": True})     # no password yet: refused
        auth.set_password("правильный-пароль")
        self.ok("PUT", "/api/panel/settings", {"require_password": True})
        self.err(401, "GET", "/api/overview")
        tok = self.login("правильный-пароль", tunnel=True)
        self.assertTrue(self.ok("GET", "/api/overview", cookie=tok)["ok"])
        cfg = conf.load()
        cfg["require_password"] = False
        conf.save(cfg)
        self.panel.reload()
        self.assertTrue(self.ok("GET", "/api/overview")["ok"])
        self.public_on()
        self.err(401, "GET", "/api/overview")                              # open mode: the tunnel asks too


class LinkTest(PanelCase):
    def test_one_time_link_redirects_and_burns(self):
        token = auth.issue_link("cli")
        r = self.call("GET", "/login?t=" + token, tunnel=False)
        self.assertEqual((r.status, r.headers["location"]), (303, "/"))
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["referrer-policy"], "no-referrer")
        tok = self.cookie_of(r)
        self.assertTrue(tok)
        doc = self.ok("GET", "/api/session", tunnel=False, cookie=tok)
        self.assertEqual(doc["via"], "link")
        r = self.call("GET", "/login?t=" + token, tunnel=False)
        self.assertEqual((r.status, r.headers["location"], r.cookies), (303, "/?link=expired", []))
        self.assertIn("вход по одноразовой ссылке", self.read("/var/log/kaskad.log"))
        self.assertNotIn(token, self.read("/var/log/kaskad.log"))

    def test_link_from_the_panel_and_tries_of_bad_links(self):
        doc = self.ok("POST", "/api/panel/link", {})
        self.assertTrue(doc["url"].startswith("http://127.0.0.1:%d/login?t=" % self.port))
        r = self.call("GET", doc["url"].split(str(self.port), 1)[1], tunnel=False)
        self.assertEqual(r.headers["location"], "/")
        for _ in range(auth.LINK_ATTEMPTS):
            self.assertEqual(self.call("GET", "/login?t=" + "x" * 43, tunnel=False).headers["location"],
                             "/?link=expired")
        self.assertEqual(self.call("GET", "/login?t=" + auth.issue_link(), tunnel=False).headers["location"],
                         "/?link=limit")
        self.assertEqual(self.call("GET", "/login", tunnel=False).headers["location"], "/")

    def test_wrong_passwords_never_close_the_link_of_the_menu(self):
        """T-G1b N5: over the SSH tunnel everybody is 127.0.0.1 — five typos of the password closed the login for 15
        minutes, and the fresh link the menu (root) gave was refused too («?link=limit»). The links have a count of
        their own now; the password stays closed after five wrong ones."""
        auth.set_password("правильный-пароль", "admin")
        for _ in range(auth.ATTEMPTS):
            self.err(401, "POST", "/api/login", {"password": "неправильный-пароль"}, tunnel=False)
        self.assertEqual(self.call("POST", "/api/login", {"password": "правильный-пароль"}, tunnel=False).status, 429)
        r = self.call("GET", "/login?t=" + auth.login_link(by="cli")["token"], tunnel=False)
        self.assertEqual((r.status, r.headers["location"]), (303, "/"))
        self.assertEqual(self.ok("GET", "/api/session", tunnel=False, cookie=self.cookie_of(r))["via"], "link")
        self.assertEqual(self.call("POST", "/api/login", {"password": "правильный-пароль"}, tunnel=False).status, 429)
        # and the other way round: spent links do not close the password
        auth.LIMIT.clear(auth.bucket("127.0.0.1"))
        for _ in range(auth.LINK_ATTEMPTS):
            self.call("GET", "/login?t=" + "y" * 43, tunnel=False)
        self.assertEqual(self.call("GET", "/login?t=" + auth.issue_link(), tunnel=False).headers["location"],
                         "/?link=limit")
        self.login("правильный-пароль")

    def test_an_empty_current_password_is_asked_for_and_not_counted(self):
        auth.set_password("правильный-пароль", "admin")
        tok = self.login("правильный-пароль")
        for _ in range(auth.ATTEMPTS + 2):
            doc = self.err(400, "PUT", "/api/panel/password", {"current": "", "password": "новый-пароль-123"},
                           tunnel=False, cookie=tok)
            self.assertEqual(doc["error"], "Введите текущий пароль.")
        doc = self.err(403, "PUT", "/api/panel/password", {"current": "не-тот-пароль", "password": "новый-пароль-123"},
                       tunnel=False, cookie=tok)
        # the first try that counts: the empty ones before it did not take any
        self.assertEqual((doc["error"], doc["data"]["left"]), ("Текущий пароль не подошёл.", auth.ATTEMPTS - 1))
