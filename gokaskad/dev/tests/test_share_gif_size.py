"""«Поделиться», «≈ N МБ» у «Скачать GIF» (EST_MB в pages/diag-share.js) — честная оценка 15-секундного ролика: настоящий
GIF (makeGIF в Chrome без окна, tools/k3_share_gif_size.mjs) на заглушке панели в «худшем» кадре (полоса четырёх цветов
вперемешку, длинные числа, все галочки, строки «чьи это цифры») не больше оценки, а оценка не раздута — не больше 2,5
замера (макет r1 обещал 1,4/1,7 МБ при настоящих ≈ 0,2 МБ). Обе темы, оба формата. Нет Chrome или Node 22 — пропуск
с причиной; статическая часть (оценка под пределом 3 МБ, ступень 10 кадров/с) — без них."""

import json
import os
import re
import subprocess
import unittest

from tests.test_panel_diag_screen import HAS_NODE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(ROOT, "lib", "kaskad", "panel", "static", "pages", "diag-share.js")
TOOL = os.path.join(ROOT, "tools", "k3_share_gif_size.mjs")
MIB = 1024 * 1024


def est_mb():
    src = open(SHARE, encoding="utf-8").read()
    m = re.search(r"const EST_MB = \{ sq: ([0-9.]+), post: ([0-9.]+) \};", src)
    return {"sq": float(m.group(1)), "post": float(m.group(2))}, src


class EstimateTest(unittest.TestCase):
    def test_it_is_said_as_a_bound(self):
        # T-3310 М-4: «≈ 0,4 МБ» при настоящих 0,06 МБ — оценка худшего кадра; честно — «до 0,4 МБ»
        for lang, word in (("ru", "до {mb} МБ"), ("en", "up to {mb} MB")):
            with open(os.path.join(ROOT, "lib", "kaskad", "panel", "static", "pages", "diag-i18n-%s.js" % lang),
                      encoding="utf-8") as f:
                line = [l for l in f.read().splitlines() if '"dg.sh.info"' in l][0]
            self.assertIn(word, line)
            self.assertNotIn("≈", line)

    def test_the_estimate_is_under_the_limit(self):
        est, src = est_mb()
        limit = int(re.search(r"const MAX_BYTES = (\d+) \* 1024 \* 1024;", src).group(1))
        for fmt, v in est.items():
            self.assertTrue(0.1 <= v < limit, (fmt, v))

    @unittest.skipUnless(HAS_NODE, "нет node")
    def test_the_estimate_matches_a_real_gif(self):
        r = subprocess.run(["node", TOOL, "sq,post", "dark,light", "ru", "worst"], cwd=ROOT, capture_output=True,
                           text=True, timeout=300)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        if out.get("skip"):
            self.skipTest(out["skip"])
        self.assertEqual(r.returncode, 0, out)
        est, _ = est_mb()
        self.assertEqual(len(out["res"]), 4, out)
        for fmt in ("sq", "post"):
            got = [x for x in out["res"] if x["fmt"] == fmt]
            top = max(x["bytes"] for x in got)
            for x in got:
                self.assertEqual(x["fps"], 15, x)                                 # не ступень 10 кадров/с
                self.assertLessEqual(x["bytes"], est[fmt] * MIB, x)              # оценка не меньше настоящего
            self.assertLessEqual(est[fmt] * MIB, 2.5 * top, (fmt, est[fmt], top))   # и не раздута


if __name__ == "__main__":
    unittest.main()
