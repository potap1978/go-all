"""3.3.7.15 F11 — «Приём» по IPv6 (``addresses.in6``, цепочка ``gw_pre6`` в ``ip6 kaskad6``) — k3/handoff/SPEC-R1-3315-
2026-10-30.md §3 F11, PLAN-multi-ip-roles §9.2, §9.7.

What is checked (the «Тесты» of F11; the locks — tests/test_r15_locks.py; the rollback on the trees of 3.3.7.13/14 —
tests/test_r15_in6_rollback.py):
* ``in6 = null`` — no gw_pre6 anywhere: kaskad6 of the boot file, of the repair and of an apply as before;
* С-1 (the condition of F11): ``in6 = {"*": [X]}`` — ``render_full`` (the boot file kaskad.nft), the text of
  ``apply_ip6`` (the repair of the watchdog) and ``update_lines`` all hold gw_pre6 with its rule; ``ip6_problems``
  without gw_pre6 while a list is chosen — a problem, the repair draws it; a chain left at ``null`` — a problem, the
  repair removes it; the live chain is deleted by the next apply once ``in6`` is ``null``;
* ``{"*": []}`` — the rule without a list; a chosen IPv6 gone — still closed for the others; a new IPv6 not in the
  list takes nothing; a deprecated one in the list is in the set; SSH, 80, the forwards and ICMPv6 never in the rule;
  ``ipv6.disable=1`` — no table, no rule;
* the CLI ``gw addresses in6`` (and ``in --all`` takes ``in6`` off too); a copy of another server keeps ``in6`` of this
  machine; ``set_src`` of an IPv4 for an exit whose key leads to an IPv6 — refused (was written before and broke the
  exit);
* the panel: ``PUT /api/gw/addresses/in6`` and the old ``…/in``, ``…/exit`` without ``ver`` — 400, with an old one —
  409 and nothing written; a field of another name — 400."""

import json
import os
import re
import unittest
from unittest import mock

from kaskad import errors, nft, paths, state
from kaskad.gw import addresses as A
from kaskad.gw import exits, mip, net
from tests.fakes_nft import F443
from tests.panel.panelcase import PanelCase
from tests.test_gw_ops import CliGwCase
from tests.test_mip_s34 import A_IP, B_IP, leg_conf, two_addresses
from tests.test_r15_addr6 import doc6, with_ipv6

X6, Y6, OLD6, STATIC6 = "2001:db8:10::5", "2001:db8:10::7", "2001:db8:20::5", "2001:db8:30::a3f"
PRE6 = 'iifname != "lo" iifname != "kgw-*" ct direction original fib daddr type local meta l4proto . th dport'


def boot6():
    """The ip6 kaskad6 part of the boot file kaskad.nft."""
    text = open(paths.nft_file(), encoding="utf-8").read()
    i = text.find("table ip6 kaskad6 {")
    return text[i:] if i >= 0 else ""


def chain6(text, name="gw_pre6"):
    m = re.search(r"\n\tchain %s \{\n(.*?)\n\t\}" % re.escape(name), text, re.S)
    return [ln.strip() for ln in m.group(1).splitlines()[1:]] if m else None


class Node6(CliGwCase):
    """The scenario of tests/test_mip_s34.Node (wg 51820, awg2 41023, the forward of 443, VLESS_A + the leg kawg2) with
    the IPv6 of tests/test_r15_addr6 on eth0 (SLAAC, deprecated, static, stable-privacy), the gateway applied."""

    def setUp(self):
        super().setUp()
        doc6(self)
        two_addresses(self)
        self.scenario(forwards=[F443])
        self.w.leg(2)
        self.leg = [e for e in exits.pool(self.w.runner) if e["id"] == self.aid][0]["iface"]
        leg_conf(self.leg)
        with_ipv6(self)
        self.ok("gw", "host", A_IP)
        self.ok("gw", "apply")

    def live6(self, name="gw_pre6"):
        """The rules of a chain of the live ip6 kaskad6 (the fake nft), None — no such chain."""
        t = self.w.nft.tables6.get("kaskad6")
        if not t or name not in t["chains"]:
            return None
        return [r["text"] for r in t["chains"][name]["rules"]]

    def in6(self, *args, env=None):
        with mock.patch.dict(os.environ, env or {}, clear=False):
            return self.run_json("gw", "addresses", "in6", *args)


class NullTest(Node6):
    def test_null_draws_no_chain(self):
        self.assertNotIn("gw_pre6", open(paths.nft_file(), encoding="utf-8").read())
        self.assertIsNone(self.live6())
        st = state.load()
        self.assertEqual(net.pre6_rules(st), [])
        self.assertEqual(net._table6_text("kaskad6", ["x"], ["y"], net.pre6_rules(st)), net._table6_text("kaskad6", ["x"],
                                                                                                         ["y"]))
        self.assertNotIn("gw_pre6", "\n".join(net.update_lines(st, nft.read_live(self.w.runner))))
        self.assertEqual(net.ip6_problems(st, self.w.runner), [])


class ChainTest(Node6):
    def test_s1_the_boot_file_the_repair_and_the_apply(self):
        """С-1: a list of IPv6 — gw_pre6 in every text that makes kaskad6."""
        rc, doc, _ = self.in6(X6)
        self.assertEqual(rc, 0, doc)
        self.assertEqual(state.load()["gateway"]["addresses"]["in6"], {"*": [X6]})
        st = state.load()
        rule = PRE6 + " { udp . 41023, udp . 51820 } ip6 daddr != { %s } drop" % X6
        self.assertEqual(net.pre6_rules(st), [rule])
        self.assertEqual(chain6(boot6()), [rule])                               # the boot file (render_full)
        self.assertEqual(chain6(nft.render_full(st)[nft.render_full(st).find("table ip6 kaskad6 {"):]), [rule])
        self.assertEqual(self.live6(), [rule])                                 # the apply (update_lines)
        lines = net.update_lines(st, nft.read_live(self.w.runner))
        self.assertIn("add chain ip6 kaskad6 gw_pre6 { %s }" % net.HOOK6_PRE, lines)
        self.assertEqual(lines[-1], "add rule ip6 kaskad6 gw_pre6 " + rule)
        got = []
        with mock.patch.object(self.w.runner, "run", side_effect=lambda argv, input=None, **kw: got.append(input) or
                               type("R", (), {"ok": True, "out": "", "err": "", "rc": 0})()):
            net.apply_ip6(self.w.runner, st)                                    # the repair of the watchdog
        self.assertEqual(chain6(got[0]), [rule])
        self.assertIn("\tchain gw_pre6 {\n\t\t" + net.HOOK6_PRE, got[0])
        self.assertEqual(net.ip6_problems(st, self.w.runner), [])           # one address: a plain value in nft -j
        self.in6(X6, Y6)                                                    # two: a set
        self.assertEqual(net.ip6_problems(state.load(), self.w.runner), [])

    def test_s1_the_watchdog_sees_a_missing_chain_and_repairs_it(self):
        self.in6(X6)
        st = state.load()
        self.w.nft.run_script("flush chain ip6 kaskad6 gw_pre6\ndelete chain ip6 kaskad6 gw_pre6\n", True)   # a reboot
        self.assertIsNone(self.live6())                                        # with an old file, by hand…
        probs = net.ip6_problems(st, self.w.runner)
        self.assertEqual(len(probs), 1)
        self.assertIn("gw_pre6", probs[0])
        net.apply_ip6(self.w.runner, st)
        self.assertEqual(len(self.live6()), 1)
        self.assertEqual(net.ip6_problems(st, self.w.runner), [])
        # another list in the live chain (a rule by hand): a problem too
        self.w.nft.run_script("flush chain ip6 kaskad6 gw_pre6\nadd rule ip6 kaskad6 gw_pre6 %s { tcp . 443 } ip6 daddr != "
                              "{ %s } drop\n" % (PRE6, Y6), True)
        self.assertEqual(len(net.ip6_problems(st, self.w.runner)), 1)

    def test_back_to_null_deletes_the_live_chain(self):
        self.in6(X6)
        self.assertIsNotNone(self.live6())
        rc, doc, _ = self.in6("--all")
        self.assertEqual(rc, 0, doc)
        self.assertNotIn("addresses", state.load()["gateway"])
        self.assertIsNone(self.live6())                                        # update_lines: flush + delete chain
        self.assertNotIn("gw_pre6", open(paths.nft_file(), encoding="utf-8").read())
        # a chain left at null (a rollback did it) — the watchdog sees it and the repair takes it off
        self.w.nft.run_script("add chain ip6 kaskad6 gw_pre6 { %s }\nadd rule ip6 kaskad6 gw_pre6 %s { tcp . 443 } drop\n"
                              % (net.HOOK6_PRE, PRE6), True)
        st = state.load()
        self.assertEqual(len(net.ip6_problems(st, self.w.runner)), 1)
        net.apply_ip6(self.w.runner, st)
        self.assertIsNone(self.live6())

    def test_none_gone_new_deprecated(self):
        rc, doc, _ = self.in6("--none")
        self.assertEqual((rc, state.load()["gateway"]["addresses"]["in6"]), (0, {"*": []}), doc)
        self.assertEqual(self.live6(), [PRE6 + " { udp . 41023, udp . 51820 } drop"])   # no list: none takes
        self.assertIn("ни на одном адресе", doc["text"])
        self.in6(OLD6)                                                         # deprecated: may be chosen, in the set
        self.assertTrue(self.live6()[0].endswith("ip6 daddr != { %s } drop" % OLD6))
        self.in6(X6)
        with_ipv6(self, lines="")                                              # X gone from the machine
        A.collect(self.w.runner, net=False)
        self.ok("gw", "apply")
        self.assertTrue(self.live6()[0].endswith("ip6 daddr != { %s } drop" % X6))   # the others stay closed
        out, _ = self.ok("gw", "addresses")
        self.assertIn("Выбранных IPv6 (%s) на сервере сейчас нет" % X6, out)

    def test_never_ssh_80_forwards_icmpv6(self):
        with mock.patch.object(mip, "sshd_ports", lambda: {2222}):
            with mock.patch.dict(os.environ, {"SSH_CONNECTION": "2001:db8:99::1 50000 %s 2222" % X6}):
                self.in6(Y6)
            rule = self.live6()[0]
        ports = re.search(r"th dport \{ (.*?) \}", rule).group(1).split(", ")
        self.assertEqual(ports, ["udp . 41023", "udp . 51820"])    # the inbounds only (443 — the forward)
        self.assertFalse({"tcp . 22", "tcp . 2222", "tcp . 80"} & set(ports))   # SSH (its port), 80 — never
        self.assertNotIn("icmp", rule)                                         # tcp/udp ports only: ICMPv6 passes

    def test_ipv6_off_no_table_no_rule(self):
        self.in6(X6)
        with mock.patch.object(net, "ipv6_off", lambda: True):
            st = state.load()
            self.assertEqual(net.SECTION.full_extra(st), "")
            self.assertFalse([x for x in net.update_lines(st, nft.read_live(self.w.runner)) if "ip6" in x])
            self.assertEqual(net.ip6_problems(st, self.w.runner), [])


class CliTest(Node6):
    def test_the_command_and_its_refusals(self):
        rc, doc, _ = self.in6()
        self.assertEqual(rc, errors.USAGE)
        rc, doc, _ = self.in6(X6, "--none")
        self.assertEqual(rc, errors.USAGE)
        rc, doc, _ = self.in6("203.0.113.5")
        self.assertEqual((rc, "IPv6" in doc["error"]), (errors.USAGE, True))
        rc, doc, _ = self.in6("2001:db8:77::1")                                # not on this machine
        self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, "address_not_here"))
        self.assertIn(X6, doc["error"])
        self.assertIn(mip.BACK, doc["error"])
        rc, doc, _ = self.in6("[2001:DB8:10:0::5]," + Y6)                       # canonical, brackets, commas
        self.assertEqual((rc, state.load()["gateway"]["addresses"]["in6"]), (0, {"*": [X6, Y6]}), doc)
        out, _ = self.ok("gw", "addresses")
        self.assertIn("Приём по IPv6: только на %s, %s" % (X6, Y6), out)
        self.assertIn("SSH-меню 17 → 4 → «Приём подключений» → 1", out)

    def test_in_all_takes_in6_off_too(self):
        self.ok("gw", "addresses", "in", A_IP)
        self.in6(X6)
        g = state.load()["gateway"]["addresses"]
        self.assertEqual((g["in"], g["in6"]), ({"*": [A_IP]}, {"*": [X6]}))
        self.ok("gw", "addresses", "in", A_IP)                                 # a list of IPv4 keeps IPv6 as it is
        self.assertEqual(state.load()["gateway"]["addresses"]["in6"], {"*": [X6]})
        self.ok("gw", "addresses", "in", "--all")
        self.assertNotIn("addresses", state.load()["gateway"])
        self.assertIsNone(self.live6())

    def test_hidden_from_mcp(self):
        a = json.load(open(os.path.join(os.path.dirname(mip.__file__), "..", "mcp", "actions.json"), encoding="utf-8"))
        self.assertIn("gw addresses in6", a["hidden"])
        self.assertFalse([t for t, v in a["actions"].items() if v.get("cmd") == ["gw", "addresses", "in6"]])


class SrcFamilyTest(Node6):
    def test_an_ipv4_for_an_exit_over_ipv6_is_refused(self):
        pool = exits.pool(self.w.runner, roles=exits.ROLES)
        e = [x for x in pool if x["id"] == self.vid][0]
        with mock.patch.object(mip, "exit_v6", lambda x: "2001:db8:5::1" if x["id"] == self.vid else None):
            rc, doc, _ = self.run_json("gw", "addresses", "exit", self.vid, B_IP)
            self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, "src_family"), doc)
            self.assertIn("подключается по IPv6 (2001:db8:5::1)", doc["error"])
            self.assertNotIn("src", state.load()["gateway"]["exits"].get(self.vid, {}))
            self.ok("gw", "addresses", "exit", self.vid, "main")               # by the route — always
        self.assertIsNone(mip.exit_v6(e))                                      # VLESS_A leads to an IPv4

    def test_exit_v6_reads_the_key(self):
        self.assertEqual(mip.exit_v6({"kind": "xray", "outbound": {"settings": {"vnext": [{"address": "2001:DB8:5::1"}]}},
                                      "host": ""}), "2001:db8:5::1")
        self.assertEqual(mip.exit_v6({"kind": "xray", "outbound": None, "host": "[2001:db8:5::1]:443"}), "2001:db8:5::1")
        self.assertIsNone(mip.exit_v6({"kind": "xray", "outbound": None, "host": "vpn.example.com:443"}))
        self.assertIsNone(mip.exit_v6({"kind": "xray", "outbound": None, "host": "198.51.100.7"}))


class PanelVerTest(PanelCase):
    def setUp(self):
        super().setUp()
        doc6(self)
        self.gateway()
        with_ipv6(self)

    def test_ver_is_required_and_checked(self):
        doc = self.ok("GET", "/api/gw/addresses")
        ver = doc["ver"]
        for path, body in (("/api/gw/addresses/in6", {"ips": [X6]}), ("/api/gw/addresses/in", {"all": True}),
                           ("/api/gw/addresses/exit", {"id": self.vid, "ip": "main"})):
            self.assertEqual(self.err(400, "PUT", path, body)["code"], "ver_required", path)
            self.assertEqual(self.err(409, "PUT", path, dict(body, ver="0" * 12))["code"], "object_changed", path)
        self.assertNotIn("addresses", state.load()["gateway"])
        got = self.ok("PUT", "/api/gw/addresses/in6", {"ips": [X6], "ver": ver})
        self.assertEqual(got["in6"], [X6])
        self.assertNotEqual(got["ver"], ver)
        self.assertEqual(got["ver"], self.ok("GET", "/api/gw/addresses")["ver"])
        self.assertEqual(self.err(409, "PUT", "/api/gw/addresses/in6", {"none": True, "ver": ver})["code"],
                         "object_changed")                                     # the old ver: another window wrote
        self.assertEqual(state.load()["gateway"]["addresses"]["in6"], {"*": [X6]})
        self.assertEqual(self.err(400, "PUT", "/api/gw/addresses/in6", {"ips": [X6], "ver": got["ver"], "x": 1})["code"],
                         "field")
        self.ok("PUT", "/api/gw/addresses/in6", {"all": True, "ver": got["ver"]})
        self.assertNotIn("addresses", state.load()["gateway"])


class BackupTest(unittest.TestCase):
    def test_in6_of_a_copy_stays_on_its_machine(self):
        """SPEC-R1 F11 «Данные»: a copy of another server — ``addresses`` of this machine (``in6`` of the copy goes,
        this one's stays); a copy of this very server — as it was."""
        from kaskad import backup
        g_file = {"addresses": {"in": {"*": [B_IP]}, "in6": {"*": [X6]}}}
        now = {"gateway": {"addresses": {"in6": {"*": [Y6]}}}}
        same = json.loads(json.dumps(g_file))
        backup._addresses_of_this_machine(same, now, True)
        self.assertEqual(same, g_file)
        other = json.loads(json.dumps(g_file))
        backup._addresses_of_this_machine(other, now, False)
        self.assertEqual(other["addresses"], {"in6": {"*": [Y6]}})
        backup._addresses_of_this_machine(other, {"gateway": {}}, False)
        self.assertNotIn("addresses", other)
