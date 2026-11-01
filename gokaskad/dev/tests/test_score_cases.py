"""B4, круг 2 (k3/stand/logs/REV-ns-b4.md): the cases of the strict review of lib/kaskad/score.py — a partial
automatic repeat, nothing after ``now``, the window of the base, one event per link, the reason of a yellow day,
«now» older than 30 d, the kind «drop», non-finite values, the surviving mutations, stale of the probes, now_day,
the loss shown by v. Every test here was red on the round-1 code (cf7bce85). Круг 3 (REV-ns-b4-2.md) — the classes
after the marked line: red on the round-2 code (d2af76d5) or under its surviving mutations (R5, R10, R19, R20, N6).
After the cut of 23.10 (NS-D30–NS-D34): the totals, the score of a day and the conditions 300/100/fall are gone — the
same cases are checked by the raw numbers, rel and the one condition of a drop (rel <= 50, tests/test_score_own.py)."""

import unittest

from kaskad import score as S
from tests.score_hist import ABS, FIX, S4, T, Hist, at, day_of, hhmm_of, iso

RULE = dict(S.DEV_RULE)
NO_AES = next(c["vals"] for c in FIX["abs"] if c["name"] == "S4 без AES-NI (AES 400, ChaCha 2400)")


def dn(x):
    return {"net_down": x}


def link(v, lid):
    return next(x for x in v["links"] if x["id"] == lid)


class PartialRepeat(unittest.TestCase):
    """REV 1: a repeat holds only the tests of the fallen links (§2.8 п. 2); it is not «the last check» by itself."""

    def test_s4_upload(self):
        h = Hist()
        for d in (0, 7, 14):
            h.add(d, dict(S4))
        orig = h.add(21, dict(S4, net_up=5))
        rep = h.add(21, {"net_up": 5}, hh=6, retry=True)
        v = h.view(at(21, 12))
        sm = v["summary"]
        self.assertEqual(sm["last_id"], orig)            # the repeat is not «the last check» by itself
        self.assertEqual(sm["last_at"], at(21))          # the original's time, not of its repeat (REV-ns-p4b З-1)
        up, down = link(v, "up"), link(v, "down")
        self.assertEqual((up["fresh_raw"], up["fresh_n"], up["now_id"]), (5, 3, orig))   # 1300, 5, 5 -> 5
        self.assertEqual((down["fresh_raw"], down["now_id"]), (S4["net_down"], orig))
        self.assertNotIn(rep, [down["now_id"]])

    def scenario(self, partial):
        x = 100                                       # a low upload (was the raw value of the link score 240)
        h = Hist()
        for d in (0, 7, 14):
            h.add(d, dict(NO_AES))
        h.add(21, dict(NO_AES, net_up=x))
        h.add(28, dict(NO_AES, net_up=x))
        h.add(28, {"net_up": x} if partial else dict(NO_AES, net_up=x), hh=6, retry=True)
        return h, h.view(at(28, 12))

    def test_s4_without_aes_ni(self):
        """S4 без AES-NI: the partial repeat gives the same as the full one — _cpu_flags [] of the original (ChaCha
        2400, not AES 400), the links by the original with the repeat."""
        hp, vp = self.scenario(True)
        hf, vf = self.scenario(False)
        for v in (vp, vf):
            cr = link(v, "crypto")
            self.assertEqual((cr["test"], cr["fresh_raw"]), ("chacha_1t", NO_AES["chacha_1t"]))
            self.assertEqual([p["test"] for p in cr["parts"]], ["chacha_1t"])
        self.assertEqual(S.fresh(ABS, hp.runs, at(28, 12), day_of=day_of)["vals"]["_cpu_flags"], [])
        cp = S.check_vals(ABS, hp.runs, hp.runs[-1])                          # the pair: the original + its repeat
        self.assertEqual(cp, S.check_vals(ABS, hf.runs, hf.runs[-1]))
        self.assertEqual((cp["_cpu_flags"], cp["net_up"]["v"]), ([], 100))
        keys = ("rel", "test", "fresh_raw", "usual_raw", "now_day", "dev", "dev_pending_kind")
        # the link of the repeat's test is the same; the others are not compared: a full repeat measures them too, so
        # their history has one more point (another window, another «обычно»)
        self.assertEqual([link(vp, "up")[k] for k in keys], [link(vf, "up")[k] for k in keys])

    def test_best_of_two_and_rest_of_original(self):
        """check_vals: the tests of the repeat — the better of the two, the others — of the original."""
        h = Hist()
        h.add(0, dict(S4))
        h.add(7, dict(S4, net_up=5))
        h.add(7, {"net_up": 1300}, hh=6, retry=True)          # a false drop
        cv = S.check_vals(ABS, h.runs, h.runs[-1])
        self.assertEqual(cv["net_up"]["v"], 1300)
        self.assertEqual(cv["net_down"]["v"], S4["net_down"])
        self.assertEqual(S.check_vals(ABS, h.runs, h.runs[-2], now=at(7, 5))["net_up"]["v"], 5)   # repeat after now
        self.assertEqual(link(h.view(at(7, 12)), "up")["now_id"], h.runs[-1]["id"])   # the pair shows the repeat's
        h.runs[-1]["vals"] = {"net_up": 3}                    # a worse repeat: the original stays
        self.assertEqual(S.check_vals(ABS, h.runs, h.runs[-1])["net_up"]["v"], 5)


class NothingAfterNow(unittest.TestCase):
    """REV 2: epochs, events and probe days after ``now`` do not exist yet (/api/host/day/<date> — the end of a day)."""

    def test_epoch_after_now(self):
        h = Hist()
        for d in range(0, 70, 7):
            h.add(d, dict(S4))
        a = h.view(at(35, 23, 59))
        b = h.view(at(35, 23, 59), epochs=[{"ts": at(63, 10), "kind": "tariff"}])
        self.assertEqual(b["summary"]["base_since"], [])
        self.assertEqual(b["summary"]["rel_total"], a["summary"]["rel_total"])
        self.assertIsNotNone(a["summary"]["rel_total"])
        self.assertEqual([(x["id"], x["rel"], x["fresh_raw"], x["usual_raw"]) for x in b["links"]],
                         [(x["id"], x["rel"], x["fresh_raw"], x["usual_raw"]) for x in a["links"]])
        self.assertEqual(S.epoch_since(ABS, [{"ts": at(63), "kind": "tariff"}], day_of, now=at(35)), {})

    def test_events_kept_before_a_later_epoch(self):
        h = Hist()
        d = h.usual(S4, n=6)
        h.add(d, dict(S4, net_up=5))
        h.add(d, dict(S4, net_up=5), hh=6, retry=True)
        h.add(d + 7, dict(S4, net_up=5))
        plain = [(e["link"], e["day"]) for e in h.events()]
        later = [(e["link"], e["day"]) for e in h.events(epochs=[{"ts": at(d + 9, 10), "kind": "tariff"}])]
        self.assertTrue(plain)
        self.assertEqual(later, plain)

    def test_events_and_probe_days_after_now(self):
        h = Hist()
        h.add(0, dict(S4))
        rtt = [{"day": iso(1 + i), "v": 20 + (i % 3), "hours": 24} for i in range(20)]
        late = [{"day": iso(21 + i), "v": 60, "hours": 24} for i in range(3)]
        now = at(21, 0) - 1                                   # the end of day 20
        a = link(h.view(now, probes=[], probe_days={"rtt": rtt}), "net_q")
        b = link(h.view(now, probes=[], probe_days={"rtt": rtt + late}), "net_q")
        self.assertEqual((b["dev"], b["usual_n"]), (a["dev"], a["usual_n"]))
        ev = [{"link": "down", "ts": at(25), "day": iso(0), "auto": True, "cond": "rel"}]
        self.assertIsNone(link(h.view(at(0, 23), events=ev), "down")["dev_pending_kind"])


class Window(unittest.TestCase):
    def test_base_from_the_first_day_of_the_window(self):
        """REV 3 (NS-D25): 3 values on day 150, one on day 100 — the window is days 100 and 150, the base — 60 d
        before day 100 (5 weekly points), not before day 150."""
        h = Hist()
        for i, x in enumerate([600, 620, 640, 660, 700]):
            h.add(50 + 7 * i, {"net_down": x})
        h.add(100, {"net_down": 540})
        for hh in (2, 4, 6):
            h.add(150, {"net_down": 560}, hh=hh, by="panel")
        r = h.link(at(150, 12), "down")
        self.assertEqual((r["dev"], r["usual_n"]), ("down", 5))


class OneEventPerLink(unittest.TestCase):
    def test_original_event_and_its_repeat(self):
        """REV 4: fresh 700, a check 100 without a repeat, the same day another 100 -> event; its repeat 100 — no
        second one (two checks of one day: no ▼, so the kind is «drop»)."""
        h = Hist()
        d = h.usual(dn(700), n=6)
        h.add(d, dn(100))
        orig = h.add(d, dn(100), hh=12, by="panel")
        h.add(d, dn(100), hh=14, retry=True)
        ev = [e for e in h.events() if e["link"] == "down"]
        self.assertEqual([(e["run"], e["auto"]) for e in ev], [(orig, False)])
        self.assertEqual(h.link(at(d, 20), "down")["dev_pending_kind"], "drop")


class Reasons(unittest.TestCase):
    D = {"hours": 24, "test": True, "offline_min": 0, "lost": 0, "n": 0, "alarm_min": 0, "warn_min": 0}

    def test_link_down_args(self):
        """REV 5 (NS C6): reason link_down {link, now, usual, unit} — raw values of the test that gave the ▼."""
        h = Hist()
        d = h.series("net_down", [600, 620, 640, 660, 700, 640, 540])
        h.add(d, {"net_down": 560})
        v = h.view(at(d + 1, 0) - 1)
        tested = S.tested_links(ABS, h.runs, iso(d), day_of)
        c = S.day_color(dict(self.D), S.day_devs(v, tested), [])
        self.assertEqual((c[0], c[2]["code"]), ("yellow", "link_down"))
        a = c[2]["args"]
        self.assertEqual((a["link"], a["unit"]), ("net_bw", "Мбит/с"))   # the first ▼ in the order of the links
        self.assertEqual([x["link"] for x in S.day_devs(v, tested)], ["net_bw", "down"])
        self.assertAlmostEqual(a["now"], 560, places=6)
        self.assertAlmostEqual(a["usual"], 640, places=6)

    def test_link_down_args_of_a_group(self):
        """A group link (net_bw): the args of its dev_test, the test with the largest |d| (REV 9: dev_test)."""
        h = Hist()
        for i, (x, y) in enumerate([(600, 600), (620, 620), (640, 640), (660, 660), (700, 700)]):
            h.add(7 * i, {"net_down": x, "net_up": y})
        h.add(35, {"net_down": 500, "net_up": 400})
        h.add(42, {"net_down": 520, "net_up": 420})
        v = h.view(at(42, 12))
        row = link(v, "net_bw")
        self.assertEqual((row["dev"], row["dev_test"]), ("down", "net_up"))
        self.assertAlmostEqual(v["dev_args"]["net_bw"]["now"], 420, places=6)


class NowAge(unittest.TestCase):
    def test_now_older_than_30_days(self):
        """REV 6 (NS §6.2): a link without a measurement for 30 d — now: null «не измерено» (now_id, now_day)."""
        h = Hist()
        rid = h.add(0, dict(S4))
        self.assertTrue(all(x["now_id"] == rid and x["now_day"] == iso(0) for x in h.view(at(0) + 30 * S.DAY)["links"]))
        self.assertTrue(all(x["now_id"] is None and x["now_day"] is None
                            for x in h.view(at(0) + 30 * S.DAY + 60)["links"]))

    def test_now_day(self):
        """REV 11 (NS §6.2, /api/host/day/<date>): now_day — the day of the check (a repeat — of its original)."""
        h = Hist()
        h.add(0, dict(S4))
        h.add(6, dict(S4, net_up=5), hh=23, mm=30)
        h.add(7, {"net_up": 5}, hh=1, mm=10, retry=True)
        v = h.view(at(7, 12))
        self.assertEqual((link(v, "up")["now_day"], link(v, "down")["now_day"]), (iso(6), iso(6)))
        self.assertEqual(link(h.view(at(3)), "up")["now_day"], iso(0))


class KindDrop(unittest.TestCase):
    def test_event_without_a_repeat(self):
        """REV 7: §2.8 п. 8 «свежие …, проверки 600 и 580 при обычно 1300» — the event comes from the checks, not from
        a repeat: the kind is «drop», not «retry_confirmed» (two checks of one day: no ▼)."""
        h = Hist()
        d = h.usual(S4, n=6)
        h.add(d, dict(S4, net_up=600))
        h.add(d, dict(S4, net_up=580), hh=12, by="panel")
        ev = [e for e in h.events() if e["link"] == "up"]
        self.assertEqual([(e["cond"], e["auto"], e["now"]) for e in ev], [("rel", False, 46)])        # 600 of 1300
        r = h.link(at(d, 20), "up")
        self.assertEqual((r["dev"], r["dev_pending"], r["dev_pending_kind"]), (None, "down", "drop"))

    def test_event_of_a_repeat(self):
        h = Hist()
        d = h.usual(S4, n=6)
        h.add(d, dict(S4, net_up=5))
        h.add(d, {"net_up": 5}, hh=6, retry=True)
        self.assertEqual(h.link(at(d, 12), "up")["dev_pending_kind"], "retry_confirmed")


class NonFinite(unittest.TestCase):
    """REV 8: NaN and ±inf are not values — «не измерено»; view() does not fail."""

    def test_view(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            for tid in ("net_down", "cpu_1t"):
                runs = [{"id": "a", "ts": at(0), "status": "ok", "vals": dict(S4, **{tid: bad})}]
                v = S.view(ABS, runs, at(1), day_of=day_of, hhmm_of=hhmm_of)
                ref = S.view(ABS, [dict(runs[0], vals={k: m for k, m in S4.items() if k != tid})], at(1),
                             day_of=day_of, hhmm_of=hhmm_of)
                self.assertEqual(v["summary"], ref["summary"], (tid, bad))
                self.assertEqual(v["links"], ref["links"], (tid, bad))

    def test_helpers(self):
        t = T["net_down"]
        self.assertIsNone(S.rel_of(t, float("nan"), 600))
        self.assertIsNone(S.rel_of(t, 600, float("inf")))
        self.assertIsNone(S.usable({"ts": 0, "vals": {"net_down": float("nan")}}, "net_down"))
        self.assertIsNone(S.usable({"ts": 0, "vals": {"loss": {"v": 0.1, "lo": float("nan"), "n": 20000}}}, "loss"))
        self.assertIsNone(S.load_correct(400, float("nan"), 900))
        vals, _days = S.fresh_probes([{"day": iso(1), "hours": 24, "rtt": float("nan")},
                                      {"day": iso(0), "hours": 24, "rtt": 20}])
        self.assertEqual(vals["rtt"]["v"], 20)


class Mutations(unittest.TestCase):
    """REV 9: the mutations of the round-1 tests that survived (the boundary «700 → 400 — нет» of the scale is now
    rel 51 / 50 — tests/test_score_own.py DropTest)."""

    def test_tested_links_repeat_after_midnight(self):
        """A repeat at 01:10 belongs to the day of its original (23:30): it does not make the next day «tested»."""
        h = Hist()
        h.add(6, dict(S4, net_up=5), hh=23, mm=30)
        h.add(7, {"net_up": 5}, hh=1, mm=10, retry=True)
        self.assertIn("up", S.tested_links(ABS, h.runs, iso(6), day_of))
        self.assertNotIn("up", S.tested_links(ABS, h.runs, iso(7), day_of))

    def test_common_floor_of_loss(self):
        """NS-D25 З-8: base days at the floor of 100 000 probes, the last two at the floor of 1000 — no ▼ (all at the
        largest floor); without the common floor it would be ▼."""
        t = T["loss"]
        base = [(i, S._u(t, 0.005), 0.005, 0.002) for i in range(5)]
        last = [(5, S._u(t, 0.02), 0.1, 0.0), (6, S._u(t, 0.02), 0.1, 0.0)]
        r = S.dev_core(t, base + last, upper=5, lower=-60, rule=RULE)
        self.assertEqual((r["n"], r["dev"], r["pending"]), (5, None, None))

    def test_first_is_24_hours(self):
        h = Hist()
        h.add(0, dict(S4))
        self.assertTrue(h.view(at(0) + S.DAY - 60)["summary"]["first"])
        self.assertFalse(h.view(at(0) + S.DAY)["summary"]["first"])


class StaleProbes(unittest.TestCase):
    def test_probe_links_keep_fresh(self):
        """REV 10: stale (no check for 45 d) nulls the fresh values (and rel) of the links of the tests only."""
        h = Hist()
        h.add(0, dict(S4))
        probes = [{"day": iso(59 - i), "hours": 24, "rtt": 20, "jitter": {"v": 2, "n": 5000}, "steal_p95": 0.5,
                   "loss": {"a": {"n": 30000, "k": 3, "e": 3}, "b": {"n": 30000, "k": 2, "e": 2}}} for i in range(3)]
        v = h.view(at(60, 1), probes=probes)
        self.assertTrue(v["summary"]["stale"])
        for x in v["links"]:
            got = [p["fresh_raw"] for p in x["parts"] if p["fresh_raw"] is not None]
            if x["source"] == "probes":
                self.assertTrue(got, x["id"])
            else:
                self.assertEqual((got, x["fresh_raw"], x["rel"]), ([], None, None), x["id"])
        self.assertAlmostEqual(link(v, "loss")["fresh_raw"], S.fresh_probes(probes)[0]["loss"]["v"])


class LossShownByV(unittest.TestCase):
    def test_usual_and_days_in_v(self):
        """REV 12 (NS-D25): the hint of loss shows v — fresh, the two days and «usual»; ▲/▼ goes by lo."""
        h = Hist()
        for i in range(5):
            h.add(7 * i, {"loss": {"v": 0.5, "lo": 0.3 + 0.01 * i, "n": 20000}})
        h.add(35, {"loss": {"v": 2.0, "lo": 1.5, "n": 20000}})
        h.add(42, {"loss": {"v": 2.2, "lo": 1.6, "n": 20000}})
        v = h.view(at(42, 12))
        row = link(v, "loss")
        self.assertEqual(row["dev"], "down")
        self.assertAlmostEqual(row["usual_raw"], 0.5)
        self.assertAlmostEqual(v["dev_args"]["loss"]["now"], 2.2)
        self.assertAlmostEqual(v["dev_args"]["loss"]["usual"], 0.5)


# ── B4, круг 3 (k3/stand/logs/REV-ns-b4-2.md): red on the round-2 code (d2af76d5) or under its surviving mutation ──

class LossHintWithoutNumbers(unittest.TestCase):
    """REV-2 З-1 (NS-D25, решение координатора): ▲/▼ of loss goes by lo; when v of the two days is not on the side of
    the mark from «обычно», the hint goes without numbers (dev_args now/usual None)."""

    def run_case(self, base, last):
        h = Hist()
        h.add(0, dict(S4))
        recs = []
        for i in range(10):
            v, lo, n = base
            f = 1 + 0.02 * (i % 3 - 1)
            recs.append({"day": iso(i), "v": v * f, "lo": lo * f, "n": n, "hours": 24})
        for i in range(10, 13):
            v, lo, n = last
            recs.append({"day": iso(i), "v": v, "lo": lo, "n": n, "hours": 24})
        k = last[0] * last[2] / 100
        e = max(1, k / (30 if last[1] < last[0] / 2 else 1))
        wins = [{"day": iso(i), "hours": 24, "loss": {"a": {"n": last[2], "k": k, "e": e},
                                                     "b": {"n": last[2], "k": k, "e": e}}} for i in range(10, 13)]
        return h.view(at(13, 1), probes=wins, probe_days={"loss": recs})

    def test_bursty_better_by_lo_worse_by_v(self):
        v = self.run_case((0.10, 0.095, 1e6), (0.30, 0.05, 5e4))
        self.assertEqual(link(v, "loss")["dev"], "up")
        self.assertEqual(v["dev_args"]["loss"], {"link": "loss", "test": "loss", "now": None, "usual": None, "unit": "%",
                                                   "days": None})

    def test_worse_by_lo_better_by_v_and_the_yellow_day(self):
        v = self.run_case((0.30, 0.05, 5e4), (0.10, 0.095, 1e6))
        self.assertEqual(link(v, "loss")["dev"], "down")
        self.assertEqual((v["dev_args"]["loss"]["now"], v["dev_args"]["loss"]["usual"]), (None, None))
        c = S.day_color({"hours": 24, "test": False, "n": 0}, S.day_devs(v, set()), [])
        self.assertEqual((c[0], c[2]["code"]), ("yellow", "link_down"))
        self.assertEqual(c[2]["args"], {"link": "net_q", "test": "loss", "now": None, "usual": None, "unit": "%"})

    def test_agreeing_sign_keeps_the_numbers(self):
        """The same sign of v and lo — numbers as before (LossShownByV); the helper itself."""
        t = T["loss"]
        self.assertTrue(S._shown_agrees(t, "down", [(1, 2.0), (2, 2.2)], 0.5))
        self.assertFalse(S._shown_agrees(t, "down", [(1, 2.0), (2, 0.4)], 0.5))
        self.assertTrue(S._shown_agrees(t, "up", [(1, 0.1), (2, 0.2)], 0.5))
        self.assertFalse(S._shown_agrees(t, "up", [(1, 0.5), (2, 0.2)], 0.5))   # equal to usual — not on the side


class SurvivingMutations(unittest.TestCase):
    """REV-2 З-2: R5, R10, R19, R20 (the round-2 code), N6 (since round 1)."""

    def test_a_false_drop_goes_by_the_pair(self):
        """R5 (the score of the day is gone): the pair counts — the original 5 Mbit/s, its repeat 1300: the link shows
        the repeat's value, no event, nothing pending."""
        h = Hist()
        d = h.usual(S4, n=6)
        h.add(d, dict(S4, net_up=5))
        rep = h.add(d, {"net_up": 1300}, hh=6, retry=True)
        up = h.link(at(d, 12), "up")
        self.assertEqual((up["now_id"], up["fresh_raw"], up["rel"]), (rep, 1300, 100))
        self.assertEqual((up["dev_pending"], up["dev_pending_kind"]), (None, None))
        self.assertEqual([e for e in h.events() if e["link"] == "up"], [])

    def test_probe_windows_after_now(self):
        """R10: a probe window of a day after now does not exist yet — it does not change the fresh loss."""
        h = Hist()
        h.add(0, dict(S4))
        def w(day, k):
            return {"day": iso(day), "hours": 24, "loss": {"a": {"n": 30000, "k": k, "e": k},
                                                         "b": {"n": 30000, "k": k, "e": k}}}
        past = [w(12, 3), w(11, 2), w(10, 3)]
        got = link(h.view(at(13, 1), probes=[w(14, 3000)] + past), "loss")
        ref = link(h.view(at(13, 1), probes=past), "loss")
        self.assertEqual((got["rel"], got["fresh_raw"], got["parts"]), (ref["rel"], ref["fresh_raw"], ref["parts"]))
        self.assertIsNotNone(ref["fresh_raw"])

    def test_corr_nan(self):
        """R19: corr NaN of a channel test under load is «no correction» — the same as without corr."""
        def run(m):
            h = Hist()
            h.add(0, dict(S4))
            h.add(7, dict(S4, net_down=m), under_load=True)
            return h.view(at(7, 12))
        ref = run({"v": 400})
        got = run({"v": 400, "corr": float("nan")})
        self.assertEqual((got["summary"], got["links"]), (ref["summary"], ref["links"]))
        self.assertEqual(link(got, "down")["fresh_raw"], S4["net_down"])     # the run under load gives no channel

    def test_loss_window_counts_nan(self):
        """R20: an anchor with n/k/e NaN is left out of the 72 h sum."""
        nan = float("nan")
        for f in ("n", "k", "e"):
            a = {"n": 30000, "k": 3, "e": 3}
            a[f] = nan
            w = [{"day": iso(i), "hours": 24, "loss": {"a": dict(a), "b": {"n": 30000, "k": 2, "e": 2}}} for i in range(3)]
            ref = [{"day": iso(i), "hours": 24, "loss": {"b": {"n": 30000, "k": 2, "e": 2}}} for i in range(3)]
            self.assertEqual(S.fresh_probes(w)[0]["loss"], S.fresh_probes(ref)[0]["loss"], f)

    def test_stale_boundary_45_days(self):
        """N6 (NS-S10, §4): the last check exactly 45 d ago — not stale; a minute later — stale."""
        h = Hist()
        h.add(0, dict(S4))
        self.assertFalse(h.view(at(0) + 45 * S.DAY)["summary"]["stale"])
        self.assertTrue(h.view(at(0) + 45 * S.DAY + 60)["summary"]["stale"])

    def test_stale_carries_the_time_of_the_last_check(self):
        """REV-ns-p4b З-1: stale — no check in 45 d, so none in the 30 days of the strip either; the date of «последняя
        проверка» is summary.last_at (the last good check), not a day of the strip."""
        h = Hist()
        h.add(0, dict(S4))
        h.add(3, dict(S4), status="failed")              # a failed check is not «the last check»
        sm = h.view(at(0) + 60 * S.DAY)["summary"]
        self.assertTrue(sm["stale"])
        self.assertEqual(sm["last_at"], at(0))
        self.assertIsNone(Hist().view(at(0))["summary"]["last_at"])


class RepeatAfterMidnightDayPage(unittest.TestCase):
    def test_day_of_the_original(self):
        """REV-2 З-3 (§2.8 п. 6 «День события — сутки исходной»): the original 23:30 with upload 5, its repeat 01:10 —
        the page of the original's day (view at its end) holds the repeat and its events: retry_confirmed, the value
        of the pair; the next day does not."""
        h = Hist()
        d = h.usual(S4, n=6)
        orig = h.add(d, dict(S4, net_up=5), hh=23, mm=30)
        h.add(d + 1, {"net_up": 5}, hh=1, mm=10, retry=True)
        ev = h.events()
        self.assertEqual([(e["link"], e["day"], e["auto"]) for e in ev],
                         [("up", iso(d), True), ("net_bw", iso(d), True)])
        end = at(d + 1, 0) - 1
        for kw in ({"events": ev}, {}):
            v = h.view(end, **kw)
            up = link(v, "up")
            self.assertEqual(up["dev_pending_kind"], "retry_confirmed", kw)
            self.assertEqual((up["now_id"], up["now_day"], up["fresh_raw"], up["rel"]), (orig, iso(d), 5, 0), kw)
        self.assertIsNone(link(h.view(at(d, 12)), "up")["dev_pending_kind"])    # before the original: nothing

    def test_same_day_repeat_after_now(self):
        """Only a repeat after midnight goes to the day of its original: a repeat later the same day does not exist
        before its time (the original 04:00 with upload 5, its repeat 06:00 with 1300; at 05:00 — the original alone)."""
        h = Hist()
        for d in (0, 7, 14):
            h.add(d, dict(S4))
        orig = h.add(21, dict(S4, net_up=5))
        rep = h.add(21, {"net_up": 1300}, hh=6, retry=True)
        self.assertEqual(h.link(at(21, 5), "up")["now_id"], orig)
        self.assertEqual(h.link(at(21, 7), "up")["now_id"], rep)
        self.assertNotIn(rep, h.view(at(21, 5))["summary"]["fresh_from"]["tests"])


class NowIdAndTestArg(unittest.TestCase):
    def test_now_id_of_the_check_that_gave_the_value(self):
        """REV-2 П-1: now_id — the check whose value the link shows (of the pair — the better one)."""
        for rep_v, who in ((3, "orig"), (8, "rep")):
            h = Hist()
            for d in (0, 7, 14):
                h.add(d, dict(S4))
            ids = {"orig": h.add(21, dict(S4, net_up=5))}
            ids["rep"] = h.add(21, {"net_up": rep_v}, hh=6, retry=True)
            v = h.view(at(21, 12))
            self.assertEqual(link(v, "up")["now_id"], ids[who], rep_v)
            self.assertEqual(link(v, "down")["now_id"], ids["orig"])

    def test_link_down_args_carry_the_test(self):
        """REV-2 П-3 (NS C6): args of link_down hold the test that gave the ▼."""
        h = Hist()
        d = h.series("net_down", [600, 620, 640, 660, 700, 640, 540])
        h.add(d, {"net_down": 560})
        v = h.view(at(d + 1, 0) - 1)
        c = S.day_color({"hours": 24, "test": True}, S.day_devs(v, S.tested_links(ABS, h.runs, iso(d), day_of)), [])
        self.assertEqual((c[2]["args"]["link"], c[2]["args"]["test"]), ("net_bw", "net_down"))


class OpensslNewBase(unittest.TestCase):
    """Решение координатора 19.10 (RESEARCH «по живым образцам», п. 2): the version of OpenSSL in the inventory; its
    change is «новая база» of the link crypto only (as the CPU in inv_changes); no key — no rule."""

    def test_inv_changes(self):
        a = {"cpu": "AMD EPYC", "nproc": 2, "mem_gb": 2.0, "engines": ["awg"], "openssl_ver": "3.0.14"}
        self.assertEqual(S.inv_changes(a, dict(a, openssl_ver="3.5.5")), ["openssl"])
        self.assertEqual(S.inv_changes(a, dict(a)), [])
        no = {k: v for k, v in a.items() if k != "openssl_ver"}
        self.assertEqual(S.inv_changes(no, dict(a, openssl_ver="3.5.5")), [])
        self.assertEqual(S.inv_changes(a, no), [])

    def test_new_base_of_crypto_only(self):
        """The same machine, OpenSSL 3.0.14 -> 3.5.5: the base of the crypto tests starts on the day of the change,
        the other links keep theirs."""
        h = Hist()
        for i in range(8):
            h.add(7 * i, dict(S4))
        ep = [{"ts": at(49, 3), "kind": "openssl"}]
        self.assertEqual(sorted(S.epoch_since(ABS, ep, day_of)), ["aes_1t", "chacha_1t"])
        v, ref = h.view(at(49, 12), epochs=ep), h.view(at(49, 12))
        self.assertEqual(v["summary"]["base_since"], [{"kind": "openssl", "day": iso(49)}])
        self.assertLess(link(v, "crypto")["usual_n"], link(ref, "crypto")["usual_n"])
        self.assertEqual((link(v, "crypto")["rel"], link(ref, "crypto")["rel"]), (None, 100))   # «обычно» anew
        for lid in ("down", "up", "net_bw", "net_q", "stab", "loss"):
            self.assertEqual((link(v, lid)["usual_n"], link(v, lid)["usual_raw"], link(v, lid)["rel"]),
                             (link(ref, lid)["usual_n"], link(ref, lid)["usual_raw"], link(ref, lid)["rel"]), lid)
        # the group cpu holds the same crypto tests: its usual goes by cpu_1t and cpu_mt until they have a base again
        self.assertEqual(link(v, "cpu")["usual_n"], link(ref, "cpu")["usual_n"])


if __name__ == "__main__":
    unittest.main()
