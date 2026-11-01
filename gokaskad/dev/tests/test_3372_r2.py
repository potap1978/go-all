"""3.3.7.2, круг 2 (SEC-3372, REV-3372, T-KT3 КТ3-2): порты двух dnsmasq закрыты клиентам мимо порта 53; откат
восстановления при любой ошибке; ключи ИИ-агентов из копии — только отметкой; прямой DNS — системные резолверы узла;
жизненный цикл kaskad-gw-dnsdirect (stop, remove, сторож); сброс conntrack DNS при уходе с «напрямую»; kaskad-dns при
нечитаемом пуле; U+2028 и соседи в /etc/kaskad/config."""

import json
import os
import subprocess
import types
from unittest import mock

from kaskad import backup, paths, state, util
from kaskad.bot import config as bcfg
from kaskad.errors import KaskadError
from kaskad.gw import dns, exits, net, ops
from kaskad.gw import sys as gsys
from tests.fakes_gw import VLESS_A, install_parser, write_pool
from tests.fakes_nft import F443, save_state
from tests.gwcase import GwCase
from tests.test_backup import PW, TOKEN, PAT, BackupCase
from tests.test_gw_ops import CliGwCase

DROP = "ip daddr 10.77.0.1 meta l4proto { tcp, udp } th dport { 5354, 5364 } drop"


class DnsPortsClosedTest(GwCase):
    """SEC-3372 З-1: a client asks 10.77.0.1:5364 (kaskad-gw-dnsdirect, the direct upstream) itself — dropped in gw_pre
    before ``fib daddr type local … return`` (no UFW needed); port 53 (gw_dns DNATs it after gw_pre) is untouched."""

    def test_the_drop_stands_between_the_dns_return_and_the_local_return(self):
        st = self.scenario(save=False)
        pre = dict(net.rules(st))["gw_pre"]
        self.assertEqual(pre.count(DROP), 1)
        i = pre.index(DROP)
        self.assertLess(pre.index("ip saddr != @gw_nets return"), i)          # clients only
        self.assertLess(pre.index("ip daddr 10.77.0.1 meta l4proto { tcp, udp } th dport 53 ct mark set ct mark or "
                                  "0x4b000000 return"), i)
        local = [n for n, r in enumerate(pre) if r.startswith("fib daddr type local")]
        self.assertTrue(local and i < local[0])

    def test_ports_follow_the_constants(self):
        st = self.scenario(save=False)
        with mock.patch.object(dns, "PORT_DIRECT", 5399):
            pre = dict(net.rules(st))["gw_pre"]
        self.assertIn(DROP.replace("5364", "5399"), pre)


class SystemResolversTest(GwCase):
    """КТ3-2: «напрямую» = как у самого сервера — kaskad-gw-dnsdirect спрашивает системные резолверы узла (без петли
    127.0.0.53), 1.1.1.1 / 1.0.0.1 — запасом после них."""

    def test_the_resolvers_of_systemd_resolved_first_then_the_reserve(self):
        st = self.scenario(save=False)
        self.write("/etc/resolv.conf", "nameserver 127.0.0.53\noptions edns0 trust-ad\n", 0o644)
        self.write("/run/systemd/resolve/resolv.conf",
                   "# This is /run/systemd/resolve/resolv.conf\nnameserver 77.88.8.8\nnameserver 127.0.0.1\n"
                   "nameserver fe80::1%eth0\nnameserver 10.77.0.1\nnameserver 2a02:6b8::feed:0ff\n"
                   "nameserver 77.88.8.8\nnameserver 77.88.8.1 ; tail\nnameserver 192.0.2.53\nsearch example.com\n",
                   0o644)
        self.assertEqual(dns.system_servers(st), ["77.88.8.8", "2a02:6b8::feed:ff", "77.88.8.1"])   # at most 3
        text = dns.direct_text_of(st, True)
        self.assertIn("strict-order\nserver=127.0.0.1#10853\nserver=77.88.8.8\nserver=2a02:6b8::feed:ff\n"
                      "server=77.88.8.1\nserver=1.1.1.1\nserver=1.0.0.1\n", text)
        self.assertNotIn("127.0.0.53", text)
        self.assertIn("kaskad-gw-dnsdirect.service", text.splitlines()[0])         # REV-3372 П-3: its own header
        self.assertIn("5364", text.splitlines()[1])
        # kaskad-dns never gets them
        self.assertFalse([ln for ln in dns.text_of(st, True)[0].splitlines()
                          if ln.startswith("server=") and ln != "server=127.0.0.1#10853"])

    def test_etc_resolv_conf_when_resolved_names_nothing(self):
        st = self.scenario(save=False)
        self.write("/run/systemd/resolve/resolv.conf", "nameserver 127.0.0.53\n", 0o644)
        self.write("/etc/resolv.conf", "nameserver 8.8.4.4\nnameserver 1.1.1.1\n", 0o644)
        self.assertEqual(dns.direct_servers(st), ["8.8.4.4", "1.1.1.1", "1.0.0.1"])     # no 1.1.1.1 twice

    def test_only_the_loop_or_nothing_leaves_the_reserve(self):
        st = self.scenario(save=False)
        self.assertEqual(dns.direct_servers(st), ["1.1.1.1", "1.0.0.1"])            # no files at all
        self.write("/etc/resolv.conf", "nameserver 127.0.0.53\n", 0o644)
        self.assertEqual(dns.direct_servers(st), ["1.1.1.1", "1.0.0.1"])

    def test_no_line_of_the_file_reaches_the_config_but_an_address(self):
        st = self.scenario(save=False)
        self.write("/etc/resolv.conf", "nameserver 9.9.9.9\nserver=/example.com/203.0.113.9\n"
                   "nameserver 203.0.113.7\\nserver=8.8.8.8\nnameserver ١.١.١.١\nnameserver 9.9.9.9 x\n", 0o644)
        self.assertEqual(dns.system_servers(st), ["9.9.9.9"])
        self.assertNotIn("203.0.113", dns.direct_text_of(st, False))

    def test_apply_writes_them_and_rewrites_when_they_change(self):
        st = self.scenario(save=False)
        self.write("/run/systemd/resolve/resolv.conf", "nameserver 77.88.8.8\n", 0o644)
        res = dns.apply(st, self.w.runner, via_pool=True, direct=True)
        self.assertEqual(res["direct_servers"], ["127.0.0.1#10853", "77.88.8.8", "1.1.1.1", "1.0.0.1"])
        self.write("/run/systemd/resolve/resolv.conf", "nameserver 77.88.8.1\n", 0o644)
        res = dns.apply(st, self.w.runner, via_pool=True, direct=True)
        self.assertTrue(res["direct"]["changed"])
        self.assertIn("server=77.88.8.1\n", self.read("/etc/kaskad/gw/dnsmasq-direct.conf"))


class DnsDirectLifeTest(CliGwCase):
    """REV-3372 З-1: kaskad-gw-dnsdirect — gw disable stops it, removal takes the unit and the config, the watchdog
    restarts a stopped one."""

    def setUp(self):
        super().setUp()
        save_state([F443])
        install_parser()
        write_pool([VLESS_A])
        self.ok("gw", "enable")
        self.st = state.load()
        dns.apply(self.st, self.w.runner, via_pool=True, direct=True)
        self.assertTrue(gsys.unit_active(self.w.runner, dns.UNIT_DIRECT))

    def test_stop_stops_and_disables_it(self):
        dns.stop(self.w.runner)
        u = self.w.sysd.units[dns.UNIT_DIRECT]
        self.assertEqual((u["active"], u["enabled"]), (False, False))
        self.assertTrue(os.path.exists(self.path("/etc/kaskad/gw/dnsmasq-direct.conf")))   # stop is not removal

    def test_gw_disable_stops_it(self):
        self.ok("gw", "disable")
        self.assertFalse(gsys.unit_active(self.w.runner, dns.UNIT_DIRECT))

    def test_remove_takes_the_unit_and_the_config(self):
        dns.remove(self.w.runner)
        self.assertFalse(os.path.exists(self.path("/etc/systemd/system/kaskad-gw-dnsdirect.service")))
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/gw/dnsmasq-direct.conf")))
        self.assertFalse(gsys.unit_active(self.w.runner, dns.UNIT_DIRECT))

    def test_the_watchdog_restarts_a_stopped_one(self):
        self.w.sysd.units[dns.UNIT_DIRECT]["active"] = False
        probs = [p for p in ops.check(self.st, self.w.runner) if dns.UNIT_DIRECT in (p.get("detail") or "")]
        self.assertTrue(probs, "gw check does not see a stopped kaskad-gw-dnsdirect")
        ops.check(self.st, self.w.runner, repair=True)
        self.assertTrue(gsys.unit_active(self.w.runner, dns.UNIT_DIRECT))

    def test_kaskad_dns_is_rewritten_while_the_pool_is_unreadable(self):
        """SEC-3372 П-2: an update with a pool that cannot be read — kaskad-dns of 3.3.7.1 (1.1.1.1 for everyone)
        does not stay: the pool only."""
        util.atomic_write(dns.conf_path(), dns.conf_text(self.st, True, True), 0o644)        # as 3.3.7.1 wrote it
        self.assertIn("server=1.1.1.1", self.read("/etc/kaskad/gw/dnsmasq.conf"))
        with mock.patch.object(exits, "pool", side_effect=KaskadError("parser broken")):
            self.run_cli("gw", "apply")
        text = self.read("/etc/kaskad/gw/dnsmasq.conf")
        self.assertNotIn("1.1.1.1", text)
        self.assertEqual([ln for ln in text.splitlines() if ln.startswith("server=")], ["server=127.0.0.1#10853"])


class DirectDnsFlowsTest(GwCase):
    """SEC-3372 П-1: the DNAT of gw_dns is per connection — after a change of gw_route the flows to the direct dnsmasq
    of a device no longer «напрямую» are forgotten; those of a device still «напрямую» stay."""

    def setUp(self):
        super().setUp()
        self.write("/etc/systemd/system/kaskad-gw-dnsdirect.service", "[Unit]\n", 0o644)

    def test_no_direct_dnsmasq_no_conntrack_run(self):
        st = self.scenario(save=False)
        os.unlink(self.path("/etc/systemd/system/kaskad-gw-dnsdirect.service"))
        self.assertEqual(net.flush_direct_dns(self.w.runner, st, {}), 0)
        self.assertEqual(self.w.conntrack.calls, [])

    def test_only_the_flows_of_devices_off_direct_go(self):
        st = self.scenario(save=False)
        ct = self.w.conntrack
        ct.dns_flows = {("10.77.1.10", "udp"): 2, ("10.77.1.11", "udp"): 1, ("10.77.1.11", "tcp"): 1,
                        ("198.51.100.1", "udp"): 1}
        n = net.flush_direct_dns(self.w.runner, st, {"10.77.1.10": exits.MARK_DIRECT, "10.77.1.11": 0x102})
        self.assertEqual(n, 2)
        self.assertEqual(sorted(ct.dns_flows), [("10.77.1.10", "udp"), ("198.51.100.1", "udp")])
        self.assertIn(["-D", "-s", "10.77.1.11", "-p", "tcp", "--reply-port-src", "5364"], ct.calls)

    def test_a_change_of_the_map_sweeps_them(self):
        from kaskad import nft as knft
        st = self.scenario()
        knft.apply(st, self.w.runner)
        ip = sorted(net.map_elems(knft.read_live(self.w.runner)))[0]
        net.update_map(self.w.runner, st, elems={ip: exits.MARK_DIRECT})
        self.w.conntrack.dns_flows = {(ip, "udp"): 1}
        self.assertFalse(net.update_map(self.w.runner, st, elems={ip: exits.MARK_DIRECT}))   # no change: no sweep
        self.assertEqual(self.w.conntrack.dns_flows, {(ip, "udp"): 1})
        self.assertTrue(net.update_map(self.w.runner, st, elems={ip: exits.MARK_BLOCK}))
        self.assertEqual(self.w.conntrack.dns_flows, {})

    def test_without_conntrack_nothing(self):
        st = self.scenario(save=False)
        self.w.runner.binaries.discard("conntrack")
        self.assertIsNone(net.flush_direct_dns(self.w.runner, st, {}))


class BackupRollbackAnyErrorTest(BackupCase):
    """SEC-3372 З-2: a copy of an unexpected shape (``"forwards": ["x"]`` — AttributeError in the middle) is rolled
    back like any failure: nothing of the copy stays on the node (the bot token, the GitHub key, the keys of agents)."""

    def test_forwards_of_strings_roll_everything_back(self):
        self.old_node()
        doc = backup.payload(self.w.runner, backup.PARTS)
        st = json.loads(doc["files"][backup.F_STATE])
        st["forwards"] = ["x"]
        doc["files"][backup.F_STATE] = json.dumps(st)
        self.new_node()
        before = self.read("/etc/kaskad/config")
        with self.assertRaises(KaskadError) as c:
            backup.apply(self.w.runner, doc, agents=True)
        self.assertIn("Вернул как было", str(c.exception))
        self.assertIn("AttributeError", str(c.exception))                     # what broke, in the person's words
        cfg = self.read("/etc/kaskad/config")
        self.assertIn('BOT_TOKEN=""', cfg)                                    # the rollback: as before the import
        self.assertIn('BOT_TOKEN=""', before)
        self.assertNotIn(TOKEN, cfg)
        self.assertNotIn(PAT, cfg)
        self.assertIn("AttributeError", util.read_text(paths.P("/var/log/kaskad.log")) or "")


class BackupAgentKeysTest(BackupCase):
    """SEC-3372 З-3 (решение координатора): ключи ИИ-агентов из копии по умолчанию не переносятся — только отметкой
    (панель «agents»: true, kaskadctl --agents, вопрос меню); «Вернуть как было» — как прежде, точно."""

    def _copy(self):
        from kaskad.mcp import keys
        from tests.test_333d_backend import ed25519
        self.old_node()
        line, fp = ed25519(7)
        keys.add(line, label="laptop")
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        keys.delete(fp, self.w.runner)
        return keys, fp, backup.unseal(self.w.runner, text, PW)

    def test_not_by_default(self):
        keys, fp, doc = self._copy()
        pv = backup.preview(self.w.runner, doc)
        row = [r for r in pv["parts"] if r["part"] == "mcp"][0]
        self.assertEqual(row["action"], "none")
        self.assertEqual(row["text"], backup.tr("backup.row.mcp_off", keys=1))
        self.assertEqual(pv["agents"], {"keys": 1, "on": False})
        res = backup.apply(self.w.runner, doc)
        self.assertNotIn("mcp", res["parts"])
        self.assertNotIn(fp, json.dumps(keys.listing()["keys"]))

    def test_only_by_the_tick_with_the_danger_said(self):
        keys, fp, doc = self._copy()
        pv = backup.preview(self.w.runner, doc, agents=True)
        self.assertEqual([r["action"] for r in pv["parts"] if r["part"] == "mcp"], ["add"])
        self.assertIn(backup.tr("backup.preview.mcp_on", n=1), pv["warnings"])
        res = backup.apply(self.w.runner, doc, agents=True)
        self.assertIn("mcp", res["parts"])
        self.assertIn(fp, json.dumps(keys.listing()["keys"]))

    def test_the_surfaces_pass_the_tick_only_when_given(self):
        from kaskad import cli_backup
        from kaskad.panel import api
        self.assertFalse(api._backup_agents({}))
        self.assertFalse(api._backup_agents({"agents": "true"}))
        self.assertFalse(api._backup_agents({"agents": 1}))
        self.assertTrue(api._backup_agents({"agents": True}))
        got = []
        with mock.patch.object(backup, "apply", lambda *a, **kw: got.append(kw.get("agents")) or
                               {"text": [], "parts": []}), \
                mock.patch.object(cli_backup, "_read_file", lambda arg: ("x", True)), \
                mock.patch.object(cli_backup, "_password", lambda **kw: PW), \
                mock.patch.object(cli_backup, "_r", lambda: None), \
                mock.patch.object(backup, "unseal", lambda *a: {}), \
                mock.patch.object(cli_backup, "emit", lambda *a, **kw: None):
            for argv in (["import", "f.kbk", "--yes"], ["import", "f.kbk", "--yes", "--agents"]):
                cli_backup.cmd_import(cli_backup_args(argv))
        self.assertEqual(got, [False, True])


def cli_backup_args(argv):
    """``kaskadctl backup <argv>`` through the real parser of the command."""
    import argparse
    from kaskad import cli_backup
    p = argparse.ArgumentParser()
    cli_backup.register(p.add_subparsers())
    return p.parse_args(["backup"] + argv)


class ConfigLineBreaksTest(BackupCase):
    """SEC-3372 П-3: U+2028/U+2029/U+0085 in a value — never a line of its own after the next rewrite (bash sources
    /etc/kaskad/config and splits by «\\n» only)."""

    def test_two_writes_never_make_a_command(self):
        cfg = paths.config()
        scratch = self.path("/tmp/pwned")
        for ch in (" ", " ", "\x85", "\x1c"):
            bcfg.save_value("BOT_PROXY", "x%stouch %s #" % (ch, scratch), cfg)
            self.assertNotIn(ch, util.read_text(cfg), repr(ch))                   # cut from the value itself
            bcfg.save_value("BOT_PROXY", "", cfg)
            bcfg.save_value("MENU_STYLE", "inline", cfg)
            backup._set_config({"KASKAD_TZ": "UTC%stouch %s" % (ch, scratch)})
            backup._set_config({"BOT_CHAT_ID": "42"})
            subprocess.run(["bash", "-c", 'source "$1"', "_", cfg], capture_output=True, timeout=30)
            self.assertFalse(os.path.exists(scratch), repr(ch))
            text = util.read_text(cfg)
            self.assertFalse(any(c in text for c in (" ", " ", "\x85", "\x1c")), repr(ch))

    def test_a_line_bash_sees_as_one_is_kept_as_one(self):
        cfg = paths.config()
        util.atomic_write(cfg, 'A="1 2"\nB="3"\n', 0o600)
        bcfg.save_value("B", "4", cfg)
        self.assertEqual(util.read_text(cfg), 'A="1 2"\nB="4"\n')


class AsciiDigitsTest(GwCase):
    """SEC-3372 П-4, REV-3372 П-1: the core reads a monitor as bash does — ASCII digits only, the whole value."""

    def test_arabic_digits_and_a_trailing_newline_are_refused(self):
        from kaskad.bot import data, monitors
        self.assertFalse(data.valid_ip("\u0661.\u0662.\u0663.\u0664"))
        self.assertFalse(data.valid_ip("1.2.3.4\n5"))
        self.assertTrue(data.valid_ip("1.2.3.4"))
        self.assertIsNone(monitors.parse('MON_IP="1.2.3.4"\nMON_INTERVAL=\u0666\u0660\nMON_THRESHOLD=100\n'))
        self.assertIsNone(monitors._int("6\u0660", 1, 100))
        self.assertEqual(monitors.parse('MON_IP="1.2.3.4"\nMON_INTERVAL=60\nMON_THRESHOLD=100\n')["interval"], 60)


class DirectPortProbeTest(GwCase):
    """REV-3372 П-4: ``gw probe`` says it when 5364 (kaskad-gw-dnsdirect) is taken on the gateway's address."""

    def test_a_foreign_listener_on_5364(self):
        st = self.scenario(save=False)
        self.w.ss = ('udp   UNCONN 0      0            0.0.0.0:5364       0.0.0.0:*    users:(("adguardhome",pid=900,fd=12))\n')
        out = dns.probe(self.w.runner, st)
        self.assertEqual([l["port"] for l in out["busy_direct"]], [5364])
        self.assertIn("5364", out["warning"])
        self.assertIn("adguardhome", out["warning"])
        self.w.ss = ""
        self.assertNotIn("busy_direct", dns.probe(self.w.runner, st))
