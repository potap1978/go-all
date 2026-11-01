"""The state machine of the exits (SPEC-stage1-exits §4.1–4.3, §4.6, §3.2 «упал», §5.5–5.6; T2). ONLY pure functions:
no command, no file, no clock — the time comes as a parameter. The collector calls them under its lock (T4).

    evaluate(prev, obs, policy, node, now) -> (health, events)

``prev``   {eid: ExitHealth} of the last tick (``from_snapshot`` after a restart), ``{}`` at the start;
``obs``    {eid: Obs} — every exit of the pool (both roles) this tick; an exit not in ``obs`` is gone (no record);
``policy`` the effective exit policy (``policy.load``);
``node``   {"offline": bool, "ru_offline": bool, "mono": monotonic s (durations; default ``now``),
            "pool": rows of the pool (kinds — the observatory interval), "geo_pass": {"direct_loc", "locs": {eid: loc}}
            — the result of an extra pass over the exits (§4.3 «Страна»), "tz": tzinfo of the node (texts)};
``now``    wall seconds (what goes to the files: ``since``, ``off_until``, ``unstable_until``, ``falls``).

Obs of an exit (every field optional)::

    {"kind": "awg"|"xray", "probes": [{"ok": bool, "rtt": ms|None, "id": last_try_time|None, "at": mono s}],
     "silent": bool (xray of the node silent / the leg does not come up — «упал» at once, no window),
     "enabled": bool, "manual": None|"standby"|"maintenance", "manual_why": None|"sub_confirm",
     "restore": bool (the one-shot «Вернуть» of the owner), "quota": exit_pref.quota|None,
     "traffic": {"used_gb", "day_gb", "month_gb", "period": quota_period(), "warned": [80, 100], "reset": "date"},
     "speed_mbps": float, "services": {sid: bool}, "geo": {"loc", "ip", "how": "plan"|"pass"|"confirm"},
     "country": exit_pref.country, "sub": id, "sub_name": name}

ExitHealth (a dict; ``snapshot``/``from_snapshot`` — the part kept in ``exits.json``)::

    {"state": "ok"|"degraded"|"standby"|"off"|"unknown", "reasons": [...], "since": wall, "off_until": wall|None,
     "falls": [wall], "unstable_until": wall|None, "unstable": bool, "recovering": bool, "shown": state of «unknown»,
     "tries": [[mono, 1|0]] (the window of «упал», in /run), "oks": n, "last_try_id": …,
     "rules": {rid: {"active", "then", "info", "leave": {...}, "back": {...}}}, "samples": [[mono, 1|0, rtt]],
     "norm_rtt": [24 hourly medians], "rtt_hour": [wall, [rtt]], "geo": {"loc", "ip", "pending", "told_all"},
     "speed": mbps, "services": {sid: bool}, "quota": {"period", "warned", "full", "restored", "action"}}

Reasons -> state: dead, disabled, maintenance, quota (action remove), rule:<id> (then remove) -> off; manual_standby,
unstable, quota (standby), rule (standby) -> standby; rule (degraded) -> degraded; unknown — «как прошлое
состояние», no new devices. The rule «упал» is the one of 3.3.5 (``exits.WINDOW`` …), P — of the group of the exit.

Events — {"kind", "exit", "group", "role", ...args of SPEC §3.6}: exit_dead, exit_alive, exit_degraded,
exit_recovered, exit_standby, exit_removed, exit_rule_push, exit_quota, exit_country_changed; «transition» (every
change of the state, for metrics.db, before the antispam); requests for the collector (``"request": True``):
geo_pass, geo_confirm (after ``after_s``), country_first (write exit_pref.country.expected), country_notified (add
``loc`` to exit_pref.country.notified), country_notified_clear (the expected country is back: empty it, §3.1).

A record of the snapshot of 3.3.5 (``alive``…) is read by ``from_snapshot`` as its state (``_legacy``); after the
network of the node is back the collector calls ``node_online`` (the windows anew, §4.6).
"""

import copy
import datetime
import math
import statistics

from . import exits, policy as P
from .probe import geo as probe_geo
from ..i18n import tr, trn

WINDOW = exits.WINDOW                  # the constants of the rule «упал» — the ones of 3.3.5, no second set (§3.2)
DEAD_SHARE = exits.DEAD_SHARE
MIN_TRIES = exits.MIN_TRIES
RISE = exits.RISE
SLACK = exits.CHECK_SLACK
LAZY_AGE = 3600                        # «Лениво»: the last MIN_TRIES checks, each not older than an hour
UNSTABLE_S = P.UNSTABLE_S
FLAP_FALLS = P.FLAP_FALLS
GEO_CONFIRM_S = 300
PUSH_EVERY_S = 3600                    # exit_rule_push of one rule: once an hour at the most
DAY = 86400
SEVERITY = {"ok": 0, "degraded": 1, "standby": 2, "off": 3}
USABLE = ("ok", "degraded")            # «годен» (§4.2); standby — only as a reserve (B2)
PROBED = ("ok", "degraded", "standby")  # probed all along: a change between them keeps the window of «упал» (Н12)
_WHY_ORDER = ("dead", "disabled", "maintenance", "quota", "country")
MSK = datetime.timezone(datetime.timedelta(hours=3))     # Europe/Moscow (no DST since 2014): the default zone (§3.7)


# ── records ────────────────────────────────────────────────────────────────

def fresh(now):
    """The record of an exit nobody has checked yet: «unknown», no new devices."""
    return {"state": "unknown", "reasons": ["unknown"], "since": now, "off_until": None, "falls": [],
            "unstable_until": None, "unstable": False, "recovering": False, "shown": None, "tries": [], "oks": 0,
            "last_try_id": None, "rules": {}, "samples": [], "norm_rtt": [], "rtt_hour": None,
            "geo": {"loc": None, "ip": None, "pending": None, "told_all": None}, "speed": None, "services": {},
            "quota": {}}


SNAPSHOT_KEYS = ("state", "reasons", "since", "off_until", "falls", "unstable_until", "unstable", "recovering",
                 "norm_rtt", "geo", "shown", "rules", "quota")
RULE_KEPT = ("active", "then", "info", "since", "pushed_at")   # a rule in the snapshot: its state, not its windows
MANUAL = ("disabled", "maintenance", "manual_standby")         # the owner's own states (3.3.5 had «disabled» too)


def snapshot(h):
    """The part of ExitHealth kept in ``exits.json`` (§3.4): state, reasons, state_since, off_until, falls,
    unstable_until, unstable, recovering, norm_rtt, geo, the rules (active, then, info, since, pushed_at — «вернуть
    только вручную» and «только уведомить» survive a restart; the holds start anew) and the quota (period, warned,
    full, restored — the owner's «Вернуть» survives it, action) — the window of «упал» lives in /run, not here."""
    out = dict((k, copy.deepcopy(h.get(k))) for k in SNAPSHOT_KEYS)
    out["state_since"] = out.pop("since")
    rules = out.get("rules") if isinstance(out.get("rules"), dict) else {}
    out["rules"] = dict((rid, dict((k, rh[k]) for k in RULE_KEPT if k in rh)) for rid, rh in sorted(rules.items())
                        if isinstance(rh, dict) and (rh.get("active") or _num(rh.get("pushed_at")) is not None))
    out["quota"] = out.get("quota") if isinstance(out.get("quota"), dict) else {}
    return out


def _legacy(rec):
    """A record of the snapshot of 3.3.5 (``alive``, ``fails``, ``since``, ``new``; no ``state``) -> the fields of
    ExitHealth: alive — «ok», dead (``exits.state_of``) — «off» (dead), else «unknown». ``legacy`` — the first tick
    after the update does not tell what 3.3.5 already had (a switched-off exit, §1.3 «Как раньше»)."""
    was = exits.state_of(rec)
    out = {"legacy": True}
    if was == "alive":
        out.update(state="ok", reasons=[])
    elif was == "dead":
        out.update(state="off", reasons=["dead"])
    if _num(rec.get("since")) is not None:
        out["since"] = rec["since"]
    return out


def from_snapshot(rec, now, policy=None):
    """ExitHealth from a record of the snapshot (a restart of the collector; a record of 3.3.5 — ``_legacy``): the
    windows are empty (soft rules wait for their samples, «упал» — for MIN_TRIES checks), the times clamped
    (``clamp``)."""
    h = fresh(now)
    rec = rec if isinstance(rec, dict) else {}
    if "state" not in rec and "alive" in rec:
        h.update(_legacy(rec))
    for k in SNAPSHOT_KEYS:
        if k in rec:
            h[k] = copy.deepcopy(rec[k])
    if "state_since" in rec:
        h["since"] = rec["state_since"]
    if not isinstance(h.get("reasons"), list):
        h["reasons"] = []
    if h.get("state") not in ("ok", "degraded", "standby", "off", "unknown"):
        h["state"] = "unknown"
    if not isinstance(h.get("geo"), dict):
        h["geo"] = fresh(now)["geo"]
    if not isinstance(h.get("quota"), dict):
        h["quota"] = {}
    rules = h.get("rules") if isinstance(h.get("rules"), dict) else {}
    h["rules"] = {}
    for rid, rh in rules.items():
        if isinstance(rh, dict):
            rh = dict((k, rh[k]) for k in RULE_KEPT if k in rh)
            rh.update(active=bool(rh.get("active")), leave={}, back={})
            for k in ("since", "pushed_at"):
                if _num(rh.get(k)) is not None:
                    rh[k] = min(rh[k], now)
            h["rules"][rid] = rh
    return clamp(h, now, policy)


def node_online(prev, policy=None, role=None):
    """The network of the node is back (§4.6, ``node_online`` / ``node_ru_online``): the windows of the exits start
    anew — the checks before and during the outage say nothing of now (the window of «упал», the samples of the soft
    rules and their holds; the states, the pauses and the active rules stay). The collector (T4) calls it in the tick
    that tells the network back, before ``evaluate``. ``role`` None — every exit; "abroad" / "ru" — the exits of the
    groups of that role (an exit outside the groups counts as foreign). -> a new {eid: ExitHealth}."""
    out = {}
    for eid, h in (prev or {}).items():
        h = copy.deepcopy(h)
        grp = P.group_of(policy, eid) if isinstance(policy, dict) else None
        if role is None or (grp or {}).get("role", "abroad") == role:
            h["tries"] = []
            h["oks"] = 0
            h["samples"] = []
            for rh in (h.get("rules") or {}).values():
                if isinstance(rh, dict):
                    rh["leave"] = {}
                    rh["back"] = {}
        out[eid] = h
    return out


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def clamp(h, now, policy=None):
    """Values a jump of the clock could have pushed into the future (§3.4, «Скачки часов»): ``off_until`` ≤ now +
    ``off_backoff.max_s``, ``unstable_until`` ≤ now + 15 min, ``since`` and the falls ≤ now. In place; -> h."""
    mx = _backoff(policy)["max_s"]
    if _num(h.get("off_until")) is not None and h["off_until"] > now + mx:
        h["off_until"] = now + mx
    if _num(h.get("unstable_until")) is not None and h["unstable_until"] > now + UNSTABLE_S:
        h["unstable_until"] = now + UNSTABLE_S
    if _num(h.get("since")) is None or h["since"] > now:
        h["since"] = now
    h["falls"] = [min(t, now) for t in (h.get("falls") or []) if _num(t) is not None]
    return h


def _backoff(policy):
    ob = (((policy or {}).get("probes") or {}).get("off_backoff")) or {}
    d = P.default_probes()["off_backoff"]
    return dict((k, ob.get(k, d[k]) if _num(ob.get(k)) is not None else d[k]) for k in d)


def _window_n(policy):
    w = ((policy or {}).get("probes") or {}).get("window")
    return w if isinstance(w, int) and not isinstance(w, bool) and w > 0 else 10


# ── the rule «упал» (§3.2) ─────────────────────────────────────────────────

def window(tries, at, lazy=False):
    """The checks of the window at ``at``: those younger than WINDOW s; fewer than MIN_TRIES — the last MIN_TRIES
    (lazy: each not older than an hour). ``tries`` — the checks since the window last started anew: a fall, a return,
    unknown or off — not a change between ok, degraded and standby (Н12, ``_events``)."""
    recent = [t for t in tries if 0 <= at - t[0] < WINDOW]
    if len(recent) >= MIN_TRIES:
        return recent
    old = [t for t in tries if at - t[0] >= 0 and (not lazy or at - t[0] <= LAZY_AGE)]
    return old[-MIN_TRIES:]


def _count(tries, ok, at, p):
    """One check into the window: a new one if at least P − 2 s passed since the last counted (a good one cancels a
    failed one up to P, as 3.3.5), else it REPLACES the result of the last (§3.2, Н1). In place."""
    gap = at - tries[-1][0] if tries else None
    if gap is not None and (gap < p - SLACK or (ok and not tries[-1][1] and gap < p)):
        tries[-1] = [tries[-1][0], 1 if ok else 0]
    else:
        tries.append([at, 1 if ok else 0])
    keep = max(len([t for t in tries if at - t[0] < WINDOW]), MIN_TRIES)
    del tries[:-keep]


def is_dead(tries, at, lazy=False):
    win = window(tries, at, lazy)
    n = len(win)
    f = n - sum(t[1] for t in win)
    return n >= MIN_TRIES and f >= DEAD_SHARE * n


# ── one exit ───────────────────────────────────────────────────────────────

class _Ctx(object):
    def __init__(self, doc, node, now):
        self.doc = doc
        self.node = node or {}
        self.now = now
        self.mono = _num(self.node.get("mono"))
        if self.mono is None:
            self.mono = now
        self.pool = self.node.get("pool")
        self.events = []
        self.expected = {}

    def ev(self, kind, eid, grp, **kw):
        e = dict(kw, kind=kind, exit=eid, group=(grp or {}).get("id"), role=(grp or {}).get("role", "abroad"))
        self.events.append(e)
        return e


def _fall(h, reasons, ctx):
    """«упал»: reason dead, a fall counted, the pause «снят» from the falls of the last hour (30 s, 1, 2 … 10 min)."""
    bo = _backoff(ctx.doc)
    reasons.add("dead")
    reasons.discard("unknown")
    h["falls"] = [t for t in h.get("falls") or [] if ctx.now - t < max(bo["reset_after_s"], UNSTABLE_S)] + [ctx.now]
    k = len([t for t in h["falls"] if ctx.now - t < bo["reset_after_s"]])
    h["off_until"] = ctx.now + min(bo["base_s"] * 2 ** (k - 1), bo["max_s"])
    h["oks"] = 0
    h["tries"] = []
    h["unstable_until"] = None
    reasons.discard("unstable")


def _sample(h, at, ok, rtt, ctx):
    n = max(_window_n(ctx.doc), 30)
    h["samples"] = (h.get("samples") or []) + [[at, 1 if ok else 0, _num(rtt)]]
    del h["samples"][:-n]
    if _num(rtt) is None:
        return
    hr = h.get("rtt_hour")
    if not hr or ctx.now - hr[0] >= 3600 or ctx.now < hr[0]:
        if hr and hr[1]:
            h["norm_rtt"] = ((h.get("norm_rtt") or []) + [statistics.median(hr[1])])[-24:]
        h["rtt_hour"] = [ctx.now, [rtt]]
    else:
        hr[1].append(rtt)


def _probe(h, reasons, pr, p, lazy, ctx):
    tid = pr.get("id")
    if tid is not None and tid == h.get("last_try_id"):
        return False                       # the same probe of xray read again: not one more check (§5.1)
    if tid is not None:
        h["last_try_id"] = tid
    at = _num(pr.get("at"))
    at = ctx.mono if at is None else at
    ok = bool(pr.get("ok"))
    _sample(h, at, ok, pr.get("rtt"), ctx)
    if "dead" in reasons:
        h["oks"] = h.get("oks", 0) + 1 if ok else 0
        return True
    if ok:
        reasons.discard("unknown")
    _count(h["tries"], ok, at, p)
    if not ok and is_dead(h["tries"], at, lazy):
        _fall(h, reasons, ctx)
    return True


def _period_of(ctx, grp, kind):
    if grp is None:
        return exits.CHECK_PERIOD
    return P.period(ctx.doc, grp, kind, ctx.pool)


def _sample_period(h, p):
    ats = [s[0] for s in (h.get("samples") or [])][-11:]
    gaps = [b - a for a, b in zip(ats, ats[1:]) if b > a]
    return statistics.median(gaps) if gaps else p


# ── conditions (§3.3, §4.3 «Выдержка») ─────────────────────────────────────

def _win_samples(h, at, side_hold, wn):
    ss = [s for s in h.get("samples") or [] if s[0] <= at]
    t = _num((side_hold or {}).get("minutes"))
    if t is not None:
        return [s for s in ss if s[0] > at - t * 60], True
    return ss[-wn:], False


def _cond(c, h, o, grp, at, hold, ctx):
    """True | False | None (unknown) of one condition at the sample ``at``."""
    m, op, v = c.get("metric"), c.get("op"), c.get("value")
    checks = (grp or {}).get("checks") or {}
    if m == "rtt":
        s = next((x for x in reversed(h.get("samples") or []) if x[0] <= at), None)
        val = s[2] if s else None
        if val is None:
            return None, None
        if c.get("unit") == "x_norm":
            norm = statistics.median(h["norm_rtt"]) if h.get("norm_rtt") else None
            if not norm:
                return None, None              # less than an hour of data: unknown
            val = val / float(norm)
    elif m in ("loss", "jitter"):
        wn = _window_n(ctx.doc)
        ss, by_time = _win_samples(h, at, hold, wn)
        if not ss or (not by_time and len(ss) < wn):
            return None, None                  # the window has not got its samples yet (§3.4, start)
        if m == "loss":
            f = len([s for s in ss if not s[1]])
            val = 100.0 * f / len(ss)
            if op == ">" and f < (c.get("min_fails") or P.MIN_FAILS_DEFAULT):
                return False, val
        else:
            rt = [s[2] for s in ss if s[2] is not None]
            if len(rt) < 2:
                return None, None
            val = statistics.pstdev(rt)
    elif m == "speed":
        if not (checks.get("speed") or {}).get("enabled") or _num(h.get("speed")) is None:
            return None, None
        val = h["speed"]
    elif m == "service":
        val = (h.get("services") or {}).get(c.get("service"))
        if val is None:
            return None, None
        return (val == v) if op == "==" else (val != v), val
    elif m in ("country", "country_changed"):
        if not (checks.get("geo") or {}).get("enabled", True):
            return None, None
        loc = (h.get("geo") or {}).get("loc")
        if not loc:
            return None, None
        if m == "country_changed":
            exp = _expected(grp, o)
            if not exp:
                return None, None
            val = loc != exp
            return val == v, val
        return (loc == v) if op == "==" else (loc != v), loc
    elif m == "traffic":
        t = o.get("traffic") or {}
        val = _num(t.get("day_gb" if c.get("unit") == "gb_day" else "month_gb"))
        if val is None:
            return None, None
    else:
        return None, None
    if _num(v) is None:
        return None, val
    return ((val > v) if op == ">" else (val < v)), val


def _side(side, h, o, grp, at, ctx):
    vals = []
    info = None
    for c in side.get("conds") or []:
        r, val = _cond(c, h, o, grp, at, side.get("hold"), ctx)
        vals.append(r)
        if r is True and info is None:
            info = {"metric": c.get("metric"), "value": val, "threshold": c.get("value"), "unit": c.get("unit")}
    if not vals:
        return False, None
    if side.get("match", "all") == "any":
        return any(x is True for x in vals), info
    return all(x is True for x in vals), info


def _hold(sd, hold, val, at, period):
    """The hold of a side (§4.3): ``samples`` N — N different samples in a row; ``minutes`` T — true in every sample
    of T and at least ceil(0.8·T / period) samples there (period — the real interval of this exit's samples)."""
    if not val:
        sd.clear()
        sd["last_false"] = at
        return False
    sd["n"] = sd.get("n", 0) + 1
    hold = hold if isinstance(hold, dict) else {"samples": 1}
    t = _num(hold.get("minutes"))
    if t is None:
        return sd["n"] >= int(hold.get("samples") or 1)
    times = [x for x in sd.get("times") or [] if x >= at - t * 60] + [at]
    sd["times"] = times
    lf = sd.get("last_false")
    need = int(math.ceil(0.8 * t * 60 / max(period, 1)))
    return (lf is None or lf < at - t * 60) and len(times) >= need


def _applies(rule, eid, kind, grp):
    sel = rule.get("exits", "all")
    if sel == "all":
        return True
    if isinstance(sel, list):
        return eid in sel
    if isinstance(sel, dict):
        return sel.get("kind") == kind
    return False


def _rules(h, o, eid, kind, grp, points, p, ctx):
    """Advance the rules of the group over the new samples ``points``; -> the rules that fired now."""
    rules = [r for r in (grp or {}).get("rules") or [] if isinstance(r, dict) and r.get("id")]
    have = dict((r["id"], r) for r in rules if r.get("enabled", True) and _applies(r, eid, kind, grp))
    hits = h.setdefault("rules", {})
    for rid in list(hits):
        if rid not in have:
            del hits[rid]                      # the rule went, was switched off or no longer covers the exit
    fired = []
    period = _sample_period(h, p)
    for rid, r in sorted(have.items()):
        rh = hits.setdefault(rid, {"active": False, "leave": {}, "back": {}})
        rh["then"] = r.get("then")
        for at in points:
            if not rh["active"]:
                val, info = _side(r.get("when") or {}, h, o, grp, at, ctx)
                if _hold(rh["leave"], (r.get("when") or {}).get("hold"), val, at, period):
                    rh.update(active=True, leave={}, back={}, info=info, since=ctx.now)
                    fired.append(r)
            else:
                back = r.get("back")
                if back is None:                 # «only notify» without a return: re-armed when «when» is false
                    val, _ = _side(r.get("when") or {}, h, o, grp, at, ctx)
                    if not val:
                        rh.update(active=False, leave={}, back={})
                    continue
                if back.get("manual"):
                    continue                     # back only by hand («Вернуть»)
                val, _ = _side(back, h, o, grp, at, ctx)
                if _hold(rh["back"], back.get("hold"), val, at, period):
                    rh.update(active=False, leave={}, back={})
    return fired


# ── country (§4.3 «Страна», §5.5) ──────────────────────────────────────────

def _expected(grp, o):
    exp = (((grp or {}).get("checks") or {}).get("geo") or {}).get("expect")
    if exp:
        return exp
    c = (o or {}).get("country") or {}
    return c.get("expected") if isinstance(c, dict) else None


def _geo(h, o, eid, grp, ctx):
    geo = (((grp or {}).get("checks") or {}).get("geo")) or {"enabled": True, "confirm": 2}
    if not geo.get("enabled", True):
        return
    g = h.setdefault("geo", fresh(0)["geo"])
    exp = _expected(grp, o)
    res = o.get("geo") or {}
    loc = res.get("loc")
    pass_ = ctx.node.get("geo_pass") or {}
    if loc is None and eid in (pass_.get("locs") or {}):
        loc = pass_["locs"][eid]
        res = {"loc": loc, "how": "pass"}
    if not loc:
        return
    if res.get("ip"):
        g["ip"] = res["ip"]
    if not exp:                                # the first country seen is the expected one (exit_pref.country)
        g["loc"] = loc
        g["pending"] = None
        ctx.ev("country_first", eid, grp, loc=loc, request=True)
        return
    if g.get("loc") is None and loc == exp:
        g["loc"] = loc
    if loc == g.get("loc"):
        g["pending"] = None
        return
    pend = g.get("pending")
    if not pend or pend.get("loc") != loc:     # first sight of a change: not a change yet — the extra pass
        g["pending"] = {"loc": loc, "n": 0, "at": ctx.now}
        if loc != exp:
            ctx.ev("geo_pass", eid, grp, loc=loc, request=True)
        else:
            g["pending"]["n"] = 1
            ctx.ev("geo_confirm", eid, grp, after_s=GEO_CONFIRM_S, request=True)
        return
    if res.get("how") == "pass" and loc != exp:
        expected = dict(ctx.expected, **{eid: exp})
        if probe_geo.suspect(eid, loc, expected, pass_.get("locs") or {}, pass_.get("direct_loc"),
                             role=(grp or {}).get("role")):
            g["pending"] = None                # a broken measure, most likely: nothing taken out, a push once a day
            if not g.get("told_all") or ctx.now - g["told_all"] >= DAY:
                g["told_all"] = ctx.now
                ctx.ev("exit_country_changed", eid, grp, old=exp, new=loc, all_exits=True)
            return
    pend["n"] = pend.get("n", 0) + 1
    if pend["n"] < int(geo.get("confirm") or 2):
        ctx.ev("geo_confirm", eid, grp, after_s=GEO_CONFIRM_S, request=True)
        return
    old = g.get("loc")
    g["loc"] = loc
    g["pending"] = None
    notified = ((o.get("country") or {}).get("notified")) or []
    if loc == exp:                             # the expected country again: «notified» is cleared (§3.1)
        if notified:
            ctx.ev("country_notified_clear", eid, grp, request=True)
        return
    if loc in notified:
        return                                 # the same new country is never pushed twice
    ctx.ev("exit_country_changed", eid, grp, old=old or exp, new=loc, all_exits=False)
    ctx.ev("country_notified", eid, grp, loc=loc, request=True)


# ── quota (§5.6) ───────────────────────────────────────────────────────────

def quota_period(quota, local_date):
    """The key of the current period of a quota at the local date (y, m, d) of the node (§3.7): «day» — the date,
    «month» — the last reset day (``reset_day``) not after it."""
    y, m, d = local_date
    if (quota or {}).get("period") == "day":
        return "%04d-%02d-%02d" % (y, m, d)
    rd = int((quota or {}).get("reset_day") or 1)
    if d < rd:
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return "%04d-%02d-%02d" % (y, m, rd)


def next_reset(quota, local_date):
    """(y, m, d) of the next reset of the quota after the local date."""
    y, m, d = local_date
    if (quota or {}).get("period") == "day":
        nd = datetime.date(y, m, d) + datetime.timedelta(days=1)
        return nd.year, nd.month, nd.day
    rd = int((quota or {}).get("reset_day") or 1)
    if d < rd:
        return y, m, rd
    return (y + 1, 1, rd) if m == 12 else (y, m + 1, rd)


def _quota(h, reasons, o, eid, grp, ctx):
    q = o.get("quota")
    qs = h.setdefault("quota", {})
    had = "quota" in reasons
    was_remove = had and qs.get("action") == "remove"
    t = o.get("traffic") or {}
    used, per = _num(t.get("used_gb")), t.get("period")
    lim = P.quota_gb(q)                # 10**400, 1e-320 of an older file: no quota, not an OverflowError (С-1)
    if lim is None:
        reasons.discard("quota")
    elif used is not None:
        if per is not None and per != qs.get("period"):
            for k in ("warned", "full", "restored"):
                qs.pop(k, None)
            qs["period"] = per
        pct = 100.0 * used / lim
        told = t.get("warned")
        warn = int(q.get("warn_pct") or 80)
        told = set(told) if isinstance(told, (list, tuple, set)) else ({warn} if told else set())
        if warn <= pct < 100 and qs.get("warned") != per and warn not in told:
            qs["warned"] = per
            ctx.ev("exit_quota", eid, grp, used_gb=used, limit_gb=lim, pct=int(pct), reset=t.get("reset"))
        act = q.get("action") or "remove"
        if pct >= 100:
            if qs.get("full") != per and 100 not in told:
                qs["full"] = per
                ctx.ev("exit_quota", eid, grp, used_gb=used, limit_gb=lim, pct=int(pct), reset=t.get("reset"))
            if act in ("remove", "standby") and qs.get("restored") != per:
                reasons.add("quota")
                qs["action"] = act
            else:
                reasons.discard("quota")
        else:
            reasons.discard("quota")           # the limit raised above the used
    if was_remove and not ("quota" in reasons and qs.get("action") == "remove"):
        reasons.discard("dead")                # back from «снят по квоте»: unknown until the first sample (О8)
        reasons.add("unknown")
        h["shown"] = h.get("state") if h.get("state") != "unknown" else h.get("shown")
        h["tries"] = []
        h["oks"] = 0
    return "quota" in reasons and qs.get("action") == "remove"


# ── state ──────────────────────────────────────────────────────────────────

def _of_reason(r, h):
    if r in ("dead", "disabled", "maintenance"):
        return "off"
    if r == "quota":
        return "off" if (h.get("quota") or {}).get("action") == "remove" else "standby"
    if r in ("manual_standby", "unstable"):
        return "standby"
    if r.startswith("rule:"):
        then = ((h.get("rules") or {}).get(r[5:]) or {}).get("then")
        return {"degraded": "degraded", "standby": "standby", "remove": "off"}.get(then)
    return None


def _state(reasons, h):
    """(state, why): the strictest of the active reasons; only «unknown» — «unknown»."""
    best, why = "ok", None
    order = list(_WHY_ORDER) + sorted(r for r in reasons if r not in _WHY_ORDER)
    for r in order:
        if r not in reasons:
            continue
        s = _of_reason(r, h)
        if s and SEVERITY[s] > SEVERITY[best]:
            best, why = s, r
    if best == "ok" and "unknown" in reasons:
        return "unknown", "unknown"
    return best, why


def _why_word(why, h, o):
    if why == "manual_standby":
        return "sub_confirm" if (o or {}).get("manual_why") == "sub_confirm" else "manual"
    return why


def evaluate(prev, obs, policy, node, now):
    """One tick of every exit (§4.1–4.3). -> ({eid: ExitHealth}, [event])."""
    doc = policy if isinstance(policy, dict) else P.load({})
    ctx = _Ctx(doc, node, now)
    prev = prev or {}
    obs = obs or {}
    out = {}
    work = {}
    for eid, o in obs.items():
        ctx.expected[eid] = _expected(P.group_of(doc, eid), o or {})
    for eid, o in sorted(obs.items()):
        o = o or {}
        grp, ti = P.tier_of(doc, eid)
        old = prev.get(eid)
        if ctx.node.get("offline") or (ctx.node.get("ru_offline") and (grp or {}).get("role") == "ru"):
            out[eid] = clamp(copy.deepcopy(old), now, doc) if old else fresh(now)
            continue                           # the node without a network: frozen, no samples, no events (§4.6)
        h = clamp(copy.deepcopy(old), now, doc) if old else fresh(now)
        reasons = set(h.get("reasons") or [])
        reasons -= {"disabled", "maintenance", "manual_standby", "unstable"}
        h["recovering"] = False
        h["_returned"] = False
        if not o.get("enabled", True):
            reasons.add("disabled")
        if o.get("manual") == "maintenance":
            reasons.add("maintenance")
        elif o.get("manual") == "standby":
            reasons.add("manual_standby")
        if o.get("restore"):                   # «Вернуть»: unstable, a rule of manual return, the quota of this period
            h["unstable_until"] = None
            for rid, rh in (h.get("rules") or {}).items():
                rh.update(active=False, leave={}, back={})
            if "quota" in reasons:
                per = (o.get("traffic") or {}).get("period")
                h.setdefault("quota", {})["restored"] = per
        kind = "xray" if exits.is_xray(o) or o.get("kind") == "xray" else (o.get("kind") or "awg")
        paused = _quota(h, reasons, o, eid, grp, ctx)
        p = _period_of(ctx, grp, kind)
        lazy = P.is_lazy(grp)
        points = []
        judged = not paused and o.get("enabled", True)    # К2-7: off for the gateway — no probes of it, no «упал»
        if judged:
            if o.get("silent") and "dead" not in reasons:
                _fall(h, reasons, ctx)
            for pr in o.get("probes") or []:
                if isinstance(pr, dict) and _probe(h, reasons, pr, p, lazy, ctx):
                    at = _num(pr.get("at"))
                    points.append(ctx.mono if at is None else at)
        if o.get("speed_mbps") is not None:
            h["speed"] = _num(o.get("speed_mbps"))
        if isinstance(o.get("services"), dict):
            h.setdefault("services", {}).update(o["services"])
        if not paused:
            _geo(h, o, eid, grp, ctx)
        if not points and any(o.get(k) is not None for k in ("speed_mbps", "services", "geo", "traffic")):
            points = [ctx.mono]
        fired = []
        if grp is not None and not paused:
            fired = _rules(h, o, eid, kind, grp, points, p, ctx)
        reasons = set(r for r in reasons if not r.startswith("rule:"))
        for rid, rh in (h.get("rules") or {}).items():
            if rh.get("active") and rh.get("then") in ("degraded", "standby", "remove"):
                reasons.add("rule:" + rid)
        for r in fired:
            rh = h["rules"][r["id"]]
            if r.get("then") == "push" and (_num(rh.get("pushed_at")) is None or now - rh["pushed_at"] >= PUSH_EVERY_S):
                rh["pushed_at"] = now                  # «только уведомить»: not more often than once an hour (§4.3)
                info = rh.get("info") or {}
                ctx.ev("exit_rule_push", eid, grp, rule=r["id"], metric=info.get("metric"), value=info.get("value"),
                       threshold=info.get("threshold"))
        if "dead" in reasons and h.get("oks", 0) >= RISE:
            if grp is None or not pauses(grp) or _num(h.get("off_until")) is None or now >= h["off_until"]:
                _back(h, reasons)
            else:
                h["recovering"] = True         # answers again, the pause holds it (§4.3, Н5): no events
        h["reasons"] = sorted(reasons)
        h["state"] = _state(reasons, h)[0]
        if old is not None and old.get("legacy"):
            old = _legacy_view(old, reasons)
        h.pop("legacy", None)
        work[eid] = (h, o, grp, ti, old)
        out[eid] = h
    _groups(work, ctx)
    for eid, (h, o, grp, ti, old) in sorted(work.items()):
        _events(eid, h, o, grp, old, ctx)
        h.pop("_returned", None)
    return out, ctx.events


def _legacy_view(old, reasons):
    """The record of 3.3.5 as the first tick compares it: the owner's own states of this tick (a switched-off exit)
    were there before the update — told then, not again."""
    rs = set(old.get("reasons") or []) | (set(reasons) & set(MANUAL))
    if rs - {"unknown"}:
        rs.discard("unknown")
    view = dict(old, reasons=sorted(rs))
    view["state"] = _state(rs, view)[0]
    if view["state"] != "unknown" and old.get("state") == "unknown":
        view["shown"] = None
    return view


def pauses(grp):
    """The pause «снят» holds an exit of ``grp`` that answers again (§4.3) — the policies of stage 1 only: «Как раньше»
    (``compat``) is 3.3.5 — «снова работает» after RISE good checks in a row, no pause (REV-s1-t4 З4, §1.3 отличие 3)."""
    return (grp or {}).get("preset", "compat") != "compat"


def _back(h, reasons):
    reasons.discard("dead")
    h["off_until"] = None
    h["tries"] = []
    h["oks"] = 0
    h["recovering"] = False
    h["_returned"] = True


def _groups(work, ctx):
    """The decisions that need the other exits of the group after their own evaluation: the pause never holds the
    last usable exit of a group (Р7); an unstable exit goes «в запас» only while the group has another usable one
    (§4.3, правка 5)."""
    by_group = {}
    for eid, (h, o, grp, ti, old) in work.items():
        if grp is not None:
            by_group.setdefault(grp["id"], (grp, []))[1].append((ti, eid))
    now = ctx.now
    for gid, (grp, members) in sorted(by_group.items()):
        members.sort()
        hs = dict((eid, work[eid][0]) for _, eid in members)
        cands = [eid for _, eid in members if hs[eid].get("recovering")]
        if cands:
            others = [e for e in hs if e not in cands and hs[e]["state"] in ("ok", "degraded", "standby")]
            if not others:                     # without it the group would stand on its last step: at once
                h = hs[cands[0]]
                reasons = set(h["reasons"])
                _back(h, reasons)
                h["reasons"] = sorted(reasons)
                h["state"] = _state(reasons, h)[0]
        if grp.get("flap_standby"):
            for eid, h in hs.items():
                if h.get("_returned"):
                    recent = [t for t in h.get("falls") or [] if now - t < UNSTABLE_S]
                    if len(recent) >= FLAP_FALLS:
                        h["unstable_until"] = now + UNSTABLE_S
        else:
            for h in hs.values():
                h["unstable_until"] = None
        flaky = set(e for e, h in hs.items() if _num(h.get("unstable_until")) is not None
                    and h["unstable_until"] > now and h["state"] != "off")
        for eid, h in hs.items():
            if _num(h.get("unstable_until")) is not None and h["unstable_until"] <= now:
                h["unstable_until"] = None
            h["unstable"] = eid in flaky
            if eid not in flaky:
                continue
            others = [e for e, x in hs.items() if e != eid and e not in flaky and x["state"] in USABLE]
            reasons = set(h["reasons"])
            if others:
                reasons.add("unstable")
            else:
                reasons.discard("unstable")    # the only usable one: stays in work, marked «нестабилен»
            h["reasons"] = sorted(reasons)
            h["state"] = _state(reasons, h)[0]
    for eid, (h, o, grp, ti, old) in work.items():
        if grp is None:
            h["unstable_until"] = None
            h["unstable"] = False


def _events(eid, h, o, grp, old, ctx):
    now = ctx.now
    new = h["state"]
    if old is None:
        h["since"] = now
        if new not in ("ok", "unknown"):
            why = _state(set(h["reasons"]), h)[1]
            _transition(eid, grp, None, new, why, h, o, ctx)
        return
    was = old.get("state")
    was_seen = old.get("shown") if was == "unknown" else was
    if new == "unknown":
        if was != "unknown":
            h["shown"] = was
        return
    if new == was and set(old.get("reasons") or []) == set(h["reasons"]):
        return
    dead_before = "dead" in (old.get("reasons") or [])
    dead_now = "dead" in h["reasons"]
    why = _state(set(h["reasons"]), h)[1]
    if new != was and not dead_now and not (was in PROBED and new in PROBED):
        # the window of «упал» starts anew on a change of the state (§3.2) — not between ok, degraded and standby: the
        # exit was probed all along, its checks stand (Н12, T-KT1: back from «в запасе», a 15 s outage gave «упал» in
        # 16 s — 2 failures of the 4 checks since the return instead of the window of 2 min)
        h["tries"] = []
    if new != was and new != was_seen:                            # «unknown» back to what it showed: no change
        h["since"] = now
        ctx.events.append({"kind": "transition", "exit": eid, "group": (grp or {}).get("id"),
                           "role": (grp or {}).get("role", "abroad"), "from": was_seen, "to": new,
                           "why": _why_word(why, h, o)})
    h["shown"] = None
    if dead_now and not dead_before:
        ctx.ev("exit_dead", eid, grp, since=now)
        return
    if dead_before and not dead_now and new in USABLE:
        ctx.ev("exit_alive", eid, grp)
        if new == "degraded":
            _degraded_event(eid, h, grp, ctx)
        return
    if new == was_seen and not (dead_before and not dead_now):
        if new == "standby" and "unstable" in h["reasons"] and "unstable" not in (old.get("reasons") or []):
            _standby_event(eid, h, o, grp, ctx, "unstable")
        return
    _transition(eid, grp, was_seen, new, why, h, o, ctx, old)


def _transition(eid, grp, was, new, why, h, o, ctx, old=None):
    if new == "degraded":
        _degraded_event(eid, h, grp, ctx)
    elif new == "standby":
        _standby_event(eid, h, o, grp, ctx, _why_word(why, h, o))
    elif new == "off":
        ctx.ev("exit_removed", eid, grp, why=_why_word(why, h, o))
    elif new == "ok" and was in ("degraded", "standby", "off"):
        since = (old or {}).get("since")
        ctx.ev("exit_recovered", eid, grp, **{"from": was, "after_s": int(ctx.now - since) if _num(since) else None})


def _degraded_event(eid, h, grp, ctx):
    rid = next((r[5:] for r in h["reasons"] if r.startswith("rule:")
                and (h["rules"].get(r[5:]) or {}).get("then") == "degraded"), None)
    info = ((h.get("rules") or {}).get(rid) or {}).get("info") or {}
    ctx.ev("exit_degraded", eid, grp, rule=rid, metric=info.get("metric"), value=info.get("value"),
           threshold=info.get("threshold"))


def _standby_event(eid, h, o, grp, ctx, why):
    kw = {"why": why}
    if why == "unstable":
        kw["flaps"] = len([t for t in h.get("falls") or [] if ctx.now - t < UNSTABLE_S])
        kw["until"] = h.get("unstable_until")
    if why == "sub_confirm":
        kw["sub"] = (o or {}).get("sub")
        kw["sub_name"] = (o or {}).get("sub_name")
    ctx.ev("exit_standby", eid, grp, **kw)


# ── texts (§4.3 «Показ паузы», «Нестабильный выход») ───────────────────────

def _hhmm(ts, tz=None):
    return datetime.datetime.fromtimestamp(ts, tz or MSK).strftime("%H:%M")


def pause_text(h, lang=None, tz=None):
    """«отвечает снова · новые устройства — с ЧЧ:ММ» while the pause holds an exit that answers again; else ""."""
    if not h.get("recovering") or _num(h.get("off_until")) is None:
        return ""
    return tr("ep.show.recovering", lang, time=_hhmm(h["off_until"], tz))


def unstable_text(h, now, lang=None, tz=None):
    """«в запасе: нестабилен (N падений за 15 мин), вернётся в ЧЧ:ММ» (standby) or «нестабилен · до ЧЧ:ММ» (in work);
    "" — not unstable."""
    until = _num(h.get("unstable_until"))
    if not h.get("unstable") or until is None:
        return ""
    if "unstable" in (h.get("reasons") or []):
        n = len([t for t in h.get("falls") or [] if now - t < UNSTABLE_S])
        return trn("ep.show.standby_unstable", n, lang, time=_hhmm(until, tz))
    return tr("ep.show.unstable", lang, time=_hhmm(until, tz))
