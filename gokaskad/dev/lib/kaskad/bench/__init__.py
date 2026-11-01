"""The check of the server by the button and by the schedule (SPEC-node-score B, SPEC-node-score-schedule-share).

    schedule  the entries, the one ``validate`` of the limits, the events of the clock (pure)
    cost      the price of the schedule (pure)
    machine   the marks of the first manual check and the consent, bound to /etc/machine-id

    fix_state(timeout=15.0)  saves what ``model.bench`` found broken in the saved schedule (an entry off with
                             ``off_why``), a transaction of its own OUTSIDE any other: under the lock it reads the
                             state again (never writes a snapshot taken before the lock) and changes only those
                             entries; one line of the journal per save. The lock busy (or no right to write) -> the
                             answer of ``model.bench`` unsaved, never a 500 or a wait. Callers: the runner
                             ``--scheduled``, ``GET /api/host/bench/schedule`` and ``schedule list`` (timeout ≤ 1 s).
    fix_in(st)               the same inside a transaction the caller holds (PUT of the schedule, ``apply``, the
                             restore of a .kbk): changes ``st`` — the save of the transaction writes it; the line of
                             the journal — ``log_fixed(m)`` by the caller after that save.
"""

_UNSET = object()


def _here(here):
    from . import machine
    return machine.mid() if here is _UNSET else here


def _apply(st, m, here):
    from ..gw import model
    from . import machine, schedule
    b = machine._cfg(st)
    b["schedule"] = schedule.apply_fix(b.get("schedule"), m["fix"])
    return dict(model.bench(st, here=here), fixed=m["fix"])


def log_fixed(m):
    """The one line of the journal of a fix — AFTER its save (§2.1; REV-ns-b10 П-2): ``fix_state`` calls it itself,
    the caller of ``fix_in`` — after the save of its transaction (a rolled back one says nothing)."""
    fix = (m or {}).get("fixed")
    if not fix:
        return
    from .. import util
    try:
        util.log_tr("bench.log.sched_fixed", n=len(fix),
                    why=", ".join(sorted(set(str((f.get("fields") or {}).get("off_why") or f.get("op"))
                                             for f in fix))))
    except Exception:                                  # noqa: BLE001 — the journal never stops the work
        pass


def fix_in(st, here=_UNSET):
    """``model.bench(st)`` and, when it found something, the fix written into ``st`` (the caller saves, then
    ``log_fixed(m)``: ``m["fixed"]`` — what was fixed)."""
    from ..gw import model
    here = _here(here)
    m = model.bench(st, here=here)
    return _apply(st, m, here) if m["fix"] else m


def fix_state(timeout=15.0, here=_UNSET):
    """See the head of the module. -> ``model.bench`` of the state as saved (``saved``: the fix was written now)."""
    from .. import state
    from ..errors import LockedError
    from ..gw import model

    here = _here(here)

    try:
        with state.locked(timeout):
            st = state.load()                          # read again under the lock: another edit is not lost
            m = model.bench(st, here=here)
            if not m["fix"]:
                return dict(m, saved=False)
            m = _apply(st, m, here)
            state.save(st)
        log_fixed(m)                                   # after the save (П-2): a failed save says nothing
        return dict(m, saved=True)
    except (LockedError, OSError):                     # busy, nested, or no right to the lock: the answer only
        return dict(model.bench(state.load(), here=here), saved=False)
