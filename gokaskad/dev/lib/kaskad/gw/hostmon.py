"""The light health of the node (SPEC-node-score §2, §2.1, §3.2, §3.5; A2). ONLY pure functions: no command, no
file, no clock — the time comes as a parameter (``now`` — the wall, what goes to the files and the texts; ``mono`` —
the windows). The collector (A5) and the bot without the gateway (A6) call them; ``gw/hostprobe.py`` reads the raw
counters.

    derive(prev_raw, raw)                       -> sample   the percents and the rates between two raw (rule of deltas)
    evaluate(prev, sample, cfg, ctx, now, mono) -> (state, events)   windows + step() of every rule
    evaluate_instant(prev, raw, cfg, ctx, now, mono) -> (state, events)   the rules ``instant`` alone (bot, no gateway)
    step(rs, cur, rule, ctx, now, mono)         -> (rs, event | None)   the state machine of a rule and «push»
    light(state, lang=None)                     -> {"level", "top", "info", "checks", "alarms", "instant"}
    probes_view(state, cfg, mono)               -> ``health.probes`` of live.json (§3.6)
    health_in(health, lang)                     -> ``host.health`` with its texts in ``lang`` (by the msgs)
    instant(raw, cfg, ctx)                      -> state    one snapshot, the rules ``instant``, no automaton
    minute_row(state, mono) / ping_rows(state, mono) -> the rows of host_m1 / host_ping_m1 (§3.4)
    to_disk(state) / from_disk(doc, mono, now)  -> the part of hostmon.json (NS-D10)
    say(m, lang)                                -> the text of a msg of this module (numbers and times in ``lang``,
                                                   the forms of a number «1 раз / 2 раза / 5 раз», lists of names)
    why_msg(why, args)                          -> the msg of a reason of wait / na

``prev`` — the state of the last round (``{}`` at the start)::

    {"v": 1, "at": wall, "mono": mono, "enabled": bool, "instant": bool,
     "win": {metric: [[mono, value…]]}             rings no longer than their window
     "series": {addr: [[mono, sent, lost, rtt_med, excl, outage, runs, via, [rtt]]]}   ≤ 20 counted + ≤ 45 dropped / 15 min
     "pt_na": {addr: [mono, why]}                  the last series of the point could not be sent (no_route, blocked)
     "rules": {id: rs}                             §2.1 (+ the memory of the hysteresis: hl, hc, sub)
     "norm": {"rtt": {addr: [24 hourly medians]}, "hour": [wall, {addr: [rtt]}]},
     "disk": {mount: [[mono, used]]}, "mem_min": [[mono, avail_pct, swap_pct]], "oomsnap": {...}, "oomwin": [...],
     "rounds": [[mono, {...}]]                     the last minute of rounds, for ``minute_row``
     "last_min": {...}}                            the minute values (disk, inodes, clock, fd) for ``minute_row``

``cfg`` — ``model.hostmon(st)``: {"enabled", "probes", "anchors", "port_mbit"} (the port of the tariff, C1/C7). ``ctx`` — {"uptime", "virt", "cores",
"node_offline": bool, "services": {name: bool|None}, "inbounds_why": {kind: bool}, "probes": the result of a series of
``hostping`` (§3.3) | None, "probes_on": bool, "owner": "collector" | "bot", "push": gateway.notify.push,
"host_level": "crit" | "warn"}. The raw of the minute carries ``probe_mt`` — the mtime of probe-state.json (NS-D24,
revised 17.10: the probe writes it on every run, one killed by an OOM does not).
A raw field of a wrong type is taken as not read (``derive``, REV-ns-a12 П-5): one bad value never stops the round.

Texts: ``msg`` dicts ({"key", "args"}, args may be msgs); ``say`` renders them — a float in the reader's language
(«2,4» / «2.4»), an argument ``*_at`` as HH:MM of the server's clock. The collector (A5) gives ``say`` to ``localize``
and ``event_text`` for the kinds ``host_*``.
"""

import math
import statistics

V = 1
STEP = 10.0                 # s: the nominal round of the collector
COVER = 0.8                 # a window counts with ≥ 80 % of its points (else «ждёт истории»)
EVENT_QUIET = 15 * 60       # s = live.EVENT_QUIET (a test compares them): the antispam of the events (§3.5)
WAIT_MAX = 60 * 60          # s: «ждёт» longer than this with said warn/crit — a real na «history» (§2.1)
LOSS_SPAN = 15 * 60         # s: the series of the window of ``loss`` (§2)
LOSS_RUNS = 20              # the last counted series of a point
LOSS_MIN = 15               # a point counts with ≥ 15 of them
DROPPED_KEEP = 45           # the dropped series kept per point (all of 15 min: the rule «noisy»)
NORM_HOURS = 24             # hourly medians of the norm of the response (as gw/health.py norm_rtt)
NORM_MIN = 6                # a norm with ≥ 6 hours
OOM_SPAN = 30 * 60          # s: the sum of the OOM rules
OOM_FULL = OOM_SPAN // 60   # minute deltas of our own before an OOM alarm may go lower (a restart: REV-ns-a12 С-1)
PROBE_UNIT = "kaskad-gw-probe.service"      # NS-D24: the oneshot probe — gw/probe/unit.py (a test compares them)
PROBE_MAX = 96 * 1024 ** 2                  # its MemoryMax=96M
PROBE_GAP = 100.0           # s between two writes of probe-state.json: a run is missing (its timer: 60 s + ≤ 10 s)
PROBE_ALIVE = 600.0         # s: probe-state.json written this recently — the probe's timer is active
SUB_MAX = 16                # parts of a rule kept in hostmon.json (mounts, certificates)
MEM_MIN_SPAN = 5 * 60       # s: the minimum of MemAvailable before the minute of an OOM
ETA_SPAN = 60 * 60          # s: the slope of the used space
ETA_POINTS = 30
ETA_USED_PCT = 70
CLOCK_UPTIME = 17 * 60      # s: «не синхронизированы» only after this much uptime
GIB = 1024 ** 3
MIB = 1024 ** 2

# К2-3: the floor of the rule «Повторы TCP» in its window of 5 min. 10 000 segments ≈ 33/s ≈ 14 MB at the MSS — less
# is a quiet node, its share is the noise of a few dead connections; 500 resent ≈ 1.7/s — more than the ≤ 15 retries
# each of 2–3 connections of scanners make (tcp_retries2), while the loss of 2 % on a channel under load (≥ 25 000
# segments) gives that many anyway
RETRANS_MIN_OUT = 10000
RETRANS_MIN_BAD = 500
RANK = {"ok": 0, "warn": 1, "crit": 2}
REAL = ("ok", "warn", "crit")
TEMP_WHY = ("error", "no_file", "noisy", "no_route")         # a temporary na — for the automaton like «wait»
BAD = (ArithmeticError, LookupError, TypeError, ValueError, AttributeError, statistics.StatisticsError)
REAL_WHY = ("probes_off", "container", "no_gateway", "off", "no_psi", "no_conntrack", "no_swap", "history",
            "blocked", "no_inodes", "no_cert", "no_port")
CONTAINERS = ("lxc", "lxc-libvirt", "openvz", "docker", "podman", "systemd-nspawn", "wsl", "container-other")


def _rule(rid, group, metric, window_s=0, warn=None, crit=None, crit_draft=None, alarm=None, hold=1, min_n=0,
          needs=None, draft=False, instant=False, every="round", unit="pct", down=False, ge=False, min_bad=0):
    return {"id": rid, "group": group, "metric": metric, "window_s": window_s, "cover": COVER, "warn": warn,
            "crit": crit, "crit_draft": crit_draft, "alarm": alarm, "hold": hold, "min_n": min_n, "needs": needs,
            "draft": draft, "instant": instant, "every": every, "unit": unit, "down": down, "ge": ge,
            "min_bad": min_bad}


# One line — one row of SPEC §2 (data, not code). ``crit`` None and ``crit_draft`` set: the threshold has no source,
# it waits for the calibration of stage B and does not act (NS-D11, §10.1 п. 5, 17, 18). ``alarm`` — the level from
# which the rule goes to the problems and to the push by default (NS-D4, NS-D5). The order is the order of «top».
HOST_RULES = (
    _rule("cpu", "cpu", "busy", 600, warn=[85, 75], crit=[95, 85], alarm="crit"),
    _rule("steal", "steal", "steal", 1200, warn=[10, 5], crit_draft=[25, 15], alarm="warn", needs="vm"),
    _rule("iowait", "io", "iowait", 600, warn=[40, 20], alarm="warn"),
    _rule("load", "cpu", "load_core", warn=[2.0, 1.75], hold=3, unit="per_core"),
    _rule("psi_cpu", "cpu", "psi_cpu", 180, warn=[20, 10], crit_draft=[50, 30], draft=True, needs="psi"),
    _rule("psi_mem", "mem", "psi_mem", 180, warn=[20, 10], crit_draft=[50, 30], draft=True, needs="psi"),
    _rule("psi_io", "io", "psi_io", 180, warn=[20, 10], crit_draft=[50, 30], draft=True, needs="psi"),
    _rule("mem", "mem", "mem_pct", warn=[10, 15], crit_draft=[5, 8], alarm="warn", hold=3, instant=True, down=True),
    _rule("oom", "mem", "oom", OOM_SPAN, warn=[0, 0], crit=[0, 0], alarm="crit", every="minute", unit="count"),
    _rule("oom_svc", "mem", "oom_svc", OOM_SPAN, warn=[0, 0], every="minute", unit="count"),
    _rule("swap", "mem", "swap_pct", warn=[90, 85], crit=[98, 95], alarm="crit", hold=3, instant=True),
    _rule("disk", "disk", "disk", warn=[90, 85], crit=[98, 95], alarm="crit", instant=True, every="minute"),
    _rule("disk_eta", "disk", "disk_eta", ETA_SPAN, warn=[8, 12], crit=[2, 4], alarm="crit", every="minute",
          unit="h", down=True),
    _rule("inodes", "disk", "inodes", warn=[90, 85], crit=[98, 95], alarm="crit", instant=True, every="minute"),
    # C7 (NS-D6): the channel near the port of the TARIFF (C1: typed in or a catalog; ethtool and /sys speed never —
    # vmxnet3 says 10G, virtio nothing); RS §5.2: 1 min, > 90 % → < 85 %, Netdata net.conf:27, :43 (no crit there)
    _rule("port", "net", "port", 60, warn=[90, 85]),
    _rule("drops", "net", "drops", 600, warn=[2, 1], min_n=10000, ge=True),
    _rule("nic_err", "net", "nic_err", 120, warn=[1, 0.5]),
    _rule("softnet", "net", "softnet", 60, warn=[10, 5], unit="per_s"),
    # К2-3 (T-KT2): on a quiet node 2–3 connections of scanners (FIN-WAIT, 90 % of their bytes resent) gave 7–8 % — the
    # share means something only with traffic: ≥ RETRANS_MIN_OUT segments sent and ≥ RETRANS_MIN_BAD resent in 5 min
    # (SYN/SYN-ACK resent not counted: hostprobe ``syn_retrans``); still a draft (warn, no push) till the calibration
    _rule("retrans", "net", "retrans", 300, warn=[2, 1.5], crit_draft=[5, 4], draft=True, min_n=RETRANS_MIN_OUT,
          min_bad=RETRANS_MIN_BAD),
    _rule("listen", "net", "listen", 60, warn=[1, 0.5], crit=[5, 2], alarm="crit", unit="per_s"),
    _rule("conntrack", "conntrack", "conntrack_pct", warn=[90, 85], crit=[95, 90], alarm="crit", hold=3,
          instant=True, needs="conntrack"),
    _rule("fd", "conntrack", "fd", warn=[70, 65], crit=[90, 85], alarm="crit", hold=3, instant=True, every="minute"),
    _rule("clock", "clock", "clock", warn=[0.05, 0.03], crit_draft=[1.0, 1.0], alarm="warn", hold=3, instant=True,
          every="minute", unit="s"),
    _rule("cert", "cert", "cert", warn=[14, 14], crit=[7, 7], alarm="crit", instant=True, every="hour",
          unit="days", down=True),
    _rule("loss", "loss", "loss", LOSS_SPAN, warn=[2, 1], crit_draft=[10, 5], alarm="warn", needs="probes"),
    _rule("rtt", "loss", "rtt", LOSS_SPAN, warn=[3, 2], unit="x", needs="probes"),
    _rule("anchor", None, "anchor", LOSS_SPAN, warn=[10, 5], needs="probes"),
    _rule("services", None, "services", crit=[1, 1], alarm="crit", instant=True, unit="count"),
)
RULES = dict((r["id"], r) for r in HOST_RULES)
HOST_GROUPS = {}
for _r in HOST_RULES:
    if _r["group"]:
        HOST_GROUPS.setdefault(_r["group"], []).append(_r["id"])
HOST_KINDS = tuple("host_" + g for g in ("cpu", "steal", "io", "mem", "disk", "net", "conntrack", "clock", "cert",
                                         "loss"))
# the windows of the round metrics (s): what each ring keeps
WIN_KEEP = {"busy": 600, "steal": 1200, "iowait": 600, "psi_cpu": 180, "psi_mem": 180, "psi_io": 180, "drops": 600,
            "nic_err": 120, "softnet": 60, "retrans": 300, "listen": 60, "port": 60}
NO_PROBLEM = ("services",)  # NS-D4: their problems are there already (and the site's certificate, by its finding)
# the codes of ipcert.store.last_try.reason (ipcert/ops.py, acme.py, http01.py) said as words; another code — no reason
CERT_REASONS = ("limit", "challenge", "net", "nonce", "server", "unsupported", "profile", "private", "openssl",
                "port80")
SERVICES = ("dns", "xray")  # live.json gateway.services: the names said by gw_host.svc.<name>
# the texts the readers (A5–A8) say by themselves: the light of the tile, the hint of the bot without the gateway
READER_KEYS = ("gw_host.light.ok", "gw_host.light.warn", "gw_host.light.crit", "gw_host.light.wait",
               "gw_host.light.off", "gw_host.light.instant", "gw_host.hint.bot_python")
# the type of each raw field (SPEC §3.1); another type — the field is not read this round
RAW_TYPES = {"cpu": list, "load": list, "mem": dict, "psi": dict, "net": dict, "softnet": dict, "tcp": dict,
             "listen": dict, "conntrack": dict, "fd": dict, "fs": list, "clock": dict, "cg": dict, "fd_proc": list,
             "certs": list, "ifaces": list, "absent": list, "virt": str}
RAW_NUMS = ("mono", "cores", "uptime", "oom_kill", "probe_mt")


def msg(key, **args):
    return {"key": key, "args": args}


def kind_of(rule):
    return ("host_" + rule["group"]) if rule.get("group") else None


# ── numbers and deltas ─────────────────────────────────────────────────────

def _num(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return x if math.isfinite(x) else None


def _d(a, b):
    """The growth of a counter: a decrease (a restart, a wrap) is no point — None, never the whole counter."""
    a, b = _num(a), _num(b)
    if a is None or b is None or b < a:
        return None
    return b - a


def _pct(a, b):
    a, b = _num(a), _num(b)
    if a is None or not b:
        return None
    return max(0.0, min(100.0, 100.0 * a / b))


def _r1(x):
    return None if x is None else round(x, 1)


def _r2(x):
    return None if x is None else round(x, 2)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _typed(raw):
    """``raw`` with every field of a wrong type as not read (None); lists keep their items of the right type."""
    raw = dict(raw) if isinstance(raw, dict) else {}
    for k, t in RAW_TYPES.items():
        if raw.get(k) is not None and not isinstance(raw[k], t):
            raw[k] = None
    for k in RAW_NUMS:
        if raw.get(k) is not None and _num(raw[k]) is None:
            raw[k] = None
    for k in ("ifaces", "absent"):
        if raw.get(k) is not None:
            raw[k] = [x for x in raw[k] if isinstance(x, str)]
    for k in ("fs", "certs", "fd_proc"):
        if raw.get(k) is not None:
            raw[k] = [x for x in raw[k] if isinstance(x, dict)]
    return raw


def derive(prev_raw, raw):
    """A sample of the round from two raw (SPEC §3.2, the rule of deltas, REV С-4): the CPU fields — Δ of each field,
    a negative Δ is 0 (iowait «may decrease»), Δtotal ≤ 0 — no CPU point, the percents clipped to 0–100; the other
    counters — a decrease is no point (None), an interface not in the previous raw — None. ``metrics.delta`` is not
    used (it gives the whole counter after a decrease)."""
    raw = _typed(raw)
    p = _typed(prev_raw)
    s = {"mono": _num(raw.get("mono")), "cores": raw.get("cores"), "uptime": raw.get("uptime"),
         "virt": raw.get("virt"), "absent": list(raw.get("absent") or []), "minute": bool(raw.get("minute")),
         "hour": bool(raw.get("hour"))}
    for k in ("fs", "clock", "cg", "fd_proc", "certs", "oom_kill", "psi", "probe_mt"):
        s[k] = raw.get(k)
    dt = None
    if s["mono"] is not None and _num(p.get("mono")) is not None and s["mono"] > p["mono"]:
        dt = s["mono"] - p["mono"]
    s["dt"] = dt
    # CPU
    for k in ("busy", "steal", "iowait", "softirq"):
        s[k] = None
    c1, c0 = raw.get("cpu"), p.get("cpu")
    if isinstance(c1, list) and isinstance(c0, list) and len(c1) >= 8 and len(c0) >= 8:
        try:
            d = [max(0, int(c1[i]) - int(c0[i])) for i in range(8)]
        except (TypeError, ValueError):
            d = None
        if d is not None and sum(d) > 0:
            tot = float(sum(d))
            user, nice, system, _idle, iowait, irq, softirq, steal = d
            s["busy"] = _pct(user + nice + system + irq + softirq, tot)
            s["steal"], s["iowait"], s["softirq"] = _pct(steal, tot), _pct(iowait, tot), _pct(softirq, tot)
    load = raw.get("load")
    s["load_core"] = (load[2] / raw["cores"]) if isinstance(load, list) and len(load) >= 3 and \
        _num(load[2]) is not None and raw.get("cores") else None
    # memory
    mem = raw.get("mem") if isinstance(raw.get("mem"), dict) else None
    s["mem_pct"] = _pct(mem.get("avail"), mem.get("total")) if mem else None
    s["mem_avail"] = mem.get("avail") if mem else None
    s["swap_total"] = mem.get("swap_total") if mem else None
    s["swap_pct"] = None
    if mem and mem.get("swap_total"):
        s["swap_pct"] = _pct(mem["swap_total"] - (mem.get("swap_free") or 0), mem["swap_total"])
    psi = raw.get("psi") if isinstance(raw.get("psi"), dict) else {}
    s["psi_cpu"], s["psi_mem"], s["psi_io"] = psi.get("cpu"), psi.get("memory"), psi.get("io")
    # network: the interfaces of the default route, summed; one missing before — no point
    for k in ("rx", "tx", "pkts", "drops", "errs", "rx_rate", "tx_rate"):
        s[k] = None
    n1, n0, ifs = raw.get("net"), p.get("net"), raw.get("ifaces")
    if isinstance(n1, dict) and isinstance(n0, dict) and ifs:
        tot = {"rx": 0, "tx": 0, "pkts": 0, "drops": 0, "errs": 0}
        ok = True
        for name in ifs:
            a, b = n0.get(name), n1.get(name)
            if not isinstance(a, dict) or not isinstance(b, dict):
                ok = False
                break
            parts = {"rx": _d(a.get("rx_bytes"), b.get("rx_bytes")), "tx": _d(a.get("tx_bytes"), b.get("tx_bytes")),
                     "rxp": _d(a.get("rx_packets"), b.get("rx_packets")),
                     "txp": _d(a.get("tx_packets"), b.get("tx_packets")),
                     "rxd": _d(a.get("rx_drop"), b.get("rx_drop")), "txd": _d(a.get("tx_drop"), b.get("tx_drop")),
                     "rxe": _d(a.get("rx_errs"), b.get("rx_errs")), "txe": _d(a.get("tx_errs"), b.get("tx_errs"))}
            if any(v is None for v in parts.values()):
                ok = False
                break
            tot["rx"] += parts["rx"]
            tot["tx"] += parts["tx"]
            tot["pkts"] += parts["rxp"] + parts["txp"]
            tot["drops"] += parts["rxd"] + parts["txd"]
            tot["errs"] += parts["rxe"] + parts["txe"]
        if ok:
            s.update(tot)
            if dt:
                s["rx_rate"], s["tx_rate"] = tot["rx"] / dt, tot["tx"] / dt
    sn1, sn0 = raw.get("softnet"), p.get("softnet")
    s["softnet_n"] = None
    if isinstance(sn1, dict) and isinstance(sn0, dict):
        a, b = _d(sn0.get("dropped"), sn1.get("dropped")), _d(sn0.get("squeezed"), sn1.get("squeezed"))
        s["softnet_n"] = None if a is None or b is None else a + b
    s["softnet"] = (s["softnet_n"] / dt) if s["softnet_n"] is not None and dt else None
    t1, t0 = raw.get("tcp"), p.get("tcp")
    s["retrans"] = _d((t0 or {}).get("retrans"), (t1 or {}).get("retrans")) if t1 and t0 else None
    syn = _d((t0 or {}).get("syn_retrans"), (t1 or {}).get("syn_retrans")) if t1 and t0 else None
    if s["retrans"] is not None and syn is not None:
        s["retrans"] = max(0, s["retrans"] - syn)       # К2-3: SYN/SYN-ACK resent — scanners, dead peers, not loss
    s["out_segs"] = _d((t0 or {}).get("out_segs"), (t1 or {}).get("out_segs")) if t1 and t0 else None
    l1, l0 = raw.get("listen"), p.get("listen")
    s["listen_n"] = None
    if isinstance(l1, dict) and isinstance(l0, dict):
        s["listen_n"] = _d(l0.get("drops"), l1.get("drops"))   # NS-D23: ListenDrops has the overflows already
    s["listen"] = (s["listen_n"] / dt) if s["listen_n"] is not None and dt else None
    ct = raw.get("conntrack") if isinstance(raw.get("conntrack"), dict) else None
    s["conntrack_pct"] = _pct(ct.get("count"), ct.get("max")) if ct else None
    fd = raw.get("fd") if isinstance(raw.get("fd"), dict) else None
    s["fd_sys"] = _pct(fd.get("alloc"), fd.get("max")) if fd else None
    return s


# ── the state ──────────────────────────────────────────────────────────────

def _start(prev):
    prev = prev if isinstance(prev, dict) else {}
    st = {"v": V}
    st["win"] = dict((k, list(v)) for k, v in (prev.get("win") or {}).items())
    st["series"] = dict((k, list(v)) for k, v in (prev.get("series") or {}).items())
    st["pt_na"] = dict(prev.get("pt_na") or {})
    st["rules"] = dict((k, dict(v)) for k, v in (prev.get("rules") or {}).items())
    norm = prev.get("norm") or {}
    st["norm"] = {"rtt": dict((k, list(v)) for k, v in (norm.get("rtt") or {}).items()),
                  "hour": norm.get("hour")}
    st["disk"] = dict((k, list(v)) for k, v in (prev.get("disk") or {}).items())
    st["mem_min"] = list(prev.get("mem_min") or [])
    st["oomsnap"] = prev.get("oomsnap")
    st["oomwin"] = list(prev.get("oomwin") or [])
    st["oom_n"] = int(prev.get("oom_n") or 0)              # minute deltas of the OOM counters taken so far
    st["anchors"] = list(prev.get("anchors") or [])
    st["rounds"] = list(prev.get("rounds") or [])
    st["last_min"] = dict(prev.get("last_min") or {})
    st["anchor_on"] = list(prev.get("anchor_on") or [])
    return st


def _trim(ring, mono, span):
    return [x for x in ring if mono - span < x[0] <= mono]


def _feed(st, s, mono):
    """The points of this round into the rings."""
    win = st["win"]

    def put(name, *vals):
        ring = win.get(name) or []
        if all(v is not None for v in vals):
            ring.append([mono] + list(vals))
        win[name] = _trim(ring, mono, WIN_KEEP[name])
    put("busy", s.get("busy"))
    put("steal", s.get("steal"))
    put("iowait", s.get("iowait"))
    put("psi_cpu", s.get("psi_cpu"))
    put("psi_mem", s.get("psi_mem"))
    put("psi_io", s.get("psi_io"))
    put("drops", s.get("drops"), s.get("pkts"))
    put("nic_err", s.get("errs"), s.get("pkts"))
    put("softnet", s.get("softnet"))
    put("retrans", s.get("retrans"), s.get("out_segs"))
    put("listen", s.get("listen"))
    put("port", s.get("rx_rate"), s.get("tx_rate"))
    mm = st["mem_min"]
    if s.get("mem_pct") is not None:
        mm.append([mono, s["mem_pct"], s.get("swap_pct")])
    st["mem_min"] = _trim(mm, mono, MEM_MIN_SPAN + 60)


def _window(st, name, mono, span):
    """(points of the window, their share of the expected ones): the expected number is span / step, the step — the
    median gap of the points (≥ STEP: the collector's round is its own time + the pause of 10 s)."""
    pts = [x for x in st["win"].get(name) or [] if mono - span < x[0] <= mono]
    if not pts:
        return pts, 0.0
    gaps = [b[0] - a[0] for a, b in zip(pts, pts[1:]) if b[0] > a[0]]
    step = max(STEP, statistics.median(gaps)) if gaps else STEP
    return pts, len(pts) / (span / step)


def _wait(have_s=None, need_s=None):
    out = {"level": "wait", "why": "wait"}
    if need_s:
        out["args"] = {"have": int((have_s or 0) // 60), "need": int(need_s // 60)}
    return out


def _na(why):
    return {"level": "na", "why": why}


# ── the hysteresis and «hold» ──────────────────────────────────────────────

def _hyst(prev, w_on, w_back, c_on, c_back):
    """The level by the thresholds «вкл» / «выкл» (as Netdata): it comes when the value passed «вкл», it goes only
    when the value is back past «выкл»."""
    if c_on or (prev == "crit" and not c_back):
        return "crit"
    if w_on or (prev in ("warn", "crit") and not w_back):
        return "warn"
    return "ok"


def _cmp(rule, v, th, on):
    """v beyond the threshold ``th`` («вкл» when ``on``, else «not yet back past выкл» is ``not _cmp(..., off)``)."""
    if rule["down"]:
        return v < th if on else v > th
    if on and rule["ge"]:
        return v >= th
    return v > th if on else v < th


def _scalar_preds(rule, v):
    w, c = rule["warn"], rule["crit"]
    w_on = bool(w) and _cmp(rule, v, w[0], True)
    w_back = (not w) or _cmp(rule, v, w[1], False)
    c_on = bool(c) and _cmp(rule, v, c[0], True)
    c_back = (not c) or _cmp(rule, v, c[1], False)
    return w_on, w_back, c_on, c_back


def _confirm(rs, rule, cand):
    """«hold»: a higher level needs ``hold`` evaluations in a row; a lower one comes at once."""
    hl = rs["hl"] = rs.get("hl") or "ok"
    if RANK[cand] > RANK[hl]:
        rs["hc"] = int(rs.get("hc") or 0) + 1
        if rs["hc"] >= rule["hold"]:
            rs["hl"], rs["hc"] = cand, 0
    else:
        rs["hl"], rs["hc"] = cand, 0
    return rs["hl"]


def _level(rs, rule, preds):
    cand = _hyst(rs.get("hl") or "ok", *preds)
    return _confirm(rs, rule, cand)


def _sub_level(rs, key, preds):
    """The hysteresis of one part of a rule (a mount, a certificate, a point) — no «hold». A part not seen yet starts
    from ``sub_start``: the level held before a restart or a new owner (REV-ns-a12 З-1: a disk cleaned from 99 % to
    96 % stays crit through a restart), else ok (a mount new in this run)."""
    sub = rs.setdefault("sub", {})
    sub[key] = _hyst(sub.get(key) or rs.get("sub_start") or "ok", *preds)
    return sub[key]


def _find(rid, level, variant="", **args):
    return msg("gw_host.find.%s.%s%s" % (rid, level, variant), **args)


def _real(rs, rule, level, value=None, find=None, advice=None, **extra):
    out = {"level": level, "value": value, "find": find if level in ("warn", "crit") else None,
           "advice": advice if level in ("warn", "crit") else None}
    out.update(extra)
    return out


# ── the rules ──────────────────────────────────────────────────────────────

def _mean_rule(rule, st, rs, mono, metric=None, min_of=False, extra=None):
    span = rule["window_s"]
    pts, share = _window(st, metric or rule["metric"], mono, span)
    if share < rule["cover"]:
        have = (pts[-1][0] - pts[0][0] + STEP) if pts else 0
        return _wait(have, span)
    vals = [p[1] for p in pts]
    v = min(vals) if min_of else sum(vals) / len(vals)
    lvl = _level(rs, rule, _scalar_preds(rule, v))
    args = dict(pct=int(round(v)), min=int(span // 60))
    if extra:
        args.update(extra)
    return _real(rs, rule, lvl, _r1(v), _find(rule["id"], lvl, **args), msg("gw_host.advice." + rule["id"]))


def _ev_cpu(rule, st, s, rs, cfg, ctx, now, mono):
    out = _mean_rule(rule, st, rs, mono)
    if out.get("find"):
        pts, _share = _window(st, "steal", mono, rule["window_s"])
        steal = _mean([p[1] for p in pts])
        if steal is not None and steal > 5:
            out["find"] = _find("cpu", out["level"], "_steal", steal=int(round(steal)), **out["find"]["args"])
    return out


def _ev_steal(rule, st, s, rs, cfg, ctx, now, mono):
    if (s.get("virt") or ctx.get("virt")) in CONTAINERS:
        return _na("container")
    return _mean_rule(rule, st, rs, mono)


def _ev_mean(rule, st, s, rs, cfg, ctx, now, mono):
    return _mean_rule(rule, st, rs, mono)


def _ev_psi(rule, st, s, rs, cfg, ctx, now, mono):
    if "psi" in (s.get("absent") or []):
        return _na("no_psi")
    if s.get(rule["metric"]) is None:
        return _na("no_file")
    return _mean_rule(rule, st, rs, mono, min_of=True)      # «все точки выше порога»: the least of the window


def _ev_load(rule, st, s, rs, cfg, ctx, now, mono):
    v = s.get("load_core")
    if v is None:
        return _na("no_file")
    lvl = _level(rs, rule, _scalar_preds(rule, v))
    return _real(rs, rule, lvl, _r2(v), _find("load", lvl, per_core=_r1(v)), msg("gw_host.advice.load"))


def _ev_mem(rule, st, s, rs, cfg, ctx, now, mono):
    v = s.get("mem_pct")
    if v is None:
        return _na("no_file")
    lvl = _level(rs, rule, _scalar_preds(rule, v))
    mb = int((s.get("mem_avail") or 0) // MIB)
    return _real(rs, rule, lvl, _r1(v), _find("mem", lvl, pct=int(round(v)), mb=mb), msg("gw_host.advice.mem"))


def _ev_swap(rule, st, s, rs, cfg, ctx, now, mono):
    if s.get("swap_total") == 0:
        return _na("no_swap")
    v = s.get("swap_pct")
    if v is None:
        return _na("no_file")
    lvl = _level(rs, rule, _scalar_preds(rule, v))
    return _real(rs, rule, lvl, _r1(v), _find("swap", lvl, pct=int(round(v))), msg("gw_host.advice.mem"))


def _ev_conntrack(rule, st, s, rs, cfg, ctx, now, mono):
    if "conntrack" in (s.get("absent") or []):
        return _na("no_conntrack")
    v = s.get("conntrack_pct")
    if v is None:
        return _na("no_file")
    lvl = _level(rs, rule, _scalar_preds(rule, v))
    return _real(rs, rule, lvl, _r1(v), _find("conntrack", lvl, pct=int(round(v))), msg("gw_host.advice.conntrack"))


def _ev_fd(rule, st, s, rs, cfg, ctx, now, mono):
    if not s.get("minute"):
        return None
    best, who = s.get("fd_sys"), None
    for p in s.get("fd_proc") or []:
        v = _pct(p.get("open"), p.get("soft"))
        if v is not None and (best is None or v > best):
            best, who = v, p.get("who")
    if best is None:
        return _na("no_file")
    lvl = _level(rs, rule, _scalar_preds(rule, best))
    who_m = who if who else msg("gw_host.who.system")
    return _real(rs, rule, lvl, _r1(best), _find("fd", lvl, pct=int(round(best)), who=who_m),
                 msg("gw_host.advice.fd"))


def _ev_clock(rule, st, s, rs, cfg, ctx, now, mono):
    if not s.get("minute"):
        return None
    c = s.get("clock")
    if not isinstance(c, dict) or c.get("synced") is None:
        return _na("no_file")
    unsynced = c.get("synced") is False
    off = abs(c["offset_s"]) if _num(c.get("offset_s")) is not None else 0.0
    up = s.get("uptime") if s.get("uptime") is not None else ctx.get("uptime")
    if unsynced and (up is None or up < CLOCK_UPTIME):
        return {"level": "wait", "why": "boot"}
    w_on = unsynced or off > rule["warn"][0]
    w_back = not unsynced and off < rule["warn"][1]
    lvl = _level(rs, rule, (w_on, w_back, False, True))
    find = _find("clock", lvl) if unsynced else _find("clock", lvl, "_offset", sec=_r2(off))
    return _real(rs, rule, lvl, _r2(off), find, msg("gw_host.advice.clock"), synced=not unsynced)


def _fs_parts(s):
    out = []
    for f in s.get("fs") or []:
        if not isinstance(f, dict):
            continue
        size, free, avail = _num(f.get("size")), _num(f.get("free")), _num(f.get("avail"))
        if not size or free is None or avail is None:
            continue
        used = max(0, size - free)
        pct = 100.0 * used / (used + avail) if used + avail > 0 else 0.0
        out.append((f.get("mount"), used, pct, avail, f.get("files"), f.get("ffree")))
    return out


def _worst(items):
    """The worst (level, …) by RANK; the first one of the equals."""
    best = None
    for it in items:
        if best is None or RANK[it[0]] > RANK[best[0]]:
            best = it
    return best


def _ev_disk(rule, st, s, rs, cfg, ctx, now, mono):
    if not s.get("minute"):
        return None
    parts = _fs_parts(s)
    if not parts:
        return _na("no_file")
    got = []
    for mount, used, pct, avail, _f, _ff in parts:
        preds = (pct > 90 and avail < 5 * GIB, pct < 85 or avail >= 6 * GIB,
                 pct > 98 or avail < 0.5 * GIB, pct < 95 and avail >= 1 * GIB)
        got.append((_sub_level(rs, mount, preds), mount, pct, avail))
    lvl, mount, pct, avail = _worst(got)
    rs["hl"] = lvl
    return _real(rs, rule, lvl, _r1(pct), _find("disk", lvl, mount=mount, gb=_r1(avail / GIB), pct=int(round(pct))),
                 msg("gw_host.advice.disk"), mount=mount)


def _ev_inodes(rule, st, s, rs, cfg, ctx, now, mono):
    if not s.get("minute"):
        return None
    got = []
    for mount, _u, _p, _a, files, ffree in _fs_parts(s):
        if not files or ffree is None:
            continue
        pct = 100.0 * (files - ffree) / files
        got.append((_sub_level(rs, mount, _scalar_preds(rule, pct)), mount, pct))
    if not got:
        return _na("no_inodes") if _fs_parts(s) else _na("no_file")
    lvl, mount, pct = _worst(got)
    rs["hl"] = lvl
    return _real(rs, rule, lvl, _r1(pct), _find("inodes", lvl, mount=mount, pct=int(round(pct))),
                 msg("gw_host.advice.inodes"), mount=mount)


def _slope(pts):
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    den = sum((p[0] - mx) ** 2 for p in pts)
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / den if den else 0.0


def _ev_disk_eta(rule, st, s, rs, cfg, ctx, now, mono):
    """The forecast of the space: the slope of the used space over the last hour (≥ 30 minute points, growth in
    ≥ 50 % of the steps), only while more than 70 % is used. No steady growth (a rotated journal) — no forecast, ok."""
    if not s.get("minute"):
        return None
    parts = _fs_parts(s)
    if not parts:
        return _na("no_file")
    got, waiting = [], False
    for mount, used, pct, avail, _f, _ff in parts:
        ring = _trim((st["disk"].get(mount) or []) + [[mono, used]], mono, ETA_SPAN)
        st["disk"][mount] = ring
        if pct <= ETA_USED_PCT:
            got.append((_sub_level(rs, mount, (False, True, False, True)), mount, None))
            continue
        if len(ring) < ETA_POINTS:
            waiting = True
            continue
        ups = sum(1 for a, b in zip(ring, ring[1:]) if b[1] > a[1])
        slope = _slope(ring)
        if ups < 0.5 * (len(ring) - 1) or slope <= 0:
            got.append((_sub_level(rs, mount, (False, True, False, True)), mount, None))
            continue
        eta_h = avail / slope / 3600.0
        got.append((_sub_level(rs, mount, _scalar_preds(rule, eta_h)), mount, eta_h))
    for m in list(st["disk"]):
        if m not in [p[0] for p in parts]:
            st["disk"].pop(m, None)
    real = [g for g in got if g[0] != "ok"]
    if not real and waiting:
        return _wait(None, None)
    if not got:
        return _wait(None, None)
    lvl, mount, eta_h = _worst(got)
    rs["hl"] = lvl
    find = _find("disk_eta", lvl, mount=mount, h=int(math.ceil(eta_h))) if eta_h is not None else None
    return _real(rs, rule, lvl, _r1(eta_h), find, msg("gw_host.advice.disk"), mount=mount)


def _oom_minute(st, s, ctx, mono):
    """Once a minute: the deltas of oom_kill and of the cgroups between the SAME two minute reads (REV-3 П-1), into
    the window of 30 min — [mono, node, group, svc, unit, likely, max] (NS-D17, §2 «oom», «oom_svc»):
    K — Δ oom_kill of the node; L — Σ min(Δ oom_kill, Δ oom) of the slices (a global kill raises oom_kill of a slice
    without its oom — not in L); R = max(0, K − L) — a kill of the node when the memory was low (MemAvailable < 10 %
    or swap > 90 % within 5 min before), else of the limit of a group; L outside system.slice — a limit of a group
    too; Δ oom of system.slice — a service at its MemoryMax (named by its own file, or «likely» by its cgroup gone).
    cgroup v1 or memory_localevents — no subtraction: the memory alone decides, a service only by its own file.
    NS-D24 (revised 17.10): a service at its limit with no name of its own, while the probe is alive (probe-state.json
    written within PROBE_ALIVE) and missed a run around this minute (the file not written since the last minute read,
    or two writes ≥ PROBE_GAP apart) — «likely the probe of Kaskad» (the oneshot probe has no lasting cgroup)."""
    if not s.get("minute") or s.get("oom_kill") is None:
        return
    pmt = _num(s.get("probe_mt"))
    snap = {"mono": mono, "kill": s["oom_kill"], "cg": s.get("cg"), "pmt": pmt}
    prev = st.get("oomsnap")
    st["oomsnap"] = snap
    if not prev:
        return
    K = _d(prev.get("kill"), snap["kill"])
    if K is None:
        return
    cg, cg0 = snap["cg"], prev.get("cg")
    both = isinstance(cg, dict) and isinstance(cg0, dict)
    sub = both and not cg.get("local") and cg.get("v") == 2
    L = L_out = svc = 0
    if sub:
        for name, ev in (cg.get("slices") or {}).items():
            ev0 = (cg0.get("slices") or {}).get(name)
            if not isinstance(ev, dict) or not isinstance(ev0, dict):
                continue
            dk, do = _d(ev0.get("oom_kill"), ev.get("oom_kill")), _d(ev0.get("oom"), ev.get("oom"))
            if dk is None or do is None:
                continue
            L += min(dk, do)
            if name == "system.slice":
                svc = do
            else:
                L_out += min(dk, do)
    named = gone = None
    if both:
        units, units0 = cg.get("units") or {}, cg0.get("units") or {}
        for name in sorted(units0):
            ev0, ev = units0.get(name), units.get(name)
            if not isinstance(ev0, dict):
                continue
            if isinstance(ev, dict) and _d(ev0.get("oom"), ev.get("oom")):
                named = (name, ev.get("max"), _d(ev0.get("oom"), ev.get("oom")))
                break
            if gone is None and (ev is None or (isinstance(ev, dict) and _num(ev.get("oom")) is not None
                                                and ev["oom"] < (ev0.get("oom") or 0))):
                gone = (name, ev0.get("max"))
    if not sub and named:
        svc = named[2]
    unit = likely = mx = None
    if svc:
        if named:
            unit, likely, mx = named[0], False, named[1]
        elif gone:
            unit, likely, mx = gone[0], True, gone[1]
        elif _probe_missed(pmt, _num(prev.get("pmt")), _num(st.get("at"))):
            unit, likely, mx = PROBE_UNIT, "probe", PROBE_MAX           # NS-D24: by the coincidence
    R = max(0, K - L)
    mm = [x for x in st["mem_min"] if mono - MEM_MIN_SPAN - 60 < x[0] <= mono]
    low = bool(mm) and (min(x[1] for x in mm) < 10 or any((x[2] or 0) > 90 for x in mm))
    node = R if R and low else 0
    grp = (R if R and not low else 0) + L_out
    st["oomwin"] = _trim(st["oomwin"] + [[mono, node, grp, svc, unit, likely, mx]], mono, OOM_SPAN)
    st["oom_n"] = st.get("oom_n", 0) + 1


def _probe_missed(pmt, pmt0, now):
    """NS-D24: the probe is alive (its file written within PROBE_ALIVE) and a run of it is missing around this minute:
    no write since the last minute read, or the last two writes PROBE_GAP or more apart."""
    if pmt is None or pmt0 is None or now is None or now - pmt > PROBE_ALIVE:
        return False
    return pmt == pmt0 or pmt - pmt0 >= PROBE_GAP


def _oom_hold(st, rs, out):
    """REV-ns-a12 С-1: the window of OOM is not in hostmon.json — after a restart (or a new owner) an alarm said
    before goes lower only when this run has a full window of its own deltas (§2: «→ 0 за 30 мин», §2.1
    «Перезапуск»: no history — wait); a new OOM is said at once."""
    n = int(st.get("oom_n") or 0)
    said = rs.get("said")
    if said in ("warn", "crit") and RANK[out["level"]] < RANK[said] and n < OOM_FULL:
        return _wait(n * 60, OOM_SPAN)
    return out


def _times(n):
    return msg("gw_host.n.times", n=n)


def _ev_oom(rule, st, s, rs, cfg, ctx, now, mono):
    if not s.get("minute"):
        return None
    if not st.get("oom_n"):
        return _wait(0, OOM_SPAN)                   # one minute read only: no delta yet
    node = sum(x[1] for x in st["oomwin"])
    grp = sum(x[2] for x in st["oomwin"])
    if node:
        return _real(rs, rule, "crit", node, _find("oom", "crit", times=_times(node), min=30),
                     msg("gw_host.advice.mem"))
    if grp:
        return _oom_hold(st, rs, _real(rs, rule, "warn", grp, _find("oom", "warn", times=_times(grp), min=30),
                                       msg("gw_host.advice.oom")))
    return _oom_hold(st, rs, _real(rs, rule, "ok", 0))


def _ev_oom_svc(rule, st, s, rs, cfg, ctx, now, mono):
    if not s.get("minute"):
        return None
    if not st.get("oom_n"):
        return _wait(0, OOM_SPAN)
    hits = [x for x in st["oomwin"] if x[3]]
    if not hits:
        return _oom_hold(st, rs, _real(rs, rule, "ok", 0))
    n = sum(x[3] for x in hits)
    last = hits[-1]
    unit, likely, mx = last[4], last[5], last[6]
    mb = int(mx // MIB) if _num(mx) else None
    if likely == "probe":
        find, adv = _find("oom_svc", "warn", "_probe", max=mb), msg("gw_host.advice.oom_svc")      # NS-D24
    elif unit and mb is not None:
        find = _find("oom_svc", "warn", "_likely" if likely else "", unit=unit, max=mb)
        adv = msg("gw_host.advice.oom_svc") if unit.startswith("kaskad-") else msg("gw_host.advice.oom_svc_other")
    elif unit:
        find = _find("oom_svc", "warn", "_likely_nomax" if likely else "_nomax", unit=unit)
        adv = msg("gw_host.advice.oom_svc") if unit.startswith("kaskad-") else msg("gw_host.advice.oom_svc_other")
    else:                                           # NS-D24: no name — the common advice, not «your program»
        find, adv = _find("oom_svc", "warn", "_any"), msg("gw_host.advice.oom_svc_any")
    return _real(rs, rule, "warn", n, find, adv, unit=unit)


def _ev_ratio(rule, st, s, rs, cfg, ctx, now, mono):
    span = rule["window_s"]
    pts, share = _window(st, rule["metric"], mono, span)
    if share < rule["cover"]:
        have = (pts[-1][0] - pts[0][0] + STEP) if pts else 0
        return _wait(have, span)
    a, b = sum(p[1] for p in pts), sum(p[2] for p in pts)
    if b <= rule["min_n"] or b <= 0:
        lvl = _level(rs, rule, (False, True, False, True))
        return _real(rs, rule, lvl, None)
    v = 100.0 * a / b
    if a < rule.get("min_bad", 0):
        # too few resent to RAISE a level (К2-3) — but a level already up goes only past «выкл», as the hysteresis says:
        # a warn of 2 % never drops at 1.9 % because the count fell under min_bad (REV-s1-kt2-final П-1)
        _, w_back, _, c_back = _scalar_preds(rule, v)
        lvl = _level(rs, rule, (False, w_back, False, c_back))
        if lvl == "ok":
            return _real(rs, rule, lvl, None)
    else:
        lvl = _level(rs, rule, _scalar_preds(rule, v))
    return _real(rs, rule, lvl, _r2(v), _find(rule["id"], lvl, pct=_r1(v)), msg("gw_host.advice." + rule["id"]))


def _ev_rate(rule, st, s, rs, cfg, ctx, now, mono):
    span = rule["window_s"]
    pts, share = _window(st, rule["metric"], mono, span)
    if share < rule["cover"]:
        have = (pts[-1][0] - pts[0][0] + STEP) if pts else 0
        return _wait(have, span)
    v = sum(p[1] for p in pts) / len(pts)
    lvl = _level(rs, rule, _scalar_preds(rule, v))
    return _real(rs, rule, lvl, _r1(v), _find(rule["id"], lvl, n=_r1(v)), msg("gw_host.advice." + rule["id"]))


def port_text(mbit):
    """The port of the tariff as words: «1 Гбит/с» from 1000 up when whole hundreds, else «500 Мбит/с»."""
    if mbit >= 1000 and mbit % 100 == 0:
        g = mbit / 1000.0
        return msg("gw_host.n.port_gbit", v=int(g) if g == int(g) else g)
    return msg("gw_host.n.port_mbit", v=int(mbit))


def _ev_port(rule, st, s, rs, cfg, ctx, now, mono):
    """C7: the busier direction of the interfaces of the default route (bytes/s, averaged over the window each)
    ÷ the port of the tariff (Mbit/s, ``cfg["port_mbit"]``); no port — na «no_port» (a real reason)."""
    port = cfg.get("port_mbit")
    if isinstance(port, bool) or not isinstance(port, (int, float)) or port <= 0:
        return _na("no_port")
    span = rule["window_s"]
    pts, share = _window(st, rule["metric"], mono, span)
    if share < rule["cover"]:
        have = (pts[-1][0] - pts[0][0] + STEP) if pts else 0
        return _wait(have, span)
    rx = sum(p[1] for p in pts) / len(pts)
    tx = sum(p[2] for p in pts) / len(pts)
    v = 100.0 * max(rx, tx) * 8 / (float(port) * 1e6)
    lvl = _level(rs, rule, _scalar_preds(rule, v))
    return _real(rs, rule, lvl, _r1(v), _find(rule["id"], lvl, pct=int(round(v)), port=port_text(port),
                                                 min=int(span // 60)), msg("gw_host.advice." + rule["id"]))


def _ev_cert(rule, st, s, rs, cfg, ctx, now, mono):
    certs = s.get("certs")
    if certs is None:
        return None
    if not certs:
        return _na("no_cert")
    got = []
    for c in certs:
        if not isinstance(c, dict):
            continue
        if c.get("what") == "site":
            exp = rs.get("site_exp") if isinstance(rs.get("site_exp"), list) else None
            if c.get("failed"):                     # REV-ns-a12-2 П-15: the site not read — its last expiry, the IP on
                if exp is None or now is None or _num(exp[0]) is None:
                    continue
                d, c = (exp[0] - now) / 86400.0, {"what": "site", "name": exp[1]}
            else:
                d = _num(c.get("days_left"))
                if d is None:
                    continue
                if now is not None:
                    rs["site_exp"] = [now + d * 86400.0, c.get("name") or ""]
            lvl = _sub_level(rs, "site", (d < 14, d >= 14, d < 7, d >= 7))
            got.append((lvl, "site", _find("cert", lvl, name=c.get("name") or "", days=int(d)), d))
        elif c.get("what") == "ip":
            h, R = _num(c.get("hours_left")), _num(c.get("renew_left_h")) or 72
            if h is None:
                if c.get("state") != "expired":
                    continue
                h = 0.0
            at = _num(c.get("try_at"))
            failed = c.get("try_ok") is False and at is not None and \
                (now is None or at >= now - max(0.0, R - h) * 3600)     # a try after the start of the renewal
            lvl = _sub_level(rs, "ip", ((h < R and failed) or h < 48, h >= R,
                                        h < 24 or c.get("state") == "expired", h >= R))
            code = c.get("reason") if failed else None
            find = _find("cert_ip", lvl, hours=int(max(0, h)), reason=msg("gw_host.reason." + code)) \
                if code in CERT_REASONS else _find("cert_ip", lvl, "_noreason", hours=int(max(0, h)))
            got.append((lvl, "ip", find, h / 24.0))
    if not got:
        return _na("no_cert")
    lvl, what, find, days = _worst(sorted(got, key=lambda g: g[1] != "ip"))     # П-1: of the equals — the IP one
    rs["hl"] = lvl
    out = _real(rs, rule, lvl, _r1(days), find, msg("gw_host.advice.cert" if what == "site" else
                                                    "gw_host.advice.cert_ip"), what=what)
    out["problem"] = what != "site"           # NS-D4: the site's certificate has its problem already (site_cert)
    return out


def _ev_services(rule, st, s, rs, cfg, ctx, now, mono):
    svc, inb = ctx.get("services"), ctx.get("inbounds_why")
    if svc is None and inb is None:
        return _wait(None, None)
    down = [msg("gw_host.svc." + k) if k in SERVICES else str(k)
            for k, v in sorted((svc if isinstance(svc, dict) else {}).items()) if v is False]
    down += [_inbound_title(k) for k, v in sorted((inb if isinstance(inb, dict) else {}).items()) if v]
    lvl = "crit" if down else "ok"
    rs["hl"] = lvl
    return _real(rs, rule, lvl, len(down), _find("services", lvl, what=down) if down else None, None)


def _inbound_title(kind):
    """The name of a protocol for people (gw/registry.title: «AmneziaWG 2.0», not awg2; REV-ns-a12 З-4)."""
    try:
        from . import registry
        return registry.title(kind)
    except Exception:                                # noqa: BLE001 — no adapters here (a broken payload): the code
        return str(kind)


# ── the light probes (§2 loss, rtt, anchor; the series of gw/hostping.py) ──

def _feed_series(st, ctx, cfg, now, mono):
    anchors = list(cfg.get("anchors") or [])
    for addr in list(st["series"]):
        if addr not in anchors:
            st["series"].pop(addr, None)
    for addr in list(st["pt_na"]):
        if addr not in anchors:
            st["pt_na"].pop(addr, None)
    for addr in list(st["norm"]["rtt"]):
        if addr not in anchors:
            st["norm"]["rtt"].pop(addr, None)               # a new set of points: the norms of the removed go
    pr = ctx.get("probes") if isinstance(ctx.get("probes"), dict) else None
    if pr:
        excl = pr.get("why") if pr.get("excl") else None
        out = bool(ctx.get("node_offline"))
        for a in pr.get("anchors") or []:
            addr = a.get("addr")
            if addr not in anchors:
                continue
            if a.get("na"):
                st["pt_na"][addr] = [mono, a["na"]]
                continue
            st["pt_na"].pop(addr, None)
            rtt = [x for x in a.get("rtt") or [] if _num(x) is not None]
            med = statistics.median(rtt) if rtt else None
            st["series"].setdefault(addr, []).append([mono, int(a.get("n") or 0), int(a.get("lost") or 0), med,
                                                      excl, out,
                                                      int(a.get("runs") or 0), a.get("via") or "icmp", rtt])
            if med is not None and not excl and not out:
                _norm_add(st, addr, med, now)
    for addr, ring in list(st["series"].items()):
        ring = [x for x in ring if mono - LOSS_SPAN < x[0] <= mono]
        counted = [x for x in ring if not x[4] and not x[5]][-LOSS_RUNS:]
        dropped = [x for x in ring if x[4] or x[5]][-DROPPED_KEEP:]
        st["series"][addr] = sorted(counted + dropped, key=lambda x: x[0])
    for addr in list(st["pt_na"]):
        if not mono - LOSS_SPAN < st["pt_na"][addr][0] <= mono:
            st["pt_na"].pop(addr, None)


def _norm_add(st, addr, rtt, now):
    hr = st["norm"].get("hour")
    if not hr or now - hr[0] >= 3600 or now < hr[0]:
        if hr and isinstance(hr[1], dict):
            for a, vals in hr[1].items():
                if vals:
                    st["norm"]["rtt"][a] = (st["norm"]["rtt"].get(a, []) + [statistics.median(vals)])[-NORM_HOURS:]
        hr = [now, {}]
        st["norm"]["hour"] = hr
    hr[1].setdefault(addr, []).append(rtt)


def _points(st, cfg, mono):
    """{addr: {"state": good|wait|na, "why", "loss", "rtt", "counted", "dropped"}} over the window of the series."""
    out = {}
    for addr in cfg.get("anchors") or []:
        ring = [x for x in st["series"].get(addr) or [] if mono - LOSS_SPAN < x[0] <= mono]
        counted = [x for x in ring if not x[4] and not x[5]][-LOSS_RUNS:]
        noisy = [x for x in ring if x[4] in ("noisy", "overflow")]
        p = {"counted": len(counted), "dropped": len(noisy)}
        na = st["pt_na"].get(addr)
        if na:
            p.update(state="na", why=na[1])
        elif len(counted) >= LOSS_MIN:
            sent = sum(x[1] for x in counted)
            p.update(state="good", loss=(100.0 * sum(x[2] for x in counted) / sent) if sent else 0.0)
            rtts = [x[3] for x in counted if x[3] is not None]
            p["rtt"] = statistics.median(rtts) if rtts else None
        elif len(noisy) > len(counted):
            p.update(state="na", why="noisy")
        else:
            p.update(state="wait")
        out[addr] = p
    return out


def _reduce(vals):
    """The node from k points (§2 loss): sorted, the worst ⌊(k−1)/2⌋ dropped, the mean of the rest; k = 2 — the less."""
    vals = sorted(vals)
    k = len(vals)
    if k == 2:
        return vals[0]
    keep = vals[:k - (k - 1) // 2]
    return sum(keep) / len(keep)


def _probe_gate(rule, cfg, ctx):
    if not cfg.get("probes") or ctx.get("probes_on") is False:
        return _na("probes_off")
    if ctx.get("node_offline"):
        return {"level": "wait", "why": "offline", "nowait": True}       # NS-D14: the outage is said by its event
    return None


def _ev_loss(rule, st, s, rs, cfg, ctx, now, mono):
    gate = _probe_gate(rule, cfg, ctx)
    if gate:
        return gate
    pts = _points(st, cfg, mono)
    good = dict((a, p) for a, p in pts.items() if p["state"] == "good")
    k = len(good)
    if k == 0:
        nas = [p for p in pts.values() if p["state"] == "na"]
        if pts and len(nas) == len(pts):
            whys = set(p["why"] for p in nas)
            return _na("blocked" if whys == set(["blocked"]) else ("no_route" if "no_route" in whys else "noisy"))
        return _wait(None, None)
    losses = [p["loss"] for p in good.values()]
    if k == 1:
        v = losses[0]
        return {"level": "info", "why": "one_point", "value": _r1(v), "args": {"pct": _r1(v)}}
    v = _reduce(losses)
    seen = sum(1 for x in losses if x > rule["warn"][1])
    lvl = _level(rs, rule, _scalar_preds(rule, v))
    return _real(rs, rule, lvl, _r1(v), _find("loss", lvl, pct=_r1(v), seen=seen, of=k),
                 msg("gw_host.advice.loss"))


def _ev_rtt(rule, st, s, rs, cfg, ctx, now, mono):
    gate = _probe_gate(rule, cfg, ctx)
    if gate:
        return gate
    pts = _points(st, cfg, mono)
    got = []
    for addr, p in pts.items():
        norm = st["norm"]["rtt"].get(addr) or []
        if p["state"] != "good" or p.get("rtt") is None or len(norm) < NORM_MIN:
            continue
        n = statistics.median(norm)
        if n and n > 0:
            got.append((p["rtt"] / n, p["rtt"], n))
    if not got:
        return _wait(None, None)
    ratio = _reduce([g[0] for g in got])
    got.sort()
    mid = got[(len(got) - 1) // 2]
    lvl = _level(rs, rule, _scalar_preds(rule, ratio))
    return _real(rs, rule, lvl, _r2(ratio), _find("rtt", lvl, x=msg("gw_host.n.x", n=_r1(ratio)), ms=int(round(mid[1])),
                                                   norm=int(round(mid[2]))), msg("gw_host.advice.rtt"))


def _ev_anchor(rule, st, s, rs, cfg, ctx, now, mono):
    gate = _probe_gate(rule, cfg, ctx)
    if gate:
        return gate
    pts = _points(st, cfg, mono)
    good = dict((a, p) for a, p in pts.items() if p["state"] == "good")
    if len(good) < 2:
        st["anchor_on"] = []
        return _wait(None, None)
    node = _reduce([p["loss"] for p in good.values()])
    on = set(st.get("anchor_on") or [])
    flagged = []
    for addr, p in good.items():
        was = addr in on
        if node < 1 and (p["loss"] > rule["warn"][0] or (was and not p["loss"] < rule["warn"][1])):
            flagged.append({"addr": addr, "pct": _r1(p["loss"])})
    st["anchor_on"] = [f["addr"] for f in flagged]
    rs["hl"] = "ok"
    if flagged:
        return {"level": "info", "why": "anchor", "value": len(flagged), "points": flagged}
    return _real(rs, rule, "ok", 0)


EVAL = {"cpu": _ev_cpu, "steal": _ev_steal, "iowait": _ev_mean, "load": _ev_load, "psi_cpu": _ev_psi,
        "psi_mem": _ev_psi, "psi_io": _ev_psi, "mem": _ev_mem, "oom": _ev_oom, "oom_svc": _ev_oom_svc,
        "swap": _ev_swap, "disk": _ev_disk, "disk_eta": _ev_disk_eta, "inodes": _ev_inodes, "drops": _ev_ratio,
        "nic_err": _ev_ratio, "softnet": _ev_rate, "port": _ev_port, "retrans": _ev_ratio, "listen": _ev_rate,
        "conntrack": _ev_conntrack, "fd": _ev_fd, "clock": _ev_clock, "cert": _ev_cert, "loss": _ev_loss,
        "rtt": _ev_rtt, "anchor": _ev_anchor, "services": _ev_services}


# ── the state machine (§2.1) ───────────────────────────────────────────────

def _soft(level, why):
    return level in ("wait", "info") or (level == "na" and why in TEMP_WHY)


def step(rs, cur, rule, ctx, now, mono):
    """One rule after its window, hysteresis and «hold»: ``cur`` — {"level": ok|warn|crit|info|wait|na, "why",
    "value", "find", "advice", …}. Returns the new ``rs`` and the event to publish (or None). See SPEC §2.1: the
    table of transitions, ``pending`` (only the event waits for the end of ``quiet_until``, never the view), WAIT_MAX,
    the decision «push» (``args.push``)."""
    rs = dict(rs or {})
    ctx = ctx or {}
    lvl = cur.get("level") or "wait"
    why = cur.get("why")
    rs["level"], rs["why"] = lvl, why
    if lvl in REAL:
        for k in ("value", "find", "advice", "args", "points", "problem", "mount", "what", "unit"):
            if k in cur:
                rs[k] = cur[k]
            else:
                rs.pop(k, None)
    elif lvl == "info":                             # the words of the last real finding stay (a held alarm)
        for k in ("args", "points"):
            rs[k] = cur.get(k)
    rs["why_args"] = cur.get("args") if lvl == "wait" and isinstance(cur.get("args"), dict) else None
    if lvl in REAL and not cur.get("reused"):
        rs["eval_mono"], rs["eval_at"] = mono, now
    said = rs.get("said")
    if _soft(lvl, why):
        if cur.get("nowait"):                       # NS-D14: the node without network — no count of WAIT_MAX
            rs["wait_since"] = None
            return rs, None
        if rs.get("wait_since") is None:
            rs["wait_since"] = mono
        if not (said in ("warn", "crit") and mono - rs["wait_since"] > WAIT_MAX):
            return rs, None
        lvl, why = "na", (why if lvl == "na" else "history")
        rs["level"], rs["why"] = lvl, why
    else:
        rs["wait_since"] = None
    if lvl == "na":
        rs["pending"] = None
        if said in ("warn", "crit"):
            rs["said"], rs["since"] = None, now
            return rs, _publish(rs, rule, ctx, "na", "na", said, mono, why)
        if said is not None:
            rs["said"], rs["since"] = None, now
        return rs, None
    quiet = kind_of(rule) is not None and _num(rs.get("quiet_until")) is not None and mono < rs["quiet_until"]
    if lvl == said:
        rs["pending"] = None
        return rs, None
    if said in (None, "ok"):
        rs["pending"] = None
        rs["said"], rs["since"] = lvl, now
        if lvl == "ok":
            return rs, None
        if lvl == "warn" and quiet and rs.get("last_ev") == "raise" and rs.get("last_lvl") == "warn":
            return rs, None                         # a repeat of the same «raise» within 15 min: said, not told
        return rs, _publish(rs, rule, ctx, "raise", lvl, "ok", mono)
    if RANK[lvl] > RANK[said]:                      # warn -> crit: at once, but after a «lower» of this window
        if quiet and rs.get("last_ev") == "lower":
            rs["pending"] = lvl
            return rs, None
        rs["pending"] = None
        rs["said"], rs["since"] = lvl, now
        return rs, _publish(rs, rule, ctx, "raise", lvl, said, mono)
    if quiet:                                       # lower / clear within the window: the event waits, the view not
        rs["pending"] = lvl
        return rs, None
    rs["pending"] = None
    rs["said"], rs["since"] = lvl, now
    return rs, _publish(rs, rule, ctx, "lower" if lvl == "warn" else "clear", lvl, said, mono)


def _publish(rs, rule, ctx, ev, level, frm, mono, why=None):
    kind = kind_of(rule)
    if kind is None:
        return None
    sw = (ctx.get("push") or {}).get(kind, True) is not False
    alarm = rule.get("alarm")
    host_level = ctx.get("host_level") or "crit"
    pushed = rs.get("pushed")
    if ev == "raise":
        push = sw and ((alarm is not None and RANK[level] >= RANK[alarm]) or host_level == "warn")
        if push:
            rs["pushed"] = level if pushed is None or RANK[level] > RANK[pushed] else pushed
    elif ev == "lower":
        push = sw and pushed == "crit"
    else:
        push = sw and pushed is not None
        rs["pushed"] = None
    rs["quiet_until"] = mono + EVENT_QUIET
    rs["last_ev"], rs["last_lvl"] = ev, level
    args = {"check": rule["id"], "level": level, "from": frm, "push": bool(push),
            "what": msg("gw_host.check." + rule["id"]),
            "find": rs.get("find") if ev in ("raise", "lower") else None,
            "why": msg("gw_host.why." + why) if why else None}
    return {"kind": kind, "key": "gw_host.event." + ev, "args": args}


# ── evaluate ───────────────────────────────────────────────────────────────

def _cur(rule, st, s, rs, cfg, ctx, now, mono):
    try:
        cur = EVAL[rule["id"]](rule, st, s, rs, cfg, ctx, now, mono)
    except BAD:
        cur = _na("error")                          # a bad value in one rule: this rule, the others go on
    if cur is None:                                 # not this round (a minute or an hour rule): the last one again
        if rs.get("level") is None:
            return _wait(None, None)
        cur = {"level": rs["level"], "why": rs.get("why"), "reused": True}
        for k in ("value", "find", "advice", "args", "points", "problem", "mount", "what", "unit"):
            if k in rs:
                cur[k] = rs[k]
        if rs["level"] == "wait":                   # REV-ns-a12-2 З-9: «ждёт истории: N мин из M» in every round
            cur["args"] = rs.get("why_args")
    if cur.get("level") not in REAL and cur.get("level") != "info":
        rs["hc"] = 0
    return cur


def _round_summary(st, s, mono):
    lv = None
    warn = alarm = False
    for r in HOST_RULES:
        sh = _shown(st["rules"].get(r["id"]) or {})
        if sh in RANK and (lv is None or RANK[sh] > lv):
            lv = RANK[sh]
        if sh in ("warn", "crit"):
            warn = True
            if r["alarm"] and RANK[sh] >= RANK[r["alarm"]]:
                alarm = True
    keys = ("busy", "steal", "iowait", "softirq", "load_core", "psi_cpu", "psi_mem", "psi_io", "mem_pct", "swap_pct",
            "rx", "tx", "rx_rate", "tx_rate", "pkts", "drops", "errs", "softnet_n", "retrans", "out_segs",
            "listen_n", "conntrack_pct", "fd_sys")
    row = dict((k, s.get(k)) for k in keys)
    row.update(level=lv, warn=warn, alarm=alarm)
    st["rounds"] = _trim(st["rounds"] + [[mono, row]], mono, 60)
    if s.get("minute"):
        parts = _fs_parts(s)
        lm = st["last_min"]
        lm["at"] = mono
        lm["disk"] = max([p[2] for p in parts]) if parts else None
        lm["disk_free"] = min([p[3] for p in parts]) if parts else None
        ino = [100.0 * (p[4] - p[5]) / p[4] for p in parts if p[4] and p[5] is not None]
        lm["inodes"] = max(ino) if ino else None
        c = s.get("clock") if isinstance(s.get("clock"), dict) else {}
        lm["clock_ok"] = None if c.get("synced") is None else (1 if c["synced"] else 0)
        lm["clock_off"] = c.get("offset_s")
        fdr = st["rules"].get("fd") or {}
        lm["fd"] = fdr.get("value") if fdr.get("level") in REAL else None
        lm["oom"] = (st["oomwin"][-1][1] if st["oomwin"] and st["oomwin"][-1][0] == mono else 0)


def _shown(rs):
    """The level seen now (§2.1, REV-3 З-2): ``pending`` when there is one, else ``said``."""
    p = rs.get("pending")
    return p if p is not None else rs.get("said")


def evaluate(prev, sample, cfg, ctx, now, mono, only=None):
    """One full round of the collector: the windows, every rule and its ``step``. ``only`` — the ids evaluated (the
    others: na «no_gateway», evaluate_instant). Returns (state, events)."""
    cfg = cfg or {}
    ctx = ctx or {}
    s = sample or {}
    st = _start(prev)
    st["at"], st["mono"] = now, mono
    st["enabled"] = cfg.get("enabled", True) is not False
    st["instant"] = only is not None
    st["owner"] = ctx.get("owner") or "collector"
    st["anchors"] = list(cfg.get("anchors") or [])
    st["errors"] = []                               # the parts skipped this round (REV-ns-a12-2 П-13: A5 logs them)
    if st["enabled"]:
        _guard(_feed, st, s, mono)
        if only is None:
            _guard(_feed_series, st, ctx, cfg, now, mono)
            _guard(_oom_minute, st, s, ctx, mono)
    events = []
    for rule in HOST_RULES:
        rs = st["rules"].get(rule["id"]) or {}
        if rs.get("hl") is None and rs.get("said") in REAL:
            rs["hl"] = rs["said"]                   # after a restart: the hysteresis from the level said before
        if "sub" not in rs:
            rs["sub_start"] = rs.get("hl")          # the parts of the rule too (З-1); only this round
        if not st["enabled"]:
            cur = _na("off")
        elif only is not None and rule["id"] not in only:
            cur = _na("no_gateway")
        else:
            cur = _cur(rule, st, s, rs, cfg, ctx, now, mono)
        rs.pop("sub_start", None)
        rs, ev = step(rs, cur, rule, ctx, now, mono)
        st["rules"][rule["id"]] = rs
        if ev:
            events.append(ev)
    if st["enabled"]:
        _guard(_round_summary, st, s, mono)
    return st, events


def _guard(fn, *args):
    """A part of the round outside the rules (the rings, the series, OOM, the summary): a bad value skips it this
    round (REV-ns-a12 П-5); the rules and the automaton go on. Its name goes to ``st["errors"]`` (the first argument
    is the state): the collector logs it once an hour (REV-ns-a12-2 П-13)."""
    try:
        fn(*args)
    except BAD as e:
        errs = args[0].setdefault("errors", []) if args and isinstance(args[0], dict) else []
        errs.append("%s: %s" % (getattr(fn, "__name__", "part").lstrip("_"), type(e).__name__))


INSTANT = tuple(r["id"] for r in HOST_RULES if r["instant"])


def evaluate_instant(prev, raw, cfg, ctx, now, mono):
    """The rules ``instant`` alone by one raw of the minute (the bot without the gateway, §3.9): the same ``step``
    and «hold» (3 evaluations — 3 minutes); the rules with a history — na «no_gateway» (one event when it was
    said warn/crit)."""
    s = derive(None, dict(raw or {}, minute=True))
    return evaluate(prev, s, cfg, dict(ctx or {}, owner=(ctx or {}).get("owner") or "bot"), now, mono, only=INSTANT)


def instant(raw, cfg, ctx, now=None):
    """The rules ``instant`` by one snapshot, no history, no automaton (the panel and kaskadctl without a fresh
    live.json, §3.9 п. 4): ``said`` = the level of this evaluation."""
    cfg = cfg or {}
    ctx = ctx or {}
    s = derive(None, dict(raw or {}, minute=True, hour=True))
    st = _start({})
    st["at"], st["mono"] = now, s.get("mono") or 0.0
    st["enabled"] = cfg.get("enabled", True) is not False
    st["instant"] = True
    for rule in HOST_RULES:
        if rule["id"] not in INSTANT:
            continue
        rs = {"hl": "ok"}
        if not st["enabled"]:
            cur = _na("off")
        else:
            one = dict(rule, hold=1)
            cur = _cur(one, st, s, rs, cfg, ctx, now, st["mono"])
        for k in ("value", "find", "advice", "args", "points", "problem", "mount", "what", "unit"):
            if k in cur:
                rs[k] = cur[k]
        rs["level"], rs["why"] = cur.get("level"), cur.get("why")
        rs["said"] = cur["level"] if cur.get("level") in REAL else None
        st["rules"][rule["id"]] = rs
    return st


# ── the light (§2 «Светофор», §3.6) ────────────────────────────────────────

def _finding(rule, rs, st, lang):
    shown = _shown(rs)
    mono, now = st.get("mono"), st.get("at")
    f = {"id": rule["id"], "level": shown, "stale": _soft(rs.get("level"), rs.get("why")) and rs.get("level") != "info",
         "confirm_until": None, "since": rs.get("since"), "value": rs.get("value"), "unit": rule["unit"],
         "msg": rs.get("find"), "advice_msg": rs.get("advice"),
         "alarm": bool(rule["alarm"]) and RANK[shown] >= RANK[rule["alarm"]]}
    f["problem"] = f["alarm"] and rule["id"] not in NO_PROBLEM and rs.get("problem") is not False
    p = rs.get("pending")
    if p is not None and rs.get("said") in RANK and RANK[p] < RANK[rs["said"]] and _num(rs.get("quiet_until")) \
            is not None and mono is not None and now is not None:
        f["confirm_until"] = now + max(0.0, rs["quiet_until"] - mono)
    if f["stale"] and _num(rs.get("eval_at")) is not None and now is not None:
        f["stale_msg"] = msg("gw_host.stale", min=int(max(0, now - rs["eval_at"]) // 60))
    if f["confirm_until"] is not None:
        f["better_msg"] = msg("gw_host.better", until_at=f["confirm_until"])
    if lang is not False:
        f["text"] = say(f["msg"], lang) if f["msg"] else ""
        f["advice"] = say(f["advice_msg"], lang) if f["advice_msg"] else ""
    return f


def light(state, lang=None):
    """The light of the node by the SHOWN levels (``pending ?? said``): red — a crit (``services`` too), yellow — a
    warn, green — none and at least one ok, «wait» — nothing said yet; «off» — the light health is switched off.
    ``top`` — ≤ 3 findings (crit first, then warn, in the order of HOST_RULES), ``info`` — the lines that change
    nothing (a point that cuts pings, the series dropped as noisy, «стало лучше», «по одной точке»), ``checks`` — the
    evaluation of every rule this round (with a reason: ``why_msg`` and ``why_text`` — «ждёт истории: 7 мин из 30»),
    ``alarms`` — every rule at its alarm (for the problems, NS-D4). ``lang`` —
    the language of ``text``/``advice`` (None: the caller's; False: no texts, only msgs)."""
    st = state or {}
    rules = st.get("rules") or {}
    out = {"level": "wait", "top": [], "info": [], "checks": {}, "alarms": [], "instant": bool(st.get("instant"))}
    if st.get("enabled") is False:
        out["level"] = "off"
    found, any_ok, worst = [], False, -1
    for rule in HOST_RULES:
        rid = rule["id"]
        rs = rules.get(rid)
        if not rs:
            continue
        chk = {"level": rs.get("level")}
        if rs.get("why"):
            chk["why"] = rs["why"]
            chk["why_msg"] = why_msg(rs["why"], rs.get("why_args"))
            if lang is not False:
                chk["why_text"] = say(chk["why_msg"], lang)
        if rs.get("value") is not None:
            chk["value"] = rs["value"]
        if _shown(rs) is not None:
            chk["shown"] = _shown(rs)
        out["checks"][rid] = chk
        shown = _shown(rs)
        if shown == "ok":
            any_ok = True
        if shown in ("warn", "crit"):
            worst = max(worst, RANK[shown])
            f = _finding(rule, rs, st, lang)
            found.append(f)
            if f["alarm"]:
                out["alarms"].append({"id": rid, "kind": kind_of(rule), "level": shown, "problem": f["problem"],
                                      "msg": f["msg"]})
        p = rs.get("pending")
        if p == "ok" and rs.get("said") in ("warn", "crit"):
            f = _finding(rule, rs, st, False)
            out["info"].append(_info({"id": "better", "check": rid, "until": f["confirm_until"],
                                      "msg": msg("gw_host.info.better", what=msg("gw_host.check." + rid),
                                                 until_at=f["confirm_until"])}, lang))
        if rid == "loss" and rs.get("level") == "info" and rs.get("why") == "one_point":
            out["info"].append(_info({"id": "one_point", "msg": msg("gw_host.one_point",
                                                                     pct=(rs.get("args") or {}).get("pct"))}, lang))
        if rid == "anchor" and rs.get("level") == "info":
            for pnt in rs.get("points") or []:
                out["info"].append(_info({"id": "anchor", "addr": pnt["addr"],
                                          "msg": msg("gw_host.info.anchor", addr=pnt["addr"], pct=pnt["pct"])}, lang))
    pts = _points(st, {"anchors": st.get("anchors") or []}, st.get("mono") or 0.0) if st.get("anchors") else {}
    for addr, p in sorted(pts.items()):
        if p.get("state") == "na" and p.get("why") == "noisy":
            n, of = p["dropped"], p["dropped"] + p["counted"]
            out["info"].append(_info({"id": "noisy", "addr": addr, "n": n, "of": of,
                                      "msg": msg("gw_host.info.noisy", n=n, of=of)}, lang))
    found.sort(key=lambda f: -RANK[f["level"]])                    # stable: the order of HOST_RULES within a level
    out["top"] = found[:3]
    if out["level"] != "off":
        if worst == 2:
            out["level"] = "crit"
        elif worst == 1:
            out["level"] = "warn"
        elif any_ok:
            out["level"] = "ok"
    return out


def health_in(h, lang=None):
    """``host.health`` of live.json / host-health.json with its texts said again in ``lang`` (None: the caller's) by
    their msgs: ``top[].text/advice``, ``info[].text``, ``checks[].why_text`` (§3.6; the collector's ``localize``, the
    readers of the bot's file). A copy; a field that is not a msg keeps its text."""
    if not isinstance(h, dict):
        return h
    out = dict(h)
    top = []
    for f in h.get("top") or []:
        if isinstance(f, dict):
            f = dict(f)
            if isinstance(f.get("msg"), dict):
                f["text"] = say(f["msg"], lang)
            if isinstance(f.get("advice_msg"), dict):
                f["advice"] = say(f["advice_msg"], lang)
        top.append(f)
    info = []
    for i in h.get("info") or []:
        if isinstance(i, dict) and isinstance(i.get("msg"), dict):
            i = dict(i, text=say(i["msg"], lang))
        info.append(i)
    checks = {}
    for rid, c in (h.get("checks") or {}).items() if isinstance(h.get("checks"), dict) else ():
        if isinstance(c, dict) and isinstance(c.get("why_msg"), dict):
            c = dict(c, why_text=say(c["why_msg"], lang))
        checks[rid] = c
    out.update(top=top, info=info, checks=checks)
    return out


PROBES_SPAN = 10 * 60       # s: the window of ``health.probes`` (loss10, rtt10; §3.6)


def probes_view(state, cfg, mono):
    """``health.probes`` of live.json (§3.6): the counted series of the last 10 min — the loss of every point (lost ÷
    sent, %), its median response, the way of its last series; the node — the reduction of §2 over the points with
    series (``_reduce``), the median of their responses; ``norm`` — the median of the hourly norms of the points that
    have NORM_MIN hours."""
    st = state or {}
    cfg = cfg or {}
    out = {"on": bool(cfg.get("probes")), "loss10": None, "rtt10": None, "norm": None, "anchors": []}
    losses, rtts, norms = [], [], []
    for addr in list(cfg.get("anchors") or []):
        ring = (st.get("series") or {}).get(addr) or []
        cnt = [x for x in ring if mono - PROBES_SPAN < x[0] <= mono and not x[4] and not x[5]]
        n = sum(int(x[1] or 0) for x in cnt)
        lost = sum(int(x[2] or 0) for x in cnt)
        meds = [x[3] for x in cnt if _num(x[3]) is not None]
        row = {"addr": addr, "via": ring[-1][7] if ring else None,
               "loss10": _r1(100.0 * lost / n) if n else None,
               "rtt10": _r1(statistics.median(meds)) if meds else None}
        out["anchors"].append(row)
        if row["loss10"] is not None:
            losses.append(row["loss10"])
        if row["rtt10"] is not None:
            rtts.append(row["rtt10"])
        nv = [x for x in ((st.get("norm") or {}).get("rtt") or {}).get(addr) or [] if _num(x) is not None]
        if len(nv) >= NORM_MIN:
            norms.append(statistics.median(nv))
    if losses:
        out["loss10"] = _r1(_reduce(losses))
    if rtts:
        out["rtt10"] = _r1(statistics.median(rtts))
    if norms:
        out["norm"] = _r1(statistics.median(norms))
    return out


def why_msg(why, args=None):
    """The words of a reason of wait/na: «ждёт истории: 7 мин из 30» with ``args`` {have, need} (REV-ns-a12 З-2),
    «ждёт истории» without them."""
    if why == "wait":
        a = args if isinstance(args, dict) else {}
        if _num(a.get("have")) is not None and _num(a.get("need")):
            return msg("gw_host.why.wait", have=a["have"], need=a["need"])
        return msg("gw_host.why.wait_any")
    return msg("gw_host.why." + str(why))


def _info(item, lang):
    if lang is not False:
        item["text"] = say(item["msg"], lang)
    return item


# ── the rows of the history (§3.4) ─────────────────────────────────────────

def _sum(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) if xs else None


def minute_row(state, mono):
    """The row of host_m1 for the minute ending at ``mono``: the counters — the sums of the deltas of its rounds
    (points None out of the sum, a minute without points — NULL), the percents — means, ``*_max``/``*_peak`` — the
    largest, ``level`` — the worst shown level (0/1/2, NULL — none), ``warn_min``/``alarm_min`` — 1 when a round of the
    minute had a rule at warn / at its alarm. ``canary_ms`` — None (the control work of A3 fills it)."""
    st = state or {}
    rows = [r for m, r in (st.get("rounds") or []) if mono - 60 < m <= mono]
    lm = st.get("last_min") or {}

    def col(k):
        return [r.get(k) for r in rows]
    lv = [r.get("level") for r in rows if r.get("level") is not None]
    busy = [x for x in col("busy") if x is not None]
    out = {"n": len(rows), "busy": _r2(_mean(busy)), "busy_max": _r2(max(busy)) if busy else None,
           "steal": _r2(_mean(col("steal"))), "iowait": _r2(_mean(col("iowait"))),
           "softirq": _r2(_mean(col("softirq"))), "load_core": _r2(_mean(col("load_core"))),
           "psi_cpu": _r2(_mean(col("psi_cpu"))), "psi_mem": _r2(_mean(col("psi_mem"))),
           "psi_io": _r2(_mean(col("psi_io"))), "mem_avail": _r2(_mean(col("mem_pct"))),
           "mem_avail_min": _r2(min([x for x in col("mem_pct") if x is not None] or [None])) if
           any(x is not None for x in col("mem_pct")) else None,
           "swap": _r2(_mean(col("swap_pct"))), "oom": lm.get("oom") if lm.get("at") is not None and
           mono - 60 < lm["at"] <= mono else None,
           "disk": _r2(lm.get("disk")), "disk_free": lm.get("disk_free"), "inodes": _r2(lm.get("inodes")),
           "rx": _sum(col("rx")), "tx": _sum(col("tx")),
           "rx_peak": _r2(max([x for x in col("rx_rate") if x is not None] or [0])) if
           any(x is not None for x in col("rx_rate")) else None,
           "tx_peak": _r2(max([x for x in col("tx_rate") if x is not None] or [0])) if
           any(x is not None for x in col("tx_rate")) else None,
           "pkts": _sum(col("pkts")), "drops": _sum(col("drops")), "errs": _sum(col("errs")),
           "softnet": _sum(col("softnet_n")), "retrans": _sum(col("retrans")), "out_segs": _sum(col("out_segs")),
           "listen": _sum(col("listen_n")), "conntrack": _r2(max([x for x in col("conntrack_pct") if x is not None]
                                                                   or [None])) if
           any(x is not None for x in col("conntrack_pct")) else None,
           "fd": _r2(lm.get("fd")), "clock_ok": lm.get("clock_ok"), "clock_off": lm.get("clock_off"),
           "canary_ms": None, "level": max(lv) if lv else None,
           "warn_min": 1 if any(r.get("warn") for r in rows) else 0,
           "alarm_min": 1 if any(r.get("alarm") for r in rows) else 0}
    if st.get("at") is not None:
        out["ts"] = int(st["at"] // 60 * 60)
    return out


def ping_rows(state, mono):
    """The rows of host_ping_m1 for the minute ending at ``mono``: {addr: row} + "*" (the node, ≥ 2 points, the
    reduction of §2). ``n`` — packets sent, ``lost`` — packets lost (SUM over the thinning: the loss is lost ÷ n),
    ``runs`` — runs of lost packets, ``rtt_med``/``rtt_p90`` (ms), ``jit`` — the mean |Δ| of neighbour replies over
    ``jit_n`` pairs, ``excl``/``outage`` — series left out, ``via`` — of the last series."""
    st = state or {}
    out, node = {}, []
    for addr, ring in sorted((st.get("series") or {}).items()):
        cur = [x for x in ring if mono - 60 < x[0] <= mono]
        if not cur:
            continue
        cnt = [x for x in cur if not x[4] and not x[5]]
        rtts = [r for x in cnt for r in (x[8] or [])]
        jit, jn = 0.0, 0
        for x in cnt:
            r = x[8] or []
            for a, b in zip(r, r[1:]):
                jit += abs(b - a)
                jn += 1
        n = sum(x[1] for x in cnt)
        lost = sum(x[2] for x in cnt)
        srt = sorted(rtts)
        out[addr] = {"via": cur[-1][7], "n": n, "lost": float(lost), "runs": sum(x[6] for x in cnt),
                     "rtt_med": _r2(statistics.median(srt)) if srt else None,
                     "rtt_p90": _r2(srt[min(len(srt) - 1, int(math.ceil(0.9 * len(srt))) - 1)]) if srt else None,
                     "jit": _r2(jit / jn) if jn else None, "jit_n": jn,
                     "excl": sum(1 for x in cur if x[4]), "outage": sum(1 for x in cur if x[5])}
        if n:
            node.append((100.0 * lost / n, n))
    if len(node) >= 2:
        pct = _reduce([x[0] for x in node])
        n = sum(x[1] for x in node)
        out["*"] = {"via": None, "n": n, "lost": _r2(pct * n / 100.0), "runs": 0, "rtt_med": None, "rtt_p90": None,
                    "jit": None, "jit_n": 0, "excl": 0, "outage": 0}
    return out


# ── hostmon.json (NS-D10) ──────────────────────────────────────────────────

KEEP_RS = ("said", "since", "pushed", "pending", "last_ev", "last_lvl", "value", "find", "advice", "eval_at", "sub")


def to_disk(state):
    """The part of hostmon.json: the levels, ``since``, ``pushed``, ``pending``, the end of the antispam on the wall
    clock (``quiet_until_wall``), the last finding (``value``, ``find``, ``advice`` — the words of a stale alarm after
    a restart) and the norms of the response."""
    st = state or {}
    rules = {}
    for rid, rs in (st.get("rules") or {}).items():
        rec = dict((k, rs.get(k)) for k in KEEP_RS)
        rec["sub"] = dict(rs["sub"]) if isinstance(rs.get("sub"), dict) and rs["sub"] else None
        q = _num(rs.get("quiet_until"))
        rec["quiet_until_wall"] = (st["at"] + (q - st["mono"])) if q is not None and st.get("at") is not None \
            and st.get("mono") is not None and q > st["mono"] else None
        if any(v is not None for v in rec.values()):
            rules[rid] = rec
    norm = (st.get("norm") or {}).get("rtt") or {}
    return {"v": V, "at": st.get("at"), "rules": rules, "norm": {"rtt": dict((a, list(v)) for a, v in norm.items())}}


def _our_msg(m, depth=0):
    """A msg of this module as hostmon.json keeps it: the key of gw_host, the args — numbers, words, None, our msgs
    and lists of them (names of services, REV-ns-a12 З-4), no deeper than 3."""
    if depth > 3 or not isinstance(m, dict) or not isinstance(m.get("key"), str) \
            or not m["key"].startswith("gw_host."):
        return False
    args = m.get("args")
    if args is None:
        return True
    if not isinstance(args, dict):
        return False

    def ok(v):
        if v is None or isinstance(v, (bool, int, float, str)):
            return True
        if isinstance(v, dict):
            return _our_msg(v, depth + 1)
        return isinstance(v, list) and all(isinstance(x, str) or _our_msg(x, depth + 1) for x in v)
    return all(ok(v) for v in args.values())


def from_disk(doc, mono, now=None):
    """The state from hostmon.json (a restart, a new owner): the windows empty — the rules «wait», the alarms stay
    with «stale», no event (§2.1 «Перезапуск»). ``now`` — the wall clock to turn ``quiet_until_wall`` into mono
    (None: as if no time passed since the file was written)."""
    doc = doc if isinstance(doc, dict) else {}
    now = now if _num(now) is not None else _num(doc.get("at"))
    rules = {}
    for rid, rec in (doc.get("rules") or {}).items():
        if rid not in RULES or not isinstance(rec, dict):
            continue
        rs = dict((k, rec.get(k)) for k in KEEP_RS)
        for k in ("said", "pending"):
            if rs.get(k) not in REAL:
                rs[k] = None
        if rs.get("pushed") not in ("warn", "crit"):
            rs["pushed"] = None
        for k in ("find", "advice"):                 # the words of a stale finding after a restart: a msg of ours
            if not _our_msg(rs.get(k)):
                rs[k] = None
        for k in ("value", "eval_at", "since"):
            if _num(rs.get(k)) is None:
                rs[k] = None
        sub = rs.pop("sub", None)                    # REV-ns-a12-2 З-8: the level of each part, not of the rule
        if isinstance(sub, dict):
            good = sorted((k, v) for k, v in sub.items() if isinstance(k, str) and len(k) <= 256 and v in REAL)
            if good:
                rs["sub"] = dict(good[:SUB_MAX])
        qw = _num(rec.get("quiet_until_wall"))
        if qw is not None and now is not None and qw > now:
            rs["quiet_until"] = mono + (qw - now)
        rs["level"] = "wait"
        rules[rid] = rs
    norm = ((doc.get("norm") or {}).get("rtt") or {}) if isinstance(doc.get("norm"), dict) else {}
    return {"v": V, "rules": rules,
            "norm": {"rtt": dict((a, [x for x in v if _num(x) is not None][-NORM_HOURS:])
                                 for a, v in norm.items() if isinstance(v, list)), "hour": None}}


# ── texts ──────────────────────────────────────────────────────────────────

def _fmt_float(v, lang):
    from .. import i18n
    r = round(float(v), 2)
    digits = 0 if r == int(r) else (1 if round(r, 1) == r else 2)
    return i18n.fmt_num(r, lang, digits=digits) if digits else i18n.fmt_num(r, lang)


def _clock_text(ts):
    import time
    try:
        from .. import tz as ktz
        t = ktz.local(ts)
    except Exception:                                # noqa: BLE001 — no zone module or a bad zone: UTC+03:00
        t = time.gmtime(ts + 3 * 3600)
    return "%02d:%02d" % (t.tm_hour, t.tm_min)


def say(m, lang=None):
    """The text of a msg of this module in ``lang``: nested msgs said too, numbers in the language («2,4» / «2.4»,
    thousands apart), an argument named ``*_at`` — HH:MM of the server's clock."""
    from .. import i18n
    if not isinstance(m, dict) or not isinstance(m.get("key"), str):
        return "" if m is None else str(m)
    kw = {}
    args = m.get("args") if isinstance(m.get("args"), dict) else {}
    for k, v in args.items():
        if isinstance(v, dict):
            kw[k] = say(v, lang)
        elif isinstance(v, list):                   # a list of names / msgs: «DNS шлюза, VLESS»
            kw[k] = ", ".join(say(x, lang) for x in v)
        elif k.endswith("_at") and _num(v) is not None:
            kw[k] = _clock_text(v)
        elif isinstance(v, bool) or v is None:
            kw[k] = "" if v is None else str(v)
        elif isinstance(v, float):
            kw[k] = _fmt_float(v, lang)
        elif isinstance(v, int):
            kw[k] = i18n.fmt_num(v, lang)
        else:
            kw[k] = v
    text = i18n.lookup(m["key"], lang)
    if isinstance(text, str) and "|" in text and _num(args.get("n")) is not None:
        return _form(m["key"], text, args["n"], lang, kw)
    return i18n.tr(m["key"], lang, **kw) if kw else i18n.tr(m["key"], lang)


def _form(key, text, n, lang, kw):
    """A text with the forms of a number «a|b|c» by ``n`` (REV-ns-a12 З-5: «1 раз», «2 раза», «5 раз»); a fraction —
    the form of «2,4 раза» / «2.4 times»."""
    from .. import i18n
    lg = i18n.normalize(lang, "en") if lang else i18n.get_lang()
    if i18n.catalog(lg).get(key) is None:
        lg = i18n.DEFAULT
    forms = text.split("|")
    idx = 1 if float(n) != int(n) else i18n.plural_index(int(n), lg)
    return i18n._format(forms[min(idx, len(forms) - 1)], kw)       # as tr(): a missing value stays {name}


def event_text(ev, lang=None):
    """The text of an event ``host_*`` («Сервер: {find}», «Сервер: стало легче — {find}», «Сервер: снова в норме —
    {what}», «Сервер: {what} — не проверяется: {why}»)."""
    return say({"key": ev.get("key"), "args": ev.get("args") or {}}, lang) if isinstance(ev, dict) else ""
