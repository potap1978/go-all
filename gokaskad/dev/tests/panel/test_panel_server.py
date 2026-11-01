"""The HTTP server of the panel (WP-P1, spec §3.1): Host, CSRF, limits, security headers, errors, no access log,
static files, the user page's rate limit, the open mode over HTTPS."""

import gzip
import http.client
import io
import json
import os
import socket
import ssl
import tempfile
import time
from unittest import mock

from kaskad.gw.inbounds import pki
from kaskad.panel import auth, conf, routes, server
from tests.panel.panelcase import PanelCase, fixture

SECURITY = {"content-security-policy": server.CSP, "x-frame-options": "DENY", "referrer-policy": "no-referrer",
            "x-content-type-options": "nosniff"}


class Routes(object):
    """Test routes added to the shared table and taken away again."""

    def __init__(self, case):
        self.before = list(routes.ROUTES)
        case.addCleanup(self.restore)

    def restore(self):
        routes.ROUTES[:] = self.before


class GuardTest(PanelCase):
    def assertSecure(self, r):
        for k, v in SECURITY.items():
            self.assertEqual(r.headers.get(k), v, k)
        self.assertEqual(r.headers.get("server"), "kaskad-panel")

    def test_csp_is_the_spec_one(self):
        self.assertTrue(server.CSP.startswith("default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                              "script-src 'self'; connect-src 'self'"))

    def test_csrf_header_for_changes(self):
        doc = self.err(403, "POST", "/api/gw/users", {"name": "Мама"}, csrf=False)
        self.assertIn("X-Kaskad-Admin", doc["error"])
        self.err(403, "POST", "/api/gw/users", {"name": "Мама"}, headers={"X-Kaskad-Admin": "yes"})
        self.ok("POST", "/api/gw/users", {"name": "Мама"})
        self.ok("GET", "/api/gw/people", csrf=False)                       # reads need no header

    def test_cross_origin_changes_refused(self):
        self.err(403, "POST", "/api/gw/users", {"name": "А"}, headers={"Origin": "http://evil.example"})
        self.err(403, "POST", "/api/gw/users", {"name": "Б"}, headers={"Sec-Fetch-Site": "cross-site"})
        self.ok("POST", "/api/gw/users", {"name": "В"}, headers={"Origin": "http://127.0.0.1:%d" % self.port,
                                                                  "Sec-Fetch-Site": "same-origin"})

    def test_host_allow_list(self):
        r = self.call("GET", "/api/session", host="evil.example")
        self.assertEqual(r.status, 403)
        self.assertSecure(r)
        self.err(403, "GET", "/api/session", host="127.0.0.1.nip.io:%d" % self.port)
        for host in ("localhost:9999", "127.0.0.1", "[::1]:1980", "LOCALHOST:1"):
            self.assertEqual(self.call("GET", "/api/session", host=host).status, 200, host)
        out = self.raw(b"GET /api/session HTTP/1.0\r\n\r\n")             # an old tool without Host
        self.assertTrue(out.startswith(b"HTTP/1.0 200") or out.startswith(b"HTTP/1.1 200"), out[:80])

    def test_body_limits(self):
        out = self.raw(b"POST /api/gw/users HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Kaskad-Admin: 1\r\n"
                       b"Content-Length: %d\r\n\r\n" % (2 << 20))
        self.assertIn(b" 413 ", out.split(b"\r\n", 1)[0])
        self.assertIn(b"Connection: close", out)
        out = self.raw(b"POST /api/gw/users HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Kaskad-Admin: 1\r\n"
                       b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n")
        self.assertIn(b" 411 ", out.split(b"\r\n", 1)[0])
        self.err(400, "POST", "/api/gw/users", raw_body=b"{not json")
        self.err(400, "POST", "/api/gw/users", raw_body=b"[1, 2]")

    def test_a_copy_of_the_settings_may_be_bigger(self):
        """3.3.0: up to 20 MB — only for the two routes of a copy (backup/preview, backup/import), only with a session."""
        big = json.dumps({"file": "A" * (3 << 20), "password": "x" * 12}).encode()
        r = self.call("POST", "/api/backup/preview", raw_body=big)
        self.assertNotEqual(r.status, 413)                              # read and refused by the core, not by size
        out = self.raw(b"POST /api/gw/users HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Kaskad-Admin: 1\r\n"
                       b"Content-Length: %d\r\n\r\n" % (3 << 20))
        self.assertIn(b" 413 ", out.split(b"\r\n", 1)[0])
        out = self.raw(b"POST /api/backup/import HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Kaskad-Admin: 1\r\n"
                       b"Content-Length: %d\r\n\r\n" % (21 << 20))
        self.assertIn(b" 413 ", out.split(b"\r\n", 1)[0])
        self.assertIn("20 МБ".encode(), out)
        self.assertEqual(server.BACKUP_BODY_MAX, 20 << 20)
        self.assertEqual(server.BIG_BODY_PATHS, ("/api/backup/preview", "/api/backup/import"))
        # the limit is chosen only after the session is known: no session — the usual 1 MB
        with open(server.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertLess(src.index("session = auth.SESSIONS.get(token)"), src.index("body = self._read_body(headers, "))
        self.assertIn("path in BIG_BODY_PATHS and session is not None", src)

    def test_nginx_takes_a_copy_of_20_mb_on_its_two_paths(self):
        from kaskad.site import nginx
        t = nginx.render("a.example.org", "app-abcdefabcdef", version="1.24.0", ipv6=False)
        for p in ("preview", "import"):
            a = t.index("location = /app-abcdefabcdef/api/backup/%s {" % p)
            block = t[a:t.index("}", a)]
            self.assertIn("client_max_body_size 20m;", block)
            self.assertIn(":/api/backup/%s;" % p, block)
        a = t.index("location ^~ /app-abcdefabcdef/ {")
        self.assertIn("client_max_body_size 1m;", t[a:t.index("}", a)])

    def test_not_found_wrong_method_unknown_method(self):
        r = self.call("GET", "/api/nothing")
        self.assertEqual(r.status, 404)
        self.assertSecure(r)
        self.err(405, "DELETE", "/api/overview")
        r = self.call("PATCH", "/api/overview")      # 3.3.7.5 T-3375 Н-1: PATCH/OPTIONS/TRACE go through _dispatch —
        self.assertEqual(r.status, 405)              # a wrong method of a known route (no more 501 of http.server)
        self.assertSecure(r)
        self.assertEqual(r.json()["ok"], False)
        r = self.call("HEAD", "/")
        self.assertEqual((r.status, r.body), (200, b""))
        self.assertTrue(int(r.headers["content-length"]) > 0)

    def test_500_without_details_and_no_request_body_in_the_log(self):
        Routes(self)

        @routes.route("POST", r"/api/test/boom")
        def boom(req):
            raise RuntimeError("сломалось vpn://AAAABBBBCCCC")
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            r = self.call("POST", "/api/test/boom", {"password": "секрет-из-тела"})
        self.assertEqual(r.status, 500)
        self.assertEqual(r.json(), {"ok": False, "error": "Внутренняя ошибка панели."})
        self.assertSecure(r)
        log = buf.getvalue()
        self.assertIn("RuntimeError", log)
        self.assertIn("vpn://<скрыт>", log)
        self.assertNotIn("AAAABBBBCCCC", log)
        self.assertNotIn("секрет-из-тела", log)

    def test_core_errors_of_handlers_become_json(self):
        Routes(self)

        @routes.route("GET", r"/api/test/core")
        def core(req):
            raise auth.KaskadError("Порт занят.", code=3, data={"kind": "listener"})
        doc = self.err(409, "GET", "/api/test/core")
        self.assertEqual(doc, {"ok": False, "error": "Порт занят.", "code": 3, "data": {"kind": "listener"}})

    def test_no_access_log(self):
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            self.call("GET", "/")
            self.call("GET", "/api/overview")
            self.call("GET", "/api/nothing")
            self.call("GET", "/api/overview", tunnel=False)
            self.raw(b"garbage\r\n\r\n")
        self.assertEqual(buf.getvalue(), "")

    def test_keep_alive_and_connection_limit(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        for _ in range(3):
            c.request("GET", "/api/session", headers={"Host": "127.0.0.1"})
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            r.read()
        c.close()
        with mock.patch.object(server, "MAX_CONN", 2):
            idle = [socket.create_connection(("127.0.0.1", self.port), timeout=5) for _ in range(2)]
            time.sleep(0.2)
            s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            s.sendall(b"GET /api/session HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            try:
                got = s.recv(100)
            except ConnectionResetError:
                got = b""
            self.assertEqual(got, b"")                                      # closed at once: no thread for it
            s.close()
            for x in idle:
                x.close()
        self.assertEqual(self.wait(lambda: self.call("GET", "/api/session").status), 200)


class StaticTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(dir=self.root)
        os.makedirs(os.path.join(self.dir, "fonts"))
        with open(os.path.join(self.dir, "index.html"), "w", encoding="utf-8") as f:
            f.write('<!doctype html><script src="/static/app.js?v=__KASKAD_V__"></script>')
        with open(os.path.join(self.dir, "app.js"), "w", encoding="utf-8") as f:
            f.write("console.log('kaskad');\n" * 50)
        with open(os.path.join(self.dir, "fonts", "x.css"), "w", encoding="utf-8") as f:
            f.write("body{}\n")
        with open(os.path.join(self.dir, "fonts", "x.woff2"), "wb") as f:
            f.write(b"wOF2" + b"\0" * 60)
        with open(os.path.join(self.dir, ".hidden.js"), "w", encoding="utf-8") as f:
            f.write("no")
        p = mock.patch.object(server, "STATIC", server.Static(self.dir))
        p.start()
        self.addCleanup(p.stop)
        self.v = server.STATIC.version

    def test_index_gets_the_version_and_no_cache(self):
        self.assertRegex(self.v, r"^\d+(\.\d+){2,3}-[0-9a-f]{10}$")   # 3.3.5.1 — four numbers
        r = self.call("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn('/static/app.js?v=%s"' % self.v, r.text)
        self.assertEqual(r.headers["cache-control"], "no-cache")
        self.assertEqual(r.headers["content-type"], "text/html; charset=utf-8")
        r2 = self.call("GET", "/index.html", headers={"If-None-Match": r.headers["etag"]})
        self.assertEqual((r2.status, r2.body), (304, b""))

    def test_versioned_is_immutable_gzip_when_asked(self):
        r = self.call("GET", "/static/app.js?v=" + self.v)
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertNotIn("content-encoding", r.headers)
        plain = r.body
        r = self.call("GET", "/static/app.js?v=" + self.v, headers={"Accept-Encoding": "br, gzip"})
        self.assertEqual(r.headers["content-encoding"], "gzip")
        self.assertEqual(r.headers["vary"], "Accept-Encoding")
        self.assertEqual(gzip.decompress(r.body), plain)
        self.assertLess(len(r.body), len(plain))
        r = self.call("GET", "/static/app.js?v=" + self.v, headers={"Accept-Encoding": "gzip;q=0"})
        self.assertNotIn("content-encoding", r.headers)
        self.assertEqual(self.call("GET", "/static/app.js?v=old").headers["cache-control"], "no-cache")
        self.assertEqual(self.call("GET", "/static/app.js").headers["cache-control"], "no-cache")
        self.assertEqual(self.call("GET", "/app.js").body, plain)              # the root alias
        self.assertEqual(self.call("GET", "/static/fonts/x.css").headers["content-type"], "text/css; charset=utf-8")

    def test_fonts_next_to_the_styles(self):
        """tokens.css asks for url(fonts/x.woff2) next to itself: /fonts/x.woff2 (and under the secret prefix)."""
        r = self.call("GET", "/fonts/x.woff2?v=" + self.v)
        self.assertEqual((r.status, r.headers["content-type"]), (200, "font/woff2"))
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertNotIn("content-encoding", self.call("GET", "/fonts/x.woff2", headers={"Accept-Encoding": "gzip"}).headers)
        for path in ("/fonts/x.css", "/fonts/../app.js", "/fonts/nothing.woff2", "/fonts/.x.woff2", "/fonts/sub/x.woff2"):
            self.assertEqual(self.call("GET", path, tunnel=False).status, 404, path)

    def test_static_is_public_and_closed_to_tricks(self):
        self.assertEqual(self.call("GET", "/static/app.js", tunnel=False).status, 200)
        for path in ("/static/../server.py", "/static/.hidden.js", "/static/nothing.js", "/static/%2e%2e/x.js",
                     "/server.py", "/static/fonts/../app.js"):
            self.assertEqual(self.call("GET", path, tunnel=False).status, 404, path)

    def test_the_shipped_placeholder(self):
        st = server.Static()
        self.assertIn("index.html", st.entries)
        self.assertIn(st.version.encode(), st.entries["index.html"][0])


class PageLimitTest(PanelCase):
    def test_ten_misses_ban_the_address(self):
        Routes(self)

        # "/t/": the real user page (WP-U) owns every path under "/p/" with its own uniform 404
        @routes.route("GET", r"/t/(?P<t>[a-z]{4})", auth="page")
        def page(req):
            if req.match.group("t") != "good":
                raise routes.HttpError(404, "Не найдено.")
            return routes.json_response({"ok": True})
        self.assertEqual(self.call("GET", "/t/good", tunnel=False).status, 200)
        now = [time.time()]
        with mock.patch.object(auth, "_now", lambda: now[0]):
            for _ in range(auth.PAGE_MISSES):
                self.assertEqual(self.call("GET", "/t/miss", tunnel=False).status, 404)
            self.assertEqual(self.call("GET", "/t/good", tunnel=False).status, 404)       # banned
            now[0] += auth.PAGE_BAN + 1
            self.assertEqual(self.call("GET", "/t/good", tunnel=False).status, 200)


class PublicModeTest(PanelCase):
    def test_https_listener_secure_cookie_no_tunnel_host_check(self):
        port = self.public_on()
        doc = self.ok("GET", self.pb + "/api/session", port=port, https=True)
        self.assertEqual((doc["scheme"], doc["auth"], doc["public"]), ("https", False, True))
        self.err(401, "GET", self.pb + "/api/overview", port=port, https=True)       # 127.0.0.1, but the public listener
        self.assertEqual(self.call("GET", self.pb + "/api/session", port=port, https=True, host="evil.example").status, 403)
        auth.set_password("правильный-пароль")
        r = self.call("POST", self.pb + "/api/login", {"password": "правильный-пароль"}, port=port, https=True)
        self.assertEqual(r.status, 200, r)
        self.assertTrue(r.cookies[0].endswith("; Secure"))
        tok = self.cookie_of(r)
        self.assertTrue(self.ok("GET", self.pb + "/api/overview", port=port, https=True, cookie=tok)["ok"])
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            with ctx.wrap_socket(s) as tls:
                der = tls.getpeercert(binary_form=True)
                self.assertIn(tls.version(), ("TLSv1.2", "TLSv1.3"))
        want = pki.sha256_fingerprint(fixture("node.crt"))
        import hashlib
        got = hashlib.sha256(der).hexdigest().upper()
        self.assertEqual(":".join(got[i:i + 2] for i in range(0, len(got), 2)), want)
        status = conf.read_status()
        self.assertEqual(status["fingerprint"], want)
        self.assertEqual(status["url"], "https://127.0.0.1:%d%s/" % (port, self.pb))
        self.assertEqual(self.ok("GET", self.pb + "/api/panel", port=port, https=True, cookie=tok)["fingerprint"], want)

    def test_a_plain_client_on_the_tls_port_and_switching_off(self):
        port = self.public_on()
        try:
            out = self.raw(b"GET / HTTP/1.0\r\n\r\n", port=port)           # no TLS: the handshake fails, closed
        except ConnectionResetError:
            out = b""
        self.assertNotIn(b"HTTP/1", out)
        self.assertEqual(self.ok("GET", self.pb + "/api/session", port=port, https=True)["scheme"], "https")
        cfg = conf.load()
        cfg["public"]["on"] = False
        conf.save(cfg)
        self.panel.reload()
        self.assertNotIn("public", self.panel.servers)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=2).close()

    def test_without_a_certificate_the_open_mode_says_why(self):
        cfg = conf.load()
        cfg["public"] = {"on": True, "port": 45678, "bind": "127.0.0.1", "host": "127.0.0.1", "cert_host": ""}
        conf.save(cfg)
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            self.panel.reload()
        self.assertNotIn("public", self.panel.servers)
        self.assertEqual(self.panel.errors["public"], "127.0.0.1:45678 — нет сертификата узла (его делает "
                                                      "kaskadctl panel public on)")
        self.assertIn("открытый режим не поднялся", buf.getvalue())
        self.assertIn({"kind": "public", "error": self.panel.errors["public"]}, conf.read_status()["listen"])

    def test_access_json_for_the_user_page(self):
        self.assertEqual(json.loads(self.read("/etc/kaskad/panel/access.json")),
                         {"public": False, "port": self.port, "scheme": "http", "host": None})
        port = self.public_on()
        self.assertEqual(json.loads(self.read("/etc/kaskad/panel/access.json")),
                         {"public": True, "port": port, "scheme": "https", "host": "127.0.0.1"})

    def test_unknown_paths_answer_with_the_user_pages_404(self):
        from kaskad.panel import routes as r
        with mock.patch.object(server, "_userpage", [type("M", (), {"not_found": staticmethod(
                lambda: r.Response(404, b"<html>nothing</html>", {"Content-Type": "text/html; charset=utf-8"}))})]):
            for path in ("/nothing", "/api/nothing"):      # "/p/…" is answered by the real user page itself
                got = self.call("GET", path, tunnel=False)
                self.assertEqual((got.status, got.body), (404, b"<html>nothing</html>"), path)
                self.assertEqual(got.headers["x-frame-options"], "DENY")
        with mock.patch.object(server, "_userpage", [None]):           # no user page module: the JSON 404
            self.assertEqual(self.call("GET", "/nothing").json(), {"ok": False, "error": "Не найдено."})
