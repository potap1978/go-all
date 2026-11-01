"""kaskad.gw.xray (kaskad-gw-xray.service) and kaskad.gw.dns (kaskad-dns.service)."""

import json
import os
import stat

from kaskad import paths, secrets
from kaskad.errors import KaskadError
from kaskad.gw import dns, exits, xray
from kaskad.gw import sys as gsys
from tests.fakes_gw import VLESS_A, VLESS_B, register_dummies, write_pool
from tests.gwcase import GwCase

SOCKS_IN = {"tag": "socks-in", "listen": "0.0.0.0", "port": 1080, "protocol": "socks",
            "settings": {"auth": "password", "udp": True, "accounts": []}}


def rule_for(cfg, **match):
    for r in cfg["routing"]["rules"]:
        if all(r.get(k) == v for k, v in match.items()):
            return r
    return None


class XrayRenderTest(GwCase):
    def test_config_of_the_scenario(self):
        st = self.scenario(save=False)
        pool = exits.pool(self.w.runner)
        cfg = xray.render(st, secrets.load(), pool)
        tags = [ib["tag"] for ib in cfg["inbounds"]]
        tpv = "tproxy-" + self.vid
        # stage 1 (SPEC-stage1-exits §1.3: «без влияния на маршрут»; §2.4, §5.1, §5.2): + the API (stats-in, always
        # with exits), the pool inbound for the probes of the pool (pool-socks) and the probes of the xray exit
        self.assertEqual(tags, ["tproxy-in", tpv, "dns-in", "metrics-in", "stats-in", xray.POOL_SOCKS_TAG,
                                xray.probe_tag(self.vid)])
        # T-M6 №9: no access log — no journal line per connection of a client or per poll of the API
        self.assertEqual(cfg["log"], {"loglevel": "warning", "access": "none"})
        tp = cfg["inbounds"][0]
        self.assertEqual((tp["listen"], tp["port"], tp["protocol"]), ("127.0.0.1", 12345, "dokodemo-door"))
        self.assertEqual(tp["streamSettings"], {"sockopt": {"tproxy": "tproxy"}})
        self.assertEqual(tp["settings"], {"network": "tcp,udp", "followRedirect": True})
        # no sniffing: IPOnDemand would check a sniffed SNI instead of the real destination
        self.assertNotIn("sniffing", tp)
        own = cfg["inbounds"][1]                                   # the VLESS exit of line 1: its own TPROXY port
        self.assertEqual((own["listen"], own["port"], own["streamSettings"]), ("127.0.0.1", 12401, tp["streamSettings"]))
        self.assertEqual(cfg["inbounds"][2]["settings"], {"address": "1.1.1.1", "port": 53, "network": "tcp,udp"})
        self.assertEqual(cfg["inbounds"][3]["port"], xray.METRICS_PORT)
        self.assertEqual("127.0.0.1:%d" % xray.METRICS_PORT, exits.METRICS_ADDR)
        obs = [o["tag"] for o in cfg["outbounds"]]
        # stage 1 (§2.2): fail-block — the block of the last step of a group, no restart; «block» stays the last
        self.assertEqual(obs, ["gx-" + self.vid, "gx-" + self.aid, "direct", xray.FAIL_BLOCK, "block"])
        awg = cfg["outbounds"][1]
        self.assertEqual((awg["protocol"], awg["streamSettings"]["sockopt"]["mark"]), ("freedom", 258))
        # stage 1 (§2.7): the TPROXY of the exit and its probes share its rules px-<id>-… (it comes and goes live)
        own = rule_for(cfg, inboundTag=[tpv, xray.probe_tag(self.vid)], outboundTag="gx-" + self.vid)
        self.assertTrue(own["ruleTag"].startswith(xray.PX_PREFIX + self.vid))
        self.assertFalse([r for r in cfg["routing"]["rules"] if "source" in r])        # no source rules at all
        self.assertEqual(cfg["routing"]["rules"][-1], {"type": "field", "inboundTag": ["tproxy-in"],
                                                       "balancerTag": "gw-vless"})
        self.assertEqual(rule_for(cfg, inboundTag=["dns-in"])["balancerTag"], "gw-all")
        bals = dict((b["tag"], b) for b in cfg["routing"]["balancers"])
        # stage 1 (§1.3 отличие 2, §2.8): the devices are pinned (0x300+N), nobody goes through gw-vless without «по
        # соединениям» — its selector is the prefix (a change of the pool changes no balancer)
        self.assertEqual(bals["gw-vless"]["selector"], ["gx-"])
        self.assertEqual(bals["gw-all"]["selector"], ["gx-"])
        self.assertTrue(all(b["fallbackTag"] == "block" and b["strategy"] == {"type": "leastPing"}
                            for b in bals.values()))
        self.assertEqual(cfg["observatory"]["subjectSelector"], ["gx-"])
        self.assertEqual(cfg["observatory"]["probeURL"], "http://cp.cloudflare.com/generate_204")
        self.assertEqual(cfg["metrics"], {"tag": "metrics"})
        self.assertEqual(rule_for(cfg, inboundTag=["metrics-in"])["outboundTag"], "metrics")
        # liveness never changes the config (no xray restarts when a leg dies) ...
        snap = exits.load_snapshot()
        snap["exits"][self.vid]["alive"] = False
        exits.save_snapshot(snap)
        self.assertEqual(xray.render(st, secrets.load(), pool), cfg)
        # ... nor does a route of a device: it is an element of gw_route (mark 0x301 -> port 12401)
        for d in st["gateway"]["devices"]:
            d["route"] = {"via": "exit:" + self.vid, "fail": "direct"}
        self.assertEqual(xray.render(st, secrets.load(), pool), cfg)

    def test_private_destinations_are_blocked_for_every_client_inbound(self):
        st = self.scenario(save=False)
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [], "xray_inbound": SOCKS_IN}))
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        st["gateway"]["devices"][0]["access"].append("socks5")
        st["gateway"]["devices"][0]["route"] = {"via": "direct", "fail": "block"}
        cfg = xray.render(st, secrets.load(), exits.pool(self.w.runner), ["203.0.113.10/32", "2001:db8::10/128"])
        self.assertEqual(cfg["routing"]["domainStrategy"], "IPOnDemand")
        rules = cfg["routing"]["rules"]
        # stage 1 (SPEC-stage1-exits §1.3: «без влияния на маршрут»; §2.6, §2.7): the clients are the TPROXY of the pool,
        # the pool inbound of the probes and the inbounds of the adapters; the TPROXY of an exit (and its probes) has
        # the same blocks in its own rules px-<id>-… (the exit comes and goes live)
        clients = ["tproxy-in", xray.POOL_SOCKS_TAG, "socks-in"]
        ip_rule = rule_for(cfg, inboundTag=clients, outboundTag="block", ip=xray.blocked_ips(
            ["203.0.113.10/32", "2001:db8::10/128"]))
        self.assertIsNotNone(ip_rule)
        own = ["tproxy-" + self.vid, xray.probe_tag(self.vid)]
        self.assertIsNotNone(rule_for(cfg, inboundTag=own, outboundTag="block", ip=ip_rule["ip"]))
        self.assertIsNotNone(rule_for(cfg, inboundTag=own, outboundTag="block", domain=["full:localhost"]))
        for net_ in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
                     "169.254.0.0/16", "0.0.0.0/8", "224.0.0.0/3", "::1/128", "fc00::/7", "fe80::/10",
                     "203.0.113.10/32", "2001:db8::10/128"):
            self.assertIn(net_, ip_rule["ip"])
        dom_rule = rule_for(cfg, inboundTag=clients, domain=["full:localhost"])
        self.assertEqual(dom_rule["outboundTag"], "block")
        # BEFORE the per-user rules of SOCKS5 and the routes of the TPROXY inbounds
        first_user = next(i for i, r in enumerate(rules) if r.get("user"))
        first_tp = next(i for i, r in enumerate(rules) if r.get("inboundTag") == own and "ip" not in r
                        and "domain" not in r)
        self.assertLess(rules.index(ip_rule), min(first_user, first_tp))
        self.assertLess(rules.index(dom_rule), min(first_user, first_tp))
        self.assertNotIn("dns-in", ip_rule["inboundTag"])          # dnsmasq's upstream is not a client

    def test_internal_dns_goes_through_the_pool(self):
        st = self.scenario(save=False)
        pool = exits.pool(self.w.runner)
        cfg = xray.render(st, secrets.load(), pool)
        dns_ = cfg["dns"]
        self.assertEqual((dns_["tag"], dns_["queryStrategy"], dns_["disableFallbackIfMatch"]),
                         ("gw-dns-int", "UseIPv4", True))
        # the VLESS server of VLESS_A is an IP address: only the probe host is resolved by the node itself
        self.assertEqual(dns_["servers"], [{"address": "localhost", "domains": ["full:cp.cloudflare.com"],
                                            "skipFallback": True}, "1.1.1.1", "1.0.0.1"])
        self.assertEqual(rule_for(cfg, inboundTag=["gw-dns-int"])["balancerTag"], "gw-all")
        # a VLESS server with a name: its name too (it must resolve before any leg is up); an SNI is not one
        write_pool([VLESS_A, VLESS_B, VLESS_A.replace("198.51.100.20", "VPN.Example.org")])
        cfg = xray.render(st, secrets.load(), exits.pool(self.w.runner))
        self.assertEqual(cfg["dns"]["servers"][0]["domains"], ["full:cp.cloudflare.com", "full:vpn.example.org"])
        # nothing in the pool (SOCKS5 alone): the DNS of xray is silent (Р-Г9, REV-337-final-sec З-1) unless a user of
        # SOCKS5 goes directly — its route «напрямую» or the last step of its group «напрямую»
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [], "xray_inbound": SOCKS_IN}))
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        for d in st["gateway"]["devices"]:
            d["access"].append("socks5")
            d["route"] = {"via": "pool", "fail": "block"}
        cfg = xray.render(st, secrets.load(), [])
        self.assertEqual(rule_for(cfg, inboundTag=["gw-dns-int"])["outboundTag"], "block")
        self.assertEqual(cfg["dns"]["servers"], ["1.1.1.1", "1.0.0.1"])
        for route in ({"via": "direct", "fail": "block"}, {"via": "pool", "fail": "direct"}):
            st["gateway"]["devices"][1]["route"] = route
            cfg = xray.render(st, secrets.load(), [])
            self.assertEqual(rule_for(cfg, inboundTag=["gw-dns-int"])["outboundTag"], "direct", route)
        st["gateway"]["devices"][1]["enabled"] = False          # an inactive device: no path of its own
        cfg = xray.render(st, secrets.load(), [])
        self.assertEqual(rule_for(cfg, inboundTag=["gw-dns-int"])["outboundTag"], "block")

    def test_socks_users_by_route(self):
        st = self.scenario(save=False)
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [], "xray_inbound": SOCKS_IN}))
        g = st["gateway"]
        g["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        for d in g["devices"]:
            d["access"].append("socks5")
        g["devices"][2]["route"] = {"via": "exit:" + self.vid, "fail": "direct"}
        g["devices"][0]["route"] = {"via": "direct", "fail": "block"}
        cfg = xray.render(st, secrets.load(), exits.pool(self.w.runner))
        self.assertIn("socks-in", [ib["tag"] for ib in cfg["inbounds"]])
        self.assertEqual(rule_for(cfg, user=["telefon-0001"])["outboundTag"], "direct")
        self.assertEqual(rule_for(cfg, user=["router-0002"])["outboundTag"], "gx-" + self.aid)
        r = rule_for(cfg, user=["noutbuk-0003"])
        # stage 1 (SPEC-stage1-exits §1.3 отличия 1, 4; §2.2): «exit:X, fail direct» — X itself while it works (here it
        # does), else another exit of its group, direct on the last step — the decision says it, not a balancer
        # b-<id>-direct with its fallback (3.3.5)
        self.assertEqual(exits.decision(st, pool_exits=exits.pool(self.w.runner))["states"][self.vid], "ok")
        self.assertEqual((r.get("outboundTag"), r.get("balancerTag")), ("gx-" + self.vid, None))
        bals = dict((b["tag"], b) for b in cfg["routing"]["balancers"])
        self.assertNotIn("b-%s-direct" % self.vid, bals)
        last = [r for r in cfg["routing"]["rules"] if r.get("inboundTag") == ["socks-in"]][-1]
        self.assertEqual((last.get("user"), last["outboundTag"]), (None, "block"))             # unknown users
        socks = [r for r in cfg["routing"]["rules"] if r.get("inboundTag") == ["socks-in"]]
        first_open = next(i for i, r in enumerate(socks) if not r.get("user"))
        self.assertTrue(all(r.get("user") for r in socks[:first_open]))                         # users first
        self.assertEqual(set(r["outboundTag"] for r in socks[first_open:]), {"block"})           # then block

    def test_without_exits_only_the_adapters_need_xray(self):
        st = self.scenario(save=False)
        with open(paths.egress_up(), "w") as f:
            f.write("")
        self.assertFalse(xray.needed(st, secrets.load(), []))
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [], "xray_inbound": SOCKS_IN}))
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        st["gateway"]["devices"][0]["access"].append("socks5")
        self.assertTrue(xray.needed(st, secrets.load(), []))
        cfg = xray.render(st, secrets.load(), [])
        self.assertEqual([ib["tag"] for ib in cfg["inbounds"]], ["tproxy-in", "socks-in"])
        self.assertNotIn("observatory", cfg)
        self.assertEqual(rule_for(cfg, inboundTag=["tproxy-in", "socks-in"], outboundTag="block",
                                  ip=xray.blocked_ips())["outboundTag"], "block")
        # pool, fail=block, no exit: stage 1 (SPEC-stage1-exits §1.3, §2.2) — the block of the last step, fail-block
        # (its return is live, no restart); a block by choice stays «block»
        self.assertEqual(rule_for(cfg, user=["telefon-0001"])["outboundTag"], xray.FAIL_BLOCK)
        self.assertEqual(cfg["routing"]["rules"][-1]["outboundTag"], "block")


class XrayApplyTest(GwCase):
    def setUp(self):
        super().setUp()
        self.with_xports()                 # a table of stage 1 before xray of stage 1 (B3, REV-s1-b1 З1)

    def test_apply_writes_tests_and_starts_once(self):
        st = self.scenario()
        res = xray.apply(st, secrets.load(), self.w.runner)
        self.assertEqual((res["needed"], res["changed"], res["started"]), (True, True, True))
        p = xray.config_path()
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)               # root only: a credential
        cfg = json.loads(self.read("/etc/kaskad/gw/xray.json"))
        self.assertEqual(cfg["inbounds"][0]["tag"], "tproxy-in")
        # the node's own address — in the block of the clients (stage 1: after the rules of the API, the metrics and
        # the DNS — SPEC-stage1-exits §1.3 «без влияния на маршрут», §2.4)
        own = rule_for(cfg, inboundTag=["tproxy-in", xray.POOL_SOCKS_TAG], outboundTag="block", domain=None)
        self.assertIn("203.0.113.10/32", own["ip"])
        self.assertFalse(os.path.exists(p.replace("xray.json", "xray.new.json")))
        unit = self.read("/etc/systemd/system/kaskad-gw-xray.service")
        run = xray.run_binary()
        self.assertTrue(os.path.isfile(run) and stat.S_IMODE(os.stat(run).st_mode) == 0o755)
        self.assertEqual(self.w.xray.tested[0], cfg)                             # tested with the copy that runs
        for line in ("User=kaskad-xray", "Group=kaskad-xray", "NoNewPrivileges=yes",
                     "AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE",
                     "CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE",
                     "LoadCredential=xray.json:%s" % p,
                     "ExecStartPre=+/bin/sh -c 'exec /usr/sbin/nft list chain ip kaskad gw_pre >/dev/null'",
                     "ExecStart=%s run -c %%d/xray.json" % run, "After=network-online.target kaskad-net.service"):
            self.assertIn(line + "\n", unit)
        self.assertEqual(self.w.users.uids, {"kaskad-xray": 990})               # the system user, made once
        self.assertTrue(self.w.sysd.units[xray.UNIT]["active"])
        res = xray.apply(st, secrets.load(), self.w.runner)
        self.assertEqual((res["changed"], res["started"], res["restarted"]), (False, False, False))
        self.assertEqual(len(self.w.xray.tested), 1)
        self.assertEqual(len(self.w.users.calls), 1)
        st["gateway"]["devices"][0]["route"] = {"via": "exit:" + self.vid, "fail": "block"}
        res = xray.apply(st, secrets.load(), self.w.runner)                      # a route: no restart
        self.assertEqual((res["changed"], res["restarted"]), (False, False))
        st["gateway"]["exits"][self.aid] = {"enabled": False}                  # the pool xray serves
        self.w.xray.api[:] = []
        res = xray.apply(st, secrets.load(), self.w.runner)
        # stage 1 (SPEC-stage1-exits §1.3: «без влияния на маршрут»; §2.7): a change of the pool is live — the exit's
        # outbound leaves through the API, no restart (3.3.5 restarted xray); the file follows
        self.assertEqual((res["changed"], res["restarted"]), (True, False))
        self.assertEqual(res.get("hot"), {"rm_outbounds": 1})
        self.assertEqual([v for v, _, _ in self.w.xray.api], ["rmo"])
        self.assertEqual(self.w.sysd.restarts.get(xray.UNIT, 0), 0)
        self.assertNotIn("gx-" + self.aid, [o["tag"] for o in json.loads(self.read("/etc/kaskad/gw/xray.json"))
                                            ["outbounds"]])

    def test_binary_copy_follows_the_egress(self):
        st = self.scenario()
        xray.apply(st, secrets.load(), self.w.runner)
        with open(paths.egress_xray(), "w") as f:
            f.write("#!/bin/sh\n# a newer xray\n")
        os.utime(paths.egress_xray(), (1790000500, 1790000500))
        xray.apply(st, secrets.load(), self.w.runner)
        with open(xray.run_binary()) as f:
            self.assertIn("a newer xray", f.read())
        # 3.3.4: a new own xray (the pinned version replaced an old one; install.sh checked the settings with it) —
        # the running service is restarted onto it, not left on the old file until some later restart
        self.assertEqual(self.w.sysd.restarts.get(xray.UNIT, 0), 1)
        xray.apply(st, secrets.load(), self.w.runner)
        self.assertEqual(self.w.sysd.restarts.get(xray.UNIT, 0), 1)              # the same file: no restart

    def test_refused_config_keeps_the_old_one(self):
        st = self.scenario()
        xray.apply(st, secrets.load(), self.w.runner)
        before = self.read("/etc/kaskad/gw/xray.json")
        self.w.xray.fail = "Failed to start: main: failed to load config files > infra/conf: unknown field\n"
        st["gateway"]["exits"][self.aid] = {"enabled": False}
        with self.assertRaises(KaskadError) as cm:
            xray.apply(st, secrets.load(), self.w.runner)
        self.assertIn("unknown field", str(cm.exception))
        self.assertEqual(self.read("/etc/kaskad/gw/xray.json"), before)
        self.assertEqual(self.w.sysd.restarts.get(xray.UNIT, 0), 0)

    def test_not_needed_stops_it(self):
        st = self.scenario()
        xray.apply(st, secrets.load(), self.w.runner)
        res = xray.apply(st, secrets.load(), self.w.runner, pool_exits=[])
        self.assertEqual((res["needed"], res["stopped"]), (False, True))
        self.assertFalse(self.w.sysd.units[xray.UNIT]["active"])

    def test_no_binary(self):
        st = self.scenario()
        os.unlink(paths.egress_xray())
        with self.assertRaises(KaskadError) as cm:
            xray.apply(st, secrets.load(), self.w.runner)
        self.assertIn("Прокси для API Telegram", str(cm.exception))


class DnsTest(GwCase):
    def test_conf(self):
        st = self.scenario(save=False)
        via_pool = dns.conf_text(st, True)
        self.assertIn("bind-interfaces\nlisten-address=10.77.0.1\nno-resolv\n", via_pool)
        self.assertEqual([ln for ln in via_pool.splitlines() if ln.startswith("listen-address")],
                         ["listen-address=10.77.0.1"])                 # 10.77.0.1 only, no 127.0.0.1
        self.assertNotIn("interface=", via_pool)
        self.assertIn("server=127.0.0.1#10853\n", via_pool)
        self.assertNotIn("server=1.1.1.1", via_pool)
        direct = dns.conf_text(st, False)
        self.assertIn("server=1.1.1.1\nserver=1.0.0.1\n", direct)

    def test_direct_upstream_only_for_direct_devices(self):
        st = self.scenario(save=False)
        both = dns.conf_text(st, True, direct=True)
        self.assertIn("strict-order\nserver=127.0.0.1#10853\nserver=1.1.1.1\nserver=1.0.0.1\n", both)   # pool first
        pool_only = dns.conf_text(st, True, direct=False)
        self.assertNotIn("1.1.1.1", pool_only)
        self.assertNotIn("strict-order", pool_only)
        closed = dns.conf_text(st, False, direct=False)                # no pool, no direct device: fail closed
        self.assertFalse([ln for ln in closed.splitlines() if ln.startswith("server=")])
        res = dns.apply(st, self.w.runner, via_pool=False, direct=False)
        self.assertEqual(res["servers"], [])
        res = dns.apply(st, self.w.runner, via_pool=True, direct=exits.direct_needed(st))
        # 3.3.7.2 (SEC-3371 Б-2): kaskad-dns — the pool only; the direct upstream in kaskad-gw-dnsdirect
        # (d-3: fail=direct)
        self.assertEqual(res["servers"], ["127.0.0.1#10853"])
        self.assertEqual(res["direct_servers"], ["127.0.0.1#10853", "1.1.1.1", "1.0.0.1"])
        self.assertIn("strict-order\nserver=127.0.0.1#10853\nserver=1.1.1.1\nserver=1.0.0.1\n",
                      self.read("/etc/kaskad/gw/dnsmasq-direct.conf"))
        self.assertIn("\nport=5364\n", self.read("/etc/kaskad/gw/dnsmasq-direct.conf"))
        self.assertNotIn("1.1.1.1", self.read("/etc/kaskad/gw/dnsmasq.conf"))
        unit = self.read("/etc/systemd/system/kaskad-gw-dnsdirect.service")
        self.assertIn("--conf-file=", unit)
        self.assertIn("dnsmasq-direct.conf", unit)
        self.assertIn("direct", dns.status(self.w.runner))
        # nobody may go directly any more — the second dnsmasq goes (unit and config)
        res = dns.apply(st, self.w.runner, via_pool=True, direct=False)
        self.assertEqual(res["direct_servers"], [])
        self.assertFalse(os.path.exists(self.path("/etc/systemd/system/kaskad-gw-dnsdirect.service")))
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/gw/dnsmasq-direct.conf")))
        self.assertNotIn("direct", dns.status(self.w.runner))

    def test_filter_aaaa_from_dnsmasq_2_89(self):
        st = self.scenario()
        res = dns.apply(st, self.w.runner, via_pool=True)
        self.assertTrue(res["filter_aaaa"])
        self.assertIn("\nfilter-AAAA\n", self.read("/etc/kaskad/gw/dnsmasq.conf"))
        self.assertEqual(dns.status(self.w.runner)["filter_aaaa"], True)
        self.w.dnsmasq.version = "2.88"                                 # older: skipped with a note
        res = dns.apply(st, self.w.runner, via_pool=True)
        self.assertEqual((res["filter_aaaa"], res["changed"]), (False, True))
        self.assertNotIn("filter-AAAA", self.read("/etc/kaskad/gw/dnsmasq.conf"))
        s = dns.status(self.w.runner)
        self.assertFalse(s["filter_aaaa"])
        self.assertIn("2.88", s["note"])
        self.assertEqual(dns.version(self.w.runner), (2, 88))

    def test_apply_and_restart_on_change(self):
        st = self.scenario()
        res = dns.apply(st, self.w.runner, via_pool=True)
        self.assertEqual((res["changed"], res["started"]), (True, True))
        unit = self.read("/etc/systemd/system/kaskad-dns.service")
        # F2: forking — dnsmasq binds its port before it forks, a port it cannot take fails ``systemctl start``
        self.assertIn("Type=forking\nPIDFile=/run/kaskad-dns.pid\n"
                      "ExecStart=/usr/sbin/dnsmasq --conf-file=%s\n" % dns.conf_path(), unit)
        self.assertIn("\nport=5354\nbind-interfaces\nlisten-address=10.77.0.1\n", self.read("/etc/kaskad/gw/dnsmasq.conf"))
        self.assertIn("After=network.target kaskad-net.service", unit)
        self.assertEqual(dns.apply(st, self.w.runner, via_pool=True)["changed"], False)
        res = dns.apply(st, self.w.runner, via_pool=False)
        self.assertEqual((res["changed"], res["restarted"]), (True, True))

    def test_dnsmasq_base_is_installed_without_starting_services(self):
        self.w = self.make_world(dnsmasq=False)
        self.assertEqual(dns.ensure_installed(self.w.runner), ["dnsmasq-base"])
        self.assertEqual(self.w.pkgs.policy_seen, [True])                         # policy-rc.d during apt
        self.assertFalse(os.path.exists(paths.policy_rc_d()))                     # and gone after
        self.assertEqual(dns.ensure_installed(self.w.runner), [])


class SysTest(GwCase):
    def test_apt_keeps_a_foreign_policy_and_masks(self):
        self.write("/usr/sbin/policy-rc.d", "#!/bin/sh\nexit 101\n", 0o755)        # Docker images ship one
        self.w.sysd.units["strongswan-starter.service"] = {"packaged": True, "enabled": True, "active": True}
        self.assertEqual(gsys.apt_install(self.w.runner, ["strongswan"], mask=["strongswan-starter.service"]),
                         ["strongswan"])
        self.assertEqual(self.read("/usr/sbin/policy-rc.d"), "#!/bin/sh\nexit 101\n")
        self.assertEqual(self.w.sysd.units["strongswan-starter.service"],
                         {"packaged": True, "enabled": False, "active": False, "masked": True})
        with self.assertRaises(KaskadError):
            gsys.apt_install(self.w.runner, ["bad; rm -rf /"])

    def test_failed_apt(self):
        self.w.pkgs.fail = "E: Unable to locate package nosuch\n"
        with self.assertRaises(KaskadError) as cm:
            gsys.apt_install(self.w.runner, ["nosuch"])
        self.assertIn("nosuch", str(cm.exception))
        self.assertFalse(os.path.exists(paths.policy_rc_d()))

    def test_sysctl_set_persists(self):
        self.w.sysctl.values["net.ipv4.conf.kawg2.rp_filter"] = "1"
        self.assertTrue(gsys.sysctl_set(self.w.runner, "net.ipv4.conf.kawg2.rp_filter", 2))
        self.assertFalse(gsys.sysctl_set(self.w.runner, "net.ipv4.conf.kawg2.rp_filter", 2))
        self.assertIn("net.ipv4.conf.kawg2.rp_filter=2", self.read("/etc/sysctl.d/90-kaskad-gw.conf"))

    def test_modprobe(self):
        self.w.modprobe_fail.add("nft_tproxy")
        self.assertEqual(gsys.modprobe(self.w.runner, ["nft_tproxy", "dummy"]), ["nft_tproxy"])
