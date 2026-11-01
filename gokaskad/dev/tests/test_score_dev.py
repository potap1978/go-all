"""B4, ▲/▼ of a link (SPEC-node-score-schedule-share.md NS-D25 «Тесты», with the clarification of the coordinator
to п. 3 of 18.10): by days, raw values, base before the window of fresh, the gap with the Student quantile, «ждёт
подтверждения», the automatic repeat inside its day, thinning, the channel by mode, loss at the lower bound, groups.
The numbers of the ТЗ by hand; the cases and random inputs of dev_calc.py (tests/fixtures/score_dev_cases.json, made
by k3/handoff/data/node-score-samples/make_dev_fixture_b4.py); properties with a fixed seed."""

import json
import math
import os
import random
import statistics
import unittest

from kaskad import score as S
from tests.score_hist import S4, T, Hist, at, day_of, iso

HERE = os.path.dirname(os.path.abspath(__file__))
DEV = json.load(open(os.path.join(HERE, "fixtures", "score_dev_cases.json"), encoding="utf-8"))
B = [600, 620, 640, 660, 700]
RULE = dict(S.DEV_RULE)
LN = math.log
DOWN = T["net_down"]


def days_of(xs, start=0, step=7, t=DOWN):
    return [(start + i * step, S._u(t, x), None) for i, x in enumerate(xs)]


def core(base, pair, rule=RULE, m=1, t=DOWN):
    """dev_core on daily values: base, a day in the window (not compared, not in base), the pair."""
    days = days_of(list(base) + [640] + list(pair), t=t)
    first = days[-3][0]
    return S.dev_core(t, days, upper=first, lower=first - 60, rule=rule, m=m, base_max=10)


def pair_view(base, pair, filler=640, tid="net_down", lid="down", **kw):
    h = Hist()
    d = h.series(tid, list(base) + ([filler] if filler is not None else []) + list(pair))
    return h.link(at(d - 7, 12), lid, **kw)


class Numbers(unittest.TestCase):
    def test_gap_by_hand(self):
        r = core(B, [540, 560])
        self.assertEqual(r["n"], 5)
        self.assertAlmostEqual(r["c"], LN(640), places=12)
        self.assertAlmostEqual(r["s"], 0.0592, places=4)
        self.assertAlmostEqual(LN(1.1), 0.0953, places=4)
        self.assertAlmostEqual(r["gap"], 0.1484, places=4)
        self.assertAlmostEqual(r["gap1"], 0.1884, places=3)       # 0.18850: the ТЗ cut the 4th digit
        self.assertAlmostEqual(r["d"], -0.152, places=3)
        self.assertEqual(r["dev"], "down")
        self.assertAlmostEqual(r["usual_raw"], 640.0, places=6)

    def test_pairs(self):
        for pair, dev, pend in (([540, 560], "down", None), ([560, 570], None, None), ([420, 660], None, None),
                                ([420, 635], None, None),                 # круг 2 давал ▼: 635 ниже лишь на 0,8 %
                                ([740, 760], "up", None), ([720, 740], None, None),
                                ([640, 420], None, "down"), ([640, 560], None, None)):
            r = core(B, pair)
            self.assertEqual((r["dev"], r["pending"]), (dev, pend), pair)
            row = pair_view(B, pair)
            self.assertEqual((row["dev"], row["dev_pending"]), (dev, pend), pair)
            self.assertEqual(row["dev_pending_kind"], "day" if pend else None)
            self.assertEqual(row["usual_n"], 5)
            self.assertAlmostEqual(row["usual_raw"], 640.0, places=6)
            self.assertEqual(row["rel"], S.rel_of(DOWN, sorted([640] + pair)[1], row["usual_raw"]))
            self.assertIsNone(row["p25_raw"])             # < 8 points
        self.assertAlmostEqual(core(B, [740, 760])["d"], 0.159, places=3)

    def test_rule_settings(self):
        r30 = core(B, [540, 560], {"pct": 30, "use_spread": True})
        self.assertAlmostEqual(r30["gap"], 0.2624, places=4)
        self.assertIsNone(r30["dev"])
        flat = core(B, [560, 570], {"pct": 10, "use_spread": False})
        self.assertAlmostEqual(flat["gap"], 0.0953, places=4)
        self.assertEqual(flat["dev"], "down")
        self.assertEqual(pair_view(B, [560, 570], rule={"pct": 10, "use_spread": False})["dev"], "down")
        self.assertIsNone(pair_view(B, [560, 570], rule={"pct": 10, "use_spread": True})["dev"])
        for ok in (5, 30):
            self.assertEqual(S.valid_dev_rule({"pct": ok, "use_spread": True})["pct"], ok)
        for bad in ({"pct": 4}, {"pct": 31}, {"pct": 10.5}, {"pct": True}, {"use_spread": "on"}, {"x": 1}, []):
            with self.assertRaises(ValueError):
                S.valid_dev_rule(bad)
        h = Hist()
        d = h.series("net_down", B + [640, 540, 560])
        self.assertEqual(h.view(at(d - 7, 12))["dev_rule"], {"pct": 10, "use_spread": True})
        self.assertEqual(h.view(at(d - 7, 12), rule={"pct": 30, "use_spread": True})["dev_rule"]["pct"], 30)

    def test_little_history(self):
        r = core(B[:3], [540, 560])
        self.assertEqual(r["n"], 3)
        self.assertIsNone(r["dev"])
        row = pair_view(B[:3], [400, 410])
        self.assertIsNone(row["dev"])                     # ▲/▼ from 4 base days (NS-D25) …
        self.assertEqual(row["usual_n"], 3)
        self.assertAlmostEqual(row["usual_raw"], 620.0, places=6)     # … rel from 3 (NS-D30): 410 of 620
        self.assertEqual(row["rel"], 66)
        row = pair_view(B[:2], [400, 410])
        self.assertEqual((row["usual_n"], row["usual_raw"], row["rel"]), (2, None, None))

    def test_window_not_in_base(self):
        """The 3 last values (their days) are not in the base; the base is 60 d before the first day of the window."""
        r = core(B, [540, 560])
        self.assertEqual(r["n"], 5)                       # the day of 640 between base and pair is not in it
        days = days_of([600] + B + [640, 540, 560], start=0, step=10)
        first = days[-3][0]
        got = S.dev_core(DOWN, days, upper=first, lower=first - 60, rule=RULE, base_max=10)
        self.assertEqual(got["n"], 6)                     # day 0 — 60 d before the first day of the window: taken
        self.assertEqual(first - days[1][0], 50)
        days2 = [(first - 61, S._u(DOWN, 600), None)] + days[1:]
        got2 = S.dev_core(DOWN, days2, upper=first, lower=first - 60, rule=RULE, base_max=10)
        self.assertEqual(got2["n"], 5)                    # 61 d before — not taken
        days3 = [(first - 60, S._u(DOWN, 600), None)] + days[1:]
        self.assertEqual(S.dev_core(DOWN, days3, upper=first, lower=first - 60, rule=RULE, base_max=10)["n"], 6)
        many = days_of([640] * 14 + [640, 540, 560], step=3)
        f = many[-3][0]
        self.assertEqual(S.dev_core(DOWN, many, upper=f, lower=f - 60, rule=RULE, base_max=10)["n"], 10)

    def test_window_by_view(self):
        h = Hist()
        h.add(0, {"net_down": 600})                       # 61 d before the first day of the window
        for i, x in enumerate(B):
            h.add(14 + 7 * i, {"net_down": x})
        for i, x in enumerate([640, 540, 560]):
            h.add(61 + 7 * i, {"net_down": x})
        row = h.link(at(76, 12), "down")
        self.assertEqual(row["usual_n"], 5)
        self.assertEqual(row["dev"], "down")

    def test_new_base(self):
        """«Новая база» (NS-S17): tariff resets all tests — the base is only after the day of the event."""
        h = Hist()
        d = h.series("net_down", B + [640, 540, 560])
        self.assertEqual(h.link(at(d - 7, 12), "down")["dev"], "down")
        row = h.link(at(d - 7, 12), "down", epochs=[{"ts": at(d - 22, 10), "kind": "tariff"}])
        self.assertIsNone(row["dev"])
        self.assertEqual((row["usual_raw"], row["rel"]), (None, None))
        self.assertEqual(h.view(at(d - 7, 12),
                                epochs=[{"ts": at(d - 22, 10), "kind": "tariff"}])["summary"]["base_since"],
                         [{"kind": "tariff", "day": iso(d - 22)}])
        # cpu resets the cpu and stab groups, not the channel
        self.assertEqual(h.link(at(d - 7, 12), "down", epochs=[{"ts": at(d - 22, 10), "kind": "cpu"}])["dev"], "down")


class Repeat(unittest.TestCase):
    """The automatic repeat inside its day (упрощение ▲/▼ 18.10 + уточнение к п. 3)."""

    def hist(self, base=B, tid="net_down", vals=None):
        h = Hist()
        for i, x in enumerate(base):
            h.add(7 * i, dict(vals or {}, **{tid: x}))
        return h, 7 * len(base)

    def test_refuted(self):
        h, d = self.hist()
        h.add(d, {"net_down": 640})
        h.add(d + 7, {"net_down": 50})
        h.add(d + 7, {"net_down": 575}, hh=6, retry=True)
        row = h.link(at(d + 7, 12), "down")
        self.assertEqual((row["dev"], row["dev_pending"], row["dev_pending_kind"]), (None, None, None))
        self.assertEqual(h.events(), [])
        pts = S._points(h.runs, "net_down", at(d + 7, 12), day_of)
        dv = S._day_values(DOWN, pts)
        self.assertAlmostEqual(math.exp(dv[-1][1]), 575.0)

    def test_confirmed_then_next_week(self):
        h, d = self.hist()
        h.add(d, {"net_down": 640})
        h.add(d + 7, {"net_down": 50})
        h.add(d + 7, {"net_down": 40}, hh=6, retry=True)
        row = h.link(at(d + 7, 12), "down")
        self.assertIsNone(row["dev"])
        self.assertEqual((row["dev_pending"], row["dev_pending_kind"]), ("down", "retry_confirmed"))
        ev = [e for e in h.events() if e["link"] == "down"]      # net_bw (the group of the download alone) — its own
        self.assertEqual([(e["link"], e["cond"], e["day"], e["auto"]) for e in ev], [("down", "rel", iso(d + 7), True)])
        h2 = Hist()
        h2.runs = list(h.runs)
        h.add(d + 14, {"net_down": 45})
        self.assertEqual(h.link(at(d + 14, 12), "down")["dev"], "down")
        h2.add(d + 14, {"net_down": 640})
        row = h2.link(at(d + 14, 12), "down")
        self.assertEqual((row["dev"], row["dev_pending_kind"]), (None, None))

    def test_refuted_but_both_days_below(self):
        """560, then 50 and repeat 540 -> ▼ by the days (560 and 540), as 540/560 — not by the repeat."""
        h, d = self.hist()
        h.add(d, {"net_down": 560})
        h.add(d + 7, {"net_down": 50})
        h.add(d + 7, {"net_down": 540}, hh=6, retry=True)
        row = h.link(at(d + 7, 12), "down")
        self.assertEqual(row["dev"], "down")
        self.assertEqual(h.events(), [])

    def test_s4(self):
        """S4 (upload): base 1250…1350, day 1300, then 5 and repeat 5 -> one bench_drop (rel 0 <= 50), no ▼,
        retry_confirmed; next week 5 -> ▼; waiting -> retry_wait; repeat 1150 -> nothing (the same 1150 as an
        ordinary check — day); repeat 1300 -> nothing."""
        base = [1250, 1280, 1300, 1320, 1350]
        b = dict(S4)

        def make(*tail):
            h, d = self.hist(base, "net_up", b)
            h.add(d, dict(b, net_up=1300))
            for i, (x, auto) in enumerate(tail):
                h.add(d + 7, dict(b, net_up=x), hh=4 + 2 * i, retry=auto)
            return h, d
        h, d = make((5, False), (5, True))
        row = h.link(at(d + 7, 12), "up")
        self.assertIsNone(row["dev"])
        self.assertEqual((row["dev_pending"], row["dev_pending_kind"]), ("down", "retry_confirmed"))
        ev = [e for e in h.events() if e["link"] == "up"]
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["cond"], ev[0]["push"], ev[0]["auto"], ev[0]["value"], ev[0]["unit"], ev[0]["now"]),
                         ("rel", True, True, 5, "Мбит/с", 0))
        self.assertEqual(row["rel"], 0)                   # 5 of 1300
        h.add(d + 14, dict(b, net_up=5))
        self.assertEqual(h.link(at(d + 14, 12), "up")["dev"], "down")
        h, d = make((5, False))
        row = h.link(at(d + 7, 12), "up", retry={"ts": at(d + 7, 15, 40), "links": ["up"]})
        self.assertEqual((row["dev_pending"], row["dev_pending_kind"]), ("down", "retry_wait"))
        self.assertEqual(h.view(at(d + 7, 12), retry={"ts": at(d + 7, 15, 40), "links": ["up"]})["summary"]["retry"],
                         {"at": "15:40", "links": ["up"], "link": "up", "test": "net_up", "value": 5.0, "unit": "Мбит/с"})
        h, d = make((5, False), (1150, True))
        row = h.link(at(d + 7, 12), "up")
        self.assertEqual((row["dev"], row["dev_pending"], row["dev_pending_kind"]), (None, None, None))
        self.assertEqual([e for e in h.events() if e["link"] == "up"], [])
        h, d = make((1150, False))
        row = h.link(at(d + 7, 12), "up")
        self.assertEqual((row["dev_pending"], row["dev_pending_kind"]), ("down", "day"))
        h, d = make((5, False), (1300, True))
        row = h.link(at(d + 7, 12), "up")
        self.assertEqual((row["dev"], row["dev_pending"], row["dev_pending_kind"]), (None, None, None))
        self.assertEqual((row["fresh_raw"], row["rel"]), (1300, 100))

    def test_no_base(self):
        """A young node (NS-D33): 700, then 50 and its repeat 40 — without «обычно» there is no sharp drop, only a
        failed test: no repeat is asked, no bench_drop, no ▼, nothing pending (the repeat confirmed nothing)."""
        h = Hist()
        h.add(0, {"net_down": 700})
        h.add(7, {"net_down": 50})
        run = h.runs.pop()
        self.assertEqual(S.retry_wanted(S.load_spec(), h.runs, run, day_of=day_of), [])
        h.runs.append(run)
        h.add(7, {"net_down": 40}, hh=6, retry=True)
        row = h.link(at(7, 12), "down")
        self.assertEqual((row["usual_raw"], row["rel"], row["fresh_raw"]), (None, None, 50))
        self.assertEqual((row["dev"], row["dev_pending"], row["dev_pending_kind"]), (None, None, None))
        self.assertEqual(h.events(), [])

    def test_after_midnight(self):
        """The original 23:30 (50), its repeat 01:10 (575) -> the pair in the day of the original (575)."""
        h, d = self.hist()
        h.add(d, {"net_down": 640})
        h.add(d + 7, {"net_down": 50}, hh=23, mm=30)
        h.add(d + 8, {"net_down": 575}, hh=1, mm=10, retry=True)
        dv = S._day_values(DOWN, S._points(h.runs, "net_down", at(d + 8, 12), day_of))
        self.assertEqual(dv[-1][0], S._ord(iso(d + 7)))
        self.assertAlmostEqual(math.exp(dv[-1][1]), 575.0)
        h2, d = self.hist()
        h2.add(d, {"net_down": 640})
        h2.add(d + 7, {"net_down": 50}, hh=23, mm=30)
        h2.add(d + 8, {"net_down": 40}, hh=1, mm=10, retry=True)
        self.assertEqual([e["day"] for e in h2.events() if e["link"] == "down"], [iso(d + 7)])

    def test_two_repeats_a_day(self):
        h, d = self.hist()
        h.add(d, {"net_down": 640})
        h.add(d + 7, {"net_down": 50}, hh=4)
        h.add(d + 7, {"net_down": 575}, hh=6, retry=True)
        a = h.add(d + 7, {"net_down": 40}, hh=12, by="panel")
        h.add(d + 7, {"net_down": 600}, hh=14, retry_of=a)
        dv = S._day_values(DOWN, S._points(h.runs, "net_down", at(d + 7, 20), day_of))
        self.assertEqual(round(math.exp(dv[-1][1])), 587)

    def test_two_values_not_repeat(self):
        """Not from a repeat: the mean of u (420 and 635 -> 516, pending ▼; 600 and 560 -> 580, nothing)."""
        for vals, val, pend in (((420, 635), 516, "down"), ((600, 560), 580, None)):
            h, d = self.hist()
            h.add(d, {"net_down": 640})
            h.add(d + 7, {"net_down": vals[0]}, hh=4)
            h.add(d + 7, {"net_down": vals[1]}, hh=12, by="panel")
            dv = S._day_values(DOWN, S._points(h.runs, "net_down", at(d + 7, 20), day_of))
            self.assertEqual(round(math.exp(dv[-1][1])), val)
            row = h.link(at(d + 7, 20), "down")
            self.assertEqual((row["dev"], row["dev_pending"]), (None, pend))

    def test_dev_calc_scenarios(self):
        """scen() of dev_calc.py on the cases of the ТЗ — the same mark, hint, kind and day values."""
        for c in DEV["scen"]:
            h = Hist()
            for i, x in enumerate(c["base"]):
                h.add(7 * i, {"net_down": x})
            d = 7 * len(c["base"])
            if sum(len(x) for x in c["days"]) < 3:  # the third value of the window: not compared, not in base
                h.add(d, {"net_down": 640})
                d += 7
            auto = {int(k): v for k, v in c["auto"].items()}
            retry = None
            for j, day in enumerate(c["days"]):
                for i, x in enumerate(day):
                    rep = auto.get(j) in ("confirmed", "refuted") and i == len(day) - 1 and len(day) == 2
                    h.add(d, {"net_down": x}, hh=4 + 2 * i, retry=rep, by="schedule" if i == 0 else "panel")
                if auto.get(j) == "wait":
                    retry = {"ts": at(d, 15, 40), "links": ["down"]}
                d += 7
            row = h.link(at(d - 7, 20), "down", retry=retry)
            dev = {-1: "down", 0: None, 1: "up"}
            self.assertEqual(row["dev"], dev[c["mark"]], c)
            if not c["mark"]:
                self.assertEqual((row["dev_pending"], row["dev_pending_kind"]), (dev[c["pending"]], c["kind"]), c)

    def test_dev_calc_random(self):
        """mark() and pending() of dev_calc.py on 3000 random bases and pairs — the same."""
        for c in DEV["random"]:
            days = days_of(c["base"] + [1.0] + c["last2"])
            f = days[-3][0]
            r = S.dev_core(DOWN, days, upper=f, lower=f - 1000, rule={"pct": c["pct"], "use_spread": True}, m=c["m"])
            self.assertEqual(r["dev"], {-1: "down", 0: None, 1: "up"}[c["mark"]], c)
            if not c["mark"]:
                self.assertEqual(r["pending"], {-1: "down", 0: None, 1: "up"}[c["pending"]], c)


class Kinds(unittest.TestCase):
    def test_lower_is_better(self):
        """Response (probes): base 19, 20, 20, 21, 22 ms, two days of 30 ms -> ▼."""
        h = Hist()
        h.add(0, dict(S4))
        rtt = [19, 20, 20, 21, 22, 20, 30, 30]
        start = S._ord(iso(0)) + 1
        days = [{"day": S._iso(start + i), "v": x, "hours": 24} for i, x in enumerate(rtt)]
        row = h.link(at(len(rtt) + 1, 1), "net_q", probes=[], probe_days={"rtt": days})
        self.assertEqual((row["dev"], row["dev_test"], row["source"]), ("down", "rtt", "probes"))
        r = S._probe_dev(T["rtt"], days, RULE, 1, None)
        self.assertEqual(r["n"], 5)

    def test_thinning(self):
        self.assertEqual([d[0] for d in S.take([(i, 0, None) for i in range(10)], 2)], [1, 3, 5, 7, 9])
        self.assertEqual([d[0] for d in S.take([(0, 0, None), (1, 0, None), (5, 0, None), (6, 0, None)], 2)], [1, 6])
        h = Hist()
        d = h.series("net_down", [600, 620, 640, 660, 700, 680, 690, 640, 540, 560], step=1)
        S.DEV_SPACING_DAYS, keep = 2, S.DEV_SPACING_DAYS
        try:
            r = h.link(at(d - 1, 12), "down")
        finally:
            S.DEV_SPACING_DAYS = keep
        self.assertEqual(r["usual_n"], 3)            # base days 1, 3, 5 (the window starts on day 7)
        r = h.link(at(d - 1, 12), "down", k=2)
        self.assertEqual(r["usual_n"], 3)

    def test_probes_spacing(self):
        """k_p 2: the pair — the last full day and the day 2 before; k_p 7 -> the base window 56 d, 7 points."""
        t = T["rtt"]
        days = [{"day": S._iso(1000 + i), "v": 20.0, "hours": 24} for i in range(80)]
        r2 = S._probe_dev(t, days, RULE, 1, None, k=2)
        self.assertEqual([x[0] for x in r2["last"]], [1077, 1079])
        r7 = S._probe_dev(t, days, RULE, 1, None, k=7)
        self.assertEqual(r7["n"], 7)
        r1 = S._probe_dev(t, days, RULE, 1, None, k=1)
        self.assertEqual(r1["n"], 30)
        r4 = S._probe_dev(t, days, RULE, 1, None, k=4)
        self.assertEqual(r4["n"], 7)

    def test_channel_by_mode(self):
        """Quick ≈ 1000 eight days and 2 full 1900 -> dev by the quick series, «· быстрые проверки»."""
        h = Hist()
        for i, x in enumerate([1000, 990, 1010, 1005, 995, 1000, 1002, 998, 1000, 1000, 1000]):
            h.add(7 * i, {"net_down": {"v": x, "budget": True}}, mode="quick")   # 250 MB hit: a lower bound
        h.add(71, {"net_down": 1900}, hh=6, mode="full")
        h.add(72, {"net_down": 1900}, hh=6, mode="full")
        row = h.link(at(72, 12), "down")
        self.assertEqual(row["fresh_raw"], 1900)          # fresh — the full value (quick does not contradict)
        self.assertEqual((row["usual_mode"], row["usual_n"], row["dev"]), ("quick", 8, None))
        self.assertAlmostEqual(row["usual_raw"], 1000, places=6)
        h = Hist()
        for i, x in enumerate([1000, 990, 1010, 1005, 995, 1000, 1002, 998, 1000]):
            h.add(7 * i, {"net_down": x}, mode="quick")
        h.add(60, {"net_down": 1900}, hh=6, mode="full")
        h.add(61, {"net_down": 1900}, hh=6, mode="full")
        h.add(63, {"net_down": 600}, mode="quick")
        h.add(70, {"net_down": 620}, mode="quick")
        row = h.link(at(70, 12), "down")
        self.assertEqual(row["usual_mode"], "quick")
        self.assertEqual(row["dev"], "down")
        self.assertEqual(row["usual_n"], 8)
        self.assertIsNotNone(row["p25_raw"])

    def test_loss_lower_bound(self):
        """Base 10 days without loss at n ≈ 26 000 (bound 0.005 %), two days without loss at n ≈ 17 000 (0.0059 %)
        -> nothing; two days with lo 0.05 % -> ▼."""
        t = T["loss"]
        base = [{"day": S._iso(2000 + i), "v": 0.0, "lo": 0.0, "n": 25920, "hours": 24} for i in range(11)]
        quiet = [{"day": S._iso(2011 + i), "v": 0.0, "lo": 0.0, "n": 17000, "hours": 24} for i in range(2)]
        r = S._probe_dev(t, base + quiet, RULE, 1, None)
        self.assertEqual((r["n"], r["dev"], r["pending"]), (10, None, None))
        self.assertEqual(r["d"], 0)
        bad = [{"day": S._iso(2011 + i), "v": 0.06, "lo": 0.05, "n": 25920, "hours": 24} for i in range(2)]
        self.assertEqual(S._probe_dev(t, base + bad, RULE, 1, None)["dev"], "down")

    def test_group(self):
        """cpu group, 4 tests with a base: m = 4 (K 4.315, gap 0.2306): 540/560 of one test -> nothing; 450/470 -> ▼,
        dev_test — that test; a ▲ of another test with a larger |d| does not become dev_test."""
        r = core(B, [540, 560], m=4)
        self.assertAlmostEqual(r["gap"], 0.2306, places=4)
        self.assertIsNone(r["dev"])
        self.assertEqual(core(B, [450, 470], m=4)["dev"], "down")

        def run(h, day, one, other=640, up=640):
            h.add(day, {"cpu_1t": one, "cpu_mt": other, "chacha_1t": up, "aes_1t": other, "_engines": ["awg"]})
        for pair, dev in (((540, 560), None), ((450, 470), "down")):
            h = Hist()
            for i, x in enumerate(B + [640]):
                run(h, 7 * i, x, x, x)
            run(h, 42, pair[0], 640, 640)
            run(h, 49, pair[1], 640, 640)
            row = h.link(at(49, 12), "cpu")
            self.assertEqual(row["dev"], dev)
            if dev:
                self.assertEqual(row["dev_test"], "cpu_1t")
        h = Hist()
        for i, x in enumerate(B + [640]):
            run(h, 7 * i, x, x, x)
        run(h, 42, 450, 640, 1400)
        run(h, 49, 470, 640, 1500)
        row = h.link(at(49, 12), "cpu")
        self.assertEqual((row["dev"], row["dev_test"]), ("down", "cpu_1t"))

    def test_pct_changes_dev(self):
        h = Hist()
        d = h.series("net_down", B + [640, 540, 560])
        self.assertEqual(h.link(at(d - 7, 12), "down", rule={"pct": 10, "use_spread": True})["dev"], "down")
        self.assertIsNone(h.link(at(d - 7, 12), "down", rule={"pct": 30, "use_spread": True})["dev"])

    def test_p25_p75(self):
        h = Hist()
        d = h.series("net_down", [600, 610, 620, 630, 640, 650, 660, 670, 640, 640, 640])
        row = h.link(at(d - 7, 12), "down")
        self.assertEqual(row["usual_n"], 8)
        q = statistics.quantiles([600, 610, 620, 630, 640, 650, 660, 670], n=4, method="inclusive")
        self.assertAlmostEqual(row["p25_raw"], q[0], places=6)
        self.assertAlmostEqual(row["p75_raw"], q[2], places=6)
        self.assertLess(row["p25_raw"], row["p75_raw"])
        row = h.link(at(d - 14, 12), "down")              # 7 base points: no spread (П3)
        self.assertEqual((row["usual_n"], row["p25_raw"], row["p75_raw"]), (7, None, None))


class Properties(unittest.TestCase):
    """dev_calc.py «Свойства с фиксированным зерном» through dev_core (the margin 5.0 % — no silent loosening)."""

    @staticmethod
    def noise(r, cv):
        s = math.sqrt(math.log(1 + cv * cv))
        return math.exp(r.gauss(-s * s / 2, s))

    def mark(self, base, last2, pct=10, m=1):
        days = days_of(list(base) + [1.0] + list(last2))
        f = days[-3][0]
        return S.dev_core(DOWN, days, upper=f, lower=f - 10 ** 6, rule={"pct": pct, "use_spread": True}, m=m)["dev"]

    def false_rate(self, cv, nb, R=4000, seed=5):
        r = random.Random(seed)
        bad = 0
        for _ in range(R):
            v = [self.noise(r, cv) for _ in range(nb + 2)]
            bad += self.mark(v[:nb], v[-2:]) is not None
        return 100.0 * bad / R

    def detect(self, cv, nb, drop, checks, R=3000, seed=5):
        r = random.Random(seed)
        hit = 0
        for _ in range(R):
            v = [self.noise(r, cv) for _ in range(nb + 3)]
            got = False
            for _c in range(checks):
                v.append((1 - drop) * self.noise(r, cv))
                got = got or self.mark(v[-3 - nb:-3], v[-2:]) == "down"
            hit += got
        return 100.0 * hit / R

    def test_false(self):
        for cv in (0.10, 0.15, 0.20):
            for nb in (4, 5, 8, 10):
                self.assertLessEqual(self.false_rate(cv, nb), 5.0, (cv, nb))

    def test_detect(self):
        self.assertGreaterEqual(self.detect(0.10, 5, 0.3, 2), 81)
        self.assertGreaterEqual(self.detect(0.10, 8, 0.3, 2), 92)
        self.assertGreaterEqual(self.detect(0.15, 8, 0.3, 3), 78)
        self.assertGreaterEqual(self.detect(0.20, 8, 0.5, 2), 92)
        self.assertGreaterEqual(self.detect(0.20, 8, 0.4, 2), 73)

    def test_group_and_within_day(self):
        r = random.Random(7)
        bad = 0
        for _ in range(3000):
            V = [[self.noise(r, 0.10) for _ in range(10)] for _ in range(4)]
            ks = [self.mark(v[:8], v[-2:], m=4) for v in V]
            bad += any(ks)
        self.assertLessEqual(100.0 * bad / 3000, 5.0)
        r = random.Random(11)
        s = math.sqrt(math.log(1 + 0.2 ** 2))
        bad = 0
        for _ in range(3000):
            dv = []
            for _d in range(11):
                a = r.gauss(0, s * math.sqrt(0.9))
                xs = [a + r.gauss(0, s * math.sqrt(0.1)) for _ in range(6)]
                dv.append(math.exp(statistics.median(xs)))
            bad += self.mark(dv[:8], dv[-2:]) is not None
        self.assertLessEqual(100.0 * bad / 3000, 5.0)

    def test_large_values(self):
        """A fast link (4000 Mbit/s): +50 % of the raw value -> ▲ (▲/▼ go by raw values, nothing squeezes them)."""
        x = 4000.0
        self.assertEqual(self.mark([x * f for f in (0.97, 0.99, 1.0, 1.01, 1.03)], [1.5 * x, 1.5 * x]), "up")


if __name__ == "__main__":
    unittest.main()


class UsualDayTest(unittest.TestCase):
    """REV-it1-b С-4: «обычно» — от REL_N_MIN дней истории ДО окна трёх последних значений, поэтому при одном значении
    в день оно появляется на 6-й день, не на 3-й (подпись панели номер дня не обещает)."""

    def test_one_value_a_day(self):
        got = {}
        for n in (3, 4, 5, 6):
            h = Hist()
            d = h.series("net_down", [600] * n, step=1)
            got[n] = h.link(at(d - 1, 12), "down")["usual_n"]
        self.assertEqual(got, {3: 0, 4: 1, 5: 2, 6: 3})
        self.assertEqual(S.REL_N_MIN, 3)
