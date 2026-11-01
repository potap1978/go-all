"""migrate rollback --to-legacy (SPEC §4.9): the rules must be exactly what Kaskad PRO 2.13's
apply_iptables_rules creates, so 2.13 sees and manages them again."""

import copy
import re

from kaskad import errors, state
from kaskad.errors import KaskadError
from kaskad.migrate import apply as apply_mod
from kaskad.migrate import rollback
from tests.fakes_ipt import FakeIptables
from tests.helpers import RootCase
from tests.migrate import scripts
from tests.migrate.env import Env


def get_rules_list_213(emu):
    """2.13 get_rules_list: grep DNAT + kaskad: in nat PREROUTING, sed out port/proto/dest."""
    out = []
    for line in emu.list_S("nat", "PREROUTING").splitlines():
        if "DNAT" not in line or "kaskad:" not in line:
            continue
        port = re.search(r".*--dport ([0-9]*)", line)
        proto = re.search(r".*-p ([a-z]*)", line)
        dest = re.search(r".*--to-destination ([0-9.:]*)", line)
        if port and port.group(1):
            out.append("%s|%s|%s" % (proto.group(1), port.group(1), dest.group(1)))
    return out


class RollbackTest(RootCase):
    def migrated(self, scenario="pro_213", **kw):
        env = Env(self, scenario, **kw).start()
        apply_mod.run(env.runner)
        self.assertEqual(state.load()["engine"], "nft")
        return env

    def test_rules_are_exactly_213_format(self):
        env = self.migrated()
        # reference: the iptables of the server without the compat rule, then 2.13 adds every forward
        ref = FakeIptables().load(env.ipt.save_text())
        ref.cmd("-D", *env.compat.ARGS)
        for f in state.load()["forwards"]:
            scripts.pro213_add(ref, f["proto"], f["port"], f["to_port"], f["to"], iface="eth0")
        res = rollback.run(env.runner)
        self.assertEqual(res["result"], "ok")
        for t in ("nat", "filter"):
            self.assertEqual(env.ipt.list_S(t), ref.list_S(t), t)
        self.assertEqual(env.runner.unmatched, [])

    def test_213_sees_and_manages_the_rules(self):
        env = self.migrated()
        rollback.run(env.runner)
        self.assertEqual(sorted(get_rules_list_213(env.ipt)),
                         sorted(["tcp|443|203.0.113.5:443", "tcp|8443|198.51.100.7:443",
                                 "udp|51820|198.51.100.7:51820"]))
        scripts.remove_rules_for_port(env.ipt, "tcp", 443)
        self.assertNotIn("kaskad:443:tcp", env.ipt.save_text())
        self.assertIn("kaskad:8443:tcp", env.ipt.save_text())

    def test_engine_off_and_order(self):
        env = self.migrated()
        n = len(env.events)
        res = rollback.run(env.runner)
        ev = [e[0] for e in env.events[n:]]
        self.assertLess(ev.index("iptables-restore"), ev.index("nft.delete_table"))
        self.assertLess(ev.index("nft.delete_table"), ev.index("compat.remove"))
        self.assertLess(ev.index("compat.remove"), ev.index("units.remove"))
        self.assertIsNone(env.nft.table)
        self.assertFalse(env.units.installed)
        self.assertFalse(env.compat.present(None))
        st = state.load()
        self.assertEqual(st["engine"], "legacy")
        self.assertEqual(len(st["forwards"]), 3)                      # kept for reference
        self.assertEqual((st["migrations"][-1]["kind"], st["migrations"][-1]["result"]), ("rollback", "ok"))
        self.assertEqual(res["saved_by"], "netfilter-persistent")
        self.assertIn("kaskad:443:tcp", self.read("/etc/iptables/rules.v4"))
        self.assertIn("ROLLBACK to iptables: 3 forwards", self.read("/var/log/kaskad.log"))

    def test_masquerade_added_once_with_detected_interface(self):
        env = self.migrated("free_v10")
        rollback.run(env.runner)
        post = env.ipt.rules("nat", "POSTROUTING")
        self.assertEqual(post, ["-A POSTROUTING -o eth0 -j MASQUERADE",
                                '-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])

    def test_masquerade_not_duplicated(self):
        for sc in ("foreign", "pro_213"):        # kaskad:masq is kept by the migration
            self.tearDown()
            self.setUp()
            env = self.migrated(sc)
            self.assertEqual(len([r for r in env.ipt.rules("nat", "POSTROUTING") if "kaskad:masq" in r]), 1, sc)
            rollback.run(env.runner)
            masq = [r for r in env.ipt.rules("nat", "POSTROUTING") if "kaskad:masq" in r]
            self.assertEqual(len(masq), 1, sc)

    def test_stale_labeled_rules_are_replaced_not_duplicated(self):
        env = self.migrated()
        scripts.pro213_add(env.ipt, "tcp", 443, 443, "192.0.2.99")      # 2.x run on top meanwhile
        rollback.run(env.runner)
        self.assertEqual([l for l in get_rules_list_213(env.ipt) if l.startswith("tcp|443|")],
                         ["tcp|443|203.0.113.5:443"])
        self.assertNotIn("192.0.2.99", env.ipt.save_text())

    def test_warns_about_earlier_unlabeled_dnat(self):
        env = self.migrated()
        scripts.free_add(env.ipt, "tcp", 443, 443, "192.0.2.9")
        res = rollback.run(env.runner)
        self.assertTrue(any("tcp :443" in w and "сработает оно" in w for w in res["warnings"]))

    def test_warns_about_dnat_inside_a_jumped_chain(self):
        env = self.migrated()
        e = env.ipt
        e.cmd("-t", "nat", "-N", "DOCKER")
        e.cmd("-t", "nat", "-A", "DOCKER", "!", "-i", "docker0", "-p", "tcp", "--dport", "8443", "-j", "DNAT",
              "--to-destination", "172.17.0.2:443")
        # published on 127.0.0.1 only: never sees traffic from outside, no warning for :443
        e.cmd("-t", "nat", "-A", "DOCKER", "-d", "127.0.0.1/32", "!", "-i", "docker0", "-p", "tcp", "--dport", "443",
              "-j", "DNAT", "--to-destination", "172.17.0.3:443")
        e.cmd("-t", "nat", "-A", "PREROUTING", "-m", "addrtype", "--dst-type", "LOCAL", "-j", "DOCKER")
        res = rollback.run(env.runner)
        mine = [w for w in res["warnings"] if "раньше правила Kaskad" in w]
        self.assertEqual(len(mine), 1, res["warnings"])
        self.assertIn("tcp :8443: раньше правила Kaskad стоит DOCKER: ", mine[0])
        self.assertIn("172.17.0.2:443", mine[0])

    def test_round_trip_keeps_names(self):
        env = self.migrated()
        before = copy.deepcopy(state.load()["forwards"])
        rollback.run(env.runner)
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        after = state.load()["forwards"]
        self.assertEqual([(f["proto"], f["port"], f["to"], f["to_port"], f["name"], f["added"]) for f in after],
                         [(f["proto"], f["port"], f["to"], f["to_port"], f["name"], f["added"]) for f in before])

    def test_without_netfilter_persistent(self):
        env = self.migrated(netfilter_persistent=False)
        res = rollback.run(env.runner)
        self.assertEqual(res["saved_by"], "")
        self.assertTrue(any("не обновлены" in w for w in res["warnings"]))

    def test_without_netfilter_persistent_and_without_a_rules_file(self):
        env = self.migrated(netfilter_persistent=False)
        import os
        os.unlink(self.path("/etc/iptables/rules.v4"))
        res = rollback.run(env.runner)
        self.assertEqual(res["saved_by"], "")
        self.assertIn("Правила iptables не сохранены: нет netfilter-persistent. После перезагрузки пробросов не будет "
                      "— их вернёт переезд на nftables (меню 16 → 1).", res["warnings"])

    def test_engine_legacy_nothing_to_do(self):
        env = Env(self, "pro_213").start()
        res = rollback.run(env.runner)
        self.assertEqual(res["result"], "nothing")
        self.assertEqual(env.events, [])

    def test_failure_keeps_nft_and_restores_nat(self):
        env = self.migrated()
        before_nat = env.ipt.list_S("nat")
        env.ipt.inject(lambda argv, inp: (1, "", "line 4 failed\n")
                       if argv[0] == "iptables-restore" and "--noflush" in argv and inp.startswith("*filter") else None)
        with self.assertRaises(KaskadError) as cm:
            rollback.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ERROR)
        self.assertIn("через nftables", str(cm.exception))
        self.assertEqual(env.ipt.list_S("nat"), before_nat)
        self.assertIsNotNone(env.nft.table)
        self.assertEqual(state.load()["engine"], "nft")

    def test_no_interface(self):
        env = self.migrated()
        env.runner._handlers.insert(0, (["ip", "route", "get", "8.8.8.8"], (2, "", "RTNETLINK answers: Network is unreachable\n")))
        with self.assertRaises(KaskadError):
            rollback.run(env.runner)
        self.assertIsNotNone(env.nft.table)

    def test_no_iptables(self):
        st = state.empty()
        st["engine"] = "nft"
        state.save(st)
        env = Env(self, None, iptables=False).start()
        with self.assertRaises(KaskadError) as cm:
            rollback.run(env.runner)
        self.assertEqual(cm.exception.code, errors.UNSUPPORTED)

    def test_legacy_lines_shape(self):
        nat_lines, filt_lines = rollback.legacy_lines(
            [{"proto": "udp", "port": 51820, "to": "198.51.100.7", "to_port": 51820}], "ens3",
            {"POSTROUTING": [], "PREROUTING": []}, {})
        self.assertEqual(nat_lines, [
            "-A PREROUTING -p udp --dport 51820 -m comment --comment kaskad:51820:udp -j DNAT --to-destination 198.51.100.7:51820",
            "-A POSTROUTING -o ens3 -m comment --comment kaskad:masq -j MASQUERADE"])
        self.assertEqual(filt_lines, [
            "-I INPUT -p udp --dport 51820 -m comment --comment kaskad:51820:udp -j ACCEPT",
            "-I FORWARD -p udp -d 198.51.100.7 --dport 51820 -m state --state NEW,ESTABLISHED,RELATED -m comment --comment kaskad:51820:udp -j ACCEPT",
            "-I FORWARD -p udp -s 198.51.100.7 --sport 51820 -m state --state ESTABLISHED,RELATED -m comment --comment kaskad:51820:udp -j ACCEPT"])
