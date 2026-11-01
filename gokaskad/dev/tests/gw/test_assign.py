"""The choice of an exit and the pins — kaskad.gw.assign (SPEC-stage1-exits §2.2, §2.6, §4.4–4.5, §11.1 «assign.py»;
SPEC-exit-groups §2.7; T2)."""

import copy
import random
import unittest

from kaskad.gw import assign as A, exits, policy as P

E1, E2, E3, E4, E5 = ("e-000000000%d" % i for i in range(1, 6))


def doc(*tiers, **kw):
    d = P.default_doc("compat")
    g = d["groups"][0]
    g["tiers"] = [P.default_tier("t%d" % (i + 1), list(t)) for i, t in enumerate(tiers)]
    g.update(kw)
    return d


def devs(n, prefix="d-"):
    return [{"id": "%s%08x" % (prefix, i)} for i in range(n)]


def pins_of(dec, gid="main"):
    return dict((d, p["exit"]) for d, p in dec["groups"][gid]["pins"].items())


class PinTest(unittest.TestCase):
    def test_pins_stay_inside_a_tier_and_a_revived_exit_gets_only_new(self):
        d = doc([E1, E2, E3])
        ds = devs(60)
        st = {E1: "ok", E2: "ok", E3: "off"}
        d1 = A.assign({"main": ds}, st, d, None, {}, {}, 1000)
        self.assertNotIn(E3, pins_of(d1).values())
        st[E3] = "ok"                                         # E3 is back
        d2 = A.assign({"main": ds}, st, d, d1, {}, {}, 1010)
        self.assertEqual(pins_of(d2), pins_of(d1))              # nobody jumps back
        self.assertEqual(d2["moves"], [])
        new = devs(300, "d-f")
        d3 = A.assign({"main": ds + new}, st, d, d2, {}, {}, 1020)
        got = [pins_of(d3)[x["id"]] for x in new]
        self.assertTrue(80 <= got.count(E3) <= 120, got.count(E3))   # ≈ 1/3 of the new ones

    def test_evict_from_off_and_standby(self):
        d = doc([E1, E2])
        ds = devs(40)
        st = {E1: "ok", E2: "ok"}
        d1 = A.assign({"main": ds}, st, d, None, {}, {}, 0)
        on1 = [k for k, v in pins_of(d1).items() if v == E1]
        for bad in ("off", "standby"):
            st2 = dict(st, **{E1: bad})
            d2 = A.assign({"main": ds}, st2, d, d1, {}, {}, 10)
            self.assertEqual(set(pins_of(d2).values()), {E2}, bad)
            self.assertEqual(sorted(m["device"] for m in d2["moves"]), sorted(on1))
            self.assertTrue(all(m["why"] == "evict" for m in d2["moves"]))

    def test_degraded(self):
        ds = devs(20)
        st = {E1: "ok", E2: "ok"}
        for evict, want in ((True, {E2}), (False, {E1, E2})):
            d = doc([E1, E2], evict_degraded=evict)
            d1 = A.assign({"main": ds}, st, d, None, {}, {}, 0)
            d2 = A.assign({"main": ds}, dict(st, **{E1: "degraded"}), d, d1, {}, {}, 10)
            self.assertEqual(set(pins_of(d2).values()), want, evict)
        d = doc([E1, E2])
        d1 = A.assign({"main": ds}, st, d, None, {}, {}, 0)
        d2 = A.assign({"main": ds}, {E1: "degraded", E2: "degraded"}, d, d1, {}, {}, 10)
        self.assertEqual(pins_of(d2), pins_of(d1))             # no good one anywhere: nowhere better

    def test_return_to_primary_after_hold_min(self):
        d = doc([E1], [E2])
        ds = devs(5)
        d1 = A.assign({"main": ds}, {E1: "off", E2: "ok"}, d, None, {}, {}, 0)
        self.assertEqual(set(pins_of(d1).values()), {E2})
        ev = A.assign({"main": ds}, {E1: "ok", E2: "ok"}, d, d1, {}, {}, 100)
        self.assertEqual(set(pins_of(ev).values()), {E2})      # the primary good only now
        self.assertEqual(ev["groups"]["main"]["tier_good_since"]["t1"], 100)
        d3 = A.assign({"main": ds}, {E1: "ok", E2: "ok"}, d, ev, {}, {}, 100 + 599)
        self.assertEqual(set(pins_of(d3).values()), {E2})
        d4 = A.assign({"main": ds}, {E1: "ok", E2: "ok"}, d, d3, {}, {}, 100 + 600)
        self.assertEqual(set(pins_of(d4).values()), {E1})
        self.assertTrue(all(m["why"] == "return" for m in d4["moves"]))
        back = [e for e in d4["events"] if e["kind"] == "exit_failover"]
        self.assertEqual((back[0]["back"], back[0]["moved"]), (True, 5))
        # the primary falls during the hold: the count starts anew
        d5 = A.assign({"main": ds}, {E1: "ok", E2: "ok"}, d, d1, {}, {}, 0)
        d5 = A.assign({"main": ds}, {E1: "off", E2: "ok"}, d, d5, {}, {}, 300)
        d5 = A.assign({"main": ds}, {E1: "ok", E2: "ok"}, d, d5, {}, {}, 400)
        d5 = A.assign({"main": ds}, {E1: "ok", E2: "ok"}, d, d5, {}, {}, 900)
        self.assertEqual(set(pins_of(d5).values()), {E2})
        # the backup exit falls: the devices leave at once, no hold (Р7)
        d6 = A.assign({"main": ds}, {E1: "ok", E2: "off"}, d, d1, {}, {}, 10)
        self.assertEqual(set(pins_of(d6).values()), {E1})
        off = A.assign({"main": ds}, {E1: "ok", E2: "ok"}, doc([E1], [E2], return_to_primary={"enabled": False,
                                                                                              "hold_min": 10}),
                       d1, {}, {}, 5000)
        self.assertEqual(set(pins_of(off).values()), {E2})

    def test_order_b2_and_the_last_step(self):
        d = doc([E1], [E2], [E3])
        ds = devs(3)
        for st, want_tier, want in (({E1: "degraded", E2: "standby", E3: "ok"}, "t3", {E3}),
                                    ({E1: "standby", E2: "degraded", E3: "off"}, "t2", {E2}),
                                    ({E1: "off", E2: "standby", E3: "off"}, "t2", {E2})):
            dec = A.assign({"main": ds}, st, d, None, {}, {}, 0)
            self.assertEqual(dec["groups"]["main"]["active_tier"], want_tier, st)
            self.assertEqual(set(pins_of(dec).values()), want, st)
        dec = A.assign({"main": ds}, {E1: "off", E2: "off", E3: "unknown"}, d, None, {}, {}, 0)
        g = dec["groups"]["main"]
        self.assertEqual((g["active_tier"], g["last_step"], g["pins"]), (None, "block", {}))
        self.assertEqual(set(g["steps"].values()), {"last_block"})
        ev = [e for e in dec["events"] if e["kind"] == "group_last_step"]
        self.assertEqual((ev[0]["group"], ev[0]["step"], ev[0]["devices"]), ("main", "block", 3))
        dec2 = A.assign({"main": ds}, {E1: "ok", E2: "off", E3: "off"}, d, dec, {}, {}, 30)
        rs = [e for e in dec2["events"] if e["kind"] == "group_restored"]
        self.assertEqual((rs[0]["exit"], rs[0]["after_s"]), (E1, 30))

    def test_last_step_direct_by_group_or_route(self):
        st = {E1: "off"}
        ds = [{"id": "d-00000001"}, {"id": "d-00000002", "fail": "direct"}]
        dec = A.assign({"main": ds}, st, doc([E1]), None, {}, {}, 0)
        self.assertEqual(dec["groups"]["main"]["steps"], {"d-00000001": "last_block", "d-00000002": "last_direct"})
        dec = A.assign({"main": ds}, st, doc([E1], fail="direct"), None, {}, {}, 0)
        self.assertEqual(set(dec["groups"]["main"]["steps"].values()), {"last_direct"})
        self.assertEqual(dec["groups"]["main"]["last_step"], "direct")

    def test_groups_are_independent_and_a_new_route_moves_the_pin(self):
        d = doc([E1, E2])
        d["groups"].append(P.default_group("g-123456", "abroad", "compat", name="S", exits=[E3, E4]))
        st = dict((e, "ok") for e in (E1, E2, E3, E4))
        dv = {"id": "d-00000001"}
        d1 = A.assign({"main": [dv], "g-123456": []}, st, d, None, {}, {}, 0)
        self.assertIn(pins_of(d1)["d-00000001"], (E1, E2))
        d2 = A.assign({"main": [], "g-123456": [dv]}, st, d, d1, {}, {}, 10)  # the route changed to the group
        self.assertEqual(pins_of(d2), {})
        self.assertIn(pins_of(d2, "g-123456")["d-00000001"], (E3, E4))
        self.assertEqual(d2["groups"]["g-123456"]["pins"]["d-00000001"]["why"], "new")
        d3 = A.assign({"main": [], "g-123456": [dv]}, dict(st, **{E1: "off", E2: "off"}), d, d2, {}, {}, 20)
        self.assertEqual(d3["groups"]["main"]["last_step"], "block")
        self.assertIn(pins_of(d3, "g-123456")["d-00000001"], (E3, E4))   # another group is not touched

    def test_route_to_an_exit(self):
        d = doc([E1, E2, E3])
        dv = {"id": "d-00000001", "exit": E1}
        dec = A.assign({"main": [dv]}, {E1: "off", E2: "ok", E3: "ok"}, d, None, {}, {}, 0)
        self.assertIn(pins_of(dec)["d-00000001"], (E2, E3))   # E removed: another exit of its group
        dec = A.assign({"main": [dv]}, {E1: "standby", E2: "standby", E3: "off"}, d, None, {}, {}, 0)
        self.assertEqual(pins_of(dec)["d-00000001"], E1)      # E in reserve and no better one: E itself (П4)
        dec = A.assign({"main": [dv]}, {E1: "off", E2: "off", E3: "off"}, d, None, {}, {}, 0)
        self.assertEqual(dec["groups"]["main"]["steps"]["d-00000001"], "last_block")
        dec = A.assign({"main": [dict(dv, fail="direct")]}, {E1: "off", E2: "off", E3: "off"}, d, None, {}, {}, 0)
        self.assertEqual(dec["groups"]["main"]["steps"]["d-00000001"], "last_direct")

    def test_offline_keeps_everything(self):
        d = doc([E1, E2])
        ds = devs(10)
        d1 = A.assign({"main": ds}, {E1: "ok", E2: "ok"}, d, None, {}, {}, 0)
        d2 = A.assign({"main": ds}, {E1: "off", E2: "off"}, d, d1, {}, {"offline": True}, 10)
        self.assertEqual((pins_of(d2), d2["gw_all"], d2["moves"], d2["events"]), (pins_of(d1), d1["gw_all"], [], []))

    def test_failover_leave_at_a_tier_change(self):
        d = doc([E1], [E2])
        ds = devs(4)
        d1 = A.assign({"main": ds}, {E1: "ok", E2: "ok"}, d, None, {}, {}, 0)
        d2 = A.assign({"main": ds}, {E1: "off", E2: "ok"}, d, d1, {}, {}, 10)
        fo = [e for e in d2["events"] if e["kind"] == "exit_failover"]
        self.assertEqual((fo[0]["from_tier"], fo[0]["to_tier"], fo[0]["back"], fo[0]["moved"]), ("t1", "t2", False, 4))
        d3 = A.assign({"main": ds}, {E1: "ok", E2: "ok"}, d, d2, {}, {}, 20)
        self.assertEqual([e for e in d3["events"] if e["kind"] == "exit_failover"], [])   # only when they return

    def test_night_rebalance_inside_the_tier(self):
        d = doc([E1, E2], [E3], night_rebalance={"enabled": True, "at": "04:00"})
        ds = devs(30)
        d1 = A.assign({"main": ds}, {E1: "ok", E2: "off", E3: "ok"}, d, None, {}, {}, 0)
        d2 = A.assign({"main": ds}, {E1: "ok", E2: "ok", E3: "ok"}, d, d1, {}, {"local": ("2026-10-16", "03:59")}, 10)
        self.assertEqual(pins_of(d2), pins_of(d1))
        d3 = A.assign({"main": ds}, {E1: "ok", E2: "ok", E3: "ok"}, d, d2, {}, {"local": ("2026-10-16", "04:01")}, 20)
        home = dict((x["id"], A.hrw(x["id"], [E1, E2])) for x in ds)
        self.assertEqual(pins_of(d3), home)
        self.assertTrue(all(m["why"] == "rebalance" for m in d3["moves"]))
        self.assertTrue(all(m["to"] in (E1, E2) for m in d3["moves"]))
        d4 = A.assign({"main": ds}, {E1: "ok", E2: "ok", E3: "ok"}, d, d3, {}, {"local": ("2026-10-16", "04:05")}, 30)
        self.assertEqual(d4["moves"], [])                     # once a night


class MethodTest(unittest.TestCase):
    def test_hash_is_the_one_of_333(self):
        rnd = random.Random(7)
        ids = ["e-%010x" % rnd.getrandbits(40) for _ in range(5)]
        d = doc(ids)
        st = dict((e, "ok") for e in ids)
        ds = [{"id": "d-%08x" % rnd.getrandbits(32)} for _ in range(1000)]
        dec = A.assign({"main": ds}, st, d, None, {}, {}, 0)
        for x in ds:
            want = exits.hrw(x["id"], [{"id": e} for e in ids])["id"]
            self.assertEqual(pins_of(dec)[x["id"]], want)
            self.assertEqual(A.pick_new(x["id"], "main", st, d), want)

    def test_hrw_stability_when_an_exit_is_added(self):
        d = doc([E1, E2, E3])
        ds = devs(400)
        st = {E1: "ok", E2: "ok", E3: "ok"}
        d1 = A.assign({"main": ds}, st, d, None, {}, {}, 0)
        d2doc = doc([E1, E2, E3, E4])
        st2 = dict(st, **{E4: "ok"})
        d2 = A.assign({"main": ds}, st2, d2doc, d1, {}, {}, 10)
        self.assertEqual(d2["moves"], [])                     # no pinned one moves
        new = devs(400, "d-e")
        share = [A.pick_new(x["id"], "main", st2, d2doc) for x in new].count(E4) / 400.0
        self.assertTrue(0.18 <= share <= 0.32, share)          # ≈ 1/K

    def test_weights(self):
        d = doc([E1, E2])
        d["groups"][0]["tiers"][0].update(method="weights", weights={E1: 3})
        st = {E1: "ok", E2: "ok"}
        got = [A.pick_new(x["id"], "main", st, d) for x in devs(800)]
        self.assertTrue(0.68 <= got.count(E1) / 800.0 <= 0.82, got.count(E1))
        dec = A.assign({"main": devs(800)}, st, d, None, {}, {}, 0)
        self.assertEqual([pins_of(dec)[x["id"]] for x in devs(800)], got)

    def test_best_rtt(self):
        d = doc([E1, E2, E3])
        t = d["groups"][0]["tiers"][0]
        t.update(method="best_rtt", tolerance_ms=30, tolerance_pct=20)
        st = {E1: "ok", E2: "ok", E3: "ok"}
        m = {E1: {"kind": "xray", "rtt": 100}, E2: {"kind": "xray", "rtt": 125}, E3: {"kind": "xray", "rtt": 200}}
        got = set(A.pick_new(x["id"], "main", st, d, m) for x in devs(100))
        self.assertEqual(got, {E1, E2})                       # 125 ≤ 100 + max(30, 20 %): equal
        m[E2]["rtt"] = 140
        self.assertEqual(set(A.pick_new(x["id"], "main", st, d, m) for x in devs(100)), {E1})
        m = {E1: {"kind": "awg", "rtt": 30, "x_norm": 2.0}, E2: {"kind": "xray", "rtt": 300, "x_norm": 1.0},
             E3: {"kind": "xray", "rtt": 310, "x_norm": 1.1}}
        got = set(A.pick_new(x["id"], "main", st, d, m) for x in devs(100))
        self.assertEqual(got, {E2, E3})                       # both kinds in the tier: by x_norm

    def test_capacity(self):
        d = doc([E1, E2])
        d["groups"][0]["tiers"][0]["method"] = "capacity"
        st = {E1: "ok", E2: "ok"}
        m = {E1: {"speed_mbps": 90, "load": 0.0}, E2: {"speed_mbps": 30, "load": 0.0}}
        got = [A.pick_new(x["id"], "main", st, d, m) for x in devs(800)]
        self.assertTrue(0.68 <= got.count(E1) / 800.0 <= 0.82, got.count(E1))
        even = [A.pick_new(x["id"], "main", st, d, {}) for x in devs(800)]   # no data: weight 1
        self.assertEqual(even, [A.hrw(x["id"], [E1, E2]) for x in devs(800)])

    def test_per_connection_has_no_pins(self):
        d = doc([E1], [E2], per_connection=True)
        dec = A.assign({"main": devs(5)}, {E1: "off", E2: "ok"}, d, None, {}, {}, 0)
        self.assertEqual((dec["groups"]["main"]["pins"], dec["groups"]["main"]["active_tier"]), ({}, "t2"))


class GwAllTest(unittest.TestCase):
    def test_only_main_and_its_last_step(self):
        d = doc([E1, E2])
        g = P.default_group("g-123456", "abroad", "compat", name="Стриминг", exits=[E3, E4])
        d["groups"].append(g)
        m = {E1: {"kind": "xray", "rtt": 200}, E2: {"kind": "xray", "rtt": 50}, E3: {"kind": "xray", "rtt": 10}}
        st = {E1: "ok", E2: "ok", E3: "ok", E4: "ok"}
        dec = A.assign({}, st, d, None, m, {}, 0)
        self.assertEqual((dec["gw_all"], dec["dns_via"]), (exits.tag(E2), "main"))   # the least response of main
        st = {E1: "off", E2: "off", E3: "ok", E4: "ok"}
        dec = A.assign({"main": devs(1)}, st, d, None, dict(m, **{E3: {"kind": "xray", "rtt": 10, "quota": True}}), {},
                       0)                                     # a device on the last step: the event is told (З8)
        self.assertEqual((dec["gw_all"], dec["dns_via"]), (exits.tag(E4), "g-123456"))   # no exit with a quota
        ev = [e for e in dec["events"] if e["kind"] == "group_last_step" and e["group"] == "main"]
        self.assertEqual(ev[0]["dns_via"], "g-123456")
        d2 = copy.deepcopy(d)
        d2["groups"][0]["fail"] = "direct"
        dec = A.assign({}, st, d2, None, m, {}, 0)
        self.assertEqual((dec["gw_all"], dec["dns_via"]), ("direct", "direct"))
        dec = A.assign({}, {E1: "off", E2: "off", E3: "off", E4: "off"}, d, None, m, {}, 0)
        self.assertEqual((dec["gw_all"], dec["dns_via"]), ("block", "none"))
        d3 = copy.deepcopy(d)
        d3["groups"].append(P.default_group("g-abcdef", "ru", "ru_home", name="Дом", exits=[E5]))
        dec = A.assign({}, {E1: "off", E2: "off", E3: "off", E4: "off", E5: "ok"}, d3, None, m, {}, 0)
        self.assertEqual(dec["gw_all"], "block")             # a Russian group never carries gw-all
        self.assertTrue(A.gw_all(d, {E1: "ok"})[0].startswith(exits.ABROAD_PREFIX))



# ── REV-s1-t2: the review of T2 ────────────────────────────────────────────

def unknown(shown=None):
    return {"state": "unknown", "reasons": ["unknown"], "shown": shown}


class UnknownTest(unittest.TestCase):
    """С1 (§4.2): «unknown» — as its last state for what is there; new devices never get it."""

    def test_unknown_after_a_restart_is_no_last_step(self):
        d = doc([E1, E2])
        ds = devs(20)
        d1 = A.assign({"main": ds}, {E1: "ok", E2: "ok"}, d, None, {}, {}, 1000)
        st = {E1: unknown("ok"), E2: unknown("ok")}
        new = {"id": "d-ffffffff"}
        d2 = A.assign({"main": ds + [new]}, st, d, d1, {}, {}, 1010)
        g = d2["groups"]["main"]
        self.assertEqual(pins_of(d2), pins_of(d1))              # nobody moves, nobody is blocked
        self.assertEqual((g["last_step"], d2["moves"], d2["events"]), (None, [], []))
        self.assertEqual(g["steps"][new["id"]], "last_block")   # an «unknown» exit takes no new device
        self.assertNotIn(new["id"], g["pins"])
        self.assertEqual(d2["gw_all"], d1["gw_all"])            # gw-all is not blocked for that tick
        d3 = A.assign({"main": ds}, {E1: unknown(), E2: unknown()}, d, None, {}, {}, 1010)
        self.assertEqual(d3["groups"]["main"]["last_step"], "block")   # never known: nothing to stand on
        d4 = A.assign({"main": ds}, {E1: unknown("off"), E2: "ok"}, d, d1, {}, {}, 1010)
        self.assertNotIn(E1, pins_of(d4).values())               # «unknown» after «снят»: still «снят»

    def test_unknown_primary_tier_holds_the_return_and_keeps_the_pins(self):
        d = doc([E1], [E2])
        ds = devs(10)
        d1 = A.assign({"main": ds}, {E1: "off", E2: "ok"}, d, None, {}, {}, 0)
        self.assertEqual(set(pins_of(d1).values()), {E2})
        d1["groups"]["main"]["tier_good_since"]["t1"] = 0         # as if t1 had been good for long
        d2 = A.assign({"main": ds}, {E1: unknown("ok"), E2: "ok"}, d, d1, {}, {}, 10 ** 6)
        self.assertEqual(pins_of(d2), pins_of(d1))              # no return onto an «unknown» exit
        self.assertEqual(d2["moves"], [])
        self.assertEqual(d2["groups"]["main"]["pins"], d1["groups"]["main"]["pins"])   # not rewritten

    def test_unknown_primary_tier_no_return_move_inside_the_reserve(self):
        """REV-s1-t2-2 Н1: t1 «unknown» (shown «ok»), the device on t2 off its HRW home — no «return» inside t2."""
        d = doc([E1], [E2, E3])
        dev = {"id": "d-00000001"}
        home = pins_of(A.assign({"main": [dev]}, {E1: "off", E2: "ok", E3: "ok"}, d, None, {}, {}, 100))[dev["id"]]
        other = E3 if home == E2 else E2
        now = 10 ** 5
        pins = {"groups": {"main": {"pins": {dev["id"]: {"exit": other, "tier": "t2", "since": 1, "why": "evict"}},
                                    "active_tier": "t1", "tier_good_since": {"t1": now - 3600}, "last_step": None}}}
        st = {E1: unknown("ok"), E2: "ok", E3: "ok"}
        d2 = A.assign({"main": [dev]}, st, d, copy.deepcopy(pins), {}, {}, now)
        self.assertEqual(pins_of(d2), {dev["id"]: other})              # stays where it is
        self.assertEqual((d2["moves"], d2["events"]), ([], []))         # no false «back on the primary»
        st[E1] = "ok"
        d3 = A.assign({"main": [dev]}, st, d, d2, {}, {}, now + 5)
        self.assertEqual(d3["moves"], [{"device": dev["id"], "group": "main", "from": other, "to": E1,
                                        "why": "return"}])              # t1 known «ok» again: one move, onto t1


class ReviewAssignTest(unittest.TestCase):
    def test_route_to_e_after_a_pin_elsewhere_is_a_move(self):
        d = doc([E1, E2])
        pins = {"groups": {"main": {"pins": {"d-00000001": {"exit": E1, "tier": "t1", "since": 1, "why": "new"}}}}}
        dec = A.assign({"main": [{"id": "d-00000001", "exit": E2}]}, {E1: "ok", E2: "ok"}, d, pins, {}, {}, 100)
        self.assertEqual(pins_of(dec), {"d-00000001": E2})
        self.assertEqual(dec["moves"], [{"device": "d-00000001", "group": "main", "from": E1, "to": E2, "why": "new"}])
        dec = A.assign({"main": [{"id": "d-00000002", "exit": E2}]}, {E1: "ok", E2: "off"}, d, None, {}, {}, 100)
        self.assertEqual(dec["groups"]["main"]["pins"]["d-00000002"]["why"], "evict")   # §2.2: E «снят»

    def test_no_last_step_event_without_devices(self):
        d = doc([])
        dec = A.assign({}, {}, d, None, {}, {}, 100)
        self.assertEqual((dec["events"], dec["gw_all"]), ([], "block"))
        self.assertEqual(dec["groups"]["main"]["last_step"], "block")
        dec = A.assign({"main": devs(2)}, {}, d, dec, {}, {}, 110)          # devices came: told now
        ev = [e for e in dec["events"] if e["kind"] == "group_last_step"]
        self.assertEqual((len(ev), ev[0]["devices"]), (1, 2))
        dec = A.assign({"main": devs(2)}, {}, d, dec, {}, {}, 120)
        self.assertEqual([e for e in dec["events"] if e["kind"] == "group_last_step"], [])
        d["groups"][0]["tiers"][0]["exits"] = [E1]
        dec = A.assign({"main": devs(2)}, {E1: "ok"}, d, dec, {}, {}, 130)
        self.assertEqual([e["after_s"] for e in dec["events"] if e["kind"] == "group_restored"], [20])
        dec = A.assign({}, {E1: "off"}, d, dec, {}, {}, 140)
        dec = A.assign({}, {E1: "ok"}, d, dec, {}, {}, 150)
        self.assertEqual(dec["events"], [])                   # never told — no «restored» either

    def test_gw_all_skips_an_exit_with_a_quota(self):
        d = doc([E1])
        d["groups"].append(P.default_group("g-123456", "abroad", "compat", name="S", exits=[E3, E4]))
        m = {E3: {"kind": "xray", "rtt": 10, "quota": True}, E4: {"kind": "xray", "rtt": 100}}
        self.assertEqual(A.gw_all(d, {E1: "off", E3: "ok", E4: "ok"}, m)[1], E4)
        self.assertEqual(A.gw_all(d, {E1: "off", E3: "ok", E4: "off"}, m)[0], "block")

    def test_hrw_tie_takes_the_smaller_id(self):
        from unittest import mock
        with mock.patch.object(A, "_score", return_value=1.0):
            self.assertEqual(A.hrw("d-1", [E3, E1, E2]), E1)


# ── PLAN-order Ш1: the rows of «strength» and the manual order ─────────────────────────────────────────────────────

def _old_equal(cands, tier, metrics):
    """The «equal» set of ``_best_rtt`` of 3.3.7.13 (its formula, written out)."""
    kinds = set(A._kind(metrics, e) for e in cands)
    use = "rtt" if len(kinds) == 1 else "x_norm"
    vals = dict((e, (metrics.get(e) or {}).get(use)) for e in cands)
    vals = dict((e, v) for e, v in vals.items() if isinstance(v, (int, float)) and not isinstance(v, bool))
    if not vals:
        return set(cands)
    best = min(vals.values())
    tol_pct = best * float(tier.get("tolerance_pct", 20) or 0) / 100.0
    tol = max(float(tier.get("tolerance_ms", 30) or 0), tol_pct) if use == "rtt" else tol_pct
    return set(e for e, v in vals.items() if v <= best + tol)


def _rand_case(rnd, n=None):
    ids = ["e-%010x" % rnd.getrandbits(40) for _ in range(n or rnd.randint(1, 6))]
    st = dict((e, rnd.choice(("ok", "ok", "ok", "degraded", "standby", "off", "unknown"))) for e in ids)
    m = {}
    for e in ids:
        x = {"kind": rnd.choice(("awg", "xray"))}
        if rnd.random() < 0.85:
            x["rtt"] = rnd.choice((rnd.randint(5, 400), round(rnd.uniform(5, 400), 1)))
            x["x_norm"] = round(rnd.uniform(0.5, 4), 2)
        if rnd.random() < 0.6:
            x["speed_mbps"] = rnd.choice((0, rnd.randint(1, 900)))
            x["load"] = rnd.choice((None, round(rnd.random(), 2), 1.5))
        m[e] = x
    tier = {"method": rnd.choice(P.METHODS), "tolerance_ms": rnd.choice((0, 10, 30, 80)),
            "tolerance_pct": rnd.choice((0, 5, 20, 50)),
            "weights": dict((e, rnd.randint(1, 100)) for e in ids if rnd.random() < 0.5)}
    return ids, st, m, tier


def _doc_of(ids, tier, rnd, **kw):
    cut = rnd.randint(0, len(ids))
    tiers = [ids[:cut], ids[cut:]] if 0 < cut < len(ids) else [ids]
    d = doc(*tiers, **kw)
    for t in d["groups"][0]["tiers"]:
        t.update(copy.deepcopy(dict((k, v) for k, v in tier.items() if k != "exits")))
    return d


class RowsTest(unittest.TestCase):
    def test_rows_zero_is_the_old_equal_set_and_the_choice_is_in_it(self):
        rnd = random.Random(20261029)
        for case in range(500):
            ids, st, m, tier = _rand_case(rnd)
            tier = dict(tier, method="best_rtt", exits=ids)
            r = A.rows(tier, st, m)
            cands = [e for e in ids if A._state(st, e) == r["klass"]] if r["klass"] else []
            if not cands:
                self.assertEqual(r["rows"], [], case)
                continue
            first = set(x["id"] for x in r["rows"][0])
            self.assertEqual(first, _old_equal(cands, tier, m), case)
            for k in range(5):
                self.assertIn(A._choose("d-%d-%d" % (case, k), cands, tier, m), first, case)
            shown = [x["id"] for row in r["rows"] for x in row] + [o["id"] for o in r["out"]]
            self.assertEqual(sorted(shown), sorted(ids), case)           # every exit once: a row or «out»

    def test_owner_example_11_95_12_12(self):
        t = {"exits": [E1, E2, E3, E4], "method": "best_rtt", "tolerance_ms": 30, "tolerance_pct": 20}
        st = dict((e, "ok") for e in t["exits"])
        m = {E1: {"kind": "awg", "rtt": 11}, E2: {"kind": "awg", "rtt": 95}, E3: {"kind": "awg", "rtt": 12},
             E4: {"kind": "awg", "rtt": 12}}
        r = A.rows(t, st, m)
        self.assertEqual([[x["id"] for x in row] for row in r["rows"]], [[E1, E3, E4], [E2]])
        self.assertEqual((r["by"], r["queue"], r["order"], r["nodata"], r["out"]), ("rtt", True, "auto", False, []))
        self.assertEqual([x["v"] for x in r["rows"][0]], [11, 12, 12])
        self.assertEqual([round(x["share"], 3) for x in r["rows"][0]] + [r["rows"][1][0]["share"]], [0.333] * 3 + [0.0])
        got = set(A.pick_new(x["id"], "main", st, self._doc(t), m) for x in devs(300))
        self.assertEqual(got, {E1, E3, E4})                                 # 95 ms never gets a new device

    def _doc(self, t):
        d = doc(t["exits"])
        d["groups"][0]["tiers"][0].update(t)
        return d

    def test_nan_and_infinity_are_no_measure(self):
        """REV-order П-7: NaN и ∞ в замере — «нет замера» (assign._num), а не число: выход «ждёт замера», новых не
        получает, пока у других замер есть. 3.3.7.12 брал ∞ как число (выход с rtt = ∞ шёл раньше выхода без замера);
        источники rtt (collect, hostmon, exits) дают конечные числа — на деле разница не достижима, тест закрепляет
        поведение."""
        t = {"exits": [E1, E2, E3], "method": "best_rtt"}
        st = dict.fromkeys(t["exits"], "ok")
        m = {E1: {"kind": "awg", "rtt": float("inf")}, E2: {"kind": "awg", "rtt": 40.0}, E3: {"kind": "awg", "rtt": float("nan")}}
        r = A.rows(t, st, m)
        self.assertEqual([[x["id"] for x in row] for row in r["rows"]], [[E2]])
        self.assertEqual(sorted((o["id"], o["why"]) for o in r["out"]), sorted([(E1, "nometric"), (E3, "nometric")]))
        self.assertEqual(set(A.pick_new(x["id"], "main", st, self._doc(t), m) for x in devs(100)), {E2})
        m2 = {E1: {"kind": "awg", "rtt": float("inf")}, E2: {"kind": "awg"}}       # замеров нет ни у кого — поровну
        r = A.rows({"exits": [E1, E2], "method": "best_rtt"}, dict.fromkeys([E1, E2], "ok"), m2)
        self.assertEqual((r["nodata"], [[x["id"] for x in row] for row in r["rows"]]), (True, [[E1, E2]]))

    def test_shares_of_weights_capacity_even_and_what_is_out(self):
        t = {"exits": [E1, E2, E3], "method": "weights", "weights": {E1: 3}}
        r = A.rows(t, dict.fromkeys(t["exits"], "ok"), {})
        self.assertEqual([[x["id"] for x in row] for row in r["rows"]], [[E1], [E2, E3]])
        self.assertEqual([round(x["share"], 2) for row in r["rows"] for x in row], [0.6, 0.2, 0.2])
        self.assertFalse(r["queue"])
        cap = {"exits": [E1, E2, E3, E4], "method": "capacity", "tolerance_pct": 20}
        m = {E1: {"speed_mbps": 100}, E2: {"speed_mbps": 90}, E3: {"speed_mbps": 40}}
        r = A.rows(cap, dict.fromkeys(cap["exits"], "ok"), m)
        self.assertEqual([[x["id"] for x in row] for row in r["rows"]], [[E1, E2], [E3], [E4]])
        self.assertIsNone(r["rows"][2][0]["v"])                              # no speed test: the last row, weight 1
        self.assertAlmostEqual(sum(x["share"] for row in r["rows"] for x in row), 1.0)
        r = A.rows(cap, dict.fromkeys(cap["exits"], "ok"), {})
        self.assertEqual((r["nodata"], len(r["rows"]), len(r["rows"][0])), (True, 1, 4))
        st = {E1: "ok", E2: "degraded", E3: "unknown", E4: "off"}
        r = A.rows({"exits": [E1, E2, E3, E4], "method": "best_rtt"}, st, {E1: {"kind": "awg"}})
        self.assertEqual((r["by"], r["nodata"], r["klass"]), ("rtt", True, "ok"))
        self.assertEqual(r["out"], [{"id": E2, "why": "degraded"}, {"id": E3, "why": "unknown"}, {"id": E4, "why": "off"}])
        r = A.rows({"exits": [E1, E2], "method": "best_rtt"}, {E1: "ok", E2: "ok"}, {E1: {"kind": "awg", "rtt": 50}})
        self.assertEqual((r["rows"], r["out"]), ([[{"id": E1, "v": 50, "share": 1.0}]], [{"id": E2, "why": "nometric"}]))
        r = A.rows({"exits": [E1, E2], "order": "manual"}, {E1: "ok", E2: "ok"}, {}, per_conn=True)
        self.assertEqual((r["by"], r["order"], len(r["rows"])), ("per_connection", "auto", 1))
        self.assertEqual(A.rows({"exits": []}, {}, {})["rows"], [])

    def test_rows_always_end(self):
        """NaN, infinity and a negative tolerance (refused by validate, but stored data is not trusted): rows end."""
        t = {"exits": [E1, E2, E3], "method": "best_rtt", "tolerance_ms": -5, "tolerance_pct": -50}
        m = {E1: {"kind": "awg", "rtt": float("nan")}, E2: {"kind": "awg", "rtt": float("inf")},
             E3: {"kind": "awg", "rtt": 20}}
        r = A.rows(t, dict.fromkeys(t["exits"], "ok"), m)
        self.assertEqual([[x["id"] for x in row] for row in r["rows"]], [[E3]])
        t = {"exits": [E1, E2], "method": "capacity", "tolerance_pct": -50}
        r = A.rows(t, dict.fromkeys(t["exits"], "ok"), {E1: {"speed_mbps": 10}, E2: {"speed_mbps": 10}})
        self.assertEqual(len(r["rows"]), 2)


def _no_ranks(dec):
    """A decision without the rows of «strength» (Ш2: shown in live.json, no part of the choice of 3.3.7.13)."""
    dec = copy.deepcopy(dec)
    for g in dec["groups"].values():
        g.pop("ranks", None)
    return dec


class OldChoiceTest(unittest.TestCase):
    """Without the flag the choice is that of 3.3.7.13 (``assign.py`` is the one of 3.3.7.12, 8582b624)."""

    def setUp(self):
        from tests.gw import oldcode
        self.old = oldcode.load("assign")
        if self.old is None:
            self.skipTest("нет git или коммита 8582b624")

    def test_pick_new_assign_and_gw_all_as_before(self):
        rnd = random.Random(3313)
        for case in range(300):
            ids, st, m, tier = _rand_case(rnd)
            d = _doc_of(ids, tier, rnd, night_rebalance={"enabled": True, "at": "04:00"})
            ds = devs(12, "d-%03d-" % case)
            for x in ds:
                self.assertEqual(A.pick_new(x["id"], "main", st, d, m), self.old.pick_new(x["id"], "main", st, d, m),
                                 case)
            node = {"local": ("2026-10-29", "04:02")}
            st0 = dict((e, "ok") for e in ids)
            new0 = A.assign({"main": ds}, st0, d, None, m, {}, 0)
            old0 = self.old.assign({"main": ds}, st0, d, None, m, {}, 0)
            self.assertEqual(_no_ranks(new0), old0, case)
            self.assertEqual(_no_ranks(A.assign({"main": ds}, st, d, new0, m, node, 900)),
                             self.old.assign({"main": ds}, st, d, old0, m, node, 900), case)
            self.assertEqual(A.gw_all(d, st, m), self.old.gw_all(d, st, m), case)


class ManualOrderTest(unittest.TestCase):
    def _doc(self, *tiers, **kw):
        d = doc(*tiers, **kw)
        for t in d["groups"][0]["tiers"]:
            t.update(method="best_rtt", order="manual")
        return d

    M = {E1: {"kind": "awg", "rtt": 95}, E2: {"kind": "awg", "rtt": 11}, E3: {"kind": "awg", "rtt": 12}}

    def test_first_good_of_the_list_skips_degraded_and_unknown(self):
        d = self._doc([E1, E2, E3])
        st = {E1: "ok", E2: "ok", E3: "ok"}
        self.assertEqual(set(A.pick_new(x["id"], "main", st, d, self.M) for x in devs(50)), {E1})   # 95 ms: yours
        st[E1] = "degraded"
        self.assertEqual(set(A.pick_new(x["id"], "main", st, d, self.M) for x in devs(50)), {E2})
        st[E2] = "unknown"
        self.assertEqual(A.pick_new("d-1", "main", st, d, self.M), E3)
        st = {E1: "degraded", E2: "degraded", E3: "off"}
        self.assertEqual(A.pick_new("d-1", "main", st, d, self.M), E1)        # only degraded: the first of them
        r = A.rows(d["groups"][0]["tiers"][0], {E1: "ok", E2: "unknown", E3: "ok"}, self.M)
        self.assertEqual(([[(x["id"], x["v"], x["share"]) for x in row] for row in r["rows"]], r["order"], r["by"]),
                         ([[(E1, 1, 1.0)], [(E3, 2, 0.0)]], "manual", "manual"))

    def test_pins_stay_and_the_night_brings_them_home_to_the_first(self):
        d = doc([E1, E2, E3], night_rebalance={"enabled": True, "at": "04:00"})
        ds = devs(40)
        st = dict.fromkeys((E1, E2, E3), "ok")
        d1 = A.assign({"main": ds}, st, d, None, {}, {}, 0)                   # «even»: spread over three
        self.assertGreater(len(set(pins_of(d1).values())), 1)
        dm = copy.deepcopy(d)
        dm["groups"][0]["tiers"][0]["order"] = "manual"
        d2 = A.assign({"main": ds}, st, dm, d1, {}, {"local": ("2026-10-29", "03:00")}, 10)
        self.assertEqual((pins_of(d2), d2["moves"]), (pins_of(d1), []))     # the pinned ones never move by day
        new = devs(20, "d-n")
        d3 = A.assign({"main": ds + new}, st, dm, d2, {}, {}, 20)
        self.assertEqual(set(pins_of(d3)[x["id"]] for x in new), {E1})
        d4 = A.assign({"main": ds + new}, st, dm, d3, {}, {"local": ("2026-10-29", "04:01")}, 30)
        self.assertEqual(set(pins_of(d4).values()), {E1})                     # the night: home is the first
        self.assertTrue(d4["moves"] and all(mv["why"] == "rebalance" for mv in d4["moves"]))

    def test_per_connection_and_gw_all(self):
        d = self._doc([E1, E2], per_connection=True)
        self.assertEqual(A.spread_cands({E1: "ok", E2: "ok"}, d["groups"][0]), [E1, E2])   # the flag is not seen
        self.assertEqual(A.assign({"main": devs(3)}, {E1: "ok", E2: "ok"}, d, None, {}, {}, 0)["groups"]["main"]["pins"], {})
        d = self._doc([E1, E2, E3])
        self.assertEqual(A.gw_all(d, dict.fromkeys((E1, E2, E3), "ok"), self.M)[1], E1)   # the node too: the first
        d["groups"][0]["tiers"][0]["order"] = "auto"
        self.assertEqual(A.gw_all(d, dict.fromkeys((E1, E2, E3), "ok"), self.M)[1], E2)


if __name__ == "__main__":
    unittest.main()
