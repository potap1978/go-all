"""«Только когда свободен» and «под нагрузкой» of the server check (SPEC-node-score-schedule-share NS-S8, §11.1
п. 17, 29–31; SPEC-node-score §6.2 B1; RESEARCH-node-score §2).

One source for every node, with the gateway or without it:

* the traffic of people = the counters of the interfaces of the default routes v4/v6 (``/proc/net/route``,
  ``/proc/net/ipv6_route``, ``/proc/net/dev``; the tunnels ``wg*``/``awg*``/``kawg*``/``tun*`` and ``lo`` never
  count — else the traffic of people twice) MINUS what the transient unit of the check moved itself
  (``IPIngressBytes``/``IPEgressBytes`` of ``IPAccounting=yes``) MINUS what that accounting does not see on the wire:
  ``OWN_SLACK × bytes of the unit + H × packets of the unit``. ``H`` — 14 on Ethernet (``/sys/class/net/<if>/type``
  1, VLAN included), else 0 (tun/wg 65534, PPP 512, venet). ``OWN_SLACK = (H + hdr) / (seg − hdr) + 0.005`` with
  ``seg = min(MTU, 1500)`` (the coordinator's batch of 18.10, З-1: MTU 9000 gives the slack of 1500) and ``hdr`` 52
  (IPv4 + TCP with timestamps) or 72 (IPv6; the family unknown — 72: a larger slack, fewer false «busy»). Negative —
  0. Each direction apart; the larger of rx and tx counts. The counters of the devices of the gateway (``live.json``
  ``devices[].rx/tx``, where not null) are only an extra source: people = max(interfaces, devices). The devices are
  taken apart (``{id: (rx, tx)}``): only a device in both samples counts, by its own difference — a device that came
  back carries the counters of its whole session, a sum of whoever is shown now would jump by them (REV-ns-b1 С-2).
* the CPU of others = user + nice + system of every core (the line ``cpu`` of ``/proc/stat``) minus ``CPUUsageNSec``
  of the unit, over all the time of the cores in the window; idle, iowait, steal, irq, softirq never count.
* free: over the window, people < 2 Mbit/s AND the CPU of others < 20 %. The same threshold before the start and
  during the check (``under_load``). No accounting of the unit (an old systemd, cgroup v1) — ``known: false``: the
  check goes on and is marked ``load_unknown`` (the caller decides).
* the disk (RS §2: «проверка iowait перед стартом» — the one guard of a VPN against the disk test on virtio/NVMe with
  the scheduler ``none``): iowait over the same samples as the share of ONE core, ``io_pct`` = Δiowait × N / Δ(all
  the time of the cores), N — the lines ``cpuN`` of /proc/stat, at most 100 (REV-ns-b1-2 придирка 2: one process
  that waits on the disk gives ≤ 100/N % of all the cores — on 8 vCPU never 20 %; the kernel counts its wait on
  whichever idle core, so the maximum over the cores is not steadier);
  ``disk_busy`` — ``io_pct`` ≥ ``THR_IO`` (20 %: RS §2 gives no number — the coordinator's batch of 19.10, З-9). Only
  the start looks at it (the fio of the check itself is iowait): ``free`` never includes it.
* «под нагрузкой» (``Watch``) — the moving average of 5 min (NS-S8): a decision counts only over a FULL window (the
  sample ≥ 5 min older is its start); a check shorter than 5 min — the one decision over all of it. A sample whose
  ``systemctl show`` failed or gave no counters is left out; «unknown» — only when no sample of the check was good.

    ifaces(route4, route6)            the interfaces of the default routes (names), tunnels and lo dropped
    net_dev(text)                     {iface: (rx bytes, rx packets, tx bytes, tx packets)}
    cpu_stat(text)                    (busy jiffies, all jiffies) of the line «cpu»
    cpu_iowait(text)                  iowait jiffies of the line «cpu»
    cpu_count(text)                   the lines «cpuN» (the cores the jiffies of «cpu» are summed over)
    l2_header(type_text)              H of an interface
    own_slack(h, mtu, family)         OWN_SLACK
    unit_counters(show_text)          the counters of ``systemctl show`` of the unit, None where unknown
    decide(a, b)                      the decision over two samples -> dict (below)
    Meter(unit, runner)               reads one sample (``read()``) of the files and of the unit
    Watch(meter)                      a thread: a sample every 10 s, the moving average of 5 min, the worst seen
"""

import json
import re
import threading
import time

from .. import paths, util

THR_MBPS = 2.0                  # people < 2 Mbit/s over the window (RESEARCH §2)
THR_CPU = 20.0                  # CPU of others < 20 % (the coordinator's decision, §11.1 п. 17, п. 29)
THR_IO = 20.0                   # iowait < 20 % before the start (RS §2 has no number: the coordinator, 19.10, З-9)
WINDOW_S = 300.0                # the window of the decision: the last 5 min
SAMPLE_S = 10.0                 # during the check: a sample every 10 s
USER_HZ = 100                   # the clock of /proc/stat on Linux (USER_HZ, never CONFIG_HZ)
TUNNEL = re.compile(r"^(wg|awg|kawg|tun)")
UINT64_MAX = 2 ** 64 - 1        # systemd shows it for a counter it does not have
_IFNAME = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")
SHOW_PROPS = ("IPIngressBytes", "IPEgressBytes", "IPIngressPackets", "IPEgressPackets", "CPUUsageNSec")

_mono = time.monotonic          # tests replace these
_sleep = time.sleep


# ── pure readers ────────────────────────────────────────────────────────────

def _usable(name):
    return bool(_IFNAME.match(name)) and name != "lo" and not TUNNEL.match(name)


def ifaces(route4="", route6=""):
    """The interfaces of the default routes. /proc/net/route: «Iface Destination Gateway Flags … Mask …», the default
    is Destination 00000000 and Mask 00000000 with RTF_UP; /proc/net/ipv6_route: «dest plen src splen nexthop metric
    refcnt use flags iface», the default is dest 0…0 and plen 00 with RTF_UP (the «unreachable» defaults sit on lo)."""
    out = set()
    for i, line in enumerate((route4 or "").splitlines()):
        f = line.split()
        if i == 0 and f[:1] == ["Iface"]:
            continue
        if len(f) >= 8 and f[1] == "00000000" and f[7] == "00000000":
            try:
                up = int(f[3], 16) & 1
            except ValueError:
                continue
            if up and _usable(f[0]):
                out.add(f[0])
    for line in (route6 or "").splitlines():
        f = line.split()
        if len(f) >= 10 and f[0] == "0" * 32 and f[1] == "00":
            try:
                up = int(f[8], 16) & 1
            except ValueError:
                continue
            if up and _usable(f[9]):
                out.add(f[9])
    return sorted(out)


def net_dev(text):
    out = {}
    for line in (text or "").splitlines():
        name, sep, rest = line.partition(":")
        name = name.strip()
        if not sep or not _IFNAME.match(name):
            continue
        f = rest.split()
        if len(f) < 10:
            continue
        try:
            out[name] = (int(f[0]), int(f[1]), int(f[8]), int(f[9]))
        except ValueError:
            continue
    return out


def cpu_stat(text):
    """(user + nice + system, user + nice + system + idle + iowait + irq + softirq + steal) of the line «cpu»; guest is
    inside user already. None when the line is not there."""
    for line in (text or "").splitlines():
        f = line.split()
        if f and f[0] == "cpu" and len(f) >= 9:
            try:
                v = [int(x) for x in f[1:9]]
            except ValueError:
                return None
            return v[0] + v[1] + v[2], sum(v)
    return None


def cpu_iowait(text):
    """The iowait jiffies (the 5th number) of the line «cpu»; None when the line is not there."""
    for line in (text or "").splitlines():
        f = line.split()
        if f and f[0] == "cpu" and len(f) >= 9:
            try:
                return int(f[5])
            except ValueError:
                return None
    return None


_CPUN = re.compile(r"^cpu[0-9]+\s", re.M)


def cpu_count(text):
    """The number of the lines «cpuN» of /proc/stat (the line «cpu» sums them), None when there is none."""
    return len(_CPUN.findall(text or "")) or None


def l2_header(type_text):
    try:
        return 14 if int(str(type_text).strip()) == 1 else 0
    except ValueError:
        return 0


def own_slack(h, mtu, family=None):
    try:
        mtu = int(mtu)
    except (TypeError, ValueError):
        mtu = 1500
    seg = min(mtu if mtu > 0 else 1500, 1500)
    hdr = 52 if family == 4 else 72
    if seg <= hdr:
        seg = 1500
    return (h + hdr) / float(seg - hdr) + 0.005


def unit_counters(show_text):
    """``systemctl show <unit> -p IPIngressBytes …`` -> {"rx", "tx", "rxp", "txp", "cpu_ns"}: a value systemd does not
    have («[no data]» of the IP counters without ``IPAccounting``, «[not set]» of ``CPUUsageNSec`` of a unit that is
    gone — systemd 249–257 of Debian 12/13 and Ubuntu 22.04/24.04; UINT64_MAX, empty) is None."""
    keys = {"IPIngressBytes": "rx", "IPEgressBytes": "tx", "IPIngressPackets": "rxp", "IPEgressPackets": "txp",
            "CPUUsageNSec": "cpu_ns"}
    out = dict.fromkeys(keys.values())
    for line in (show_text or "").splitlines():
        k, sep, v = line.partition("=")
        if sep and k in keys:
            try:
                n = int(v.strip())
            except ValueError:
                continue
            if 0 <= n < UINT64_MAX:
                out[keys[k]] = n
    return out


def _d(a, b):
    """b − a, never below 0 (a counter that went back: the interface came again)."""
    return max(0, b - a) if a is not None and b is not None else None


def decide(a, b):
    """The decision over the samples ``a`` (older) and ``b`` (newer) of ``Meter.read``:
    {"free", "people_mbps", "rx_mbps", "tx_mbps", "cpu_pct", "io_pct", "disk_busy", "known", "span_s"}; ``known`` —
    the counters of the unit were there (without them, before the start, there is nothing of ours to take off: the
    unit does not run yet). ``disk_busy`` is never a part of ``free`` (the start alone looks at it)."""
    dt = max(1e-3, b["t"] - a["t"])
    ua, ub = a.get("unit"), b.get("unit")
    known = True
    if ua is None or ub is None:
        own = dict.fromkeys(("rx", "tx", "rxp", "txp", "cpu_ns"), 0)
        known = ua is None and ub is None and not b.get("in_unit")
    else:
        own = {k: _d(ua.get(k), ub.get(k)) for k in ("rx", "tx", "rxp", "txp", "cpu_ns")}
        if any(v is None for v in own.values()):
            known = False
            own = {k: (v or 0) for k, v in own.items()}
    h = max([lk["h"] for lk in b["links"].values()] or [0])
    slack = max([own_slack(lk["h"], lk["mtu"], b.get("family")) for lk in b["links"].values()] or [own_slack(0, 1500)])
    rx = tx = 0
    for name in b["links"]:
        if name in a["ifs"] and name in b["ifs"]:
            x, y = a["ifs"][name], b["ifs"][name]
            rx += _d(x[0], y[0])
            tx += _d(x[2], y[2])
    rx = max(0.0, rx - own["rx"] * (1 + slack) - h * own["rxp"])
    tx = max(0.0, tx - own["tx"] * (1 + slack) - h * own["txp"])
    da, db = a.get("dev"), b.get("dev")
    if isinstance(da, dict) and isinstance(db, dict):
        drx = dtx = 0
        for k in set(da) & set(db):         # each device by its own difference; one that came or went never counts
            drx += _d(da[k][0], db[k][0]) or 0
            dtx += _d(da[k][1], db[k][1]) or 0
        rx, tx = max(rx, drx), max(tx, dtx)
    rx_mbps, tx_mbps = rx * 8 / dt / 1e6, tx * 8 / dt / 1e6
    cpu = io = None
    if a.get("cpu") and b.get("cpu"):
        busy, total = b["cpu"][0] - a["cpu"][0], b["cpu"][1] - a["cpu"][1]
        if total > 0:
            mine = own["cpu_ns"] / 1e9 * USER_HZ
            cpu = 100.0 * max(0.0, busy - mine) / total
            if a.get("io") is not None and b.get("io") is not None:
                io = min(100.0, 100.0 * max(0, b["io"] - a["io"]) * (b.get("ncpu") or 1) / total)
    people = max(rx_mbps, tx_mbps)
    free = people < THR_MBPS and (cpu is None or cpu < THR_CPU)
    return {"free": free, "people_mbps": round(people, 3), "rx_mbps": round(rx_mbps, 3), "tx_mbps": round(tx_mbps, 3),
            "cpu_pct": None if cpu is None else round(cpu, 1), "io_pct": None if io is None else round(io, 1),
            "disk_busy": io is not None and io >= THR_IO, "known": known, "span_s": round(dt, 1)}


# ── the samples ─────────────────────────────────────────────────────────────

def _read(path):
    return util.read_text(paths.P(path)) or ""


class Meter(object):
    """One sample: ``read()`` -> {"t", "ifs", "links": {iface: {"h", "mtu"}}, "cpu", "io", "ncpu", "unit", "dev",
    "family", "in_unit"}. ``unit`` — the name of the transient unit of the check (the counters are taken off), None
    before it runs; ``runner`` — for ``systemctl show``; ``family`` — 4 | 6 | None, of the connection of the channel test (B3)."""

    def __init__(self, unit=None, runner=None, family=None):
        self.unit, self.runner, self.family = unit, runner, family

    def _links(self):
        out = {}
        for name in ifaces(_read("/proc/net/route"), _read("/proc/net/ipv6_route")):
            out[name] = {"h": l2_header(_read("/sys/class/net/%s/type" % name)),
                         "mtu": (_read("/sys/class/net/%s/mtu" % name).strip() or "1500")}
        return out

    def _unit(self):
        if not (self.unit and self.runner):
            return None
        argv = ["systemctl", "show", self.unit] + ["-p%s" % p for p in SHOW_PROPS]
        res = self.runner.run(argv, timeout=10)
        return unit_counters(res.out) if res.ok else None

    @staticmethod
    def _devices():
        """{id: (rx, tx)} of the devices of live.json (only those with counters); no file or none — None."""
        try:
            doc = json.loads(util.read_text(paths.live_json()) or "null")
        except ValueError:
            return None
        devs = doc.get("devices") if isinstance(doc, dict) else None
        if not isinstance(devs, dict):
            return None
        out = {}
        for k, d in devs.items():
            if isinstance(d, dict) and type(d.get("rx")) is int and type(d.get("tx")) is int:
                out[str(k)] = (d["rx"], d["tx"])
        return out or None

    def read(self):
        stat = _read("/proc/stat")
        return {"t": _mono(), "ifs": net_dev(_read("/proc/net/dev")), "links": self._links(),
                "cpu": cpu_stat(stat), "io": cpu_iowait(stat), "ncpu": cpu_count(stat), "unit": self._unit(),
                "dev": self._devices(),
                "family": self.family, "in_unit": bool(self.unit)}


def check(meter, seconds, stop=None, out=None):
    """«Свободен ли сервер» over ``seconds`` (the pre-check of a manual run): two samples ``seconds`` apart; ``out`` —
    a list that gets both (the last one starts the window of ``Watch``)."""
    a = meter.read()
    _sleep(seconds)
    if stop is not None and stop.is_set():
        return None
    b = meter.read()
    if out is not None:
        out.extend((a, b))
    return decide(a, b)


class Watch(object):
    """«Под нагрузкой» during the check: a sample every ``SAMPLE_S``; the moving average of ``WINDOW_S`` (NS-S8) —
    a decision over the newest sample at least ``WINDOW_S`` older (a full window) counts; while the check is shorter,
    only the last decision, over all of it, does (``result``). A sample without the counters of the unit (``systemctl
    show`` failed, timed out — likely just when the node is busy — or no accounting at all) is left out of the window
    (REV-ns-b1 З-1). ``result()`` -> {"under_load": True | False | None, "people_max", "cpu_max", "known", "n"}; None —
    no good sample (``load_unknown``)."""

    def __init__(self, meter, sample_s=SAMPLE_S, window_s=WINDOW_S, seed=()):
        self.meter, self.sample_s, self.window_s = meter, sample_s, window_s
        self.samples = []               # the good ones of the window (+ the start of a full window)
        self.decisions = []             # (full window?, decision)
        self.bad = []                   # the first and the last sample without counters (only for the numbers)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        for s in seed:                  # the last sample of the pre-check: the window starts there
            self._add(s)

    @staticmethod
    def _good(s):
        if not s.get("in_unit"):
            return True
        u = s.get("unit")
        return isinstance(u, dict) and all(u.get(k) is not None for k in ("rx", "tx", "rxp", "txp", "cpu_ns"))

    def tick(self):
        self._add(self.meter.read())

    def _add(self, s):
        with self._lock:
            if not self._good(s):
                self.bad = (self.bad[:1] + [s])[:2]
                return
            self.samples.append(s)
            # keep the newest sample that is at least a window old: the start of the moving average
            while len(self.samples) >= 2 and s["t"] - self.samples[1]["t"] >= self.window_s - 1e-6:
                self.samples.pop(0)
            if len(self.samples) >= 2:
                first = self.samples[0]
                self.decisions.append((s["t"] - first["t"] >= self.window_s - 1e-6, decide(first, s)))

    def _loop(self):
        while not self._stop.wait(self.sample_s):
            try:
                self.tick()
            except Exception:                   # noqa: BLE001 — a sample that failed never stops the check
                pass

    def start(self):
        self.tick()
        self._thread = threading.Thread(target=self._loop, name="kaskad-bench-watch", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
        try:
            self.tick()                         # the last bit of the check
        except Exception:                       # noqa: BLE001
            pass

    def last_unit(self):
        """The counters of the unit of the last GOOD sample (rx, tx, cpu of the check)."""
        with self._lock:
            return self.samples[-1].get("unit") if self.samples and self.samples[-1].get("in_unit") else None

    def counted(self):
        """The decisions that count: every full window; none full — the last one (over the whole check)."""
        with self._lock:
            ds = list(self.decisions)
        full = [d for f, d in ds if f]
        return full or [d for _, d in ds[-1:]]

    def result(self):
        ds = self.counted()
        if not ds:
            with self._lock:
                bad = list(self.bad)
            d = decide(bad[0], bad[-1]) if len(bad) >= 2 else None     # the numbers only: the load is unknown
            return {"under_load": None, "people_max": d["people_mbps"] if d else None,
                    "cpu_max": d["cpu_pct"] if d else None, "known": False, "n": 0}
        known = all(d["known"] for d in ds)
        under = any(not d["free"] for d in ds)
        cpus = [d["cpu_pct"] for d in ds if d["cpu_pct"] is not None]
        return {"under_load": under if known else None, "people_max": max(d["people_mbps"] for d in ds),
                "cpu_max": max(cpus) if cpus else None, "known": known, "n": len(ds)}
