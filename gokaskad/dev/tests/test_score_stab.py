"""C6 (k3/handoff/SPEC-node-score.md §6.3; RESEARCH-node-score.md 3.4, 3.5; NS-D7): the stability from the history of
A — the canary (``cpu_canary_cv``: CV of the 10-min samples of host_m10 with the node busy < 50 %, a day or window of
≥ 12 clock hours, in the total only after 24 h with it — before that «ждёт истории»; REV-ns-c6 З-1, З-2), the spread of
the channel (``net_cv``: ≥ 3 heavy tests of one mode, one per clock hour, no repeat, not under load, the budget not hit —
else «не измерено», «предварительно»; its «обычно» is the same value, С-1), steal p95 null in a container and when
hidden (p95 over 7 d < 0.2 % while the canary jumps > 10 %); ``score.view`` gives "stab"."""

import statistics
import unittest

from kaskad import score as S
from tests.score_hist import ABS, S4, Hist, at, iso

H = 3600


def hours(ms, busy=10.0, start=0):
    """[(ts, canary_ms, busy)] one per hour."""
    return [(start + i * H, x, busy) for i, x in enumerate(ms)]


def samples(ms, busy=10.0, start=0):
    """[(ts, canary_ms, busy)] one per 10 min (host_m10)."""
    return [(start + i * 600, x, busy) for i, x in enumerate(ms)]


def win(day, canary=None, steal=0.5, hrs=24, can_h=None, up_h=None):
    w = {"day": iso(day), "hours": hrs, "rtt": 30.0, "steal_p95": steal,
         "canary_h": (hrs if canary is not None else 0) if can_h is None else can_h}
    if up_h is not None:
        w["up_h"] = up_h
    if canary is not None:
        w["cpu_canary_cv"] = canary
    return w


class CanaryTest(unittest.TestCase):
    def test_cv_of_the_quiet_hours(self):
        xs = [50.0, 55.0] * 6
        cv, n = S.canary_cv(hours(xs))
        self.assertEqual(n, 12)
        self.assertAlmostEqual(cv, 100 * statistics.stdev(xs) / statistics.mean(xs))
        self.assertEqual(S.canary_cv(hours([50.0] * 11)), (None, 11))                 # < 12 hours — no value
        busy = hours([50.0] * 12) + hours([500.0], busy=50.0, start=12 * H)          # a busy hour does not count
        self.assertEqual(S.canary_cv(busy), (0.0, 12))
        junk = hours([50.0] * 12) + [(0, None, 1.0), (0, float("nan"), 1.0), (0, -5, 1.0), (0, 50.0, float("nan")),
                                     (float("nan"), 500.0, 1.0), (None, 500.0, 1.0)]
        self.assertEqual(S.canary_cv(junk), (0.0, 12))

    def test_the_spread_of_the_samples_not_of_hourly_means(self):
        """З-2 (A.1): the CV of every 10-min sample — hourly means would shrink it about √6 times."""
        xs = [40.0, 60.0, 50.0, 70.0, 30.0, 50.0] * 12                               # 12 hours, 6 samples each
        cv, n = S.canary_cv(samples(xs))
        self.assertEqual(n, 12)
        self.assertAlmostEqual(cv, 100 * statistics.stdev(xs) / statistics.mean(xs))
        self.assertGreater(cv, 25)                                                    # hourly means: CV 0
        self.assertEqual(S.canary_cv(samples([50.0] * 66)), (None, 11))               # 11 h of samples — no value
        self.assertEqual(S.canary_cv(samples([50.0] * 12)), (None, 2))                # 12 samples are 2 h, not 12
        self.assertEqual(S.canary_hours(samples([50.0] * 6) + samples([50.0], busy=90.0, start=H)), 2)  # busy too

    def test_fresh_and_waiting(self):
        F = S.fresh(ABS, [], at(10), probes=[win(9, 4.0), win(8, 6.0), win(7, 5.0)])["vals"]
        self.assertEqual(F["cpu_canary_cv"], {"v": 5.0})                              # the median of 3 windows
        F = S.fresh(ABS, [], at(10), probes=[win(9, hrs=10), win(8, hrs=10)])["vals"]
        self.assertEqual(F["cpu_canary_cv"], {"v": None, "wait": True, "why": "short_history"})
        F = S.fresh(ABS, [], at(10), probes=[win(9), win(8), win(7)])["vals"]       # 3 days, no canary at all
        self.assertEqual(F["cpu_canary_cv"], {"v": None, "why": "no_canary"})

    def test_the_canary_counts_after_a_day(self):
        """З-1 (A.1 «>= 24 h»): a window of 12 h has a value, the fresh value waits until 24 h with the canary."""
        F = S.fresh(ABS, [], at(10), probes=[win(9, 9.5, hrs=12)])["vals"]           # half a day of history
        self.assertEqual(F["cpu_canary_cv"], {"v": None, "wait": True, "why": "short_history"})
        F = S.fresh(ABS, [], at(10), probes=[win(9, 9.5, hrs=23)])["vals"]
        self.assertEqual(F["cpu_canary_cv"]["why"], "short_history")
        fr = S.fresh(ABS, [], at(10), probes=[win(9, 9.5, hrs=24)])
        self.assertEqual(fr["vals"]["cpu_canary_cv"], {"v": 9.5})
        fr = S.fresh(ABS, [], at(10), probes=[win(9, 9.5, hrs=14), win(8, can_h=10, hrs=10)])   # 14 + 10 h
        self.assertEqual(fr["vals"]["cpu_canary_cv"], {"v": 9.5})
        fr = S.fresh(ABS, [], at(10), probes=[win(9, 9.5, hrs=20, can_h=13)])        # the canary 13 of 20 h: young
        self.assertEqual(fr["vals"]["cpu_canary_cv"]["why"], "short_history")
        self.assertNotIn("cpu_canary_cv", fr["meta"])

    def test_the_canary_goes_by_its_own_hours(self):
        """REV-ns-c6-2 З-1: the canary runs whatever the probes do — probes 10 h a day (or off) and 72 h of the canary
        give its value; the reason without a value comes of the hours of the canary and of the node (``up_h``)."""
        ws = [win(9 - i, cv, hrs=10, can_h=24, up_h=24) for i, cv in enumerate((7.4, 7.0, 8.0))]
        fr = S.fresh(ABS, [], at(10), probes=ws)
        self.assertEqual(fr["vals"]["cpu_canary_cv"], {"v": 7.4})
        self.assertEqual(fr["meta"]["cpu_canary_cv"]["fresh_n"], 3)
        self.assertNotIn("rtt", fr["vals"])                                           # the probes of 10 h do not count
        ws = [win(9 - i, cv, hrs=0, can_h=24, up_h=24) for i, cv in enumerate((7.4, 7.0, 8.0))]   # probes off
        self.assertEqual(S.fresh(ABS, [], at(10), probes=ws)["vals"]["cpu_canary_cv"], {"v": 7.4})
        busy = [win(9 - i, hrs=0, can_h=0, up_h=24) for i in range(3)]                # off probes, the server busy
        self.assertEqual(S.fresh(ABS, [], at(10), probes=busy)["vals"]["cpu_canary_cv"], {"v": None, "why": "no_canary"})
        young = [win(9, hrs=0, can_h=10, up_h=10)]                                    # off probes, 10 h of the node
        self.assertEqual(S.fresh(ABS, [], at(10), probes=young)["vals"]["cpu_canary_cv"]["why"], "short_history")

    def test_the_days_of_the_canary_have_its_hours(self):
        """REV-ns-c6-2 З-1: with the probes off a day of the canary has the hours of its quiet samples (for «обычно»
        and ▲/▼), not the 0 h of the probes that would drop it; the windows carry ``up_h``."""
        from kaskad.bench import hostscore as HS
        cn = samples([50.0, 60.0] * 72 * 3, start=0) + [(3 * 86400 - 600, None, 90.0)]
        wins, days = HS.probe_inputs([], [], 3 * 86400, lambda ts: "2026-01-%02d" % (1 + int(ts) // 86400), canary=cn)
        self.assertEqual([d["hours"] for d in days["cpu_canary_cv"]], [24, 24, 24])
        self.assertEqual(len(S._probe_day_values(S.tests_of(ABS)["cpu_canary_cv"], days["cpu_canary_cv"])), 3)
        self.assertEqual([(w["hours"], w["canary_h"], w["up_h"]) for w in wins], [(0, 24, 24)] * 3)

    def test_a_young_history_waits(self):
        """REV-ns-c6-2 З-2 (N11): ≥ 24 h of the node, < 24 h of the canary that ran in at least every other hour —
        the history is young (wait), not «сервер был занят»."""
        F = S.fresh(ABS, [], at(10), probes=[win(9, hrs=24, can_h=18), win(8, hrs=6, can_h=2)])["vals"]
        self.assertEqual(F["cpu_canary_cv"], {"v": None, "wait": True, "why": "short_history"})
        F = S.fresh(ABS, [], at(10), probes=[win(9, hrs=0, can_h=18, up_h=24), win(8, hrs=0, can_h=2, up_h=6)])["vals"]
        self.assertEqual(F["cpu_canary_cv"]["why"], "short_history")                  # the same with the probes off
        F = S.fresh(ABS, [], at(10), probes=[win(9, hrs=24, can_h=10), win(8, hrs=24, can_h=10)])["vals"]
        self.assertEqual(F["cpu_canary_cv"]["why"], "no_canary")                      # 20 of 48 h — busy

    def test_why_without_a_canary_is_true(self):
        """П-1: no_canary only when the canary hardly ran (busy) or a window of ≥ 12 h of it had no quiet hours;
        three short windows (10 h each) — few_hours, not «сервер всё время был занят»."""
        F = S.fresh(ABS, [], at(10), probes=[win(9, hrs=10, can_h=10), win(8, hrs=10, can_h=10),
                                             win(7, hrs=10, can_h=10)])["vals"]
        self.assertEqual(F["cpu_canary_cv"], {"v": None, "why": "few_hours"})
        F = S.fresh(ABS, [], at(10), probes=[win(9, can_h=5), win(8, can_h=5), win(7, can_h=5)])["vals"]
        self.assertEqual(F["cpu_canary_cv"], {"v": None, "why": "no_canary"})        # 15 of 72 h — the server busy
        F = S.fresh(ABS, [], at(10), probes=[win(9, can_h=20), win(8, can_h=4)])["vals"]   # 24 h, none quiet
        self.assertEqual(F["cpu_canary_cv"], {"v": None, "why": "no_canary"})
        for why in ("few_hours", "no_canary", "short_history"):
            self.assertIn(why, S.STAB_WHY)
        st = S.stab_view(ABS, {"cpu_canary_cv": {"v": None, "wait": True, "why": "short_history"}})
        self.assertEqual(st["cpu_canary_cv"], {"v": None, "n": None, "why": "short_history"})   # the reason is shown
        self.assertNotIn("cpu_canary_cv", S.fresh(ABS, [], at(10))["vals"])          # no probes — nothing said

    def test_canary_days_give_the_mark_of_the_link(self):
        h = Hist()
        days = [{"day": iso(1 + i), "v": x, "hours": 24} for i, x in enumerate([3, 3, 3.2, 2.9, 3, 3.1, 3, 30, 32])]
        v = h.view(at(10, 0) - 1, probes=[win(9, 32), win(8, 30)], probe_days={"cpu_canary_cv": days})
        lk = next(x for x in v["links"] if x["id"] == "stab")
        self.assertEqual((lk["dev"], lk["source"]), ("down", "probes"))


class NetCvTest(unittest.TestCase):
    def runs(self, vals, mode="full", hh=4):
        h = Hist()
        for d, x in enumerate(vals):
            h.add(d, {"net_down": x}, mode=mode, hh=hh)
        return h

    def test_three_tests_of_one_mode(self):
        h = self.runs([300, 400, 500])
        F = S.fresh(ABS, h.runs, at(3))["vals"]
        self.assertAlmostEqual(F["net_cv"]["v"], 25.0)
        self.assertEqual((F["net_cv"]["n"], F["net_cv"]["of_mode"]), (3, "full"))
        F = S.fresh(ABS, h.runs[:2], at(3))["vals"]
        self.assertEqual(F["net_cv"], {"v": None, "why": "few_tests", "n": 2})      # «не измерено», RS 3.5 — no wait

    def test_what_does_not_count(self):
        h = self.runs([300, 400])
        h.add(2, {"net_down": 500}, hh=6, retry=True)                                 # an automatic repeat
        h.add(3, {"net_down": {"v": 900, "budget": True}})                            # the budget hit
        h.add(4, {"net_down": {"v": 100, "corr": 450}}, under_load=True)              # under load
        h.add(5, {"net_down": 450}, status="failed")
        h.add(6, {"net_down": 450}, mode="quick")                                     # another mode
        F = S.fresh(ABS, h.runs, at(7))["vals"]
        self.assertEqual(F["net_cv"]["n"], 2)
        h.add(6, {"net_down": 450}, hh=4, mm=30)                                      # the same clock hour: the last
        h.add(6, {"net_down": 500}, hh=4, mm=40)
        F = S.fresh(ABS, h.runs, at(7))["vals"]
        self.assertEqual(F["net_cv"]["n"], 3)
        self.assertAlmostEqual(F["net_cv"]["v"], 100 * statistics.stdev([300, 400, 500]) / 400)
        F = S.fresh(ABS, h.runs, at(6) + 46 * 86400)["vals"]                          # older than 45 d
        self.assertIsNone(F["net_cv"]["v"])

    def test_the_mode_with_more_tests_and_the_last_ten(self):
        h = self.runs([300, 400, 500, 600], mode="quick")
        h.add(5, {"net_down": 100})
        h.add(6, {"net_down": 1000})
        F = S.fresh(ABS, h.runs, at(7))["vals"]
        self.assertEqual((F["net_cv"]["of_mode"], F["net_cv"]["n"]), ("quick", 4))
        h = self.runs([100] * 5 + [400, 400, 400, 400, 400, 400, 400, 400, 400, 400])
        self.assertEqual(S.fresh(ABS, h.runs, at(16))["vals"]["net_cv"]["v"], 0.0)   # only the last 10

    def test_a_tie_of_the_modes_takes_the_full_one(self):
        """П-5 (M8): as many quick tests as full ones — the spread of the full ones."""
        h = self.runs([300, 400, 500], mode="quick")
        for d, x in enumerate([400, 400, 400]):
            h.add(3 + d, {"net_down": x}, hh=6)
        F = S.fresh(ABS, h.runs, at(7))["vals"]
        self.assertEqual((F["net_cv"]["of_mode"], F["net_cv"]["n"], F["net_cv"]["v"]), ("full", 3, 0.0))
        h.add(6, {"net_down": 450}, mode="quick", hh=9)                               # one quick more — quick
        self.assertEqual(S.fresh(ABS, h.runs, at(7))["vals"]["net_cv"]["of_mode"], "quick")

    def test_now_and_usual_agree_on_a_steady_node(self):
        """С-1 after NS-D30: a steady node is «как обычно» — 12 equal checks give rel 100 of the link «Стабильность»
        and of the total; a spread of the channel of 10 % — too. net_cv is the spread of the last checks itself: it has
        no series of its own, so no «обычно», no rel and no part of the link (the group goes by its other tests; its
        value is in ``stab``)."""
        for downs in ((1700,) * 12, (1500, 1700, 1900) * 4):
            h = Hist()
            for d in range(12):
                h.add(d, dict(S4, net_down=downs[d]), hh=4 + d % 5)
            v = h.view(at(12, 12))
            self.assertIsNotNone(v["stab"]["net_cv"]["v"], downs)
            self.assertEqual(v["summary"]["rel_total"], 100, downs)
            lk = next(x for x in v["links"] if x["id"] == "stab")
            self.assertEqual(lk["rel"], 100, downs)
            parts = dict((p["test"], p) for p in lk["parts"])
            self.assertNotIn("net_cv", parts, downs)      # no «обычно» of its own — not a part of rel (no «0 из 3»)
            self.assertEqual(parts["cpu_cv"]["rel"], 100, downs)                  # the rest is steady too
        self.assertAlmostEqual(v["stab"]["net_cv"]["v"], 100 * statistics.stdev(downs[-10:]) / statistics.mean(downs[-10:]),
                               2)                                                     # the last 10: CV ≈ 10 %

    def test_the_new_base(self):
        h = self.runs([300, 400, 500, 600])
        F = S.fresh(ABS, h.runs, at(5), epochs=[{"ts": at(2, 1), "kind": "cpu"}])["vals"]   # cpu -> stab (NS-S17)
        self.assertEqual(F["net_cv"]["n"], 2)
        F = S.fresh(ABS, h.runs, at(5), epochs=[{"ts": at(2, 1), "kind": "mem"}])["vals"]
        self.assertEqual(F["net_cv"]["n"], 4)

    def test_a_value_of_a_check_is_never_net_cv(self):
        h = self.runs([300, 400])
        h.runs[-1]["vals"]["net_cv"] = 5.0                                            # the history decides
        self.assertEqual(S.fresh(ABS, h.runs, at(3))["vals"]["net_cv"]["why"], "few_tests")


class StealTest(unittest.TestCase):
    def F(self, runs=(), steal_7d=None, canary=5.0):
        return S.fresh(ABS, list(runs), at(10), probes=[win(9, canary), win(8, canary), win(7, canary)],
                       steal_7d=steal_7d)

    def test_container(self):
        h = Hist()
        h.add(1, {"cpu_1t": 1000})
        self.assertEqual(self.F(h.runs)["vals"]["steal_p95"], {"v": 0.5})
        for virt in ("lxc", "openvz", "docker"):
            h.runs[-1]["inv"] = {"virt": virt}
            fr = self.F(h.runs)
            self.assertEqual(fr["vals"]["steal_p95"], {"v": None, "why": "container"}, virt)
            self.assertNotIn("steal_p95", fr["meta"])
        h.runs[-1]["inv"] = {"virt": "kvm"}
        self.assertEqual(self.F(h.runs)["vals"]["steal_p95"], {"v": 0.5})

    def test_hidden(self):
        self.assertEqual(self.F(steal_7d=0.1, canary=12.0)["vals"]["steal_p95"], {"v": None, "why": "hidden"})
        fr = S.fresh(ABS, [], at(10), probes=[win(9, 40.0, hrs=12)], steal_7d=0.05)   # С-2: 12 h — the canary waits
        self.assertEqual(fr["vals"]["steal_p95"], {"v": 0.5})
        self.assertEqual(self.F(steal_7d=0.2, canary=12.0)["vals"]["steal_p95"], {"v": 0.5})   # bounds: 0.2 …
        self.assertEqual(self.F(steal_7d=0.1, canary=10.0)["vals"]["steal_p95"], {"v": 0.5})   # … and 10 %
        self.assertEqual(self.F(steal_7d=None, canary=40.0)["vals"]["steal_p95"], {"v": 0.5})  # no rows of 7 d
        self.assertEqual(self.F(steal_7d=float("nan"), canary=40.0)["vals"]["steal_p95"], {"v": 0.5})

    def test_view_hides_the_usual_of_a_hidden_steal(self):
        h = Hist()
        days = [{"day": iso(i), "v": x, "hours": 24} for i, x in enumerate([0.1] * 7 + [5, 6])]
        kw = dict(probes=[win(9, 12.0, 6), win(8, 12.0, 5), win(7, 12.0, 0.1)], probe_days={"steal_p95": days})
        v = h.view(at(10, 0) - 1, steal_7d=3.0, **kw)
        self.assertEqual(v["stab"]["steal_p95"]["v"], 5.0)
        self.assertEqual(next(x for x in v["links"] if x["id"] == "stab")["dev"], "down")
        v = h.view(at(10, 0) - 1, steal_7d=0.1, **kw)
        self.assertEqual(v["stab"]["steal_p95"], {"v": None, "n": None, "why": "hidden"})
        lk = next(x for x in v["links"] if x["id"] == "stab")
        self.assertIsNone(lk["dev"])                                                  # no «обычно» of it either

    def test_stab_view(self):
        h = Hist()
        for d, x in enumerate([300, 400, 500]):
            h.add(d, {"net_down": x, "cpu_cv": 4.0})
        v = h.view(at(10), probes=[win(9, 5.0), win(8, 5.0), win(7, 5.0)])
        st = v["stab"]
        self.assertEqual(sorted(st), sorted(t["id"] for g in ABS["groups"] if g["id"] == "stab" for t in g["tests"]))
        self.assertEqual(st["net_cv"], {"v": 25.0, "n": 3, "why": None})
        self.assertEqual(st["cpu_canary_cv"], {"v": 5.0, "n": None, "why": None})
        self.assertEqual(st["cpu_sustain"], {"v": None, "n": None, "why": "not_measured"})
        self.assertEqual(st["cpu_cv"]["v"], 4.0)


if __name__ == "__main__":
    unittest.main()
