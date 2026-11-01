"""The relative score of a node against its own history (решение владельца 23.10; SPEC-node-score.md NS-D30, NS-D31,
NS-D33, NS-D34): ``rel_of`` / ``rel_total`` on their edges, the floor of the tests where less is better, the rel of a
group (the median) and of crypto (the least), «резкое падение» and the automatic repeat by rel <= 50, the shape of
``view()`` without the absolute score. Every test here is red on the code before the cut (f7560ed5)."""

import unittest

from kaskad import score as S
from tests.score_hist import ABS, S4, T, Hist, at, day_of, iso

DOWN, UP, RTT, LOSS, JITTER, DNS = (T[i] for i in ("net_down", "net_up", "rtt", "loss", "jitter", "dns"))


def link(v, lid):
    return next(x for x in v["links"] if x["id"] == lid)


def events(h, lid="down"):
    return [e for e in h.events() if e["link"] == lid]


class RelOfTest(unittest.TestCase):
    """NS-D30: rel = round(100 · fresh / usual); less is better — max(usual, floor) / max(fresh, floor); 0…300."""

    def test_more_is_better(self):
        self.assertEqual(S.rel_of(DOWN, 600, 600), 100)
        self.assertEqual(S.rel_of(DOWN, 300, 600), 50)
        self.assertEqual(S.rel_of(DOWN, 0, 600), 0)                 # a dead channel against a live one — 0, a number
        self.assertIsNone(S.rel_of(DOWN, 600, 0))                    # nothing usually: no number, not infinity
        self.assertIsNone(S.rel_of(DOWN, 0, 0))
        self.assertIsNone(S.rel_of(DOWN, 0.0, 0.0))

    def test_not_a_value(self):
        for bad in (None, float("nan"), float("inf"), float("-inf"), -1, True, "600", {"v": 600}):
            self.assertIsNone(S.rel_of(DOWN, bad, 600), bad)
            self.assertIsNone(S.rel_of(DOWN, 600, bad), bad)
            self.assertIsNone(S.rel_of(RTT, bad, 20), bad)

    def test_within_0_300(self):
        self.assertEqual(S.rel_of(DOWN, 1800, 600), 300)
        self.assertEqual(S.rel_of(DOWN, 1806, 600), 300)             # 301 -> 300
        self.assertEqual(S.rel_of(DOWN, 2400, 600), 300)
        self.assertEqual(S.rel_of(DOWN, 1794, 600), 299)
        self.assertEqual(S.rel_of(RTT, 1, 500), 300)

    def test_half_up(self):
        """Half goes up (not to the even one, as round() of Python): 12.5 -> 13, 50.5 -> 51, 50.49 -> 50."""
        self.assertEqual(S.rel_of(DOWN, 1, 8), 13)                  # 12.5
        self.assertEqual(S.rel_of(DOWN, 5, 8), 63)                  # 62.5
        self.assertEqual(S.rel_of(DOWN, 303, 600), 51)              # 50.5
        self.assertEqual(S.rel_of(DOWN, 302.94, 600), 50)           # 50.49
        self.assertEqual(S.rel_of(RTT, 16, 2), 13)                  # less is better: 100 · 2 / 16 = 12.5

    def test_less_is_better(self):
        """The inverse ratio: the delay twice as long — 50, twice as short — 200."""
        self.assertEqual(S.rel_of(RTT, 40, 20), 50)
        self.assertEqual(S.rel_of(RTT, 10, 20), 200)
        self.assertEqual(S.rel_of(DNS, 10, 5), 50)
        self.assertEqual(S.rel_of(T["disk_fsync"], 0.3, 0.15), 50)

    def test_the_floor_of_less_is_better(self):
        """The coordinator 23.10 (NS-D30): loss 0.1 %, delay 1 ms, jitter 0.5 ms — 0 % against 0 % usually is 100,
        0 % against 0.4 % is 400 -> 300; no floor (dns) and 0 now — no number."""
        self.assertEqual(S.rel_of(LOSS, 0, 0), 100)
        self.assertEqual(S.rel_of(LOSS, 0.0, 0.4), 300)              # 400 -> 300
        self.assertEqual(S.rel_of(LOSS, 0.0, 0.2), 200)
        self.assertEqual(S.rel_of(LOSS, 0.05, 0.08), 100)            # both under the floor
        self.assertEqual(S.rel_of(LOSS, 0.4, 0), 25)                 # 0.1 / 0.4
        self.assertEqual(S.rel_of(RTT, 0, 0), 100)
        self.assertEqual(S.rel_of(RTT, 0.5, 2), 200)                 # 2 / max(0.5, 1)
        self.assertEqual(S.rel_of(RTT, 0, 20), 300)                  # 2000 -> 300, not infinity
        self.assertEqual(S.rel_of(JITTER, 0, 0.25), 100)
        self.assertEqual(S.rel_of(JITTER, 1.0, 0.25), 50)            # 0.5 / 1.0
        self.assertEqual(S.rel_of(DNS, 0, 5), 300)                   # dns 1 ms (NS-D35): 500 -> 300, a number
        self.assertEqual(S.rel_of(DNS, 5, 0), 20)                    # 1 / 5
        self.assertEqual(S.rel_of(DNS, 0.3, 0.8), 100)               # both under the floor

    def test_every_less_is_better_has_a_floor(self):
        """NS-D35, REV-score-cut С-1 (сторож): every test of SPEC where less is better has rel_floor > 0 (net_cv —
        no rel at all, USUAL_AS_FRESH); a new such test without one fails here."""
        lower = [t for g in S.SPEC["groups"] for t in g["tests"] if t["better"] == "lower"]
        self.assertTrue(lower)
        no = [t["id"] for t in lower if t["id"] not in S.USUAL_AS_FRESH and not (t.get("rel_floor") or 0) > 0]
        self.assertEqual(no, [])
        floors = dict((t["id"], t.get("rel_floor")) for t in lower)
        self.assertEqual(floors, {"rtt": 1.0, "loss": 0.1, "jitter": 0.5, "dns": 1.0, "steal_p95": 1.0, "cpu_cv": 1.0,
                                  "cpu_canary_cv": 1.0, "net_cv": None, "disk_fsync": 0.1})

    def test_the_noise_of_stability_is_100(self):
        """REV-score-cut С-1: steal 0.5 % against 0.05 % usually is noise — 100, not 10; zeros — 100, not «no
        number»; above the floor — the ratio."""
        steal, cv, canary, fsync = (T[i] for i in ("steal_p95", "cpu_cv", "cpu_canary_cv", "disk_fsync"))
        self.assertEqual(S.rel_of(steal, 0.5, 0.05), 100)
        self.assertEqual(S.rel_of(steal, 0.2, 0), 100)
        self.assertEqual(S.rel_of(steal, 0, 0), 100)
        self.assertEqual(S.rel_of(steal, 4, 2), 50)
        self.assertEqual(S.rel_of(cv, 1.0, 0.3), 100)
        self.assertEqual(S.rel_of(cv, 3.0, 1.5), 50)
        self.assertEqual(S.rel_of(canary, 0, 0), 100)
        self.assertEqual(S.rel_of(fsync, 0, 0), 100)
        self.assertEqual(S.rel_of(fsync, 0.05, 0.08), 100)            # under 0.1 ms — the panel shows no difference


class RelTotalTest(unittest.TestCase):
    """NS-D31: the median of the links that have rel; fewer than 3 such links — None."""

    def test_edges(self):
        self.assertIsNone(S.rel_total([]))
        self.assertIsNone(S.rel_total([None] * 8))
        self.assertIsNone(S.rel_total([100, None, 90, None]))       # 2 links with rel
        self.assertEqual(S.rel_total([100, 90, 80]), 90)             # exactly 3 — the median
        self.assertEqual(S.rel_total([None, 300, 0, 50, None]), 50)
        self.assertEqual(S.rel_total([100, 90, 80, 70]), 85)
        self.assertEqual(S.rel_total([100, 91, 80, 70]), 86)         # 85.5 — half up
        self.assertEqual(S.rel_total([0, 0, 0]), 0)
        self.assertEqual((S.REL_TOTAL_MIN, S.REL_N_MIN, S.REL_MAX, S.DROP_REL), (3, 3, 300, 50))


class LinkRelTest(unittest.TestCase):
    """A link of one test — its rel; a group — the median of its tests' rel; crypto — the least (the cipher the node
    counts on); a test without «обычно» (usual_n < 3) has none."""

    @staticmethod
    def series(usual, n=5):
        return lambda tid: {"n": n if tid in usual else 0, "rel_usual": usual.get(tid)}

    def rel(self, lid, F, usual, n=5):
        b = next(x for x in ABS["links"] if x["id"] == lid)
        return S.link_rel(ABS, b, F, self.series(usual, n))

    def test_one_test(self):
        rel, parts, group = self.rel("down", {"net_down": {"v": 300}}, {"net_down": 600})
        self.assertEqual((rel, group), (50, False))
        self.assertEqual(parts, [{"test": "net_down", "fresh_raw": 300.0, "usual_raw": 600, "usual_n": 5,
                                  "unit": "Мбит/с", "rel": 50}])

    def test_usual_n_below_3(self):
        rel, parts, _g = self.rel("down", {"net_down": {"v": 300}}, {"net_down": 600}, n=2)
        self.assertIsNone(rel)
        self.assertEqual((parts[0]["usual_raw"], parts[0]["usual_n"], parts[0]["rel"]), (None, 2, None))
        self.assertEqual(self.rel("down", {"net_down": {"v": 300}}, {"net_down": 600}, n=3)[0], 50)

    def test_group_median(self):
        rel, parts, group = self.rel("net_bw", {"net_down": 300, "net_up": 900}, {"net_down": 600, "net_up": 600})
        self.assertEqual((rel, group), (100, True))                  # median of 50 and 150
        self.assertEqual([p["rel"] for p in parts], [50, 150])
        rel = self.rel("net_bw", {"net_down": 303, "net_up": 600}, {"net_down": 600, "net_up": 600})[0]
        self.assertEqual(rel, 76)                                    # (51 + 100) / 2 = 75.5 — half up
        rel = self.rel("net_bw", {"net_down": 300}, {"net_down": 600, "net_up": 600})[0]
        self.assertEqual(rel, 50)                                    # the upload not measured: the download alone
        F = {"cpu_1t": 500, "cpu_mt": 1000, "chacha_1t": 1500, "aes_1t": 2000, "_engines": ["awg", "vless"],
             "_cpu_flags": ["aes"]}
        rel, parts, _g = self.rel("cpu", F, dict.fromkeys(("cpu_1t", "cpu_mt", "chacha_1t", "aes_1t"), 1000))
        self.assertEqual([p["rel"] for p in parts], [50, 100, 150, 200])
        self.assertEqual(rel, 125)
        self.assertIsNone(self.rel("net_bw", {}, {"net_down": 600, "net_up": 600})[0])

    def test_stability_of_zeros_is_100(self):
        """REV-score-cut С-1: the link «Стабильность» with every value 0 (a quiet node) — 100, not None; the noise of
        the report (steal 0.05 -> 0.5, cpu_cv 0.3 -> 0.9, canary 0.5 -> 1.5) — 100, not 33 (the canary above its floor
        still counts: 67)."""
        tids = ("steal_p95", "cpu_cv", "cpu_canary_cv", "cpu_sustain")
        rel, parts, _g = self.rel("stab", dict.fromkeys(tids[:3], 0.0), dict.fromkeys(tids[:3], 0.0))
        self.assertEqual((rel, [p["rel"] for p in parts if p["test"] in tids[:3]]), (100, [100, 100, 100]))
        F = {"steal_p95": 0.5, "cpu_cv": 0.9, "cpu_canary_cv": 1.5}
        rel, parts, _g = self.rel("stab", F, {"steal_p95": 0.05, "cpu_cv": 0.3, "cpu_canary_cv": 0.5})
        self.assertEqual([p["rel"] for p in parts if p["test"] in tids[:3]], [100, 100, 67])  # canary 1 / 1.5
        self.assertEqual(rel, 100)                                   # the median, not 33

    def test_crypto_least(self):
        F = {"chacha_1t": 1200, "aes_1t": 4800, "_engines": ["awg", "vless"], "_cpu_flags": ["aes"]}
        rel, parts, group = self.rel("crypto", F, {"chacha_1t": 1500, "aes_1t": 4000})
        self.assertEqual(([p["test"] for p in parts], [p["rel"] for p in parts]), (["chacha_1t", "aes_1t"], [80, 120]))
        self.assertEqual((rel, group), (80, False))
        self.assertEqual(S.one_part(parts, group)["test"], "chacha_1t")       # the line shows the one of its rel
        F = dict(F, _engines=["vless"])                                       # VLESS with AES-NI: AES-GCM only
        self.assertEqual(self.rel("crypto", F, {"chacha_1t": 1500, "aes_1t": 4000})[0], 120)


class ViewTest(unittest.TestCase):
    """view(): the raw numbers and rel of the links, rel_total in the summary; no absolute score (NS-D34)."""

    def test_shape(self):
        h = Hist()
        h.usual(S4, n=8)
        v = h.view(at(50, 12))
        self.assertEqual(set(v), {"summary", "dev_rule", "links", "dev_args", "pend_args", "kind_args", "stab", "vals"})
        for gone in ("score", "band", "weakest", "fresh_total", "capacity", "fit_for", "offline_budget", "rel",
                     "fresh", "usual", "curves"):
            self.assertNotIn(gone, v, gone)
        self.assertEqual(set(v["summary"]), {"rel_total", "rel_n", "rel_need", "last_id", "last_at", "fresh_from",
                                             "first", "stale", "checked", "retry", "base_since"})
        for lk in v["links"]:
            for gone in ("fresh", "now", "usual", "p25", "p75", "band", "score", "weak"):
                self.assertNotIn(gone, lk, (lk["id"], gone))
            for key in ("rel", "fresh_raw", "usual_raw", "p25_raw", "p75_raw", "parts", "test", "unit", "usual_n"):
                self.assertIn(key, lk, (lk["id"], key))
        self.assertEqual((v["summary"]["rel_total"], v["summary"]["rel_n"], v["summary"]["rel_need"]), (100, 8, 3))
        self.assertEqual([lk["rel"] for lk in v["links"]], [100] * 8)

    def test_rel_total_of_the_links(self):
        """Download at half of usual for its three last values: «down» 50, the channel the median of 50 and 100, the
        total the median of the 8 links — still 100 (as usual for this server)."""
        h = Hist()
        d = h.usual(S4, n=6)
        for i in range(3):
            h.add(d + i, dict(S4, net_down=S4["net_down"] / 2))
        v = h.view(at(d + 2, 12))
        self.assertEqual(link(v, "down")["rel"], 50)
        self.assertEqual(link(v, "net_bw")["rel"], 75)
        self.assertEqual([p["rel"] for p in link(v, "net_bw")["parts"]], [50, 100])
        self.assertEqual(v["summary"]["rel_total"], 100)

    def test_no_history_no_rel(self):
        """One check or a short history: the raw numbers only, rel None everywhere, «N из 3» from usual_n."""
        h = Hist()
        h.usual(S4, n=4)
        v = h.view(at(22, 12))
        self.assertEqual((v["summary"]["rel_total"], v["summary"]["rel_n"]), (None, 0))
        down = link(v, "down")
        self.assertEqual((down["rel"], down["fresh_raw"], down["usual_raw"], down["usual_n"]),
                         (None, S4["net_down"], None, 1))
        h.add(28, dict(S4))
        self.assertEqual([link(h.view(at(28, 12)), "down")[k] for k in ("rel", "usual_n")], [None, 2])
        h.add(35, dict(S4))                                          # the sixth: 3 base days before the window
        down = link(h.view(at(35, 12)), "down")
        self.assertEqual((down["rel"], down["usual_n"]), (100, 3))
        self.assertAlmostEqual(down["usual_raw"], S4["net_down"], places=6)

    def test_floor_through_the_probes(self):
        """The coordinator 23.10 through view: loss 0 % now and 0 % usually — the link loss 100 (and net_q); 0 % now
        and 0.4 % usually — 300; the delay 0 ms against 20 ms — 300, not infinity."""
        def run(base_loss, rtt_now=None):
            h = Hist()
            h.add(0, dict(S4))
            days = {"loss": [{"day": iso(i), "v": base_loss, "lo": base_loss, "n": 25920, "hours": 24}
                             for i in range(10)]}
            wins = []
            for i in (10, 11, 12):
                w = {"day": iso(i), "hours": 24, "loss": {a: {"n": 8640, "k": 0, "e": 0} for a in ("a", "b", "c")}}
                if rtt_now is not None:
                    w["rtt"] = rtt_now
                wins.append(w)
            if rtt_now is not None:
                days["rtt"] = [{"day": iso(i), "v": 20.0, "hours": 24} for i in range(10)]
            return h.view(at(13, 1), probes=wins, probe_days=days)
        v = run(0.0)
        self.assertEqual((link(v, "loss")["rel"], link(v, "loss")["fresh_raw"]), (100, 0.0))
        self.assertEqual(link(v, "net_q")["rel"], 100)
        self.assertEqual(link(v, "loss")["rel"], S.rel_of(LOSS, 0.0, 0.0))
        v = run(0.4)
        self.assertEqual(link(v, "loss")["rel"], 300)
        self.assertAlmostEqual(link(v, "loss")["usual_raw"], 0.4)
        v = run(0.0, rtt_now=0.0)
        rtt = next(p for p in link(v, "net_q")["parts"] if p["test"] == "rtt")
        self.assertEqual((rtt["fresh_raw"], rtt["rel"]), (0.0, 300))
        self.assertEqual(link(v, "net_q")["rel"], 200)               # median of rtt 300 and loss 100


class DropTest(unittest.TestCase):
    """NS-D33: «резкое падение» — rel of a link of the tests <= 50 at usual_n >= 3; the event — only on the way from
    «выше» to «ниже»; the first rel of a link — no event; without history — no event and no repeat; the repeat that
    confirms — auto, one event per drop; a push at most once in 7 days per link."""

    def drop(self, x):
        """Five usual checks of 600, the sixth 600 (the first rel: 100, «выше»), then x and its repeat x."""
        h = Hist()
        d = h.usual({"net_down": 600}, n=6)
        orig = h.add(d, {"net_down": x})
        h.add(d, {"net_down": x}, hh=6, retry=True)
        return h, d, orig

    def test_rel_50_is_a_drop_51_is_not(self):
        h, d, _o = self.drop(300)
        ev = events(h)
        self.assertEqual([(e["cond"], e["matched"], e["now"], e["auto"]) for e in ev], [("rel", ["rel"], 50, True)])
        h, d, _o = self.drop(306)
        self.assertEqual(events(h), [])
        self.assertIs(S.drop_events(ABS, h.runs, day_of=day_of)[1]["down"], True)    # 51: still «выше»

    def test_retry_wanted_at_the_boundary(self):
        for x, want in ((300, ["down", "net_bw"]), (306, []), (0, ["down", "net_bw"])):
            h = Hist()
            d = h.usual({"net_down": 600}, n=6)
            h.add(d, {"net_down": x})
            run = h.runs.pop()
            self.assertEqual(S.retry_wanted(ABS, h.runs, run, day_of=day_of), want, x)

    def test_the_event_fields(self):
        h, d, orig = self.drop(120)
        ev = events(h)
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual((e["run"], e["day"], e["cond"], e["push"], e["auto"], e["now"]),
                         (h.runs[-1]["id"], iso(d), "rel", True, True, 20))
        self.assertEqual((e["test"], e["value"], e["unit"]), ("net_down", 120.0, "Мбит/с"))
        self.assertAlmostEqual(e["usual"], 600.0, places=6)
        bw = events(h, "net_bw")
        self.assertEqual([(x["now"], x["test"], x["value"], x["usual"], x["unit"]) for x in bw],
                         [(20, None, None, None, None)])                      # a group: no one raw number

    def test_only_on_the_way_down(self):
        """Still below — no second event; back above (rel > 50) and down again — a new event."""
        h, d, _o = self.drop(120)
        h.add(d + 7, {"net_down": 120})
        h.add(d + 14, {"net_down": 120})
        self.assertEqual(len(events(h)), 1)
        self.assertEqual(S.retry_wanted(ABS, h.runs[:-1], h.runs[-1], day_of=day_of), [])    # «ниже» already: no repeat
        for i in range(3):
            h.add(d + 21 + 7 * i, {"net_down": 600})
        self.assertTrue(S.drop_events(ABS, h.runs, day_of=day_of)[1]["down"])                # «выше» again
        h.add(d + 42, {"net_down": 120})
        h.add(d + 42, {"net_down": 110}, hh=6, retry=True)
        ev = events(h)
        self.assertEqual([(e["day"], e["auto"], e["push"]) for e in ev],
                         [(iso(d), True, True), (iso(d + 42), True, True)])

    def test_the_first_rel_gives_no_event(self):
        """The history becomes long enough while the link is already low: that is its «обычно» to be — no event."""
        h = Hist()
        for i in range(3):
            h.add(7 * i, {"net_down": 600})
        for i in range(3, 7):
            h.add(7 * i, {"net_down": 100})
        evs, state = S.drop_events(ABS, h.runs, day_of=day_of)
        self.assertEqual([e for e in evs if e["link"] == "down"], [])
        self.assertIs(state["down"], False)                                   # known, and «ниже»

    def test_no_history_no_event_no_repeat(self):
        """NS-D33: without «обычно» (usual_n < 3) there is no sharp drop — only a failed test: no event, no repeat."""
        h = Hist()
        h.add(0, {"net_down": 600})
        h.add(7, {"net_down": 600})
        h.add(14, {"net_down": 30})
        run = h.runs.pop()
        self.assertEqual(S.retry_wanted(ABS, h.runs, run, day_of=day_of), [])
        h.runs.append(run)
        h.add(14, {"net_down": 20}, hh=6, retry=True)
        self.assertEqual(S.drop_events(ABS, h.runs, day_of=day_of), ([], {}))
        h = Hist()
        h.usual({"net_down": 600}, n=4)                                       # 4 checks + the fifth: usual_n 2
        h.add(28, {"net_down": 30})
        self.assertEqual(S.retry_wanted(ABS, h.runs[:-1], h.runs[-1], day_of=day_of), [])
        h = Hist()
        h.usual({"net_down": 600}, n=5)                                       # 5 checks + the sixth: usual_n 3
        h.add(35, {"net_down": 30})
        self.assertEqual(S.retry_wanted(ABS, h.runs[:-1], h.runs[-1], day_of=day_of), ["down", "net_bw"])

    def test_one_event_per_drop_of_the_original_and_its_repeats(self):
        """The original falls by its latest checks (the event is the original's, not auto); its repeat and a second
        repeat of the same original give no second event."""
        h = Hist()
        d = h.usual({"net_down": 600}, n=6)
        h.add(d, {"net_down": 100})                                           # median of 600, 600, 100: «выше»
        orig = h.add(d + 7, {"net_down": 100})                                # median of 600, 100, 100: the event
        h.add(d + 7, {"net_down": 90}, hh=6, retry=True)
        h.add(d + 7, {"net_down": 80}, hh=8, retry_of=orig)
        ev = events(h)
        self.assertEqual([(e["run"], e["auto"], e["cond"]) for e in ev], [(orig, False, "rel")])

    def test_the_rel_of_the_event_is_the_rel_on_the_screen(self):
        """The replay sees the same «обычно» as view() at that check (60 d before the window, not a 45-day cut):
        weekly checks — 8 weeks of 1000, 4 of 600 (60: «выше»), then 180 and its repeat: the event says 18 (180 of
        1000), as the screen; the base cut at 45 d gave 30 (180 of 600)."""
        h = Hist()
        for i in range(12):
            h.add(7 * i, {"net_down": 1000 if i < 8 else 600})
        h.add(84, {"net_down": 180})
        h.add(84, {"net_down": 170}, hh=6, retry=True)
        ev = events(h)
        shown = link(h.view(h.runs[-1]["ts"]), "down")
        self.assertEqual([e["now"] for e in ev], [shown["rel"]])
        self.assertEqual(shown["rel"], 18)
        self.assertAlmostEqual(ev[0]["usual"], shown["usual_raw"], places=6)

    def test_push_once_in_7_days(self):
        h, d, _o = self.drop(120)
        h.add(d + 1, {"net_down": 600})
        h.add(d + 2, {"net_down": 600})                                       # back «выше»
        h.add(d + 3, {"net_down": 120})
        h.add(d + 3, {"net_down": 110}, hh=6, retry=True)                     # 3 d later: an event, no push
        h.add(d + 4, {"net_down": 600})
        h.add(d + 5, {"net_down": 600})
        h.add(d + 7, {"net_down": 120})
        h.add(d + 7, {"net_down": 110}, hh=6, retry=True)                     # 7 d after the push: push again
        self.assertEqual([(e["day"], e["push"], e["cond"]) for e in events(h)],
                         [(iso(d), True, "rel"), (iso(d + 3), False, "rel"), (iso(d + 7), True, "rel")])


if __name__ == "__main__":
    unittest.main()
