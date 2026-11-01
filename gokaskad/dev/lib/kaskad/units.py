"""systemd units of the traffic engine (work package 1 implements this file).

    kaskad-net.service        oneshot, RemainAfterExit: at boot ``nft -f /etc/kaskad/net/kaskad.nft``,
                              then ``kaskadctl net check --repair --quiet`` (compat rule, command);
                              on stop ``kaskadctl net save-counters --quiet``.
                              DefaultDependencies=no, After=local-fs.target nftables.service
                              netfilter-persistent.service ufw.service firewalld.service,
                              Before=network-pre.target, Wants=network-pre.target,
                              ConditionPathExists=/etc/kaskad/net/kaskad.nft.
    kaskad-net-watch.service  oneshot ``kaskadctl net check --repair --quiet``.
    kaskad-net-watch.timer    OnBootSec=2min, OnUnitActiveSec=1min.

CONTRACT — keep the signatures. Unit files go to paths.systemd_dir(); the nft binary path is taken
from ``runner.which("nft")`` at install time.

Details beyond the list above:
  * kaskad-net.service also has ``Conflicts=shutdown.target`` + ``Before=shutdown.target`` (as
    nftables.service does): with DefaultDependencies=no a unit is otherwise never stopped at
    shutdown, and ExecStop (saving the counters) would not run.
  * ExecStartPost/ExecStop are ``-`` prefixed: the table is loaded by plain ``nft -f`` and a
    broken /opt/kaskad must not mark the unit failed.
  * the timer has ``AccuracySec=10s`` (the default 1 min would make "every minute" every 1-2 min).
  * the paths in the unit texts come from kaskad.paths, so they equal the production paths unless
    KASKAD_ROOT is set (tests).
  * install() enables the service but does not start it (the table is already live; starting it
    would run ``net check`` that waits for the state lock its caller may hold). The caller runs
    activate() AFTER releasing the lock: a unit that was never started is not stopped at shutdown,
    so its ExecStop (saving the counters) would not run on the first reboot (found on the stand,
    2026-10-04: counter 123 came back as 54).
  * remove() stops kaskad-net.service with ``--no-block``: a blocking stop would wait for its
    ExecStop, which waits for the state lock the caller holds; the queued job finishes after the
    caller releases it (ExecStop is a no-op on engine legacy).
"""

import os

from . import paths, util
from .errors import KaskadError
from .i18n import tr

NET_UNIT = "kaskad-net.service"
WATCH_SERVICE = "kaskad-net-watch.service"
WATCH_TIMER = "kaskad-net-watch.timer"
UNITS = (NET_UNIT, WATCH_SERVICE, WATCH_TIMER)
NFT_FALLBACK = "/usr/sbin/nft"

_WANTS = {NET_UNIT: "multi-user.target.wants", WATCH_TIMER: "timers.target.wants"}


def unit_texts(runner):
    """{file_name: text} for the three units."""
    nft = runner.which("nft") or NFT_FALLBACK
    ctl = paths.kaskadctl()
    nft_file = paths.nft_file()
    net = "\n".join([
        "[Unit]",
        "Description=Kaskad 3: port forwards (nftables table ip kaskad)",
        "DefaultDependencies=no",
        "After=local-fs.target nftables.service netfilter-persistent.service ufw.service firewalld.service",
        "Before=network-pre.target shutdown.target",
        "Wants=network-pre.target",
        "Conflicts=shutdown.target",
        "ConditionPathExists=%s" % nft_file,
        "",
        "[Service]",
        "Type=oneshot",
        "RemainAfterExit=yes",
        "ExecStart=%s -f %s" % (nft, nft_file),
        "ExecStartPost=-%s net check --repair --quiet" % ctl,
        "ExecStop=-%s net save-counters --quiet" % ctl,
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])
    watch = "\n".join([
        "[Unit]",
        "Description=Kaskad 3: port forwards watchdog",
        "After=%s" % NET_UNIT,
        "",
        "[Service]",
        "Type=oneshot",
        "ExecStart=%s net check --repair --quiet" % ctl,
        "",
    ])
    timer = "\n".join([
        "[Unit]",
        "Description=Kaskad 3: port forwards watchdog, every minute",
        "",
        "[Timer]",
        "OnBootSec=2min",
        "OnUnitActiveSec=1min",
        "AccuracySec=10s",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])
    return {NET_UNIT: net, WATCH_SERVICE: watch, WATCH_TIMER: timer}


def _links_changed(res):
    """``systemctl enable`` made a link (as kaskad.gw.sys.links_changed; the engine does not import the gateway)."""
    text = (res.err or "") + "\n" + (res.out or "")
    return "Created symlink" in text or "\nRemoved " in "\n" + text


def _systemctl(runner, *args):
    res = runner.run(["systemctl"] + list(args))
    if not res.ok:
        detail = (res.err or res.out or "").strip().splitlines()
        raise KaskadError("systemctl %s: %s" % (" ".join(args), detail[0] if detail else tr("units.code", rc=res.rc)))
    return res


def install(runner):
    """Write the units, ``systemctl daemon-reload``, ``enable kaskad-net.service`` and
    ``enable --now kaskad-net-watch.timer``. Idempotent (rewrites only changed files).
    Returns {"changed": [file names written]}. Raises KaskadError if systemctl fails."""
    d = paths.systemd_dir()
    changed = []
    for name, text in sorted(unit_texts(runner).items()):
        p = os.path.join(d, name)
        if util.read_text(p) != text:
            try:
                util.atomic_write(p, text, 0o644)
            except OSError as e:
                raise KaskadError(tr("units.write_failed", path=p, error=e))
            changed.append(name)
    if changed:
        _systemctl(runner, "daemon-reload")         # before the start below: the timer starts as written
    # --no-reload: a bare enable reloads systemd even when nothing changes (T-M6-qs330b №14) — a reload only for
    # links it made (none when the units were enabled already)
    linked = False
    for args in ((NET_UNIT,), ("--now", WATCH_TIMER)):
        linked = _links_changed(_systemctl(runner, "enable", "--no-reload", *args)) or linked
    if linked and not changed:
        _systemctl(runner, "daemon-reload")
    return {"changed": changed}


def activate(runner):
    """Start kaskad-net.service if it is not active (blocking). Its ExecStart re-loads kaskad.nft,
    which the last apply has just written, so nothing is lost. Call WITHOUT holding the state lock
    (ExecStartPost runs ``net check --repair``, which takes it). Returns True if it was started."""
    if runner.run(["systemctl", "is-active", NET_UNIT]).out.strip() == "active":
        return False
    _systemctl(runner, "start", NET_UNIT)
    return True


def remove(runner):
    """``disable --now`` the timer, disable the service (without stopping the live table — the
    caller decides about the table), delete the files, daemon-reload. Never fails if absent.
    Returns {"removed": [file names deleted]}."""
    runner.run(["systemctl", "disable", "--now", WATCH_TIMER])
    runner.run(["systemctl", "disable", NET_UNIT])
    # stop without waiting: its ExecStop (save-counters) takes the state lock the caller may hold;
    # --no-block lets the job finish after the caller releases it. Without the stop the unit stayed
    # "active (exited)" in memory and the menu showed «выключена, работает» (stand, 2026-10-04).
    runner.run(["systemctl", "stop", "--no-block", NET_UNIT])
    d = paths.systemd_dir()
    removed = []
    for name in UNITS:
        p = os.path.join(d, name)
        if os.path.lexists(p):
            try:
                os.unlink(p)
                removed.append(name)
            except OSError:
                pass
    for name, wants in _WANTS.items():
        link = os.path.join(d, wants, name)
        if os.path.lexists(link):       # left behind if the unit file vanished before disable
            try:
                os.unlink(link)
            except OSError:
                pass
    if removed:
        runner.run(["systemctl", "daemon-reload"])
    return {"removed": removed}


def status(runner):
    """{"kaskad-net.service": {"enabled": bool, "active": bool}, ...} via systemctl is-enabled/is-active."""
    out = {}
    for name in UNITS:
        en = runner.run(["systemctl", "is-enabled", name])
        ac = runner.run(["systemctl", "is-active", name])
        out[name] = {"enabled": en.ok and en.out.strip() not in ("", "disabled", "masked"),
                     "active": ac.ok and ac.out.strip() == "active"}
    return out
