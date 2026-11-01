"""VLESS Reality inbound with the real core (docs/k3/SPEC-vless-inbound.md) through ``kaskadctl gw ...``: the port
(443 -> 8443 -> a free one), the gateway xray (Reality, users, routes, the kill switch, the rules of wave 3, the
StatsService), issuance, the cover site and the keys of the node, secrets nowhere but in issuance."""

import json
import os
from urllib.parse import parse_qs, urlsplit

from kaskad import errors, paths, secrets, state
from kaskad.gw import exits, live, model, ops, registry, trace, xray
from kaskad.gw.inbounds import base
from kaskad.gw.inbounds import vless as vin
from kaskad.rules import apply as rules_apply
from kaskad.rules import lists
from tests.fakes_nft import F443, F8443, save_state
from tests.rules_helpers import consented
from tests.test_gw_integration import RealAdapterCase


def ok_check(host, port=443, timeout=5.0):
    return {"ok": True, "tls": "TLSv1.3", "alpn": "h2", "h2": True, "why": ""}


def rule_for(cfg, **match):
    for r in cfg["routing"]["rules"]:
        if all(r.get(k) == v for k, v in match.items()):
            return r
    return None


class NoShuffle(object):
    def shuffle(self, seq):
        return None


class VlessCase(RealAdapterCase):
    def setUp(self):
        super().setUp()
        self.answer = {}
        self.country = {"code": "", "source": ""}
        old = vin._checker["fn"]
        vin._checker["fn"] = lambda host, port=443, timeout=5.0: dict(self.answer.get(host) or ok_check(host))
        self.addCleanup(vin._checker.__setitem__, "fn", old)
        old_geo, old_rng = vin.vless_lists._geo["fn"], vin._rng
        vin.vless_lists._geo["fn"] = lambda st=None: dict(self.country)
        vin._rng = NoShuffle()
        self.addCleanup(vin.vless_lists._geo.__setitem__, "fn", old_geo)
        self.addCleanup(setattr, vin, "_rng", old_rng)

    def cfg(self):
        return json.loads(self.read("/etc/kaskad/gw/xray.json"))

    def rec(self):
        return model.inbound(state.load(), "vless")

    def restarts(self):
        return sum(1 for argv, _ in self.w.runner.calls if argv[:3] == ["systemctl", "restart", xray.UNIT])

    def add_device(self, name="Телефон", platform="android", *extra):
        rc, doc, err = self.run_json("gw", "device", "add", "Мама", name, "--platform", platform, "--access", "vless",
                                     *extra)
        self.assertEqual(rc, 0, err)
        return doc["device"]


class LifeTest(VlessCase):
    def test_the_whole_life_of_a_vless_device(self):
        self.assertNotIsInstance(registry.get("vless"), base.DummyInbound)
        self.enabled_world()                                   # pool VLESS + AWG; the forward F443 holds 443/tcp
        rc, doc, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        rec = self.rec()
        self.assertEqual(rec["port"], 8443)                    # 443 is the forward's: the next candidate
        self.assertTrue(any("443/tcp занят" in n and "взял 8443" in n for n in doc["notes"]), doc["notes"])
        self.assertTrue(any("маскировка — www.microsoft.com" in n for n in doc["notes"]), doc["notes"])
        self.assertEqual((rec["sni"], rec["dest"], rec["mask"], rec["fp"]),
                         ("www.microsoft.com", "www.microsoft.com:443", "list", "chrome"))
        self.assertTrue(rec["mask_check"]["ok"])
        sec = secrets.load()
        self.assertEqual(rec["pub"], vin.public_of(sec["vless.server.private"]))
        self.assertNotIn("private", json.dumps(doc))
        self.assertEqual(self.w.ufw.rules, [("8443/tcp", "kaskad-gw vless")])
        ib = [i for i in self.cfg()["inbounds"] if i["tag"] == "gw-vless"][0]          # no device yet: the inbound
        self.assertEqual(ib["settings"]["clients"], [])                                  # is there, empty — the first
        self.assertEqual(self.cfg()["outbounds"][0]["tag"], "block")                     # one comes without a restart

        self.ok("gw", "user", "add", "Мама")
        restarts = self.restarts()
        d = self.add_device()
        self.assertEqual(d["ips"], {})                         # an xray user: no address
        self.assertEqual(self.restarts(), restarts)           # live, through the API of xray
        verbs = [v for v, _, _ in self.w.xray.api]
        self.assertEqual(verbs, ["adrules", "adu"])            # its chain first, then the user
        (_, args, (rules,)), (_, _, (users,)) = self.w.xray.api
        self.assertEqual(args, ["-timeout", "8", "--server=127.0.0.1:%d" % xray.STATS_PORT, "-append"])
        self.assertEqual([r["user"] for r in rules["routing"]["rules"]], [[d["login"]]])
        self.assertEqual([c["email"] for c in users["inbounds"][0]["settings"]["clients"]], [d["login"]])
        self.assertEqual(os.listdir(paths.gw_dir()).count(".xray-api-0.json"), 0)      # the files are gone
        sec = secrets.load()
        uid = sec["dev.%s.vless.uuid" % d["id"]]
        cfg = self.cfg()
        ib = [i for i in cfg["inbounds"] if i["tag"] == "gw-vless"][0]
        self.assertEqual((ib["port"], ib["protocol"]), (8443, "vless"))
        self.assertEqual(ib["settings"]["clients"], [{"id": uid, "email": d["login"], "flow": "xtls-rprx-vision"}])
        rs = ib["streamSettings"]["realitySettings"]
        self.assertEqual((rs["privateKey"], rs["serverNames"], rs["shortIds"]),
                         (sec["vless.server.private"], ["www.microsoft.com"], [rec["short_id"]]))
        # the StatsService for the sessions: first rule, loopback only, per-user counters
        st_in = [i for i in cfg["inbounds"] if i["tag"] == "stats-in"][0]
        self.assertEqual((st_in["listen"], st_in["port"]), ("127.0.0.1", xray.STATS_PORT))
        self.assertEqual(xray.STATS_PORT, vin.STATS_PORT)
        self.assertEqual(cfg["routing"]["rules"][0], {"type": "field", "inboundTag": ["stats-in"],
                                                      "outboundTag": "gw-stats"})
        self.assertEqual(cfg["api"], {"tag": "gw-stats", "services": ["StatsService", "HandlerService",
                                                                     "RoutingService"]})
        lvl = cfg["policy"]["levels"]["0"]
        self.assertTrue(lvl["statsUserUplink"] and lvl["statsUserDownlink"])
        # private destinations closed for the VLESS users too, then their route, then a closing block
        priv = [r for r in cfg["routing"]["rules"] if "ip" in r and r.get("outboundTag") == "block"][0]
        self.assertIn("gw-vless", priv["inboundTag"])
        self.assertIn("127.0.0.0/8", priv["ip"])
        # stage 1 (SPEC-stage1-exits §1.3 отличия 1, 2; §2.2, §2.4): the user goes where its device is pinned by the
        # decision — one outbound (here the fit exit of «Основная»), not the balancer gw-all of 3.3.5
        mine = rule_for(cfg, inboundTag=["gw-vless"], user=[d["login"]])
        self.assertNotIn("balancerTag", mine)
        self.assertEqual(mine["outboundTag"], self.target_of(d["id"])["outboundTag"])
        self.assertEqual(mine["outboundTag"], "gx-" + self.aid)         # VLESS not checked yet: AmneziaWG, the fit one
        self.assertIsNone(rule_for(cfg, inboundTag=["gw-vless"], outboundTag="block"))     # no closing rule: the
        self.assertEqual(cfg["outbounds"][0]["tag"], "block")                               # first outbound is block
        self.assertTrue(rule_for(cfg, inboundTag=["gw-vless"], user=[d["login"]])["ruleTag"].startswith("vl-"))
        self.assertTrue(self.w.sysd.units[xray.UNIT]["active"])

        self.ok("gw", "device", "route", d["id"], "exit:" + self.vid)
        got = rule_for(self.cfg(), user=[d["login"]])
        self.assertEqual(got["outboundTag"], self.target_of(d["id"])["outboundTag"])     # gw_route says the same
        self.assertEqual(exits.decision(state.load())["states"], {self.vid: "unknown", self.aid: "ok"})
        self.assertEqual(got["outboundTag"], "gx-" + self.aid)   # X not checked yet: §1.3 отличие 4 — another of its group
        self.ok("gw", "device", "route", d["id"], "exit:" + self.vid, "--fail", "direct")
        # stage 1 (§1.3 отличие 4, §2.2): «fail direct» is the last step of the decision, not the balancer
        # b-<id>-direct of 3.3.5: the same target while an exit works
        got = rule_for(self.cfg(), user=[d["login"]])
        self.assertNotIn("balancerTag", got)
        self.assertEqual(got["outboundTag"], self.target_of(d["id"])["outboundTag"])

        rc, doc, _ = self.run_json("gw", "issue", d["id"], "--inbound", "vless")
        self.assertEqual(rc, 0)
        u = urlsplit(doc["items"][0]["body"][0] if doc["items"][0]["format"] == "qr" else doc["items"][0]["body"])
        q = dict((k, v[0]) for k, v in parse_qs(u.query).items())
        self.assertEqual((u.scheme, u.username, u.port, q["pbk"], q["sid"], q["sni"], q["security"]),
                         ("vless", uid, 8443, rec["pub"], rec["short_id"], "www.microsoft.com", "reality"))
        self.assertIn("v2RayTun", " ".join(doc["howto"]))
        out, _ = self.ok("gw", "issue", d["id"], "--inbound", "vless")
        self.assertIn("vless://", out)

        # secrets: the node's key and the UUID only in xray.json (root) and in issuance
        for v in (sec["vless.server.private"], uid):
            for argv in (("gw", "status"), ("gw", "device", "list"), ("gw", "inbound", "list"), ("gw", "sessions"),
                         ("gw", "vless", "show"), ("gw", "scheme")):
                self.assertNotIn(v, self.run_cli(*argv)[1], argv)
            self.assertNotIn(v, self.log())
            self.assertNotIn(v, json.dumps(state.load()))
        self.assertEqual(oct(os.stat(xray.config_path()).st_mode & 0o777), "0o600")
        for argv, _ in self.w.runner.calls:                    # never in argv
            self.assertFalse(any(sec["vless.server.private"] in a or uid in a for a in argv), argv)

        self.ok("gw", "device", "reset", d["id"], "--inbound", "vless")       # a new UUID: the old link is dead
        uid2 = secrets.load()["dev.%s.vless.uuid" % d["id"]]
        self.assertNotEqual(uid, uid2)
        ib = [i for i in self.cfg()["inbounds"] if i["tag"] == "gw-vless"][0]
        self.assertEqual([c["id"] for c in ib["settings"]["clients"]], [uid2])
        restarts, self.w.xray.api[:] = self.restarts(), []
        before = self.target_of(d["id"])
        self.ok("gw", "device", "route", d["id"], "pool")      # every exit is there already: live too
        # stage 1 (SPEC-stage1-exits §1.3 отличие 1, §2.4): the chain of the user is its target — the same exit of the
        # decision (here AmneziaWG, the only fit one, before and after) is the same chain: nothing to call, no restart
        # (3.3.5 swapped the balancer b-<id>-direct for gw-all: adrules + rmrules)
        self.assertEqual((before, self.target_of(d["id"])), ({"outboundTag": "gx-" + self.aid},) * 2)
        self.assertEqual([v for v, _, _ in self.w.xray.api], [])
        self.assertEqual(self.restarts(), restarts)
        self.w.xray.api[:] = []
        self.ok("gw", "device", "route", d["id"], "block")     # «block»: its open connections must go — a restart
        self.assertEqual(self.restarts(), restarts + 1)
        self.ok("gw", "device", "route", d["id"], "pool")
        restarts, self.w.xray.api[:] = self.restarts(), []
        self.ok("gw", "device", "disable", d["id"])                            # off: out of xray, the UUID kept —
        ib = [i for i in self.cfg()["inbounds"] if i["tag"] == "gw-vless"][0]  # a restart cuts what it has open
        self.assertEqual(ib["settings"]["clients"], [])
        self.assertEqual(secrets.load()["dev.%s.vless.uuid" % d["id"]], uid2)
        self.assertEqual(self.w.xray.api, [])
        self.assertEqual(self.restarts(), restarts + 1)
        self.ok("gw", "device", "enable", d["id"])                             # back: live again
        self.assertEqual(self.restarts(), restarts + 1)
        rc, out, err = self.run_cli("gw", "device", "revoke", d["id"], "vless")
        self.assertEqual(rc, 0, err)
        self.assertIn("VLESS переподключится у всех на пару секунд", out + err)          # said honestly
        cfg = self.cfg()
        self.assertEqual([i for i in cfg["inbounds"] if i["tag"] == "gw-vless"][0]["settings"]["clients"], [])
        self.assertNotIn("dev.%s.vless.uuid" % d["id"], secrets.load())
        self.assertEqual(self.restarts(), restarts + 2)                         # revoked: cut at once
        restarts = self.restarts()

        self.w.xray.api_fail = "adu"                                           # an xray without the API: a restart
        d2 = self.add_device("Планшет", "android")
        self.assertEqual(self.restarts(), restarts + 1)
        self.assertIn("dev.%s.vless.uuid" % d2["id"], secrets.load())
        self.assertIn("API не принял adu", self.log())
        self.w.xray.api_fail = None
        self.ok("gw", "device", "revoke", d2["id"], "vless")

        pub = self.rec()["pub"]
        self.ok("gw", "inbound", "uninstall", "vless")
        self.assertEqual(self.w.ufw.rules, [])
        self.assertIn("vless.server.private", secrets.load())  # kept for a reinstall
        rc, doc, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        self.assertEqual((self.rec()["port"], self.rec()["pub"], self.rec()["sni"]), (8443, pub, "www.microsoft.com"))

    def test_the_cover_site_and_the_keys_of_the_node(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "vless")
        self.ok("gw", "user", "add", "Мама")
        d = self.add_device("Ноутбук", "windows")
        rc, doc, _ = self.run_json("gw", "vless", "masks")
        self.assertEqual((rc, doc["current"], doc["masks"][0]["current"], doc["masks"][0]["check"]),
                         (0, "www.microsoft.com", True, None))
        rc, doc, _ = self.run_json("gw", "vless", "masks", "--check")
        self.assertTrue(all(m["check"]["ok"] for m in doc["masks"]))

        rc, doc, err = self.run_json("gw", "vless", "mask", "dl.google.com")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["sni"], doc["changed"], [r["id"] for r in doc["reissue"]]),
                         ("dl.google.com", True, [d["id"]]))
        self.assertIn("Ноутбук (Мама)", doc["text"])
        rs = [i for i in self.cfg()["inbounds"] if i["tag"] == "gw-vless"][0]["streamSettings"]["realitySettings"]
        self.assertEqual((rs["dest"], rs["serverNames"]), ("dl.google.com:443", ["dl.google.com"]))

        self.answer["old.example.org"] = {"ok": False, "tls": "TLSv1.2", "alpn": "", "h2": False,
                                          "why": "сайт ответил TLSv1.2, а нужен TLS 1.3"}
        rc, doc, _ = self.run_json("gw", "vless", "mask", "old.example.org")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("TLS 1.3", doc["error"])
        self.assertEqual(self.rec()["sni"], "dl.google.com")                    # nothing changed
        self.ok("gw", "vless", "mask", "old.example.org", "--no-check")          # the person insists
        self.assertEqual((self.rec()["sni"], self.rec()["mask"]), ("old.example.org", "custom"))
        rc, doc, _ = self.run_json("gw", "vless", "mask", "own")
        self.assertEqual(rc, errors.USAGE)                                       # no site of the node

        rc, out, err = self.run_cli("gw", "vless", "rekey")
        self.assertEqual(rc, errors.USAGE)
        old = (secrets.load()["vless.server.private"], self.rec()["pub"], self.rec()["short_id"])
        rc, doc, err = self.run_json("gw", "vless", "rekey", "--yes")
        self.assertEqual(rc, 0, err)
        sec = secrets.load()
        self.assertNotEqual(old[0], sec["vless.server.private"])
        self.assertEqual((doc["pub"], self.rec()["pub"]), (vin.public_of(sec["vless.server.private"]),) * 2)
        self.assertNotEqual(old[2], self.rec()["short_id"])
        self.assertEqual([r["id"] for r in doc["reissue"]], [d["id"]])
        rs = [i for i in self.cfg()["inbounds"] if i["tag"] == "gw-vless"][0]["streamSettings"]["realitySettings"]
        self.assertEqual(rs["privateKey"], sec["vless.server.private"])
        self.assertNotIn(sec["vless.server.private"], json.dumps(doc))
        rc, doc, _ = self.run_json("gw", "vless", "show")
        self.assertEqual((doc["sni"], doc["pub"], doc["devices"]), ("old.example.org", self.rec()["pub"], 1))


class PortTest(VlessCase):
    def test_443_when_free(self):
        save_state([])
        self.ok("gw", "enable")
        rc, doc, err = self.run_json("gw", "inbound", "plan", "vless")
        self.assertEqual((rc, doc["inbounds"][0]["port"]["port"], doc["inbounds"][0]["port"]["conflicts"]), (0, 443, []))
        self.ok("gw", "inbound", "install", "vless")
        self.assertEqual(self.rec()["port"], 443)

    def test_vless_on_443_holds_the_port_against_the_site(self):
        from kaskad.site import ops as site_ops
        save_state([])
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "vless")
        p = site_ops.port443(state.load(), self.w.runner)
        self.assertFalse(p["free"])
        self.assertIn("VLESS", p["who"])
        self.assertIn("перенесите протокол на другой порт", site_ops.port443_text(p))
        self.ok("gw", "inbound", "port", "vless", "8443")
        self.assertTrue(site_ops.port443(state.load(), self.w.runner)["free"])

    def test_the_cover_site_of_the_node_keeps_443(self):
        st = save_state([])
        st["site"] = {"domain": "vpn.example.com", "mode": "site"}
        state.save(st)
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "vless")
        rec = self.rec()
        self.assertEqual((rec["port"], rec["sni"], rec["mask"]), (8443, "vpn.example.com", "own"))

    def test_443_and_8443_busy_a_free_port(self):
        save_state([F443, F8443])
        self.ok("gw", "enable")
        rc, doc, err = self.run_json("gw", "inbound", "plan", "vless")
        p = doc["inbounds"][0]["port"]
        self.assertEqual(p["port"], 443)
        self.assertTrue(p["conflicts"] and p["free"])
        rc, doc, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        port = self.rec()["port"]
        self.assertTrue(20000 <= port < 60000, port)
        self.assertTrue(any("443/tcp, 8443/tcp занят" in n for n in doc["notes"]), doc["notes"])
        rc, doc, _ = self.run_json("gw", "inbound", "port", "vless", "443")      # the person asks for a busy one
        self.assertEqual(rc, errors.CONFLICT)


class RoutingTest(VlessCase):
    """The kill switch and the routes inside the gateway xray: the rendered config, no command."""

    def setUp(self):
        super().setUp()
        self.st = self.scenario(save=False)
        g = self.st["gateway"]
        g["inbounds"]["vless"] = {"enabled": True, "port": 8443, "sni": "www.microsoft.com",
                                  "dest": "www.microsoft.com:443", "mask": "list"}
        for d in g["devices"]:
            d["access"] = ["vless"]
        self.sec = secrets.load()
        registry.get("vless").prepare(self.st, self.sec)
        self.pool = exits.pool(self.w.runner)
        self.devs = g["devices"]

    def render(self, pool=None):
        return xray.render(self.st, self.sec, self.pool if pool is None else pool)

    def target_of(self, cfg, d):
        r = [x for x in cfg["routing"]["rules"] if x.get("inboundTag") == ["gw-vless"] and d["login"] in x.get("user", [])
             and "domain" not in x and "ip" not in x][0]
        return r.get("outboundTag") or r.get("balancerTag")

    def test_routes_and_the_kill_switch(self):
        """Stage 1 (SPEC-stage1-exits §1.3 отличия 1, 2, 4; §2.2): a user goes where its device is pinned by the decision
        (one outbound — no balancer per route: gw-all / gw-all-direct of 3.3.5 are gone); «exit:X» with X unfit or out
        of the pool — another fit exit of its group (3.3.5: block); nothing fit — the last step: ``fail-block`` (no
        restart) or ``direct``; a block by choice stays ``block``. The kill switch holds: never a leak past the exits."""
        a, b, c = self.devs
        dec = exits.decision(self.st, pool_exits=self.pool)
        self.assertEqual(dec["states"], {self.vid: "ok", self.aid: "ok"})         # this world: both exits work
        fit = ("gx-" + self.vid, "gx-" + self.aid)

        def pin(d, cfg):                                                        # the exit of the decision (gw_route)
            got = self.target_of(cfg, d)
            self.assertEqual(got, exits.target(self.st, d, exits.decision(self.st, pool_exits=self.pool))["xray"]
                             ["outboundTag"])
            return got
        a["route"] = {"via": "pool", "fail": "block"}
        b["route"] = {"via": "exit:e-0000000000", "fail": "block"}             # an exit not in the pool
        c["route"] = {"via": "block", "fail": "block"}
        cfg = self.render()
        self.assertIn(pin(a, cfg), fit)                                         # a fit exit of «Основная»
        self.assertIn(pin(b, cfg), fit)                                         # §1.3 отличие 4 (3.3.5: block)
        self.assertFalse([x for x in cfg["routing"]["rules"] if x.get("user") and "balancerTag" in x])  # no balancer
        self.assertEqual(self.target_of(cfg, c), "block")                       # by choice
        b["route"] = {"via": "exit:e-0000000000", "fail": "direct"}
        a["route"] = {"via": "pool", "fail": "direct"}
        cfg = self.render()
        self.assertIn(pin(a, cfg), fit)
        self.assertIn(pin(b, cfg), fit)
        cfg = self.render(pool=[])                                              # no exits at all: the last step
        self.assertEqual((self.target_of(cfg, a), self.target_of(cfg, b)), ("direct", "direct"))
        a["route"] = {"via": "pool", "fail": "block"}
        self.assertEqual(self.target_of(self.render(pool=[]), a), xray.FAIL_BLOCK)   # a block without a restart
        a["enabled"] = False                                                    # off: no client, no rule
        cfg = self.render()
        self.assertFalse([x for x in cfg["routing"]["rules"] if a["login"] in x.get("user", [])])
        self.assertNotIn(a["login"], json.dumps([i for i in cfg["inbounds"] if i["tag"] == "gw-vless"]))

    def test_vless_devices_use_neither_the_kernel_nor_dnsmasq(self):
        for d in self.devs:
            d["route"] = {"via": "direct", "fail": "block"}
        self.assertFalse(exits.direct_needed(self.st))                         # no direct upstream for dnsmasq
        self.assertFalse(rules_apply.kernel_clients(self.st, True))
        self.assertEqual(model.live_ips(self.st, self.devs[0]), [])
        self.assertEqual(exits.marks(self.st, self.pool, set()), {})           # nothing in gw_route

    def test_rules_of_wave_3_for_vless_users(self):
        adapters = list(xray._adapters(self.st))
        found = rules_apply.socks_users(self.st, self.sec, adapters)
        self.assertEqual([t for t, _ in found], [["gw-vless"]])
        man = {"geosite": {"codes": [c.upper() for c in lists.CATEGORIES]}, "geoip": {"codes": ["DIRECT"]}}
        self.st["gateway"]["rules"] = {"default": "all_exit"}
        self.devs[0]["rules"] = "ru_direct"
        consented(self.st)                 # a node of stage 1: «Россия напрямую» applies with the consent ⚠РФ (R2)
        rules = rules_apply.xray_rules(self.st, self.sec, lambda r, d=None: {"balancerTag": "gw-all"}, adapters, man=man,
                                       is_ready=True)
        self.assertEqual(rules[0]["inboundTag"], ["gw-vless"])
        self.assertEqual(rules[0]["outboundTag"], "block")                      # ads: for every user
        direct = [r for r in rules if r.get("outboundTag") == "direct"]
        self.assertTrue(direct and all(r["user"] == [self.devs[0]["login"]] for r in direct))
        self.assertTrue(rules_apply.xray_wanted(self.st, self.sec, adapters, is_ready=True))

    def test_internal_port_of_the_stats_only_with_vless(self):
        ports = [p for _, p, _ in xray.internal_ports(self.st, self.pool, self.w.runner)]
        self.assertIn(xray.STATS_PORT, ports)
        del self.st["gateway"]["inbounds"]["vless"]
        # stage 1 (SPEC-stage1-exits §1.3: «без влияния на маршрут»; §5.2, REV-s1-t5 З2): the API is drawn whenever
        # there are exits (bo of gw-all, the pool through the API) — its port is reserved with them too
        ports = [p for _, p, _ in xray.internal_ports(self.st, self.pool, self.w.runner)]
        self.assertIn(xray.STATS_PORT, ports)
        ports = [p for _, p, _ in xray.internal_ports(self.st, [], self.w.runner)]     # no VLESS, no exits: none
        self.assertNotIn(xray.STATS_PORT, ports)


class TraceAndLiveTest(VlessCase):
    def test_trace_says_xray_decides(self):
        st = self.scenario(save=False)
        st["gateway"]["inbounds"]["vless"] = {"enabled": True, "port": 8443}
        d = st["gateway"]["devices"][0]
        d["access"] = ["vless"]
        ctx = trace.context(st, pool=exits.pool(self.w.runner), snap=exits.load_snapshot(), v=None, table={},
                            dns_feats={}, socks_files={})
        res = trace.trace(st, d["id"], "8.8.8.8", ctx=ctx, resolver=lambda name: ([], "нет"))
        self.assertTrue(res["who"]["socks"])
        self.assertTrue(any("Через VLESS решает xray" in n for n in res["notes"]), res["notes"])
        d["access"] = ["wg", "vless"]
        res = trace.trace(st, d["id"], "8.8.8.8", ctx=ctx, resolver=lambda name: ([], "нет"))
        self.assertFalse(res["who"]["socks"])
        self.assertTrue(any("выберите VLESS во «Через»" in n for n in res["notes"]), res["notes"])

    def test_live_takes_the_counters_of_xray(self):
        st = self.scenario(save=False)
        st["gateway"]["inbounds"]["vless"] = {"enabled": True, "port": 8443}
        d = st["gateway"]["devices"][0]
        d["access"] = ["vless"]
        stats = {"wg": {"running": True, "sessions": []}, "awg2": {"running": True, "sessions": []},
                 "vless": {"running": True, "rx": 500, "tx": 7000,
                           "sessions": [{"device": d["id"], "ip": None, "since": 1791189000, "rx": 500, "tx": 7000}]}}
        lv = live.Live()
        doc = lv.full(st, self.w.runner, stats, {}, exits.pool(self.w.runner), now=1791190000.0)
        self.assertEqual((doc["inbounds"]["vless"]["rx"], doc["inbounds"]["vless"]["tx"]), (500, 7000))
        self.assertEqual(doc["inbounds"]["vless"]["sessions"], 1)
        dv = doc["devices"][d["id"]]
        self.assertEqual((dv["online"], dv["inbound"], dv["addr"], dv["rx"]), (True, "vless", None, 500))
        fast = lv.fast(self.w.runner, now=1791190002.0)
        self.assertEqual(fast["inbounds"]["vless"]["rx"], 500)                 # kept between the full rounds

    def test_status_reads_the_sessions_from_xray(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "vless")
        self.ok("gw", "user", "add", "Мама")
        d = self.add_device()
        self.w.xray.stats = {d["login"]: (1200, 34000)}
        rc, doc, _ = self.run_json("gw", "sessions")
        self.assertEqual([(s["kind"], s["device"], s["ip"], s["rx"]) for s in doc["sessions"]],
                         [("vless", d["id"], None, 1200)])
        q = self.w.xray.queries[-1]
        self.assertIn("--server=127.0.0.1:%d" % xray.STATS_PORT, q)
        out, _ = self.ok("gw", "sessions")
        self.assertIn("vless", out)
        self.assertTrue(os.path.exists(os.path.join(paths.gw_run_dir(), vin.TRAFFIC_FILE)))
        self.assertEqual(ops.vless_show(None)["devices"], 1)
