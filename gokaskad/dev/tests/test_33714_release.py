"""Kaskad 3.3.7.14 — the small fixes after 3.3.7.13: one version (3.3.7.14 newer than 3.3.7.13 for the update), and
«Что нового» of 3.3.7.14 for the admin, tied to what the code says and does (the fixes themselves —
tests/test_33714_fixes.py)."""
import json
import re
import subprocess
import unittest

import kaskad
from tests.test_3371_release import entry, text


def _flat(s):
    return re.sub(r"\s+", " ", s)


def _bullets(raw):
    return [_flat(b) for b in re.split(r"(?m)^- ", raw)[1:]]


def _sh(lang, key):
    m = re.search(r"(?m)^M_%s_%s='([^']*)'$" % (lang, key), text("lib/kaskad/i18n/sh/gwstat.%s.sh" % lang))
    return m.group(1)


def _cat(lang, name):
    return json.loads(text("lib/kaskad/i18n/catalog/%s/%s.json" % (lang, name)))


def _js(lang, name="i18n-%s.js"):
    return dict(re.findall(r'(?m)^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', text("lib/kaskad/panel/static/" + name % lang)))


class Release33714Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.14")
        self.ru_raw, sep, self.en_raw = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.14")
        self.ru, self.en = _flat(self.ru_raw), _flat(self.en_raw)

    def test_one_version(self):
        # 3.3.7.15 is above it now (tests/test_33715_release.py): the version is its, the heading names 3.3.7.14 too
        self.assertTrue(kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.12, 3\.3\.7\.13, 3\.3\.7\.14[ ,]")
        self.assertLess(self.readme.index("**3.3.7.15 — "), self.readme.index("**3.3.7.14 — "))
        self.assertLess(self.readme.index("**3.3.7.14 — "), self.readme.index("**3.3.7.13 — "))

    def test_the_update_sees_it_newer(self):
        fn = re.search(r"(?m)^k3_ver_lt\(\) \{.*\}$", text("install.sh")).group(0)
        for a, b, lt in (("3.3.7.13", "3.3.7.14", 0), ("3.3.7.14", "3.3.7.13", 1), ("3.3.7.14", "3.3.7.14", 1),
                         ("3.3.7.9", "3.3.7.14", 0), ("3.3.7.14", "3.3.8", 0), ("3.3.3", "3.3.7.14", 0)):
            rc = subprocess.run(["bash", "-c", fn + '\nk3_ver_lt "$1" "$2"', "_", a, b], capture_output=True).returncode
            self.assertEqual(rc, lt, (a, b))

    def test_seven_items_both_languages(self):
        self.assertEqual(len(_bullets(self.ru_raw)), 7)
        self.assertEqual(len(_bullets(self.en_raw)), 7)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en_raw.split("**", 2)[2]), "Cyrillic in the English part")

    def test_the_grid_on_the_charts(self):
        """REV-33714 З-1: the grid on the charts and the view of 3.3.7.14 (tour, «All exits», 90 days) are in «Что
        нового», in the words of the panel."""
        self.assertIn("сетк", self.ru_raw.split("\n", 1)[0])                     # и в заголовке раздела
        self.assertIn("grid", self.en_raw.split("\n", 1)[0])
        ru = [b for b in _bullets(self.ru_raw) if "сетк" in b]
        en = [b for b in _bullets(self.en_raw) if "grid" in b]
        self.assertEqual((len(ru), len(en)), (1, 1))
        for lang, part, q, mb in (("ru", ru[0], "«%s»", "МБ"), ("en", en[0], '"%s"', "MB")):
            core, eg = _js(lang), _js(lang, "pages/exits-groups-i18n-%s.js")
            ovb = _js(lang, "pages/ovb-i18n-%s.js")
            for w in (core["grid.perH"].replace("{u}", mb), core["grid.ms"], core["grid.now"], core["pp.act.graph"],
                      eg["ep.charts"], ovb["ovb.trT"], eg["ep.r90d"], core["tour.cap"], core["n2.exList"],
                      core["lk.fsL"], core["ex.add"]):
                self.assertIn(q % w, part, lang)
            for w in ("360×640", "375×667", "680 px"):
                self.assertIn(w, part, lang)
        self.assertIn("«1 окт.»", ru[0])
        self.assertIn('"Oct 1"', en[0])

    def test_the_words_are_the_code_s(self):
        """The quoted words are what the menu, the core and the panel say."""
        for lang, part, q in (("ru", self.ru, "«%s»"), ("en", self.en, '"%s"')):
            cli = _cat(lang, "gw_cli")
            self.assertIn(q % cli["gw_cli.groups.place"].format(n=1), part, lang)
            self.assertIn(q % cli["gw_cli.groups.row"].format(n=1), part, lang)
            self.assertIn(q % cli["gw_cli.groups.row"].format(n=2), part, lang)
            ex = _cat(lang, "gw_exits")["gw_exits.problem.vless_self_ep"]
            self.assertIn(ex.split(" — ")[0], part, lang)                            # «адрес сервера … этот сервер»
            self.assertIn(_sh(lang, "GWS_IN_MANY").split(" — ")[0], part, lang)
            self.assertIn(_sh(lang, "GWS_HOST_IN_ALL").split(" — ")[0], part, lang)
            bad = _cat(lang, "gw_addresses")["gw_addresses.err.not_public"].format(value="abc")
            self.assertIn(bad.split(".")[0], part, lang)
            nh = _cat(lang, "gw_mip")["gw_mip.err.not_here"].format(ip="…", known="", back="").split(" — ")[0]
            self.assertIn(nh, part, lang)
            no_item = re.search(r"(?m)^M_%s_C_NO_ITEM='([^':]*)" % lang,
                                text("lib/kaskad/i18n/sh/core.%s.sh" % lang)).group(1)
            self.assertIn(q % no_item, part, lang)
            core = _js(lang)
            self.assertIn(q % core["conn.live"], part, lang)
            eg = _js(lang, "pages/exits-groups-i18n-%s.js")
            self.assertIn(q % eg["ep.tiers"], part, lang)
        for w in ("ip route flush", "kaskadctl gw groups", "kaskadctl gw addresses in", "kaskadctl gw host", ".kbk",
                  "1100–1199", "NaN", "Infinity", "±10^18", "⌘K"):
            self.assertIn(w, self.ru)
            self.assertIn(w, self.en)


if __name__ == "__main__":
    unittest.main()
