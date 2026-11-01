"""The schedule of the checks of the server (SPEC-node-score-schedule-share §2.1–2.2, §2.4, §2.8, NS-S2…S7, S18).

Pure functions, the time is a parameter (``now``, ``frm``/``to`` — UTC seconds; ``zone`` — ``tz.Zone``):

    validate(entries) -> (entries_norm, errors)
        the ONE check of the limits (the panel, the CLI and MCP call it; the JS holds no copy of the rules).
        ``errors`` — [{"code", "id" | None, "args"}], the texts — ``bench.sched.err.<code>`` (``error_text``).
        ``entries_norm`` — what may run: a bad entry is off with ``off_why`` = its code, an entry that would break a
        limit of the week is off with that code (the earlier entries of the list win), the entries past 12 dropped.
    prepare(entries, prev, now, rng=None) -> (entries_norm, errors)
        ``validate`` + the fields the server gives: ``id`` of a new entry, ``since`` (creation, off → on, a change of
        the time, the days or ``monthly``), ``off_why`` cleared on an entry that passed. Errors -> nothing to save.
    events(entries, frm, to, zone)       the nominal events of the enabled entries in [frm, to) (NS-S6)
    next_runs(entries, now, zone, n=5)   the first n events after ``now`` (no ``systemd-analyze``)
    due(entries, now, zone, done)        the event to run now (± 20 min of any of its moments) or None
    missed(entries, now, zone, done, armed_at)  the events nobody ran (§2.5 п. 2; the runner B11 writes them)
    calendar_lines(entries, zone, now)   the ``OnCalendar=`` values of the timer (§2.4): the time minus 15 min, the
                                         one-shot UTC lines of the days of the spring change for 12 months ahead
    propose(h1, now, zone, rng=None)     the offer after the first manual check (§1.3, NS-S18): a quick check once a
                                         week and a full one once a month in the quietest hour

All four of the clock (events, next_runs, due, missed, calendar_lines) come from ONE function of events (``_events``),
so the timer, «the next one», ``due`` and ``missed`` cannot disagree. Everything that goes into a unit file is built of
checked numbers and the zone name after ``fullmatch`` (NS-S7): a string of a request never reaches it.
"""

import calendar
import hashlib
import random
import re
import time

from .. import tz

PER_DAY = 6                         # NS-S3: checks of the enabled entries on a day of the week
FULL_PER_DAY = 2                    # of them full
GAP_MIN = 120                       # between any two, around the circle of the week
TOTAL = 12                          # entries, on and off
JITTER_MIN = 15                     # the timer: the line is t − 15 min + RandomizedDelaySec=30min
WINDOW_MIN = 20                     # ``due`` looks ± 20 min around a moment of the event
MISSED_CAP = 7                      # ``missed``: rows per entry at a time; the older ones — one row with ``n``
LIMITS = {"per_day": PER_DAY, "full_per_day": FULL_PER_DAY, "gap_min": GAP_MIN, "total": TOTAL,
          "jitter_min": JITTER_MIN}
MODES = ("quick", "full")
CODES = ("list", "entry", "time", "mode", "days", "on", "quiet", "monthly", "monthly_days", "id", "total",
         "per_day", "full_per_day", "gap")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")   # systemd.time(7); index = ISO day − 1

_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
_ID = re.compile(r"[0-9a-f]{6}")
_ZONE = re.compile(tz.NAME_RE.pattern.lstrip("^").rstrip("$"))   # the name of tz.py, by fullmatch (not «$»)
_DAY = 86400
_WEEK = 7 * 1440
_LOOK_BACK = 400 * _DAY             # ``missed`` never looks further (the history keeps 400 days)
_LOOK_AHEAD = 42 * _DAY             # ``next_runs``: a monthly entry is at most ~5 weeks away


# ── the check ─────────────────────────────────────────────────────────────

def _err(code, eid=None, **args):
    return {"code": code, "id": eid, "args": args}


def _hm(t):
    return int(t[:2]), int(t[3:])


def _entry(e):
    """One entry -> (norm, [codes of its fields]). ``norm`` None: not an object (dropped)."""
    if not isinstance(e, dict):
        return None, ["entry"]
    codes = []
    t = e.get("time")
    if not (isinstance(t, str) and _TIME.fullmatch(t)):     # fullmatch: "03:45\n" is an error, not 03:45
        codes.append("time")
        t = None
    m = e.get("mode")
    if not (isinstance(m, str) and m in MODES):
        codes.append("mode")
        m = None
    d = e.get("days")
    days = None
    if (isinstance(d, list) and d and all(type(x) is int and 1 <= x <= 7 for x in d)   # type(): True is no day
            and len(set(d)) == len(d)):
        days = sorted(d)
    else:
        codes.append("days")
    flags = {}
    for k, dflt in (("on", True), ("quiet", True), ("monthly", False)):
        v = e.get(k, dflt)
        if type(v) is not bool:
            codes.append(k)
            v = dflt
        flags[k] = v
    if flags["monthly"] and days is not None and len(days) != 1:
        codes.append("monthly_days")
    i = e.get("id")
    if i is not None and not (isinstance(i, str) and _ID.fullmatch(i)):
        codes.append("id")
        i = None
    since = e.get("since")
    since = since if type(since) is int and since >= 0 else None
    why = e.get("off_why")
    why = why if isinstance(why, str) and why in CODES else None
    return {"id": i, "time": t, "mode": m, "days": days, "on": flags["on"], "quiet": flags["quiet"],
            "monthly": flags["monthly"], "since": since, "off_why": why}, codes


def _points(e):
    """The minutes of the week of an entry (a monthly one counts as weekly: the first week may hold all, NS-S18)."""
    hh, mm = _hm(e["time"])
    return [(d - 1) * 1440 + hh * 60 + mm for d in e["days"]]


def _dist(a, b):
    x = abs(a - b) % _WEEK
    return min(x, _WEEK - x)


class _Week(object):
    """The enabled entries taken so far: counts per day and the points of the week (NS-S3)."""

    def __init__(self):
        self.n = [0] * 8
        self.full = [0] * 8
        self.pts = []                                   # (minute of the week, entry)

    def clash(self, e):
        """The limits ``e`` would break -> [(code, args)]."""
        out = []
        for d in e["days"]:
            if self.n[d] + 1 > PER_DAY:
                out.append(("per_day", {"day": d, "n": self.n[d] + 1}))
            if e["mode"] == "full" and self.full[d] + 1 > FULL_PER_DAY:
                out.append(("full_per_day", {"day": d, "n": self.full[d] + 1}))
        near = {}                                       # one error per PAIR of entries, the smallest gap (REV З-1)
        for p in _points(e):
            for q, o in self.pts:
                g = _dist(p, q)
                if g < GAP_MIN and (id(o) not in near or g < near[id(o)][0]):
                    near[id(o)] = (g, o)
        for g, o in near.values():
            out.append(("gap", {"a": o["time"], "b": e["time"], "min": g, "ia": o["id"], "ib": e["id"]}))
        return out

    def add(self, e):
        for d in e["days"]:
            self.n[d] += 1
            if e["mode"] == "full":
                self.full[d] += 1
        self.pts.extend((p, e) for p in _points(e))


def _check(entries):
    """-> ([(index in ``entries``, norm)], errors): the work of ``validate`` with the place of every kept entry."""
    if not isinstance(entries, list):
        return [], [_err("list")]
    errors = []
    kept = []
    seen = set()
    for i, raw in enumerate(entries):
        e, codes = _entry(raw)
        if e is None:
            errors.append(_err("entry", None, n=i + 1))
            continue
        if e["id"] is not None:
            if e["id"] in seen:
                codes.append("id")
                e["id"] = None
            else:
                seen.add(e["id"])
        for c in codes:
            errors.append(_err(c, e["id"], n=i + 1))
        if codes:
            e["on"] = False
            e["off_why"] = codes[0]
        kept.append((i, e, bool(codes)))
    if len(kept) > TOTAL:
        errors.append(_err("total", None, n=len(kept), max=TOTAL))
        kept = kept[:TOTAL]
    # the limits of the week: the totals are told once per day, a pair too close — once per pair; in the result the
    # earlier entries of the list win and the one that would break a limit is off with that code
    week, total = _Week(), _Week()
    for _i, e, bad in kept:
        if bad or not e["on"]:
            continue
        for code, args in total.clash(e):
            if code == "gap":
                errors.append(_err("gap", e["id"], **args))
        total.add(e)
        hit = week.clash(e)
        if hit:
            e["on"] = False
            e["off_why"] = hit[0][0]
        else:
            week.add(e)
            e["off_why"] = None
    for d in range(1, 8):
        if total.n[d] > PER_DAY:
            errors.append(_err("per_day", None, day=d, n=total.n[d], max=PER_DAY))
        if total.full[d] > FULL_PER_DAY:
            errors.append(_err("full_per_day", None, day=d, n=total.full[d], max=FULL_PER_DAY))
    return [(i, e) for i, e, _b in kept], errors


def validate(entries):
    """-> (entries_norm, errors); see the head of the module. Never raises on any JSON value."""
    kept, errors = _check(entries)
    return [e for _i, e in kept], errors


def error_text(err):
    """The text of one error of ``validate`` in the language of the caller (``bench.sched.err.<code>``)."""
    from ..i18n import tr
    a = dict(err.get("args") or {})
    if "day" in a:
        a["day"] = tr("bench.sched.day.%d" % a["day"])
    key = err["code"]
    if key in ("field", "type") and a.get("n") == 0:   # the body itself, not an entry (REV-ns-b13 П-2)
        key += "_body"
    return tr("bench.sched.err." + key, **a)


# ── the body of a request (B13: the API of the panel; MCP and the CLI may call it too) ─────────────

BODY_KEYS = ("schedule", "confirm", "retry", "tz")
ENTRY_KEYS = ("id", "time", "mode", "days", "on", "quiet", "monthly", "since", "off_why")   # since/off_why: the
                                                                     # server's, sent back as GET gave them, ignored
STR_MAX = 64                        # any string of the body (an entry's time is 5, a zone name ≤ 64)
ERR_MAX = 20                        # errors told at most (a body of junk never gives a page of them)
_KEY = re.compile(r"[a-z_]{1,24}")


def _key_shown(k):
    """An unknown key is said only when it is a plain word (never a string of a request as it came)."""
    return k if isinstance(k, str) and _KEY.fullmatch(k) else "?"


def check_body(doc):
    """The JSON body of a request -> (entries, opts, errors). Strict (B13): only the keys ``BODY_KEYS`` (``schedule`` —
    a list, required), in an entry only ``ENTRY_KEYS``; ``confirm``/``retry`` — bool; ``tz`` — a zone name by
    ``fullmatch`` or null (as GET gives it at ``tz_fallback``: REV-ns-b13 З-1); any string ≤ ``STR_MAX``; at most ``TOTAL`` entries (more — the one error ``total``, the entries are
    not looked at); ``since`` — an integer ≥ 0 or null, ``off_why`` — a string or null (both ignored: the server
    sets them). ``errors`` — as ``validate`` (code, id None, args; ``error_text``), at most ``ERR_MAX``; non-empty —
    the body is refused as a whole. The fields of an entry (time, mode, days …) are ``validate``'s."""
    errors = []
    if not isinstance(doc, dict) or "schedule" not in doc:
        return [], {}, [_err("body")]
    for k in doc:
        if k not in BODY_KEYS:
            errors.append(_err("field", None, field=_key_shown(k), n=0))
    opts = {}
    for k in ("confirm", "retry"):
        if k in doc:
            if type(doc[k]) is not bool:
                errors.append(_err("type", None, field=k, n=0))
            else:
                opts[k] = doc[k]
    if "tz" in doc:
        z = doc["tz"]
        if z is None:                                   # GET says null when the zone is not read (``tz_fallback``)
            opts["tz"] = None
        elif not (isinstance(z, str) and len(z) <= STR_MAX and _ZONE.fullmatch(z)):
            errors.append(_err("tz"))
        else:
            opts["tz"] = z
    entries = doc["schedule"]
    if not isinstance(entries, list):
        errors.append(_err("list"))
        return [], opts, errors[:ERR_MAX]
    if len(entries) > TOTAL:
        errors.append(_err("total", None, n=len(entries), max=TOTAL))
        return [], opts, errors[:ERR_MAX]
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            continue                                    # ``validate`` says «entry»
        for k, v in e.items():
            if k not in ENTRY_KEYS:
                errors.append(_err("field", None, field=_key_shown(k), n=i + 1))
            elif isinstance(v, str) and len(v) > STR_MAX:
                errors.append(_err("long", None, field=k, n=i + 1, max=STR_MAX))
            elif isinstance(v, list) and len(v) > 7:
                errors.append(_err("long", None, field=k, n=i + 1, max=7))
        s, w = e.get("since"), e.get("off_why")
        if s is not None and not (type(s) is int and s >= 0):
            errors.append(_err("type", None, field="since", n=i + 1))
        if w is not None and not isinstance(w, str):
            errors.append(_err("type", None, field="off_why", n=i + 1))
    return entries, opts, errors[:ERR_MAX]


def new_id(taken, rng=None):
    """A fresh id of an entry (6 hex, NS-S2) not in ``taken``."""
    rng = rng or random.SystemRandom()
    while True:
        i = "%06x" % rng.randrange(1 << 24)
        if i not in taken:
            return i


def stable_id(e, i, taken):
    """An id for an entry of the state that has none or a broken one: the same on every read (a read that does not
    save it shows the same id), not in ``taken``."""
    salt = 0
    while True:
        h = hashlib.sha256(("%d|%r|%r|%r|%r|%d" % (i, e.get("time"), e.get("mode"), e.get("days"),
                                                   e.get("monthly"), salt)).encode()).hexdigest()[:6]
        if h not in taken:
            return h
        salt += 1


def prepare(entries, prev, now, rng=None):
    """The list of a request -> (entries_norm, errors). ``prev`` — the saved list (its ids are the known ones: an id
    the server did not give is an error ``id``). The server gives ``id`` to a new entry and sets ``since``."""
    norm, errors = validate(entries)
    known = {}
    for p in prev if isinstance(prev, list) else []:
        if isinstance(p, dict) and isinstance(p.get("id"), str):
            known.setdefault(p["id"], p)
    for n, e in enumerate(norm):
        if e["id"] is not None and e["id"] not in known:
            errors.append(_err("id", e["id"], n=n + 1, why="unknown"))
    if errors:
        return norm, errors
    taken = set(e["id"] for e in norm if e["id"])
    now = int(now)
    for e in norm:
        old = known.get(e["id"]) if e["id"] else None
        if old is None:
            e["id"] = new_id(taken, rng)
            taken.add(e["id"])
            e["since"] = now
        else:
            moved = (old.get("time") != e["time"] or old.get("days") != e["days"]
                     or bool(old.get("monthly")) != e["monthly"])
            woke = old.get("on", True) is not True and e["on"]
            since = old.get("since")
            e["since"] = now if moved or woke or type(since) is not int else since
        # off_why is the server's (§2.1: «why the check turned it off»): never the request's (REV-ns-b10 П-1)
        e["off_why"] = None if e["on"] or old is None or old.get("on", True) is True else _old_why(old)
    return norm, errors


def _old_why(old):
    w = old.get("off_why")
    return w if isinstance(w, str) and w in CODES else None


# ── the clock ─────────────────────────────────────────────────────────────

def _occurrences(wall, z):
    """The UTC moments when the clock of ``z`` shows the naive local ``wall`` (seconds): 1, 2 (the autumn hour) or
    0 (the spring hour)."""
    offs = sorted(set(z.offset(wall + k) for k in (-_DAY, 0, _DAY)))
    return sorted(wall - o for o in offs if z.offset(wall - o) == o)


def _dates(frm, to, z):
    """The local dates (y, mo, d) from the day before ``frm`` to the day after ``to``."""
    a = tz.local(frm, z)
    b = tz.local(to, z)
    d0 = calendar.timegm((a.tm_year, a.tm_mon, a.tm_mday, 0, 0, 0, 0, 0, 0)) - _DAY
    d1 = calendar.timegm((b.tm_year, b.tm_mon, b.tm_mday, 0, 0, 0, 0, 0, 0)) + _DAY
    t = d0
    while t <= d1:
        g = time.gmtime(t)
        yield g.tm_year, g.tm_mon, g.tm_mday, g.tm_wday + 1
        t += _DAY


def _on(entries):
    """The entries that run: enabled after ``validate`` (a bad one or one past a limit is off there) AND with an
    ``id`` — the mark of a done event is kept by id (NS-S6): two entries without one would share a mark (REV-ns-b10
    З-2). The ids are given by the server (``prepare``) and by ``model.bench`` (``check_saved``): pass the list of
    ``model.bench(st)["schedule"]``, never the raw one of state.json."""
    return [e for e in validate(entries)[0] if e["on"] and e["id"]]


def _events(on, frm, to, z):
    """THE function of events: the nominal events of ``on`` (normalized enabled entries) with ``ts`` in [frm, to).
    ``cands`` — every moment the timer may aim at (§2.4): ``tz.to_utc(t)`` and each appearance of the time of the
    line (t − 15 min) plus 15 min; ``line`` — the line appears on the clock that day (spring: maybe not)."""
    out = []
    for y, mo, d, wd in _dates(frm, to, z):
        for e in on:
            if wd not in e["days"] or (e["monthly"] and d > 7):
                continue
            hh, mm = _hm(e["time"])
            ts = tz.to_utc(y, mo, d, hh, mm, z)
            if not frm <= ts < to:
                continue
            wall = calendar.timegm((y, mo, d, hh, mm, 0, 0, 0, 0))
            line = _occurrences(wall - JITTER_MIN * 60, z)
            cands = sorted(set([ts] + [x + JITTER_MIN * 60 for x in line]))
            out.append({"key": "%04d-%02d-%02d %s" % (y, mo, d, e["time"]), "id": e["id"], "mode": e["mode"],
                        "quiet": e["quiet"], "ts": ts, "cands": cands, "line": bool(line)})
    out.sort(key=lambda v: (v["ts"], v["key"], v["id"] or ""))
    return out


def _public(ev):
    v = dict(ev)
    v.pop("line", None)
    alt = [c for c in ev["cands"] if c != ev["ts"]]
    if alt:
        v["ts_alt"] = alt[0]
    return v


def events(entries, frm, to, zone):
    """The nominal events of the enabled entries in [frm, to): {"key": "YYYY-MM-DD HH:MM", "id", "mode", "quiet",
    "ts", "cands"[, "ts_alt"]} by ``ts``. ``key`` — the local date and the time of the entry (the mark of a done
    event, NS-S6); ``ts`` — ``tz.to_utc`` (the spring hour +1 h, the autumn one — the first); ``ts_alt`` — the other
    moment of the day of the change (§2.4)."""
    return [_public(v) for v in _events(_on(entries), int(frm), int(to), zone)]


def next_runs(entries, now, zone, n=5):
    """The first ``n`` events after ``now`` (looked for up to a year ahead, by 6 weeks)."""
    on, n, out = _on(entries), max(0, int(n)), []
    frm = int(now)
    while on and len(out) < n and frm < now + 366 * _DAY:
        out.extend(v for v in _events(on, frm, frm + _LOOK_AHEAD, zone) if v["ts"] > now)
        frm += _LOOK_AHEAD
    return [_public(v) for v in out[:n]]


def _done_key(done, eid):
    k = done.get(eid) if isinstance(done, dict) else None
    return k if isinstance(k, str) else ""


def due(entries, now, zone, done):
    """The event to run at ``now`` (NS-S6, §2.5 п. 1): of the entries' events of yesterday, today and tomorrow, the
    one with a moment (``cands``) nearest to ``now`` within ± 20 min and the key past the mark ``done[id]``; None.
    ``done`` — {id: "YYYY-MM-DD HH:MM"} of sched-state.json (the key of the last event run or missed)."""
    best = None
    w = WINDOW_MIN * 60
    for v in _events(_on(entries), int(now) - 2 * _DAY, int(now) + 2 * _DAY, zone):
        gap = min(abs(now - c) for c in v["cands"])
        if gap > w or v["key"] <= _done_key(done, v["id"]):
            continue
        if best is None or gap < best[0]:
            best = (gap, v)
    return _public(best[1]) if best else None


def missed(entries, now, zone, done, armed_at=None):
    """The events nobody ran (§2.5 п. 2): of every enabled entry, in (max(since, armed_at, the mark), now − 20 min)
    with the key past the mark. -> {"rows": [event…] (≤ 7 per entry, the newest), "older": {id: n} (the rest — one
    row of the runner with ``why_args.n``), "done": {id: key} (the new marks)}."""
    rows, older, marks = [], {}, {}
    now = int(now)
    on = _on(entries)
    for e in on:
        mark = _done_key(done, e["id"])
        lo = max(e["since"] or 0, int(armed_at or 0), now - _LOOK_BACK)
        evs = [v for v in _events([e], lo + 1, now - WINDOW_MIN * 60, zone) if v["key"] > mark]
        if not evs:
            continue
        if len(evs) > MISSED_CAP:
            older[e["id"]] = len(evs) - MISSED_CAP
            evs = evs[-MISSED_CAP:]
        rows.extend(_public(v) for v in evs)
        marks[e["id"]] = evs[-1]["key"]
    rows.sort(key=lambda v: (v["ts"], v["key"]))
    return {"rows": rows, "older": older, "done": marks}


def _zone_suffix(zone):
    """-> (the suffix of OnCalendar, the shift in minutes): the name of the zone (fullmatch), or «UTC» and the time
    moved to UTC when tz.py could not read the zone (the fallback UTC+03:00, §2.4)."""
    if not zone.ok:
        return "UTC", -tz.FALLBACK_OFFSET // 60
    name = zone.name
    if not (isinstance(name, str) and _ZONE.fullmatch(name) and tz.valid_name(name)):
        raise ValueError("calendar_lines: bad zone name")       # never: a zone that loaded passed tz.valid_name
    return name, 0


def calendar_lines(entries, zone, now):
    """The values of ``OnCalendar=`` (§2.4, §2.8): per time of the line (t − 15 min, the day carried) the days of the
    week folded, ``*-*-*`` (weekly) or the first week of the month (``*-*-01..07``; carried over midnight —
    ``*-*-01..06`` and ``*-*~01``, the last day of the month before); then the one-shot UTC lines of the days of a
    change of the clock within 12 months of ``now`` where the time of the line does not exist."""
    on = _on(entries)
    suffix, shift = _zone_suffix(zone)
    groups = {}
    for e in on:
        hh, mm = _hm(e["time"])
        for d in e["days"]:
            m = (d - 1) * 1440 + hh * 60 + mm - JITTER_MIN + shift
            carry = m // 1440 - (d - 1)                     # 0 or −1 (the shift is never ahead)
            m %= _WEEK
            day, hm = m // 1440 + 1, m % 1440
            if not e["monthly"]:
                specs = ("*-*-*",)
            elif carry == 0:
                specs = ("*-*-01..07",)
            else:
                specs = ("*-*-01..06", "*-*~01")
            for s in specs:
                groups.setdefault((hm, s), set()).add(day)
    lines = []
    for (hm, s), days in sorted(groups.items()):
        lines.append("%s %s %02d:%02d:00 %s" % (",".join(DAY_NAMES[d - 1] for d in sorted(days)), s,
                                               hm // 60, hm % 60, suffix))
    if zone.ok:
        lines.extend(_spring_lines(on, zone, int(now)))
    return lines


def _spring_lines(on, z, now):
    """The one-shot lines (UTC) for the events whose line (t − 15 min) is not on the clock that day: the moment
    ``tz.to_utc(t)`` − 15 min (§2.4), for the changes of the clock within 12 months of ``now``."""
    out = set()
    k = 0
    while k <= 366:
        a = now + k * _DAY
        if z.offset(a) != z.offset(a + _DAY):
            for v in _events(on, a - _DAY, a + 3 * _DAY, z):
                at = v["ts"] - JITTER_MIN * 60
                if not v["line"] and now < at <= now + 366 * _DAY:
                    out.add(time.strftime("%Y-%m-%d %H:%M:00 UTC", time.gmtime(at)))
        k += 1
    return sorted(out)


# ── the offer (§1.3, NS-S18) ──────────────────────────────────────────────

H1_DAYS = 14                        # the hourly rows looked at
H1_MIN_DAYS = 7                     # fewer local days with rows — 04:00–05:59
DEFAULT_HOURS = (4, 5)


def propose(h1, now, zone, rng=None, ctx=None):
    """The two entries the panel offers after the first manual check (§1.3, NS-S18): a quick check once a week in
    the quietest local hour of ``host_h1`` (rx + tx) for 14 days plus random minutes, and a full one once a month
    (the first week, another day of the week, the same hour), both «only when idle». ``h1`` — rows {"ts", "rx",
    "tx"} (UTC hour, bytes); no rows or fewer than 7 local days — 04:00–05:59 and random minutes. ``rng`` — the
    random of the node (its own minutes). -> {"entries": [quick, full], "basis": "h1" | "default"} and, with ``ctx``
    (of ``cost.estimate``), the price before adding: ``cost`` (both) and ``cost_quick`` («Только быструю»). No id, no
    ``since``: they come with ``prepare`` when the owner adds them."""
    rng = rng or random.SystemRandom()
    lo = int(now) - H1_DAYS * _DAY
    by = {}                                              # (iso day, hour) -> [bytes, rows]
    days = set()
    for r in h1 if isinstance(h1, (list, tuple)) else []:
        if not isinstance(r, dict):
            continue
        ts = r.get("ts")
        if type(ts) not in (int, float) or not lo <= ts < now:
            continue
        b = sum(v for v in (r.get("rx"), r.get("tx")) if type(v) in (int, float) and v >= 0)
        lt = tz.local(ts, zone)
        days.add((lt.tm_year, lt.tm_yday))
        acc = by.setdefault((lt.tm_wday + 1, lt.tm_hour), [0, 0])
        acc[0] += b
        acc[1] += 1
    if len(days) >= H1_MIN_DAYS:
        hours = {}                                       # the mean of an hour of the day: a missing row is no quiet
        for (_d, h), (b, n) in by.items():
            hb = hours.setdefault(h, [0, 0])
            hb[0] += b
            hb[1] += n
        hour = min(hours, key=lambda h: (hours[h][0] / float(hours[h][1]), h))

        def mean(d):
            b, n = by.get((d, hour), (0, 0))
            return (0, b / float(n), d) if n else (1, 0, d)  # a day with no row at that hour — the last choice
        order = sorted(range(1, 8), key=mean)
        basis = "h1"
    else:
        hour = DEFAULT_HOURS[rng.randrange(len(DEFAULT_HOURS))]
        first = 1 + rng.randrange(7)
        order = [first, (first - 1 + rng.randrange(1, 7)) % 7 + 1]     # another day of the week
        basis = "default"
    t = "%02d:%02d" % (hour, rng.randrange(60))
    quick = {"time": t, "mode": "quick", "days": [order[0]], "on": True, "quiet": True, "monthly": False}
    full = {"time": t, "mode": "full", "days": [order[1]], "on": True, "quiet": True, "monthly": True}
    out = {"entries": [quick, full], "basis": basis}
    if ctx is not None:
        from . import cost
        out["cost"] = cost.estimate([quick, full], ctx)
        out["cost_quick"] = cost.estimate([quick], ctx)
    return out


# ── the check at a read (§2.1, §11.1 п. 12, 19) ───────────────────────────

def check_saved(raw):
    """The saved list (``gateway.hostmon.bench.schedule``) -> (entries_norm, fix). Pure: ``fix`` says what the save
    must change so that the state holds what runs — [{"op": "reset"}] (not a list), {"op": "drop", "i"} (not an
    object, past 12), {"op": "set", "i", "fields"} (``on: false`` + ``off_why`` of a bad entry or one past a limit; an
    ``id`` for an entry with none or a broken/repeated one — the same id at every read, ``stable_id``)."""
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [{"op": "reset"}]
    kept, _errors = _check(raw)
    fix = []
    have = set(i for i, _e in kept)
    for i in range(len(raw)):
        if i not in have:
            fix.append({"op": "drop", "i": i})
    taken = set(e["id"] for _i, e in kept if e["id"])
    for i, e in kept:
        r = raw[i]
        fields = {}
        if e["id"] is None:
            e["id"] = stable_id(r, i, taken)
            taken.add(e["id"])
            fields["id"] = e["id"]
        if r.get("on", True) is not e["on"]:                  # no key = on (the default), nothing to write
            fields["on"] = e["on"]
        if r.get("off_why") != e["off_why"]:
            fields["off_why"] = e["off_why"]
        if fields:
            fix.append({"op": "set", "i": i, "fields": fields})
    return [e for _i, e in kept], fix


def apply_fix(raw, fix):
    """``fix`` of ``check_saved`` on the saved list ``raw`` -> the new list (``raw`` itself changed when a list)."""
    if any(f.get("op") == "reset" for f in fix):
        return []
    drop = set()
    for f in fix:
        if f.get("op") == "drop":
            drop.add(f["i"])
        elif f.get("op") == "set" and isinstance(raw[f["i"]], dict):
            raw[f["i"]].update(f["fields"])
    return [r for i, r in enumerate(raw) if i not in drop]
