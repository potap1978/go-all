"""The runner of the schedule and of the repeat after a sharp drop (SPEC-node-score-schedule-share §2.5, §2.8, NS-S4,
NS-S6, NS-S8, NS-S15; B11): ``kaskadctl host bench --scheduled`` (the service of the timer) and ``--retry <id>``.

    scheduled(runner, clock)   one firing of the timer (§2.5):
        the queue cleaned (``spool.prune``, with the gateway or without) -> ``bench.fix_state`` -> not armed: a line,
        exit 0 -> the ``missed`` rows of ALL enabled entries (≤ 7 per entry, the older — one row with ``n``) -> ``due``
        (nothing: a line, exit 0) -> ``disabled`` | ``running`` -> «only when quiet» (NS-S8: the first decision 5 min
        after the first reading, then the moving window of 5 min every 5 min up to 60 min; not quiet — ``busy``) ->
        the lock tried without waiting once a minute up to 10 min (``locked``) -> ``run.start(mode, by="schedule",
        sched_id=…)``; the mark of the event in ``sched-state.json`` (0600, atomic) in every case; after it — the
        lines of the timer compared with the file (``sched_unit.apply`` when they drifted).
    retry(runner, rid, clock, links)  the repeat of check ``rid`` (the transient timer of ``plan_retry``): the same
        wait, the same lock, ``by = "auto"``, ``retry_of = rid``, ``only = links`` — the tests of the links that fell
        alone (SS §2.8 п. 2; never 8b, 9; the mode and the caps of the original: its traffic is never more); busy
        60 min — a ``busy`` row, no second repeat.
    plan_retry(runner, doc, …) after a good check (not a repeat): ``score.retry_wanted`` gave links — the limits of
        §2.8 п. 4 (one per check, ≤ 2 a day by ``KASKAD_TZ``, no event of the schedule in [−30, +120] min, armed,
        the switch ``gateway.hostmon.bench.retry``) -> the transient timer (``sched_unit.schedule_retry``).

A skipped event is a file of the queue (``spool.put``: ``status: skipped``, ``why`` a code of §2.5, ``ts`` — the
nominal moment of the event) — the history and the count of the skips of a day (§2.5 п. 7) read it. No push for a skip.
"""

import contextlib
import json
import os
import secrets
import time

from .. import paths, tz, util

STATE = "sched-state.json"
WAIT_S = 3600                          # «only when quiet» waits at most 60 min (§2.5 п. 4)
WINDOW_S = 300                         # the window of the decision (NS-S8)
STEP_S = 60                            # a reading / a try of the lock once a minute
LOCK_S = 600                           # the lock is tried up to 10 min (within the same 60)
RETRY_PER_DAY = 2                      # §2.8 п. 4
RETRY_SCHED_LO, RETRY_SCHED_HI = -30 * 60, 120 * 60
DAY_S = 86400
PENDING_KEEP_S = WAIT_S + 1800         # a planned repeat waits ≤ 60 min for quiet + the check ≤ 10 min, then has its row
NO_START = ("retry_off", "disabled", "not_armed", "retry_day", "retry_links")   # a repeat that did not start: not one of the 2 a day


class Clock(object):
    """The time of the runner (tests put a fake one): ``now()`` and ``sleep(s)``."""

    def now(self):
        return time.time()

    def sleep(self, s):
        time.sleep(s)


def state_path():
    return os.path.join(paths.bench_dir(), STATE)


@contextlib.contextmanager
def _locked():
    """sched-state.json is changed by three processes (the service of the timer, the unit of a check that plans a
    repeat, the repeat itself): every change reads and writes the file under one flock (a short one)."""
    import fcntl
    util.ensure_dir(paths.bench_dir(), 0o700)
    fd = os.open(state_path() + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read():
    try:
        with open(state_path(), "rb") as f:
            doc = json.loads(f.read(65536).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write(doc):
    util.ensure_dir(paths.bench_dir(), 0o700)
    util.atomic_write(state_path(), json.dumps(doc, sort_keys=True), 0o600)


def _key(ts, zone):
    return time.strftime("%Y-%m-%d %H:%M", tz.local(int(ts), zone))


def load_done(now=None, zone=None):
    """{id: "YYYY-MM-DD HH:MM"} of sched-state.json; a broken file — {} (``due`` then looks at the events again,
    ``missed`` starts from ``since``/``armed_at``). With ``now``: a mark later than ``now`` + 1 day (the clock ran
    ahead once) is no mark (SEC П-2) — the event is not silenced till that moment."""
    done = _read().get("done")
    if not isinstance(done, dict):
        return {}
    from .schedule import _ID
    out = dict((k, v) for k, v in done.items() if isinstance(k, str) and _ID.fullmatch(k) and isinstance(v, str)
               and len(v) == 16)
    if now is not None:
        lim = _key(now + DAY_S, zone)
        out = dict((k, v) for k, v in out.items() if v <= lim)
    return out


def save_done(done):
    with _locked():
        doc = _read()
        doc["done"] = done
        _write(doc)


def load_pending(now):
    """{check id: the moment its repeat fires} — the repeats planned and not yet past (REV З-2, SEC С-1: they count in
    the limit of a day before they reach the history). Past ``PENDING_KEEP_S`` (the repeat has its row by then) or
    later than the longest delay (the clock ran ahead) — not kept."""
    p = _read().get("retries")
    if not isinstance(p, dict):
        return {}
    from . import sched_unit
    return dict((k, v) for k, v in p.items() if isinstance(k, str) and sched_unit._ID.fullmatch(k)
                and isinstance(v, int) and not isinstance(v, bool)
                and now - PENDING_KEEP_S <= v <= now + sched_unit.RETRY_MAX_S + 120)


def _pending_set(rid, at, now):
    """Under ``_locked`` (the caller holds it)."""
    doc = _read()
    p = load_pending(now)
    if at is None:
        p.pop(rid, None)
    else:
        p[rid] = int(at)
    doc["retries"] = p
    _write(doc)


def clear_pending():
    """The schedule disarmed (``sched_unit.apply``): no repeat waits any more."""
    with _locked():
        doc = _read()
        if doc.get("retries"):
            doc["retries"] = {}
            _write(doc)


def _skip(mode, why, ts, now, sched_id=None, retry_of=None, args=None, by="schedule"):
    """A skipped event into the queue (§3.2) — the one way into the history."""
    from . import run, spool
    rid = secrets.token_hex(5)
    r = run._run_doc(rid, mode, by, sched_id, retry_of, int(ts))
    r.update(status="skipped", why=why, why_args=args or None, end_ts=max(int(ts), int(now)))
    try:
        spool.put({"v": run.V, "run": r, "tests": []})
    except (OSError, ValueError) as e:
        util.log_tr("bench.log.sched_skip_lost", why=type(e).__name__)
    return rid


# ── «only when quiet» and the lock ────────────────────────────────────────

def wait_quiet(meter, clock, deadline):
    """NS-S8 over a moving window of 5 min: a reading now, a decision every 5 min (the first one 5 min after the
    first reading), up to ``deadline``. -> (True, decision) | (False, the last decision or None)."""
    from . import quiet
    samples = []
    t0 = clock.now()
    last = None
    while True:
        try:
            s = meter.read()
            s["t"] = clock.now()
            samples.append(s)
        except Exception:                            # noqa: BLE001 — a failed reading is no decision
            pass
        now = clock.now()
        if now - t0 >= WINDOW_S - 1e-6 and int(round(now - t0)) % WINDOW_S < STEP_S and samples:
            b = samples[-1]
            olds = [a for a in samples if b["t"] - a["t"] >= WINDOW_S - 1e-6]
            if olds:
                last = quiet.decide(olds[-1], b)
                if last["free"] and last["known"]:
                    return True, last
        samples = [a for a in samples if now - a["t"] <= WINDOW_S + STEP_S]
        if now + STEP_S > deadline:
            return False, last
        clock.sleep(STEP_S)


def try_lock(path):
    """The lock of the speed tests tried without waiting and let go at once: True — it was free."""
    import fcntl
    util.ensure_run_dir(os.path.dirname(path))
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def wait_lock(clock, deadline, path=None, free=None):
    """The lock of the speed tests tried once a minute up to ``LOCK_S`` (within ``deadline``). ``free()`` — one more
    condition of «go» read with every try (REV-ns-b11-2 П-1: no unit of a check still alive). -> True | False."""
    from . import run
    end = min(deadline, clock.now() + LOCK_S)
    while True:
        if try_lock(path or run.lock_path()) and (free is None or free()):
            return True
        if clock.now() + STEP_S > end:
            return False
        clock.sleep(STEP_S)


def _busy_args(d):
    from . import quiet
    a = {"thr_mbps": quiet.THR_MBPS, "thr_cpu": quiet.THR_CPU}
    if d:
        if d.get("people_mbps") is not None:
            a["mbps"] = round(d["people_mbps"], 1)
        if d.get("cpu_pct") is not None:
            a["cpu"] = round(d["cpu_pct"], 1)
        if not d.get("known"):
            a["load"] = "unknown"
    return a


def _gate(runner, clock, meter, quiet_on, t0):
    """The common way of the schedule and the repeat to the start: -> None (go) | (why, args)."""
    from . import run
    if run.active(runner):
        return "running", None
    deadline = t0 + WAIT_S
    if quiet_on:
        ok, d = wait_quiet(meter, clock, deadline)
        if not ok:
            return "busy", _busy_args(d)
    # REV-ns-b11-2 П-1: a check lets the lock go before its unit ends (the drops and the repeat are counted after
    # it) — or a manual one started during the wait for quiet: its unit alive is «not free» too (else ``run.start``
    # says «running» and the event is lost as an «error»)
    if not wait_lock(clock, deadline, free=lambda: not run.active(runner)):
        return ("running" if run.active(runner) else "locked"), None
    return None


def _meter(runner):
    from . import quiet
    return quiet.Meter(unit=None, runner=runner)


# ── one firing of the timer ───────────────────────────────────────────────

def _bench_now(here=None):
    """(model.bench of the state as it is NOW, hostmon enabled) — read again after every long wait (SEC С-1)."""
    from ..gw import model
    from . import machine
    st = _state()
    return model.bench(st, here=machine.mid() if here is None else here), model.hostmon_flags(st)[0]


def scheduled(runner=None, clock=None, meter=None, here=None):
    """See the head. -> {"done": key | None, "id": the check | None, "skip": why | None, "missed": n}. Exit 0 always
    (the result is in the queue)."""
    from .. import sysrun
    from . import fix_state, run, schedule, sched_unit
    runner = runner or sysrun.default_runner()
    clock = clock or Clock()
    run.prune_spool()
    m = fix_state(timeout=5.0) if here is None else fix_state(timeout=5.0, here=here)
    out = {"done": None, "id": None, "skip": None, "missed": 0}
    if not m.get("armed"):
        util.log_tr("bench.log.sched_not_armed")
        _redraw(runner, clock.now(), here)           # REV З-4: a timer left over (a clone of the disk, a failed apply)
        return out
    zone, _w = sched_unit._zone()
    now = clock.now()
    done = load_done(now, zone)
    miss = schedule.missed(m["schedule"], now, zone, done, armed_at=m.get("armed_at"))
    first = {}
    for v in miss["rows"]:
        first.setdefault(v["id"], v)
        _skip(v["mode"], "missed", v["ts"], now, sched_id=v["id"])
    for eid, n in miss["older"].items():
        v = first.get(eid)
        if v is not None:
            _skip(v["mode"], "missed", v["ts"] - 1, now, sched_id=eid, args={"n": n})
    out["missed"] = len(miss["rows"]) + sum(miss["older"].values())
    if miss["done"]:
        done.update(miss["done"])
        save_done(done)
    ev = schedule.due(m["schedule"], now, zone, done)
    if ev is None:
        util.log_tr("bench.log.sched_nothing")
        _redraw(runner, now, here)
        return out
    out["done"] = ev["key"]
    why, args = _still(here, retry=False)
    if why is None:
        g = _gate(runner, clock, meter or _meter(runner), ev.get("quiet", True), now)
        why, args = g if g else _still(here, retry=False)    # SEC С-1: up to 60 min passed — the switches again
    if why is None:
        try:
            out["id"] = run.start(ev["mode"], by="schedule", sched_id=ev["id"], runner=runner,
                                  skip=run.AUTO_SKIP)["id"]          # NS-S5: never 8b, 9 by the schedule
        except Exception as e:                       # noqa: BLE001 — a start that failed is a row, not a crash
            why, args = _start_failed(e)
    if why is not None:
        _skip(ev["mode"], why, ev["ts"], clock.now(), sched_id=ev["id"], args=args)
        out["skip"] = why
    done[ev["id"]] = ev["key"]
    save_done(done)
    if why is None:
        util.log_tr("bench.log.sched_started", event=ev["key"], id=out["id"])
    else:
        from .run import why_text
        util.log_tr("bench.log.sched_event", event=ev["key"], why=why_text(why, args))
    _redraw(runner, clock.now(), here)
    return out


def _still(here, retry):
    """None, None — the start may go on; else (why, None): the schedule disarmed on this machine (``not_armed``),
    the health off (``disabled``), for a repeat — its switch off (``retry_off``)."""
    try:
        m, enabled = _bench_now(here)
    except Exception:                                # noqa: BLE001 — a state that cannot be read starts nothing
        return "not_armed", None
    if not m.get("armed"):
        return "not_armed", None
    if not enabled:
        return "disabled", None
    if retry and not m.get("retry", True):
        return "retry_off", None
    return None, None


def _state():
    from .. import state
    try:
        return state.load()
    except Exception:                                # noqa: BLE001
        return {}


def _redraw(runner, now, here=None):
    """§2.4: the lines of the timer drifted from the file (the one-shot spring lines run out, the schedule disarmed) —
    ``apply`` from INSIDE the service: only the timer is removed (SEC П-1, REV П-1 — never a stop of its own unit)."""
    from . import sched_unit
    try:
        st = _state()
        text = sched_unit.wanted(st, now, here)[0]
        if text != sched_unit.current_timer():
            sched_unit.apply(runner, st, now, here, inside=True)
    except Exception:                                # noqa: BLE001 — the run is done; the next firing tries again
        pass


# ── the repeat after a sharp drop (§2.8) ──────────────────────────────────

def _links(links):
    """The links of the repeat from its timer (``--links``) -> the known ones, sorted, or None: none given, or a word
    that is not a link — a timer we did not set (``schedule_retry`` writes words of ``suite.LINKS`` only, never an
    empty list); the repeat is refused, never widened to all the links (REV-ns-join З-1; no timer of an older version
    exists: B11 is not out)."""
    from . import suite
    got = [x for x in (links or ()) if isinstance(x, str)]
    if not got or any(x not in suite.LINKS for x in got):
        return None
    return sorted(set(got))


def retry(rid, runner=None, clock=None, meter=None, here=None, links=None):
    """The repeat of check ``rid`` (see the head). Before the wait for quiet and after it the limits are read again
    (SEC С-1, REV З-3): the switch ``bench.retry``, the health, the schedule armed on THIS machine, 2 a day (the
    history by ``KASKAD_TZ`` + the other repeats still waiting). Not met — a row ``skipped`` without a start.
    -> {"id" | "skip"}."""
    from .. import sysrun
    from . import run, sched_unit
    sched_unit.retry_unit(rid)                       # the id by fullmatch, or KaskadError
    runner = runner or sysrun.default_runner()
    clock = clock or Clock()
    src = _source(rid)
    mode = src.get("mode") if src and src.get("mode") in run.MODES else "quick"
    now = clock.now()
    only = _links(links)
    if only is None:                                 # REV-ns-join З-1: a broken --links — no repeat, never all links
        util.log_tr("bench.log.retry_bad_links", id=rid)
        _skip(mode, "retry_links", now, clock.now(), retry_of=rid, by="auto")
        return {"skip": "retry_links"}
    why = _retry_still(rid, now, here)
    if why is None:
        g = _gate(runner, clock, meter or _meter(runner), True, now)
        why, args = g if g else (_retry_still(rid, clock.now(), here), None)
    else:
        args = None
    if why is not None:
        _skip(mode, why, now, clock.now(), retry_of=rid, args=args, by="auto")
        return {"skip": why}
    try:
        return {"id": run.start(mode, by="auto", retry_of=rid, runner=runner, skip=run.AUTO_SKIP,
                                only=only)["id"]}
    except Exception as e:                           # noqa: BLE001
        why, args = _start_failed(e)
        _skip(mode, why, now, clock.now(), retry_of=rid, args=args, by="auto")
        return {"skip": why}


def _start_failed(e):
    """The reason of a start that failed: another check took the server in the last second (``run.start`` says
    ``running``) — ``running``, as the gate says it; else ``error`` with the type."""
    if isinstance(getattr(e, "data", None), dict) and e.data.get("kind") == "running":
        return "running", None
    return "error", {"err": type(e).__name__}


def _retry_still(rid, now, here):
    why = _still(here, retry=True)[0]
    if why is not None:
        return why
    from . import sched_unit
    zone = sched_unit._zone()[0]
    if _auto_of_day(_auto_runs(now), load_pending(now), tz.day(now, zone), zone, me=rid) >= RETRY_PER_DAY:
        return "retry_day"
    return None


def _source(rid):
    from . import history
    try:
        return history.run(rid)
    except Exception:                                # noqa: BLE001
        return None


def _auto_runs(now):
    """The repeats of the last 3 days in the history (rows: by, retry_of, ts, why)."""
    from . import history
    try:
        return history.runs(frm=now - 3 * DAY_S, now=now, by="auto", limit=500)["runs"]
    except Exception:                                # noqa: BLE001
        return []


def _auto_of_day(runs, pending, day, zone, me=None):
    """The repeats of the local ``day``: the rows ``by = auto`` that started or waited for a start (not ``NO_START``)
    and the planned ones still waiting (by the day they fire), each original once; ``me`` — the repeat asking."""
    seen, n = set(), 0
    for r in runs:
        if r.get("by") != "auto" or r.get("retry_of") == me:
            continue
        seen.add(r.get("retry_of"))
        if r.get("why") not in NO_START and tz.day(r.get("ts") or 0, zone) == day:
            n += 1
    for rid, at in pending.items():
        if rid != me and rid not in seen and tz.day(at, zone) == day:
            n += 1
    return n


def retry_blocked(m, runs, run_doc, now, zone, pending=None, at=None):
    """§2.8 п. 4 -> None (a repeat may be set) | the reason: ``off`` (the switch), ``not_armed``, ``auto`` (a repeat
    sets none), ``has_one`` (one per check, a waiting one too), ``day`` (2 a day by KASKAD_TZ, the waiting repeats
    too — REV З-2), ``schedule`` (an event in [−30, +120] min around ``at`` — the real moment of the repeat, REV З-1).
    ``runs`` — the checks (rows of the history: by, retry_of, ts, why); ``pending`` — ``load_pending``."""
    from . import schedule
    pending = pending or {}
    at = now + sched_unit_retry_mid() if at is None else at
    if not m.get("retry", True):
        return "off"
    if not m.get("armed"):
        return "not_armed"
    if run_doc.get("by") == "auto":
        return "auto"
    rid = run_doc.get("id")
    if rid in pending or any(r.get("retry_of") == rid for r in runs):
        return "has_one"
    if _auto_of_day(runs, pending, tz.day(at, zone), zone) >= RETRY_PER_DAY:
        return "day"
    evs = schedule.events(m["schedule"], at + RETRY_SCHED_LO, at + RETRY_SCHED_HI + 1, zone)
    if evs:
        return "schedule"
    return None


def sched_unit_retry_mid():
    """The moment of the repeat when no real one is given (an estimate): the middle of 60–120 min."""
    from . import sched_unit
    return (sched_unit.RETRY_MIN_S + sched_unit.RETRY_MAX_S) // 2


def plan_retry(runner, run_doc, links, now=None, rng=None, here=None):
    """After a good check whose ``links`` (``score.retry_wanted``) fell: the seconds of the delay first, then the
    limits around that very moment, then the transient timer and the mark of the waiting repeat (one flock: two
    plans never both see «one left»). -> {"set": seconds} | {"blocked": why} (``not_armed`` — the caller shows
    «Проверьте ещё раз через час-два»)."""
    from ..gw import model
    from . import machine, sched_unit
    if not links:
        return {"blocked": "no_drop"}
    now = int(time.time() if now is None else now)
    st = _state()
    m = model.bench(st, here=machine.mid() if here is None else here)
    zone = sched_unit._zone()[0]
    s = sched_unit.retry_delay(rng)
    # REV-ns-b11-2 П-2: the history (all the queue, up to 3000 files) is read BEFORE the flock — the holder runs with
    # CPUSchedulingPolicy=idle; the rows of the repeats come from the repeats themselves, the ones still waiting are
    # in sched-state.json and are read under the flock
    runs = _auto_runs(now)
    with _locked():
        why = retry_blocked(m, runs, run_doc, now, zone, pending=load_pending(now), at=now + s)
        if not why and not model.hostmon_flags(st)[0]:
            why = "disabled"                         # the health is off: the repeat would only write a row
        if why:
            return {"blocked": why}
        try:
            sched_unit.schedule_retry(runner, run_doc["id"], s=s, links=links)
        except Exception as e:                       # noqa: BLE001
            util.log_tr("bench.log.retry_not_set", why=type(e).__name__)
            return {"blocked": "error"}
        _pending_set(run_doc["id"], now + s, now)
    return {"set": s}
