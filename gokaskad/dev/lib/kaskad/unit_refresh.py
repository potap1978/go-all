"""After an update: the units of Kaskad that are already on disk get the text of this version (``kaskadctl update units``).

Most units are rewritten by their owners on the way of an update (``gw apply`` — xray, DNS, the collector, the inbounds,
the rules timers; ``panel unit --refresh``; ``net install-units``; the update timer of install.sh). Some are written only
on an action of their own: the daily exit subscriptions (``kaskad-exit-sub``), the watchdog of the domain certificate
(``kaskad-site``), our certbot renewal (``kaskad-cert-renew``), the IP certificate (``kaskad-ipcert``), the boot unit of
the AmneziaWG kernel module. An update left them with an older text — 3.3.2 T-M6-332b №2: four units kept their Russian
``Description=`` after the update. This module is the one list of such units; ``--k3-post-update`` calls it.

The drop-ins ``kaskad-gate.conf`` of the inbounds (gw/gate.py) — the same: those on disk get this version's text
(T-67-332 №7: their Russian echo stayed after the update), ``gates`` of the result; ``gw apply`` creates and removes them.

Rules (the same as ``gw/sys.ensure_unit``): only a unit whose file exists (none is created or enabled here); one
``daemon-reload`` for all; no service is restarted. A unit that differs only in its soft lines (``Description=``,
``ExecReload=``) — rewritten; a oneshot service — rewritten whatever changed (it runs the new text next time); a timer
whose ``[Timer]`` changed — rewritten and ``try-restart`` (the new schedule now). A long-running service that differs in
its hard lines is left to its owner (a restart is its decision) — counted as ``left``.
"""

import os

from . import util
from .gw import sys as gsys

ONESHOT = "oneshot"     # a run per start: a new text is taken by the next run, no restart
TIMER = "timer"         # a new schedule — try-restart of the timer (its service is not started)
RUNNING = "running"     # a long-running service: only soft lines here, a hard change is its owner's


def registry():
    """[(unit name, text function, kind)] — the units that their owners write only on an action of their own (and the
    argument-free ones that ``gw apply`` writes: an update that did not change the code still gets their captions)."""
    from .bench import sched_unit
    from .gw import awgkmod, collect, exitsubs
    from .gw.probe import unit as probe_unit
    from .ipcert import ops as ipcert
    from .rules import timer as rtimer
    from .site import acme

    def ip_svc():
        return ipcert.unit_texts()[0]

    def ip_timer():
        return ipcert.unit_texts()[1]

    return [
        (exitsubs.SERVICE, exitsubs.service_text, ONESHOT),
        (exitsubs.TIMER, exitsubs.timer_text, TIMER),
        (acme.WATCH_UNIT, acme._watch_service, ONESHOT),
        (acme.WATCH_TIMER, acme._watch_timer, TIMER),
        (acme.RENEW_UNIT, acme._renew_service, ONESHOT),
        (acme.RENEW_TIMER, acme._renew_timer, TIMER),
        (ipcert.UNIT, ip_svc, ONESHOT),
        (ipcert.TIMER, ip_timer, TIMER),
        (awgkmod.BOOT_UNIT, awgkmod.boot_unit_text, ONESHOT),
        (rtimer.SERVICE, rtimer.service_text, ONESHOT),
        (rtimer.TIMER, rtimer.timer_text, TIMER),
        (rtimer.LOAD_SERVICE, rtimer.load_service_text, ONESHOT),
        (rtimer.LOAD_TIMER, rtimer.load_timer_text, TIMER),
        (collect.UNIT, collect.unit_text, RUNNING),
    ] + list(probe_unit.REFRESH) + [            # T6: the units of the probes (kaskad-gw-probe.service/.timer/@)
        (sched_unit.SERVICE, sched_unit.service_text, ONESHOT),       # B11: the schedule of the checks of the server,
        (sched_unit.TIMER, sched_unit.refresh_text, TIMER),           # the timer's text from the state (§2.4)
    ]


def refresh(runner):
    """Rewrite the existing units of ``registry()`` to this version's text. -> {"ok", "written": [...],
    "restarted": [...], "left": [...], "removed": [...]} (``left`` — a long-running service with other hard lines: its
    owner's; ``removed`` — a unit whose owner wants none now: the text function gave None)."""
    written, restarted, left, removed = [], [], [], []
    for name, text_fn, kind in registry():
        path = gsys.unit_path(name)
        if not os.path.isfile(path):
            continue
        old = util.read_text(path)
        new = text_fn()
        if new is None:                             # the owner wants no unit now (an unarmed schedule, REV З-4)
            if gsys.remove_unit(runner, name):
                removed.append(name)
            continue
        if old == new:
            continue
        hard = gsys.unit_needs_restart(old, new)
        if hard and kind == RUNNING:
            left.append(name)
            continue
        if gsys.write_unit(name, new):
            written.append(name)
            if hard and kind == TIMER:
                restarted.append(name)
    gates = _gates(runner)
    if written or gates:
        gsys.systemd(runner, "daemon-reload", check=False)
    for name in restarted:
        gsys.systemd(runner, "try-restart", name, check=False)
    from .gw import gate
    from .rules import rfgate
    return {"ok": True, "written": written + ["%s.d/%s" % (u, gate.NAME) for u in gates], "restarted": restarted,
            "left": left, "removed": removed, "gates": gates, "rf": rfgate.migrate_node()}     # ⚠РФ: the mark of an updated node (rf §3.6)


def _gates(runner):
    """gw/gate.refresh with the state of the gateway; a state that cannot be read — the drop-ins stay as they are."""
    from . import state
    from .errors import KaskadError
    from .gw import gate
    try:
        return gate.refresh(runner, state.load())
    except (KaskadError, OSError, ValueError):
        return []
