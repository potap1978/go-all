"""Kaskad 3.3.6, BACKLOG-3.3.6 п. 4: «Вид» панели выровнен с goAmnezia Pro.

One list of axes in one order (LOOK_AXES) in the drawer, in the bottom sheet of a phone and in the card of «Настройки»:
the axes shared with goAmnezia Pro in its order, the own ones of Kaskad next to them by meaning, the language of the
server last; the menu is not on a phone. The accent «Каскад» (auto — mint in the dark theme, violet in the light) is a
circle of two halves: in the dark theme it no longer repeats «Мята». The labels are the ones of goAmnezia Pro where the
meaning is the same. The model of «Вид» (the key, the version, the fields, the migration) does not change.

In a real Chrome (tools/k3_look_check.mjs, ?mock=1): 390 (phone), 768 and 1440, dark and light, RU and EN."""

import functools
import http.server
import json
import os
import re
import subprocess
import threading
import unittest

from tests.test_332f_give import HAS_NODE, read
from tests.test_333q_layout import ChromeServer, _Static, chrome_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHECK = os.path.join(ROOT, "tools", "k3_look_check.mjs")
AXES = ["presets", "accent", "theme", "tint", "cards", "btn", "radius", "nav", "fs", "density", "vibrant", "motion", "lang"]


def words(name):
    src = read(name)
    return dict(re.findall(r'\n  "([a-zA-Z.]+)": "([^"\n]*)"', src))


class RuleTest(unittest.TestCase):
    def test_one_list_of_axes(self):
        js = read("app.js")
        self.assertIn("const LOOK_AXES = %s;" % json.dumps(AXES), js)
        self.assertIn("return LOOK_AXES.map((id) => lookAxis(id, wide)).filter(Boolean);", js)
        self.assertNotIn("function lookSegs(", js)                               # no second list
        # the shared axes go in the order of goAmnezia Pro
        shared = [a for a in AXES if a in ("presets", "accent", "theme", "tint", "cards", "btn", "nav", "fs", "density")]
        self.assertEqual(shared, ["presets", "accent", "theme", "tint", "cards", "btn", "nav", "fs", "density"])
        self.assertIn('nav: () => (isPhone() ? null : lookSeg(t("lk.menu"), "nav"', js)
        self.assertIn('"data-look": key', js)
        self.assertIn('"data-look": "lang"', js)

    def test_the_drawer_the_sheet_and_the_settings_draw_the_same(self):
        js = read("app.js")
        self.assertEqual(js.count("lookBody(false)"), 1)                          # the drawer (on a phone — from below)
        self.assertEqual(js.count("lookBody(true)"), 1)                           # «Настройки → Вид»
        self.assertIn('lookRow.dataset.lookOpen = "";', js)                       # «Ещё» on a phone opens the same drawer

    def test_kaskad_accent_is_two_halves(self):
        """REV-336-look З-1: no gradient in the circles (go-ds: a gradient only in the sign); the halves are checked by
        the look in ChromeTest.test_kaskad_halves_by_the_look."""
        js, css = read("app.js"), read("styles.css")
        body = js[js.index("function lookSwatches()"):js.index("function lookHue()")]
        self.assertNotIn("gradient", body)
        swatch_css = "\n".join(x for x in css.splitlines() if "gx-swatch" in x)
        self.assertNotIn("gradient", swatch_css)
        self.assertIn('"data-accent": k', body)

    def test_the_model_stays(self):
        js = read("app.js")
        self.assertIn('const LOOK_KEY = "kaskad-appearance-v1";', js)
        self.assertIn("const LOOK_V = 3;", js)
        self.assertIn('const LOOK_DEFAULTS = { theme: "dark", accent: "auto", hue: 200, tint: "neutral", density: "detailed", radius: 8, '
                      'btn: "pill", cards: "shadow", nav: "full", fs: "m", vibrant: false, motion: true };', js)

    def test_the_words_of_goamnezia(self):
        ru, en = words("i18n-ru.js"), words("i18n-en.js")
        want_ru = {"lk.title": "Внешний вид", "sh.look": "Внешний вид", "sh.view": "Вид", "lk.system": "Система", "lk.shadow": "Тень",
                   "lk.border": "Рамка", "lk.pills": "Капсулы", "lk.fsS": "Мельче", "lk.fsM": "Обычный", "lk.fsL": "Крупнее",
                   "lk.hue": "Свой оттенок", "lk.theme": "Тема", "lk.tint": "Оттенок фона", "lk.density": "Подробность",
                   "lk.vibrant": "Яркие статусы", "lk.motion": "Анимации", "lk.radius": "Скругление",
                   "lk.accAuto": "Каскад: мятный в тёмной теме, фиолетовый в светлой"}             # REV-336-look З-2
        want_en = {"lk.title": "Appearance", "lk.system": "System", "lk.shadow": "Shadow", "lk.border": "Border", "lk.pills": "Pills",
                   "lk.fsS": "Smaller", "lk.fsM": "Normal", "lk.fsL": "Larger", "lk.density": "Detail", "lk.hue": "Custom hue",
                   "lk.vibrant": "Vivid statuses", "lk.motion": "Animations", "lk.radius": "Corner radius",
                   "lk.accAuto": "Kaskad: mint in dark theme, violet in light"}
        self.assertEqual({k: ru.get(k) for k in want_ru}, want_ru)
        self.assertEqual({k: en.get(k) for k in want_en}, want_en)

    def test_the_docs_use_the_same_words(self):
        """REV-336-look З-3: the design system names the cards as the panel does; the glossary knows «Внешний вид»."""
        with open(os.path.join(ROOT, "docs", "k3", "DESIGN-SYSTEM.md"), encoding="utf-8") as f:
            ds = f.read()
        self.assertNotIn("«С тенью»", ds)
        self.assertNotIn("«С рамкой»", ds)
        self.assertIn("«Тень»", ds)
        self.assertIn("«Рамка»", ds)
        with open(os.path.join(ROOT, "docs", "k3", "GLOSSARY-en.md"), encoding="utf-8") as f:
            self.assertIn("| «Внешний вид» | Appearance |", f.read())


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: «Вид» в браузере не проверен (K3_CHROME=…)")
class ChromeTest(unittest.TestCase):
    rows = None

    @classmethod
    def setUpClass(cls):
        srv = ChromeServer(("127.0.0.1", 0), functools.partial(_Static, directory=STATIC))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            p = subprocess.run(["node", CHECK, "http://127.0.0.1:%d" % srv.server_address[1], chrome_path()],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=320)
        finally:
            srv.shutdown()
            srv.server_close()
        if p.returncode != 0:
            raise AssertionError("k3_look_check: %s" % p.stderr.decode("utf-8", "replace")[-800:])
        cls.rows = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])

    def test_every_place_opens(self):
        # UI-6: лист «Внешний вид» — только на телефоне; на компьютере «Вид» в шапке ведёт в «Настройки › Вид» (viewbtn)
        self.assertEqual(len(self.rows), 16)
        self.assertEqual([r for r in self.rows if not r["ok"]], [])
        self.assertEqual([r for r in self.rows if r["where"] == "settings" and r["w"] >= 700 and r["viewbtn"] is not True], [])

    def test_same_axes_everywhere(self):
        for r in self.rows:
            want = [a for a in AXES if not (r["w"] < 700 and a == "nav")]
            self.assertEqual(r["axes"], want, (r["where"], r["w"], r["theme"], r["lang"]))

    def test_theme_choices(self):
        for r in self.rows:
            self.assertEqual(r["themes"], ["Тёмная", "Светлая", "Система"] if r["lang"] == "ru" else ["Dark", "Light", "System"])

    def test_no_double_circle(self):
        for r in self.rows:
            sw = r["swatches"]
            self.assertEqual(len(sw), 9)
            for k, v in sw.items():                                             # no gradient, neither on the circle nor on ::before
                self.assertEqual(v[1:], ["none", "none"], (k, r["where"], r["w"], r["theme"]))
            self.assertNotEqual(sw["auto"], sw["mint"], (r["where"], r["w"], r["theme"]))
            self.assertEqual(len({json.dumps(v) for v in sw.values()}), 9, (r["where"], r["w"], r["theme"]))

    def test_kaskad_halves_by_the_look(self):
        """REV-336-look З-1, П-2: by the look on the screen — the upper left half is the accent of the dark theme
        (mint), the lower right — of the light one (violet); the check mark sits on the surface."""
        def near(a, b):
            return max(abs(x - y) for x, y in zip(a, b)) <= 6
        for r in self.rows:
            h, at = r["halves"], (r["where"], r["w"], r["theme"], r["lang"])
            self.assertIsNotNone(h, at)
            self.assertTrue(near(h["tl"], h["a"]), (at, h))
            self.assertTrue(near(h["br"], h["b"]), (at, h))
            self.assertFalse(near(h["a"], h["b"]), (at, h))
            self.assertEqual(h["chip"], h["surface"], at)

    def test_the_ninth_circle_is_not_alone(self):
        """REV-336-look П-1: 9 circles — one row when they fit, otherwise 5 + 4."""
        for r in self.rows:
            self.assertIn(r["rows"], ([9], [5, 4]), (r["where"], r["w"], r["theme"], r["lang"]))
        self.assertIn([5, 4], [r["rows"] for r in self.rows if r["w"] < 700])

    def test_arrows_walk_the_accents(self):
        """REV-336-look З-4: a radiogroup — one Tab stop, the arrows go to the next circle (by a ring) and keep the focus."""
        for r in self.rows:
            k, at = r["keys"], (r["where"], r["w"], r["theme"], r["lang"])
            self.assertEqual(k, {"tabs": 1, "start": "auto", "right": ["ga", "ga"], "left": ["auto", "auto"]}, at)

    def test_phone_targets(self):
        phone = [r for r in self.rows if r["w"] < 700]
        self.assertEqual(len(phone), 8)
        for r in phone:
            self.assertGreater(r["checked"], 25, (r["where"], r["theme"], r["lang"]))
            self.assertEqual(r["small"], [], (r["where"], r["theme"], r["lang"]))


if __name__ == "__main__":
    unittest.main()
