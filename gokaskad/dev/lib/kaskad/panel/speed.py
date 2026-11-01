"""The browser test of the path «your device → the server» (k3/handoff/SPEC-browser-channel.md §4.3–§4.7, §5): the logic
without HTTP. The routes are in ``panel/host.py``; the server gives ``speed/up`` its body unread (``routes.route(…,
body="stream")``) and writes ``speed/down`` itself (``routes.RawStream``).

    limits(st)                -> (limits, notes)   §5.1: strict ints, clamps, defaults; ``gateway.hostmon.speed``
    up_rate(buckets, …)       -> {bps, window_ms, buckets} | None   §4.4: the upload by the server's clock
    via_of(listener, fronts)  -> "direct" | "proxy" | "front" | "tunnel"   §4.7
    via_vpn(ip, own, cascade, runner) -> "self" | "cascade" | False | None   §4.7
    cpu_limited(a, b)         -> bool | None       §4.5: Δthrottled_usec > 10 % of the side
    Speed                     the one lease of the node (NS-B2), the reserve of bytes (§4.3), the counter of the day
    send_down / recv_up       the loops of one ``down`` / ``up`` request

Time is a parameter everywhere (``now`` — unix seconds, ``mono`` — monotonic seconds, ``clock`` — a callable): the
tests move it. The lease is bound to the session: ``sha256(token)`` of the cookie, or the constant ``"tunnel"`` for the
tunnel without a password (one «session» of every loopback client). Nothing here keeps an address or a result on disk:
the counter file has the bytes and the count of tests per day and the times of the last starts (NS-B9).
"""

import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets as _random
import threading
import time

from .. import paths, util

# ── limits (§5) ────────────────────────────────────────────────────────────
DEFAULTS = {"tests_hour": 6, "tests_day": 30, "side_bytes": 800 * 1000 * 1000, "day_bytes": 10 * 1000 ** 3}
CLAMPS = {"tests_hour": (1, 12), "tests_day": (1, 100), "side_bytes": (50 * 1000 * 1000, 2 * 1000 ** 3),
          "day_bytes": (100 * 1000 * 1000, 50 * 1000 ** 3)}
REQ_DOWN_MAX = 256 << 20        # bytes of one ``down`` (413 above)
REQ_UP_MAX = 64 << 20           # Content-Length of one ``up`` (413 before the body)
STREAMS_MAX = 4                 # open requests of one side at once (429 streams)
LEASE_S = 45                    # a lease lives at most this long from its start
SIDE_S_MAX = 10                 # a side of the test (the client keeps it)
STREAM_S = 15                   # one ``down``/``up`` request is cut after this
IDLE_S = 10                     # no open request and no new one this long — the lease ends
DAY_MIN_LEFT = 50 * 1000 * 1000     # less left of the day — 429 traffic
BUCKET_MS = 100                 # the buckets of the received bytes (§4.4)
BUCKETS_MAX = LEASE_S * 1000 // BUCKET_MS
UP_SKIP_MS = 2000               # the window of the upload starts ≥ 2 s after its first byte …
UP_RAMP_MS = 1000               # … and ≥ 1 s after the most streams were first open
UP_MIN_MS = 2000                # a shorter window — no number
DAYS_KEPT = 35
STARTS_KEPT = 12                # ≥ the biggest tests_hour
REFUNDS_HOUR = 6                # failed starts given back within an hour, at most
FAILED_BYTES = 1000 * 1000      # a lease that moved less than this was a failed start
FINISH_KEEP_S = 60              # a repeated ``finish`` gets the same answer this long
CHUNK = 64 << 10                # one write / one read
BLOCK = 1 << 20                 # the one random block the downloads repeat
CPU_SHARE = 0.10                # throttled longer than this share of a side — «the panel hit its processor limit»
IP_TIMEOUT = 2

ID_RE = re.compile(r"^[0-9a-f]{32}$")
DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

_KEY_SAFE = re.compile(r"[^A-Za-z0-9_]")    # a foreign key name goes to the journal only by these letters (SEC П-1)
_NOTED = [None]                 # the last state of ``gateway.hostmon.speed`` whose fixes went to the journal
_NOTED_LOCK = threading.Lock()


def _strict_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def limits(st):
    """-> (limits, notes) of ``gateway.hostmon.speed`` (§5.1): each value is a strict ``int`` (not bool, not a string,
    not a float) or its default; outside the clamp — the nearest bound. ``notes`` — one word per fixed value ("key:
    default" / "key: clamped" / "key: unknown"); no ``speed`` (an update from 3.3.5) — the defaults, no notes."""
    g = (st or {}).get("gateway") if isinstance(st, dict) else None
    hm = g.get("hostmon") if isinstance(g, dict) else None
    raw = hm.get("speed") if isinstance(hm, dict) else None
    out = dict(DEFAULTS)
    notes = []
    if raw is None:
        return out, notes
    if not isinstance(raw, dict):
        return out, ["speed: default"]
    for k in sorted(raw):
        if k not in DEFAULTS:
            notes.append("%s: unknown" % (_KEY_SAFE.sub("", str(k))[:40] or "?"))
    for k, dv in sorted(DEFAULTS.items()):
        if k not in raw:
            continue
        v = raw[k]
        if not _strict_int(v):
            notes.append("%s: default" % k)
            continue
        lo, hi = CLAMPS[k]
        if v < lo or v > hi:
            out[k] = lo if v < lo else hi
            notes.append("%s: clamped" % k)
        else:
            out[k] = v
    return out, notes


def note_limits(st, notes, log=None):
    """One journal line per change of ``gateway.hostmon.speed`` with fixed values (not one per request)."""
    if not notes:
        return False
    try:
        raw = st["gateway"]["hostmon"]["speed"]
        sig = json.dumps(raw, sort_keys=True, default=str)[:2000]
    except Exception:                                # noqa: BLE001 — any odd state: a mark of its shape
        sig = repr(type(st))
    with _NOTED_LOCK:
        if _NOTED[0] == sig:
            return False
        _NOTED[0] = sig
    (log or _log)("panel.speed.log.limits", notes=", ".join(notes))
    return True


def _log(key, **kw):
    try:
        util.log_tr(key, **kw)
    except Exception:                                # noqa: BLE001 — the journal never stops a test
        pass


# ── the upload by the server's clock (§4.4) ────────────────────────────────

def up_rate(buckets, up_first, up_ramp, up_last):
    """The speed of the upload from the received bytes in 100 ms buckets (times — ms since the lease start): the window
    starts at the first whole bucket after ``max(up_first + 2000, up_ramp + 1000)`` and ends at the last whole bucket
    before ``up_last`` (the last, partial one is dropped). ``bps = 8 × bytes / seconds``; a window shorter than 2 s —
    ``bps: None``. No upload at all — None."""
    if up_first is None or up_last is None or not buckets:
        return None
    start = max(up_first + UP_SKIP_MS, (up_ramp if up_ramp is not None else up_first) + UP_RAMP_MS)
    k0 = int(math.ceil(start / float(BUCKET_MS)))
    k1 = min(int(up_last // BUCKET_MS) - 1, len(buckets) - 1)
    n = k1 - k0 + 1
    if n <= 0:
        return {"bps": None, "window_ms": 0, "buckets": []}
    win = [int(b) for b in buckets[k0:k1 + 1]]
    window_ms = n * BUCKET_MS
    bps = int(round(8.0 * sum(win) * 1000.0 / window_ms)) if window_ms >= UP_MIN_MS else None
    return {"bps": bps, "window_ms": window_ms, "buckets": win}


# ── the processor of the panel (§4.5) ─────────────────────────────────────

def parse_cgroup(text):
    """The cgroup v2 path of /proc/self/cgroup (the line «0::/…») or None (cgroup v1, a container without it)."""
    for line in (text or "").splitlines():
        if line.startswith("0::"):
            p = line[3:].strip()
            if p.startswith("/") and ".." not in p.split("/"):
                return p
    return None


def parse_cpu_stat(text):
    """``throttled_usec`` of a cpu.stat or None."""
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "throttled_usec" and parts[1].isdigit():
            return int(parts[1])
    return None


def cpu_throttled():
    """``throttled_usec`` of the panel's own cgroup (cgroup v2) now, or None (no file — v1, a container)."""
    cg = parse_cgroup(util.read_text(paths.P("/proc/self/cgroup")))
    if cg is None:
        return None
    return parse_cpu_stat(util.read_text(paths.P("/sys/fs/cgroup" + cg.rstrip("/") + "/cpu.stat")))


def cpu_limited(a, b):
    """Snapshots ``(mono, throttled_usec)`` at the start and at the end of a side -> the panel stood in its quota
    longer than 10 % of the side; None — a snapshot is missing (the side was not there, no cgroup v2)."""
    if not a or not b or a[1] is None or b[1] is None:
        return None
    dur = (b[0] - a[0]) * 1e6
    if dur <= 0:
        return None
    return (b[1] - a[1]) > CPU_SHARE * dur


# ── how it was measured (§4.7) ─────────────────────────────────────────────

def via_of(listener, fronts):
    """The way into the panel: the loopback listener — the SSH tunnel; behind nginx — "front" when xray of the gateway
    holds 443 (``fronts()`` — site.front.xray_fronts), else "proxy"; the open mode — "direct"."""
    if listener == "local":
        return "tunnel"
    if listener == "proxy":
        try:
            return "front" if fronts() else "proxy"
        except Exception:                            # noqa: BLE001 — not known: the plain nginx
            return "proxy"
    return "direct"


def _addr(v):
    """An address of ``v`` (an IPv4-mapped IPv6 — its IPv4), or None: not an address, or a scoped one («%»)."""
    v = str(v).strip().strip("[]")
    if "%" in v:
        return None
    try:
        a = ipaddress.ip_address(v)
    except ValueError:
        return None
    if a.version == 6 and a.ipv4_mapped is not None:
        return a.ipv4_mapped
    return a


def _route_json(runner, argv):
    """The list of an ``ip -j …`` answer, or None (no answer, an error, not JSON)."""
    try:
        res = runner.run(argv, timeout=IP_TIMEOUT)
    except Exception:                                # noqa: BLE001
        return None
    if not res.ok:
        return None
    try:
        doc = json.loads(res.out or "")
    except ValueError:
        return None
    return doc if isinstance(doc, list) else None


def _devs(routes):
    out = set()
    for r in routes or []:
        if not isinstance(r, dict):
            continue
        if isinstance(r.get("dev"), str):
            out.add(r["dev"])
        for nh in r.get("nexthops") or []:
            if isinstance(nh, dict) and isinstance(nh.get("dev"), str):
                out.add(nh["dev"])
    return out


def via_vpn(client_ip, own=(), cascade=(), runner=None):
    """Did the owner come through a VPN (§4.7)? "self" — through this node: the address is the node's own (``own`` —
    the addresses of the open mode and ``gateway.host``), or the route back to it is ``local`` (an own address, IPv6
    and behind nginx too), or it leaves by another device than the default route of its family (AmneziaWG/VLESS of
    this node, another VPN on it); "cascade" — the address of another node of the cascade (``cascade``); False —
    nothing of that; None — ``ip`` gave no answer. Not an address — None, ``ip`` is not run. ``ip`` gets a list of
    arguments (no shell); the address is never kept."""
    a = _addr(client_ip) if client_ip is not None else None
    if a is None:
        return None
    if any(_addr(x) == a for x in own or ()):
        return "self"
    failed = runner is None and not a.is_loopback
    if not a.is_loopback and runner is not None:
        fam = ["-6"] if a.version == 6 else []
        got = _route_json(runner, ["ip", "-j"] + fam + ["route", "get", str(a)])
        if got is None or not got:
            failed = True
        else:
            first = got[0] if isinstance(got[0], dict) else {}
            if first.get("type") == "local" or first.get("dev") == "lo":
                return "self"
            default = _route_json(runner, ["ip", "-j"] + fam + ["route", "show", "default"])
            devs = _devs(default)
            if default is None or not devs or not isinstance(first.get("dev"), str):
                failed = True
            elif first["dev"] not in devs:
                return "self"
    if any(_addr(x) == a for x in cascade or ()):
        return "cascade"
    return None if failed else False


# ── the counter of the day (§4.3) ──────────────────────────────────────────

def store_path():
    """The counter of the traffic of the tests, in the panel's own directory (not in a .kbk)."""
    return os.path.join(paths.panel_dir(), "speed.json")


def _nonneg(v):
    return v if _strict_int(v) and v >= 0 else None


def _times(v, keep):
    if not isinstance(v, list):
        return None
    out = [float(t) for t in v if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t)]
    return sorted(out)[-keep:]


def parse_counts(raw):
    """-> (doc, broken): ``{"YYYY-MM-DD": {"rx", "tx", "n"}, "starts": [unix …], "refunds": [unix …]}`` with every
    value checked; ``broken`` — the text was not such a file (the counter starts from zero, a line in the journal)."""
    doc = {"starts": [], "refunds": []}
    if raw is None:
        return doc, False
    try:
        obj = json.loads(raw)
    except ValueError:
        return doc, True
    if not isinstance(obj, dict):
        return doc, True
    broken = False
    for k, v in obj.items():
        if k in ("starts", "refunds"):
            t = _times(v, STARTS_KEPT if k == "starts" else REFUNDS_HOUR * 4)
            if t is None:
                broken = True
            else:
                doc[k] = t
        elif isinstance(k, str) and DAY_RE.match(k) and isinstance(v, dict):
            rec = dict((f, _nonneg(v.get(f, 0))) for f in ("rx", "tx", "n"))
            if any(x is None for x in rec.values()):
                broken = True
            else:
                doc[k] = rec
        else:
            broken = True
    return doc, broken


def days_of(doc):
    return sorted(k for k in doc if DAY_RE.match(k))


def _pruned(doc):
    keep = set(days_of(doc)[-DAYS_KEPT:])
    out = dict((k, v) for k, v in doc.items() if k in keep)
    out["starts"] = sorted(doc.get("starts") or [])[-STARTS_KEPT:]
    out["refunds"] = sorted(doc.get("refunds") or [])[-REFUNDS_HOUR * 4:]
    return out


def read_counts(path=None):
    """The counter as it is on disk (``bench.cost.estimate`` of stage C reads the bytes of 30 days from here)."""
    return parse_counts(util.read_text(path or store_path()))[0]


def _to_midnight(now):
    """Seconds to the next midnight of the node (KASKAD_TZ)."""
    from .. import tz
    lt = tz.local(now)
    return max(1, 86400 - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec))


def _day(now):
    from .. import tz
    return tz.day(now)


class Refusal(Exception):
    """A refusal of the test: ``status`` (403, 409, 410, 413, 429, 500), ``kind`` (the word of its text) and the
    data of the answer."""

    def __init__(self, status, kind, data=None):
        Exception.__init__(self, kind)
        self.status = status
        self.kind = kind
        self.data = data


def bind_key(session):
    """The key a lease is bound to (NS-B2): sha256 of the cookie token, or "tunnel" (a session without a token — the
    tunnel without a password)."""
    tok = (session or {}).get("token") if isinstance(session, dict) else None
    if isinstance(tok, str) and tok:
        return hashlib.sha256(tok.encode("utf-8", "ignore")).hexdigest()
    return "tunnel"


class Lease(object):
    """One test (§4.3). Times ``*_ms`` — milliseconds since ``mono0``."""

    def __init__(self, lid, key, now, mono, day, down, up):
        self.id = lid
        self.key = key
        self.wall0 = now
        self.mono0 = mono
        self.day = day
        self.down0, self.up0 = down, up         # the budget of each side, added to the day at the start
        self.left = {"down": down, "up": up}    # not reserved yet
        self.moved = {"down": 0, "up": 0}       # sent / received
        self.open = {"down": 0, "up": 0}
        self.held = {"down": 0, "up": 0}        # reserved by the open requests (given back at their end, once)
        self.last = mono                        # the last request began or ended
        self.closed = None                      # None | "finish" | "time"
        self.buckets = None                     # [bytes] of 100 ms, the upload (lazily, ≤ BUCKETS_MAX)
        self.up_first = self.up_ramp = self.up_last = None
        self.up_most = 0
        self.cpu = {}                           # "down" / "up" / "end" -> (mono, throttled_usec)

    def ms(self, mono):
        return (mono - self.mono0) * 1000.0

    def ends_at(self):
        """The monotonic time this lease ends by itself (45 s, or 10 s of quiet without open requests)."""
        end = self.mono0 + LEASE_S
        if not self.open["down"] and not self.open["up"]:
            end = min(end, self.last + IDLE_S)
        return end


class Speed(object):
    """The one test of the node: ``start`` → ``lease`` (ping/down/up/finish) → the end (``finish``, 45 s, or 10 s of
    quiet). Everything under one lock; the counter file is written at the start (the whole reserve, ``n`` + 1, the
    start time) and at the end (the unused part back; a failed start given back)."""

    def __init__(self, path=None, day=None, to_midnight=None, cpu=None, log=None, block=None):
        self._lock = threading.Lock()
        self._path = path
        self._day = day or _day
        self._to_midnight = to_midnight or _to_midnight
        self._cpu = cpu or cpu_throttled
        self._log = log or _log
        self._lease = None
        self._closed = {}                       # id -> {"key", "mono", "why", "result"}
        self._block = block

    def path(self):
        return self._path or store_path()

    # ── the counter file ──
    def _load(self):
        doc, broken = parse_counts(util.read_text(self.path()))
        if broken:
            self._log("panel.speed.log.counterBroken")
        return doc

    def _save(self, doc):
        util.atomic_write(self.path(), json.dumps(_pruned(doc), sort_keys=True) + "\n", 0o600)

    # ── the lease ──
    def _reap(self, now, mono):
        L = self._lease
        if L is not None and mono >= L.ends_at():
            self._close(L, "time", now, mono)
        for lid in [k for k, c in self._closed.items() if mono - c["mono"] > FINISH_KEEP_S]:
            self._closed.pop(lid, None)

    def start(self, key, lim, now, mono):
        """A new lease for ``key`` with the limits ``lim`` (``limits()``) -> the body of the answer of ``start``.
        Refusal 409 busy, 429 hour | day | traffic (with ``limit``, ``used``, ``retry_after``), 500 store."""
        with self._lock:
            self._reap(now, mono)
            L = self._lease
            if L is not None:
                raise Refusal(409, "busy", {"busy": True,
                                            "retry_after": max(1, int(math.ceil(L.ends_at() - mono)))})
            doc = self._load()
            day = self._day(now)
            rec = dict(doc.get(day) or {"rx": 0, "tx": 0, "n": 0})
            hour = sorted(t for t in doc["starts"] if t > now - 3600)
            if len(hour) >= lim["tests_hour"]:
                oldest = hour[len(hour) - lim["tests_hour"]]
                raise Refusal(429, "hour", {"reason": "hour", "limit": lim["tests_hour"], "used": len(hour),
                                            "retry_after": max(1, int(math.ceil(oldest + 3600 - now)))})
            if rec["n"] >= lim["tests_day"]:
                raise Refusal(429, "day", {"reason": "day", "limit": lim["tests_day"], "used": rec["n"],
                                           "retry_after": self._to_midnight(now)})
            today = rec["rx"] + rec["tx"]
            day_left = lim["day_bytes"] - today
            if day_left < DAY_MIN_LEFT:
                raise Refusal(429, "traffic", {"reason": "traffic", "limit": lim["day_bytes"], "used": today,
                                               "retry_after": self._to_midnight(now)})
            side = min(lim["side_bytes"], day_left // 2)
            rec["tx"] += side
            rec["rx"] += side
            rec["n"] += 1
            doc[day] = rec
            stamp = round(float(now), 3)
            doc["starts"] = sorted(doc["starts"] + [stamp])
            try:
                self._save(doc)
            except OSError as e:
                self._log("panel.speed.log.counterUnsaved", err=e)
                raise Refusal(500, "store")
            L = Lease(_random.token_hex(16), key, stamp, mono, day, side, side)
            self._lease = L
            return {"ok": True, "id": L.id, "until_s": LEASE_S,
                    "limits": {"down_bytes": side, "up_bytes": side, "req_down_max": REQ_DOWN_MAX,
                               "req_up_max": REQ_UP_MAX, "streams_max": STREAMS_MAX, "side_s_max": SIDE_S_MAX},
                    "left_day_bytes": max(0, day_left - 2 * side),
                    "tests_left_hour": max(0, lim["tests_hour"] - len(hour) - 1),
                    "tests_left_day": max(0, lim["tests_day"] - rec["n"])}

    def _gone(self, key, lid):
        c = self._closed.get(lid) if isinstance(lid, str) else None
        if c is not None and hmac.compare_digest(c["key"], key) and c["why"] == "time":
            return Refusal(410, "expired", {"expired": True})
        return Refusal(403, "lease")

    def lease(self, key, lid, now, mono):
        """The live lease ``lid`` of ``key`` (a request of it — the lease is not quiet), else Refusal 403 (no such
        lease of this key) or 410 (it ended by time)."""
        if not isinstance(lid, str) or not ID_RE.match(lid):
            raise Refusal(403, "lease")
        with self._lock:
            self._reap(now, mono)
            L = self._lease
            if L is not None and hmac.compare_digest(L.id, lid) and hmac.compare_digest(L.key, key):
                L.last = mono
                return L
            raise self._gone(key, lid)

    def take(self, L, side, n, now, mono):
        """Reserve ``n`` bytes of ``side`` for one request (before its first byte): 429 streams — ``streams_max``
        requests of the side are open; 429 budget — ``n`` is more than is left (``left``)."""
        with self._lock:
            self._reap(now, mono)
            if L.closed is not None or self._lease is not L:
                raise self._gone(L.key, L.id)
            if L.open[side] >= STREAMS_MAX:
                raise Refusal(429, "streams", {"reason": "streams"})
            if n > L.left[side]:
                raise Refusal(429, "budget", {"reason": "budget", "left": L.left[side]})
            L.left[side] -= n
            L.open[side] += 1
            L.held[side] += n
            L.last = mono
            if side == "up" and L.open["up"] > L.up_most:
                L.up_most = L.open["up"]
                L.up_ramp = L.ms(mono)
            if side not in L.cpu:
                L.cpu[side] = (mono, self._cpu())

    def moved(self, L, side, n):
        """``n`` more bytes of ``side`` went through (the downloads: once in 1 MiB)."""
        with self._lock:
            L.moved[side] += n

    def got(self, L, k, n, first_mono, last_mono):
        """``n`` received bytes of the upload in the bucket ``k`` (the first of them came at ``first_mono``, the last
        at ``last_mono``)."""
        with self._lock:
            L.moved["up"] += n
            if L.buckets is None:
                L.buckets = [0] * BUCKETS_MAX
            if 0 <= k < BUCKETS_MAX:
                L.buckets[k] += n
            f, l = L.ms(first_mono), L.ms(last_mono)
            if L.up_first is None or f < L.up_first:
                L.up_first = f
            if L.up_last is None or l > L.up_last:
                L.up_last = l

    def release(self, L, side, reserved, used, now, mono):
        """The request ended: what it did not move goes back — to the side while the lease lives, else to its day in
        the counter file (§4.3, §6.2 п. 3: ``finish`` may come before the server sees the client cut its requests).
        Exactly one call per request ``take`` reserved (``send_down``/``recv_up`` call it once, in ``finally``): the
        side is not told which request ends. What goes back is capped by what the side still holds (never more than
        was reserved for it in all), and a call while the side has no open request gives back nothing."""
        with self._lock:
            if L.open[side] <= 0:                   # a repeat: this side has no open request
                return
            L.open[side] -= 1
            L.last = mono
            reserved = min(max(0, reserved), L.held[side])
            back = max(0, reserved - max(0, used))
            L.held[side] -= reserved
            if L.closed is None:
                L.left[side] += back
            elif back:
                self._refund(L, side, back)
            self._reap(now, mono)

    def _refund(self, L, side, n):
        """``n`` bytes of a request that ended after its lease go back to the day of the lease (under the lock)."""
        try:
            doc = self._load()
            rec = doc.get(L.day)
            if rec is None:
                return
            f = "tx" if side == "down" else "rx"
            rec[f] = max(0, rec[f] - n)
            self._save(doc)
        except OSError as e:
            self._log("panel.speed.log.counterUnsaved", err=e)

    def finish(self, key, lid, now, mono):
        """The end of the lease by the client -> {ok, bytes_down, bytes_up, up, cpu_limited}; the same answer to a
        repeat within 60 s; 410 — it ended by time; 403 — no such lease of this key."""
        if not isinstance(lid, str) or not ID_RE.match(lid):
            raise Refusal(403, "lease")
        with self._lock:
            self._reap(now, mono)
            L = self._lease
            if L is not None and hmac.compare_digest(L.id, lid) and hmac.compare_digest(L.key, key):
                return self._close(L, "finish", now, mono)
            c = self._closed.get(lid)
            if c is not None and hmac.compare_digest(c["key"], key) and c["why"] == "finish":
                return c["result"]
            raise self._gone(key, lid)

    def _result(self, L):
        cpu_end = L.cpu.get("end")
        down_end = L.cpu.get("up") or cpu_end
        return {"ok": True, "bytes_down": L.moved["down"], "bytes_up": L.moved["up"],
                "up": up_rate(L.buckets, L.up_first, L.up_ramp, L.up_last),
                "cpu_limited": {"down": cpu_limited(L.cpu.get("down"), down_end),
                                "up": cpu_limited(L.cpu.get("up"), cpu_end)}}

    def _close(self, L, why, now, mono):
        """The end of a lease (under the lock): the unreserved rest of both sides goes back to its day, a failed start
        (< 1 MB moved) is given back (≤ 6 an hour), one journal line without an address. The reserve of a request
        still running goes back at its own end (``release``)."""
        L.closed = why
        if L.cpu:
            L.cpu["end"] = (mono, self._cpu())
        res = self._result(L)
        self._lease = None
        self._closed[L.id] = {"key": L.key, "mono": mono, "why": why, "result": res}
        try:
            doc = self._load()
            rec = doc.get(L.day)
            if rec is not None:
                rec["tx"] = max(0, rec["tx"] - L.left["down"])
                rec["rx"] = max(0, rec["rx"] - L.left["up"])
            if L.moved["down"] + L.moved["up"] < FAILED_BYTES:
                refunds = [t for t in doc["refunds"] if t > now - 3600]
                if len(refunds) < REFUNDS_HOUR and L.wall0 in doc["starts"]:
                    doc["starts"].remove(L.wall0)
                    if rec is not None and rec["n"] > 0:
                        rec["n"] -= 1
                    refunds.append(round(float(now), 3))
                doc["refunds"] = refunds
            self._save(doc)
        except OSError as e:
            self._log("panel.speed.log.counterUnsaved", err=e)
        self._log("panel.speed.log.done", down=_mb(L.moved["down"]), up=_mb(L.moved["up"]),
                  secs=int(round(max(0.0, min(mono, L.ends_at()) - L.mono0))))
        return res

    def busy(self, now, mono):
        """Seconds the live lease may still last (0 — none): «Проверить сервер» from the panel waits for it (R-bc5;
        HANDOFF r1 «409 и проверка сервера»)."""
        with self._lock:
            self._reap(now, mono)
            L = self._lease
            return max(1, int(math.ceil(L.ends_at() - mono))) if L is not None else 0

    def budget(self, lim, now, mono):
        """The words under the button: the traffic of today, the tests of the hour and of the day, the limits."""
        with self._lock:
            self._reap(now, mono)
            doc = parse_counts(util.read_text(self.path()))[0]
        rec = doc.get(self._day(now)) or {"rx": 0, "tx": 0, "n": 0}
        return {"ok": True, "today_bytes": rec["rx"] + rec["tx"], "day_cap_bytes": lim["day_bytes"],
                "tests_hour": len([t for t in doc["starts"] if t > now - 3600]), "tests_day": rec["n"],
                "caps": {"tests_hour": lim["tests_hour"], "tests_day": lim["tests_day"],
                         "side_bytes": lim["side_bytes"], "day_bytes": lim["day_bytes"],
                         "req_down_max": REQ_DOWN_MAX, "req_up_max": REQ_UP_MAX}}

    # ── the bytes ──
    def block(self):
        """The one random block of 1 MiB the downloads repeat (made once per process)."""
        if self._block is None:
            with self._lock:
                if self._block is None:
                    self._block = os.urandom(BLOCK)
        return self._block


def _mb(n):
    return "%.1f" % (n / 1e6)


def send_down(sp, L, n, write, clock, now_fn=time.time):
    """The body of one ``down`` (``RawStream.write_fn``): ``n`` bytes of the random block in 64 KiB pieces; stops at
    the deadline (15 s, and the end of the lease) or when the lease is closed; what was not sent goes back. ``write(data,
    timeout)`` — the socket waits no longer than the deadline."""
    mv = memoryview(sp.block())
    sent = pend = 0
    deadline = min(clock() + STREAM_S, L.mono0 + LEASE_S)
    try:
        while sent < n:
            t = clock()
            if t >= deadline or L.closed is not None:
                break
            k = min(CHUNK, n - sent)
            off = sent % BLOCK                      # CHUNK divides BLOCK: a piece never crosses its end
            write(mv[off:off + k], deadline - t)
            sent += k
            pend += k
            if pend >= BLOCK:
                sp.moved(L, "down", pend)
                pend = 0
    finally:
        if pend:
            sp.moved(L, "down", pend)
        sp.release(L, "down", n, sent, now_fn(), clock())
    return sent


def recv_up(sp, L, reader, clock, now_fn=time.time):
    """One ``up``: ``reader.readinto1`` (one read of the socket — what has come, §4.4) into one 64 KiB buffer,
    thrown away; every read goes into the bucket of its time. Stops at the end of the body, at the deadline, when the
    lease is closed or the client went away (a read of 0). -> the bytes received."""
    mv = memoryview(bytearray(CHUNK))
    got = 0
    pend, pk, pfirst, plast = 0, None, None, None
    total = reader.left
    deadline = min(clock() + STREAM_S, L.mono0 + LEASE_S)
    try:
        while reader.left > 0:
            t = clock()
            if t >= deadline or L.closed is not None:
                break
            try:
                n = reader.readinto1(mv, deadline - t)
            except OSError:                         # a reset, a TLS error, the deadline: the client is gone — quiet
                break
            if not n:
                break
            t = clock()
            got += n
            k = int(L.ms(t) // BUCKET_MS)
            if pend and (k != pk or pend >= BLOCK):
                sp.got(L, pk, pend, pfirst, plast)
                pend = 0
            if not pend:
                pk, pfirst = k, t
            pend += n
            plast = t
            if got == n:                            # the first bytes of this request: their time at once
                sp.got(L, pk, pend, pfirst, plast)
                pend = 0
    finally:
        if pend:
            sp.got(L, pk, pend, pfirst, plast)
        sp.release(L, "up", total, got, now_fn(), clock())
    return got


SPEED = Speed()
