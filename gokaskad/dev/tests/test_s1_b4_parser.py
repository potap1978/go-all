"""Stage 1, B4 (REV-s1-hotfix-merge): the address of a key — the parser (egress_parse.py of install.sh) and the core alike,
the same on Python 3.9 and 3.12+.

З-1: the words of ``bad_link`` fit a key of any kind (an AmneziaWG .conf / vpn:// is no «ссылка», xray is not in it).
З-2: (а) the Endpoint of AmneziaWG takes one strict port (``_port1``, not the range ``_port`` of Hysteria2);
     (б) ``exits.pool`` — an AmneziaWG record with a bad Endpoint is «unparsed», not usable (no leg).
П-1: the port of a VLESS / trojan key is read from its text (``_netloc_port``), never ``urlparse().port``.
П-2: ports and hops of the T11 schemes — ASCII digits only (``[0-9]``, not ``\\d``)."""

import base64
import io
import json
import os
import re
import unittest

from kaskad import i18n
from kaskad.gw import exits
from tests.fakes_gw import awg_conf, install_parser, write_pool
from tests.gwcase import GwCase
from tests.test_334_xray import Quiet, parser

UUID = "11111111-2222-3333-4444-555555555555"
TAIL = "?type=tcp&security=reality&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com"
SH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "i18n", "sh")


def _awg_pool_line(p, endpoint):
    rec = p.awg_from_conf(awg_conf())
    rec["peer"]["endpoint"] = endpoint
    return "awg://" + base64.urlsafe_b64encode(json.dumps(rec).encode()).decode().rstrip("=")


class AwgEndpointPortTest(Quiet):
    def test_one_strict_port(self):
        """З-2 (а): «h:», a range, a list, «４４３» — bad_link for AmneziaWG (they pass ``_port`` of Hysteria2)."""
        p = parser()
        for ep in ("198.51.100.31:", "198.51.100.31:51820-51830", "198.51.100.31:51820,51821",
                   "198.51.100.31:４４３", "198.51.100.31:-1", "awg.example.com:"):
            self.assertIsNone(p._awg_addr(ep), ep)
            good, skipped = p.extract_report(awg_conf(endpoint=ep))
            self.assertEqual((good, [x["code"] for x in skipped]), ([], ["bad_link"]), ep)
        self.assertEqual(p._awg_addr("198.51.100.31:51820"), ("198.51.100.31", 51820))


class AwgPoolUnparsedTest(GwCase):
    def test_a_bad_endpoint_is_unparsed_not_usable(self):
        """З-2 (б): exits.pool — an AmneziaWG line of the pool with a bad Endpoint: «unparsed», never usable."""
        install_parser()
        p = exits.load_parser()
        bad, good = _awg_pool_line(p, "198.51.100.1;x:51820"), _awg_pool_line(p, "198.51.100.31:51820")
        write_pool([bad, good])
        pool = exits.pool(self.w.runner)
        self.assertEqual(len(pool), 2)
        self.assertFalse(exits.parsed(pool[0]), pool[0])
        self.assertTrue(exits.parsed(pool[1]), pool[1])
        with i18n.use_lang("ru"):
            self.assertEqual(pool[0].get("problem"), i18n.tr("gw_exits.problem.unparsed"))


class BadLinkWordsTest(unittest.TestCase):
    """З-1: no «ссылка»/«link» and no «xray» in the words of bad_link — the menu, the core, the subscriptions."""

    KEYS = ("gw_ops.script_fail.skipped.bad_link", "gw_ops.script_warn.skipped.bad_link",
            "gw_ops.script_warn.vless_key.bad_link", "gw_exitsubs.skip.bad_link")
    SH = ("EGRESS_KEY_SKIP_BAD_LINK", "EGRESS_SKIP_WHY_BAD_LINK", "EGRESS_W_VLESS_BAD_LINK")

    def test_words_fit_a_key_of_any_kind(self):
        bad = re.compile(r"ссылк|xray|\blinks?\b", re.I)
        for lang in ("ru", "en"):
            with i18n.use_lang(lang):
                for k in self.KEYS:
                    t = i18n.tr(k, n=2)
                    self.assertNotEqual(t, k)
                    self.assertIsNone(bad.search(t), (lang, k, t))
            with io.open(os.path.join(SH, "egress.%s.sh" % lang), encoding="utf-8") as f:
                text = f.read()
            for k in self.SH:
                m = re.search(r"^M_%s_%s='(.*)'$" % (lang, k), text, re.M)
                self.assertIsNotNone(m, (lang, k))
                self.assertIsNone(bad.search(m.group(1)), (lang, k, m.group(1)))


class VlessPortTest(Quiet):
    """П-1: the port is read from the text of the key, as ``_port1`` (spaces around stripped) — on 3.9 and 3.14 alike."""

    LINES = ("vless://%s@198.51.100.7: 443%s#k" % (UUID, TAIL), "vless://%s@198.51.100.7:443 %s#k" % (UUID, TAIL))

    def test_same_on_every_python(self):
        p = parser()
        for line in self.LINES:
            self.assertEqual(p.vless_problem(line), "", line)
            self.assertNotEqual(p.link_problem(line), "bad_link", line)
            want = "vless|%s|198.51.100.7:443" % UUID
            self.assertEqual(p.peer_key(line), want, line)
            self.assertEqual(exits.vless_peer(line), want, line)
        for bad in ("vless://%s@198.51.100.7:４４３%s#k" % (UUID, TAIL), "vless://%s@198.51.100.7:0%s#k" % (UUID, TAIL)):
            self.assertEqual(exits.vless_peer(bad), "", bad)
            self.assertFalse(p.peer_key(bad).startswith("vless|"), bad)
        self.assertEqual(exits._netloc_port("u@h"), 443)
        self.assertEqual(exits._netloc_port("u@[2001:db8::1]:8443"), 8443)
        self.assertIsNone(exits._netloc_port("u@[2001:db8::1]x"))
        self.assertIsNone(exits._netloc_port("u@h:1:2"))
        for nl in ("u@h", "u@h: 443", "u@[2001:db8::1]:8443", "u@h:1:2", "u@h:４４３", "u@h:", "u@h:65536"):
            self.assertEqual(p._netloc_port(nl), exits._netloc_port(nl), nl)

    def test_tofu_server_reads_the_port_so(self):
        import urllib.parse
        p = parser()
        line = "vless://%s@tls.example.com: 8443?security=tls&sni=tls.example.com&type=tcp#k" % UUID
        u = urllib.parse.urlparse(line)
        self.assertEqual(p._tofu_server(u, urllib.parse.parse_qs(u.query)), "tls.example.com:8443|tls.example.com")


class AsciiDigitsTest(Quiet):
    """П-2: «４４３» and the hop «４４３-４５０» are no ports — in the parser and in the core alike."""

    def test_no_unicode_digits(self):
        p = parser()
        for mod in (p, exits):
            self.assertIsNone(mod._HOP.match("４４３-４５０"), mod)
            self.assertIsNotNone(mod._HOP.match("20000-30000,443"), mod)
            self.assertIsNone(mod._HOP.match("443\n"), mod)
        self.assertIsNone(p._port("４４３"))
        self.assertEqual(p._port("443,20000-30000"), 443)
        self.assertIsNone(exits._first_port("４４３"))
        self.assertEqual(exits._first_port("20000-30000"), 20000)
        self.assertIsNone(p._HOP_INT.match("１０-３０"))
        for link in ("hy2://hyAuth1@hy.example.com:４４３/?sni=hy.example.com#k",
                     "hy2://hyAuth1@hy.example.com:443/?sni=hy.example.com&mport=４４３-４５０#k"):
            self.assertIsNone(p._hy2(link), link)
            self.assertEqual(p.link_problem(link), "bad_link", link)


if __name__ == "__main__":
    unittest.main()
