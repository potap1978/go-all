"""The pins of devices to exits (/var/lib/kaskad/gw/pins.json) and the counters of the quota period
(/var/lib/kaskad/gw/traffic.json) — docs/k3/SPEC-stage1-exits.md §3.4, §3.5, §5.6.

pins.json — by group (SPEC-groups §2.7)::

    {"version": 1, "saved": <ts>,
     "groups": {"<gid>": {"pins": {"<device id>": {"exit", "tier", "since", "why"}},
                          "tier_good_since": {"<tier id>": <ts>}, "active_tier": "<tier id>"|null,
                          "last_step": null|"direct"|"block", "told_tier": "<tier id>"|null}},
     "gw_all": "gx-<exit id>"|null}

Only the collector writes it (``Store``), at once in the tick where the pins changed, before the state lock is let go:
every other process (``ops._apply`` → ``net.plan`` → ``exits.marks``, ``xray.apply``, ``hooks``, ``trace``) reads the
decision from the file (``read``), and a stale file would undo moves. Only ``saved`` and ``tier_good_since`` without a
change of pins are written at most once in 60 s; on SIGTERM — at once (``force``). One exception to «only the
collector»: ``handover_file`` (the routes of a key that leaves the pool go to its other copy, REV-335-2) rewrites the
file under the state lock; ``Store.doc`` sees the new file and takes it.

Clock jumps (a VPS without RTC, NTP after boot): the times in the files are only for people; on reading they are
clamped (``since``, ``tier_good_since`` ≤ now; the snapshot of states — ``clamp_states``), so a jump back neither
keeps an exit «removed» for hours nor stops the return to the primary tier.

traffic.json — ``{"version": 1, "exits": {"<exit id>": {"day", "day_bytes", "period_start", "period_bytes",
"last": {"rx", "tx"}, "warned": {"<period_start>": [80, 100]}}}}``: quotas do not depend on sqlite; ``last`` outlives a
restart of the collector, ``warned`` keeps a push of 80 % / 100 % from coming twice in one period.

A broken file -> ``<file>.bad-<ts>``, an empty table, a line in the journal (no push).
"""

import calendar
import json
import os
import re
import time

from .. import paths, tz, util
from . import exits          # exits.tag: the one place that makes «gx-<id>» (SPEC-stage1-exits §3.1, the role guard)
from .metrics import delta   # the rule of deltas (§3.5): one for the history and the quota

VERSION = 1
WHYS = ("new", "failover", "evict", "return", "rebalance")
LAST_STEPS = ("direct", "block")
SLOW_S = 60                                        # saved / tier_good_since alone; traffic.json
UNSTABLE_MAX_S = 15 * 60                           # unstable_until ≤ now + 15 min (§3.4)
OFF_MAX_S = 600                                    # off_backoff.max_s by default (§3.1)


class BadFile(ValueError):
    """The file is no pins.json / traffic.json of this version."""


def _mono():
    return time.monotonic()


def _int(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    return int(v)


def _sig(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def _read_json(path):
    """-> (doc or None when there is no file). BadFile when it does not parse."""
    raw = util.read_text(path)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise BadFile("json")


def _set_aside(path, why):
    """The broken file -> <file>.bad-<ts> and a line in the journal; the caller starts from an empty table."""
    bad = "%s.bad-%d" % (path, int(time.time()))
    try:
        os.rename(path, bad)
    except OSError:
        bad = None
    util.log_action("GW %s: broken (%s), set aside as %s, starting empty" % (os.path.basename(path), why, bad or "-"))
    return bad


def _ensure_dir(d):
    """Make a missing directory 0700; an existing one keeps its mode (/var/lib/kaskad/gw is shared with other files
    of the gateway, gw/inlog.py; the files here are 0600 anyway)."""
    if not os.path.isdir(d):
        util.ensure_dir(d, 0o700)


def _write(path, doc):
    _ensure_dir(os.path.dirname(path))
    util.atomic_write(path, util.json_text(doc), 0o600)


# ── pins.json ───────────────────────────────────────────────────────────────

def empty():
    return {"version": VERSION, "saved": 0, "groups": {}, "gw_all": None}


def empty_group():
    return {"pins": {}, "tier_good_since": {}, "active_tier": None, "last_step": None}


def normalize(doc, now=None):
    """A read pins.json -> a clean one: unknown fields dropped, broken entries dropped, times clamped to ``now``.
    BadFile when it is no pins.json of this version at all."""
    now = int(time.time() if now is None else now)
    if not isinstance(doc, dict) or doc.get("version") != VERSION or not isinstance(doc.get("groups", {}), dict):
        raise BadFile("version" if isinstance(doc, dict) else "type")
    out = empty()
    saved = _int(doc.get("saved"))
    out["saved"] = saved if saved and saved > 0 else 0
    gw_all = doc.get("gw_all")
    out["gw_all"] = gw_all if isinstance(gw_all, str) and gw_all else None
    for gid, g in (doc.get("groups") or {}).items():
        if not isinstance(gid, str) or not gid or not isinstance(g, dict):
            continue
        ng = empty_group()
        for did, p in (g.get("pins") or {}).items() if isinstance(g.get("pins"), dict) else ():
            if not isinstance(did, str) or not did or not isinstance(p, dict):
                continue
            ex, tier, since = p.get("exit"), p.get("tier"), _int(p.get("since"))
            if not isinstance(ex, str) or not ex or not isinstance(tier, str) or not tier:
                continue
            ng["pins"][did] = {"exit": ex, "tier": tier,
                               "since": min(since, now) if since is not None and since > 0 else now,
                               "why": p.get("why") if p.get("why") in WHYS else "new"}
        tgs = g.get("tier_good_since")
        for tid, ts in (tgs.items() if isinstance(tgs, dict) else ()):
            ts = _int(ts)
            if isinstance(tid, str) and tid and ts is not None and ts > 0:
                ng["tier_good_since"][tid] = min(ts, now)
        at = g.get("active_tier")
        ng["active_tier"] = at if isinstance(at, str) and at else None
        ng["last_step"] = g.get("last_step") if g.get("last_step") in LAST_STEPS else None
        if "told_tier" in g:                       # the tier the feed said (T-3379 З-1); none — assign takes active_tier
            tt = g.get("told_tier")
            ng["told_tier"] = tt if isinstance(tt, str) and tt else None
        out["groups"][gid] = ng
    return out


def read(now=None):
    """pins.json for the processes that only read it (ops, hooks, trace, the panel): no file or a broken one — an
    empty table (setting a broken file aside is the collector's: ``Store.load``)."""
    try:
        doc = _read_json(paths.pins_json())
        return empty() if doc is None else normalize(doc, now)
    except BadFile:
        return empty()


def decision_key(doc):
    """What other processes act on: the pins, the active tier and the last step of each group, gw_all. ``saved``,
    ``tier_good_since`` and ``since`` of a pin that stays are no part of it."""
    groups = {}
    for gid, g in sorted((doc.get("groups") or {}).items()):
        groups[gid] = {"pins": dict((did, (p.get("exit"), p.get("tier"), p.get("why")))
                                    for did, p in (g.get("pins") or {}).items()),
                       "active_tier": g.get("active_tier"), "last_step": g.get("last_step")}
    return json.dumps({"groups": groups, "gw_all": doc.get("gw_all")}, sort_keys=True)


def _rest(doc):
    """The whole table but ``saved``: to tell «nothing to write» from «only the slow fields changed»."""
    return json.dumps(dict(doc, saved=0, version=VERSION), sort_keys=True)


class Store(object):
    """pins.json of the collector: ``load`` at start, ``doc`` at each tick under the state lock (takes the file anew
    when another process rewrote it — ``handover_file``), ``save`` in the same tick before the lock is let go."""

    def __init__(self):
        self.path = paths.pins_json()
        self._sig = None
        self._doc = None
        self._key = None                           # decision_key of the file on the disk
        self._slow = None                          # monotonic time of the last write
        self._rest = None                          # _rest() of the file on the disk

    def load(self, now=None):
        """Read the file (start, after a reboot). Broken -> set aside, empty table."""
        try:
            raw = _read_json(self.path)
            doc = empty() if raw is None else normalize(raw, now)
        except BadFile as e:
            _set_aside(self.path, e)
            doc = empty()
        self._doc, self._sig = doc, _sig(self.path)
        self._key = decision_key(doc) if self._sig else None
        self._rest = _rest(doc) if self._sig else None
        return doc

    def doc(self, now=None):
        """The table now: the one in memory, or the file again when it changed on the disk since our last look. The
        file of another process (``handover_file``) brings the decision; ``tier_good_since`` and ``told_tier`` of the
        groups we still have are taken from memory — they are written at most once in 60 s, and the file may lag (the
        return to the primary tier must not start over, the feed must not say a fall it said already)."""
        if self._doc is None or _sig(self.path) != self._sig:
            mine = self._doc
            doc = self.load(now)
            for gid, g in ((mine or {}).get("groups") or {}).items():
                if gid in doc["groups"]:
                    doc["groups"][gid]["tier_good_since"] = dict(g.get("tier_good_since") or {})
                    if "told_tier" in g:
                        doc["groups"][gid]["told_tier"] = g["told_tier"]
            return doc
        return self._doc

    def save(self, doc, now=None, force=False):
        """Write ``doc`` when its decision changed — at once; when only ``saved``/``tier_good_since``/``since`` did —
        at most once in 60 s; ``force`` (SIGTERM) — at once. -> True when written."""
        now = int(time.time() if now is None else now)
        key = decision_key(doc)
        mono = _mono()
        if not force and key == self._key and self._sig is not None and _sig(self.path) == self._sig:
            if _rest(doc) == self._rest:
                self._doc = doc
                return False                       # nothing changed but ``saved``
            if self._slow is not None and mono - self._slow < SLOW_S:
                self._doc = doc
                return False                       # tier_good_since / since alone: once in 60 s
        doc["version"] = VERSION
        doc["saved"] = now
        _write(self.path, doc)
        self._doc, self._sig, self._key, self._slow = doc, _sig(self.path), key, mono
        self._rest = _rest(doc)
        return True


def prune(doc, allowed):
    """Drop the pins that may not stay: ``allowed`` — {gid: device ids whose route resolves into that group} built
    from the model (every device, not only the active ones). A group missing from ``allowed`` goes entirely (deleted);
    a device missing from its group — its pin goes (the device was deleted or its route changed: it is picked anew
    in the new group, ``why = "new"``). -> [(gid, device id), ...] removed."""
    gone = []
    for gid in list((doc.get("groups") or {}).keys()):
        keep = allowed.get(gid)
        if keep is None:
            gone.extend((gid, did) for did in sorted(doc["groups"][gid].get("pins") or {}))
            del doc["groups"][gid]
            continue
        keep = set(keep)
        pins = doc["groups"][gid].setdefault("pins", {})
        for did in sorted(pins):
            if did not in keep:
                gone.append((gid, did))
                del pins[did]
    return gone


def handover(doc, moves, where=None):
    """The pins of an exit that leaves the pool go to the other copy of its key (REV-335-2; ``ops.hand_over``): the
    same server, the same IP for the sites. ``moves`` — {exit that goes: exit that stays}; ``where(exit id)`` ->
    (gid, tier id) | None — where the copy that stays is placed (None: unknown, not placed). A pin moves when the copy
    stays in the same group (its tier taken); otherwise it is dropped and the collector picks anew (``why = "new"``).
    Without ``where`` the pin keeps its group and tier. ``since`` and ``why`` stay. -> the number of pins moved."""
    moves = dict((a, b) for a, b in (moves or {}).items() if a and b and a != b)
    n = 0
    for gid, g in (doc.get("groups") or {}).items():
        pins = g.get("pins") or {}
        for did in sorted(pins):
            p = pins[did]
            b = moves.get(p.get("exit"))
            if not b:
                continue
            if where is None:
                p["exit"] = b
                n += 1
                continue
            at = where(b)
            if at and at[0] == gid and at[1]:
                p["exit"], p["tier"] = b, at[1]
                n += 1
            else:
                del pins[did]
    for a, b in moves.items():                     # gw_all — only from «Основная» (§2.6); elsewhere the collector picks
        at = where(b) if where is not None else ("main", "")
        if doc.get("gw_all") == exits.tag(a) and at and at[0] == "main":   # gw-all: foreign exits only
            doc["gw_all"] = exits.tag(b)
    return n


def handover_file(moves, where=None, now=None):
    """``handover`` on pins.json itself. Only under the state lock (the collector writes the file under it too).
    No file — 0, nothing written."""
    path = paths.pins_json()
    try:
        raw = _read_json(path)
    except BadFile:
        return 0                                   # the collector sets it aside on its next start
    if raw is None:
        return 0
    try:
        doc = normalize(raw, now)
    except BadFile:
        return 0
    before = decision_key(doc)
    n = handover(doc, moves, where)
    if decision_key(doc) != before:
        doc["saved"] = int(time.time() if now is None else now)
        _write(path, doc)
    return n


def clamp_states(snap, now=None, off_max_s=OFF_MAX_S):
    """The snapshot of states (/etc/kaskad/gw/exits.json) after reading: ``off_until`` ≤ now + off_backoff.max_s,
    ``unstable_until`` ≤ now + 15 min, ``since``/``state_since`` ≤ now (§3.4). In place; -> ``snap``."""
    now = int(time.time() if now is None else now)
    for rec in ((snap or {}).get("exits") or {}).values():
        if not isinstance(rec, dict):
            continue
        for k, top in (("off_until", now + int(off_max_s)), ("unstable_until", now + UNSTABLE_MAX_S),
                       ("since", now), ("state_since", now)):
            v = _int(rec.get(k))
            if v is not None and v > top:
                rec[k] = top
    return snap


# ── traffic.json ────────────────────────────────────────────────────────────

def traffic_empty():
    return {"version": VERSION, "exits": {}}


_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")       # ASCII: «\d» takes any Unicode digit (REV-s1-t3b Н-а)


def _date(v):
    """``v`` when it is a real day 'YYYY-MM-DD', else None. The day and the period only go forward (``traffic_add``):
    a string of 10 that is no date ('2026/10/01', 'zzzzzzzzzz') would be later than any day and freeze them (REV Н2)."""
    if not isinstance(v, str) or not _DATE.match(v):
        return None
    y, m, d = int(v[:4]), int(v[5:7]), int(v[8:])
    if y < 1 or not 1 <= m <= 12 or not 1 <= d <= calendar.monthrange(y, m)[1]:
        return None                                # not time.strptime: its first call in a thread is not safe
    return v


def _traffic_rec(r):
    out = {"day": None, "day_bytes": 0, "period_start": None, "period_bytes": 0, "last": {"rx": None, "tx": None},
           "warned": {}}
    if not isinstance(r, dict):
        return out
    for k in ("day", "period_start"):
        out[k] = _date(r.get(k))
    for k in ("day_bytes", "period_bytes"):
        v = _int(r.get(k))
        out[k] = v if v is not None and v >= 0 else 0
    last = r.get("last") if isinstance(r.get("last"), dict) else {}
    for k in ("rx", "tx"):
        v = _int(last.get(k))
        out["last"][k] = v if v is not None and v >= 0 else None      # None: no point of reference yet
    w = r.get("warned") if isinstance(r.get("warned"), dict) else {}
    for ps, lv in w.items():
        if isinstance(ps, str) and isinstance(lv, list):
            out["warned"][ps] = sorted(set(x for x in (_int(v) for v in lv) if x is not None))
    return out


def traffic_normalize(doc):
    if not isinstance(doc, dict) or doc.get("version") != VERSION or not isinstance(doc.get("exits", {}), dict):
        raise BadFile("version" if isinstance(doc, dict) else "type")
    return {"version": VERSION, "exits": dict((eid, _traffic_rec(r)) for eid, r in (doc.get("exits") or {}).items()
                                              if isinstance(eid, str) and eid)}


def traffic_read():
    """traffic.json for readers (the panel, the backup): no file or a broken one — empty."""
    try:
        doc = _read_json(paths.traffic_json())
        return traffic_empty() if doc is None else traffic_normalize(doc)
    except BadFile:
        return traffic_empty()


class TrafficStore(object):
    """traffic.json of the collector: ``load`` at start (broken -> set aside), ``save`` at most once in 60 s."""

    def __init__(self):
        self.path = paths.traffic_json()
        self._slow = None

    def load(self):
        try:
            raw = _read_json(self.path)
            return traffic_empty() if raw is None else traffic_normalize(raw)
        except BadFile as e:
            _set_aside(self.path, e)
            return traffic_empty()

    def save(self, doc, force=False):
        mono = _mono()
        if not force and self._slow is not None and mono - self._slow < SLOW_S:
            return False
        doc["version"] = VERSION
        _write(self.path, doc)
        self._slow = mono
        return True


def period_start(quota, now=None, z=None):
    """'YYYY-MM-DD' — the first day of the quota period at ``now`` by the clock of the node: ``day`` — today;
    ``month`` — the last ``reset_day`` (1–28) not after today."""
    lt = tz.local(now, z)
    if (quota or {}).get("period") == "day":
        return "%04d-%02d-%02d" % (lt.tm_year, lt.tm_mon, lt.tm_mday)
    rd = _int((quota or {}).get("reset_day")) or 1
    rd = min(max(rd, 1), 28)
    y, m = lt.tm_year, lt.tm_mon
    if lt.tm_mday < rd:
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return "%04d-%02d-%02d" % (y, m, rd)


def next_reset(quota, now=None, z=None, start=None):
    """'YYYY-MM-DD' — the day the next quota period starts (the date of the reset in texts): the first day of the
    grid of the quota (``day`` — every day; ``month`` — ``reset_day``) after the period the exit counts in. ``start``
    — that period (``period_of``) when it is later than the clock gives; it may be off the grid after a change of
    ``reset_day`` or ``day`` -> ``month`` (the period only goes forward), the next point of the grid is still the
    reset (REV-s1-t3-2 Н1). A ``start`` that is no real day is ignored."""
    ps = period_start(quota, now, z)
    start = _date(start)
    if start is not None and start > ps:
        ps = start
    y, m, d = int(ps[:4]), int(ps[5:7]), int(ps[8:])
    if (quota or {}).get("period") == "day":
        t = time.gmtime(calendar.timegm((y, m, d, 12, 0, 0, 0, 0, 0)) + 86400)
        return "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
    rd = min(max(_int((quota or {}).get("reset_day")) or 1, 1), 28)
    if d >= rd:                                    # the grid point of this month is passed (or is the period itself)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return "%04d-%02d-%02d" % (y, m, rd)


def _counter(v):
    v = _int(v)
    return v if v is not None and v >= 0 else None


def traffic_add(doc, eid, rx, tx, now=None, quota=None, z=None):
    """The counters of exit ``eid`` read now (``rx``/``tx`` — the cumulative counters of xray or kawgN) -> (d_rx, d_tx)
    by the rule of deltas; the day and the quota period of the exit grow by them.

    * No point of reference (a new exit, a broken file set aside, an unreadable ``last``) -> the reading becomes it,
      the delta is 0: the whole counter since xray / kawgN came up is no traffic of this period.
    * A counter missing in this tick (``None``) -> its ``last`` stays, its delta is 0 (no double count next tick).
    * The day and the period only go forward: a new one starts when it is later than the recorded one. A change of
      the zone to the west or the clock jumping back (boot without RTC, §3.4) keep counting into the recorded day and
      period — the split changes from the nearest midnight (§3.7), the counters and ``warned`` are not wiped."""
    now = time.time() if now is None else now
    ex = doc.setdefault("exits", {})
    r = ex.get(eid)
    if not isinstance(r, dict) or _traffic_rec(r) != r:
        r = ex[eid] = _traffic_rec(r)                 # a new exit, or a record not read through the store
    d = {}
    for k, cur in (("rx", _counter(rx)), ("tx", _counter(tx))):
        last = r["last"][k]
        if cur is None:
            d[k] = 0                                  # not read this tick: keep the reference
            continue
        d[k] = 0 if last is None else delta(cur, last)
        r["last"][k] = cur
    got = d["rx"] + d["tx"]
    today = tz.day(now, z)
    if r["day"] is None or today > r["day"]:
        r["day"], r["day_bytes"] = today, 0
    r["day_bytes"] += got
    ps = period_start(quota, now, z)
    if r["period_start"] is None or ps > r["period_start"]:
        r["period_start"], r["period_bytes"] = ps, 0
        r["warned"] = dict((k, v) for k, v in r["warned"].items() if k == ps)
    r["period_bytes"] += got
    return d["rx"], d["tx"]


def traffic_new_leg(doc, eid):
    """The exit ``eid`` got a new leg (a change of role, SG §2.2 П3: a new ``kawgN`` and tag, their counters from 0):
    ``last`` = {rx: 0, tx: 0}, so the first reading of the new leg is all traffic of the period. Not None (that is
    «no point of reference»: the first reading would be lost) and not a removal of the record: the day, the period,
    their counters and ``warned`` stay — the quota is the tariff of the same server. -> the record."""
    ex = doc.setdefault("exits", {})
    r = ex[eid] = _traffic_rec(ex.get(eid))
    r["last"] = {"rx": 0, "tx": 0}
    return r


def period_of(doc, eid, quota, now=None, z=None):
    """The period ``eid`` counts in: the recorded one when it is not earlier than the clock gives (a zone moved west,
    the clock behind), else the clock's (its record has not started it yet)."""
    ps = period_start(quota, now, z)
    r = (doc.get("exits") or {}).get(eid)
    rp = _date(r.get("period_start")) if isinstance(r, dict) else None
    return rp if rp is not None and rp >= ps else ps


def used(doc, eid, quota, now=None, z=None):
    """Bytes of the current quota period of ``eid``: 0 when the clock is in a later period than the record (it has
    not started yet); the recorded counter when the clock is behind it (§3.4, §3.7)."""
    r = (doc.get("exits") or {}).get(eid)
    if not isinstance(r, dict) or r.get("period_start") != period_of(doc, eid, quota, now, z):
        return 0
    return int(r.get("period_bytes") or 0)


def warn_level(quota):
    """The first threshold of a quota, % — 50–99 (80 by default)."""
    return min(max(_int((quota or {}).get("warn_pct")) or 80, 50), 99)


def quota_due(doc, eid, quota, now=None, z=None):
    """The thresholds of the quota reached in this period and not pushed yet: [warn_pct] and/or [100]; they are marked
    pushed (``warned``) — saved with the file, a restart of the collector does not push them again.

    * A mark the current use is below again (the limit was raised, §5.6) is taken back: the push comes at the next
      crossing.
    * Only the pushes: the action of the quota is by ``used(...) >= limit``, not by this list (it is empty when the
      100 % was pushed before a restart).
    * When the list is not empty the caller writes the file at once (``TrafficStore.save(doc, force=True)``): a crash
      before the next write of the minute would push again."""
    from .policy import quota_gb
    gb = quota_gb(quota)               # 10**400 of an older version: no quota, never an OverflowError (REV-33714 С-1)
    if gb is None:
        return []
    limit = gb * 1e9
    r = (doc.get("exits") or {}).get(eid)
    if not isinstance(r, dict):
        return []
    ps = period_of(doc, eid, quota, now, z)
    pct = used(doc, eid, quota, now, z) * 100.0 / limit
    warned = r.setdefault("warned", {})
    told = [lv for lv in (warned.get(ps) or []) if pct >= lv]
    out = [lv for lv in (warn_level(quota), 100) if pct >= lv and lv not in told]
    mark = sorted(set(told + out))
    if mark != (warned.get(ps) or []) or (out and ps not in warned):
        warned[ps] = mark
    return out
