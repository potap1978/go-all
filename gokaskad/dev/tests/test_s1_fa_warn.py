"""3.3.7 (REV-s1-rel Б-2): the SSH menu tells every code of a pool key it is told about — egress_awg_warn_text has a
line for the codes of the other xray schemes (pool_problem = link_problem of ss/trojan/vmess/hy2: weak_cipher, no_tls,
dup_endpoint, unsupported_scheme, xray_unchecked) and a plain line for a code it does not know: never an empty «⚠ ».
A Shadowsocks key with rc4-md5 in the pool (an old pool, a file edited by hand) — «problems» of the parser says
weak_cipher, the menu says why."""

import os
import shlex
import shutil
import subprocess
import unittest

from kaskad.gw import exits
from tests.fakes_gw import INSTALL_SH
from tests.test_332lc_codes import sh_function

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src():
    with open(INSTALL_SH, encoding="utf-8") as f:
        sh = f.read()
    i = sh.index("\nt() {") + 1                     # t and tf — one line each
    out = sh[i:sh.index("\n", sh.index("\ntf() {") + 1) + 1]
    out += sh_function(sh, "egress_awg_warn_text")
    for lang in ("ru", "en"):
        out += "source %s\n" % shlex.quote(os.path.join(HERE, "lib/kaskad/i18n/sh/egress.%s.sh" % lang))
    return out


@unittest.skipUnless(shutil.which("bash"), "нет bash")
class WarnTextTest(unittest.TestCase):
    CODES = tuple(exits.LINK_PROBLEMS) + ("xray_unchecked", "xray_legacy", "xray_ext", "bad_addr", "gw_net",
                                          "lost3x", "need31", "loss226", "some_new_code")

    def say(self, lang, code, *more):
        r = subprocess.run(["bash", "-c", _src() + 'K3_LANG="$1"; shift; egress_awg_warn_text "$@"', "x", lang, "3",
                            code] + list(more), stdout=subprocess.PIPE, universal_newlines=True, check=True)
        return r.stdout

    def test_every_code_has_words(self):
        for lang, head in (("ru", "Ключ #3: "), ("en", "Key #3: ")):
            for code in self.CODES:
                txt = self.say(lang, code, "10.8.0.2", "10.8.0.0/24")
                self.assertTrue(txt.startswith(head), (lang, code, txt))
                self.assertGreater(len(txt), len(head) + 20, (lang, code))
                self.assertNotIn("EGRESS_W_", txt, (lang, code))     # tf of a missing key prints the key
                self.assertNotIn(code, txt, (lang, code))           # words, not the code

    def test_weak_cipher_of_a_pool_key_is_told(self):
        from tests.test_334_xray import parser
        import base64
        ss = "ss://" + base64.urlsafe_b64encode(b"rc4-md5:pw").decode().rstrip("=") + "@198.51.100.42:8388#Old"
        self.assertEqual(parser().pool_problem(ss), "weak_cipher")
        self.assertIn("слабым шифром", self.say("ru", "weak_cipher"))
        self.assertIn("weak cipher", self.say("en", "weak_cipher"))


if __name__ == "__main__":
    unittest.main()
