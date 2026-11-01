"""Kaskad 3.3.7.8 — VLESS with its own certificate (TLS) by default, Reality as the backup: one version, and
«Что нового» of 3.3.7.8 for people, tied to what the code does (owner 20.10: working servers are not switched
silently)."""
import re
import subprocess
import unittest

import kaskad
from tests.test_3371_release import entry, text


class Release3378Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.8")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.8")
        self.ru, self.en = re.sub(r"\s+", " ", self.ru), re.sub(r"\s+", " ", self.en)

    def test_one_version(self):
        # 3.3.7.9 is above it now (tests/test_3379_release.py): the version is its, the heading names 3.3.7.8 too
        self.assertTrue(kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.7, 3\.3\.7\.8[ ,]")
        self.assertLess(self.readme.index("**3.3.7.8 — "), self.readme.index("**3.3.7.7 — "))
        self.assertNotIn("Следующий выпуск", self.readme)
        self.assertNotIn("Next release", self.readme)
        order = subprocess.run(["sort", "-V"], input="3.3.8\n3.3.7.8\n3.3.7.7\n", capture_output=True, text=True).stdout
        self.assertEqual(order.split(), ["3.3.7.7", "3.3.7.8", "3.3.8"])

    def test_russian_and_english(self):
        for words in ("Маскировка VLESS", "Reality", "цену", "Без согласия ничего не меняется",
                      "режим VLESS не меняется, пока вы его не выберете"):
            self.assertIn(words, self.ru)
        for words in ("VLESS masking", "Reality", "the price", "nothing changes",
                      "the VLESS mode does not change until you choose it"):
            self.assertIn(words, self.en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))
        self.assertNotIn("fallback", self.en)
        for part in (self.ru, self.en):
            self.assertNotRegex(part, r"\.ru\b")
            self.assertIsNone(re.search(r"\b(REV|SEC|Ш\d|З\d|П\d|Н-\d)\b", part))

    def test_tied_to_the_code(self):
        """REV-3378-final З1: the names «Что нового» gives are the names the code shows (the SSH menu, the panel, the
        wizard); the old items are gone from the code; the server is «сервер» / “server”, not «узел» / “node”."""
        sh = dict((lang, _sh(text("lib/kaskad/i18n/sh/gwvless.%s.sh" % lang), lang)) for lang in ("ru", "en"))
        js = dict((lang, _js(text("lib/kaskad/panel/static/i18n-%s.js" % lang))) for lang in ("ru", "en"))
        setup = dict((lang, _js(text("lib/kaskad/panel/static/setup/setup-i18n-%s.js" % lang))) for lang in ("ru", "en"))
        self.assertEqual((sh["ru"]["GWV_MENU_MODE"], sh["en"]["GWV_MENU_MODE"]), ("Маскировка VLESS", "VLESS masking"))
        self.assertIn("«%s» в SSH-меню" % sh["ru"]["GWV_MENU_MODE"], self.ru)
        self.assertIn("“%s” in the SSH menu" % sh["en"]["GWV_MENU_MODE"], self.en)
        self.assertIn("«1) %s»" % sh["ru"]["GWV_MODE_TLS_ITEM"], self.ru)
        self.assertIn("“1) %s”" % sh["en"]["GWV_MODE_TLS_ITEM"], self.en)
        self.assertIn("окно «%s» (Протоколы → VLESS → «%s»)" % (sh["ru"]["GWV_MENU_MODE"], js["ru"]["vl.maskBtn"]), self.ru)
        self.assertIn("“%s” window (Protocols → VLESS → “%s”)" % (sh["en"]["GWV_MENU_MODE"], js["en"]["vl.maskBtn"]),
                      self.en)
        self.assertEqual((js["ru"]["vl.toTls"], js["en"]["vl.toTls"]), ("Перейти на TLS", "Switch to TLS"))
        self.assertIn("«%s»" % js["ru"]["vl.phrase"].split(",")[0], self.ru)            # «Понимаю риски»
        self.assertIn("“%s”" % js["en"]["vl.phrase"].split(",")[0], self.en)
        self.assertIn("«%s»" % setup["ru"]["dm.site.decline"].split(" →")[0], self.ru)  # «Не включать сайт»
        self.assertIn("“%s”" % setup["en"]["dm.site.decline"].split(" →")[0], self.en)
        # the old items: neither a key nor a text of the menu or the panel
        for src in (text("install.sh"), text("lib/kaskad/i18n/sh/gwvless.ru.sh"), text("lib/kaskad/i18n/sh/gwvless.en.sh"),
                    text("lib/kaskad/panel/static/i18n-ru.js"), text("lib/kaskad/panel/static/i18n-en.js")):
            for gone in ("GWV_MENU_MASK", "GWV_MENU_SYNC", "vl.syncBtn"):
                self.assertNotIn(gone, src)
            self.assertIsNone(re.search(r"""['"]Режим по сайту узла""", src))
        # the backup Reality is «запасной» / “backup” in the texts of VLESS too, never “fallback” (REV-3378-final п.3)
        for name in ("gw_in_vless", "gw_ops", "gw_cli"):
            self.assertIsNone(re.search(r"(?i)fallback(?: Reality|\)| \(with)", text("lib/kaskad/i18n/catalog/en/%s.json" % name)))
        # «узел» / “node” — not in the entry (the old item is quoted: «Режим по сайту узла»)
        self.assertIsNone(re.search(r"(?i)\bnodes?\b", self.en))
        self.assertIsNone(re.search(r"\b[Уу]з(ел|л[а-яё]*)\b", re.sub(r"«[^»]*»", "", self.ru)))


def _sh(src, lang):
    """M_<lang>_KEY='text' of an i18n/sh file -> {KEY: text}."""
    return dict(re.findall(r"(?m)^M_%s_([A-Z0-9_]+)='([^']*)'" % lang, src))


def _js(src):
    """"key": "text" lines of an i18n-*.js file -> {key: text}."""
    return dict(re.findall(r'(?m)^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', src))


if __name__ == "__main__":
    unittest.main()
