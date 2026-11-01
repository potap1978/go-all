"""The exit policy — kaskad.gw.policy (SPEC-stage1-exits §1.3, §3.1–3.3, §6, §11.1 «policy.py»; SPEC-exit-groups
§2.1–2.6; T2)."""

import copy
import unittest

from kaskad import names, state
from kaskad.errors import USAGE, KaskadError
from kaskad.gw import model, policy as P, rev
from kaskad.i18n import tr
from tests.helpers import RootCase

E1, E2, E3, R1 = "e-0000000001", "e-0000000002", "e-0000000003", "e-00000000a1"
POOL = [{"id": E1, "role": "abroad", "kind": "awg"}, {"id": E2, "role": "abroad", "kind": "xray"},
        {"id": E3, "role": "abroad", "kind": "xray"}, {"id": R1, "role": "ru", "kind": "awg"}]


def doc(preset="compat", exits=(E1, E2)):
    d = P.default_doc(preset)
    d["groups"][0]["tiers"][0]["exits"] = list(exits)
    return d


def codes(errs):
    return [e["code"] for e in errs]


def ru_group(gid="g-abcdef", exits=(R1,)):
    return P.default_group(gid, "ru", "ru_home", name="Дом", exits=list(exits))


class DefaultsTest(unittest.TestCase):
    def test_no_policy_reads_as_one_main_in_compat(self):
        for gen in (1, 2):                                   # a read never turns «Стандарт» on (§1.3)
            d = P.load({"gen": gen, "gateway": {}})
            self.assertEqual([g["id"] for g in d["groups"]], ["main"])
            self.assertEqual(d["groups"][0]["preset"], "compat")
            self.assertEqual(d["groups"][0]["name"], "")
            self.assertFalse(d["groups"][0]["flap_standby"])
            self.assertEqual(d["groups"][0]["rules"], [])
            self.assertEqual(d["groups"][0]["checks"]["freq"], {"mode": "often", "every_s": None})
        self.assertIsNone(P.stored({"gateway": {}}))

    def test_ensure_initial_by_generation(self):
        st = state.empty()
        self.assertTrue(P.ensure_initial(st))
        g = st["gateway"]["exit_policy"]["groups"][0]
        self.assertEqual((g["id"], g["preset"], g["name"], g["tiers"][0]["name"], g["tiers"][0]["id"]),
                         ("main", "standard", "", "", "t1"))
        self.assertTrue(g["flap_standby"])
        st1 = state.empty()
        st1["gen"] = 1
        self.assertTrue(P.ensure_initial(st1))
        self.assertEqual(st1["gateway"]["exit_policy"]["groups"][0]["preset"], "compat")
        self.assertFalse(st1["gateway"]["exit_policy"]["groups"][0]["flap_standby"])
        before = copy.deepcopy(st1)
        st1["gen"] = 2
        self.assertFalse(P.ensure_initial(st1))              # a policy there — untouched
        self.assertEqual(st1["gateway"]["exit_policy"], before["gateway"]["exit_policy"])
        self.assertEqual(len(st["gateway"]["exit_policy"]["groups"]), 1)      # no Russian group by default

    def test_flap_summary_by_generation_not_by_the_policy(self):
        new, old = state.empty(), state.empty()
        old["gen"] = 1
        self.assertTrue(model.notify(new)["flap_summary"])
        self.assertFalse(model.notify(old)["flap_summary"])
        P.ensure_initial(old)
        P.save(old, P.apply_preset(P.load(old), "standard") and P.load(old))
        self.assertFalse(model.notify(old)["flap_summary"])  # a write of the policy never changes it
        model.gw(old)["notify"] = {"flap_summary": True, "push": {"exit_dead": False}}
        n = model.notify(old)
        self.assertTrue(n["flap_summary"])
        self.assertFalse(n["push"]["exit_dead"])
        self.assertTrue(n["push"]["exit_alive"])
        self.assertFalse(n["push"]["speedtest_budget"])

    def test_probes_have_no_rule_of_death(self):
        p = P.default_probes()
        for k in ("die_after", "live_after", "observatory_s", "awg_rtt"):
            self.assertNotIn(k, p)
        d = doc()
        d["probes"]["die_after"] = 5                          # a field of an old draft: kept, never acts
        self.assertEqual(P.validate(d, POOL), ([], []))
        self.assertEqual(P.normalize(d)["probes"]["die_after"], 5)

    def test_unknown_fields_are_kept(self):
        d = doc()
        d["future"] = {"x": 1}
        d["groups"][0]["later"] = [1, 2]
        d["groups"][0]["tiers"][0]["extra"] = "y"
        n = P.normalize(d)
        self.assertEqual((n["future"], n["groups"][0]["later"], n["groups"][0]["tiers"][0]["extra"]),
                         ({"x": 1}, [1, 2], "y"))


class PresetTest(unittest.TestCase):
    def test_every_preset_passes_its_own_validate(self):
        for preset in ("compat", "economy", "standard", "strict"):
            d = doc(preset)
            self.assertEqual(P.validate(d, POOL), ([], []), preset)
            for r in d["groups"][0]["rules"]:
                self.assertIn("back", r, (preset, r["id"]))
                self.assertIn("minutes", r["when"]["hold"])
                self.assertIn("minutes", r["back"]["hold"])
                self.assertGreaterEqual(r["back"]["hold"]["minutes"], r["when"]["hold"]["minutes"])
        d = doc()
        d["groups"].append(ru_group())
        self.assertEqual(P.validate(d, POOL), ([], []))
        ru = d["groups"][1]
        self.assertEqual((ru["checks"]["freq"]["mode"], ru["fail"], ru["checks"]["geo"]["expect"],
                          ru["checks"]["targets"]), ("often", "direct", "RU", "ru"))   # T-q Н-lazy: «Часто»

    def test_the_table_of_presets(self):
        f = dict((p, P.preset_fields(p)) for p in ("economy", "standard", "strict", "compat"))
        self.assertEqual(f["economy"]["checks"]["freq"]["mode"], "normal")
        for p in ("standard", "strict", "compat"):
            self.assertEqual(f[p]["checks"]["freq"]["mode"], "often")
        self.assertEqual([p for p in f if f[p]["flap_standby"]], ["economy", "standard", "strict"])
        self.assertEqual(f["standard"]["rules"][0]["when"]["conds"],
                         [{"metric": "rtt", "op": ">", "value": 2, "unit": "x_norm"}])
        self.assertEqual(f["standard"]["rules"][0]["back"]["conds"][0]["value"], 1.5)
        self.assertEqual(len(f["strict"]["rules"]), 3)
        self.assertEqual(f["strict"]["rules"][2]["then"], "remove")
        self.assertEqual(f["strict"]["checks"]["services"][0]["every_min"], 30)
        self.assertEqual(f["strict"]["checks"]["geo"]["every_h"], 6)
        for p in f:
            self.assertFalse(f[p]["checks"]["speed"]["enabled"])
            self.assertEqual(f[p]["checks"]["geo"]["action"], "push")
            self.assertFalse(f[p]["night_rebalance"]["enabled"])

    def test_no_preset_changes_the_rule_of_death(self):
        for p in ("compat", "economy", "standard", "strict", "ru_home"):
            self.assertEqual(P.default_doc()["probes"], P.default_probes())
            f = P.preset_fields(p)
            self.assertFalse(set(f) & {"probes", "die_after", "live_after"})

    def test_a_change_of_a_preset_field_makes_custom(self):
        st = state.empty()
        P.ensure_initial(st)
        d = P.load(st)
        self.assertEqual(P.preset_of(d["groups"][0]), "standard")
        d["groups"][0]["checks"]["geo"]["enabled"] = False      # turning the country check off — «Свой»
        new, _ = P.save(st, d, POOL)
        self.assertEqual(new["groups"][0]["preset"], "custom")
        d = P.load(st)
        P.apply_preset(d["groups"][0], "standard")               # «Вернуть пресет»
        new, _ = P.save(st, d, POOL)
        self.assertEqual(new["groups"][0]["preset"], "standard")
        d["groups"][0]["tiers"][0]["exits"] = [E1]               # tiers and names are not the preset's
        d["hint_seen"] = True
        new, _ = P.save(st, d, POOL)
        self.assertEqual(new["groups"][0]["preset"], "standard")


class ValidateTest(unittest.TestCase):
    def assertErr(self, d, code, pool=POOL, prev=None):
        errs, _ = P.validate(d, pool, prev)
        self.assertIn(code, codes(errs), errs)
        self.assertTrue(all(e["text"] and e["text"] != e["code"] for e in errs))

    def test_exit_in_two_tiers_or_groups(self):
        d = doc()
        d["groups"][0]["tiers"].append(P.default_tier("t2", [E1]))
        self.assertErr(d, "ep.err.exit_twice")
        d = doc()
        g = P.default_group("g-123456", "abroad", "compat", name="Стриминг", exits=[E2])
        d["groups"].append(g)
        self.assertErr(d, "ep.err.exit_twice")

    def test_role_of_the_exit_and_of_the_group(self):
        d = doc(exits=(E1, R1))
        errs, _ = P.validate(d, POOL)
        e = [x for x in errs if x["code"] == "ep.err.exit_role"]
        self.assertEqual((len(e), e[0]["id"], e[0]["field"]), (1, R1, "groups[0].tiers[0].exits"))
        d = doc()
        d["groups"].append(ru_group(exits=(E3,)))
        self.assertErr(d, "ep.err.exit_role")
        # with no pool given the roles are unknown: no refusal by role
        self.assertNotIn("ep.err.exit_role", codes(P.validate(doc(exits=(E1, R1)))[0]))

    def test_groups_rules(self):
        d = doc()
        g = P.default_group("g-123456", "abroad", "compat", name="Стриминг")
        g["per_connection"] = True
        d["groups"].append(g)
        self.assertErr(d, "ep.err.per_connection_main")
        d = doc()
        d["groups"] = d["groups"][1:]
        self.assertErr(d, "ep.err.main")                         # main never goes
        d = doc()
        for i in range(8):
            d["groups"].append(P.default_group("g-00000%d" % i, "abroad", "compat", name="Г%d" % i))
        self.assertErr(d, "ep.err.groups_many")                  # the 9th group
        d = doc()
        d["groups"].append(ru_group())
        prev = copy.deepcopy(d)
        d["groups"][1]["role"] = "abroad"
        d["groups"][1]["preset"] = "compat"
        d["groups"][1]["checks"]["targets"] = "abroad"
        self.assertErr(d, "ep.err.role_change", pool=None, prev=prev)
        for v in ("g-abcdef", "g-gone00"):                       # a Russian group, a group gone
            d = doc()
            d["groups"].append(ru_group())
            d["tg_proxy_group"] = v
            self.assertErr(d, "ep.err.tg_proxy_group")
        d = doc()
        d["groups"].append(dict(P.default_group("g-123456", "abroad", "compat", name="X"), id="main"))
        self.assertErr(d, "ep.err.group_twice")
        d = doc()
        d["groups"][0]["tiers"].append(P.default_tier("t1"))
        self.assertErr(d, "ep.err.tier_twice")

    def test_weights_holds_and_frequency(self):
        d = doc()
        t = d["groups"][0]["tiers"][0]
        t["method"] = "weights"
        t["weights"] = {E1: 0}
        self.assertErr(d, "ep.err.weight")
        t["weights"] = {E1: 101}
        self.assertErr(d, "ep.err.weight")
        t["weights"] = {E1: 100}
        self.assertEqual(P.validate(d, POOL)[0], [])
        d = doc()
        d["groups"][0]["return_to_primary"]["hold_min"] = 121
        self.assertErr(d, "ep.err.hold_min")
        for mode in P.FREQ_MODES:
            d = doc()
            d["groups"][0]["checks"]["freq"] = {"mode": mode, "every_s": 60 if mode == "custom" else None}
            self.assertEqual(P.validate(d, POOL)[0], [], mode)
        for every in (9, 601, None):
            d = doc()
            d["groups"][0]["checks"]["freq"] = {"mode": "custom", "every_s": every}
            self.assertErr(d, "ep.err.freq_every")
        d = doc()
        d["groups"][0]["checks"]["freq"] = {"mode": "often", "every_s": 30}
        self.assertErr(d, "ep.err.freq_every_custom")
        d["groups"][0]["checks"]["freq"] = {"mode": "sometimes", "every_s": None}
        self.assertErr(d, "ep.err.freq_mode")

    def test_speed_test_of_the_group(self):
        for every in P.SPEED_EVERY_H:
            d = doc()
            d["groups"][0]["checks"]["speed"]["every_h"] = every
            self.assertEqual(P.validate(d, POOL)[0], [])
        for every in (2, 48, "6", True):
            d = doc()
            d["groups"][0]["checks"]["speed"]["every_h"] = every
            self.assertErr(d, "ep.err.speed_every")
        for url in ("http://example.com/100mb", "ftp://example.com", "https://", "https://exa mple.com/x"):
            d = doc()
            d["groups"][0]["checks"]["speed"]["url"] = url
            self.assertErr(d, "ep.err.speed_url")
        d = doc()
        d["groups"][0]["checks"]["speed"]["url"] = "https://example.com/100mb.bin"
        self.assertEqual(P.validate(d, POOL)[0], [])

    def test_geo_action_is_push_only(self):
        for act in ("remove", "standby", "off"):
            d = doc()
            d["groups"][0]["checks"]["geo"]["action"] = act
            self.assertErr(d, "ep.err.geo_action")

    def test_rules(self):
        d = doc("standard")
        r = d["groups"][0]["rules"][0]
        r["back"]["conds"][0]["value"] = 3                     # back on the bad side of the threshold (> 2)
        self.assertErr(d, "ep.err.back_side")
        d = doc("standard")
        d["groups"][0]["rules"][0]["back"]["hold"] = {"minutes": 4}    # return shorter than the leave (5)
        self.assertErr(d, "ep.err.back_hold")
        d = doc("strict")
        d["groups"][0]["rules"][2]["back"] = {"match": "all", "conds": [{"metric": "loss", "op": "<", "value": 10,
                                                                           "unit": "pct"}], "manual": False}
        self.assertErr(d, "ep.err.remove_back")                 # «снят» without an explicit hold of return
        d["groups"][0]["rules"][2]["back"] = {"manual": True}
        self.assertNotIn("ep.err.remove_back", codes(P.validate(d, POOL)[0]))
        d = doc("standard")
        d["groups"][0]["rules"][0]["then"] = "off"               # the old word: refused with the hint
        errs, _ = P.validate(d, POOL)
        self.assertIn("ep.err.then_off", codes(errs))
        self.assertIn("remove", [e for e in errs if e["code"] == "ep.err.then_off"][0]["text"])
        d = doc("standard")
        d["groups"][0]["rules"] = [dict(copy.deepcopy(d["groups"][0]["rules"][0]), id="r-%d" % i) for i in range(21)]
        self.assertErr(d, "ep.err.rules_many")
        d = doc("standard")
        d["groups"][0]["rules"][0]["when"]["conds"] *= 6
        self.assertErr(d, "ep.err.conds")
        d = doc("standard")
        del d["groups"][0]["rules"][0]["back"]
        self.assertErr(d, "ep.err.back_missing")

    def test_loss_without_min_fails_gets_two(self):
        d = doc()
        d["groups"][0]["rules"] = [P._rule("r-l", [{"metric": "loss", "op": ">", "value": 10, "unit": "pct"}],
                                           "degraded", [{"metric": "loss", "op": "<", "value": 5, "unit": "pct"}], 2, 5)]
        self.assertEqual(P.validate(d, POOL)[0], [])
        self.assertEqual(P.normalize(d)["groups"][0]["rules"][0]["when"]["conds"][0]["min_fails"], 2)
        d["groups"][0]["rules"][0]["when"]["conds"][0]["min_fails"] = 11
        self.assertErr(d, "ep.err.min_fails")

    def test_holds_compared_by_p_nom(self):
        d = doc(exits=(E1,))                                   # one AWG exit at 10 s: P_nom = max(3P, 30) = 30
        r = P._rule("r-x", [{"metric": "rtt", "op": ">", "value": 300, "unit": "ms"}], "degraded",
                    [{"metric": "rtt", "op": "<", "value": 200, "unit": "ms"}], 1, 1)
        r["when"]["hold"] = {"samples": 3}                     # 90 s
        r["back"]["hold"] = {"minutes": 1}                     # 60 s < 90 s
        d["groups"][0]["rules"] = [r]
        self.assertErr(d, "ep.err.back_hold")
        r["back"]["hold"] = {"minutes": 1.5}                   # 90 s — the same
        self.assertEqual(P.validate(d, POOL)[0], [])
        self.assertEqual(P.p_nom(d, d["groups"][0], r, POOL), 30)
        d2 = doc(exits=(E2,))                                  # xray: the observatory's 10 s
        self.assertEqual(P.p_nom(d2, d2["groups"][0], r, POOL), 10)
        self.assertEqual(P.hold_s({"samples": 4}, 30), 120)
        self.assertEqual(P.hold_s({"minutes": 2}, 30), 120)

    def test_warnings(self):
        d = doc(exits=(E1, E2))                                # AWG and xray in one tier
        d["groups"][0]["rules"] = [P._rule("r-x", [{"metric": "rtt", "op": ">", "value": 300, "unit": "ms"}],
                                           "degraded", [{"metric": "rtt", "op": "<", "value": 200, "unit": "ms"}], 2, 2)]
        errs, warns = P.validate(d, POOL)
        self.assertEqual(errs, [])
        self.assertIn("ep.warn.rtt_mixed", codes(warns))
        d["groups"][0]["rules"][0]["when"]["conds"] = [{"metric": "speed", "op": "<", "value": 5, "unit": "mbps"}]
        d["groups"][0]["rules"][0]["back"]["conds"] = [{"metric": "speed", "op": ">", "value": 10, "unit": "mbps"}]
        self.assertIn("ep.warn.speed_off", codes(P.validate(d, POOL)[1]))

    def test_names(self):
        d = doc()
        d["groups"][0]["tiers"][0]["name"] = "Ве​на"     # an invisible character: the names rule refuses it
        errs, _ = P.validate(d, POOL)
        self.assertEqual(codes(errs), ["ep.err.name"])
        self.assertEqual(errs[0]["reason"], "char")
        d["groups"][0]["tiers"][0]["name"] = "x" * 41
        self.assertEqual(P.validate(d, POOL)[0][0]["reason"], "long")
        d = doc()
        d["groups"].append(P.default_group("g-123456", "abroad", "compat", name=""))
        errs, _ = P.validate(d, POOL)
        self.assertEqual((codes(errs), errs[0]["reason"]), (["ep.err.name"], "empty"))   # only main may be empty

    def test_tier_and_group_duplicates_are_warnings(self):
        d = doc()
        d["groups"][0]["tiers"].append(P.default_tier("t2"))
        d["groups"][0]["tiers"][1]["name"] = "Level 1"         # the shown name of the first tier
        errs, warns = P.validate(d, POOL, lang="en")
        self.assertEqual(errs, [])
        self.assertEqual(codes(warns), ["ep.warn.tier_dup"])
        d = doc()
        d["groups"].append(P.default_group("g-123456", "abroad", "compat", name="main"))
        errs, warns = P.validate(d, POOL)
        self.assertEqual(errs, [])
        self.assertIn("ep.warn.group_dup", codes(warns))
        self.assertEqual(P.group_duplicates(d, "ОСНОВНАЯ"), ["main"])
        self.assertEqual(P.group_duplicates(d, "Main", "g-123456"), ["main"])

    def test_validate_exit(self):
        ok = {"enabled": True, "manual": "standby", "manual_why": "sub_confirm", "speed_skip": True,
              "country": {"expected": "DE", "accepted_at": 1, "notified": ["FI"]},
              "quota": {"period": "month", "limit_gb": 500, "reset_day": 1, "warn_pct": 80, "action": "remove"}}
        self.assertEqual(P.validate_exit(ok), [])
        for bad, code in (({"manual": "off"}, "ep.err.manual"), ({"manual_why": "sub_confirm"}, "ep.err.manual_why"),
                          ({"quota": {"limit_gb": 0}}, "ep.err.quota_limit"),
                          ({"quota": {"limit_gb": 1.25}}, "ep.err.quota_limit"),
                          ({"quota": {"limit_gb": 1, "reset_day": 29}}, "ep.err.quota_reset_day"),
                          ({"quota": {"limit_gb": 1, "warn_pct": 100}}, "ep.err.quota_warn"),
                          ({"quota": {"limit_gb": 1, "action": "off"}}, "ep.err.then_off"),
                          ({"quota": {"limit_gb": 1, "period": "week"}}, "ep.err.quota_period"),
                          ({"country": {"expected": "Germany"}}, "ep.err.country")):
            self.assertIn(code, codes(P.validate_exit(bad)), bad)


class GroupsTest(unittest.TestCase):
    def test_group_and_group_of(self):
        d = doc()
        d["groups"].append(ru_group())
        self.assertEqual(P.group(d, "g-abcdef")["role"], "ru")
        self.assertIsNone(P.group(d, "g-000000"))
        self.assertEqual(P.group_of(d, E2)["id"], "main")
        self.assertEqual(P.group_of(d, R1)["id"], "g-abcdef")
        self.assertIsNone(P.group_of(d, E3))
        self.assertEqual(P.tier_of(d, E1), (d["groups"][0], 0))

    def test_place_new_is_deterministic(self):
        d = doc(exits=())
        d["groups"][0]["tiers"].append(P.default_tier("t2"))
        d["groups"].append(P.default_group("g-123456", "abroad", "compat", name="Стриминг"))
        d["groups"].append(ru_group(exits=()))
        d["new_exits_group"] = "g-123456"
        hints = {E1: "main", E2: ["g-gone00", "main"], R1: "g-abcdef"}
        a = copy.deepcopy(d)
        got = P.place_new(a, POOL, hints)
        self.assertEqual(got, [(E1, "main", "t2"), (E2, "main", "t2"), (E3, "g-123456", "t1"), (R1, "g-abcdef", "t1")])
        b = copy.deepcopy(d)
        self.assertEqual(P.place_new(b, POOL, hints), got)      # the same in every process
        self.assertEqual(P.place_new(a, POOL, hints), [])       # placed: nothing more
        c = copy.deepcopy(d)
        c["groups"][0]["new_exits_tier"] = "first"
        P.place_new(c, POOL, {E1: "g-abcdef"})                  # a Russian hint never takes a foreign exit
        self.assertEqual(c["groups"][1]["tiers"][0]["exits"], [E1, E2, E3])

    def test_russian_exit_without_a_group_stays_out(self):
        d = doc(exits=())
        self.assertEqual(P.place_new(d, {"abroad": [E1], "ru": [R1]}), [(E1, "main", "t1")])
        self.assertIsNone(P.group_of(d, R1))

    def test_load_places_the_pool(self):
        st = {"gateway": {"exits": {E2: {"group": "g-123456"}}}}
        d = P.load(st, POOL)
        self.assertEqual(d["groups"][0]["tiers"][0]["exits"], [E1, E2, E3])
        st["gateway"]["exit_policy"] = doc(exits=())
        st["gateway"]["exit_policy"]["groups"].append(P.default_group("g-123456", "abroad", "compat", name="S"))
        d = P.load(st, POOL)
        self.assertEqual(P.group_of(d, E2)["id"], "g-123456")
        self.assertIsNone(P.stored({"gateway": {}}))

    def test_tg_proxy_group_falls_back_to_main_on_read(self):
        st = {"gateway": {"exit_policy": dict(doc(), tg_proxy_group="g-gone00")}}
        self.assertEqual(P.load(st)["tg_proxy_group"], "main")


class PeriodTest(unittest.TestCase):
    def test_observatory_is_the_most_frequent_foreign_group(self):
        d = doc(exits=(E1,))
        self.assertEqual(P.observatory_s(d), 10)
        d["groups"][0]["checks"]["freq"] = {"mode": "normal", "every_s": None}
        g = P.default_group("g-123456", "abroad", "compat", name="S", exits=[E2])
        g["checks"]["freq"] = {"mode": "rare", "every_s": None}
        d["groups"].append(g)
        self.assertEqual(P.observatory_s(d), 30)
        d["groups"][0]["checks"]["freq"] = {"mode": "lazy", "every_s": None}
        self.assertEqual(P.observatory_s(d), 120)                # «Лениво» counts as 2 min
        d["groups"].append(ru_group())
        d["groups"][-1]["checks"]["freq"] = {"mode": "custom", "every_s": 10}
        self.assertEqual(P.observatory_s(d), 120)                # Russian groups never count
        self.assertIsNone(P.observatory_s(doc(exits=())))       # no foreign exits — no observatory
        self.assertIsNone(P.observatory_s(doc(exits=(E1,)), {"abroad": [], "ru": []}))

    def test_period_of_an_exit(self):
        d = doc(exits=(E1, E2))
        g = d["groups"][0]
        g["checks"]["freq"] = {"mode": "custom", "every_s": 60}
        self.assertEqual(P.period(d, g, "awg"), 60)
        g2 = P.default_group("g-123456", "abroad", "compat", name="S", exits=[E3])
        d["groups"].append(g2)
        self.assertEqual(P.period(d, g, "xray"), 10)             # xray — the observatory of the most frequent group
        g2["checks"]["freq"] = {"mode": "lazy", "every_s": None}
        self.assertEqual(P.period(d, g2, "awg"), 15)
        self.assertEqual(P.awg_rtt_period(10), 30)
        self.assertEqual(P.awg_rtt_period(10, traffic=False), 120)


class NamesTest(unittest.TestCase):
    def test_labels(self):
        g = P.default_group()
        for lang, main, lvl in (("ru", "Основная", "Уровень 2"), ("en", "Main", "Level 2")):
            self.assertEqual(P.label(g, lang), main)
            self.assertEqual(tr("ep.name.main", lang), main)
            g["tiers"].append(P.default_tier("t2")) if len(g["tiers"]) < 2 else None
            self.assertEqual(P.tier_label(g, 1, lang), lvl)
        g["name"] = "Дом"
        g["tiers"][1]["name"] = "Дача"
        self.assertEqual((P.label(g, "en"), P.tier_label(g, 1, "en")), ("Дом", "Дача"))

    def test_default_names_are_stored_empty(self):
        st = state.empty()
        d = doc()
        d["groups"][0]["name"] = "main"
        d["groups"][0]["tiers"][0]["name"] = "уровень 1"
        d["groups"][0]["tiers"].append(dict(P.default_tier("t2"), name="Level 1"))   # another tier's default: text
        d["groups"].append(P.default_group("g-123456", "abroad", "compat", name="Основная"))
        new, warns = P.save(st, d, POOL)
        self.assertEqual(new["groups"][0]["name"], "")
        self.assertEqual(new["groups"][0]["tiers"][0]["name"], "")
        self.assertEqual(new["groups"][0]["tiers"][1]["name"], "Level 1")
        self.assertEqual(new["groups"][1]["name"], "Основная")   # another group: an ordinary text, a duplicate
        self.assertIn("ep.warn.group_dup", codes(warns))
        self.assertIn("ep.warn.tier_dup", codes(warns))

    def test_validate_calls_names_check(self):
        calls = []
        real = names.check

        def spy(value, what="name", lang=None):
            calls.append(what)
            return real(value, what, lang)
        d = doc()
        d["groups"][0]["tiers"][0]["name"] = "Быстрые"
        d["groups"].append(P.default_group("g-123456", "abroad", "compat", name="Стриминг"))
        with unittest.mock.patch.object(names, "check", spy):
            P.validate(d, POOL)
        self.assertEqual(sorted(calls), ["group", "level"])


class SaveRevTest(RootCase):
    """A write through policy.save and model.set_exit (quota, manual, enabled) gives rev.current + 1 (§11.1, З-3)."""

    def setUp(self):
        super().setUp()
        st = state.empty()
        st["gateway"] = {"exits": {}}
        state.save(st)
        self.r0 = rev.current(state.load())[0]

    def test_policy_save(self):
        with state.transaction() as st:
            P.save(st, doc("standard"), POOL)
        self.assertEqual(rev.current(state.load())[0], self.r0 + 1)
        with state.transaction() as st:
            P.save(st, P.load(st), POOL)                        # no change: no + 1
        self.assertEqual(rev.current(state.load())[0], self.r0 + 1)
        with state.transaction() as st:
            d = P.load(st)
            d["groups"][0]["name"] = "Главная"                  # a name is outside the fingerprint
            d["hint_seen"] = True
            P.save(st, d, POOL)
        self.assertEqual(rev.current(state.load())[0], self.r0 + 1)

    def test_set_exit(self):
        for kw in ({"quota": {"limit_gb": 100}}, {"manual": "standby"}, {"enabled": False}, {"manual": None},
                   {"quota": None}):
            before = rev.current(state.load())[0]
            with state.transaction() as st:
                model.set_exit(st, E1, **kw)
            self.assertEqual(rev.current(state.load())[0], before + 1, kw)
        before = rev.current(state.load())[0]
        with state.transaction() as st:
            model.set_exit(st, E1, name="Вена", group="main", country={"expected": "DE", "notified": ["FI"]})
        self.assertEqual(rev.current(state.load())[0], before)  # name, hint, country — outside

    def test_save_refuses_with_codes(self):
        d = doc()
        d["groups"][0]["per_connection"] = "yes"
        with self.assertRaises(KaskadError) as cm:
            P.save(state.load(), d, POOL)
        self.assertEqual(cm.exception.code, USAGE)
        self.assertEqual(codes(cm.exception.data["errors"]), ["ep.err.type"])



# ── REV-s1-t2: the review of T2 ────────────────────────────────────────────

class ReviewPolicyTest(unittest.TestCase):
    def test_load_places_a_key_of_a_subscription_into_its_group(self):
        st = {"gateway": {"exit_policy": doc(exits=()), "exits": {E2: {"group": "main"}}}}
        st["gateway"]["exit_policy"]["groups"].append(P.default_group("g-123456", "abroad", "compat", name="S"))
        subs = {"subs": [{"id": "s-1a2b3c", "group": "g-123456", "keys": [E2, E3]},
                         {"id": "s-4d5e6f", "keys": [E1]}]}
        d = P.load(st, POOL, subs)
        self.assertEqual(P.group_of(d, E3)["id"], "g-123456")    # the group of its subscription (SG §2.4)
        self.assertEqual(P.group_of(d, E2)["id"], "main")        # the hint of the key first
        self.assertEqual(P.group_of(d, E1)["id"], "main")        # a subscription without a group: new_exits_group
        self.assertEqual(P.group_of(P.load(st, POOL, subs["subs"]), E3)["id"], "g-123456")   # its list will do
        self.assertEqual(P.group_of(P.load(st, POOL), E3)["id"], "main")
        self.assertEqual(P.hints_of(st, subs), {E2: ["main", "g-123456"], E3: ["g-123456"]})
        self.assertEqual(P.group_of(model.exit_policy(st, POOL, subs), E3)["id"], "g-123456")

    def test_at_most_five_conditions_in_a_group(self):
        def rule(i, n):
            c = [{"metric": "rtt", "op": ">", "value": 300 + j, "unit": "ms"} for j in range(n)]
            return {"id": "r-%d" % i, "name": "", "enabled": True, "exits": "all", "then": "degraded", "push": True,
                    "when": {"match": "any", "conds": c, "hold": {"samples": 1}},
                    "back": {"match": "all", "conds": [{"metric": "rtt", "op": "<", "value": 200, "unit": "ms"}],
                             "hold": {"samples": 2}, "manual": False}}
        d = doc()
        d["groups"][0]["rules"] = [rule(0, 3), rule(1, 2)]
        errs, _ = P.validate(d, POOL)
        self.assertNotIn("ep.err.conds_group", codes(errs))
        d["groups"][0]["rules"].append(rule(2, 1))
        errs, _ = P.validate(d, POOL)
        self.assertIn("ep.err.conds_group", codes(errs))
        e = [x for x in errs if x["code"] == "ep.err.conds_group"][0]
        self.assertIn("6", e["text"])
        errs, _ = P.validate(doc("strict"), POOL)                # «Строго»: 3 conditions — within
        self.assertEqual(errs, [])
        self.assertIn("правил", tr("ep.err.rules_many", "ru", max=20))


if __name__ == "__main__":
    unittest.main()


class RuHomeTiersTest(unittest.TestCase):
    """T15 Q3 (SPEC-groups §6.6 «Дом»: «уровни по порядку — каждый ключ свой уровень»): a new key of a Russian group
    gets a tier of its own after the last one («Роутер → Дача → с сервера напрямую»), not a share of the router's."""

    def doc(self):
        d = P.default_doc("standard")
        d["groups"].append(P.default_group("g-0a0b0c", "ru", "ru_home", name="Дом"))
        return d

    def test_each_key_its_own_tier(self):
        d = self.doc()
        got = P.place_new(d, [{"id": "e-00000000b1", "role": "ru"}, {"id": "e-00000000b2", "role": "ru"},
                              {"id": "e-00000000b3", "role": "ru"}, {"id": "e-00000000a1", "role": "abroad"}])
        self.assertEqual(got, [("e-00000000b1", "g-0a0b0c", "t1"), ("e-00000000b2", "g-0a0b0c", "t2"),
                               ("e-00000000b3", "g-0a0b0c", "t3"), ("e-00000000a1", "main", "t1")])
        home = P.group(d, "g-0a0b0c")
        self.assertEqual([t["exits"] for t in home["tiers"]], [["e-00000000b1"], ["e-00000000b2"], ["e-00000000b3"]])
        self.assertEqual([t["name"] for t in home["tiers"]], ["", "", ""])         # «Уровень N» by the language
        pool = {"abroad": ["e-00000000a1"], "ru": ["e-00000000b1", "e-00000000b2", "e-00000000b3"]}
        self.assertEqual(P.validate(d, pool)[0], [])
        self.assertEqual(P.group(d, "main")["tiers"][0]["exits"], ["e-00000000a1"])   # a foreign group as before

    def test_the_owner_choice_and_the_limit(self):
        d = self.doc()
        home = P.group(d, "g-0a0b0c")
        home["new_exits_tier"] = "t1"
        P.place_new(d, [{"id": "e-00000000b1", "role": "ru"}, {"id": "e-00000000b2", "role": "ru"}])
        self.assertEqual([t["exits"] for t in home["tiers"]], [["e-00000000b1", "e-00000000b2"]])   # his tier
        home["new_exits_tier"] = "first"
        home["tiers"].append(P.default_tier("t2", ["e-00000000b3"]))
        P.place_new(d, [{"id": "e-00000000b4", "role": "ru"}])
        self.assertEqual(home["tiers"][0]["exits"][-1], "e-00000000b4")
        home["new_exits_tier"] = "last"
        home["tiers"] = [P.default_tier("t%d" % i, ["e-000000%04d" % i]) for i in range(1, P.MAX_TIERS + 1)]
        P.place_new(d, [{"id": "e-00000000b9", "role": "ru"}])
        self.assertEqual(len(home["tiers"]), P.MAX_TIERS)                          # full: the last one
        self.assertEqual(home["tiers"][-1]["exits"][-1], "e-00000000b9")

    def test_free_tier_id_and_an_empty_last_tier(self):
        d = self.doc()
        home = P.group(d, "g-0a0b0c")
        home["tiers"] = [P.default_tier("t2", ["e-00000000b1"]), P.default_tier("t3", ["e-00000000b2"])]
        self.assertEqual(P.new_tier(home)["id"], "t4")
        home["tiers"].append(P.default_tier("t9"))
        self.assertEqual(P.new_tier(home)["id"], "t9")                             # the empty last one is taken


class ManualOrderPolicyTest(unittest.TestCase):
    """PLAN-order Ш1, §3.4: the flag ``order`` of a tier — checked, «auto» not stored, read by 3.3.7.12–13 safely."""

    def _manual(self, order="manual"):
        d = doc("compat", (E1, E2, E3))
        d["groups"][0]["tiers"][0].update(method="best_rtt", order=order)
        return d

    def test_checked_and_auto_is_no_field(self):
        self.assertNotIn("order", P.default_tier("t1"))
        self.assertEqual(P.validate(self._manual(), POOL), ([], []))
        self.assertEqual(P.validate(self._manual("auto"), POOL)[0], [])
        errs = P.validate(self._manual("by_hand"), POOL)[0]
        self.assertEqual([(e["code"], e["field"]) for e in errs], [("ep.err.order", "groups[0].tiers[0].order")])
        for lang in ("ru", "en"):
            self.assertIn("by_hand", tr("ep.err.order", lang, value="'by_hand'"))
        st = {}
        new, _ = P.save(st, self._manual(), POOL)
        self.assertEqual(new["groups"][0]["tiers"][0]["order"], "manual")
        new, _ = P.save(st, self._manual("auto"), POOL)
        self.assertNotIn("order", new["groups"][0]["tiers"][0])
        self.assertEqual(new["groups"][0]["preset"], "compat")              # the order is no field of a preset

    def test_the_old_release_reads_the_new_flag(self):
        from tests.gw import oldcode
        from kaskad.gw import assign as A
        old_p, old_a = oldcode.load("policy"), oldcode.load("assign")
        if old_p is None or old_a is None:
            self.skipTest("нет git или коммита 8582b624")
        d = self._manual()
        self.assertEqual(old_p.normalize(d)["groups"][0]["tiers"][0]["order"], "manual")   # kept, not cleaned
        self.assertEqual(old_p.validate(d, POOL), ([], []))
        st = {}
        saved, _ = old_p.save(st, d, POOL)                                  # a save from the old panel keeps it
        self.assertEqual(saved["groups"][0]["tiers"][0]["order"], "manual")
        states = dict.fromkeys((E1, E2, E3), "ok")
        m = {E1: {"kind": "awg", "rtt": 95}, E2: {"kind": "awg", "rtt": 11}, E3: {"kind": "awg", "rtt": 12}}
        auto = copy.deepcopy(d)
        del auto["groups"][0]["tiers"][0]["order"]
        for i in range(200):
            dev = "d-%08x" % i
            self.assertEqual(old_a.pick_new(dev, "main", states, d, m), A.pick_new(dev, "main", states, auto, m))
            self.assertEqual(A.pick_new(dev, "main", states, d, m), E1)     # the new one: the first of the list
        self.assertNotEqual(set(old_a.pick_new("d-%d" % i, "main", states, d, m) for i in range(100)), {E1})
