"""Круг 1 Т1 (вариант Б «Обзора»): чистые помощники части static/pages/ovb.js — офлайн в node
(tests/panel/test_ovb.mjs). Без node — пропуск."""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


@unittest.skipUnless(shutil.which("node"), "нет node: помощники «Обзора» не проверены")
class OvbJsTest(unittest.TestCase):
    def test_pure_helpers(self):
        r = subprocess.run(["node", os.path.join(HERE, "test_ovb.mjs")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=120, cwd=ROOT)
        out = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, out[-3000:])
        self.assertRegex(out, r"OVB OK: \d+ проверок в 15 тестах")


class NoRemarksTest(unittest.TestCase):
    """Владелец 28.10 «Замечания не пишем»: на Сводке нет строк-замечаний балла («… хуже обычного») и здоровья, в
    заголовке состояния — нет «· N замечаний» (RU и EN); отметки ▲▼ звеньев остаются (подсказка радара)."""

    def read(self, name):
        with open(os.path.join(ROOT, "lib", "kaskad", "panel", "static", "pages", name), encoding="utf-8") as f:
            return f.read()

    def test_no_remark_rows_and_no_count_in_the_state(self):
        js = self.read("ovb.js")
        for gone in ("ovb.isWorse", "ovb.v.ok1", '"sc:"', '"hh:"'):
            self.assertFalse(gone in js, gone)
        self.assertTrue("ovb.tipDn" in js)                                        # ▼ «хуже обычного» у звена — данные, остаётся
        for name, word in (("ovb-i18n-ru.js", "замечани"), ("ovb-i18n-en.js", "remark")):
            src = self.read(name)
            states = [ln for ln in src.splitlines() if '"ovb.v.' in ln]
            self.assertTrue(states, name)
            for ln in states:
                self.assertNotIn(word, ln.lower(), ln)
                self.assertNotIn("{n}", ln, ln)

    def test_health_screen_head_has_no_count(self):
        # «Здоровье сервера»: «Внимание: N замечаний» → состояние словами без числа (решение владельца 28.10)
        js = self.read("ovb.js")
        self.assertNotIn('tn("ovb.hh."', js)
        for name, word in (("ovb-i18n-ru.js", "замечани"), ("ovb-i18n-en.js", "remark")):
            src = self.read(name)
            heads = [ln for ln in src.splitlines() if '"ovb.hh.warn"' in ln or '"ovb.hh.crit"' in ln]
            self.assertEqual(len(heads), 2, name)
            for ln in heads:
                self.assertNotIn(word, ln.lower(), ln)
                self.assertNotIn("{n}", ln, ln)
                self.assertNotIn("|", ln, ln)



class NoReferenceServersTest(unittest.TestCase):
    """Владелец 29.10 (D-142) «Вот эту инфу надо убрать, где упоминается aeza»: в «Как считается балл» нет блока
    «Эталоны» — ни хостера, ни тарифа, ни процессора, ни города эталонных серверов (RU и EN, часть ovb и мок панели).
    Сами эталоны — ``score_abs.REFS`` внутри кода, для шкалы и тестов калибровки."""

    def read(self, *path):
        with open(os.path.join(ROOT, "lib", "kaskad", "panel", "static", *path), encoding="utf-8") as f:
            return f.read()

    def test_no_hoster_tariff_cpu_or_city_of_the_references(self):
        from kaskad import score_abs as SA
        words = sorted({str(r[k]) for r in SA.REFS for k in ("hoster", "tariff", "cpu")} | {"9950X", "Ryzen"})
        self.assertIn("Aeza", words)                                   # сторож видит то, что прячет
        for path in (("pages", "ovb-i18n-ru.js"), ("pages", "ovb-i18n-en.js"), ("pages", "ovb.js"), ("dev-mock.js",),
                     ("ovb.css",)):
            src = self.read(*path).lower()
            for w in words:
                self.assertNotIn(w.lower(), src, (path, w))
        for name in ("ovb-i18n-ru.js", "ovb-i18n-en.js", "ovb.js"):
            src = self.read("pages", name)
            for gone in ("{host}", "{tariff}", "{cpu}", "{city}", "ovb.refsT", "ovb.refCpu", "ovb.cityMsk", '"ovb.ref"',
                         "Москва", "Moscow", "kb-refs", "syn.refs"):
                self.assertNotIn(gone, src, (name, gone))


if __name__ == "__main__":
    unittest.main()
