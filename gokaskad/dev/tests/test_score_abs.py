"""Круг 1, Т1 (владелец 27.10: «сухая синтетика»): балл 0–1000 восьми звеньев и итог — ``kaskad.score_abs``.

Эталон — код оценки до score-cut (коммит 3cae428a^: score.total по score-curves.json ``abs`` 0.4-draft): числа ниже
посчитаны им (сверка на 400 случайных наборах совпала до единицы) — на якорях исследования, поэтому сверка идёт с ними
(``research_anchors``); калибровка К1 и её якоря — tests/test_score_abs_calib.py.
"""

import contextlib
import math
import unittest

from kaskad import score_abs as SA
from tests.test_score_abs_calib import _data


@contextlib.contextmanager
def research_anchors():
    """Якоря исследования (0.4-draft) на время блока — эталон кода до score-cut считал по ним."""
    keep = dict((tid, t["anchors"]) for tid, t in SA.TESTS.items())
    try:
        for tid, a in _data()["research_anchors"].items():
            SA.TESTS[tid]["anchors"] = dict(zip(SA.A, a))
        yield
    finally:
        for tid, a in keep.items():
            SA.TESTS[tid]["anchors"] = a

FAST = {"net_down": 1700.0, "net_up": 1300.0, "rtt": 3.0, "loss": {"v": 0.0, "n": 20000, "lo": 0.009},
        "jitter": {"v": 0.3, "n": 3000}, "dns": 5.0, "cpu_1t": 2700.0, "cpu_mt": 10500.0, "chacha_1t": 2400.0,
        "aes_1t": 7000.0, "steal_p95": 0.2, "cpu_cv": 1.2, "cpu_sustain": 0.99, "mem_gb": 8.0, "mem_bw": 20000.0,
        "disk_rr": 130000.0, "disk_rw": 85000.0, "disk_fsync": 0.15}
# (итог, слабое звено, баллы звеньев) старого score.total; слабое звено — по правилу 30 % (владелец 29.10, D-143): у
# «fast» и «quick» худшее звено не ниже среднего больше чем на 30 % — слабого звена нет, итог тот же (предел их не держал)
REF = {
    "fast": (906, None, {"net_bw": 912, "net_q": 911, "cpu": 910, "stab": 916, "down": 915, "up": 910, "loss": 905,
                           "crypto": 916}),
    "slow_up": (350, "up", {"net_bw": 496, "net_q": 911, "cpu": 910, "stab": 916, "down": 915, "up": 77, "loss": 905,
                            "crypto": 916}),
    "quick": (885, None, {"net_bw": 805, "net_q": 911, "cpu": 910, "stab": 916, "down": 700, "up": 910, "loss": 905,
                            "crypto": 916}),
    "vless_aes": (371, "crypto", {"net_bw": 912, "net_q": 911, "cpu": 804, "stab": 916, "down": 915, "up": 910,
                                  "loss": 905, "crypto": 82}),
    "lossy": (488, "loss", {"net_bw": 912, "net_q": 619, "cpu": 910, "stab": 916, "down": 915, "up": 910, "loss": 138,
                            "crypto": 916}),
}
CASES = {"fast": FAST, "slow_up": dict(FAST, net_up=15.0), "quick": dict(FAST, net_down={"v": 1700.0, "mode": "quick"}),
         "vless_aes": dict(FAST, _engines=["vless"], _cpu_flags=["aes"], aes_1t=250.0),
         "lossy": dict(FAST, loss={"v": 3.0, "n": 20000, "lo": 3.4})}


class WeakLinkTest(unittest.TestCase):
    """Владелец 29.10 (D-143): слабое звено — только если его балл ниже среднего по звеньям этой проверки больше чем
    на 30 %; звенья без данных в среднее не входят."""

    @staticmethod
    def links(worst, rest=1000, n=7, **more):
        out = dict((k, rest) for k in SA.LINK_IDS[1:n + 1])
        out.update(net_bw=worst, **more)
        return out

    def test_29_and_31_percent(self):
        # 8 звеньев: худшее b, остальные 1000 — среднее (b + 7000) / 8
        self.assertIsNone(SA.weak_link(self.links(680)))             # 680 / 960 = 0,708 — ниже на 29,2 %
        self.assertEqual(SA.weak_link(self.links(660)), "net_bw")   # 660 / 957,5 = 0,689 — ниже на 31,1 %

    def test_the_boundary_is_strict(self):
        # два звена: 70 и 130 — среднее 100, ниже ровно на 30 % — не слабое; 69 и 131 — на 31 % — слабое
        self.assertIsNone(SA.weak_link({"net_bw": 70, "net_q": 130}))
        self.assertEqual(SA.weak_link({"net_bw": 69, "net_q": 131}), "net_bw")
        self.assertIsNone(SA.weak_link({"net_bw": 71, "net_q": 129}))         # 29 %

    def test_links_without_data_are_not_in_the_mean(self):
        # с нулями вместо «нет данных» среднее упало бы и 500 не было бы слабым; без них среднее 800 -> 500 ниже на 37,5 %
        got = {"net_bw": 500, "net_q": 900, "cpu": 900, "stab": 900, "down": None, "up": None, "loss": None, "crypto": None}
        self.assertEqual(SA.weak_link(got), "net_bw")
        self.assertIsNone(SA.weak_link({"net_bw": 500, "net_q": None}))       # одно звено — сравнивать не с чем
        self.assertIsNone(SA.weak_link({}))
        self.assertIsNone(SA.weak_link(None))

    def test_ties_take_the_first_of_the_radar(self):
        got = dict((k, 900) for k in SA.LINK_IDS)
        got.update(cpu=300, loss=300)
        self.assertEqual(SA.weak_link(got), "cpu")

    def test_total_uses_the_rule(self):
        """slow_up: отдача 77 при среднем ≈ 756 — слабое звено, предел держит итог; lossy и vless_aes — тоже."""
        for name, weak in (("slow_up", "up"), ("lossy", "loss"), ("vless_aes", "crypto")):
            got = SA.total(CASES[name])
            self.assertEqual(got["weak"], weak, name)
            self.assertTrue(got["limited"], name)
            self.assertEqual(got["total"], got["cap"], name)

    def test_a_weak_link_above_the_total_does_not_hold_it(self):
        """Слабое звено есть, но его предел выше итога — limited False, итог = raw (в панели блока «Почему итог не
        выше» нет)."""
        got = SA.total(dict(FAST, net_down=300.0))          # скачивание 500 при среднем ≈ 840: ниже на 40 %
        self.assertEqual(got["weak"], "down")
        self.assertEqual(got["cap"], 850)                         # 500 + 350 — выше итога
        self.assertFalse(got["limited"])
        self.assertEqual(got["total"], got["raw"])
        got = SA.total(dict(FAST, net_down=420.0))          # 601 при среднем ≈ 847: ниже на 29 % — не слабое
        self.assertEqual((got["weak"], got["cap"]), (None, None))


class CurveTest(unittest.TestCase):
    def test_the_anchors_give_100_500_700_900(self):
        for tid, t in SA.TESTS.items():
            for a in SA.A:
                self.assertAlmostEqual(SA.score_of(tid, t["anchors"][a]), a, places=6, msg=(tid, a))

    def test_monotone_by_the_direction(self):
        self.assertLess(SA.score_of("net_down", 100), SA.score_of("net_down", 200))
        self.assertGreater(SA.score_of("rtt", 5), SA.score_of("rtt", 50))

    def test_ends_never_reach_0_or_1000_and_no_nonsense_values(self):
        self.assertGreater(SA.score_of("net_down", 0.0), 0)
        self.assertLess(SA.score_of("net_down", 1e9), 1000)
        for bad in (None, "100", True, float("nan"), float("inf"), -float("inf")):
            self.assertIsNone(SA.score_of("net_down", bad), bad)
        self.assertIsNone(SA.score_of("no_such_test", 1.0))

    def test_quick_cap_and_few_samples(self):
        self.assertEqual(SA.test_score("net_down", {"v": 5000.0, "mode": "quick"}), 700.0)
        self.assertIsNone(SA.test_score("jitter", {"v": 1.0, "n": 999}))          # min_n 1000
        self.assertIsNotNone(SA.test_score("jitter", {"v": 1.0, "n": 1000}))
        self.assertIsNone(SA.test_score("net_down", {"v": 500.0, "ok": False}))   # упавший тест — не измерено


class TotalTest(unittest.TestCase):
    def test_the_reference_of_the_code_before_the_cut(self):
        for name, vals in CASES.items():
            with research_anchors():
                got = SA.total(vals)
            tot, weak, links = REF[name]
            self.assertEqual((got["total"], got["weak"], got["links"]), (tot, weak, links), name)
            self.assertIsNone(got["why"], name)

    def test_the_weak_link_caps_the_total(self):
        got = SA.total(CASES["slow_up"])
        self.assertTrue(got["limited"])
        b = SA.score_of("net_up", 15.0)
        self.assertEqual(got["cap"], math.floor(b + 350 * min(1.0, b / 100.0)))   # b + 350·min(1, b/100)
        self.assertLess(got["cap"], got["raw"])
        self.assertEqual(got["total"], got["cap"])
        self.assertFalse(SA.total(FAST)["limited"])

    def test_no_weak_link_no_limit(self):
        """D-143: худшее звено не ниже среднего больше чем на 30 % — нет слабого звена и предела «слабое + 350»."""
        got = SA.total(FAST)
        self.assertEqual((got["weak"], got["cap"], got["limited"]), (None, None, False))
        self.assertEqual(got["total"], got["raw"])

    def test_no_total_without_a_required_group(self):
        vals = dict((k, v) for k, v in FAST.items() if k not in ("cpu_1t", "cpu_mt", "chacha_1t", "aes_1t"))
        got = SA.total(vals)
        self.assertIsNone(got["total"])
        self.assertEqual(got["why"], "incomplete")
        self.assertIsNone(got["links"]["cpu"])
        self.assertEqual(SA.total({})["why"], "nothing_measured")
        self.assertEqual(SA.total(None)["why"], "nothing_measured")

    def test_links_still_measured_are_left_out(self):
        """Владелец 28.10, решение 4 (вариант А): «Сеть» ещё копит пробы — итог по остальным звеньям; её вес, доля
        измеренного, обязательность и слабое звено её не касаются. Без ``skip`` такой набор итога не даёт."""
        young = dict((k, v) for k, v in FAST.items() if k not in ("rtt", "loss", "jitter"))
        young["dns"] = 150                                  # одна «Сеть» из DNS — слабое звено ≈191
        whole = SA.total(young)
        self.assertEqual(whole["weak"], "net_q")
        got = SA.total(young, skip=("net_q", "loss"))
        self.assertEqual((got["links"]["net_q"], got["links"]["loss"], got["why"]), (None, None, None))
        self.assertNotIn("net_q", got["groups"])
        self.assertNotEqual(got["weak"], "net_q")
        self.assertGreater(got["total"], whole["total"])
        self.assertGreater(got["measured"], whole["measured"])                   # «Сеть» (DNS из 4 тестов) — вне доли
        no_net = dict((k, v) for k, v in young.items() if k != "dns")
        self.assertEqual(SA.total(no_net)["why"], "incomplete")                    # «Сеть» обязательна
        self.assertIsNotNone(SA.total(no_net, skip=("net_q",))["total"])
        self.assertEqual(SA.total(FAST, skip=()), SA.total(FAST))

    def test_eight_links_in_the_order_of_the_radar(self):
        self.assertEqual(SA.LINK_IDS, ("net_bw", "net_q", "cpu", "stab", "down", "up", "loss", "crypto"))
        self.assertEqual(list(SA.total(FAST)["links"]), list(SA.LINK_IDS))

    def test_research_anchors_are_restored(self):
        before = dict((tid, dict(t["anchors"])) for tid, t in SA.TESTS.items())
        with research_anchors():
            self.assertEqual(SA.TESTS["cpu_1t"]["anchors"][900], 2500)
        self.assertEqual(dict((tid, t["anchors"]) for tid, t in SA.TESTS.items()), before)

    def test_median_and_quartiles(self):
        self.assertEqual(SA.median([3, None, 1, 2]), 2)
        self.assertEqual(SA.median([1, 2]), 2)
        self.assertIsNone(SA.median([None]))
        self.assertEqual(SA.quartiles([1, 2, 3, 4, 5]), (2, 4))
        self.assertIsNone(SA.quartiles([1]))


if __name__ == "__main__":
    unittest.main()
