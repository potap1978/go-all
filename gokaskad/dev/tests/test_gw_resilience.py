"""What must survive failures: secrets of a failed change, a config xray refuses, a broken adapter, a
broken or repeated pool, the collector repairing the table and the routing, one --egress-awg-up at a time."""

import fcntl
import json
import os

from kaskad import errors, netcheck, nft, secrets, state
from kaskad.gw import collect, exits, model, net, registry, xray
from kaskad.gw.inbounds.base import DummyInbound, InboundError
from tests.fakes_gw import VLESS_A, awg_line, install_parser, register_dummies, write_pool
from tests.fakes_nft import F443, save_state
from tests.test_gw_ops import CliGwCase


class SecretsOfAFailedChangeTest(CliGwCase):
    def test_store_keeps_deletions_until_the_final_save(self):
        s = secrets.load()
        s.set("dev.d-1.password", "Abcdefgh2345")
        s.set("dev.d-1.wg.private", "KEY1KEY1KEY1")
        s.save()
        s = secrets.load()
        self.assertEqual(s.delete_prefix("dev.d-1."), 2)
        s.set("l2tp.psk", "PskPskPsk2345")
        self.assertTrue(s.save(keep_deleted=True))
        disk = secrets.load()
        self.assertEqual(sorted(disk), ["dev.d-1.password", "dev.d-1.wg.private", "l2tp.psk"])  # nothing lost yet
        self.assertTrue(s.dirty)
        secrets.set("wg.server.private", "SRVSRVSRV234")       # an adapter writes a single key meanwhile
        self.assertTrue(s.save())
        self.assertEqual(sorted(secrets.load()), ["l2tp.psk", "wg.server.private"])
        self.assertFalse(s.save())                              # clean: nothing to write

    def test_device_del_that_fails_keeps_its_secrets(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "Телефон", "--access", "wg")
        did = doc["device"]["id"]
        keys = sorted(k for k in secrets.load() if k.startswith("dev.%s." % did))
        self.assertEqual(len(keys), 2)                          # password + the dummy's key
        self.w.nft.fail_check = "Error: Could not process rule: Device or resource busy\n"
        rc, out, err = self.run_cli("gw", "device", "del", did)
        self.assertEqual(rc, errors.ERROR)
        self.assertIsNotNone(model.find_device(state.load(), did))       # state.json as it was
        self.assertEqual(sorted(k for k in secrets.load() if k.startswith("dev.%s." % did)), keys)
        self.w.nft.fail_check = None
        self.ok("gw", "device", "del", did)
        self.assertEqual([k for k in secrets.load() if k.startswith("dev.%s." % did)], [])


class XrayRefusesTest(CliGwCase):
    def test_a_refused_config_is_a_warning_and_the_old_one_runs(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "A", "--access", "wg", "--route", "exit:" + self.aid)
        self.w.curl.observatory = {"gx-" + self.vid: True}                # the VLESS exit works
        collect.tick(self.w.runner)
        before = self.read("/etc/kaskad/gw/xray.json")
        restarts = self.w.sysd.restarts.get(xray.UNIT, 0)
        self.w.xray.fail = "Failed to start: main: failed to load config files > invalid outbound\n"
        rc, out, err = self.run_cli("gw", "exit", self.aid, "--disable")  # the exits xray serves change
        self.assertEqual(rc, 0, err)
        self.assertIn("xray не принял конфиг шлюза", err)
        self.assertIn("Работает прежний конфиг", err)
        self.assertFalse(model.exit_pref(state.load(), self.aid)["enabled"])          # saved
        # the table did change: stage 1 (SE §2.2, difference 4) — «exit:E», E switched off: another fit exit of its
        # group (the VLESS one), not the block of 3.3.5
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.1.10"], 0x301)
        self.assertEqual(self.read("/etc/kaskad/gw/xray.json"), before)
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/gw/xray.new.json")))
        self.assertEqual(self.w.sysd.restarts.get(xray.UNIT, 0), restarts)
        self.w.xray.fail = None
        self.ok("gw", "apply")
        self.assertNotIn("gx-" + self.aid, self.read("/etc/kaskad/gw/xray.json"))


class BrokenAdapter(DummyInbound):
    def apply(self, st, sec, runner):
        self.calls.append("apply")
        raise InboundError("сломался")


class BrokenAdapterTest(CliGwCase):
    def test_one_broken_inbound_does_not_stop_the_others_or_the_table(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        registry.register(BrokenAdapter(kind="awg2", default_port=41023, ifaces=["kgw-awg2"]))
        rc, out, err = self.run_cli("gw", "inbound", "install", "awg2")
        self.assertEqual(rc, 0, err)
        self.assertIn("сломался", err)
        self.assertIsNotNone(model.inbound(state.load(), "awg2"))
        self.ok("gw", "user", "add", "Мама")
        rc, out, err = self.run_cli("gw", "device", "add", "Мама", "A", "--access", "wg,awg2", "--route", "direct")
        self.assertEqual(rc, 0, err)
        self.assertIn("сломался", err)
        self.assertTrue(self.ads["wg"].running)
        self.assertEqual(sorted(self.w.nft.elements("gw_route")), ["10.77.1.10", "10.77.3.10"])
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])


class PoolTest(CliGwCase):
    def test_the_same_key_twice_is_one_exit(self):
        self.enabled_world()
        awg = awg_line(2)
        write_pool([VLESS_A, awg, VLESS_A])
        got = exits.pool(self.w.runner)
        self.assertEqual([e["kind"] for e in got], ["xray", "awg", "bad"])
        self.assertIn("повтор", got[2]["problem"])
        self.ok("gw", "apply")
        cfg = json.loads(self.read("/etc/kaskad/gw/xray.json"))
        tags = [o["tag"] for o in cfg["outbounds"]]
        self.assertEqual(len(tags), len(set(tags)))

    def test_an_unreadable_pool_keeps_the_snapshot_and_the_map(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        snap = exits.load_snapshot()
        marks = self.w.nft.elements("gw_route")
        self.write("/etc/kaskad/egress/egress_parse.py", "raise RuntimeError('half an update')\n")  # broken
        res = collect.tick(self.w.runner)
        self.assertTrue(res["exits"].get("error"))
        self.assertEqual(exits.load_snapshot(), snap)
        self.assertEqual(self.w.nft.elements("gw_route"), marks)


class RenumberedPoolTest(CliGwCase):
    """The marks of the exits follow the line of the pool: a key removed shifts every line after it. gw apply
    (install.sh runs it right after a change of the pool) or the collector moves the devices at once and
    forgets their connections under the old marks; a key added at the end changes no line."""

    def setUp(self):
        super().setUp()
        install_parser()
        self.hel, self.mos = awg_line(2, name="Helsinki"), awg_line(3, name="Moscow")

    def world(self, lines):
        self.ads = self.dummies()
        save_state([F443])
        write_pool(lines)
        for i, line in enumerate(lines, 1):
            if line.startswith("awg://"):
                self.w.leg(i)
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        hid, mid = exits.exit_id(self.hel), exits.exit_id(self.mos)
        out = {}
        for name, eid in (("A", hid), ("B", mid)):
            rc, doc, _ = self.run_json("gw", "device", "add", "Мама", name, "--access", "wg", "--route", "exit:" + eid)
            out[name] = doc["device"]["ips"]["wg"]
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "C", "--access", "wg", "--route", "direct")
        out["C"] = doc["device"]["ips"]["wg"]
        return out

    def renumber(self, lines):
        """What egress_rebuild does to the legs: every leg of the new pool brought up again, the extra ones gone."""
        write_pool(lines)
        for i in range(1, 6):
            self.w.kill_leg(i)
        for i, line in enumerate(lines, 1):
            if line.startswith("awg://"):
                self.w.leg(i)

    def test_pure_functions(self):
        self.assertEqual(exits.renumbered(["a", "b", "c"], ["a", "c"]), [2, 3])
        self.assertEqual(exits.renumbered(["a", "b"], ["a", "b", "c"]), [])
        self.assertEqual(exits.renumbered([], ["a"]), [])
        self.assertEqual(exits.renumbered(["a", "b"], ["c", "d"]), [1, 2])
        self.assertEqual(exits.line_marks([2, 3]), [0x102, 0x302, 0x103, 0x303])

    def test_a_removed_key_moves_the_pinned_devices_at_once_and_forgets_their_connections(self):
        ip = self.world([VLESS_A, self.hel, self.mos])
        self.assertEqual(self.w.nft.elements("gw_route"), {ip["A"]: 0x102, ip["B"]: 0x103, ip["C"]: 0x201})
        self.w.conntrack.marked = {0x4b000102: 5, 0x4b000103: 7, 0x4b000201: 2, 0x4b000302: 1}
        self.renumber([self.hel, self.mos])                                       # the VLESS key removed
        self.ok("gw", "apply")
        self.assertEqual(self.w.nft.elements("gw_route"), {ip["A"]: 0x101, ip["B"]: 0x102, ip["C"]: 0x201})
        self.assertEqual(self.w.conntrack.mark_calls(), ["0x4b000101/0xff00ffff", "0x4b000102/0xff00ffff",
                                                         "0x4b000103/0xff00ffff", "0x4b000301/0xff00ffff",
                                                         "0x4b000302/0xff00ffff", "0x4b000303/0xff00ffff"])
        self.assertEqual(self.w.conntrack.marked, {0x4b000201: 2})              # «direct» is not a line of the pool
        self.assertIn("GW exits: строки пула 1, 2, 3 сменили выход — соединения клиентов через них сброшены (13)",
                      self.log())
        self.w.conntrack.calls[:] = []
        self.ok("gw", "apply")
        self.assertEqual(self.w.conntrack.mark_calls(), [])                     # once

    def test_a_key_added_at_the_end_changes_no_line(self):
        ip = self.world([self.hel])
        self.w.conntrack.marked = {0x4b000102: 5}
        self.renumber([self.hel, VLESS_A])
        self.ok("gw", "apply")
        self.assertEqual(self.w.conntrack.mark_calls(), [])
        self.assertEqual(self.w.conntrack.marked, {0x4b000102: 5})
        self.assertEqual(self.w.nft.elements("gw_route")[ip["A"]], 0x101)

    def test_the_collector_does_it_when_it_sees_the_change_first(self):
        ip = self.world([VLESS_A, self.hel, self.mos])
        self.w.conntrack.marked = {0x4b000103: 7}
        self.renumber([VLESS_A, self.mos])                                       # Helsinki removed: Moscow is line 2
        res = collect.tick(self.w.runner)
        self.assertEqual(res["exits"]["renumbered"], [2, 3])
        self.assertEqual(res["exits"]["flushed"], 7)
        self.assertEqual(self.w.nft.elements("gw_route")[ip["B"]], 0x102)
        # its exit is gone: stage 1 (SE §2.2, З1) — through the group (here «Основная»), not the block of 3.3.5
        self.assertIn(self.w.nft.elements("gw_route")[ip["A"]], (0x301, 0x102))
        self.w.conntrack.calls[:] = []
        self.ok("gw", "apply")
        self.assertEqual(self.w.conntrack.mark_calls(), [])                     # nothing left to forget

    def test_without_conntrack_it_is_said(self):
        self.world([VLESS_A, self.hel])
        self.w.runner.binaries.discard("conntrack")
        self.renumber([self.hel])
        self.ok("gw", "apply")
        self.assertIn("conntrack не установлен", self.log())


class CollectorRepairsTest(CliGwCase):
    def test_flushed_ruleset_and_lost_rule_come_back_within_one_round(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        marks = self.w.nft.elements("gw_route")
        self.w.nft.flush_ruleset()
        self.w.ip.rules = [r for r in self.w.ip.rules if "0x200" not in r[1]]
        res = collect.tick(self.w.runner)
        self.assertEqual(res["exits"]["repaired"], ["table", "routing"])
        self.assertEqual(self.w.nft.elements("gw_route"), marks)
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443)})   # wave 1 too
        self.assertIn("from all fwmark 0x200 lookup 200", self.w.ip.rule_texts())
        self.assertIn("GW repair: части шлюза в таблице nftables не было", self.log())
        res = collect.tick(self.w.runner)
        self.assertEqual(res["exits"]["repaired"], [])

    def device(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")

    def assert_routing_whole(self, how=""):
        st = state.load()
        self.assertEqual(net.routing_problems(self.w.runner, st), [], how)
        self.assertEqual(self.w.ip.routes["200"], ["local default dev lo scope host", "throw 10.77.0.0/16"], how)
        self.assertEqual((self.w.ip.links["kgw0"]["addrs"], self.w.ip.links["kgw0"]["up"]), (["10.77.0.1/32"], True), how)
        legs = [e for e in exits.pool(self.w.runner) if e["kind"] == "awg"]
        self.assertEqual(exits.leg_problems(self.w.runner, legs), {}, how)
        self.assertIn("from all fwmark 0x102 lookup 258", self.w.ip.rule_texts(), how)

    def test_a_restart_of_systemd_networkd_costs_one_round_and_no_leg(self):
        """Stand, Ubuntu 26.04: a restart of networkd took fwmark 0x200 (back within a minute) and the rules
        of the AWG legs (back only with --egress-awg-up, which recreates every leg). Now one collector round
        puts all of it back, the legs stay as they are."""
        self.device()
        idx = self.w.ip.links["kawg2"]["idx"]
        self.assertEqual(self.w.script.calls, [])
        self.w.networkd_restart()
        res = collect.tick(self.w.runner)
        self.assertEqual(res["exits"]["repaired"], ["routing"])
        legs = res["exits"]["legs"]
        self.assertEqual((legs["broken"], legs["healed"], legs["ok"]), (["kawg2"], True, True))
        self.assertEqual(self.w.script.calls, [["--egress-awg-heal"]])
        self.assertEqual(self.w.ip.links["kawg2"]["idx"], idx)                   # the leg was not recreated
        self.assert_routing_whole()
        self.assertEqual(self.w.ip.routes["258"], ["unreachable default metric 1000", "default dev kawg2 scope link"])
        res = collect.tick(self.w.runner)
        self.assertEqual((res["exits"]["repaired"], res["exits"]["legs"]["healed"]), ([], False))

    def test_everything_the_gateway_routes_by_comes_back(self):
        """More than networkd takes: the rule 0x200, table 200 (``local default dev lo``), kgw0 with its address
        and the legs' rules — a collector round, the minute watchdog and «gw check --repair» (collector
        stopped) each put back all of it."""
        self.device()
        idx = self.w.ip.links["kawg2"]["idx"]
        for how in ("collector", "net check --repair", "gw check --repair"):
            self.w.networkd_restart()
            self.w.ip.routes["200"] = []
            self.w.ip.drop_link("kgw0")
            if how == "collector":
                collect.tick(self.w.runner)
            elif how == "net check --repair":
                self.w.sysd.units[collect.UNIT]["active"] = False
                self.w.sysd.units[collect.UNIT]["masked"] = True              # stays stopped: legs by the watchdog
                netcheck.check(self.w.runner, repair=True)
            else:
                self.run_cli("gw", "check", "--repair")
            self.assert_routing_whole(how)
        self.assertEqual(self.w.ip.links["kawg2"]["idx"], idx)
        self.assertNotIn(["--egress-awg-up"], self.w.script.calls)
        self.assertEqual(self.w.script.calls, [["--egress-awg-heal"]] * 3)


class LegsLockTest(CliGwCase):
    def test_one_heal_at_a_time(self):
        self.enabled_world()
        self.w.kill_leg(2)
        pool_exits = exits.pool(self.w.runner)
        lock = exits._legs_lock()
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            res = exits.ensure_legs(self.w.runner, pool_exits, force=True)
            self.assertEqual((res["healed"], res["missing"]), (False, ["kawg2"]))
        finally:
            os.close(fd)
        res = exits.ensure_legs(self.w.runner, pool_exits, force=True)
        self.assertEqual((res["healed"], res["ok"]), (True, True))
        self.assertIn("kawg2", self.w.ip.links)
        self.w.kill_leg(2)
        self.w.sysd.units[exits.EGRESS_UNIT] = {"enabled": True, "active": False, "activating": True}
        res = exits.ensure_legs(self.w.runner, pool_exits)                 # 3.2: the egress no longer owns legs
        self.assertEqual((res["healed"], res["ok"]), (True, True))
        self.assertNotIn(["--egress-awg-up"], self.w.script.calls)


class VlessExitRouteTest(CliGwCase):
    def test_a_dynamic_device_reaches_its_vless_exit_without_xray_changes(self):
        # a device routed to one VLESS exit is an element of gw_route with the exit's own mark (its own
        # TPROXY port and xray inbound): IKEv2 addresses that come and go never touch xray
        self.enabled_world()
        register_dummies(("ikev2", {"proto": "", "default_port": 0, "dynamic": True, "ifaces": []}))
        self.ok("gw", "inbound", "install", "ikev2")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "iPhone", "--access", "ikev2",
                                   "--route", "exit:" + self.vid)
        login = doc["device"]["login"]
        self.w.sysd.units[xray.UNIT]["active"] = True
        self.w.curl.observatory = {"gx-" + self.vid: True}
        collect.tick(self.w.runner)                                         # the VLESS exit is alive
        before = self.read("/etc/kaskad/gw/xray.json")
        restarts = self.w.sysd.restarts.get(xray.UNIT, 0)
        rc, doc, _ = self.run_json("gw", "hook", "ikev2", "up", login, "10.77.8.10")
        self.assertEqual((rc, doc["mark"]), (0, 0x301))
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.8.10"], 0x301)
        self.assertEqual(self.read("/etc/kaskad/gw/xray.json"), before)
        self.assertEqual(self.w.sysd.restarts.get(xray.UNIT, 0), restarts)
        cfg = json.loads(before)
        self.assertFalse([r for r in cfg["routing"]["rules"] if "source" in r])
        self.assertIn("meta mark 0x301 meta l4proto { tcp, udp } tproxy to 127.0.0.1:12401", self.w.nft.rules("gw_pre"))
