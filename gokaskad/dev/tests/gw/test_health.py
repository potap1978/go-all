"""The state machine of the exits — kaskad.gw.health (SPEC-stage1-exits §3.2 «упал», §4.1–4.3, §4.6, §5.5–5.6,
§11.1 «health.py»; T2). Pure functions: the time is a parameter, the ticks are simulated."""

import copy
import unittest
from unittest import mock

from kaskad.gw import health, policy as P

E1, E2, E3, R1 = "e-0000000001", "e-0000000002", "e-0000000003", "e-00000000a1"


def kinds_of(events, exclude=("transition",)):
    return [e["kind"] for e in events if e["kind"] not in exclude and not e.get("request")]


class Sim(object):
    """Ticks of the collector over a policy: ``probe(t, {eid: ok})`` — the probes of this round at t."""

    def __init__(self, preset="compat", exits=(E1,), kind="awg", freq=None, kinds=None, extra=None):
        self.doc = P.default_doc(preset)
        self.doc["groups"][0]["tiers"][0]["exits"] = list(exits)
        if freq:
            self.doc["groups"][0]["checks"]["freq"] = freq
        self.kinds = dict((e, (kinds or {}).get(e, kind)) for e in exits)
        if extra:
            for e, k in extra.items():
                self.kinds[e] = k
        self.h = {}
        self.events = []
        self.base = {}

    def tick(self, t, probes=None, node=None, per=None):
        obs = {}
        for e, k in self.kinds.items():
            o = dict(self.base.get(e) or {}, kind=k)
            pr = (probes or {}).get(e)
            if pr is not None:
                items = pr if isinstance(pr, list) else [pr]
                o["probes"] = [p if isinstance(p, dict) else {"ok": p, "rtt": 50 if p else None, "at": t}
                               for p in items]
            o.update((per or {}).get(e) or {})
            obs[e] = o
        nd = dict(node or {})
        nd.setdefault("mono", t)
        self.h, ev = health.evaluate(self.h, obs, self.doc, nd, t)
        self.events += [(t, e) for e in ev]
        return ev

    def run(self, t0, t1, step, eid=E1, ok=True, rtt=50):
        t = t0
        while t <= t1:
            self.tick(t, {eid: {"ok": ok, "rtt": rtt if ok else None, "at": t}})
            t += step
        return t - step

    def state(self, eid=E1):
        return self.h[eid]["state"]

    def first(self, kind, eid=E1, after=None):
        for t, e in self.events:
            if e["kind"] == kind and e.get("exit") == eid and (after is None or t >= after):
                return t
        return None


class FallRuleTest(unittest.TestCase):
    def test_one_failure_of_two_after_the_start_is_not_dead(self):
        s = Sim()
        s.tick(0, {E1: True})
        s.tick(10, {E1: False})
        s.tick(20, {E1: False})
        self.assertNotEqual(s.state(), "off")                # 3 checks: fewer than MIN_TRIES
        s.tick(30, {E1: True})
        self.assertEqual(s.state(), "ok")

    def test_a_series_below_half_is_not_dead(self):
        s = Sim()
        s.run(0, 300, 10)
        for i, t in enumerate(range(310, 600, 10)):
            s.tick(t, {E1: i % 3 != 0})                       # a third failed
        self.assertEqual(s.state(), "ok")
        self.assertIsNone(s.first("exit_dead"))

    def _outage(self, p, kind="awg", freq=None, start_ok=600):
        s = Sim(kind=kind, freq=freq)
        last = s.run(0, start_ok, p)
        s0 = last + 1                                        # the outage starts right after a good check
        t = last + p
        while s.state() != "off" and t < last + 10 * p + 4000:
            s.tick(t, {E1: False})
            t += p
        return s.first("exit_dead") - s0

    def test_full_outage_at_10_and_30_s_takes_a_minute(self):
        d10 = self._outage(10)
        self.assertTrue(50 <= d10 <= 70, d10)
        d30 = self._outage(30, freq={"mode": "normal", "every_s": None})
        self.assertTrue(50 <= d30 <= 90, d30)

    def test_a_missed_round_at_30_s_takes_the_last_four(self):
        s = Sim(freq={"mode": "normal", "every_s": None})
        for t in (0, 30, 60, 90):
            s.tick(t, {E1: True})
        s.tick(150, {E1: False})                              # 120 skipped: 2 checks younger than 2 min
        self.assertEqual(s.state(), "ok")                    # [30, 60, 90, 150] — 1 of 4
        s.tick(180, {E1: False})
        self.assertEqual(s.state(), "off")                   # [60, 90, 150, 180] — 2 of 4
        self.assertEqual(health.window([[60, 1], [90, 1], [150, 0]], 150), [[60, 1], [90, 1], [150, 0]])

    def test_rare_and_custom_600_bounds(self):
        for freq, p in (({"mode": "rare", "every_s": None}, 120), ({"mode": "custom", "every_s": 600}, 600)):
            for kind in ("awg", "xray"):
                for late in (False, True):
                    s = Sim(kind=kind, freq=freq)
                    last = s.run(0, 8 * p, p)
                    start = last + (p - 1 if late else 1)     # right before a check / right after one
                    t = last + p
                    while s.state() != "off":
                        s.tick(t, {E1: False})
                        t += p
                    took = s.first("exit_dead") - start
                    self.assertGreaterEqual(took, p - 2, (freq, kind, late, took))
                    self.assertLessEqual(took, 2 * p + 10, (freq, kind, late, took))

    def test_cold_start_needs_four_checks(self):
        s = Sim()
        for t in (0, 10, 20):
            s.tick(t, {E1: False})
            self.assertNotEqual(s.state(), "off")
        s.tick(30, {E1: False})
        self.assertEqual(s.state(), "off")
        self.assertEqual(kinds_of(e for _, e in s.events), ["exit_dead"])

    def test_one_check_per_period_a_recheck_replaces(self):
        s = Sim()
        s.run(0, 200, 10)
        s.tick(210, {E1: False})
        s.tick(215, {E1: False})                              # the recheck 5 s later: replaces, never adds
        tries = s.h[E1]["tries"]
        self.assertEqual(len([x for x in tries if not x[1]]), 1)
        s.tick(220, {E1: True})                               # the next round: a check of its own
        self.assertEqual([x[1] for x in s.h[E1]["tries"][-2:]], [0, 1])
        s.tick(230, {E1: False})
        s.tick(235, {E1: True})                               # the recheck 5 s later answered: the failure replaced
        self.assertEqual(len([x for x in s.h[E1]["tries"] if not x[1]]), 1)
        self.assertEqual(s.h[E1]["tries"][-1], [230, 1])

    def test_short_outages_are_not_dead(self):
        for freq, p in (({"mode": "rare", "every_s": None}, 120), ({"mode": "custom", "every_s": 60}, 60)):
            s = Sim(freq=freq)
            last = s.run(0, 10 * p, p)
            s.tick(last + p, {E1: False})
            s.tick(last + p + 5, {E1: False})                 # failed recheck
            s.tick(last + p + 10, {E1: True})                 # back
            s.run(last + 2 * p, last + 6 * p, p)
            self.assertEqual(s.state(), "ok", freq)
            self.assertEqual(kinds_of(e for _, e in s.events), [], freq)
        # «Лениво» after an idle hour at most: the window holds 4 old good checks
        s = Sim(freq={"mode": "lazy", "every_s": None})
        for t in (0, 15, 30, 45):
            s.tick(t, {E1: True})
        for t in range(60, 3000, 60):
            s.tick(t)                                         # no traffic: no probes
        s.tick(3000, {E1: False})
        s.tick(3005, {E1: False})
        s.tick(3014, {E1: True})
        self.assertEqual(s.state(), "ok")
        self.assertIsNone(s.first("exit_dead"))

    def test_an_outage_shorter_than_p_never_kills(self):
        for freq, p in ((None, 10), ({"mode": "normal", "every_s": None}, 30), ({"mode": "rare", "every_s": None}, 120)):
            s = Sim(freq=freq)
            last = s.run(0, 20 * p, p)
            s.tick(last + p, {E1: False})
            s.run(last + 2 * p, last + 20 * p, p)
            self.assertIsNone(s.first("exit_dead"), freq)

    def test_lazy_window_and_idle(self):
        s = Sim(freq={"mode": "lazy", "every_s": None})
        for t in (0, 15, 30, 45):
            s.tick(t, {E1: True})
        for t in range(60, 20000, 60):                        # hours without traffic: a good exit stays good
            s.tick(t)
        self.assertEqual(s.state(), "ok")
        self.assertEqual(health.window([[0, 1], [15, 1], [30, 1], [45, 1], [20000, 0]], 20000, lazy=True), [[20000, 0]])
        s.tick(20000, {E1: False})
        s.tick(20015, {E1: False})
        s.tick(20030, {E1: False})
        self.assertNotEqual(s.state(), "off")                 # the old checks are older than an hour: 3 of 3
        s.tick(20045, {E1: False})
        self.assertEqual(s.state(), "off")

    def test_the_same_probe_of_the_observatory_read_again(self):
        s = Sim(kind="xray")
        for t in range(0, 200, 10):
            s.tick(t, {E1: {"ok": True, "rtt": 40, "id": t, "at": t}})
        for t in range(200, 260, 5):                          # xray did not probe again: the same last_try_time
            s.tick(t, {E1: {"ok": False, "id": 190, "at": t}})
        self.assertEqual(s.state(), "ok")
        self.assertEqual([x for x in s.h[E1]["tries"] if not x[1]], [])     # not one failed check counted

    def test_window_anew_on_a_change_and_back_after_two(self):
        s = Sim()
        s.run(0, 200, 10)
        t = 210
        while s.state() != "off":
            s.tick(t, {E1: False})
            t += 10
        self.assertEqual(s.h[E1]["tries"], [])
        s.tick(t + 100, {E1: True})
        self.assertEqual(s.state(), "off")                    # one good check is not back
        s.tick(t + 110, {E1: True})
        self.assertEqual(s.state(), "ok")
        self.assertEqual(s.h[E1]["tries"], [])
        self.assertEqual(kinds_of(e for _, e in s.events), ["exit_dead", "exit_alive"])
        s.tick(t + 120, {E1: False})
        self.assertEqual(s.state(), "ok")                     # a fresh window: one failure is not «half»

    def test_silence_is_dead_at_once(self):
        s = Sim(kind="xray")
        s.run(0, 100, 10)
        s.tick(110, per={E1: {"silent": True}})
        self.assertEqual(s.state(), "off")
        self.assertEqual(s.h[E1]["reasons"], ["dead"])


class PauseTest(unittest.TestCase):
    def kill(self, s, t, eid=E1):
        s.tick(t, per={eid: {"silent": True}})
        self.assertIn("dead", s.h[eid]["reasons"])

    def test_backoff_grows_and_resets_after_an_hour(self):
        s = Sim()
        s.run(0, 50, 10)
        want = [30, 60, 120, 240, 480, 600, 600]
        t = 100
        for w in want:
            self.kill(s, t)
            self.assertEqual(s.h[E1]["off_until"] - t, w)
            s.tick(t + w, {E1: True})
            s.tick(t + w + 10, {E1: True})                    # the only exit of its group: no pause anyway
            t += w + 20
        self.kill(s, t + 3700)
        self.assertEqual(s.h[E1]["off_until"] - (t + 3700), 30)

    def test_the_last_usable_exit_is_never_held(self):
        s = Sim(exits=(E1,))
        s.run(0, 50, 10)
        self.kill(s, 60)
        s.tick(65, {E1: True})
        s.tick(70, {E1: True})                                # off_until = 90, but it is the only one: at once
        self.assertEqual(s.state(), "ok")
        self.assertEqual(s.first("exit_alive"), 70)

    def test_another_usable_exit_lets_the_pause_hold(self):
        """The pause — the policies of stage 1 (here «Стандарт»); «Как раньше» has none (test_compat_has_no_pause)."""
        for other in ("ok", "standby"):
            s = Sim("standard", exits=(E1, E2))
            if other == "standby":
                s.base[E2] = {"manual": "standby"}
            for t in range(0, 60, 10):
                s.tick(t, {E1: True, E2: True})
            self.kill(s, 60)
            s.tick(65, {E1: True, E2: True})
            s.tick(70, {E1: True, E2: True})
            h = s.h[E1]
            self.assertEqual((h["state"], h["recovering"], h["off_until"]), ("off", True, 90), other)
            self.assertIsNone(s.first("exit_alive"))
            for lang, word in (("ru", "отвечает снова · новые устройства — с "), ("en", "responding again · new devices from ")):
                txt = health.pause_text(h, lang, tz=health.datetime.timezone.utc)
                self.assertEqual(txt, word + "00:01")
            s.tick(80, {E1: True, E2: True})
            self.assertIsNone(s.first("exit_alive"))
            s.tick(90, {E1: True, E2: True})
            self.assertEqual(s.first("exit_alive"), 90)
            self.assertFalse(s.h[E1]["recovering"])
            self.assertEqual(health.pause_text(s.h[E1]), "")

    def test_compat_has_no_pause(self):
        """REV-s1-t4 З4, §1.3 отличие 3: «Как раньше» = 3.3.5 — «снова работает» after two good checks in a row, no
        pause, though another exit of the group is usable."""
        for preset, alive in (("compat", 70), ("standard", None), ("strict", None), ("economy", None)):
            s = Sim(preset, exits=(E1, E2))
            for t in range(0, 60, 10):
                s.tick(t, {E1: True, E2: True})
            self.kill(s, 60)
            s.tick(65, {E1: True, E2: True})
            self.assertEqual(s.state(), "off", preset)                     # one good check: not yet
            s.tick(70, {E1: True, E2: True})
            self.assertEqual(s.first("exit_alive"), alive, preset)
            self.assertEqual(s.state(), "ok" if alive else "off", preset)
            self.assertEqual(s.h[E1]["recovering"], not alive, preset)
        self.assertFalse(health.pauses({"preset": "compat"}))
        self.assertFalse(health.pauses({}))
        self.assertTrue(health.pauses({"preset": "custom"}))

    def test_two_back_at_once_the_first_by_tier_and_id(self):
        for tiers, first in (([[E2, E1]], E1), ([[E2], [E1]], E2)):
            s = Sim("standard", exits=())                                  # the pause: stage 1 policies only
            s.doc["groups"][0]["tiers"] = [P.default_tier("t%d" % (i + 1), ex) for i, ex in enumerate(tiers)]
            s.kinds = {E1: "awg", E2: "awg"}
            for t in range(0, 60, 10):
                s.tick(t, {E1: True, E2: True})
            self.kill(s, 60, E1)
            self.kill(s, 60, E2)
            s.tick(65, {E1: True, E2: True})
            s.tick(70, {E1: True, E2: True})
            other = E2 if first == E1 else E1
            self.assertEqual((s.state(first), s.state(other)), ("ok", "off"), tiers)  # the tier, then the id
            self.assertTrue(s.h[other]["recovering"])

    def test_clock_jump_back_clamps_off_until(self):
        s = Sim(exits=(E1, E2))
        for t in range(0, 60, 10):
            s.tick(10000 + t, {E1: True, E2: True})
        self.kill(s, 10060)
        h = copy.deepcopy(s.h[E1])
        back = 10060 - 7200                                    # the wall clock jumped back 2 hours
        health.clamp(h, back, s.doc)
        self.assertLessEqual(h["off_until"], back + 600)
        rec = health.snapshot(s.h[E1])
        h2 = health.from_snapshot(rec, back, s.doc)
        self.assertLessEqual(h2["off_until"], back + 600)
        self.assertLessEqual(h2["since"], back)


class UnstableTest(unittest.TestCase):
    def flap(self, s, t, eid=E1, others=(E2,)):
        """eid falls and comes back (others answer): -> the time after."""
        s.tick(t, per={eid: {"silent": True}})
        w = s.h[eid]["off_until"] - t
        for dt in (w, w + 10):
            s.tick(t + dt, dict([(eid, True)] + [(o, True) for o in others]))
        return t + w + 20

    def warm(self, s, exits=(E1, E2)):
        for t in range(0, 60, 10):
            s.tick(t, dict((e, True) for e in exits))

    def test_three_falls_in_15_min_go_to_standby(self):
        s = Sim("standard", exits=(E1, E2))
        self.warm(s)
        t = self.flap(s, 100)
        t = self.flap(s, t)
        self.assertEqual(s.state(), "ok")                     # 2 falls: not unstable
        t = self.flap(s, t)
        h = s.h[E1]
        self.assertEqual((h["state"], h["reasons"], h["unstable"]), ("standby", ["unstable"], True))
        until = h["unstable_until"]
        self.assertEqual(until, t - 20 + 0 + 10 + UNSTABLE)
        st = [e for _, e in s.events if e["kind"] == "exit_standby"]
        self.assertEqual((len(st), st[0]["why"], st[0]["flaps"]), (1, "unstable", 3))
        self.assertNotIn("exit_alive", [e["kind"] for tt, e in s.events if tt >= t - 20])
        txt = health.unstable_text(h, t, "en", health.datetime.timezone.utc)
        self.assertTrue(txt.startswith("on standby: unstable (3 drops in 15 min), back at "), txt)
        self.assertTrue(health.unstable_text(h, t, "ru").startswith("в запасе: нестабилен (3 падения за 15 мин)"))
        s.tick(until - 10, {E1: True, E2: True})
        self.assertEqual(s.state(), "standby")
        s.tick(until, {E1: True, E2: True})
        self.assertEqual(s.state(), "ok")
        self.assertEqual(s.first("exit_recovered", after=until), until)
        self.assertFalse(s.h[E1]["unstable"])

    def test_the_only_exit_stays_in_work_marked(self):
        s = Sim("standard", exits=(E1,))
        self.warm(s, (E1,))
        t = 100
        for _ in range(3):
            t = self.flap(s, t, others=())
        h = s.h[E1]
        self.assertEqual((h["state"], h["unstable"]), ("ok", True))
        self.assertNotIn("exit_standby", [e["kind"] for _, e in s.events])
        self.assertTrue(health.unstable_text(h, t, "ru").startswith("нестабилен · до "))

    def test_another_good_appears_before_the_end(self):
        s = Sim("standard", exits=(E1, E2))
        self.warm(s)
        s.base[E2] = {"manual": "maintenance"}                  # E2 out: E1 alone
        t = 100
        for _ in range(3):
            t = self.flap(s, t)
        self.assertEqual(s.state(), "ok")
        s.base[E2] = {}
        s.tick(t, {E1: True, E2: True})                       # E2 back in work
        s.tick(t + 10, {E1: True, E2: True})
        self.assertEqual(s.state(), "standby")
        self.assertIn("exit_standby", kinds_of(e for tt, e in s.events if tt >= t))
        s.base[E2] = {"manual": "maintenance"}                  # the others gone: back in work at once
        s.tick(t + 20, {E1: True, E2: True})
        self.assertEqual(s.state(), "ok")
        self.assertIn("exit_recovered", kinds_of(e for tt, e in s.events if tt >= t + 20))

    def test_compat_is_33_5(self):
        s = Sim("compat", exits=(E1, E2))
        self.warm(s)
        t = 100
        for _ in range(4):
            t = self.flap(s, t)
        self.assertEqual((s.state(), s.h[E1]["unstable"]), ("ok", False))
        self.assertNotIn("exit_standby", [e["kind"] for _, e in s.events])

    def test_restore_clears_it(self):
        s = Sim("standard", exits=(E1, E2))
        self.warm(s)
        t = 100
        for _ in range(3):
            t = self.flap(s, t)
        self.assertEqual(s.state(), "standby")
        s.tick(t, {E1: True, E2: True}, per={E1: {"restore": True}})
        self.assertEqual((s.state(), s.h[E1]["unstable"]), ("ok", False))

    def test_unstable_until_survives_a_restart_and_is_clamped(self):
        s = Sim("standard", exits=(E1, E2))
        self.warm(s)
        t = 100
        for _ in range(3):
            t = self.flap(s, t)
        until = s.h[E1]["unstable_until"]
        rec = health.snapshot(s.h[E1])
        s.h = {E1: health.from_snapshot(rec, t, s.doc), E2: health.from_snapshot(health.snapshot(s.h[E2]), t, s.doc)}
        s.tick(t + 10, {E1: True, E2: True})
        self.assertEqual(s.state(), "standby")
        s.tick(until - 1, {E1: True, E2: True})
        self.assertEqual(s.state(), "standby")
        s.tick(until, {E1: True, E2: True})
        self.assertEqual(s.state(), "ok")
        rec["unstable_until"] = t + 5 * 3600                   # a jump of the clock
        self.assertEqual(health.from_snapshot(rec, t, s.doc)["unstable_until"], t + UNSTABLE)


UNSTABLE = health.UNSTABLE_S


class RulesTest(unittest.TestCase):
    def rule(self, conds, then="degraded", back=None, hold=None, bhold=None, match="all", bmatch="all"):
        r = {"id": "r-x", "name": "", "enabled": True, "exits": "all",
             "when": {"match": match, "conds": conds, "hold": hold or {"samples": 1}}, "then": then, "push": True}
        if back is not None:
            r["back"] = {"match": bmatch, "conds": back, "hold": bhold or {"samples": 1}, "manual": False}
        return r

    def sim(self, rule, **kw):
        s = Sim(**kw)
        s.doc["groups"][0]["rules"] = [rule]
        return s

    def test_samples_hold_and_hysteresis(self):
        r = self.rule([{"metric": "rtt", "op": ">", "value": 300, "unit": "ms"}], back=[
            {"metric": "rtt", "op": "<", "value": 200, "unit": "ms"}], hold={"samples": 3}, bhold={"samples": 2})
        s = self.sim(r)
        for t, rtt in ((0, 400), (10, 400), (20, 100), (30, 400), (40, 400)):
            s.tick(t, {E1: {"ok": True, "rtt": rtt, "at": t}})
            self.assertEqual(s.state(), "ok", t)               # a false sample resets the count
        s.tick(50, {E1: {"ok": True, "rtt": 400, "at": 50}})
        self.assertEqual(s.state(), "degraded")
        ev = [e for _, e in s.events if e["kind"] == "exit_degraded"][0]
        self.assertEqual((ev["rule"], ev["metric"], ev["value"], ev["threshold"]), ("r-x", "rtt", 400, 300))
        for t, rtt in ((60, 250), (70, 250), (80, 150), (90, 250)):  # between the thresholds: stays
            s.tick(t, {E1: {"ok": True, "rtt": rtt, "at": t}})
            self.assertEqual(s.state(), "degraded", t)
        s.tick(100, {E1: {"ok": True, "rtt": 150, "at": 100}})
        s.tick(110, {E1: {"ok": True, "rtt": 150, "at": 110}})
        self.assertEqual(s.state(), "ok")
        self.assertEqual(kinds_of(e for _, e in s.events), ["exit_degraded", "exit_recovered"])

    def test_minutes_by_the_real_period(self):
        r = self.rule([{"metric": "rtt", "op": ">", "value": 300, "unit": "ms"}],
                      back=[{"metric": "rtt", "op": "<", "value": 200, "unit": "ms"}], hold={"minutes": 2},
                      bhold={"minutes": 2})
        s = self.sim(r)
        for t in range(0, 1200, 120):                          # an idle AWG: one sample in 2 min
            s.tick(t, {E1: {"ok": True, "rtt": 100, "at": t}})
        t0 = 1200
        for t in range(t0, t0 + 480, 120):
            s.tick(t, {E1: {"ok": True, "rtt": 500, "at": t}})
        self.assertEqual(s.state(), "degraded")                # ceil(0.8 · 120 / 120) = 1 sample — it fires
        s2 = self.sim(copy.deepcopy(r))
        for t in range(0, 300, 10):
            s2.tick(t, {E1: {"ok": True, "rtt": 100, "at": t}})
        for t in range(300, 410, 10):
            s2.tick(t, {E1: {"ok": True, "rtt": 500, "at": t}})
        self.assertEqual(s2.state(), "ok")                      # 110 s of 120
        s2.tick(420, {E1: {"ok": True, "rtt": 500, "at": 420}})
        self.assertEqual(s2.state(), "degraded")

    def test_all_any_and_unknown(self):
        cond_unknown = {"metric": "speed", "op": "<", "value": 5, "unit": "mbps"}          # speed test off
        cond_true = {"metric": "rtt", "op": ">", "value": 10, "unit": "ms"}
        for match, conds, want in (("all", [cond_true, cond_unknown], "ok"), ("any", [cond_true, cond_unknown], "degraded"),
                                   ("any", [cond_unknown], "ok"), ("all", [cond_unknown], "ok")):
            r = self.rule(conds, back=[{"metric": "rtt", "op": "<", "value": 5, "unit": "ms"}], match=match)
            s = self.sim(r)
            s.tick(0, {E1: {"ok": True, "rtt": 50, "at": 0}})
            self.assertEqual(s.state(), want, (match, conds))

    def test_x_norm_needs_an_hour(self):
        r = self.rule([{"metric": "rtt", "op": ">", "value": 2, "unit": "x_norm"}],
                      back=[{"metric": "rtt", "op": "<", "value": 1.5, "unit": "x_norm"}])
        s = self.sim(r)
        for t in range(0, 3000, 10):
            s.tick(t, {E1: {"ok": True, "rtt": 50, "at": t}})
        s.tick(3000, {E1: {"ok": True, "rtt": 500, "at": 3000}})
        self.assertEqual(s.state(), "ok")                       # less than an hour: unknown
        for t in range(3010, 3700, 10):
            s.tick(t, {E1: {"ok": True, "rtt": 50, "at": t}})
        self.assertEqual(len(s.h[E1]["norm_rtt"]), 1)
        s.tick(3700, {E1: {"ok": True, "rtt": 500, "at": 3700}})
        self.assertEqual(s.state(), "degraded")

    def test_loss_with_min_fails(self):
        r = self.rule([{"metric": "loss", "op": ">", "value": 5, "unit": "pct", "min_fails": 2}],
                      back=[{"metric": "loss", "op": "<", "value": 1, "unit": "pct"}])
        s = self.sim(r)
        s.run(0, 200, 10)
        s.tick(210, {E1: False})                                 # 1 of 10 = 10 % > 5 %, but one failure
        self.assertEqual(s.state(), "ok")
        s.tick(220, {E1: True})
        s.tick(230, {E1: False})
        self.assertEqual(s.state(), "degraded")

    def test_push_rule_keeps_the_state(self):
        r = self.rule([{"metric": "rtt", "op": ">", "value": 300, "unit": "ms"}], then="push",
                      back=[{"metric": "rtt", "op": "<", "value": 200, "unit": "ms"}])
        s = self.sim(r)
        s.tick(0, {E1: {"ok": True, "rtt": 400, "at": 0}})
        self.assertEqual(s.state(), "ok")
        self.assertEqual(kinds_of(e for _, e in s.events), ["exit_rule_push"])
        for t, rtt in ((10, 100), (20, 400), (30, 100), (3600, 400)):   # fired again within the hour: no push
            s.tick(t, {E1: {"ok": True, "rtt": rtt, "at": t}})
        self.assertEqual(kinds_of(e for _, e in s.events), ["exit_rule_push", "exit_rule_push"])

    def test_remove_and_standby_rules(self):
        for then, want in (("remove", "off"), ("standby", "standby")):
            r = self.rule([{"metric": "rtt", "op": ">", "value": 300, "unit": "ms"}], then=then,
                          back=[{"metric": "rtt", "op": "<", "value": 200, "unit": "ms"}])
            s = self.sim(r)
            s.tick(0, {E1: {"ok": True, "rtt": 50, "at": 0}})
            s.tick(10, {E1: {"ok": True, "rtt": 400, "at": 10}})
            self.assertEqual(s.state(), want)
            ev = [e for _, e in s.events if e["kind"] in ("exit_removed", "exit_standby")][0]
            self.assertEqual(ev["why"], "rule:r-x")

    def test_manual_back(self):
        r = self.rule([{"metric": "rtt", "op": ">", "value": 300, "unit": "ms"}], then="standby", back=[])
        r["back"] = {"manual": True}
        s = self.sim(r)
        s.tick(0, {E1: {"ok": True, "rtt": 400, "at": 0}})
        s.tick(10, {E1: {"ok": True, "rtt": 40, "at": 10}})
        self.assertEqual(s.state(), "standby")
        s.tick(20, {E1: {"ok": True, "rtt": 40, "at": 20}}, per={E1: {"restore": True}})
        self.assertEqual(s.state(), "ok")


class CountryTest(unittest.TestCase):
    def geo(self, s, t, loc, how="plan", node=None, eid=E1, **o):
        per = {eid: dict(o, geo={"loc": loc, "how": how})}
        return s.tick(t, node=node, per=per)

    def test_change_needs_a_pass_and_two_confirmations_then_only_a_push(self):
        s = Sim()
        s.base[E1] = {"country": {"expected": "DE", "notified": []}}
        s.run(0, 30, 10)
        ev = self.geo(s, 40, "FI")
        self.assertEqual([e["kind"] for e in ev if e.get("request")], ["geo_pass"])
        self.assertIsNone(s.first("exit_country_changed"))
        ev = s.tick(50, node={"geo_pass": {"direct_loc": "NL", "locs": {E1: "FI"}}})
        self.assertEqual([e["kind"] for e in ev if e.get("request")], ["geo_confirm"])
        self.assertIsNone(s.first("exit_country_changed"))
        ev = self.geo(s, 350, "FI", "confirm")
        ch = [e for e in ev if e["kind"] == "exit_country_changed"]
        self.assertEqual((ch[0]["old"], ch[0]["new"], ch[0]["all_exits"]), ("DE", "FI", False))
        self.assertIn("country_notified", [e["kind"] for e in ev])
        self.assertEqual(s.state(), "ok")                         # only a push: the traffic goes as is
        s.base[E1] = {"country": {"expected": "DE", "notified": ["FI"]}}
        self.geo(s, 400, "DE")
        self.geo(s, 500, "FI")
        s.tick(510, node={"geo_pass": {"direct_loc": "NL", "locs": {E1: "FI"}}})
        ev = self.geo(s, 810, "FI", "confirm")
        self.assertNotIn("exit_country_changed", [e["kind"] for e in ev])   # the same new country: never twice
        s.base[E1] = {"country": {"expected": "FI", "notified": []}}           # «Принять новую страну»
        self.geo(s, 900, "SE")
        s.tick(910, node={"geo_pass": {"direct_loc": "NL", "locs": {E1: "SE"}}})
        ev = self.geo(s, 1210, "SE", "confirm")
        self.assertIn("exit_country_changed", [e["kind"] for e in ev])

    def test_signs_of_a_broken_measure(self):
        s = Sim(exits=(E1, E2))
        for e in (E1, E2):
            s.base[e] = {"country": {"expected": "DE", "notified": []}}
        s.tick(0, {E1: True, E2: True})
        self.geo(s, 10, "NL")
        ev = s.tick(20, node={"geo_pass": {"direct_loc": "NL", "locs": {E1: "NL", E2: "DE"}}})
        ch = [e for e in ev if e["kind"] == "exit_country_changed"]
        self.assertEqual((len(ch), ch[0]["all_exits"]), (1, True))   # loc = the direct path of the node
        self.assertEqual(s.state(), "ok")
        s2 = Sim(exits=(E1, E2))
        for e in (E1, E2):
            s2.base[e] = {"country": {"expected": "DE", "notified": []}}
        s2.tick(0, {E1: True, E2: True})
        self.geo(s2, 10, "FI")
        ev = s2.tick(20, node={"geo_pass": {"direct_loc": "NL", "locs": {E1: "FI", E2: "FI"}}})
        self.assertTrue(any(e["kind"] == "exit_country_changed" and e["all_exits"] for e in ev))   # all changed

    def test_the_signs_are_the_ones_of_the_unit(self):
        # T4 (REV-s1-t6-2): one function for both sides — health._geo asks probe.geo.suspect
        from kaskad.gw.probe import geo as pgeo
        for locs, direct in (({E1: "FI", E2: None}, "NL"), ({E1: "FI", E2: "FI"}, "NL"), ({E1: "NL", E2: "DE"}, "NL"),
                             ({E1: "FI", E2: "DE"}, "NL"), ({E1: "FI"}, "FI"), ({E1: "FI"}, None)):
            s = Sim(exits=(E1, E2))
            for e in (E1, E2):
                s.base[e] = {"country": {"expected": "DE", "notified": []}}
            s.tick(0, {E1: True, E2: True})
            self.geo(s, 10, locs[E1])
            ev = s.tick(20, node={"geo_pass": {"direct_loc": direct, "locs": locs}})
            told = any(e["kind"] == "exit_country_changed" and e["all_exits"] for e in ev)
            sign = pgeo.suspect(E1, locs[E1], {E1: "DE", E2: "DE"}, locs, direct)
            self.assertEqual(told, bool(sign), (locs, direct))
        s = Sim(exits=(E1, E2))
        for e in (E1, E2):
            s.base[e] = {"country": {"expected": "DE", "notified": []}}
        s.tick(0, {E1: True, E2: True})
        self.geo(s, 10, "FI")
        with mock.patch.object(pgeo, "suspect", lambda *a, **k: "all_exits"):
            ev = s.tick(20, node={"geo_pass": {"direct_loc": "NL", "locs": {E1: "FI", E2: "DE"}}})
        self.assertTrue(any(e["kind"] == "exit_country_changed" and e["all_exits"] for e in ev))

    def test_first_seen_and_disabled(self):
        s = Sim()
        ev = self.geo(s, 0, "DE")
        self.assertEqual([e["kind"] for e in ev if e.get("request")], ["country_first"])
        s.doc["groups"][0]["checks"]["geo"]["enabled"] = False
        s.doc["groups"][0]["rules"] = [{"id": "r-c", "exits": "all", "then": "degraded",
                                        "when": {"match": "all", "conds": [{"metric": "country", "op": "!=",
                                                                            "value": "RU", "unit": "iso"}],
                                                 "hold": {"samples": 1}},
                                        "back": {"match": "all", "conds": [{"metric": "country", "op": "==",
                                                                            "value": "RU", "unit": "iso"}],
                                                 "hold": {"samples": 2}}}]
        s.tick(10, {E1: True})
        self.assertEqual(s.state(), "ok")                          # geo off: «unknown» -> false
        s.doc["groups"][0]["checks"]["geo"]["enabled"] = True
        s.tick(20, {E1: True})
        self.assertEqual(s.state(), "degraded")                    # the condition «Страна не та»


class QuotaTest(unittest.TestCase):
    Q = {"period": "month", "limit_gb": 100, "reset_day": 1, "warn_pct": 80, "action": "remove"}

    def tr(self, used, per="2026-10-01", warned=None):
        return {"used_gb": used, "period": per, "warned": warned or [], "reset": "2026-11-01"}

    def test_warn_once_full_action_and_reset(self):
        s = Sim(exits=(E1, E2))
        s.base[E1] = {"quota": dict(self.Q)}
        s.tick(0, {E1: True, E2: True}, per={E1: {"traffic": self.tr(50)}})
        s.tick(10, {E1: True, E2: True}, per={E1: {"traffic": self.tr(85)}})
        s.tick(20, {E1: True, E2: True}, per={E1: {"traffic": self.tr(90)}})
        q = [e for _, e in s.events if e["kind"] == "exit_quota"]
        self.assertEqual([e["pct"] for e in q], [85])              # 80 % once
        s2 = Sim()
        s2.base[E1] = {"quota": dict(self.Q)}
        s2.tick(0, {E1: True}, per={E1: {"traffic": self.tr(85, warned=[80])}})   # after a restart: by «warned»
        self.assertEqual([e for _, e in s2.events if e["kind"] == "exit_quota"], [])
        s.tick(30, {E1: True, E2: True}, per={E1: {"traffic": self.tr(100)}})
        self.assertEqual(s.state(), "off")
        self.assertEqual(s.h[E1]["reasons"], ["quota"])
        rm = [e for _, e in s.events if e["kind"] == "exit_removed"]
        self.assertEqual(rm[0]["why"], "quota")
        for t in range(40, 400, 10):                               # probes paused: never dead without samples
            s.tick(t, {E2: True}, per={E1: {"traffic": self.tr(100)}})
        self.assertEqual(s.h[E1]["reasons"], ["quota"])
        s.tick(400, {E1: False, E2: True}, per={E1: {"traffic": self.tr(100)}})   # a probe that came anyway
        self.assertNotIn("dead", s.h[E1]["reasons"])
        s.tick(410, {E2: True}, per={E1: {"traffic": self.tr(0, per="2026-11-01")}})   # the reset day
        self.assertEqual(s.state(), "unknown")
        s.tick(420, {E1: True, E2: True}, per={E1: {"traffic": self.tr(0, per="2026-11-01")}})
        self.assertEqual(s.state(), "ok")
        self.assertIn("exit_recovered", kinds_of(e for t, e in s.events if t >= 410))

    def test_returns(self):
        for how in ("limit", "action", "none", "restore"):
            s = Sim()
            s.base[E1] = {"quota": dict(self.Q)}
            s.tick(0, {E1: True}, per={E1: {"traffic": self.tr(120)}})
            self.assertEqual(s.state(), "off")
            per = {"traffic": self.tr(120)}
            if how == "limit":
                s.base[E1] = {"quota": dict(self.Q, limit_gb=200)}
            elif how == "action":
                s.base[E1] = {"quota": dict(self.Q, action="push")}
            elif how == "none":
                s.base[E1] = {}
            else:
                per["restore"] = True
            s.tick(10, per={E1: per})
            self.assertEqual(s.state(), "unknown", how)
            s.tick(20, {E1: True}, per={E1: {"traffic": self.tr(120)}})
            self.assertEqual(s.state(), "ok", how)

    def test_standby_and_push_keep_probing(self):
        for act, want in (("standby", "standby"), ("push", "ok")):
            s = Sim()
            s.base[E1] = {"quota": dict(self.Q, action=act)}
            s.tick(0, {E1: True}, per={E1: {"traffic": self.tr(120)}})
            self.assertEqual(s.state(), want)
            t = 10
            while s.state() != "off" and t < 400:
                s.tick(t, {E1: False}, per={E1: {"traffic": self.tr(120)}})
                t += 10
            self.assertIn("dead", s.h[E1]["reasons"], act)        # the probes go on

    def test_quota_period(self):
        q = {"period": "month", "reset_day": 15}
        self.assertEqual(health.quota_period(q, (2026, 10, 16)), "2026-10-15")
        self.assertEqual(health.quota_period(q, (2026, 10, 14)), "2026-09-15")
        self.assertEqual(health.quota_period(q, (2026, 1, 3)), "2025-12-15")
        self.assertEqual(health.quota_period({"period": "day"}, (2026, 10, 16)), "2026-10-16")
        self.assertEqual(health.next_reset(q, (2026, 12, 20)), (2027, 1, 15))
        self.assertEqual(health.next_reset({"period": "day"}, (2026, 12, 31)), (2027, 1, 1))


class NodeAndManualTest(unittest.TestCase):
    def test_offline_freezes(self):
        s = Sim()
        s.run(0, 100, 10)
        before = copy.deepcopy(s.h)
        for t in range(110, 400, 10):
            ev = s.tick(t, {E1: False}, node={"offline": True})
            self.assertEqual(ev, [])
        self.assertEqual(s.h[E1]["state"], before[E1]["state"])
        self.assertEqual(s.h[E1]["tries"], before[E1]["tries"])
        self.assertEqual(s.h[E1]["samples"], before[E1]["samples"])

    def test_ru_offline_freezes_only_russian_groups(self):
        s = Sim(exits=(E1,))
        s.doc["groups"].append(P.default_group("g-abcdef", "ru", "ru_home", name="Дом", exits=[R1]))
        s.kinds[R1] = "awg"
        for t in range(0, 100, 15):
            s.tick(t, {E1: True, R1: True})
        for t in range(100, 300, 15):
            s.tick(t, {E1: False, R1: False}, node={"ru_offline": True})
        self.assertEqual((s.state(E1), s.state(R1)), ("off", "ok"))

    def test_manual_states_and_disabled(self):
        for o, state, kind, why in (({"manual": "standby"}, "standby", "exit_standby", "manual"),
                                    ({"manual": "standby", "manual_why": "sub_confirm"}, "standby", "exit_standby",
                                     "sub_confirm"),
                                    ({"manual": "maintenance"}, "off", "exit_removed", "maintenance"),
                                    ({"enabled": False}, "off", "exit_removed", "disabled")):
            s = Sim()
            s.run(0, 30, 10)
            s.base[E1] = o
            s.tick(40, {E1: True})
            self.assertEqual(s.state(), state, o)
            ev = [e for _, e in s.events if e["kind"] == kind][0]
            self.assertEqual(ev["why"], why)
            s.base[E1] = {}
            s.tick(50, {E1: True})
            self.assertEqual(s.state(), "ok")
            self.assertEqual(kinds_of(e for t, e in s.events if t == 50), ["exit_recovered"])

    def test_transitions_are_recorded(self):
        s = Sim()
        s.run(0, 30, 10)
        s.base[E1] = {"manual": "standby"}
        s.tick(40, {E1: True})
        tr = [e for _, e in s.events if e["kind"] == "transition"]
        self.assertEqual((tr[-1]["from"], tr[-1]["to"], tr[-1]["why"]), ("ok", "standby", "manual"))

    def test_exit_outside_groups_is_only_alive_or_dead(self):
        s = Sim(exits=())
        s.kinds[R1] = "awg"
        for t in range(0, 60, 10):
            s.tick(t, {R1: False})
        self.assertEqual(s.state(R1), "off")
        s.tick(60, {R1: True})
        s.tick(70, {R1: True})
        self.assertEqual(s.state(R1), "ok")                      # no pause outside the groups



# ── REV-s1-t2: the review of T2 ────────────────────────────────────────────

class LegacySnapshotTest(unittest.TestCase):
    """С1: a record of the snapshot of 3.3.5 (``alive``, ``fails``, ``since``) after the update."""

    def test_a_record_of_335_reads_as_its_state(self):
        h = health.from_snapshot({"alive": True, "since": 9000, "fails": 0}, 10000)
        self.assertEqual((h["state"], h["reasons"], h["since"]), ("ok", [], 9000))
        h = health.from_snapshot({"alive": False, "since": 8000, "fails": 3, "fail_since": 7990}, 10000)
        self.assertEqual((h["state"], h["reasons"]), ("off", ["dead"]))
        for rec in ({"alive": False, "fails": 0, "new": True}, {"alive": False, "fails": 0}):
            self.assertEqual(health.from_snapshot(rec, 10000)["state"], "unknown")
        rec = health.snapshot(health.from_snapshot({"alive": True}, 10000))
        self.assertNotIn("legacy", rec)                       # the next snapshot is of stage 1

    def test_the_first_tick_after_the_update_tells_nothing(self):
        s = Sim(exits=(E1, E2), kind="xray")
        s.h = dict((e, health.from_snapshot({"alive": True, "since": 9000, "fails": 0}, 10000, s.doc))
                   for e in (E1, E2))
        ev = s.tick(10000)                                   # the observatory is still silent: no probes
        self.assertEqual(ev, [])
        self.assertEqual((s.state(E1), s.state(E2)), ("ok", "ok"))

    def test_what_335_already_had_is_not_told_again(self):
        s = Sim(exits=(E1, E2, E3))
        s.h = {E1: health.from_snapshot({"alive": True, "since": 9000}, 10000, s.doc),
               E2: health.from_snapshot({"alive": False, "since": 9000, "fails": 4}, 10000, s.doc),
               E3: health.from_snapshot({"alive": True, "since": 9000}, 10000, s.doc)}
        ev = s.tick(10000, {E3: True}, per={E1: {"enabled": False}})   # switched off in 3.3.5
        self.assertEqual(ev, [])
        self.assertEqual((s.state(E1), s.state(E2)), ("off", "off"))
        for t in range(10010, 10100, 10):                   # dead in 3.3.5: no new exit_dead
            s.tick(t, {E2: False, E3: True}, per={E1: {"enabled": False}})
        self.assertIsNone(s.first("exit_dead", E2))
        s.tick(10100, {E2: True, E3: True}, per={E1: {"enabled": False}})
        s.tick(10110, {E2: True, E3: True}, per={E1: {"enabled": False}})
        self.assertIsNotNone(s.first("exit_alive", E2))      # back: told as in 3.3.5
        s.tick(10120, {E3: True}, per={E1: {"enabled": True}})
        s.tick(10130, {E3: True}, per={E1: {"enabled": False}})
        self.assertIsNotNone(s.first("exit_removed", E1))    # a change after the first tick is told


class RestartTest(unittest.TestCase):
    """С2: the rules and the quota survive a restart of the collector — no state change, no event again."""

    def restart(self, s, t):
        s.h = dict((e, health.from_snapshot(health.snapshot(h), t, s.doc)) for e, h in s.h.items())

    def rule(self, then="degraded", back=None):
        r = {"id": "r-x", "name": "", "enabled": True, "exits": "all", "then": then, "push": True,
             "when": {"match": "all", "conds": [{"metric": "rtt", "op": ">", "value": 300, "unit": "ms"}],
                      "hold": {"samples": 1}}}
        r["back"] = back if back is not None else {
            "match": "all", "conds": [{"metric": "rtt", "op": "<", "value": 200, "unit": "ms"}],
            "hold": {"samples": 2}, "manual": False}
        return r

    def test_a_rule_state_survives(self):
        s = Sim(exits=(E1, E2))
        s.doc["groups"][0]["rules"] = [self.rule()]
        s.tick(0, {E1: {"ok": True, "rtt": 400, "at": 0}, E2: True})
        self.assertEqual(s.state(), "degraded")
        rec = health.snapshot(s.h[E1])
        self.assertEqual(set(rec["rules"]["r-x"]), {"active", "then", "info", "since"})   # no windows
        self.restart(s, 300)
        n = len(s.events)
        s.tick(300, {E1: {"ok": True, "rtt": 400, "at": 300}, E2: True})
        self.assertEqual((s.state(), s.events[n:]), ("degraded", []))
        s.tick(310, {E1: {"ok": True, "rtt": 50, "at": 310}, E2: True})
        s.tick(320, {E1: {"ok": True, "rtt": 50, "at": 320}, E2: True})
        self.assertEqual(s.state(), "ok")                      # the return holds as before

    def test_manual_back_survives(self):
        s = Sim(exits=(E1, E2))
        s.doc["groups"][0]["rules"] = [self.rule("standby", {"manual": True})]
        s.tick(0, {E1: {"ok": True, "rtt": 400, "at": 0}, E2: True})
        self.assertEqual(s.state(), "standby")
        self.restart(s, 100)
        n = len(s.events)
        for t in (100, 110, 120):
            s.tick(t, {E1: {"ok": True, "rtt": 40, "at": t}, E2: True})
        self.assertEqual((s.state(), s.events[n:]), ("standby", []))   # «вернуть только вручную»
        s.tick(130, {E1: {"ok": True, "rtt": 40, "at": 130}, E2: True}, per={E1: {"restore": True}})
        self.assertEqual(s.state(), "ok")

    def test_push_once_an_hour_survives(self):
        s = Sim()
        s.doc["groups"][0]["rules"] = [self.rule("push")]
        s.tick(0, {E1: {"ok": True, "rtt": 400, "at": 0}})
        self.restart(s, 60)
        for t, rtt in ((60, 400), (70, 50), (80, 400)):
            s.tick(t, {E1: {"ok": True, "rtt": rtt, "at": t}})
        self.assertEqual(kinds_of(e for _, e in s.events), ["exit_rule_push"])

    def test_restore_of_the_quota_survives(self):
        q = {"period": "month", "limit_gb": 10, "reset_day": 1, "warn_pct": 80, "action": "remove"}
        tr = {"used_gb": 11, "period": "2026-10-01", "warned": [80, 100]}
        s = Sim(exits=(E1, E2))
        s.base[E1] = {"quota": q, "traffic": tr}
        s.tick(0, {E1: True, E2: True})
        self.assertEqual(s.state(), "off")
        s.tick(10, {E1: True, E2: True}, per={E1: {"restore": True}})
        s.tick(20, {E1: True, E2: True})
        self.assertEqual(s.state(), "ok")
        self.restart(s, 25)
        n = len(s.events)
        s.tick(30, {E1: True, E2: True})
        self.assertEqual((s.state(), s.events[n:]), ("ok", []))
        s.base[E1] = {"quota": q, "traffic": dict(tr, period="2026-11-01")}    # the next period: the quota again
        s.tick(40, {E1: True, E2: True})
        self.assertEqual(s.state(), "off")


class NodeOnlineTest(unittest.TestCase):
    """З5 (§4.6): the network of the node is back — the windows of the exits start anew."""

    def test_windows_anew_by_side(self):
        s = Sim(exits=(E1, R1))
        s.doc["groups"].append(P.default_group("g-abcdef", "ru", "ru_home", exits=[R1]))
        s.doc["groups"][0]["tiers"][0]["exits"] = [E1]
        s.tick(0, {E1: True, R1: True})
        s.tick(10, {E1: False, R1: False})
        s.tick(20, {E1: False, R1: False})
        self.assertTrue(s.h[E1]["tries"] and s.h[R1]["samples"])
        got = health.node_online(s.h, s.doc, role="ru")
        self.assertEqual((got[R1]["tries"], got[R1]["samples"], got[R1]["oks"]), ([], [], 0))
        self.assertTrue(got[E1]["tries"])                      # the foreign side keeps its window
        self.assertTrue(s.h[R1]["tries"])                      # a copy: prev untouched
        got = health.node_online(s.h, s.doc)
        self.assertEqual((got[E1]["tries"], got[E1]["samples"]), ([], []))
        self.assertEqual(got[E1]["state"], s.h[E1]["state"])   # the state stays
        s.h = got
        s.tick(30, {E1: False, R1: False})
        s.tick(40, {E1: False, R1: False})
        self.assertNotIn("dead", s.h[E1]["reasons"])           # two failures after the return are not «упал»


class ReviewSmallTest(unittest.TestCase):
    def test_a_good_check_cancels_a_failed_one_up_to_p(self):
        s = Sim()                                              # P = 10, slack 2: 3.3.5's asymmetry
        s.run(0, 100, 10)
        s.tick(110, {E1: False})
        s.tick(119, {E1: True})                                # 9 s: past P − 2, yet a good one replaces
        self.assertEqual(s.h[E1]["tries"][-1], [110, 1])
        s.tick(130, {E1: False})
        s.tick(139, {E1: False})                               # a failed one after P − 2: a check of its own
        self.assertEqual([x[1] for x in s.h[E1]["tries"][-2:]], [0, 0])

    def test_quota_remove_pauses_the_probes(self):
        s = Sim(exits=(E1, E2))
        s.base[E1] = {"quota": {"period": "month", "limit_gb": 10, "reset_day": 1, "warn_pct": 80,
                                "action": "remove"}, "traffic": {"used_gb": 12, "period": "2026-10-01"}}
        for t in range(0, 200, 10):
            s.tick(t, {E1: False, E2: True})
        self.assertEqual(s.state(), "off")
        self.assertIn("quota", s.h[E1]["reasons"])
        self.assertNotIn("dead", s.h[E1]["reasons"])
        self.assertEqual(s.h[E1]["tries"], [])
        self.assertIsNone(s.first("exit_dead"))

    def test_the_same_new_country_is_never_pushed_twice(self):
        s = Sim()
        s.base[E1] = {"country": {"expected": "DE", "notified": []}}
        geo = CountryTest.geo

        def move(t, loc, notified):
            s.base[E1] = {"country": {"expected": "DE", "notified": notified}}
            geo(self, s, t, loc)
            s.tick(t + 10, node={"geo_pass": {"direct_loc": "NL", "locs": {E1: loc}}})
            return geo(self, s, t + 310, loc, "confirm")

        s.run(0, 30, 10)
        self.assertIn("exit_country_changed", [e["kind"] for e in move(40, "FI", [])])
        self.assertIn("exit_country_changed", [e["kind"] for e in move(400, "SE", ["FI"])])
        self.assertNotIn("exit_country_changed", [e["kind"] for e in move(800, "FI", ["FI", "SE"])])

    def test_notified_is_cleared_when_the_expected_country_is_back(self):
        s = Sim()
        s.base[E1] = {"country": {"expected": "DE", "notified": ["FI"]}}
        geo = CountryTest.geo
        s.run(0, 30, 10)
        s.h[E1]["geo"]["loc"] = "FI"                           # the exit is in FI, pushed once
        ev = geo(self, s, 40, "DE")
        self.assertEqual([e["kind"] for e in ev if e.get("request")], ["geo_confirm"])
        ev = geo(self, s, 340, "DE", "confirm")
        self.assertEqual([e["kind"] for e in ev if e.get("request")], ["country_notified_clear"])
        self.assertNotIn("exit_country_changed", [e["kind"] for e in ev])
        s.base[E1] = {"country": {"expected": "DE", "notified": []}}
        self.assertEqual([e for e in geo(self, s, 400, "DE") if e.get("request")], [])   # nothing to clear

    def test_warned_by_the_warn_pct(self):
        q = {"period": "month", "limit_gb": 100, "reset_day": 1, "warn_pct": 70, "action": "remove"}
        for warned, want in (([70], []), ([], [75])):
            s = Sim()
            s.base[E1] = {"quota": q}
            s.tick(0, {E1: True}, per={E1: {"traffic": {"used_gb": 75, "period": "2026-10-01", "warned": warned}}})
            self.assertEqual([e["pct"] for _, e in s.events if e["kind"] == "exit_quota"], want, warned)

    def test_unknown_back_to_what_it_showed_is_no_transition(self):
        s = Sim()
        s.run(0, 30, 10)
        s.h[E1].update(state="unknown", reasons=["unknown"], shown="ok")
        since = s.h[E1]["since"]
        ev = s.tick(40, {E1: True})
        self.assertEqual((s.state(), ev), ("ok", []))
        self.assertEqual(s.h[E1]["since"], since)


if __name__ == "__main__":
    unittest.main()


class RuQ3Test(unittest.TestCase):
    """T15 Q3 (SPEC-groups §6.2, §6.4; SE §4.6): the Russian side of the state machine."""

    geo = CountryTest.geo

    def ru_sim(self, expect="DE"):
        s = Sim(exits=(E1,))
        home = P.default_group("g-abcdef", "ru", "ru_home", name="Дом", exits=[R1])
        home["checks"]["geo"]["expect"] = expect
        s.doc["groups"].append(home)
        s.kinds[R1] = "awg"
        return s

    def test_a_node_in_russia_tells_the_country_of_a_russian_exit(self):
        """§6.2: the sign «loc выхода = loc узла» is not taken for a Russian exit of a node in RU — the owner's
        ``geo.expect = DE`` (stand S22 (б)) gives the push after the confirmations, not «сбой определения страны»."""
        s = self.ru_sim("DE")
        s.tick(0, {E1: True, R1: True})
        self.geo(s, 10, "RU", eid=R1)
        s.tick(20, node={"geo_pass": {"direct_loc": "RU", "locs": {R1: "RU"}}})
        ev = self.geo(s, 330, "RU", "confirm", eid=R1)
        ch = [e for e in ev if e["kind"] == "exit_country_changed"]
        self.assertEqual([(e["exit"], e["old"], e["new"], e["all_exits"], e["role"]) for e in ch],
                         [(R1, "DE", "RU", False, "ru")])
        self.assertEqual(s.state(R1), "ok")                        # only a push (D-024)

    def test_a_foreign_exit_keeps_the_sign_of_the_node(self):
        """The same pass for a FOREIGN exit (expect DE, the node in RU, the exit shows RU) — still the broken measure."""
        s = Sim(exits=(E1,))
        s.base[E1] = {"country": {"expected": "DE", "notified": []}}
        s.tick(0, {E1: True})
        self.geo(s, 10, "RU")
        ev = s.tick(20, node={"geo_pass": {"direct_loc": "RU", "locs": {E1: "RU"}}})
        ch = [e for e in ev if e["kind"] == "exit_country_changed"]
        self.assertEqual([e["all_exits"] for e in ch], [True])

    def test_node_online_of_the_russian_side_starts_only_its_windows(self):
        """§6.4 «Выход из node.ru_offline — окна российских выходов очищаются»: ``node_online(role="ru")``."""
        s = self.ru_sim()
        for t in range(0, 60, 10):
            s.tick(t, {E1: True, R1: True})
        self.assertTrue(s.h[E1]["tries"] and s.h[R1]["tries"])
        got = health.node_online(s.h, s.doc, role="ru")
        self.assertEqual(got[R1]["tries"], [])
        self.assertEqual(got[E1]["tries"], s.h[E1]["tries"])
