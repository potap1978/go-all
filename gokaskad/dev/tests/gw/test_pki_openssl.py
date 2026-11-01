"""pki.py with the REAL openssl (skipped where there is none): a CA constrained to one host signs a server
certificate valid under the constraint and one for another host that every verifier refuses; the CRL of
``openssl ca -gencrl`` revokes by the serial ``serial_hex`` reads; the pure Python readers agree with openssl."""

import os
import shutil
import tempfile
import time
import unittest

from kaskad.gw.inbounds import pki
from kaskad.sysrun import SystemRunner

OPENSSL = shutil.which("openssl")


@unittest.skipUnless(OPENSSL, "openssl is not installed")
class RealOpensslTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="kaskad-pki-")
        os.chmod(self.d, 0o700)
        self.runner = SystemRunner()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def put(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def verify(self, ca, cert, crl=None, purpose=None):
        argv = ["openssl", "verify"] + (["-crl_check"] if crl else []) + (["-purpose", purpose] if purpose else [])
        argv += ["-CAfile", self.put("trust.pem", ca + (crl or "")), self.put("leaf.pem", cert)]
        res = self.runner.run(argv)
        return res.ok, res.out + res.err

    def refused(self, ca, cert, why="permitted subtree violation", purpose=None):
        ok, out = self.verify(ca, cert, purpose=purpose)
        self.assertFalse(ok, "a CA scoped to one host must not vouch for this certificate")
        self.assertIn(why, out)

    def test_ip_scope(self):
        key, ca = pki.new_ca(self.runner, self.d, "Kaskad IKEv2 CA 203.0.113.10 a1b2c3", scope="203.0.113.10",
                             eku="ike")
        self.assertEqual(pki.name_constraints(ca), ["IP:203.0.113.10/255.255.255.255", "DNS:203.0.113.10"])
        self.assertEqual(pki.subject_cn(ca), "Kaskad IKEv2 CA 203.0.113.10 a1b2c3")
        _, srv = pki.issue(self.runner, self.d, key, ca, "203.0.113.10", "ike", san="203.0.113.10")
        for purpose in (None, "sslserver"):
            ok, out = self.verify(ca, srv, purpose=purpose)
            self.assertTrue(ok, out)
        self.assertIsNone(pki.name_constraints(srv))
        _, other = pki.issue(self.runner, self.d, key, ca, "198.51.100.7", "ike", san="198.51.100.7")
        self.refused(ca, other)
        _, site = pki.issue(self.runner, self.d, key, ca, "bank.example.com", "server", san="bank.example.com")
        self.refused(ca, site)                                     # IP:<host> alone would let this pass
        _, cn_only = pki.issue(self.runner, self.d, key, ca, "bank.example.com", "server")
        self.refused(ca, cn_only)
        _, client = pki.issue(self.runner, self.d, key, ca, "user", "client")
        self.refused(ca, client, why="unsuitable certificate purpose", purpose="sslclient")   # the EKU of the CA

    def test_dns_scope(self):
        key, ca = pki.new_ca(self.runner, self.d, "Kaskad IKEv2 CA vpn.example.org a1b2c3", scope="vpn.example.org",
                             eku="ike")
        self.assertEqual(pki.name_constraints(ca), ["DNS:vpn.example.org", "IP:0.0.0.0/255.255.255.255"])
        _, srv = pki.issue(self.runner, self.d, key, ca, "vpn.example.org", "ike", san="vpn.example.org")
        self.assertTrue(self.verify(ca, srv, purpose="sslserver")[0])
        _, sub = pki.issue(self.runner, self.d, key, ca, "a.vpn.example.org", "ike", san="a.vpn.example.org")
        self.assertTrue(self.verify(ca, sub)[0])                   # the owner's own subdomains only
        _, foreign = pki.issue(self.runner, self.d, key, ca, "bank.example.com", "server", san="bank.example.com")
        self.refused(ca, foreign)
        _, by_ip = pki.issue(self.runner, self.d, key, ca, "198.51.100.7", "server", san="198.51.100.7")
        self.refused(ca, by_ip)                                    # DNS:<name> alone would let this pass

    def test_ca_without_scope_and_the_crl(self):
        key, ca = pki.new_ca(self.runner, self.d, "Kaskad OpenVPN CA")
        self.assertIsNone(pki.name_constraints(ca))
        _, a = pki.issue(self.runner, self.d, key, ca, "d-11111111", "client")
        _, b = pki.issue(self.runner, self.d, key, ca, "d-22222222", "client")
        res = self.runner.run(["openssl", "x509", "-noout", "-serial", "-in", self.put("a.pem", a)])
        self.assertEqual(res.out.strip(), "serial=" + pki.serial_hex(a))
        crl = pki.crl(self.runner, self.d, key, ca, [(pki.serial_hex(a), time.time() - 60)])
        ok, out = self.verify(ca, a, crl)
        self.assertFalse(ok)
        self.assertIn("certificate revoked", out)
        self.assertTrue(self.verify(ca, b, crl)[0])
        empty = pki.crl(self.runner, self.d, key, ca, [])
        self.assertTrue(self.verify(ca, a, empty)[0])
        res = self.runner.run(["openssl", "crl", "-noout", "-nextupdate", "-in", self.put("crl.pem", empty)])
        self.assertTrue(res.ok, res.err)
        year = int(res.out.strip().split()[-2])
        self.assertGreaterEqual(year, time.gmtime().tm_year + 9)                  # as long as the CA
        self.assertEqual(sorted(os.listdir(self.d)), ["a.pem", "crl.pem", "leaf.pem", "trust.pem"])  # no key left
