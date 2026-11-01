"""The engine of AmneziaWG on the node (k3/SPEC-awg-engine.md §1–§4): ``kernel`` — the module amneziawg through DKMS
(gw/awgkmod.py), ``program`` — amneziawg-go. One engine for every line of the node: an inbound on the program with a
leg on the module (or the other way round) cuts one direction 2.5–3 times (the laboratory of 07.10.2026,
k3/stand/logs/lab-awg-mac.md).

Why the module is the default (1 vCPU, measured): plain VPN — half the CPU of amneziawg-go, a ceiling 10–20 % higher;
transit (inbound -> leg -> exit) — 2–3 times more; no pool of buffers of amneziawg-go (0.9–1.4 GB under load, OOM on
2 GB nodes).

The choice lives in state.json, ``gateway.awg_engine``::

    {"want": "kernel" | "program" | null,   null — a node from before 3.3 that has not been asked yet: the program
     "why": "установка: по умолчанию",      who chose (the install, the person in the menu / panel / bot, the CLI)
     "at": "2026-10-07T12:00:00Z",
     "reason": "…", "code": "host|kernel|headers|packages|disk|build|selftest|mix|rollback",
                                            why the node is on the program although the module is wanted (words)
     "pending": true,                       an update without a person found the node on the program: the faces
                                            offer «Перейти на модуль ядра» (one button)
     "force": true}                         the module with a line it cannot take (a leg of 3.1 or 1.x; an inbound
                                            3.1 when the set of fixes of the module did not build here): mixing on
                                            purpose

What really runs (and the fallback at boot) stays in /etc/kaskad/gw/awg-kmod.json (awgkmod).

``check`` — can the module go here (no change): blockers (host — container, Secure Boot, architecture; the kernel;
headers; packages; disk; mix — a line the module cannot take), warnings, what will be installed. It is the check
before an install and before a switch.

``switch`` — ``kaskadctl gw awg engine kernel|program``: everything moves (keys, parameters, ports, people, devices,
issued settings, the legs) — the state is one and the same, only what brings the interfaces up changes; one short
reconnect. Before: a copy (state.json, secrets.json, the AmneziaWG configs, the engine file) to
/var/backups/kaskad/awg-engine-<time>/ (the last BACKUPS kept). After: the self-test of the module (``awgkmod.install``)
and the clients that were alive must handshake again within WAIT_CLIENTS s — else the former engine comes back and the
reason is written down.

``first`` — the first AmneziaWG inbound or leg of a node (the install): the module by default (the wizard asks, «да»
by default; the panel and the CLI do not ask), the program on a «нет»; a failure — the program and the reason in
words, the install goes on. ``offer`` — after an update without a person: never switch, mark ``pending`` (or the
reason the module cannot go here). ``reconcile`` — after ``gw apply``: a line the module cannot take appeared on a
node on the module (a key 3.1 of an exit; an inbound 3.1 while the module in memory lacks the set of 3.1) — the
whole node goes to the program, with the reason, unless ``force``.

Line 3.1 (awgkmod.kernel_31): the inbound awg3 goes onto the module with the set of fixes of Kaskad
(vendor/awg-kmod-31.patch), the legs of 3.1, 1.5 and 1.0 too (awgkmod.leg_lines); without it — the program, as before
(a leg of 3.1: #222 on x86).

``export`` / ``import_`` — people, devices, the AmneziaWG inbounds with their keys and parameters to a file (0600,
secrets inside) and back: to another server or a manual way back.
"""

import copy
import glob
import json
import os
import re
import shutil
import time

from .. import paths, secrets, state, util
from ..errors import ConflictError, KaskadError, UnsupportedError, USAGE
from ..i18n import LANGS, fmt_num, lower_first, tr, trn
from . import awgkmod, exits, model

AUTO = True                     # tests of other parts switch the automatic module off (gwcase): no host commands
ENGINES = ("kernel", "program")
TITLE = awgkmod.Words("gw_awgengine.title.", ENGINES)      # {engine: its name} in the language of the moment
# {engine: its word in the journal line «GW AWG engine: was → target»}: RU — the code as in 3.3.1, EN — «kernel module»,
# «userspace» (glossary; T-M6-332 №19: «program → kernel» in an English journal)
LOG_WORD = awgkmod.Words("gw_awgengine.log.", ENGINES)
INBOUND_LINE = {"awg1": "1.5", "awg2": "2.0", "awg3": "3.1"}
# Смена движка, перезапуск входа (MTU): сервер поднимает интерфейс заново за доли секунды, но клиент сам повторяет
# рукопожатие только через ≈15 с (KEEPALIVE_TIMEOUT 10 с + REKEY_TIMEOUT 5 с) — замер T-N5 3.2.19: 14–23 с. Одна
# фраза для меню, мастера, бота и панели (в install.sh — K3_AWG_RECONNECT, в панели — окно «Подробнее» (install.text) и awg.toProgramText):
# RECONNECT — gw_awgengine.reconnect (module __getattr__ below: the language of the moment).
KERNEL_MIN = (5, 4)                       # older: the module is not built by Kaskad
KERNEL_TESTED = ((6, 1), (7, 0))          # built in the laboratory: 6.1 (Debian 12) … 7.0 (Ubuntu 26.04)
FREE_NEED_MB = 600                        # the compiler, the headers (≈200 МБ), the build and apt's cache
TOOLCHAIN_MB = 150
HEADERS_MB = 60
BACKUPS = 3
WAIT_CLIENTS = 180                        # s for the clients that were alive to handshake again
POLL = 5
ALIVE = 180                               # s: a handshake younger than this — the client is alive
STICKY = ("host", "kernel", "headers", "packages", "disk", "build", "selftest")   # need a person, not a minute
# Reasons about one kernel (too old, no headers for it, the module did not build or pass the self-test under it): the
# node booted another kernel — the reason no longer holds (T-N5 3.2.19-r2, Н-1: after 6.8.0-48 → 6.8.0-146 the faces
# kept saying «для ядра 6.8.0-48 нет заголовков» next to the offer of the module).
KERNEL_BOUND = ("kernel", "headers", "build", "selftest")
_KVER_RE = re.compile(r"\b\d+\.\d+(?:\.\d+)?-[0-9A-Za-z][0-9A-Za-z.+~_-]*")
EXPORT_KIND = "kaskad-awg-export"

BENEFIT_KEYS = ("gw_awgengine.benefit.cpu", "gw_awgengine.benefit.transit", "gw_awgengine.benefit.memory")
RISK_KEYS = ("gw_awgengine.risk.panic", "gw_awgengine.risk.build")
# Who chose the engine (``why`` of the record, ``--why`` of kaskadctl gw awg engine|first): a code; the words —
# tr("gw_awgengine.why.<code with . for : and _ for ->") when shown. The words before 3.3.2 (old records, an old
# install.sh passing --why) are taken as their code; any other text — as is.
WHY_CODES = ("install:default", "cli", "person:menu", "person:menu-update", "awg:first-leg", "person:install",
             "person:panel", "person:bot", "person:wizard", "mcp", "rollback")
_WHY_OLD = {"установка: по умолчанию": "install:default", "человек: меню": "person:menu",     # i18n: skip — old words
            "человек: меню (после обновления)": "person:menu-update",                       # i18n: skip — old words
            "первая нога AmneziaWG": "awg:first-leg", "человек при установке": "person:install",  # i18n: skip — old
            "человек: панель": "person:panel", "человек: бот": "person:bot",                 # i18n: skip — old words
            "человек: мастер": "person:wizard", "откат": "rollback"}                        # i18n: skip — old words
# Texts other modules read as attributes: a tr() in the language of the moment (never frozen at import).
_TEXTS = {"RECONNECT": "gw_awgengine.reconnect", "MIX_COST": "gw_awgengine.mix_cost"}


def __getattr__(name):
    if name in _TEXTS:
        return tr(_TEXTS[name])
    if name == "BENEFIT":
        return [tr(k) for k in BENEFIT_KEYS]
    if name == "RISKS":
        return [tr(k) for k in RISK_KEYS]
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def _reconnect():
    return tr("gw_awgengine.reconnect")


def why_code(why):
    """The code of who chose: a code as is, the words of a code (any language, before 3.3.2 too) -> the code, anything
    else -> the text itself (shown as is)."""
    text = str(why or "").strip()
    if not text:
        return ""
    if text.lower() in WHY_CODES:
        return text.lower()
    if text in _WHY_OLD:
        return _WHY_OLD[text]
    for code in WHY_CODES:
        if any(text == tr(_why_key(code), lang) for lang in LANGS):
            return code
    return text


def _why_key(code):
    return "gw_awgengine.why." + code.replace(":", ".").replace("-", "_")


def why_text(why):
    """The words of ``why`` (a code or a free text) in the language of the moment."""
    code = why_code(why)
    return tr(_why_key(code)) if code in WHY_CODES else code

_now = time.time                # tests patch them
_sleep = time.sleep


# ── the record in state.json ───────────────────────────────────────────────

def running_kernel():
    """The running kernel as ``uname -r`` prints it, without a command (/proc/sys/kernel/osrelease); "" — unknown."""
    return (util.read_text(paths.P("/proc/sys/kernel/osrelease")) or "").strip()


def record(st):
    """``gateway.awg_engine`` with every key present."""
    g = model.peek(st or {})
    rec = g.get("awg_engine") if isinstance(g.get("awg_engine"), dict) else {}
    want = rec.get("want") if rec.get("want") in ENGINES else None
    return {"want": want, "why": why_text(rec.get("why")), "why_code": why_code(rec.get("why")), "at": rec.get("at"),
            "reason": rec.get("reason") or None, "code": rec.get("code") or None,
            "kernel": str(rec.get("kernel") or "") or None,
            "pending": bool(rec.get("pending")), "force": bool(rec.get("force"))}


def stale_kernel(rec, now=None):
    """The kernel the reason of ``rec`` was about, when the node now runs another one (the reason no longer holds);
    None — the reason holds, is not about a kernel, or the running kernel is unknown. A record from before 3.2.20 has
    no ``kernel``: the version in the words of the reason («Для работающего ядра 6.8.0-48-generic …») is compared."""
    if not rec.get("reason") or rec.get("code") not in KERNEL_BOUND:
        return None
    now = running_kernel() if now is None else now
    if not now:
        return None
    was = rec.get("kernel")
    if was:
        return was if was != now else None
    seen = [k.rstrip(".-_~+") for k in _KVER_RE.findall(rec["reason"])]
    return seen[0] if seen and now not in seen else None


def _load():
    try:
        return state.load()
    except KaskadError:
        return {}


def save_record(**kw):
    """Change ``gateway.awg_engine`` under the state lock; -> the record."""
    with state.transaction() as st:
        g = model.gw(st)
        rec = dict(g.get("awg_engine") if isinstance(g.get("awg_engine"), dict) else {})
        if "why" in kw:
            kw = dict(kw, why=why_code(kw["why"]))                              # stored as a code (words when shown)
        if "reason" in kw and "kernel" not in kw:
            kw = dict(kw, kernel=running_kernel() if kw["reason"] else None)   # the reason is about this kernel
        rec.update(kw)
        if "want" in kw or "reason" in kw:
            rec["at"] = util.now_iso()
        g["awg_engine"] = dict((k, v) for k, v in rec.items() if v not in (None, False, ""))
        return record(st)


# ── what runs where ────────────────────────────────────────────────────────

def inbounds_of(st, k31=None):
    """AmneziaWG inbounds installed and enabled: [{"kind", "line", "title", "module"}]. ``k31`` — whether the module
    takes 3.1 (None: as now, awgkmod.kernel_31; True: the module about to be built with the set)."""
    out = []
    on_module = awgkmod.kernel_kinds(k31)
    try:
        kinds = model.enabled_kinds(st)
    except (KaskadError, AttributeError, KeyError, TypeError):
        return out
    for kind in kinds:
        if kind not in awgkmod.AWG_KINDS:
            continue
        rec = model.inbound(st, kind) or {}
        line = str(rec.get("line") or INBOUND_LINE[kind])
        out.append({"kind": kind, "line": line, "title": "AmneziaWG %s" % line, "module": kind in on_module})
    return out


def legs_of(st, pool, k31=None):
    """AmneziaWG legs of the pool the gateway uses: [{"id", "iface", "line", "name", "module"}]."""
    out = []
    on_module = awgkmod.leg_lines(k31)
    for e in pool or []:
        if e.get("kind") != "awg":
            continue
        try:
            if not model.exit_pref(st, e["id"])["enabled"]:
                continue
            name = exits.label(st, e)
        except (KaskadError, AttributeError, KeyError, TypeError):
            name = e.get("id") or ""
        line = str(e.get("version") or "")
        out.append({"id": e.get("id"), "iface": e.get("iface"), "line": line, "name": name,
                    "module": line in on_module, "loss226": bool(e.get("loss226"))})
    return out


def _pool(runner, pool):
    if pool is not None:
        return pool
    try:
        return exits.pool(runner, roles=exits.ROLES)
    except KaskadError:
        return []


def mix_texts(inbounds, legs):
    """What stays on amneziawg-go although the module is the engine — in words, with what to do."""
    out = []
    cost = tr("gw_awgengine.mix_cost")
    for r in inbounds:
        if not r["module"]:
            out.append(tr("gw_awgengine.mix.inbound", title=r["title"], cost=cost, line=r["line"]))
    for r in legs:
        if not r["module"]:
            if r["line"] == "3.1":
                why = tr("gw_awgengine.mix.leg31")
            elif r["line"] in awgkmod.LEG_LINES_SET:
                why = tr("gw_awgengine.mix.leg_set")
            else:
                why = tr("gw_awgengine.mix.leg_other")
            what = tr("gw_awgengine.mix.key", line=r["line"]) if r["line"] else tr("gw_awgengine.mix.key_unknown")
            out.append(tr("gw_awgengine.mix.leg", name=r["name"], what=what, why=why, cost=cost))
    return out


def mix(runner=None, st=None, pool=None, k31=None):
    """Mixing texts for this node now ([] — every AmneziaWG line can go onto the module, or there is none). ``k31``
    — see inbounds_of."""
    st = _load() if st is None else st
    return mix_texts(inbounds_of(st, k31), legs_of(st, _pool(runner, pool), k31))


def mix_warning(texts):
    """One short line for ``gw status`` when the node is on the module and something stays on the program."""
    if not texts:
        return []
    return [tr("gw_awgengine.mix.warning", lines=trn("gw_awgengine.mix.lines", len(texts)))]


# ── the host ───────────────────────────────────────────────────────────────

def _free_mb(path):
    p = path
    while p and not os.path.isdir(p):
        p = os.path.dirname(p)
    try:
        s = os.statvfs(p or "/")
    except OSError:
        return None
    return int(s.f_bavail * s.f_frsize / 2 ** 20)


def free_mb():
    """Free space where apt and DKMS write (/usr, /var — the smaller one), МБ."""
    vals = [v for v in (_free_mb(paths.P("/usr")), _free_mb(paths.P("/var"))) if v is not None]
    return min(vals) if vals else None


def _apt_has(runner, pkg):
    res = runner.run(["apt-cache", "show", "--no-all-versions", pkg], timeout=30)
    return res.ok and "Package:" in (res.out or "")


def mem_total():
    for line in (util.read_text(paths.P("/proc/meminfo")) or "").splitlines():
        k, _, v = line.partition(":")
        f = v.split()
        if k.strip() == "MemTotal" and f and f[0].isdigit():
            return int(f[0]) * 1024
    return None


def cpus():
    n = sum(1 for line in (util.read_text(paths.P("/proc/stat")) or "").splitlines() if re.match(r"^cpu\d+\s", line))
    return n or None


def go_rss():
    """Resident memory of every amneziawg-go process (bytes, summed); None when /proc cannot be read."""
    base = paths.P("/proc")
    try:
        pids = [p for p in os.listdir(base) if p.isdigit()]
    except OSError:
        return None
    total = 0
    for pid in pids:
        if (util.read_text(os.path.join(base, pid, "comm")) or "").strip() != "amneziawg-go":
            continue
        for line in (util.read_text(os.path.join(base, pid, "status")) or "").splitlines():
            if line.startswith("VmRSS:"):
                f = line.split()
                if len(f) >= 2 and f[1].isdigit():
                    total += int(f[1]) * 1024
                break
    return total


def host_facts(runner):
    """{"host", "kernel", "headers": installed|available|missing|None, "apt", "dkms", "gcc", "free_mb"} — commands
    uname, systemd-detect-virt, mokutil, apt-cache."""
    host = awgkmod.supported_host(runner)
    kernel = awgkmod.running_kernel(runner)
    apt = bool(runner.which("apt-cache"))
    out = {"host": host, "kernel": kernel, "headers": None, "apt": apt, "dkms": None, "gcc": bool(runner.which("gcc")),
           "free_mb": free_mb()}
    if host.get("ok") and not os.path.exists(paths.P("/proc/modules")) and os.path.isdir(paths.P("/proc")) \
            and os.path.exists(paths.P("/proc/stat")):
        host = dict(host, ok=False, why=tr("gw_awgengine.host.no_modules"))
        out["host"] = host
    if kernel and os.path.exists(awgkmod.kernel_tree(kernel)):
        out["headers"] = "installed"
    elif kernel and apt:
        out["headers"] = "available" if _apt_has(runner, "linux-headers-%s" % kernel) else "missing"
    if runner.which("dkms"):
        out["dkms"] = "installed"
    elif apt:
        out["dkms"] = "available" if _apt_has(runner, "dkms") else "missing"
    return out


def install_plan(f):
    """What switching to the module puts on the node: {"packages", "mb", "minutes", "text"}."""
    pkgs, mb = [], 0
    if f.get("dkms") != "installed" or not f.get("gcc"):
        pkgs += ["dkms", "gcc", "make", "patch"]
        mb += TOOLCHAIN_MB
    if f.get("headers") != "installed":
        pkgs.append("linux-headers-%s" % (f.get("kernel") or "$(uname -r)"))
        mb += HEADERS_MB
    if pkgs:
        return {"packages": pkgs, "mb": mb, "minutes": "1–3",
                "text": tr("gw_awgengine.plan.install", pkgs=", ".join(pkgs), mb=mb, reconnect=_reconnect())}
    return {"packages": [], "mb": 0, "minutes": "1–2",
            "text": tr("gw_awgengine.plan.ready", reconnect=_reconnect())}


def blockers_of(f, inbounds, legs):
    """[(code, text)] of what keeps the module away, and warnings — from the host facts and the lines."""
    out, warns = [], []
    host = f.get("host") or {}
    if host.get("ok") is False:
        out.append(("host", tr("gw_awgengine.block.host", why=host.get("why") or tr("gw_awgengine.block.host_unfit"))))
    kernel = f.get("kernel") or ""
    kv = awgkmod._kver(kernel)[:2]
    if kernel and kv and kv < KERNEL_MIN:
        out.append(("kernel", tr("gw_awgengine.block.kernel_old", kernel=kernel,
                                 min="%d.%d" % (KERNEL_MIN[0], KERNEL_MIN[1]))))
    elif kernel and kv and not (KERNEL_TESTED[0] <= kv <= KERNEL_TESTED[1]):
        warns.append(tr("gw_awgengine.warn.kernel_untested", kernel=kernel))
    if f.get("apt") is False:
        out.append(("packages", tr("gw_awgengine.block.no_apt")))
    else:
        if f.get("headers") == "missing":
            out.append(("headers", tr("gw_awgengine.block.headers", kernel=kernel)))
        if f.get("dkms") == "missing":
            out.append(("packages", tr("gw_awgengine.block.no_dkms")))
    free = f.get("free_mb")
    if isinstance(free, int) and install_plan(f)["packages"] and free < FREE_NEED_MB:
        out.append(("disk", tr("gw_awgengine.block.disk", free=free, need=FREE_NEED_MB)))
    for t in mix_texts(inbounds, legs):
        out.append(("mix", t))
    return out, warns


def check(runner, st=None, pool=None, host=None):
    """Can the module go here (nothing changes): {"ok", "can", "blockers", "codes", "warnings", "install", "facts",
    "benefit", "risks", "pays"}. ``can`` — no blocker except, maybe, mixing (that one ``--force`` passes). The module
    is judged as the switch would build it: with the set of 3.1 (an inbound 3.1 is not mixing); a module in memory
    without the set — told: the switch rebuilds it."""
    st = _load() if st is None else st
    ins, legs = inbounds_of(st, True), legs_of(st, _pool(runner, pool), True)
    f = host if host is not None else host_facts(runner)
    bl, warns = blockers_of(f, ins, legs)
    doc = awgkmod.load()
    if any(r["line"] == "3.1" for r in ins) or any(r["line"] in awgkmod.LEG_LINES_SET for r in legs):
        failed = doc.get("set_failed") or {}
        if failed:
            warns.append(tr("gw_awgengine.warn.set_failed_before", why=failed.get("reason")))
        elif awgkmod.module_loaded() and not awgkmod.set_loaded():
            warns.append(tr("gw_awgengine.warn.rebuild", reconnect=lower_first(_reconnect())))
    for r in legs:
        if r.get("loss226"):
            warns.append(tr("gw_awgengine.warn.loss226", name=r["name"], text=awgkmod.TRAILERS_LOSS_TEXT))
    mem, rss = mem_total(), go_rss()
    pays = []
    if ins and legs:
        pays.append(tr("gw_awgengine.pays.transit"))
    if mem and mem <= int(2.25 * 2 ** 30) and rss and rss > 0.25 * mem:
        pays.append(tr("gw_awgengine.pays.memory", mem=fmt_bytes(mem), rss=rss // 2 ** 20))
    return {"ok": True, "can": not [c for c, _ in bl if c != "mix"], "blockers": [t for _, t in bl],
            "items": [{"code": c, "text": t} for c, t in bl], "codes": sorted(set(c for c, _ in bl)), "warnings": warns, "install": install_plan(f),
            "facts": {"cpus": cpus(), "mem_total": mem, "kernel": f.get("kernel") or None, "headers": f.get("headers"),
                      "dkms": f.get("dkms"), "free_mb": f.get("free_mb"), "go_rss": rss,
                      "host": f.get("host"), "inbounds": ins, "legs": legs, "transit": bool(ins and legs)},
            "benefit": [tr(k) for k in BENEFIT_KEYS], "risks": [tr(k) for k in RISK_KEYS], "pays": pays}


def fmt_bytes(n):
    """Memory and disk for people: whole MB below 1 GB, GB with one decimal (ru «300 МБ», «2,0 ГБ»; en «300 MB»,
    «2.0 GB»: the decimal sign — kaskad.i18n.fmt_num)."""
    if not isinstance(n, (int, float)) or isinstance(n, bool):
        return "—"
    if n >= 2 ** 30:
        return tr("gw_awgengine.unit.gb", n=fmt_num(n / 2.0 ** 30, digits=1))
    return tr("gw_awgengine.unit.mb", n="%d" % int(round(n / 2.0 ** 20)))


# ── status (no commands: the faces read it) ────────────────────────────────

def active():
    """"kernel" | "program" — what the node runs now (acceleration on and the module loaded = kernel)."""
    return "kernel" if awgkmod.enabled() and awgkmod.module_loaded() else "program"


def status(runner=None, st=None, pool=None):
    """The engine for people and faces — files only (state.json, awg-kmod.json, /sys/module), the pool parsed in this
    process: {"want", "why", "active", "fallback", "reason", "code", "pending", "force", "has_awg", "mix",
    "offer": {"show", "blocked"}, "warnings", "text"}."""
    st = _load() if st is None else st
    rec = record(st)
    # the reason is about a kernel the node no longer runs (rebooted into a new one): not shown, not sticky — the
    # module is offered again; the check before the switch looks at the running kernel anew (uname -r)
    was = stale_kernel(rec)
    if was:
        rec = dict(rec, reason=None, code=None, stale_kernel=was, kernel_now=running_kernel())
    doc = awgkmod.load()
    enabled, loaded = bool(doc.get("enabled")), awgkmod.module_loaded()
    act = "kernel" if enabled and loaded else "program"
    ins, legs = inbounds_of(st), legs_of(st, _pool(runner, pool))
    has_awg = bool(ins or legs)
    mixed = mix_texts(ins, legs)
    fb = (doc.get("fallback") or {}) if enabled and not loaded else {}
    out = dict(rec, active=act, enabled=enabled, fallback=bool(enabled and not loaded),
               fallback_reason=fb.get("reason") or (tr("gw_awgengine.fallback.not_loaded") if enabled and not loaded
                                                    else None),
               has_awg=has_awg, mix=mixed, warnings=[])
    if enabled and mixed:
        out["warnings"] += mix_warning(mixed)
    # «Перейти на модуль ядра»: the node is on the program, and either an update without a person marked it (pending) or
    # the module is wanted and nothing that needs a person keeps it away (a line 3.1 — the explanation instead)
    show = has_awg and act == "program" and not enabled and rec["code"] not in STICKY \
        and ((rec["want"] is None and (rec["pending"] or bool(was))) or rec["want"] == "kernel")
    out["offer"] = {"show": show, "blocked": mixed if show else [], "pending": (rec["pending"] or bool(was)) and show}
    out["text"] = text_of(out)
    return out


def text_of(s):
    """«Движок AmneziaWG: модуль ядра» / «программа (…почему)» — one line for people."""
    if s["active"] == "kernel":
        if s.get("force") and s.get("mix"):
            return tr("gw_awgengine.text.kernel_forced")
        return tr("gw_awgengine.text.kernel")
    if s.get("fallback"):
        return tr("gw_awgengine.text.fallback", why=s.get("fallback_reason"))
    if s.get("want") == "program":
        return tr("gw_awgengine.text.chosen")
    if s.get("reason"):
        return tr("gw_awgengine.text.reason", why=s["reason"])
    if s.get("stale_kernel") and s.get("has_awg"):
        return tr("gw_awgengine.text.new_kernel", now=s.get("kernel_now"), was=s["stale_kernel"])
    if s.get("want") is None and s.get("has_awg"):
        return tr("gw_awgengine.text.never")
    if s.get("has_awg"):
        return tr("gw_awgengine.text.program")
    return tr("gw_awgengine.text.default") if s.get("want") != "program" else tr("gw_awgengine.text.chosen")


# ── backups ────────────────────────────────────────────────────────────────

def backups_dir():
    return paths.backups()


def backup(reason):
    """state.json, secrets.json, the AmneziaWG configs (/etc/kaskad/gw/awg*/) and the engine file into
    /var/backups/kaskad/awg-engine-<UTC time>/ (0700/0600); the last BACKUPS kept. -> its path."""
    base = backups_dir()
    if not os.path.isdir(base):
        os.makedirs(base, 0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(_now()))
    d = os.path.join(base, "awg-engine-%s" % stamp)
    n = 1
    while os.path.exists(d):
        n += 1
        d = os.path.join(base, "awg-engine-%s-%d" % (stamp, n))
    os.mkdir(d, 0o700)
    os.chmod(d, 0o700)
    files = [(paths.state(), "state.json"), (paths.secrets_file(), "secrets.json"),
             (awgkmod.state_path(), "awg-kmod.json")]
    for kind in awgkmod.AWG_KINDS:
        kd = paths.P("/etc/kaskad/gw/%s" % kind)
        for p in sorted(glob.glob(os.path.join(kd, "*"))):
            if os.path.isfile(p):
                files.append((p, os.path.join(kind, os.path.basename(p))))
    for src, rel in files:
        if not os.path.isfile(src):
            continue
        dst = os.path.join(d, rel)
        if not os.path.isdir(os.path.dirname(dst)):
            os.mkdir(os.path.dirname(dst), 0o700)
        with open(src, "rb") as f:
            util.atomic_write(dst, f.read(), 0o600)
    with util.journal_lang():                         # a file on the server: the language of the server
        readme = tr("gw_awgengine.backup.readme", reason=reason, at=util.now_iso())
    util.atomic_write(os.path.join(d, "README.txt"), readme + "\n", 0o600)
    old = sorted(p for p in glob.glob(os.path.join(base, "awg-engine-*")) if os.path.isdir(p))
    for p in old[:-BACKUPS]:
        shutil.rmtree(p, ignore_errors=True)
    return d


# ── the clients that were alive ────────────────────────────────────────────

def _adapters():
    from . import registry
    out = []
    for kind in awgkmod.AWG_KINDS:
        ad = registry.get(kind)
        ad = ad() if isinstance(ad, type) else ad
        if ad is not None and callable(getattr(ad, "tool", None)) and isinstance(getattr(ad, "iface", None), str):
            out.append(ad)
    return out


def handshakes(runner, st=None):
    """{(kind, public key): epoch of the latest handshake} of the AmneziaWG inbounds that run (``awg show <iface>
    latest-handshakes``)."""
    st = _load() if st is None else st
    on = set(model.enabled_kinds(st)) if model.enabled(st) else set()
    out = {}
    for ad in _adapters():
        if ad.kind not in on:
            continue
        try:
            res = runner.run([ad.tool(runner), "show", ad.iface, "latest-handshakes"], timeout=15)
        except Exception:                               # noqa: BLE001 — no answer is «no handshakes»
            continue
        for line in (res.out if res.ok else "").splitlines():
            f = line.split()
            if len(f) >= 2 and f[1].isdigit() and int(f[1]) > 0:
                out[(ad.kind, f[0])] = int(f[1])
    return out


def alive_clients(runner, st=None, now=None):
    now = _now() if now is None else now
    return set(k for k, ts in handshakes(runner, st).items() if now - ts <= ALIVE)


def wait_clients(runner, alive, since, say, wait=WAIT_CLIENTS):
    """True when at least one of ``alive`` handshook after ``since`` within ``wait`` s (nobody was alive: True)."""
    if not alive:
        return True
    say(tr("gw_awgengine.say.wait", alive=len(alive), minutes=max(1, wait // 60)))
    deadline = _now() + wait
    while True:
        hs = handshakes(runner)
        if any(hs.get(k, 0) >= since for k in alive):
            return True
        if _now() >= deadline:
            return False
        _sleep(POLL)


# ── switching ──────────────────────────────────────────────────────────────

_ERROR_CODES = ("selftest", "build", "headers", "packages", "host")
_ERROR_KINDS = {"apt_busy": "packages"}                  # data.kind of gw/sys.apt_install: apt held by the system


def _code_of(e):
    """The code of a failure of the module: ``data.kind`` of the error (awgkmod._fail, gw/sys.apt_install) — never
    the words, they are in the language of the moment; a refusal of the host (UnsupportedError) — host; else build."""
    data = getattr(e, "data", None)
    kind = data.get("kind") if isinstance(data, dict) else None
    kind = _ERROR_KINDS.get(kind, kind)
    if kind in _ERROR_CODES:
        return kind
    if isinstance(e, UnsupportedError):
        return "host"
    return "build"


def _to_kernel(runner, say, force):
    return awgkmod.install(runner, progress=say, force=force)


def _to_program(runner, say):
    return awgkmod.disable(runner, progress=say)


def switch(runner, target, force=False, dry_run=False, why="", progress=None, verify=True, host=None):
    """``kaskadctl gw awg engine kernel|program``. -> {"ok", "engine", "was", "backup", "warnings", "check"?,
    "rolled_back"?, "dry_run"?}. Refusals: mixing without ``force`` (ConflictError, code 3), a host where the module
    cannot go (UnsupportedError, code 5) — the node then stays as it was and the reason is written down."""
    if target not in ENGINES:
        raise KaskadError(tr("gw_awgengine.err.target"), code=USAGE)
    say = progress or (lambda text: None)
    st = _load()
    was = active()
    out = {"ok": True, "engine": target, "was": was, "warnings": [], "backup": None}
    chk = None
    if target == "kernel":
        chk = check(runner, st=st, host=host)
        out["check"] = chk
        hard = [i["text"] for i in chk["items"] if i["code"] != "mix"]
        mixed = [i["text"] for i in chk["items"] if i["code"] == "mix"]
        if dry_run:
            out.update(dry_run=True, ok=not hard and (force or not mixed))
            return out
        if hard:
            code = [c for c in chk["codes"] if c != "mix"][0]
            save_record(want="kernel", why=why or "cli", reason=hard[0], code=code, pending=False)
            raise UnsupportedError(tr("gw_awgengine.err.cannot", why=" ".join(hard)), data={"kind": code})
        if mixed and not force:
            save_record(want="kernel", why=why or "cli", code="mix", pending=False,
                        reason=tr("gw_awgengine.reason.mix", what=_mix_short(chk["facts"])))
            raise ConflictError(tr("gw_awgengine.err.mix", mix=" ".join(mixed)), data={"kind": "mix"})
    elif dry_run:
        out.update(dry_run=True, check={"alive": len(alive_clients(runner, st))})
        return out
    # on the module already, but its module lacks the set and there is a line 3.1 (an inbound or a leg): rebuilt
    needs_set = target == "kernel" and was == "kernel" and not awgkmod.set_loaded() \
        and (any(r["line"] == "3.1" for r in inbounds_of(st, True))
             or any(r["line"] in awgkmod.LEG_LINES_SET for r in legs_of(st, _pool(runner, None), True)))
    if was == target and (target == "program" or awgkmod.enabled()) and not needs_set:
        save_record(want=target, why=why or "cli", reason=None, code=None, pending=False,
                    force=bool(force and target == "kernel"))
        out["unchanged"] = True
        return out
    out["backup"] = backup("%s → %s" % (was, target))
    say(tr("gw_awgengine.say.backup", path=out["backup"]))
    alive = alive_clients(runner, st) if verify else set()
    since = int(_now())
    try:
        res = _to_kernel(runner, say, force) if target == "kernel" else _to_program(runner, say)
    except KaskadError as e:
        if target == "kernel":
            save_record(want="kernel", why=why or "cli", reason=_short(e), code=_code_of(e), pending=False)
        raise
    out["warnings"] += list((res or {}).get("warnings") or [])
    if verify and not wait_clients(runner, alive, since, say):
        say(tr("gw_awgengine.say.rollback", minutes=WAIT_CLIENTS // 60))
        try:
            _to_program(runner, say) if target == "kernel" else _to_kernel(runner, say, True)
        except KaskadError as e:
            out["warnings"].append(tr("gw_awgengine.warn.no_way_back", why=e))
        reason = _rollback_reason(target, was, len(alive))
        save_record(want=was, why="rollback", reason=reason if target == "kernel" else None,
                    code="rollback" if target == "kernel" else None, pending=False)
        with util.journal_lang():
            util.log_action("GW AWG engine: %s" % _rollback_reason(target, was, len(alive)))
        out.update(ok=False, rolled_back=True, engine=was, reason=reason)
        return out
    save_record(want=target, why=why or "cli", reason=None, code=None, pending=False,
                force=bool(force and target == "kernel" and chk and chk.get("codes") and "mix" in chk["codes"]))
    with util.journal_lang():
        util.log_action("GW AWG engine: %s → %s (%s)" % (LOG_WORD.get(was, was), LOG_WORD.get(target, target),
                                                          why_text(why or "cli")))
    return out


def _rollback_reason(target, was, alive):
    return tr("gw_awgengine.reason.rollback", target=TITLE[target], alive=alive, minutes=WAIT_CLIENTS // 60,
              was=TITLE[was])


def _mix_short(facts):
    """What keeps the module away, in a few words: «вход 3.1 без набора исправлений модуля», «нога 3.1»."""
    out = []
    for r in facts.get("inbounds") or []:
        if not r["module"]:
            out.append(tr("gw_awgengine.short.inbound", line=r["line"]))
    for r in facts.get("legs") or []:
        if not r["module"]:
            line = r["line"] or tr("gw_awgengine.short.line_unknown")
            out.append(tr("gw_awgengine.short.leg_set" if r["line"] in awgkmod.LEG_LINES_SET
                          else "gw_awgengine.short.leg", line=line))
    return ", ".join(out) or tr("gw_awgengine.short.mix")


def _short(e):
    return util.redact(str(e))[:300]


# ── install, update, apply ─────────────────────────────────────────────────

def first(runner, choice=None, kind=None, progress=None, why=None):
    """The first AmneziaWG inbound or leg of a node (before its units are written): the module by default, the program
    on ``choice`` "program". An old node waiting for a person (pending) or one that chose the program is left alone; an
    inbound 3.1 (``kind`` awg3) keeps the whole node on the program — the module cannot take it yet. A failure — the
    program and the reason in words, never an exception. -> {"engine", "warnings"} or None (nothing done)."""
    if not AUTO:
        return None
    st = _load()
    rec = record(st)
    if choice == "program":
        save_record(want="program", why=why or "person:install", reason=None, code=None, pending=False)
        return {"engine": "program", "warnings": []}
    if choice != "kernel" and (rec["want"] == "program" or (rec["want"] is None and rec["pending"])):
        return None
    if awgkmod.enabled() and awgkmod.module_loaded():
        return None                                   # already on the module
    if kind is not None and kind not in awgkmod.kernel_kinds(True):
        reason = tr("gw_awgengine.reason.line", line=INBOUND_LINE.get(kind, kind))
        save_record(want=rec["want"] or "kernel", why=rec["why_code"] or why or "install:default", reason=reason,
                    code="mix", pending=False)
        return {"engine": "program", "warnings": ["AmneziaWG: %s" % reason]}
    if choice != "kernel" and rec["code"] in STICKY:
        return None                                   # it failed here before: a person decides (gw awg engine kernel)
    try:
        res = switch(runner, "kernel", why=why or "install:default", progress=progress, verify=False)
        return {"engine": res["engine"], "warnings": res.get("warnings") or []}
    except KaskadError as e:
        return {"engine": "program", "warnings": [tr("gw_awgengine.warn.first_failed", why=_short(e))]}


def offer(runner):
    """After an update without a person: never switch. An old node on the program that has not chosen gets
    ``pending`` (the faces offer the module) — or the reason the module cannot go here."""
    st = _load()
    rec = record(st)
    s = status(runner, st)
    if rec["want"] is not None or not s["has_awg"] or s["active"] == "kernel" or awgkmod.enabled():
        return {"ok": True, "pending": False, "status": s}
    chk = check(runner, st=st)
    hard = [(i["code"], i["text"]) for i in chk["items"] if i["code"] != "mix"]
    if hard:
        save_record(reason=hard[0][1], code=hard[0][0], pending=False)
        return {"ok": True, "pending": False, "reason": hard[0][1]}
    save_record(pending=True, reason=None, code=None)    # the old reason (another kernel) no longer holds
    return {"ok": True, "pending": True, "blocked": chk["blockers"]}


def reconcile(runner, st=None, pool=None):
    """After ``gw apply`` / an inbound installed: a node on the module got a line the module cannot take (an inbound
    3.1, a key 3.1 of an exit) — the whole node goes to the program, with the reason; ``force`` keeps the mix.
    Nothing runs while the node is on the program. -> {"switched", "reason"} or None."""
    if not AUTO or not awgkmod.enabled():
        return None
    st = _load() if st is None else st
    rec = record(st)
    mixed = mix(runner, st, pool)
    if not mixed or rec["force"]:
        return None
    facts = {"inbounds": inbounds_of(st), "legs": legs_of(st, _pool(runner, pool))}
    reason = tr("gw_awgengine.reason.mix", what=_mix_short(facts))
    try:
        _to_program(runner, lambda text: None)
    except KaskadError as e:
        return {"switched": False, "reason": tr("gw_awgengine.reason.mix_failed", reason=reason, why=e)}
    save_record(reason=reason, code="mix", pending=False)
    with util.journal_lang():
        util.log_action(tr("gw_awgengine.log.reconcile", reason=tr("gw_awgengine.reason.mix", what=_mix_short(facts))))
    return {"switched": True, "reason": reason, "mix": mixed}


# ── export / import ────────────────────────────────────────────────────────

def _awg_secret(key):
    return re.match(r"^(awg[123]\.server\.private|dev\.d-[0-9a-f]{8}\.awg[123]\.(private|psk))$", key) is not None


def export(path, st=None):
    """People, devices, the AmneziaWG inbounds with their keys and parameters -> ``path`` (0600, secrets inside)."""
    st = _load() if st is None else st
    g = model.peek(st)
    kinds = [k for k in awgkmod.AWG_KINDS if model.inbound(st, k) is not None]
    sec = secrets.load()
    doc = {"kind": EXPORT_KIND, "v": 1, "at": util.now_iso(),
           "net": g.get("net"), "host": g.get("host"),
           "inbounds": dict((k, model.inbound(st, k)) for k in kinds),
           "users": copy.deepcopy(g["users"]), "devices": copy.deepcopy(g["devices"]),
           "secrets": dict((k, v) for k, v in sec.items() if _awg_secret(k))}
    d = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(d):
        raise KaskadError(tr("gw_awgengine.export.no_dir", path=d))
    util.atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", 0o600)
    os.chmod(path, 0o600)
    return {"ok": True, "file": path, "inbounds": kinds, "users": len(doc["users"]), "devices": len(doc["devices"]),
            "secrets": len(doc["secrets"])}


def read_export(path, name=None):
    """The export file, checked: errors in words for people — never the text of a Python exception (T-N5 3.2.19, №8:
    «Expecting value: line 1 column 1 (char 0)»). ``name`` — how to call the file in them (the panel: a temporary
    file of its own — «Файл»)."""
    what = name or tr("gw_awgengine.import.file", path=path)
    if os.path.isdir(path):
        raise KaskadError(tr("gw_awgengine.import.dir", what=name or path))
    if not os.path.exists(path):
        raise KaskadError(tr("gw_awgengine.import.missing", path=path))
    raw = util.read_text(path)
    if raw is None:
        raise KaskadError(tr("gw_awgengine.import.unreadable", what=what))
    try:
        doc = json.loads(raw)
    except ValueError:
        raise KaskadError(tr("gw_awgengine.import.not_json", what=what))
    if not isinstance(doc, dict) or doc.get("kind") != EXPORT_KIND:
        raise KaskadError(tr("gw_awgengine.import.not_export", what=what))
    if doc.get("v") != 1:
        raise KaskadError(tr("gw_awgengine.import.version", what=what))
    for k in ("inbounds", "secrets", "users", "devices"):
        if not isinstance(doc.get(k), dict if k in ("inbounds", "secrets") else list):
            raise KaskadError(tr("gw_awgengine.import.partial", what=what, part=k))
    bad = [k for k in doc["inbounds"] if k not in awgkmod.AWG_KINDS]
    if bad:
        from . import registry
        raise KaskadError(tr("gw_awgengine.import.foreign_inbounds", what=what, kinds=", ".join(registry.titles(bad))))
    keys = [k for k in doc["secrets"] if not _awg_secret(k)]
    if keys:
        raise KaskadError(tr("gw_awgengine.import.foreign_secrets", what=what, n=len(keys)))
    return doc


def check_export(path, name=None):
    """``gw awg import --check``: the file is read and checked, nothing changes — the menu asks only after it.
    -> {"ok", "inbounds", "users", "devices"} (counts in the file)."""
    doc = read_export(path, name)
    return {"ok": True, "inbounds": sorted(doc["inbounds"]), "users": len(doc["users"]), "devices": len(doc["devices"])}


def import_(runner, path, name=None):
    """``gw awg import``: people and devices added (by id; an existing one gets the AmneziaWG accesses of the file),
    the AmneziaWG inbounds and their keys of the file take the place of this node's, then ``gw apply`` when the gateway
    is on. -> {"ok", "users", "devices", "inbounds", "warnings"}."""
    from . import ops
    doc = read_export(path, name)
    warns = []
    counts = {"users": 0, "devices": 0}

    def mutate(new, sec):
        g = model.gw(new)
        if doc.get("net") and g.get("net") and (doc["net"] or {}).get("base") != (g["net"] or {}).get("base"):
            warns.append(tr("gw_awgengine.import.net", file=(doc["net"] or {}).get("base"),
                            here=(g["net"] or {}).get("base")))
        uids = set(u.get("id") for u in g["users"])
        for u in doc["users"]:
            if isinstance(u, dict) and u.get("id") not in uids:
                g["users"].append(u)
                counts["users"] += 1
        byid = dict((d.get("id"), d) for d in g["devices"])
        for d in doc["devices"]:
            if not isinstance(d, dict):
                continue
            cur = byid.get(d.get("id"))
            if cur is None:
                g["devices"].append(d)
                counts["devices"] += 1
                continue
            acc = [k for k in cur.get("access") or [] if k not in awgkmod.AWG_KINDS]
            cur["access"] = acc + [k for k in d.get("access") or [] if k in awgkmod.AWG_KINDS]
            ips = dict(cur.get("ips") or {})
            ips.update((k, v) for k, v in (d.get("ips") or {}).items() if k in awgkmod.AWG_KINDS)
            cur["ips"] = ips
        for kind, rec in doc["inbounds"].items():
            if isinstance(rec, dict):
                model.set_inbound(new, kind, rec)
                model.forget_kept(new, kind)
        for k, v in doc["secrets"].items():
            sec.set(k, v)
        return True

    _, res = ops._change(runner, mutate)
    util.log_tr("gw_awgengine.log.import", path=path, inbounds=", ".join(sorted(doc["inbounds"])),
                users=counts["users"], devices=counts["devices"])
    for w in (res or {}).get("warnings") or []:
        warns.append(w)
    return {"ok": True, "inbounds": sorted(doc["inbounds"]), "users": counts["users"], "devices": counts["devices"],
            "warnings": warns}


# ── text for people ────────────────────────────────────────────────────────

def check_lines(chk):
    out = []
    if chk["can"] and not chk["blockers"]:
        out.append(tr("gw_awgengine.check.can"))
    elif chk["can"]:
        out.append(tr("gw_awgengine.check.mix"))
    else:
        out.append(tr("gw_awgengine.check.cannot"))
    out += ["  • %s" % b for b in chk["blockers"]]
    out += ["  ! %s" % w for w in chk["warnings"]]
    out += ["  + %s" % p for p in chk.get("pays") or []]
    f = chk.get("facts") or {}
    # «свободно 26267 МБ» после ОЗУ читалось как память (T-N5 3.2.19, №11): место — словами «на диске», в ГБ
    free = f.get("free_mb")
    out.append("  " + tr("gw_awgengine.check.facts", kernel=f.get("kernel") or "—", cpus=f.get("cpus") or "—",
                         mem=fmt_bytes(f.get("mem_total")),
                         free=fmt_bytes(free * 2 ** 20) if isinstance(free, int) and not isinstance(free, bool) else "—"))
    if chk["can"]:
        out.append("  %s" % chk["install"]["text"])
    return out
