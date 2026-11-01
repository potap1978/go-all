"""3.3.7.15 Д2 (BRIEF-polish-3315 «Д2», stand/logs/D-3315-view-adr.md): two fields of ``GET /api/gw/addresses`` the view
«Адреса» needs — both added, never changed:

* ``addresses[].secondary`` — the local address is a secondary one of its interface (``secondary`` of ``ip -o addr``):
  the line «eth0 · второй адрес» of the frames Д5 and М3; None — not on an interface (added by hand, the address for
  clients the server has not), or a cache without the mark;
* ``ipv6_off`` — IPv6 is turned off in the kernel (``disable_ipv6`` of every interface of the server, ``all`` for an
  interface not read, no /proc/sys/net/ipv6 at all — ``ipv6.disable=1``): the frame С3 «IPv6 выключен в ядре» apart from
  С2 «IPv6 нет»; None — not read (the page opens as before); the line of ``kaskadctl gw addresses``.

The sysctl files live under KASKAD_ROOT (``paths.P``) — the tests write them there."""

import os
import unittest
from unittest import mock

from kaskad.gw import addresses as A
from kaskad.i18n import tr
from tests.helpers import RootCase
from tests.test_r15_addr6 import IP4, IP6, doc6, machine

ROUTE = "1.1.1.1 via 85.143.0.1 dev eth0 src 85.143.0.20 uid 0 \n    cache \n"
LINES = (r"2: eth0    inet 85.143.0.20/24 brd 85.143.0.255 scope global eth0\       valid_lft forever preferred_lft forever"
         "\n"
         r"2: eth0    inet 85.143.0.21/24 brd 85.143.0.255 scope global secondary eth0\       valid_lft forever "
         "preferred_lft forever\n"
         r"3: eth1    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth1\       valid_lft forever preferred_lft forever"
         "\n")


def _m(addrs=None, **kw):
    m = {"at": 1790000000, "route": A.parse_route(ROUTE), "addrs": A.parse_addrs(LINES) if addrs is None else addrs,
         "nat": [{"local": "10.0.0.5", "ip": "85.143.0.30"}], "ways": {}}
    m.update(kw)
    return m


class SecondaryTest(unittest.TestCase):
    ST = {"gateway": {"host": "85.143.0.50", "addresses": {"manual": [{"ip": "85.143.0.40"}]}}}

    def rows(self, m):
        return dict((it["ip"], it["secondary"]) for it in A.compose(m, self.ST)["addresses"])

    def test_the_mark_of_each_origin(self):
        self.assertEqual(self.rows(_m()), {
            "85.143.0.20": False,               # the primary one of eth0
            "85.143.0.21": True,                # «secondary» of ip addr: «eth0 · второй адрес»
            "85.143.0.30": False,               # a NAT pair — the mark of its local address 10.0.0.5
            "85.143.0.40": None,                # added by hand, no local address on an interface
            "85.143.0.50": None})               # the address for clients the server has not

    def test_a_cache_without_the_mark(self):
        old = [dict((k, v) for k, v in a.items() if k != "secondary") for a in A.parse_addrs(LINES)]
        self.assertEqual(set(self.rows(_m(addrs=old)).values()), {None})
        self.assertEqual(set(self.rows(_m(addrs=[dict(a, secondary="yes") for a in old])).values()), {None})


class Ipv6OffTest(RootCase):
    def put(self, dev, value):
        d = self.path("/proc/sys/net/ipv6/conf/%s" % dev)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "disable_ipv6"), "w") as f:
            f.write(value)

    def test_not_read(self):
        self.assertIsNone(A.ipv6_off(["eth0"]))                         # no /proc/sys/net (KASKAD_ROOT empty)
        os.makedirs(self.path("/proc/sys/net/ipv6/conf"))
        self.assertIsNone(A.ipv6_off(["eth0"]))                         # no file of eth0, none of all
        self.put("eth0", "x\n")
        self.assertIsNone(A.ipv6_off(["eth0"]))                         # not a number
        os.makedirs(self.path("/proc/sys/net/ipv6/conf/eth1/disable_ipv6"))
        self.assertIsNone(A.ipv6_off(["eth1"]))                         # cannot be read (a directory, even for root)

    def test_no_ipv6_in_the_kernel(self):
        os.makedirs(self.path("/proc/sys/net/ipv4"))
        self.assertIs(A.ipv6_off(["eth0"]), True)                       # ipv6.disable=1: no /proc/sys/net/ipv6

    def test_disable_ipv6_of_the_interfaces(self):
        self.put("all", "1\n")
        self.put("eth0", "1\n")
        self.assertIs(A.ipv6_off(["eth0"]), True)
        self.put("eth0", "0\n")
        self.assertIs(A.ipv6_off(["eth0"]), False)                      # «all» written, eth0 turned on again
        self.put("all", "0\n")
        self.put("eth0", "1\n")
        self.assertIs(A.ipv6_off(["eth0"]), True)                       # off at the interface of the server only
        self.put("eth1", "0\n")
        self.assertIs(A.ipv6_off(["eth0", "eth1"]), False)              # on at one of them: «IPv6 нет», not «выключен»
        self.put("all", "1\n")
        self.assertIs(A.ipv6_off(["eth2"]), True)                       # no file of the interface — the value of all
        self.assertIs(A.ipv6_off([]), True)                             # no interface known — all
        self.assertIs(A.ipv6_off(["../all", "a/b", ".", ""]), True)     # names that are no directory — skipped

    def test_the_document_the_cache_and_the_line(self):
        doc6(self)
        self.put("eth0", "1\n")
        r = machine(addr=IP4)                                           # no inet6 at all
        d = A.collect(r, {}, net=False)
        self.assertIs(d["ipv6_off"], True)
        self.assertIs(A.load_cache()["ipv6_off"], True)
        self.assertIn(tr("gw_addresses.v6_off"), A.lines(d))
        self.assertIs(A.cached({})["ipv6_off"], True)                   # the menu header reads the cache
        self.put("eth0", "0\n")
        d = A.collect(machine(addr=IP4 + IP6), {}, net=False)
        self.assertIs(d["ipv6_off"], False)
        self.assertNotIn(tr("gw_addresses.v6_off"), A.lines(d))

    def test_no_line_when_not_known(self):
        d = A.collect(machine(addr=IP4), {}, net=False)                 # no /proc/sys/net under the root
        self.assertIsNone(d["ipv6_off"])
        self.assertNotIn(tr("gw_addresses.v6_off"), A.lines(d))
        self.assertIsNone(A.compose(None, {})["ipv6_off"])
        self.assertIsNone(A.compose({"addrs": []}, {})["ipv6_off"])     # a cache before 3.3.7.15

    def test_the_words_ru_en(self):
        ru, en = tr("gw_addresses.v6_off", "ru"), tr("gw_addresses.v6_off", "en")
        self.assertIn("выключен в ядре", ru)
        self.assertIn("turned off in the kernel", en)
        self.assertFalse(any("а" <= c <= "я" for c in en.lower()))


from tests.panel.panelcase import PanelCase                         # noqa: E402 — after the helpers it reuses


def with_secondary(case):
    """The fake machine of a GwCase: a secondary IPv4 on eth0 next to its own address."""
    ip = case.w.ip

    def answer(argv, input):
        if list(argv[1:]) == ["-o", "addr", "show"]:
            rc, out, err = ip(argv, input)
            return rc, out + ("2: eth0    inet 85.143.0.21/32 scope global secondary eth0\\       valid_lft forever "
                              "preferred_lft forever\n"), err
        return ip(argv, input)
    case.w.runner._handlers.insert(0, (["ip"], answer))


class PanelGetTest(PanelCase):
    def test_the_two_fields_and_the_page_opens(self):
        self.gateway()
        with_secondary(self)
        d = self.ok("GET", "/api/gw/addresses")
        self.assertIsNone(d["ipv6_off"])                                # nothing read: the page opens as before
        rows = dict((it["ip"], it["secondary"]) for it in d["addresses"])
        self.assertIs(rows.get("85.143.0.21"), True)
        self.assertEqual([v for ip, v in rows.items() if ip != "85.143.0.21"], [False] * (len(rows) - 1))
        os.makedirs(self.root + "/proc/sys/net/ipv4")
        with mock.patch.object(A, "parse_addrs6", lambda text: []):
            d = self.ok("GET", "/api/gw/addresses")
        self.assertIs(d["ipv6_off"], True)                              # no /proc/sys/net/ipv6: ipv6.disable=1
        self.assertEqual(d["addresses6"], [])


if __name__ == "__main__":
    unittest.main()
