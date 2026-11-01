"""Kaskad 3.3.7.4 — the Russian groups («Россия через свой ключ»): one version, and «Что нового» of 3.3.7.4 for people,
tied to what the code says (and not promising what the panel does not have yet)."""
import re
import subprocess
import unittest

import kaskad
from tests.test_3371_release import entry, text

STATIC = ("lib", "kaskad", "panel", "static", "pages")


class Release3374Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.4")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.4")
        self.ru, self.en = re.sub(r"\s+", " ", self.ru), re.sub(r"\s+", " ", self.en)

    def test_one_version(self):
        # 3.3.7.5 is above it now (tests/test_3375_release.py): the version is its, the heading names 3.3.7.4 too
        self.assertTrue(kaskad.__version__ == "3.3.7.4" or kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme,
                         r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.1, 3\.3\.7\.2, 3\.3\.7\.3(, 3\.3\.7\.\d+)* и 3\.3\.7\.\d+\)")
        self.assertLess(self.readme.index("**3.3.7.4 — "), self.readme.index("**3.3.7.3 — "))
        order = subprocess.run(["sort", "-V"], input="3.3.8\n3.3.7.4\n3.3.7.3\n", capture_output=True, text=True).stdout
        self.assertEqual(order.split(), ["3.3.7.3", "3.3.7.4", "3.3.8"])

    def test_russian_and_english(self):
        for words in ("российскую группу", "домашний роутер", "«Часто»", "«Лениво»", "второй и третий ключ",
                      "без зарубежных", "сам xray не перезапускается", "в следующих версиях",
                      "«Появится в следующем обновлении»", "остаются как были"):
            self.assertIn(words, self.ru)
        for words in ("Russian group", "home router", "“Often”", "“Lazy”", "second and a third key",
                      "without foreign ones", "xray itself does not restart", "in the next versions",
                      "“Comes in the next update”", "stay as they were"):
            self.assertIn(words, self.en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))

    def test_plain_words(self):
        codes = re.compile(r"\b(REV|SEC|UI-1|F\d\d|Н-[\w-]+|Q[1-7]|R-s1|T1[0-9]|К2-\d|Д-\d|З-\d|Б-\d|П-\d|С-\d|D-\d{3}|"
                           r"КТ\d|ru_via|gw_rx|gw_rd|upstream-ru)\b")
        for part in (self.ru, self.en):
            self.assertIsNone(codes.search(part), codes.search(part))
            self.assertNotRegex(part, r"\.ru\b")
            for push in ("обязательно", "должны", "немедленно", "must ", "immediately"):
                self.assertNotIn(push, part)

    def test_does_not_promise_the_panel(self):
        # README 3.3.7.4 — история того выпуска: тогда панель российскую группу не делала (выбор был выключен). С 3.3.7.10
        # (Q6) выбор «Российская» открыт, а описание говорит, для кого она — README 3.3.7.10 об этом и пишет
        ru, en = text(*STATIC, "exits-groups-i18n-ru.js"), text(*STATIC, "exits-groups-i18n-en.js")
        self.assertIn('"ep.roleRuD": "только российские сайты — у выбранных пользователей с профилем «Россия напрямую»"', ru)
        self.assertNotRegex(text(*STATIC, "exits-groups.js"), r'v: "ru", title: t\("ep\.roleRu"\)[^}]*dis: true')
        self.assertIn("Включить их пока нельзя", self.ru)
        self.assertIn("They cannot be turned on yet", self.en)
        self.assertIn('"ep.roleRuD": "Russian sites only — for selected users with the “Russia direct” profile"', en)
        # no setup recipe for a half-way state: no API request, no exit-add into a group nobody can choose
        for part in (self.ru, self.en):
            for words in ("API", "exit-add", "exit-move"):
                self.assertNotIn(words, part)
        self.assertIn('"ep.roleRu": "Domestic"', en)      # 29.10: «внутренняя»

    def test_what_it_names_is_in_the_code(self):
        ru, en = text(*STATIC, "exits-groups-i18n-ru.js"), text(*STATIC, "exits-groups-i18n-en.js")
        for k, v in (('"ep.freq.often": "Часто"', ru), ('"ep.freq.lazy": "Лениво"', ru), ('"ep.reactLazy": "«Лениво»', ru),
                     ('"ep.freq.often": "Often"', en), ('"ep.freq.lazy": "Lazy"', en)):
            self.assertIn(k, v)
        from kaskad.gw import model, policy, xray
        self.assertEqual(policy.preset_fields("ru_home", "ru")["checks"]["freq"]["mode"], "often")
        self.assertTrue(callable(model.set_ru_via))
        self.assertEqual(xray.SS_KILL[:2], ["ss", "-K"])
        cli = text("lib", "kaskad", "gw", "cli_policy.py")
        self.assertIn('"exit-move"', cli)
        self.assertIn("--confirm-role", cli)
        self.assertIn('"gw_cli.preset.ru_home": "Дом"', text("lib", "kaskad", "i18n", "catalog", "ru", "gw_cli.json"))
        self.assertIn('"gw_cli.preset.ru_home": "Home"', text("lib", "kaskad", "i18n", "catalog", "en", "gw_cli.json"))
        self.assertIn('@route("POST", r"/api/gw/groups")', text("lib", "kaskad", "panel", "api.py"))
        import json
        live = json.loads(text("lib", "kaskad", "i18n", "catalog", "ru", "gw_live.json"))
        self.assertIn("российские сайты закрыты", live["gw_live.event.group_last_step_ru"])
        trace = json.loads(text("lib", "kaskad", "i18n", "catalog", "ru", "gw_trace.json"))
        self.assertIn("Российские сайты — через группу", trace["gw_trace.step.ru_exit"])


if __name__ == "__main__":
    unittest.main()
