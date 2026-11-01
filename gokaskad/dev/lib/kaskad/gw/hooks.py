"""``kaskadctl gw hook <kind> up|down <identity> <ip>`` — daemons tell the core about client sessions
(spec §3.4, §3.6, §7).

* Dynamic kinds (IKEv2: strongSwan gives addresses from its pool): ``up`` puts ``<ip> : <mark of the
  device>`` into gw_route at once, ``down`` takes it out. The lease is kept in
  /run/kaskad/gw/leases.json, so every later render of the table (nft.apply, the watchdog, the exits
  watchdog) keeps the element until ``down`` (or a reboot: /run is tmpfs, sessions are gone too).
* Other kinds (ocserv connect/disconnect, ...): only the session record, for ``gw sessions``.

``identity`` is the device login (EAP identity, ocserv user; PPTP: ``<login>-pptp``); ``login@realm`` is
accepted. The address
must be inside the gateway base (dynamic kinds: inside the kind's subnet). An unknown login gets no
element — gw_pre blocks unknown addresses anyway. Leases change under a lock of their own; the element
is written under the state lock when it is free within a few seconds (so an apply running at that
moment cannot drop it), otherwise without it — the next render takes the lease from the file.
"""

import contextlib
import fcntl
import ipaddress
import json
import os
import re

from .. import paths, state, util
from ..errors import USAGE, KaskadError, LockedError
from ..i18n import tr
from . import addr, model

DYNAMIC_KINDS = ("ikev2",)
ACTIONS = ("up", "down")
LOCK_WAIT = 3.0                      # a daemon waits for its hook: short
_IDENT = re.compile(r"^[A-Za-z0-9._@+=:-]{1,128}$")


def leases_path():
    return os.path.join(paths.gw_run_dir(), "leases.json")


@contextlib.contextmanager
def _leases_lock():
    d = paths.gw_run_dir()
    util.ensure_dir(d, 0o700)                          # /run/kaskad above it stays passable (nginx → the panel)
    fd = os.open(os.path.join(d, "leases.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def load_leases():
    raw = util.read_text(leases_path())
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    return dict((k, v) for k, v in doc.items() if isinstance(v, dict))


def _save_leases(doc):
    util.atomic_write(leases_path(), util.json_text(doc), 0o600)


PPTP_SUFFIX = "-pptp"                 # PPTP logs in as <login>-pptp (inbounds/accel.py)


def _device_by_identity(st, identity, kind=None):
    d = model.find_device_by_login(st, identity)
    if d is None and "@" in identity:
        d = model.find_device_by_login(st, identity.split("@", 1)[0])
    if d is None and kind == "pptp" and identity.endswith(PPTP_SUFFIX):
        d = model.find_device_by_login(st, identity[:-len(PPTP_SUFFIX)])
    if d is None:
        d = next((x for x in model.devices(st) if x.get("id") == identity), None)
    return d


def leases_for(st):
    """[(ip, device)] of the dynamic leases that may be in gw_route: the inbound is enabled, the device
    exists, has the kind in its access and the address is inside the kind's subnet."""
    out = []
    doc = load_leases()
    enabled = model.enabled_kinds(st)
    for kind in DYNAMIC_KINDS:
        if kind not in enabled:
            continue
        try:
            net = ipaddress.IPv4Network(addr.subnet(kind, st))
        except KaskadError:
            continue
        for ip, rec in sorted((doc.get(kind) or {}).items()):
            d = next((x for x in model.devices(st) if x.get("id") == (rec or {}).get("device")), None)
            try:
                ok = ipaddress.IPv4Address(ip) in net
            except ValueError:
                ok = False
            if ok and d is not None and kind in (d.get("access") or []):
                out.append((ip, d))
    return out


def sessions(kind=None):
    """[{"kind", "device", "identity", "ip", "since"}] recorded by hooks."""
    out = []
    for k, recs in sorted(load_leases().items()):
        if kind and k != kind:
            continue
        for ip, rec in sorted(recs.items()):
            out.append({"kind": k, "ip": ip, "device": rec.get("device"), "identity": rec.get("identity"),
                        "since": rec.get("since")})
    return out


def _check(kind, action, identity, ip):
    if kind not in model.INBOUND_KINDS:
        raise KaskadError(tr("gw_hooks.bad_kind", kind=repr(kind)), code=USAGE)
    if action not in ACTIONS:
        raise KaskadError(tr("gw_hooks.bad_action", action=repr(action)), code=USAGE)
    if not _IDENT.match(str(identity or "")):
        raise KaskadError(tr("gw_hooks.bad_identity", identity=repr(identity)), code=USAGE)
    try:
        return str(ipaddress.IPv4Address(str(ip).split("/", 1)[0]))
    except ValueError:
        raise KaskadError(tr("gw_hooks.bad_ip", ip=repr(ip)), code=USAGE)


@contextlib.contextmanager
def _maybe_state_lock():
    """The state lock if it is free within LOCK_WAIT seconds (yields True), else go on without it."""
    cm = state.locked(timeout=LOCK_WAIT)
    try:
        cm.__enter__()
    except LockedError:
        yield False
        return
    try:
        yield True
    finally:
        cm.__exit__(None, None, None)


def hook(kind, action, identity, ip, runner):
    """Handle one event. Returns {"ok", "kind", "action", "ip", "device", "mark"?, "element"?}.
    Raises KaskadError (code 2) on bad arguments, code 1 if the address is outside the gateway."""
    ip = _check(kind, action, identity, ip)
    from . import exits, net
    with _maybe_state_lock() as locked:
        st = state.load()
        if not addr.in_base(st, ip):
            raise KaskadError(tr("gw_hooks.outside_base", ip=ip, base=addr.base(st)))
        if kind in DYNAMIC_KINDS and ipaddress.IPv4Address(ip) not in ipaddress.IPv4Network(addr.subnet(kind, st)):
            raise KaskadError(tr("gw_hooks.outside_subnet", ip=ip, kind=kind, subnet=addr.subnet(kind, st)))
        dev = _device_by_identity(st, identity, kind)
        if dev is not None and kind not in (dev.get("access") or []):
            dev = None                    # the daemon let in someone this inbound was not given to
        res = {"ok": True, "kind": kind, "action": action, "ip": ip, "device": dev["id"] if dev else None}
        with _leases_lock():
            doc = load_leases()
            recs = doc.setdefault(kind, {})
            old = recs.get(ip) or {}
            if action == "up" and dev is not None:
                # strongSwan re-assigns an online lease: "up" of the new SA can come before "down" of the
                # old one with the same address — count, and let the address go only at zero
                n = int(old.get("count") or 0) + 1 if old.get("device") == dev["id"] else 1
                recs[ip] = {"device": dev["id"], "identity": identity, "since": old.get("since") or util.now_iso(),
                            "count": n}
            elif action == "down" and old and int(old.get("count") or 1) > 1:
                old["count"] = int(old.get("count") or 1) - 1
                res["still"] = old["count"]
            else:
                recs.pop(ip, None)
            if not recs:
                doc.pop(kind, None)
            _save_leases(doc)
        if kind in DYNAMIC_KINDS and model.enabled(st) and "still" not in res:
            from ..rules import apply as rules_apply
            if action == "up" and dev is not None and model.device_active(st, dev) \
                    and kind in model.enabled_kinds(st):
                pool_exits, snap = exits.current(st)
                dec = exits.decision(st, snap, pool_exits)
                t = exits.target(st, dev, dec)                              # the pin of the device (§2.2)
                res["mark"] = t["mark"]
                # wave 3: «Russia directly» for this address too (gw_rd), or certainly not (a lease reused). Only under
                # the state lock (SEC-s1-r2 П4): without it ``st`` may be older than a change that took the choice or
                # the consent ⚠РФ away a moment ago — the address waits for the collector (``net.update_map`` puts it
                # into gw_rd by ``rd()`` within its tick), never «directly» by a stale state. Q2 (§3): the Russian
                # target (``exits.ru_target``) puts it into gw_rd («direct») OR into gw_rx (its Russian group)
                rt = exits.ru_target(st, dev, dec)
                if rt is not None and not locked:
                    rt, res["rd_wait"] = None, True
                res["ru_direct"] = bool(rt is not None and rt["step"] == "direct")
                rx = rt["mark"] if rt is not None and rt["step"] != "direct" else None
                if rx is not None:
                    res["ru_mark"] = rx
                # stage 1 (§2.3, Б1): the last step «block» of its group keeps Russia open — gw_live + gw_ruonly
                ru_open = res["ru_direct"] or rx not in (None, exits.MARK_BLOCK)
                res["ru_only"] = bool(ru_open and t["mark"] == exits.MARK_BLOCK and t["step"] == "last_block")
                # SEC-s1-q2 П-1: gw_rd and gw_rx in one transaction, BEFORE the address is live — and seen: gw_rx that
                # may keep another owner's mark keeps the address closed until the collector's tick
                if net.set_ru(runner, st, ip, res["ru_direct"], rx, rx=net.ru_groups(dec.get("policy"))):
                    res["element"] = net.set_element(runner, st, ip, t["mark"], ruonly=res["ru_only"])
                else:
                    res["element"], res["ru_failed"] = net.del_element(runner, st, ip), True
                    util.log_tr("gw_hooks.log.ru_failed", ip=ip)
            else:
                res["element"] = net.del_element(runner, st, ip)
                # Б10: a stale gw_rx entry would lead the next owner home — out of it; no Russian group — no gw_rx in
                # the table (one left by a failed apply is a difference ``verify`` repairs), no call (REV-s1-q2 П-2)
                net.set_ru(runner, st, ip, False, None, rx=net.ru_groups(model.peek(st).get("exit_policy")))
        if action == "up" and dev is None:
            res["ok"] = False
            util.log_tr("gw_hooks.log.unknown_client", kind=kind, identity=identity, ip=ip)
    return res


def forget(kind=None):
    """Drop recorded leases (of one kind or all) — e.g. when an inbound is uninstalled."""
    with _leases_lock():
        doc = load_leases()
        if kind:
            doc.pop(kind, None)
        else:
            doc = {}
        _save_leases(doc)
