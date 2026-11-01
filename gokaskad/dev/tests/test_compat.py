"""compat.py — the single iptables rule for ct-marked (Kaskad) connections."""

import os
import shlex

from kaskad import compat
from kaskad.errors import KaskadError
from tests.fakes_nft import DEFAULT_BINARIES, World
from tests.helpers import RootCase

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "nft")
RULE = ["FORWARD", "-m", "connmark", "--mark", "0x4b000000/0xff000000", "-m", "comment", "--comment",
        "kaskad:compat", "-j", "ACCEPT"]


class CompatTest(RootCase):
    def setUp(self):
        super().setUp()
        self.w = World()

    def tearDown(self):
        self.assertEqual(self.w.runner.unmatched, [])
        super().tearDown()

    def test_rule_args(self):
        self.assertEqual(compat.rule_args(), RULE)

    def test_rule_is_what_real_iptables_lists(self):
        with open(os.path.join(FIX, "real-compat-S.txt"), encoding="utf-8") as f:
            listed = shlex.split(f.read().strip())
        self.assertEqual(listed, ["-A"] + RULE)

    def test_ensure_inserts_at_the_top_once(self):
        self.w.ipt.policy[("filter", "FORWARD")] = "DROP"
        self.w.ipt.add("filter", "FORWARD", "-j ufw-before-forward")
        self.assertFalse(compat.present(self.w.runner))
        self.assertTrue(compat.ensure(self.w.runner))
        self.assertEqual(self.w.ipt.rules("filter", "FORWARD")[0], RULE[1:])
        self.assertTrue(compat.present(self.w.runner))
        self.assertFalse(compat.ensure(self.w.runner))
        self.assertEqual(len(self.w.ipt.rules("filter", "FORWARD")), 2)
        inserts = [a for a in self.w.ipt.calls if "-I" in a]
        self.assertEqual(inserts, [["iptables", "-w", "5", "-I", "FORWARD", "1"] + RULE[1:]])
        listing = self.w.runner.run(["iptables", "-S", "FORWARD"]).out
        self.assertIn('--comment "kaskad:compat" -j ACCEPT', listing)

    def test_no_iptables_binary_is_a_silent_no_op(self):
        self.w = World(binaries=[b for b in DEFAULT_BINARIES if b != "iptables"])
        self.assertFalse(compat.available(self.w.runner))
        self.assertFalse(compat.present(self.w.runner))
        self.assertFalse(compat.ensure(self.w.runner))
        self.assertEqual(compat.remove(self.w.runner), 0)
        self.assertEqual(self.w.runner.commands(["iptables"]), [])

    def test_refused_rule_raises(self):
        self.w.ipt.fail_insert = "iptables v1.8.9 (nf_tables): Couldn’t load match `connmark':No such file or directory\n"
        with self.assertRaises(KaskadError) as cm:
            compat.ensure(self.w.runner)
        self.assertIn("connmark", str(cm.exception))

    def test_remove_every_copy(self):
        self.assertEqual(compat.remove(self.w.runner), 0)
        compat.ensure(self.w.runner)
        self.w.ipt.add("filter", "FORWARD", " ".join(RULE[1:]))
        self.w.ipt.add("filter", "FORWARD", "-j DOCKER-USER")
        self.assertEqual(compat.remove(self.w.runner), 2)
        self.assertEqual(self.w.ipt.rules("filter", "FORWARD"), [["-j", "DOCKER-USER"]])
        self.assertFalse(compat.present(self.w.runner))

    # ── kaskad:compat-in (the gateway, wave 2) ──
    def test_input_twin_for_the_gateway(self):
        rule_in = ["INPUT"] + RULE[1:8] + ["kaskad:compat-in", "-j", "ACCEPT"]
        self.assertEqual(compat.rule_args_in(), rule_in)
        self.w.ipt.policy[("filter", "INPUT")] = "DROP"
        self.w.ipt.add("filter", "INPUT", "-j ufw-before-input")
        self.assertFalse(compat.present_in(self.w.runner))
        self.assertTrue(compat.ensure_in(self.w.runner))
        self.assertEqual(self.w.ipt.rules("filter", "INPUT")[0], rule_in[1:])
        self.assertFalse(compat.ensure_in(self.w.runner))
        self.assertFalse(compat.present(self.w.runner))                  # the FORWARD rule is separate
        listing = self.w.runner.run(["iptables", "-S", "INPUT"]).out
        self.assertIn('-A INPUT -m connmark --mark 0x4b000000/0xff000000 -m comment --comment "kaskad:compat-in" '
                      '-j ACCEPT', listing)
        compat.ensure(self.w.runner)
        self.assertEqual(compat.remove_in(self.w.runner), 1)
        self.assertTrue(compat.present(self.w.runner))
        self.assertEqual(self.w.ipt.rules("filter", "INPUT"), [["-j", "ufw-before-input"]])

    def test_input_twin_without_iptables(self):
        self.w = World(binaries=[b for b in DEFAULT_BINARIES if b != "iptables"])
        self.assertFalse(compat.ensure_in(self.w.runner))
        self.assertFalse(compat.present_in(self.w.runner))
        self.assertEqual(compat.remove_in(self.w.runner), 0)
        self.assertEqual(self.w.runner.commands(["iptables"]), [])
