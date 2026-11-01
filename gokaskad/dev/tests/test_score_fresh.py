"""B4, fresh and the retrospective (SPEC-node-score-schedule-share.md §4 «Тесты B4/B12» (1), (3)–(7); NS-S10, NS-S16,
NS-S17): the median of the 3 last good raw values, the channel by mode, the channel under load with the correction,
the label of the hour, first/stale, «новая база»; fresh of the probes. After the cut of 23.10 (NS-D34) the scores of
the values, the totals and days[].score are gone: the raw values are checked."""

import unittest

from kaskad import score as S
from tests.score_hist import ABS, DAY, FIX, S4, T, Hist, at, day_of, iso


class Fresh(unittest.TestCase):
    def fresh_down(self, xs, step=7, now_day=None):
        h = Hist()
        d = h.series("net_down", xs, step=step)
        return h.link(at(now_day if now_day is not None else d - step, 12), "down")

    def test_median(self):
        for xs, want, n in (([700, 700, 120], 700, 3), ([700, 120, 120], 120, 3), ([700, 120], 700, 2),
                            ([120], 120, 1)):
            row = self.fresh_down(xs)
            self.assertEqual((row["fresh_raw"], row["fresh_n"]), (want, n), xs)

    def test_age(self):
        h = Hist()
        h.add(0, {"net_down": 120, "cpu_1t": 1000})      # 46 d before now — not fresh
        h.add(30, {"net_down": 700, "cpu_1t": 1000})
        row = h.link(at(46, 4, 30), "down")
        self.assertEqual((row["fresh_raw"], row["fresh_n"]), (700, 1))
        h = Hist()
        h.add(0, {"net_down": 700, "cpu_1t": 1000})
        v = h.view(at(46, 5))
        self.assertTrue(v["summary"]["stale"])
        self.assertIsNone(v["summary"]["rel_total"])
        self.assertIsNone(next(x for x in v["links"] if x["id"] == "down")["fresh_raw"])
        v = h.view(at(44, 5))
        self.assertFalse(v["summary"]["stale"])
        cpu = next(x for x in v["links"] if x["id"] == "cpu")
        self.assertEqual([(p["test"], p["fresh_raw"]) for p in cpu["parts"] if p["fresh_raw"] is not None],
                         [("cpu_1t", 1000)])                             # 45 d (the channel's full value — 30 d)

    def test_stale_keeps_last(self):
        h = Hist()
        rid = h.add(0, dict(S4))
        v = h.view(at(46, 5))
        self.assertEqual((v["summary"]["stale"], v["summary"]["rel_total"]), (True, None))
        self.assertEqual((v["summary"]["last_id"], v["summary"]["last_at"]), (rid, at(0)))

    def test_no_good_check(self):
        h = Hist()
        h.add(0, dict(S4), status="failed")
        v = h.view(at(1, 5))
        self.assertEqual((v["summary"]["rel_total"], v["summary"]["last_id"], v["summary"]["last_at"]),
                         (None, None, None))
        self.assertTrue(all(x["fresh_raw"] is None and x["now_id"] is None for x in v["links"]))

    def test_failed_value_not_taken(self):
        h = Hist()
        h.add(0, {"net_down": 700})
        h.add(7, {"net_down": {"v": 50, "ok": False}})
        h.add(14, {"net_down": 700}, status="skipped")
        row = h.link(at(14, 12), "down")
        self.assertEqual((row["fresh_raw"], row["fresh_n"]), (700, 1))

    def test_channel_modes(self):
        """(3): a full 800 <= 30 d and three quick with raw >= 80 % -> the fresh download from the full one; quick < 80 %
        -> the median of the quick ones with quick_cap; the full older than 30 d -> only the quick ones."""
        def make(qs, full_day=0):
            h = Hist()
            h.add(full_day, {"net_down": 800}, mode="full")
            for i, x in enumerate(qs):
                h.add(7 + 7 * i, {"net_down": x}, mode="quick")
            return h
        row = make([700, 650, 660]).link(at(22, 12), "down")
        self.assertEqual(row["fresh_raw"], 800)
        row = make([500, 600, 620]).link(at(22, 12), "down")
        self.assertEqual(row["fresh_raw"], 600)
        row = make([1600, 1700, 1800]).link(at(22, 12), "down")
        self.assertEqual(row["fresh_raw"], 800)                 # quick 1700 >= 80 % of the full: the full one
        h = Hist()
        h.add(0, {"net_down": 1500}, mode="full")
        for i, x in enumerate([1500, 1600, 1700]):
            h.add(14 + 7 * i, {"net_down": x}, mode="quick")
        row = h.link(at(31, 12), "down")                         # the full one is 31 d old
        self.assertEqual(row["fresh_raw"], 1600)
        h = Hist()
        h.add(0, {"net_down": 1500}, mode="full")
        h.add(7, {"net_down": {"v": 600, "budget": True}}, mode="quick")  # the budget hit: a lower bound, no contradiction
        self.assertEqual(h.link(at(8, 12), "down")["fresh_raw"], 1500)

    def test_test_without_value_in_one_check(self):
        h = Hist()
        h.add(0, {"net_down": 700, "cpu_1t": 1500})
        h.add(7, {"net_down": 600})
        h.add(14, {"net_down": 650})
        fr = S.fresh(ABS, h.runs, at(14, 12), day_of=day_of)
        self.assertEqual(fr["meta"]["cpu_1t"]["fresh_n"], 1)
        self.assertEqual(fr["vals"]["cpu_1t"]["v"], 1500)
        self.assertEqual(fr["meta"]["net_down"]["fresh_n"], 3)
        self.assertEqual(fr["from"]["tests"], [r["id"] for r in h.runs])

    def test_fresh_of_three_checks_of_s4(self):
        """SS §4 (2б), the samples fresh_s4 (tests/fixtures/score_samples.json): three checks of S4 with the upload
        1300/5/5 -> 5, 1300/5/1300 -> 1300, 300/5/300 -> 300 — the median. C6: net_cv is made of the history, so the
        three downloads spread by 4 %, as net_cv of the sample (1632/1700/1768)."""
        self.assertEqual(len(FIX["fresh_s4"]), 3)
        for c in FIX["fresh_s4"]:
            h = Hist()
            for i, (up, down) in enumerate(zip(c["net_up"], [1632, 1700, 1768])):
                h.add(7 * i, dict(S4, net_up=up, net_down=down))
            v = h.view(at(14, 12))
            self.assertEqual(c["median"], sorted(c["net_up"])[1])
            self.assertEqual(S.fresh(ABS, h.runs, at(14, 12), day_of=day_of)["vals"]["net_up"]["v"], c["median"])
            up = next(x for x in v["links"] if x["id"] == "up")
            self.assertEqual((up["fresh_raw"], up["fresh_n"]), (c["median"], 3))
            self.assertAlmostEqual(v["stab"]["net_cv"]["v"], S4["net_cv"], 3)
            self.assertEqual([x["id"] for x in v["links"]], [b["id"] for b in ABS["links"]])

    def test_wait_from_last_check(self):
        """«ждёт истории» and ctx — of the last good check (S6)."""
        h = Hist()
        h.add(0, dict(S4, _engines=["vless"], cpu_canary_cv={"v": None, "wait": True}))
        fr = S.fresh(ABS, h.runs, at(1, 1), day_of=day_of)
        self.assertEqual(fr["vals"]["_engines"], ["vless"])
        self.assertTrue(fr["vals"]["cpu_canary_cv"]["wait"])
        self.assertNotIn("cpu_canary_cv", fr["meta"])                      # a mark, not a fresh value


class Probes(unittest.TestCase):
    def test_windows(self):
        w = [{"day": "2026-10-17", "hours": 24, "rtt": 20, "jitter": {"v": 1.0, "n": 17000}, "steal_p95": 0.5,
              "loss": {"a": {"n": 8640, "k": 5, "e": 5}, "b": {"n": 8640, "k": 0, "e": 0}, "c": {"n": 8640, "k": 9, "e": 3}}},
             {"day": "2026-10-16", "hours": 24, "rtt": 30, "jitter": {"v": 2.0, "n": 17000}, "steal_p95": 0.7,
              "loss": {"a": {"n": 8640, "k": 1, "e": 1}, "b": {"n": 8640, "k": 2, "e": 2}, "c": {"n": 8640, "k": 0, "e": 0}}},
             {"day": "2026-10-15", "hours": 11, "rtt": 90, "loss": {"a": {"n": 4000, "k": 400, "e": 1}}},
             {"day": "2026-10-14", "hours": 24, "rtt": 10}]
        vals, days = S.fresh_probes(w, T)
        self.assertEqual(vals["rtt"]["v"], 20)                   # 2 windows (11 h does not count; the 4th is beyond 3)
        self.assertEqual(vals["jitter"], {"v": 1.0, "n": 34000})
        self.assertEqual(vals["steal_p95"]["v"], 0.5)
        want = S.loss_value([{"n": 17280, "k": 6, "e": 6}, {"n": 17280, "k": 2, "e": 2}, {"n": 17280, "k": 9, "e": 3}])
        want.pop("points")
        self.assertEqual(vals["loss"], want)
        self.assertEqual(days["loss"], ["2026-10-17", "2026-10-16"])

    def test_view_with_probes(self):
        h = Hist()
        h.add(0, dict(S4))
        w = [{"day": "2026-09-01", "hours": 24, "rtt": 2.5, "jitter": {"v": 0.25, "n": 17280}, "steal_p95": 0.1,
              "loss": {"a": {"n": 8640, "k": 0.86, "e": 0.86}}}]
        v = h.view(at(1, 1), probes=w, probe_days={})
        self.assertEqual(v["summary"]["fresh_from"]["days"], ["2026-09-01"])
        self.assertEqual(next(x for x in v["links"] if x["id"] == "loss")["source"], "probes")


class UnderLoad(unittest.TestCase):
    """NS-S16: value_corr = min(value + people, max(value, L)), L = max(usual_raw, fresh_quiet) of the same mode."""

    def test_formula(self):
        self.assertEqual(S.load_correct(420, 180, 600), 600)
        self.assertEqual(S.load_correct(420, 180, 550), 550)
        self.assertEqual(S.load_correct(300, 200, 300), 300)     # the bottleneck is the path to the point
        self.assertEqual(S.load_correct(500, 100, 450), 500)
        self.assertIsNone(S.load_correct(100, 180, 600))         # people > value — not usable
        self.assertIsNone(S.load_correct(420, 180, None))        # no quiet level — not usable
        self.assertEqual(S.load_correct(420, 180, 650, port=500), 500)

    def level(self, h, ts, mode="full", **kw):
        return S.quiet_level(ABS, h.runs, "net_down", mode, ts, day_of=day_of, **kw)

    def test_quiet_level(self):
        h = Hist()
        h.series("net_down", [600] * 8)
        self.assertAlmostEqual(self.level(h, at(56, 12)), 600)
        self.assertEqual(S.load_correct(420, 180, self.level(h, at(56, 12))), 600)
        h = Hist()
        h.series("net_down", [550] * 8)
        self.assertAlmostEqual(S.load_correct(420, 180, self.level(h, at(56, 12))), 550)
        self.assertIsNone(self.level(Hist(), at(56, 12)))         # neither base nor quiet fresh
        h = Hist()
        h.add(50, {"net_down": 600})
        self.assertEqual(self.level(h, at(56, 12)), 600)          # one quiet fresh without base
        self.assertEqual(S.load_correct(420, 180, self.level(h, at(56, 12))), 600)

    def test_modes(self):
        """Quick ≈ 1000 (base), a quiet full 1900 among the fresh full ones, a full under load 1300 + 600 -> 1900; no
        quiet full ones -> not usable."""
        h = Hist()
        for i in range(8):
            h.add(7 * i, {"net_down": 1000}, mode="quick")
        h.add(55, {"net_down": 1900}, mode="full", hh=6)
        L = self.level(h, at(56, 12), "full")
        self.assertEqual(S.load_correct(1300, 600, L), 1900)
        h2 = Hist()
        for i in range(8):
            h2.add(7 * i, {"net_down": 1000}, mode="quick")
        self.assertIsNone(S.load_correct(1300, 600, self.level(h2, at(56, 12), "full")))
        self.assertAlmostEqual(self.level(h2, at(56, 12), "quick"), 1000)

    def test_age_of_quiet(self):
        h = Hist()
        h.add(0, {"net_down": 1900}, mode="full")
        self.assertIsNone(self.level(h, at(31, 4, 1), "full"))     # 31 d — not taken
        self.assertEqual(self.level(h, at(30, 4), "full"), 1900)  # 30 d — taken
        h = Hist()
        h.add(0, {"net_down": 900}, mode="quick")
        self.assertIsNone(self.level(h, at(46, 4), "quick"))
        self.assertEqual(self.level(h, at(45, 4), "quick"), 900)
        h = Hist()
        h.add(0, {"net_down": 2500}, mode="full")                  # 50 d
        h.add(15, {"net_down": 2400}, mode="full")                 # 35 d
        h.add(45, {"net_down": 1900}, mode="full")                 # 5 d
        self.assertEqual(self.level(h, at(50, 4), "full"), 1900)  # the age cuts before the pick of 3

    def test_growth(self):
        """«Обычно» 480, quiet fresh 950/960/940, 600 + 350 -> 950."""
        h = Hist()
        h.series("net_down", [480] * 6, step=7)
        for i, x in enumerate([950, 960, 940]):
            h.add(42 + i, {"net_down": x})
        L = self.level(h, at(45, 12))
        self.assertEqual(L, 950)
        self.assertEqual(S.load_correct(600, 350, L), 950)

    def test_into_fresh(self):
        """A run under load gives the channel by value_corr; its other tests (crypto) do not go to fresh;
        quiet unknown — as measured."""
        h = Hist()
        h.add(0, {"net_down": 700, "chacha_1t": 1500})
        h.add(7, {"net_down": {"v": 420, "people": 180, "corr": 600}, "chacha_1t": 300}, under_load=True)
        h.add(14, {"net_down": {"v": 400, "people": 900}, "chacha_1t": 300}, under_load=True)   # not usable
        fr = S.fresh(ABS, h.runs, at(14, 12), day_of=day_of)
        self.assertEqual((fr["vals"]["net_down"]["v"], fr["meta"]["net_down"]["fresh_n"]), (700, 2))
        self.assertEqual((fr["vals"]["chacha_1t"]["v"], fr["meta"]["chacha_1t"]["fresh_n"]), (1500, 1))
        h.add(21, {"net_down": 420}, under_load=None)
        fr = S.fresh(ABS, h.runs, at(21, 12), day_of=day_of)
        self.assertEqual(fr["vals"]["net_down"]["v"], 600)        # 700, 600, 420 -> 600
        self.assertIsNone(S.last_good(h.runs[:3], at(14, 12))["under_load"] or None)
        self.assertEqual(S.last_good(h.runs[:3], at(14, 12))["id"], h.runs[0]["id"])

    def test_new_base_quiet(self):
        """After «новая база» (tariff) runs under load are not usable until the first quiet point of the mode."""
        h = Hist()
        h.series("net_down", [600] * 8)
        ep = [{"ts": at(55, 10), "kind": "tariff"}]
        self.assertIsNone(self.level(h, at(56, 12), epochs=ep))


class Days(unittest.TestCase):
    def test_checked(self):
        h = Hist()
        for hh, mm in ((3, 50), (4, 5), (4, 10)):
            h.add(0, {"net_down": 700}, hh=hh, mm=mm)
        self.assertEqual(h.view(at(0, 12))["summary"]["checked"], {"part": "night", "hhmm": "04:10"})
        self.assertEqual(S.checked(["04:10", "19:30"]), {"part": "mixed", "hhmm": "19:30"})
        self.assertEqual(S.checked(["06:59"])["part"], "night")
        self.assertEqual(S.checked(["07:00"])["part"], "morning")
        self.assertEqual(S.checked(["12:00"])["part"], "day")
        self.assertEqual(S.checked(["18:00"])["part"], "evening")
        self.assertIsNone(S.checked([]))

    def test_first_stale(self):
        h = Hist()
        h.add(0, {"net_down": 700}, hh=1)
        self.assertTrue(h.view(at(0, 4))["summary"]["first"])
        self.assertFalse(h.view(at(1, 2))["summary"]["first"])
        self.assertTrue(h.view(at(47, 2))["summary"]["stale"])


class NewBase(unittest.TestCase):
    def test_inv_known_carries_the_last_known_value(self):
        """REV-ns-b6 З-2: the chain A(EPYC) → B(null) → C(Xeon) — the change is seen against the known A."""
        a, b, c = {"cpu": "AMD EPYC", "nproc": 2}, {"nproc": 2, "cpu": None}, {"cpu": "Intel Xeon", "nproc": 2}
        self.assertEqual(S.inv_changes(a, b), [])                            # unknown is not «changed»
        self.assertEqual(S.inv_changes(S.inv_known(a, b), c), ["cpu"])
        self.assertEqual(S.inv_known(a, b), a)
        self.assertEqual(S.inv_known(None, None), None)
        self.assertEqual(S.inv_known(S.inv_known(a, c), b), c)               # the newest known wins

    def test_inv_changes(self):
        a = {"cpu": "AMD EPYC 7763", "nproc": 2, "mem_gb": 2.0, "engines": ["awg", "vless"]}
        self.assertEqual(S.inv_changes(a, dict(a)), [])
        self.assertEqual(S.inv_changes(a, dict(a, cpu="Intel Xeon")), ["cpu"])
        self.assertEqual(S.inv_changes(a, dict(a, nproc=4)), ["cpu"])
        self.assertEqual(S.inv_changes(a, dict(a, mem_gb=2.1)), [])         # +5 %
        self.assertEqual(S.inv_changes(a, dict(a, mem_gb=2.3)), ["mem"])    # +15 %
        self.assertEqual(S.inv_changes(a, dict(a, engines=["vless", "awg"])), [])
        self.assertEqual(S.inv_changes(a, dict(a, engines=["awg"])), ["engines"])
        self.assertEqual(S.inv_changes(None, a), [])
        # B6 (REV-ns-b12-2 стык): unknown is not changed; ± 10 % exactly — no change either way (floats)
        self.assertEqual(S.inv_changes(a, dict(a, cpu=None)), [])
        self.assertEqual(S.inv_changes(a, dict(a, engines=None)), [])
        self.assertEqual(S.inv_changes(a, dict(a, nproc=None)), [])
        self.assertEqual(S.inv_changes(dict(a, cpu=None), a), [])
        for m0 in (1.0, 2.0, 4.0, 8.0, 7.8, 15.6, 62.8):
            for k in (1.1, 0.9):
                self.assertEqual(S.inv_changes(dict(a, mem_gb=m0), dict(a, mem_gb=m0 * k)), [], (m0, k))
            self.assertEqual(S.inv_changes(dict(a, mem_gb=m0), dict(a, mem_gb=m0 * 1.1001)), ["mem"], m0)
            self.assertEqual(S.inv_changes(dict(a, mem_gb=m0), dict(a, mem_gb=m0 * 0.8999)), ["mem"], m0)

    def test_cpu_change(self):
        """A change of the CPU model -> fresh and the base of the processor only after it (fresh_n 1, dev null); the
        channel is untouched; tariff -> all tests; engines -> nothing reset, the line is there."""
        h = Hist()
        for i in range(8):
            h.add(7 * i, {"net_down": 640, "cpu_1t": 1500, "steal_p95": 1, "_engines": ["awg"]})
        h.add(56, {"net_down": 640, "cpu_1t": 900, "steal_p95": 1, "_engines": ["awg"]})
        now = at(56, 12)
        ep = [{"ts": at(56, 4, 5), "kind": "cpu"}]
        fr = S.fresh(ABS, h.runs, now, day_of=day_of, epochs=ep)
        self.assertEqual((fr["meta"]["cpu_1t"]["fresh_n"], fr["meta"]["net_down"]["fresh_n"]), (1, 3))
        self.assertEqual(fr["meta"]["steal_p95"]["fresh_n"], 1)
        cpu = h.link(now, "cpu", epochs=ep)
        self.assertEqual((cpu["fresh_n"], cpu["dev"], cpu["rel"]), (1, None, None))
        self.assertEqual(h.link(now, "cpu")["rel"], 100)                     # without it: the median 1500 of 1500
        self.assertEqual(h.link(now, "down", epochs=ep)["fresh_n"], 3)
        fr = S.fresh(ABS, h.runs, now, day_of=day_of, epochs=[{"ts": at(56, 4, 5), "kind": "tariff"}])
        self.assertEqual(fr["meta"]["net_down"]["fresh_n"], 1)
        fr = S.fresh(ABS, h.runs, now, day_of=day_of, epochs=[{"ts": at(56, 4, 5), "kind": "engines"}])
        self.assertEqual(fr["meta"]["cpu_1t"]["fresh_n"], 3)
        v = h.view(now, epochs=[{"ts": at(56, 4, 5), "kind": "engines"}])
        self.assertEqual(v["summary"]["base_since"], [{"kind": "engines", "day": iso(56)}])
        since = S.epoch_since(ABS, [{"ts": at(56, 4, 5), "kind": "mem"}], day_of)
        self.assertEqual(sorted(since), ["mem_bw", "mem_gb"])
        self.assertEqual(since["mem_gb"], S._ord(iso(56)))
        self.assertEqual(DAY, 86400)


if __name__ == "__main__":
    unittest.main()
