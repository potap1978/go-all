"""``kaskadctl gw ...`` end to end through kaskad.cli.main: enable/disable, inbounds, users, devices,
issue, exits and the exits watchdog, hooks, the watchdog of ``net check``, teardown, conflicts."""

import contextlib
import io
import json
import os
import time
from unittest import mock

from kaskad import cli, compat, errors, i18n, netcheck, nft, paths, secrets, state
from kaskad.gw import cli as gwcli
from kaskad.gw import collect, exits, hooks, model, net, registry, xray
from kaskad.gw import sys as gsys
from tests.fakes_gw import (FakeIp, FakeUfw, VLESS_A, awg_line, install_parser, register_dummies, touch_exec,
                            write_pool)
from tests.fakes_nft import F443, save_state
from tests.gwcase import GwCase
from tests.helpers import lock_held_elsewhere


def registry_get(kind):
    from kaskad.gw import registry
    return registry.get(kind)


class CliGwCase(GwCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def run_json(self, *argv):
        rc, out, err = self.run_cli(*(argv + ("--json",)))
        self.assertEqual(len(out.strip().splitlines()), 1, out + err)
        return rc, json.loads(out), err

    def ok(self, *argv):
        rc, out, err = self.run_cli(*argv)
        self.assertEqual(rc, 0, "%s -> %s %s" % (argv, out, err))
        return out, err

    def log(self):
        p = self.path("/var/log/kaskad.log")
        return self.read("/var/log/kaskad.log") if os.path.exists(p) else ""

    def enabled_world(self, pool=True):
        """Engine nft with a forward, the gateway on, wg + awg2 inbounds (dummies)."""
        self.ads = self.dummies()
        save_state([F443])
        if pool:
            install_parser()
            awg = awg_line(2)
            write_pool([VLESS_A, awg])
            self.vid, self.aid = exits.exit_id(VLESS_A), exits.exit_id(awg)
            self.w.leg(2)
        self.ok("gw", "enable")
        return state.load()


class EnableTest(CliGwCase):
    def test_refused_on_engine_legacy_and_unsupported_systems(self):
        save_state(engine="legacy")
        rc, out, err = self.run_cli("gw", "enable")
        self.assertEqual(rc, errors.UNSUPPORTED)
        self.assertIn("nftables", err)
        save_state()
        self.write("/etc/os-release", 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04.5 LTS"\n')
        rc, doc, _ = self.run_json("gw", "enable")
        self.assertEqual((rc, doc["code"]), (errors.UNSUPPORTED, errors.UNSUPPORTED))
        self.assertIn("Ubuntu 22.04", doc["error"])
        self.assertFalse(model.enabled(state.load()))

    def test_refused_when_nft_rejects_the_gateway_table(self):
        save_state()
        self.w.nft.fail_check = lambda text: "Error: Could not process rule: No such file or directory\n" \
            if "kaskad_gwprobe" in text else None
        rc, out, err = self.run_cli("gw", "enable")
        self.assertEqual(rc, errors.UNSUPPORTED)
        self.assertIn("TPROXY", err)

    def test_enable_from_scratch(self):
        self.w = self.make_world(dnsmasq=False)
        import kaskad.sysrun as sysrun
        sysrun._default = self.w.runner
        save_state([F443])
        rc, doc, err = self.run_json("gw", "enable")
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["base"], "10.77.0.0/16")
        self.assertEqual(doc["installed"], ["dnsmasq-base"])
        st = state.load()
        self.assertTrue(st["gateway"]["enabled"])
        self.assertEqual(st["gateway"]["net"], {"base": "10.77.0.0/16", "dns": "10.77.0.1"})
        self.assertEqual((st["gateway"]["host"], st["gateway"]["host_auto"]), ("203.0.113.10", True))
        self.assertEqual(doc["host"], "203.0.113.10")
        self.assertEqual(nft.verify(st, nft.read_live(self.w.runner)), [])
        self.assertIn("map gw_route", self.read("/etc/kaskad/net/kaskad.nft"))
        self.assertIn("from all fwmark 0x200 lookup 200", self.w.ip.rule_texts())
        self.assertEqual(self.w.ip.links["kgw0"]["addrs"], ["10.77.0.1/32"])
        self.assertTrue(compat.present_in(self.w.runner))
        self.assertTrue(self.w.sysd.units["kaskad-dns.service"]["active"])
        self.assertTrue(self.w.sysd.units[collect.UNIT]["active"])
        self.assertNotIn(xray.UNIT, self.w.sysd.units)                    # no exits, no SOCKS5: no xray
        conf = self.read("/etc/kaskad/gw/dnsmasq.conf")
        self.assertNotIn("server=", conf)                                 # no exits, nobody direct: DNS closed
        self.assertIn("\nfilter-AAAA\n", conf)
        # the kill switch: unmarked packets of the base find only «unreachable»
        self.assertIn((4004, "from 10.77.0.0/16 fwmark 0/0xffff lookup 199"), self.w.ip.rules)
        self.assertIn((4001, "from all to 10.77.0.0/16 lookup main"), self.w.ip.rules)
        self.assertEqual(self.w.ip.routes["199"], ["unreachable default"])
        self.assertEqual(self.w.users.uids, {"kaskad-xray": 990})       # its uid is in gw_out
        self.assertIn("meta skuid 990 ip daddr 127.0.0.0/8 ct direction original meta l4proto { tcp, udp } "
                      "th dport != 53 drop", self.w.nft.rules("gw_out"))
        self.assertIn(["modprobe", "nft_tproxy"], self.w.runner.commands(["modprobe"]))
        self.assertIn("GW enable: шлюз включён, сеть 10.77.0.0/16", self.log())
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443)})

    def test_base_moves_off_a_busy_network(self):
        self.w.ip = FakeIp(extra_routes=["10.0.0.0/8 dev eth1 proto kernel scope link src 10.1.2.3"])
        self.w.runner._handlers = [h for h in self.w.runner._handlers if h[0] != ["ip"]]
        self.w.runner.on(["ip"], self.w.ip)
        save_state()
        rc, doc, err = self.run_json("gw", "enable")
        self.assertEqual((rc, doc["base"]), (0, "172.27.0.0/16"), err)
        self.assertEqual(self.w.ip.links["kgw0"]["addrs"], ["172.27.0.1/32"])
        rc, doc, _ = self.run_json("gw", "enable", "--base", "10.77.0.0/16")
        self.assertEqual((rc, doc["code"]), (errors.CONFLICT, errors.CONFLICT))
        self.assertIn("10.0.0.0/8", doc["error"])

    def _node(self, node_ip, echo):
        self.w.ip = FakeIp(node_ip=node_ip)
        self.w.runner._handlers = [h for h in self.w.runner._handlers if h[0] != ["ip"]]
        self.w.runner.on(["ip"], self.w.ip)
        self.w.curl.echo_ip = echo

    def test_host_address(self):
        save_state()
        self._node("85.143.0.10", "85.143.0.99")
        rc, doc, err = self.run_json("gw", "enable")
        self.assertEqual((rc, doc["host"], doc["host_detected"]["how"]), (0, "85.143.0.10", "route"))
        self.assertEqual(model.public_host(state.load()), "85.143.0.10")
        self.assertEqual(state.load()["gateway"]["host"], "85.143.0.10")             # what adapters read
        self._node("10.0.0.5", "85.143.0.20")                             # 1:1 NAT cloud
        rc, doc, _ = self.run_json("gw", "host", "--detect")
        self.assertEqual((doc["host"], doc["how"], doc["auto"]), ("85.143.0.20", "echo", True))
        self.ok("gw", "host", "VPN.Example.org.")
        self.assertEqual(state.load()["gateway"]["host"], "vpn.example.org")
        self.assertFalse(state.load()["gateway"]["host_auto"])
        self.ok("gw", "enable")                                          # a manual address is kept
        self.assertEqual(model.public_host(state.load()), "vpn.example.org")
        out, _ = self.ok("gw", "host")
        self.assertIn("vpn.example.org", out)
        for bad in ("10.1.2.3", "bad_name!", "127.0.0.1", "1.2.3"):
            rc, _, err = self.run_cli("gw", "host", bad)
            self.assertEqual(rc, errors.ERROR, bad)
        # PLAN-multi-ip Ш2 (Л3): an IPv4 the server does not have is refused with the ways on; --force sets it anyway
        rc, doc, _ = self.run_json("gw", "enable", "--host", "85.143.0.77")
        self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, "address_not_here"))
        self.assertIn("kaskadctl gw addresses add 85.143.0.77", doc["error"])
        self.ok("gw", "enable", "--host", "85.143.0.77", "--force")       # set together with enable
        self.assertEqual(state.load()["gateway"]["host"], "85.143.0.77")
        self.assertIn("GW host: vpn.example.org (manual)", self.log())

    def test_public_address_unknown_is_a_warning(self):
        save_state()
        self._node("10.0.0.5", "")
        rc, out, err = self.run_cli("gw", "enable")
        self.assertEqual(rc, 0)
        self.assertIn("публичный адрес узла не определён", err)
        self.assertEqual(model.public_host(state.load()), "10.0.0.5")

    def test_disable_falls_back_to_recreating_the_table(self):
        self.enabled_world(pool=False)
        self.w.nft.set_counter("fwd_tcp_443", 9, 900)
        self.w.nft.fail_check = lambda text: ("Error: Could not process rule: Device or resource busy\n"
                                              if "delete map" in text else None)
        self.ok("gw", "disable")
        t = self.w.nft.table()
        self.assertEqual(list(t["chains"]), ["fwd_pre", "fwd_dnat", "fwd_count", "fwd_post"])
        self.assertEqual(dict(t["sets"]), {})
        self.assertEqual(self.w.nft.counter("fwd_tcp_443"), {"packets": 9, "bytes": 900})
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443)})
        self.assertIsNone(self.w.nft.table("kaskad6", "ip6"))

    def test_disable_and_enable_again(self):
        self.enabled_world()
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "Телефон", "--access", "wg")
        self.assertEqual(self.w.nft.elements("gw_route"), {})             # wg is not installed: no route
        self.ok("gw", "inbound", "install", "wg")
        self.assertEqual(len(self.w.nft.elements("gw_route")), 1)
        rc, doc, _ = self.run_json("gw", "disable")
        self.assertEqual(rc, 0)
        st = state.load()
        self.assertFalse(st["gateway"]["enabled"])
        self.assertEqual(len(st["gateway"]["devices"]), 1)                # people and inbounds stay
        self.assertEqual(sorted(st["gateway"]["inbounds"]), ["wg"])
        self.assertFalse(self.ads["wg"].running)
        t = self.w.nft.table()
        self.assertEqual(list(t["chains"]), ["fwd_pre", "fwd_dnat", "fwd_count", "fwd_post"])
        self.assertIsNone(self.w.nft.table("kaskad6", "ip6"))
        self.assertNotIn("gw_", self.read("/etc/kaskad/net/kaskad.nft"))
        self.assertNotIn("from all fwmark 0x200 lookup 200", self.w.ip.rule_texts())
        self.assertNotIn("kgw0", self.w.ip.links)
        self.assertFalse(compat.present_in(self.w.runner))
        for u in ("kaskad-dns.service", collect.UNIT, xray.UNIT):
            self.assertFalse(self.w.sysd.units[u]["active"], u)
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443)})
        self.ok("gw", "enable")
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])
        self.assertEqual(len(self.w.nft.elements("gw_route")), 1)


class InboundTest(CliGwCase):
    UFW = FakeUfw

    def test_install_on_a_running_gateway(self):
        self.enabled_world(pool=False)
        rc, doc, err = self.run_json("gw", "inbound", "install", "wg")
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["record"], {"enabled": True, "port": 51820, "subnet": "10.77.1.0/24"})
        self.assertEqual(self.ads["wg"].calls[:2], ["install", "prepare"])
        self.assertIn("apply", self.ads["wg"].calls)
        self.assertTrue(self.ads["wg"].running)
        self.assertEqual(self.w.ufw.rules, [])                            # UFW is the adapter's business
        self.assertEqual(self.w.runner.commands(["ufw"]), [])
        self.assertEqual(self.w.nft.rules("gw_fwd6", "kaskad6", "ip6"), ['iifname "kgw-wg" drop'])
        self.assertEqual(secrets.load()["wg.server.private"], "SERVERKEY-wg")
        self.assertIn("GW inbound install: wg port=51820", self.log())
        out, _ = self.ok("gw", "inbound", "list")
        self.assertIn("работает", [ln for ln in out.splitlines() if ln.startswith("wg ")][0])
        self.assertIn("не установлен", [ln for ln in out.splitlines() if ln.startswith("awg2 ")][0])
        registry.unregister("pptp")                                       # a kind without an adapter
        out, _ = self.ok("gw", "inbound", "list")
        self.assertIn("нет в этой версии", [ln for ln in out.splitlines() if ln.startswith("pptp ")][0])

    def test_ports_conflicts_and_random_ports(self):
        self.enabled_world(pool=False)
        self.ok("fwd", "add", "--proto", "udp", "--port", "51820", "--to", "198.51.100.7", "--to-port", "51820")
        rc, doc, err = self.run_json("gw", "inbound", "install", "wg")
        self.assertEqual(rc, 0)
        port = doc["record"]["port"]
        self.assertTrue(20000 <= port < 60000 and port != 51820, port)
        self.assertIn("занят", doc["notes"][0])
        self.ok("gw", "inbound", "uninstall", "wg")
        rc, doc, _ = self.run_json("gw", "inbound", "install", "wg", "--port", "51820")
        self.assertEqual((rc, doc["data"]["kind"]), (errors.CONFLICT, "forward"))
        self.ok("gw", "inbound", "install", "wg", "--port", "51820", "--force")
        self.assertEqual(model.inbound(state.load(), "wg")["port"], 51820)
        self.ads.update(register_dummies(("awg1", {"random_port": True, "default_port": 0, "ifaces": ["kgw-awg1"]})))
        rc, doc, _ = self.run_json("gw", "inbound", "install", "awg1")
        self.assertTrue(20000 <= doc["record"]["port"] < 60000)
        rc, doc, _ = self.run_json("gw", "inbound", "port", "awg1", str(model.inbound(state.load(), "wg")["port"]))
        self.assertEqual(rc, errors.CONFLICT)
        self.assertEqual([c["kind"] for c in doc["data"]["conflicts"]], ["forward", "gateway"])
        rc, doc, _ = self.run_json("gw", "inbound", "port", "awg1", "31871")
        self.assertEqual((rc, doc["port"]), (0, 31871))
        self.assertEqual(model.inbound(state.load(), "awg1")["port"], 31871)
        self.assertEqual(self.ads["awg1"].calls[-1], "apply")             # re-applied with the new port
        rc, doc, _ = self.run_json("gw", "inbound", "port", "awg1", "70000")
        self.assertNotEqual(rc, 0)

    def test_kinds_without_adapters_and_unsupported_systems(self):
        self.enabled_world(pool=False)
        registry.unregister("pptp")                                       # a kind without an adapter
        self.assertEqual(self.run_cli("gw", "inbound", "install", "pptp")[0], errors.UNSUPPORTED)
        self.assertEqual(self.run_cli("gw", "inbound", "install", "nosuch")[0], errors.USAGE)
        register_dummies(("ocserv", {"supported": False, "why": "нет пакета ocserv"}))
        rc, out, err = self.run_cli("gw", "inbound", "install", "ocserv")
        self.assertEqual(rc, errors.UNSUPPORTED)
        self.assertIn("нет пакета ocserv", err)

    def test_disable_uninstall_keep_people(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "Телефон", "--access", "wg")
        self.ok("gw", "inbound", "disable", "wg")
        self.assertIn("stop", self.ads["wg"].calls)
        self.assertFalse(self.ads["wg"].running)
        self.assertEqual(self.w.nft.rules("gw_fwd6", "kaskad6", "ip6"), [])
        self.assertEqual(self.w.nft.elements("gw_route"), {})             # its clients are out of the map
        self.ok("gw", "inbound", "enable", "wg")
        self.assertTrue(self.ads["wg"].running)
        self.assertEqual(list(self.w.nft.elements("gw_route")), ["10.77.1.10"])
        self.ok("gw", "inbound", "uninstall", "wg")
        self.assertIn("uninstall", self.ads["wg"].calls)
        st = state.load()
        self.assertIsNone(model.inbound(st, "wg"))
        self.assertEqual(st["gateway"]["devices"][0]["access"], ["wg"])
        self.assertEqual(st["gateway"]["devices"][0]["ips"], {"wg": "10.77.1.10"})
        rc, out, err = self.run_cli("gw", "inbound", "uninstall", "wg")
        self.assertEqual(rc, errors.ERROR)


class PeopleTest(CliGwCase):
    def test_users_devices_routes_and_the_map(self):
        st = self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        rc, doc, _ = self.run_json("gw", "user", "add", "Мама")
        uid = doc["user"]["id"]
        rc, doc, err = self.run_json("gw", "device", "add", "Мама", "Телефон мамы", "--kind", "phone",
                                     "--platform", "android", "--access", "wg")
        self.assertEqual(rc, 0, err)
        d = doc["device"]
        self.assertEqual((d["user"], d["ips"], d["access"]), (uid, {"wg": "10.77.1.10"}, ["wg"]))
        self.assertRegex(d["login"], r"^telefon-mamy-[0-9a-f]{4}$|^telefon-mam-[0-9a-f]{4}$")
        pw = secrets.load()["dev.%s.password" % d["id"]]
        self.assertEqual(len(pw), 14)
        rc, out, _ = self.run_cli("gw", "device", "add", "Мама", "Планшет", "--access", "wg")
        self.assertNotIn(pw, out)
        self.assertNotIn("password", out.lower())
        # pool of two live legs: the device got one of them
        self.w.sysd.units[xray.UNIT]["active"] = True
        self.w.curl.observatory = {"gx-" + self.vid: True}
        collect.tick(self.w.runner)
        m = self.w.nft.elements("gw_route")
        self.assertIn(m["10.77.1.10"], (0x102, 0x301))          # stage 1: a VLESS exit by the device (§2.3)
        self.ok("gw", "device", "route", d["id"], "direct")
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.1.10"], 0x201)
        cfg_before = self.read("/etc/kaskad/gw/xray.json")
        restarts = self.w.sysd.restarts.get(xray.UNIT, 0)
        self.ok("gw", "device", "route", d["id"], "exit:" + self.vid)
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.1.10"], 0x301)       # its own TPROXY: 12401
        self.assertEqual(self.read("/etc/kaskad/gw/xray.json"), cfg_before)          # xray untouched ...
        self.assertEqual(self.w.sysd.restarts.get(xray.UNIT, 0), restarts)           # ... not restarted
        cfg = json.loads(cfg_before)
        # stage 1 (SPEC-stage1-exits §1.3: «без влияния на маршрут»; §2.7, §5.1): the TPROXY of the exit shares its
        # rules ``px-<id>-…`` with the inbound of its probes (``probe-<id>``) — the exit comes and goes through the API
        own = [r for r in cfg["routing"]["rules"] if r.get("inboundTag") == ["tproxy-" + self.vid, "probe-" + self.vid]
               and "ip" not in r and "domain" not in r]
        self.assertEqual([(r["outboundTag"], r["ruleTag"][:len("px-" + self.vid)]) for r in own],
                         [("gx-" + self.vid, "px-" + self.vid)])
        self.ok("gw", "user", "route", "Мама", "block")
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.1.10"], 0x301)       # device route wins
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.1.11"], 0x2ff)       # the tablet: user route
        self.ok("gw", "device", "route", d["id"], "inherit")
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.1.10"], 0x2ff)
        self.ok("gw", "device", "revoke", d["id"], "wg")
        self.assertNotIn("10.77.1.10", self.w.nft.elements("gw_route"))
        self.assertEqual(model.get_device(state.load(), d["id"])["ips"], {})
        self.ok("gw", "device", "grant", d["id"], "wg")
        self.assertEqual(model.get_device(state.load(), d["id"])["ips"], {"wg": "10.77.1.10"})
        out, _ = self.ok("gw", "device", "list")
        self.assertIn("10.77.1.10", out)
        rc, out, err = self.run_cli("gw", "user", "del", "Мама")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("--force", err)
        self.ok("gw", "user", "del", "Мама", "--force")
        self.assertEqual(state.load()["gateway"]["devices"], [])
        self.assertFalse([k for k in secrets.load() if k.startswith("dev.")])
        self.assertEqual(self.w.nft.elements("gw_route"), {})

    def test_people_and_inbounds_while_the_gateway_is_off(self):
        self.ads = self.dummies()
        save_state()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "Телефон", "--access", "wg")
        self.assertEqual(doc["device"]["ips"], {"wg": "10.77.1.10"})
        self.assertIsNone(doc["apply"])
        self.assertEqual(self.ads["wg"].calls, ["install", "prepare"])
        self.assertEqual(self.w.nft_writes(), [])
        self.assertFalse([c for c in self.w.runner.commands(["systemctl"]) if c[1] in ("start", "restart", "enable")])
        self.assertIsNone(self.w.nft.table())
        rc, out, err = self.run_cli("gw", "apply")
        self.assertEqual(rc, errors.ERROR)
        self.assertIn("gw enable", err)

    def test_disabled_device_or_user_is_blocked(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Дача")
        rc, doc, _ = self.run_json("gw", "device", "add", "Дача", "Роутер", "--access", "wg", "--route", "direct")
        did = doc["device"]["id"]
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x201})
        self.ok("gw", "device", "disable", did)
        self.assertEqual(self.w.nft.elements("gw_route"), {})
        self.ok("gw", "device", "enable", did)
        self.ok("gw", "user", "disable", "Дача")
        self.assertEqual(self.w.nft.elements("gw_route"), {})
        with open(self.ads["wg"].conf_path("dummy.conf"), encoding="utf-8") as f:
            self.assertNotIn(did, f.read())                               # no peer for a disabled user

    def test_pause_and_resume_are_logged_by_the_core(self):
        """T-G1b N6: a pause of a person or of a device and its end are named in kaskad.log — by the core, so the
        menu, the panel and the bot (all of them call ops.user_enable / ops.device_enable) leave the same line."""
        from kaskad.gw import ops
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "Телефон", "--access", "wg")
        did, uid = doc["device"]["id"], doc["device"]["user"]
        self.ok("gw", "device", "disable", did)                           # the menu's way: kaskadctl
        self.assertIn("GW device pause: Телефон (%s), Мама — доступ приостановлен" % did, self.log())
        ops.device_enable(self.w.runner, did, True)                       # the panel's and the bot's way: the core
        self.assertIn("GW device resume: Телефон (%s), Мама — доступ возобновлён" % did, self.log())
        ops.device_enable(self.w.runner, did, True)
        self.assertIn("GW device resume: Телефон (%s), Мама — доступ возобновлён (и так был включён)" % did, self.log())
        self.ok("gw", "user", "disable", "Мама")
        self.assertIn("GW user pause: Мама (%s), устройств 1 — доступ приостановлен" % uid, self.log())
        ops.user_enable(self.w.runner, uid, True)
        self.assertIn("GW user resume: Мама (%s), устройств 1 — доступ возобновлён" % uid, self.log())

    def test_issue_is_the_only_place_with_secrets(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "inbound", "install", "awg2")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "Роутер", "--platform", "keenetic",
                                   "--access", "wg,awg2")
        did = doc["device"]["id"]
        out, _ = self.ok("gw", "issue", did)
        self.assertIn("awg2", out.splitlines()[0])                       # Keenetic: AmneziaWG 2.0 first
        self.assertIn("PrivateKey = DEVKEY-%s" % did, out)
        self.assertIn("Как подключить:", out)                            # the beginner's steps come first
        self.assertLess(out.index("Как подключить:"), out.index("PrivateKey"))
        rc, doc, _ = self.run_json("gw", "issue", did)
        self.assertIn("импорт из файла", doc["howto"][1])
        rc, doc, _ = self.run_json("gw", "issue", did, "--inbound", "wg", "--os", "linux")
        self.assertEqual((doc["kind"], doc["os"]), ("wg", "linux"))
        self.assertEqual(doc["howto"], [])                                # Linux: the items say it all
        self.assertEqual(doc["items"][0]["body"],
                         "PrivateKey = DEVKEY-%s\nAddress = 10.77.1.10\nEndpoint = 203.0.113.10\n" % did)
        # steps around a file (MikroTik: the script kaskad-wg.rsc) only when the issuance has that file
        rc, doc, _ = self.run_json("gw", "issue", did, "--inbound", "wg", "--os", "mikrotik")
        self.assertNotIn("kaskad-wg.rsc", [it["filename"] for it in doc["items"]])
        self.assertEqual(doc["howto"], [])
        rc, out, err = self.run_cli("gw", "issue", did, "--inbound", "l2tp")
        self.assertEqual(rc, errors.ERROR)
        self.assertIn("не выдан", err)
        self.assertNotIn("DEVKEY", self.log())
        self.assertIn("GW issue: Роутер (%s) — wg для linux" % did, self.log())
        for argv in (("gw", "status"), ("gw", "device", "list"), ("gw", "exits")):
            rc, out, _ = self.run_cli(*argv)
            self.assertNotIn("DEVKEY", out)
            self.assertNotIn("SERVERKEY", out)


class ExitsTest(CliGwCase):
    def test_exits_list_and_the_watchdog(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        for name in ("A", "B", "C", "D", "E", "F"):
            self.ok("gw", "device", "add", "Мама", name, "--access", "wg")
        rc, doc, _ = self.run_json("gw", "exits")
        rows = dict((e["id"], e) for e in doc["exits"])
        self.assertEqual(rows[self.aid]["kind"], "awg")
        self.assertTrue(rows[self.aid]["usable"])
        self.assertEqual(rows[self.aid]["state"], "alive")
        self.assertFalse(rows[self.vid]["usable"])                        # xray not checked yet ...
        self.assertEqual(rows[self.vid]["state"], "unknown")             # ... which is not «dead»
        out, _ = self.ok("gw", "exits")
        line = [l for l in out.splitlines() if l.startswith(self.vid)][0]
        self.assertIn("ещё не проверялся (xray шлюза не отвечает)", line)
        self.assertNotIn("не работает", line)
        self.assertNotIn("11111111", json.dumps(doc))
        self.w.sysd.units[xray.UNIT]["active"] = True
        self.w.curl.observatory = {"gx-" + self.vid: True}
        res = collect.tick(self.w.runner)
        self.assertTrue(res["exits"]["map_changed"])
        marks = self.w.nft.elements("gw_route")
        self.assertEqual(set(marks.values()), {0x102, 0x301})              # both legs carry clients (VLESS by device)
        on_awg = sorted(ip for ip, m in marks.items() if m == 0x102)
        for _ in range(3):                                                 # 40 s of good checks
            self.now += 10
            collect.tick(self.w.runner)
        self.w.leg(2, handshake=None, probe=False)                        # the far end is gone
        good = len(exits.load_snapshot()["exits"][self.aid]["tries"])
        self.now += 10                                                     # the next check (one per 10 s, 3.3.5)
        for _ in range(good - 1):                                          # 3.3.5: fewer than half of the checks
            res = collect.tick(self.w.runner)                              # of 2 min failed: nobody moves yet
            self.assertEqual(res["exits"]["changes"], [])
            self.assertEqual(self.w.nft.elements("gw_route"), marks)
            self.now += 10
        self.assertNotIn("не работает", self.log())
        res = collect.tick(self.w.runner)                                  # as many failed as good: dead
        self.assertEqual(res["exits"]["changes"], [{"id": self.aid, "was": True, "alive": False}])
        marks2 = self.w.nft.elements("gw_route")
        self.assertEqual(set(marks2.values()), {0x301})                    # the leg's clients moved (VLESS by device)
        self.assertTrue(all(marks2[ip] == marks[ip] for ip in marks if ip not in on_awg))
        self.assertIn("GW exit %s (Amsterdam): не работает — нет ответа %d с\n" % (self.aid, 10 * (good - 1)),
                      self.log())
        self.w.curl.observatory = {"gx-" + self.vid: False}
        t0 = self.now
        for _ in range(10):
            self.now += 10
            collect.tick(self.w.runner)
            if set(self.w.nft.elements("gw_route").values()) == {0x2ff}:
                break
        self.assertLessEqual(self.now - t0, 70)                            # a whole failure: in about a minute
        self.assertEqual(set(self.w.nft.elements("gw_route").values()), {0x2ff})   # fail-closed
        self.assertIn("(xray: задержка 5000 мс, ошибка: context deadline exceeded)", self.log())   # 3.3.5: why
        self.w.leg(2, handshake=10)                                       # both far ends are back:
        self.w.curl.observatory = {"gx-" + self.vid: True}
        for _ in range(2):                                                # two good checks in a row
            self.now += 10
            collect.tick(self.w.runner)
        # stage 1 (SE §1.3 difference 1): from the last step every client takes a fit exit and STAYS pinned to it —
        # the exit that is back later gets only new devices (3.3.5 put every client on its old leg again)
        back = self.w.nft.elements("gw_route")
        self.assertEqual(set(back), set(marks))
        self.assertTrue(set(back.values()) <= {0x102, 0x301}, back)
        self.assertIn("снова работает", self.log())

    def test_exit_preferences_and_pool_changes(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        self.ok("gw", "exit", self.aid, "--disable", "--name", "Амстердам")
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x2ff})
        pref = model.exit_pref(state.load(), self.aid)
        self.assertEqual((pref["enabled"], pref["name"]), (False, "Амстердам"))
        self.ok("gw", "exit", self.aid, "--enable")
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102})
        restarts = self.w.sysd.restarts.get(xray.UNIT, 0)
        write_pool([awg_line(2)])                                         # the VLESS key was removed: renumbered
        self.w.kill_leg(2)
        self.w.xray.api[:] = []
        res = collect.tick(self.w.runner)
        self.assertTrue(res["exits"]["pool_changed"])
        self.assertIn("kawg1", self.w.ip.links)                           # legs brought up again
        # stage 1 (SPEC-stage1-exits §1.3: «без влияния на маршрут»; §2.7): a change of the pool is hot — the exit
        # leaves through the API (its rules, its inbounds, its outbound), xray is not restarted (3.3.5: a restart)
        self.assertEqual(self.w.sysd.restarts.get(xray.UNIT, 0), restarts)
        # the AmneziaWG exit renumbered (line 2 -> 1, its leg kawg1): its outbound replaced (rmo + ado); every rule
        # replaced at once and bo of gw-all again (§2.4); the VLESS exit's inbounds and outbound gone (rmi, rmo)
        self.assertEqual([v for v, _, _ in self.w.xray.api], ["rmo", "ado", "adrules", "bo", "rmi", "rmo"])
        cfg = json.loads(self.read("/etc/kaskad/gw/xray.json"))
        # the AmneziaWG exit, direct, fail-block (the last step without a restart, §2.2) and block
        self.assertEqual([o["tag"] for o in cfg["outbounds"]], [exits.tag(self.aid), "direct", xray.FAIL_BLOCK,
                                                               "block"])


class ExitStateTextTest(CliGwCase):
    def test_before_the_first_probe_an_exit_is_not_dead(self):
        install_parser()
        write_pool([VLESS_A])                                             # the wizard: the gateway is not on yet
        save_state()
        rc, doc, _ = self.run_json("gw", "exits")
        self.assertEqual((doc["exits"][0]["state"], doc["exits"][0]["detail"]), ("unknown", "ещё не проверялся"))
        # the gateway is off: nobody checks the exits — not «ещё не проверялся», but what to do (T-X6)
        self.assertEqual((doc["exits"][0]["gw_off"], doc["exits"][0]["text"]), (True, exits.GW_OFF_TEXT))
        out, _ = self.ok("gw", "exits")
        self.assertIn("включите шлюз, чтобы проверять выходы", out)
        self.assertNotIn("не работает", out)
        self.assertEqual(exits.state_text({"state": "unknown", "gw_off": True, "detail": "ещё не проверялся"}),
                         "включите шлюз, чтобы проверять выходы")
        self.assertEqual(exits.state_text({"state": "alive", "usable": True, "gw_off": True}), "работает")
        t = exits.state_text
        self.assertEqual(t({"usable": True}), "работает")
        self.assertEqual(t({"problem": "ключ не разобран", "state": "dead"}), "ключ не разобран")
        self.assertEqual(t({"state": "dead", "detail": "нет интерфейса kawg2"}), "не работает: нет интерфейса kawg2")
        self.assertEqual(t({"state": "unknown", "detail": "ещё не проверялся"}), "ещё не проверялся")
        self.assertEqual(exits.state_of(None), "unknown")
        self.assertEqual(exits.state_of({"alive": False, "fails": 0, "unknown_since": 1}), "unknown")
        self.assertEqual(exits.state_of({"alive": False, "fails": 2}), "dead")
        self.assertEqual(exits.state_of({"alive": True, "fails": 1}), "alive")


class HooksTest(CliGwCase):
    def test_ikev2_dynamic_addresses(self):
        self.enabled_world()
        register_dummies(("ikev2", {"proto": "", "default_port": 0, "dynamic": True, "ifaces": [],
                                    "fixed_ports": (("udp", 500), ("udp", 4500))}))
        self.ok("gw", "inbound", "install", "ikev2")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "iPhone", "--access", "ikev2", "--route", "direct")
        d = doc["device"]
        self.assertEqual(d["ips"], {})
        rc, doc, err = self.run_json("gw", "hook", "ikev2", "up", d["login"], "10.77.8.10")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["mark"], doc["element"]), (0x201, True))
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.8.10"], 0x201)
        self.assertEqual(hooks.sessions()[0]["device"], d["id"])
        self.ok("gw", "apply")                                            # a full apply keeps the lease
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.8.10"], 0x201)
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])
        self.ok("gw", "hook", "ikev2", "down", d["login"], "10.77.8.10")
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_route"))
        self.assertEqual(hooks.sessions(), [])
        rc, doc, _ = self.run_json("gw", "hook", "ikev2", "up", "nosuch-0000", "10.77.8.11")
        self.assertEqual((rc, doc["ok"]), (errors.ERROR, False))
        self.assertNotIn("10.77.8.11", self.w.nft.elements("gw_route"))
        self.assertIn("неизвестного клиента nosuch-0000", self.log())
        rc, doc, _ = self.run_json("gw", "hook", "ikev2", "up", d["login"], "10.77.1.10")
        self.assertEqual(rc, errors.ERROR)                                # not the IKEv2 subnet
        rc, doc, _ = self.run_json("gw", "hook", "ikev2", "up", "bad name", "10.77.8.12")
        self.assertEqual(rc, errors.USAGE)
        rc, doc, _ = self.run_json("gw", "hook", "ocserv", "up", d["login"], "10.77.11.10")
        self.assertEqual((rc, doc["device"]), (errors.ERROR, None))      # ocserv was not given to it
        self.assertNotIn("10.77.11.10", self.w.nft.elements("gw_route"))
        self.ok("gw", "device", "grant", d["id"], "ocserv")
        self.assertNotIn("10.77.11.10", self.w.nft.elements("gw_route"))  # ocserv is not installed yet
        register_dummies(("ocserv", {"proto": "tcp", "default_port": 4443, "ifaces": ["kgw-oc*"]}))
        self.ok("gw", "inbound", "install", "ocserv")
        rc, doc, _ = self.run_json("gw", "hook", "ocserv", "up", d["login"], "10.77.11.10")
        self.assertEqual((rc, doc["device"]), (0, d["id"]))               # static kinds: only the session
        self.assertNotIn("element", doc)
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.11.10"], 0x201)    # its static address (grant)
        self.assertEqual([s["kind"] for s in hooks.sessions()], ["ocserv"])
        self.ok("gw", "inbound", "disable", "ikev2")                      # leases of a disabled inbound: out
        self.ok("gw", "hook", "ikev2", "up", d["login"], "10.77.8.13")
        self.assertNotIn("10.77.8.13", self.w.nft.elements("gw_route"))
        self.ok("gw", "inbound", "enable", "ikev2")                       # the session is still there: back in
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.8.13"], 0x201)

    def test_hook_goes_on_when_the_state_is_busy(self):
        self.enabled_world(pool=False)
        register_dummies(("ikev2", {"proto": "", "default_port": 0, "dynamic": True, "ifaces": []}))
        self.ok("gw", "inbound", "install", "ikev2")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "iPhone", "--access", "ikev2")
        login = doc["device"]["login"]
        from unittest import mock
        with mock.patch.object(hooks, "LOCK_WAIT", 0.2):
            with lock_held_elsewhere():                  # an apply of another process holds it (T1: not the same thread)
                res = hooks.hook("ikev2", "up", login, "10.77.8.20", self.w.runner)
        self.assertTrue(res["element"])
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.8.20"], 0x2ff)     # pool without exits: block

    def test_rekey_keeps_the_address_until_the_last_down(self):
        self.enabled_world(pool=False)
        register_dummies(("ikev2", {"proto": "", "default_port": 0, "dynamic": True, "ifaces": []}))
        self.ok("gw", "inbound", "install", "ikev2")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "iPhone", "--access", "ikev2", "--route", "direct")
        login = doc["device"]["login"]
        self.ok("gw", "hook", "ikev2", "up", login, "10.77.8.10")
        self.ok("gw", "hook", "ikev2", "up", login, "10.77.8.10")          # strongSwan: new SA before old down
        rc, doc, _ = self.run_json("gw", "hook", "ikev2", "down", login, "10.77.8.10")
        self.assertEqual((rc, doc["still"]), (0, 1))
        self.assertNotIn("element", doc)
        self.assertEqual(self.w.nft.elements("gw_route")["10.77.8.10"], 0x201)    # still online
        self.ok("gw", "hook", "ikev2", "down", login, "10.77.8.10")
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_route"))
        self.assertEqual(hooks.sessions(), [])
        self.ok("gw", "hook", "ikev2", "down", login, "10.77.8.10")         # a stray down is harmless

    def test_fixed_ports_conflict_and_the_core_leaves_ufw_alone(self):
        self.w = self.make_world(ufw=FakeUfw())
        import kaskad.sysrun as sysrun
        sysrun._default = self.w.runner
        self.enabled_world(pool=False)
        register_dummies(("ikev2", {"proto": "", "default_port": 0, "dynamic": True, "ifaces": [],
                                    "fixed_ports": (("udp", 500), ("udp", 4500))}))
        self.ok("gw", "inbound", "install", "ikev2")
        self.assertEqual(self.w.ufw.rules, [])                            # adapters open their own ports
        self.assertFalse([c for c in self.w.runner.commands(["ufw"]) if len(c) > 1 and c[1] in ("allow", "delete")])
        rc, doc, _ = self.run_json("fwd", "add", "--proto", "udp", "--port", "4500", "--to", "198.51.100.7",
                                   "--to-port", "4500")
        self.assertEqual((rc, doc["data"]["kind"]), (errors.CONFLICT, "gateway"))
        self.assertEqual(self.run_cli("gw", "check")[0], 0)
        self.ok("gw", "inbound", "uninstall", "ikev2")
        self.assertIn("uninstall", registry_get("ikev2").calls)


class WatchdogTest(CliGwCase):
    def healthy(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        compat.ensure(self.w.runner)
        res = netcheck.check(self.w.runner, repair=False)
        self.assertEqual(res["problems"], [])
        return res

    def kinds(self, res):
        return [p["kind"] for p in res["problems"]]

    def test_flushed_ruleset_comes_back_whole(self):
        self.healthy()
        self.w.nft.flush_ruleset()
        res = netcheck.check(self.w.runner, repair=False)
        self.assertEqual(self.kinds(res), ["table_missing", "gw_ip6"])
        res = netcheck.check(self.w.runner, repair=True)
        self.assertTrue(res["ok"], res)
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])
        self.assertEqual(net.ip6_problems(state.load(), self.w.runner), [])

    def test_routing_compat_in_services(self):
        self.healthy()
        fwd_rules = self.w.nft.rules("gw_fwd")
        self.w.ip.rules = [r for r in self.w.ip.rules if "0x200" not in r[1]]
        del self.w.ip.links["kgw0"]
        compat.remove_in(self.w.runner)
        self.w.sysd.units["kaskad-dns.service"]["active"] = False
        self.w.nft.run_script("flush chain ip kaskad gw_fwd\n", True)
        before = len(self.w.runner.calls)
        res = netcheck.check(self.w.runner, repair=False)
        self.assertEqual(self.kinds(res), ["drift", "gw_routing", "gw_compat_in", "gw_service"])
        self.assertFalse(any(c[:3] in (["ip", "rule", "add"], ["ip", "link", "add"])
                             for c, _ in self.w.runner.calls[before:]))
        res = netcheck.check(self.w.runner, repair=True)
        self.assertTrue(res["ok"], res)
        self.assertIn("from all fwmark 0x200 lookup 200", self.w.ip.rule_texts())
        self.assertEqual(self.w.ip.links["kgw0"]["addrs"], ["10.77.0.1/32"])
        self.assertTrue(compat.present_in(self.w.runner))
        self.assertTrue(self.w.sysd.units["kaskad-dns.service"]["active"])
        self.assertEqual(self.w.nft.rules("gw_fwd"), fwd_rules)
        self.assertIn("GW repair", self.log())
        self.assertEqual(netcheck.check(self.w.runner, repair=False)["problems"], [])

    def test_services_restart_without_waiting(self):
        self.healthy()
        self.w.sysd.units["kaskad-dns.service"]["active"] = False
        res = netcheck.check(self.w.runner, repair=True)
        self.assertTrue(res["ok"], res)
        self.assertIn(["systemctl", "restart", "--no-block", "kaskad-dns.service"], self.w.sysd.calls)

    def test_boot_run_leaves_services_and_exits_to_systemd(self):
        self.healthy()
        del self.w.ip.links["kgw0"]
        self.w.sysd.units["kaskad-dns.service"]["active"] = False
        self.w.sysd.units[collect.UNIT]["active"] = False
        self.w.sysd.units["kaskad-net.service"] = {"enabled": True, "active": False, "activating": True}
        before = len(self.w.runner.calls)
        res = netcheck.check(self.w.runner, repair=True)
        self.assertEqual(self.kinds(res), ["gw_routing"])
        self.assertTrue(res["ok"], res)
        calls = [c for c, _ in self.w.runner.calls[before:]]
        self.assertFalse([c for c in calls if c[:2] in (["systemctl", "restart"], ["systemctl", "start"])])
        self.assertFalse([c for c in calls if c[0] in ("curl", paths.egress_awg_tool(), paths.script_copy())])
        self.assertIn("kgw0", self.w.ip.links)

    def test_inbound_down_is_repaired_and_gw_check_cli(self):
        self.healthy()
        self.ads["wg"].running = False
        self.ads["wg"].calls = []
        rc, doc, _ = self.run_json("gw", "check")
        self.assertEqual((rc, [p["kind"] for p in doc["problems"]]), (errors.ERROR, ["gw_inbound"]))
        self.assertEqual(self.ads["wg"].calls, [])                         # a report changes nothing
        rc, out, err = self.run_cli("gw", "check", "--repair", "--quiet")
        self.assertEqual((rc, out, err), (0, "", ""))
        self.assertEqual(self.ads["wg"].calls, ["prepare", "apply"])
        self.assertTrue(self.ads["wg"].running)

    def test_disabled_gateway_costs_nothing(self):
        save_state([F443])
        nft.apply(state.load(), self.w.runner)
        compat.ensure(self.w.runner)
        before = len(self.w.runner.calls)
        netcheck.check(self.w.runner, repair=True)
        self.assertFalse([c for c, _ in self.w.runner.calls[before:] if c[0] in ("ip", "curl", "dpkg-query")])

    def test_collector_loop_survives_errors(self):
        self.healthy()
        calls = []

        def boom(runner, log=None):
            calls.append(1)
            raise RuntimeError("boom")
        orig = collect.tick
        collect.tick = boom
        try:
            from unittest import mock
            with mock.patch.object(collect, "_sleep", lambda s: None):
                self.assertEqual(collect.loop(self.w.runner, interval=10, rounds=3), 3)
        finally:
            collect.tick = orig
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.log().count("GW collect: ошибка — RuntimeError"), 1)


class SessionsTest(CliGwCase):
    def test_collect_once_and_sessions(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        register_dummies(("ocserv", {"proto": "tcp", "default_port": 4443, "ifaces": ["vpns*"]}))
        self.ok("gw", "inbound", "install", "ocserv")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "Ноутбук", "--access", "wg,ocserv")
        d = doc["device"]
        from kaskad.gw import registry
        registry.get("wg").sessions = [{"device": d["id"], "ip": "10.77.1.10", "since": "2026-10-04T12:00:00Z",
                                        "rx": 10, "tx": 20}]
        self.ok("gw", "hook", "ocserv", "up", d["login"], "10.77.11.10")
        rc, doc, _ = self.run_json("gw", "collect", "--once")
        self.assertEqual((rc, doc["enabled"], doc["sessions"]), (0, True, 2))
        rc, doc, _ = self.run_json("gw", "sessions")
        rows = sorted((s["kind"], s["ip"], s["name"]) for s in doc["sessions"])
        self.assertEqual(rows, [("ocserv", "10.77.11.10", "Ноутбук"), ("wg", "10.77.1.10", "Ноутбук")])
        out, _ = self.ok("gw", "sessions")
        self.assertIn("10.77.11.10", out)
        self.assertEqual(self.w.nft.rules("gw_fwd6", "kaskad6", "ip6"), ['iifname "kgw-wg" drop',
                                                                        'iifname "vpns*" drop'])

    def test_collect_off_gateway(self):
        save_state()
        rc, doc, _ = self.run_json("gw", "collect", "--once")
        self.assertEqual((rc, doc["enabled"]), (0, False))
        self.assertEqual(self.w.runner.commands(["nft"]), [])

    def tz(self, name):
        old = os.environ.get("TZ")
        os.environ["TZ"] = name
        time.tzset()

        def back():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.addCleanup(back)

    def test_since_is_local_time(self):
        self.tz("MSK-3")                                     # UTC+3 without tzdata
        now = 1791131435 + 3600                              # 2026-10-04 20:30:35 MSK
        cases = [(1791131435, "19:30"), ("1791131435", "19:30"), (1791131435.7, "19:30"),
                 (1790000000, "21.09, 17:13"),                    # another day: with the date
                 ("2026-10-04T12:00:00Z", "15:00"),               # hooks write ISO in UTC
                 ("2026-10-03T20:00:00Z", "03.10, 23:00"),
                 (None, ""), ("", ""), (0, ""), ("вчера", "вчера")]
        for value, want in cases:
            self.assertEqual(gwcli.since_text(value, now), want, value)
        self.tz("UTC0")
        self.assertEqual(gwcli.since_text(1791131435, now), "16:30")
        with i18n.use_lang("en"):                            # one date format per language (3.3.2, finding №2)
            self.assertEqual(gwcli.since_text("2026-10-03T20:00:00Z", now), "Oct 3, 20:00")
            self.assertEqual(gwcli.since_text("2025-12-31T20:00:00Z", now), "Dec 31, 2025, 20:00")

    def test_sessions_text_shows_the_time_json_keeps_the_epoch(self):
        self.tz("MSK-3")
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "Ноутбук", "--access", "wg")
        from kaskad.gw import registry
        registry.get("wg").sessions = [{"device": doc["device"]["id"], "ip": "10.77.1.10", "since": 1791131435,
                                        "rx": 10, "tx": 20}]
        self.ok("gw", "collect", "--once")
        with mock.patch.object(gwcli, "_now", lambda: 1791131435 + 3600):
            out, _ = self.ok("gw", "sessions")
        row = [l for l in out.splitlines() if "10.77.1.10" in l][0]
        self.assertTrue(row.rstrip().endswith(" 19:30"), row)
        self.assertNotIn("1791131435", out)
        rc, doc, _ = self.run_json("gw", "sessions")
        self.assertEqual([s["since"] for s in doc["sessions"]], [1791131435])


class NetworkdTest(CliGwCase):
    def test_the_gateway_writes_it_and_leaves_it_to_the_egress_legs(self):
        touch_exec(self.path("/usr/lib/systemd/systemd-networkd"))
        p = paths.networkd_dropin()
        self.enabled_world()                                  # the pool has an AWG leg: kawg2 is up
        self.assertEqual(self.read("/etc/systemd/networkd.conf.d/90-kaskad.conf"), gsys.NETWORKD_DROPIN)
        os.unlink(p)
        self.run_json("gw", "check")                          # a report writes nothing
        self.assertFalse(os.path.exists(p))
        self.run_cli("gw", "check", "--repair", "--quiet")    # a gateway enabled by a build without it: the
        self.assertTrue(os.path.exists(p))                    # minute watchdog writes it
        self.ok("gw", "disable")
        self.assertTrue(os.path.exists(p))                    # the leg of «Прокси для API Telegram» needs it
        self.ok("gw", "enable")
        self.w.kill_leg(2)                                    # the egress stopped: its legs and rules went
        self.w.ip.rules = [r for r in self.w.ip.rules if "0x102" not in r[1]]
        rc, doc, _ = self.run_json("gw", "disable")
        self.assertEqual((rc, doc["networkd_released"]), (0, True))
        self.assertFalse(os.path.exists(p))
        self.assertIn("NETWORKD: %s удалён" % p, self.log())
        self.assertEqual([a for a, _ in self.w.runner.calls if any("networkd" in x for x in a)], [])

    def test_net_teardown_removes_it_when_nothing_is_left(self):
        touch_exec(self.path("/usr/lib/systemd/systemd-networkd"))
        self.enabled_world(pool=False)
        self.assertTrue(os.path.exists(paths.networkd_dropin()))
        self.ok("net", "teardown")
        self.assertFalse(os.path.exists(paths.networkd_dropin()))


class TeardownAndConflictsTest(CliGwCase):
    def test_net_teardown_takes_the_gateway_down(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        rc, doc, _ = self.run_json("net", "teardown")
        self.assertEqual(rc, 0)
        self.assertIsNone(self.w.nft.table())
        self.assertIsNone(self.w.nft.table("kaskad6", "ip6"))
        self.assertNotIn("from all fwmark 0x200 lookup 200", self.w.ip.rule_texts())
        self.assertNotIn("kgw0", self.w.ip.links)
        self.assertFalse(compat.present_in(self.w.runner))
        self.assertFalse(self.w.sysd.units["kaskad-dns.service"]["active"])
        self.assertIn("stop", self.ads["wg"].calls)
        self.assertTrue(model.enabled(state.load()))                     # the caller decides about state

    def test_forward_on_an_inbound_port_is_a_conflict(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        rc, doc, _ = self.run_json("fwd", "add", "--proto", "udp", "--port", "51820", "--to", "198.51.100.7",
                                   "--to-port", "51820")
        self.assertEqual((rc, doc["data"]["kind"]), (errors.CONFLICT, "gateway"))
        self.assertIn("протокол шлюза", doc["error"])

    def test_status(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        rc, doc, _ = self.run_json("gw", "status")
        self.assertEqual(rc, 0)
        self.assertTrue(doc["enabled"])
        self.assertEqual(doc["net"], {"base": "10.77.0.0/16", "dns": "10.77.0.1"})
        self.assertEqual(doc["host"], "203.0.113.10")
        self.assertEqual([r["kind"] for r in doc["inbounds"] if r["installed"]], ["wg"])
        self.assertEqual(len(doc["exits"]), 2)
        self.assertEqual(doc["problems"], [])
        rc, out, _ = self.run_cli("gw", "status")
        self.assertIn("шлюз: включён", out)
        self.assertIn("Тестовый вход wg (wg): работает, порт 51820/udp", out)


class DeviceResetOpsTest(CliGwCase):
    """``gw device reset`` in the core: what goes (the adapters say), the shared password of L2TP, SSTP, IKEv2
    and OpenConnect changes for all of them (``also``), revoke deletes by the adapter's prefixes."""

    def shared_dummies(self):
        from kaskad.gw.inbounds.base import DummyInbound

        class Shared(DummyInbound):
            def reset_secrets(self, device_id):
                return ["dev.%s.password" % device_id]

        class Ovpn(DummyInbound):
            def secret_prefixes(self, device_id):
                return ["dev.%s.ovpn." % device_id]

        ads = {}
        for kind in ("l2tp", "ikev2"):
            ads[kind] = Shared(kind=kind, proto=None, ifaces=["kgw-%s*" % kind])
            registry.register(ads[kind])
        ads["openvpn"] = Ovpn(kind="openvpn", default_port=1194, ifaces=["kgw-ovpn-u"])
        registry.register(ads["openvpn"])
        return ads

    def test_shared_password_and_prefixes(self):
        self.dummies()
        self.shared_dummies()
        save_state([F443])
        self.ok("gw", "enable")
        for k in ("l2tp", "ikev2", "openvpn", "wg"):
            self.ok("gw", "inbound", "install", k)
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "Ноутбук", "--access", "l2tp,ikev2,openvpn,wg")
        did = doc["device"]["id"]
        sec = secrets.load()
        old_pw, old_wg = sec["dev.%s.password" % did], sec["dev.%s.wg.private" % did]
        rc, doc, err = self.run_json("gw", "device", "reset", did, "--inbound", "l2tp")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["kinds"], doc["also"]), (["l2tp"], ["ikev2"]))
        sec = secrets.load()
        self.assertNotEqual(sec["dev.%s.password" % did], old_pw)
        self.assertEqual(sec["dev.%s.wg.private" % did], old_wg)
        out, _ = self.ok("gw", "device", "reset", did, "--inbound", "l2tp")
        self.assertIn("Общий пароль устройства сменился и у: ikev2.", out)
        secrets.set("dev.%s.ovpn.crt" % did, "CERT")
        secrets.set("dev.%s.openvpn.junk" % did, "X")
        self.ok("gw", "device", "revoke", did, "openvpn")                       # the adapter's prefix: ovpn
        sec = secrets.load()
        self.assertNotIn("dev.%s.ovpn.crt" % did, sec)
        self.assertIn("dev.%s.password" % did, sec)

    def test_pptp_hook_identity_is_the_device(self):
        register_dummies(("pptp", {"proto": None, "ifaces": ["kgw-pptp*"]}))
        save_state([F443])
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "pptp")
        self.ok("gw", "user", "add", "Дача")
        rc, doc, _ = self.run_json("gw", "device", "add", "Дача", "Роутер", "--access", "pptp")
        d = doc["device"]
        rc, res, _ = self.run_json("gw", "hook", "pptp", "up", d["login"] + "-pptp", d["ips"]["ppp"])
        self.assertEqual((rc, res["device"]), (0, d["id"]))
        rc, res, _ = self.run_json("gw", "hook", "l2tp", "up", d["login"] + "-pptp", d["ips"]["ppp"])
        self.assertIsNone(res["device"])                                         # the suffix means PPTP only


class PkiRotateTest(CliGwCase):
    """``gw pki rotate ikev2 --yes``: the adapter's rotate_pki under the state lock, then the usual apply; the
    status shows what an adapter warns about (a CA of an earlier build)."""

    def ike(self):
        from kaskad.gw.inbounds.base import DummyInbound

        class Ike(DummyInbound):
            def rotate_pki(self, st, sec, runner):
                self.calls.append("rotate")
                sec.set("ikev2.ca.crt", "NEWCA")
                return ["центр сертификации IKEv2 для 203.0.113.10", "сертификат сервера IKEv2 для 203.0.113.10"]

            def status(self, runner):
                out = super(Ike, self).status(runner)
                out["warnings"] = ["центр сертификации IKEv2 старого образца"]
                return out

        ad = Ike(kind="ikev2", proto=None, dynamic=True, ifaces=[])
        registry.register(ad)
        return ad

    def test_rotate_needs_yes_and_applies(self):
        self.dummies()
        ad = self.ike()
        save_state([F443])
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "ikev2")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "Айфон", "--access", "ikev2")
        rc, out, err = self.run_cli("gw", "pki", "rotate", "ikev2")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("--yes", err)
        self.assertNotIn("rotate", ad.calls)
        ad.calls[:] = []
        rc, doc, err = self.run_json("gw", "pki", "rotate", "ikev2", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(ad.calls[0], "rotate")
        self.assertIn("apply", ad.calls)                                  # then the whole gateway, as any change
        self.assertEqual(doc["created"][0], "центр сертификации IKEv2 для 203.0.113.10")
        self.assertEqual(len(doc["devices"]), 1)
        self.assertEqual(secrets.load()["ikev2.ca.crt"], "NEWCA")
        out, _ = self.ok("gw", "pki", "rotate", "ikev2", "--yes")
        self.assertIn("подтверждает только 203.0.113.10", out)
        self.assertIn("Выдайте устройствам ikev2 настройки заново (устройств: 1)", out)
        self.assertIn("GW pki rotate: ikev2 — центр сертификации IKEv2 для 203.0.113.10", self.log())
        rc, out, err = self.run_cli("gw", "pki", "rotate", "wg", "--yes")
        self.assertEqual(rc, errors.USAGE)
        out, _ = self.ok("gw", "status")
        self.assertIn("    ! центр сертификации IKEv2 старого образца", out)

    def test_rotate_of_an_inbound_not_installed(self):
        self.ike()
        save_state()
        rc, out, err = self.run_cli("gw", "pki", "rotate", "ikev2", "--yes")
        self.assertEqual(rc, errors.ERROR)
        self.assertIn("не установлен", err)


class InheritTextTest(CliGwCase):
    def test_a_person_inherits_the_default_a_device_its_person(self):
        """No own route: a person takes the node's default («как по умолчанию узла»), a device — its person's («как у
        человека»): `gw user list`, `gw user route … inherit`, `gw device route … inherit`."""
        st = self.scenario(enabled=False)
        st["gateway"]["users"][0].pop("route", None)
        state.save(st)
        u = st["gateway"]["users"][0]
        out, _ = self.ok("gw", "user", "list")
        line = [l for l in out.splitlines() if l.startswith(u["id"])][0]
        self.assertIn("маршрут: как по умолчанию узла", line)
        self.assertNotIn("как у пользователя", out)
        # M6 332c №10: в JSON — и действующий маршрут человека (меню 17 → 2: «как по умолчанию — сейчас: …»)
        st["gateway"]["routes"]["default"] = {"via": "direct", "fail": "block"}
        state.save(st)
        doc = json.loads(self.ok("gw", "user", "list", "--json")[0])
        row = [r for r in doc["users"] if r["id"] == u["id"]][0]
        self.assertIsNone(row.get("route"))
        self.assertEqual((row["route_effective"]["via"], row["route_effective"]["from"]), ("direct", "default"))
        out, _ = self.ok("gw", "user", "route", u["id"], "inherit")
        self.assertIn(": как по умолчанию узла.", out)
        out, _ = self.ok("gw", "device", "route", "d-00000001", "inherit")
        self.assertIn(": как у пользователя.", out)
        out, _ = self.ok("gw", "user", "route", u["id"], "block")
        self.assertIn(": block.", out)


class FailShownTest(CliGwCase):
    def test_block_and_direct_never_say_a_fail_policy(self):
        """A route «блок» or «напрямую» may keep the fail policy it inherited (model.kept_fail: «напрямую» comes back with
        the pool) — it means nothing there: no «при отказе» in the CLI (gw route, user/device route, device list), in the
        bot, in the scheme's texts. A route through the exits still says it."""
        from kaskad.bot import gateway as bot_gw
        from kaskad.gw import cli as gw_cli
        from kaskad.panel import scheme
        st = self.scenario(enabled=False)
        st["gateway"]["routes"]["default"] = {"via": "pool", "fail": "direct"}
        state.save(st)
        for via in ("block", "direct"):
            out, _ = self.ok("gw", "device", "route", "d-00000001", via)
            self.assertNotIn("при отказе", out, via)
            self.assertEqual(model.get_device(state.load(), "d-00000001")["route"], {"via": via, "fail": "direct"})  # kept
            out, _ = self.ok("gw", "device", "list")
            self.assertNotIn("при отказе", [l for l in out.splitlines() if "Телефон" in l][0])
            out, _ = self.ok("gw", "route", via)
            self.assertNotIn("при отказе", out)
            r = {"via": via, "fail": "direct"}
            self.assertEqual(gw_cli._route_text(r), via)
            self.assertNotIn("при отказе", bot_gw._route_text(r, state.load()))
            self.assertNotIn("при отказе", scheme._route_text(state.load(), r, []))
        out, _ = self.ok("gw", "device", "route", "d-00000001", "pool")
        self.assertIn("при отказе — напрямую", out)                        # the policy is back with the pool
        self.assertIn("GW route default -> direct\n", self.log() + "\n")
