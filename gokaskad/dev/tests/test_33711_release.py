"""Kaskad 3.3.7.11 — what the stage-1 branch adds to 3.3.7.10: «Пользователи» and «Выходы» at least every 5 s while
the panel watches (the light round of the collector), «Вернуть как было» bringing back the domain, the certificate,
the site and VLESS TLS of this very server, a move into a Russian group without «Неверный id выхода» (no doubles of a
key), the Russian exits in ``gw exits``, the version of a row of «Российские сайты», the feed of the exit levels and
the bot without a summary of a blip, the «График активности» in hours: one version (3.3.7.11 newer than 3.3.7.10 for
the update), and «Что нового» of 3.3.7.11 for the admin, tied to what the code does. The lines about the refresh
every 5 s that had got into the block of 3.3.7.10 live in 3.3.7.11 (a server on 3.3.7.10 does not do it)."""
import json
import re
import subprocess
import unittest

import kaskad
from tests.test_3371_release import entry, text


def _js(lang, name="i18n-%s.js"):
    return dict(re.findall(r'(?m)^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', text("lib/kaskad/panel/static/" + name % lang)))


def _cat(lang, name):
    return json.loads(text("lib/kaskad/i18n/catalog/%s/%s.json" % (lang, name)))


def _flat(s):
    return re.sub(r"\s+", " ", s)


class Release33711Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.11")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.11")
        self.ru, self.en = _flat(self.ru), _flat(self.en)

    def test_one_version(self):
        # 3.3.7.12 is above it now (tests/test_33712_release.py): the version is its, the heading names 3.3.7.11 too
        self.assertTrue(kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.9, 3\.3\.7\.10, 3\.3\.7\.11[ ,]")
        self.assertLess(self.readme.index("**3.3.7.12 — "), self.readme.index("**3.3.7.11 — "))
        self.assertLess(self.readme.index("**3.3.7.11 — "), self.readme.index("**3.3.7.10 — "))

    def test_the_update_sees_it_newer(self):
        """``k3_ver_lt`` of install.sh (sort -V): 3.3.7.11 is newer than 3.3.7.10 and 3.3.7.9, older than 3.3.8."""
        fn = re.search(r"(?m)^k3_ver_lt\(\) \{.*\}$", text("install.sh")).group(0)
        for a, b, lt in (("3.3.7.10", "3.3.7.11", 0), ("3.3.7.11", "3.3.7.10", 1), ("3.3.7.11", "3.3.7.11", 1),
                         ("3.3.7.9", "3.3.7.11", 0), ("3.3.7.11", "3.3.8", 0), ("3.3.3", "3.3.7.11", 0)):
            rc = subprocess.run(["bash", "-c", fn + '\nk3_ver_lt "$1" "$2"', "_", a, b], capture_output=True).returncode
            self.assertEqual(rc, lt, (a, b))

    def test_the_refresh_of_5_s_is_this_version(self):
        """REV-s1fin З-3: the light round (``LIGHT_MAX`` 5 s) came after 3.3.7.10 — its words are in 3.3.7.11 only, the
        block of 3.3.7.10 says what that release does (about 10 s, the exits once a minute)."""
        self.assertRegex(text("lib/kaskad/gw/collect.py"), r"(?m)^LIGHT_MAX = 5(\.0)? ")
        self.assertIn("не реже раза в 5 секунд", self.ru)
        self.assertIn("at least every 5 seconds", self.en)
        old = _flat(entry(self.readme, "3.3.7.10"))
        self.assertNotRegex(old, r"(?<![\d,.])5 (секунд|seconds)\b")                  # «15 секунд» of the clip stays
        self.assertIn("примерно раз в 10 секунд, линии выходов — раз в минуту", old)
        self.assertIn("about every 10 seconds, the lines of the exits once a minute", old)

    def test_the_new_parts_tied_to_the_code(self):
        """What README names in the parts of 3.3.7.11 — the same words as the panel, the feed and the commands."""
        for lang, part, q in (("ru", self.ru, "«%s»"), ("en", self.en, '"%s"')):
            core, sp = _js(lang), _js(lang, "pages/speed-i18n-%s.js")
            dg, eg = _js(lang, "pages/diag-i18n-%s.js"), _js(lang, "pages/exits-groups-i18n-%s.js")
            ep, gw = _cat(lang, "ep"), _cat(lang, "gw_live")
            # «Вернуть как было» in «Настройках»
            self.assertIn(q % core["bk.undo"], part)
            where = {"ru": ("Настройки", "в «Настройках»"), "en": ("Settings", 'in "Settings"')}[lang]
            self.assertEqual(core["nav.settings"], where[0])
            self.assertIn(where[1], part)
            # a move into a Russian group, «Неверный id выхода»; the Russian exits in gw exits
            self.assertIn(q % ep["ep.err.exit_id"].split(":")[0], part)
            self.assertIn(q % {"ru": "Российские выходы", "en": "Russian exits"}[lang], part)   # имя до 29.10 (внутренние)
            self.assertTrue(_cat(lang, "gw_cli")["gw_cli.exits.ru_head"].startswith({"ru": "Внутренние выходы (", "en": "Domestic exits ("}[lang]))
            self.assertIn(q % core["rs.title"] + " → " + q % core["rs.pickBtn"], part)
            # the feed of the exit levels
            back = re.split(r" [«“]", gw["gw_live.event.exit_failover_back_none"])[0]       # «Снова основной уровень»
            self.assertIn(q % back, part)
            self.assertIn(q % (re.split(r" [«“]", gw["gw_live.event.exit_failover"])[0] + " …"), part)
            # «График активности»: 7 дней, Сутки, «Графики» of the exit; hours and minutes
            for k in ("pp.act.graphT", "pp.act.r7d", "pp.act.r1d", "nav.exits"):
                self.assertIn(q % core[k], part)
            self.assertIn(q % eg["ep.charts"], part)
            self.assertIn("423 %s 5 %s" % (core["fmt.h"], core["fmt.m"]), part)
            # «Поделиться» and the test from the device
            self.assertIn(q % dg["dg.share"], part)
            self.assertIn(q % sp["panel.speed.title"], part)
            self.assertIn(q % sp["panel.speed.checkBusy"].split(". ")[0], part)
        self.assertIn("«до 0,4 МБ»", self.ru)
        self.assertIn('"up to 0.4 MB"', self.en)
        self.assertTrue(_js("ru", "pages/diag-i18n-%s.js")["dg.sh.info"].startswith("до {mb} МБ"))
        self.assertTrue(_js("en", "pages/diag-i18n-%s.js")["dg.sh.info"].startswith("up to {mb} MB"))
        self.assertRegex(text("lib/kaskad/panel/static/pages/diag-share.js"), r"const EST_MB = \{ sq: 0\.4, post: 0\.4 \};")
        self.assertRegex(text("lib/kaskad/bot/monitor.py"), r"(?m)^BLIP_S = 60\b")        # a blip shorter than a minute
        self.assertIn('add_parser("undo"', text("lib/kaskad/cli_backup.py"))
        for part in (self.ru, self.en):
            for cmd in ("`kaskadctl backup undo`", "`kaskadctl gw exits`", "`kaskadctl rules ru-via`"):
                self.assertIn(cmd, part)

    def test_russian_and_english(self):
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))
        for part in (self.ru, self.en):
            self.assertNotRegex(part, r"\.ru\b")
            self.assertIsNone(re.search(r"\b(REV|SEC|DQA|D-\d+|Н-\d|З-\d|М-\d|С-\d|П-\d|Р-\d|А\d|Q\d|AC\d|T-\d+)\b",
                                        part))
        self.assertIsNone(re.search(r"(?i)\bnodes?\b", self.en))
        self.assertNotRegex(self.ru, r"(?i)человек|\bлюд")
        self.assertNotRegex(self.en, r"(?i)\b(person|people)\b")


if __name__ == "__main__":
    unittest.main()
