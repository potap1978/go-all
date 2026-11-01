"""kaskad.gw.model (users, devices, routes) and kaskad.gw.addr (base, subnets, addresses, overlaps);
the registry and the adapter base class."""

import os
import random
from unittest import mock

from kaskad import state
from kaskad.errors import KaskadError, StateError
from kaskad.gw import addr, model, registry
from kaskad.gw.inbounds.base import DummyInbound, Inbound, InboundError
from tests.fakes_gw import FakeIp
from tests.helpers import RootCase
from kaskad.sysrun import FakeRunner


class ModelTest(RootCase):
    def setUp(self):
        super().setUp()
        self._p = mock.patch.object(model, "_rng", random.Random(7))
        self._p.start()
        self.st = state.empty()

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    def test_structure_and_peek_does_not_change_state(self):
        st = state.empty()
        g = model.peek(st)
        self.assertEqual(g["routes"], {"default": {"via": "pool", "fail": "block"}})
        self.assertEqual(st["gateway"], {})                                  # untouched
        self.assertFalse(model.enabled(st))
        model.gw(st)
        self.assertEqual(sorted(st["gateway"]), ["devices", "enabled", "exits", "inbounds", "net", "routes", "users"])

    def test_users(self):
        u = model.add_user(self.st, "Мама")
        self.assertRegex(u["id"], r"^u-[0-9a-f]{8}$")
        with self.assertRaises(KaskadError):
            model.add_user(self.st, "Мама")
        with self.assertRaises(KaskadError):
            model.add_user(self.st, "  \x00 ")
        self.assertIs(model.find_user(self.st, u["id"]), u)
        self.assertIs(model.find_user(self.st, "мама"), u)                    # case-insensitive
        self.assertIsNone(model.find_user(self.st, "Папа"))
        model.add_user(self.st, "Дача", route="direct")
        self.assertEqual(model.find_user(self.st, "Дача")["route"], {"via": "direct", "fail": "block"})
        model.add_device(self.st, "Мама", "Телефон")
        with self.assertRaises(KaskadError):
            model.del_user(self.st, "Мама")
        u2, devs = model.del_user(self.st, "Мама", cascade=True)
        self.assertEqual((u2["name"], len(devs)), ("Мама", 1))
        self.assertEqual(model.devices(self.st), [])

    def test_devices_and_addresses(self):
        model.add_user(self.st, "Мама")
        d = model.add_device(self.st, "Мама", "Роутер на даче", kind="router", platform="keenetic",
                             access=["awg2", "l2tp", "openvpn"])
        self.assertRegex(d["login"], r"^router-na-da-[0-9a-f]{4}$")
        self.assertEqual(d["ips"], {"awg2": "10.77.3.10", "ppp": "10.77.5.10", "ovpn_u": "10.77.9.10",
                                    "ovpn_t": "10.77.10.10"})
        d2 = model.add_device(self.st, "Мама", "Ноутбук", access=["l2tp", "sstp", "ikev2", "socks5"])
        self.assertEqual(d2["ips"], {"ppp": "10.77.5.11"})                   # one PPP address, IKEv2 dynamic
        self.assertEqual(model.revoke(self.st, d2, "l2tp"), [])               # sstp still uses "ppp"
        self.assertEqual(model.revoke(self.st, d2, "sstp"), ["10.77.5.11"])
        d3 = model.add_device(self.st, "Мама", "Планшет", access=["l2tp"])
        self.assertEqual(d3["ips"], {"ppp": "10.77.5.11"})                    # the freed address is reused
        self.assertEqual(model.grant(self.st, d, "awg2"), {"awg2": "10.77.3.10"})   # stable
        self.assertEqual([x["id"] for x in model.devices_for(self.st, "l2tp")], [d["id"], d3["id"]])
        d3["enabled"] = False
        self.assertEqual([x["id"] for x in model.devices_for(self.st, "l2tp")], [d["id"]])
        self.assertIs(model.find_device(self.st, d["login"]), d)
        self.assertIs(model.find_device_by_ip(self.st, "10.77.9.10"), d)
        for bad in (dict(kind="car"), dict(platform="amiga"), dict(access=["ipsec"])):
            with self.assertRaises(KaskadError):
                model.add_device(self.st, "Мама", "X", **bad)
        self.assertEqual(model.validate(self.st), [])
        d3["ips"]["ppp"] = d["ips"]["ppp"]
        self.assertTrue(any("у двух устройств" in p for p in model.validate(self.st)))

    def test_route_resolution(self):
        g = model.gw(self.st)
        u = model.add_user(self.st, "Мама")
        d = model.add_device(self.st, "Мама", "Телефон")
        self.assertEqual(model.route_of(self.st, d), {"via": "pool", "fail": "block", "from": "default"})
        model.set_default_route(self.st, "direct")
        self.assertEqual(model.route_of(self.st, d)["via"], "direct")
        model.set_route(u, "exit:e-3fa9c1d2b4", "direct")
        self.assertEqual(model.route_of(self.st, d), {"via": "exit:e-3fa9c1d2b4", "fail": "direct", "from": "user"})
        model.set_route(d, "block")
        self.assertEqual(model.route_of(self.st, d)["from"], "device")
        model.set_route(d, "inherit")
        self.assertEqual(model.route_of(self.st, d)["from"], "user")
        for bad in ("exit:3fa9", "exit:e-3FA9C1D2B4", "proxy", "pool; drop"):
            with self.assertRaises(KaskadError):
                model.parse_route(bad)
        with self.assertRaises(KaskadError):
            model.parse_route("pool", "allow")
        u["route"] = {"via": "garbage"}                                       # broken state: next level
        self.assertEqual(model.route_of(self.st, d)["from"], "default")
        self.assertEqual(g["routes"]["default"]["via"], "direct")

    def test_exit_preferences(self):
        self.assertEqual(model.exit_pref(self.st, "e-3fa9c1d2b4"), dict(model.EXIT_PREF, enabled=True, name=""))
        model.set_exit(self.st, "e-3fa9c1d2b4", enabled=False, name="Вена\n|x")
        self.assertEqual(model.exit_pref(self.st, "e-3fa9c1d2b4"), dict(model.EXIT_PREF, enabled=False, name="Вена  x"))
        with self.assertRaises(KaskadError):
            model.set_exit(self.st, "vless://x", enabled=True)


class AddrTest(RootCase):
    def test_subnets_of_the_spec_table(self):
        want = {"wg": "10.77.1.0/24", "awg1": "10.77.2.0/24", "awg2": "10.77.3.0/24", "awg3": "10.77.4.0/24",
                "l2tp": "10.77.5.0/24", "pptp": "10.77.5.0/24", "sstp": "10.77.5.0/24", "ikev2": "10.77.8.0/24",
                "openvpn": "10.77.9.0/24", "ovpn_t": "10.77.10.0/24", "ocserv": "10.77.11.0/24"}
        for kind, net in want.items():
            self.assertEqual(addr.subnet(kind), net, kind)
        self.assertEqual(addr.server_ip("wg"), "10.77.1.1")
        self.assertEqual(addr.dns_ip(), "10.77.0.1")
        with self.assertRaises(KaskadError):
            addr.subnet("socks5")
        st = {"gateway": {"net": {"base": "172.27.0.0/16"}}}
        self.assertEqual(addr.subnet("ocserv", st), "172.27.11.0/24")
        self.assertEqual(addr.dns_ip(st), "172.27.0.1")
        for bad in ("10.77.0.0/24", "8.8.0.0/16", "10.77.1.0/16", "x"):
            with self.assertRaises(StateError):
                addr.parse_base(bad)

    def test_what_adapters_call(self):
        st = state.empty()
        st["gateway"] = {"net": {"base": "172.27.0.0/16"}, "devices": []}
        dev = {"id": "d-1", "ips": {}, "access": ["openvpn", "l2tp", "sstp"]}
        st["gateway"]["devices"].append(dev)
        self.assertEqual(addr.allocate(st, dev, "openvpn"), "172.27.9.10")          # a string; both pools
        self.assertEqual(dev["ips"], {"ovpn_u": "172.27.9.10", "ovpn_t": "172.27.10.10"})
        self.assertEqual(addr.allocate(st, dev, "ovpn_t"), "172.27.10.10")          # a pool key; never changes
        self.assertEqual(addr.allocate(st, dev, "l2tp"), "172.27.5.10")
        self.assertIsNone(addr.allocate(st, dev, "ikev2"))                          # dynamic: strongSwan's pool
        self.assertIsNone(addr.allocate(st, dev, "socks5"))
        other = {"id": "d-2", "ips": {}, "access": ["openvpn"]}
        st["gateway"]["devices"].append(other)
        self.assertEqual(addr.allocate(st, other, "ovpn_u"), "172.27.9.11")         # the lowest free one
        self.assertEqual(addr.release(st, dev, "l2tp"), [])                         # sstp still holds "ppp"
        dev["access"].remove("sstp")
        self.assertEqual(addr.release(st, dev, "l2tp"), ["172.27.5.10"])
        # subnet(kind) without st: the state being applied (addr.using), else the saved state.json
        self.assertEqual(addr.subnet("wg"), "10.77.1.0/24")
        with addr.using(st):
            self.assertEqual(addr.subnet("ocserv"), "172.27.11.0/24")
            self.assertEqual(addr.server_ip("wg"), "172.27.1.1")
        saved = state.empty()
        saved["gateway"] = {"net": {"base": "10.79.0.0/16"}}
        state.save(saved)
        self.assertEqual(addr.subnet("wg"), "10.79.1.0/24")
        with addr.using(st):
            self.assertEqual(addr.subnet("wg"), "172.27.1.0/24")                    # the applied one wins

    def test_pool_full(self):
        st = state.empty()
        model.add_user(st, "U")
        for i in range(245):
            model.add_device(st, "U", "d%d" % i, access=["wg"])
        with self.assertRaises(KaskadError) as cm:
            model.add_device(st, "U", "one too many", access=["wg"])
        self.assertIn("закончились адреса", str(cm.exception))

    def test_rebase(self):
        st = state.empty()
        model.add_user(st, "U")
        d = model.add_device(st, "U", "d", access=["wg", "l2tp"])
        addr.rebase(st, "10.77.0.0/16", "172.27.0.0/16")
        self.assertEqual(d["ips"], {"wg": "172.27.1.10", "ppp": "172.27.5.10"})

    def test_overlaps_and_choice(self):
        ip = FakeIp()
        r = FakeRunner(binaries=["ip"]).on(["ip"], ip)
        self.assertEqual(addr.choose_base(r), ("10.77.0.0/16", []))
        ip.routes["main"].append("10.77.5.0/24 dev eth1 proto kernel scope link src 10.77.5.2")
        ip.routes["300"] = ["default dev kawg44 scope link"]                       # an egress leg: ours
        ip.add_link("kgw-wg", "none", ["10.78.1.1/24"])                           # our own inbound: ignored
        base, skipped = addr.choose_base(r)
        self.assertEqual(base, "10.78.0.0/16")
        self.assertIn("10.77.5.0/24", skipped[0])
        ip.add_link("eth2", "ether", ["10.0.0.5/8"])
        self.assertEqual(addr.choose_base(r)[0], "172.27.0.0/16")
        self.assertEqual(addr.choose_base(r, current="172.27.0.0/16")[0], "172.27.0.0/16")
        ip.add_link("eth3", "ether", ["172.16.0.5/12"])
        with self.assertRaises(KaskadError):
            addr.choose_base(r)
        self.assertEqual(r.unmatched, [])


class RegistryTest(RootCase):
    def tearDown(self):
        registry.reset()
        super().tearDown()

    def test_register_get_kinds_reset(self):
        registry.reset()
        builtin = registry.kinds()              # what the adapter modules registered (WP5, WP6)
        self.assertEqual(builtin, [k for k in model.INBOUND_KINDS if k in builtin])  # order of the spec
        real_wg = registry.get("wg")
        registry.register(DummyInbound("ikev2"))
        registry.register(DummyInbound("wg"))
        self.assertIn("ikev2", registry.kinds())
        self.assertIsInstance(registry.get("wg"), DummyInbound)
        self.assertIsNone(registry.get("nosuch"))

        @registry.register
        class Odd(Inbound):
            kind = "zzz-test"
            title = "Тест"
        self.assertIsInstance(registry.get("zzz-test"), Odd)
        self.assertEqual(registry.kinds()[-1], "zzz-test")          # unknown kinds after the spec's
        registry.reset()                                            # test registrations dropped
        self.assertEqual(registry.kinds(), builtin)
        self.assertIs(registry.get("wg"), real_wg)
        self.assertIsNone(registry.get("zzz-test"))
        with self.assertRaises(ValueError):
            registry.register(Inbound())

    def test_base_class_defaults(self):
        ad = Inbound()
        ad.kind = "wg"
        st = {"gateway": {"enabled": True, "inbounds": {"wg": {"port": 51820}}}}
        self.assertEqual(ad.ports(st), [("udp", 51820)])
        self.assertEqual(ad.default_port(), 0)
        self.assertEqual(ad.addr_kinds(st), ["wg"])
        self.assertEqual(ad.units(), ["kaskad-gw-wg.service"])
        self.assertEqual(ad.render(st, {}), {})
        self.assertEqual(ad.unit_texts(None), {})
        self.assertEqual(ad.xray_inbounds(st, {}), [])
        self.assertEqual(ad.xray_routing(st, {}, target=lambda r: {"outboundTag": "direct"}), [])
        self.assertTrue(ad.wanted(st))
        st["gateway"]["inbounds"]["wg"]["enabled"] = False
        self.assertFalse(ad.wanted(st))
        with self.assertRaises(InboundError):
            ad.issue(st, {}, {"id": "d-1"}, "ios")
        self.assertTrue(issubclass(InboundError, KaskadError))
        files = {ad.conf_path("a.conf"): ("x\n", 0o600)}
        self.assertEqual(ad.write_files(files), [ad.conf_path("a.conf")])
        self.assertEqual(ad.write_files(files), [])
        self.assertEqual(oct(os.stat(ad.conf_path("a.conf")).st_mode & 0o777), "0o600")
        self.assertTrue(ad.conf_path("a.conf").endswith("/etc/kaskad/gw/wg/a.conf"))
        # ports of other kinds (the core's conflict check): two fields, two protocols, fixed ports
        ov = Inbound()
        ov.kind = "openvpn"
        self.assertEqual(ov.ports({"gateway": {"inbounds": {"openvpn": {"udp_port": 1194, "tcp_port": 443}}}}),
                         [("udp", 1194), ("tcp", 443)])
        oc = Inbound()
        oc.kind = "ocserv"
        self.assertEqual(oc.ports({"gateway": {"inbounds": {"ocserv": {"port": 4443}}}}),
                         [("tcp", 4443), ("udp", 4443)])
        l2 = Inbound()
        l2.kind = "l2tp"
        self.assertEqual(l2.ports({"gateway": {"inbounds": {"l2tp": {}}}}),
                         [("udp", 1701), ("udp", 500), ("udp", 4500)])
        self.assertEqual(l2.addr_kinds(), ["ppp"])
        bad = Inbound()
        bad.kind = "sstp"
        self.assertEqual(bad.ports({"gateway": {"inbounds": {"sstp": {"port": True}}}}), [])
