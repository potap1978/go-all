"""Kaskad 3.3.7.5, круг 2 — решения владельца 20.10:

1. Ссылки людей (страница /p/…, подписка, всё, что строится из ``access.json``) — на домене (nginx, 443), когда сайт
   на домене включён; порт открытого режима — только без него. (Когда вход по порту выключается сам — круг 3,
   ``tests/test_3375_hf_r3.py``.)
2. Без домена — панель и ссылки людей на сертификате Let's Encrypt по IP (``kaskad.ipcert``).
3. Вкладка знает, каким путём пришла (``GET /api/session``: listener), смена секрета отдаёт новый адрес своего входа.
4. Поток по старому секрету на порту закрывается; чужой Host на /p/ — ответ пустого сайта.
6. Секрет сравнивается за постоянное время; смена секрета гасит сессии из интернета на сервере.

Каждая проверка держит порчу (класс ``Porcha3375R2Test``)."""
import hashlib
import json
import os
import ssl
import socket
from unittest import mock

import unittest

from kaskad import state, util
from kaskad.panel import auth, conf, server, userpage
from kaskad.site import consumers, nginx
from kaskad.site import ops as sops
from tests.panel.panelcase import PanelCase
from tests.panel.test_panel_revoke import TlsSse, until_eof
from tests.site.sitecase import DOMAIN, SiteCase
from tests.userpage_case import UID1, PageCase

DOMAIN_ACC = {"public": True, "port": 443, "scheme": "https", "host": DOMAIN}


def _site_state(mode="site", domain=DOMAIN):
    st = state.load()
    st["site"] = dict(st.get("site") or {}, domain=domain, mode=mode)
    state.save(st)


def _cfg(public_on=True, proxy_on=True, domain=DOMAIN, port=41234):
    cfg = conf.load()
    cfg["public"] = {"on": public_on, "port": port, "bind": "203.0.113.10", "host": "203.0.113.10",
                     "cert_host": "203.0.113.10"}
    cfg["proxy"].update(on=proxy_on, domain=domain if proxy_on else "", path="app-" + "s" * 86)
    conf.save(cfg)
    return cfg


class PeopleLinksOnTheDomainTest(PageCase):
    """1: where the links of people point — the domain with the site, the port only without it."""

    def links(self, cfg):
        conf.write_access(cfg)
        tok = self.token()
        return userpage.url(UID1), userpage.sub_url(UID1) if userpage.sub_enable(UID1) else None, tok

    def test_with_the_site_every_link_is_on_the_domain(self):
        _site_state("site")
        cfg = _cfg(public_on=True)
        self.assertEqual(conf.access_doc(cfg), DOMAIN_ACC)
        page, sub, tok = self.links(cfg)
        self.assertEqual(page, "https://%s/p/%s" % (DOMAIN, tok))
        self.assertTrue(sub.startswith("https://%s/p/" % DOMAIN) and sub.endswith("/sub"), sub)
        st = userpage.status(UID1)
        self.assertEqual((st["url"], st["public"], st["note"]), (page, True, ""))
        # the port off (a new install with the domain): the links stay — on the domain
        cfg = _cfg(public_on=False)
        self.assertEqual(conf.access_doc(cfg), DOMAIN_ACC)
        self.assertEqual(self.links(cfg)[0], page)

    def test_without_the_site_the_port(self):
        for mode, proxy_on in (("domain", False), ("domain", True), ("site", False)):
            _site_state(mode)
            cfg = _cfg(public_on=True, proxy_on=proxy_on)
            self.assertEqual(conf.access_doc(cfg)["port"], 41234, (mode, proxy_on))
            self.assertIn(":41234/p/", self.links(cfg)[0], (mode, proxy_on))
        # the site on for another domain than the panel's listener — not trusted for /p/
        _site_state("site", domain="other.example.net")
        self.assertEqual(conf.pages_domain(_cfg()), "")
        # no domain, no port — nothing public: the link is only a path
        st = state.load()
        st.pop("site", None)
        state.save(st)
        cfg = _cfg(public_on=False, proxy_on=False)
        self.assertEqual(conf.access_doc(cfg)["public"], False)
        self.assertTrue(self.links(cfg)[0].startswith("/p/"))


class SiteOnWritesTheLinksTest(SiteCase):
    """1: ``site mode site`` itself turns the links of people to the domain (the panel's reload may have read the site
    still off: SIGHUP is asynchronous)."""

    def setUp(self):
        super().setUp()
        self.with_domain(issued="le")

    def test_site_on_then_off(self):
        from kaskad.site import templates
        with mock.patch.object(nginx, "ensure", return_value=[]), \
                mock.patch.object(nginx, "write", return_value="start"), \
                mock.patch.object(nginx, "can_reject_handshake", return_value=True), \
                mock.patch.object(nginx, "ufw_allow443", return_value=False), \
                mock.patch.object(nginx, "ours", return_value=True), \
                mock.patch.object(nginx, "remove", return_value="reload"), \
                mock.patch.object(nginx, "ufw_delete443", return_value=False), \
                mock.patch.object(templates, "deploy", return_value={"ok": True, "id": "demo", "name": "Демо",
                                                                        "source": "repo", "fallback": False}), \
                mock.patch.object(consumers, "_reload_panel", return_value=False):      # the panel's reload: nothing
            sops.mode(self.w.runner, "site")
            self.assertEqual(json.loads(util.read_text(conf.access_path()) or "null"), DOMAIN_ACC)
            sops.mode(self.w.runner, "domain")
            self.assertEqual(conf.access_doc(conf.load())["host"], None)       # back: not public (no port here)


class IpCertificateTest(PanelCase):
    """2: no domain — the panel under its secret prefix and the links of people go on the Let's Encrypt certificate
    of the IP address (not the node's self-signed one)."""

    def setUp(self):
        super().setUp()
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "site")
        with open(os.path.join(here, "other.pem")) as a, open(os.path.join(here, "other.key")) as b:
            self.ip = (a.read(), b.read())
        for p in (mock.patch.object(conf, "site_cert", return_value=None),
                  mock.patch.object(conf, "ip_cert", side_effect=lambda cfg=None: self.ip)):
            p.start()
            self.addCleanup(p.stop)
        self.pport = self.public_on()

    def peer(self, path):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", self.pport), timeout=5) as s, ctx.wrap_socket(s) as t:
            t.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n"
                       % (path, self.pport)).encode("ascii"))
            der, head = t.getpeercert(binary_form=True), t.recv(64)
        return hashlib.sha256(der).hexdigest(), head.split(b"\r\n")[0]

    def test_panel_and_pages_on_the_ip_certificate(self):
        want = hashlib.sha256(ssl.PEM_cert_to_DER_cert(self.ip[0].split("-----END CERTIFICATE-----")[0]
                                                       + "-----END CERTIFICATE-----\n")).hexdigest()
        sha, status = self.peer(self.pb + "/")
        self.assertEqual(sha, want)
        self.assertTrue(status.startswith(b"HTTP/1.") and status.endswith(b" 200 OK"), status)
        self.assertEqual(self.peer("/p/" + "x" * 43)[0], want)
        cfg = conf.load()
        # the name in the links is the very address the certificate is for (cert_host), on the port, with the secret
        self.assertEqual(conf.public_url(cfg), "https://127.0.0.1:%d%s/" % (self.pport, self.pb))
        self.assertEqual(conf.access_doc(cfg), {"public": True, "port": self.pport, "scheme": "https",
                                                "host": cfg["public"]["cert_host"]})


class _Port(PanelCase):
    def setUp(self):
        super().setUp()
        self.pport = self.public_on()
        auth.set_password("правильный-пароль")
        r = self.call("POST", self.pb + "/api/login", {"password": "правильный-пароль"}, port=self.pport, https=True,
                      tunnel=False)
        self.assertEqual(r.status, 200, r)
        self.tok = self.cookie_of(r)

    def pcall(self, method, path, body=None, base=None, **kw):
        return self.call(method, (self.pb if base is None else base) + path, body, port=self.pport, https=True,
                         tunnel=False, cookie=self.tok, **kw)


class ListenerTest(_Port):
    """3: the tab knows its way in; «Сменить секретный адрес» from the port gives the port's new address, not the
    domain's."""

    def test_session_names_the_listener(self):
        self.assertEqual(self.pcall("GET", "/api/session").json().get("listener"), "public")
        self.assertEqual(self.ok("GET", "/api/session").get("listener"), "local")

    def test_rotate_from_the_port_answers_with_the_port(self):
        old = self.pb
        with mock.patch.object(sops.consumers, "_reload_panel", lambda r: self.panel.reload()):
            r = self.pcall("POST", "/api/site/panel-path/rotate", {})
        self.assertEqual(r.status, 200, r)
        new = conf.proxy_base(conf.load())
        self.assertNotEqual(new, old)
        self.assertEqual(r.json()["here"], "https://127.0.0.1:%d%s/" % (self.pport, new))
        self.assertNotIn(new[1:], r.json()["url"])                   # the main address stays masked
        # with the site on the domain the port's tab still gets the port (the backup entry), the domain's — the domain
        from kaskad.site import panel as spanel
        with mock.patch.object(sops, "panel_url", return_value={"kind": "domain", "url": "https://d/x/",
                                                                "fallback": "https://ip:1/x/"}):
            self.assertEqual(spanel._here_url("public"), "https://ip:1/x/")
            self.assertEqual(spanel._here_url("proxy"), "https://d/x/")
            self.assertIsNone(spanel._here_url("local"))


class StreamAndHostTest(_Port):
    """4: the stream of the open mode under the old secret ends at the rotation; a foreign Host on /p/ — the empty
    site, not the panel's 403."""

    def test_rotation_ends_the_stream_of_the_port(self):
        cl = TlsSse(self.pport, self.tok)
        self.addCleanup(cl.close)
        self.assertEqual(cl.status, 200)
        self.assertEqual(cl.next()[0], "live")
        conf.rotate_secret()
        self.panel.reload()
        until_eof(cl, 2.0)

    def test_foreign_host_on_a_page(self):
        for p in ("/p/" + "x" * 43, "/p/" + "x" * 43 + "/sub", "/p/user.js"):
            r = self.call("GET", p, port=self.pport, https=True, tunnel=False, host="evil.example")
            self.assertEqual((r.status, r.body), (404, userpage.NOT_FOUND_BODY), p)
            self.assertNotIn("server", r.headers, p)


class SecretTest(_Port):
    """6: constant-time comparison; a new secret ends the sessions from outside on the server (the tunnel's stay)."""

    def test_compare_digest_on_the_prefix(self):
        seen = []
        real = server.hmac.compare_digest

        def spy(a, b):
            seen.append((a, b))
            return real(a, b)
        with mock.patch.object(server.hmac, "compare_digest", spy):
            self.assertEqual(self.pcall("GET", "/").status, 200)
        self.assertTrue(any(b == self.pb.encode() for _, b in seen), seen)

    def test_new_secret_ends_remote_sessions(self):
        remote = auth.SESSIONS.create("password", "198.51.100.9")
        local = auth.SESSIONS.create("password", "127.0.0.1")
        conf.rotate_secret()
        self.panel.reload()
        self.assertIsNone(auth.SESSIONS.get(remote))
        self.assertIsNotNone(auth.SESSIONS.get(local))
        # a reload with the same secret ends nothing
        again = auth.SESSIONS.create("password", "198.51.100.9")
        self.panel.reload()
        self.assertIsNotNone(auth.SESSIONS.get(again))


class Porcha3375R2Test(unittest.TestCase):
    """Порча: each guard of this round taken away — its test turns red."""

    def red(self, case, name):
        t = case(name)
        t.setUp()
        try:
            with self.assertRaises(AssertionError):
                getattr(t, name)()
        finally:
            t.doCleanups()
            t.tearDown()

    def test_porcha_links_on_the_port(self):
        with mock.patch.object(conf, "pages_domain", return_value=""):
            self.red(PeopleLinksOnTheDomainTest, "test_with_the_site_every_link_is_on_the_domain")

    def test_porcha_links_on_the_domain_without_the_site(self):
        with mock.patch.object(conf, "pages_domain", side_effect=lambda cfg: DOMAIN):
            self.red(PeopleLinksOnTheDomainTest, "test_without_the_site_the_port")

    def test_porcha_site_on_does_not_write(self):
        with mock.patch.object(conf, "write_access", return_value=False):
            self.red(SiteOnWritesTheLinksTest, "test_site_on_then_off")

    def test_porcha_ip_certificate_not_served(self):
        with mock.patch.object(conf, "panel_cert", side_effect=lambda: conf.node_cert()):
            self.red(IpCertificateTest, "test_panel_and_pages_on_the_ip_certificate")

    def test_porcha_no_listener(self):
        from kaskad.panel import api
        real = api.ok
        with mock.patch.object(api, "ok", lambda **kw: real(**dict((k, v) for k, v in kw.items() if k != "listener"))):
            self.red(ListenerTest, "test_session_names_the_listener")

    def test_porcha_rotate_without_here(self):
        from kaskad.site import panel as spanel
        with mock.patch.object(spanel, "_here_url", lambda listener: None):
            self.red(ListenerTest, "test_rotate_from_the_port_answers_with_the_port")

    def test_porcha_stream_keeps_the_old_secret(self):
        with mock.patch.object(server, "_same_public_view", lambda *a: True):
            self.red(StreamAndHostTest, "test_rotation_ends_the_stream_of_the_port")

    def test_porcha_foreign_host_branch(self):
        # the branch «чужой Host вне префикса — пустой сайт» gone: its first look at Host says yes, the later check
        # of the panel answers (403 JSON with the panel's words)
        import threading
        seen = threading.local()
        real = server.Listener.host_allowed

        def first_yes(self, raw):
            n = getattr(seen, "n", 0)
            seen.n = n + 1
            return True if n == 0 else real(self, raw)
        with mock.patch.object(server.Listener, "host_allowed", first_yes):
            self.red(StreamAndHostTest, "test_foreign_host_on_a_page")

    def test_porcha_plain_compare(self):
        with mock.patch.object(server, "_secret_eq", lambda a, b: a == b):
            self.red(SecretTest, "test_compare_digest_on_the_prefix")

    def test_porcha_sessions_live_on(self):
        with mock.patch.object(auth.Sessions, "drop_remote", lambda self: 0):
            self.red(SecretTest, "test_new_secret_ends_remote_sessions")
