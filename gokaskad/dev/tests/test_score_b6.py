"""B6 batch of lib/kaskad/score.py (REV-ns-b4-3, REV-ns-b12-2 стыки): a repeat after midnight on the page of the day
of its original, one event of a drop for the original and its repeat (NS-D33, rel <= 50), drop_events of a catalogue
without the links of the tests, the numbers of the hint «ждёт подтверждения» of loss (by v, decided by lo)."""

import unittest

from kaskad import score as S
from tests.score_hist import ABS, S4, T, Hist, at, day_of, iso


def link(v, lid):
    return next(x for x in v["links"] if x["id"] == lid)


class LateRepeatTest(unittest.TestCase):
    """REV-ns-b4-3 З-1 (r3/s3.py, s3b.py): the original 23:30 of day 21 falls (net_up 5), its repeat 01:10 of day 22
    refutes it (1300). The page of day 21 sees the pair everywhere: no ▼, the day green, the value of the pair."""

    def hist(self, late=True):
        h = Hist()
        for d in range(20):
            h.add(d, dict(S4))
        h.add(20, dict(S4, net_up=S4["net_up"] * 0.6))
        h.add(21, dict(S4, net_up=5), hh=23, mm=30)
        if late:
            h.add(22, {"net_up": S4["net_up"]}, hh=1, mm=10, retry=True)
        else:
            h.add(21, {"net_up": S4["net_up"]}, hh=23, mm=50, retry=True)
        return h

    def page(self, h):
        v = h.view(at(22, 0) - 1, events=h.events())
        col = S.day_color({"hours": 24, "test": True},
                          S.day_devs(v, S.tested_links(ABS, h.runs, iso(21), day_of=day_of)),
                          [e for e in h.events() if e["day"] == iso(21)])
        return v, col

    def test_after_midnight_as_before_it(self):
        late, col_late = self.page(self.hist(True))
        same, col_same = self.page(self.hist(False))
        up = link(late, "up")
        self.assertIsNone(up["dev"])
        self.assertNotIn("up", late["dev_args"])
        self.assertEqual(col_late, ("green", False, None))
        self.assertEqual(up["now_id"], self.hist(True).runs[-1]["id"])        # the value of the pair — the repeat's
        for k in ("rel_total", "rel_n", "last_id", "last_at", "fresh_from", "stale", "first"):
            self.assertEqual(late["summary"][k], same["summary"][k], k)
        for a, b in zip(late["links"], same["links"]):
            for k in ("rel", "fresh_raw", "usual_raw", "usual_n", "now_id", "now_day", "dev", "dev_pending"):
                self.assertEqual(a[k], b[k], (a["id"], k))
        self.assertEqual(col_late, col_same)

    def test_the_hour_of_checked_is_the_repeats_own(self):
        h = self.hist(True)
        v = h.view(at(22, 0) - 1, events=h.events())
        self.assertIn(h.runs[-1]["id"], v["summary"]["fresh_from"]["tests"])
        self.assertEqual(v["summary"]["checked"]["part"], "mixed")        # 04:00 … 01:10 — not the end of the day
        self.assertNotEqual(v["summary"]["checked"]["hhmm"], "23:59")

    def test_a_repeat_after_the_page_stays_out(self):
        h = self.hist(True)
        v = h.view(at(21, 12))                                              # before the original: neither is there
        self.assertNotIn(h.runs[-1]["id"], v["summary"]["fresh_from"]["tests"])


class RepeatPushTest(unittest.TestCase):
    """REV-ns-b4-3 З-2 after NS-D33: the original and its repeat are one drop — one event (there are no heavier
    conditions any more: one condition, rel <= 50); the event is the check's that took the link «ниже»."""

    def test_the_repeat_gives_the_event(self):
        h = Hist()
        d = h.usual({"net_down": 700}, n=6)
        h.add(d, {"net_down": 200})
        rep = h.add(d, {"net_down": 150}, hh=6, retry=True)
        ev = [(e["run"], e["cond"], e["push"], e["auto"]) for e in h.events() if e["link"] == "down"]
        self.assertEqual(ev, [(rep, "rel", True, True)])

    def test_a_repeat_after_the_originals_event_gives_none(self):
        h = Hist()
        d = h.usual({"net_down": 700}, n=6)
        h.add(d, {"net_down": 200})
        orig = h.add(d + 7, {"net_down": 200})                          # by the latest checks: the original's event
        h.add(d + 7, {"net_down": 20}, hh=6, retry_of=orig)
        h.add(d + 7, {"net_down": 20}, hh=7, retry_of=orig)
        ev = [(e["run"], e["cond"], e["push"], e["auto"]) for e in h.events() if e["link"] == "down"]
        self.assertEqual(ev, [(orig, "rel", True, False)])


class NoTestLinksTest(unittest.TestCase):
    def test_drop_events_of_a_catalogue_without_the_links_of_the_tests(self):
        """view() asks drop_events only of a catalogue with the links of the tests; without them — nothing."""
        spec = S.load_spec()
        spec["links"] = [b for b in spec["links"] if b["id"] in S.PROBE_LINKS]
        h = Hist()
        h.usual(S4, n=8)
        h.add(56, dict(S4, net_down=1))
        self.assertEqual(S.drop_events(spec, h.runs, day_of=day_of), ([], {}))
        self.assertEqual(S.retry_wanted(spec, h.runs[:-1], h.runs[-1], day_of=day_of), [])
        v = S.view(spec, h.runs, at(56, 12), day_of=day_of)
        self.assertEqual([x["id"] for x in v["links"]], ["net_q", "stab", "loss"])


class PendingHintTest(unittest.TestCase):
    """REV-ns-b4-3 П-1 (r3/s5.py): «ждёт подтверждения» of loss is decided by lo; the numbers of the hint are by v —
    none when v is on the other side."""

    def view(self, base, last):
        h = Hist()
        h.add(0, dict(S4))
        recs = [{"day": iso(i), "v": base[0] * (1 + 0.02 * (i % 3 - 1)), "lo": base[1] * (1 + 0.02 * (i % 3 - 1)),
                 "n": base[2], "hours": 24} for i in range(10)]
        recs.append({"day": iso(10), "v": last[0], "lo": last[1], "n": last[2], "hours": 24})
        k = last[0] * last[2] / 100
        wins = [{"day": iso(10), "hours": 24, "loss": {a: {"n": last[2], "k": k, "e": 1} for a in ("a", "b")}}]
        return h.view(at(11, 1), probes=wins, probe_days={"loss": recs})

    def test_v_against_lo_no_numbers(self):
        v = self.view((0.10, 0.095, 1e6), (0.30, 0.05, 5e4))           # v worse ×3, lo better
        lk = link(v, "loss")
        self.assertEqual((lk["dev"], lk["dev_pending"], lk["dev_pending_kind"]), (None, "up", "day"))
        self.assertEqual(v["pend_args"]["loss"], {"link": "loss", "test": "loss", "now": None, "usual": None,
                                                  "unit": "%", "days": None})

    def test_v_with_lo_numbers(self):
        v = self.view((0.10, 0.095, 1e6), (0.60, 0.55, 1e6))           # both worse
        lk = link(v, "loss")
        self.assertEqual(lk["dev_pending"], "down")
        a = v["pend_args"]["loss"]
        self.assertAlmostEqual(a["now"], 0.60)
        self.assertAlmostEqual(a["usual"], 0.10, places=2)

    def test_pending_of_a_test_link_numbers(self):
        h = Hist()
        for d in range(12):
            h.add(d, dict(S4))
        h.add(12, dict(S4, net_down=S4["net_down"] * 0.5))
        v = h.view(at(13))
        lk = link(v, "down")
        self.assertEqual((lk["dev"], lk["dev_pending"]), (None, "down"))
        a = v["pend_args"]["down"]
        self.assertAlmostEqual(a["now"], S4["net_down"] * 0.5)
        self.assertAlmostEqual(a["usual"], S4["net_down"])
        self.assertEqual(a["unit"], T["net_down"]["unit"])


class HintNumbersTest(unittest.TestCase):
    """REV-ns-b6 З-1, П-2: links[].hint carries the numbers of the texts of NS-D25 — the two days of ▲/▼ («520 и 540»),
    the last day of «ждёт» day, the value of the event of retry_confirmed / drop, the latest check of retry_wait;
    rounded as the series."""

    @staticmethod
    def hint(v, lid):
        from kaskad.bench import hostscore as HS
        return next(r for r in HS._links(v, HS.LINK_FIELDS) if r["id"] == lid)

    def test_two_days_of_a_mark(self):
        h = Hist()
        for d in range(12):
            h.add(d, dict(S4))
        h.add(12, dict(S4, net_down=S4["net_down"] * 0.5))
        h.add(13, dict(S4, net_down=S4["net_down"] * 0.55))
        row = self.hint(h.view(at(14)), "down")
        self.assertEqual(row["dev"], "down")
        self.assertEqual(row["hint"]["days"], [[iso(12), round(S4["net_down"] * 0.5, 3)],
                                               [iso(13), round(S4["net_down"] * 0.55, 3)]])
        self.assertEqual(row["hint"]["now"], round(S4["net_down"] * 0.55, 3))      # П-2: not 935.0000000000003
        self.assertEqual(row["hint"]["unit"], T["net_down"]["unit"])

    def test_the_last_day_of_pending(self):
        h = Hist()
        for d in range(12):
            h.add(d, dict(S4))
        h.add(12, dict(S4, net_down=S4["net_down"] * 0.5))
        row = self.hint(h.view(at(13)), "down")
        self.assertEqual((row["dev"], row["dev_pending_kind"]), (None, "day"))
        self.assertEqual(row["hint"]["days"], [[iso(12), round(S4["net_down"] * 0.5, 3)]])
        self.assertIsNotNone(row["hint"]["usual"])

    def test_the_value_of_the_event(self):
        h = Hist()
        for i, x in enumerate([600, 620, 640, 660, 700]):
            h.add(7 * i, {"net_down": x})
        h.add(35, {"net_down": 640})
        h.add(42, {"net_down": 50})
        h.add(42, {"net_down": 40}, hh=6, retry=True)
        ev = [e for e in h.events() if e["link"] == "down"]
        row = self.hint(h.view(at(42, 12), events=h.events()), "down")
        self.assertEqual(row["dev_pending_kind"], "retry_confirmed")
        self.assertEqual((row["hint"]["now"], row["hint"]["unit"], row["hint"]["days"]),
                         (ev[-1]["value"], T["net_down"]["unit"], None))
        self.assertIsNotNone(row["hint"]["now"])

    def test_the_value_of_a_drop_without_a_repeat(self):
        """Six usual checks («обычно» of rel, NS-D33), then two checks of one day with upload 5 and 6 — no repeat (the
        switch is off): the fresh value falls at the second (no ▼: one day only)."""
        h = Hist()
        d = h.usual(S4, n=6)
        h.add(d, dict(S4, net_up=5))
        h.add(d, dict(S4, net_up=6), hh=12, by="panel")
        ev = [e for e in h.events() if e["link"] == "up"]
        self.assertTrue(len(ev) == 1 and not ev[-1]["auto"] and ev[-1]["day"] == iso(d), ev)
        self.assertEqual((ev[-1]["run"], ev[-1]["value"]), (h.runs[-1]["id"], 6))
        row = self.hint(h.view(at(d, 13), events=h.events()), "up")
        self.assertEqual(row["dev_pending_kind"], "drop")
        self.assertEqual(row["hint"]["now"], ev[-1]["value"])
        self.assertIsNotNone(row["hint"]["now"])

    def test_retry_wait_the_latest_check(self):
        h = Hist()
        h.add(0, dict(S4))
        h.add(7, dict(S4, net_up=5))
        row = self.hint(h.view(at(7, 12), retry={"ts": at(7, 15), "links": ["up"]}), "up")
        self.assertEqual(row["dev_pending_kind"], "retry_wait")
        self.assertEqual(row["hint"]["now"], 5)

    def test_retry_line_has_the_value_of_the_latest_check(self):
        """6C (SS §2.8 п. 7): «Последняя проверка: отдача 5 Мбит/с. Проверю ещё раз около 15:40» — score.retry carries
        the link, the test, the value and the unit of the latest check besides the time; no value — nulls (summary.retry
        after the cut of 23.10)."""
        h = Hist()
        h.add(0, dict(S4))
        h.add(7, dict(S4, net_up=5))
        v = h.view(at(7, 12), retry={"ts": at(7, 15), "links": ["up"]})
        r = v["summary"]["retry"]
        self.assertEqual((r["links"], r["link"], r["test"], r["value"]), (["up"], "up", "net_up", 5))
        self.assertTrue(r["unit"])
        self.assertEqual(r["at"], "15:00")                    # hhmm_of of the time of the repeat (UTC here)
        r = h.view(at(7, 12), retry={"ts": at(7, 15), "links": ["crypto"]})["summary"]["retry"]
        self.assertEqual((r["link"], r["value"], r["unit"]), (None, None, None))


if __name__ == "__main__":
    unittest.main()
