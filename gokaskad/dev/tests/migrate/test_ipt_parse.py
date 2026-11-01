"""kaskad.ipt: splitting, Rule fields, normalized comparison, -D lines — on synthetic lines and on
the real captures from Debian 12 / Ubuntu 24.04 (tests/fixtures/iptables/real-*)."""

import os
import unittest

from kaskad import ipt
from kaskad.sysrun import FakeRunner
from tests.fakes_ipt import FakeIptables, ipt_tokenize
from tests.migrate.env import FIX, fixture

LABELED_DNAT = ('-A PREROUTING -p tcp -m tcp --dport 443 -m comment --comment "kaskad:443:tcp" '
                '-j DNAT --to-destination 198.51.100.20:443')


class SplitTest(unittest.TestCase):
    def test_quoted_and_bare_comment(self):
        self.assertEqual(ipt.split_line('-m comment --comment "kaskad:443:tcp" -j ACCEPT'),
                         ["-m", "comment", "--comment", "kaskad:443:tcp", "-j", "ACCEPT"])
        self.assertEqual(ipt.split_line('-m comment --comment kaskad:443:tcp -j ACCEPT'),
                         ["-m", "comment", "--comment", "kaskad:443:tcp", "-j", "ACCEPT"])

    def test_spaces_and_escapes_inside_quotes(self):
        self.assertEqual(ipt.split_line('--log-prefix "[UFW BLOCK] " -x'), ["--log-prefix", "[UFW BLOCK] ", "-x"])
        self.assertEqual(ipt.split_line(r'--comment "a \"b\" c"'), ["--comment", 'a "b" c'])
        # a single quote is an ordinary character for iptables-restore
        self.assertEqual(ipt.split_line("--comment it’s"), ["--comment", "it’s"])

    def test_unbalanced_quote_is_an_error_not_a_crash(self):
        with self.assertRaises(ValueError):
            ipt.split_line('--comment "open')
        r = ipt.Rule('-A INPUT -m comment --comment "open -j ACCEPT')
        self.assertTrue(r.error)
        self.assertFalse(r.simple)

    def test_agrees_with_iptables_tokenizer_on_every_fixture_line(self):
        n = 0
        for d in sorted(os.listdir(FIX)):
            for line in (fixture(d, "save.txt") or "").splitlines():
                if line.startswith("-A "):
                    self.assertEqual(ipt.split_line(line), ipt_tokenize(line), line)
                    n += 1
        self.assertGreater(n, 200)


class RuleTest(unittest.TestCase):
    def test_labeled_dnat(self):
        r = ipt.Rule(LABELED_DNAT, "nat")
        self.assertEqual((r.chain, r.proto, r.dport, r.port, r.target), ("PREROUTING", "tcp", "443", 443, "DNAT"))
        self.assertEqual(r.comment, "kaskad:443:tcp")
        self.assertEqual(r.label, (443, "tcp"))
        self.assertEqual(r.to_destination, "198.51.100.20:443")
        self.assertEqual(r.matches, ["tcp", "comment"])
        self.assertEqual(r.extra, [])
        self.assertTrue(r.simple)

    def test_free_forward_pair(self):
        d = ipt.Rule("-A FORWARD -d 198.51.100.10/32 -p udp -m udp --dport 51820 -m state "
                     "--state NEW,RELATED,ESTABLISHED -j ACCEPT")
        self.assertEqual((d.dst, d.proto, d.dport), ("198.51.100.10", "udp", "51820"))
        self.assertEqual(d.states, frozenset(["NEW", "RELATED", "ESTABLISHED"]))
        self.assertIsNone(d.comment)
        s = ipt.Rule("-A FORWARD -s 198.51.100.10/32 -p udp -m udp --sport 51820 -m state "
                     "--state RELATED,ESTABLISHED -j ACCEPT")
        self.assertEqual((s.src, s.sport, s.dport), ("198.51.100.10", "51820", None))

    def test_docker_rule_negation_and_qualifiers(self):
        r = ipt.Rule("-A DOCKER ! -i docker0 -p tcp -m tcp --dport 8080 -j DNAT --to-destination 172.17.0.2:80")
        self.assertEqual((r.iif, r.iif_neg), ("docker0", True))
        self.assertIn("! -i docker0", r.qualifiers())
        self.assertFalse(r.simple)

    def test_extra_modules(self):
        r = ipt.Rule("-A PREROUTING -p tcp -m multiport --dports 25,465 -j DNAT --to-destination 192.0.2.25")
        self.assertEqual(r.extra, ["-m", "multiport", "--dports", "25,465"])
        self.assertIsNone(r.dport)
        self.assertFalse(r.simple)
        a = ipt.Rule("-A PREROUTING -m addrtype --dst-type LOCAL -j DOCKER")
        self.assertEqual(a.extra, ["-m", "addrtype", "--dst-type", "LOCAL"])
        self.assertEqual(a.target, "DOCKER")
        c = ipt.Rule("-A ufw-logging-deny -m conntrack --ctstate INVALID -m limit --limit 3/min "
                     "--limit-burst 10 -j RETURN")
        self.assertEqual(c.states, frozenset(["INVALID"]))
        self.assertEqual(c.extra, ["-m", "limit", "--limit", "3/min", "--limit-burst", "10"])

    def test_ranges_and_negated_ports_are_qualifiers(self):
        r = ipt.Rule("-A PREROUTING -p tcp -m tcp --dport 1000:2000 -j DNAT --to-destination 192.0.2.1")
        self.assertEqual(r.dport, "1000:2000")
        self.assertIsNone(r.port)
        self.assertFalse(r.simple)
        n = ipt.Rule("-A PREROUTING -p tcp -m tcp ! --dport 22 -j ACCEPT")
        self.assertTrue(n.dport_neg)
        self.assertFalse(n.simple)

    def test_counters_prefix_and_target_args(self):
        r = ipt.Rule("[12:3456] -A POSTROUTING -o eth0 -j MASQUERADE --random")
        self.assertEqual(r.counters, (12, 3456))
        self.assertEqual(r.chain, "POSTROUTING")
        self.assertEqual(r.target_args, ["--random"])
        self.assertEqual(r.spec(), "-o eth0 -j MASQUERADE --random")

    def test_spec(self):
        self.assertEqual(ipt.Rule(LABELED_DNAT).spec(), LABELED_DNAT.split(" ", 2)[2])


class NormTest(unittest.TestCase):
    def same(self, a, b):
        self.assertEqual(ipt.Rule(a).norm(), ipt.Rule(b).norm(), "%s\n%s" % (a, b))

    def test_quoting_m_tcp_mask_state_order(self):
        self.same('-A FORWARD -d 1.2.3.4/32 -p tcp -m tcp --dport 443 -m state --state NEW,RELATED,ESTABLISHED '
                  '-m comment --comment "kaskad:443:tcp" -j ACCEPT',
                  '-A FORWARD -p tcp -d 1.2.3.4 --dport 443 -m state --state NEW,ESTABLISHED,RELATED '
                  '-m comment --comment kaskad:443:tcp -j ACCEPT')

    def test_state_vs_conntrack(self):
        self.same("-A FORWARD -s 1.2.3.4/32 -p udp -m udp --sport 53 -m state --state RELATED,ESTABLISHED -j ACCEPT",
                  "-A FORWARD -s 1.2.3.4/32 -p udp -m udp --sport 53 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT")

    def test_comment_position_legacy_vs_nft(self):
        self.same('-A INPUT -p tcp -m comment --comment "x y" -m tcp --dport 80 -j ACCEPT',
                  '-A INPUT -p tcp -m tcp --dport 80 -m comment --comment "x y" -j ACCEPT')

    def test_different_rules_differ(self):
        base = ipt.Rule("-A INPUT -p tcp -m tcp --dport 443 -j ACCEPT").norm()
        for other in ("-A INPUT -p tcp -m tcp --dport 8443 -j ACCEPT",
                      "-A INPUT -p udp -m udp --dport 443 -j ACCEPT",
                      "-A INPUT -i eth0 -p tcp -m tcp --dport 443 -j ACCEPT",
                      '-A INPUT -p tcp -m tcp --dport 443 -m comment --comment "kaskad:443:tcp" -j ACCEPT',
                      "-A FORWARD -p tcp -m tcp --dport 443 -j ACCEPT",
                      "-A INPUT -p tcp -m tcp --dport 443 -j DROP"):
            self.assertNotEqual(base, ipt.Rule(other).norm(), other)


class ParseOutputTest(unittest.TestCase):
    def test_parse_S_real_capture(self):
        nat = ipt.parse_S(fixture("real-free-v1.1", "S-nat.txt"), "nat")
        self.assertEqual(list(nat), ["PREROUTING", "INPUT", "OUTPUT", "POSTROUTING"])
        self.assertEqual(nat.policies["PREROUTING"], "ACCEPT")
        pre = nat["PREROUTING"]
        self.assertEqual([r.index for r in pre], [1, 2, 3, 4])
        self.assertEqual([r.to_destination for r in pre],
                         ["198.51.100.10:51820", "198.51.100.20:443", "198.51.100.20:443", "198.51.100.30:51820"])
        filt = ipt.parse_S(fixture("real-pro23-ufw", "S-filter.txt"), "filter")
        self.assertEqual(filt.policies, {"INPUT": "DROP", "FORWARD": "DROP", "OUTPUT": "ACCEPT"})
        self.assertIn("ufw-user-input", filt)
        self.assertEqual(len(filt["ufw-user-input"]), 3)
        lp = [r for r in filt["ufw-user-limit"] if r.target == "LOG"][0]
        self.assertEqual(lp.target_args, ["--log-prefix", "[UFW LIMIT BLOCK] "])

    def test_parse_save_real_capture_with_counters(self):
        ss = ipt.parse_save(fixture("real-pro23-ufw", "save.txt"))
        self.assertEqual(sorted(ss), ["filter", "nat"])
        self.assertEqual(ss.problems, [])
        self.assertEqual(ss.policies["filter"]["INPUT"], "DROP")
        dn = ss["nat"]["PREROUTING"]
        self.assertEqual(len(dn), 2)
        self.assertTrue(all(r.lineno is not None for r in dn))
        self.assertEqual(ss.lines[dn[0].lineno].rstrip("\n"), dn[0].text)
        self.assertEqual("".join(ss.lines), fixture("real-pro23-ufw", "save.txt"))

    def test_parse_save_flags_garbage(self):
        ss = ipt.parse_save("*filter\n:INPUT ACCEPT [0:0]\n-I INPUT -j ACCEPT\nCOMMIT\nrubbish\n")
        self.assertEqual(ss.problems, [2, 4])

    def test_split_keepends_only_on_newline(self):
        self.assertEqual(ipt.split_keepends("a\r\nb\x85c\nd"), ["a\r\n", "b\x85c\n", "d"])


class DeleteLineTest(unittest.TestCase):
    def test_every_fixture_rule_is_deleted_exactly(self):
        """The -D line built from the printed rule removes that rule and nothing else."""
        for d in sorted(os.listdir(FIX)):
            text = fixture(d, "save.txt")
            ss = ipt.parse_save(text)
            for table in ("nat", "filter"):
                if table not in ss:
                    continue
                for r in ss[table].all_rules():
                    emu = FakeIptables().load(text)
                    before = emu.rules(table, r.chain)
                    rc, err = emu.restore(ipt.restore_input(table, [ipt.delete_line(r)]), noflush=True)
                    self.assertEqual(rc, 0, "%s: %s %s" % (d, r.text, err))
                    after = emu.rules(table, r.chain)
                    self.assertEqual(len(after), len(before) - 1, r.text)
                    self.assertNotIn(r.text, after if before.count(r.text) == 1 else [])

    def test_delete_line_shape(self):
        self.assertEqual(ipt.delete_line(ipt.Rule(LABELED_DNAT)), "-D" + LABELED_DNAT[2:])
        with self.assertRaises(ValueError):
            ipt.delete_line(ipt.Rule("-I INPUT -j ACCEPT"))


class HelpersTest(unittest.TestCase):
    def test_parse_to_destination(self):
        self.assertEqual(ipt.parse_to_destination("203.0.113.5:443"), ("203.0.113.5", 443))
        self.assertEqual(ipt.parse_to_destination("203.0.113.5", 8443), ("203.0.113.5", 8443))
        self.assertIsNone(ipt.parse_to_destination("203.0.113.5"))
        for bad in ("1.2.3.4-1.2.3.6:443", "1.2.3.4:1000-2000", "host:443", "1.2.3.4:0", "1.2.3.4:70000", ""):
            self.assertIsNone(ipt.parse_to_destination(bad), bad)

    def test_ports(self):
        self.assertTrue(ipt.port_matches("443", 443))
        self.assertTrue(ipt.port_matches("400:500", 443))
        self.assertFalse(ipt.port_matches("444", 443))
        self.assertIsNone(ipt.port_matches("https", 443))
        self.assertTrue(ipt.port_list_matches("25,465", 465))
        self.assertFalse(ipt.port_list_matches("25,465", 443))
        self.assertTrue(ipt.port_list_matches("1:100,443", 443))

    def test_backend(self):
        r = FakeRunner(binaries=["iptables"]).on(["iptables", "-V"], "iptables v1.8.9 (nf_tables)\n")
        self.assertEqual(ipt.backend(r)["backend"], "nf_tables")
        r = FakeRunner(binaries=["iptables"]).on(["iptables", "-V"], "iptables v1.8.7 (legacy)\n")
        self.assertEqual(ipt.backend(r), {"backend": "legacy", "version": "1.8.7", "text": "iptables v1.8.7 (legacy)"})
        r = FakeRunner()
        self.assertEqual(ipt.backend(r)["backend"], "none")
        self.assertEqual(r.calls, [])

    def test_list_table_uses_wait_only_for_legacy(self):
        for be, prefix in (("nf_tables", ["iptables", "-t", "nat", "-S"]),
                           ("legacy", ["iptables", "-w", "5", "-t", "nat", "-S"])):
            r = FakeRunner(binaries=["iptables"])
            FakeIptables(backend=be).install(r)
            ipt.list_table(r, "nat", be)
            self.assertEqual(r.commands(["iptables"])[-1], prefix)
            self.assertEqual(r.unmatched, [])


if __name__ == "__main__":
    unittest.main()
