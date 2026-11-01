"""The IP certificate of Let's Encrypt (kaskad.ipcert): the ACME flow against a fake CA, the http-01 listener, the
store, the renewal by the timer, what the panel serves, refusals in words — no network, no openssl (one test uses the
real openssl when it is there: the CSR)."""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from kaskad import sysrun
from kaskad.ipcert import acme, crypto as kc, http01, ops, store
from kaskad.ipcert.http01 import Refusal
from kaskad.panel import conf
from tests.helpers import RootCase
from tests.ipcert.fakeca import D, FakeCA, FakeCrypto, cert_pem


def slurp(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


IP = "1.1.1.1"


def public_cfg(ip=IP):
    os.makedirs(os.path.dirname(conf.config_path()), exist_ok=True)
    conf.save({"schema": 1, "public": {"on": True, "port": 41234, "bind": ip, "host": ip, "cert_host": ip}})


class IpCase(RootCase):
    def setUp(self):
        RootCase.setUp(self)
        self.ca = FakeCA(IP)
        self.crypto = FakeCrypto()
        self.runner = sysrun.FakeRunner()
        self.env = mock.patch.dict(os.environ, {"KASKAD_ACME_DIRECTORY": D})
        self.env.start()
        public_cfg()

    def tearDown(self):
        self.env.stop()
        RootCase.tearDown(self)

    def standalone(self):
        r = http01.Standalone(self.runner, port=0, host="127.0.0.1")
        self.ca.responder = r
        return r

    def issue(self, **kw):
        resp = kw.pop("responder", None) or self.standalone()
        return ops.issue(self.runner, crypto=self.crypto, transport=self.ca.transport, responder=resp, **kw)


class FlowTest(IpCase):
    def test_issue_end_to_end_with_standalone_listener(self):
        steps = []
        res = self.issue(progress=lambda i, s, d="": steps.append((i, s)))
        self.assertTrue(res["issued"])
        self.assertEqual(res["via"], "standalone")
        self.assertEqual(self.ca.profile, "shortlived")                 # the IP certificate needs the profile
        self.assertEqual(res["cert"]["state"], "valid")
        self.assertEqual(res["cert"]["ip"], IP)
        self.assertIn(("check", "ok"), steps)
        self.assertIn(("cert", "ok"), steps)
        # the files: key 0600, current is a link, meta records the try
        self.assertTrue(os.path.islink(store.current_dir()))
        self.assertEqual(os.stat(store.privkey_path()).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(store.base_dir()).st_mode & 0o777, 0o700)
        self.assertTrue(store.meta()["last_try"]["ok"])
        self.assertEqual(store.meta()["profile"], "shortlived")
        # the CSR: empty subject, SAN iPAddress of the node
        self.assertIn(b"\x87\x04" + bytes([1, 1, 1, 1]), self.ca.csr)
        # the listener is gone after the check
        self.assertIsNone(self.ca.responder.srv)

    def test_valid_one_is_kept_and_force_reissues(self):
        self.issue()
        res = self.issue()
        self.assertFalse(res["issued"])
        self.assertTrue(res["kept"])
        self.assertEqual(self.ca.issued, 1)
        res = self.issue(force=True)
        self.assertTrue(res["issued"])
        self.assertEqual(self.ca.issued, 2)
        # two issues are kept on disk at most
        self.issue(force=True)
        dirs = [n for n in os.listdir(store.base_dir()) if n[:1].isdigit()]
        self.assertLessEqual(len(dirs), store.KEEP)

    def test_account_key_made_once(self):
        self.issue()
        self.issue(force=True)
        akeys = [k for k in self.crypto.keys if "account" in k]
        self.assertEqual(len(akeys), 1)

    def test_webroot_writes_the_token_and_cleans_it(self):
        web = os.path.join(self.root, "acme")
        self.ca.webroot = web
        res = self.issue(responder=http01.Webroot(web))
        self.assertTrue(res["issued"])
        self.assertEqual(res["via"], "webroot")
        self.assertEqual(os.listdir(os.path.join(web, ".well-known", "acme-challenge")), [])


class RefusalTest(IpCase):
    def test_rate_limit_keeps_retry_after_and_waits(self):
        self.ca.fail_order = (429, {"type": acme.PROBLEM + "rateLimited", "detail": "too many certificates"})
        with self.assertRaises(Refusal) as cm:
            self.issue()
        e = cm.exception
        self.assertEqual(e.reason, "limit")
        self.assertTrue(e.retry_after)
        self.assertIn("лимит", e.text)
        self.assertEqual(store.meta()["last_try"]["reason"], "limit")
        # the next try before retry_after does not even ask the CA
        calls = len(self.ca.calls)
        with self.assertRaises(Refusal):
            self.issue()
        self.assertEqual(len(self.ca.calls), calls)

    def test_challenge_failure_says_port_80(self):
        self.ca.fail_check = {"type": acme.PROBLEM + "connection", "detail": "Timeout during connect"}
        with self.assertRaises(Refusal) as cm:
            self.issue()
        self.assertEqual(cm.exception.reason, "challenge")
        self.assertIn("порт 80", cm.exception.text)
        self.assertFalse(os.path.exists(store.fullchain_path()))

    def test_no_network(self):
        self.ca.net = True
        with self.assertRaises(Refusal) as cm:
            self.issue()
        self.assertEqual(cm.exception.reason, "net")

    def test_no_profile(self):
        def dir_without_shortlived(method, url, body=None, headers=None, timeout=None):
            if url == D:
                return 200, {}, json.dumps({"newNonce": "n", "newAccount": "a", "newOrder": "o",
                                            "meta": {"profiles": {"classic": ""}}}).encode()
            raise AssertionError(url)
        with self.assertRaises(Refusal) as cm:
            ops.issue(self.runner, crypto=self.crypto, transport=dir_without_shortlived, responder=self.standalone())
        self.assertEqual(cm.exception.reason, "profile")

    def test_private_address_refused_before_anything(self):
        with self.assertRaises(Refusal) as cm:
            self.issue(ip="10.0.0.5")
        self.assertEqual(cm.exception.reason, "private")
        self.assertEqual(self.ca.calls, [])

    def test_port80_taken_by_foreign_program(self):
        with mock.patch.object(http01, "holders", return_value=["nginx (pid 77) слушает 0.0.0.0:80"]), \
                mock.patch("kaskad.site.nginx.ours", return_value=False):
            with self.assertRaises(Refusal) as cm:
                http01.choose(self.runner, {"engine": "nft"})
        self.assertEqual(cm.exception.reason, "port80")
        self.assertIn("nginx", cm.exception.text)
        self.assertEqual(cm.exception.code, 3)

    def test_site_nginx_is_used_as_webroot(self):
        with mock.patch("kaskad.site.nginx.ours", return_value=True):
            r = http01.choose(self.runner, {"engine": "nft"})
        self.assertEqual(r.kind, "webroot")

    def test_problem_document_texts(self):
        e = acme.problem_error(400, {}, json.dumps({"type": acme.PROBLEM + "rejectedIdentifier",
                                                    "detail": "IP not allowed"}).encode())
        self.assertEqual(e.reason, "unsupported")
        e = acme.problem_error(429, {}, json.dumps({"detail": "retry after 2026-10-09 12:00:00 UTC"}).encode())
        self.assertEqual((e.reason, e.retry_after), ("limit", "2026-10-09T12:00:00Z"))


class RenewalTest(IpCase):
    def test_tick_not_needed_without_open_panel(self):
        conf.save({"schema": 1, "public": {"on": False}})
        self.assertEqual(ops.tick(self.runner)["why"], "not_needed")

    def test_tick_renews_when_less_than_three_days_left(self):
        self.issue()
        with mock.patch.object(ops, "issue", wraps=None) as iss:
            iss.return_value = {"ok": True, "issued": True}
            self.assertEqual(ops.tick(self.runner)["action"], "none")       # 160 h left: nothing
            iss.assert_not_called()
            with mock.patch.object(store, "_now", return_value=time.time() + 100 * 3600):
                res = ops.tick(self.runner)                                 # 60 h left: renew
            self.assertEqual(res["action"], "renewed")
            iss.assert_called_once()

    def test_tick_failure_is_reported_not_raised(self):
        with mock.patch.object(ops, "issue", side_effect=Refusal("challenge", "порт 80 закрыт")):
            res = ops.tick(self.runner)
        self.assertEqual((res["action"], res["reason"]), ("failed", "challenge"))

    def test_timer_every_12_hours_installed_after_issue(self):
        self.runner.on(["systemctl"], (0, "", ""))
        self.issue()
        from kaskad.gw import sys as gsys
        timer = slurp(gsys.unit_path(ops.TIMER))
        self.assertIn("OnCalendar=*-*-* 00/12:00:00", timer)
        self.assertIn("ipcert tick --quiet", slurp(gsys.unit_path(ops.UNIT)))


class PanelServesTest(IpCase):
    def test_panel_serves_ip_certificate_then_falls_back(self):
        self.assertIsNone(conf.ip_cert())
        self.issue()
        crt, key = conf.panel_cert()
        self.assertIn("BEGIN CERTIFICATE", crt)
        self.assertEqual(crt, slurp(store.fullchain_path()))
        # expired (or about to) — the node certificate again
        with mock.patch.object(store, "_now", return_value=time.time() + 160 * 3600):
            self.assertIsNone(conf.ip_cert())

    def test_staging_and_other_address_not_served(self):
        self.ca.issuer = "(STAGING) Pretend Pear X1"
        self.issue(staging=True)
        self.assertTrue(store.status(IP)["staging"])
        self.assertIsNone(conf.ip_cert())                                     # browsers do not trust staging
        self.ca.issuer = "YE1"
        self.issue()                                                          # a real one replaces it
        self.assertIsNotNone(conf.ip_cert())
        public_cfg("198.51.100.7")
        self.assertIsNone(conf.ip_cert())                                     # another address never matches

    def test_status_has_no_secret(self):
        self.issue()
        doc = ops.status(self.runner)
        text = json.dumps(doc)
        self.assertNotIn("PRIVATE", text)
        self.assertEqual(doc["state"], "valid")
        self.assertTrue(doc["serving"])


class CsrTest(unittest.TestCase):
    def test_der_helpers(self):
        self.assertEqual(kc.oid("1.2.840.113549.1.1.11").hex(), "06092a864886f70d01010b")
        self.assertEqual(kc.integer(128), b"\x02\x02\x00\x80")
        jwk = kc.rsa_jwk(__import__("tests.ipcert.fakeca", fromlist=["rsa_spki"]).rsa_spki())
        self.assertEqual(jwk["e"], "AQAB")
        self.assertEqual(len(kc.thumbprint(jwk)), 43)

    @unittest.skipUnless(shutil.which("openssl"), "нет openssl")
    def test_real_openssl_csr_verifies(self):
        work = tempfile.mkdtemp(prefix="kaskad-csr-")
        try:
            c = kc.Openssl(sysrun.SystemRunner())
            key = c.gen_ec(os.path.join(work, "k.pem"))
            self.assertEqual(os.stat(key).st_mode & 0o777, 0o600)
            der = kc.csr(c, key, IP, work)
            p = os.path.join(work, "csr.der")
            with open(p, "wb") as f:
                f.write(der)
            out = subprocess.run(["openssl", "req", "-inform", "DER", "-in", p, "-noout", "-verify", "-text"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            self.assertEqual(out.returncode, 0, out.stdout)
            self.assertIn("IP Address:%s" % IP, out.stdout)
            # the account key and a JWS signature
            ak = c.gen_rsa(os.path.join(work, "a.pem"))
            jwk = kc.rsa_jwk(c.public_der(ak))
            self.assertEqual(jwk["kty"], "RSA")
            self.assertEqual(len(c.sign(ak, data="abc.def")), 256)
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
