"""REV-it1-b П-2: правила для разметки, которую Т3 убрал (деталь VLESS на телефоне, окно выбора перед выдачей,
подстрока VLESS), — удалены; класс не должен вернуться в CSS без разметки, которая его использует."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
GONE = ("kk-vphone", "kk-vhero", "kk-qrpick", "kk-vsub")


def texts(ext):
    out = {}
    for dp, _, fns in os.walk(STATIC):
        for fn in fns:
            if fn.endswith(ext):
                with open(os.path.join(dp, fn), encoding="utf-8") as f:
                    out[os.path.relpath(os.path.join(dp, fn), STATIC)] = f.read()
    return out


class DeadCssTest(unittest.TestCase):
    def test_removed_markup_has_no_rules(self):
        css, js = texts(".css"), texts(".js")
        for cls in GONE:
            pat = re.compile(r"\b%s\b" % re.escape(cls))
            self.assertEqual([n for n, s in js.items() if pat.search(s)], [], cls)       # разметки нет…
            self.assertEqual([n for n, s in css.items() if pat.search(s)], [], cls)      # …и правил нет

    def test_live_rule_kept(self):
        # .kk-actl — строка активности «Выходов» (exRow), не мёртвый (в REV-it1-b П-2 — по ошибке)
        self.assertIn("kk-actl", texts(".js")["app.js"])
        self.assertIn(".kk-actl {", texts(".css")["people.css"])


if __name__ == "__main__":
    unittest.main()
