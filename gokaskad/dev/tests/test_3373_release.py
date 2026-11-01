"""Kaskad 3.3.7.3 — the panel's layout (UI-1), incy and Karing, sign-out and revoked access (F19, F19b), the cards of the
protocols (stand 3.3.7.2 Н-8): one version, and «Что нового» of 3.3.7.3 for people, tied to what the code says."""
import json
import re
import subprocess
import unittest

import kaskad
from tests.test_3371_release import entry, text


class Release3373Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.3")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.3")
        self.ru, self.en = re.sub(r"\s+", " ", self.ru), re.sub(r"\s+", " ", self.en)

    def test_one_version(self):
        # 3.3.7.4 is above it now (tests/test_3374_release.py): the version is its, the heading names 3.3.7.3 too
        self.assertTrue(kaskad.__version__ == "3.3.7.3" or kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.1, 3\.3\.7\.2(, 3\.3\.7\.\d+)* и 3\.3\.7\.\d+\)")
        self.assertLess(self.readme.index("**3.3.7.3 — "), self.readme.index("**3.3.7.2 — "))
        order = subprocess.run(["sort", "-V"], input="3.3.8\n3.3.7.3\n3.3.7.2\n", capture_output=True, text=True).stdout
        self.assertEqual(order.split(), ["3.3.7.2", "3.3.7.3", "3.3.8"])

    def test_russian_and_english(self):
        for words in ("«Подробнее»", "incy", "Karing", "«Выход не подтверждён»", "«Пароль и в SSH-туннеле»",
                      "секретный путь", "443/udp", "443/tcp", "«Применить настройки»",
                      "«Настройки» — в одну колонку"):
            self.assertIn(words, self.ru)
        for words in ("“Details”", "incy", "Karing", "“Sign-out not confirmed”", "“Password in SSH tunnel too”",
                      "secret path", "443/udp", "443/tcp", "“Apply settings”",
                      "Settings is one column"):
            self.assertIn(words, self.en)
        # REV-3373: без обещаний «одной высоты / ровные колонки» и без совета выпустить сертификат заново (pin меняется)
        for words in ("одной высоты", "ровные колонки", "выпустить сертификат заново"):
            self.assertNotIn(words, self.ru)
        for words in ("one height", "even columns", "issuing the certificate again"):
            self.assertNotIn(words, self.en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))

    def test_plain_words(self):
        codes = re.compile(r"\b(REV|SEC|UI-1|F\d\d|F19b|Н-8|R-s1|T1[0-9]|К2-\d|Д-\d|З-\d|Б-\d|П-\d|С-\d|D-\d{3}|КТ\d)\b")
        for part in (self.ru, self.en):
            self.assertIsNone(codes.search(part), codes.search(part))
            self.assertNotRegex(part, r"\.ru\b")
            for push in ("обязательно", "должны", "немедленно", "must ", "immediately"):
                self.assertNotIn(push, part)

    def test_what_it_names_is_in_the_code(self):
        ru, en = text("lib", "kaskad", "panel", "static", "i18n-ru.js"), text("lib", "kaskad", "panel", "static", "i18n-en.js")
        self.assertIn('"st.reqPw": "Пароль и в SSH-туннеле"', ru)
        self.assertIn('"st.reqPw": "Password in SSH tunnel too"', en)
        self.assertIn('"c.logoutUnconfirmed": "Выход не подтверждён', ru)
        self.assertIn('"c.logoutUnconfirmed": "Sign-out not confirmed', en)
        self.assertIn('"pc.details": "Подробнее"', text("lib", "kaskad", "panel", "static", "ux-i18n-ru.js"))
        self.assertIn('"pc.details": "Details"', text("lib", "kaskad", "panel", "static", "ux-i18n-en.js"))
        apps = json.loads(text("lib", "kaskad", "gw", "install_apps.json"))
        names = json.dumps(apps, ensure_ascii=False)
        self.assertIn('"name": "incy"', names)
        self.assertIn('"name": "Karing"', names)
        from kaskad.gw import inlog
        self.assertEqual(inlog.CERT_KINDS, ("hy2",))
        cat = json.loads(text("lib", "kaskad", "i18n", "catalog", "ru", "gw_inlog.json"))
        self.assertIn("выпустите его заново", cat["gw_inlog.human.cert"])


if __name__ == "__main__":
    unittest.main()
