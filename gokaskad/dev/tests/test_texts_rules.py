"""Тексты продукта RU/EN по правилам владельца — один проход по всем текстам (tools/k3_text_check.py).

Что ловит: примеры доменов в региональных зонах и совет зоны (12.10, neutral-domain-examples), настоящие IP вместо
192.0.2.x / 198.51.100.x / 203.0.113.x, вопросы y/n вместо выбора цифрами (07.10, menu-numbered-choices), числа
(десятичная запятая в RU и точка в EN, единица через пробел, процент слитно), пары RU/EN (ключи, заполнители,
пустые тексты), навязывающие формулировки (зона, регистратор, страна, хостинг как единственный путь).

Старые нарушения — в tools/k3_text_exceptions.tsv с причиной; тест падает на новых и на исключениях, которые уже
не нужны (текст поправили — уберите строку). Отчёт и список текстов к правке — k3/stand/logs/R-texts.md.
"""
# k3-select: dirs=lib/kaskad/i18n,lib/kaskad/panel/static
# k3-select: files=install.sh,README.md,DOCS.md,ИНСТРУКЦИЯ_ДЛЯ_ПОЛЬЗОВАТЕЛЯ.md,tools/k3_text_check.py,tools/k3_text_exceptions.tsv,lib/kaskad/mcp/actions.json,lib/kaskad/mcp/AGENT_GUIDE.md
import contextlib
import glob
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import k3_text_check as tc  # noqa: E402


def rules_of(fs):
    return sorted(set(f.rule for f in fs))


def one(text, lang="ru", kind="catalog", src="x.json", key="k"):
    return tc.text_findings(tc.Item(src, lang, key, text, kind))


class TheProductTextsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found = tc.findings(ROOT)
        cls.allowed, cls.why = tc.read_exceptions()

    def test_no_new_violations(self):
        new, stale = tc.compare(self.found, self.allowed)
        self.assertEqual([tc.show(f) for f in new], [],
                         "новые нарушения правил текстов — поправьте текст (причины — tools/k3_text_check.py)")
        self.assertEqual([tc.show(f) for f in stale], [],
                         "исключения больше не нужны — уберите строки из tools/k3_text_exceptions.tsv")

    def test_no_russian_role_words(self):
        """29.10 (domestic): ни одного «российская группа / Russian group» в текстах — и без исключений."""
        found = [tc.show(f) for f in self.found if f.rule == "domestic"]
        self.assertEqual(found, [])
        _a, why = tc.read_exceptions()
        self.assertFalse([f for f in why if f.rule == "domestic"], "у роли «ru» исключений нет — правьте текст")
        # README (текущие разделы — до «Что нового» и после записей) и памятка ИИ-агента MCP: правило — то же
        readme = tc._read(os.path.join(ROOT, "README.md"))
        head, sep, rest = readme.partition("### Что нового")
        self.assertTrue(sep)
        tail = rest[rest.index("\n### ", 1):]
        guide = tc._read(os.path.join(ROOT, "lib/kaskad/mcp/AGENT_GUIDE.md"))
        for name, part in (("README.md", head + tail), ("AGENT_GUIDE.md", guide)):
            for rx in (tc._DOMESTIC_RU, tc._DOMESTIC_EN):
                self.assertEqual([m.group(1) for m in rx.finditer(part)], [], name)
        self.assertIn("Внутренние группы", guide)

    def test_every_exception_has_a_reason(self):
        self.assertTrue(self.allowed)
        for f, why in self.why.items():
            self.assertTrue(why.strip() and not why.startswith("TODO"), tc.show(f))

    def test_all_the_texts_are_read(self):
        pairs = tc.all_pairs(ROOT)
        kinds = {}
        for kind, label, docs in pairs:
            kinds.setdefault(kind, []).append(label)
            self.assertTrue(docs["ru"] and docs["en"], label)
        self.assertGreaterEqual(len(kinds["catalog"]), 60)
        self.assertGreaterEqual(len(kinds["sh"]), 30)
        web = " ".join(kinds["web"])
        for name in ("static/i18n-", "setup/setup-i18n-", "ux-i18n-", "give-i18n-", "user-i18n-", "graph-i18n-"):
            self.assertIn(name, web)
        its = tc.items(ROOT, pairs)
        srcs = set(i.src for i in its)
        for name in ("install.sh",) + tc.DOCS:
            self.assertIn(name, srcs)
        self.assertGreater(len(its), 20000)
        code = set(i.src for i in its if i.kind == "code")           # литералы и страницы веба, которые видит человек
        for name in ("static/setup/setup.js", "static/app.js", "static/dev-mock.js", "static/dev-mock-en.js",
                     "static/setup/dev-mock-setup.js", "static/index.html"):
            self.assertIn("lib/kaskad/panel/" + name, code)
        self.assertFalse([s for s in code if "i18n-" in s])                  # словари читаются парами, не дважды
        sh = dict((label, docs) for kind, label, docs in pairs if kind == "sh")
        core = sh["lib/kaskad/i18n/sh/core.{ru,en}.sh"]
        self.assertEqual(core["ru"]["C_CHOICE_DEF"], "Выбор [%s]: ")       # кавычки и '\'' разобраны

    def test_no_links_to_the_old_diagnostics_section(self):
        # 3.3.7.7 (REV-3377-final З-1): раздела «Диагностика» в меню панели нет — его части в «Обзор › Проверка
        # сервера». Тексты бота, CLI, меню и MCP не ведут в старый раздел; имя nav.diag осталось только для старых адресов
        old = re.compile(r"Диагностик|Diagnostics")
        hits = [tc.show(tc.Finding("diag", i.src, i.key, i.text[:60]))
                for i in tc.items(ROOT, tc.all_pairs(ROOT)) if old.search(i.text) and i.key != "nav.diag"]
        self.assertEqual(hits, [], "ссылка на раздел «Диагностика» — теперь «Обзор › Проверка сервера»")
        for rel in ("lib/kaskad/mcp/actions.json", "lib/kaskad/mcp/AGENT_GUIDE.md"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                self.assertIsNone(old.search(f.read()), rel)

    def test_install_sh_without_the_generated_blocks(self):
        lines = [ln for _, ln in tc.install_lines(ROOT)]
        self.assertFalse([ln for ln in lines if ln.startswith("M_ru_") or ln.startswith("M_en_")])
        self.assertFalse([ln for ln in lines if ln.startswith("K3_LIB_SHA=")])


class TheRulesTest(unittest.TestCase):
    """Каждое правило на коротких строках: что ловит и что пропускает."""

    def test_domain(self):
        for s in ("адрес vpn.мой-сайт.ru", "домен example.ru", "впн.мойсайт.рф", "k3.example.su", "my.site.kz",
                  "https://vpn.мой-сайт.ru/sub/abc", "https://vpn.example.ru/sub/abc", "почта you@example.ru",
                  "vless://id@vpn.example.ru:443"):
            self.assertIn("domain", rules_of(one(s)), s)
        for s in ("правило ozon.ru напрямую", "сайты gosuslugi.ru и vk.ru", "домен example.com", "README.md",
                  "можно кириллицей: впн.пример.рф", "почта user@mail.ru", "vk.cc/ct29NQ", "https://ozon.ru/x",
                  "файл rules.ru", "поле name.by", "путь lib/b.ru", "зона второго уровня com.ru"):
            self.assertNotIn("domain", rules_of(one(s)), s)

    def test_zone(self):
        for s in ("домен в зоне .ru — около 5 минут", "a .ru zone domain", "домен .рф"):
            self.assertIn("zone", rules_of(one(s)), s)
        self.assertNotIn("zone", rules_of(one("просто «домен», без зоны .ru")))

    def test_ip(self):
        for s in ("например 45.10.20.30", "адрес 45.10.20.32:443", "сервер 45.10.20.33", "http://45.10.20.31:8080/",
                  "панель https://45.10.20.32/p/", "vless://id@45.10.20.30:443",
                  # REV-texts-2 Н1: название клиента и «+» через пробел — не версия; «:порт» и «/маска» — адрес
                  "AmneziaWG 45.10.20.31:51820", "WireGuard 45.10.20.31", "Сервер Kaskad 45.10.20.31", "Happ 45.10.20.31",
                  "Адрес 45.10.20.31 + порт 443", "версия 45.10.20.31:443", "version 45.10.20.31/24"):
            self.assertIn("ip", rules_of(one(s)), s)
        for s in ("например 203.0.113.7", "198.51.100.1 и 192.0.2.10", "сеть 10.8.0.0/24", "DNS 1.1.1.1",
                  "маска 255.255.255.0", "127.0.0.1", "0.0.0.0", "100.64.0.1", "версия 3.3.5", "AWG 2.0.1.4.5",
                  "AmneziaVPN 4.8.12.9 или новее", "версия 1.10.2.3", "v2rayN 6.4.2.1 или новее", "1.10.2.3 or newer", "4.8.12.9+",
                  "http://203.0.113.5:8080/", "путь a/45.10.20.31",
                  # выпуск самого Kaskad из 4 чисел (заголовок README) — в любом окружении
                  "исправление безопасности 3.3.5.1", "до 3.3.5.1", "**3.3.5.1 — исправление", "(see 3.3.5.1 below)"):
            self.assertNotIn("ip", rules_of(one(s)), s)
        self.assertIn("3.3.5.1", tc.OWN_VERSIONS)
        self.assertIn("ip", rules_of(one("безопасности 3.3.5.2")))         # не выпуск Kaskad — адрес, как раньше
        self.assertIn("ip", rules_of(one("адрес 3.3.5.1:443")))           # с портом — адрес
        self.assertIn("ip", rules_of(one("IP /45.10.20.31")))                 # «/» после пробела — не путь (REV-texts-2)
        # в install.sh — только строки вывода: рабочие адреса в коде не примеры
        self.assertNotIn("ip", rules_of(one('ping -c1 45.10.20.30 >/dev/null', None, "install", "install.sh", "")))
        self.assertIn("ip", rules_of(one('echo "Например 45.10.20.30"', None, "install", "install.sh", "")))

    def test_yn(self):
        for s in ("Продолжить? (y/n) ", "Удалить? [Y/n]: ", "Применить (д/н)?", "Ответьте y", "Type y to go on",
                  "Напечатайте **y**", "Удалить всё? y/N: ", "Go on? Y/n", "Enter y to confirm"):
            self.assertEqual(rules_of(one(s, kind="sh")).count("yn"), 1, s)
        self.assertEqual(len(one("Продолжить? (y/n) ", kind="sh")), 1)          # скобки и пара — одно нарушение
        for s in ("a simple yes/no question", "вопрос да/нет без выбора"):  # без скобок и не вопрос (REV-texts-2)
            self.assertNotIn("yn", rules_of(one(s, kind="sh")), s)
        for code in ('[ "$ans" = "y" ] && go', 'case "$a" in y|Y) go;; esac', '[[ $a =~ ^[Yy] ]]', "k3_yes 'X?'",
                     'case "$a" in [yYдД]*) go;; esac', '[[ $a == [yY]* ]]', 'case "$a" in д|Д|y|yes) go;; esac',
                     '[[ $a =~ ^(y|yes)$ ]]', "[ \"$a\" == 'yes' ]", 'case "$a" in "y"|"Y") x;; esac', '[[ $a == y* ]]',
                     'case $a in n|N) no;; y|Y) go;; esac', 'read -rp "Удалить всё? y/N: " a'):
            self.assertIn("yn", rules_of(one(code, None, "install", "install.sh", "")), code)
        for code in ('case "$c" in 1) a;; 2) b;; esac', 'case $x in a|b) z;; esac', '[[ $f == yaml ]]',
                     "grep -E 'a|b' | wc -l", 'x=$(echo y | tr y z)', 'local y="$1"', 'RemainAfterExit=yes',
                     '[ "$(systemctl show -p NeedDaemonReload --value u)" = yes ]', 'y=cr[pp]-pr[pp]'):
            self.assertNotIn("yn", rules_of(one(code, None, "install", "install.sh", "")), code)
        for s in ("1) Да, ускорить (рекомендуется)\n2) Нет", "Выбор [1]: ", "Введите yes, чтобы подтвердить",
                  "Нажмите Enter", "y-координата"):
            self.assertNotIn("yn", rules_of(one(s, kind="sh")), s)
        self.assertNotIn("yn", rules_of(one("Да (y/n)", kind="web")))     # веб и бот — кнопками

    def test_numbers(self):
        self.assertIn("num-dec", rules_of(one("около 0.8 ГБ")))
        self.assertIn("num-dec", rules_of(one("about 0,8 GB", "en")))
        self.assertNotIn("num-dec", rules_of(one("около 0,8 ГБ")))
        self.assertNotIn("num-dec", rules_of(one("about 0.8 GB", "en")))
        self.assertNotIn("num-dec", rules_of(one("1,500 MB free", "en")))       # разделитель тысяч в EN
        self.assertNotIn("num-dec", rules_of(one("1,500,000 MB", "en")))
        self.assertIn("num-dec", rules_of(one("0,500 MB", "en")))
        for s in ("AmneziaWG 3.1 с модулем", "TLS 1.3 с узла", "Kaskad PRO 2.13 с GitHub"):
            self.assertNotIn("num-dec", rules_of(one(s)), s)                # версия и предлог, не секунды
        for s, lang in (("12МБ", "ru"), ("Ping: {ms}ms", "ru"), ("%sс | %sмс", "ru"), ("(<50ms)", "ru"),
                        ("12MB", "en"), ("{ms}ms", "en"), ("Задержка {ms} ms", "ru"), ("раз в {n}мин", "ru"),
                        ("12мин", "ru"), ("every {n}s", "en"), ("%ds left", "en"), ("15min", "en")):
            self.assertIn("num-unit", rules_of(one(s, lang, "sh")), s)
        self.assertEqual(len(one("Ping: {ms}ms", "ru", "sh")), 1)                # вплотную латиницей — одно нарушение
        for s, lang in (("12 МБ", "ru"), ("{ms} мс", "ru"), ("%s с", "ru"), ("12 MB", "en"), ("{n} ms", "en"),
                        ("x2", "en"), ("AES-256", "en"), ("{n}с", "en"), ("{n} мин", "ru"), ("12 минут", "ru"),
                        ("{n} s", "en"), ("{n}st", "en"), ("1990s", "en")):
            self.assertNotIn("num-unit", rules_of(one(s, lang, "sh")), s)
        for s in ("≈25 % трафика", "{p} %", "7 %%"):
            self.assertIn("pct", rules_of(one(s, kind="sh")), s)
        for s in ("60% скорости", "{p}%", "%s%%", "100%", "5 %s"):
            self.assertNotIn("pct", rules_of(one(s, kind="sh")), s)
        # install.sh: числа — только в строках вывода; формат даты «%d %H» — не процент
        inst = lambda s: rules_of(one(s, None, "install", "install.sh", ""))  # noqa: E731
        self.assertIn("num-dec", inst('echo "Нужно 1.5 ГБ"'))
        self.assertIn("num-unit", inst('printf "%dMB\\n" "$m"'))
        self.assertEqual(inst('SIZE=1.5GB'), [])
        self.assertEqual(inst("echo \"[$(date '+%Y-%m-%d %H:%M:%S')]\""), [])

    def test_push(self):
        for s in ("Купите домен на reg.ru", "зарегистрируйте домен у регистратора", "домен только в зоне .ru",
                  "нужен российский хостинг", "Лучше всего взять сервер в Германии", "Buy a domain at Namecheap",
                  "we recommend to buy a domain", "Возьмите VPS у хостинга",
                  "Rent a VPS from a host below", "Арендуйте сервер у Aeza", "Арендуйте VPS на Aeza",
                  "Rent a server in Germany", "Сервер нужен в Нидерландах", "Нужен VPS в Германии",
                  "The server must be in Germany", "домен на nic.ru", "Register at Namecheap"):
            self.assertIn("push", rules_of(one(s)), s)
        for s in ("Для выхода нужен сервер не в России — при заказе выберите другую страну.",
                  "1) Да, ускорить модулем ядра (рекомендуется)", "Провайдер видит только РФ IP.",
                  "only Russian networks go direct", "Есть домен? Укажите его.", "Нет сервера за рубежом?",
                  "Доступ к nic.ru напрямую", "Сервер нужен в другой стране, не в России",
                  "Первый сервер нужен в России, второй — за рубежом"):       # схема каскада (REV-texts-2)
            self.assertNotIn("push", rules_of(one(s)), s)

    def test_people(self):
        """24.10: сущность учёта — «пользователь» (у пользователя — устройства), не «человек / люди»."""
        for s, lang, kind in (("Люди и устройства", "ru", "web"), ("Добавить человека", "ru", "catalog"),
                              ("Как у человека «{name}»", "ru", "sh"), ("👥 Людей пока нет.", "ru", "catalog"),
                              ("Человек выключен", None, "code"), ("People & devices", "en", "web"),
                              ("Add a person", "en", "sh"), ("the person’s page", "en", "catalog"),
                              ("send those people a new link.", "en", "web"),
                              ("Люди: id, имя, свой маршрут", "ru", "mcp"), ("People, devices", "en", "mcp")):
            self.assertIn("people", rules_of(one(s, lang, kind)), s)
        for s, lang, kind in (("Пользователи и устройства", "ru", "web"), ("ждёт истории: нужны сутки наблюдений", "ru", "catalog"),
                              ("Users & devices", "en", "web"), ("nav.people", "en", "web"), ("{units}{people}", "en", "catalog"),
                              ("people.css?v=1", None, "code"), ("person:menu", "en", "catalog"),
                              # MCP: «человек» — тот, кто ведёт агента; проверяется только множественное
                              ("только по просьбе человека", "ru", "mcp"), ("only when the person asks", "en", "mcp"),
                              # README и install.sh правило не читает (старые «Что нового» не переписываем)
                              ("Люди и устройства", None, "doc"), ("echo 'Люди'", None, "install")):
            self.assertNotIn("people", rules_of(one(s, lang, kind)), s)
        texts = [(k, lang) for k, lang, _t in tc.mcp_texts(ROOT)]
        self.assertIn(("param_types.person.description", "ru"), texts)        # описания MCP читаются
        self.assertIn(("param_types.person.description", "en"), texts)

    def test_domestic(self):
        """29.10: роль «ru» — «внутренняя группа / внутренний выход», EN domestic; факты про страну остаются."""
        for s, lang, kind in (("Российские группы", "ru", "web"), ("Последняя российская группа", "ru", "catalog"),
                              ("только в российской группе", "ru", "sh"), ("Добавить российский ключ", "ru", "web"),
                              ("Российских выходов: {n}", "ru", "catalog"), ("российские и зарубежные группы", "ru", "web"),
                              ("перенос (зарубежная ↔ российская)", "ru", "mcp"), ("Группа «{g}» — российская: …", "ru", "catalog"),
                              ("российская", "ru", "catalog"), ("Российская", "ru", "web"),
                              ("Russian groups", "en", "web"), ("Add a Russian key", "en", "web"),
                              ("the last Russian group", "en", "mcp"), ("a foreign or a Russian one", "en", "catalog"),
                              ("Exit {exit} is Russian: …", "en", "catalog"), ("Russian", "en", "catalog"),
                              ("throw httpErr(400, \"Российской группы нет\")", None, "code")):
            self.assertIn("domestic", rules_of(one(s, lang, kind)), s)
        # факты про страну — не роль: разрешённый список (сайты, адреса, провайдеры, сети, App Store, ⚠РФ, метка РФ)
        for s, lang, kind in (("Внутренние группы", "ru", "web"), ("Domestic groups", "en", "web"),
                              ("Российские сайты → группа", "ru", "web"), ("Российские сайты группы", "ru", "web"),
                              ("Сервер не достаёт до российских адресов", "ru", "catalog"),
                              ("до серверов замера скорости российских провайдеров", "ru", "catalog"),
                              ("в России не меряется", "ru", "catalog"), ("Российский трафик пойдёт через роутер", "ru", "catalog"),
                              ("на территории Российской Федерации", "ru", "sh"), ("В российском App Store скрыт", "ru", "catalog"),
                              ("российские — через «{name}»", "ru", "web"), ("Россия напрямую", "ru", "web"), ("⚠РФ", "ru", "web"),
                              ("РФ", "ru", "web"), ("RU", "en", "web"),
                              ("Russian sites go through a domestic group", "en", "web"), ("in Russian networks", "en", "catalog"),
                              ("the Russian Federation", "en", "sh"), ("the Russian App Store", "en", "catalog"),
                              ("Russian — via “{name}”", "en", "web"), ("Russian sites direct", "en", "mcp"),
                              # README и install.sh правило не читает (старые «Что нового», комментарии кода)
                              ("Российские группы", None, "doc"), ("# российские ноги", None, "install")):
            self.assertNotIn("domestic", rules_of(one(s, lang, kind)), s)
        self.assertEqual(rules_of(one("Russian", "en", "catalog", key="lang.name.ru")), [])      # имя языка

    def test_vo_before_domestic(self):
        """З4 REV-score-rename: перед «внутренн…» — «во», и когда код склеивает подпись и ссылку соседних ключей."""
        for s in ("Внутренние выходы: {n} — в «Внутренних группах»", "ключ уже в внутренних выходах",
                  "В внутренней группе нет ключей"):
            self.assertIn("domestic", rules_of(one(s, "ru", "web")), s)
        for s in ("Внутренние выходы: {n} — во «Внутренних группах»", "ключ уже во внутренних выходах",
                  "Вы внутренние выходы не трогали", "в «Выходах»"):
            self.assertNotIn("domestic", rules_of(one(s, "ru", "web")), s)
        ru = {"a.pre": "Внутренние выходы: {n} — в", "a.link": "«Внутренних группах»",
              "b.pre": "Внутренние выходы: {n} — во", "b.link": "«Внутренних группах»",
              "c.pre": "Выходы — в", "c.link": "«Выходах»"}
        glued = [("web", "x/i18n-{ru,en}.js", {"ru": ru, "en": {}})]
        self.assertEqual([(f.src, f.key) for f in tc.domestic_glue_findings(glued)], [("x/i18n-ru.js", "a.pre")])

    def test_pairs(self):
        pairs = [("catalog", "a.{ru,en}.json", {"ru": {"a": "{n} шт", "b": "x", "c": "", "s": " "},
                                                  "en": {"a": "{m} pcs", "c": "y", "d": "z", "s": ","}}),
                 ("sh", "b.{ru,en}.sh", {"ru": {"A": "%s из %d, 5%%", "O": "%s из %d", "W": "%-12s|%5d"},
                                         "en": {"A": "%s of %s, 5%%", "O": "%d of %s", "W": "%-10s|%3d"}})]
        got = sorted((f.rule, f.key, f.match) for f in tc.pair_findings(pairs))
        # в SSH порядок важен: printf подставляет по порядку; ширина колонки в языках может различаться
        self.assertEqual(got, [("empty", "c", "пусто в RU"), ("holes", "A", "RU %s %d ≠ EN %s %s"),
                               ("holes", "O", "RU %s %d ≠ EN %d %s"),
                               ("holes", "a", "RU n ≠ EN m"), ("keys", "b", "нет в EN"), ("keys", "d", "нет в RU")])

    def test_code_literals(self):
        code = lambda s: rules_of(one(s, None, "code", "x.js", "ph"))  # noqa: E731
        self.assertIn("ip", code("45.10.20.32"))
        self.assertIn("domain", code("https://vpn.example.ru/sub"))
        self.assertEqual(code("Нужно 1.5 ГБ, 12МБ — 5 %"), [])                # числа в коде — не тексты правил
        self.assertEqual(code("203.0.113.5"), [])


    def test_code_literals_without_a_dot(self):
        """REV-texts-2: строка кода без точки, но с пробелом — текст («Сервер нужен в Нидерландах»); имя без пробела — нет."""
        d = tempfile.mkdtemp(prefix="k3-texts-js-")
        self.addCleanup(shutil.rmtree, d, True)
        js = os.path.join(d, "lib", "kaskad", "panel", "static", "x.js")
        os.makedirs(os.path.dirname(js))
        with open(js, "w", encoding="utf-8") as f:
            f.write('var a = { note: "Сервер нужен в Нидерландах", id: "kaskadPanel" };\n')
        texts = [x[2] for x in tc.code_texts(d)]
        self.assertIn("Сервер нужен в Нидерландах", texts)
        self.assertNotIn("kaskadPanel", texts)


class SpoiledCopyTest(unittest.TestCase):
    """Порча во временной копии текстов: проверка находит каждую, исходные файлы не трогаются.

    Каждый тест получает свою копию (setUp) — тесты не зависят от порядка, копия удаляется и при падении. Полная копия
    текстов — только у порчи (b): тестам c и d хватает своего файла (REV-texts-2 Н2: тест держится в 5 с с запасом).
    Чистую копию отдельно не проверяем — это test_no_new_violations на самом дереве."""

    ONLY = {"test_c_fixed_text_makes_the_exception_stale": ("lib/kaskad/i18n/catalog/ru/gw_in_accel.json",),
            "test_d_lost_closing_marker_fails_loudly": ("install.sh",)}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="k3-texts-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        only = self.ONLY.get(self._testMethodName)
        if only:
            for rel in only:
                os.makedirs(os.path.dirname(self.path(rel)) or self.tmp, exist_ok=True)
                shutil.copy(os.path.join(ROOT, rel), self.path(rel))
            return
        for rel in ("lib/kaskad/i18n/catalog", "lib/kaskad/i18n/sh"):
            shutil.copytree(os.path.join(ROOT, rel), os.path.join(self.tmp, rel))
        static = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
        for ext in ("js", "html"):
            for p in glob.glob(os.path.join(static, "**", "*." + ext), recursive=True):
                dst = os.path.join(self.tmp, os.path.relpath(p, ROOT))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(p, dst)
        for name in ("install.sh",) + tc.DOCS:
            shutil.copy(os.path.join(ROOT, name), os.path.join(self.tmp, name))

    def path(self, rel):
        return os.path.join(self.tmp, rel)

    def edit(self, rel, fn):
        with open(self.path(rel), encoding="utf-8") as f:
            text = f.read()
        with open(self.path(rel), "w", encoding="utf-8") as f:
            f.write(fn(text))

    def json_edit(self, rel, fn):
        with open(self.path(rel), encoding="utf-8") as f:
            doc = json.load(f)
        fn(doc)
        with open(self.path(rel), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)

    def check(self):
        allowed, _ = tc.read_exceptions()
        return tc.compare(tc.findings(self.tmp), allowed)

    def test_b_spoiled_copy_fails(self):
        cat = "lib/kaskad/i18n/catalog/%s/panel.json"
        ru_texts = {
            "zz_dom": "Адрес будет vpn.мой-сайт.ru, домен в зоне .ru",
            "zz_ip": "Например, 45.10.20.31",
            "zz_num": "Нужно 1.5 ГБ, а скачано 12МБ — 5 %",
            "zz_push": "Купите домен на reg.ru",
            "zz_url": "Ссылка https://vpn.мой-сайт.ru/sub/abc",           # S1: домен и адрес внутри ссылки
            "zz_ipurl": "Панель: http://45.10.20.31:8080/",
            "zz_mail": "Почта you@example.ru",                              # N8
            "zz_lat": "Задержка {ms} ms, раз в {n}мин",                     # N4
            "zz_rent": "Арендуйте VPS на Aeza",                             # N3
            "zz_land": "Сервер нужен в Нидерландах",
            "zz_reg": "Домен — через nic.ru",
            "zz_ver": "AmneziaVPN 4.8.12.9 или новее",                      # N2: не адрес
        }
        en_texts = {
            "zz_url": "Link https://vpn.example.ru/sub/abc",
            "zz_lat": "Delay {ms} ms, every {n}s",
            "zz_rent": "Rent a server in Germany",
            "zz_num": "1,500 MB free",                                      # N5: не нарушение
        }

        def ru(doc):
            doc["panel.zz_only_ru"] = "только по-русски"
            for k, v in ru_texts.items():
                doc["panel." + k] = v
            doc["panel.zz_holes"] = "Готово: {n}"
            doc["panel.zz_empty"] = ""

        def en(doc):
            for k in ru_texts:
                doc["panel." + k] = en_texts.get(k, "text")
            doc["panel.zz_lat"] = en_texts["zz_lat"]
            doc["panel.zz_holes"] = "Done: {count}"
            doc["panel.zz_empty"] = "Something"

        self.json_edit(cat % "ru", ru)
        self.json_edit(cat % "en", en)
        self.edit("lib/kaskad/i18n/sh/menu.ru.sh", lambda s: s + "M_ru_MM_ZZ_Q='Продолжить? (y/n) '\n"
                  "M_ru_MM_ZZ_ORD='%s из %d'\nM_ru_MM_ZZ_W='%-12s|'\nM_ru_MM_ZZ_YN='Удалить всё? y/N: '\n")
        self.edit("lib/kaskad/i18n/sh/menu.en.sh", lambda s: s + "M_en_MM_ZZ_Q='Go on? %s'\n"
                  "M_en_MM_ZZ_ORD='%d of %s'\nM_en_MM_ZZ_W='%-10s|'\nM_en_MM_ZZ_YN='Delete all? Enter y to confirm'\n")
        self.edit("install.sh", lambda s: s + '\nread -r ans; [ "$ans" = "y" ] && echo go\n'
                  'read -rp "Удалить всё? y/N: " a\n[[ $a == [yY]* ]] && go\ncase "$a" in д|Д|y|yes) go;; esac\n'
                  '[[ $a =~ ^(y|yes)$ ]] && go\necho "Нужно 1.5 ГБ"\n')
        self.edit("lib/kaskad/panel/static/ux-i18n-en.js", lambda s: s.replace('"c.copy": "Copy",\n', "", 1))
        self.edit("README.md", lambda s: s + "\nАдрес сервера, например 45.10.20.32.\nПочта you@example.ru\n")
        # S2 и макеты: строки в коде веба, которые видит человек; ключ словаря в t(…) — не текст
        self.edit("lib/kaskad/panel/static/setup/setup.js", lambda s: s + '\nconst zzF = { ph: "45.10.20.33" };\n')
        self.edit("lib/kaskad/panel/static/dev-mock.js", lambda s: s + '\nconst zzM = { url: `https://vpn.example.ru/s` };\n')
        self.edit("lib/kaskad/panel/static/ux.js", lambda s: s + "\nconst zzT = t(lang, 'zz.ru');\n")
        self.edit("lib/kaskad/panel/static/index.html", lambda s: s + "\n<p>45.10.20.34</p>\n")
        new, stale = self.check()
        got = set((f.rule, f.src.rsplit("/", 1)[-1], f.key, f.match) for f in new)
        want = {("keys", "panel.json", "panel.zz_only_ru", "нет в EN"),
                ("domain", "panel.json", "panel.zz_dom", "vpn.мой-сайт.ru"),
                ("zone", "panel.json", "panel.zz_dom", "зоне .ru"),
                ("ip", "panel.json", "panel.zz_ip", "45.10.20.31"),
                ("num-dec", "panel.json", "panel.zz_num", "1.5 ГБ"),
                ("num-unit", "panel.json", "panel.zz_num", "12МБ"), ("pct", "panel.json", "panel.zz_num", "5 %"),
                ("push", "panel.json", "panel.zz_push", "Купите домен на reg.ru"),
                ("holes", "panel.json", "panel.zz_holes", "RU n ≠ EN count"),
                ("empty", "panel.json", "panel.zz_empty", "пусто в RU"),
                ("domain", "panel.json", "panel.zz_url", "vpn.мой-сайт.ru"),
                ("domain", "panel.json", "panel.zz_url", "vpn.example.ru"),
                ("ip", "panel.json", "panel.zz_ipurl", "45.10.20.31"),
                ("domain", "panel.json", "panel.zz_mail", "example.ru"),
                ("num-unit", "panel.json", "panel.zz_lat", "{ms} ms"),
                ("num-unit", "panel.json", "panel.zz_lat", "{n}мин"),
                ("num-unit", "panel.json", "panel.zz_lat", "{n}s"),
                ("push", "panel.json", "panel.zz_rent", "Арендуйте VPS на Aeza"),
                ("push", "panel.json", "panel.zz_rent", "Rent a server in Germany"),
                ("push", "panel.json", "panel.zz_land", "Сервер нужен в Нидерландах"),
                ("push", "panel.json", "panel.zz_reg", "через nic.ru"),
                ("yn", "menu.ru.sh", "MM_ZZ_Q", "(y/n)"), ("holes", "menu.{ru,en}.sh", "MM_ZZ_Q", "RU — ≠ EN %s"),
                ("holes", "menu.{ru,en}.sh", "MM_ZZ_ORD", "RU %s %d ≠ EN %d %s"),
                ("yn", "menu.ru.sh", "MM_ZZ_YN", "y/N"), ("yn", "menu.en.sh", "MM_ZZ_YN", "Enter y"),
                ("yn", "install.sh", "", '= "y"'), ("yn", "install.sh", "", "y/N"),
                ("yn", "install.sh", "", "== [yY]*"), ("yn", "install.sh", "", "д|Д|y|yes)"),
                ("yn", "install.sh", "", "=~ ^(y"), ("num-dec", "install.sh", "", "1.5 ГБ"),
                ("keys", "ux-i18n-{ru,en}.js", "c.copy", "нет в EN"), ("ip", "README.md", "", "45.10.20.32"),
                ("domain", "README.md", "", "example.ru"),
                ("ip", "setup.js", "ph", "45.10.20.33"), ("domain", "dev-mock.js", "url", "vpn.example.ru"),
                ("ip", "index.html", "", "45.10.20.34")}
        self.assertEqual(sorted(want - got), [], "порча не найдена")
        wrong = [g for g in got if g[2] in ("panel.zz_ver", "MM_ZZ_W") or g[3] in ("zz.ru", "4.8.12.9")
                 or (g[2] == "panel.zz_num" and g[3].startswith("1,500"))]
        self.assertEqual(wrong, [], "ложное срабатывание")
        self.assertEqual(stale, [])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(tc.main(["--root", self.tmp]), 1)
        self.assertIn("НОВОЕ", out.getvalue())

    def test_c_fixed_text_makes_the_exception_stale(self):
        rel = "lib/kaskad/i18n/catalog/ru/gw_in_accel.json"
        self.json_edit(rel, lambda d: d.update({"gw_in_accel.warn.extra_virtual":
                                                d["gw_in_accel.warn.extra_virtual"].replace("0.8 ГБ", "0,8 ГБ")}))
        _new, stale = self.check()
        self.assertIn(("num-dec", "gw_in_accel.warn.extra_virtual"), [(f.rule, f.key) for f in stale])

    def test_d_lost_closing_marker_fails_loudly(self):
        # без «# <<< K3_I18N» остаток install.sh молча выпал бы из проверки — вместо этого ошибка
        self.edit("install.sh", lambda s: s.replace("\n# <<< K3_I18N", "\n# (метка потеряна)", 1))
        with self.assertRaisesRegex(ValueError, "K3_I18N"):
            tc.findings(self.tmp)


if __name__ == "__main__":
    unittest.main()
