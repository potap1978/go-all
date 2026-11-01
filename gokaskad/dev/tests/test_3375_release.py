"""Kaskad 3.3.7.5 — the hotfix of the panel's address: one version, and «Что нового» of 3.3.7.5 for people, honest about
the update (the old address without the path stops at once, where the new one is) and tied to what the code says."""
import re
import unittest

import kaskad
from tests.test_3371_release import entry, text


class Release3375Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.5")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.5")
        self.ru, self.en = re.sub(r"\s+", " ", self.ru), re.sub(r"\s+", " ", self.en)

    def test_one_version(self):
        # 3.3.7.6 is above it now (tests/test_3376_release.py): the version is its, the heading names 3.3.7.5 too
        self.assertTrue(kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*3\.3\.7\.5[^)]*\)")
        self.assertLess(self.readme.index("**3.3.7.5 — "), self.readme.index("**3.3.7.4 — "))

    def test_honest_about_the_update(self):
        for words in ("старый адрес без пути сразу перестаёт открываться", "kaskadctl panel status --show",
                      "пункт 18", "F1 или «п»", "Войти в панель после обновления нужно заново",
                      "Страницы людей и выданные им ссылки работают как раньше", "«Адрес панели»",
                      "SSTP и OpenConnect", "с сайтом на домене теперь строятся на домене",
                      "Уже выданные ссылки с портом открываются, пока включён запасной вход по порту",
                      "сертификате Let’s Encrypt для IP-адреса, если он выпущен; иначе — на самоподписанном",
                      "вход по порту выключается сам — после первого входа администратора в панель через домен",
                      "если ни одной ссылки людей с портом ещё не выдавалось", "можно выключить",
                      "людям нужно выдать новые", "«Убрать домен» сначала включает его", "остаётся как был"):
            self.assertIn(words, self.ru)
        for words in ("the old address without the path stops opening right after the update",
                      "kaskadctl panel status --show", "item 18", "Sign in to the panel again",
                      "People’s pages and the links given to them work as before", "“Panel address”",
                      "SSTP and OpenConnect", "are now built on the domain",
                      "Links with the port given earlier keep opening while the backup entry by port is on",
                      "Let’s Encrypt certificate for the IP address if it was issued; otherwise a self-signed one",
                      "goes off by itself — after the admin’s first sign-in to the panel through the domain",
                      "no people’s link with the port has been given yet", "can be turned off",
                      "people need new ones", "“Remove domain” switches it on first", "stays as it was"):
            self.assertIn(words, self.en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))

    def test_plain_words(self):
        codes = re.compile(r"\b(REV|SEC|UI-1|F\d\d|Н-[\w-]+|Q[1-7]|R-s1|T1[0-9]|D-\d{3}|КТ\d|f19b)\b")
        for part in (self.ru, self.en):
            self.assertIsNone(codes.search(part), codes.search(part))
            self.assertNotRegex(part, r"\.ru\b")
            for push in ("обязательно", "должны", "немедленно", "must ", "immediately"):
                self.assertNotIn(push, part)
            self.assertNotRegex(part, r"app-[A-Za-z0-9_-]{8,}")      # no secret-looking path in the text

    def test_what_it_names_is_in_the_code(self):
        st = "lib/kaskad/panel/static/"
        ru, en = text(st + "i18n-ru.js"), text(st + "i18n-en.js")
        for k, v in (('"site.panelT": "Адрес панели"', ru), ('"site.panelT": "Panel address"', en),
                     ('"site.fallbackOff": "Выключить запасной вход"', ru), ('"site.fallbackOff": "Turn off the backup entry"', en),
                     ('"site.rotate": "Сменить секретный адрес"', ru), ('"site.rotate": "Change secret address"', en),
                     ('"st.access": "Доступ к панели"', ru), ('"st.access": "Panel access"', en)):
            self.assertIn(k, v)
        self.assertNotIn('"site.panelUrl"', ru)                   # «Новый адрес панели» is gone from the cert card
        self.assertIn('"--show"', text("lib/kaskad/panel/cli.py"))
        self.assertIn("M_ru_MM_H_REVEAL='F1 или «п» — показать'", text("lib/kaskad/i18n/sh/menu.ru.sh"))
        # round 3: the port goes off by itself after the first sign-in through the domain, never in the wizard
        self.assertNotIn("set_public", text("lib/kaskad/setup/web.py"))
        self.assertIn("portoff.mark_pending()", text("lib/kaskad/setup/steps.py"))
        self.assertIn("_domain_sign_in()", text("lib/kaskad/panel/api.py"))
        self.assertIn("portoff.mark_port_link()", text("lib/kaskad/panel/userpage.py"))
        self.assertIn("way_in=True", text("lib/kaskad/site/panel.py"))
        self.assertIn("def pages_domain", text("lib/kaskad/panel/conf.py"))
        self.assertIn('"st.fbOffPages"', ru)
        self.assertIn("M_ru_PNL_FB_OFF_WARN=", text("lib/kaskad/i18n/sh/panel.ru.sh"))
        from kaskad.panel import server
        self.assertEqual(server.public_view({"proxy": {"path": ""}}, "/")[3], True)      # no secret: nothing answers


if __name__ == "__main__":
    unittest.main()
