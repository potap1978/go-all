"""B4, the automatic repeat and the confirmed drop (SPEC-node-score-schedule-share.md §2.8 п. 1, п. 6, тесты п. 8 —
the part of the score) after the cut of 23.10 (NS-D33): a repeat is wanted and a link is «ниже» when its rel (this
node against its own «обычно») is <= 50 at usual_n >= 3 — one condition instead of the levels 300/100 and the fall of
300 of the scale; the event bench_drop on the way from «выше» to «ниже», its push at most once in 7 d per link; and the
colour of a day (SPEC-node-score.md C6: day_color, the cases of its test list). The edges of rel itself —
tests/test_score_own.py."""

import unittest

from kaskad import score as S
from tests.score_hist import ABS, S4, Hist, at, day_of, iso


def dn(x):
    return {"net_down": x}


def down_events(h):
    return [e for e in h.events() if e["link"] == "down"]


def want(h, run_vals, day, hh=12):
    h.add(day, run_vals, hh=hh)
    run = h.runs.pop()
    return "down" in S.retry_wanted(ABS, h.runs, run, day_of=day_of)


class Retry(unittest.TestCase):
    def hist(self, xs, start=0):
        h = Hist()
        for i, x in enumerate(xs):
            h.add(start + 7 * i, dn(x))
        return h, start + 7 * len(xs)

    def test_level(self):
        """rel <= 50 of the check's own value against «обычно» — a repeat; 51 — none."""
        h, d = self.hist([600] * 6)
        self.assertTrue(want(h, dn(300), d))
        self.assertTrue(want(h, dn(0), d))
        self.assertFalse(want(h, dn(306), d))
        self.assertFalse(want(h, dn(5000), d))

    def test_no_history(self):
        """NS-D33: without «обычно» (fewer than 3 base days before the window) — no repeat, no event."""
        h = Hist()
        self.assertFalse(want(h, dn(1), 0))
        h, d = self.hist([600] * 4)
        self.assertFalse(want(h, dn(1), d))
        h.add(d, dn(1))
        h.add(d, dn(1), hh=6, retry=True)
        self.assertEqual(down_events(h), [])

    def test_chronic_weak(self):
        """S5: download 177 at «обычно» 177 — no repeat, no bench_drop (a weak node is not a drop: rel 100); a
        breakdown 177 -> 1 — a repeat, and confirmed — one bench_drop."""
        h, d = self.hist([177] * 6)
        self.assertFalse(want(h, dn(177), d))
        h.add(d, dn(177))
        self.assertEqual(down_events(h), [])
        self.assertTrue(want(h, dn(1), d + 7))
        h.add(d + 7, dn(1))
        h.add(d + 7, dn(1), hh=6, retry=True)
        ev = down_events(h)
        self.assertEqual([(e["cond"], e["auto"], e["push"], e["now"]) for e in ev], [("rel", True, True, 1)])

    def test_state_machine(self):
        """«выше» -> «ниже» (the event); still «ниже» — no repeat, no event; back «выше» (rel > 50), then down — the
        next event, pushed (more than 7 d after the first)."""
        h, d = self.hist([600] * 6)
        self.assertTrue(want(h, dn(250), d))
        h.add(d, dn(250))
        h.add(d, dn(240), hh=6, retry=True)
        self.assertEqual([(e["cond"], e["auto"], e["push"]) for e in down_events(h)], [("rel", True, True)])
        self.assertFalse(want(h, dn(245), d + 7))      # «ниже» already
        h.add(d + 7, dn(245))
        self.assertEqual(len(down_events(h)), 1)
        self.assertIs(S.drop_events(ABS, h.runs, day_of=day_of)[1]["down"], False)
        h.add(d + 14, dn(600))
        h.add(d + 21, dn(600))                          # the median of 245, 600, 600: back «выше»
        self.assertIs(S.drop_events(ABS, h.runs, day_of=day_of)[1]["down"], True)
        self.assertTrue(want(h, dn(240), d + 28))
        h.add(d + 28, dn(240))
        h.add(d + 28, dn(230), hh=6, retry=True)
        ev = down_events(h)
        self.assertEqual([(e["day"], e["push"]) for e in ev], [(iso(d), True), (iso(d + 28), True)])
        e1, e2 = S.drop_events(ABS, h.runs, day_of=day_of), S.drop_events(ABS, h.runs, day_of=day_of)
        self.assertEqual(e1, e2)                       # derived from the history, deterministic

    def test_push_within_7_days(self):
        """A second drop of the same link within 7 d after the push — an event with push: false."""
        h, d = self.hist([600] * 6)
        h.add(d, dn(240))
        h.add(d, dn(230), hh=6, retry=True)
        h.add(d + 1, dn(600))
        h.add(d + 2, dn(600))                           # «выше» again
        h.add(d + 3, dn(240))
        h.add(d + 3, dn(230), hh=6, retry=True)
        ev = down_events(h)
        self.assertEqual([(e["cond"], e["matched"], e["push"]) for e in ev],
                         [("rel", ["rel"], True), ("rel", ["rel"], False)])

    def test_by_latest_checks(self):
        """«Обычно» 600, checks 280 (no repeat: the switch is off) and 270 -> fresh 280 — bench_drop «по последним
        проверкам», not of a repeat."""
        h, d = self.hist([600] * 5)
        h.add(d, dn(280))
        h.add(d + 7, dn(270))
        ev = down_events(h)
        self.assertEqual([(e["cond"], e["auto"], e["now"], e["run"]) for e in ev],
                         [("rel", False, 47, h.runs[-1]["id"])])

    def test_not_after_repeat_and_probes(self):
        h, d = self.hist([600] * 6)
        h.add(d, dn(100))
        h.add(d, dn(90), hh=6, retry=True)
        self.assertEqual(S.retry_wanted(ABS, h.runs[:-1], h.runs[-1], day_of=day_of), [])
        h2 = Hist()
        h2.usual(S4, n=6)
        run = {"id": "x", "ts": at(42), "status": "ok", "vals": dict(S4, loss={"v": 50, "n": 30000, "lo": 49},
                                                                       rtt=900, steal_p95=60)}
        self.assertEqual(S.retry_wanted(ABS, h2.runs, run, day_of=day_of), [])      # the probes: never a repeat
        run["vals"]["net_up"] = 5
        self.assertEqual(S.retry_wanted(ABS, h2.runs, run, day_of=day_of), ["up", "net_bw"])   # net_bw: (100 + 0) / 2

    def test_s4_upload(self):
        """S4, weekly, upload 1300 -> 5: a repeat; confirmed — one event with the raw value and unit."""
        h = Hist()
        d = h.usual(S4, n=6)
        h.add(d, dict(S4, net_up=5))
        run = h.runs.pop()
        self.assertIn("up", S.retry_wanted(ABS, h.runs, run, day_of=day_of))
        h.runs.append(run)
        h.add(d, dict(S4, net_up=5), hh=6, retry=True)
        ev = [e for e in h.events() if e["link"] == "up"]
        self.assertEqual([(e["cond"], e["value"], e["unit"], e["day"], e["now"]) for e in ev],
                         [("rel", 5, "Мбит/с", iso(d), 0)])
        self.assertAlmostEqual(ev[0]["usual"], S4["net_up"], places=6)


class DayColor(unittest.TestCase):
    D = {"hours": 24, "test": False, "offline_min": 0, "lost": 0, "n": 0, "alarm_min": 0, "warn_min": 0}

    def color(self, devs=(), drops=(), **kw):
        return S.day_color(dict(self.D, **kw), list(devs), list(drops))

    def test_each_and_bounds(self):
        self.assertEqual(self.color(), ("green", False, None))
        self.assertEqual(self.color(hours=0)[0], "grey")
        self.assertEqual(self.color(hours=0)[2]["code"], "no_data")
        self.assertEqual(self.color(hours=0, test=True)[0], "green")
        self.assertEqual(self.color(offline_min=4.9)[0], "green")
        self.assertEqual(self.color(offline_min=5)[2], {"code": "offline", "args": {"min": 5}})
        self.assertEqual(self.color(lost=19, n=1000)[0], "green")
        self.assertEqual(self.color(lost=20, n=1000)[2], {"code": "loss", "args": {"pct": 2.0, "thr": 2}})
        self.assertEqual(self.color(alarm_min=4)[0], "green")
        self.assertEqual(self.color(alarm_min=5)[:1], ("red",))
        self.assertEqual(self.color(warn_min=14)[0], "green")
        self.assertEqual(self.color(warn_min=15)[2]["code"], "warn")
        self.assertEqual(self.color(devs=["down"])[2]["code"], "link_down")
        self.assertEqual(self.color(devs=["up"])[0], "green")
        self.assertEqual(self.color(hours=11)[1], True)
        self.assertEqual(self.color(hours=12)[1], False)

    def test_order(self):
        self.assertEqual(self.color(offline_min=6, lost=30, n=1000)[2]["code"], "offline")
        ev = {"link": "up", "test": "net_up", "value": 5, "usual": 1300, "unit": "Мбит/с", "now": 0}
        self.assertEqual(self.color(devs=["down"], drops=[ev])[2],
                         {"code": "drop", "args": {"link": "up", "test": "net_up", "now": 5, "usual": 1300,
                                                   "unit": "Мбит/с", "rel": 0}})     # now — the raw value, rel — 0…300
        self.assertEqual(self.color(warn_min=20, drops=[ev])[2]["code"], "warn")

    def test_offline_sum(self):
        """Three times 2 min without network (6 min a day, never 5 in a row) -> red; an open span — to «now»."""
        s = at(3, 0)
        spans = [(s + 3600, s + 3720), (s + 7200, s + 7320), (s + 9000, s + 9120)]
        m = S.offline_minutes(spans, s, s + 86400)
        self.assertEqual(m, 6.0)
        self.assertEqual(self.color(offline_min=m)[0], "red")
        self.assertEqual(S.offline_minutes([(s - 600, s + 120)], s, s + 86400), 2.0)
        self.assertEqual(S.offline_minutes([(s + 600, None)], s, s + 86400, now=s + 900), 5.0)
        self.assertEqual(S.offline_minutes([(s + 86000, None)], s, s + 86400), 400 / 60.0)

    def test_peak_of_loss_is_not_daily(self):
        """30 % in one 15-minute point at 0.4 % a day -> not red."""
        self.assertEqual(self.color(lost=104, n=25920)[0], "green")

    def strip(self, h, day, **kw):
        end = at(day + 1, 0) - 1
        v = h.view(end, **kw)
        ev = [e for e in h.events() if e["day"] == iso(day)]
        tested = S.tested_links(ABS, h.runs, iso(day), day_of)
        return S.day_color(dict(self.D, test=bool(tested)), S.day_devs(v["links"], tested), ev)

    def test_link_down_only_on_check_day(self):
        h = Hist()
        d = h.series("net_down", [600, 620, 640, 660, 700, 640, 540])
        h.add(d, {"net_down": 560})                       # Tuesday: ▼
        self.assertEqual(self.strip(h, d)[2]["code"], "link_down")
        self.assertEqual(self.strip(h, d + 1)[0], "green")     # Wednesday without a check
        h.add(d + 2, {"cpu_1t": 1500})                    # Thursday: a partial check without the channel
        self.assertEqual(self.strip(h, d + 2)[0], "green")

    def test_refuted_and_by_days(self):
        h = Hist()
        d = h.series("net_down", [600, 620, 640, 660, 700])
        h.add(d, {"net_down": 640})
        h.add(d + 7, {"net_down": 50})
        h.add(d + 7, {"net_down": 575}, hh=6, retry=True)
        self.assertEqual(self.strip(h, d + 7)[0], "green")     # the repeat itself gives nothing
        h = Hist()
        d = h.series("net_down", [600, 620, 640, 660, 700])
        h.add(d, {"net_down": 560})
        h.add(d + 7, {"net_down": 50})
        h.add(d + 7, {"net_down": 540}, hh=6, retry=True)
        self.assertEqual(self.strip(h, d + 7)[2]["code"], "link_down")   # ▼ by the days (уточнение к п. 3)

    def test_drop_days(self):
        h = Hist()
        base = [1250, 1280, 1300, 1320, 1350]
        for i, x in enumerate(base):
            h.add(7 * i, dict(S4, net_up=x))
        h.add(35, dict(S4, net_up=1300))
        h.add(42, dict(S4, net_up=5))
        h.add(42, dict(S4, net_up=5), hh=6, retry=True)
        c = self.strip(h, 42)
        self.assertEqual((c[0], c[2]["code"], c[2]["args"]["link"], c[2]["args"]["rel"]), ("yellow", "drop", "up", 0))
        self.assertIsNone(next(x for x in h.view(at(43, 0) - 1)["links"] if x["id"] == "up")["dev"])
        h = Hist()                                       # no base: no drop (NS-D33), the day stays green
        h.add(0, dict(S4))
        h.add(7, dict(S4, net_up=5))
        h.add(7, dict(S4, net_up=5), hh=6, retry=True)
        self.assertEqual(self.strip(h, 7), ("green", False, None))
        h = Hist()                                       # after midnight — the day of the original
        d = h.usual(S4, n=6)
        h.add(d + 6, dict(S4, net_up=5), hh=23, mm=30)
        h.add(d + 7, dict(S4, net_up=5), hh=1, mm=10, retry=True)
        self.assertEqual(self.strip(h, d + 6)[2]["code"], "drop")
        self.assertNotEqual((self.strip(h, d + 7)[2] or {}).get("code"), "drop")

    def test_push_false_still_yellow(self):
        h = Hist()
        d = h.usual(dn(700), n=6, step=1)
        h.add(d, dn(240))
        h.add(d, dn(230), hh=6, retry=True)
        h.add(d + 1, dn(700))
        h.add(d + 2, dn(700))
        h.add(d + 3, dn(240))
        h.add(d + 3, dn(230), hh=6, retry=True)
        ev = down_events(h)
        self.assertEqual([e["push"] for e in ev], [True, False])
        self.assertEqual(self.strip(h, d + 3)[2]["code"], "drop")

    def test_probe_link_every_day(self):
        h = Hist()
        h.add(0, dict(S4))
        rtt = [20, 20, 21, 19, 20, 22, 20, 21, 20, 20, 35, 36, 34, 35]
        days = [{"day": iso(1 + i), "v": x, "hours": 24} for i, x in enumerate(rtt)]
        got = []
        for day in (12, 13, 14):
            v = h.view(at(day + 1, 0) - 1, probes=[], probe_days={"rtt": [x for x in days if x["day"] <= iso(day)]})
            got.append(S.day_color(dict(self.D), S.day_devs(v["links"], set()), [])[2]["code"])
        self.assertEqual(got, ["link_down"] * 3)


if __name__ == "__main__":
    unittest.main()
