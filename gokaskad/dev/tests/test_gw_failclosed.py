"""Fail-closed without the gw_* chains (review 2026-10-04): the routing kill switch, the order of _apply
(table and routing before any inbound), the gate of the inbound units, the watchdog that starts nothing
without the gw part and flushes the clients' connections when it puts the part back, exits that do not
flap when xray restarts."""

import os

from kaskad import netcheck, nft, state
from kaskad.gw import collect, gate, net, ops, registry, xray
from kaskad.gw.inbounds.base import DummyInbound
from tests.fakes_gw import T0
from tests.fakes_nft import F443, save_state
from tests.test_gw_ops import CliGwCase

# the block of our ip rules (net.place_rules, F1): 4000 replies, 4001 «to <base>», 4002/4003 TPROXY, 4004 kill switch
KILL = (4004, "from 10.77.0.0/16 fwmark 0/0xffff lookup 199")
KILL_BASE = (4001, "from all to 10.77.0.0/16 lookup main")


class Watcher(DummyInbound):
    """A dummy inbound that records, when it is applied, whether the gw part and the kill switch exist."""

    def __init__(self, case, **kw):
        DummyInbound.__init__(self, **kw)
        self.case = case
        self.seen = []

    def apply(self, st, sec, runner):
        live = nft.read_live(runner)
        self.seen.append((net.part_present(live), KILL in self.case.w.ip.rules))
        return DummyInbound.apply(self, st, sec, runner)


class OrderTest(CliGwCase):
    def test_the_table_and_the_kill_switch_come_before_any_inbound(self):
        w = Watcher(self, kind="wg", ifaces=["kgw-wg"])
        registry.register(w)
        save_state([F443])
        self.ok("gw", "inbound", "install", "wg")                  # gateway off: prepared, not started
        self.assertEqual(w.seen, [])
        self.ok("gw", "enable")
        self.assertEqual(w.seen, [(True, True)])
        # nft flush ruleset + the rules gone: the next change protects first, then runs the inbound
        self.w.nft.flush_ruleset()
        self.w.ip.rules = [r for r in self.w.ip.rules if r not in (KILL, KILL_BASE)]
        self.ok("gw", "user", "add", "Мама")
        self.assertEqual(w.seen[-1], (True, True))
        self.ok("gw", "apply")                                      # gw apply: always the table first
        self.assertEqual(w.seen[-1], (True, True))
        self.assertIn(KILL_BASE, self.w.ip.rules)

    def test_a_change_with_the_part_in_place_does_not_apply_twice(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        writes = len(self.w.nft_writes())
        self.ok("gw", "user", "add", "Мама")
        self.assertEqual(len(self.w.nft_writes()), writes + 1)      # one transaction, not two

    def test_disable_takes_the_kill_switch(self):
        self.enabled_world(pool=False)
        self.assertIn(KILL, self.w.ip.rules)
        self.ok("gw", "disable")
        self.assertFalse([r for r in self.w.ip.rules if r in (KILL, KILL_BASE) or "lookup 199" in r[1]])
        self.assertEqual(self.w.ip.routes.get("199"), [])


class GateTest(CliGwCase):
    def unit(self, name):
        self.write("/etc/systemd/system/" + name, "[Unit]\n[Service]\nExecStart=/bin/true\n", 0o644)

    def test_inbound_units_get_the_gate_without_a_restart(self):
        self.enabled_world(pool=False)
        self.unit("kaskad-gw-wg.service")                           # what the real adapter's install writes
        self.ok("gw", "inbound", "install", "wg")
        p = gate.dropin_path("kaskad-gw-wg.service")
        self.assertEqual(p, self.path("/etc/systemd/system/kaskad-gw-wg.service.d/kaskad-gate.conf"))
        text = self.read("/etc/systemd/system/kaskad-gw-wg.service.d/kaskad-gate.conf")
        self.assertIn("[Service]\nExecStartPre=+/bin/sh -c 'exec /usr/sbin/nft list chain ip kaskad gw_pre "
                      ">/dev/null'\n", text)
        self.assertEqual(self.w.sysd.restarts.get("kaskad-gw-wg.service", 0), 0)
        self.assertEqual(gate.present(), ["kaskad-gw-wg.service"])
        reloads = self.w.sysd.reloads
        self.ok("gw", "apply")
        self.assertEqual(self.w.sysd.reloads, reloads)              # nothing changed: no daemon-reload
        self.ok("gw", "inbound", "uninstall", "wg")                  # the unit file stays (a dummy), the gate goes
        self.assertFalse(os.path.exists(p))
        self.assertFalse(os.path.isdir(os.path.dirname(p)))
        self.assertEqual(gate.present(), [])

    def test_remove_all_keeps_foreign_drop_ins(self):
        self.enabled_world(pool=False)
        self.unit("kaskad-gw-wg.service")
        self.ok("gw", "inbound", "install", "wg")
        self.write("/etc/systemd/system/kaskad-gw-wg.service.d/owner.conf", "[Service]\nNice=5\n", 0o644)
        self.assertEqual(gate.remove_all(self.w.runner), ["kaskad-gw-wg.service"])
        self.assertTrue(os.path.exists(self.path("/etc/systemd/system/kaskad-gw-wg.service.d/owner.conf")))


class WatchdogWithoutThePartTest(CliGwCase):
    def healthy(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        from kaskad import compat
        compat.ensure(self.w.runner)
        self.assertEqual(netcheck.check(self.w.runner, repair=False)["problems"], [])

    def test_no_inbound_starts_without_the_part_then_the_part_comes_back_first(self):
        self.healthy()
        self.ads["wg"].running = False
        self.ads["wg"].calls = []
        self.w.nft.flush_ruleset()
        probs = ops.check(state.load(), self.w.runner, repair=True)
        p = [x for x in probs if x["kind"] == "gw_inbound"][0]
        self.assertFalse(p["repaired"])
        self.assertIn("нет части шлюза", p["detail"])
        self.assertEqual(self.ads["wg"].calls, [])                   # not started without the table
        res = netcheck.check(self.w.runner, repair=True)             # the table first, then the inbound
        self.assertTrue(res["ok"], res)
        self.assertTrue(self.ads["wg"].running)
        self.assertEqual(self.w.conntrack.calls, [["-D", "-s", "10.77.0.0/16"]])
        self.assertIn("сброшены соединения клиентов 10.77.0.0/16 (3)", self.log())
        netcheck.check(self.w.runner, repair=True)
        self.assertEqual(len(self.w.conntrack.calls), 1)             # only after the part was missing

    def test_kill_switch_is_repaired_by_the_watchdog(self):
        self.healthy()
        self.w.ip.rules = [r for r in self.w.ip.rules if r != KILL]
        del self.w.ip.routes["199"]
        res = netcheck.check(self.w.runner, repair=False)
        self.assertEqual([p["kind"] for p in res["problems"]], ["gw_routing"])
        self.assertIn("нет аварийного ip rule from 10.77.0.0/16", res["problems"][0]["detail"])
        self.assertTrue(netcheck.check(self.w.runner, repair=True)["ok"])
        self.assertIn(KILL, self.w.ip.rules)
        self.assertEqual(self.w.ip.routes["199"], ["unreachable default"])

    def test_xray_is_not_restarted_without_the_part(self):
        self.healthy()
        self.w.sysd.units[xray.UNIT]["active"] = False
        self.w.nft.flush_ruleset()
        probs = ops.check(state.load(), self.w.runner, repair=True)
        p = [x for x in probs if x["kind"] == "gw_service" and xray.UNIT in x["detail"]][0]
        self.assertFalse(p["repaired"])
        self.assertNotIn(["systemctl", "restart", "--no-block", xray.UNIT], self.w.sysd.calls)


class CollectorRestoresTest(CliGwCase):
    def test_flushed_ruleset_kill_switch_and_connections(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        self.w.nft.flush_ruleset()
        self.w.ip.rules = [r for r in self.w.ip.rules if r not in (KILL, KILL_BASE)]
        res = collect.tick(self.w.runner)
        self.assertEqual(res["exits"]["repaired"], ["table", "routing"])
        self.assertEqual(res["exits"]["conntrack"], 3)
        self.assertIn(KILL, self.w.ip.rules)
        self.assertIn(KILL_BASE, self.w.ip.rules)
        self.assertIn("GW repair: сброшены соединения клиентов 10.77.0.0/16 (3)", self.log())
        res = collect.tick(self.w.runner)
        self.assertEqual((res["exits"]["repaired"], len(self.w.conntrack.calls)), ([], 1))

    def test_without_conntrack_the_log_says_so(self):
        self.enabled_world()
        self.w.runner.binaries.discard("conntrack")
        self.w.nft.flush_ruleset()
        res = collect.tick(self.w.runner)
        self.assertIsNone(res["exits"]["conntrack"])
        self.assertIn("conntrack не установлен", self.log())
        self.assertEqual(self.w.conntrack.calls, [])

    def test_enable_installs_conntrack_with_the_other_packages(self):
        self.w.runner.binaries.discard("conntrack")
        save_state([F443])
        rc, doc, err = self.run_json("gw", "enable")
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["installed"], ["conntrack"])
        self.assertEqual(self.w.pkgs.policy_seen, [True])           # apt under policy-rc.d


class NoFlappingTest(CliGwCase):
    def mono(self):
        return self.now - T0 + 1000.0                               # exits._mono in GwCase

    def test_an_xray_restart_moves_nobody(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        for name in ("A", "B", "C", "D"):
            self.ok("gw", "device", "add", "Мама", name, "--access", "wg")
        self.w.sysd.units[xray.UNIT]["active"] = True
        self.w.curl.observatory = {"gx-" + self.vid: True}
        collect.tick(self.w.runner)
        marks = self.w.nft.elements("gw_route")
        vless = 0x300 + 1                                            # stage 1: a VLESS exit by the device (§2.3)
        self.assertIn(vless, marks.values())                         # somebody is on the VLESS leg
        log = self.log()
        # xray restarts: its observatory knows nothing for ~10 s, then its first probe may say "dead"
        self.w.sysd.units[xray.UNIT]["since_mono"] = self.mono()
        self.w.curl.observatory = {}
        for _ in range(2):
            self.now += 5
            res = collect.tick(self.w.runner)
            self.assertEqual(res["exits"]["changes"], [])
        self.w.curl.observatory = {"gx-" + self.vid: False}
        self.now += 5
        collect.tick(self.w.runner)                                  # 15 s after the start: not believed
        self.assertEqual(self.w.nft.elements("gw_route"), marks)
        self.assertEqual(self.log(), log)                            # and nothing to log
        self.now += 20                                               # past the grace: 3.3.5 — dead when half of
        for _ in range(2):                                           # 4 checks at the least failed (1 good, 3 bad)
            collect.tick(self.w.runner)
            self.assertEqual(self.w.nft.elements("gw_route"), marks)
            self.now += 10
        collect.tick(self.w.runner)
        self.assertNotIn(vless, self.w.nft.elements("gw_route").values())
        self.assertIn("не работает", self.log())
