# -*- coding: utf-8 -*-
"""WORD-01 / A2-11 (критики 16.10): в текстах для владельца тех, кому выдают ключи, зовём
«пользователь/пользователи», а не «человек/люди» — в панели, боте и SSH-меню одно слово.

Проверяется:
* русский словарь панели (admin-web/static/app.js, блок ru), бота (goamnezia-bot/lang/ru.json)
  и SSH (lib/lang/ru.sh, lib/lang/ru.d/*.sh): ни одной формы «человек/люди» вне белого списка;
* английские словари (блок en в app.js, en.json, en.sh, en.d/*.sh): ни «person», ни «people»
  вне белого списка — там «user/users»;
* слова в счётчике «{n} пользователь|{n} пользователя|{n} пользователей» — для 1, 2 и 5.
Комментарии в коде не проверяются: это не тексты для владельца.

Запуск: python3 -m unittest tests/test_word01_users.py
"""
import glob
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "admin-web", "static", "app.js")

RU_WORD = re.compile(r"(?<![А-Яа-яЁё])([Чч]еловек(?:а|у|ом|е)?|[Лл]юд(?:и|ей|ям|ьми|ях))(?![А-Яа-яЁё])")
EN_WORD = re.compile(r"\b([Pp]eople|[Pp]ersons?)\b")

# Белый список: ключ → почему здесь не пользователь продукта.
RU_ALLOW = {
    # «сайт-заглушка для сканеров, не для людей»: речь о посетителях сайта на IP сервера
    # (люди вообще против сканеров), а не о тех, кому выданы ключи.
    "siteCertSelf",
    "site_cert_selfsigned",
}
EN_ALLOW = {
    # то же «this stub site is for scanners, not people» — посетители, не пользователи.
    "siteCertSelf",
    "site_cert_selfsigned",
    # «usually the person who set the bot up» — о владельце бота (администраторе), не о пользователе.
    "admins_owner_rule",
}

JS_PAIR = re.compile(r'(\w+):\s*"((?:[^"\\\n]|\\.)*)"')
SH_PAIR = re.compile(r"^I18N\[(\w+)\]=(?:\"((?:[^\"\\]|\\.)*)\"|'([^']*)'|\$'((?:[^'\\]|\\.)*)')", re.M)
JS_STR = re.compile(r'"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`|\'(?:[^\'\\\n]|\\.)*\'')


def app_block(lang):
    """Текст словаря панели для языка: от «    ru: {» до «    en: {» (en — до конца словаря)."""
    lines = open(APP, encoding="utf-8").read().split("\n")
    start = lines.index("    %s: {" % lang)
    end = start + 1
    while lines[end] != "    },":
        end += 1
    return "\n".join(lines[start + 1:end])


def js_entries(block):
    """Пары ключ → строка из блока словаря; комментарии отброшены. Плюс остаток текста вне
    строк словаря (без комментариев) — чтобы строка в другой кавычке не прошла мимо проверки."""
    code = "\n".join(l for l in block.split("\n") if not l.lstrip().startswith("//"))
    pairs = JS_PAIR.findall(code)
    rest = JS_STR.sub('""', code)
    rest = re.sub(r"//[^\n]*", "", rest)
    return pairs, rest


def sh_entries(path):
    text = open(path, encoding="utf-8").read()
    pairs = [(m.group(1), m.group(2) or m.group(3) or m.group(4) or "") for m in SH_PAIR.finditer(text)]
    rest = SH_PAIR.sub("", text)
    rest = "\n".join(l for l in rest.split("\n") if not l.lstrip().startswith("#"))
    return pairs, rest


class Word01Tests(unittest.TestCase):
    def check(self, pairs, rest, word, allow, where):
        bad = ["%s: %s" % (k, v[:120]) for k, v in pairs if k not in allow and word.search(v)]
        self.assertEqual(bad, [], "%s: «%s» вместо «пользователь»" % (where, word.pattern))
        self.assertIsNone(word.search(rest), "%s: слово вне разобранных строк словаря: %r" % (
            where, word.search(rest) and rest[max(0, word.search(rest).start() - 60):word.search(rest).end() + 60]))

    def test_panel_ru(self):
        pairs, rest = js_entries(app_block("ru"))
        self.assertGreater(len(pairs), 2000)
        self.check(pairs, rest, RU_WORD, RU_ALLOW, "app.js ru")

    def test_panel_en(self):
        pairs, rest = js_entries(app_block("en"))
        self.assertGreater(len(pairs), 2000)
        self.check(pairs, rest, EN_WORD, EN_ALLOW, "app.js en")

    def test_bot(self):
        for lang, word, allow in (("ru", RU_WORD, RU_ALLOW), ("en", EN_WORD, EN_ALLOW)):
            with open(os.path.join(ROOT, "goamnezia-bot", "lang", lang + ".json"), encoding="utf-8") as f:
                d = json.load(f)
            self.assertGreater(len(d), 500)
            self.check(list(d.items()), "", word, allow, "bot %s.json" % lang)

    def test_ssh(self):
        for lang, word, allow in (("ru", RU_WORD, RU_ALLOW), ("en", EN_WORD, EN_ALLOW)):
            files = [os.path.join(ROOT, "lib", "lang", lang + ".sh")] + \
                sorted(glob.glob(os.path.join(ROOT, "lib", "lang", lang + ".d", "*.sh")))
            self.assertGreater(len(files), 10)
            total = 0
            for path in files:
                pairs, rest = sh_entries(path)
                total += len(pairs)
                self.check(pairs, rest, word, allow, os.path.relpath(path, ROOT))
            self.assertGreater(total, 1000)

    def test_allowlist_is_live(self):
        """Белый список не копит мёртвые ключи: каждое исключение и правда стоит в словаре со словом."""
        ru_pairs, _ = js_entries(app_block("ru"))
        en_pairs, _ = js_entries(app_block("en"))
        with open(os.path.join(ROOT, "goamnezia-bot", "lang", "ru.json"), encoding="utf-8") as f:
            ru_bot = json.load(f)
        with open(os.path.join(ROOT, "goamnezia-bot", "lang", "en.json"), encoding="utf-8") as f:
            en_bot = json.load(f)
        ru_all = dict(ru_pairs, **ru_bot)
        en_all = dict(en_pairs, **en_bot)
        for k in RU_ALLOW:
            self.assertRegex(ru_all.get(k, ""), RU_WORD, k)
        for k in EN_ALLOW:
            self.assertRegex(en_all.get(k, ""), EN_WORD, k)

    def test_people_counter_forms(self):
        """Счётчик «{n} …» выбирает форму по правилам языка: 1 пользователь, 2 пользователя, 5 пользователей."""
        ru = dict(js_entries(app_block("ru"))[0])
        en = dict(js_entries(app_block("en"))[0])
        self.assertEqual(ru["peopleN"], "{n} пользователь|{n} пользователя|{n} пользователей")
        self.assertEqual(ru["peopleOnlineN"],
                         "{n} пользователь на связи|{n} пользователя на связи|{n} пользователей на связи")
        self.assertEqual(en["peopleN"], "{n} user|{n} users")
        self.assertEqual(en["peopleOnlineN"], "{n} user online|{n} users online")

    def test_bulk_counts_do_not_glue_number_to_a_fixed_form(self):
        """Где число подставляется %s/{n} без выбора формы, слово стоит перед числом («пользователей: 2»),
        а не после («2 пользователей», «1 пользователей»)."""
        bad = []
        fixed = re.compile(r"(%s|\{\w+\}) пользовател(ей|я|ь|и)\b")
        ru_pairs, _ = js_entries(app_block("ru"))
        with open(os.path.join(ROOT, "goamnezia-bot", "lang", "ru.json"), encoding="utf-8") as f:
            ru_pairs += list(json.load(f).items())
        for path in sorted(glob.glob(os.path.join(ROOT, "lib", "lang", "ru.d", "*.sh"))):
            ru_pairs += sh_entries(path)[0]
        for k, v in ru_pairs:
            if k in ("peopleN", "peopleOnlineN"):
                continue
            for m in fixed.finditer(v):
                # «В контейнере %s пользователей нет» — %s там имя контейнера, а не число.
                if v[max(0, m.start() - 12):m.start()].endswith("контейнере "):
                    continue
                bad.append("%s: %s" % (k, v[max(0, m.start() - 30):m.end() + 10]))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
