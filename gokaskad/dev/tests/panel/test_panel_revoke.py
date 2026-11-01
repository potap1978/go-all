"""F19b: a right taken away reaches the connections that are ALREADY open, not only the new ones.

    * open mode off — every connection its listener accepted ends: a kept-alive HTTPS connection gets no answer
      (no 200, no change), its event stream ends (SEC-ui1-f19, finding 4);
    * «always ask the password» on — the open event stream of the SSH tunnel ends (finding 5);
    * the first password (SSH menu, CLI, wizard) — a session opened by a link before it is gone: REST 401, its
      stream ends (finding 6);
    * the quick setup's stream looks at its session and link on every wake (finding 7);
    * a stream whose session is already gone sends not even its first snapshot (finding 3);
    * behind nginx: a new secret path (``kaskadctl panel proxy --new-path``) or another domain ends the stream under
      the old one, as its REST (SEC-f19b 4).
"""

import http.client
import json
import ssl
import threading
import time
from unittest import mock

from kaskad.panel import auth, conf, sse
from kaskad.setup import model, web
from tests.panel.panelcase import PanelCase
from tests.panel.test_panel_proxy import DOMAIN, PREFIX, ProxyCase, UnixHTTPConnection
from tests.panel.test_panel_sse import SseClient
from tests.setup.test_setup_web import SetupWebCase

PW = "правильный-пароль"


def tls_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class TlsSse(SseClient):
    """SseClient over the open mode's HTTPS listener."""

    def __init__(self, port, cookie):           # noqa: super().__init__ is the plain HTTP one
        self.c = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=tls_ctx())
        self.c.putrequest("GET", conf.proxy_base(conf.load()) + "/api/events", skip_host=True)
        self.c.putheader("Host", "127.0.0.1:%d" % port)
        self.c.putheader("Cookie", "kaskad_session=%s" % cookie)
        self.c.endheaders()
        self.sock = self.c.sock
        self.r = self.c.getresponse()
        self.status = self.r.status
        self.headers = dict((k.lower(), v) for k, v in self.r.getheaders())


def until_eof(cl, timeout):
    """Everything the stream sends until it ends: [kinds]; AssertionError when it has not ended in ``timeout`` s."""
    got, end = [], time.time() + timeout
    while time.time() < end:
        try:
            got.append(cl.next(timeout=max(0.05, end - time.time()))[0])
        except EOFError:
            return got
        except OSError:                         # the socket timed out: the stream is still open
            break
    raise AssertionError("the stream is still open: %r" % got)


class FirstWriteTest(PanelCase):
    def test_a_stream_whose_session_is_gone_sends_nothing(self):
        st = sse.open_stream(lambda: False)
        out = []
        th = threading.Thread(target=st.run, args=(out.append,), daemon=True)
        th.start()
        th.join(3)
        self.assertFalse(th.is_alive())
        self.assertEqual(out, [])                       # not even «retry» with the full snapshot
        self.assertEqual(sse.counts()["streams"], 0)    # its slot is given back

    def test_a_gate_ends_the_stream_too(self):
        flag = {"ok": True}
        st = sse.open_stream(None).gate(lambda: flag["ok"])
        out = []
        th = threading.Thread(target=st.run, args=(out.append,), daemon=True)
        th.start()
        self.wait(lambda: out)
        flag["ok"] = False
        sse.HUB.kick()
        th.join(3)
        self.assertFalse(th.is_alive())
        self.assertEqual(len(out), 1)                   # the first snapshot only, nothing after the gate closed


class OpenModeOffTest(PanelCase):
    def setUp(self):
        super().setUp()
        auth.set_password(PW, "admin")

    def _off(self):
        cfg = conf.load()
        cfg["public"]["on"] = False
        conf.save(cfg)
        self.panel.reload()
        self.assertNotIn("public", self.panel.servers)

    def test_kept_alive_connection_gets_no_answer_after_open_mode_off(self):
        """Each request goes on ITS OWN kept-alive connection, opened and used before the open mode went off
        (SEC-f19b 2: after a failed request http.client would open a new connection — refused by the closed
        listener anyway, which proves nothing). ``c.sock`` is checked to be that very socket."""
        port = self.public_on()
        h = {"Host": "127.0.0.1:%d" % port, "Content-Type": "application/json", "X-Kaskad-Admin": "1"}

        def kept(login):
            c = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=tls_ctx())
            if login:
                c.request("POST", self.pb + "/api/login", json.dumps({"login": "admin", "password": PW}), h)
                r = c.getresponse()
                r.read()
                self.assertEqual(r.status, 200)
                tok = [v for k, v in r.getheaders() if k.lower() == "set-cookie"][0].split(";")[0].split("=", 1)[1]
                h["Cookie"] = "kaskad_session=" + tok
            c.request("GET", self.pb + "/api/overview", headers=h)      # the same connection, still open mode: works
            r = c.getresponse()
            r.read()
            self.assertEqual(r.status, 200)
            self.assertIsNotNone(c.sock)
            return c, c.sock
        put, put_sock = kept(True)
        get, get_sock = kept(False)
        tok = h["Cookie"].split("=", 1)[1]
        intro0 = self.ok("GET", "/api/intro", tunnel=False, cookie=tok)
        self._off()
        for c, sock, method, path, body in ((put, put_sock, "PUT", "/api/intro", json.dumps({"done": True})),
                                            (get, get_sock, "GET", "/api/overview", None)):
            with self.assertRaises((OSError, http.client.HTTPException), msg=path):
                c.request(method, path, body, headers=h)
                self.assertIs(c.sock, sock, path)            # sent on the kept-alive socket, not a new one
                c.getresponse().read()
            c.close()
        self.assertEqual(self.ok("GET", "/api/intro", tunnel=False, cookie=tok), intro0)   # the change never came

    def test_stream_of_the_open_mode_ends_with_it(self):
        port = self.public_on()
        r = self.call("POST", self.pb + "/api/login", {"login": "admin", "password": PW}, port=port, https=True)
        self.assertEqual(r.status, 200, r)
        tok = self.cookie_of(r)
        cl = TlsSse(port, tok)
        try:
            self.assertEqual(cl.status, 200)
            self.assertEqual(cl.next()[0], "live")
            self._off()
            until_eof(cl, 2.0)
        finally:
            cl.close()
        # the session itself lives on: over the tunnel listener with its cookie (the owner's own way in) it still works
        self.assertTrue(auth.SESSIONS.alive(tok))

    def test_the_local_listener_keeps_its_streams(self):
        """Only the closed listener's connections end: a reload that keeps a listener keeps its streams (a cookie
        session: the tunnel itself is no admin while the open mode is on — its stream ends then, like its REST)."""
        r = self.call("POST", "/api/login", {"login": "admin", "password": PW}, tunnel=False)
        cl = SseClient(self.port, tunnel=False, cookie=self.cookie_of(r))
        try:
            self.assertEqual(cl.next()[0], "live")
            port = self.public_on()
            self.assertTrue(port)
            self._off()
            goes_on(cl)                                            # heartbeats, not the end
        finally:
            cl.close()


def goes_on(cl, n=2, timeout=3):
    """The stream sends ``n`` more things (heartbeats, state…) and does not end."""
    for _ in range(n):
        cl.next(timeout=timeout)


class TunnelRightTest(PanelCase):
    def setUp(self):
        super().setUp()
        auth.set_password(PW, "admin")             # «always ask the password» needs one

    def test_require_password_ends_the_tunnel_stream(self):
        cl = SseClient(self.port, tunnel=True)
        try:
            self.assertEqual(cl.next()[0], "live")
            self.ok("PUT", "/api/panel/settings", {"require_password": True})
            self.err(401, "GET", "/api/overview")
            got = until_eof(cl, 2.0)
            self.assertNotIn("live", got)
        finally:
            cl.close()

    def test_open_mode_on_ends_the_tunnel_stream(self):
        """SEC-f19b 1 (N5): the open mode on takes the tunnel's right too — on REST and on its open stream."""
        cl = SseClient(self.port, tunnel=True)
        try:
            self.assertEqual(cl.next()[0], "live")
            self.public_on()
            self.err(401, "GET", "/api/overview")
            self.assertNotIn("live", until_eof(cl, 2.0))
        finally:
            cl.close()

    def test_the_tunnel_stream_goes_on_while_it_is_admin(self):
        cl = SseClient(self.port, tunnel=True)
        try:
            self.assertEqual(cl.next()[0], "live")
            self.ok("PUT", "/api/panel/settings", {"require_password": False})   # a reload, the right unchanged
            goes_on(cl)
        finally:
            cl.close()


class FirstPasswordTest(PanelCase):
    def setUp(self):
        super().setUp()
        # no heartbeat in the time of a test: the stream must end because the hub saw password.json change, not on
        # its next wake by itself
        p = mock.patch.object(sse, "HEARTBEAT", 30.0)
        p.start()
        self.addCleanup(p.stop)

    def _link_session(self):
        self.assertFalse(auth.password_set())
        r = self.call("GET", "/login?t=" + auth.issue_link("cli"), tunnel=False)
        tok = self.cookie_of(r)
        self.assertTrue(self.ok("GET", "/api/overview", tunnel=False, cookie=tok)["ok"])
        return tok

    def test_first_password_from_the_cli_ends_a_link_session_opened_before_it(self):
        tok = self._link_session()
        cl = SseClient(self.port, tunnel=False, cookie=tok)
        try:
            self.assertEqual(cl.next()[0], "live")
            auth.set_password("первый-пароль-1", "admin")         # as the SSH menu / kaskadctl do: no panel call
            until_eof(cl, 3.0)                                    # the hub sees password.json change
        finally:
            cl.close()
        self.assertFalse(auth.SESSIONS.alive(tok))
        self.err(401, "GET", "/api/overview", tunnel=False, cookie=tok)

    def test_a_password_change_outside_the_panel_ends_the_stream(self):
        auth.set_password(PW, "admin")
        r = self.call("POST", "/api/login", {"login": "admin", "password": PW}, tunnel=False)
        tok = self.cookie_of(r)
        cl = SseClient(self.port, tunnel=False, cookie=tok)
        try:
            self.assertEqual(cl.next()[0], "live")
            auth.set_password("новый-пароль-123", "admin")
            until_eof(cl, 3.0)
        finally:
            cl.close()
        self.err(401, "GET", "/api/overview", tunnel=False, cookie=tok)

    def test_a_link_session_with_no_password_lives_on(self):
        tok = self._link_session()
        self.assertTrue(auth.SESSIONS.alive(tok))
        self.assertEqual(auth.SESSIONS._s[auth._hash(tok)]["pw"], auth.NO_PASSWORD)
        self.assertTrue(self.ok("GET", "/api/overview", tunnel=False, cookie=tok)["ok"])


class SetupStreamTest(SetupWebCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(web, "HEARTBEAT", 0.6)
        p.start()
        self.addCleanup(p.stop)

    def _stream(self, cookie):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("GET", self.base + "/api/setup/events", skip_host=True)
        c.putheader("Host", "127.0.0.1:%d" % self.port)
        c.putheader("X-Forwarded-For", "198.51.100.9")
        c.putheader("Cookie", "%s=%s" % (web.COOKIE, cookie))
        c.endheaders()
        cl = SseClient.__new__(SseClient)
        cl.c, cl.sock = c, c.sock
        cl.r = c.getresponse()
        cl.status = cl.r.status
        self.addCleanup(cl.close)
        return cl

    def _open(self):
        tok, _raw, _r = self.enter()
        cl = self._stream(tok)
        self.assertEqual(cl.status, 200)
        self.assertEqual(cl.next(timeout=3)[0], "job")
        return tok, cl

    def test_logout_ends_the_wizard_stream(self):
        tok, cl = self._open()
        r = self.scall("POST", "/api/setup/logout", cookie=tok)
        self.assertEqual(r.status, 200, r)
        self.assertNotIn("job", until_eof(cl, 2.0))

    def test_the_link_time_ends_the_wizard_stream(self):
        _tok, cl = self._open()
        model.update(lambda d: d["link"].__setitem__("expires", time.time() - 1))
        until_eof(cl, 2.0)

    def test_the_wizard_closed_ends_its_stream(self):
        """SEC-f19b 1 (N4): the wizard closed (or its secret path changed) — its open stream ends, the session alive."""
        tok, cl = self._open()
        model.update(lambda d: d.__setitem__("status", "done"))
        self.assertFalse(model.expired(model.load()))          # not the link time: the secret path alone
        until_eof(cl, 2.0)

    def test_a_link_bound_to_another_address_ends_the_stream(self):
        """SEC-f19b 1 (N3): the link bound to another address — the stream of this one ends."""
        _tok, cl = self._open()

        def bind(d):
            d["link"]["bind"] = True
            d["link"]["bound_ip"] = "203.0.113.7"
        model.update(bind)
        until_eof(cl, 2.0)

    def test_the_wizard_stream_goes_on_while_its_session_lives(self):
        _tok, cl = self._open()
        self.assertEqual(cl.next(timeout=3)[0], "comment")
        self.assertEqual(cl.next(timeout=3)[0], "comment")


class ProxyPathTest(ProxyCase):
    """SEC-f19b 4: what a new request behind nginx is refused by (404 outside the secret path, 403 for a foreign
    name) ends the stream that was let in before it."""

    def setUp(self):
        super().setUp()
        self.proxy_on(path=PREFIX, domain=DOMAIN)
        auth.set_password(PW, "admin")
        self.tok = self.cookie_of(self.pcall("POST", "/%s/api/login" % PREFIX, {"login": "admin", "password": PW}))

    def _stream(self, base="/" + PREFIX):
        c = UnixHTTPConnection(self.sock_path(), timeout=5)
        c.putrequest("GET", "%s/api/events" % base, skip_host=True)
        for k, v in (("Host", DOMAIN), ("X-Real-IP", "198.51.100.7"), ("X-Forwarded-Proto", "https"),
                     ("X-Forwarded-Host", DOMAIN), ("Cookie", "%s=%s" % (auth.COOKIE, self.tok))):
            c.putheader(k, v)
        c.endheaders()
        cl = SseClient.__new__(SseClient)
        cl.c, cl.sock = c, c.sock
        cl.r = c.getresponse()
        cl.status = cl.r.status
        self.addCleanup(cl.close)
        self.assertEqual(cl.status, 200)
        self.assertEqual(cl.next()[0], "live")
        return cl

    def test_a_new_secret_path_ends_the_stream_under_the_old_one(self):
        cl = self._stream()
        conf.set_proxy(True, new_path=True)                  # what ``kaskadctl panel proxy --new-path`` does
        self.panel.reload()
        self.assertNotEqual(conf.load()["proxy"]["path"], PREFIX)
        self.assertEqual(self.pcall("GET", "/%s/api/overview" % PREFIX, cookie=self.tok).status, 404)
        self.assertNotIn("live", until_eof(cl, 2.0))

    def test_another_domain_ends_the_stream_of_the_old_name(self):
        cl = self._stream()
        conf.set_proxy(True, domain="panel.example.net")
        self.panel.reload()
        self.assertEqual(self.pcall("GET", "/%s/api/overview" % PREFIX, cookie=self.tok, host=DOMAIN).status, 403)
        until_eof(cl, 2.0)

    def test_the_same_path_keeps_the_stream(self):
        cl = self._stream()
        conf.set_proxy(True)                                 # a reload, the path and the name unchanged
        self.panel.reload()
        self.assertEqual(conf.load()["proxy"]["path"], PREFIX)
        goes_on(cl)

    def _path(self, path):
        cfg = conf.load()
        cfg["proxy"]["path"] = path
        conf.save(cfg)
        self.panel.reload()

    def test_the_secret_path_taken_away_ends_the_stream_under_it(self):
        """No prefix any more: the old path is no panel's (404 — no route), not «outside» — the stream ends too."""
        cl = self._stream()
        self._path("")
        self.assertEqual(self.pcall("GET", "/%s/api/overview" % PREFIX, cookie=self.tok).status, 404)
        until_eof(cl, 2.0)

    def test_a_secret_path_given_ends_the_stream_at_the_root(self):
        """A prefix where there was none: the root is outside it now (404) — the stream at the root ends."""
        self._path("")
        cl = self._stream(base="")
        self._path(PREFIX)
        self.assertEqual(self.pcall("GET", "/api/overview", cookie=self.tok).status, 404)
        until_eof(cl, 2.0)
