"""forwards.py (add/delete/flush/list) and conflicts.py (ssh, listener, docker, foreign_dnat)."""

import os
import unittest

from kaskad import compat, conflicts, errors, forwards, state
from kaskad.errors import ConflictError, KaskadError, UnsupportedError
from tests.fakes_nft import F443, F51820, SS_SSHD, World, save_state
from tests.helpers import RootCase

SS_MIXED = (
    'tcp   LISTEN 0      511          0.0.0.0:443       0.0.0.0:*    users:(("nginx",pid=1000,fd=6),("nginx",pid=999,fd=6))\n'
    'tcp   LISTEN 0      4096       127.0.0.1:10808     0.0.0.0:*    users:(("xray",pid=2000,fd=3))\n'
    'tcp   LISTEN 0      4096  [2001:db8::1]:8443          [::]:*    users:(("caddy",pid=3000,fd=7))\n'
    'udp   UNCONN 0      0            0.0.0.0:51820     0.0.0.0:*\n'
    'udp   UNCONN 0      0      127.0.0.53%lo:53        0.0.0.0:*    users:(("systemd-resolve",pid=500,fd=13))\n'
    'tcp   LISTEN 0      4096               *:9090            *:*    users:(("prometheus",pid=4000,fd=8))\n'
)


class ForwardsTest(RootCase):
    def setUp(self):
        super().setUp()
        self.w = World()
        self._ssh = os.environ.pop("SSH_CONNECTION", None)

    def tearDown(self):
        if self._ssh is not None:
            os.environ["SSH_CONNECTION"] = self._ssh
        else:
            os.environ.pop("SSH_CONNECTION", None)
        self.assertEqual(self.w.runner.unmatched, [])
        super().tearDown()

    def log(self):
        p = self.path("/var/log/kaskad.log")
        return self.read("/var/log/kaskad.log") if os.path.exists(p) else ""

    def add(self, proto, port, to, to_port, **kw):
        return forwards.add_forward(proto, port, to, to_port, runner=self.w.runner, **kw)

    def assert_conflict(self, kind, proto, port, to="203.0.113.5", to_port=None):
        before = self.read("/etc/kaskad/state.json")
        with self.assertRaises(ConflictError) as cm:
            self.add(proto, port, to, to_port or port)
        self.assertEqual(cm.exception.code, errors.CONFLICT)
        self.assertEqual(cm.exception.data["kind"], kind)
        self.assertEqual(self.read("/etc/kaskad/state.json"), before)
        self.assertEqual(self.w.nft_writes(), [])
        self.assertIn("--force", str(cm.exception))
        return cm.exception

    # ── the normal path ──
    def test_add_applies_saves_and_logs(self):
        save_state()
        res = self.add("TCP", "443", "203.0.113.5", "443", name="Вена")
        self.assertEqual(forwards.describe(res["forward"]), "tcp: :443 -> 203.0.113.5:443")
        self.assertIsNone(res["replaced"])
        self.assertEqual(res["apply"]["added"], ["tcp:443"])
        f = state.find_forward(state.load(), "tcp", 443)
        self.assertEqual((f["to"], f["to_port"], f["name"], f["origin"]), ("203.0.113.5", 443, "Вена", "k3"))
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443)})
        self.assertTrue(self.log().rstrip().endswith("] ADD rule: tcp :443 -> 203.0.113.5:443 (Вена)"))
        self.assertTrue(os.path.exists(self.path("/etc/kaskad/net/kaskad.nft")))
        self.assertTrue(self.w.runner.commands(["ss"]))                   # conflicts were looked for
        self.assertTrue(compat.present(self.w.runner))                    # works at once under UFW/Docker

    def test_refused_compat_rule_is_only_a_warning(self):
        save_state()
        self.w.ipt.fail_insert = "iptables: Couldn’t load match `connmark'\n"
        res = self.add("tcp", 443, "203.0.113.5", 443)
        self.assertTrue(res["ok"])
        self.assertIn("connmark", res["apply"]["warnings"][0])
        self.assertIsNotNone(state.find_forward(state.load(), "tcp", 443))

    def test_add_without_name_logs_without_parentheses(self):
        save_state()
        self.add("udp", 51820, "198.51.100.7", 51820)
        self.assertTrue(self.log().rstrip().endswith("] ADD rule: udp :51820 -> 198.51.100.7:51820"))

    def test_engine_legacy_refuses_everything(self):
        save_state([F443], engine="legacy")
        for call in (lambda: self.add("tcp", 80, "203.0.113.5", 80),
                     lambda: forwards.delete_forward("tcp", 443, runner=self.w.runner),
                     lambda: forwards.flush_forwards(runner=self.w.runner),
                     lambda: forwards.list_forwards(state.load(), self.w.runner)):
            with self.assertRaises(UnsupportedError) as cm:
                call()
            self.assertEqual(cm.exception.code, errors.UNSUPPORTED)
        self.assertEqual(self.w.runner.calls, [])
        self.assertEqual(self.log(), "")

    def test_bad_arguments_are_usage_errors(self):
        save_state()
        for args in (("icmp", 1, "203.0.113.5", 1), ("tcp", 70000, "203.0.113.5", 1),
                     ("tcp", 80, "example.com", 80), ("tcp", 80, "127.0.0.1", 80), ("tcp", 80, "203.0.113.5", "x")):
            with self.assertRaises(KaskadError) as cm:
                self.add(*args)
            self.assertEqual(cm.exception.code, errors.USAGE, args)
        with self.assertRaises(KaskadError) as cm:
            forwards.delete_forward("tcp", "0", runner=self.w.runner)
        self.assertEqual(cm.exception.code, errors.USAGE)
        self.assertEqual(self.w.runner.calls, [])

    def test_failed_apply_leaves_state_and_log_untouched(self):
        save_state([F51820])
        before = self.read("/etc/kaskad/state.json")
        self.w.nft.fail_check = "Error: Could not process rule: Operation not supported\n"
        with self.assertRaises(KaskadError) as cm:
            self.add("tcp", 443, "203.0.113.5", 443)
        self.assertEqual(cm.exception.code, errors.ERROR)
        self.assertEqual(self.read("/etc/kaskad/state.json"), before)
        self.assertEqual(self.log(), "")
        self.w.nft.fail_check = None
        self.w.nft.after_apply = lambda fake: fake.drop_rule("fwd_pre", 0)        # verification fails
        with self.assertRaises(KaskadError) as cm:
            forwards.delete_forward("udp", 51820, runner=self.w.runner)
        self.assertEqual(cm.exception.data["stage"], "verify")
        self.assertEqual(self.read("/etc/kaskad/state.json"), before)
        self.assertEqual(self.log(), "")

    def test_delete(self):
        save_state()
        self.add("tcp", 443, "203.0.113.5", 443)
        self.add("udp", 51820, "198.51.100.7", 51820)
        res = forwards.delete_forward("TCP", "443", runner=self.w.runner)
        self.assertEqual(res["removed"]["to"], "203.0.113.5")
        self.assertIsNone(state.find_forward(state.load(), "tcp", 443))
        self.assertEqual(list(self.w.nft.forwards()), [("udp", 51820)])
        self.assertIsNone(self.w.nft.counter("fwd_tcp_443"))
        self.assertTrue(self.log().rstrip().endswith("] DELETE rule: tcp :443 -> 203.0.113.5:443"))
        with self.assertRaises(KaskadError) as cm:
            forwards.delete_forward("tcp", 443, runner=self.w.runner)
        self.assertEqual(cm.exception.code, errors.ERROR)

    def test_flush(self):
        save_state()
        self.add("tcp", 443, "203.0.113.5", 443)
        self.add("udp", 51820, "198.51.100.7", 51820)
        res = forwards.flush_forwards(runner=self.w.runner)
        self.assertEqual(res["removed"], 2)
        self.assertEqual(state.load()["forwards"], [])
        self.assertEqual(self.w.nft.forwards(), {})
        self.assertEqual(len(self.w.nft.rules("fwd_pre")), 1)              # jump and masquerade stay
        self.assertEqual(len(self.w.nft.rules("fwd_post")), 1)
        self.assertTrue(self.log().rstrip().endswith("] FLUSH all kaskad rules"))

    def test_list_forwards(self):
        st = save_state([F51820, F443])
        rows = forwards.list_forwards(st, self.w.runner)
        self.assertEqual([forwards.legacy_line(r) for r in rows], ["tcp|443|203.0.113.5:443",
                                                                    "udp|51820|198.51.100.7:51820"])
        self.assertEqual(self.w.runner.calls, [])                          # cheap: no command without traffic
        rows = forwards.list_forwards(st, self.w.runner, traffic=True)
        self.assertEqual([(r["packets"], r["bytes"], r["live"]) for r in rows], [(0, 0, False), (0, 0, False)])
        from kaskad import nft
        nft.apply(st, self.w.runner)
        self.w.nft.set_counter("fwd_tcp_443", 12, 3456)
        rows = forwards.list_forwards(st, self.w.runner, traffic=True)
        self.assertEqual(forwards.legacy_line(rows[0], True), "tcp|443|203.0.113.5:443|12|3456")
        self.assertTrue(rows[0]["live"])

    # ── replacement ──
    def test_replacing_the_same_port_is_not_a_conflict(self):
        save_state()
        self.add("tcp", 443, "203.0.113.5", 443, name="Вена")
        self.w.ss = SS_MIXED                                                # nginx appears on 443 later
        res = self.add("tcp", 443, "203.0.113.9", 8443)
        self.assertEqual(res["replaced"]["to"], "203.0.113.5")
        self.assertEqual(res["forward"]["name"], "Вена")                    # kept when not given
        self.assertEqual(res["apply"]["changed"], ["tcp:443"])
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.9", 8443)})
        self.assertEqual(len(state.load()["forwards"]), 1)

    def test_re_adding_the_same_target_keeps_origin_and_date(self):
        st = state.empty()
        st["engine"] = "nft"
        state.upsert_forward(st, dict(F443, origin="free", added="2026-01-01T00:00:00Z"))
        state.save(st)
        res = self.add("tcp", 443, "203.0.113.5", 443)
        self.assertEqual((res["forward"]["origin"], res["forward"]["added"]), ("free", "2026-01-01T00:00:00Z"))
        self.assertEqual(state.find_forward(state.load(), "tcp", 443)["origin"], "free")

    # ── conflicts ──
    def test_conflict_ssh_listener(self):
        save_state()
        self.w.ss = SS_SSHD
        exc = self.assert_conflict("ssh", "tcp", 22)
        self.assertEqual(len(exc.data["conflicts"]), 1)                    # v4 + v6 listeners: one entry
        self.assertIn("sshd", exc.data["conflicts"][0]["detail"])
        self.add("udp", 22, "203.0.113.5", 22)                              # sshd is tcp only

    def test_conflict_ssh_session_port(self):
        save_state()
        os.environ["SSH_CONNECTION"] = "198.51.100.1 50000 203.0.113.1 2222"
        exc = self.assert_conflict("ssh", "tcp", 2222)
        self.assertIn("SSH-подключение", exc.data["conflicts"][0]["detail"])
        self.add("udp", 2222, "203.0.113.5", 2222)

    def test_conflict_ssh_port_22_says_the_cut_once(self):
        """T-M6 (перепроверка f2a725a): sshd слушает 22, и по нему же идёт подключение — чем грозит, сказано один раз."""
        save_state()
        self.w.ss = SS_SSHD
        os.environ["SSH_CONNECTION"] = "198.51.100.1 50000 203.0.113.1 22"
        exc = self.assert_conflict("ssh", "tcp", 22)
        detail = exc.data["conflicts"][0]["detail"]
        self.assertIn("sshd", detail)
        self.assertIn("SSH-подключение", detail)
        self.assertEqual(detail.count("отрежет вход"), 1, detail)
        self.assertTrue(detail.endswith("— проброс отрежет вход на этот сервер"), detail)
        self.assertEqual(str(exc).count("отрежет вход"), 1, str(exc))

    def test_conflict_listener(self):
        save_state()
        self.w.ss = SS_MIXED
        exc = self.assert_conflict("listener", "tcp", 443)
        self.assertIn("nginx", exc.data["conflicts"][0]["detail"])
        self.assertEqual(exc.data["conflicts"][0]["detail"].count("nginx"), 1)
        exc = self.assert_conflict("listener", "udp", 51820)                # kernel WireGuard: no process
        self.assertIn("без имени процесса", exc.data["conflicts"][0]["detail"])
        self.assert_conflict("listener", "tcp", 9090)                       # "*" address
        self.add("tcp", 10808, "203.0.113.5", 10808)                        # loopback only: not a conflict
        self.add("tcp", 8443, "203.0.113.5", 8443)                          # IPv6-only address
        self.add("udp", 53, "203.0.113.5", 53)                              # 127.0.0.53%lo

    def test_conflict_docker(self):
        save_state()
        self.w.ipt.new_chain("nat", "DOCKER")
        self.w.ipt.add("nat", "DOCKER", "-i docker0 -j RETURN")
        self.w.ipt.add("nat", "DOCKER", "! -i docker0 -p tcp -m tcp --dport 8080 -j DNAT --to-destination 172.17.0.2:80")
        self.w.ipt.add("nat", "DOCKER", "-d 127.0.0.1/32 ! -i docker0 -p tcp -m tcp --dport 9000 "
                                        "-j DNAT --to-destination 172.17.0.3:9000")
        self.w.ss = ('tcp LISTEN 0 4096 0.0.0.0:8080 0.0.0.0:* users:(("docker-proxy",pid=7,fd=4))\n'
                     'tcp LISTEN 0 4096 127.0.0.1:9000 0.0.0.0:* users:(("docker-proxy",pid=8,fd=4))\n')
        exc = self.assert_conflict("docker", "tcp", 8080)
        self.assertEqual([c["kind"] for c in exc.data["conflicts"]], ["docker"])    # docker-proxy is not "listener"
        self.assertIn("172.17.0.2:80", exc.data["conflicts"][0]["detail"])
        self.add("tcp", 9000, "203.0.113.5", 9000)                          # published on 127.0.0.1 only

    def test_conflict_foreign_dnat(self):
        save_state()
        self.w.ipt.add("nat", "PREROUTING", "-p tcp -m tcp --dport 3389 -j DNAT --to-destination 10.0.0.5:3389")
        self.w.ipt.add("nat", "PREROUTING", "-p tcp -m tcp --dport 443 -m comment --comment \"kaskad:443:tcp\" "
                                            "-j DNAT --to-destination 192.0.2.1:443")
        self.w.ipt.add("nat", "PREROUTING", "-p udp -m multiport --dports 5000:5010,6000 -j DNAT "
                                            "--to-destination 10.0.0.6")
        self.w.ipt.add("nat", "PREROUTING", "-m addrtype --dst-type LOCAL -j DOCKER")
        self.w.ipt.add("nat", "PREROUTING", "-p tcp -m tcp --dport 80 -j REDIRECT --to-ports 8080")
        exc = self.assert_conflict("foreign_dnat", "tcp", 3389)
        self.assertIn("10.0.0.5:3389", exc.data["conflicts"][0]["detail"])
        self.assert_conflict("foreign_dnat", "udp", 5005)
        self.assert_conflict("foreign_dnat", "udp", 6000)
        self.assert_conflict("foreign_dnat", "tcp", 80)
        self.add("tcp", 443, "203.0.113.5", 443)                            # a 2.x kaskad rule: not foreign
        self.add("udp", 5011, "203.0.113.5", 5011)

    def test_several_conflicts_most_severe_first(self):
        save_state()
        self.w.ss = SS_SSHD
        self.w.ipt.add("nat", "PREROUTING", "-p tcp -m tcp --dport 22 -j DNAT --to-destination 10.0.0.5:22")
        exc = self.assert_conflict("ssh", "tcp", 22)
        self.assertEqual([c["kind"] for c in exc.data["conflicts"]], ["ssh", "foreign_dnat"])

    def test_force_skips_the_checks(self):
        save_state()
        self.w.ss = SS_SSHD
        res = self.add("tcp", 22, "203.0.113.5", 22, force=True)
        self.assertTrue(res["ok"])
        self.assertEqual(self.w.runner.commands(["ss"]), [])

    def test_missing_tools_mean_no_conflicts(self):
        self.w = World(binaries=("nft", "sysctl"))
        save_state()
        self.w.ss = SS_SSHD
        self.add("tcp", 22, "203.0.113.5", 22)
        self.assertEqual(self.w.runner.commands(["ss"]), [])
        self.assertEqual(self.w.runner.commands(["iptables"]), [])


class ConflictParsingTest(unittest.TestCase):
    def test_parse_ss(self):
        rows = conflicts.parse_ss("Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
                                  + SS_MIXED + SS_SSHD)
        self.assertEqual(rows[0], {"proto": "tcp", "addr": "0.0.0.0", "port": 443, "procs": ["nginx"], "pids": [1000, 999]})
        self.assertEqual(rows[2]["addr"], "2001:db8::1")
        self.assertEqual(rows[3], {"proto": "udp", "addr": "0.0.0.0", "port": 51820, "procs": [], "pids": []})
        self.assertEqual(rows[4]["addr"], "127.0.0.53")
        self.assertEqual(rows[5]["addr"], "*")
        self.assertEqual(rows[7]["addr"], "::")
        self.assertEqual(len(rows), 8)

    def test_exposed(self):
        for addr, want in (("0.0.0.0", True), ("*", True), ("::", True), ("203.0.113.1", True),
                           ("127.0.0.1", False), ("127.0.0.53", False), ("::1", False), ("2001:db8::1", False),
                           ("::ffff:127.0.0.1", False), ("::ffff:203.0.113.1", True), ("fe80::1", False)):
            self.assertEqual(conflicts.exposed(addr), want, addr)

    def test_parse_rule_and_match(self):
        r = conflicts.parse_rule('-A PREROUTING -p tcp -m tcp --dport 443 -m comment --comment "kaskad:443:tcp" '
                                 '-j DNAT --to-destination 203.0.113.5:443')
        self.assertEqual((r["proto"], r["dports"], r["target"], r["comment"], r["to"]),
                         ("tcp", "443", "DNAT", "kaskad:443:tcp", "203.0.113.5:443"))
        self.assertIsNone(conflicts.parse_rule("-P PREROUTING ACCEPT"))
        self.assertIsNone(conflicts.parse_rule('-A X "unterminated'))
        neg = conflicts.parse_rule("-A PREROUTING ! -p udp ! --dport 22 -j DNAT --to-destination 10.0.0.1")
        self.assertEqual((neg["proto"], neg["dports"]), ("!udp", "!22"))
        self.assertTrue(conflicts.rule_matches(neg, "tcp", 80))
        self.assertFalse(conflicts.rule_matches(neg, "udp", 80))
        self.assertFalse(conflicts.rule_matches(neg, "tcp", 22))
        anyport = conflicts.parse_rule("-A PREROUTING -d 203.0.113.1/32 -j DNAT --to-destination 10.0.0.1")
        self.assertTrue(conflicts.rule_matches(anyport, "udp", 1))
        rng = conflicts.parse_rule("-A PREROUTING -p tcp --dport 8000: -j DNAT --to-destination 10.0.0.1")
        self.assertTrue(conflicts.rule_matches(rng, "tcp", 65000))
        self.assertFalse(conflicts.rule_matches(rng, "tcp", 7999))


if __name__ == "__main__":
    unittest.main()
