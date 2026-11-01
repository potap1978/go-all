"""Kaskad 3.3.7.6 — the quick setup has its look again (3.3.7.5 closed the root of the port, the wizard took the shared
files of the panel from there): one version, and «Что нового» of 3.3.7.6 for people, tied to what the code does."""
import re
import subprocess
import unittest

import kaskad
from kaskad.panel import server
from tests.test_3371_release import entry, text


class Release3376Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.6")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.6")
        self.ru, self.en = re.sub(r"\s+", " ", self.ru), re.sub(r"\s+", " ", self.en)

    def test_one_version(self):
        # 3.3.7.7 is above it now (tests/test_3377_release.py): the version is its, the heading names 3.3.7.6 too
        self.assertTrue(kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.5, 3\.3\.7\.6[ ,]")
        self.assertLess(self.readme.index("**3.3.7.6 — "), self.readme.index("**3.3.7.5 — "))
        order = subprocess.run(["sort", "-V"], input="3.3.8\n3.3.7.6\n3.3.7.5\n", capture_output=True, text=True).stdout
        self.assertEqual(order.split(), ["3.3.7.5", "3.3.7.6", "3.3.8"])

    def test_russian_and_english(self):
        for words in ("без оформления", "пока мастер открыт", "по-прежнему не открываются", "gokaskad"):
            self.assertIn(words, self.ru)
        for words in ("without its look", "while the wizard is open", "still do not open", "gokaskad"):
            self.assertIn(words, self.en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))
        for part in (self.ru, self.en):
            self.assertNotRegex(part, r"\.ru\b")
            self.assertIsNone(re.search(r"\b(REV|SEC|T-3375|Н-\d)\b", part))

    def test_what_it_names_is_in_the_code(self):
        self.assertIn("/tokens.css", server._SETUP_SHARED)
        self.assertNotIn("/app.js", server._SETUP_SHARED)
        self.assertNotIn("/index.html", server._SETUP_SHARED)


if __name__ == "__main__":
    unittest.main()
