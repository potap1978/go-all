"""The adapters as the core sees them (SPEC §3, §7): registration, the interface, names of units and
interfaces; the stand-ins leave no trace in sys.modules; no secret in logs, status or requirements."""

import json
import sys
import unittest

import kaskad
from tests.gw.tunnel_case import PARAMS, GwCase, device, gw_state
from tests.gwstub import Stubbed

KINDS = ["awg1", "awg2", "awg3", "ocserv", "openvpn", "socks5", "vless", "wg"]
METHODS = ("requirements", "install", "uninstall", "render", "apply", "status", "issue", "ifaces", "default_port",
           "prepare", "addr_kinds")


class StubHygieneTest(unittest.TestCase):
    def test_sys_modules_restored(self):
        before = {k: v for k, v in sys.modules.items() if k == "kaskad.secrets" or k.startswith("kaskad.gw")}
        attrs = {a: getattr(kaskad, a, None) for a in ("gw", "secrets")}
        with Stubbed() as gw:
            self.assertIs(sys.modules["kaskad.secrets"], gw.secrets)
            self.assertIs(kaskad.secrets, gw.secrets)
            self.assertEqual(gw.registry.kinds(), KINDS)
        after = {k: v for k, v in sys.modules.items() if k == "kaskad.secrets" or k.startswith("kaskad.gw")}
        self.assertEqual(after, before)
        self.assertEqual({a: getattr(kaskad, a, None) for a in ("gw", "secrets")}, attrs)


class InterfaceTest(unittest.TestCase):
    def setUp(self):
        self.gw = Stubbed().__enter__()
        self.addCleanup(self.gw.__exit__, None, None, None)

    def test_every_adapter_implements_the_interface(self):
        for kind in KINDS:
            cls = self.gw.registry.get(kind)
            self.assertTrue(issubclass(cls, self.gw.base.Inbound), kind)
            self.assertEqual(cls.kind, kind)
            self.assertTrue(cls.engine and cls.title, kind)
            for m in METHODS:
                self.assertIn(m, dir(cls), "%s.%s" % (kind, m))
                self.assertIsNot(getattr(cls, m), getattr(self.gw.base.Inbound, m, None), "%s.%s" % (kind, m))

    def test_interfaces_and_units(self):
        a = self.gw.adapter
        self.assertEqual({k: a(k).ifaces() for k in KINDS}, {
            "wg": ["kgw-wg"], "awg1": ["kgw-awg1"], "awg2": ["kgw-awg2"], "awg3": ["kgw-awg3"],
            "openvpn": ["kgw-ovpn-u", "kgw-ovpn-t"], "ocserv": ["kgw-oc*"], "socks5": [], "vless": []})
        self.assertEqual(a("wg").unit, "kaskad-gw-wg.service")
        self.assertEqual(a("awg2").unit, "kaskad-gw-awg2.service")
        self.assertEqual(a("openvpn").units(), ["kaskad-gw-ovpn-udp.service", "kaskad-gw-ovpn-tcp.service"])
        self.assertEqual(self.gw.ocserv.UNIT, "kaskad-gw-ocserv.service")
        self.assertEqual({k: a(k).default_port() for k in ("wg", "openvpn", "ocserv", "socks5", "vless")},
                         {"wg": 51820, "openvpn": 1194, "ocserv": 4443, "socks5": 1080, "vless": 443})


class NoLeakTest(GwCase):
    def test_logs_status_requirements_have_no_secrets(self):
        inbounds = {"wg": {"enabled": True, "port": 51820, "subnet": "10.77.1.0/24"},
                    "awg3": {"enabled": True, "port": 51911, "subnet": "10.77.4.0/24", "line": "3.1",
                             "params": PARAMS["3.1"]},
                    "openvpn": {"enabled": True, "udp_port": 1194, "tcp_port": 0},
                    "ocserv": {"enabled": True, "port": 4443}, "socks5": {"enabled": True, "port": 1080, "udp": True},
                    "vless": {"enabled": True, "port": 8443, "sni": "www.microsoft.com", "dest": "www.microsoft.com:443",
                              "mask": "list"}}
        st = gw_state(inbounds, [device("d-1", "Всё сразу", "linux", sorted(inbounds), kind="pc")])
        self.write("/etc/kaskad/egress/awg/amneziawg-go", "fake disable_cookies", 0o755)
        self.write("/etc/kaskad/egress/awg/awg", "fake DisableCookies", 0o755)
        out = []
        for kind in sorted(inbounds):
            a = self.adapter(kind)
            a.install(self.runner)
            out.append(json.dumps(a.apply(st, self.sec, self.runner), ensure_ascii=False))
            out.append(json.dumps(a.status(self.runner), ensure_ascii=False))
            out.append(json.dumps(a.requirements(self.runner), ensure_ascii=False))
        self.assertIn("dev.d-1.password", self.sec)
        self.assertIn("ovpn.ca.key", self.sec)
        self.assertIn("vless.server.private", self.sec)
        self.assert_no_secrets("\n".join(out) + self.log_text())
        self.assert_no_secrets(json.dumps(st, ensure_ascii=False).replace(PARAMS["3.1"]["HeaderProtectionKey"], ""))
