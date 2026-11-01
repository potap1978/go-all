"""The name of the domain (IDNA, lengths, not an IP, not «*») and what a certificate says about itself (pure Python)."""

import unittest
from unittest import mock

from kaskad.errors import USAGE, KaskadError
from kaskad.site import certinfo, model
from tests.site.sitecase import NOW, fixture


class DomainNameTest(unittest.TestCase):
    def ok(self, value, want):
        self.assertEqual(model.parse_domain(value), want)

    def bad(self, value, part):
        with self.assertRaises(KaskadError) as c:
            model.parse_domain(value)
        self.assertEqual(c.exception.code, USAGE)
        self.assertIn(part, str(c.exception))

    def test_plain_names(self):
        self.ok("vpn.example.com", "vpn.example.com")
        self.ok("  VPN.Example.COM.  ", "vpn.example.com")
        self.ok("https://vpn.example.com/path", "vpn.example.com")
        self.ok("a-b.c-d.example.org", "a-b.c-d.example.org")
        self.ok("x" * 63 + ".example.com", "x" * 63 + ".example.com")

    def test_idna_to_punycode_and_back(self):
        self.ok("впн.пример.рф", "xn--b1awf.xn--e1afmkfd.xn--p1ai")
        self.ok("ВПН.Пример.РФ", "xn--b1awf.xn--e1afmkfd.xn--p1ai")
        self.assertEqual(model.unicode("xn--b1awf.xn--e1afmkfd.xn--p1ai"), "впн.пример.рф")
        self.assertEqual(model.unicode("vpn.example.com"), "vpn.example.com")

    def test_refused(self):
        self.bad("", "не указан")
        self.bad("203.0.113.10", "IP-адрес")
        self.bad("2001:db8::1", "IP-адрес")
        self.bad("*.example.com", "звёздочкой")
        self.bad("localhost", "точка")
        self.bad("vpn..example.com", "пустая часть")
        self.bad("x" * 64 + ".example.com", "длиннее 63")
        self.bad("-vpn.example.com", "недопустимые знаки")
        self.bad("vpn-.example.com", "недопустимые знаки")
        self.bad("my_vpn.example.com", "недопустимые знаки")
        self.bad("vpn.example.123", "из одних цифр")
        self.bad(".".join(["a" * 60] * 5), "253")

    def test_email(self):
        self.assertEqual(model.parse_email(" admin@example.com "), "admin@example.com")
        for bad in ("admin", "admin@", "@example.com", "a@b", "a..b@example.com", "a b@example.com"):
            with self.assertRaises(KaskadError):
                model.parse_email(bad)


class CertInfoTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(certinfo, "_now", lambda: NOW)
        p.start()
        self.addCleanup(p.stop)

    def test_lets_encrypt_leaf(self):
        info = certinfo.parse(fixture("le.pem"))
        self.assertEqual(info["names"], ["vpn.example.com"])
        self.assertEqual(info["not_after"], "2026-12-30T10:00:00Z")
        self.assertEqual(info["issuer"], "R11")
        self.assertFalse(info["staging"])
        self.assertEqual(certinfo.days_left(info), 83)
        self.assertEqual(certinfo.doc(info, "letsencrypt"),
                         {"kind": "letsencrypt", "names": ["vpn.example.com"], "not_after": "2026-12-30T10:00:00Z",
                          "days_left": 83, "staging": False, "issuer": "R11"})

    def test_staging_is_recognized(self):
        info = certinfo.parse(fixture("staging.pem"))
        self.assertTrue(info["staging"])
        self.assertIn("STAGING", info["issuer"])

    def test_covers(self):
        info = certinfo.parse(fixture("le.pem"))
        self.assertTrue(certinfo.covers(info, "VPN.example.com."))
        self.assertFalse(certinfo.covers(info, "other.example.com"))
        self.assertTrue(certinfo.covers({"names": ["*.example.com"]}, "vpn.example.com"))
        self.assertFalse(certinfo.covers({"names": ["*.example.com"]}, "a.vpn.example.com"))
        idn = certinfo.parse(fixture("idn.pem"))
        self.assertTrue(certinfo.covers(idn, model.parse_domain("впн.пример.рф")))

    def test_expiry(self):
        info = certinfo.parse(fixture("le.pem"))
        self.assertFalse(certinfo.expired(info))
        with mock.patch.object(certinfo, "_now", lambda: info["not_after_ts"] + 1):
            self.assertTrue(certinfo.expired(info))
            self.assertEqual(certinfo.days_left(info), -1)

    def test_garbage(self):
        for bad in ("", "nonsense", "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", None):
            with self.assertRaises(ValueError):
                certinfo.parse(bad)

    def test_node_certificate_of_the_panel_fixture(self):
        from tests.panel.panelcase import fixture as panel_fixture
        info = certinfo.parse(panel_fixture("node.crt"))
        self.assertTrue(info["names"])
        self.assertFalse(info["staging"])
