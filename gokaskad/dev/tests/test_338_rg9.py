"""REV-338 З-1 (решение координатора 19.10): поведение не меняем — без выходов и при «напрямую» у кого-то на узле
``fail-block`` людей «всё через выход» стоит выше их правил, поэтому у них с «Россия напрямую» российские сайты закрыты
и по имени, и по адресу. Тексты говорят это правдиво: строка панели ``ep.dnsMainBlock`` — «российские сайты не
откроются» без «по имени» и без слов про DNS; Р-Г9 и «Что нового» следующего выпуска (RU и EN) — о случае целиком."""
# k3-select: files=README.md,docs/k3/SPEC-stage1-exit-groups.md,lib/kaskad/panel/static/pages/exits-groups-i18n-*.js
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def text(*name):
    with open(os.path.join(ROOT, *name), encoding="utf-8") as f:
        return f.read()


def flat(s):
    return re.sub(r"\s+", " ", s)


def panel_line(lang, key):
    src = text("lib", "kaskad", "panel", "static", "pages", "exits-groups-i18n-%s.js" % lang)
    return json.loads('"%s"' % re.search(r'^  "%s": "((?:[^"\\]|\\.)*)"' % re.escape(key), src, re.M).group(1))


class DnsMainBlockLineTest(unittest.TestCase):
    def test_no_by_name_no_dns(self):
        ru, en = panel_line("ru", "ep.dnsMainBlock"), panel_line("en", "ep.dnsMainBlock")
        self.assertIn("российские сайты не откроются — даже у пользователей с «Россия напрямую»", ru)
        self.assertIn("Russian sites won’t open — even for users with “Russian sites direct”", en)
        self.assertNotIn("по имени", ru)
        self.assertNotIn("by name", en)
        for line in (ru, en):
            self.assertNotIn("DNS", line)

    def test_ru_group_line_says_the_same(self):
        # REV-338-2 П-1: «по имени» is not the whole truth — with no exit drawn the fail-block closes them altogether
        ru, en = panel_line("ru", "ep.dnsRuGroup"), panel_line("en", "ep.dnsRuGroup")
        self.assertIn("российские сайты у пользователей этой группы не откроются", ru)
        self.assertIn("Russian sites won’t open for users of this group", en)
        self.assertNotIn("по имени", ru)
        self.assertNotIn("by name", en)


class NotesTest(unittest.TestCase):
    def test_rg9_says_the_case(self):
        row = next(ln for ln in text("docs", "k3", "SPEC-stage1-exit-groups.md").splitlines()
                   if ln.startswith("| Р-Г9."))
        for words in ("ни одного включённого выхода (нет, выключены владельцем или сняты квотой",
                      "лежащие выходы сюда не относятся", "а у кого-то на узле маршрут «напрямую»", "«Россия напрямую»",
                      "закрыты и по имени, и по адресу", "мимо выхода", "fail-block"):
            self.assertIn(words, row)

    def test_whats_new_of_the_next_release(self):
        readme = text("README.md")
        i = readme.index("### Что нового")
        m = re.compile(r"(?m)^\*\*(3\.3\.7\.1) — ").search(readme, i)    # 3.3.7.1 (3.3.7.2 is above it now)
        self.assertIsNotNone(m)
        nxt = re.compile(r"(?m)^\*\*\d+(?:\.\d+){2,3} — ").search(readme, m.end())
        ru, sep, en = readme[m.start():nxt.start()].partition("*In English.*")
        self.assertTrue(sep)
        ru, en = flat(ru), flat(en)
        # REV-338-2 З-1: the case is «no exit turned on», not «exits down»; the leak (names past the exit) is said
        self.assertIn("Ни одного включённого выхода** (не добавлены, выключены или сняты квотой)", ru)
        self.assertIn("у кого-то на сервере маршрут «напрямую»", ru)
        self.assertIn("адреса сайтов остальных людей мимо выхода", ru)
        self.assertIn("«Россия напрямую»", ru)
        # REV-3371 З-1: «раньше открывались» — только о VLESS и SOCKS5 (Hysteria2 в 3.3.7 не было); Hysteria2 — без
        # «UDP у хостера не проверено» (такого предупреждения нет), с мобильными сетями РФ, запасным VLESS и роутерами
        self.assertIn("ни по имени, ни по адресу — на VLESS и SOCKS5 раньше открывались, на новом Hysteria2 так же закрыты", ru)
        self.assertNotIn("у хостера", ru)
        self.assertIn("Блокировки UDP в мобильных сетях РФ не проверены — если не подключается, VLESS (TCP)", ru)
        self.assertIn("Роутеры Hysteria2 не поддерживают", ru)
        self.assertIn("Если выходы есть, но не работают, всё как раньше", ru)
        self.assertNotIn("Нет работающих выходов", ru)
        self.assertIn("No exit is turned on** (none added, all turned off or taken out by the quota)", en)
        self.assertIn("someone on the server has the route “Direct”", en)
        self.assertIn("addresses of the sites of everyone else past the exit", en)
        self.assertIn("“Russian sites direct”", en)
        self.assertIn("neither by name nor by address (on VLESS and SOCKS5 they opened before; on the new Hysteria2 they "
                      "are closed the same way)", en)
        self.assertNotIn("through the host", en)
        self.assertIn("Blocking of UDP in Russian mobile networks has not been checked", en)
        self.assertIn("Routers don’t support Hysteria2", en)
        self.assertNotIn("No working exit", en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", en))
        for part in (ru, en):
            self.assertNotIn("DNS", part)


if __name__ == "__main__":
    unittest.main()
