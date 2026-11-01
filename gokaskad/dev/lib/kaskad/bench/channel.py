"""The channel of the server check: tests 8, 8b and 8c (SPEC-node-score §6.2 B3; RESEARCH-node-score §2, rows 8, 8b,
8c, §1.5, §6.1, appendix A.1 ``channel_confirm``, A.3 ``iperf3_8b``; SPEC-node-score NS-D27;
SPEC-node-score-schedule-share NS-S5, NS-S8, NS-S16). A portable block (k3/handoff/process/BLOCKS.md): the standard
library only (and the pure readers of ``bench.quiet`` — one rule of the interfaces for the block), nothing of
``gw``/``panel``/``site``; what it needs of the product comes in ``Env``; texts are codes of the
catalog (``bench_run.why.<code>``, ``bench_run.log.channel``), never phrases.

**Test 8 — by the clock, not by the bytes.** Each direction: 4 streams of 10 s; the first 2 s (TCP warm-up) are
dropped; the speed = the bytes of the next 8 s ÷ their time, counted every 0,25 s by this reader (no ``curl -w``). A
stream asks again on its connection when its file ends, until 10 s or its share of the cap. The cap of a direction:
quick 250 MB, full 2 GB (decimal). The cap hit before 5 s — «not less than X» = all the bytes ÷ all the time, warm-up
included (``extra.budget``). The download — by the country of the node (``channel-targets.json``): in Russia the
files of Selectel / the Yandex mirror, abroad Cloudflare ``__down`` (then OVH); the upload — POST to Cloudflare
``__up`` abroad; a node in Russia uploads ONLY to the point its owner allowed (``Env.up_url``, NS-S5), else «не
измерено: нет точки отдачи в России» — or test 8b by the button. A target that does not answer — the next one; none —
«не измерено: точки недоступны» — after the first one once more, ``FAIL_WAIT`` later (B9: the first connection to
Cloudflare from a Russian network hangs now and then; the clock of a try starts at its first connection, ``conn_s``
for it, ``ADDR_S`` per address with the families in turn). **Confirm** (A.1 ``channel_confirm``): a value below the anchor 500 (download 300,
upload 200 Mbit/s) is tried once more at another target (the same one after 20 s when there is no other — RS §2 «до
+20 с», the coordinator 19.10, REV-ns-b3 З-7), inside the same cap of the direction; the value of the test = the best
of the tries.

**The country** (``Env.ru``): True / False — known; None — unknown. Unknown is «Russia» — for the schedule, the repeat
and the button alike (no upload but to the allowed point, the Russian download targets — NS-S5, «a failure is a
block»; the coordinator 19.10, REV-ns-b3 З-3 = SEC З-5; П3-а after B9: one rule, the series of the button and of the
schedule compare), with a line «страна не определена — меряю как для России» in the journal and ``extra.geo:
"unknown_ru"`` on both rows of test 8, so the person sees it (REV-ns-b3-2 З-1; «unknown» — the rows before П3-б,
REV-ns-p3a П-2).

**Test 8b — iperf3, by the button only** (``Env.iperf``: a manual check; since 29.10 — владелец, D-144 — a FULL manual
check runs it by default and the person may turn it off — the tick of the panel, ``--no-upload-ru`` of the CLI and
MCP, the question of the SSH menu; the line «the chosen point, and when it does not answer the next one of the list,
sees the address of the server» is said beside that choice; turned off — the upload is «не измерено» with ``why:
upload_off``, and so are the rows of 8c; the schedule and the repeat never — the runner refuses them). A direction without an HTTP target (a node in Russia without an allowed
point: the upload) goes to the iperf3 points of the Russian providers ONE BY ONE in the order of the list until the
first live one (never a connection to all of them — the line of consent, REV-ns-b3 С-1): a TCP connection checks the
point and gives its GLOBAL address; iperf3 gets that address, never the name (SEC З-3):
``iperf3 -c <address> -p <port> -P 4 -t 10 -O 2 -i 0.25 --forceflush [-R]``, as ``nobody`` (``setpriv``, SEC З-6).
**The cap of 8b never rests on the text alone** (SEC З-1, З-2): every line is read at once; iperf3 is stopped when what
passed plus what passes until the stop (the rate of the last interval) reaches the cap; the byte counters of the
interfaces stop it too, whatever the text says — the interfaces of the default routes as ``bench.quiet`` counts the
traffic of people (``quiet.ifaces``: no tunnels, no ``lo``, else a byte of a person through the node counts twice),
minus the background of the last ``bg_s`` before the start, one cap at most (REV-ns-b3-2 С-1: people never make 8b
«не измерено»). People who pass more than the cap during a try CAN stop 8b early by the counters: then, when the
counters are above what iperf3 itself moved by ``PEOPLE_SHARE`` of the cap at least, the value is the one of the text —
no ``budget``, no «не меньше»: the cap was reached by people, not by iperf3 (the coordinator 19.10, REV-ns-b3-3 З-1).
What iperf3 moved is the projection of its text to the moment of the stop (the bytes of the sums + the rate of the last
interval since its line), one interval of the lines more (``LINE_LAG_S``: a line comes after its interval), times
``HDR`` (the counters see the headers of L2/IP/TCP too, 3–5 %): a quiet node at 1 Gbit/s, where the counters reach the
quick cap a quarter of a second before the text, keeps its ``budget`` (REV-ns-join С-1). No line of the sums in
``first_s``: lines of the intervals of the streams came — an output we do not know, «не измерено» (``tool_failed``);
none (the lines «connected» of the streams are not intervals — REV-ns-b3-3 П-1) — the point took the connection and
is silent (``no_target``); both stop the direction, no next point. ``tool_failed`` only when no line of the sums came
at all.
«Server is busy» — the next point; 3 runs of a direction at most, the confirm at the same point after 20 s included
(the line of consent names no second point).

**Test 8c — «под нагрузкой»** (NS-D27), inside test 8, not in the score until the calibration B9: ``TCP_INFO`` of
every upload stream at the 2nd and the last second of the window (HTTP — of our own sockets; 8b — of the sockets of
iperf3, by ``Env.tcp(pid, port)`` of the product at its first measured line and at 1 s before the end of its window or
at its stop; its control connection — under ``STREAM_MIN`` bytes sent between the samples — is left out): ``load_retr`` = 100 · (Δ bytes_retrans − Δ dsack_dups
× snd_mss) ÷ Δ bytes_sent over the streams, %; ``load_rtt`` = the median ``tcpi_rtt`` at the end − the least
``tcpi_min_rtt``, ms. The congestion control is the system's (the sockets never set it); the actual one
(``TCP_CONGESTION``) goes to ``ca``. «Не измерено» by its true reason (REV-ns-b3 З-2): a kernel without the fields
(< 4.19) — ``no_tcp_info``; the streams opened new connections between the two samples — ``reconnect``.
**The upload hit the cap before 5 s** (Н-1 of the stand 3.3.7.13, the coordinator 29.10: a full check moves 2000 MB in
under 5 s above ≈ 3.2 Gbit/s, and 8c was never measured there): 8c over the window it had — from the first sample after
the first interval (a line of the sums of iperf3, a tick of HTTP; ≥ 0.25 s) at which the upload at its rate reaches
the cap before ``min_s`` but not sooner than ``LOAD_MIN_S`` (``early``), or the usual sample of the 2nd second when it
came first, to the sample at the stop by the cap; its seconds go to ``extra.win`` of both rows (the hint «по короткому окну — число менее надёжно»); a window under
``LOAD_MIN_S`` (``Timing.load_min_s``) — «не измерено», ``cap_early``, as before. The usual window (the cap after 5 s
or never) is as it was: below the rate of that projection no sample is added.

**People during the test** (NS-S16): ``Env.probe()`` takes a sample of the counters at the 2nd and the last second of
the window, ``Env.people(a, b, side)`` gives the traffic of others of the same side, Mbit/s (None — unknown). With it
``value_corr`` = ``kaskad.score.load_correct(value, people, L, port)``, ``L`` = ``Env.level(test, mode, ts)``; the score
takes ``value_corr`` only for a check under load.

**The extension point of the schedule and the repeat** (B11): ``steps(mode, skip, iperf)``; ``skip`` ⊆ ``SKIP``:
``8b`` (no iperf3), ``9`` (the loop of stage C; nothing yet), ``up`` (no upload and no 8c), ``down`` (no download — a
repeat of the upload only, SS §2.8 п. 2) — a skipped test is «не измерено» with ``why: excluded`` (and no
«страна не определена» beside it, REV-ns-b3-3 П-3).

The User-Agent is the one of the HTTP library of Python (``Python-urllib/3.x``), never a word of the product (RS §6.1).
Every address is checked after the connection: only a global one (no loopback, private, link-local, multicast, no
IPv4 of such a kind inside an IPv6 address); a target of the list is a host NAME, never an address literal.

Rows (``ctx.row``): ``net_down``, ``net_up`` (Mbit/s; ``extra.budget``, ``extra.geo``; ``people``, ``value_corr``; ``ca`` of the
upload), ``load_retr`` (%), ``load_rtt`` (ms) with ``ca`` (and ``extra.win`` — s of a short window of 8c). Reasons: no_target, no_upload_ru, excluded, no_iperf3,
points_busy, no_tcp_info, cap_early, reconnect, no_upload, upload_off, tool_failed, time.
"""

import http.client
import ipaddress
import json
import os
import queue
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time

SCHEMA = 1
TESTS = ("net_down", "net_up", "load_retr", "load_rtt")
UNITS = {"net_down": "Mbit/s", "net_up": "Mbit/s", "load_retr": "%", "load_rtt": "ms"}
SIDE_CAP = {"quick": 250 * 10 ** 6, "full": 2000 * 10 ** 6}    # bytes of a direction (RS §2; cost.SIDE_CAP_MB)
ANCHOR = {"net_down": 300.0, "net_up": 200.0}                   # Mbit/s: below — one more try (A.1 channel_confirm)
SKIP = ("8b", "9", "up", "down")
STREAMS = 4
RETRY_WAIT = 20.0                   # s: the same target again when there is no other (RS §2 «до +20 с», REV З-7)
FAIL_WAIT = 5.0                     # s: every target of a direction failed (no_target) — the first one once more (B9)
ADDR_S = 2.0                        # s: a TCP connection to ONE address of a target at most, then the next (B9)
RETRY_MIN_SHARE = 0.1               # the rest of the cap a try needs at least
DOWN_REQ = 99 * 10 ** 6             # a sized request at most (Cloudflare refuses 10^8 bytes and more)
UP_REQ = 25 * 10 ** 6               # the body of one POST at most
RESP_MAX = 64 * 1024                # the answer to a POST read at most; a longer one closes the connection (SEC П-1)
REQ_MAX = 64                        # requests of one stream at most: a guard against a tiny file asked in a loop
CHUNK = 64 * 1024
TRY_NEED_S = 16                     # a try is not started with less left (connection 3 s + 10 s + the reader's grace)
TAIL_S = 30                         # left after the channel for the series B of the processor (bench.suite.TAIL, 25 s)
NEED_S = TRY_NEED_S + TAIL_S        # the step is not started with less left: one try and the series B (REV З-1)
IPERF_RUNS = 3                      # runs of iperf3 of a direction at most (busy points and the confirm included)
STREAM_MIN = 64 * 1024              # 8c over 8b: a socket of iperf3 that sent less between the samples — its control one
PEOPLE_SHARE = 0.1                  # 8b stopped by the counters, people passed this share of the cap beyond iperf3: theirs
HDR = 1.05                          # the counters of the interfaces against the bytes of the text: L2/IP/TCP headers, 3–5 %
LINE_LAG_S = 0.25                   # a line of the sums comes after its interval (-i 0.25): iperf3 moved that much more
LOAD_MIN_S = 1.0                    # s: the least window of 8c when the upload hit the cap before min_s (Н-1); below it
#                                     — cap_early. 4 intervals of 0.25 s: tcpi_rtt is a running mean over every ACK
#                                     (tens of thousands a second at ≥ 400 Mbit/s) and min_rtt is of the whole
#                                     connection, so the rise is honest; the retransmits are counted over ≥ 50 MB, but
#                                     over few cycles of the congestion control — less reliable, said in the hint
RTT_FACTOR, RTT_GAP_MS = 2.0, 10.0  # «nearer» by the delays: twice and by 10 ms at least (SEC З-5); else unknown
RTT_TRIES = 3                       # TCP connections to each anchor when the series has no delays; 2 answers at least
NOBODY = 65534                      # iperf3 runs as nobody/nogroup (SEC З-6)
_IPERF_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}
TARGETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel-targets.json")
UA = "Python-urllib/%d.%d" % sys.version_info[:2]
_URL = re.compile(r"https://([a-z0-9.-]{1,253})(?::([0-9]{1,5}))?(/[A-Za-z0-9._~/?=&%{}+-]*)")
_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}")
_ID = re.compile(r"[a-z0-9_-]{1,32}")
_CA = re.compile(r"[a-z0-9_]{1,16}")

# struct tcp_info (linux/uapi/linux/tcp.h; tests check them against the member list of the header, a fixture —
# the same list as gw/hostpeople.py): (offset, struct format)
TI = {"snd_mss": (16, "I"), "rtt": (68, "I"), "bytes_acked": (120, "Q"), "min_rtt": (148, "I"),
      "bytes_sent": (200, "Q"), "bytes_retrans": (208, "Q"), "dsack_dups": (216, "I")}
TI_LEN = 256
TCP_INFO = getattr(socket, "TCP_INFO", 11)
TCP_CONGESTION = getattr(socket, "TCP_CONGESTION", 13)
_INT63 = 2 ** 63 - 1

_mono = time.monotonic              # tests replace these
_now = time.time
_sleep = time.sleep


class Timing(object):
    """The clock of a try (tests make it short). iperf3: ``first_s`` — its first line of the sums comes in this time
    or it is stopped (an output we do not know); ``poll_s`` — how often the reader looks at the cap between the lines;
    ``stop_s`` — what the stop itself takes (the bytes of this time are counted ahead); ``bg_s`` — two samples of the
    counters of the interfaces this far apart before the start give the background (the traffic of people). HTTP:
    ``conn_s`` — the first connection of a try comes in this time or the try is «no_target»; the clock of the window
    (``run_s``, ``warm_s``) starts at that connection, not before it (B9: a slow first TLS connection to Cloudflare
    ate the window, and the upload was «no_target» with the target alive). ``load_min_s`` — the least window of 8c
    when the cap came before ``min_s`` (``LOAD_MIN_S``, Н-1)."""

    def __init__(self, run_s=10.0, warm_s=2.0, min_s=5.0, tick_s=0.25, io_s=5.0, grace_s=3.0, first_s=1.5,
                 poll_s=0.02, stop_s=0.05, bg_s=0.5, conn_s=3.0, load_min_s=None):
        self.run_s, self.warm_s, self.min_s, self.tick_s, self.io_s, self.grace_s = \
            run_s, warm_s, min_s, tick_s, io_s, grace_s
        self.first_s, self.poll_s, self.stop_s, self.bg_s = first_s, poll_s, stop_s, bg_s
        self.conn_s = conn_s
        self.load_min_s = LOAD_MIN_S if load_min_s is None else load_min_s


T = Timing()


# ── the targets ─────────────────────────────────────────────────────────────

def load_targets(path=None):
    """``channel-targets.json`` checked: a URL — ``https://`` with a plain host name (no user, no address literal);
    a point — a host name and a port. A bad entry is dropped; a broken file (not JSON, not an object) — no targets
    (every test «не измерено»; SEC П-4)."""
    try:
        with open(path or TARGETS, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    out = {"version": str(doc.get("version") or "")[:32], "http": {"down": {}, "up": {}}, "iperf3": {}}
    http_ = doc.get("http") if isinstance(doc.get("http"), dict) else {}
    for side in ("down", "up"):
        by = http_.get(side) if isinstance(http_.get(side), dict) else {}
        for region in ("ru", "abroad"):
            out["http"][side][region] = [t for t in (http_target(x) for x in _list(by.get(region))) if t]
    ip = doc.get("iperf3") if isinstance(doc.get("iperf3"), dict) else {}
    for region in ("ru", "abroad"):
        out["iperf3"][region] = [p for p in (iperf_point(x) for x in _list(ip.get(region))) if p]
    return out


def _list(x):
    return x if isinstance(x, list) else []


def http_target(x):
    """{"id", "url", "host", "port", "path", "max_req"} of an entry (or of a bare URL), or None."""
    if isinstance(x, str):
        x = {"id": "point", "url": x}
    if not isinstance(x, dict) or not isinstance(x.get("url"), str):
        return None
    m = _URL.fullmatch(x["url"])
    tid = x.get("id") if isinstance(x.get("id"), str) and _ID.fullmatch(x.get("id")) else None
    if not m or not tid or not host_name(m.group(1)):
        return None
    port = int(m.group(2) or 443)
    if not 0 < port < 65536:
        return None
    mr = x.get("max_req")
    mr = mr if type(mr) is int and 0 < mr <= DOWN_REQ else None
    return {"id": tid, "url": x["url"], "host": m.group(1), "port": port, "path": m.group(3), "max_req": mr}


def iperf_point(x):
    if not isinstance(x, dict):
        return None
    host, port, pid = x.get("host"), x.get("port"), x.get("id")
    if not (isinstance(host, str) and host_name(host) and type(port) is int
            and 0 < port < 65536 and isinstance(pid, str) and _ID.fullmatch(pid)):
        return None
    return {"id": pid, "host": host, "port": port}


def host_name(host):
    """A host NAME of the list (SEC З-3): labels of ``[a-z0-9-]``, a dot at least, the last label with a letter — so
    never an address literal (``127.0.0.1``, ``0``, ``127.1``, ``0x7f.1`` — the resolver takes them all as addresses);
    a label ``localhost`` anywhere (``localhost.``, ``foo.localhost`` — names of this host, RFC 6761) — never."""
    if not isinstance(host, str) or not _HOST.fullmatch(host) or ".." in host or "." not in host:
        return False
    if "localhost" in host.rstrip(".").split("."):
        return False
    last = host.rstrip(".").rsplit(".", 1)[-1]
    return bool(re.search(r"[a-z]", last)) and not last.startswith("0x")


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_V4COMPAT = ipaddress.ip_network("::/96")


def is_global(addr):
    """A global unicast address (SEC П-2): not loopback, private, link-local, multicast; an IPv6 that carries an IPv4
    (mapped, 6to4, NAT64) — by that IPv4; the old «IPv4-compatible» ``::/96`` — never."""
    try:
        a = ipaddress.ip_address(str(addr).split("%", 1)[0])
    except ValueError:
        return False
    if a.version == 6:
        if a.ipv4_mapped:
            a = a.ipv4_mapped
        elif a.sixtofour:
            a = a.sixtofour
        elif a in _NAT64:
            a = ipaddress.IPv4Address(int(a) & 0xFFFFFFFF)
        elif a in _V4COMPAT:
            return False
    return a.is_global and not a.is_multicast


def ru_by_rtt(ya_ms, cf_ms):
    """«Узел в России» by the delays (RS §2, the last resort): 77.88.8.8 clearly nearer than 1.1.1.1 — True; 1.1.1.1
    clearly nearer — False; clearly = ``RTT_FACTOR`` times and ``RTT_GAP_MS`` at least (SEC З-5); else None —
    unknown (the schedule takes it as Russia)."""
    if ya_ms is None or cf_ms is None or ya_ms <= 0 or cf_ms <= 0:
        return None
    if cf_ms >= ya_ms * RTT_FACTOR and cf_ms - ya_ms >= RTT_GAP_MS:
        return True
    if ya_ms >= cf_ms * RTT_FACTOR and ya_ms - cf_ms >= RTT_GAP_MS:
        return False
    return None


# ── TCP_INFO ────────────────────────────────────────────────────────────────

def tcp_fields(buf):
    """The fields of ``TI`` a ``tcp_info`` of ``len(buf)`` bytes holds; a counter that cannot be (a u64 above 2^63−1,
    a u32 at its maximum — a broken answer) is left out."""
    out = {}
    for k, (off, fmt) in TI.items():
        n = struct.calcsize("=" + fmt)
        if len(buf) < off + n:
            continue
        (v,) = struct.unpack_from("=" + fmt, buf, off)
        if (fmt == "Q" and v > _INT63) or (fmt == "I" and v == 0xFFFFFFFF):
            continue
        out[k] = v
    return out


def read_tcp_info(sock):
    """{field: value} of ``TCP_INFO`` of the socket (an ``ssl.SSLSocket`` too), or None (not Linux, an error)."""
    if sock is None or not sys.platform.startswith("linux"):
        return None
    try:
        return tcp_fields(sock.getsockopt(socket.IPPROTO_TCP, TCP_INFO, TI_LEN))
    except (OSError, ValueError, AttributeError):
        return None


def read_ca(sock):
    """The congestion control the socket really uses (inherited from net.ipv4.tcp_congestion_control), or None."""
    if sock is None or not sys.platform.startswith("linux"):
        return None
    try:
        raw = sock.getsockopt(socket.IPPROTO_TCP, TCP_CONGESTION, 16)
    except (OSError, ValueError, AttributeError):
        return None
    s = raw.split(b"\0", 1)[0].decode("ascii", "replace")
    return s if _CA.fullmatch(s) else None


def load_calc(a, b):
    """8c over two snapshots {key: tcp fields} of the upload streams (the 2nd second and the end of the window):
    -> {"retr": %, "rtt": ms, "why"}; ``why`` (REV-ns-b3 З-2): ``no_tcp_info`` — no answer of the kernel holds the
    fields (an old kernel); ``reconnect`` — the answers hold them, but no stream kept its connection between the two
    samples (a key is the stream and its socket)."""
    need = ("snd_mss", "rtt", "min_rtt", "bytes_sent", "bytes_retrans", "dsack_dups")
    full = [s for s in list((a or {}).values()) + list((b or {}).values())
            if s and all(f in s for f in need)]
    sent = lost = 0
    rtts, mins = [], []
    for k, y in (b or {}).items():
        x = (a or {}).get(k)
        if not x or not y or any(f not in x or f not in y for f in need):
            continue
        ds = y["bytes_sent"] - x["bytes_sent"]
        if ds < 0:
            continue
        sent += ds
        lost += max(0, y["bytes_retrans"] - x["bytes_retrans"]) - max(0, y["dsack_dups"] - x["dsack_dups"]) * y["snd_mss"]
        rtts.append(y["rtt"])
        mins.extend(m for m in (x["min_rtt"], y["min_rtt"]) if m > 0)
    if not rtts or sent <= 0:
        return {"retr": None, "rtt": None, "why": "reconnect" if full and not rtts else "no_tcp_info"}
    rtts.sort()
    n = len(rtts)
    med = rtts[n // 2] if n % 2 else (rtts[n // 2 - 1] + rtts[n // 2]) / 2.0
    rise = max(0.0, (med - min(mins)) / 1000.0) if mins else None
    return {"retr": round(100.0 * max(0, lost) / sent, 4), "rtt": None if rise is None else round(rise, 3), "why": None}


def early(moved, t, rate, cap, timing=T):
    """8c of a short window (Н-1): ``moved`` bytes in ``t`` s, the last rate ``rate`` bytes/s — at it the upload
    reaches the cap before ``min_s`` (the cap will end the usual window early), yet not sooner than ``load_min_s``
    from now: this sample may start the window of 8c. A nearer cap — no sample: the window would be under the least
    anyway (cap_early), and a sample of the sockets of iperf3 (sock_diag, up to ``suite.IPERF_TCP_S`` = 0.5 s) never
    holds the reader of the cap with less than ``load_min_s`` of the cap left at the rate (SEC З-1)."""
    if not (0 < t < timing.min_s and rate > 0):
        return False
    left = cap - moved
    return rate * timing.load_min_s <= left <= rate * (timing.min_s - t)


def short_window(starts, end, t_end, least=LOAD_MIN_S):
    """The window of 8c when the upload hit the cap early: the earliest of ``starts`` [(t, sample)] with a sample, to
    ``end`` taken at ``t_end`` -> (sample, seconds) or None (no sample, or under ``least`` s)."""
    got = [(t, x) for t, x in starts if x and t is not None]
    if not got or not end or t_end is None:
        return None
    t0, a = min(got, key=lambda p: p[0])
    return (a, round(t_end - t0, 2)) if t_end - t0 >= least else None


# ── one try over HTTP ───────────────────────────────────────────────────────

def https_connect(host, port, timeout):
    """A verified TLS connection to a GLOBAL address: the address is checked right after the TCP connection, before
    a byte is sent (a name that resolves to a local or private address never gets the TLS hello)."""
    raw = tcp_connect(host, port, timeout)
    try:
        peer = raw.getpeername()[0]
        if not is_global(peer):
            raise ConnectionError("not a global address")
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
        conn.sock = ctx.wrap_socket(raw, server_hostname=host)
    except BaseException:
        raw.close()
        raise
    conn.auto_open = 0              # never a silent new connection: a new one goes through this check again
    return conn


def addr_order(infos):
    """getaddrinfo results with the families taken in turn, the first family first (RFC 8305 §4, without the race):
    a node with a broken IPv6 reaches the IPv4 address after one ``ADDR_S``, not after every IPv6 one (B9)."""
    first = infos[0][0] if infos else None
    a = [i for i in infos if i[0] == first]
    b = [i for i in infos if i[0] != first]
    out = []
    for k in range(max(len(a), len(b))):
        out.extend(x[k] for x in (a, b) if k < len(x))
    return out


def tcp_connect(host, port, timeout, getaddrinfo=None):
    """``socket.create_connection`` with ``ADDR_S`` per address (families in turn), then ``timeout`` for the I/O."""
    infos = (getaddrinfo or socket.getaddrinfo)(host, port, 0, socket.SOCK_STREAM)
    err = None
    for fam, kind, proto, _canon, addr in addr_order(infos):
        sock = socket.socket(fam, kind, proto)
        try:
            sock.settimeout(min(timeout, ADDR_S))
            sock.connect(addr)
            sock.settimeout(timeout)
            return sock
        except OSError as e:
            err = e
            sock.close()
    raise err or OSError("no address of %s" % host)


class _Clock(object):
    """The clock of one HTTP try: ``t0`` — the first connection of its streams (None before it); the end of the
    streams — ``t0 + run_s``, and before any connection ``conn_s + run_s`` after the start (a bound only)."""

    def __init__(self, timing):
        self.timing, self.start, self.t0 = timing, _mono(), None
        self._lock = threading.Lock()

    def opened(self):
        with self._lock:
            if self.t0 is None:
                self.t0 = _mono()

    def end(self):
        t0 = self.t0
        return (t0 if t0 is not None else self.start + self.timing.conn_s) + self.timing.run_s


_BODY = os.urandom(CHUNK)


class _Stream(threading.Thread):
    """One stream of a direction. ``count()`` — the bytes moved so far: read (download) or acknowledged by the peer
    (upload: ``bytes_acked`` of ``TCP_INFO``, else written)."""

    def __init__(self, i, side, target, share, t_end, stop, connect, timing):
        super().__init__(name="kaskad-bench-net-%d" % i, daemon=True)
        self.i, self.side, self.t, self.share, self.stop = i, side, target, share, stop
        self.clock = t_end if isinstance(t_end, _Clock) else None        # the time of the end, or the clock of the try
        self.t_end = None if self.clock else t_end
        self.connect, self.timing = connect, timing
        self.n = 0                  # bytes of the application (read or written)
        self.done = 0               # bytes of the connections already closed (upload: all of them acknowledged)
        self.cur = 0                # bytes written on the current connection
        self.conn = self.sock = None
        self.opened = False
        self.ended = False
        self.err = None
        self.status = None
        self.family = None

    def count(self):
        if self.side == "up":
            ti = read_tcp_info(self.sock)
            if ti and "bytes_acked" in ti:
                return self.done + min(self.cur, ti["bytes_acked"])
        return self.n

    def _over(self):
        return self.stop.is_set() or _mono() >= (self.clock.end() if self.clock else self.t_end) or \
            self.n >= self.share

    def _open(self):
        if self.conn is not None:
            self._close()
        self.conn = self.connect(self.t["host"], self.t["port"], self.timing.io_s)
        self.sock = self.conn.sock
        self.opened = True
        if self.clock is not None:
            self.clock.opened()
        try:
            self.family = 6 if self.sock.family == socket.AF_INET6 else 4
        except AttributeError:
            pass

    def _close(self):
        c, self.conn = self.conn, None
        self.done += self.cur
        self.cur = 0
        if c is not None:
            try:
                c.close()
            except Exception:                   # noqa: BLE001
                pass

    def run(self):
        try:
            self._open()
            reqs = 0
            while not self._over() and reqs < REQ_MAX:
                if self.conn is None or self.conn.sock is None:
                    self._open()
                reqs += 1
                if self.side == "down":
                    if not self._get():
                        break
                elif not self._post():
                    break
        except Exception as e:                  # noqa: BLE001 — a stream that fails ends; the reader decides
            self.err = type(e).__name__
        finally:
            if self.side == "down" and self.n >= self.share:
                self.abort()                    # its share read: the peer stops now, the kernel never fills its
                #                                 buffer with bytes above the cap (SEC З-1); an upload stops itself
            elif self.stop.is_set():
                self._close()                   # connected after the try had ended (B9: conn_s): never left open
            self.ended = True

    def _headers(self):
        return {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity", "Cache-Control": "no-cache"}

    def _get(self):
        path = self.t["path"]
        if "{bytes}" in path:
            path = path.replace("{bytes}", str(max(1, min(self.share - self.n, self.t["max_req"] or DOWN_REQ))))
        self.conn.request("GET", path, headers=self._headers())
        resp = self.conn.getresponse()
        if resp.status != 200:
            self.status = resp.status
            return False
        buf = bytearray(CHUNK)
        mv = memoryview(buf)
        while not self._over():
            k = resp.readinto(mv[:max(1, min(CHUNK, self.share - self.n))])
            if not k:
                break
            self.n += k
            self.cur += k
        if self._over():
            return False
        if resp.will_close:
            self._close()
        return True

    def _post(self):
        size = max(1, min(self.share - self.n, self.t["max_req"] or UP_REQ))
        c = self.conn
        c.putrequest("POST", self.t["path"], skip_accept_encoding=True)
        for k, v in sorted(self._headers().items()):
            c.putheader(k, v)
        c.putheader("Content-Type", "application/octet-stream")
        c.putheader("Content-Length", str(size))
        c.endheaders()
        sent = 0
        while sent < size:
            if self._over():
                return False
            k = min(CHUNK, size - sent)
            c.sock.sendall(_BODY[:k])
            sent += k
            self.n += k
            self.cur += k
        resp = c.getresponse()
        resp.read(RESP_MAX)                     # at most 64 KB of an answer (SEC П-1) ...
        if resp.status != 200:
            self.status = resp.status
            return False
        if resp.will_close or not resp.isclosed():
            self._close()                       # ... a longer one is never read: the connection goes
        return True

    def abort(self):
        s = self.sock
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except (OSError, ValueError):
                pass
        self._close()


def window(samples, warm_s, min_s, cap, moved, budget):
    """The value of a try from the samples [(t s, bytes)] of the reader: the bytes after ``warm_s`` ÷ their time;
    the cap hit before ``min_s`` — the lower bound (all the bytes ÷ all the time). -> (Mbit/s | None, lower)."""
    if not samples:
        return None, False
    t_end, b_end = samples[-1]
    start = next((s for s in samples if s[0] >= warm_s), None)
    if budget and t_end < min_s or start is None or start is samples[-1]:
        if budget and t_end > 0 and moved > 0:
            return moved * 8 / t_end / 1e6, True
        return None, False
    span = t_end - start[0]
    if span <= 0 or b_end <= start[1]:
        return None, False
    return (b_end - start[1]) * 8 / span / 1e6, False


def http_try(side, target, cap, timing=T, connect=https_connect, probe=None):
    """One try of a direction over HTTP -> {"value", "lower", "budget", "bytes", "family", "tcp": (a, b) | None,
    "load": (a, b) | None, "ca", "why"} (+ "win": s — the upload hit the cap before ``min_s`` and 8c has a window of
    ``short_window`` from the first tick of ``early``, Н-1). ``probe`` — a callable that takes a sample of the load
    (NS-S16)."""
    stop = threading.Event()
    clock = _Clock(timing)
    share = max(1, cap // STREAMS)
    ss = [_Stream(i, side, target, share, clock, stop, connect, timing) for i in range(STREAMS)]
    out = {"value": None, "lower": False, "budget": False, "bytes": 0, "family": None, "tcp": None, "load": None,
           "ca": None, "why": None, "target": target["id"], "how": "http"}
    samples, tcp_a, load_a = [], None, None
    tcp_e = t_a = t_e = t_b = None              # Н-1: the start of a short window of 8c and the moments of the samples

    def snap():
        if side != "up":
            return None
        return dict(((s.i, id(s.sock)), ti) for s in ss for ti in (read_tcp_info(s.sock),) if ti)

    def take():
        try:
            return probe() if probe else None
        except Exception:                       # noqa: BLE001 — the load is a hint; never the end of the try
            return None
    try:
        for s in ss:
            s.start()
        while True:
            _sleep(timing.tick_s)
            if clock.t0 is None:                # no connection yet: the window has not started (B9)
                if _mono() - clock.start >= timing.conn_s or all(s.ended for s in ss):
                    break
                continue
            t = _mono() - clock.t0
            samples.append((t, sum(s.count() for s in ss)))
            if side == "up" and tcp_e is None and tcp_a is None and t >= timing.tick_s:
                pt, pb = samples[-2] if len(samples) > 1 else (0.0, 0)
                if t > pt and early(samples[-1][1], t, (samples[-1][1] - pb) / (t - pt), share * STREAMS, timing):
                    tcp_e, t_e = snap(), _mono()
            if tcp_a is None and t >= timing.warm_s:
                tcp_a, t_a = snap(), _mono()
                load_a = take()
            if t >= timing.run_s or all(s.ended for s in ss):
                break
        if clock.t0 is not None:
            tcp_b, t_b = snap(), _mono()
            load_b = take()
        else:
            tcp_b = load_b = None
        if side == "up":
            out["ca"] = next((c for c in (read_ca(s.sock) for s in ss) if c), None)
    finally:
        stop.set()
        for s in ss:
            s.abort()
        for s in ss:
            s.join(timing.grace_s)
    moved = sum(s.n for s in ss)
    out["bytes"] = moved
    out["family"] = next((s.family for s in ss if s.family), None)
    if not any(s.opened for s in ss) or clock.t0 is None:
        out["why"] = "no_target"
        return out
    out["budget"] = moved >= share * STREAMS
    v, lower = window(samples, timing.warm_s, timing.min_s, cap, moved, out["budget"])
    if v is None or v <= 0:
        out["why"] = "no_target"
        return out
    out.update(value=round(v, 3), lower=lower)
    if tcp_a is not None:
        out["tcp"] = (tcp_a, tcp_b)
    win = short_window(((t_e, tcp_e), (t_a, tcp_a)), tcp_b, t_b, timing.load_min_s) if lower and side == "up" else None
    if win is not None:                         # Н-1: the cap came before min_s — 8c over the window it had
        out["tcp"], out["win"] = (win[0], tcp_b), win[1]
    if load_a is not None and load_b is not None and not lower:
        out["load"] = (load_a, load_b)
    return out


# ── one try over iperf3 (8b) ────────────────────────────────────────────────

_SUM = re.compile(r"^\[SUM\]\s+([0-9.]+)-\s*([0-9.]+)\s+sec\s+([0-9.]+)\s+([KMGT]?)Bytes\s+[0-9.]+\s+[KMGT]?bits/sec(.*)$")
_MULT = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}


def iperf_argv(point, reverse):
    """A fixed argv: the CHECKED address of the point (``reach``, never its name — SEC З-3) and its port."""
    return (["iperf3", "-c", point["addr"], "-p", str(point["port"]), "-P", str(STREAMS), "-t", "10", "-O", "2",
             "-i", "0.25", "--forceflush"] + (["-R"] if reverse else []))


def drop_argv(which=shutil.which, euid=None):
    """What runs iperf3 without the rights of root (SEC З-6): ``setpriv`` to nobody/nogroup, no groups, no new
    privileges, no capabilities -> the argv before iperf3; [] — this process is not root; None — root without
    ``setpriv`` (iperf3 is then never run: it reads the answers of servers of others)."""
    if euid is None:
        euid = os.geteuid() if hasattr(os, "geteuid") else 0
    if euid != 0:
        return []
    sp = which("setpriv")
    if not sp:
        return None
    return [sp, "--reuid=%d" % NOBODY, "--regid=%d" % NOBODY, "--clear-groups", "--no-new-privs", "--inh-caps=-all"]


def _proc(path):
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def counted_ifaces(route4="/proc/net/route", route6="/proc/net/ipv6_route", dev="/proc/net/dev"):
    """The interfaces the counters of 8b sum (REV-ns-b3-2 С-1) -> sorted names: those of the default routes v4/v6, as
    ``bench.quiet`` counts the traffic of people (``quiet.ifaces``: no tunnels ``wg*``/``awg*``/``kawg*``/``tun*``, no
    ``lo`` — a byte of a person through the node is on a tunnel AND on the outer interface); no default route read —
    every interface of ``dev`` but those; [] — not Linux."""
    from . import quiet
    names = quiet.ifaces(_proc(route4) or "", _proc(route6) or "")
    if names:
        return names
    return sorted(n for n in quiet.net_dev(_proc(dev) or "") if n != "lo" and not quiet.TUNNEL.match(n))


def net_counters(side, path="/proc/net/dev", names=None):
    """The bytes of the direction (``/proc/net/dev``: received for the download, sent for the upload) of the
    interfaces ``names`` (``counted_ifaces()`` when None), or None (not Linux, none of them there). People count too
    unless the caller takes their background off (``iperf_try``)."""
    from . import quiet
    text = _proc(path)
    if text is None:
        return None
    if names is None:
        names = counted_ifaces(dev=path)
    devs = quiet.net_dev(text)
    got = [devs[n] for n in names if n in devs]
    if not got:
        return None
    return sum(x[0] if side == "down" else x[2] for x in got)


def iperf_line(line):
    """A line of the intervals of all the streams -> (seconds, bytes, omitted) or None (a stream's own line, the
    summary «sender»/«receiver», anything else)."""
    m = _SUM.match(line.strip())
    if not m:
        return None
    tail = m.group(5)
    if "sender" in tail or "receiver" in tail:
        return None
    try:
        a, b, n = float(m.group(1)), float(m.group(2)), float(m.group(3))
    except ValueError:
        return None
    if b <= a:
        return None
    return b - a, int(n * _MULT[m.group(4)]), "omitted" in tail


_STREAM = re.compile(r"^\[\s*(?:[0-9]+|SUM)\]\s+[0-9.]+\s*-\s*[0-9.]+\s+sec\b")   # a line of the intervals


def _tcp_snap(tcp, pid, port):
    """A sample {key: tcp fields} of the sockets of iperf3 (``Env.tcp``), or None (none given, an error, not a dict)."""
    if tcp is None or pid is None:
        return None
    try:
        got = tcp(pid, port)
    except Exception:                           # noqa: BLE001 — 8c is a hint; never the end of the try
        return None
    return got if isinstance(got, dict) else None


def streams_of(a, b):
    """The two samples of 8c over 8b without the control connection of iperf3: a key whose ``bytes_sent`` grew by less
    than ``STREAM_MIN`` between them (or that is not in both) is left out -> (a, b)."""
    keep = [k for k, y in b.items() if isinstance(y, dict) and isinstance(a.get(k), dict)
            and isinstance(y.get("bytes_sent"), int) and isinstance(a[k].get("bytes_sent"), int)
            and y["bytes_sent"] - a[k]["bytes_sent"] >= STREAM_MIN]
    return dict((k, a[k]) for k in keep), dict((k, b[k]) for k in keep)


def iperf_try(side, point, cap, timing=T, popen=None, counter=None, drop=None, tcp=None):
    """One try of 8b -> the same dict as ``http_try`` (``why``: points_busy — «the server is busy», the next point;
    no_target — it refused or broke; tool_failed — an output we do not know, an iperf3 without ``--forceflush``, root
    without ``setpriv``: no next point; ``end`` — no next point either: the point took the connection and is silent).
    The cap (SEC З-1, З-2): every line at once; iperf3 is stopped when the bytes of the sums plus the bytes of the last
    rate until the stop reach the cap, when the counters of the interfaces (``counter(side)``, ``net_counters``) minus
    their background reach it, or when no line of the sums came in ``first_s``. The background (REV-ns-b3-2 С-1): two
    samples of the counters ``bg_s`` apart before the start give the rate of people; it is taken off for the time since
    the start, the cap at most — so the counters stop iperf3 at its own cap, and «не измерено» comes only when no line of the sums came
    at all (people never make a measured try «not measured»). ``bytes`` — the larger of the sums and the counters (the
    cap of the direction is shared by all its tries). ``tcp(pid, port)`` — the samples of 8c (the upload only): at the
    first measured line of the sums and when 1 s of the window is left (or at the stop, iperf3 still running);
    ``tcp`` of the result — (a, b) of ``streams_of``. The cap before ``min_s`` (Н-1): one more sample at the first line
    of ``early`` (omitted or not), the window from it (or from the first measured line, the earlier) to the stop —
    ``short_window``; ``win`` of the result — its seconds (no key — no such window)."""
    out = {"value": None, "lower": False, "budget": False, "bytes": 0, "family": None, "tcp": None, "load": None,
           "ca": None, "why": None, "target": point["id"], "how": "iperf3"}
    if not is_global(point.get("addr")):
        out["why"] = "tool_failed"              # never a name, never a local address
        return out
    pre = drop_argv() if drop is None else drop
    if pre is None:
        out["why"] = "tool_failed"
        return out
    popen = popen or subprocess.Popen
    bg = 0.0
    c_a, t_a = _count(counter, side), _mono()
    if c_a is not None and timing.bg_s > 0:
        _sleep(timing.bg_s)
    c0, t_c0 = _count(counter, side), _mono()
    if c_a is not None and c0 is not None and t_c0 > t_a:
        bg = max(0.0, (c0 - c_a) / (t_c0 - t_a))   # bytes/s of people before the start
    try:
        p = popen(pre + iperf_argv(point, side == "down"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                  stdin=subprocess.DEVNULL, env=dict(_IPERF_ENV), cwd="/", close_fds=True, universal_newlines=True,
                  encoding="utf-8", errors="replace", bufsize=1)
    except OSError:
        out["why"] = "no_iperf3"
        return out
    q = queue.Queue()

    def reader():
        try:
            for line in p.stdout:
                q.put(line)
        except (OSError, ValueError):
            pass
        finally:
            q.put(None)
    th = threading.Thread(target=reader, name="kaskad-bench-iperf3", daemon=True)
    th.start()
    t0 = _mono()

    def own(c, now):
        # the counters without the background of people; never more than the cap taken off — a burst in the samples
        # of the background never lets iperf3 pass more than twice the cap by the counters
        return c - c0 - min(bg * (now - t_c0), cap)

    meas_s = meas_b = all_s = all_b = guard = 0
    rate, t_line, by, people = 0.0, None, None, None
    up_tcp = tcp if side == "up" else None
    tcp_a = tcp_b = tcp_e = None
    t_a = t_e = t_b = None                      # Н-1: the start of a short window of 8c and the moments of the samples
    pid = getattr(p, "pid", None)
    busy = bad = err = unknown = streams = False
    try:
        while True:
            try:
                line = q.get(timeout=timing.poll_s)
            except queue.Empty:
                line = ""
            if line is None:
                break                           # iperf3 ended
            if line:
                low = line.lower()
                busy = busy or "busy" in low
                bad = bad or "unrecognized option" in low or "invalid option" in low   # no --forceflush
                err = err or "error" in low
                streams = streams or bool(_STREAM.match(line.strip()))
                x = iperf_line(line)
                if x is not None:
                    s, n, omitted = x
                    all_s, all_b = all_s + s, all_b + n
                    if up_tcp is not None and tcp_e is None and tcp_a is None and \
                            early(all_b, all_s, n / s, cap, timing):
                        # Н-1: at this rate the cap comes before min_s — the start of a short window of 8c
                        tcp_e, t_e = _tcp_snap(up_tcp, pid, point["port"]), _mono()
                        if tcp_e is None:
                            up_tcp = None
                    if not omitted:
                        meas_s, meas_b = meas_s + s, meas_b + n
                        if up_tcp is not None:      # 8c: the 2nd second (the first measured line) and the last one
                            if tcp_a is None:
                                tcp_a, t_a = _tcp_snap(up_tcp, pid, point["port"]), _mono()
                                if tcp_a is None:
                                    up_tcp = None
                            elif tcp_b is None and meas_s >= timing.run_s - 1.0:
                                tcp_b, t_b = _tcp_snap(up_tcp, pid, point["port"]), _mono()
                    rate, t_line = n / s, _mono()
            now = _mono()
            # 1. by the text: what passed + what passes until iperf3 stops, at the rate of the last interval
            if t_line is not None and all_b + rate * (now - t_line + timing.stop_s) >= cap:
                out["budget"], by = True, "text"
                break
            # 2. by the counters of the interfaces (people taken off), whatever the text says
            c = _count(counter, side)
            if c is not None and c0 is not None:
                guard = max(guard, own(c, now))
                if guard >= cap:
                    out["budget"], by = True, "counters"
                    if t_line is not None:      # what people passed beyond iperf3 (its text projected, with margins)
                        people = guard - HDR * (all_b + rate * (now - t_line + LINE_LAG_S))
                    break
            # 3. no line of the sums in time: an output we do not know or a silent point — never on blind
            if t_line is None and now - t0 > timing.first_s:
                unknown = True
                break
            if now - t0 > timing.warm_s + timing.run_s + timing.grace_s + 2:
                break
    finally:
        if up_tcp is not None and (tcp_a is not None or tcp_e is not None) and tcp_b is None and p.poll() is None:
            tcp_b, t_b = _tcp_snap(up_tcp, pid, point["port"]), _mono()   # stopped by the cap: the sockets before they go
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timing.grace_s)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timing.grace_s)
        th.join(timing.grace_s)
        close = getattr(p.stdout, "close", None)
        if close:
            close()
    t_all = _mono() - t0
    c = _count(counter, side)
    if c is not None and c0 is not None:
        guard = max(guard, own(c, _mono()))
    guard = int(max(0, guard))
    out["bytes"] = max(all_b, guard)
    if out["budget"] and t_line is None and not unknown and not bad and t_all > 0:
        # the counters reached the cap before the first line (a fast link, the quick cap): «not less than» by them
        out.update(value=round(guard * 8 / t_all / 1e6, 3), lower=True)
    elif bad or (unknown and streams):
        out.update(budget=False, why="tool_failed")         # no line of the sums at all: not measured
    elif unknown and not busy:
        out.update(budget=False, why="no_target", end=True)  # took the connection and is silent: no next point
    elif by == "counters" and people is not None and people >= cap * PEOPLE_SHARE and (meas_s > 0 and meas_b > 0
                                                                                         or all_s > 0):
        # REV-ns-b3-3 З-1 (the coordinator, 19.10): the counters stopped it, and they are above what iperf3 moved by
        # its own text (projected, with the headers and one interval of the lines — REV-ns-join С-1) by a share of
        # the cap — people filled the cap; the value of the text as it is, never «не меньше» and never ``budget``
        # (``bytes`` stays the cap: no more tries of the direction)
        v = meas_b * 8 / meas_s / 1e6 if meas_s > 0 and meas_b > 0 else all_b * 8 / all_s / 1e6
        out.update(value=round(v, 3), budget=False, lower=False)
    elif out["budget"] and meas_s < timing.min_s - timing.warm_s and all_s > 0:
        out.update(value=round(all_b * 8 / all_s / 1e6, 3), lower=True)
    elif meas_s > 0 and meas_b > 0:
        out["value"] = round(meas_b * 8 / meas_s / 1e6, 3)
    elif t_line is None:
        out["why"] = "points_busy" if busy else "no_target" if err else "tool_failed"
    else:
        out["why"] = "no_target"                # it broke in the first seconds
    if out["value"] is not None and tcp_a and tcp_b:
        out["tcp"] = streams_of(tcp_a, tcp_b)
    win = (short_window(((t_e, tcp_e), (t_a, tcp_a)), tcp_b, t_b, timing.load_min_s)
           if out["value"] is not None and out["lower"] else None)
    if win is not None:                         # Н-1: the cap came before min_s — 8c over the window it had
        out["tcp"], out["win"] = streams_of(win[0], tcp_b), win[1]
    return out


def _count(counter, side):
    if counter is None:
        return None
    try:
        return counter(side)
    except Exception:                           # noqa: BLE001 — no counters: the text and the clock still stop it
        return None


def connect_addr(host, port, timeout=2.0):
    """One TCP connection -> (its address if global, ms) or (None, None): the address iperf3 then gets."""
    t = _mono()
    try:
        s = socket.create_connection((host, port), timeout)
    except (OSError, ValueError):
        return None, None
    try:
        peer = s.getpeername()[0]
    except OSError:
        peer = None
    finally:
        s.close()
    if not is_global(peer):
        return None, None
    return peer, round((_mono() - t) * 1000.0, 2)


def connect_ms(host, port, timeout=2.0):
    """The time of a TCP connection to a global address, ms, or None."""
    return connect_addr(host, port, timeout)[1]


def reach(point, addr=connect_addr):
    """The point with its checked address ``addr`` (one TCP connection, 2 s), or None — it does not answer."""
    a = addr(point["host"], point["port"])[0]
    return dict(point, addr=a) if a else None


class Net(object):
    """The network of the tests; tests replace it."""

    def __init__(self, timing=T, tcp=None):
        self.timing, self.tcp = timing, tcp     # tcp — ``Env.tcp``: the samples of 8c over 8b

    def http(self, side, target, cap, probe=None):
        return http_try(side, target, cap, self.timing, probe=probe)

    def iperf(self, side, point, cap):
        names = counted_ifaces()                # once a try: the interfaces of the default routes (REV-ns-b3-2 С-1)
        return iperf_try(side, point, cap, self.timing, counter=lambda s: net_counters(s, names=names), tcp=self.tcp)

    def reach(self, point):
        return reach(point)


# ── what the product gives ──────────────────────────────────────────────────

class Env(object):
    """What the block needs of the product (all of it optional):

    ru          the node is in Russia: True | False | None (unknown — see ``in_ru``)
    manual      the check is by the button (panel, bot, SSH, CLI, MCP); False — the schedule or the repeat (the
                country no longer depends on it — ``in_ru``)
    up_url      the upload point a node in Russia may use (its owner allowed it, NS-S5), ``https://`` — or None
    up_cap_mb   its cap of a direction, MB (decimal) — or None (the cap of the mode)
    iperf       test 8b allowed (a manual check: a full one by default, the person did not turn it off — D-144)
    have_iperf  iperf3 is on the node
    skip        ⊆ SKIP
    probe()     a sample of the load counters (NS-S8) — or None
    people(a, b, side)    the traffic of others of the side between two samples, Mbit/s, or None
    level(test, mode, ts) L of NS-S16 — or None
    port        the port of the tariff, Mbit/s — or None
    family(f)   told the family (4 | 6) of the connection of the test
    log(key, **args)      a line of the journal
    left()      seconds left of the check
    progress(step, i, n)
    targets     ``load_targets()``
    tcp(pid, port)        8c over 8b: {key: fields of ``TI``} of the TCP connections of the process ``pid`` to the
                          remote ``port`` — or None (no sock_diag); keys stay the same for a connection
    """

    def __init__(self, ru=None, up_url=None, up_cap_mb=None, iperf=False, have_iperf=False, skip=(), probe=None,
                 people=None, level=None, port=None, family=None, log=None, left=None, progress=None, targets=None,
                 manual=False, tcp=None):
        self.ru, self.up_url, self.up_cap_mb, self.iperf, self.have_iperf = ru, up_url, up_cap_mb, iperf, have_iperf
        self.manual = bool(manual)
        self.tcp = tcp
        self.skip = frozenset(skip or ())
        self.probe, self.people, self.level, self.port = probe, people, level, port
        self.family = family or (lambda f: None)
        self.log = log or (lambda key, **kw: None)
        self.left = left or (lambda: 1e9)
        self.progress = progress or (lambda step, i, n: None)
        self.targets = targets if targets is not None else load_targets()

    def in_ru(self):
        """The node is taken as one in Russia: known so — or unknown, by the button too (one rule with the schedule
        and the repeat: no upload but to the allowed point, the Russian targets — «a failure is a block», SEC З-5;
        П3-а after B9: the button measured abroad, the schedule as Russia — two series that never compare)."""
        return self.ru is not False


def correct(value, people, level, port=None):
    """value_corr of NS-S16 — the one formula of the score (``kaskad.score.load_correct``)."""
    from .. import score
    return score.load_correct(value, people, level, port)


# ── the tests ───────────────────────────────────────────────────────────────

def _row(test, value=None, why=None, **more):
    r = {"test": test, "value": value, "why": None if value is not None else (why or "tool_failed"),
         "unit": UNITS[test]}
    r.update((k, v) for k, v in more.items() if v is not None)
    return r


def _cap(env, test, mode):
    cap = SIDE_CAP[mode]
    if test == "net_up" and env.in_ru() and env.up_url and env.up_cap_mb:
        cap = min(cap, int(env.up_cap_mb) * 10 ** 6)
    return cap


def _plan(env, test, mode=None):
    """-> ("http" | "iperf3" | None, [targets], why when None). 8b — for a node in Russia only (its points are those
    of the Russian providers, the line of consent names them). The upload of a node in Russia without an allowed point
    in a FULL manual check that turned 8b off — ``upload_off`` (D-144: the person's choice, said in words); the quick
    one, the schedule and the repeat — ``no_upload_ru`` as before."""
    side = "down" if test == "net_down" else "up"
    ru = env.in_ru()
    region = "ru" if ru else "abroad"
    if side in env.skip:
        return None, [], "excluded"
    if test == "net_up" and ru:
        t = http_target({"id": "allowed", "url": env.up_url}) if env.up_url else None
        hs = [t] if t else []
    else:
        hs = list(env.targets["http"][side].get(region) or ())
    if hs:
        return "http", hs, None
    if ru and env.iperf and "8b" not in env.skip:
        if not env.have_iperf:
            return None, [], "no_iperf3"
        return "iperf3", list(env.targets["iperf3"].get("ru") or ()), None
    if test == "net_up" and ru:
        off = env.manual and mode == "full" and (not env.iperf or "8b" in env.skip)
        return None, [], "upload_off" if off else "no_upload_ru"
    return None, [], "no_target"


def _attempt(net, how, side, target, cap, env):
    if how == "http":
        return net.http(side, target, cap, probe=env.probe)
    return net.iperf(side, target, cap)


def _step(env, test, tries):
    """«Шаг i из n» of a direction: two as planned (a try and a confirm), more when a target failed and the next one or
    the first once more (B9) went — then i of i, never «3 из 2» (REV-ns-p3a П-1)."""
    i = len(tries) + 1
    env.progress(test, i, max(2, i))


def side_test(test, mode, env, net):
    """One direction with its confirm -> (the chosen try or None, why, tries). Every try leaves ``TAIL_S`` for the
    series B (REV З-1); the cap of the direction is one for all the tries, the failed ones too (SEC З-2). 8b: the
    points one by one in the order of the list, each checked by one connection just before its run (the line of
    consent, REV С-1); a broken iperf3 (``no_iperf3``, ``tool_failed``) or a silent point (``end``) ends the
    direction; ``IPERF_RUNS`` counts the confirm too (REV-ns-b3-2 П-1)."""
    side = "down" if test == "net_down" else "up"
    how, cands, why = _plan(env, test, mode)
    if how is None:
        return None, why, []
    cap = _cap(env, test, mode)
    used, tries, first, why, nxt = 0, [], None, ("points_busy" if how == "iperf3" else "no_target"), 0
    for i, t in enumerate(cands):
        if env.left() < TRY_NEED_S + TAIL_S:
            why = "time"
            break
        if cap - used < cap * RETRY_MIN_SHARE:
            break
        if how == "iperf3":
            if sum(1 for r in tries if r["how"] == "iperf3") >= IPERF_RUNS:
                break
            t = net.reach(t)
            if t is None:
                continue                        # it does not answer: the next one of the list
        _step(env, test, tries)
        r = _attempt(net, how, side, t, cap - used, env)
        used += max(0, int(r.get("bytes") or 0))
        tries.append(r)
        if r["value"] is not None:
            first, nxt = (r, t), i + 1
            break
        why = r["why"] or why
        if r["why"] in ("no_iperf3", "tool_failed") or r.get("end"):
            break
    if first is None and how == "http" and tries and len(tries) == len(cands) and \
            all(r["why"] == "no_target" for r in tries) and cap - used >= cap * RETRY_MIN_SHARE and \
            env.left() >= FAIL_WAIT + TRY_NEED_S + TAIL_S:
        # B9: every target failed — the first connection to Cloudflare from a Russian network hangs now and then, the
        # next one answers; the upload has one target: the first target once more after FAIL_WAIT
        _sleep(FAIL_WAIT)
        _step(env, test, tries)
        r = _attempt(net, how, side, cands[0], cap - used, env)
        used += max(0, int(r.get("bytes") or 0))
        tries.append(r)
        if r["value"] is not None:
            first, nxt = (r, cands[0]), 1
        else:
            why = r["why"] or why
    if first is None:
        return None, why, tries
    best, at = first
    runs_left = how != "iperf3" or sum(1 for r in tries if r["how"] == "iperf3") < IPERF_RUNS   # REV-ns-b3-2 П-1
    if best["value"] < ANCHOR[test] and not best["lower"] and cap - used >= cap * RETRY_MIN_SHARE and runs_left:
        # another target of the list; the same one after RETRY_WAIT when there is none — and for 8b always (the
        # line of consent names no second point; A.1 «8b: another point» gives way to it)
        other = how == "http" and nxt < len(cands)
        again, wait = (cands[nxt], 0) if other else (at, RETRY_WAIT)
        if env.left() >= wait + TRY_NEED_S + TAIL_S:
            if wait:
                _sleep(wait)
            _step(env, test, tries)
            r = _attempt(net, how, side, again, cap - used, env)
            tries.append(r)
            if r["value"] is not None and r["value"] > best["value"]:
                best = r
    return best, None, tries


def measure(mode, env, net=None):
    """Tests 8, 8b, 8c -> rows (``_row``) in the order of ``TESTS``."""
    net = net or Net()
    if getattr(net, "tcp", False) is None:
        net.tcp = env.tcp                       # the sampler of 8c over 8b — of the product (a test's net keeps its own)
    rows, up, up_why = [], None, None
    geo = {"geo": "unknown_ru"} if env.ru is None else {}   # the person sees it (REV-ns-b3-2 З-1), not only the journal
    if env.ru is None:
        env.log("bench_run.log.country_ru")
    for test in ("net_down", "net_up"):
        ts = int(_now())
        best, why, tries = side_test(test, mode, env, net)
        if best is None:
            env.log("bench_run.log.channel_na", test=test, tries=len(tries), why=why or "-")
            # REV-ns-b3-3 П-3: no «страна не определена» beside «исключено» — nothing was measured anywhere
            rows.append(_row(test, why=why, extra=dict(geo) if geo and why != "excluded" else None))
            if test == "net_up":
                up_why = why
            continue
        env.log("bench_run.log.channel", test=test, value=best["value"], how=best["how"], target=best["target"],
                tries=len(tries), why=why or "-")
        if best.get("family"):
            env.family(best["family"])
        extra = dict(geo, **({"budget": True} if best["budget"] else {})) or None
        if isinstance(best.get("target"), str):                    # до какой точки мерили — в кадр «Поделиться» (владелец 24.10)
            extra = dict(extra or {}, target=best["target"])
        people = corr = None
        if best.get("load") and env.people:
            try:
                people = env.people(best["load"][0], best["load"][1], "down" if test == "net_down" else "up")
            except Exception:                   # noqa: BLE001 — unknown, as without the counters
                people = None
        if people is not None:
            people = round(max(0.0, people), 3)
            level = None
            try:
                level = env.level(test, mode, ts) if env.level else None
            except Exception:                   # noqa: BLE001 — no L: «под нагрузкой, не годен»
                level = None
            corr = correct(best["value"], people, level, env.port)
            corr = None if corr is None else round(corr, 3)
        rows.append(_row(test, best["value"], extra=extra, people=people, value_corr=corr,
                         ca=best["ca"] if test == "net_up" else None))
        if test == "net_up":
            up = best
    if "up" in env.skip:
        rows += [_row("load_retr", why="excluded"), _row("load_rtt", why="excluded")]
    elif up is None or up["lower"] and not up.get("win"):
        # the upload turned off by the person — 8c says so too (D-144), not «the upload did not run»; the cap came
        # before min_s and the window left is under LOAD_MIN_S (Н-1) — cap_early
        why = "cap_early" if up is not None else "upload_off" if up_why == "upload_off" else "no_upload"
        rows += [_row("load_retr", why=why), _row("load_rtt", why=why)]
    else:
        lc = load_calc(*up["tcp"]) if up.get("tcp") else {"retr": None, "rtt": None, "why": "no_tcp_info"}
        win = up["win"] if up["lower"] else None    # Н-1: a short window — the hint «число менее надёжно»
        rows.append(_row("load_retr", lc["retr"], lc["why"], ca=up["ca"],
                         extra={"win": win} if win and lc["retr"] is not None else None))
        rows.append(_row("load_rtt", lc["rtt"], lc["why"] if lc["rtt"] is None else None, ca=up["ca"],
                         extra={"win": win} if win and lc["rtt"] is not None else None))
    return rows


def load_line(rows):
    """The line «под нагрузкой» (NS-D27, before the calibration: numbers only) -> (key, args) or None."""
    by = dict((r["test"], r) for r in rows)
    a, b = by.get("load_retr") or {}, by.get("load_rtt") or {}
    if a.get("value") is None or b.get("value") is None:
        return None
    return "bench_run.load.line", {"retr": round(a["value"], 1), "rtt": int(round(b["value"]))}


# ── the step of the check ───────────────────────────────────────────────────

def steps(mode, skip=(), iperf=False):
    """The steps of the channel for ``bench.suite`` (between the disk and the series B): (name, fn(ctx), the seconds
    it may take at least, tests(mode)). ``ctx.channel_env(skip, iperf)`` gives the ``Env`` of the product;
    ``ctx.row(test, value, why, **fields)`` takes the rows."""
    skip = frozenset(skip or ())
    bad = sorted(skip - set(SKIP))
    if bad:
        raise ValueError("skip: %s" % ", ".join(bad))

    def net(ctx):
        env = ctx.channel_env(skip=skip, iperf=bool(iperf) and "8b" not in skip)
        for r in measure(ctx.mode, env):
            fields = dict((k, r[k]) for k in ("extra", "people", "value_corr", "ca") if k in r)
            ctx.row(r["test"], r["value"], r["why"], unit=r["unit"], **fields)
    return (("net", net, lambda m: NEED_S, lambda m: TESTS),)
