"""«Адреса сервера» — PLAN-multi-ip Ш1 (k3/handoff/briefs/PLAN-multi-ip-2026-10-29.md §2.2 С1, §3, §4 Ш1).

gw/addresses.py is new: every case here fails without it. What is checked (the table of §4, Ш1):
* the parsing of ``ip -o addr show`` — a secondary address and an alias label, a second interface, a NAT cloud's private
  address, the gateway's own (kgw*, kawg*) and the container networks (docker*, br-*, veth*) dropped;
* ``route_ok`` from ``ip route get 1.1.1.1 from <адрес>``: the same device and gateway as the default route or not;
* the NAT pairs: probed once in 10 minutes (in parallel, the route's own address first), again by ``--refresh``, never
  with ``net=False`` and never for a public address plus one private network of the hoster;
* the document and ``kaskadctl gw addresses [--json] / add / remove``; MCP ``gateway_addresses`` (read only);
* the menu header (summary.addresses: «IP: A (+N)») and the warning «адреса для клиентов на сервере нет» (вопрос 4).
"""

import json
import os
import unittest
from unittest import mock

from kaskad import errors, state, summary, sysrun
from kaskad.gw import addresses as A
from tests.fakes_gw import FakeIp
from tests.helpers import RootCase
from tests.test_gw_ops import CliGwCase

IP_ADDR = r"""1: lo    inet 127.0.0.1/8 scope host lo\       valid_lft forever preferred_lft forever
1: lo    inet6 ::1/128 scope host \       valid_lft forever preferred_lft forever
2: enp0s3    inet 85.143.0.20/32 brd 85.143.0.20 scope global enp0s3\       valid_lft forever preferred_lft forever
2: enp0s3    inet 85.143.0.10/32 brd 85.143.0.10 scope global secondary enp0s3:0\       valid_lft forever preferred_lft forever
2: enp0s3    inet6 2a01:4f8::5/64 scope global \       valid_lft forever preferred_lft forever
2: enp0s3    inet6 2a01:4f8::77/64 scope global temporary dynamic \       valid_lft 86000sec preferred_lft 14000sec
2: enp0s3    inet6 fe80::1/64 scope link \       valid_lft forever preferred_lft forever
3: eth1    inet 85.143.1.5/24 brd 85.143.1.255 scope global eth1\       valid_lft forever preferred_lft forever
4: eth2    inet 10.10.0.5/24 brd 10.10.0.255 scope global eth2\       valid_lft forever preferred_lft forever
5: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\       valid_lft forever preferred_lft forever
6: br-1a2b3c    inet 172.18.0.1/16 brd 172.18.255.255 scope global br-1a2b3c\       valid_lft forever preferred_lft forever
7: veth12ab@if6    inet 172.19.0.1/16 scope global veth12ab\       valid_lft forever preferred_lft forever
8: kgw-wg    inet 10.77.1.1/24 scope global kgw-wg\       valid_lft forever preferred_lft forever
9: kawg2    inet 10.8.0.2/32 scope global kawg2\       valid_lft forever preferred_lft forever
"""
ROUTE = "1.1.1.1 via 10.0.0.1 dev enp0s3 src 85.143.0.20 uid 0 \n    cache \n"


class Runner(object):
    """A runner of canned answers: ``answers`` — {(argv tuple prefix): (rc, out) or callable(argv) -> (rc, out)}; the
    longest prefix wins. Every call is kept."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def run(self, argv, timeout=None, **kw):
        self.calls.append(list(argv))
        best = None
        for key in self.answers:
            if tuple(argv[:len(key)]) == key and (best is None or len(key) > len(best)):
                best = key
        if best is None:
            return sysrun.Result(argv, 127, "", "not mocked")
        got = self.answers[best]
        rc, out = got(argv) if callable(got) else got
        return sysrun.Result(argv, rc, out, "")

    def curls(self):
        return [c for c in self.calls if c[0] == "curl"]


def _m(addrs_text=IP_ADDR, route=ROUTE, ways=None, nat=(), nat_at=None, seen=None):
    found = A.parse_addrs(addrs_text)
    main = A.parse_route(route)
    return {"at": 1790000000, "route": main, "addrs": found, "nat": list(nat), "nat_at": nat_at, "seen": seen or [],
            "nat_wanted": A.nat_wanted(found), "ways": ways if ways is not None else
            dict((a["ip"], dict(main, src="")) for a in found if a["v"] == 4 and A.is_public4(a["ip"]))}


class ParseTest(unittest.TestCase):
    def test_a_deprecated_ipv4_stays_a_deprecated_ipv6_not(self):
        """REV З-4: ``preferred_lft 0`` keeps a second IPv4 from becoming the route's source — it is the server's."""
        got = A.parse_addrs("2: eth0    inet 85.143.0.30/32 scope global deprecated eth0\\       valid_lft forever "
                            "preferred_lft 0sec\n2: eth0    inet6 2a01:4f8::9/64 scope global deprecated \\       "
                            "valid_lft forever preferred_lft 0sec\n")
        self.assertEqual([a["ip"] for a in got], ["85.143.0.30"])

    def test_interfaces_alias_second_interface_and_what_is_dropped(self):
        got = [(a["ip"], a["iface"], a["secondary"]) for a in A.parse_addrs(IP_ADDR)]
        self.assertEqual(got, [("85.143.0.20", "enp0s3", False), ("85.143.0.10", "enp0s3", True),   # alias enp0s3:0
                               ("2a01:4f8::5", "enp0s3", False), ("85.143.1.5", "eth1", False),
                               ("10.10.0.5", "eth2", False)])
        # the loopback, link-local, temporary IPv6, Docker, its bridges and veth, the gateway's own — never

    def test_the_way_of_an_answer(self):
        main = A.parse_route(ROUTE)
        self.assertEqual(main, {"dev": "enp0s3", "via": "10.0.0.1", "src": "85.143.0.20"})
        same = A.parse_route("1.1.1.1 from 85.143.0.10 via 10.0.0.1 dev enp0s3 uid 0 \n    cache \n")
        other = A.parse_route("1.1.1.1 from 85.143.1.5 via 85.143.1.1 dev eth1 table 100 uid 0 \n    cache \n")
        self.assertTrue(A.same_way(same, main))
        self.assertFalse(A.same_way(other, main))
        self.assertIsNone(A.same_way(None, main))
        self.assertEqual(A.route_text(other), "via 85.143.1.1 dev eth1")
        self.assertIsNone(A.parse_route("RTNETLINK answers: Network is unreachable"))

    def test_nat_is_looked_for_only_in_a_cloud(self):
        def found(*ips):
            return [{"ip": ip, "v": 4} for ip in ips]
        self.assertTrue(A.nat_wanted(found("10.0.0.5")))                       # the one address is private
        self.assertTrue(A.nat_wanted(found("85.143.0.20", "10.0.0.5", "10.0.0.6")))     # several private ones
        self.assertFalse(A.nat_wanted(found("85.143.0.20", "10.10.0.5")))     # a private network of the hoster
        self.assertFalse(A.nat_wanted(found("85.143.0.20")))
        self.assertFalse(A.nat_wanted(found("203.0.113.10")))                  # documentation: neither


class ComposeTest(unittest.TestCase):
    def test_the_primary_first_the_client_and_the_dns(self):
        st = {"gateway": {"host": "85.143.0.10", "enabled": True},
              "site": {"domain": "vpn.example.com", "dns": {"a": ["85.143.0.10"]}}}
        ways = {"85.143.0.20": {"dev": "enp0s3", "via": "10.0.0.1"}, "85.143.0.10": {"dev": "enp0s3", "via": "10.0.0.1"},
                "85.143.1.5": {"dev": "eth1", "via": "85.143.1.1"}}
        d = A.compose(_m(ways=ways), st)
        rows = [(it["ip"], it["origin"], it["primary"], it["client"], it["route_ok"], it["in_dns"]) for it in d["addresses"]]
        self.assertEqual(rows, [("85.143.0.20", "iface", True, False, True, False),
                                ("85.143.0.10", "iface", False, True, True, True),
                                ("85.143.1.5", "iface", False, False, False, False)])
        self.assertEqual((d["primary"], d["show"], d["more"], d["client_found"]), ("85.143.0.20", "85.143.0.10", 2, True))
        self.assertEqual(d["private"], [{"ip": "10.10.0.5", "iface": "eth2"}])
        self.assertEqual(d["v6"], ["2a01:4f8::5"])
        text = "\n".join(A.lines(d))
        self.assertIn("85.143.1.5 — интерфейс eth1 · A-записи домена на него нет · ответ с него уходит через via 85.143.1.1 "
                      "dev eth1", text)
        self.assertIn("85.143.0.20 — интерфейс enp0s3 · основной (маршрут по умолчанию)", text)
        # 3.3.7.15 F10 (PLAN-multi-ip-roles §9.1, «было до правки»): Reality IPv6 не слушает, WG/AWG, OpenConnect и IKEv2 — да
        self.assertIn("IPv6: 2a01:4f8::5 — по нему принимают подключения VLESS TLS, Hysteria2, WireGuard, AmneziaWG, "
                      "OpenConnect и IKEv2; VLESS Reality, SOCKS5, L2TP и PPTP — только по IPv4", text)
        self.assertIsNone(A.gone_problem(d, st))
        # T-mip-lab Н6: the hoster made the bought address primary — the address for clients is here, but not the one
        # the server goes online from: said, nothing moved
        self.assertIs(d["client_primary"], False)
        self.assertIn("! Адрес для клиентов 85.143.0.10 — не основной: в интернет сервер выходит с 85.143.0.20", text)
        same = A.compose(_m(ways=ways), dict(st, gateway={"host": "85.143.0.20"}))
        self.assertIs(same["client_primary"], True)
        self.assertNotIn("не основной", "\n".join(A.lines(same)))
        self.assertIsNone(A.compose(_m(ways=ways), {"gateway": {"host": "vpn.example.com"}})["client_primary"])
        # REV З-1: the same, but the address was chosen by hand — a choice, not a fault: no line, no false
        chosen = A.compose(_m(ways=ways), dict(st, gateway={"host": "85.143.0.10", "host_auto": False}))
        self.assertIsNone(chosen["client_primary"])
        self.assertNotIn("не основной", "\n".join(A.lines(chosen)))

    def test_the_client_address_gone(self):
        st = {"gateway": {"host": "85.143.0.99", "enabled": True}}
        d = A.compose(_m(), st)
        gone = [it for it in d["addresses"] if it["origin"] == "host"]
        self.assertEqual([(it["ip"], it["found"], it["client"]) for it in gone], [("85.143.0.99", False, True)])
        self.assertIs(d["client_found"], False)
        self.assertIsNone(d["client_primary"])                                  # «not here» is said, not both
        # REV З-2: the server may not see it at all (an external address of a cloud) — both ways are named
        text = "\n".join(A.lines(d))
        self.assertIn("Сервер не видит у себя адрес для клиентов 85.143.0.99", text)
        self.assertIn("kaskadctl gw addresses add 85.143.0.99", text)
        self.assertIn("Если адрес пропал", text)
        self.assertEqual(d["show"], "85.143.0.20")                             # the primary one, not the gone one
        self.assertFalse(A.known(d, "85.143.0.99"))
        self.assertIs(d["client_seen"], False)
        self.assertIsNone(A.gone_problem(d, st))            # З-2: never seen here (a cloud's address) — not «gone»
        d = A.compose(_m(seen=["85.143.0.99", "85.143.0.10"]), st)
        self.assertIs(d["client_seen"], True)
        self.assertIn("85.143.0.99", A.gone_problem(d, st))                    # it was here once: gone
        self.assertIsNone(A.gone_problem(d, {"gateway": {"host": "85.143.0.99"}}))   # off and nobody has it
        self.assertIsNone(A.compose(_m(), {"gateway": {"host": "vpn.example.com"}})["client_found"])   # a domain

    def test_nat_pairs_manual_and_nothing_known(self):
        text = ("2: ens5    inet 10.0.0.5/24 scope global ens5\\\n"
                "2: ens5    inet 10.0.0.6/24 scope global secondary ens5\\\n")
        m = _m(text, "1.1.1.1 via 10.0.0.1 dev ens5 src 10.0.0.5 uid 0", ways={},
               nat=[{"local": "10.0.0.5", "ip": "85.143.0.20"}, {"local": "10.0.0.9", "ip": "85.143.0.29"}])
        st = {"gateway": {"addresses": {"manual": [{"ip": "93.184.216.34", "local": "10.0.0.6"},
                                                   {"ip": "93.184.216.35"}, {"ip": "10.1.2.3"}, "x"]}}}
        d = A.compose(m, st)
        self.assertEqual([(it["ip"], it["origin"], it["local"], it["found"], it["primary"]) for it in d["addresses"]],
                         [("85.143.0.20", "nat", "10.0.0.5", True, True),     # the pair of a gone local is dropped
                          ("93.184.216.34", "manual", "10.0.0.6", True, False),
                          ("93.184.216.35", "manual", None, None, False)])    # a private «manual» one is not taken
        self.assertEqual(d["private"], [{"ip": "10.0.0.6", "iface": "ens5"}])
        self.assertEqual(A.listen_extra(st, ["10.0.0.5", "10.0.0.6"]), ["10.0.0.6"])
        self.assertEqual(A.compose(None, {})["addresses"], [])


class DiscoverTest(RootCase):
    def setUp(self):
        super().setUp()
        self.now = 1790000000.0
        p = mock.patch.object(A, "_now", lambda: self.now)
        p.start()
        self.addCleanup(p.stop)
        self.cloud = Runner({
            ("ip", "-o", "addr", "show"): (0, "2: ens5    inet 10.0.0.6/24 scope global secondary ens5\\\n"
                                              "2: ens5    inet 10.0.0.5/24 scope global ens5\\\n"),
            ("ip", "-4", "route", "get", "1.1.1.1"): (0, "1.1.1.1 via 10.0.0.1 dev ens5 src 10.0.0.5 uid 0 \n"),
            ("ip", "-4", "route", "get", "1.1.1.1", "from"): (0, "1.1.1.1 via 10.0.0.1 dev ens5 uid 0 \n"),
            ("curl",): lambda argv: (0, {"10.0.0.5": "85.143.0.20", "10.0.0.6": "85.143.0.21"}[
                argv[argv.index("--interface") + 1]])})

    def test_the_pairs_of_a_cloud_once_in_ten_minutes(self):
        d = A.collect(self.cloud, {})
        self.assertEqual([(it["ip"], it["local"], it["origin"], it["primary"], it["route_ok"]) for it in d["addresses"]],
                         [("85.143.0.20", "10.0.0.5", "nat", True, True), ("85.143.0.21", "10.0.0.6", "nat", False, True)])
        self.assertEqual(sorted(c[c.index("--interface") + 1] for c in self.cloud.curls()), ["10.0.0.5", "10.0.0.6"])
        self.assertTrue(all("https://api.ipify.org" in c and "--max-time" in c for c in self.cloud.curls()))
        self.assertTrue(os.path.exists(A.cache_path()))
        self.assertEqual(oct(os.stat(A.cache_path()).st_mode & 0o777), "0o600")
        self.now += 300
        self.assertEqual(A.collect(self.cloud, {})["show"], "85.143.0.20")
        self.assertEqual(len(self.cloud.curls()), 2)                            # the pairs of the cache, no probe
        A.collect(self.cloud, {}, refresh=True)
        self.assertEqual(len(self.cloud.curls()), 4)                            # --refresh probes again
        self.now += 3600
        d = A.collect(self.cloud, {}, net=False)                                # stale, but no network
        self.assertEqual(len(self.cloud.curls()), 4)
        self.assertEqual([it["ip"] for it in d["addresses"]], ["85.143.0.20", "85.143.0.21"])   # the last pairs kept

    def test_without_the_network_first_then_the_pairs(self):
        d = A.collect(self.cloud, {}, net=False)
        self.assertEqual((d["addresses"], d["nat_wanted"], d["nat_at"]), ([], True, None))
        self.assertIn("ещё не проверялись", "\n".join(A.lines(d)))
        self.assertEqual(self.cloud.curls(), [])
        d = A.collect(self.cloud, {})                       # the cache is fresh, but its NAT was never looked at
        self.assertEqual(len(d["addresses"]), 2)
        self.cloud.answers[("ip", "-o", "addr", "show")] = (0, "2: ens5    inet 10.0.0.5/24 scope global ens5\\\n")
        d = A.collect(self.cloud, {}, net=False)            # an address gone is gone at once, with its pair
        self.assertEqual([it["ip"] for it in d["addresses"]], ["85.143.0.20"])

    def test_two_locals_behind_one_public_and_a_failed_probe(self):
        self.cloud.answers[("curl",)] = lambda argv: (0, "85.143.0.20") if "10.0.0.5" in argv else (0, "85.143.0.20")
        self.assertEqual([it["ip"] for it in A.collect(self.cloud, {})["addresses"]], ["85.143.0.20"])
        self.cloud.answers[("curl",)] = (28, "")
        d = A.collect(self.cloud, {}, refresh=True)                 # no answer: the pair found before stays (REV С-2)
        self.assertEqual(([it["ip"] for it in d["addresses"]], [p["ip"] for p in d["private"]]),
                         (["85.143.0.20"], ["10.0.0.6"]))

    def test_a_probe_without_an_answer_keeps_the_pairs(self):
        """REV С-2: ipify not answering once (from a node in Russia — often) never makes «адреса для клиентов нет»."""
        st = {"gateway": {"host": "85.143.0.21", "enabled": True}}
        d = A.collect(self.cloud, st)
        self.assertIs(d["client_found"], True)
        self.cloud.answers[("curl",)] = (28, "")
        self.now += A.FRESH + 1
        d = A.collect(self.cloud, st)
        self.assertEqual(len(self.cloud.curls()), 4)                           # it was probed again
        self.assertEqual([(it["ip"], it["local"]) for it in d["addresses"]],
                         [("85.143.0.20", "10.0.0.5"), ("85.143.0.21", "10.0.0.6")])
        self.assertIs(d["client_found"], True)
        self.assertIsNone(A.gone_problem(d, st))
        A.check_known(self.cloud, "85.143.0.21", st)                            # no refusal either
        self.cloud.answers[("curl",)] = lambda argv: (0, {"10.0.0.5": "85.143.0.20", "10.0.0.6": "85.143.0.29"}[
            argv[argv.index("--interface") + 1]])
        d = A.collect(self.cloud, st, refresh=True)                             # an answer replaces a pair
        self.assertEqual([it["ip"] for it in d["addresses"] if it["origin"] == "nat"], ["85.143.0.20", "85.143.0.29"])

    def test_a_public_address_and_a_private_network_never_probe(self):
        r = Runner({("ip", "-o", "addr", "show"): (0, IP_ADDR), ("ip", "-4", "route", "get"): (0, ROUTE),
                    ("curl",): (0, "85.143.0.99")})
        d = A.collect(r, {}, refresh=True)
        self.assertEqual(r.curls(), [])
        self.assertEqual([it["ip"] for it in d["addresses"]], ["85.143.0.20", "85.143.0.10", "85.143.1.5"])

    def test_ip_failing_is_an_error(self):
        with self.assertRaises(errors.KaskadError):
            A.collect(Runner({}), {})


class CliTest(CliGwCase):
    def node(self, ip="85.143.0.10", extra=("85.143.0.11/32",)):
        self.w.ip = FakeIp(node_ip=ip, extra_addrs=list(extra))
        self.w.runner._handlers = [h for h in self.w.runner._handlers if h[0] != ["ip"]]
        self.w.runner.on(["ip"], self.w.ip)
        self.w.ip.add_link("kgw-wg", "wireguard", ["10.77.1.1/24"])
        self.w.ip.add_link("docker0", "bridge", ["172.17.0.1/16"])
        self.w.ip.add_link("eth1", "ether", ["10.10.0.5/24"])

    def test_gw_addresses(self):
        from tests.fakes_nft import save_state
        save_state()
        self.node()
        rc, doc, _ = self.run_json("gw", "addresses")
        self.assertEqual(rc, 0)
        self.assertEqual([(a["ip"], a["origin"], a["primary"]) for a in doc["addresses"]],
                         [("85.143.0.10", "iface", True), ("85.143.0.11", "iface", False)])
        self.assertEqual(doc["addresses"][0]["text"], "85.143.0.10 — интерфейс eth0 · основной (маршрут по умолчанию)")
        self.assertEqual((doc["show"], doc["more"], doc["private"]), ("85.143.0.10", 1, [{"ip": "10.10.0.5", "iface": "eth1"}]))
        out, _ = self.ok("gw", "addresses")
        self.assertIn("Адреса сервера\n  85.143.0.10 — интерфейс eth0 · основной (маршрут по умолчанию)\n  85.143.0.11 — интерфейс eth0\n", out)
        self.assertEqual([c for c in self.w.curl.calls if "--interface" in c], [])

    def test_add_and_remove_by_hand(self):
        from tests.fakes_nft import save_state
        save_state()
        self.node()
        _, doc, _ = self.run_json("gw", "addresses", "add", "93.184.216.34")
        self.assertTrue(doc["added"])
        self.assertEqual(state.load()["gateway"]["addresses"], {"manual": [{"ip": "93.184.216.34"}]})
        self.ok("gw", "addresses", "add", "93.184.216.34", "--local", "10.10.0.5")          # again: replaced, one entry
        self.assertEqual(state.load()["gateway"]["addresses"]["manual"], [{"ip": "93.184.216.34", "local": "10.10.0.5"}])
        rc, doc, _ = self.run_json("gw", "addresses", "add", "93.184.216.35", "--local", "10.9.9.9")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertIn("Адреса 10.9.9.9 на сервере нет", doc["error"])
        _, doc, _ = self.run_json("gw", "addresses", "add", "85.143.0.11")                   # found already
        self.assertFalse(doc["added"])
        for bad in ("10.1.2.3", "203.0.113.7", "vpn.example.com"):
            rc, doc, _ = self.run_json("gw", "addresses", "add", bad)
            self.assertEqual(rc, errors.ERROR, bad)
        _, doc, _ = self.run_json("gw", "addresses")
        self.assertEqual([(a["ip"], a["origin"], a["found"]) for a in doc["addresses"]][-1],
                         ("93.184.216.34", "manual", True))
        st = state.load()
        st["gateway"]["host"] = "93.184.216.34"
        state.save(st)
        rc, doc, _ = self.run_json("gw", "addresses", "remove", "93.184.216.34")   # REV П-1: the address for clients
        self.assertEqual(rc, errors.CONFLICT)
        self.assertIn("Сначала выберите другой адрес для клиентов", doc["error"])
        self.ok("gw", "host", "85.143.0.11")
        self.ok("gw", "addresses", "remove", "93.184.216.34")
        self.assertEqual(state.load()["gateway"]["addresses"]["manual"], [])
        rc, doc, _ = self.run_json("gw", "addresses", "remove", "93.184.216.34")
        self.assertEqual(rc, errors.ERROR)
        self.assertIn("Адреса сервера: добавлен вручную 93.184.216.34", self.log())

    def test_the_header_and_the_gone_client_address(self):
        from tests.fakes_nft import save_state
        save_state()
        self.node()
        self.assertIsNone(summary.summary()["addresses"])                  # nothing looked at yet: the menu falls back
        self.ok("gw", "addresses")
        self.assertEqual(summary.summary()["addresses"],
                         {"show": "85.143.0.10", "more": 1, "client_found": None, "count": 2})
        st = state.load()
        st.setdefault("gateway", {}).update(host="85.143.0.99", enabled=True)
        state.save(st)
        doc = summary.summary()
        self.assertEqual(doc["addresses"]["client_found"], False)
        self.assertEqual([p for p in doc["problems"] if p["kind"] == "gw_host_gone"], [])   # З-2: never seen here
        cache = A.load_cache()
        self.assertEqual(cache["seen"], ["85.143.0.10", "85.143.0.11"])
        A.save_cache(dict(cache, seen=["85.143.0.99"] + cache["seen"]))     # it was on the machine once
        doc = summary.summary()
        gone = [p for p in doc["problems"] if p["kind"] == "gw_host_gone"]
        self.assertEqual([(p["item"], p["source"]) for p in gone], [(17, "rule")])
        self.assertIn("85.143.0.99", gone[0]["detail"])
        self.assertIn("17 → 4", gone[0]["advice"])


class McpTest(unittest.TestCase):
    def test_read_only_tool_and_its_argv(self):
        from tests.test_mcp import ACTIONS
        t = ACTIONS["actions"]["gateway_addresses"]
        self.assertEqual((t["cmd"], t["readonly"], t["destructive"]), (["gw", "addresses"], True, False))
        self.assertEqual(t["params"], {"refresh": {"ref": "addr_refresh", "flag": "--refresh"}})
        for k in ("gw addresses add", "gw addresses remove"):                   # writing by hand — not for an agent yet
            self.assertIn(k, ACTIONS["hidden"])


class McpCallTest(unittest.TestCase):
    def test_argv(self):
        from tests.test_mcp import McpCase, call

        class Case(McpCase):
            def runTest(self):
                pass
        c = Case()
        c.setUp()
        try:
            s = c.run_session([call(1, "gateway_addresses", {"refresh": True}), call(2, "gateway_addresses", {}),
                               call(3, "gateway_set_host", {"host": "85.143.0.11", "force": True,
                                                            "confirm": "85.143.0.11"})])
            self.assertEqual([x["argv"] for x in s.calls], [["gw", "addresses", "--refresh", "--json"],
                                                             ["gw", "addresses", "--json"],
                                                             ["gw", "host", "85.143.0.11", "--force", "--json"]])
        finally:
            c.tearDown()


if __name__ == "__main__":
    unittest.main()
