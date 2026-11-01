"""Raw counters of the node for the light health (SPEC-node-score §3.1; A1). ONLY reading: no command, no process,
no arithmetic beyond parsing — the percents, the rates and the rules are ``gw/hostmon.py``.

    read(cfg, mono, every=()) -> raw          one round; ``every`` — the sets due this round: "minute", "hour"
    clock()                   -> {"synced", "offset_s"} | None     one adjtimex() (collect._clock_synced wraps it)
    virt()                    -> "kvm" | "lxc" | "openvz" | "docker" | … | "none" | None   once per process
    own_addrs(mono)           -> (address, …)   the addresses of the interfaces (fib_trie, if_inet6), kept 60 s
    read_health(now, lang=)   -> (health, by)   for a reader: live.json → host-health.json → hostmon.instant (§3.9 п. 4)
    hostmon_path() / health_path() / probe_state_path()   the files of the light health (§3.7)

Every file goes through ``paths.P`` (tests set ``KASKAD_ROOT``); a file that cannot be read gives ``None`` in its
field — never an exception. A source the kernel does not have at all (no PSI, no nf_conntrack) is named in
``raw["absent"]``: ``hostmon`` tells «не проверяется» (a real reason) from «сбой одного чтения» (a temporary one) by it.

The round set (10 s, 15 files): /proc/stat (the line ``cpu``, 8 fields; the lines ``cpuN`` — the cores), loadavg,
meminfo, pressure/{cpu,memory,io}, vmstat (oom_kill), net/dev, net/softnet_stat, net/snmp, net/netstat,
sys/net/netfilter/nf_conntrack_{count,max}, sys/fs/file-nr, uptime.
The minute set: statvfs of /, /var and the FS of /var/lib/kaskad (one record per f_fsid), adjtimex, the interfaces of
the default route (net/route, net/ipv6_route; kept 60 s and given every round in ``ifaces``), memory.events of every
top-level cgroup (``cg.slices``, ≤ SLICES_MAX; NS-D17) and of our own units with a memory limit (``cg.units``, ≤
UNITS_MAX: only to name the unit of ``oom_svc``), open files of xray and amneziawg-go (``fd_proc``).
The lists (top-level cgroups, our limited units, the PIDs) are taken once an hour; a unit gone between two reads is
``None`` in ``cg.units`` (a oneshot probe after an OOM: its cgroup is removed), not an error.
The hour set: the certificates (the site's, as ``summary._site``; the IP one — ``ipcert.store.status()``).
Once per process: the virtualisation (/proc/1/environ ``container=``, /run/systemd/container, /proc/vz, the flag
``hypervisor`` of /proc/cpuinfo — no ``systemd-detect-virt``), the cgroup version and ``memory_localevents``.

The PIDs (A1 «сверить»): the collector does not keep the PIDs of its services (``live`` asks ``systemctl
is-active``), so they are read here without a command: xray — ``cgroup.procs`` of kaskad-gw-xray.service;
amneziawg-go — the process of that name with the most open files (/proc/*/comm, once an hour).
"""

import calendar
import errno
import os
import time

from .. import paths

SLICES_MAX = 8                     # memory.events of the top-level cgroups read each minute (more — ``cg.over``)
UNITS_MAX = 4                      # our own units with a memory limit (only to name the unit of oom_svc)
LIST_EVERY = 3600.0                # s: the lists of cgroups, units and PIDs are taken again this often
ROUTE_EVERY = 60.0                 # s: the interfaces of the default route are kept this long
FS_MOUNTS = ("/", "/var", "/var/lib/kaskad")
XRAY_UNIT = "kaskad-gw-xray.service"           # = live.XRAY_UNIT (a test compares them)
SLICE_FIRST = ("system.slice", "user.slice", "machine.slice")
CPU_FIELDS = 8                     # user nice system idle iowait irq softirq steal
CONTAINERS = ("lxc", "lxc-libvirt", "openvz", "docker", "podman", "systemd-nspawn", "wsl", "container-other")

TIME_ERROR = 5                     # adjtimex(): the clock is not synchronised
STA_UNSYNC = 0x0040
STA_NANO = 0x2000


class _Cache(object):
    """What lives longer than a round: the route, the lists of the hour, the facts of the process."""

    def __init__(self):
        self.ifaces = None         # [names] of the default route
        self.ifaces_at = None      # mono of that read
        self.lists_at = None       # mono of the last list of cgroups / units / PIDs
        self.slices = []           # top-level cgroups with memory.events
        self.over = 0              # top-level cgroups left out (more than SLICES_MAX)
        self.units = {}            # {unit: memory.max in bytes} of our limited units
        self.pids = []             # [[who, pid]]
        self.cgv = None            # 2 | 1 | None (unknown yet)
        self.local = None          # memory_localevents of the cgroup2 mount
        self.virt = None
        self.virt_done = False
        self.own = ()              # the addresses of the interfaces (own_addrs)
        self.own_at = None


_C = _Cache()


def reset():
    """Forget every cache (tests; a new process starts clean anyway)."""
    global _C
    _C = _Cache()


# ── small readers: each returns None on any error ──────────────────────────

def _text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, ValueError):
        return None


def _p(path):
    return paths.P(path)


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _stat(text):
    """/proc/stat -> ([8 cpu fields], cores) — each part None when it cannot be read."""
    if not text:
        return None, None
    cpu, cores = None, 0
    for line in text.splitlines():
        f = line.split()
        if not f:
            continue
        if f[0] == "cpu":
            vals = [_int(x) for x in f[1:1 + CPU_FIELDS]]
            if len(vals) >= 4 and all(v is not None and v >= 0 for v in vals):
                cpu = vals + [0] * (CPU_FIELDS - len(vals))
        elif f[0].startswith("cpu") and f[0][3:].isdigit():
            cores += 1
    return cpu, (cores or None)


def _loadavg(text):
    f = (text or "").split()
    vals = [_float(x) for x in f[:3]]
    return vals if len(vals) == 3 and all(v is not None and v >= 0 for v in vals) else None


def _meminfo(text):
    mem = {}
    for line in (text or "").splitlines():
        k, _, v = line.partition(":")
        f = v.split()
        if f and f[0].isdigit():
            mem[k.strip()] = int(f[0]) * 1024
    if not mem.get("MemTotal"):
        return None
    avail = mem.get("MemAvailable")
    if avail is None and "MemFree" in mem:
        avail = mem["MemFree"] + mem.get("Buffers", 0) + mem.get("Cached", 0)
    if avail is None:
        return None
    return {"total": mem["MemTotal"], "avail": avail, "swap_total": mem.get("SwapTotal", 0),
            "swap_free": mem.get("SwapFree", 0)}


def _psi(text):
    """``some avg10=… avg60=12.34 …`` -> 12.34."""
    for line in (text or "").splitlines():
        f = line.split()
        if f and f[0] == "some":
            for part in f[1:]:
                k, _, v = part.partition("=")
                if k == "avg60":
                    x = _float(v)
                    return x if x is not None and x >= 0 else None
    return None


def _vmstat(text):
    for line in (text or "").splitlines():
        f = line.split()
        if len(f) == 2 and f[0] == "oom_kill":
            return _int(f[1])
    return None


def _netdev(text):
    """/proc/net/dev -> {iface: {rx_bytes, rx_packets, rx_errs, rx_drop, tx_bytes, tx_packets, tx_errs, tx_drop}}."""
    out = {}
    for line in (text or "").splitlines()[2:]:
        name, sep, rest = line.partition(":")
        f = rest.split()
        if not sep or len(f) < 16:
            continue
        v = [_int(x) for x in f[:16]]
        if any(x is None for x in v):
            continue
        out[name.strip()] = {"rx_bytes": v[0], "rx_packets": v[1], "rx_errs": v[2], "rx_drop": v[3],
                             "tx_bytes": v[8], "tx_packets": v[9], "tx_errs": v[10], "tx_drop": v[11]}
    return out or None


def _softnet(text):
    """/proc/net/softnet_stat (hex; per core: processed dropped time_squeeze …) -> the sums over the cores."""
    dropped = squeezed = 0
    rows = 0
    for line in (text or "").splitlines():
        f = line.split()
        if len(f) < 3:
            continue
        try:
            dropped += int(f[1], 16)
            squeezed += int(f[2], 16)
        except ValueError:
            return None
        rows += 1
    return {"dropped": dropped, "squeezed": squeezed} if rows else None


def _pairs(text, head):
    """/proc/net/snmp, /proc/net/netstat: the header line and the value line of ``head`` -> {name: int}."""
    lines = [ln.split() for ln in (text or "").splitlines()]
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if a and b and a[0] == head + ":" and b[0] == head + ":" and len(a) == len(b) and not b[1].lstrip("-").isalpha():
            out = {}
            for k, v in zip(a[1:], b[1:]):
                n = _int(v)
                if n is None:
                    return None
                out[k] = n
            return out
    return None


def _snmp(text, netstat=None):
    """Tcp of /proc/net/snmp; ``netstat`` (the text of /proc/net/netstat) adds ``syn_retrans`` — TcpExt TCPSynRetrans,
    the SYN and SYN-ACK resent (part of RetransSegs): a scanner that never answers our SYN-ACK, a probe to a dead exit —
    no loss on the way under load (К2-3, T-KT2)."""
    t = _pairs(text, "Tcp")
    if not t or "RetransSegs" not in t or "OutSegs" not in t:
        return None
    out = {"retrans": t["RetransSegs"], "out_segs": t["OutSegs"]}
    x = _pairs(netstat, "TcpExt") if netstat else None
    if x and "TCPSynRetrans" in x:
        out["syn_retrans"] = x["TCPSynRetrans"]
    return out


def _netstat(text):
    t = _pairs(text, "TcpExt")
    if not t or "ListenOverflows" not in t or "ListenDrops" not in t:
        return None
    return {"overflows": t["ListenOverflows"], "drops": t["ListenDrops"]}


def _conntrack(count_text, max_text):
    c, m = _int((count_text or "").strip()), _int((max_text or "").strip())
    return {"count": c, "max": m} if c is not None and m else None


def _filenr(text):
    f = (text or "").split()
    if len(f) < 3:
        return None
    alloc, mx = _int(f[0]), _int(f[2])
    return {"alloc": alloc, "max": mx} if alloc is not None and mx else None


def _uptime(text):
    f = (text or "").split()
    return _float(f[0]) if f else None


def _route(text4, text6):
    """The interfaces of the default route (IPv4 /proc/net/route and IPv6 /proc/net/ipv6_route); None — neither file
    could be read."""
    if text4 is None and text6 is None:
        return None
    out = []
    for line in (text4 or "").splitlines()[1:]:
        f = line.split()
        if len(f) >= 8 and f[1] == "00000000" and f[7] == "00000000" and f[0] != "lo":
            if f[0] not in out:
                out.append(f[0])
    for line in (text6 or "").splitlines():
        f = line.split()
        if len(f) >= 10 and f[0] == "0" * 32 and f[1] == "00" and f[9] != "lo":
            if f[9] not in out:
                out.append(f[9])
    return out


def _statvfs(path):
    return os.statvfs(path)


def _fs():
    """statvfs of FS_MOUNTS, one record per file system (f_fsid; 0 — the st_dev of the mount point)."""
    out, seen = [], set()
    for mount in FS_MOUNTS:
        p = _p(mount)
        try:
            s = _statvfs(p)
            fsid = getattr(s, "f_fsid", 0) or os.stat(p).st_dev
        except (OSError, ValueError):
            continue
        if fsid in seen:
            continue
        seen.add(fsid)
        fr = s.f_frsize or s.f_bsize
        out.append({"mount": mount, "fsid": fsid, "size": s.f_blocks * fr, "free": s.f_bfree * fr,
                    "avail": s.f_bavail * fr, "files": s.f_files, "ffree": s.f_ffree})
    return out or None


def _events(text):
    """memory.events -> {"oom", "oom_kill"} (None when either is missing)."""
    out = {}
    for line in (text or "").splitlines():
        f = line.split()
        if len(f) == 2 and f[0] in ("oom", "oom_kill"):
            out[f[0]] = _int(f[1])
    return out if out.get("oom") is not None and out.get("oom_kill") is not None else None


def _limits(text):
    """/proc/<pid>/limits -> the soft «Max open files» (None — unlimited or unreadable)."""
    for line in (text or "").splitlines():
        if line.startswith("Max open files"):
            f = line[len("Max open files"):].split()
            return _int(f[0]) if f else None
    return None


# ── the clock: one adjtimex() ──────────────────────────────────────────────

def _adjtimex():
    """(rc, status, offset) of one adjtimex() with modes 0 (only reads), None — no adjtimex (not Linux)."""
    import ctypes
    import ctypes.util

    class _Timex(ctypes.Structure):
        # struct timex of <sys/timex.h> (glibc, Linux): what is read here comes first, the rest is padding
        _fields_ = [("modes", ctypes.c_uint), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
                    ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long), ("status", ctypes.c_int),
                    ("pad", ctypes.c_long * 40)]
    libc = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
    if not hasattr(libc, "adjtimex"):
        return None
    tx = _Timex()
    rc = libc.adjtimex(ctypes.byref(tx))
    if rc < 0:
        return None
    return rc, tx.status, tx.offset


def clock():
    """{"synced": bool, "offset_s": float} of one adjtimex(); None — no adjtimex (not Linux, another libc)."""
    try:
        got = _adjtimex()
    except Exception:                                # noqa: BLE001 — another libc, not Linux
        return None
    if got is None:
        return None
    rc, status, offset = got
    scale = 1e9 if status & STA_NANO else 1e6
    return {"synced": rc != TIME_ERROR and not status & STA_UNSYNC, "offset_s": offset / scale}


# ── facts of the process ───────────────────────────────────────────────────

def virt():
    """The kind of virtualisation, once per process: a container by /proc/1/environ, /run/systemd/container, /proc/vz;
    else "kvm" (any hypervisor: the flag of /proc/cpuinfo) or "none"; None — nothing could be read."""
    if _C.virt_done:
        return _C.virt
    _C.virt_done = True
    env = _text(_p("/proc/1/environ"))
    for part in (env or "").split("\0"):
        if part.startswith("container="):
            _C.virt = _container(part[len("container="):])
            return _C.virt
    run = _text(_p("/run/systemd/container"))
    if run and run.strip():
        _C.virt = _container(run.strip())
        return _C.virt
    if os.path.isdir(_p("/proc/vz")) and not os.path.isdir(_p("/proc/bc")):
        _C.virt = "openvz"
        return _C.virt
    cpu = _text(_p("/proc/cpuinfo"))
    if cpu is None:
        _C.virt = None if env is None else "none"
        return _C.virt
    flags = set()
    for line in cpu.splitlines():
        if line.startswith("flags"):
            flags.update(line.partition(":")[2].split())
            break
    _C.virt = "kvm" if "hypervisor" in flags else "none"
    return _C.virt


def _container(name):
    name = name.strip().lower()
    if name.startswith("lxc"):
        return "lxc"
    return name if name in CONTAINERS else "container-other"


def _cgroup_facts():
    """(version, memory_localevents) once per process."""
    if _C.cgv is not None:
        return _C.cgv, _C.local
    base = _p("/sys/fs/cgroup")
    if os.path.isfile(os.path.join(base, "cgroup.controllers")):
        _C.cgv = 2
        local = False
        for line in (_text(_p("/proc/mounts")) or "").splitlines():
            f = line.split()
            if len(f) >= 4 and f[2] == "cgroup2" and f[1] == "/sys/fs/cgroup":
                local = "memory_localevents" in f[3].split(",")
        _C.local = local
    elif os.path.isdir(base):
        _C.cgv = 1
        _C.local = False
    else:
        _C.cgv = 0                                   # no cgroup file system at all
        _C.local = False
    return _C.cgv, _C.local


# ── the lists of the hour ──────────────────────────────────────────────────

def _list_slices():
    base = _p("/sys/fs/cgroup")
    try:
        names = os.listdir(base)
    except OSError:
        return [], 0
    have = [n for n in names if os.path.isfile(os.path.join(base, n, "memory.events"))]
    first = [n for n in SLICE_FIRST if n in have]
    rest = sorted(n for n in have if n not in SLICE_FIRST)
    allv = first + rest
    return allv[:SLICES_MAX], max(0, len(allv) - SLICES_MAX)


def _list_units():
    base = _p("/sys/fs/cgroup/system.slice")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return {}
    out = {}
    for n in names:
        if not (n.startswith("kaskad-") and n.endswith(".service")):
            continue
        mx = (_text(os.path.join(base, n, "memory.max")) or "").strip()
        if mx and mx != "max" and mx.isdigit():
            out[n] = int(mx)
        if len(out) >= UNITS_MAX:
            break
    return out


def _fd_count(pid):
    try:
        return len(os.listdir(_p("/proc/%d/fd" % pid)))
    except OSError:
        return None


def _list_pids():
    """[[who, pid]]: xray of the gateway (cgroup.procs of its unit) and the amneziawg-go with the most open files."""
    out = []
    for rel in ("/sys/fs/cgroup/system.slice/%s/cgroup.procs" % XRAY_UNIT,
                "/sys/fs/cgroup/systemd/system.slice/%s/cgroup.procs" % XRAY_UNIT):
        pids = [_int(x) for x in (_text(_p(rel)) or "").split()]
        pids = [p for p in pids if p]
        if pids:
            out.append(["xray", pids[0]])
            break
    try:
        names = [n for n in os.listdir(_p("/proc")) if n.isdigit()]
    except OSError:
        names = []
    best = None
    for n in names:
        if (_text(_p("/proc/%s/comm" % n)) or "").strip() != "amneziawg-go":
            continue
        k = _fd_count(int(n))
        if k is not None and (best is None or k > best[0]):
            best = (k, int(n))
    if best:
        out.append(["amneziawg-go", best[1]])
    return out


def _lists(mono, force=False):
    if not force and _C.lists_at is not None and 0 <= mono - _C.lists_at < LIST_EVERY:
        return
    _C.lists_at = mono
    _C.slices, _C.over = _list_slices()
    _C.units = _list_units()
    _C.pids = _list_pids()


# ── the sets ───────────────────────────────────────────────────────────────

def _cg(mono):
    v, local = _cgroup_facts()
    if v != 2:
        return None
    _lists(mono)
    base = _p("/sys/fs/cgroup")
    slices = {}
    for n in _C.slices:
        ev = _events(_text(os.path.join(base, n, "memory.events")))
        if ev is not None:
            slices[n] = ev
    units = {}
    for n, mx in sorted(_C.units.items()):
        ev = _events(_text(os.path.join(base, "system.slice", n, "memory.events")))
        units[n] = dict(ev, max=mx) if ev is not None else None       # gone between two reads: None, not an error
    return {"v": 2, "local": bool(local), "slices": slices, "units": units, "over": _C.over}


def _fd_proc(mono):
    _lists(mono)
    out, gone = [], False
    for who, pid in _C.pids:
        n = _fd_count(pid)
        soft = _limits(_text(_p("/proc/%d/limits" % pid))) if n is not None else None
        if n is None:
            gone = True
            continue
        out.append({"who": who, "open": n, "soft": soft})
    if gone:
        _C.lists_at = None                          # a process restarted: its new PID with the next minute
    return out or None


def _parse_at(at):
    if isinstance(at, (int, float)) and not isinstance(at, bool):
        return float(at)
    if isinstance(at, str) and at:
        try:
            return float(calendar.timegm(time.strptime(at, "%Y-%m-%dT%H:%M:%SZ")))
        except ValueError:
            return None
    return None


def _certs(cfg):
    """The certificates that exist and are used: the site's (a domain is set, the copy covers it) and the IP one
    (``ipcert.store.status()`` valid / expiring / expired). A read of the site that failed — ``{"what": "site",
    "failed": True}`` (REV-ns-a12-2 П-15: the IP one is still read; ``hostmon`` keeps the site's last level); a read of
    the IP one that failed — None for both (REV-ns-a12 П-4: not «no certificate», which would say «не проверяется»
    of an alarm; None keeps the last evaluation)."""
    out = []
    st = (cfg or {}).get("st")
    if st is not None:
        try:
            from ..site import certinfo, model as sm
            s = sm.peek(st)
            d = s["domain"]
            info = sm.copy_info() if d else None
            if info is not None and certinfo.covers(info, d):
                out.append({"what": "site", "name": sm.unicode(d), "days_left": certinfo.days_left(info)})
        except Exception:                            # noqa: BLE001 — a broken copy: not read this hour, not a crash
            out = [{"what": "site", "failed": True}]
    try:
        from ..ipcert import store
        s = store.status()
    except Exception:                                # noqa: BLE001
        return None
    if isinstance(s, dict) and s.get("state") in ("valid", "expiring", "expired"):
        lt = s.get("last_try") if isinstance(s.get("last_try"), dict) else {}
        out.append({"what": "ip", "hours_left": s.get("hours_left"), "state": s.get("state"),
                    "renew_left_h": s.get("renew_left_h"),
                    "try_ok": bool(lt["ok"]) if "ok" in lt else None, "try_at": _parse_at(lt.get("at")),
                    "reason": lt.get("reason")})
    return out


def _ifaces(mono, minute):
    if minute or _C.ifaces_at is None or not 0 <= mono - _C.ifaces_at < ROUTE_EVERY:
        got = _route(_text(_p("/proc/net/route")), _text(_p("/proc/net/ipv6_route")))
        if got is not None or _C.ifaces is None:
            _C.ifaces = got
        _C.ifaces_at = mono
    return list(_C.ifaces) if _C.ifaces is not None else None


def _pressure():
    """({"cpu", "memory", "io"} | None, absent: bool) — an absent /proc/pressure (or EOPNOTSUPP: psi=0) is «no PSI»."""
    out = {}
    for k in ("cpu", "memory", "io"):
        p = _p("/proc/pressure/" + k)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            if e.errno in (errno.ENOENT, errno.EOPNOTSUPP):
                return None, True
            return None, False
        out[k] = _psi(text)
    if any(v is None for v in out.values()):
        return None, False
    return out, False


def read(cfg=None, mono=None, every=()):
    """One round of raw counters (the contract of SPEC §3.1; ``ifaces`` and ``absent`` beside it). ``cfg`` —
    {"st": state.json (the site's certificate)}; ``mono`` — the monotonic time of the round; ``every`` — "minute"
    and/or "hour": the sets due this round."""
    mono = time.monotonic() if mono is None else mono
    every = set([every] if isinstance(every, str) else (every or ()))
    minute, hour = "minute" in every or "hour" in every, "hour" in every
    absent = []
    cpu, cores = _stat(_text(_p("/proc/stat")))
    psi, no_psi = _pressure()
    if no_psi:
        absent.append("psi")
    ctc, ctm = _text(_p("/proc/sys/net/netfilter/nf_conntrack_count")), \
        _text(_p("/proc/sys/net/netfilter/nf_conntrack_max"))
    conntrack = _conntrack(ctc, ctm)
    netstat = _text(_p("/proc/net/netstat"))
    if conntrack is None and not os.path.exists(_p("/proc/sys/net/netfilter/nf_conntrack_max")):
        absent.append("conntrack")
    raw = {"mono": mono, "cpu": cpu, "cores": cores, "load": _loadavg(_text(_p("/proc/loadavg"))),
           "uptime": _uptime(_text(_p("/proc/uptime"))), "mem": _meminfo(_text(_p("/proc/meminfo"))), "psi": psi,
           "oom_kill": _vmstat(_text(_p("/proc/vmstat"))), "net": _netdev(_text(_p("/proc/net/dev"))),
           "softnet": _softnet(_text(_p("/proc/net/softnet_stat"))),
           "tcp": _snmp(_text(_p("/proc/net/snmp")), netstat), "listen": _netstat(netstat), "conntrack": conntrack,
           "fd": _filenr(_text(_p("/proc/sys/fs/file-nr"))), "ifaces": _ifaces(mono, minute),
           "fs": None, "clock": None, "cg": None, "fd_proc": None, "certs": None, "virt": virt(),
           "minute": minute, "hour": hour}
    if minute:
        raw["fs"] = _fs()
        raw["clock"] = clock()
        raw["cg"] = _cg(mono)
        if raw["cg"] is None and _C.cgv != 2:
            absent.append("cg")
        raw["fd_proc"] = _fd_proc(mono)
    if hour:
        raw["certs"] = _certs(cfg)
    if minute:
        raw["probe_mt"] = probe_mtime()
    raw["absent"] = absent
    return raw


def probe_state_path():
    """= gw/probe/run.state_path() (a test compares them): not imported — the round reads only a stat."""
    return os.path.join(paths.gw_var_dir(), "probe-state.json")


def probe_mtime():
    """The mtime of probe-state.json (NS-D24, revised 17.10: the probe writes it on every run) or None."""
    try:
        return os.stat(probe_state_path()).st_mtime
    except OSError:
        return None


# ── the addresses of the node (an anchor of the probes may not be one; REV-ns-a5-sec П-1) ──

OWN_MAX = 256                      # addresses kept
OWN_BYTES = 1024 * 1024            # read of each /proc file at most


def _head(path, limit):
    try:
        with open(path, "r", encoding="ascii", errors="replace") as f:
            return f.read(limit)
    except (OSError, ValueError):
        return None


def _own(trie, inet6):
    """IPv4 — the «/32 host LOCAL» leaves of /proc/net/fib_trie (the local table: every address of every interface),
    IPv6 — /proc/net/if_inet6 (32 hex digits first). -> [address text], no repeats, ≤ OWN_MAX."""
    import ipaddress
    out, leaf = [], None
    for line in (trie or "").splitlines():
        t = line.strip()
        if t.startswith("|-- "):
            leaf = t[4:].strip()
        elif leaf and t.startswith("/32 ") and t.split()[-1] == "LOCAL" and "host" in t.split():
            if leaf not in out:
                out.append(leaf)
            leaf = None
    for line in (inet6 or "").splitlines():
        f = line.split()
        if f and len(f[0]) == 32:
            try:
                a = str(ipaddress.IPv6Address(int(f[0], 16)))
            except ValueError:
                continue
            if a not in out:
                out.append(a)
    return out[:OWN_MAX]


def own_addrs(mono=None):
    """The addresses of the node's interfaces, kept ROUTE_EVERY s (no socket, no command: two /proc files). -> tuple
    of address texts (empty when neither file is read)."""
    mono = time.monotonic() if mono is None else mono
    if _C.own_at is None or not 0 <= mono - _C.own_at < ROUTE_EVERY:
        _C.own = tuple(_own(_head(_p("/proc/net/fib_trie"), OWN_BYTES), _head(_p("/proc/net/if_inet6"), OWN_BYTES)))
        _C.own_at = mono
    return _C.own


# ── the files of the light health (§3.7, §3.9) ─────────────────────────────

HEALTH_FRESH = 180.0               # s: host-health.json of the bot younger than this is taken (§3.8, §3.9 п. 4)


def hostmon_path():
    """The automaton of the rules between runs (NS-D10): written by the collector or the bot (§3.7)."""
    return paths.P("/var/lib/kaskad/hostmon.json")


def health_path():
    """The health of a node without the gateway — the bot writes it once a minute (§3.7, §3.9)."""
    return os.path.join(paths.run_dir(), "host-health.json")


def _bot_bash():
    try:
        from ..bot import config as bc
        return bc.load().impl == "bash"
    except Exception:                                # noqa: BLE001 — no config: not the bot on bash
        return False


def _collector_on(st):
    """The collector works by its own check (``collect.tick``): the gateway on with the engine nft."""
    from . import model
    return bool(model.enabled(st)) and st.get("engine") == "nft"


def instant_health(now=None, st=None, mono=None, lang=None):
    """``host.health`` by a snapshot of its own (§3.9 п. 4, the last source of ``read_health``): ``hostprobe.read`` +
    ``hostmon.instant`` — the rules ``instant``, no probes, no automaton, no event; ``by: "panel"``, ``instant: true``.
    ``health.hint`` — "bot_python" when the bot is on bash and the collector does not work (§3.9 п. 7). ``lang`` — as
    ``hostmon.light`` (False: msgs only — the panel keeps this snapshot for 30 s and says it in each reader's language,
    REV-sec П-6)."""
    from .. import state as kstate
    from . import hostmon, model
    now = time.time() if now is None else now
    st = kstate.load() if st is None else st
    cfg = model.hostmon(st)
    raw = read({"st": st}, mono, ("minute", "hour")) if cfg["enabled"] else {}
    one = hostmon.instant(raw, cfg, {"owner": "panel", "uptime": raw.get("uptime"), "virt": raw.get("virt")}, now)
    lt = hostmon.light(one, lang)
    return {"v": hostmon.V, "at": int(now), "level": lt["level"], "since": None, "instant": True, "by": "panel",
            "cost": None, "hint": "bot_python" if _bot_bash() and not _collector_on(st) else None,
            "top": lt["top"], "info": lt["info"], "checks": lt["checks"], "probes": None}


def read_health(now=None, st=None, mono=None, lang=None, instant=None):
    """-> (health, by) — the health of the node for a reader (§3.9 п. 4): a fresh live.json (``host.health``,
    "collector") → a fresh host-health.json of the bot (≤ HEALTH_FRESH s, "bot") → a snapshot of its own
    (``instant_health``, "panel"; ``instant(now, st, mono)`` — the reader's own way to it, e.g. the panel's cache of
    30 s). The texts — in ``lang`` (None: the reader's) from every source alike (``hostmon.health_in``; REV-ns-a5
    З-1; ``instant`` gives its own). Never raises for a bad file."""
    import json
    from . import hostmon, live
    now = time.time() if now is None else now
    try:
        doc, age = live.read(now)
    except Exception:                                # noqa: BLE001 — a broken live.json: the next source
        doc, age = None, None
    h = ((doc or {}).get("host") or {}).get("health") if isinstance((doc or {}).get("host"), dict) else None
    if isinstance(h, dict) and age is not None and live.fresh(age):
        return hostmon.health_in(h, lang), h.get("by") or "collector"
    try:
        with open(health_path(), "r", encoding="utf-8") as f:
            hd = json.loads(f.read(1024 * 1024))
    except (OSError, ValueError):
        hd = None
    if isinstance(hd, dict) and isinstance(hd.get("health"), dict) and isinstance(hd.get("at"), (int, float)) \
            and not isinstance(hd.get("at"), bool) and 0 <= now - hd["at"] <= HEALTH_FRESH:
        return hostmon.health_in(hd["health"], lang), "bot"
    if instant is not None:
        return instant(now, st, mono), "panel"
    return instant_health(now, st, mono, lang), "panel"
