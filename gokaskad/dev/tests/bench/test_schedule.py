"""B10: the schedule of the checks — the one ``validate``, the events of the clock, ``due``, the lines of the timer,
the offer (SPEC-node-score-schedule-share §2.1–2.2, §2.4, §2.8, NS-S3, S6, S7, S18)."""

import calendar
import random
import time
import unittest

from kaskad import tz
from kaskad.bench import schedule as S

BERLIN = tz.Zone("Europe/Berlin", footer=tz.Posix("CET-1CEST,M3.5.0,M10.5.0/3"))
MOSCOW = tz.Zone("Europe/Moscow", types=[(10800, False)])
UTC = tz.Zone("UTC", types=[(0, False)])
FALLBACK = tz.Zone.fixed("Europe/Nowhere", tz.FALLBACK_OFFSET)     # tz.py could not read the zone


def utc(y, mo, d, h=0, mi=0, s=0):
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))


def ent(t, days, mode="quick", **kw):
    e = {"time": t, "mode": mode, "days": list(days)}
    e.update(kw)
    return e


_SEQ = [0]


def eid(t, days, mode="quick", **kw):
    """An entry with an id (the clock takes only entries with one, REV-ns-b10 З-2)."""
    if "id" not in kw:
        _SEQ[0] += 1
        kw["id"] = "%06x" % (0xe00000 + _SEQ[0])
    return ent(t, days, mode, **kw)


def codes(errors):
    return [e["code"] for e in errors]


class ValidateFieldsTest(unittest.TestCase):

    def test_a_good_entry_passes_and_is_normalized(self):
        norm, err = S.validate([ent("03:45", [5, 1, 3])])
        self.assertEqual(err, [])
        self.assertEqual(norm, [{"id": None, "time": "03:45", "mode": "quick", "days": [1, 3, 5], "on": True,
                                 "quiet": True, "monthly": False, "since": None, "off_why": None}])

    def test_bad_times(self):
        for t in ("03:45\n", "03:45\nExecStart=x", "3:45", "24:00", "23:60", "03:45;", "[03:45", " 03:45", "",
                  None, 345, "03-45", "03:45:00"):
            norm, err = S.validate([ent(t, [1])])
            self.assertEqual(codes(err), ["time"], repr(t))
            self.assertFalse(norm[0]["on"])
            self.assertEqual(norm[0]["off_why"], "time")
            self.assertIsNone(norm[0]["time"], "a bad time never goes on (NS-S7)")

    def test_edges_of_the_time(self):
        for t in ("00:00", "23:59", "09:05", "19:30"):
            self.assertEqual(S.validate([ent(t, [1])])[1], [], t)

    def test_bad_mode(self):
        for m in ("Quick", "fast", "", None, 1, ["quick"]):
            self.assertEqual(codes(S.validate([ent("03:00", [1], mode=m)])[1]), ["mode"], repr(m))

    def test_bad_days(self):
        for d in ([], [0], [8], [1, 1], [True], [1, True], ["1"], [1.0], None, "1", [[1]]):
            e = ent("03:00", [1])
            e["days"] = d
            self.assertEqual(codes(S.validate([e])[1]), ["days"], repr(d))

    def test_flags_strictly_bool(self):
        for k in ("on", "quiet", "monthly"):
            for v in (1, 0, "true", None, "yes"):
                e = ent("03:00", [1])
                e[k] = v
                self.assertEqual(codes(S.validate([e])[1]), [k], (k, v))

    def test_monthly_needs_one_day(self):
        self.assertEqual(codes(S.validate([ent("04:40", [1, 2], monthly=True)])[1]), ["monthly_days"])
        self.assertEqual(S.validate([ent("04:40", [1], monthly=True)])[1], [])

    def test_ids(self):
        self.assertEqual(S.validate([ent("03:00", [1], id="a1b2c3")])[1], [])
        for i in ("A1B2C3", "a1b2c", "a1b2c3d", "a1b2c3\n", "a;rm -", 123456, True):
            self.assertEqual(codes(S.validate([ent("03:00", [1], id=i)])[1]), ["id"], repr(i))
        norm, err = S.validate([ent("03:00", [1], id="a1b2c3"), ent("09:00", [1], id="a1b2c3")])
        self.assertEqual(codes(err), ["id"])
        self.assertTrue(norm[0]["on"])
        self.assertFalse(norm[1]["on"])

    def test_not_a_list_or_not_an_object(self):
        self.assertEqual(S.validate({"time": "03:00"}), ([], [{"code": "list", "id": None, "args": {}}]))
        norm, err = S.validate(["03:00", ent("03:00", [1])])
        self.assertEqual(codes(err), ["entry"])
        self.assertEqual(len(norm), 1)

    def test_off_entry_still_checked_for_its_fields(self):
        self.assertEqual(codes(S.validate([ent("25:00", [1], on=False)])[1]), ["time"])

    def test_every_code_has_a_text_in_both_languages(self):
        from kaskad.i18n import tr
        for c in S.CODES:
            for lang in ("ru", "en"):
                self.assertNotEqual(tr("bench.sched.err." + c, lang, n=1, max=1, day="x", a="1", b="2", min=1),
                                    "bench.sched.err." + c)
        self.assertIn(tr("bench.sched.day.2"), S.error_text({"code": "per_day", "args": {"day": 2, "n": 7, "max": 6}}))


class ValidateLimitsTest(unittest.TestCase):

    def test_one_gap_error_per_pair_of_entries(self):
        """REV-ns-b10 З-1: 00:30 and 23:30 on all days — one error for the pair (the smallest gap), not seven."""
        _n, err = S.validate([ent("00:30", range(1, 8), id="aaaaaa"), ent("23:30", range(1, 8), id="bbbbbb")])
        gaps = [e for e in err if e["code"] == "gap"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["args"]["min"], 60)
        _n, err = S.validate([ent("03:00", [1]), ent("04:00", [1]), ent("04:30", [1])])
        self.assertEqual(sorted(e["args"]["min"] for e in err if e["code"] == "gap"), [30, 60, 90])


    """NS-S3: by the ENABLED entries on every day of the week, around the circle of the week."""

    def times(self, n, start=0, step=120):
        return ["%02d:%02d" % divmod(start + i * step, 60) for i in range(n)]

    def test_six_a_day_yes_seven_no(self):
        six = [ent(t, [1]) for t in self.times(6)]
        self.assertEqual(S.validate(six)[1], [])
        norm, err = S.validate(six + [ent("12:00", [1])])
        self.assertEqual([(e["code"], e["args"]["day"], e["args"]["n"]) for e in err], [("per_day", 1, 7)])
        self.assertEqual([e["on"] for e in norm], [True] * 6 + [False])
        self.assertEqual(norm[6]["off_why"], "per_day")

    def test_a_disabled_entry_is_not_counted(self):
        six = [ent(t, [1]) for t in self.times(6)]
        self.assertEqual(S.validate(six + [ent("12:00", [1], on=False)])[1], [])
        self.assertEqual(S.validate([ent("03:00", [1]), ent("03:30", [1], on=False)])[1], [])

    def test_two_full_yes_three_no(self):
        two = [ent("00:00", [3], "full"), ent("04:00", [3], "full")]
        self.assertEqual(S.validate(two + [ent("08:00", [3], "quick")])[1], [])
        err = S.validate(two + [ent("08:00", [3], "full")])[1]
        self.assertEqual([(e["code"], e["args"]["day"], e["args"]["n"]) for e in err], [("full_per_day", 3, 3)])

    def test_gap_120_yes_119_no(self):
        self.assertEqual(S.validate([ent("03:00", [2]), ent("05:00", [2])])[1], [])
        err = S.validate([ent("03:00", [2]), ent("04:59", [2])])[1]
        self.assertEqual(codes(err), ["gap"])
        self.assertEqual(err[0]["args"]["min"], 119)

    def test_across_the_week(self):
        """§11.1 п. 25: Sun 23:30 + Mon 00:30 — 60 min, refused; Mon 00:30 + Mon 23:30 — fine (23 h inside the day,
        6 days 1 h across the week); the same two on every day — refused (Tue 00:30 after Mon 23:30)."""
        err = S.validate([ent("23:30", [7]), ent("00:30", [1])])[1]
        self.assertEqual(codes(err), ["gap"])
        self.assertEqual(err[0]["args"]["min"], 60)
        self.assertEqual(S.validate([ent("00:30", [1]), ent("23:30", [1])])[1], [])
        every = list(range(1, 8))
        self.assertIn("gap", codes(S.validate([ent("00:30", every), ent("23:30", every)])[1]))

    def test_one_entry_on_two_days_is_no_gap(self):
        self.assertEqual(S.validate([ent("23:30", [1, 2, 3, 4, 5, 6, 7])])[1], [])

    def test_twelve_yes_thirteen_no(self):
        many = [ent("%02d:00" % (2 * i), [1], on=(i < 6)) for i in range(12)]
        self.assertEqual(S.validate(many)[1], [])
        norm, err = S.validate(many + [ent("01:00", [2], on=False)])
        self.assertEqual(codes(err), ["total"])
        self.assertEqual(len(norm), 12)

    def test_monthly_counts_as_weekly(self):
        """NS-S18: the first week may hold all — a monthly entry is in the limits of its day."""
        err = S.validate([ent("03:00", [1]), ent("04:00", [1], "full", monthly=True)])[1]
        self.assertEqual(codes(err), ["gap"])
        two = [ent("00:00", [1], "full"), ent("04:00", [1], "full")]
        self.assertEqual(codes(S.validate(two + [ent("08:00", [1], "full", monthly=True)])[1]), ["full_per_day"])

    def test_the_earlier_entries_win_in_the_result(self):
        norm, err = S.validate([ent("03:00", [1]), ent("04:00", [1]), ent("06:00", [1])])
        self.assertEqual([e["on"] for e in norm], [True, False, True])
        self.assertEqual(norm[1]["off_why"], "gap")

    def test_spring_day_compresses_but_is_valid(self):
        """§2.4 придирка 2: 01:05 and 03:05 in Europe/Berlin are 120 min by the clock of the entries — valid."""
        self.assertEqual(S.validate([ent("01:05", [7]), ent("03:05", [7])])[1], [])


class PrepareTest(unittest.TestCase):

    def test_off_why_is_the_servers(self):
        """REV-ns-b10 П-1: a client cannot mark an entry «turned off by the check»."""
        norm, err = S.prepare([ent("03:00", [1], on=False, off_why="time")], [], 5, rng=random.Random(1))
        self.assertEqual(err, [])
        self.assertIsNone(norm[0]["off_why"])
        prev = [ent("03:00", [1], id="aaaaaa", on=False, off_why="gap", since=1),
                ent("09:00", [1], id="bbbbbb", on=True, since=1)]
        norm = S.prepare([ent("03:00", [1], id="aaaaaa", on=False, off_why="time"),
                          ent("09:00", [1], id="bbbbbb", on=False, off_why="per_day")], prev, 5)[0]
        self.assertEqual([e["off_why"] for e in norm], ["gap", None])



    def test_new_entry_gets_id_and_since(self):
        norm, err = S.prepare([ent("03:00", [1])], [], 1000, rng=random.Random(1))
        self.assertEqual(err, [])
        self.assertRegex(norm[0]["id"], r"\A[0-9a-f]{6}\Z")
        self.assertEqual(norm[0]["since"], 1000)

    def test_unknown_id_refused(self):
        _n, err = S.prepare([ent("03:00", [1], id="abcdef")], [], 1000)
        self.assertEqual(codes(err), ["id"])

    def test_since_moves_on_wake_and_on_a_change_of_time_or_days(self):
        prev = [dict(ent("03:00", [1], id="aaaaaa", since=10)), dict(ent("09:00", [1], id="bbbbbb", since=10,
                                                                             on=False))]
        same = S.prepare([ent("03:00", [1], id="aaaaaa"), ent("09:00", [1], id="bbbbbb", on=False)], prev, 99)[0]
        self.assertEqual([e["since"] for e in same], [10, 10])
        woke = S.prepare([ent("03:00", [1], id="aaaaaa"), ent("09:00", [1], id="bbbbbb")], prev, 99)[0]
        self.assertEqual([e["since"] for e in woke], [10, 99])
        moved = S.prepare([ent("03:10", [1], id="aaaaaa"), ent("09:00", [2], id="bbbbbb", on=False)], prev, 99)[0]
        self.assertEqual([e["since"] for e in moved], [99, 99])

    def test_off_why_cleared_on_a_passing_edit(self):
        prev = [ent("03:00", [1], id="aaaaaa", on=False, off_why="gap", since=1)]
        norm, err = S.prepare([ent("03:00", [1], id="aaaaaa", on=True, off_why="gap")], prev, 5)
        self.assertEqual(err, [])
        self.assertIsNone(norm[0]["off_why"])


class EventsTest(unittest.TestCase):

    def test_moscow_week(self):
        # Mon 2026-10-19 00:00 MSK = Sun 21:00 UTC
        now = utc(2026, 10, 18, 21, 0)
        es = [eid("03:45", [1, 3, 5]), eid("00:05", [7], "full")]
        got = S.next_runs(es, now, MOSCOW, n=5)
        self.assertEqual([v["key"] for v in got],
                         ["2026-10-19 03:45", "2026-10-21 03:45", "2026-10-23 03:45", "2026-10-25 00:05",
                          "2026-10-26 03:45"])
        self.assertEqual(got[0]["ts"], utc(2026, 10, 19, 0, 45))
        self.assertEqual(got[3]["mode"], "full")
        self.assertTrue(all("ts_alt" not in v for v in got))

    def test_utc_and_fallback(self):
        now = utc(2026, 10, 18, 0, 0)
        self.assertEqual(S.next_runs([eid("01:00", [1])], now, UTC, 1)[0]["ts"], utc(2026, 10, 19, 1, 0))
        self.assertEqual(S.next_runs([eid("01:00", [1])], now, FALLBACK, 1)[0]["ts"], utc(2026, 10, 18, 22, 0))

    def test_monthly_first_week_only(self):
        """NS-S18: «Mon 04:40, monthly» — the first Monday (dates 1–7) of each month."""
        now = utc(2026, 10, 1)
        got = S.next_runs([eid("04:40", [1], monthly=True)], now, MOSCOW, n=4)
        self.assertEqual([v["key"] for v in got], ["2026-10-05 04:40", "2026-11-02 04:40", "2026-12-07 04:40",
                                                   "2027-01-04 04:40"])

    def test_disabled_and_bad_entries_give_nothing(self):
        now = utc(2026, 10, 18)
        self.assertEqual(S.next_runs([eid("03:00", [1], on=False), eid("3:00", [2])], now, MOSCOW), [])

    def test_a_month_of_events(self):
        es = [eid("03:45", [1, 2, 3, 4, 5, 6, 7]), eid("14:00", [6], "full")]
        evs = S.events(es, utc(2026, 11, 1), utc(2026, 12, 1), MOSCOW)
        self.assertEqual(len([v for v in evs if v["mode"] == "quick"]), 30)
        self.assertEqual(len([v for v in evs if v["mode"] == "full"]), 4)
        self.assertEqual(len(set((v["key"], v["mode"]) for v in evs)), 34)


class DstTest(unittest.TestCase):
    """§2.4 on Europe/Berlin: 2026-10-25 (the hour twice) and 2027-03-28 (the hour skipped) — every entry has exactly
    one event; a start of the timer anywhere in [line; line + 30 min] (both appearances of the double hour) finds
    it; a second start of the same event finds nothing (the mark)."""

    TIMES = ("01:50", "02:10", "02:30", "03:05", "03:10", "03:20")
    DAYS = ((2026, 10, 25), (2027, 3, 28))

    def day_events(self, e, y, mo, d):
        evs = S.events([e], utc(y, mo, d) - 86400, utc(y, mo, d) + 2 * 86400, BERLIN)
        return [v for v in evs if v["key"].startswith("%04d-%02d-%02d" % (y, mo, d))]

    def fire_moments(self, e, y, mo, d):
        """Where the timer may start for the event: each appearance of the line (t − 15) on the clock + [0, 30] min;
        the spring day with no line — the one-shot UTC line of ``calendar_lines``."""
        hh, mm = int(e["time"][:2]), int(e["time"][3:])
        wall = utc(y, mo, d, hh, mm) - 15 * 60
        starts = S._occurrences(wall, BERLIN)
        if not starts:
            now = utc(y, mo, d) - 30 * 86400
            one = [l for l in S.calendar_lines([e], BERLIN, now) if l.endswith(" UTC")]
            self.assertEqual(len(one), 1, (e, one))
            starts = [calendar.timegm(time.strptime(one[0][:-4], "%Y-%m-%d %H:%M:%S"))]
        return [s + k * 60 for s in starts for k in range(0, 31, 3)]

    def test_one_event_found_from_every_start(self):
        for y, mo, d in self.DAYS:
            for t in self.TIMES:
                e = eid(t, [7], id="abcdef")
                evs = self.day_events(e, y, mo, d)
                self.assertEqual(len(evs), 1, (y, mo, d, t))
                for at in self.fire_moments(e, y, mo, d):
                    got = S.due([e], at, BERLIN, {})
                    self.assertIsNotNone(got, (y, mo, d, t, time.strftime("%H:%M", time.gmtime(at))))
                    self.assertEqual(got["key"], evs[0]["key"])
                    self.assertIsNone(S.due([e], at, BERLIN, {"abcdef": got["key"]}), "the second start is a no-op")

    def test_the_spring_one_shot_line_only_where_the_line_is_not_on_the_clock(self):
        now = utc(2026, 10, 19)
        for t in self.TIMES:
            one = [l for l in S.calendar_lines([eid(t, [7])], BERLIN, now) if l.endswith(" UTC")]
            hh, mm = int(t[:2]), int(t[3:])
            missing = not S._occurrences(utc(2027, 3, 28, hh, mm) - 900, BERLIN)
            self.assertEqual(bool(one), missing, t)
        self.assertEqual([l for l in S.calendar_lines([eid("02:30", [7])], BERLIN, now) if l.endswith("UTC")],
                         ["2027-03-28 01:15:00 UTC"])

    def test_the_one_shot_line_lives_12_months_and_comes_back_a_year_later(self):
        e = [eid("02:30", [7])]
        self.assertEqual([l for l in S.calendar_lines(e, BERLIN, utc(2026, 4, 1)) if l.endswith("UTC")],
                         ["2027-03-28 01:15:00 UTC"])
        self.assertEqual([l for l in S.calendar_lines(e, BERLIN, utc(2027, 3, 29)) if l.endswith("UTC")],
                         ["2028-03-26 01:15:00 UTC"])
        self.assertEqual([l for l in S.calendar_lines(e, MOSCOW, utc(2026, 4, 1))], ["Sun *-*-* 02:15:00 Europe/Moscow"])

    def test_spring_0105_and_0305_no_double_run_second_missed(self):
        """§2.4 придирка 2: 01:05 and 03:05 are 60 min of real time on 2027-03-28: valid; the long run of the first
        overlaps the window of the second -> the second becomes ``missed`` at the next start, never a double run."""
        since = utc(2027, 3, 27)
        es = [eid("01:05", [7], id="aaaaaa", since=since), eid("03:05", [7], id="bbbbbb", since=since)]
        self.assertEqual(S.validate(es)[1], [])
        first = S.due(es, utc(2027, 3, 28, 0, 5), BERLIN, {})
        self.assertEqual(first["id"], "aaaaaa")
        done = {"aaaaaa": first["key"]}
        # the run of the first lasts past the window of the second (01:05 UTC ± 20); the next start — a day later
        later = utc(2027, 3, 29, 0, 0)
        self.assertIsNone(S.due(es, later, BERLIN, done))
        miss = S.missed(es, later, BERLIN, done, armed_at=since)
        self.assertEqual([(v["id"], v["key"]) for v in miss["rows"]], [("bbbbbb", "2027-03-28 03:05")])
        # in its window it would have been found (never two events at one moment)
        self.assertEqual(S.due(es, utc(2027, 3, 28, 1, 0), BERLIN, done)["id"], "bbbbbb")


class DueTest(unittest.TestCase):

    def test_window_20_minutes(self):
        e = [ent("03:45", [1], id="abcdef")]
        t = utc(2026, 10, 19, 0, 45)                      # Mon 03:45 MSK
        for k in (-20, -15, 0, 15, 20):
            self.assertIsNotNone(S.due(e, t + k * 60, MOSCOW, {}), k)
        for k in (-21, 21, 120):
            self.assertIsNone(S.due(e, t + k * 60, MOSCOW, {}), k)

    def test_across_midnight(self):
        """NS-S6: a start Sun 23:55 runs the event of Mon 00:05; a start Tue 00:03 runs the one of Mon 23:50."""
        a = [ent("00:05", [1], id="aaaaaa")]
        got = S.due(a, utc(2026, 10, 18, 20, 55), MOSCOW, {})          # Sun 23:55 MSK
        self.assertEqual(got["key"], "2026-10-19 00:05")
        b = [ent("23:50", [1], id="bbbbbb")]
        got = S.due(b, utc(2026, 10, 19, 21, 3), MOSCOW, {})           # Tue 00:03 MSK
        self.assertEqual(got["key"], "2026-10-19 23:50")

    def test_daily_entry_late_evening_is_not_already_done(self):
        e = [ent("23:50", [1, 2, 3, 4, 5, 6, 7], id="aaaaaa")]
        got = S.due(e, utc(2026, 10, 20, 20, 41), MOSCOW, {"aaaaaa": "2026-10-19 23:50"})   # Tue 23:41
        self.assertEqual(got["key"], "2026-10-20 23:50")

    def test_key_not_past_the_mark_gives_nothing(self):
        e = [ent("03:45", [1], id="abcdef")]
        t = utc(2026, 10, 19, 0, 45)
        self.assertIsNone(S.due(e, t, MOSCOW, {"abcdef": "2026-10-19 03:45"}))
        self.assertIsNone(S.due(e, t, MOSCOW, {"abcdef": "2026-10-20 03:45"}))
        self.assertIsNotNone(S.due(e, t, MOSCOW, {"abcdef": "2026-10-12 03:45"}))
        self.assertIsNotNone(S.due(e, t, MOSCOW, {"zzz": 5, "abcdef": None}))
        self.assertIsNotNone(S.due(e, t, MOSCOW, "garbage"))

    def test_monthly_8th_gives_nothing(self):
        e = [ent("04:40", [1], id="abcdef", monthly=True)]
        self.assertIsNone(S.due(e, utc(2027, 2, 8, 1, 40), MOSCOW, {}))   # Mon Feb 8, 04:40 MSK
        self.assertIsNotNone(S.due(e, utc(2027, 2, 1, 1, 40), MOSCOW, {}))  # Mon Feb 1

    def test_nearest_of_two(self):
        es = [ent("03:00", [1], id="aaaaaa"), ent("05:00", [1], id="bbbbbb")]
        self.assertEqual(S.due(es, utc(2026, 10, 19, 1, 50), MOSCOW, {})["id"], "bbbbbb")


class MissedTest(unittest.TestCase):

    def test_entries_without_id_never_share_a_mark(self):
        """REV-ns-b10 З-2: the clock takes only entries with an id (the contract: the list of model.bench); two raw
        entries without one give no event, no missed row and no mark «None»."""
        raw = [ent("03:45", [1], since=1), ent("09:45", [1], since=1)]
        now = utc(2026, 10, 19, 0, 40)                                   # Mon 03:40 MSK
        self.assertIsNone(S.due(raw, now, MOSCOW, {}))
        self.assertEqual(S.events(raw, now - 86400, now + 86400, MOSCOW), [])
        miss = S.missed(raw, utc(2026, 10, 27, 0, 0), MOSCOW, {}, armed_at=1)
        self.assertEqual((miss["rows"], miss["done"], miss["older"]), ([], {}, {}))
        self.assertEqual(S.calendar_lines(raw, MOSCOW, now), [])
        with_ids = [dict(raw[0], id="aaaaaa"), dict(raw[1], id="bbbbbb")]
        miss = S.missed(with_ids, utc(2026, 10, 27, 0, 0), MOSCOW, {}, armed_at=1)
        self.assertEqual(miss["done"], {"aaaaaa": "2026-10-26 03:45", "bbbbbb": "2026-10-26 09:45"})



    def test_created_at_noon_for_0345_gives_no_missed(self):
        since = utc(2026, 10, 19, 9, 0)                                  # Mon 12:00 MSK
        e = [ent("03:45", [1, 2, 3, 4, 5, 6, 7], id="aaaaaa", since=since)]
        miss = S.missed(e, utc(2026, 10, 20, 0, 30), MOSCOW, {}, armed_at=1)   # Tue 03:30
        self.assertEqual(miss["rows"], [])

    def test_server_off_two_days(self):
        e = [ent("03:45", [1, 2, 3, 4, 5, 6, 7], id="aaaaaa", since=1)]
        done = {"aaaaaa": "2026-10-19 03:45"}
        miss = S.missed(e, utc(2026, 10, 22, 0, 30), MOSCOW, done, armed_at=1)   # Thu 03:30: Tue and Wed missed
        self.assertEqual([v["key"] for v in miss["rows"]], ["2026-10-20 03:45", "2026-10-21 03:45"])
        self.assertEqual(miss["done"], {"aaaaaa": "2026-10-21 03:45"})

    def test_older_than_seven_one_row(self):
        e = [ent("03:45", [1, 2, 3, 4, 5, 6, 7], id="aaaaaa", since=1)]
        miss = S.missed(e, utc(2026, 10, 31, 12), MOSCOW, {"aaaaaa": "2026-10-19 03:45"}, armed_at=1)
        self.assertEqual(len(miss["rows"]), 7)
        self.assertEqual(miss["older"], {"aaaaaa": 5})

    def test_before_armed_is_no_miss(self):
        e = [ent("03:45", [1, 2, 3, 4, 5, 6, 7], id="aaaaaa", since=1)]
        miss = S.missed(e, utc(2026, 10, 22, 0, 30), MOSCOW, {}, armed_at=utc(2026, 10, 21, 12))
        self.assertEqual(miss["rows"], [])


class CalendarLinesTest(unittest.TestCase):

    def test_line_is_t_minus_15_with_the_day_carried(self):
        self.assertEqual(S.calendar_lines([eid("00:05", [1])], MOSCOW, 0), ["Sun *-*-* 23:50:00 Europe/Moscow"])
        self.assertEqual(S.calendar_lines([eid("03:45", [1, 2, 3, 4, 5])], MOSCOW, 0),
                         ["Mon,Tue,Wed,Thu,Fri *-*-* 03:30:00 Europe/Moscow"])

    def test_days_fold(self):
        lines = S.calendar_lines([eid("03:45", [1, 3]), eid("03:45", [5], "full"), eid("12:00", [7])], MOSCOW, 0)
        self.assertEqual(lines, ["Mon,Wed,Fri *-*-* 03:30:00 Europe/Moscow", "Sun *-*-* 11:45:00 Europe/Moscow"])

    def test_fallback_utc(self):
        """§2.4: tz.py could not read the zone — the suffix UTC, the time −3 h with the same carry of the day."""
        self.assertEqual(S.calendar_lines([eid("01:00", [1])], FALLBACK, 0), ["Sun *-*-* 21:45:00 UTC"])
        self.assertEqual(S.calendar_lines([eid("04:10", [1])], FALLBACK, 0), ["Mon *-*-* 00:55:00 UTC"])
        self.assertEqual(S.calendar_lines([eid("02:00", [7])], FALLBACK, 0), ["Sat *-*-* 22:45:00 UTC"])
        self.assertEqual(S.calendar_lines([eid("01:00", [1, 2]), eid("03:00", [7])], FALLBACK, 0),
                         ["Mon,Sun *-*-* 21:45:00 UTC", "Sat *-*-* 23:45:00 UTC"])

    def test_monthly(self):
        self.assertEqual(S.calendar_lines([eid("04:40", [1], monthly=True)], MOSCOW, 0),
                         ["Mon *-*-01..07 04:25:00 Europe/Moscow"])
        # carried over midnight: the Sunday before the first Monday may be the last day of the month before
        self.assertEqual(S.calendar_lines([eid("00:05", [1], monthly=True)], MOSCOW, 0),
                         ["Sun *-*-01..06 23:50:00 Europe/Moscow", "Sun *-*~01 23:50:00 Europe/Moscow"])

    def test_bad_input_never_reaches_a_line(self):
        """NS-S7: a newline, «;» or «[» in the time — an error ``time``, nothing of it in the output."""
        for t in ("03:45\nExecStart=/bin/sh", "03:45;", "[03:45", "03:45\n"):
            self.assertEqual(S.calendar_lines([eid(t, [1])], MOSCOW, 0), [])
        lines = S.calendar_lines([eid("03:45\nX", [1]), eid("05:00", [1])], MOSCOW, 0)
        self.assertEqual(lines, ["Mon *-*-* 04:45:00 Europe/Moscow"])

    def test_zone_name_by_fullmatch(self):
        bad = tz.Zone("Europe/Moscow\n", types=[(10800, False)])
        with self.assertRaises(ValueError):
            S.calendar_lines([eid("03:00", [1])], bad, 0)

    def test_lines_and_events_agree(self):
        """The timer and the events come from one function: every event of a month is aimed at by a line."""
        es = [eid("00:05", [1, 4]), eid("13:20", [6], "full"), eid("04:40", [3], monthly=True)]
        lines = S.calendar_lines(es, MOSCOW, 0)
        for v in S.events(es, utc(2026, 11, 1), utc(2027, 1, 1), MOSCOW):
            at = time.gmtime(v["ts"] + 3 * 3600 - 15 * 60)
            dn = S.DAY_NAMES[at.tm_wday]
            hm = time.strftime("%H:%M:00", at)
            self.assertTrue(any(l.split()[0].count(dn) and hm in l for l in lines), (v["key"], lines))


class ProposeTest(unittest.TestCase):

    def rows(self, days, quiet_hour, now, zone=MOSCOW):
        out = []
        for h in range(days * 24):
            ts = now - (h + 1) * 3600
            lh = tz.local(ts, zone).tm_hour
            b = 10 if lh == quiet_hour else 10 ** 9
            out.append({"ts": ts, "rx": b, "tx": b})
        return out

    def test_quiet_hour_of_h1(self):
        now = utc(2026, 10, 19, 12)
        got = S.propose(self.rows(14, 3, now), now, MOSCOW, rng=random.Random(7))
        q, f = got["entries"]
        self.assertEqual(got["basis"], "h1")
        self.assertEqual(q["time"][:2], "03")
        self.assertEqual(f["time"], q["time"])
        self.assertEqual((q["mode"], q["monthly"], f["mode"], f["monthly"]), ("quick", False, "full", True))
        self.assertNotEqual(q["days"], f["days"])
        self.assertTrue(q["quiet"] and f["quiet"])
        self.assertEqual(S.validate(got["entries"])[1], [])

    def test_missing_hours_are_not_quiet(self):
        """An hour with no rows (the collector was down) is not «the quietest»; the mean, not the sum, decides."""
        now = utc(2026, 10, 19, 12)
        rows = [r for r in self.rows(14, 3, now) if tz.local(r["ts"], MOSCOW).tm_hour != 7]
        self.assertEqual(S.propose(rows, now, MOSCOW, rng=random.Random(1))["entries"][0]["time"][:2], "03")

    def test_fewer_than_7_days_default_night(self):
        now = utc(2026, 10, 19, 12)
        for seed in range(30):
            got = S.propose(self.rows(5, 13, now), now, MOSCOW, rng=random.Random(seed))
            q, f = got["entries"]
            self.assertEqual(got["basis"], "default")
            self.assertIn(q["time"][:2], ("04", "05"))
            self.assertNotEqual(q["days"], f["days"])
            self.assertEqual(S.validate(got["entries"])[1], [])
        self.assertEqual(S.propose(None, now, MOSCOW, rng=random.Random(1))["basis"], "default")

    def test_minutes_differ_by_node(self):
        now = utc(2026, 10, 19, 12)
        times = set(S.propose([], now, MOSCOW, rng=random.Random(s))["entries"][0]["time"] for s in range(20))
        self.assertGreater(len(times), 5)

    def test_price_before_adding(self):
        now = utc(2026, 10, 19, 12)
        got = S.propose([], now, MOSCOW, rng=random.Random(3), ctx={"pkgs": "yes", "history": []})
        self.assertEqual(got["cost"]["week"]["quick"], 1.0)
        self.assertGreater(got["cost"]["month"]["rx_mb"], got["cost_quick"]["month"]["rx_mb"])
        self.assertAlmostEqual(got["cost"]["per_run"]["full"]["rx_mb"] + got["cost"]["per_run"]["full"]["tx_mb"],
                               4000.03)


class CheckSavedTest(unittest.TestCase):

    def test_good_list_needs_no_fix(self):
        raw = [ent("03:00", [1], id="aaaaaa", on=True, quiet=True, monthly=False, since=1, off_why=None)]
        self.assertEqual(S.check_saved(raw)[1], [])
        self.assertEqual(S.check_saved([ent("03:00", [1], id="aaaaaa")])[1], [], "the defaults are not written")

    def test_bad_entry_off_and_stable(self):
        raw = [ent("03:45\nExecStart=x", [1], id="aaaaaa"), ent("05:00", [1], id="bbbbbb", on=True,
                                                                off_why=None)]
        norm, fix = S.check_saved(raw)
        self.assertEqual(fix, [{"op": "set", "i": 0, "fields": {"on": False, "off_why": "time"}}])
        fixed = S.apply_fix(raw, fix)
        self.assertEqual(S.check_saved(fixed)[1], [], "a second read finds nothing")

    def test_ids_given_stably(self):
        raw = [ent("03:00", [1]), ent("06:00", [1], id="BAD")]
        n1, f1 = S.check_saved(raw)
        n2, _f2 = S.check_saved(raw)
        self.assertEqual([e["id"] for e in n1], [e["id"] for e in n2])
        fixed = S.apply_fix([dict(r) for r in raw], f1)
        self.assertEqual(S.check_saved(fixed)[1], [])
        self.assertFalse(S.check_saved(fixed)[0][1]["on"])

    def test_not_a_list_reset_and_past_12_dropped(self):
        self.assertEqual(S.check_saved("x"), ([], [{"op": "reset"}]))
        raw = [ent("%02d:00" % (2 * i), [2], on=False, id="%06x" % i) for i in range(12)] + ["junk", ent("01:00", [3])]
        norm, fix = S.check_saved(raw)
        self.assertEqual(len(norm), 12)
        self.assertEqual([f["i"] for f in fix if f["op"] == "drop"], [12, 13])
        self.assertEqual(len(S.apply_fix(raw, fix)), 12)


if __name__ == "__main__":
    unittest.main()
