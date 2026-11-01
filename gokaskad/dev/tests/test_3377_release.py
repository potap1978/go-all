"""Kaskad 3.3.7.7 — the new panel look and the server check: one version, and «Что нового» of 3.3.7.7 for people, tied
to what the code does. 3.3.7.6 had no server check at all, so the entry names it as new (REV-3377-final С-2), not as
«no longer gives points»."""
import re
import subprocess
import unittest

import kaskad
from kaskad.gw import awgkmod
from tests.test_3371_release import entry, text


class Release3377Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.7")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.7")
        self.ru, self.en = re.sub(r"\s+", " ", self.ru), re.sub(r"\s+", " ", self.en)

    def test_one_version(self):
        # 3.3.7.8 is above it now (tests/test_3378_release.py): the version is its, the heading names 3.3.7.7 too
        self.assertTrue(kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.6, 3\.3\.7\.7[ ,]")
        self.assertLess(self.readme.index("**3.3.7.7 — "), self.readme.index("**3.3.7.6 — "))
        order = subprocess.run(["sort", "-V"], input="3.3.8\n3.3.7.7\n3.3.7.6\n", capture_output=True, text=True).stdout
        self.assertEqual(order.split(), ["3.3.7.6", "3.3.7.7", "3.3.8"])

    def test_russian_and_english(self):
        for words in ("меню второго уровня", "от обычного", "3 дня истории", "от 2 ГБ памяти", "Старые ссылки"):
            self.assertIn(words, self.ru)
        for words in ("second-level menu", "of usual", "3 days of history", "2 GB of memory", "Old links"):
            self.assertIn(words, self.en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))
        for part in (self.ru, self.en):
            self.assertNotRegex(part, r"\.ru\b")
            self.assertIsNone(re.search(r"\b(REV|SEC|NS-D\d+|UI-[ABC]\d?|Н-\d)\b", part))

    def test_what_it_names_is_in_the_code(self):
        # «рекомендуем от 2 ГБ» — the build error, not a workaround (owner 23.10: no swap below 2 GB)
        self.assertGreaterEqual(awgkmod.MEM_SUPPORTED_MB, 1800)
        ru = text("lib/kaskad/i18n/catalog/ru/gw_awgkmod.json")
        self.assertIn("Рекомендуем сервер от 2 ГБ памяти", ru)
        self.assertNotIn("swapon", text("lib/kaskad/gw/awgkmod.py"))

    def test_the_server_check_is_named_as_new(self):
        # REV-3377-final С-2: where it starts, the schedule, the history, «of usual», the bot, the programs it installs
        for words in ("Новое — проверка сервера", "«Проверить сейчас»", "«Нагрузка и сеть сервера» → «Проверить сервер»",
                      "`kaskadctl host bench`", "`host_bench`", "расписание", "`kaskadctl host bench schedule`",
                      "«Истории проверок»", "сравнение двух", "Бот сообщает", "пропущены", "sysbench и fio",
                      "без согласия ничего не ставит", "по расписанию программы не устанавливает"):
            self.assertIn(words, self.ru)
        for words in ("New: the server check", '"Check now"', '"Server load & network" → "Check the server"',
                      "`kaskadctl host bench`", "`host_bench`", "schedule", "`kaskadctl host bench schedule`",
                      '"Check history"', "compares two checks", "The bot tells you", "skipped", "sysbench and fio",
                      "without a yes it installs nothing", "A scheduled check never installs programs"):
            self.assertIn(words, self.en)
        # no grades of the check: points, bands, verdicts — and no «no longer» about what 3.3.7.6 never had
        self.assertNotRegex(self.ru, r"(?i)больше не[^.]*балл|балл|0[–-]1000|пояс|годи?тся|годн|годен|оценк|вывод")
        self.assertNotRegex(self.en, r"(?i)no longer[^.]*points|0[–-]1000|\bgrades?\b|\bbands?\b|verdict|\bscores?\b")

    def test_the_server_check_words_are_in_the_code(self):
        def sh(lang, name):
            m = re.search(r"(?m)^M_%s_%s='([^']*)'" % (lang, name), text("lib/kaskad/i18n/sh/menu.%s.sh" % lang))
            return m and m.group(1)
        self.assertEqual((sh("ru", "MM_I22"), sh("ru", "HM_I3"), sh("ru", "HM_I4"), sh("ru", "HM_I5")),
                         ("Нагрузка и сеть сервера", "Проверить сервер", "Расписание проверок", "История проверок"))
        self.assertEqual((sh("en", "MM_I22"), sh("en", "HM_I3"), sh("en", "HM_I4"), sh("en", "HM_I5")),
                         ("Server load & network", "Check the server", "Check schedule", "Check history"))
        self.assertIn('"dg.checkBtn": "Проверить сейчас"', text("lib/kaskad/panel/static/pages/diag-i18n-ru.js"))
        self.assertIn('"dg.checkBtn": "Check now"', text("lib/kaskad/panel/static/pages/diag-i18n-en.js"))
        # «Поделиться» вышло (C10): кнопка живая, номера будущего выпуска у неё больше нет
        self.assertIn('t("dg.share"), "tertiary", () => openShare()', text("lib/kaskad/panel/static/pages/diag.js"))
        self.assertIn('add_parser("bench"', text("lib/kaskad/host_bench_cli.py"))
        sched = text("lib/kaskad/host_sched.py")
        for name in ("schedule", "history", "compare", "export"):
            self.assertIn('add_parser("%s"' % name, sched)
        self.assertIn('"host_bench": {', text("lib/kaskad/mcp/actions.json"))
        mon = text("lib/kaskad/bot/monitor.py")
        self.assertIn('"bench_done"', mon)
        self.assertIn('"bench_skipped_day"', mon)
        ru = text("lib/kaskad/i18n/catalog/ru/bench_run.json")
        self.assertIn("Проверка по расписанию программы не устанавливает", ru)
        self.assertIn("Проверка сервера ставит только sysbench, fio и iperf3", ru)


if __name__ == "__main__":
    unittest.main()
