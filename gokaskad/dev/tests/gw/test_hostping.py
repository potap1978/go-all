"""Light probes of the node's network — kaskad.gw.hostping (SPEC-node-score §3.3, §4 A3, NS-D16; REV-spec-node-score-sec).

No packet leaves the machine: the sockets are fakes with a queue of packets on a virtual clock (FakeNet), the parsing
is checked on bytes. On Linux with the right to a ping socket one series goes to 127.0.0.1 for real."""

import builtins
import contextlib
import errno
import fcntl
import json
import os
import random
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from kaskad.gw import hostping as hp
from tests.helpers import lock_held_elsewhere

V4 = socket.AF_INET
V6 = socket.AF_INET6
A1, A2, A3 = "1.1.1.1", "8.8.8.8", "77.88.8.8"
A6 = "2606:4700:4700::1111"
S = 1000000000
MARK = b"MARKER-7f3a"


def reply(fam, ident, seq, data, typ=None, code=0, good_sum=True):
    """An echo reply: the ICMP header (with a right checksum for IPv4) + data."""
    typ = hp.ECHO[fam][1] if typ is None else typ
    head = struct.pack("!BBHHH", typ, code, 0, ident, seq)
    cs = hp.checksum(head + data) if fam == V4 else 0
    if not good_sum:
        cs ^= 0x5A5A
    return struct.pack("!BBHHH", typ, code, cs, ident, seq) + data


def ipv4(icmp, src="1.1.1.1", ihl=5, proto=1, version=4):
    """An IPv4 header of ``ihl`` words (options zero) before ``icmp``."""
    hl = ihl * 4
    head = struct.pack("!BBHHHBBH4s4s", (version << 4) | ihl, 0, max(hl, 20) + len(icmp), 0, 0, 64, proto, 0,
                       socket.inet_aton(src), socket.inet_aton("10.0.0.2"))
    head += b"\x00" * max(0, hl - 20)
    return head + icmp


class FakeSock(object):
    def __init__(self, net, fam, kind, proto):
        self.net, self.family, self.type, self.proto = net, fam, kind, proto
        self.q = []
        self.opts = {}
        self.closed = False
        self.connected = None
        self.port = None
        self.calls = []
        self.fd = len(net.socks) + 3

    def fileno(self):
        return self.fd

    def setblocking(self, v):
        self.calls.append(("setblocking", v))

    def setsockopt(self, level, opt, val):
        if (level, opt) in self.net.fail_opts:
            raise OSError(errno.EINVAL, "no")
        self.opts[(level, opt)] = val

    def getsockopt(self, level, opt):
        self.calls.append(("getsockopt", level, opt))
        return 0

    def connect(self, addr):
        self.calls.append(("connect", addr))
        if self.net.connect_err:
            raise OSError(self.net.connect_err, "connect")
        self.connected = addr

    def getsockname(self):
        return ("0.0.0.0", self.port)

    def sendto(self, pkt, addr):
        self.calls.append(("sendto", addr))
        self.net.on_send(self, pkt, addr[0])

    def send(self, pkt):
        self.calls.append(("send",))
        self.net.on_send(self, pkt, self.connected[0])

    def recvmsg(self, size, anc):
        now = self.net.mono
        for i, item in enumerate(self.q):
            if item[0] <= now:
                del self.q[i]
                if isinstance(item[1], OSError):
                    raise item[1]
                return item[1:]
        raise BlockingIOError(errno.EAGAIN, "again")

    def close(self):
        self.closed = True

    def put(self, delay_ns, data, src, flags=0, ovfl=None, ts_shift=0, ts=True):
        """A packet into the queue in ``delay_ns`` (an OSError instead of ``data`` — an error of recv)."""
        at = self.net.mono + int(delay_ns)
        anc = []
        if ts and (socket.SOL_SOCKET, hp.SO_TIMESTAMPNS) in self.opts and self.net.kernel_ts:
            w = at + self.net.wall_off + int(ts_shift)
            anc.append((socket.SOL_SOCKET, hp.SO_TIMESTAMPNS, struct.pack("@ll", w // S, w % S)))
        if ovfl is not None:
            anc.append((socket.SOL_SOCKET, hp.SO_RXQ_OVFL, struct.pack("=I", ovfl)))
        if isinstance(data, OSError):
            self.q.append((at, data))
        else:
            self.q.append((at, data, anc, flags, (src, 0)))
        self.q.sort(key=lambda x: x[0])


class FakeNet(object):
    """The system calls of hostping on a virtual clock. ``rtt[addr]`` — ms of the answer (None — silent); every
    sent packet is in ``sent`` as (socket, addr, packet, mono)."""

    def __init__(self):
        self.mono = 1000 * S
        self.wall_off = 1760000000 * S - self.mono
        self.socks = []
        self.sent = []
        self.rtt = {}
        self.deny = {}            # (fam, kind) -> errno of socket()
        self.fail_opts = set()
        self.send_err = {}        # addr -> errno of sendto
        self.connect_err = None
        self.kernel_ts = True
        self.busy = False
        self.busy_calls = []
        self.on_open = None       # fn(sock) at its creation
        self.on_packet = None     # fn(sock, addr, pkt) -> True: the fake answered itself
        self.bits = random.Random(7)
        self.polls = 0
        self.port = 40000
        self.step_at = None       # (mono ns, shift ns): the wall clock steps

    # the Net interface
    def socket(self, fam, kind, proto):
        e = self.deny.get((fam, kind, "udp" if proto == socket.IPPROTO_UDP else "icmp"))
        if e is None and proto != socket.IPPROTO_UDP:
            e = self.deny.get((fam, kind))
        if e:
            raise OSError(e, os.strerror(e))
        s = FakeSock(self, fam, kind, proto)
        if kind == socket.SOCK_DGRAM and proto != socket.IPPROTO_UDP:
            self.port += 1
            s.port = self.port
        self.socks.append(s)
        if self.on_open:
            self.on_open(s)
        return s

    def poll(self, socks, timeout):
        self.polls += 1
        ready = [s for s in socks if s.q and s.q[0][0] <= self.mono]
        if ready:
            return ready
        nxt = [s.q[0][0] for s in socks if s.q]
        target = self.mono + int(timeout * S)
        if nxt:
            target = min(target, min(nxt))
        self.advance(max(0, target - self.mono))
        return [s for s in socks if s.q and s.q[0][0] <= self.mono]

    def advance(self, ns):
        self.mono += ns
        if self.step_at and self.mono >= self.step_at[0]:
            self.wall_off += self.step_at[1]
            self.step_at = None

    def time_ns(self):
        return self.mono + self.wall_off

    def monotonic_ns(self):
        return self.mono

    def speed_busy(self, fresh_s):
        self.busy_calls.append(fresh_s)
        return self.busy(fresh_s) if callable(self.busy) else self.busy

    def randbits(self, n):
        return self.bits.getrandbits(n)

    def pid(self):
        return 4242

    # the far end
    def on_send(self, sock, pkt, addr):
        e = self.send_err.get(addr)
        if e:
            raise OSError(e, os.strerror(e))
        self.sent.append((sock, addr, pkt, self.mono))
        self.advance(1000)                                     # 1 µs per send
        if self.on_packet and self.on_packet(sock, addr, pkt):
            return
        ms = self.rtt.get(addr, 20.0)
        if ms is None:
            return
        sock.put(ms * 1e6, self.answer(sock, addr, pkt), addr)

    def answer(self, sock, addr, pkt):
        if sock.proto == socket.IPPROTO_UDP:
            return pkt[:2] + b"\x81\x80" + pkt[4:] + b"\x00" * 20
        fam = sock.family
        _t, _c, _cs, ident, seq = struct.unpack("!BBHHH", pkt[:8])
        if sock.type == socket.SOCK_DGRAM:
            ident = sock.port                                  # the kernel puts the socket's id
        r = reply(fam, ident, seq, pkt[8:])
        if sock.type == socket.SOCK_RAW and fam == V4:
            r = ipv4(r, addr)
        return r

    def of(self, addr):
        return [(s, p, t) for s, a, p, t in self.sent if a == addr]


class Base(unittest.TestCase):
    def setUp(self):
        self.net = FakeNet()

    def prober(self, anchors=(A1, A2, A3), seed=1, confirmed=True):
        """``confirmed`` — every anchor already answered ICMP in this process («in account»: its dark series are
        losses); False — a fresh process, the anchors on trial (NS-D19, ``TrialTest``)."""
        p = hp.Prober(list(anchors), random.Random(seed), net=self.net)
        if confirmed:
            for st in p.state.values():
                st["ok"] = True
        return p

    def by(self, res):
        return dict((a["addr"], a) for a in res["anchors"])


class AnchorTest(unittest.TestCase):
    def test_good_and_bad(self):
        ok = hp.valid_anchor
        self.assertEqual(ok(" 1.1.1.1 "), "1.1.1.1")
        self.assertEqual(ok("2606:4700:4700::1111"), "2606:4700:4700::1111")
        self.assertEqual(ok("::ffff:8.8.8.8"), "8.8.8.8")                 # mapped -> IPv4, checked again
        self.assertEqual(ok("64:ff9b::808:808"), "8.8.8.8")               # NAT64 -> IPv4
        for bad in ("10.0.0.1", "192.168.1.1", "172.16.0.1", "100.64.1.1", "127.0.0.1", "169.254.1.1", "::1",
                    "fe80::1", "fe80::1%eth0", "224.0.0.1", "239.1.1.1", "ff02::1", "0.0.0.0", "::ffff:10.0.0.1",
                    "64:ff9b::a00:1", "255.255.255.255", "one.one.one.one", "1.1.1.1/32", "", None, 16843009,
                    "1.1.1.1 8.8.8.8", "x" * 100):
            self.assertIsNone(ok(bad), bad)
        self.assertIsNone(ok("1.1.1.1", own=["1.1.1.1"]))                 # not the node's own address
        self.assertIsNone(ok("::ffff:1.1.1.1", own=["1.1.1.1"]))

    def test_list_trimmed_and_checked_on_every_read(self):
        many = ["1.0.0.%d" % (i % 250 + 1) for i in range(1000)]          # a .kbk with 1000 anchors
        good, bad = hp.valid_anchors(many)
        self.assertEqual(len(good), 5)
        self.assertEqual(good, ["1.0.0.%d" % i for i in range(1, 6)])
        self.assertGreater(bad, 0)
        good, bad = hp.valid_anchors(["10.0.0.1", A1, A1, "8.8.8.8"])
        self.assertEqual((good, bad), ([A1, A2], 1))
        self.assertEqual(hp.valid_anchors("1.1.1.1"), ([], 1))
        p = hp.Prober(["10.0.0.1", "127.0.0.1"], random.Random(1), net=FakeNet())
        self.assertEqual(p.anchors, list(hp.DEFAULT_ANCHORS))            # nothing good — the defaults
        p.set_anchors(many)
        self.assertEqual(len(p.anchors), 5)


class PacketTest(Base):
    def test_like_ping_timeval_pattern_numbers(self):
        """NS-D16: 56 bytes — timeval of the sending, then 0x10…0x37; numbers 1, 2, 3 per anchor, again in a new
        series; a ping socket of its own per anchor; no connect() on ICMP sockets."""
        p = self.prober()
        for _ in range(2):
            self.net.sent = []
            res = p.series(1000.0)
            self.assertFalse(res["excl"])
            for a in (A1, A2, A3):
                sent = self.net.of(a)
                self.assertEqual([struct.unpack("!H", pkt[6:8])[0] for _s, pkt, _t in sent], [1, 2, 3])
                for _s, pkt, t in sent:
                    self.assertEqual(len(pkt), 8 + 56)
                    self.assertEqual(pkt[0], 8)
                    sec, usec = struct.unpack("@ll", pkt[8:24])
                    wall = t + self.net.wall_off                            # taken right before sendto
                    self.assertEqual(sec, wall // S)
                    self.assertLess(usec, 10 ** 6)
                    self.assertEqual(sec * 10 ** 6 + usec, (wall // 1000))
                    self.assertEqual(pkt[24:], bytes(range(0x10, 0x38)))
                self.assertEqual(len(set(s for s, _p, _t in sent)), 1)
            socks = set(s for s, _a, _p, _t in self.net.sent)
            self.assertEqual(len(socks), 3)                             # own socket per anchor
            for s in socks:
                self.assertEqual((s.type, s.proto), (socket.SOCK_DGRAM, socket.IPPROTO_ICMP))
                self.assertFalse([c for c in s.calls if c[0] == "connect"])
                self.assertTrue(s.closed)                               # closed after the series
                self.assertEqual(s.opts[(socket.SOL_SOCKET, socket.SO_RCVBUF)], 16384)
                self.assertEqual(s.opts[(socket.SOL_SOCKET, hp.SO_RXQ_OVFL)], 1)
            by = self.by(res)
            self.assertEqual([by[a]["n"] for a in (A1, A2, A3)], [3, 3, 3])
            self.assertEqual([by[a]["lost"] for a in (A1, A2, A3)], [0, 0, 0])
            for a in (A1, A2, A3):
                self.assertEqual(by[a]["via"], "icmp")
                for r in by[a]["rtt"]:
                    self.assertAlmostEqual(r, 20.0, delta=0.1)

    def test_packet_gaps_and_duration(self):
        p = self.prober()
        for a in (A1, A2, A3):
            self.net.rtt[a] = None                                     # all three silent
        t0 = self.net.mono
        res = p.series(1000.0)
        dur = (self.net.mono - t0) / 1e9
        self.assertLessEqual(dur, 1.5)
        self.assertGreaterEqual(dur, 1.4)
        times = [t for _s, _p, t in self.net.of(A1)]
        self.assertAlmostEqual((times[1] - times[0]) / 1e9, 0.2, delta=0.001)
        self.assertAlmostEqual((times[2] - times[1]) / 1e9, 0.2, delta=0.001)
        self.assertEqual([(a["n"], a["lost"]) for a in res["anchors"]], [(3, 3)] * 3)

    def test_parse_on_bytes(self):
        data = hp.payload(1760000000 * S + 123456789)
        self.assertEqual(struct.unpack("@ll", data[:16]), (1760000000, 123456))
        ok = reply(V4, 77, 2, data)
        self.assertEqual(hp.parse_echo(ok, V4, False), (77, 2, data))
        self.assertEqual(hp.parse_echo(ipv4(ok), V4, True), (77, 2, data))
        self.assertEqual(hp.parse_echo(ipv4(ok, ihl=6), V4, True), (77, 2, data))     # IHL 6: 24 bytes
        self.assertIsNone(hp.parse_echo(ipv4(ok, ihl=4), V4, True))                   # IHL 4
        big = bytearray(ipv4(ok))
        big[0] = 0x4F                                                                 # IHL 60 > length
        self.assertIsNone(hp.parse_echo(bytes(big[:40]), V4, True))
        self.assertIsNone(hp.parse_echo(ipv4(ok, version=6), V4, True))
        self.assertIsNone(hp.parse_echo(ipv4(ok, proto=17), V4, True))
        self.assertIsNone(hp.parse_echo(reply(V4, 77, 2, data, good_sum=False), V4, False))
        self.assertIsNone(hp.parse_echo(ok[:-1], V4, False))                          # short
        self.assertIsNone(hp.parse_echo(ok + b"\x00\x00", V4, False))                 # long
        for t in (3, 8, 40, 255):
            self.assertIsNone(hp.parse_echo(reply(V4, 77, 2, data, typ=t), V4, False), t)
        self.assertIsNone(hp.parse_echo(reply(V4, 77, 2, data, code=1), V4, False))
        self.assertEqual(hp.parse_echo(reply(V6, 9, 1, data), V6, True), (9, 1, data))
        self.assertIsNone(hp.parse_echo(reply(V6, 9, 1, data, typ=0), V6, True))
        self.assertIsNone(hp.parse_echo(b"", V4, True))
        self.assertIsNone(hp.parse_echo(b"\x45", V4, True))
        q = hp.dns_query(0xBEEF)
        self.assertEqual(q, b"\xbe\xef\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x01")
        self.assertEqual(len(q), 17)                                                   # «.» NS IN — no name
        self.assertEqual(hp.parse_dns(b"\xbe\xef\x81\x80" + b"\x00" * 8), 0xBEEF)
        self.assertIsNone(hp.parse_dns(b"\xbe\xef\x01\x00" + b"\x00" * 8))            # QR = 0
        self.assertIsNone(hp.parse_dns(b"\xbe\xef\x81"))

    def test_filters_of_the_kernel(self):
        self.assertEqual(struct.unpack("=I", hp.icmp_filter_v4())[0], 0xFFFFFFFE)     # all but type 0 dropped
        words = struct.unpack("=8I", hp.icmp_filter_v6())
        self.assertEqual(words[4], 0xFFFFFFFD)                                         # 129 = word 4, bit 1
        self.assertEqual([w for i, w in enumerate(words) if i != 4], [0xFFFFFFFF] * 7)


class ForeignTest(Base):
    def inject(self, make):
        """``make(sock, pkt, addr)`` -> [(delay ns, data, src)] put into the queue of that socket; the real answer
        still comes after them."""
        def on_packet(sock, addr, pkt):
            for d, data, src in make(sock, pkt, addr):
                sock.put(d, data, src)
            return False
        self.net.on_packet = on_packet

    def test_foreign_echo_replies_not_counted(self):
        self.net.rtt[A1] = None                                         # only foreign packets come to A1

        def make(sock, pkt, addr):
            if addr != A1:
                return []
            seq = struct.unpack("!H", pkt[6:8])[0]
            data = pkt[8:]
            other = bytes([data[0] ^ 1]) + data[1:]
            return [(1e6, reply(V4, sock.port + 1, seq, data), A1),           # foreign id
                    (1e6, reply(V4, sock.port, seq + 10, data), A1),          # foreign number
                    (1e6, reply(V4, sock.port, seq, data), "9.9.9.9"),        # foreign address
                    (1e6, reply(V4, sock.port, seq, data)[:30], A1),          # short
                    (1e6, reply(V4, sock.port, seq, data, typ=3), A1),        # «unreachable»
                    (1e6, reply(V4, sock.port, seq, other), A1)]              # foreign data, own id and number
        self.inject(make)
        res = self.prober().series(1000.0)
        by = self.by(res)
        self.assertEqual((by[A1]["n"], by[A1]["lost"], by[A1]["rtt"]), (3, 3, []))
        self.assertEqual(by[A2]["lost"], 0)
        self.assertFalse(res["excl"])                                   # 18 dropped ≤ 64

    def test_ipv6_reply_with_foreign_data(self):
        self.net.rtt[A6] = None

        def make(sock, pkt, addr):
            seq = struct.unpack("!H", pkt[6:8])[0]
            return [(1e6, reply(V6, sock.port, seq, b"\x00" * 56), A6)]
        self.inject(make)
        res = self.prober((A6, A1)).series(1000.0)
        self.assertEqual(self.by(res)[A6]["lost"], 3)
        self.assertEqual(self.net.of(A6)[0][0].proto, hp.IPPROTO_ICMPV6)
        self.assertEqual(self.net.of(A6)[0][1][0], 128)

    def test_duplicate_counted_once_and_late_is_loss(self):
        def make(sock, pkt, addr):
            if addr == A1:
                return [(5e6, self.net.answer(sock, addr, pkt), addr)]      # a duplicate before the answer
            return []
        self.inject(make)
        self.net.rtt[A2] = 1100.0                                       # after 1 s
        res = self.prober().series(1000.0)
        by = self.by(res)
        self.assertEqual((by[A1]["n"], by[A1]["lost"], len(by[A1]["rtt"])), (3, 0, 3))
        self.assertAlmostEqual(by[A1]["rtt"][0], 5.0, delta=0.1)
        self.assertEqual(by[A2]["lost"], 3)

    def test_flood_100_noisy_300_ends_at_256(self):
        for count, reads in ((100, None), (300, 256)):
            self.setUp()
            self.net.rtt[A1] = None
            n = [0]

            def make(sock, pkt, addr, count=count):
                if addr != A1 or n[0]:
                    return []
                n[0] = 1
                return [(1e5 * i, reply(V4, 1, 1, b"\x00" * 56), "9.9.9.9") for i in range(count)]
            self.inject(make)
            got = []
            orig = FakeSock.recvmsg

            def counting(sock, size, anc):
                r = orig(sock, size, anc)
                got.append(1)
                return r
            with mock.patch.object(FakeSock, "recvmsg", counting):
                res = self.prober().series(1000.0)
            self.assertEqual((res["excl"], res["why"]), (True, "noisy"), count)
            self.assertLessEqual(len(got), 256)
            if reads:
                self.assertEqual(len(got), 256)
                self.assertEqual(sum(len(s.q) for s in self.net.socks) > 0, True)   # the rest never read
            self.assertTrue(all(s.closed for s in self.net.socks))
            self.assertEqual([a["runs"] for a in res["anchors"]], [0, 0, 0])

    def test_clock_step_and_negative_rtt(self):
        self.net.step_at = (self.net.mono + int(0.3 * S), 2 * S)
        res = self.prober().series(1000.0)
        self.assertEqual((res["excl"], res["why"]), (True, "clock"))
        self.setUp()

        def on_packet(sock, addr, pkt):
            if addr == A1:
                sock.put(2e7, self.net.answer(sock, addr, pkt), addr, ts_shift=-30e6)   # before the send
                return True
            return False
        self.net.on_packet = on_packet
        res = self.prober().series(1000.0)
        self.assertFalse(res["excl"])
        self.assertEqual(self.by(res)[A1]["lost"], 3)
        self.assertEqual(self.by(res)[A2]["lost"], 0)

    def test_no_kernel_time_monotonic_both(self):
        self.net.kernel_ts = False
        self.net.wall_off += 0                                          # wall and mono apart: only mono is used
        res = self.prober().series(1000.0)
        for a in res["anchors"]:
            self.assertEqual(len(a["rtt"]), 3)
            self.assertAlmostEqual(a["rtt"][0], 20.0, delta=0.1)

    def test_parse_error_holds_no_bytes(self):
        """REV-sec П-4: an exception in the parsing of a packet with a marker in it — the marker is nowhere."""
        def boom(buf, fam, raw):
            raise ValueError("bad %r" % bytes(buf))

        def make(sock, pkt, addr):
            return [(1e6, MARK * 6 + b"\x00" * 2, addr)]
        self.inject(make)
        logged = []
        from kaskad import util
        with mock.patch.object(hp, "parse_echo", boom), \
                mock.patch.object(util, "log_action", lambda *a, **k: logged.append(a)), \
                mock.patch.object(util, "log_tr", lambda *a, **k: logged.append(a)):
            res = self.prober().series(1000.0)
        text = json.dumps(res) + repr(logged)
        self.assertNotIn("MARKER", text)
        self.assertEqual([a["lost"] for a in res["anchors"]], [3, 3, 3])   # the parsing broke: nothing counted
        self.assertFalse(res["excl"])
        # the collector logs str(e) of a failed series: an error of the series carries no packet either
        from kaskad.gw import collect
        self.net = FakeNet()
        self.net.deny[(V4, socket.SOCK_DGRAM)] = errno.EMFILE
        try:
            self.prober().series(1000.0)
        except OSError as e:
            with mock.patch.object(util, "log_action", lambda *a, **k: logged.append(a)):
                collect._live_errors.pop("hostping", None)
                collect._note_error("hostping", e)
        self.assertTrue(logged)
        self.assertNotIn("MARKER", repr(logged))


class RawTest(Base):
    def raw_net(self):
        self.net.deny[(V4, socket.SOCK_DGRAM)] = errno.EACCES
        self.net.deny[(V6, socket.SOCK_DGRAM)] = errno.EACCES

    def test_dgram_denied_raw_with_filter(self):
        self.raw_net()
        p = self.prober((A1, A2, A3, A6))
        res = p.series(1000.0)
        self.assertFalse(res["excl"])
        self.assertEqual([a["lost"] for a in res["anchors"]], [0, 0, 0, 0])
        raws = [s for s in self.net.socks if s.type == socket.SOCK_RAW]
        self.assertEqual(sorted(s.family for s in raws), sorted([V4, V6]))   # one per family and series
        v4 = [s for s in raws if s.family == V4][0]
        v6 = [s for s in raws if s.family == V6][0]
        self.assertEqual(v4.opts[(hp.SOL_RAW, hp.ICMP_FILTER)], hp.icmp_filter_v4())
        self.assertEqual(v6.opts[(hp.IPPROTO_ICMPV6, hp.ICMP6_FILTER)], hp.icmp_filter_v6())
        ids = [struct.unpack("!H", self.net.of(a)[0][1][4:6])[0] for a in (A1, A2, A3)]
        self.assertEqual(len(set(ids)), 3)                              # own id per anchor
        self.assertNotIn(4242, ids)                                     # never the PID
        self.assertTrue(all(s.closed for s in self.net.socks))
        self.assertFalse([c for s in raws for c in s.calls if c[0] == "connect"])
        self.assertEqual(p.icmp, {V4: "raw", V6: "raw"})
        n = len(self.net.socks)
        p.series(1100.0)                                                # kept for the process
        self.assertFalse([s for s in self.net.socks[n:] if s.type == socket.SOCK_DGRAM])
        ids2 = [struct.unpack("!H", self.net.of(a)[-1][1][4:6])[0] for a in (A1, A2, A3)]
        self.assertEqual([struct.unpack("!H", self.net.of(A1)[-1][1][6:8])[0]], [3])
        self.assertNotEqual(ids, ids2)                                  # a new id per series

    def test_raw_types_40_255_ihl_checksum_late(self):
        self.raw_net()
        p = self.prober()
        p.series(1000.0)
        last = {}
        for a in (A1, A2, A3):
            last[a] = self.net.of(a)[0][1]                              # number 1 of the last series
        self.net.rtt[A1] = None
        self.net.rtt[A2] = None

        def on_packet(sock, addr, pkt):
            ident, seq = struct.unpack("!HH", pkt[4:8])
            data = pkt[8:]
            if addr == A1:
                for t in (40, 255):
                    sock.put(1e6, ipv4(reply(V4, ident, seq, data, typ=t), addr), addr)
                sock.put(1e6, ipv4(reply(V4, ident, seq, data, good_sum=False), addr), addr)
                sock.put(1e6, ipv4(reply(V4, ident, seq, data), addr, ihl=4), addr)
                if seq == 1:                                            # late reply of the last series, own id
                    old = last[A1][8:]
                    sock.put(1e6, ipv4(reply(V4, ident, 1, old), addr), addr)
            if addr == A2:
                sock.put(3e6, ipv4(reply(V4, ident, seq, data), addr, ihl=6), addr)     # IHL 6 — right
            return False
        self.net.on_packet = on_packet
        with mock.patch.object(self.net, "randbits", side_effect=[100, 200, 300] + [7] * 50):
            res = p.series(1100.0)
        by = self.by(res)
        self.assertEqual(by[A1]["lost"], 3)
        self.assertEqual(by[A2]["lost"], 0)
        self.assertAlmostEqual(by[A2]["rtt"][0], 3.0, delta=0.1)
        self.assertEqual(by[A3]["lost"], 0)
        self.assertFalse(res["excl"])

    def test_raw_id_not_pid_nor_repeat(self):
        self.raw_net()
        p = self.prober()
        with mock.patch.object(self.net, "randbits", side_effect=[4242, 0, 5, 5, 6, 7] + [9] * 30):
            p.series(1000.0)
        ids = sorted(struct.unpack("!H", self.net.of(a)[0][1][4:6])[0] for a in (A1, A2, A3))
        self.assertEqual(ids, [5, 6, 7])

    def test_no_filter_no_raw_then_dns(self):
        self.raw_net()
        self.net.fail_opts.add((hp.SOL_RAW, hp.ICMP_FILTER))
        p = self.prober()
        res = p.series(1000.0)
        self.assertEqual([a["via"] for a in res["anchors"]], ["dns"] * 3)
        raws = [s for s in self.net.socks if s.type == socket.SOCK_RAW]
        self.assertEqual(len(raws), 1)
        self.assertTrue(raws[0].closed)
        self.assertFalse([1 for s, _a, _p, _t in self.net.sent if s.type == socket.SOCK_RAW])  # never used
        self.assertEqual(p.icmp[V4], "none")

    def test_raw_denied_dns(self):
        self.raw_net()
        self.net.deny[(V4, socket.SOCK_RAW)] = errno.EPERM
        p = self.prober()
        res = p.series(1000.0)
        self.assertEqual([(a["via"], a["n"], a["lost"]) for a in res["anchors"]], [("dns", 3, 0)] * 3)
        dns = [s for s in self.net.socks if s.proto == socket.IPPROTO_UDP]
        self.assertEqual(len(dns), 3)
        for s in dns:
            self.assertEqual([c for c in s.calls if c[0] == "connect"][0][1][1], 53)
            self.assertTrue(s.closed)
        pkts = [pk for _s, _a, pk, _t in self.net.sent]
        self.assertTrue(all(len(pk) == 17 and pk[2:] == hp.DNS_QUERY_TAIL for pk in pkts))
        ids = [pk[:2] for pk in self.net.of(A1) and [x[1] for x in self.net.of(A1)]]
        self.assertEqual(len(set(ids)), 3)                              # a random id per query


class SpinTest(Base):
    def test_ready_but_empty_ends(self):
        """A socket always «ready» with nothing to read: the series still ends; a spin — noisy, bounded."""
        orig = FakeNet.poll
        for step_ns, why in ((1000000, None), (1000, "noisy")):
            self.setUp()
            self.net.rtt[A1] = None

            def always(net, socks, timeout, step_ns=step_ns):
                orig(net, socks, 0)
                net.advance(step_ns)
                return list(socks)
            with mock.patch.object(FakeNet, "poll", always):
                res = self.prober().series(1000.0)
            self.assertLessEqual(self.net.polls, hp.MAX_POLLS + 1)
            self.assertEqual(res["why"], why)
            if why is None:
                self.assertEqual((self.by(res)[A1]["lost"], self.by(res)[A2]["lost"]), (3, 0))


class ErrorTest(Base):
    def test_sendto_errors(self):
        self.net.send_err[A1] = errno.ENETUNREACH
        self.net.send_err[A2] = errno.EPERM
        res = self.prober().series(1000.0)
        by = self.by(res)
        self.assertEqual((by[A1]["na"], by[A1]["n"], by[A1]["lost"]), ("no_route", 0, 0))
        self.assertEqual((by[A2]["na"], by[A2]["n"]), ("blocked", 0))
        self.assertEqual((by[A3]["na"], by[A3]["lost"]), (None, 0))
        for e in (errno.EHOSTUNREACH, errno.EADDRNOTAVAIL):
            self.setUp()
            self.net.send_err[A1] = e
            self.assertEqual(self.by(self.prober().series(1000.0))[A1]["na"], "no_route")

    def test_no_ipv6(self):
        self.net.deny[(V6, socket.SOCK_DGRAM)] = errno.EAFNOSUPPORT
        res = self.prober((A6, A1)).series(1000.0)
        self.assertEqual(self.by(res)[A6]["na"], "no_route")
        self.assertEqual(self.by(res)[A1]["lost"], 0)

    def test_recv_errors_are_losses(self):
        def on_packet(sock, addr, pkt):
            if addr == A1:
                sock.put(1e6, OSError(errno.EHOSTUNREACH, "unreachable"), addr)
                return True
            return False
        self.net.on_packet = on_packet
        res = self.prober().series(1000.0)
        by = self.by(res)
        self.assertEqual((by[A1]["na"], by[A1]["n"], by[A1]["lost"]), (None, 3, 3))
        self.assertEqual(by[A1]["runs"], 1)
        # DNS: ECONNREFUSED at recv — a loss
        self.setUp()
        self.net.deny[(V4, socket.SOCK_DGRAM)] = errno.EACCES
        self.net.deny[(V4, socket.SOCK_RAW)] = errno.EPERM

        def on_dns(sock, addr, pkt):
            if addr == A2:
                sock.put(1e6, OSError(errno.ECONNREFUSED, "refused"), addr)
                return True
            return False
        self.net.on_packet = on_dns
        by = self.by(self.prober().series(1000.0))
        self.assertEqual((by[A2]["via"], by[A2]["na"], by[A2]["lost"]), ("dns", None, 3))
        # the error of the path is read before every send: it is never taken as the send's own
        s = self.net.of(A2)[0][0]
        self.assertTrue([c for c in s.calls if c[0] == "getsockopt" and c[2] == socket.SO_ERROR])

    def test_other_send_error_loses_the_packet(self):
        seen = []

        def on_packet(sock, addr, pkt):
            return False
        orig = FakeNet.on_send

        def flaky(net, sock, pkt, addr):
            if addr == A1 and not seen:
                seen.append(1)
                raise OSError(errno.ENOBUFS, "nobufs")
            return orig(net, sock, pkt, addr)
        with mock.patch.object(FakeNet, "on_send", flaky):
            by = self.by(self.prober().series(1000.0))
        self.assertEqual((by[A1]["na"], by[A1]["n"], by[A1]["lost"]), (None, 3, 1))

    def test_dns_connect_no_route(self):
        self.net.deny[(V4, socket.SOCK_DGRAM)] = errno.EACCES
        self.net.deny[(V4, socket.SOCK_RAW)] = errno.EPERM
        self.net.connect_err = errno.ENETUNREACH
        res = self.prober().series(1000.0)
        self.assertEqual([a["na"] for a in res["anchors"]], ["no_route"] * 3)


class StateTest(Base):
    def test_runs_across_series(self):
        p = self.prober((A1, A2))
        lost = {1: {3}, 2: {1, 2}, 3: {2}}                              # series -> lost numbers of A1

        def on_packet(sock, addr, pkt):
            return addr == A1 and struct.unpack("!H", pkt[6:8])[0] in lost[self.k]
        self.net.on_packet = on_packet
        runs = []
        for self.k in (1, 2, 3):
            runs.append(self.by(p.series(1000.0 + self.k * 30))[A1]["runs"])
        self.assertEqual(runs, [1, 0, 1])                               # the run of 1-3 / 2-1,2 is one

    def test_dark_to_dns_and_back(self):
        p = self.prober()
        self.net.rtt[A1] = None
        mono = 1000.0
        for i in range(19):
            self.assertEqual(self.by(p.series(mono))[A1]["via"], "icmp")
            mono += 30
        p.series(mono)
        sw = mono
        self.assertEqual(p.state[A1]["via"], "dns")
        self.net.rtt[A1] = 20.0                                         # its DNS answers
        mono += 30
        res = p.series(mono)
        self.assertEqual(self.by(res)[A1]["via"], "dns")
        self.assertEqual(self.by(res)[A1]["lost"], 0)                   # DNS answers
        self.assertEqual(self.by(res)[A2]["via"], "icmp")
        day = p.state[A1]["dns_at"]                                     # + U[0; 1 h] (REV-ns-a3-2 П-1)
        self.assertTrue(sw <= day <= sw + hp.RETRY_SPREAD)
        p.series(day + 86400 - 10)
        self.assertEqual(p.state[A1]["via"], "dns")
        res = p.series(day + 86400 + 1)                               # once a day ICMP again
        self.assertEqual(self.by(res)[A1]["via"], "icmp")

    def test_dark_without_alive_others_stays(self):
        p = self.prober()
        for a in (A1, A2, A3):
            self.net.rtt[a] = None
        for i in range(25):
            p.series(1000.0 + i * 30)
        self.assertEqual([p.state[a]["via"] for a in (A1, A2, A3)], ["icmp"] * 3)

    def test_interval_and_order(self):
        p = self.prober(seed=3)
        self.assertGreater(p.until(1000.0), 0)
        gaps, orders = [], set()
        mono = 1000.0
        for _ in range(40):
            self.net.sent = []
            p.series(mono)
            gaps.append(p.next_at - mono)
            orders.add(tuple(a for _s, a, _p, _t in self.net.sent[:3]))
            mono = p.next_at
        self.assertTrue(all(21.0 <= g <= 39.0 for g in gaps))
        self.assertGreater(max(gaps) - min(gaps), 8)
        self.assertGreater(len(orders), 1)                              # the order changes
        self.assertTrue(p.due(mono))
        self.assertFalse(p.due(mono - 1))
        q = self.prober(seed=3)
        self.assertEqual(q.until(1000.0), self.prober(seed=3).until(1000.0))   # the seed fixes it

    def test_series_count_and_size_out(self):
        self.prober().series(1000.0)
        self.assertEqual(len(self.net.sent), 9)                         # 9 packets of 84 bytes with IPv4
        self.assertTrue(all(len(pk) + 20 == 84 for _s, _a, pk, _t in self.net.sent))


class SwitchTest(Base):
    def test_probes_off_no_socket(self):
        p = self.prober()
        for hm in (None, {}, {"enabled": True}, {"enabled": True, "probes": False},
                   {"enabled": False, "probes": True}, {"enabled": True, "probes": "yes"}, {"probes": 1}):
            p.next_at = 0.0                                             # a series is due: only the flag stops it
            with mock.patch.object(socket, "socket") as real:
                self.assertIsNone(hp.step(p, hm, 10 ** 6))
            real.assert_not_called()
        self.assertEqual(self.net.socks, [])
        self.assertEqual(self.net.busy_calls, [])

    def test_flag_off_between_series(self):
        p = self.prober()
        hm = {"enabled": True, "probes": True, "anchors": [A1, A2]}
        mono = 1000.0
        self.assertIsNone(hp.step(p, hm, mono))                         # the first ask schedules
        mono = p.next_at
        res = hp.step(p, hm, mono)
        self.assertEqual([a["addr"] for a in res["anchors"]], [A1, A2])
        n = len(self.net.socks)
        hm["probes"] = False
        self.assertIsNone(hp.step(p, hm, p.next_at + 1))
        self.assertEqual(len(self.net.socks), n)
        hm.update(probes=True, anchors=["10.0.0.1", A3])                # checked again before the series
        res = hp.step(p, hm, p.next_at + 1)
        self.assertEqual([a["addr"] for a in res["anchors"]], [A3])

    def test_no_rights_widened(self):
        """REV-sec П-2: no sysctl, no write to /proc/sys, no process; only /proc/locks is read."""
        opened = []
        real_open = builtins.open

        def spy(path, mode="r", *a, **k):
            opened.append((str(path), mode))
            return real_open(path, mode, *a, **k)
        with mock.patch.object(builtins, "open", spy), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("no process")), \
                mock.patch.object(os, "system", side_effect=AssertionError("no process")), \
                mock.patch.object(fcntl, "flock", side_effect=AssertionError("no flock")):
            self.net.deny[(V4, socket.SOCK_DGRAM)] = errno.EACCES
            p = self.prober()
            p.net.speed_busy = lambda fresh: hp.speed_busy("/nonexistent/lock", fresh)
            p.series(1000.0)
            hp.speed_busy(__file__, 0, locks=__file__)
        self.assertFalse([o for o in opened if "/proc/sys" in o[0] or o[1] != "r"])
        with real_open(hp.__file__) as f:
            src = f.read()
        for word in ("subprocess", "sysctl", "ping_group_range\" ", "os.system", "flock("):
            self.assertNotIn(word, src.replace("``net.ipv4.ping_group_range``", ""))


class SpeedLockTest(Base):
    def test_busy_skips_series_no_socket(self):
        self.net.busy = True
        res = self.prober().series(1000.0)
        self.assertEqual((res["excl"], res["why"]), (True, "speedtest"))
        self.assertEqual(self.net.socks, [])
        self.assertEqual([a["n"] for a in res["anchors"]], [0, 0, 0])

    def test_busy_at_the_end(self):
        self.net.busy = lambda fresh: fresh > hp.SPEED_FRESH            # only the check at the end
        res = self.prober().series(1000.0)
        self.assertEqual((res["excl"], res["why"]), (True, "speedtest"))
        self.assertGreater(self.net.busy_calls[-1], hp.SPEED_FRESH)     # 5 s + the series

    def test_proc_locks_by_inode_and_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            lock = os.path.join(d, "speedtest.lock")
            with open(lock, "w"):
                pass
            st = os.stat(lock)
            os.utime(lock, (st.st_atime, st.st_mtime - 100))
            dev = "%02x:%02x:%d" % (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
            locks = os.path.join(d, "locks")
            with open(locks, "w") as f:
                f.write("1: POSIX  ADVISORY  WRITE 99 00:01:5 0 EOF\n")
            with mock.patch.object(fcntl, "flock", side_effect=AssertionError("no flock")):
                self.assertFalse(hp.speed_busy(lock, locks=locks))
                with open(locks, "a") as f:
                    f.write("2: FLOCK  ADVISORY  WRITE 1234 %s 0 EOF\n" % dev)
                self.assertTrue(hp.speed_busy(lock, locks=locks))
                with open(locks, "w") as f:
                    f.write("2: -> FLOCK  ADVISORY  WRITE 1235 %s 0 EOF\n" % dev)
                self.assertTrue(hp.speed_busy(lock, locks=locks))
                with open(locks, "w"):
                    pass
                self.assertFalse(hp.speed_busy(lock, locks=locks))
                os.utime(lock)                                          # the test just ended
                self.assertTrue(hp.speed_busy(lock, locks=locks))
                self.assertFalse(hp.speed_busy(lock, fresh_s=0, locks=locks))
                self.assertFalse(hp.speed_busy(os.path.join(d, "none"), locks=locks))
                self.assertFalse(hp.speed_busy(lock, fresh_s=0, locks=os.path.join(d, "nolocks")))

    @unittest.skipUnless(os.path.exists("/proc/locks"), "Linux /proc/locks")
    def test_real_lock_seen(self):
        with tempfile.TemporaryDirectory() as d:
            lock = os.path.join(d, "speedtest.lock")
            with open(lock, "w"):
                pass
            os.utime(lock, (1000, 1000))
            self.assertFalse(hp.speed_busy(lock))
            with lock_held_elsewhere(lock):
                self.assertTrue(hp.speed_busy(lock))


class TrialTest(Base):
    """NS-D19 (REV-ns-a3 С-1): an anchor without an ICMP answer in this process goes on trial — a dark trial series is
    no loss; the first answer puts it in account; 20 dark trials — DNS until the daily try."""

    def moscow(self):
        """ICMP to A1 and A2 is cut on the way (a node in Moscow, NS-D15), their DNS answers; A3 answers ICMP."""
        self.net.on_packet = lambda sock, addr, pkt: addr in (A1, A2) and sock.proto != socket.IPPROTO_UDP

    def run_series(self, p, mono, count, out):
        for _ in range(count):
            out.append(p.series(mono))
            mono += 30
        return mono

    def test_moscow_two_days_and_restarts_no_counted_loss(self):
        self.moscow()
        res = []
        p = self.prober(confirmed=False)
        mono = self.run_series(p, 1000.0, 40, res)                     # the start of the process
        self.assertEqual([p.state[a]["via"] for a in (A1, A2, A3)], ["dns", "dns", "icmp"])
        for day in (1, 2):                                              # the daily try back to ICMP, twice
            mono = max(p.state[A1]["dns_at"], p.state[A2]["dns_at"]) + hp.ICMP_RETRY + 1
            mono = self.run_series(p, mono, 40, res)
            self.assertEqual([p.state[a]["via"] for a in (A1, A2)], ["dns", "dns"], day)
        p = self.prober(confirmed=False)                                # a restart of the collector
        self.run_series(p, mono, 40, res)
        trials = [r for r in res if r["trial"]]
        self.assertEqual(len(trials), 4 * hp.DARK_TO_DNS)               # 20 dark trials per start / daily try
        self.assertTrue(all(sorted(r["trial"]) == sorted([A1, A2]) for r in trials))
        rows = [a for r in res for a in r["anchors"] if a["addr"] in (A1, A2)]
        self.assertTrue(rows)
        self.assertEqual(sum(a["lost"] for a in rows), 0)               # not a single counted loss
        self.assertTrue(all(a["via"] == "dns" for a in rows))
        self.assertTrue(all(not r["excl"] for r in res))
        self.assertEqual(sum(a["lost"] for r in res for a in r["anchors"] if a["addr"] == A3), 0)

    def test_confirmed_anchor_losses_still_count(self):
        p = self.prober(confirmed=False)
        res = p.series(1000.0)                                          # all answer: all in account
        self.assertEqual((res["trial"], [a["lost"] for a in res["anchors"]]), ([], [0, 0, 0]))
        self.assertTrue(all(p.state[a]["ok"] for a in (A1, A2, A3)))
        self.net.rtt[A3] = None                                         # a real loss at a confirmed anchor
        self.net.on_packet = lambda sock, addr, pkt: addr == A1 and struct.unpack("!H", pkt[6:8])[0] == 2
        res = p.series(1030.0)
        by = self.by(res)
        self.assertEqual(res["trial"], [])
        self.assertEqual((by[A3]["n"], by[A3]["lost"], by[A3]["runs"]), (3, 3, 1))
        self.assertEqual((by[A1]["n"], by[A1]["lost"]), (3, 1))
        for i in range(2, hp.DARK_TO_DNS + 1):                          # dark while the others answer: counted
            self.assertEqual(self.by(p.series(1000.0 + 30 * i))[A3]["lost"], 3, i)
        self.assertEqual(p.state[A3]["via"], "dns")                     # then DNS, as before
        self.assertFalse(p.state[A3]["ok"])
        res = p.series(p.state[A3]["dns_at"] + hp.ICMP_RETRY + 1)       # the daily try: on trial again
        self.assertEqual(res["trial"], [A3])
        self.assertNotIn(A3, self.by(res))

    def test_trial_with_an_answer_counts_already(self):
        self.net.on_packet = lambda sock, addr, pkt: addr == A1 and struct.unpack("!H", pkt[6:8])[0] != 2
        p = self.prober(confirmed=False)
        res = p.series(1000.0)
        self.assertEqual(res["trial"], [])
        self.assertEqual((self.by(res)[A1]["n"], self.by(res)[A1]["lost"]), (3, 2))
        self.assertTrue(p.state[A1]["ok"])

    def test_total_dark_start_goes_to_dns_then_counts(self):
        for a in (A1, A2, A3):
            self.net.rtt[a] = None
        self.net.on_packet = lambda sock, addr, pkt: sock.proto != socket.IPPROTO_UDP   # only DNS answers
        p = self.prober(confirmed=False)
        res = [p.series(1000.0 + 30 * i) for i in range(hp.DARK_TO_DNS)]
        self.assertTrue(all(r["anchors"] == [] and len(r["trial"]) == 3 for r in res))
        self.assertTrue(all(p.state[a]["via"] == "dns" for a in (A1, A2, A3)))
        self.net.rtt = {}
        r = p.series(1000.0 + 30 * hp.DARK_TO_DNS)
        self.assertEqual([(a["via"], a["n"], a["lost"]) for a in r["anchors"]], [("dns", 3, 0)] * 3)

    def test_new_anchor_and_restart_on_trial(self):
        p = self.prober((A1, A2))
        p.set_anchors([A1, A2, A3])
        self.assertEqual([p.state[a]["ok"] for a in (A1, A2, A3)], [True, True, False])
        self.net.rtt[A3] = None
        r = p.series(1000.0)
        self.assertEqual(r["trial"], [A3])
        self.assertEqual([a["addr"] for a in r["anchors"]], [A1, A2])


class WorkingAnchorsTest(Base):
    """REV-ns-a3 З-1: one list of the anchors in work for the probes and for ``model.hostmon`` / the rule of A2."""

    def test_one_list_defaults_checked_trimmed(self):
        self.assertEqual(hp.working_anchors(["10.0.0.1", "::1"]), (list(hp.DEFAULT_ANCHORS), 2))
        self.assertEqual(hp.working_anchors(None), (list(hp.DEFAULT_ANCHORS), 0))
        self.assertEqual(hp.working_anchors("1.1.1.1"), (list(hp.DEFAULT_ANCHORS), 1))
        many = ["1.0.0.%d" % i for i in range(1, 9)]
        self.assertEqual(hp.working_anchors(many), (many[:5], 3))
        for values in (["10.0.0.1"], many, [A2, "bad", A1], None):
            p = hp.Prober(values, random.Random(1), net=self.net)
            self.assertEqual(p.anchors, hp.working_anchors(values)[0], values)

    def test_all_bad_series_reach_the_rule(self):
        p = self.prober(())
        p.next_at = 0.0
        hm = {"enabled": True, "probes": True, "anchors": ["10.0.0.1", "127.0.0.1"]}
        res = hp.step(p, hm, 1000.0)
        cfg = {"anchors": hp.working_anchors(hm["anchors"])[0]}       # what model.hostmon (A5) gives the rule
        self.assertEqual([a["addr"] for a in res["anchors"]], list(hp.DEFAULT_ANCHORS))
        self.assertTrue(all(a["addr"] in cfg["anchors"] for a in res["anchors"]))   # A2 keeps every series


class BudgetEdgeTest(Base):
    """REV-ns-a3 П-5: the edge of «> 64 dropped» and ``MSG_TRUNC``."""

    def flood(self, count):
        n = [0]

        def on_packet(sock, addr, pkt):
            if addr == A1 and not n[0]:
                n[0] = 1
                for i in range(count):
                    sock.put(1e6 * (i + 1), reply(V4, 1, 1, b"\x00" * 56), "9.9.9.9")   # one at a time
            return False
        self.net.on_packet = on_packet
        return self.prober().series(1000.0)

    def test_64_dropped_counted_65_noisy(self):
        res = self.flood(64)
        self.assertEqual((res["excl"], res["why"]), (False, None))
        self.assertEqual([a["lost"] for a in res["anchors"]], [0, 0, 0])
        self.setUp()
        res = self.flood(65)
        self.assertEqual((res["excl"], res["why"]), (True, "noisy"))

    def test_truncated_reply_not_counted(self):
        def on_packet(sock, addr, pkt):
            if addr == A1:
                sock.put(2e7, self.net.answer(sock, addr, pkt), addr, flags=getattr(socket, "MSG_TRUNC", 0x20))
                return True
            return False
        self.net.on_packet = on_packet
        res = self.prober().series(1000.0)
        self.assertEqual((self.by(res)[A1]["lost"], self.by(res)[A2]["lost"]), (3, 0))


class OverflowTest(Base):
    """NS-D21 (REV-ns-a3 «к безопасности»): the kernel's drop counter grows also for a single packet it drops for a
    bad checksum (UDP, raw ICMPv6) — that alone is not an overflow; an overflow is a full queue that lost our reply."""

    def run_with(self, lose, burst, ovfl_on, dns=False):
        if dns:
            self.net.deny[(V4, socket.SOCK_DGRAM)] = errno.EACCES
            self.net.deny[(V4, socket.SOCK_RAW)] = errno.EPERM

        def on_packet(sock, addr, pkt):
            seq = len([1 for s, a, _p, _t in self.net.sent if s is sock])
            if addr != A2:
                return False
            if burst and seq == 2:
                for _ in range(burst):                                  # queued all at once
                    sock.put(1e7, b"\x00" * 60, "9.9.9.9")
            if lose and seq == 2:
                return True                                             # this reply never comes
            if seq == ovfl_on:
                sock.put(2e7, self.net.answer(sock, addr, pkt), addr, ovfl=1)
                return True
            return False
        self.net.on_packet = on_packet
        return self.prober().series(1000.0)

    def test_full_queue_lost_reply_overflow(self):
        res = self.run_with(lose=True, burst=5, ovfl_on=3)
        self.assertEqual((res["excl"], res["why"]), (True, "overflow"))

    def test_one_damaged_packet_not_overflow(self):
        for dns in (False, True):
            self.setUp()
            res = self.run_with(lose=False, burst=0, ovfl_on=2, dns=dns)
            self.assertEqual((res["excl"], res["why"]), (False, None), dns)
            self.assertEqual([a["lost"] for a in res["anchors"]], [0, 0, 0], dns)

    def test_damaged_packet_and_a_real_loss_counts(self):
        res = self.run_with(lose=True, burst=0, ovfl_on=3)
        self.assertEqual((res["excl"], res["why"]), (False, None))
        self.assertEqual(self.by(res)[A2]["lost"], 1)

    def test_burst_but_nothing_lost_counts(self):
        res = self.run_with(lose=False, burst=5, ovfl_on=3)
        self.assertEqual((res["excl"], res["why"]), (False, None))


class A5BatchTest(Base):
    """«Пачка к A5» (REV-ns-a3-2, REV-ns-a3-sec-2): NS-D22, the edges of NS-D21, the branches A2 leans on."""

    SEQ = staticmethod(lambda pkt: struct.unpack("!H", pkt[6:8])[0])

    def test_dark_dns_tries_icmp_at_once_ns_d22(self):
        """12 min without any answer at the start, then ICMP answers and UDP/53 is closed: the anchors went to DNS
        after 20 dark trials, their DNS stays dark — after 20 dark DNS series ICMP again (not in a day); the first
        ICMP answer counts, no false loss is left."""
        dark = [True]
        self.net.on_packet = lambda sock, addr, pkt: dark[0] or sock.proto == socket.IPPROTO_UDP
        p = self.prober(confirmed=False)
        mono, res = 1000.0, []
        while mono < 1000.0 + 12 * 60:
            res.append(p.series(mono))
            mono += 30
        self.assertEqual([p.state[a]["via"] for a in (A1, A2, A3)], ["dns"] * 3)
        dark[0] = False
        back = mono
        for _ in range(2 * hp.DARK_TO_DNS + 2):
            res.append(p.series(mono))
            mono += 30
            if all(p.state[a]["via"] == "icmp" for a in (A1, A2, A3)) and p.state[A1]["ok"]:
                break
        self.assertTrue(all(p.state[a]["via"] == "icmp" and p.state[a]["ok"] for a in (A1, A2, A3)))
        self.assertLessEqual(mono - back, 22 * 60)                      # ≈ 20 min, not a day
        last = p.series(mono)
        self.assertEqual([(a["via"], a["lost"]) for a in last["anchors"]], [("icmp", 0)] * 3)
        self.assertEqual(last["trial"], [])

    def test_offline_no_series_no_socket(self):
        """NS-D22: while ``node_offline`` is open — no series, no socket, nothing dark is counted."""
        p = self.prober()
        p.next_at = 0.0
        hm = {"enabled": True, "probes": True, "anchors": [A1, A2, A3]}
        self.assertIsNone(hp.step(p, hm, 1000.0, offline=True))
        self.assertEqual(self.net.socks, [])
        self.assertTrue(all(st["dark"] == 0 for st in p.state.values()))
        self.assertIsNotNone(hp.step(p, hm, 1000.0))

    def test_fresh_process_without_icmp_counts_dns_losses(self):
        """N4 / П-4: no ICMP way at all (ping EACCES, raw EPERM) — the slot goes by DNS, not on trial: its losses
        count (``trial`` by the way of the slot, not of the state)."""
        self.net.deny[(V4, socket.SOCK_DGRAM)] = errno.EACCES
        self.net.deny[(V4, socket.SOCK_RAW)] = errno.EPERM
        self.net.rtt[A1] = None
        p = self.prober(confirmed=False)
        res = p.series(1000.0)
        self.assertEqual(res["trial"], [])
        self.assertEqual((self.by(res)[A1]["via"], self.by(res)[A1]["lost"]), ("dns", 3))
        self.assertEqual(self.by(res)[A2]["lost"], 0)

    def test_trial_with_a_send_error_is_a_row_not_a_trial(self):
        """N5 / П-4: a fresh process, ``sendto`` EPERM to A2 — a row «blocked» (a real reason for A2), not a trial."""
        self.net.send_err[A2] = errno.EPERM
        p = self.prober(confirmed=False)
        res = p.series(1000.0)
        self.assertNotIn(A2, res["trial"])
        self.assertEqual(self.by(res)[A2]["na"], "blocked")

    def burst(self, n, lose_on=A2, ovfl_on=A2):
        def on_packet(sock, addr, pkt):
            seq = self.SEQ(pkt)
            if addr == ovfl_on and seq == 2 and n:
                for _ in range(n):
                    sock.put(1e7, b"\x00" * 60, "9.9.9.9")
            if addr == lose_on and seq == 2:
                return True
            if addr == ovfl_on and seq == 3:
                sock.put(2e7, self.net.answer(sock, addr, pkt), addr, ovfl=1)
                return True
            return False
        self.net.on_packet = on_packet
        return self.prober().series(1000.0)

    def test_overflow_edge_three_at_once(self):
        """N6 / П-4: exactly OVFL_BURST (3) packets at once — a full queue; 2 — not."""
        self.assertEqual(hp.OVFL_BURST, 3)
        res = self.burst(3)
        self.assertEqual((res["excl"], res["why"]), (True, "overflow"))
        self.setUp()
        res = self.burst(2)
        self.assertEqual((res["excl"], res["why"]), (False, None))
        self.assertEqual(self.by(res)[A2]["lost"], 1)

    def test_overflow_needs_a_lost_reply_of_its_own_socket(self):
        """N9: the counter grew on the socket of A2, the reply lost is of A1 — not an overflow, A1 loses 1."""
        res = self.burst(5, lose_on=A1, ovfl_on=A2)
        self.assertEqual((res["excl"], res["why"]), (False, None))
        self.assertEqual((self.by(res)[A1]["lost"], self.by(res)[A2]["lost"]), (1, 0))

    def test_raw_ipv4_any_growth_with_a_loss_is_an_overflow(self):
        """REV-sec-2 П-1: raw IPv4 checks no ICMP checksum — any growth of the counter with a reply lost is an
        overflow (a packet of ≥ 32 KB fills the queue alone); a ping socket with the same — a loss."""
        for raw in (True, False):
            self.setUp()
            if raw:
                self.net.deny[(V4, socket.SOCK_DGRAM)] = errno.EACCES
            res = self.burst(0)
            if raw:
                self.assertTrue(any(s.type == socket.SOCK_RAW for s in self.net.socks))
                self.assertEqual((res["excl"], res["why"]), (True, "overflow"))
            else:
                self.assertEqual((res["excl"], res["why"]), (False, None))
                self.assertEqual(self.by(res)[A2]["lost"], 1)

    def test_a_truncated_packet_marks_a_full_queue(self):
        """REV-sec-2 П-1: a truncated packet read on the socket (≥ 1500 bytes) — its queue was full at once."""
        def on_packet(sock, addr, pkt):
            seq = self.SEQ(pkt)
            if addr == A2 and seq == 2:
                sock.put(1e7, b"\x00" * 60, "9.9.9.9", flags=getattr(socket, "MSG_TRUNC", 0x20))
                return True
            if addr == A2 and seq == 3:
                sock.put(2e7, self.net.answer(sock, addr, pkt), addr, ovfl=1)
                return True
            return False
        self.net.on_packet = on_packet
        res = self.prober().series(1000.0)
        self.assertEqual((res["excl"], res["why"]), (True, "overflow"))

    def test_site_local_is_no_anchor(self):
        """REV-sec-2 П-3: fec0::/10 (deprecated site-local) is no anchor."""
        for bad in ("fec0::1", "feff::1"):
            self.assertIsNone(hp.valid_anchor(bad), bad)

    def test_unroutable_blocks_are_no_anchor_on_every_python(self):
        """REV-ns-a8-sec П-3: 3fff::/20 (documentation), 2001:20::/28 (ORCHIDv2), 192.88.99.0/24 (6to4 relay) … —
        refused by the explicit list even where ``is_global`` of this Python lets them through (3.9 vs 3.13+)."""
        import ipaddress
        bad = ("3fff::1", "2001:20::1", "2001:30::1", "2001:3::1", "2001:1::1", "5f00::1", "100::1", "192.88.99.1",
               "192.0.0.9", "192.0.0.10", "198.18.0.1", "2001:db8::1", "2002:101:101::1", "fd00::1", "0.1.2.3")
        good = ("1.1.1.1", "8.8.8.8", "77.88.8.8", "2606:4700:4700::1111", "2a02:6b8::feed:ff")
        always = property(lambda _ip: True)
        never = property(lambda _ip: False)
        for loose in (False, True):
            with contextlib.ExitStack() as stack:
                if loose:                                   # a Python whose lists let everything through
                    for cls in (ipaddress.IPv4Address, ipaddress.IPv6Address):
                        stack.enter_context(mock.patch.object(cls, "is_global", always))
                        stack.enter_context(mock.patch.object(cls, "is_reserved", never))
                for a in bad:
                    self.assertIsNone(hp.valid_anchor(a), (a, loose))
                for a in good:
                    self.assertEqual(hp.valid_anchor(a), a, (a, loose))

    def test_the_daily_try_of_the_anchors_apart(self):
        """REV-ns-a3-2 П-1: two anchors going to DNS in the same series get their own day (U[0; 1 h])."""
        self.net.on_packet = lambda sock, addr, pkt: addr in (A1, A2) and sock.proto != socket.IPPROTO_UDP
        p = self.prober(confirmed=False)
        for i in range(hp.DARK_TO_DNS):
            p.series(1000.0 + 30 * i)
        a, b = p.state[A1]["dns_at"], p.state[A2]["dns_at"]
        self.assertNotEqual(a, b)
        last = 1000.0 + 30 * (hp.DARK_TO_DNS - 1)
        self.assertTrue(all(last <= x <= last + hp.RETRY_SPREAD for x in (a, b)))


class AnchorKindsTest(unittest.TestCase):
    """REV-ns-a3-sec П-1: ``::a.b.c.d``, 6to4 and the local NAT64 prefix are no anchors on any Python (3.9 too)."""

    def test_compat_6to4_local_nat64(self):
        for bad in ("::7f00:1", "::a00:1", "::808:808", "2002:7f00:1::1", "2002:a00:1::1", "2002:808:808::1",
                    "64:ff9b:1::808:808", "64:ff9b:1::a00:1"):
            self.assertIsNone(hp.valid_anchor(bad), bad)
        self.assertEqual(hp.valid_anchor("64:ff9b::808:808"), "8.8.8.8")  # the well-known NAT64 prefix stays
        self.assertEqual(hp.valid_anchor("2606:4700:4700::1111"), "2606:4700:4700::1111")


class SpeedLockFutureTest(unittest.TestCase):
    """REV-ns-a3 П-2: an mtime from the future (the clock went back) is an old one, the probes do not stop."""

    def test_future_mtime_is_old(self):
        with tempfile.TemporaryDirectory() as d:
            lock = os.path.join(d, "speedtest.lock")
            with open(lock, "w"):
                pass
            locks = os.path.join(d, "locks")
            with open(locks, "w"):
                pass
            now = 1760000000.0
            os.utime(lock, (now + 3600, now + 3600))
            self.assertFalse(hp.speed_busy(lock, now=now, locks=locks))
            os.utime(lock, (now - 2, now - 2))
            self.assertTrue(hp.speed_busy(lock, now=now, locks=locks))
            os.utime(lock, (now, now))
            self.assertTrue(hp.speed_busy(lock, now=now, locks=locks))


class CanaryTest(unittest.TestCase):
    def test_once_per_600_and_not_busy(self):
        c = hp.Canary()
        self.assertIsNone(c.maybe(100.0, 60.0))
        self.assertIsNone(c.maybe(100.0, None))
        ms = c.maybe(100.0, 10.0)
        self.assertIsInstance(ms, float)
        self.assertGreater(ms, 0)
        self.assertIsNone(c.maybe(699.0, 10.0))
        self.assertIsNotNone(c.maybe(700.0, 49.9))
        self.assertIsNone(c.maybe(1300.0, 50.0))
        t = iter([1.0, 1.05])
        self.assertEqual(hp.Canary(lambda: next(t)).maybe(0, 0), 50.0)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux ping sockets")
class RealLoopbackTest(unittest.TestCase):
    def test_series_to_loopback(self):
        try:
            socket.socket(V4, socket.SOCK_DGRAM, socket.IPPROTO_ICMP).close()
        except OSError:
            self.skipTest("no right to a ping socket (ping_group_range)")
        with mock.patch.object(hp, "valid_anchors", lambda v, own=(): (list(v), 0)), \
                mock.patch.object(hp, "speed_busy", lambda *a, **k: False):
            p = hp.Prober(["127.0.0.1"], random.Random(1))
            res = p.series()
        a = res["anchors"][0]
        self.assertEqual((res["excl"], a["via"], a["n"], a["lost"], a["na"]), (False, "icmp", 3, 0, None))
        self.assertTrue(all(0 <= r < 100 for r in a["rtt"]))
        self.assertEqual(p.icmp[V4], "dgram")


if __name__ == "__main__":
    unittest.main()
