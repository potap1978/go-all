"""migrate scan: classification table (SPEC §4.3) over every fixture, plus synthetic edge cases."""

import json

from kaskad import state
from kaskad.migrate import scan
from tests.helpers import RootCase
from tests.migrate import scripts
from tests.migrate.env import SECRETS, Env


def table(inv):
    return [(c.key_text, c.to, c.to_port, c.cls, c.status, c.reason) for c in inv.cands]


class FixtureClassificationTest(RootCase):
    def scan(self, scenario, **kw):
        env = Env(self, scenario, **kw).start()
        return env, scan.collect(env.runner)

    def test_free_v10(self):
        env, inv = self.scan("free_v10")
        self.assertEqual(table(inv), [
            ("udp:51820", "198.51.100.7", 51820, "free", "import", ""),
            ("tcp:443", "203.0.113.5", 443, "free", "import", "")])
        self.assertEqual((inv.edition, inv.version, inv.version_source), ("free", "1.x", "rules"))
        self.assertEqual([role for _r, role in inv.cands[0].assoc], ["input", "forward", "forward_reply"])
        self.assertEqual(inv.conflicts, [])
        self.assertEqual(env.runner.unmatched, [])

    def test_free_v11_two_rules_on_one_port_first_wins(self):
        _env, inv = self.scan("free_v11_two_rules")
        self.assertEqual(table(inv), [
            ("tcp:443", "203.0.113.5", 443, "free", "import", ""),
            ("tcp:443", "192.0.2.9", 443, "free", "duplicate", "second"),
            ("tcp:8443", "198.51.100.7", 443, "free", "import", "")])
        self.assertEqual((inv.edition, inv.version, inv.version_source), ("free", "1.1", "command"))

    def test_real_free_v11_capture(self):
        _env, inv = self.scan("real-free-v1.1")
        self.assertEqual(table(inv), [
            ("udp:51820", "198.51.100.10", 51820, "free", "import", ""),
            ("tcp:443", "198.51.100.20", 443, "free", "import", ""),
            ("tcp:8443", "198.51.100.20", 443, "free", "import", ""),
            ("udp:51820", "198.51.100.30", 51820, "free", "duplicate", "second")])
        # :443 and :8443 share one FORWARD pair (the free script deleted and re-added it)
        self.assertEqual([r.text for r, _ in inv.cands[1].assoc][1:], [r.text for r, _ in inv.cands[2].assoc][1:])
        self.assertEqual(inv.edition, "free")

    def test_pro_23_unlabeled_dnat(self):
        _env, inv = self.scan("pro_23")
        self.assertEqual(table(inv), [
            ("udp:51820", "198.51.100.7", 51820, "pro-legacy", "import", ""),
            ("tcp:443", "203.0.113.5", 443, "pro-legacy", "import", "")])
        self.assertEqual([c.name for c in inv.cands], ["Амстердам", "Вена"])
        self.assertEqual((inv.edition, inv.version), ("pro", "2.3"))
        self.assertEqual(inv.report()["masquerade"]["unlabeled"], ["-A POSTROUTING -o eth0 -j MASQUERADE"])

    def test_real_pro23_ufw(self):
        env, inv = self.scan("real-pro23-ufw", ufw_active=True)
        self.assertEqual(table(inv), [
            ("udp:51820", "198.51.100.10", 51820, "pro-legacy", "import", ""),
            ("tcp:443", "198.51.100.20", 443, "pro-legacy", "import", "")])
        self.assertEqual([c.name for c in inv.cands], ["Вена", "Амстердам"])     # 2.1 and 2.2 alias lines
        fw = inv.report()["firewall"]
        self.assertEqual((fw["ufw_active"], fw["ufw_forward_policy"], fw["forward_policy"]), (True, "DROP", "DROP"))
        self.assertEqual((inv.edition, inv.version, inv.version_source), ("pro", "2.0-2.4", "rules"))
        self.assertEqual(env.runner.unmatched, [])

    def test_pro_213(self):
        _env, inv = self.scan("pro_213", services={"kaskad-bot.service": ("enabled", "active")})
        self.assertEqual([(c.key_text, c.cls, c.status, c.name) for c in inv.cands], [
            ("udp:51820", "pro", "import", "Амстердам"), ("tcp:443", "pro", "import", "Вена"),
            ("tcp:8443", "pro", "import", "Амстердам")])
        rep = inv.report()
        self.assertEqual(rep["masquerade"]["kaskad"], ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])
        self.assertEqual(rep["config"], {"exists": True, "keys": {"BOT_TOKEN": True, "BOT_CHAT_ID": True,
                                                                  "MENU_STYLE": True, "GITHUB_PAT": True,
                                                                  "BOT_PROXY": False}})
        self.assertEqual(rep["egress"], {"keys": 2})
        self.assertEqual(rep["monitors"], ["198.51.100.7"])
        self.assertEqual(rep["services"]["kaskad-bot.service"], {"enabled": "enabled", "active": "active"})
        self.assertEqual(rep["services"]["docker.service"]["enabled"], "not-found")
        self.assertEqual((rep["edition"], rep["version"]), ("pro", "2.13"))
        self.assertEqual(rep["persisted"]["only_live"], [])
        self.assertEqual(rep["persisted"]["only_persisted"], [])
        dump = json.dumps(rep, ensure_ascii=False)
        for s in SECRETS:
            self.assertNotIn(s, dump)

    def test_real_pro213_over_free(self):
        _env, inv = self.scan("real-pro213-over-free")
        self.assertEqual([(c.key_text, c.to, c.cls, c.status) for c in inv.cands], [
            ("tcp:443", "198.51.100.20", "pro", "import"), ("tcp:8443", "198.51.100.20", "pro", "import"),
            ("udp:51820", "198.51.100.30", "pro", "import")])
        self.assertEqual((inv.edition, inv.version), ("pro", "2.13"))

    def test_pro_211_over_free_first_rule_wins(self):
        _env, inv = self.scan("pro_211_over_free")
        self.assertEqual(table(inv), [
            ("tcp:443", "192.0.2.9", 443, "free", "import", ""),
            ("udp:51820", "198.51.100.7", 51820, "free", "import", ""),
            ("tcp:443", "203.0.113.5", 443, "pro", "duplicate", "second"),
            ("tcp:8443", "198.51.100.7", 443, "pro", "import", "")])

    def test_docker_before_shadows(self):
        _env, inv = self.scan("docker_before")
        self.assertEqual(table(inv), [
            ("tcp:443", "203.0.113.5", 443, "pro", "import", ""),
            ("tcp:8080", "10.0.0.5", 80, "pro", "shadowed", "docker")])
        self.assertEqual(inv.conflicts, [])
        self.assertTrue(inv.report()["firewall"]["docker"])

    def test_docker_after_is_a_blocking_conflict(self):
        _env, inv = self.scan("docker_after")
        self.assertEqual([c.status for c in inv.cands], ["import", "import"])
        self.assertEqual([(cf["key"], cf["kind"], cf["blocking"]) for cf in inv.conflicts],
                         [("tcp:8080", "docker_port", True)])

    def test_foreign_rules(self):
        _env, inv = self.scan("foreign")
        self.assertEqual(table(inv), [
            ("tcp:443", "203.0.113.5", 443, "pro", "import", ""),
            ("tcp:465", "198.51.100.7", 465, "pro", "shadowed", "foreign"),
            ("udp:51820", "198.51.100.7", 51820, "free", "import", ""),
            ("tcp:8443", "198.51.100.7", 443, "pro", "import", ""),
            ("udp:1194", "127.0.0.1", 1194, "pro", "skipped", "target")])
        self.assertEqual(inv.cands[3].qualifiers, ["-i eth0"])
        self.assertIn("manual", inv.cands[3].warnings)
        self.assertEqual([(cf["key"], cf["kind"], cf["blocking"]) for cf in inv.conflicts],
                         [("tcp:443", "foreign_after", False)])
        fd = inv.report()["foreign_dnat"]
        self.assertEqual(len(fd), 6)
        self.assertFalse(any(f["docker"] for f in fd))

    def test_empty(self):
        env, inv = self.scan("empty")
        self.assertEqual(inv.cands, [])
        self.assertEqual(inv.edition, "none")
        self.assertEqual(env.runner.unmatched, [])


class EdgeCaseTest(RootCase):
    def env(self, **kw):
        return Env(self, "empty", **kw).start()

    def statuses(self, env):
        inv = scan.collect(env.runner)
        return [(c.key_text, c.to, c.status, c.reason) for c in inv.cands], inv

    def test_engine_nft_free_script_on_top(self):
        st = state.empty()
        st["engine"] = "nft"
        state.upsert_forward(st, {"proto": "tcp", "port": 443, "to": "203.0.113.5", "to_port": 443})
        state.save(st)
        env = self.env()
        scripts.free_add(env.ipt, "tcp", 443, 443, "192.0.2.9")      # same port as Kaskad 3
        scripts.free_add(env.ipt, "tcp", 8080, 80, "192.0.2.10")     # new port
        got, inv = self.statuses(env)
        self.assertEqual(got, [("tcp:443", "192.0.2.9", "duplicate", "k3"),
                               ("tcp:8080", "192.0.2.10", "import", "")])
        self.assertEqual(inv.edition, "k3")

    def test_narrowed_kaskad_rule_sharing_the_port_is_left_alone(self):
        env = self.env()
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-i", "eth0", "-p", "tcp", "--dport", "443", "-m", "comment",
                    "--comment", "kaskad:443:tcp", "-j", "DNAT", "--to-destination", "203.0.113.5:443")
        scripts.free_add(env.ipt, "tcp", 443, 443, "192.0.2.9")
        got, _ = self.statuses(env)
        self.assertEqual(got, [("tcp:443", "203.0.113.5", "skipped", "partial"),
                               ("tcp:443", "192.0.2.9", "skipped", "partial")])

    def test_transit_only_kaskad_rule_is_left_alone(self):
        env = self.env()
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "443", "-m", "addrtype", "!",
                    "--dst-type", "LOCAL", "-m", "comment", "--comment", "kaskad:443:tcp", "-j", "DNAT",
                    "--to-destination", "203.0.113.5:443")
        scripts.free_add(env.ipt, "tcp", 443, 443, "192.0.2.9")
        got, _ = self.statuses(env)
        self.assertEqual(got, [("tcp:443", "203.0.113.5", "skipped", "unreachable"),
                               ("tcp:443", "192.0.2.9", "import", "")])

    def test_jump_into_user_chain_with_dnat_shadows(self):
        env = self.env()
        env.ipt.cmd("-t", "nat", "-N", "MYNAT")
        env.ipt.cmd("-t", "nat", "-A", "MYNAT", "-p", "tcp", "--dport", "443", "-j", "DNAT",
                    "--to-destination", "10.9.9.9:443")
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-j", "MYNAT")
        scripts.pro213_add(env.ipt, "tcp", 443, 443, "203.0.113.5")
        got, inv = self.statuses(env)
        self.assertEqual(got, [("tcp:443", "203.0.113.5", "shadowed", "foreign")])
        self.assertIn("10.9.9.9", inv.cands[0].winner)

    def test_return_in_user_chain_hides_later_dnat(self):
        env = self.env()
        env.ipt.cmd("-t", "nat", "-N", "MYNAT")
        env.ipt.cmd("-t", "nat", "-A", "MYNAT", "-p", "tcp", "-j", "RETURN")
        env.ipt.cmd("-t", "nat", "-A", "MYNAT", "-p", "tcp", "--dport", "443", "-j", "DNAT",
                    "--to-destination", "10.9.9.9:443")
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-j", "MYNAT")
        scripts.pro213_add(env.ipt, "tcp", 443, 443, "203.0.113.5")
        got, _ = self.statuses(env)
        self.assertEqual(got, [("tcp:443", "203.0.113.5", "import", "")])

    def test_accept_in_nat_before_kaskad_shadows(self):
        env = self.env()
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-p", "udp", "--dport", "51820", "-j", "ACCEPT")
        scripts.free_add(env.ipt, "udp", 51820, 51820, "198.51.100.7")
        got, _ = self.statuses(env)
        self.assertEqual(got, [("udp:51820", "198.51.100.7", "shadowed", "foreign")])

    def test_transit_only_dnat_does_not_shadow(self):
        env = self.env()
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "443", "-m", "addrtype", "!",
                    "--dst-type", "LOCAL", "-j", "DNAT", "--to-destination", "10.9.9.9:443")
        scripts.free_add(env.ipt, "tcp", 443, 443, "203.0.113.5")
        got, _ = self.statuses(env)
        self.assertEqual(got, [("tcp:443", "203.0.113.5", "import", "")])

    def test_docker_published_without_jump_is_a_conflict(self):
        env = self.env()
        env.ipt.cmd("-t", "nat", "-N", "DOCKER")
        env.ipt.cmd("-t", "nat", "-A", "DOCKER", "!", "-i", "docker0", "-p", "tcp", "--dport", "8080", "-j", "DNAT",
                    "--to-destination", "172.17.0.2:80")
        scripts.pro213_add(env.ipt, "tcp", 8080, 80, "10.0.0.5")
        _got, inv = self.statuses(env)
        self.assertEqual([(cf["kind"], cf["blocking"]) for cf in inv.conflicts], [("docker_port", True)])

    def test_loopback_destination_is_no_coverage(self):
        from kaskad import ipt
        from kaskad.ipt import FULL, NONE, PARTIAL

        def cov(spec):
            return scan.coverage(ipt.Rule("-A DOCKER " + spec, "nat"), "tcp", 8080)
        self.assertEqual(cov("-d 127.0.0.1/32 ! -i docker0 -p tcp -m tcp --dport 8080 -j DNAT "
                             "--to-destination 172.17.0.2:80"), NONE)
        self.assertEqual(cov("-d 127.0.0.0/8 -p tcp -m tcp --dport 8080 -j DNAT --to-destination 10.0.0.1:80"), NONE)
        self.assertEqual(cov("! -d 127.0.0.0/8 -p tcp -m tcp --dport 8080 -j DNAT --to-destination 10.0.0.1:80"), FULL)
        self.assertEqual(cov("-d 10.8.0.1/32 -p tcp -m tcp --dport 8080 -j DNAT --to-destination 10.0.0.1:80"), PARTIAL)
        self.assertEqual(cov("! -d 10.8.0.1/32 -p tcp -m tcp --dport 8080 -j DNAT --to-destination 10.0.0.1:80"),
                         PARTIAL)

    def test_docker_port_published_on_loopback_does_not_shadow(self):
        env = self.env()
        env.ipt.cmd("-t", "nat", "-N", "DOCKER")
        env.ipt.cmd("-t", "nat", "-A", "DOCKER", "-d", "127.0.0.1/32", "!", "-i", "docker0", "-p", "tcp", "--dport",
                    "8080", "-j", "DNAT", "--to-destination", "172.17.0.2:80")
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-m", "addrtype", "--dst-type", "LOCAL", "-j", "DOCKER")
        scripts.pro213_add(env.ipt, "tcp", 8080, 80, "10.0.0.5")
        got, inv = self.statuses(env)
        self.assertEqual(got, [("tcp:8080", "10.0.0.5", "import", "")])
        self.assertEqual(inv.conflicts, [])

    def test_docker_port_published_on_loopback_without_jump_is_no_conflict(self):
        env = self.env()
        env.ipt.cmd("-t", "nat", "-N", "DOCKER")
        env.ipt.cmd("-t", "nat", "-A", "DOCKER", "-d", "127.0.0.1/32", "!", "-i", "docker0", "-p", "tcp", "--dport",
                    "8080", "-j", "DNAT", "--to-destination", "172.17.0.2:80")
        scripts.pro213_add(env.ipt, "tcp", 8080, 80, "10.0.0.5")
        got, inv = self.statuses(env)
        self.assertEqual(got, [("tcp:8080", "10.0.0.5", "import", "")])
        self.assertEqual(inv.conflicts, [])

    def test_partial_foreign_rule_before_kaskad_is_not_called_dead(self):
        from kaskad.migrate import plan
        env = self.env()
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-s", "10.0.0.0/8", "-p", "tcp", "--dport", "443", "-j", "DNAT",
                    "--to-destination", "10.9.9.9:443")
        scripts.free_add(env.ipt, "tcp", 443, 443, "203.0.113.5")
        scripts.free_add(env.ipt, "tcp", 443, 443, "192.0.2.9")       # after the first Kaskad rule: dead
        got, inv = self.statuses(env)
        self.assertEqual(got, [("tcp:443", "203.0.113.5", "skipped", "partial_before"),
                               ("tcp:443", "192.0.2.9", "skipped", "key_kept")])
        self.assertEqual(inv.cands[0].before, "-s 10.0.0.0/8")
        self.assertEqual(inv.cands[0].to_dict()["condition_before"], "-s 10.0.0.0/8")
        pl = plan.build(inv)
        text = pl.preview()
        self.assertIn("tcp :443 → 203.0.113.5:443 — работает не для всего трафика (раньше стоит правило с условием "
                      "-s 10.0.0.0/8) — оставлю как есть.", text)
        self.assertNotIn("не работало", text)
        self.assertEqual(pl.removed_rules, [])                         # left exactly as is
        self.assertEqual(pl.imports, [])

    def test_full_foreign_rule_after_a_partial_one_still_shadows(self):
        env = self.env()
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-s", "10.0.0.0/8", "-p", "tcp", "--dport", "443", "-j", "DNAT",
                    "--to-destination", "10.9.9.9:443")
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "443", "-j", "DNAT",
                    "--to-destination", "10.8.8.8:443")
        scripts.free_add(env.ipt, "tcp", 443, 443, "203.0.113.5")
        got, inv = self.statuses(env)
        self.assertEqual(got, [("tcp:443", "203.0.113.5", "shadowed", "foreign")])
        self.assertIn("10.8.8.8", inv.cands[0].winner)

    def test_not_kaskad(self):
        env = self.env()
        # label does not match the port
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "443", "-m", "comment", "--comment",
                    "kaskad:444:tcp", "-j", "DNAT", "--to-destination", "203.0.113.5:443")
        # unlabeled simple DNAT without a FORWARD pair
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "2222", "-j", "DNAT",
                    "--to-destination", "10.0.0.5:22")
        # FORWARD pair with a foreign comment
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "8080", "-j", "DNAT",
                    "--to-destination", "10.0.0.6:80")
        env.ipt.cmd("-A", "FORWARD", "-d", "10.0.0.6", "-p", "tcp", "--dport", "80", "-m", "comment", "--comment",
                    "mine", "-j", "ACCEPT")
        got, inv = self.statuses(env)
        self.assertEqual(got, [])
        self.assertEqual(len(inv.foreign_dnat()), 3)

    def test_stale_table_with_engine_legacy_blocks(self):
        env = self.env()
        env.nft.table = {"forwards": {("tcp", 443): ("203.0.113.5", 443)}, "counters": {},
                         "has_jump": True, "has_masquerade": True}
        _got, inv = self.statuses(env)
        self.assertEqual([cf["kind"] for cf in inv.conflicts], ["stale_table"])

    def test_mixed_backend_warning(self):
        env = self.env()
        env.ipt.inject(lambda argv, inp: (0, "-P INPUT ACCEPT\n# Warning: iptables-legacy tables present, use "
                                             "iptables-legacy to see them\n", "")
                       if argv[-1] == "-S" and "filter" in argv else None)
        _got, inv = self.statuses(env)
        self.assertTrue(inv.mixed)
        self.assertTrue(any("Смешанный" in w for w in inv.warnings))

    def test_mixed_backend_by_legacy_tables(self):
        env = self.env()
        self.write("/proc/net/ip_tables_names", "nat\nfilter\n")
        env.runner.binaries.add("iptables-legacy-save")
        env.runner.on(["iptables-legacy-save"], "*nat\n-A PREROUTING -p tcp -j ACCEPT\nCOMMIT\n")
        _got, inv = self.statuses(env)
        self.assertTrue(inv.mixed)
        self.assertEqual(env.runner.unmatched, [])

    def test_no_iptables_at_all(self):
        env = Env(self, None, iptables=False).start()
        inv = scan.collect(env.runner)
        self.assertEqual((inv.cands, inv.backend["backend"]), ([], "none"))
        self.assertEqual(env.runner.unmatched, [])

    def test_edition_from_gokaskad_bak_when_command_is_3x(self):
        env = Env(self, "pro_213").start()
        self.write("/usr/local/bin/gokaskad", '#!/bin/bash\nKASKAD_VERSION="3.0.1"\n')
        self.write("/usr/local/bin/gokaskad.bak", '#!/bin/bash\nKASKAD_VERSION="2.12"\n')
        inv = scan.collect(env.runner)
        self.assertEqual((inv.edition, inv.version, inv.version_source), ("pro", "2.12", "command.bak"))

    def test_edition_fresh_3x_install(self):
        env = self.env()
        self.write("/usr/local/bin/gokaskad", '#!/bin/bash\nKASKAD_VERSION="3.0.1"\n')
        self.write("/etc/kaskad/config", 'BOT_TOKEN=""\n')
        inv = scan.collect(env.runner)
        self.assertEqual((inv.edition, inv.version), ("k3", "3.0.1"))

    def test_broken_state_is_reported(self):
        self.write("/etc/kaskad/state.json", '{"schema": 99}')
        env = self.env()
        inv = scan.collect(env.runner)
        self.assertIn("схема", inv.state_error)
        self.assertEqual(inv.engine, "legacy")

    def test_forward_keeps_established(self):
        got = {}
        for sc in ("real-pro23-ufw", "docker_before", "pro_213", "foreign"):
            self.tearDown()
            self.setUp()
            env = Env(self, sc).start()
            got[sc] = scan.collect(env.runner).report()["firewall"]["forward_keeps_established"]
        # UFW: ufw-before-forward accepts RELATED,ESTABLISHED; Docker: only for -o docker0
        self.assertEqual(got, {"real-pro23-ufw": True, "docker_before": False, "pro_213": True, "foreign": False})

    def test_listeners(self):
        env = Env(self, "real-pro23-ufw").start()
        inv = scan.collect(env.runner)
        self.assertIn({"proto": "tcp", "address": "*", "port": 22, "process": "sshd"}, inv.listeners)
        self.assertIn({"proto": "udp", "address": "127.0.0.53%lo", "port": 53, "process": "systemd-resolve"},
                      inv.listeners)
