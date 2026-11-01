"""gw/hostpeople.py (SPEC-node-score §6.2 B17, NS-D26): «Связь с людьми» — the request and the filter of the kernel, the
parsing of foreign netlink bytes (a full answer, cut, without INET_DIAG_INFO, tcp_info shorter than the field, a live
sample), the choice of the sockets by the process of the unit and the port (nginx, sshd, the panel and the bot are not
taken; VLESS on 443 with «xray in front» — shared_port; SOCKS5, VLESS Reality, SSTP, OpenVPN TCP, ocserv — counted;
PPTP and the UDP inputs — not), «socket → unit» on a fake cgroup.procs and /proc/<pid>/fd, a restart of a unit in the
middle of the hour, the hour on hand numbers, the needless resends, «few», no rights / no sock_diag / cgroup v1 / old
kernel, the limits, the budget of a sample and no address or port of a person anywhere in what comes out."""

import errno
import json
import os
import random
import re
import struct
import time
from unittest import mock

from kaskad import util
from kaskad.gw import collect, hostpeople as HPL
from tests.helpers import RootCase

FIX = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "gw", "sockdiag")
H = 1800000000 - 1800000000 % 3600            # the start of an hour
MB = 1000 * 1000
PERSON_PORT = 61717                           # the port of «a person» in every fixture: it must never come out
PERSON_V4 = bytes([198, 51, 100, 7])          # RFC 5737 documentation addresses
OUR_V4 = bytes([192, 0, 2, 1])
XRAY, ACCEL, OCSERV, OVPN = ("kaskad-gw-xray.service", "kaskad-gw-accel.service", "kaskad-gw-ocserv.service",
                             "kaskad-gw-ovpn-tcp.service")
CG = {XRAY: 7001, ACCEL: 7002, OCSERV: 7003, OVPN: 7004}     # the cgroup ids of the units (fake kernel)
OTHER_CG = 1                                  # nginx, sshd, a process outside the units


def _al(n):
    return (n + 3) & ~3


def nlmsg(typ, payload, seq, flags=2):
    ln = 16 + len(payload)
    return struct.pack("=IHHII", ln, typ, flags, seq, 0) + payload + b"\0" * (_al(ln) - ln)


def attr(typ, payload):
    ln = 4 + len(payload)
    return struct.pack("=HH", ln, typ) + payload + b"\0" * (_al(ln) - ln)


def tcpinfo(length=232, rtt=40000, mrtt=30000, mss=1448, sent=0, retr=0, dsack=0):
    b = bytearray(length)
    b[0] = 1
    for fmt, off, v in (("=I", 16, mss), ("=I", 68, rtt), ("=I", 148, mrtt), ("=Q", 200, sent), ("=Q", 208, retr),
                        ("=I", 216, dsack)):
        if off + struct.calcsize(fmt) <= length:
            struct.pack_into(fmt, b, off, v)
    return bytes(b)


def addr16(fam, a4):
    return (a4 + b"\0" * 12) if fam == 2 else (b"\0" * 10 + b"\xff\xff" + a4)


def diag(sport, cookie, inode, info=b"", fam=10, state=1, seq=1, extra=True, cg=4242):
    """One inet_diag_msg as the kernel sends it: ``info`` — the bytes of tcp_info (None: no INET_DIAG_INFO);
    ``cg`` — INET_DIAG_CGROUP_ID (None: a kernel before 5.7)."""
    body = struct.pack("=BBBB", fam, state, 0, 0) + struct.pack(">HH", sport, PERSON_PORT)
    body += addr16(fam, OUR_V4) + addr16(fam, PERSON_V4) + struct.pack("=I", 0)
    body += struct.pack("=II", cookie & 0xFFFFFFFF, cookie >> 32) + struct.pack("=IIIII", 0, 0, 0, 0, inode)
    if extra:
        body += attr(8, b"\0")                        # INET_DIAG_SHUTDOWN
    if info is not None:
        body += attr(2, info)                         # INET_DIAG_INFO
    if extra and cg is not None:
        body += attr(21, struct.pack("=Q", cg))       # INET_DIAG_CGROUP_ID (5.7+)
    return nlmsg(20, body, seq)


def done(seq):
    return nlmsg(3, struct.pack("=i", 0), seq)


def error(seq, err):
    return nlmsg(2, struct.pack("=i", -err) + b"\0" * 16, seq, flags=0)


def bc_run(code, sport):
    """inet_diag_bc_run of the kernel (net/ipv4/inet_diag.c) for the port opcodes."""
    i, ln = 0, len(code)
    while ln > 0:
        c, yes, no = struct.unpack_from("=BBH", code, i)
        if c == 0:
            y = True
        elif c == 1:
            y = False
        elif c in (2, 3):
            p = struct.unpack_from("=BBH", code, i + 4)[2]
            y = sport >= p if c == 2 else sport <= p
        else:
            raise AssertionError("opcode %d" % c)
        step = yes if y else no
        i, ln = i + step, ln - step
    return ln == 0


def bc_audit(code):
    """inet_diag_bc_audit + valid_cc of the kernel: what it refuses with EINVAL."""
    def valid_cc(cc):
        ln, i = len(code), 0
        while ln >= 0:
            if cc > ln:
                return False
            if cc == ln:
                return True
            _c, yes, _no = struct.unpack_from("=BBH", code, i)
            if yes < 4 or yes & 3:
                return False
            ln, i = ln - yes, i + yes
        return False
    ln, i = len(code), 0
    while ln > 0:
        c, yes, no = struct.unpack_from("=BBH", code, i)
        min_len = 4
        if c in (2, 3):
            min_len += 4
            if ln < min_len:
                return False
        elif c not in (0, 1, 6):
            return False
        if c != 0:
            if no < min_len or no > ln + 4 or no & 3:
                return False
            if no < ln and not valid_cc(ln - no):
                return False
        if yes < min_len or yes > ln + 4 or yes & 3:
            return False
        i, ln = i + yes, ln - yes
    return ln == 0


class Kernel(object):
    """A fake sock_diag: ``socks[fam]`` — [(sport, cookie, inode, info bytes | None)]; the filter of ports as the
    kernel runs it; ``refuse_bc`` — EINVAL on a filter (an old kernel); ``per`` messages per datagram; ``cg_attr`` —
    INET_DIAG_CGROUP_ID in the answer (5.7+), ``owner(inode)`` its value; per family: ``silent`` (no answer),
    ``err`` (NLMSG_ERROR at once), ``mid`` (OSError after the first datagram)."""

    def __init__(self):
        self.socks = {2: [], 10: []}
        self.refuse_bc = False
        self.fail = None
        self.per = 3
        self.reqs = []
        self.cg_attr = True
        self.owner = lambda inode: OTHER_CG
        self.silent, self.err, self.mid = set(), {}, {}

    def add(self, sport, cookie, inode, fam=10, **kw):
        info = kw.pop("info", "x")
        self.socks[fam].append((sport, cookie, inode, tcpinfo(**kw) if info == "x" else info))

    def dump(self, req, deadline, budget):
        self.reqs.append(req)
        if self.fail is not None:
            raise self.fail
        seq = struct.unpack_from("=I", req, 8)[0]
        fam = req[16]
        if fam in self.silent:
            raise HPL._Stop()
        if fam in self.err:
            yield error(seq, self.err[fam])
            return
        code = None
        if len(req) > 72:
            ln, typ = struct.unpack_from("=HH", req, 72)
            assert typ == 1 and ln == len(req) - 72
            code = req[76:]
            assert bc_audit(code), "the kernel would refuse the filter"
            if self.refuse_bc:
                yield error(seq, 22)
                return
        msgs = [diag(s, c, i, info, fam=fam, seq=seq, cg=self.owner(i) if self.cg_attr else None)
                for s, c, i, info in self.socks.get(fam, []) if code is None or bc_run(code, s)]
        for k in range(0, len(msgs), self.per):
            yield b"".join(msgs[k:k + self.per])
            if fam in self.mid:
                raise OSError(self.mid[fam], "in the middle of the dump")
        yield done(seq)


def unit_map(units):
    """pids/inodes/readable/cgroup readers and the owner of a socket for the fake kernel from {unit: {pid: [inodes] |
    None (no rights)}}; a unit not in the map has no cgroup."""
    def pids(unit):
        u = units.get(unit)
        return None if u is None else sorted(u)

    def cgroup(unit):
        return CG[unit] if unit in units else "no_cgroup"

    def owner(inode):
        for unit, u in units.items():
            if any(inode in (v or ()) for v in u.values()):
                return CG[unit]
        return OTHER_CG

    def inodes(pid, into, deadline=None):
        for u in units.values():
            if pid in u:
                if u[pid] is None:
                    return False
                into.update(u[pid])
        return True

    def readable(unit):
        u = units.get(unit)
        return u is not None and all(v is not None for v in u.values())
    return pids, inodes, readable, cgroup, owner


def people(k, units, cgv=2, **kw):
    pids, inodes, readable, cgroup, owner = unit_map(units)
    k.owner = owner
    args = dict(dump=k.dump, cgv=lambda: cgv, pids=pids, inodes=inodes, readable=readable, cgroup=cgroup)
    args.update(kw)
    return HPL.People(**args)


def st_of(**inbounds):
    return {"gateway": {"enabled": True, "inbounds": inbounds}}


class OffsetsTest(RootCase):
    def test_tcp_info_offsets_are_those_of_the_header(self):
        size = {"__u8": 1, "__u16": 2, "__u32": 4, "__u64": 8}
        off, at = 0, {}
        with open(os.path.join(FIX, "tcp_info_members.txt"), encoding="utf-8") as f:
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                t, name = line.split()
                n = size[t]
                off = (off + n - 1) // n * n
                at[name] = off
                off += n
        self.assertEqual(at["tcpi_snd_mss"], HPL.TI_SND_MSS)
        self.assertEqual(at["tcpi_rtt"], HPL.TI_RTT)
        self.assertEqual(at["tcpi_min_rtt"], HPL.TI_MIN_RTT)
        self.assertEqual(at["tcpi_bytes_sent"], HPL.TI_BYTES_SENT)
        self.assertEqual(at["tcpi_bytes_retrans"], HPL.TI_BYTES_RETRANS)
        self.assertEqual(at["tcpi_dsack_dups"], HPL.TI_DSACK_DUPS)
        self.assertEqual(at["tcpi_snd_wnd"] + 4, 232)  # the length a 5.4 kernel gives (the fixtures use it)


class RequestTest(RootCase):
    def test_header_and_body(self):
        r = HPL.request(10, 7)
        ln, typ, flags, seq, pid = struct.unpack_from("=IHHII", r)
        self.assertEqual((ln, typ, flags, seq, pid), (len(r), 20, 0x301, 7, 0))
        self.assertEqual(len(r), 16 + 56)
        fam, proto, ext, pad, states = struct.unpack_from("=BBBBI", r, 16)
        self.assertEqual((fam, proto, ext, pad, states), (10, 6, 2, 0, 2))

    def test_the_filter_takes_exactly_the_ports_and_the_kernel_accepts_it(self):
        rnd = random.Random(5)
        for n in range(1, 9):
            ports = sorted(rnd.sample(range(1, 65536), n))
            code = HPL.request(2, 1, ports)[76:]
            self.assertTrue(bc_audit(code), ports)
            for p in ports:
                self.assertTrue(bc_run(code, p), (ports, p))
            for p in [0, 1, 22, 80, 443, 1080, 8443, 65535] + [rnd.randrange(65536) for _ in range(300)]:
                self.assertEqual(bc_run(code, p), p in ports, (ports, p))


    def test_the_filter_of_the_remote_port(self):
        """D-144 (8c over iperf3, ``bench.suite.iperf_tcp``): ``dst`` — the same code with D_GE / D_LE (4, 5 of
        inet_diag.h) instead of S_GE / S_LE; with them read as the local ones the kernel's run and audit accept
        exactly the port."""
        for ports in ([5201], [5201, 5203], [3333]):
            code = HPL.request(2, 1, ports, dst=True)[76:]
            ops = [struct.unpack_from("=BBH", code, i)[0] for i in range(0, len(code), 4)]
            self.assertTrue({4, 5} <= set(ops) and not {2, 3} & set(ops), ops)
            as_src = bytearray(code)
            for i in range(0, len(code), 4):
                if as_src[i] in (4, 5) and i + 4 < len(code):
                    as_src[i] -= 2
            self.assertEqual(bytes(as_src), HPL.request(2, 1, ports)[76:])          # the layout of the local filter
            self.assertTrue(bc_audit(bytes(as_src)))
            for p in (0, 22, 3333, 5201, 5202, 5203, 65535):
                self.assertEqual(bc_run(bytes(as_src), p), p in ports, (ports, p))
        self.assertEqual(HPL.request(2, 1, [5201]), HPL.request(2, 1, [5201], dst=False))


class ParseTest(RootCase):
    def test_a_full_answer(self):
        buf = diag(8443, 5, 900, tcpinfo(sent=123456, retr=1000, dsack=2, rtt=48000, mrtt=31000, mss=1400), seq=9) \
            + diag(1080, (7 << 32) | 3, 901, tcpinfo(length=248), fam=2, seq=9)
        sink = []
        self.assertEqual(HPL.parse_datagram(buf, 9, sink, 100), "more")
        self.assertEqual(HPL.parse_datagram(done(9), 9, sink, 100), "done")
        self.assertEqual(sink[0], (8443, 5, 900, (48000, 31000, 1400, 123456, 1000, 2), 4242))
        self.assertEqual(sink[1][:3], (1080, (7 << 32) | 3, 901))

    def test_cut_short_and_lengths_that_do_not_fit(self):
        good = diag(8443, 1, 10, tcpinfo(), seq=1)
        second = diag(8443, 2, 11, tcpinfo(), seq=1)
        sink = []
        self.assertEqual(HPL.parse_datagram(good + second[:-30], 1, sink, 100), "bad")
        self.assertEqual([s[1] for s in sink], [1])           # what came before the cut is kept
        for bad in (good[:10], struct.pack("=IHHII", 8, 20, 0, 1, 0), struct.pack("=IHHII", 9999, 20, 0, 1, 0)):
            self.assertEqual(HPL.parse_datagram(bad, 1, [], 100), "bad")

    def test_without_info_short_info_cut_attribute(self):
        sink = []
        HPL.parse_datagram(diag(8443, 1, 10, None, seq=1), 1, sink, 9)
        HPL.parse_datagram(diag(8443, 2, 11, tcpinfo(length=192), seq=1), 1, sink, 9)   # 4.15: no bytes_sent
        HPL.parse_datagram(diag(8443, 3, 12, tcpinfo(length=219), seq=1), 1, sink, 9)   # one byte short of dsack_dups
        HPL.parse_datagram(diag(8443, 4, 13, tcpinfo(length=220), seq=1), 1, sink, 9)
        self.assertEqual([s[3] if not isinstance(s[3], tuple) else "ok" for s in sink], [None, "old", "old", "ok"])
        # an attribute claiming more than its message: the message stays, its info is not trusted
        m = bytearray(diag(8443, 5, 14, tcpinfo(), seq=1, extra=False))
        struct.pack_into("=H", m, 16 + 72, 4000)
        sink = []
        self.assertEqual(HPL.parse_datagram(bytes(m), 1, sink, 9), "more")
        self.assertEqual(sink, [(8443, 5, 14, None, None)])
        m = bytearray(diag(8443, 6, 15, tcpinfo(), seq=1, extra=False))
        struct.pack_into("=H", m, 16 + 72, 2)                  # shorter than an attribute header
        sink = []
        HPL.parse_datagram(bytes(m), 1, sink, 9)
        self.assertEqual(sink, [(8443, 6, 15, None, None)])

    def test_the_cgroup_id_and_counters_that_cannot_be(self):
        sink = []
        HPL.parse_datagram(diag(8443, 1, 10, tcpinfo(), seq=1, cg=0x123456789A), 1, sink, 9)
        HPL.parse_datagram(diag(8443, 2, 11, tcpinfo(), seq=1, cg=None), 1, sink, 9)
        self.assertEqual([s[4] for s in sink], [0x123456789A, None])
        m = nlmsg(20, diag(8443, 3, 12, None, seq=1, extra=False)[16:] + attr(21, b"\1\2\3\4"), 1)
        sink = []
        HPL.parse_datagram(m, 1, sink, 9)                       # an attribute too short for a u64: not taken
        self.assertEqual(sink[0][4], None)
        for kw in ({"sent": 2 ** 64 - 1}, {"sent": 2 ** 63}, {"retr": 2 ** 64 - 1}, {"dsack": 0xFFFFFFFF},
                   {"mss": 0xFFFFFFFF}):
            b = tcpinfo(**kw)
            self.assertEqual(HPL.tcp_fields(b, 0, len(b)), "invalid", kw)
        b = tcpinfo(sent=2 ** 63 - 1)
        self.assertEqual(HPL.tcp_fields(b, 0, len(b))[3], 2 ** 63 - 1)

    def test_other_seq_error_state_room(self):
        sink = []
        self.assertEqual(HPL.parse_datagram(diag(8443, 1, 10, tcpinfo(), seq=2) + done(2), 1, sink, 9), "more")
        self.assertEqual(sink, [])
        self.assertEqual(HPL.parse_datagram(error(1, 22), 1, sink, 9), 22)
        self.assertEqual(HPL.parse_datagram(diag(8443, 1, 10, tcpinfo(), state=8, seq=1), 1, sink, 9), "more")
        self.assertEqual(sink, [])                              # not ESTABLISHED
        buf = b"".join(diag(8443, i, 10 + i, tcpinfo(), seq=1) for i in range(5))
        self.assertEqual(HPL.parse_datagram(buf, 1, sink, 3), "full")
        self.assertEqual(len(sink), 3)
        self.assertEqual(HPL.parse_datagram(nlmsg(1, b"", 1), 1, [], 9), "more")   # NLMSG_NOOP

    def test_random_bytes_never_raise(self):
        rnd = random.Random(17)
        base = diag(8443, 1, 10, tcpinfo(), seq=1) + diag(1080, 2, 11, tcpinfo(), seq=1) + done(1)
        for _ in range(3000):
            b = bytearray(base)
            for _ in range(rnd.randint(1, 8)):
                b[rnd.randrange(len(b))] = rnd.randrange(256)
            cut = bytes(b[:rnd.randint(0, len(b))])
            sink = []
            got = HPL.parse_datagram(cut, 1, sink, 50)
            self.assertTrue(got in ("more", "done", "bad", "full") or isinstance(got, int))
            for s in sink:
                self.assertEqual(len(s), 5)
        for _ in range(500):
            HPL.parse_datagram(bytes(rnd.randrange(256) for _ in range(rnd.randint(0, 400))), 1, [], 50)

    def test_live_samples(self):
        """Datagrams taken from real kernels (k3/stand B17 capture: loopback sockets of a test process, the addresses
        and the remote port rewritten to RFC 5737 / 61717 afterwards; README of the folder)."""
        names = sorted(n for n in os.listdir(FIX) if n.startswith("live-") and n.endswith(".hex"))
        self.assertTrue(names, "no live sample in tests/fixtures/gw/sockdiag")
        for name in names:
            with open(os.path.join(FIX, name), encoding="ascii") as f:
                lines = [ln.split() for ln in f if ln.strip()]
            self.assertEqual(lines[0][0], "#", name)
            meta = dict(x.split("=", 1) for x in lines[0][1:])
            seq, port = int(meta["seq"]), int(meta["port"])
            sink, status = [], None
            for ln in lines[1:]:
                status = HPL.parse_datagram(bytes.fromhex(ln[0]), seq, sink, 1000)
                if status != "more":
                    break
            self.assertEqual(status, "done", name)
            self.assertEqual(len(sink), int(meta["socks"]), name)
            for sport, cookie, inode, info, cg in sink:
                self.assertEqual(sport, port)
                self.assertTrue(cookie and inode)
                self.assertEqual(cg, 0x1057, name)                  # INET_DIAG_CGROUP_ID of the capture (6.8)
                self.assertIsInstance(info, tuple, name)
                rtt, mrtt, mss, sent, retr, dsack = info
                self.assertGreater(rtt, 0)
                self.assertGreater(mss, 0)
                self.assertLessEqual(mrtt, rtt * 10)
                self.assertGreaterEqual(sent, int(meta["min_sent"]), name)
            doc = {addr16(2, OUR_V4), addr16(2, PERSON_V4), addr16(10, OUR_V4), addr16(10, PERSON_V4),
                   bytes.fromhex("20010db8000000000000000000000001"), bytes.fromhex("20010db8000000000000000000000007")}
            for ln in lines[1:]:                                    # the fixture holds no real address
                raw = bytes.fromhex(ln[0])
                o = 0
                while o + 16 <= len(raw):
                    mlen, typ = struct.unpack_from("=IH", raw, o)
                    if typ == 20:
                        m = o + 16
                        self.assertEqual(struct.unpack_from(">H", raw, m + 6)[0], PERSON_PORT)
                        self.assertIn(raw[m + 8:m + 24], doc)
                        self.assertIn(raw[m + 24:m + 40], doc)
                    o += _al(mlen)


class PlanTest(RootCase):
    def test_inputs(self):
        st = st_of(vless={"port": 8443}, socks5={"port": 1080}, sstp={}, pptp={}, awg2={"port": 51820},
                   openvpn={"udp_port": 1194, "tcp_port": 1443}, ocserv={"port": 4443}, wg={"enabled": False},
                   ikev2={}, l2tp={}, future={"port": 5})
        p = HPL.plan(st)
        self.assertEqual(p["vless"], (XRAY, 8443))
        self.assertEqual(p["socks5"], (XRAY, 1080))
        self.assertEqual(p["sstp"], (ACCEL, 4433))
        self.assertEqual(p["ocserv"], (OCSERV, 4443))
        self.assertEqual(p["openvpn"], (OVPN, 1443))
        for k in ("pptp", "awg2", "ikev2", "l2tp"):
            self.assertEqual(p[k], "udp", k)
        self.assertNotIn("wg", p)                               # switched off
        self.assertEqual(p["future"], "no_data")

    def test_vless_on_443_in_front_of_the_site_is_shared(self):
        st = st_of(vless={"port": 443}, openvpn={"udp_port": 1194})
        self.assertEqual(HPL.plan(st, fronts=True)["vless"], "shared_port")
        self.assertEqual(HPL.plan(st, fronts=False)["vless"], (XRAY, 443))   # Reality on 443 without a site
        self.assertEqual(HPL.plan(st)["openvpn"], "udp")

    def test_openvpn_with_udp_and_tcp_tells_the_udp_part(self):
        """П-4: OpenVPN on UDP and TCP — the row also says its UDP part is not measured."""
        p = HPL.plan(st_of(openvpn={"udp_port": 1194, "tcp_port": 1443}))
        self.assertEqual((p["openvpn"], p["openvpn_udp"]), ((OVPN, 1443), "udp"))
        p = HPL.plan(st_of(openvpn={"tcp_port": 1443}))         # no udp_port: the default 1194 is on
        self.assertEqual(p["openvpn_udp"], "udp")
        p = HPL.plan(st_of(openvpn={"udp_port": 0, "tcp_port": 1443}))
        self.assertNotIn("openvpn_udp", p)
        p = HPL.plan(st_of(openvpn={"udp_port": 1194, "tcp_port": 0}))
        self.assertEqual(p, {"openvpn": "udp"})

    def test_gateway_off_or_no_inputs(self):
        self.assertEqual(HPL.plan({"gateway": {"enabled": False, "inbounds": {"vless": {"port": 8443}}}}), {})
        self.assertEqual(HPL.plan({}), {})
        self.assertEqual(HPL.plan({"gateway": {"inbounds": []}}), {})

    def test_fronts_443_by_the_deployed_config(self):
        from kaskad.gw import xray
        path = xray.config_path()
        self.assertFalse(HPL.fronts_443())
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cfg = {"inbounds": [{"protocol": "vless", "port": 443, "streamSettings": {"security": "tls"}}]}
        with open(path, "w") as f:
            json.dump(cfg, f)
        self.assertTrue(HPL.fronts_443())
        cfg["inbounds"][0]["streamSettings"]["security"] = "reality"
        with open(path, "w") as f:
            json.dump(cfg, f)
        os.utime(path, ns=(1, 10 ** 18))
        self.assertFalse(HPL.fronts_443())


class PeopleTest(RootCase):
    def setUp(self):
        super().setUp()
        self.k = Kernel()
        self.units = {XRAY: {100: set(range(1000, 2000))}, ACCEL: {200: set(range(3000, 3100))},
                      OCSERV: {300: {4000}, 301: {4001}, 302: {4002}}, OVPN: {400: {5000, 5001}}}
        self.p = people(self.k, self.units)
        self.plan = {"vless": (XRAY, 8443), "socks5": (XRAY, 1080), "sstp": (ACCEL, 4433), "pptp": "udp",
                     "awg2": "udp", "ocserv": (OCSERV, 4443), "openvpn": (OVPN, 1443)}
        self.mono = 1000.0

    def at(self, minute, plan=None):
        """A sample at minute ``minute`` of the hour H (60 s of mono per minute)."""
        self.assertTrue(self.p.step(self.plan if plan is None else plan, self.mono + 60 * minute, H + 60 * minute))

    def row(self):
        rows = self.p.take()
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def test_choice_by_process_and_port(self):
        self.at(0)                                              # the first sample: nothing yet
        for i in range(25):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB, rtt=20000)        # VLESS Reality on its port
        self.k.add(1080, 50, 1100, sent=3 * MB)                               # SOCKS5 of the same xray
        self.k.add(4433, 60, 3000, sent=3 * MB)                               # SSTP
        self.k.add(4443, 70, 4001, sent=3 * MB)                               # a worker of ocserv
        self.k.add(1443, 80, 5000, sent=3 * MB)                               # OpenVPN TCP
        self.k.add(8443, 90, 777, sent=900 * MB)               # on the port of VLESS but not of xray: not taken
        self.k.add(443, 91, 1500, sent=900 * MB)               # nginx on 443 (an inode of xray even): not a port of ours
        self.k.add(22, 92, 1501, sent=900 * MB)                # sshd
        self.at(1)
        self.at(61)
        r = self.row()
        self.assertEqual(r["conns"], 29)
        self.assertEqual(r["sent"], 29 * 3 * MB)
        self.assertEqual(r["inputs"], {"vless": "ok", "socks5": "ok", "sstp": "ok", "ocserv": "ok",
                                       "openvpn": "ok", "pptp": "udp", "awg2": "udp"})
        self.assertIsNone(r["why"])

    def test_without_the_filter_the_ports_are_checked_here(self):
        self.k.refuse_bc = True
        self.at(0)
        self.assertFalse(self.p.bc)                             # EINVAL once: the dump without the filter
        self.k.refuse_bc = False
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB)
        self.k.add(22, 99, 1999, sent=900 * MB)                 # sshd, in the dump now, inode «of xray»
        self.at(1)
        self.assertTrue(all(len(r) == 72 for r in self.k.reqs[-2:]))
        self.at(61)
        r = self.row()
        self.assertEqual((r["conns"], r["sent"]), (20, 60 * MB))

    def test_mapping_on_a_fake_cgroup_and_proc(self):
        self.write("/sys/fs/cgroup/system.slice/%s/cgroup.procs" % XRAY, "100\n101\n")
        self.write("/sys/fs/cgroup/system.slice/%s/cgroup.procs" % ACCEL, "200\n")
        for pid, links in ((100, ["socket:[1000]", "socket:[1001]", "pipe:[5]", "/dev/null"]),
                           (101, ["socket:[1002]"]), (200, ["socket:[3000]"]), (999, ["socket:[777]"])):
            d = self.path("/proc/%d/fd" % pid)
            os.makedirs(d)
            for i, t in enumerate(links):
                os.symlink(t, os.path.join(d, str(i)))
        got = set()
        self.assertEqual(HPL.unit_pids(XRAY), [100, 101])
        self.assertEqual(HPL.unit_pids(OCSERV), [])             # not running: no cgroup
        self.assertTrue(HPL.sock_inodes(100, got) and HPL.sock_inodes(101, got) and HPL.sock_inodes(4242, got))
        self.assertEqual(got, {1000, 1001, 1002})
        cg_dir = self.path("/sys/fs/cgroup/system.slice/%s" % XRAY)
        self.assertEqual(HPL.unit_cgroup(XRAY), os.stat(cg_dir).st_ino)
        self.assertEqual(HPL.unit_cgroup(OCSERV), "no_cgroup")
        ino = {XRAY: HPL.unit_cgroup(XRAY), ACCEL: HPL.unit_cgroup(ACCEL)}
        self.k.owner = lambda i: ino[XRAY] if i in (1000, 1001, 1002) else ino[ACCEL] if i == 3000 else OTHER_CG
        p = HPL.People(dump=self.k.dump, cgv=lambda: 2)
        p.step(self.plan, 0.0, H)
        self.k.add(8443, 1, 1001, sent=3 * MB)
        self.k.add(8443, 2, 777, sent=3 * MB)                   # a process outside the unit (pid 999)
        self.k.add(4433, 3, 3000, sent=3 * MB)
        p.step(self.plan, 60.0, H + 60)
        self.assertEqual(sorted((c[0], k) for k, c in p.conns.items()), [("sstp", 3), ("vless", 1)])
        os.chmod(self.path("/proc/200/fd"), 0)
        try:
            if os.access(self.path("/proc/200/fd"), os.R_OK):
                self.skipTest("root reads anything")
            self.assertFalse(HPL.sock_inodes(200, set()))
        finally:
            os.chmod(self.path("/proc/200/fd"), 0o700)

    def test_a_restart_of_the_unit_in_the_middle_of_the_hour(self):
        self.at(0)
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=2 * MB)
        self.at(1)
        for i in range(20):                                     # more sent before the restart
            self.k.socks[10][i] = (8443, 10 + i, 1000 + i, tcpinfo(sent=3 * MB))
        self.at(20)
        self.k.socks[10] = []
        self.units[XRAY] = {150: set(range(6000, 6100))}        # a new process, new sockets
        for i in range(20):
            self.k.add(8443, 500 + i, 6000 + i, sent=1 * MB)
        self.at(21)
        self.at(61)
        r = self.row()
        self.assertEqual(r["conns"], 40)
        self.assertEqual(r["sent"], 20 * 3 * MB + 20 * 1 * MB)

    def test_the_hour_on_hand_numbers(self):
        self.at(0)
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB, retr=30000, dsack=5, mss=1000, rtt=(10 + i) * 1000,
                       mrtt=(5 + i) * 1000)
        self.k.add(8443, 99, 1999, sent=60000, rtt=900000)     # under 64 KB: no vote
        self.at(1)
        self.at(61)
        r = self.row()
        self.assertEqual(r["ts"], H)
        self.assertEqual(r["conns"], 20)
        self.assertEqual((r["rtt_med"], r["rtt_p90"], r["rtt_min_med"]), (19.5, 28.0, 14.5))
        self.assertEqual((r["sent"], r["retr"], r["dsack"]), (60 * MB, 600000, 100000))
        s = HPL.summary([r])
        self.assertEqual((s["rtt_ms"], s["best_ms"], s["conns"], s["hours"]), (19.5, 14.5, 20, 1))
        self.assertEqual(s["retr_pct"], round(100.0 * 500000 / (60 * MB), 2))

    def test_needless_resends_never_below_zero_and_counters_going_back(self):
        self.at(0)
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB, retr=1000, dsack=50, mss=1400)
        self.at(1)
        r = self.p.row()
        self.assertEqual(r["dsack"], 20 * 50 * 1400)
        self.assertEqual(HPL.summary([r])["retr_pct"], 0.0)
        self.k.socks[10][0] = (8443, 10, 1000, tcpinfo(sent=100))         # cannot happen; the base starts again
        self.at(2)
        self.assertEqual(self.p.conns[10][1], 100)

    def test_the_base_first_sample_gap_and_new_connections(self):
        self.k.add(8443, 1, 1000, sent=10 ** 10)                # an old connection: its past is not this hour
        for i in range(20):
            self.k.add(8443, 10 + i, 1010 + i, sent=3 * MB)
        self.at(0)
        self.k.socks[10][0] = (8443, 1, 1000, tcpinfo(sent=10 ** 10 + 200000))
        self.k.add(8443, 2, 1001, sent=5 * MB)                  # new in a continuous sample: all of it counts
        self.at(1)
        self.k.add(8443, 3, 1002, sent=7 * MB)                  # first seen after a gap: its past is unknown
        self.at(10)
        c = self.p.conns
        self.assertEqual([c[k][4] - c[k][1] for k in (1, 2, 3)], [200000, 5 * MB, 0])
        r = self.p.row()
        self.assertEqual(r["why"], "few")                       # the 20 sent nothing after their first sight

    def test_few(self):
        self.at(0)
        for i in range(19):
            self.k.add(8443, 10 + i, 1000 + i, sent=30 * MB)
        self.at(1)
        self.at(61)
        r = self.row()
        self.assertEqual(r["why"], "few")
        for k in ("conns", "rtt_med", "rtt_p90", "rtt_min_med", "sent", "retr", "dsack"):
            self.assertIsNone(r[k], k)
        self.assertEqual(r["inputs"]["vless"], "few")
        self.assertEqual(HPL.summary([r])["why"], "few")
        self.assertIsNone(HPL.summary([]))

    def test_no_tcp_input(self):
        """Only VLESS on 443 with the site has TCP: «the input shares port 443 with the website» (З-4), not «no
        input with TCP»; nothing with TCP at all — no_tcp_inbound."""
        self.p.step({"awg2": "udp", "vless": "shared_port"}, 0.0, H)
        self.p.step({"awg2": "udp", "vless": "shared_port"}, 3700.0, H + 3700)
        r = self.row()
        self.assertEqual((r["why"], r["inputs"]), ("shared_443", {"awg2": "udp", "vless": "shared_port"}))
        self.p.step({"awg2": "udp", "pptp": "udp"}, 7300.0, H + 7300)
        self.assertEqual(self.row()["why"], "shared_443")      # the hour H+1 was of the first plan
        self.p.step({"awg2": "udp", "pptp": "udp"}, 10900.0, H + 10900)
        r = self.row()
        self.assertEqual((r["why"], r["inputs"]), ("no_tcp_inbound", {"awg2": "udp", "pptp": "udp"}))
        self.assertEqual(self.k.reqs, [])
        self.assertEqual(HPL.summary([r])["why"], "no_tcp_inbound")

    def _reason(self, p):
        p.step(self.plan, 0.0, H)
        p.step(self.plan, 60.0, H + 60)
        p.step(self.plan, 3700.0, H + 3700)
        r = p.take()[0]
        self.assertIsNone(r["conns"])
        self.assertEqual([r["inputs"][k] for k in ("vless", "sstp", "pptp")], ["no_data", "no_data", "udp"])
        return r["why"]

    def test_no_sock_diag_no_netlink(self):
        self.k.fail = OSError(93, "Protocol not supported")
        self.assertEqual(self._reason(people(self.k, self.units)), "no_diag")
        with mock.patch.object(HPL.socket, "AF_NETLINK", None, create=True):    # macOS, a kernel without netlink
            self.assertEqual(self._reason(HPL.People(cgv=lambda: 2)), "no_diag")

    def test_cgroup_v1_no_rights_old_kernel(self):
        self.assertEqual(self._reason(people(self.k, self.units, cgv=1)), "old_cgroup")
        self.assertEqual(self.k.reqs, [])                       # the path of v1 is not guessed: nothing read
        for i in range(30):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB)
            self.k.add(4433, 100 + i, 3000 + i, sent=3 * MB)
        locked = dict((u, dict((pid, None) for pid in v)) for u, v in self.units.items())
        if self.k.cg_attr:                                      # the cgroup is enough: /proc is not read at all
            self.assertEqual(self._reason(people(self.k, self.units, cgroup=lambda u: None)), "no_access")
        else:
            self.assertEqual(self._reason(people(self.k, locked)), "no_access")
        self.k.socks[10] = []
        for i in range(30):
            self.k.add(8443, 10 + i, 1000 + i, info=tcpinfo(length=192))
            self.k.add(4433, 100 + i, 3000 + i, info=tcpinfo(length=192))
        self.assertEqual(self._reason(people(self.k, self.units)), "old_kernel")

    def test_off_forgets_the_hour(self):
        self.at(0)
        self.k.add(8443, 1, 1000, sent=3 * MB)
        self.at(1)
        self.assertTrue(self.p.conns)
        self.assertFalse(self.p.step(None, self.mono + 200, H + 200))
        self.assertEqual((self.p.conns, self.p.hour), ({}, None))
        self.assertTrue(self.p.step(self.plan, self.mono + 230, H + 230))
        self.assertFalse(self.p.step(self.plan, self.mono + 250, H + 250))     # once a minute
        self.assertEqual(self.p.take(), [])

    def test_limits(self):
        self.at(0)
        for i in range(30):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB)
        with mock.patch.object(HPL, "MAX_SOCKS", 7), mock.patch.object(HPL, "MAX_TRACK", 5):
            self.at(1)
        self.assertEqual(len(self.p.conns), 5)
        self.assertEqual(self.p.over, 1)
        self.at(61)
        self.assertEqual(self.row()["partial"], 1)              # the hour tells a sample saw not everything
        self.assertLessEqual(max(HPL.MAX_SOCKS, HPL.MAX_TRACK), 20000)   # sec З-1: bounded memory

    def test_a_cut_sample_is_no_base_for_the_next(self):
        """A socket a cut sample missed must not look new in the next one (its whole past would come into the hour)."""
        self.at(0)
        for i in range(10):
            self.k.add(8443, 10 + i, 1000 + i, sent=900 * MB)
        with mock.patch.object(HPL, "MAX_SOCKS", 4):
            self.at(1)
        self.assertFalse(self.p.whole)
        self.at(2)                                              # the six missed ones: their past is not counted
        self.assertEqual(sorted(c[4] - c[1] for c in self.p.conns.values()), [0] * 6 + [900 * MB] * 4)

    def test_a_kernel_that_does_not_answer_reads_nothing(self):
        def silent(req, deadline, budget):
            raise HPL._Stop()
            yield b""
        self.p.dump = silent
        self.at(0)
        self.at(1)
        self.assertEqual(self.p.read, {})
        self.assertEqual(self.p.why, "no_diag")

    def test_the_rows_wait_and_are_kept_in_order(self):
        rows = [{"ts": H + 3600 * i} for i in range(HPL.KEEP_ROWS + 5)]
        self.p.keep(rows[:3])
        self.p.rows.append(rows[3])
        self.p.keep(self.p.take())                              # no writer: back, in their order
        for r in rows[4:]:
            self.p.rows.append(r)
        got = self.p.take()
        self.assertEqual(len(got), HPL.KEEP_ROWS)              # the oldest dropped
        self.assertEqual([got[0]["ts"], got[-1]["ts"]], [rows[5]["ts"], rows[-1]["ts"]])

    def test_no_address_or_port_of_a_person_comes_out(self):
        logged = []
        with mock.patch.object(util, "log_action", lambda *a, **k: logged.append(a)):
            self.at(0)
            for i in range(40):
                self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB + i, fam=10 if i % 2 else 2)
            for m in range(1, 70, 7):
                self.at(m)
            self.k.fail = OSError(1, "EPERM")
            self.at(71)
        rows = self.p.take()
        mem = dict((k, v) for k, v in vars(self.p).items() if not callable(v))
        text = json.dumps(rows, sort_keys=True) + repr(mem)
        for bad in ("198.51.100", "192.0.2", "c6336407", "::ffff", str(PERSON_PORT)):
            self.assertNotIn(bad, text)
        self.assertFalse(re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", json.dumps(rows)))
        self.assertEqual(logged, [])
        for r in rows:
            self.assertEqual(set(r), {"ts", "conns", "rtt_med", "rtt_p90", "rtt_min_med", "sent", "retr", "dsack",
                                      "why", "inputs", "partial"})
            self.assertTrue(all(isinstance(v, str) and v in ("ok", "few", "shared_port", "udp", "no_data")
                                for v in r["inputs"].values()))

    def test_the_budget_of_a_sample(self):
        """2000 connections of the people (two families): a sample within a few ms of CPU — far inside the budget of
        the collector (health.cost ≤ 0,3 % of a core = 180 ms a minute for all of its new part)."""
        self.units[XRAY] = {100: set(range(1000, 3500))}
        built = {}
        for fam in (2, 10):                                     # the datagrams are built once: only our side is timed
            msgs = [diag(8443, 10 + i, 1000 + i, tcpinfo(sent=3 * MB + i), fam=fam, seq=0,
                         cg=CG[XRAY] if self.k.cg_attr else None) for i in range(0 if fam == 2 else 1, 2000, 2)]
            grams = [bytearray(b"".join(msgs[j:j + 100])) for j in range(0, len(msgs), 100)]
            built[fam] = [(g, [o for o in self.offsets(g)]) for g in grams] + [(bytearray(done(0)), [0])]

        def dump(req, deadline, budget):
            seq = struct.unpack_from("=I", req, 8)[0]
            for g, offs in built[req[16]]:
                for o in offs:
                    struct.pack_into("=I", g, o + 8, seq)
                yield bytes(g)
        self.p.dump = dump
        self.at(0)
        cost = []
        for m in range(1, 6):
            t0 = time.process_time()
            self.at(m)
            cost.append(time.process_time() - t0)
        self.assertEqual(len(self.p.conns), 2000)
        self.assertLess(sorted(cost)[2], 0.060, cost)          # median; the real one is ≈ 5–15 ms

    @staticmethod
    def offsets(g):
        o = 0
        while o < len(g):
            yield o
            o += _al(struct.unpack_from("=I", g, o)[0])

    # -- круг 2 (REV-ns-b17 З-1…З-7, П-1, П-2, П-4; REV-ns-b17-sec З-1, П-2) --
    def test_two_hours_in_a_row(self):
        """З-2: the second row holds only the bytes of its hour (the base moves at the end of an hour), and the
        connections gone in the first hour do not live in the second."""
        self.at(0)
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB)
        for i in range(5):
            self.k.add(8443, 100 + i, 1100 + i, sent=2 * MB)
        self.at(1)
        self.k.socks[10] = [(8443, 10 + i, 1000 + i, tcpinfo(sent=6 * MB)) for i in range(20)]   # the 5 left
        self.at(30)
        self.at(59)
        self.k.socks[10] = [(8443, 10 + i, 1000 + i, tcpinfo(sent=9 * MB)) for i in range(20)]
        self.k.silent = {2}                                     # a sample not whole: no cleaning of the gone ones in it
        self.at(60)                                             # hour 2: hour 1 closed with 6 MB each
        self.k.silent = set()
        r1 = self.row()
        self.assertEqual((r1["ts"], r1["conns"], r1["sent"]), (H, 25, 20 * 6 * MB + 5 * 2 * MB))
        self.assertEqual(sorted(self.p.conns), list(range(10, 30)))   # the gone ones are not carried
        self.k.socks[10] = [(8443, 10 + i, 1000 + i, tcpinfo(sent=12 * MB)) for i in range(20)]
        self.at(119)
        self.at(120)
        r2 = self.row()
        self.assertEqual((r2["ts"], r2["conns"], r2["sent"]), (H + 3600, 20, 20 * 6 * MB))

    def test_a_clock_going_back_keeps_the_open_hour(self):
        """П-1: NTP steps the clock back over the border of an hour: never two rows of one hour."""
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB)
        plan = self.plan
        self.p.step(plan, 0.0, H + 3500)
        self.p.step(plan, 60.0, H + 3600 + 10)                  # hour H closed
        self.p.step(plan, 120.0, H + 3500 + 70)                 # the clock back: still the hour H+1
        self.p.step(plan, 180.0, H + 3600 + 130)
        self.p.step(plan, 240.0, H + 7200 + 10)                 # hour H+1 closed
        self.p.step(plan, 300.0, H + 7200 - 30)
        self.p.step(plan, 360.0, H + 10800 + 10)
        self.assertEqual([r["ts"] for r in self.p.take()], [H, H + 3600, H + 7200])
        self.p.step(plan, 420.0, H + 10800 - 86400)            # a day back (a wrong clock fixed): the open hour goes
        self.assertEqual((self.p.hour, self.p.take()), (H + 10800 - 86400, []))
        self.p.step(plan, 480.0, H + 14400 - 86400)
        self.assertEqual([r["ts"] for r in self.p.take()], [H + 10800 - 86400])

    def test_a_long_gap_across_hours_carries_nothing(self):
        """П-1: after a gap longer than GAP_S at the border of an hour the known connections start from what they show:
        the bytes of the gap are of no known hour."""
        self.at(0)
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB)
        self.at(1)
        self.k.socks[10] = [(8443, 10 + i, 1000 + i, tcpinfo(sent=900 * MB)) for i in range(20)]
        self.at(181)                                            # 3 hours later (the gateway was off)
        self.assertEqual(self.row()["sent"], 20 * 3 * MB)
        self.assertEqual(set(c[4] - c[1] for c in self.p.conns.values()), {0})
        self.k.socks[10] = [(8443, 10 + i, 1000 + i, tcpinfo(sent=903 * MB)) for i in range(20)]
        self.at(182)
        self.at(241)
        self.assertEqual(self.row()["sent"], 20 * 3 * MB)

    def test_gone_small_connections_are_forgotten(self):
        """З-1: short connections every minute do not fill the hour: after a whole sample the gone ones with
        Δ < 64 KB are forgotten, a big connection late in the hour is still counted."""
        self.at(0)
        with mock.patch.object(HPL, "MAX_TRACK", 1000):
            for m in range(1, 51):
                self.k.socks[10] = [(8443, m * 1000 + i, 1000 + i, tcpinfo(sent=10 * 1024)) for i in range(300)]
                self.at(m)
                self.assertLessEqual(len(self.p.conns), 300)
            self.k.socks[10] = [(8443, 999999, 1999, tcpinfo(sent=1 * MB))]
            self.at(51)
        self.assertIn(999999, self.p.conns)
        self.assertTrue(self.p.whole)

    def test_max_track_is_no_base(self):
        """З-2 / sec З-1: a sample that could not follow every connection (MAX_TRACK) is not whole: the ones it missed
        do not bring their past into the hour in the next sample; the row tells it."""
        self.at(0)
        for i in range(10):
            self.k.add(8443, 10 + i, 1000 + i, sent=900 * MB)
        with mock.patch.object(HPL, "MAX_TRACK", 4):
            self.at(1)
        self.assertFalse(self.p.whole)
        self.at(2)
        self.assertEqual(sorted(c[4] - c[1] for c in self.p.conns.values()), [0] * 6 + [900 * MB] * 4)
        self.at(61)
        self.assertEqual(self.row()["partial"], 1)

    def _two_families(self):
        self.at(0)
        for i in range(10):
            self.k.add(8443, 10 + i, 1000 + i, fam=2, sent=900 * MB)
            self.k.add(8443, 50 + i, 1050 + i, fam=10, sent=900 * MB)

    def _v6_not_counted_after(self, whole, seen=0):
        """The next sample: the AF_INET6 sockets the failing one missed do not bring their past (900 MB)."""
        self.assertEqual(self.p.whole, whole)
        self.k.silent, self.k.err, self.k.mid = set(), {}, {}
        self.at(9)
        delta = dict((ck, c[4] - c[1]) for ck, c in self.p.conns.items())
        self.assertEqual([delta[50 + i] for i in range(seen, 10)], [0 if not whole else 900 * MB] * (10 - seen))

    def test_one_family_silent_is_not_whole(self):
        """З-2 / З-5: AF_INET6 does not answer: the sample is not whole; its sockets come later without their past."""
        self._two_families()
        self.k.silent = {10}
        self.at(1)
        self._v6_not_counted_after(False)

    def test_one_family_failing_in_the_middle_is_not_whole(self):
        """З-5: ENOMEM of one family (NLMSG_ERROR or OSError in the middle of its dump) — not «no such family»."""
        self._two_families()
        self.k.err = {10: errno.ENOMEM}
        self.at(1)
        self._v6_not_counted_after(False)
        self.p = people(self.k, self.units)
        self.k.socks = {2: [], 10: []}
        self._two_families()
        self.k.mid = {10: errno.ENOMEM}
        self.at(1)
        self.assertEqual(sorted(k for k in self.p.conns if k >= 50), [50, 51, 52])   # what came before is kept
        self._v6_not_counted_after(False, seen=3)

    def test_a_family_the_kernel_does_not_have_is_whole(self):
        """IPv6 switched off (ENOENT at once) or no such family (EAFNOSUPPORT): the sample is still whole."""
        self._two_families()
        self.k.socks[10] = []
        self.k.err = {10: errno.ENOENT}
        self.at(1)
        self.assertTrue(self.p.whole)
        self.assertEqual(len(self.p.conns), 10)

    def test_inputs_agree_with_the_numbers(self):
        """З-3: an input that gave numbers to the sum is not «few»; «few» only when the row has no numbers."""
        self.at(0)
        for i in range(15):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB)
            self.k.add(4433, 100 + i, 3000 + (i % 100), sent=3 * MB)
        self.at(1)
        self.at(61)
        r = self.row()
        self.assertEqual((r["why"], r["conns"]), (None, 30))
        self.assertEqual([r["inputs"][k] for k in ("vless", "sstp", "socks5")], ["ok", "ok", "ok"])
        self.k.socks[10] = [s for s in self.k.socks[10] if s[0] == 8443][:5]
        self.at(62)
        self.at(122)
        r = self.row()
        self.assertEqual(r["why"], "few")
        self.assertEqual([r["inputs"][k] for k in ("vless", "sstp", "socks5")], ["few", "few", "few"])

    def test_a_unit_without_cgroup_is_not_measured(self):
        """З-6: no cgroup directory of the unit — «not measured» (no_cgroup), not «few connections»."""
        del self.units[OVPN]
        p = people(self.k, self.units)
        plan = {"openvpn": (OVPN, 1443), "awg2": "udp"}
        p.step(plan, 0.0, H)
        p.step(plan, 60.0, H + 60)
        p.step(plan, 3700.0, H + 3700)
        r = p.take()[0]
        self.assertEqual((r["why"], r["inputs"]), ("no_cgroup", {"openvpn": "no_data", "awg2": "udp"}))
        self.assertEqual(HPL.summary([r])["why"], "no_cgroup")
        p.step(self.plan, 3760.0, H + 3760)                     # with VLESS read: OpenVPN alone «not measured»
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB)
        p.step(self.plan, 3820.0, H + 3820)
        p.step(self.plan, 7300.0, H + 7300)
        r = p.take()[0]
        self.assertEqual((r["why"], r["conns"], r["inputs"]["openvpn"], r["inputs"]["vless"]), (None, 20, "no_data", "ok"))

    def test_the_border_of_64_kb(self):
        """П-4: Δ bytes_sent = 64 KB counts, one byte less does not."""
        self.at(0)
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB)
        self.k.add(8443, 98, 1998, sent=HPL.MIN_BYTES)
        self.k.add(8443, 99, 1999, sent=HPL.MIN_BYTES - 1)
        self.at(1)
        self.at(61)
        r = self.row()
        self.assertEqual((r["conns"], r["sent"]), (21, 60 * MB + HPL.MIN_BYTES))

    def test_counters_that_cannot_be_never_reach_the_row(self):
        """sec П-2: u64-max and anything above 2^63−1 give no number; a product dsack × mss over it is left out — a
        row SQLite always takes."""
        import sqlite3
        self.at(0)
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB)
        self.k.add(8443, 90, 1990, sent=2 ** 64 - 1)
        self.k.add(8443, 91, 1991, sent=2 ** 63, retr=2 ** 63)
        self.k.add(8443, 92, 1992, sent=3 * MB, dsack=0xFFFFFFFE, mss=0xFFFFFFFE)
        self.k.add(8443, 93, 1993, sent=3 * MB, dsack=0xFFFFFFFE, mss=0xFFFFFFFE)
        self.at(1)
        self.at(61)
        r = self.row()
        self.assertGreaterEqual(self.p.bad, 3)
        self.assertEqual((r["conns"], r["sent"]), (20, 60 * MB))
        self.assertLessEqual(max(r["sent"], r["retr"], r["dsack"]), 2 ** 63 - 1)
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE t (conns INTEGER, sent INTEGER, retr INTEGER, dsack INTEGER)")
        db.execute("INSERT INTO t VALUES (?,?,?,?)", (r["conns"], r["sent"], r["retr"], r["dsack"]))
        db.close()

    def test_a_row_from_the_base(self):
        """З-7: the rows of a day come from the base: ``inputs`` is JSON text there (sqlite3.Row and a dict of it)."""
        import sqlite3
        self.at(0)
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=3 * MB, rtt=20000, mrtt=10000)
        self.at(1)
        self.at(61)
        r = self.row()
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE host_people_h1 (ts INTEGER PRIMARY KEY, conns INTEGER, rtt_med REAL, rtt_p90 REAL, "
                   "rtt_min_med REAL, sent INTEGER, retr INTEGER, dsack INTEGER, why TEXT, inputs TEXT)")
        cols = ("ts", "conns", "rtt_med", "rtt_p90", "rtt_min_med", "sent", "retr", "dsack", "why")
        db.execute("INSERT OR REPLACE INTO host_people_h1 VALUES (?,?,?,?,?,?,?,?,?,?)",
                   [r[c] for c in cols] + [json.dumps(r["inputs"], sort_keys=True)])
        db.row_factory = sqlite3.Row
        got = db.execute("SELECT * FROM host_people_h1").fetchall()
        for rows in (got, [dict(x) for x in got]):
            s = HPL.summary(rows)
            self.assertEqual((s["rtt_ms"], s["best_ms"], s["conns"], s["inputs"]["vless"], s["inputs"]["awg2"]),
                             (20.0, 10.0, 20, "ok", "udp"))
        bad = dict(got[0])
        bad["inputs"] = "{not json"
        self.assertEqual(HPL.summary([bad])["inputs"], {})
        db.close()

    def test_proc_is_not_read_with_the_cgroup_id(self):
        """sec З-2: a kernel giving INET_DIAG_CGROUP_ID — the unit of a socket by its cgroup, /proc never read."""
        if not self.k.cg_attr:
            self.skipTest("a kernel before 5.7")

        def never(*a, **k):
            raise AssertionError("/proc read")
        self.p = people(self.k, self.units, pids=never, inodes=never, readable=never)
        for i in range(20):
            self.k.add(8443, 10 + i, 1000 + i, sent=0)
        self.k.add(8443, 99, 777, sent=0)                       # not of xray: its cgroup is another
        self.at(0)
        self.k.socks[10] = [(8443, 10 + i, 1000 + i, tcpinfo(sent=3 * MB)) for i in range(20)]
        self.k.add(8443, 99, 777, sent=900 * MB)
        self.at(1)
        self.at(61)
        self.assertEqual((self.row()["conns"], self.p.cg_attr), (20, True))


class PeopleOldKernelTest(PeopleTest):
    """Every test of PeopleTest on a kernel before 5.7: no INET_DIAG_CGROUP_ID, the processes of the unit and /proc."""

    def setUp(self):
        super().setUp()
        self.k.cg_attr = False


class FallbackTest(RootCase):
    """The walk of /proc without INET_DIAG_CGROUP_ID: the deadline of the sample, the cut lists, a process leaving."""

    def setUp(self):
        super().setUp()
        self.k = Kernel()
        self.k.cg_attr = False
        self.plan = {"ocserv": (OCSERV, 4443)}

    def test_the_walk_stops_at_the_deadline(self):
        """sec З-2: the walk of /proc shares the 2 s of the sample: past it the sample is not whole."""
        clock = [100.0]
        walked = []

        def inodes(pid, into, deadline=None):
            walked.append(pid)
            clock[0] += 1.5
            into.add(4000 + pid)
            return True
        p = HPL.People(dump=self.k.dump, cgv=lambda: 2, cgroup=lambda u: 7, pids=lambda u: [1, 2, 3, 4, 5],
                       inodes=inodes, readable=lambda u: True)
        for pid in (1, 2, 3, 4, 5):
            self.k.add(4443, 10 + pid, 4000 + pid, sent=3 * MB)
        with mock.patch.object(HPL.time, "monotonic", lambda: clock[0]):
            p.step(self.plan, 0.0, H)
        self.assertEqual(walked, [1, 2])
        self.assertFalse(p.whole)
        self.assertEqual(sorted(p.conns), [11, 12])

    def test_a_cut_list_of_processes_or_descriptors_is_not_whole(self):
        """З-5: more than MAX_PIDS processes, or a process with more than MAX_FDS descriptors — not whole."""
        p = HPL.People(dump=self.k.dump, cgv=lambda: 2, cgroup=lambda u: 7, pids=lambda u: list(range(1, 300)),
                       inodes=lambda pid, into, deadline=None: into.add(4000) or True, readable=lambda u: True)
        self.k.add(4443, 10, 4000, sent=3 * MB)
        p.step(self.plan, 0.0, H)
        self.assertFalse(p.whole)
        p = HPL.People(dump=self.k.dump, cgv=lambda: 2, cgroup=lambda u: 7, pids=lambda u: [1],
                       inodes=lambda pid, into, deadline=None: into.add(4000) or "cut", readable=lambda u: True)
        p.step(self.plan, 0.0, H)
        self.assertFalse(p.whole)
        self.assertEqual(sorted(p.conns), [10])                 # what was read is used

    def test_unit_pids_tells_a_cut_list(self):
        self.write("/sys/fs/cgroup/system.slice/%s/cgroup.procs" % OCSERV,
                   "\n".join(str(1000 + i) for i in range(400)) + "\n")
        self.assertEqual(len(HPL.unit_pids(OCSERV)), HPL.MAX_PIDS + 1)

    def _fds(self, pid, n):
        d = self.path("/proc/%d/fd" % pid)
        os.makedirs(d)
        for i in range(n):
            os.symlink("socket:[%d]" % (5000 + i), os.path.join(d, str(i)))

    def test_sock_inodes_deadline_and_limit(self):
        self._fds(70, 600)
        got = set()
        self.assertEqual(HPL.sock_inodes(70, got, time.monotonic() - 1), "late")
        self.assertEqual(len(got), 255)
        with mock.patch.object(HPL, "MAX_FDS", 3):
            self.assertEqual(HPL.sock_inodes(70, set()), "cut")
        got = set()
        self.assertIs(HPL.sock_inodes(70, got, time.monotonic() + 60), True)
        self.assertEqual(len(got), 600)

    def test_a_process_leaving_in_the_middle_of_the_walk(self):
        """sec П-1: getdents of /proc/<pid>/fd of a process that left — ENOENT in the middle of the walk: one process
        lost, not the sample; another error — this process only, the sample not whole."""
        class Walk(object):
            def __init__(self, exc):
                self.exc = exc

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                return self

            def __next__(self):
                raise self.exc
        with mock.patch.object(HPL.os, "scandir", lambda p: Walk(FileNotFoundError(2, "gone"))):
            self.assertIs(HPL.sock_inodes(4242, set()), True)
        with mock.patch.object(HPL.os, "scandir", lambda p: Walk(OSError(5, "EIO"))):
            self.assertEqual(HPL.sock_inodes(4242, set()), "cut")

        def boom(pid, into, deadline=None):
            if pid == 2:
                raise OSError(5, "EIO")
            into.add(4000 + pid)
            return True
        p = HPL.People(dump=self.k.dump, cgv=lambda: 2, cgroup=lambda u: 7, pids=lambda u: [1, 2, 3],
                       inodes=boom, readable=lambda u: True)
        for pid in (1, 2, 3):
            self.k.add(4443, 10 + pid, 4000 + pid, sent=3 * MB)
        p.step(self.plan, 0.0, H)
        self.assertEqual(sorted(p.conns), [11, 13])
        self.assertFalse(p.whole)


class HostileTest(RootCase):
    def test_a_hostile_kernel_readers_and_clock(self):
        """REV-ns-b17-sec fuzz3 as a test: hostile dumps (errors, cuts, damaged bytes, u64-max counters, foreign cgroup
        ids), hostile readers and a clock going back and forth — nothing escapes, the limits hold, every row is one
        SQLite takes, never two rows of one hour (unless the clock went back more than an hour: then the writer replaces
        the row, INSERT OR REPLACE)."""
        rnd = random.Random(29)
        u64 = (1 << 64) - 1
        ino = list(range(1000, 1600))

        def info():
            k = rnd.random()
            if k < 0.1:
                return None
            if k < 0.2:
                return tcpinfo(length=rnd.choice([1, 100, 192, 219]))
            big = rnd.random() < 0.1
            return tcpinfo(length=rnd.choice([220, 232, 248, 300]), rtt=rnd.choice([0, 1, 40000, 0xFFFFFFFF]),
                           mrtt=rnd.randrange(1 << 32), mss=rnd.choice([0, 1448, 0xFFFFFFFE]),
                           sent=u64 if big else rnd.randrange(1 << 34), retr=u64 if big else rnd.randrange(1 << 30),
                           dsack=0xFFFFFFFE if big else rnd.randrange(1000))

        def dump(req, deadline, budget):
            seq = struct.unpack_from("=I", req, 8)[0]
            if rnd.random() < 0.03:
                raise OSError(rnd.choice([97, 12, 1]), "x")
            if rnd.random() < 0.03:
                yield error(seq, rnd.choice([2, 12, 22]))
                return
            for _ in range(rnd.randint(0, 6)):
                msgs = [diag(rnd.choice([443, 1080, 4433, 22, 80]), rnd.randrange(1, 5000), rnd.choice(ino + [0, 7]),
                             info(), fam=req[16], state=rnd.choice([1] * 9 + [8]), seq=seq,
                             cg=rnd.choice([None, CG[XRAY], CG[XRAY], CG[ACCEL], OTHER_CG, rnd.getrandbits(64)]))
                        for _ in range(rnd.randint(0, 50))]
                b = bytearray(b"".join(msgs))
                for _ in range(rnd.choice([0, 0, 0, 1, 3])):
                    if b:
                        b[rnd.randrange(len(b))] = rnd.randrange(256)
                if rnd.random() < 0.1 and b:
                    b = b[:rnd.randrange(len(b))]
                budget[0] -= len(b)
                yield bytes(b)
            if rnd.random() < 0.8:
                yield done(seq)

        def inodes(pid, into, deadline=None):
            r = rnd.random()
            if r < 0.03:
                return False
            if r < 0.05:
                raise OSError(5, "EIO")
            into.update(rnd.sample(ino, 400))
            return rnd.choice([True] * 8 + ["cut", "late"])

        p = HPL.People(dump=dump, cgv=lambda: 2, inodes=inodes, readable=lambda u: rnd.random() < 0.95,
                       pids=lambda u: rnd.choice([[1], [1, 2], [], None, list(range(300))]) if rnd.random() < 0.1
                       else [1], cgroup=lambda u: rnd.choice([CG.get(u), CG.get(u), CG.get(u), "no_cgroup", None]))
        plan = {"vless": (XRAY, 443), "socks5": (XRAY, 1080), "sstp": (ACCEL, 4433), "pptp": "udp"}
        mono, now, ts, epochs = 0.0, float(H), [], 0
        with mock.patch.object(HPL, "MAX_TRACK", 3000), mock.patch.object(HPL, "MAX_SOCKS", 2000):
            for _ in range(500):
                mono += rnd.choice([60] * 8 + [200, -10])
                now += rnd.choice([60] * 8 + [3600, -3600])
                before = p.hour
                p.step(plan, mono, now)
                self.assertLessEqual(len(p.conns), 3000)
                for r in p.take():
                    self.assertTrue(not ts or r["ts"] > ts[-1], (ts[-3:], r["ts"]))   # one row an hour, in order
                    ts.append(r["ts"])
                    for k in ("sent", "retr", "dsack", "conns"):
                        self.assertTrue(r[k] is None or 0 <= r[k] <= HPL.INT63, (k, r[k]))
                    HPL.summary([r])
                if before is not None and p.hour is not None and p.hour < before:
                    ts, epochs = [], epochs + 1                # more than an hour back: a new run of hours
        self.assertGreater(len(ts) + epochs, 5)


class RowsTest(RootCase):
    def test_the_rows_past_48_hours_are_counted_and_logged(self):
        """П-2: no writer for 48 hours — the oldest row goes, with a line in the journal (once), not silently."""
        collect.MEM.reset()
        self.addCleanup(collect.MEM.reset)
        p = collect.MEM.people = HPL.People(dump=Kernel().dump, cgv=lambda: 2)
        for i in range(HPL.KEEP_ROWS + 3):
            p._push({"ts": H + 3600 * i})
        p.keep(p.take())
        self.assertEqual(p.dropped, 3)
        logged = []
        with mock.patch.object(util, "log_action", lambda *a, **k: logged.append(a[0])):
            collect._people_flush(object())
            collect._people_flush(object())
        self.assertEqual(len(logged), 1, logged)
        self.assertIn("3 hour row(s) dropped", logged[0])
        self.assertEqual((p.dropped, len(p.rows)), (0, HPL.KEEP_ROWS))


class FakeSock(object):
    def __init__(self, chunks, flags=0):
        self.chunks = list(chunks)
        self.flags = flags
        self.closed = False

    def bind(self, a):
        pass

    def settimeout(self, t):
        pass

    def sendto(self, data, a):
        pass

    def recvmsg(self, n):
        if not self.chunks:
            raise HPL.socket.timeout()
        return self.chunks.pop(0), [], self.flags, None

    def close(self):
        self.closed = True


class NetlinkTest(RootCase):
    def run_dump(self, sock, budget=1 << 20, deadline=None):
        with mock.patch.object(HPL.socket, "AF_NETLINK", 16, create=True), \
                mock.patch.object(HPL.socket, "socket", lambda *a: sock):
            out = []
            try:
                for d in HPL.netlink_dump(b"req", deadline or time.monotonic() + 5, [budget]):
                    out.append(d)
            except HPL._Stop:
                out.append("stop")
        self.assertTrue(sock.closed)
        return out

    def test_flood_stops_by_bytes(self):
        out = self.run_dump(FakeSock([b"x" * 1000] * 100), budget=5000)
        self.assertEqual(out.count("stop"), 1)
        self.assertLessEqual(len(out), 6)

    def test_cut_datagram_timeout_and_time(self):
        self.assertEqual(self.run_dump(FakeSock([b"x"], flags=HPL.socket.MSG_TRUNC)), ["stop"])
        self.assertEqual(self.run_dump(FakeSock([b"a", b"b"])), [b"a", b"b", "stop"])
        self.assertEqual(self.run_dump(FakeSock([b"a"]), deadline=time.monotonic() - 1), ["stop"])

    def test_the_sample_survives_a_flood(self):
        """A kernel answering without end: one sample stops at the bytes and still uses what it parsed."""
        seq_box = {}

        def endless(req, deadline, budget):
            seq_box["seq"] = struct.unpack_from("=I", req, 8)[0]
            while budget[0] > 0:
                d = diag(8443, random.getrandbits(40) + 1, 1000, tcpinfo(sent=3 * MB), seq=seq_box["seq"]) * 50
                budget[0] -= len(d)
                yield d
            raise HPL._Stop()
        p = HPL.People(dump=endless, cgv=lambda: 2, pids=lambda u: [1], cgroup=lambda u: 4242,
                       inodes=lambda pid, into, deadline=None: into.add(1000) or True)
        with mock.patch.object(HPL, "MAX_DUMP_BYTES", 200000):
            p.step({"vless": (XRAY, 8443)}, 0.0, H)
        self.assertGreater(len(p.conns), 0)
        self.assertGreaterEqual(p.over, 1)


class CollectorTest(RootCase):
    def setUp(self):
        super().setUp()
        collect.MEM.reset()
        self.addCleanup(collect.MEM.reset)

    def test_rows_go_to_the_writer_when_it_can_take_them(self):
        p = collect.MEM.people = HPL.People(dump=Kernel().dump, cgv=lambda: 2)
        rows = [{"ts": H}, {"ts": H + 3600}]
        p.keep(rows)

        class Old(object):
            pass
        collect._people_flush(Old())                            # SCHEMA 2: no table — the rows wait
        self.assertEqual(len(p.rows), 2)
        got = []

        class Bad(object):
            def write_people_hour(self, ts, row):
                if ts != H:
                    raise RuntimeError("disk")
                got.append(ts)
        with mock.patch.object(collect, "_note_error", lambda *a: None):
            collect._people_flush(Bad())
        self.assertEqual((got, [r["ts"] for r in p.rows]), ([H], [H + 3600]))

        class New(object):
            def write_people_hour(self, ts, row):
                got.append(row)
        collect._people_flush(New())
        self.assertEqual(got[-1], {"ts": H + 3600})
        self.assertEqual(len(p.rows), 0)

    def test_the_pause_takes_a_sample_once_a_minute_and_counts_it(self):
        calls = []
        collect.MEM.host_cfg = {"enabled": True, "probes": False, "anchors": []}
        collect.MEM.people_plan = {"vless": (XRAY, 8443)}
        collect.MEM.people = HPL.People(dump=Kernel().dump, cgv=lambda: 2, pids=lambda u: [], inodes=None)
        real = collect.MEM.people.sample
        collect.MEM.people.sample = lambda *a: (calls.append(a), real(*a))
        clock = [5000.0]
        with mock.patch.object(collect, "_mono", lambda: clock[0]), \
                mock.patch("kaskad.gw.hostping.step", lambda *a, **k: None), \
                mock.patch("kaskad.gw.hostping.Prober", lambda *a, **k: object()), \
                mock.patch("kaskad.gw.hostping.Canary") as can:
            can.return_value.maybe.return_value = None
            collect._host_pause(10)
            clock[0] += 10
            collect._host_pause(10)
            self.assertEqual(len(calls), 1)
            clock[0] += 60
            collect._host_pause(1.0)                            # no room for a sample in this step
            self.assertEqual(len(calls), 1)
            collect._host_pause(10)
            self.assertEqual(len(calls), 2)
        self.assertIsNotNone(collect.MEM.cost_since)

    def test_plan_errors_do_not_stop_the_round(self):
        errs = []
        with mock.patch.object(HPL, "plan", mock.Mock(side_effect=ValueError("x"))), \
                mock.patch.object(collect, "_note_error", lambda k, e: errs.append(k)):
            collect._people_plan({})
        self.assertIsNone(collect.MEM.people_plan)
        self.assertEqual(errs, ["hostpeople_plan"])

    def test_a_row_the_base_refuses_is_dropped_not_retried(self):
        """sec П-2: a row refused for its data (OverflowError, IntegrityError) is dropped with a line in the journal
        and never blocks the rows after it; the base failing (OperationalError) keeps them for the next minute."""
        import sqlite3
        p = collect.MEM.people = HPL.People(dump=Kernel().dump, cgv=lambda: 2)
        got, logged = [], []

        class W(object):
            fail = {}

            def write_people_hour(self, ts, row):
                e = self.fail.get(ts)
                if e is not None:
                    raise e
                got.append(ts)
        w = W()
        w.fail = {H: OverflowError("Python int too large to convert to SQLite INTEGER"),
                  H + 3600: sqlite3.IntegrityError("x")}
        p.keep([{"ts": H}, {"ts": H + 3600}, {"ts": H + 7200}])
        with mock.patch.object(util, "log_action", lambda *a, **k: logged.append(a[0])):
            collect._people_flush(w)
            collect._people_flush(w)
        self.assertEqual((got, len(logged), len(p.rows)), ([H + 7200], 2, 0))
        self.assertTrue(all("dropped" in x for x in logged))
        w.fail = {H + 10800: sqlite3.OperationalError("database is locked")}
        p.keep([{"ts": H + 10800}, {"ts": H + 14400}])
        with mock.patch.object(collect, "_note_error", lambda *a: None):
            collect._people_flush(w)
        self.assertEqual([r["ts"] for r in p.rows], [H + 10800, H + 14400])
        w.fail = {}
        collect._people_flush(w)
        self.assertEqual(got[-2:], [H + 10800, H + 14400])


class PeopleB6Test(RootCase):
    """B6 batch of REV-ns-b17-2: the full table and the cleaning (З-1, scenario s1_lock), the mark ``partial`` of the
    day (З-2), «back more than an hour» by the wall clock (П-1), the tests the mutants A and L slipped past (П-2), an
    hour given as a row is never opened again (П-3)."""

    def setUp(self):
        super().setUp()
        self.k = Kernel()
        self.units = {XRAY: {1: set()}}
        self.p = people(self.k, self.units)
        self.plan = {"vless": (XRAY, 443)}
        self.ino = 10 ** 6

    def new(self, n, sent, fam=10):
        out = []
        for _ in range(n):
            self.ino += 1
            self.units[XRAY][1].add(self.ino)
            self.k.add(443, self.ino, self.ino, fam=fam, sent=sent)
            out.append(self.ino)
        return out

    def test_a_full_table_is_cleaned_by_a_whole_dump(self):
        """s1_lock: 100 short connections fill MAX_TRACK = 100, then they are gone and 30 new short ones come every
        minute; the cleaning goes on (the dump saw everything), the big connection of minute 30 is followed."""
        with mock.patch.object(HPL, "MAX_TRACK", 100):
            mono, now = 0.0, float(H) + 1
            self.new(100, 10000)
            self.p.step(self.plan, mono, now)
            self.assertEqual(len(self.p.conns), 100)
            for _m in range(2, 30):
                self.k.socks[10] = []
                self.new(30, 10000)
                mono += 60
                now += 60
                self.p.step(self.plan, mono, now)
                self.assertLessEqual(len(self.p.conns), 30 + 30)
                self.assertTrue(self.p.whole)
            self.k.socks[10] = []
            big = self.new(1, 0)[0]
            mono += 60
            now += 60
            self.p.step(self.plan, mono, now)
            self.assertIn(big, self.p.conns)
            self.assertEqual(self.p.partial, 0)

    def test_the_table_still_full_after_cleaning_is_partial(self):
        with mock.patch.object(HPL, "MAX_TRACK", 10):
            self.new(10, 10000)
            self.p.step(self.plan, 0.0, float(H) + 1)
            self.new(5, 10000)                       # the old ones live on: no room
            self.p.step(self.plan, 60.0, float(H) + 61)
            self.assertFalse(self.p.whole)
            self.assertEqual((len(self.p.conns), self.p.partial), (10, 1))

    def test_a_silent_family_keeps_its_small_connection(self):
        """П-2, mutant A: a sample with one family silent is not whole — a small living connection of that family is
        not forgotten (it would come back with its whole past as «new»)."""
        v6 = self.new(1, 10000, fam=10)[0]
        v4 = self.new(1, 10000, fam=2)[0]
        self.p.step(self.plan, 0.0, float(H) + 1)
        self.assertEqual(set(self.p.conns), {v6, v4})
        self.k.silent = {10}
        self.p.step(self.plan, 60.0, float(H) + 61)
        self.assertFalse(self.p.whole)
        self.assertIn(v6, self.p.conns)

    def test_enoent_in_the_middle_of_a_dump_is_not_whole(self):
        """П-2, mutant L: ENOENT after the first datagram is an error of the dump, not «no such family»."""
        self.k.per = 1
        self.new(3, 10000, fam=10)
        self.k.mid = {10: errno.ENOENT}
        self.p.step(self.plan, 0.0, float(H) + 1)
        self.assertFalse(self.p.whole)
        self.assertEqual(self.p.partial, 1)

    def test_back_more_than_an_hour_by_the_wall_clock(self):
        """П-1: at 10:55 the clock back 70 min forgets the hour (it was 1–2 h by the starts of the hours)."""
        self.new(20, 3 * MB)
        self.p.step(self.plan, 0.0, H + 55 * 60)
        self.p.step(self.plan, 60.0, H + 55 * 60 - 70 * 60)
        self.assertEqual((self.p.hour, self.p.n), (H - 3600, 1))      # forgotten: a new hour from this sample
        self.assertEqual(self.p.take(), [])

    def test_back_less_than_an_hour_keeps_the_open_hour(self):
        self.new(20, 3 * MB)
        self.p.step(self.plan, 0.0, H + 5 * 60)
        self.p.step(self.plan, 60.0, H + 5 * 60 - 59 * 60)
        self.assertEqual((self.p.hour, self.p.n), (H, 2))

    def test_measure_off_and_clock_back_never_replaces_a_row(self):
        """П-3: the row of the hour H is given; the measure off (clear) and the clock back into H: H is not opened again
        — never a second (partial) row with the same ts."""
        self.new(20, 3 * MB)
        for i in range(60):
            self.p.step(self.plan, 60.0 * i, H + 60 * i + 1)
        self.p.step(self.plan, 3600.0, H + 3601)
        self.assertEqual([r["ts"] for r in self.p.take()], [H])
        self.p.step(None, 3660.0, H + 3661)                         # the measure off
        self.p.step(self.plan, 3720.0, H + 2400)                    # back into the hour H
        self.assertIsNone(self.p.hour)
        self.p.step(self.plan, 3780.0, H + 3600 + 300)              # the next hour opens as usual
        self.assertEqual(self.p.hour, H + 3600)
        self.p.step(self.plan, 7300.0, H + 7300)
        self.assertEqual([r["ts"] for r in self.p.take()], [H + 3600])

    def test_summary_counts_partial_hours(self):
        """З-2: the line of the day says how many hours did not see every connection (base rows: text, a bad value)."""
        good = {"conns": 30, "rtt_med": 40.0, "rtt_min_med": 30.0, "sent": 100 * MB, "retr": MB, "dsack": 0,
                "why": None, "inputs": '{"vless": "ok"}'}
        rows = [dict(good, ts=H, partial=2), dict(good, ts=H + 3600, partial=0),
                dict(good, ts=H + 7200, partial=None), {"ts": H + 10800, "why": "few", "partial": 1, "inputs": "{}"},
                {"ts": H + 14400, "why": "few", "partial": "x"}]
        s = HPL.summary(rows)
        self.assertEqual((s["partial"], s["hours"], s["conns"]), (2, 3, 90))
        self.assertEqual(HPL.summary([dict(good, partial=0)])["partial"], 0)
