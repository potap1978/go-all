"""Kaskad 3.3.7.10 — «Пользователи» instead of «Люди», the subscription with every protocol of the server (AmneziaWG and
WireGuard next to VLESS and Hysteria2), the choice at the QR button, «Пользователи» and «Выходы» updating by themselves,
the rows of 01b; one release with everything that was going (24.10): the Russian groups (the Russian sites of chosen
users through the owner's router), the bot to Telegram through the exits of a group, «Поделиться» (a clip of 15 s with
the times UTC and МСК and the points of the measure) and the test «От вашего устройства до сервера»: one version (the
first with two digits in its last part — the update must see it as newer than 3.3.7.9), and «Что нового» of 3.3.7.10
for the admin, tied to what the code does."""
import json
import re
import subprocess
import unittest

import kaskad
from kaskad.gw import model
from tests.test_3371_release import entry, text


def _js(lang, name="i18n-%s.js"):
    return dict(re.findall(r'(?m)^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', text("lib/kaskad/panel/static/" + name % lang)))


def _cat(lang, name):
    return json.loads(text("lib/kaskad/i18n/catalog/%s/%s.json" % (lang, name)))


class Release33710Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.10")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.10")
        self.ru, self.en = re.sub(r"\s+", " ", self.ru), re.sub(r"\s+", " ", self.en)

    def test_one_version(self):
        # 3.3.7.11 is above it now (tests/test_33711_release.py): the version is its, the heading names 3.3.7.10 too
        self.assertTrue(kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.8, 3\.3\.7\.9, 3\.3\.7\.10[ ,]")
        self.assertLess(self.readme.index("**3.3.7.10 — "), self.readme.index("**3.3.7.9 — "))
        order = subprocess.run(["sort", "-V"], input="3.3.8\n3.3.7.10\n3.3.7.9\n", capture_output=True, text=True).stdout
        self.assertEqual(order.split(), ["3.3.7.9", "3.3.7.10", "3.3.8"])

    def test_the_update_sees_it_newer(self):
        """The menu's update compares versions with ``k3_ver_lt`` of install.sh (sort -V), never as strings: 3.3.7.10
        is newer than 3.3.7.9 — a string compare would say «older» and offer nothing."""
        fn = re.search(r"(?m)^k3_ver_lt\(\) \{.*\}$", text("install.sh")).group(0)
        for a, b, lt in (("3.3.7.9", "3.3.7.10", 0), ("3.3.7.10", "3.3.7.9", 1), ("3.3.7.10", "3.3.7.10", 1),
                         ("3.3.7.10", "3.3.8", 0), ("3.3.3", "3.3.7.10", 0)):
            rc = subprocess.run(["bash", "-c", fn + '\nk3_ver_lt "$1" "$2"', "_", a, b], capture_output=True).returncode
            self.assertEqual(rc, lt, (a, b))

    def test_what_came_has_no_grey_marks(self):
        """«Российские группы», «Поделиться» и прокси для API Telegram вошли в 3.3.7.10: в подменю «Выходов» российские
        группы — обычная часть, «Скоро → Прокси для API Telegram» нет, кнопка «Поделиться» живая. Номер будущего выпуска
        у серого пункта (RELnn, SHARE_WHEN), если появится снова, никогда не называет эту версию или старше."""
        app, diag = text("lib/kaskad/panel/static/app.js"), text("lib/kaskad/panel/static/pages/diag.js")
        n2 = re.search(r"(?s)\n  const N2 = \{.*?\n  \};", app).group(0)
        exits = re.search(r"(?s)exits: \[.*?\]\]\]\],", n2).group(0)
        self.assertIn('["ru", "n2.exRu", "router"]', exits)
        self.assertNotIn("n2.exTg", app)
        self.assertNotIn("SHARE_WHEN", diag)
        self.assertIn('U.btn(t("dg.share"), "tertiary", () => openShare()', diag)
        marks = []
        for src in (app, diag):
            marks += re.findall(r'\b(?:REL\d+|SHARE_WHEN)\s*=\s*"(\d+(?:\.\d+)+)"', src)
        for v in marks:
            order = subprocess.run(["sort", "-V"], input="%s\n%s\n" % (v, kaskad.__version__), capture_output=True,
                                   text=True).stdout.split()
            self.assertEqual(order, [kaskad.__version__, v], v)
            self.assertNotEqual(v, kaskad.__version__)

    def test_the_new_parts_tied_to_the_code(self):
        """Что README называет в новых частях выпуска — те же слова, что в панели, боте и командах."""
        for lang, part, q in (("ru", self.ru, "«%s»"), ("en", self.en, '"%s"')):
            core, ux = _js(lang), _js(lang, "ux-i18n-%s.js")
            sp, dg = _js(lang, "pages/speed-i18n-%s.js"), _js(lang, "pages/diag-i18n-%s.js")
            bot = _cat(lang, "bot")
            self.assertIn(q % (core["nav.exits"] + " › " + ("Российские группы" if lang == "ru" else "Russian groups")), part)  # имя до 29.10
            self.assertIn(q % core["rs.pickBtn"], part)
            self.assertIn(q % ux["tb.pxT"], part)                                            # бот через выходы
            self.assertIn(q % sp["panel.speed.title"], part)                                 # замер из браузера
            self.assertIn(q % sp["panel.speed.btn"], part)
            self.assertIn(q % dg["dg.share"], part)                                          # «Поделиться»
            self.assertIn(q % bot["bot.share.btn"], part)
            self.assertIn(q % bot["bot.menu.btn_system"], part)
            for dns in ("cloudflare", "google", "yandex"):                                   # «чьи это цифры»
                self.assertIn(dg["dg.sh.dns." + dns], ("Cloudflare", "Google", "Яндекс", "Yandex"))
            self.assertIn("UTC", part)
        self.assertIn("15 секунд", self.ru)
        self.assertIn("15-second", self.en)
        self.assertRegex(text("lib/kaskad/panel/static/pages/diag-share.js"), r"const DUR = 15;")
        self.assertIn("ru-via <user|device>", text("lib/kaskad/rules/cli.py"))
        self.assertIn('add_parser("proxy"', text("lib/kaskad/bot/cli.py"))
        self.assertIn('@command("/share")', text("lib/kaskad/bot/share.py"))
        for part in (self.ru, self.en):
            for cmd in ("`kaskadctl rules ru-via`", "`kaskadctl bot proxy`", "`/share`"):
                self.assertIn(cmd, part)

    def test_tied_to_the_code(self):
        js = {lang: _js(lang) for lang in ("ru", "en")}
        self.assertIn("«%s»" % js["ru"]["nav.people"], self.ru)
        self.assertIn('"%s"' % js["en"]["nav.people"], self.en)
        self.assertIn("«%s»" % js["ru"]["qp.sub"], self.ru)
        self.assertIn("«%s»" % js["ru"]["qp.static"], self.ru)
        self.assertIn('"%s"' % js["en"]["qp.sub"], self.en)
        self.assertIn('"%s"' % js["en"]["qp.static"], self.en)
        for lang, part in (("ru", self.ru), ("en", self.en)):
            self.assertIn(js[lang]["pp.k1.nOf"].format(n="N", total="M"), part)
            for app in ("v2RayTun", "Hiddify", "v2rayN", "Streisand", "incy"):
                self.assertIn(app, js[lang]["sb.what"])                   # the panel names the same apps
                self.assertIn(app, part)
        self.assertIn("ждут новых настроек", js["ru"]["mtu.waitT"])
        self.assertIn("«ждут новых настроек»", self.ru)
        self.assertIn("waiting for new settings", js["en"]["mtu.waitT"])
        self.assertIn('"waiting for new settings"', self.en)
        # the protocols of the subscription — the README's list, the panel's choice and the model are the same
        app = text("lib/kaskad/panel/static/app.js")
        sub_ib = re.findall(r'"([a-z0-9]+)"', re.search(r"const SUB_IB = \[([^\]]*)\]", app).group(1))
        self.assertEqual(set(sub_ib), set(model.SUB_KINDS) | set(model.SUB_CONF_KINDS))
        self.assertEqual(set(model.SUB_KINDS), {"vless", "hy2"})
        self.assertEqual(set(model.SUB_CONF_KINDS), {"awg3", "awg2", "awg1", "wg"})
        for kind in ("socks5", "l2tp"):                                   # «например, только SOCKS5 или L2TP»
            self.assertNotIn(kind, sub_ib)
        for part in (self.ru, self.en):
            for proto in ("VLESS", "Hysteria2", "AmneziaWG", "WireGuard", "SOCKS5", "L2TP"):
                self.assertIn(proto, part)
            self.assertIn("goKaskad PRO · AmneziaWG 2.0 · ", part)          # the name of an entry (tests/test_sub_awg.py)

    def test_russian_and_english(self):
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))
        for part in (self.ru, self.en):
            self.assertNotRegex(part, r"\.ru\b")
            self.assertIsNone(re.search(r"\b(REV|SEC|DQA|D-\d+|Н-\d|Q\d|ACT-\d+|AC\d|М-\d|01b)\b", part))
        self.assertIsNone(re.search(r"(?i)\bnodes?\b", self.en))
        self.assertNotRegex(self.ru, r"(?i)человек")                       # the new word only (D-138)
        self.assertNotRegex(self.en, r"(?i)\bperson\b")


if __name__ == "__main__":
    unittest.main()
