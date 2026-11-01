"""Kaskad 3.3.7.5 — the open mode only under the secret prefix of the panel (one secret with nginx), every address it
gives carries the prefix (masked unless asked), the bare port and anything outside the prefix answer as an empty site
with nothing of Kaskad; «Сменить секретный адрес» / ``panel proxy --new-path`` move the open mode too. Each guard has a
«порча» (the guard taken away — the test turns red)."""
import contextlib
import io
import json
from unittest import mock

from kaskad import cli, summary
from kaskad.panel import auth, conf, server, userpage
from kaskad.panel import cli as pcli
from tests.panel.panelcase import PanelCase

OUTSIDE = ("/", "/login", "/login?t=abc", "/api/status", "/api/session", "/api/panel", "/index.html", "/app.js",
           "/static/app.js", "/favicon.ico", "/fonts/x.woff2", "/robots.txt", "/.env", "/p", "/api/setup/state")
SIGNS = (b"kaskad", b"Kaskad", b"Content-Security-Policy", b"Permissions-Policy", b"X-Frame-Options", b"{")


class _Base(PanelCase):
    def setUp(self):
        super().setUp()
        self.pport = self.public_on()

    def get(self, path, method="GET", host=None, **kw):
        return self.call(method, path, port=self.pport, https=True, tunnel=False, host=host, **kw)

    def assert_empty_site(self, r, what):
        self.assertEqual(r.status, 404, "%s -> %r" % (what, r))
        self.assertEqual(r.body, userpage.NOT_FOUND_BODY, what)
        self.assertNotIn("server", r.headers, what)                # no «Server: kaskad-panel»
        head = "\n".join("%s: %s" % kv for kv in r.headers.items()).encode()
        for s in SIGNS:
            self.assertNotIn(s, head + r.body, (what, s))

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(list(argv))
        return rc, out.getvalue()


class OpenModeSecretPathTest(_Base):
    def test_outside_the_prefix_an_empty_site(self):
        self.assertTrue(self.pb.startswith("/app-") and len(self.pb) == 1 + 4 + 86)
        for p in OUTSIDE:
            self.assert_empty_site(self.get(p), p)
            h = self.get(p, method="HEAD")
            self.assertEqual((h.status, h.body, "server" in h.headers), (404, b"", False), "HEAD " + p)
        self.assert_empty_site(self.get("/", method="POST", body={"x": 1}), "POST /")
        for m in ("OPTIONS", "PATCH", "TRACE"):                        # T-3375 Н-1: not the 501 of http.server
            for p in ("/", "/login", "/api/status"):
                self.assert_empty_site(self.get(p, method=m), m + " " + p)
        # a foreign prefix, a prefix of the prefix, the prefix with a tail — the same
        for p in ("/app-" + "A" * 86 + "/", self.pb[:-1] + "/", self.pb + "x/", self.pb.upper() + "/api/session"):
            self.assert_empty_site(self.get(p), p)
        # a foreign Host outside the prefix — not the panel's 403 (that names it), the same empty site
        self.assert_empty_site(self.get("/", host="evil.example"), "evil host")
        # an absolute URI and an unknown method — no JSON of the panel
        self.assert_empty_site(self.get("http://127.0.0.1/"), "absolute uri")
        raw = self.tls_raw(b"BREW / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        self.assertIn(b"501", raw.split(b"\r\n", 1)[0])
        for s in SIGNS[:5]:
            self.assertNotIn(s, raw)
        self.assertNotIn(b"Server:", raw)

    def tls_raw(self, data):
        import socket
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", self.pport), timeout=10) as s, ctx.wrap_socket(s) as t:
            t.sendall(data)
            out = b""
            while True:
                got = t.recv(65536)
                if not got:
                    return out
                out += got

    def test_wrong_token_of_a_page_is_the_same_answer(self):
        root = self.get("/")
        for p in ("/p/", "/p/x", "/p/" + "x" * 43 + "/sub/more", "/p/../api/session"):
            r = self.get(p)
            self.assertEqual((r.status, r.body), (root.status, root.body), p)
            self.assertNotIn("server", r.headers, p)
        # a link-shaped token that opens nothing: the page «Ссылка больше не действует» (T-G1b) — one and the same
        # for any token, as before 3.3.7.5 and as on the domain; no Server there either
        a, b = self.get("/p/" + "x" * 43), self.get("/p/" + "y" * 43)
        self.assertEqual((a.status, a.body), (b.status, b.body))
        self.assertEqual(a.status, 404)
        self.assertNotIn("server", a.headers)

    def test_under_the_prefix_the_panel(self):
        r = self.get(self.pb)
        self.assertEqual((r.status, r.headers.get("location")), (301, self.pb + "/"))
        r = self.get(self.pb + "/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"<html", r.body.lower())
        self.assertEqual(self.ok("GET", self.pb + "/api/session", port=self.pport, https=True)["scheme"], "https")
        auth.set_password("правильный-пароль")
        r = self.call("POST", self.pb + "/api/login", {"password": "правильный-пароль"}, port=self.pport, https=True,
                      tunnel=False)
        self.assertEqual(r.status, 200, r)
        ck = r.cookies[0]
        self.assertIn("Path=%s/" % self.pb, ck)                     # the session only under the prefix
        self.assertIn("Secure", ck)
        tok = self.cookie_of(r)
        self.assertTrue(self.ok("GET", self.pb + "/api/overview", port=self.pport, https=True, tunnel=False,
                                cookie=tok)["ok"])
        # the same session outside the prefix — nothing
        self.assert_empty_site(self.get("/api/overview", cookie=tok), "cookie outside")
        # a one-time link redeems under the prefix and brings back under it
        tl = auth.issue_link(by="test")
        r = self.get(self.pb + "/login?t=" + tl)
        self.assertIn(r.status, (302, 303))
        self.assertTrue(r.headers["location"].startswith(self.pb + "/"), r.headers)

    def test_every_given_address_carries_the_prefix(self):
        cfg = conf.load()
        path = cfg["proxy"]["path"]
        origin = "https://127.0.0.1:%d/" % self.pport
        self.assertEqual(conf.public_origin(cfg), origin)
        self.assertEqual(conf.public_url(cfg), origin + path + "/")
        self.assertEqual(conf.public_url_masked(cfg), origin + conf.mask_path(path) + "/")
        self.assertNotIn(path, conf.public_url_masked(cfg))
        self.assertTrue(conf.login_url("TOK", cfg).startswith(origin + path + "/login?t="))
        self.assertTrue(auth.login_link(by="bot")["url"] is None or path in auth.login_link(by="bot")["url"])
        # the API of the panel: «Адрес панели» masked, the full one only by the button
        tok = auth.SESSIONS.create("password", "127.0.0.1")        # with the open mode the tunnel asks a login
        doc = self.ok("GET", "/api/panel", cookie=tok)
        self.assertEqual(doc["address"], {"kind": "public", "masked": conf.public_url_masked(cfg), "fallback": None,
                                          "port": self.pport})
        self.assertNotIn(path, json.dumps(doc["address"]))
        full = self.ok("GET", "/api/site/panel-url", cookie=tok)
        self.assertEqual((full["url"], full["kind"]), (origin + path + "/", "public"))
        link = self.ok("POST", "/api/panel/link", {}, cookie=tok)
        self.assertTrue(link["url"].startswith(origin + path + "/login?t="), link)
        # CLI: masked unless --show
        st = pcli.status_doc(self.w.runner)
        self.assertEqual(st["url"], conf.public_url_masked(cfg))
        self.assertEqual(pcli.status_doc(self.w.runner, show=True)["url"], origin + path + "/")
        rc, out = self.cli("panel", "status")
        self.assertNotIn(path, out)
        self.assertIn(conf.public_url_masked(cfg), out)
        rc, out = self.cli("panel", "status", "--show")
        self.assertIn(origin + path + "/", out)
        rc, out = self.cli("site", "panel-url")
        self.assertIn(conf.public_url_masked(cfg), out)
        self.assertNotIn(path, out)
        # the head of the SSH menu (summary): masked only
        pan = summary._panel({}, None)
        self.assertEqual(pan["masked"], conf.public_url_masked(cfg))
        self.assertNotIn(path, json.dumps(pan))
        # the status file of the running panel (0600) — the full address of the open mode
        self.assertEqual(conf.read_status()["url"], origin + path + "/")

    def test_new_path_moves_the_open_mode(self):
        old = self.pb
        self.assertEqual(self.get(old + "/").status, 200)
        rc, _ = self.cli("panel", "proxy", "off", "--new-path")
        self.assertEqual(rc, 0)
        self.panel.reload()
        new = conf.proxy_base(conf.load())
        self.assertNotEqual(new, old)
        self.assert_empty_site(self.get(old + "/"), "old prefix")
        self.assert_empty_site(self.get(old + "/api/session"), "old prefix api")
        self.assertEqual(self.get(new + "/").status, 200)
        # «Сменить секретный адрес» without a site: the open mode's own secret
        from kaskad.site import ops as sops
        with mock.patch.object(sops.consumers, "_reload_panel", lambda r: self.panel.reload()):
            res = sops.rotate_panel_path(self.w.runner)
        newer = conf.proxy_base(conf.load())
        self.assertNotEqual(newer, new)
        self.assertEqual(res["kind"], "public")
        self.assertNotIn(newer[1:], res["url"])
        self.assert_empty_site(self.get(new + "/"), "rotated away")
        self.assertEqual(self.get(newer + "/").status, 200)

    def test_an_updated_node_gets_its_secret(self):
        cfg = conf.load()
        cfg["proxy"]["path"] = ""                       # a node of 3.3.7.4: the open mode at the bare port
        conf.save(cfg)
        self.panel.reload()
        path = conf.load()["proxy"]["path"]
        self.assertTrue(conf.PROXY_PATH_RE.match(path) and path.startswith("app-"))
        self.assert_empty_site(self.get("/"), "updated root")
        self.assertEqual(self.get("/" + path + "/").status, 200)
        self.assertIn("секретный путь панели", self.read("/var/log/kaskad.log"))
        self.assertNotIn(path, self.read("/var/log/kaskad.log"))

    def test_setup_and_pages_stay_at_the_root(self):
        # the quick setup has its own secret at the root of the port, not under the panel's
        from kaskad.setup import link
        doc = {"link": {"secret": "s" * 40}}
        self.assertEqual(link.url(doc, cfg=conf.load()), "https://127.0.0.1:%d/setup-%s/" % (self.pport, "s" * 40))
        self.assertEqual(server.public_view(conf.load(), "/setup-abc/")[3], False)
        self.assertEqual(server.public_view(conf.load(), "/p/abc")[3], False)
        # under the panel's prefix the setup is not served
        self.assertEqual(self.get(self.pb + "/setup-" + "s" * 40 + "/").status, 404)

    def test_wizard_takes_its_shared_files_only_while_active(self):      # 3.3.7.6: the wizard came without its look
        import os
        import re
        shared = ("/tokens.css", "/components.css", "/ux.css", "/give.css", "/boot.js", "/ux.js", "/give.js", "/qr.js")
        for p in shared:                                                  # no wizard: the root stays shut
            self.assert_empty_site(self.get(p), p)
        with mock.patch("kaskad.setup.model.load", return_value={"status": "active", "link": {"secret": "s" * 40}}):
            for p in shared:
                self.assertEqual(self.get(p).status, 200, p)
            for p in ("/", "/index.html", "/app.js", "/api/status", "/login", "/static/../app.js", "/fonts/../app.js",
                      "/static/index.html", "/static/app.js", "/static/i18n-ru.js", "/static/apps/../app.js"):   # REV-3376 4
                self.assert_empty_site(self.get(p), p)                    # the panel itself — still nobody's path
            # every «../» the wizard's page and its scripts ask for is let in (a new one would lose the look again)
            st = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel",
                              "static", "setup")
            asked = set()
            for f in ("index.html", "setup.js", "look.js"):
                src = open(os.path.join(st, f), encoding="utf-8").read()
                asked |= {m for m in re.findall(r'["(]\.\./([A-Za-z0-9_.-]+\.(?:js|css))', src)
                          if not m.startswith("dev-")}
                if '"../static/"' in src:                                  # give.js: iconBase + apps/<id>.png
                    asked.add("static/apps/v2rayng.png")
            self.assertTrue(asked >= {"tokens.css", "ux.js", "give-i18n-en.js"}, asked)
            for name in sorted(asked):
                self.assertTrue(server._setup_shared("/" + name), name)
            self.assertTrue(server._setup_shared("/fonts/onest.woff2"))
            icon = next(n for n in server.STATIC.entries if n.startswith("apps/") and n.endswith(".png"))
            self.assertEqual(self.get("/static/" + icon).status, 200, icon)     # a real icon of an app
            self.assertFalse(server._setup_shared("/static/app.js"))
        with mock.patch("kaskad.setup.model.load", side_effect=OSError("broken")):
            self.assertFalse(server._setup_shared("/tokens.css"))          # fail closed
        for doc in ({"status": "finished", "link": {"secret": "s" * 40}}, {"status": "active", "link": {}}):
            with mock.patch("kaskad.setup.model.load", return_value=doc):
                self.assertFalse(server._setup_shared("/tokens.css"), doc)


class Porcha3375Test(_Base):
    """Порча: the guard taken away — the root of the port gives the panel; the tests above would see it."""

    def test_porcha_without_the_prefix_check(self):
        with mock.patch.object(server, "public_view", lambda cfg, path: ("", path, False, False)):
            r = self.get("/")
            self.assertEqual(r.status, 200)                         # the login page of the panel at the bare port
            with self.assertRaises(AssertionError):
                self.assert_empty_site(r, "porcha")

    def test_porcha_server_header(self):
        with mock.patch.object(server.Handler, "_send_plain", server.Handler._send):
            with self.assertRaises(AssertionError):
                self.assert_empty_site(self.get("/"), "porcha header")

    def test_porcha_unmasked_status(self):
        with mock.patch.object(conf, "public_url_masked", conf.public_url):
            with self.assertRaises(AssertionError):
                OpenModeSecretPathTest.test_every_given_address_carries_the_prefix(self)

    def test_porcha_no_secret_made(self):
        with mock.patch.object(conf, "ensure_secret", lambda cfg=None: cfg):
            with self.assertRaises(AssertionError):
                OpenModeSecretPathTest.test_an_updated_node_gets_its_secret(self)
