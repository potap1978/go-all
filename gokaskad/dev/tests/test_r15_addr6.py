"""3.3.7.15 F10 — «Адреса сервера» с IPv6 (k3/handoff/SPEC-R1-3315-2026-10-30.md §3 F10; PLAN-multi-ip-roles §9.4).

What is checked (the «Тесты» of F10):
* ``parse_addrs6`` of real ``ip -o addr`` lines: SLAAC ``dynamic``, stable-privacy, ``temporary``, ``deprecated``,
  ``dadfailed``, ``mngtmpaddr`` — the fields of ``addresses6``; a deprecated IPv6 is KEPT with its mark (before F10 it
  was dropped: the filter of IPv6 must not cut the devices sitting on it);
* ``route_ok6`` by ``ip -6 route get`` with a default IPv6 route and without; ``ipv6.disable=1`` — no ``addresses6``
  rows, no ``ip -6`` command, no error; the echo of IPv6 answering another address — ``echo6`` ≠ ip; the echo probed
  at most once in FRESH s, in parallel with the NAT probes, never with ``net=False``;
* ``addresses`` and ``v6`` of the same samples as 3.3.7.14 — the module of the tree fc5ef683 (``git show``) gives the
  same document (and reads a new cache without an error: the rollback);
* the prefix: A deprecated + B of another /64 — an event; only deprecated without a new one — none; the collector puts
  it into the feed once (``_ip6_round``), the words of the event in RU and EN;
* the page model (codes, never words) and ``GET /api/gw/addresses`` (``addresses6``, ``page``, ``ver``).

2001:db8::/32 is not ``is_global``: ``is_public6`` takes it here (as 198.18.0.0/15 for IPv4 in SPEC-multi-ip §7.1)."""

import importlib.util
import ipaddress
import json
import os
import subprocess
import sys
import types
import unittest
from unittest import mock

from kaskad import sysrun
from kaskad.gw import addresses as A
from kaskad.gw import collect, live
from tests.helpers import RootCase

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REV14 = "fc5ef683"                                     # 3.3.7.14 (dev-14, the base of 3.3.7.15)
DOC6 = ipaddress.IPv6Network("2001:db8::/32")


def public6(ip):
    """``is_global`` plus the documentation range the tests live on."""
    a = A._v6(ip)
    return a is not None and (a.is_global or a in DOC6)


def doc6(case):
    p = mock.patch.object(A, "is_public6", public6)
    p.start()
    case.addCleanup(p.stop)


IP4 = (r"2: eth0    inet 85.143.0.20/32 brd 85.143.0.20 scope global eth0\       valid_lft forever preferred_lft "
       "forever\n")
# eth0: SLAAC (dynamic mngtmpaddr), its privacy twin (temporary), a deprecated one of the old prefix, a failed DAD, a
# static one; eth1: stable-privacy, a ULA (not global), link-local; the loopback and a container — never
IP6 = r"""1: lo    inet6 ::1/128 scope host noprefixroute \       valid_lft forever preferred_lft forever
2: eth0    inet6 2001:db8:10::5/64 scope global dynamic mngtmpaddr noprefixroute \       valid_lft 86313sec preferred_lft 14313sec
2: eth0    inet6 2001:db8:10:0:a1b2:c3d4:e5f6:1234/64 scope global temporary dynamic \       valid_lft 86313sec preferred_lft 14313sec
2: eth0    inet6 2001:db8:20::5/64 scope global deprecated dynamic mngtmpaddr \       valid_lft 7100sec preferred_lft 0sec
2: eth0    inet6 2001:db8:10::99/64 scope global tentative dadfailed \       valid_lft forever preferred_lft forever
2: eth0    inet6 2001:db8:10:0:0:0:0:7/64 scope global \       valid_lft forever preferred_lft forever
2: eth0    inet6 fe80::1/64 scope link \       valid_lft forever preferred_lft forever
3: eth1    inet6 2001:db8:30::a3f/64 scope global dynamic stable-privacy \       valid_lft 2591998sec preferred_lft 604798sec
3: eth1    inet6 fd00::5/64 scope global \       valid_lft forever preferred_lft forever
5: docker0    inet6 2001:db8:99::1/64 scope global \       valid_lft forever preferred_lft forever
"""
ROUTE4 = "1.1.1.1 via 85.143.0.1 dev eth0 src 85.143.0.20 uid 0 \n    cache \n"
ROUTE6 = "2606:4700:4700::1111 from :: via fe80::1 dev eth0 proto ra src 2001:db8:10::5 metric 1024 pref medium\n"
ROUTE6_ETH1 = "2606:4700:4700::1111 from 2001:db8:30::a3f via fe80::9 dev eth1 proto ra metric 1024 pref medium\n"
NO_ROUTE = (2, "")


class Runner(object):
    """Canned answers: {argv prefix: (rc, out) | callable(argv)}; the longest prefix wins; every call kept."""

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

    def which(self, name):
        return None

    def of(self, *prefix):
        return [c for c in self.calls if tuple(c[:len(prefix)]) == prefix]


def from6(table, default=ROUTE6):
    """``ip -6 route get … from X`` answers: ``table`` {X: text | NO_ROUTE}, else ``default``."""
    def answer(argv):
        x = argv[argv.index("from") + 1]
        got = table.get(x, default)
        return got if isinstance(got, tuple) else (0, got)
    return answer


def echo6(seen):
    def answer(argv):
        x = argv[argv.index("--interface") + 1]
        return (0, seen[x]) if x in seen else (28, "")
    return answer


def machine(addr=IP4 + IP6, route6=(0, ROUTE6), ways6=None, seen=None):
    return Runner({("ip", "-o", "addr", "show"): (0, addr), ("ip", "-4", "route", "get", "1.1.1.1"): (0, ROUTE4),
                   ("ip", "-4", "route", "get", "1.1.1.1", "from"): (0, ROUTE4),
                   ("ip", "-6", "route", "get", A.PROBE_DST6): route6,
                   ("ip", "-6", "route", "get", A.PROBE_DST6, "from"): from6(ways6 or {"2001:db8:30::a3f": ROUTE6_ETH1}),
                   ("curl", "-s", "-6"): echo6(seen or {}), ("curl", "-s", "-4"): (28, "")})


class ParseTest(unittest.TestCase):
    def setUp(self):
        doc6(self)

    def test_the_fields_and_what_is_dropped(self):
        got = A.parse_addrs6(IP6)
        self.assertEqual(got, [
            {"ip": "2001:db8:10::5", "iface": "eth0", "prefix": 64, "deprecated": False, "dynamic": True,
             "valid_lft": 86313},
            {"ip": "2001:db8:20::5", "iface": "eth0", "prefix": 64, "deprecated": True, "dynamic": True,
             "valid_lft": 7100},                                                # kept: it takes connections until 7100 s
            {"ip": "2001:db8:10::7", "iface": "eth0", "prefix": 64, "deprecated": False, "dynamic": False,
             "valid_lft": None},                                                # static; the canonical form
            {"ip": "2001:db8:30::a3f", "iface": "eth1", "prefix": 64, "deprecated": False, "dynamic": True,
             "valid_lft": 2591998}])                                            # stable-privacy — a usual one
        # temporary, tentative/dadfailed, link-local, ULA, the loopback, docker0 — never; IPv4 lines — not here
        self.assertEqual(A.parse_addrs6(IP4), [])

    def test_the_ipv4_parser_and_v6_are_those_of_3_3_7_14(self):
        """``parse_addrs`` is left as it is: the deprecated and the temporary IPv6 stay out of ``addrs`` and ``v6``."""
        found = A.parse_addrs(IP4 + IP6)
        self.assertEqual([a["ip"] for a in found if a["v"] == 6],
                         ["2001:db8:10::5", "2001:db8:10::7", "2001:db8:30::a3f", "fd00::5"])
        self.assertEqual([a["ip"] for a in found if a["v"] == 4], ["85.143.0.20"])


class DiscoverTest(RootCase):
    def setUp(self):
        super().setUp()
        doc6(self)
        self.now = 1790000000.0
        p = mock.patch.object(A, "_now", lambda: self.now)
        p.start()
        self.addCleanup(p.stop)

    def test_the_way_out_with_and_without_a_default_route(self):
        r = machine(seen={"2001:db8:10::5": "2001:db8:10::5", "2001:db8:10::7": "2001:db8:10::5"})
        m = A.machine(r)
        rows = dict((a["ip"], a) for a in m["addresses6"])
        self.assertEqual(dict((ip, a["route_ok6"]) for ip, a in rows.items()),
                         {"2001:db8:10::5": True, "2001:db8:20::5": True, "2001:db8:10::7": True,
                          "2001:db8:30::a3f": False})                           # eth1 answers by another way
        self.assertEqual(rows["2001:db8:10::5"]["echo6"], "2001:db8:10::5")
        self.assertEqual(rows["2001:db8:10::7"]["echo6"], "2001:db8:10::5")     # «наружу виден как …»: not given out
        self.assertIsNone(rows["2001:db8:30::a3f"]["echo6"])                     # no way out — not probed
        self.assertNotIn("2001:db8:30::a3f", [c[c.index("--interface") + 1] for c in r.of("curl", "-s", "-6")])
        self.assertTrue(all(A.IP_ECHO6 in c and "--max-time" in c for c in r.of("curl", "-s", "-6")))
        r = machine(route6=NO_ROUTE, ways6={"2001:db8:10::5": NO_ROUTE})
        m = A.machine(r, refresh=True)
        self.assertEqual([a["route_ok6"] for a in m["addresses6"]], [False] * 4)   # «IPv6 есть, маршрута наружу нет»
        self.assertEqual(r.of("curl", "-s", "-6"), [])

    def test_ipv6_disabled_no_rows_no_command(self):
        r = machine(addr=IP4)                                                   # ipv6.disable=1: no inet6 at all
        d = A.collect(r, {})
        self.assertEqual((d["addresses6"], d["v6"]), ([], []))
        self.assertEqual(r.of("ip", "-6"), [])
        self.assertEqual(r.of("curl", "-s", "-6"), [])

    def test_the_echo_once_in_ten_minutes_never_without_the_network(self):
        r = machine(seen={"2001:db8:10::5": "2001:db8:10::5"})
        A.collect(r, {}, net=False)
        self.assertEqual(r.of("curl"), [])                                     # the menu header, the watch: no probe
        A.collect(r, {})
        self.assertEqual(len(r.of("curl", "-s", "-6")), 3)                     # the three with a way out
        self.now += 60
        d = A.collect(r, {})
        self.assertEqual(len(r.of("curl", "-s", "-6")), 3)                     # fresh: the cache's echo
        self.assertEqual(d["addresses6"][0]["echo6"], "2001:db8:10::5")
        A.collect(r, {}, refresh=True)
        self.assertEqual(len(r.of("curl", "-s", "-6")), 6)
        self.now += A.FRESH + 1
        A.collect(r, {})
        self.assertEqual(len(r.of("curl", "-s", "-6")), 9)

    def test_the_echo_runs_with_the_nat_probes_at_once(self):
        """A cloud: the NAT probes of IPv4 and the echo of IPv6 — one pool, every probe at once."""
        cloud4 = (r"2: eth0    inet 10.0.0.5/24 scope global eth0\       valid_lft forever preferred_lft forever"
                  "\n")
        r = machine(addr=cloud4 + IP6, seen={"2001:db8:10::5": "2001:db8:10::5"})
        r.answers[("ip", "-4", "route", "get", "1.1.1.1")] = (0, "1.1.1.1 via 10.0.0.1 dev eth0 src 10.0.0.5 uid 0\n")
        r.answers[("curl", "-s", "-4")] = (0, "85.143.0.20")
        pools = []
        real = A._probe

        def spy(runner, nat_locals=(), v6_locals=()):
            pools.append((list(nat_locals), list(v6_locals)))
            return real(runner, nat_locals, v6_locals)
        with mock.patch.object(A, "_probe", spy):
            d = A.collect(r, {})
        self.assertEqual(pools, [(["10.0.0.5"], ["2001:db8:10::5", "2001:db8:20::5", "2001:db8:10::7"])])
        self.assertEqual([it["ip"] for it in d["addresses"]], ["85.143.0.20"])
        self.assertEqual(d["addresses6"][0]["echo6"], "2001:db8:10::5")

    def test_in_dns_by_the_aaaa_records(self):
        r = machine()
        st = {"site": {"domain": "example.com", "dns": {"a": ["85.143.0.20"], "aaaa": ["2001:DB8:10:0::5"]}}}
        d = A.collect(r, st, net=False)
        self.assertEqual([(a["ip"], a["in_dns"]) for a in d["addresses6"]],
                         [("2001:db8:10::5", True), ("2001:db8:20::5", False), ("2001:db8:10::7", False),
                          ("2001:db8:30::a3f", False)])
        self.assertEqual([a["in_dns"] for a in A.collect(r, {}, net=False)["addresses6"]], [None] * 4)


def _old_module():
    """gw/addresses.py of 3.3.7.14 (``git show``) as a module of the package kaskad.gw; SkipTest without git."""
    try:
        src = subprocess.run(["git", "-C", REPO, "show", REV14 + ":lib/kaskad/gw/addresses.py"], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise unittest.SkipTest("no git")
    if src.returncode != 0:
        raise unittest.SkipTest("no commit %s" % REV14)
    mod = types.ModuleType("kaskad.gw._addresses_3314")
    mod.__package__ = "kaskad.gw"
    mod.__file__ = "<3.3.7.14>"
    exec(compile(src.stdout, mod.__file__, "exec"), mod.__dict__)
    return mod


class Same3314Test(RootCase):
    """The document of 3.3.7.14 for the same machine and state — the same, plus ``addresses6``."""

    def setUp(self):
        super().setUp()
        doc6(self)
        self.old = _old_module()
        for mod in (A, self.old):
            p = mock.patch.object(mod, "_now", lambda: 1790000000.0)
            p.start()
            self.addCleanup(p.stop)

    def test_addresses_and_v6_byte_for_byte(self):
        st = {"gateway": {"host": "85.143.0.20"}, "site": {"domain": "example.com", "dns": {"a": ["85.143.0.20"]}}}
        old = self.old.compose(self.old.discover(machine(), None), st)
        new = A.compose(A.discover(machine(), None), st)
        # 3.3.7.15 Д2 — fields added, none changed: ``ipv6_off`` of the document, ``secondary`` of an address
        # (tests/test_r15_addr_kernel.py)
        same = dict((k, v) for k, v in new.items() if k not in ("addresses6", "ipv6_off"))
        same["addresses"] = [dict((k, v) for k, v in it.items() if k != "secondary") for it in new["addresses"]]
        self.assertEqual(json.dumps(same, sort_keys=True), json.dumps(old, sort_keys=True))
        self.assertEqual([a["ip"] for a in new["addresses6"]],
                         ["2001:db8:10::5", "2001:db8:20::5", "2001:db8:10::7", "2001:db8:30::a3f"])

    def test_the_rollback_reads_a_new_cache(self):
        m = A.machine(machine())                                               # this version wrote the cache
        self.assertIn("addresses6", A.load_cache())
        st = {"gateway": {"host": "85.143.0.20"}}
        self.assertEqual(self.old.compose(self.old.load_cache(), st),
                         self.old.compose(dict((k, v) for k, v in m.items()
                                               if k not in ("addresses6", "route6", "echo6_at")), st))
        self.old.machine(machine(), net=False)                                 # 3.3.7.14 writes its own back
        self.assertNotIn("addresses6", A.load_cache())
        self.assertEqual(A.compose(A.load_cache(), st)["addresses6"], [])      # and this version reads that one


A6 = {"ip": "2001:db8:10::5", "iface": "eth0", "deprecated": False}


def rows6(*items):
    return [dict(zip(("ip", "iface", "deprecated"), it)) for it in items]


class PrefixTest(unittest.TestCase):
    def test_deprecated_and_a_new_prefix(self):
        prev = rows6(("2001:db8:10::5", "eth0", False))
        now = rows6(("2001:db8:10::5", "eth0", True), ("2001:db8:20::9", "eth0", False), ("2001:db8:20::5", "eth0", False))
        self.assertEqual(A.prefix_changes(prev, now), [{"iface": "eth0", "old": "2001:db8:10::/64",
                                                        "new": "2001:db8:20::/64", "suggest": "2001:db8:20::5"}])
        # gone instead of deprecated — the same
        self.assertEqual(len(A.prefix_changes(prev, rows6(("2001:db8:20::5", "eth0", False)))), 1)

    def test_no_event(self):
        prev = rows6(("2001:db8:10::5", "eth0", False))
        self.assertEqual(A.prefix_changes(prev, rows6(("2001:db8:10::5", "eth0", True))), [])        # only deprecated
        self.assertEqual(A.prefix_changes(prev, rows6(("2001:db8:10::5", "eth0", True),
                                                      ("2001:db8:10::6", "eth0", False))), [])       # the same /64
        self.assertEqual(A.prefix_changes(prev, rows6(("2001:db8:10::5", "eth0", True),
                                                      ("2001:db8:20::5", "eth1", False))), [])       # another interface
        self.assertEqual(A.prefix_changes(rows6(("2001:db8:10::5", "eth0", True)),
                                          rows6(("2001:db8:20::5", "eth0", False))), [])           # it was already
        self.assertEqual(A.prefix_changes(None, rows6(("2001:db8:20::5", "eth0", False))), [])

    def test_the_words_of_the_event(self):
        ev = {"kind": "ip6_prefix_changed", "key": "gw_live.event.ip6_prefix_changed", "text": "",
              "args": {"iface": "eth0", "old": "2001:db8:10::/64", "new": "2001:db8:20::/64", "suggest": "2001:db8:20::5"}}
        ru, en = live.event_text(ev, "ru"), live.event_text(ev, "en")
        self.assertIn("Префикс IPv6 сменился: 2001:db8:10::/64 → 2001:db8:20::/64 (интерфейс eth0)", ru)
        self.assertIn("2001:db8:20::5", ru)
        self.assertIn("The IPv6 prefix changed: 2001:db8:10::/64 → 2001:db8:20::/64 (interface eth0)", en)
        self.assertFalse(any("а" <= c <= "я" for c in en.lower()))


class CollectorTest(RootCase):
    def test_the_collector_tells_it_once(self):
        mem = collect.Mem()
        n = [0]

        def put(rows):
            A.save_cache({"addrs": [], "addresses6": rows})
            n[0] += 1
            os.utime(A.cache_path(), (1790000000 + n[0], 1790000000 + n[0]))     # another mtime each write
        out = {"events": []}
        with mock.patch.object(collect, "MEM", mem):
            collect._ip6_round(out)                                            # no cache: nothing
            put([dict(A6)])
            collect._ip6_round(out)                                            # the first look only remembers
            self.assertEqual(out["events"], [])
            put([dict(A6, deprecated=True), {"ip": "2001:db8:20::5", "iface": "eth0", "deprecated": False}])
            with mock.patch.object(A, "load_cache", wraps=A.load_cache) as reads:
                collect._ip6_round(out)
                collect._ip6_round(out)                                        # the file did not change: not read
            self.assertEqual(reads.call_count, 1)
        self.assertEqual(out["events"], [{"kind": "ip6_prefix_changed", "iface": "eth0", "old": "2001:db8:10::/64",
                                          "new": "2001:db8:20::/64", "suggest": "2001:db8:20::5"}])

    def reboot(self, before, after):
        """REV-3315-b1 С-1 (the probe of the reviewer as a test): the cache the last process left holds ``before``; a new
        process (a fresh ``Mem``) — its first look (``mip.watch``) writes the machine now (``after``) over it, then the
        round of the addresses compares. -> the events of that one round."""
        from kaskad.gw import mip
        A.save_cache({"addrs": [], "addresses6": before})
        os.utime(A.cache_path(), (1790000001, 1790000001))

        def look(runner, st, now, last):
            A.save_cache({"addrs": [], "addresses6": after})
            os.utime(A.cache_path(), (1790000002, 1790000002))
            return True
        out = {"events": []}
        with mock.patch.object(collect, "MEM", collect.Mem()), mock.patch.object(mip, "watch", look):
            collect._mip_round({"gateway": {}}, Runner({}), [], out, None, 100.0)
        return out["events"]

    def test_a_prefix_changed_during_a_reboot_is_said(self):
        new = {"ip": "2001:db8:20::5", "iface": "eth0", "deprecated": False}
        self.assertEqual(self.reboot([dict(A6)], [new]), [
            {"kind": "ip6_prefix_changed", "iface": "eth0", "old": "2001:db8:10::/64", "new": "2001:db8:20::/64",
             "suggest": "2001:db8:20::5"}])
        self.assertEqual(self.reboot([dict(A6)], [dict(A6)]), [])                 # a restart, the same: nothing
        self.assertEqual(self.reboot(None, [new]), [])                            # no list before (3.3.7.14): nothing

    def test_the_look_sees_an_ipv6_change_alone(self):
        """REV-3315-b1 С-1 (a guard of the main case): the IPv4 and the route the same, the IPv6 prefix another — the look
        writes the cache (``addrs`` holds the IPv6 that are not deprecated), so ``addresses6`` follows and the round of
        the addresses can say it; the same machine again — not written; ``valid_lft`` counting down — not written."""
        from kaskad.gw import mip
        doc6(self)
        A.machine(machine(), net=False)
        self.assertFalse(mip.watch(machine(), {}, 0, None))
        moved = IP6.replace("2001:db8:10:", "2001:db8:40:")
        self.assertTrue(mip.watch(machine(addr=IP4 + moved), {}, 0, None))
        self.assertIn("2001:db8:40::5", [a["ip"] for a in A.load_cache()["addresses6"]])
        self.assertFalse(mip.watch(machine(addr=IP4 + moved), {}, 0, None))
        A.machine(machine(), net=False)                                            # back as it was
        old = IP6.replace("2001:db8:10::5/64 scope global dynamic", "2001:db8:10::5/64 scope global deprecated dynamic")
        self.assertNotEqual(old, IP6)
        self.assertTrue(mip.watch(machine(addr=IP4 + old), {}, 0, None))           # deprecated now: a change too
        self.assertFalse(mip.watch(machine(addr=IP4 + old.replace("86313sec", "86300sec")), {}, 0, None))  # valid_lft

    def test_a_cache_of_3_3_7_14_is_no_event(self):
        mem = collect.Mem()
        out = {"events": []}
        with mock.patch.object(collect, "MEM", mem):
            A.save_cache({"addrs": []})                                        # no addresses6 yet
            collect._ip6_round(out)
            A.save_cache({"addrs": [], "addresses6": [dict(A6)]})
            os.utime(A.cache_path(), (1790000009, 1790000009))
            collect._ip6_round(out)
        self.assertEqual(out["events"], [])


class PageTest(unittest.TestCase):
    def setUp(self):
        doc6(self)

    def test_the_codes_of_the_rows(self):
        m = {"at": 1790000000, "route": A.parse_route(ROUTE4), "addrs": A.parse_addrs(IP4 + IP6), "nat": [],
             "ways": {"85.143.0.20": A.parse_route(ROUTE4)},
             "addresses6": [dict(a, route_ok6=a["ip"] != "2001:db8:30::a3f",
                                 echo6={"2001:db8:10::7": "2001:db8:10::5"}.get(a["ip"], a["ip"]))
                            for a in A.parse_addrs6(IP6)]}
        st = {"gateway": {"host": "85.143.0.20"}}
        p = A.page(st, A.compose(m, st), now=1790000000 + 3600)
        self.assertEqual(p["v4"], [{"ip": "85.143.0.20", "iface": "eth0", "codes": ["accept", "lock_host", "route_src"],
                                    "out": True}])                     # F12а: the route's — sites see it
        self.assertEqual([(r["ip"], r["codes"], r.get("hours")) for r in p["v6"]], [
            ("2001:db8:10::5", ["reserve", "dynamic"], 23),                  # 86313 s at the look, an hour later
            ("2001:db8:20::5", ["reserve", "deprecated", "dynamic"], 1),
            ("2001:db8:10::7", ["reserve", "echo_other"], None),
            ("2001:db8:30::a3f", ["reserve", "dynamic", "no_route6"], 719)])   # IPv6: «только принимать» (F11)
        self.assertEqual(p["v6"][2]["seen_as"], "2001:db8:10::5")
        self.assertEqual(p["codes"], list(A.STATE_CODES))
        # the words are the panel's (adr.* of pages/addresses.js, П-1 REV-3315-fin): every code is read there
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel",
                               "static", "pages", "addresses.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertEqual([c for c in A.STATE_CODES if '"%s"' % c not in js], [])

    def test_the_codes_with_a_choice_of_ipv6(self):
        """REV-3315-b1 З-4: the IPv6 of ``in6`` — ``reserve`` («только принимать»), the others — ``off``, one chosen and
        gone from the machine — a row ``["reserve", "gone"]``; ``accept`` stays a word of IPv4."""
        m = {"at": 1790000000, "route": A.parse_route(ROUTE4), "addrs": A.parse_addrs(IP4 + IP6), "nat": [],
             "ways": {}, "addresses6": [dict(a, route_ok6=True) for a in A.parse_addrs6(IP6)]}
        st = {"gateway": {"addresses": {"in6": {"*": ["2001:db8:10::7", "2001:db8:77::1"]}}}}
        p = A.page(st, A.compose(m, st), locks={}, now=1790000000)
        self.assertEqual([(r["ip"], r["codes"][0]) for r in p["v6"]], [
            ("2001:db8:10::5", "off"), ("2001:db8:20::5", "off"), ("2001:db8:10::7", "reserve"),
            ("2001:db8:30::a3f", "off"), ("2001:db8:77::1", "reserve")])
        self.assertEqual(p["v6"][-1]["codes"], ["reserve", "gone"])
        self.assertFalse([r for r in p["v6"] if "accept" in r["codes"]])

    def test_in6_list_reads_softly(self):
        def st(v):
            return {"gateway": {"addresses": {"in6": v}}}
        self.assertIsNone(A.in6_list({}))
        self.assertIsNone(A.in6_list(st(None)))
        self.assertIsNone(A.in6_list(st(["2001:db8::1"])))                     # another type — as 3.3.7.14
        self.assertIsNone(A.in6_list(st({"*": ["x", 5]})))                     # only unknown entries — as 3.3.7.14
        self.assertEqual(A.in6_list(st({"*": []})), [])                        # none takes connections
        self.assertEqual(A.in6_list(st({"*": ["2001:DB8::0:1", "x", "fe80::1", "2001:db8::1"]})), ["2001:db8::1"])


if __name__ == "__main__":
    unittest.main()


def with_ipv6(case, lines=IP6, route6=ROUTE6):
    """The fake machine of a GwCase with IPv6 on eth0: ``ip -o addr show`` gets ``lines``, ``ip -6 route get`` answers
    ``route6`` (None — no default IPv6 route) by eth0 for every address."""
    ip = case.w.ip

    def answer(argv, input):
        if list(argv[1:]) == ["-o", "addr", "show"]:
            rc, out, err = ip(argv, input)
            return rc, out + lines, err
        if list(argv[1:4]) == ["-6", "route", "get"]:
            return (0, route6, "") if route6 else (2, "", "RTNETLINK answers: Network is unreachable\n")
        return ip(argv, input)
    case.w.runner._handlers.insert(0, (["ip"], answer))


from tests.panel.panelcase import PanelCase                         # noqa: E402 — after the helpers it reuses


class PanelGetTest(PanelCase):
    def test_the_document_the_page_and_ver(self):
        doc6(self)
        self.gateway()
        with_ipv6(self)
        with mock.patch.object(A, "_echo6", lambda runner, local: local):
            d = self.ok("GET", "/api/gw/addresses")
        self.assertEqual([a["ip"] for a in d["addresses6"]],
                         ["2001:db8:10::5", "2001:db8:20::5", "2001:db8:10::7", "2001:db8:30::a3f"])
        self.assertEqual(d["addresses6"][1]["deprecated"], True)
        self.assertTrue("addresses" in d and "v6" in d and d["mip"]["in"]["all"])
        self.assertEqual([r["ip"] for r in d["page"]["v6"]], [a["ip"] for a in d["addresses6"]])
        self.assertRegex(d["ver"], r"^[0-9a-f]{12}$")
        from kaskad import state
        from kaskad.gw import mip
        self.assertEqual(d["ver"], mip.addresses_ver(state.load()))
        rc = self.call("GET", "/api/gw/addresses?refresh=1", tunnel=False)
        self.assertIn(rc.status, (401, 403))                                   # not without a session of the panel
