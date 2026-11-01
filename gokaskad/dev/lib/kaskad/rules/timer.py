"""The units of the rules (docs/k3/SPEC-wave3.md §1, §2):

    kaskad-gw-rules.service        oneshot ``kaskadctl rules update`` (downloads without the state lock, applies
                                   under it)
    kaskad-gw-rules.timer          OnCalendar=daily, RandomizedDelaySec=2h (nodes do not hit GitHub at one minute),
                                   Persistent=true (a node that was off at midnight updates at boot)
    kaskad-gw-rules-load.service   oneshot ``kaskadctl rules load --quiet``: the networks of Russia into gw_ru in a
                                   transaction of their own (``kaskad.rules.apply.ensure_kernel``) — a no-op when
                                   gw_ru_ver already names the stored lists, a pause after a failure
    kaskad-gw-rules-load.timer     OnBootSec=20s (after kaskad-net loaded the table, whose gw_ru is empty: the boot
                                   file never carries the ranges), then 5 minutes after each run — a table recreated
                                   meanwhile (``nft flush ruleset`` + restore) gets its ranges back without an apply;
                                   the watchdog that put the table back starts the load at once (``after_restore``)

Installed with the gateway (``kaskad.gw.ops._apply``), stopped by ``gw disable``, removed by ``gw purge``. A node
without lists gets its first download at once (``kick``: ``systemctl start --no-block`` of the update, at most
once an hour) — the daily timer alone would wait for the next midnight.
"""

import os
import time

from .. import paths, util
from ..gw import sys as gsys
from . import store

SERVICE = "kaskad-gw-rules.service"
TIMER = "kaskad-gw-rules.timer"
LOAD_SERVICE = "kaskad-gw-rules-load.service"
LOAD_TIMER = "kaskad-gw-rules-load.timer"
UNITS = (TIMER, SERVICE, LOAD_TIMER, LOAD_SERVICE)
TIMERS = (TIMER, LOAD_TIMER)
KICK_EVERY = 3600

_now = time.time


def service_text():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: update of the RoscomVPN lists (rules \"Russia direct\")",  # one text for every language
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        "ExecStart=%s rules update" % paths.kaskadctl(),
        "TimeoutStartSec=15min",
        "",
    ])


def timer_text():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: RoscomVPN lists once a day",  # one text for every language
        "",
        "[Timer]",
        "OnCalendar=daily",
        "RandomizedDelaySec=2h",
        "Persistent=true",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


def load_service_text():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: Russian networks into the nftables table (rules \"Russia direct\")",
        "After=kaskad-net.service",
        "",
        "[Service]",
        "Type=oneshot",
        "ExecStart=%s rules load --quiet" % paths.kaskadctl(),
        "TimeoutStartSec=5min",
        "",
    ])


def load_timer_text():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: Russian networks into the table at boot and every 5 minutes if missing",
        "",
        "[Timer]",
        "OnBootSec=20s",
        "OnUnitInactiveSec=5min",
        "AccuracySec=30s",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


def ensure(runner):
    """The four units written (daemon-reload when one changed), both timers enabled and started. Returns
    {"written", "started"}."""
    written = False
    for name, text in ((SERVICE, service_text()), (LOAD_SERVICE, load_service_text())):
        written = gsys.write_unit(name, text) or written
    started = False
    reloaded = False
    for name, text in ((TIMER, timer_text()), (LOAD_TIMER, load_timer_text())):
        r = gsys.ensure_unit(runner, name, text)
        reloaded = reloaded or r["written"]
        started = started or r["started"]
    if written and not reloaded:
        gsys.systemd(runner, "daemon-reload")
    return {"written": bool(written or reloaded), "started": started}


def _kick_stamp():
    return os.path.join(paths.gw_run_dir(), "rules-kick.stamp")


def kick(runner):
    """No lists yet: start the update now, in the background (``--no-block``: the caller may hold the state lock
    the service needs for its apply). At most once per KICK_EVERY. True if started."""
    if store.active() is not None:
        return False
    stamp = _kick_stamp()
    try:
        if _now() - os.stat(stamp).st_mtime < KICK_EVERY:
            return False
    except OSError:
        pass
    try:
        util.atomic_write(stamp, "%d\n" % int(_now()), 0o600)
    except OSError:
        pass
    return runner.run(["systemctl", "start", "--no-block", SERVICE]).ok


def after_restore(runner):
    """The gateway part of the table has just been put back (the collector, ``net check``): a recreated gw_ru is
    empty and kaskad-gw-rules-load.timer would bring the networks of Russia back only within 5 minutes, while the
    state of the last load still said «загружены» to the panel (stand T-G1, D3-3). When the table does not hold
    the stored lists (one small read of gw_ru_ver): that state says so at once and the load starts now
    (``--no-block``: the caller holds the state lock the load waits for). True — the load was started."""
    from ..errors import KaskadError
    from ..gw import net
    from . import apply as rapply
    wanted = net.ru_wanted()
    if not wanted:
        return False                                    # no lists: nothing to load
    try:
        if net.read_ru_version(runner) == wanted:
            return False
    except KaskadError:
        pass
    rapply.table_recreated(wanted)
    if not os.path.exists(gsys.unit_path(LOAD_SERVICE)):
        return False
    return runner.run(["systemctl", "start", "--no-block", LOAD_SERVICE]).ok


def stop(runner):
    """Both timers off (``gw disable``); the lists stay. True if they were there."""
    found = False
    for name in TIMERS:
        if os.path.exists(gsys.unit_path(name)):
            runner.run(["systemctl", "disable", "--now", name])
            found = True
    return found


def remove(runner):
    """All four units away (``gw purge``). Returns the removed unit names."""
    return [u for u in UNITS if gsys.remove_unit(runner, u)]


def status(runner):
    return {"timer": {"active": gsys.unit_active(runner, TIMER), "enabled": gsys.unit_enabled(runner, TIMER)},
            "service": {"active": gsys.unit_active(runner, SERVICE)},
            "load_timer": {"active": gsys.unit_active(runner, LOAD_TIMER),
                           "enabled": gsys.unit_enabled(runner, LOAD_TIMER)}}
