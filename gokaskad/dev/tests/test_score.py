"""B4 after the cut of 23.10 (SPEC-node-score.md NS-D30–NS-D34): what of lib/kaskad/score.py is not the history — the
catalogue of the tests and the links (``score.SPEC`` / ``load_spec()``, the part of score-curves.json the history
needs, without the scale), the table of the Student quantile of ▲/▼ (NS-D25, unchanged — NS-D32) and the loss link
(Wilson bound, «seen by >= 2 of 3 anchors») against the independent sample of A.4 (tests/score_ref.py).
The scale 0–1000 itself (curves, bands, weights, the weak link, C2, the samples of appendix C as totals) is gone with
the code; the relative score of a node against its own history — tests/test_score_own.py."""

import math
import random
import unittest

from kaskad import score as S
from tests import score_ref as REF

SPEC = S.load_spec()
T = S.tests_of(SPEC)


class Catalogue(unittest.TestCase):
    def test_links_and_groups(self):
        self.assertEqual([b["id"] for b in SPEC["links"]],
                         ["net_bw", "net_q", "cpu", "stab", "down", "up", "loss", "crypto"])
        self.assertEqual([g["id"] for g in SPEC["groups"]], ["net_bw", "net_q", "cpu", "stab", "mem", "disk"])
        self.assertEqual(S.TEST_LINKS, ("down", "up", "crypto", "net_bw", "cpu"))
        self.assertEqual(S.PROBE_LINKS, ("loss", "net_q", "stab"))
        self.assertEqual(set(S.TEST_LINKS) | set(S.PROBE_LINKS), set(b["id"] for b in SPEC["links"]))
        for t in T.values():
            self.assertIn(t["better"], ("higher", "lower"), t["id"])
            self.assertTrue(t["unit"], t["id"])
            self.assertGreater(S.floor_of(t), 0, t["id"])

    def test_no_scale_left(self):
        """NS-D34: no anchors, weights, bands, weak link, star axes or capacity in the catalogue; no scale functions."""
        def keys(x):
            if isinstance(x, dict):
                return set(x) | set().union(*[keys(v) for v in x.values()])
            if isinstance(x, list):
                return set().union(*[keys(v) for v in x]) if x else set()
            return set()
        self.assertFalse(keys(SPEC) & {"anchors", "weight", "bands", "combine", "weak_link", "star_axes", "capacity",
                                       "reference_machines", "needs_tariff", "quick_cap", "min_n"})
        for name in ("load_curves", "score", "total", "combine", "test_scores", "band", "shown", "rel_view",
                     "day_score", "capacity"):
            self.assertFalse(hasattr(S, name), name)

    def test_rel_floor(self):
        """NS-D30, NS-D35: the floor of rel of the tests where less is better — loss 0.1 %, delay 1 ms, jitter 0.5 ms,
        dns 1 ms, steal and the two cv 1 %, fsync 0.1 ms; no test where more is better has one."""
        got = dict((i, t["rel_floor"]) for i, t in T.items() if "rel_floor" in t)
        self.assertEqual(got, {"loss": 0.1, "rtt": 1.0, "jitter": 0.5, "dns": 1.0, "steal_p95": 1.0, "cpu_cv": 1.0,
                               "cpu_canary_cv": 1.0, "disk_fsync": 0.1})
        for i in got:
            self.assertEqual(T[i]["better"], "lower", i)

    def test_load_spec_is_a_copy(self):
        a = S.load_spec()
        a["links"].pop()
        a["groups"][0]["tests"][0]["unit"] = "x"
        self.assertEqual(len(S.load_spec()["links"]), 8)
        self.assertEqual(S.load_spec()["groups"][0]["tests"][0]["unit"], "Мбит/с")

    def test_crypto_by_the_engines(self):
        """The cipher the node's engines really use: ChaCha20 for AmneziaWG/WireGuard and VLESS without AES-NI,
        AES-GCM for VLESS with it."""
        b = next(x for x in SPEC["links"] if x["id"] == "crypto")
        self.assertEqual(S.link_tests(SPEC, b, ["awg"], ["aes"])[0], ["chacha_1t"])
        self.assertEqual(S.link_tests(SPEC, b, ["vless"], ["aes"])[0], ["aes_1t"])
        self.assertEqual(S.link_tests(SPEC, b, ["vless"], [])[0], ["chacha_1t"])
        self.assertEqual(S.link_tests(SPEC, b, ["awg", "vless"], ["aes"])[0], ["chacha_1t", "aes_1t"])
        net_q = next(x for x in SPEC["links"] if x["id"] == "net_q")
        self.assertEqual(S.link_tests(SPEC, net_q), (["rtt", "loss", "jitter", "dns"], True))


class KTable(unittest.TestCase):
    def test_k_table(self):
        """The table of NS-D25 (printed by dev_calc.py), n_base 4…10."""
        spec = {4: (3.182, 4.177, 4.857, 5.392, 5.841), 5: (2.776, 3.495, 3.961, 4.315, 4.604),
                6: (2.571, 3.163, 3.534, 3.810, 4.032), 7: (2.447, 2.969, 3.287, 3.521, 3.707),
                8: (2.365, 2.841, 3.128, 3.335, 3.499), 9: (2.306, 2.752, 3.016, 3.206, 3.355),
                10: (2.262, 2.685, 2.933, 3.111, 3.250)}
        for n, row in spec.items():
            self.assertEqual(S.T_TABLE[n - 1], row)
        for df in range(3, 29):                 # the Student quantile falls with df and grows with m
            for m in range(5):
                self.assertGreater(S.T_TABLE[df][m], S.T_TABLE[df + 1][m])
                if m:
                    self.assertGreater(S.T_TABLE[df][m], S.T_TABLE[df][m - 1])
        self.assertEqual(S._k(40, 1), S.T_TABLE[29][0])
        self.assertEqual(S._k(1, 9), S.T_TABLE[3][4])        # df below 3 and m above 5 are clamped


class LossReference(unittest.TestCase):
    """The loss link against the sample of A.4: the same v, lo and n on hand-made and random anchors."""

    def test_hand_made(self):
        pts = [dict(n=8640, k=1036.8, e=912.4), dict(n=8640, k=6.9, e=6.9), dict(n=8640, k=6.9, e=6.9)]
        self.assertEqual(S.loss_value(pts), REF.loss_value(pts))
        self.assertEqual(S.loss_value([dict(n=0, k=0, e=0)]), {"v": None, "why": "no_probes"})   # a code, not text
        self.assertEqual(S.wilson_lo(0, 100), 0.0)
        self.assertEqual(S.wilson_lo(5, 0), 0.0)
        self.assertEqual(S.best2([3.0, 1.0, 2.0]), 1.5)          # the worst dropped, the mean of the other two
        self.assertEqual(S.best2([3.0, 1.0]), 1.0)               # of two — the better
        self.assertEqual(S.best2([4.0]), 4.0)

    def test_random(self):
        rnd = random.Random(2026)
        for _ in range(3000):
            pts = []
            for _a in range(rnd.choice((1, 2, 3))):
                n = rnd.choice((300, 3000, 8640, 25920, 259200)) / 3.0
                k = n * math.exp(rnd.uniform(math.log(1e-5), math.log(0.3)))
                pts.append(dict(n=n, k=k, e=max(min(k, 1.0), k * rnd.uniform(0.05, 1))))
            self.assertEqual(S.loss_value(pts), REF.loss_value(pts), pts)
            n = rnd.uniform(1, 1e5)
            k = n * rnd.uniform(0, 0.5)
            self.assertEqual(S.wilson_lo(k, n), REF.wilson_lo(k, n))
            xs = [rnd.uniform(0, 5) for _ in range(rnd.choice((1, 2, 3)))]
            self.assertEqual(S.best2(xs), REF.best2(xs))


if __name__ == "__main__":
    unittest.main()
