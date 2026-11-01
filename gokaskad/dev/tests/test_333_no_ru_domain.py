"""Kaskad 3.3.3 — the owner 2026-10-12: the wizard and the panel never suggest a domain in the zone .ru/.рф — just
«домен». Examples of the user's own domain are example.com; real Russian sites (ozon.ru, gosuslugi.ru — routing rules
«RU напрямую») are not examples of a domain and stay.

Allowed: the IDN hint «можно кириллицей: впн.пример.рф» (site.model.domain_hint) and the scene «idn» of the panel mock
(vpn.пример.рф) — they show that a name in Cyrillic works; a fully Cyrillic name exists only in a Cyrillic zone, so
this is about Cyrillic letters, not advice to take the zone .рф. The main example next to it is example.com.
"""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib", "kaskad")
STATIC = os.path.join(LIB, "panel", "static")

FILES = (glob.glob(os.path.join(STATIC, "setup", "setup-i18n-*.js")) + glob.glob(os.path.join(STATIC, "i18n-*.js"))
         + glob.glob(os.path.join(STATIC, "dev-mock*.js")) + glob.glob(os.path.join(STATIC, "setup", "dev-*.js"))
         + glob.glob(os.path.join(LIB, "i18n", "catalog", "*", "*.json"))
         # R-333 П-7: the pages themselves, the SSH texts and the single file too
         + glob.glob(os.path.join(STATIC, "*.js")) + glob.glob(os.path.join(STATIC, "setup", "*.js"))
         + glob.glob(os.path.join(LIB, "i18n", "sh", "*.sh")) + [os.path.join(ROOT, "install.sh")])
FILES = sorted(set(FILES))
# an example of the user's own domain in the zone .ru/.рф: example.ru, ваш-домен.ru, vpn.example.ru, k3.что-то.ru…
OWN = re.compile(r"(?i)(?:\b|(?<=[\s«“\"'(]))(?:[\w-]+\.)*(?:example|пример|ваш-домен|your-domain|my-?domain|mysite)"
                 r"(?:\.[\w-]+)*\.(?:ru|рф)\b|\b(?:vpn|k3|m6|panel|sub|впн)\.[\w-]+\.(?:ru|рф)\b")
ZONE = re.compile(r"(?i)зоне\s+\.(?:ru|рф)|\.(?:ru|рф)\s+zone|zone\s+\.(?:ru|рф)")
ALLOWED = {"впн.пример.рф", "vpn.пример.рф"}


class NoRuDomainExamplesTest(unittest.TestCase):
    def test_files_found(self):
        self.assertGreaterEqual(len(FILES), 8, FILES)

    def test_no_own_domain_in_ru_zone(self):
        bad = []
        for f in FILES:
            with open(f, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            for n, line in enumerate(lines, 1):
                for m in OWN.finditer(line):
                    if m.group(0).lower() not in ALLOWED:
                        bad.append("%s:%d %s" % (os.path.relpath(f, ROOT), n, m.group(0)))
                if ZONE.search(line):
                    bad.append("%s:%d zone: %s" % (os.path.relpath(f, ROOT), n, line.strip()[:120]))
        self.assertEqual(bad, [])

    def test_the_rule_catches_what_it_should(self):
        for s in ("vpn.ваш-домен.ru", "example.ru", "k3.example.ru", "your-domain.ru", "впн.пример.рф"):
            self.assertTrue(OWN.search("адрес будет " + s), s)
        for s in ("ozon.ru", "gosuslugi.ru", "adminvps.ru", "example.com", "vpn.example.com"):
            self.assertFalse(OWN.search("сайт " + s), s)
        self.assertTrue(ZONE.search("домен в зоне .ru — около 5 минут"))


if __name__ == "__main__":
    unittest.main()
