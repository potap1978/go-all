"""Stage 1, T11 (B9 — the new keys of an exit; SPEC-stage1-exits §9, §2.9, §3.8): egress_parse.py of install.sh takes
hy2:// (hysteria2://), ss://, trojan://, vmess:// besides vless:// and AmneziaWG — the outbound of xray, the code of a key
refused alone, the check «a second Hysteria2 at one address», who a key is (peer_key/peers — the same string as
kaskad/gw/exits.py), the endpoint of its server, the config of the bot's proxy for the keys of one group (--only) and
without the probes (--no-observatory); the core loads it (REQUIRED, PARSER_MIN, a parser of 3.3.5 on the disk rewritten
once) and reads the pool of the new schemes without it too (pool_fallback). The fixtures FIX — every scheme (other tests
of the stage take them: tests/gw/test_s1_idcarry.py, tests/test_util_redact_s1.py)."""

import base64
import json
import os
import re
import subprocess
import sys
import unittest
from urllib.parse import quote

from kaskad import paths
from kaskad.errors import KaskadError
from kaskad.gw import exits, exitsubs
from tests import realbin
from tests.fakes_gw import INSTALL_SH, install_parser, write_pool
from tests.gwcase import GwCase
from tests.test_334_xray import PIN, Quiet, parser

UUID = "11111111-2222-3333-4444-555555555555"
SS22_KEY = "AAAAAAAAAAAAAAAAAAAAAA=="                       # 16 bytes of base64: the key of 2022-blake3-aes-128-gcm
PQV = base64.urlsafe_b64encode(bytes(i % 251 for i in range(1952))).decode().rstrip("=")   # ML-DSA-65: 1952 bytes


def b64(s, url=False):
    raw = s.encode() if isinstance(s, str) else s
    return (base64.urlsafe_b64encode(raw) if url else base64.b64encode(raw)).decode()


def vmess(**kw):
    j = {"v": "2", "ps": "VMess TLS", "add": "vm.example.com", "port": "443", "id": UUID.replace("5555", "6666", 1),
         "aid": "0", "scy": "auto", "net": "ws", "type": "none", "host": "vm.example.com", "path": "/vm", "tls": "tls",
         "sni": "vm.example.com"}
    j.update(kw)
    return "vmess://" + b64(json.dumps(j))


# the links of every scheme of §9.1 — secrets are made up; «#name» stays readable after redact
FIX = {
    "hy2": "hy2://hyAuthSecret1@hy.example.com:443/?sni=hy.example.com&obfs=salamander&obfs-password=obfsSecret1"
           "&pinSHA256=" + PIN + "#Hysteria%20One",
    "hy2_hop": "hysteria2://hyAuthSecret2@hop.example.com:443,20000-20010/?sni=hop.example.com&alpn=h3"
               "&hopInterval=30#Hop",
    "hy2_insecure": "hy2://hyAuthSecret3@self.example.com:443?insecure=1#SelfSigned",
    "ss_sip002": "ss://" + b64("chacha20-ietf-poly1305:ssSecret1", url=True).rstrip("=") + "@ss.example.com:8388#SS%20One",
    "ss_old": "ss://" + b64("chacha20-ietf-poly1305:ssSecret1@ss.example.com:8388") + "#SS%20old",
    "ss_2022": "ss://2022-blake3-aes-128-gcm:" + quote(SS22_KEY, safe="") + "@ss22.example.com:8443#SS2022",
    "ss_outline": "ss://" + b64("chacha20-ietf-poly1305:ssSecret4", url=True).rstrip("=")
                  + "@ol.example.com:443/?outline=1&prefix=%16%03%01#Outline",
    "ss_none": "ss://" + b64("none:ssPlain") + "@plain.example.com:8388#NoCipher",
    "trojan_tls": "trojan://tjSecret1@tj.example.com:443?security=tls&sni=tj.example.com&type=tcp#Trojan",
    "trojan_ws": "trojan://tjSecret2@tjws.example.com:443?security=tls&sni=tjws.example.com&type=ws&path=%2Fws"
                 "&host=tjws.example.com#TrojanWS",
    "trojan_notls": "trojan://tjSecret3@tj0.example.com:80?security=none#NoTLS",
    "vmess_tls": vmess(),
    "vmess_none": vmess(ps="VMess none", scy="none", add="vm0.example.com"),
    "vless_enc": "vless://" + UUID + "@enc.example.com:443?security=none&type=xhttp&path=%2Fx"
                 "&encryption=mlkem768x25519plus.native.0rtt.MJsTzeRGHeL9rTagmcpDoWFMa1C7xno4UGAkn5EaqwA"
                 "&extra=" + quote('{"xPaddingBytes":"100-1000"}', safe="") + "#ENC",
    "vless_hu": "vless://" + UUID + "@hu.example.com:443?security=tls&sni=hu.example.com&type=httpupgrade&path=%2Fu"
                "&ech=" + quote("hu.example.com+https://1.1.1.1/dns-query", safe="") + "#HTTPUpgrade",
    "vless_fm": "vless://" + UUID + "@fm.example.com:443?security=tls&sni=fm.example.com&type=tcp"
                "&fm=" + quote('{"tcp":[{"type":"fragment","settings":{"packets":"tlshello","length":"100-200"}}]}',
                               safe="") + "#Finalmask",
    "tuic": "tuic://" + UUID + ":tuicSecret@tuic.example.com:443?congestion_control=bbr#TUIC",
}
GOOD = ("hy2", "hy2_hop", "ss_sip002", "ss_old", "ss_2022", "ss_outline", "trojan_tls", "trojan_ws", "vmess_tls",
        "vless_enc", "vless_hu", "vless_fm")
REFUSED = {"hy2_insecure": "insecure", "ss_none": "weak_cipher", "trojan_notls": "no_tls", "vmess_none": "weak_cipher",
           "tuic": "unsupported_scheme"}
PROTO = {"hy2": "hysteria2", "ss": "shadowsocks", "trojan": "trojan", "vmess": "vmess", "vless": "vless"}


def proto_of(name):
    return PROTO.get(name.split("_", 1)[0], "")


_PATH = {}


def parser_path():
    if "p" not in _PATH:
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "egress_parse.py")
        with open(p, "w", encoding="utf-8") as f:
            from tests.fakes_gw import parser_source
            f.write(parser_source())
        _PATH["p"] = p
    return _PATH["p"]


def cli(args, text):
    r = subprocess.run([sys.executable, parser_path()] + args, input=text, capture_output=True, text=True, timeout=60)
    return r


class SchemesTest(Quiet):
    def test_version_one_number(self):
        """§9.2: the number of the stage = the number of dev + 1 (19 — 3.3.5 → 20; with 3.3.6 in, 25 → 26: SPEC-todo-coordinator
        «Стык 3.3.6 ↔ этап 1»); one constant for the core."""
        self.assertEqual(parser().PARSER_VERSION, 28)
        self.assertEqual(exits.PARSER_MIN, 28)
        self.assertEqual(exitsubs.PARSER_MIN, exits.PARSER_MIN)
        with open(INSTALL_SH, encoding="utf-8") as f:
            self.assertIn("# PARSER_VER=28 ", f.read())
        for n in ("xray_outbound", "link_proto", "peer_key"):
            self.assertIn(n, exits.REQUIRED)

    def test_proto_and_problem_of_every_fixture(self):
        p = parser()
        for name in GOOD:
            self.assertEqual(p.link_proto(FIX[name]), proto_of(name), name)
            self.assertEqual(p.link_problem(FIX[name]), "", name)
            ob = p.xray_outbound(FIX[name], "out_1", 1000)
            self.assertTrue(ob, name)
            self.assertEqual(ob["tag"], "out_1")
        for name, code in REFUSED.items():
            self.assertEqual(p.link_problem(FIX[name]), code, name)
            self.assertIsNone(p.xray_outbound(FIX[name], "out_1", 0), name)
        self.assertEqual(p.link_proto(FIX["tuic"]), "")
        self.assertEqual(p.scheme_title(FIX["tuic"]), "TUIC")
        self.assertEqual(p.scheme_title("https://sub.example.com/x"), "")      # an address, not a key

    def test_hysteria2_form_of_t0(self):
        """T0 (26.3.27): masks and udpHop in streamSettings.finalmask, ALPN h3 by default, the pin."""
        p = parser()
        ob = p.xray_outbound(FIX["hy2"], "gx-1", 1000)
        self.assertEqual(ob["protocol"], "hysteria")
        self.assertEqual(ob["settings"], {"version": 2, "address": "hy.example.com", "port": 443})
        st = ob["streamSettings"]
        self.assertEqual(st["network"], "hysteria")
        self.assertEqual(st["hysteriaSettings"], {"version": 2, "auth": "hyAuthSecret1"})
        self.assertEqual(st["tlsSettings"]["alpn"], ["h3"])
        self.assertEqual(st["tlsSettings"]["pinnedPeerCertSha256"], PIN)
        self.assertEqual(st["finalmask"]["udp"], [{"type": "salamander", "settings": {"password": "obfsSecret1"}}])
        self.assertNotIn("udphop", json.dumps(st["hysteriaSettings"]).lower())
        hop = p.xray_outbound(FIX["hy2_hop"], "gx-2", 0)["streamSettings"]
        self.assertEqual(hop["finalmask"]["quicParams"]["udpHop"], {"ports": "443,20000-20010", "interval": "30"})
        self.assertEqual(p.xray_outbound(FIX["hy2_hop"], "gx-2", 0)["settings"]["port"], 443)   # the first port
        self.assertNotIn("allowInsecure", json.dumps(hop))

    def test_shadowsocks_forms_are_one_key(self):
        p = parser()
        a, b = p.xray_outbound(FIX["ss_sip002"], "t", 0), p.xray_outbound(FIX["ss_old"], "t", 0)
        self.assertEqual(a["settings"], b["settings"])
        self.assertEqual(a["settings"]["servers"][0]["method"], "chacha20-ietf-poly1305")
        s22 = p.xray_outbound(FIX["ss_2022"], "t", 0)["settings"]["servers"][0]
        self.assertEqual((s22["method"], s22["password"]), ("2022-blake3-aes-128-gcm", SS22_KEY))
        self.assertTrue(p.xray_outbound(FIX["ss_outline"], "t", 0))           # Outline's prefix: the client side only
        plug = "ss://" + b64("aes-128-gcm:pw", url=True) + "@p.example.com:8388/?plugin=obfs-local%3Bobfs%3Dhttp#P"
        self.assertEqual(p.link_problem(plug), "bad_link")                   # xray has no plugins

    def test_trojan_and_vmess(self):
        p = parser()
        tj = p.xray_outbound(FIX["trojan_ws"], "t", 0)
        self.assertEqual(tj["settings"]["servers"][0]["password"], "tjSecret2")
        self.assertEqual(tj["streamSettings"]["wsSettings"]["path"], "/ws")
        self.assertEqual(tj["streamSettings"]["security"], "tls")
        self.assertEqual(p.link_problem("trojan://pw@10.1.2.3:80?security=none#lan"), "")   # a private address: TLS
        vm = p.xray_outbound(FIX["vmess_tls"], "t", 0)                                       # is not asked for
        self.assertEqual(vm["settings"]["vnext"][0]["users"][0]["id"], UUID.replace("5555", "6666", 1))
        self.assertEqual(p.xray_label(FIX["vmess_tls"])["name"], "VMess TLS")
        self.assertEqual(p.link_problem(vmess(scy="zero")), "weak_cipher")
        self.assertEqual(p.link_problem("vmess://bm90IGpzb24"), "bad_link")

    def test_vless_extras_of_9_1(self):
        """The full form («ext»: a line the real xray took with them — xray_vet) carries them; a line not checked so —
        the form of 3.3.5 (REV-s1-t11-2 С2-2)."""
        p = parser()
        for key in ("vless_enc", "vless_hu", "vless_fm"):
            plain = json.dumps(p.xray_outbound(FIX[key], "t", 0))
            for word in ("mlkem768x25519plus", "xPaddingBytes", "echConfigList", "finalmask"):
                self.assertNotIn(word, plain, key)
        enc = p.xray_outbound(FIX["vless_enc"], "t", 0, "ext")
        self.assertTrue(enc["settings"]["vnext"][0]["users"][0]["encryption"].startswith("mlkem768x25519plus."))
        self.assertEqual(enc["streamSettings"]["xhttpSettings"]["extra"], {"xPaddingBytes": "100-1000"})
        hu = p.xray_outbound(FIX["vless_hu"], "t", 0, "ext")["streamSettings"]
        self.assertEqual(hu["httpupgradeSettings"], {"path": "/u", "host": "hu.example.com"})
        self.assertEqual(hu["tlsSettings"]["echConfigList"], "hu.example.com+https://1.1.1.1/dns-query")
        fm = p.xray_outbound(FIX["vless_fm"], "t", 0, "ext")["streamSettings"]
        self.assertEqual(fm["finalmask"]["tcp"][0]["type"], "fragment")
        pqv = p.xray_outbound("vless://%s@r.example.com:443?security=reality&pbk=k&sid=ab&pqv=%s#R" % (UUID, PQV), "t", 0,
                              "ext")
        self.assertEqual(pqv["streamSettings"]["realitySettings"]["mldsa65Verify"], PQV)
        # REV-s1-t11 С2: a wrong one — left out of the outbound of a key of the pool (as 3.3.5), refused for a new key
        bad = "vless://%s@r.example.com:443?security=reality&pbk=k&sid=ab&pqv=VERIFY#R" % UUID
        self.assertNotIn("mldsa65Verify", p.xray_outbound(bad, "t", 0, "ext")["streamSettings"]["realitySettings"])
        self.assertEqual(p.link_problem(bad), "bad_link")
        # a VLESS without TLS and without its own encryption: refused when it is added, kept when it is in the pool
        bare = "vless://%s@bare.example.com:80?security=none&type=tcp#bare" % UUID
        self.assertEqual(p.link_problem(bare), "no_tls")
        self.assertEqual(p.pool_problem(bare), "")
        self.assertTrue(p.vless_outbound(bare, "t", 0))

    def test_every_outbound_passes_real_xray(self):
        """§11.1: ``xray -test`` on every outbound (the real xray of the nodes, when the cache has it)."""
        if realbin.xray_path() is None:
            self.skipTest("no real xray (tools/fetch_test_bins.sh)")
        p = parser()
        obs = [p.xray_outbound(FIX[n], "out_%d" % i, 1000) for i, n in enumerate(GOOD, 1)]
        cfg = {"log": {"loglevel": "warning"}, "outbounds": obs,
               "inbounds": [{"tag": "s", "listen": "127.0.0.1", "port": 10808, "protocol": "socks",
                             "settings": {"udp": True}}]}
        ok, text = realbin.xray_check(cfg)
        self.assertTrue(ok, text)


class ExtractTest(Quiet):
    def test_mixed_base64_subscription(self):
        """The good keys + skipped with their codes; tuic:// — unsupported_scheme by the name of its scheme."""
        sub = b64("\n".join(FIX[n] for n in ("hy2", "ss_none", "trojan_tls", "tuic", "vmess_tls", "trojan_notls")))
        good, skipped = parser().extract_report(sub)
        self.assertEqual(good, [FIX["hy2"], FIX["trojan_tls"], FIX["vmess_tls"]])
        self.assertEqual([(x["code"], x["name"]) for x in skipped],
                         [("weak_cipher", "NoCipher"), ("no_tls", "NoTLS"), ("unsupported_scheme", "TUIC")])
        for x in skipped:
            self.assertNotIn("Secret", x["name"])

    def test_cli_extract_never_prints_a_key_on_stderr(self):
        r = cli(["extract"], "\n".join(FIX.values()))
        self.assertEqual(r.returncode, 0, r.stderr)
        skip = [l.split("\t") for l in r.stderr.splitlines() if l.startswith("K3SKIP\t")]
        self.assertIn(["K3SKIP", "unsupported_scheme", "TUIC"], skip)
        self.assertIn(["K3SKIP", "insecure", "SelfSigned"], skip)
        for secret in ("Secret", "ssPlain", UUID):
            self.assertNotIn(secret, r.stderr)

    def test_dup_endpoint(self):
        """T0: xray keeps one QUIC client per host:port — a second hy2 at one address is refused; the same key renamed
        is not a second key."""
        p = parser()
        a = "hy2://authA@hy.example.com:443?sni=hy.example.com#A"
        b = "hy2://authB@hy.example.com:443?sni=hy.example.com#B"
        a_renamed = "hy2://authA@hy.example.com:443?sni=hy.example.com&alpn=h3#A%20new"
        self.assertEqual([x["code"] for x in p.extract_report(a + "\n" + b)[1]], ["dup_endpoint"])
        self.assertEqual(p.extract_report(b, pool=[a])[0], [])
        self.assertEqual(p.extract_report(a_renamed, pool=[a]), ([a_renamed], []))
        other = "hy2://authB@hy.example.com:8443?sni=hy.example.com#B2"            # another port — another client
        self.assertEqual(p.extract_report(other, pool=[a]), ([other], []))
        with open(os.path.join(os.path.dirname(parser_path()), "pool.txt"), "w") as f:
            f.write(a + "\n")
        r = cli(["extract", "--pool", f.name, "--pool", f.name + ".missing"], b)
        self.assertEqual(r.stdout, "")
        self.assertIn("K3SKIP\tdup_endpoint\tB", r.stderr)


class PeersTest(Quiet):
    def test_parser_peer_key_is_the_core_one(self):
        """§3.8, Р3: peer_key of the parser (and its «peers») = exits.peer_key on stdlib, for every scheme."""
        p = parser()
        for name, line in FIX.items():
            if name == "tuic":
                continue
            self.assertEqual(p.peer_key(line), exits.peer_key(line), name)
            self.assertNotEqual(exits.peer_key(line), exits.exit_id(line), name)
        self.assertEqual(exits.peer_key(FIX["ss_sip002"]), exits.peer_key(FIX["ss_old"]))
        self.assertEqual(exits.peer_key(FIX["hy2"]), "hy2|hyAuthSecret1|hy.example.com:443")
        self.assertEqual(exits.peer_key(FIX["vmess_tls"]), "vmess|%s|vm.example.com:443" % UUID.replace("5555", "6666", 1))
        lines = [l for n, l in FIX.items() if n != "tuic"]
        r = cli(["peers"], "\n".join(lines) + "\n")
        want = [__import__("hashlib").sha1(exits.peer_key(l).encode()).hexdigest()[:16] for l in lines]
        self.assertEqual(r.stdout.split(), want)

    def test_name_and_parameters_are_not_who_the_key_is(self):
        for a, b in (("hy2://x@h.example.com:443?sni=a#One", "hysteria2://x@H.example.com?obfs=salamander&obfs-password=p#Two"),
                     ("trojan://pw@t.example.com:443?sni=a&fp=chrome#One", "trojan://pw@t.example.com?type=ws#Two"),
                     (vmess(ps="One"), vmess(ps="Two", path="/other", sni="x.example.com"))):
            self.assertEqual(exits.peer_key(a), exits.peer_key(b))
            self.assertEqual(parser().peer_key(a), parser().peer_key(b))
        for a, b in (("hy2://x@h.example.com:443#n", "hy2://y@h.example.com:443#n"),
                     ("trojan://pw@t.example.com:443#n", "trojan://pw@t2.example.com:443#n")):
            self.assertNotEqual(exits.peer_key(a), exits.peer_key(b))

    def test_endpoint(self):
        lines = [FIX["hy2_hop"], FIX["ss_old"], "awg://e30", FIX["vmess_tls"],
                 "hy2://a@[2001:db8::1]:443,1000-2000/?sni=x#v6"]
        r = cli(["endpoint"], "\n".join(lines) + "\n\n")
        self.assertEqual(r.stdout.splitlines(), ["hop.example.com\t443\thysteria2", "ss.example.com\t8388\tshadowsocks",
                                                 "-", "vm.example.com\t443\tvmess", "2001:db8::1\t443\thysteria2"])


class BotConfigTest(Quiet):
    """§2.9: the config of the bot's proxy — only the lines of the group (their numbers kept), and «without probes»."""

    LINES = [FIX["trojan_tls"], FIX["vless_hu"], FIX["ss_sip002"], FIX["hy2"]]

    def config(self, *args):
        r = cli(["config"] + list(args), "\n".join(self.LINES) + "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_only_keeps_the_numbers(self):
        cfg = self.config("--only", "2 4")
        tags = [o["tag"] for o in cfg["outbounds"]]
        self.assertEqual(tags, ["out_2", "out_4", "direct", "block"])
        self.assertEqual(cfg["outbounds"][0]["protocol"], "vless")
        self.assertEqual(cfg["outbounds"][1]["protocol"], "hysteria")
        r = cli(["config", "--only", ""], "\n".join(self.LINES) + "\n")
        self.assertEqual(r.returncode, 2)                                   # no line of the group: refused as no keys

    def test_no_observatory(self):
        for only in ("3", "1 2 3"):
            cfg = self.config("--only", only, "--no-observatory")
            self.assertNotIn("observatory", cfg)
            self.assertEqual(cfg["api"]["services"], ["RoutingService", "StatsService"])
            bal = cfg["routing"]["balancers"]
            self.assertEqual(bal, [{"tag": "bot-bal", "selector": ["out_"], "strategy": {"type": "roundRobin"}}])
            self.assertIn({"type": "field", "inboundTag": ["socks-in"], "balancerTag": "bot-bal"}, cfg["routing"]["rules"])
            if realbin.xray_path() is not None:
                ok, text = realbin.xray_check(cfg)
                self.assertTrue(ok, text)
        old = self.config()                                                   # without the flags: as 3.3.3
        self.assertIn("observatory", old)
        self.assertEqual(old["routing"]["balancers"][0]["fallbackTag"], "out_1")


class CoreTest(GwCase):
    """The core: the pool of the new schemes, the preview, pool_fallback, the parser of 3.3.5 on the disk."""

    def setUp(self):
        super().setUp()
        exits._refreshed.clear()
        self.addCleanup(exits._refreshed.clear)

    def test_pool_of_every_scheme(self):
        install_parser()
        names = ["hy2", "ss_2022", "trojan_ws", "vmess_tls", "hy2_insecure"]
        write_pool([FIX[n] for n in names])
        pool = exits.pool(self.w.runner)
        self.assertEqual([(e["kind"], e["proto"]) for e in pool][:4],
                         [("xray", "hysteria2"), ("xray", "shadowsocks"), ("xray", "trojan"), ("xray", "vmess")])
        self.assertEqual([e["mark"] for e in pool][:4], [0x301, 0x302, 0x303, 0x304])
        self.assertEqual(pool[0]["outbound"]["tag"], exits.tag(pool[0]["id"]))
        self.assertEqual(pool[4]["kind"], "bad")
        self.assertEqual(pool[4]["problem_code"], "problem.key_insecure_quic")   # REV-s1-t11 З6: no TOFU over QUIC
        self.assertIn("Hysteria2", pool[4]["problem"])
        pub = json.dumps([exits.public(e) for e in pool], ensure_ascii=False)
        for secret in ("hyAuthSecret", "obfsSecret", "tjSecret", UUID[:8], "hy2://", "trojan://"):
            self.assertNotIn(secret, pub)

    def test_pool_fallback_is_not_bad(self):
        """§9.2: without the parser the new schemes are «not ready» (kind xray, a mark), never «bad»."""
        write_pool([FIX["hy2"], FIX["ss_old"], FIX["trojan_tls"], FIX["vmess_tls"], FIX["tuic"]])
        rows = exits.pool_fallback()
        self.assertEqual([(e["kind"], e["mark"]) for e in rows],
                         [("xray", 0x301), ("xray", 0x302), ("xray", 0x303), ("xray", 0x304), ("bad", None)])
        self.assertEqual([e["proto"] for e in rows][:4], ["hysteria2", "shadowsocks", "trojan", "vmess"])
        self.assertTrue(all(e["outbound"] is None for e in rows))

    def test_preview(self):
        install_parser()
        par = exits.load_parser()
        rows = exits.preview(par, "\n".join([FIX["hy2"], FIX["trojan_notls"], FIX["tuic"], FIX["ss_none"]]))
        by = dict((r["name"] or r["title"], r) for r in rows)
        self.assertEqual(by["Hysteria One"]["title"], "Hysteria2")
        self.assertNotIn("problem", by["Hysteria One"])
        self.assertIn("TLS", by["NoTLS"]["problem"])
        self.assertIn("Trojan", by["NoTLS"]["problem"])
        self.assertEqual(by["TUIC"]["kind"], "bad")
        self.assertIn("не принимает", by["TUIC"]["problem"])
        self.assertIn("шифр", by["NoCipher"]["problem"])

    def _v19(self, mtime=1):
        """The parser of 3.3.5 on the disk: PARSER_VERSION 19, no xray_outbound/link_proto (and an old mtime)."""
        p = install_parser()
        with open(p, encoding="utf-8") as f:
            text = f.read()
        text = re.sub(r"^PARSER_VERSION = \d+", "PARSER_VERSION = 19", text, count=1, flags=re.M)
        text += "\ndel xray_outbound\ndel link_proto\n"
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        os.utime(p, (mtime, mtime))
        return p

    def calls(self):
        return [c for c in self.w.script.calls if c == ["--egress-ensure-parser"]]

    def test_parser_of_335_is_rewritten_once(self):
        """§9.2 (З1): a file without the new names of REQUIRED — load_parser(runner) asks --egress-ensure-parser once
        and returns v26, without k3_post_update; the same through exitsubs._parser."""
        self._v19()
        self.w.script.calls.clear()
        mod = exits.load_parser(self.w.runner)
        self.assertEqual(mod.PARSER_VERSION, 28)
        self.assertEqual(len(self.calls()), 1)
        self._v19(mtime=2)                                                  # another file of 3.3.5 (another mtime)
        self.assertEqual(exitsubs._parser(self.w.runner).PARSER_VERSION, 28)
        self.assertEqual(len(self.calls()), 2)

    def test_a_rewrite_that_does_not_help(self):
        self._v19()
        self.w.runner._handlers.insert(0, ([paths.script_copy(), "--egress-ensure-parser"],
                                           lambda argv, inp: (self.w.script.calls.append(argv[1:]) or 0, "", "")))
        self.w.script.calls.clear()
        for _ in range(2):
            with self.assertRaises(KaskadError) as cm:
                exits.load_parser(self.w.runner)
            self.assertIn("xray_outbound", str(cm.exception))
        self.assertEqual(len(self.calls()), 1)                               # once per file

    def test_no_runner_parser_old_and_the_readers_fall_back(self):
        self._v19()
        write_pool([FIX["hy2"]])
        self.w.script.calls.clear()
        with self.assertRaises(KaskadError):
            exits.load_parser(None)
        got = exits.current({})[0]
        self.assertEqual([(e["kind"], e["mark"]) for e in got], [("xray", 0x301)])
        self.assertEqual(self.calls(), [])

    def test_subscription_keys_of_every_scheme(self):
        install_parser()
        skipped = []
        text = b64("\n".join(FIX[n] for n in ("hy2", "ss_2022", "trojan_tls", "vmess_tls", "tuic", "ss_none")))
        lines = exitsubs.keys_of(text, exits.load_parser(), skipped)
        self.assertEqual(lines, [FIX[n] for n in ("hy2", "ss_2022", "trojan_tls", "vmess_tls")])
        self.assertEqual(sorted(skipped), ["unsupported_scheme:TUIC", "weak_cipher"])
        why = exitsubs.skip_why(skipped + ["unsupported_scheme:TUIC"])
        self.assertIn("TUIC — 2", why)
        self.assertIn("шифр", why)
        with self.assertRaises(KaskadError) as cm:
            exitsubs.keys_of(b64(FIX["tuic"] + "\n" + FIX["tuic"].replace("#TUIC", "#T2")), exits.load_parser(), [])
        self.assertIn("TUIC — 2", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
