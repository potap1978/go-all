"""«Связь с людьми» (SPEC-node-score §6.2 B17, NS-D26): the delay and the resent share of the TCP connections of the
people with the VPN inputs of the node, read passively from the kernel — no test, no traffic of ours.

    plan(st, fronts)          -> {key: (unit, port) | "udp" | "shared_port" | "no_data"}   the inputs of the node
                              (OpenVPN with UDP and TCP: also ``openvpn_udp`` = "udp")
    fronts_443()              -> bool   xray of the gateway holds 443 with VLESS + TLS (site/front.py «xray in front»)
    People(dump=None)         the state of the process: ``step(plan, mono, now)`` once a minute (the collector calls it
                              in the pause of a round), ``take()`` -> [row of host_people_h1], ``keep(rows)``
    summary(rows)             -> {…} | None   the line of a day from its rows (for /api/host/score ``rows.people``)
    request(family, seq, ports) / parse_datagram(buf, seq, sink, room) / tcp_fields(buf, start, end)   pure parsing

Source — ``NETLINK_SOCK_DIAG`` (``inet_diag_req_v2``, ``IPPROTO_TCP``, state ESTABLISHED, ``INET_DIAG_INFO`` →
``struct tcp_info``), AF_INET and AF_INET6 (xray listens on ``[::]``: an IPv4 person is a mapped address of an AF_INET6
socket). The kernel filters by the local ports of the inputs (``INET_DIAG_REQ_BYTECODE`` of ``S_GE``/``S_LE`` pairs —
every kernel since 2.6 knows them); a kernel that refuses the filter (EINVAL) gets the dump without it once and the
filter is not asked again by this process; the local port is checked here anyway.

A socket counts for an input only when it belongs to THE UNIT of that input and its local port is the port of that
input: nginx (the site, the panel on the domain, /p/, the subscription), sshd, the panel and the bot belong to no unit
of an input. The unit of a socket — ``INET_DIAG_CGROUP_ID`` (5.9+; an accepted socket keeps the cgroup of its listener)
against the inode of ``/sys/fs/cgroup/system.slice/<unit>/`` (cgroup v2; v1 — ``old_cgroup``, the path is not guessed;
no such directory — ``no_cgroup``): /proc is not read at all. A kernel without the attribute: the processes of the unit
(``cgroup.procs``, at most ``MAX_PIDS``) and their sockets (``readlink /proc/<pid>/fd/*`` = ``socket:[inode]``, at most
``MAX_FDS``), only in a minute that has a socket not attributed yet and within the same ``DEADLINE_S`` as the dump.

PRIVACY (NS-D26): the parser takes from a socket its LOCAL port (the port of our input), the state, ``idiag_cookie``
(the key of the connection — only inside this process, never written), ``idiag_inode`` and the counters of
``tcp_info``. The addresses and the ports of the people are never unpacked: they are not in memory beyond the bytes of
the datagram being parsed, not in a row, not in the journal; nothing leaves the node. A row holds only the aggregates of
an hour and, in ``inputs``, the names of the inputs with a code — no port.

Foreign bytes (the answer of the kernel is trusted no more than a file): every length is checked before the field it
covers — a message shorter than its header or longer than the datagram, an attribute shorter than 4 bytes or longer
than its message, ``tcp_info`` shorter than the last field we take (an old kernel: ``old_kernel``), a counter above
2^63−1 (the socket gives no number); a datagram cut by ``MSG_TRUNC`` ends the dump of that family. Flood: at most
``MAX_DUMP_BYTES`` read and ``MAX_SOCKS`` sockets taken per sample, ``DEADLINE_S`` for the whole sample, ``MAX_TRACK``
connections followed at once — beyond, the rest is left out (a sample cut short loses nothing it saw). A sample that
did not see everything (a limit, the deadline, one family failing in the middle, a cut list of processes) is NOT WHOLE:
it is no base for the next one and the row counts it in ``partial``. Anyone can open connections to an input (Reality
forwards a stranger to its mask site; SSTP, ocserv, SOCKS5 before the login): the row is for reference, not in the score.

The hour (in memory): per connection the counters at its first sight in the hour (the base) and at its last sight.
A connection first seen in a sample that follows the previous one within ``GAP_S`` is new: its base is 0 (all it sent
is of this hour); after a gap (the first sample of the process, the input unreadable before) its base is what it shows
now (its past is unknown). At the first sample of a new hour the previous one is closed into a row with what the
connections showed at their last sight (the bytes between the last sample of an hour and the first of the next go to
the next), the connections not in the last sample are forgotten, the rest start the hour from their last values
(after a gap longer than ``GAP_S`` nothing is carried). After a whole sample the connections gone with Δ < ``MIN_BYTES``
are forgotten (they never count). The hour is the UTC hour of the wall clock; a clock going back keeps the open hour
(more than an hour back — the open hour is forgotten without a row).

A row of ``host_people_h1`` (SS §3.1): ``{"ts", "conns", "rtt_med", "rtt_p90", "rtt_min_med", "sent", "retr", "dsack",
"why", "inputs", "partial"}`` — ``ts`` the start of the hour (UTC seconds), ``conns`` connections with Δ ``bytes_sent`` ≥
``MIN_BYTES``, the delays in ms by the last ``tcpi_rtt`` / ``tcpi_min_rtt`` of each of them (one connection — one
vote), ``sent``/``retr`` Δ bytes, ``dsack`` Δ ``dsack_dups`` × ``snd_mss`` (bytes resent needlessly), ``inputs`` —
a dict {key: "ok" | "few" | "shared_port" | "udp" | "no_data"} (the writer stores it as JSON; a read input is
``ok`` when the row has numbers, ``few`` when it has none), ``partial`` — samples of the hour not whole. ``why``:
``None`` (the numbers are there), ``few`` (fewer than ``MIN_CONNS`` connections or less than ``MIN_SENT`` bytes: no
numbers), ``no_tcp_inbound`` (no input with TCP to measure), ``shared_443`` (the only TCP input is VLESS sharing 443
with the site), or, when no TCP input could be read the whole hour, the reason — ``no_diag`` (no sock_diag in the
kernel, no netlink), ``old_kernel`` (``tcp_info`` without ``bytes_sent``), ``old_cgroup`` (cgroup v1), ``no_cgroup``
(the unit has no cgroup directory), ``no_access`` (the cgroup or the descriptors of the unit cannot be read). The
thresholds are an assumption of the spec, calibrated at B9.
"""

import errno
import json
import os
import socket
import struct
import time
from collections import deque

from .. import paths

MINUTE_S = 60.0                    # one sample a minute
GAP_S = 150.0                      # s: two samples further apart are not continuous (see the docstring)
HOUR_S = 3600
MIN_BYTES = 64 * 1024              # a connection counts with Δ bytes_sent ≥ this in the hour
MIN_CONNS = 20                     # fewer counted connections in the hour — «few»
MIN_SENT = 50 * 1024 * 1024        # less Σ Δ bytes_sent in the hour — «few»
MAX_DUMP_BYTES = 16 * 1024 * 1024  # bytes read from netlink per sample (both families)
MAX_SOCKS = 20000                  # sockets taken per sample (sec З-1: anyone can open connections — bounded memory)
MAX_TRACK = 20000                  # connections followed at once in an hour (the gone small ones are forgotten)
MAX_PIDS = 256                     # processes of a unit read (fallback without INET_DIAG_CGROUP_ID)
MAX_FDS = 262144                   # descriptors of a process read (the same fallback)
MAX_PORTS = 16                     # ports in the filter of the kernel
DEADLINE_S = 2.0                   # s for the whole sample: the dump and the fallback walk of /proc together
INT63 = (1 << 63) - 1              # a counter above it cannot be (u64-max of a broken answer): never in a row
RECV_BUF = 65536
KEEP_ROWS = 48                     # rows of finished hours kept while nobody takes them

# netlink / inet_diag (linux/netlink.h, linux/sock_diag.h, linux/inet_diag.h)
NETLINK_SOCK_DIAG = 4
SOCK_DIAG_BY_FAMILY = 20
NLMSG_NOOP, NLMSG_ERROR, NLMSG_DONE = 1, 2, 3
NLM_F_REQUEST, NLM_F_DUMP = 0x1, 0x300
IPPROTO_TCP = 6
TCP_ESTABLISHED = 1
INET_DIAG_REQ_BYTECODE = 1
INET_DIAG_INFO = 2
INET_DIAG_CGROUP_ID = 21           # __u64, 5.9+: the cgroup v2 id of the socket (= of its listener for an accepted one)
BC_JMP, BC_S_GE, BC_S_LE, BC_D_GE, BC_D_LE = 1, 2, 3, 4, 5
NLA_TYPE_MASK = 0x3FFF
AF_INET, AF_INET6 = 2, 10
# the kernel has no such family / no sock_diag (IPv6 off, an old kernel): a family absent, not a part of a sample missing
UNSUPPORTED = (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.ENOENT, errno.EOPNOTSUPP)

_NLH = struct.Struct("=IHHII")     # nlmsghdr: len, type, flags, seq, pid
_RTA = struct.Struct("=HH")        # rtattr / nlattr: len, type
_OP = struct.Struct("=BBH")        # inet_diag_bc_op: code, yes, no
NLH_LEN = 16
MSG_LEN = 72                       # struct inet_diag_msg
REQ_LEN = 56                       # struct inet_diag_req_v2

# struct tcp_info (linux/uapi/linux/tcp.h; the test checks them against the member list of the header, a fixture)
TI_SND_MSS = 16                    # __u32 tcpi_snd_mss
TI_RTT = 68                        # __u32 tcpi_rtt, µs
TI_MIN_RTT = 148                   # __u32 tcpi_min_rtt, µs (4.10+)
TI_BYTES_SENT = 200                # __u64 tcpi_bytes_sent (4.19+)
TI_BYTES_RETRANS = 208             # __u64 tcpi_bytes_retrans (4.19+)
TI_DSACK_DUPS = 216                # __u32 tcpi_dsack_dups (4.19+)
TI_NEED = TI_DSACK_DUPS + 4        # the shortest tcp_info we take

# the inputs with TCP: (key, unit, field of the port in the record, the default port)
TCP_INPUTS = (
    ("vless", "kaskad-gw-xray.service", "port", 443),
    ("socks5", "kaskad-gw-xray.service", "port", 1080),
    ("sstp", "kaskad-gw-accel.service", "port", 4433),
    ("ocserv", "kaskad-gw-ocserv.service", "port", 4443),
    ("openvpn", "kaskad-gw-ovpn-tcp.service", "tcp_port", 0),      # 0: no TCP instance — «udp»
)
# the data of these goes outside TCP: UDP (AmneziaWG, WireGuard, IKEv2, L2TP/IPsec), GRE (PPTP: TCP 1723 is control only)
NOT_TCP = ("wg", "awg1", "awg2", "awg3", "ikev2", "l2tp", "pptp")
SHARED_PORT = 443                  # VLESS on 443 with «xray in front»: the people and the site in one process, one port
OVPN_UDP = "openvpn_udp"           # OpenVPN with both UDP and TCP: the UDP part — a key of its own, «udp»
CG_DIR = "/sys/fs/cgroup/system.slice/%s"

OK, FEW, SHARED, UDP, NO_DATA = "ok", "few", "shared_port", "udp", "no_data"
NO_CGROUP = "no_cgroup"
# why of a row with no TCP input read the whole hour (order: the tie-break of a mixed hour)
REASONS = ("no_diag", "old_kernel", "old_cgroup", NO_CGROUP, "no_access")
SHARED_443 = "shared_443"          # why: the only TCP input is VLESS sharing 443 with the site
_MORE, _DONE, _BAD, _FULL = "more", "done", "bad", "full"
_OLD, _INVALID = "old", "invalid"
_CUT, _LATE = "cut", "late"        # sock_inodes: more than MAX_FDS / the deadline came (both: the sample is not whole)


# ── the plan: which inputs, which unit, which port ─────────────────────────

def plan(st, fronts=False):
    """{key: (unit, port)} for an input to measure, {key: "udp" | "shared_port" | "no_data"} for one that is not.
    ``fronts`` — xray of the gateway holds 443 with VLESS + TLS (``fronts_443``). Only the inputs that should run
    (installed, enabled, the gateway on)."""
    from .inbounds import tunnel
    out = {}
    gw = st.get("gateway") if isinstance(st, dict) else None
    recs = (gw or {}).get("inbounds") if isinstance(gw, dict) else None
    if not isinstance(recs, dict):
        return out
    tcp = dict((k, (u, f, d)) for k, u, f, d in TCP_INPUTS)
    for kind in sorted(recs):
        if not isinstance(kind, str) or not tunnel.wanted(st, kind):
            continue
        if kind in tcp:
            unit, field, dflt = tcp[kind]
            port = tunnel.port_value(recs.get(kind) or {}, field, dflt)
            if not port:
                out[kind] = UDP
            elif kind == "vless" and port == SHARED_PORT and fronts:
                out[kind] = SHARED
            else:
                out[kind] = (unit, port)
            if kind == "openvpn" and port:            # TCP measured; its UDP part (on unless udp_port 0) is not
                rec = recs.get(kind) or {}
                if not isinstance(rec, dict) or "udp_port" not in rec or tunnel.port_value(rec, "udp_port", 0):
                    out[OVPN_UDP] = UDP
        elif kind in NOT_TCP:
            out[kind] = UDP
        else:
            out[kind] = NO_DATA                       # an input this version does not know
    return out


_FRONT = {"sig": None, "val": False}


def fronts_443():
    """site/front.py «xray in front»: the deployed xray.json of the gateway has VLESS + TLS on 443. Read again only when
    the file changed (mtime, size)."""
    from . import xray
    from ..site import front
    path = xray.config_path()
    try:
        s = os.stat(path)
        sig = (s.st_mtime_ns, s.st_size)
    except OSError:
        _FRONT.update(sig=None, val=False)
        return False
    if sig != _FRONT["sig"]:
        try:
            val = bool(front.config_fronts(front._deployed()))
        except Exception:                            # noqa: BLE001 — an unreadable config: not in front
            val = False
        _FRONT.update(sig=sig, val=val)
    return _FRONT["val"]


# ── netlink: the request and the parsing (pure) ────────────────────────────

def _align(n):
    return (n + 3) & ~3


def bytecode(ports, dst=False):
    """INET_DIAG_REQ_BYTECODE: «sport == p1 or sport == p2 …» from S_GE/S_LE pairs (accepted when the run ends exactly
    at the end of the code, rejected when it jumps past it — inet_diag_bc_run). Each port but the last: GE, LE and a
    JMP to the end (20 bytes); the last: GE, LE (16 bytes). ``dst`` — the remote port (D_GE/D_LE, the same layout):
    8c of the server check over iperf3 (``bench.suite.iperf_tcp``)."""
    ge, le = (BC_D_GE, BC_D_LE) if dst else (BC_S_GE, BC_S_LE)
    ports = sorted(set(int(p) for p in ports))
    total = 20 * (len(ports) - 1) + 16
    out = b""
    for i, p in enumerate(ports):
        s = 20 * i
        last = i == len(ports) - 1
        if last:
            out += _OP.pack(ge, 8, total - s + 4) + _OP.pack(0, 0, p)
            out += _OP.pack(le, 8, total - s - 8 + 4) + _OP.pack(0, 0, p)
        else:
            out += _OP.pack(ge, 8, 20) + _OP.pack(0, 0, p)
            out += _OP.pack(le, 8, 12) + _OP.pack(0, 0, p)
            out += _OP.pack(BC_JMP, 4, total - s - 16)
    return out


def request(family, seq, ports=None, dst=False):
    """nlmsghdr + inet_diag_req_v2 (TCP, ESTABLISHED, INET_DIAG_INFO) [+ the filter of the local ports; ``dst`` — of
    the remote ones]."""
    body = struct.pack("=BBBBI", family, IPPROTO_TCP, 1 << (INET_DIAG_INFO - 1), 0, 1 << TCP_ESTABLISHED)
    body += b"\0" * (REQ_LEN - 8)                     # inet_diag_sockid: any address, any port, no cookie
    if ports:
        bc = bytecode(ports, dst)
        body += _RTA.pack(4 + len(bc), INET_DIAG_REQ_BYTECODE) + bc
    return _NLH.pack(NLH_LEN + len(body), SOCK_DIAG_BY_FAMILY, NLM_F_REQUEST | NLM_F_DUMP, seq, 0) + body


def tcp_fields(buf, start, end):
    """(rtt µs, min_rtt µs, snd_mss, bytes_sent, bytes_retrans, dsack_dups) of a tcp_info in buf[start:end];
    ``"old"`` when it is shorter than the last field taken; ``"invalid"`` when a counter cannot be (above 2^63−1 — the
    u64-max of a broken answer; dsack_dups or snd_mss at u32-max): such a socket gives no number."""
    if end - start < TI_NEED:
        return _OLD
    (mss,) = struct.unpack_from("=I", buf, start + TI_SND_MSS)
    (rtt,) = struct.unpack_from("=I", buf, start + TI_RTT)
    (mrtt,) = struct.unpack_from("=I", buf, start + TI_MIN_RTT)
    sent, retr = struct.unpack_from("=QQ", buf, start + TI_BYTES_SENT)
    (dsack,) = struct.unpack_from("=I", buf, start + TI_DSACK_DUPS)
    if sent > INT63 or retr > INT63 or dsack == 0xFFFFFFFF or mss == 0xFFFFFFFF:
        return _INVALID
    return (rtt, mrtt, mss, sent, retr, dsack)


def parse_msg(buf, start, end):
    """One inet_diag_msg in buf[start:end] -> (local port, cookie, inode, info, cgroup id) or None (short, not
    ESTABLISHED). ``info``: the tuple of ``tcp_fields``, ``"old"``, ``"invalid"`` or None (no INET_DIAG_INFO, or its
    attribute is cut); ``cgroup id``: INET_DIAG_CGROUP_ID (5.9+) or None. The remote end of the socket (its address
    and port) is not read."""
    if end - start < MSG_LEN or buf[start + 1] != TCP_ESTABLISHED:
        return None
    (sport,) = struct.unpack_from(">H", buf, start + 4)
    lo, hi = struct.unpack_from("=II", buf, start + 44)
    (inode,) = struct.unpack_from("=I", buf, start + 68)
    info = cg = None
    a = start + MSG_LEN
    while a + 4 <= end:
        alen, atyp = _RTA.unpack_from(buf, a)
        if alen < 4 or a + alen > end:
            break                                    # a cut attribute: the rest of the message is not trusted
        t = atyp & NLA_TYPE_MASK
        if t == INET_DIAG_INFO:
            info = tcp_fields(buf, a + 4, a + alen)
        elif t == INET_DIAG_CGROUP_ID and alen >= 12:
            (cg,) = struct.unpack_from("=Q", buf, a + 4)
        a += _align(alen)
    return (sport, lo | (hi << 32), inode, info, cg)


def parse_datagram(buf, seq, sink, room):
    """The messages of one datagram into ``sink`` (only ours: ``seq``). -> "more" (the dump goes on), "done", "bad"
    (a length that does not fit), "full" (``room`` sockets taken) or a positive errno (NLMSG_ERROR)."""
    off, n = 0, len(buf)
    while off < n:
        if n - off < NLH_LEN:
            return _BAD
        ln, typ, _flags, sq, _pid = _NLH.unpack_from(buf, off)
        if ln < NLH_LEN or ln > n - off:
            return _BAD
        if sq == seq:
            if typ == NLMSG_DONE:
                return _DONE
            if typ == NLMSG_ERROR:
                if ln < NLH_LEN + 4:
                    return _BAD
                (err,) = struct.unpack_from("=i", buf, off + NLH_LEN)
                if err < 0:
                    return -err
            elif typ == SOCK_DIAG_BY_FAMILY:
                if len(sink) >= room:
                    return _FULL
                got = parse_msg(buf, off + NLH_LEN, off + ln)
                if got is not None:
                    sink.append(got)
        off += _align(ln)
    return _MORE


class _Stop(Exception):
    """The dump of a family ends early (a cut datagram, the limits)."""


def netlink_dump(req, deadline, budget):
    """The datagrams of one dump (a generator). ``budget`` — [bytes left], shared by the families of a sample.
    OSError (no AF_NETLINK, no sock_diag) goes to the caller; a cut datagram, the bytes or the time ending — _Stop."""
    af = getattr(socket, "AF_NETLINK", None)
    if af is None:
        raise OSError(errno.EAFNOSUPPORT, "no netlink")
    s = socket.socket(af, socket.SOCK_RAW, NETLINK_SOCK_DIAG)
    try:
        s.bind((0, 0))
        s.settimeout(max(0.05, deadline - time.monotonic()))
        s.sendto(req, (0, 0))
        trunc = getattr(socket, "MSG_TRUNC", 0x20)
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or budget[0] <= 0:
                raise _Stop()
            s.settimeout(left)
            try:
                data, _anc, flags, _addr = s.recvmsg(RECV_BUF)
            except socket.timeout:
                raise _Stop()
            if flags & trunc:
                raise _Stop()
            budget[0] -= len(data)
            if not data:
                raise _Stop()
            yield data
    finally:
        s.close()


# ── the unit of a socket ──────────────────────────────────────────────────

def _cg_version():
    from . import hostprobe
    return hostprobe._cgroup_facts()[0]


def unit_cgroup(unit):
    """The inode of the cgroup v2 directory of the unit — on 64-bit it IS the cgroup id the kernel gives a socket in
    INET_DIAG_CGROUP_ID (an accepted socket keeps the cgroup of its listener). -> int; ``"no_cgroup"`` — no such
    directory (the unit does not run, or the system keeps its units elsewhere: the path is not guessed); None — it
    cannot be read."""
    try:
        return os.stat(paths.P(CG_DIR % unit)).st_ino
    except FileNotFoundError:
        return NO_CGROUP
    except OSError:
        return None


_LOW32 = struct.calcsize("P") < 8                    # a 32-bit ino_t: st_ino is the low half of the cgroup id


def _same_cgroup(cg, ino):
    return cg == ino or (_LOW32 and (cg & 0xFFFFFFFF) == ino)


def unit_pids(unit):
    """[pid] of the unit (cgroup v2; at most MAX_PIDS + 1 — one more tells the list was cut); [] — the unit does not
    run (no cgroup); None — cannot be read. Only the fallback without INET_DIAG_CGROUP_ID reads it."""
    path = paths.P(CG_DIR % unit + "/cgroup.procs")
    try:
        with open(path, "r", encoding="ascii", errors="replace") as f:
            text = f.read(16 * (MAX_PIDS + 1))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    out = []
    for x in text.split():
        if x.isdigit() and len(out) <= MAX_PIDS:
            out.append(int(x))
    return out


def sock_inodes(pid, into, deadline=None):
    """Add the socket inodes of a process to ``into``. -> False when its descriptors cannot be read (no rights);
    ``"cut"`` — more than MAX_FDS, or another error in the middle of the walk (this process only); ``"late"`` — the
    deadline of the sample came; True otherwise (a process gone, even in the middle of the walk, is no error)."""
    base = paths.P("/proc/%d/fd" % pid)
    try:
        it = os.scandir(base)
    except (FileNotFoundError, ProcessLookupError):
        return True
    except OSError:
        return False
    n = 0
    try:
        with it:
            for e in it:
                n += 1
                if n > MAX_FDS:
                    return _CUT
                if deadline is not None and not n & 255 and time.monotonic() > deadline:
                    return _LATE
                try:
                    t = os.readlink(e.path)
                except OSError:
                    continue
                if t.startswith("socket:[") and t.endswith("]") and t[8:-1].isdigit():
                    into.add(int(t[8:-1]))
    except (FileNotFoundError, ProcessLookupError):  # the process left while it was read (fs/proc/fd.c: -ENOENT)
        return True
    except OSError:
        return _CUT
    return True


def unit_readable(unit):
    """The descriptors of the unit can be read (the first process): a minute without a new socket of an input still
    tells «no rights» from «nobody connected» (only without INET_DIAG_CGROUP_ID). A unit without processes —
    readable (nothing to read)."""
    pids = unit_pids(unit)
    if pids is None:
        return False
    if not pids:
        return True
    try:
        with os.scandir(paths.P("/proc/%d/fd" % pids[0])):
            return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


# ── the hour ──────────────────────────────────────────────────────────────

def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * 0.9))] if xs else None


def _ms(us):
    return None if us is None else round(us / 1000.0, 1)


def _rtt_ok(v):
    return isinstance(v, int) and 0 < v < 0xFFFFFFFF


def _reason(reasons):
    """The why of an hour with no TCP input read: the most frequent reason, a tie by the order of REASONS."""
    order = dict((r, i) for i, r in enumerate(REASONS))
    return sorted(set(reasons), key=lambda r: (-reasons.count(r), order.get(r, len(order)), r))[0]


class People(object):
    """The state of the process (see the module docstring). ``dump(req, deadline, budget)`` — the datagrams of one
    netlink dump (tests give their own); ``cgv``, ``cgroup`` — the readers of cgroup; ``pids``, ``inodes``,
    ``readable`` — of cgroup.procs and /proc (only without INET_DIAG_CGROUP_ID)."""

    def __init__(self, dump=None, cgv=None, pids=None, inodes=None, readable=None, cgroup=None):
        self.dump = dump or netlink_dump
        self.cgv = cgv or _cg_version
        self.cgroup = cgroup or unit_cgroup
        self.pids = pids or unit_pids
        self.inodes = inodes or sock_inodes
        self.readable = readable or unit_readable
        self.bc = True                 # the kernel takes the filter of ports (False after one EINVAL)
        self.cg_attr = None            # the kernel gives INET_DIAG_CGROUP_ID (None: no socket of ours seen yet)
        self.rows = deque(maxlen=KEEP_ROWS)
        self.dropped = 0               # rows pushed out past KEEP_ROWS (the collector logs and zeroes it)
        self.seq = 0
        self.at = None                 # mono of the last sample
        self.over = 0                  # samples cut by a limit (diagnostics)
        self.bad = 0                   # sockets with counters that cannot be (diagnostics)
        self.whole = True              # the last sample saw every socket of the inputs
        self.deadline = 0.0            # time.monotonic() at which the current sample stops
        self.last_ms = None            # CPU ms of the last sample (diagnostics)
        self.given = deque(maxlen=KEEP_ROWS)   # ts of the hours lately given as rows: never opened again (REV-ns-b17-2
                                       # П-3 — the measure off and the clock back would replace a full row by a part)
        self.clear()

    def clear(self):
        """Forget the hour (the measure off, the plan gone)."""
        self.conns = {}                # cookie -> [key, b_sent, b_retr, b_dsack, sent, retr, dsack, mss, rtt, mrtt, n]
        self.hour = None               # start of the hour (UTC s)
        self.last_now = None           # the wall clock of the last sample (the clock back, REV-ns-b17-2 П-1)
        self.prev_ok = set()           # inputs read in the previous sample
        self.prev_mono = None
        self.n = 0                     # the number of the sample in the hour
        self.read = {}                 # key -> samples read in the hour
        self.miss = {}                 # key -> the reason of its last sample not read
        self.old = {}                  # key -> sockets without the fields (old kernel)
        self.good = {}                 # key -> sockets with the fields
        self.fixed = {}                # key -> «udp» | «shared_port» | «no_data» of the plan in the hour
        self.tcp_keys = set()          # the inputs to measure in the hour
        self.partial = 0               # samples of the hour that read but did not see everything
        self.why = None                # the reason of the last sample that read nothing

    # -- the minute --
    def step(self, plan_, mono, now):
        """One sample when a minute has passed since the last one. -> True when sampled."""
        if plan_ is None:
            if self.hour is not None:
                self.clear()
            self.at = None
            return False
        if self.at is not None and 0 <= mono - self.at < MINUTE_S:
            return False
        self.at = mono
        t0 = time.process_time()
        try:
            self.sample(plan_, mono, now)
        finally:
            self.last_ms = (time.process_time() - t0) * 1000.0
        return True

    def sample(self, plan_, mono, now):
        cont = self.prev_mono is not None and 0 <= mono - self.prev_mono <= GAP_S
        hr = int(now // HOUR_S) * HOUR_S             # UTC: the key of the row
        if self.hour is not None and self.last_now is not None and now < self.last_now - HOUR_S:
            self.clear()                             # the clock went back more than an hour: that hour is not trusted
            cont = False
        if (self.hour is None or hr > self.hour) and hr in self.given:
            if self.hour is not None:
                self._finish(False)                  # an hour already given as a row: no second row of it (П-3)
            self.last_now = now
            return
        if self.hour is None or hr > self.hour:
            if self.hour is not None:
                self._finish(cont)
            self.hour = hr
        # the clock went back within an hour (NTP, by hand): the sample stays in the open hour — never two rows of one
        # hour; further back — the open hour is forgotten without a row
        self.n += 1
        self.last_now = now
        self.deadline = time.monotonic() + DEADLINE_S
        tcp = dict((k, v) for k, v in plan_.items() if isinstance(v, tuple))
        for k, v in plan_.items():
            if not isinstance(v, tuple):
                self.fixed[k] = v
        self.tcp_keys.update(tcp)
        self.prev_mono = mono
        self.whole = True
        ok = set()
        try:
            if not tcp:
                return
            if self.cgv() != 2:
                self._miss_all(tcp, "old_cgroup")
                return
            socks = self._read(sorted(set(p for _, p in tcp.values())))
            if socks is None:
                self._miss_all(tcp, "no_diag")
                return
            ok = self._attribute(tcp, socks, cont)
        finally:
            # a sample that did not see every socket (cut by a limit, one family unanswered, the deadline) is no base
            # for the next: a socket it missed would look new there and bring its whole past into the hour
            self.prev_ok = ok if self.whole else set()
            if ok and not self.whole:
                self.partial += 1
            for k in ok:
                self.read[k] = self.read.get(k, 0) + 1

    def _miss_all(self, tcp, why):
        self.why = why
        for k in tcp:
            self.miss[k] = why

    def _read(self, ports):
        """[(port, cookie, inode, info, cgroup id)] of both families; None — nothing could be read. A family the
        kernel does not have (EAFNOSUPPORT, ENOENT… before any socket) is left out; any other error, a silent family
        or a cut dump make the sample not whole (what came before the error is kept)."""
        budget = [MAX_DUMP_BYTES]
        socks = []
        fams_ok = 0
        cut = partial = False
        want = set(ports)
        for fam in (AF_INET, AF_INET6):
            if cut:
                break
            for attempt in (0, 1):
                self.seq = (self.seq + 1) & 0x7FFFFFFF or 1
                req = request(fam, self.seq, ports[:MAX_PORTS] if self.bc and len(ports) <= MAX_PORTS else None)
                got = []
                status = None
                it = None
                grams = 0
                try:
                    it = self.dump(req, self.deadline, budget)
                    for data in it:
                        grams += 1
                        status = parse_datagram(data, self.seq, got, MAX_SOCKS - len(socks))
                        if status != _MORE:
                            break
                except _Stop:
                    status = _BAD if grams else errno.ETIMEDOUT     # not one datagram: the kernel did not answer
                except OSError as e:
                    status = e.errno if isinstance(e.errno, int) and e.errno > 0 else errno.EIO
                finally:
                    close = getattr(it, "close", None)
                    if close is not None:
                        close()                      # the socket of netlink_dump is closed now, not by the GC
                if status is None:
                    status = errno.ETIMEDOUT                # a dump that ended without a word: no answer
                if status == errno.EINVAL and self.bc and attempt == 0 and not got:
                    self.bc = False                  # an old kernel refuses the filter: once without it
                    continue
                if isinstance(status, int):          # an error (NLMSG_ERROR, OSError, no answer)
                    if not (status in UNSUPPORTED and grams <= 1 and not got):
                        partial = True
                        if got:
                            fams_ok += 1
                            socks.extend(s for s in got if s[0] in want)
                else:
                    fams_ok += 1
                    socks.extend(s for s in got if s[0] in want)
                    if status != _DONE:
                        cut = True
                        self.over += 1
                break
        if not fams_ok:
            return None
        if cut or partial:
            self.whole = False
        return socks

    def _attribute(self, tcp, socks, cont):
        """Give each socket to its input (the cgroup of the unit — or, without INET_DIAG_CGROUP_ID, a process of the
        unit — and the port) and follow its counters. -> the inputs read."""
        by_port = {}
        for k, (unit, port) in tcp.items():
            by_port.setdefault(port, []).append((k, unit))
        cgs = dict((u, self.cgroup(u)) for u in set(u for u, _ in tcp.values()))
        ok = set()
        for k, (unit, _port) in tcp.items():
            v = cgs[unit]
            if isinstance(v, int) and not isinstance(v, bool):
                ok.add(k)
            else:
                self.miss[k] = NO_CGROUP if v == NO_CGROUP else "no_access"
        fds = {}                                     # unit -> socket inodes | None (no rights): the fallback, on demand
        conns = self.conns
        n = self.n
        dump_whole = self.whole                      # the dump saw every socket (before MAX_TRACK and the fallback)
        new = []                                     # (cookie, key, counters): taken after the gone ones are cleaned
        for port, cookie, inode, info, cg in socks:
            cands = by_port.get(port)
            if not cands:
                continue
            self.cg_attr = cg is not None
            c = conns.get(cookie)
            if c is not None and c[0] not in tcp:    # its input left the plan: not followed further
                del conns[cookie]
                c = None
            if c is not None:
                key = c[0]
                c[10] = n                            # seen (even when this answer has no usable counters)
            else:
                key = None
                for k, unit in cands:
                    if k not in ok:
                        continue
                    if cg is not None:
                        if _same_cgroup(cg, cgs[unit]):
                            key = k
                            break
                        continue
                    if not inode:
                        continue
                    if unit not in fds:
                        fds[unit] = self._unit_inodes(unit)
                    if fds[unit] is None:
                        ok.discard(k)
                        self.miss[k] = "no_access"
                        continue
                    if inode in fds[unit]:
                        key = k
                        break
                if key is None:
                    continue
            if info is None or info in (_OLD, _INVALID):
                if info == _OLD:
                    self.old[key] = self.old.get(key, 0) + 1
                elif info == _INVALID:
                    self.bad += 1
                continue
            self.good[key] = self.good.get(key, 0) + 1
            rtt, mrtt, mss, sent, retr, dsack = info
            if c is None:
                new.append((cookie, key, info))
            else:
                if sent < c[4] or retr < c[5] or dsack < c[6]:  # a counter went back (cannot): start it again here
                    c[1], c[2], c[3] = sent, retr, dsack
                c[4], c[5], c[6], c[7], c[8], c[9] = sent, retr, dsack, mss, rtt, mrtt
        if self.cg_attr is not True:                 # the fallback: a unit not looked at, its rights still matter
            for k, (unit, _port) in tcp.items():
                if k in ok and unit not in fds:
                    fds[unit] = set() if self.readable(unit) else None
                if k in ok and fds[unit] is None:
                    ok.discard(k)
                    self.miss[k] = "no_access"
        # the dump saw every socket: the gone ones that sent < MIN_BYTES this hour never count — forgotten even when
        # MAX_TRACK cannot take the new ones (REV-ns-b17-2 З-1: else the full table locks the cleaning till the end
        # of the hour), and before the new ones are taken, so they get the room
        if dump_whole:
            gone = [ck for ck, c in conns.items() if c[10] != n and c[4] - c[1] < MIN_BYTES]
            for ck in gone:
                del conns[ck]
        for cookie, key, info in new:
            if len(conns) >= MAX_TRACK:
                self.whole = False                   # one not followed: the sample did not see everything
                break
            rtt, mrtt, mss, sent, retr, dsack = info
            base = (0, 0, 0) if (cont and key in self.prev_ok) else (sent, retr, dsack)
            conns[cookie] = [key, base[0], base[1], base[2], sent, retr, dsack, mss, rtt, mrtt, n]
        if not ok:
            self.why = _reason([self.miss.get(k, "no_access") for k in tcp])
        return ok

    def _unit_inodes(self, unit):
        """The socket inodes of the processes of a unit (the fallback without INET_DIAG_CGROUP_ID), within the deadline
        of the sample; None — no rights. A list cut at MAX_PIDS / MAX_FDS, an error of one process or the deadline —
        what was read is used and the sample is not whole."""
        pids = self.pids(unit)
        if pids is None:
            return None
        if len(pids) > MAX_PIDS:
            self.whole = False
            pids = pids[:MAX_PIDS]
        got = set()
        for pid in pids:
            if time.monotonic() > self.deadline:
                self.whole = False
                break
            try:
                r = self.inodes(pid, got, self.deadline)
            except OSError:
                self.whole = False
                continue
            if r is False:
                return None
            if r in (_CUT, _LATE):
                self.whole = False
                if r == _LATE:
                    break
        return got

    # -- the end of an hour --
    def _finish(self, carry=True):
        """Close the hour into a row. ``carry`` — the new sample follows within GAP_S: the connections of the last
        sample start the next hour from their last values; after a longer gap nothing is carried (the bytes of the gap
        are of no known hour)."""
        row = self.row()
        if row is not None:
            self._push(row)
            if row["ts"] not in self.given:
                self.given.append(row["ts"])
        last = self.n
        keep = {}
        if carry:
            for cookie, c in self.conns.items():
                if c[10] == last:                    # seen in the last sample: the next hour starts from here
                    c[1], c[2], c[3] = c[4], c[5], c[6]
                    c[10] = 0
                    keep[cookie] = c
        prev_ok = self.prev_ok if carry else set()
        prev_mono, last_now = self.prev_mono, self.last_now
        self.clear()
        self.conns, self.prev_ok, self.prev_mono, self.last_now = keep, prev_ok, prev_mono, last_now

    def row(self):
        """The row of the hour so far (``_finish`` closes the hour with it)."""
        if self.hour is None:
            return None
        rtts, mrtts = [], []
        sent = retr = dsack = n = 0
        for c in self.conns.values():
            ds = c[4] - c[1]
            if ds < MIN_BYTES:
                continue
            dr = max(0, c[5] - c[2])
            dd = max(0, c[6] - c[3]) * c[7]
            if sent + ds > INT63 or retr + dr > INT63 or dsack + dd > INT63:
                self.bad += 1                        # cannot be: never a number the base refuses
                continue
            n += 1
            sent, retr, dsack = sent + ds, retr + dr, dsack + dd
            if _rtt_ok(c[8]):
                rtts.append(c[8])
            if _rtt_ok(c[9]):
                mrtts.append(c[9])
        inputs = dict(self.fixed)
        reasons = []
        measured = []
        old = any(self.old.values()) and not any(self.good.values())   # the kernel: for every input, not one
        for k in sorted(self.tcp_keys):
            if not self.read.get(k):
                inputs[k] = NO_DATA
                reasons.append(self.miss.get(k) or "no_diag")
            elif old:
                inputs[k] = NO_DATA
                reasons.append("old_kernel")
            else:
                measured.append(k)
        numbers = bool(measured) and n >= MIN_CONNS and sent >= MIN_SENT
        for k in measured:                           # «few» only when the row has no numbers (З-3)
            inputs[k] = OK if numbers else FEW
        out = {"ts": self.hour, "conns": None, "rtt_med": None, "rtt_p90": None, "rtt_min_med": None,
               "sent": None, "retr": None, "dsack": None, "why": None, "inputs": inputs, "partial": self.partial}
        if not self.tcp_keys:
            out["why"] = SHARED_443 if SHARED in self.fixed.values() else "no_tcp_inbound"
        elif not measured:
            out["why"] = _reason(reasons)
        elif not numbers:
            out["why"] = FEW
        else:
            out.update(conns=n, rtt_med=_ms(_median(rtts)), rtt_p90=_ms(_p90(rtts)), rtt_min_med=_ms(_median(mrtts)),
                       sent=sent, retr=retr, dsack=dsack)
        return out

    # -- the rows --
    def _push(self, row):
        if len(self.rows) == self.rows.maxlen:
            self.dropped += 1                        # the oldest goes: no writer for KEEP_ROWS hours
        self.rows.append(row)

    def take(self):
        """The rows of the finished hours, oldest first; the queue is emptied."""
        out = list(self.rows)
        self.rows.clear()
        return out

    def keep(self, rows):
        """Put back rows nobody could write (no writer yet): they go first next time (the oldest dropped past
        ``KEEP_ROWS`` and counted in ``dropped``)."""
        rest = list(self.rows)
        self.rows.clear()
        for r in list(rows) + rest:
            self._push(r)


# ── the day ───────────────────────────────────────────────────────────────

def _wmedian(pairs):
    """Median of (value, weight)."""
    pairs = sorted((v, w) for v, w in pairs if v is not None and w)
    tot = sum(w for _, w in pairs)
    if not tot:
        return None
    acc = 0
    for v, w in pairs:
        acc += w
        if acc * 2 >= tot:
            return v
    return pairs[-1][0]


_RANK = {OK: 0, FEW: 1, NO_DATA: 2, SHARED: 3, UDP: 4}


def _row_dict(r):
    if isinstance(r, dict):
        return r
    try:
        return dict(zip(r.keys(), tuple(r)))         # sqlite3.Row
    except Exception:                                # noqa: BLE001 — not a row
        return None


def _inputs(v):
    """``inputs`` of a row: a dict, or its JSON text as the base keeps it; anything else — {}."""
    if isinstance(v, (bytes, bytearray)):
        v = bytes(v).decode("utf-8", "replace")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def summary(rows):
    """The line of a day under the star (NS-D26 «Показ»): {"rtt_ms", "best_ms", "retr_pct", "conns", "hours", "why",
    "inputs", "partial"} — ``partial``: hours with a sample that did not see every connection (the mark «часть
    соединений не учтена», REV-ns-b17-2 З-2); the delay is the median of the hourly ``rtt_med`` weighted by ``conns``, «best» the same of
    ``rtt_min_med``, the resent share (Σ retr − Σ dsack) / Σ sent (not below 0). No row — None; no hour with numbers —
    the numbers None and ``why`` (``few`` when an hour had too few, else the reason of the last row). A row is a dict
    (``row()``) or a row of the base (``sqlite3.Row`` or a dict of it): ``inputs`` — a dict or its JSON text.
    ``conns`` of the day is the SUM of the hours: a connection that lived 5 hours is counted 5 times."""
    rows = [_row_dict(r) for r in rows or []]
    rows = [r for r in rows if r is not None]
    if not rows:
        return None
    inputs = {}
    for r in rows:
        for k, v in _inputs(r.get("inputs")).items():
            if v in _RANK and (k not in inputs or _RANK[v] < _RANK[inputs[k]]):
                inputs[k] = v
    good = [r for r in rows if r.get("why") is None and r.get("conns")]
    partial = 0
    for r in rows:
        try:
            partial += 1 if int(r.get("partial") or 0) > 0 else 0
        except (TypeError, ValueError):
            pass
    out = {"rtt_ms": None, "best_ms": None, "retr_pct": None, "conns": None, "hours": len(good), "why": None,
           "inputs": inputs, "partial": partial}
    if not good:
        whys = [r.get("why") for r in rows]
        out["why"] = FEW if FEW in whys else whys[-1]
        return out
    sent = sum(r.get("sent") or 0 for r in good)
    bad = sum(r.get("retr") or 0 for r in good) - sum(r.get("dsack") or 0 for r in good)
    out.update(rtt_ms=_wmedian((r.get("rtt_med"), r["conns"]) for r in good),
               best_ms=_wmedian((r.get("rtt_min_med"), r["conns"]) for r in good),
               retr_pct=round(max(0.0, 100.0 * bad / sent), 2) if sent else None,
               conns=sum(r["conns"] for r in good))
    return out
