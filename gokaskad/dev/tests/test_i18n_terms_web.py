"""Термины EN в веб-словарях (3.3.2, решения после критики T-332, docs/k3/GLOSSARY-en.md): панель, «Схема», мастер,
страница человека. Машина с Kaskad — server (не node); правила маршрутизации — Routing rules; режим без модуля ядра —
userspace (amneziawg-go), не program; Access from the internet; en-US; Please note; Delete key; типографский ’ и «…»;
платформы «iPhone / iPad», «macOS»; голос — «we» или безлично. Плюс точечные находки критика (№ в именах проверок)
и минимум пароля копии в мастере = backup.MIN_PASSWORD. Исключения — явным списком с причиной."""

import os
import re
import unittest

from kaskad import backup
from tests.test_i18n_web import dictionary, read

EN = ("i18n-en.js", "graph-i18n-en.js", "setup/setup-i18n-en.js", "user-i18n-en.js")


def en_items():
    for name in EN:
        for key, text in dictionary(name).items():
            yield name, key, text


def hits(pattern, allowed=(), flags=0):
    rx = re.compile(pattern, flags)
    return ["%s:%s %s" % (n, k, t) for n, k, t in en_items() if rx.search(t) and (n, k) not in allowed]


class TermsTest(unittest.TestCase):
    def test_server_not_node(self):
        self.assertEqual(hits(r"\bnodes?\b", flags=re.I), [])
        self.assertEqual(hits(r"\b(server|node) menu\b", flags=re.I), [], "раздел — «the SSH menu»")

    def test_rules_are_routing_rules(self):
        # «Rules»/«Rule» с большой буквы — подпись раздела, поля, узла «Схемы»: только «Routing rules». Строчное «rules»
        # в цепочках «Схемы» (device → … → rules → route) и рядом с ними однозначно — это и есть routing rules.
        self.assertEqual(hits(r"(?<!Routing )\bRules?\b"), [])
        for name, key in (("i18n-en.js", "nav.rules"), ("i18n-en.js", "pp.rules"), ("i18n-en.js", "tour.s3"),
                          ("graph-i18n-en.js", "graph.rules"), ("graph-i18n-en.js", "graph.insp.rules")):
            self.assertEqual(dictionary(name)[key], "Routing rules", key)

    def test_userspace_not_program(self):
        # «program» — только про чужие программы на сервере (сайт другой программы на 443, чужой nginx)
        other = {("i18n-en.js", k) for k in ("site.whyNeighbour", "site.fixNeighbour", "site.fixNginx",
                                             "site.defaultPageX")}
        self.assertEqual(hits(r"\bprogram\b", other, re.I), [])
        d = dictionary("i18n-en.js")
        self.assertEqual(d["awg.toProgram"], "Switch to userspace")
        for k in ("awg.desc", "awg.busyProgram", "awg.nowProgram", "awg.toProgramQ", "awg.mixText"):
            self.assertIn("userspace (amneziawg-go)", d[k].replace("Userspace", "userspace"), k)
        self.assertIn("userspace (amneziawg-go)", dictionary("setup/setup-i18n-en.js")["det.protocolsB"])

    def test_engine_is_only_the_mode(self):
        self.assertEqual(hits(r"\bno AmneziaWG engine\b|\bengine\b.{0,20}\bGitHub\b", flags=re.I), [])
        self.assertIn("Kaskad 3 core", dictionary("graph-i18n-en.js")["graph.rules.soon"])
        self.assertEqual(hits(r"gateway core", flags=re.I), [])

    def test_access_from_the_internet(self):  # №3
        self.assertEqual(hits(r"internet access|public mode", flags=re.I), [])
        d = dictionary("i18n-en.js")
        self.assertEqual(d["st.public"], "Access from the internet")
        self.assertIn("SSH menu: “Web panel → Access from the internet”", d["st.accessHow"])
        self.assertIn("SSH menu (“Web panel” section)", d["login.hint"])
        # ссылка на раздел — по настоящему имени пункта 18 SSH-меню (в RU было «Панель» при «Веб-панель» в меню)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for lang, name, q in (("ru", "i18n-ru.js", "«%s"), ("en", "i18n-en.js", "“%s")):
            with open(os.path.join(root, "lib", "kaskad", "i18n", "sh", "menu.%s.sh" % lang), encoding="utf-8") as f:
                title = re.search(r"^M_%s_MM_I18='([^']*)'" % lang, f.read(), re.M).group(1)
            for key in ("login.hint", "st.accessHow"):
                self.assertIn(q % title, dictionary(name)[key], (lang, key))

    def test_us_spelling(self):  # №24
        self.assertEqual(hits(r"neighbou?r|licence|\bgrey\b|colour|cancell|organis|behaviour|favour|centre",
                              flags=re.I), [])

    def test_please_note_not_attention(self):  # №34
        self.assertEqual(hits(r"\bAttention\b"), [])
        self.assertEqual(dictionary("setup/setup-i18n-en.js")["c.attention"], "Please note")
        self.assertEqual(dictionary("i18n-en.js")["c.attention"], "Please note")

    def test_delete_key(self):  # №20
        self.assertEqual(hits(r"\bRemove key\b", flags=re.I), [])
        # 3.3.3: the keys of the exits are rows of ExitsInput (ux.js, one with the panel) — × «Delete {name}»
        d = dictionary("ux-i18n-en.js")
        self.assertEqual(d["ei.del"], "Delete {name}")

    def test_typography(self):  # №25
        self.assertEqual(hits(r"[A-Za-z]'[A-Za-z]|\.\.\."), [], "апостроф ’ и многоточие …")

    def test_voice(self):  # №33: в вебе «we» или безлично
        # «I’ll add keys in the panel» — ответ самого админа на выбор (как «I have a domain»), не голос продукта
        self.assertEqual(hits(r"\bI(’|')(ll|ve|m|d)\b", {("setup/setup-i18n-en.js", "ex.laterD")}), [])

    def test_platforms(self):  # №34
        self.assertEqual(hits(r"iPhone (and|or) iPad|iPhone, iPad|\bMac\b"), [])
        self.assertEqual(dictionary("i18n-en.js")["plat.ios"], "iPhone / iPad")
        self.assertEqual(dictionary("graph-i18n-en.js")["graph.platform.ios"], "iPhone / iPad")
        self.assertEqual(dictionary("user-i18n-en.js")["platforms.ios"], "iPhone / iPad")

    def test_glossary_names(self):  # №8, №11, №4
        self.assertEqual(hits(r"network card|narrow card|address for devices", flags=re.I), [])
        self.assertEqual(dictionary("i18n-en.js")["st.gwHost"], "Client address")
        self.assertEqual(dictionary("i18n-en.js")["bk.title"], "Settings backup")
        self.assertIn("“Settings backup”", dictionary("setup/setup-i18n-en.js")["imp.bakUndo"])
        self.assertIn("«Копия настроек»", dictionary("setup/setup-i18n-ru.js")["imp.bakUndo"])
        self.assertEqual(hits(r"your server abroad|servers abroad", flags=re.I), [], "exit = your VPN abroad")


class FindingsTest(unittest.TestCase):
    def test_wizard_heads(self):  # №10
        d = dictionary("setup/setup-i18n-en.js")
        self.assertEqual(d["head.welcomeWhy"], "About {n} minutes without a domain; you can skip any stage.")
        self.assertEqual(d["head.doneClassicWhy"], "We’ll check that the forward exists and the other server answers.")
        self.assertEqual(d["head.exitsWhy"], "An exit is your VPN abroad.")

    def test_short_password_placeholder(self):  # №15: на 390 под кнопкой «Show» не прячется конец
        self.assertLessEqual(len(dictionary("setup/setup-i18n-en.js")["w.pwPh"]), 16)

    def test_no_pause_texts(self):  # owner, 3.3.2: the parts of a QR change by themselves — no pause, no buttons
        for name, keys in (("i18n-en.js", ("qr.play", "qr.pause")), ("setup/setup-i18n-en.js", ("qr.play", "qr.pause")),
                           ("user-i18n-en.js", ("qr_play", "qr_pause"))):
            for key in keys:
                self.assertNotIn(key, dictionary(name), (name, key))
        self.assertEqual(hits(r"cycling", flags=re.I), [])

    def test_critic2_small_things(self):  # critic2 №12, 22, 23
        s = dictionary("setup/setup-i18n-en.js")
        self.assertEqual(s["imp.move"], "Move")
        self.assertEqual(hits(r"\b[Tt]he club\b|anten-ka club"), [], "клуб — «the Anten-ka club»")
        self.assertIn("The Anten-ka club (where you got Kaskad)", dictionary("i18n-en.js")["gh.why"])
        self.assertNotIn("twice as fast", dictionary("i18n-en.js")["awg.desc"])
        self.assertIn("вдвое меньше процессора", dictionary("i18n-ru.js")["awg.desc"])   # RU и EN — одно: half the CPU
        self.assertIn("“Forwarding engine and migration”", dictionary("i18n-en.js")["fw.legacyCode"])

    def test_sentence_case_and_no_broken_range(self):  # №27, №41
        d = dictionary("i18n-en.js")
        # «awg.toInbounds» (совет движка на «Обзоре») снят в UI-6 (a066c525) — остаётся одна кнопка «ov.toInbounds»
        self.assertNotIn("awg.toInbounds", d)
        self.assertEqual(d["ov.toInbounds"], "Go to protocols")
        self.assertNotIn("20–35 seconds", d["ex.listText"], "диапазон рвётся на строке")
        self.assertEqual(hits(r"\bmove over\b", flags=re.I), [])

    def test_one_name_for_the_placeholder_page(self):  # №32
        self.assertEqual(hits(r"simple (placeholder )?page|stub page|Domain & site"), [])


class BackupPasswordTest(unittest.TestCase):
    """№30: мастер требует тот же минимум пароля копии, что ядро (backup.MIN_PASSWORD), панель и SSH."""

    def test_wizard_min(self):
        n = backup.MIN_PASSWORD
        js = read("setup/setup.js")
        m = re.search(r'disabled: \(u\.pw \|\| ""\)\.length < (\d+), k: "bak-dl"', js)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), n)
        self.assertTrue(dictionary("setup/setup-i18n-en.js")["x.bakPwH"].startswith("At least %d characters" % n))
        self.assertTrue(dictionary("setup/setup-i18n-ru.js")["x.bakPwH"].startswith("Не короче %d символов" % n))
        self.assertIn("BACKUP_PW_MIN = %d;" % n, read("app.js"))


class NoscriptTest(unittest.TestCase):
    """№36: без JavaScript англоязычный видит английский первым (обе строки помечены lang)."""

    def test_english_first(self):
        for name in ("index.html", "setup/index.html", "user.html"):
            m = re.search(r"<noscript>(.*?)</noscript>", read(name), re.S)
            self.assertIsNotNone(m, name)
            body = m.group(1)
            en, ru = body.find('<span lang="en">'), body.find('<span lang="ru">')
            self.assertTrue(0 <= en < ru, name)
            self.assertIsNone(re.search("[А-Яа-яЁё]", body[:ru]), name)

    def test_only_the_server_language_is_shown(self):
        """Стили страницы прячут строку другого языка и разделитель (html lang ставит сервер; CSP — внешний CSS)."""
        for name, css in (("index.html", "components.css"), ("setup/index.html", "components.css"),
                          ("user.html", "user.css")):
            body = re.search(r"<noscript>(.*?)</noscript>", read(name), re.S).group(1)
            self.assertIn('<span class="kk-ns-sep" aria-hidden="true"> · </span>', body, name)
            rules = read(css)
            for sel in ('html[lang="en"] noscript [lang="ru"]', 'html[lang="ru"] noscript [lang="en"]',
                        'html[lang="en"] noscript .kk-ns-sep', 'html[lang="ru"] noscript .kk-ns-sep'):
                self.assertIn(sel, rules, css)


if __name__ == "__main__":
    unittest.main()
