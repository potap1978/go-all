"""The schedule and the history of the checks of the server (k3/handoff/SPEC-node-score-schedule-share.md §2.6, §3.4,
§6.1, §6.3; B13 — the API of the panel, B14 — kaskadctl, MCP and the SSH menu over the SAME functions here).

One checker of a body (``bench.schedule.check_body`` + ``prepare``), one price (``cost``), one version of the saved
schedule (``etag``): the panel (``panel/host.py``), ``kaskadctl host bench schedule|history|show|compare|export`` and
the MCP tools (``host_bench_schedule`` action=add|on|off|rm; ``host_score`` action=schedule|history|show) give the
same errors and the same price. A refusal is ``Refused`` (the HTTP status for the panel, the exit code for kaskadctl, ``key`` — the machine
code of the answer).

kaskadctl (SS §6.1)::

    host bench schedule [list] [--json]          the list with numbers, the next checks, the price, armed
    host bench schedule cost [--json]            the price of the list as it is
    host bench schedule add --time HH:MM --mode quick|full --days 1-5|1,3,7|all [--no-quiet] [--off] [--monthly]
    host bench schedule rm|on|off <number|id>
        [--dry] — the change and its price and ``code``, nothing saved; [--yes] — agree to «much traffic» (heavy);
        [--code C] — save only the change ``--dry`` showed (the same list, the same version); [--by-mcp] — the agent:
        without ``--code`` it is ``--dry``, heavy refused (MCP asks no questions and never passes --yes; SS §6.3)
    host bench history [--days N | --from D --to D] [--mode M] [--by B] [--skipped] [--limit N] [--json]
    host bench show <id> [--json]
    host bench compare <id> <id> [--json]
    host bench export [--format csv|json] [--from D] [--to D] [--out FILE]

Exit codes: 0; 1 — a read failed; 2 — refused (the checker, heavy without consent, a bad argument); 3 — the schedule
changed meanwhile (``stale``) or the ``code`` is another change (``code_mismatch``).
"""

import argparse
import datetime
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time

from . import errors, state, tz
from .errors import KaskadError
from .i18n import fmt_num, tr

BODY_MAX = 16 * 1024            # bytes of a body of the schedule (12 entries are ≈ 2 KB)
HIST_TTL = 60.0                 # s: the scheduled checks for the price are read once in this time
COST_DAYS = 120                 # the price looks at the scheduled checks of these days (≥ 3 of a monthly full one)
BY = ("schedule", "panel", "bot", "cli", "mcp", "ssh", "auto")               # §3.1
STATUS = ("ok", "partial", "skipped", "failed", "cancelled")
RANGE_DAYS = 400                # §3.4: the period of the history and the export
YEARS = (2000, 2100)            # a date of a query: its year (the history never has others; 9999 overflowed, З-2)
CODE_LEN = 10                   # hex of the ``code`` of a change (as the tariff, C1)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_ETAG = re.compile(r'"[0-9a-f]{20}"')
_REF = re.compile(r"[0-9]{1,2}|[0-9a-f]{6}")
_HEX = re.compile(r"[0-9a-f]{%d}" % CODE_LEN)
_DAYS = re.compile(r"[1-7](-[1-7])?(,[1-7](-[1-7])?)*")

_HIST = {"mono": None, "rows": None}            # the scheduled checks for the price (the preview runs every 300 ms)
_HLOCK = threading.Lock()


class Refused(KaskadError):
    """A refusal of the schedule or the history: ``status`` — of the panel, ``key`` — the code of its answer,
    ``more`` — the other fields of the answer (``errors``, ``cost``, ``field``)."""

    def __init__(self, status, key, text, code=errors.USAGE, **more):
        KaskadError.__init__(self, text, code=code, data=dict(more, key=key))
        self.status, self.key, self.more = status, key, more


def reset():
    """Forget the cached history of the price (tests)."""
    with _HLOCK:
        _HIST["mono"], _HIST["rows"] = None, None


# ── the schedule: the version, the price, the timer ────────────────────────

def zone():
    """(zone, name | None, warn | None) — the zone of the clock of the schedule (``sched_unit._zone``: the name by
    ``fullmatch``; a bad one — UTC+03:00, the name None, ``tz_fallback``)."""
    from .bench import sched_unit
    z, warn = sched_unit._zone()
    return z, (None if warn else tz.name()), warn


def etag(m, tzname):
    """The version of the saved schedule as GET shows it (the list after the check at a read, ``retry``, the zone)."""
    raw = json.dumps({"s": m.get("schedule"), "r": m.get("retry"), "z": tzname}, sort_keys=True, ensure_ascii=True)
    return '"%s"' % hashlib.sha256(raw.encode("ascii")).hexdigest()[:20]


def _cost_history(now):
    """The rows of the scheduled checks (``history.runs``) for ``cost.estimate``, kept HIST_TTL s."""
    from .bench import history
    with _HLOCK:
        m = time.monotonic()
        if _HIST["rows"] is None or _HIST["mono"] is None or not 0 <= m - _HIST["mono"] < HIST_TTL:
            try:
                _HIST["rows"] = history.runs(frm=int(now) - COST_DAYS * 86400, by="schedule", limit=500,
                                             now=now)["runs"]
            except Exception:                          # noqa: BLE001 — no history: the price by the caps
                _HIST["rows"] = []
            _HIST["mono"] = m
        return list(_HIST["rows"])


def node_ru(st):
    """The node is in Russia for the price (SS §2.3: no upload of a scheduled check), never by the network: the country
    the product keeps (``bench.geo.country``: the geo pass of the exits, the setting «узел в России», the delays of
    an earlier check — the coordinator 19.10) -> the country of the server its VLESS record keeps
    (``vless.country_of`` without the network). Unknown — not in Russia (the caps of a foreign node: the price is never
    understated)."""
    try:
        from .bench import geo as bgeo
        ru = bgeo.country(st)["ru"]
        if ru is not None:
            return ru
    except Exception:                                  # noqa: BLE001 — a record that cannot be read: the next source
        pass
    try:
        from .gw.inbounds import vless
        return vless.country_of(st, detect=False).get("code") == "RU"
    except Exception:                                  # noqa: BLE001 — no VLESS part, an older state
        return False


def ru_point(st):
    """The upload point the owner allowed a node in Russia (NS-S5, ``bench.suite.ru_point``) for the price: False —
    none (or not a target the channel would use); else {mode: its cap, MB} or True (the caps of the modes)."""
    try:
        from .bench import channel, suite
        url = suite.ru_point(st, "quick")[0]
        if not url or channel.http_target({"id": "allowed", "url": url}) is None:
            return False
        caps = dict((m, suite.ru_point(st, m)[1]) for m in ("quick", "full") if suite.ru_point(st, m)[1])
        return caps or True
    except Exception:                                  # noqa: BLE001
        return False


def cost(entries, m, now, retry=None, st=None):
    """``cost.estimate`` of ``entries``: the traffic of a month of the tariff (C1, ``bench.tariff.traffic_limit_gb``:
    «≈ 3 % of the tariff», the threshold 10 %), the node in Russia (``node_ru``) and its allowed upload point
    (``ru_point``, NS-S5). ``st`` — the state the list is of (None — read now)."""
    from .bench import cost as bcost
    return bcost.estimate(entries, cost_ctx(m, now, retry=retry, st=st))


def cost_ctx(m, now, retry=None, st=None):
    """The context of ``cost.estimate`` (the same for the list, its preview and ``propose``)."""
    from .bench import tariff as btariff
    from .gw import model
    st = state.load() if st is None else st
    return {"history": _cost_history(now), "ru": node_ru(st), "ru_point": ru_point(st),
            "tariff_traffic_gb": btariff.traffic_limit_gb(model.tariff(st)[0]),
            "pkgs": m.get("pkgs"), "retry": m.get("retry", True) if retry is None else retry}


def _h1(now):
    """The hourly rows {ts, rx, tx} of host_h1 for ``schedule.propose`` (its 14 days); no base — []."""
    from .bench import schedule as bsched
    from .gw import metrics
    conn = metrics.reader()
    if conn is None:
        return []
    try:
        return [{"ts": ts, "rx": rx, "tx": tx} for ts, rx, tx in conn.execute(
            "SELECT ts, rx, tx FROM host_h1 WHERE ts >= ? AND ts < ?",
            (int(now) - bsched.H1_DAYS * 86400, int(now))).fetchall()]
    except Exception:                                  # noqa: BLE001 — no table yet: the default hours
        return []
    finally:
        try:
            conn.close()
        except Exception:                              # noqa: BLE001
            pass


def propose(m, st, now, z):
    """``schedule.propose`` for the empty list of the screen (B15; SS §1.3, NS-S18): the minutes are the node's own
    and the same at every GET (the random is seeded by the machine's HMAC, else the host name), the price — the same
    ``cost`` context as the list (``cost`` both, ``cost_quick`` the quick one). A failure — None (never the reason the
    schedule is not shown)."""
    import random
    import socket
    from .bench import schedule as bsched
    seed = "kaskad-propose:%s" % (m.get("mid") or socket.gethostname())
    rng = random.Random(int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16))
    try:
        return bsched.propose(_h1(now), now, z, rng=rng, ctx=cost_ctx(m, now, st=st))
    except Exception:                                  # noqa: BLE001
        return None


def errs_said(errs):
    from .bench import schedule as bsched
    return [dict(e, text=bsched.error_text(e)) for e in errs]


def invalid(errs, **more):
    said = errs_said(errs)
    return Refused(400, "invalid", said[0]["text"] if said else tr("bench.sched.err.body"), errors=said, **more)


def check(doc):
    """The JSON body of the schedule -> (entries, opts); errors of ``check_body`` — ``Refused`` 400 ``invalid``."""
    from .bench import schedule as bsched
    entries, opts, errs = bsched.check_body(doc)
    if errs:
        raise invalid(errs)
    return entries, opts


def timer(st):
    """{"active", "applied", "why"}: the timer on the disk against the one the state wants."""
    from .bench import sched_unit
    try:
        want, _warn, _lines = sched_unit.wanted(st)
        cur = sched_unit.current_timer()
    except Exception:                                  # noqa: BLE001
        return {"active": False, "applied": False, "why": "read"}
    if cur == want:
        return {"active": cur is not None, "applied": True, "why": None}
    why = "not_written" if cur is None else ("stale" if want is not None else "left")
    return {"active": cur is not None, "applied": False, "why": why}


def doc_of(m, st, now):
    """The answer of GET /api/host/bench/schedule (§2.6)."""
    from .bench import schedule as bsched
    z, tzname, warn = zone()
    doc = {"ok": True, "schedule": m["schedule"], "retry": m["retry"], "armed": m["armed"],
           "armed_why": m["armed_why"], "pkgs": m.get("pkgs"), "tz": tzname, "tz_warn": warn,
           "next": bsched.next_runs(m["schedule"], now, z, 5), "cost": cost(m["schedule"], m, now, st=st),
           "limits": dict(bsched.LIMITS), "timer": timer(st), "etag": etag(m, tzname)}
    if not m["schedule"]:
        doc["propose"] = propose(m, st, now, z)
    return doc


def read(now=None, lock_timeout=1.0):
    """GET /api/host/bench/schedule: the list as saved (a broken entry is saved off first, ``fix_state``)."""
    from .bench import fix_state
    now = time.time() if now is None else now
    m = fix_state(timeout=lock_timeout)
    return doc_of(m, state.load(), now)


def _next_draft(norm, now, z):
    """``next_runs`` of a draft: an entry without an id (new) runs under a temporary one, said as null."""
    from .bench import schedule as bsched
    taken = set(e["id"] for e in norm if e["id"])
    tmp = set()
    draft = []
    for e in norm:
        if not e["id"]:
            i = bsched.new_id(taken | tmp)
            tmp.add(i)
            e = dict(e, id=i)
        draft.append(e)
    return [dict(v, id=None) if v["id"] in tmp else v for v in bsched.next_runs(draft, now, z, 5)]


def preview(entries, opts, now=None):
    """POST /api/host/bench/schedule/preview: the list after ``prepare``, its errors, price and next checks, ``consent``
    — saving needs the human's «yes» (``needs_consent``: the traffic of a month grows to «much traffic»). Saves
    nothing."""
    from .bench import schedule as bsched
    from .gw import model
    now = int(time.time() if now is None else now)
    st = state.load()
    m = model.bench(st)
    norm, errs = bsched.prepare(entries, m["schedule"], now)
    if not errs:                                       # aligned 1:1 (no entry dropped): a new one has no id yet
        for raw, e in zip(entries, norm):
            if raw.get("id") is None:
                e["id"], e["since"] = None, None
    z, _tzname, _warn = zone()
    c = cost(norm, m, now, opts.get("retry"), st=st)
    return {"ok": True, "schedule": norm, "errors": errs_said(errs), "cost": c, "next": _next_draft(norm, now, z),
            "consent": needs_consent(cost(m["schedule"], m, now, m["retry"], st=st), c)}      # С-1: PUT wants «confirm»


def heavy(c):
    return any(n.get("code") == "heavy" for n in (c or {}).get("notes") or [])


GROW_MB = 0.5                   # MB: less is the noise of rounding, one check is hundreds of MB


def _month_mb(c):
    m = (c or {}).get("month") or {}
    return float(m.get("rx_mb") or 0) + float(m.get("tx_mb") or 0)


def _retry_mb(c):
    return float(((c or {}).get("retry") or {}).get("max_mb") or 0)


def needs_consent(old, new):
    """«Much traffic» asks the human only when the change makes the traffic GROW (REV-ns-b14 С-1): the new list is
    heavy and its month (or the cap of the repeats) is above the old one. To turn off, delete or make lighter a heavy
    list — never a question (the panel, kaskadctl, MCP and the SSH menu — one rule)."""
    if not heavy(new):
        return False
    return _month_mb(new) > _month_mb(old) + GROW_MB or _retry_mb(new) > _retry_mb(old) + GROW_MB


def save(entries, opts, got, now=None):
    """PUT /api/host/bench/schedule: ``got`` — the version the caller saw (``If-Match``). Under the lock of the state:
    the version, the checker, ``need_confirm``; then the timer (and the repeats waiting stop when ``retry`` goes
    off). -> the answer of GET + ``apply``; a refusal — ``Refused`` (409 ``tz_changed``/``stale``/``need_confirm``,
    400 ``invalid``)."""
    from .bench import fix_in, log_fixed, machine, sched_unit
    from .bench import schedule as bsched
    from .gw import model
    _z, tzname, _warn = zone()
    if "tz" in opts and opts["tz"] != tzname:
        raise Refused(409, "tz_changed", tr("host_sched.err.tz_changed"), code=errors.CONFLICT)
    now = int(time.time() if now is None else now)
    with state.transaction(timeout=5) as st:
        m = fix_in(st)                                 # the old list AFTER the fix: the ids GET gave (REV-ns-b10)
        if not (isinstance(got, str) and _ETAG.fullmatch(got) and got == etag(m, tzname)):
            raise Refused(409, "stale", tr("host_sched.err.stale"), code=errors.CONFLICT)
        norm, errs = bsched.prepare(entries, m["schedule"], now)
        if errs:
            raise invalid(errs)
        retry = opts.get("retry", m["retry"])
        c = cost(norm, m, now, retry, st=st)
        if needs_consent(cost(m["schedule"], m, now, m["retry"], st=st), c) and opts.get("confirm") is not True:
            raise Refused(409, "need_confirm", tr("host_sched.err.need_confirm"), cost=c)
        b = machine._cfg(st)
        b["schedule"] = norm
        if "retry" in opts:
            b["retry"] = opts["retry"]
        retry_off = m["retry"] and opts.get("retry") is False
    log_fixed(m)                                       # after the save (REV-ns-b10 П-2)
    runner = sched_unit.system_runner()
    st = state.load()                                  # the state as saved now (a later PUT wins here too)
    if runner is None:
        applied = {"applied": False, "why": "no_root"}             # «armed» — of the answer itself (REV-ns-b13 П-3)
    else:
        applied = sched_unit.apply(runner, st)
        if retry_off:                                  # the repeats still waiting go too (REV-ns-b13)
            sched_unit.stop_retries(runner)
    out = doc_of(model.bench(st), state.load(), now)
    out["apply"] = applied
    return out


# ── the history: dates and the export (§3.4) ───────────────────────────────

def date_of(v, field):
    """'YYYY-MM-DD' (fullmatch, a real date, the year in YEARS) -> date; empty -> None; else ``Refused`` 400."""
    if v is None or v == "":
        return None
    d = None
    if isinstance(v, str) and _DATE.fullmatch(v):
        try:
            d = datetime.date(int(v[:4]), int(v[5:7]), int(v[8:]))
        except ValueError:
            d = None
    if d is None or not YEARS[0] <= d.year <= YEARS[1]:
        raise Refused(400, "bad_date", tr("host_sched.err.date", field=field), field=field)
    return d


def _today(now):
    return datetime.date(*(int(x) for x in tz.day(now).split("-")))


def period(frm, to, now):
    """from/to (local days 'YYYY-MM-DD' or None) -> (frm, to) for ``history``: real dates, ``to`` not before ``from``,
    ≤ RANGE_DAYS days."""
    a, b = date_of(frm, "from"), date_of(to, "to")
    if a is not None:
        end = b if b is not None else _today(now)
        if end < a or (end - a).days + 1 > RANGE_DAYS:
            raise Refused(400, "bad_span", tr("host_sched.err.span", max=RANGE_DAYS), field="from")
    return (a.isoformat() if a else None), (b.isoformat() if b else None)


def export_body(fmt, frm, to, now=None):
    """The export (§3.4) -> (file name, content type, bytes). The name has no node; the fields — ``history``'s."""
    from .bench import history
    now = int(time.time() if now is None else now)
    z = tz.zone()
    lo, hi = history.span(frm, to, now)
    doc = history.details(frm, to, now)
    name = "kaskad-bench-%s-%s.%s" % (tz.day(lo, z).replace("-", ""), tz.day(hi - 1, z).replace("-", ""), fmt)
    if fmt == "csv":
        return name, "text/csv; charset=utf-8", history.to_csv(doc["runs"], z).encode("utf-8")
    body = json.dumps({"v": 1, "tz": tz.name(), "runs": history.for_export(doc["runs"])}, ensure_ascii=False,
                      sort_keys=True)
    return name, "application/json; charset=utf-8", body.encode("utf-8")


# ── kaskadctl: the changes of the list (§6.1) ──────────────────────────────

def parse_days(s):
    """'1-5' | '1,3,7' | 'all' (also '6-7', '1-3,5') -> sorted ISO days; else ``Refused`` 2."""
    v = str(s or "").strip().lower()
    if v == "all":
        return [1, 2, 3, 4, 5, 6, 7]
    days = set()
    if _DAYS.fullmatch(v):
        for part in v.split(","):
            a, _, b = part.partition("-")
            lo, hi = int(a), int(b or a)
            if lo > hi:
                days = set()
                break
            days.update(range(lo, hi + 1))
    if not days:
        raise Refused(400, "bad_days", tr("host_sched.err.days"), field="days")
    return sorted(days)


def _shown(v):
    """A reference of a person said back only when it is a plain number or id."""
    return v if isinstance(v, str) and _REF.fullmatch(v) else "?"


def find(entries, ref):
    """The index of the entry ``ref`` — its number in the list (1…) or its id (6 hex); else ``Refused`` 2."""
    r = str(ref or "").strip().lower()
    if _REF.fullmatch(r):
        if r.isdigit() and len(r) <= 2:
            n = int(r)
            if 1 <= n <= len(entries):
                return n - 1
        else:
            for i, e in enumerate(entries):
                if e.get("id") == r:
                    return i
    raise Refused(404, "no_entry", tr("host_sched.err.no_entry", ref=_shown(r)), field="entry")


def changed_list(old, op, ref=None, entry=None):
    """The list after the change ``op`` (add | rm | on | off) of the list ``old`` (as GET gives it)."""
    new = [dict(e) for e in old]
    if op == "add":
        new.append(entry)
        return new
    i = find(new, ref)
    if op == "rm":
        del new[i]
    else:
        new[i]["on"] = op == "on"
    return new


def change_code(tag, new):
    """The ``code`` of a change: the version it starts from + the list it gives (the tariff's way, C1)."""
    raw = json.dumps({"e": tag, "s": new}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()[:CODE_LEN]


def terminal_cmd(op, ref, entry, cur):
    """REV-ns-p3a П-5: the whole command of the terminal that does the change refused to MCP (``heavy``) — with its
    subcommand: ``kaskadctl host bench schedule`` alone only lists. The values are checked ones (``check`` passed);
    something unexpected — the subcommand with ``--help``."""
    base = "kaskadctl host bench schedule "
    try:
        if op == "add":
            days = sorted(int(d) for d in entry["days"])
            out = [base + "add", "--time", entry["time"], "--mode", entry["mode"], "--days",
                   "all" if days == list(range(1, 8)) else ",".join(str(d) for d in days)]
            if not entry.get("quiet", True):
                out.append("--no-quiet")
            if entry.get("monthly"):
                out.append("--monthly")
            if not _TIME_OK.fullmatch(entry["time"]) or entry["mode"] not in ("quick", "full"):
                raise ValueError(entry)
            return " ".join(out)
        if op in ("on", "off", "rm"):
            return base + "%s %d" % (op, find(cur["schedule"], ref) + 1)
    except (KeyError, TypeError, ValueError, Refused):
        pass
    return base + str(op if op in ("add", "on", "off", "rm") else "") + " --help"


_TIME_OK = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def change(op, ref=None, entry=None, dry=False, yes=False, code=None, by_mcp=False, ask=None, now=None):
    """One change of the list -> the answer: ``dry`` — {"dry": true, "schedule", "cost", "next", "heavy", "consent",
    "code", "errors": []} (``consent`` — saving needs the human's «yes», ``needs_consent``); else the answer of
    ``save`` + ``changed``. ``ask(cost)`` — the question «much traffic» in a
    terminal (True — save); None — no terminal: heavy without ``yes`` is refused."""
    now = int(time.time() if now is None else now)
    if by_mcp and not code:                            # the agent's first call: the preview with its code (SS §6.3)
        dry = True
    if code is not None and not (isinstance(code, str) and _HEX.fullmatch(code)):
        raise Refused(409, "code_mismatch", tr("host_sched.err.code"), code=errors.CONFLICT)
    cur = read(now)
    new = changed_list(cur["schedule"], op, ref, entry)
    entries, opts = check({"schedule": new})
    pv = preview(entries, opts, now)
    key = change_code(cur["etag"], new)
    if not dry and code is not None and not hmac.compare_digest(code, key):    # first: «look again» says more
        raise Refused(409, "code_mismatch", tr("host_sched.err.code"), code=errors.CONFLICT)
    if pv["errors"]:
        raise Refused(400, "invalid", pv["errors"][0]["text"], errors=pv["errors"], cost=pv["cost"])
    hv = heavy(pv["cost"])
    need = pv["consent"]                               # С-1: off, rm and a lighter list never ask
    if dry:
        return dict(pv, dry=True, heavy=hv, consent=need, code=key, op=op)
    confirm = False
    if need:
        if by_mcp:                                     # SS §6.3: MCP asks no questions — heavy is the human's
            raise Refused(409, "heavy", tr("host_sched.err.heavy_mcp", cmd=terminal_cmd(op, ref, entry, cur)),
                          cost=pv["cost"])
        if yes:
            confirm = True
        elif ask is not None:
            confirm = bool(ask(pv["cost"]))
            if not confirm:
                raise Refused(409, "cancelled", tr("host_sched.cancelled"), code=errors.ERROR)
        else:
            raise Refused(409, "need_confirm", tr("host_sched.err.need_yes"), cost=pv["cost"])
    out = save(entries, dict(opts, confirm=confirm), cur["etag"], now)
    out["changed"] = op
    return out


# ── kaskadctl: the words ───────────────────────────────────────────────────

def _mode_word(mode):
    return tr("host_bench.mode_in." + mode) if mode in ("quick", "full") else "?"


def days_word(days, monthly=False):
    d = sorted(days or [])
    if d == [1, 2, 3, 4, 5, 6, 7]:
        w = tr("host_sched.days.all")
    elif d == [1, 2, 3, 4, 5]:
        w = tr("host_sched.days.work")
    elif d == [6, 7]:
        w = tr("host_sched.days.weekend")
    else:
        w = ", ".join(tr("host_sched.day.%d" % x) for x in d)
    return tr("host_sched.days.monthly", days=w) if monthly else w


def _hm(ts, z=None):
    """The wall clock of the node at ``ts`` in the words of the reader (``fmt_datetime`` of a naive datetime)."""
    from .i18n import fmt_datetime
    t = tz.local(ts, z)
    return fmt_datetime(datetime.datetime(*t[:6]))


def _mb(v):
    """MB -> «≈ 520 МБ» or «≈ 1,5 ГБ» (decimal, as the price)."""
    v = float(v or 0)
    if v >= 1000:
        return tr("host_sched.gb", n=fmt_num(v / 1000.0, digits=1 if v < 10000 else 0))
    return tr("host_sched.mb", n=fmt_num(v))


def cost_lines(c):
    day, month = c.get("day") or {}, c.get("month") or {}
    lines = [tr("host_sched.cost", day=_mb((day.get("rx_mb") or 0) + (day.get("tx_mb") or 0)),
                month=_mb((month.get("rx_mb") or 0) + (month.get("tx_mb") or 0)),
                cpu=fmt_num(day.get("cpu_min") or 0, digits=1))]
    lines += ["  " + n["text"] for n in c.get("notes") or [] if n.get("text")]
    return lines


def entry_line(n, e):
    parts = [e.get("time") or "?", _mode_word(e.get("mode")), days_word(e.get("days"), e.get("monthly"))]
    if e.get("quiet"):
        parts.append(tr("host_sched.quiet"))
    parts.append(tr("host_sched.on") if e.get("on") else tr("host_sched.off"))
    line = "%2d) %s" % (n, " · ".join(parts))
    if not e.get("on") and e.get("off_why"):             # saved off by the check of the list (§2.1)
        line += " — " + tr("host_sched.off_why." + (e["off_why"] if e["off_why"] in _OFF_WHY else "other"))
    return line


_OFF_WHY = ("per_day", "full_per_day", "gap", "total")


def list_lines(d, screen=False):
    z, _name, _warn = zone()
    out = [tr("host_sched.title", tz=d.get("tz") or tr("host_sched.tz_unknown"))]
    if d.get("tz_warn"):
        out.append(tr("host_sched.tz_warn"))
    if not d["schedule"]:
        out.append(tr("host_sched.empty"))
    for i, e in enumerate(d["schedule"], 1):
        out.append(entry_line(i, e))
    out.append("")
    nx = d.get("next") or []
    out.append(tr("host_sched.next", at=_hm(nx[0]["ts"], z), mode=_mode_word(nx[0].get("mode")))
               if nx else tr("host_sched.next_none"))
    out += cost_lines(d["cost"])
    if d["schedule"] and not d.get("armed") and d.get("armed_why"):
        out.append(tr("host_sched.armed_why." + d["armed_why"]))
    out.append(tr("host_sched.retry_" + ("on" if d.get("retry") else "off")))
    t = d.get("timer") or {}
    if d.get("armed") and not t.get("applied"):
        out.append(tr("host_sched.timer_stale_screen" if screen else "host_sched.timer_stale"))
    return out


_BY = {"schedule", "panel", "bot", "cli", "mcp", "ssh", "auto"}


def run_line(n, r, z=None):
    """One check of the history: number, time, kind, who started it, the result."""
    by = r.get("by_text") or (tr("host_sched.by." + r["by"]) if r.get("by") in _BY else "?")
    head = "%2d) %s · %s · %s" % (n, _hm(r["ts"], z), _mode_word(r.get("mode")), by)
    st = r.get("status")
    if st in ("skipped", "cancelled", "failed"):
        why = r.get("why_text") or ""
        return head + " · " + tr("host_sched.status." + st) + ((": " + why) if why else "")
    tail = tr("host_sched.status." + st) if st in ("ok", "partial") else "?"
    return head + " · " + tail


def _test_name(t):
    return tr("host_sched.test." + t) if t in _TESTS else (t if re.fullmatch(r"[a-z0-9_]{1,24}", str(t)) else "?")


_TESTS = ("net_down", "net_up", "loss", "rtt", "jitter", "dns", "chacha_1t", "aes_1t", "cpu_1t", "cpu_mt",
          "steal_p95", "cpu_cv", "cpu_sustain", "cpu_canary_cv", "net_cv")


def _value(v, test):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "—"
    unit = tr("score.unit." + test) if test in _TESTS else ""
    return ("%s %s" % (fmt_num(v, digits=1 if abs(v) < 100 else 0), unit)).strip()


def show_lines(doc, z=None):
    out = [run_line(0, doc, z)[4:]]
    for t in doc.get("tests") or []:
        line = "  %s: %s" % (_test_name(t.get("test")), _value(t.get("value"), t.get("test")))
        if t.get("under_load"):
            line += " · " + tr("host_sched.under_load")
        out.append(line)
    cr = doc.get("cost_real") or {}
    if cr.get("rx") is not None or cr.get("tx") is not None:
        out.append(tr("host_sched.used", rx=_mb((cr.get("rx") or 0) / 1e6), tx=_mb((cr.get("tx") or 0) / 1e6)))
    return out


def compare_lines(doc, z=None):
    out = ["A: " + run_line(0, doc["a"], z)[4:], "B: " + run_line(0, doc["b"], z)[4:]]
    if doc.get("mode_differs"):
        out.append(tr("host_sched.mode_differs"))
    for r in doc.get("rows") or []:
        t = r.get("test")
        line = "  %s: %s → %s" % (_test_name(t), _value(r.get("a"), t), _value(r.get("b"), t))
        if isinstance(r.get("delta_pct"), (int, float)) and not isinstance(r.get("delta_pct"), bool):
            line += " (%s%s%%)" % ("+" if r["delta_pct"] > 0 else "", fmt_num(r["delta_pct"], digits=1))   # the panel since UI-C: ▲/▼ in units · %
        out.append(line)
    if doc.get("rel_a") is not None and doc.get("rel_b") is not None:     # NS-D33: by rel_total of both
        out.append(tr("host_sched.compare_rel", a=fmt_num(doc["rel_a"]), b=fmt_num(doc["rel_b"])))
    return out


# ── kaskadctl: the commands ────────────────────────────────────────────────

def _isatty():
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _ask_heavy(c, question=None):
    """«Много трафика» in a terminal: numbers, never y/n. -> True — save."""
    sys.stdout.write("\n".join(cost_lines(c)) + "\n\n " + (question or tr("host_sched.ask_heavy")) + "\n 1) " +
                     tr("host_sched.save") + "\n 2) " + tr("host_sched.cancel") + "\n" + tr("host_sched.choice"))
    sys.stdout.flush()
    try:
        got = sys.stdin.readline()
    except (KeyboardInterrupt, EOFError):
        return False
    return got.strip() == "1"


def _entry_of(args):
    from .bench import schedule as bsched
    if args.mode not in bsched.MODES:
        raise Refused(400, "bad_mode", tr("host_sched.err.mode"), field="mode")
    return {"time": args.time, "mode": args.mode, "days": parse_days(args.days), "on": not args.off,
            "quiet": not args.no_quiet, "monthly": bool(args.monthly)}


def cmd_schedule(args):
    from .cli import emit
    op = getattr(args, "sched_cmd", None) or "list"
    if op in ("list", "cost"):
        d = read()
        if op == "cost":
            emit(args, {"ok": True, "cost": d["cost"], "etag": d["etag"]}, cost_lines(d["cost"]))
        else:
            emit(args, d, list_lines(d, screen=getattr(args, "screen", False)))
        return errors.OK
    entry = _entry_of(args) if op == "add" else None
    js, by_mcp = getattr(args, "json", False), getattr(args, "by_mcp", False)
    ask = _ask_heavy if (_isatty() and not js and not by_mcp) else None
    doc = change(op, ref=getattr(args, "entry", None), entry=entry, dry=getattr(args, "dry", False),
                 yes=getattr(args, "yes", False), code=args.code, by_mcp=by_mcp, ask=ask)
    screen = getattr(args, "screen", False)
    if doc.get("dry"):
        lines = [tr("host_sched.dry")] + [entry_line(i, e) for i, e in enumerate(doc["schedule"], 1)]
        lines += [""] + cost_lines(doc["cost"])
        if doc["consent"]:
            lines.append(tr("host_sched.heavy_screen" if screen else "host_sched.heavy_dry"))
        if not screen:                                 # the code is for kaskadctl and the agent, not for the menu (З-5)
            lines.append(tr("host_sched.code", code=doc["code"]))
    else:
        lines = [tr("host_sched.saved." + op)] + list_lines(doc, screen=screen)
        if not (doc.get("apply") or {}).get("applied"):
            why = (doc.get("apply") or {}).get("why")
            lines.append(tr("host_sched.not_applied_root" if why == "no_root" else
                            "host_sched.timer_stale_screen" if screen else "host_sched.timer_stale"))
    emit(args, doc, lines)
    return errors.OK


def cmd_history(args):
    from .bench import history
    from .bench import schedule as bsched
    from .cli import emit
    now = int(time.time())
    frm, to = args.from_, args.to
    if args.days is not None:
        if frm or to:
            raise Refused(400, "bad_span", tr("host_sched.err.days_and_dates"), field="days")
        if not 1 <= args.days <= RANGE_DAYS:
            raise Refused(400, "bad_span", tr("host_sched.err.span", max=RANGE_DAYS), field="days")
        frm = (_today(now) - datetime.timedelta(days=args.days - 1)).isoformat()
    frm, to = period(frm, to, now)
    if args.mode is not None and args.mode not in bsched.MODES:
        raise Refused(400, "bad_mode", tr("host_sched.err.mode"), field="mode")
    if args.by is not None and args.by not in BY:
        raise Refused(400, "bad_by", tr("host_sched.err.by"), field="by")
    if not 1 <= args.limit <= history.MAX_LIMIT:
        raise Refused(400, "bad_limit", tr("host_sched.err.limit", max=history.MAX_LIMIT), field="limit")
    doc = history.runs(frm, to, mode=args.mode, by=args.by, status="skipped" if args.skipped else None,
                       limit=args.limit, now=now, rel=True)             # the rows of the panel (rel too, UI-C)
    doc = dict(doc, ok=True)
    z = tz.zone()
    lines = [run_line(i, r, z) for i, r in enumerate(doc["runs"], 1)] or [
        tr("host_sched.history_none_skipped" if args.skipped else "host_sched.history_none")]
    if doc.get("more"):
        lines.append(tr("host_sched.more_screen", n=fmt_num(len(doc["runs"]))) if getattr(args, "screen", False)
                     else tr("host_sched.more"))
    emit(args, doc, lines)
    return errors.OK


def _rid(v):
    from .bench import spool
    if not (isinstance(v, str) and spool.ID.fullmatch(v)):
        raise Refused(404, "not_found", tr("host_sched.err.not_found"), field="id")
    return v


def cmd_show(args):
    from .bench import history
    from .cli import emit
    doc = history.run(_rid(args.id), rel=True)     # as the panel's detail (rel.tests, UI-C)
    if doc is None:
        raise Refused(404, "not_found", tr("host_sched.err.not_found"), field="id")
    doc = dict(doc, ok=True)
    emit(args, doc, show_lines(doc, tz.zone()))
    return errors.OK


def cmd_compare(args):
    from .bench import history
    from .cli import emit
    doc = history.compare(_rid(args.a), _rid(args.b))
    if doc is None:
        raise Refused(404, "not_found", tr("host_sched.err.not_found"), field="id")
    doc = dict(doc, ok=True)
    emit(args, doc, compare_lines(doc, tz.zone()))
    return errors.OK


def cmd_export(args):
    from .cli import emit
    if args.format not in ("csv", "json"):
        raise Refused(400, "bad_format", tr("host_sched.err.format"), field="format")
    frm, to = period(args.from_, args.to, int(time.time()))
    name, _ctype, body = export_body(args.format, frm, to)
    if not args.out:
        out = getattr(sys.stdout, "buffer", None)
        if out is None:
            sys.stdout.write(body.decode("utf-8"))
        else:
            out.write(body)
        sys.stdout.flush()
        return errors.OK
    try:                                               # a new file only, the owner's (no other file is overwritten)
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Refused(409, "exists", tr("host_sched.err.exists"), code=errors.CONFLICT)
    except OSError as e:
        raise KaskadError(tr("host_sched.err.write", why=e.strerror or "?"), code=errors.ERROR)
    with os.fdopen(fd, "wb") as f:
        f.write(body)
    emit(args, {"ok": True, "file": args.out, "name": name, "bytes": len(body)},
         tr("host_sched.exported", n=fmt_num(len(body))))
    return errors.OK


def cmd(args):
    """``host bench <schedule|history|show|compare|export>`` (the timer's «schedule apply» stays in host_cli)."""
    bc = args.bench_cmd
    if bc == "schedule":
        return cmd_schedule(args)
    return {"history": cmd_history, "show": cmd_show, "compare": cmd_compare, "export": cmd_export}[bc](args)


# The flags ``host bench`` has itself (--json, --dry, --yes, --by-mcp) are SUPPRESS in its subcommands: given before the
# subcommand (``host bench --yes schedule add …``, ``host bench --dry schedule rm 1``) they stay — the default of a
# subparser overwrote the value of its parent (REV-ns-b14 П-1: --yes was lost, --dry would have saved).
_KEEP = argparse.SUPPRESS


def _json(p, add_json_flag=None):
    p.add_argument("--json", action="store_true", default=_KEEP, help=tr("cli.json_help"))
    return p


def _screen(p):
    """--screen: the words of the screen of the SSH menu (22 → 4, 22 → 5) — no flags and commands of kaskadctl, the
    hints lead to the items of the screen (REV-ns-b14 З-5, as ``host health --screen``)."""
    p.add_argument("--screen", action="store_true", help=argparse.SUPPRESS)
    return p


def _change_flags(p, add_json_flag=None):
    _json(p)
    _screen(p)
    p.add_argument("--dry", action="store_true", default=_KEEP, help=tr("host_sched.help.dry"))
    p.add_argument("--yes", action="store_true", default=_KEEP, help=tr("host_sched.help.yes"))
    p.add_argument("--code", metavar="C", help=tr("host_sched.help.code"))
    p.add_argument("--by-mcp", dest="by_mcp", action="store_true", default=_KEEP, help=argparse.SUPPRESS)


def register(bs, add_json_flag):
    """The subcommands of ``host bench`` (``bs`` — its subparsers)."""
    sp = _screen(_json(bs.add_parser("schedule", help=tr("host_cli.help.bench_schedule"))))
    ss = sp.add_subparsers(dest="sched_cmd")
    _screen(_json(ss.add_parser("list", help=tr("host_sched.help.list"))))
    _json(ss.add_parser("cost", help=tr("host_sched.help.cost")))
    ss.add_parser("apply", help=tr("host_cli.help.bench_apply"))
    a = ss.add_parser("add", help=tr("host_sched.help.add"))
    a.add_argument("--time", required=True, metavar="HH:MM", help=tr("host_sched.help.time"))
    a.add_argument("--mode", required=True, metavar="quick|full", help=tr("host_sched.help.mode"))
    a.add_argument("--days", required=True, metavar="1-5|1,3,7|all", help=tr("host_sched.help.days"))
    a.add_argument("--no-quiet", dest="no_quiet", action="store_true", help=tr("host_sched.help.no_quiet"))
    a.add_argument("--off", action="store_true", help=tr("host_sched.help.off"))
    a.add_argument("--monthly", action="store_true", help=tr("host_sched.help.monthly"))
    _change_flags(a, add_json_flag)
    for op in ("rm", "on", "off"):
        p = ss.add_parser(op, help=tr("host_sched.help." + op))
        p.add_argument("entry", metavar="N|ID", help=tr("host_sched.help.entry"))
        _change_flags(p, add_json_flag)
    h = _screen(_json(bs.add_parser("history", help=tr("host_sched.help.history"))))
    h.add_argument("--days", type=int, metavar="N", help=tr("host_sched.help.hdays"))
    h.add_argument("--from", dest="from_", metavar="YYYY-MM-DD", help=tr("host_sched.help.from"))
    h.add_argument("--to", metavar="YYYY-MM-DD", help=tr("host_sched.help.to"))
    h.add_argument("--mode", metavar="quick|full", help=tr("host_sched.help.mode"))
    h.add_argument("--by", metavar="|".join(BY), help=tr("host_sched.help.by"))
    h.add_argument("--skipped", action="store_true", help=tr("host_sched.help.skipped"))
    h.add_argument("--limit", type=int, default=200, metavar="N", help=tr("host_sched.help.limit"))
    s = _json(bs.add_parser("show", help=tr("host_sched.help.show")))
    s.add_argument("id", metavar="ID", help=tr("host_sched.help.id"))
    c = _json(bs.add_parser("compare", help=tr("host_sched.help.compare")))
    c.add_argument("a", metavar="ID", help=tr("host_sched.help.id"))
    c.add_argument("b", metavar="ID", help=tr("host_sched.help.id"))
    e = _json(bs.add_parser("export", help=tr("host_sched.help.export")))
    e.add_argument("--format", default="csv", metavar="csv|json", help=tr("host_sched.help.format"))
    e.add_argument("--from", dest="from_", metavar="YYYY-MM-DD", help=tr("host_sched.help.from"))
    e.add_argument("--to", metavar="YYYY-MM-DD", help=tr("host_sched.help.to"))
    e.add_argument("--out", metavar="FILE", help=tr("host_sched.help.out"))
