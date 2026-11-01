"""T5 — the text of the gateway part of stage 1 (``net.rules()``, SPEC-stage1-exits §2.3, §4.5, §5.2, §2.9 П5, §11.1
«net.py»): against the text of 3.3.5 exactly ``gw_ruonly`` (its rule) and the condition of ``gw_rd`` gone; ``gw_out``
closes to non-root exactly the ports of the config of xray and the API of the bot; ``numgen`` of «по соединениям».
The filling of the sets (gw_live, gw_ruonly) and the marks — T4."""

import json
import os
import unittest
from unittest import mock

from kaskad.gw import net, ruguard
from tests.gwcase import GwCase

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "gw",
                   "s1_t5_net_rules_335.json")


class NetS1Test(GwCase):
    def plan(self, **kw):
        with open(FIX, encoding="utf-8") as f:
            fx = json.load(f)
        p = dict(fx["plan"])
        p["elems"] = dict((k, v) for k, v in p["elems"].items())
        p["vless"] = [tuple(x) for x in p["vless"]]
        p.update(kw)
        return fx, p

    def test_diff_against_335_is_exactly_ruonly_and_rd(self):
        st = self.scenario(save=False)
        fx, p = self.plan()
        got = dict(net.rules(st, p))
        old = dict((c, rs) for c, rs in fx["rules"])
        self.assertEqual(set(got), set(old))
        for chain in old:
            if chain not in ("gw_pre", "gw_out", "gw_fwd", "gw_dns"):
                self.assertEqual(got[chain], old[chain], chain)
        # gw_dns (3.3.7.2, SEC-3371 Б-2): the clients of the mark 0x201 to the second dnsmasq — the rules of 3.3.5 stay,
        # each with the mark put back to 0 before its DNAT; the map lookup and the two rules of 0x201 before them
        self.assertEqual(got["gw_dns"][3:], [r.replace(" dnat to", " meta mark set 0x0 dnat to") for r in old["gw_dns"]])
        self.assertEqual(len(got["gw_dns"]), 5)
        self.assertTrue(got["gw_dns"][0].endswith("meta mark set ip saddr map @gw_route"))
        # gw_fwd: + one rule, the guard of «по соединениям» drawn always (SEC-s1-t45 С-1: an element 0x2fe between two
        # full applies is dropped, never out directly), right after «ct mark 0x0 drop»; nothing of 3.3.5 changes or goes
        i = old["gw_fwd"].index("ip saddr @gw_nets ip daddr != @gw_nets ct mark and 0xffff == 0x0 drop") + 1
        self.assertEqual(got["gw_fwd"], old["gw_fwd"][:i] + [net.SPREAD_GUARD_FWD] + old["gw_fwd"][i:])
        self.assertEqual(net.SPREAD_GUARD_FWD, "ip saddr @gw_nets ct mark and 0xffff == 0x2fe drop")
        # gw_out: + one rule, the ports of xray root alone may open — by the set gw_xports (§5.2, §2.9 П5; REV-s1-t5
        # З3: a set refilled with the pool, not a rule text); nothing of 3.3.5 changes or goes
        self.assertEqual(got["gw_out"], old["gw_out"] + ["ip daddr 127.0.0.1 tcp dport @gw_xports meta skuid != 0 "
                                                         "reject"])
        added = [r for r in got["gw_pre"] if r not in old["gw_pre"]]
        gone = [r for r in old["gw_pre"] if r not in got["gw_pre"]]
        self.assertEqual(added, ["ip daddr 10.77.0.1 meta l4proto { tcp, udp } th dport { 5354, 5364 } drop",  # SEC-3372 З-1
                                 "ip saddr @gw_ruonly fib daddr type local drop",
                                 "meta mark 0x2fe meta mark set 0x2ff",          # С-1: always, the mode on or off
                                 "ip saddr @gw_rd ip daddr . meta l4proto @gw_ru ip daddr != @gw_force meta mark set 0x201",
                                 "ip saddr @gw_rd ip daddr @gw_ru_dns ip daddr != @gw_force meta mark set 0x201"])
        self.assertEqual([r.replace(" meta mark != 0x2ff", "") for r in gone], added[3:])
        self.assertEqual(got["gw_pre"][got["gw_pre"].index("meta mark set ip saddr map @gw_route") + 1],
                         "meta mark 0x2fe meta mark set 0x2ff")                  # right after the route
        i = got["gw_pre"].index("ip saddr != @gw_live drop")
        self.assertEqual(got["gw_pre"][i + 1], "ip saddr @gw_ruonly fib daddr type local drop")   # right after it
        self.assertEqual(list(net.SETS), fx["sets"] + ["gw_ruonly", "gw_xports"])
        self.assertEqual(net.SET_SPECS["gw_ruonly"], "type ipv4_addr;")
        self.assertEqual(net.SET_SPECS["gw_xports"], "type inet_service;")

    def test_gw_out_root_only_exactly_the_drawn_ports(self):
        st = self.scenario(save=False)
        _, p = self.plan(xray_ports=[10852, 10854, 12699, 12701])
        out = dict(net.rules(st, p))["gw_out"]
        self.assertEqual(out[-1], "ip daddr 127.0.0.1 tcp dport @gw_xports meta skuid != 0 reject")
        ports = net.xports_of(p)                                      # REV-s1-t5 З3: the ports — the set's elements
        self.assertEqual(ports, [10851, 10852, 10854, 12699, 12701])
        self.assertNotIn(12702, ports)                                # not drawn: not closed for the neighbours
        self.assertNotIn(10853, ports)                                # dnsmasq goes there
        lines = net._table_lines(st, p)
        self.assertIn("\t\telements = { 10851, 10852, 10854, 12699, 12701 }",
                      lines[lines.index("\tset gw_xports {"):][:4])
        _, p0 = self.plan()                                           # no xray of stage 1 here: the bot's API alone
        self.assertEqual(net.xports_of(p0), [10851])
        self.assertEqual([r for r in dict(net.rules(st, p0))["gw_out"] if "reject" in r], [out[-1]])

    def test_spread_numgen(self):
        st = self.scenario(save=False)
        _, p = self.plan(spread=[0x102, 0x200, 0x103])
        pre = dict(net.rules(st, p))["gw_pre"]
        i = pre.index("meta mark set ip saddr map @gw_route")
        self.assertEqual(pre[i + 1:i + 3], [
            "meta mark 0x2fe meta mark set numgen random mod 3 map { 0 : 0x102, 1 : 0x200, 2 : 0x103 }",
            "meta mark 0x2fe meta mark set 0x2ff"])
        self.assertIn("ip saddr @gw_nets ct mark and 0xffff == 0x2fe drop", dict(net.rules(st, p))["gw_fwd"])
        _, p = self.plan(spread=[])                                    # the mode on, nothing good: blocked
        pre = dict(net.rules(st, p))["gw_pre"]
        self.assertIn("meta mark 0x2fe meta mark set 0x2ff", pre)
        self.assertFalse([r for r in pre if "numgen" in r])
        self.assertNotIn(net.MARK_SPREAD, net._ALLOWED_MARKS)          # T4 lets it into gw_route with the plan only

    def test_the_fake_nft_takes_the_new_text(self):
        st = self.scenario(save=False)
        _, p = self.plan(xray_ports=[10852, 10854, 12699, 12701], spread=[0x102, 0x200], ruonly=["10.77.1.11"])
        with mock.patch.object(ruguard, "allowed", return_value=True):     # D-136: gw_ruonly needs «Россия напрямую»
            lines = net._table_lines(st, p)
        self.assertIn("\tset gw_ruonly {", lines)
        self.assertIn("\t\telements = { 10.77.1.11 }", lines)
        text = "table ip kaskad {\n" + "\n".join(lines) + "\n}\n"
        rc, err = self.w.nft.run_script(text, True)[:2]
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.w.nft.elements("gw_xports"), ["10851", "10852", "10854", "12699", "12701"])


if __name__ == "__main__":
    unittest.main()
