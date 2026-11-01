"""Stage 1, T11 — the round of fixes after the strict review REV-s1-t11 (SPEC-stage1-exits §9.1, §2.9).

С2: a key the real xray refuses never gets into the pool — the parser refuses what it knows (vmess/trojan over QUIC,
vmess with REALITY, a key of Shadowsocks 2022 of a wrong base64 or length, junk in encryption/pqv/fm of VLESS), the
outbound of a VLESS of the pool leaves such junk out as 3.3.5 did, and a new line is asked of the real xray
(xray_refused, the subcommand vet; a subscription — before the replace, exitsubs._apply).
С3: a VLESS without TLS a subscription gave on 3.3.5 stays in the pool on its refresh (with a warning); a new one — no.
П1, П2: «443abc» is no port, a host with spaces or «$(…)» is no host, IDN — in punycode (the parser and exits alike)."""

import base64
import json
import os
import urllib.parse
from unittest import mock

from kaskad.gw import exits, exitsubs
from tests import realbin
from tests.fakes_gw import VLESS_B, write_pool
from tests.test_335_rename import RenameCase
from tests.test_334_xray import Quiet, parser
from tests.test_egress_parse_s1 import FIX, SS22_KEY, UUID, cli, vmess
from tests.test_gw_exitsubs import IP_URL

# a finalmask of the right shape with a type xray does not know: only the real xray can tell
FM_BOGUS = "vless://" + UUID + "@fmb.example.com:443?security=tls&sni=fmb.example.com&type=tcp&fm=" + \
    urllib.parse.quote('{"tcp":[{"type":"bogus"}]}', safe="") + "#FmBogus"
NOTLS = "vless://" + UUID + "@notls.example.com:8080?security=none&type=tcp#NoTLS"


def b64(lines):
    return base64.b64encode("\n".join(lines).encode()).decode()


def need_xray(test):
    if realbin.xray_path() is None:
        test.skipTest("no real xray (tools/fetch_test_bins.sh)")
    return realbin.xray_path()


class ParserRefusesTest(Quiet):
    def test_what_xray_26_refuses(self):
        p = parser()
        k32 = base64.b64encode(bytes(32)).decode()
        cases = {
            "vmess quic": vmess(net="quic"),
            "vmess reality": vmess(tls="reality"),
            "trojan quic": "trojan://pw@tq.example.com:443?security=tls&type=quic#TQ",
            "vless quic": "vless://%s@vq.example.com:443?security=tls&type=quic#VQ" % UUID,
            "ss2022 not base64": "ss://2022-blake3-aes-128-gcm:pw@s.example.com:8443#S",
            "ss2022 no padding": "ss://2022-blake3-aes-128-gcm:%s@s.example.com:8443#S"
                                 % urllib.parse.quote(SS22_KEY.rstrip("="), safe=""),
            "ss2022 wrong length": "ss://2022-blake3-aes-128-gcm:%s@s.example.com:8443#S"
                                   % urllib.parse.quote(k32, safe=""),
            "vless encryption bogus": "vless://%s@e.example.com:443?security=tls&encryption=bogus#E" % UUID,
            "vless encryption short key": "vless://%s@e.example.com:443?security=tls&encryption="
                                          "mlkem768x25519plus.native.0rtt.abc#E" % UUID,
            "vless pqv junk": "vless://%s@r.example.com:443?security=reality&pbk=k&sid=ab&pqv=xyz#R" % UUID,
            "vless fm junk": "vless://%s@f.example.com:443?security=tls&fm=%s#F"
                             % (UUID, urllib.parse.quote('{"foo":1}', safe="")),
            "vless fm not json": "vless://%s@f.example.com:443?security=tls&fm=zzz#F" % UUID,
        }
        for name, link in cases.items():
            self.assertEqual(p.link_problem(link), "bad_link", name)
        # the good ones of the same kinds stay good
        self.assertEqual(p.link_problem(FIX["ss_2022"]), "")
        self.assertEqual(p.link_problem("ss://2022-blake3-aes-256-gcm:%s@s.example.com:8443#S"
                                        % urllib.parse.quote(k32, safe="")), "")
        self.assertEqual(p.link_problem(FIX["vless_enc"]), "")
        self.assertEqual(p.link_problem(FIX["vless_fm"]), "")

    def test_a_vless_of_the_pool_keeps_working_as_on_335(self):
        """The junk 3.3.5 left out is left out of the outbound again — a line of the pool is not refused by xray."""
        p = parser()
        link = ("vless://%s@old.example.com:443?security=tls&sni=old.example.com&type=tcp&encryption=bogus"
                "&fm=%s#Old" % (UUID, urllib.parse.quote('{"foo":1}', safe="")))
        ob = p.xray_outbound(link, "out_1", 0)
        self.assertEqual(ob["settings"]["vnext"][0]["users"][0]["encryption"], "none")
        self.assertNotIn("finalmask", ob["streamSettings"])
        self.assertEqual(p.pool_problem(link), "")
        if realbin.xray_path():
            ok, text = realbin.xray_check({"outbounds": [ob], "inbounds": [
                {"tag": "s", "listen": "127.0.0.1", "port": 10808, "protocol": "socks", "settings": {}}]})
            self.assertTrue(ok, text)

    def test_port_and_host(self):
        """П1, П2: «443abc» — no port; spaces, «$(…)», quotes — no host; IDN — punycode; the same in exits."""
        p = parser()
        for link in ("trojan://pw@h.example.com:443abc?security=tls#P",
                     "trojan://pw@bad host.example.com:443?security=tls#P",
                     "trojan://pw@$(id).example.com:443?security=tls#P",
                     "hy2://auth@h'.example.com:443?sni=x#P",
                     vmess(add="a b.example.com")):
            self.assertEqual(p.link_problem(link), "bad_link", link)
            self.assertEqual(exits.new_peer(link), "", link)
        idn = "trojan://pw@пример.example:443?security=tls#IDN"
        self.assertEqual(p.endpoint(idn), ("xn--e1afmkfd.example", 443))
        self.assertEqual(exits.new_peer(idn), p.peer_key(idn))
        self.assertIn("xn--e1afmkfd.example", exits.new_peer(idn))
        self.assertEqual(p.endpoint("hy2://a@[2001:db8::1]:443?sni=x#6"), ("2001:db8::1", 443))

    def test_ss_method_in_upper_case(self):
        """REV З5 (M14): the method of Shadowsocks in upper case — the same key (exits and the parser)."""
        p = parser()
        lo = "ss://" + base64.urlsafe_b64encode(b"chacha20-ietf-poly1305:pw1").decode().rstrip("=") + "@s.example.com:8388#a"
        up = "ss://" + base64.urlsafe_b64encode(b"CHACHA20-IETF-POLY1305:pw1").decode().rstrip("=") + "@s.example.com:8388#b"
        self.assertEqual(exits.new_peer(up), exits.new_peer(lo))
        self.assertEqual(exits.new_peer(up), p.peer_key(up))
        self.assertEqual(p.link_problem(up), "")


class XrayRefusedTest(Quiet):
    def setUp(self):
        super().setUp()
        p = parser()                    # its own xray-forms.json: vet marks the lines it took (REV-s1-t11-2)
        patch = mock.patch.object(p, "_FORMS_FILE", os.path.join(__import__("tempfile").mkdtemp(), "xray-forms.json"))
        patch.start()
        self.addCleanup(patch.stop)
        p._forms_cache.clear()
        self.addCleanup(p._forms_cache.clear)

    def test_the_real_xray_is_asked(self):
        x = need_xray(self)
        p = parser()
        self.assertEqual(p.link_problem(FM_BOGUS), "")                     # the parser cannot tell
        lines = [FIX["trojan_tls"], FM_BOGUS, FIX["ss_sip002"]]
        self.assertEqual(p.xray_refused(lines, x), [2])
        self.assertEqual(p.xray_refused(lines, x, {1, 3}), [])             # only the lines asked for
        self.assertEqual(p.xray_refused(lines, ""), [])                    # no xray — nothing said
        self.assertEqual(p.xray_refused([FIX["trojan_tls"]], x), [])

    def test_vet_keeps_the_lines_of_the_pool(self):
        x = need_xray(self)
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(FM_BOGUS + "\n")
        self.addCleanup(os.unlink, f.name)
        text = "\n".join([FIX["trojan_tls"], FM_BOGUS]) + "\n"
        r = cli(["vet", "--xray", x], text)
        self.assertEqual(r.stdout.split(), [FIX["trojan_tls"]])
        self.assertEqual(r.stderr.strip(), "K3SKIP\txray_refused\tFmBogus")
        r = cli(["vet", "--xray", x, "--keep", f.name], text)            # a line of the pool: as it was
        self.assertEqual(r.stdout.split(), [FIX["trojan_tls"], FM_BOGUS])


class SubscriptionVetTest(RenameCase):
    def xray_here(self):
        """The fake xray of GwCase asks the real one (realbin.in_fakes) — through the runner, as on a node."""
        need_xray(self)
        if not realbin.in_fakes():
            self.skipTest("KASKAD_TEST_NO_REAL=1")

    def test_a_line_xray_refuses_is_left_out_alone(self):
        self.xray_here()
        self.add(IP_URL, b64([FM_BOGUS, VLESS_B]))
        self.assertEqual(self.pool(), [VLESS_B])
        self.assertIn("xray_refused", exitsubs.load()["subs"][0].get("skip_codes") or [])

    def test_renamed_into_a_line_xray_refuses_keeps_the_line_of_before(self):
        self.xray_here()
        a = "trojan://tjOne@198.51.100.43:443?security=tls&sni=tj.example.com#A"
        self.add(IP_URL, b64([a, VLESS_B]))
        eid = self.ids()[0]
        bad = a.replace("#A", "&fm=" + urllib.parse.quote('{"tcp":[{"type":"bogus"}]}', safe="") + "#A2")
        self.feed.bodies[IP_URL] = b64([bad, VLESS_B])
        r = self.refresh()["results"][0]
        self.assertEqual(self.pool(), [a, VLESS_B], r["text"])
        self.assertEqual(self.ids()[0], eid)
        self.assertEqual((r["removed"], r["skipped"]), (0, 1), r["text"])


class NoTlsKeptTest(RenameCase):
    def add_as_335(self, lines):
        """The subscription added as 3.3.5 did: a VLESS without TLS was taken."""
        p = exitsubs._parser(self.w.runner)
        old = p.link_problem
        with mock.patch.object(p, "link_problem", lambda l: p.vless_problem(l) if p.link_proto(l) == "vless" else old(l)):
            self.add(IP_URL, b64(lines))

    def test_refresh_keeps_it_with_a_warning(self):
        self.add_as_335([NOTLS, VLESS_B])
        self.assertEqual(self.pool(), [NOTLS, VLESS_B])
        eid = self.ids()[0]
        r = self.refresh()["results"][0]
        self.assertEqual(self.pool(), [NOTLS, VLESS_B], r["text"])
        self.assertEqual(self.ids()[0], eid)
        self.assertEqual((r["added"], r["removed"]), (0, 0), r["text"])
        self.assertTrue(any("без TLS оставлен" in w for w in r["warnings"]), r)

    def test_a_new_one_is_not_taken(self):
        self.add(IP_URL, b64([NOTLS, VLESS_B]))
        self.assertEqual(self.pool(), [VLESS_B])
        self.assertIn("no_tls", exitsubs.load()["subs"][0].get("skip_codes") or [])


class BotOffWarningTest(Quiet):
    def test_the_core_says_it_in_its_words(self):
        """REV-s1-t11 С5: the mark tg_group_empty of the rebuild — the core's words in the language of the call."""
        from kaskad.gw import ops
        from kaskad.i18n import use_lang
        lines = ["⚠ whatever the script said", "@K3:egress_warn:tg_group_empty"]
        for lang, words in (("ru", "бот ходит напрямую"), ("en", "the bot goes direct")):
            with use_lang(lang):
                got = ops._script_warnings(lines)
            self.assertEqual(len(got), 1, got)
            self.assertIn(words, got[0])


class ProblemWordsTest(Quiet):
    """REV-s1-t11 З5 (M18): a VLESS key keeps the words of 3.3.4 (problem.vless_*) in the pool and in the preview;
    З6: Hysteria2 without a check of the certificate is not told «add it again to pin» — no TOFU over QUIC."""

    def test_vless_keeps_the_words_of_334(self):
        p = parser()
        line = "vless://%s@h.example.com:443?security=tls&sni=h.example.com&allowInsecure=1#X" % UUID
        e = {}
        exits._key_problem(e, p, line, "unparsed")
        self.assertEqual(e["problem_code"], "problem.vless_insecure")
        row = exits.preview(p, line)[0]
        self.assertEqual(row["problem"], exits.word("problem.vless_insecure"))

    def test_hysteria2_insecure_has_its_own_advice(self):
        p = parser()
        e = {}
        exits._key_problem(e, p, FIX["hy2_insecure"], "unparsed")
        self.assertEqual(e["problem_code"], "problem.key_insecure_quic")
        self.assertIn("pinSHA256", e["problem"])
        self.assertNotIn("заново", e["problem"])
        self.assertIn("problem.key_insecure_quic", exits.DETAIL_CODES)
        e = {}
        exits._key_problem(e, p, "trojan://pw@t.example.com:443?security=tls&allowInsecure=1#T", "unparsed")
        self.assertEqual(e["problem_code"], "problem.key_insecure")          # over TCP the advice of TOFU stays
