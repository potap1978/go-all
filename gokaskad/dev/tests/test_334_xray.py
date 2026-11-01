"""3.3.4: the pinned xray (one version, SHA-256, never an xray of the server or of PATH) and the keys with allowInsecure
(the field is never written; pcs/pinSHA256 -> pinnedPeerCertSha256; without them only that key is left out, the rest
of a paste or a subscription goes on), the catalog of Keenetic (L2TP before IKEv2, IKEv2 unverified).
The bash side — tools/egress_xray_selftest.sh and tools/k3_install_test.sh (sections 11 and «--egress-add»)."""

import base64
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from kaskad import paths
from kaskad.errors import KaskadError
from kaskad.gw import exits, exitsubs, ops, xray
from kaskad.i18n import use_lang
from tests import realbin
from tests.fakes_gw import FAKE_XRAY, VLESS_B, install_parser, parser_source, touch_exec, write_pool
from tests.gwcase import GwCase
from tests.test_gw_exitsubs import IP_URL, SubsCase, b64

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(ROOT, "install.sh")
PIN = "6fbc15ba46dfed152ad6c8d2129dd774707dd667a9ab4965476fa0f79ba82670"
PIN_COLON = ":".join(PIN[i:i + 2].upper() for i in range(0, 64, 2))
PIN_B64 = base64.b64encode(bytes.fromhex(PIN)).decode()
BASE = "vless://11111111-2222-3333-4444-%012d@198.51.100.%d:8443?type=tcp&security=tls&sni=a.example.com"


def key(n, extra="", name=None):
    return BASE % (n, 20 + n) + extra + "#" + (name or "k%d" % n)


_PARSER = {}


def parser():
    """egress_parse.py exactly as install.sh writes it, as a module."""
    if "m" not in _PARSER:
        d = tempfile.mkdtemp()
        p = os.path.join(d, "egress_parse.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(parser_source())
        spec = importlib.util.spec_from_file_location("ep334", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _PARSER["m"], _PARSER["path"] = m, p
    return _PARSER["m"]


class Quiet(unittest.TestCase):
    """The parser says «skip vless (…)» on stderr — not in the output of the tests."""

    def setUp(self):
        self._q = exits._quiet()
        self._q.__enter__()

    def tearDown(self):
        self._q.__exit__(None, None, None)


def tls_of(ob):
    return ob["streamSettings"]["tlsSettings"]


class ParserInsecureTest(Quiet):
    def test_version(self):
        self.assertGreaterEqual(parser().PARSER_VERSION, 15)

    def test_allow_insecure_is_never_written(self):
        p = parser()
        for flag in ("allowInsecure=1", "allowInsecure=true", "insecure=1", "allow_insecure=1", "allowInsecure=0", ""):
            for pin in ("&pcs=" + PIN, ""):
                line = key(1, "&" + flag + pin if flag else pin)
                ob = p.vless_outbound(line, "t")
                if ob:
                    self.assertNotIn("allowInsecure", json.dumps(ob), line)

    def test_insecure_without_pin_is_refused_alone(self):
        p = parser()
        for flag in ("allowInsecure=1", "allowInsecure=True", "insecure=1", "allow_insecure=yes"):
            line = key(1, "&" + flag)
            self.assertEqual(p.vless_problem(line), "insecure", line)
            self.assertIsNone(p.vless_outbound(line, "t"))
        # off, or not TLS: nothing to refuse (REALITY ignores the flag)
        self.assertEqual(p.vless_problem(key(1, "&allowInsecure=0")), "")
        reality = key(1, "&allowInsecure=1").replace("security=tls", "security=reality") + "&pbk=x&sid=1"
        self.assertEqual(p.vless_problem(reality), "")

    def test_pin_forms_become_hex(self):
        p = parser()
        for extra in ("&pcs=" + PIN, "&pcs=" + PIN.upper(), "&pcs=" + PIN_COLON, "&pcs=" + PIN_B64.replace("+", "%2B"),
                      "&pinSHA256=" + PIN, "&allowInsecure=1&pcs=" + PIN):
            ob = p.vless_outbound(key(1, extra), "t")
            self.assertIsNotNone(ob, extra)
            self.assertEqual(tls_of(ob)["pinnedPeerCertSha256"], PIN, extra)
            self.assertNotIn("allowInsecure", tls_of(ob))
        two = PIN + "," + "ab" * 32
        self.assertEqual(tls_of(p.vless_outbound(key(1, "&pcs=" + two), "t"))["pinnedPeerCertSha256"], two)

    def test_bad_pin_is_refused(self):
        p = parser()
        for bad in ("zz", PIN[:-2], PIN + "00", base64.b64encode(b"x" * 31).decode()):
            line = key(1, "&pcs=" + bad)
            self.assertEqual(p.vless_problem(line), "bad_pin", bad)
            self.assertIsNone(p.vless_outbound(line, "t"))

    def test_vcn_checks_by_name(self):
        p = parser()
        ob = p.vless_outbound(key(1, "&allowInsecure=1&vcn=real.example.com"), "t")
        self.assertEqual(tls_of(ob)["verifyPeerCertByName"], "real.example.com")
        self.assertNotIn("allowInsecure", tls_of(ob))

    def test_extract_report_leaves_out_one_key(self):
        p = parser()
        bad, good, pinned, badpin = (key(1, "&allowInsecure=1", "Плохой"), key(2), key(3, "&insecure=1&pcs=" + PIN),
                                     key(4, "&pcs=zz", "Кривой"))
        lines, skipped = p.extract_report("\n".join([bad, good, pinned, badpin]))
        self.assertEqual(lines, [good, pinned])
        self.assertEqual([(x["code"], x["name"]) for x in skipped], [("insecure", "Плохой"), ("bad_pin", "Кривой")])
        self.assertEqual(p.extract("\n".join([bad, good])), [good])
        # a base64 subscription — the same
        lines, skipped = p.extract_report(base64.b64encode("\n".join([bad, good]).encode()).decode())
        self.assertEqual((lines, len(skipped)), ([good], 1))

    def test_extract_cli_prints_reasons_not_keys(self):
        parser()
        bad = key(1, "&allowInsecure=1", "Bad%09name")
        r = subprocess.run([sys.executable, "-I", _PARSER["path"], "extract"], input=bad + "\n" + key(2) + "\n",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.stdout, key(2) + "\n")
        self.assertEqual(r.stderr, "K3SKIP\tinsecure\tBad name\n")
        self.assertNotIn("11111111", r.stderr)

    def test_skip_name_cannot_drive_the_terminal(self):
        """The name of a key comes from someone else's link and is printed with echo -e: no backslash, no control byte."""
        bad = key(1, "&allowInsecure=1", "X%5Ce%5B31mRED%5Cx07%1B%5B0m")
        _, skipped = parser().extract_report(bad)
        name = skipped[0]["name"]
        self.assertNotIn("\\", name)
        self.assertFalse(re.search(r"[\x00-\x1f\x7f]", name), name)

    def test_problems_cli_numbers_the_pool(self):
        parser()
        pool = "\n".join([key(1), "", key(2, "&allowInsecure=1"), "awg://e30", key(3, "&pcs=zz")]) + "\n"
        r = subprocess.run([sys.executable, "-I", _PARSER["path"], "problems"], input=pool, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True)
        # 3.3.5.1: «awg://e30» — запись без Endpoint — тоже ключ, который не подключится (bad_link)
        self.assertEqual(r.stdout, "2\tinsecure\n3\tbad_link\n4\tbad_pin\n")

    def test_config_skips_an_old_pool_line(self):
        """A key with allowInsecure added before 01.06.2026 (a warning then) and still in the pool: the config leaves
        it out instead of failing whole."""
        p = parser()
        cfg, n = p.build_config([key(1, "&allowInsecure=1"), key(2, "&pcs=" + PIN)], 10808, "http://x/", "leastPing",
                                "10s", 100, 10851, 0)
        self.assertEqual(n, 1)
        self.assertNotIn("allowInsecure", json.dumps(cfg))
        self.assertIn(PIN, json.dumps(cfg))

    @unittest.skipUnless(realbin.xray_path(), "нет xray (bash tools/fetch_test_bins.sh)")
    def test_real_xray_accepts_the_config(self):
        p = parser()
        cfg, n = p.build_config([key(1, "&allowInsecure=1"), key(2, "&allowInsecure=1&pcs=" + PIN_COLON),
                                 key(3, "&vcn=b.example.com&allowInsecure=1"), key(4)],
                                10808, "http://x/", "leastPing", "10s", 100, 10851, 0)
        self.assertEqual(n, 3)
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "c.json")
            with open(f, "w") as fh:
                json.dump(cfg, fh)
            r = subprocess.run([realbin.xray_path(), "-test", "-c", f], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stdout[-400:])


REMOVED = ('Failed to start: main: failed to load config files: [c.json] > infra/conf: failed to build outbound config '
           'with tag x > infra/conf: Failed to build TLS config. > common/errors: The feature "allowInsecure" has been '
           'removed and migrated to "pinnedPeerCertSha256". Please update your config(s) according to release note '
           'and documentation.\n')


class XrayWhyTest(Quiet):
    def test_removed_allow_insecure_in_words(self):
        p = parser()
        with mock.patch.dict(os.environ, {"KASKAD_LANG": "ru"}):
            ru = p.xray_why(REMOVED)
        with mock.patch.dict(os.environ, {"KASKAD_LANG": "en"}):
            en = p.xray_why(REMOVED)
        self.assertIn("allowInsecure", ru)
        self.assertIn("отпечаток", ru)
        self.assertIn("fingerprint", en)
        for w in (ru, en):
            self.assertNotIn("<…>", w)
        self.assertEqual(p.xray_code(REMOVED), "removed:allowinsecure")
        other = REMOVED.replace('"allowInsecure"', '"verifyPeerCertInNames"')
        self.assertEqual(p.xray_code(other), "removed:verifypeercertinnames")

    def test_core_words_of_the_marks(self):
        with use_lang("ru"):
            lines = ["Конфиг xray невалиден: …", "@K3:egress_fail:xray_invalid:removed:allowinsecure"]
            self.assertIn("allowInsecure", ops._fail_text(lines, 1, ["xray_invalid", "removed", "allowinsecure"]))
            self.assertIn("verifypeercertinnames",
                          ops._fail_text(lines, 1, ["xray_invalid", "removed", "verifypeercertinnames"]))
            self.assertIn("allowInsecure", ops._fail_text(["x", "@K3:egress_fail:skipped:insecure"], 1,
                                                          ["skipped", "insecure"]))
            warn = ops._script_warnings(["Пропущен ключ «x»", "@K3:egress_warn:skipped:insecure:2",
                                         "@K3:egress_warn:skipped:bad_pin:1"])
            self.assertEqual(len(warn), 2)
            self.assertIn("Пропущено ключей: 2", warn[0])
            self.assertIn("pcs", warn[1])
            warn = ops._script_warnings(["⚠ Ключ #2: …", "@K3:egress_warn:vless_key:2:insecure"])
            self.assertIn("Ключ #2: VLESS без проверки сертификата", warn[0])
            self.assertNotIn("Остальные добавлены", " ".join(warn))
        with use_lang("en"):
            warn = ops._script_warnings(["@K3:egress_warn:skipped:insecure:3", "@K3:egress_warn:vless_key:5:bad_pin"])
            self.assertIn("Keys skipped: 3", warn[0])
            self.assertIn("Key #5", warn[1])


class PreviewAndPoolTest(GwCase):
    def setUp(self):
        super().setUp()
        install_parser()
        self.p = exits.load_parser()

    def test_preview_shows_the_left_out_key_with_its_reason(self):
        rows = exits.preview(self.p, "\n".join([key(1, "&allowInsecure=1", "Bad"), key(2)]))
        self.assertEqual(len(rows), 2)
        bad = [r for r in rows if r.get("problem")]
        self.assertEqual(len(bad), 1)
        self.assertIn("allowInsecure", bad[0]["problem"])

    def test_pool_line_gets_its_reason(self):
        write_pool([key(1, "&allowInsecure=1"), key(2)])
        es = exits.pool(self.w.runner)
        self.assertEqual(es[0].get("problem_code"), "problem.vless_insecure")
        self.assertEqual(es[1].get("kind"), "xray")


class SubscriptionSkipTest(SubsCase):
    def test_one_bad_key_does_not_stop_the_subscription(self):
        bad = key(1, "&allowInsecure=1", "Bad")
        res = self.add(IP_URL, b64([bad, VLESS_B, key(3, "&pcs=" + PIN)]))
        self.assertEqual(self.pool(), [VLESS_B, key(3, "&pcs=" + PIN)])
        self.assertEqual(res["result"]["skipped"], 1)
        self.assertIn("Пропущено ключей: 1", res["result"]["text"])
        self.assertIn("allowInsecure", res["result"]["text"])
        self.assertEqual(res["sub"]["skipped"], 1)
        sub = exitsubs.load()["subs"][0]
        self.assertEqual(sub["skip_codes"], ["insecure"])               # codes: words in the reader's language
        self.assertIn("пропущено ключей: 1", exitsubs.state_text(sub, self.now))
        with use_lang("en"):
            self.assertIn("keys skipped: 1 (the certificate check is off", exitsubs.state_text(sub, self.now))
        self.assertIn("пропущено 1", self.journal())
        self.assertNotIn("11111111-2222", self.journal())
        # the next refresh: the same — no failure, the pool stays
        r = self.refresh()["results"][0]
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["skipped"], 1)

    def test_only_bad_keys_is_a_failure_in_words(self):
        with self.assertRaises(KaskadError) as cm:
            self.add(IP_URL, b64([key(1, "&allowInsecure=1"), key(2, "&insecure=1")]))
        self.assertIn("Из 2 ключей подписки xray не примет ни одного", str(cm.exception))
        self.assertIn("allowInsecure", str(cm.exception))
        self.assertEqual(self.pool(), [])


def _install_pins():
    """{arch: (zip, zip sha, file sha)} of egress_xray_pin and EGRESS_XVER of install.sh."""
    with open(INSTALL, encoding="utf-8") as f:
        text = f.read()
    ver = re.search(r'^EGRESS_XVER="v([0-9.]+)"$', text, re.M).group(1)
    body = re.search(r"^egress_xray_pin\(\) \{\n(.*?)\n\}", text, re.M | re.S).group(1)
    pins = {}
    for m in re.finditer(r'^\s+([\w|]+)\)\s+echo "(\S+) ([0-9a-f]{64}) ([0-9a-f]{64})"', body, re.M):
        for a in m.group(1).split("|"):
            pins[a] = (m.group(2), m.group(3), m.group(4))
    return ver, pins


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class PinnedVersionTest(unittest.TestCase):
    def test_one_version_everywhere(self):
        ver, pins = _install_pins()
        self.assertEqual(ver, xray.XRAY_VERSION)
        self.assertEqual(ver, "26.3.27")
        self.assertEqual(realbin.XRAY_VERSION, "v" + ver)
        for a, sha in xray.XRAY_SHA256.items():
            self.assertEqual(pins[a][2], sha, a)
        self.assertEqual(set(pins), {"x86_64", "amd64", "aarch64", "arm64", "armv7l"})
        with open(os.path.join(ROOT, "bin", "VERSIONS.txt"), encoding="utf-8") as f:
            doc = f.read()
        for z, zs, bs in pins.values():
            self.assertRegex(doc, r"(?m)^%s +sha256 %s$" % (re.escape(z), zs))
            self.assertIn(bs, doc)


class PinnedXrayTest(GwCase):
    def test_never_an_xray_of_the_server_or_of_path(self):
        os.unlink(paths.egress_xray())
        with mock.patch.object(self.w.runner, "which", lambda name: "/usr/local/bin/xray"):
            self.assertIsNone(xray.binary(self.w.runner))
        src = _read("lib", "kaskad", "gw", "xray.py")
        self.assertNotIn('which("xray")', src)

    def test_is_pinned_by_sha256(self):
        self.assertTrue(xray.is_pinned(paths.egress_xray()))       # GwCase: the fake file is the pinned one
        with open(paths.egress_xray(), "wb") as f:
            f.write(b"#!/bin/sh\n# 25.12.8\n")
        self.assertFalse(xray.is_pinned(paths.egress_xray()))
        with mock.patch.object(xray, "arch", lambda: "riscv64"):
            touch_exec(paths.egress_xray())
            self.assertFalse(xray.is_pinned(paths.egress_xray()))
        self.assertEqual(xray.XRAY_SHA256[xray.arch()], hashlib.sha256(FAKE_XRAY).hexdigest())

    def test_own_xray_of_another_version_is_replaced_through_the_script(self):
        with open(paths.egress_xray(), "wb") as f:
            f.write(b"#!/bin/sh\n# 25.12.8\n")
        self.w.script.calls[:] = []
        self.assertEqual(xray.ensure_binary(self.w.runner), paths.egress_xray())
        self.assertEqual(self.w.script.calls, [["--egress-ensure-xray"]])
        self.assertTrue(xray.is_pinned(paths.egress_xray()))
        xray.ensure_binary(self.w.runner)
        self.assertEqual(self.w.script.calls, [["--egress-ensure-xray"]])     # pinned now: no more calls

    def test_own_xray_stays_when_the_pinned_one_cannot_come(self):
        old = b"#!/bin/sh\n# 25.12.8\n"
        with open(paths.egress_xray(), "wb") as f:
            f.write(old)
        self.w.script.xray_ok = False
        self.w.script.calls[:] = []
        self.assertEqual(xray.ensure_binary(self.w.runner), paths.egress_xray())   # works on, the old own one
        with open(paths.egress_xray(), "rb") as f:
            self.assertEqual(f.read(), old)
        xray.ensure_binary(self.w.runner)
        self.assertEqual(len(self.w.script.calls), 1)                  # the next try — after FETCH_RETRY
        xray.ensure_binary(self.w.runner, force=True)
        self.assertEqual(len(self.w.script.calls), 2)

    def test_reality_min_client_ver_for_mihomo(self):
        """26.3.27 sets no minClientVer by default (infra/conf/transport_internet.go): mihomo (FlClash, Clash Verge)
        sends 1.8.2 and connects. The next releases default it to 26.3.27 — whoever moves the pin past 26.3.27 must set
        minClientVer in the REALITY inbound explicitly (gw/inbounds/vless.py), and this test says so."""
        v = tuple(int(x) for x in xray.XRAY_VERSION.split("."))
        src = _read("lib", "kaskad", "gw", "inbounds", "vless.py")
        if v > (26, 3, 27):
            self.assertIn('"minClientVer"', src, "xray > 26.3.27: задайте minClientVer в REALITY явно (mihomo шлёт 1.8.2)")
        else:
            self.assertNotIn('"minClientVer"', src)


class KeeneticCatalogTest(unittest.TestCase):
    def test_l2tp_before_ikev2_and_ikev2_unverified(self):
        with open(os.path.join(ROOT, "lib", "kaskad", "gw", "install_apps.json"), encoding="utf-8") as f:
            doc = json.load(f)
        rows = doc["support"]["keenetic"]
        order = [r["proto"] for r in rows]
        self.assertLess(order.index("l2tp"), order.index("ikev2"))
        ike = rows[order.index("ikev2")]
        self.assertEqual(ike["level"], "native")
        self.assertIs(ike.get("unverified"), True)
        self.assertIn("сертификат центра Kaskad", ike["note"]["ru"])
        self.assertIn("L2TP/IPsec или AmneziaWG", ike["note"]["ru"])
        self.assertIn("Kaskad CA certificate", ike["note"]["en"])
        self.assertIn("L2TP/IPsec or AmneziaWG", ike["note"]["en"])


if __name__ == "__main__":
    unittest.main()
