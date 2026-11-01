"""F2 — findings of the sysadmin critic on 3.1.0:

    [3] the gateway's DNS next to a foreign resolver on *:53 — dnsmasq on its own port, the clients' DNS DNATed
        (chain gw_dns), the update from 3.1.0, enable/probe/check say when kaskad-dns does not work.
    [R-perf] the collector (every 10 s) and ``gw check`` never dump the whole table (gw_ru: ~15 k ranges).
    [8] a change of the gateway is finished whatever SIGHUP, SIGINT or SIGTERM come; a closed terminal loses text,
        not the change; the menu runs kaskadctl with SIGHUP ignored.
    [5] UFW enabled or reset after the inbounds: the watchdog and apply put their ports back.
"""

import errno
import json
import os
import re
import signal
import subprocess
import sys
from unittest import mock

from kaskad import cli, netcheck, nft, state
from kaskad.errors import KaskadError
from kaskad.gw import collect, dns, model, net, peek
from kaskad.gw import sys as gsys
from kaskad.gw.inbounds import tunnel
from tests.fakes_gw import INSTALL_SH
from tests.fakes_nft import F443, save_state
from tests.test_gw_integration import RealAdapterCase
from tests.test_gw_ops import CliGwCase

ADGUARD_5354 = ('udp   UNCONN 0      0            0.0.0.0:5354       0.0.0.0:*    users:(("adguardhome",pid=900,fd=12))\n'
                'tcp   LISTEN 0      4096         0.0.0.0:5354       0.0.0.0:*    users:(("adguardhome",pid=900,fd=13))\n')
ADGUARD_53 = ('udp   UNCONN 0      0            0.0.0.0:53         0.0.0.0:*    users:(("AdGuardHome",pid=900,fd=12))\n'
              'tcp   LISTEN 0      4096               *:53               *:*    users:(("AdGuardHome",pid=900,fd=13))\n')
OURS_5354 = ('udp   UNCONN 0      0          10.77.0.1:5354       0.0.0.0:*    users:(("dnsmasq",pid=700,fd=4))\n'
             'tcp   LISTEN 0      32         10.77.0.1:5354       0.0.0.0:*    users:(("dnsmasq",pid=700,fd=5))\n')
# 3.3.7.2 (SEC-3371 Б-2): 0x201 of gw_route -> kaskad-gw-dnsdirect (5364), the rest -> kaskad-dns (5354)
DNAT = [
    "ip saddr @gw_nets ip daddr 10.77.0.1 meta l4proto { tcp, udp } th dport 53 meta mark set ip saddr map @gw_route",
    "ip saddr @gw_nets ip daddr 10.77.0.1 meta mark 0x201 udp dport 53 meta mark set 0x0 dnat to 10.77.0.1:5364",
    "ip saddr @gw_nets ip daddr 10.77.0.1 meta mark 0x201 tcp dport 53 meta mark set 0x0 dnat to 10.77.0.1:5364",
    "ip saddr @gw_nets ip daddr 10.77.0.1 udp dport 53 meta mark set 0x0 dnat to 10.77.0.1:5354",
    "ip saddr @gw_nets ip daddr 10.77.0.1 tcp dport 53 meta mark set 0x0 dnat to 10.77.0.1:5354"]
OLD_HEADER = "# Kaskad 3: DNS шлюза (kaskad-dns.service). Файл пишет kaskadctl, правки руками затрутся."


def old_unit(conf):
    """kaskad-dns.service exactly as 3.1.0 wrote it."""
    return "\n".join(["[Unit]", "Description=Kaskad 3: DNS шлюза (dnsmasq на адресе шлюза)",
                      "After=network.target kaskad-net.service", "", "[Service]", "Type=simple",
                      "ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file=%s" % conf,
                      "ExecReload=/bin/kill -HUP $MAINPID", "Restart=on-failure", "RestartSec=5", "",
                      "[Install]", "WantedBy=multi-user.target", ""])


class DnsPortTest(CliGwCase):
    """[3]: dnsmasq of the gateway on 10.77.0.1:5354, the clients' 10.77.0.1:53 DNATed to it."""

    def as_310(self):
        """The node as 3.1.0 left it: dnsmasq on :53 (config and unit of 3.1.0), no chain gw_dns."""
        text = self.read("/etc/kaskad/gw/dnsmasq.conf")
        body = [ln for ln in text.splitlines() if not ln.startswith("#") and not ln.startswith("port=")]
        self.write("/etc/kaskad/gw/dnsmasq.conf", "\n".join([OLD_HEADER] + body) + "\n", 0o644)
        self.write("/etc/systemd/system/kaskad-dns.service", old_unit(dns.conf_path()), 0o644)
        self.assertEqual(self.w.nft.run_script("flush chain ip kaskad gw_dns\ndelete chain ip kaskad gw_dns\n",
                                               True)[0], 0)
        self.assertEqual(sorted(dns.stale(self.w.runner)), ["config", "unit"])
        return body

    def test_enable_puts_dnsmasq_on_its_port_and_the_dnat_in_the_table(self):
        self.enabled_world()
        conf = self.read("/etc/kaskad/gw/dnsmasq.conf")
        self.assertIn("\nport=5354\nbind-interfaces\nlisten-address=10.77.0.1\nno-resolv\n", conf)
        unit = self.read("/etc/systemd/system/kaskad-dns.service")
        self.assertIn("Type=forking\nPIDFile=/run/kaskad-dns.pid\n", unit)
        self.assertNotIn("--keep-in-foreground", unit)
        t = self.w.nft.table()
        self.assertEqual(t["chains"]["gw_dns"]["hook"],
                         {"type": "nat", "hook": "prerouting", "prio": -102, "policy": "accept"})
        self.assertEqual(self.w.nft.rules("gw_dns"), DNAT)                 # udp and tcp, no interface: PPP, IKEv2
        self.assertLess(t["chains"]["gw_dns"]["hook"]["prio"], t["chains"]["fwd_pre"]["hook"]["prio"])
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])
        self.assertIn("chain gw_dns {", self.read("/etc/kaskad/net/kaskad.nft"))   # the boot file too
        self.assertEqual(dns.stale(self.w.runner), [])
        self.assertEqual(dns.status(self.w.runner)["port"], 5354)

    def test_disable_takes_the_chain_away(self):
        self.enabled_world()
        self.ok("gw", "disable")
        t = self.w.nft.table()
        self.assertNotIn("gw_dns", t["chains"])
        self.assertEqual([n for n in t["sets"] if n.startswith("gw_")], [])     # gw_nets is not held by gw_dns

    def test_a_foreign_resolver_on_53_does_not_matter(self):
        self.w.ss = ADGUARD_53
        self.enabled_world()
        rc, doc, err = self.run_json("gw", "probe")
        self.assertEqual((rc, doc["supported"], doc["dns"]["busy"], doc["dns"]["warning"]), (0, True, [], ""))
        rc, doc, err = self.run_json("gw", "check")
        self.assertEqual((rc, doc["problems"]), (0, []), err)

    def test_a_port_it_cannot_take_is_said_by_enable_check_and_probe(self):
        self.w.ss = ADGUARD_5354
        self.w.sysd.fail_units["kaskad-dns.service"] = (
            "Job for kaskad-dns.service failed because the control process exited with error code.\n")
        rc, doc, _ = self.run_json("gw", "probe")
        self.assertTrue(doc["supported"])                                    # the rest of the gateway works
        self.assertEqual(doc["dns"]["busy"][0]["procs"], ["adguardhome"])
        self.assertIn("5354", doc["dns"]["warning"])
        st = self.enabled_world()
        self.assertTrue(st["gateway"]["enabled"])
        rc, out, err = self.run_cli("gw", "apply")
        self.assertEqual(rc, 0)
        self.assertIn("Внимание: DNS шлюза (kaskad-dns.service) не запустился: порт 5354 на 10.77.0.1 занят: "
                      "adguardhome (0.0.0.0:5354/udp); adguardhome (0.0.0.0:5354/tcp)", err)
        rc, doc, _ = self.run_json("gw", "check")
        details = [p["detail"] for p in doc["problems"]]
        self.assertNotEqual(rc, 0)
        self.assertIn("DNS шлюза (kaskad-dns.service) не работает", details)
        self.assertTrue(any(d.startswith("DNS шлюза (kaskad-dns.service) не запускается: порт 5354") and "adguardhome"
                            in d for d in details), details)

    def test_the_reason_from_the_journal(self):
        self.enabled_world()
        self.w.sysd.fail_units["kaskad-dns.service"] = "Job for kaskad-dns.service failed.\n"
        self.w.journal["kaskad-dns.service"] = (
            "Starting kaskad-dns.service - Kaskad 3: DNS шлюза...\n"
            "dnsmasq: failed to create listening socket for 10.77.0.1: Cannot assign requested address\n"
            "kaskad-dns.service: Control process exited, code=exited, status=2/INVALIDARGUMENT\n")
        with self.assertRaises(KaskadError) as cm:
            dns.apply(state.load(), self.w.runner, via_pool=False, direct=True)
        self.assertIn("не запустился: dnsmasq: failed to create listening socket for 10.77.0.1: Cannot assign",
                      str(cm.exception))

    def test_probe_does_not_count_our_own_dnsmasq(self):
        self.enabled_world()
        self.w.ss = OURS_5354
        rc, doc, _ = self.run_json("gw", "probe")
        self.assertEqual(doc["dns"]["busy"], [])

    def test_gw_apply_after_the_update_moves_the_dns(self):
        self.enabled_world()
        body = self.as_310()
        before = self.w.sysd.restarts.get("kaskad-dns.service", 0)
        self.ok("gw", "apply")
        conf = self.read("/etc/kaskad/gw/dnsmasq.conf")
        self.assertIn("\nport=5354\n", conf)
        self.assertEqual([ln for ln in conf.splitlines() if ln.startswith("server=")],
                         [ln for ln in body if ln.startswith("server=")])
        self.assertIn("Type=forking", self.read("/etc/systemd/system/kaskad-dns.service"))
        self.assertGreater(self.w.sysd.restarts.get("kaskad-dns.service", 0), before)
        self.assertEqual(self.w.nft.rules("gw_dns"), DNAT)
        self.assertEqual(dns.stale(self.w.runner), [])

    def test_the_watchdog_moves_a_dns_of_310_without_gw_apply(self):
        self.enabled_world()
        body = self.as_310()
        self.w.conntrack.calls = []
        res = netcheck.check(self.w.runner, repair=True)
        kinds = dict((p["kind"], p) for p in res["problems"])
        self.assertTrue(kinds["drift"]["repaired"])                       # step 2: the chain gw_dns put in
        self.assertIn("gw_dns", kinds["drift"]["detail"])
        self.assertTrue(kinds["gw_dns"]["repaired"], kinds["gw_dns"])      # the gateway: dnsmasq to 5354
        self.assertEqual(self.w.nft.rules("gw_dns"), DNAT)
        conf = self.read("/etc/kaskad/gw/dnsmasq.conf")
        self.assertTrue(conf.startswith(dns.HEADER + "\nport=5354\nbind-interfaces\n"), conf)
        self.assertEqual([ln for ln in conf.splitlines() if not ln.startswith("#") and not ln.startswith("port=")],
                         body)                                             # upstreams and rule lines kept
        self.assertEqual(dns.stale(self.w.runner), [])
        # a table of 3.1.0 is not «the gateway part was missing»: no flush of the clients' connections
        self.assertEqual([c for c in self.w.conntrack.calls if c[:2] == ["-D", "-s"]], [])
        self.assertIn("перевёл DNS шлюза на порт 5354", self.log())
        self.assertTrue(netcheck.check(self.w.runner, repair=True)["ok"])  # and next minute — nothing to do

    def test_part_present_without_gw_dns(self):
        self.enabled_world()
        live = nft.read_live(self.w.runner)
        self.assertTrue(net.part_present(live))
        self.w.nft.run_script("flush chain ip kaskad gw_dns\ndelete chain ip kaskad gw_dns\n", True)
        live = nft.read_live(self.w.runner)
        self.assertTrue(net.part_present(live))
        self.assertIn("нет цепочки шлюза gw_dns", nft.verify(state.load(), live))

    def test_check_without_repair_only_reports_the_old_dns(self):
        self.enabled_world()
        self.as_310()
        rc, doc, _ = self.run_json("gw", "check")
        p = [x for x in doc["problems"] if x["kind"] == "gw_dns"]
        self.assertEqual(len(p), 1)
        self.assertIn("конфиг и служба", p[0]["detail"])
        self.assertEqual(sorted(dns.stale(self.w.runner)), ["config", "unit"])   # nothing changed
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/gw/dnsmasq.conf.new")))


FULL = ["nft", "-j", "list", "table", "ip", "kaskad"]


class HotPathsTest(CliGwCase):
    """R-perf: the collector round and ``gw check`` read the chains, the counters and the small sets only."""

    def world(self):
        st = self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        self.w.nft.set_counter("fwd_tcp_443", 7, 700)
        self.w.runner.calls = []
        return st

    def test_a_collector_round_never_dumps_the_table(self):
        self.world()
        res = collect.tick(self.w.runner)
        self.assertTrue(res["enabled"])
        self.assertEqual(self.w.runner.commands(FULL), [])
        lists = sorted(" ".join(a[3:]) for a in self.w.runner.commands(["nft", "-j", "list"]))
        # stage 1 (SPEC-stage1-exits §1.3: «без влияния на маршрут»; §2.3, §5.2): + gw_ruonly and gw_xports — small sets
        # the round refills only when it sees them (never gw_ru); B1 (REV-s1-t45 П2): xray.apply reads gw_xports once
        # more under its lock — the ports it dropped leave the set only after it (one small listing, no dump)
        self.assertEqual(lists, ["chains ip", "counters table ip kaskad", "map ip kaskad gw_route",
                                 "set ip kaskad gw_live", "set ip kaskad gw_rd", "set ip kaskad gw_ruonly",
                                 "set ip kaskad gw_xports", "set ip kaskad gw_xports"])
        doc = json.loads(self.read("/run/kaskad/live.json"))
        self.assertEqual(doc["forwards"], {"tcp:443": {"packets": 7, "bytes": 700}})     # the counters still there
        # gw_route drifted: the round puts it back from the small reads alone
        self.w.nft.run_script("delete element ip kaskad gw_route { 10.77.1.10 }\n", True)
        res = collect.tick(self.w.runner)
        self.assertTrue(res["exits"]["map_changed"])
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102})
        self.assertEqual(self.w.runner.commands(FULL), [])

    def test_gw_check_never_dumps_the_table(self):
        self.world()
        rc, doc, _ = self.run_json("gw", "check")
        self.assertEqual((rc, doc["problems"]), (0, []))
        self.assertEqual(self.w.runner.commands(FULL), [])

    def test_a_missing_part_is_still_put_back(self):
        self.world()
        self.w.nft.flush_ruleset()
        res = collect.tick(self.w.runner)
        self.assertIn("table", res["exits"]["repaired"])                    # nft.apply: the full read is fine here
        self.assertTrue(net.part_present(nft.read_live(self.w.runner)))
        self.w.runner.calls = []
        self.assertTrue(peek.part_present(self.w.runner))
        self.assertEqual(self.w.runner.commands(FULL), [])

    def test_peek_reads_the_shape_of_read_live(self):
        self.world()
        full, small = nft.read_live(self.w.runner), peek.read(self.w.runner)
        for key in ("counters", "chain_info"):
            self.assertEqual(small[key], full[key])
        for name in peek.HOT_SETS:
            self.assertEqual(small["set_info"][name], full["set_info"][name])
        self.assertNotIn(net.RU, small["set_info"])
        self.assertEqual(small["rules"], {})
        self.assertTrue(net.part_present(small))
        self.w.nft.run_script("delete table ip kaskad\n", True)
        self.assertIsNone(peek.read(self.w.runner))
        self.assertIsNone(peek.read(self.w.runner, sets=(net.LIVE,), counters=False))
        self.assertFalse(peek.part_present(self.w.runner))
        self.w.nft.json_ok = False
        with self.assertRaises(KaskadError):
            peek.read(self.w.runner)


class _Dead(object):
    """A terminal that is gone: every write and flush is EIO."""

    def write(self, text):
        raise OSError(errno.EIO, "Input/output error")

    def flush(self):
        raise OSError(errno.EIO, "Input/output error")


class SignalsTest(CliGwCase):
    """[8]: SIGHUP (the SSH session drops), SIGINT (Ctrl+C) and SIGTERM in the middle of a change."""

    def setUp(self):
        super().setUp()
        self.caught = []
        # a hold that does not work must fail the test, not kill the test process
        for sig in (signal.SIGHUP, signal.SIGTERM):
            old = signal.signal(sig, lambda signum, frame: self.caught.append(signum))
            self.addCleanup(signal.signal, sig, old)

    def signal_at(self, prefix, sigs):
        """Send ``sigs`` to ourselves when the first command starting with ``prefix`` runs."""
        orig = self.w.runner.run
        sent = []

        def run(argv, input=None, check=False, timeout=60):
            if not sent and list(argv[:len(prefix)]) == list(prefix):
                sent.append(list(argv))
                for sg in sigs:
                    os.kill(os.getpid(), sg)
            return orig(argv, input=input, check=check, timeout=timeout)

        self.w.runner.run = run
        return sent

    def test_hold_ignores_hup_and_int_and_remembers_term(self):
        before = (signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
        with gsys.hold():
            self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
            self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
            with gsys.hold():                                          # nested: one hold
                pass
            self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
            for sg in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
                os.kill(os.getpid(), sg)
        self.assertEqual((signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGINT),
                          signal.getsignal(signal.SIGTERM)), before)
        self.assertEqual(self.caught, [])
        self.assertIn("GW: получен SIGTERM во время изменения шлюза — изменение доведено до конца", self.log())

    def test_the_commands_of_a_change_inherit_the_ignored_hangup(self):
        code = ("import signal; print(signal.getsignal(signal.SIGHUP) == signal.SIG_IGN, "
                "signal.getsignal(signal.SIGINT) == signal.SIG_IGN)")
        with gsys.hold():
            out = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE, universal_newlines=True).stdout
        self.assertEqual(out.split(), ["True", "True"])

    def test_enable_cut_by_every_signal_still_ends_enabled(self):
        self.ads = self.dummies()
        save_state([F443])
        sent = self.signal_at(["ip", "rule", "add"], [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
        self.ok("gw", "enable")
        self.assertTrue(sent)                                          # the signals came in the middle
        self.assertTrue(model.enabled(state.load()))                   # state.json says what the system is
        self.assertTrue(net.part_present(nft.read_live(self.w.runner)))
        self.assertIn("gw_pre", self.read("/etc/kaskad/net/kaskad.nft"))
        self.assertEqual(self.caught, [])
        self.assertIn("получен SIGTERM во время изменения шлюза", self.log())

    def test_a_change_with_its_steps_after_the_lock(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        sent = self.signal_at(["nft", "-f"], [signal.SIGHUP, signal.SIGINT])
        self.ok("gw", "inbound", "uninstall", "wg")
        self.assertTrue(sent)
        self.assertIsNone(model.inbound(state.load(), "wg"))
        self.assertEqual(self.caught, [])

    def test_a_dead_terminal_loses_the_text_not_the_change(self):
        self.enabled_world()
        with mock.patch.object(sys, "stdout", _Dead()), mock.patch.object(sys, "stderr", _Dead()):
            rc = cli.main(["gw", "user", "add", "Мама"])
        self.assertEqual(rc, 0)
        self.assertEqual([u["name"] for u in model.gw(state.load())["users"]], ["Мама"])

    def test_apt_keeps_a_hangup_the_menu_ignores(self):
        seen = []
        orig = self.w.pkgs.apt

        def apt(argv, input):
            seen.append(signal.getsignal(signal.SIGHUP))
            return orig(argv, input)

        self.w.runner._handlers = [(p, apt if h == orig else h) for p, h in self.w.runner._handlers]
        old = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        try:
            gsys.apt_install(self.w.runner, ["wireguard-tools"])
            self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
        finally:
            signal.signal(signal.SIGHUP, old)
        gsys.apt_install(self.w.runner, ["openvpn"])                    # not ignored before: the guard as it was
        self.assertEqual(seen[0], signal.SIG_IGN)                       # apt-get and dpkg inherit it
        self.assertNotEqual(seen[1], signal.SIG_IGN)

    def test_the_menu_runs_kaskadctl_with_the_hangup_ignored(self):
        with open(INSTALL_SH, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^k3_gw\(\) \{\n.*?^\}\n", text, re.M | re.S)
        self.assertIsNotNone(m)
        stub = ("k3ctl() { %s -c 'import json, signal; print(json.dumps({\"ok\": True, \"hup\": "
                "signal.getsignal(signal.SIGHUP) == signal.SIG_IGN}))'; }\n" % sys.executable)
        script = stub + m.group(0) + 'k3_gw probe; printf "%s|%s" "$K3_GW_RC" "$K3_GW_OUT"\n'
        env = dict(os.environ, LANG="C.UTF-8", LC_ALL="C.UTF-8", TMPDIR=self.root)
        res = subprocess.run(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, env=env)
        rc, _, doc = res.stdout.partition("|")
        self.assertEqual((rc, json.loads(doc)), ("0", {"ok": True, "hup": True}), res.stderr)


class UfwTest(RealAdapterCase):
    """[5]: UFW enabled after the inbounds («защищу потом») or ``ufw reset``: the watchdog and apply put the ports
    of the inbounds back; cheap while UFW's files stay; nothing without UFW."""

    def setUp(self):
        super().setUp()
        self.clock = 1790000000.0
        p = mock.patch.object(tunnel, "_now", lambda: self.clock)
        p.start()
        self.addCleanup(p.stop)

    def wg_world(self, ufw_on=True):
        self.w.ufw.active = ufw_on
        save_state([F443])
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "wg")

    def ufw_probs(self, *flags):
        rc, doc, err = self.run_json("gw", "check", *flags)
        return [p for p in doc["problems"] if p["kind"] == "gw_ufw"]

    def test_ufw_enabled_after_the_inbounds(self):
        self.wg_world(ufw_on=False)
        self.assertEqual(self.w.ufw.rules, [])                              # UFW off: nothing to open
        rec = json.loads(self.read("/etc/kaskad/gw/wg/ufw.json"))
        self.assertEqual((rec["want"], rec["open"]), ([[51820, "udp"]], []))   # but what it wants is kept
        self.w.ufw.enable()                                                 # ufw allow OpenSSH; ufw enable
        probs = self.ufw_probs()
        self.assertEqual(len(probs), 1)
        self.assertIn("UFW закрывает порты протоколов шлюза: 51820/udp (WireGuard)", probs[0]["detail"])   # имя, не код
        self.assertEqual(self.w.ufw.rules, [])                              # report only
        probs = self.ufw_probs("--repair")
        self.assertTrue(probs[0]["repaired"])
        self.assertEqual(self.w.ufw.rules, [("51820/udp", "kaskad-gw wg")])
        self.assertIn("GW repair: UFW закрывает порты протоколов шлюза: 51820/udp (wg)", self.log())
        self.assertEqual(json.loads(self.read("/etc/kaskad/gw/wg/ufw.json"))["open"], [[51820, "udp"]])
        self.assertEqual(self.ufw_probs("--repair"), [])

    def test_cheap_while_ufw_stays_as_it_was(self):
        self.wg_world()
        self.assertEqual(self.ufw_probs("--repair"), [])                    # the first look: one ufw status
        self.w.ufw.calls = []
        self.clock += 60
        self.assertEqual(self.ufw_probs("--repair"), [])
        self.assertEqual(self.w.ufw.calls, [])                              # a minute later: not a single ufw
        self.clock += tunnel.UFW_CHECK_EVERY
        self.ufw_probs("--repair")
        self.assertEqual(self.w.ufw.calls, [["status"]])                    # five minutes: one look
        self.w.ufw.calls = []
        self.w.ufw.deny("22/tcp")                                           # UFW's files changed: at once
        self.ufw_probs("--repair")
        self.assertEqual(self.w.ufw.calls, [["status"]])

    def test_ufw_reset_then_gw_apply_and_the_watchdog(self):
        self.wg_world()
        self.assertEqual(self.w.ufw.rules, [("51820/udp", "kaskad-gw wg")])
        self.w.ufw.reset()
        self.w.ufw.enable()
        self.ok("gw", "apply")                                              # ufw.json no longer trusted blindly
        self.assertEqual(self.w.ufw.rules, [("51820/udp", "kaskad-gw wg")])
        self.w.ufw.reset()
        self.w.ufw.enable()
        probs = self.ufw_probs("--repair")                                  # and the watchdog alone
        self.assertTrue(probs and probs[0]["repaired"])
        self.assertEqual(self.w.ufw.rules, [("51820/udp", "kaskad-gw wg")])

    def test_a_ufw_json_of_310(self):
        self.wg_world()
        self.write("/etc/kaskad/gw/wg/ufw.json", "[[51820, \"udp\"]]\n")    # 3.1.0: only what was opened
        self.w.ufw.reset()
        self.w.ufw.enable()
        probs = self.ufw_probs("--repair")
        self.assertTrue(probs and probs[0]["repaired"])
        self.assertEqual(self.w.ufw.rules, [("51820/udp", "kaskad-gw wg")])
        rec = json.loads(self.read("/etc/kaskad/gw/wg/ufw.json"))
        self.assertEqual((rec["want"], rec["open"]), ([[51820, "udp"]], [[51820, "udp"]]))

    def test_the_owner_denies_the_port(self):
        self.wg_world()
        self.w.ufw.rules = []
        self.w.ufw.deny("51820/udp")
        probs = self.ufw_probs("--repair")
        self.assertEqual(len(probs), 1)
        self.assertIn("UFW запрещает порты протоколов шлюза правилом владельца: 51820/udp (WireGuard)", probs[0]["detail"])
        self.assertFalse(probs[0]["repaired"])
        self.assertEqual(self.w.ufw.rules, [])                              # never fought

    def test_whole_protocol_rules_are_seen(self):
        """ESP of IPsec, GRE of PPTP: real ``ufw status`` prints «Anywhere/esp» in From too — such a rule is there,
        the watchdog does not «repair» it every five minutes (G2: 12 times an hour, «клиенты не подключатся»)."""
        self.wg_world()
        self.write("/etc/kaskad/gw/ipsec/ufw.json",
                   json.dumps({"kind": "ipsec", "want": [[None, "esp"]], "open": [[None, "esp"]], "sig": None}) + "\n")
        self.w.ufw.rules.append(("Anywhere/esp", "kaskad-gw ipsec"))
        self.w.ufw.calls = []
        self.clock += tunnel.UFW_CHECK_EVERY
        self.assertEqual(self.ufw_probs("--repair"), [])
        self.assertEqual(self.w.ufw.calls, [["status"]])                    # looked, added nothing
        self.assertNotIn("GW repair: UFW", self.log())

    def test_ufw_off_or_missing(self):
        self.wg_world(ufw_on=False)
        self.assertEqual(self.ufw_probs("--repair"), [])
        self.w.runner.binaries.discard("ufw")
        self.w.ufw.calls = []
        self.clock += 3600
        self.assertEqual(self.ufw_probs("--repair"), [])
        self.assertEqual(self.w.ufw.calls, [])


UBUNTU = 'PRETTY_NAME="Ubuntu 24.04.1 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\nID_LIKE=debian\n'


class PppModulesTest(CliGwCase):
    """[7]: the PPP modules of the running kernel — Ubuntu's extras with the meta package of the flavor at install,
    ``gw check`` when a new kernel booted without them."""

    def setUp(self):
        super().setUp()
        self.absent = set()            # modules modinfo does not find
        self.extra = set(["l2tp_ppp"])  # modules that are files of linux-modules-extra-*
        r = self.w.runner
        r._handlers.insert(0, (["modinfo", "-n"], self._modinfo))
        r._handlers.insert(0, (["dpkg-query", "-S"], self._dpkg_s))

    def path_of(self, m):
        return "/lib/modules/%s/kernel/drivers/net/ppp/%s.ko.zst" % (self.w.uname, m)

    def _modinfo(self, argv, inp):
        m = argv[-1]
        if m in self.absent and "linux-modules-extra-%s" % self.w.uname not in self.w.pkgs.installed:
            return 1, "", "modinfo: ERROR: Module %s not found.\n" % m
        return 0, self.path_of(m) + "\n", ""

    def _dpkg_s(self, argv, inp):
        m = os.path.basename(argv[-1]).split(".")[0]
        pkg = ("linux-modules-extra-%s" if m in self.extra else "linux-modules-%s") % self.w.uname
        return 0, "%s: %s\n" % (pkg, argv[-1]), ""

    def ubuntu(self, uname="6.8.0-50-generic", installed=("linux-image-virtual", "linux-virtual")):
        self.write("/etc/os-release", UBUNTU, 0o644)
        self.w.uname = uname
        self.w.pkgs.installed.update(installed)

    def test_the_extras_and_the_meta_package_of_each_flavor(self):
        from kaskad.gw.inbounds import accel
        self.assertEqual(accel.extra_packages(self.w.runner, ["l2tp"]), [])         # Debian: every module is there
        self.ubuntu()
        self.absent.add("l2tp_ppp")
        self.assertEqual(accel.extra_packages(self.w.runner, ["l2tp"]),
                         ["linux-modules-extra-6.8.0-50-generic", "linux-image-extra-virtual"])
        self.w.pkgs.installed.add("linux-image-virtual-hwe-24.04")
        self.assertEqual(accel.extra_packages(self.w.runner, ["l2tp"])[1], "linux-image-extra-virtual-hwe-24.04")
        self.w.pkgs.installed.add("linux-image-generic")                           # it pulls the extras itself
        self.assertEqual(accel.extra_packages(self.w.runner, ["l2tp"]), ["linux-modules-extra-6.8.0-50-generic"])
        self.w.uname = "6.8.0-1015-aws"
        self.assertEqual(accel.extra_packages(self.w.runner, ["l2tp"]),
                         ["linux-modules-extra-6.8.0-1015-aws", "linux-modules-extra-aws"])
        self.w.uname = "6.8.0-50-generic"
        self.w.pkgs.installed.discard("linux-image-generic")
        self.absent.clear()                                                         # there now, but from the extras:
        self.assertEqual(accel.extra_packages(self.w.runner, ["l2tp"]), ["linux-image-extra-virtual-hwe-24.04"])
        self.assertEqual(accel.extra_packages(self.w.runner, ["sstp"]), [])          # ppp_async: the base package
        self.extra.clear()
        self.assertEqual(accel.extra_packages(self.w.runner, ["l2tp"]), [])

    def test_install_brings_the_extras_first(self):
        from kaskad.gw.inbounds import accel
        self.ubuntu()
        self.absent.add("l2tp_ppp")
        with mock.patch.object(accel, "ensure_build", lambda runner: "уже стоит"), \
                mock.patch.object(accel, "foreign_units", lambda runner, kind: []):
            accel.install(self.w.runner, "l2tp")
        self.assertIn(["linux-modules-extra-6.8.0-50-generic", "linux-image-extra-virtual"], self.w.pkgs.installs)
        req = accel.PppInbound()
        req.kind, req.title = "l2tp", "L2TP"
        self.assertTrue(req.requirements(self.w.runner)["supported"])

    def test_requirements_on_a_virtual_kernel(self):
        from kaskad.gw.inbounds import accel
        self.ubuntu()
        self.absent.add("l2tp_ppp")
        ad = accel.PppInbound()
        ad.kind, ad.title = "l2tp", "L2TP"
        with mock.patch.object(accel, "foreign_units", lambda runner, kind: []):
            req = ad.requirements(self.w.runner)
        self.assertTrue(req["supported"], req)                                     # the extras will bring them
        self.assertIn("linux-modules-extra-6.8.0-50-generic", req["packages"])
        self.assertIn("linux-image-extra-virtual", req["warning"])

    def test_gw_check_after_a_kernel_without_the_extras(self):
        from kaskad.gw import registry
        from tests.fakes_gw import register_dummies
        self.enabled_world()
        register_dummies(("l2tp", {"ifaces": ["kgw-l2tp*"]}))
        self.ok("gw", "inbound", "install", "l2tp")
        self.ubuntu()
        self.absent.add("l2tp_ppp")
        rc, doc, _ = self.run_json("gw", "check")
        p = [x for x in doc["problems"] if x["kind"] == "gw_kernel"]
        self.assertEqual(len(p), 1, doc)
        self.assertIn("протоколы L2TP не работают: у ядра 6.8.0-50-generic нет модулей l2tp_ppp — apt-get install "
                      "linux-modules-extra-6.8.0-50-generic linux-image-extra-virtual && systemctl restart "
                      "kaskad-gw-accel.service", p[0]["detail"])
        rc, doc, _ = self.run_json("gw", "check", "--repair")
        self.assertEqual([x for x in doc["problems"] if x["kind"] == "gw_kernel"], [])  # the watchdog: the log only
        self.assertIn("GW check: протоколы L2TP не работают", self.log())
        self.w.pkgs.installed.add("linux-modules-extra-6.8.0-50-generic")
        rc, doc, _ = self.run_json("gw", "check")
        self.assertEqual([x for x in doc["problems"] if x["kind"] == "gw_kernel"], [])
        self.assertIsNotNone(registry.get("l2tp"))


class StaleBinaryTest(CliGwCase):
    """[9]: apt replaced the binary of one of our daemons (Debian: no needrestart) — the watchdog try-restarts it
    once per update."""

    def daemon(self, unit, pid, exe, deleted):
        if not os.path.exists(self.path("/etc/systemd/system/" + unit)):          # kaskad-dns: the one enable wrote
            self.write("/etc/systemd/system/" + unit, "[Service]\nExecStart=%s\n" % exe, 0o644)
        self.w.sysd.units[unit] = {"enabled": True, "active": True, "pid": pid}
        link = self.path("/proc/%d/exe" % pid)
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(exe + (" (deleted)" if deleted else ""), link)

    def probs(self, *flags):
        rc, doc, _ = self.run_json("gw", "check", *flags)
        return [p for p in doc["problems"] if p["kind"] == "gw_binary"]

    def test_restart_once_per_update(self):
        self.enabled_world()
        self.write("/usr/sbin/ocserv", "new build\n", 0o755)
        self.daemon("kaskad-gw-ocserv.service", 4100, "/usr/sbin/ocserv", deleted=True)
        self.daemon("kaskad-dns.service", 4200, "/usr/sbin/dnsmasq", deleted=False)
        probs = self.probs()
        self.assertEqual(len(probs), 1)
        self.assertIn("kaskad-gw-ocserv.service работает на удалённом /usr/sbin/ocserv", probs[0]["detail"])
        self.assertEqual(self.w.sysd.restarts.get("kaskad-gw-ocserv.service"), None)     # report only
        probs = self.probs("--repair")
        self.assertTrue(probs[0]["repaired"])
        self.assertEqual(self.w.sysd.restarts.get("kaskad-gw-ocserv.service"), 1)
        self.assertNotIn("kaskad-dns.service", self.w.sysd.restarts)
        self.assertIn("GW repair: kaskad-gw-ocserv.service работает на удалённом /usr/sbin/ocserv", self.log())
        self.assertEqual(self.probs("--repair"), [])                       # still deleted (the fake): not again
        self.assertEqual(self.w.sysd.restarts.get("kaskad-gw-ocserv.service"), 1)
        self.write("/usr/sbin/ocserv", "the next update\n", 0o755)          # another update: once more
        os.utime(self.path("/usr/sbin/ocserv"), (1790000999, 1790000999))
        self.probs("--repair")
        self.assertEqual(self.w.sysd.restarts.get("kaskad-gw-ocserv.service"), 2)
        self.daemon("kaskad-gw-ocserv.service", 4101, "/usr/sbin/ocserv", deleted=False)  # runs the new binary
        self.assertEqual(self.probs(), [])
