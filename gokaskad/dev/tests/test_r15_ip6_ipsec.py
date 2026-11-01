"""F8 (3.3.7.15, PLAN-n13 §8.0): IPv6 out of IPsec (IKEv2) is not forwarded — ``meta ipsec exists drop`` in gw_fwd6.

A decrypted IPsec packet arrives on the external interface, not on a kgw-* one, so the per-interface drops of gw_fwd6 do
not see it. The rule stands while an IKEv2 inbound is enabled (the condition under which its pool is forwarded at all);
read_ip6/ip6_problems expect it — a lost rule is «часть IPv6 потеряна», and apply_ip6 (the watchdog's repair) puts it
back. Without IPv6 in the kernel there is no ip6 table and no rule.
"""

from kaskad import nft, state
from kaskad.gw import net
from tests.fakes_gw import register_dummies
from tests.fakes_nft import F443
from tests.gwcase import GwCase

RULE = "meta ipsec exists drop"


class Ip6IpsecTest(GwCase):
    def ikev2(self, enabled=True):
        st = self.scenario(save=False, forwards=[F443])
        register_dummies(("ikev2", {"ifaces": [], "dynamic": True}))
        st["gateway"]["inbounds"]["ikev2"] = {"enabled": enabled}
        state.save(st)
        return state.load()

    def test_rules6_drop_ipv6_out_of_ipsec(self):
        st = self.ikev2()
        rs = net.rules6(st)
        self.assertIn(RULE, rs)                                         # without the fix: only the iifname drops
        self.assertEqual(rs[:2], ['iifname "kgw-wg" drop', 'iifname "kgw-awg2" drop'])
        for text in (nft.render_full(st), nft.render_update(st, None)):
            self.assertIn(RULE, text)

    def test_no_rule_without_an_enabled_ikev2(self):
        self.assertNotIn(RULE, net.rules6(self.scenario()))
        self.assertNotIn(RULE, net.rules6(self.ikev2(enabled=False)))

    def test_live_table_carries_it_and_problems_see_its_loss(self):
        st = self.ikev2()
        nft.apply(st, self.w.runner)
        self.assertEqual(self.w.nft.rules("gw_fwd6", "kaskad6", "ip6"),
                         ['iifname "kgw-wg" drop', 'iifname "kgw-awg2" drop', RULE])
        self.assertEqual(net.ip6_problems(st, self.w.runner), [])
        self.assertEqual(net.read_ip6(self.w.runner)["rules"], ["kgw-wg", "kgw-awg2", net.IPSEC6_NAME])
        self.w.nft.drop_rule("gw_fwd6", 2, "kaskad6", "ip6")              # the IPsec rule lost
        probs = net.ip6_problems(st, self.w.runner)
        self.assertEqual(len(probs), 1)
        self.assertIn("IPsec", probs[0])
        net.apply_ip6(self.w.runner, st)                                  # the repair of ops.check / the watchdog
        self.assertEqual(net.ip6_problems(st, self.w.runner), [])
        self.assertIn(RULE, self.w.nft.rules("gw_fwd6", "kaskad6", "ip6"))

    def test_rule_without_ikev2_is_extra(self):
        st = self.ikev2()
        nft.apply(st, self.w.runner)
        st["gateway"]["inbounds"]["ikev2"]["enabled"] = False             # the live table still has the rule
        self.assertEqual(len(net.ip6_problems(st, self.w.runner)), 1)

    def test_probe_checks_the_construct(self):
        ok, err = net.probe(self.w.runner)
        self.assertTrue(ok, err)
        self.assertIn(RULE, self.w.nft.checked[-1])

    def test_ipv6_off_no_table_no_rule(self):
        self.write(net.IPV6_DISABLE, "1\n", 0o644)
        st = self.ikev2()
        self.assertNotIn(RULE, nft.render_full(st))
        self.assertNotIn("kaskad6", nft.render_update(st, None))
        nft.apply(st, self.w.runner)
        self.assertIsNone(self.w.nft.table("kaskad6", "ip6"))
        self.assertEqual(net.ip6_problems(st, self.w.runner), [])


if __name__ == "__main__":
    import unittest
    unittest.main()
