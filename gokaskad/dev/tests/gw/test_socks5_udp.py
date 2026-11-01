"""T-mip-lab Н1: the UDP of SOCKS5 went to «block» — xray hands a UDP packet of SOCKS5 to the routing without the user
(UDP ASSOCIATE carries no login), so no chain ``sk-<login>`` matched it. One rule ``sk-_udp-…`` for the UDP of the
inbound, to the target all the accounts share; different routes — no rule (closed), the issuance says so."""

import json
import unittest

from kaskad.gw import xray
from tests import realbin
from tests.gw.s1_xray import S1Case
from tests.gw.tunnel_case import GwCase, device, gw_state

SOCKS = {"socks5": {"enabled": True, "port": 1080, "udp": True}}


def udp_rules(rules):
    return [r for r in rules if r.get("network") == "udp" and "gw-socks5" in (r.get("inboundTag") or [])]


class AdapterTest(GwCase):
    def setUp(self):
        super().setUp()
        self.s5 = self.adapter("socks5")

    def state(self, devs, socks=SOCKS):
        st = gw_state(json.loads(json.dumps(socks)), devs)
        self.s5.prepare(st, self.sec)
        return st

    def test_one_route_for_all_routes_the_udp(self):
        st = self.state([device("d-pc", "Компьютер", "windows", ["socks5"], kind="pc"),
                         device("d-tel", "Телефон", "android", ["socks5"])])
        rules = self.s5.xray_routing(st, self.sec)
        udp = udp_rules(rules)
        self.assertEqual(len(udp), 1)
        self.assertIs(rules[-1], udp[0])                       # after the chains: the API appends there too
        tag = udp[0].pop("ruleTag")
        self.assertEqual(udp[0], {"type": "field", "inboundTag": ["gw-socks5"], "network": "udp", "balancerTag": "pool"})
        self.assertRegex(tag, r"^sk-_udp-[0-9a-f]{10}-0$")     # «sk-»: hot_plan swaps it; «_» — never a login
        self.assertNotIn("user", udp[0])
        self.assertEqual(self.s5._udp_key(st, self.sec), "gw_in_socks5.udp_on")
        # REV-mip-s34 З-3: the UDP rule has no user — the lists of the user («Россия напрямую», own) are not for it
        got = json.dumps(self.s5.issue(st, self.sec, "d-pc")["items"], ensure_ascii=False)
        self.assertIn("UDP через прокси: да.", got)
        self.assertIn("UDP идёт без правил «Россия напрямую» и списков пользователя — весь по его маршруту.", got)

    def test_different_routes_leave_the_udp_closed_and_say_so(self):
        st = self.state([device("d-pc", "Компьютер", "windows", ["socks5"], kind="pc"),
                         device("d-guest", "Гость", "android", ["socks5"], user="u-2")])     # u-2: direct
        self.assertEqual(udp_rules(self.s5.xray_routing(st, self.sec)), [])
        self.assertIsNone(self.s5.udp_target(st, self.sec))
        self.assertEqual(self.s5._udp_key(st, self.sec), "gw_in_socks5.udp_mixed")
        got = json.dumps(self.s5.issue(st, self.sec, "d-pc")["items"], ensure_ascii=False)
        self.assertIn("разные маршруты", got)
        self.assertNotIn("UDP идёт без правил", got)            # closed: nothing goes

    def test_udp_off_no_rule(self):
        st = self.state([device("d-pc", "Компьютер", "windows", ["socks5"], kind="pc")],
                        socks={"socks5": {"enabled": True, "port": 1080, "udp": False}})
        self.assertEqual(udp_rules(self.s5.xray_routing(st, self.sec)), [])
        self.assertEqual(self.s5._udp_key(st, self.sec), "gw_in_socks5.udp_off")

    def test_one_pin_by_the_device_else_the_route_alone(self):
        st = self.state([device("d-pc", "Компьютер", "windows", ["socks5"], kind="pc"),
                         device("d-tel", "Телефон", "android", ["socks5"])])
        seen = []

        def same_pin(route, dev):
            seen.append(dev["id"] if dev else None)
            return {"outboundTag": "gx-a"}
        self.assertEqual(self.s5.udp_target(st, self.sec, same_pin), {"outboundTag": "gx-a"})
        self.assertNotIn(None, seen)                           # both by the device: one pin

        def pins(route, dev):
            return {"outboundTag": "gx-" + dev["id"]} if dev else {"balancerTag": "gw-all"}
        self.assertEqual(self.s5.udp_target(st, self.sec, pins), {"balancerTag": "gw-all"})     # the group's balancer


class RenderTest(S1Case):
    def test_the_udp_of_socks5_leaves_by_the_route_not_block(self):
        self.node()
        self.st["gateway"]["inbounds"]["socks5"]["udp"] = True
        for d in self.st["gateway"]["devices"]:
            d["route"] = {"via": "pool", "fail": "block"}          # one route, three pins (two exits)
        cfg = self.render()
        udp = udp_rules(cfg["routing"]["rules"])
        self.assertEqual(len(udp), 1)
        self.assertNotIn(udp[0].get("outboundTag"), ("block", xray.FAIL_BLOCK))
        self.assertEqual(udp[0].get("balancerTag"), "gw-all")  # three pins, one route «pool»: the pool of «Основная»
        self.assertIn("gw-all", [b["tag"] for b in cfg["routing"]["balancers"]])
        self.assertIs(cfg["routing"]["rules"][-1], udp[0])
        old = self.render()
        self.dev("d-00000001")["route"] = {"via": "direct", "fail": "block"}
        new = self.render()
        self.assertEqual(udp_rules(new["routing"]["rules"]), [])                # routes differ: closed
        plan = xray.hot_plan(old, new, ["gw-socks5"], modes={"gw-socks5": None})
        self.assertIsNotNone(plan)                              # live, no restart: the rule goes with the chains
        gone = plan["rm_rules"] if plan["routing"] is None else \
            [udp[0]["ruleTag"]] if not udp_rules(plan["routing"]["rules"]) else []
        self.assertIn(udp[0]["ruleTag"], gone)
        ok = realbin.xray_check(cfg)
        if ok is not None:
            self.assertTrue(ok[0], ok[1][-500:])


if __name__ == "__main__":
    unittest.main()
