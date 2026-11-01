"""Operations of the gateway — what ``kaskadctl gw ...`` (and later the menu) calls (spec §5, §7).

Every change follows the wave-1 order: under ``state.locked()`` load -> change a COPY -> bring the system
to the copy (``_apply``) -> save state.json, then the secrets' deletions -> log. A failed apply leaves
state.json as it was and loses no secret it refers to. Long steps (apt, builds of adapters) run BEFORE
the lock is taken, so the watchdog and the bot are not blocked for minutes.

``_apply(st, sec, runner)`` — the whole gateway from state, in this order (agreed with the adapters):
    0. the system user of xray (its uid is in gw_out); when the gw part is not in the live table yet
       (``gw enable``, ``gw apply``, after ``nft flush ruleset``): ``nft.apply`` + routing (the kill switch,
       ip rules, kgw0) FIRST — no inbound starts before the table and the routing protect its clients; the
       gate drop-ins of the inbound units (gw/gate.py);
    1. every installed adapter's ``apply`` (it prepares keys/addresses and runs or stops itself — an
       inbound that must not run, or a gateway that is off, stops and closes its UFW ports); additions to
       the secrets are saved at once (harmless), deletions wait for state.json;
    2. exits: a missing or broken leg brought up / repaired alone (the working ones are not touched), probe,
       snapshot — so the first table already has the right marks;
    3. ``nft.apply`` (forwards + the gw part with the new device addresses + ip6 table, one transaction;
       boot file; ip_forward); routing again; ``kaskad:compat-in``;
    4. xray (only when needed; a config xray refuses is a warning — the running one stays), then DNS
       (upstreams: the pool when it has exits, direct only while some device may go out directly);
    5. the collector unit.
An adapter that fails is a warning: one broken inbound must not stop the others or the table.

The gateway needs engine nft (forwards in table ip kaskad) and a supported system (nft.probe_support:
Debian 12+, Ubuntu 24.04+) plus the gateway constructs accepted by ``nft -c`` (net.probe). UFW rules of
the inbound ports belong to the adapters (comment ``kaskad-gw <kind>``); the core opens nothing in UFW —
the clients' DNS and the xray path pass ``kaskad:compat-in``.

The menu of install.sh (work package 7) also calls: ``probe`` (can the gateway run here),
``inbound_plan`` (ports of the install wizard and what holds them now, read-only), ``offers`` (what to
give a platform first, spec §4), ``inbound_option`` (switches of an inbound record: plain L2TP, UDP of
SOCKS5) and ``purge`` (the whole gateway away: «Удалить шлюз», full uninstall, the way back to 2.13).
"""

import copy
import glob
import os
import random
import re
import shutil
import time

from .. import compat, conflicts, nft, paths, secrets, state, util
from ..errors import CONFLICT, USAGE, ConflictError, KaskadError, LockedError, StateError, UnsupportedError
from ..i18n import get_lang, retr, saved_lang, tr, trn
from ..rules import rfgate
from ..rules import store as rules_store
from ..rules import timer as rules_timer
from . import addr, awgengine, collect, dns, exits, exitsubs, gate, hooks, live, model, net, policy, registry, rev, xray
from ..vendor import awgkeys
from . import mtu as gwmtu
from . import sys as gsys
from .probe import unit as probe_unit

GW_MODULES = ("nft_tproxy", "nf_tproxy_ipv4", "dummy")
CONNTRACK_PACKAGE = "conntrack"         # conntrack(8): the watchdog forgets connections made without the gw part
RANDOM_PORTS = (20000, 60000)
NET_UNIT = "kaskad-net.service"

# what to offer first per platform when kaskad.gw.issue_os (work package 5) is not there (spec §4)
ISSUE_ORDER = {
    "ios": ("awg3", "vless", "hy2", "ikev2", "wg", "ocserv"), "macos": ("awg3", "vless", "hy2", "ikev2", "wg", "ocserv"),
    "android": ("awg3", "vless", "hy2", "wg", "ikev2", "openvpn"), "androidtv": ("awg3", "awg2", "vless", "wg"),
    "windows": ("awg3", "awg2", "vless", "hy2", "ikev2", "sstp", "l2tp", "openvpn", "ocserv"),
    "keenetic": ("awg2", "awg1", "wg", "l2tp"), "mikrotik": ("wg", "l2tp", "sstp", "openvpn"),
    "openwrt": ("awg2", "awg1", "wg", "openvpn"), "tplink": ("l2tp", "pptp"), "huawei": ("l2tp", "pptp"),
    "xiaomi": ("l2tp", "pptp"), "asus": ("l2tp", "pptp", "openvpn"),
    "linux": ("wg", "awg3", "vless", "hy2", "openvpn", "ocserv", "socks5"),
    "other": ("wg", "awg3", "awg2", "vless", "hy2", "openvpn", "ocserv", "socks5", "l2tp"),
}

_rng = random.SystemRandom()


# ── preconditions ──────────────────────────────────────────────────────────

def require_engine(st):
    if st.get("engine") != "nft":
        raise UnsupportedError(tr("gw_ops.err.need_nft"))


def support(runner):
    """{"supported", "code", "reason"}: nft.probe_support (system, nft) + the gateway's constructs."""
    sup = nft.probe_support(runner)
    if not sup["supported"]:
        return {"supported": False, "code": sup["code"], "reason": sup["reason"]}
    ok, err = net.probe(runner)
    if not ok:
        return {"supported": False, "code": "gw_check_failed",
                "reason": tr("gw_ops.support.nft_refused", err=err)}
    why = net.preflight(runner)  # F1: таблицы 199/200 у другого, внутренний порт xray занят чужой программой
    if why:  # F1
        return {"supported": False, "code": "gw_conflict", "reason": why}  # F1
    return {"supported": True, "code": "", "reason": ""}


def _unknown_kind(kind):
    return KaskadError(tr("gw_ops.err.unknown_kind_of", kind=repr(kind), kinds=", ".join(model.INBOUND_KINDS)),
                       code=USAGE)


def adapter(kind):
    if kind not in model.INBOUND_KINDS:
        raise _unknown_kind(kind)
    ad = registry.get(kind)
    if ad is None:
        raise UnsupportedError(tr("gw_ops.err.kind_unsupported", kind=registry.title(kind)))
    return ad


def _default_port(ad):
    v = getattr(ad, "default_port", 0)
    v = v() if callable(v) else v
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v <= 65535 else 0


def _port_fields(ad):
    v = getattr(ad, "port_fields", ())
    return list(v() if callable(v) else v)


def _ports_of(ad, st):
    """[(proto, port)] an installed inbound listens on (its record + fixed ports)."""
    try:
        return list(ad.ports(st))
    except Exception:                           # noqa: BLE001 — an adapter of another work package
        return []


def ports_shown(ad, st):
    """["443/udp", "20000–20099/udp"] — the ports of an inbound for people: a range of Hysteria2 port hopping is ONE
    entry (the adapter's ``ports_view``; ``ports`` lists each port of it for the conflict checks)."""
    fn = getattr(ad, "ports_view", None)
    try:
        if callable(fn):
            return ["%s/%s" % (p, pr) for pr, p in fn(st)]
        return ["%d/%s" % (p, pr) for pr, p in ad.ports(st)]
    except Exception:                           # noqa: BLE001 — an adapter of another work package
        return []


# ── the whole gateway from state ───────────────────────────────────────────

def _warn(res, text):
    res.setdefault("warnings", []).append(util.redact(text))


def _apply_adapters(st, sec, runner, res):
    res.setdefault("inbounds", {})
    with addr.using(st):
        for kind in model.installed_kinds(st):
            ad = registry.get(kind)
            if ad is None:
                _warn(res, tr("gw_ops.warn.no_adapter", kind=registry.title(kind)))
                continue
            try:
                out = ad.apply(st, sec, runner)
                res["inbounds"][kind] = (out or {}).get("action", "applied") if isinstance(out, dict) else "applied"
            except KaskadError as e:
                res["inbounds"][kind] = "error"
                _warn(res, "%s: %s" % (ad.title or kind, e))


def _protect(st, runner, res, pre=None):
    """Step 0 of _apply: xray's user (its uid is in gw_out), and — when ``pre`` or when the gw part is not
    in the live table — the table and the routing before any inbound runs; the gate drop-ins."""
    try:
        xray.ensure_user(runner)
    except KaskadError as e:
        _warn(res, str(e))
    if pre is None:
        pre = not net.part_present(nft.read_live(runner))
    if pre:
        res["nft_pre"] = nft.apply(st, runner)
        res["routing_pre"] = net.ensure_routing(runner, st)
    try:
        res["gates"] = gate.sync(runner, st)
    except KaskadError as e:
        _warn(res, tr("gw_ops.warn.gates", err=e))


def _rx_home(st, pool_exits):
    """``net.rx_home`` by the stored policy (REV-s1-q2-2 З-1); unreadable — None (``ru_revoked`` as before)."""
    from . import policy as P
    try:
        return net.rx_home(st, P.load(st), pool_exits)
    except (KaskadError, ValueError, OSError, TypeError, KeyError):
        return None


def _apply(st, sec, runner, probe=True, pre=None):
    """Bring the system to ``st`` (the caller holds the lock and saves afterwards). Raises KaskadError
    if the table or the routing fail; a failing adapter, xray or DNS is a warning. ``pre`` — apply the
    table and the routing before the adapters (None: only if the gw part is missing from the live table)."""
    res = {"warnings": []}
    for note in gwmtu.refresh(st):                     # §6.1: the card of the node -> the server MTU of the inbounds
        util.log_action("GW: %s" % note)
    _protect(st, runner, res, pre)
    _apply_adapters(st, sec, runner, res)
    sec.save(keep_deleted=True)
    pool_error = None
    renum = []
    try:
        pool_exits = exits.pool(runner, roles=exits.ROLES)
    except KaskadError as e:
        pool_exits, pool_error = [], str(e)
        _warn(res, tr("gw_ops.warn.pool_unread", err=e))
    if probe and pool_error is None:
        legs = exits.ensure_legs(runner, pool_exits, force=True)     # nothing runs if every leg is whole
        if legs["healed"] and not legs["ok"]:
            # человеку: какие выходы и что делать (внутренние «ноги kawgN» — в журнал, не на экран; стенд T-G2, B2.3)
            util.log_tr("gw_ops.log.legs_broken", legs=", ".join(legs["missing"] + legs["broken"]))
            _warn(res, tr("gw_ops.warn.legs_down", n=len(legs["missing"] + legs["broken"])))
        snap = exits.load_snapshot()
        ids = [e["id"] for e in pool_exits]
        ru = exits.ru_count(pool_exits)                                 # T15: each file by itself
        renum = exits.renumbered(snap.get("pool"), ids, snap.get("pool_ru"), ru)    # the lines that now hold another exit
        # REV-q-r3 З-1: a Russian xray exit is probed by the collector only (its own SOCKS probe, never the
        # observatory): here it would be UNKNOWN every time and two applies >= UNKNOWN_MAX apart would call it «упал».
        # Left out of the answer — merge keeps its record as the collector last wrote it.
        mine = [e["id"] for e in pool_exits if not (exits.is_xray(e) and collect.own_check(e))]
        new, _, dirty = exits.merge(snap, exits.probe(runner, pool_exits, only=mine), ids, ru=ru)
        exits.save_snapshot(new, state=dirty)
    hold = exits.ru_hold() if pool_error is None else None
    if hold:                                                            # REV-s1-q4-2 С-1: the lines whose legs wait —
        renum = sorted(set(renum) | set(hold["lines"]))                 # their old connections are forgotten too
    if pool_error is None:
        for note in gwmtu.sync_legs(st, runner, pool_exits):   # §6.1: the legs' MTU by the card, live, no restart
            util.log_action("GW: %s" % note)
    ru0 = net.ru_live(runner)                                           # SEC-s1-q2 Б-2: the Russian sets before
    res["nft"] = nft.apply(st, runner)
    for w in res["nft"].get("warnings") or []:
        _warn(res, w)
    try:                                       # SEC-3372 П-1: a device off «напрямую» — its DNS flows to the direct dnsmasq go
        from . import peek
        net.flush_direct_dns(runner, st, net.map_elems(peek.read(runner, sets=(net.MAP,), counters=False)))
    except KaskadError:
        pass
    revoked = net.ru_revoked(ru0, net.ru_live(runner),
                             _rx_home(st, pool_exits) if pool_error is None else None) if ru0 else {}
    if revoked:                                                         # Russia taken away (the consent, «напрямую»,
        res["ru_flushed"] = net.flush_revoked(runner, revoked)          # ru_via): its open connections go too
    if renum:                                                           # gw_route has the new marks: now forget
        res["renumbered"] = renum                                       # what went out under the old ones
        res["flushed"] = forget_renumbered(runner, renum)
    res["routing"] = net.ensure_routing(runner, st)
    try:
        compat.ensure_in(runner)
    except KaskadError as e:
        _warn(res, str(e))
    if pool_error is None:
        dec = None
        try:
            # stage 1 (§2.5, §2.7; T4): the decision of the collector (pins.json is fresh: no move is undone), the number of
            # the render under the state lock this change holds, the xray lock up to 60 s (a timeout is no failure: the
            # state is saved, the collector applies it on its next tick — gw_ops.warn.xray_deferred)
            res["xray"], dec = xray.apply_now(st, sec, runner, pool_exits, lock_wait=60)
            for w in res["xray"].get("warnings") or []:
                _warn(res, w)
            for n in res["xray"].get("notes") or []:          # VLESS cut on purpose: said with the result
                _warn(res, n)
        except KaskadError as e:
            res["xray_error"] = str(e)                    # inbound_install: an xray inbound that did not come up
            closed = (getattr(e, "data", None) or {}).get("ru_closed")
            if closed:                                    # SEC-s1-r2 З2: the old config sent Russia directly — stopped
                res["xray_ru_closed"] = closed
                _warn(res, tr("gw_ops.warn.xray_ru_closed", err=e))
            elif (getattr(e, "data", None) or {}).get("ru_open"):    # REV-s1-b5 П1: xray did not stop — said so
                res["xray_ru_open"] = e.data["ru_open"]
                _warn(res, tr("gw_ops.warn.xray_ru_open", err=e))
            elif gsys.unit_active(runner, xray.UNIT):
                _warn(res, tr("gw_ops.warn.xray_old_config", err=e))
            else:                                         # T-M6-qs330b №1: no «previous config» to speak of
                _warn(res, tr("gw_ops.warn.xray_down", err=e))
        # REV-s1-q4-2 С-1: the table (raised above if it failed) and xray by this pool — the held legs may come up
        if hold and "xray_error" not in res and xray.applied_ok(res.get("xray")) \
                and exits.ru_hold_release(pool_exits, "gw apply"):
            res["ru_released"] = hold["legs"]
            if probe:
                exits.ensure_legs(runner, pool_exits, force=True)
        try:
            res["dns"] = dns.apply(st, runner, via_pool=xray.dns_via_pool(st, pool_exits),
                                   direct=exits.direct_needed(st, (dec or {}).get("policy")))
        except KaskadError as e:
            _warn(res, str(e))
    else:
        # SEC-3372 П-2: the pool unreadable — xray is left as it is, but kaskad-dns is still written in the form of this
        # version (the pool only: its names never pass the exits; a kaskad-dns of 3.3.7.1 had 1.1.1.1 for everyone),
        # and the direct dnsmasq only for a device whose settings let it go directly
        try:
            res["dns"] = dns.apply(st, runner, via_pool=True, direct=exits.direct_needed(st))
        except KaskadError as e:
            _warn(res, str(e))
    gsys.ensure_unit(runner, collect.UNIT, collect.unit_text())
    try:                                       # stage 1 (T6): the unit of probes — speed, services, country (§5.7)
        res["probe_unit"] = probe_unit.ensure(runner)
    except KaskadError as e:
        _warn(res, tr("gw_probe.warn.unit", err=e))
    try:                                       # wave 3: the daily lists of RoscomVPN; the first download at once
        res["rules_timer"] = rules_timer.ensure(runner)
        rules_timer.kick(runner)
    except KaskadError as e:
        _warn(res, tr("gw_ops.warn.rules_timer", err=e))
    return res


def _change(runner, mutate, rf=None):
    """Load -> mutate(copy, sec) -> the check ⚠РФ -> apply the copy if the gateway is on -> save state, then the
    secrets (with deletions). Returns (mutate result, apply result or None).

    ``rules.rfgate.check`` (SPEC-stage1-rf-mark-builtin §3.5) runs right after ``mutate``, before anything is applied
    or saved, whatever the state of the gateway: a new «Россия напрямую» without the consent is refused here with the
    model, the daemons and secrets.json untouched; ``rf`` — the tick of the caller ({"confirm", "via", "who", "lang",
    "orgs"}), written into the same copy, so the rules applied below already have it."""
    with state.locked(), gsys.hold():                   # F2: SIGHUP/SIGINT/SIGTERM do not cut the change
        st = state.load()
        new = copy.deepcopy(st)
        sec = secrets.load()
        with addr.using(new):
            out = mutate(new, sec)
        model.pin_default(new)                          # SEC-s1-r2 П3: a new node keeps «всё через выход» in the file
        said = []
        rfgate.check(st, new, rf, log=said)
        res = None
        if model.enabled(new):
            require_engine(new)
            res = _apply(new, sec, runner, probe=False)
        else:
            sec.save(keep_deleted=True)
        state.save(new)
        rfgate.said(said)                               # ⚠РФ lines only of a write that is made
        sec.save()
    return out, res


AFTER_LOCK_S = 30.0         # s the restart after the answer of the panel waits for the state lock (AmneziaWG ≈ 25 s)


def xray_after_answer(runner, token):
    """3.3.7.15 П3 (T-3315 п. 1.7): the restart of xray a write of the panel deferred to after its answer
    (``xray.after_answer``) — now, under the state lock: the render of the state as it is now (``xray.apply_now``,
    nothing deferred). Another token in the mark — a later write owes its own restart: nothing here. The answer is
    gone, so what this says goes to the journal: a failure (``gw_ops.log.xray_after_failed``) and the warnings of xray;
    a failure also stays as ``xray.after_failed`` — a warning of ``GET /api/gw/addresses`` until a render is applied
    (REV-3315-p3 З-1). The mark goes either way — the collector, no longer held back, applies on its next tick
    (nothing was noted as applied; a refusal of ``xray -test`` — its event ``xray_rejected`` in the feed). The state
    busy longer than AFTER_LOCK_S — the write that holds it applies xray itself, the mark expires by itself. -> the
    result of ``xray.apply`` | None."""
    try:
        with state.locked(timeout=AFTER_LOCK_S):
            cur = xray.after_token()
            if cur is not None and cur != token:
                return None
            try:
                st = state.load()
                if not model.enabled(st):
                    return None
                res = xray.apply_now(st, secrets.load(), runner, exits.pool(runner, roles=exits.ROLES), lock_wait=60)[0]
            finally:
                xray.after_done(token)
    except LockedError as e:                            # the state busy: the write that holds it applies xray itself
        util.log_tr("gw_ops.log.xray_after_failed", err=util.redact(str(e)).strip()[:300] or type(e).__name__)
        return None
    except KaskadError as e:
        err = util.redact(str(e)).strip()[:300] or type(e).__name__
        util.log_tr("gw_ops.log.xray_after_failed", err=err)
        xray.after_failed_note(err)                     # З-1: ``GET /api/gw/addresses`` says it until applied
        return None
    for w in list(res.get("warnings") or []) + list(res.get("notes") or []):
        util.log_action("GW: %s" % util.redact(w))
    return res


def apply_all(runner):
    """``gw apply``: everything from state (probing the exits first)."""
    pre = {"warnings": []}
    _xray_ready(state.load(), runner, pre)
    with state.locked(), gsys.hold():                   # F2
        st = state.load()
        require_engine(st)
        if not model.enabled(st):
            raise KaskadError(tr("gw_ops.err.gw_off"))
        new = copy.deepcopy(st)
        sec = secrets.load()
        res = _apply(new, sec, runner, pre=True)
        state.save(new)                                 # ⚠РФ: no person, device or profile changes — no rfgate.check
        sec.save()
    res["warnings"] = pre["warnings"] + res.get("warnings", [])
    rec = awgengine.reconcile(runner)                   # a key 3.1 of an exit on a node of the module: all to the program
    if rec:
        res["warnings"].append("AmneziaWG: %s" % rec["reason"])
        res["awg_engine"] = rec
    return res


def ensure_conntrack(runner):
    """conntrack(8) (package conntrack, ~100 KB) for the watchdog: installed at ``gw enable`` with the
    other packages of the gateway (apt under policy-rc.d), never in the middle of a repair. Returns the
    installed packages; a failure is a warning (the watchdog then only says it could not flush)."""
    if runner.which("conntrack"):
        return []
    return gsys.apt_install(runner, [CONNTRACK_PACKAGE])


def _enable_base(st, base, rebase_ok):
    """F6 (SPEC-R1 3.3.7.15, Б-6): ``gw enable --base`` / ``POST /api/gw/enable {base}`` — a new base only inside RFC
    1918 (``addr.new_base``; a refusal is a USAGE, not a broken state), and never a move of the addresses of devices:
    that is «Сеть шлюза» in the panel (``net_base`` — what changes, a confirmation, the safe apply of F7). A restore of
    a copy (``backup._apply_gateway``) passes ``rebase_ok`` and works as before. -> the base as text."""
    if rebase_ok:
        return str(addr.parse_base(base))
    try:
        chosen = str(addr.new_base(base))
    except StateError as e:
        raise KaskadError(str(e), code=USAGE, data={"field": "base"})
    cur = model.peek(st)["net"].get("base")
    old = str(addr.parse_base(cur)) if cur else addr.DEFAULT_BASE
    if old != chosen and any(d.get("ips") for d in model.peek(st)["devices"]):
        raise ConflictError(tr("gw_net.base.enable_moved"), data={"why": "net_base", "base": old})
    return chosen


def enable(runner, base=None, host=None, known_only=False, host_auto=False, rebase_ok=False):
    """Switch the gateway on: checks, dnsmasq, kernel modules, the address clients connect to, the base
    network, then _apply. ``host`` — that address or name (else detected, unless set by hand before); ``known_only``
    (the CLI and the panel without --force) — an IPv4 only one of «Адреса сервера» (``_check_host``); ``host_auto`` —
    ``host`` stays marked «determined by itself» (a copy of this very server restored, PLAN-multi-ip REV С-1).
    ``base`` — F6 ``_enable_base`` (``rebase_ok`` — the restore of a copy only)."""
    st0 = state.load()
    require_engine(st0)
    if base:
        _enable_base(st0, base, rebase_ok)              # F6: before any package or lock (Б-6)
    sup = support(runner)
    if not sup["supported"]:
        raise UnsupportedError(tr("gw_ops.err.unsupported_here", reason=sup["reason"]))
    want_host = net.parse_host(host) if host else None
    if want_host and known_only:
        _check_host(runner, want_host, st0)
    res_pre = {"installed": dns.ensure_installed(runner), "modules_failed": gsys.modprobe(runner, GW_MODULES)}
    try:
        res_pre["installed"] = res_pre["installed"] + ensure_conntrack(runner)
    except KaskadError as e:
        res_pre["conntrack_error"] = str(e)
    _xray_ready(st0, runner, res_pre)
    g0 = model.peek(st0)
    if want_host is None and (not g0.get("host") or g0.get("host_auto", True)):
        found, how = net.detect_host(runner)
        res_pre["host_detected"] = {"address": found, "how": how}
    with state.locked(), gsys.hold():                   # F2: enable is finished, or never begun
        st = state.load()
        require_engine(st)
        new = copy.deepcopy(st)
        g = model.gw(new)
        cur = g["net"].get("base")
        warnings = []
        if base:
            chosen = _enable_base(st, base, rebase_ok)  # again under the lock: devices may have come meanwhile
            why = addr.overlaps(runner, chosen)
            if why:
                raise ConflictError(tr("gw_ops.err.base_busy", base=chosen, why="; ".join(why[:3])))
        elif cur:
            chosen = str(addr.parse_base(cur))
            why = addr.overlaps(runner, chosen)
            if why:
                warnings.append(tr("gw_ops.warn.base_overlaps", base=chosen, why=why[0]))
        else:
            chosen, _ = addr.choose_base(runner)
        # addresses given before the first enable (inbounds and devices added while the gateway was off,
        # people kept by gw purge) are in the default base: they move with the base too
        old = str(addr.parse_base(cur)) if cur else addr.DEFAULT_BASE
        if old != chosen and any(d.get("ips") for d in g["devices"]):
            addr.rebase(new, old, chosen)
            warnings.append(tr("gw_ops.warn.rebased"))
        g["net"]["base"] = chosen
        g["net"]["dns"] = addr.dns_ip(new)
        if want_host is not None:
            from . import mip
            warnings.extend(mip.host_check(new, want_host))     # REV-mip-s34 С-1: as ``gw host``
            g["host"], g["host_auto"] = want_host, bool(host_auto)
        elif "host_detected" in res_pre:
            found = res_pre["host_detected"]
            if found["address"]:
                g["host"], g["host_auto"] = found["address"], True
            if found["how"] == "route-private":
                warnings.append(tr("gw_ops.warn.host_private",
                                   address=found["address"] or tr("gw_ops.warn.host_no_route")))
        for kind in model.installed_kinds(new):
            if kind in addr.KIND_SUBNET:
                g["inbounds"][kind]["subnet"] = addr.subnet(kind, new)
        g["enabled"] = True
        model.pin_default(new)                          # SEC-s1-r2 П3: «всё через выход» of a new node in the file
        # when: the panel tells «сборщик запускается» from «сборщик молчит» by it — files only, no command
        # (kaskad.panel.api._starting; stand T-G1b, N2/O)
        g["enabled_at"] = util.now_iso()
        # stage 1 (T7, SPEC-stage1-exits §1.3, §3.1): the first policy of the node — «Стандарт» on a node born on the
        # code of stage 1, «Как раньше» on an updated one; a policy there stays. The fingerprint counts it (rev + 1)
        policy.ensure_initial(new)
        rev.note(new)
        sec = secrets.load()
        res = _apply(new, sec, runner, pre=True)
        state.save(new)                                 # ⚠РФ: no person, device or profile changes — no rfgate.check
        sec.save()
    _tg_rebuild(runner)                                 # SE §2.9: the bot's proxy without its observatory now
    if res_pre.get("conntrack_error"):
        warnings.append(tr("gw_ops.warn.no_conntrack", err=res_pre["conntrack_error"]))
    res["warnings"] = res_pre.pop("warnings", []) + warnings + res.get("warnings", [])
    res.update(res_pre)
    res["base"] = chosen
    res["host"] = model.public_host(new)
    util.log_tr("gw_ops.log.enable", base=chosen, host=res["host"] or "—")
    return res


def _check_host(runner, h, st):
    """PLAN-multi-ip Ш2 (Л3): an IPv4 for clients is one of «Адреса сервера» (found on the machine, a NAT pair, added
    by hand — gw/addresses.py) or the address for clients it is already; else a refusal that names the addresses and
    the ways on (``gw addresses add``, ``--force``). A name (a domain) is not checked: its A records are DNS."""
    from . import addresses
    if not addresses.is_public4(h) or h == model.public_host(st):
        return
    addresses.check_known(runner, h, st)


def host(runner, value=None, detect=False, public=False, known_only=False):
    """Show, set (``value``) or re-detect ``gateway.host`` — the address clients connect to; the adapters
    re-render with it (issued configs carry it: give them out again when it changes). ``public`` (with ``detect``:
    «Вернуть на IP», круг 1 №39) — only a public IPv4: the route's private address (NAT without an answer of the IP
    echo) is a refusal, nothing changes. ``known_only`` (the CLI without --force, the menu, the panel, MCP): an IPv4
    ``value`` only one of «Адреса сервера» (``_check_host``; PLAN-multi-ip Ш2, Л3)."""
    st0 = state.load()
    if not value and not detect:
        g = model.peek(st0)
        return {"ok": True, "host": g.get("host") or "", "auto": bool(g.get("host_auto", True)), "apply": None}
    if value:
        h, auto, how = net.parse_host(value), False, "manual"
        if known_only:
            _check_host(runner, h, st0)
    else:
        h, how = net.detect_host(runner)
        auto = True
        if not h:
            raise KaskadError(tr("gw_ops.err.host_unknown"))
        if public and how == "route-private":
            raise KaskadError(tr("gw_ops.err.host_not_public", ip=h), code=CONFLICT)

    said = []

    def mutate(new, sec):
        from . import mip
        said.extend(mip.host_check(new, h))             # REV-mip-s34 С-1: outside «Приём подключений» — a refusal
        g = model.gw(new)
        g["host"], g["host_auto"] = h, auto
        return h

    _, res = _change(runner, mutate)
    util.log_action("GW host: %s (%s)" % (h, how))
    out = {"ok": True, "host": h, "auto": auto, "how": how, "apply": res}
    if said:
        out["warnings"] = said                          # the A records of a domain (printed with those of the apply)
    return out


def _stop_adapters(runner, st_off, sec, res):
    """Every installed adapter applied with the gateway off: they stop and close their UFW ports."""
    _apply_adapters(st_off, sec, runner, res)


def _stop_services(runner):
    out = {"collect": False, "xray": xray.stop(runner), "dns": dns.stop(runner)}
    if gsys.unit_active(runner, collect.UNIT) or gsys.unit_enabled(runner, collect.UNIT):
        runner.run(["systemctl", "disable", "--now", collect.UNIT])
        out["collect"] = True
    out["rules_timer"] = rules_timer.stop(runner)         # the lists stay in /var/lib/kaskad/rules
    return out


def _remove_table_part(runner, st_off):
    """The gw part out of the live table: the surgical script (flush + delete chains, then sets) if nft
    accepts it, else the whole table recreated without the gateway (``st_off``) with the live counters
    — the form of render_full verified on the stand. Forwards keep working and keep their counters."""
    live = nft.read_live(runner)
    script = net.teardown_script(live)
    ok, err = nft.check(script, runner)
    if not ok and live is not None:
        script = nft.render_full(st_off, live.get("counters")) + "add table %s\ndelete table %s\n" % (
            net.TABLE6, net.TABLE6)
    res = runner.run([nft.NFT, "-f", nft.STDIN], input=script)
    if not res.ok:
        raise KaskadError(tr("gw_ops.err.teardown_nft", err=(res.err or res.out or err).strip()[:300]))


def disable(runner):
    """Switch the gateway off: inbounds stop (their UFW ports close), services, the gw part of the table,
    routing, compat-in. Users, devices and inbounds stay in state (``gw enable`` brings everything back)."""
    with state.locked(), gsys.hold():                   # F2
        st = state.load()
        new = copy.deepcopy(st)
        model.gw(new)["enabled"] = False
        sec = secrets.load()
        res = {"warnings": []}
        _stop_adapters(runner, new, sec, res)
        res["stopped"] = _stop_services(runner)
        res["probe_removed"] = probe_unit.remove(runner)    # T6: the timer, the service and the template (§5.7)
        _remove_table_part(runner, new)
        if new.get("engine") == "nft":
            nft.apply(new, runner)                     # rewrites kaskad.nft without the gateway part
        res["routing"] = net.remove_routing(runner)
        res["networkd_released"] = net.release_networkd(runner)     # only if no egress leg needs it
        res["compat_in_removed"] = compat.remove_in(runner)
        state.save(new)                                 # ⚠РФ: no person, device or profile changes — no rfgate.check
        sec.save()
        _drop_live()                                    # сборщик остановлен: его последний снимок говорит «включён»
    _tg_rebuild(runner)                                 # SE §2.9: the bot's proxy probes by itself again (T7)
    util.log_tr("gw_ops.log.disable")
    res["ok"] = True
    return res


def _drop_live():
    """Remove /run/kaskad/live.json: the collector is stopped with the gateway, and its last snapshot (enabled,
    running, devices online) would keep the panel and the bot showing a working gateway (stand T-G1, D1-1)."""
    try:
        os.unlink(paths.live_json())
    except OSError:
        pass


def _core_down(runner):
    """The core's own pieces down: services (xray, DNS, collector), ip rule/table 200/kgw0, compat-in,
    the ip6 table. Idempotent; the inbounds are not touched."""
    _stop_services(runner)
    net.remove_routing(runner)
    net.release_networkd(runner)
    compat.remove_in(runner)
    runner.run([nft.NFT, "delete", "table", net.TABLE6_FAMILY, net.TABLE6_NAME])


@gsys.held                                              # F2
def teardown(runner):
    """Everything of the gateway off WITHOUT touching state.json (``net teardown``, rollback to 2.x)."""
    try:
        st = state.load()
    except KaskadError:
        st = state.empty()
    off = copy.deepcopy(st)
    model.gw(off)["enabled"] = False
    try:
        sec = secrets.load()
    except KaskadError:
        sec = secrets.Store({})
    _stop_adapters(runner, off, sec, {})
    _core_down(runner)
    return True


# ── inbounds ───────────────────────────────────────────────────────────────

def _listeners(runner):
    if not runner.which("ss"):
        return []
    res = runner.run(["ss", "-Hltnup"])
    return conflicts.parse_ss(res.out) if res.ok else []


def _taken(st, runner, proto, skip_kind=None):
    """Ports of ``proto`` taken by forwards, other inbounds and listeners reachable from outside."""
    taken = set(f["port"] for f in st.get("forwards") or [] if f.get("proto") == proto)
    for kind in model.installed_kinds(st):
        if kind == skip_kind:
            continue
        ad = registry.get(kind)
        if ad is not None:
            taken.update(p for pr, p in _ports_of(ad, st) if pr == proto)
    taken.update(l["port"] for l in _listeners(runner) if l["proto"] == proto and conflicts.exposed(l["addr"]))
    return taken


def free_port(st, runner, protos, skip_kind=None):
    """A random port free for every protocol in ``protos``."""
    protos = [protos] if isinstance(protos, str) else list(protos)
    taken = set()
    for pr in protos:
        taken |= _taken(st, runner, pr, skip_kind)
    for _ in range(200):
        p = _rng.randrange(*RANDOM_PORTS)
        if p not in taken:
            return p
    raise KaskadError(tr("gw_ops.err.no_free_port", protos="/".join(protos)))


def free_port_for(st, runner, kind, ports):
    """The first of ``ports`` free for ``kind`` over tcp (its own port is no conflict), else a random free one."""
    for p in ports:
        if not port_conflicts(st, runner, kind, "tcp", p):
            return p
    return free_port(st, runner, ["tcp"], skip_kind=kind)


def port_conflicts(st, runner, kind, proto, port):
    """[{"kind", "detail"}] — forwards and other inbounds on proto/port, plus kaskad.conflicts.check
    (ssh, listener, docker, foreign_dnat). An adapter's ``port_shared(st, proto, port, conflict)`` drops what is no
    conflict for it (VLESS on 443 in front of the node's own site — site.front)."""
    found = _port_conflicts(st, runner, kind, proto, port)
    ad = registry.get(kind) if kind in model.INBOUND_KINDS else None
    if kind in model.XRAY_KINDS and ad is not None and (proto, port) in _ports_of(ad, st):
        # its own port, held by the gateway xray for it (VLESS on 8443 asked for 8443 again — site.front's undo): no
        # conflict, it does not move to a random port and the links given out keep working
        found = [c for c in found if not (c["kind"] == "listener" and "xray" in c["detail"])]
    fn = getattr(ad, "port_shared", None)
    if callable(fn):
        found = [c for c in found if not fn(st, proto, port, c)]
    return found


def _port_conflicts(st, runner, kind, proto, port):
    out = []
    f = state.find_forward(st, proto, port) if (st.get("forwards")) else None
    if f is not None:
        out.append({"kind": "forward", "detail": tr("gw_ops.port.by_forward", to=f["to"], to_port=f["to_port"])})
    on = model.enabled_kinds(st)
    for other in model.installed_kinds(st):
        if other == kind:
            continue
        ad = registry.get(other)
        if ad is not None and (proto, port) in _ports_of(ad, st):
            # a protocol switched off keeps its port: switched on again, it takes it back (круг 1 №14)
            key = "gw_ops.port.by_inbound" if other in on else "gw_ops.port.by_inbound_off"
            out.append({"kind": "gateway", "detail": tr(key, title=ad.title or other)})
    svc = _service_word(kind, port)
    if svc:
        out.append({"kind": "service", "detail": tr("gw_ops.port.by_service", what=svc)})
    # a Kaskad service holding the port shows in ``ss`` too (the panel, python3): said once, by its name
    out.extend(dict(c, detail=_inbound_words(c["detail"])) for c in conflicts.check(proto, port, runner)
               if c["kind"] != "gateway" and not (svc and c["kind"] == "listener"))
    return out


# 12400…12999: TPROXY of the xray exits (12400+N), of the Russian ones (12601…12698), pool-socks 12699, the checks of the
# exits 12700+N (gw/exits.py, gw/xray.py) — 127.0.0.1, but an inbound on every address cannot take them
EXIT_SERVICE_PORTS = (exits.VLESS_PORT_BASE, 12999)


def _service_word(kind, port, panel=None):
    """What of Kaskad itself listens on ``port`` (any protocol: its services hold tcp and udp, mostly on 127.0.0.1 —
    an inbound on every address of the node cannot bind it then), "" — nothing: the xray of the gateway (TPROXY, DNS,
    metrics, statistics), the xray of «Прокси для API Telegram», dnsmasq of the gateway, the ports of the exits, the
    panel (127.0.0.1:1980 and the port of its open mode). ``kind`` "panel" — the panel's own ports are no conflict of its
    own choice (``panel.conf.choose_port``); ``panel`` — its ports read once by the caller (``_panel_ports``)."""
    fixed = {xray.TPROXY_PORT: "gw_xray.port.tproxy", xray.DNS_PORT: "gw_xray.port.dns",
             xray.METRICS_PORT: "gw_xray.port.metrics", xray.STATS_PORT: "gw_xray.port.stats",
             xray.EGRESS_API_PORT: "gw_ops.port.svc.egress", dns.PORT: "gw_ops.port.svc.dnsmasq"}
    if port in fixed:
        return tr(fixed[port])
    if EXIT_SERVICE_PORTS[0] <= port <= EXIT_SERVICE_PORTS[1]:
        return tr("gw_ops.port.svc.exits")
    if kind != "panel" and port in (_panel_ports() if panel is None else panel):
        return tr("gw_ops.port.svc.panel")
    return ""


def _panel_ports():
    try:
        from ..panel import conf as pconf
        cfg = pconf.load()
    except Exception:                           # noqa: BLE001 — a port check never fails on the panel's settings
        return set()
    out = {pconf.LOCAL_PORT}
    pub = cfg.get("public") or {}
    if pub.get("on") and pub.get("port"):
        out.add(pub["port"])
    return out


# kaskad.conflicts говорит словами проброса («проброс отрежет вход…»); для порта входа — его словами (стенд T-M6, F5).
# Хвост причины — conflicts.effect(name) на языке вызова: он и заменяется (по имени), не русские слова.
# name of conflicts.EFFECTS -> the key of the inbound's own tail (None: the tail goes away)
# «listener»: «подключения к нему снаружи перестанут доходить» is what a forward breaks; a protocol is just not put on
# the port — the tail goes away (R-333 С1: «занят другой программой — его слушает xl2tpd — подключения… перестанут…»)
_FWD_WORDS = {"ssh": "gw_ops.port.effect.ssh", "foreign_dnat": "gw_ops.port.effect.foreign_dnat", "gateway": None,
              "listener": None}


def _inbound_words(detail):
    for name, key in _FWD_WORDS.items():
        detail = detail.replace(conflicts.effect(name), (" — " + tr(key)) if key else "")
    return detail.strip()


def _busy_text(found):
    """«…: a; b» — каждая причина один раз (у входа tcp+udp одна и та же строка SSH шла дважды)."""
    seen = []
    for c in found:
        if c["detail"] not in seen:
            seen.append(c["detail"])
    return "; ".join(seen)


def _fields(ad):
    """{field: [protos]} in the order of the adapter's port fields."""
    out = []
    for field, proto in _port_fields(ad):
        for f, protos in out:
            if f == field:
                protos.append(proto)
                break
        else:
            out.append((field, [proto]))
    return out


def _candidates(ad, st):
    """Ports a new inbound tries first, in order (VLESS: 443, then 8443 — ``port_candidates``); [] — its default."""
    fn = getattr(ad, "port_candidates", None)
    try:
        got = list(fn(st) or []) if callable(fn) else []
    except Exception:                           # noqa: BLE001 — an adapter of another work package
        got = []
    return [p for p in got if isinstance(p, int) and not isinstance(p, bool) and 0 < p <= 65535]


def _first_free(st, runner, kind, protos, ports, planned=None):
    """The first of ``ports`` nothing holds (forwards, other inbounds, listeners, Docker…; ``planned`` — the ports a
    plan already gave to other kinds) -> (port or None, [(port, first conflict)] of the busy ones before it)."""
    busy = []
    for p in ports:
        found = _plan_conflicts(st, runner, kind, protos, p, planned) if planned is not None else \
            [c for pr in protos for c in port_conflicts(st, runner, kind, pr, p)]
        if not found:
            return p, busy
        busy.append((p, found[0]))
    return None, busy


def _kept_port(kept, field, first):
    """The port ``field`` of a removed inbound's record: int (0 — off: OpenVPN over TCP), None if there is none."""
    p = (kept or {}).get(field)
    if not isinstance(p, int) or isinstance(p, bool) or not 0 <= p <= 65535 or (first and p == 0):
        return None
    return p


def _choose_ports(st, ad, kind, port, force, runner, kept=None):
    """{field: port} for the record. An explicit port -> conflicts refuse (code 3) unless --force; a busy
    default port -> a random free one (said in the notes); a field already set stays. ``kept`` — the record of
    the removed inbound (a reinstall): its ports come back when they are free — the settings given to the devices
    work on; a port taken meanwhile is replaced by a free one. -> (fields, notes, moved: [(field, old, new)])."""
    rec = model.inbound(st, kind) or {}
    defaults = ad.defaults(st) or {}
    out, notes, moved = {}, [], []
    for i, (field, protos) in enumerate(_fields(ad)):
        cur = rec.get(field)
        old = _kept_port(kept, field, i == 0)
        if i == 0 and port is not None:
            p = state.parse_port(port)
            found = [] if (force or cur == p) else [c for pr in protos for c in port_conflicts(st, runner, kind, pr, p)]
            if found:
                raise ConflictError(tr("gw_ops.err.port_busy", port=p, protos="+".join(protos), why=_busy_text(found)),
                                    data={"kind": found[0]["kind"], "conflicts": found})
            out[field] = p
            if old is not None and old != p:
                moved.append((field, old, p))
            continue
        if isinstance(cur, int) and not isinstance(cur, bool) and (cur or field in rec):
            out[field] = cur
            continue
        if old is not None:
            found = [c for pr in protos for c in port_conflicts(st, runner, kind, pr, old)] if old else []
            if not found:
                out[field] = old
                continue
            alt = free_port(st, runner, protos, kind)
            notes.append(tr("gw_ops.note.old_port_busy", port=old, protos="+".join(protos), why=found[0]["detail"],
                            alt=alt))
            out[field] = alt
            moved.append((field, old, alt))
            continue
        cands = _candidates(ad, st) if i == 0 else []
        if cands:                                      # VLESS: 443, else 8443, else a random free port
            p, busy = _first_free(st, runner, kind, protos, cands)
            if p is None:
                p = free_port(st, runner, protos, kind)
            if busy:
                notes.append(tr("gw_ops.note.ports_busy", ports=", ".join("%d/%s" % (b, "+".join(protos)) for b, _ in busy),
                                why=busy[0][1]["detail"], alt=p))
            out[field] = p
            continue
        p = defaults.get(field, _default_port(ad) if i == 0 else None)
        if p is None:
            continue                                   # the adapter decides (e.g. OpenVPN TCP off = 0)
        if not p:
            out[field] = free_port(st, runner, protos, kind) if i == 0 else 0
            continue
        found = [c for pr in protos for c in port_conflicts(st, runner, kind, pr, p)]
        if found:
            alt = free_port(st, runner, protos, kind)
            notes.append(tr("gw_ops.note.ports_busy", ports="%d/%s" % (p, "+".join(protos)), why=found[0]["detail"],
                            alt=alt))
            p = alt
        out[field] = p
    return out, notes, moved


# ── the port of the panel's «Установить …?» (круг 1 №14) ──────────────────────
NEAR_SPAN = 200            # how far from the recommended port the nearest free one is looked for (both sides)
PICK_CONFIRM = 5           # free by the quick reading, busy by port_conflicts (Docker, a site of its own…): tries


def recommended_ports(ad, st):
    """The ports the code itself gives a new inbound of ``ad``, in order: its candidates (VLESS, Hysteria2: 443, 8443),
    else its default port (WireGuard 51820, OpenVPN 1194, SSTP 4433, OpenConnect 4443, SOCKS5 1080); [] — the port is
    drawn (AmneziaWG) or the protocol has no port of its own (L2TP, PPTP, IKEv2)."""
    cands = _candidates(ad, st)
    if cands:
        return cands
    if getattr(ad, "port_random", False):
        return []
    p = _default_port(ad)
    return [p] if p else []


def _offer_avoid(st, kind):
    """Ports a suggestion leaves alone though they are free: the recommended ports of the other protocols and the
    kept ports of removed ones (their settings work again when they come back on them)."""
    out = set()
    for k in model.INBOUND_KINDS:
        ad = registry.get(k)
        if k == kind or ad is None:
            continue
        out.update(recommended_ports(ad, st))
        kept = model.kept_inbound(st, k)
        for field, _ in _fields(ad):
            p = _kept_port(kept, field, False)
            if p:
                out.add(p)
    return out


def _busy_many(st, runner, kind, protos, ports):
    """The ports of ``ports`` busy for ``kind`` by one reading of the system (``conflicts.check_many``: one ``ss``, one
    read of iptables per protocol) plus forwards, the other inbounds and Kaskad's services."""
    ours = set()
    for k in model.installed_kinds(st):
        ad = registry.get(k)
        if k != kind and ad is not None:
            ours.update(_ports_of(ad, st))
    ours.update((f.get("proto"), f.get("port")) for f in st.get("forwards") or [])
    panel = _panel_ports()
    busy = set(p for p in ports if _service_word(kind, p, panel))
    for pr in protos:
        busy.update(p for p in ports if (pr, p) in ours)
        busy.update(p for p, found in conflicts.check_many(pr, ports, runner, st=st).items()
                    if any(c["kind"] != "gateway" for c in found))
    return busy


def _pick(st, runner, kind, protos, ports, avoid):
    """The first of ``ports`` (in order, ``avoid`` skipped) nothing holds — the quick reading over all of them, the
    chosen one confirmed by ``port_conflicts`` (what the install itself checks). None — none."""
    seen, order = set(), []
    for p in ports:
        if p not in seen and p not in avoid:
            seen.add(p)
            order.append(p)
    busy = _busy_many(st, runner, kind, protos, order)
    tries = 0
    for p in order:
        if p in busy:
            continue
        if not [c for pr in protos for c in port_conflicts(st, runner, kind, pr, p)]:
            return p
        tries += 1
        if tries >= PICK_CONFIRM:
            break
    return None


def _random_free(st, runner, ad, kind, protos):
    """A random free port: AmneziaWG — from its own range (``default_port`` draws it), the others — RANDOM_PORTS."""
    draw = ad.default_port if getattr(ad, "port_random", False) else (lambda: _rng.randrange(*RANDOM_PORTS))
    got = _pick(st, runner, kind, protos, [draw() for _ in range(64)], _offer_avoid(st, kind))
    if got is None:
        raise KaskadError(tr("gw_ops.err.no_free_port", protos="/".join(protos)))
    return got


def port_offer(runner, kind, port=None, shuffle=False, st=None):
    """The port field of the panel's «Установить …?» (круг 1 №14), read-only. What to put in it at once so that only
    «Установить» is left: the installed inbound — its port; a removed one — its kept port when free (its settings work
    again; busy — ``kept_busy`` says why); else the first free recommended port; else the free port nearest to the
    highest recommended one (OpenVPN: 1195, 1193…; never below 1024, never another protocol's recommended or kept port);
    else (AmneziaWG, nothing near) a random free one. ``recommended`` — the ports of ``recommended_ports`` with what
    holds them; ``port`` — is this one free for ``kind`` (``check``: the same ``port_conflicts`` the install refuses
    by: forwards, other inbounds — switched off too, Kaskad's services, the site, SSH, listeners, Docker, foreign DNAT —
    over the protocols of the inbound: OpenVPN udp, OpenConnect tcp+udp); ``shuffle`` — a random free port instead.

    -> {"kind", "fixed": no port of its own (L2TP, PPTP, IKEv2), "protos", "port", "recommended": [{"port", "free",
        "why"}], "kept", "kept_busy", "check": {"port", "free", "why"} | None}"""
    ad = adapter(kind)
    st = state.load() if st is None else st
    fields = _fields(ad)
    out = {"kind": kind, "fixed": not fields, "protos": [], "port": None, "recommended": [], "kept": None,
           "kept_busy": "", "check": None}
    if not fields:
        return out
    field, protos = fields[0]
    out["protos"] = list(protos)
    rec = model.inbound(st, kind)
    cur = (rec or {}).get(field)
    cur = cur if isinstance(cur, int) and not isinstance(cur, bool) and cur > 0 else None

    def why(p):                                        # its own port is no conflict (as _choose_ports: cur == p)
        return "" if p == cur else _busy_text([c for pr in protos for c in port_conflicts(st, runner, kind, pr, p)])
    recs = recommended_ports(ad, st)
    for p in recs:
        w = why(p)
        out["recommended"].append({"port": p, "free": not w, "why": w})
    if port is not None:
        p = state.parse_port(port)
        w = why(p)
        out["check"] = {"port": p, "free": not w, "why": w}
    if shuffle:
        out["port"] = _random_free(st, runner, ad, kind, protos)
        return out
    if cur:
        out["port"] = cur
        return out
    old = _kept_port(model.kept_inbound(st, kind) if rec is None else None, field, True)
    if old:
        out["kept"] = old
        w = why(old)
        if not w:
            out["port"] = old
            return out
        out["kept_busy"] = w
    free = [r["port"] for r in out["recommended"] if r["free"]]
    if free:
        out["port"] = free[0]
        return out
    if recs:
        base = max(recs)
        near = [q for d in range(1, NEAR_SPAN + 1) for q in (base + d, base - d) if 1024 <= q <= 65535]
        got = _pick(st, runner, kind, protos, near, _offer_avoid(st, kind) | set(recs))
        if got is not None:
            out["port"] = got
            return out
    out["port"] = _random_free(st, runner, ad, kind, protos)
    return out


# What the settings given to a device carry besides the ports (WireGuard, AmneziaWG: the server key, the line and
# its parameters); certificates, PSK and passwords live in secrets.json and stay there when an inbound is removed.
# (field, key of its words) — the words are said through tr() when the text goes out
CLIENT_FIELDS = (("pub", "gw_ops.field.pub"), ("line", "gw_ops.field.line"), ("params", "gw_ops.field.params"),
                 ("security", "gw_ops.field.security"), ("sni", "gw_ops.field.sni"),
                 ("short_id", "gw_ops.field.short_id"), ("hop", "gw_ops.field.hop"))
REISSUE_SHOWN = 10                     # devices named in the text; the panel gets them all


def _reissue_devices(st, kind):
    """The devices with ``kind`` in their access — whom to give the settings again (F-2)."""
    devs = [d for d in model.devices(st) if kind in (d.get("access") or [])]
    users = dict((u.get("id"), u.get("name")) for u in model.users(st))
    return [{"id": d.get("id"), "name": d.get("name") or d.get("id"), "platform": d.get("platform") or "other",
             "user": d.get("user"), "user_name": users.get(d.get("user")) or ""} for d in devs]


def _reissue_names(rows):
    return ["%s (%s)" % (r["name"], r["user_name"]) if r["user_name"] else r["name"] for r in rows]


def _who(names):
    """«a, b, c» of the first REISSUE_SHOWN names + « и ещё N» when there are more."""
    more = len(names) - REISSUE_SHOWN
    return ", ".join(names[:REISSUE_SHOWN]) + (tr("gw_ops.reissue.more", n=more) if more > 0 else "")


def _changed_words(kept, rec, moved):
    """What a reinstall changed of the settings given to the devices, in words of the language of the call."""
    changed = [tr("gw_ops.field.port_moved", old=old, new=new) for _, old, new in moved]
    norm = lambda r, f: ((r or {}).get(f) or "reality") if f == "security" else (r or {}).get(f)    # noqa: E731
    return changed + [tr(what) for f, what in CLIENT_FIELDS if f in kept and norm(kept, f) != norm(rec, f)]


def _previous(st, kind, title, kept, rec, moved):
    """What a reinstall kept of the removed inbound's settings, for the menu and the panel. None — there was no kept
    record (a first install). ``restored``: the port, the keys and the parameters are the same — what the devices were
    given works again; else ``changed`` says what moved and ``text`` whom to give the settings again."""
    if kept is None:
        return None
    changed = _changed_words(kept, rec, moved)
    rows = _reissue_devices(st, kind)
    out = {"restored": not changed, "changed": changed, "devices": rows, "text": ""}
    if changed and rows:
        out["text"] = tr("gw_ops.reissue.server_changed", title=title, changed=", ".join(changed),
                         who=_who(_reissue_names(rows)))
    return out


def _kept_ports(ad, kept):
    """["40850/udp", …] of a removed inbound's kept record (none for L2TP, PPTP, IKEv2: their ports are fixed)."""
    out = []
    for i, (field, protos) in enumerate(_fields(ad)):
        p = _kept_port(kept, field, i == 0)
        if p:
            out.extend("%d/%s" % (p, pr) for pr in protos)
    return out


def inbound_list(runner, st=None, with_status=True):
    """Rows of every inbound kind. ``kept`` of a removed one: {"ports"} — its port and keys wait for a reinstall."""
    st = state.load() if st is None else st
    rows = []
    for kind in model.INBOUND_KINDS:
        ad = registry.get(kind)
        rec = model.inbound(st, kind)
        kept = model.kept_inbound(st, kind) if rec is None and ad is not None else None
        row = {"kind": kind, "title": ad.title if ad else kind, "engine": ad.engine if ad else "",
               "available": ad is not None, "installed": rec is not None,
               "enabled": bool(rec and rec.get("enabled", True)), "ports": [], "running": False, "sessions": 0,
               "devices": len(model.devices_for(st, kind)), "warnings": [],
               "kept": None if kept is None else {"ports": _kept_ports(ad, kept)}}
        from . import rename                                        # T13: the label — only shown, never issued
        row["label"] = rename.inbound_label(st, kind)
        row["shown"] = rename.inbound_shown(st, kind)
        if ad is not None and rec is not None:
            row["ports"] = ports_shown(ad, st)
            if with_status and row["enabled"]:
                try:
                    s = ad.status(runner) or {}
                except KaskadError:
                    s = {}
                row["running"] = bool(s.get("running"))
                row["sessions"] = len(s.get("sessions") or [])
                row["warnings"] = [util.redact(w) for w in s.get("warnings") or [] if isinstance(w, str)]
                if s.get("core") in ("kernel", "userspace"):         # AmneziaWG: module of the kernel or amneziawg-go
                    row["core"] = s["core"]
            if "core" not in row and hasattr(ad, "unit_core"):      # off or no status: what its unit runs (files)
                core = ad.unit_core()
                if core in ("kernel", "userspace"):
                    row["core"] = core
            if kind in gwmtu.KINDS:                                 # §6.1: server and client MTU, a card too narrow
                p = gwmtu.inbound_plan(st, kind)
                row["mtu"] = {"server": p["server"], "client": p["client"], "narrow": p["narrow"], "low": p["low"],
                              "offer": p["offer"], "kept": bool(p.get("kept"))}
                if p["offer"]:
                    row["warnings"] = list(row["warnings"]) + [gwmtu.offer_text(kind, p, gwmtu.nic_of(st))]
        rows.append(row)
    return rows


def _needs_xray(st, runner, pool_exits=None):
    """An installed inbound runs inside the gateway xray (SOCKS5, VLESS) or the pool has VLESS exits."""
    if any(k in model.XRAY_KINDS for k in model.installed_kinds(st)):
        return True
    try:
        pool_exits = exits.pool(runner, roles=exits.ROLES) if pool_exits is None else pool_exits
    except KaskadError:
        return False
    return any(exits.is_xray(e) for e in pool_exits)


def _xray_ready(st, runner, res):
    """Before the lock (a download can take a minute): the gateway xray exists when something needs it. A
    failure is a warning — the other inbounds work without it."""
    if not _needs_xray(st, runner):
        return
    try:
        xray.ensure_binary(runner)
    except KaskadError as e:
        res.setdefault("warnings", []).append(tr("gw_ops.warn.no_xray", err=e))


def inbound_install(runner, kind, port=None, force=False, awg_engine=None, progress=None):
    """Install an inbound. A removed one comes back with its kept record (``gw inbound uninstall``): the same port
    when it is still free, the same parameters and options — and, as the server keys never left secrets.json, the
    settings the devices were given work again. ``previous`` of the result says so, or what moved and whom to give
    the settings again (``_previous``)."""
    ad = adapter(kind)
    req = ad.requirements(runner) or {}
    if not req.get("supported", True):
        raise UnsupportedError(tr("gw_ops.err.not_here", title=ad.title or kind,
                                  why=req.get("why") or tr("gw_ops.err.no_support")),
                               data={"needs_key": True, "key": "none"} if req.get("needs_key") else None)
    st0 = state.load()
    kept = model.kept_inbound(st0, kind) if model.inbound(st0, kind) is None else None
    fields, notes, moved = _choose_ports(st0, ad, kind, port, force, runner, kept)
    drop_hop = _hy2_kept_hop(st0, runner, kind, kept, fields, notes)
    if xray.UNIT in (req.get("needs") or []):
        xray.ensure_binary(runner, force=True)          # SOCKS5 and VLESS live in the gateway xray: no xray, no install
    extra = {}
    pre = getattr(ad, "pre_install", None)
    if callable(pre) and model.inbound(st0, kind) is None and not kept:
        extra, more = pre(runner, st0)                  # VLESS: the cover site, checked from the node (network)
        notes = list(notes) + list(more or [])
    engine = _awg_first(runner, st0, kind, awg_engine, progress)
    ad.install(runner)

    def mutate(new, sec):
        cur = model.inbound(new, kind)
        rec = dict(cur if cur is not None else (model.kept_inbound(new, kind) or {}))
        if cur is None and kind == VLESS:           # a snapshot in a kept record is never applied (SEC-vless-sh12 №2б);
            rec.pop("mode_pending", None)           # a refusal of TLS is of the other moment (§3.2 П4)
            rec.pop("tls_error", None)
        rec.setdefault("enabled", True)
        rec.update(fields)
        for k, v in (extra or {}).items():
            rec.setdefault(k, v)
        if kind in addr.KIND_SUBNET:
            rec["subnet"] = addr.subnet(kind, new)
        for k, v in (ad.defaults(new) or {}).items():
            rec.setdefault(k, v)
        if drop_hop:
            rec["hop"] = None
        model.set_inbound(new, kind, rec)
        model.forget_kept(new, kind)
        for d in model.devices_for(new, kind):
            addr.allocate_kind(new, d, kind)
        if not model.enabled(new):
            ad.prepare(new, sec, runner)
        return rec

    rec, res = _change(runner, mutate)
    if kind in model.XRAY_KINDS and (res or {}).get("xray_error"):
        _xray_install_failed(runner, ad, kind, st0, kept, res["xray_error"])
    if kind == VLESS:                                   # the mode by the site of this node (a copy from another one)
        notes = list(notes) + _vless_follow(runner, new=model.inbound(st0, kind) is None and not kept)
    st1 = state.load()
    final = model.inbound(st1, kind) or rec
    prev = _previous(st1, kind, ad.title or kind, kept, final, moved)
    with util.journal_lang():
        util.log_action(tr("gw_ops.log.inbound_install", kind=kind,
                           fields=" ".join("%s=%s" % kv for kv in sorted(fields.items())),
                           how="" if prev is None else (tr("gw_ops.log.install_restored") if prev["restored"] else
                                                       tr("gw_ops.log.install_changed",
                                                          changed=", ".join(_changed_words(kept, final, moved))))))
    out = {"ok": True, "kind": kind, "record": _public_record(final), "notes": notes, "previous": prev, "apply": res}
    if engine is not None:
        out["awg_engine"] = engine.get("engine")
        out["warnings"] = list(engine.get("warnings") or [])
    if kind in awgengine.INBOUND_LINE:
        rec_ = awgengine.reconcile(runner)                  # 3.1 on a node of the module: the whole node to the program
        if rec_:
            out.setdefault("warnings", []).append("AmneziaWG: %s" % rec_["reason"])
    return out


def _hy2_kept_hop(st0, runner, kind, kept, fields, notes):
    """A reinstall of Hysteria2 brings its range of port hopping back only when it still fits: the port outside it, none
    of its ports taken meanwhile (a neighbour installed while it was away, a listener) — a taken port of the range keeps
    the whole xray of the gateway from starting (REV-s1-t12 С-1). Else the range is dropped, said in ``notes``. -> drop."""
    if kind != HY2 or not kept:
        return False
    from .inbounds import hy2 as hin
    b = hin.hop_bounds(kept.get("hop"))
    if not b:
        return False
    p = fields.get("port")
    if isinstance(p, int) and b[0] <= p <= b[1]:
        why = tr("gw_ops.err.hy2_hop_port", port=p, hop="%d-%d" % b)
    else:
        found = hop_conflicts(st0, runner, b[0], b[1])
        if not found:
            return False
        why = "; ".join("%d/udp — %s" % (c["port"], c["detail"]) for c in found[:5])
    notes.append(tr("gw_ops.note.hy2_hop_dropped", hop="%d-%d" % b, why=why))
    return True


def _xray_install_failed(runner, ad, kind, st0, kept, why):
    """SOCKS5 or VLESS live inside the gateway xray: xray refused the config with the new inbound (T-M6-qs330b №1 —
    the wizard said «VLESS установлен» while nothing listened). A new inbound is taken back — the record (and the
    kept one it came from) as before, its files and UFW rule gone — and the install fails in words. One that was
    installed before stays as it is; the error is the same."""
    title = ad.title or kind
    if model.inbound(st0, kind) is None:
        def undo(new, sec):
            model.remove_inbound(new, kind)
            if kept is not None:
                model.keep_inbound(new, kind, kept)
            return None
        try:
            _change(runner, undo)
            ad.uninstall(runner)
        except KaskadError as e:
            util.log_tr("gw_ops.log.install_undo_failed", kind=kind, err=e)
        util.log_tr("gw_ops.log.install_xray_refused", kind=kind)
        raise KaskadError(tr("gw_ops.err.not_installed_xray", title=title, why=why))
    raise KaskadError(tr("gw_ops.err.not_working", title=title, why=why))


def _awg_first(runner, st0, kind, choice, progress):
    """The first AmneziaWG inbound of a node (no other one, no AmneziaWG leg): the engine before its unit is written —
    the module by default, the program on ``choice`` "program" (awgengine.first). Anything else: None."""
    if kind not in awgengine.INBOUND_LINE:
        return None
    if choice == "program" or choice == "kernel":
        return awgengine.first(runner, choice=choice, kind=kind, progress=progress, why="person:install")
    if awgengine.inbounds_of(st0) or awgengine.legs_of(st0, awgengine._pool(runner, None)):
        return None
    return awgengine.first(runner, kind=kind, progress=progress)


def _public_record(rec):
    """An inbound record without anything that looks secret (adapters keep secrets in secrets.json anyway)."""
    return dict((k, v) for k, v in (rec or {}).items() if "priv" not in k.lower() and "psk" not in k.lower())


@gsys.held                                              # F2: the record and the units go together
def inbound_uninstall(runner, kind):
    """Remove an inbound: its units, configs and UFW rules go; its record is kept (``model.keep_inbound``) and the
    server keys stay in secrets.json — a reinstall brings the same port and keys back (``inbound_install``), so the
    settings given to the devices work again. ``gw purge`` forgets both. VLESS — under the lock of a transition of
    its mode (§3.5 Н1, SEC-vless-sh12 придирка 5): never in the middle of somebody else's; one left behind is undone
    first, so the kept record never carries ``mode_pending``."""
    if kind not in model.INBOUND_KINDS:
        raise KaskadError(tr("gw_ops.err.unknown_kind", kind=repr(kind)), code=USAGE)
    if kind == VLESS:
        from ..site import front
        with front.vless_guard(runner):
            return _inbound_uninstall(runner, kind)
    return _inbound_uninstall(runner, kind)


def _inbound_uninstall(runner, kind):
    ad = registry.get(kind)

    def mutate(new, sec):
        rec = model.remove_inbound(new, kind)
        if rec is None:
            raise KaskadError(tr("gw_ops.err.not_installed", kind=registry.title(kind)))
        model.keep_inbound(new, kind, rec)
        return rec

    rec, res = _change(runner, mutate)
    if ad is not None:
        ad.uninstall(runner)                           # its units, configs and UFW rules
    hooks.forget(kind)
    util.log_tr("gw_ops.log.inbound_uninstall", kind=kind)
    return {"ok": True, "kind": kind, "kept": True, "apply": res}


def inbound_enable(runner, kind, on=True):
    adapter(kind)

    def mutate(new, sec):
        rec = model.inbound(new, kind)
        if rec is None:
            raise KaskadError(tr("gw_ops.err.not_installed_hint", kind=kind))
        rec["enabled"] = bool(on)
        return rec

    rec, res = _change(runner, mutate)
    util.log_action("GW inbound %s: %s" % ("enable" if on else "disable", kind))
    return {"ok": True, "kind": kind, "enabled": bool(on), "apply": res}


def inbound_port(runner, kind, port, proto=None, force=False, who=None):
    """The port of an inbound (CLI ``gw inbound port``, PUT …/port of an old tab, MCP ``inbound_port``, the SSH menu) — an
    alias of «Настройки протокола» (3.3.7.15 F3): one application, the marks «ждут новых настроек», «Вернуть прежние».
    The answer keeps its old form (kind, field, old, port, reissue, text, warnings, apply) with the new keys added;
    ``yes`` is implied (the old contract had no confirmation). ``who`` — the panel gives its own; else SSH_CONNECTION."""
    from . import pending
    return inbound_settings(runner, kind, port=port, proto=proto, force=force, yes=True,
                            who=who if who is not None else pending.who_ssh())


# ── «Настройки протокола» одним действием (3.3.7.15 F2 + F3, PLAN-n13 Ш2–Ш3, 8.4.1) ───────────────────────────────

SETTINGS_RESTART = {
    # what an application restarts, by the field it changes (8.4.1; the words — gw_ops.settings.restart.<word>)
    "wg": {"port": "syncconf", "mtu": "live"},
    "awg1": {"port": "awg", "mtu": "live"}, "awg2": {"port": "awg", "mtu": "live"}, "awg3": {"port": "awg", "mtu": "live"},
    "openvpn": {"port": "ovpn"}, "ocserv": {"port": "ocserv", "tls": "ocserv"}, "sstp": {"port": "sstp", "tls": "sstp"},
    "l2tp": {}, "pptp": {}, "ikev2": {},
    "vless": {"port": "xray", "tls": "xray"}, "socks5": {"port": "xray"}, "hy2": {"port": "xray"},
}
OVPN_INST = {"udp_port": "udp", "tcp_port": "tcp"}
ACCEL_RESTART = ("sstp",)   # the words of SETTINGS_RESTART that restart accel-ppp (it carries L2TP and PPTP as well)


def settings_last_path():
    return os.path.join(paths.gw_var_dir(), "settings-last.json")


def _last_read():
    """{kind: {"id", "at", "old", "new", "field", "what", "hard", "inst", "marked"}} — the last application of each
    protocol (F3); soft read: anything else is dropped."""
    import json
    try:
        doc = json.loads(util.read_text(settings_last_path()) or "{}")
    except ValueError:
        return {}
    if not isinstance(doc, dict):
        return {}
    # NET_SCOPE «base» — the last change of the network of the gateway (F6, lane Г): kept by every write of a protocol
    return dict((k, v) for k, v in doc.items() if (k in model.INBOUND_KINDS or k == NET_SCOPE) and isinstance(v, dict)
                and isinstance(v.get("id"), str) and isinstance(v.get("old"), dict) and isinstance(v.get("new"), dict))


def _last_write(kind, rec):
    """The record of ``kind`` set (``rec`` None — removed), the file 0600; a flock of its own (two protocols applied at
    once keep both records)."""
    import fcntl
    import json
    util.ensure_dir(paths.gw_run_dir(), 0o700)
    fd = os.open(os.path.join(paths.gw_run_dir(), "settings-last.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        doc = _last_read()
        if rec is None:
            if kind not in doc:
                return
            doc.pop(kind)
        else:
            doc[kind] = rec
        util.ensure_dir(paths.gw_var_dir(), 0o700)
        util.atomic_write(settings_last_path(), json.dumps(doc, sort_keys=True) + "\n", 0o600)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _settings_ver(st, kind):
    """``ver`` of a protocol's settings (§3 «Общее», З-3): the hash of its record without secrets."""
    from . import policy_ops as pops
    return pops.ver_of(_public_record(model.inbound(st, kind)))


def _set_mtu(new, kind, n):
    """F2: the client MTU of ``kind`` — ``inbounds.wg.mtu`` or ``inbounds.awgN.params.MTU``; the server follows at the
    ``gwmtu.refresh`` of ``_apply`` (its journal line «сервер MTU …» stays), on the live interface without a restart."""
    rec = model.inbound(new, kind)
    if kind == "wg":
        rec["mtu"] = int(n)
    else:
        params = dict(rec.get("params") or {})
        params["MTU"] = str(int(n))
        rec["params"] = params


def tls_choices(st, kind):
    """F4: the TLS versions ``kind`` can be given — SSTP and OpenConnect: all three; VLESS while its record is on TLS:
    «1.2 и 1.3» and «только 1.3» (Vision of the pinned xray refuses a client whose outer TLS is not 1.3 — «только 1.2»
    would cut every device); [] — no TLS block (VLESS Reality, the other protocols)."""
    from .inbounds import tunnel
    if kind == VLESS:
        return list(tunnel.TLS_VERSIONS[:2]) if (model.inbound(st, kind) or {}).get("security") == "tls" else []
    return list(tunnel.TLS_VERSIONS) if kind in ("sstp", "ocserv") else []


def _tls_check(st, kind, value):
    """F4: the versions asked for ``kind`` (a string of ``tls_choices``) -> the value; else a refusal (USAGE)."""
    title = registry.title(kind)
    choices = tls_choices(st, kind)
    if not choices:
        raise KaskadError(tr("gw_ops.settings.no_tls", title=title), code=USAGE)
    if not isinstance(value, str) or value not in choices:
        raise KaskadError(tr("gw_ops.settings.tls_bad", title=title, choices=", ".join(
            tls_text(v) for v in choices)), code=USAGE)
    return value


def tls_text(v):
    """«1.2 и 1.3» / «только 1.3» / «только 1.2» (an unknown value — as it is)."""
    return tr(TLS_WORDS[v]) if v in TLS_WORDS else str(v)


TLS_WORDS = {"1.2-1.3": "gw_ops.settings.tls.both", "1.3": "gw_ops.settings.tls.only13",
             "1.2": "gw_ops.settings.tls.only12"}


def _tls_of(st, kind):
    """F4: the versions ``kind`` works with — read softly, as its adapter reads them: VLESS takes anything but «1.3»
    as minVersion 1.2, i.e. «1.2 и 1.3» (a «1.2» written by hand — REV-3315-a2 П-1)."""
    from .inbounds import tunnel
    v = tunnel.tls_versions(model.inbound(st, kind))
    return tunnel.TLS_DEFAULT if kind == VLESS and v != "1.3" else v


def _set_tls(new, kind, v):
    """F4: ``inbounds.<kind>.tls.versions`` — «1.2 и 1.3» (as before 3.3.7.15) leaves no field behind (the record of
    3.3.7.14 again); other keys of ``tls`` (``alpn``, reserved, §0а) stay as they are."""
    rec = model.inbound(new, kind)
    t = dict(rec.get("tls")) if isinstance(rec.get("tls"), dict) else {}
    if v == "1.2-1.3":
        t.pop("versions", None)
    else:
        t["versions"] = v
    if t:
        rec["tls"] = t
    else:
        rec.pop("tls", None)


def _tls_warnings(st, kind, v):
    """F4 «Что изменится»: «только 1.3» at SSTP — Windows 10 and MikroTik have no TLS 1.3 by default (their devices of
    SSTP named), at OpenConnect — old applications. Each needs the confirmation (``need_yes``)."""
    if v != "1.3":
        return []
    if kind == "sstp":
        names = _reissue_names([d for d in _reissue_devices(st, kind) if d["platform"] in ("windows", "mikrotik")])
        return [tr("gw_ops.settings.tls_warn.sstp") + (" " + tr("gw_ops.settings.tls_warn.sstp_who", who=_who(names))
                                                       if names else "")]
    if kind == "ocserv":
        return [tr("gw_ops.settings.tls_warn.ocserv")]
    return []


def _settings_request(runner, st0, kind, ad, port, proto, mtu, force, tls=None):
    """The checks of the fields before any lock (§3 «Общее»): the port as ``inbound_port`` checked it (VLESS TLS — 443
    only, 443 next to the site — ``site.front``, the hops of Hysteria2, ``port_conflicts`` unless ``force``), the MTU —
    ``gwmtu.manual_check`` (F2), the TLS versions — ``_tls_check`` (F4). A field equal to the current value is no
    change. -> {"field", "protos", "port", "mtu", "tls", "front"} (None — not changed)."""
    rec = model.inbound(st0, kind) or {}
    out = {"field": None, "protos": [], "port": None, "mtu": None, "tls": None, "front": False}
    if tls is not None and _tls_check(st0, kind, tls) != _tls_of(st0, kind):
        out["tls"] = tls
    if port is None and proto is not None:
        raise KaskadError(tr("gw_ops.settings.proto_alone"), code=USAGE)
    if port is not None:
        p = state.parse_port(port)
        fields = [(f, protos) for f, protos in _fields(ad) if proto is None or proto in protos]
        if not fields:
            raise KaskadError(tr("gw_ops.err.no_port", kind=registry.title(kind), proto=proto or ""), code=USAGE)
        out["field"], out["protos"] = fields[0][0], list(fields[0][1])
        if rec.get(out["field"]) != p:
            _port_checks(runner, st0, kind, rec, out["field"], out["protos"], p, force, out)
            out["port"] = p
    if mtu is not None:
        if kind not in gwmtu.KINDS:
            raise KaskadError(tr("gw_mtu.manual.kinds", title=registry.title(kind)), code=USAGE)
        if isinstance(mtu, bool) or mtu != gwmtu.client_mtu(st0, kind):
            gwmtu.manual_check(st0, kind, mtu)
            out["mtu"] = int(mtu)
    return out


def _port_checks(runner, st0, kind, rec, field, protos, p, force, out):
    if kind == VLESS and rec.get("security") == "tls" and p != 443:
        raise KaskadError(tr("gw_ops.err.vless_tls_port"), code=CONFLICT)
    if kind == VLESS and p == 443 and _site_on(st0) and rec.get("port") != 443:
        # 443 is the site's: VLESS takes it only in front of the site, with TLS (site.front) — never Reality next to it
        from ..site import front
        ok, why = front.tls_possible(st0, runner)
        if front.want_of(rec) is not None:
            # a record with a choice of the mode (SPEC-vless-tls-default, решения координатора З-2): a port is no way
            # to switch the mode — the links of every device change; the switch is its own action with its price
            raise ConflictError(tr("gw_ops.err.vless_443_mode") if ok else
                                tr("gw_ops.err.vless_443_mode_not", why=why), data={"kind": "site", "need": "mode_tls"})
        if not ok:
            raise ConflictError(tr("gw_ops.err.vless_443_site", why=why), data={"kind": "site"})
        out["front"] = True
        return
    if kind == HY2:
        from .inbounds import hy2 as hin
        b = hin.hop_bounds(rec.get("hop"))
        if b and b[0] <= p <= b[1]:
            raise ConflictError(tr("gw_ops.err.hy2_hop_port", port=p, hop="%d-%d" % b), data={"kind": "gateway"})
    if not force:
        found = [c for pr in protos for c in port_conflicts(st0, runner, kind, pr, p)]
        if found:
            raise ConflictError(tr("gw_ops.err.port_busy", port=p, protos="+".join(protos), why=_busy_text(found)),
                                data={"kind": found[0]["kind"], "conflicts": found})


def _settings_mutate(new, kind, req):
    """The change itself (the plan on a copy, the write in ``_change``). -> {"port": old, "mtu": old} of what changed."""
    rec = model.inbound(new, kind)
    if rec is None:
        raise KaskadError(tr("gw_ops.err.not_installed", kind=registry.title(kind)))
    old = {}
    if req.get("port") is not None and rec.get(req["field"]) != req["port"]:
        old["port"] = rec.get(req["field"])
        rec[req["field"]] = req["port"]
    if req.get("mtu") is not None and gwmtu.client_mtu(new, kind) != req["mtu"]:
        old["mtu"] = gwmtu.client_mtu(new, kind)
        _set_mtu(new, kind, req["mtu"])
    if req.get("tls") is not None and _tls_of(new, kind) != req["tls"]:
        old["tls"] = _tls_of(new, kind)
        _set_tls(new, kind, req["tls"])
    return old


def settings_mutate(st, kind, changes):
    """F3, pure (for ``issue_preview`` of F7, CONTRACT-pending-3315): ``changes`` {"port"?, "proto"?, "mtu"?, "tls"?}
    put into ``st`` in place — nothing checked, written or applied. -> {"port": old, "mtu": old, "tls": old} of what
    changed."""
    req = {"field": None, "port": None, "mtu": None, "tls": None}
    if changes.get("tls") is not None:
        req["tls"] = _tls_check(st, kind, changes["tls"])
    if changes.get("port") is not None:
        fields = [f for f, protos in _fields(adapter(kind)) if changes.get("proto") in (None,) + tuple(protos)]
        if not fields:
            raise KaskadError(tr("gw_ops.err.no_port", kind=registry.title(kind), proto=changes.get("proto") or ""),
                              code=USAGE)
        req["field"], req["port"] = fields[0], state.parse_port(changes["port"])
    if changes.get("mtu") is not None:
        req["mtu"] = int(changes["mtu"])
    return _settings_mutate(st, kind, req)


def _settings_marks(st, kind, req):
    """Whom an application leaves without working settings (F1): the port — hard for every device of the protocol
    (OpenVPN with two ``remote``: soft while the other one is on and unchanged); the MTU — soft. -> {"ids", "what",
    "hard", "inst"}."""
    what, hard, inst = [], False, None
    if req.get("port") is not None:
        what.append("port")
        hard = True
        if kind == "openvpn" and req["field"] in OVPN_INST:
            inst = [OVPN_INST[req["field"]]]
            other = [f for f in OVPN_INST if f != req["field"]][0]
            v = (model.inbound(st, kind) or {}).get(other)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                hard = False                            # the other remote of the file still connects
    if req.get("mtu") is not None:
        what.append("mtu")
    devs = _reissue_devices(st, kind) if what else []
    return {"ids": [d["id"] for d in devs], "rows": devs, "what": what, "hard": hard, "inst": inst}


def _restart_rows(st, kind, fields):
    """«Что изменится»: what restarts for each of ``fields`` and what else a restart touches (inlog.shared)."""
    from . import inlog
    rows, by = [], {}
    for f in fields:
        word = (SETTINGS_RESTART.get(kind) or {}).get(f)
        if not word:
            continue
        if word in by:                                  # the port and TLS of SSTP: one restart, one line
            by[word].append(f)
        else:
            by[word] = [f]
            rows.append({"what": f, "word": word})
    for r in rows:
        # the line starts with the field (3.3.7.15 Д1, №13 r2 Н1): «Порт: …», «Порт и TLS: …» — the title is the window's
        names = [tr("gw_ops.settings.restart_field." + f) for f in by[r["word"]]]
        what = names[0] if len(names) == 1 else tr("gw_ops.settings.restart_fields", a=", ".join(names[:-1]), b=names[-1])
        r["text"] = tr("gw_ops.settings.restart." + r["word"], what=what)
    out = {"rows": rows, "with": [], "gateway": False}
    if any(r["word"] not in ("syncconf", "live") for r in rows):
        try:
            sh = inlog.shared(kind, model.enabled_kinds(st))
        except Exception:                           # noqa: BLE001 — an adapter of another package: no «also»
            sh = {"with": [], "gateway": False}
        out["with"] = [{"kind": k, "title": registry.title(k)} for k in sh.get("with") or []]
        out["gateway"] = bool(sh.get("gateway"))
    return out


def _settings_plan(st0, kind, req, who=None, keep=None, safe=False):
    """«Что изменится» (F3), nothing written: ``changed`` {field: {"old", "new"}}, ``restart``, ``reissue`` {"hard",
    "soft"}, ``warnings``, ``need_yes`` (hard marks, the warning ``via_xray`` of VLESS/SOCKS5/Hysteria2, the hit of the
    administrator — F7). ``safe`` — only for the words «безопасное применение недоступно» (the refusal is guard's)."""
    from . import pending
    rec0 = model.inbound(st0, kind) or {}
    new = copy.deepcopy(st0)
    with addr.using(new):
        _settings_mutate(new, kind, req)
    changed = {}
    if req.get("port") is not None:
        changed["port"] = {"old": rec0.get(req["field"]), "new": req["port"], "field": req["field"],
                           "protos": list(req["protos"])}
    if req.get("mtu") is not None:
        changed["mtu"] = {"old": gwmtu.client_mtu(st0, kind), "new": req["mtu"],
                          "server": gwmtu.server_mtu(gwmtu.budget(st0), gwmtu.inbound_s4(st0, kind), req["mtu"])}
    tls_warn = []
    if req.get("tls") is not None:
        changed["tls"] = {"old": _tls_of(st0, kind), "new": req["tls"]}
        tls_warn = _tls_warnings(st0, kind, req["tls"])
    marks = _settings_marks(st0, kind, req)
    hard = marks["rows"] if marks["hard"] else []
    soft = [] if marks["hard"] else marks["rows"]
    via_xray = bool(pending.via_xray(kind, changed))
    warnings = ([tr("gw_ops.settings.via_xray")] if via_xray else []) + tls_warn
    if req.get("front"):                                # REV-3315-a1 П-6: the switch to TLS said before «Применить»
        warnings.append(tr("gw_ops.settings.front_note"))
    hit = pending.admin_hit(st0, new, kind, who, changed)   # a code ("pool"/"base") only if the change cuts (F7)
    if keep is not None:
        keep["st_new"] = new                            # for pending.guard only: never in an answer (the whole state)
    # the words of lane Г (gw_pending.hit.*), never the code; also alone in ``hit_text`` (CONTRACT добавка 4)
    hit_text = (pending.hit_text(hit, kind) or "") if hit else ""
    if hit_text:
        warnings.append(hit_text)
    if (safe or hit) and not _last_can_write():
        # CONTRACT-pending-3315, добавка 3: nothing to undo with — guard (lane Г) refuses 409 pending_no_undo; the plan
        # says the reason before «Применить». The refusal itself is guard's only.
        warnings.append(tr("gw_ops.settings.no_undo", title=registry.title(kind), err=settings_last_path()))
    restart = _restart_rows(st0, kind, list(changed))
    title = registry.title(kind)
    # CONTRACT добавка 5: the lines of ``plan_lines`` «перезапуск задевает и…» and «выдайте заново…» alone for the
    # panel (the same ``tr``; no line — "")
    with_text = tr("gw_ops.settings.restart_with", titles=", ".join(w["title"] for w in restart["with"])) \
        if restart["with"] else ""
    reissue_text = dict((k, tr("gw_ops.settings.reissue_" + k, title=title, who=_who(_reissue_names(rows))) if rows
                         else "") for k, rows in (("hard", hard), ("soft", soft)))
    return {"kind": kind, "title": title, "changed": changed,
            "restart": restart, "reissue": {"hard": hard, "soft": soft},
            "warnings": warnings, "via_xray": via_xray, "admin_hit": hit or None, "hit_text": hit_text,
            "reissue_text": reissue_text, "with_text": with_text,
            "need_yes": bool(hard) or via_xray or bool(hit) or bool(tls_warn), "front": bool(req.get("front"))}


def _accel_cut(runner, plan):
    """A restart of accel-ppp cuts every PPP connection (SSTP, L2TP, PPTP; решение координатора к REV-3315-a2 п. 7):
    N > 0 live sessions — the line «Оборвутся N подключений» and the confirmation; none (or accel-cmd does not answer)
    — as before, without one. Here and not in ``_settings_plan``: the plan stays pure (no accel-cmd)."""
    if not any(r.get("word") in ACCEL_RESTART for r in plan["restart"]["rows"]):
        return
    from .inbounds import accel, tunnel
    if not tunnel.unit_installed(accel.UNIT):          # accel-ppp is not here: nothing to cut, no accel-cmd to ask
        return
    n = len(accel.sessions(runner))
    if n:
        plan["warnings"].append(trn("gw_ops.settings.sessions_cut", n))
        plan["need_yes"] = True


def settings_changed_text(changed):
    parts = []
    for f in ("port", "mtu", "tls"):
        c = changed.get(f)
        if c:
            old, new = (tls_text(c.get("old")), tls_text(c.get("new"))) if f == "tls" else (c.get("old"), c.get("new"))
            parts.append(tr("gw_ops.settings.change." + f, old=old, new=new))
    return ", ".join(parts)


def plan_lines(plan):
    lines = [r["text"] for r in plan["restart"]["rows"]]
    if plan["restart"]["with"]:
        lines.append(tr("gw_ops.settings.restart_with", titles=", ".join(w["title"] for w in plan["restart"]["with"])))
    names = _reissue_names(plan["reissue"]["hard"])
    if names:
        lines.append(tr("gw_ops.settings.reissue_hard", title=plan["title"], who=_who(names)))
    names = _reissue_names(plan["reissue"]["soft"])
    if names:
        lines.append(tr("gw_ops.settings.reissue_soft", title=plan["title"], who=_who(names)))
    return lines + list(plan["warnings"])


def inbound_settings(runner, kind, port=None, proto=None, mtu=None, tls=None, net=None, dry=False, yes=False,
                     force=False, safe=False, who=None, ver=None):
    """«Настройки протокола» одним действием (3.3.7.15 F3): порт, MTU (F2, WireGuard/AmneziaWG), TLS (F4) — проверки
    до блокировки, план «Что изменится» (``dry`` — только он, ничего не пишется), подтверждение (``need_yes`` без
    ``yes`` — ConflictError с планом), одна запись ``_change`` (один ``_apply``, не больше одного перезапуска службы),
    пометки «ждут новых настроек» (F1) с id применения, «порт закрыт?» (``inlog.mark_issued``), запись «последнего
    применения» для «Вернуть прежние». ``ver`` — версия записи протокола из ``GET …/settings`` (панель; CLI — без
    неё): другая — 409 ``object_changed``. Запись идёт через ``pending.guard`` (F7: безопасное применение)."""
    if kind == VLESS and not dry:                       # a port of VLESS: under the lock of a transition (§3.5 Н1)
        from ..site import front
        with front.vless_guard(runner):
            return _inbound_settings(runner, kind, port, proto, mtu, tls, net, dry, yes, force, safe, who, ver)
    return _inbound_settings(runner, kind, port, proto, mtu, tls, net, dry, yes, force, safe, who, ver)


def _inbound_settings(runner, kind, port, proto, mtu, tls, net, dry, yes, force, safe, who, ver):
    from . import pending
    ad = adapter(kind)
    st0 = state.load()
    if model.inbound(st0, kind) is None:
        raise KaskadError(tr("gw_ops.err.not_installed", kind=registry.title(kind)))
    if net is not None:
        raise KaskadError(tr("gw_ops.settings.no_net"), code=USAGE)          # F5 — not in 3.3.7.15 (§0а)
    req = _settings_request(runner, st0, kind, ad, port, proto, mtu, force, tls)
    keep = {}
    plan = _settings_plan(st0, kind, req, who, keep, safe)
    _accel_cut(runner, plan)
    rec0 = model.inbound(st0, kind) or {}
    field = req["field"] or (_fields(ad)[0][0] if _fields(ad) else None)
    base = {"ok": True, "kind": kind, "field": field, "old": rec0.get(field) if field else None,
            "port": req["port"] if req["port"] is not None else (rec0.get(field) if field else None),
            "ver": _settings_ver(st0, kind)}
    if dry:
        return dict(base, dry=True, plan=plan, **plan)
    if not plan["changed"]:
        return dict(base, plan=plan, changed={}, reissue=[], text=tr("gw_ops.settings.nothing", title=plan["title"]),
                    warnings=[], apply=None, pending=None, id=None)
    if plan["need_yes"] and not yes:
        raise ConflictError(tr("gw_ops.settings.need_yes", title=plan["title"], what=" ".join(plan_lines(plan))),
                            data={"need": "yes", "plan": plan})

    aid = "a-" + os.urandom(4).hex()
    # the id given to pending.guard is settings-last[kind].id (CONTRACT-pending-3315; REV-3315-a1 З-2). The record is
    # written first thing in ``apply`` — inside the guard, under its lock (F7): a refusal of the guard (409, a live
    # wait) writes nothing, the timer never sees a half state; a failed application puts the record back. No record —
    # no id: VLESS on 443 in front of the site (site.front, no «Вернуть прежние»), a file that can't be written now.
    pid, last, warns = None, None, []
    if req["front"]:
        if safe:
            raise ConflictError(tr("gw_ops.settings.front_no_safe", title=plan["title"]), data={"why": "front"})
    elif _last_can_write():
        pid, last = aid, _last_rec(aid, st0, kind, req, field)
    else:
        warns.append(tr("gw_ops.settings.last_failed", err=settings_last_path()))

    def apply():
        prev = _last_read().get(kind) if pid else None
        if pid:
            try:
                _last_write(kind, last)
            except OSError as e:                        # the wait would have nothing to undo: nothing is applied
                raise KaskadError(tr("gw_ops.settings.last_refused", title=plan["title"], err=util.redact(str(e))),
                                  code=CONFLICT)
        try:
            return _settings_apply(runner, kind, req, plan, rec0, ver, base, aid, (pid, last, prev))
        except BaseException:
            if pid:
                _last_put_back(kind, prev)
            raise

    out = pending.guard(runner, st0, dict(plan, id=pid, st_new=keep.get("st_new")), scope=kind, who=who, safe=safe,
                        apply=apply)
    out.setdefault("pending", None)
    if warns:
        out["warnings"] = list(out.get("warnings") or []) + warns
    if out.get("pending"):                              # ``note`` — the line alone (the SSH menu asks itself, F9)
        out["note"] = tr("gw_ops.settings.pending_note")
        out["text"] = " ".join(t for t in (out.get("text"), out["note"]) if t)
    return out


def _last_rec(aid, st0, kind, req, field):
    """The record of settings-last for the application planned on ``st0`` (written before it, F7 З-2)."""
    old, new = {}, {}
    if req.get("port") is not None:
        old["port"], new["port"] = (model.inbound(st0, kind) or {}).get(req["field"]), req["port"]
    if req.get("mtu") is not None:
        old["mtu"], new["mtu"] = gwmtu.client_mtu(st0, kind), req["mtu"]
    if req.get("tls") is not None:
        old["tls"], new["tls"] = _tls_of(st0, kind), req["tls"]
    marks = _settings_marks(st0, kind, req)
    return {"id": aid, "at": int(time.time()), "old": old, "new": new, "field": field, "what": marks["what"],
            "hard": marks["hard"], "inst": marks["inst"], "marked": marks["ids"]}


def _last_can_write():
    """settings-last.json and its lock can be written now (else no id for pending.guard — З-2)."""
    try:
        util.ensure_dir(paths.gw_var_dir(), 0o700)
        util.ensure_dir(paths.gw_run_dir(), 0o700)
    except OSError:
        return False
    p = settings_last_path()
    return all(os.access(d, os.W_OK) for d in (paths.gw_var_dir(), paths.gw_run_dir())) and \
        (not os.path.exists(p) or os.access(p, os.W_OK))


def _last_put_back(kind, prev):
    try:
        _last_write(kind, prev)
    except OSError as e:
        util.log_tr("gw_ops.log.last_failed", err=e)


def _settings_apply(runner, kind, req, plan, rec0, ver, base, aid, lastw=(None, None, None)):
    from . import inlog, reissue
    pid, last, prev = lastw
    field = base["field"]
    if req["front"]:                                    # VLESS on 443 in front of the site: the old path of site.front
        from ..site import front
        got = front.sync(runner)
        if got.get("error"):
            raise ConflictError(tr("gw_ops.err.vless_port_after", err=got["error"], port=got.get("port")),
                                data={"kind": "site"})
        rows = got.get("reissue") or []
        ids = [r.get("id") or r.get("device") for r in rows if isinstance(r, dict)]
        try:
            reissue.mark(kind, [i for i in ids if i], ["port"], True)
            inlog.mark_issued(kind)
        except (OSError, ValueError) as e:
            util.log_tr("gw_ops.log.reissue_mark", err=e)
        return dict(base, field="port", old=rec0.get("port"), port=got.get("port"), security=got.get("security"),
                    reissue=rows, text=got.get("text") or "", warnings=[got["error"]] if got.get("error") else [],
                    apply=None, changed=plan["changed"], plan=plan, id=None)

    def mutate(new, sec):
        if ver is not None:
            cur = _settings_ver(new, kind)
            if cur != ver:                              # З-3: another tab changed it first — nothing is written
                from . import policy_ops as pops
                raise pops.object_changed(cur)
        return _settings_mutate(new, kind, req)

    with gsys.hold():                   # REV-3315-g3 Г3-2: Ctrl+C after the write never cuts the steps after it
        old, res = _change(runner, mutate)
        new_vals = dict((f, req[f]) for f in ("port", "mtu", "tls") if f in old)
        changed = dict((f, {"old": old[f], "new": new_vals[f]}) for f in old)
        util.log_action("GW inbound settings: %s %s (%s)" % (kind, ", ".join("%s %s -> %s" % (f, old[f], new_vals[f])
                                                                              for f in sorted(old)), aid))
        try:
            if pid and (old != last["old"] or new_vals != last["new"]):
                # another write came between the plan and the lock (CLI has no ``ver``): the record says what was done
                if old:
                    try:
                        _last_write(kind, dict(last, old=old, new=new_vals))
                    except OSError as e:
                        util.log_tr("gw_ops.log.last_failed", err=e)
                else:
                    _last_put_back(kind, prev)
            st = state.load()
            marks = _settings_marks(st, kind, req)
            given = _preview_given(kind, st)            # П5 З-2: «Скачать мои новые настройки» of this change
            try:
                reissue.mark(kind, marks["ids"], marks["what"], marks["hard"], by=aid, inst=marks["inst"], st=st)
                if given:
                    reissue.issued(kind, given, st=st)      # given the settings of now: as «Выдать»
                if "port" in old:
                    inlog.mark_issued(kind)                 # Е4: «порт закрыт?» 10 min later without a session
            except (OSError, ValueError) as e:
                util.log_tr("gw_ops.log.reissue_mark", err=e)
            rows = [r for r in marks["rows"] if r["id"] != given] if old else []
            out = dict(base, port=(model.inbound(st, kind) or {}).get(field) if field else None, changed=changed,
                       plan=plan, reissue=rows, warnings=(res or {}).get("warnings") or [],
                       apply=res, id=aid, restart=plan["restart"])
            if kind == HY2 and "port" in old:
                _hy2_reissue(out)                             # the port is in the links given out (the old words)
            elif rows:
                key = "gw_ops.settings.reissue_hard" if marks["hard"] else "gw_ops.settings.reissue_soft"
                out["text"] = tr(key, title=plan["title"], who=_who(_reissue_names(rows)))
            else:
                out["text"] = tr("gw_ops.settings.applied", title=plan["title"], what=settings_changed_text(changed))
            return out
        except Exception as e:                          # noqa: BLE001 — Н-2 (REV-3315-g2) × F3: the write is made
            # as at F6: an error out of here would make ``apply`` put the record back and ``pending.guard`` drop the
            # wait — the new port without its timer and without «Вернуть прежние». Said; the wait and the record stay.
            # ``after_failed`` — the flag of the panel (CONTRACT-pending-3315 добавка 6); no record — no button named
            _after_failed(e)                            # the text says it (once: not in ``warnings`` again)
            key = "gw_ops.settings.after_failed" if pid else "gw_ops.settings.after_failed_no_undo"
            return dict(base, port=req["port"] if "port" in old else base["port"], changed=changed, plan=plan,
                        reissue=[], warnings=list((res or {}).get("warnings") or []), apply=res, id=aid,
                        restart=plan["restart"], after_failed=True,
                        text=tr(key, title=plan["title"], err=type(e).__name__))


def inbound_settings_undo(runner, kind, pending=None, yes=False, dry=False, who=None):
    """«Вернуть прежние настройки» (F3, 8.4.1 №10): the last application of ``kind`` undone in one ``_change`` (port, MTU),
    its marks taken off; the devices given the new settings meanwhile get a hard mark again. The old port taken by
    somebody since — refused (it is never taken back without ``force``). The record goes after (no «forward» again).
    A call by hand (panel, CLI, menu) takes ``pending.lock`` around everything and drops a live wait of this protocol
    first (``pending.disarm``, F7 С-3); the timer of F7 calls with ``pending=<its record>`` holding the lock itself and
    undoes only its own application (``id``).

    3.3.7.15 П6 (T-3315 п. 2.2): a restart of a service other protocols share asks as «Применить» asks (``_undo_plan``:
    accel-ppp with live sessions, the xray of the gateway) — without ``yes`` ConflictError ``need: "yes"`` with the plan;
    a live wait of this protocol and its timer never ask (the revert comes anyway, «Применить» was confirmed). ``dry`` —
    the plan only (the checks of the old port too), nothing written, no lock. ``who`` — the administrator (panel:
    its client, CLI and SSH menu: ``pending.who_ssh``): the undo that cuts his own link asks too (П7, REV-3315-p256)."""
    from . import pending as gpending
    if dry:
        return _settings_undo(runner, kind, None, dry=True, who=who)
    if kind == VLESS:
        from ..site import front
        with front.vless_guard(runner):
            return _undo_locked(runner, kind, pending, gpending, yes, who)
    return _undo_locked(runner, kind, pending, gpending, yes, who)


def _undo_locked(runner, kind, pending, gpending, yes=False, who=None):
    if pending is not None:                             # the timer of F7: it holds pending.lock itself
        return _settings_undo(runner, kind, pending, yes=True)
    with gpending.lock():                               # the order: pending.lock, then the state lock (F7 С-2)
        live = gpending.view()
        if isinstance(live, dict) and live.get("scope") == kind:
            gpending.disarm(runner)                     # С-3: after «Вернуть прежние» by hand no wait is left
            yes = True                                  # П6: the timer would revert it without asking
        return _settings_undo(runner, kind, None, yes=yes, who=who)


def _undo_plan(runner, st, kind, title, last, who=None):
    """«Что изменится» of «Вернуть прежние» (П6): the fields it changes back, what restarts (``_restart_rows``) and
    ``need_yes`` by the rule of «Применить» for a service other protocols share — accel-ppp with live sessions
    (``_accel_cut``: SSTP restarts it, L2TP and PPTP are cut too), the xray of the gateway (VLESS, SOCKS5, Hysteria2:
    ``pending.via_xray``) — and, as «Применить» (П7, REV-3315-p256 Б-1), the link of the administrator ``who`` it cuts
    (``pending.admin_hit``: his address in a pool of the protocol, a port or TLS back) with its own words — an undo has
    no timer to bring the link back. Whom to give the settings again is said by the answer, as before."""
    from . import pending
    old, new = last.get("old") or {}, last.get("new") or {}
    changed = dict((f, {"old": new.get(f), "new": old[f]}) for f in ("port", "mtu", "tls")
                   if f in old and old[f] != new.get(f))
    via = bool(pending.via_xray(kind, changed))
    hit = pending.admin_hit(st, st, kind, who, changed)     # a port, MTU or TLS back moves no pool: one state for both
    hit_text = tr("gw_ops.settings.undo_hit", what=title) if hit else ""
    plan = {"kind": kind, "title": title, "changed": changed, "restart": _restart_rows(st, kind, list(changed)),
            "reissue": {"hard": [], "soft": []},
            "warnings": ([tr("gw_ops.settings.via_xray")] if via else []) + ([hit_text] if hit_text else []),
            "via_xray": via, "admin_hit": hit or None, "hit_text": hit_text, "need_yes": via or bool(hit)}
    _accel_cut(runner, plan)
    plan["lines"] = plan_lines(plan)
    return plan


def _last_now(st, kind, last):
    """{field: value now} of the fields an application changed (port, MTU, TLS) — what ``last.new`` is compared with."""
    rec = model.inbound(st, kind) or {}
    out = {}
    if "port" in last["new"]:
        out["port"] = rec.get(last.get("field"))
    if "mtu" in last["new"]:
        out["mtu"] = gwmtu.client_mtu(st, kind) if kind in gwmtu.KINDS else None
    if "tls" in last["new"]:
        out["tls"] = _tls_of(st, kind)                  # F4: read softly, as the adapter reads it
    return out


def _last_in_force(st, kind, last):
    """The application of ``last`` is what the protocol has now (REV-3315-a1 С-2): nobody changed these fields since —
    ``front.sync``, «Выдать с меньшим MTU», a new S4, an older version after a rollback."""
    return bool(last) and _last_now(st, kind, last) == last["new"]


def _moved(kind, title, st, last):
    """Refusal «changed since»: the record goes (nothing of it is in force), nothing is written."""
    now = _last_now(st, kind, last)
    words = ", ".join(tr("gw_ops.settings.now." + f, v=(tls_text(now[f]) if f == "tls" else now[f])
                                                      if now[f] is not None else "—")
                      for f in ("port", "mtu", "tls") if f in now)
    return ConflictError(tr("gw_ops.settings.undo_moved", title=title, now=words), data={"why": "moved", "now": now})


def _settings_undo(runner, kind, pending, yes=False, dry=False, who=None):
    from . import inlog, reissue
    ad = adapter(kind)
    title = registry.title(kind)
    last = _last_read().get(kind)
    if not last:
        raise KaskadError(tr("gw_ops.settings.undo_nothing", title=title), code=USAGE)
    if pending is not None and pending.get("id") != last.get("id"):
        raise ConflictError(tr("gw_ops.settings.undo_changed", title=title), data={"why": "changed"})
    st0 = state.load()
    rec0 = model.inbound(st0, kind)
    if rec0 is None:
        raise KaskadError(tr("gw_ops.err.not_installed", kind=registry.title(kind)))
    if not _last_in_force(st0, kind, last):
        err = _moved(kind, title, st0, last)
        if not dry:                                     # П6: the plan writes nothing
            _last_put_back(kind, None)
        raise err
    old, field = last["old"], last.get("field")
    if "port" in old:
        protos = dict(_fields(ad)).get(field) or []
        p = old["port"]
        if not isinstance(p, int) or isinstance(p, bool) or not field:
            raise KaskadError(tr("gw_ops.settings.undo_nothing", title=title), code=USAGE)
        found = [c for pr in protos for c in port_conflicts(st0, runner, kind, pr, p)]
        if found:
            raise ConflictError(tr("gw_ops.settings.undo_port_busy", title=title, port=p, why=_busy_text(found)),
                                data={"why": "port_busy", "port": p, "conflicts": found})
        chk = {"front": False}
        _port_checks(runner, st0, kind, rec0, field, protos, p, True, chk)   # VLESS TLS — 443, Hysteria2 hops
        if chk["front"]:                                # 443 in front of the site: the switch of site.front, not a port
            raise ConflictError(tr("gw_ops.settings.undo_front", title=title), data={"why": "front"})
    m = old.get("mtu")
    if kind in gwmtu.KINDS and isinstance(m, int) and not isinstance(m, bool) and m > gwmtu.manual_limit(st0, kind):
        gwmtu.manual_check(st0, kind, m)                # the card narrowed since: the words of F2 (over / closed)
    if dry or not yes:                                  # П6: a shared service asks as «Применить» does
        plan = _undo_plan(runner, st0, kind, title, last, who)
        if dry:
            return {"ok": True, "kind": kind, "dry": True, "plan": plan, "lines": plan["lines"],
                    "need_yes": plan["need_yes"], "what": settings_changed_text(plan["changed"])}
        if plan["need_yes"]:
            raise ConflictError(tr("gw_ops.settings.undo_need_yes", title=title,
                                   what=settings_changed_text(plan["changed"]), lines=" ".join(plan["lines"])),
                                data={"need": "yes", "plan": plan})

    def mutate(new, sec):
        rec = model.inbound(new, kind)
        if rec is None:
            raise KaskadError(tr("gw_ops.err.not_installed", kind=registry.title(kind)))
        if not _last_in_force(new, kind, last):         # changed between the check and the lock
            raise _moved(kind, title, new, last)
        done = {}
        if "port" in old and rec.get(field) != old["port"]:
            done["port"] = {"old": rec.get(field), "new": old["port"]}
            rec[field] = old["port"]
        m = old.get("mtu")
        if kind in gwmtu.KINDS and isinstance(m, int) and not isinstance(m, bool) and gwmtu.client_mtu(new, kind) != m:
            done["mtu"] = {"old": gwmtu.client_mtu(new, kind), "new": m}
            _set_mtu(new, kind, m)
        t = old.get("tls")
        if t in TLS_WORDS and _tls_of(new, kind) != t:          # F4; a value this version does not know — left
            done["tls"] = {"old": _tls_of(new, kind), "new": t}
            _set_tls(new, kind, t)
        return done

    try:
        done, res = _change(runner, mutate)
    except ConflictError as e:
        if (e.data or {}).get("why") == "moved":
            _last_put_back(kind, None)
        raise
    st = state.load()
    rows = []
    try:
        kept = set(reissue.marked_by(kind, last["id"]))   # never given the new settings: the old ones work again
        reissue.drop_by(kind, last["id"], st=st)
        what = [w for w in last.get("what") or [] if w in ("port", "mtu")]
        # only what the clients carry (port, MTU) asks to give the settings again; TLS alone — files and links the same
        # (SPEC F4, REV-3315-a2 З-1)
        rows = [d for d in _reissue_devices(st, kind) if d["id"] not in kept] if what else []
        if rows and what:
            reissue.mark(kind, [d["id"] for d in rows], what, bool(last.get("hard")), inst=last.get("inst"), st=st)
        if "port" in done:
            inlog.mark_issued(kind)
    except (OSError, ValueError) as e:
        util.log_tr("gw_ops.log.reissue_mark", err=e)
    _last_put_back(kind, None)
    util.log_action("GW inbound settings undo: %s %s (%s)" % (kind, ", ".join(
        "%s %s -> %s" % (f, done[f]["old"], done[f]["new"]) for f in sorted(done)), last["id"]))
    out = {"ok": True, "kind": kind, "undone": done, "id": last["id"], "reissue": rows,
           "warnings": (res or {}).get("warnings") or [], "apply": res}
    if rows:
        key = "gw_ops.settings.reissue_hard" if last.get("hard") else "gw_ops.settings.reissue_soft"
        out["text"] = tr("gw_ops.settings.undone", title=title, what=settings_changed_text(done)) + " " + tr(
            key, title=title, who=_who(_reissue_names(rows)))
    else:
        out["text"] = tr("gw_ops.settings.undone", title=title, what=settings_changed_text(done))
    return out


def _last_view(last):
    """«Вернуть прежние» of the window: {"at", "id", "changed", "text"} — ``text`` in words («порт 51820 → 5000»)."""
    changed = dict((f, {"old": last["old"][f], "new": last["new"].get(f)}) for f in last["old"])
    return {"at": last.get("at"), "id": last["id"], "changed": changed, "text": settings_changed_text(changed)}


def _tls_view(st, kind, choices):
    """F4, the block «TLS» of the window: {"cur", "choices": [{"value", "text", "hint"}], "note"} — the words for the
    panel and ``gw inbound show``; VLESS has no «только 1.2» and the note why «только 1.3» is still worth it."""
    hints = {"1.2": tr("gw_ops.settings.tls_hint.only12")}
    return {"cur": _tls_of(st, kind),
            "choices": [{"value": v, "text": tls_text(v), "hint": hints.get(v, "")} for v in choices],
            "note": tr("gw_ops.settings.tls_note.vless") if kind == VLESS else ""}


def inbound_settings_view(runner, kind):
    """The window «Настройки протокола» (F3), read-only (``port_offer`` runs ss): {"kind", "title", "port", "mtu",
    "tls", "restart", "devices", "reissue", "last", "closed", "pending", "ver"}."""
    from . import inlog, pending, reissue
    ad = adapter(kind)
    st = state.load()
    if model.inbound(st, kind) is None:
        raise KaskadError(tr("gw_ops.err.not_installed", kind=registry.title(kind)))
    choices = tls_choices(st, kind)
    fields = [f for f in ("port", "mtu", "tls") if (f != "port" or _fields(ad)) and (f != "mtu" or kind in gwmtu.KINDS)
              and (f != "tls" or choices)]
    last = _last_read().get(kind)
    if not _last_in_force(st, kind, last):            # changed since by another action: nothing to bring back (С-2)
        last = None
    rv = reissue.view(st).get(kind) or {"title": registry.title(kind), "hard": [], "soft": []}
    return {"ok": True, "kind": kind, "title": registry.title(kind), "port": port_offer(runner, kind, st=st),
            "mtu": gwmtu.manual_view(st, kind) if kind in gwmtu.KINDS else None,
            "tls": _tls_view(st, kind, choices) if choices else None,
            "restart": _restart_rows(st, kind, fields),
            "devices": [{"id": d["id"], "name": n} for d, n in zip(_reissue_devices(st, kind),
                                                                    _reissue_names(_reissue_devices(st, kind)))],
            "reissue": rv, "last": _last_view(last) if last else None,
            "closed": inlog.closed(kind), "pending": pending.view(), "ver": _settings_ver(st, kind)}


# ── users and devices ──────────────────────────────────────────────────────

def user_add(runner, name, via=None, fail=None):
    def mutate(new, sec):
        return model.add_user(new, name, model.parse_route(via, fail or model.kept_fail(new, "default")) if via else None)
    u, _ = _change(runner, mutate)
    util.log_action("GW user add: %s (%s)" % (u["name"], u["id"]))
    return u


class _Gone(Exception):
    """The object a deletion names by its id is not there (any more): nothing to change, nothing saved."""


_ID_REF = re.compile(r"^[ud]-[0-9a-f]{8}$")
# texts other modules (and tests) read as ops.GONE_DEVICE_TEXT…: tr() at the moment of the read (module __getattr__)
_TEXTS = {"GONE_DEVICE_TEXT": "gw_ops.gone.device", "GONE_USER_TEXT": "gw_ops.gone.user",
          "DUP_KEY_TEXT": "gw_ops.exit.dup_key",
          "POOL_CHANGED_TEXT": "gw_ops.exit.pool_changed", "POOL_BUSY_TEXT": "gw_ops.exit.pool_busy",
          "EXIT_GONE_TEXT": "gw_ops.exit.gone", "NOT_TEXT": "gw_ops.key.not_text", "PATH_TEXT": "gw_ops.key.path",
          "NO_EXITS_TEXT": "gw_ops.no_exits"}


def __getattr__(name):
    key = _TEXTS.get(name)
    if key is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    return tr(key)


def _gone(ref, missing_ok, find, new):
    """``missing_ok`` and an id (never a name: «нет» of a typo stays an error) that ``find`` does not find -> _Gone.
    The panel deletes by id: its DELETE repeated (the browser re-sends an idempotent request when the connection broke
    before the answer — xray restarted by that very change; T-M6-qs330b №6) is «уже удалено», not «Устройства d-… нет»."""
    if missing_ok and _ID_REF.match(str(ref or "")) and find(new, ref) is None:
        raise _Gone()


def user_del(runner, ref, force=False, missing_ok=False):
    def mutate(new, sec):
        _gone(ref, missing_ok, model.find_user, new)
        u, devs = model.del_user(new, ref, cascade=force)
        for d in devs:
            sec.delete_prefix("dev.%s." % d["id"])
        sec.delete_prefix("user.%s." % u["id"])          # the links of his page and subscription die with him
        return u, devs
    try:
        (u, devs), res = _change(runner, mutate)
    except _Gone:
        return {"ok": True, "gone": True, "user": {"id": ref}, "devices": [], "apply": None, "text": tr("gw_ops.gone.user")}
    util.log_tr("gw_ops.log.user_del", name=u["name"], id=u["id"], n=len(devs))
    return {"ok": True, "user": u, "devices": [d["id"] for d in devs], "apply": res}


def user_route(runner, ref, via, fail=None):
    def mutate(new, sec):
        u = model.get_user(new, ref)
        model.set_route(u, via, fail or model.kept_fail(new, "user", u), st=new)
        return u
    u, res = _change(runner, mutate)
    util.log_action("GW user route: %s -> %s" % (u["name"], (u.get("route") or {}).get("via", "inherit")))
    return {"ok": True, "user": u, "apply": res}


def _pause_word(on, was):
    """«доступ возобновлён» / «доступ приостановлен» for the log line of a pause; «(и так …)» when nothing changed."""
    word = tr("gw_ops.pause.resumed" if on else "gw_ops.pause.paused")
    return word if was != bool(on) else tr("gw_ops.pause.already", word=word,
                                           state=tr("gw_ops.pause.on" if on else "gw_ops.pause.off"))


def user_enable(runner, ref, on=True):
    """Pause (``on`` false) or resume a person: every device of his goes off or on. Logged here, in the core — the
    menu, the panel and the bot all come through (stand T-G1b, N6)."""
    def mutate(new, sec):
        u = model.get_user(new, ref)
        was = u.get("enabled", True) is not False
        u["enabled"] = bool(on)
        return u, was, len(model.devices(new, user=u["id"]))
    (u, was, devs), res = _change(runner, mutate)
    with util.journal_lang():
        util.log_action(tr("gw_ops.log.user_pause", what="resume" if on else "pause", name=u["name"], id=u["id"],
                           n=devs, word=_pause_word(on, was)))
    return {"ok": True, "user": u, "apply": res}


def default_route(runner, via, fail=None):
    def mutate(new, sec):
        return model.set_default_route(new, via, fail or model.kept_fail(new, "default"))
    r, res = _change(runner, mutate)
    util.log_action("GW route default -> %s%s" % (r["via"], (" (%s)" % r["fail"]) if r["via"] not in ("block", "direct") else ""))
    return {"ok": True, "route": r, "apply": res}


def device_add(runner, user_ref, name, kind="other", platform="other", access=(), via=None, fail=None):
    def mutate(new, sec):
        owner = model.get_user(new, user_ref)
        d = model.add_device(new, user_ref, name, kind=kind, platform=platform, access=access,
                             route=model.parse_route(via, fail or model.kept_fail(new, "user", owner)) if via else None)
        sec.set("dev.%s.password" % d["id"], secrets.new_password(14))
        return d
    d, res = _change(runner, mutate)
    util.log_tr("gw_ops.log.device_add", name=d["name"], id=d["id"], login=d["login"],
                kinds=", ".join(d["access"]) or "—")
    return {"ok": True, "device": model.find_device(state.load(), d["id"]) or d, "apply": res}


def device_del(runner, ref, missing_ok=False):
    """The device away. ``missing_ok`` (the panel, by id): a device already deleted -> {"gone": True}, not an error."""
    def mutate(new, sec):
        _gone(ref, missing_ok, model.find_device, new)
        d = model.del_device(new, ref)
        sec.delete_prefix("dev.%s." % d["id"])
        return d
    try:
        d, res = _change(runner, mutate)
    except _Gone:
        return {"ok": True, "gone": True, "device": {"id": ref}, "apply": None, "text": tr("gw_ops.gone.device")}
    util.log_action("GW device del: %s (%s)" % (d["name"], d["id"]))
    return {"ok": True, "device": d, "apply": res}


def _secret_prefixes(kind, device_id):
    """What of the device's secrets is this inbound's alone (the adapter says; OpenVPN: ``dev.<id>.ovpn.``)."""
    ad = registry.get(kind)
    fn = getattr(ad, "secret_prefixes", None) if ad is not None else None
    got = fn(device_id) if callable(fn) else None
    return [p for p in (got or ["dev.%s.%s." % (device_id, kind)]) if str(p).startswith("dev.%s." % device_id)]


def device_grant(runner, ref, kind, on=True):
    if kind not in model.INBOUND_KINDS:
        raise KaskadError(tr("gw_ops.err.unknown_kind", kind=repr(kind)), code=USAGE)

    def mutate(new, sec):
        d = model.get_device(new, ref)
        if on:
            ips = model.grant(new, d, kind)
        else:
            ips = model.revoke(new, d, kind)
            for p in _secret_prefixes(kind, d["id"]):
                sec.delete_prefix(p)
        return d, ips
    (d, ips), res = _change(runner, mutate)
    util.log_action("GW device %s: %s (%s) %s" % ("grant" if on else "revoke", d["name"], d["id"], kind))
    return {"ok": True, "device": d, "kind": kind, "ips": ips, "apply": res}


def device_route(runner, ref, via, fail=None):
    def mutate(new, sec):
        d = model.get_device(new, ref)
        model.set_route(d, via, fail or model.kept_fail(new, "device", d), st=new)
        return d
    d, res = _change(runner, mutate)
    util.log_action("GW device route: %s (%s) -> %s" % (d["name"], d["id"], (d.get("route") or {}).get("via", "inherit")))
    return {"ok": True, "device": d, "apply": res}


def device_enable(runner, ref, on=True):
    """Pause or resume one device (its keys stay). Logged in the core, whoever asked (stand T-G1b, N6)."""
    def mutate(new, sec):
        d = model.get_device(new, ref)
        was = d.get("enabled", True) is not False
        d["enabled"] = bool(on)
        u = model.find_user(new, d.get("user")) or {}
        return d, was, u.get("name") or ""
    (d, was, owner), res = _change(runner, mutate)
    with util.journal_lang():
        util.log_action("GW device %s: %s (%s)%s — %s" % ("resume" if on else "pause", d["name"], d["id"],
                                                          (", %s" % owner) if owner else "", _pause_word(on, was)))
    return {"ok": True, "device": d, "apply": res}


SHARED_PASSWORD_KINDS = ("l2tp", "sstp", "ikev2", "ocserv")     # inbounds/tunnel.py: dev.<id>.password
SHARED_KEY = "dev.%s.password"


def _reset_secrets(kind, device_id):
    ad = registry.get(kind)
    fn = getattr(ad, "reset_secrets", None) if ad is not None else None
    got = fn(device_id) if callable(fn) else None
    return [p for p in (got or _secret_prefixes(kind, device_id)) if str(p).startswith("dev.%s." % device_id)]


def device_reset(runner, ref, inbound=None):
    """``gw device reset <dev> [--inbound K]``: new keys, passwords and certificates of the device — on every
    inbound it has or on one. Its secrets are deleted and the adapters make new ones in the same apply; the
    adapters cut the device's sessions whose credentials changed (accel-ppp, strongSwan, ocserv; OpenVPN: the
    old certificate goes into the CRL and the client is killed; WireGuard/AmneziaWG: the old peer is gone;
    SOCKS5: the account changes, xray restarts). Sessions of other devices stay. The shared password of
    L2TP, SSTP, IKEv2 and OpenConnect changes for all of them at once (``also``)."""
    if inbound is not None and inbound not in model.INBOUND_KINDS:
        raise _unknown_kind(inbound)

    def mutate(new, sec):
        d = model.get_device(new, ref)
        access = [k for k in d.get("access") or [] if k in model.INBOUND_KINDS]
        if inbound is not None and inbound not in access:
            raise KaskadError(tr("gw_ops.err.not_granted", kind=registry.title(inbound)))
        kinds = [inbound] if inbound is not None else access
        if not kinds:
            raise KaskadError(tr("gw_ops.err.no_access"))
        drop = set() if inbound is not None else {"dev.%s." % d["id"]}      # all inbounds: every secret of it
        for k in kinds:
            drop.update(_reset_secrets(k, d["id"]))
        for p in sorted(drop):
            if p.endswith("."):
                sec.delete_prefix(p)
            else:
                sec.delete(p)
        shared = SHARED_KEY % d["id"]
        also = []
        if shared in drop or "dev.%s." % d["id"] in drop:
            sec.set(shared, secrets.new_password(14))                    # every device has one (device_add)
            also = [k for k in access if k in SHARED_PASSWORD_KINDS and k not in kinds]
        if not model.enabled(new):                                       # no apply: make them now
            for k in kinds + also:
                ad = registry.get(k)
                if ad is not None and model.inbound(new, k) is not None:
                    ad.prepare(new, sec, runner)
        return d, kinds, also

    (d, kinds, also), res = _change(runner, mutate)
    if also:
        util.log_tr("gw_ops.log.device_reset_also", name=d["name"], id=d["id"], kinds=", ".join(kinds),
                    also=", ".join(also))
    else:
        util.log_action("GW device reset: %s (%s) — %s" % (d["name"], d["id"], ", ".join(kinds)))
    return {"ok": True, "device": model.find_device(state.load(), d["id"]) or d, "kinds": kinds, "also": also,
            "apply": res}


PKI_KINDS = ("ikev2",)              # inbounds whose CA the devices put into their trusted roots


def pki_rotate(runner, kind):
    """``gw pki rotate ikev2``: a new CA of the inbound (constrained to gateway.host) and a new server
    certificate. Every device of the inbound needs new settings; established sessions stay until they
    reconnect. Rotating a CA is never done behind the owner's back (an earlier build's CA stays until this)."""
    if kind not in PKI_KINDS:
        raise KaskadError(tr("gw_ops.err.pki_kinds", kinds=", ".join(PKI_KINDS)), code=USAGE)
    ad = adapter(kind)
    if not callable(getattr(ad, "rotate_pki", None)):
        raise UnsupportedError(tr("gw_ops.err.pki_unsupported", kind=registry.title(kind)))

    def mutate(new, sec):
        if model.inbound(new, kind) is None:
            raise KaskadError(tr("gw_ops.err.not_installed", kind=registry.title(kind)))
        return ad.rotate_pki(new, sec, runner)

    done, res = _change(runner, mutate)
    st = state.load()
    devs = [d["id"] for d in model.devices_for(st, kind)]
    util.log_tr("gw_ops.log.pki_rotate", kind=kind, done=", ".join(retr(done, saved_lang())) or "—", n=len(devs))
    return {"ok": True, "kind": kind, "created": done, "devices": devs, "host": model.public_host(st), "apply": res}


# ── VLESS Reality (docs/k3/SPEC-vless-inbound.md §9, §10) ─────────────────

VLESS = "vless"


def _vless_rec(st):
    rec = model.inbound(st, VLESS)
    if rec is None:
        raise KaskadError(tr("gw_ops.err.vless_not_installed"))
    return rec


def _reissue(st, kind, what):
    """Whom to give the settings again after a change of the node (the cover site, the keys) and the text of it."""
    rows = _reissue_devices(st, kind)
    if not rows:
        return rows, ""
    return rows, tr("gw_ops.reissue.vless", what=what, who=_who(_reissue_names(rows)))


def _site_on(st):
    from ..site import model as site_model
    s = site_model.peek(st)
    return s["mode"] == "site" and bool(s["domain"])


VLESS_INSTALL_WAIT = 30         # s: the install step of VLESS waits for a transition of the mode (решения координатора)


def _vless_follow(runner, new=False):
    """After an install of VLESS: its mode by this node's site (site.front.sync) — a record of another node (a copy,
    ``gw inbound install`` of a kept one) may name TLS on a domain this node has not. ``new`` — a record made by this
    install (``want`` "tls", SPEC-vless-tls-default §3.2): a domain with its certificate and the site off — the site
    goes on in the same install (``_vless_site_on``). -> notes for people."""
    from ..site import front
    try:
        with front.vless_guard(runner, wait=VLESS_INSTALL_WAIT):    # a transition running: wait, then a clear refusal
            if new:
                said = _vless_site_on(runner)
                if said is not None:
                    return said
            got = front.sync(runner)
    except KaskadError as e:
        return [tr("gw_ops.vless.mode_unset", err=e)]
    out = []
    if got.get("changed"):
        out.append("VLESS — %s" % (tr("gw_ops.vless.mode_tls") if got["security"] == "tls" else
                                   tr("gw_ops.vless.mode_reality", why="; " + got["why"] if got.get("why") else "")))
    if got.get("error"):
        out.append(got["error"])
    return out


def _vless_site_on(runner):
    """§3.2: a new VLESS (``want`` "tls", Reality now) on a node with a domain and its certificate of Let's Encrypt and
    the site off — the site goes on with VLESS on TLS in front of it, by ``vless_to_tls`` (the checks В2 first: the
    neighbours and the panel on 443, port 80; the snapshot; a refusal undone). Nobody has links of a new record — its
    keys are new, the price is 0 (§3.2) even when devices were given the access to VLESS before the install: ``yes``
    (REV-vless-sh3 заметно 2). A refusal never fails the install: VLESS stays on Reality, the reason in words and
    «Повторить TLS». -> notes, or None (nothing to do here)."""
    from ..site import front
    st = state.load()
    rec = _vless_rec(st)
    if front.want_of(rec) != front.WANT_TLS or (rec.get("security") or "reality") == "tls" \
            or front.model.peek(st)["mode"] == "site" or not front.site_ready(st, ignore_mode=True)[0]:
        return None
    dom = front.model.unicode(front.model.domain_of(st))
    try:
        got = vless_to_tls(runner, via="install", who=None, yes=True)
    except KaskadError as e:                            # the checks: 443 not free, the panel on 443, port 80, devices
        why = front.clean_why(str(e)) or e.__class__.__name__
        util.log_tr("gw_ops.log.vless_site_not_on", why=why)
        return [tr("gw_ops.vless.site_not_on", domain=dom, why=why.rstrip(" ."))]
    if got.get("ok"):
        return ["VLESS — " + tr("gw_ops.vless.mode_tls"), tr("gw_ops.vless.site_on", domain=dom)]
    return [tr("gw_ops.vless.site_not_on", domain=dom, why=(got.get("why") or got.get("error") or "").rstrip(" ."))]


def _after_cert_due(runner):
    """The conditions of §3.3 on the state as it is now: (the state, the devices with links of VLESS), or None — nothing
    for VLESS here (no record wanting TLS on Reality, the site on, a refusal of TLS before, TLS not possible)."""
    from ..site import front
    st = state.load()
    rec = model.inbound(st, VLESS)
    if rec is None or front.want_of(rec) not in (front.WANT_TLS, front.WANT_NODOMAIN) \
            or (rec.get("security") or "reality") == "tls" or isinstance(rec.get("tls_error"), dict) \
            or front.model.peek(st)["mode"] == "site" or not front.tls_possible(st, runner, ignore_mode=True)[0]:
        return None
    return st, _reissue_devices(st, VLESS)


def _after_cert_recommend(st, devs):
    """§3.3 with devices: the recommendation with the price — who and how to switch (REV-vless-sh3 придирка 3)."""
    from ..site import front
    return {"changed": False, "rows": [], "security": "reality",
            "line": trn("gw_ops.vless.cert_recommend", len(devs),
                        domain=front.model.unicode(front.model.domain_of(st)), who=_who(_reissue_names(devs)))}


def vless_after_cert(runner):
    """§3.3: the first certificate of the node's domain came (``site.ops.issue`` → ``_finish``). A record that wants TLS
    (``want`` "tls" | "reality_nodomain") on Reality with the site off, no refusal of TLS before: nobody has links of
    VLESS — the site on and TLS by ``vless_to_tls`` (cost 0, a refusal undone by the snapshot); devices have them — no
    switch, the recommendation with the price, the names and how to switch. The conditions are read again under the
    lock of a transition (a choice made in between wins, REV-vless-sh3 придирка 1). The lock busy — not switched: a line
    «отложен — повторите «Включить TLS» позже» and a journal line (решение координатора 20.10: no later ``sync`` does it
    by itself). Never raises: the certificate stays issued. -> {"changed", "line", "rows", "security"} | None (nothing
    for VLESS here)."""
    from ..site import front
    due = _after_cert_due(runner)
    if due is None:
        return None
    st, devs = due
    dom = front.model.unicode(front.model.domain_of(st))
    if devs:
        return _after_cert_recommend(st, devs)
    try:
        with front.vless_guard(runner, recover=False):
            due = _after_cert_due(runner)                # again under the lock: what changed in between wins
            if due is None:
                return None
            if due[1]:
                return _after_cert_recommend(*due)
            got = vless_to_tls(runner, via="cert", who=None, yes=False)
    except KaskadError as e:
        if (getattr(e, "data", None) or {}).get("need") == "busy":
            util.log_tr("gw_ops.log.vless_cert_busy")
            return {"changed": False, "rows": [], "security": "reality", "line": tr("gw_ops.vless.cert_busy")}
        why = front.clean_why(str(e)) or e.__class__.__name__
        util.log_tr("gw_ops.log.vless_site_not_on", why=why)
        return {"changed": False, "rows": [], "security": "reality",
                "line": tr("gw_ops.vless.site_not_on", domain=dom, why=why.rstrip(" ."))}
    except Exception as e:                              # noqa: BLE001 — the certificate stays issued whatever broke
        why = front.clean_why(e) or e.__class__.__name__
        util.log_tr("gw_ops.log.vless_site_not_on", why=why)
        return {"changed": False, "rows": [], "security": "reality",
                "line": tr("gw_ops.vless.site_not_on", domain=dom, why=why.rstrip(" ."))}
    if not got.get("ok"):
        return {"changed": bool(got.get("changed")), "rows": got.get("reissue") or [], "security": "reality",
                "line": tr("gw_ops.vless.site_not_on", domain=dom,
                           why=(got.get("why") or got.get("error") or "").rstrip(" ."))}
    return {"changed": bool(got.get("changed")), "rows": got.get("reissue") or [], "security": "tls",
            "line": tr("gw_ops.vless.cert_auto_tls", domain=dom)}


def vless_show(runner, st=None):
    """``gw vless show``: what the clients of VLESS are given (no secrets): the mode (Reality / TLS), port, the cover site
    and its last check, the public key, shortId, fingerprint; the domain in TLS mode; why not TLS. Read-only."""
    from .inbounds import vless as vin
    st = state.load() if st is None else st
    rec = _vless_rec(st)
    out = {"ok": True, "kind": VLESS, "enabled": bool(rec.get("enabled", True)), "port": rec.get("port"),
           "sni": rec.get("sni") or "", "dest": rec.get("dest") or "", "mask": rec.get("mask") or "",
           "mask_check": rec.get("mask_check"), "mask_list": rec.get("mask_list") or "",
           "country": rec.get("country") if isinstance(rec.get("country"), dict) else None,
           "pub": rec.get("pub") or "", "short_id": rec.get("short_id") or "",
           "fp": rec.get("fp") or vin.FINGERPRINT, "flow": vin.FLOW, "own": vin.own_domain(st),
           "devices": len(model.devices_for(st, VLESS)), "host": model.public_host(st)}
    out.update(vless_view(st, runner))
    return out


# reason of Reality -> what fixes it (SPEC-vless-tls-default §3.1): an action for the interfaces and its words
_VLESS_FIX = {"no_domain": "domain", "no_cert": "cert", "busy_443": "free_443", "panel_443": "panel_port",
              "no_site": "site_on", "tls_failed": "mode_tls", "ready": "mode_tls", "chosen": "mode_tls",
              "consent_bad": "consent", "legacy": "mode_tls"}


def vless_view(st, runner=None):
    """The mode of VLESS for people (``gw vless show``, the card of the panel): {"security": "tls" | "reality",
    "domain" (TLS), "tls_block" (the node has a domain but TLS is off now — why), "want" (None — a record of 3.3.7),
    "legacy", "reason", "forced", "fix" {do, text} | None, "recommend" {mode, devices, text} | None, "consent"
    {…, "ok"} | None, "reality_risks" {ver, site, need_pick, text, short, …} | None, "pending" {at, busy} | None}. No
    network; ``runner`` None — 443 only by the files (``front.reason``). Read-only."""
    from ..site import front
    rec = model.inbound(st, VLESS) or {}
    sec = "tls" if front.tls_wanted(st) else "reality"
    out = {"security": sec, "domain": front.model.domain_of(st) if sec == "tls" else ""}
    # the node's domain whatever the mode (the SSH menu «Маскировка VLESS»: «Свой сертификат (TLS) [домен …]», §4.2)
    out["node_domain"] = front.model.unicode(front.model.domain_of(st)) if front.model.domain_of(st) else ""
    if sec != "tls" and front.model.domain_of(st):
        ok, why = front.site_ready(st)
        if not ok:
            out["tls_block"] = why
        elif not rec.get("enabled", True) or not model.enabled(st):
            out["tls_block"] = tr("gw_ops.vless.block_off")
        elif rec.get("security") != "tls":
            out["tls_block"] = tr("gw_ops.vless.block_reality", port=rec.get("port"))
    if not rec:
        return out
    want = front.want_of(rec)
    r = front.reason(st, runner)
    if r["reason"] == "ready" and front.model.peek(st)["mode"] != "site":
        out.pop("tls_block", None)                      # Н-17: the site goes on with the transition — not «нельзя»
    rows = _reissue_devices(st, VLESS)
    devs = len(rows)
    fix = None
    do = _VLESS_FIX.get(r["sub"] or r["reason"])
    if do:
        fix = {"do": do, "text": tr("gw_in_vless.fix." + do)}
    recommend = None
    if r["reason"] in ("ready", "legacy", "consent_bad"):
        # the site off: ``gw vless mode tls`` switches it on — the price says so, as the transition asks it (С1)
        if not devs:
            text = tr("gw_in_vless.recommend_free")
        elif _site_on(st):
            text = trn("gw_in_vless.recommend", devs)
        else:
            text = tr("gw_in_vless.recommend_site", price=_price_tls(rows, False))
        # ``reissue`` — whom (the rows of the price, like ``devices`` of «vless_change»): the plate of the panel names
        # them at once, without the people loaded (DQA-3378 М-1)
        recommend = {"mode": "tls", "devices": devs, "text": text, "reissue": rows}
    c = rec.get("reality_consent")
    consent = None
    if isinstance(c, dict):
        consent = dict((k, c.get(k)) for k in ("at", "via", "who", "risks", "site", "domain", "lang"))
        consent["ok"] = front.consent_ok(rec)
    pend = rec.get("mode_pending")
    out.update(want=want, legacy=want is None, reason=r["reason"], forced=r["forced"], reason_text=r["why"],
               fix=fix, recommend=recommend, consent=consent,
               reality_risks=front.risks_data(st) if front.model.domain_of(st) else None,
               pending={"at": pend.get("at"), "busy": front.lock_busy()} if isinstance(pend, dict) else None)
    if isinstance(rec.get("tls_error"), dict):
        out["tls_error"] = {"at": rec["tls_error"].get("at"), "why": rec["tls_error"].get("why")}
    return out


def vless_masks(runner, check=False, st=None):
    """``gw vless masks [--check]``: the cover sites to choose from — the list by the country of the server (the node's
    own site first, when it is on 443 and VLESS is not); ``check`` — each one checked from the node now (TLS 1.3; a
    few seconds per site). Read-only (the country may be asked from the network)."""
    from .inbounds import vless as vin
    from .inbounds import vless_lists
    st = state.load() if st is None else st
    cur = (model.inbound(st, VLESS) or {}).get("sni") or ""
    country = vin.country_of(st)
    rows = vin.masks(st, check=check, country=country)
    for r in rows:
        r["current"] = r["sni"] == cur
    lst = vless_lists.list_of(country.get("code"))[0]
    return {"ok": True, "current": cur, "masks": rows, "checked": bool(check), "list": lst,
            "list_title": vless_lists.TITLES[lst], "country": country}


def vless_sync(runner):
    """``gw vless sync``: the mode of VLESS by the site of the node now (site.front.sync) — TLS in front of the site, or
    Reality; ``reissue`` — whom to give the links again when it switched. A record of 3.3.7 (no ``want``) that would
    go to TLS and change the links devices have is never switched here (SEC-vless-sh12 №5): a recommendation only
    (``recommend`` "tls", ``why``) — the switch with its price is ``gw vless mode tls``. Going to Reality because TLS
    cannot work (no site) is a repair and still done."""
    from ..site import front
    with front.vless_guard(runner):
        st = state.load()
        rec = _vless_rec(st)
        if front.want_of(rec) is None:
            p = front.predict(st, runner)
            if p["changes"] and p["devices"] and p["security"] == "tls":
                names = _reissue_names(p["devices"])
                why = tr("gw_in_vless.sync_legacy_recommend", n=len(names), who=_who(names))
                return {"ok": True, "kind": VLESS, "changed": False, "security": rec.get("security") or "reality",
                        "port": rec.get("port"), "recommend": "tls", "why": why, "reissue": [], "text": ""}
        got = front.sync(runner)
    got.setdefault("reissue", [])
    got.setdefault("text", "")
    return dict(got, kind=VLESS)


def vless_mask(runner, value, check=True, accept=False, risks=None, via="cli", who=None, lang=None):
    """``gw vless mask <site|own|auto> [--no-check]``: the cover site of VLESS Reality. The site is checked from the
    node first (TLS 1.3 — without it no client connects); the links given before name the old site: ``reissue``.
    Reality chosen with a domain on a consent to the own site (r1b) and a foreign cover now (SEC-vless-sh12 №7): the
    consent to the text of variant A first — ``accept`` with ``risks`` of the text shown (panel/MCP must name it), else
    CONFLICT «reality_consent» with the block; nothing changes."""
    from ..site import front
    with front.vless_guard(runner):
        return _vless_mask(runner, value, check, accept, risks, via, who, lang)


def _vless_mask(runner, value, check, accept=False, risks=None, via="cli", who=None, lang=None):
    from ..site import front
    from .inbounds import vless as vin
    st0 = state.load()
    rec0 = _vless_rec(st0)
    adapter(VLESS)
    try:
        fields = vin.mask_fields(st0, value, check=check)       # network: before the lock
    except KaskadError as e:
        raise KaskadError(str(e), code=USAGE, data=e.data)       # data.kind «mask_check»: the menu offers --no-check
    consent = None
    if front.mask_needs_consent(rec0, fields["mask"]):
        via = via if via in front.VLESS_VIAS else "cli"
        if value == "auto":
            # the site picked again after the consent could differ from the one shown (pick_mask shuffles): with a
            # consent to give, the person names the site first — as ``vless_mode`` (REV-vless-sh2 придирка 1)
            raise ConflictError(tr("gw_in_vless.pick_first"), data={"need": "pick_first", "kind": VLESS})
        rd = front.risks_data(st0, lang, site=fields["sni"])
        if not accept:
            raise _consent_refusal(rd, tr("gw_in_vless.mask_consent_a"))
        if (via in ("mcp", "panel", "setup") and not risks) or (risks and risks != rd["ver"]):
            raise _consent_refusal(rd, tr("gw_in_vless.risks_changed"))
        consent = {"at": util.now_iso(), "via": via, "who": "mcp" if via == "mcp" else (who or via),
                   "risks": rd["ver"], "site": rd["site"], "domain": front.model.domain_of(st0),
                   "lang": lang or saved_lang()}

    def mutate(new, sec):
        rec = _vless_rec(new)
        old = rec.get("sni") or ""
        rec.update(fields)
        if consent is not None:
            rec["reality_consent"] = consent
        return old, front.fit_consent(rec)

    (old, gone), res = _change(runner, mutate)
    front.log_consent_gone(gone)
    if consent is not None:
        util.log_tr("gw_in_vless.log.mask_consent", site=fields["sni"], via=via, who=consent["who"])
    st = state.load()
    changed = old != fields["sni"]
    rows, text = _reissue(st, VLESS, tr("gw_ops.vless.mask_changed", old=old or "—", new=fields["sni"])) \
        if changed else ([], "")
    with util.journal_lang():
        util.log_action(tr("gw_ops.log.vless_mask", old=old or "—", new=fields["sni"], mask=fields["mask"],
                           check=tr("gw_ops.yes" if fields["mask_check"].get("ok") else "gw_ops.no")))
    return {"ok": True, "kind": VLESS, "old": old, "sni": fields["sni"], "dest": fields["dest"], "mask": fields["mask"],
            "mask_check": fields["mask_check"], "changed": changed, "reissue": rows, "text": text, "apply": res}


# ── the mode of VLESS: TLS with the node's own domain or Reality (SPEC-vless-tls-default §3.5) ──────────────────

def _price_tls(devs, site_on=True):
    """The price of the transition to TLS in words. The site off — it goes on with the transition (§3.3, T-3378 Н-17):
    the words of ``site mode site`` say so (REV-3378-fix С1) — the CLI, the SSH menu, MCP and the wizard show this
    text."""
    return tr("gw_in_vless.price_tls" if site_on else "site.ops.vless_price_tls", n=len(devs),
              who=_who(_reissue_names(devs)))


def vless_to_tls(runner, via="cli", who=None, yes=False, site_args=None, dry=False):
    """The one transition Reality -> TLS (§3.5 п.1–7, З-2): ``gw vless mode tls``, the button of the wizard, §3.3, an
    implicit ``sync`` with nobody to give links again, ``site mode site`` that switches VLESS to TLS. Under the lock of
    a transition: the checks first (nothing written), the price (devices and no ``yes`` -> CONFLICT «vless_change»),
    the snapshot (``mode_pending``), the site on (``site_args`` — its template) or ``sync(want="tls")``; success ->
    ``want`` "tls", the consent and ``tls_error`` off; any refusal or exception -> undone by the snapshot (the site off
    again, the record as it was, ``tls_error``). -> {"ok", "changed", "security", "old", "port", "reissue", "text",
    "reason", "error"?, "restored"?, "site_out"?}. ``dry`` — only the question (``vless_ask``): no lock, nothing
    written; the refusals as they are, nothing to ask -> {"ok", "dry", "need": None}."""
    from ..site import front
    from ..site import ops as site_ops
    with (_no_guard() if dry else front.vless_guard(runner)):
        st = state.load()
        rec = _vless_rec(st)
        sec = rec.get("security") or "reality"
        if sec == "tls" and front.xray_fronts(runner):
            if dry:
                return _dry_none()
            front._note([("want", front.WANT_TLS)], [k for k in ("reality_consent", "tls_error") if k in rec])
            return {"ok": True, "changed": False, "security": "tls", "old": "tls", "port": rec.get("port"),
                    "reissue": [], "text": "", "reason": "", "why": "", "kind": VLESS}
        ok, why = front.tls_possible(st, runner, ignore_mode=True)
        if not ok:
            r = front._why_reason(st, runner)
            fix = _VLESS_FIX.get(r)
            raise ConflictError(tr("gw_in_vless.tls_impossible", why=why),
                                data={"need": "tls_impossible", "reason": r,
                                      "fix": {"do": fix, "text": tr("gw_in_vless.fix." + fix)} if fix else None})
        site_on = _site_on(st)
        if not site_on:
            site_ops.site_checks(st, runner, for_vless=True)
        devs = _reissue_devices(st, VLESS)
        if devs and not yes:
            text = _price_tls(devs, site_on)
            raise ConflictError(text, data={"need": "vless_change", "kind": VLESS, "text": text, "devices": devs,
                                            "to": {"security": "tls"}, "site_on": site_on})
        if dry:
            return _dry_none()
        before = front.link_key(st)
        snap = front.snap_take(runner)
        site_out, got, fail = None, {}, ""
        try:
            if site_on:
                got = front.sync(runner, want=front.WANT_TLS)
            else:
                site_out = site_ops.mode(runner, "site", vless_want=front.WANT_TLS, tx=True, yes=True,
                                         **(site_args or {}))
                got = site_out.pop("vless", None) or {}
            if (front._rec(state.load()) or {}).get("security") != "tls" or not front.xray_fronts(runner):
                fail = got.get("error") or "; ".join(w for w in ((site_out or {}).get("warnings") or [])
                                                      if isinstance(w, str)) or tr("site.front.xray_refused")
        except Exception as e:                          # noqa: BLE001 — whatever broke, the snapshot comes back (п.6)
            fail = front.clean_why(e) or e.__class__.__name__
        if fail:
            return _vless_undo(runner, snap, front.clean_why(front.tls_why(fail)).rstrip(" ."), before, sec, via, who)
        front._note([("want", front.WANT_TLS)], ("reality_consent", "tls_error", "mode_pending"))
        st1 = state.load()
        changed = front.link_key(st1) != before
        rows, text = _reissue(st1, VLESS, tr("site.front.to_tls", domain=front.model.unicode(
            front.model.domain_of(st1)))) if changed else ([], "")
        util.log_tr("gw_in_vless.log.to_tls", via=via or "—", who=who or "—", n=len(rows))
        out = {"ok": True, "changed": changed, "security": "tls", "old": sec, "port": front.TLS_PORT,
               "reissue": rows, "text": text, "reason": "", "why": "", "kind": VLESS}
        if site_out is not None:
            out["site_out"] = site_out
        return out


def _vless_undo(runner, snap, why, before, sec, via, who):
    """§3.5 п.6: the transition refused — the snapshot back. -> the answer ``ok`` False with ``restored``."""
    from ..site import front
    try:
        res = front.snap_restore(runner, snap, why, before=before)
    except Exception as e:                              # noqa: BLE001 — mode_pending stays: the next entry tries again
        err = front.clean_why(e) or e.__class__.__name__
        util.log_tr("gw_in_vless.log.recover_failed", why=err)
        return {"ok": False, "changed": True, "restored": False, "security": None, "old": sec, "reason": "tls_failed",
                "error": tr("gw_in_vless.undo_failed", why=why, err=err), "reissue": [], "text": "", "kind": VLESS}
    util.log_tr("gw_in_vless.log.tls_undone", via=via or "—", who=who or "—", why=why)
    st = state.load()
    rec = model.inbound(st, VLESS) or {}
    out = {"ok": False, "changed": not res["restored"], "restored": res["restored"], "security": "reality",
           "old": sec, "port": rec.get("port"), "reason": "tls_failed", "why": why,
           "error": tr("gw_in_vless.tls_undone", why=why), "reissue": [], "text": "", "kind": VLESS}
    if not res["restored"]:
        out["reissue"], out["text"] = _reissue(st, VLESS, "; ".join(res["changed"]))
    return out


def _dry_none():
    return {"ok": True, "dry": True, "need": None}


def _no_guard():
    import contextlib
    return contextlib.nullcontext()


# what the actions of the panel ask before they act (T-3378 Н-2): «do» of GET /api/gw/vless/ask -> (value, drop_domain)
# of site.ops.vless_price — the site on, off, the domain away
_ASK_SITE = {"site_on": ("site", False), "site_off": ("domain", False), "clear": ("domain", True)}
ASK_DOS = ("tls", "reality") + tuple(sorted(_ASK_SITE))
_ASK_NEEDS = ("reality_consent", "vless_change")


def vless_ask(runner, do, site=None, lang=None):
    """GET /api/gw/vless/ask (T-3378 Н-2): what an action of the panel asks before it acts — the consent to Reality
    («reality_consent»: title, block, risks, site, phrase) or the price of new links («vless_change»: text, devices,
    to, site, need_pick) — as a plain read with the very words the core refuses the action with (the browser writes
    every 4xx to the console as an error; the window asked by a refusal at each opening). ``do``: "tls" | "reality"
    (``vless_mode`` with ``dry``; ``site`` — the cover of Reality) | "site_on" | "site_off" | "clear" (the site on,
    off, «Убрать домен» — ``site.ops.vless_price``; ``site`` — the cover named). No lock, nothing written: the action
    asks again itself (a device that came in between — its refusal again, the price anew). Nothing to ask, or another
    refusal (TLS impossible, a port busy, a transition under way) — ``need`` None: the action says it. -> {"ok",
    "need", …the data of the refusal, "message"}."""
    from ..site import model as smodel
    from ..site import ops as site_ops
    if do not in ASK_DOS:
        raise KaskadError(tr("gw_in_vless.bad_mode", mode=do), code=USAGE)
    try:
        if do in ("tls", "reality"):
            vless_mode(runner, do, site=site or None, via="panel", lang=lang, dry=True)
        else:
            st = state.load()
            value, drop = _ASK_SITE[do]
            if smodel.peek(st)["domain"]:
                site_ops.vless_price(st, runner, value, vless_site=site or None, drop_domain=drop)
    except ConflictError as e:
        data = getattr(e, "data", None) or {}
        if data.get("need") in _ASK_NEEDS:
            return dict(data, ok=True, message=str(e))
    except KaskadError:
        pass
    return {"ok": True, "need": None}


def _consent_refusal(rd, text=None):
    return ConflictError(text or tr("gw_in_vless.need_consent"),
                         data={"need": "reality_consent", "kind": VLESS, "block": rd["text"], "title": rd["title"],
                               "risks": rd["ver"], "site": rd["site"], "phrase": rd["phrase"]})


def vless_mode(runner, mode, site=None, accept=False, risks=None, via="cli", who=None, yes=False, no_check=False,
               lang=None, recover=False, dry=False):
    """``gw vless mode tls|reality`` (§3.5, §4.2): ``tls`` — ``vless_to_tls``; ``reality`` — by §3.5 T→R п.0 (Н2):
    with a domain and the consent (``accept``, ``risks`` and ``site`` of the text shown — for panel/setup/mcp they must
    be given and match) -> ``want`` "reality" + ``reality_consent``; TLS impossible now and no consent -> the cover only
    (``want`` stays); no domain -> ``want`` "reality_nodomain" (a consent given before stays). The price of new links
    — ``yes`` (else CONFLICT «vless_change»). ``recover`` — ``gw vless mode --recover``: a transition left behind undone
    now. ``dry`` — only the question (``vless_ask``): no consent taken, no lock, nothing written."""
    from ..site import front
    from .inbounds import vless as vin
    if dry and (accept or recover):
        raise KaskadError("vless_mode: dry asks before the consent", code=USAGE)
    if recover:
        with front.vless_guard(runner, recover=False):
            got = front.recover_pending(runner)
        if got is None:
            return {"ok": True, "kind": VLESS, "recovered": False, "text": tr("gw_in_vless.nothing_pending")}
        if not got["restored"]:
            raise KaskadError(tr("gw_in_vless.recover_failed_entry", why=got["why"]), data={"need": "recover"})
        return {"ok": True, "kind": VLESS, "recovered": True, "text": tr("gw_in_vless.recovered")}
    if mode == "tls":
        return vless_to_tls(runner, via=via, who=who, yes=yes, dry=dry)
    if mode != "reality":
        raise KaskadError(tr("gw_in_vless.bad_mode", mode=mode), code=USAGE)
    via = via if via in front.VLESS_VIAS else "cli"
    who = "mcp" if via == "mcp" else who
    with (_no_guard() if dry else front.vless_guard(runner)):
        st = state.load()
        rec = _vless_rec(st)
        dom = front.model.domain_of(st)
        sec = rec.get("security") or "reality"
        if site == "kept":
            site = rec.get("sni") if front.kept_mask(rec) else None
        if not dom:                                     # no domain: no consent needed, the choice is remembered (З1)
            return _dry_none() if dry else _reality_nodomain(runner, st, rec, site, no_check, via, who)
        ok_tls = front.tls_possible(st, runner, ignore_mode=True)[0]
        if not accept and not ok_tls and sec != "tls":  # a forced Reality: the cover only, ``want`` stays (Н2)
            if dry:
                return _dry_none()
            if not site:
                raise ConflictError(tr("gw_in_vless.reality_already", why=front.reason(st, runner)["why"] or "—"),
                                    data={"need": "site", "kind": VLESS})
            got = vless_mask(runner, site, check=not no_check)
            got["note"] = tr("gw_in_vless.choice_not_written")
            got["want"] = front.want_of(model.inbound(state.load(), VLESS) or {})
            return got
        strict = via in ("mcp", "panel", "setup")       # the text shown must be named: risks and site (С2)
        if site in (None, ""):
            site = front.risks_data(st)["site"]         # the saved cover, else own, else none (pick first)
            if strict and accept and site:
                raise _consent_refusal(front.risks_data(st, lang, site=site), tr("gw_in_vless.risks_changed"))
        if site == "auto" or not site:
            rd = front.risks_data(st, lang, site=None if site == "auto" else site)
            raise _consent_refusal(rd, tr("gw_in_vless.pick_first"))
        rd = front.risks_data(st, lang, site=site)
        if not accept:
            raise _consent_refusal(rd)
        if (via in ("mcp", "panel", "setup") and not risks) or (risks and risks != rd["ver"]):
            raise _consent_refusal(rd, tr("gw_in_vless.risks_changed"))
        try:
            fields = vin.mask_for_mode(st, site, check=not no_check)       # network: before the lock of state
        except KaskadError as e:
            raise KaskadError(str(e), code=USAGE, data=e.data)
        cp = copy.deepcopy(st)
        r2 = model.inbound(cp, VLESS)
        r2.update(fields)
        r2.update(want=front.WANT_REALITY, reality_consent={"at": "x", "via": via, "risks": rd["ver"]})
        p = front.plan(cp, runner, pick=lambda _s: {"_pick": True})
        if p["fields"].get("port", "x") is None:
            p["fields"]["port"] = front.REALITY_PORTS[0]
        was = front._link_of(st, {"security": sec, "fields": {}})
        now = front._link_of(cp, p)
        devs = _reissue_devices(st, VLESS)
        if was != now and devs and not yes:
            text = tr("gw_in_vless.price_reality", n=len(devs), who=_who(_reissue_names(devs)), port=now[2],
                      site=now[3] or site)
            raise ConflictError(text, data={"need": "vless_change", "kind": VLESS, "text": text, "devices": devs,
                                            "to": {"security": "reality", "port": now[2], "sni": now[3]}})
        consent = {"at": util.now_iso(), "via": via, "who": who or via, "risks": rd["ver"], "site": rd["site"],
                   "domain": dom, "lang": lang or saved_lang()}

        def mutate(new, s_):
            r = _vless_rec(new)
            r.update(fields)
            r.update(want=front.WANT_REALITY, reality_consent=consent)
            r.pop("tls_error", None)
        _change(runner, mutate)
        got = front.sync(runner, pick=lambda _s: vin.mask_for_mode(_s, site, check=False))
        st1 = state.load()
        changed = was != front._link_of(st1, {"security": (model.inbound(st1, VLESS) or {}).get("security"),
                                               "fields": {}})
        rows, text = _reissue(st1, VLESS, tr("site.front.to_reality", sni=(model.inbound(st1, VLESS) or {}).get(
            "sni") or "—", port=(model.inbound(st1, VLESS) or {}).get("port"))) if changed else ([], "")
        util.log_tr("gw_in_vless.log.reality_consent", via=via, who=who or via, risks=rd["ver"], site=rd["site"])
        r1 = model.inbound(st1, VLESS) or {}
        return {"ok": True, "kind": VLESS, "changed": changed, "security": r1.get("security") or "reality",
                "old": sec, "port": r1.get("port"), "sni": r1.get("sni"), "want": r1.get("want"),
                "reissue": rows, "text": text or (tr("gw_in_vless.consent.price_same") if not changed else ""),
                "error": got.get("error") or None}


def _reality_nodomain(runner, st, rec, site, no_check, via, who):
    """``mode reality`` with no domain: the cover (``site``) and ``want`` "reality_nodomain" — no consent (nothing to
    weigh); a choice of Reality with a consent given before stays as it is (решения координатора, придирка)."""
    from ..site import front
    from .inbounds import vless as vin
    out = {"ok": True, "kind": VLESS, "changed": False, "reissue": [], "text": ""}
    if site:
        out = vless_mask(runner, site, check=not no_check)
    keep = front.want_of(rec) == front.WANT_REALITY and front.consent_ok(rec)
    if not keep:
        front._note([("want", front.WANT_NODOMAIN)])
    util.log_tr("gw_in_vless.log.nodomain", via=via, who=who or via)
    out["want"] = front.want_of(model.inbound(state.load(), VLESS) or {})
    return out


def vless_rekey(runner):
    """``gw vless rekey --yes``: a new key and shortId of the node. Every link given before stops connecting: the
    devices of VLESS need their settings again (``reissue``); the UUIDs of the devices stay."""
    ad = adapter(VLESS)

    def mutate(new, sec):
        _vless_rec(new)
        return ad.rekey(new, sec)

    done, res = _change(runner, mutate)
    st = state.load()
    rows, text = _reissue(st, VLESS, tr("gw_ops.vless.rekeyed"))
    util.log_tr("gw_ops.log.vless_rekey", done=", ".join(retr(done, saved_lang())), n=len(rows))
    return {"ok": True, "kind": VLESS, "created": done, "pub": (model.inbound(st, VLESS) or {}).get("pub") or "",
            "short_id": (model.inbound(st, VLESS) or {}).get("short_id") or "", "reissue": rows, "text": text,
            "apply": res}


def _first_offer(platform, usable):
    try:
        from . import issue_os
    except ImportError:
        issue_os = None
    if issue_os is not None:
        offer = issue_os.first(platform, usable)
        if offer:
            return offer["inbound"]
    order = ISSUE_ORDER.get(platform, ISSUE_ORDER["other"])
    return next((k for k in order if k in usable), usable[0])


def issue(runner, ref, inbound=None, os_name=None, where="menu", mark=True, host=None):
    """Settings for a device (spec §4) — the ONLY output with secrets. Read-only. ``where`` — who reads the steps
    (``issue_os.WHERE``: the SSH menu, the panel, the person's page, the bot): «Как подключить» (``howto``) and the
    guides of the adapters speak the actions of that screen — «Сохранить в файлы» of the menu, «Скачать» and
    «Скопировать» of the panel (stand T-G1b, N4). ``mark=False`` — the subscription of /p/ (panel/userpage.py): an app
    refreshes it by itself every 12 h with every protocol of every device of the user — that is not «выдали»: the
    mark «ждут новых настроек» (SPEC-awg-engine §6.1) stays (the device may still hold its static key) and «выдали —
    подключился ли кто-нибудь» (inlog.closed: «порт закрыт?» 10 min later) does not start. ``host`` — 3.3.7.15 F13:
    the settings for this address instead of ``gateway.host`` (the extra IPv6 line of the subscription): the adapters
    get a copy of the state with ``gateway.host`` replaced (the future ``model.endpoint_of``, PLAN-multi-ip-roles
    §6.1), none of them is touched; only with ``mark=False`` — no file or QR is ever given on another address."""
    from . import issue_os
    st = state.load()
    if host is not None:
        st = _issue_host(st, host, mark)
    d = model.get_device(st, ref)
    os_name = os_name or d.get("platform") or "other"
    if os_name not in model.PLATFORMS:
        raise KaskadError(tr("gw_ops.err.platform", platforms=", ".join(model.PLATFORMS)), code=USAGE)
    usable = [k for k in d.get("access") or [] if k in model.enabled_kinds(st) and registry.get(k) is not None]
    if inbound:
        if inbound not in (d.get("access") or []):
            raise KaskadError(tr("gw_ops.err.not_granted_hint", kind=inbound, id=d["id"]))
        if inbound not in usable:
            raise KaskadError(tr("gw_ops.err.not_usable", kind=registry.title(inbound)))
        kind = inbound
    else:
        if not usable:
            raise KaskadError(tr("gw_ops.err.no_usable"))
        kind = _first_offer(os_name, usable)
    sec = secrets.load()
    with addr.using(st), issue_os.issued_for(where):
        got = adapter(kind).issue(st, sec, d, os_name)
    if isinstance(got, dict) and isinstance(got.get("items"), list):
        items = list(got["items"])
    elif isinstance(got, dict):
        items = [got]
    else:
        items = list(got or [])
    items = [dict(it) for it in items]
    for it in items:
        for k in ("format", "title", "body", "filename", "notes"):
            if it.get(k) is None:
                it[k] = [] if k == "notes" else ""
    util.log_tr("gw_ops.log.issue", name=d["name"], id=d["id"], kind=kind, os=os_name)
    if mark:
        _preview_forget(kind, d["id"])                 # П8 (OBS-1822): the settings of now replace those of F7
    try:
        if mark:
            gwmtu.issued(kind, d["id"])                # §6.1: «ждут новых настроек» — выдали
            from . import inlog
            inlog.mark_issued(kind)                    # 3.3.3: «выдали — подключился ли кто-нибудь» (порт закрыт?)
    except (OSError, ValueError) as e:
        util.log_tr("gw_ops.log.issue_mark", err=e)
    howto = issue_os.howto(os_name, kind, [it.get("filename") for it in items if it.get("filename")], where=where)
    # «Установите приложение»: приложения этой пары протокол + система со ссылками на магазины (каталоги, не код);
    # нет приложения — честная строка, протокол не подменяется (владелец, 3.3.2)
    from . import install_apps
    # таблица поддержки «платформа × протокол» (SPEC-issue-fallbacks.md): уровень пары, условие, инструкция, пометка и
    # другие протоколы этой платформы с состоянием по факту сервера и устройства (ready / device_off / server_off)
    server = [k for k in model.enabled_kinds(st) if registry.get(k) is not None]
    alts = install_apps.alternatives(kind, os_name, server=server, device=d.get("access") or [],
                                     known=[k for k in model.INBOUND_KINDS if registry.get(k) is not None], where=where)
    return {"ok": True, "device": d["id"], "name": d["name"], "kind": kind, "os": os_name, "items": items,
            "available": usable, "howto": howto, "install": install_apps.block(kind, os_name),
            "support": install_apps.support(kind, os_name, alts, where=where), "alternatives": alts}


def _issue_host(st, host, mark):
    """F13: a copy of ``st`` whose ``gateway.host`` is ``host`` (an IP in its one written form). Refused with ``mark``
    (a file, a QR, the window of the panel or of the menu: 3.3.7.15 gives another address only in the subscription)
    and for anything but an IP."""
    import ipaddress
    try:
        ip = str(ipaddress.ip_address(str(host).strip()))
    except ValueError:
        ip = None
    if mark or ip is None or ip != str(host).strip():
        raise KaskadError(tr("gw_ops.err.issue_host"), code=USAGE)
    out = dict(st)
    out["gateway"] = dict(model.gw(st), host=ip)
    return out


# ── exits ──────────────────────────────────────────────────────────────────

def exits_view(runner, st=None, roles=("abroad",)):
    """The exits for people: ``exits.public`` rows with ``usable``, ``handshake`` (seconds since the last one of an
    AmneziaWG leg — from the collector's live.json while it is fresh, else None), ``others`` (how many other exits
    work) and ``text`` (``exits.state_text``: «работает, рукопожатие 40 с назад», «не работает: нет ответа 35 с —
    клиенты переходят на другой выход»). ``roles`` — as ``exits.pool``: the foreign exits by default; ``gw exits``
    asks both (T-3379 М-1: the Russian ones, ``role`` «ru»; ``others`` — of the same role)."""
    st = state.load() if st is None else st
    pool_exits = exits.pool(runner, roles=roles)
    snap = exits.load_snapshot()
    alive = exits.alive_ids(snap)
    doc, age = live.read()
    lex = ((doc or {}).get("exits") or {}) if live.fresh(age) else {}
    now = exits._now()
    src = exitsubs.owners()
    gw_off = not model.enabled(st)                      # the collector checks the exits only while the gateway is on
    rows = []
    for e in pool_exits:
        row = exits.public(e, st, snap)
        row["gw_off"] = gw_off
        row["source"] = src.get(e["id"])                # the subscription the key came from (None — by hand)
        row["usable"] = bool(exits.parsed(e) and row["enabled"] and e["id"] in alive)
        at = (lex.get(e["id"]) or {}).get("handshake_at") if e["kind"] == "awg" else None
        row["handshake"] = max(0, int(now - at)) if isinstance(at, (int, float)) and not isinstance(at, bool) \
            and at > 0 else None
        rows.append(row)
    working = {}
    for r in rows:
        working[r.get("role")] = working.get(r.get("role"), 0) + (1 if r["usable"] else 0)
    for r in rows:
        r["others"] = working[r.get("role")] - (1 if r["usable"] else 0)
        r["text"] = exits.state_text(r)
        held = reserve_text((snap.get("exits") or {}).get(r["id"]), now)
        if held and not r.get("problem") and r.get("enabled", True) and r.get("state") == "alive":
            # Н11 (T-KT1): an exit «в запасе» is not «работает»; one left in work while unstable says so
            r["text"] = held if not r["usable"] else "%s · %s" % (r["text"], held)
    return rows


def reserve_text(rec, now):
    """The words of the reserve of stage 1 (§4.3) for a record of the snapshot: «в запасе: нестабилен (N падений за 15
    мин), вернётся в ЧЧ:ММ» / «в запасе» (``standby``), «нестабилен · до ЧЧ:ММ» (in work, the only usable one of its
    group); "" — neither. The time — on the clock of the node."""
    rec = rec if isinstance(rec, dict) else {}
    until = rec.get("unstable_until")
    until = until if isinstance(until, (int, float)) and not isinstance(until, bool) and until > now else None
    if rec.get("state") == "standby":
        if until is not None and "unstable" in (rec.get("reasons") or []):
            n = len([t for t in rec.get("falls") or [] if isinstance(t, (int, float)) and now - t < policy.UNSTABLE_S])
            return trn("ep.show.standby_unstable", max(n, 1), time=live._clock(until))
        return tr("gw_live.state_now.standby")
    if rec.get("unstable") and until is not None:
        return tr("ep.show.unstable", time=live._clock(until))
    return ""


def exit_set(runner, eid, on=None, name=None, allow_duplicate=False, via="cli"):
    """The switch and the name of an exit. A name alone — the light write of ``rename`` (no apply, T13); with the
    switch — the name checked the same way (``names.check``, duplicates), then applied with it."""
    if on is None and name is not None:
        from . import rename
        r = rename.rename(runner, "exit", eid, name, allow_duplicate=allow_duplicate, via=via)
        return {"ok": True, "id": eid, "pref": r["pref"], "apply": None, "rename": r}
    if name is not None:
        from . import rename
        name = rename.checked_exit_name(eid, name, allow_duplicate)

    def mutate(new, sec):
        return model.set_exit(new, eid, enabled=on, name=name)
    p, res = _change(runner, mutate)
    with util.journal_lang():
        util.log_action("GW exit %s: %s" % (eid, ", ".join(x for x in (
            None if on is None else tr("gw_ops.exit.on" if on else "gw_ops.exit.off"),
            None if name is None else tr("gw_ops.exit.named", name=name)) if x)))
    return {"ok": True, "id": eid, "pref": p, "apply": res}


# ── keys of the exits: added and removed the way the SSH menu does it ──────
# The pool is «Прокси для API Telegram» (/etc/kaskad/egress/upstream.txt), and only install.sh writes it: its parser,
# its rebuild (xray of the bot, the legs, then ``gw apply``), its choice of the engine of the first AmneziaWG leg. The
# panel runs ``gokaskad --egress-add`` (the key on stdin — never in argv: /proc/<pid>/cmdline is open to everyone) and
# ``gokaskad --egress-del <line> <hex>`` and holds NO state lock meanwhile: the script's ``gw apply`` takes it. The
# script serialises changes of the pool itself (flock on /run/kaskad/egress-pool.lock).

EXIT_ADD_TIMEOUT = 23 * 60          # the first AmneziaWG key: headers + DKMS build (1–3 min), apt may wait for its lock
EXIT_DEL_TIMEOUT = 10 * 60
RC_POOL_CHANGED = 3                 # --egress-del: the line under that number is another key now
RC_ADD_DUP = 4                      # --egress-add: no new key — every one is in the pool already
RC_ADD_FULL = 5                     # --egress-add: the pool holds EGRESS_MAX keys
RC_POOL_BUSY = 6                    # another window (a tab, the menu) changes the pool
RC_HANDOVER = 7                     # --egress-del: the routes of the copy did not move to the other one — not removed
RC_TIMEOUT = 124                    # sysrun: the command ran out of time
KEY_MAX = 64 * 1024
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
MARK = "@K3:"                       # a machine line of install.sh (k3_mark): never shown, never translated

# The contract with install.sh (3.3.2, k3/handoff/I18N-3.3.2.md «Обмен меню ↔ ядро»): the core runs the pool's modes
# (--egress-add, --egress-del, --egress-replace) with K3_MARKS=1 (script_env) and the script adds machine lines:
#   @K3:egress_fail:<code>[:args]   a cause of the failure (codes: SCRIPT_FAIL below; more may come — then the line
#                                   for people printed right before the mark is said instead)
#   @K3:egress_warn:<code>[:args]   a warning: xray_builtin, awg_core_missing, tg_group_empty (stage 1: the bot's proxy
#                                   is off — its group has no usable key), awg_key:<n>:<lost3x|need31|loss226>,
#                                   skipped:<code of SKIP_CODES>:<n> (3.3.4: keys xray would refuse, left out one by one;
#                                   bad_addr — 3.3.5.1: no usable address of the AmneziaWG client; self_ep — 3.3.6: the
#                                   server is this one; gw_net:<n>:<network> — 3.3.6 round 3: the Address in the network
#                                   of the gateway of this node), vless_key:<n>:<code of VLESS_KEY_CODES; gw_net:<address>:
#                                   <network>> (3.3.4: such a key already in the pool, left out; stage 1 — an xray key of
#                                   any scheme, the name of the mark kept); egress_fail:skipped:<code>[:<network> of gw_net]
#   @K3:egress_limit:<max>          the pool is full
#   @K3:egress_same_peer:<n>, @K3:egress_pool_empty — facts the core reads from the pool itself
#   @K3:egress_handover:<n>         --egress-del (3.3.5): the routes moved to the other copy of the key (gw exit-handover)
# Each mark comes right after the line for people it marks, in the same stream. The words of the core are its own
# (gw_ops.script_*, the language of the call); the script's lines are read only for a cause it alone knows (the
# reason xray gave) and, for an install.sh without marks (older than 3.3.2: Russian only), the old way below.
SCRIPT_FAIL = ("awg_core", "awg_only", "no_keys", "no_keys_found", "parse", "xray_invalid", "rolled_back",
               "sub_cert", "sub_http", "sub_fetch", "sub_clash", "pool_busy", "pool_changed", "bad_index", "bad_ref",
               "bad_hash", "bad_line", "tmp", "empty", "path", "no_xray", "skipped", "handover", "handover_unfit",
               "handover_broken")
# its line has what only the script knows (xray's reason; handover_broken — the cause the core gave install.sh)
_SAID_BY_SCRIPT = ("xray_invalid", "rolled_back", "skipped", "handover_broken")
# 3.3.4: why the parser left a key out (egress_parse.vless_problem); stage 1 (§9.2): link_problem of every scheme —
# no_tls, weak_cipher, bad_link, unsupported_scheme (tuic://, anytls:// …), dup_endpoint (a second Hysteria2 at an address);
# REV-s1-t11-2: xray_refused (the real xray refused the key), xray_unchecked (it did not answer in time: not taken);
# 3.3.6: self_ep (the server is this node — any scheme), gw_net (the Address in the network of the gateway of this node)
SKIP_CODES = ("insecure", "bad_pin", "no_tls", "weak_cipher", "bad_link", "unsupported_scheme", "dup_endpoint",
              "xray_refused", "xray_unchecked", "bad_addr", "self_ep", "gw_net")   # bad_addr: 3.3.5.1
# a key of the pool left out; tofu_wait — its server is tried again; xray_legacy — built without the parameters xray
# refused (the form of 3.3.5: egress_parse xray_heal)
VLESS_KEY_CODES = SKIP_CODES + ("tofu_wait", "xray_legacy", "xray_ext")
# good news of a key, not a warning (REV-s1-b2 П-2): xray_ext — a key added with no xray, now checked in full. The mark
# stays ``egress_warn:vless_key:<n>:xray_ext`` (the contract with install.sh); the core says it as a note, not a warning
GOOD_KEY_CODES = ("xray_ext",)
SCRIPT_WARN = ("xray_builtin", "awg_core_missing", "tg_group_empty",   # tg_group_empty: REV-s1-t11 С5, the bot direct
               "ru_held")                  # REV-s1-q4-2 С-1: Russian legs wait for the gateway to apply
AWG_KEY_WARN = ("lost3x", "need31", "loss226")
# the old install.sh (no marks) speaks Russian only — its words are matched as they are
_RU = re.compile("[А-Яа-яЁё]")  # i18n: skip — вывод install.sh без меток @K3: (до 3.3.2) только русский
_SCRIPT_WARNINGS = ("Достигнут предел", "AWG-ядро недоступно",  # i18n: skip — вывод старого install.sh
                    "Не удалось загрузить отдельный xray")  # i18n: skip — вывод старого install.sh
_FULL_WORDS = "Достигнут предел"  # i18n: skip — вывод старого install.sh


def script_env():
    """The environment of a mode of gokaskad whose output the core reads: the marks on (K3_MARKS=1) and the language
    of the call (KASKAD_LANG) — the lines for people come in the language of the request."""
    return {"K3_MARKS": "1", "KASKAD_LANG": get_lang()}


def _script_lines(res):
    """What the script said, colours away, one line per line."""
    text = _ANSI.sub("", "%s\n%s" % (res.out or "", res.err or ""))
    return [" ".join(ln.split()) for ln in text.replace("\r", "\n").splitlines() if ln.strip()]


def _marks(lines):
    """[(index, code, [args])] of the marks of the pool's modes (``@K3:egress_…``); [] — an install.sh without them."""
    out = []
    for i, ln in enumerate(lines):
        if ln.startswith(MARK + "egress_"):
            parts = ln[len(MARK):].strip().split(":")
            out.append((i, parts[0], parts[1:]))
    return out


_NET_ARG = re.compile(r"^[0-9.]+(/[0-9]{1,2})?$")   # gw_net: the address of the client and the network (IPv4) of a mark


def _net_args(addr=None, net=None):
    """{"addr", "net"} of a gw_net mark for the words (anything else in their place — «?»)."""
    return {"addr": addr if addr and _NET_ARG.match(addr) else "?", "net": net if net and _NET_ARG.match(net) else "?"}


def _known(key, **kw):
    """tr(key, **kw), or None when no catalog has the key (a code of a newer script)."""
    text = tr(key, **kw)
    return None if text == key else text


def _said_before(lines, i):
    """The line for people the mark at ``i`` stands right after (the script's own words), or None."""
    if i <= 0:
        return None
    ln = lines[i - 1]
    if ln.startswith((MARK, "[*]")):
        return None
    return ln.lstrip("⚠").strip() or None


def _warn_text(lines, i, args):
    what = args[0] if args else ""
    if what == "awg_key" and len(args) >= 3 and args[2] in AWG_KEY_WARN:
        text = _known("gw_ops.script_warn.awg_key." + args[2], n=args[1])
    elif what == "vless_key" and len(args) >= 3 and args[2] in VLESS_KEY_CODES:
        text = _known("gw_ops.script_warn.vless_key." + args[2], n=args[1], **_net_args(*args[3:5]))
    elif what == "skipped" and len(args) >= 3 and args[1] in SKIP_CODES and args[2].isdigit():
        text = _known("gw_ops.script_warn.skipped." + args[1], n=int(args[2]), **_net_args(None, *args[3:4]))
    else:
        text = _known("gw_ops.script_warn." + what) if what in SCRIPT_WARN else None
    return text or _said_before(lines, i)


def _limit_text(args):
    return tr("gw_ops.script_warn.limit", max=args[0] if args else "?")


# The field xray names in «invalid "<field>"» (the mark ``xray_invalid:field:<field>[:reality]``, lower case) -> its
# label in the key (the same as the parser's XRAY_FIELDS in install.sh; names of the URI, not translated).
XRAY_FIELD_LABELS = {"password": "publicKey (pbk)", "publickey": "publicKey (pbk)", "shortid": "shortId (sid)",
                     "shortids": "shortId (sid)", "id": "UUID", "servername": "SNI (sni)",
                     "fingerprint": "fingerprint (fp)", "flow": "flow", "spiderx": "spiderX (spx)", "path": "path",
                     "servicename": "serviceName"}


def _coded_fail(what, args):
    """The words of the core for a failure whose mark carries its cause as a code (install.sh 3.3.2, F):
    ``rolled_back:add|replace`` and ``xray_invalid:field:<field>[:reality]|none``; None — no such code (an older
    mark, ``xray_invalid:other``: the reason is xray's own, only the script's line has it)."""
    if what == "rolled_back" and args[1:2] in (["add"], ["replace"]):
        return tr("gw_ops.script_fail.rolled_back_" + args[1])
    if what == "xray_invalid" and args[1:2] == ["none"]:
        return tr("gw_ops.script_fail.xray_none")
    if what == "xray_invalid" and args[1:2] == ["removed"] and len(args) >= 3 and re.match(r"^\w+$", args[2]):
        if args[2] == "allowinsecure":
            return tr("gw_ops.script_fail.xray_insecure")
        return tr("gw_ops.script_fail.xray_removed", feature=args[2])
    if what == "skipped" and args[1:2] and args[1] in SKIP_CODES:
        return tr("gw_ops.script_fail.skipped." + args[1], **_net_args(None, *args[2:3]))
    if what == "xray_invalid" and args[1:2] == ["field"] and len(args) >= 3 and re.match(r"^\w+$", args[2]):
        label = XRAY_FIELD_LABELS.get(args[2].lower(), args[2])
        key = "gw_ops.script_fail.xray_field_reality" if args[3:4] == ["reality"] else "gw_ops.script_fail.xray_field"
        return tr(key, field=label)
    return None


def _fail_text(lines, i, args):
    what = args[0] if args else ""
    if what == "awg_core":
        tool = paths.egress_awg_tool()
        return tr("gw_ops.script_fail.awg_core", go=os.path.join(os.path.dirname(tool), "amneziawg-go"), tool=tool)
    if what in _SAID_BY_SCRIPT:
        # the cause as a code — the core's words; the script's line before the mark — only the fallback
        return _coded_fail(what, args) or _said_before(lines, i) or tr("gw_ops.script_fail." + what)
    return (_known("gw_ops.script_fail." + what) if what in SCRIPT_FAIL else None) or _said_before(lines, i)


def _key_no(code, args):
    """The number of the pool key a warning mark is about (``egress_warn:vless_key|awg_key:<N>:<code>``), else None."""
    if code == "egress_warn" and args[:1] in (["vless_key"], ["awg_key"]) and len(args) >= 2 and args[1].isdigit():
        return int(args[1])
    return None


def _good(code, args):
    """The mark is good news of a key (GOOD_KEY_CODES), not a warning."""
    return code == "egress_warn" and args[:1] == ["vless_key"] and len(args) >= 3 and args[2] in GOOD_KEY_CODES


def _script_notes(lines, only=None):
    """The good news of the rebuild (REV-s1-b2 П-2: «ключ #N проверен — работает со всеми параметрами») in the core's
    words, redacted, each once — of any key of the pool (the rebuild checks them all); the panel says them with the
    result, not among the warnings. ``only`` — the numbers of the keys a change touched (a subscription: its added and
    renamed keys, REV-s1-b4 П-3); None — every key. An install.sh without marks — none."""
    out = []
    for _i, code, args in _marks(lines):
        if _good(code, args) and (only is None or _key_no(code, args) in only):
            w = util.redact(_known("gw_ops.script_warn.vless_key." + args[2], n=args[1]) or "")
            if w and w not in out:
                out.append(w)
    return out


def _script_warnings(lines, only=None):
    """The warnings of the rebuild (marks ``egress_warn``/``egress_limit`` -> the core's words), redacted, each once.
    An install.sh without marks: its «⚠ …» lines and the old Russian words. ``only`` (3.3.5) — the numbers of the pool
    keys this change touched (the added ones): the warnings of the other keys (the rebuild tells the whole pool) are
    one line «ещё в пуле ключей с предупреждениями: N» instead; None — every one."""
    marks = _marks(lines)
    out = []
    if marks:
        mine = [(i, code, args) for i, code, args in marks if code in ("egress_warn", "egress_limit")
                and not _good(code, args)]                  # good news — _script_notes (REV-s1-b2 П-2)
        others = set()
        if only is not None:
            nos = [_key_no(c, a) for _, c, a in mine]
            others = set(n for n in nos if n is not None and n not in only)
            mine = [m for m, n in zip(mine, nos) if n is None or n in only]
        got = [_warn_text(lines, i, args) if code == "egress_warn" else _limit_text(args) for i, code, args in mine]
        if others:
            got.append(tr("gw_ops.script_warn.others", n=len(others)))
    else:
        got = [ln.lstrip("⚠").strip() for ln in lines
               if ln.startswith("⚠") or any(w in ln for w in _SCRIPT_WARNINGS)]
    for w in got:
        w = util.redact(w or "")
        if w and w not in out:
            out.append(w)
    return out


def _pool_full(lines):
    """The script said the pool holds its limit of keys (mark ``egress_limit``; the old script — in words)."""
    marks = _marks(lines)
    if marks:
        return any(code == "egress_limit" for _, code, _ in marks)
    return any(_FULL_WORDS in ln for ln in lines)


_ADVICE = re.compile(r"Что сделать|What to do|ключ\w* GitHub|GitHub key|8 → 6|^\d\) ")  # i18n: skip — слова install.sh


def _sentence(ln):
    ln = ln.rstrip(":").rstrip()
    return ln if ln.endswith((".", "!", "?", "…", ")", ";")) else ln + "."


def _script_error(lines, default):
    """The cause of a failure of the script in words for people. With the marks (install.sh 3.3.2): each
    ``egress_fail`` / ``egress_limit`` in the script's order, in the core's words (gw_ops.script_fail.*, the language
    of the call; the reason of xray — the script's line). Without them (an older install.sh, Russian only): the script's
    own words — the cause (its last two Russian lines) and what to do (its lines of advice wherever they stand: «Что
    сделать…», «1) ключ GitHub — меню 8 → 6…»), in the script's order, without the progress «[*] …» and warnings. What
    xray or the parser printed (English, it may quote a key) never gets here; the rest goes through util.redact."""
    marks = _marks(lines)
    if marks:
        parts = []
        for i, code, args in marks:
            text = _fail_text(lines, i, args) if code == "egress_fail" else \
                _limit_text(args) if code == "egress_limit" else None
            if text and _sentence(text) not in parts:
                parts.append(_sentence(text))
        return util.redact(" ".join(parts)) if parts else default
    said = [ln for ln in lines if _RU.search(ln) and not ln.startswith(("[*]", "⚠"))]
    keep = set(range(max(0, len(said) - 2), len(said)))
    keep.update(i for i, ln in enumerate(said) if _ADVICE.search(ln))
    parts = []
    order = sorted(keep)[-10:]
    for n, i in enumerate(order):
        ln = said[i]
        if ln.endswith(":") and n + 1 < len(order) and _ADVICE.search(said[order[n + 1]]):
            parts.append(ln)                            # «Что сделать (любое из):» and its list after it
            continue
        parts.append(_sentence(ln))
    return util.redact(" ".join(parts)) if parts else default


def _gw_net_marked(lines):
    """A key left out because its Address is in the network of the gateway of this node (marks ``egress_fail:skipped:
    gw_net`` / ``egress_warn:skipped:gw_net`` / ``egress_warn:vless_key:<n>:gw_net``) — F6 Е3 offers another network."""
    for _i, code, args in _marks(lines):
        if code in ("egress_fail", "egress_warn") and (args[:2] == ["skipped", "gw_net"]
                                                       or (args[:1] == ["vless_key"] and args[2:3] == ["gw_net"])):
            return True
    return False


def _needs_github_key(lines):
    """The script failed for want of the core of AmneziaWG (mark ``egress_fail:awg_core``; an older script: it named
    GitHub), and the node has neither a core nor a GitHub key: the panel shows its window of the GitHub key (data
    needs_key — the same as for the protocols)."""
    marks = _marks(lines)
    if marks:
        if not any(code == "egress_fail" and args[:1] == ["awg_core"] for _, code, args in marks):
            return False
    elif not any("GitHub" in ln for ln in lines):
        return False
    try:
        from .inbounds import awg as awg_inbound
        return bool(awg_inbound.needs_key())
    except Exception:                                   # noqa: BLE001 — a hint for a button, never a broken answer
        return False


def key_text(text):
    """The text of a key as the panel may hand it on (a paste or a file the browser read): not empty, at most 64 KB,
    text (no NUL, not a mass of U+FFFD — a binary file read as UTF-8), not a path to a file of this node (the script
    would read that file). Raises KaskadError (USAGE)."""
    if not isinstance(text, str) or not text.strip():
        raise KaskadError(tr("gw_ops.key.empty"), code=USAGE)
    if len(text) > KEY_MAX:
        raise KaskadError(tr("gw_ops.key.too_long", kb=KEY_MAX // 1024), code=USAGE)
    bad = text.count("\ufffd")
    if "\x00" in text or bad > 8 or bad * 100 > len(text):
        raise KaskadError(tr("gw_ops.key.not_text"), code=USAGE)
    one = text.strip()
    if "\n" not in one and (os.path.exists(text) or os.path.exists(one)):
        raise KaskadError(tr("gw_ops.key.path"), code=USAGE)
    return exits.squeeze(text)              # long runs of blanks: the parser would chew them for minutes


def exit_preview(runner, text, tofu_by=None):
    """What the text holds, before anything is written: {"ok", "items": [exits.preview rows + in_pool], "subscription",
    "text"}. The parser of the menu in this process (``--egress-ensure-parser`` only on a node without it); a
    subscription link is not downloaded. No key, no secret in the answer; nothing logged."""
    text = key_text(text)
    if exits.is_subscription(text):
        return {"ok": True, "items": [], "subscription": True, "text": exits.SUBSCRIPTION_TEXT}
    parser = exits.load_parser(runner)
    # stage 1 (§9.1; T7): ``tofu_by`` — the panel asks for the fingerprint of a key over TLS without a check (the button
    # «Закрепить» of the preview, T8): ``tofu`` of the row (gw/tofu.check)
    items = exits.preview(parser, text, set(i for i, _ in exits.line_ids()), _pool_peers(parser), tofu_by=tofu_by)
    new = [x for x in items if not x["in_pool"]]
    text = None
    if any(x.get("gw_net") for x in items):             # F6 Е3: «Сменить сеть шлюза на …» by the key refused
        free = suggest_base(runner)
        for x in items:
            if x.pop("gw_net", None) and free:
                x["suggest_base"] = free
    return {"ok": True, "items": items, "subscription": False, "module_build": module_build(items),
            "text": tr("gw_ops.exit.all_in_pool") if not new else ""}


def module_build(items):
    """Will adding these keys build the AmneziaWG kernel module (1–3 min: headers, DKMS)? A new AmneziaWG key on a node
    whose engine is (or by default becomes) the module, while no module is in memory — or a key of 3.1 while the module
    in memory has no set of 3.1. Files only: the words of the panel («Собираю модуль ядра…» — the owner took the wait
    for a fallen server, 3.2.21), never a decision."""
    from . import awgkmod
    new = [x for x in items or [] if not x.get("in_pool") and not x.get("problem") and x.get("kind") == "awg"]
    if not new:
        return False
    try:
        if awgengine.record(state.load())["want"] == "program":
            return False
        if not awgkmod.module_loaded():
            return True
        return any("3.1" in str(x.get("title") or "") for x in new) and not awgkmod.set_loaded()
    except (KaskadError, OSError, ValueError):
        return False


def _pool_peers(parser=None, role="abroad"):
    """{``exits.peer_key``: (id of the exit, its line, its name)} of the pool: the keys it already has (the first line of
    each) — a key of the text with the same ``peer_key`` is not added again, and the words say which exit has it.
    ``role`` — the file (T15: upstream-ru.txt for «ru»)."""
    out = {}
    lines = exits.pool_lines(role)
    if not lines:
        return out
    try:
        st = state.load()
    except KaskadError:
        st = {}
    for (eid, _), line in zip(exits.line_ids(lines, role), lines):
        k = exits.peer_key(line, parser)
        if k in out:
            continue
        name = model.exit_pref(st, eid)["name"]
        if not name and parser is not None:
            try:
                with exits._quiet():
                    lab = parser.pool_label(line) or {}
                name = exits.own_name({"name": lab.get("name"), "host": lab.get("host")})
            except Exception:                           # noqa: BLE001 — the id alone is said then
                name = ""
        out[k] = (eid, line, name)
    return out


def _found(runner, text):
    """The keys of the text as the script will write them (the same parser), or None: a subscription, no parser.
    A key the OTHER file of the pool holds (upstream-ru.txt, by its text or its server: ``peer_key``) — refused before
    the script (T15, SPEC-groups §2.2 «Один ключ — в одном файле»): ``key_in_other_role``."""
    if exits.is_subscription(text):
        return None                                     # its keys: install.sh egress_drop_ru_keys (REV-s1-q4 З3)
    try:
        parser = exits.load_parser(runner)
    except KaskadError:
        return None
    _other_role_refuse(parser, text, "abroad")          # SEC-s1-q4 З-4: before the preview — its failure is no way round
    try:
        return exits.preview(parser, text, set(i for i, _ in exits.line_ids()), _pool_peers(parser))
    except KaskadError:
        return None


def _other_role_refuse(parser, text, role):
    """A key of ``text`` that the file of the other role holds — by its text (``exit_id``) or by its server and
    credentials (``peer_key``) — ConflictError ``key_in_other_role`` RU/EN: «этот ключ уже в зарубежных (российских)
    выходах — перенесите его» (T15, SPEC-groups §2.2)."""
    other = "ru" if role == "abroad" else "abroad"
    have = exits.pool_lines(other)
    if not have:
        return
    ids = dict((exits.exit_id(l), i) for (i, _), l in zip(exits.line_ids(have, other), have))
    peers = _pool_peers(parser, other)
    try:
        with exits._quiet():
            report = getattr(parser, "extract_report", None)
            lines = list(report(exits.squeeze(text))[0] or []) if callable(report) else list(parser.extract(text) or [])
    except Exception:                                   # noqa: BLE001 — the preview said why already
        return
    for line in lines:
        hit = ids.get(exits.exit_id(line)) or (peers.get(exits.peer_key(line, parser)) or (None,))[0]
        if hit:
            raise ConflictError(tr("gw_ops.err.key_in_other_role", where=tr("gw_ops.where." + other), id=hit),
                                data={"code": "key_in_other_role", "exit": hit, "role": other})


def _results(found, before, after, full, failed=False):
    """Per key: added | in_pool | not_added (why: the limit of keys or an error)."""
    out = []
    for x in found or []:
        r = dict((k, x.get(k)) for k in ("id", "kind", "title", "where", "name"))
        if x["id"] in before or (x.get("same_peer") and x["id"] not in after):
            r["result"] = "in_pool"
            if x.get("same_text"):
                r["why"] = x["same_text"]           # «этот ключ уже есть как e-…, по другому адресу» (3.3.5 №4)
        elif x["id"] in after:
            r["result"] = "added"
        else:
            r["result"] = "not_added"
            r["why"] = tr("gw_ops.exit.why_full" if full else ("gw_ops.exit.why_error" if failed else
                                                                "gw_ops.exit.why_not_written"))
        out.append(r)
    return out


def _hint_group(group, role="abroad"):
    """The group a new key goes to (``exit_add --group``): a group of the policy of ``role`` (SPEC-groups §2.4) — its
    id; another — USAGE (``field``)."""
    from . import policy_ops
    if group in (None, ""):
        return None
    doc = policy_ops._base(state.load())
    g = policy.group(doc, group)
    if g is None or g.get("role", "abroad") != role:
        raise policy_ops.bad_field("group", tr("gw_policy_ops.group_target", field="group", value=group))
    return group


def _group_role(group):
    """"ru" — ``group`` is a Russian group of the policy (T15: its keys go to upstream-ru.txt), else "abroad"."""
    if group in (None, "") or not state.exists():
        return "abroad"
    from . import policy_ops
    g = policy.group(policy_ops._base(state.load()), group)
    return "ru" if g is not None and g.get("role") == "ru" else "abroad"


def _hints(ids, gid, keep=None):
    """Step 1 (§3.1 «Записи через скрипт пула»): the hint ``exit_pref.group`` of the keys about to come — written before
    the script in a short transaction of its own; ``keep`` — the ids that came: the hints of the others go. No ``gid``
    (a key without a group): a hint of an earlier add of the same key left by a panel stopped during its script goes —
    the key never lands in that old group (проверка T7, П-3)."""
    if not ids:
        return
    if not gid:
        if keep is not None or not os.path.exists(paths.state()):
            return
        ex = model.peek(state.load())["exits"]
        if not any(isinstance(ex.get(i), dict) and ex[i].get("group") for i in ids):
            return
        with state.transaction() as st:
            for i in ids:
                p = model.peek(st)["exits"].get(i)
                if isinstance(p, dict) and p.pop("group", None) is not None and not p:
                    model.peek(st)["exits"].pop(i, None)
        return
    with state.transaction() as st:
        for i in ids:
            p = model.peek(st)["exits"].get(i)
            if keep is None:
                model.set_exit(st, i, group=gid)
            elif i not in keep and isinstance(p, dict) and p.get("group") == gid:
                p.pop("group", None)
                if not p:
                    model.peek(st)["exits"].pop(i, None)


def _after_pool(what, new_ids=(), gid=None):
    """Step 3 of a write through the script of the pool (§3.1): ``policy_ops.after_pool``. -> rev | None."""
    from . import policy_ops
    return policy_ops.after_pool(what, new_ids, gid)


def _pin_tofu(runner, text, pin, by):
    """«Закрепить» of the preview (§9.1, T7): the keys of ``text`` whose certificate ``tofu.check`` asks about with the
    fingerprint ``pin`` are pinned (``tofu.consent`` takes it again and compares) before the script adds them; none of
    them has it — KaskadError ``bad_pin`` (the certificate changed between the look and the consent)."""
    from . import tofu
    parser = exits.load_parser(runner)
    want = str(pin or "").strip().lower().replace(":", "")
    done = 0
    for line in [ln.strip() for ln in text.splitlines() if ln.strip()]:
        chk = tofu.check(parser, line, by, runner)
        if (chk or {}).get("status") == "ask" and chk.get("sha256") == want:
            tofu.consent(parser, line, want, by, runner)
            done += 1
    if not done:
        raise KaskadError(tr("gw_tofu.changed"), code=CONFLICT, data={"code": "bad_pin"})
    return done


def exit_add(runner, text, group=None, pin_tofu=None, by="panel"):
    """A key into the pool — one vless://, vpn://, the whole text of an AmneziaWG or WireGuard .conf (1.0, 1.5, 2.0,
    3.1), several keys, a file's text or a subscription link: ``gokaskad --egress-add`` with the text on stdin.
    Returns {"ok", "added": [{"id", "name"}], "results": [per key: added | in_pool | not_added], "warnings", "text",
    "rev"}. Nothing new (the keys are in the pool) — ConflictError; a failure — KaskadError with the script's own words.
    The key is never logged or returned.

    Stage 1 (T7, §3.1 «Записи через скрипт пула»): ``group`` — the hint ``exit_pref.group`` of the keys the preview
    sees, written BEFORE the script (step 1); the script runs WITHOUT the state lock (its ``gw apply`` takes it); after
    it the new lines are placed and ``rev`` counted (step 3; ``rev`` None — not counted now).

    T15 Q4 (SPEC-groups §2.4, §7): ``group`` of a Russian group — the keys go to upstream-ru.txt and only there
    (``_exit_add_ru``: the hint before the line, the bot and its proxy untouched)."""
    if _group_role(group) == "ru":
        return _exit_add_ru(runner, text, _hint_group(group, "ru"), pin_tofu, by)
    gid = _hint_group(group)
    text = key_text(text)
    if pin_tofu:
        _pin_tofu(runner, text, pin_tofu, by)
    found = _found(runner, text)
    before = set(i for i, _ in exits.line_ids())
    coming = [x["id"] for x in found or [] if x.get("id") and x["id"] not in before]
    _hints(coming, gid)
    try:
        res = runner.run([exits._script(runner), "--egress-add"], input=text if text.endswith("\n") else text + "\n",
                         timeout=EXIT_ADD_TIMEOUT, env=script_env())
    except Exception:
        _hints(coming, gid, keep=set(i for i, _ in exits.line_ids()))
        raise
    text = None
    if res.rc != 0:
        _hints(coming, gid, keep=set(i for i, _ in exits.line_ids()))
    lines = _script_lines(res)
    full = _pool_full(lines)
    in_ru = sum(int(a[0]) for _i, c, a in _marks(lines) if c == "egress_in_ru" and a and a[0].isdigit())
    if res.rc == RC_ADD_DUP and in_ru:                  # REV-s1-q4 З3: the keys (a subscription's too) are Russian ones
        raise ConflictError(tr("gw_ops.err.keys_in_other_role", n=in_ru),
                            data={"code": "key_in_other_role", "role": "ru", "n": in_ru})
    if res.rc == RC_ADD_DUP:
        many = found is not None and len(found) > 1
        peer = [x for x in found or [] if x.get("same_text")]
        # REV-335-2 Н3: «only one connection works» — only for the same key at another address, not for another form
        if many:
            why = tr("gw_ops.exit.all_dup")
        elif peer:
            at = peer[0].get("same_how") in exits.SAME_AT
            why = tr("gw_ops.exit.dup_peer_at" if at else "gw_ops.exit.dup_peer", why=peer[0]["same_text"])
        else:
            why = tr("gw_ops.exit.dup_key")
        raise ConflictError(why,
                            data={"results": _results(found, before, before, False)} if many else None)
    if res.rc == RC_ADD_FULL:
        raise ConflictError(_script_error(lines, tr("gw_ops.exit.full")) + tr("gw_ops.exit.full_hint"))
    if res.rc == RC_POOL_BUSY:
        raise LockedError(tr("gw_ops.exit.pool_busy"))
    if res.rc == RC_TIMEOUT:
        raise KaskadError(tr("gw_ops.exit.add_timeout", minutes=EXIT_ADD_TIMEOUT // 60))
    if res.rc != 0:
        why = _script_error(lines, tr("gw_ops.exit.not_added", rc=res.rc))
        data = {"needs_key": True, "key": "none"} if _needs_github_key(lines) else None
        free = suggest_base(runner) if _gw_net_marked(lines) else None
        if free:                                        # F6 Е3: the key's Address is in the network of this gateway
            why = "%s %s" % (why, tr("gw_net.base.suggest", base=free))
            data = dict(data or {}, suggest_base=free)
        raise KaskadError(why, data=data)
    after = set(i for i, _ in exits.line_ids())
    new = [i for i, _ in exits.line_ids() if i not in before]
    names = {}
    try:
        st = state.load()
        names = dict((e["id"], exits.label(st, e)) for e in exits.pool(runner) if e["id"] in new)
    except KaskadError:
        pass
    added = [{"id": i, "name": names.get(i) or i} for i in new]
    util.log_action("GW exit add: %s" % (", ".join(new) or "—"))
    mine = set(idx for i, idx in exits.line_ids() if i in new)       # 3.3.5: the warnings of these keys, not the pool's
    if coming:
        _hints([i for i in coming if i not in new], gid, keep=set(new))
    rv = _after_pool("exit add", new, gid)
    warns = _script_warnings(lines, mine)
    if in_ru:
        warns.append(tr("gw_ops.err.keys_in_other_role", n=in_ru))
    out = {"ok": True, "added": added, "results": _results(found, before, after, full),
           "warnings": warns, "notes": _script_notes(lines), "rev": rv,
           "text": tr("gw_ops.exit.added") if len(added) <= 1 else tr("gw_ops.exit.added_n", n=len(added))}
    free = suggest_base(runner) if _gw_net_marked(lines) else None
    if free:                                            # F6 Е3: a key skipped — its Address in this gateway's network
        out["suggest_base"] = free
    return out


def _exit_add_ru(runner, text, gid, pin_tofu=None, by="panel"):
    """``exit_add`` into a Russian group (T15 Q4; SPEC-groups §2.4, §7): the keys of ``text`` (the parser's own lines,
    as the script would write them; a key with a problem — not added, said) go to the end of upstream-ru.txt —
    ``gokaskad --egress-ru-apply <hash>``: its legs and gw apply, never the bot nor its proxy. The hint
    ``exit_pref.group`` is written BEFORE the line (step 1, as ``exit_add``), the line placed after (step 3). A key the
    foreign file holds — ConflictError ``key_in_other_role``; a key upstream-ru.txt has — «in_pool»; more than RU_MAX —
    ConflictError. A subscription — USAGE (it is added under «Подписки» with its group). -> as ``exit_add``."""
    text = key_text(text)
    if exits.is_subscription(text):
        raise KaskadError(tr("gw_ops.exit.ru_no_sub"), code=USAGE)
    if pin_tofu:
        _pin_tofu(runner, text, pin_tofu, by)
    parser = exits.load_parser(runner)
    _other_role_refuse(parser, text, "ru")
    cur = exits.pool_lines("ru")
    before = set(i for i, _ in exits.line_ids(cur, "ru"))
    found = exits.preview(parser, text, before, _pool_peers(parser, "ru"))
    with exits._quiet():
        report = getattr(parser, "extract_report", None)
        lines = list(report(text)[0] or []) if callable(report) else list(parser.extract(text) or [])
    ok_ids = set(x["id"] for x in found if not x.get("problem") and not x.get("in_pool"))
    new, seen, broken = [], set(), 0
    for line in lines:
        i = exits.pool_id(line)
        if len(str(line).splitlines()) != 1:    # SEC П-3: U+2028, \x85, \v … inside — two lines for the gateway, not one
            broken += 1
            continue
        if i in ok_ids and i not in seen:
            seen.add(i)
            new.append((i, line))
    if not new:
        why = tr("gw_ops.exit.ru_none") if any(x.get("in_pool") for x in found) else (
            tr("gw_ops.exit.line_break") if broken else tr("gw_ops.exit.not_added", rc=1))   # REV-s1-q4-2 П-2
        raise ConflictError(why, data={"results": _results(found, before, before, False)})
    if len(cur) + len(new) > exits.RU_MAX:
        raise ConflictError(tr("gw_ops.exit.ru_full", max=exits.RU_MAX))
    coming = [i for i, _ in new]
    _hints(coming, gid)
    rc = exitsubs.RC_POOL_CHANGED
    try:
        for _ in range(exitsubs.REPLACE_TRIES):
            cur = exits.pool_lines("ru")
            want = cur + [l for _, l in new if l not in cur]
            if len(want) > exits.RU_MAX:
                raise ConflictError(tr("gw_ops.exit.ru_full", max=exits.RU_MAX))
            rc, warns, notes = exitsubs.write_pool(runner, "ru", cur, want, set())
            if rc == 0:
                break
    except Exception:
        _hints(coming, gid, keep=set(i for i, _ in exits.line_ids(role="ru")))
        raise
    if rc != 0:
        _hints(coming, gid, keep=set(i for i, _ in exits.line_ids(role="ru")))
        raise LockedError(tr("gw_exitsubs.pool_churn"))
    after_ids = [i for i, _ in exits.line_ids(role="ru")]
    added_ids = [i for i in after_ids if i not in before]
    _hints([i for i in coming if i not in added_ids], gid, keep=set(added_ids))
    names = {}
    try:
        st = state.load()
        names = dict((e["id"], exits.label(st, e)) for e in exits.pool(runner, roles=("ru",)) if e["id"] in added_ids)
    except KaskadError:
        pass
    util.log_action("GW exit add (ru): %s" % (", ".join(added_ids) or "—"))
    rv = _after_pool("exit add", added_ids, gid)
    added = [{"id": i, "name": names.get(i) or i} for i in added_ids]
    return {"ok": True, "added": added, "results": _results(found, before, set(after_ids), False), "role": "ru",
            "warnings": warns, "notes": notes, "rev": rv,
            "text": tr("gw_ops.exit.ru_added") if len(added) <= 1 else tr("gw_ops.exit.ru_added_n", n=len(added))}


def _exit_del_ru(runner, eid, moves):
    """``exit_del`` of a key of upstream-ru.txt (T15 Q4): the line out by ``--egress-ru-apply`` (its legs, gw apply; the
    bot untouched); a node with subscriptions — the id of its text goes to subs-excluded.pending, as install.sh
    ``egress_pool_drop`` does for upstream.txt (SE §3.8: a subscription never brings back a key removed by hand)."""
    for _ in range(exitsubs.REPLACE_TRIES):
        cur = exits.pool_lines("ru")
        hit = [n for i, n, r in exits.all_line_ids() if i == eid and r == "ru"]
        if not hit:
            from . import policy_ops
            raise policy_ops.gone("exit", eid)
        pos = hit[0] - exits.RU_INDEX_BASE
        line = cur[pos - 1]
        rc, warns, notes = exitsubs.write_pool(runner, "ru", cur, cur[:pos - 1] + cur[pos:], set())
        if rc != 0:
            continue
        if os.path.exists(exitsubs.path()):
            try:
                with open(exitsubs.pending_path(), "a", encoding="utf-8") as f:
                    f.write(exits.exit_id(line) + "\n")
                os.chmod(exitsubs.pending_path(), 0o600)
            except OSError:
                pass
        line = None
        util.log_action("GW exit del (ru): %s" % eid)
        return {"ok": True, "id": eid, "left": len(exits.pool_lines("ru")), "warnings": warns, "notes": notes,
                "moves": moves, "rev": _after_pool("exit del"), "role": "ru"}
    raise ConflictError(tr("gw_ops.exit.pool_changed"))


def exit_del(runner, eid, want_rev=None, dry=False):
    """The key of an exit out of the pool (a repeated key ``e-<hex>-<line>`` too): ``gokaskad --egress-del <line>
    <hex>`` — that line only while it still holds that key. The last key — «Прокси для API Telegram» switches off
    (the bot goes to Telegram directly). Returns {"ok", "id", "left": keys left, "warnings", "rev", "moves"}. No such
    exit — KaskadError (``policy_ops.Refused`` «gone», code CONFLICT, data {"gone": true}); the pool changed meanwhile
    — ConflictError.

    Stage 1 (T7, §3.1 «Записи через скрипт пула», проверка 19, С-1): step 1 — a short ``state.transaction``: gone and
    ``want_rev`` in the order of §7.9 (an id of its own — gone first; ``e-…-N`` — ``rev`` first), the ``moves``; ``dry``
    stops here (``{ok, dry, rev, moves, warnings}``); step 2 — the script WITHOUT the state lock; step 3 — ``rev.note``
    «по возможности»."""
    from . import policy_ops
    if not re.match(r"^e-[0-9a-f]{10}(-[0-9]{1,4})?$", str(eid)):
        raise policy_ops.gone("exit", eid)
    cur, moves = None, None
    if state.exists():                                  # no state.json (a pool of the bot only): no rev, no moves
        with state.transaction() as st0:
            cur = policy_ops.find_exit(st0, eid, want_rev)
            gone_st = copy.deepcopy(st0)
            if eid.count("-") != 2:                     # a repeat of a key carries nobody: no moves
                model.set_exit(gone_st, eid, enabled=False)
            moves = policy_ops.impact(st0, gone_st) if dry else policy_ops.safe_impact(st0, gone_st)
    if dry:
        return {"ok": True, "dry": True, "id": eid, "rev": cur, "moves": moves, "warnings": []}
    if role_of_exit(eid) == "ru":                       # T15 Q4: a key of upstream-ru.txt — its own file, no bot
        return _exit_del_ru(runner, eid, moves)
    pool_now = exits.pool_lines()
    hit = [idx for i, idx in exits.line_ids(pool_now) if i == eid]
    if not hit:
        raise policy_ops.gone("exit", eid)
    had = len(pool_now)
    _me, twin = exits.twin_of(pool_now, hit[0])     # one key twice: its routes go to the other copy (REV-335-2)
    # install.sh knows a line by its text (sha1): a key a subscription renamed keeps another id (3.3.5, exits.pool_id)
    text_id = exits.exit_id(pool_now[hit[0] - 1])
    res = runner.run([exits._script(runner), "--egress-del", str(hit[0]), text_id[2:12]], timeout=EXIT_DEL_TIMEOUT,
                     env=script_env())
    lines = _script_lines(res)
    if res.rc == RC_POOL_CHANGED:
        raise ConflictError(tr("gw_ops.exit.pool_changed"))
    if res.rc == RC_POOL_BUSY:
        raise LockedError(tr("gw_ops.exit.pool_busy"))
    if res.rc == RC_HANDOVER:                       # the routes did not move to the other copy: the line stays (REV-335-3)
        data = {"exit": eid, "twin": twin}
        fails = [a[:1] for _i, c, a in _marks(lines) if c == "egress_fail"]
        if twin and ["handover_unfit"] in fails:
            raise ConflictError(tr("gw_ops.exit.twin_unfit", name=_exit_name(twin)), data=data)
        if ["handover_broken"] in fails:            # not the lock: the core could not move them — no «повторите»
            raise KaskadError(_script_error(lines, tr("gw_ops.script_fail.handover_broken")), data=data)
        raise LockedError(_script_error(lines, tr("gw_ops.script_fail.handover")), data=data)
    if res.rc != 0:
        why = _script_error(lines, "")
        if len(exits.pool_lines()) < had:          # the line is gone, the rebuild after it failed: say both
            util.log_tr("gw_ops.log.exit_del_failed", id=eid)
            raise KaskadError(tr("gw_ops.exit.del_rebuild_failed",
                                 why=why.rstrip(".") if why else tr("gw_ops.exit.rc", rc=res.rc)),
                              data={"removed": True, "exit": eid})
        raise KaskadError(tr("gw_ops.exit.not_deleted", why=why) if why else
                          tr("gw_ops.exit.not_deleted_rc", rc=res.rc))
    left = len(exits.pool_lines())
    util.log_action("GW exit del: %s" % eid)
    out = {"ok": True, "id": eid, "left": left, "warnings": _script_warnings(lines, set()),    # the others: one line
           "notes": _script_notes(lines), "moves": moves, "rev": _after_pool("exit del")}
    if twin:                    # what install.sh moved (egress_handover: the mark egress_handover:<n>), not a count before
        moved = [int(a[0]) for _i, c, a in _marks(lines) if c == "egress_handover" and a[:1] and a[0].isdigit()]
        out["twin"] = twin
        out["moved"] = moved[-1] if moved else 0
        if out["moved"]:
            out["text"] = tr("gw_ops.exit.twin_done", name=_exit_name(twin))
    return out


# ── a change of role: the line of an exit between upstream.txt and upstream-ru.txt (T15 Q4; SPEC-groups §2.2) ────────

def role_of_exit(eid):
    """"abroad" | "ru" — the file that holds the line of exit ``eid`` now (no parser); None — in neither."""
    for i, _n, role in exits.all_line_ids():
        if i == eid:
            return role
    return None


ROLE_UNDO_WAIT = 1.0                    # s between the tries of an undo of a move of role when the pool is busy


def _pool_edit(runner, role, edit):
    """One write of the file of ``role`` for an undo: ``edit(cur)`` -> the new lines (None — nothing to do); a changed
    file (rc 3) or a busy pool — tried again (REPLACE_TRIES), every code checked. -> True — done, False — not."""
    from . import exitsubs
    for i in range(exitsubs.REPLACE_TRIES):
        cur = exits.pool_lines(role)
        new = edit(cur)
        if new is None:
            return True
        try:
            rc, _w, _n = exitsubs.write_pool(runner, role, cur, new, set())
        except KaskadError:
            rc = None
        if rc == 0:
            return True
        if i + 1 < exitsubs.REPLACE_TRIES:
            time.sleep(ROLE_UNDO_WAIT)
    return False


def _drop_last(cur, line):
    """``cur`` without its LAST copy of ``line`` (the one a move appended); None — no copy."""
    if line not in cur:
        return None
    k = len(cur) - 1 - cur[::-1].index(line)
    return cur[:k] + cur[k + 1:]


def _role_back(runner, eid, src, dst, line, pos, added):
    """The line back into ``src`` at ``pos`` FIRST (never in neither file), then out of ``dst`` — only the copy the move
    added (``added``; a copy that was there before is not touched). -> None — back as it was; else where the key is
    now ("both", "only_abroad", "only_ru")."""
    ok = _pool_edit(runner, src, lambda cur: None if line in cur else cur[:pos - 1] + [line] + cur[pos - 1:])
    if ok and added:
        _pool_edit(runner, dst, lambda cur: _drop_last(cur, line))
    in_s, in_d = line in exits.pool_lines(src), line in exits.pool_lines(dst)
    if in_s and not (added and in_d):
        util.log_action("GW exit role: %s %s -> %s undone" % (eid, src, dst))
        return None
    where = "both" if in_s and in_d else ("only_" + (src if in_s else dst))
    util.log_action("GW exit role: %s %s -> %s broke off, the key is %s" % (eid, src, dst, where))
    return where


def _role_move_undo(runner, eid, src, dst, line, pos, added, msg):
    """A move of role broke off (SEC-s1-q4 З-2, REV-s1-q4 С2): ``_role_back``. Back as it was — ``msg``; else an honest
    error: where the key is now (both files / one), «try again». Always raises."""
    where = _role_back(runner, eid, src, dst, line, pos, added)
    if where is None:
        raise KaskadError(msg)
    raise KaskadError(tr("gw_ops.exit.role_move_stuck", id=eid, where=tr("gw_ops.where." + where)))


def exit_role_back(runner, moved):
    """The policy of a move of role failed after the move (REV-s1-q4-2 З-1): the line back into its file AT ITS PLACE
    (``moved["back"]`` of ``exit_role_move``) — not at the end, where the lines below it would shift (and for
    upstream-ru.txt close the legs of other exits) — then out of the other file (only the copy the move added). Raises
    KaskadError (where the key is) when it is not back as it was."""
    b = moved["back"]
    where = _role_back(runner, moved["id"], moved["from"], moved["to"], b["line"], b["pos"], b["added"])
    if where is not None:
        raise KaskadError(tr("gw_ops.exit.role_move_stuck", id=moved["id"], where=tr("gw_ops.where." + where)))


def exit_role_move(runner, eid, to_role):
    """The line of exit ``eid`` into the file of ``to_role`` AS IT IS (its text, so its ``exit_id`` and ``pool_id`` —
    the id of the exit stays: SE §3.8 С2): first written into the other file, then out of its own (the id never leaves
    the pool between the two); each write by its own script (``exitsubs.write_pool``: upstream.txt — the bot's proxy
    rebuilt, upstream-ru.txt — its legs; each — gw apply), under no state lock (the scripts take it). The map ``ids``
    is not touched (the text is the same). Atomic (REV-s1-q4 С2, SEC-s1-q4 З-2): the second write failed, or the id
    did not survive — the line back into its file first, then out of the other (only the copy this move added), every
    code checked; the error says where the key is. -> {"from", "to", "id", "back"}; nothing to move — None. ``back``
    ({"line", "pos", "added"}: the line is a secret — never into an answer or the log) is for ``exit_role_back``."""
    from . import exitsubs, policy_ops
    src = role_of_exit(eid)
    if src is None:
        raise policy_ops.gone("exit", eid)
    if src == to_role:
        return None
    room = exits.RU_MAX if to_role == "ru" else exitsubs.POOL_MAX
    for _ in range(exitsubs.REPLACE_TRIES):
        s_lines, d_lines = exits.pool_lines(src), exits.pool_lines(to_role)
        rows = exits.all_line_ids()
        hit = [n for i, n, r in rows if i == eid and r == src]
        if not hit:
            if any(i == eid and r == to_role for i, _n, r in rows):
                return None                             # moved meanwhile
            raise policy_ops.gone("exit", eid)
        pos = hit[0] - exits.index_base(src)
        line = s_lines[pos - 1]
        added = line not in d_lines                     # a copy there before (SSH menu): not ours, never taken out
        if added:
            if len(d_lines) >= room:
                raise ConflictError(tr("gw_exitsubs.pool_full", max=room))
            try:
                rc, _w, _n = exitsubs.write_pool(runner, to_role, d_lines, d_lines + [line], set())
            except KaskadError as e:
                if line in exits.pool_lines(to_role):   # written, then the script failed: the copy goes
                    _role_move_undo(runner, eid, src, to_role, line, pos, True,
                                    tr("gw_ops.exit.role_move_failed", id=eid, err=str(e)))
                raise
            if rc == exitsubs.RC_POOL_CHANGED:
                continue
        try:
            rc, _w, _n = exitsubs.write_pool(runner, src, s_lines, s_lines[:pos - 1] + s_lines[pos:], set())
        except KaskadError as e:
            _role_move_undo(runner, eid, src, to_role, line, pos, added,
                            tr("gw_ops.exit.role_move_failed", id=eid, err=str(e)))
        if rc == exitsubs.RC_POOL_CHANGED:
            if added and not _pool_edit(runner, to_role, lambda cur: _drop_last(cur, line)):
                _role_move_undo(runner, eid, src, to_role, line, pos, added,
                                tr("gw_ops.exit.role_move_failed", id=eid, err=tr("gw_exitsubs.pool_churn")))
            continue
        after = [i for i, _n, r in exits.all_line_ids() if r == to_role]
        if eid not in after:                            # С2: the id did not survive — the line goes back
            _role_move_undo(runner, eid, src, to_role, line, pos, added, tr("gw_ops.exit.role_id_changed", id=eid))
        util.log_action("GW exit role: %s %s -> %s" % (eid, src, to_role))
        return {"from": src, "to": to_role, "id": eid, "back": {"line": line, "pos": pos, "added": added}}
    raise LockedError(tr("gw_exitsubs.pool_churn"))


def role_reset(st, eid):
    """``role_reset_state`` + ``role_reset_files`` at once."""
    return role_reset_state(st, eid) + role_reset_files(eid)


def role_reset_state(st, eid):
    """The part of ``role_reset`` in the state (the caller's transaction): the expected country."""
    p = model.peek(st)["exits"].get(eid)
    if isinstance(p, dict) and p.get("country"):
        model.set_exit(st, eid, country=None)
        return ["country"]
    return []


def role_reset_files(eid):
    """The part of ``role_reset`` on disk — written at once, outside any transaction, so only after the state of the
    move is saved (REV-s1-q4-2 З-1: a refused move never loses the pins or the record of the snapshot).

    What a change of role resets (SPEC-groups §2.2): the state, the reasons,
    the windows, ``off_until`` and ``norm_rtt`` of the exit (its record of the snapshot but its ``quota`` — the collector
    starts it anew under its new leg and tag), the expected country, its pins in the old group and the last reading of its counters
    (``traffic.json → last``: new ``kawgN`` and tag, П3). The quota and its counters stay — the tariff of the same
    server. -> [what was reset]."""
    from . import pins as PN
    done = []
    snap = exits.load_snapshot()
    if eid in (snap.get("exits") or {}):
        rec = snap["exits"].pop(eid, None)
        q = rec.get("quota") if isinstance(rec, dict) else None
        if isinstance(q, dict) and q:                   # REV-s1-q4 П1: the quota of the record stays (the owner's «Вернуть»)
            snap["exits"][eid] = {"quota": q}
        exits.save_snapshot(snap, state=True)
        done.append("state")
    try:
        raw = PN._read_json(paths.pins_json())
        doc = PN.normalize(raw) if raw is not None else None
    except (PN.BadFile, OSError, ValueError):
        doc = None
    if doc is not None:
        n = 0
        for g in (doc.get("groups") or {}).values():
            pins = g.get("pins") or {}
            for did in [d for d, x in pins.items() if isinstance(x, dict) and x.get("exit") == eid]:
                del pins[did]
                n += 1
        if n:
            doc["saved"] = int(time.time())
            PN._write(paths.pins_json(), doc)
            done.append("pins")
    try:
        traffic = PN.traffic_read()
        if eid in (traffic.get("exits") or {}):
            PN.traffic_new_leg(traffic, eid)
            traffic["version"] = PN.VERSION
            PN._write(paths.traffic_json(), traffic)
            done.append("traffic_last")
    except (OSError, ValueError):
        pass
    return done


# ── one key twice in the pool (by IP and by domain, a pool of 3.3.4): the copy that goes gives its routes to the one
# that stays (REV-335-2, the coordinator 15.10). One place for every removal: install.sh egress_remove_key_now (the
# menu, the panel, the wizard, the CLI, MCP — all come there) runs ``gw exit-handover`` right before the line leaves the
# pool, under the lock of the pool; its rebuild (gw apply) applies the routes with the pool. The handover failed, or the
# other copy may not take them (off for the gateway, a problem in the pool) — the line stays, the removal is refused
# (REV-335-3). A subscription that drops one copy — exitsubs._apply, after the new pool is in place.

def _routes_to(st, eid):
    """The objects whose own route goes to exit ``eid``: ("default"|"user"|"device", the object)."""
    via = "exit:" + eid
    g = model.peek(st)
    if (g["routes"].get("default") or {}).get("via") == via:
        yield "default", g["routes"]["default"]
    for kind, objs in (("user", g["users"]), ("device", g["devices"])):
        for o in objs:
            if isinstance(o, dict) and isinstance(o.get("route"), dict) and o["route"].get("via") == via:
                yield kind, o


def _exit_name(eid, st=None):
    """«Аня IP» — the name of an exit of the pool for people (``exits.label``), the id when it is not there."""
    try:
        st = state.load() if st is None else st
        for e in exits.pool(None):
            if e["id"] == eid:
                return exits.label(st, e)
    except KaskadError:
        pass
    return eid


def twin_unfit(st, eid, rows=None):
    """Why the routes may not go to exit ``eid`` of the pool (REV-335-3): "off" — off for the gateway, "broken" — not
    in the pool or a problem there (too far in the list, a key 3.x the core cannot keep…); None — it may take them.
    The pool unknown (no parser) — None: nothing to judge by."""
    if rows is None:
        try:
            rows = exits.pool(None)
        except KaskadError:
            return None
    e = next((x for x in rows if x.get("id") == eid), None)
    if e is None or not exits.parsed(e):
        return "broken"
    return None if model.exit_pref(st, eid)["enabled"] else "off"


def hand_over(moves, runner=None, unfit=None):
    """{id of the copy that goes: id of the copy that stays} -> the number of routes moved: the routes ``exit:<goes>``
    (the default, people, devices; the fail policy kept) -> ``exit:<stays>``. A copy that may not take them
    (``twin_unfit``) gets none: its id goes to the list ``unfit``. Without ``runner`` — saved, not applied (install.sh:
    the rebuild of the pool right after applies them); with it — saved and applied (a subscription, after its new pool is
    in place). No gateway, nothing to move — 0."""
    moves = dict((a, b) for a, b in (moves or {}).items() if a and b and a != b)
    if not moves or not os.path.exists(paths.state()):
        return 0
    rows = [None]

    def move(st, _sec=None):
        n = 0
        for a, b in moves.items():
            objs = list(_routes_to(st, a))
            if not objs:
                continue
            if rows[0] is None:
                try:
                    rows[0] = exits.pool(runner)
                except KaskadError:
                    rows[0] = []
            if rows[0] and twin_unfit(st, b, rows[0]):
                if unfit is not None and b not in unfit:
                    unfit.append(b)
                continue
            for kind, obj in objs:
                r = obj if kind == "default" else obj["route"]
                r["via"] = "exit:" + b
                n += 1
        return n

    if runner is None:
        with state.locked(), gsys.hold():
            st = state.load()
            if not isinstance(st.get("gateway"), dict):
                return 0
            n = move(st)
            if n:
                state.save(st)                          # ⚠РФ: routes only — no rfgate.check
            _pins_follow(st, moves)                    # T7 (R-s1-t3): the pins go to the other copy too
    else:
        st = state.load()
        if not isinstance(st.get("gateway"), dict):
            return 0
        n = 0
        if any(any(True for _ in _routes_to(st, a)) for a in moves):
            n, _res = _change(runner, move)
        with state.locked():                            # no route to a copy — its pins still go (проверка T7, З-2)
            _pins_follow(state.load(), moves)          # T7 (R-s1-t3): the pins go to the other copy too
    if n:
        util.log_action("GW exit handover: %s (%d)" % (", ".join("%s -> %s" % m for m in sorted(moves.items())), n))
    return n


def _twin_failed(e):
    """Why the routes of a copy did not move, as the error of ``exit_handover``: the state lock busy — LockedError «…,
    повторите» (code 6: a retry helps); anything else (a broken state.json…) — its cause, code 1, no «повторите»."""
    if isinstance(e, LockedError):
        return LockedError(tr("gw_ops.exit.twin_failed"))
    return KaskadError(tr("gw_ops.exit.twin_broken", error=str(e).strip().rstrip(".") or type(e).__name__))


def _pins_follow(st, moves):
    """The pins of the copies that go -> the copies that stay (``pins.handover_file``, under the state lock the caller
    holds: the collector writes pins.json under it too; R-s1-t3). A pin moves when the copy that stays is in the same
    group; else it goes and the collector picks anew. A failure is no failure of the handover."""
    try:
        from . import pins as PN
        pol = policy.load(st, exits.pool_fallback())

        def where(eid):
            g, i = policy.tier_of(pol, eid)
            return (g["id"], g["tiers"][i].get("id")) if g is not None else None
        return PN.handover_file(moves, where)
    except Exception as e:                              # noqa: BLE001 — the routes moved; the collector re-pins
        util.log_action("GW exit handover: pins not moved (%s)" % type(e).__name__)
        return 0


def _tg_rebuild(runner):
    """The bot's proxy rebuilt by its group (SE §2.9; ``policy_ops.tg_rebuild``): no pool — nothing; never a failure."""
    try:
        from . import policy_ops
        return policy_ops.tg_rebuild(runner)
    except Exception:                                   # noqa: BLE001
        return None


def exit_handover(n, hexid="", dry=False):
    """``gw exit-handover <N> [<hex10>]`` (install.sh, before line N leaves the pool): its routes go to the other copy
    of the same key. ``dry`` — only say what would be done (the question of the menu). -> {"id", "twin" (None — no
    copy), "routes": how many go (moved), "unfit"?, "text"}; the line is not N any more (hex), no copy or no route of
    it — nothing done, nothing said. Not moved (REV-335-3: install.sh then keeps the line and says so): the other copy
    may not take them (``twin_unfit``) — ConflictError; the state lock busy — LockedError («повторите»); the state not
    readable or another failure of the core — KaskadError with its cause and no «повторите» (a retry will not help,
    REV-335-4 №2)."""
    lines = exits.pool_lines()
    me, twin = exits.twin_of(lines, n)
    if me is None or (hexid and exits.exit_id(lines[n - 1])[2:12] != hexid):
        return {"id": None, "twin": None, "routes": 0, "text": ""}
    out = {"id": me, "twin": twin, "routes": 0, "text": ""}
    if not twin:
        return out
    try:
        st = state.load()
        out["routes"] = sum(1 for _ in _routes_to(st, me))
    except KaskadError as e:
        if dry:
            return out
        raise _twin_failed(e)
    if not out["routes"]:
        return out                                  # nothing goes anywhere: nothing to say (REV-335-3 №8)
    name = _exit_name(twin, st)
    if twin_unfit(st, twin):
        out["unfit"] = True
        out["text"] = tr("gw_ops.exit.twin_unfit_ask" if dry else "gw_ops.exit.twin_unfit", name=name)
        if dry:
            return out
        raise ConflictError(out["text"], data=dict(out, text=""))
    if dry:
        out["text"] = tr("gw_ops.exit.twin_ask", name=name)
        return out
    unfit = []
    try:
        out["routes"] = hand_over({me: twin}, unfit=unfit)
    except KaskadError as e:
        raise _twin_failed(e)
    if unfit:                                       # it became unfit meanwhile
        raise ConflictError(tr("gw_ops.exit.twin_unfit", name=name))
    if out["routes"]:
        out["text"] = tr("gw_ops.exit.twin_done", name=name)
    return out


# ── status, sessions, the watchdog ─────────────────────────────────────────

NO_EXITS_FRESH = 60                 # s: the age of live.json k3_gw_dead (menu 17) accepts


def no_exits_problem(st, rows, live_age=None):
    """The gateway is on, its default route goes through the exits (the pool, a group or one exit) and resolves to a
    group whose fail policy is «block», the collector is alive (its data fresh) and not one exit of that group works —
    the devices of the default route have no internet. A problem for ``gw status`` and ``gw check`` (critic T-M6, F3);
    nothing to repair automatically. ``rows`` — exits_view rows. The red line of menu 17 (install.sh k3_gw_dead) still
    counts the exits of the pool alone — T10 brings it to this rule (REV-s1-t45 П3)."""
    if not model.enabled(st):
        return None
    r = exits.default_route(st)
    if not exits.via_exits(r) or r.get("fail") == "direct":
        return None
    if live_age is None:
        _doc, live_age = live.read()
    if live_age is None or live_age > NO_EXITS_FRESH:
        return None                                 # nobody measured: unknown is not «dead»
    # stage 1 (§2.2, REV-s1-t4 С1): the route resolves to a group — «pool» to «Основная», «group:G» to G, «exit:E» to
    # the group of E (E unfit: the other exits of that group carry it) — and the devices are without internet only
    # when none of that group's exits works and its fail policy is «block»
    from . import policy as P
    try:
        pool = [{"id": x["id"], "role": x.get("role") or "abroad"} for x in rows or [] if x.get("id")]
        _gid, grp, _missing = exits.route_group(st, r, {"policy": P.load(st, pool)})
    except (KaskadError, OSError, ValueError, TypeError):
        grp = None
    if grp is not None:
        if grp.get("fail") == "direct":
            return None
        ids = set(P.exits_of(grp))
        mine = [x for x in rows or [] if x.get("id") in ids]
    else:
        via = r.get("via")
        mine = [x for x in rows or [] if via == "pool" or via == "exit:%s" % x.get("id")]
    if any(x.get("usable") for x in mine):
        return None
    return {"kind": "no_exits", "detail": tr("gw_ops.no_exits"), "repaired": False}

def sessions(runner):
    st = state.load()
    out = collect.read_sessions()
    if not out and model.enabled(st):
        out = collect.sessions(st, runner)
    names = dict((d["id"], d.get("name")) for d in model.devices(st))
    for s in out:
        s["name"] = names.get(s.get("device"), "")
    return out


def status(runner):
    st = state.load()
    g = model.peek(st)
    doc = {"ok": True, "enabled": bool(g["enabled"]), "engine": st.get("engine"),
           "net": {"base": str(addr.base(st)), "dns": addr.dns_ip(st)}, "host": model.public_host(st),
           "users": len(g["users"]), "devices": len(g["devices"]),
           "inbounds": inbound_list(runner, st, with_status=bool(g["enabled"]))}
    try:
        doc["exits"] = exits_view(runner, st)
    except KaskadError as e:
        doc["exits"] = []
        doc["exits_error"] = str(e)
    doc["dns"] = dns.status(runner)
    doc["xray"] = xray.status(runner)
    doc["collect"] = {"active": gsys.unit_active(runner, collect.UNIT)}
    try:                                                   # wave 3: rules «Россия напрямую»
        from ..rules import ops as rules_ops
        doc["rules"] = rules_ops.status(st)
        doc["rules"]["line"] = rules_ops.summary(doc["rules"])
    except (KaskadError, OSError, ValueError) as e:
        doc["rules"] = {"ok": False, "error": str(e), "line": tr("gw_ops.rules_unread", err=e)}
    doc["problems"] = check(st, runner, repair=False) if g["enabled"] else []
    dead = no_exits_problem(st, doc.get("exits") if "exits_error" not in doc else [])
    if dead:
        doc["problems"].append(dead)
    doc["mtu"] = gwmtu.view(st, gwmtu.read_nic())
    return doc


# ── §6.1: a card too narrow for the clients' MTU — new settings with a smaller MTU, or as it is ────────────────

def _mtu_kind(st, kind):
    if kind not in gwmtu.KINDS:
        raise KaskadError(tr("gw_ops.mtu.kinds", kinds=", ".join(gwmtu.KINDS), kind=kind), code=USAGE)
    if model.inbound(st, kind) is None:
        raise KaskadError(tr("gw_ops.err.not_installed", kind=registry.title(kind)), code=USAGE)


def mtu_view(st=None):
    """Карта, бюджет, входы и ноги (files only — the panel and the bot ask it)."""
    st = state.load() if st is None else st
    return gwmtu.view(st, gwmtu.read_nic())


def mtu_reissue(runner, kind):
    """«Выдать новые настройки с меньшим MTU»: the client MTU of ``kind`` -> ``reissue_mtu`` (fits the card); the
    server follows. The devices on it must be given their settings again — the list (F-2) comes back."""
    _mtu_kind(state.load(), kind)

    def mutate(new, sec):
        gwmtu.refresh(new)
        p = gwmtu.inbound_plan(new, kind)
        target = p["new_client"] if p["narrow"] else p["client"]       # the card fits them: nothing to change
        rec = model.inbound(new, kind)
        if kind == "wg":
            rec["mtu"] = target
        else:
            params = dict(rec.get("params") or {})
            params["MTU"] = str(target)
            rec["params"] = params
        keep = (model.gw(new).get("mtu") or {}).get("keep")
        if isinstance(keep, dict):
            keep.pop(kind, None)
        return p["client"], target

    (was, target), res = _change(runner, mutate)
    st = state.load()
    title = gwmtu.TITLES.get(kind, kind)
    out = {"kind": kind, "was": was, "client": target, "server": gwmtu.server_for(st, kind),
           "devices": _reissue_devices(st, kind), "warnings": (res or {}).get("warnings") or []}
    if was == target:
        out["text"] = tr("gw_ops.mtu.fits", title=title, mtu=target)
    elif out["devices"]:
        out["text"] = tr("gw_ops.mtu.reissue", title=title, was=was, now=target,
                         who=_who(_reissue_names(out["devices"])))
    else:
        out["text"] = tr("gw_ops.mtu.no_devices", title=title, was=was, now=target)
    out["changed"] = was != target
    if out["changed"]:
        util.log_tr("gw_ops.log.mtu_client", kind=kind, was=was, now=target)
    return out


def mtu_keep(runner, kind):
    """«Оставить как есть» for what is offered now: a server below 1232 (remembered for this S4) or a narrow card
    (remembered for this card) — the warning turns into a quiet line until the S4 or the card changes."""
    with state.locked():
        st = state.load()
        _mtu_kind(st, kind)
        gwmtu.refresh(st)
        m = model.gw(st)["mtu"]
        p = gwmtu.inbound_plan(st, kind)
        what = "s4" if p["offer"] == "s4" else "mtu"
        name, value = ("keep_s4", p["s4"]) if what == "s4" else ("keep", gwmtu.nic_of(st))
        if not isinstance(m.get(name), dict):
            m[name] = {}
        m[name][kind] = value
        m["inbounds"] = dict((k, gwmtu.inbound_plan(st, k)) for k in m.get("inbounds") or {})
        state.save(st)                                  # ⚠РФ: the MTU only — no rfgate.check
    with util.journal_lang():
        util.log_action(tr("gw_ops.log.mtu_keep", kind=kind,
                           what="S4 = %d" % p["s4"] if what == "s4" else tr("gw_ops.mtu.narrow")))
    return {"kind": kind, "kept": True, "what": what, "nic": gwmtu.nic_of(st), "s4": p["s4"]}


def mtu_s4(runner, kind, yes=False):
    """«Выдать новые настройки с меньшим S4» (line 2.0 whose server MTU fell below 1232): new S3/S4 within the budget —
    the inbound restarts and the settings given before stop connecting. Without ``yes`` nothing changes: who would
    need new settings (F-2) and what happens. With it: the parameters, the restart, the marks «ждут новых настроек»."""
    st = state.load()
    _mtu_kind(st, kind)
    if kind not in gwmtu.S4_KINDS:
        raise KaskadError(tr("gw_ops.mtu.s4_only"), code=USAGE)
    title = gwmtu.TITLES.get(kind, kind)
    devices = _reissue_devices(st, kind)
    names = _reissue_names(devices)
    who = _who(names) if names else ""
    limit = max(15, awgkeys.s4_max(gwmtu.budget(st), gwmtu.client_mtu(st, kind)))
    was = gwmtu.inbound_s4(st, kind)
    plan = gwmtu.inbound_plan(st, kind)
    if not plan.get("new_s4_max"):
        return {"kind": kind, "done": False, "nothing": True, "was": was, "devices": [],
                "text": tr("gw_ops.mtu.s4_now", title=title, s4=was, server=plan["server"],
                           what=tr("gw_ops.mtu.s4_nothing" if not plan["low"] else "gw_ops.mtu.s4_no_help"))}
    if not yes:
        text = tr("gw_ops.mtu.s4_offer", title=title, limit=limit, was=was,
                  whom=tr("gw_ops.mtu.s4_whom", who=who) if who else tr("gw_ops.mtu.s4_nobody"))
        return {"kind": kind, "done": False, "was": was, "s4_max": limit, "devices": devices, "text": text}

    def mutate(new, sec):
        gwmtu.refresh(new)
        rec = model.inbound(new, kind)
        rec["params"] = awgkeys.regen_s4(rec.get("params") or {}, gwmtu.budget(new))
        keep = (model.gw(new).get("mtu") or {}).get("keep_s4")
        if isinstance(keep, dict):
            keep.pop(kind, None)
        return gwmtu.s4_of(rec["params"])

    s4, res = _change(runner, mutate)
    st = state.load()
    devices = _reissue_devices(st, kind)
    try:
        gwmtu.mark_waiting(kind, [d["id"] for d in devices])
    except (OSError, ValueError) as e:                  # the change is saved: a mark that failed — the journal only
        util.log_tr("gw_ops.log.reissue_mark", err=e)
    out = {"kind": kind, "done": True, "was": was, "s4": s4, "server": gwmtu.server_for(st, kind),
           "devices": devices, "warnings": (res or {}).get("warnings") or []}
    if devices:
        out["text"] = tr("gw_ops.mtu.s4_done", title=title, was=was, s4=s4, server=out["server"],
                         who=_who(_reissue_names(devices)))
    else:
        out["text"] = tr("gw_ops.mtu.s4_done_none", title=title, was=was, s4=s4, server=out["server"])
    util.log_tr("gw_ops.log.mtu_s4", kind=kind, was=was, s4=s4, n=len(devices))
    return out


def booting(runner):
    """True while kaskad-net.service is starting — its ExecStartPost runs ``net check --repair`` very early
    at boot (DefaultDependencies=no, before network-pre.target). Then the gateway's own services, inbounds
    and the exits are left to systemd and the collector: starting a unit ordered After=kaskad-net from
    there would wait for kaskad-net itself (a deadlock), and probing legs would delay the network."""
    return runner.run(["systemctl", "is-active", NET_UNIT]).out.strip() == "activating"


def forget_renumbered(runner, lines, log=None):
    """The pool was renumbered (a key removed or the pool replaced): forget the clients' connections that went
    out through these lines (their marks now lead to other exits) and say so in the log. -> flushed or None."""
    log = log or util.log_action
    n = net.flush_marks(runner, exits.line_marks(lines))
    where = ", ".join(str(i) for i in lines)
    with util.journal_lang():
        if n is None:
            log(tr("gw_ops.log.renumbered_no_conntrack", lines=where))
        else:
            log(tr("gw_ops.log.renumbered", lines=where, n=n))
    return n


def restored(st, runner, log=None):
    """The gw part of the table was missing and has just been put back (``net check`` step 1/2, the
    collector): connections the clients made meanwhile have no route of the gateway — forget them
    (``conntrack -D -s <base>``; gw_fwd drops them anyway, the clients reconnect through gw_pre). Says
    in the log what was done or why not. A recreated gw_ru is empty: the networks of Russia are loaded at once
    (``rules.timer.after_restore``). Returns the number of flushed entries or None."""
    if not model.enabled(st) or st.get("engine") != "nft":
        return None
    log = log or (lambda kind, text: util.log_action(text))
    n = net.flush_conntrack(runner, st)
    with util.journal_lang():
        if n is None:
            log("gw_conntrack", tr("gw_ops.log.restored_no_conntrack"))
        else:
            log("gw_conntrack", tr("gw_ops.log.restored", base=addr.base(st), n=n))
    rules_timer.after_restore(runner)
    return n


def _rules_kernel(st, live, repair):
    """Wave 3: are the networks of Russia (gw_ru) in the table the lists of the store — told by ``gw check`` / ``gw
    status`` only. The repair never loads them (15 k ranges in the minute watchdog — the loop the stand caught): that
    is kaskad-gw-rules-load.timer's job (``kaskadctl rules load``, a pause after a failure)."""
    if repair:
        return []
    from ..rules import apply as rules_apply
    text = rules_apply.kernel_problem(st, live)
    return [{"kind": "gw_rules", "detail": text, "repaired": False}] if text else []


def check(st, runner, repair=False, log=None):
    """The gateway part of the watchdog (``net check`` calls it when the gateway is on): routing (the kill
    switch first), ip6 table, compat-in, then — not during boot — services, inbounds and the exits (only if
    the collector is not running). Returns problems ``{"kind", "detail", "repaired", "error"?}``. The table
    itself is checked by nft.verify (step 2 of the watchdog). Services are restarted with ``--no-block``.
    Inbounds and xray are never (re)started while the gw part is missing from the live table (their units
    would refuse anyway: ExecStartPre of gw/gate.py)."""
    if not model.enabled(st) or st.get("engine") != "nft":
        return []
    log = log or (lambda kind, text: util.log_action(text))
    probs = []
    if repair:
        # a gateway enabled by a build without it gets systemd-networkd's drop-in from the minute watchdog
        # (not a problem to report: nothing is broken yet; written only if missing or different)
        gsys.networkd_keep_foreign()

    def add(kind, detail, fix):
        p = {"kind": kind, "detail": detail, "repaired": False}
        probs.append(p)
        if repair:
            try:
                fix()
                p["repaired"] = True
                with util.journal_lang():
                    log(kind, tr("gw_ops.log.repaired", detail=detail))
            except KaskadError as e:
                p["error"] = str(e)

    r = net.routing_problems(runner, st)
    if r:
        add("gw_routing", "; ".join(r), lambda: net.ensure_routing(runner, st))
    for text in net.routing_conflicts(runner, st):  # F1: чужое мешает, а починить нельзя — только сказать
        probs.append({"kind": "gw_conflict", "detail": text, "repaired": False})  # F1
        log("gw_conflict", "GW: %s" % text) if repair else None  # F1
    r = net.rp_problems(runner, st)  # F1: строгий rp_filter на интерфейсах шлюза режет выходы AmneziaWG
    if r:  # F1
        add("gw_rp_filter", "; ".join(r), lambda: net.ensure_rp(runner, st))  # F1
    r = net.ip6_problems(st, runner)
    if r:
        add("gw_ip6", "; ".join(r), lambda: net.apply_ip6(runner, st))
    if compat.available(runner) and not compat.present_in(runner):
        add("gw_compat_in", tr("gw_ops.check.no_compat_in", comment=compat.COMMENT_IN), lambda: compat.ensure_in(runner))
    if not repair:
        # what the renderer skipped (blocked) — a human has to look; the minute watchdog stays quiet
        for text in net.bad_data(st):
            probs.append({"kind": "gw_data", "detail": text, "repaired": False})
    if booting(runner):
        return probs
    try:
        pool_exits = exits.pool(runner, roles=exits.ROLES)
    except KaskadError:
        pool_exits = []
    sec = secrets.load()
    try:
        from . import peek                     # F2 R-perf: chains, gw_live and gw_ru_ver — never gw_ru's elements
        live = peek.read(runner, sets=(net.LIVE, net.RU_VER), counters=False)
        part = net.part_present(live)
    except KaskadError:
        live, part = None, False
    if part:
        probs.extend(_rules_kernel(st, live, repair))
    if xray.ru_open(runner, st=st, sec=sec, pool_exits=pool_exits):     # not the allowed ones (REV-s1-b7-2)
        # B7 (REV-s1-b6-sec Н-2): a closing that could not stop xray — said while it is so; no repair here (the
        # collector closes again every tick, under the xray lock)
        probs.append({"kind": "gw_xray_ru_open", "detail": tr("gw_ops.check.xray_ru_open"), "repaired": False})
    units = [(dns.UNIT, tr("gw_ops.unit.dns")), (collect.UNIT, tr("gw_ops.unit.collect"))]
    if os.path.exists(gsys.unit_path(dns.UNIT_DIRECT)):     # 3.3.7.2: the DNS of the devices going out directly
        units.append((dns.UNIT_DIRECT, tr("gw_ops.unit.dns_direct")))
    if xray.needed(st, sec, pool_exits):
        units.append((xray.UNIT, tr("gw_ops.unit.xray")))

    def ru_file(unit):
        # B7 круг 2 (-sec С-1): the file on the disk sends Russia directly to someone the state does not give it — the
        # watchdog never starts nor enables xray with it; the collector draws the file (under the xray lock) first.
        # Asked only before a repair of xray (a render — not every minute)
        if unit != xray.UNIT or not xray.unsafe_file(st, sec, runner, pool_exits):
            return False
        probs.append({"kind": "gw_xray_ru_open", "detail": tr("gw_ops.check.xray_ru_file"), "repaired": False})
        return True

    for unit, what in units:
        active = gsys.unit_active(runner, unit)
        enabled = gsys.unit_enabled(runner, unit) if active else None
        if (not active or not enabled) and ru_file(unit):
            continue
        if not active:
            if unit == xray.UNIT and not part:
                probs.append({"kind": "gw_service", "detail": tr("gw_ops.check.unit_down_no_part", what=what, unit=unit),
                              "repaired": False})
                continue
            add("gw_service", tr("gw_ops.check.unit_down", what=what, unit=unit),
                lambda u=unit: gsys.systemd(runner, "restart", u, no_block=True))
        elif not enabled:
            # running but not enabled: the next reboot leaves it down (3.3.0 T-M6 №2 — the same of nginx)
            add("gw_service", tr("gw_ops.check.unit_not_enabled", what=what, unit=unit),
                lambda u=unit: gsys.systemd(runner, "enable", u))
    probs.extend(dns.check(st, runner, repair, log))      # F2: DNS of 3.1.0 on :53 -> its port; why it is down
    from .inbounds import tunnel as ufw_of_inbounds         # F2: UFW enabled or reset after the inbounds
    probs.extend(ufw_of_inbounds.ufw_check(runner, repair, log))
    from .inbounds import accel as ppp_engine               # F2: PPP modules of the running kernel (no repair)
    probs.extend(ppp_engine.module_problems(st, runner, repair, log))
    probs.extend(gsys.stale_check(runner, repair, log))      # F2: our daemons on a binary apt replaced
    for kind in model.enabled_kinds(st):
        ad = registry.get(kind)
        if ad is None:
            continue
        try:
            running = bool((ad.status(runner) or {}).get("running"))
        except KaskadError:
            running = False
        if not running and not part:
            probs.append({"kind": "gw_inbound", "detail": tr("gw_ops.check.inbound_down_no_part", title=ad.title or kind),
                          "repaired": False})
        elif not running:
            def fix(a=ad):
                # callers of repair hold the state lock (net check --repair, gw check --repair): what
                # the adapter's prepare filled in is saved, as after any apply
                before = copy.deepcopy(st)
                with addr.using(st):
                    a.apply(st, sec, runner)
                sec.save(keep_deleted=True)
                if st != before:
                    state.save(st)                      # ⚠РФ: what an adapter filled in — no rfgate.check
            add("gw_inbound", tr("gw_ops.check.inbound_down", title=ad.title or kind), fix)
    if repair and not gsys.unit_active(runner, collect.UNIT):
        try:
            collect.exits_tick(st, runner, log=lambda text: log("gw_exit", text))
        except KaskadError:
            pass
    return probs


# ── the menu of install.sh (work package 7) ────────────────────────────────

def probe(runner):
    """``gw probe``: can the gateway be switched on here — ``support`` plus the engine of the forwards
    (with engine legacy the menu offers the move to nftables first). Read-only."""
    try:
        eng = state.load().get("engine") or ""
    except KaskadError:
        eng = ""
    sup = support(runner)
    pkgs = ([] if dns.binary(runner) else [dns.PACKAGE]) + ([] if runner.which("conntrack") else [CONNTRACK_PACKAGE])
    doc = {"ok": bool(sup["supported"]), "supported": bool(sup["supported"]), "code": sup["code"],
           "reason": sup["reason"], "engine": eng, "packages": pkgs}
    doc["dns"] = dns.probe(runner)                          # F2: the port of dnsmasq (5354) free here?
    return doc


def _plan_conflicts(st, runner, kind, protos, port, planned):
    found = []
    for pr in protos:
        if (pr, port) in planned:
            found.append({"kind": "plan", "detail": tr("gw_ops.port.by_plan", title=planned[(pr, port)])})
        found.extend(port_conflicts(st, runner, kind, pr, port))
    return found


def _free_planned(st, runner, protos, kind, planned):
    """free_port that also avoids the ports planned for the other kinds of the same plan."""
    for _ in range(50):
        p = free_port(st, runner, protos, kind)
        if not any((pr, p) in planned for pr in protos):
            return p
    raise KaskadError(tr("gw_ops.err.no_free_port", protos="/".join(protos)))


def _fixed_ports(ad):
    try:
        return [tuple(x) for x in (ad.fixed_ports() or [])]
    except Exception:                           # noqa: BLE001 — an adapter of another work package
        return []


def inbound_plan(runner, kinds, st=None):
    """Ports the install wizard proposes for ``kinds`` and what holds them now (spec §5: «сводка портов с
    проверкой занятости») — read-only. An installed inbound keeps its port. A removed one (``kept``: its record
    was kept) is offered its former port — with it the settings given to its devices work again. A new one gets its
    default port (AmneziaWG: a random one) or, when the default is 0, a random free port; ports planned for an
    earlier kind of the same call are not given twice. A busy default keeps its conflicts (forwards, other
    inbounds, listeners, Docker, foreign DNAT — ``port_conflicts``) and gets ``free``: the random free port
    the wizard offers instead. Fixed ports (L2TP, PPTP, IKEv2) are checked too, except the ones an
    installed inbound of ours already holds (strongSwan is shared by L2TP/IPsec and IKEv2).

    ``packages`` — what the install would still put in with apt (the menu tells a busy apt in advance).
    ``needs_core`` / ``needs_key`` — AmneziaWG has no core on disk (and no GitHub key to download one: it cannot be
    installed now, the menu says so before «Установить»); ``build`` — what the install builds from source (PPP:
    accel-ppp), "" when a build is there already.

    -> [{"kind", "title", "available", "installed", "supported", "why", "warning", "needs_core", "needs_key", "build",
         "packages", "kept",
         "port": {"field", "protos", "port", "default", "conflicts": [{"kind", "detail"}], "free", "random"} or None,
         "fixed": [{"proto", "port", "conflicts"}]}]"""
    bad = [k for k in kinds if k not in model.INBOUND_KINDS]
    if bad:
        raise _unknown_kind(bad[0])
    st = state.load() if st is None else st
    ours = set()
    for k in model.installed_kinds(st):
        ad = registry.get(k)
        if ad is not None:
            ours.update(_ports_of(ad, st))
    planned = {}                                       # (proto, port) -> title of the kind it is planned for
    rows = []
    for kind in kinds:
        ad = registry.get(kind)
        rec = model.inbound(st, kind)
        row = {"kind": kind, "title": (ad.title if ad else "") or kind, "available": ad is not None,
               "installed": rec is not None, "supported": ad is not None,
               "why": "" if ad is not None else tr("gw_ops.plan.not_in_version"), "warning": "",
               "needs_core": False, "needs_key": False, "build": "", "packages": [], "kept": False, "port": None,
               "fixed": []}
        rows.append(row)
        if ad is None:
            continue
        try:
            req = ad.requirements(runner) or {}
        except KaskadError as e:
            req = {"supported": False, "why": str(e)}
        row["supported"] = bool(req.get("supported", True))
        row["why"] = util.redact(req.get("why") or "")
        row["warning"] = util.redact(req.get("warning") or "")
        row["needs_core"] = bool(req.get("needs_core"))       # AmneziaWG: the menu downloads the core and plans again
        row["needs_key"] = bool(req.get("needs_key"))         # ...and cannot: no core on disk, no GitHub key (T-G2b N-3)
        row["build"] = req.get("build") or ""                 # PPP: accel-ppp is built from source by this install
        if row["supported"] and rec is None:
            row["packages"] = [p for p in req.get("packages") or [] if not gsys.pkg_installed(runner, p)]
        fields = _fields(ad)
        kept = model.kept_inbound(st, kind) if rec is None else None
        row["kept"] = kept is not None                     # removed earlier: its port and keys come back
        if fields:
            field, protos = fields[0]
            cur = (rec or {}).get(field)
            if rec is not None and isinstance(cur, int) and not isinstance(cur, bool) and cur > 0:
                p = {"field": field, "protos": protos, "port": cur, "default": cur, "conflicts": [], "free": None,
                     "random": False}
            else:
                given = (ad.defaults(st) or {}).get(field)
                default = _kept_port(kept, field, True) or given or _default_port(ad)
                cands = [] if _kept_port(kept, field, True) else _candidates(ad, st)
                # «random»: the install draws the port itself (AmneziaWG; no default) — the number here is only a
                # sample, the wizard says «порт выберем сами» (T-M6-qs330 №11)
                drawn = not _kept_port(kept, field, True) and not cands and not given and \
                    bool(getattr(ad, "port_random", False))
                if cands:                                  # VLESS: 443, else 8443 — the first one free
                    got, _ = _first_free(st, runner, kind, protos, cands, planned)
                    default = got or cands[0]
                if default:
                    found = _plan_conflicts(st, runner, kind, protos, default, planned)
                    p = {"field": field, "protos": protos, "port": default, "default": default, "conflicts": found,
                         "free": _free_planned(st, runner, protos, kind, planned) if found else None,
                         "random": drawn}
                else:
                    p = {"field": field, "protos": protos, "port": _free_planned(st, runner, protos, kind, planned),
                         "default": 0, "conflicts": [], "free": None, "random": True}
            if row["supported"]:                           # a kind that cannot work here takes no port
                for pr in protos:
                    planned[(pr, p["free"] or p["port"])] = row["title"]
            row["port"] = p
        for proto, port in _fixed_ports(ad):
            found = [] if (proto, port) in ours else port_conflicts(st, runner, kind, proto, port)
            row["fixed"].append({"proto": proto, "port": port, "conflicts": found})
    return rows


def offers(platform, st=None):
    """What to give a device of ``platform`` (spec §4, ``issue_os.offers``), best first, with the state of
    each inbound here — the menu preselects the inbounds of a new device by it. Read-only."""
    from . import issue_os
    st = state.load() if st is None else st
    p = issue_os.platform(platform)
    inst, on = model.installed_kinds(st), model.enabled_kinds(st)
    rows = []
    for o in issue_os.offers(p):
        ad = registry.get(o["inbound"])
        rows.append(dict(o, title=(ad.title if ad else "") or issue_os.TITLES.get(o["inbound"], o["inbound"]),
                         installed=o["inbound"] in inst, enabled=o["inbound"] in on))
    return {"ok": True, "platform": p, "offers": rows, "notes": offer_notes(p)}


def offer_notes(platform):
    """What to know of ``platform`` before choosing its inbounds (the menu, the panel, the bot show them under the
    offers), in the language of the call. One source with the issue screen — ``issue_os.NOTES`` (gw_issue.note.*):
    two copies of the same notes drifted apart in English (T-332 №29)."""
    from . import issue_os
    return list(issue_os.NOTES.get(platform, ()))


class _Options(dict):
    """{(kind, key): what it is} — the words in the language of the moment they are read."""

    def __getitem__(self, k):
        return tr(dict.__getitem__(self, k))

    def get(self, k, default=None):
        return self[k] if k in self else default

    def items(self):
        return [(k, self[k]) for k in self]

    def values(self):
        return [self[k] for k in self]


# switches of an inbound record the menu may change: (kind, key) -> what it is
OPTIONS = _Options({("l2tp", "plain"): "gw_ops.option.l2tp_plain", ("socks5", "udp"): "gw_ops.option.socks5_udp",
                    ("hy2", "obfs"): "gw_ops.option.hy2_obfs"})
_ON = ("on", "yes", "true", "1")
_OFF = ("off", "no", "false", "0")


def inbound_option(runner, kind, key, value):
    """``gw inbound option <kind> <key> on|off`` — one switch of OPTIONS in the inbound record, applied
    like any change (L2TP: plain L2TP for routers that cannot do IPsec, spec §3.3). Hysteria2: also ``hop
    <lo-hi>|off`` and ``cert domain|self`` (``hy2_set``)."""
    if kind == HY2 and key in ("hop", "cert"):
        res = hy2_set(runner, **{key: value})
        return dict(res, key=key, value=res.get(key))
    if (kind, key) not in OPTIONS:
        raise KaskadError(tr("gw_ops.err.no_option", kind=kind, key=repr(key),
                             keys=", ".join("%s %s" % k for k in sorted(OPTIONS))), code=USAGE)
    v = str(value).strip().lower()
    if v not in _ON + _OFF:
        raise KaskadError(tr("gw_ops.err.on_off", value=repr(value)), code=USAGE)
    on = v in _ON
    adapter(kind)

    def mutate(new, sec):
        rec = model.inbound(new, kind)
        if rec is None:
            raise KaskadError(tr("gw_ops.err.not_installed_hint", kind=kind))
        old = rec.get(key)
        rec[key] = on
        return old

    old, res = _change(runner, mutate)
    util.log_action("GW inbound option: %s %s %s -> %s" % (kind, key, old, "on" if on else "off"))
    out = {"ok": True, "kind": kind, "key": key, "value": on, "old": old, "apply": res}
    if kind == HY2 and bool(old if old is not None else True) != on:
        _hy2_reissue(out)                                 # the Salamander password is in the links given out
    return out


def _hy2_reissue(out):
    """``reissue`` and ``text`` of a change of Hysteria2 that changes its links given out (the port, the mask)."""
    rows = _reissue_devices(state.load(), HY2)
    out["reissue"] = rows
    if rows:
        out["text"] = tr("gw_ops.hy2.reissue", who=_who(_reissue_names(rows)))
    return out


HY2 = "hy2"
HY2_CERTS = ("domain", "self")


def hop_conflicts(st, runner, lo, hi):
    """[{"port", "kind", "detail"}] of the range lo..hi of Hysteria2 port hopping: forwards and other inbounds on any
    of its UDP ports (in the state — also while they are off), and what listens there or Docker publishes — ONE ``ss``
    for the whole range (kaskad.conflicts.check_many). The ports xray of the gateway already holds for this very range
    are no conflict."""
    ports = list(range(lo, hi + 1))
    out = []
    fw = dict((f["port"], f) for f in st.get("forwards") or [] if f.get("proto") == "udp")
    for p in ports:
        if p in fw:
            out.append({"port": p, "kind": "forward",
                        "detail": tr("gw_ops.port.by_forward", to=fw[p]["to"], to_port=fw[p]["to_port"])})
    for other in model.installed_kinds(st):
        if other == HY2:
            continue
        ad = registry.get(other)
        mine = set(p for pr, p in _ports_of(ad, st) if pr == "udp") if ad is not None else set()
        for p in sorted(mine & set(ports)):
            out.append({"port": p, "kind": "gateway", "detail": tr("gw_ops.port.by_inbound", title=ad.title or other)})
    try:                                      # xray's own ports at 127.0.0.1 (TPROXY, DNS of dnsmasq, VLESS exits)
        inner = [(p, what) for pr, p, what in xray.internal_ports(st, runner=runner) if pr == "udp"]
    except Exception:                         # noqa: BLE001 — the static ones at least
        inner = [(xray.TPROXY_PORT, "TPROXY"), (xray.DNS_PORT, "DNS")]
    for p, what in inner:
        if lo <= p <= hi:
            out.append({"port": p, "kind": "gateway", "detail": what})
    own = set(p for pr, p in _ports_of(registry.get(HY2), st) if pr == "udp") if registry.get(HY2) else set()
    for p, found in sorted(conflicts.check_many("udp", ports, runner, st=st, local=True).items()):
        for c in found:
            if c["kind"] == "gateway" or (p in own and c["kind"] == "listener" and "xray" in c["detail"]):
                continue
            out.append({"port": p, "kind": c["kind"], "detail": _inbound_words(c["detail"])})
    return out


def hy2_set(runner, hop=None, cert=None, force=False):
    """``gw inbound option hy2 hop <lo-hi>|off`` and ``… cert domain|self``: port hopping of Hysteria2 (up to
    hy2.HOP_MAX ports, 1024–65535, none of them taken — the list of the busy ones in the refusal) and the certificate
    it prefers (``domain`` — the domain's when the node has one; ``self`` — always its self-signed one). The links
    given out stop working: ``reissue`` lists whom to give them again."""
    from .inbounds import hy2 as hin
    adapter(HY2)
    st0 = state.load()
    rec0 = model.inbound(st0, HY2)
    if rec0 is None:
        raise KaskadError(tr("gw_ops.err.not_installed_hint", kind=HY2))
    fields = {}
    if hop is not None:
        try:
            h = hin.parse_hop(hop)
        except KaskadError as e:
            raise KaskadError(str(e), code=USAGE)
        if h:
            lo, hi = hin.hop_bounds(h)
            if lo <= (rec0.get("port") or 0) <= hi:
                raise ConflictError(tr("gw_ops.err.hy2_hop_port", port=rec0.get("port"), hop=h),
                                    data={"kind": "gateway"})
            found = [] if force or hin.hop_bounds(rec0.get("hop")) == (lo, hi) else hop_conflicts(st0, runner, lo, hi)
            if found:
                busy = ", ".join("%d/udp — %s" % (c["port"], c["detail"]) for c in found[:10])
                more = len(set(c["port"] for c in found)) - len(set(c["port"] for c in found[:10]))
                raise ConflictError(tr("gw_ops.err.hy2_hop_busy", hop=h, busy=busy + (
                    tr("gw_ops.reissue.more", n=more) if more > 0 else "")),
                    data={"kind": found[0]["kind"], "conflicts": found})
        fields["hop"] = h
    if cert is not None:
        c = str(cert).strip().lower()
        if c not in HY2_CERTS:
            raise KaskadError(tr("gw_ops.err.hy2_cert", value=repr(cert)), code=USAGE)
        fields["cert"] = c

    def mutate(new, sec):
        rec = model.inbound(new, HY2)
        if rec is None:
            raise KaskadError(tr("gw_ops.err.not_installed_hint", kind=HY2))
        old = dict((k, rec.get(k)) for k in fields)
        rec.update(fields)
        return old

    old, res = _change(runner, mutate)
    st1 = state.load()
    rec1 = model.inbound(st1, HY2) or {}
    # what the links carry: the range, the certificate the inbound takes now (``cert`` alone, the mode the same — nothing)
    changed = [k for k in fields if k != "cert" and old.get(k) != rec1.get(k)] + (
        ["cert_mode"] if (rec0.get("cert_mode") != rec1.get("cert_mode")) else [])
    rows = _reissue_devices(st1, HY2) if changed else []
    util.log_action("GW inbound option: hy2 %s" % " ".join("%s %s -> %s" % (k, old.get(k), rec1.get(k))
                                                            for k in sorted(fields)))
    out = {"ok": True, "kind": HY2, "changed": changed, "hop": rec1.get("hop"), "cert": rec1.get("cert"),
           "cert_mode": rec1.get("cert_mode"), "reissue": rows, "apply": res}
    if rows:
        out["text"] = tr("gw_ops.hy2.reissue", who=_who(_reissue_names(rows)))
    return out


def hy2_rekey(runner):
    """``gw hy2 rekey --yes``: a new self-signed pair of Hysteria2 — every link given out with the old pin stops
    connecting (``reissue``). With the domain's certificate the pin is not in the links, the new pair waits."""
    ad = adapter(HY2)

    def mutate(new, sec):
        if model.inbound(new, HY2) is None:
            raise KaskadError(tr("gw_ops.err.not_installed_hint", kind=HY2))
        return ad.rekey(new, sec, runner)

    _, res = _change(runner, mutate)
    st1 = state.load()
    rec = model.inbound(st1, HY2) or {}
    rows = _reissue_devices(st1, HY2) if rec.get("cert_mode") != "domain" else []
    if rec.get("cert_mode") != "domain" and rec.get("enabled", True) is not False and model.enabled(st1) \
            and gsys.unit_active(runner, xray.UNIT):
        # the config of xray is the same (the files are), so apply restarted nothing: xray must take the new pair now —
        # else it serves the old one, the old pin keeps working and the new links do not (SEC-s1-t12 С-1)
        runner.run(["systemctl", "restart", xray.UNIT])
    util.log_action("GW hy2 rekey")
    out = {"ok": True, "kind": HY2, "pin": rec.get("pin") or "", "reissue": rows, "apply": res}
    if rows:
        out["text"] = tr("gw_ops.hy2.reissue", who=_who(_reissue_names(rows)))
    return out


def hy2_apply(runner):
    """The card of Hysteria2, «Применить настройки» at a certificate that did not fit (REV-3373 З-3): the gateway from
    state (``gw apply``) — it writes the copy xray reads again (self: the pair of secrets.json, the same pin; domain:
    the site's pair) — and xray restarts ALWAYS (the panel asked first): the config is the same, so apply alone leaves
    xray with the pair it read at its start, and the copy may have been written already by another apply. Once only:
    when that apply started or restarted xray itself (the config changed, e.g. the mode), the new process read the copy.
    ``reissue`` — whom to give the links again: the mode changed (domain ↔ self — the link gains or loses the pin, as in
    ``hy2_set``) or, self, the pin; never ``gw hy2 rekey``."""
    st0 = state.load()
    rec0 = model.inbound(st0, HY2)
    if rec0 is None:
        raise KaskadError(tr("gw_ops.err.not_installed_hint", kind=HY2))
    res = apply_all(runner)
    st1 = state.load()
    rec1 = model.inbound(st1, HY2) or {}
    xr = res.get("xray") if isinstance(res.get("xray"), dict) else {}
    restarted = bool(xr.get("started") or xr.get("restarted"))      # REV-3373-pre 4: not twice
    if not restarted and rec1.get("enabled", True) is not False and model.enabled(st1):
        restarted = runner.run(["systemctl", "restart", xray.UNIT]).ok
    mode0, mode1 = rec0.get("cert_mode"), rec1.get("cert_mode")
    rows = _reissue_devices(st1, HY2) if mode0 != mode1 or \
        (mode1 != "domain" and (rec0.get("pin") or "") != (rec1.get("pin") or "")) else []
    util.log_action("GW hy2 apply (restart %s)" % ("yes" if restarted else "no"))
    out = {"ok": True, "kind": HY2, "restarted": restarted, "reissue": rows, "apply": res}
    if rows:
        out["text"] = tr("gw_ops.hy2.reissue", who=_who(_reissue_names(rows)))
    return out


def hy2_show(runner, st=None):
    """``gw hy2 show``: what the clients of Hysteria2 are given, no secrets — the port, the range, Salamander, the
    certificate (domain / self-signed and its pin), the devices; ``warnings`` (no domain, UDP not checked)."""
    from .inbounds import hy2 as hin
    st = state.load() if st is None else st
    rec = model.inbound(st, HY2)
    if rec is None:
        return {"ok": True, "kind": HY2, "installed": False}
    ad = registry.get(HY2)
    mode = rec.get("cert_mode") if rec.get("cert_mode") in HY2_CERTS else "self"
    from ..site import model as site_model
    out = {"ok": True, "kind": HY2, "installed": True, "enabled": bool(rec.get("enabled", True)),
           "port": rec.get("port"), "hop": rec.get("hop") or None, "obfs": rec.get("obfs", True) is not False,
           "cert": rec.get("cert") or "domain", "cert_mode": mode, "pin": rec.get("pin") or "" if mode == "self" else "",
           "domain": site_model.domain_of(st) if mode == "domain" else "", "domain_ready": bool(hin.domain_ready(st)),
           "congestion": rec.get("congestion") or "bbr", "devices": len(model.devices_for(st, HY2)),
           "host": model.public_host(st), "label": rec.get("label") or "",
           "ports_view": [{"proto": pr, "port": p} for pr, p in (ad.ports_view(st) if ad is not None else [])]}
    out["warnings"] = [tr("gw_in_hy2.warn.self_cert")] if mode == "self" else []
    # UDP и мобильные сети — не предупреждение, а справка (решение владельца 20.10, UI-2 круг 3): панель показывает её одной
    # нейтральной заметкой внизу «Подробнее», без жёлтой строки и без повторов
    out["note"] = tr("gw_in_hy2.udp_note")
    return out


CORE_UNITS = (xray.UNIT, dns.UNIT, collect.UNIT) + rules_timer.UNITS
_KEEP_SECRET = "dev.%s.password"


def _present_kinds(st):
    """Inbound kinds with something of theirs on the node: a record in state, the adapter's directory in
    /etc/kaskad/gw or one of its units (an install cut short leaves files without a record)."""
    out = set(model.installed_kinds(st))
    for kind in model.INBOUND_KINDS:
        ad = registry.get(kind)
        if kind in out or ad is None:
            continue
        try:
            units = list(ad.units() or [])
        except Exception:                       # noqa: BLE001 — an adapter of another work package
            units = []
        if os.path.isdir(os.path.join(paths.gw_dir(), kind)) or any(os.path.lexists(gsys.unit_path(u)) for u in units):
            out.add(kind)
    return [k for k in model.INBOUND_KINDS if k in out]


def _remove_units(runner):
    """Unit files of the core (xray, DNS, collector) and every kaskad-gw-*.service still on disk."""
    names = list(CORE_UNITS) + sorted(os.path.basename(p) for p in
                                      glob.glob(os.path.join(paths.systemd_dir(), "kaskad-gw-*.service")))
    gone = []
    for name in names:
        if name not in gone and os.path.lexists(gsys.unit_path(name)) and gsys.remove_unit(runner, name):
            gone.append(name)
    return gone


def _unmask_packages(runner):
    """Packaged units Kaskad masked when it installed their packages (strongSwan, OpenVPN, ocserv) and no
    adapter's uninstall gave back: unmasked by /etc/kaskad/gw/packages.json — it goes with /etc/kaskad/gw."""
    try:
        from .inbounds import tunnel
    except ImportError:
        return []
    rec = tunnel.read_json(paths.P(tunnel.PACKAGES_FILE), {})
    if not isinstance(rec, dict) or not rec:
        return []
    return tunnel.unmask_packages(runner, sorted(rec))


def _step(res, what, fn):
    """One step of purge: an error of any kind is a warning and the rest still goes. -> (ok, value)."""
    try:
        return True, fn()
    except Exception as e:                      # noqa: BLE001 — adapters of other work packages, the OS
        _warn(res, "%s: %s" % (what, e))
        return False, None


def _mark_off():
    with state.locked():
        st = state.load()
        if model.enabled(st):
            new = copy.deepcopy(st)
            model.gw(new)["enabled"] = False
            state.save(new)                             # ⚠РФ: «off» only — no rfgate.check


def _drop_xray_user(runner):
    """The system user of the gateway's xray (xray.ensure_user) goes with the gateway."""
    if net.xray_uid() is None:
        return False
    res = runner.run(["userdel", net.XRAY_USER])
    if not res.ok:
        raise KaskadError("userdel %s: %s" % (net.XRAY_USER, util.redact((res.err or res.out).strip()[:200])))
    return True


@gsys.held                                              # F2
def purge(runner, users=False):
    """``gw purge --yes``: the whole gateway away («Удалить шлюз», full uninstall, the way back to 2.13).

    The gateway goes off first (``disable``: state says off, so the watchdog leaves it alone), then every
    inbound is uninstalled — the ones in state and leftovers of a cut-short install (units, configs, UFW
    rules, the daemons' system files, the masks of packaged units; packages and the accel-ppp build stay,
    as on any uninstall) — then masks nobody gave back (packages.json), the units of the core and every
    kaskad-gw-*.service left, /etc/wireguard/kgw-*.conf, /etc/kaskad/gw and /run/kaskad/gw — in this
    order: packages.json lives in /etc/kaskad/gw. In state: no inbounds (nor kept records of removed ones), address
    or ``enabled``; users,
    devices (with their addresses and accesses), routes and the network base they are in stay, of the
    secrets only the devices' passwords and the links of the people's pages — keys, certificates and the
    PSK go (a new install makes new ones: issued settings must be given out again). ``users``: the whole gateway part of state.json and
    every secret go as well. A step that fails is a warning; the rest still goes."""
    res = {"ok": True, "warnings": [], "uninstalled": [], "units": [], "unmasked": [], "users": bool(users)}
    st0 = state.load()
    table_left = False
    if model.enabled(st0):
        try:
            disable(runner)
        except KaskadError as e:
            _warn(res, tr("gw_ops.purge.disable_failed", err=e))
            _mark_off()
            table_left = True
            _step(res, tr("gw_ops.purge.stop"), lambda: teardown(runner))
    else:
        _step(res, tr("gw_ops.purge.services"), lambda: _core_down(runner))
    for kind in _present_kinds(state.load()):
        ad = registry.get(kind)
        if ad is None:
            _warn(res, tr("gw_ops.purge.no_adapter", kind=registry.title(kind)))
            continue
        ok, _ = _step(res, ad.title or kind, lambda a=ad: a.uninstall(runner))
        if ok:
            res["uninstalled"].append(kind)
    _, got = _step(res, tr("gw_ops.purge.masks"), lambda: _unmask_packages(runner))   # packages.json is in gw_dir
    res["unmasked"] = got or []
    _, probe_gone = _step(res, tr("gw_probe.purge.units"), lambda: probe_unit.remove(runner))   # T6 (§5.7)
    _, got = _step(res, tr("gw_ops.purge.services"), lambda: _remove_units(runner))
    res["units"] = (probe_gone or []) + (got or [])
    _, got = _step(res, tr("gw_ops.purge.gates"), lambda: gate.remove_all(runner))
    res["gates"] = got or []
    _step(res, tr("gw_ops.unit.xray"), lambda: xray.remove(runner))          # its unit, config and /opt/kaskad/gw/xray
    _step(res, tr("gw_ops.purge.user", user=net.XRAY_USER), lambda: _drop_xray_user(runner))
    for p in glob.glob(paths.P("/etc/wireguard/kgw-*.conf")):
        try:
            os.unlink(p)                               # keys of the server and the devices' PSK
        except OSError:
            pass
    for d in (paths.gw_dir(), paths.gw_run_dir(), paths.gw_var_dir()):     # T7: pins, traffic, probe results
        shutil.rmtree(d, ignore_errors=True)
    for p in (paths.metrics_db(), paths.metrics_db() + "-wal", paths.metrics_db() + "-shm"):
        try:
            os.unlink(p)                               # the history of the charts (T3) goes with the gateway (T7)
        except OSError:
            pass
    _step(res, tr("gw_ops.purge.rules"), rules_store.purge)                # /var/lib/kaskad/rules (downloaded, not ours)
    with state.locked():
        st = state.load()
        new = copy.deepcopy(st)
        try:
            sec = secrets.load()
        except KaskadError as e:                   # a broken secrets.json must not stop the purge for good
            bad = "%s.bad-%d" % (paths.secrets_file(), int(time.time()))
            try:
                os.rename(paths.secrets_file(), bad)
                _warn(res, tr("gw_ops.purge.secrets_moved", err=e, path=bad))
            except OSError:
                _warn(res, str(e))
            sec = secrets.Store({})
        if users:
            kept_rf = dict((k, v) for k, v in (st.get("gateway") or {}).items() if k in rfgate.KEYS)
            # as state.empty(); the marks ⚠РФ are of the person and the server, not of the gateway (rf §3.6)
            new["gateway"] = kept_rf
            rev.seed(new)                              # the version of the policy goes on from a random number (§3.1)
            keep = set()
        else:
            g = model.gw(new)
            g["enabled"] = False
            g["inbounds"] = {}
            model.forget_kept(new)                     # the records of removed inbounds: their keys go too
            g.pop("host", None)                        # the base stays: the kept devices' addresses are in it
            g.pop("host_auto", None)
            keep = set(_KEEP_SECRET % d.get("id") for d in g["devices"])
            keep.update("user.%s.page" % u.get("id") for u in g["users"])      # links given to the people we keep
            keep.update("user.%s.sub" % u.get("id") for u in g["users"])       # and their subscriptions for apps
        for k in list(sec):
            if k not in keep:
                sec.delete(k)
        if table_left and new.get("engine") == "nft":
            try:
                _remove_table_part(runner, new)        # what disable did not get to
                nft.apply(new, runner)                 # and kaskad.nft without the gateway part
            except KaskadError as e:
                _warn(res, tr("gw_ops.purge.table_left", err=e))
        rfgate.check(st, new, refuse=False)            # people gone: the consent goes when nothing chooses ru_direct
        state.save(new)
        sec.save()
    with util.journal_lang():
        util.log_action(tr("gw_ops.log.purge", inbounds=len(res["uninstalled"]), units=len(res["units"]),
                           people=tr("gw_ops.log.purge_people_gone" if users else "gw_ops.log.purge_people_kept")))
    return res


# ── F7 (3.3.7.15, полоса Г): «Скачать мои новые настройки» ─────────────────

def _preview_port(st, kind, changes):
    """The change of the preview before F3 brings ``settings_mutate``: the port (and its protocol) only — the field
    ``_inbound_port`` writes. Anything else — USAGE (nothing is guessed)."""
    extra = sorted(k for k in changes if k not in ("port", "proto"))
    if extra:
        raise KaskadError(tr("gw_pending.err.preview_field"), code=USAGE, data={"field": extra[0]})
    if changes.get("port") is None:
        return
    p = state.parse_port(changes["port"])
    proto = changes.get("proto")
    fields = [f for f, protos in _fields(adapter(kind)) if proto is None or proto in protos]
    if not fields:
        raise KaskadError(tr("gw_ops.err.no_port", kind=registry.title(kind), proto=proto or ""), code=USAGE)
    model.inbound(st, kind)[fields[0]] = p


PREVIEW_TTL = 900                   # s: «Скачать мои новые настройки» counts for an application this soon after it


def preview_path():
    return os.path.join(paths.gw_var_dir(), "issue-preview.json")


def _client_view(st, kind, dev_id):
    """What the settings of device ``dev_id`` for ``kind`` carry of the server's settings: the ports, MTU and the
    parameters of AmneziaWG, TLS, the network of the gateway and the addresses of the device — the same view, the same
    file as «Выдать» gives (П5 З-2)."""
    rec = model.inbound(st, kind) or {}
    d = model.find_device(st, dev_id) or {}
    view = {"ports": dict((f, rec.get(f)) for f, _ in _fields(adapter(kind))), "tls": _tls_of(st, kind),
            "base": str(addr.base(st)), "ips": dict(d.get("ips") or {})}
    if kind in gwmtu.KINDS:
        view["mtu"] = gwmtu.client_mtu(st, kind)
        view["params"] = dict(rec.get("params") or {}) if kind != "wg" else {}
    return view


def _preview_read():
    import json
    try:
        doc = json.loads(util.read_text(preview_path()) or "{}")
    except ValueError:
        return {}
    if not isinstance(doc, dict):
        return {}
    return dict((k, v) for k, v in doc.items() if k in model.INBOUND_KINDS and isinstance(v, dict)
                and isinstance(v.get("device"), str) and isinstance(v.get("at"), int)
                and isinstance(v.get("view"), dict))


def _preview_write(doc):
    import json
    if not doc:
        try:
            os.unlink(preview_path())
        except FileNotFoundError:
            pass
        return
    util.ensure_dir(paths.gw_var_dir(), 0o700)
    util.atomic_write(preview_path(), json.dumps(doc, sort_keys=True) + "\n", 0o600)


def _preview_note(kind, dev_id, st_after):
    """«Скачать мои новые настройки» gave ``dev_id`` the settings of ``kind`` as ``st_after`` has them: the last one of
    each kind kept (0600, no secret). A failed write only leaves the mark on (the safe side)."""
    try:
        doc = _preview_read()
        doc[kind] = {"device": dev_id, "at": int(time.time()), "view": _client_view(st_after, kind, dev_id)}
        _preview_write(doc)
    except Exception as e:                              # noqa: BLE001 — the preview itself never fails for it
        util.log_tr("gw_ops.log.preview_note", err=e)       # its own words: no mark was touched (П7, П-2)


def _preview_given(kind, st):
    """The device given «Скачать мои новые настройки» of ``kind`` for exactly what ``st`` holds now (the view of
    ``_client_view`` the same, not older than ``PREVIEW_TTL``) — its id, else None. Any application of ``kind`` takes
    the note away: it counts once (П5 З-2)."""
    try:
        doc = _preview_read()
        rec = doc.pop(kind, None)
        if rec is None:
            return None
        _preview_write(doc)
        if not 0 <= int(time.time()) - rec["at"] <= PREVIEW_TTL:
            return None
        import json
        now = json.loads(json.dumps(_client_view(st, kind, rec["device"]), sort_keys=True))
        return rec["device"] if rec["view"] == now else None
    except Exception as e:                              # noqa: BLE001 — never fails the application: the mark stays
        util.log_tr("gw_ops.log.preview_note", err=e)
        return None


def _preview_forget(kind, dev_id):
    """«Выдать» (``issue`` with ``mark``) gave ``dev_id`` the settings of ``kind`` of now: they replace those of «Скачать
    мои новые настройки» on the device, so its note of ``kind`` goes (П8, OBS-3315-1822: else the application of the
    previewed change within ``PREVIEW_TTL`` took the mark off a device holding the old settings). A note of another
    device stays. Never fails the issue: a failure is said in the journal."""
    try:
        doc = _preview_read()
        rec = doc.get(kind)
        if rec is None or rec.get("device") != dev_id:
            return
        doc.pop(kind)
        _preview_write(doc)
    except Exception as e:                              # noqa: BLE001 — the issue itself never fails for it
        util.log_tr("gw_ops.log.preview_note", err=e)


def issue_preview(runner, ref, kind, changes, who=None, os_name=None, mutate=None):
    """F7 «Скачать мои новые настройки» (SPEC-R1 §3 F7, 8.4.1 №4): the settings of a device for ``kind`` as they will
    be after ``changes`` ({port?, proto?, mtu?, tls?, base?}; ``base`` — F6, the new network of the gateway: the
    addresses moved as ``net_base`` moves them) — the adapter's ``issue`` on a COPY of the state with the same change
    applied (``mutate``, else F3's ``settings_mutate(st, kind, changes)``, else the port only). No state, no mark
    «ждут новых настроек», no ``inlog.mark_issued`` — the settings are not given out yet; only a note
    (``_preview_note``: the device and what its settings carry, no secret) — the application of the same change counts
    it as «выдали» (П5 З-2: else its own device waits for new settings after every change made through its tunnel);
    a «Выдать» of the device before that application takes the note away (П8, ``_preview_forget``).
    ``ref`` None — the device whose address in a pool of ``kind`` is ``who.ip``. The same access as ``issue`` (secrets:
    the panel's session)."""
    from . import issue_os
    st = copy.deepcopy(state.load())
    if model.inbound(st, kind) is None:
        raise KaskadError(tr("gw_ops.err.not_installed", kind=registry.title(kind)))
    changes = dict(changes or {})
    base = changes.pop("base", None)                   # F6: the settings after «Сменить сеть шлюза» (Address, DNS)
    if base is not None:                               # foreign input: the same checks as ``net_base`` (F6)
        if not isinstance(base, str) or not base.strip():
            raise KaskadError(tr("gw_addr.err.base_format", value=repr(base)), code=USAGE, data={"field": "base"})
        try:
            base = addr.new_base(base)
        except StateError as e:
            raise KaskadError(str(e), code=USAGE, data={"field": "base"})
        if base != addr.base(st):
            _net_checks(runner, st, base)              # taken on the node, a key of an exit in it — 409 as F6
    # the device first, by the addresses of now: a change of the network moves them (F6)
    if ref:
        d = model.get_device(st, ref)
    else:
        ip = (who or {}).get("ip") if isinstance(who, dict) else None
        d = model.find_device_by_ip(st, ip) if ip else None
        pools = set(addr.KIND_POOLS.get(kind) or ())
        if d is None or not any((d.get("ips") or {}).get(p) == ip for p in pools):
            raise KaskadError(tr("gw_pending.err.preview_device"), code=USAGE)
    fn = mutate or globals().get("settings_mutate") or _preview_port
    fn(st, kind, changes)
    if base is not None and base != addr.base(st):
        with addr.using(st):
            _net_mutate(st, addr.base(st), base)
    d = model.get_device(st, d["id"])
    if kind not in (d.get("access") or []):
        raise KaskadError(tr("gw_ops.err.not_granted_hint", kind=kind, id=d["id"]))
    if kind not in model.enabled_kinds(st) or registry.get(kind) is None:      # as ``issue`` (REV-3315-g1 З-4)
        raise KaskadError(tr("gw_ops.err.not_usable", kind=registry.title(kind)))
    os_name = os_name or d.get("platform") or "other"
    if os_name not in model.PLATFORMS:
        raise KaskadError(tr("gw_ops.err.platform", platforms=", ".join(model.PLATFORMS)), code=USAGE)
    with addr.using(st), issue_os.issued_for("panel"):
        got = adapter(kind).issue(st, secrets.load(), d, os_name)
    util.log_tr("gw_pending.log.preview", name=d["name"], id=d["id"], kind=kind, os=os_name)   # secrets left: a line
    _preview_note(kind, d["id"], st)
    if isinstance(got, dict) and isinstance(got.get("items"), list):
        items = list(got["items"])
    else:
        items = [got] if isinstance(got, dict) else list(got or [])
    items = [dict(it) for it in items]
    for it in items:
        for k in ("format", "title", "body", "filename", "notes"):
            if it.get(k) is None:
                it[k] = [] if k == "notes" else ""
    return {"ok": True, "preview": True, "device": d["id"], "name": d["name"], "kind": kind, "os": os_name,
            "items": items}


# ── F6 (3.3.7.15, полоса Г): «Сеть шлюза» /16 — одно действие, «Вернуть прежнюю сеть» ─────────────────────────────
# SPEC-R1 §3 F6 (PLAN-n13 Ш4 А, В8, В10, Е2, Е3). The network is changed only here (``gw enable --base`` refuses it
# while devices hold addresses — ``_enable_base``); the write goes through ``pending.guard`` with ``scope «base»``
# (F7: the administrator in the old or the new network waits for a confirmation, any live wait — 409). The last change —
# ``settings-last.json["base"]`` (the file of F3): {"id", "at", "old": {"base", "dns"}, "new": {"base", "dns"}, "what":
# ["net"], "hard": true, "marked": {kind: [device ids]}}. No CLI and no MCP (SPEC-R1 решение 13): the panel and tests.

NET_SCOPE = "base"                                      # = pending.BASE; the key of settings-last.json
NET_MARK_KINDS = ("wg", "awg1", "awg2", "awg3")         # the Address and the DNS are in the settings given out
NET_PPP_KINDS = ("sstp", "l2tp", "pptp")                # MikroTik: its script holds the route to the DNS .0.1
NET_SUGGEST_LAST = 254                                  # suggest_base past the candidates: 10.80.0.0/16 … 10.254.0.0/16


def _net_ver(st):
    """``ver`` of «Сеть шлюза» (§3 «Общее», З-3): the hash of the base."""
    from . import policy_ops as pops
    return pops.ver_of({"base": str(addr.base(st))})


def _in_net(ip, network):
    import ipaddress
    try:
        return ipaddress.IPv4Address(str(ip or "").split("/")[0]) in network
    except ValueError:
        return False


def _exit_addrs(runner):
    """[(IPv4Address, name)] of the AmneziaWG/WireGuard keys of the pool (both roles): their Address — the parser's
    default for a key without one — as the parser's ``awg_gw_net`` reads it; -> (list, warnings). A network of the
    gateway holding one would cut that exit (the legs that run are in ``addr.node_networks`` anyway, as /32)."""
    import ipaddress
    lines = [ln for r in exits.ROLES for ln in exits.pool_lines(r) if ln.startswith("awg://")]
    if not lines:
        return [], []
    try:
        parser = exits.load_parser(runner)
    except KaskadError as e:
        return [], [tr("gw_net.base.keys_unchecked", err=e)]
    default = getattr(parser, "_AWG_DEFAULT_ADDR", None)
    ids = exits.kept_ids()
    out = []
    for line in lines:
        try:
            rec = exits._awg_record(parser, line[6:])
            lab = parser.pool_label(line) or {}
        except Exception:                               # noqa: BLE001 — a foreign line: the pool says its problem
            continue
        name = exits.term_text(lab.get("name")) or exits.pool_id(line, ids)
        for part in str(rec.get("address") or default or "").split(","):
            try:
                ip = ipaddress.ip_interface(part.strip()).ip
            except ValueError:
                continue
            if ip.version == 4:
                out.append((ip, name))
    return out, []


def suggest_base(runner, st=None, nets=None, keys=None):
    """Е3: the first network for the gateway free on this node — of ``addr.BASE_CANDIDATES``, then 10.80–254.0.0/16 —
    not the current one, overlapping nothing of the node (Docker, another VPN, a leg) and holding no Address of a key
    of an exit. -> «10.78.0.0/16» or None. Never raises (a hint)."""
    import ipaddress
    try:
        st = state.load() if st is None else st
        cur = addr.base(st)
        nets = addr.node_networks(runner) if nets is None else nets
        keys = _exit_addrs(runner)[0] if keys is None else keys
    except (KaskadError, OSError, ValueError):
        return None
    for c in list(addr.BASE_CANDIDATES) + ["10.%d.0.0/16" % x for x in range(80, NET_SUGGEST_LAST + 1)]:
        n = ipaddress.IPv4Network(c)
        if n == cur or addr.overlaps(runner, n, nets) or any(ip in n for ip, _ in keys) or addr.home_clash(n):
            continue
        return c
    return None


def _net_marks(st):
    """{kind: rows of ``_reissue_devices``} whose settings given out carry the network of the gateway (F1 «ждут новых
    настроек», hard ``net``): WireGuard and AmneziaWG — every device of the kind (its Address and DNS); SSTP, L2TP and
    PPTP — MikroTik only (the route to the DNS .0.1 is in its script: inbounds/native.py); the rest reconnect by
    themselves."""
    out = {}
    installed = set(model.installed_kinds(st))
    for kind in NET_MARK_KINDS + NET_PPP_KINDS:
        if kind not in installed:
            continue
        rows = _reissue_devices(st, kind)
        if kind in NET_PPP_KINDS:
            rows = [r for r in rows if r.get("platform") == "mikrotik"]
        if rows:
            out[kind] = rows
    return out


def _net_mutate(st, old, new):
    """The network ``old`` -> ``new`` in ``st`` (IPv4Network /16 both): ``net.base``; ``net.dns`` when it was .0.1 of
    the old one; the addresses of the devices by the same shift (``addr.rebase``); ``subnet`` of every installed kind
    (a copy of ``addr.subnet``, Д4); a forward to an address of the old network — the same shift (it points at a
    device). -> {"base": {"old", "new"}, "forwards": [{"proto", "port", "old", "new"}]}."""
    import ipaddress
    g = model.gw(st)
    dns_old = g["net"].get("dns")
    g["net"]["base"] = str(new)
    if not dns_old or dns_old == str(old.network_address + 1):
        g["net"]["dns"] = str(new.network_address + 1)
    addr.rebase(st, str(old), str(new))
    for kind in model.installed_kinds(st):
        if kind in addr.KIND_SUBNET and isinstance(g["inbounds"].get(kind), dict):
            g["inbounds"][kind]["subnet"] = addr.subnet(kind, st)
    moved = []
    for f in st.get("forwards") or []:
        if isinstance(f, dict) and _in_net(f.get("to"), old):
            a = ipaddress.IPv4Address(str(f["to"]))
            to = str(new.network_address + (int(a) - int(old.network_address)))
            moved.append({"proto": f.get("proto"), "port": f.get("port"), "old": f["to"], "new": to})
            f["to"] = to
    return {"base": {"old": str(old), "new": str(new)}, "forwards": moved}


def _net_lines(st0, marks, moved):
    """«Что изменится» of a change of the network (SPEC F6): who does not connect until his settings are given again,
    who reconnects by himself, the forwards that move."""
    lines = []
    for kind, rows in marks.items():
        key = "gw_net.base.what.mikrotik" if kind in NET_PPP_KINDS else "gw_net.base.what.reissue"
        lines.append(tr(key, title=registry.title(kind), who=_who(_reissue_names(rows))))
    # Н-4 (REV-3315-g2): SSTP/L2TP/PPTP of MikroTik only are in ``marks`` — the kind is «reconnect by themselves» only
    # while some device of it is not marked
    rest = [k for k in model.installed_kinds(st0) if k in addr.KIND_SUBNET and k not in NET_MARK_KINDS
            and not (k in marks and len(marks[k]) >= len(_reissue_devices(st0, k)))]
    if rest:
        lines.append(tr("gw_net.base.what.reconnect", titles=", ".join(registry.title(k) for k in rest)))
    if moved:
        lines.append(tr("gw_net.base.what.forwards", items=", ".join("%s :%s → %s" % (m["proto"], m["port"], m["new"])
                                                                     for m in moved)))
    return lines


def _net_checks(runner, st0, new):
    """The refusals of a new network before any lock (SPEC F6): something of the node in it (``addr.overlaps``: Docker,
    another VPN, a leg kawgN) or the Address of a key of an exit (as gw_net of the parser) — ConflictError with the
    list and ``data.suggest_base`` (Е3). -> (the networks of the node, the keys, warnings) for the plan."""
    nets = addr.node_networks(runner)
    keys, warns = _exit_addrs(runner)
    why = addr.overlaps(runner, new, nets)
    if why:
        raise ConflictError(tr("gw_ops.err.base_busy", base=new, why="; ".join(why[:3])),
                            data={"why": "busy", "base": str(new), "taken": why[:10],
                                  "suggest_base": suggest_base(runner, st0, nets, keys)})
    inside = [(ip, name) for ip, name in keys if ip in new]
    if inside:
        ip, name = inside[0]
        raise ConflictError(tr("gw_net.base.exit_key", base=new, name=name, addr=ip),
                            data={"why": "exit_key", "base": str(new), "keys": [str(i) for i, _ in inside][:10],
                                  "suggest_base": suggest_base(runner, st0, nets, keys)})
    return nets, keys, warns


def _net_plan(runner, st0, new, who):
    """«Что изменится» of ``net_base`` (nothing written): ``changed`` {"base": {"old", "new"}}, ``dns``, ``reissue``
    {kind: rows}, ``lines``, ``warnings`` (192.168.0.0/16 — В10, a popular home /24 — Е2, the administrator hit — F7),
    ``need_yes`` (devices holding addresses, 192.168.0.0/16, the hit). -> (plan, the state after — for
    ``pending.guard`` only, never in an answer)."""
    from . import pending
    cur = addr.base(st0)
    _nets, _keys, warnings = _net_checks(runner, st0, new)
    st_new = copy.deepcopy(st0)
    with addr.using(st_new):
        done = _net_mutate(st_new, cur, new)
    marks = _net_marks(st0)
    changed = {"base": {"old": str(cur), "new": str(new)}}
    need_yes = any(d.get("ips") for d in model.devices(st0))
    if new == addr.WIDE_HOME:
        warnings.append(tr("gw_net.base.wide_home"))
        need_yes = True
    clash = addr.home_clash(new) if new != addr.WIDE_HOME else []    # Н-4: its home /24 — said by «wide_home» already
    if clash:
        warnings.append(tr("gw_net.base.home", nets=", ".join(clash)))
    hit = pending.admin_hit(st0, st_new, NET_SCOPE, who, changed)
    hit_text = (pending.hit_text(hit, NET_SCOPE) or "") if hit else ""     # also alone (CONTRACT добавка 4)
    if hit:
        warnings.append(hit_text)
    if hit and not _last_can_write():                   # CONTRACT-pending-3315, добавка 3: guard refuses 409
        warnings.append(tr("gw_ops.settings.no_undo", title=tr("gw_pending.base"), err=settings_last_path()))
    return {"scope": NET_SCOPE, "title": tr("gw_pending.base"), "changed": changed,
            "dns": {"old": addr.dns_ip(st0), "new": addr.dns_ip(st_new)}, "reissue": marks,
            "forwards": done["forwards"], "lines": _net_lines(st0, marks, done["forwards"]), "warnings": warnings,
            "admin_hit": hit or None, "hit_text": hit_text, "need_yes": bool(need_yes or hit)}, st_new


def net_view(runner):
    """«Настройки › Шлюз › Сеть шлюза» (GET /api/gw/net), read-only (runs ``ip``): {"base", "dns", "default",
    "candidates": [{"base", "current", "free", "why"}], "taken": [{"net", "why"}] — what of the node holds private
    networks (Docker, another VPN, legs, keys of exits), "suggest_base", "last" (the change «Вернуть прежнюю сеть»
    undoes, or None), "pending" (F7), "ver"}."""
    import ipaddress
    from . import pending
    st = state.load()
    cur = addr.base(st)
    nets = addr.node_networks(runner)
    keys, warns = _exit_addrs(runner)
    cands = []
    for c in addr.BASE_CANDIDATES:
        n = ipaddress.IPv4Network(c)
        why = addr.overlaps(runner, n, nets) + [tr("gw_net.base.why_key", name=name, addr=ip) for ip, name in keys
                                                if ip in n]
        cands.append({"base": c, "current": n == cur, "free": n != cur and not why, "why": why[:3]})
    taken = [{"net": str(n), "why": w} for n, w in nets if any(n.overlaps(r) for r in addr.RFC1918)]
    taken += [{"net": "%s/32" % ip, "why": tr("gw_net.base.why_key", name=name, addr=ip)} for ip, name in keys]
    last = _last_read().get(NET_SCOPE)
    can = bool(last) and (last.get("new") or {}).get("base") == str(cur)
    if can:                                             # REV-3315-g3 Г3-1: never a button that always refuses —
        try:                                            # the old network outside RFC 1918 only under its own wait
            odd = not addr.rfc1918(addr.parse_base((last.get("old") or {}).get("base")))
        except StateError:
            odd = False                                 # a broken record: the undo says «nothing», as before
        live = pending.read() if odd else None
        can = not odd or (isinstance(live, dict) and live.get("scope") == NET_SCOPE and bool(last.get("id"))
                          and live.get("id") == last.get("id"))
    return {"ok": True, "base": str(cur), "dns": addr.dns_ip(st), "default": addr.DEFAULT_BASE, "candidates": cands,
            "taken": taken, "suggest_base": suggest_base(runner, st, nets, keys),
            "last": {"old": last["old"].get("base"), "new": last["new"].get("base"), "at": last.get("at"),
                     "id": last["id"]} if can else None,
            "pending": pending.view(), "ver": _net_ver(st), "warnings": warns}


def net_base(runner, base, dry=False, yes=False, who=None, ver=None):
    """F6 «Сменить сеть шлюза» (SPEC-R1 §3 F6): ``base`` — a /16 inside RFC 1918 (``addr.new_base``). Checks before any
    lock: something of the node or a key of an exit in it — refused with ``data.suggest_base``; 192.168.0.0/16 and a
    popular home /24 — warnings. ``dry`` — the plan only; ``need_yes`` without ``yes`` — ConflictError with the plan.
    The write — one ``_change`` inside ``pending.guard(scope «base»)`` (F7), then ``_renet`` and the marks of F1; the
    record «Вернуть прежнюю сеть» first (settings-last ``base``). ``ver`` — of ``net_view`` (the panel; another — 409
    ``object_changed``); ``who`` — the administrator (F7)."""
    from . import pending
    from . import policy_ops as pops
    st0 = state.load()
    try:
        new = addr.new_base(base)
    except StateError as e:
        raise KaskadError(str(e), code=USAGE, data={"field": "base"})
    cur = addr.base(st0)
    now_ver = _net_ver(st0)
    if ver is not None and ver != now_ver:
        raise pops.object_changed(now_ver)
    head = {"ok": True, "scope": NET_SCOPE, "old": str(cur), "ver": now_ver}
    if new == cur:
        return dict(head, base=str(cur), changed={}, reissue={}, warnings=[], pending=None, id=None, dry=bool(dry),
                    text=tr("gw_net.base.same", base=cur))
    plan, st_new = _net_plan(runner, st0, new, who)
    if dry:
        return dict(head, base=str(new), dry=True, plan=plan)
    if plan["need_yes"] and not yes:
        what = " ".join(plan["lines"] + plan["warnings"])
        raise ConflictError(tr("gw_net.base.need_yes", old=cur, new=new, what=what), data={"need": "yes", "plan": plan})
    aid = "a-" + os.urandom(4).hex()
    pid, warns = aid, []
    if not _last_can_write():                           # no record — no «Вернуть прежнюю сеть»; admin hit — guard 409
        pid, warns = None, [tr("gw_net.base.last_failed", err=settings_last_path())]
    last = {"id": aid, "at": int(time.time()), "old": {"base": str(cur), "dns": addr.dns_ip(st0)},
            "new": {"base": str(new), "dns": plan["dns"]["new"]}, "what": ["net"], "hard": True,
            "marked": dict((k, [r["id"] for r in rows]) for k, rows in plan["reissue"].items())}

    def apply():
        prev = _last_read().get(NET_SCOPE) if pid else None
        if pid:
            try:
                _last_write(NET_SCOPE, last)
            except OSError as e:                        # the wait would have nothing to undo: nothing is applied
                raise KaskadError(tr("gw_ops.settings.last_refused", title=plan["title"], err=util.redact(str(e))),
                                  code=CONFLICT)
        try:
            return _net_apply(runner, cur, new, ver, last if pid else None, aid)
        except BaseException:
            if pid:
                _last_put_back(NET_SCOPE, prev)
            raise

    out = pending.guard(runner, st0, {"changed": plan["changed"], "id": pid, "st_new": st_new}, scope=NET_SCOPE,
                        who=who, apply=apply)
    out.setdefault("pending", None)
    out["plan"] = plan
    out["warnings"] = list(plan["warnings"]) + list(out.get("warnings") or []) + warns
    if out.get("pending"):
        out["text"] = " ".join(t for t in (out.get("text"), tr("gw_ops.settings.pending_note")) if t)
    return out


def _net_apply(runner, old, new, ver, last, aid):
    """The write of ``net_base``: one ``_change`` (``ver`` and the base checked again under the lock), then what holds
    the old network goes (``_renet``) and the marks «ждут новых настроек» (F1, hard ``net``, the id ``aid`` —
    «Вернуть прежнюю сеть» takes them off). ``last`` — the record written before (None — no record): its ``marked``
    follows what is marked now."""
    from . import reissue

    def mutate(st, sec):
        if ver is not None and _net_ver(st) != ver:     # З-3: another tab changed it first — nothing is written
            from . import policy_ops as pops
            raise pops.object_changed(_net_ver(st))
        if addr.base(st) != old:                        # changed between the plan and the lock (a restore, CLI)
            from . import policy_ops as pops
            raise pops.object_changed(_net_ver(st))
        return _net_mutate(st, old, new)

    with gsys.hold():                   # REV-3315-g3 Г3-2: Ctrl+C after the write never cuts the steps after it
        done, res = _change(runner, mutate)
        util.log_action("GW net base: %s -> %s (%s)" % (old, new, aid))
        try:
            st = state.load()
            cut, warns = _renet(runner, old, st) if model.enabled(st) else ({}, [])
            marks = _net_marks(st)
            given = {}
            try:
                for kind, rows in marks.items():
                    reissue.mark(kind, [r["id"] for r in rows], ["net"], True, by=aid, st=st)
                    given[kind] = _preview_given(kind, st)      # П5 З-2: «Скачать мои новые настройки» of this network
                    if given[kind]:
                        reissue.issued(kind, given[kind], st=st)
            except (OSError, ValueError) as e:
                util.log_tr("gw_ops.log.reissue_mark", err=e)
            # «marked» keeps the given one: «Вернуть прежнюю сеть» marks it again (it holds the new network)
            ids = dict((k, [r["id"] for r in rows]) for k, rows in marks.items())
            if last is not None and ids != last.get("marked"):
                try:
                    _last_write(NET_SCOPE, dict(last, marked=ids))
                except OSError as e:
                    util.log_tr("gw_ops.log.last_failed", err=e)
            marks = dict((k, v) for k, v in ((k, [r for r in rows if r["id"] != given.get(k)])
                                             for k, rows in marks.items()) if v)
            rows = _net_rows(marks)
            text = tr("gw_net.base.applied", old=old, new=new)
            if rows:
                text += " " + tr("gw_net.base.reissue", who=_who(_reissue_names(rows)))
            return {"ok": True, "scope": NET_SCOPE, "base": str(new), "old": str(old), "dns": addr.dns_ip(st),
                    "changed": {"base": done["base"]}, "forwards": done["forwards"], "reissue": marks, "cut": cut,
                    "warnings": list((res or {}).get("warnings") or []) + warns, "apply": res, "id": aid, "text": text}
        except Exception as e:                          # noqa: BLE001 — Н-2 (REV-3315-g2): the network is new already
            # an error out of here would make ``apply`` put the record back and ``pending.guard`` drop the wait: the
            # new network without its timer and without «Вернуть прежнюю сеть». Said; the wait and the record stay.
            # ``after_failed`` — the flag of the panel (CONTRACT-pending-3315 добавка 6); no record — no button named;
            # the DNS — of the new network written (Г3-4), never read again here
            _after_failed(e)                            # the text says it (once: not in ``warnings`` again)
            key = "gw_net.base.after_failed" if last is not None else "gw_net.base.after_failed_no_undo"
            return {"ok": True, "scope": NET_SCOPE, "base": str(new), "old": str(old),
                    "dns": addr.dns_ip({"gateway": {"net": {"base": str(new)}}}),
                    "changed": {"base": done["base"]}, "forwards": done["forwards"], "reissue": {}, "cut": {},
                    "warnings": list((res or {}).get("warnings") or []), "apply": res, "id": aid, "after_failed": True,
                    "text": tr(key, old=old, new=new, err=type(e).__name__)}


def _after_failed(e):
    """Н-2 (REV-3315-g2): a step after a made write of F3/F6 failed — the journal gets its type and its words
    (redacted, short); the answer says the type only."""
    util.log_tr("gw_ops.log.after_failed", err="%s: %s" % (type(e).__name__, util.redact(str(e))[:200]))


def _net_rows(marks):
    """The devices of ``marks`` once each (a device of WireGuard and SSTP is one name)."""
    seen, out = set(), []
    for rows in marks.values():
        for r in rows:
            if r["id"] not in seen:
                seen.add(r["id"])
                out.append(r)
    return out


def _renet(runner, old, st):
    """After the write of another network (SPEC F6): what still holds an address of ``old`` goes — its connections
    (``conntrack -D -s <old>``, as ``net.flush_conntrack``), the SAs of IKEv2 (``ipsec.terminate``), the sessions of PPP
    (``accel-cmd terminate if <ppp> soft``) and of OpenConnect (``ocserv.disconnect``); OpenVPN and WireGuard/AmneziaWG
    came up on the new network in their ``apply``. Never raises: the change is made. -> ({what: n}, warnings)."""
    import re as _re
    out, warns = {}, []
    kinds = set(model.installed_kinds(st))

    def step(what, fn):
        try:
            n = fn()
            if n is not None:
                out[what] = n
        except Exception as e:                          # noqa: BLE001 — said, the rest goes on
            warns.append(tr("gw_net.base.cut_failed", what=what, err=type(e).__name__))

    def conntrack():
        if not runner.which("conntrack"):
            return None
        res = runner.run(["conntrack", "-D", "-s", str(old)], timeout=30)
        m = _re.search(r"(\d+) flow entr", (res.err or "") + (res.out or ""))
        return int(m.group(1)) if m else 0

    def ikev2():
        from .inbounds import ipsec
        n = 0
        for sa in ipsec.list_sas(runner):
            vips = sa.get("remote-vips") or []
            if sa.get("name") == ipsec.CONN_IKEV2 and sa.get("uniqueid") and any(_in_net(v, old) for v in vips) \
                    and ipsec.terminate(runner, sa):
                n += 1
        return n

    def ppp():
        from .inbounds import accel
        n = 0
        for s in accel.sessions(runner):
            if s.get("ifname") and _in_net(s.get("ip"), old):
                accel.cli(runner, "terminate", "if", s["ifname"], "soft")
                n += 1
        return n

    def ocserv():
        ad = adapter("ocserv")
        logins = sorted(set(s["login"] for s in (ad.status(runner).get("sessions") or [])
                            if s.get("login") and _in_net(s.get("ip"), old)))
        ad.disconnect(runner, logins)
        return len(logins)

    step("conntrack", conntrack)
    if "ikev2" in kinds:
        step("ikev2", ikev2)
    if kinds & set(NET_PPP_KINDS):
        step("ppp", ppp)
    if "ocserv" in kinds:
        step("ocserv", ocserv)
    return out, warns


def net_base_undo(runner, pending=None):
    """«Вернуть прежнюю сеть» (F6; F7 ``expire`` with ``pending=<its record>``): the last change of the network undone
    in one ``_change`` (the addresses of the devices back by the same shift), its marks taken off — the devices given
    the new settings meanwhile get a hard mark again; the record goes. Only while the network is the one of that
    change (else «moved» — the record goes, nothing written); the old network taken by something of the node now —
    refused. By hand (the panel) — under ``pending.lock``: a live wait of another kind — 409 ``pending_busy``, as
    ``pending.guard`` refuses a change of the network (Н-1: its timer reverts a port or MTU, not the network); the live
    wait of «base» goes only once the network is back (a failed undo leaves the wait and its timer); the old network
    outside RFC 1918 (an older version took any private /16) — refused (Н-6: a new write of the network is RFC 1918
    only), but under the live wait of that very change — brought back as its timer would (REV-3315-g3 Г3-1). The
    timer of F7 holds the lock itself, undoes only its own id and brings back the network that was, whatever
    it is (it is the way back of an administrator cut off)."""
    from . import pending as gpending
    if pending is not None:
        return _net_undo(runner, pending)
    with gpending.lock():                               # the order: pending.lock, then the state lock (F7 С-2)
        cur = gpending.read()
        if cur is not None and cur.get("scope") != NET_SCOPE:
            raise gpending.busy(cur)
        out = _net_undo(runner, None, live=cur)
        live = gpending.view()
        if isinstance(live, dict) and live.get("scope") == NET_SCOPE:
            gpending.disarm(runner)                     # С-3: after «Вернуть прежнюю сеть» by hand no wait is left
        return out


def _net_undo(runner, pending, live=None):
    """``live`` — the live wait of «base» (``pending.read``; by hand only): a refusal by hand keeps the record for its
    timer; the wait of this very change — the undo by hand does what its timer would (REV-3315-g3 Г3-1)."""
    from . import reissue
    last = _last_read().get(NET_SCOPE)
    if not last:
        raise KaskadError(tr("gw_net.base.undo_nothing"), code=USAGE)
    if pending is not None and pending.get("id") != last.get("id"):
        raise ConflictError(tr("gw_net.base.undo_changed"), data={"why": "changed"})
    try:
        old = addr.parse_base(last["old"].get("base"))
        new = addr.parse_base(last["new"].get("base"))
    except StateError:                                  # a record of somebody's hands: nothing to undo by it
        _last_put_back(NET_SCOPE, None)
        raise KaskadError(tr("gw_net.base.undo_nothing"), code=USAGE)
    own = isinstance(live, dict) and bool(last.get("id")) and live.get("id") == last.get("id")    # Г3-1: as its timer
    if pending is None and not own and not addr.rfc1918(old):     # Н-6: by hand — RFC 1918 only, as a new write
        if not live:                                    # never possible by hand: «Вернуть прежнюю сеть» goes
            _last_put_back(NET_SCOPE, None)
        raise KaskadError(tr("gw_net.base.undo_odd", old=old), code=USAGE, data={"why": "rfc1918", "base": str(old)})
    st0 = state.load()
    cur = addr.base(st0)
    if cur != new:
        _last_put_back(NET_SCOPE, None)
        raise ConflictError(tr("gw_net.base.undo_moved", now=cur), data={"why": "moved", "now": str(cur)})
    why = addr.overlaps(runner, old)
    if why:
        raise ConflictError(tr("gw_ops.err.base_busy", base=old, why="; ".join(why[:3])),
                            data={"why": "busy", "base": str(old), "taken": why[:10]})

    def mutate(st, sec):
        if addr.base(st) != new:                        # changed between the check and the lock
            raise ConflictError(tr("gw_net.base.undo_moved", now=addr.base(st)),
                                data={"why": "moved", "now": str(addr.base(st))})
        return _net_mutate(st, new, old)

    try:
        done, res = _change(runner, mutate)
    except ConflictError as e:
        if (e.data or {}).get("why") == "moved":
            _last_put_back(NET_SCOPE, None)
        raise
    st = state.load()
    cut, warns = _renet(runner, new, st) if model.enabled(st) else ({}, [])
    again = {}
    try:
        for kind, ids in (last.get("marked") or {}).items():
            if kind not in model.INBOUND_KINDS or not isinstance(ids, list):
                continue
            kept = set(reissue.marked_by(kind, last["id"]))     # never given the new settings: the old ones work again
            reissue.drop_by(kind, last["id"], st=st)
            have = set(r["id"] for r in _reissue_devices(st, kind))
            back = [i for i in ids if isinstance(i, str) and i in have and i not in kept]
            if back:
                reissue.mark(kind, back, ["net"], True, st=st)
                again[kind] = back
    except (OSError, ValueError) as e:
        util.log_tr("gw_ops.log.reissue_mark", err=e)
    _last_put_back(NET_SCOPE, None)
    util.log_action("GW net base undo: %s -> %s (%s)" % (new, old, last["id"]))
    names = dict((d.get("id"), d) for d in _reissue_devices_all(st))
    rows = [names[i] for i in sorted(set(i for ids in again.values() for i in ids)) if i in names]
    text = tr("gw_net.base.undone", old=old, new=new)
    if rows:
        text += " " + tr("gw_net.base.reissue", who=_who(_reissue_names(rows)))
    return {"ok": True, "scope": NET_SCOPE, "base": str(old), "old": str(new), "dns": addr.dns_ip(st),
            "undone": {"base": {"old": str(new), "new": str(old)}}, "forwards": done["forwards"], "id": last["id"],
            "reissue": again, "cut": cut, "warnings": list((res or {}).get("warnings") or []) + warns, "apply": res,
            "text": text}


def _reissue_devices_all(st):
    """Every device as a row of ``_reissue_devices`` (its name and its person) — for the words of the undo."""
    users = dict((u.get("id"), u.get("name")) for u in model.users(st))
    return [{"id": d.get("id"), "name": d.get("name") or d.get("id"), "platform": d.get("platform") or "other",
             "user": d.get("user"), "user_name": users.get(d.get("user")) or ""} for d in model.devices(st)]
