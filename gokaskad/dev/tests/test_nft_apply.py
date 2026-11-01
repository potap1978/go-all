"""nft.read_live / apply / verify / restore / delete_table / save_counters / probe_support."""

import copy
import json
import os
import stat
import unittest

from kaskad import nft, state
from kaskad.errors import KaskadError
from kaskad.sysrun import FakeRunner
from tests.fakes_nft import (DEFAULT_BINARIES, F443, F51820, F8443, World, make_state, save_state,
                             write_os_release)
from tests.helpers import RootCase

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "nft")

REAL_A = ({"proto": "tcp", "port": 8081, "to": "10.99.0.2", "to_port": 8080},
          {"proto": "udp", "port": 5353, "to": "10.99.0.2", "to_port": 5353})
REAL_B = ({"proto": "tcp", "port": 8081, "to": "10.99.0.2", "to_port": 8080},
          {"proto": "tcp", "port": 8082, "to": "10.99.0.2", "to_port": 8090})


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def strip_volatile(obj):
    """Drop handles, counter values and metainfo: what remains must be equal for fake and real nft."""
    if isinstance(obj, dict):
        return dict((k, strip_volatile(v)) for k, v in obj.items() if k not in ("handle", "packets", "bytes"))
    if isinstance(obj, list):
        return [strip_volatile(x) for x in obj if not (isinstance(x, dict) and "metainfo" in x)]
    return obj


class RealJsonTest(unittest.TestCase):
    """read_live's parser against what Debian 12 (nft 1.0.6) and Ubuntu 24.04 (nft 1.0.9) printed."""

    def test_parse_real_list_output(self):
        for system in ("deb12", "u2404"):
            live = nft.parse_live(json.loads(fixture("real-%s-list.json" % system)))
            self.assertEqual(live["forwards"], {("tcp", 8081): ("10.99.0.2", 8080),
                                                ("udp", 5353): ("10.99.0.2", 5353)}, system)
            self.assertEqual(live["counters"], {"fwd_tcp_8081": {"packets": 0, "bytes": 0},
                                                "fwd_udp_5353": {"packets": 0, "bytes": 0}})
            self.assertEqual(live["chains"], ["fwd_pre", "fwd_dnat", "fwd_count", "fwd_post"])
            self.assertTrue(live["has_jump"])
            self.assertTrue(live["has_masquerade"])
            self.assertEqual(live["counted"], ["fwd_udp_5353", "fwd_tcp_8081"])
            self.assertEqual(live["anomalies"], [])
            self.assertEqual(nft.verify(make_state(REAL_A), live), [])
            self.assertNotEqual(nft.verify(make_state(REAL_B), live), [])

    def test_parse_real_output_after_update_with_traffic(self):
        for system in ("deb12", "u2404"):
            doc = json.loads(fixture("real-%s-list-after-update.json" % system))
            live = nft.parse_live(doc)
            self.assertEqual(live["forwards"], {("tcp", 8081): ("10.99.0.2", 8080),
                                                ("tcp", 8082): ("10.99.0.2", 8090)}, system)
            want = dict((it["counter"]["name"], {"packets": it["counter"]["packets"], "bytes": it["counter"]["bytes"]})
                        for it in doc["nftables"] if "counter" in it)
            self.assertEqual(live["counters"], want)
            self.assertGreater(live["counters"]["fwd_tcp_8081"]["packets"], 0)
            self.assertEqual(nft.verify(make_state(REAL_B), live), [])

    def test_fake_prints_what_real_nft_prints(self):
        fake = World().nft
        self.assertEqual(fake.run_script(fixture("real-full.nft"), True)[0], 0)
        mine = strip_volatile(json.loads(fake.json_table("kaskad")))
        self.assertEqual(mine, strip_volatile(json.loads(fixture("real-deb12-list.json"))))
        self.assertEqual(fake.run_script(fixture("real-update.nft"), True)[0], 0)
        mine = strip_volatile(json.loads(fake.json_table("kaskad")))
        self.assertEqual(mine, strip_volatile(json.loads(fixture("real-u2404-list-after-update.json"))))

    def test_anomalies(self):
        doc = json.loads(fixture("real-deb12-list.json"))
        rules = dict((it["rule"]["comment"], it["rule"]) for it in doc["nftables"]
                     if "rule" in it and it["rule"]["chain"] == "fwd_dnat")
        rules["kaskad:8081:tcp"]["expr"][0]["match"]["right"] = 8099  # comment says 8081
        extra = copy.deepcopy(rules["kaskad:5353:udp"])
        extra["handle"] = 99                                          # second rule for udp:5353
        foreign = {"family": "ip", "table": "kaskad", "chain": "fwd_dnat", "handle": 100,
                   "expr": [{"accept": None}]}
        doc["nftables"] += [{"rule": extra}, {"rule": foreign}]
        live = nft.parse_live(doc)
        self.assertEqual(list(live["forwards"]), [("udp", 5353)])
        self.assertEqual(len(live["anomalies"]), 3, live["anomalies"])
        diffs = nft.verify(make_state(REAL_A), live)
        self.assertIn("нет проброса tcp:8081", diffs)
        self.assertTrue(any("дважды" in d for d in diffs))

    def test_unexpected_document_refused(self):
        for doc in ([], {"nftables": "x"}, {"other": []}):
            with self.assertRaises(KaskadError):
                nft.parse_live(doc)


class ApplyTest(RootCase):
    def setUp(self):
        super().setUp()
        self.w = World(ip_forward="0")

    def tearDown(self):
        self.assertEqual(self.w.runner.unmatched, [])
        super().tearDown()

    def test_apply_creates_table_files_and_enables_forwarding(self):
        st = make_state([F443, F51820])
        res = nft.apply(st, self.w.runner)
        self.assertEqual(res, {"ok": True, "added": ["tcp:443", "udp:51820"], "removed": [], "changed": [],
                               "recreated": False, "warnings": []})
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443),
                                                 ("udp", 51820): ("198.51.100.7", 51820)})
        p = self.path("/etc/kaskad/net/kaskad.nft")
        self.assertEqual(self.read("/etc/kaskad/net/kaskad.nft"), nft.render_full(st))
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        saved = json.loads(self.read("/etc/kaskad/net/counters.json"))
        self.assertEqual(saved["counters"], {"fwd_tcp_443": {"packets": 0, "bytes": 0},
                                             "fwd_udp_51820": {"packets": 0, "bytes": 0}})
        self.assertEqual(self.w.sysctl.values["net.ipv4.ip_forward"], "1")
        self.assertEqual(self.read("/etc/sysctl.d/90-kaskad.conf").splitlines()[-1], "net.ipv4.ip_forward=1")
        self.assertEqual(stat.S_IMODE(os.stat(self.path("/etc/sysctl.d/90-kaskad.conf")).st_mode), 0o644)
        nft_calls = [a[1:3] for a in self.w.runner.commands(["nft"])]
        self.assertEqual(nft_calls, [["-j", "list"], ["-c", "-f"], ["-f", "-"], ["-j", "list"]])

    def test_apply_twice_changes_nothing(self):
        st = make_state([F443])
        nft.apply(st, self.w.runner)
        res = nft.apply(st, self.w.runner)
        self.assertEqual((res["added"], res["removed"], res["changed"]), ([], [], []))
        self.assertNotIn("add counter", self.w.nft.applied[-1])
        self.assertEqual(self.w.sysctl.writes, ["net.ipv4.ip_forward=1"])

    def test_update_keeps_counters_of_forwards_that_stay(self):
        nft.apply(make_state([F443, F51820]), self.w.runner)
        self.w.nft.set_counter("fwd_tcp_443", 100, 5000)
        self.w.nft.set_counter("fwd_udp_51820", 7, 700)
        st = make_state([dict(F443, to="203.0.113.77"), F8443])
        res = nft.apply(st, self.w.runner)
        self.assertEqual((res["added"], res["removed"], res["changed"]), (["tcp:8443"], ["udp:51820"], ["tcp:443"]))
        self.assertEqual(self.w.nft.counter("fwd_tcp_443"), {"packets": 100, "bytes": 5000})
        self.assertIsNone(self.w.nft.counter("fwd_udp_51820"))
        self.assertEqual(self.w.nft.counter("fwd_tcp_8443"), {"packets": 0, "bytes": 0})
        self.assertIn("\tcounter fwd_tcp_443 {\n\t\tpackets 100 bytes 5000\n\t}", self.read("/etc/kaskad/net/kaskad.nft"))

    def test_rejected_script_changes_nothing(self):
        nft.apply(make_state([F443]), self.w.runner)
        before = copy.deepcopy(self.w.nft.tables)
        text = self.read("/etc/kaskad/net/kaskad.nft")
        commits = len(self.w.nft.applied)
        self.w.nft.fail_check = "Error: Could not process rule: Operation not supported\n"
        with self.assertRaises(KaskadError) as cm:
            nft.apply(make_state([F443, F51820]), self.w.runner)
        self.assertEqual(cm.exception.data["stage"], "check")
        self.assertIn("Operation not supported", str(cm.exception))
        self.assertEqual(self.w.nft.tables, before)
        self.assertEqual(len(self.w.nft.applied), commits)
        self.assertEqual(self.read("/etc/kaskad/net/kaskad.nft"), text)

    def test_failed_commit_raises(self):
        self.w.nft.fail_apply = "Error: Could not process rule: Cannot allocate memory\n"
        with self.assertRaises(KaskadError) as cm:
            nft.apply(make_state([F443]), self.w.runner)
        self.assertEqual(cm.exception.data["stage"], "apply")
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/net/kaskad.nft")))

    def test_verification_mismatch_raises_and_keeps_the_file(self):
        self.w.nft.after_apply = lambda fake: fake.drop_rule("fwd_dnat", 0)
        with self.assertRaises(KaskadError) as cm:
            nft.apply(make_state([F443, F51820]), self.w.runner)
        self.assertEqual(cm.exception.data["stage"], "verify")
        self.assertIn("нет проброса tcp:443", cm.exception.data["mismatch"])
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/net/kaskad.nft")))

    def test_gateway_objects_are_never_touched(self):
        nft.apply(make_state([F443]), self.w.runner)
        self.w.nft.add_foreign_chain("gw_in")
        self.w.nft.add_foreign_counter("gw_bytes", packets=9, nbytes=99)
        nft.apply(make_state([F51820]), self.w.runner)
        t = self.w.nft.table()
        self.assertIn("gw_in", t["chains"])
        self.assertEqual(len(t["chains"]["gw_in"]["rules"]), 1)
        self.assertEqual(self.w.nft.counter("gw_bytes"), {"packets": 9, "bytes": 99})
        self.assertIsNone(self.w.nft.counter("fwd_tcp_443"))

    def test_table_recreated_when_a_base_chain_was_changed_by_hand(self):
        nft.apply(make_state([F443]), self.w.runner)
        self.w.nft.set_counter("fwd_tcp_443", 5, 500)
        self.w.nft.set_prio("fwd_pre", 0)                      # "add chain ... priority -101" -> EEXIST
        res = nft.apply(make_state([F443]), self.w.runner)
        self.assertTrue(res["recreated"])
        self.assertEqual(self.w.nft.table()["chains"]["fwd_pre"]["hook"]["prio"], -101)
        self.assertEqual(self.w.nft.counter("fwd_tcp_443"), {"packets": 5, "bytes": 500})

    def test_masquerade_runs_after_iptables_nat_postrouting(self):
        # iptables nat POSTROUTING is priority 100 (srcnat): a foreign SNAT there must win, as with
        # 2.x's kaskad:masq that was the last rule of POSTROUTING
        self.assertIn("type nat hook postrouting priority 101;", nft.HOOKS["fwd_post"])
        self.assertIn("\t\ttype nat hook postrouting priority 101; policy accept;\n",
                      nft.render_full(make_state([F443])))
        # a table of the first 3.0.1 builds (fwd_post at 100) is brought to 101 by the next apply
        nft.apply(make_state([F443]), self.w.runner)
        self.w.nft.set_counter("fwd_tcp_443", 7, 700)
        self.w.nft.set_prio("fwd_post", 100)
        res = nft.apply(make_state([F443]), self.w.runner)
        self.assertTrue(res["recreated"])
        self.assertEqual(self.w.nft.table()["chains"]["fwd_post"]["hook"]["prio"], 101)
        self.assertEqual(self.w.nft.counter("fwd_tcp_443"), {"packets": 7, "bytes": 700})

    def test_table_with_gateway_chains_is_never_recreated(self):
        nft.apply(make_state([F443]), self.w.runner)
        self.w.nft.set_prio("fwd_pre", 0)
        self.w.nft.add_foreign_chain("gw_in")
        with self.assertRaises(KaskadError) as cm:
            nft.apply(make_state([F443]), self.w.runner)
        self.assertEqual(cm.exception.data["stage"], "check")
        self.assertIn("File exists", str(cm.exception))
        self.assertIn("gw_in", self.w.nft.table()["chains"])

    def test_ip_forward_failure_is_a_warning(self):
        self.w.sysctl.fail_write = "sysctl: permission denied on key 'net.ipv4.ip_forward'\n"
        res = nft.apply(make_state([F443]), self.w.runner)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["warnings"]), 1)
        self.assertIn("ip_forward", res["warnings"][0])


class LiveTableTest(RootCase):
    def setUp(self):
        super().setUp()
        self.w = World()

    def test_read_live_absent_table_is_none(self):
        self.assertIsNone(nft.read_live(self.w.runner))

    def test_read_live_errors_raise(self):
        for answer in ((1, "", "Error: Operation not permitted\n"), (127, "", "nft: not found"), (0, "not json", "")):
            r = FakeRunner(binaries=["nft"]).on(["nft"], answer)
            with self.assertRaises(KaskadError, msg=answer):
                nft.read_live(r)

    def test_restore_from_file_keeps_saved_counters(self):
        st = save_state([F443])
        nft.apply(st, self.w.runner)
        self.w.nft.set_counter("fwd_tcp_443", 3, 300)
        nft.save_counters(self.w.runner)
        self.w.nft.flush_ruleset()
        self.assertTrue(nft.restore(self.w.runner))
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443)})
        self.assertEqual(self.w.nft.counter("fwd_tcp_443"), {"packets": 3, "bytes": 300})
        self.assertIn(["nft", "-f", self.path("/etc/kaskad/net/kaskad.nft")], self.w.runner.commands(["nft"]))

    def test_restore_without_file_renders_it_from_state(self):
        save_state([F443, F51820])
        self.assertTrue(nft.restore(self.w.runner))
        self.assertEqual(len(self.w.nft.forwards()), 2)
        self.assertEqual(self.read("/etc/kaskad/net/kaskad.nft"), nft.render_full(state.load()))

    def test_restore_rerenders_a_damaged_file(self):
        save_state([F443])
        self.write("/etc/kaskad/net/kaskad.nft", "table ip kaskad {\n\tchain broken\n")
        self.assertTrue(nft.restore(self.w.runner))
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443)})
        self.assertEqual(self.read("/etc/kaskad/net/kaskad.nft"), nft.render_full(state.load()))

    def test_restore_failure_returns_false(self):
        save_state([F443])
        self.w.nft.fail_apply = "Error: Could not process rule: Operation not supported\n"
        self.assertFalse(nft.restore(self.w.runner))

    def test_delete_table(self):
        nft.apply(make_state([F443]), self.w.runner)
        self.assertTrue(nft.delete_table(self.w.runner))
        self.assertIsNone(self.w.nft.table())
        self.assertFalse(nft.delete_table(self.w.runner))
        no_nft = FakeRunner().on(["nft"], (127, "", "nft: not found"))
        self.assertFalse(nft.delete_table(no_nft))
        broken = FakeRunner(binaries=["nft"]).on(["nft"], (1, "", "Error: Operation not permitted\n"))
        with self.assertRaises(KaskadError):
            nft.delete_table(broken)

    def test_save_counters(self):
        self.assertEqual(nft.save_counters(self.w.runner), {})          # no table: no-op
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/net/kaskad.nft")))
        st = save_state([F443, F51820])
        nft.apply(st, self.w.runner)
        self.w.nft.set_counter("fwd_tcp_443", 10, 2000)
        self.w.nft.set_counter("fwd_udp_51820", 4, 400)
        got = nft.save_counters(self.w.runner)
        want = {"fwd_tcp_443": {"packets": 10, "bytes": 2000}, "fwd_udp_51820": {"packets": 4, "bytes": 400}}
        self.assertEqual(got, want)
        self.assertEqual(nft.saved_counters(), want)
        self.assertEqual(self.read("/etc/kaskad/net/kaskad.nft"), nft.render_full(st, want))
        self.assertEqual(self.w.runner.unmatched, [])


class ProbeTest(RootCase):
    def setUp(self):
        super().setUp()
        write_os_release(self)

    def probe(self, w):
        res = nft.probe_support(w.runner)
        self.assertEqual(w.runner.unmatched, [])
        self.assertEqual(w.nft_writes(), [])                        # read-only
        self.assertEqual(dict(w.nft.tables), {})
        return res

    def test_supported(self):
        w = World()
        res = self.probe(w)
        self.assertTrue(res["supported"], res)
        self.assertEqual((res["code"], res["reason"]), ("", ""))
        self.assertEqual((res["nft"], res["kernel"], res["backend"], res["firewalld"]),
                         ("1.0.6", "6.1.0-18-amd64", "nf_tables", False))
        self.assertEqual(res["os"], {"id": "debian", "version_id": "12", "pretty": "Debian GNU/Linux 12 (bookworm)"})
        probe_script = w.nft.checked[-1]
        self.assertTrue(probe_script.startswith("table ip kaskad_probe\ndelete table ip kaskad_probe\n"))
        for construct in ("fib daddr type local", "counter name", "ct mark set ct mark or", "dnat to 192.0.2.1:1",
                          "udp dport 2", "masquerade", 'comment "kaskad:1:tcp"', "ct original proto-dst"):
            self.assertIn(construct, probe_script)
        self.assertNotIn("table ip kaskad\n", probe_script)

    def test_os_gate(self):
        cases = [
            ('ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n', True),
            ('ID=debian\nVERSION_ID="13"\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n', True),
            ('ID=debian\nVERSION_ID="11"\nPRETTY_NAME="Debian GNU/Linux 11 (bullseye)"\n', False),
            ('ID=ubuntu\nID_LIKE=debian\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04.5 LTS"\n', False),
            ('ID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\n', True),
            ('ID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.10"\nPRETTY_NAME="Ubuntu 24.10"\n', True),
            ('ID=ubuntu\nID_LIKE=debian\nVERSION_ID="26.04"\nPRETTY_NAME="Ubuntu 26.04 LTS"\n', True),
            ("ID=linuxmint\nID_LIKE=\"ubuntu debian\"\nVERSION_ID=\"22\"\nPRETTY_NAME=\"Linux Mint 22\"\n", False),
            ('ID="almalinux"\nVERSION_ID="9.4"\nPRETTY_NAME="AlmaLinux 9.4 (Seafoam Ocelot)"\n', False),
            ('ID=debian\nPRETTY_NAME="Debian GNU/Linux trixie/sid"\n', False),       # sid: no VERSION_ID
        ]
        for text, ok in cases:
            self.write("/etc/os-release", text)
            res = self.probe(World())
            self.assertEqual(res["supported"], ok, text)
            if not ok:
                pretty = nft.read_os_release()["pretty"]
                self.assertEqual(res["code"], "os")
                self.assertEqual(res["reason"], "Новый движок пробросов — на Debian 12+ и Ubuntu 24.04+; здесь %s. "
                                                "Пробросы работают по-старому." % pretty)
        os.unlink(self.path("/etc/os-release"))
        res = self.probe(World())
        self.assertEqual((res["supported"], res["code"]), (False, "os"))
        self.assertIn("здесь неизвестная система.", res["reason"])
        self.assertEqual(res["os"], {"id": "", "version_id": "", "pretty": ""})

    def test_reasons(self):
        w = World(binaries=[b for b in DEFAULT_BINARIES if b != "nft"])
        self.assertEqual(self.probe(w)["code"], "no_nft")
        w = World()
        w.nft.version = "0.9.0"
        self.assertEqual(self.probe(w)["code"], "old_nft")
        w = World(uname="4.15.0-213-generic")
        self.assertEqual(self.probe(w)["code"], "old_kernel")
        w = World()
        w.sysd.units["firewalld"] = {"enabled": True, "active": True}
        res = self.probe(w)
        self.assertEqual(res["code"], "firewalld")
        self.assertTrue(res["firewalld"])
        w = World()
        w.nft.json_ok = False
        self.assertEqual(self.probe(w)["code"], "no_json")
        w = World()
        w.nft.fail_check = "Error: Could not process rule: No such file or directory\n"
        res = self.probe(w)
        self.assertEqual(res["code"], "check_failed")
        self.assertIn("No such file or directory", res["reason"])

    def test_iptables_backend_does_not_decide(self):
        w = World()
        w.ipt.version = "v1.8.7 (legacy)"
        res = self.probe(w)
        self.assertEqual((res["supported"], res["backend"]), (True, "legacy"))
        w = World(binaries=[b for b in DEFAULT_BINARIES if b != "iptables"])     # Debian 12 image
        res = self.probe(w)
        self.assertEqual((res["supported"], res["backend"]), (True, "none"))


if __name__ == "__main__":
    unittest.main()
