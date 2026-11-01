"""П4-а (BRIEF-P4-a): «кто запустил» проверку — одни и те же слова у панели, CLI и MCP, RU и EN.

Один словарь — каталог сервера ``host_sched.by.*``: из него строка истории получает ``by_text`` (CLI, JSON для MCP,
панель), текст CLI берёт его же. Словарь панели ``dg.by.*`` (подпись, если строка пришла без ``by_text``, и
заглушка ``?mock=1``) обязан совпадать с ним слово в слово. Слова — SPEC-node-score-schedule-share §3.5."""

import json
import os
import re
import unittest

from kaskad import host_sched
from kaskad.bench import history

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_RU = {"schedule": "по расписанию", "panel": "из панели", "bot": "из бота", "cli": "из командной строки",
           "mcp": "через ИИ-помощника", "ssh": "из меню сервера", "auto": "повтор после резкого падения"}


def _catalog(lang):
    with open(os.path.join(ROOT, "lib/kaskad/i18n/catalog", lang, "host_sched.json"), encoding="utf-8") as f:
        d = json.load(f)
    return dict((k[len("host_sched.by."):], v) for k, v in d.items() if k.startswith("host_sched.by."))


def _web(lang):
    with open(os.path.join(ROOT, "lib/kaskad/panel/static/pages/diag-i18n-%s.js" % lang), encoding="utf-8") as f:
        return dict(re.findall(r'"dg\.by\.(\w+)": "([^"]*)"', f.read()))


class ByLabelsTest(unittest.TestCase):
    def test_one_dictionary_for_every_surface(self):
        for lang in ("ru", "en"):
            cat = _catalog(lang)
            self.assertEqual(set(cat), set(host_sched.BY), lang)
            self.assertEqual(set(history.BY_TEXT), set(host_sched.BY))
            self.assertEqual(_web(lang), cat, lang)
            for by in host_sched.BY:
                self.assertEqual(history.by_text(by, lang), cat[by], (lang, by))
        self.assertEqual(_catalog("ru"), SPEC_RU)
        self.assertIsNone(history.by_text("button"))

    def test_cli_line_says_the_same(self):
        line = host_sched.run_line(1, {"ts": 1760900000, "mode": "quick", "by": "mcp", "status": "ok", "score": 700})
        self.assertIn(history.by_text("mcp"), line)


if __name__ == "__main__":
    unittest.main()
