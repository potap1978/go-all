"""Kaskad 3.3.7.9 — the history of people's activity (metrics.db schema 4), Russian exits (Q3, Q5) and the VLESS windows
as designed: one version, and «Что нового» of 3.3.7.9 for people, tied to what the code does."""
import re
import subprocess
import unittest

import kaskad
from kaskad.gw import metrics
from tests.test_3371_release import entry, text


class Release3379Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.9")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.9")
        self.ru, self.en = re.sub(r"\s+", " ", self.ru), re.sub(r"\s+", " ", self.en)

    def test_one_version(self):
        # 3.3.7.10 is above it now (tests/test_33710_release.py): the version is its, the heading names 3.3.7.9 too
        self.assertTrue(kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.8, 3\.3\.7\.9[ ,]")
        self.assertLess(self.readme.index("**3.3.7.9 — "), self.readme.index("**3.3.7.8 — "))
        order = subprocess.run(["sort", "-V"], input="3.3.8\n3.3.7.9\n3.3.7.8\n", capture_output=True, text=True).stdout
        self.assertEqual(order.split(), ["3.3.7.8", "3.3.7.9", "3.3.8"])

    def test_tied_to_the_code(self):
        js = {lang: dict(re.findall(r'(?m)^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)"',
                                    text("lib/kaskad/panel/static/i18n-%s.js" % lang))) for lang in ("ru", "en")}
        self.assertIn("«Настройки › %s», «%s»" % (js["ru"]["st.storage"], js["ru"]["sto.keep"]), self.ru)
        self.assertIn('"Settings › %s", "%s"' % (js["en"]["st.storage"], js["en"]["sto.keep"]), self.en)
        # REV-3379 З-1: «gw activity» only reads (the switch is policy_ops.activity_set, the panel only) — no promise
        # to turn it off from the command line
        self.assertIn("Посмотреть из командной строки: `kaskadctl gw activity <человек|устройство>`", self.ru)
        self.assertIn("To view it from the command line: `kaskadctl gw activity <person|device>`", self.en)
        self.assertNotIn("То же из командной строки", self.ru)
        self.assertNotIn("The same from the command line", self.en)
        self.assertEqual(metrics.SCHEMA, 4)                                     # dev_* tables (SPEC-activity)

    def test_russian_and_english(self):
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))
        for part in (self.ru, self.en):
            self.assertNotRegex(part, r"\.ru\b")
            self.assertIsNone(re.search(r"\b(REV|SEC|DQA|Q\d|ACT-\d+|AC\d|М-\d|Н-\d)\b", part))
        self.assertIsNone(re.search(r"(?i)\bnodes?\b", self.en))


if __name__ == "__main__":
    unittest.main()
