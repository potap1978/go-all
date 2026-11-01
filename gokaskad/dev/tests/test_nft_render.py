"""render_full / render_update against golden files in tests/fixtures/nft/.

Regenerate after a deliberate change of the table text:
    KASKAD_UPDATE_GOLDEN=1 PYTHONPATH=lib:. python3 -m unittest tests.test_nft_render
and review the diff of tests/fixtures/nft/ by eye.
"""

import json
import os
import unittest

from kaskad import nft, state
from kaskad.errors import KaskadError, StateError
from tests.fakes_nft import F443, F51820, F8443, make_state

GOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "nft")
UPDATE = os.environ.get("KASKAD_UPDATE_GOLDEN") == "1"

F80 = {"proto": "tcp", "port": 80, "to": "192.0.2.10", "to_port": 8080}
F443U = {"proto": "udp", "port": 443, "to": "203.0.113.6", "to_port": 443}
MIXED = (F51820, F443, F8443, F443U, F80)       # deliberately unsorted


class RenderTest(unittest.TestCase):
    def golden(self, name, text):
        p = os.path.join(GOLD, name)
        if UPDATE:
            os.makedirs(GOLD, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(text, f.read(), "golden %s differs" % name)

    # ── render_full ──
    def test_full_empty_state_keeps_chains_jump_and_masquerade(self):
        text = nft.render_full(make_state())
        self.golden("full_empty.nft", text)
        self.assertIn("jump fwd_dnat", text)
        self.assertIn("masquerade", text)
        self.assertNotIn("counter fwd_", text)

    def test_full_one_forward(self):
        self.golden("full_one.nft", nft.render_full(make_state([F443])))

    def test_full_mixed_sorted_by_port_then_proto(self):
        text = nft.render_full(make_state(MIXED))
        self.golden("full_mixed.nft", text)
        dnat = [ln.strip() for ln in text.splitlines() if " dnat to " in ln]
        self.assertEqual([d.split(" comment ")[1] for d in dnat],
                         ['"kaskad:80:tcp"', '"kaskad:443:tcp"', '"kaskad:443:udp"', '"kaskad:8443:tcp"',
                          '"kaskad:51820:udp"'])

    def test_full_with_counters(self):
        counters = {"fwd_tcp_443": {"packets": 1200, "bytes": 987654},
                    "fwd_udp_51820": {"packets": 7, "bytes": 1024},
                    "fwd_tcp_9999": {"packets": 5, "bytes": 5},       # no such forward: ignored
                    "fwd_tcp_8443": {"packets": -3, "bytes": "x"}}    # garbage: 0
        text = nft.render_full(make_state(MIXED), counters)
        self.golden("full_counters.nft", text)
        self.assertIn("\tcounter fwd_tcp_443 {\n\t\tpackets 1200 bytes 987654\n\t}\n", text)
        self.assertIn("\tcounter fwd_tcp_8443 {\n\t\tpackets 0 bytes 0\n\t}\n", text)
        self.assertNotIn("fwd_tcp_9999", text)

    def test_full_recreates_the_table_in_one_transaction(self):
        lines = nft.render_full(make_state([F443])).splitlines()
        self.assertEqual(lines[:2], ["table ip kaskad", "delete table ip kaskad"])
        self.assertIn("table ip kaskad {", lines)

    def test_full_is_deterministic_and_ignores_names_and_dates(self):
        a = make_state([F443, F51820])
        b = make_state([dict(F51820, name="другое"), dict(F443, added="2020-01-01T00:00:00Z")])
        self.assertEqual(nft.render_full(a), nft.render_full(b))

    # ── render_update ──
    def test_update_from_no_table(self):
        self.golden("update_fresh.nft", nft.render_update(make_state([F443, F51820]), None))

    def test_update_add_remove_change_keeps_counters(self):
        live = {"forwards": {("tcp", 443): ("192.0.2.99", 443), ("udp", 1234): ("192.0.2.1", 1234)},
                "counters": {"fwd_tcp_443": {"packets": 10, "bytes": 1000},
                             "fwd_udp_1234": {"packets": 1, "bytes": 100},
                             "gw_in": {"packets": 5, "bytes": 50}},
                "chains": list(nft.CHAINS) + ["gw_in"]}
        text = nft.render_update(make_state([F443, F80]), live)
        self.golden("update_change.nft", text)
        lines = text.splitlines()
        self.assertNotIn("add counter ip kaskad fwd_tcp_443", lines)       # kept: its value survives
        self.assertIn("add counter ip kaskad fwd_tcp_80", lines)
        self.assertEqual(lines[-1], "delete counter ip kaskad fwd_udp_1234")
        self.assertNotIn("gw_in", text)                                     # gateway objects untouched
        flushes = [i for i, ln in enumerate(lines) if ln.startswith("flush chain")]
        adds = [i for i, ln in enumerate(lines) if ln.startswith("add counter") or ln.startswith("add rule")]
        self.assertLess(max(flushes), min(adds))
        self.assertEqual(lines[0], "add table ip kaskad")

    def test_update_empty_state_deletes_every_fwd_counter(self):
        live = {"counters": {"fwd_tcp_443": {}, "fwd_udp_51820": {}}}
        text = nft.render_update(make_state(), live)
        self.assertTrue(text.endswith("delete counter ip kaskad fwd_tcp_443\n"
                                      "delete counter ip kaskad fwd_udp_51820\n"))
        self.assertIn("add rule ip kaskad fwd_pre fib daddr type local", text)
        self.assertIn("masquerade", text)

    # ── the texts verified on Debian 12 (nft 1.0.6) and Ubuntu 24.04 (nft 1.0.9) ──
    REAL_A = ({"proto": "tcp", "port": 8081, "to": "10.99.0.2", "to_port": 8080},
              {"proto": "udp", "port": 5353, "to": "10.99.0.2", "to_port": 5353})
    REAL_B = ({"proto": "tcp", "port": 8081, "to": "10.99.0.2", "to_port": 8080},
              {"proto": "tcp", "port": 8082, "to": "10.99.0.2", "to_port": 8090})

    def real(self, name):
        with open(os.path.join(GOLD, name), encoding="utf-8") as f:
            return f.read()

    def test_full_has_exactly_the_lines_verified_on_real_kernels(self):
        # the verified file lists 8081/tcp before 5353/udp; we sort by (port, proto) as the spec
        # says — so: the same lines, and the same skeleton once the per-forward lines are taken out
        mine = nft.render_full(make_state(self.REAL_A))
        real = self.real("real-full.nft")
        self.assertEqual(sorted(mine.splitlines()), sorted(real.splitlines()))

        def skeleton(text):
            return [ln for ln in text.splitlines() if "8081" not in ln and "5353" not in ln]
        self.assertEqual(skeleton(mine), skeleton(real))
        self.assertTrue(mine.endswith("\t}\n}\n") and real.endswith("\t}\n}\n"))

    def test_update_is_byte_for_byte_the_script_verified_on_real_kernels(self):
        for system in ("deb12", "u2404"):
            live = nft.parse_live(json.loads(self.real("real-%s-list.json" % system)))
            self.assertEqual(nft.render_update(make_state(self.REAL_B), live), self.real("real-update.nft"))

    # ── safety ──
    def test_nothing_but_validated_values_reaches_a_script(self):
        evil = [{"proto": "tcp", "port": 443, "to": '1.2.3.4:1 ; flush ruleset', "to_port": 443},
                {"proto": "tcp ; flush", "port": 443, "to": "203.0.113.5", "to_port": 443},
                {"proto": "tcp", "port": "443 drop", "to": "203.0.113.5", "to_port": 443}]
        for f in evil:
            st = state.empty()
            st["forwards"] = [f]
            with self.assertRaises(StateError, msg=f):
                nft.render_full(st)
            with self.assertRaises(StateError, msg=f):
                nft.render_update(st, None)
        st = make_state([dict(F443, name='x" ; flush ruleset ; "')])
        self.assertNotIn("flush ruleset", nft.render_full(st))

    def test_duplicate_forward_refused(self):
        st = state.empty()
        st["forwards"] = [dict(F443), dict(F443, to="203.0.113.9")]
        with self.assertRaises(KaskadError):
            nft.render_full(st)

    def test_counter_name(self):
        self.assertEqual(nft.counter_name("udp", "51820"), "fwd_udp_51820")


if __name__ == "__main__":
    unittest.main()
