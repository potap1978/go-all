"""T5 — SOCKS5 is a hot inbound (SPEC-stage1-exits §2.4, §11.1 «socks5.py, rules/apply.py»): chains ``sk-`` per account
after the shared rules, no closing ``gw-socks5 -> block``, «Россия напрямую» through the shared rules ``ls-``, a route
moves live, a new / removed account or a new password restarts xray (T0 С0.7)."""

import copy
import unittest

from kaskad.gw import exits, xray
from tests import realbin
from tests.gw.s1_xray import S1Case, chain_of, rules_of


class SocksHotTest(S1Case):
    def test_no_closing_block_and_chains_last(self):
        self.node()
        cfg = self.render()
        rules = cfg["routing"]["rules"]
        self.assertFalse([r for r in rules if r.get("inboundTag") == ["gw-socks5"] and r.get("outboundTag") == "block"
                          and "user" not in r and "domain" not in r and "ip" not in r])
        socks = [i for i, r in enumerate(rules) if "gw-socks5" in (r.get("inboundTag") or []) and "user" in r]
        chains = [i for i, r in enumerate(rules) if str(r.get("ruleTag") or "").startswith("sk-")]
        self.assertEqual(len(chains), 3)
        self.assertEqual(chains, list(range(len(rules) - 3, len(rules))))           # the chains are the last rules
        self.assertTrue(all(i < chains[0] for i in socks if i not in chains))       # ls- above them
        self.assertEqual(cfg["outbounds"][0]["tag"], "block")       # what no chain routes goes to the first outbound
        ok = realbin.xray_check(cfg)                                 # SOCKS5 traffic still routed: the real xray
        if ok is not None:
            self.assertTrue(ok[0], ok[1][-500:])

    def test_russia_directly_through_shared_rules(self):
        self.node()
        cfg = self.render()
        for login in ("telefon-0001", "router-0002", "noutbuk-0003"):
            self.assertEqual(len(chain_of(cfg, login)), 1)                          # З3: the route rule alone
        direct = [r for r in rules_of(cfg, prefix="ls-", outboundTag="direct") if "domain" in r]
        self.assertEqual(len(direct), 1)
        self.assertEqual(direct[0]["user"], ["noutbuk-0003", "router-0002", "telefon-0001"])
        self.assertTrue(any("category-ru" in d for d in direct[0]["domain"]))      # a Russian domain -> direct
        force = [r for r in rules_of(cfg, prefix="ls-") if "domain:ifconfig.me" in (r.get("domain") or [])]
        by = dict((r.get("outboundTag"), r["user"]) for r in force)
        self.assertEqual(by, {exits.tag(self.vid): ["noutbuk-0003", "telefon-0001"],
                              exits.tag(self.aid): ["router-0002"]})                 # «always through the exit»: its own
        self.dev("d-00000003")["rules"] = "all_exit"
        direct = [r for r in rules_of(self.render(), prefix="ls-", outboundTag="direct") if "domain" in r]
        self.assertEqual(direct[0]["user"], ["router-0002", "telefon-0001"])

    def test_route_change_is_live_and_accounts_restart(self):
        self.node()
        for d in self.st["gateway"]["devices"]:
            d["rules"] = "all_exit"
        old = self.render()
        self.dev("d-00000001")["route"] = {"via": "direct", "fail": "block"}
        plan = xray.hot_plan(old, self.render(), ["gw-socks5"], modes={"gw-socks5": None})
        self.assertEqual(([r["outboundTag"] for r in plan["add_rules"]], len(plan["rm_rules"]), plan["routing"]),
                         (["direct"], 1, None))
        self.dev("d-00000001")["route"] = None
        self.assertEqual(xray._clients(old, ["gw-socks5"])[("gw-socks5", "telefon-0001")]["pass"], "pw-0001")
        gone = copy.deepcopy(self.st)
        gone["gateway"]["devices"] = [d for d in gone["gateway"]["devices"] if d["id"] != "d-00000001"]
        st, self.st = self.st, gone
        self.assertIsNone(xray.hot_plan(old, self.render(), ["gw-socks5"]))       # removed: a restart
        self.st = st
        self.sec["dev.d-00000002.socks5.password"] = "changed"
        self.assertIsNone(xray.hot_plan(old, self.render(), ["gw-socks5"]))       # a new password: a restart


if __name__ == "__main__":
    unittest.main()
