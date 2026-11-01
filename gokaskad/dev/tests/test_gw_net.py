"""kaskad.gw.net — the gateway part of table ip kaskad (a section of nft.render_full/apply/verify),
the ip6 table, ip rules / table 200 / kgw0, the probe of the constructs, the live map.

Golden files (tests/fixtures/nft/gw_*.nft) — regenerate after a deliberate change:
    KASKAD_UPDATE_GOLDEN=1 PYTHONPATH=lib:. python3 -m unittest tests.test_gw_net
"""

import copy
import json
import os
import re
import stat

from kaskad import forwards, nft, paths, secrets, state
from kaskad.errors import KaskadError, UnsupportedError
from kaskad.gw import dns, exits, net, ops, xray
from kaskad.gw import sys as gsys
from kaskad.gw.inbounds.base import DummyInbound
from kaskad.gw import registry
from tests.fakes_gw import INSTALL_SH, register_dummies, touch_exec
from tests.fakes_nft import F443, F51820, make_state, save_state
from tests.gwcase import GwCase

GOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "nft")
UPDATE = os.environ.get("KASKAD_UPDATE_GOLDEN") == "1"

# the gateway table verified on Debian 12 and Ubuntu 24.04 by k3/stand/gwcheck.sh (every line must stay;
# ``fib daddr type local return`` became ``fib daddr type local meta mark set 0x202 ... return`` — the kill
# switch, 2026-10-04 — to be verified on the stand)
VERIFIED = [
    "ip saddr != @gw_nets return",
    "ip daddr @gw_nets return",
    "ct state established,related meta mark set ct mark and 0xffff return",
    "meta mark set ip saddr map @gw_route",
    # wave 3 replaced «ip daddr @gw_ru meta mark != 0x2ff meta mark set 0x201» (gw_ru was empty) with the two rules
    # of «Russia directly» (net.ru_rules: gw_rd, gw_ru of RU_FORM, gw_ru_dns, gw_force) — to be verified on the stand
    "ct mark set meta mark or 0x4b000000",
    "meta mark 0x200 meta l4proto { tcp, udp } tproxy to 127.0.0.1:12345",
    "ip saddr @gw_nets meta mark 0x2ff drop",
    "ip saddr @gw_nets tcp flags syn tcp option maxseg size set rt mtu",
    'ip saddr @gw_nets oifname != "lo" ip daddr != @gw_nets masquerade',
    "type filter hook prerouting priority -150; policy accept;",
    "type filter hook forward priority -4; policy accept;",
    "type nat hook postrouting priority 100; policy accept;",
]


class RenderTest(GwCase):
    def golden(self, name, text):
        p = os.path.join(GOLD, name)
        if UPDATE:
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(text, f.read(), "golden %s differs" % name)

    def test_full_text_of_the_report_scenario(self):
        st = self.scenario(forwards=[F443])
        self.w.users.uids[net.XRAY_USER] = 990                      # xray's user exists: gw_out has its rule
        text = nft.render_full(st)
        self.golden("gw_full.nft", text)
        lines = [ln.strip() for ln in text.splitlines()]
        for v in VERIFIED:
            self.assertIn(v, lines)
        # the verified "meta mark 0 meta mark set 0x2ff" became "meta mark set 0x2ff" BEFORE the lookup:
        # a miss keeps the block mark whatever mark the packet came with
        self.assertNotIn("meta mark 0 meta mark set 0x2ff", lines)
        self.assertEqual(lines.index("meta mark set 0x2ff") + 1, lines.index("meta mark set ip saddr map @gw_route"))
        self.assertLess(lines.index("ip saddr != @gw_live drop"),
                        lines.index("ct state established,related meta mark set ct mark and 0xffff return"))
        self.assertIn("elements = { 10.77.1.10, 10.77.1.11, 10.77.1.12, 10.77.3.10 }", lines)     # gw_live
        self.assertIn('meta mark and 0xffffff00 == 0x100 oifname != "kawg*" drop', lines)      # gw_out
        # stage 1 (SPEC-stage1-exits §1.3: «без влияния на маршрут»; §5.2, §2.9 П5; REV-s1-t5 З3): the ports of xray
        # root alone may open — the set gw_xports (the inbounds of the config drawn by the decision + the API of the
        # bot 10851; never 10853 of dnsmasq), one rule after the rule of xray's user; nothing of 3.3.5 goes
        self.assertEqual(lines[lines.index("set gw_xports {") + 1:lines.index("set gw_xports {") + 3],
                         ["type inet_service", "elements = { 10851, 10852, 10854, 12699, 12701 }"])
        self.assertEqual(lines.index("ip daddr 127.0.0.1 tcp dport @gw_xports meta skuid != 0 reject") - 1,
                         lines.index("meta skuid 990 ip daddr 127.0.0.0/8 ct direction original meta l4proto "
                                     "{ tcp, udp } th dport != 53 drop"))
        self.assertIn("elements = { 10.77.0.0/16 }", lines)
        # devices: pool (rendezvous -> the AmneziaWG leg 2), user route exit:<awg>, device route exit:<vless>
        # (line 1 of the pool: mark 0x301, its own TPROXY port 12401)
        self.assertIn("elements = { 10.77.1.10 : 0x102, 10.77.1.11 : 0x102, 10.77.1.12 : 0x301, "
                      "10.77.3.10 : 0x102 }", lines)
        self.assertTrue(text.startswith("table ip kaskad\ndelete table ip kaskad\ntable ip kaskad {\n"))
        self.assertIn("table ip6 kaskad6\ndelete table ip6 kaskad6\ntable ip6 kaskad6 {\n", text)
        self.assertIn('\t\tiifname "kgw-wg" drop\n\t\tiifname "kgw-awg2" drop\n', text)
        # one table: forwards first (wave-1 text unchanged), the gateway inside the same block
        self.assertLess(text.index("chain fwd_post"), text.index("set gw_nets"))
        self.assertEqual(text.count("table ip kaskad {"), 1)

    def test_blocked_devices_get_dns_only_and_clients_never_meet(self):
        st = self.scenario()
        lines = dict(net.rules(st))["gw_pre"]
        i = lines.index
        dns = "ip daddr 10.77.0.1 meta l4proto { tcp, udp } th dport 53 ct mark set ct mark or 0x4b000000 return"
        local = "fib daddr type local meta mark set 0x202 ct mark set ct mark or 0x202 return"
        ping = "ip daddr 10.77.0.1 icmp type echo-request ct mark set ct mark or 0x4b000000 return"
        # DNS for everyone of the base, then the cut of whoever is not allowed out, only then the node and
        # the base: a device with route «block» reaches nothing but 10.77.0.1:53 and the ping of 10.77.0.1
        # (T-N5 3.2.19, №12: without exits the client did not even ping the gateway)
        self.assertLess(i(dns), i("ip saddr != @gw_live drop"))
        self.assertLess(i(ping), i("ip saddr != @gw_live drop"))
        self.assertLess(i("ip saddr != @gw_live drop"), i(local))
        self.assertLess(i(local), i("ip daddr @gw_nets return"))
        fwd = dict(net.rules(st))["gw_fwd"]
        self.assertIn("ip saddr @gw_nets ip daddr @gw_nets drop", fwd)                      # client to client
        self.assertIn("ip saddr @gw_nets ip daddr @gw_priv ct mark and 0xffff != 0x202 drop", fwd)
        self.assertIn("ip saddr @gw_nets ip daddr != @gw_nets ct mark and 0xffff == 0x0 drop", fwd)
        self.assertIn("ip saddr @gw_nets ct mark and 0xff00 == 0x300 drop", fwd)           # VLESS exits: TPROXY
        self.assertNotIn("ip saddr @gw_nets ip daddr 169.254.0.0/16 drop", fwd)              # inside gw_priv now
        self.assertIn("169.254.0.0/16", net.PRIVATE)
        self.assertIn("100.64.0.0/10", net.PRIVATE)

    def test_spoofing_rules_follow_the_inbounds(self):
        st = self.scenario()
        register_dummies(("l2tp", {"ifaces": ["kgw-l2tp*"]}), ("sstp", {"ifaces": ["kgw-sstp*"]}),
                         ("ikev2", {"ifaces": [], "dynamic": True}), ("socks5", {"ifaces": [], "proto": "tcp"}))
        for k in ("l2tp", "sstp", "ikev2", "socks5"):
            st["gateway"]["inbounds"][k] = {"enabled": True}
        pre = dict(net.rules(st))["gw_pre"]
        self.assertEqual(pre[:4], ["ct direction reply ct mark and 0xffff != 0x0 meta mark set meta mark or 0x10000",
                                   'iifname "lo" return', 'iifname "kgw-*" ip saddr != @gw_nets drop',
                                   'iifname "kgw-*" fib saddr . iif oif missing drop'])
        self.assertEqual(net.spoof_rules(st), [
            'ip saddr 10.77.1.0/24 iifname != "kgw-wg" drop',
            'ip saddr 10.77.3.0/24 iifname != "kgw-awg2" drop',
            'ip saddr 10.77.5.0/24 iifname != "kgw-l2tp*" iifname != "kgw-sstp*" drop',     # one pool, two inbounds
            "ip saddr 10.77.8.0/24 meta ipsec missing drop"])                               # IKEv2: out of IPsec
        self.assertIn('ip daddr @gw_nets iifname != "kgw-*" meta ipsec missing drop', pre)
        self.assertLess(pre.index('ip daddr @gw_nets iifname != "kgw-*" meta ipsec missing drop'),
                        pre.index("ip saddr != @gw_nets return"))
        st["gateway"]["inbounds"]["sstp"]["enabled"] = False
        self.assertIn('ip saddr 10.77.5.0/24 iifname != "kgw-l2tp*" drop', net.spoof_rules(st))
        registry.register(DummyInbound("wg", ifaces=['kgw-wg" accept; "']))                 # broken: subnet closed
        self.assertIn("ip saddr 10.77.1.0/24 drop", net.spoof_rules(st))
        registry.register(DummyInbound("wg", ifaces=["vpns0"]))                           # not kgw-*: reported
        self.assertTrue(any("vpns0" in p and "kgw-*" in p for p in net.bad_data(st)))
        st["gateway"]["inbounds"]["l2tp"]["enabled"] = False
        text = nft.render_full(st)
        self.assertEqual(self.w.nft.run_script(text, False)[0], 0, text)                  # the emulator takes it

    def test_xray_user_rule(self):
        st = self.scenario()
        out = dict(net.rules(st))["gw_out"]
        # stage 1 (SPEC-stage1-exits §1.3: «без влияния на маршрут»; §5.2, §2.9 П5): + the rule of the ports of xray
        # root alone may open (the set gw_xports, REV-s1-t5 З3) — always the last; the rule of xray's user as before
        root_only = "ip daddr 127.0.0.1 tcp dport @gw_xports meta skuid != 0 reject"
        self.assertEqual(out[1:], [root_only])                     # no user yet: no rule of xray's user
        self.w.users.uids[net.XRAY_USER] = 990
        out = dict(net.rules(st))["gw_out"]
        self.assertEqual(out[1], "meta skuid 990 ip daddr 127.0.0.0/8 ct direction original "
                                 "meta l4proto { tcp, udp } th dport != 53 drop")
        self.assertEqual(out[2:], [root_only])
        self.assertEqual(net.rules6_out(net.xray_uid()), ["meta skuid 990 ip6 daddr ::1 ct direction original "
                                                          "meta l4proto { tcp, udp } th dport != 53 drop"])

    def test_update_script(self):
        st = self.scenario(forwards=[F443])
        self.golden("gw_update_fresh.nft", nft.render_update(st, None))

    def test_disabled_gateway_adds_nothing(self):
        st = self.scenario(enabled=False, forwards=[F443])
        self.assertEqual(nft.render_full(st), nft.render_full(make_state([F443])))
        self.assertEqual(nft.render_update(st, None), nft.render_update(make_state([F443]), None))

    def test_dns_rule_follows_the_base(self):
        st = self.scenario()
        st["gateway"]["net"]["base"] = "172.27.0.0/16"
        for d in st["gateway"]["devices"]:
            d["ips"] = dict((k, v.replace("10.77.", "172.27.")) for k, v in d["ips"].items())
        text = nft.render_full(st)
        self.assertIn("ip daddr 172.27.0.1 meta l4proto { tcp, udp } th dport 53", text)
        self.assertIn("elements = { 172.27.0.0/16 }", text)
        self.assertNotIn("10.77.", text)

    def test_nothing_unvalidated_reaches_the_text(self):
        # a broken adapter or broken state must neither inject nft text nor stop the table (forwards
        # live in it): the bad item is skipped — that address is blocked — and gw check reports it
        st = self.scenario(forwards=[F443])
        registry.register(DummyInbound("wg", ifaces=['kgw-wg" drop; flush ruleset; "']))
        text = nft.render_full(st)
        self.assertNotIn("flush ruleset", text)
        self.assertNotIn('iifname "kgw-wg', text)
        self.assertIn('iifname "kgw-awg2" drop', text)
        self.assertTrue(any("недопустимое имя интерфейса" in p for p in net.bad_data(st)))
        registry.register(DummyInbound("wg", ifaces=["kgw-wg"]))
        st["gateway"]["devices"][0]["ips"]["wg"] = "192.0.2.10"           # outside the base
        text = nft.render_full(st)
        self.assertNotIn("192.0.2.10", text)
        self.assertIn("dnat to 203.0.113.5:443", text)
        self.assertTrue(any("192.0.2.10" in p for p in net.bad_data(st)))
        self.assertEqual(net.elements_text(st, {"10.77.1.10": 0x4b000000}), "")     # not a route mark
        with self.assertRaises(KaskadError):
            net.elements_text(st, {"10.77.1.10": 0x4b000000}, loud=True)
        with self.assertRaises(KaskadError):
            net.set_element(self.w.runner, st, "192.0.2.10", 0x200)

    def test_unreadable_pool_keeps_the_last_snapshot(self):
        st = self.scenario()
        before = net.elements(st)
        os.unlink(self.path("/etc/kaskad/egress/egress_parse.py"))
        exits._cache.clear()
        # no parser: the legs of the last check stay (kinds and marks come from the line numbers)
        self.assertEqual(net.elements(st), before)
        snap = exits.load_snapshot()
        for rec in snap["exits"].values():
            rec["alive"] = False
        exits.save_snapshot(snap)
        # ... and dead exits still fall to the fail policy, never to "direct" silently
        self.assertEqual(net.elements(st), {"10.77.1.10": 0x2ff, "10.77.1.11": 0x2ff, "10.77.1.12": 0x201,
                                            "10.77.3.10": 0x2ff})
        os.unlink(paths.egress_up())                                      # no pool at all: fail policy
        exits.save_snapshot({"at": 0, "pool": [], "exits": {}})
        self.assertEqual(net.elements(st), {"10.77.1.10": 0x2ff, "10.77.1.11": 0x2ff, "10.77.1.12": 0x201,
                                            "10.77.3.10": 0x2ff})


class ApplyTest(GwCase):
    def test_apply_creates_both_parts_in_one_transaction(self):
        st = self.scenario(forwards=[F443])
        res = nft.apply(st, self.w.runner)
        self.assertEqual(res["added"], ["tcp:443"])
        self.assertEqual(len([a for a in self.w.nft_writes()]), 1)
        t = self.w.nft.table()
        self.assertEqual(list(t["chains"]), ["fwd_pre", "fwd_dnat", "fwd_count", "fwd_post", "gw_pre", "gw_fwd",
                                             "gw_out", "gw_post", "gw_dns"])
        self.assertEqual(t["chains"]["gw_dns"]["hook"], {"type": "nat", "hook": "prerouting", "prio": -102,
                                                         "policy": "accept"})     # F2: before fwd_pre (-101)
        self.assertEqual(self.w.nft.rules("gw_dns"), __import__("tests.test_gw_f2", fromlist=["DNAT"]).DNAT)
        self.assertEqual(t["chains"]["gw_out"]["hook"], {"type": "filter", "hook": "output", "prio": -4,
                                                         "policy": "accept"})
        self.assertEqual(sorted(self.w.nft.elements("gw_live")), ["10.77.1.10", "10.77.1.11", "10.77.1.12",
                                                                  "10.77.3.10"])
        self.assertEqual(self.w.nft.elements("gw_nets"), ["10.77.0.0/16"])
        self.assertEqual(self.w.nft.elements("gw_priv"), list(net.PRIVATE))
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102, "10.77.1.11": 0x102,
                                                           "10.77.1.12": 0x301, "10.77.3.10": 0x102})
        self.assertEqual(self.w.nft.rules("gw_fwd6", "kaskad6", "ip6"), ['iifname "kgw-wg" drop',
                                                                        'iifname "kgw-awg2" drop'])
        self.assertEqual(self.w.nft.rules("gw_out6", "kaskad6", "ip6"), [])                 # no xray user yet
        self.assertEqual(nft.verify(st, nft.read_live(self.w.runner)), [])
        self.assertEqual(self.read("/etc/kaskad/net/kaskad.nft"), nft.render_full(st))

    def test_forward_changes_keep_the_gateway(self):
        self.scenario(forwards=[F443])
        nft.apply(state.load(), self.w.runner)
        self.w.nft.set_counter("fwd_tcp_443", 7, 700)
        before = copy.deepcopy(self.w.nft.table()["sets"])
        forwards.add_forward("udp", 51821, "198.51.100.7", 51820, runner=self.w.runner)
        t = self.w.nft.table()
        self.assertEqual(t["sets"], before)
        self.assertEqual(len(t["chains"]["gw_pre"]["rules"]), len(dict(net.rules(state.load()))["gw_pre"]))
        self.assertEqual(len(t["chains"]["gw_pre"]["rules"]), 26)          # wave 3: one rule became two; T-N5 №12: ping; stage 1: gw_ruonly; B1 (SEC-s1-t45 С-1): «0x2fe → блок» always; SEC-3372 З-1: the dnsmasq ports
        self.assertEqual(self.w.nft.counter("fwd_tcp_443"), {"packets": 7, "bytes": 700})
        self.assertIn("gw_route", self.read("/etc/kaskad/net/kaskad.nft"))
        forwards.flush_forwards(runner=self.w.runner)
        self.assertEqual(self.w.nft.forwards(), {})
        self.assertEqual(len(self.w.nft.elements("gw_route")), 4)
        self.assertIsNotNone(self.w.nft.table("kaskad6", "ip6"))

    def test_verify_finds_drift_and_apply_repairs_it(self):
        st = self.scenario()
        nft.apply(st, self.w.runner)
        live = nft.read_live(self.w.runner)
        self.w.nft.run_script("delete element ip kaskad gw_route { 10.77.1.10 }\n"
                              "add element ip kaskad gw_route { 10.77.1.10 : 0x201 }\n"
                              "flush chain ip kaskad gw_post\n", True)
        probs = nft.verify(st, nft.read_live(self.w.runner))
        self.assertTrue(any("gw_route" in p for p in probs), probs)
        self.assertTrue(any("gw_post" in p for p in probs), probs)
        self.assertNotEqual(live, nft.read_live(self.w.runner))
        nft.apply(st, self.w.runner)
        self.assertEqual(nft.verify(st, nft.read_live(self.w.runner)), [])

    def test_recreated_with_the_gateway_when_a_base_chain_was_changed(self):
        st = self.scenario(forwards=[F443])
        nft.apply(st, self.w.runner)
        self.w.nft.set_counter("fwd_tcp_443", 5, 500)
        self.w.nft.set_prio("gw_pre", 0)
        res = nft.apply(st, self.w.runner)
        self.assertTrue(res["recreated"])
        self.assertEqual(self.w.nft.table()["chains"]["gw_pre"]["hook"]["prio"], -150)
        self.assertEqual(self.w.nft.counter("fwd_tcp_443"), {"packets": 5, "bytes": 500})
        self.assertEqual(len(self.w.nft.elements("gw_route")), 4)

    def test_restore_brings_back_both_tables(self):
        st = self.scenario(forwards=[F443])
        nft.apply(st, self.w.runner)
        self.w.nft.flush_ruleset()
        self.assertTrue(nft.restore(self.w.runner))
        self.assertEqual(nft.verify(st, nft.read_live(self.w.runner)), [])
        self.assertEqual(len(self.w.nft.rules("gw_fwd6", "kaskad6", "ip6")), 2)

    def test_teardown_script_removes_only_the_gateway(self):
        st = self.scenario(forwards=[F443])
        nft.apply(st, self.w.runner)
        script = net.teardown_script(nft.read_live(self.w.runner))
        self.assertEqual(self.w.nft.run_script(script, True)[0], 0, script)
        t = self.w.nft.table()
        self.assertEqual(list(t["chains"]), ["fwd_pre", "fwd_dnat", "fwd_count", "fwd_post"])
        self.assertEqual(dict(t["sets"]), {})
        self.assertIsNone(self.w.nft.table("kaskad6", "ip6"))
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443)})
        # nothing left: the script still runs (ip6 add+delete), so teardown is idempotent
        self.assertEqual(self.w.nft.run_script(net.teardown_script(nft.read_live(self.w.runner)), True)[0], 0)
        self.assertEqual(self.w.nft.run_script(net.teardown_script(None), True)[0], 0)

    def test_delete_table_takes_the_ip6_table(self):
        st = self.scenario()
        nft.apply(st, self.w.runner)
        self.assertTrue(nft.delete_table(self.w.runner))
        self.assertIsNone(self.w.nft.table())
        self.assertIsNone(self.w.nft.table("kaskad6", "ip6"))

    def test_update_map_only_when_it_differs(self):
        st = self.scenario()
        nft.apply(st, self.w.runner)
        writes = len(self.w.nft_writes())
        self.assertFalse(net.update_map(self.w.runner, st))
        self.assertEqual(len(self.w.nft_writes()), writes)
        snap = exits.load_snapshot()
        snap["exits"][self.aid]["alive"] = False
        exits.save_snapshot(snap)
        self.assertTrue(net.update_map(self.w.runner, st))
        # the AmneziaWG exit died: stage 1 (SE §2.2, §1.3 differences 2 and 4) — the pool takes the live VLESS exit by
        # the device (0x301, not the balancer 0x200), «exit:<awg>» goes through another fit exit of its group (the
        # same VLESS one), the device routed to that VLESS exit keeps it
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x301, "10.77.1.11": 0x301,
                                                           "10.77.1.12": 0x301, "10.77.3.10": 0x301})
        self.assertEqual(self.w.nft_writes()[-1], ["nft", "-f", "-"])
        self.assertEqual(nft.verify(st, nft.read_live(self.w.runner)), [])

    def test_single_elements(self):
        st = self.scenario()
        nft.apply(st, self.w.runner)
        self.assertTrue(net.set_element(self.w.runner, st, "10.77.8.10", 0x200))
        self.assertTrue(net.set_element(self.w.runner, st, "10.77.8.10", 0x102))       # replaced, not EEXIST
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.8.10"], 0x102)
        self.assertTrue(net.del_element(self.w.runner, st, "10.77.8.10"))
        self.assertFalse(net.del_element(self.w.runner, st, "10.77.8.10"))
        with self.assertRaises(KaskadError):
            net.del_element(self.w.runner, st, "192.0.2.1")

    def test_ip6_problems_and_repair(self):
        st = self.scenario()
        self.assertEqual(len(net.ip6_problems(st, self.w.runner)), 1)
        nft.apply(st, self.w.runner)
        self.assertEqual(net.ip6_problems(st, self.w.runner), [])
        self.w.nft.drop_rule("gw_fwd6", 0, "kaskad6", "ip6")
        self.assertEqual(len(net.ip6_problems(st, self.w.runner)), 1)
        net.apply_ip6(self.w.runner, st)
        self.assertEqual(net.ip6_problems(st, self.w.runner), [])

    def test_probe_of_the_constructs(self):
        ok, err = net.probe(self.w.runner)
        self.assertTrue(ok, err)
        script = self.w.nft.checked[-1]
        self.assertTrue(script.startswith("table ip kaskad_gwprobe\ndelete table ip kaskad_gwprobe\n"))
        self.assertIn("tproxy to 127.0.0.1:12345", script)
        self.assertIn("table ip6 kaskad6_gwprobe", script)
        self.assertEqual(self.w.nft.tables, {})                                   # -c only
        self.w.nft.fail_check = "Error: Could not process rule: No such file or directory\n"
        ok, err = net.probe(self.w.runner)
        self.assertFalse(ok)
        self.assertIn("No such file", err)


BLOCK = [(4000, "from all fwmark 0x10000/0x10000 lookup main"), (4001, "from all to 10.77.0.0/16 lookup main"),
         (4002, "from all fwmark 0x200 lookup 200"), (4003, "from all fwmark 0x300/0xff00 lookup 200"),
         (4004, "from 10.77.0.0/16 fwmark 0/0xffff lookup 199")]


class RoutingTest(GwCase):
    def ours(self):
        """Our rules (not the kernel's three, not foreign ones) in the kernel's order."""
        return [r for r in self.w.ip.ordered() if net._classify(r[0], r[1], "10.77.0.0/16") is not None]

    def rule_calls(self, since=0):
        return [c for c in self.w.ip.calls[since:] if c[1:3] in (["rule", "add"], ["rule", "del"])]

    def wg_quick(self, table=51820):
        """``wg-quick up`` of a full tunnel (Table=auto) after the gateway: two rules WITHOUT a pref (the kernel
        puts each above everything) and the tunnel's table."""
        self.w.ip.add_link("wg0", "none", ["10.66.0.2/32"])
        self.w.ip.routes[str(table)] = ["default dev wg0 scope link"]
        self.w.ip.rules.append((self.w.ip.default_pref(), "not from all fwmark 0x%x lookup %d" % (table, table)))
        self.w.ip.rules.append((self.w.ip.default_pref(), "from all lookup main suppress_prefixlength 0"))

    def wg_quick_down(self, table=51820):
        self.w.ip.rules = [r for r in self.w.ip.rules if "lookup %d" % table not in r[1]
                           and "suppress_prefixlength" not in r[1]]
        self.w.ip.drop_link("wg0")

    def test_ensure_routing_is_idempotent(self):
        st = self.scenario()
        self.assertEqual(len(net.routing_problems(self.w.runner, st)), 10)
        done = net.ensure_routing(self.w.runner, st)
        self.assertIn("throw 10.77.0.0/16 table 200", done)            # TPROXY clients under src_valid_mark
        # the bypass for replies comes before the kill switch: under rp_filter (Ubuntu) the kernel checks the
        # source of a reply with the lookup «from <client> to <source>», which must not hit the kill switch
        self.assertEqual(done[0], "net.ipv4.conf.all.src_valid_mark=1")
        self.assertTrue(done[1].startswith("unreachable default"))
        self.assertEqual(done[2], "ip rule 4000 fwmark 0x10000/0x10000 lookup main")
        self.assertEqual(done[6], "ip rule 4004 from 10.77.0.0/16 fwmark 0x0/0xffff lookup 199")
        self.assertEqual(self.ours(), BLOCK)                             # one block of pinned prefs
        self.assertEqual(self.w.ip.routes["199"], ["unreachable default"])
        self.assertEqual(self.w.ip.routes["200"], ["local default dev lo scope host", "throw 10.77.0.0/16"])
        self.assertEqual(self.w.ip.links["kgw0"]["addrs"], ["10.77.0.1/32"])
        self.assertTrue(self.w.ip.links["kgw0"]["up"])
        self.assertEqual(net.routing_problems(self.w.runner, st), [])
        calls = len(self.w.ip.calls)
        self.assertEqual(net.ensure_routing(self.w.runner, st), [])
        self.assertFalse(any(c[1:3] in (["rule", "add"], ["rule", "del"], ["link", "add"])
                             for c in self.w.ip.calls[calls:]))
        # what the kernel decides through kgw0, class by class (the questions of the watchdog)
        d = lambda mark, dst="1.1.1.1": net.route_decision(self.w.runner, dst, "10.77.0.2", "kgw0", mark)
        self.assertEqual((d(0x200)["type"], d(0x200)["table"]), ("local", "200"))
        self.assertEqual((d(0x301)["type"], d(0x301)["table"]), ("local", "200"))
        self.assertEqual((d(0)["ok"], d(0)["type"]), (False, "unreachable"))            # the kill switch
        self.assertEqual((d(0x10000)["ok"], d(0x10000)["table"], d(0x10000)["dev"]), (True, "main", "eth0"))

    def test_kill_switch_follows_the_base_and_is_found_whatever_the_order(self):
        st = self.scenario()
        net.ensure_routing(self.w.runner, st)
        self.w.ip.rules.remove(BLOCK[4])                                 # someone deleted it
        probs = net.routing_problems(self.w.runner, st)
        self.assertEqual(len(probs), 1)
        self.assertIn("нет аварийного ip rule from 10.77.0.0/16", probs[0])
        net.ensure_routing(self.w.runner, st)
        self.assertEqual(self.ours(), BLOCK)
        # a leg brought up after the gateway: install.sh puts its rule into its slot (block + 4 + N) at once —
        # nothing for the watchdog to move, no «routing restored» after an exit is added (T-3379 З-2)
        self.w.script(["gokaskad", "--egress-awg-up"], None)
        self.assertEqual(self.ours(), BLOCK + [(4006, "from all fwmark 0x102 lookup 258")])
        self.assertEqual(net.routing_problems(self.w.runner, st), [])
        # a leg rule on top anyway (an older install.sh, a hand): the watchdog moves it into its slot
        self.w.ip.rules.remove((4006, "from all fwmark 0x102 lookup 258"))
        self.w.ip.rules.append((3999, "from all fwmark 0x102 lookup 258"))
        probs = net.routing_problems(self.w.runner, st)
        self.assertEqual(probs, ["правила Kaskad не на своих местах (leg:2): их место — pref 4000–4006"])
        net.ensure_routing(self.w.runner, st)
        self.assertEqual(self.ours(), BLOCK + [(4006, "from all fwmark 0x102 lookup 258")])
        self.assertEqual(net.routing_problems(self.w.runner, st), [])
        st["gateway"]["net"]["base"] = "10.78.0.0/16"                    # a new base
        probs = net.routing_problems(self.w.runner, st)
        self.assertTrue(any("прежней сети 10.77.0.0/16" in p for p in probs), probs)
        net.ensure_routing(self.w.runner, st)
        self.assertEqual([t for _, t in self.ours()][:5], [BLOCK[0][1], "from all to 10.78.0.0/16 lookup main",
                                                          BLOCK[2][1], BLOCK[3][1],
                                                          "from 10.78.0.0/16 fwmark 0/0xffff lookup 199"])
        self.assertFalse([r for r in self.w.ip.rules if "10.77.0.0/16" in r[1]])

    def test_wg_quick_full_tunnel_after_boot(self):
        """The finding: wg-quick (WARP) comes up after kaskad-net; its rules land above ours and take the clients'
        marked packets and the unmarked ones into wg0. The watchdog sees it in the kernel's decisions, moves the
        block right above the foreign rules, logs which rule was in the way — and touches nothing after."""
        st = self.scenario()
        net.ensure_routing(self.w.runner, st)
        self.w.leg(2)
        net.ensure_routing(self.w.runner, st)
        self.wg_quick()
        self.assertEqual(self.w.ip.ordered()[1:3], [(3998, "from all lookup main suppress_prefixlength 0"),
                                                    (3999, "not from all fwmark 0xca6c lookup 51820")])
        d = net.route_decision(self.w.runner, "1.1.1.1", "10.77.0.2", "kgw0", 0x200)
        self.assertEqual((d["table"], d["dev"]), ("51820", "wg0"))      # the pool's clients went into wg0
        hij = net.hijacks(self.w.runner, st)
        self.assertEqual(sorted(h["id"] for h in hij), ["base", "kill", "leg:2", "reply", "vless", "xray"])
        self.assertEqual([f["pref"] for f in hij[0]["culprits"]], [3999])          # the rule of table 51820
        probs = net.routing_problems(self.w.runner, st)
        self.assertIn("чужое правило «3999: not from all fwmark 0xca6c lookup 51820» стоит выше правил Kaskad: "
                      "пакеты пути через xray (метка 0x200) уходят в таблицу 51820 (dev wg0)", probs)
        net.ensure_routing(self.w.runner, st)
        block = [(p - 9, t) for p, t in BLOCK] + [(3997, "from all fwmark 0x102 lookup 258")]
        self.assertEqual(self.ours(), block)                            # 3991..3997: right above 3998
        self.assertEqual(net.hijacks(self.w.runner, st), [])
        self.assertEqual(net.routing_problems(self.w.runner, st), [])
        log = self.read("/var/log/kaskad.log")
        self.assertIn("GW routing: чужое правило «3999: not from all fwmark 0xca6c lookup 51820» стоит выше правил "
                      "Kaskad", log)
        self.assertIn("правила Kaskad переставлены выше (pref 3991–3997)", log)
        d = net.route_decision(self.w.runner, "1.1.1.1", "10.77.0.2", "kgw0", 0x200)
        self.assertEqual((d["type"], d["table"]), ("local", "200"))
        d = net.route_decision(self.w.runner, "1.1.1.1", "10.77.0.2", "kgw0", 0x102)
        self.assertEqual((d["table"], d["dev"]), ("258", "kawg2"))
        self.assertFalse(net.route_decision(self.w.runner, "1.1.1.1", "10.77.0.2", "kgw0", 0)["ok"])
        # the node's own traffic keeps wg-quick's order (suppress_prefixlength first): not our business, kept
        self.assertEqual(self.w.ip.ordered()[-4:-2], [(3998, "from all lookup main suppress_prefixlength 0"),
                                                      (3999, "not from all fwmark 0xca6c lookup 51820")])
        # nothing moves while the decisions are right
        calls = len(self.w.ip.calls)
        for _ in range(3):
            self.assertEqual(net.ensure_routing(self.w.runner, st), [])
        self.assertEqual(self.rule_calls(calls), [])
        # wg-quick down: the block goes back home; up again: above it again (no ratchet towards pref 1)
        self.wg_quick_down()
        self.assertTrue(net.routing_problems(self.w.runner, st)[0].startswith("правила Kaskad не на своих местах"))
        net.ensure_routing(self.w.runner, st)
        self.assertEqual(self.ours()[0], BLOCK[0])
        self.wg_quick()
        net.ensure_routing(self.w.runner, st)
        self.assertEqual(self.ours(), block)

    def test_tailscale_exit_node_stands_below_the_block(self):
        """Tailscale puts its rules at 5210–5270 (``lookup 52``: the exit node's default route): our block at 4000
        stands above them, the rules of 3.1.0 (31993–31999) stood below."""
        st = self.scenario()
        self.w.ip.add_link("tailscale0", "none", ["100.101.102.103/32"])
        self.w.ip.routes["52"] = ["default dev tailscale0"]
        self.w.ip.rules += [(5210, "from all fwmark 0x80000/0xff0000 lookup main"),
                            (5230, "from all fwmark 0x80000/0xff0000 lookup default"),
                            (5250, "from all fwmark 0x80000/0xff0000 unreachable"), (5270, "from all lookup 52")]
        net.ensure_routing(self.w.runner, st)
        self.assertEqual(self.ours(), BLOCK)
        self.assertEqual(net.hijacks(self.w.runner, st), [])
        self.assertEqual(net.routing_problems(self.w.runner, st), [])

    def test_rules_of_3_1_0_move_into_the_block(self):
        st = self.scenario()
        net.ensure_routing(self.w.runner, st)
        self.w.leg(2)
        net.ensure_routing(self.w.runner, st)
        # what 3.1.0 left: fixed 31995/31998/31999, 0x200 and 0x300 and the leg without a pref
        self.w.ip.rules = [r for r in self.w.ip.rules if r[0] in (0, 32766, 32767)] + [
            (31992, "from all fwmark 0x102 lookup 258"), (31993, "from all fwmark 0x300/0xff00 lookup 200"),
            (31994, "from all fwmark 0x200 lookup 200"), (31995, "from all fwmark 0x10000/0x10000 lookup main"),
            (31998, "from 10.77.0.0/16 to 10.77.0.0/16 lookup main"),
            (31999, "from 10.77.0.0/16 fwmark 0/0xffff lookup 199")]
        since = len(self.w.ip.calls)
        probs = net.routing_problems(self.w.runner, st)
        self.assertIn("ip rule 31998 from 10.77.0.0/16 to 10.77.0.0/16 lookup main остался от Kaskad 3.1.0", probs)
        self.assertIn("правила Kaskad не на своих местах (kill, leg:2, reply, vless, xray): их место — pref "
                      "4000–4006", probs)
        net.ensure_routing(self.w.runner, st)
        self.assertEqual(self.ours(), BLOCK + [(4006, "from all fwmark 0x102 lookup 258")])
        self.assertEqual(net.routing_problems(self.w.runner, st), [])
        # make before break: the kill switch was never gone (added at 4004 before 31999 was deleted)
        calls = [c[1:] for c in self.rule_calls(since)]
        self.assertLess(calls.index(["rule", "add", "pref", "4004", "from", "10.77.0.0/16", "fwmark", "0x0/0xffff",
                                     "lookup", "199"]),
                        calls.index(["rule", "del", "pref", "31999", "from", "10.77.0.0/16", "fwmark", "0x0/0xffff",
                                     "lookup", "199"]))

    def test_harmless_foreign_rules_are_left_alone(self):
        """strongSwan's ``220: lookup 220`` stands above the block and decides nothing for our classes: the block
        stays at 4000. A foreign rule INSIDE the block's prefs moves it right above that rule."""
        st = self.scenario()
        self.w.ip.rules.append((220, "from all lookup 220"))
        self.w.ip.routes["220"] = ["192.0.2.0/24 via 10.0.0.1 dev eth0"]
        net.ensure_routing(self.w.runner, st)
        self.assertEqual(self.ours(), BLOCK)
        self.assertEqual(net.routing_problems(self.w.runner, st), [])
        self.w.ip.rules.append((4002, "from 192.0.2.7 lookup 100"))
        self.assertEqual(net.routing_problems(self.w.runner, st),
                         ["правила Kaskad не на своих местах (base, kill, reply, vless, xray): их место — pref "
                          "3997–4001"])
        net.ensure_routing(self.w.runner, st)
        self.assertEqual(self.ours(), [(p - 3, t) for p, t in BLOCK])

    def test_a_rule_the_block_cannot_pass_is_told_not_fought(self):
        """A foreign catch-all at pref 0 (after «local»): nothing can stand above it. The watchdog does not move
        the block every round (the collector repairs what routing_problems says and logs each repair) —
        ``routing_conflicts`` tells it in ``gw check``."""
        st = self.scenario()
        net.ensure_routing(self.w.runner, st)
        self.w.ip.add_link("wg0", "none", ["10.66.0.2/32"])
        self.w.ip.routes["51820"] = ["default dev wg0 scope link"]
        self.w.ip.rules.append((0, "from all lookup 51820"))
        self.assertEqual(net.routing_problems(self.w.runner, st), [])
        calls = len(self.w.ip.calls)
        self.assertEqual(net.ensure_routing(self.w.runner, st), [])
        self.assertEqual(self.rule_calls(calls), [])
        conf = net.routing_conflicts(self.w.runner, st)
        self.assertEqual(len(conf), 5)
        self.assertIn("чужое правило «0: from all lookup 51820» стоит выше правил Kaskad: пакеты пути через xray "
                      "(метка 0x200) уходят в таблицу 51820 (dev wg0) — правила Kaskad выше уже не поставить",
                      "\n".join(conf))
        probs = ops.check(st, self.w.runner)
        self.assertEqual([p["kind"] for p in probs if p["kind"] == "gw_conflict"], ["gw_conflict"] * 5)

    def test_wrong_address_on_kgw0_is_replaced(self):
        st = self.scenario()
        net.ensure_routing(self.w.runner, st)
        self.w.ip.links["kgw0"]["addrs"] = ["10.78.0.1/32"]
        self.w.ip.links["kgw0"]["up"] = False
        self.assertEqual(len(net.routing_problems(self.w.runner, st)), 2)
        net.ensure_routing(self.w.runner, st)
        self.assertEqual(self.w.ip.links["kgw0"]["addrs"], ["10.77.0.1/32"])
        self.assertTrue(self.w.ip.links["kgw0"]["up"])

    def test_remove_routing(self):
        st = self.scenario()
        net.ensure_routing(self.w.runner, st)
        self.w.leg(2)                                                          # the leg's rule: install.sh's
        self.w.ip.rules.append((100, "from all fwmark 0x200 lookup 200"))       # a duplicate
        self.w.ip.rules.append((31999, "from 10.79.0.0/16 fwmark 0/0xffff lookup 199"))  # of an older base
        self.w.ip.rules.append((31998, "from 10.79.0.0/16 to 10.79.0.0/16 lookup main"))  # 3.1.0
        self.w.ip.rules.append((31999, "from all lookup 199"))                         # not ours: stays
        self.w.ip.routes["200"].append("10.9.9.0/24 dev eth0 scope link")             # not ours: stays
        res = net.remove_routing(self.w.runner)
        self.assertEqual(res, {"rules": 8, "dummy": True})       # the block (5), 0x200 again, the old base (2)
        self.assertNotIn("from all fwmark 0x200 lookup 200", self.w.ip.rule_texts())
        self.assertEqual(self.ours(), [(4006, "from all fwmark 0x102 lookup 258")])
        self.assertIn((31999, "from all lookup 199"), self.w.ip.rules)
        self.assertEqual(self.w.ip.routes["200"], ["10.9.9.0/24 dev eth0 scope link"])
        self.assertEqual(self.w.ip.routes["199"], [])
        self.assertNotIn("kgw0", self.w.ip.links)
        self.assertEqual(net.remove_routing(self.w.runner), {"rules": 0, "dummy": False})

    def test_failed_command_raises(self):
        st = self.scenario()
        self.w.ip.links["kgw0"] = {"idx": 99, "type": "dummy", "up": True, "addrs": []}
        orig = self.w.ip.__call__

        def broken(argv, inp):
            if argv[1:3] == ["addr", "add"]:
                return 2, "", "RTNETLINK answers: Operation not permitted\n"
            return orig(argv, inp)
        self.w.runner._handlers.insert(0, (["ip", "addr", "add"], broken))
        with self.assertRaises(KaskadError) as cm:
            net.ensure_routing(self.w.runner, st)
        self.assertIn("Operation not permitted", str(cm.exception))

    def test_the_kernels_default_pref(self):
        """The test double puts a rule without a pref where the kernel does: the pref of the SECOND rule minus 1,
        0 when that one is at 0 (and after the rules of the same pref)."""
        ip = self.w.ip
        self.assertEqual(ip.default_pref(), 32765)
        ip.rules.append((0, "from all lookup 7"))
        self.assertEqual(ip.default_pref(), 0)


class RpFilterTest(GwCase):
    """F1, finding 6: a strict reverse path check (CIS: net.ipv4.conf.all.rp_filter=1) silently cut the AmneziaWG
    exits. The test double of ``ip route get`` checks the source the way the kernel does (max(all, iface), the
    lookup «from <dst> to <src>» with the packet's mark under src_valid_mark)."""

    def setUp(self):
        super().setUp()
        self.st = self.scenario()
        net.ensure_routing(self.w.runner, self.st)
        self.w.ip.add_link("kgw-wg", "none", ["10.77.1.1/24"])            # the WireGuard inbound's interface
        self.w.ip.routes["main"].append("10.77.1.0/24 dev kgw-wg proto kernel scope link src 10.77.1.1")
        self.w.leg(2)
        rule = (4006, "from all fwmark 0x102 lookup 258")                # in its slot since T-3379 З-2; here — on top,
        self.w.ip.rules[self.w.ip.rules.index(rule)] = (3999, rule[1])   # as an older install.sh put it (no pref)
        v = self.w.sysctl.values                                         # CIS, and new interfaces strict too
        for k in ("all", "default", "kgw-wg", "kawg2", "kgw0"):
            v["net.ipv4.conf.%s.rp_filter" % k] = "1"

    def forward(self):
        """A client of kgw-wg routed to leg 2."""
        return net.route_decision(self.w.runner, "1.1.1.1", "10.77.1.10", "kgw-wg", 0x102)

    def reply(self):
        """A reply to that client coming in on kawg2 (gw_pre marks it 0x10000)."""
        return net.route_decision(self.w.runner, "10.77.1.10", "1.1.1.1", "kawg2", 0x10000)

    def test_strict_rp_filter_and_the_awg_exit(self):
        self.assertEqual((self.forward()["ok"], self.forward()["type"]), (False, "xdev"))
        self.assertEqual((self.reply()["ok"], self.reply()["type"]), (False, "xdev"))
        self.assertEqual(net.rp_problems(self.w.runner, self.st), [
            "строгая проверка источника (rp_filter=1, net.ipv4.conf.all.rp_filter=1) на kawg2, kgw-wg, kgw0: ответы "
            "выходов AmneziaWG и пакеты их клиентов режутся молча — нужна нестрогая (2) на интерфейсах шлюза, общая "
            "настройка не меняется"])
        probs = net.routing_problems(self.w.runner, self.st)
        self.assertIn("строгая проверка источника (rp_filter=1) на kgw0: сторож не спросит ядро о маршрутах клиентов",
                      probs)
        # the block alone (the leg's rule below «to <base>»): the client's own packets pass even a strict check
        net.place_rules(self.w.runner, self.st)
        self.assertEqual((self.forward()["table"], self.forward()["dev"]), ("258", "kawg2"))
        self.assertFalse(self.reply()["ok"])                               # the reply still needs kawg2 loose
        writes = len(self.w.sysctl.writes)
        done = net.ensure_rp(self.w.runner, self.st)
        self.assertEqual(done, ["net.ipv4.conf.kawg2.rp_filter=2", "net.ipv4.conf.kgw-wg.rp_filter=2",
                                "net.ipv4.conf.kgw0.rp_filter=2"])
        self.assertEqual(self.w.sysctl.writes[writes:], ["net.ipv4.conf.kawg2.rp_filter=2",
                                                         "net.ipv4.conf.kgw-wg.rp_filter=2",
                                                         "net.ipv4.conf.kgw0.rp_filter=2"])
        v = self.w.sysctl.values                                         # the admin's setting stays
        self.assertEqual((v["net.ipv4.conf.all.rp_filter"], v["net.ipv4.conf.default.rp_filter"]), ("1", "1"))
        self.assertTrue(self.reply()["ok"])
        self.assertEqual(self.reply()["dev"], "kgw-wg")
        self.assertEqual(net.rp_problems(self.w.runner, self.st), [])
        self.assertEqual(net.ensure_rp(self.w.runner, self.st), [])           # nothing twice

    def test_patterns_for_new_interfaces_and_the_watchdog(self):
        text = self.read(net.RPF_FILE)
        for line in ("net.ipv4.conf.kgw-*.rp_filter = 2", "net.ipv4.conf.kgw0.rp_filter = 2",
                     "net.ipv4.conf.kawg*.rp_filter = 2"):
            self.assertIn(line + "\n", text)
        self.assertNotIn("conf.all.", text.replace("net.ipv4.conf.all — ", ""))
        self.assertNotIn("default.rp_filter", text)
        self.assertEqual(os.stat(self.path(net.RPF_FILE)).st_mode & 0o777, 0o644)
        # a leg that came up strict (a renamed PPP interface too): gw check tells it, the watchdog sets it
        probs = ops.check(self.st, self.w.runner)
        p = [x for x in probs if x["kind"] == "gw_rp_filter"][0]
        self.assertIn("на kawg2, kgw-wg, kgw0:", p["detail"])
        ops.check(self.st, self.w.runner, repair=True)
        self.assertTrue(self.reply()["ok"])
        self.assertEqual([x for x in ops.check(self.st, self.w.runner) if x["kind"] in ("gw_rp_filter", "gw_routing")],
                         [])
        self.assertIn("строгая проверка источника (rp_filter=1) на kgw0", self.read("/var/log/kaskad.log"))
        net.remove_routing(self.w.runner)                                   # gw disable: the file goes
        self.assertFalse(os.path.exists(self.path(net.RPF_FILE)))
        self.assertEqual(self.w.sysctl.values["net.ipv4.conf.kawg2.rp_filter"], "2")  # the leg keeps working


class Ipv6OffTest(GwCase):
    """F1, finding 4 (its ip6 part): a kernel booted with ipv6.disable=1 has no IPv6 to close, and the ip6 table
    (gw_out6 uses conntrack) would take kaskad.nft down whole — the forwards with it — and make gw probe refuse."""

    def test_no_ip6_table_without_ipv6(self):
        self.write(net.IPV6_DISABLE, "1\n", 0o644)
        st = self.scenario(forwards=[F443])
        self.assertNotIn("ip6", nft.render_full(st))
        self.assertNotIn("kaskad6", nft.render_update(st, None))
        self.assertTrue(net.probe(self.w.runner)[0])
        self.assertNotIn("ip6", self.w.nft.checked[-1])
        nft.apply(st, self.w.runner)
        self.assertIsNone(self.w.nft.table("kaskad6", "ip6"))
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443)})
        self.assertEqual(net.ip6_problems(st, self.w.runner), [])
        self.assertEqual(nft.verify(st, nft.read_live(self.w.runner)), [])
        net.apply_ip6(self.w.runner, st)
        self.assertIsNone(self.w.nft.table("kaskad6", "ip6"))
        script = net.teardown_script(nft.read_live(self.w.runner))
        self.assertNotIn("ip6", script)
        self.assertEqual(self.w.nft.run_script(script, True)[0], 0, script)
        self.write(net.IPV6_DISABLE, "0\n", 0o644)                       # IPv6 back (another boot): the table too
        self.assertIn("table ip6 kaskad6 {", nft.render_full(st))


SS_REDSOCKS = 'tcp   LISTEN 0      128        127.0.0.1:12345     0.0.0.0:*    users:(("redsocks",pid=700,fd=5))\n'
SS_AVAHI = 'udp   UNCONN 0      0            0.0.0.0:5353      0.0.0.0:*    users:(("avahi-daemon",pid=650,fd=12))\n'


class TablesAndPortsTest(GwCase):
    """F1, finding 10: a route of someone else in table 199/200 is never replaced (``ip route replace local 0.0.0.0/0``
    took the place of an admin's ``default via`` in table 200 — replace compares prefix and metric, not the type),
    ``gw probe``/``gw enable`` refuse while the tables or the internal ports of xray are taken; the DNS of xray left
    mDNS's 5353."""

    def test_a_foreign_route_in_table_200_is_never_replaced(self):
        st = self.scenario()
        self.w.ip.routes["200"] = ["default via 192.0.2.1 dev eth0"]       # LARTC: the table of a second uplink
        done = net.ensure_routing(self.w.runner, st)
        self.assertEqual(self.w.ip.routes["200"], ["default via 192.0.2.1 dev eth0"])
        self.assertFalse([d for d in done if "table 200" in d])
        self.assertEqual(self.w.ip.routes["199"], ["unreachable default"])  # 199 is free: the kill switch is there
        probs = net.routing_problems(self.w.runner, st)
        self.assertFalse([p for p in probs if "таблице 200" in p], probs)  # nothing the collector would retry
        conf = net.routing_conflicts(self.w.runner, st)
        self.assertEqual(conf, ["в таблице 200 чужие маршруты: «default via 192.0.2.1 dev eth0» — таблицы 199 и 200 "
                                "нужны шлюзу Kaskad (выход через xray и аварийный выключатель): перенесите свои маршруты "
                                "и правила в другую таблицу (например, 201) или не включайте шлюз; Kaskad их не трогает"])
        self.assertEqual([p["kind"] for p in ops.check(st, self.w.runner) if p["kind"] == "gw_conflict"],
                         ["gw_conflict"])
        net.remove_routing(self.w.runner)                                 # gw disable takes only ours
        self.assertEqual(self.w.ip.routes["200"], ["default via 192.0.2.1 dev eth0"])
        self.assertNotIn(["ip", "route", "replace", "local", "0.0.0.0/0", "dev", "lo", "table", "200"],
                         self.w.ip.calls)
        # what the kernel answers a plain ``add`` there: EEXIST, whatever the type
        res = self.w.runner.run(["ip", "route", "add", "local", "0.0.0.0/0", "dev", "lo", "table", "200"])
        self.assertIn("File exists", res.err)

    def test_table_199_rules_and_names_of_someone_else(self):
        st = self.scenario()
        self.w.ip.routes["199"] = ["198.51.100.0/24 via 10.0.0.2 dev eth0"]
        net.ensure_routing(self.w.runner, st)
        self.assertEqual(self.w.ip.routes["199"], ["198.51.100.0/24 via 10.0.0.2 dev eth0"])   # no default added
        self.assertEqual(self.w.ip.routes["200"], ["local default dev lo scope host", "throw 10.77.0.0/16"])
        self.w.ip.rules.append((150, "from 192.0.2.5 lookup 200"))
        self.write("/etc/iproute2/rt_tables.d/isp.conf", "# second uplink\n200  isp2\n", 0o644)
        text = net.table_conflicts(self.w.runner, st)[0]
        self.assertIn("в таблице 199 чужие маршруты: «198.51.100.0/24 via 10.0.0.2 dev eth0»", text)
        self.assertIn("таблицу 200 ищет чужое правило «150: from 192.0.2.5 lookup 200»", text)
        self.assertIn("таблица 200 названа «isp2» в /etc/iproute2/rt_tables.d/isp.conf", text)
        self.assertEqual(net.table_names(), {200: ("isp2", "/etc/iproute2/rt_tables.d/isp.conf")})

    def test_probe_and_enable_refuse_with_what_to_do(self):
        save_state()
        self.assertEqual(ops.probe(self.w.runner)["code"], "")
        self.w.ip.rules.append((150, "from 192.0.2.5 lookup 200"))
        res = ops.probe(self.w.runner)
        self.assertEqual((res["ok"], res["code"]), (False, "gw_conflict"))
        self.assertIn("таблицу 200 ищет чужое правило «150: from 192.0.2.5 lookup 200»", res["reason"])
        self.assertIn("например, 201", res["reason"])
        with self.assertRaises(UnsupportedError) as cm:
            ops.enable(self.w.runner)
        self.assertIn("чужое правило", str(cm.exception))
        self.assertNotIn("kgw0", self.w.ip.links)                          # nothing was touched

    def test_internal_ports_of_xray(self):
        save_state()
        self.w.ss = SS_AVAHI                                              # mDNS on 5353: not ours any more
        self.assertEqual(xray.port_conflicts(self.w.runner), [])
        self.w.ss = SS_REDSOCKS + SS_AVAHI
        self.assertEqual(xray.port_conflicts(self.w.runner),
                         ["порт 127.0.0.1:12345/tcp (TPROXY пула, xray шлюза) занят: redsocks (pid 700) — освободите "
                          "его (остановите или перенастройте эту программу) и повторите"])
        res = ops.probe(self.w.runner)
        self.assertEqual((res["ok"], res["code"]), (False, "gw_conflict"))
        self.assertIn("redsocks (pid 700)", res["reason"])
        # held by xray of the gateway itself (its unit's MainPID): no conflict
        self.w.ss = SS_REDSOCKS.replace("redsocks", "xray").replace("pid=700", "pid=4242")
        self.w.sysd.units[xray.UNIT] = {"enabled": True, "active": True, "pid": 4242}
        self.assertEqual(xray.port_conflicts(self.w.runner), [])
        self.w.ss = SS_REDSOCKS.replace("127.0.0.1:12345", "[::1]:12345")   # ::1 is another address
        self.assertEqual(xray.port_conflicts(self.w.runner), [])
        self.w.ss = 'udp   UNCONN 0      0                  *:10853            *:*    users:(("dnsproxy",pid=9,fd=3))\n'
        self.assertIn("порт *:10853/udp (DNS шлюза", xray.port_conflicts(self.w.runner)[0])

    def test_vless_exit_ports_are_checked_too(self):
        st = self.scenario()
        port = exits.tproxy_port([e for e in exits.pool(self.w.runner) if e["kind"] == "xray"][0])
        self.w.ss = 'tcp   LISTEN 0      128          0.0.0.0:%d      0.0.0.0:*    users:(("nginx",pid=1,fd=6))\n' % port
        self.assertIn("порт 0.0.0.0:%d/tcp (выход VLESS %s" % (port, self.vid), xray.port_conflicts(self.w.runner, st)[0])

    def test_the_dns_of_xray_left_5353(self):
        self.assertEqual(xray.DNS_PORT, 10853)
        self.assertEqual(dns.POOL_SERVER, "127.0.0.1#%d" % xray.DNS_PORT)        # dnsmasq asks where xray listens
        st = self.scenario()
        cfg = xray.render(st, secrets.load(), exits.pool(self.w.runner))
        self.assertEqual([ib["port"] for ib in cfg["inbounds"] if ib["tag"] == "dns-in"], [10853])
        # 3.1.0 -> now: the next apply (install.sh runs gw apply after an update) moves both
        self.write("/etc/kaskad/gw/xray.json", json.dumps({"inbounds": [{"tag": "dns-in", "port": 5353}]}))
        self.write("/etc/kaskad/gw/dnsmasq.conf", "server=127.0.0.1#5353\n")
        self.with_xports()                                     # B3: the table of stage 1 before xray
        res = xray.apply(st, secrets.load(), self.w.runner)
        self.assertTrue(res["changed"])
        dns.apply(st, self.w.runner, via_pool=True)
        self.assertIn('"port": 10853', self.read("/etc/kaskad/gw/xray.json"))
        self.assertIn("server=127.0.0.1#10853\n", self.read("/etc/kaskad/gw/dnsmasq.conf"))
        self.assertNotIn("5353", self.read("/etc/kaskad/gw/dnsmasq.conf"))


class NetworkdDropinTest(GwCase):
    """systemd-networkd's 90-kaskad.conf: written with the gateway's policy routing (only where networkd is,
    only when missing or different, networkd never restarted), gone with the last of our policy routing."""
    DROPIN = "/etc/systemd/networkd.conf.d/90-kaskad.conf"

    def networkd(self):
        touch_exec(self.path("/usr/lib/systemd/systemd-networkd"))

    def networkd_calls(self):
        return [argv for argv, _ in self.w.runner.calls if any("networkd" in a for a in argv)]

    def test_no_networkd_no_file(self):
        st = self.scenario()
        done = net.ensure_routing(self.w.runner, st)
        self.assertTrue(done and not any("networkd" in d for d in done), done)
        self.assertFalse(os.path.lexists(paths.networkd_dropin()))

    def test_written_once_readable_by_networkd_and_never_a_restart(self):
        self.networkd()
        st = self.scenario()
        done = net.ensure_routing(self.w.runner, st)
        self.assertEqual(done[0], "systemd-networkd: чужие правила и маршруты не трогать (90-kaskad.conf)")
        text = self.read(self.DROPIN)
        self.assertEqual(text, gsys.NETWORKD_DROPIN)
        self.assertTrue(text.endswith("[Network]\nManageForeignRoutingPolicyRules=no\nManageForeignRoutes=no\n"))
        p = paths.networkd_dropin()
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o644)              # networkd runs as systemd-network
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(p)).st_mode), 0o755)
        ino = os.stat(p).st_ino
        self.assertEqual(net.ensure_routing(self.w.runner, st), [])              # the same text: not rewritten
        self.assertEqual(os.stat(p).st_ino, ino)
        self.write(self.DROPIN, "[Network]\nManageForeignRoutes=no\n", 0o600)   # different: rewritten
        self.assertEqual(len(net.ensure_routing(self.w.runner, st)), 1)
        self.assertEqual((self.read(self.DROPIN), stat.S_IMODE(os.stat(p).st_mode)), (gsys.NETWORKD_DROPIN, 0o644))
        self.assertEqual(self.networkd_calls(), [])                            # no restart, no reload
        self.assertIn("NETWORKD: %s записан" % p, self.read("/var/log/kaskad.log"))

    def test_released_only_when_nothing_of_ours_is_left(self):
        self.networkd()
        st = self.scenario()
        net.ensure_routing(self.w.runner, st)
        self.w.leg(2)                                                         # an egress leg is up
        net.remove_routing(self.w.runner)
        self.assertFalse(net.release_networkd(self.w.runner))
        self.w.kill_leg(2)                                                     # no interface, its rule stays
        self.assertFalse(net.release_networkd(self.w.runner))
        self.w.ip.rules = [r for r in self.w.ip.rules if "0x102" not in r[1]]
        self.w.ip.rules.append((90, "from all fwmark 0x200 lookup 200"))       # the gateway's rule
        self.assertFalse(net.release_networkd(self.w.runner))
        net.remove_routing(self.w.runner)
        self.assertTrue(net.release_networkd(self.w.runner))
        self.assertFalse(os.path.lexists(paths.networkd_dropin()))
        self.assertFalse(net.release_networkd(self.w.runner))                  # nothing to remove
        self.assertEqual(self.networkd_calls(), [])

    def test_install_sh_writes_the_same_text(self):
        """install.sh (egress legs, uninstall) and the gateway write one file: the texts must be equal, or each
        would rewrite the other's on every run."""
        with open(INSTALL_SH, encoding="utf-8") as f:
            m = re.search(r"^K3_NETWORKD_TEXT='([^']*)'$", f.read(), re.M)
        self.assertIsNotNone(m, "K3_NETWORKD_TEXT not found in install.sh")
        self.assertEqual(m.group(1) + "\n", gsys.NETWORKD_DROPIN)


class WaveOneUntouchedTest(GwCase):
    def test_foreign_gw_objects_stay_when_the_gateway_is_off(self):
        nft.apply(make_state([F443]), self.w.runner)
        self.w.nft.add_foreign_chain("gw_pre")
        nft.apply(make_state([F51820]), self.w.runner)
        self.assertIn("gw_pre", self.w.nft.table()["chains"])
