"""Readers of the outputs of the tools of the server check (SPEC-node-score §6.2 B2; RESEARCH-node-score §2, tests 0–7).

Pure functions: text in, numbers out. Anything not understood is ``None`` (the test is then «не измерено» with its
reason) — never an exception in front of a person. Checked on the outputs of the versions of the systems Kaskad runs on
(tests/fixtures/bench, ``real/`` — the captures of 19.10): sysbench 1.0.20 (Debian 12/13, Ubuntu 22.04 and 24.04 — one
version everywhere), fio 3.33 / 3.39 / 3.28 / 3.36 with ``--output-format=json``, ``openssl speed -aead -mr`` of
OpenSSL 3.0.x and 3.5.x (and 1.1.1, the same lines), util-linux ``lscpu`` / ``lsblk``, ``/proc/cpuinfo``,
``/proc/meminfo``, ``/etc/os-release``.

    sysbench_cpu(text)            -> events per second | None
    sysbench_memory(text)         -> MiB/s | None
    openssl_speed(text, size)     -> bytes/s of the block ``size`` | None   (the line «+F:<n>:<name>:<bytes/s>»)
    openssl_version(text)         -> «3.0.14» of ``openssl version`` (the library's when it says) | None
    fio_json(text)                -> the JSON object of fio | None (lines of fio before it are skipped)
    fio_iops(doc, side)           -> IOPS of ``read`` | ``write`` | None (a job with an error — None)
    fio_clat_median_ms(doc, side) -> the median completion latency, ms | None
    fio_fsync_ms(doc)             -> 1000 / IOPS of the write of the fsync job (iodepth 1): «записал и подтвердил», ms
    fio_bw_mbs(doc, side)         -> MB/s (10^6 bytes) | None
    cpuinfo(text)                 -> {"model": str | None, "flags": [aes, avx2, vaes present]}
    lscpu(text)                   -> {"model": str | None, "threads_per_core": int | None, "sockets": int | None}
    meminfo_kb(text, key)         -> kB | None
    lsblk(text)                   -> [{"name", "rota", "size", "type"}] of ``lsblk -d -b -n -o NAME,ROTA,SIZE,TYPE``
    os_release(text)              -> {"id", "version_id", "pretty"}
    median(xs), cv_pct(xs)        the statistics of the series
"""

import json
import math
import re
import statistics

_NUM = r"([0-9]+(?:\.[0-9]+)?)"
_EPS = re.compile(r"^\s*events per second:\s*" + _NUM + r"\s*$", re.M)
_MEM = re.compile(_NUM + r" MiB transferred \(" + _NUM + r" MiB/sec\)")
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")
FLAGS = ("aes", "avx2", "vaes")         # the flags of the inventory (test 0): AES-GCM in hardware, the vector units
TEXT_MAX = 80                           # a model name is cut to this many characters


def _num(s):
    if isinstance(s, bool):                 # JSON true/false is not a number (REV-ns-b1-3 П-2)
        return None
    try:
        v = float(s)
    except (TypeError, ValueError, OverflowError):      # an integer of hundreds of digits in a JSON of fio
        return None
    return v if math.isfinite(v) and v >= 0 else None


def _clean(s):
    """A line of the system (the model of the CPU) made safe for a screen and a journal: no control characters,
    single spaces, at most ``TEXT_MAX`` characters; empty — None."""
    s = " ".join("".join(c if c.isprintable() else " " for c in _CTRL.sub(" ", str(s or ""))).split())[:TEXT_MAX]
    return s or None


# ── sysbench 1.0.20 ─────────────────────────────────────────────────────────

def sysbench_cpu(text):
    """«events per second: 1234.56» of ``sysbench cpu … run``; none, or zero events — None."""
    m = _EPS.search(text or "")
    v = _num(m.group(1)) if m else None
    return v if v else None


def sysbench_memory(text):
    """«20480.00 MiB transferred (10456.12 MiB/sec)» of ``sysbench memory … run`` -> MiB/s."""
    m = _MEM.search(text or "")
    v = _num(m.group(2)) if m else None
    return v if v else None


# ── openssl speed -mr ───────────────────────────────────────────────────────

def openssl_speed(text, size=1420):
    """bytes/s of the block ``size`` from the machine-readable output of ``openssl speed -evp <cipher> -bytes <size>
    -seconds 1 -mr``: «+H:<size>[:<size>…]» names the blocks, «+F:<n>:<name>:<bytes/s>[:…]» gives the speeds in the
    same order. No «+F» line, a block the header does not name, a value that is not a number — None."""
    sizes, speed = None, None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("+H:"):
            sizes = line.split(":")[1:]
        elif line.startswith("+F:"):
            speed = line.split(":")[3:]
    if not speed:
        return None
    if sizes is None:
        sizes = [str(size)] if len(speed) == 1 else []
    try:
        i = [s.strip() for s in sizes].index(str(size))
    except ValueError:
        return None
    if i >= len(speed):
        return None
    v = _num(speed[i])
    return v if v else None


_OSSL_VER = re.compile(r"OpenSSL\s+([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}[a-z]{0,2})(?![0-9A-Za-z.])")


def openssl_version(text):
    """The version of OpenSSL of ``openssl version``: «OpenSSL 3.0.14 4 Jun 2024 (Library: OpenSSL 3.0.14 4 Jun
    2024)» -> «3.0.14». The library does the work of ``openssl speed``: its version when the line names it, else the
    first one; 1.1.1 has a letter («1.1.1w»). LibreSSL, nothing understood — None."""
    text = text or ""
    lib = text.find("Library:")
    m = _OSSL_VER.search(text, lib) if lib >= 0 else None
    m = m or _OSSL_VER.search(text)
    return m.group(1) if m else None


# ── fio --output-format=json ────────────────────────────────────────────────

def fio_json(text):
    """The JSON document of fio. fio may print lines before it (``fio: …`` notes, a «note: …» about the queue depth):
    the document starts at the first line that begins with «{»."""
    text = text or ""
    dec = json.JSONDecoder()
    for m in re.finditer(r"(?m)^\{", text):
        try:
            doc, _end = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(doc, dict) and isinstance(doc.get("jobs"), list):
            return doc
    return None


def _side(doc, side):
    if not isinstance(doc, dict):
        return None
    jobs = doc.get("jobs")
    if not isinstance(jobs, list) or not jobs or not isinstance(jobs[0], dict):
        return None
    job = jobs[0]
    if job.get("error") not in (0, None):
        return None
    s = job.get(side)
    return s if isinstance(s, dict) else None


def fio_iops(doc, side):
    s = _side(doc, side)
    if s is None or not s.get("total_ios"):
        return None
    v = _num(s.get("iops"))
    return v if v else None


def fio_clat_median_ms(doc, side):
    """The 50th percentile of ``clat_ns`` (fio ≥ 3: nanoseconds; percentiles on by default) -> ms."""
    s = _side(doc, side)
    clat = s.get("clat_ns") if s else None
    pct = clat.get("percentile") if isinstance(clat, dict) else None
    if not isinstance(pct, dict) or not clat.get("N"):
        return None
    for k, v in pct.items():
        p = _num(k)
        if p is not None and abs(p - 50.0) < 1e-6:
            ns = _num(v)
            return ns / 1e6 if ns else None
    return None


def fio_fsync_ms(doc):
    """The time of one write with its fsync, ms = 1000 / IOPS of ``write`` of the job ``--rw=write --fsync=1``
    (iodepth 1: one write and its fsync at a time — RESEARCH-node-score, the coordinator's decisions of 19.10, п. 1).
    The median ``clat`` leaves the fsync out (0,05–0,07 ms against 0,21–0,61 ms on the same VPS); the section «sync»
    of fio 3.28 is not usable (0,1–0,2 µs). No writes, an error — None."""
    iops = fio_iops(doc, "write")
    return 1000.0 / iops if iops else None


def fio_bw_mbs(doc, side):
    s = _side(doc, side)
    if s is None or not s.get("total_ios"):
        return None
    b = _num(s.get("bw_bytes"))
    if b is None:
        kib = _num(s.get("bw"))
        b = kib * 1024 if kib is not None else None
    return b / 1e6 if b else None


# ── the inventory (test 0) ──────────────────────────────────────────────────

def cpuinfo(text):
    """/proc/cpuinfo: the model (x86 «model name»; arm has none — lscpu gives it) and which of ``FLAGS`` the first
    processor has («flags» on x86, «Features» on arm)."""
    model, flags = None, set()
    for line in (text or "").splitlines():
        k, sep, v = line.partition(":")
        if not sep:
            continue
        k = k.strip().lower()
        if k == "model name" and model is None:
            model = _clean(v)
        elif k in ("flags", "features") and not flags:
            flags = set(v.split())
    return {"model": model, "flags": [f for f in FLAGS if f in flags]}


def _int(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def lscpu(text):
    out = {"model": None, "threads_per_core": None, "sockets": None}
    for line in (text or "").splitlines():
        k, sep, v = line.partition(":")
        if not sep:
            continue
        k = k.strip()
        if k == "Model name" and out["model"] is None:
            out["model"] = _clean(v)
        elif k == "Thread(s) per core":
            out["threads_per_core"] = _int(v)
        elif k == "Socket(s)":
            out["sockets"] = _int(v)
    return out


def meminfo_kb(text, key="MemTotal"):
    m = re.search(r"(?m)^" + re.escape(key) + r":\s+([0-9]+)\s*kB\s*$", text or "")
    return int(m.group(1)) if m else None


_LSBLK_NAME = re.compile(r"^[A-Za-z0-9_.!:-]{1,64}$")


def lsblk(text):
    """``lsblk -d -b -n -o NAME,ROTA,SIZE,TYPE`` (no header, bytes): disks only (type «disk»), loop and rom dropped."""
    out = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) != 4 or not _LSBLK_NAME.match(parts[0]) or parts[3] != "disk":
            continue
        rota, size = _int(parts[1]), _int(parts[2])
        if rota not in (0, 1) or size is None:
            continue
        out.append({"name": parts[0], "rota": bool(rota), "size": size, "type": parts[3]})
    return out


def os_release(text):
    out = {"id": None, "version_id": None, "pretty": None}
    keys = {"ID": "id", "VERSION_ID": "version_id", "PRETTY_NAME": "pretty"}
    for line in (text or "").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() in keys:
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[keys[k.strip()]] = _clean(v)
    return out


# ── statistics ──────────────────────────────────────────────────────────────

def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def cv_pct(xs):
    """The coefficient of variation of a series, % (sample standard deviation / mean); fewer than 2 values — None."""
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    mean = statistics.mean(xs)
    return 100.0 * statistics.stdev(xs) / mean if mean > 0 else None
