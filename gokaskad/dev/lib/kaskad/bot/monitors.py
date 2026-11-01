"""/etc/kaskad/monitors of 2.x: one ``<ip>.conf`` per monitored server, parsed (never sourced)::

    MON_IP="203.0.113.5"
    MON_INTERVAL=60
    MON_THRESHOLD=100
    MON_COOLDOWN=300

and ``.last_alert_<ip>`` (epoch of the last alert — the cooldown survives a switch between the bots).
``.last_check_<ip>`` belongs to the 2.x daemon; it is removed with the monitor, as 2.x does.
"""

import os
import re

from .. import paths, util
from .config import _value
from .data import valid_ip
from .lang import tr

_LINE = re.compile(r"^\s*(MON_[A-Z]+)=(.*)$")
DEFAULT_COOLDOWN = 300
# Порог нового монитора (мс) — так же проверяют меню 7 (k3_read_threshold) и бот на bash. Файлы прежних версий
# читаются с порогом до 65535: старый монитор не пропадает из-за нового предела.
THRESHOLD_MAX = 10000


def threshold_hint():
    return tr("bot.mon.threshold_hint", max=THRESHOLD_MAX)


def parse_threshold(text):
    """Порог из ввода человека: int 1…THRESHOLD_MAX или None (буквы, 0, слишком много)."""
    s = "".join(str(text if text is not None else "").split())    # «1 50» — как меню и бот на bash (пробелы прочь)
    if not re.match(r"^[0-9]{1,5}$", s):                             # только ASCII-цифры: «١٥٠» — нет, как в bash
        return None
    n = int(s)
    return n if 1 <= n <= THRESHOLD_MAX else None


def _int(v, lo, hi, default=None):
    s = str(v if v is not None else "").strip()
    if not re.fullmatch(r"[0-9]{1,9}", s):            # ASCII digits, as k3_mon_load (SEC-3372 П-4, REV-3372 П-1)
        return default
    n = int(s)
    return n if lo <= n <= hi else default


def parse(text):
    """{"ip", "interval", "threshold", "cooldown"} or None (no valid IP, interval or threshold)."""
    vals = {}
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if m:
            vals[m.group(1)] = _value(m.group(2))
    ip = vals.get("MON_IP", "").strip()
    interval = _int(vals.get("MON_INTERVAL"), 1, 86400)
    threshold = _int(vals.get("MON_THRESHOLD"), 1, 65535)
    cooldown = _int(vals.get("MON_COOLDOWN"), 0, 7 * 86400, DEFAULT_COOLDOWN)
    if not valid_ip(ip) or interval is None or threshold is None:
        return None
    return {"ip": ip, "interval": interval, "threshold": threshold, "cooldown": cooldown}


def render(m):
    """The text of a monitor file — the one format (``write``; the bash reader k3_mon_load takes exactly this)."""
    return 'MON_IP="%s"\nMON_INTERVAL=%d\nMON_THRESHOLD=%d\nMON_COOLDOWN=%d\n' % (
        m["ip"], int(m["interval"]), int(m["threshold"]), int(m["cooldown"]))


def clean_text(text):
    """A monitor file from outside (a copy .kbk, a file of an older version) -> (record, its text in our format) or
    (None, None) when it is not a monitor (REV-3371-2 Б-1: bash of 2.x read these files with ``source`` — every other
    line, «$(…)», «;» is dropped: only the keys of ``parse``, checked)."""
    m = parse(text)
    return (m, render(m)) if m is not None else (None, None)


def clean_dir():
    """The monitor files already on disk, after an update (REV-3371-2 Б-1, 3.3.7.2): each ``*.conf`` rewritten in our
    format; a file that is not a monitor (no address, interval or threshold) — removed; a second file of the same
    address — removed (kept: «<ip>.conf» itself, else the first by name). Every change — in the journal.
    -> {"rewritten", "dropped"} (names)."""
    d = paths.monitors()
    out = {"rewritten": [], "dropped": []}
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    files = []
    for n in names:
        if n.startswith(".") or not n.endswith(".conf"):
            continue
        path = os.path.join(d, n)
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        raw = util.read_text(path) or ""
        m, text = clean_text(raw)
        files.append((n, path, raw, m, text))
    keep = {}                                         # ip -> the file kept: «<ip>.conf» itself, else the first
    for f in files:
        m = f[3]
        if m is not None and (m["ip"] not in keep or f[1] == conf_path(m["ip"])):
            keep[m["ip"]] = f
    kept = set(id(f) for f in keep.values())
    for f in files:                                   # drops first: the file kept may be renamed into its place
        if id(f) in kept:
            continue
        n, path, m = f[0], f[1], f[3]
        try:
            os.unlink(path)
        except OSError:
            continue
        out["dropped"].append(n)
        util.log_action("MONITOR DROP: %s — %s"
                        % (_safe(n), "not a monitor file" if m is None else "same address twice"))
    for f in files:
        if id(f) not in kept:
            continue
        n, path, raw, m, text = f
        want = conf_path(m["ip"])
        if want != path or raw != text:
            util.atomic_write(want, text, 0o600)
            if want != path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            out["rewritten"].append(n)
            util.log_action("MONITOR CLEAN: %s → %s.conf" % (_safe(n), m["ip"]))
    return out


def _safe(name):
    return re.sub(r"[^0-9A-Za-z._:-]", "?", str(name))[:80]


def conf_path(ip):
    if not valid_ip(ip):
        raise ValueError("address %r" % (ip,))
    return os.path.join(paths.monitors(), "%s.conf" % ip)


def read_all():
    d = paths.monitors()
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = {}
    for n in names:
        if n.startswith(".") or not n.endswith(".conf"):
            continue
        m = parse(util.read_text(os.path.join(d, n)) or "")
        if m is not None and m["ip"] not in out:
            out[m["ip"]] = m
    return [out[ip] for ip in sorted(out, key=lambda ip: tuple(int(x) for x in ip.split(".")))]


def get(ip):
    for m in read_all():
        if m["ip"] == ip:
            return m
    return None


def write(ip, interval, threshold, cooldown):
    p = conf_path(ip)
    d = os.path.dirname(p)
    if not os.path.isdir(d):
        os.makedirs(d, 0o700)
    util.atomic_write(p, render({"ip": ip, "interval": interval, "threshold": threshold, "cooldown": cooldown}), 0o600)
    util.log_action("MONITOR ADD: %s interval=%ds threshold=%dms cooldown=%ds"
                    % (ip, int(interval), int(threshold), int(cooldown)))


def remove(ip):
    """The monitor of ``ip`` and its runtime files away. True if there was one."""
    p = conf_path(ip)
    had = os.path.exists(p)
    for f in (p, os.path.join(paths.monitors(), ".last_check_%s" % ip), os.path.join(paths.monitors(), ".last_alert_%s" % ip)):
        try:
            os.unlink(f)
        except OSError:
            pass
    if had:
        util.log_action("MONITOR REMOVE: %s" % ip)
    return had


def remove_all():
    """Every ``*.conf`` away (2.x «Сбросить всё»). Returns how many."""
    d = paths.monitors()
    n = 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for name in names:
        if name.endswith(".conf") and not name.startswith("."):
            try:
                os.unlink(os.path.join(d, name))
                n += 1
            except OSError:
                pass
    return n


def last_alert(ip):
    raw = (util.read_text(os.path.join(paths.monitors(), ".last_alert_%s" % ip)) or "").strip()
    return int(raw) if re.fullmatch(r"[0-9]{1,12}", raw) else 0


def set_last_alert(ip, ts):
    if not valid_ip(ip):
        return
    try:
        util.atomic_write(os.path.join(paths.monitors(), ".last_alert_%s" % ip), "%d\n" % int(ts), 0o600)
    except OSError:
        pass
