"""Слова состояния одни везде (владелец 28.10 23:37, пачка А п. 2: «да», как предложено; IT-1 строки 118 и 145).

Эталон — слова панели: вердикт Сводки ``ovb.v.*`` и «Здоровье сервера» ``ovb.hh.*`` (static/pages/ovb-i18n-{ru,en}.js).
Один словарь ядра — каталог ``gw_host.light.*``: из него говорят ``kaskadctl host health``, бот (bot/sysinfo.py) и MCP
(через kaskadctl). Панель каталог не читает (статические словари веба), у SSH-меню свои тексты (i18n/sh/menu.*.sh):
равенство с панелью закрепляет этот тест — расхождение в любом языке его роняет.

Было до 3.3.7.12: CLI и бот — «Сейчас всё хорошо / Есть замечания / Есть серьёзные неполадки / Ещё собираю данные /
Проверка здоровья выключена», меню — «Здоровье сервера: есть замечания / есть серьёзные неполадки — 22» и «Неполадок не
видно» при любом свете, кроме warn/crit."""

import os
import re
import unittest

from kaskad.i18n import catalog, lower_first

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib", "kaskad")
LEVELS = ("ok", "warn", "crit", "wait", "off")
OLD = {"ru": ("Сейчас всё хорошо", "Есть замечания", "есть замечания", "серьёзные неполадки", "Ещё собираю данные",
              "Проверка здоровья выключена", "проверка здоровья выключена"),
       "en": ("All is well now", "Some issues", "some issues", "Serious problems", "serious problems",
              "Still collecting data", "The health check is off", "the health check is off")}


def _read(*parts):
    with open(os.path.join(LIB, *parts), encoding="utf-8") as f:
        return f.read()


def panel(lang):
    """{ключ: текст} словаря части «Обзор» (ovb-i18n-<lang>.js)."""
    src = _read("panel", "static", "pages", "ovb-i18n-%s.js" % lang)
    return dict((k, v) for k, v in re.findall(r'^\s*"(ovb\.[A-Za-z0-9_.]+)":\s*"((?:[^"\\]|\\.)*)"', src, re.M))


def menu(lang):
    """{ключ: текст} текстов SSH-меню (menu.<lang>.sh), ключ без приставки «M_<lang>_»."""
    src = _read("i18n", "sh", "menu.%s.sh" % lang)
    return dict(re.findall(r"^M_%s_([A-Z0-9_]+)='([^']*)'$" % lang, src, re.M))


class OneWordsTest(unittest.TestCase):
    def test_the_light_of_the_core_is_the_panels(self):
        """CLI, бот и MCP (gw_host.light.*) — ровно слова «Здоровья сервера» панели (ovb.hh.*), RU и EN; причина
        «не проверяется» у показателя — те же слова строчными."""
        for lang in ("ru", "en"):
            cat, pan = catalog(lang), panel(lang)
            for lv in LEVELS:
                self.assertEqual(cat["gw_host.light." + lv], pan["ovb.hh." + lv], (lang, lv))
            self.assertEqual(cat["gw_host.why.off"], lower_first(pan["ovb.hh.off"]), lang)

    def test_the_menu_says_the_panels_words(self):
        """Шапка SSH-меню: warn/crit — «Здоровье сервера: <слова ovb.hh> — 22»; строка без проблем — слова вердикта
        Сводки (ovb.v.ok / nohc / wait) и «· подробная проверка — 21»."""
        for lang in ("ru", "en"):
            pan, mm = panel(lang), menu(lang)
            head = {"ru": "Здоровье сервера", "en": "Server health"}[lang]
            for key, lv in (("MM_H_HOST_WARN", "warn"), ("MM_H_HOST_CRIT", "crit")):
                self.assertEqual(mm[key], "%s: %s — 22" % (head, lower_first(pan["ovb.hh." + lv])), (lang, key))
            tail = mm["MM_H_ALLOK"].split(" · ", 1)[1]
            for key, v in (("MM_H_OK", "ok"), ("MM_H_NOHC", "nohc"), ("MM_H_WAIT", "wait")):
                self.assertEqual(mm[key], "%s · %s" % (pan["ovb.v." + v], tail), (lang, key))

    def test_the_menu_calls_its_words(self):
        """Строки MM_H_* не мёртвые: k3_head_lines() в install.sh говорит слова Сводки (t MM_H_OK / NOHC / WAIT).
        REV-3312 Б-1: слияние 993ff92f потеряло ветку «ok|off|wait)», тексты остались в каталоге, а меню снова
        говорило «Неполадок не видно» при любом свете — сверка текстов выше этого не видела."""
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"^k3_head_lines\(\) \{\n(.*?)^\}$", src, re.M | re.S)
        self.assertIsNotNone(m, "install.sh: нет k3_head_lines()")
        body = m.group(1)
        for key in ("MM_H_OK", "MM_H_NOHC", "MM_H_WAIT", "MM_H_HOST_WARN", "MM_H_HOST_CRIT"):
            self.assertTrue(re.search(r"\bt %s\b" % key, body), "k3_head_lines() не вызывает t %s" % key)
        self.assertTrue(re.search(r"^\s*ok\|off\|wait\)", body, re.M), "k3_head_lines(): нет ветки ok|off|wait)")
        # и вообще: каждая строка шапки (MM_H_*) где-то сказана — t/tf в install.sh
        for key in sorted(menu("ru")):
            if key.startswith("MM_H_"):
                self.assertTrue(re.search(r"\btf? %s\b" % key, src), "install.sh не говорит %s — строка мёртвая" % key)

    def test_no_old_words_left(self):
        """Прежних слов уровня нет ни в каталоге ядра, ни в текстах SSH, ни в описаниях MCP."""
        cat_dir = os.path.join(LIB, "i18n", "catalog")
        for lang, words in OLD.items():
            texts = [("catalog/%s/%s" % (lang, n), _read("i18n", "catalog", lang, n))
                     for n in sorted(os.listdir(os.path.join(cat_dir, lang))) if n.endswith(".json")]
            texts += [("sh/" + n, _read("i18n", "sh", n)) for n in sorted(os.listdir(os.path.join(LIB, "i18n", "sh")))
                      if n.endswith(".%s.sh" % lang)]
            texts.append(("mcp/actions.json", _read("mcp", "actions.json")))
            for where, text in texts:
                for w in words:
                    self.assertNotIn(w, text, (lang, where, w))


if __name__ == "__main__":
    unittest.main()
