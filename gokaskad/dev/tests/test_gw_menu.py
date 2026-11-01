"""What the menu of install.sh (work package 7) needs from the core: ``gw probe``, ``gw inbound plan``,
``gw offers``, ``gw inbound option`` and ``gw purge`` — end to end through kaskad.cli.main."""

import json
import os
from unittest import mock

from kaskad import compat, errors, paths, secrets, state
from kaskad.errors import KaskadError
from kaskad.gw import collect, model, ops, xray
from tests.fakes_gw import register_dummies
from tests.fakes_nft import F443, save_state
from tests.test_gw_ops import CliGwCase

SS_UDP500 = 'udp   UNCONN 0      0            0.0.0.0:500       0.0.0.0:*    users:(("charon",pid=900,fd=12))\n'


class ProbeTest(CliGwCase):
    def test_supported_unsupported_and_the_engine(self):
        save_state()
        rc, doc, _ = self.run_json("gw", "probe")
        self.assertEqual((rc, doc["supported"], doc["engine"], doc["code"]), (0, True, "nft", ""))
        save_state(engine="legacy")
        rc, out, _ = self.run_cli("gw", "probe")
        self.assertEqual(rc, 0)
        self.assertIn("nftables", out)                                   # the menu offers the move first
        self.write("/etc/os-release", 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04.5 LTS"\n')
        rc, doc, _ = self.run_json("gw", "probe")
        self.assertEqual((rc, doc["supported"], doc["code"]), (errors.UNSUPPORTED, False, "os"))
        self.assertIn("Ubuntu 22.04", doc["reason"])
        self.assertEqual(self.w.nft_writes(), [])                        # read-only

    def test_packages_the_gateway_still_needs(self):
        save_state()
        rc, doc, _ = self.run_json("gw", "probe")
        self.assertEqual(doc["packages"], [])                            # dnsmasq and conntrack are there
        self.w = self.make_world(dnsmasq=False)
        import kaskad.sysrun as sysrun
        sysrun._default = self.w.runner
        self.w.runner.binaries.discard("conntrack")
        rc, doc, _ = self.run_json("gw", "probe")
        self.assertEqual(doc["packages"], ["dnsmasq-base", "conntrack"])  # the menu tells a busy apt in advance


class PlanTest(CliGwCase):
    def test_default_ports_conflicts_and_free_alternatives(self):
        self.ads = self.dummies()
        save_state([dict(F443, proto="udp", port=51820)])                # a forward holds the WireGuard port
        register_dummies(("awg1", {"random_port": True, "default_port": 0}),
                         ("ocserv", {"proto": "tcp", "default_port": 4443, "supported": False,
                                     "why": "в ядре нет tun"}))
        rc, doc, err = self.run_json("gw", "inbound", "plan", "wg", "awg2", "awg1", "ocserv")
        self.assertEqual(rc, 0, err)
        rows = dict((r["kind"], r) for r in doc["inbounds"])
        wg = rows["wg"]["port"]
        self.assertEqual((wg["port"], wg["default"], wg["protos"]), (51820, 51820, ["udp"]))
        self.assertEqual([c["kind"] for c in wg["conflicts"]], ["forward"])
        self.assertTrue(20000 <= wg["free"] < 60000)
        self.assertEqual((rows["awg2"]["port"]["port"], rows["awg2"]["port"]["conflicts"]), (41023, []))
        awg1 = rows["awg1"]["port"]
        self.assertEqual(awg1["default"], 0)                              # no default: a random free port
        self.assertTrue(20000 <= awg1["port"] < 60000)
        self.assertNotIn(awg1["port"], (wg["free"], 41023))               # never planned twice
        self.assertEqual((rows["ocserv"]["supported"], rows["ocserv"]["why"]), (False, "в ядре нет tun"))
        self.assertEqual(rows["wg"]["packages"], ["dummy-wg"])           # not installed: apt needed
        self.assertEqual(rows["ocserv"]["packages"], [])                  # cannot work here: nothing to install
        self.w.pkgs.installed.add("dummy-awg2")
        rc, doc, _ = self.run_json("gw", "inbound", "plan", "awg2")
        self.assertEqual(doc["inbounds"][0]["packages"], [])
        self.assertFalse(any(r["installed"] for r in doc["inbounds"]))
        self.assertEqual(state.load()["gateway"], {})                     # nothing written
        self.assertEqual(self.w.nft_writes(), [])
        rc, out, _ = self.run_cli("gw", "inbound", "plan", "wg", "ocserv")
        self.assertIn("51820/udp занят (его занимает проброс", out)
        self.assertIn("здесь не работает — в ядре нет tun", out)

    def test_installed_inbound_keeps_its_port_and_fixed_ports(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        fixed = {"proto": "", "default_port": 0, "dynamic": True, "ifaces": [],
                 "fixed_ports": (("udp", 500), ("udp", 4500))}
        register_dummies(("ikev2", fixed), ("l2tp", dict(fixed, fixed_ports=(("udp", 1701), ("udp", 500), ("udp", 4500)))))
        self.w.ss = SS_UDP500                                             # a foreign IKE daemon on UDP 500
        rc, doc, _ = self.run_json("gw", "inbound", "plan", "wg", "ikev2")
        rows = dict((r["kind"], r) for r in doc["inbounds"])
        self.assertEqual((rows["wg"]["installed"], rows["wg"]["port"]["port"], rows["wg"]["port"]["conflicts"]),
                         (True, 51820, []))
        ike = dict(((f["proto"], f["port"]), f["conflicts"]) for f in rows["ikev2"]["fixed"])
        self.assertIsNone(rows["ikev2"]["port"])
        self.assertEqual([c["kind"] for c in ike[("udp", 500)]], ["listener"])
        self.assertIn("charon", ike[("udp", 500)][0]["detail"])
        self.assertEqual(ike[("udp", 4500)], [])
        self.w.ss = ""
        self.ok("gw", "inbound", "install", "ikev2")
        self.w.ss = SS_UDP500                                             # now it is our own strongSwan
        rc, doc, _ = self.run_json("gw", "inbound", "plan", "l2tp")
        l2tp = dict(((f["proto"], f["port"]), f["conflicts"]) for f in doc["inbounds"][0]["fixed"])
        self.assertEqual(l2tp, {("udp", 1701): [], ("udp", 500): [], ("udp", 4500): []})

    def test_unknown_kind(self):
        save_state()
        rc, doc, _ = self.run_json("gw", "inbound", "plan", "wg", "nosuch")
        self.assertEqual(rc, errors.USAGE)

    def test_a_kind_that_cannot_work_here_takes_no_port(self):
        save_state()
        register_dummies(("wg", {"supported": False, "why": "в ядре нет модуля wireguard"}),
                         ("awg1", {"default_port": 51820}))
        rc, doc, _ = self.run_json("gw", "inbound", "plan", "wg", "awg1")
        rows = dict((r["kind"], r) for r in doc["inbounds"])
        self.assertFalse(rows["wg"]["supported"])
        self.assertEqual((rows["awg1"]["port"]["port"], rows["awg1"]["port"]["conflicts"]), (51820, []))


class EnableBaseTest(CliGwCase):
    def test_devices_added_before_the_first_enable_move_with_the_base(self):
        from tests.fakes_gw import FakeIp
        self.ads = self.dummies()
        save_state()
        self.ok("gw", "inbound", "install", "wg")                         # the gateway is off: default base
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "Телефон", "--access", "wg")
        self.assertEqual(doc["device"]["ips"], {"wg": "10.77.1.10"})
        self.w.ip = FakeIp(extra_routes=["10.0.0.0/8 dev eth1 proto kernel scope link src 10.1.2.3"])
        self.w.runner._handlers = [h for h in self.w.runner._handlers if h[0] != ["ip"]]
        self.w.runner.on(["ip"], self.w.ip)
        rc, doc, err = self.run_json("gw", "enable")
        self.assertEqual((rc, doc["base"]), (0, "172.27.0.0/16"), err)
        self.assertIn("адреса устройств сменились — выдайте им настройки заново", doc["warnings"])
        d = state.load()["gateway"]["devices"][0]
        self.assertEqual(d["ips"], {"wg": "172.27.1.10"})
        self.assertEqual(list(self.w.nft.elements("gw_route")), ["172.27.1.10"])

    def test_disable_reports_what_did_not_stop(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")

        def boom(st, sec, runner):
            from kaskad.gw.inbounds.base import InboundError
            raise InboundError("служба не остановилась")
        self.ads["wg"].apply = boom
        rc, out, err = self.run_cli("gw", "disable")
        self.assertEqual(rc, 0)
        self.assertIn("Внимание: Тестовый вход wg: служба не остановилась", err)


class OffersTest(CliGwCase):
    def test_matrix_order_with_the_state_of_the_inbounds(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "inbound", "install", "awg2")
        self.ok("gw", "inbound", "disable", "awg2")
        rc, doc, _ = self.run_json("gw", "offers", "--platform", "keenetic")
        self.assertEqual(rc, 0)
        self.assertEqual([o["inbound"] for o in doc["offers"]], ["awg2", "awg1", "wg", "l2tp"])
        rows = dict((o["inbound"], o) for o in doc["offers"])
        self.assertEqual((rows["awg2"]["installed"], rows["awg2"]["enabled"]), (True, False))
        self.assertEqual((rows["wg"]["installed"], rows["wg"]["enabled"]), (True, True))
        self.assertEqual((rows["l2tp"]["installed"], rows["l2tp"]["title"]), (False, "L2TP/IPsec"))
        self.assertIn("3.1", doc["notes"][0])
        rc, out, _ = self.run_cli("gw", "offers", "--platform", "ios")
        self.assertEqual(out.splitlines()[0].split()[0], "awg3")
        self.assertNotEqual(self.run_cli("gw", "offers", "--platform", "amiga")[0], 0)


class OptionTest(CliGwCase):
    def test_plain_l2tp_switch(self):
        self.enabled_world(pool=False)
        register_dummies(("l2tp", {"proto": "", "default_port": 0, "ifaces": [],
                                   "fixed_ports": (("udp", 1701), ("udp", 500), ("udp", 4500))}))
        rc, doc, _ = self.run_json("gw", "inbound", "option", "l2tp", "plain", "on")
        self.assertEqual(rc, errors.ERROR)                                # not installed yet
        self.assertIn("не установлен", doc["error"])
        self.ok("gw", "inbound", "install", "l2tp")
        rc, doc, err = self.run_json("gw", "inbound", "option", "l2tp", "plain", "on")
        self.assertEqual((rc, doc["value"], doc["old"]), (0, True, None), err)
        self.assertTrue(model.inbound(state.load(), "l2tp")["plain"])
        self.assertEqual(self.ads_calls("l2tp")[-1], "apply")            # applied like any change
        out, _ = self.ok("gw", "inbound", "option", "l2tp", "plain", "off")
        self.assertIn("L2TP без шифрования", out)
        self.assertIs(model.inbound(state.load(), "l2tp")["plain"], False)
        self.assertIn("GW inbound option: l2tp plain True -> off", self.log())
        self.assertEqual(self.run_cli("gw", "inbound", "option", "l2tp", "psk", "on")[0], errors.USAGE)
        self.assertEqual(self.run_cli("gw", "inbound", "option", "l2tp", "plain", "maybe")[0], errors.USAGE)
        self.assertEqual(self.run_cli("gw", "inbound", "option", "wg", "plain", "on")[0], errors.USAGE)

    def ads_calls(self, kind):
        from kaskad.gw import registry
        return registry.get(kind).calls


class PurgeTest(CliGwCase):
    def world(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "inbound", "install", "awg2")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "Телефон", "--platform", "android",
                                   "--access", "wg,awg2")
        self.ok("gw", "issue", doc["device"]["id"])                       # keys of the device made
        return doc["device"]["id"]

    def units(self):
        return sorted(n for n in os.listdir(paths.systemd_dir()) if n.startswith("kaskad-")) \
            if os.path.isdir(paths.systemd_dir()) else []

    def test_needs_yes(self):
        did = self.world()
        before = self.read("/etc/kaskad/state.json")
        rc, out, err = self.run_cli("gw", "purge")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("--yes", err)
        self.assertEqual(self.read("/etc/kaskad/state.json"), before)
        self.assertTrue(model.find_device(state.load(), did))

    def test_purge_keeps_people(self):
        did = self.world()
        self.assertIn("kaskad-dns.service", self.units())
        self.assertTrue([k for k in secrets.load() if k.startswith("dev.%s.wg" % did)])
        rc, doc, err = self.run_json("gw", "purge", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["uninstalled"], doc["users"]), (["wg", "awg2"], False))
        for kind in ("wg", "awg2"):
            self.assertIn("uninstall", self.ads[kind].calls)
            self.assertFalse(self.ads[kind].running)
        st = state.load()
        g = st["gateway"]
        self.assertEqual((g["enabled"], g["inbounds"]), (False, {}))
        self.assertEqual(g["net"]["base"], "10.77.0.0/16")                 # the kept addresses are in it
        self.assertNotIn("host", g)
        self.assertEqual([u["name"] for u in g["users"]], ["Мама"])
        self.assertEqual(g["devices"][0]["access"], ["wg", "awg2"])           # back with the inbounds
        self.assertEqual(sorted(secrets.load()), ["dev.%s.password" % did])     # keys, PSK, CA: gone
        self.assertEqual(self.units(), [])                                  # dns, collector, xray: gone
        self.assertFalse(os.path.exists(paths.gw_dir()))
        self.assertFalse(os.path.exists(paths.gw_run_dir()))
        self.assertFalse(os.path.exists(os.path.join(paths.opt(), "gw")))          # the xray copy and its folder
        t = self.w.nft.table()
        self.assertEqual(list(t["chains"]), ["fwd_pre", "fwd_dnat", "fwd_count", "fwd_post"])
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443)})    # forwards untouched
        self.assertIsNone(self.w.nft.table("kaskad6", "ip6"))
        self.assertNotIn("from all fwmark 0x200 lookup 200", self.w.ip.rule_texts())
        self.assertNotIn("kgw0", self.w.ip.links)
        self.assertFalse(compat.present_in(self.w.runner))
        self.assertIn("GW purge: шлюз удалён, входов 2", self.log())
        self.assertIn("пользователи и устройства сохранены", self.log())
        # the wizard can start again: the people come back with the inbounds
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "enable")
        self.assertEqual(model.find_device(state.load(), did)["ips"]["wg"], "10.77.1.10")
        self.assertEqual(list(self.w.nft.elements("gw_route")), ["10.77.1.10"])

    def test_purge_with_users_and_leftovers(self):
        self.world()
        register_dummies(("ocserv", {"proto": "tcp", "default_port": 4443}))
        os.makedirs(os.path.join(paths.gw_dir(), "ocserv"))                # an install cut short
        self.write("/etc/systemd/system/kaskad-gw-ovpn-udp.service", "[Service]\n", 0o644)
        self.write("/etc/wireguard/kgw-wg.conf", "[Interface]\nPrivateKey = SERVERKEY\n")   # left by an old wg.py
        self.write("/etc/wireguard/wg0.conf", "[Interface]\n")             # the owner's own: stays
        self.write("/etc/kaskad/gw/packages.json", json.dumps(
            {"openvpn": {"by_kaskad": True, "masked": ["openvpn.service"]}, "wireguard-tools": {"by_kaskad": True}}))
        self.w.users.uids["kaskad-xray"] = 990                              # made by xray.ensure_user
        self.write("/opt/kaskad/gw/notes.txt", "the owner’s own\n")         # not ours: the folder stays
        out, err = self.ok("gw", "purge", "--users", "--yes")
        self.assertFalse(os.path.exists(self.path("/opt/kaskad/gw/xray")))
        self.assertTrue(os.path.exists(self.path("/opt/kaskad/gw/notes.txt")))
        self.assertIn("пользователи и устройства", out)
        self.assertNotIn("kaskad-xray", self.w.users.uids)                 # the system user of xray is gone
        from kaskad.gw import registry
        self.assertIn("uninstall", registry.get("ocserv").calls)
        self.assertIn(["systemctl", "unmask", "openvpn.service"], self.w.sysd.calls)   # before packages.json goes
        self.assertFalse(os.path.exists(self.path("/etc/wireguard/kgw-wg.conf")))
        self.assertTrue(os.path.exists(self.path("/etc/wireguard/wg0.conf")))
        self.assertEqual(json.loads(self.read("/etc/kaskad/state.json"))["gateway"], {})
        self.assertEqual(dict(secrets.load()), {})
        self.assertEqual(self.units(), [])
        self.assertIn("пользователи и устройства удалены", self.log())

    def test_purge_of_a_gateway_that_is_off_or_was_never_on(self):
        save_state([F443])
        self.ok("gw", "purge", "--yes")                                   # nothing there: fine
        self.assertEqual(state.load()["gateway"]["inbounds"], {})
        self.world()
        self.ok("gw", "disable")
        rc, doc, _ = self.run_json("gw", "purge", "--yes")
        self.assertEqual((rc, doc["uninstalled"], doc["warnings"]), (0, ["wg", "awg2"], []))
        self.assertFalse(self.w.sysd.units[collect.UNIT]["active"])

    def test_broken_adapter_kind_without_adapter_and_broken_secrets_do_not_stop_it(self):
        did = self.world()
        st = state.load()
        st["gateway"]["inbounds"]["ocserv"] = {"enabled": True, "port": 4443}     # adapter gone
        state.save(st)
        from kaskad.gw import registry
        registry.unregister("ocserv")
        self.ads["awg2"].uninstall = lambda runner: (_ for _ in ()).throw(RuntimeError("сбой адаптера"))
        self.write("/etc/kaskad/secrets.json", "{broken")
        rc, doc, err = self.run_json("gw", "purge", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["uninstalled"], ["wg"])
        text = " | ".join(doc["warnings"])
        self.assertIn("протокол OpenConnect записан в настройках, но адаптера нет", text)
        self.assertIn("Тестовый вход awg2: сбой адаптера", text)            # not KaskadError: still a warning
        self.assertIn("шлюз не выключился обычным путём", text)             # disable reads the broken secrets
        self.assertIn("отложен в", text)
        self.assertEqual(state.load()["gateway"]["inbounds"], {})              # state saved all the same
        self.assertTrue(model.find_device(state.load(), did))
        self.assertFalse(os.path.exists(paths.gw_dir()))
        self.assertTrue([n for n in os.listdir(os.path.dirname(paths.secrets_file())) if ".bad-" in n])

    def test_disable_failing_is_a_warning_and_the_rest_goes(self):
        self.world()
        with mock.patch.object(ops, "disable", side_effect=KaskadError("nft занят")):
            rc, doc, err = self.run_json("gw", "purge", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertIn("не выключился обычным путём (nft занят)", doc["warnings"][0])
        self.assertFalse(state.load()["gateway"]["enabled"])
        self.assertEqual(list(self.w.nft.table()["chains"]), ["fwd_pre", "fwd_dnat", "fwd_count", "fwd_post"])
        self.assertNotIn("gw_", self.read("/etc/kaskad/net/kaskad.nft"))
        self.assertNotIn("kgw0", self.w.ip.links)
        for u in ("kaskad-dns.service", xray.UNIT):
            self.assertFalse(self.w.sysd.units.get(u, {}).get("active"), u)
        self.assertEqual(doc["uninstalled"], ["wg", "awg2"])
