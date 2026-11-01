"""T15 Q2 (SPEC-groups §2.8, §13.1 «exits.ru_target»): where the Russian traffic of a device goes — ONE answer for the
kernel, xray, the trace and the scheme. Every row of the table of §2.8: «Россия напрямую» not acting (no consent, another
profile, «block» by choice, a pause) — None; ``ru_via`` direct — 0x201 «direct»; a Russian group with a fit exit — its
mark and «gr-<id>» (the pin first); none fit — the last step of the group (0x201 «direct» or 0x2ff «fail-block»); the
fallback block of the FOREIGN group changes nothing. Plus the Russian group in ``devices_by_group`` of the collector and
the fingerprint of the decision (§2.7, §4.2)."""

import copy
from unittest import mock

from kaskad import state
from kaskad.gw import collect, exits, model
from kaskad.gw import policy as P
from tests.fakes_gw import awg_line, write_ru_pool
from tests.test_rules_gw import RulesGwCase

HOME = "g-0a0b0c"
DACHA_VLESS = ("vless://aaaaaaaa-1111-2222-3333-444444444444@203.0.113.32:443?type=tcp&security=reality"
               "&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome#Dacha")
D1, D3 = "d-00000001", "d-00000003"          # «Мама»: Телефон 10.77.1.10, Ноутбук 10.77.1.12 (ru_direct, consent)


class RuWorld(RulesGwCase):
    """The scenario of test_rules_gw (ru_direct with the consent for «Мама») + upstream-ru.txt (an AmneziaWG leg
    kawg201, a VLESS key 202) in the Russian group «Дом» (HOME), fit by the snapshot."""

    def world(self, fail="block", via=HOME, save=True):
        st = self.ru_state()
        write_ru_pool([awg_line(31, name="Роутер", endpoint="203.0.113.31:51820", address="10.8.31.2/32"),
                       DACHA_VLESS])
        self.ru_rows = exits.pool(self.w.runner, roles=("ru",))
        self.ru_ids = [e["id"] for e in self.ru_rows]
        doc = st["gateway"].get("exit_policy") or P.default_doc("standard")
        home = P.default_group(HOME, "ru", "ru_home", name="Дом")
        home["fail"] = fail
        home["tiers"][0]["exits"] = list(self.ru_ids)
        doc["groups"] = [g for g in doc["groups"] if g.get("id") != HOME] + [home]
        st["gateway"]["exit_policy"] = doc
        if via is not None:
            st["gateway"]["users"][0]["ru_via"] = via
        snap = exits.load_snapshot()
        for i in self.ru_ids:
            snap.setdefault("exits", {})[i] = {"alive": True, "state": "ok", "reasons": []}
        exits.save_snapshot(snap)
        if save:
            state.save(st)
            return state.load()
        return st

    def pool(self):
        return exits.pool(self.w.runner, roles=exits.ROLES)

    def dec(self, st, alive=None):
        return exits.decision(st, pool_exits=self.pool(), **({} if alive is None else {"alive": set(alive)}))

    def dev(self, st, did=D1):
        return model.get_device(st, did)


class RuTargetTest(RuWorld):
    def test_not_acting_is_none(self):
        st = self.world()
        d = self.dev(st)
        self.assertIsNotNone(exits.ru_target(st, d, self.dec(st)))
        no = copy.deepcopy(st)
        no["gateway"].pop("rf_consent", None)
        cases = [("no consent", no, d)] + [(name, st, dict(d, **change)) for name, change in (
            ("profile all_exit", {"rules": "all_exit"}), ("block by choice", {"route": {"via": "block"}}),
            ("paused", {"enabled": False}))]
        for name, s, dv in cases:
            with self.subTest(name):
                self.assertIsNone(exits.ru_target(s, dv, self.dec(s)))

    def test_direct(self):
        st = self.world(via="direct")
        self.assertEqual(exits.ru_target(st, self.dev(st), self.dec(st)),
                         {"step": "direct", "mark": exits.MARK_DIRECT, "xray": {"outboundTag": "direct"}, "group": None})
        st = self.world(via=None)                                       # no ru_via anywhere: the node — direct
        self.assertEqual(exits.ru_target(st, self.dev(st), self.dec(st))["step"], "direct")

    def test_the_exit_of_its_group(self):
        st = self.world()
        t = exits.ru_target(st, self.dev(st), self.dec(st))
        row = dict((e["id"], e) for e in self.ru_rows)[t["exit"]]
        self.assertEqual((t["step"], t["group"], t["mark"], t["xray"], t["tier"]),
                         ("exit", HOME, row["mark"], {"outboundTag": row["tag"]}, "t1"))
        self.assertTrue(row["tag"].startswith("gr-"))
        self.assertIn(t["mark"], (457, 0x3ca))                         # kawg201 (256+201) or VLESS 202 (0x300+202)

    def test_the_pin_wins(self):
        st = self.world()
        dec = self.dec(st)
        for eid in self.ru_ids:
            dec["groups"] = {HOME: {"pins": {D1: {"exit": eid, "tier": "t1", "why": "new"}}}}
            self.assertEqual(exits.ru_target(st, self.dev(st), dec)["exit"], eid)

    def test_nothing_fit_the_last_step_of_the_group(self):
        for fail, step, mark, out in (("block", "last_block", exits.MARK_BLOCK, exits.FAIL_BLOCK),
                                      ("direct", "last_direct", exits.MARK_DIRECT, "direct")):
            with self.subTest(fail):
                st = self.world(fail=fail)
                t = exits.ru_target(st, self.dev(st), self.dec(st, alive=()))
                self.assertEqual((t["step"], t["mark"], t["xray"], t["group"]),
                                 (step, mark, {"outboundTag": out}, HOME))

    def test_the_fallback_block_of_the_foreign_group_changes_nothing(self):
        """Б1: the foreign exits down (the route on its last step «block») — Russia still through «Дом»."""
        st = self.world()
        dec = self.dec(st, alive=self.ru_ids)
        self.assertEqual(exits.target(st, self.dev(st), dec)["step"], "last_block")
        self.assertEqual(exits.ru_target(st, self.dev(st), dec)["step"], "exit")

    def test_a_deleted_group_is_direct(self):
        st = self.world()
        st["gateway"]["exit_policy"]["groups"] = [g for g in st["gateway"]["exit_policy"]["groups"]
                                                  if g.get("id") != HOME]
        self.assertEqual(exits.ru_target(st, self.dev(st), None)["step"], "direct")

    def test_without_a_decision_both_files_are_read(self):
        st = self.world()
        self.assertEqual(exits.ru_target(st, self.dev(st))["step"], "exit")


class CollectorTest(RuWorld):
    def test_devices_of_the_russian_group(self):
        """§2.7: the Russian group R of ``devices_by_group`` — the devices with «Россия напрямую» acting and ru_via R."""
        st = self.world()
        dec = self.dec(st)
        got = collect.devices_by_group(st, dec["policy"], dec["states"])
        self.assertEqual(sorted(x["id"] for x in got.get(HOME) or []), [D1, D3])
        st = self.world(via="direct")
        dec = self.dec(st)
        self.assertNotIn(HOME, collect.devices_by_group(st, dec["policy"], dec["states"]))

    def test_the_fingerprint_follows_the_russian_target_of_an_xray_user(self):
        """§4.2: the pin of a Russian group changes the rules ``ls-`` — the fingerprint of the decision with it; a node
        where everyone goes «direct» keeps its fingerprint of part A (no key «ru»)."""
        st = self.world()
        st["gateway"]["devices"][0]["access"].append("socks5")
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        pool = self.pool()
        dec = self.dec(st)
        dec["groups"] = {HOME: {"pins": {D1: {"exit": self.ru_ids[0], "tier": "t1", "why": "new"}}}}
        a = collect.fingerprint(st, dec, pool)
        dec["groups"] = {HOME: {"pins": {D1: {"exit": self.ru_ids[1], "tier": "t1", "why": "new"}}}}
        self.assertNotEqual(a, collect.fingerprint(st, dec, pool))
        st2 = self.world(via="direct", save=False)
        st2["gateway"]["devices"][0]["access"].append("socks5")
        st2["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        dec2 = self.dec(st2)
        base = collect.fingerprint(st2, dec2, pool)
        with mock.patch.object(exits, "ru_target", return_value=None):          # part A: no Russian target at all
            self.assertEqual(base, collect.fingerprint(st2, dec2, pool))


class FlushAndImpactTest(RuWorld):
    def test_moved_off_a_russian_exit_forgets_by_its_mark(self):
        """§3 «Обрыв при «снят»»: a device moved off a Russian exit «снят» — conntrack by its address and the mark of
        that exit (the move of the Russian group, as of any group)."""
        st = self.world()
        dec = self.dec(st)
        row = self.ru_rows[0]
        d = {"moves": [{"device": D1, "from": row["id"], "group": HOME}], "states": {row["id"]: "off"},
             "policy": dec["policy"]}
        with mock.patch.object(collect.net, "flush_devices", return_value=1) as fl:
            collect._flush_moved(self.w.runner, st, d, self.pool())
        fl.assert_called_once()
        self.assertEqual((sorted(fl.call_args[0][1]), fl.call_args[0][2]), (["10.77.1.10"], row["mark"]))

    def test_impact_of_the_deletion_of_a_russian_group(self):
        """REV-s1-q1 (QUEUE «T15 — Q2»): «Удалить группу» «Дом» with ``reassign direct`` — the devices of «Мама» move
        (their Russia from the IP of the server); was: «Устройства не сменят путь»."""
        from kaskad.gw import policy_ops
        st = self.world()
        after = copy.deepcopy(st)
        after["gateway"]["exit_policy"]["groups"] = [g for g in after["gateway"]["exit_policy"]["groups"]
                                                     if g.get("id") != HOME]
        model.reassign_ru_via(after, HOME, "direct")
        mv = policy_ops.impact(st, after)
        self.assertEqual((mv["total"], mv["ru"]["total"], mv["ru"]["direct"]), (2, 2, 2))
        self.assertEqual(mv["by_group"], {})                           # the foreign side unchanged
        self.assertTrue(all(x.get("ru") for x in mv["devices"]))
        self.assertEqual(policy_ops.impact(st, copy.deepcopy(st))["total"], 0)


class ForeignPinTest(RuWorld):
    def test_a_foreign_exit_pinned_in_the_russian_group_is_never_taken(self):
        """A pin of the Russian group naming a FOREIGN exit (a hand edit of pins.json, a role changed meanwhile): never
        its mark or «gx-» — Russia never through a foreign exit by this way."""
        st = self.world()
        dec = self.dec(st)
        foreign = [e for e in exits.pool(self.w.runner) if e.get("mark")][0]
        dec["groups"] = {HOME: {"pins": {D1: {"exit": foreign["id"], "tier": "t1", "why": "new"}},
                                "steps": {D1: "exit"}}}
        for fail in ("direct", "block"):
            with self.subTest(fail=fail):
                st = self.world(fail=fail)
                dec = self.dec(st)
                dec["groups"] = {HOME: {"pins": {D1: {"exit": foreign["id"], "tier": "t1", "why": "new"}},
                                        "steps": {D1: "exit"}}}
                t = exits.ru_target(st, self.dev(st), dec)
                self.assertNotEqual(t.get("exit"), foreign["id"])
                self.assertNotEqual(t["mark"], foreign["mark"])
                self.assertFalse(t["xray"]["outboundTag"].startswith("gx-"))
                # REV-s1-q2 П-4: as without the pin — a fit Russian exit, not the last step of the group
                self.assertEqual(t["step"], "exit", t)
                self.assertIn(t["exit"], self.ru_ids)

    def test_a_foreign_exit_inside_the_russian_group_is_skipped(self):
        """П-4: a foreign exit put into the tiers of the Russian group by hand is never chosen — the Russian one is."""
        st = self.world()
        foreign = [e for e in exits.pool(self.w.runner) if e.get("mark")][0]
        doc = st["gateway"]["exit_policy"]
        for g in doc["groups"]:
            for t in g.get("tiers") or []:
                t["exits"] = [e for e in t.get("exits") or [] if e != foreign["id"]]
        home = P.group(doc, HOME)
        t2 = dict(home["tiers"][0], id="t2", exits=list(home["tiers"][0]["exits"]))
        home["tiers"] = [dict(home["tiers"][0], exits=[foreign["id"]]), t2]   # the foreign one alone in the first tier
        state.save(st)
        st = state.load()
        dec = self.dec(st)
        dec["groups"] = {HOME: {"pins": {D1: {"exit": foreign["id"], "tier": "t1", "why": "new"}},
                                "steps": {D1: "exit"}}}
        t = exits.ru_target(st, self.dev(st), dec)
        self.assertEqual(t["step"], "exit", t)
        self.assertIn(t["exit"], self.ru_ids)


R2 = "g-0d0e0f"


class StalePinTest(RuWorld):
    """SEC-s1-q2 С-3 (опыт test_x1): a pin of the Russian group «Дом» naming an exit the owner moved to ANOTHER Russian
    group (somebody else's home router; ``exit_move`` with the same role keeps pins.json) — the readers without the
    collector's fresh decision (gw apply: ``net.plan``; the hook; the render of xray) never take it."""

    def moved(self, keep_b=False):
        from kaskad.gw import pins as PN
        st = self.world()
        a, b = self.ru_ids
        doc = st["gateway"]["exit_policy"]
        P.group(doc, HOME)["tiers"][0]["exits"] = [a]
        r2 = P.default_group(R2, "ru", "ru_home", name="Дача-соседа")
        r2["tiers"][0]["exits"] = [b]
        doc["groups"].append(r2)
        state.save(st)
        pdoc = PN.empty()
        pdoc["groups"] = {HOME: {"pins": {D1: {"exit": a, "tier": "t1", "since": 1, "why": "new"}},
                                 "tier_good_since": {}, "active_tier": "t1", "last_step": None}}
        PN._write(PN.paths.pins_json(), pdoc)
        st = state.load()
        self.assertEqual(exits.ru_target(st, self.dev(st), self.dec(st))["exit"], a)
        doc = st["gateway"]["exit_policy"]
        P.group(doc, HOME)["tiers"][0]["exits"] = [b] if keep_b else []
        P.group(doc, R2)["tiers"][0]["exits"] = [a] if keep_b else [b, a]
        state.save(st)
        return state.load(), a, b

    def test_the_moved_exit_is_never_taken(self):
        from kaskad.gw import net
        st, a, _b = self.moved()
        t = exits.ru_target(st, self.dev(st), self.dec(st))
        self.assertEqual((t["step"], t.get("exit")), ("last_block", None), t)
        rx = net.plan(st, self.w.runner)["rx"]
        self.assertEqual(rx.get("10.77.1.10"), exits.MARK_BLOCK, rx)

    def test_another_exit_of_its_own_group_instead(self):
        st, a, b = self.moved(keep_b=True)
        t = exits.ru_target(st, self.dev(st), self.dec(st))
        self.assertEqual(t.get("exit"), b, t)

    def test_ru_offline_drops_the_pin_of_a_moved_exit(self):
        """FrozenStalePin: ``node.ru_offline`` freezes the Russian groups — never with a pin on an exit of another
        group, and the decision of the collector itself does not lead D1 there."""
        from kaskad.gw import assign
        st, a, _b = self.moved()
        dec0 = self.dec(st)
        pins = {"version": 1, "groups": {HOME: {"pins": {D1: {"exit": a, "tier": "t1", "since": 1, "why": "new"}},
                                                "steps": {D1: "exit"}, "tier_good_since": {}, "active_tier": "t1",
                                                "last_step": None}}}
        devs = collect.devices_by_group(st, dec0["policy"], dec0["states"])
        for node in ({"ru_offline": True}, {"offline": True}, {}):
            with self.subTest(node=node):
                d = assign.assign(devs, dec0["states"], dec0["policy"], pins, {}, node, 100)
                self.assertNotIn(D1, d["groups"][HOME]["pins"])
                d["policy"], d["rows"] = dec0["policy"], dec0["rows"]
                t = exits.ru_target(st, self.dev(st), d)
                self.assertNotEqual(t.get("exit"), a, t)
                self.assertEqual(t["step"], "last_block", t)

    def test_a_frozen_pin_in_its_group_stays(self):
        from kaskad.gw import assign
        st = self.world()
        a, _b = self.ru_ids
        dec0 = self.dec(st)
        pins = {"version": 1, "groups": {HOME: {"pins": {D1: {"exit": a, "tier": "t1", "since": 1, "why": "new"}}}}}
        d = assign.assign({}, dec0["states"], dec0["policy"], pins, {}, {"ru_offline": True}, 100)
        self.assertEqual(d["groups"][HOME]["pins"][D1]["exit"], a)


class RolePinTest(RuWorld):
    """SEC-s1-q2 Б-1 (опыт test_x4): a pin of «Основная» naming an exit whose LINE went to upstream-ru.txt (``exit_move``
    with a change of role: the file first, gw apply inside, ``role_reset_files`` after) — never the whole traffic of the
    device through a Russian exit, whether the policy already moved it to «Дом» or not yet."""

    def test_the_moved_line_is_never_the_target(self):
        from kaskad.gw import pins as PN
        from tests.fakes_gw import write_pool
        for policy_moved in (True, False):
            with self.subTest(policy_moved=policy_moved):
                st = self.world()
                ab = exits.pool(self.w.runner)
                f = [e for e in ab if e.get("mark")][0]
                pdoc = PN.empty()
                pdoc["groups"] = {"main": {"pins": {D1: {"exit": f["id"], "tier": "t1", "since": 1, "why": "new"}},
                                           "tier_good_since": {}, "active_tier": "t1", "last_step": None}}
                PN._write(PN.paths.pins_json(), pdoc)
                self.assertEqual(exits.target(st, self.dev(st), self.dec(st))["exit"], f["id"])
                lines, ru = exits.pool_lines("abroad"), exits.pool_lines("ru")
                write_pool([ln for ln in lines if ln != f["line"]])
                write_ru_pool(ru + [f["line"]])
                if not policy_moved:                 # f the only fit exit of «Основная»: a new pick would take it again
                    snap = exits.load_snapshot()
                    for e in ab:
                        if e["id"] != f["id"]:
                            snap.setdefault("exits", {})[e["id"]] = {"alive": False, "state": "off", "reasons": []}
                    exits.save_snapshot(snap)
                if policy_moved:
                    doc = st["gateway"]["exit_policy"]
                    for g in doc["groups"]:
                        for t in g.get("tiers") or []:
                            t["exits"] = [e for e in t.get("exits") or [] if e != f["id"]]
                    P.group(doc, HOME)["tiers"][0]["exits"].append(f["id"])
                    state.save(st)
                    st = state.load()
                dec = self.dec(st)
                self.assertEqual((dec["rows"].get(f["id"]) or {}).get("role"), "ru")
                t = exits.target(st, self.dev(st), dec)
                self.assertNotEqual(t.get("exit"), f["id"], t)
                self.assertNotEqual(t["mark"], dec["rows"][f["id"]]["mark"], t)
                write_pool(lines)
                write_ru_pool(ru)

    def test_a_decision_still_listing_it_in_the_foreign_group(self):
        """The decision of a tick drawn before the move (its policy still has f in «Основная», f the only fit one):
        the row says «ru» — never the target of a foreign group."""
        import copy
        from tests.fakes_gw import write_pool
        st = self.world()
        ab = exits.pool(self.w.runner)
        f = [e for e in ab if e.get("mark")][0]
        dec0 = self.dec(st)
        lines, ru = exits.pool_lines("abroad"), exits.pool_lines("ru")
        write_pool([ln for ln in lines if ln != f["line"]])
        write_ru_pool(ru + [f["line"]])
        dec = self.dec(st)
        dec["policy"] = copy.deepcopy(dec0["policy"])               # f still in «Основная»
        self.assertIn(f["id"], P.exits_of(P.group(dec["policy"], "main")))
        dec["states"] = dict(dec["states"], **dict((e["id"], "off") for e in ab if e["id"] != f["id"]))
        dec["states"][f["id"]] = "ok"
        dec["groups"] = {"main": {"pins": {D1: {"exit": f["id"], "tier": "t1", "why": "new"}}, "steps": {D1: "exit"}}}
        t = exits.target(st, self.dev(st), dec)
        self.assertNotEqual(t.get("exit"), f["id"], t)
        self.assertEqual(t["step"], "last_block", t)
