"""Kaskad 3.3.7.1 — the release (the owner, 19.10: small numbers, «3.3.8» comes out as 3.3.7.1): one version, and
«Что нового» of 3.3.7.1 for people, tied to what the code says.

- install.sh ``KASKAD_VERSION`` = ``kaskad.__version__`` = 3.3.7.1 (sort -V: 3.3.7 < 3.3.7.1 < 3.3.8 — a node on 3.3.7
  is offered it); the heading of Kaskad 3 ends with «… 3.3.7 и 3.3.7.1».
- The entry of 3.3.7.1 is the first of «Что нового», above 3.3.7, in Russian and in English, in plain words — no codes
  of the tasks (REV-…, З-1, К2-9, Д-1, T10…), no «.ru», no Cyrillic in the English part; the «3.3.8» entry is gone.
- What it names is in the code: the light «Есть серьёзные неполадки», the range of Hysteria2 (HOP_MAX), the CLI of the
  groups, of the time zone and of TOFU.
"""
import os
import re
import subprocess
import unittest

import kaskad

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def text(*name):
    with open(os.path.join(ROOT, *name), encoding="utf-8") as f:
        return f.read()


def entry(readme, version):
    i = readme.index("**%s — " % version)
    m = re.compile(r"(?m)^\*\*\d+(?:\.\d+){2,3} — ").search(readme, i + 1)
    return readme[i:m.start() if m else len(readme)]


class Release3371Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.1")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.1")
        self.ru, self.en = re.sub(r"\s+", " ", self.ru), re.sub(r"\s+", " ", self.en)

    def test_one_version(self):
        # 3.3.7.2 is above it now (tests/test_3372_release.py): the version is its, the heading names 3.3.7.1 too
        self.assertTrue(kaskad.__version__ == "3.3.7.1" or kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh"))
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7, 3\.3\.7\.1(, 3\.3\.7\.\d+)* и 3\.3\.7\.\d+\)")
        self.assertLess(self.readme.index("**3.3.7.1 — "), self.readme.index("**3.3.7 — "))
        self.assertNotIn("**3.3.8 — ", self.readme)
        order = subprocess.run(["sort", "-V"], input="3.3.8\n3.3.7.1\n3.3.7\n", capture_output=True, text=True).stdout
        self.assertEqual(order.split(), ["3.3.7", "3.3.7.1", "3.3.8"])          # the update offers it after 3.3.7

    def test_russian_and_english(self):
        for words in ("Ни одного включённого выхода", "мимо выхода", "повреждённом списке организаций ⚠РФ",
                      "Лента событий групп", "Есть серьёзные неполадки", "ниже 1,5%", "Новый вход Hysteria2 (UDP)",
                      "`hysteria2://`", "до 100 портов", "Salamander", "`kaskadctl gw groups`", "Состояние и группы",
                      "Часовой пояс", "`kaskadctl tz set`", "`kaskadctl gw exit-preview --tofu`",
                      "`kaskadctl gw exit-add --pin-tofu <отпечаток>`", "Переименование", "до 40 знаков",
                      "управляющими знаками"):
            self.assertIn(words, self.ru)
        for words in ("No exit is turned on", "past the exit", "damaged list of the ⚠RU organisations",
                      "The feed of group events", "Serious problems", "below 1.5%", "The new Hysteria2 inbound (UDP)",
                      "`hysteria2://`", "up to 100 ports", "Salamander", "`kaskadctl gw groups`", "State and groups",
                      "Time zone", "`kaskadctl tz set`", "`kaskadctl gw exit-preview --tofu`",
                      "`kaskadctl gw exit-add --pin-tofu <fingerprint>`", "Renaming", "up to 40 characters",
                      "control characters"):
            self.assertIn(words, self.en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))

    def test_plain_words(self):
        codes = re.compile(r"\b(REV|SEC|R-s1|T1[0-9]|К2-\d|Д-\d|З-\d|П-\d|С-\d|Л-\d|D-\d{3}|КТ\d)\b")
        for part in (self.ru, self.en):
            self.assertIsNone(codes.search(part), codes.search(part))
            self.assertNotRegex(part, r"\.ru\b")
            self.assertNotIn("DNS узла", part)

    def test_what_it_names_is_in_the_code(self):
        # 3.3.7.12 (владелец 28.10 «статусы одними словами везде»): одно слово crit осталось одним, но это слово панели
        self.assertIn('"gw_host.light.crit": "Требует внимания"', text("lib", "kaskad", "i18n", "catalog", "ru",
                                                                     "gw_host.json"))
        self.assertIn("HOP_MAX = 100", text("lib", "kaskad", "gw", "inbounds", "hy2.py"))
        cli = text("lib", "kaskad", "gw", "cli.py")
        for flag in ('"--pin-tofu"', '"--tofu"'):
            self.assertIn(flag, cli)
        self.assertIn("kaskad.tz_cli", text("lib", "kaskad", "cli.py"))
        self.assertIn('"groups"', text("lib", "kaskad", "gw", "cli_policy.py"))
        self.assertIn("M_ru_GWTZ_ITEM='Часовой пояс: %s'", text("lib", "kaskad", "i18n", "sh", "gwtime.ru.sh"))


if __name__ == "__main__":
    unittest.main()
