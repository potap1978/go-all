"""The gate of the inbound units: no inbound starts while the gateway part of table ip kaskad is missing.

Every unit of an installed inbound (``adapter.units()`` whose unit file exists) gets a drop-in

    /etc/systemd/system/<unit>.d/kaskad-gate.conf
        [Service]
        ExecStartPre=+/bin/sh -c 'exec /usr/sbin/nft list chain ip kaskad gw_pre >/dev/null'

so systemd refuses to start it (boot after a failed load of kaskad.nft, a restart after ``nft flush
ruleset``) until the watchdog has put the part back; ``After=kaskad-net.service`` of the units stays. A
drop-in, not a line in the adapters' unit texts: adapters restart their daemon when their unit text
changes, a drop-in changes nothing for a running daemon (it applies at the next start) — the update that
brings the gate cuts no session. ``+``: the check runs as root whatever user the unit has.

Drop-ins of units that are gone (an inbound uninstalled) are removed by ``sync``; ``remove_all`` takes
every one (``gw purge``). xray of the gateway has the same line in its own unit (gw/xray.py).

The units of WireGuard and AmneziaWG (one interface each, ``START_KINDS``) get one more line (T-M6-qs330b №8: after a
reboot a client of AmneziaWG 3.1 lost the inbound twice, and nothing said who started it again): every start appends
«<seconds since the boot>» to /run/kaskad/gw/start.<unit> (tmpfs: one file per boot) and says so in the journal of the
unit — «Kaskad: inbound kgw-awg3 start #2 since boot, 31 s after it». The collector (gw/ifwatch.py) reads these
records: a second start after the boot is told in /var/log/kaskad.log with who did it. (Whether the interface was there
before cannot be told here: the drop-in's lines run after the unit's own ``ip link delete``.)

The words of a drop-in (its comments, the echo of a start) are neutral English whatever the language of the server, as
the ``Description=`` of the units (T-67-332 №7: a server switched to English kept the Russian echo — a drop-in is not
rewritten on a change of the language); ``refresh`` gives the drop-ins on disk this version's text after an update
(``kaskadctl update units``, kaskad.unit_refresh).
"""

import os
import re

from .. import paths, util
from ..i18n import tr
from . import model, registry
from . import sys as gsys

NAME = "kaskad-gate.conf"
PREFIX = "kaskad-gw-"
LANG = "en"                                          # the words of a drop-in: neutral English, as the units' captions


def dropin_path(unit):
    return os.path.join(paths.systemd_dir(), unit + ".d", NAME)


START_KINDS = ("wg", "awg1", "awg2", "awg3")        # one interface per unit: its starts are recorded (start_line)


def start_record(unit):
    """/run/kaskad/gw/start.<unit>: one line per start of the unit since the boot (written by the unit itself)."""
    return os.path.join(paths.gw_run_dir(), "start." + unit)


def start_line(unit, iface):
    """ExecStartPre of the record of a start (``-``: it never fails the start; ``+``: as root). ``$$`` is a ``$`` for
    the shell (systemd expands ``$X`` itself); no ``%`` (a specifier of systemd). The words — neutral English (LANG):
    the text does not depend on the language of the server or of a request."""
    run, gw, f = paths.run_dir(), paths.gw_run_dir(), start_record(unit)
    words = tr("gw_gate.start_line", LANG, iface=iface, n="$$n", u="$$u").replace("'", "").replace("%", "")
    return ("ExecStartPre=-+/bin/sh -c 'u=$$(cut -d. -f1 /proc/uptime); mkdir -p -m 0711 %s; mkdir -p -m 0700 %s; "
            "echo \"$$u\" >> %s; n=$$(grep -c . %s); "
            "echo \"%s\"'" % (run, gw, f, f, words.replace('"', "")))


def text(runner, iface=None, unit=None):
    nft_bin = runner.which("nft") or "/usr/sbin/nft"
    lines = [
        "# " + tr("gw_gate.comment", LANG),
        "# " + tr("gw_gate.comment_owner", LANG),
        "[Service]",
        "ExecStartPre=+/bin/sh -c 'exec %s list chain ip kaskad gw_pre >/dev/null'" % nft_bin,
    ]
    if iface and unit:
        lines.append(start_line(unit, iface))
    return "\n".join(lines + [""])


def ifaces_of(st):
    """{unit: interface} of the installed WireGuard/AmneziaWG inbounds (START_KINDS) whose adapter names both."""
    out = {}
    for kind in model.installed_kinds(st):
        if kind not in START_KINDS:
            continue
        ad = registry.get(kind)
        try:
            unit, iface = getattr(ad, "unit", None), getattr(ad, "iface", None)
        except Exception:                       # noqa: BLE001 — adapters are other work packages
            continue
        if isinstance(unit, str) and isinstance(iface, str) and re.match(r"^kgw-[a-z0-9]+$", iface):
            out[unit] = iface
    return out


def wanted_units(st):
    """Units of the installed inbounds whose unit files exist (an adapter that fails is skipped)."""
    out = []
    for kind in model.installed_kinds(st):
        ad = registry.get(kind)
        if ad is None:
            continue
        try:
            units = list(ad.units() or [])
        except Exception:                       # noqa: BLE001 — adapters are other work packages
            continue
        for u in units:
            u = str(u)
            if u.startswith(PREFIX) and u.endswith(".service") and u not in out \
                    and os.path.exists(gsys.unit_path(u)):
                out.append(u)
    return sorted(out)


def present():
    """Units that have our drop-in now."""
    d = paths.systemd_dir()
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in sorted(names):
        if n.startswith(PREFIX) and n.endswith(".service.d") and os.path.exists(os.path.join(d, n, NAME)):
            out.append(n[:-2])
    return out


def _remove(unit):
    p = dropin_path(unit)
    try:
        os.unlink(p)
    except OSError:
        return False
    try:
        os.rmdir(os.path.dirname(p))             # only if empty: a drop-in of the owner's stays
    except OSError:
        pass
    return True


def sync(runner, st):
    """Drop-ins for the units of the installed inbounds, none for units that are gone; daemon-reload when
    something changed. Never starts or restarts a unit. Returns the changed unit names."""
    want = wanted_units(st)
    ifaces = ifaces_of(st)
    changed = []
    for u in want:
        p = dropin_path(u)
        body = text(runner, ifaces.get(u), u)
        if util.read_text(p) != body:
            d = os.path.dirname(p)
            if not os.path.isdir(d):
                os.makedirs(d, 0o755)
            util.atomic_write(p, body, 0o644)
            changed.append(u)
    for u in present():
        if u not in want and _remove(u):
            changed.append(u)
    if changed:
        gsys.systemd(runner, "daemon-reload")
    return changed


def refresh(runner, st):
    """After an update: the drop-ins already on disk get this version's text (none created or removed — that is
    ``sync`` of ``gw apply``). No daemon-reload here (the caller does one for all), no unit is restarted: a drop-in
    applies at the next start. Returns the changed unit names."""
    ifaces = ifaces_of(st)
    changed = []
    for u in present():
        p = dropin_path(u)
        body = text(runner, ifaces.get(u), u)
        if util.read_text(p) != body:
            util.atomic_write(p, body, 0o644)
            changed.append(u)
    return changed


def remove_all(runner):
    """Every drop-in of ours (``gw purge``). Returns the unit names."""
    gone = [u for u in present() if _remove(u)]
    if gone:
        runner.run(["systemctl", "daemon-reload"])
    return gone
