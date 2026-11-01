"""The core (work package 4) with the real adapters of work package 5 — WireGuard and SOCKS5 — through
``kaskadctl gw ...``: what the adapters call (secrets, addresses, devices, routes, gateway.host, units,
UFW) and what the core does with their results (gw_route, the ip6 table, the gateway xray, issuance)."""

import json
import os
import stat

from kaskad import paths, secrets, state
from kaskad.gw import exits, model, registry, xray
from kaskad.gw.inbounds import base
from tests.fakes_gw import FakeUfw
from tests.fakes_nft import F443, save_state
from tests.test_gw_ops import CliGwCase


class RealAdapterCase(CliGwCase):
    UFW = FakeUfw

    def setUp(self):
        super().setUp()
        self.wg_calls = []
        r = self.w.runner
        r.binaries.add("wg")
        r.on(["modinfo", "-n"], lambda a, i: (0, "/lib/modules/6.1.0/kernel/%s.ko\n" % a[-1], ""))
        r.on(["/usr/sbin/wg"], self._wg)

    def target_of(self, did):
        """Where the xray traffic of the device goes by the decision as the collector left it (§2.2)."""
        st = state.load()
        dev = next(x for x in model.devices(st) if x["id"] == did)
        dec = exits.decision(st, pool_exits=exits.pool(self.w.runner))
        return exits.target(st, dev, dec)["xray"]

    def _wg(self, argv, inp):
        self.wg_calls.append(argv[1:])
        if argv[1:2] in (["syncconf"], ["setconf"], ["show"]):
            return 0, "", ""
        return 1, "", "fake wg: %s\n" % " ".join(argv[1:])

    def secret_values(self):
        return [v for v in secrets.load().values() if isinstance(v, str) and len(v) >= 8]

    def assert_no_secret(self, text):
        for v in self.secret_values():
            self.assertNotIn(v, text)


class WireGuardTest(RealAdapterCase):
    def test_the_whole_life_of_a_wireguard_device(self):
        self.assertNotIsInstance(registry.get("wg"), base.DummyInbound)        # the adapter of WP5
        save_state([F443])
        self.ok("gw", "enable")
        rc, doc, err = self.run_json("gw", "inbound", "install", "wg")
        self.assertEqual(rc, 0, err)
        st = state.load()
        rec = model.inbound(st, "wg")
        self.assertEqual((rec["port"], rec["subnet"]), (51820, "10.77.1.0/24"))
        self.assertTrue(rec["pub"])                                         # prepare's result is kept
        self.assertIn("wg.server.private", secrets.load())
        self.assertNotIn("private", json.dumps(doc))
        self.assertTrue(self.w.sysd.units["kaskad-gw-wg.service"]["active"])
        self.assertEqual(self.w.ufw.rules, [("51820/udp", "kaskad-gw wg")])   # the adapter's own rule
        self.assertEqual(self.w.nft.rules("gw_fwd6", "kaskad6", "ip6"), ['iifname "kgw-wg" drop'])
        self.assertIn((["wireguard-tools"], True), [(c[-1:], "--no-install-recommends" in c)
                                                     for c in self.w.runner.commands(["env"])])

        self.ok("gw", "user", "add", "Мама")
        rc, doc, err = self.run_json("gw", "device", "add", "Мама", "Ноутбук", "--platform", "linux",
                                     "--access", "wg")
        self.assertEqual(rc, 0, err)
        d = doc["device"]
        self.assertEqual(d["ips"], {"wg": "10.77.1.10"})
        sec = secrets.load()
        for k in ("dev.%s.wg.private" % d["id"], "dev.%s.wg.psk" % d["id"], "dev.%s.password" % d["id"]):
            self.assertIn(k, sec)
        conf = self.read("/etc/wireguard/kgw-wg.conf")
        self.assertIn("AllowedIPs = 10.77.1.10/32", conf)
        self.assertIn(["syncconf", "kgw-wg", self.path("/etc/wireguard/kgw-wg.conf")], self.wg_calls)
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x2ff})   # pool without exits

        rc, doc, _ = self.run_json("gw", "issue", d["id"])
        self.assertEqual((rc, doc["kind"]), (0, "wg"))
        body = doc["items"][0]["body"]
        self.assertIn("Endpoint = 203.0.113.10:51820", body)                # gateway.host
        self.assertIn("PrivateKey = %s" % sec["dev.%s.wg.private" % d["id"]], body)
        self.assertIn("DNS = 10.77.0.1", body)
        out, _ = self.ok("gw", "issue", d["id"])
        self.assertIn("[Interface]", out)
        self.assertNotIn("['", out)                                       # the QR payload is printed as text
        for argv in (("gw", "status"), ("gw", "device", "list"), ("gw", "inbound", "list"), ("gw", "sessions")):
            self.assert_no_secret(self.run_cli(*argv)[1])
        self.assert_no_secret(self.log())
        self.assert_no_secret(json.dumps(state.load()))

        self.ok("gw", "device", "route", d["id"], "direct")
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x201})
        self.ok("gw", "device", "del", d["id"])
        self.assertFalse([k for k in secrets.load() if k.startswith("dev.%s." % d["id"])])
        self.assertNotIn("10.77.1.10", self.read("/etc/wireguard/kgw-wg.conf"))
        self.assertEqual(self.w.nft.elements("gw_route"), {})

        self.ok("gw", "disable")                                          # the adapter stops itself
        self.assertFalse(self.w.sysd.units["kaskad-gw-wg.service"]["active"])
        self.assertEqual(self.w.ufw.rules, [])
        self.ok("gw", "enable")
        self.assertTrue(self.w.sysd.units["kaskad-gw-wg.service"]["active"])
        self.assertEqual(self.w.ufw.rules, [("51820/udp", "kaskad-gw wg")])
        self.ok("gw", "inbound", "uninstall", "wg")
        self.assertFalse(os.path.exists(self.path("/etc/systemd/system/kaskad-gw-wg.service")))
        self.assertEqual(self.w.ufw.rules, [])
        self.assertIn("wg.server.private", secrets.load())                 # keys stay (a reinstall reuses them)
        self.assertEqual(self.w.nft.rules("gw_fwd6", "kaskad6", "ip6"), [])

    def test_port_change_and_conflict(self):
        save_state([F443])
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "wg")
        rc, doc, _ = self.run_json("fwd", "add", "--proto", "udp", "--port", "51820", "--to", "198.51.100.7",
                                   "--to-port", "51820")
        self.assertEqual(rc, 3)
        self.ok("gw", "inbound", "port", "wg", "51999")
        self.assertIn("ListenPort = 51999", self.read("/etc/wireguard/kgw-wg.conf"))
        self.assertEqual(self.w.ufw.rules, [("51999/udp", "kaskad-gw wg")])
        self.ok("fwd", "add", "--proto", "udp", "--port", "51820", "--to", "198.51.100.7", "--to-port", "51820")


class Socks5Test(RealAdapterCase):
    def test_socks5_accounts_and_routes_in_the_gateway_xray(self):
        self.enabled_world()                                              # pool: VLESS (line 1) + AWG (line 2)
        rc, doc, err = self.run_json("gw", "inbound", "install", "socks5")
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(self.w.ufw.rules), [("1080/tcp", "kaskad-gw socks5"), ("1080/udp", "kaskad-gw socks5")])
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "Телефон", "--platform", "android",
                                   "--access", "socks5")
        d = doc["device"]
        self.assertEqual(d["ips"], {})                                    # SOCKS5 needs no address
        pw = secrets.load()["dev.%s.socks5.password" % d["id"]]
        shared = secrets.load()["dev.%s.password" % d["id"]]
        self.assertNotEqual(pw, shared)                                   # plain-text auth: a password of its own
        self.assertNotIn(shared, self.read("/etc/kaskad/gw/xray.json"))
        p = xray.config_path()
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)           # it holds the passwords
        cfg = json.loads(self.read("/etc/kaskad/gw/xray.json"))
        ib = [i for i in cfg["inbounds"] if i["tag"] == "gw-socks5"][0]
        self.assertEqual((ib["port"], ib["settings"]["accounts"]), (1080, [{"user": d["login"], "pass": pw}]))
        rules = [r for r in cfg["routing"]["rules"] if r.get("inboundTag") == ["gw-socks5"] and "network" not in r]
        # T-mip-lab Н1: the UDP of SOCKS5 (no user in xray) — the last rule, to the target of the only account
        udp = cfg["routing"]["rules"][-1]
        self.assertEqual((udp.get("network"), udp.get("outboundTag")), ("udp", "gx-" + self.aid))
        # stage 1 (SPEC-stage1-exits §1.3 отличия 1, 2; §2.2, §2.4): the account goes where its device is pinned by the
        # decision (``exits.target``: one outbound — the exit, or fail-block on the last step), not to the balancer
        # gw-all of the whole pool; the chain of one rule is hot (``sk-``), the shared lists are not in it
        want = self.target_of(d["id"])
        # this world: the VLESS exit not checked yet («unknown»), the AmneziaWG one works — the device is on it
        self.assertEqual(exits.decision(state.load())["states"], {self.vid: "unknown", self.aid: "ok"})
        self.assertEqual(want, {"outboundTag": "gx-" + self.aid})
        self.assertEqual(rules[-1], dict({"type": "field", "inboundTag": ["gw-socks5"], "user": [d["login"]],
                                          "ruleTag": rules[-1]["ruleTag"]}, **want))           # pool, fail block
        self.assertTrue(rules[-1]["ruleTag"].startswith("sk-%s-" % d["login"]))     # stage 1: a hot chain (§2.4)
        self.assertEqual([r for r in cfg["routing"]["rules"] if r.get("user") == [d["login"]]], [rules[-1]])
        self.assertEqual(cfg["outbounds"][0]["tag"], "block")       # no closing rule: the first outbound is block
        self.assertTrue(self.w.sysd.units[xray.UNIT]["active"])

        self.ok("gw", "device", "route", d["id"], "exit:" + self.vid)
        cfg = json.loads(self.read("/etc/kaskad/gw/xray.json"))
        r0 = [r for r in cfg["routing"]["rules"] if r.get("user") == [d["login"]]][0]
        # stage 1 (§1.3 отличие 4, §2.2): «exit:X» with X not fit (here «unknown») — another fit exit of its group, not
        # X itself (3.3.5 drew gx-X whatever its state); the same answer as gw_route (exits.target)
        self.assertEqual(r0["outboundTag"], "gx-" + self.aid)
        self.assertEqual({"outboundTag": r0["outboundTag"]}, self.target_of(d["id"]))
        self.ok("gw", "device", "route", d["id"], "exit:" + self.vid, "--fail", "direct")
        cfg = json.loads(self.read("/etc/kaskad/gw/xray.json"))
        r0 = [r for r in cfg["routing"]["rules"] if r.get("user") == [d["login"]]][0]
        # stage 1 (§1.3 отличие 4, §2.2): «exit:X, fail direct» — X while it works, the other exits of its group when it
        # does not, direct on the last step: the target of the decision, never the balancer b-<id>-direct of 3.3.5
        self.assertEqual(dict((k, r0[k]) for k in ("outboundTag", "balancerTag") if k in r0), self.target_of(d["id"]))
        self.assertEqual(r0["outboundTag"], "gx-" + self.aid)

        rc, doc, _ = self.run_json("gw", "issue", d["id"], "--inbound", "socks5")
        self.assertEqual(rc, 0)
        bodies = " ".join(str(it["body"]) for it in doc["items"])
        self.assertIn("203.0.113.10", bodies)
        self.assertIn(pw, bodies)
        for argv in (("gw", "status"), ("gw", "device", "list"), ("gw", "inbound", "list")):
            self.assertNotIn(pw, self.run_cli(*argv)[1])
        self.assertNotIn(pw, self.log())

        self.ok("gw", "device", "revoke", d["id"], "socks5")              # the account leaves xray
        cfg = json.loads(self.read("/etc/kaskad/gw/xray.json"))
        self.assertNotIn("gw-socks5", [i["tag"] for i in cfg["inbounds"]])
        self.assertIn("dev.%s.password" % d["id"], secrets.load())        # the device password stays
        self.assertNotIn("dev.%s.socks5.password" % d["id"], secrets.load())    # SOCKS5's own goes with it

    def test_socks5_alone_runs_xray_without_exits(self):
        save_state([F443])
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "socks5")
        self.assertNotIn(xray.UNIT, self.w.sysd.units)                    # no accounts yet: no xray
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "ПК", "--access", "socks5", "--route", "direct")
        self.assertTrue(self.w.sysd.units[xray.UNIT]["active"])
        cfg = json.loads(self.read("/etc/kaskad/gw/xray.json"))
        r0 = [r for r in cfg["routing"]["rules"] if r.get("inboundTag") == ["gw-socks5"]][0]
        self.assertEqual(r0["outboundTag"], "direct")
        rc, doc, _ = self.run_json("gw", "status")
        row = [r for r in doc["inbounds"] if r["kind"] == "socks5"][0]
        self.assertTrue(row["running"])
        self.assertEqual(doc["problems"], [])


class XrayForSocks5Test(RealAdapterCase):
    """A fresh server: no pool, no /etc/kaskad/egress — SOCKS5 still gets its xray (the egress downloader), or an
    honest refusal instead of «✓»."""

    def fresh(self):
        os.unlink(self.path("/etc/kaskad/egress/xray"))
        save_state([F443])
        self.ok("gw", "enable")

    def test_install_downloads_xray_and_the_first_device_starts_it(self):
        self.fresh()
        rc, doc, err = self.run_json("gw", "inbound", "install", "socks5")
        self.assertEqual(rc, 0, err)
        self.assertIn(["--egress-ensure-xray"], self.w.script.calls)
        self.assertTrue(os.path.exists(self.path("/etc/kaskad/egress/xray")))
        self.assertIn("GW xray: скачан для шлюза", self.log())
        rc, doc, _ = self.run_json("gw", "status")
        row = [r for r in doc["inbounds"] if r["kind"] == "socks5"][0]
        self.assertTrue(row["running"])                                  # no device yet: idle, not broken
        self.assertIn("порт откроется с первым", row["warnings"][0])
        self.assertEqual(doc["problems"], [])
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "ПК", "--access", "socks5", "--route", "direct")
        self.assertTrue(self.w.sysd.units[xray.UNIT]["active"])           # unit written and started
        self.assertTrue(os.path.exists(self.path("/opt/kaskad/gw/xray")))

    def test_no_download_no_install(self):
        self.fresh()
        self.w.script.xray_ok = False
        rc, out, err = self.run_cli("gw", "inbound", "install", "socks5")
        self.assertEqual(rc, 1)
        self.assertIn("Нет xray для шлюза", err)
        self.assertIn("Не удалось скачать xray.", err)
        self.assertIsNone(model.inbound(state.load(), "socks5"))         # not «installed»
        self.w.script.xray_ok = True
        self.ok("gw", "inbound", "install", "socks5")                     # the owner retries: done at once

    def test_enable_and_apply_try_again_at_most_every_ten_minutes(self):
        save_state([F443])
        self.ok("gw", "inbound", "install", "socks5")                     # the gateway off; xray there (egress)
        os.unlink(self.path("/etc/kaskad/egress/xray"))
        self.w.script.xray_ok = False
        self.w.script.calls[:] = []
        rc, doc, err = self.run_json("gw", "enable")
        self.assertEqual(rc, 0, err)                                      # the other inbounds work without it
        self.assertTrue(any("SOCKS5, протокол VLESS и выходы VLESS не работают" in w for w in doc["warnings"]), doc["warnings"])
        tries = lambda: self.w.script.calls.count(["--egress-ensure-xray"])     # noqa: E731
        self.assertEqual(tries(), 1)
        self.ok("gw", "apply")
        self.assertEqual(tries(), 1)                                      # a failure waits 10 minutes
        stamp = os.path.join(paths.gw_run_dir(), "xray-fetch.stamp")
        old = os.stat(stamp).st_mtime - 601
        os.utime(stamp, (old, old))
        self.w.script.xray_ok = True
        self.ok("gw", "apply")
        self.assertEqual(tries(), 2)
        self.assertTrue(os.path.exists(self.path("/etc/kaskad/egress/xray")))


class DeviceResetTest(RealAdapterCase):
    """``gw device reset``: new secrets of one inbound or of all, made in the same apply and KEPT on disk (a key
    deleted and made again in one change), the old WireGuard peer gone, the other devices untouched."""

    def setup_device(self):
        save_state([F443])
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "inbound", "install", "socks5")
        self.ok("gw", "user", "add", "Мама")
        rc, doc, _ = self.run_json("gw", "device", "add", "Мама", "Ноутбук", "--access", "wg,socks5", "--route", "direct")
        rc, other, _ = self.run_json("gw", "device", "add", "Мама", "Планшет", "--access", "wg", "--route", "direct")
        return doc["device"]["id"], other["device"]["id"]

    def keys(self, did):
        sec = secrets.load()
        return dict((k[len("dev.%s." % did):], v) for k, v in sec.items() if k.startswith("dev.%s." % did))

    def test_one_inbound_then_all(self):
        did, oid = self.setup_device()
        before, other = self.keys(did), self.keys(oid)
        self.assertEqual(sorted(before), ["password", "socks5.password", "wg.private", "wg.psk"])
        restarts = self.w.sysd.restarts.get(xray.UNIT, 0)
        rc, doc, err = self.run_json("gw", "device", "reset", did, "--inbound", "wg")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["kinds"], doc["also"]), (["wg"], []))
        after = self.keys(did)
        self.assertNotEqual(after["wg.private"], before["wg.private"])
        self.assertNotEqual(after["wg.psk"], before["wg.psk"])
        self.assertEqual((after["password"], after["socks5.password"]), (before["password"], before["socks5.password"]))
        self.assertEqual(self.keys(oid), other)                          # the other device keeps its keys
        conf = self.read("/etc/wireguard/kgw-wg.conf")
        self.assertNotIn(before["wg.psk"], conf)                         # the old peer is gone (syncconf)
        self.assertIn(after["wg.psk"], conf)
        self.assertIn(other["wg.psk"], conf)
        self.assertEqual(self.w.sysd.restarts.get(xray.UNIT, 0), restarts)   # SOCKS5 untouched: no xray restart
        issued = self.run_json("gw", "issue", did, "--inbound", "wg")[1]["items"][0]["body"]
        self.assertIn("PrivateKey = %s" % after["wg.private"], issued)
        self.assertIn("GW device reset: Ноутбук (%s) — wg" % did, self.log())
        self.assert_no_secret(self.log())

        out, _ = self.ok("gw", "device", "reset", did)
        again = self.keys(did)
        self.assertTrue(all(again[k] != after[k] for k in after), "every secret of the device is new")
        self.assertEqual(self.keys(oid), other)
        self.assertGreater(self.w.sysd.restarts.get(xray.UNIT, 0), restarts)  # the SOCKS5 account changed
        self.assertIn(again["socks5.password"], self.read("/etc/kaskad/gw/xray.json"))
        self.assertIn("Выдайте ему настройки заново: kaskadctl gw issue %s" % did, out)
        self.assert_no_secret(out)

    def test_refusals_and_a_gateway_that_is_off(self):
        did, _ = self.setup_device()
        rc, out, err = self.run_cli("gw", "device", "reset", did, "--inbound", "l2tp")
        self.assertEqual(rc, 1)
        self.assertIn("не выдан протокол L2TP/IPsec", err)
        rc, out, err = self.run_cli("gw", "device", "reset", did, "--inbound", "nope")
        self.assertEqual(rc, 2)
        self.ok("gw", "disable")
        before = self.keys(did)
        self.ok("gw", "device", "reset", did, "--inbound", "socks5")
        after = self.keys(did)
        self.assertNotEqual(after.get("socks5.password"), before["socks5.password"])   # made at once
        self.assertTrue(after.get("socks5.password"))
        self.assertEqual(after["wg.private"], before["wg.private"])
