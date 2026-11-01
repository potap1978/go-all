"""After a reboot (stand T-G1, R-1): the watchdogs put back what every reboot loses — ip rules and routes of the
gateway, the iptables compat rules — and the boot file of the table is older than the live map of the exits
(gw_route follows the exits watchdog, the file follows applies). That is the normal restore, not a breakdown, yet
the journal said «NET repair … 26 расхождений» and «GW legs … не привёл ноги в порядок (код 1)» (the legs were
still starting under systemd).

While /run has no mark of ours (``pending``) and the node is up less than WINDOW s, the watchdogs say what they
put back in one calm line — «после перезагрузки: правила шлюза восстановлены (…)» — and wait for the exits'
legs without alarm. What could not be repaired is logged as always. The mark (/run/kaskad/boot.done: tmpfs, a
reboot clears it) is written by the collector after its first round in which the gateway is whole; past the
window nothing is «after a reboot» any more, mark or not.

One line per boot (T-M6-qs330c №6): a part put back twice in the window (the collector's second round found ip rules
lost again while the legs were still coming up) is not told twice — /run/kaskad/boot.said keeps what was told.
"""

import os

from . import paths, util
from .i18n import tr

WINDOW = 300                    # s of uptime: later nothing is «the restore after a reboot»

# what a reboot always loses (kinds of the watchdog's problems): repaired at boot, they are the restore. A table
# that did not load at all is not among them (kaskad-net.service loads it before anything else): that stays news.
# Values are catalog keys: summary() says them in the language of the moment (callers pass them through as they are).
KINDS = dict((k, "boot.kind." + k) for k in ("drift", "compat_missing", "ip_forward", "gw_routing", "gw_rp_filter",
                                              "gw_ip6", "gw_compat_in"))


def mark_path():
    return os.path.join(paths.run_dir(), "boot.done")


def uptime():
    """Seconds since the boot (/proc/uptime), None when unreadable."""
    raw = (util.read_text(paths.P("/proc/uptime")) or "").split()
    try:
        return float(raw[0]) if raw else None
    except ValueError:
        return None


def pending():
    """The restore after a reboot is going on: no mark yet and the node is up less than WINDOW s."""
    if os.path.exists(mark_path()):
        return False
    up = uptime()
    return up is not None and up < WINDOW


def done():
    """The gateway is whole after the boot: write the mark (once; a failure to write changes nothing)."""
    if os.path.exists(mark_path()):
        return False
    try:
        util.atomic_write(mark_path(), "%d\n" % int(uptime() or 0), 0o600)
    except OSError:
        return False
    return True


def said_path():
    return os.path.join(paths.run_dir(), "boot.said")


def first(key):
    """True the first time ``key`` is told after this boot (the list in /run — a reboot clears it). A list that cannot
    be written changes nothing: then it is told again, never lost."""
    said = [x for x in (util.read_text(said_path()) or "").split("\n") if x]
    if key in said:
        return False
    try:
        util.atomic_write(said_path(), "\n".join(said + [key]) + "\n", 0o600)
    except OSError:
        pass
    return True


def summary(what, prefix):
    """«<prefix> после перезагрузки: правила шлюза восстановлены (a, b)» of the parts put back and not told yet after
    this boot; None when there is nothing new to tell."""
    parts = []
    for w in what:
        if w and w not in parts and first("%s: %s" % (prefix, w)):
            parts.append(w)
    if not parts:
        return None
    return tr("boot.summary", prefix=prefix, parts=", ".join(tr(w) if w.startswith("boot.kind.") else w for w in parts))
