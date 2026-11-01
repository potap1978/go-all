"""The iptables emulator against reality: it must print exactly what iptables-nft 1.8.9/1.8.10
printed on the live servers (real captures), and running the real 2.13 / free v1.1 rule functions
on it must give the same rule sets the stand produced."""

import os
import re
import unittest

from tests.fakes_ipt import FakeIptables, ipt_tokenize, save_string
from tests.migrate import scripts
from tests.migrate.env import FIX, fixture


def _rules_only(text):
    out = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        out.append(re.sub(r" \[\d+:\d+\]$", "", line))
    return out


class FidelityTest(unittest.TestCase):
    def test_every_fixture_round_trips(self):
        for d in sorted(os.listdir(FIX)):
            text = fixture(d, "save.txt")
            if text is None:
                continue
            emu = FakeIptables().load(text)
            self.assertEqual(_rules_only(emu.save_text()), _rules_only(text), d)

    def test_real_S_output_byte_exact(self):
        for d in ("real-free-v1.1", "real-pro213-over-free", "real-pro23-ufw"):
            emu = FakeIptables().load(fixture(d, "save.txt"))
            for t in ("nat", "filter"):
                self.assertEqual(emu.list_S(t), fixture(d, "S-%s.txt" % t), "%s %s" % (d, t))

    def test_real_free_v11_menu_session(self):
        """Coordinator's stand: free v1.1 driven through its menu (udp 51820 twice)."""
        emu = FakeIptables()
        for proto, inp, outp, tgt in (("udp", 51820, 51820, "198.51.100.10"), ("tcp", 443, 443, "198.51.100.20"),
                                      ("tcp", 8443, 443, "198.51.100.20"), ("udp", 51820, 51820, "198.51.100.30")):
            scripts.free_add(emu, proto, inp, outp, tgt, iface="enp0s3")
        for t in ("nat", "filter"):
            self.assertEqual(emu.list_S(t), fixture("real-free-v1.1", "S-%s.txt" % t), t)

    def test_pro213_function_gives_fixture(self):
        emu = FakeIptables()
        for proto, inp, outp, tgt in (("udp", 51820, 51820, "198.51.100.7"), ("tcp", 443, 443, "203.0.113.5"),
                                      ("tcp", 8443, 443, "198.51.100.7")):
            scripts.pro213_add(emu, proto, inp, outp, tgt)
        self.assertEqual(_rules_only(emu.save_text()), _rules_only(fixture("pro_213", "save.txt")))

    def test_free_then_pro211_gives_fixture(self):
        emu = FakeIptables()
        scripts.free_add(emu, "tcp", 443, 443, "192.0.2.9")
        scripts.free_add(emu, "udp", 51820, 51820, "198.51.100.7")
        scripts.pro213_add(emu, "tcp", 443, 443, "203.0.113.5")       # 2.11 makes the same rules
        scripts.pro213_add(emu, "tcp", 8443, 443, "198.51.100.7")
        self.assertEqual(_rules_only(emu.save_text()), _rules_only(fixture("pro_211_over_free", "save.txt")))

    def test_free_two_rules_one_port_fixture(self):
        emu = FakeIptables()
        scripts.free_add(emu, "tcp", 443, 443, "203.0.113.5")
        scripts.free_add(emu, "tcp", 443, 443, "192.0.2.9")
        scripts.free_add(emu, "tcp", 8443, 443, "198.51.100.7")
        self.assertEqual(_rules_only(emu.save_text()), _rules_only(fixture("free_v11_two_rules", "save.txt")))

    def test_pro213_function_fixture_matches_install_sh(self):
        """The 2.13 functions in the fixture are the ones in install.sh (while it is 2.13)."""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "install.sh"), encoding="utf-8") as f:
            text = f.read()
        if 'KASKAD_VERSION="2.13"' not in text:
            self.skipTest("install.sh is no longer 2.13")
        with open(os.path.join(FIX, "pro213_rule_functions.sh"), encoding="utf-8") as f:
            fx = f.read()
        for name in ("remove_rules_for_port", "apply_iptables_rules"):
            self.assertEqual(scripts.function_body(text, name), scripts.function_body(fx, name), name)


class SemanticsTest(unittest.TestCase):
    def setUp(self):
        self.emu = FakeIptables().load(fixture("pro_213", "save.txt"))

    def test_save_string(self):
        self.assertEqual(save_string("kaskad-compat_1"), "kaskad-compat_1")
        self.assertEqual(save_string("kaskad:443:tcp"), '"kaskad:443:tcp"')
        self.assertEqual(save_string("it's \"x\""), '"it\\\'s \\"x\\""')
        self.assertEqual(ipt_tokenize("--comment " + save_string("it's \"x\"")), ["--comment", "it's \"x\""])

    def test_delete_by_spec_ignores_quoting_and_order(self):
        self.emu.cmd("-t", "nat", "-D", "PREROUTING", "-p", "tcp", "--dport", "443", "-m", "comment",
                     "--comment", "kaskad:443:tcp", "-j", "DNAT", "--to-destination", "203.0.113.5:443")
        self.assertEqual(len(self.emu.rules("nat", "PREROUTING")), 2)

    def test_noflush_restore_is_atomic_per_table(self):
        before = self.emu.state()
        bad = ("*nat\n-D PREROUTING -p tcp -m tcp --dport 443 -m comment --comment \"kaskad:443:tcp\" "
               "-j DNAT --to-destination 203.0.113.5:443\n-D PREROUTING -p tcp -m tcp --dport 1 -j ACCEPT\nCOMMIT\n")
        rc, err = self.emu.restore(bad, noflush=True)
        self.assertEqual(rc, 1)
        self.assertIn("line", err)
        self.assertEqual(self.emu.state(), before)

    def test_second_table_failure_keeps_first(self):
        text = ("*nat\n-D POSTROUTING -o eth0 -m comment --comment \"kaskad:masq\" -j MASQUERADE\nCOMMIT\n"
                "*filter\n-D INPUT -p tcp -m tcp --dport 1 -j ACCEPT\nCOMMIT\n")
        rc, _err = self.emu.restore(text, noflush=True)
        self.assertEqual(rc, 1)
        self.assertEqual(self.emu.rules("nat", "POSTROUTING"), [])
        self.assertEqual(len(self.emu.rules("filter", "INPUT")), 3)

    def test_T_restores_one_table_only(self):
        snap = self.emu.save_text()
        self.emu.cmd("-t", "nat", "-F")
        self.emu.cmd("-F")
        rc, err = self.emu.restore(snap, only="nat")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(self.emu.rules("nat", "PREROUTING")), 3)
        self.assertEqual(self.emu.rules("filter", "INPUT"), [])

    def test_declaring_user_chain_in_noflush_flushes_it(self):
        emu = FakeIptables().load(fixture("docker_before", "save.txt"))
        rc, _ = emu.restore("*nat\n:DOCKER - [0:0]\nCOMMIT\n", noflush=True)
        self.assertEqual(rc, 0)
        self.assertEqual(emu.rules("nat", "DOCKER"), [])

    def test_nat_targets_only_in_nat(self):
        with self.assertRaises(AssertionError):
            self.emu.cmd("-A", "INPUT", "-p", "tcp", "--dport", "1", "-j", "DNAT", "--to-destination", "1.2.3.4:1")
        with self.assertRaises(AssertionError):
            self.emu.cmd("-A", "INPUT", "-j", "NO-SUCH-CHAIN")

    def test_basic_commands(self):
        e = FakeIptables()
        e.cmd("-A", "INPUT", "-p", "tcp", "--dport", "1", "-j", "ACCEPT")
        e.cmd("-A", "INPUT", "-p", "tcp", "--dport", "3", "-j", "ACCEPT")
        e.cmd("-I", "INPUT", "2", "-p", "tcp", "--dport", "2", "-j", "ACCEPT")
        e.cmd("-I", "INPUT", "-p", "tcp", "--dport", "0", "-j", "ACCEPT")
        self.assertEqual([r.split("--dport ")[1].split()[0] for r in e.rules("filter", "INPUT")], ["0", "1", "2", "3"])
        e.cmd("-D", "INPUT", "2")
        self.assertEqual(len(e.rules("filter", "INPUT")), 3)
        e.cmd("-P", "FORWARD", "DROP")
        self.assertIn("-P FORWARD DROP\n", e.list_S("filter"))
        e.cmd("-N", "MY")
        e.cmd("-A", "INPUT", "-j", "MY")
        with self.assertRaises(AssertionError):
            e.cmd("-X", "MY")                                  # still referenced
        e.cmd("-D", "INPUT", "-j", "MY")
        e.cmd("-X", "MY")
        e.cmd("-t", "mangle", "-A", "FORWARD", "-p", "tcp", "--tcp-flags", "SYN,RST", "SYN", "-j", "TCPMSS",
              "--clamp-mss-to-pmtu")
        self.assertIn("*mangle", e.save_text())
        e.cmd("-F")
        self.assertEqual(e.rules("filter", "INPUT"), [])
        self.assertEqual(e.h_iptables(["iptables", "-w", "5", "-V"], None)[1:], ("iptables v1.8.9 (nf_tables)\n", ""))
        rc, _out, err = e.h_iptables(["iptables", "-C", "INPUT", "-j", "DROP"], None)
        self.assertEqual(rc, 1)

    def test_restore_with_wait_and_bad_option(self):
        e = FakeIptables()
        rc, _o, err = e.h_restore(["iptables-restore", "-w", "10", "--noflush"], "*filter\n-A INPUT -j ACCEPT\nCOMMIT\n")
        self.assertEqual(rc, 0, err)
        rc, _o, _e = e.h_restore(["iptables-restore", "--bogus"], "")
        self.assertEqual(rc, 2)
        rc, err = e.restore("*filter\n-A INPUT -j ACCEPT\n", noflush=True)
        self.assertEqual(rc, 1)
        self.assertIn("COMMIT expected", err)

    def test_legacy_prints_comment_in_place(self):
        emu = FakeIptables(backend="legacy")
        emu.cmd("-A", "INPUT", "-p", "tcp", "-m", "comment", "--comment", "a b", "--dport", "80", "-j", "ACCEPT")
        self.assertEqual(emu.rules("filter", "INPUT"),
                         ['-A INPUT -p tcp -m comment --comment "a b" -m tcp --dport 80 -j ACCEPT'])
        nft = FakeIptables()
        nft.cmd("-A", "INPUT", "-p", "tcp", "-m", "comment", "--comment", "a b", "--dport", "80", "-j", "ACCEPT")
        self.assertEqual(nft.rules("filter", "INPUT"),
                         ['-A INPUT -p tcp -m tcp --dport 80 -m comment --comment "a b" -j ACCEPT'])


if __name__ == "__main__":
    unittest.main()
