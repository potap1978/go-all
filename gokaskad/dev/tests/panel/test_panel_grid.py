"""Сетка графиков (Claude Design «Сетка на графиках r1» 30.10): одна шкала 1-2-5 для всех линейных графиков панели —
KaskadSpark.grid (static/spark.js) и ось рядов выхода KaskadCharts.axis; офлайн в node (tests/panel/test_grid.mjs).
Без node — пропуск."""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


@unittest.skipUnless(shutil.which("node"), "нет node: шкала сетки графиков не проверена")
class GridJsTest(unittest.TestCase):
    def test_scale_ticks_fit(self):
        r = subprocess.run(["node", os.path.join(HERE, "test_grid.mjs")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=120, cwd=ROOT)
        out = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, out[-3000:])
        self.assertRegex(out, r"GRID OK: \d+ проверок в 13 тестах")


if __name__ == "__main__":
    unittest.main()
