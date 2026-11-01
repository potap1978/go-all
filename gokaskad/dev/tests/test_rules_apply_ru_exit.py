"""T15 Q2 (SPEC-groups §3, §4.1, §13.1 «rules/apply.py»): ``rd()`` keeps its form and gives only ``ru_via`` «direct»;
``rx()`` — {ip: mark} of the other steps of ``ru_target``; the two never share an address. xray: the rules «напрямую»
of the lists go to the Russian target of their users — «direct», «gr-<id>», «fail-block»; ``force`` still to the
foreign target; a node where everyone is «direct» draws the rules of part A byte for byte."""

import json
from unittest import mock

from kaskad import secrets
from kaskad.gw import exits, xray
from kaskad.rules import apply as rapply
from tests.fakes_gw import register_dummies
from tests.gw.test_ru_target import D1, HOME, RuWorld
from tests.test_rules_gw import SOCKS_IN

D1_IP, D3_IP = "10.77.1.10", "10.77.1.12"


class RdRxTest(RuWorld):
    def test_disjoint(self):
        st = self.world()
        dec = self.dec(st)
        rx = rapply.rx(st, dec)
        self.assertEqual(rapply.rd(st), [])                             # «Мама» goes through «Дом» — not gw_rd
        self.assertEqual(sorted(rx), [D1_IP, D3_IP])
        marks = set(e["mark"] for e in self.ru_rows)
        self.assertTrue(set(rx.values()) <= marks, rx)
        st = self.world(via="direct")
        self.assertEqual(rapply.rd(st), [D1_IP, D3_IP])                 # the form of part A (a list of addresses)
        self.assertEqual(rapply.rx(st, self.dec(st)), {})

    def test_one_device_each_way(self):
        st = self.world(via=None)
        for d in st["gateway"]["devices"]:
            if d["id"] == D1:
                d["ru_via"] = HOME
        self.assertEqual(rapply.rd(st), [D3_IP])
        self.assertEqual(sorted(rapply.rx(st, self.dec(st))), [D1_IP])

    def test_last_steps_and_leases(self):
        st = self.world(fail="direct")
        rx = rapply.rx(st, self.dec(st, alive=()), leases=[("10.77.8.10", st["gateway"]["devices"][0])])
        self.assertEqual(rx, {D1_IP: exits.MARK_DIRECT, D3_IP: exits.MARK_DIRECT, "10.77.8.10": exits.MARK_DIRECT})
        st = self.world(fail="block")
        self.assertEqual(set(rapply.rx(st, self.dec(st, alive=())).values()), {exits.MARK_BLOCK})

    def test_no_consent_nothing(self):
        st = self.world()
        st["gateway"].pop("rf_consent", None)
        self.assertEqual((rapply.rd(st), rapply.rx(st, self.dec(st))), ([], {}))


class _XrayCase(RuWorld):
    def socks(self, st):
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [], "xray_inbound": SOCKS_IN}))
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        for d in st["gateway"]["devices"]:
            d["access"] = sorted(set(d.get("access") or []) | {"socks5"})
        rapply.sync_assets(st, secrets.load(), list(xray._adapters(st)))
        return st

    def rules(self, st, dec):
        cfg = xray.render(st, secrets.load(), self.pool(), (), dec)
        return [r for r in cfg["routing"]["rules"] if r.get("domain") or r.get("ip")], cfg

    def lists(self, rules, out):
        return [r for r in rules if r.get("outboundTag") == out and any("roscomvpn" in x for x in
                                                                          (r.get("domain") or []) + (r.get("ip") or []))]


class XrayRuTargetTest(_XrayCase):
    """§4.1: the rules «напрямую» of the lists of the SOCKS5 users by their Russian target."""

    def test_lists_to_the_russian_exit(self):
        st = self.socks(self.world(save=False))
        dec = self.dec(st)
        rules, cfg = self.rules(st, dec)
        tag = exits.ru_target(st, st["gateway"]["devices"][0], dec)["xray"]["outboundTag"]
        self.assertTrue(tag.startswith("gr-"))
        self.assertIn(tag, [o["tag"] for o in cfg["outbounds"]])
        mine = self.lists(rules, tag)
        self.assertTrue(mine, rules)
        self.assertTrue(all("telefon-0001" in r["user"] for r in mine), mine)
        direct = self.lists(rules, "direct")
        self.assertFalse([r for r in direct if "telefon-0001" in (r.get("user") or [])], direct)
        force = [r for r in rules if any("FORCE" in x.upper() or x.startswith("domain:") for x in r.get("domain") or [])
                 and "telefon-0001" in (r.get("user") or [])]
        self.assertTrue(all(not str(r.get("outboundTag", "")).startswith("gr-") for r in force), force)

    def test_last_step_block(self):
        st = self.socks(self.world(fail="block", save=False))
        rules, _ = self.rules(st, self.dec(st, alive=()))
        self.assertTrue(self.lists(rules, exits.FAIL_BLOCK))
        self.assertFalse([r for r in self.lists(rules, "direct") if "telefon-0001" in r.get("user", [])])

    def test_everyone_direct_is_part_a_byte_for_byte(self):
        """ru_via «direct»: the config equals the one drawn with no Russian target at all (part A: «direct»)."""
        st = self.socks(self.world(via="direct", save=False))
        dec = self.dec(st)
        _, cfg = self.rules(st, dec)

        def part_a(s, d, decision=None, is_ready=None):
            return {"step": "direct", "mark": exits.MARK_DIRECT, "xray": {"outboundTag": "direct"}, "group": None} \
                if rapply.device_rd(s, d, is_ready=is_ready) else None
        with mock.patch.object(exits, "ru_target", part_a):
            _, cfg_a = self.rules(st, dec)
        self.assertEqual(json.dumps(cfg, sort_keys=True), json.dumps(cfg_a, sort_keys=True))
        self.assertTrue(self.lists(self.rules(st, dec)[0], "direct"))


class HotTest(_XrayCase):
    """§4.2: another pin in the Russian group (and the last step and back) changes the users of the rules ``ls-`` — the
    routing anew through the API, no restart."""

    def test_a_russian_pin_is_hot(self):
        st = self.socks(self.world(save=False))
        dec = self.dec(st)
        a, b = self.ru_ids
        cfgs = []
        for eid in (a, b):
            dec["groups"] = {HOME: {"pins": {D1: {"exit": eid, "tier": "t1", "why": "new"}}}}
            cfgs.append(xray.render(st, secrets.load(), self.pool(), (), dec))
        self.assertNotEqual(json.dumps(cfgs[0]["routing"], sort_keys=True), json.dumps(cfgs[1]["routing"], sort_keys=True))
        tags = xray.hot_tags(st, secrets.load())
        plan = xray.hot_plan(cfgs[0], cfgs[1], tags)
        self.assertIsNotNone(plan)
        self.assertTrue(plan.get("routing"))
        last = xray.render(st, secrets.load(), self.pool(), (), self.dec(st, alive=()))
        self.assertIsNotNone(xray.hot_plan(cfgs[0], last, tags))
