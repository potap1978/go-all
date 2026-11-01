"""B10: the price of the schedule — ``bench.cost.estimate`` (SPEC-node-score-schedule-share §2.3, §2.8 п. 4,
§11.1 п. 2, 26)."""

import unittest

from kaskad.bench import cost as C


def ent(t, days, mode="quick", **kw):
    e = {"time": t, "mode": mode, "days": list(days)}
    e.update(kw)
    return e


def run(mode, rx_mb, tx_mb, cpu_s=60, by="schedule", status="ok", ts=1):
    return {"mode": mode, "by": by, "status": status, "rx": int(rx_mb * C.MB), "tx": int(tx_mb * C.MB),
            "cpu_s": cpu_s, "ts": ts}


WEEKLY_QUICK = [ent("03:00", [1])]


def codes(est):
    return [n["code"] for n in est["notes"]]


class CapsTest(unittest.TestCase):

    def test_per_run_caps(self):
        """§11.1 п. 26: «до 500 МБ» quick, «до 4 ГБ» full (+ probes and DNS < 30 KB)."""
        est = C.estimate([], {"pkgs": "yes"})
        q, f = est["per_run"]["quick"], est["per_run"]["full"]
        self.assertAlmostEqual(q["rx_mb"] + q["tx_mb"], 500.03)
        self.assertAlmostEqual(f["rx_mb"] + f["tx_mb"], 4000.03)
        self.assertEqual((q["cpu_min"], f["cpu_min"]), (0.9, 1.0))
        self.assertEqual(est["basis"], "caps")

    def test_weekly_and_monthly_counts(self):
        est = C.estimate([ent("03:00", [1, 4]), ent("05:00", [3], "full", monthly=True)], {"pkgs": "yes"})
        self.assertEqual(est["week"]["quick"], 2.0)
        self.assertAlmostEqual(est["week"]["full"], 7 / 30.4, places=2)
        # a month: 2 × 30.4 / 7 quick + 1 full
        want = 2 * 30.4 / 7 * 250.03 + 2000.03
        self.assertAlmostEqual(est["month"]["rx_mb"], want, places=1)
        self.assertAlmostEqual(est["day"]["rx_mb"], want / 30.4, places=1)

    def test_disabled_and_bad_entries_cost_nothing(self):
        est = C.estimate([ent("03:00", [1], on=False), ent("3:00", [2])], {"pkgs": "yes"})
        self.assertEqual(est["month"], {"rx_mb": 0.0, "tx_mb": 0.0, "cpu_min": 0.0})
        self.assertIsNone(est["retry"])

    def test_no_pkgs_less_cpu(self):
        a = C.estimate(WEEKLY_QUICK, {"pkgs": "yes"})
        b = C.estimate(WEEKLY_QUICK, {"pkgs": None})
        self.assertLess(b["per_run"]["quick"]["cpu_min"], a["per_run"]["quick"]["cpu_min"])
        self.assertLess(b["month"]["cpu_min"], a["month"]["cpu_min"])
        self.assertIn("no_pkgs", codes(b))
        self.assertNotIn("no_pkgs", codes(a))

    def test_ru_no_upload(self):
        est = C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "ru": True})
        self.assertEqual(est["per_run"]["quick"]["tx_mb"], 0)
        self.assertEqual(est["month"]["tx_mb"], 0)
        self.assertIn("no_upload_ru", codes(est))

    def test_ru_with_the_allowed_point(self):
        est = C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "ru": True, "ru_point": True})
        self.assertGreater(est["per_run"]["quick"]["tx_mb"], 0)
        self.assertIn("upload_ru_point", codes(est))
        est = C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "ru": True, "ru_point": {"quick": 100}})
        self.assertEqual(est["per_run"]["quick"]["tx_mb"], 100)

    def test_bad_ctx(self):
        for ctx in (None, "x", [], {"history": "x"}, {"history": [1, None, {"by": "schedule"}]}):
            est = C.estimate(WEEKLY_QUICK, ctx)
            self.assertEqual(est["basis"], "caps", ctx)


class PerDayTest(unittest.TestCase):
    """6C (BRIEF-P4-a): ``max_per_day`` — «до N проверок в день» of the summary, ``per_day_even`` — «N в день»."""

    def test_max_per_day(self):
        est = C.estimate([ent("02:00", [1, 2, 3, 4, 5, 6, 7]), ent("09:00", [1, 3]), ent("16:00", [1], mode="full"),
                          ent("21:00", [2], on=False)], {})
        self.assertEqual((est["max_per_day"], est["per_day_even"]), (3, False))
        est = C.estimate([ent("02:00", [1, 2, 3, 4, 5, 6, 7]), ent("09:00", [1, 2, 3, 4, 5, 6, 7], mode="full")], {})
        self.assertEqual((est["max_per_day"], est["per_day_even"]), (2, True))
        est = C.estimate([ent("04:30", [1], mode="full", monthly=True)], {})       # a monthly one — on its day
        self.assertEqual((est["max_per_day"], est["per_day_even"]), (1, False))
        self.assertEqual((C.estimate([], {})["max_per_day"], C.estimate([], {})["per_day_even"]), (0, True))


class MeasuredTest(unittest.TestCase):

    def test_three_scheduled_runs_make_it_measured(self):
        hist = [run("quick", 100, 80, ts=i) for i in range(3)]
        est = C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "history": hist})
        self.assertEqual(est["basis"], "measured")
        self.assertEqual(est["per_run"]["quick"], {"rx_mb": 100.0, "tx_mb": 80.0, "cpu_min": 1.0})

    def test_two_are_not_enough(self):
        hist = [run("quick", 100, 80, ts=i) for i in range(2)]
        self.assertEqual(C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "history": hist})["basis"], "caps")

    def test_median_of_the_last_five(self):
        vals = [500, 10, 20, 30, 40, 50]                 # the oldest (ts 0) is 500 and out of the last 5
        hist = [run("quick", v, v, ts=i) for i, v in enumerate(vals)]
        est = C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "history": hist})
        self.assertEqual(est["per_run"]["quick"]["rx_mb"], 30.0)

    def test_manual_runs_do_not_move_it(self):
        """A manual full check with 8b (up to + 2 GB) is another set of tests: out of the median."""
        hist = [run("full", 1500, 1500, ts=i) for i in range(3)]
        base = C.estimate([ent("03:00", [1], "full")], {"pkgs": "yes", "history": hist})
        hist += [run("full", 3500, 3500, by=b, ts=10 + i) for i, b in enumerate(("panel", "bot", "cli", "mcp", "ssh"))]
        hist += [run("full", 9000, 9000, status="failed", ts=20), run("full", 9000, 9000, status="skipped", ts=21),
                 run("full", 9000, 9000, by="auto", ts=22)]
        est = C.estimate([ent("03:00", [1], "full")], {"pkgs": "yes", "history": hist})
        self.assertEqual(est["per_run"]["full"], base["per_run"]["full"])
        self.assertEqual(est["per_run"]["full"]["rx_mb"], 1500.0)

    def test_several_manual_runs_newer_than_the_schedule(self):
        """REV-ns-b10 П-4: three manual checks of one kind, newer than the scheduled ones — still out of the median
        (one of them alone would hide in the median of five)."""
        hist = [run("full", 1500, 1500, ts=i) for i in range(3)]
        hist += [run("full", 3500, 3500, by="panel", ts=10 + i) for i in range(3)]
        est = C.estimate([ent("03:00", [1], "full")], {"pkgs": "yes", "history": hist})
        self.assertEqual(est["per_run"]["full"]["rx_mb"], 1500.0)

    def test_partial_counts(self):
        hist = [run("quick", 100, 80, status="partial", ts=i) for i in range(3)]
        self.assertEqual(C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "history": hist})["basis"], "measured")

    def test_mixed_modes_basis_caps(self):
        hist = [run("quick", 100, 80, ts=i) for i in range(3)]
        est = C.estimate([ent("03:00", [1]), ent("09:00", [1], "full")], {"pkgs": "yes", "history": hist})
        self.assertEqual(est["basis_mode"], {"quick": "measured", "full": "caps"})
        self.assertEqual(est["basis"], "caps")

    def test_no_cpu_of_the_unit_caps_cpu(self):
        hist = [run("quick", 100, 80, cpu_s=None, ts=i) for i in range(3)]
        est = C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "history": hist})
        self.assertEqual(est["per_run"]["quick"]["cpu_min"], 0.9)


class HeavyTest(unittest.TestCase):
    """§11.1 п. 2: a month ≥ 100 GB or ≥ 10 % of the tariff — what comes first."""

    def test_edges(self):
        self.assertFalse(C.heavy(99900, None))
        self.assertTrue(C.heavy(100000, None))
        self.assertFalse(C.heavy(49500, 500))            # 9.9 % of 500 GB
        self.assertTrue(C.heavy(50000, 500))             # 10 %
        self.assertTrue(C.heavy(100000, 5000))           # 2 % of the tariff, but 100 GB
        self.assertFalse(C.heavy(99900, 0))

    def test_note_by_the_schedule(self):
        six_full = [ent("%02d:00" % (4 * i), [d], "full") for i in range(2) for d in range(1, 8)]
        est = C.estimate(six_full, {"pkgs": "yes"})          # 14 full a week ≈ 243 GB a month
        self.assertIn("heavy", codes(est))
        self.assertNotIn("heavy", codes(C.estimate(WEEKLY_QUICK, {"pkgs": "yes"})))

    def test_tariff_share(self):
        est = C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "tariff_traffic_gb": 1000})
        share = [n for n in est["notes"] if n["code"] == "tariff_share"]
        self.assertEqual(len(share), 1)
        self.assertEqual(share[0]["args"]["pct"], 0.2)
        from kaskad.i18n import use_lang
        with use_lang("ru"):
            got = C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "tariff_traffic_gb": 1000})["notes"]
        self.assertIn("0,2%", [n for n in got if n["code"] == "tariff_share"][0]["text"])
        est = C.estimate([ent("03:00", [1, 2, 3, 4, 5, 6, 7])], {"pkgs": "yes", "tariff_traffic_gb": 100})
        self.assertIn("heavy", codes(est))                # 15.2 GB of 100 GB = 15 %

    def test_tiny_tariff_share_is_not_zero(self):
        """REV-ns-b10 П-3: 32 TB, a quick check a week — «< 0,1%», never «≈ 0,0%»."""
        from kaskad.i18n import use_lang
        for lang, want in (("ru", "< 0,1% трафика тарифа в месяц."), ("en", "< 0.1% of the tariff traffic a month.")):
            with use_lang(lang):
                notes = C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "tariff_traffic_gb": 32000})["notes"]
            self.assertNotIn("tariff_share", codes({"notes": notes}))
            self.assertEqual([n["text"] for n in notes if n["code"] == "tariff_share_small"], [want])


class RetryLineTest(unittest.TestCase):

    def test_retry_worst_case(self):
        est = C.estimate([ent("03:00", [1])], {"pkgs": "yes"})
        self.assertEqual(est["retry"], {"per_day": 2, "max_mb": 1000})
        est = C.estimate([ent("03:00", [1]), ent("09:00", [1], "full")], {"pkgs": "yes"})
        self.assertEqual(est["retry"]["max_mb"], 8000)
        self.assertIn("retry", codes(est))

    def test_retry_off(self):
        est = C.estimate(WEEKLY_QUICK, {"pkgs": "yes", "retry": False})
        self.assertIsNone(est["retry"])
        self.assertNotIn("retry", codes(est))

    def test_notes_have_text_in_both_languages(self):
        from kaskad.i18n import tr
        for c in ("no_upload_ru", "upload_ru_point", "no_pkgs", "tariff_share", "tariff_share_small", "heavy",
                  "retry"):
            for lang in ("ru", "en"):
                self.assertNotEqual(tr("bench.cost.note." + c, lang, pct="1", gb="1", n=2, mb=1),
                                    "bench.cost.note." + c)


if __name__ == "__main__":
    unittest.main()
