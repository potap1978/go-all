"""One HTTPS GET through an exit, guarded against SSRF (docs/k3/SPEC-stage1-exits §5.3, §5.4; stage 1, T6).

``safe_get(url, via, limits, guard)`` is the one function of the speed test, the check of a service, the country
and «Проверить ссылку»:

1. ``https://`` only, no ``user:pass@``, port 443 or an explicit one — not 25, 53 nor a port of the node
   (``xray.internal_ports()`` and the SOCKS inbounds of the probes).
2. The name is resolved **through the same exit** — DoH to ``https://1.1.1.1/dns-query`` (AmneziaWG: the socket with
   the mark of the leg; xray: SOCKS5 of ``probe-<id>`` to the IP 1.1.1.1). Refused when any address is not global or
   is in 0.0.0.0/8, 100.64.0.0/10, 198.18.0.0/15, 224.0.0.0/4, 240.0.0.0/4, among the node's addresses or ``gw_nets``.
3. The connection goes to the **checked IP** (xray: SOCKS ATYP=IPv4); SNI and ``Host`` are the name (DNS rebinding:
   a second answer of the resolver is never what we connect to).
4. Redirects ≤ 3, each one checked by 1–3 again; https → http — refused.
5. Limits: bytes (``max_mb``, at most 50 MB), time (``max_s``, at most 30 s), the connection 5 s, TTFB 10 s, the
   headers ≤ 16 KB. A download stopped by a limit is closed with a reset (no FIN, no TIME_WAIT). One deadline holds
   the whole request — DoH, every hop, the connection, TLS, the headers and the body: ``total_s(lim)`` =
   ``connect_s + ttfb_s + max_s`` from the start (and never past the caller's ``deadline``), so a slow server never
   keeps a probe longer (``timeout``). The headers have ``ttfb_s`` from the request, not from their first byte.
6. Only ``host/path`` goes to the journal, the panel and ``probe-results.json`` (``shown``) — no query, no fragment.
7. «Проверить ссылку» — ``LinkLimiter``: once in 30 s per session, 20 times an hour per node.

The client is stdlib: ``ssl.create_default_context()`` (the certificate is always checked), a GET by hand,
``Accept-Encoding: identity``, ``User-Agent: Kaskad-speedtest``, ``recv_into`` a 256 KB buffer.
Failures are ``FetchError(code, **args)`` — and only them: an error of the socket, of TLS or of a broken
``Location`` becomes one too (``read``, ``timeout``, ``url``). The codes have texts ``gw_probe.why.<code>`` (RU and EN).
``rx`` of a result (and of a FetchError) — every byte received through the exit for it: DoH, headers, bodies.
"""

import errno
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import select
import socket
import ssl
import struct
import time
from urllib.parse import urljoin, urlsplit, quote

from ... import paths, util

MB = 1000 * 1000
MAX_MB = 50                          # the ceiling of one download (§5.4 п. 5)
MAX_S = 30
CONNECT_S = 5
TTFB_S = 10
HEADERS_MAX = 16 * 1024
REDIRECTS = 3
BUF = 256 * 1024                     # recv_into(bytearray(256 KB)) (§5.3)
WARMUP_S = 1.0                       # the speed counts the bytes after one second of the download (§5.3)
USER_AGENT = "Kaskad-speedtest"
DOH_IP = "1.1.1.1"
DOH_PATH = "/dns-query"
DOH_MAX = 64 * 1024
BAD_PORTS = (25, 53)
BLOCKED = tuple(ipaddress.ip_network(n) for n in
                ("0.0.0.0/8", "100.64.0.0/10", "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4"))
PROBE_PORT_BASE = 12700              # SOCKS of probe-<id>: 127.0.0.1:12700+N (§5.2; rendered by T5 — xray.py)
POOL_SOCKS_PORT = 12699              # pool-socks -> gw-all (§5.2)
SO_MARK = getattr(socket, "SO_MARK", 36)
_HOST = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
                   r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$")

_mono = time.monotonic               # tests replace it


class FetchError(Exception):
    """A refused or failed fetch: ``code`` (the key ``gw_probe.why.<code>``) and its arguments."""

    def __init__(self, code, **args):
        Exception.__init__(self, code)
        self.code = code
        self.args_ = args

    def text(self, lang=None):
        return why_text(self.code, self.args_, lang)


def why_text(code, args=None, lang=None):
    """The text of a failure ``gw_probe.why.<code>`` in words: the reply of the SOCKS inbound (``rep``) too."""
    from ...i18n import tr
    a = dict(args or {})
    if code == "socks" and "rep" in a:
        rep = a["rep"]
        a["rep"] = (tr("gw_probe.socks." + rep, lang) if isinstance(rep, str)
                    else tr("gw_probe.socks.code", lang, n=rep))
    return tr("gw_probe.why." + str(code), lang, **a) if a else tr("gw_probe.why." + str(code), lang)


# ── limits and the guard ───────────────────────────────────────────────────

def limits(max_mb=1.0, max_s=10.0, connect_s=CONNECT_S, ttfb_s=TTFB_S, headers=HEADERS_MAX):
    """The limits of one fetch, clamped to the ceilings of §5.4 п. 5."""
    def num(v, lo, hi, default):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return default
        return default if v != v else min(max(v, lo), hi)
    return {"max_bytes": int(num(max_mb, 0.001, MAX_MB, 1.0) * MB), "max_s": num(max_s, 0.5, MAX_S, 10.0),
            "connect_s": num(connect_s, 0.5, CONNECT_S, CONNECT_S), "ttfb_s": num(ttfb_s, 0.5, TTFB_S, TTFB_S),
            "headers": int(num(headers, 1024, HEADERS_MAX, HEADERS_MAX))}


def total_s(lim):
    """The longest one ``safe_get`` with ``lim`` may take — the one deadline of the whole request (§5.4 п. 5)."""
    return lim["connect_s"] + lim["ttfb_s"] + lim["max_s"]


def _left(lim, own):
    """``own`` seconds, never past the deadline of the request; FetchError ``timeout`` when nothing is left."""
    d = lim.get("deadline") if lim else None
    if d is None:
        return own
    left = d - _mono()
    if left <= 0:
        raise FetchError("timeout")
    return min(own, left)


def _count(lim, n):
    m = lim.get("meter") if lim else None
    if m is not None:
        m["rx"] += n


class Guard(object):
    """What a fetch may not reach: ports (25, 53 and the node's own), addresses (not global, the ranges of §5.4,
    the node's addresses, ``gw_nets``)."""

    def __init__(self, node_addrs=(), gw_nets=(), ports=()):
        self.nets = list(BLOCKED)
        for n in list(node_addrs) + list(gw_nets):
            try:
                self.nets.append(ipaddress.ip_network(str(n), strict=False))
            except ValueError:
                pass
        self.ports = set(BAD_PORTS) | set(int(p) for p in ports if isinstance(p, int))

    def url(self, url):
        """-> (host, port, path with query). FetchError: scheme, userinfo, host, port."""
        if not isinstance(url, str) or len(url) > 2048 or any(c in url for c in "\r\n\t "):
            raise FetchError("url")
        try:
            u = urlsplit(url)
            port = u.port
        except ValueError:
            raise FetchError("url")
        if u.scheme.lower() != "https":
            raise FetchError("scheme")
        if u.username is not None or u.password is not None or "@" in u.netloc:
            raise FetchError("userinfo")
        host = (u.hostname or "").rstrip(".").lower()
        if not host:
            raise FetchError("host")
        if ":" in host:
            raise FetchError("ipv6")                    # the paths through the exits are IPv4 (marks, SOCKS ATYP=1)
        if not _is_ipv4(host) and (not _HOST.match(host) or "." not in host):
            raise FetchError("host")
        port = 443 if port is None else port
        if not 0 < port < 65536 or port in self.ports:
            raise FetchError("port", port=port)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        return host, port, quote(path, safe="/?&=%:@!$'()*+,;~-._")

    def addr(self, a):
        """FetchError ``addr`` unless ``a`` is a global IPv4 outside the blocked ranges."""
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            raise FetchError("addr", addr=str(a)[:64])
        if ip.version != 4 or not ip.is_global or any(ip in n for n in self.nets):
            raise FetchError("addr", addr=str(ip))
        return str(ip)


def _is_ipv4(host):
    try:
        return ipaddress.ip_address(host).version == 4
    except ValueError:
        return False


def guard_for(st, runner, pool_exits=None):
    """The guard of this node: its global addresses (``net.node_addresses``), the base of the gateway, the ports of
    xray (``internal_ports``) and of the SOCKS inbounds of the probes. A part that cannot be read is left out — the
    fixed rules (not global, the ranges, 25/53) hold anyway."""
    from .. import addr, net, xray
    node, nets, ports = [], [], set(range(POOL_SOCKS_PORT, PROBE_PORT_BASE + 256))
    try:
        node = net.node_addresses(runner, st)
    except Exception:                                   # noqa: BLE001 — a guard never fails the run
        pass
    try:
        nets = [str(addr.base(st))]
    except Exception:                                   # noqa: BLE001
        pass
    try:
        ports |= set(p for pr, p, _ in xray.internal_ports(st, pool_exits, runner) if pr == "tcp")
    except Exception:                                   # noqa: BLE001
        pass
    return Guard(node, nets, ports)


def shown(url):
    """``host/path`` of a URL — what the journal, the panel and the results may hold (§5.4 п. 6)."""
    try:
        u = urlsplit(str(url))
        host = (u.hostname or "").lower()
        return (host + (u.path or "/"))[:200] if host else "?"
    except ValueError:
        return "?"


# ── the way through an exit ────────────────────────────────────────────────

class Via(object):
    """How a socket reaches the internet: ``awg`` — the mark 256+N of the leg; ``xray`` — SOCKS5 of ``probe-<id>``
    (127.0.0.1:12700+N); ``direct`` — the node's own path (the direct check of the country, §4.3)."""

    def __init__(self, kind, mark=None, port=None):
        self.kind, self.mark, self.port = kind, mark, port

    def __repr__(self):
        return "Via(%s, %s)" % (self.kind, self.mark if self.kind == "awg" else self.port)

    def connect(self, ip, port, timeout, deadline=None):
        """A TCP socket connected to ``ip:port`` this way (timeout ``timeout`` s for the connection, the SOCKS
        handshake never past ``deadline``)."""
        if self.kind == "xray":
            return _socks(self.port, ip, port, timeout, deadline)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if self.kind == "awg":
                try:
                    s.setsockopt(socket.SOL_SOCKET, SO_MARK, int(self.mark))
                except (OSError, TypeError, ValueError):
                    raise FetchError("mark")
            s.settimeout(timeout)
            s.connect((ip, port))
        except FetchError:
            drop(s)
            raise
        except socket.timeout:
            drop(s)
            raise FetchError("connect", what="timeout")
        except OSError as e:
            drop(s)
            raise FetchError("connect", what=errno.errorcode.get(e.errno, str(e.errno)))
        return s


def via_of(e):
    """Via of a row of ``exits.pool`` (None — a kind without a way: «bad»)."""
    from .. import exits
    if e.get("kind") == "awg" and e.get("mark"):
        return Via("awg", mark=int(e["mark"]))
    if exits.is_xray(e):
        return Via("xray", port=PROBE_PORT_BASE + int(e["index"]))
    return None


def _recv_exact(s, n, deadline=None):
    out = b""
    while len(out) < n:
        if deadline is not None:
            left = deadline - _mono()
            if left <= 0:
                raise FetchError("timeout")
            s.settimeout(min(s.gettimeout() or left, left))
        got = s.recv(n - len(out))
        if not got:
            raise FetchError("socks", rep="eof")
        out += got
    return out


def _socks(port, ip, dport, timeout, deadline=None):
    """SOCKS5 without a password to 127.0.0.1:``port``, CONNECT ATYP=IPv4 ``ip:dport`` (§5.4 п. 3)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", int(port)))
        except OSError:
            raise FetchError("socks", rep="no_inbound")
        s.sendall(b"\x05\x01\x00")
        if _recv_exact(s, 2, deadline) != b"\x05\x00":
            raise FetchError("socks", rep="auth")
        s.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(ip) + struct.pack("!H", dport))
        head = _recv_exact(s, 4, deadline)
        if head[0] != 5 or head[1] != 0:
            raise FetchError("socks", rep=head[1])
        _recv_exact(s, {1: 6, 4: 18}.get(head[3], 0) or (ord(_recv_exact(s, 1, deadline)) + 2), deadline)
    except FetchError:
        drop(s)
        raise
    except socket.timeout:
        drop(s)
        raise FetchError("connect", what="timeout")
    except OSError as e:
        drop(s)
        raise FetchError("connect", what=errno.errorcode.get(e.errno, str(e.errno)))
    return s


def drop(s):
    """Close with a reset (SO_LINGER 0): the download stops at once, nothing is sent through the exit later."""
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    except (OSError, ValueError, AttributeError):
        pass
    try:
        s.close()
    except (OSError, AttributeError):
        pass


def open_tls(via, ip, port, host, lim):
    """-> (TLS socket to ``ip:port`` with SNI ``host``, ms of the TLS handshake). The certificate is checked.

    The handshake runs without blocking until ``ttfb_s`` from its start or the deadline of the request, whichever comes
    first: a server that trickles its handshake byte by byte is cut there too (a blocking socket would give every read
    the whole timeout again)."""
    raw = via.connect(ip, port, _left(lim, lim["connect_s"]), lim.get("deadline"))
    t0 = _mono()
    end = t0 + lim["ttfb_s"]
    if lim.get("deadline") is not None:
        end = min(end, lim["deadline"])
    s = raw
    try:
        raw.settimeout(0.0)
        ctx = ssl.create_default_context()
        s = ctx.wrap_socket(raw, server_hostname=host, do_handshake_on_connect=False)
        while True:
            left = end - _mono()
            if left <= 0:
                raise FetchError("timeout")
            try:
                s.do_handshake()
                break
            except ssl.SSLWantReadError:
                select.select([s], [], [], left)
            except ssl.SSLWantWriteError:
                select.select([], [s], [], left)
        s.settimeout(_left(lim, lim["ttfb_s"]))
    except FetchError:
        drop(s)
        raise
    except ssl.SSLCertVerificationError:
        drop(s)
        raise FetchError("cert")
    except (ssl.SSLError, OSError, ValueError) as e:
        drop(s)
        raise FetchError("tls", what=type(e).__name__)
    return s, (_mono() - t0) * 1000.0


# ── HTTP by hand ───────────────────────────────────────────────────────────

def _request(s, host, port, path, accept="*/*"):
    h = host if port == 443 else "%s:%d" % (host, port)
    s.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\nAccept: %s\r\nAccept-Encoding: identity\r\n"
               "Connection: close\r\n\r\n" % (path, h, USER_AGENT, accept)).encode("ascii"))


def _head_left(lim, until):
    """Seconds left for the headers: ``ttfb`` past ``until``, ``timeout`` past the deadline of the request."""
    now = _mono()
    d = lim.get("deadline")
    if d is not None and now >= d:
        raise FetchError("timeout")
    if now >= until:
        raise FetchError("ttfb")
    return min(until, d) - now if d is not None else until - now


def _head(s, lim, started):
    """-> (status, headers {lower: value}, the bytes of the body already read, ms to the first byte).

    All the headers within ``ttfb_s`` of the request (§5.4 п. 5): a server that sends them byte by byte is cut too."""
    buf = bytearray(BUF)
    data = b""
    first = None
    until = started + lim["ttfb_s"]
    while b"\r\n\r\n" not in data:
        s.settimeout(_head_left(lim, until))
        try:
            n = s.recv_into(buf)
        except socket.timeout:
            _head_left(lim, until)
            raise FetchError("ttfb")
        except (ssl.SSLError, OSError) as e:
            raise FetchError("read", what=type(e).__name__)
        if not n:
            raise FetchError("http", what="eof")
        _count(lim, n)
        if first is None:
            first = _mono()
        data += bytes(buf[:n])
        if len(data.split(b"\r\n\r\n", 1)[0]) > lim["headers"]:
            raise FetchError("headers")
    head, rest = data.split(b"\r\n\r\n", 1)
    if len(head) > lim["headers"]:
        raise FetchError("headers")
    lines = head.decode("iso-8859-1").split("\r\n")
    m = re.match(r"^HTTP/1\.[01] (\d{3})", lines[0])
    if not m:
        raise FetchError("http", what="status")
    hdr = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            hdr[k.strip().lower()] = v.strip()
    return int(m.group(1)), hdr, rest, (first - started) * 1000.0


def _dechunk(data):
    """The body of ``Transfer-Encoding: chunked`` (what was read of it)."""
    out, i = b"", 0
    while True:
        j = data.find(b"\r\n", i)
        if j < 0:
            return out
        try:
            n = int(data[i:j].split(b";")[0].strip() or b"0", 16)
        except ValueError:
            return out
        if n == 0:
            return out
        out += data[j + 2:j + 2 + n]
        i = j + 2 + n + 2


def _body(s, lim, rest, hdr, keep):
    """Read the body within the limits. -> {bytes, kept, stopped (None | "max_bytes" | "max_s"), t_first, samples}."""
    buf = bytearray(BUF)
    total = len(rest)
    kept = rest[:keep] if keep else b""
    clen = None
    try:
        clen = int(hdr.get("content-length")) if "content-length" in hdr else None
    except ValueError:
        clen = None
    t_first = _mono()
    deadline = t_first + lim["max_s"]
    if lim.get("deadline") is not None:
        deadline = min(deadline, lim["deadline"])
    warm = None                                         # (time, bytes) one second into the download
    stopped = None
    while True:
        if warm is None and _mono() - t_first >= WARMUP_S:
            warm = (_mono(), total)
        if clen is not None and total >= clen:
            break
        if total >= lim["max_bytes"]:
            stopped = "max_bytes"
            break
        left = deadline - _mono()
        if left <= 0:
            stopped = "max_s"
            break
        s.settimeout(min(left, lim["ttfb_s"]))
        try:
            n = s.recv_into(buf, min(BUF, lim["max_bytes"] - total))
        except socket.timeout:
            stopped = "max_s" if _mono() >= deadline else "stall"
            break
        except (ssl.SSLError, OSError):
            break                                       # a reset by the far end: what came is what we have
        if not n:
            break
        _count(lim, n)
        total += n
        if keep and len(kept) < keep:
            kept += bytes(buf[:min(n, keep - len(kept))])
    end = _mono()
    if hdr.get("transfer-encoding", "").lower() == "chunked" and kept:
        kept = _dechunk(kept)
    return {"bytes": total, "kept": kept, "stopped": stopped, "t_first": t_first, "end": end, "warm": warm}


def _mbps(b):
    """Mbit/s after the warm-up (§5.3); a download shorter than it — over the whole of it (``short``)."""
    if b["warm"] and b["end"] - b["warm"][0] >= 0.5:
        return (b["bytes"] - b["warm"][1]) * 8.0 / (b["end"] - b["warm"][0]) / 1e6, False
    dt = b["end"] - b["t_first"]
    return (b["bytes"] * 8.0 / dt / 1e6 if dt > 0 and b["bytes"] else None), True


# ── the resolver through the exit ──────────────────────────────────────────

def resolve(host, via, guard, lim=None):
    """[IPv4] of ``host`` by DoH (``https://1.1.1.1/dns-query``, the JSON form) through ``via``; every address is
    checked by the guard (one bad — FetchError ``addr``). An IP literal is checked as it is."""
    if _is_ipv4(host):
        return [guard.addr(host)]
    lim = lim or limits()
    s, _ = open_tls(via, DOH_IP, 443, DOH_IP, lim)
    try:
        started = _mono()
        s.settimeout(_left(lim, lim["ttfb_s"]))
        _request(s, DOH_IP, 443, "%s?name=%s&type=A" % (DOH_PATH, quote(host, safe=".-")), "application/dns-json")
        status, hdr, rest, _ = _head(s, lim, started)
        body = _body(s, dict(lim, max_bytes=DOH_MAX, max_s=min(lim["ttfb_s"], 10.0)), rest, hdr, DOH_MAX)
    finally:
        drop(s)
    if status != 200:
        raise FetchError("dns", what="http %d" % status)
    try:
        doc = json.loads(body["kept"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise FetchError("dns", what="json")
    if not isinstance(doc, dict) or doc.get("Status") != 0:
        raise FetchError("dns", what="rcode %s" % (doc.get("Status") if isinstance(doc, dict) else "?"))
    ips = [a.get("data") for a in doc.get("Answer") or [] if isinstance(a, dict) and a.get("type") == 1]
    if not ips:
        raise FetchError("dns", what="no A")
    return [guard.addr(a) for a in ips]


# ── safe_get ───────────────────────────────────────────────────────────────

def safe_get(url, via, lim=None, guard=None, keep=0, deadline=None):
    """GET ``url`` through ``via`` (§5.4). -> {"status", "shown", "ip", "bytes", "rx", "ttfb_ms", "tls_ms", "mbps",
    "short", "ms", "ms_all", "stopped", "redirects", "body" (the first ``keep`` bytes)}.

    ``bytes`` — of the body; ``rx`` — everything received through the exit (DoH, headers, bodies of every hop);
    ``ms`` — the time of the hops to the target (the connection, TLS, the request, the body; redirects too) without
    the resolver; ``ms_all`` — with it. One deadline for all of it (``total_s``; never past ``deadline``, a monotonic
    time of the caller). FetchError when refused or failed — never another exception (``e.rx`` — what came)."""
    lim = dict(lim or limits())
    guard = guard or Guard()
    started_all = _mono()
    lim["deadline"] = started_all + total_s(lim)
    if deadline is not None:
        lim["deadline"] = min(lim["deadline"], deadline)
    meter = lim["meter"] = {"rx": 0}
    try:
        return _safe_get(url, via, lim, guard, keep, started_all)
    except FetchError as e:
        e.rx = meter["rx"]
        raise
    except socket.timeout:
        err = FetchError("timeout")
    except (ssl.SSLError, OSError) as e:                # a reset while sending, a broken TLS record…
        err = FetchError("read", what=type(e).__name__)
    except (ValueError, UnicodeError):                  # a broken Location (urljoin / urlsplit)
        err = FetchError("url")
    err.rx = meter["rx"]
    raise err


def _safe_get(url, via, lim, guard, keep, started_all):
    redirects = 0
    net = 0.0
    while True:
        host, port, path = guard.url(url)
        ip = resolve(host, via, guard, lim)[0]          # every address checked; we connect to the checked one
        hop = _mono()
        s, tls_ms = open_tls(via, ip, port, host, lim)
        try:
            started = _mono()
            s.settimeout(_left(lim, lim["ttfb_s"]))
            _request(s, host, port, path)
            status, hdr, rest, ttfb = _head(s, lim, started)
            if 300 <= status < 400 and hdr.get("location"):
                redirects += 1
                if redirects > REDIRECTS:
                    raise FetchError("redirects", n=REDIRECTS)
                url = urljoin(url, hdr["location"])
                if urlsplit(url).scheme.lower() != "https":
                    raise FetchError("downgrade")
                net += _mono() - hop
                continue
            b = _body(s, lim, rest, hdr, keep)
        finally:
            drop(s)
        net += b["end"] - hop
        mbps, short = _mbps(b)
        return {"status": status, "shown": shown(url), "ip": ip, "bytes": b["bytes"], "rx": lim["meter"]["rx"],
                "ttfb_ms": round(ttfb, 1), "tls_ms": round(tls_ms, 1), "mbps": None if mbps is None else round(mbps, 2),
                "short": short, "ms": round(net * 1000.0, 1), "ms_all": round((b["end"] - started_all) * 1000.0, 1),
                "stopped": b["stopped"], "redirects": redirects, "body": b["kept"]}


# ── «Проверить ссылку»: how often ───────────────────────────────────────────

LINK_SESSION_S = 30
LINK_HOUR_MAX = 20


class LinkLimiter(object):
    """«Проверить ссылку» at most once in 30 s per session and 20 times an hour per node (§5.4 п. 7). The record
    lives in /run (a reboot forgets it); the session is kept as a hash, never as the cookie."""

    def __init__(self, path=None):
        self.path = path or paths.P("/run/kaskad/probe-link.json")

    def allow(self, session, now=None):
        """-> (True, None, 0) and the try counted, or (False, code, seconds to wait): ``rate_session`` | ``rate_node``."""
        now = time.time() if now is None else now
        sid = hashlib.sha256(str(session).encode("utf-8")).hexdigest()[:16]
        if not os.path.isdir(os.path.dirname(self.path)):
            util.ensure_run_dir()
        fd = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                doc = json.loads(util.read_text(self.path) or "{}")
            except ValueError:
                doc = {}
            doc = doc if isinstance(doc, dict) else {}
            node = [t for t in doc.get("node") or [] if isinstance(t, (int, float)) and now - 3600 < t <= now + 60]
            ses = dict((k, v) for k, v in (doc.get("sessions") or {}).items()
                       if isinstance(v, (int, float)) and now - LINK_SESSION_S < v <= now + 60)
            if sid in ses:
                return False, "rate_session", int(ses[sid] + LINK_SESSION_S - now) + 1
            if len(node) >= LINK_HOUR_MAX:
                return False, "rate_node", int(min(node) + 3600 - now) + 1
            node.append(now)
            ses[sid] = now
            util.atomic_write(self.path, util.json_text({"node": node, "sessions": ses}), 0o600)
            return True, None, 0
        finally:
            os.close(fd)
