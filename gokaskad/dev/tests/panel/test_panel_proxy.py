"""The panel behind nginx (docs/k3/SPEC-wave4.md §3, stage 4.0-A): the listener "proxy" on a unix socket — the real
server on the fake node, requests as nginx would send them (its proxy_set_header), and as a stranger would.

    D3  no tunnel's admin on the proxy listener — with nginx's headers and without any; 1980 stays the tunnel;
    D1/D2  the limits of passwords, links and /p/ misses — per the real address (X-Real-IP; IPv6 per /64);
    D5  Host (X-Forwarded-Host) and Origin — the domain of the settings, https://<domain>;
    D4  the session cookie — Secure always, Path the prefix of the panel;
    D8  the panel and the page of a person under any prefix: the login by a link comes back under it, SSE, the
        shell's own files; outside the prefix nothing of the panel answers;
    D9  the 404 names no server."""

import contextlib
import http.client
import io
import json
import os
import shutil
import socket
import stat
import tempfile
import time
from unittest import mock

from kaskad import cli, errors, util
from kaskad.panel import auth, conf, server, userpage
from tests.panel.panelcase import PanelCase, Resp

DOMAIN = "vpn.example.com"
PREFIX = "app-" + "A1b2C3d4" * 10 + "xyz-_q"           # 86 characters after «app-», like the real one


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=10):
        http.client.HTTPConnection.__init__(self, "localhost", timeout=timeout)
        self.unix_path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.unix_path)
        self.sock = s


class ProxyCase(PanelCase):
    """``self.pcall`` — a request to the proxy listener; ``nginx=True`` adds what nginx of stage 4.0-C sends."""

    def setUp(self):
        super().setUp()
        self.short = None
        if len(conf.proxy_sock_path()) > 100:              # AF_UNIX: at most 104 bytes on macOS
            self.short = tempfile.mkdtemp(prefix="kp", dir="/tmp")
            self.panel.proxy_sock = os.path.join(self.short, "proxy.sock")

    def tearDown(self):
        super().tearDown()
        userpage.GUARDS.reset()
        if self.short:
            shutil.rmtree(self.short, ignore_errors=True)

    def sock_path(self):
        return self.panel.proxy_sock or conf.proxy_sock_path()

    def proxy_on(self, path="", domain=""):
        cfg = conf.load()
        cfg["proxy"] = {"on": True, "path": path, "domain": domain}
        conf.save(cfg)
        self.panel.reload()
        self.assertIn("proxy", self.panel.servers, self.panel.errors)
        return self.sock_path()

    def pcall(self, method, path, body=None, headers=None, nginx=True, ip="198.51.100.7", host=None,
              proto="https", cookie=None, csrf=True, origin=None):
        name = host if host is not None else (conf.load()["proxy"]["domain"] or "localhost")
        h = {"Host": name}
        if nginx:
            h.update({"X-Real-IP": ip, "X-Forwarded-For": ip, "X-Forwarded-Proto": proto,
                      "X-Forwarded-Host": name})
        if method in ("POST", "PUT", "DELETE") and csrf:
            h["X-Kaskad-Admin"] = "1"
        if origin:
            h["Origin"] = origin
        if cookie:
            h["Cookie"] = "%s=%s" % (auth.COOKIE, cookie)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c = UnixHTTPConnection(self.sock_path())
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                if v is not None:
                    c.putheader(k, v)
            if data is not None:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            return Resp(r.status, r.getheaders(), r.read())
        finally:
            c.close()


class ListenerTest(ProxyCase):
    def test_off_by_default_and_the_old_listeners_as_they_were(self):
        self.assertEqual(conf.load()["proxy"], {"on": False, "path": "", "domain": ""})
        self.assertNotIn("proxy", self.panel.servers)
        self.assertFalse(os.path.exists(self.sock_path()))
        self.assertEqual(self.ok("GET", "/api/session")["via"], "tunnel")
        self.assertTrue(self.ok("GET", "/api/overview")["ok"])

    def test_the_socket_0660_its_directory_0710_and_off_again(self):
        path = self.proxy_on()
        st = os.stat(path)
        self.assertTrue(stat.S_ISSOCK(st.st_mode))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o660)
        self.panel.write_status()                                  # the status file keeps the directory passable
        if not self.short:
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), conf.RUN_DIR_MODE)
        self.assertIn({"kind": "proxy", "addr": "unix:%s" % path, "scheme": "http"}, conf.read_status()["listen"])
        cfg = conf.load()
        cfg["proxy"]["on"] = False
        conf.save(cfg)
        self.panel.reload()
        self.assertNotIn("proxy", self.panel.servers)
        self.assertFalse(os.path.exists(path))

    def test_a_stale_socket_is_replaced_another_file_is_not(self):
        path = self.sock_path()
        util.ensure_dir(os.path.dirname(path), conf.RUN_DIR_MODE)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(path)
        s.close()                                                  # the socket file of a previous run
        self.proxy_on()
        self.assertEqual(self.pcall("GET", "/api/health").status, 200)
        cfg = conf.load()
        cfg["proxy"]["on"] = False
        conf.save(cfg)
        self.panel.reload()
        with open(path, "w") as f:
            f.write("not a socket")
        cfg["proxy"]["on"] = True
        conf.save(cfg)
        said = []
        with mock.patch.object(server, "log", said.append):
            self.panel.reload()
        self.assertNotIn("proxy", self.panel.servers)
        self.assertIn("на месте сокета другой файл", self.panel.errors["proxy"])
        self.assertIn("слушатель для nginx не поднялся", said[0])
        with open(path) as f:
            self.assertEqual(f.read(), "not a socket")


class NoTunnelTest(ProxyCase):
    """D3: nginx connects from the node itself — on 1980 that was the tunnel's admin without a password."""

    def test_no_admin_without_a_password_with_and_without_headers(self):
        self.proxy_on()
        for nginx in (True, False):
            r = self.pcall("GET", "/api/overview", nginx=nginx)
            self.assertEqual(r.status, 401, (nginx, r))
            self.assertEqual(r.json()["data"], {"auth": "required"})
            doc = self.pcall("GET", "/api/session", nginx=nginx).json()
            self.assertEqual((doc["auth"], doc["via"]), (False, None))
            self.assertEqual(self.pcall("PUT", "/api/panel/password", {"password": "новый-пароль-1"},
                                        nginx=nginx).status, 401)
        # the tunnel over 1980 — as before
        self.assertEqual(self.ok("GET", "/api/session")["via"], "tunnel")
        self.assertTrue(self.ok("GET", "/api/overview")["ok"])
        self.assertEqual(self.call("GET", "/api/overview", tunnel=False).status, 401)

    def test_proxy_view_believes_nginx_only_here(self):
        # on 1980 X-Real-IP is no address: the limit stays the peer's, and it is still «not the tunnel»
        auth.set_password("правильный-пароль")
        for _ in range(auth.ATTEMPTS):
            self.call("POST", "/api/login", {"password": "неверный-пароль"}, headers={"X-Real-IP": "203.0.113.9"})
        r = self.call("POST", "/api/login", {"password": "неверный-пароль"}, headers={"X-Real-IP": "203.0.113.10"})
        self.assertEqual(r.status, 429, r)

    def test_the_view(self):
        cfg = {"proxy": {"on": True, "path": "app-secret", "domain": DOMAIN}}
        v = server.proxy_view(cfg, "/app-secret/api/x", {"x-real-ip": "2001:db8::1", "x-forwarded-host": DOMAIN})
        self.assertEqual((v.base, v.path, v.client_ip, v.scheme, v.host, v.outside, v.redirect),
                         ("/app-secret", "/api/x", "2001:db8::1", "https", DOMAIN, False, False))
        v = server.proxy_view(cfg, "/app-secret", {})
        self.assertEqual((v.base, v.path, v.redirect), ("/app-secret", "/", True))
        v = server.proxy_view(cfg, "/api/x", {"x-forwarded-prefix": "/app-secret/"})     # nginx cut it off
        self.assertEqual((v.base, v.path, v.outside), ("/app-secret", "/api/x", False))
        v = server.proxy_view(cfg, "/api/x", {"x-forwarded-prefix": "/app-other"})       # not that prefix
        self.assertEqual((v.base, v.outside), ("", True))
        v = server.proxy_view(cfg, "/p/abc", {"x-forwarded-for": "10.0.0.1, 198.51.100.4", "host": "h"})
        self.assertEqual((v.base, v.outside, v.client_ip, v.host), ("", False, "198.51.100.4", "h"))
        v = server.proxy_view(cfg, "/app-secretX/api", {"x-real-ip": "not an ip", "x-forwarded-proto": "gopher",
                                                         "x-forwarded-host": "a b"})
        self.assertEqual((v.base, v.outside, v.client_ip, v.scheme, v.host), ("", True, "", "https", ""))
        cfg = {"proxy": {"on": True, "path": "", "domain": ""}}
        v = server.proxy_view(cfg, "/api/x", {"x-forwarded-prefix": "/any/prefix"})
        self.assertEqual((v.base, v.path, v.outside), ("/any/prefix", "/api/x", False))
        v = server.proxy_view(cfg, "/api/x", {"x-forwarded-prefix": "/../x"})
        self.assertEqual((v.base, v.outside), ("", False))


class LimitsTest(ProxyCase):
    """D1/D2: one scanner never closes the way in for everybody."""

    def test_passwords_per_real_address(self):
        self.proxy_on()
        auth.set_password("правильный-пароль")
        for i in range(auth.ATTEMPTS):
            r = self.pcall("POST", "/api/login", {"password": "неверный-пароль"}, ip="198.51.100.1")
            self.assertEqual(r.status, 401, (i, r))
        self.assertEqual(self.pcall("POST", "/api/login", {"password": "правильный-пароль"},
                                    ip="198.51.100.1").status, 429)
        r = self.pcall("POST", "/api/login", {"password": "правильный-пароль"}, ip="198.51.100.2")
        self.assertEqual(r.status, 200, r)
        # IPv6: one /64 is one address
        for i in range(auth.ATTEMPTS):
            self.pcall("POST", "/api/login", {"password": "неверный-пароль"}, ip="2001:db8:1:2::%x" % (i + 1))
        self.assertEqual(self.pcall("POST", "/api/login", {"password": "правильный-пароль"},
                                    ip="2001:db8:1:2::ffff").status, 429)
        self.assertEqual(self.pcall("POST", "/api/login", {"password": "правильный-пароль"},
                                    ip="2001:db8:1:3::1").status, 200)
        # the tunnel's 127.0.0.1 is a bucket of its own
        self.assertEqual(self.call("POST", "/api/login", {"password": "правильный-пароль"}, tunnel=False).status, 200)

    def test_links_per_real_address(self):
        self.proxy_on()
        for _ in range(auth.LINK_ATTEMPTS):
            self.pcall("GET", "/login?t=" + "x" * 43, ip="198.51.100.1")
        r = self.pcall("GET", "/login?t=" + auth.issue_link(), ip="198.51.100.1")
        self.assertEqual(r.headers["location"], "/?link=limit")
        r = self.pcall("GET", "/login?t=" + auth.issue_link(), ip="198.51.100.2")
        self.assertEqual(r.headers["location"], "/")
        self.assertTrue(self.cookie_of(r))

    def test_page_misses_per_real_address(self):
        self.proxy_on()
        now = [time.time()]
        with mock.patch.object(auth, "_now", lambda: now[0]):
            for i in range(auth.PAGE_MISSES):
                self.pcall("GET", "/p/%s" % ("Z%042d" % i), ip="198.51.100.66")
        self.assertTrue(auth.PAGES.banned("198.51.100.66", now=now[0]))
        self.assertTrue(userpage.GUARDS.banned("198.51.100.66"))
        self.assertFalse(auth.PAGES.banned("198.51.100.67", now=now[0]))
        self.assertFalse(userpage.GUARDS.banned("198.51.100.67"))
        self.assertFalse(auth.PAGES.banned("?", now=now[0]))
        self.assertFalse(userpage.GUARDS.banned("127.0.0.1"))

    def test_the_device_of_the_scheme_is_the_real_address(self):
        """scheme._client_device (the guard «do not cut off your own access») gets the address nginx saw."""
        self.proxy_on()
        seen = []
        from kaskad.panel import scheme
        auth.set_password("правильный-пароль")
        tok = self.cookie_of(self.pcall("POST", "/api/login", {"password": "правильный-пароль"}, ip="10.66.0.5"))
        with mock.patch.object(scheme, "apply", lambda body, client_ip=None, runner=None: seen.append(client_ip)
                               or {"ok": True}):
            r = self.pcall("POST", "/api/scheme/apply", {"intents": [], "dry_run": True}, ip="10.66.0.5", cookie=tok)
        self.assertEqual((r.status, seen), (200, ["10.66.0.5"]))


class HostOriginTest(ProxyCase):
    """D5: the domain of the settings in Host/X-Forwarded-Host, https://<domain> in Origin."""

    def test_host_and_origin(self):
        self.proxy_on(domain=DOMAIN)
        auth.set_password("правильный-пароль")
        self.assertEqual(self.pcall("GET", "/api/session").status, 200)
        self.assertEqual(self.pcall("GET", "/api/session", host="evil.example").status, 403)
        # nginx without X-Forwarded-Host: its Host «localhost» is not the domain
        self.assertEqual(self.pcall("GET", "/api/session", host="localhost").status, 403)
        r = self.pcall("GET", "/api/session", headers={"X-Forwarded-Host": None, "Host": DOMAIN})
        self.assertEqual(r.status, 200)
        good = "https://" + DOMAIN
        for origin, want in ((good, 200), ("https://evil.example", 403), ("http://" + DOMAIN, 403),
                             (good + ":8443", 403)):
            r = self.pcall("POST", "/api/login", {"password": "правильный-пароль"}, origin=origin)
            self.assertEqual(r.status, want, (origin, r))
        r = self.pcall("POST", "/api/login", {"password": "правильный-пароль"},
                       headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status, 403)

    def test_the_domain_of_state_and_without_any_only_loopback(self):
        self.proxy_on()
        self.assertEqual(self.pcall("GET", "/api/session", host="localhost").status, 200)
        self.assertEqual(self.pcall("GET", "/api/session", host=DOMAIN).status, 403)
        from kaskad import state
        with state.locked():
            st = state.load()
            st.setdefault("gateway", {})["site"] = {"domain": "Site.Example.org."}
            state.save(st)
        self.panel.reload()
        self.assertEqual(self.pcall("GET", "/api/session", host="site.example.org").status, 200)
        self.assertEqual(self.pcall("GET", "/api/session", host="localhost").status, 403)


class CookiePrefixTest(ProxyCase):
    """D4/D8: under the secret prefix the cookie, the redirects, SSE and the pages follow it."""

    def test_cookie_secure_and_path(self):
        self.proxy_on(path=PREFIX, domain=DOMAIN)
        auth.set_password("правильный-пароль")
        for proto in ("https", "http"):
            r = self.pcall("POST", "/%s/api/login" % PREFIX, {"password": "правильный-пароль"}, proto=proto)
            self.assertEqual(r.status, 200, r)
            parts = [p.strip() for p in r.cookies[0].split(";")]
            self.assertIn("Secure", parts)
            self.assertIn("Path=/%s/" % PREFIX, parts)
            self.assertIn("HttpOnly", parts)
            self.assertIn("SameSite=Strict", parts)
            self.assertEqual(sum(1 for p in parts if p.startswith("Path=")), 1)
        tok = self.cookie_of(r)
        self.assertTrue(self.pcall("GET", "/%s/api/overview" % PREFIX, cookie=tok).json()["ok"])
        r = self.pcall("POST", "/%s/api/logout" % PREFIX, {}, cookie=tok)
        parts = [p.strip() for p in r.cookies[0].split(";")]
        self.assertIn("Secure", parts)
        self.assertIn("Path=/%s/" % PREFIX, parts)
        self.assertIn("Max-Age=0", parts)
        # the tunnel's cookie as before: Path=/, no Secure over http
        r = self.call("POST", "/api/login", {"password": "правильный-пароль"}, tunnel=False)
        self.assertTrue(r.cookies[0].endswith("; Path=/"), r.cookies)

    def test_a_link_brings_back_under_the_prefix(self):
        self.proxy_on(path=PREFIX, domain=DOMAIN)
        r = self.pcall("GET", "/%s/login?t=%s" % (PREFIX, auth.issue_link()))
        self.assertEqual((r.status, r.headers["location"]), (303, "/%s/" % PREFIX))
        tok = self.cookie_of(r)
        self.assertIn("Path=/%s/" % PREFIX, r.cookies[0])
        self.assertEqual(self.pcall("GET", "/%s/api/session" % PREFIX, cookie=tok).json()["via"], "link")
        r = self.pcall("GET", "/%s/login?t=%s" % (PREFIX, "x" * 43))
        self.assertEqual(r.headers["location"], "/%s/?link=expired" % PREFIX)
        # nginx that cuts the prefix off and says it in X-Forwarded-Prefix
        r = self.pcall("GET", "/login?t=%s" % auth.issue_link(), headers={"X-Forwarded-Prefix": "/" + PREFIX})
        self.assertEqual(r.headers["location"], "/%s/" % PREFIX)
        # the link of the CLI and the bot, the link the panel gives
        self.assertEqual(conf.login_url("TOKEN"), "https://%s/%s/login?t=TOKEN" % (DOMAIN, PREFIX))
        doc = self.pcall("POST", "/%s/api/panel/link" % PREFIX, {}, cookie=tok).json()
        self.assertTrue(doc["url"].startswith("https://%s/%s/login?t=" % (DOMAIN, PREFIX)), doc)

    def test_outside_the_prefix_nothing_of_the_panel(self):
        self.proxy_on(path=PREFIX, domain=DOMAIN)
        for path in ("/", "/index.html", "/app.js", "/api/session", "/api/overview", "/login?t=x",
                     "/app-wrong/api/session", "/%sX/" % PREFIX):
            r = self.pcall("GET", path)
            self.assertEqual((r.status, r.body), (404, userpage.NOT_FOUND_BODY), path)
        r = self.pcall("GET", "/" + PREFIX + "?x=1")
        self.assertEqual((r.status, r.headers["location"]), (301, "/%s/?x=1" % PREFIX))
        r = self.pcall("GET", "/%s/" % PREFIX)
        self.assertEqual(r.status, 200)
        self.assertIn(b'src="app.js?v=', r.body)                    # relative: under the prefix
        self.assertEqual(self.pcall("GET", "/%s/app.js" % PREFIX).status, 200)
        # the page of a person answers at the root of the site and under the prefix
        for base in ("", "/" + PREFIX):
            r = self.pcall("GET", "%s/p/%s" % (base, "Q" * 43))
            self.assertEqual(r.status, 404)
            self.assertIn(('href="%s/p/user.css?v=' % base).encode(), r.body)
            self.assertIn(('src="%s/p/user.js?v=' % base).encode(), r.body)
            self.assertEqual(self.pcall("GET", "%s/p/user.css" % base).status, 200)

    def test_sse_under_the_prefix(self):
        self.proxy_on(path=PREFIX, domain=DOMAIN)
        self.assertEqual(self.pcall("GET", "/%s/api/events" % PREFIX).status, 401)
        auth.set_password("правильный-пароль")
        tok = self.cookie_of(self.pcall("POST", "/%s/api/login" % PREFIX, {"password": "правильный-пароль"}))
        c = UnixHTTPConnection(self.sock_path(), timeout=5)
        try:
            c.putrequest("GET", "/%s/api/events" % PREFIX, skip_host=True)
            for k, v in (("Host", DOMAIN), ("X-Real-IP", "198.51.100.7"), ("X-Forwarded-Proto", "https"),
                         ("X-Forwarded-Host", DOMAIN), ("Cookie", "%s=%s" % (auth.COOKIE, tok))):
                c.putheader(k, v)
            c.endheaders()
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            self.assertEqual(r.getheader("Content-Type").split(";")[0], "text/event-stream")
            self.assertEqual(r.getheader("X-Accel-Buffering"), "no")
            first = b""
            while b"\n\n" not in first:
                first += r.fp.readline()
            self.assertTrue(first.startswith(b"retry:") or b"event:" in first or first.startswith(b":"), first)
        finally:
            c.close()

    def test_the_404_names_no_server_and_the_journal_no_prefix(self):
        self.assertNotIn(b"nginx", userpage.NOT_FOUND_BODY)
        self.proxy_on(path=PREFIX, domain=DOMAIN)
        auth.set_password("правильный-пароль")
        tok = self.cookie_of(self.pcall("POST", "/%s/api/login" % PREFIX, {"password": "правильный-пароль"}))
        from kaskad.panel import routes

        @routes.route("GET", r"/api/__boom", auth="admin")
        def boom(req):
            raise RuntimeError("boom")
        try:
            buf = []
            with mock.patch.object(server, "log", lambda text: buf.append(text)):
                self.assertEqual(self.pcall("GET", "/%s/api/__boom" % PREFIX, cookie=tok).status, 500)
                with mock.patch.object(server.Handler, "_handle", side_effect=RuntimeError("x")):
                    self.pcall("GET", "/%s/api/session" % PREFIX)
            self.assertTrue(buf)
            self.assertFalse([b for b in buf if PREFIX in b], buf)
        finally:
            routes.ROUTES[:] = [r for r in routes.ROUTES if r.handler is not boom]
        self.assertNotIn(PREFIX, self.read("/var/log/kaskad.log") if os.path.exists(self.path("/var/log/kaskad.log"))
                         else "")


class CliTest(ProxyCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_proxy_on_off(self):
        rc, out, err = self.run_cli("panel", "proxy", "on", "--domain", "VPN.Example.com", "--json")
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        cfg = conf.load()
        path = cfg["proxy"]["path"]
        self.assertRegex(path, r"^app-[A-Za-z0-9_-]{86}$")
        self.assertEqual(cfg["proxy"], {"on": True, "path": path, "domain": "vpn.example.com"})
        self.assertNotIn(path, out)                                   # masked unless --show
        self.assertEqual(doc["proxy"]["path"], conf.mask_path(path))
        self.assertEqual(conf.login_url("T"), "https://vpn.example.com/%s/login?t=T" % path)
        self.assertNotIn(path, self.read("/var/log/kaskad.log"))
        rc, out, err = self.run_cli("panel", "status")
        self.assertIn("Адрес панели: https://vpn.example.com/%s/" % conf.mask_path(path), out)
        self.assertNotIn(path, out)
        rc, out, err = self.run_cli("panel", "proxy", "on", "--show", "--json")
        self.assertEqual(json.loads(out)["proxy"]["path"], path)                # the path stays
        rc, out, err = self.run_cli("panel", "link", "--json")
        self.assertTrue(json.loads(out)["url"].startswith("https://vpn.example.com/%s/login?t=" % path))
        self.assertIsNone(json.loads(out)["tunnel"])
        self.assertEqual(self.run_cli("panel", "proxy", "on", "--domain", "bad domain")[0], errors.USAGE)
        self.assertEqual(self.run_cli("panel", "proxy", "on", "--path", "a/b")[0], errors.USAGE)
        self.assertEqual(self.run_cli("panel", "proxy", "on", "--new-path")[0], 0)
        self.assertNotEqual(conf.load()["proxy"]["path"], path)
        self.assertEqual(self.run_cli("panel", "proxy", "off")[0], 0)
        self.assertFalse(conf.load()["proxy"]["on"])
        self.assertTrue(conf.login_url("T").startswith("http://127.0.0.1:"))
        self.panel.reload()
        self.assertNotIn("proxy", self.panel.servers)

    def test_broken_settings_are_no_proxy(self):
        self.write("/etc/kaskad/panel/config.json", json.dumps(
            {"proxy": {"on": "yes", "path": "../etc", "domain": "<script>"}}))
        self.assertEqual(conf.load()["proxy"], {"on": False, "path": "", "domain": ""})
