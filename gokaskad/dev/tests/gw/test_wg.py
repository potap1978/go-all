"""WireGuard inbound: golden configs, keys, install, apply without dropping sessions, status, issuance."""

import json
import os

from kaskad import paths
from tests.gw.tunnel_case import GwCase, device, gw_state, key, slurp

WG = {"wg": {"enabled": True, "port": 51820, "subnet": "10.77.1.0/24"}}


def devices():
    return [
        device("d-phone", "Телефон Мамы", "ios", ["wg", "awg3"], ips={"wg": "10.77.1.10"}),
        device("d-dacha", "Роутер на даче", "mikrotik", ["wg"], kind="router", ips={"wg": "10.77.1.11"}),
        device("d-tv", "Телевизор", "androidtv", ["awg3"], kind="tv"),
        device("d-off", "Старый ноутбук", "windows", ["wg"], kind="laptop", enabled=False, ips={"wg": "10.77.1.12"}),
    ]


class WgCase(GwCase):
    def setUp(self):
        super().setUp()
        self.st = gw_state(WG, devices())
        self.wg = self.adapter("wg")

    def seed(self):
        self.sec.update({
            "wg.server.private": key("srv-wg"),
            "dev.d-phone.wg.private": key("dev1-wg"), "dev.d-phone.wg.psk": key("psk1"),
            "dev.d-dacha.wg.private": key("dev2-wg"), "dev.d-dacha.wg.psk": key("psk2"),
        })

    def dev(self, dev_id):
        return [d for d in self.st["gateway"]["devices"] if d["id"] == dev_id][0]

    def installed(self):
        self.wg.install(self.runner)
        self.node.runner.calls[:] = []
        self.node.sysd.calls[:] = []


class RenderTest(WgCase):
    def test_golden_server_files(self):
        self.seed()
        self.wg.prepare(self.st, self.sec)
        files = self.files(self.wg.render(self.st, self.sec))
        self.assertEqual(sorted(files), sorted(["/etc/kaskad/gw/wg/env", "/etc/wireguard/kgw-wg.conf",
                                                "/etc/kaskad/gw/wg/peers.json"]))
        self.assertTrue(all(mode == 0o600 for _, mode in files.values()))
        self.golden("wg-server.conf", files["/etc/wireguard/kgw-wg.conf"][0])
        self.assertEqual(files["/etc/kaskad/gw/wg/env"][0],
                         "# Kaskad 3 gateway: settings of kaskad-gw-wg.service (written by kaskadctl)\n"
                         "ADDR=10.77.1.1/24\nMTU=1280\n")
        peers = json.loads(files["/etc/kaskad/gw/wg/peers.json"][0])
        self.assertEqual(sorted(v["device"] for v in peers.values()), ["d-dacha", "d-phone"])   # not d-off, not d-tv

    def test_golden_unit(self):
        self.golden("kaskad-gw-wg.service", self.wg.unit_texts(self.runner)["kaskad-gw-wg.service"])

    def test_prepare_creates_what_is_missing_once(self):
        st = gw_state({"wg": {"enabled": True}}, [device("d-new", "Новый", "android", ["wg"])])
        done = self.wg.prepare(st, self.sec)
        cfg = st["gateway"]["inbounds"]["wg"]
        self.assertEqual(cfg["port"], 51820)
        self.assertEqual(cfg["subnet"], "10.77.1.0/24")
        self.assertEqual(cfg["pub"], self.gw.wg.pubkey(self.sec["wg.server.private"]))
        self.assertEqual(st["gateway"]["devices"][0]["ips"]["wg"], "10.77.1.10")
        self.assertEqual(sorted(self.gw.secrets.SETS), ["dev.d-new.wg.private", "dev.d-new.wg.psk", "wg.server.private"])
        self.assertIn("ключ сервера", done)
        self.assertEqual(self.wg.prepare(st, self.sec), [])                     # nothing new the second time
        self.assertEqual(len(self.gw.secrets.SETS), 3)
        self.assertNotIn(self.sec["wg.server.private"], " ".join(done))

    def test_render_skips_devices_without_keys(self):
        self.sec["wg.server.private"] = key("srv-wg")
        text = self.wg.render(self.st, self.sec)[self.wg.conf_path()][0]
        self.assertNotIn("[Peer]", text)


class InstallTest(WgCase):
    def test_install_packages_without_autostart_then_unit(self):
        self.wg.install(self.runner)
        self.assertEqual(self.node.apt, [(["wireguard-tools"], True)])          # under policy-rc.d = 101
        self.assertFalse(os.path.exists(paths.P("/usr/sbin/policy-rc.d")))       # removed afterwards
        self.assertEqual(self.node.modprobed, ["wireguard"])
        self.assertTrue(os.path.exists(os.path.join(paths.systemd_dir(), "kaskad-gw-wg.service")))
        self.assertEqual(self.node.sysd.reloads, 1)
        self.assertEqual([c for c in self.node.sysd.calls if c[0] in ("mask", "disable")], [])  # nothing to mask
        self.assertEqual(json.loads(slurp(paths.P("/etc/kaskad/gw/packages.json"))),
                         {"wireguard-tools": {"by_kaskad": True}})
        self.node.apt[:] = []
        self.wg.install(self.runner)                                           # idempotent: no apt-get again
        self.assertEqual(self.node.apt, [])

    def test_requirements(self):
        req = self.wg.requirements(self.runner)
        self.assertEqual((req["packages"], req["modules"], req["supported"]), (["wireguard-tools"], ["wireguard"], True))
        self.assertEqual(req["ports"], [{"proto": "udp", "port": 51820}])
        self.node.modules.discard("wireguard")
        req = self.wg.requirements(self.runner)
        self.assertFalse(req["supported"])
        self.assertIn("wireguard", req["why"])

    def test_uninstall_keeps_packages_and_keys(self):
        self.node.ufw_active = True
        self.seed()
        self.wg.install(self.runner)
        self.wg.apply(self.st, self.sec, self.runner)
        self.assertTrue(os.path.exists(paths.P("/etc/wireguard/kgw-wg.conf")))
        self.wg.uninstall(self.runner)
        self.assertFalse(os.path.exists(os.path.join(paths.systemd_dir(), "kaskad-gw-wg.service")))
        self.assertFalse(os.path.exists(paths.P("/etc/kaskad/gw/wg")))
        self.assertFalse(os.path.exists(paths.P("/etc/wireguard/kgw-wg.conf")))     # the server key is not left
        self.assertIn("disable --now", self.node.sysd.verbs("kaskad-gw-wg.service"))
        self.assertEqual(self.node.ufw, ["allow 51820/udp comment kaskad-gw wg", "delete allow 51820/udp"])
        self.assertIn("wg.server.private", self.sec)


class ApplyTest(WgCase):
    def test_first_apply_starts_the_unit(self):
        self.seed()
        self.installed()
        res = self.wg.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "started")
        self.assertEqual(self.node.sysd.verbs("kaskad-gw-wg.service"), ["is-active", "enable --no-reload --now"])
        self.assertEqual(self.node.syncs, [])
        self.assertTrue(self.node.sysd.units["kaskad-gw-wg.service"]["active"])

    def test_peer_changes_go_through_syncconf_without_restart(self):
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        self.node.sysd.calls[:] = []
        self.st["gateway"]["devices"].append(device("d-laptop", "Ноутбук", "linux", ["wg"], kind="laptop"))
        res = self.wg.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "synced")
        self.assertEqual(self.node.sysd.verbs("kaskad-gw-wg.service"), ["is-active", "is-enabled"])  # no restart
        tool, verb, iface, text = self.node.syncs[-1]
        self.assertEqual((tool, verb, iface), ("wg", "syncconf", "kgw-wg"))
        self.assertIn("# device d-laptop", text)
        self.assertIn("AllowedIPs = 10.77.1.13/32", text)       # .12 stays with the disabled device
        self.dev("d-phone")["access"] = ["awg3"]                # access revoked -> peer leaves
        res = self.wg.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "synced")
        self.assertNotIn("# device d-phone", self.node.syncs[-1][3])
        self.assertEqual(self.wg.apply(self.st, self.sec, self.runner)["action"], "unchanged")

    def test_port_change_is_synced_and_moves_the_ufw_rule(self):
        self.node.ufw_active = True
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        self.st["gateway"]["inbounds"]["wg"]["port"] = 51830
        self.assertEqual(self.wg.apply(self.st, self.sec, self.runner)["action"], "synced")
        self.assertIn("ListenPort = 51830", self.node.syncs[-1][3])
        self.assertEqual(self.node.ufw, ["allow 51820/udp comment kaskad-gw wg", "delete allow 51820/udp",
                                         "allow 51830/udp comment kaskad-gw wg"])

    def test_failed_syncconf_falls_back_to_a_restart(self):
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        self.runner.on(["/usr/sbin/wg", "syncconf"], (1, "", "Unable to modify interface: No such device"))
        self.runner._handlers.insert(0, self.runner._handlers.pop())          # before the generic wg fake
        self.st["gateway"]["devices"].append(device("d-laptop", "Ноутбук", "linux", ["wg"], kind="laptop"))
        self.assertEqual(self.wg.apply(self.st, self.sec, self.runner)["action"], "restarted")
        self.assertIn("syncconf не прошёл (Unable to modify interface: No such device)", self.log_text())

    def card(self, mtu):
        self.st["gateway"]["mtu"] = {"nic": {"dev": "eth0", "mtu": mtu}}

    def test_mtu_only_goes_onto_the_live_interface_without_a_restart(self):
        # T-N5 3.2.19, №3: the card 1500 -> 1300 (and an update with a new server MTU) restarted the inbound — every
        # AmneziaWG client lost 14–23 s. Only MTU= changed in env: ``ip link set mtu`` on the live interface.
        from kaskad.gw.inbounds import tunnel
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        self.node.sysd.calls[:] = []
        self.card(1300)
        res = self.wg.apply(self.st, self.sec, self.runner)
        self.assertEqual((res["action"], res["changed"]), ("mtu", ["env"]))
        self.assertNotIn("restart", self.node.sysd.verbs("kaskad-gw-wg.service"))
        self.assertIn(["link", "set", "dev", "kgw-wg", "mtu", "1216"], [c[1:] for c in self.node.commands()
                                                                       if c[0].endswith("ip")])
        self.assertIn("MTU 1280 → 1216 без перезапуска", self.log_text())
        self.assertIsNone(tunnel.last_restart())
        # a device added at the same time: the live MTU and syncconf, still no restart
        self.card(1500)
        self.st["gateway"]["devices"].append(device("d-laptop", "Ноутбук", "linux", ["wg"], kind="laptop"))
        self.assertEqual(self.wg.apply(self.st, self.sec, self.runner)["action"], "synced")
        self.assertNotIn("restart", self.node.sysd.verbs("kaskad-gw-wg.service"))

    def test_mtu_that_does_not_go_live_restarts_and_says_why(self):
        from kaskad.gw.inbounds import tunnel
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        ip = self.runner.which("ip") or "/usr/sbin/ip"
        self.runner.on([ip, "link", "set", "dev", "kgw-wg", "mtu"], (2, "", "RTNETLINK answers: Invalid argument"))
        self.runner._handlers.insert(0, self.runner._handlers.pop())
        self.card(1300)
        self.assertEqual(self.wg.apply(self.st, self.sec, self.runner)["action"], "restarted")
        self.assertIn("restart", self.node.sysd.verbs("kaskad-gw-wg.service"))
        last = tunnel.last_restart()
        self.assertEqual((last["kind"], last["why"], last["mtu_was"], last["mtu_now"]), ("wg", "mtu", 1280, 1216))
        self.assertIn("wg — ", tunnel.restart_text(last))
        self.assertIn("причина: MTU 1280 → 1216", tunnel.restart_text(last))
        self.assertIn("перезапуск входа, причина: MTU 1280 → 1216", self.log_text())
        self.assertEqual(os.stat(tunnel.restarts_path()).st_mode & 0o777, 0o600)

    def test_a_unit_whose_only_change_is_execreload_is_not_restarted(self):
        from kaskad.gw.inbounds import tunnel
        old = "[Service]\nExecStart=/x\nExecReload=/a syncconf k /c\n"
        self.assertFalse(tunnel.unit_needs_restart(old, old.replace("/c", "/c.sync")))
        self.assertTrue(tunnel.unit_needs_restart(old, old.replace("ExecStart=/x", "ExecStart=/y")))
        self.assertTrue(tunnel.unit_needs_restart(None, old))

    def test_ufw_only_on_changes_and_again_once_it_is_active(self):
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)                           # UFW inactive: nothing kept
        self.assertEqual(self.node.ufw, [])
        self.node.ufw_active = True
        self.wg.apply(self.st, self.sec, self.runner)
        self.assertEqual(self.node.ufw, ["allow 51820/udp comment kaskad-gw wg"])
        self.wg.apply(self.st, self.sec, self.runner)
        self.wg.apply(self.st, self.sec, self.runner)
        self.assertEqual(self.node.ufw, ["allow 51820/udp comment kaskad-gw wg"])     # not every apply
        self.assertEqual([c for c in self.node.commands("ufw") if c[1:] != ["status"]],
                         [["ufw", "allow", "51820/udp", "comment", "kaskad-gw wg"]])

    def test_address_change_restarts(self):
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        self.st["gateway"]["inbounds"]["wg"]["subnet"] = "10.78.1.0/24"
        self.assertEqual(self.wg.apply(self.st, self.sec, self.runner)["action"], "restarted")
        self.assertIn("restart", self.node.sysd.verbs("kaskad-gw-wg.service"))

    def test_inbound_or_gateway_off_stops_the_unit(self):
        self.node.ufw_active = True
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        self.st["gateway"]["inbounds"]["wg"]["enabled"] = False
        self.assertEqual(self.wg.apply(self.st, self.sec, self.runner)["action"], "stopped")
        self.assertFalse(self.node.sysd.units["kaskad-gw-wg.service"]["active"])
        self.assertEqual(self.node.ufw[-1], "delete allow 51820/udp")
        self.assertEqual(self.wg.apply(self.st, self.sec, self.runner)["action"], "off")
        self.st["gateway"]["inbounds"]["wg"]["enabled"] = True
        self.st["gateway"]["enabled"] = False
        self.assertEqual(self.wg.apply(self.st, self.sec, self.runner)["action"], "off")
        self.st["gateway"]["enabled"] = True
        self.assertEqual(self.wg.apply(self.st, self.sec, self.runner)["action"], "started")

    def test_a_unit_from_an_older_version_is_rewritten_and_restarted(self):
        # stand, 2026-10-04: the config moved to /etc/wireguard (AppArmor on Ubuntu 26.04), the unit
        # installed by the older version still pointed to the old path and the service kept failing
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        unit = os.path.join(paths.systemd_dir(), "kaskad-gw-wg.service")
        with open(unit, encoding="utf-8") as f:
            fresh = f.read()
        with open(unit, "w", encoding="utf-8") as f:
            f.write(fresh.replace("/etc/wireguard/kgw-wg.conf", "/etc/kaskad/gw/wg/kgw-wg.conf"))
        self.node.sysd.calls[:] = []
        res = self.wg.apply(self.st, self.sec, self.runner)
        with open(unit, encoding="utf-8") as f:
            self.assertEqual(f.read(), fresh)
        self.assertEqual(res["action"], "restarted")
        self.assertIn(["daemon-reload"], self.node.sysd.calls)
        res = self.wg.apply(self.st, self.sec, self.runner)                 # nothing changed any more
        self.assertEqual(res["action"], "unchanged")

    def test_apply_before_install_explains(self):
        self.seed()
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.wg.apply(self.st, self.sec, self.runner)
        self.assertIn("не установлен", str(cm.exception))
        with self.assertRaises(self.gw.base.InboundError):
            self.wg.apply(gw_state({}, devices()), self.sec, self.runner)

    def test_log_has_no_secrets(self):
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        self.st["gateway"]["devices"].append(device("d-laptop", "Ноутбук", "linux", ["wg"], kind="laptop"))
        self.wg.apply(self.st, self.sec, self.runner)
        log = self.log_text()
        self.assertIn("gw wg: применено: synced", log)
        self.assert_no_secrets(log)


class StatusTest(WgCase):
    def test_sessions_from_handshakes(self):
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        now = 1759579200
        self.gw.tunnel.now = lambda: now
        phone, dacha = self.gw.wg.pubkey(key("dev1-wg")), self.gw.wg.pubkey(key("dev2-wg"))
        self.node.handshakes["kgw-wg"] = "%s\t%d\n%s\t%d\n" % (phone, now - 30, dacha, now - 600)
        self.node.transfer["kgw-wg"] = "%s\t1000\t2000\n%s\t5\t6\n" % (phone, dacha)
        st = self.wg.status(self.runner)
        self.assertEqual((st["installed"], st["running"], st["port"]), (True, True, 51820))
        self.assertEqual(st["sessions"], [{"device": "d-phone", "ip": "10.77.1.10", "since": now - 30,
                                           "rx": 1000, "tx": 2000}])
        self.assert_no_secrets(json.dumps(st))

    def test_not_running(self):
        st = self.wg.status(self.runner)
        self.assertEqual((st["installed"], st["running"], st["sessions"]), (False, False, []))


class IssueTest(WgCase):
    def setUp(self):
        super().setUp()
        self.seed()
        self.wg.prepare(self.st, self.sec)

    def test_ios_gets_qr_first(self):
        res = self.wg.issue(self.st, self.sec, self.dev("d-phone"), "ios")
        self.assertEqual(res["inbound"], "wg")
        self.assertEqual([i["format"] for i in res["items"]], ["qr", "conf"])
        self.assertEqual(res["format"], "qr")
        conf = res["items"][1]["body"]
        self.assertEqual(res["body"], [conf])
        self.assertEqual(res["items"][1]["filename"], "goKaskadPro-telefon-mamy-wg.conf")       # wg + awg3: the suffix
        self.golden("wg-client-phone.conf", conf)

    def test_mikrotik_gets_a_routeros_script(self):
        res = self.wg.issue(self.st, self.sec, "d-dacha", None)            # platform from the device
        self.assertEqual([i["filename"] for i in res["items"]], ["kaskad-wg.rsc", "goKaskadPro-router-na-dache.conf"])
        self.golden("wg-client-dacha.rsc", res["body"])

    def test_keenetic_and_linux_get_the_file_first(self):
        for os_name, hint in (("keenetic", "импорт из файла"), ("linux", "wg-quick up")):
            res = self.wg.issue(self.st, self.sec, self.dev("d-phone"), os_name)
            self.assertEqual(res["format"], "conf")
            self.assertIn(hint, res["notes"][0])

    def test_a_stale_copy_of_the_device_is_resolved_in_the_state(self):
        stale = dict(self.dev("d-phone"), ips={})
        res = self.wg.issue(self.st, self.sec, stale, "linux")
        self.assertIn("Address = 10.77.1.10/32", res["body"])
        res = self.wg.issue(self.st, self.sec, "dev-phone", "linux")                 # by login
        self.assertIn("Address = 10.77.1.10/32", res["body"])

    def test_refusals(self):
        err = self.gw.base.InboundError
        with self.assertRaises(err):
            self.wg.issue(self.st, self.sec, self.dev("d-tv"), "androidtv")       # no wg access
        with self.assertRaises(err):
            self.wg.issue(self.st, self.sec, self.dev("d-off"), "windows")        # device disabled
        self.st["gateway"]["devices"].append(device("d-late", "Поздний", "android", ["wg"]))
        with self.assertRaises(err) as cm:
            self.wg.issue(self.st, self.sec, "d-late", "android")                 # keys come with apply
        self.assertIn("kaskadctl gw apply", str(cm.exception))
        del self.st["gateway"]["host"]
        with self.assertRaises(err) as cm:
            self.wg.issue(self.st, self.sec, self.dev("d-phone"), "ios")
        self.assertIn("gateway.host", str(cm.exception))
