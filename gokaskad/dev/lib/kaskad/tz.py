"""The time zone of the node (docs/k3/SPEC-stage1-exits.md §3.7): the day of a quota reset, «traffic of the day», the
night rebalance, the time in pushes and on charts.

The name (IANA, ``Europe/Moscow`` by default) lives in /etc/kaskad/config as ``KASKAD_TZ`` next to ``KASKAD_LANG``.
The time zone of the OS is never changed: other products and journals live on the server.

The standard module of time zones is 3.9+ (the code must run on 3.8, tools/k3_py_compat.py), so the TZif file of the
system database (/usr/share/zoneinfo/<name>, RFC 8536) is read here: version 1 or 2+ (64-bit transitions), and the
POSIX-TZ string of its footer, which gives the time after the last transition. The footer is a must: files built by
``zic -b slim`` hold no transitions after the last change of the rules (``Europe/Berlin``: summer time after 1996 is
only in the footer), and «the last offset» would be an hour wrong half a year.

No file or a broken one -> a fixed UTC+03:00 and ``warning()`` for «Настройки → Время».
"""

import calendar
import os
import re
import struct
import threading
import time

DEFAULT = "Europe/Moscow"
KEY = "KASKAD_TZ"
FALLBACK_OFFSET = 3 * 3600                         # UTC+03:00 when the zone cannot be read
ZONEINFO = "/usr/share/zoneinfo"                   # the system database; tests point it elsewhere
NAME_RE = re.compile(r"^[A-Za-z_+-]+(/[A-Za-z0-9_+-]+){0,2}$")
_CONF_LINE = re.compile(r"^\s*(?:export\s+)?KASKAD_TZ\s*=\s*(.*?)\s*$")
_MAX_FILE = 1 << 20                                # a TZif file is a few KB; anything bigger is not one

_lock = threading.Lock()
_conf_cache = [None, None]                         # [(path, inode, mtime_ns, size), name or None]
_zone_cache = {}                                   # name -> ((path, inode, mtime_ns, size), Zone)


class TzError(ValueError):
    """The TZif file or the POSIX-TZ string does not parse."""


# ── POSIX-TZ string (RFC 8536 §3.3, POSIX 8.3) ─────────────────────────────

def _days_in_month(y, m):
    return calendar.monthrange(y, m)[1]


def _rule_day(rule, y):
    """The day of the year (0-based) of a rule ``("M", m, w, d)`` | ``("J", n)`` | ``("N", n)`` in year ``y``."""
    kind = rule[0]
    if kind == "J":                                # 1..365, the 29th of February never counted
        n = rule[1] - 1
        return n + 1 if calendar.isleap(y) and n >= 59 else n
    if kind == "N":                                # 0..365, the 29th of February counted
        return rule[1]
    _k, m, w, d = rule
    first = (calendar.weekday(y, m, 1) + 1) % 7    # 0 = Sunday, as in POSIX
    day = 1 + (d - first) % 7 + (w - 1) * 7
    while day > _days_in_month(y, m):              # w = 5: the last such day of the month
        day -= 7
    return (calendar.timegm((y, m, day, 0, 0, 0, 0, 0, 0)) - calendar.timegm((y, 1, 1, 0, 0, 0, 0, 0, 0))) // 86400


class Posix(object):
    """``std offset [dst [offset] [,start[/time],end[/time]]]``: ``utcoff`` — seconds east of UTC (POSIX counts west)."""

    def __init__(self, text):
        self.text = text
        s = text
        self.std, s = self._name(s)
        off, s = self._time(s, signed=True, limit=24)
        self.std_off = -off
        self.dst = None
        self.dst_off = self.std_off
        self.start = self.end = None
        if s:
            self.dst, s = self._name(s)
            self.dst_off = self.std_off + 3600
            if s and s[0] != ",":
                off, s = self._time(s, signed=True, limit=24)
                self.dst_off = -off
            if s:
                if s[0] != ",":
                    raise TzError("posix: %r" % text)
                self.start, s = self._rule(s[1:])
                if not s or s[0] != ",":
                    raise TzError("posix: %r" % text)
                self.end, s = self._rule(s[1:])
            else:
                self.start, self.end = ("M", 3, 2, 0, 7200), ("M", 11, 1, 0, 7200)   # the default of glibc
            if s:
                raise TzError("posix: %r" % text)

    @staticmethod
    def _name(s):
        if s.startswith("<"):
            i = s.find(">")
            if i < 4:
                raise TzError("posix name: %r" % s)
            return s[1:i], s[i + 1:]
        m = re.match(r"[A-Za-z]{3,}", s)
        if not m:
            raise TzError("posix name: %r" % s)
        return m.group(0), s[m.end():]

    @staticmethod
    def _time(s, signed, limit):
        """``[+-]hh[:mm[:ss]]`` -> (seconds, rest); hours up to ``limit`` (167 in a rule time, RFC 8536 §3.3.1)."""
        m = re.match(r"([+-]?)(\d{1,3})(?::(\d{1,2}))?(?::(\d{1,2}))?", s)
        if not m or (m.group(1) and not signed):
            raise TzError("posix time: %r" % s)
        h, mi, se = int(m.group(2)), int(m.group(3) or 0), int(m.group(4) or 0)
        if h > limit or mi > 59 or se > 59:
            raise TzError("posix time: %r" % s)
        v = h * 3600 + mi * 60 + se
        return (-v if m.group(1) == "-" else v), s[m.end():]

    def _rule(self, s):
        m = re.match(r"M(\d{1,2})\.(\d)\.(\d)", s)
        if m:
            mo, w, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if not (1 <= mo <= 12 and 1 <= w <= 5 and 0 <= d <= 6):
                raise TzError("posix rule: %r" % s)
            rule, s = ("M", mo, w, d), s[m.end():]
        else:
            m = re.match(r"(J?)(\d{1,3})", s)
            if not m:
                raise TzError("posix rule: %r" % s)
            n = int(m.group(2))
            if m.group(1):
                if not 1 <= n <= 365:
                    raise TzError("posix rule: %r" % s)
                rule = ("J", n)
            else:
                if not 0 <= n <= 365:
                    raise TzError("posix rule: %r" % s)
                rule = ("N", n)
            s = s[m.end():]
        at = 7200
        if s.startswith("/"):
            at, s = self._time(s[1:], signed=True, limit=167)
        return rule + (at,), s

    def _edge(self, rule, y, off):
        """The UTC moment of a rule in year ``y``; its time is local by the offset in force before it (``off``)."""
        day = _rule_day(rule[:-1], y)
        return calendar.timegm((y, 1, 1, 0, 0, 0, 0, 0, 0)) + day * 86400 + rule[-1] - off

    def offset(self, ts):
        """-> (seconds east of UTC, is summer time) at UTC ``ts``."""
        if self.dst is None:
            return self.std_off, False
        y = time.gmtime(ts + self.std_off).tm_year     # the year by the standard clock, as the rules count it
        start = self._edge(self.start, y, self.std_off)
        end = self._edge(self.end, y, self.dst_off)
        if start < end:                                # the north: summer inside the year
            dst = start <= ts < end
        else:                                          # the south: summer across the new year
            dst = not (end <= ts < start)
        return (self.dst_off, True) if dst else (self.std_off, False)


# ── TZif (RFC 8536) ─────────────────────────────────────────────────────────

class Zone(object):
    """A parsed zone: transitions, local time types and the POSIX footer. ``fixed`` — a zone of one offset."""

    def __init__(self, name, trans=(), idx=(), types=((0, False),), footer=None, ok=True):
        self.name = name
        self.trans = list(trans)                   # UTC seconds, ascending
        self.idx = list(idx)                       # the type after each transition
        self.types = list(types)                   # (seconds east of UTC, is summer time)
        self.footer = footer                       # Posix or None
        self.ok = ok

    @classmethod
    def fixed(cls, name, off):
        return cls(name, types=[(off, False)], ok=False)

    def offset(self, ts):
        """Seconds east of UTC at UTC ``ts``."""
        return self.info(ts)[0]

    def info(self, ts):
        """(seconds east of UTC, is summer time) at UTC ``ts``."""
        ts = int(ts)
        if self.trans and ts >= self.trans[-1] or not self.trans:
            if self.footer is not None:
                return self.footer.offset(ts)
            if not self.trans:
                return self.types[0]
            return self.types[self.idx[-1]]
        if ts < self.trans[0]:
            return self.types[0]                   # before the first transition: the first type (RFC 8536 §3.2)
        lo, hi = 0, len(self.trans) - 1            # the last transition <= ts
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.trans[mid] <= ts:
                lo = mid
            else:
                hi = mid - 1
        return self.types[self.idx[lo]]


def parse_tzif(data, name=""):
    """The bytes of a TZif file -> Zone. TzError when it is not one."""
    def header(off):
        if len(data) < off + 44 or data[off:off + 4] != b"TZif":
            raise TzError("tzif: no header")
        ver = data[off + 4:off + 5]
        counts = struct.unpack(">6l", data[off + 20:off + 44])
        if min(counts) < 0:
            raise TzError("tzif: counts")
        return ver, counts

    def block(off, counts, tsize):
        isutcnt, isstdcnt, leapcnt, timecnt, typecnt, charcnt = counts
        if typecnt < 1 or (isutcnt and isutcnt != typecnt) or (isstdcnt and isstdcnt != typecnt):
            raise TzError("tzif: types")
        size = timecnt * tsize + timecnt + typecnt * 6 + charcnt + leapcnt * (tsize + 4) + isstdcnt + isutcnt
        body = data[off:off + size]
        if len(body) != size:
            raise TzError("tzif: short")
        fmt = ">%d%s" % (timecnt, "q" if tsize == 8 else "l")
        trans = struct.unpack(fmt, body[:timecnt * tsize])
        p = timecnt * tsize
        idx = list(bytearray(body[p:p + timecnt]))
        p += timecnt
        types = []
        for i in range(typecnt):
            utoff, isdst, _abbr = struct.unpack(">lBB", body[p + i * 6:p + i * 6 + 6])
            if abs(utoff) > 26 * 3600:
                raise TzError("tzif: offset")
            types.append((utoff, bool(isdst)))
        if any(i >= typecnt for i in idx):
            raise TzError("tzif: index")
        if any(b <= a for a, b in zip(trans, trans[1:])):
            raise TzError("tzif: order")
        return list(trans), idx, types, off + size

    ver, counts = header(0)
    trans, idx, types, end = block(44, counts, 4)
    footer = None
    if ver >= b"2":
        ver2, counts2 = header(end)
        trans, idx, types, end = block(end + 44, counts2, 8)
        rest = data[end:]
        if rest[:1] == b"\n":
            nl = rest.find(b"\n", 1)
            if nl < 0:
                raise TzError("tzif: footer")
            text = rest[1:nl].decode("ascii", "replace")
            if text:
                footer = Posix(text)
    return Zone(name, trans, idx, types, footer)


# ── the name of the node ───────────────────────────────────────────────────

def valid_name(name):
    """The name looks like an IANA one (no dots: no way out of the database)."""
    return isinstance(name, str) and bool(NAME_RE.match(name))


def zone_path(name):
    return os.path.join(ZONEINFO, name)


def _config_name():
    """KASKAD_TZ from /etc/kaskad/config (the last assignment wins, as in bash ``source``); None when not set."""
    from . import paths
    p = paths.config()
    try:
        st = os.stat(p)
        sig = (p, st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    with _lock:
        if _conf_cache[0] == sig:
            return _conf_cache[1]
    val = None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _CONF_LINE.match(line)
                if m:
                    v = m.group(1)
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                        v = v[1:-1]
                    val = v.strip() or None
    except (OSError, IOError):
        return None
    with _lock:
        _conf_cache[0], _conf_cache[1] = sig, val
    return val


def name():
    """The name of the zone of the node as written (``Europe/Moscow`` when none is written)."""
    return _config_name() or DEFAULT


def load(zname):
    """The zone ``zname`` from the system database; Zone.fixed(UTC+03:00) with ``ok = False`` when it cannot be read."""
    if not valid_name(zname):
        return Zone.fixed(zname, FALLBACK_OFFSET)
    p = zone_path(zname)
    try:
        st = os.stat(p)
        sig = (p, st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        return Zone.fixed(zname, FALLBACK_OFFSET)
    with _lock:
        hit = _zone_cache.get(zname)
        if hit and hit[0] == sig:
            return hit[1]
    try:
        if st.st_size > _MAX_FILE or not os.path.isfile(p):
            raise TzError("tzif: not a file")
        with open(p, "rb") as f:
            z = parse_tzif(f.read(), zname)
    except (OSError, IOError, TzError, struct.error, ValueError):
        z = Zone.fixed(zname, FALLBACK_OFFSET)
    with _lock:
        _zone_cache[zname] = (sig, z)
    return z


def zone():
    """The zone of the node now (re-read when /etc/kaskad/config or the zone file changes)."""
    return load(name())


def warning():
    """None — the zone of the node is read; "bad_name" | "no_zone" — the node counts by UTC+03:00 (§3.7)."""
    n = name()
    if not valid_name(n):
        return "bad_name"
    return None if load(n).ok else "no_zone"


def set_name(zname):
    """Write the zone of the node to /etc/kaskad/config (as i18n.set_lang) -> the name. ValueError: not a name of the
    system database. The time zone of the OS is not touched."""
    from . import paths, util
    from .i18n import tr
    v = str(zname or "").strip()
    if not valid_name(v):
        raise ValueError(tr("tz.bad", value=str(zname if zname is not None else "")))
    if not load(v).ok:
        raise ValueError(tr("tz.missing", value=v))
    p = paths.config()
    with util.config_locked():                 # one writer of the file at a time (util.config_locked)
        keep = [l for l in util.config_lines(util.read_text(p)) if not _CONF_LINE.match(l)]
        util.ensure_dir(paths.etc(), 0o700)
        util.atomic_write(p, "\n".join(keep + ['%s="%s"' % (KEY, v)]) + "\n", 0o600)
    with _lock:
        _conf_cache[0] = _conf_cache[1] = None
    return v


# ── the clock of the node ──────────────────────────────────────────────────

def offset(ts=None, z=None):
    """Seconds east of UTC at ``ts`` (now when None) in the zone of the node (or ``z``)."""
    z = z or zone()
    return z.offset(time.time() if ts is None else ts)


def local(ts=None, z=None):
    """time.struct_time of the wall clock of the node at ``ts`` (tm_isdst, tm_gmtoff, tm_zone set)."""
    z = z or zone()
    ts = int(time.time() if ts is None else ts)
    off, dst = z.info(ts)
    g = time.gmtime(ts + off)
    sign = "+" if off >= 0 else "-"
    label = "UTC%s%02d:%02d" % (sign, abs(off) // 3600, abs(off) % 3600 // 60)
    return time.struct_time(tuple(g[:8]) + (1 if dst else 0, label, off))


def day(ts=None, z=None):
    """'YYYY-MM-DD' of the node at ``ts``."""
    return time.strftime("%Y-%m-%d", local(ts, z))


def _parse_hm(at):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(at or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("HH:MM expected: %r" % (at,))
    return int(m.group(1)), int(m.group(2))


def to_utc(y, mo, d, hh, mm, z=None):
    """The UTC moment when the clock of the node shows ``y-mo-d hh:mm``. The hour skipped at the spring change -> the
    moment of the change plus the same distance (02:30 -> 03:30); the hour that runs twice in autumn -> the first one."""
    z = z or zone()
    wall = calendar.timegm((y, mo, d, hh, mm, 0, 0, 0, 0))
    offs = sorted(set(z.offset(wall + k) for k in (-86400, 0, 86400)), reverse=True)
    good = [wall - o for o in offs if z.offset(wall - o) == o]
    if good:
        return min(good)
    return wall - z.offset(wall - 86400)           # a skipped time: by the offset before the change


def next_at(at, now=None, z=None):
    """The next UTC moment after ``now`` when the clock of the node shows ``at`` ('04:00')."""
    z = z or zone()
    hh, mm = _parse_hm(at)
    now = time.time() if now is None else now
    lt = local(now, z)
    base = calendar.timegm((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, 0))
    for k in range(-1, 3):
        g = time.gmtime(base + k * 86400)
        t = to_utc(g.tm_year, g.tm_mon, g.tm_mday, hh, mm, z)
        if t > now:
            return t
    raise ValueError("next_at: no moment")         # never: two days ahead always hold one
