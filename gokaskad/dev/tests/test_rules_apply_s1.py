"""T5 — ``rules/apply.py`` of stage 1 (SPEC-stage1-exits §2.2–2.4, §11.1): ``xray_rules``/``user_chain`` call
``target(route, device)`` (the pin of the device), shared rules ``ls-`` by target with the users, never with an empty
list (З9), only a block BY CHOICE closes Russia (the fallback ``fail-block`` does not); ``rd()`` without a block by
choice, a pause or a revoke (§2.3)."""

import unittest

from kaskad.gw import xray
from kaskad.rules import apply as rules_apply
from tests.gw.s1_xray import MAN, S1Case


class RulesApplyS1Test(S1Case):
    def adapters(self):
        return list(xray._adapters(self.st))

    def target(self, shared=True):
        seen = []

        def t(route, device=None):
            seen.append((device or {}).get("id"))
            if (route or {}).get("via") == "block":
                return {"outboundTag": "block"}
            return {"outboundTag": {"d-00000001": "gx-a", "d-00000002": xray.FAIL_BLOCK}.get((device or {}).get("id"),
                                                                                            "gx-b")}
        t.shared_lists = shared
        return t, seen

    def test_shared_lists_by_pin_and_fail_block_keeps_russia(self):
        self.node()
        t, seen = self.target()
        rules = rules_apply.xray_rules(self.st, self.sec, t, self.adapters(), man=MAN, is_ready=True,
                                       chained=["gw-socks5"])
        self.assertTrue({"d-00000001", "d-00000002", "d-00000003"} <= set(seen))   # each device: its own pin
        ls = [r for r in rules if str(r.get("ruleTag") or "").startswith("ls-")]
        force = dict((r["outboundTag"], r["user"]) for r in ls if "domain:ifconfig.me" in (r.get("domain") or []))
        self.assertEqual(force, {"gx-a": ["telefon-0001"], xray.FAIL_BLOCK: ["router-0002"],
                                 "gx-b": ["noutbuk-0003"]})
        direct = [r for r in ls if r.get("outboundTag") == "direct" and "domain" in r][0]
        self.assertIn("router-0002", direct["user"])                  # the fallback block: Russia still direct
        self.assertEqual(rules_apply.user_chain(self.st, t, ["gw-socks5"], "telefon-0001",
                                                self.dev("d-00000001"), man=MAN, is_ready=True), [])
        self.dev("d-00000001")["route"] = {"via": "block", "fail": "block"}         # a block by choice: no Russia
        rules = rules_apply.xray_rules(self.st, self.sec, t, self.adapters(), man=MAN, is_ready=True,
                                       chained=["gw-socks5"])
        self.assertFalse([r for r in rules if "telefon-0001" in (r.get("user") or [])])

    def test_no_rule_with_an_empty_list(self):
        self.node()
        for d in self.st["gateway"]["devices"]:
            d["rules"] = "all_exit"
        t, _ = self.target()
        rules = rules_apply.xray_rules(self.st, self.sec, t, self.adapters(), man=MAN, is_ready=True,
                                       chained=["gw-socks5"])
        self.assertTrue(rules)                                         # the ads block stays
        self.assertFalse([r for r in rules if r.get("outboundTag") != "block"])
        self.assertFalse([r for r in rules if str(r.get("ruleTag") or "").startswith("ls-")])

    def test_legacy_lists_per_user_chain(self):
        self.node()
        t, seen = self.target(shared=False)
        chain = rules_apply.user_chain(self.st, t, ["gw-socks5"], "telefon-0001", self.dev("d-00000001"), man=MAN,
                                       is_ready=True)
        self.assertEqual([r["outboundTag"] for r in chain], ["gx-a", "direct", "direct"])
        self.assertEqual(seen, ["d-00000001"])
        rules = rules_apply.xray_rules(self.st, self.sec, t, self.adapters(), man=MAN, is_ready=True,
                                       chained=["gw-socks5"])
        self.assertFalse([r for r in rules if str(r.get("ruleTag") or "").startswith("ls-")])

    def test_rd_without_block_by_choice_pause_revoke(self):
        self.node()
        self.dev("d-00000001")["route"] = None
        got = rules_apply.rd(self.st, is_ready=True)
        self.assertIn("10.77.1.10", got)
        self.dev("d-00000001")["route"] = {"via": "block", "fail": "block"}
        self.assertNotIn("10.77.1.10", rules_apply.rd(self.st, is_ready=True))
        self.dev("d-00000001")["route"] = None
        self.dev("d-00000001")["enabled"] = False                      # a pause
        self.assertNotIn("10.77.1.10", rules_apply.rd(self.st, is_ready=True))
        self.dev("d-00000001")["enabled"] = True
        self.st["gateway"]["users"][0]["enabled"] = False              # the person off
        self.assertNotIn("10.77.1.10", rules_apply.rd(self.st, is_ready=True))
        self.st["gateway"]["users"][0]["enabled"] = True
        self.dev("d-00000003")["route"] = {"via": "exit:e-dead", "fail": "block"}   # exits dead: still in gw_rd
        self.assertIn("10.77.1.12", rules_apply.rd(self.st, is_ready=True))


if __name__ == "__main__":
    unittest.main()
