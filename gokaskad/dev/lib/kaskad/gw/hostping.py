"""Light probes of the node's own network and the canary work (SPEC-node-score §3.3, §4 A3, NS-D12, NS-D15, NS-D16).

Sockets only — no process, no command, no right beyond the collector's own (``net.ipv4.ping_group_range`` and the
capabilities of the unit are never touched). A series: 3 echo packets to every anchor 200 ms apart (as ``ping -i 0.2``),
an answer later than 1 s is a loss; the anchors in a random order; the next series in 30 s × U[0,7; 1,3].

    prober = Prober(anchors, rng)        the state of the process (the way to each anchor, the dark series, the clock)
    step(prober, hm, mono)               the flags of the last round (``gateway.hostmon``) checked before every series:
                                         ``enabled`` and ``probes is True`` — else no socket at all; -> result | None
    prober.due(mono) / until(mono)       is a series due / seconds until it
    prober.series(mono)                  one series now -> result (below)
    valid_anchor(text, own) / valid_anchors(values, own)   the check of an anchor
    working_anchors(values, own)         the anchors in work: checked, ≤ 5, empty — the defaults; the ONE list of
                                         ``Prober``, ``model.hostmon`` (A5) and so of the rule of A2 (``cfg["anchors"]``)
    speed_busy(path, fresh_s)            the speed test's lock is held or was let go less than ``fresh_s`` ago —
                                         read from /proc/locks and the file's mtime; the lock is NEVER taken here
    Canary().maybe(mono, busy)           the fixed sha256 work, ≤ once per 600 s, only while the CPU is < 50 % busy

The way to an anchor (§3.3 «Сокеты»), tried in this order and kept for the process, family by family:
  1. the kernel's ping socket ``SOCK_DGRAM/IPPROTO_ICMP[V6]``, one per anchor (the kernel gives each its own id and
     passes it only the echo replies to that id);
  2. on EACCES/EPERM — one raw socket per family and series, only with the kernel's filter (``ICMP_FILTER`` /
     ``ICMP6_FILTER``); the filter of IPv4 holds only the types < 32, so the type is always checked here too;
  3. else — a DNS query ``. NS`` over UDP (no name in it), its own connected socket per anchor.
An anchor whose ICMP stays dark 20 counted series in a row while the others answer goes to DNS; once a day it tries
ICMP again (the day of each anchor shifted by U[0; 1 h], so the anchors never try together: REV-ns-a3-2 П-1). An
anchor on DNS whose DNS stays dark 20 series in a row tries ICMP at once, on trial (NS-D22: a DNS path closed after an
outage at the start must not hold a false loss for a day). While the node is offline (``node_offline`` of the
collector) no series goes at all (NS-D22): no socket, no dark series, no loss. ICMP sockets are never connected:
every packet is a ``sendto()`` to its anchor.

Trial series (NS-D19): an anchor with no ICMP answer yet in this process (the start of the process, a new anchor, the
daily try back from DNS) goes by ICMP on trial — a trial series without an answer is no loss and no event: the anchor
is left out of ``anchors`` (listed in ``trial``). Its first answer puts it «in account» (that series counts already);
20 dark trial series — it stays on DNS until the next daily try. A dark ICMP of a far anchor (NS-D15) thus never
gives a loss: neither once a day nor after every restart of the collector.

The packet (NS-D16, as iputils ``ping``): 56 bytes of data — ``struct timeval`` of the sending (native order),
then the bytes ``i`` for i = 16…55 (0x10…0x37); the numbers 1, 2, 3 in every series for every anchor; the id —
the kernel's (DGRAM) or a random 16 bits per anchor and series (raw), never the PID.

A reply counts only when everything holds (else it is dropped silently, nothing of it goes anywhere but «own» and
the time): type 0/129 and code 0; the sender is the anchor of this socket (raw: of this id); the id is ours; the
number waits for a reply in this series; all 56 bytes are the ones sent with this number; raw IPv4 — version 4,
20 ≤ IHL ≤ 60, IHL ≤ length, protocol 1, exactly IHL + 8 + 56 bytes, the ICMP checksum right; DNS — the connected
socket, ≥ 12 bytes, our id, QR = 1. Budget of a series (all its sockets together): ``SO_RCVBUF`` 16 KB, the 256-th
read ends it at once as ``excl`` (``noisy``), more than 64 dropped — ``excl`` (``noisy``); the kernel's drop counter
(``SO_RXQ_OVFL``) grew on a socket that lost a reply of its own while its queue was full — it read ≥ 3 packets at
once, or a truncated one (a packet of ≥ 32 KB fills the queue alone), or it is the raw IPv4 socket (the kernel checks
no ICMP checksum there: any growth is an overflow) — ``excl`` (``overflow``; NS-D21: a single damaged packet the kernel
drops for its checksum — UDP, raw ICMPv6 — grows the counter too, and alone it is not an overflow); the clocks stepped > 50 ms during it — ``excl`` (``clock``); the
speed test's lock held or let go < 5 s + the series ago — ``excl`` (``speedtest``).

Errors (§3.3 «Ошибки»): only an error of ``sendto()`` itself makes an anchor ``na`` for the series — ENETUNREACH,
EHOSTUNREACH, EADDRNOTAVAIL, no IPv6 → ``no_route``; EPERM (own nft/ufw) → ``blocked``. An error at ``recv`` (or of a
connected DNS socket: ECONNREFUSED, EHOSTUNREACH from a router) is a loss of that packet only.

Result of a series (``ctx.probes`` of ``hostmon``)::

    {"anchors": [{"addr", "via": "icmp"|"dns", "n": sent, "lost", "runs", "rtt": [ms], "na": None|"no_route"|"blocked"}],
     "excl": bool, "why": None|"speedtest"|"noisy"|"overflow"|"clock", "trial": [addr]}

``trial`` — the anchors of a dark trial series (not in ``anchors``: no loss, no point data; NS-D19).

``runs`` — the runs of lost packets that began in this series (a run going on from the last counted series is not
counted again: dark series in a row are one event).
"""

import errno
import hashlib
import ipaddress
import os
import random
import secrets
import select
import socket
import struct
import time

DEFAULT_ANCHORS = ("1.1.1.1", "8.8.8.8", "77.88.8.8")
MAX_ANCHORS = 5
SCAN_MAX = 64                  # values of the setting looked at: the rest dropped unparsed (a .kbk, REV-ns-a5-sec З-2)
COUNT = 3                      # packets per anchor and series
GAP = 0.2                      # s between the packets of an anchor (ping -i 0.2)
WAIT = 1.0                     # s: a later answer is a loss
SERIES_S = (COUNT - 1) * GAP + WAIT + 0.1   # s a series may take at most (the last packet + WAIT + a margin)
PERIOD = 30.0                  # s between series × U[JITTER]
JITTER = (0.7, 1.3)
DATA_LEN = 56                  # bytes of echo data, as ping
RCVBUF = 16 * 1024
MAX_READS = 256                # reads per series: the 256-th ends it (noisy)
MAX_DROPPED = 64               # dropped packets per series: more — noisy
MAX_POLLS = 4096               # waits per series (a guard against a socket always «ready» with nothing in it; NS-D20)
OVFL_BURST = 3                 # packets read at once: below it the queue of 32 KB was never full (NS-D21)
CLOCK_STEP_NS = 50 * 1000000   # the wall clock against the monotonic one during a series
DARK_TO_DNS = 20               # dark ICMP series in a row (the others alive) -> DNS
ICMP_RETRY = 86400.0           # s on DNS before ICMP is tried again
RETRY_SPREAD = 3600.0          # s: + U[0; this] to the day of each anchor (REV-ns-a3-2 П-1: never all together)
SPEED_FRESH = 5.0              # s after the speed test let its lock go

# Linux values (asm-generic; x86-64 and arm64 alike) where Python names none.
SO_TIMESTAMPNS = getattr(socket, "SO_TIMESTAMPNS", 35)
SO_RXQ_OVFL = getattr(socket, "SO_RXQ_OVFL", 40)
SOL_RAW = getattr(socket, "SOL_RAW", 255)
ICMP_FILTER = 1                # linux/icmp.h: a set bit drops the type (types < 32 only)
ICMP6_FILTER = 1               # linux/icmpv6.h: a set bit drops the type
IPPROTO_ICMPV6 = getattr(socket, "IPPROTO_ICMPV6", 58)

ECHO = {socket.AF_INET: (8, 0), socket.AF_INET6: (128, 129)}   # request, reply
NAT64 = ipaddress.ip_network("64:ff9b::/96")
# Not anchors whatever ``is_global`` of this Python says (its list differs between 3.9 and 3.13+: REV-ns-a8-sec П-3) —
# the IANA special-purpose blocks that no echo reply comes from, listed explicitly so every Python refuses the same:
# 6to4 (``2002::/16``) carries an IPv4 of any kind inside, the local NAT64 prefix (RFC 8215) is the node's own network's,
# ``2001::/23`` — IETF protocol assignments (Teredo, ORCHIDv2 ``2001:20::/28``, benchmarking …), ``3fff::/20`` —
# documentation (RFC 9637), ``5f00::/16`` — SRv6 SIDs, ``192.88.99.0/24`` — the former 6to4 relay anycast, ``192.0.0.0/24``
# — IETF protocol assignments. ``::a.b.c.d`` (``::/96``) is caught by ``is_reserved``.
NOT_ANCHOR6 = tuple(ipaddress.ip_network(n) for n in (
    "::/8", "64:ff9b:1::/48", "100::/64", "2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20", "5f00::/16",
    "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8"))
NOT_ANCHOR4 = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24",
    "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4"))
NO_ROUTE = frozenset((errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT))
BLOCKED = frozenset((errno.EPERM,))
DENIED = frozenset((errno.EACCES, errno.EPERM))             # a socket we may not open: the next way
NO_PING = frozenset((errno.EPROTONOSUPPORT, errno.ESOCKTNOSUPPORT))   # a kernel without ping sockets
_TV = struct.Struct("@ll")     # struct timeval / timespec of the machine (8 + 8 bytes on 64-bit Linux)
DNS_QUERY_TAIL = b"\x01\x00" + b"\x00\x01\x00\x00\x00\x00\x00\x00" + b"\x00" + b"\x00\x02\x00\x01"   # RD; 1 question: . NS IN


# ---------------------------------------------------------------- anchors

def valid_anchor(text, own=()):
    """An anchor of the setting -> its address (IPv4 for ``::ffff:a.b.c.d`` and NAT64) or None: a literal only (no
    name — no DNS in the round), no ``%scope``, global and not multicast, not an address of the node (``own``)."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text or len(text) > 45 or "%" in text or "/" in text:
        return None
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        return None
    if any(ip in n for n in (NOT_ANCHOR6 if ip.version == 6 else NOT_ANCHOR4)):         # the same on every Python
        return None
    mine = set()
    for a in own or ():
        try:
            mine.add(ipaddress.ip_address(str(a).split("%")[0]))
        except ValueError:
            pass
    return None if ip in mine else str(ip)


def valid_anchors(values, own=()):
    """The anchors of the setting -> ([good, ≤ 5, no repeats, in order], dropped count). Not a list — all dropped. Only
    the first SCAN_MAX values are parsed (a list of a million from a ``.kbk`` costs no CPU every round)."""
    if not isinstance(values, (list, tuple)):
        return [], (0 if values is None else 1)
    good, bad = [], max(0, len(values) - SCAN_MAX)
    for v in values[:SCAN_MAX]:
        a = valid_anchor(v, own)
        if a is None:
            bad += 1
        elif a not in good:
            if len(good) >= MAX_ANCHORS:
                bad += 1
            else:
                good.append(a)
    return good, bad


def working_anchors(values, own=()):
    """The anchors in work -> ([checked, ≤ 5, in order; none good — the defaults], dropped count). The one place of
    the defaults: ``Prober.set_anchors`` and ``model.hostmon`` (A5, which gives ``cfg["anchors"]`` to the rule of A2)
    both call it, so the probes and the rule never see two lists."""
    good, bad = valid_anchors(values, own)
    return (good or list(DEFAULT_ANCHORS)), bad


def wanted(hm):
    """The flags of ``gateway.hostmon`` (the last round's): probes go only when ``enabled`` is not false and
    ``probes`` is exactly true (no key — off, §10.1 п. 22)."""
    return isinstance(hm, dict) and hm.get("enabled", True) is not False and hm.get("probes") is True


# ---------------------------------------------------------------- packets (pure)

def checksum(data):
    """The internet checksum (RFC 1071) of ``data``."""
    if len(data) % 2:
        data += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def payload(wall_ns):
    """56 bytes as iputils ping: byte i = i, the first bytes — ``struct timeval`` of ``wall_ns``."""
    tv = _TV.pack(wall_ns // 1000000000, (wall_ns % 1000000000) // 1000)
    return tv + bytes(range(len(tv), DATA_LEN))


def echo_request(fam, ident, seq, data):
    """An echo request: the header (type, code 0, checksum, id, number) + data. The checksum is ours for IPv4 (the
    kernel's ping socket writes its own, ICMPv6 — always the kernel's: it needs the addresses)."""
    req = ECHO[fam][0]
    head = struct.pack("!BBHHH", req, 0, 0, ident & 0xFFFF, seq & 0xFFFF)
    if fam == socket.AF_INET:
        cs = checksum(head + data)
        head = struct.pack("!BBHHH", req, 0, cs, ident & 0xFFFF, seq & 0xFFFF)
    return head + data


def parse_echo(buf, fam, raw):
    """A received ICMP packet -> (id, number, data) of an echo reply, or None (anything else). Raw IPv4 comes with
    the IP header (checked: version 4, 20 ≤ IHL ≤ 60, IHL ≤ length, protocol 1); raw IPv6 and ping sockets — the
    ICMP header at byte 0. The type and code are checked always (the kernel's filter of IPv4 passes types ≥ 32),
    the length is exactly 8 + 56, the ICMP checksum of IPv4 is checked (ICMPv6 — the kernel's: it needs the
    pseudo-header)."""
    b = bytes(buf)
    if raw and fam == socket.AF_INET:
        if len(b) < 20 or b[0] >> 4 != 4:
            return None
        ihl = (b[0] & 0x0F) * 4
        if ihl < 20 or ihl > 60 or ihl > len(b) or b[9] != 1:
            return None
        b = b[ihl:]
    if len(b) != 8 + DATA_LEN:
        return None
    typ, code, _cs, ident, seq = struct.unpack("!BBHHH", b[:8])
    if typ != ECHO[fam][1] or code != 0:
        return None
    if fam == socket.AF_INET and checksum(b) != 0:
        return None
    return ident, seq, b[8:]


def dns_query(qid):
    """``. NS IN`` with RD: 12 + 5 = 17 bytes, no name of a domain in it."""
    return struct.pack("!H", qid & 0xFFFF) + DNS_QUERY_TAIL


def parse_dns(buf):
    """A DNS answer -> its id when it is an answer (≥ 12 bytes, QR = 1), else None. Nothing further is read."""
    b = bytes(buf)
    if len(b) < 12 or not b[2] & 0x80:
        return None
    return struct.unpack("!H", b[:2])[0]


def icmp_filter_v4():
    """``ICMP_FILTER`` of a raw IPv4 socket: every type < 32 dropped but 0 (echo reply)."""
    return struct.pack("=I", 0xFFFFFFFF & ~(1 << 0))


def icmp_filter_v6():
    """``ICMP6_FILTER`` of a raw IPv6 socket: every type dropped but 129 (echo reply)."""
    words = [0xFFFFFFFF] * 8
    words[129 >> 5] &= ~(1 << (129 & 31)) & 0xFFFFFFFF
    return struct.pack("=8I", *words)


def cmsg_info(ancdata):
    """(kernel receive time in wall ns | None, the kernel's drop counter | None) from the ancillary data."""
    ts = drops = None
    for level, kind, data in ancdata or ():
        if level != socket.SOL_SOCKET:
            continue
        if kind == SO_TIMESTAMPNS and len(data) >= _TV.size:
            sec, nsec = _TV.unpack(data[:_TV.size])
            ts = sec * 1000000000 + nsec
        elif kind == SO_RXQ_OVFL and len(data) >= 4:
            drops = struct.unpack("=I", data[:4])[0]
    return ts, drops


# ---------------------------------------------------------------- the speed test's lock (read only)

def speed_lock_path():
    from .probe import run as probe_run
    return probe_run.speed_lock_path()


def speed_busy(path=None, fresh_s=SPEED_FRESH, now=None, locks="/proc/locks"):
    """True — the speed test holds its lock (a line of ``/proc/locks`` with the device and inode of the file) or let
    it go less than ``fresh_s`` ago (it touches the file then). Only ``os.stat`` and a read of /proc/locks: the lock
    is never taken (the test takes it with LOCK_NB and would skip its measure)."""
    path = path or speed_lock_path()
    try:
        st = os.stat(path)
    except OSError:
        return False
    now = time.time() if now is None else now
    if 0 <= now - st.st_mtime < fresh_s:
        return True                              # an mtime from the future (the clock went back) is an old one
    want = "%02x:%02x:%d" % (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
    try:
        with open(locks, "r") as f:
            text = f.read(256 * 1024)
    except OSError:
        return False
    for line in text.splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[1] == "->":
            parts = parts[:1] + parts[2:]
        if len(parts) > 5 and parts[5] == want:
            return True
    return False


# ---------------------------------------------------------------- the system calls of a series

class Net(object):
    """What a series asks of the system; the tests give a fake one."""

    def socket(self, fam, kind, proto):
        return socket.socket(fam, kind, proto)

    def poll(self, socks, timeout):
        """-> the sockets ready to read (or with an error) within ``timeout`` s."""
        p = select.poll()
        by = {}
        for s in socks:
            by[s.fileno()] = s
            p.register(s.fileno(), select.POLLIN | select.POLLERR)
        ms = max(0, int(timeout * 1000 + 0.999))
        return [by[fd] for fd, _ev in p.poll(ms) if fd in by]

    def time_ns(self):
        return time.time_ns()

    def monotonic_ns(self):
        return time.monotonic_ns()

    def speed_busy(self, fresh_s):
        return speed_busy(fresh_s=fresh_s)

    def randbits(self, n):
        return secrets.randbits(n)

    def pid(self):
        return os.getpid()


ANC = socket.CMSG_SPACE(_TV.size) + socket.CMSG_SPACE(4) if hasattr(socket, "CMSG_SPACE") else 128


class _Slot(object):
    """One anchor in one series."""

    def __init__(self, addr, fam, via):
        self.addr, self.ip, self.fam, self.via = addr, ipaddress.ip_address(addr), fam, via
        self.kind = None        # "dgram" | "raw" | "dns"
        self.sock = None
        self.ident = None
        self.sent = {}          # seq -> (wall ns, mono ns, data | dns id)
        self.got = {}           # seq -> rtt ms
        self.na = None          # None | "no_route" | "blocked" (an error of sendto itself)
        self.n = 0              # packets tried
        self.runs = 0
        self.trial = False      # ICMP on trial: no answer of this anchor yet in this process (NS-D19)


class Prober(object):
    """The light probes of a process. ``anchors`` — the setting (checked here again, ≤ 5; empty — the defaults),
    ``rng`` — ``random.Random`` (the interval and the order; the tests fix the seed), ``own`` — the node's
    addresses (an anchor may not be one), ``net`` — the system calls (tests)."""

    def __init__(self, anchors=None, rng=None, own=(), net=None):
        self.rng = rng or random.Random()
        self.net = net or Net()
        self.icmp = {}          # family -> "dgram" | "raw" | "none": the way found, kept for the process
        self.state = {}         # addr -> {"via", "dark", "dns_at", "tail", "ok": an ICMP answer in this process,
                                #          "dns_dark": dark DNS series in a row (NS-D22)}
        self.anchors = []
        self.dropped = 0
        self.next_at = None
        self.set_anchors(anchors, own)

    def set_anchors(self, values, own=()):
        good, self.dropped = working_anchors(values, own)
        self.anchors = good
        for a in list(self.state):
            if a not in good:
                del self.state[a]
        for a in good:
            self.state.setdefault(a, {"via": "icmp", "dark": 0, "dns_at": None, "tail": False, "ok": False,
                                      "dns_dark": 0})

    def until(self, mono):
        """Seconds until the next series (the first one comes in 30 s × U[0,1; 1,3] after the first ask)."""
        if self.next_at is None:
            self.next_at = mono + PERIOD * self.rng.uniform(0.1, JITTER[1])
        return max(0.0, self.next_at - mono)

    def due(self, mono):
        return self.until(mono) <= 0

    # -- one series

    def series(self, mono=None):
        """One series now (the caller checked the flags: ``step``). -> the result (the module's doc)."""
        mono = time.monotonic() if mono is None else mono
        self.next_at = mono + PERIOD * self.rng.uniform(*JITTER)
        net = self.net
        order = list(self.anchors)
        self.rng.shuffle(order)
        for a in order:
            st = self.state[a]
            if st["via"] == "dns" and st["dns_at"] is not None and mono - st["dns_at"] >= ICMP_RETRY:
                st["via"], st["dark"], st["dns_at"] = "icmp", 0, None      # once a day ICMP again, on trial
                st["ok"] = False
        if net.speed_busy(SPEED_FRESH):
            return self._result([], "speedtest")                          # not a packet while it measures
        w0, m0 = net.time_ns(), net.monotonic_ns()
        slots, socks, raw = [], [], {}
        try:
            for a in order:
                s = _Slot(a, socket.AF_INET6 if ":" in a else socket.AF_INET, self.state[a]["via"])
                self._open(s, raw, socks)
                s.trial = s.via == "icmp" and not self.state[a]["ok"]
                slots.append(s)
            why = self._run(slots, raw)
        finally:
            for sock in socks:
                try:
                    sock.close()
                except OSError:
                    pass
        w1, m1 = net.time_ns(), net.monotonic_ns()
        if why is None and net.speed_busy(SPEED_FRESH + (m1 - m0) / 1e9):
            why = "speedtest"
        if why is None and abs((w1 - w0) - (m1 - m0)) > CLOCK_STEP_NS:
            why = "clock"
        if why is None:
            self._learn(slots, mono)
        return self._result(slots, why)

    def _open(self, s, raw, socks):
        """The socket of an anchor: its own ping socket, the family's raw one (with the filter), or its own DNS
        socket. ``raw`` — {family: (socket, {id: slot}) | "noaf" | None} of this series."""
        if s.via == "icmp":
            mode = self.icmp.get(s.fam, "dgram")
            if mode == "dgram":
                proto = socket.IPPROTO_ICMP if s.fam == socket.AF_INET else IPPROTO_ICMPV6
                try:
                    sock = self.net.socket(s.fam, socket.SOCK_DGRAM, proto)
                except OSError as e:
                    if e.errno == errno.EAFNOSUPPORT:
                        s.na = "no_route"                                 # no IPv6 on the node
                        return
                    if e.errno not in DENIED and e.errno not in NO_PING:
                        raise
                    mode = self.icmp[s.fam] = "raw"
                else:
                    self.icmp[s.fam] = "dgram"
                    socks.append(sock)
                    self._setup(sock)
                    s.kind, s.sock = "dgram", sock
                    return
            if mode == "raw":
                if s.fam not in raw:
                    raw[s.fam] = self._raw(s.fam, socks)
                r = raw[s.fam]
                if r == "noaf":
                    s.na = "no_route"
                    return
                if r is not None:
                    s.kind, s.sock = "raw", r[0]
                    s.ident = self._raw_id(r[1])
                    r[1][s.ident] = s
                    return
            s.via = "dns"                                                 # no ICMP way for this family
        s.kind = "dns"
        try:
            sock = self.net.socket(s.fam, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        except OSError as e:
            if e.errno == errno.EAFNOSUPPORT:
                s.na = "no_route"
                return
            raise
        socks.append(sock)
        self._setup(sock)
        try:
            sock.connect((s.addr, 53))
        except OSError as e:
            s.na = self._na(e)
            if s.na is None:
                raise
            return
        s.sock = sock

    def _raw(self, fam, socks):
        """The raw socket of a family for this series, only with the kernel's filter. -> (socket, {}) | "noaf" (no
        such family on the node) | None (no raw way: DNS, kept for the process)."""
        proto = socket.IPPROTO_ICMP if fam == socket.AF_INET else IPPROTO_ICMPV6
        try:
            sock = self.net.socket(fam, socket.SOCK_RAW, proto)
        except OSError as e:
            if e.errno == errno.EAFNOSUPPORT:
                return "noaf"
            if e.errno not in DENIED:
                raise
            self.icmp[fam] = "none"
            return None
        socks.append(sock)
        try:
            if fam == socket.AF_INET:
                sock.setsockopt(SOL_RAW, ICMP_FILTER, icmp_filter_v4())
            else:
                sock.setsockopt(IPPROTO_ICMPV6, ICMP6_FILTER, icmp_filter_v6())
        except OSError:
            self.icmp[fam] = "none"                                       # no filter — no raw
            return None
        self.icmp[fam] = "raw"
        self._setup(sock)
        return sock, {}

    def _raw_id(self, ids):
        """A random 16-bit id of an anchor in this series: not 0, not the PID's, not another anchor's."""
        pid = self.net.pid() & 0xFFFF
        while True:
            i = self.net.randbits(16)
            if i and i != pid and i not in ids:
                return i

    def _setup(self, sock):
        """Non-blocking, a 16 KB queue, the kernel's receive time and drop counter (best effort: without the time
        both ends are taken by the monotonic clock; without the counter an overflow is a loss)."""
        sock.setblocking(False)
        for level, opt, val in ((socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF),
                                (socket.SOL_SOCKET, SO_TIMESTAMPNS, 1), (socket.SOL_SOCKET, SO_RXQ_OVFL, 1)):
            try:
                sock.setsockopt(level, opt, val)
            except OSError:
                pass

    @staticmethod
    def _na(e):
        if e.errno in NO_ROUTE:
            return "no_route"
        if e.errno in BLOCKED:
            return "blocked"
        return None

    def _send(self, s, seq):
        """Packet ``seq`` to the anchor; the time of the sending is taken right before ``sendto`` and goes into the
        packet (``timeval``). An error of the sending itself: no route / blocked — the anchor is ``na`` for the
        series; another one (ENOBUFS …) — this packet is lost."""
        net = self.net
        try:
            s.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)          # a late error of the path (an ICMP
        except OSError:                                                   # «unreachable» to the last packet) is
            pass                                                          # that packet's loss, not this send's
        s.n += 1
        if s.kind == "dns":
            qid = net.randbits(16)
            while any(v[2] == qid for v in s.sent.values()):
                qid = net.randbits(16)
            key, pkt = qid, dns_query(qid)
            w, m = net.time_ns(), net.monotonic_ns()
        else:
            w, m = net.time_ns(), net.monotonic_ns()
            key = payload(w)
            pkt = echo_request(s.fam, s.ident or 0, seq, key)
        try:
            if s.kind == "dns":
                s.sock.send(pkt)
            else:
                s.sock.sendto(pkt, (s.addr, 0))
        except OSError as e:
            s.na = self._na(e)
            return
        s.sent[seq] = (w, m, key)
        if s.kind == "dgram" and s.ident is None:
            try:
                s.ident = s.sock.getsockname()[1]                           # the kernel's id of this ping socket
            except (OSError, IndexError, TypeError):
                s.ident = None

    def _run(self, slots, raw):
        """Send and read until every packet is answered or past its second. -> None | "noisy" | "overflow"."""
        net = self.net
        live = [s for s in slots if s.na is None and s.sock is not None]
        if not live:
            return None
        raw_by = dict((v[0], (fam, v[1])) for fam, v in raw.items() if isinstance(v, tuple))
        by_sock = dict((s.sock, s) for s in live if s.kind != "raw")
        socks = list(by_sock) + list(raw_by)
        reads = dropped = polls = 0
        ovfl, burst = set(), {}                      # sockets whose drop counter grew; most packets read at once
        trunc = set()                                # sockets that read a truncated packet (a queue full alone)
        raw4 = set(v[0] for fam, v in raw.items() if isinstance(v, tuple) and fam == socket.AF_INET)
        t0 = net.monotonic_ns()
        k = 0
        while True:
            now = net.monotonic_ns()
            while k < COUNT and now >= t0 + int(k * GAP * 1e9):
                k += 1
                for s in live:
                    if s.na is None:
                        self._send(s, k)
                now = net.monotonic_ns()
            ends = [m + int(WAIT * 1e9) for s in live if s.na is None
                    for seq, (_w, m, _key) in s.sent.items() if seq not in s.got]
            ends = [t for t in ends if t > now]
            if k < COUNT:
                ends.append(t0 + int(k * GAP * 1e9))
            if not ends:
                break
            polls += 1
            if polls > MAX_POLLS:
                return "noisy"                       # a socket «ready» with nothing to read, again and again
            for sock in net.poll(socks, max(0.0, (min(ends) - now) / 1e9)):
                at_once = 0
                while True:
                    try:
                        data, anc, flags, src = sock.recvmsg(1500, ANC)
                    except (BlockingIOError, InterruptedError):
                        break
                    except OSError:
                        data = None                  # an error from the path: that packet is lost, not na
                    reads += 1
                    if data is not None:
                        at_once += 1
                        if flags & getattr(socket, "MSG_TRUNC", 0x20):
                            trunc.add(sock)
                        burst[sock] = max(burst.get(sock, 0), at_once)
                        rm = net.monotonic_ns()
                        try:
                            ok, drops = self._take(sock, by_sock, raw_by, data, anc, flags, src, rm)
                        except Exception:            # noqa: BLE001 — a packet never fails the series, nor its bytes
                            ok, drops = False, None
                        if drops:
                            ovfl.add(sock)
                        if ok is False:
                            dropped += 1
                    if reads >= MAX_READS:
                        return "noisy"
        if dropped > MAX_DROPPED:
            return "noisy"
        for sock in ovfl:
            full = sock in raw4 or sock in trunc or burst.get(sock, 0) >= OVFL_BURST
            if full and self._missing(sock, by_sock, raw_by):
                return "overflow"
        return None

    @staticmethod
    def _missing(sock, by_sock, raw_by):
        """A packet sent through ``sock`` is still without its answer (only then could the kernel's drop have been
        one of ours)."""
        slots = [by_sock[sock]] if sock in by_sock else list(raw_by[sock][1].values()) if sock in raw_by else []
        return any(seq not in s.got for s in slots for seq in s.sent)

    def _take(self, sock, by_sock, raw_by, data, anc, flags, src, rm):
        """One packet read -> (True ours | None ours, not counted: a duplicate or out of time | False dropped,
        the kernel's drop counter | None)."""
        ts, drops = cmsg_info(anc)
        if flags & getattr(socket, "MSG_TRUNC", 0x20):
            return False, drops
        if sock in raw_by:
            fam, ids = raw_by[sock]
            got = parse_echo(data, fam, True)
            s = ids.get(got[0]) if got is not None else None
            return (False if s is None else self._echo(s, got, src, ts, rm)), drops
        s = by_sock.get(sock)
        if s is None:
            return False, drops
        if s.kind == "dns":
            qid = parse_dns(data)
            for seq, (w, m, key) in s.sent.items():
                if qid is not None and key == qid:
                    return self._got(s, seq, w, m, ts, rm), drops
            return False, drops
        got = parse_echo(data, s.fam, False)
        return (False if got is None else self._echo(s, got, src, ts, rm)), drops

    def _echo(self, s, got, src, ts, rm):
        """An echo reply of the slot's socket: the sender, the id, the number and all 56 bytes."""
        ident, seq, data = got
        try:
            sender = ipaddress.ip_address(str(src[0]).split("%")[0])
        except (ValueError, TypeError, IndexError):
            return False
        if sender != s.ip or s.na is not None or s.ident is None or ident != s.ident:
            return False
        sent = s.sent.get(seq)
        if sent is None or sent[2] != data:
            return False
        return self._got(s, seq, sent[0], sent[1], ts, rm)

    @staticmethod
    def _got(s, seq, w, m, ts, rm):
        if seq in s.got:
            return None                          # a duplicate: counted once
        rtt = (ts - w) if ts is not None else (rm - m)
        if rtt < 0 or rtt > WAIT * 1e9:
            return None                          # out of time: the packet stays lost
        s.got[seq] = round(rtt / 1e6, 3)
        return True

    def _learn(self, slots, mono):
        """After a counted series: the runs of losses and the dark series of ICMP (20 in a row while another
        anchor answers — DNS)."""
        alive = dict((s.addr, bool(s.got)) for s in slots if s.na is None and s.n)
        for s in slots:
            st = self.state.get(s.addr)
            if st is None or s.na is not None or not s.n:
                continue
            if s.trial and not s.got:                # a dark trial: no loss; 20 of them — DNS until the daily try
                st["tail"], s.runs = False, 0
                st["dark"] += 1
                if st["dark"] >= DARK_TO_DNS:
                    self._to_dns(st, mono)
                continue
            if s.trial:
                st["ok"] = True                      # the first answer: «in account», this series counts already
            runs, prev = 0, st["tail"]
            for q in range(1, s.n + 1):
                lost = q not in s.got
                if lost and not prev:
                    runs += 1
                prev = lost
            st["tail"], s.runs = prev, runs
            if s.via != "icmp":
                st["dns_dark"] = 0 if s.got else st.get("dns_dark", 0) + 1
                if st["dns_dark"] >= DARK_TO_DNS:          # NS-D22: a dark DNS — ICMP at once, on trial
                    st["via"], st["dark"], st["dns_at"], st["ok"], st["dns_dark"] = "icmp", 0, None, False, 0
                continue
            if s.got:
                st["dark"] = 0
            elif any(v for a, v in alive.items() if a != s.addr):
                st["dark"] += 1
                if st["dark"] >= DARK_TO_DNS:
                    self._to_dns(st, mono)

    def _to_dns(self, st, mono):
        """ICMP dark — DNS until the day of this anchor (+ U[0; RETRY_SPREAD]: the anchors apart, REV-ns-a3-2 П-1)."""
        st["via"], st["dark"], st["ok"], st["dns_dark"] = "dns", 0, False, 0
        st["dns_at"] = mono + self.rng.uniform(0.0, RETRY_SPREAD)

    def _result(self, slots, why):
        """Only the counts and times go out — nothing of a packet."""
        excl = why is not None
        by = dict((s.addr, s) for s in slots)
        out, trial = [], []
        for a in self.anchors:
            s = by.get(a)
            if s is not None and s.trial and not s.got and s.na is None:
                trial.append(a)                      # a dark trial series: no point data at all (NS-D19)
                continue
            if s is None:
                out.append({"addr": a, "via": self.state[a]["via"], "n": 0, "lost": 0, "runs": 0, "rtt": [],
                            "na": None})
                continue
            n = s.n if s.na is None else 0
            rtt = [s.got[q] for q in sorted(s.got)] if n else []
            out.append({"addr": a, "via": s.via, "n": n, "lost": n - len(rtt), "runs": 0 if excl else s.runs,
                        "rtt": rtt, "na": s.na})
        return {"anchors": out, "excl": excl, "why": why, "trial": trial}


def step(prober, hm, mono, own=(), offline=False):
    """The pause of the collector asks: the flags of the last round first (no probes — no socket), the node offline
    (``node_offline`` of the collector: no series at all, NS-D22), then the anchors of the setting (checked again),
    then is a series due. -> result | None."""
    if not wanted(hm) or offline:
        return None
    prober.set_anchors(hm.get("anchors"), own)
    if not prober.due(mono):
        return None
    return prober.series(mono)


# ---------------------------------------------------------------- the canary work

CANARY_EVERY = 600.0
CANARY_BUSY = 50.0             # % of the CPU: at or above — no canary (RS §3.5)
CANARY_BUF = bytes(range(256)) * 256          # 64 KB, fixed
CANARY_ROUNDS = 512                           # 32 MB of sha256 ≈ 50 ms on a VPS core
CANARY_S = 0.25                               # s the pause must still have for it (≈ 50 ms, ×5 for a slow core)


class Canary(object):
    """A fixed piece of work timed with ``perf_counter``: how fast the node's CPU is for us right now."""

    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.next_at = None

    def maybe(self, mono, busy):
        """-> ms of the work, or None (not yet, or the CPU is busy ≥ 50 % / unknown)."""
        if self.next_at is not None and mono < self.next_at:
            return None
        if busy is None or busy >= CANARY_BUSY:
            return None
        self.next_at = mono + CANARY_EVERY
        t = self.clock()
        h = hashlib.sha256()
        for _ in range(CANARY_ROUNDS):
            h.update(CANARY_BUF)
        h.digest()
        return round((self.clock() - t) * 1000.0, 2)
