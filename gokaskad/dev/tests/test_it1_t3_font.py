"""Круг 1, Т3 №17 — «Размер текста» (Д2 Б r3, Ш1 / Ш2 / П5): три шага 93,75 / 100 / 112,5 % — html { font-size },
весь текст панели в rem; выбор — только в этом браузере (kaskad-font), на узел не уходит. Шапка компьютера — «A A A»
(радиогруппа), телефон — «Aa» и лист с тремя «A»; «Вид» — тот же выбор. В Chrome — tests/test_ui4_people.py::test_font_step,
вёрстка на крайних шагах — tools/k3_layout_check.mjs с K3_FONT=s|l."""
import re
import unittest

from tests.test_332f_give import read

# стили страницы панели (index.html) и окна «График связей» (graph.css грузится по требованию)
PANEL_CSS = ("tokens.css", "components.css", "styles.css", "give.css", "ux.css", "charts.css", "mobile.css", "find.css",
             "xt.css", "people.css", "check.css", "speed.css", "ovb.css", "grid.css", "graph.css", "pset.css", "regroup.css", "addresses.css", "stream.css")
DECL = re.compile(r'(?<![\w-])(font|font-size|line-height|--fs(?:-[a-z0-9]+)?)\s*:([^;{}]*)')
# нижний порог в px при размере в rem — max(<rem>, <px>): текст идёт за шагом, px держит только низ. Так сделаны поля
# телефона (max(1rem, 16px), iOS) и подписи осей графиков (charts.css max(.6875rem, 11px) — не мельче 11 px на «мельче»,
# 375f31e3). Голые px, min(…px…), max(<px>, …) — нарушение
FLOOR = re.compile(r'max\(\s*\d*\.?\d+rem\s*,\s*\d+(?:\.\d+)?px\s*\)')
PX = re.compile(r'(?<![\w.-])\d+(?:\.\d+)?px\b')


def px_in(value):
    return bool(PX.search(FLOOR.sub("", value)))


class FontStepTest(unittest.TestCase):
    def test_text_in_rem(self):
        # px в размерах текста нет; исключение — нижний порог max(<rem>, <px>) (FLOOR)
        bad = []
        for f in PANEL_CSS:
            for m in DECL.finditer(read(f)):
                if px_in(m.group(2)):
                    bad.append("%s: %s:%s" % (f, m.group(1), m.group(2).strip()))
        self.assertEqual(bad, [])
        self.assertIn("max(1rem, 16px)", read("components.css"))
        self.assertIn("font: 400 max(.6875rem, 11px)/.875rem var(--mono)", read("grid.css"))   # подписи осей — сетка графиков (было charts.css)

    def test_px_rule(self):
        # правило ловит px в строке и размере (xt.css is-fit line-height: 20px, 2cd301db) и не прощает порог «наоборот»
        for v in (" 20px", " 500 12px/1 var(--font)", " .75rem/20px var(--font)", " min(1rem, 16px)", " max(16px, 1rem)",
                  " max(.75rem, 11px) 12px", " calc(1rem + 2px)"):
            self.assertTrue(px_in(v), v)
        for v in (" 1.25rem", " max(1rem, 16px)", " max(.6875rem, 11px)", " 500 var(--fs-xs)/1 var(--font)", " 1"):
            self.assertFalse(px_in(v), v)

    def test_steps_and_storage(self):
        tok, boot, app = read("tokens.css"), read("boot.js"), read("app.js")
        self.assertIn('html[data-hero][data-fs="s"] { font-size: 93.75%; }', tok)
        self.assertIn('html[data-hero][data-fs="l"] { font-size: 112.5%; }', tok)
        self.assertNotIn('[data-fs="s"] { --fs:', tok)
        self.assertIn('localStorage.getItem("kaskad-font")', boot)
        self.assertIn('const FONT_KEY = "kaskad-font", FONT_STEPS = ["s", "m", "l"];', app)
        self.assertIn("root.dataset.fs = fontStep();", app)
        # выбор не уходит на узел: setFont не зовёт setLook / scheduleLookSave
        body = app[app.index("function setFont(v)"):app.index("function fontSwitch(phone)")]
        self.assertNotIn("setLook", body)
        self.assertNotIn("scheduleLookSave", body)
        self.assertIn("lsSet(FONT_KEY, v);", body)
        self.assertIn("fontMem = v;", body)                                    # приватное окно: шаг держится до перезагрузки

    def test_switches(self):
        app, st, mob = read("app.js"), read("styles.css"), read("mobile.css")
        self.assertIn("refreshBtn, fontSwitch(false),", app)                                   # шапка: … обновить · A A A · тема · Вид
        self.assertIn('role: "radiogroup", "aria-label": t("lk.fs")', app)
        self.assertIn('class: "ga-btn ga-btn--tertiary kk-mhead__fs"', app)                  # телефон: «Aa»
        self.assertIn('pick: () => setFont(v)', app)                                         # «Вид» — тот же выбор
        self.assertIn('html[data-fs="l"] .kk-hfind__k { display: none; }', st)
        self.assertIn(".kb2-fz > button[aria-checked=\"true\"]", st)
        self.assertIn(".kk-fsheet__seg > button[aria-checked=\"true\"]", mob)

    def test_texts(self):
        for name, want in (("i18n-ru.js", ("Поиск по панели", "Текст мельче", "Текст обычный", "Текст крупнее")),
                           ("i18n-en.js", ("Search the panel", "Smaller text", "Normal text", "Larger text"))):
            src = read(name)
            self.assertIn('"find.ph": "%s",' % want[0], src)
            for k, v in zip(("lk.fzS", "lk.fzM", "lk.fzL"), want[1:]):
                self.assertIn('"%s": "%s",' % (k, v), src)
            self.assertIn('"lk.fzSub":', src)


if __name__ == "__main__":
    unittest.main()
