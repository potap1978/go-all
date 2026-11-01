"""BC5 (SPEC-browser-channel §6.4, З-11): the texts of the card «От вашего устройства до сервера» —
pages/speed-i18n-{ru,en}.js — have no network jargon. The match is by the border of a word, case-insensitive: whole
words ping, rtt, tcp, tls, http, cpu, latency, jitter, bufferbloat; stems пинг, throttl, дрожани only from the start of
a word (so «stopping» is not «ping»). Also: the same keys in both, every form of the limits through «|» (RU three,
EN two), {wait}/{gb}/{n} where the text needs them, and the owner's 24.10 words («users on the VPN»)."""

import os
import re
import unittest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib", "kaskad",
                      "panel", "static")
JARGON = re.compile(r"(?<!\w)(?:ping|rtt|tcp|tls|http|cpu|latency|jitter|bufferbloat)(?!\w)|(?<!\w)(?:пинг|throttl|дрожани)",
                    re.I | re.U)
ENTRY = re.compile(r'^\s*"(panel\.speed\.[A-Za-z0-9_.]+)":\s*"((?:[^"\\]|\\.)*)"', re.M)


def texts(lang):
    with open(os.path.join(STATIC, "pages", "speed-i18n-%s.js" % lang), encoding="utf-8") as f:
        return dict(ENTRY.findall(f.read()))


class SpeedTextsTest(unittest.TestCase):
    def test_the_rule_itself(self):
        self.assertIsNone(JARGON.search("stopping and keeping"))
        self.assertIsNotNone(JARGON.search("ping"))
        self.assertIsNotNone(JARGON.search("Пинг вырос"))
        self.assertIsNotNone(JARGON.search("CPU"))
        self.assertIsNone(JARGON.search("httpd"))

    def test_no_jargon_in_ru_and_en(self):
        for lang in ("ru", "en"):
            tx = texts(lang)
            self.assertGreater(len(tx), 50, lang)
            bad = {k: v for k, v in tx.items() if JARGON.search(v)}
            self.assertEqual(bad, {}, lang)

    def test_same_keys_and_forms(self):
        ru, en = texts("ru"), texts("en")
        self.assertEqual(sorted(ru), sorted(en))
        for k in ("panel.speed.lim.hour", "panel.speed.lim.day"):
            self.assertEqual(len(ru[k].split("|")), 3, k)
            self.assertEqual(len(en[k].split("|")), 2, k)
            for form in ru[k].split("|") + en[k].split("|"):
                self.assertIn("{n}", form, k)
                self.assertIn("{wait}", form, k)
        for k, need in (("panel.speed.lim.traffic", ("{gb}", "{wait}")), ("panel.speed.other", ("{wait}",)),
                        ("panel.speed.price", ("{gb}",)), ("panel.speed.mobile", ("{gb}",)), ("panel.speed.grows", ("{n}",))):
            for lang, tx in (("ru", ru), ("en", en)):
                for v in need:
                    self.assertIn(v, tx[k], "%s %s" % (lang, k))
        self.assertEqual([v for v in ru.values() if "|" in v and not v.startswith(("За последний", "Сегодня уже"))], [])

    def test_owner_words(self):
        """§6.4 and the owner's 24.10 choice: «users on the VPN», not «people»; the line under the title is the first
        sentence of the warning; the price follows the server's limits ({gb})."""
        ru, en = texts("ru"), texts("en")
        self.assertIn("users on the VPN", en["panel.speed.vpn"])
        self.assertNotIn("people", " ".join(en.values()).lower())
        self.assertTrue(ru["panel.speed.warn"].startswith(ru["panel.speed.sub"]))
        self.assertTrue(en["panel.speed.warn"].startswith(en["panel.speed.sub"]))


if __name__ == "__main__":
    unittest.main()
