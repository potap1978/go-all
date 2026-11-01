"""Круг 1, шаг Т3, «Обзор Б r3» (Claude Design it1 Д2 r3: HANDOFF «## it1 · Сводка и проверка сервера» → «Б · r3»;
k3/stand/logs/D-it1-d2.md «## r3» → «Обзор Б»).

* E (кадры Б1, Б2, Т1, Т2): «Сервер сейчас» — «паутинка» слева, справа одной колонкой сверху вниз вердикт и балл,
  замечания с действием, полоса 30 дней (было: полоса строкой во всю ширину под ними — пустота под вердиктом). Без
  замечаний колонка — по центру «паутинки». Телефон — прежняя раскладка: 30 дней во всю ширину под «паутинкой».
* F (кадры Б3, Т3): у звена со значением и малой историей вместо «— —» в «Обычно» и «От обычного» — одна ячейка: точки
  дней истории и «"обычно" появится, когда наберётся история» (С-4: без номера дня), «k из n» подсказкой; отметки «мало истории» у имени нет. N и число точек —
  правило сервера summary.rel_need (= kaskad.score.REL_N_MIN: «обычно» и rel — от 3 дней базы), закрашено — usual_n
  звена; не 8 из макета. ПК — на месте двух колонок, телефон и узкая карточка — третьей строкой под линией.

Строение — в node на поддельном DOM (настоящие pages/ovb.js и pages/diag.js), раскладка — в настоящем Chrome
(tools/k3_ovb_r3_check.mjs на заглушке панели; нет node или Chrome — пропуск со словами)."""
import functools
import json
import os
import re
import subprocess
import threading
import unittest

from tests.panel.test_ovb_live import run as ovb_run
from tests.test_333q_layout import ChromeServer, _Static, chrome_path
from tests.test_panel_diag_now import go as diag_go
from tests.test_panel_diag_screen import HAS_NODE

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")


def read_en():
    with open(os.path.join(STATIC, "pages", "diag-i18n-en.js"), encoding="utf-8") as f:
        return dict(re.findall(r'"(dg\.\w+)": "([^"]+)"', f.read()))
CHECK = os.path.join(ROOT, "tools", "k3_ovb_r3_check.mjs")

# 30 дней полосы /api/host/score (цвет дня) — для части Сводки
DAYS = "Array.from({ length: 30 }, (_x, i) => ({ day: '2026-10-' + String(i + 1).padStart(2, '0'), color: i === 5 ? 'yellow' : 'green' }))"


@unittest.skipUnless(HAS_NODE, "нет node: Сводку не отрисовать")
class HeroColumnTest(unittest.TestCase):
    """E: строение «Сервер сейчас» не меняется (места задаёт ovb.css строками сетки): «паутинка», вердикт и балл,
    замечания, 30 дней — прямые дети карточки в этом порядке, без замечаний и на телефоне — тоже."""

    def test_order_of_the_card(self):
        out = ovb_run(r"""
const o = {};
API_DATA.score = { days: %s };
const kids = (n) => n.children.map((c) => (c.getAttribute("class") || "").split(" ")[0]);
ISS = [{ key: "w:x", tone: "warning", title: "Выход «Осло» не работает", sub: "нет ответа", n: 1, go: () => null, link: "Открыть" }];
HV = { view: { level: "ok" } };
let box = await summary();
const hero = () => box.querySelector(".kb-hero");
o.hero = kids(hero());
o.days = hero().querySelector(".kb-30__row").children.length;
ISS = []; scheduleRefresh();
o.none = kids(hero());
PHONE = true; scheduleRefresh();
o.phone = [kids(hero()), hero().querySelector(".kb-30").tagName];
console.log(JSON.stringify(o));
""" % DAYS)
        self.assertEqual(out["hero"], ["kb-hero__star", "kb-hero__v", "kb-iss", "kb-30"])
        self.assertEqual(out["days"], 30)
        self.assertEqual(out["none"], ["kb-hero__star", "kb-hero__v", "kb-30"])
        self.assertEqual(out["phone"], [["kb-hero__star", "kb-hero__v", "kb-30"], "BUTTON"])   # телефон: полоса — кнопка в «Историю»

    def test_css_rows_on_the_computer_and_the_old_layout_on_the_phone(self):
        with open(os.path.join(STATIC, "ovb.css"), encoding="utf-8") as f:
            css = f.read()
        # ПК: строки 1fr · вердикт · замечания · 30 дней · 1fr, «паутинка» на всех, 30 дней — во второй колонке
        self.assertIn(".kb-hero { grid-template-rows: 1fr auto auto auto 1fr; }\n.kb .kb-hero { row-gap: 0; }", css)
        self.assertIn(".kb-hero > .kb-hero__star { grid-column: 1; grid-row: 1 / -1; }", css)
        self.assertIn(".kb-hero > .kb-30 { grid-column: 2; grid-row: 4; margin-top: 12px; }", css)
        # телефон и узкая карточка — строки сбрасываются, замечания и 30 дней во всю ширину (прежняя раскладка)
        self.assertIn('[data-device="phone"] .kb .kb-hero { grid-template-rows: none; row-gap: 10px; }', css)
        self.assertIn('[data-device="phone"] .kb-hero > .kb-iss, [data-device="phone"] .kb-hero > .kb-30 { grid-column: 1 / -1; }', css)
        narrow = css.split("@container kbhero (max-width: 519.98px) {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn(".kb-hero { grid-template-rows: none; }", narrow)
        self.assertIn(".kb-hero > .kb-iss, .kb-hero > .kb-30 { grid-column: 1 / -1; }", narrow)


@unittest.skipUnless(HAS_NODE, "нет node: «Звенья» не отрисовать")
class FewHistoryCellTest(unittest.TestCase):
    """F: звено со значением, «обычно» не набралось — одна ячейка вместо «— —»; N и точки — из summary.rel_need."""

    SCRIPT = r"""
const cellsOf = (r) => r.children.map((c) => [(c.getAttribute("class") || ""), c.getAttribute("aria-colspan"), sp(c.textContent)]);
const fewOf = (r) => { const f = r.querySelector(".kb-lk__few"); if (!f) return null; const p = f.querySelector(".kb-lk__pips");
  return { tip: f.getAttribute("title"), aria: f.getAttribute("aria-label"), pips: p ? p.children.length : 0,
    on: p ? p.children.filter((i) => (i.getAttribute("class") || "") === "is-on").length : 0, hidden: p ? p.getAttribute("aria-hidden") : null }; };
SCORE = scoreOf({ young: true });
NEED
await mount([], { tab: "now" });
const rs = SEC.querySelectorAll(".kb-lk__r");
console.log(JSON.stringify({ rows: lrows(), tips: ltips(), cells: rs.map(cellsOf), few: rs.map(fewOf), cls: rs.map((r) => r.getAttribute("class")) }));
"""

    def go(self, need=""):
        return diag_go(self.SCRIPT.replace("NEED", need))

    def test_one_cell_instead_of_two_dashes(self):
        r = self.go()
        # было: «Отдачамало истории||120Мбит/с|—|—» — отметка у имени и прочерки в «Обычно» и «От обычного»
        self.assertEqual(r["rows"][1], "Отдача||120Мбит/с|«обычно» появится, когда наберётся история")
        self.assertEqual(r["cells"][1], [["kb-lk__n", None, "Отдача"], ["kb-lk__c", None, ""], ["kb-lk__v", None, "120Мбит/с"],
                                         ["kb-lk__few", "2", "«обычно» появится, когда наберётся история"]])
        self.assertEqual(r["cls"][1], "kb-lk__r is-few")
        # три точки (summary.rel_need = 3), закрашены две (usual_n = 2); полный текст — подсказкой и для чтения с экрана
        self.assertEqual(r["few"][1], {"tip": "Дней истории для сравнения: 2 из 3; последние 3 дня сравниваются с ней и в неё не входят.",
                                       "aria": "Дней истории для сравнения: 2 из 3; последние 3 дня сравниваются с ней и в неё не входят.",
                                       "pips": 3, "on": 2, "hidden": "true"})
        self.assertEqual(r["tips"][1], "Сравнивать пока не с чем: история 2 из 3 дней")      # подсказка строки — прежняя
        for row in (r["rows"][0], r["rows"][1], r["rows"][5], r["rows"][6]):
            self.assertNotIn("—|", row)
            self.assertNotIn("мало истории", row)
        # у кого «обычно» есть — как было: число, «обычно», «от обычного»; ноль в знаменателе — «—» (это не «мало истории»)
        self.assertEqual(r["rows"][2], "Отклик||38мс|36мс|95")
        self.assertEqual(r["rows"][4], "Потери||0,06%|0%|—")
        self.assertEqual([f is not None for f in r["few"]], [True, True, False, False, False, True, True])

    def test_n_and_the_dots_follow_the_rule_of_the_server(self):
        """N — summary.rel_need сервера (не 8 из макета): правило сменится — сменятся и подпись, и число точек."""
        r = self.go("SCORE.summary.rel_need = 5;")
        self.assertEqual(r["rows"][1], "Отдача||120Мбит/с|«обычно» появится, когда наберётся история")   # номер дня не обещаем (С-4)
        self.assertEqual((r["few"][1]["pips"], r["few"][1]["on"]), (5, 2))
        self.assertTrue(r["few"][1]["tip"].startswith("Дней истории для сравнения: 2 из 5;"), r["few"][1]["tip"])
        self.assertEqual(r["rows"][2], "Отклик||38мс|36мс|95")         # usual_n 12 ≥ 5 — как было
        # точек больше 10 не рисуем — только подпись (строка не растягивается)
        r = self.go("SCORE.summary.rel_need = 30;")
        self.assertEqual(r["rows"][1], "Отдача||120Мбит/с|«обычно» появится, когда наберётся история")
        self.assertEqual(r["few"][1]["pips"], 0)

    def test_english(self):
        en = read_en()
        r = self.go('DICT["dg.fewHist"] = %s; DICT["dg.fewHistTip"] = %s;' % (json.dumps(en["dg.fewHist"]), json.dumps(en["dg.fewHistTip"])))
        self.assertEqual(r["rows"][1], "Отдача||120Мбит/с|“usually” appears once there’s enough history")
        self.assertEqual(r["few"][1]["tip"], "Days of history for comparison: 2 of 3; the last 3 days are compared with it and aren’t part of it.")

    def test_no_day_number_promised(self):
        """REV-it1-b С-4: «обычно» появляется не на n-й день (история — дни до окна трёх последних значений), поэтому
        номера дня в подписи нет ни на одном языке; в подсказке — «k из n» и почему последние 3 дня не в счёт."""
        for lang in ("ru", "en"):
            with open(os.path.join(STATIC, "pages", "diag-i18n-%s.js" % lang), encoding="utf-8") as f:
                words = dict(re.findall(r'"(dg\.fewHist\w*)": "([^"]+)"', f.read()))
            self.assertNotIn("{n}", words["dg.fewHist"], lang)
            self.assertNotRegex(words["dg.fewHist"] + words["dg.fewHistTip"], r"-го|day \{n\}|after day", lang)
            self.assertIn("{k}", words["dg.fewHistTip"], lang)
            self.assertIn("3", words["dg.fewHistTip"], lang)

    def test_no_value_keeps_the_reason_cell(self):
        """Звено без значения (Л3 №8) — прежняя ячейка причины на месте трёх колонок, точек нет."""
        r = self.go("const ul = SCORE.links.find((l) => l.id === 'up'); Object.assign(ul, { fresh_raw: null, rel: null, why: 'no_upload_ru' }); "
                    "Object.assign(ul.parts[0], { fresh_raw: null, rel: null });")
        self.assertEqual(r["cells"][1][2], ["kb-lk__v kb-lk__why", "3", "в России не меряется"])
        self.assertIsNone(r["few"][1])

    def test_the_line_label_says_from_which_day(self):
        r = diag_go(r"""
SCORE = scoreOf({ young: true });
K0.isPhone = () => false;
await mount([], { tab: "now" });
console.log(JSON.stringify(SEC.querySelectorAll(".kb-lk__r").map((x) => x.querySelector(".gx-cl").getAttribute("aria-label"))));
""")
        self.assertEqual(r[1], "Отдача: 120 Мбит/с, «обычно» появится, когда наберётся история")       # было «…, обычно —»
        self.assertEqual(r[2], "Отклик: 38 мс, обычно 36 мс")


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: раскладка «Обзора» r3 в браузере не проверена (K3_CHROME=…)")
class ChromeLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        srv = ChromeServer(("127.0.0.1", 0), functools.partial(_Static, directory=STATIC))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = subprocess.run(["node", CHECK, "http://127.0.0.1:%d" % srv.server_address[1], chrome_path()],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        finally:
            srv.shutdown()
            srv.server_close()
        if r.returncode:
            raise AssertionError(r.stderr.decode("utf-8", "replace")[-3000:])
        cls.r = json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])

    def test_e_strip_beside_the_radar_on_the_computer(self):
        h = self.r["hero"]
        self.assertEqual(h["dev"], "desktop")
        # было: полоса от левого края карточки (под «паутинкой») во всю ширину — d30.l == inner.l
        self.assertGreaterEqual(h["d30"]["l"], h["star"]["r"], h)
        self.assertLessEqual(abs(h["d30"]["r"] - h["inner"]["r"]), 1, h)
        self.assertEqual((h["v"]["l"], h["iss"]["l"]), (h["d30"]["l"], h["d30"]["l"]))
        self.assertTrue(h["v"]["b"] <= h["iss"]["t"] and h["iss"]["b"] <= h["d30"]["t"], h)      # сверху вниз
        self.assertLessEqual(abs((h["col"]["t"] + h["col"]["b"]) / 2 - (h["star"]["t"] + h["star"]["b"]) / 2), 2, h)

    def test_e_no_gap_under_the_verdict_without_remarks(self):
        h = self.r["heroNoIss"]
        self.assertGreaterEqual(h["d30"]["l"], h["star"]["r"], h)
        self.assertTrue(h["star"]["t"] <= h["col"]["t"] and h["col"]["b"] <= h["star"]["b"], h)   # колонка не ниже «паутинки»
        self.assertLessEqual(abs((h["col"]["t"] + h["col"]["b"]) / 2 - (h["star"]["t"] + h["star"]["b"]) / 2), 2, h)
        self.assertLessEqual(h["hero"]["b"] - h["star"]["b"], 24, h)                           # под ней — только отступ карточки

    def test_e_phone_keeps_the_old_layout(self):
        h = self.r["heroPh"]
        self.assertEqual(h["dev"], "phone")
        self.assertLessEqual(abs(h["d30"]["l"] - h["inner"]["l"]), 1, h)
        self.assertLessEqual(abs(h["d30"]["r"] - h["inner"]["r"]), 1, h)
        self.assertGreaterEqual(h["d30"]["t"], h["star"]["b"], h)
        self.assertGreaterEqual(h["v"]["l"], h["star"]["r"], h)              # вердикт — справа от «паутинки», как было
        self.assertEqual((h["iss"]["t"] - h["v"]["b"], h["d30"]["t"] - h["iss"]["b"]), (10, 10), h)   # зазоры прежние

    def check_computer(self, f, text):
        us, rel, now = f["head"][3], f["head"][4], f["head"][2]
        few = [r for r in f["rows"] if r["few"]]
        self.assertEqual(len(few), 5, [r["name"] for r in f["rows"]])        # ?score=links: «обычно» нет у 5 звеньев из 7
        for r in few:
            c = r["few"]
            self.assertEqual((c["box"]["l"], c["box"]["r"]), (us["l"], rel["r"]), r)   # на месте «Обычно» и «От обычного»
            self.assertEqual(r["v"]["r"], now["r"], r)
            self.assertLessEqual(abs((c["box"]["t"] + c["box"]["b"]) / 2 - (r["v"]["t"] + r["v"]["b"]) / 2), 2, r)
            self.assertEqual((c["text"], c["span"], c["pips"], c["on"], r["us"], r["rel"], r["mk"]), (text, "2", 3, 2, None, None, False))
            self.assertLessEqual(c["sw"], c["cw"], r)
        other = [r for r in f["rows"] if not r["few"]]
        for r in other:
            self.assertEqual((r["us"]["l"], r["rel"]["r"]), (us["l"], rel["r"]), r)

    def test_f_computer_ru_and_en(self):
        self.check_computer(self.r["few"], "«обычно» появится, когда наберётся история")
        self.check_computer(self.r["fewEn"], "“usually” appears once there’s enough history")

    def test_f_card_520_720_same_columns(self):
        """Окно 1100: карточка «Звенья» 520–720 (строка в два этажа, шапка без «30 дней») — ячейка на месте тех же колонок."""
        self.check_computer(self.r["few1100"], "«обычно» появится, когда наберётся история")

    def test_f_phone_and_narrow_card_third_line(self):
        for key in ("fewPh", "few1024"):
            f = self.r[key]
            few = [r for r in f["rows"] if r["few"]]
            self.assertEqual(len(few), 5, key)
            for r in few:
                c = r["few"]["box"]
                self.assertGreaterEqual(c["t"], r["line"]["b"], (key, r))     # под линией
                self.assertEqual((c["l"], c["r"]), (r["row"]["l"], r["row"]["r"]), (key, r))
                self.assertEqual(r["v"]["r"], r["row"]["r"], (key, r))         # «Сейчас» — у правого края
                self.assertLessEqual(r["few"]["sw"], r["few"]["cw"], (key, r))


if __name__ == "__main__":
    unittest.main()
