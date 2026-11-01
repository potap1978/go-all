"""PLAN-multi-ip Ш3 (the inbounds take connections on the chosen addresses, «Защита доступа») and Ш4 (the address an
exit is reached from: sendThrough of the xray exits, SNAT of the AmneziaWG legs, Н4, Л2) — the table of §4 of the plan:
the rules of nft and xray, «SSH and the forwards never in the set», the refusals, the collector (an address gone and
back), a copy of another server, the SSH menu, MCP. The migration byte for byte and the rollback to 8582b624 —
tests/test_mip_migration.py."""

import json
import os
import random
import re
import shutil
import subprocess
import unittest
from unittest import mock

from kaskad import errors, paths, state
from kaskad.gw import addresses as A
from kaskad.gw import exits, mip, model, xray
from tests.fakes_gw import FakeIp, VLESS_A, awg_line, install_parser, write_pool
from tests.fakes_nft import F443
from tests.panel.test_panel_proxy import ProxyCase
from tests.test_3351_cmdinj import function
from tests.test_egress_parse_s1 import FIX
from tests.test_gw_ops import CliGwCase

A_IP, B_IP = "85.143.0.20", "85.143.0.10"          # A — the route's (primary), B — the second address of eth0
IN_MARK = "fib daddr type local meta l4proto . th dport"
LEG_EP = ("198.51.100.31", 51820)                  # awg_conf's Endpoint (tests/fakes_gw.py)


def two_addresses(case, primary=A_IP, second=B_IP):
    case.w.ip = FakeIp(node_ip=primary, extra_addrs=["%s/32" % second] if second else [])
    case.w.runner._handlers = [h for h in case.w.runner._handlers if h[0] != ["ip"]]
    case.w.runner.on(["ip"], case.w.ip)


def chain(name):
    """The rules of chain ``name`` of the boot file kaskad.nft (the whole table as applied)."""
    text = open(paths.nft_file(), encoding="utf-8").read()
    m = re.search(r"\n\tchain %s \{\n(.*?)\n\t\}" % re.escape(name), text, re.S)
    return [ln.strip() for ln in m.group(1).splitlines()[1:]] if m else []


def leg_conf(iface, ep=LEG_EP):
    """The config install.sh writes for a leg (Endpoint = the checked IP): the SNAT of the leg reads it."""
    p = os.path.join(paths.egress_dir(), "awg", ".%s.conf" % iface)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("[Interface]\nPrivateKey = x\n\n[Peer]\nPublicKey = y\nEndpoint = %s:%d\n" % ep)


def cache(addrs, route_src, dev="eth0", via="85.143.0.1", nat=(), ways=None):
    """A cache of «Адреса сервера»: ``addrs`` — [(ip, prefix, iface, secondary)]."""
    found = [{"ip": ip, "prefix": pfx, "iface": ifc, "v": 4, "secondary": sec} for ip, pfx, ifc, sec in addrs]
    route = {"dev": dev, "via": via, "src": route_src}
    return {"at": 1, "route": route, "addrs": found, "nat": [{"local": l, "ip": p} for l, p in nat], "nat_at": 1,
            "ways": ways if ways is not None else dict((a["ip"], dict(route)) for a in found)}


class Node(CliGwCase):
    """The scenario of GwCase (wg 51820, awg2 41023, the forward of 443, VLESS_A + the leg kawg2) on a machine with A
    (the route's) and B, the gateway applied."""

    def setUp(self):
        super().setUp()
        two_addresses(self)
        self.scenario(forwards=[F443])
        self.w.leg(2)
        self.leg = [e for e in exits.pool(self.w.runner) if e["id"] == self.aid][0]["iface"]
        leg_conf(self.leg)
        self.ok("gw", "host", A_IP)
        self.ok("gw", "apply")

    def run_in(self, *ips, env=None):
        with mock.patch.dict(os.environ, env or {}, clear=False):
            return self.run_json("gw", "addresses", "in", *ips)


# ── Ш3: the rule of gw_pre ─────────────────────────────────────────────────

class InRuleTest(Node):
    def test_one_rule_right_after_lo_return_and_back(self):
        before = open(paths.nft_file(), encoding="utf-8").read()
        self.assertNotIn(IN_MARK, before)
        old = chain("gw_pre")
        rc, doc, _ = self.run_in(A_IP)
        self.assertEqual(rc, 0, doc)
        self.assertEqual(state.load()["gateway"]["addresses"]["in"], {"*": [A_IP]})
        pre = chain("gw_pre")
        mine = [r for r in pre if IN_MARK in r]
        self.assertEqual(len(mine), 1)
        self.assertEqual(pre.index(mine[0]), pre.index('iifname "lo" return') + 1)
        self.assertTrue(mine[0].startswith('iifname != "kgw-*" ct direction original ' + IN_MARK + " { "), mine[0])
        self.assertTrue(mine[0].endswith(" } ip daddr != { %s } drop" % A_IP), mine[0])
        self.assertIn("{ udp . 41023, udp . 51820 }", mine[0])                  # wg, awg2
        self.assertNotIn("tcp . 443", mine[0])                                  # the forward of 443: never
        self.assertNotIn("tcp . 22", mine[0])
        self.assertEqual([r for r in pre if r != mine[0]], old)                 # nothing else moved
        self.assertIn(mip.BACK, doc["text"])
        self.ok("gw", "addresses", "in", "--all")
        self.assertEqual(open(paths.nft_file(), encoding="utf-8").read(), before)   # byte for byte as it was
        self.assertNotIn("addresses", state.load()["gateway"])

    def test_a_list_with_commas_and_the_lines(self):
        rc, doc, _ = self.run_in("%s,%s" % (A_IP, B_IP))
        self.assertEqual((rc, doc["in"]), (0, [A_IP, B_IP]))
        self.assertTrue([r for r in chain("gw_pre") if r.endswith("ip daddr != { %s, %s } drop" % (B_IP, A_IP))])
        out, _ = self.ok("gw", "addresses")
        self.assertIn("Приём подключений: только на %s, %s" % (A_IP, B_IP), out)
        self.assertIn("SSH-меню 17 → 4 → «Приём подключений» → 1", out)
        rc, doc, _ = self.run_json("gw", "addresses")
        self.assertEqual(doc["mip"]["in"]["list"], [A_IP, B_IP])
        self.assertEqual([x["id"] for x in doc["mip"]["pool"]], [self.vid, self.aid])

    def test_every_chosen_address_gone_no_rule(self):
        self.run_in(A_IP)
        two_addresses(self, B_IP, None)                     # A gone (the hoster took it)
        self.w.leg(2)
        A.collect(self.w.runner, net=False)
        self.ok("gw", "apply")
        self.assertFalse([r for r in chain("gw_pre") if IN_MARK in r])          # every address takes them again
        out, _ = self.ok("gw", "addresses")
        self.assertIn("Выбранных адресов приёма (%s) на сервере сейчас нет" % A_IP, out)


class InPortsTest(unittest.TestCase):
    """The set of the rule: the ports of the inbounds — never SSH, the session's port, 80, the forwards."""

    def ports(self, st, pairs, env=None):
        class Ad:
            def ports(self, st):
                return list(pairs)
        with mock.patch.object(model, "enabled_kinds", lambda st: ["x"]), \
                mock.patch("kaskad.gw.registry.get", lambda kind: Ad()), \
                mock.patch.object(mip, "site_on_443", lambda st: False), \
                mock.patch.object(mip, "sshd_ports", lambda: {2222}):
            return mip.inbound_ports(st, env or {})

    def test_ssh_and_the_forwards_never_in_the_set(self):
        st = {"forwards": [dict(F443)]}
        never = {("tcp", 22), ("tcp", 2222), ("tcp", 80), ("tcp", 4022), ("tcp", 443)}
        env = {"SSH_CONNECTION": "198.51.100.9 50000 85.143.0.20 4022"}
        rnd = random.Random(34)
        pool = sorted(never) + [("udp", 22), ("udp", 443), ("tcp", 8443), ("udp", 51820)]
        for _ in range(200):
            pairs = rnd.sample(pool, rnd.randint(1, len(pool)))
            got = self.ports(st, pairs, env)
            flat = set((pr, p) for pr, lo, hi in got for p in range(lo, hi + 1))
            self.assertFalse(flat & never, (pairs, got))
            self.assertEqual(flat, set(pairs) - never)

    def test_the_hop_range_is_one_interval(self):
        pairs = [("udp", 8443)] + [("udp", p) for p in range(20000, 20100)]
        got = self.ports({}, pairs)
        self.assertEqual(got, [("udp", 8443, 8443), ("udp", 20000, 20099)])
        rule = mip.in_rule({"in": {"addrs": [A_IP], "ports": got}})
        self.assertIn("{ udp . 8443, udp . 20000-20099 }", rule)

    def test_the_real_hy2_adapter_gives_its_range(self):
        st = {"gateway": {"inbounds": {"hy2": {"enabled": True, "port": 8443, "hop": "20000-20099"}}}}
        with mock.patch.object(model, "enabled_kinds", lambda st: ["hy2"]), \
                mock.patch.object(mip, "site_on_443", lambda st: False):
            got = mip.inbound_ports(st, {})
        self.assertEqual(got, [("udp", 8443, 8443), ("udp", 20000, 20099)])

    def test_our_site_on_443_is_in_80_never(self):
        with mock.patch.object(model, "enabled_kinds", lambda st: []):
            got = mip.inbound_ports({"site": {"mode": "site", "domain": "vpn.example.com"}}, {})
        self.assertEqual(got, [("tcp", 443, 443)])

    def test_a_nat_pair_its_private_address_in_the_rule(self):
        m = cache([("10.0.0.5", 24, "ens5", False), ("10.0.0.6", 24, "ens5", True)], "10.0.0.5", dev="ens5",
                  via="10.0.0.1", nat=[("10.0.0.5", A_IP), ("10.0.0.6", B_IP)])
        st = {"gateway": {"addresses": {"in": {"*": [B_IP]}}}}
        with mock.patch.object(mip, "inbound_ports", lambda st, env=None: [("udp", 51820, 51820)]):
            p = mip.in_plan(st, mip.view(m))
        self.assertEqual((p["addrs"], p["gone"]), (["10.0.0.6"], []))
        self.assertTrue(mip.in_rule({"in": p}).endswith("ip daddr != { 10.0.0.6 } drop"))
        self.assertIsNone(mip.in_rule({"in": dict(p, addrs=[])}))             # every one gone: no rule
        self.assertIsNone(mip.in_rule({"in": None}))


# ── «Защита доступа» (PLAN §3 п. 1–4) ──────────────────────────────────────

class AccessTest(Node):
    def refused(self, reason, *ips, env=None):
        before = json.dumps(state.load()["gateway"].get("addresses"))
        rc, doc, _ = self.run_in(*ips, env=env)
        self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, reason), doc)
        self.assertIn(mip.BACK, doc["error"])
        self.assertEqual(json.dumps(state.load()["gateway"].get("addresses")), before)   # nothing written
        self.assertFalse([r for r in chain("gw_pre") if IN_MARK in r])
        return doc

    def test_the_address_for_clients_left_out(self):
        doc = self.refused("client_out", B_IP)
        self.assertIn("Клиенты подключаются на %s" % A_IP, doc["error"])

    def test_an_address_not_on_the_server(self):
        self.refused("address_not_here", "85.143.0.99")

    def test_the_panel_and_the_subscriptions_left_out(self):
        with mock.patch.object(mip, "_panel_host", lambda: (B_IP, None)):
            self.refused("panel_out", A_IP)

    def test_the_way_back_and_the_empty_list(self):
        self.run_in(A_IP)
        st = state.load()                                   # the address for clients outside the list (a state of an
        st["gateway"]["host"] = B_IP                        # older run — ``gw host`` refuses it now, REV-mip-s34 С-1)
        state.save(st)
        self.ok("gw", "addresses", "in", "--all")           # never refused, whatever is chosen
        rc, doc, _ = self.run_json("gw", "addresses", "in")
        self.assertEqual(rc, errors.USAGE)
        rc, doc, _ = self.run_json("gw", "addresses", "in", A_IP, "--all")
        self.assertEqual(rc, errors.USAGE)

    def test_ssh_through_another_address_is_a_warning(self):
        rc, doc, _ = self.run_in(A_IP, env={"SSH_CONNECTION": "198.51.100.9 50000 %s 22" % B_IP})
        self.assertEqual(rc, 0)
        self.assertTrue(any("Вы подключены по SSH через %s" % B_IP in w for w in doc["warnings"]), doc)

    def test_the_panel_opened_through_another_address_is_409(self):
        from kaskad.panel import api, routes

        class Req:
            listener, local_ip, query = "public", B_IP, {}

            def json(self):                                 # 3.3.7.15 F11 (З-3): ``ver`` of GET /api/gw/addresses
                return {"ips": [A_IP], "ver": mip.addresses_ver(state.load())}
        with mock.patch.object(api, "runner", lambda: self.w.runner), mock.patch.object(api, "_changed", lambda: None):
            with self.assertRaises(routes.HttpError) as cm:
                api.gw_addresses_in(Req())
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("Вы открыли панель через %s" % B_IP, str(cm.exception.args))
        self.assertIn("SSH-меню 17 → 4 → «Приём подключений» → 1", str(cm.exception.args))
        self.assertNotIn("addresses", state.load()["gateway"])
        Req.local_ip = A_IP                                  # through the chosen one: taken
        with mock.patch.object(api, "runner", lambda: self.w.runner), mock.patch.object(api, "_changed", lambda: None):
            api.gw_addresses_in(Req())
        self.assertEqual(state.load()["gateway"]["addresses"]["in"], {"*": [A_IP]})

    def test_the_panel_listens_on_the_chosen_ones(self):
        from kaskad.panel import conf
        self.assertEqual(sorted(conf.listen_addresses(self.w.runner, A_IP)), sorted([A_IP, B_IP]))
        self.run_in(A_IP)
        self.assertEqual(conf.listen_addresses(self.w.runner, A_IP), [A_IP])


class HostInTest(Node):
    """REV-mip-s34 С-1: the address for clients outside «Приём подключений» is refused (``gw host`` with and without
    --force, ``--detect``, ``gw enable --host``); a domain — a warning; on every address again — taken."""

    def refused(self, *args):
        before = state.load()["gateway"]["host"]
        rc, doc, _ = self.run_json(*args)
        self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, "host_not_in"), doc)
        self.assertIn("kaskadctl gw addresses in %s,%s" % (A_IP, B_IP), doc["error"])
        self.assertIn(mip.BACK, doc["error"])
        self.assertEqual(state.load()["gateway"]["host"], before)              # nothing written
        return doc

    def test_the_address_for_clients_outside_the_list(self):
        from kaskad.gw import net
        self.run_in(A_IP)
        self.refused("gw", "host", B_IP)
        self.refused("gw", "host", B_IP, "--force")
        with mock.patch.object(net, "detect_host", lambda runner: (B_IP, "route")):
            self.refused("gw", "host", "--detect")
        self.refused("gw", "enable", "--host", B_IP)
        self.assertEqual([r for r in chain("gw_pre") if IN_MARK in r][0].split("ip daddr != ")[1], "{ %s } drop" % A_IP)
        self.ok("gw", "host", A_IP)                                            # in the list: as before
        rc, doc, _ = self.run_json("gw", "host", "vpn.example.com")
        self.assertEqual(rc, 0, doc)
        self.assertTrue(any("vpn.example.com" in w for w in doc["warnings"]), doc)     # its A records not known
        self.ok("gw", "addresses", "in", "--all")
        self.ok("gw", "host", B_IP)                                            # every address again: taken


class PanelAddrTest(Node):
    def test_behind_nginx_through_another_address_is_409(self):
        """REV-mip-s34 С-2: the request behind nginx carries the address it came to (the header of our vhost) — the
        same «Защита доступа» as the open mode."""
        from kaskad.panel import api, routes

        class Req:
            listener, local_ip, query = "proxy", B_IP, {}

            def json(self):                                 # 3.3.7.15 F11 (З-3): ``ver`` of GET /api/gw/addresses
                return {"ips": [A_IP], "ver": mip.addresses_ver(state.load())}
        with mock.patch.object(api, "runner", lambda: self.w.runner), mock.patch.object(api, "_changed", lambda: None):
            with self.assertRaises(routes.HttpError) as cm:
                api.gw_addresses_in(Req())
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("Вы открыли панель через %s" % B_IP, str(cm.exception.args))
        self.assertNotIn("addresses", state.load()["gateway"])
        Req.listener = "local"                               # the SSH tunnel: no address of its own is believed
        with mock.patch.object(api, "runner", lambda: self.w.runner), mock.patch.object(api, "_changed", lambda: None):
            api.gw_addresses_in(Req())
        self.assertEqual(state.load()["gateway"]["addresses"]["in"], {"*": [A_IP]})


class ServerAddrHeaderTest(ProxyCase):
    """REV-mip-s34 С-2: «the address the request came to» — behind nginx the header of our vhost, on the open mode the
    address of its socket, on the tunnel nothing; the visitor's own ``X-Kaskad-Server-Addr`` is never believed where
    nginx does not set it (and where it does, ``proxy_set_header`` replaces it — VhostTest)."""

    def setUp(self):
        super().setUp()
        self.got = []

        def fake(runner, ips=None, every=False, local=None, env=None, back=None, **kw):
            self.got.append(local)
            return {"ok": True, "in": ips, "warnings": [], "text": "", "back": back}
        p = mock.patch.object(mip, "set_in", fake)
        p.start()
        self.addCleanup(p.stop)

    def test_the_address_of_each_listener(self):
        from kaskad.panel import auth
        auth.set_password("правильный-пароль")
        self.proxy_on()
        tok = self.cookie_of(self.pcall("POST", "/api/login", {"password": "правильный-пароль"}))
        body = {"ips": [A_IP], "ver": "0" * 12}           # 3.3.7.15 F11: ``ver`` is required (the fake does not check it)
        for head, want in ((B_IP, B_IP), ("::ffff:%s" % A_IP, A_IP), ("not-an-ip", ""), (None, "")):
            r = self.pcall("PUT", "/api/gw/addresses/in", body, cookie=tok, headers={"X-Kaskad-Server-Addr": head})
            self.assertEqual(r.status, 200, r)
            self.assertEqual(self.got[-1], want, head)
        r = self.call("PUT", "/api/gw/addresses/in", body, headers={"X-Kaskad-Server-Addr": B_IP})     # the tunnel
        self.assertEqual((r.status, self.got[-1]), (200, None), r)
        port = self.public_on()                                                # the open mode: its socket
        r = self.call("POST", self.pb + "/api/login", {"password": "правильный-пароль"}, port=port, https=True)
        tok = self.cookie_of(r)
        r = self.call("PUT", self.pb + "/api/gw/addresses/in", body, port=port, https=True, cookie=tok,
                      headers={"X-Kaskad-Server-Addr": B_IP})
        self.assertEqual((r.status, self.got[-1]), (200, "127.0.0.1"), r)


class VhostTest(unittest.TestCase):
    def test_every_location_of_the_panel_sets_the_address(self):
        """REV-mip-s34 С-2: each ``proxy_pass`` of our vhost has ``proxy_set_header X-Kaskad-Server-Addr`` — nginx sends
        its own value and drops the visitor's header of that name; behind xray — the address of the PROXY protocol."""
        from kaskad.site import nginx
        for behind, var in ((None, "$server_addr"), ("/run/kaskad/site.sock", "$proxy_protocol_server_addr")):
            text = nginx.render("vpn.example.com", "app-" + "A" * 86, version="1.24.0", ipv6=False, behind=behind)
            blocks = [b for b in text.split("location ")[1:] if "proxy_pass" in b]
            self.assertGreater(len(blocks), 3)
            for b in blocks:
                self.assertEqual(b.count("proxy_set_header X-Kaskad-Server-Addr %s;" % var), 1, b)
            self.assertEqual(text.lower().count("x-kaskad-server-addr"), len(blocks))


class DomainWarnTest(unittest.TestCase):
    def test_the_records_of_the_site_only_for_its_own_domain(self):
        """REV-mip-s34 З-1: the A records the site's check found are those of the site's domain only."""
        site = {"domain": "site.example.com", "dns": {"a": [B_IP]}}
        st = {"gateway": {"host": "vpn.example.com"}, "site": site}
        self.assertIn("vpn.example.com", mip._domain_warn("vpn.example.com", st, [B_IP]))     # not known: said
        self.assertIsNone(mip._domain_warn("site.example.com", st, [B_IP]))                   # its records chosen
        self.assertIsNone(mip._domain_warn("Site.Example.com.", st, [A_IP, B_IP]))
        self.assertIn(B_IP, mip._domain_warn("site.example.com", st, [A_IP]))                 # one left out


class SshSocketTest(unittest.TestCase):
    def test_the_ports_of_ssh_socket_are_never_in_the_set(self):
        """REV-mip-s34 П-3: sshd under ssh.socket listens where ListenStream says (Ubuntu 22.10+)."""
        import tempfile
        root = tempfile.mkdtemp(prefix="k3-sshsock-")
        self.addCleanup(shutil.rmtree, root, True)
        d = os.path.join(root, "etc", "systemd", "system", "ssh.socket.d")
        os.makedirs(d)
        with open(os.path.join(d, "port.conf"), "w", encoding="utf-8") as f:
            f.write("[Socket]\nListenStream=\nListenStream=0.0.0.0:2222\nListenStream=[::]:2223\nListenStream=2224\n"
                    "ListenStream=/run/x.sock\n")
        with mock.patch.dict(os.environ, {"KASKAD_ROOT": root}):
            self.assertEqual(mip.socket_ports(), {2222, 2223, 2224})
            self.assertTrue({2222, 2223, 2224} <= mip.sshd_ports())
            self.assertTrue({("tcp", 2222), ("tcp", 2224)} <= mip.never_ports({}, env={}))


# ── Ш4: the address of an exit ────────────────────────────────────────────

XRAY_KEYS = {"vless": VLESS_A, "hy2": FIX["hy2"], "ss": FIX["ss_sip002"], "trojan": FIX["trojan_tls"],
             "vmess": FIX["vmess_tls"]}


class SrcTest(Node):
    def outbounds(self):
        cfg = json.load(open(xray.config_path(), encoding="utf-8"))
        return dict((o.get("tag"), o) for o in cfg["outbounds"])

    def test_every_xray_exit_type_and_never_awg(self):
        install_parser()
        write_pool(list(XRAY_KEYS.values()) + [awg_line(2)])
        ids = dict((k, exits.exit_id(v)) for k, v in XRAY_KEYS.items())
        self.ok("gw", "apply")
        obs = self.outbounds()
        for k, eid in ids.items():
            self.assertNotIn("sendThrough", obs[exits.tag(eid)], k)            # src null: as before
        for eid in ids.values():
            self.ok("gw", "addresses", "exit", eid, B_IP)
        self.ok("gw", "addresses", "exit", self.aid, B_IP)
        obs = self.outbounds()
        for k, eid in ids.items():
            self.assertEqual(obs[exits.tag(eid)].get("sendThrough"), B_IP, k)
        self.assertNotIn("sendThrough", obs[exits.tag(self.aid)])              # AWG: SNAT of the leg, not xray
        self.assertNotIn("sendThrough", obs["direct"])                         # Н4: the route's src is the first one
        for eid in ids.values():
            self.ok("gw", "addresses", "exit", eid, "main")
        obs = self.outbounds()
        self.assertFalse([k for k, eid in ids.items() if "sendThrough" in obs[exits.tag(eid)]])

    def test_the_leg_snat_before_masquerade_and_conntrack_forgets(self):
        self.w.conntrack.legs.add((LEG_EP[0], str(LEG_EP[1])))
        rc, doc, _ = self.run_json("gw", "addresses", "exit", self.aid, B_IP)
        self.assertEqual(rc, 0, doc)
        post = chain("gw_post")
        rule = "ip daddr %s udp dport %d snat to %s" % (LEG_EP + (B_IP,))
        self.assertIn(rule, post)
        self.assertLess(post.index(rule), [i for i, r in enumerate(post) if r.endswith("masquerade")][0])
        self.assertEqual(doc["flushed"], 1)
        argv = ["-D", "-p", "udp", "-d", LEG_EP[0], "--dport", str(LEG_EP[1])]
        self.assertEqual(self.w.conntrack.calls.count(argv), 1)               # the old address forgotten
        self.ok("gw", "addresses", "exit", self.aid, "main")
        self.assertNotIn(rule, chain("gw_post"))
        self.assertEqual(self.w.conntrack.calls.count(argv), 2)               # and back: forgotten again
        self.ok("gw", "addresses", "exit", self.vid, B_IP)
        self.assertEqual(self.w.conntrack.calls.count(argv), 2)               # an xray exit: no leg, nothing

    def test_the_same_address_again_does_not_break_the_leg(self):
        """REV-mip-s34 П-2: ``exit <AWG> B`` twice — the flow of the leg forgotten once."""
        self.w.conntrack.legs.add((LEG_EP[0], str(LEG_EP[1])))
        argv = ["-D", "-p", "udp", "-d", LEG_EP[0], "--dport", str(LEG_EP[1])]
        self.ok("gw", "addresses", "exit", self.aid, B_IP)
        rc, doc, _ = self.run_json("gw", "addresses", "exit", self.aid, B_IP)
        self.assertEqual((rc, doc["flushed"]), (0, 0), doc)
        self.assertEqual(self.w.conntrack.calls.count(argv), 1)

    def test_the_bots_proxy_is_said(self):
        """REV-mip-s34 З-4: the «Telegram API proxy» reaches the xray exits from the main address — said on the choice
        and in ``gw addresses``."""
        rc, doc, _ = self.run_json("gw", "addresses", "exit", self.vid, B_IP)
        self.assertTrue(any("«Прокси для API Telegram»" in w for w in doc["warnings"]), doc)
        doc = self.run_json("gw", "addresses")[1]
        self.assertTrue(any("«Прокси для API Telegram»" in x for x in doc["mip"]["src_lines"]), doc["mip"])

    def test_the_collector_forgets_a_leg_whose_address_went_and_came(self):
        """REV-mip-s34 С-3: the SNAT of a flow lives in its conntrack record — the collector forgets the leg's flow after
        the apply where its address went, and again where it came back; a round without a change — nothing."""
        self.ok("gw", "addresses", "exit", self.aid, B_IP)
        argv = ["-D", "-p", "udp", "-d", LEG_EP[0], "--dport", str(LEG_EP[1])]
        n = self.w.conntrack.calls.count(argv)
        self.ok("gw", "collect", "--once")                  # the first round of the process: nothing known of the last
        self.assertEqual(self.w.conntrack.calls.count(argv), n + 1)
        self.ok("gw", "collect", "--once")
        self.assertEqual(self.w.conntrack.calls.count(argv), n + 1)             # nothing changed: not forgotten
        two_addresses(self, A_IP, None)                     # B gone
        self.w.leg(2)
        self.ok("gw", "collect", "--once")
        self.assertNotIn("snat to %s" % B_IP, " ".join(chain("gw_post")))
        self.assertEqual(self.w.conntrack.calls.count(argv), n + 2)             # the leg goes on from A at once
        two_addresses(self)                                 # B back
        self.w.leg(2)
        self.ok("gw", "collect", "--once")
        self.assertIn("snat to %s" % B_IP, " ".join(chain("gw_post")))
        self.assertEqual(self.w.conntrack.calls.count(argv), n + 3)             # and from B again

    def test_the_group_gets_one_address(self):
        rc, doc, _ = self.run_json("gw", "addresses", "exit", "--group", "main", B_IP)
        self.assertEqual(rc, 0, doc)
        self.assertEqual(sorted(doc["ids"]), sorted([self.vid, self.aid]))
        self.assertEqual(mip.src_map(state.load()), {self.vid: B_IP, self.aid: B_IP})

    def test_refusals(self):
        rc, doc, _ = self.run_json("gw", "addresses", "exit", self.aid, "85.143.0.99")
        self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, "address_not_here"))
        with mock.patch.object(mip, "route_ok", lambda v, local: False):
            rc, doc, _ = self.run_json("gw", "addresses", "exit", self.aid, B_IP)
            self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, "route_other"))
            self.ok("gw", "addresses", "exit", self.vid, B_IP)              # xray binds the socket: its route
        rc, doc, _ = self.run_json("gw", "addresses", "exit", "e-0000000000", B_IP)
        self.assertEqual(rc, errors.USAGE)
        rc, doc, _ = self.run_json("gw", "addresses", "exit", B_IP)            # neither an exit nor a group
        self.assertEqual(rc, errors.USAGE)
        self.assertEqual(mip.src_map(state.load()), {self.vid: B_IP})

    def test_gone_and_back_by_the_collector(self):
        self.ok("gw", "addresses", "exit", self.aid, B_IP)
        self.ok("gw", "addresses", "exit", self.vid, B_IP)
        rule = "ip daddr %s udp dport %d snat to %s" % (LEG_EP + (B_IP,))
        self.ok("gw", "collect", "--once")
        self.assertIn(rule, chain("gw_post"))
        two_addresses(self, A_IP, None)                     # B gone
        self.w.leg(2)
        self.ok("gw", "collect", "--once")
        self.assertNotIn(rule, chain("gw_post"))
        self.assertNotIn("sendThrough", self.outbounds()[exits.tag(self.vid)])
        self.assertIn("временно подключается с основного адреса — адреса %s нет" % B_IP, self.log())
        self.assertEqual(mip.src_map(state.load()), {self.vid: B_IP, self.aid: B_IP})    # the choice stays
        two_addresses(self)                                 # B back
        self.w.leg(2)
        self.ok("gw", "collect", "--once")
        self.assertIn(rule, chain("gw_post"))
        self.assertEqual(self.outbounds()[exits.tag(self.vid)].get("sendThrough"), B_IP)
        self.assertIn("снова подключается с %s" % B_IP, self.log())


class N4Test(unittest.TestCase):
    """Н4 (T-mip-lab): the kernel's inbounds leave from the first address of the device, xray's — from the route's src.
    REV-mip-s34 Б-1: without the administrator's choice nothing of it is drawn (3.3.7.12 byte for byte) — the difference
    is only said (``gw addresses``, the SSH menu)."""

    def test_without_any_choice_nothing_is_drawn_the_difference_is_said(self):
        # Р2 of the lab: B first on eth0, then A; the route from A; neither ``in`` nor ``src`` in the state
        m = cache([(B_IP, 32, "eth0", False), (A_IP, 32, "eth0", False)], A_IP)
        st = {"gateway": {"enabled": True}}
        mp = mip.plan(st, [], m=m)
        self.assertEqual(mip.post_rules(mp), [])                              # no SNAT of the clients
        self.assertIsNone(mip.direct_send(mp))                                # no sendThrough of «direct»
        self.assertEqual(mip.fingerprint(mp), "")
        doc = mip.status(st, mip.view(m))
        self.assertEqual(doc["h4"], {"kernel": B_IP, "xray": A_IP})
        line = [x for x in mip.src_lines(doc) if "двух адресов" in x]
        self.assertEqual(len(line), 1, mip.src_lines(doc))
        self.assertIn("WireGuard, AmneziaWG, OpenVPN, IKEv2, L2TP, PPTP и SSTP — с %s" % B_IP, line[0])
        self.assertIn("через xray — с %s (адрес маршрута системы)" % A_IP, line[0])
        self.assertIsNone(mip.status(st, mip.view(cache([(A_IP, 32, "eth0", False), (B_IP, 32, "eth0", False)],
                                                        A_IP)))["h4"])          # one address for both: no line

    def test_the_difference_is_found_where_masquerade_takes_another_one(self):
        two = [(B_IP, 24, "eth0", False), (A_IP, 32, "eth0", False)]
        v = mip.view(cache(two, A_IP, via="85.143.0.1"))
        self.assertEqual(mip.masq_source(v), B_IP)                            # its network holds the gateway
        self.assertEqual(mip.snat_plan(v), {"dev": "eth0", "src": A_IP})
        self.assertIsNone(mip.plan({}, [], m=cache(two, A_IP, via="85.143.0.1"))["snat"])     # found, not drawn
        mp = {"snat": mip.snat_plan(v), "legs": [("198.51.100.31", 51820, B_IP, "e-1")]}      # a choice (later)
        self.assertEqual(mip.post_rules(mp), ["ip daddr 198.51.100.31 udp dport 51820 snat to %s" % B_IP,
                                              'ip saddr @gw_nets oifname "eth0" ip daddr != @gw_nets snat to %s' % A_IP])
        self.assertEqual(mip.direct_send(mp), A_IP)
        self.assertIsNone(mip.snat_plan(mip.view(cache(two, B_IP))))              # the route's own: as before
        self.assertIsNone(mip.snat_plan(mip.view(cache(two[:1], B_IP))))         # one address: as before
        self.assertIsNone(mip.snat_plan(mip.view(cache([(B_IP, 24, 'e"x', False), (A_IP, 32, 'e"x', False)], A_IP,
                                                         dev='e"x'))))         # a strange name: nothing drawn
        self.assertEqual(mip.fingerprint({}), "")
        self.assertEqual(mip.post_rules({}), [])


class LimitsTest(unittest.TestCase):
    """T-mip-lab Н2, Н3: Hysteria2 with the mask answers from the route's address — said at the inbounds and on a
    choice of another address (a line, not a refusal: a client without NAT still gets in)."""

    def test_hy2_with_the_mask_answers_from_the_main_address_only(self):
        st = {"gateway": {"inbounds": {"hy2": {"enabled": True, "port": 8443}}}}
        two = mip.view(cache([(A_IP, 32, "eth0", False), (B_IP, 32, "eth0", False)], A_IP))
        one = mip.view(cache([(A_IP, 32, "eth0", False)], A_IP))
        line = "Отвечают только с основного адреса %s: Hysteria2 с маскировкой —" % A_IP
        with mock.patch.object(model, "enabled_kinds", lambda st: ["hy2"]), \
                mock.patch("kaskad.gw.awgengine.inbounds_of", lambda st: []), \
                mock.patch.object(mip, "_panel_host", lambda: ("", None)):
            self.assertIn(line, "\n".join(mip.lines(mip.status(st, two))))
            self.assertNotIn(line, "\n".join(mip.lines(mip.status(st, one))))           # one address: nothing to say
            self.assertTrue(any(line in w for w in mip.access_check(st, [B_IP], two, env={})))
            self.assertFalse(mip.access_check(st, [A_IP], two, env={}))                # the main one: no line
            st["gateway"]["inbounds"]["hy2"]["obfs"] = False
            self.assertEqual(mip.reply_limits(st), [])                                 # without the mask: answers right


class TellTest(unittest.TestCase):
    def test_the_transitions(self):
        said = []

        def log(key, **kw):
            said.append(key)
        st = {"gateway": {"addresses": {"in": {"*": [A_IP, B_IP]}}, "exits": {"e-1": {"src": B_IP}}}}
        ok = ((), False, ())
        self.assertEqual(mip.tell(None, ok, log=log), [])                        # the first round: nothing
        mip.tell(ok, ((B_IP,), False, (("e-1", B_IP),)), log=log, st=st)
        self.assertEqual(said, ["gw_mip.log.in_gone", "gw_mip.log.src_gone"])
        del said[:]
        mip.tell(((B_IP,), False, (("e-1", B_IP),)), ((A_IP, B_IP), True, ()), log=log, st=st)
        self.assertEqual(said, ["gw_mip.log.in_all_gone", "gw_mip.log.src_back"])
        del said[:]
        st2 = {"gateway": {"addresses": {"in": {"*": [A_IP]}}}}                 # B taken off the list by a person
        mip.tell(((B_IP,), False, (("e-1", B_IP),)), ok, log=log, st=st2)
        self.assertEqual(said, [])


class SeenTest(unittest.TestCase):
    def test_an_address_found_once_is_remembered(self):
        found = [{"ip": A_IP, "v": 4, "iface": "eth0"}, {"ip": B_IP, "v": 4, "iface": "eth0"}]
        s1 = A._seen(None, found, [])
        self.assertEqual(s1, [A_IP, B_IP])
        s2 = A._seen({"seen": s1}, found[:1], [{"local": "10.0.0.5", "ip": "85.143.0.30"}])
        self.assertEqual(s2, [B_IP, A_IP, "85.143.0.30"])                     # B gone — still seen; the newest last
        s3 = A._seen({"seen": ["10.0.0.1", "x"] + ["85.143.1.%d" % i for i in range(40)]}, found, [])
        self.assertEqual(len(s3), A.SEEN_MAX)
        self.assertEqual(s3[-2:], [A_IP, B_IP])


# ── a copy of the node ────────────────────────────────────────────────────

class BackupTest(unittest.TestCase):
    def test_the_addresses_of_the_copy_stay_on_its_machine(self):
        from kaskad import backup
        g_file = {"addresses": {"in": {"*": [B_IP]}}, "exits": {"e-1": {"src": B_IP, "name": "Вена"},
                                                                 "e-2": {"src": B_IP}, "e-3": {"name": "Рига"}}}
        now = {"gateway": {"exits": {"e-2": {"src": "93.184.216.40"}}}}
        same = json.loads(json.dumps(g_file))
        backup._addresses_of_this_machine(same, now, True)
        self.assertEqual(same, g_file)                                          # this very server: as it was
        other = json.loads(json.dumps(g_file))
        backup._addresses_of_this_machine(other, now, False)
        self.assertNotIn("addresses", other)
        self.assertEqual(other["exits"], {"e-1": {"name": "Вена"}, "e-2": {"src": "93.184.216.40"},
                                          "e-3": {"name": "Рига"}})


# ── Л2: the table of a leg is the leg's alone ─────────────────────────────

class L2Test(unittest.TestCase):
    FAKE_IP = r"""
ip() {
    echo "IP:$*" >> "$CALLS"
    case "$*" in
        "-4 route show table 257") printf '%s' "$ROUTES" ;;
        "-4 rule show") printf '%s' "$RULES" ;;
        "rule del fwmark "*" unreachable") [ -e "$CALLS.del" ] && return 2; : > "$CALLS.del" ;;
    esac
    return 0
}
k3_fold_tty() { shift; local s; for s in "$@"; do echo -e "$s"; done; }
k3_networkd_keep_foreign() { :; }
tf() { local k=$1; shift; printf '%s' "$k:$*"; }
egress_leg_held() { return 1; }
egress_up_file() { return 1; }
AWG_DIR="$LEG_DIR"
"""

    def run_bash(self, call, routes="", rules=""):
        import tempfile
        d = tempfile.mkdtemp(prefix="k3-l2-")
        self.addCleanup(shutil.rmtree, d, True)
        calls = os.path.join(d, "calls")
        src = "".join(function(n) for n in ("egress_leg_table_foreign", "egress_leg_table_refuse",
                                           "egress_awg_route_leg", "egress_awg_conf_unit")) + self.FAKE_IP + call
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8", "CALLS": calls,
               "ROUTES": routes, "RULES": rules, "LEG_DIR": d}
        r = subprocess.run(["bash", "-c", "set -o pipefail\n" + src + '\necho "RC:$?"'], capture_output=True,
                           text=True, timeout=60, env=env)
        got = open(calls, encoding="utf-8").read() if os.path.exists(calls) else ""
        return r.stdout, r.stderr, got

    def test_a_foreign_default_via_in_table_257_is_never_overwritten(self):
        out, err, calls = self.run_bash('egress_awg_route_leg 257 257 kawg1',
                                        routes="default via 85.143.0.1 dev eth0 src 85.143.0.10 \n",
                                        rules="0:\tfrom all lookup local\n32765:\tfrom 85.143.0.10 lookup 257\n")
        self.assertIn("RC:1", out)
        self.assertNotIn("route replace", calls)
        self.assertNotIn("table 257", calls.replace("IP:-4 route show table 257", ""))     # nothing into it, no lookup
        self.assertNotIn("rule del", calls)
        self.assertIn("EGRESS_LEG_TABLE_FOREIGN:kawg1 257 table 257: default via 85.143.0.1 dev eth0 src 85.143.0.10;"
                      " ip rule: from 85.143.0.10 lookup 257 257", err)
        out, err, calls = self.run_bash('egress_awg_conf_unit kawg1',
                                        routes="default via 85.143.0.1 dev eth0 \n")
        self.assertIn("RC:1", out)                                              # the unit does not start
        self.assertIn("EGRESS_LEG_TABLE_FOREIGN:kawg1 257", err)

    def test_a_refused_leg_is_closed_and_opens_when_its_table_is_free(self):
        """REV-mip-s34 С-4: the refusal leaves the mark of the leg closed — «fwmark 0x101 unreachable» (no table: the
        foreign one is never touched), once; the table ours again — the lookup rule first, then the closing one goes."""
        foreign = "default via 85.143.0.1 dev eth0 src 85.143.0.10 \n"
        out, err, calls = self.run_bash('egress_awg_route_leg 257 257', routes=foreign, rules="0:\tfrom all lookup local\n")
        self.assertIn("RC:1", out)
        self.assertIn("IP:rule add fwmark 257 unreachable", calls)
        self.assertNotIn("route replace", calls)
        self.assertNotIn("lookup 257", calls)
        self.assertNotIn("rule add fwmark 257 table", calls)
        closed = "0:\tfrom all lookup local\n32765:\tfrom all fwmark 0x101 unreachable\n"
        out, err, calls = self.run_bash('egress_awg_route_leg 257 257', routes=foreign, rules=closed)
        self.assertIn("RC:1", out)
        self.assertNotIn("rule add", calls)                                    # closed already: not a second rule
        out, err, calls = self.run_bash('egress_awg_route_leg 257 257 kawg1', routes="", rules=closed)
        self.assertIn("RC:0", out)
        lines = calls.splitlines()
        self.assertIn("IP:rule add fwmark 257 table 257", lines)
        self.assertIn("IP:rule del fwmark 257 unreachable", lines)
        self.assertLess(lines.index("IP:rule add fwmark 257 table 257"), lines.index("IP:rule del fwmark 257 unreachable"))
        self.assertIn("IP:route replace default dev kawg1 table 257", lines)

    def test_the_closing_rule_goes_with_kaskad(self):
        """REV-mip-s34 С-4: the removal of Kaskad (``k3_leg_routes_remove``) takes the closing rule of a leg off too —
        only ours (a mark 0x1NN, no table), by its pref."""
        import tempfile
        d = tempfile.mkdtemp(prefix="k3-l2r-")
        self.addCleanup(shutil.rmtree, d, True)
        calls = os.path.join(d, "calls")
        src = function("k3_leg_routes_remove") + self.FAKE_IP + "k3_leg_routes_remove"
        rules = ("0:\tfrom all lookup local\n4010:\tfrom all fwmark 0x101 unreachable\n"
                 "4011:\tfrom all fwmark 0x300 unreachable\n32766:\tfrom all lookup main\n")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8", "CALLS": calls, "ROUTES": "",
               "RULES": rules, "LEG_DIR": d}
        subprocess.run(["bash", "-c", src], capture_output=True, text=True, timeout=120, env=env)
        with open(calls, encoding="utf-8") as f:
            got = f.read()
        self.assertIn("IP:rule del pref 4010 fwmark 0x101 unreachable", got)
        self.assertNotIn("0x300", got)                                        # not a mark of a leg: not ours

    def test_the_refused_unit_leaves_no_config(self):
        """REV-mip-s34 П-5: the unit refused on a foreign table — its old config (with the key) goes, as on the other
        refusals of ``egress_awg_conf_unit``."""
        import tempfile
        d = tempfile.mkdtemp(prefix="k3-l2c-")
        self.addCleanup(shutil.rmtree, d, True)
        conf = os.path.join(d, ".kawg1.conf")
        open(conf, "w").close()
        out, err, calls = self.run_bash('AWG_DIR=%s; egress_awg_conf_unit kawg1' % d,
                                        routes="default via 85.143.0.1 dev eth0 \n")
        self.assertIn("RC:1", out)
        self.assertFalse(os.path.exists(conf))

    def test_our_own_table_goes_on_as_before(self):
        out, err, calls = self.run_bash('egress_awg_route_leg 257 257 kawg1',
                                        routes="default dev kawg1 scope link \nunreachable default metric 1000 \n",
                                        rules="4005:\tfrom all fwmark 0x101 lookup 257\n")
        self.assertIn("RC:0", out)
        self.assertEqual(err, "")
        self.assertIn("IP:route replace default dev kawg1 table 257", calls)
        out, err, calls = self.run_bash('egress_awg_route_leg 257 257', routes="", rules="")
        self.assertIn("IP:rule add fwmark 257 table 257", calls)               # an empty table: as before
        self.assertIn("IP:route replace unreachable default table 257 metric 1000", calls)


# ── the SSH menu (17 → 4) and MCP ─────────────────────────────────────────

@unittest.skipUnless(shutil.which("jq"), "нет jq")
class MenuTest(unittest.TestCase):
    STUBS = r"""
t() { printf '%s' "$1"; }
tf() { local k=$1; shift; printf '%s' "$k:$*"; }
k3_gw_head() { echo "HEAD:$1"; }
k3_gw_fold() { printf '%s\n' "$2"; }
k3_gw_pause() { :; }
k3_note_take() { :; }
k3_bad_choice() { echo "BAD"; }
k3_gw_show_warn() { [ -z "$K3_GW_WARN" ] || echo "WARN:$K3_GW_WARN"; }
k3_choose_yes() { return 1; }
k3_gw() {
    echo "CALL:$*"
    K3_GW_RC=0; K3_GW_ERR=""; K3_GW_WARN=""
    case "$1 $2" in
        "host ") K3_GW_OUT=$HOSTDOC ;;
        "addresses ") K3_GW_OUT=$ADDRS ;;
        "addresses in")
            if [ "$3" = "$REFUSE" ]; then K3_GW_RC=3; K3_GW_ERR="REFUSED"; K3_GW_OUT='{"ok":false}'
            else K3_GW_OUT='{"ok":true,"text":"DONE-IN"}'; fi ;;
        "addresses exit") K3_GW_OUT='{"ok":true,"text":"DONE-EXIT"}' ;;
        *) K3_GW_OUT='{"ok":true}' ;;
    esac
    return $K3_GW_RC
}
WHITE="" NC="" YELLOW="" GREEN="" RED=""
"""
    MIP = {"in": {"all": True, "list": [], "gone": [], "active": False}, "exits": [], "limits": [],
           "in_lines": ["Приём подключений: на всех адресах сервера."], "src_lines": [],
           "pool": [{"id": "e-00000000a1", "name": "Вена", "kind": "xray", "src": None},
                    {"id": "e-00000000b2", "name": "Рига", "kind": "awg", "src": B_IP}]}
    ADDRS = {"ok": True, "client_found": True, "addresses": [
        {"ip": A_IP, "origin": "iface", "found": True, "text": A_IP + " — основной"},
        {"ip": B_IP, "origin": "iface", "found": True, "text": B_IP + " — интерфейс eth0"}]}

    def run_menu(self, fn, keys, addrs=None, refuse=""):
        import tempfile
        d = tempfile.mkdtemp(prefix="k3-mip34-menu-")
        self.addCleanup(shutil.rmtree, d, True)
        names = ("k3_jq", "k3_term_esc", "k3_gw_name_arg", "k3_gw_host_menu", "k3_gw_in_menu", "k3_gw_src_menu")
        src = "".join(function(n) for n in names) + self.STUBS
        src += "%s <<'EOF'\n%sEOF\n" % (fn, "".join(k + "\n" for k in keys))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8", "REFUSE": refuse,
               "HOSTDOC": json.dumps({"ok": True, "host": A_IP, "auto": False}),
               "ADDRS": json.dumps(addrs if addrs is not None else dict(self.ADDRS, mip=self.MIP))}
        r = subprocess.run(["bash", "-c", src], capture_output=True, text=True, timeout=60, env=env)
        return r.stdout + r.stderr

    def test_the_two_items_of_17_4(self):
        out = self.run_menu("k3_gw_host_menu", ["5", "0", "6", "0", "0"])
        self.assertIn(" 3) GWS_HOST_OTHER\n 4) GWS_HOST_M2\n 5) GWS_HOST_IN_ALL\n 6) GWS_HOST_SRC\n 0) C_BACK", out)
        self.assertIn("HEAD:GWS_IN_TITLE", out)
        self.assertIn("HEAD:GWS_SRC_TITLE", out)
        one = dict(self.ADDRS, addresses=self.ADDRS["addresses"][:1], mip=self.MIP)
        out = self.run_menu("k3_gw_host_menu", ["0"], addrs=one)
        self.assertNotIn("GWS_HOST_IN_ALL", out)                               # one address: nothing to choose
        chosen = dict(one, mip=dict(self.MIP, **{"in": {"all": False, "list": [A_IP], "gone": [], "active": True}}))
        out = self.run_menu("k3_gw_host_menu", ["0"], addrs=chosen)
        self.assertIn(" 4) GWS_HOST_IN_ONLY:%s" % A_IP, out)                   # chosen: the way back stays

    def test_taking_connections_by_digits(self):
        out = self.run_menu("k3_gw_in_menu", ["3", "1", "4", "%s,%s" % (A_IP, B_IP), "4", "--all", "9", "0"])
        self.assertIn(" 1) GWS_IN_ALL\n 2) GWS_IN_ONLY:%s\n 3) GWS_IN_ONLY:%s\n 4) GWS_IN_MANY\n 0) C_BACK"
                      % (A_IP, B_IP), out)
        calls = [ln for ln in out.splitlines() if ln.startswith("CALL:addresses in")]
        self.assertEqual(calls, ["CALL:addresses in %s" % B_IP, "CALL:addresses in --all",
                                 "CALL:addresses in %s,%s" % (A_IP, B_IP)])     # «--all» typed: not a flag
        self.assertEqual(out.count("\nBAD\n"), 1)                              # «9» — no such item
        self.assertIn("[ERROR] GWS_IN_BAD:--all", out)                          # 3.3.7.14: words of their own
        self.assertIn("DONE-IN", out)
        out = self.run_menu("k3_gw_in_menu", ["3", "0"], refuse=B_IP)
        self.assertIn("[ERROR] REFUSED", out)

    def test_the_exit_ip_by_digits(self):
        out = self.run_menu("k3_gw_src_menu", ["1", "3", "2", "1", "0"])
        self.assertIn(" 1) GWS_SRC_ITEM_ROUTE:Вена\n", out)
        self.assertIn(" 2) GWS_SRC_ITEM_IP:Рига %s\n" % B_IP, out)
        self.assertIn(" 1) GWS_SRC_ROUTE\n 2) %s\n 3) %s\n 0) C_BACK" % (A_IP, B_IP), out)
        calls = [ln for ln in out.splitlines() if ln.startswith("CALL:addresses exit")]
        self.assertEqual(calls, ["CALL:addresses exit e-00000000a1 %s" % B_IP, "CALL:addresses exit e-00000000b2 main"])
        none = dict(self.ADDRS, mip=dict(self.MIP, pool=[]))
        self.assertIn("GWS_SRC_NONE", self.run_menu("k3_gw_src_menu", ["0"], addrs=none))


class McpTest(unittest.TestCase):
    def test_the_tools_and_their_argv(self):
        from tests.test_mcp import ACTIONS, McpCase, call
        a = ACTIONS["actions"]
        self.assertEqual((a["gateway_addresses_in"]["cmd"], a["gateway_addresses_in"]["destructive"],
                          a["gateway_addresses_in"]["confirm"]), (["gw", "addresses", "in"], True, "@tool"))  # REV З-2
        self.assertEqual(a["gateway_exit_src"]["cmd"], ["gw", "addresses", "exit"])

        class Case(McpCase):
            def runTest(self):
                pass
        c = Case()
        c.setUp()
        try:
            ok = "gateway_addresses_in"
            s = c.run_session([call(1, "gateway_addresses_in", {"ips": "%s,%s" % (A_IP, B_IP), "confirm": ok}),
                               call(2, "gateway_addresses_in", {"all": True, "confirm": ok}),
                               call(3, "gateway_exit_src", {"exit": "e-00000000a1", "ip": B_IP}),
                               call(4, "gateway_exit_src", {"group": "main", "ip": "main"}),
                               call(5, "gateway_addresses_in", {"ips": A_IP}),                  # no confirm
                               call(6, "gateway_addresses_in", {"ips": A_IP, "confirm": A_IP})])
            self.assertEqual((s.error(5)["code"], s.error(6)["code"]), (-32602, -32602))
            self.assertEqual([x["argv"] for x in s.calls],
                             [["gw", "addresses", "in", A_IP, B_IP, "--json"],
                              ["gw", "addresses", "in", "--all", "--json"],
                              ["gw", "addresses", "exit", "e-00000000a1", B_IP, "--json"],
                              ["gw", "addresses", "exit", "main", "--group=main", "--json"]])
        finally:
            c.tearDown()


if __name__ == "__main__":
    unittest.main()
