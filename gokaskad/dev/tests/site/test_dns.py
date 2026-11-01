"""The DNS check of the domain: our UDP client against a local DNS server in the test (A, AAAA, CNAME chains with
compressed names, NXDOMAIN, SERVFAIL, silence), the states ok/partial/elsewhere/none/unknown, Cloudflare and AAAA,
the node's own addresses, ``--wait``."""

import socket
import struct
import threading
import unittest

from kaskad import state
from kaskad.site import dns, ops
from tests.site.sitecase import DOMAIN, NODE, SiteCase


def _qname(msg):
    """(name, qtype, end of the question) of a query."""
    labels, pos = [], 12
    while msg[pos]:
        n = msg[pos]
        labels.append(msg[pos + 1:pos + 1 + n].decode())
        pos += 1 + n
    qtype = struct.unpack("!H", msg[pos + 1:pos + 3])[0]
    return ".".join(labels), qtype, pos + 5


def _wire(name):
    return b"".join(bytes([len(x)]) + x.encode() for x in name.split(".")) + b"\x00"


def answer(query, records, rcode=0, qid=None):
    """A DNS answer to ``query`` with ``records`` [(owner, type, value)]: the owner equal to the question is written
    as a pointer to it (compression), CNAME targets in full."""
    name, qtype, end = _qname(query)
    rid = struct.unpack("!H", query[:2])[0] if qid is None else qid
    out = struct.pack("!HHHHHH", rid, 0x8180 | rcode, 1, len(records), 0, 0) + query[12:end]
    for owner, rtype, value in records:
        out += b"\xc0\x0c" if owner == name else _wire(owner)
        if rtype == dns.A:
            rdata = socket.inet_aton(value)
        elif rtype == dns.AAAA:
            rdata = socket.inet_pton(socket.AF_INET6, value)
        else:
            rdata = _wire(value)
        out += struct.pack("!HHIH", rtype, 1, 300, len(rdata)) + rdata
    return out


class Server(object):
    """A DNS server on 127.0.0.1:<free port>; ``handler(query bytes) -> bytes | None`` (None: silence)."""

    def __init__(self, handler):
        self.handler = handler
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.addr = self.sock.getsockname()
        self.queries = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        while not self.stop.is_set():
            try:
                data, peer = self.sock.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            self.queries.append(_qname(data)[:2])
            out = self.handler(data)
            if out is not None:
                self.sock.sendto(out, peer)

    def close(self):
        self.stop.set()
        self.thread.join(2)
        self.sock.close()


class ClientTest(unittest.TestCase):
    def serve(self, handler):
        s = Server(handler)
        self.addCleanup(s.close)
        return s

    def ask(self, servers, qtype=dns.A, name=DOMAIN):
        return dns.query(name, qtype, servers=[s.addr for s in servers], timeout=0.3, tries=2)

    def test_a_and_aaaa(self):
        def h(q):
            _, qtype, _ = _qname(q)
            if qtype == dns.A:
                return answer(q, [(DOMAIN, dns.A, NODE), (DOMAIN, dns.A, "198.51.100.7")])
            return answer(q, [(DOMAIN, dns.AAAA, "2001:db8::10")])
        s = self.serve(h)
        self.assertEqual(self.ask([s]), ("ok", [NODE, "198.51.100.7"]))
        self.assertEqual(self.ask([s], dns.AAAA), ("ok", ["2001:db8::10"]))

    def test_cname_chain_in_the_answer(self):
        def h(q):
            return answer(q, [(DOMAIN, dns.CNAME, "edge.example.net"), ("edge.example.net", dns.CNAME, "x.cdn.example"),
                              ("x.cdn.example", dns.A, NODE), ("unrelated.example", dns.A, "192.0.2.1")])
        self.assertEqual(self.ask([self.serve(h)]), ("ok", [NODE]))

    def test_name_without_a_and_nxdomain(self):
        s = self.serve(lambda q: answer(q, []))
        self.assertEqual(self.ask([s]), ("ok", []))
        s = self.serve(lambda q: answer(q, [], rcode=3))
        self.assertEqual(self.ask([s]), ("nxdomain", []))

    def test_servfail_and_silence_go_to_the_next_resolver(self):
        silent = self.serve(lambda q: None)
        servfail = self.serve(lambda q: answer(q, [], rcode=2))
        good = self.serve(lambda q: answer(q, [(DOMAIN, dns.A, NODE)]))
        self.assertEqual(self.ask([silent, servfail, good]), ("ok", [NODE]))
        self.assertEqual(len(silent.queries), 2)                 # two tries of the silent one
        self.assertEqual(len(servfail.queries), 1)               # an answer «fail» is not retried

    def test_everyone_silent_is_fail(self):
        a, b = self.serve(lambda q: None), self.serve(lambda q: None)
        self.assertEqual(self.ask([a, b]), ("fail", []))

    def test_an_answer_with_another_id_is_not_taken(self):
        s = self.serve(lambda q: answer(q, [(DOMAIN, dns.A, "192.0.2.66")], qid=(struct.unpack("!H", q[:2])[0] + 1) % 65536))
        self.assertEqual(self.ask([s]), ("fail", []))

    def test_the_query_on_the_wire(self):
        s = self.serve(lambda q: answer(q, []))
        self.ask([s], dns.AAAA, "Xn--B1awf.xn--e1afmkfd.xn--p1ai")
        self.assertEqual(s.queries, [("Xn--B1awf.xn--e1afmkfd.xn--p1ai", dns.AAAA)])

    def test_resolvers_of_the_contract(self):
        self.assertEqual(dns.RESOLVERS, (("1.1.1.1", 53), ("8.8.8.8", 53), ("77.88.8.8", 53)))
        self.assertEqual((dns.TIMEOUT, dns.TRIES), (2.0, 2))
        self.assertEqual(len(dns.CF_NETS), 15)


class EvaluateTest(unittest.TestCase):
    def ev(self, a, aaaa=(), own4=(NODE,), own6=(), status="ok"):
        return dns.evaluate(status, list(a), list(aaaa), list(own4), list(own6))

    def test_states(self):
        self.assertEqual(self.ev([NODE])["state"], "ok")
        self.assertTrue(self.ev([NODE])["ready"])
        self.assertEqual(self.ev([NODE, "198.51.100.7"])["state"], "partial")
        self.assertEqual(self.ev(["198.51.100.7"])["state"], "elsewhere")
        self.assertEqual(self.ev([])["state"], "none")
        self.assertEqual(self.ev([], status="nxdomain")["state"], "none")
        self.assertEqual(self.ev([], status="fail")["state"], "unknown")
        self.assertEqual(self.ev([NODE], own4=())["state"], "unknown")       # nothing to compare with
        for st in ("partial", "elsewhere", "none", "unknown"):
            self.assertFalse(self.ev({"partial": [NODE, "198.51.100.7"], "elsewhere": ["198.51.100.7"],
                                      "none": [], "unknown": []}[st], status="fail" if st == "unknown" else "ok")["ready"])

    def test_cloudflare(self):
        d = self.ev(["104.21.3.4", "172.67.1.2"])
        self.assertEqual(d["state"], "elsewhere")
        self.assertTrue(d["cloudflare"])
        self.assertFalse(self.ev(["104.21.3.4", "198.51.100.7"])["cloudflare"])     # not all foreign in Cloudflare
        self.assertFalse(self.ev([NODE])["cloudflare"])
        self.assertTrue(dns.in_cloudflare("131.0.72.1"))
        self.assertTrue(dns.in_cloudflare("2606:4700::6810:1"))
        self.assertFalse(dns.in_cloudflare("198.51.100.7"))

    def test_aaaa(self):
        d = self.ev([NODE], ["2001:db8::99"], own6=["2001:db8::10"])
        self.assertEqual(d["state"], "ok")
        self.assertTrue(d["aaaa_foreign"])
        self.assertFalse(d["ready"])
        self.assertFalse(self.ev([NODE], ["2001:db8::10"], own6=["2001:db8::10"])["aaaa_foreign"])
        self.assertTrue(self.ev([NODE], ["2001:db8::10"])["aaaa_foreign"])          # the node has no IPv6
        # behind Cloudflare its own IPv6 in AAAA is not «foreign» (goAmnezia LZ-03)
        self.assertFalse(self.ev(["104.21.3.4"], ["2606:4700::6810:1"])["aaaa_foreign"])

    def test_advice(self):
        def adv(d):
            return dns.advice(DOMAIN, d)
        self.assertIsNone(adv(self.ev([NODE])))
        self.assertIn("нет A-записи", adv(self.ev([])))
        self.assertIn(NODE, adv(self.ev([])))
        self.assertIn("не пройдёт", adv(self.ev(["198.51.100.7"])))
        self.assertIn("198.51.100.7", adv(self.ev([NODE, "198.51.100.7"])))
        self.assertIn("оранжевое облако", adv(self.ev(["104.21.3.4"])))
        self.assertIn("AAAA", adv(self.ev([NODE], ["2001:db8::99"])))
        self.assertIn("не ответил", adv(self.ev([], status="fail")))


class CheckTest(SiteCase):
    def test_own_addresses_of_the_node(self):
        own4, own6 = dns.own_addresses(state.load(), self.w.runner)
        self.assertEqual(own4, [NODE])
        self.assertEqual(own6, [])

    def test_gateway_host_counts_as_own(self):
        st = state.load()
        st["gateway"] = {"host": "198.51.100.50"}
        self.assertIn("198.51.100.50", dns.own_addresses(st, self.w.runner)[0])
        st["gateway"] = {"host": DOMAIN}                          # a name is not an address
        self.assertEqual(dns.own_addresses(st, self.w.runner)[0], [NODE])

    def test_check_document(self):
        self.answers[dns.AAAA] = ("ok", ["2001:db8::99"])
        d = dns.check(DOMAIN, state.load(), self.w.runner)
        self.assertEqual(sorted(d), ["a", "aaaa", "aaaa_foreign", "advice", "checked_at", "cloudflare", "own", "ready",
                                     "state"])
        self.assertEqual((d["state"], d["a"], d["aaaa"], d["own"]), ("ok", [NODE], ["2001:db8::99"], [NODE]))
        self.assertTrue(d["aaaa_foreign"])
        self.assertFalse(d["ready"])
        self.assertIn("AAAA", d["advice"])

    def test_wait_repeats_every_15_s_until_ready(self):
        ops.set_domain(self.w.runner, DOMAIN)
        seq = [("ok", []), ("ok", []), ("ok", [NODE])]
        self.answers[dns.A] = seq[0]
        said = []

        def step(s):
            self.slept.append(s)
            self.mono += s
            self.answers[dns.A] = seq[min(len(self.slept), 2)]
        self._site_patches[3].stop()
        from unittest import mock
        with mock.patch.object(ops, "_sleep", step):
            d = ops.check_dns(self.w.runner, wait=60, say=said.append)
        self._site_patches[3].start()
        self.assertEqual(d["state"], "ok")
        self.assertEqual(self.slept, [15, 15])
        self.assertEqual(len(said), 2)
        self.assertEqual(self.site()["dns"]["state"], "ok")         # saved as the last check

    def test_wait_gives_up(self):
        ops.set_domain(self.w.runner, DOMAIN)
        self.answers[dns.A] = ("ok", ["198.51.100.7"])
        d = ops.check_dns(self.w.runner, wait=40)
        self.assertEqual(d["state"], "elsewhere")
        self.assertEqual(self.slept, [15, 15])
        self.assertEqual(ops.check_dns(self.w.runner)["state"], "elsewhere")
        self.assertEqual(self.slept, [15, 15])                       # without --wait: once

    def test_wait_limits(self):
        from kaskad.errors import KaskadError
        ops.set_domain(self.w.runner, DOMAIN)
        for bad in (-1, 301, "x"):
            with self.assertRaises(KaskadError):
                ops.check_dns(self.w.runner, wait=bad)

    def test_dns_without_a_domain(self):
        from kaskad.errors import KaskadError
        with self.assertRaises(KaskadError) as c:
            ops.check_dns(self.w.runner)
        self.assertIn("Домен не задан", str(c.exception))
