"""The panel (open mode) and the pages of people /p/ take the certificate of the domain by themselves: the running
panel serves it, its addresses become https://<домен>:<порт>/…, and back to the node certificate without a domain.
And the routes /api/site of the contract (admin, X-Kaskad-Admin, the JSON of the CLI)."""

import contextlib
import io
import json
import os
import socket
import ssl
from unittest import mock

from kaskad import cli, state
from kaskad.gw.inbounds import pki
from kaskad.panel import conf, server, userpage
from kaskad.panel import cli as panel_cli
from kaskad.site import consumers, dns, model, ops
from tests.panel.panelcase import PanelCase
from tests.site.sitecase import DOMAIN, SiteFakes, fixture


def peer_der(port):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname=DOMAIN) as s:
            return s.getpeercert(binary_form=True)


class PanelTakesTheDomainTest(SiteFakes, PanelCase):
    def setUp(self):
        super().setUp()
        self.site_fakes()
        p = mock.patch.object(server, "TLS_CHECK_EVERY", 0.0)
        p.start()
        self.addCleanup(p.stop)

    def access(self):
        with open(conf.access_path()) as f:
            return json.load(f)

    def test_open_mode_and_pages_move_to_the_domain_and_back(self):
        port = self.public_on()
        node_der = peer_der(port)
        self.assertEqual(conf.public_origin(conf.load()), "https://127.0.0.1:%d/" % port)
        ops.set_domain(self.w.runner, DOMAIN)
        self.assertEqual(peer_der(port), node_der)                     # a domain without a certificate: nothing moves
        self.put_live("le")
        res = consumers.cert_installed(self.w.runner, lineage=model.live_dir(DOMAIN))
        self.assertEqual([c["name"] for c in res["consumers"]], ["panel", "pages"])
        # the running panel serves the certificate of the domain
        self.assertEqual(peer_der(port), pki.pem_der(fixture("le.pem")))
        self.assertEqual(conf.public_origin(conf.load()), "https://vpn.example.com:%d/" % port)
        self.assertTrue(self.wait(lambda: self.access()["host"] == DOMAIN))
        self.assertEqual(self.access(), {"public": True, "port": port, "scheme": "https", "host": DOMAIN})
        self.assertEqual(userpage._link("u-x", "T" * 43)[1], "https://vpn.example.com:%d/p/%s" % (port, "T" * 43))
        # the domain is a name the open mode answers to
        self.assertEqual(self.call("GET", self.pb + "/api/session", port=port, https=True,
                                   host="vpn.example.com:%d" % port).status, 200)
        self.assertEqual(self.call("GET", self.pb + "/api/session", port=port, https=True, host="evil.example").status, 403)
        doc = ops.status(self.w.runner)
        self.assertEqual(doc["use"], {"panel": True, "pages": True, "sstp": False, "ocserv": False})
        self.assertEqual(conf.fingerprint(), pki.sha256_fingerprint(fixture("le.pem")))
        # renewed: the new one without a restart
        self.put_live("le2")
        consumers.cert_installed(self.w.runner)
        self.assertEqual(peer_der(port), pki.pem_der(fixture("le2.pem")))
        # the domain cleared: the node certificate again, the address of the node
        ops.clear(self.w.runner)
        self.assertEqual(peer_der(port), node_der)
        self.assertEqual(conf.public_origin(conf.load()), "https://127.0.0.1:%d/" % port)
        self.assertTrue(self.wait(lambda: self.access()["host"] == "127.0.0.1"))

    def test_an_expired_or_foreign_copy_is_not_served(self):
        port = self.public_on()
        node_der = peer_der(port)
        ops.set_domain(self.w.runner, DOMAIN)
        self.put_copy("other")
        self.assertEqual(peer_der(port), node_der)
        self.assertEqual(conf.public_origin(conf.load()), "https://127.0.0.1:%d/" % port)
        self.put_copy("le")
        self.assertEqual(peer_der(port), pki.pem_der(fixture("le.pem")))
        self.clock += 200 * 86400
        self.assertEqual(peer_der(port), node_der)

    def test_port_443_is_not_printed(self):
        ops.set_domain(self.w.runner, DOMAIN)
        self.put_copy("le")
        cfg = conf.load()
        cfg["public"] = {"on": True, "port": 443, "bind": "127.0.0.1", "host": "127.0.0.1", "cert_host": ""}
        self.assertEqual(conf.public_origin(cfg), "https://vpn.example.com/")
        self.assertEqual(userpage._format_url(conf.access_doc(cfg), DOMAIN, "T"), "https://vpn.example.com/p/T")

    def test_panel_status_names_the_lets_encrypt_certificate(self):
        self.public_on()
        ops.set_domain(self.w.runner, DOMAIN)
        self.put_live("le")
        consumers.cert_installed(self.w.runner, lineage=model.live_dir(DOMAIN))
        self.write("/etc/systemd/system/" + conf.UNIT, panel_cli.unit_text(), 0o644)
        self.w.sysd.units[conf.UNIT] = {"enabled": True, "active": True}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["panel", "status"]), 0)
        self.assertEqual(err.getvalue(), "")
        self.assertIn("Сертификат Let's Encrypt для vpn.example.com, до 30.12.2026", out.getvalue())
        self.assertNotIn("Отпечаток сертификата SHA-256", out.getvalue())


class SiteApiTest(SiteFakes, PanelCase):
    def setUp(self):
        super().setUp()
        self.site_fakes()

    def test_status(self):
        doc = self.ok("GET", "/api/site")
        self.assertEqual(doc, ops.status(self.w.runner))
        self.assertEqual(doc["mode"], "off")

    def test_admin_only_and_the_write_guard(self):
        self.assertEqual(self.call("GET", "/api/site", tunnel=False).status, 401)
        self.assertEqual(self.call("PUT", "/api/site/domain", {"domain": DOMAIN}, csrf=False).status, 403)
        self.assertEqual(self.call("PUT", "/api/site/domain", {"domain": DOMAIN}, tunnel=False).status, 401)
        self.assertNotIn("site", state.load())

    def test_domain_dns_email_and_clear(self):
        doc = self.ok("PUT", "/api/site/domain", {"domain": "VPN.Example.com"})
        self.assertEqual((doc["ok"], doc["site"]["domain"], doc["dns"]["state"], doc["affected"]),
                         (True, DOMAIN, "ok", []))
        self.err(400, "PUT", "/api/site/domain", {"domain": "203.0.113.10"})
        self.err(400, "PUT", "/api/site/domain", {})
        self.answers[dns.A] = ("ok", [])
        doc = self.ok("POST", "/api/site/dns", {})
        self.assertEqual(doc["state"], "none")
        self.err(400, "POST", "/api/site/dns", {"wait": 301})
        self.err(400, "POST", "/api/site/dns", {"wait": True})
        self.assertEqual(self.ok("PUT", "/api/site/email", {"email": "admin@example.com"})["email"], "admin@example.com")
        self.assertEqual(self.ok("PUT", "/api/site/email", {"email": ""})["email"], "")
        self.err(400, "PUT", "/api/site/email", {"email": "nonsense"})
        doc = self.ok("DELETE", "/api/site/domain")
        self.assertIsNone(doc["site"]["domain"])
        self.assertEqual(doc["affected"], [])

    def test_issue_refusal_is_409_with_the_reason(self):
        self.ok("PUT", "/api/site/domain", {"domain": DOMAIN})
        self.certbot_present()
        self.certbot.mode = "limit"
        r = self.call("POST", "/api/site/cert", {})
        self.assertEqual(r.status, 409)
        doc = r.json()
        self.assertEqual((doc["ok"], doc["reason"], doc["retry_after"]), (False, "limit", "2026-10-08T12:00:00Z"))
        self.assertEqual(doc["error"], doc["text"])
        self.err(400, "POST", "/api/site/cert", {"staging": "yes"})

    def test_issue_renew_and_use(self):
        self.ok("PUT", "/api/site/domain", {"domain": DOMAIN})
        doc = self.ok("POST", "/api/site/cert", {"staging": False})
        self.assertEqual((doc["ok"], doc["issued"], doc["kept"], doc["cert"]["kind"]), (True, True, False, "letsencrypt"))
        self.assertTrue(os.path.exists(model.fullchain_path()))
        doc = self.ok("POST", "/api/site/cert/renew")
        self.assertEqual((doc["ok"], doc["renewed"]), (True, False))
        doc = self.ok("PUT", "/api/site/use", {"kind": "sstp", "on": True, "dry_run": True})
        self.assertEqual((doc["dry_run"], doc["use"]["sstp"], doc["affected"]), (True, True, []))
        self.assertFalse(model.peek(state.load())["use"]["sstp"])
        doc = self.ok("PUT", "/api/site/use", {"kind": "ocserv", "on": True})
        self.assertTrue(doc["use"]["ocserv"])
        self.assertTrue(model.peek(state.load())["use"]["ocserv"])
        self.err(400, "PUT", "/api/site/use", {"kind": "ikev2", "on": True})
        self.err(400, "PUT", "/api/site/use", {"kind": "sstp", "on": "on"})

    def test_use_without_a_certificate_is_refused(self):
        self.ok("PUT", "/api/site/domain", {"domain": DOMAIN})
        doc = self.err(409, "PUT", "/api/site/use", {"kind": "sstp", "on": True})
        self.assertIn("cert issue", doc["error"])
