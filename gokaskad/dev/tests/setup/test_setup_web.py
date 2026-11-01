"""The quick setup through the real panel server: the secret path (404 of a foreign one), the one-time link and its
cookie, «used», the password step and the password-only entry afterwards, the expiry, the job with live rows over
GET /api/setup/job, «Готово» -> 404, nothing secret in the journal of the panel or in /var/log/kaskad.log."""

import io
import json
import os
import time
from contextlib import redirect_stderr
from unittest import mock

from kaskad import paths
from kaskad.panel import auth
from kaskad.setup import jobs, link, model, steps, web
from tests.panel.panelcase import PanelCase

PW = "Correct-Horse-9"
STATIC_SETUP = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "lib", "kaskad", "panel", "static", "setup")


def slurp(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


class SetupWebCase(PanelCase):
    def setUp(self):
        super().setUp()
        self._sp = [mock.patch.object(web, "SESSIONS", auth.Sessions()),
                    mock.patch.object(web, "LINKS", auth.RateLimit(auth.LINK_ATTEMPTS, auth.WINDOW)),
                    mock.patch.object(steps, "online", lambda force=False: True)]
        for p in self._sp:
            p.start()
        self.token = model.update(lambda d: link.issue(d))
        self.secret = model.load()["link"]["secret"]
        self.base = "/setup-" + self.secret

    def tearDown(self):
        for p in reversed(self._sp):
            p.stop()
        super().tearDown()

    def scall(self, method, path, body=None, cookie=None, **kw):
        h = dict(kw.pop("headers", {}) or {})
        if cookie:
            h["Cookie"] = "%s=%s" % (web.COOKIE, cookie)
        return self.call(method, self.base + path, body, headers=h, tunnel=False, **kw)

    @staticmethod
    def setup_cookie(r):
        for c in r.cookies:
            name, _, rest = c.partition("=")
            if name == web.COOKIE:
                return rest.split(";", 1)[0], c
        return None, None

    def enter(self):
        r = self.call("GET", "%s/?t=%s" % (self.base, self.token), tunnel=False)
        self.assertEqual(r.status, 303, r)
        tok, raw = self.setup_cookie(r)
        self.assertTrue(tok, r.cookies)
        return tok, raw, r


class EntryTest(SetupWebCase):
    def test_foreign_path_is_the_404_of_an_unknown_path(self):
        unknown = self.call("GET", "/nothing-here", tunnel=False)
        for p in ("/setup-%s/" % ("A" * 32), "/setup-x/", "/setup-%s/api/setup/state" % ("B" * 32)):
            r = self.call("GET", p, tunnel=False)
            self.assertEqual((r.status, r.body), (unknown.status, unknown.body), p)
        # the API of the wizard does not exist outside its path
        self.assertEqual(self.call("GET", "/api/setup/state", tunnel=False).status, 404)
        # under the path — nothing of the panel itself
        self.assertEqual(self.scall("GET", "/api/overview").status, 404)

    def test_bare_base_redirects_to_slash(self):
        r = self.call("GET", self.base, tunnel=False)
        self.assertEqual((r.status, r.headers.get("location")), (301, self.base + "/"))

    def test_link_once_cookie_attributes_and_used(self):
        tok, raw, r = self.enter()
        self.assertEqual(r.headers["location"], self.base + "/")             # the token leaves the address bar
        self.assertIn("HttpOnly", raw)
        self.assertIn("SameSite=Strict", raw)
        self.assertIn("Path=%s/" % self.base, raw)
        doc = self.scall("GET", "/api/setup/session", cookie=tok).json()
        self.assertEqual((doc["auth"], doc["via"]), (True, "link"))
        again = self.call("GET", "%s/?t=%s" % (self.base, self.token), tunnel=False)
        self.assertEqual(again.headers["location"], self.base + "/?link=used")
        self.assertIsNone(self.setup_cookie(again)[0])
        doc = self.scall("GET", "/api/setup/session").json()
        self.assertEqual((doc["auth"], doc["reason"], doc["cmd"]), (False, "link", "kaskadctl setup link"))
        self.assertEqual(self.scall("GET", "/api/setup/state").status, 401)
        self.assertEqual(self.scall("GET", "/api/setup/state", cookie=tok).status, 200)

    def test_page_is_served_under_the_path(self):
        r = self.scall("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("setup.js?v=", r.text)                                  # the version placeholder is replaced
        js = self.scall("GET", "/setup.js")
        self.assertEqual(js.status, 200)
        self.assertIn("text/javascript", js.headers["content-type"])
        self.assertIn("KASKAD_SETUP_MOCK", js.text)
        self.assertEqual(self.call("GET", "/setup.js", tunnel=False).status, 404)   # only under the secret path

    def test_every_file_of_the_page_answers_under_the_path(self):
        """The page loads its own files next to it and the shared ones of the panel by «../» (tokens, components,
        icons, QR, fonts): every src/href of index.html and every url() of tokens.css answers 200 to a stranger of the
        public listener — nothing of the wizard needs a panel session."""
        import re
        from urllib.parse import urljoin, urlsplit
        page = self.scall("GET", "/")
        refs = re.findall(r'(?:src|href)="([^"]+)"', page.text)
        refs = [r for r in refs if not r.startswith("data:")]
        self.assertTrue({"../tokens.css", "../components.css", "../boot.js", "../icons.js", "../qr.js", "setup.js",
                         "qs.css", "setup-i18n-ru.js"} <= set(r.split("?")[0] for r in refs), refs)
        for ref in refs:
            path = urlsplit(urljoin(self.base + "/", ref)).path
            r = self.call("GET", path, tunnel=False)
            self.assertEqual(r.status, 200, ref)
            if ref.startswith("../tokens.css"):
                for font in re.findall(r"url\(([^)]+)\)", r.text):
                    fp = urlsplit(urljoin(path, font.strip("'\""))).path
                    self.assertEqual(self.call("GET", fp, tunnel=False).status, 200, fp)

    def test_design_system_under_the_path(self):
        # the wizard takes the panel's tokens.css, components.css, boot.js and the fonts (docs/k3/DESIGN-SYSTEM.md):
        # tokens.css asks for url(fonts/…) — under /setup-<secret>/ too
        for name, ctype in (("tokens.css", "text/css"), ("components.css", "text/css"), ("boot.js", "javascript"),
                            ("fonts/onest.woff2", "font/woff2"), ("fonts/rubik.woff2", "font/woff2"),
                            ("fonts/jbmono.woff2", "font/woff2")):
            r = self.scall("GET", "/" + name)
            self.assertEqual(r.status, 200, name)
            self.assertIn(ctype, r.headers["content-type"], name)
        self.assertEqual(self.scall("GET", "/fonts/../tokens.css").status, 404)
        self.assertEqual(self.scall("GET", "/fonts/x/onest.woff2").status, 404)

    def test_csrf_header_needed(self):
        tok, _, _ = self.enter()
        r = self.scall("POST", "/api/setup/step/password", {"password": PW}, cookie=tok, csrf=False)
        self.assertEqual(r.status, 403)

    def test_password_then_only_password(self):
        tok, _, _ = self.enter()
        r = self.scall("POST", "/api/setup/step/password", {"password": "short"}, cookie=tok)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.json()["error"]["code"], "weak")
        r = self.scall("POST", "/api/setup/step/password", {"password": PW}, cookie=tok)
        doc = r.json()
        self.assertEqual(doc["state"], "ok", doc)
        new, raw = self.setup_cookie(r)
        self.assertTrue(new)
        # the link session is gone (the password changed), the new one is «password»
        self.assertEqual(self.scall("GET", "/api/setup/state", cookie=tok).status, 401)
        self.assertEqual(self.scall("GET", "/api/setup/session", cookie=new).json()["via"], "password")
        # a fresh link has no token any more; another browser logs in by the password
        self.assertNotIn("?t=", link.url(model.load(), model.update(lambda d: link.issue(d))) or "")
        self.assertEqual(self.scall("GET", "/api/setup/session").json()["reason"], "password")
        bad = self.scall("POST", "/api/setup/login", {"password": "wrong-wrong-1"})
        self.assertEqual(bad.status, 401)
        good = self.scall("POST", "/api/setup/login", {"password": PW})
        self.assertEqual(good.status, 200)
        self.assertTrue(self.setup_cookie(good)[0])

    def test_expired_link(self):
        tok, _, _ = self.enter()
        model.update(lambda d: d["link"].update(expires=int(time.time()) - 1))
        self.assertEqual(self.scall("GET", "/").status, 200)                # the page says «истекла»
        doc = self.scall("GET", "/api/setup/session", cookie=tok).json()
        self.assertEqual((doc["auth"], doc["reason"]), (False, "expired"))
        r = self.scall("GET", "/api/setup/state", cookie=tok)
        self.assertEqual((r.status, r.json()["data"]["reason"]), (410, "expired"))

    def test_bound_link_answers_404_to_another_address(self):
        model.update(lambda d: d["link"].update(bind=True, bound_ip="198.51.100.1"))
        r = self.call("GET", self.base + "/", tunnel=False, headers={"X-Forwarded-For": "198.51.100.9"})
        self.assertEqual(r.status, 404)

    def test_finish_then_404_and_panel_session(self):
        tok, _, _ = self.enter()
        r = self.scall("POST", "/api/setup/step/password", {"password": PW}, cookie=tok)
        new, _ = self.setup_cookie(r)
        r = self.scall("POST", "/api/setup/finish", {}, cookie=new)
        self.assertEqual(r.status, 200)
        self.assertTrue(self.cookie_of(r))                                    # «Открыть панель» without a second login
        self.assertEqual(self.scall("GET", "/").status, 404)
        self.assertEqual(self.scall("GET", "/api/setup/session", cookie=new).status, 404)

    def test_long_step_runs_as_job_with_rows(self):
        tok, _, _ = self.enter()
        r = self.scall("POST", "/api/setup/step/password", {"password": PW}, cookie=tok)
        cookie, _ = self.setup_cookie(r)

        def launch(job_id, step):
            body = jobs.take_input(job_id)
            prog = jobs.Progress(job_id, step)
            out = steps.run(step, body, self.w.runner, prog, by="job")
            prog.finish(out)

        def fake_import(runner, force=False):
            return {"result": "ok", "imported": [{"proto": "tcp", "port": 443}], "warnings": [], "backup": "/b"}
        with mock.patch.object(jobs, "LAUNCH", launch), mock.patch("kaskad.migrate.apply.run", fake_import):
            r = self.scall("POST", "/api/setup/step/import", {}, cookie=cookie)
        self.assertEqual(r.status, 202, r)
        job = r.json()["job"]
        doc = self.scall("GET", "/api/setup/job", cookie=cookie).json()["job"]
        self.assertEqual((doc["id"], doc["state"]), (job["id"], "done"))
        rows = dict((x["item"], x) for x in doc["items"])
        self.assertEqual(rows["forwards"]["state"], "ok")
        self.assertTrue(isinstance(rows["forwards"]["since"], float))
        self.assertTrue(doc["result"]["data"]["undo"])
        st = self.scall("GET", "/api/setup/state", cookie=cookie).json()
        self.assertEqual(st["steps"]["import"]["state"], "ok")

    def test_nothing_secret_in_journals(self):
        err = io.StringIO()
        with redirect_stderr(err):
            tok, _, _ = self.enter()
            self.scall("POST", "/api/setup/step/password", {"password": PW}, cookie=tok)
            self.call("GET", "/setup-%s/x" % ("Z" * 32), tunnel=False)
            self.scall("GET", "/api/setup/explode")
        logs = err.getvalue() + slurp(paths.log())
        for secret in (self.secret, self.token, PW):
            self.assertNotIn(secret, logs)
        self.assertIn("SETUP: вход в мастер по одноразовой ссылке", slurp(paths.log()))


class IpCertBannerTest(SetupWebCase):
    def test_banner_says_why_without_certificate(self):
        from kaskad.ipcert import store
        tok, _, _ = self.enter()
        store.note_try(False, "port80", "Порт 80/tcp занят: nginx (pid 7).")
        doc = self.scall("GET", "/api/setup/ipcert", cookie=tok).json()
        self.assertFalse(doc["serving"])
        self.assertEqual(doc["last_try"]["reason"], "port80")
        self.assertIn("не выпустился: Порт 80/tcp занят: nginx (pid 7)", doc["text"])
        self.assertIn("предупреждение один раз", doc["text"])


class BackupBodyLimitTest(SetupWebCase):
    """T-M6-qs330 №4: a copy of the settings up to 16 MB opens on stage 1 too (the panel's 20 MB body), but only the
    step «backup-open» and only with a live session of the wizard; the rest of the wizard keeps 1 MB."""

    def test_big_copy_only_for_backup_open_with_a_session(self):
        from kaskad.panel import server
        self.assertEqual(server.SETUP_BIG_BODY_PATHS, ("/api/setup/step/backup-open",))
        tok, _, _ = self.enter()
        r = self.scall("POST", "/api/setup/step/password", {"password": PW}, cookie=tok)
        cookie, _ = self.setup_cookie(r)
        big = json.dumps({"file": "A" * (3 << 20), "password": "x" * 12}).encode()
        r = self.scall("POST", "/api/setup/step/backup-open", raw_body=big, cookie=cookie)
        self.assertNotEqual(r.status, 413, r)                       # read and answered by the step, not by size
        def head(path, ck):
            out = self.raw(("POST %s%s HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Kaskad-Admin: 1\r\n%sContent-Length: %d\r\n\r\n"
                            % (self.base, path, ("Cookie: %s=%s\r\n" % (web.COOKIE, ck)) if ck else "", 3 << 20)).encode())
            return out.split(b"\r\n", 1)[0]
        self.assertIn(b" 413 ", head("/api/setup/step/backup-open", None))           # no session — the usual 1 MB
        self.assertIn(b" 413 ", head("/api/setup/step/exits-add", cookie))           # another step — 1 MB
        self.assertIn(b" 413 ", head("/api/setup/step/backup-open", "Z" * 40))       # a made-up session

    def test_page_allows_16_mb_for_a_copy(self):
        with open(os.path.join(STATIC_SETUP, "setup.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("const BACKUP_FILE_MAX = 16 * 1024 * 1024;", js)
        self.assertIn('const max = kind === "backup" ? BACKUP_FILE_MAX : FILE_MAX;', js)


class MoveTest(SetupWebCase):
    """Stage 4 (T-M6-qs330 №2): the cookie of the wizard belongs to the host, so before the certificate of the domain
    the page takes a one-time token of the move and opens the wizard on the domain with it — no second login."""

    def setUp(self):
        super().setUp()
        tok, _, _ = self.enter()
        r = self.scall("POST", "/api/setup/step/password", {"password": PW}, cookie=tok)
        self.cookie, _ = self.setup_cookie(r)
        self.pub = self.public_on()
        self.dom = mock.patch("kaskad.site.model.domain_of", return_value="vpn.example.com")
        self.dom.start()
        self.addCleanup(self.dom.stop)

    def move(self, **kw):
        return self.scall("POST", "/api/setup/move", {}, cookie=kw.pop("cookie", self.cookie), **kw)

    def test_token_and_address_on_the_domain(self):
        self.assertEqual(self.move(csrf=False).status, 403)
        self.assertEqual(self.move(cookie=None).status, 401)
        doc = self.move().json()
        want = "https://vpn.example.com:%d%s/" % (self.pub, self.base)
        self.assertEqual((doc["host"], doc["clean"]), ("vpn.example.com", want))
        self.assertTrue(doc["url"].startswith(want + "?t="), doc["url"])
        self.assertTrue(0 < doc["expires_in"] <= link.MOVE_TTL)
        t = doc["url"].split("?t=", 1)[1]
        # the browser opens it on the domain: a session «password» at once, the token leaves the address bar
        r = self.call("GET", "%s/?t=%s" % (self.base, t), tunnel=False)
        self.assertEqual((r.status, r.headers["location"]), (303, self.base + "/"))
        new, _ = self.setup_cookie(r)
        doc = self.scall("GET", "/api/setup/session", cookie=new).json()
        self.assertEqual((doc["auth"], doc["via"]), (True, "password"))
        # one use only; a stale move token asks the password (not «ссылка уже открыта»)
        again = self.call("GET", "%s/?t=%s" % (self.base, t), tunnel=False)
        self.assertEqual(again.headers["location"], self.base + "/?link=revoked")
        self.assertIsNone(self.setup_cookie(again)[0])
        self.assertNotIn(t, slurp(paths.log()))

    def test_expired_move_token_asks_the_password(self):
        t = self.move().json()["url"].split("?t=", 1)[1]
        with mock.patch.object(link, "_now", return_value=time.time() + link.MOVE_TTL + 1):
            r = self.call("GET", "%s/?t=%s" % (self.base, t), tunnel=False)
        self.assertEqual(r.headers["location"], self.base + "/?link=revoked")

    def test_no_domain_or_closed_panel(self):
        self.dom.stop()
        with mock.patch("kaskad.site.model.domain_of", return_value=None):
            r = self.move()
        self.assertEqual((r.status, r.json()["data"]["reason"]), (409, "no_domain"))
        self.dom.start()
        from kaskad.panel import conf
        cfg = conf.load()
        cfg["public"]["on"] = False
        conf.save(cfg)
        r = self.move()
        self.assertEqual((r.status, r.json()["data"]["reason"]), (409, "not_public"))

    def test_move_token_never_outlives_the_link(self):
        model.update(lambda d: d["link"].update(expires=int(time.time()) + 60))
        doc = self.move().json()
        self.assertLessEqual(doc["expires_in"], 60)
        tokens = [x for x in model.load()["tokens"] if x.get("by") == "move"]
        self.assertTrue(tokens and all(x["expires"] <= int(time.time()) + 60 for x in tokens))


class IpCertNextToDomainTest(SetupWebCase):
    def test_banner_by_ip_names_the_ip_certificate(self):
        """The page by the address is served the IP certificate (SNI) — the banner says so, not «на домен»."""
        from kaskad.panel import conf
        tok, _, _ = self.enter()
        with mock.patch.object(conf, "site_cert", return_value=("vpn.example.com", "C", "K")), \
                mock.patch.object(conf, "ip_cert", return_value=("C2", "K2")):
            doc = self.scall("GET", "/api/setup/ipcert", cookie=tok).json()     # Host 127.0.0.1:<port>
        self.assertTrue(doc["domain"] and doc["serving"])
        self.assertIn("сертификатом Let's Encrypt на IP", doc["text"])
        with mock.patch.object(conf, "site_cert", return_value=("vpn.example.com", "C", "K")), \
                mock.patch.object(conf, "ip_cert", return_value=None):
            doc = self.scall("GET", "/api/setup/ipcert", cookie=tok).json()
        self.assertIn("на домен", doc["text"])

    def test_by_address(self):
        from kaskad.panel.routes import Request
        for host, want in (("203.0.113.7:43193", True), ("[2001:db8::1]:443", True), ("127.0.0.1", True),
                           ("vpn.example.com:43193", False), ("vpn.example.com", False), ("", False)):
            req = Request("GET", "/", headers={"host": host})
            self.assertEqual(web._by_address(req), want, host)


class UxRoutesTest(SetupWebCase):
    """3.3.3: «Telegram-бот» and «Ключ ИИ-агента» of the wizard are the panel's components with the panel's own handlers
    (kaskad.panel.ux) behind the wizard's session — /api/setup/ux/…: nothing without the session; the token goes in the
    body only, never into setup.json or a journal; the removal of a key is a POST (the page has no DELETE)."""

    def test_session_needed(self):
        for method, path in (("GET", "/api/setup/ux/bot"), ("GET", "/api/setup/ux/mcp"),
                             ("POST", "/api/setup/ux/bot/check"), ("POST", "/api/setup/ux/mcp/keys")):
            self.assertEqual(self.scall(method, path, {} if method == "POST" else None).status, 401, path)

    def test_the_panel_handlers(self):
        from kaskad.bot import onboard
        tok, _, _ = self.enter()
        tok, _ = self.setup_cookie(self.scall("POST", "/api/setup/step/password", {"password": PW}, cookie=tok))
        token = "123456789:" + "A" * 35
        r = self.scall("POST", "/api/setup/ux/bot/check", {"token": "nope"}, cookie=tok)
        self.assertEqual((r.status, r.json()["data"]["reason"]), (400, "bad_format"))
        seen = []

        def apply(runner, t, chat, test=True):
            seen.append((t, chat))
            return {"username": "kaskad_test_bot", "chat_id": chat, "started": True, "sent": True, "sent_why": None}
        with mock.patch.object(onboard, "apply", apply):
            r = self.scall("POST", "/api/setup/ux/bot/test", {"token": token, "chat_id": "555"}, cookie=tok)
        self.assertEqual(r.status, 200, r)
        self.assertEqual((r.json()["started"], r.json()["chat_id"]), (True, "555"))
        self.assertEqual(seen, [(token, "555")])
        self.assertNotIn(token, r.text)
        self.assertNotIn(token, slurp(model.path()))
        self.assertNotIn(token, slurp(paths.log()))
        self.assertIn("SETUP: бот настроен в мастере (@kaskad_test_bot)", slurp(paths.log()))
        from kaskad.mcp import keys as mkeys
        fp = "SHA256:" + "x" * 43
        with mock.patch.object(mkeys, "delete", lambda f, runner: {"removed": True, "ended": 0}):
            r = self.scall("POST", "/api/setup/ux/mcp/keys/%s/del" % fp.replace(":", "%3A").replace("/", "%2F"), {}, cookie=tok)
        self.assertEqual(r.status, 200, r)
        self.assertEqual(r.json()["fingerprint"], fp)

    def test_panel_hosting(self):
        """OWNER-FEEDBACK №45 for the panel: GET /api/hosting with the panel's own session (the wizard's
        /api/setup/product needs the wizard's one) — the partner hosting of product.HOSTING in words of the language."""
        from kaskad.setup import product
        doc = self.ok("GET", "/api/hosting")
        self.assertEqual([(x["id"], x["url"]) for x in doc["hosting"]], [(x["id"], x["url"]) for x in product.HOSTING])
        self.assertIsNone(doc["guide"])
        self.assertEqual(self.call("GET", "/api/hosting", tunnel=False).status, 401)
