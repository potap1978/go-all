"""Stage 1, T11 (SPEC-stage1-exits §9.7): the secrets of the new keys never reach a journal — util.redact (Python) and
egress_redact of install.sh (bash, sed -E) hide the password of hy2/hysteria2 (auth), trojan, ss (SIP002 base64 and
plain 2022, the old «all in base64» whole), vmess (base64 JSON with the id) whole and obfs-password; «#name» stays,
pcs/pinSHA256 stay (no secret)."""

import os
import re
import subprocess
import unittest

from kaskad import util
from kaskad.i18n import use_lang
from tests.fakes_gw import INSTALL_SH
from tests.test_334_xray import PIN
from tests.test_egress_parse_s1 import FIX, SS22_KEY, UUID, b64

SECRETS = ("hyAuthSecret", "obfsSecret", "ssSecret", "ssPlain", "tjSecret", "tuicSecret", UUID, SS22_KEY,
           UUID.replace("5555", "6666", 1))


def bodies():
    """The base64 bodies of the keys that are base64 whole (ss old, vmess) and of the SIP002 user part."""
    out = []
    for name in ("ss_old", "vmess_tls", "vmess_none", "ss_sip002", "ss_outline", "ss_none"):
        body = FIX[name].split("://", 1)[1].split("#", 1)[0]
        out.append(body.split("@", 1)[0])
    return out


def bash_redact(text):
    """egress_redact of install.sh on ``text`` (stdin), the language of the server — Russian."""
    r = subprocess.run(["bash", "-c", 'source "$1" --lib >/dev/null 2>&1 || exit 97; K3_LANG=ru; egress_redact',
                        "_", INSTALL_SH], input=text, capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8"))
    if r.returncode == 97:
        raise unittest.SkipTest("install.sh --lib did not load")
    return r.stdout


class RedactTest(unittest.TestCase):
    def setUp(self):
        use_lang("ru")
        self.addCleanup(use_lang, "ru")

    def check(self, out, src):
        for s in SECRETS:
            self.assertNotIn(s, out, src)
        for b in bodies():
            self.assertNotIn(b, out, src)
        if PIN in src:
            self.assertIn(PIN, out)                                          # a pin is no secret
        for name in re.findall(r"#([^\s]+)", src):
            self.assertIn("#" + name, out)                                   # the name of the exit stays

    def test_python_every_scheme(self):
        for name, line in FIX.items():
            if name == "tuic":
                continue                                                      # the pool never takes it
            out = util.redact("ошибка ключа %s в пуле" % line)
            self.check(out, line)
            self.assertIn("скрыт", out, name)

    def test_inside_quotes_and_json(self):
        text = '{"line": "%s", "x": \'%s\'}' % (FIX["vmess_tls"], FIX["ss_old"])
        out = util.redact(text)
        self.check(out, text)
        self.assertTrue(out.endswith("}"))

    def test_nothing_else_touched(self):
        for text in ("https://sub.example.com/ss://path", "class://x", "pcs=" + PIN, "address 203.0.113.5:443"):
            self.assertEqual(util.redact(text), text)

    def test_bash_egress_redact_same_rules(self):
        lines = [l for n, l in FIX.items() if n != "tuic"]
        text = "\n".join("skip key (%s) %s" % (n, l) for n, l in zip(FIX, lines)) + "\n"
        out = bash_redact(text)
        self.assertEqual(len(out.splitlines()), len(lines))
        self.check(out, text)
        self.assertIn("<скрыт>", out)
        self.assertEqual(bash_redact("ничего секретного: example.com:443\n"), "ничего секретного: example.com:443\n")


if __name__ == "__main__":
    unittest.main()


def upper_scheme(line):
    s, _, rest = line.partition("://")
    return s.upper() + "://" + rest, s.capitalize() + "://" + rest


class UpperCaseSchemeTest(unittest.TestCase):
    """REV-s1-t11 З1: the parser takes a scheme in any case («VLESS://», «Trojan://») — the masks too."""

    def setUp(self):
        use_lang("ru")
        self.addCleanup(use_lang, "ru")

    def lines(self):
        out = []
        for n, l in FIX.items():
            if n != "tuic":
                out.extend(upper_scheme(l))
        return out + ["VPN://" + "A" * 40, "AWG://" + "B" * 40]

    def test_python(self):
        for line in self.lines():
            out = util.redact("ключ %s" % line)
            RedactTest.check(self, out, line)
            self.assertNotIn("A" * 40, out)
            self.assertNotIn("B" * 40, out)

    def test_bash(self):
        lines = self.lines()
        out = bash_redact("\n".join("key %s" % l for l in lines) + "\n")
        RedactTest.check(self, out, "\n".join(lines))
        self.assertNotIn("A" * 40, out)
        self.assertNotIn("B" * 40, out)
        self.assertNotIn("obfsSecret", out)

    def test_the_parser_writes_the_scheme_in_lower_case(self):
        from tests.test_334_xray import parser
        p = parser()
        got = p.extract("\n".join(upper_scheme(FIX["trojan_tls"])[:1] + upper_scheme(FIX["hy2"])[1:]))
        self.assertEqual(got, [FIX["trojan_tls"], FIX["hy2"]])
