"""The collector: ``kaskad-gw-collect.service`` runs ``kaskadctl gw collect`` — every 10 s (spec §1.3):

1. exits watchdog (``exits_tick``): a leg without its interface or without its rule/routes (a restart of
   systemd-networkd) -> ``gokaskad --egress-awg-heal``: that leg alone is brought up or repaired in place,
   the working legs are not touched (``exits.ensure_legs``); probe every exit (AmneziaWG: a connection through
   each leg itself); the snapshot /etc/kaskad/gw/exits.json changes only when the state of an exit or the pool
   changes (exits.merge: unknown answers keep the state; dead when half of the checks of the last 2 min failed, 4
   checks at the least, back after 2 good ones — the same for AmneziaWG and VLESS, 3.3.5; the windows of the checks
   live in /run; while an AmneziaWG exit fails, the next round comes in RECHECK s); then gw_route/gw_live follow
   (``net.update_map`` — one nft transaction, only if they differ). A changed pool (keys added/removed in
   «Прокси для API Telegram») also rebuilds xray, the DNS upstream and the whole gw part (each VLESS exit
   has its own TPROXY rules), and forgets the connections of the lines that now hold another exit (their
   marks lead elsewhere: ``exits.renumbered``, ``net.flush_marks``) — install.sh runs ``gw apply`` right after
   a change of the pool anyway, this is for a change made elsewhere. Transitions are logged only: ``GW exit e-…: не работает`` / ``…: снова работает``.
   The gateway part of the table and its routing (the kill switch too) are checked every round and put
   back at once — the minute watchdog would leave clients unprotected up to a minute after ``nft flush
   ruleset``; after the part was missing the connections of the clients are flushed (conntrack) and the networks
   of Russia are loaded again at once (``rules.timer.after_restore``). In the first minutes after a reboot
   (``kaskad.boot``) what every reboot loses is put back with a calm line, not as a breakdown; the first whole
   round writes the mark that ends that.
   Right after a reboot the devices moved to another exit are told in one calm line (``_tell_moves``).
2. sessions of the inbounds (``adapter.status()`` + what hooks recorded) -> /run/kaskad/gw/sessions.json; the
   interfaces of WireGuard/AmneziaWG under watch (``ifwatch.observe``: recreated, started again, sessions reset — who).
   The same sessions go into the activity of devices (SPEC-activity §3.2, ``_activity_round``): windows of 5 min
   into metrics.db, the accumulator in /run/kaskad/gw/activity.json — no new command.
3. the light health of the node (A5, SPEC-node-score §4 A5): ``_host_round`` after the work of the exits —
   ``hostprobe.read`` (/proc, once a minute statvfs and cgroups, once an hour the certificates) → ``hostmon.derive`` →
   ``hostmon.evaluate`` (the automaton of the rules, its events go to the feed) → ``live.json host.health``, the alarms
   into the problems; /var/lib/kaskad/hostmon.json on a change of the levels and once an hour; the light probes of the
   network (``hostping``) and the control work go in the pause between rounds, ``health.cost`` — the CPU of all that
   (``time.process_time``) over the hour.
4. the live snapshot for the web panel and the bot -> /run/kaskad/live.json (``kaskad.gw.live``, wave 3): every
   round, and while a panel watches (the beacon /run/kaskad/live.want younger than 30 s) also every 2 s in the
   pause between rounds — those live rounds only read peer counters (one ``transfer`` per WireGuard-style
   interface), /proc and /sys. The adapters' status of a round is taken once and shared by 2. and 3.
   3.3.7.11 (the owner 27.10 «всё до 5 с»): while a panel watches, what «Пользователи» and «Выходы» show is never
   older than LIGHT_MAX s — a step of the pause that would leave it older runs a light round instead of a live one
   (``_light_round``: the status of the inbounds → who is online and the open window of activity, on a copy of the
   accumulator — its windows go by the full rounds only; the counters of the exits → their open minute). Without a
   panel the pace is the one before: a full round every 10 s, nothing between.

Probes run WITHOUT the state lock (dead legs cost one wait of up to 4 s); only the commit — snapshot, xray/DNS,
gw_route — takes it for a moment (waiting 2 s at most, else the round is skipped), so changes of people
and the minute watchdog never interleave with it and are never held up by probes. Errors of one tick are
logged (hourly at most) and the loop goes on.
"""

import copy
import hashlib
import ipaddress
import json
import os
import time

from .. import boot, paths, secrets, state, tz, util
from ..errors import KaskadError, LockedError, NestedLockError
from ..i18n import tr
from ..rules import timer as rules_timer
from . import addr, exits, ifwatch, live, model, net, peek
from . import sys as gsys

UNIT = "kaskad-gw-collect.service"
AWGKMOD_UNIT = "kaskad-gw-awgkmod.service"        # = awgkmod.BOOT_UNIT (a test compares them)
INTERVAL = 10
RECHECK = 5                 # s to the next round while an AmneziaWG exit fails: it replaces the last check (3.3.5)
LOCK_TIMEOUT = 2.0
ERROR_LOG_EVERY = 3600
POOL_BUSY_LOG_EVERY = 600   # s: «the pool is busy, the groups are pruned later» in the journal at most so often
LIGHT_MAX = 5.0             # s: while a panel watches, «Пользователи» and «Выходы» are never older (owner 27.10)
LIGHT_LEAD = 2.0            # s: from the start of a full round to its write of live.json, until a round measured it

_now = time.time
_sleep = time.sleep
LIVE = live.Live()          # the live snapshot of this collector process (events, transitions, CPU sample)
_live_errors = {}           # kind of error -> when it was last logged (live.json never fails a round)
EGRESS_API_PORT = 10851     # the API of the xray of «Прокси для API Telegram» (install.sh EGRESS_API_PORT; §2.9)
TOFU_EVERY = 3600           # s: how often the round looks for the pins put off on the update (egress_parse «tofu»)
_tofu_at = [0.0]


def sessions_path():
    return os.path.join(paths.gw_run_dir(), "sessions.json")


def unit_text():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: gateway watchdog of the exits and sessions",  # one text for every language (3.3.2 №3)
        # after the module of AmneziaWG (kaskad-gw-awgkmod.service decides at boot what the AWG inbounds and legs run
        # on): the first round must not bring legs up or probe them before that decision (T-M6-qs330b №8)
        "After=kaskad-net.service network-online.target %s" % AWGKMOD_UNIT,
        "",
        "[Service]",
        "Type=simple",
        "ExecStart=%s gw collect --interval %d" % (paths.kaskadctl(), INTERVAL),
        "Restart=always",
        "RestartSec=10",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def _jtr(key, **kw):
    """A journal line of the catalog: in the language of the server, whatever the language of the caller."""
    with util.journal_lang():
        return tr(key, **kw)


def _name(st, pool_exits, eid):
    for e in pool_exits:
        if e["id"] == eid:
            return exits.label(st, e)
    return eid


def _why(rec, probed, now):
    """«нет ответа 30 с, рукопожатие 2 мин назад» of an exit that changed its state — the log line."""
    rec = rec or {}
    fs = rec.get("fail_since") if not rec.get("alive") else None
    stale = rec.get("detail_code") == "detail.xray_stale"      # 3.3.5: xray stopped probing — not what its last probe said
    src = probed if (probed.get("detail") or probed.get("detail_code")) and not stale else rec    # the code of the detail
    text = exits.why_text(dict((k, src[k]) for k in exits.DETAIL_KEYS if k in src),
                          max(0, int(now - fs)) if isinstance(fs, (int, float)) and not isinstance(fs, bool) else None)
    hs = probed.get("handshake")
    if isinstance(hs, (int, float)) and not isinstance(hs, bool):
        text += tr("gw_collect.handshake_ago", ago=exits.ago_text(hs))
    if not rec.get("alive") and not stale:      # 3.3.5: what xray's observatory says of a VLESS exit (delay, its error)
        d, err = probed.get("obs_delay"), util.redact(str(probed.get("obs_error") or ""))[:exits.OBS_ERROR_MAX]
        if err:
            text += tr("gw_collect.obs_error", delay="—" if d is None else d, err=err)
        elif isinstance(d, int) and not isinstance(d, bool):
            text += tr("gw_collect.obs_delay", delay=d)
    return text


# ── stage 1: what the collector keeps between its ticks (SPEC-stage1-exits §2.7, §3.2, §4.6, §5.1, §5.6) ──

MINUTE_S = 60               # metrics.db m1, traffic.json, the counters of the quotas (§3.5, §5.6)
CLOCK_LOG_S = 24 * 3600     # s: the clock not synchronised — a line in the journal this often (REV-s1-t4 З3)
CLOCK_PROBLEM_S = 3600      # s: … and a problem of the gateway after this long, when an exit has a quota
GW_ALL_CHECK_S = 300        # ``xray api bi`` of gw-all: the override still there (§2.6)
NO_BALANCER = "\0no-balancer"   # MEM.gw_all["target"] while the config has no gw-all (KT3-8): never a real target
LAZY_TRAFFIC_S = 600        # «Лениво»: the active probes only while the group had traffic for the last 10 min (§3.2)
LAZY_WAKE_S = 300           # T-q Н-lazy: … and a good AmneziaWG exit of an idle lazy group still once in 5 min — a leg
                            # that died under its clients moves no bytes («quiet»), it was never checked again
RTT_TRAFFIC_S = 60          # the response probe of AmneziaWG: max(3P, 30) with traffic for the last minute, else 12P
TRAFFIC_MIN_BYTES = 20000   # bytes of a tick through an exit that count as its traffic (its own probes are far less)
REJECT_TELL_S = 3600        # ``xray_rejected``: the same refusal again in the feed at most once an hour (§2.7)
XRAY_LOCK_WAIT = 5          # s the collector waits for the xray lock, else the next tick (§2.5)
HOLD_RETRY_S = 30           # s between two full applies for legs held by install.sh (REV-s1-q4-2 С-1)
XPORTS_RETRY_S = 60         # s between two full applies for gw_xports that failed, the pool unchanged (REV-s1-b1 П1)
OBS_QUIET_S = 60            # s: the metrics of xray did not answer — a pool of AmneziaWG alone asks again this late (П5)
HOST_DISK_S = 3600          # s: hostmon.json at least this often (and at every change of the levels said, §3.7)
HOST_HOUR_S = 3600          # s: the hour set of hostprobe (the certificates)
HOST_COST_S = 3600          # s: the window of health.cost and its line in the journal (REV З-4)


def _mono():
    """The clock of the durations of the collector — ``exits._mono`` (time.monotonic; one clock for the probes, the
    windows and the start of the units: tests patch that one)."""
    return exits._mono()



class Mem(object):
    """What the collector process keeps between ticks: the health of the exits (their windows live here and in /run;
    the states — in the snapshot), the pins (``pins.Store``), the counters of the quotas (``pins.TrafficStore``), the
    writer of metrics.db, the last decision, the network of the node, when each exit was probed, the overrides put."""

    def __init__(self):
        self.reset()

    def reset(self):
        old = getattr(self, "metrics", None)
        if old:                     # the writer of the memory dropped is closed (its base and flock), not left to the GC
            try:
                old.close()
            except Exception:
                pass
        self.health = None          # {eid: ExitHealth} of the last tick; None — from the snapshot at the first tick
        self.pins = None            # pins.Store, loaded at the first tick
        self.traffic = None         # pins.TrafficStore and its document
        self.tdoc = None
        self.tsig = None            # (mtime, size) of traffic.json as written: another writer (a restore) — read again
        self.metrics = None         # metrics.Writer; False — none (no sqlite3, another writer)
        self.decision = None
        self.node = {"offline": False, "since": None, "ru_offline": False}
        self.node_restored = False  # the outage left open by the previous process taken (REV-s1-t4 З1), once
        self.unsync_since = None    # mono since the clock is seen not synchronised (no quota counted, REV-s1-t4 З3)
        self.unsync_told = None     # mono of the last journal line of it (once in CLOCK_LOG_S)
        self.awg_at = {}            # eid -> mono of its last probe «alive» (the period of its group, §3.2): AmneziaWG
                                    # and a Russian xray exit (its own probe, ``own_check``)
        self.awg_failed = set()     # … whose last probe failed: again in RECHECK s (3.3.5)
        self.http_at = {}           # eid -> mono of its last response probe (§5.1)
        self.vouch_at = {}          # eid -> last_try_time of the probe of the observatory that stood for its own (Н3)
        self.obs_quiet_until = 0    # mono: before it a pool without xray exits does not ask the metrics of xray (П5)
        self.bytes = {}             # eid -> rx + tx (cumulative) read last tick
        self.busy_at = {}           # eid -> mono of the last tick with traffic through it
        self.counters = {}          # eid -> (rx, tx) of this tick (None: not read)
        self.minute_at = None       # mono of the last minute's work
        self.samples = {}           # eid -> [(ok, rtt)] of the minute (m1)
        self.minute_bytes = {}      # eid -> (rx, tx) at the last point of m1
        self.gw_all = {"target": None, "start": None, "checked": None}
        self.bot = {"target": None, "start": None}
        self.started = False        # the first tick of the process: one render and the comparison with the disk (§2.5)
        self.rejected = None        # (hash of the refusal, mono) — xray_rejected told
        self.ru_open = 0            # B7: the number of xray_ru_open told (0 — «closed» told or nothing open)
        self.probe_seq = None       # the last seq of probe-results.json taken (None: from now on)
        self.results = {}           # eid -> {"geo", "speed_mbps", "services"} of results not given to health yet
        self.geo_pass = None
        self.xports_failed = None   # mono of the last full apply for gw_xports that failed (None: none since it worked)
        self.hold_try = None        # mono of the last full apply for the held legs (REV-s1-q4-2 С-1)
        self.people = None          # hostpeople.People of the process (B17, NS-D26): the hour of «Связь с людьми»
        self.act = None             # the activity of devices (SPEC-activity §3.2): ``_act_mem()`` at the first round
        self.rxtx = {}              # T15 Q3: eid -> (rx, tx) read last tick — «подозрение» of «Лениво» (``_note_suspect``)
        self.suspect = {}           # … eid -> ticks in a row its sending grew and nothing came back (SPEC-groups §6.1)
        self.ru_restored = False    # the Russian outage left open by the previous process taken (``_restore_ru_node``)
        self.reresolve = None       # {eid: wall of the last try} of ``--egress-reresolve`` (§6.5; /run, ``_reresolve``)
        self.shown_at = None        # mono of the last write of what the pages show (a full or a light round), 3.3.7.11
        self.shown_lead = LIGHT_LEAD    # s from the start of the last full round to that write
        self.light_pool = None      # the pool of the last full round: the counters of the light rounds
        self.open_base = None       # metrics_ts of the point ``minute_bytes`` belong to (None: no point written)
        self.mip_fp = None          # PLAN-multi-ip Ш3/Ш4: mip.fingerprint of the last apply (None — none in this process)
        self.mip_at = None          # mono of the last look at the addresses of the machine (mip.watch)
        self.mip_gone = None        # mip.gone_state told last (the journal says the transitions)
        self.mip_legs = None        # mip.leg_map of the last apply (REV-mip-s34 С-3: conntrack of the changed legs)
        self.mip_next = None        # mip.leg_map of this round, kept by _mip_applied after an apply
        self.addr6 = None           # 3.3.7.15 F10: addresses6 of the cache seen last (None — not yet in this process)
        self.addr6_mtime = None     # mtime of the cache file then (the cache is read again only when it changed)
        self.host_reset()

    def host_reset(self):
        """The light health (A5): the automaton is read from hostmon.json again at the next round (a new process, or
        the gateway on again — the bot may have been its owner meanwhile, §2.1 «Смена хозяина»)."""
        self.host_state = None      # the state of hostmon; None — hostmon.json not read yet
        self.host_raw = None        # the raw of the last full round (the deltas of hostmon, not of live.Host)
        self.host_min_at = None     # mono of the last minute set / hour set of hostprobe
        self.host_hour_at = None
        self.host_disk_at = None    # mono of the last write of hostmon.json
        self.host_sig = None        # the levels said, as written last
        self.host_hint = None       # wall: the hint of the probes told (hostmon.json «probes_hint»)
        self.host_level = None      # (level of the light, wall since)
        self.host_cfg = None        # model.hostmon of the last round: the pause asks the probes by it
        self.host_own = ()
        self.host_busy = None       # busy % of the last full round (the control work runs only below 50 %)
        self.ping = None            # the last series of hostping not given to hostmon yet
        self.ping_at = None
        self.prober = None          # hostping.Prober / Canary of the process
        self.canary = None
        self.canary_at = None       # mono and ms of the last control work
        self.canary_ms = None
        self.cost_s = 0.0           # CPU s of the new part in the window of health.cost
        self.cost_since = None      # wall of the start of the window
        self.people_plan = None     # hostpeople.plan of the last minute of the round; None — the measure is off


MEM = Mem()


def _policy(st, pool_exits):
    """The effective policy with every exit of the pool placed (as the collector places them, SPEC-groups §2.4)."""
    from . import policy as P
    subs = None
    try:
        from . import exitsubs
        subs = exitsubs.load()
    except Exception:                                # noqa: BLE001 — the hint of a subscription is only a hint
        subs = None
    return P.load(st, pool_exits, subs)


def _role(pol, e):
    from . import policy as P
    grp = P.group_of(pol, e["id"])
    return (grp or {}).get("role") or exits.role_of(e)


def _usable_h(h):
    s = (h or {}).get("state")
    if s == "unknown":
        s = (h or {}).get("shown")
    return s in exits.USABLE


def _off_quota(h):
    """Off by its quota with ``action = remove``: no probe of any kind until the day of the reset (§5.6, О8)."""
    h = h or {}
    return h.get("state") == "off" and "quota" in (h.get("reasons") or []) and \
        (h.get("quota") or {}).get("action") == "remove"


def _health_now(snap=None):
    """{eid: health-like record} for the plan of the probes: the memory, else the snapshot (the first tick)."""
    if MEM.health is not None:
        return MEM.health
    snap = exits.load_snapshot() if snap is None else snap
    out = {}
    for eid, rec in (snap.get("exits") or {}).items():
        if isinstance(rec, dict):
            s, shown = exits.health_of(rec)
            out[eid] = {"state": s, "shown": shown, "reasons": rec.get("reasons") or [], "quota": rec.get("quota")}
    return out


def _busy(eids, mono, within):
    return any(mono - MEM.busy_at.get(e, -1e18) <= within for e in eids)


def own_check(e):
    """The exit is checked by a probe of the collector, at the period of its group (§3.2): AmneziaWG (its mark) and a
    RUSSIAN xray exit (its ``probe-<id>`` to the Russian targets — T-q круг 3, З-2: never the observatory, whose target
    is foreign). A foreign xray exit — the observatory, every round."""
    return e.get("kind") == "awg" or (exits.is_xray(e) and exits.role_of(e) == "ru")


def _socks_port(e):
    from . import xray
    return xray.probe_port(e)


def _lazy_wake(eid, mono):
    """An idle lazy AmneziaWG exit is checked anyway (T-q Н-lazy): its last check failed (the recheck — «упал» needs
    more than one), or none for LAZY_WAKE_S. The reaction of «Лениво» to a dead leg: LAZY_WAKE_S + the window."""
    last = MEM.awg_at.get(eid)
    return eid in MEM.awg_failed or last is None or mono - last >= LAZY_WAKE_S


def probe_plan(st, pol, pool_exits, mono):
    """What this round probes (§3.2, §5.1): {"only": ids, "targets": {eid: [(host, port)]}, "http": [(eid, mark, url)],
    "socks": {eid: (port, [(ip, port)], name)} (``exits.socks_probe``), "idle": ids of the good exits of a lazy group
    without traffic, "off": ids off by the quota}.

    * foreign xray exits — every round (the one observatory reads them; a repeated probe counts once — ``health``);
    * AmneziaWG and a Russian xray exit (``own_check``; the latter through its ``probe-<id>`` — «socks») — once in P of
      its group (``policy.period``; the recheck of a failed one in RECHECK s, 3.3.5);
      «Лениво» — only while the group had traffic for the last 10 min, its exits not good — always (every 15 s), a good
      one of an idle group — once in LAZY_WAKE_S and its recheck (``_lazy_wake``);
      the response probe — ``max(3P, 30)`` s with traffic through the exit for the last minute, else ``max(12P, 120)``;
    * an exit off by its quota with ``action = remove`` — nothing at all."""
    from . import policy as P
    health = _health_now()
    out = {"only": set(), "targets": {}, "http": [], "socks": {}, "idle": set(), "off": set(), "period": {}}
    for e in pool_exits:
        eid = e["id"]
        h = health.get(eid)
        if _off_quota(h):
            out["off"].add(eid)
            continue
        grp, _ = P.tier_of(pol, eid)
        lazy = P.is_lazy(grp)
        if lazy and _usable_h(h) and not _busy(P.exits_of(grp), mono, LAZY_TRAFFIC_S):
            out["idle"].add(eid)
            if own_check(e) and not _lazy_wake(eid, mono) and not _suspect_due(eid, mono):
                continue                                # the good exits of an idle lazy group: not checked
        if not own_check(e):
            out["only"].add(eid)
            continue
        kind = "awg" if e.get("kind") == "awg" else "xray"
        p = P.period(pol, grp, kind, pool_exits) if grp else exits.CHECK_PERIOD
        last = MEM.awg_at.get(eid)
        due = last is None or mono - last >= p - exits.CHECK_SLACK or \
            (eid in MEM.awg_failed and mono - last >= RECHECK - 1)
        if not due:
            continue
        out["only"].add(eid)
        out["period"][eid] = p
        role = ((grp or {}).get("checks") or {}).get("targets") or _role(pol, e)
        tg = exits.probe_targets(role)
        if kind == "xray":
            out["socks"][eid] = (_socks_port(e), tg["dns"], tg["dns_name"])
            continue
        out["targets"][eid] = tg["tcp"]
        if not tg.get("http"):                          # T15 Q3 (SPEC-groups §6.1, Б3): the Russian targets — no HTTP
            out.setdefault("conn", set()).add(eid)      # to Yandex; the response is the time of the TCP connection
            continue
        hp = P.awg_rtt_period(p, traffic=_busy([eid], mono, RTT_TRAFFIC_S))
        hl = MEM.http_at.get(eid)
        if e.get("mark") and (hl is None or mono - hl >= hp - exits.CHECK_SLACK):
            out["http"].append((eid, e["mark"], tg["http"]))
    return out


def _vouch_cands(pool_exits, plan):
    """The AmneziaWG exits of this round a good probe of the observatory may stand for (``vouched_awg``): due, the last
    own check good, the targets those of «abroad» (the observatory's)."""
    targets = plan.get("targets") or {}
    abroad = list(exits.probe_targets("abroad")["tcp"])
    return [e for e in pool_exits if e.get("kind") == "awg" and e["id"] in targets and e["id"] not in MEM.awg_failed
            and list(targets[e["id"]]) == abroad]


def vouched_awg(runner, pool_exits, plan, doc):
    """The AmneziaWG exits due this round whose probe «жив» a GOOD probe of the gateway's observatory stands for (Н3,
    T-KT1: one exit was probed twice every 10 s — by the observatory through its outbound, by the collector through its
    mark): the observatory got an answer through this very leg within the period of the exit (+ CHECK_SLACK), that
    probe not used before, the last check of the exit good. A failure is never borrowed — a silent observatory, a failed
    or an old probe of it (its DNS goes through gw-all, another exit) and a failing leg (the recheck of 3.3.5) take our
    own connection as before. Only an exit checked by the targets of «abroad» — those the observatory probes
    (cp.cloudflare.com): a group with other ``checks.targets`` decides by its own probe (REV-s1-kt1b П1). -> set of ids."""
    out = set()
    if not doc:
        return out
    cands = _vouch_cands(pool_exits, plan)
    if not cands:
        return out
    obs = exits.observatory_detail(runner, doc) or {}
    now = exits._now()
    for e in cands:
        eid = e["id"]
        o = obs.get(e.get("tag"))
        t = (o or {}).get("try_at")
        p = (plan.get("period") or {}).get(eid) or exits.CHECK_PERIOD
        if not (o and o.get("alive") and t and 0 <= now - t <= p + exits.CHECK_SLACK and MEM.vouch_at.get(eid) != t):
            continue
        out.add(eid)
        MEM.vouch_at[eid] = t
    return out


def _counters(pool_exits, doc):
    """{eid: (rx, tx)} of this round: kawgN from /sys, the xray exits from ``/debug/vars`` (§5.6); an exit whose counter
    was not read is not there (its delta is no 0, T3)."""
    out = {}
    xr = exits.xray_counters(doc, [e["tag"] for e in pool_exits if exits.is_xray(e) and e.get("tag")]) if doc else {}
    for e in pool_exits:
        if e.get("kind") == "awg" and e.get("iface"):
            got = exits.iface_counters(e["iface"])
        elif exits.is_xray(e):
            got = xr.get(e.get("tag"))
        else:
            got = None
        if got is not None:
            out[e["id"]] = got
    return out


def _note_traffic(counters, mono):
    for eid, (rx, tx) in counters.items():
        cur = rx + tx
        last = MEM.bytes.get(eid)
        if last is not None and (cur - last if cur >= last else cur) >= TRAFFIC_MIN_BYTES:
            MEM.busy_at[eid] = mono
        MEM.bytes[eid] = cur


def node_check(pool_exits, probed, pol, plan):
    """The network of the node (§4.6), BEFORE ``health.evaluate``: an exit «answered» — a fresh good probe (AmneziaWG:
    this round; xray: ``last_try_time`` younger than 2 × observatory_s). Every probed exit of a side silent (or the node
    offline already) -> the direct probe without a mark to a Russian and a foreign target. Exits without a probe this
    round (off by the quota, the good exits of an idle lazy group) take no part; a good exit of an idle lazy group woken
    this round (``_lazy_wake``: in «only») does — T-q круг 3, З-3. A Russian xray exit (its own probe, «socks») is
    fresh this round. -> {"offline", "ru_offline", "checked", "direct": {"abroad", "ru"}}."""
    from . import policy as P
    obs_s = P.observatory_s(pol, pool_exits) or exits.CHECK_PERIOD
    sides = {"abroad": [], "ru": []}
    by_ru = {}                                      # T15 Q3: the Russian side by group — «все её выходы» (§4.6)
    skip = set(plan.get("off") or ()) | (set(plan.get("idle") or ()) - set(plan.get("only") or ()))
    own = plan.get("socks") or {}
    for e in pool_exits:
        p = probed.get(e["id"])
        if p is None or e["id"] in skip or not exits.parsed(e):
            continue
        a = p.get("alive")
        if a is None:
            continue                                # unknown (a leg booting, xray young): says nothing
        if exits.is_xray(e) and e["id"] not in own:
            age = p.get("try_age")
            a = bool(a) and age is not None and age <= 2 * obs_s
        sides["ru" if _role(pol, e) == "ru" else "abroad"].append(bool(a))
        if _role(pol, e) == "ru":
            grp = P.group_of(pol, e["id"])
            if grp is not None:
                by_ru.setdefault(grp.get("id"), []).append(bool(a))
    silent = dict((k, bool(v) and not any(v)) for k, v in sides.items())
    silent["ru"] = silent["ru"] or any(bool(v) and not any(v) for v in by_ru.values())
    out = {"offline": False, "ru_offline": False, "checked": False, "direct": {}}
    if not (silent["abroad"] or silent["ru"] or MEM.node.get("offline") or MEM.node.get("ru_offline")):
        return out
    ab = exits.direct_probe(exits.probe_targets("abroad")["direct"])
    ru = exits.direct_probe(exits.probe_targets("ru")["direct"])
    has_ru = any(isinstance(g, dict) and g.get("role") == "ru" for g in pol.get("groups") or [])
    out.update(checked=True, direct={"abroad": ab, "ru": ru}, offline=not ab and not ru,
               ru_offline=bool(has_ru and ab and not ru))
    return out


def _restore_node():
    """The first round of the process with a pool (REV-s1-t4 З1): an outage the previous process left open in live.json
    (it was restarted while offline) is the outage of this one — the node is checked at once (``node_check``); the network
    there already, ``node_online`` closes it with the end at the last round of that process (``restart``: the real end is
    not known, it is at that time or later)."""
    if MEM.node_restored:
        return
    MEM.node_restored = True
    if MEM.node.get("offline"):
        return
    got = live.outage_open()
    if got:
        MEM.node.update(offline=True, since=got["since"], last=got["last"])


def _own_probes(items):
    """The probes of the Russian xray exits (``exits.socks_probe``) started now, in a thread: -> a callable giving
    their answers — ``exits.probe`` calls it after the connections of the legs, so a round waits once for both."""
    if not items:
        return {}
    import threading
    box = {}
    th = threading.Thread(target=lambda: box.update(exits.socks_probe(items)), daemon=True)
    th.start()

    def got():
        th.join(exits.PROBE_TIMEOUT + 2)
        return dict((k, box.get(k, (None, None))) for k in items)
    return got


# ── T15 Q3: the Russian groups (SPEC-groups §6.1, §6.4, §6.5; SE §4.6) — functions of their own ──

SUSPECT_TICKS = 2           # «подозрение» (§6.1): the sending of an exit grew, nothing came back — this many ticks in a row
RERESOLVE_DEAD_S = 60       # §6.5 (Б5): an AmneziaWG exit with a NAME in its key dead this long — its name again …
RERESOLVE_EVERY_S = 300     # … not more often than once in 5 min an exit (``/run/kaskad/reresolve.json``) …
RERESOLVE_TIMEOUT = 15      # … ``--egress-reresolve N`` after the state lock, this long at the most


def _suspect_due(eid, mono):
    """An idle lazy exit under suspicion (§6.1): its sending grew and nothing came back SUSPECT_TICKS ticks in a row
    (a person began to use a router that died while nobody did) — checked now, not in LAZY_WAKE_S; still not more often
    than once in the period of «Лениво» (15 s, «не больше одной пробы за 15 с»)."""
    from . import policy as P
    if MEM.suspect.get(eid, 0) < SUSPECT_TICKS:
        return False
    last = MEM.awg_at.get(eid)
    return last is None or mono - last >= P.LAZY_P - exits.CHECK_SLACK


def _note_suspect(counters):
    """«Подозрение» from the counters of this round ({eid: (rx, tx)}, ``_counters``): tx grew, rx did not — one tick
    more; anything else (rx grew, nothing sent, a counter that went back or was not read) — none. The passive sign
    «rx grew = alive» is NOT taken (SPEC-groups §6.1 «Сверка 24.10»): packets keep coming in through a leg whose
    sending is cut (stand T-G1, C3-1) — only an answer to our own probe says «жив»."""
    for eid in list(MEM.suspect):
        if eid not in counters:
            MEM.suspect.pop(eid, None)
    for eid, (rx, tx) in counters.items():
        last = MEM.rxtx.get(eid)
        MEM.rxtx[eid] = (rx, tx)
        if last is None or rx < last[0] or tx < last[1]:
            MEM.suspect.pop(eid, None)
            continue
        if tx > last[1] and rx == last[0]:
            MEM.suspect[eid] = MEM.suspect.get(eid, 0) + 1
        else:
            MEM.suspect.pop(eid, None)
    for eid in [x for x in MEM.rxtx if x not in counters]:
        MEM.rxtx.pop(eid, None)


def _conn_rtt(probed, ids):
    """The response of the exits checked without an HTTP probe (the Russian targets, §6.1): the time of the TCP
    connection of their probe «жив» (``exits.probe`` → ``conn_ms``). ``conn_ms`` never stays in the answer."""
    for eid, p in (probed or {}).items():
        ms = p.pop("conn_ms", None) if isinstance(p, dict) else None
        if ms is not None and eid in (ids or ()) and p.get("alive"):
            p["rtt"] = ms


def _restore_ru_node():
    """The first round of the process (SE §4.6, like ``_restore_node``): a Russian outage the previous process left open
    in live.json (a ``node_ru_offline`` with no ``node_ru_online`` after it) is the outage of this one — its ``since``
    stays and its end is told with ``restart`` when the network is found back."""
    if MEM.ru_restored:
        return
    MEM.ru_restored = True
    if MEM.node.get("ru_offline"):
        return
    got = live.ru_outage_open()
    if got:
        MEM.node.update(ru_offline=True, ru_since=got["since"], ru_last=got["last"])


def _ru_node(node, now, out):
    """The Russian side of the network of the node (SE §4.6, SPEC-groups §6.4) after the direct probe of this round
    (``node_check``): «прямо к российской молчит, к зарубежной отвечает» — ``node.ru_offline``: the Russian groups are
    frozen (health, assign), event ``node_ru_offline`` (``since``); the first good direct Russian probe — off, the
    windows of the Russian exits anew (``out["ru_back"]``), the summary ``node_ru_online`` (since, until, minutes;
    ``restart`` — found back by a restarted collector). The whole network gone (``node.offline``): the Russian outage
    stays as it is — frozen with the rest, told when the Russian addresses answer again."""
    if MEM.node.get("offline"):
        return
    was = bool(MEM.node.get("ru_offline"))
    off = bool(node.get("ru_offline"))
    if off and not was:
        MEM.node.update(ru_offline=True, ru_since=now, ru_last=None)
        out["events"].append({"kind": "node_ru_offline", "since": int(now)})
    elif was and not off:
        since, last = MEM.node.get("ru_since"), MEM.node.get("ru_last")
        until = min(now, last) if last is not None else now
        MEM.node.update(ru_offline=False, ru_since=None, ru_last=None)
        ev = {"kind": "node_ru_online", "since": int(since or until), "until": int(until),
              "minutes": int(max(0, until - (since or until)) // 60)}
        if last is not None:
            ev["restart"] = True
        out["events"].append(ev)
        out["ru_back"] = True
    elif off and node.get("checked"):
        MEM.node["ru_last"] = None                  # the outage goes on under this process: its end is seen


def _reresolve_path():
    return os.path.join(paths.run_dir(), "reresolve.json")


def _reresolve_tries():
    """{eid: wall of the last try} — memory, else /run (a restarted collector keeps it until a reboot)."""
    if MEM.reresolve is None:
        try:
            with open(_reresolve_path(), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {}
        MEM.reresolve = dict((k, v) for k, v in (doc.get("tries") or {}).items()
                             if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)) \
            if isinstance(doc, dict) and isinstance(doc.get("tries"), dict) else {}
    return MEM.reresolve


def reresolve_due(pool_exits, health, now):
    """The AmneziaWG exits (any role) whose leg must look its server's NAME up again (§6.5, Б5): the key names its
    Endpoint by a domain (dynamic DNS of a home router), the exit is dead (``dead``, not turned off by the owner) for
    RERESOLVE_DEAD_S at least, its last try RERESOLVE_EVERY_S ago or more. -> [(eid, index)]."""
    tries = _reresolve_tries()
    out = []
    for e in pool_exits or []:
        if e.get("kind") != "awg" or not e.get("index"):
            continue
        host = exits.host_only(e.get("host"))
        if not host or host == "?" or _is_ip(host):
            continue
        h = (health or {}).get(e["id"]) or {}
        reasons = h.get("reasons") or []
        since = h.get("since")
        if h.get("state") != "off" or "dead" not in reasons or "disabled" in reasons:
            continue
        if not isinstance(since, (int, float)) or isinstance(since, bool) or now - since < RERESOLVE_DEAD_S:
            continue
        last = tries.get(e["id"])
        if last is not None and 0 <= now - last < RERESOLVE_EVERY_S:
            continue
        out.append((e["id"], int(e["index"])))
    return out


def _is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _reresolve(runner, pool_exits):
    """After the state lock (§6.5, Б5): ``install.sh --egress-reresolve N`` for ONE exit of ``reresolve_due`` a round
    (the round is 5–10 s: several dead names never hold it for several timeouts; the next one — the next round, its
    try is older) — a process with a timeout of RERESOLVE_TIMEOUT s; the time of the try in /run first (a hang never
    makes it every round). rc 6 — the pool is being changed right now: not a try, the next round. -> [(eid, rc)]."""
    now = _now()
    if MEM.node.get("offline"):
        return []                                   # no network at all: no name answers, the states are frozen
    due = reresolve_due(pool_exits, MEM.health, now)
    if not due:
        return []
    tries = _reresolve_tries()
    ids = set(e["id"] for e in pool_exits or [])
    for eid in [k for k in tries if k not in ids]:
        tries.pop(eid, None)                        # an exit that left the pool
    done = []
    for eid, idx in due[:1]:
        tries[eid] = now
        _reresolve_save(tries)
        res = runner.run([exits._script(runner), "--egress-reresolve", str(idx)], timeout=RERESOLVE_TIMEOUT)
        rc = getattr(res, "rc", 1)
        if rc == 6:
            tries.pop(eid, None)
            _reresolve_save(tries)
        done.append((eid, rc))
    return done


def _reresolve_save(tries):
    try:
        util.atomic_write(_reresolve_path(), json.dumps({"tries": tries}, sort_keys=True), 0o600)
    except OSError:
        pass


def probe_round(runner, legs=True, st=None):
    """The slow half of a round, WITHOUT the state lock: the pool (both roles), legs up if missing, the probes of this
    round (``probe_plan``: the period of the group, «Лениво», the quota — dead legs cost one wait of up to 4 s, all of
    them together), the response probes of AmneziaWG, the counters, the network of the node (§4.6). Returns {"pool",
    "probed", "legs", "plan", "node", "counters", "error"?}."""
    out = {"pool": [], "probed": {}, "legs": None, "plan": {"only": set(), "targets": {}, "http": [], "socks": {},
                                                            "idle": set(), "off": set()}, "node": {}, "counters": {}}
    try:
        out["pool"] = exits.pool(runner, roles=exits.ROLES)
    except KaskadError as e:
        out["error"] = str(e)
    if legs:
        out["legs"] = exits.ensure_legs(runner, out["pool"])
    pool_exits = out["pool"]
    if not pool_exits:
        return out
    if st is None:
        st = state.load()
    mono = _mono()
    pol = _policy(st, pool_exits)
    plan = out["plan"] = probe_plan(st, pol, pool_exits, mono)
    # the metrics of xray (one curl): for the xray exits every round; for AmneziaWG alone only when a probe of the
    # observatory may stand for one (``_vouch_cands``) and xray answered last time or OBS_QUIET_S ago (REV-s1-kt1b П5:
    # a node of WG inbounds alone has no xray of the gateway — no curl refused every 5–10 s)
    xr = any(exits.is_xray(e) for e in pool_exits)
    ask = not xr and bool(_vouch_cands(pool_exits, plan)) and mono >= MEM.obs_quiet_until
    doc = exits.debug_vars(runner) if xr or ask else None
    if ask:
        MEM.obs_quiet_until = 0 if doc is not None else mono + OBS_QUIET_S
    vouched = vouched_awg(runner, pool_exits, plan, doc)
    own = _own_probes(plan["socks"])
    probed = exits.probe(runner, pool_exits, only=plan["only"], doc=doc, targets=plan["targets"], measure=True,
                         vouched=vouched, own=own)
    for e in pool_exits:
        p = probed.get(e["id"])
        if p is not None and (e["id"] in plan["targets"] or e["id"] in plan["socks"]):
            MEM.awg_at[e["id"]] = mono
            if p.get("alive") is False:
                MEM.awg_failed.add(e["id"])
            else:
                MEM.awg_failed.discard(e["id"])
    _conn_rtt(probed, plan.get("conn"))
    by_url = {}
    for eid, mark, url in plan["http"]:
        if (probed.get(eid) or {}).get("alive"):
            by_url.setdefault(url, []).append((eid, mark))
    for url, items in sorted(by_url.items()):
        got = exits.http_probe([m for _, m in items], url)
        for eid, mark in items:
            MEM.http_at[eid] = mono
            if got.get(mark) is not None:
                probed[eid]["rtt"] = got[mark]
    out["probed"] = probed
    out["counters"] = _counters(pool_exits, doc)
    _note_traffic(out["counters"], mono)
    _note_suspect(out["counters"])
    _restore_node()
    _restore_ru_node()
    out["node"] = node_check(pool_exits, probed, pol, plan)
    return out


def _place(st, pool_exits):
    """Place every unplaced exit of the pool into its group (SPEC-groups §2.4) in the STORED policy and count ``rev``
    (``rev.note`` — the state lock of the tick is held; §3.1, проверка 16, 18): the open form of that group gets 409.
    Nothing stored (a node never set up) — nothing to write: the reading places them the same way. -> [(eid, gid, tid)]."""
    from . import policy as P, policy_ops, rev
    raw = P.stored(st)
    if raw is None:
        return []
    doc = P.normalize(raw)
    # KT3-1: ids no file of the pool has any more (a key deleted, a subscription that dropped or renamed one) out of
    # the tiers — read fresh under the lock of the tick (a line added meanwhile is in the file before it is placed).
    # The lock of the pool is not waited for here (REV-3371-pre З-1): the state lock is held; busy — the next tick
    gone = P.prune_gone(doc, policy_ops.known_ids(wait=0, on_busy=_pool_busy))
    if not pool_exits and not gone:
        return []
    subs = None
    try:
        from . import exitsubs
        subs = exitsubs.load()
    except Exception:                                # noqa: BLE001
        subs = None
    placed = P.place_new(doc, pool_exits, P.hints_of(st, subs)) if pool_exits else []
    if not placed and not gone:
        return []
    st["gateway"]["exit_policy"] = doc
    rev.note(st)
    state.save(st)
    if gone:
        util.log_action("GW policy: gone from the groups: %s" % ", ".join(sorted(set(e for e, _ in gone))))
    return placed or gone


def _results():
    """The results of the unit of probes the collector has not taken yet (probe-results.json, ``seq``): {eid: {"geo",
    "speed_mbps", "services": {sid: ok}}} for health, the rows for metrics.db, and the last extra pass (§4.3)."""
    from .probe import run as prun
    doc = prun.read_results()
    rows = [r for r in doc.get("results") or [] if isinstance(r, dict) and isinstance(r.get("seq"), int)]
    top = max([r["seq"] for r in rows] + [int(doc.get("seq") or 0)])
    if MEM.probe_seq is None:
        MEM.probe_seq = top                         # the first tick: only what comes from now on
        return {}, [], None
    new = [r for r in rows if r["seq"] > MEM.probe_seq]
    MEM.probe_seq = max(top, MEM.probe_seq) if new else MEM.probe_seq
    out, gpass = {}, None
    for r in sorted(new, key=lambda r: r["seq"]):
        eid = r.get("exit")
        if not isinstance(eid, str):
            continue
        x = out.setdefault(eid, {})
        if r.get("kind") == "geo" and r.get("loc"):
            x["geo"] = {"loc": r.get("loc"), "ip": r.get("ip"), "how": r.get("how") or "plan"}
        elif r.get("kind") == "speed" and r.get("ok") and r.get("mbps") is not None:
            x["speed_mbps"] = r.get("mbps")
        elif r.get("kind") == "svc" and r.get("svc"):
            x.setdefault("services", {})[r["svc"]] = bool(r.get("ok"))
        elif r.get("kind") == "geo_pass":
            gpass = {"direct_loc": r.get("direct_loc"), "locs": dict(r.get("locs") or {})}
    return out, new, gpass


def _clock_synced(runner):
    """The clock of the node is synchronised (the kernel's STA_UNSYNC is off — chrony, ntpd, timesyncd alike; else
    ``timedatectl``): before it, no counter of a quota goes into traffic.json — a jump of the clock forward would
    start a period of the future and keep it (T3, C2). One adjtimex() — ``hostprobe.clock`` (SPEC-node-score §3.1:
    the same call gives the light health its offset)."""
    from . import hostprobe
    got = hostprobe.clock()
    if got is not None:
        return bool(got["synced"])
    res = runner.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], timeout=5)
    return bool(res.ok and (res.out or "").strip() == "yes")


def _traffic_doc():
    from . import pins as PN
    if MEM.traffic is None:
        MEM.traffic = PN.TrafficStore()
    sig = _sig(paths.traffic_json())
    if MEM.tdoc is None or (MEM.tsig is not None and sig != MEM.tsig):
        MEM.tdoc = MEM.traffic.load()               # first, or another writer (a restore of .kbk, T7) rewrote it
        MEM.tsig = sig
    return MEM.tdoc


def _sig(path):
    try:
        s = os.stat(path)
        return (s.st_mtime_ns, s.st_size)
    except OSError:
        return None


def _save_traffic(force=False):
    if MEM.traffic is not None and MEM.tdoc is not None and MEM.traffic.save(MEM.tdoc, force=force):
        MEM.tsig = _sig(paths.traffic_json())


def _quota_obs(st, eid, pref, now):
    """``traffic`` of the Obs of an exit with a quota (§5.6; T3): the action by ``pins.used(...) >= limit``; the pushes
    of 80 % / 100 % — those ``quota_due`` marks now (the file is written at once then); the date of the reset by the
    period the exit counts in."""
    from . import pins as PN, policy as P
    q = pref.get("quota")
    if P.quota_gb(q) is None:          # the one test of a limit (REV-33714 С-1): 10**400 of an older file — no quota
        return None
    doc = _traffic_doc()
    z = tz.zone()
    per = PN.period_of(doc, eid, q, now, z)
    used = PN.used(doc, eid, q, now, z)
    due = PN.quota_due(doc, eid, q, now, z)
    rec = (doc.get("exits") or {}).get(eid) or {}
    told = [x for x in ((rec.get("warned") or {}).get(per) or []) if x not in due]
    if due:
        _save_traffic(force=True)
    return {"used_gb": used / 1e9, "period": per, "warned": told, "reset": PN.next_reset(q, now, z, start=per)}


def _sub_owners():
    """{exit id: {"sub", "name"}} of the keys of the subscriptions (none — the file unreadable)."""
    from . import exitsubs
    try:
        return exitsubs.owners()
    except Exception:                                   # noqa: BLE001 — the event without the name then
        return {}


def _obs(st, pool_exits, snap, probed, mono, now):
    """{eid: Obs} of every exit of the pool for ``health.evaluate`` (§4.1): its probe of this round (none — nothing new),
    the silence of xray / of a leg that does not come up (``merge``: unknown for UNKNOWN_MAX s, a frozen probe),
    ``exit_pref``, the traffic of a quota, the results of the unit of probes."""
    out = {}
    recs = snap.get("exits") or {}
    owners = None
    for e in pool_exits:
        eid = e["id"]
        pref = model.exit_pref(st, eid)
        o = {"kind": "xray" if exits.is_xray(e) else "awg", "enabled": bool(pref["enabled"] and exits.parsed(e)),
             "manual": pref.get("manual"), "manual_why": pref.get("manual_why"), "quota": pref.get("quota"),
             "country": pref.get("country"), "probes": []}
        if o["manual_why"] == "sub_confirm":            # D-068: the event names the subscription (health «sub_name»)
            if owners is None:
                owners = _sub_owners()
            o.update(owners.get(eid) and {"sub": owners[eid]["sub"], "sub_name": owners[eid]["name"]} or {})
        p = probed.get(eid)
        if p is not None and p.get("alive") is not None:
            at = mono - (p.get("try_age") or 0) if exits.is_xray(e) else mono
            o["probes"].append({"ok": bool(p["alive"]), "rtt": p.get("rtt"), "at": at,
                                "id": p.get("try_at") if exits.is_xray(e) else None})
        rec = recs.get(eid) or {}
        us = rec.get("unknown_since")
        if o["enabled"] and not rec.get("alive") and (rec.get("detail_code") == "detail.xray_stale" or (
                isinstance(us, (int, float)) and not isinstance(us, bool) and now - us >= exits.UNKNOWN_MAX)):
            o["silent"] = True
        t = _quota_obs(st, eid, pref, now)
        if t is not None:
            o["traffic"] = t
        o.update(MEM.results.pop(eid, {}))
        out[eid] = o
    return out


def _metrics_of(st, health_now, pool_exits):
    """{eid: {"kind", "rtt", "x_norm", "speed_mbps", "quota"}} of ``assign`` (the methods other than «even», §4.4)."""
    import statistics
    out = {}
    for e in pool_exits:
        h = health_now.get(e["id"]) or {}
        rtts = [s[2] for s in (h.get("samples") or [])[-10:] if isinstance(s, list) and len(s) > 2 and s[2] is not None]
        rtt = statistics.median(rtts) if rtts else None
        norm = [x for x in h.get("norm_rtt") or [] if isinstance(x, (int, float))]
        out[e["id"]] = {"kind": "xray" if exits.is_xray(e) else "awg", "rtt": rtt,
                        "x_norm": (rtt / statistics.median(norm)) if rtt is not None and norm and
                        statistics.median(norm) > 0 else None,
                        "speed_mbps": h.get("speed"), "quota": bool(model.exit_pref(st, e["id"]).get("quota"))}
    return out


def devices_by_group(st, pol, states):
    """{gid: [{"id", "fail", "exit"?}]} — the active devices whose route resolves into each group (SPEC-groups §2.7):
    ``pool`` -> «main», ``group:G`` -> G, ``exit:E`` -> the group of E (with ``exit``: assign keeps it on E while E is
    fit); ``exit:X`` with X gone from the pool -> the group where X is still listed, else «main» (§2.2, З1)."""
    out = {}
    dec = {"policy": pol, "states": states}
    for d in model.devices(st):
        if not model.device_active(st, d):
            continue
        r = model.route_of(st, d)
        if r["via"] in ("direct", "block"):
            continue
        gid, grp, missing = exits.route_group(st, r, dec)
        if gid is None:
            continue
        rec = {"id": d["id"], "fail": r.get("fail")}
        eid = model.exit_of(r)
        if eid and not missing:
            rec["exit"] = eid
        out.setdefault(gid, []).append(rec)
    _ru_devices(st, pol, out)
    return out


def _ru_devices(st, pol, out):
    """The Russian group R of ``devices_by_group`` (SPEC-groups §2.7, Q2): the active devices for which «Россия
    напрямую» acts (``rules.apply.device_rd``: the lists, the consent ⚠РФ, the profile, not «block» by choice) and whose
    ``ru_via`` resolves to R — each pinned there as by ``exits.ru_target``. Without a Russian group — nothing."""
    from . import policy as P
    ru = set(g.get("id") for g in (pol or {}).get("groups") or [] if isinstance(g, dict) and g.get("role") == "ru")
    if not ru:
        return
    from ..rules import apply as rules_apply
    for d in model.devices(st):
        if not d.get("id") or not rules_apply.device_rd(st, d):
            continue
        via = model.ru_via_of(st, d)["via"]
        if via in ru and P.group(pol, via) is not None:
            out.setdefault(via, []).append({"id": d["id"], "fail": None})


def _pins_doc(d):
    """The part of a Decision pins.json keeps (§3.4)."""
    groups = {}
    for gid, g in (d.get("groups") or {}).items():
        groups[gid] = {"pins": dict(g.get("pins") or {}), "tier_good_since": dict(g.get("tier_good_since") or {}),
                       "active_tier": g.get("active_tier"), "last_step": g.get("last_step"),
                       "told_tier": g.get("told_tier")}   # REV-s1fin С-1: the next round compares against it
    return {"version": 1, "saved": 0, "groups": groups, "gw_all": d.get("gw_all")}


REGROUP_MAX_AGE = 300       # s: F14 — a request «Разложить заново» older than this is dropped (the collector stood)
WORK = ".work"


def regroup_take(pol, now=None, late=None):
    """F14 (SPEC-R1 §1 решение 4, З-5): the requests «Разложить заново» — one file per group ``<gid>`` in
    ``paths.gw_regroup_dir()`` (the panel writes it, ``policy_ops.group_regroup``). Each is taken by ``rename`` to
    ``<gid>.work`` (atomic): a request written while this round runs is a new ``<gid>`` file the next round takes — none
    lost, no lock. A ``.work`` left by a failed round is taken again; one older than REGROUP_MAX_AGE s by its mtime (the
    collector stood: no sudden move later), of no group of the policy or with a foreign name — removed. Names starting
    with «.» (the temp files of ``util.atomic_write``) are never touched. The age — against the wall clock of the
    kernel that stamped the mtime (``time.time()``), not the clock of the round. ``late`` (a list) gets the groups whose
    request was removed for its age (П-4: the feed says so, no silent wait). -> ({gid}, [paths of the taken .work])."""
    now = time.time() if now is None else now
    d = paths.gw_regroup_dir()
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return set(), []
    gids = set(g.get("id") for g in (pol or {}).get("groups") or [] if isinstance(g, dict) and g.get("id"))
    got, taken = set(), []
    for n in names:
        if n.startswith("."):
            continue
        gid = n[:-len(WORK)] if n.endswith(WORK) else n
        p, work = os.path.join(d, n), os.path.join(d, gid + WORK)
        try:
            if gid not in gids:
                os.unlink(p)
                continue
            if p != work:
                os.rename(p, work)                     # a newer request over a .work left by a failed round: one
            age = now - os.stat(work).st_mtime
        except OSError:
            continue
        if not -REGROUP_MAX_AGE <= age <= REGROUP_MAX_AGE:
            _unlink(work)
            if late is not None and gid not in late:
                late.append(gid)
            continue
        if gid not in got:
            got.add(gid)
            taken.append(work)
    return got, taken


def regroup_done(taken):
    """F14: the ``.work`` of the requests this round took — removed after the decision was written."""
    for p in taken or ():
        _unlink(p)


def _pending_events():
    """F7: the events of the safe apply other processes wrote (``pending.peek``: the timer reverted, or did not) and
    their files — removed by ``_pending_done`` once the feed has them (З-3); never fails the round."""
    try:
        from . import pending
        return pending.peek()
    except Exception as e:                           # noqa: BLE001
        _note_error("pending_events", e)
        return [], []


def _pending_done(files):
    try:
        from . import pending
        pending.done(files)
    except Exception as e:                           # noqa: BLE001
        _note_error("pending_events", e)


def _pending_overdue(runner):
    """F7 (З-2): every round — a wait past its deadline whose timer is lost is reverted (``pending.overdue``: one
    read of a file while nothing is overdue); never fails the round."""
    try:
        from . import pending
        return pending.overdue(runner)
    except Exception as e:                           # noqa: BLE001
        _note_error("pending_due", e)
        return None


def _pending_due(runner):
    """F7 (С-2): before the first round — a wait left by a reboot, an update or a crash (``pending.expire_due``);
    never fails the loop."""
    try:
        from . import pending
        return pending.expire_due(runner)
    except Exception as e:                           # noqa: BLE001
        _note_error("pending_due", e)
        return None


def _unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def _prev_decision(pins_doc):
    """The Decision of the last tick for ``assign``: the one in memory (its ``last_since``, ``night_on``), else the
    file; the file wins when another process rewrote it (``pins.handover_file``)."""
    d = MEM.decision
    if d is None:
        return pins_doc
    out = dict(pins_doc)
    groups = {}
    for gid, g in (pins_doc.get("groups") or {}).items():
        mine = (d.get("groups") or {}).get(gid) or {}
        groups[gid] = dict(g, last_since=mine.get("last_since"), night_on=mine.get("night_on"))
    out["groups"] = groups
    return out


def fingerprint(st, d, pool_exits):
    """What the config of xray depends on in the decision (§2.7): the pool, the exits off by the quota, the target of
    every user of an xray inbound (its pin, the last step, «exit:E»), the selector of «по соединениям», the interval of
    the observatory. sha1 — compared with ``xray.decision`` (the render applied)."""
    from . import policy as P
    pol = d.get("policy") or P.load(st, pool_exits)
    xr, ru = {}, {}
    kinds = set(model.enabled_kinds(st)) & set(model.XRAY_KINDS)
    for dev in model.devices(st):
        if not kinds or not model.device_active(st, dev) or not (kinds & set(dev.get("access") or [])):
            continue
        xr[dev["id"]] = exits.target(st, dev, d)["xray"]
        rt = exits.ru_target(st, dev, d)                 # Q2 (§4.2): the Russian target of the lists «напрямую»
        if rt is not None and rt["xray"] != {"outboundTag": "direct"}:
            ru[dev["id"]] = rt["xray"]                   # «direct» (and none) — as before stage 1 part B: no key
    main = P.group(pol, P.MAIN) or {}
    spread = None
    if main.get("per_connection"):
        spread = sorted((e, s) for e, s in (d.get("states") or {}).items() if e in P.exits_of(main))
    doc = {"pool": [e["id"] for e in pool_exits], "quota_off": sorted(d.get("quota_off") or []), "xray": xr,
           "spread": spread, "obs": P.observatory_s(pol, pool_exits), "tier": (d.get("groups") or {}).get(
               P.MAIN, {}).get("active_tier") if spread is not None else None}
    if ru:
        doc["ru"] = ru
    try:                                                 # Ш4: the addresses of the xray exits and of «direct» (Н4) —
        from . import mip                                # a key only when some address is drawn (no restart after the
        mp = mip.plan(st, pool_exits)                    # update of a node without them)
        if mp.get("xsrc") or mip.direct_send(mp):
            doc["mip"] = {"xsrc": mp.get("xsrc") or {}, "direct": mip.direct_send(mp)}
    except Exception:                                    # noqa: BLE001
        pass
    return hashlib.sha1(util.json_text(doc).encode("utf-8")).hexdigest()


H_WIN = ("tries", "oks", "last_try_id", "samples")


def _window_of(h):
    """The window of health kept in /run (``exits.WINDOW_KEYS`` ``h_win``, §3.2): a restart of the collector goes on."""
    return dict((k, copy.deepcopy(h.get(k))) for k in H_WIN if h.get(k) not in (None, []))


def _restored(h, rec):
    """ExitHealth of a restarted collector: the snapshot (``from_snapshot``) + its window from /run (none after a reboot).
    The monotonic times of the window are of this boot (/run is cleared at a reboot)."""
    w = (rec or {}).get("h_win")
    if isinstance(w, dict):
        for k in H_WIN:
            if k in w and isinstance(w[k], type(h.get(k))) or (k == "last_try_id" and k in w):
                h[k] = copy.deepcopy(w[k])
    return h


def _health_fields(h):
    from . import health
    return health.snapshot(h)


def _exit_lines(st, pool_exits, new, probed, events, log, now):
    """The journal lines of «не работает» / «снова работает» (3.3.5): from the transitions of health (one source, §4.2)
    with the reason of the probes (``_why``)."""
    for ev in events:
        if ev.get("kind") not in ("exit_dead", "exit_alive"):
            continue
        eid = ev.get("exit")
        alive = ev["kind"] == "exit_alive"
        with util.journal_lang():
            line = tr("gw_collect.log.exit", id=eid, name=_name(st, pool_exits, eid),
                      state=tr("gw_collect.exit_alive" if alive else "gw_collect.exit_dead"),
                      why=_why(new["exits"].get(eid), probed.get(eid) or {}, now))
        log(line)


def _geo_kept(st, gpass, now):
    """The country of the node by the geo pass of the exits (``direct_loc``) kept in ``gateway.hostmon.geo`` (the
    coordinator 19.10; ``bench.geo``): the check of the server, the price of the schedule and the repeat read it.
    Under the lock the caller holds; the caller saves. -> True when ``st`` changed."""
    loc = gpass.get("direct_loc") if isinstance(gpass, dict) else None
    if not (isinstance(loc, str) and len(loc) == 2 and loc.isascii() and loc.isalpha()):
        return False
    from ..bench import geo as bgeo
    try:
        return bgeo.put(st, loc, "exits_geo", int(now))
    except Exception as e:                           # noqa: BLE001 — the country never fails the round
        _note_error("geo", e)
        return False


def _rtt_kept(st, now):
    """The answer of the delays a check of the server left (``bench.geo.remember_rtt``: the check never holds the lock
    of the state — SEC-ns-join П-3) carried into ``gateway.hostmon.geo`` under the lock the caller holds; the caller
    saves. -> True when ``st`` changed."""
    from ..bench import geo as bgeo
    try:
        return bgeo.take_rtt(st, int(now))
    except Exception as e:                           # noqa: BLE001 — the country never fails the round
        _note_error("geo", e)
        return False


def _own_events(events, pool_exits):
    """The events of ``health`` without those of a repeated key (``dup_of``, «e-…-203»): no exit of its own — never
    judged (not parsed: «off», ``disabled``), so its first round said «Выход «<the name of the exit above>» выключен»
    while a move of role held the key in both files for a round, or «Вернуть как было» did (T-s1fin). Neither the
    feed, nor metrics.db.events, nor the journal, nor a request — as no point of it (``_minute``, ``_samples``, Н-3)."""
    dups = set(e["id"] for e in pool_exits if e.get("dup_of"))
    if not dups:
        return events
    return [ev for ev in events if ev.get("exit") not in dups]


def _requests(st, events):
    """The requests ``health`` makes of the collector (§4.3 «Страна»): ``country_first`` / ``country_notified`` /
    ``country_notified_clear`` change ``exit_pref.country`` (out of the fingerprint: ``rev`` stays, §3.1); ``geo_pass``
    / ``geo_confirm`` go to the unit of probes after the lock (``probe.run.request``). -> (wrote, [(kind, eid, after)])."""
    wrote = False
    later = []
    for ev in events:
        if not ev.get("request"):
            continue
        eid, kind = ev.get("exit"), ev.get("kind")
        if kind in ("geo_pass", "geo_confirm"):
            later.append((kind, eid, int(ev.get("after_s") or 0)))
            continue
        pref = model.exit_pref(st, eid)
        c = dict(pref.get("country") or {})
        if kind == "country_first" and ev.get("loc") and not c.get("expected"):
            c.update(expected=ev["loc"], accepted_at=int(_now()))
        elif kind == "country_notified" and ev.get("loc"):
            c["notified"] = sorted(set(c.get("notified") or []) | {ev["loc"]})
        elif kind == "country_notified_clear":
            c["notified"] = []
        else:
            continue
        try:
            model.set_exit(st, eid, country=c)
            wrote = True
        except KaskadError as e:
            _note_error("country", e)
    return wrote, later


def exits_tick(st, runner, log=None, legs=True, probed=None):
    """One round of the exits watchdog (caller holds the state lock; §2.7): ``merge`` of the probes (the reading of the
    answers) -> ``health.evaluate`` (every exit, the rules and checks of its group; the network of the node measured
    before the lock) -> new exits placed (``rev``) -> ``assign`` (by group) -> ``pins.json`` and the snapshot
    ``exits.json`` written AT ONCE (before the lock goes: another process never glues fresh pins to old states) -> the
    number of the render and the fingerprint (xray is applied after the lock) -> ``net.update_map`` with the decision
    in memory -> the events for live.json. The gateway part of the table (put back at once when it is gone) and ip
    rule/table 200/kgw0 as before. ``probed`` — a ``probe_round`` made before the lock was taken.
    Returns {"exits", "changes", "map_changed", "pool_changed", "legs", "repaired", "events", "decision", "xray"?,
    "node", "error"?}.

    A pool that cannot be read (broken parser) keeps the last snapshot and leaves xray/DNS alone; gw_route is kept on
    the legs of that snapshot and the pins of pins.json (``exits.decision``, ``pool_fallback``)."""
    from .. import nft
    from . import assign, health, pins as PN, policy as P
    log = log or util.log_action
    pr = probed or probe_round(runner, legs, st)
    out = {"exits": len(pr["pool"]), "changes": [], "map_changed": False, "pool_changed": False,
           "legs": pr["legs"], "repaired": [], "probed": pr["probed"], "events": [], "decision": None,
           "node": dict(pr.get("node") or {}), "requests": []}
    pool_exits, probed = pr["pool"], pr["probed"]
    now, mono = _now(), _mono()
    d = None
    mip_fp = _mip_round(st, runner, pool_exits, out, log, mono)     # Ш3/Ш4: before the render of xray (its fingerprint)
    if pr.get("error"):
        out["error"] = pr["error"]
    else:
        snap = exits.load_snapshot()
        ids = [e["id"] for e in pool_exits]
        # К2-7 (T-KT2): an exit off for the gateway is out of the observatory of xray — not judged by its last probe
        off = set(e["id"] for e in pool_exits if not model.exit_pref(st, e["id"])["enabled"])
        ru = exits.ru_count(pool_exits)
        new, changes, dirty = exits.merge(snap, probed, ids, off=off, ru=ru)
        # the recheck in RECHECK s — every exit of its own probe (AmneziaWG and a Russian xray exit, REV-q-r3 П-1)
        out["failing"] = any(own_check(e) and (new["exits"].get(e["id"]) or {}).get("alive")
                             and int((new["exits"].get(e["id"]) or {}).get("fails") or 0) > 0 for e in pool_exits)
        out["changes"] = [{"id": eid, "was": was, "alive": alive} for eid, was, alive in changes]
        if list(snap.get("pool") or []) != ids or int(snap.get("pool_ru") or 0) != ru:
            out["pool_changed"] = True
            out["renumbered"] = exits.renumbered(snap.get("pool"), ids, snap.get("pool_ru"), ru)
        hold = exits.ru_hold()
        if hold:                # REV-s1-q4-2 С-1: legs held by install.sh — the table and xray by this pool, then they go
            out["ru_hold"] = hold
            out["renumbered"] = sorted(set(out.get("renumbered") or []) | set(hold["lines"]))
        # ── health (§4): the network of the node first, then every exit ──
        if _place(st, pool_exits):
            out["placed"] = True
        pol = _policy(st, pool_exits)
        node = out["node"]
        was_off = MEM.node.get("offline")
        if node.get("checked") or was_off:
            if node.get("offline") and not was_off:
                MEM.node.update(offline=True, since=now)
                out["events"].append({"kind": "node_offline", "since": int(now)})
            elif not node.get("offline") and was_off:
                since, last = MEM.node.get("since"), MEM.node.get("last")
                until = min(now, last) if last is not None else now   # З1: back before this process saw it
                MEM.node.update(offline=False, since=None, last=None)
                ev = {"kind": "node_online", "since": int(since or until), "until": int(until),
                      "minutes": int(max(0, until - (since or until)) // 60)}
                if last is not None:
                    ev["restart"] = True
                out["events"].append(ev)
                out["back"] = True
        if node.get("checked") and MEM.node.get("offline"):
            MEM.node["last"] = None                     # the outage goes on under this process: its end is seen
        _ru_node(node, now, out)
        out["node_offline"] = bool(MEM.node["offline"])
        out["node_ru_offline"] = bool(MEM.node.get("ru_offline"))     # T15 Q6: live.policy.node_ru_offline (§8.9)
        prev = MEM.health
        if prev is None:
            prev = dict((eid, _restored(health.from_snapshot(new["exits"].get(eid), now, pol), new["exits"].get(eid)))
                        for eid in ids if new["exits"].get(eid) is not None)
        prev = dict((eid, h) for eid, h in prev.items() if eid in ids)
        if out.get("back"):
            prev = health.node_online(prev, pol)       # §4.6, З5: the windows anew after the outage (П2: after load)
        if out.get("ru_back"):
            prev = health.node_online(prev, pol, role="ru")    # T15 Q3: the Russian addresses back — their windows
        hnode = {"offline": MEM.node["offline"], "ru_offline": MEM.node["ru_offline"], "mono": mono, "pool": pool_exits,
                 "tz": tz.zone()}
        res_new, rows, gpass = _results()
        for eid, x in res_new.items():
            MEM.results.setdefault(eid, {}).update(x)
        out["probe_rows"] = rows
        if gpass is not None:
            hnode["geo_pass"] = gpass
        obs = _obs(st, pool_exits, new, probed, mono, now)
        hnew, hev = health.evaluate(prev, obs, pol, hnode, now)
        MEM.health = hnew
        hev = _own_events(hev, pool_exits)
        wrote, out["requests"] = _requests(st, hev)
        # T-q Н-state: the hints «group» of the records of exits left by an older version (a deleted group) — once
        if P.stored(st) is not None and model.prune_exit_groups(st, [g.get("id") for g in pol.get("groups") or []
                                                                     if isinstance(g, dict)]):
            wrote = True
        if gpass is not None and _geo_kept(st, gpass, now):
            wrote = True
        if _rtt_kept(st, now):
            wrote = True
        if wrote:
            state.save(st)
        hev = [e for e in hev if not e.get("request")]
        _exit_lines(st, pool_exits, new, probed, hev, log, now)
        for eid, h in hnew.items():
            rec = new["exits"].get(eid)
            if rec is None:
                continue
            rec["h_win"] = _window_of(h)                 # /run only (exits.WINDOW_KEYS)
            fields = _health_fields(h)
            if any(rec.get(k) != v for k, v in fields.items()):
                if any(rec.get(k) != v for k, v in fields.items() if k != "norm_rtt"):
                    dirty = True
                rec.update(fields)
        # ── the decision (§4.4) ──
        if MEM.pins is None:
            MEM.pins = PN.Store()
            MEM.pins.load(now)
        pins_doc = MEM.pins.doc(now)
        states = dict((eid, h.get("state")) for eid, h in hnew.items())
        dbg = devices_by_group(st, pol, states)
        lt = tz.local(now)
        anode = {"offline": MEM.node["offline"], "ru_offline": MEM.node["ru_offline"],
                 "local": ("%04d-%02d-%02d" % (lt.tm_year, lt.tm_mon, lt.tm_mday), "%02d:%02d" % (lt.tm_hour, lt.tm_min))}
        late = []
        regroup, taken = regroup_take(pol, late=late)   # F14: «Разложить заново» of the panel (usually none)
        if regroup:
            anode["regroup"] = regroup
        d = assign.assign(dbg, hnew, pol, _prev_decision(pins_doc), _metrics_of(st, hnew, pool_exits), anode, now)
        d["policy"] = pol
        d["rows"] = dict((e["id"], exits._row(e)) for e in pool_exits)
        d["quota_off"] = sorted(eid for eid, h in hnew.items() if _off_quota(h))
        MEM.pins.save(_pins_doc(d), now)               # pins.json at once, before the lock goes (§3.4)
        exits.save_snapshot(new, state=dirty)          # and the states beside it
        regroup_done(taken)                            # F14: the requests of this round are done (pins.json has them)
        MEM.decision = d
        out["decision"] = d
        out["events"] += hev + list(d.get("events") or [])
        out["events"] += [assign.regrouped(g, P.group(pol, g), 0, late=True) for g in late if g not in regroup]
        out["moves"] = list(d.get("moves") or [])
        # ── xray: the number of the render under the state lock; applied after it (§2.5, §2.7) ──
        fp = fingerprint(st, d, pool_exits)
        out["fp"] = fp
        from . import xray
        if not MEM.started or out["pool_changed"] or out.get("ru_hold") or xray.render_wanted(fp):
            seq = xray.next_seq()
            out["xray"] = {"st": copy.deepcopy(st), "pool": pool_exits, "decision": d, "seq": seq, "fp": fp}
        if out["pool_changed"]:
            from . import dns
            try:
                if os.path.exists(gsys.unit_path(dns.UNIT)):
                    dns.apply(st, runner, via_pool=xray.dns_via_pool(st, pool_exits),
                              direct=exits.direct_needed(st, pol))
            except KaskadError as e:
                out["error"] = str(e)
    try:
        live = peek.read(runner, sets=peek.RX_SETS if net.ru_groups((d or {}).get("policy")) else peek.HOT_SETS)
        # F2 R-perf: chains, counters, gw_route/gw_live/gw_rd (gw_rx with a Russian group, Q2) — never gw_ru
    except KaskadError as e:
        out["error"] = str(e)
        out.pop("xray", None)                  # the table unknown: xray waits (SEC-s1-t45 З-2), the number stays wanted
        return out
    out["counters"] = dict((live or {}).get("counters") or {})        # forwards' counters for live.json
    # xray of stage 1 opens the SOCKS of the probes and of the pool only with gw_xports in the table (SEC-s1-t45 З-2,
    # REV-s1-t45 З1): a table of 3.3.5 passes part_present without it — a full apply first, as for a new pool
    xports = net.XPORTS in ((live or {}).get("set_info") or {})
    if not net.part_present(live):
        try:
            nft.apply(st, runner)
            _mip_applied(runner, mip_fp)
            xports = True
            out["repaired"].append("table")
            log(_jtr("gw_collect.log.table_restored"))
            out["conntrack"] = _forget_connections(st, runner, log)
            out["nft_ok"] = True
            out["rules_load"] = rules_timer.after_restore(runner)    # gw_ru came back empty: load it now, not in 5 min
        except KaskadError as e:
            out["error"] = str(e)
    elif out["pool_changed"] or (out.get("ru_hold") and _hold_due(mono)) \
            or (out.get("xray") and not xports and _xports_due(mono)) or out.get("mip_changed"):
        if out.get("ru_hold"):
            MEM.hold_try = mono
        try:
            nft.apply(st, runner)                  # TPROXY rules of the VLESS exits follow the pool; gw_xports (З1)
            out["nft_ok"] = True
            _mip_applied(runner, mip_fp)           # Ш3/Ш4: the addresses as the table has them now
            xports = True
            MEM.xports_failed = None
            out["map_changed"] = True
            if out.get("renumbered"):              # connections under the old marks: to their right exits
                out["flushed"] = _forget_renumbered(runner, out["renumbered"], log)
        except KaskadError as e:
            out["error"] = str(e)
            if not xports:                         # REV-s1-b1 П1: not the whole table every 10 s on a broken node
                MEM.xports_failed = mono
    else:
        before = net.map_elems(live)
        # the plan from the decision (§2.7); with a render of xray this round — gw_xports first (REV-s1-t5 З3)
        out["map_changed"] = net.update_map(runner, st, live=live, decision=d, ports=bool(out.get("xray")))
        if out["map_changed"] and boot.pending():
            _tell_moves(st, runner, before, log)
    if out.get("xray") and not xports:
        out.pop("xray")                            # no gw_xports (the apply failed): xray waits for the next tick
    if net.routing_problems(runner, st):
        try:
            net.ensure_routing(runner, st)
            out["repaired"].append("routing")
            if boot.pending():                     # every reboot loses ip rules: the restore, not a breakdown
                with util.journal_lang():
                    line = boot.summary([boot.KINDS["gw_routing"]], "GW")
                if line:
                    log(line)
                else:                              # told already after this boot: no second line, no second event
                    out["hushed"] = ["routing"]
            else:
                log(_jtr("gw_collect.log.routing_restored"))
        except KaskadError as e:
            out["error"] = str(e)
    return out


def _hold_due(mono):
    """A full apply for the legs install.sh holds (REV-s1-q4-2 С-1) may be tried now: the first at once, then every
    HOLD_RETRY_S — not the whole table every tick on a node where it fails."""
    last = MEM.hold_try
    return last is None or mono - last >= HOLD_RETRY_S or mono < last


def _xports_due(mono):
    """A full apply for gw_xports may be tried now: none failed yet, or the last one XPORTS_RETRY_S ago (REV-s1-b1 П1).
    Until then the map goes on (``update_map``) and xray of stage 1 waits."""
    last = MEM.xports_failed
    return last is None or mono - last >= XPORTS_RETRY_S or mono < last


def mark_name(mark, lines=None):
    """«kawg3», «VLESS 2», «пул VLESS (xray)», «напрямую», «блок» of a route mark of gw_route. An xray exit is named by
    its protocol — by the scheme of its line in ``lines`` (the pool), «выход 2» when the line is not known (T1)."""
    if not isinstance(mark, int):
        return tr("gw_collect.mark.none")
    if mark == exits.MARK_BLOCK:
        return tr("gw_collect.mark.block")
    if mark == exits.MARK_DIRECT:
        return tr("gw_collect.mark.direct")
    if mark == exits.MARK_XRAY:
        return tr("gw_collect.mark.xray")
    if exits.AWG_MARK_BASE < mark < exits.MARK_XRAY:
        return "kawg%d" % (mark - exits.AWG_MARK_BASE)
    if exits.VLESS_MARK_BASE < mark < exits.VLESS_MARK_BASE + 0x100:
        n = mark - exits.VLESS_MARK_BASE
        line = lines[n - 1] if lines and 0 < n <= len(lines) else ""
        title = exits.proto_title(exits.link_proto(line))
        return "%s %d" % (title, n) if title else tr("gw_collect.mark.exit_n", n=n)
    return hex(mark)


def _tell_moves(st, runner, before, log):
    """Right after a reboot (``boot.pending``): which devices the exits watchdog moved to another exit — one calm line
    (T-M6-qs330b №8: was a client's outage after the boot its inbound or its exit?). Never fails the round."""
    try:
        after = net.map_elems(peek.read(runner, sets=(net.MAP,), counters=False))
    except KaskadError:
        return
    names = {}
    for d in model.peek(st).get("devices") or []:
        for ip in ((d.get("ips") or {}).values() if isinstance(d, dict) else []):
            names[str(ip)] = str(d.get("name") or d.get("id") or "")
    moved = []
    lines = exits.pool_lines()
    with util.journal_lang():                          # the journal speaks the language of the server
        for ip in sorted(set(before) | set(after)):
            if before.get(ip) != after.get(ip):
                who = (" (%s)" % names[ip]) if names.get(ip) else ""
                moved.append("%s%s: %s → %s" % (ip, who, mark_name(before.get(ip), lines), mark_name(after.get(ip), lines)))
        line = tr("gw_collect.log.moved", moves="; ".join(moved[:20])) if moved else ""
    if line:
        log(line)


def _forget_renumbered(runner, lines, log):
    """``ops.forget_renumbered`` without the import cycle: the lines of the pool that changed their exit."""
    n = net.flush_marks(runner, exits.line_marks(lines))
    where = ", ".join(str(i) for i in lines)
    if n is None:
        log(_jtr("gw_collect.log.renumbered_no_conntrack", lines=where))
    else:
        log(_jtr("gw_collect.log.renumbered", lines=where, n=n))
    return n


def _forget_connections(st, runner, log):
    """After the gw part was put back: conntrack -D -s <base> (``ops.restored`` without the import cycle)."""
    n = net.flush_conntrack(runner, st)
    if n is None:
        log(_jtr("gw_collect.log.flush_no_conntrack"))
    else:
        log(_jtr("gw_collect.log.flushed", base=addr.base(st), n=n))
    return n


def inbound_statuses(st, runner):
    """{kind: adapter.status() or None (it failed)} of every enabled inbound with an adapter — one call each;
    ``sessions`` and the live snapshot share it."""
    from . import registry
    out = {}
    for kind in model.enabled_kinds(st):
        ad = registry.get(kind)
        if ad is None:
            continue
        try:
            out[kind] = ad.status(runner) or {}
        except KaskadError:
            out[kind] = None
    return out


def sessions(st, runner, stats=None):
    """[{"kind", "device", "ip", "since", "rx", "tx", ["last"]}] of every enabled inbound (adapters' status, then
    hook records the adapter did not report; ``last`` — VLESS/Hysteria2 only). ``stats`` — ``inbound_statuses`` already
    taken this round."""
    from . import hooks, registry
    out = []
    seen = set()
    for kind in model.enabled_kinds(st):
        ad = registry.get(kind)
        if ad is None:
            continue
        if stats is not None:
            got = (stats.get(kind) or {}).get("sessions") or []
        else:
            try:
                got = (ad.status(runner) or {}).get("sessions") or []
            except KaskadError:
                got = []
        for s in got:
            rec = {"kind": kind, "device": s.get("device"), "ip": s.get("ip"), "since": s.get("since"),
                   "rx": int(s.get("rx") or 0), "tx": int(s.get("tx") or 0)}
            if isinstance(s.get("last"), int):          # xray: the last growth of the counters (gw/reissue.fresh)
                rec["last"] = s["last"]
            out.append(rec)
            seen.add((kind, rec["ip"]))
    for s in hooks.sessions():
        if (s["kind"], s["ip"]) not in seen:
            out.append({"kind": s["kind"], "device": s["device"], "ip": s["ip"], "since": s["since"], "rx": 0, "tx": 0})
    return out


def _apply_xray(runner, want, events, log):
    """After the state lock (§2.5): the render of this tick applied under the xray lock (≤ XRAY_LOCK_WAIT s, else the
    next tick); a restart waits for the window of the storm (``defer_restart`` STORM: asked under the xray lock — a mark
    of a write of the panel it waited for counts, П3 З-2) — not when an address xray sends from is gone (П6: the same
    round as nft, ``xray.sends_changed``; back — the window, П7); a refused config is not tested again
    every tick (``skip_rejected``) — its event ``xray_rejected`` once (again at most hourly)."""
    from . import xray
    try:
        res = xray.apply(want["st"], secrets.load(), runner, want["pool"], decision=want["decision"],
                         seq=want["seq"], fp=want["fp"], defer_restart=xray.STORM,
                         lock_wait=XRAY_LOCK_WAIT, skip_rejected=True)
    except KaskadError as e:
        err = util.redact(str(e)).strip().splitlines()[0][:300] if str(e).strip() else type(e).__name__
        _ru_watch(getattr(e, "data", None) or {}, events, log, runner, (want["st"], want["pool"]))
        h = hashlib.sha1(err.encode("utf-8")).hexdigest()
        if MEM.rejected is None or MEM.rejected[0] != h or _mono() - MEM.rejected[1] >= REJECT_TELL_S:
            MEM.rejected = (h, _mono())
            ev = {"kind": "xray_rejected", "error": err}
            if (getattr(e, "data", None) or {}).get("ru_closed"):
                ev["stopped"] = True                  # REV-s1-b7 №2: not «the previous one runs» — xray is stopped
            events.append(ev)
            log(_jtr("gw_collect.log.xray_rejected", error=err))
        MEM.started = True
        return {"error": err}
    MEM.started = True
    _ru_watch(res or {}, events, log, runner, (want["st"], want["pool"]))
    if (res or {}).get("restarted") or (res or {}).get("started"):
        MEM.gw_all["start"] = None                    # the override is lost with the process (§2.6, T0 С0.5)
    return res


def _ru_watch(res, events, log, runner=None, ctx=None):
    """REV-s1-b6-sec Н-2 (B7): «Россия напрямую без согласия» is told to the owner — not the hourly ``xray_rejected``,
    an event of its own the bot always sends: ``xray_ru_open`` when a closing could not stop xray (again when the
    number changes — no hourly silence; the number told is kept in the mark, so a restarted collector does not tell it
    again — REV-s1-b7 придирка 3), ``xray_ru_closed`` when this tick closed it (``why`` stopped: xray is stopped, SOCKS5
    and VLESS wait for a config it takes — REV-s1-b7 №2) or the mark of an open one went (``why`` restarted: xray
    restarted with the state, stopped by hand, the node rebooted). By the mark (``xray.ru_open``) AND by the result
    (круг 2, -sec С-1: a mark no place took), so a closing of ``gw apply`` or of the rules page is told too. ``ru_guard``
    — the last line of D-136 dropped a direct path (``gw.ruguard``), once. ``res`` — the result of ``xray.apply`` or
    ``data`` of its error; None — no apply this tick. ``ctx`` — (state, pool) of the tick: an open file of a closing
    (``xray.closed.json``) counts only the paths the state does not give (REV-s1-b7-2 придирка 2)."""
    from . import ruguard, xray
    res = res or {}

    def num(v):
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

    st, pool = ctx if ctx else (None, None)
    n = max(xray.ru_open(runner, st=st, pool_exits=pool), num(res.get("ru_open")))
    closed = num(res.get("ru_closed"))
    told = max(MEM.ru_open, xray.ru_told())
    if n and n != told:
        events.append({"kind": "xray_ru_open", "n": n})
        log(_jtr("gw_collect.log.xray_ru_open", n=n))
        xray.note_told(n)
    elif not n and (MEM.ru_open or closed):
        # REV-s1-b7-2 №2: «allowed» — the choice or the consent came back (the mark narrowed: xray was neither
        # restarted nor stopped); the told number goes with the closing, so a second opening is told again
        why = "stopped" if closed else ("allowed" if xray.narrowed(runner) else "restarted")
        was = closed or MEM.ru_open
        events.append({"kind": "xray_ru_closed", "n": was, "why": why})
        log(_jtr("gw_collect.log.xray_ru_closed_" + why, n=was))
        xray.note_told(0)
    MEM.ru_open = n
    for _ in ruguard.WHERE:                   # xray and nft may both have tripped this tick: each told once
        note = ruguard.tripped()
        if note is None:
            break
        events.append({"kind": "ru_guard", "n": num(note.get("n")), "where": str(note.get("where") or "")})
        log(_jtr("gw_collect.log.ru_guard", n=num(note.get("n"))))
        ruguard.told(note)


def _unit_start(runner, unit):
    """ActiveEnterTimestampMonotonic of a unit (its start), None when it is not active."""
    res = runner.run(["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value", unit])
    v = (res.out or "").strip() if res.ok else ""
    return int(v) if v.isdigit() and int(v) > 0 else None


def _gw_all(runner, d, pool_exits):
    """``bo gw-all <decision.gw_all>`` (§2.6): put again when the target changed or xray started again (the override
    lives in its memory); checked by ``bi`` every 5 min; ``-r`` only with no foreign exit in the pool. Needs the API
    of the xray of stage 1 (T5: ``xray.balancer_override``)."""
    from . import xray
    if d is None:
        return None
    start = _unit_start(runner, xray.UNIT)
    if start is None:
        MEM.gw_all.update(target=None, start=None, checked=None)
        return None
    tgt = d.get("gw_all")
    if not any(exits.role_of(e) == "abroad" and exits.parsed(e) for e in pool_exits):
        tgt = None
    g = MEM.gw_all
    mono = _mono()
    if xray.has_balancer("gw-all") is False:
        # no gw-all in the config xray runs (no exit drawn: all off by the owner, none, all off by the quota): nothing
        # to put — ``bo`` is refused («cannot find tag») and was asked again every tick (KT3-8). When it comes back
        # (the hot path puts the override itself), the mark differs from any target: put once more
        g.update(target=NO_BALANCER, start=start, checked=mono)
        return None
    due = tgt != g.get("target") or start != g.get("start")
    if not due and (g.get("checked") is None or mono - g["checked"] >= GW_ALL_CHECK_S):
        g["checked"] = mono
        got = xray.balancer_info(runner, "gw-all")
        due = got is not None and got != (tgt or "")
    if not due:
        return False
    ok = xray.balancer_override(runner, tgt, "gw-all")
    if ok:
        g.update(target=tgt, start=start, checked=mono)
    return ok


def _bot_target(st, d, pool_exits):
    """The best fit exit of the group of «Прокси для API Telegram» (§2.9): its lines (``tgproxy.indexes``), good before
    degraded, then the least response of the last probes -> «out_<N>»; none -> None (``bo -r``: round robin)."""
    from . import tgproxy
    try:
        idx = set(tgproxy.indexes(st).get("indexes") or [])
    except Exception:                                # noqa: BLE001 — the bot is not worth a failed round
        return None
    health = MEM.health or {}
    best = None
    for e in pool_exits:
        if e.get("index") not in idx or exits.role_of(e) != "abroad":
            continue
        s = (d.get("states") or {}).get(e["id"])
        if s not in exits.USABLE:
            continue
        rtts = [x[2] for x in ((health.get(e["id"]) or {}).get("samples") or [])[-5:]
                if isinstance(x, list) and len(x) > 2 and x[2] is not None]
        key = (0 if s == "ok" else 1, sorted(rtts)[len(rtts) // 2] if rtts else 1e9, e["index"])
        if best is None or key < best[0]:
            best = (key, e["index"])
    return "out_%d" % best[1] if best else None


def _bot(runner, st, d, pool_exits):
    """``bo bot-bal`` of the bot's xray (kaskad-egress, API 127.0.0.1:EGRESS_API_PORT; §2.9, T4): the target by the
    health of the gateway; a manual choice (``.override`` of the menu) wins — not touched; put again after a restart
    of kaskad-egress. Without the bot's xray or its service — nothing."""
    if d is None:
        return None
    if os.path.exists(os.path.join(paths.egress_dir(), ".override")):
        MEM.bot.update(target=None, start=None)
        return None
    xb = paths.egress_xray()
    if not os.path.exists(xb):
        return None
    start = _unit_start(runner, exits.EGRESS_UNIT)
    if start is None:
        MEM.bot.update(target=None, start=None)
        return None
    tgt = _bot_target(st, d, pool_exits)
    if tgt == MEM.bot.get("target") and start == MEM.bot.get("start"):
        return False
    args = [xb, "api", "bo", "-timeout", "8", "--server=127.0.0.1:%d" % EGRESS_API_PORT, "-b", "bot-bal"]
    res = runner.run(args + (["-r"] if tgt is None else [tgt]), timeout=10)
    if res.ok:
        MEM.bot.update(target=tgt, start=start)
    else:
        _note_error("bot_bo", KaskadError((res.err or res.out or "").strip()[-200:]))
    return res.ok


def _flush_moved(runner, st, d, pool_exits):
    """§2.3: the open connections of the devices moved off an exit «снят» (and «ухудшен» / «в запасе» with
    ``drop_on_degrade`` of the group) through the kernel are forgotten — conntrack by address and the old mark."""
    from . import hooks, policy as P
    moves = [m for m in (d or {}).get("moves") or [] if m.get("from")]
    if not moves:
        return None
    rows = dict((e["id"], e) for e in pool_exits)
    states = d.get("states") or {}
    pol = d.get("policy") or {}
    kinds = model.enabled_kinds(st)
    leases = {}
    for ip, dev in hooks.leases_for(st):
        leases.setdefault(dev.get("id"), []).append(ip)
    by_mark = {}
    for m in moves:
        e = rows.get(m["from"])
        s = states.get(m["from"], "off")
        grp = P.group(pol, m.get("group")) or {}
        if e is None or not e.get("mark") or not (s == "off" or (s in ("degraded", "standby")
                                                                  and grp.get("drop_on_degrade"))):
            continue
        dev = model.find_device(st, m["device"])
        if dev is None:
            continue
        ips = list(model.live_ips(st, dev, kinds)) + leases.get(dev["id"], [])
        by_mark.setdefault(e["mark"], set()).update(ips)
    n = 0
    for mark, ips in sorted(by_mark.items()):
        got = net.flush_devices(runner, ips, mark)
        if got is None:
            return None
        n += got
    return n


def _note_clock(synced, mono):
    """REV-s1-t4 З3: the clock not synchronised — no counter goes into traffic.json (T3, C2), so a line in the journal
    (once in CLOCK_LOG_S, and when it is synchronised again); ``clock_problem`` makes it a problem of the gateway."""
    if synced:
        if MEM.unsync_since is not None and MEM.unsync_told is not None:
            util.log_tr("gw_collect.log.clock_synced")
        MEM.unsync_since = MEM.unsync_told = None
        return
    if MEM.unsync_since is None:
        MEM.unsync_since = mono
    if MEM.unsync_told is None or mono - MEM.unsync_told >= CLOCK_LOG_S:
        MEM.unsync_told = mono
        util.log_tr("gw_collect.log.clock_unsynced")


def clock_problem(st, pool_exits, mono=None):
    """The clock not synchronised for CLOCK_PROBLEM_S and an exit of the pool has a quota: its quota is not counted
    (REV-s1-t4 З3) — a problem of the gateway in live.json."""
    if MEM.unsync_since is None:
        return False
    mono = _mono() if mono is None else mono
    if mono - MEM.unsync_since < CLOCK_PROBLEM_S:
        return False
    return any(model.exit_pref(st, e["id"]).get("quota") for e in pool_exits or [] if e.get("id"))


def _minute(runner, st, res, pool_exits, d, now):
    """Once in MINUTE_S (§3.5, §5.6): the counters into traffic.json (only with the clock synchronised) and the point of
    every exit into metrics.db (m1), the results of the unit of probes, the thinning once an hour. A repeated key
    (``dup_of``) is no exit of its own: no point, no counters (T-3379 Н-3 — «e-…-201» of a change of role in the charts)."""
    from . import pins as PN
    pool_exits = [e for e in pool_exits if not e.get("dup_of")]
    mono = _mono()
    if MEM.minute_at is not None and mono - MEM.minute_at < MINUTE_S:
        return False
    MEM.minute_at = mono
    counters = res.get("probe_counters") or {}
    synced = _clock_synced(runner) if counters else None
    if synced is not None:
        _note_clock(synced, mono)
    if counters and synced:
        doc = _traffic_doc()
        z = tz.zone()
        for e in pool_exits:
            got = counters.get(e["id"])
            rx, tx = got if got is not None else (None, None)
            PN.traffic_add(doc, e["id"], rx, tx, now, model.exit_pref(st, e["id"]).get("quota"), z)
        _save_traffic()
    w = _writer()
    if not w:
        return True
    rows = {}
    pins_n = {}
    for g in ((d or {}).get("groups") or {}).values():
        for p in (g.get("pins") or {}).values():
            pins_n[p.get("exit")] = pins_n.get(p.get("exit"), 0) + 1
    for e in pool_exits:
        eid = e["id"]
        smp = MEM.samples.pop(eid, [])
        rtts = sorted(r for ok, r in smp if ok and r is not None)
        cur = counters.get(eid)
        last = MEM.minute_bytes.get(eid)
        drx = dtx = None
        if cur is not None:
            if last is not None:
                from .metrics import delta
                drx, dtx = delta(cur[0], last[0]), delta(cur[1], last[1])
            MEM.minute_bytes[eid] = cur
        rows[eid] = {"rtt_med": rtts[len(rtts) // 2] if rtts else None,
                     "rtt_p90": rtts[min(len(rtts) - 1, int(len(rtts) * 0.9))] if rtts else None,
                     "loss": (1.0 - sum(1 for ok, _ in smp if ok) / float(len(smp))) if smp else None,
                     "jitter": _jitter(rtts), "ok": sum(1 for ok, _ in smp if ok), "n": len(smp), "rx": drx, "tx": dtx,
                     "devices": pins_n.get(eid, 0), "state": ((d or {}).get("states") or {}).get(eid)}
    if w.write_minute(now, rows):
        LIVE.metrics_ts = int(now) // 60 * 60       # 3.3.7.10: the panel reads the line of «Выходы» again when it moves
        MEM.open_base = LIVE.metrics_ts             # 3.3.7.11: the open minute counts from this point
    else:
        MEM.open_base = None                        # the bytes of the minute are not in the base: no open minute
    _host_minute(w, mono, now)
    _people_flush(w)
    _bench_spool(w, res, now)
    for r in res.get("probe_rows") or []:
        _probe_row(w, r)
    w.maybe_maintain(now, _max_mb(st))
    return True


def _bench_spool(w, res, now):
    """SCHEMA 3 (SS §3.2, NS-S9): the queue of the checks into metrics.db — the collector is its one writer; the
    results.json of the runner is never carried (only the queue is the history). The events «2 checks skipped in a
    day» go to ``res["bench_events"]`` and into the feed of live.json (B11: ``res["events"]``, the bot pushes them
    and writes skip-push.json; the collector never writes the mark); «a check ended» (``bench_done``, B7) — to the
    events of the round with the score after it (``bench_done_args``), the layer publishes it (``LIVE.full``)."""
    try:
        from ..bench import spool
        got = spool.drain(w, now)
        done = [e for e in got["events"] if e["kind"] == "bench_done"]
        rest = [e for e in got["events"] if e["kind"] != "bench_done"]
        if rest:
            res.setdefault("bench_events", []).extend(rest)
            res.setdefault("events", []).extend(_bench_feed(e, now) for e in rest)
        for e in done:
            res.setdefault("events", []).append({"kind": "bench_done", "args": bench_done_args(e["args"])})
    except Exception as e:                           # noqa: BLE001 — the history never stops a tick
        _note_error("bench_spool", e)


def bench_done_args(a):
    """The args of ``bench_done``: the check + ``rel`` (``rel_total`` of the node after it, NS-D31); not read — without
    it (the event still goes)."""
    try:
        from ..bench import donepush
        return donepush.with_rel(a)
    except Exception as e:                           # noqa: BLE001 — the news of the check without its number
        _note_error("bench_score", e)
    return dict(a)


def _bench_feed(e, now):
    """An event of the queue -> the event of the feed: ``bench_skipped_day`` with ``key`` (the words of the feed)
    and ``args`` {day, n} (the bot says «today / yesterday / a date» itself, §2.5 п. 7)."""
    a = dict(e.get("args") or {})
    return {"kind": e.get("kind"), "ts": int(now), "key": "bench.skipday.feed",
            "args": {"day": a.get("day"), "n": a.get("n")}}


def _jitter(rtts):
    if len(rtts) < 2:
        return None
    import statistics
    return statistics.pstdev(rtts)


def _max_mb(st):
    g = (st or {}).get("gateway") if isinstance(st, dict) else None
    v = (g or {}).get("metrics_max_mb") if isinstance(g, dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def _writer():
    from . import metrics
    if MEM.metrics is None:
        MEM.metrics = metrics.open_writer() or False
    return MEM.metrics


def _probe_row(w, r):
    eid, ts = r.get("exit"), r.get("ts") or int(_now())
    k = r.get("kind")
    if k == "speed":
        w.add_speed(eid, ts, mbps=r.get("mbps"), ttfb_ms=r.get("ttfb_ms"), tls_ms=r.get("tls_ms"),
                    nbytes=r.get("bytes"), host=r.get("host"), ok=bool(r.get("ok")), why=r.get("why"))
    elif k == "svc":
        w.add_svc(eid, r.get("svc"), ts, bool(r.get("ok")), code=r.get("code"), ms=r.get("ms"))
    elif k == "geo" and r.get("loc"):
        w.add_geo(eid, ts, r.get("ip"), r.get("loc"))
    if r.get("bytes"):
        w.add_traffic(eid, tz.day(ts), probe=int(r.get("bytes") or 0))


def _samples(pool_exits, probed):
    for e in pool_exits:
        if e.get("dup_of"):                         # a repeated key: no exit of its own, no point (T-3379 Н-3)
            continue
        p = probed.get(e["id"])
        if p is not None and p.get("alive") is not None:
            MEM.samples.setdefault(e["id"], []).append((bool(p["alive"]), p.get("rtt")))


def _transitions(events, now):
    """Every change of the state of an exit into metrics.db.events BEFORE the antispam (§3.6): «transition»."""
    w = _writer()
    if not w:
        return
    for ev in events:
        if ev.get("kind") == "transition":
            w.add_transition(int(now), ev.get("exit"), ev.get("from"), ev.get("to"), why=ev.get("why"))


def _published(events):
    w = _writer()
    if not w:
        return
    for ev in events:
        w.add_event(ev.get("ts") or int(_now()), ev.get("key") or ev.get("kind"), eid=ev.get("exit"),
                    args=ev.get("args"))


def _where(e):
    """The place of the call that nested the state lock (the innermost frame of kaskad) — for the journal."""
    import traceback
    frames = traceback.extract_tb(e.__traceback__) if e.__traceback__ else []
    mine = [f for f in frames if os.sep + "kaskad" + os.sep in f.filename] or frames
    if not mine:
        return "?"
    f = mine[-1]
    return "%s:%d %s" % (os.path.basename(f.filename), f.lineno, f.name)


def _after(runner, st, res, pr, log):
    """The work of a tick after the state lock is let go (§2.7): xray under its own lock, ``bo`` of gw-all and of the
    bot, the conntrack of the moved devices, the requests to the unit of probes, metrics.db and traffic.json once a
    minute. Never fails the round: each part logs its error (hourly)."""
    d = res.get("decision")
    events = res.setdefault("events", [])
    if res.get("xray"):
        try:
            res["xray_result"] = _apply_xray(runner, res["xray"], events, log)
        except Exception as e:                       # noqa: BLE001
            _note_error("xray", e)
        if res.get("ru_hold") and res.get("nft_ok"):
            try:                                     # REV-s1-q4-2 С-1: the table and xray by this pool — the legs go up
                from . import xray
                pool = res["xray"].get("pool")
                if xray.applied_ok(res.get("xray_result")) and exits.ru_hold_release(pool, "collector"):
                    MEM.hold_try = None
                    exits.ensure_legs(runner, pool, force=True)
            except Exception as e:                   # noqa: BLE001
                _note_error("ru_hold", e)
    else:
        try:
            # the mark of a closing by gw apply, or gone without a render
            _ru_watch(None, events, log, runner, (st, pr["pool"]))
        except Exception as e:                       # noqa: BLE001
            _note_error("xray", e)
    for name, fn in (("gw_all", lambda: _gw_all(runner, d, pr["pool"])),
                     ("bot", lambda: _bot(runner, st, d, pr["pool"])),
                     ("flush", lambda: _flush_moved(runner, st, d, pr["pool"]))):
        try:
            res[name] = fn()
        except Exception as e:                       # noqa: BLE001
            _note_error(name, e)
    if res.get("requests"):
        try:
            from .probe import run as prun
            for kind, eid, after in res["requests"]:
                prun.request(kind, eid, after)
        except Exception as e:                       # noqa: BLE001
            _note_error("probe_request", e)
    try:
        _samples(pr["pool"], pr["probed"])
        _transitions(events, _now())
        res["probe_counters"] = pr.get("counters") or {}
        _minute(runner, st, res, pr["pool"], d, _now())
    except Exception as e:                           # noqa: BLE001
        _note_error("metrics", e)
    try:
        res["clock_unsynced"] = clock_problem(st, pr["pool"])
    except Exception as e:                           # noqa: BLE001
        _note_error("clock", e)
    try:
        res["reresolved"] = _reresolve(runner, pr["pool"])
    except Exception as e:                           # noqa: BLE001
        _note_error("reresolve", e)


# ── the light health of the node (A5; SPEC-node-score §3.6, §3.7, §4 A5) ──

HOST_FIELDS = ("cores", "busy", "steal", "iowait", "softirq")     # of the full round, from hostmon (§3.6)


def _host_cost(sec, now=None):
    """The CPU of the new part (``time.process_time``: the round of hostmon, the series, the control work) into the
    window of the hour; at its end — one line in the journal and a new window (REV З-4)."""
    now = _now() if now is None else now
    if MEM.cost_since is None:
        MEM.cost_since = now
    elif not 0 <= now - MEM.cost_since < HOST_COST_S:
        ms = MEM.cost_s * 1000.0
        try:
            from .. import i18n
            with util.journal_lang():              # the numbers in the language of the line (REV-ns-a5 П-3)
                util.log_action(i18n.tr("gw_host.log.cost", ms=i18n.fmt_num(ms),
                                        pct=i18n.fmt_num(ms / (HOST_COST_S * 10.0), None, digits=3)))
        except Exception:                            # noqa: BLE001 — the journal never stops the round
            pass
        MEM.cost_s, MEM.cost_since = 0.0, now
    MEM.cost_s += max(0.0, sec)


def _host_load(mono, now):
    """hostmon.json at the first round of the process (or after the bot owned it): the levels said, ``pushed``, the
    antispam, the parts, the norms (``hostmon.from_disk``; a file of the bot too). A broken file goes aside with a line
    in the journal, the automaton starts anew (as live-feed.json, D-088)."""
    from . import hostmon, hostprobe
    path = hostprobe.hostmon_path()
    MEM.host_state, MEM.host_hint, MEM.host_disk_at, MEM.host_sig = {}, None, mono, None
    raw = util.read_text(path)
    if not raw:
        return
    try:
        doc = json.loads(raw)
        if not isinstance(doc, dict) or not isinstance(doc.get("rules") or {}, dict):
            raise ValueError("not hostmon.json")
    except ValueError:
        try:
            os.replace(path, path + ".bad")
            util.log_tr("gw_host.log.state_bad", path=path, aside=path + ".bad")
        except Exception:                            # noqa: BLE001
            pass
        return
    MEM.host_state = hostmon.from_disk(doc, mono, now)
    h = doc.get("probes_hint")
    MEM.host_hint = h if isinstance(h, (int, float)) and not isinstance(h, bool) else None
    MEM.host_sig = _host_sig(MEM.host_state)


def _host_sig(state):
    out = []
    for rid, rs in sorted(((state or {}).get("rules") or {}).items()):
        sub = rs.get("sub") if isinstance(rs.get("sub"), dict) else {}
        out.append((rid, rs.get("said"), rs.get("pending"), rs.get("pushed"), rs.get("last_ev"),
                    tuple(sorted(sub.items()))))
    return tuple(out)


def _host_save(state, now, mono, force=False):
    """hostmon.json (§3.7): at a change of the levels said (and of ``pending``/``pushed``/the parts), at once when the
    hint of the probes was told, and once an hour; ``owner: "collector"``; 0600, atomically."""
    from . import hostmon, hostprobe
    sig = _host_sig(state)
    if not force and sig == MEM.host_sig and MEM.host_disk_at is not None and mono - MEM.host_disk_at < HOST_DISK_S:
        return False
    doc = hostmon.to_disk(state)
    doc.update(owner="collector", probes_hint=MEM.host_hint)
    util.atomic_write(hostprobe.hostmon_path(), json.dumps(doc, ensure_ascii=False, sort_keys=True), 0o600)
    MEM.host_sig, MEM.host_disk_at = sig, mono
    return True


def _host_ctx(st, cfg):
    prev = LIVE.doc if isinstance(LIVE.doc, dict) else None
    notify = model.notify(st)
    lvl = notify.get("host_level")
    ctx = {"owner": "collector", "push": notify.get("push") or {}, "host_level": lvl if lvl in ("crit", "warn")
           else "crit", "node_offline": bool(MEM.node.get("offline")), "probes": MEM.ping, "probes_on": cfg["probes"],
           "services": None, "inbounds_why": None}
    if prev is not None:                               # the services and inbounds of the last full round
        svc = (prev.get("gateway") or {}).get("services")
        ctx["services"] = dict(svc) if isinstance(svc, dict) else None
        ctx["inbounds_why"] = dict((k, True) for k, v in (prev.get("inbounds") or {}).items()
                                   if isinstance(v, dict) and v.get("enabled") and v.get("running") is False)
    return ctx


def _host_eval(st, res, mono, now):
    from . import hostmon, hostprobe
    on, probes = model.hostmon_flags(st)
    own = tuple(model.own_addrs(st))
    if on and probes:                                  # the addresses of the interfaces too (REV-ns-a5-sec П-1)
        own += tuple(a for a in hostprobe.own_addrs(mono) if a not in own)
    cfg = model.hostmon(st, own)
    MEM.host_cfg, MEM.host_own = cfg, own
    if MEM.host_state is None:
        _host_load(mono, now)
    ctx = _host_ctx(st, cfg)
    MEM.ping = None
    sample = {}
    if cfg["enabled"]:
        every = []
        if MEM.host_min_at is None or not 0 <= mono - MEM.host_min_at < MINUTE_S:
            every.append("minute")
            MEM.host_min_at = mono
        if MEM.host_hour_at is None or not 0 <= mono - MEM.host_hour_at < HOST_HOUR_S:
            every.append("hour")
            MEM.host_hour_at = mono
        if "minute" in every:
            _people_plan(st)
        raw = hostprobe.read({"st": st}, mono, every)
        sample = hostmon.derive(MEM.host_raw, raw)
        MEM.host_raw = raw
        ctx["uptime"], ctx["virt"] = raw.get("uptime"), raw.get("virt")
    else:                                              # off: nothing is read (§4 A5 п. 1), the alarms go «na off»
        MEM.host_raw = MEM.host_min_at = MEM.host_hour_at = None
        MEM.people_plan = None
        if MEM.people is not None:                     # the hour of «Связь с людьми» is forgotten, not closed later
            MEM.people.step(None, mono, now)
    state, events = hostmon.evaluate(MEM.host_state, sample, cfg, ctx, now, mono)
    MEM.host_state = state
    MEM.host_busy = sample.get("busy")
    for e in state.get("errors") or []:                 # a part of the round skipped (REV-ns-a12-2 П-13): hourly
        _note_error("hostmon " + e.split(":")[0], ValueError(e))
    force = False
    if cfg["enabled"] and not cfg["probes_key"] and MEM.host_hint is None:
        MEM.host_hint, force = now, True               # §10.1 п. 20, 22: an updated node — the hint once, no push
        events = list(events) + [{"kind": "host_loss", "key": "gw_host.event.probes_hint",
                                  "args": {"check": "loss", "level": "na", "push": False,
                                           "addrs": list(cfg["anchors"])}}]
    res.setdefault("events", []).extend(events)
    try:
        _host_save(state, now, mono, force)
    except Exception as e:                           # noqa: BLE001 — the file never stops the round
        _note_error("hostmon_json", e)
    lt = hostmon.light(state)
    if MEM.host_level is None or MEM.host_level[0] != lt["level"]:
        since = int(now)
        if MEM.host_level is None:                     # a new process: an alarm of hostmon.json keeps its time (П-4)
            old = [f["since"] for f in lt["top"] if f.get("level") == lt["level"] and isinstance(f.get("since"), (
                int, float)) and not isinstance(f["since"], bool) and 0 < f["since"] <= now]
            since = int(min(old)) if old else since
        MEM.host_level = (lt["level"], since)
    health = {"v": hostmon.V, "at": int(now), "level": lt["level"], "since": MEM.host_level[1], "instant": False,
              "by": "collector", "cost": {"cpu_ms_h": round(MEM.cost_s * 1000.0, 1),
                                          "since": int(MEM.cost_since if MEM.cost_since is not None else now)},
              "hint": None, "top": lt["top"], "info": lt["info"], "checks": lt["checks"],
              "probes": hostmon.probes_view(state, cfg, mono)}
    host = dict((k, sample.get(k)) for k in HOST_FIELDS)
    host.update(health=health, alarms=lt["alarms"])
    res["host"] = host


def _host_round(st, res, pr=None):
    """A5 п. 1: the light health after the work of the exits, before ``LIVE.full`` (it puts ``res["host"]`` into
    live.json and the alarms into the problems). The last guard of ``derive`` + ``evaluate`` (REV-ns-a12 п. 7): an
    error is a line in the journal once an hour, the round of the exits and live.json (without ``health``) go on."""
    t0 = time.process_time()
    now = _now()
    _host_cost(0.0, now)                               # the window of the hour first: health.cost of this round
    try:
        _host_eval(st, res, _mono(), now)
    except Exception as e:                           # noqa: BLE001
        res.pop("host", None)
        _note_error("hostmon", e)
    finally:
        _host_cost(time.process_time() - t0, now)


def _host_minute(w, mono, now):
    """A5 п. 2: the minute of the node and of the probes into metrics.db (host_m1, host_ping_m1) after the minute of the
    exits; the control work of this minute in ``canary_ms``."""
    from . import hostmon
    stt = MEM.host_state
    if not stt or not stt.get("enabled") or not stt.get("rounds"):
        return
    try:
        row = hostmon.minute_row(stt, mono)
        if MEM.canary_at is not None and 0 <= mono - MEM.canary_at < MINUTE_S:
            row["canary_ms"] = MEM.canary_ms
        w.write_host_minute(now, row)
        w.write_ping_minute(now, hostmon.ping_rows(stt, mono))
    except Exception as e:                           # noqa: BLE001
        _note_error("metrics_host", e)


def _host_pause(room=None):
    """A5 п. 11: in a step of the pause — a series of the light probes when due (``hostping.step``: the flags of the
    last round, nothing while the node is offline, NS-D22) and the control work once in 600 s. ``room`` — seconds left
    in the pause: a series (≤ ``hostping.SERIES_S``) or the control work that would not fit waits for the next pause
    (REV-ns-a5 П-2). -> seconds spent."""
    cfg = MEM.host_cfg
    if not cfg or not cfg.get("enabled"):
        return 0.0
    from . import hostping
    m0, t0 = _mono(), time.process_time()
    try:
        if MEM.prober is None:
            MEM.prober = hostping.Prober(cfg.get("anchors"), own=MEM.host_own)
        got = None
        if room is None or room >= hostping.SERIES_S:
            got = hostping.step(MEM.prober, cfg, m0, MEM.host_own, offline=bool(MEM.node.get("offline")))
        if got is not None:
            MEM.ping, MEM.ping_at = got, m0
    except Exception as e:                           # noqa: BLE001
        _note_error("hostping", e)
    try:
        if MEM.canary is None:
            MEM.canary = hostping.Canary()
        ms = None
        if room is None or room - (_mono() - m0) >= hostping.CANARY_S:
            ms = MEM.canary.maybe(_mono(), MEM.host_busy)
        if ms is not None:
            MEM.canary_ms, MEM.canary_at = ms, _mono()
    except Exception as e:                           # noqa: BLE001
        _note_error("canary", e)
    try:                                               # B17: one sample of «Связь с людьми» a minute (NS-D26)
        from . import hostpeople
        if MEM.people_plan is not None:
            if MEM.people is None:
                MEM.people = hostpeople.People()
            if room is None or room - (_mono() - m0) >= hostpeople.DEADLINE_S:
                MEM.people.step(MEM.people_plan, _mono(), _now())
    except Exception as e:                           # noqa: BLE001
        _note_error("hostpeople", e)
    _host_cost(time.process_time() - t0)
    return max(0.0, _mono() - m0)


def _people_plan(st):
    """B17: the inputs of «Связь с людьми» once a minute of the round (the pause has no state)."""
    try:
        from . import hostpeople
        MEM.people_plan = hostpeople.plan(st, hostpeople.fronts_443())
    except Exception as e:                           # noqa: BLE001 — the round goes on without the measure
        MEM.people_plan = None
        _note_error("hostpeople_plan", e)


def _people_bad_row():
    """The errors of a row the base can never take (its data, not the base): such a row is dropped, not retried."""
    out = (OverflowError, TypeError, ValueError, KeyError)
    try:
        import sqlite3
        out += (sqlite3.IntegrityError, sqlite3.DataError, sqlite3.InterfaceError)
    except ImportError:                              # no sqlite3: no writer either
        pass
    return out


def _people_flush(w):
    """B17: the finished hours of «Связь с людьми» to the writer of metrics.db (``write_people_hour(ts, row)`` —
    the table host_people_h1 of SCHEMA 3, SS §3.1, INSERT OR REPLACE by the hour). A writer without it (SCHEMA 2) — the
    rows wait in the memory of the process (``hostpeople.KEEP_ROWS``; the oldest dropped with a line in the journal).
    A row refused for its data (OverflowError, IntegrityError…) is dropped with a line in the journal — never retried
    each minute in front of the others; the base failing (OperationalError, …) — the rest waits for the next minute."""
    p = MEM.people
    if p is None:
        return
    if p.dropped:
        util.log_action("GW collect: hostpeople — %d hour row(s) dropped: not written for %d h" % (
            p.dropped, p.rows.maxlen))
        p.dropped = 0
    rows = p.take()
    if not rows:
        return
    put = getattr(w, "write_people_hour", None)
    if put is None:
        p.keep(rows)
        return
    bad = _people_bad_row()
    for i, r in enumerate(rows):
        try:
            put(r["ts"], r)
        except bad as e:
            util.log_action("GW collect: hostpeople — the row of an hour dropped, the base refuses its data: %s"
                            % type(e).__name__)
        except Exception as e:                       # noqa: BLE001
            p.keep(rows[i:])
            _note_error("metrics_people", e)
            return


# ── the activity of devices — «кардиограмма» of «Люди» (SPEC-activity §3.2, §3.3, AC3; the core — kaskad.activity) ──
# The sessions of the full round (``sessions``: no new command, ACT-1) go into the accumulator of the core; a closed
# window of 5 min goes into metrics.db (``write_dev``: four tables, one transaction) — with the clock synchronised
# (the rule of traffic.json: before it the windows wait, ≤ 12, the oldest merged) and the base answering. The
# accumulator lives in /run/kaskad/gw/activity.json (tmpfs, 0600): once a minute and right after a window is written,
# so a restarted collector goes on from its counters (no false peak, ACT-4, §3.3). The devices deleted from the state
# leave the accumulator and the base at the next window (≤ 5 min; the panel hides them at once). The history off
# (``gateway.activity.keep`` false, ACT-8): nothing is counted, the file goes, dev_* are erased once. «Удалить историю
# активности» (№41: the panel erases dev_* itself, ``metrics.wipe_dev``, and leaves ``meta.dev_wiped``): every round —
# full and light — drops the windows that began at or before that mark from the accumulator (``activity.wipe``), so
# nothing counted before the wipe reaches the base, /run or live.json after it; ``write_dev`` checks the mark too.

ACT_SAVE_S = 60             # s: the accumulator into /run at most this often (and after every window written)


def activity_path():
    return os.path.join(paths.gw_run_dir(), "activity.json")


def _act_mem():
    if MEM.act is None:
        MEM.act = {"acc": None, "saved": None, "sig": None, "off": None, "cut": None}
    return MEM.act


def _act_save(m, mono):
    from .. import activity as A
    util.atomic_write(activity_path(), A.dump(m["acc"]), 0o600)
    m["saved"] = mono


def _act_int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _act_off(m):
    """The history off (ACT-8): the accumulator and its file go; dev_* of the base are erased once (a line in the
    journal when anything was there); the base not open or failing — again at the next round."""
    m["acc"] = None
    LIVE.activity_ts = LIVE.activity_open = None
    if m["off"]:
        return
    try:
        os.remove(activity_path())
    except FileNotFoundError:
        pass
    w = _writer()
    n = w.drop_dev() if w else None
    if n is None:                                   # no base now (or it failed): erased at a later round
        return
    m["off"] = True
    if n:
        util.log_action("GW activity: the history of activity is off — %d rows erased" % n)


def _act_wiped(w=None):
    """``meta.dev_wiped`` of the base (№41, «Удалить историю активности» of the panel): an int, None — none, no base,
    no writer. ``w`` — the writer (None: ``_writer()``; a light round passes the one the full round opened)."""
    from . import metrics
    w = _writer() if w is None else w
    return _act_int(w.meta(metrics.DEV_WIPED)) if w else None


def _act_flush(st, m, win):
    """A closed window (and those waiting) into metrics.db: only the devices of the state; the clock not synchronised
    or the base failing — they wait in the accumulator. Then the base keeps only the devices of the state (once the
    set of ids changed). -> True all written."""
    from .. import activity as A
    from . import hostprobe
    acc = m["acc"]
    ids = sorted(d.get("id") for d in model.devices(st) if isinstance(d.get("id"), str) and d.get("id"))
    keep = set(ids)
    A.forget(acc, keep)
    win["rows"] = dict((d, r) for d, r in win["rows"].items() if d in keep)
    A.hold(acc, win)
    w = _writer()
    clock = hostprobe.clock()
    if not w or (clock is not None and not clock["synced"]):
        return False
    z = tz.zone()
    todo = A.take(acc)
    for i, x in enumerate(todo):
        if not w.write_dev(x, z):
            for y in todo[i:]:
                A.hold(acc, y)
            return False
    LIVE.activity_ts = _act_int(w.meta("dev_flush"))
    sig = hashlib.sha1("\n".join(ids).encode("utf-8")).hexdigest()
    if sig != m["sig"] and w.forget_dev(keep) is not None:
        m["sig"] = sig
    return True


def _activity_round(st, ss, now=None):
    """AC3: the sessions ``ss`` of a full round into the activity of devices. -> the window closed by the round (None
    — none, or the history off / no sqlite3). Never fails the round: ``tick`` logs its error hourly."""
    from .. import activity as A
    from . import metrics
    m = _act_mem()
    if not model.activity_keep(st):
        _act_off(m)
        return None
    m["off"] = False
    if not metrics.available():                     # no sqlite3: no history anywhere (the panel says so)
        m["acc"] = None
        LIVE.activity_ts = LIVE.activity_open = None
        return None
    now = _now() if now is None else now
    mono = _mono()
    if m["acc"] is None:                            # the first round of the process: from /run (≤ 15 min, §3.3)
        m["acc"] = A.load(util.read_text(activity_path()), now)
        w = _writer()
        if w:
            flushed = _act_int(w.meta("dev_flush"))
            A.after_flushed(m["acc"], flushed)      # written before the crash, saved after it: not twice
            LIVE.activity_ts = flushed
    cut = _act_wiped()
    A.wipe(m["acc"], cut)                           # №41: before the step — the window it closes is not written
    if cut is not None and LIVE.activity_ts is not None and LIVE.activity_ts <= cut:
        LIVE.activity_ts = None                     # the window it named is erased: none written since
    fresh = cut is not None and cut != m.get("cut")  # a wipe seen first: /run without what it erased at once
    m["cut"] = cut
    _acc, win = A.step(m["acc"], ss, now)
    A.wipe(m["acc"], cut)                           # … and the open window of the wipe stays empty
    if win is not None:
        _act_flush(st, m, win)
    # 3.3.7.10: the open window into live.json — the panel adds it to the summary of the base between the windows
    LIVE.activity_open = A.open_view(m["acc"], _act_keep(st))
    if win is not None or fresh or m["saved"] is None or mono - m["saved"] >= ACT_SAVE_S:
        _act_save(m, mono)
    return win


def _act_keep(st):
    return set(d.get("id") for d in model.devices(st) if isinstance(d.get("id"), str) and d.get("id"))


def _activity_light(st, ss, now=None):
    """3.3.7.11, a light round: the open window of activity as the full round would show it if it came now — the rule
    of the full round on a COPY of the accumulator, over the time since the last full round. The accumulator itself
    (memory, /run, the windows of the base) goes by the full rounds only (REV-s1fin З-2): stepped by every light round,
    the same traffic cut into rounds of 2–5 s reached the threshold of «активен» less often (a burst of 5 KB every
    10 s: 16 s «активен» of 50 with a panel open, 50 without) — the history depended on whether a panel watched. Across
    the border of a window, or with the clock back, the open window of the full round stays: the full round closes
    and writes the window (the summary of the panel never loses it in between). -> True — the open window moved."""
    from .. import activity as A
    m = _act_mem()
    acc = m.get("acc")
    if acc is None or m.get("off"):
        return False
    now = _now() if now is None else now
    at, b = acc.get("at"), acc.get("bucket")
    if at is None or b is None or now < at or int(now) // A.WINDOW * A.WINDOW != b:
        return False
    cut = _act_wiped(MEM.metrics or False)          # №41: a wipe since the full round — its rule on the copy
    peek, _ = A.step(A.wipe(copy.deepcopy(acc), cut), ss, now)
    LIVE.activity_open = A.open_view(A.wipe(peek, cut), _act_keep(st))
    return True


def _mip_round(st, runner, pool_exits, out, log, mono):
    """PLAN-multi-ip Ш3/Ш4: the addresses of the machine looked at (``mip.watch``: every round while an address is chosen,
    else every WATCH_EVERY s), the plan of the addresses (the chosen ones of the inbounds, SNAT of the legs and of Н4)
    compared with the one the table was applied with: another — ``out["mip_changed"]`` (a full apply this round; xray
    follows by its fingerprint). The first round of the process applies when the plan draws something or the boot file
    has such a rule (an address gone during a reboot). The journal says an address of the list or of an exit gone and
    back. Never fails the round. -> the fingerprint of the plan."""
    from . import mip
    MEM.mip_next = None
    if MEM.addr6 is None:
        _ip6_first()
    try:
        got = mip.watch(runner, st, mono, MEM.mip_at)
        if got is not None:
            MEM.mip_at = mono
        mp = mip.plan(st, pool_exits)
        legs = mip.leg_map(mp, pool_exits)
    except Exception as e:                           # noqa: BLE001 — the addresses never stop the watchdog
        _note_error("mip", e)
        return MEM.mip_fp
    MEM.mip_next = legs
    _ip6_round(out)
    fp = mip.fingerprint(mp)
    if MEM.mip_fp is None:
        if fp or mip.file_has_rules() or legs:
            out["mip_changed"] = True
        else:
            MEM.mip_fp, MEM.mip_legs = fp, legs
    elif fp != MEM.mip_fp:
        out["mip_changed"] = True
    gone = mip.gone_state(mp)
    try:
        from . import exits
        names = dict((e["id"], exits.label(st, e)) for e in pool_exits or [])
        mip.tell(MEM.mip_gone, gone, names, st=st)
    except Exception as e:                           # noqa: BLE001
        _note_error("mip_tell", e)
    MEM.mip_gone = gone
    return fp


def _ip6_first():
    """REV-3315-b1 С-1: the first round of a process — ``addresses6`` of the cache as the last process left it, taken
    before ``mip.watch`` writes the machine now over it: a prefix changed during a reboot (or while the collector was
    down) is said like one changed under a running process; the same addresses — nothing. The time of the file is not
    taken: ``_ip6_round`` reads the cache after the look."""
    from . import addresses
    try:
        m = addresses.load_cache()
        now6 = (m or {}).get("addresses6")
        if isinstance(now6, list):
            MEM.addr6 = now6
    except Exception as e:                           # noqa: BLE001 — the addresses never stop the watchdog
        _note_error("ip6", e)


def _ip6_round(out):
    """3.3.7.15 F10: «префикс IPv6 сменился» — ``addresses6`` of the cache of «Адреса сервера» compared with the one seen
    last (``addresses.prefix_changes``), read again only when the file changed (whoever wrote it: ``mip.watch`` here,
    the panel, the CLI): the events ``ip6_prefix_changed {iface, old, new, suggest}`` go to the feed and the bot. The
    first look of a process compares with the cache the last process left (``_ip6_first``); no such cache (3.3.7.14,
    the first start) — it only remembers. Never fails the round."""
    from . import addresses
    try:
        try:
            mt = os.stat(addresses.cache_path()).st_mtime
        except OSError:
            return
        if mt == MEM.addr6_mtime:
            return
        m = addresses.load_cache()
        now6 = (m or {}).get("addresses6")
        if m is None or not isinstance(now6, list):
            return                                      # a cache of 3.3.7.14 (no key yet): nothing to compare
        MEM.addr6_mtime = mt
        if MEM.addr6 is not None:
            for ev in addresses.prefix_changes(MEM.addr6, now6):
                out["events"].append(dict(ev, kind="ip6_prefix_changed"))
        MEM.addr6 = now6
    except Exception as e:                           # noqa: BLE001 — the addresses never stop the watchdog
        _note_error("ip6", e)


def _mip_applied(runner, fp):
    """After an apply of the whole table (``nft.apply`` went through): the fingerprint the table has now, and the legs
    whose address changed, came or went since the last apply forgotten by conntrack (REV-mip-s34 С-3, PLAN §3 Ш4: the
    SNAT of a flow lives in its conntrack record, keepalive keeps it for good — a leg went on from an address gone,
    or stayed on the main one after its address came back). The first apply of the process (nothing known of the last
    one) — every leg with an address of its own. Never fails the round."""
    MEM.mip_fp = fp
    legs = MEM.mip_next
    if legs is None:
        return
    try:
        from . import mip
        changed = mip.leg_changes(MEM.mip_legs, legs)
        if changed:
            mip.forget(runner, changed)
        MEM.mip_legs = legs
    except Exception as e:                           # noqa: BLE001
        _note_error("mip_forget", e)


def tick(runner, log=None):
    """One collector round (§2.7): probes and the network of the node without the lock, then a short locked commit
    (health, decision, pins, snapshot, gw_route), then xray and the rest after the lock. Returns {"skipped"?,
    "enabled", "exits"?, "sessions"?, "error"?}."""
    log = log or util.log_action
    t0 = _mono()
    _pending_overdue(runner)                           # F7 (З-2): a wait whose timer is lost — not only at the start
    st = state.load()
    if not model.enabled(st) or st.get("engine") != "nft":
        if MEM.host_state is not None:
            MEM.host_reset()                           # the bot is the owner now: its file at the next start (§2.1)
        return {"enabled": False}
    pr = probe_round(runner, st=st)
    try:
        with state.locked(timeout=LOCK_TIMEOUT):
            st = state.load()
            if not model.enabled(st) or st.get("engine") != "nft":
                return {"enabled": False}
            res = exits_tick(st, runner, log, probed=pr)
    except NestedLockError as e:                     # a bug of the code, not «busy» (§3.1, проверка 19): said loudly
        util.log_tr("gw_collect.log.nested_lock", where=_where(e), error=util.redact(e))
        return {"enabled": True, "error": str(e)}
    except LockedError:
        return {"skipped": True}
    _after(runner, st, res, pr, log)
    if not pr.get("error"):
        MEM.light_pool = pr["pool"]
    try:
        LIVE.metrics_open = _exits_open(res.get("probe_counters"), _now())
    except Exception as e:                           # noqa: BLE001 — the open minute never fails the round
        _note_error("metrics_open", e)
    legs = res.get("legs")
    if not res.get("repaired") and not res.get("error") and (legs is None or legs.get("ok")) and boot.pending():
        boot.done()                                    # whole again after the reboot: from now on a repair is news
    stats = inbound_statuses(st, runner)
    try:                                               # who recreated an inbound's interface (T-M6-qs330b №8)
        ifwatch.observe(st, runner, stats)
    except Exception as e:                           # noqa: BLE001 — the watch never fails the round
        _note_error("ifwatch", e)
    ss = sessions(st, runner, stats)
    try:
        util.atomic_write(sessions_path(), json.dumps({"at": util.now_iso(), "sessions": ss}, ensure_ascii=False,
                                                      sort_keys=True), 0o600)
    except OSError:
        pass
    try:                                               # SPEC-activity AC3: the same sessions, no new command
        _activity_round(st, ss)
    except Exception as e:                           # noqa: BLE001 — the activity never fails the round
        _note_error("activity", e)
    try:                                               # F1 (3.3.7.15): «порт» снят у подключившихся на новом порту
        from . import reissue
        reissue.seen(ss, _now(), st)
    except Exception as e:                           # noqa: BLE001 — the marks never fail the round
        _note_error("reissue", e)
    _host_round(st, res, pr)
    pev, pfiles = _pending_events()                    # F7: «настройки возвращены» of the timer's process (spool)
    if pev:
        res["events"] = list(res.get("events") or []) + pev
    fed = LIVE.fed
    _live_round(lambda: LIVE.full(st, runner, stats, res, pr["pool"], pool_known=not pr.get("error")))
    if pfiles and LIVE.fed != fed:                     # З-3: the files go only once the feed has their events
        _pending_done(pfiles)
    MEM.shown_at = _mono()
    MEM.shown_lead = max(0.0, MEM.shown_at - t0)
    try:
        _published(LIVE.take_published())
    except Exception as e:                           # noqa: BLE001
        _note_error("metrics_events", e)
    tofu_kick(runner)
    return {"enabled": True, "exits": res, "sessions": len(ss)}


def tofu_waiting(now=None):
    """3.3.4: a key of the pool with allowInsecure whose server did not answer on the update waits for its pin
    (tofu-pins.json «pending»), the last try more than an hour ago — cheap: no network, no parser."""
    now = _now() if now is None else now
    try:
        st = os.stat(paths.egress_tofu())
        if not os.path.exists(paths.egress_tofu_done()) or now - st.st_mtime < TOFU_EVERY:
            return False
        with open(paths.egress_tofu(), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and bool(doc.get("pending"))


def tofu_kick(runner, now=None):
    """Once an hour: the pins put off on the update tried again without a restart of the services (``gokaskad
    --egress-tofu-kick`` — its own short unit; the gateway gets them by gw apply). Never fails the round."""
    now = _now() if now is None else now
    if now - _tofu_at[0] < TOFU_EVERY:
        return False
    _tofu_at[0] = now
    if not tofu_waiting(now):
        return False
    try:
        runner.run([exits._script(runner), "--egress-tofu-kick"], timeout=60)
    except Exception as e:                           # noqa: BLE001 — the round goes on
        _note_error("tofu", e)
    return True


def _pool_busy():
    """The lock of the pool was busy at the tick (install.sh writes the pool): the groups are pruned later — a line
    in the journal every ``POOL_BUSY_LOG_EVERY`` s at most (a lock held for good would say so, not prune silently)."""
    if _now() - _live_errors.get("pool_busy", 0) >= POOL_BUSY_LOG_EVERY:
        util.log_tr("gw_collect.log.pool_busy")
        _live_errors["pool_busy"] = _now()


def _note_error(key, e):
    """An error of a side job of the round: logged hourly at most per kind."""
    if _now() - _live_errors.get(key, 0) >= ERROR_LOG_EVERY:
        util.log_action("GW collect: %s — %s: %s" % (key, type(e).__name__, util.redact(e)))
        _live_errors[key] = _now()


def _live_round(make):
    """Build and write live.json; a failure is logged (hourly per kind) and never fails the round."""
    try:
        doc = make()
        if doc is not None:
            live.write(doc)
    except Exception as e:                           # noqa: BLE001 — the watchdog matters more than the panel
        key = type(e).__name__
        if _now() - _live_errors.get(key, 0) >= ERROR_LOG_EVERY:
            util.log_tr("gw_collect.log.live_not_written", kind=key, error=util.redact(e))
            _live_errors[key] = _now()


def pause(runner, interval):
    """The pause between two rounds, in steps of ``live.FAST`` s: while a panel watches (live.want is fresh)
    each step is a live round (peer counters, /proc, /sys — no probes, no nft), or a light one when what the pages
    show would else be older than LIGHT_MAX s (``_light_due``, 3.3.7.11); otherwise only sleep. A series of the
    light probes (``hostping``, ≈ 1,4 s) and the control work take their time out of the step (A5 п. 11): the pause is
    never longer than ``interval``."""
    left = float(interval)
    while left > 0:
        step = min(live.FAST, left)
        spent = _host_pause(left)                      # A5: a series of the light probes when due, the control work
        if spent < step:
            _sleep(step - spent)
        left -= max(step, spent)
        if not live.want_fresh():
            continue
        if _light_due(left):
            _light_round(runner)
        elif left > 0:
            _live_round(lambda: LIVE.fast(runner))


def _light_due(left):
    """A panel watches: a light round now? — when waiting for the next chance would leave what «Пользователи» and
    «Выходы» show older than LIGHT_MAX s. The next chance: the next step of the pause (``live.FAST`` s, less at its
    end), or after the pause the write of the next full round (as far into it as the last one: ``shown_lead``)."""
    if MEM.shown_at is None:
        return True
    nxt = min(live.FAST, left) if left > 0 else MEM.shown_lead
    return _mono() - MEM.shown_at + nxt > LIGHT_MAX


def _light_round(runner):
    """3.3.7.11, only while a panel watches: what the pages show, without the work of a round — the status of every
    enabled inbound (one call each, as the full round): who is online and the open window of activity; the counters of
    the exits of the last full round (/sys, one ``/debug/vars`` with xray exits): the open minute of «Выходы». No
    probes, no nft, no lock, nothing of the health or the decision. Never fails the pause; -> True written."""
    MEM.shown_at = _mono()                             # tried: a failing one is not tried again at every step
    try:
        st = state.load()
        if not model.enabled(st) or st.get("engine") != "nft" or LIVE.doc is None:
            return False
        stats = inbound_statuses(st, runner)
        if model.activity_keep(st) and MEM.act is not None and MEM.act.get("acc") is not None:
            try:                                       # the open window only; the accumulator — full rounds (З-2)
                _activity_light(st, sessions(st, runner, stats))
            except Exception as e:                   # noqa: BLE001
                _note_error("activity", e)
        pool = MEM.light_pool
        if pool and MEM.open_base is not None:
            try:
                doc = exits.debug_vars(runner) if any(exits.is_xray(e) for e in pool) else None
                LIVE.metrics_open = _exits_open(_counters(pool, doc), _now())
            except Exception as e:                   # noqa: BLE001
                _note_error("metrics_open", e)
    except Exception as e:                           # noqa: BLE001 — the watchdog matters more than the panel
        _note_error("light", e)
        return False
    _live_round(lambda: LIVE.light(st, stats))
    MEM.shown_at = _mono()
    return True


def _exits_open(counters, now):
    """3.3.7.11: the open minute of «Выходы» — {"at", "cur": {eid: [rx, tx]}}: the bytes of every exit since the point
    of m1 ``LIVE.metrics_ts`` (the counters ``counters`` of now against the ones that point took — the rule of deltas of
    the next point, ``metrics.delta``); an exit not read now or not then is not there (unknown is not zero). None — no
    point written by this process, or the last one failed."""
    from .metrics import EXA_MAX, delta
    if MEM.open_base is None or MEM.open_base != LIVE.metrics_ts:
        return None
    cur = {}
    for eid, got in sorted((counters or {}).items()):
        last = MEM.minute_bytes.get(eid)
        if got is None or last is None or len(cur) >= EXA_MAX:
            continue
        cur[eid] = [delta(got[0], last[0]), delta(got[1], last[1])]
    return {"at": int(now), "cur": cur}


def loop(runner, interval=INTERVAL, rounds=None):
    """The service body. ``rounds`` limits the loop (tests). While an AmneziaWG exit fails but is not dead yet the
    next round comes in RECHECK s (stand T-G1, C3-1; 3.3.5: it replaces the result of the last check — one check per 10 s)."""
    last_err = {}
    n = 0
    _pending_due(runner)                               # F7: a wait without its timer — reverted or timed again
    while rounds is None or n < rounds:
        n += 1
        res = None
        try:
            res = tick(runner)
        except Exception as e:                       # noqa: BLE001 — the daemon must survive one bad round
            key = type(e).__name__
            if _now() - last_err.get(key, 0) >= ERROR_LOG_EVERY:
                util.log_tr("gw_collect.log.error", kind=key, error=e)
                last_err[key] = _now()
        if rounds is None or n < rounds:
            failing = bool(((res or {}).get("exits") or {}).get("failing"))
            pause(runner, min(RECHECK, interval) if failing else interval)
    return n


def read_sessions():
    raw = util.read_text(sessions_path())
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    if not isinstance(doc, dict):
        return []
    return doc.get("sessions") or []
