"""The scheduler of the unit of probes (docs/k3/SPEC-stage1-exits §4.3 «Страна», §5.3–§5.7; stage 1, T6).

One run (``kaskad-gw-probe.service``, every 60 s by its timer) decides by the schedules and the budgets what is due and
does it; ``kaskad-gw-probe@<exit id>.service`` is the manual speed test («Проверить скорость сейчас»). It reads the
policy (``groups[].checks`` of the group of an exit, ``probes.speed``), ``exit_pref`` (``speed_skip``, ``quota``,
``country``), the snapshot of the states (``exits.json``) and ``traffic.json`` (the quiet of an exit) and writes:

* ``/var/lib/kaskad/gw/probe-results.json`` — atomically, ``{"seq", "results": [{"seq", "exit", "kind", "ts", …}]}``;
  kinds ``speed`` (a row of ``metrics.speed``), ``svc`` (``metrics.svc``), ``geo`` (``ip``, ``loc``, ``how``: ``plan`` |
  ``pass`` | ``confirm``) and ``geo_pass`` (``direct_loc``, ``locs`` — the extra pass, §4.3; ``node.geo_pass`` of
  ``health``). The collector takes the results with a ``seq`` it has not seen; the last ``RESULTS_KEEP`` stay.
* ``/var/lib/kaskad/gw/probe-state.json`` — its own: when what is due, the budgets, the pending country.

Rules:

* An exit removed by its quota with ``quota.action = remove`` is skipped whole — no speed, no services, no country, not
  even in the extra pass — until it is back (§5.6, §5.7).
* Speed: only an exit of a group with ``checks.speed.enabled`` and without ``exit_pref.speed_skip``; every
  ``every_h`` ± 20 %; «only when quiet» (the traffic of the exit for the last minute < 1 Mbit/s; ``probes.speed.
  only_quiet``); no quiet for 2 intervals — the test anyway, with half the volume; one test on the node at a time
  (``flock /run/kaskad/speedtest.lock``, ``LOCK_NB`` — skipped); exits ≥ 60 s apart; the budget of a day and a month
  per exit (``probes.speed``, counted by the bytes of the tests) — a test that does not fit does not start, the event
  ``speedtest_budget`` once a period. A manual test: the same lock and budget; over the day's budget only ``force``.
* Services: each of ``checks.services`` every ``every_min`` ± 20 %.
* An exit switched off by the owner (``exit_pref.enabled = false``) is not probed at all.
* Time: every probe has one deadline for its whole request (``fetch.total_s``) and never runs past the run's own
  (``RUN_BUDGET_S`` < ``TimeoutStartSec``); a probe that would not fit what is left starts in the next run. A failure
  of one probe is a row with ``ok: false`` — the run goes on and writes what it gathered (also on SIGTERM).
* A speed test is written at once: its budget is spent (checked under the lock against the state on the disk) together
  with ``speed_due``/``last_speed`` before it starts and corrected by the bytes received right after it — a run killed
  in between never repeats the test nor loses its budget.
* Country: ``checks.geo.enabled = false`` — no request at all. Otherwise every ``geo.every_h`` ± 20 % (exits ≥ 60 s
  apart) and after each return from «dead»; the first sight of a country other than the expected one — at once the
  extra pass over every exit with ``geo.enabled`` (no spacing, one timeout for all) and the node's direct check, then a
  confirmation of this exit in 5 min (unless the pass shows a broken measure). The collector may ask more
  (``request``: ``geo_pass``, ``geo_confirm`` of ``health``).
"""

import concurrent.futures
import contextlib
import fcntl
import json
import os
import random
import signal
import time

from ... import paths, state, sysrun, tz, util
from ...errors import KaskadError
from .. import exits, model, pins, policy as P
from ..metrics import delta
from . import fetch, geo, services, speed

STATE_VERSION = 1
RESULTS_KEEP = 300
JITTER = 0.2                    # ± 20 % of every schedule
SPACING_S = 60                  # planned tests of the exits at least this far apart (speed; country)
CONFIRM_S = 300                 # the confirmation of a new country 5 min after the pass (§4.3)
PASS_TIMEOUT_S = 180            # one timeout for the whole extra pass (§4.3) — and never past the run's budget
PASS_WORKERS = 8
RUN_BUDGET_S = 100              # the service has TimeoutStartSec=120: nothing new starts after this
QUIET_BPS = 1000 * 1000 // 8    # «тихо»: < 1 Mbit/s through the exit for the last minute
SVC_PER_RUN = 12
SPEED_EVERY_H = (1, 3, 6, 12, 24)
EVERY_MIN = (5, 1440)
GEO_EVERY_H = (1, 168)
REQUEST_KINDS = ("geo_pass", "geo_confirm")
PASS_DEDUP_S = 600              # a request for a pass of an exit the unit passed this long ago — done already

_now = time.time                # tests replace these
_mono = time.monotonic
_rand = random.uniform


def state_path():
    return os.path.join(paths.gw_var_dir(), "probe-state.json")


def lock_path():
    return paths.P("/run/kaskad/probe.lock")


def speed_lock_path():
    return paths.P("/run/kaskad/speedtest.lock")


def requests_path():
    return paths.P("/run/kaskad/probe-requests.json")


def jitter(seconds):
    """``seconds`` ± 20 %."""
    return seconds * _rand(1.0 - JITTER, 1.0 + JITTER)


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return default if v != v else min(max(v, lo), hi)


# ── files ──────────────────────────────────────────────────────────────────

def _read_json(path, default):
    try:
        doc = json.loads(util.read_text(path) or "null")
    except ValueError:
        doc = None
    return doc if isinstance(doc, dict) else default


def _empty_state():
    return {"version": STATE_VERSION, "exits": {}, "last_speed": 0, "last_geo": 0, "seq": 0}


def read_state():
    doc = _read_json(state_path(), None)
    if not doc or doc.get("version") != STATE_VERSION or not isinstance(doc.get("exits"), dict):
        return _empty_state()
    return doc


def read_results():
    """``probe-results.json`` (readers: the collector, the panel, ``kaskadctl``): none or broken — empty."""
    doc = _read_json(paths.probe_results(), None)
    if not doc or not isinstance(doc.get("results"), list):
        return {"seq": 0, "results": []}
    return doc


@contextlib.contextmanager
def flocked(path, block=True):
    """flock on ``path``: yields True when held, False when busy (``block=False``: LOCK_NB)."""
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        if d.startswith(paths.run_dir()):
            util.ensure_run_dir(d)
        else:
            util.ensure_dir(d, 0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def _touch(path):
    """The mtime of the speed test's lock = the end of a test (gw/hostping.py reads it; SPEC-node-score §3.3)."""
    try:
        os.utime(path)
    except OSError:
        pass


def _seq(v):
    try:
        return max(int(v or 0), 0)
    except (TypeError, ValueError):
        return 0


def commit(results=(), mutate=None, taken=()):
    """Under the lock of the files: re-read the state and the results, ``mutate(state)``, append ``results`` with new
    ``seq`` numbers, take the done requests ``taken`` off their file, write all atomically (a run and a manual test may
    commit at the same time: each applies its own change to what is on the disk). ``seq`` is kept in the state too: a
    removed or broken ``probe-results.json`` never starts it again from 0 (the collector would skip the new results).
    -> the results as written."""
    with flocked(lock_path()):
        st = read_state()
        if mutate:
            mutate(st)
        out = []
        if results:
            doc = read_results()
            seq = max(_seq(doc.get("seq")), _seq(st.get("seq")))
            for r in results:
                seq += 1
                r = dict(r, seq=seq)
                out.append(r)
            st["seq"] = seq
            doc = {"seq": seq, "results": (doc["results"] + out)[-RESULTS_KEEP:]}
            pins._ensure_dir(paths.gw_var_dir())
            util.atomic_write(paths.probe_results(), util.json_text(doc), 0o600)
        if taken:
            _drop_requests(taken)
        pins._ensure_dir(paths.gw_var_dir())
        util.atomic_write(state_path(), util.json_text(st), 0o600)
    return out


def request(kind, eid, after_s=0, now=None):
    """A request of the collector (``health``: ``geo_pass``, ``geo_confirm`` after ``after_s``) for the next run."""
    if kind not in REQUEST_KINDS:
        raise ValueError(kind)
    now = _now() if now is None else now
    with flocked(lock_path()):
        doc = _read_json(requests_path(), {"requests": []})
        reqs = [r for r in doc.get("requests") or [] if isinstance(r, dict)][-200:]
        if not any(r.get("kind") == kind and r.get("exit") == eid for r in reqs):
            reqs.append({"kind": kind, "exit": eid, "at": now + max(0, int(after_s or 0))})
        util.atomic_write(requests_path(), util.json_text({"requests": reqs}), 0o600)


def _peek_requests(now):
    """The requests due now. They stay in the file until the run that does them commits (``commit(taken=…)``): a run
    that breaks or is killed leaves them to the next one."""
    with flocked(lock_path()):
        doc = _read_json(requests_path(), None)
    if not doc:
        return []
    return [r for r in doc.get("requests") or [] if isinstance(r, dict) and r.get("kind") in REQUEST_KINDS
            and isinstance(r.get("exit"), str) and (r.get("at") or 0) <= now]


def _drop_requests(taken):
    """Take ``taken`` off the file of requests (under the lock of ``commit``); a request added since stays."""
    doc = _read_json(requests_path(), None)
    if not doc:
        return
    gone = set((r.get("kind"), r.get("exit"), r.get("at")) for r in taken)
    keep = [r for r in doc.get("requests") or [] if isinstance(r, dict)
            and (r.get("kind"), r.get("exit"), r.get("at")) not in gone]
    util.atomic_write(requests_path(), util.json_text({"requests": keep}), 0o600)


# ── what the run knows of an exit ──────────────────────────────────────────

def _snap_dead(rec):
    """Is the exit «dead» in the snapshot: the state of stage 1 (``off`` for ``dead``), else 3.3.5's ``alive``."""
    if not isinstance(rec, dict):
        return None
    if "state" in rec:
        return rec.get("state") == "off" and "dead" in (rec.get("reasons") or [])
    return None if rec.get("alive") is None else not rec.get("alive")


def quota_removed(rec, pref, traffic, eid, now=None):
    """Is the exit off by its quota with ``quota.action = remove`` (§5.6)? The snapshot of stage 1 says it (the reason
    ``quota`` with ``quota.action = remove`` of the record — not ``standby``, whatever else keeps the exit off, «dead»
    among them; a manual «Вернуть» there is honoured; a record without the action — ``exit_pref.quota.action``); a
    snapshot without states (before the collector of stage 1) — the counters of the period against the limit."""
    if isinstance(rec, dict) and "state" in rec:
        if "quota" not in (rec.get("reasons") or []):
            return False
        action = (rec.get("quota") or {}).get("action") if isinstance(rec.get("quota"), dict) else None
        return (action or ((pref or {}).get("quota") or {}).get("action") or "remove") == "remove"
    q = (pref or {}).get("quota")
    if not q or (q.get("action") or "remove") != "remove":
        return False
    gb = P.quota_gb(q)                 # 10**400 of an older file: no quota, not an OverflowError (REV-33714 С-1)
    return gb is not None and pins.used(traffic, eid, q, now) >= gb * 1e9


def _quiet(rec, tdoc, tmtime, eid):
    """(quiet: True | False | None, the reading to keep) — by the counters of ``traffic.json`` between two of its writes
    (the collector writes it once a minute). None — nothing to judge by (no file, a new exit)."""
    last = (((tdoc or {}).get("exits") or {}).get(eid) or {}).get("last") or {}
    if tmtime is None or last.get("rx") is None or last.get("tx") is None:
        return None, None
    cur = int(last["rx"]) + int(last["tx"])
    prev = (rec or {}).get("tr")
    if not isinstance(prev, dict) or prev.get("at") is None:
        return None, {"at": tmtime, "b": cur, "quiet": None}
    if tmtime <= prev["at"]:
        return prev.get("quiet"), prev
    rate = delta(cur, int(prev.get("b") or 0)) / max(tmtime - prev["at"], 1.0)
    q = rate < QUIET_BPS
    return q, {"at": tmtime, "b": cur, "quiet": q}


class Exit(object):
    """One exit of this run: its row of the pool, way, group, checks, preferences and what the run learned."""

    def __init__(self, e, st, doc, snap, traffic, tmtime, rec, now):
        self.id = e["id"]
        self.row = e
        self.via = fetch.via_of(e)
        grp = P.group_of(doc, self.id)                  # ``doc`` — policy.load with the pool: a new exit is placed
        self.gid = grp.get("id") if grp else None
        self.checks = grp.get("checks") if grp and isinstance(grp.get("checks"), dict) else \
            P.default_checks(e.get("role") or "abroad") # a Russian exit with no Russian group: its role's defaults
        self.pref = model.exit_pref(st, self.id)
        self.enabled = self.pref["enabled"]
        srec = (snap.get("exits") or {}).get(self.id)
        self.removed = quota_removed(srec, self.pref, traffic, self.id, now)
        self.dead = _snap_dead(srec)
        self.returned = bool(rec.get("dead")) and self.dead is False
        self.quiet, self.tr = _quiet(rec, traffic, tmtime, self.id)
        self.expected = geo.expected_country(self.checks, self.pref)
        self.geo_on = bool((self.checks.get("geo") or {}).get("enabled", True))
        self.rec_seen = rec.get("geo_seen")
        self.probes = doc.get("probes")


def _subs():
    """``subs.json`` for ``policy.load`` (the group of a subscription places its new keys); unreadable — no hint."""
    from .. import exitsubs
    try:
        return exitsubs.load()
    except Exception:                                   # noqa: BLE001 — the hint of a subscription is only a hint
        return None


def context(runner, st=None, now=None):
    """(exits [Exit] of the pool that the unit may probe, probes, guard, state) — or None when the gateway is off or
    the pool cannot be read."""
    now = _now() if now is None else now
    st = state.load() if st is None else st
    if not model.enabled(st):
        return None
    try:
        pool = exits.pool(runner, roles=exits.ROLES)
    except KaskadError as e:
        util.log_tr("gw_probe.log.pool", err=str(e)[:200])
        return None
    snap = exits.load_snapshot()
    traffic = pins.traffic_read()
    try:
        tmtime = os.stat(paths.traffic_json()).st_mtime
    except OSError:
        tmtime = None
    pst = read_state()
    doc = P.load(st, pool, _subs())                     # every exit in its group, as the collector places it
    probes_ = doc.get("probes")
    out = []
    for e in pool:
        if not exits.parsed(e) or e.get("dup_of"):
            continue
        x = Exit(e, st, doc, snap, traffic, tmtime, pst["exits"].get(e["id"]) or {}, now)
        if x.via is not None:
            out.append(x)
    return out, probes_, fetch.guard_for(st, runner, pool), pst


# ── budget ─────────────────────────────────────────────────────────────────

def _budget(rec, now):
    """The budget record of an exit for the current day and month of the node's clock (§3.7)."""
    day = tz.day(now)
    b = dict(rec.get("budget") or {})
    if b.get("day") != day:
        b.update(day=day, day_b=0)
    if b.get("month") != day[:7]:
        b.update(month=day[:7], month_b=0)
    return b


def budget_left(rec, probes_, now):
    """(bytes left today, bytes left this month) of the speed tests of an exit."""
    sp = (probes_ or {}).get("speed") or {}
    b = _budget(rec, now)
    day = _clamp(sp.get("budget_day_mb"), 0, 1e6, 200) * fetch.MB
    month = _clamp(sp.get("budget_month_gb"), 0, 1e4, 3) * 1000 * fetch.MB
    return day - (b.get("day_b") or 0), month - (b.get("month_b") or 0)


def _spend(st, eid, nbytes, now):
    rec = st["exits"].setdefault(eid, {})
    b = _budget(rec, now)
    b["day_b"] = int(b.get("day_b") or 0) + int(nbytes or 0)
    b["month_b"] = int(b.get("month_b") or 0) + int(nbytes or 0)
    rec["budget"] = b


def _over_budget(rec, probes_, size, now, force=False):
    """None — the test fits; else ``day`` | ``month`` (``force`` lets a manual test past the day's budget only)."""
    day_left, month_left = budget_left(rec, probes_, now)
    if size > month_left:
        return "month"
    if size > day_left and not force:
        return "day"
    return None


# ── one run ────────────────────────────────────────────────────────────────

class Run(object):
    """What one run decides and gathers: ``results`` and changes of the state (applied on commit). ``all`` — every
    exit of the pool; ``exits`` — those the run probes (not switched off by the owner)."""

    def __init__(self, ctx, now, deadline):
        self.all, self.probes, self.guard, self.st = ctx
        self.exits = [x for x in self.all if x.enabled]
        self.now = now
        self.deadline = deadline
        self.results = []
        self.ops = []                                   # (fn(state)) applied under the lock on commit
        self.written = []

    def time_left(self):
        return self.deadline - _mono()

    def fits(self, worst_s):
        """Does a probe that may take ``worst_s`` end before the run's own deadline?"""
        return self.time_left() >= worst_s

    def rec(self, eid):
        return self.st["exits"].get(eid) or {}

    def set(self, eid, **kw):
        def fn(st, eid=eid, kw=kw):
            r = st["exits"].setdefault(eid, {})
            for k, v in kw.items():
                if v is None:
                    r.pop(k, None)
                else:
                    r[k] = v
        self.ops.append(fn)
        fn(self.st)

    def set_node(self, **kw):
        def fn(st, kw=kw):
            st.update(kw)
        self.ops.append(fn)
        fn(self.st)

    def add(self, eid, kind, **kw):
        r = dict(kw, exit=eid, kind=kind, ts=int(_now()))
        self.results.append(r)
        return r

    def commit(self, taken=()):
        """Write what was gathered since the last commit (the results, the changes of the state, the done requests).
        -> every result this run wrote."""
        ops, results = self.ops, self.results
        self.ops, self.results = [], []

        def mutate(st):
            for fn in ops:
                fn(st)
        self.written += commit(results, mutate, taken)
        return self.written


def _return_check(x):
    """Is the check of the country after a return from «dead» due (kept until it is done)?"""
    return x.returned and x.geo_on and not x.removed


def _housekeep(run):
    """The record of each exit: «dead» now (for the return: kept «dead» until the check of the country after the
    return is done), the reading of the traffic; records of exits that left the pool."""
    ids = set(x.id for x in run.all)
    for x in run.exits:
        run.set(x.id, dead=bool(x.dead) or _return_check(x), tr=x.tr)

    def fn(st):
        for eid in [e for e in st["exits"] if e not in ids]:
            del st["exits"][eid]
    run.ops.append(fn)


# country ----------------------------------------------------------------------

def _geo_every(x):
    return _clamp((x.checks.get("geo") or {}).get("every_h"), GEO_EVERY_H[0], GEO_EVERY_H[1], 24) * 3600


def _row(r):
    """The fields of a reading of the country in a result."""
    out = dict(ip=r.get("ip"), loc=r.get("loc"), ok=r.get("ok"), why=r.get("why"), bytes=r.get("bytes") or 0)
    if r.get("why_args"):
        out["why_args"] = r["why_args"]
    return out


def _trace(via, run, deadline=None):
    """``geo.trace`` that never breaks the run (its own deadline and the run's)."""
    d = run.deadline if deadline is None else min(deadline, run.deadline)
    try:
        return geo.trace(via, run.guard, deadline=d)
    except Exception as e:                              # noqa: BLE001
        return {"ip": None, "loc": None, "ok": False, "why": "error", "why_args": {"what": type(e).__name__}}


def _geo_one(x, how, run):
    r = _trace(x.via, run)
    out = run.add(x.id, "geo", how=how, **_row(r))
    run.set(x.id, geo_due=run.now + jitter(_geo_every(x) if r.get("ok") else min(_geo_every(x), 1800)))
    return out


def geo_pass(run, trigger):
    """The extra pass (§4.3): every exit with ``geo.enabled`` (not off by quota) and the node's direct path, at once,
    one timeout for all. -> {"direct_loc", "locs": {exit: loc}}; the readings go to the results (``how: pass``)."""
    xs = [x for x in run.exits if x.geo_on and not x.removed]
    end = _mono() + max(1.0, min(PASS_TIMEOUT_S, run.time_left()))
    jobs = {}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=PASS_WORKERS)
    try:
        # every reading ends by the pass's timeout (a queued one that starts late — at once): the pass waits for all
        # its threads, none outlives it (nor runs beside the speed test of this run)
        jobs[pool.submit(_trace, fetch.Via("direct"), run, end)] = None
        for x in xs:
            jobs[pool.submit(_trace, x.via, run, end)] = x
    finally:
        pool.shutdown(wait=True)
    locs, direct = {}, None
    for f, x in jobs.items():
        r = f.result()
        if x is None:
            direct = r.get("loc")
            continue
        run.add(x.id, "geo", how="pass", **_row(r))
        if r.get("loc"):
            locs[x.id] = r["loc"]
        run.set(x.id, geo_due=run.now + jitter(_geo_every(x)), passed=run.now)
    expected = dict((x.id, x.expected) for x in xs)
    sus = geo.suspect(trigger.id, locs.get(trigger.id), expected, locs, direct, role=(trigger.row or {}).get("role"))
    p = {"direct_loc": direct, "locs": locs, "suspect": sus}
    run.add(trigger.id, "geo_pass", **p)
    run.set_node(last_pass=dict(p, at=int(run.now), exit=trigger.id))
    _log_pass(trigger.id, p)
    return p


def _log_pass(eid, p):
    from ...i18n import tr
    with util.journal_lang():
        util.log_tr("gw_probe.log.pass", exit=eid, n=len(p["locs"]), direct=p["direct_loc"] or tr("gw_probe.word.none"),
                    suspect=tr("gw_probe.suspect." + (p["suspect"] or "none")))


def _after_geo(run, x, res, how):
    """The first sight of another country — the extra pass and a confirmation in 5 min (§4.3)."""
    loc = res.get("loc")
    if how == "confirm":
        run.set(x.id, confirm_at=None)                  # one confirmation per request; more — by a new one
    if not loc or not x.expected or loc == x.expected:
        run.set(x.id, geo_seen=None)
        return
    if how != "confirm" and (x.rec_seen or {}).get("loc") != loc:
        if not run.fits(geo.WORST_S):
            run.set(x.id, pass_due=True)                # no time left for the pass in this run: the next one
            return
        p = geo_pass(run, x)
        loc2 = p["locs"].get(x.id)
        if p["suspect"] or not loc2 or loc2 == x.expected:
            run.set(x.id, geo_seen=None, confirm_at=None)
            return
        run.set(x.id, geo_seen={"loc": loc2, "at": int(run.now)}, confirm_at=run.now + CONFIRM_S)


def _pass_again(run, x):
    """A pass the collector asked for an exit the unit passed a moment ago: the readings of that pass once more (no
    new requests through the exits) — ``health`` gets the answer it waits for and counts it by its own rules."""
    p = run.st.get("last_pass") or {}
    if not isinstance(p.get("locs"), dict):
        return False
    loc = p["locs"].get(x.id)
    run.add(x.id, "geo", how="pass", ip=None, loc=loc, ok=bool(loc), why=None if loc else "empty", bytes=0,
            again=True)
    run.add(x.id, "geo_pass", direct_loc=p.get("direct_loc"), locs=p["locs"], suspect=p.get("suspect"),
            again=True)
    return True


def do_geo(run, reqs):
    """Planned checks (one per run, ≥ 60 s apart), returns from «dead», confirmations and the collector's requests.
    A reading starts only when it fits the run's time; else — the next run (a pass wanted is kept as ``pass_due``)."""
    by_id = dict((x.id, x) for x in run.exits)
    asked = dict(((r["kind"], r["exit"]), r) for r in reqs)
    for x in run.exits:
        if not x.geo_on or x.removed:
            run.set(x.id, geo_due=None, geo_seen=None, confirm_at=None, pass_due=None)
            continue
        if ("geo_confirm", x.id) in asked:
            run.set(x.id, confirm_at=min(run.rec(x.id).get("confirm_at") or run.now, run.now))
        if run.rec(x.id).get("pass_due"):
            asked.setdefault(("geo_pass", x.id), {"own": True})
    for (kind, eid), r in asked.items():
        x = by_id.get(eid)
        if kind != "geo_pass" or x is None or not x.geo_on or x.removed:
            continue
        if not r.get("own") and run.now - (run.rec(eid).get("passed") or 0) < PASS_DEDUP_S:
            _pass_again(run, x)                         # the unit passed it a moment ago (its own first sight)
            continue
        if not run.fits(geo.WORST_S):
            run.set(eid, pass_due=True)                 # no time left in this run: the next one
            continue
        run.set(eid, pass_due=None)
        p = geo_pass(run, x)
        if not p["suspect"] and p["locs"].get(eid) and p["locs"].get(eid) != x.expected:
            run.set(eid, geo_seen={"loc": p["locs"][eid], "at": int(run.now)}, confirm_at=run.now + CONFIRM_S)
    planned_done = run.now - (run.st.get("last_geo") or 0) < SPACING_S
    for x in sorted(run.exits, key=lambda x: run.rec(x.id).get("geo_due") or 0):
        if not x.geo_on or x.removed or not run.fits(geo.WORST_S):
            continue
        rec = run.rec(x.id)
        every = _geo_every(x)
        due = rec.get("geo_due")
        if due is None or due > run.now + every * (1 + JITTER):
            due = run.now                               # a new exit (its first country) or a shorter interval
            run.set(x.id, geo_due=due)
        confirm = rec.get("confirm_at")
        if confirm is not None and confirm <= run.now:
            _after_geo(run, x, _geo_one(x, "confirm", run), "confirm")
        elif _return_check(x):
            _after_geo(run, x, _geo_one(x, "plan", run), "plan")
            run.set(x.id, dead=False)                   # the check after the return is done
        elif due <= run.now and not planned_done:
            planned_done = True
            run.set_node(last_geo=run.now)
            _after_geo(run, x, _geo_one(x, "plan", run), "plan")


# services ---------------------------------------------------------------------

def do_services(run):
    n = 0
    for x in run.exits:
        if x.removed:
            run.set(x.id, svc_due=None)
            continue
        svcs = [s for s in x.checks.get("services") or [] if isinstance(s, dict) and s.get("id") and s.get("url")][:5]
        due = dict((k, v) for k, v in (run.rec(x.id).get("svc_due") or {}).items() if any(s["id"] == k for s in svcs))
        for s in svcs:
            every = _clamp(s.get("every_min"), EVERY_MIN[0], EVERY_MIN[1], 30) * 60
            d = due.get(s["id"])
            if d is None or d > run.now + every * (1 + JITTER):
                d = run.now
            if d > run.now or n >= SVC_PER_RUN or not run.fits(services.worst_s(s)):
                due[s["id"]] = d
                continue
            n += 1
            try:
                r = services.check(x.via, s, run.guard, deadline=run.deadline)
            except Exception as e:                      # noqa: BLE001 — one check never breaks the run
                r = {"svc": s["id"], "ok": False, "code": None, "ms": None, "bytes": 0, "host": fetch.shown(s["url"]),
                     "why": "error", "why_args": {"what": type(e).__name__}}
            run.add(x.id, "svc", **r)
            due[s["id"]] = run.now + jitter(every)
        run.set(x.id, svc_due=due or None)


# speed ------------------------------------------------------------------------

def _speed_every(x):
    h = (x.checks.get("speed") or {}).get("every_h")
    return (h if h in SPEED_EVERY_H else 6) * 3600


def speed_wanted(x):
    """Does the schedule test this exit: the group's ``checks.speed.enabled``, no ``speed_skip``, not off by quota and
    not «dead» (a test of a dead exit tells nothing and is put off until it answers)."""
    return (bool((x.checks.get("speed") or {}).get("enabled")) and not x.pref["speed_skip"] and not x.removed
            and x.dead is not True)


def _speed_size(run, x, half=False):
    sp = (run.probes or {}).get("speed") or {}
    mb = _clamp(sp.get("max_mb"), 0.1, fetch.MAX_MB, 10)
    return (mb / 2.0 if half else mb), _clamp(sp.get("max_s"), 1, fetch.MAX_S, 8)


def _budget_event(run, x, period):
    """``speedtest_budget`` once a period (a day, a month) per exit."""
    rec = run.rec(x.id)
    key = tz.day(run.now) if period == "day" else tz.day(run.now)[:7]
    told = dict(rec.get("budget_told") or {})
    if told.get(period) == key:
        return False
    told[period] = key
    run.set(x.id, budget_told=told)
    run.add(x.id, "speed", ok=False, why="budget_" + period, event="speedtest_budget", period=period,
            name=x.pref.get("name") or "")
    from ...i18n import tr
    with util.journal_lang():
        util.log_tr("gw_probe.log.budget", exit=x.id, period=tr("gw_probe.period." + period))
    return True


def _reserve(run, eid, size, force=False, plan=None):
    """Before a test, under the lock of the files: check the budget against the state on the disk (another test may
    have spent it since this run read it) and spend ``size`` there at once, with ``plan(state)`` (``speed_due``,
    ``last_speed``). A run killed during the test keeps both: the test is neither repeated nor free. -> None (reserved)
    | ``day`` | ``month``."""
    got = {}

    def mutate(st):
        got["over"] = _over_budget(st["exits"].get(eid) or {}, run.probes, size, run.now, force)
        if not got["over"]:
            _spend(st, eid, size, run.now)
            if plan:
                plan(st)
    commit((), mutate)
    if not got["over"]:
        _spend(run.st, eid, size, run.now)
        if plan:
            plan(run.st)
            run.ops.append(plan)                        # after the ops queued before it: a commit on SIGTERM keeps it
    return got["over"]


def _speed_test(run, x, mb, max_s, manual=False, force=False, plan=None):
    """One test under the node's lock (LOCK_NB), the budget reserved first and the result written at once.
    -> (the result | None, None | ``busy`` | ``day`` | ``month``)."""
    size = int(mb * fetch.MB)
    with flocked(speed_lock_path(), block=False) as held:
        if not held:
            return None, "busy"
        over = _reserve(run, x.id, size, force, plan)
        if over:
            return None, over
        try:
            r = speed.run(x.via, (x.checks.get("speed") or {}).get("url"), mb, max_s, run.guard,
                          deadline=run.deadline)
        except Exception as e:                          # noqa: BLE001 — the reserved budget stays spent
            r = speed.failed({"mbps": None, "ttfb_ms": None, "tls_ms": None, "bytes": 0, "rx": 0,
                              "host": "?", "ok": False}, e)
        _touch(speed_lock_path())                       # «the test just ended» (still held: no gap for the light
                                                        # probes between the unlock and the mtime); they skip 5 s more
    nbytes = int(r.get("rx") or r.get("bytes") or 0)    # what came through the exit, DoH too
    fix = nbytes - size
    run.ops.append(lambda st, eid=x.id, b=fix, now=run.now: _spend(st, eid, b, now))
    _spend(run.st, x.id, fix, run.now)
    out = run.add(x.id, "speed", **dict(r, manual=True) if manual else r)
    _log_speed(x.id, r, nbytes)
    return out, None


def _log_speed(eid, r, nbytes):
    mb = round(nbytes / float(fetch.MB), 1)
    if r.get("ok"):
        util.log_tr("gw_probe.log.speed", exit=eid, mbps=r.get("mbps"), mb=mb, host=r.get("host"))
        return
    with util.journal_lang():
        util.log_tr("gw_probe.log.speed_fail", exit=eid, mb=mb, host=r.get("host"),
                    why=fetch.why_text(r.get("why") or "error", r.get("why_args")))


def _speed_worst(mb, max_s):
    return speed.worst_s(mb, max_s)


def do_speed(run):
    """At most one planned test per run, ≥ 60 s after the last one of the node, and only when it fits the time left
    of the run (else — the next run)."""
    cands = []
    for x in run.exits:
        if not speed_wanted(x):
            run.set(x.id, speed_due=None)
            continue
        every = _speed_every(x)
        due = run.rec(x.id).get("speed_due")
        if due is None or due > run.now + every * (1 + JITTER):
            due = run.now
            run.set(x.id, speed_due=due)
        if due <= run.now:
            cands.append((due, x))
    if not cands or run.now - (run.st.get("last_speed") or 0) < SPACING_S:
        return None
    only_quiet = ((run.probes or {}).get("speed") or {}).get("only_quiet", True)
    for due, x in sorted(cands, key=lambda c: c[0]):
        every = _speed_every(x)
        half = False
        if only_quiet and x.quiet is False:
            if run.now - due < 2 * every:
                continue                                # wait for the quiet
            half = True                                 # no quiet for 2 intervals: the test anyway, half the volume
        mb, max_s = _speed_size(run, x, half)
        if not run.fits(_speed_worst(mb, max_s)):
            return None                                 # it would not end before the run's deadline: next run
        nxt = run.now + jitter(every)

        def plan(st, eid=x.id, nxt=nxt, now=run.now):
            st["exits"].setdefault(eid, {})["speed_due"] = nxt
            st["last_speed"] = now
        r, why = _speed_test(run, x, mb, max_s, plan=plan)
        if why == "busy":
            return None                                 # another test on the node: next run
        if why:
            _budget_event(run, x, why)
            continue
        if half:
            r["half"] = True
        run.set(x.id, speed_due=nxt)
        run.set_node(last_speed=run.now)
        run.commit()                                    # the test is written at once, not at the end of the run
        return r
    return None


def manual_speed(runner, eid, force=False, now=None):
    """«Проверить скорость сейчас» (the template unit): any checkboxes, the same lock and budget; past the day's
    budget only with ``force``. -> the result as written (``manual: true``)."""
    now = _now() if now is None else now
    deadline = _mono() + RUN_BUDGET_S                   # from the start: context() may run --egress-ensure-parser
    ctx = context(runner, now=now)
    if ctx is None:
        return None
    run = Run(ctx, now, deadline)
    x = next((x for x in run.all if x.id == eid), None)
    if x is None:
        run.add(eid, "speed", ok=False, why="no_exit", manual=True)
    elif not x.enabled:
        run.add(eid, "speed", ok=False, why="disabled", manual=True)
    elif x.removed:
        run.add(eid, "speed", ok=False, why="quota", manual=True)
    else:
        mb, max_s = _speed_size(run, x)
        r, why = _speed_test(run, x, mb, max_s, manual=True, force=force)
        if why == "busy":
            run.add(eid, "speed", ok=False, why="busy", manual=True)
        elif why:
            run.add(eid, "speed", ok=False, why="budget_" + why, manual=True, period=why)
    out = run.commit()
    return out[-1] if out else None


def tick(runner, now=None):
    """One planned run. -> {"ok", "results": n} (None of the gateway off: nothing done)."""
    now = _now() if now is None else now
    deadline = _mono() + RUN_BUDGET_S                   # from the start: context() may run the parser once (60 s)
    ctx = context(runner, now=now)
    if ctx is None:
        return None
    run = Run(ctx, now, deadline)
    reqs = _peek_requests(now)
    taken = []
    ok = True
    try:
        _housekeep(run)
        for name, stage in (("geo", lambda: do_geo(run, reqs)), ("speed", lambda: do_speed(run)),
                            ("services", lambda: do_services(run))):
            try:
                stage()
                if name == "geo":
                    taken = reqs                        # done: off the file with this commit
            except Exception as e:                      # noqa: BLE001 — one stage never loses what the others did
                ok = False
                util.log_tr("gw_probe.log.error", err=("%s: %s: %s" % (name, type(e).__name__, e))[:300])
    finally:
        out = run.commit(taken)                         # also on SIGTERM (TimeoutStartSec): what was gathered stays
    return {"ok": ok, "results": len(out)}


def main(argv=None):
    """``python3 -m kaskad.gw.probe [--exit <id>[:force]]`` — the ExecStart of the units (unit.py)."""
    import argparse
    ap = argparse.ArgumentParser(prog="kaskad-gw-probe")
    ap.add_argument("--exit", dest="exit_id")
    a = ap.parse_args(argv)
    runner = sysrun.default_runner()
    signal.signal(signal.SIGTERM, _on_term)
    try:
        if a.exit_id:
            eid, _, flag = a.exit_id.partition(":")
            manual_speed(runner, eid, force=(flag == "force"))
        else:
            tick(runner)
    except Terminated:
        util.log_tr("gw_probe.log.error", err="SIGTERM")
        return 1
    except Exception as e:                              # noqa: BLE001 — the journal, not a traceback
        util.log_tr("gw_probe.log.error", err=("%s: %s" % (type(e).__name__, e))[:300])
        return 1
    return 0


class Terminated(BaseException):
    """SIGTERM of systemd (TimeoutStartSec, a stop): the run ends, what it gathered is committed (``tick``)."""


def _on_term(signum, frame):
    raise Terminated()


__all__ = ["tick", "manual_speed", "request", "read_results", "commit", "quota_removed", "speed_wanted", "jitter"]
