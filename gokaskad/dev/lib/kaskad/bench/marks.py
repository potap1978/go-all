"""The marks the executor of the server check writes in ``state.json → gateway.hostmon.bench`` (SPEC-node-score-
schedule-share §2.1, NS-S4): ``first_manual_at`` after the first manual check that measured, ``pkgs`` after the
answer about the packages or a manual start with sysbench and fio already there.

The rule is ONE — ``bench.machine`` (B10: ``mid``, the reset of every mark on another machine, ``armed_at``); this
module only puts ``machine.mark`` into a transaction of the state (REV-ns-b1-2 З-3, придирка 3):

* a write only when a mark changes: the state is read first without the lock, and again under it; nothing to
  change — no lock, no save, no ``AFTER`` (``AFTER`` runs after the save, outside the lock);
* the lock is waited for ``TIMEOUT`` s at most; a broken ``state.json``, a busy lock, no right to write — the error
  goes up to the caller, who writes a line of the journal and goes on (a mark never stops or loses a check).

    record(first_manual=False, pkgs=None, now=None, timeout=None) -> True written | False nothing written (no
                                  machine id, or the marks are so already)
"""

import time

from .. import state, util
from . import machine

PKGS = ("yes", "no")


def _timer(st):
    from . import sched_unit                    # B11: a mark armed (or disarmed) the schedule — the timer follows
    sched_unit.after_mark(st)


AFTER = [_timer]                        # callables(st) after a mark, once it is SAVED (B11: the timer; SEC П-5 — no
                                        # systemctl under the lock of state.json, no timer ahead of a failed save)
TIMEOUT = 2.0


def _cfg(st):
    """``gateway.hostmon.bench`` of ``st`` for reading (nothing created)."""
    x = st
    for k in ("gateway", "hostmon", "bench"):
        x = x.get(k) if isinstance(x, dict) else None
    return x if isinstance(x, dict) else {}


def _wanted(cfg, here, first_manual, pkgs):
    cur = machine.status(cfg, here)
    out = []
    if first_manual and cur["first_manual_at"] is None:
        out.append("first_manual_at")
    if pkgs is not None and cur["pkgs"] != pkgs:
        out.append("pkgs")
    return out


def record(first_manual=False, pkgs=None, now=None, timeout=None):
    if pkgs is not None and pkgs not in PKGS:
        raise ValueError("pkgs: %r" % (pkgs,))
    here = machine.mid()
    if here is None:
        util.log_tr("bench_run.log.no_machine_id")
        return False
    if not _wanted(_cfg(state.load()), here, first_manual, pkgs):
        return False
    now = int(now if now is not None else time.time())
    with state.transaction(TIMEOUT if timeout is None else timeout) as st:
        todo = _wanted(_cfg(st), here, first_manual, pkgs)
        for field in todo:
            machine.mark(st, field, now if field == "first_manual_at" else pkgs, now, here=here)
    if todo:
        for fn in AFTER:
            fn(st)
    return bool(todo)
