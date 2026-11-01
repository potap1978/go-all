"""Kaskad 3.3.7.2 — the security patch over 3.3.7.1: one version, and «Что нового» of 3.3.7.2 for people, tied to what
the code says (the monitors of a copy parsed, the second dnsmasq, the number of «Добавить ключ выхода»)."""
import re
import subprocess
import unittest

import kaskad
from tests.test_3371_release import entry, text


class Release3372Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.2")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.2")
        self.ru, self.en = re.sub(r"\s+", " ", self.ru), re.sub(r"\s+", " ", self.en)

    def test_one_version(self):
        # 3.3.7.3 is above it now (tests/test_3373_release.py): the version is its, the heading names 3.3.7.2 too
        self.assertTrue(kaskad.__version__ == "3.3.7.2" or kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.1(, 3\.3\.7\.\d+)* и 3\.3\.7\.\d+\)")
        self.assertLess(self.readme.index("**3.3.7.2 — "), self.readme.index("**3.3.7.1 — "))
        order = subprocess.run(["sort", "-V"], input="3.3.8\n3.3.7.2\n3.3.7.1\n", capture_output=True, text=True).stdout
        self.assertEqual(order.split(), ["3.3.7.1", "3.3.7.2", "3.3.8"])

    def test_russian_and_english(self):
        for words in ("`.kbk`", "не может выполнить команды", "`/var/log/kaskad.log`", "не обрывается на полпути",
                      "до 64 знаков", "мимо выхода", "`kaskad-gw-dnsdirect`", "17 → 3 → 2", "один JSON"):
            self.assertIn(words, self.ru)
        for words in ("`.kbk`", "can no longer run commands", "`/var/log/kaskad.log`", "no longer stops half-way",
                      "to 64 characters", "past the exit", "`kaskad-gw-dnsdirect`", "17 → 3 → 2", "one JSON"):
            self.assertIn(words, self.en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))

    def test_plain_words(self):
        codes = re.compile(r"\b(REV|SEC|R-s1|T1[0-9]|К2-\d|Д-\d|З-\d|Б-\d|П-\d|С-\d|D-\d{3}|КТ\d)\b")
        for part in (self.ru, self.en):
            self.assertIsNone(codes.search(part), codes.search(part))
            self.assertNotRegex(part, r"\.ru\b")
            self.assertNotIn("DNS узла", part)

    def test_what_it_names_is_in_the_code(self):
        from kaskad.gw import dns
        self.assertEqual(dns.UNIT_DIRECT, "kaskad-gw-dnsdirect.service")
        self.assertIn("k3_mon_load()", text("install.sh"))
        self.assertIn('"update files"', text("lib", "kaskad", "mcp", "actions.json"))
        self.assertIn("k3ctl update files --quiet", text("install.sh"))
        self.assertIn("KEEP = 255", text("lib", "kaskad", "bot", "aliases.py"))


if __name__ == "__main__":
    unittest.main()
