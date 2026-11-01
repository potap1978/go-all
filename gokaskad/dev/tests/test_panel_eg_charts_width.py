"""Графики детали выхода (pages/exits-groups.js, T8m: подписи осей 11 px на 390 px) — по ширине места, и когда она
меняется (ветка выпуска 3.3.7, шаг 4: k3_layout_check открывает деталь на 1440, затем сужает до 390 — графики,
нарисованные по боковому листу компьютера, давали подписи 8,8 px; под нагрузкой — каждый прогон, по одному виду — нет).
``onWidth``: смена ширины на 8 px и больше — перерисовать (из тех же данных, без запроса), мелочь — нет; блок ушёл из
страницы — наблюдение снято. ``chartsBlock``: ответ прежнего диапазона не рисует поверх нового."""
import re
import unittest

from tests import jsparts
from tests.test_panel_kit_s1 import HAS_NODE, read, run_node

EG = "pages/exits-groups.js"


@unittest.skipUnless(HAS_NODE, "no node")
class OnWidthTest(unittest.TestCase):
    def run_case(self, body):
        fn = jsparts.function(read(EG), "onWidth")
        return run_node(r"""
const obs = [];
global.ResizeObserver = class { constructor(cb) { this.cb = cb; this.off = false; obs.push(this); } observe() {} disconnect() { this.off = true; } };
%s
const out = {};
%s
console.log(JSON.stringify(out));
""" % (fn, body))

    def test_redraws_when_the_width_changes(self):
        out = self.run_case(r"""
const n = { clientWidth: 0, isConnected: false }; let calls = 0;
onWidth(n, () => { calls += 1; });
obs[0].cb(); out.unplaced = calls;                   // ещё не в листе: размера нет
n.isConnected = true; n.clientWidth = 447; obs[0].cb(); out.placed = calls;   // встал в лист — рисуем по нему
n.clientWidth = 450; obs[0].cb(); out.small = calls;                         // мелочь — нет
n.clientWidth = 358; obs[0].cb(); out.narrow = calls;                        // сузили до 390 — заново
n.isConnected = false; n.clientWidth = 0; obs[0].cb(); out.gone = obs[0].off; out.calls = calls;""")
        self.assertEqual(out, {"unplaced": 0, "placed": 1, "small": 1, "narrow": 2, "gone": True, "calls": 2})

    def test_charts_block_uses_it(self):
        src = jsparts.function(read(EG), "chartsBlock")
        self.assertIn("onWidth(box, () => { if (st.last) paint(st.last); });", src)
        self.assertRegex(src, r"const width = box\.clientWidth")
        self.assertIn("(j.points || []).map((p) => p.slice())", src)         # перерисовка не дописывает трафик дважды
        self.assertTrue(re.search(r"if \(seq === st\.seq\) \{? ?paint\(j\)", src), "ответ прежнего диапазона")


if __name__ == "__main__":
    unittest.main()
