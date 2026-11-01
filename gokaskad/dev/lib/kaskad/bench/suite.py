"""The tests 0–7 of the server check (SPEC-node-score §6.2 B2; RESEARCH-node-score §2, the table, rows 0–7; правка 18.10
П8). They run inside the transient unit of the check (``bench.run``: idle classes, ``MemoryMax=256M``), one after
another, the heavy ones last; every step looks at the time left before it starts and at the stop.

    0  inv     the inventory: CPU model and flags (aes, avx2, vaes), nproc, threads per core, MemTotal (-> mem_gb),
               disks (lsblk: rotational, size), the speed of the ports of the default routes (a hint only),
               systemd-detect-virt, the kernel, the OS, the engines of the node, the version of OpenSSL
               (``openssl_ver``: its change is a new base of the ciphers, like a new CPU), the memory kdump keeps
               (``crash_mb``, /sys/kernel/kexec_crash_size: the relative score adds it back to MemTotal, C2 / RS 3.4)
               -> ``bench_run.inv`` (NS-S17)
    3A cpu_a   sysbench cpu --cpu-max-prime=10000 --threads=1 --time=2 × 5 -> cpu_1t = the median
    1  now     the «now» series of the light probes (3 anchors × 21 echo packets; only when the probes are on) ->
               now_loss, now_rtt + ``series`` — the history only, never the score (the coordinator 19.10: the score
               takes the losses and the reply time of the light probes of 72 h, NS-S10)
    2  dns     10 random names under ``DNS_ZONE`` through what people use (RS §2 «Kaskad DNS»; the coordinator, 19.10,
               З-8): the gateway serving people — on, an inbound enabled, its address (kgw0) up — its dnsmasq
               (``gw.addr.dns_ip``:``gw.dns.PORT``), else the first nameserver of /etc/resolv.conf that is not
               link-local (circle 1, K-it1-calib п. 1: a gateway without inbounds has nobody on its DNS — it failed
               every check) -> the median, ms; the row says which (``dns_src``: kaskad | system). An answer is NOERROR
               or NXDOMAIN only: REFUSED, SERVFAIL … — the query failed (REV-ns-b1-2 З-1)
    4  cpu_mt  the same with --threads=nproc, 10 s (quick: 5 s)
    5  crypto  openssl speed -aead -evp chacha20-poly1305 | aes-128-gcm -bytes 1420 -seconds 1 -mr × 3 -> the median,
               MB/s (``-aead``: the cipher as TLS runs it; without it AES depends on the version of OpenSSL — the
               coordinator, 19.10, п. 2)
    6  mem     sysbench memory --memory-block-size=1M --memory-total-size=1T --threads=1 --time=5 -> MiB/s (by the
               time: 20G went in 0,7 s — the coordinator, 19.10, п. 3)
    7  disk    fio on the file ``<work>/fio.tmp`` of 1 GiB (never a device): randread 4k qd32 10 s time_based,
               randwrite 4k qd32 io_size=1G runtime=10, write 4k fsync=1 16M runtime=5 (1000 / IOPS: the write
               with its fsync — the coordinator, 19.10, п. 1); full: read
               1M 5 s. Only with ≥ 3 GiB free, < 85 % used, not tmpfs/overlay/ZFS; the file goes away after.
       net     the channel 8/8b/8c — the portable block ``bench.channel`` (``steps(mode, skip, iperf)``); its Env —
               ``channel_env`` here (the country of the node, the allowed upload point, the load, L of NS-S16)
    3B cpu_b   the series B (× 5 at the end) -> cpu_cv (all 10), cpu_sustain = median B / median A

A row: {"test", "ts", "value", "unit", "ok", "why"}; not measured -> value None, ok 0 and ``why`` — the code of the
reason (``bench_run.why.<code>`` of the catalog): no_pkgs, no_openssl, time, tool_failed, probes_off, no_answer,
no_resolver, dns_failed, disk_space, disk_full, disk_fs, partial_series. Every command is a fixed argv of this module
(numbers and the path of the work directory only; nothing of a person); a tool that fails or prints what is not
understood is a row with its reason, never the end of the check; a step that raises (a bug, an OSError) — its tests
not yet written are «не измерено» (``tool_failed``, the type in the journal) and the next step goes (REV-ns-b1 З-2).
"""

import errno
import ipaddress
import json
import os
import re
import secrets
import socket
import struct
import time

from .. import paths, util
from .. import score as S
from . import parse

DNS_ZONE = "example.com"            # random names under it: never cached; NXDOMAIN from the servers of the zone
DNS_N = 10
DNS_TIMEOUT = 2.0                   # s; an unanswered name counts as this in the median
DNS_MIN_ANSWERS = 5
SERIES_N = 7                        # series of the «now» probes: 3 packets per anchor each -> 21 per anchor
CPU_RUNS = 5
CPU_SECS = 2
OPENSSL_RUNS = 3
MEM_SECS = 5
BLOCK = 1420                        # the packet of a tunnel
FIO_FILE = "fio.tmp"
FIO_FREE_MIN = 3 * 1024 ** 3        # free on the file system of the work directory
FIO_USED_MAX = 85.0                 # % used
BAD_FS = ("tmpfs", "ramfs", "overlay", "zfs", "aufs", "squashfs", "devtmpfs")
UNITS = {"mem_gb": "GB", "cpu_1t": "ev/s", "cpu_mt": "ev/s", "cpu_cv": "%", "cpu_sustain": "x", "now_loss": "%",
         "now_rtt": "ms", "dns": "ms", "chacha_1t": "MB/s", "aes_1t": "MB/s", "mem_bw": "MiB/s", "disk_rr": "IOPS",
         "disk_rw": "IOPS", "disk_fsync": "ms", "disk_seq": "MB/s"}

_now = time.time                    # tests replace these
_mono = time.monotonic


class Cancelled(BaseException):
    """The check was stopped (``systemctl stop`` of its unit: SIGTERM; the end of ``RuntimeMaxSec``). A BaseException:
    the broad ``except Exception`` of the steps and of the modules they call never swallow the stop."""


class Ctx(object):
    """What the steps share. ``deadline`` — monotonic seconds; ``st`` — the state (read once at the start);
    ``progress(step, i, n)`` — for ``bench.run.status``; ``have`` — which tools are on the PATH."""

    def __init__(self, runner, mode, work, deadline, st=None, progress=None, meter=None, opts=None):
        self.runner, self.mode, self.work, self.deadline = runner, mode, work, deadline
        self.st = st or {}
        self.progress = progress or (lambda step, i, n: None)
        self.stage = None               # (k, m): the step k of m of run_all — progress.json says it (T-3377 Н-8)
        self.meter = meter              # bench.quiet.Meter of the check: the load during the channel (NS-S16)
        self.opts = dict(opts or {})    # {"skip": [...], "iperf": bool, "manual": bool} of the channel
        self.have = {t: bool(runner.which(t)) for t in ("sysbench", "fio", "openssl", "iperf3")}
        self.rows = []
        self.inv = {}
        self.series = None
        self.cpu_a, self.cpu_b = [], []
        self.files = []                 # what a step created and the end removes (the fio file)

    def left(self):
        return self.deadline - _mono()

    def row(self, test, value, why=None, dns_src=None, unit=None, **more):
        """``more`` — the fields of the channel (``extra``, ``people``, ``value_corr``, ``ca``), kept when not None."""
        ok = value is not None and why is None
        r = {"test": test, "ts": int(_now()), "value": (round(float(value), 4) if ok else None),
             "unit": unit or UNITS.get(test), "ok": 1 if ok else 0, "why": None if ok else (why or "tool_failed")}
        if dns_src:
            r["dns_src"] = dns_src
        for k in ("extra", "people", "value_corr", "ca"):
            if more.get(k) is not None:
                r[k] = more[k]
        self.rows.append(r)

    def channel_env(self, skip=(), iperf=False):
        """The ``bench.channel.Env`` of this node (the glue of the portable block: the state, the probes, the
        counters of the load, the history of the score)."""
        return channel_env(self, skip, iperf)

    def na(self, tests, why):
        for t in tests:
            self.row(t, None, why)

    def run(self, argv, timeout):
        return self.runner.run(argv, timeout=timeout)


def _read(path):
    return util.read_text(paths.P(path)) or ""


# ── 0: the inventory ────────────────────────────────────────────────────────

def _nproc():
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def _engines(st):
    from ..gw import model
    fam = {"awg1": "awg", "awg2": "awg", "awg3": "awg"}
    try:
        return sorted(set(fam.get(k, k) for k in model.enabled_kinds(st)))
    except Exception:                       # noqa: BLE001 — a broken state never stops the inventory
        return None


# the limits of the queue (bench.spool.INV_FIELDS, the contract with B12, SS §3.2): one field out of them makes the
# whole file of the check bad — the inventory keeps to them itself (REV-ns-b1-3 З-1)
INV_DISKS = 64
INV_NICS = 32
INV_SMT_MAX = 64
INV_FLAGS = 512
INV_MAX = 4096
# systemd-detect-virt --container names: their kernel (and /sys/kernel/kexec_crash_size) is the hoster's node
CONTAINER_VIRT = S.CONTAINER_VIRT


def _text(s, most):
    """A line of the inventory within the contract: printable only (no soft hyphen, no control character), at most
    ``most`` characters; empty — None."""
    if not isinstance(s, str):
        return None
    s = "".join(c for c in s if c.isprintable()).strip()[:most].strip()
    return s or None


def inventory(ctx):
    from . import quiet
    ci = parse.cpuinfo(_read("/proc/cpuinfo"))
    ls = parse.lscpu(ctx.run(["lscpu"], 15).out)
    kb = parse.meminfo_kb(_read("/proc/meminfo"))
    disks = parse.lsblk(ctx.run(["lsblk", "-d", "-b", "-n", "-o", "NAME,ROTA,SIZE,TYPE"], 15).out)
    virt = (ctx.run(["systemd-detect-virt"], 15).out or "").strip()
    nic = {}
    for name in quiet.ifaces(_read("/proc/net/route"), _read("/proc/net/ipv6_route")):
        try:
            v = int(_read("/sys/class/net/%s/speed" % name).strip())
        except ValueError:
            v = None
        nic[name] = v if v and v > 0 else None      # a hint only: vmxnet3 says 10G, virtio nothing (RS З-4)
    osr = parse.os_release(_read("/etc/os-release"))
    ossl = parse.openssl_version(ctx.run(["openssl", "version"], 15).out) if ctx.have["openssl"] else None
    mem_gb = round(kb * 1024 / 1e9, 3) if kb else None
    smt = ls["threads_per_core"]
    try:
        crash = int(_read("/sys/kernel/kexec_crash_size").strip())
    except ValueError:
        crash = None
    if not virt or virt in CONTAINER_VIRT:
        crash = None        # a container sees the kdump reserve of the hoster's node, not its own (REV-ns-c2 З-4)
    ctx.inv = {"cpu": _text(ci["model"] or ls["model"], 128), "flags": ci["flags"], "nproc": _nproc(),
               "smt": smt if type(smt) is int and 1 <= smt <= INV_SMT_MAX else None, "mem_gb": mem_gb,
               "disks": [{"rota": d["rota"], "size": d["size"]} for d in disks][:INV_DISKS],
               "nic": dict((k, nic[k]) for k in sorted(nic)[:INV_NICS]),
               "virt": virt if virt and len(virt) <= 32 and virt.replace("-", "").isalnum() else None,
               "kernel": _text(os.uname().release, 64), "os": {"id": _text(osr["id"], 64),
                                                               "version_id": _text(osr["version_id"], 64)},
               "engines": _engines(ctx.st), "openssl_ver": _text(ossl, 64),
               "crash_mb": int(round(crash / 1048576.0)) if crash is not None and 0 <= crash < 2 ** 40 else None}
    _fit_inv(ctx.inv)
    ctx.row("mem_gb", mem_gb, None if mem_gb else "tool_failed")


def _inv_len(inv):
    # the measure of bench.spool.inv: compact JSON of the fields that are not null, characters
    return len(json.dumps(dict((k, v) for k, v in inv.items() if v is not None), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")))


def _fit_inv(inv):
    """The whole inventory within ``INV_MAX`` (bench.spool.MAX_INV, REV-ns-b3 П-3): the flags within their limit, then
    the last disks, the last interfaces and the last flags go until it fits — never a bad file of the check."""
    if isinstance(inv.get("flags"), list):
        inv["flags"] = inv["flags"][:INV_FLAGS]
    for k in ("disks", "nic", "flags"):
        while _inv_len(inv) > INV_MAX and inv.get(k):
            if isinstance(inv[k], dict):
                inv[k].pop(sorted(inv[k])[-1])
            else:
                inv[k].pop()


# ── 3, 4: the processor ─────────────────────────────────────────────────────

def _sysbench_cpu(ctx, threads, secs):
    res = ctx.run(["sysbench", "cpu", "--cpu-max-prime=10000", "--threads=%d" % threads, "--time=%d" % secs, "run"],
                  secs + 20)
    return parse.sysbench_cpu(res.out) if res.ok else None


def _series(ctx, label):
    out = []
    for i in range(CPU_RUNS):
        ctx.progress(label, i + 1, CPU_RUNS)
        v = _sysbench_cpu(ctx, 1, CPU_SECS)
        if v is not None:
            out.append(v)
    return out


def cpu_a(ctx):
    if not ctx.have["sysbench"]:
        return ctx.na(("cpu_1t",), "no_pkgs")
    ctx.cpu_a = _series(ctx, "cpu_a")
    ctx.row("cpu_1t", parse.median(ctx.cpu_a))


def cpu_mt(ctx):
    if not ctx.have["sysbench"]:
        return ctx.na(("cpu_mt",), "no_pkgs")
    secs = 10 if ctx.mode == "full" else 5
    ctx.row("cpu_mt", _sysbench_cpu(ctx, max(1, int(ctx.inv.get("nproc") or _nproc())), secs))


def cpu_b(ctx):
    if not ctx.have["sysbench"]:
        return ctx.na(("cpu_cv", "cpu_sustain"), "no_pkgs")
    ctx.cpu_b = _series(ctx, "cpu_b")
    a, b = parse.median(ctx.cpu_a), parse.median(ctx.cpu_b)
    ctx.row("cpu_cv", parse.cv_pct(ctx.cpu_a + ctx.cpu_b),
            None if len(ctx.cpu_a) >= 3 and len(ctx.cpu_b) >= 3 else "partial_series")
    ctx.row("cpu_sustain", (b / a) if a and b else None, None if a and b else "partial_series")


# ── 1: the «now» series of the light probes ─────────────────────────────────

def _net():
    from ..gw import hostping

    class OwnLock(hostping.Net):
        """The check holds the speed test's lock itself: its own series must not be cut by it."""

        def speed_busy(self, fresh_s):
            return False
    return OwnLock()


def _best2(losses):
    """The loss of the node = what ≥ 2 of 3 anchors see: the worst anchor dropped, the mean of the other two
    (2 anchors — the better, 1 — that one; RESEARCH §3.1)."""
    xs = sorted(losses)
    if len(xs) >= 3:
        return (xs[0] + xs[1]) / 2.0
    return xs[0] if xs else None


def now_series(ctx, prober=None):
    from ..gw import hostping, model
    hm = model.hostmon(ctx.st)
    if not hostping.wanted(hm):
        return ctx.na(("now_loss", "now_rtt"), "probes_off")
    prober = prober or hostping.Prober(hm["anchors"], own=model.own_addrs(ctx.st), net=_net())
    agg = {}
    for i in range(SERIES_N):
        ctx.progress("now", i + 1, SERIES_N)
        r = prober.series()
        if r.get("excl"):
            continue
        for a in r.get("anchors") or ():
            x = agg.setdefault(a["addr"], {"addr": a["addr"], "n": 0, "lost": 0, "rtt": [], "na": None})
            if a.get("na"):
                x["na"] = a["na"]
                continue
            x["n"] += a["n"]
            x["lost"] += a["lost"]
            x["rtt"].extend(a["rtt"])
    anchors = [x for x in agg.values() if x["n"] > 0]
    ctx.series = {"anchors": [{"addr": x["addr"], "n": x["n"], "lost": x["lost"], "rtt_med": parse.median(x["rtt"]),
                               "na": x["na"]} for x in sorted(agg.values(), key=lambda y: y["addr"])]}
    if not anchors:
        return ctx.na(("now_loss", "now_rtt"), "no_answer")
    ctx.row("now_loss", _best2([100.0 * x["lost"] / x["n"] for x in anchors]))
    rtts = [v for x in anchors for v in x["rtt"]]
    ctx.row("now_rtt", parse.median(rtts), None if rtts else "no_answer")


# ── 2: DNS ──────────────────────────────────────────────────────────────────

def system_resolver():
    """The first ``nameserver`` of /etc/resolv.conf that is an address and not link-local (systemd-resolved:
    127.0.0.53; ``fe80::1%eth0`` needs its zone, which a plain address loses — the next line goes) or None."""
    for line in _read("/etc/resolv.conf").splitlines():
        f = line.split()
        if len(f) >= 2 and f[0] == "nameserver":
            try:
                ip = ipaddress.ip_address(f[1])
            except ValueError:
                continue
            if not ip.is_link_local:
                return str(ip)
    return None


def addr_up(ip):
    """``ip`` is an address of the node now (the gateway's kgw0 carries it): a UDP socket binds to it — no packet, no
    command, no right. EADDRNOTAVAIL (not up) or any other error — False. (``net.ipv4.ip_nonlocal_bind`` = 1 binds any
    address: then it says True and the DNS test asks the gateway as before this check.)"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return False
    try:
        s.bind((ip, 0))
        return True
    except OSError:
        return False
    finally:
        s.close()


def resolver(st=None):
    """What the DNS test asks -> (address, port, dns_src) or None: the gateway serving people — on, at least one
    inbound enabled, its address up on the node — its DNS (dnsmasq of ``kaskad-dns.service``, the one the devices use:
    their 10.77.0.1:53 is DNATed to its port; a query of the node itself is not, so the port is asked directly),
    «kaskad»; else the system resolver, «system». A gateway without inbounds (or with kgw0 down) has nobody on its DNS:
    asking it measured nothing but «connection refused» / REFUSED (circle 1, K-it1-calib п. 1); with inbounds a silent
    gateway DNS stays «dns_failed» of «kaskad» — that is what people get."""
    try:
        from ..gw import addr, dns as gdns, model
        st = st or {}
        if model.enabled(st) and model.enabled_kinds(st):
            ip = addr.dns_ip(st)
            if addr_up(ip):
                return ip, gdns.PORT, "kaskad"
    except Exception:                       # noqa: BLE001 — a broken state: the system resolver
        pass
    a = system_resolver()
    return (a, 53, "system") if a else None


def dns_query(qid, name):
    q = b"".join(struct.pack("!B", len(p)) + p.encode("ascii") for p in name.split(".")) + b"\x00"
    return struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0) + q + struct.pack("!HH", 1, 1)


RCODE_OK = (0, 3)                   # NOERROR, NXDOMAIN (random names: NXDOMAIN is the answer)


def dns_once(addr, name, timeout=DNS_TIMEOUT, port=53):
    """ms until the answer of ``addr``:``port`` to an A query of ``name``, None — none. An answer is a reply to OUR
    query (the id, the bit QR) with the rcode NOERROR or NXDOMAIN; REFUSED, SERVFAIL and the rest — the query failed
    (a dnsmasq without upstream servers refuses at once: the best time where people have no DNS — REV-ns-b1-2 З-1)."""
    fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
    qid = secrets.randbits(16)
    s = socket.socket(fam, socket.SOCK_DGRAM)
    try:
        s.settimeout(timeout)
        s.connect((addr, port))
        t0 = time.perf_counter()
        s.send(dns_query(qid, name))
        while True:
            left = timeout - (time.perf_counter() - t0)
            if left <= 0:
                return None
            s.settimeout(left)
            try:
                buf = s.recv(4096)
            except socket.timeout:
                return None
            if len(buf) >= 12 and struct.unpack("!H", buf[:2])[0] == qid and buf[2] & 0x80:
                ms = (time.perf_counter() - t0) * 1000.0
                return ms if buf[3] & 0x0F in RCODE_OK else None
    except OSError:
        return None
    finally:
        s.close()


def dns(ctx):
    got = resolver(ctx.st)
    if got is None:
        return ctx.na(("dns",), "no_resolver")
    addr, port, dns_src = got
    times = []
    for i in range(DNS_N):
        ctx.progress("dns", i + 1, DNS_N)
        times.append(dns_once(addr, "%s.%s" % (secrets.token_hex(6), DNS_ZONE), port=port))
    answered = [t for t in times if t is not None]
    if len(answered) < DNS_MIN_ANSWERS:
        return ctx.row("dns", None, "dns_failed", dns_src=dns_src)
    ctx.row("dns", parse.median([t if t is not None else DNS_TIMEOUT * 1000.0 for t in times]), dns_src=dns_src)


# ── 5: the ciphers ──────────────────────────────────────────────────────────

def crypto(ctx):
    if not ctx.have["openssl"]:
        return ctx.na(("chacha_1t", "aes_1t"), "no_openssl")
    for alg, test in (("chacha20-poly1305", "chacha_1t"), ("aes-128-gcm", "aes_1t")):
        vals = []
        for i in range(OPENSSL_RUNS):
            ctx.progress(test, i + 1, OPENSSL_RUNS)
            res = ctx.run(["openssl", "speed", "-aead", "-evp", alg, "-bytes", str(BLOCK), "-seconds", "1", "-mr"], 30)
            v = parse.openssl_speed(res.out, BLOCK) if res.ok else None
            if v is not None:
                vals.append(v / 1e6)
        ctx.row(test, parse.median(vals))


# ── 6: memory ───────────────────────────────────────────────────────────────

def memory(ctx):
    if not ctx.have["sysbench"]:
        return ctx.na(("mem_bw",), "no_pkgs")
    res = ctx.run(["sysbench", "memory", "--memory-block-size=1M", "--memory-total-size=1T", "--threads=1",
                   "--time=%d" % MEM_SECS, "run"], MEM_SECS + 20)
    ctx.row("mem_bw", parse.sysbench_memory(res.out) if res.ok else None)


# ── 7: the disk ─────────────────────────────────────────────────────────────

def fs_type(path, mountinfo=None):
    """The type of the file system that holds ``path`` (the longest mount point of /proc/self/mountinfo)."""
    text = mountinfo if mountinfo is not None else (util.read_text("/proc/self/mountinfo") or "")
    best, typ = "", None
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        f, g = left.split(), right.split()
        if not sep or len(f) < 5 or not g:
            continue
        mp = f[4].replace("\\040", " ")
        if (path == mp or path.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(best):
            best, typ = mp, g[0]
    return typ


def disk_tests(mode):
    return ("disk_rr", "disk_rw", "disk_fsync") + (("disk_seq",) if mode == "full" else ())


def _fio(ctx, path, size, extra, timeout):
    argv = ["fio", "--name=kbench", "--filename=" + path, "--size=" + size, "--direct=1", "--ioengine=libaio",
            "--output-format=json"] + extra
    res = ctx.run(argv, timeout)
    return parse.fio_json(res.out) if res.ok else None


def disk(ctx):
    tests = disk_tests(ctx.mode)
    if not ctx.have["fio"]:
        return ctx.na(tests, "no_pkgs")
    typ = fs_type(os.path.realpath(ctx.work))
    if typ is None or typ in BAD_FS:
        return ctx.na(tests, "disk_fs")
    try:
        sv = os.statvfs(ctx.work)
    except OSError:
        return ctx.na(tests, "disk_fs")
    free = sv.f_bavail * sv.f_frsize
    used = 100.0 * (sv.f_blocks - sv.f_bfree) / sv.f_blocks if sv.f_blocks else 100.0
    if free < FIO_FREE_MIN:
        return ctx.na(tests, "disk_space")
    if used >= FIO_USED_MAX:
        return ctx.na(tests, "disk_full")
    path = os.path.join(ctx.work, FIO_FILE)
    if ":" in path:                         # fio reads «:» as a list of files
        return ctx.na(tests, "disk_fs")
    ctx.files.append(path)
    try:
        ctx.progress("disk", 1, len(tests))
        doc = _fio(ctx, path, "1G", ["--rw=randread", "--bs=4k", "--iodepth=32", "--runtime=10", "--time_based"], 60)
        ctx.row("disk_rr", parse.fio_iops(doc, "read"))
        ctx.progress("disk", 2, len(tests))
        doc = _fio(ctx, path, "1G", ["--rw=randwrite", "--bs=4k", "--iodepth=32", "--io_size=1G", "--runtime=10"], 35)
        ctx.row("disk_rw", parse.fio_iops(doc, "write"))
        ctx.progress("disk", 3, len(tests))
        doc = _fio(ctx, path, "16M", ["--rw=write", "--bs=4k", "--fsync=1", "--runtime=5"], 35)
        ctx.row("disk_fsync", parse.fio_fsync_ms(doc))
        if ctx.mode == "full":
            ctx.progress("disk", 4, len(tests))
            doc = _fio(ctx, path, "1G", ["--rw=read", "--bs=1M", "--runtime=5", "--time_based"], 35)
            ctx.row("disk_seq", parse.fio_bw_mbs(doc, "read"))
    finally:
        remove_files(ctx)


def remove_files(ctx):
    for p in list(ctx.files):
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass
        except OSError:
            continue
        ctx.files.remove(p)


# ── 8, 8b, 8c: the glue of the portable block ``bench.channel`` ─────────────

_LOC = re.compile(r"[A-Za-z]{2}")


def _bench_cfg(st):
    g = (st or {}).get("gateway") if isinstance(st, dict) else None
    hm = g.get("hostmon") if isinstance(g, dict) else None
    b = hm.get("bench") if isinstance(hm, dict) else None
    return b if isinstance(b, dict) else {}


def _last_pass():
    try:
        from ..gw.probe import run as prun
        p = prun.read_state().get("last_pass")
    except Exception:                       # noqa: BLE001 — no pass is no country
        return {}
    return p if isinstance(p, dict) else {}


def node_ru(st, series=None, last_pass=None, ms=None, keep=None):
    """«Узел в России» (RS §2): ``direct_loc`` of the geo pass of the exits (``gw/probe``) -> the country kept by the
    collector (``bench.geo``, ``src: exits_geo``) -> the setting ``gateway.hostmon.bench.ru`` («auto» | «yes» | «no»)
    -> the delays: 77.88.8.8 clearly nearer than 1.1.1.1 or the other way round (``channel.ru_by_rtt``: twice and by
    10 ms) — by the medians of the «now» series of the check, else by the least of ``RTT_TRIES`` TCP connections to
    port 53 of each, 2 answers at least (never one connection — SEC З-5); an answer of the delays goes to ``keep(ru)``
    (``geo.remember_rtt``: the price and the planner of the repeat read it) -> the delays kept by an earlier check,
    only when they said Russia (≤ 30 d; SEC-ns-join З-2). -> True | False | None (unknown: the schedule, the repeat
    and the button take it as Russia, with a line — П3-а)."""
    from . import channel, geo
    lp = _last_pass() if last_pass is None else last_pass
    loc = lp.get("direct_loc") if isinstance(lp, dict) else None
    if isinstance(loc, str) and _LOC.fullmatch(loc) and geo.is_country(loc.upper()):
        return loc.upper() == "RU"                    # Cloudflare's «XX» is no country (REV-ns-join П-4)
    try:
        known = geo.country(st)
    except Exception:                       # noqa: BLE001 — a record that cannot be read is no country
        known = {"ru": None, "src": None}
    if known["src"] in ("exits_geo", "setting"):
        return known["ru"]
    rtt = dict((a.get("addr"), a.get("rtt_med")) for a in (series or {}).get("anchors") or () if isinstance(a, dict))
    ya, cf = rtt.get("77.88.8.8"), rtt.get("1.1.1.1")
    if ya is None or cf is None:
        ms = ms or channel.connect_ms
        ya, cf = _least(ms, "77.88.8.8"), _least(ms, "1.1.1.1")
    got = channel.ru_by_rtt(ya, cf)
    if got is not None:
        if keep is not None:
            keep(got)
        return got
    return known["ru"]


def _least(ms, addr):
    from . import channel
    got = [v for v in (ms(addr, 53) for _ in range(channel.RTT_TRIES)) if v is not None]
    return min(got) if len(got) >= 2 else None


def ru_point(st, mode):
    """The upload point the owner allowed a node in Russia (NS-S5): ``gateway.hostmon.bench.ru_point`` =
    {"url": "https://…", "quick": MB, "full": MB} -> (url, the cap of the mode in MB | None) or (None, None)."""
    p = _bench_cfg(st).get("ru_point")
    if not isinstance(p, dict) or not isinstance(p.get("url"), str):
        return None, None
    cap = p.get(mode)
    return p["url"], cap if type(cap) is int and cap > 0 else None


def _people(a, b, side):
    from . import quiet
    d = quiet.decide(a, b)
    if not d["known"]:
        return None                         # no accounting of the unit: the value as taken (NS-S16, «quiet: unknown»)
    return d["rx_mbps"] if side == "down" else d["tx_mbps"]


def _level_of():
    """L of NS-S16 by the history (read once a check) and ``kaskad.score.quiet_level``."""
    got = []

    def level(test, mode, ts):
        from .. import score
        from . import history
        if not got:
            got.append(history.sample(now=ts))
        smp = got[0]
        return score.quiet_level(score.load_spec(), smp["runs"], test, mode, ts, epochs=smp["epochs"])
    return level


IPERF_TCP_S = 0.5                   # s for one sample of the sockets of iperf3 (8c over 8b): the dump of both families


def iperf_tcp(pid, port, deadline_s=IPERF_TCP_S):
    """8c over 8b (D-144): {cookie: {snd_mss, rtt, min_rtt, bytes_sent, bytes_retrans, dsack_dups}} of the TCP
    connections of the process ``pid`` (iperf3, run as nobody by ``bench.channel``) to the remote ``port`` — the
    ``tcp_info`` of the kernel by sock_diag (``gw.hostpeople``: the request filtered by the remote port, the same
    checked parsing; the remote address is never unpacked) and only the sockets of that process (its ``/proc/<pid>/fd``).
    -> None when nothing can be read (no sock_diag, no rights, the process gone); {} — no such connection."""
    from ..gw import hostpeople as HP
    inodes = set()
    if HP.sock_inodes(int(pid), inodes) is not True or not inodes:
        return None
    deadline = _mono() + deadline_s
    budget = [HP.MAX_DUMP_BYTES]
    out, seen = {}, False
    for fam in (HP.AF_INET, HP.AF_INET6):
        for flt in (True, False):                   # an old kernel that refuses the filter (EINVAL): once without it
            got, status, it = [], None, None
            try:
                it = HP.netlink_dump(HP.request(fam, 1, [int(port)] if flt else None, dst=True), deadline, budget)
                for data in it:
                    status = HP.parse_datagram(data, 1, got, HP.MAX_SOCKS)
                    if status != HP._MORE:
                        break
            except HP._Stop:
                status = None
            except OSError:
                status = None
            finally:
                close = getattr(it, "close", None)
                if close is not None:
                    close()
            if status == errno.EINVAL and flt and not got:
                continue
            if got or status == HP._DONE:
                seen = True
            for _port, cookie, inode, info, _cg in got:
                if inode in inodes and isinstance(info, tuple) and len(info) == 6:
                    rtt, mrtt, mss, sent, retr, dsack = info
                    out[cookie] = {"snd_mss": mss, "rtt": rtt, "min_rtt": mrtt, "bytes_sent": sent,
                                   "bytes_retrans": retr, "dsack_dups": dsack}
            break
    return out if seen else None


def channel_env(ctx, skip=(), iperf=False):
    from . import channel
    url, cap = ru_point(ctx.st, ctx.mode)
    meter = ctx.meter

    def family(f):
        if meter is not None:
            meter.family = f                # OWN_SLACK of NS-S8 by the family of the connection of the test

    def log(key, **kw):
        try:
            util.log_tr(key, **kw)
        except Exception:                   # noqa: BLE001 — the journal never stops the test
            pass
    from . import geo
    return channel.Env(ru=node_ru(ctx.st, ctx.series, keep=geo.remember_rtt), manual=bool(ctx.opts.get("manual")),
                       up_url=url,
                       up_cap_mb=cap, iperf=iperf,
                       have_iperf=ctx.have.get("iperf3", False), skip=skip,
                       probe=meter.read if meter is not None else None, people=_people, level=_level_of(),
                       port=None,           # the port of the tariff (C1) — when it is in the state
                       family=family, log=log, left=ctx.left, progress=ctx.progress, tcp=iperf_tcp)


# ── the order ───────────────────────────────────────────────────────────────

def _cpu_mt_s(mode):
    return 25 if mode == "full" else 20


# (name, function, the seconds it may take at most — not started when less is left, tests(mode))
BASE = (
    ("inv", inventory, lambda m: 5, lambda m: ("mem_gb",)),
    ("cpu_a", cpu_a, lambda m: 25, lambda m: ("cpu_1t",)),
    ("now", now_series, lambda m: 20, lambda m: ("now_loss", "now_rtt")),
    ("dns", dns, lambda m: DNS_N * DNS_TIMEOUT + 2, lambda m: ("dns",)),
    ("cpu_mt", cpu_mt, _cpu_mt_s, lambda m: ("cpu_mt",)),
    ("crypto", crypto, lambda m: 15, lambda m: ("chacha_1t", "aes_1t")),
    ("mem", memory, lambda m: MEM_SECS + 5, lambda m: ("mem_bw",)),
    ("disk", disk, lambda m: 140 if m == "full" else 105, disk_tests),
)
TAIL = (("cpu_b", cpu_b, lambda m: 25, lambda m: ("cpu_cv", "cpu_sustain")),)


def _channel(mode, skip=(), iperf=False):
    """B3: the module ``bench.channel`` gives the steps of the channel (8, 8b, 8c) in the same form."""
    try:
        from . import channel
    except ImportError:
        return ()
    return tuple(channel.steps(mode, skip=skip, iperf=iperf))


# ── «only these»: the repeat after a sharp drop (SS §2.8 п. 2, B11) ────────

LINKS = ("down", "up", "crypto", "net_bw", "cpu")     # = kaskad.score.TEST_LINKS (a test keeps them equal)
LINK_STEPS = {"down": (), "up": (), "net_bw": (), "crypto": ("crypto",), "cpu": ("cpu_a", "cpu_mt", "crypto")}
LINK_SIDES = {"down": ("down",), "up": ("up",), "net_bw": ("down", "up"), "crypto": (), "cpu": ()}
ONLY_ALWAYS = ("inv",)              # the inventory (no traffic, 5 s): the base of the values of the repeat (NS-S17)


def only_plan(only):
    """The least set of the check that measures the links ``only`` again (SS §2.8 п. 2: ``down``/``up`` — test 8 of
    that side, ``net_bw`` — both sides, ``crypto`` — test 5, ``cpu`` — tests 3A, 4, 5; never 8b, 9) -> (the names of
    the steps, the sides of the channel). ValueError — a link that is not one of ``LINKS``."""
    bad = [x for x in only if x not in LINKS]
    if bad:
        raise ValueError("only: %s" % ", ".join(sorted(map(str, bad))))
    names = set(ONLY_ALWAYS)
    sides = set()
    for x in only:
        names.update(LINK_STEPS[x])
        sides.update(LINK_SIDES[x])
    return names, sides


def repeat_links(links, st):
    """The links a repeat can measure again (SS §2.8 п. 2): ``up`` of a node known to be in Russia (``geo.country``:
    the geo pass of the exits, the setting, the delays kept) without an allowed upload point is left out — the repeat
    never uploads but to that point (NS-S5) and never runs 8b; ``net_bw`` stays (its download is measured). Unknown —
    kept: the check finds the country by its delays. -> [links] in the order given."""
    from . import channel, geo
    try:
        ru = geo.country(st)["ru"]
    except Exception:                       # noqa: BLE001 — unknown
        ru = None
    url = ru_point(st, "quick")[0]
    point = bool(url) and channel.http_target({"id": "allowed", "url": url}) is not None
    return [x for x in links if not (x == "up" and ru is True and not point)]


def steps(mode, skip=(), iperf=False, only=()):
    """The order of the check. ``skip``/``iperf`` — of the channel (``bench.channel.SKIP``; the extension point of the
    schedule and the repeat, B11). ``only`` — the links of a repeat (``only_plan``): its steps alone, in the same
    order; the side of the channel no link needs is skipped (``skip`` + ``down``/``up``); 8b and 9 — never."""
    if not only:
        return BASE + _channel(mode, skip, iperf) + TAIL
    names, sides = only_plan(only)
    skip = set(skip or ()) | {"8b", "9"} | set(x for x in ("down", "up") if x not in sides)
    return (tuple(x for x in BASE if x[0] in names) + (_channel(mode, sorted(skip), False) if sides else ())
            + tuple(x for x in TAIL if x[0] in names))


def run_all(ctx):
    """Every step in order. A step that does not fit the time left — its tests «не измерено: время вышло». A step
    that raises — its tests not written yet «не измерено» (``tool_failed``), the next step goes. Cancelled goes up
    (a BaseException: the caller writes what was measured)."""
    plan = steps(ctx.mode, ctx.opts.get("skip") or (), bool(ctx.opts.get("iperf")), ctx.opts.get("only") or ())
    for k, (name, fn, need, tests) in enumerate(plan, 1):
        ctx.stage = (k, len(plan))          # «этап k из m» хода в терминале (T-3377 Н-8): шаги i/n этапа — внутри него
        if ctx.left() < need(ctx.mode):
            ctx.na(tests(ctx.mode), "time")
            continue
        ctx.progress(name, 0, 0)
        n0 = len(ctx.rows)
        try:
            fn(ctx)
        except Exception as e:              # noqa: BLE001 — one step never ends the check (Cancelled is not one)
            done = set(r["test"] for r in ctx.rows[n0:])
            ctx.na([t for t in tests(ctx.mode) if t not in done], "tool_failed")
            util.log_tr("bench_run.log.step_failed", step=name, why=type(e).__name__)
    return ctx.rows
