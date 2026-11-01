"""Hysteria2 inbound, the pieces (SPEC-stage1-hy2-inbound §3, §7): the range of port hopping (parse, one UFW rule,
the neighbours, one ``ss``), the hot contract of T5 (``h2-``, gRPC account), ``listen`` by IPv6, the inbound with and
without Salamander and with the range through the real ``xray -test``, the link (fields, the pin only with the
self-signed certificate, the label of the inbound never in it), the journal hides the auth and the Salamander password."""

import copy
import os
import unittest
from urllib.parse import parse_qs, urlsplit

from kaskad import conflicts, paths, util
from kaskad.gw import registry, xray, xraygrpc
from kaskad.gw import sys as gsys
from kaskad.gw.inbounds import hy2 as hin
from kaskad.gw.inbounds import tunnel
from kaskad.gw.inbounds.base import InboundError
from kaskad.sysrun import FakeRunner
from tests import realbin
from tests.helpers import RootCase

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "gw", "tunnel")
with open(os.path.join(FIX, "ocserv.crt"), encoding="utf-8") as _f:
    CRT = _f.read()
KEY = "-----BEGIN PRIVATE KEY-----\nK\n-----END PRIVATE KEY-----\n"
AUTH = "A" * 32
OBFS = "B" * 32


def state(**rec):
    r = {"enabled": True, "port": 443, "hop": None, "obfs": True, "cert": "domain", "cert_mode": "self",
         "congestion": "bbr", "pin": hin.pin_of(CRT), "label": "Для мамы"}
    r.update(rec)
    return {"engine": "nft", "gateway": {"enabled": True, "host": "203.0.113.10", "inbounds": {"hy2": r},
                                         "users": [{"id": "u-1", "name": "Мама", "enabled": True}],
                                         "devices": [{"id": "d-1", "user": "u-1", "name": "Телефон", "login": "tel-1",
                                                      "platform": "android", "enabled": True, "access": ["hy2"]}],
                                         "routes": {"default": {"via": "direct", "fail": "block"}}}}


SEC = {hin.OBFS_KEY: OBFS, hin.SELF_CRT: CRT, hin.SELF_KEY: KEY, hin.auth_key("d-1"): AUTH}


class HopTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(hin.parse_hop("20000-20099"), "20000-20099")
        self.assertEqual(hin.parse_hop(" 20000 : 20099 "), "20000-20099")
        self.assertEqual(hin.parse_hop("20000–20099"), "20000-20099")
        for off in ("", "off", None, "0"):
            self.assertIsNone(hin.parse_hop(off))
        for bad in ("20000-20100", "100-200", "20099-20000", "x", "20000-70000"):
            with self.assertRaises(InboundError):
                hin.parse_hop(bad)
        self.assertIsNone(hin.hop_bounds("garbage"))                    # a hand edit: no range, never a crash

    def test_ports_for_checks_and_for_people(self):
        ad = registry.get("hy2")
        st = state(hop="20000-20099")
        self.assertEqual(len(ad.ports(st)), 101)
        self.assertIn(("udp", 20050), ad.ports(st))
        self.assertEqual(ad.ports_view(st), [("udp", "443"), ("udp", "20000–20099")])
        self.assertEqual(ad.ufw_want(st), [(443, "udp"), ("20000:20099", "udp")])
        st["gateway"]["inbounds"]["hy2"]["enabled"] = False             # off: the neighbours still see the range
        self.assertEqual(len(ad.ports(st)), 101)
        self.assertEqual(ad.ufw_want(st), [])


class UfwRangeTest(unittest.TestCase):
    def test_one_rule_of_the_range(self):
        self.assertEqual(gsys._rule_spec("20000:20099", "udp"), ["20000:20099/udp"])
        self.assertEqual(gsys._rule_spec(443, "udp"), ["443/udp"])
        self.assertEqual(tunnel._ufw_item("20000:20099", "udp"), ("20000:20099", "udp"))
        self.assertEqual(tunnel._spec(("20000:20099", "udp")), "20000:20099/udp")
        items = tunnel._ufw_items([["20000:20099", "udp"], [443, "udp"], [None, "esp"], ["9:1", "udp"]])
        self.assertEqual(items, [(443, "udp"), ("20000:20099", "udp"), (None, "esp")])      # a broken one is dropped


class ConflictsOnceTest(RootCase):
    def test_one_ss_for_the_whole_range(self):
        r = FakeRunner(binaries=("ss",))
        ss = {"out": 'udp UNCONN 0 0 0.0.0.0:20005 0.0.0.0:* users:(("wg-go",pid=9,fd=3))\n'}
        r.on(["ss"], lambda a, i: (0, ss["out"], ""))
        got = conflicts.check_many("udp", range(20000, 20100), r, env={}, st={})
        self.assertEqual(sorted(got), [20005])
        self.assertEqual(got[20005][0]["kind"], "listener")
        self.assertEqual(len(r.commands(["ss"])), 1)
        # the single check is the same as before
        self.assertEqual(conflicts.check("udp", 20005, r, env={}, st={}), got[20005])
        # SEC З-2: a listener on a loopback address counts for the range (xray binds every address)
        ss["out"] = 'udp UNCONN 0 0 127.0.0.1:20007 0.0.0.0:* users:(("dnsmasq",pid=7,fd=3))\n'
        self.assertEqual(sorted(conflicts.check_many("udp", range(20000, 20010), r, env={}, st={}, local=True)), [20007])
        self.assertEqual(conflicts.check_many("udp", range(20000, 20010), r, env={}, st={}), {})


class HotContractTest(unittest.TestCase):
    def test_prefix_and_account(self):
        ad = registry.get("hy2")
        self.assertIn("h2-", xray.hot_prefixes())
        self.assertEqual((ad.xray_hot, ad.xray_hot_prefix, ad.xray_add_user, ad.xray_stats), (True, "h2-", "grpc", True))
        self.assertEqual(ad.xray_account({"auth": AUTH, "email": "tel-1.h2"}),
                         ("xray.proxy.hysteria.account.Account", xraygrpc.field(1, AUTH)))
        self.assertEqual(ad.xray_user({"login": "tel-1"}), "tel-1.h2")

    def test_a_new_device_is_hot_a_new_secret_restarts(self):
        ad = registry.get("hy2")
        st = state()
        base = {"api": {"services": ["HandlerService", "RoutingService"]}, "outbounds": [{"tag": "block"}],
                "routing": {"rules": []}}
        old = dict(copy.deepcopy(base), inbounds=[dict(ad.xray_inbounds(st, {k: v for k, v in SEC.items()
                                                                                if not k.startswith("dev.")})[0])])
        new = dict(copy.deepcopy(base), inbounds=ad.xray_inbounds(st, SEC))
        new["routing"]["rules"] = ad.xray_routing(st, SEC, lambda route, d=None: {"outboundTag": "direct"})
        plan = xray.hot_plan(old, new, [hin.TAG], modes={hin.TAG: "grpc"})
        self.assertIsNotNone(plan)
        self.assertEqual([(t, c["email"]) for t, c in plan["add_users"]], [(hin.TAG, "tel-1.h2")])
        self.assertTrue(all(r["ruleTag"].startswith("h2-tel-1-") for r in plan["add_rules"]))
        sec2 = dict(SEC, **{hin.auth_key("d-1"): "C" * 32})
        newer = dict(copy.deepcopy(new), inbounds=ad.xray_inbounds(st, sec2))
        self.assertIsNone(xray.hot_plan(new, newer, [hin.TAG], modes={hin.TAG: "grpc"}))   # its QUIC must go


class InboundTest(RootCase):
    def ib(self, **rec):
        return registry.get("hy2").xray_inbounds(state(**rec), SEC)[0]

    def test_listen_by_ipv6(self):
        self.assertEqual(self.ib()["listen"], "0.0.0.0")
        p = paths.P("/proc/net/if_inet6")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("00000000000000000000000000000001 01 80 10 80       lo\n")
        self.assertEqual(self.ib()["listen"], "::")

    def test_shapes_go_through_the_real_xray(self):
        if not realbin.xray_path():
            self.skipTest("no real xray (tools/fetch_test_bins.sh)")
        for rec in ({}, {"obfs": False}, {"hop": "20000-20009"}):
            ib = self.ib(**rec)
            cfg = {"inbounds": [ib], "outbounds": [{"tag": "block", "protocol": "blackhole"},
                                                    {"tag": "direct", "protocol": "freedom"}],
                   "routing": {"rules": registry.get("hy2").xray_routing(
                       state(**rec), SEC, lambda route, d=None: {"outboundTag": "direct"})}}
            ok, text = realbin.xray_check(cfg)
            self.assertTrue(ok, (rec, text))
        self.assertNotIn("udp", self.ib(obfs=False)["streamSettings"]["finalmask"])
        self.assertEqual(self.ib(hop="20000-20009")["port"], "443,20000-20009")
        self.assertEqual(self.ib()["settings"]["clients"], [{"auth": AUTH, "level": 0, "email": "tel-1.h2"}])

    def test_golden(self):
        import json
        from tests.gw.tunnel_case import GOLDEN
        text = json.dumps(registry.get("hy2").xray_inbounds(state(hop="20000-20099"), SEC), indent=1, sort_keys=True,
                          ensure_ascii=False).replace(self.root, "") + "\n"
        path = os.path.join(GOLDEN, "hy2-xray-inbounds.json")
        if os.environ.get("KASKAD_GOLDEN_UPDATE"):
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(text, f.read(), "golden hy2-xray-inbounds.json (KASKAD_GOLDEN_UPDATE=1 rewrites it)")

    def test_no_inbound_without_the_pair(self):
        sec = dict(SEC)
        del sec[hin.SELF_KEY]
        self.assertEqual(registry.get("hy2").xray_inbounds(state(), sec), [])


class LinkTest(RootCase):
    def link(self, **rec):
        st = state(**rec)
        ad = registry.get("hy2")
        return ad.link(st, SEC, st["gateway"]["devices"][0], AUTH)

    def test_the_link_and_the_journal(self):
        url = self.link(hop="20000-20099")
        u = urlsplit(url)
        q = dict((k, v[0]) for k, v in parse_qs(u.query).items())
        self.assertTrue(url.startswith("hysteria2://%s@203.0.113.10:443,20000-20099/?" % AUTH))
        self.assertEqual(q, {"sni": "203.0.113.10", "obfs": "salamander", "obfs-password": OBFS, "insecure": "1",
                             "pinSHA256": hin.pin_of(CRT)})
        self.assertNotIn("мамы", url.lower())
        self.assertNotIn("%D0%94%D0%BB%D1%8F", url)                     # the label of the inbound — never in the link
        red = util.redact("issued " + url)
        self.assertNotIn(AUTH, red)
        self.assertNotIn(OBFS, red)
        self.assertNotIn("obfs-password", self.link(obfs=False))

    def test_the_domain_has_no_pin(self):
        st = state(cert_mode="domain")
        st["site"] = {"domain": "example.com", "mode": "domain", "cert": "letsencrypt"}
        url = registry.get("hy2").link(st, SEC, st["gateway"]["devices"][0], AUTH)
        q = parse_qs(urlsplit(url).query)
        self.assertTrue(url.startswith("hysteria2://%s@example.com:443/?" % AUTH))
        self.assertEqual(q["sni"], ["example.com"])
        self.assertNotIn("insecure", q)
        self.assertNotIn("pinSHA256", q)

    def test_the_pin_is_lower_hex_of_the_der(self):
        self.assertRegex(hin.pin_of(CRT), r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
