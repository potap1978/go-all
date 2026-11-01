"""UI-A2: «Выходы › Все выходы» таблицей — слово состояния и счётчики фильтра.

Одно состояние выхода — одно слово на компьютере (таблица, exRowSt) и на телефоне (строка и герой, exitWord): раньше
телефон не знал состояний по проверкам группы — ухудшенный выход был «работает», запасной — «ещё не проверялся».
Фильтр «Все · Работают · С проблемой» взаимоисключающий: сумма не больше «Все» (правило 14 детектора).
Раскладка таблицы — tools/k3_layout_check.mjs и k3/stand/ui_audit_run.mjs; удержание строк — tools/k3_hold_check.mjs."""
import os
import shutil
import unittest

from tests import jsparts
from tests.test_332f_give import run_node

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_js():
    with open(os.path.join(ROOT, "lib", "kaskad", "panel", "static", "app.js"), encoding="utf-8") as f:
        return f.read()


@unittest.skipUnless(shutil.which("node"), "нет node")
class ExitWordsTest(unittest.TestCase):
    def run_rows(self):
        js = app_js()
        src = "\n".join([
            "const t = (k) => k; let GW = true; const gwEnabled = () => GW;",
            jsparts.const(js, "exitOwn"), jsparts.const(js, "exitGwOff"), jsparts.const(js, "EXIT_CHIP"),
            jsparts.function(js, "exRowSt"), jsparts.const(js, "exCounts"), jsparts.function(js, "exitWord"),
        ]) + r"""
const id = "e-0123456789";
const rows = [
  { id, state: "alive", enabled: true },
  { id, state: "alive", enabled: true, hasPolicy: true, pstate: "degraded" },
  { id, state: "alive", enabled: true, hasPolicy: true, pstate: "off" },
  { id, state: "unknown", enabled: true, hasPolicy: true, pstate: "standby" },
  { id, state: "dead", enabled: true, hasPolicy: true, pstate: "degraded" },
  { id, state: "dead", enabled: true },
  { id, state: "unknown", enabled: true },
  { id, state: "nodata", enabled: true },
  { id, state: "alive", enabled: false },
  { id, state: "alive", enabled: true, problem: "dup" }];
const out = rows.map((r) => [exRowSt(r).w, exitWord(r).text]);
GW = false;
const gwOff = [exRowSt({ id, state: "unknown", enabled: true }).w, exitWord({ id, state: "unknown", enabled: true }).text];
console.log(JSON.stringify({ out, gwOff, counts: exCounts(rows) }));
"""
        return run_node(src)

    def test_one_word_on_phone_and_table(self):
        got = self.run_rows()
        want = [["ex.alive"] * 2, ["ex.pst.degraded"] * 2, ["ex.dead"] * 2, ["ex.pst.standby"] * 2, ["ex.dead"] * 2,
                ["ex.dead"] * 2, ["ex.xt.unknown"] * 2, ["ex.nodata"] * 2, ["ex.xt.off", "ex.offForGw"], ["ex.keyProblem"] * 2]
        self.assertEqual(got["out"], want)
        self.assertEqual(got["gwOff"], ["ex.gwOff"] * 2)

    def test_filter_counts_do_not_overlap(self):
        c = self.run_rows()["counts"]
        # работают: alive; с проблемой: ухудшен, не работает (3), ключ не разобран; прочие (запас, не проверен, нет данных, выключен) — только во «Все»
        self.assertEqual(c, {"all": 10, "ok": 1, "bad": 5})
        self.assertLessEqual(c["ok"] + c["bad"], c["all"])


if __name__ == "__main__":
    unittest.main()
