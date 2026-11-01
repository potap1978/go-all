"""«💻 Система» (get_system_stats of 2.x) from /proc, without forks.

CPU — over a real 0.5 s sample of /proc/stat (2.x divided the counters since boot); the top processes — by
their CPU time within the same sample (``ps aux`` shows the average over a process's whole life). Missing
/proc (not Linux) gives «—» rather than zeros.

SPEC-node-score §3.9 п. 4 (A6): the first line is the light of the node's health and ≤ 3 findings
(``hostprobe.read_health``: a fresh live.json, the bot's host-health.json, else a snapshot of its own — then «без
истории»); the steal of the sample is a line of its own («Соседи (steal): 0,4 %»), not a part of the busy CPU.
"""

import os
import time

from .. import paths
from .lang import fmt_num, tr
from .telegram import h

SAMPLE = 0.5
TOP = 7


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def cpu_counters(text):
    """(busy, total, steal) jiffies of the «cpu» line of /proc/stat (guest time is inside user already). The steal
    is not busy (as ``gw/hostmon.derive``): the time other virtual machines took is said by itself."""
    for line in (text or "").splitlines():
        parts = line.split()
        if parts and parts[0] == "cpu":
            vals = [int(x) for x in parts[1:9] if x.isdigit()]
            vals += [0] * (8 - len(vals))
            user, nice, system, idle, iowait, irq, softirq, steal = vals
            total = user + nice + system + idle + iowait + irq + softirq + steal
            return total - idle - iowait - steal, total, steal
    return None


def processes():
    """{pid: (name, cpu jiffies, rss pages)} of /proc/<pid>/stat."""
    out = {}
    root = paths.P("/proc")
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for n in names:
        if not n.isdigit():
            continue
        text = _read(os.path.join(root, n, "stat"))
        if not text or ")" not in text:
            continue
        name = text[text.find("(") + 1:text.rfind(")")]
        rest = text[text.rfind(")") + 1:].split()
        try:
            out[int(n)] = (name, int(rest[11]) + int(rest[12]), int(rest[21]))
        except (IndexError, ValueError):
            continue
    return out


def meminfo():
    out = {}
    for line in (_read(paths.P("/proc/meminfo")) or "").splitlines():
        k, _, v = line.partition(":")
        parts = v.split()
        if parts and parts[0].isdigit():
            out[k.strip()] = int(parts[0]) * 1024
    return out


def _uptime_text(seconds):
    s = int(seconds)
    d, s = divmod(s, 86400)
    hh, s = divmod(s, 3600)
    m = s // 60
    parts = []
    if d:
        parts.append(tr("bot.sys.days", n=d))
    if hh:
        parts.append(tr("bot.sys.hours", n=hh))
    parts.append(tr("bot.sys.minutes", n=m))
    return " ".join(parts)


def _gb(n):
    n = float(n)
    units = ("b", "kb", "mb", "gb", "tb")
    for unit in units:
        if n < 1024 or unit == "tb":
            word = tr("bot.sys.unit_" + unit)
            return "%s %s" % (fmt_num(n, None, digits=0 if unit in ("b", "kb") else 1), word)
        n /= 1024.0


def health():
    """(health, by) of ``hostprobe.read_health`` with its texts in the language of the chat, or None (§3.9 п. 4):
    the screen never fails for the light health."""
    try:
        from ..gw import hostmon, hostprobe
        h, by = hostprobe.read_health()
        return (hostmon.health_in(h), by) if isinstance(h, dict) else None
    except Exception:                                # noqa: BLE001 — «Система» shows the rest
        return None


def stats(sleep=time.sleep, statvfs=os.statvfs, clk_tck=None, page_size=None, health=health):
    """Everything «Система» shows; values that could not be read are None."""
    clk = clk_tck or (os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100)
    page = page_size or (os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096)
    c0 = cpu_counters(_read(paths.P("/proc/stat")))
    p0 = processes()
    t0 = time.monotonic()
    sleep(SAMPLE)
    c1 = cpu_counters(_read(paths.P("/proc/stat")))
    p1 = processes()
    dt = max(time.monotonic() - t0, SAMPLE)
    out = {"cores": os.cpu_count(), "cpu": None, "load": None, "uptime": None, "mem": None, "swap": None,
           "disk": None, "top": [], "steal": None, "health": None}
    if c0 and c1 and c1[1] > c0[1]:
        out["cpu"] = 100.0 * (c1[0] - c0[0]) / (c1[1] - c0[1])
        out["steal"] = max(0.0, 100.0 * (c1[2] - c0[2]) / (c1[1] - c0[1]))
    out["health"] = health() if health is not None else None
    la = (_read(paths.P("/proc/loadavg")) or "").split()
    if len(la) >= 3:
        out["load"] = " ".join(la[:3])
    up = (_read(paths.P("/proc/uptime")) or "").split()
    if up:
        try:
            out["uptime"] = float(up[0])
        except ValueError:
            pass
    mi = meminfo()
    total = mi.get("MemTotal")
    if total:
        avail = mi.get("MemAvailable", mi.get("MemFree", 0))
        out["mem"] = (total - avail, total)
    if "SwapTotal" in mi:
        out["swap"] = (mi["SwapTotal"] - mi.get("SwapFree", 0), mi["SwapTotal"])
    try:
        v = statvfs("/")
        size = v.f_blocks * v.f_frsize
        free = v.f_bavail * v.f_frsize
        used = size - v.f_bfree * v.f_frsize
        out["disk"] = (used, size, (100.0 * used / (used + free)) if used + free else 0.0)
    except OSError:
        pass
    rows = []
    for pid, (name, j1, rss) in p1.items():
        j0 = p0.get(pid, (name, j1, rss))[1]
        cpu = 100.0 * (j1 - j0) / clk / dt
        mem = (100.0 * rss * page / total) if total else 0.0
        rows.append((cpu, mem, pid, name))
    rows.sort(key=lambda r: (-r[0], -r[1], r[2]))
    out["top"] = rows[:TOP]
    return out


def _pct(v, digits=1):
    """A percent as the language says it (REV-ns-a6 П-3): «40,0 %» / «40.0%» — one look for every number here."""
    return tr("bot.sys.pct", pct=fmt_num(v, None, digits=digits))


def _load(text):
    """«0.10 0.20 0.30» of /proc/loadavg in the numbers of the language («0,10 0,20 0,30»)."""
    return " ".join(fmt_num(x, None, digits=2) for x in str(text).split())


def render(s):
    def mb(n):
        return int(n // (1024 * 1024))

    lines = [tr("bot.sys.title"), ""]
    lines += health_lines(s.get("health"))
    lines.append("<b>Uptime:</b> %s" % (_uptime_text(s["uptime"]) if s.get("uptime") is not None else "—"))
    lines.append(tr("bot.sys.cpu", cores=s.get("cores") or "?") + " | "
                 + (_pct(s["cpu"]) if s.get("cpu") is not None else "—"))
    if s.get("steal") is not None:
        lines.append(tr("bot.sys.steal", pct=_pct(s["steal"])))
    lines.append("<b>Load:</b> %s" % h(_load(s["load"]) if s.get("load") else "—"))
    if s.get("mem"):
        used, total = s["mem"]
        lines.append("<b>RAM:</b> %d/%dMB (%s)" % (mb(used), mb(total), _pct(100.0 * used / total)))
    else:
        lines.append("<b>RAM:</b> —")
    if s.get("swap") and s["swap"][1] > 0:
        lines.append("<b>Swap:</b> %d/%dMB" % (mb(s["swap"][0]), mb(s["swap"][1])))
    else:
        lines.append(tr("bot.sys.no_swap"))
    if s.get("disk"):
        used, size, pct = s["disk"]
        lines.append("<b>Disk /:</b> %s / %s (%s)" % (_gb(used), _gb(size), _pct(round(pct), 0)))
    else:
        lines.append("<b>Disk /:</b> —")
    lines.append("")
    if s.get("top"):
        rows = ["PID     CPU%  MEM%  CMD"]
        for cpu, mem, pid, name in s["top"]:
            rows.append("%-7d %4.1f  %4.1f  %s" % (pid, cpu, mem, name[:20]))
        lines.append(tr("bot.sys.top", sec=fmt_num(SAMPLE, None, digits=1)) + "\n<pre>%s</pre>" % h("\n".join(rows)))
    return "\n".join(lines)


HEALTH_LEVELS = ("ok", "warn", "crit", "wait", "off")
HEALTH_ICONS = {"ok": "🟢", "warn": "🟡", "crit": "🔴", "wait": "⚪", "off": "⚪"}
HEALTH_TOP = 3


def health_lines(hb):
    """The light and ≤ 3 findings (crit first — the order of ``hostmon.light``), «без истории» for a health without
    the automaton (the bot's or the panel's snapshot, ``instant``), then an empty line; nothing when there is none."""
    hl, _by = hb if isinstance(hb, tuple) and len(hb) == 2 else (None, None)
    if not isinstance(hl, dict):
        return []
    lvl = hl.get("level") if hl.get("level") in HEALTH_LEVELS else "wait"
    # the words of the light are the panel's (gw_host.light.*, hostmon.READER_KEYS — one source, REV-ns-a6 П-2)
    lines = ["%s <b>%s</b>" % (HEALTH_ICONS[lvl], tr("gw_host.light." + lvl))]
    for f in (hl.get("top") or [])[:HEALTH_TOP]:
        if isinstance(f, dict) and f.get("text"):
            lines.append("• " + h(f["text"]))
    if hl.get("instant") is True:
        lines.append("<i>%s</i>" % tr("gw_host.light.instant"))
    return lines + [""]
