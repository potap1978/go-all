"""SSTP and OpenConnect on the certificate of the domain only by ``site use``: their configs name the copy of the
domain's certificate, the issued settings name the domain and carry no fingerprint, the renewal is a reload, and the
devices with those inbounds get the F-2 mark «нужны новые настройки» (``affected``; ``--dry-run`` counts them)."""

import os
import shutil
from unittest import mock

from kaskad import state
from kaskad.errors import KaskadError
from kaskad.site import certinfo, consumers, model, ops
from tests.gw.ppp_case import INBOUNDS, PppCase, device, gw_state
from tests.gw.tunnel_case import GwCase as TunnelCase
from tests.site.sitecase import DOMAIN, NOW, SiteCase, fixture, put_copy_pair

CERT = "/etc/kaskad/site/tls/current/fullchain.pem"
KEY = "/etc/kaskad/site/tls/current/privkey.pem"


def put_copy(name="le"):
    put_copy_pair(name)


def on_domain(st, *kinds):
    st["site"] = {"domain": DOMAIN, "mode": "domain", "cert": "letsencrypt",
                  "use": dict((k, k in kinds) for k in model.USE_KINDS)}
    return st


class SstpConfigTest(PppCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(certinfo, "_now", lambda: NOW)
        p.start()
        self.addCleanup(p.stop)
        self.st = gw_state(INBOUNDS, [device("d-win", "Ноутбук", "windows", ["sstp"], login="noutbuk-3c4d",
                                             kind="laptop"),
                                      device("d-lin", "Сервер", "linux", ["sstp"], login="server-3e3e", kind="pc")])
        for d in self.st["gateway"]["devices"]:
            self.sec["dev.%s.password" % d["id"]] = "PassWord%s1" % d["id"][2:]
        self.sstp = self.adapter("sstp")
        self.sstp.prepare(self.st, self.sec, self.runner)

    def conf(self):
        return self.prod(self.gw.accel.conf_text(self.st, ["sstp"]))

    def test_paths_of_the_certificate(self):
        self.assertIn("ssl-pemfile=/etc/kaskad/gw/tls/server.crt\nssl-keyfile=/etc/kaskad/gw/tls/server.key\n",
                      self.conf())
        on_domain(self.st)                                             # a domain without «use»: self-signed
        put_copy()
        self.assertIn("ssl-pemfile=/etc/kaskad/gw/tls/server.crt", self.conf())
        on_domain(self.st, "sstp")
        self.assertIn("ssl-pemfile=%s\nssl-keyfile=%s\n" % (CERT, KEY), self.conf())
        os.unlink(model.fullchain_path())                              # no copy (the domain cleared): self-signed
        self.assertIn("ssl-pemfile=/etc/kaskad/gw/tls/server.crt", self.conf())

    def issue(self, dev_id, os_name=None):
        dev = [d for d in self.st["gateway"]["devices"] if d["id"] == dev_id][0]
        return self.sstp.issue(self.st, self.sec, dev, os_name)

    def test_issue_names_the_domain_without_a_fingerprint(self):
        before = self.issue("d-win")
        self.assertIn("203.0.113.10:4433", before["body"])
        self.assertIn("Import-Certificate", before["body"])
        on_domain(self.st, "sstp")
        put_copy()
        res = self.issue("d-win")
        body = res["body"]
        self.assertIn("vpn.example.com:4433", body)
        self.assertNotIn("Import-Certificate", body)
        self.assertNotIn("NoCertRevocationCheck", body)
        fields = [i for i in res["items"] if i["format"] == "fields"][0]
        self.assertEqual([r[0] for r in fields["body"]], ["Сервер", "Логин", "Пароль"])
        self.assertEqual(fields["body"][0][1], "vpn.example.com:4433")
        self.assertIn("Let's Encrypt", " ".join(fields["notes"]))
        lin = self.issue("d-lin")
        self.assertNotIn("kaskad-sstp.crt", [i["filename"] for i in lin["items"]])
        self.assertIn("--ca-cert /etc/ssl/certs/ca-certificates.crt vpn.example.com:4433",
                      [i for i in lin["items"] if i["title"] == "SSTP — команда для Linux"][0]["body"])


class OcservConfigTest(TunnelCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(certinfo, "_now", lambda: NOW)
        p.start()
        self.addCleanup(p.stop)
        self.st = gw_state({"ocserv": {"enabled": True, "port": 4443}},
                           [device("d-mac", "Макбук", "macos", ["ocserv"], kind="laptop"),
                            device("d-lin", "Linux", "linux", ["ocserv"], kind="pc")])
        self.oc = self.adapter("ocserv")
        self.oc.install(self.runner)
        self.oc.apply(self.st, self.sec, self.runner)
        self.node.sysd.calls[:] = []

    def conf(self):
        return self.prod(self.oc.conf_text(self.st))

    def test_switching_is_a_restart_and_back(self):
        self.assertIn("server-cert = /etc/kaskad/gw/ocserv/server-cert.pem\n", self.conf())
        on_domain(self.st, "ocserv")
        put_copy()
        self.assertIn("server-cert = %s\nserver-key = %s\n" % (CERT, KEY), self.conf())
        res = self.oc.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "restarted")                   # the paths are not reloadable
        res = self.oc.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "unchanged")
        self.st["site"]["use"]["ocserv"] = False
        self.assertEqual(self.oc.apply(self.st, self.sec, self.runner)["action"], "restarted")

    def test_issue_names_the_domain_without_a_pin(self):
        dev = self.st["gateway"]["devices"][1]
        before = self.oc.issue(self.st, self.sec, dev, "linux")
        self.assertIn("--servercert pin-sha256:", before["body"])
        on_domain(self.st, "ocserv")
        put_copy()
        res = self.oc.issue(self.st, self.sec, dev, "linux")
        self.assertNotIn("pin-sha256", str(res))
        self.assertIn("sudo openconnect --protocol=anyconnect --user=%s https://vpn.example.com:4443" % dev["login"],
                      res["body"])
        fields = [i for i in res["items"] if i["format"] == "fields"][0]
        self.assertEqual([r[0] for r in fields["body"]], ["Сервер", "Логин", "Пароль"])


class UseTest(SiteCase):
    def setUp(self):
        super().setUp()
        st = state.load()
        st["gateway"] = {"enabled": False, "host": "203.0.113.10",
                         "users": [{"id": "u-1", "name": "Мама", "route": None, "enabled": True, "added": "x"}],
                         "devices": [device("d-win", "Ноутбук", "windows", ["sstp", "ocserv"]),
                                     device("d-and", "Пиксель", "android", ["sstp"]),
                                     device("d-off", "Выключенный", "windows", ["sstp"], enabled=False),
                                     device("d-wg", "Только WireGuard", "ios", ["wg"])],
                         "inbounds": {"sstp": {"enabled": True, "port": 4433}}}
        state.save(st)

    def test_needs_a_live_certificate(self):
        with self.assertRaises(KaskadError):
            ops.use(self.w.runner, "sstp", True)                       # no domain
        ops.set_domain(self.w.runner, DOMAIN)
        with self.assertRaises(KaskadError) as c:
            ops.use(self.w.runner, "sstp", True)                       # no certificate
        self.assertIn("cert issue", str(c.exception))
        self.put_copy("other")
        with self.assertRaises(KaskadError):
            ops.use(self.w.runner, "sstp", True)                       # a certificate of another name
        ops.use(self.w.runner, "sstp", False)                          # back is always allowed

    def test_dry_run_counts_and_changes_nothing(self):
        self.with_domain(issued="le")
        doc = ops.status(self.w.runner)
        self.assertEqual(doc["use_impact"], {"sstp": 2, "ocserv": 1})
        self.assertEqual(doc["use_impact_total"], 0)
        res = ops.use(self.w.runner, "sstp", True, dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["affected"], [{"device": "d-win", "name": "Ноутбук", "kind": "sstp", "user_name": "Мама"},
                                           {"device": "d-and", "name": "Пиксель", "kind": "sstp", "user_name": "Мама"}])
        self.assertEqual(res["use"], {"sstp": True, "ocserv": False})
        self.assertFalse(self.site()["use"]["sstp"])
        self.assertIn("Выдайте их заново", res["text"])

    def test_on_and_off_mark_the_devices(self):
        self.with_domain(issued="le")
        res = ops.use(self.w.runner, "sstp", True)
        self.assertEqual(res["use"], {"sstp": True, "ocserv": False})
        self.assertEqual([a["device"] for a in res["affected"]], ["d-win", "d-and"])
        self.assertIn("SSTP: адрес сервера в настройках меняется на vpn.example.com", res["text"])
        self.assertTrue(self.site()["use"]["sstp"])
        self.assertTrue(ops.status(self.w.runner)["use"]["sstp"])
        self.assertFalse(any("не установлен" in w for w in res["warnings"]))
        again = ops.use(self.w.runner, "sstp", True)                   # nothing changes: nobody to mark
        self.assertEqual(again["affected"], [])
        self.assertIn("SITE: SSTP переведён на сертификат домена", self.log_text())
        res = ops.use(self.w.runner, "sstp", False)
        self.assertEqual([a["device"] for a in res["affected"]], ["d-win", "d-and"])
        self.assertFalse(self.site()["use"]["sstp"])

    def test_not_installed_warns(self):
        self.with_domain(issued="le")
        res = ops.use(self.w.runner, "ocserv", True)
        self.assertTrue(any("не установлен" in w for w in res["warnings"]))

    def test_clearing_the_domain_marks_and_resets(self):
        self.with_domain(issued="le")
        ops.use(self.w.runner, "sstp", True)
        ops.use(self.w.runner, "ocserv", True)
        self.assertEqual(ops.status(self.w.runner)["use_impact_total"], 2)
        res = ops.clear(self.w.runner)
        self.assertEqual(sorted((a["device"], a["kind"]) for a in res["affected"]),
                         [("d-and", "sstp"), ("d-win", "ocserv"), ("d-win", "sstp")])
        self.assertIn("SSTP, OpenConnect", res["text"])
        self.assertEqual(self.site()["use"], {"sstp": False, "ocserv": False})

    def test_renewal_reloads_the_inbounds_on_the_domain(self):
        from kaskad.gw.inbounds import accel, ocserv
        self.with_domain(issued="le")
        ops.use(self.w.runner, "sstp", True)
        ops.use(self.w.runner, "ocserv", True)
        for u in (accel.UNIT, ocserv.UNIT):
            self.write("/etc/systemd/system/" + u, "[Service]\n", 0o644)
            self.w.sysd.units[u] = {"enabled": True, "active": True}
        self.put_live("le2")
        res = consumers.cert_installed(self.w.runner, lineage=model.live_dir(DOMAIN))
        self.assertEqual([(c["name"], c["action"]) for c in res["consumers"]], [("sstp", "reload"), ("ocserv", "reload")])
        self.assertEqual(self.w.sysd.restarts.get(accel.UNIT), 1)        # the fake counts a reload as a «restart»
        self.assertIn(["systemctl", "reload", ocserv.UNIT], [a for a, _ in self.w.runner.calls])
        self.assertNotIn(["systemctl", "restart", ocserv.UNIT], [a for a, _ in self.w.runner.calls])
        with open(model.fullchain_path()) as f:
            self.assertEqual(f.read(), fixture("le2.pem"))
