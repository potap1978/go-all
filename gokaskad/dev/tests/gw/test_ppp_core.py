"""Work package 6 with the REAL gateway core (work package 4) through ``kaskadctl gw …``: install of L2TP, IKEv2 and
SSTP (accel-ppp built, strongSwan from packages), devices, issuance, hooks, status, the shared UFW ports of IPsec,
gateway off/on, uninstall one by one. Skipped in a tree without the core (tests/test_gw_ops.py, gw/ops.py)."""

import hashlib
import json
import os
import plistlib
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HAVE_CORE = all(os.path.exists(os.path.join(ROOT, p)) for p in ("lib/kaskad/gw/ops.py", "tests/test_gw_ops.py",
                                                                 "tests/fakes_gw.py"))

if not HAVE_CORE:
    class CoreIntegrationTest(unittest.TestCase):
        @unittest.skip("в этом дереве нет ядра шлюза (WP4)")
        def test_with_the_real_core(self):
            pass
else:
    from kaskad import paths, secrets, state
    from kaskad.gw import model, registry
    from kaskad.gw.inbounds import accel
    from tests.fakes_gw import FakeUfw
    from tests.fakes_nft import F443, save_state
    from tests.gw.ppp_case import TARBALL, PppNode
    from tests.gw.tunnel_fakes import FakeOpenssl
    from tests.test_gw_ops import CliGwCase

    class CoreIntegrationTest(CliGwCase):
        UFW = FakeUfw

        def setUp(self):
            super().setUp()
            for name, value in (("SHA256", hashlib.sha256(TARBALL).hexdigest()), ("SIZE", len(TARBALL))):
                p = mock.patch.object(accel, name, value)
                p.start()
                self.addCleanup(p.stop)
            r = self.w.runner
            r.binaries |= {"swanctl", "charon-systemd", "openssl"}
            self.h = h = PppNode()                       # its handlers, wired into the world's runner
            self.ssl = FakeOpenssl()
            r._handlers.insert(0, (["curl", "-fsSL", "--retry"], h._curl))     # before the world's curl
            r.on(["modinfo", "-n"], lambda a, i: (0, "/lib/modules/6.1.0/kernel/%s.ko\n" % a[-1], ""))
            r.on(["openssl"], self.ssl)
            r.on(["tar"], h._tar)
            r.on(["patch"], h._patch)
            r.on(["cmake"], h._cmake)
            r.on(["make"], h._make)
            r.on([paths.P("/opt/kaskad/accel-ppp/sbin/accel-pppd")], h._pppd)
            r.on([paths.P("/opt/kaskad/accel-ppp/bin/accel-cmd")], h._accel_cmd)
            r.on(["/usr/sbin/swanctl"], h._swanctl)
            r.on(["/bin/sh"], h._sh)
            self.w.sysd.units["strongswan.service"] = {"enabled": False, "active": False, "packaged": True}

        def secret_values(self):
            return [v for v in secrets.load().values() if isinstance(v, str) and len(v) >= 8]

        def test_l2tp_ikev2_sstp_with_the_core(self):
            self.assertIsInstance(registry.get("l2tp"), accel.PppInbound)
            save_state([F443])
            self.ok("gw", "enable")
            rc, doc, err = self.run_json("gw", "inbound", "install", "l2tp")
            self.assertEqual(rc, 0, err)
            rec = model.inbound(state.load(), "l2tp")
            self.assertEqual(rec, {"enabled": True, "subnet": "10.77.5.0/24", "ipsec": True, "plain": False})
            self.assertIn("l2tp.psk", secrets.load())
            self.assertTrue(self.w.sysd.units["kaskad-gw-accel.service"]["active"])
            self.assertTrue(self.w.sysd.units["kaskad-gw-ipsec.service"]["active"])
            self.assertTrue(self.w.sysd.units["strongswan.service"].get("masked"))
            self.assertEqual(sorted(self.w.ufw.rules), sorted([
                ("1701/udp", "kaskad-gw l2tp"), ("500/udp", "kaskad-gw ipsec"), ("4500/udp", "kaskad-gw ipsec"),
                ("Anywhere/esp", "kaskad-gw ipsec")]))
            self.assertEqual(self.w.nft.rules("gw_fwd6", "kaskad6", "ip6"), ['iifname "kgw-l2tp*" drop'])
            self.assertEqual(self.ok("gw", "inbound", "install", "ikev2")[1], "")
            rec = model.inbound(state.load(), "ikev2")
            self.assertEqual((rec["subnet"], rec["cert"], rec["cert_host"]), ("10.77.8.0/24", "self", "203.0.113.10"))
            self.assertEqual(len(self.w.ufw.rules), 4)                       # IPsec ports opened once
            rc, doc, err = self.run_json("gw", "inbound", "install", "sstp")
            self.assertEqual((rc, doc["record"]["port"]), (0, 4433), err)
            self.assertIn(("4433/tcp", "kaskad-gw sstp"), self.w.ufw.rules)

            self.ok("gw", "user", "add", "Мама")
            rc, doc, err = self.run_json("gw", "device", "add", "Мама", "Ноутбук", "--platform", "windows",
                                         "--access", "l2tp,ikev2,sstp")
            self.assertEqual(rc, 0, err)
            d = doc["device"]
            self.assertEqual(d["ips"], {"ppp": "10.77.5.10"})
            pw = secrets.load()["dev.%s.password" % d["id"]]
            chap = self.read("/etc/kaskad/gw/accel/chap-secrets")
            self.assertIn('%s\t*\t"%s"\t10.77.5.10' % (d["login"], pw), chap)
            self.assertIn("id = %s" % d["login"], self.read("/etc/swanctl/conf.d/kaskad.conf"))
            self.assertEqual(self.read("/etc/kaskad/gw/accel/allow/sstp"), d["login"] + "\n")
            self.assertIn("10.77.5.10", self.w.nft.elements("gw_route"))

            rc, doc, _ = self.run_json("gw", "issue", d["id"], "--inbound", "ikev2", "--os", "ios")
            self.assertEqual((rc, doc["items"][0]["format"]), (0, "mobileconfig"))
            prof = plistlib.loads(doc["items"][0]["body"].encode("utf-8"))
            self.assertEqual(prof["PayloadContent"][1]["IKEv2"]["AuthPassword"], pw)
            rc, doc, _ = self.run_json("gw", "issue", d["id"])                # Windows: IKEv2 first (§4)
            self.assertEqual((doc["kind"], doc["items"][0]["format"]), ("ikev2", "ps1"))
            rc, doc, _ = self.run_json("gw", "issue", d["id"], "--inbound", "l2tp")
            self.assertIn("-L2tpPsk $psk", doc["items"][0]["body"])

            rc, doc, err = self.run_json("gw", "hook", "ikev2", "up", d["login"], "10.77.8.10")
            self.assertEqual(rc, 0, err)
            self.assertIn("10.77.8.10", self.w.nft.elements("gw_route"))
            self.ok("gw", "hook", "l2tp", "up", d["login"], "10.77.5.10")
            self.h.sessions = " kgw-l2tp0 | %s | 10.77.5.10 | l2tp | active | 60 | 1 | 2\r\n" % d["login"]
            rc, doc, _ = self.run_json("gw", "inbound", "list")
            rows = dict((r["kind"], r) for r in doc["inbounds"])
            for k in ("l2tp", "ikev2", "sstp"):
                self.assertTrue(rows[k]["running"], k)
            self.assertEqual(rows["l2tp"]["sessions"], 1)

            self.ok("gw", "device", "revoke", d["id"], "ikev2")
            self.assertNotIn("id = %s" % d["login"], self.read("/etc/swanctl/conf.d/kaskad.conf"))
            self.ok("gw", "disable")
            self.assertFalse(self.w.sysd.units["kaskad-gw-accel.service"]["active"])
            self.assertFalse(self.w.sysd.units["kaskad-gw-ipsec.service"]["active"])
            self.assertEqual(self.w.ufw.rules, [])
            self.ok("gw", "enable")
            self.assertTrue(self.w.sysd.units["kaskad-gw-ipsec.service"]["active"])

            self.ok("gw", "inbound", "uninstall", "ikev2")
            self.assertTrue(os.path.exists(self.path("/etc/systemd/system/kaskad-gw-ipsec.service")))
            self.assertIn(("500/udp", "kaskad-gw ipsec"), self.w.ufw.rules)  # L2TP/IPsec still uses it
            self.ok("gw", "inbound", "uninstall", "l2tp")
            self.assertFalse(os.path.exists(self.path("/etc/systemd/system/kaskad-gw-ipsec.service")))
            self.assertEqual(self.w.ufw.rules, [("4433/tcp", "kaskad-gw sstp")])
            self.assertTrue(self.w.sysd.units["kaskad-gw-accel.service"]["active"])   # SSTP keeps accel-ppp
            self.ok("gw", "inbound", "uninstall", "sstp")
            self.assertFalse(os.path.exists(self.path("/etc/systemd/system/kaskad-gw-accel.service")))
            self.assertEqual(self.w.ufw.rules, [])

            for argv in (("gw", "status"), ("gw", "inbound", "list"), ("gw", "sessions"), ("gw", "device", "list")):
                out = self.run_cli(*argv)[1]
                for v in self.secret_values():
                    self.assertNotIn(v, out)
            log = self.log()
            for v in self.secret_values():
                self.assertNotIn(v, log)
            self.assertEqual(json.loads(self.read("/etc/kaskad/gw/accel/kinds.json"))
                             if os.path.exists(self.path("/etc/kaskad/gw/accel/kinds.json")) else [], [])
