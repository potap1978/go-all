"""The operations of the exit policy for the panel, the CLI and MCP (SPEC-stage1-exits §7.9, §3.1; SPEC-groups §8.5,
§8.9; T7). One place for every write the forms and the buttons over one object make, the HTTP layer only checks the
bodies (``rev``/``ver`` present) and maps the refusals.

The version of the policy ``rev`` (§3.1, gw/rev.py): a write of the list of §7.9 compares the ``rev`` it was given
with ``rev.note(st)`` UNDER the lock of its own write (``ops._change`` — the same transaction as the write; a dry run —
its own ``state.transaction``, the lock only for the count); another — ``PolicyChanged`` (409 ``policy_changed``,
``data.rev``), nothing written. ``want_rev`` None — no check (CLI and MCP without ``--rev``; the panel always sends it).
A success answers the new ``rev``; a dry run (``dry=True``) answers the ``rev`` it counted on and the same ``moves`` the
write would (one function ``impact`` on one ``st``), writing nothing but the count.

The version of an object ``ver`` (§7.9 «Версия объекта»): 12 hex of sha1 of the canonical JSON of the object — the
route of a person and of a device, the default route of the node, ``probes.speed``, the zone of the node, the whole
``gateway.notify``, ``gateway.metrics_max_mb``. Compared under the lock of the write; another — ``ObjectChanged`` (409
``object_changed``, ``data.ver``). ``want_ver`` None — no check (Python callers: CLI, MCP, the bot, the SSH menu).

``moves`` — what the change does to the devices (§7.9, §7): the decision of ``assign`` on the state before and on the
state after, with the same pins, states and clock (so only the effect of the change shows):

    {"total": n, "by_group": {gid: {"total", "by_tier": {tid: n}, "last_direct", "last_block"}},
     "direct": n, "block": n,                       # devices whose own route leaves the groups
     "ru": {"total": n, "by_group": {...}, "direct": n, "none": n},  # T15 Q2: the Russian sites (``exits.ru_target``)
     "devices": [{"device", "name", "from": place, "to": place,           # ≤ DEVICES_SHOWN, place = {group, exit,
                  "ru"?: True, "ru_from": place, "ru_to": place}]}        # tier, step}; «ru» — its Russian side
``total`` counts both: a device whose Russian sites change their path (another Russian group, «напрямую» after the
deletion of its group) moves too (REV-s1-q1: «Устройства не сменят путь» was said then). ``ru.direct`` — Russia from
the IP of the server, ``ru.none`` — «Россия напрямую» no longer acts for it (its Russia goes with its route).

Refusals are ``Refused`` (a KaskadError with the word of the API ``key`` and its HTTP status; the exit code of the CLI
is ``code``); ``panel.routes.core_error`` tells them as they are.
"""

import copy
import hashlib
import json
import os
import random
import time

from .. import paths, state, util
from ..errors import CONFLICT, ERROR, USAGE, KaskadError
from ..i18n import tr, trn
from . import exits, model, policy as P, rev as R

DEVICES_SHOWN = 50
PROBE_KB = {"xray": 12.2, "awg": 6.6}   # KB of one probe on the wire (T0 14.10: VLESS ≈ 105, AWG ≈ 57 MB/day at 10 s)
SPEED_KEYS = ("max_mb", "max_s", "only_quiet", "budget_day_mb", "budget_month_gb")
QUOTA_KEYS = ("period", "limit_gb", "reset_day", "warn_pct", "action")
MANUAL_STATES = {"standby": "standby", "maintenance": "maintenance", "restore": None}
METRICS_MB = (8, 1024)                  # gateway.metrics_max_mb: the size of metrics.db (T3), MB
TG_REBUILD_GAP = 60                     # s: the bot's proxy is rebuilt not more often (SE §2.9)
TG_UNIT = "kaskad-tg-rebuild"


class Refused(KaskadError):
    """A refusal with its own word for the API: ``key`` (``policy_changed``, ``gone`` …) and the HTTP ``status``."""

    def __init__(self, message, key, status=400, code=USAGE, data=None):
        KaskadError.__init__(self, message, code=code, data=data)
        self.key = key
        self.status = status


def _refuse(key, status, code, data=None, text=None, **kw):
    return Refused(text if text is not None else tr("gw_policy_ops." + key, **kw), key, status, code, data)


def policy_changed(cur):
    return _refuse("policy_changed", 409, CONFLICT, {"rev": cur})


def object_changed(cur):
    return _refuse("object_changed", 409, CONFLICT, {"ver": cur})


def moves_changed():
    """409 ``moves_changed`` (T10, REV-s1-t10-sec №1): the write would move other devices than the dry run the person
    agreed to showed — the panel set a route into the group while the question was up (routes are outside ``rev``)."""
    return _refuse("moves_changed", 409, CONFLICT, {"moves_changed": True})


def moves_sig(moves):
    """The signature of ``moves`` (a dry run, a write): what «что изменится» says — the numbers and the devices with
    their places. None (not counted) — its own signature: never equal to a counted one."""
    text = json.dumps(moves if isinstance(moves, dict) else None, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def rev_required():
    return _refuse("rev_required", 400, USAGE)


def ver_required():
    return _refuse("ver_required", 400, USAGE)


def bad_field(field, text=None):
    return _refuse("field", 400, USAGE, {"field": field}, text=text, field=field)


def gone(what, oid):
    """404 ``gone`` (§7.9 «Повтор DELETE», «Объекта нет»): ``data.gone`` and the id under ``exit`` / ``group``; an exit
    keeps the words of 3.3.5 («Такого выхода уже нет — обновите страницу»)."""
    return _refuse("gone", 404, CONFLICT, {"gone": True, what: oid},
                   text=tr("gw_ops.exit.gone") if what == "exit" else None)


# ── versions ───────────────────────────────────────────────────────────────

def ver_of(value):
    """``ver`` of an object (§7.9): 12 hex of sha1 of its canonical JSON (None and {} are different objects)."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _rev_int(v):
    """``rev`` of a request as an int; anything else — USAGE (the HTTP layer checks it is there)."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise bad_field("rev")
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        raise bad_field("rev")


def check_rev(st, want_rev):
    """Inside the lock of the write: ``rev.note(st)`` — the version now; another than ``want_rev`` — PolicyChanged.
    -> the version now."""
    cur = R.note(st)
    want = _rev_int(want_rev)
    if want is not None and want != cur:
        raise policy_changed(cur)
    return cur


def check_ver(cur, want_ver):
    if want_ver is not None and str(want_ver) != cur:
        raise object_changed(cur)
    return cur


# ── the pool, the decision, the moves ──────────────────────────────────────

def _subs():
    try:
        from . import exitsubs
        return exitsubs.load()
    except Exception:                                   # noqa: BLE001 — the hint of a subscription is only a hint
        return None


def pool_rows():
    """The rows of the pool for the decisions here: files only, no parser (``exits.pool_fallback``: ids, kinds,
    marks) — both files (T15): a Russian exit is placed and checked in its group too."""
    try:
        return exits.pool_fallback(roles=exits.ROLES)
    except (OSError, ValueError, KaskadError):
        return []


KNOWN_LOCK_WAIT = 2.0               # s: the lock of the pool for ``known_ids`` of ``after_pool`` (the collector — 0)


def known_ids(wait=None, on_busy=None):
    """The ids of both files of the pool for ``policy.prune_gone`` by role: {"abroad": [ids] | None, "ru": [ids]};
    None — nothing is taken out of any group: what is read may not be the pool (REV-3371-3 З-1) — a file is there but
    cannot be read; subs.json is there but broken (the ids of its exits are not known); the lock of the pool is not
    free within ``wait`` s (None — ``KNOWN_LOCK_WAIT``; the files are read under it, as install.sh writes them,
    egress_pool_locked; busy — ``on_busy()`` is called). upstream.txt there but with no line (a rollback of install.sh
    writes it as «: >» then the lines again; the last foreign key deleted) — «abroad» None: the foreign groups keep
    their ids, the Russian ones are pruned by upstream-ru.txt (REV-3371-pre З-2). A missing file is an empty pool."""
    fd = _pool_lock(KNOWN_LOCK_WAIT if wait is None else wait, on_busy)
    if fd is None:
        return None
    try:
        for p in (paths.egress_up(), R.ru_pool_path()):
            if os.path.lexists(p) and util.read_text(p) is None:
                return None
        if not exits.kept_ids_ok():
            return None
        ids = R.pool_ids()
        up = util.read_text(paths.egress_up())
        return {"abroad": None if up is not None and not up.strip() else list(ids.get("abroad") or []),
                "ru": list(ids.get("ru") or [])}
    finally:
        os.close(fd)


def _pool_lock(wait, on_busy=None):
    """The lock of the pool (``tofu.lock_path`` — the one of egress_pool_locked), taken within ``wait`` s (0 — one
    try): its fd (the caller closes it), or None — busy (``on_busy()`` is called then) or no lock file can be made."""
    import fcntl
    from . import tofu
    p = tofu.lock_path()
    try:
        util.ensure_run_dir(os.path.dirname(p))
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError:
        return None
    end = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= end:
                os.close(fd)
                if on_busy is not None:
                    on_busy()
                return None
            time.sleep(0.1)


def pool_ids():
    """Every id of both files of the pool (``rev.pool_ids``)."""
    ids = R.pool_ids()
    return list(ids.get("abroad") or []) + list(ids.get("ru") or [])


def _base(st, pool=None, subs=None):
    """The effective policy of ``st`` with every exit of the pool placed — what a write starts from (none stored —
    «main» in «compat», as the readers see it)."""
    return P.load(st, pool_rows() if pool is None else pool, _subs() if subs is None else subs)


def _base_w(st, pool=None, subs=None):
    """``_base`` for a WRITE: a node with no policy stored gets its first one now, as ``gw enable`` would give it
    (``policy.ensure_initial``: «Стандарт» on a node born on stage 1, «Как раньше» on an updated one) — a write before
    the first enable never stores «compat» on a new node. On an updated node the first policy is the view the readers
    had («main» in «compat», every exit placed): the same policy for the forms — no ``rev`` of its own (the count
    before it is taken first), so a name or the speed test there changes no ``rev`` and a form keeps the ``rev`` it read
    (проверка T7, С-1/П-2). A new node's «Стандарт» is a change: the caller's count gives it its ``rev``."""
    if P.stored(st) is None and not state.born_new(st):
        R.note(st)
        view = _base(st, pool, subs)
        if not isinstance(st.get("gateway"), dict):
            st["gateway"] = {}
        st["gateway"]["exit_policy"] = copy.deepcopy(view)
        R.resign(st)
        return view
    P.ensure_initial(st)
    return _base(st, pool, subs)


def _gone_then_rev(st, gid, want_rev, pool=None):
    """The order of a write over one group (§7.9): gone — 404 ``gone``, then ``rev`` — both on the policy as it is,
    BEFORE ``_base_w`` writes a first one (its ``rev`` would never be the one the form read; проверка T7, С-1).
    -> rev now."""
    if P.group(_base(st, pool), gid) is None:
        raise gone("group", gid)
    return check_rev(st, want_rev)


def _quota_hit(eid, quota, now):
    """The quota of ``eid`` is used up now (traffic.json, the period by the zone of the node)."""
    if not isinstance(quota, dict):
        return False
    try:
        from . import pins as PN
        from .. import tz
        doc = PN.traffic_read()
        lim = P.quota_gb(quota)
        return lim is not None and PN.used(doc, eid, quota, now, tz.zone()) >= lim * 1e9
    except Exception:                                   # noqa: BLE001 — an estimate only
        return False


MANUAL_REASONS = ("disabled", "maintenance", "manual_standby")


def _states(st, base, snap, now):
    """{eid: state} of the decision with what ``st`` says about each exit applied — the collector would apply it on
    its next tick (§4.2): off for the gateway / «Обслуживание» — off, «В запас» — standby, «Вернуть» — the state its
    probes give, a quota used up — by its action, a country accepted — no «country»."""
    out = {}
    recs = snap.get("exits") or {}
    for eid, s in (base or {}).items():
        pref = model.exit_pref(st, eid)
        rec = recs.get(eid) if isinstance(recs.get(eid), dict) else {}
        reasons = set(rec.get("reasons") or [])
        if s != "off" or reasons & set(MANUAL_REASONS) or "country" in reasons or "quota" in reasons:
            left = reasons - set(MANUAL_REASONS)
            geo = (rec.get("geo") or {}).get("loc") if isinstance(rec.get("geo"), dict) else None
            if geo and (pref.get("country") or {}).get("expected") == geo:
                left.discard("country")
            if "quota" in left and not _quota_hit(eid, pref.get("quota"), now):
                left.discard("quota")
            if reasons and not left and s in ("off", "standby"):
                s = "ok" if rec.get("alive") or rec.get("last_ok") else "off"
        if not pref["enabled"] or pref.get("manual") == "maintenance":
            s = "off"
        elif pref.get("manual") == "standby" and s != "off":
            s = "standby"
        q = pref.get("quota")
        if s in ("ok", "degraded", "standby") and _quota_hit(eid, q, now):
            act = (q or {}).get("action", "remove")
            s = "off" if act == "remove" else ("standby" if act == "standby" else s)
        out[eid] = s
    return out


def _decide(st, pool, subs, snap, pins_doc, now, node=None, health=None):
    """The decision of ``assign`` on the files (``node`` — {} or F14 ``{"regroup": {gid}}``; ``health`` — the windows
    of the snapshot for the methods by response, F14; none — «even» as the moves of a change always counted)."""
    from . import assign, collect
    pol = P.load(st, pool, subs)
    dec = exits.decision(st, snap=snap, pool_exits=pool, policy=pol, pins_doc=pins_doc)
    states = _states(st, dec["states"], snap, now)
    devs = collect.devices_by_group(st, pol, states)
    try:
        metrics = collect._metrics_of(st, health or {}, pool)
    except Exception:                                   # noqa: BLE001 — «even» needs none
        metrics = {}
    return assign.assign(devs, states, pol, pins_doc, metrics, node or {}, now)


def _places(st, d, ru=False):
    """{device id: {group, exit, tier, step}} of the active devices by a decision; own routes direct/block —
    {group: None, step: via}. ``ru`` (T15 Q2) — the Russian side instead: the Russian groups of the decision, and
    ``{group: None, step: "direct"}`` for a device whose «Россия напрямую» acts with ``ru_via`` «direct»."""
    from ..rules import apply as rules_apply
    rus = set(g.get("id") for g in (d.get("policy") or {}).get("groups") or [] if isinstance(g, dict)
              and g.get("role") == "ru")
    out = {}
    for gid, g in (d.get("groups") or {}).items():
        if (gid in rus) != bool(ru):
            continue
        pins = g.get("pins") or {}
        for dev, step in (g.get("steps") or {}).items():
            p = pins.get(dev)
            if step == "exit":
                out[dev] = {"group": gid, "exit": (p or {}).get("exit"),
                            "tier": (p or {}).get("tier") or g.get("active_tier"), "step": "exit"}
            else:
                out[dev] = {"group": gid, "exit": None, "tier": None, "step": step}
    for dev in model.devices(st):
        if dev.get("id") in out or not model.device_active(st, dev):
            continue
        if ru:
            if rules_apply.device_rd(st, dev) and model.ru_via_of(st, dev)["via"] == model.RU_VIA_DIRECT:
                out[dev["id"]] = {"group": None, "exit": None, "tier": None, "step": "direct"}
            continue
        via = model.route_of(st, dev).get("via")
        if via in ("direct", "block"):
            out[dev["id"]] = {"group": None, "exit": None, "tier": None, "step": via}
    return out


def _count(out, b, total=True):
    """One move to ``b`` (a place, None — none) into ``out`` (the form of ``moves`` or of its «ru»)."""
    out["total"] += 1 if total else 0
    if b is None:
        out["none"] = out.get("none", 0) + 1
    elif b["group"] is None:
        out[b["step"]] = out.get(b["step"], 0) + 1
    else:
        g = out["by_group"].setdefault(b["group"], {"total": 0, "by_tier": {}, "last_direct": 0, "last_block": 0})
        g["total"] += 1
        if b["step"] == "exit":
            key = b["tier"] or ""
            g["by_tier"][key] = g["by_tier"].get(key, 0) + 1
        else:
            g[b["step"]] = g.get(b["step"], 0) + 1


def impact(before, after, now=None, pool=None):
    """``moves`` of the change ``before`` -> ``after`` (two states; see the module). Files only: the pool without the
    parser, the snapshot, pins.json, traffic.json; no command, no network."""
    from . import pins as PN
    pool = pool_rows() if pool is None else pool
    subs = _subs()
    snap = exits.load_snapshot()
    pins_doc = PN.read()
    now = time.time() if now is None else now
    d0 = _decide(before, pool, subs, snap, pins_doc, now)
    d1 = _decide(after, pool, subs, snap, pins_doc, now)
    d0["policy"], d1["policy"] = P.load(before, pool, subs), P.load(after, pool, subs)
    p0, p1 = _places(before, d0), _places(after, d1)
    r0, r1 = _places(before, d0, ru=True), _places(after, d1, ru=True)
    names = dict((d.get("id"), d.get("name") or "") for d in model.devices(after))
    out = {"total": 0, "by_group": {}, "direct": 0, "block": 0, "devices": []}
    ru = {"total": 0, "by_group": {}, "direct": 0, "none": 0}
    alive = set(d.get("id") for d in model.devices(after) if model.device_active(after, d))
    for dev in sorted(set(p0) | set(p1) | set(r0) | set(r1)):
        a, b = p0.get(dev), p1.get(dev)
        moved = a != b and b is not None
        if moved:
            _count(out, b, total=False)                     # ``total`` — once a device, below
        ra, rb = r0.get(dev), r1.get(dev)
        ru_moved = ra != rb and dev in alive                # «none» — its Russia no longer by itself
        if ru_moved:
            _count(ru, rb)
        if moved or ru_moved:
            out["total"] += 1
            if len(out["devices"]) < DEVICES_SHOWN:
                rec = {"device": dev, "name": names.get(dev, ""), "from": a, "to": b}
                if ru_moved:
                    rec.update(ru=True, ru_from=ra, ru_to=rb)
                out["devices"].append(rec)
    out["ru"] = ru
    return out


# ── the two ways of a write ────────────────────────────────────────────────

def _warn_list(res):
    out = []
    for src in (res, (res or {}).get("apply") if isinstance(res, dict) else None):
        if isinstance(src, dict):
            out.extend(w for w in src.get("warnings") or [] if isinstance(w, str))
    return out


def _tg_indexes(st):
    """The lines of the bot's proxy for the comparison of ``run`` — «direct» when the bot goes without it (SPEC-tg-proxy
    Р6: from «Напрямую» to a group and back — a rebuild even when both have no lines)."""
    try:
        from . import tgproxy
        res = tgproxy.indexes(st)
        return P.TG_DIRECT if res.get("direct") else res["indexes"]
    except Exception:                                   # noqa: BLE001 — no pool, no policy: nothing to rebuild
        return None


def run(runner, mutate, want_rev=None, dry=False, check=True, what="", want_moves=None):
    """A write of the list of §7.9: ``mutate(st)`` -> dict of the answer (it raises a Refused/KaskadError to refuse).
    ``dry`` — in a ``state.transaction`` on a copy: ``{ok, dry, rev, moves, warnings}``, nothing written but the count;
    else through ``ops._change`` (the gateway on — applied): ``{ok, rev, moves, warnings, …mutate's}``. ``check`` —
    compare ``want_rev`` (False: the writes outside the list — names, objects with ``ver``). ``want_moves`` — the
    ``moves_sig`` of the dry run a person agreed to (the question of the CLI, T10): the write moves other devices —
    ``moves_changed``, nothing written (routes are outside ``rev``: the panel may have set one meanwhile)."""
    from . import ops
    if dry:
        with state.transaction() as st:
            cur = check_rev(st, want_rev) if check else R.note(st)
            new = copy.deepcopy(st)
            out = mutate(new) or {}
            moves = impact(st, new)
        res = {"ok": True, "dry": True, "rev": cur, "moves": moves, "warnings": list(out.pop("warnings", []))}
        res.update(dict((k, v) for k, v in out.items() if k not in res))
        return res
    box = {}

    def m(new, sec):
        cur = check_rev(new, want_rev) if check else R.note(new)
        before = copy.deepcopy(new)
        out = mutate(new) or {}
        box["moves"] = safe_impact(before, new)
        if want_moves is not None and moves_sig(box["moves"]) != want_moves:
            raise moves_changed()
        box["rev"] = R.note(new)
        box["was"] = cur
        box["tg"] = (_tg_indexes(before), _tg_indexes(new))
        return out
    out, res = ops._change(runner, m)
    tg = box.get("tg") or (None, None)
    if tg[0] != tg[1] and tg[1] is not None:
        tg_rebuild(runner)
    if what:
        util.log_action("GW policy: %s (rev %s -> %s)" % (what, box.get("was"), box.get("rev")))
    warnings = list(out.pop("warnings", [])) + _warn_list(res)
    ans = {"ok": True, "rev": box["rev"], "moves": box["moves"], "warnings": warnings}
    ans.update(dict((k, v) for k, v in out.items() if k not in ans))
    return ans


def tg_rebuild(runner, now=None):
    """The group of «Прокси для API Telegram» changed (SE §2.9): ``gokaskad --egress-tg-rebuild`` in the background
    (``systemd-run``), not more often than once in TG_REBUILD_GAP s — a later one is delayed, never lost (a timer of
    systemd, one at a time). No pool, or neither the unit of the bot's proxy (kaskad-egress) nor the mark of its turning
    off (``.bot-off``: «Напрямую» or a group without keys, REV-s1-t11 С5 — the unit is gone then, and only this rebuild
    turns the proxy on again; SPEC-tg-proxy Р6) — nothing. A failure is a line in the journal. -> "now" | "later" |
    None."""
    if runner is None or not os.path.isfile(paths.egress_up()) or not (
            os.path.exists(os.path.join(paths.systemd_dir(), exits.EGRESS_UNIT))
            or os.path.exists(os.path.join(paths.egress_dir(), ".bot-off"))):
        return None                                     # no pool or no proxy of the bot (never built): nothing to rebuild
    stamp = os.path.join(paths.run_dir(), "tg-rebuild.at")
    now = time.time() if now is None else now
    try:
        last = float((util.read_text(stamp) or "0").strip() or 0)
    except ValueError:
        last = 0.0
    wait = int(TG_REBUILD_GAP - (now - last)) + 1 if 0 <= now - last < TG_REBUILD_GAP else 0
    argv = ["systemd-run", "--collect", "--quiet"]
    if wait > 0:
        argv += ["--on-active=%ds" % wait, "--unit=%s-later" % TG_UNIT]
    else:
        argv += ["--no-block", "--unit=%s-%d" % (TG_UNIT, int(now))]
    argv += [exits._script(runner), "--egress-tg-rebuild"]
    try:
        r = runner.run(argv, timeout=15)
        if wait <= 0 and r.ok:
            util.ensure_run_dir(paths.run_dir())
            util.atomic_write(stamp, "%d\n" % int(now), 0o600)
        if not r.ok and wait <= 0:
            util.log_tr("gw_policy_ops.log.tg_rebuild_failed", rc=r.rc)
        return ("later" if wait > 0 else "now") if r.ok or wait > 0 else None
    except Exception as e:                              # noqa: BLE001 — the bot keeps its config, the next change retries
        util.log_tr("gw_policy_ops.log.tg_rebuild_failed", rc=type(e).__name__)
        return None


def after_pool(what, new_ids=(), gid=None):
    """Step 3 of a write through the script of the pool (§3.1 «Записи через скрипт пула»): its own
    ``state.transaction`` — the new lines placed (``policy.place_new``; placed already by the collector — nothing; ``gid``
    — the hint of ``exit_add --group`` for a line the preview did not see) and ``rev.note``. «По возможности» (проверка
    20, З-1): the pool has changed already, so a lock held longer than 15 s by another apply is no refusal — None and a
    line in the journal (the next ``current``/``note`` counts it, the collector places the line). A nested lock is an
    error. -> rev | None."""
    from ..errors import LockedError, NestedLockError
    if not state.exists():
        return None                                     # no state.json (a pool of the bot only): nothing to count
    try:
        with state.transaction() as st:
            if gid:
                for i in new_ids:
                    if P.group_of(P.stored(st) or {}, i) is None:
                        model.set_exit(st, i, group=gid)
            raw = P.stored(st)
            if raw is not None:
                doc = P.normalize(raw)
                gone = P.prune_gone(doc, known_ids())      # KT3-1: a key out of the pool — out of its group at once
                if (new_ids and P.place_new(doc, pool_rows(), P.hints_of(st, _subs()))) or gone:
                    st["gateway"]["exit_policy"] = doc
                # T-q Н-state: the hint «group» of a deleted line, or of a group no longer there, goes too
                model.prune_exit_groups(st, [g.get("id") for g in doc.get("groups") or [] if isinstance(g, dict)],
                                        drop=[e for e, _g in gone])
            return R.note(st)
    except NestedLockError:
        raise
    except LockedError:
        util.log_tr("gw_policy_ops.log.rev_later", what=what)
        return None


def safe_impact(before, after):
    """``impact`` of a write that goes on whatever it says: an error of the estimate is a line in the journal and
    None, never a refused write (a dry run tells its error)."""
    try:
        return impact(before, after)
    except Exception as e:                              # noqa: BLE001
        util.log_action("GW policy: moves not counted (%s)" % type(e).__name__)
        return None


def current_rev():
    """The version now for an answer outside a write (``rev.current`` on a fresh read)."""
    try:
        return R.current(state.load())[0]
    except KaskadError:
        return None


# ── reads ──────────────────────────────────────────────────────────────────

def _labels(doc, lang=None):
    """A copy of the policy with ``label`` at the groups and the tiers (§3.1 «Имена по умолчанию»)."""
    out = copy.deepcopy(doc)
    for g in out.get("groups") or []:
        if not isinstance(g, dict):
            continue
        g["label"] = P.label(g, lang)
        for i, t in enumerate(g.get("tiers") or []):
            if isinstance(t, dict):
                t["label"] = P.tier_label(g, i, lang)
    return out


def probe_mb_day(doc, grp, kind, pool=None):
    """≈ MB of probes a day of one exit of ``kind`` in the group (the period of its checks, §3.2)."""
    try:
        p = P.period(doc, grp, kind, pool)
    except Exception:                                   # noqa: BLE001
        p = None
    if not p:
        return None
    return round(PROBE_KB.get(kind, PROBE_KB["awg"]) * 86400.0 / p / 1000.0, 1)


def forecast(doc, pool):
    """{probe_mb_day: {gid: MB}, speed_gb_month} of the policy (§7.1: «≈ N МБ проб в сутки»)."""
    by = {}
    speed = 0.0
    kinds = P.pool_kinds(pool)
    sp = ((doc.get("probes") or {}).get("speed") or {})
    for g in doc.get("groups") or []:
        if not isinstance(g, dict):
            continue
        tot = 0.0
        ids = P.exits_of(g)
        for eid in ids:
            mb = probe_mb_day(doc, g, kinds.get(eid, "awg"), pool)
            tot += mb or 0.0
        by[g["id"]] = round(tot, 1)
        chk = (g.get("checks") or {}).get("speed") or {}
        if chk.get("enabled") and ids:
            every = chk.get("every_h") or 6
            speed += len(ids) * (30 * 24.0 / every) * float(sp.get("max_mb") or 10) / 1000.0
    cap = sp.get("budget_month_gb")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap > 0:
        speed = min(speed, float(cap))
    return {"probe_mb_day": by, "speed_gb_month": round(speed, 2)}


def view(st=None, lang=None):
    """``GET /api/gw/policy`` and the reads of the CLI and MCP: the data and the version of ONE read (``rev.current``,
    the pool read after it, §3.1). -> {rev, policy (labels), presets, forecast}."""
    rev, st = R.current(state.load() if st is None else st)
    pool = pool_rows()
    doc = _base(st, pool)
    # T15 Q3 (SPEC-groups §8.9, Б12): the presets of the Russian role too — «Дом» (§6.6)
    presets = dict((role, dict((p, P.preset_fields(p, role)) for p in P.PRESETS[role] if p != P.CUSTOM))
                   for role in P.ROLES)
    out = _labels(doc, lang)
    if _no_before(st):
        out["hint_seen"] = True             # новому узлу «как раньше» нет: первый enable даст «Стандарт» — плашки нет
    return {"ok": True, "rev": rev, "policy": out, "presets": presets, "forecast": forecast(doc, pool)}


def groups(st=None, lang=None):
    """``kaskadctl gw groups`` and MCP ``exit_groups_list`` (§7.6–7.8, T10): the groups of ONE read (``rev.current``)
    with what acts now — the role, the exits in the order of the tiers, the tier the collector holds active and the
    last step (``pins.json``), the devices without an exit (``live.no_exit`` of a fresh ``live.json``; stale — None:
    not known), the names ``name`` and ``label`` (§3.1). Files only: no command, no network.
    -> {ok, rev, groups: [...], no_exit, live}."""
    from . import live, pins as PN
    rev, st = R.current(state.load() if st is None else st)
    pool = pool_rows()
    doc = _base(st, pool)
    held = (PN.read().get("groups") or {})
    lv, age = live.read()
    fresh = live.fresh(age)
    ne = ((lv or {}).get("no_exit") or {}) if fresh and isinstance(lv, dict) else None
    ne_ru = ((lv or {}).get("no_exit_ru") or {}) if ne is not None else {}   # T15 Q6: the Russian groups apart
    out = []
    for g in doc.get("groups") or []:
        if not isinstance(g, dict) or not g.get("id"):
            continue
        gid = g["id"]
        tiers = [t for t in g.get("tiers") or [] if isinstance(t, dict)]
        hp = held.get(gid) if isinstance(held.get(gid), dict) else {}
        at = hp.get("active_tier")
        ti = next((i for i, t in enumerate(tiers) if t.get("id") == at), None)
        src = ne_ru if g.get("role") == "ru" else ne
        steps = [v for v in (src or {}).values() if isinstance(v, dict) and v.get("group") == gid]
        since = [v["since"] for v in steps if isinstance(v.get("since"), (int, float)) and not isinstance(v.get("since"), bool)]
        out.append({
            "id": gid, "name": g.get("name", ""), "label": P.label(g, lang), "role": g.get("role", "abroad"),
            "preset": P.preset_of(g), "fail": g.get("fail") if g.get("fail") in model.FAILS else "block",
            "flap_standby": bool(g.get("flap_standby")), "exits": P.exits_of(g),
            # PLAN-order Ш3: the way of choosing of each tier — «order» manual: the first working exit of the list,
            # ``method`` kept for the way back; «по соединениям» of «main» — neither acts (``per_connection``)
            "per_connection": bool(g.get("per_connection")) and gid == P.MAIN,
            "tiers": [{"id": t.get("id"), "label": P.tier_label(g, i, lang), "exits": list(t.get("exits") or []),
                       "method": t.get("method") if t.get("method") in P.METHODS else "even",
                       "order": "manual" if t.get("order") == "manual" else "auto"}
                      for i, t in enumerate(tiers)],
            "active_tier": at if ti is not None else None,
            "active_tier_label": P.tier_label(g, ti, lang) if ti is not None else None,
            "last_step": hp.get("last_step") if hp.get("last_step") in model.FAILS else None,
            "no_exit": len(steps) if ne is not None else None, "no_exit_since": min(since) if since else None,
            "tg_proxy": doc.get("tg_proxy_group", P.MAIN) == gid, "new_exits": doc.get("new_exits_group", P.MAIN) == gid})
    return {"ok": True, "rev": rev, "groups": out, "no_exit": len(ne) if ne is not None else None, "live": fresh}


def _no_before(st):
    """A NEW node with no policy stored yet (before its first ``gw enable``): the readers see «main» in «compat», but
    there is no «как раньше» to keep — the first write or enable gives it «Стандарт» (``ensure_initial``). The banner
    «Оставить как раньше» of §7.1 does not apply (REV-s1-t8m П-2)."""
    return P.stored(st) is None and state.born_new(st)


# ── the form of a group: PUT /api/gw/policy ────────────────────────────────

def _strip_view(doc):
    """What the panel may send back of a read: no ``label``."""
    doc = copy.deepcopy(doc)
    for g in doc.get("groups") or []:
        if isinstance(g, dict):
            g.pop("label", None)
            for t in g.get("tiers") or []:
                if isinstance(t, dict):
                    t.pop("label", None)
    return doc


def _membership(doc, ids):
    """{eid: gid} of the exits of the pool ``ids`` in ``doc``."""
    out = {}
    for eid in ids:
        g = P.group_of(doc, eid)
        out[eid] = g.get("id") if g else None
    return out


def _set_exit(st, eid, **kw):
    """``model.set_exit`` with its errors of the fields as ``Refused`` «field» (§7.9: 400 with ``data.field``)."""
    try:
        return model.set_exit(st, eid, **kw)
    except KaskadError as e:
        errs = (getattr(e, "data", None) or {}).get("errors") or []
        if errs:
            raise Refused(str(e), "field", 400, USAGE, {"field": errs[0].get("field"), "errors": errs})
        raise


def _save(st, doc, prev, pool):
    try:
        new, warnings = P.save(st, doc, pool, prev)
    except KaskadError as e:
        errs = (getattr(e, "data", None) or {}).get("errors") or []
        if errs:
            raise Refused(str(e), "field", 400, USAGE, {"field": errs[0].get("field"), "errors": errs})
        raise
    return new, [w.get("text") or w.get("code") for w in warnings or []]


def set_policy(runner, body, want_rev=None, dry=False):
    """The form of a group card (§7.1): the policy of §3.1 without its names of groups and without ``probes.speed``
    (skipped: renames — ``PUT /api/gw/groups/<gid>``, the speed test — its own form). The list of groups and their
    exits never change here: a group of the saved policy missing — 409 ``group_missing``; an exit in another group —
    400 ``use_move``; a new group — 400 ``field`` (``POST /api/gw/groups``)."""
    if not isinstance(body, dict):
        raise bad_field("policy")
    body = _strip_view(body)

    def mutate(st):
        pool = pool_rows()
        prev = _base_w(st, pool)
        ids = pool_ids()
        have = [g.get("id") for g in prev.get("groups") or [] if isinstance(g, dict)]
        sent = body.get("groups")
        if not isinstance(sent, list):
            raise bad_field("groups")
        sent_ids = [g.get("id") for g in sent if isinstance(g, dict)]
        for gid in have:
            if gid not in sent_ids:
                raise _refuse("group_missing", 409, CONFLICT, {"group": gid}, name=P.label(P.group(prev, gid)))
        for i, gid in enumerate(sent_ids):
            if gid not in have:
                raise bad_field("groups[%d].id" % i, tr("gw_policy_ops.group_new"))
        doc = copy.deepcopy(body)
        for g in doc["groups"]:
            if isinstance(g, dict):
                g["name"] = (P.group(prev, g.get("id")) or {}).get("name", "")
        pr = doc.get("probes") if isinstance(doc.get("probes"), dict) else {}
        pr["speed"] = copy.deepcopy((prev.get("probes") or {}).get("speed"))
        doc["probes"] = pr
        a, b = _membership(prev, ids), _membership(doc, ids)
        for eid in ids:
            if a.get(eid) is not None and b.get(eid) != a.get(eid):
                raise _refuse("use_move", 400, USAGE, {"exit": eid})
        _new, warnings = _save(st, doc, P.stored(st), pool)
        return {"warnings": warnings}
    return run(runner, mutate, want_rev, dry, what="set")


def reset(runner, preset=None, group=None, want_rev=None, dry=False, want_moves=None):
    """«Включить «Стандарт»» and the reset of a group to a preset (§7.1): the fields of the preset into the group
    (``apply_preset``); the tiers, the exits, the name, ``fail`` and the node's ``probes.speed`` stay (проверка 20,
    З-3). Default — «main» and «compat»."""
    gid = group or P.MAIN
    preset = preset or "compat"

    def mutate(st):
        pool = pool_rows()
        _gone_then_rev(st, gid, want_rev, pool)
        doc = _base_w(st, pool)
        g = P.group(doc, gid)
        role = g.get("role", "abroad")
        if preset not in P.PRESETS.get(role, ()) or preset == P.CUSTOM:
            raise bad_field("preset", tr("gw_policy_ops.preset_bad", value=preset,
                                         list=", ".join(p for p in P.PRESETS.get(role, ()) if p != P.CUSTOM)))
        P.apply_preset(g, preset)
        _new, warnings = _save(st, doc, P.stored(st), pool)
        return {"group": gid, "preset": preset, "warnings": warnings}
    return run(runner, mutate, None, dry, check=False, what="reset %s %s" % (gid, preset), want_moves=want_moves)


# ── groups ─────────────────────────────────────────────────────────────────

def _new_gid(doc):
    rng = random.SystemRandom()
    have = set(g.get("id") for g in doc.get("groups") or [] if isinstance(g, dict))
    while True:
        gid = "g-%06x" % rng.randrange(1 << 24)
        if gid not in have:
            return gid


def _dup_check(doc, name, self_id, allow):
    """A duplicate of a SHOWN name of another group (both languages, ``policy.group_duplicates``) — 409
    ``confirm_duplicate`` without ``allow``."""
    dup = P.group_duplicates(doc, name, self_id)
    if dup and not allow:
        raise _refuse("confirm_duplicate", 409, CONFLICT, {"need": "confirm_duplicate", "duplicates": dup}, name=name)


def group_add(runner, name, role="abroad", fail=None, allow_duplicate=False, want_rev=None):
    """«+ Группа» (§7.1): a new group ``g-<6 hex>`` (a foreign one — «Стандарт», a Russian one — «Дом»), its name
    checked (``names.check``), a duplicate of a SHOWN name — 409 ``confirm_duplicate`` without ``allow_duplicate``.
    -> {ok, id, rev}."""
    if role not in P.ROLES:
        raise bad_field("role")
    if fail is not None and fail not in P.FAILS:
        raise bad_field("fail")
    try:
        from .. import names
        clean = names.check(name, "group")
    except KaskadError as e:
        raise Refused(str(e), "field", 400, USAGE, dict(getattr(e, "data", None) or {}, field="name"))
    box = {}

    def mutate(st):
        pool = pool_rows()
        doc = _base_w(st, pool)
        if len(doc.get("groups") or []) >= P.MAX_GROUPS:
            raise _refuse("groups_many", 409, CONFLICT, {"max": P.MAX_GROUPS}, max=P.MAX_GROUPS)
        _dup_check(doc, clean, None, allow_duplicate)
        gid = _new_gid(doc)
        g = P.default_group(gid, role, "ru_home" if role == "ru" else "standard", name=clean)
        if fail is not None:
            g["fail"] = fail
        doc["groups"].append(g)
        _new, warnings = _save(st, doc, P.stored(st), pool)
        box["id"] = gid
        return {"id": gid, "warnings": warnings}
    out = run(runner, mutate, want_rev, what="group add")
    return dict(out, id=box["id"])


def hint_seen():
    """«Оставить как раньше» on the one-time banner of §7.1: ``hint_seen`` only — outside ``rev`` (gw/rev.py), like a
    name: written in a ``state.transaction`` without an apply, so closing the banner neither restarts the gateway nor
    takes the lock of the writes for long (REV-s1-t8-a З-8). Idempotent. -> {ok, hint_seen}."""
    # новый узел до первого enable: «как раньше» нет — ничего не пишем и «Стандарт» здесь не создаём (rev не растёт)
    if _no_before(state.load()):
        return {"ok": True, "hint_seen": True}
    with state.transaction() as st:
        pool = pool_rows()
        doc = _base_w(st, pool)
        if doc.get("hint_seen") is not True:
            doc["hint_seen"] = True
            _save(st, doc, P.stored(st), pool)
            R.note(st)
    util.log_action("GW policy: hint seen")
    return {"ok": True, "hint_seen": True}


def group_rename(runner, gid, name, allow_duplicate=False):
    """The name of a group in place (``Kit.rename``; RN §4.1): without ``rev`` and outside it; ``""`` of «main» — the
    default name. A name changes no config: written in a ``state.transaction`` without an apply. -> {ok, id, name,
    label, old}."""
    with state.transaction() as st:
        pool = pool_rows()
        doc = _base_w(st, pool)
        g = P.group(doc, gid)
        if g is None:
            raise gone("group", gid)
        old = P.label(g)
        nm = name if isinstance(name, str) else ""
        if gid == P.MAIN and not nm.strip():
            clean = ""
        else:
            try:
                from .. import names
                clean = names.check(nm, "group")
            except KaskadError as e:
                raise Refused(str(e), "field", 400, USAGE, dict(getattr(e, "data", None) or {}, field="name"))
            _dup_check(doc, clean, gid, allow_duplicate)
        g["name"] = clean
        new, _w = _save(st, doc, P.stored(st), pool)
        R.note(st)
        ng = P.group(new, gid)
        out = {"ok": True, "id": gid, "name": ng.get("name", ""), "label": P.label(ng), "old": old}
    util.log_action("GW policy: group rename %s" % gid)
    return out


def group_flap_standby(runner, gid, on, want_rev=None, dry=False):
    """«Нестабильный выход — в запас на 15 мин» of one group (§4.3, правка 6, З4-5; D-071): the one field of a group
    the CLI and MCP write (``gw group-flap-standby``, ``group_flap_standby_set``), as a button over an exit; another
    value than the preset's turns the preset «Свой» (``policy.save``, §1.3). Gone — 404 before ``rev``.
    -> {ok, rev, moves, group, flap_standby, preset, warnings}."""
    if not isinstance(on, bool):
        raise bad_field("flap_standby")

    def mutate(st):
        pool = pool_rows()
        _gone_then_rev(st, gid, want_rev, pool)
        doc = _base_w(st, pool)
        g = P.group(doc, gid)
        g["flap_standby"] = on
        new, warnings = _save(st, doc, P.stored(st), pool)
        return {"group": gid, "flap_standby": on, "preset": (P.group(new, gid) or g).get("preset"),
                "warnings": warnings}
    return run(runner, mutate, None, dry, check=False, what="flap_standby %s %s" % (gid, "on" if on else "off"))


# ── F14 «Разложить заново» (SPEC-R1 §1 решения 2–4, §3 F14) ───────────────────

REGROUP_WAIT_S = 60          # s: the collector takes a request within a round (≈ 10 s); the panel waits this long


def _regroup_frozen(grp):
    """Why the layout of ``grp`` is frozen now, or None: the outage of the network of the node (an open ``node_offline``
    in live.json) — every group; of the Russian addresses (``node_ru_offline``) — a Russian group. The process of ops
    has no ``MEM.node``: the same files the collector restores itself from (З-5)."""
    from . import live
    if live.outage_open() is not None:
        return "offline"
    if (grp or {}).get("role") == "ru" and live.ru_outage_open() is not None:
        return "ru_offline"
    return None


def _snap_health(snap):
    """{eid: {"samples", "norm_rtt"}} of the snapshot and its window (/run) — what ``collect._metrics_of`` reads for the
    methods by response; the speed is not kept there (the preview is approximate, §1 решение 4)."""
    out = {}
    for eid, rec in ((snap or {}).get("exits") or {}).items():
        if isinstance(rec, dict):
            w = rec.get("h_win") if isinstance(rec.get("h_win"), dict) else {}
            out[eid] = {"samples": w.get("samples") or [], "norm_rtt": rec.get("norm_rtt") or []}
    return out


def _on_exits(pins, devs):
    """{exit: devices} of ``pins`` ({dev: {"exit"}}) over the devices ``devs`` of the group."""
    out = {}
    for dev in devs:
        e = ((pins or {}).get(dev) or {}).get("exit")
        if e:
            out[e] = out.get(e, 0) + 1
    return out


def regroup_preview(st, gid, now=None):
    """The dry run of «Разложить заново»: ``assign.assign`` on the files with ``node.regroup = {gid}`` — the moves the
    next round of the collector would make in this group, ``before``/``after`` — the devices on each exit of the group.
    Approximate: the round takes the pins in its memory, this — pins.json (equal while the file is fresh). Writes
    nothing."""
    from . import pins as PN
    now = time.time() if now is None else now
    pool = pool_rows()
    snap = exits.load_snapshot()
    pins_doc = PN.read()
    d = _decide(st, pool, _subs(), snap, pins_doc, now, node={"regroup": {gid}}, health=_snap_health(snap))
    g = (d.get("groups") or {}).get(gid) or {}
    devs = sorted(g.get("steps") or {})
    was = ((pins_doc.get("groups") or {}).get(gid) or {}).get("pins") if isinstance(pins_doc, dict) else None
    names = dict((x.get("id"), x.get("name") or "") for x in model.devices(st))
    moves = [{"device": m["device"], "name": names.get(m["device"], ""), "from": m.get("from"), "to": m.get("to"),
              "why": m.get("why")} for m in d.get("moves") or [] if m.get("group") == gid]
    moved = sum(1 for m in moves if m["why"] == "rebalance")   # as the event ``group_regrouped`` counts (П-1)
    return {"moves": moves[:DEVICES_SHOWN], "moved": moved, "also": len(moves) - moved, "before": _on_exits(was, devs),
            "after": _on_exits(g.get("pins"), devs), "approx": True}


def group_regroup(runner, gid, dry=False, now=None):
    """F14 «Разложить заново» of one group (§1 решения 2–4): the night rebalance now — the devices on the active tier
    with a fit exit go back to their «home» exit; the way back to the first tier still waits for ``hold_min``. Refused
    (and in a dry run): the gateway off; no such group (404 ``gone`` — the name of the request file comes only from the
    id of an existing group, so «../x» never reaches the disk); «по соединениям» — nothing to lay out; the layout
    frozen (the node offline; a Russian group without the Russian addresses).

    ``dry`` -> {ok, dry, group, moves, moved (the rebalance only), also (the other moves of the round), before, after,
    approx, text, note}: nothing written. Else the request —
    the file ``<gid>`` in ``paths.gw_regroup_dir()`` (0600, atomic; a repeat — the same file) and at once {ok,
    accepted, group, wait_s, text}: the result comes as the event ``group_regrouped`` of the feed. The policy does not
    change — no ``rev``/``ver``, a repeat is harmless. ``runner`` — the signature of the writes (not used)."""
    st = state.load()
    if not model.enabled(st):
        raise _refuse("regroup_off", 409, CONFLICT)
    grp = P.group(_base(st), gid) if isinstance(gid, str) and P._GID.match(gid) else None
    if grp is None:
        raise gone("group", gid)
    if grp.get("per_connection") and gid == P.MAIN:
        raise _refuse("regroup_nothing", 409, CONFLICT, {"group": gid})
    frozen = _regroup_frozen(grp)
    if frozen:
        raise _refuse("regroup_frozen" + ("_ru" if frozen == "ru_offline" else ""), 409, CONFLICT,
                      {"group": gid, "frozen": frozen})
    note = tr("gw_policy_ops.regroup_note")
    if dry:
        res = regroup_preview(st, gid, now)
        text = trn("gw_policy_ops.regroup_preview", res["moved"]) if res["moved"] else \
            tr("gw_policy_ops.regroup_preview_none")
        return dict(res, ok=True, dry=True, group=gid, text=text, note=note)
    now = time.time() if now is None else now
    util.atomic_write(os.path.join(paths.gw_regroup_dir(), gid), json.dumps({"at": int(now)}) + "\n", 0o600)
    util.log_action("GW policy: regroup %s requested" % gid)
    return {"ok": True, "accepted": True, "group": gid, "wait_s": REGROUP_WAIT_S,
            "text": tr("gw_policy_ops.regroup_accepted"), "note": note}


def tg_proxy_set(runner, gid, want_rev=None, dry=False):
    """«Как бот ходит в Telegram» (SPEC-tg-proxy Р1, Р5): ``exit_policy.tg_proxy_group`` — a foreign group or
    ``policy.TG_DIRECT``; the bot's proxy is rebuilt by ``run`` (its lines changed). A group gone — 404 before ``rev``;
    a Russian group — 400 (``ep.err.tg_proxy_group``). -> {ok, rev, moves, group, warnings}."""
    if not isinstance(gid, str) or not gid:
        raise bad_field("group")
    if gid != P.TG_DIRECT and not P._GID.match(gid):
        raise bad_field("group")

    def mutate(st):
        pool = pool_rows()
        if gid != P.TG_DIRECT:
            _gone_then_rev(st, gid, want_rev, pool)
        else:
            check_rev(st, want_rev)
        doc = _base_w(st, pool)
        if gid != P.TG_DIRECT and (P.group(doc, gid) or {}).get("role", "abroad") != "abroad":
            raise bad_field("group", tr("ep.err.tg_proxy_group"))
        doc["tg_proxy_group"] = gid
        new, warnings = _save(st, doc, P.stored(st), pool)
        return {"group": new.get("tg_proxy_group", P.MAIN), "warnings": warnings}
    return run(runner, mutate, None, dry, check=False, what="tg_proxy %s" % gid)


def _refs(st, gid):
    """Who points at the group: {"users": [ids], "devices": [ids]} (their own routes ``group:<gid>``)."""
    out = {"users": [], "devices": []}
    g = model.peek(st)
    for kind, objs in (("users", g["users"]), ("devices", g["devices"])):
        for o in objs:
            r = o.get("route") if isinstance(o, dict) else None
            if isinstance(r, dict) and model.group_of_route(r) == gid:
                out[kind].append(o.get("id"))
    d = g["routes"].get("default") or {}
    if model.group_of_route(d) == gid:
        out["default"] = True
    for kind, ids in model.ru_via_refs(st, gid).items():         # T15 Q1: «Российские сайты» of a Russian group
        out[kind] += [i for i in ids if i not in out[kind]]
    return out


def _subs_of(gid, subs=None):
    subs = _subs() if subs is None else subs
    rows = subs.get("subs") if isinstance(subs, dict) else subs
    return [s.get("id") for s in rows or [] if isinstance(s, dict) and s.get("group") == gid]


def group_delete(runner, gid, exits_to=None, reassign=None, tg_proxy_to=None, new_exits_to=None, subs_to=None,
                 want_rev=None, dry=False, want_moves=None, exits=None):
    """«Удалить группу» (SPEC-groups §8.5): its exits go to ``exits_to`` (the same role; onto its ``new_exits_tier``),
    the routes that point at it — to ``reassign`` (``direct`` or a group), the group of the bot's proxy, of the new keys,
    of subscriptions — to ``tg_proxy_to``/``new_exits_to``/``subs_to``. A parameter missing where it is needed — 409
    with ``data.exits``/``data.refs``/``data.uses``. «main» — 400; gone — 404 ``gone`` before the check of ``rev``.
    ``exits="drop"`` (решение владельца 8а; a Russian group only) — its keys leave the pool with it instead of moving:
    ``_group_drop``; another value of ``exits``, or ``exits`` with ``exits_to`` — 400 ``field`` «exits».
    -> {ok, rev, moves, refs, uses, warnings}."""
    if gid == P.MAIN:
        raise _refuse("main", 400, USAGE)
    if exits is not None and (exits != "drop" or exits_to):
        raise bad_field("exits")
    if exits == "drop":
        return _group_drop(runner, gid, reassign, want_rev, dry, want_moves)
    box = {}
    mutate = _group_out(gid, box, exits_to, reassign, tg_proxy_to, new_exits_to, subs_to, want_rev)
    if dry or not subs_to:
        return run(runner, mutate, None, dry, check=False, what="group del %s" % gid, want_moves=want_moves)
    # ``subs_to``: the lock of the subscriptions taken FIRST (its order — subscriptions, then state — as their refresh
    # takes them) and held over the write: busy — «занято» with nothing written; the group gone — its subscriptions
    # moved in the same lock, never left on a group that is no more (проверка T7, З-1).
    from . import exitsubs
    with exitsubs._locked():
        out = run(runner, mutate, None, dry, check=False, what="group del %s" % gid, want_moves=want_moves)
        if box.get("subs"):
            _subs_group(box["subs"][0], box["subs"][1])
    return out


def _group_out(gid, box, exits_to=None, reassign=None, tg_proxy_to=None, new_exits_to=None, subs_to=None,
               want_rev=None, drop=None):
    """The write of «Удалить группу» as ``mutate(st)`` of ``run`` (``group_delete``). ``drop`` — a drop of its keys
    (8а, ``_group_drop``; a Russian group only — another, 400 ``field`` «exits» before the check of ``rev``): "plan" —
    step 1 on a copy: the keys of the group the pool has are the ones to drop (``box["drop"]``), off in the copy so the
    ``moves`` count them gone (as ``ops.exit_del`` does); "final" — step 3: the keys left the pool already; a key of the
    group still there (the pool got it between the steps) — 409 ``drop_more`` with ``data.exits``, nothing written. A
    drop needs no ``exits_to`` and is no ``ru_last``; its subscriptions are never moved — ``uses`` «subs» is 409 ``need``
    whatever ``subs_to`` says («сначала удалите подписку»)."""

    def mutate(st):
        pool = pool_rows()
        if drop is not None:
            g0 = P.group(_base(st, pool), gid)
            if g0 is not None and g0.get("role", "abroad") != "ru":
                raise bad_field("exits")
        _gone_then_rev(st, gid, want_rev, pool)         # «Повтор DELETE»: gone before the check of ``rev`` (§7.9)
        doc = _base_w(st, pool)
        g = P.group(doc, gid)
        role = g.get("role", "abroad")
        members = P.exits_of(g)
        live = []
        if drop is not None:
            in_pool = set(e.get("id") for e in pool if isinstance(e, dict) and e.get("role") == "ru")
            live = [e for e in members if e in in_pool]
            if drop == "final" and live:
                raise _refuse("drop_more", 409, CONFLICT, {"exits": live})
            box["drop"] = live
        refs = _refs(st, gid)
        subs = _subs()
        uses = []
        if doc.get("tg_proxy_group") == gid:
            uses.append("tg_proxy")
        if doc.get("new_exits_group") == gid:
            uses.append("new_exits")
        sub_ids = _subs_of(gid, subs)
        if sub_ids:
            uses.append("subs")
        need = {}
        if members and not exits_to and drop is None:
            need["exits"] = members
        if (refs["users"] or refs["devices"] or refs.get("default")) and not reassign:
            need["refs"] = refs
        missing = [u for u, v in (("tg_proxy", tg_proxy_to), ("new_exits", new_exits_to), ("subs", subs_to))
                   if u in uses and (not v or (u == "subs" and drop is not None))]
        if missing:
            need["uses"] = missing
        if members and drop is None and role == "ru" and not any(
                x.get("role") == "ru" and x.get("id") != gid for x in doc["groups"] if isinstance(x, dict)):
            raise _refuse("ru_last", 409, CONFLICT, {"exits": members})
        if need:
            raise _refuse("need", 409, CONFLICT, need)

        def target(v, field, want_role):
            t = P.group(doc, v)
            if t is None or v == gid:
                raise bad_field(field, tr("gw_policy_ops.group_target", field=field, value=v))
            if t.get("role", "abroad") != want_role:
                raise bad_field(field, tr("gw_policy_ops.group_role", field=field, value=v))
            return t
        if members and drop is None:
            t = target(exits_to, "exits_to", role)
            for eid in members:                     # onto its new_exits_tier; a Russian group — each its own (§6.6)
                P.new_tier(t).setdefault("exits", []).append(eid)
        for eid in live if drop == "plan" else ():
            model.set_exit(st, eid, enabled=False)  # the count of the plan: the key gone, as exit_del counts it
        if reassign and (refs["users"] or refs["devices"] or refs.get("default")) and role == "ru":
            if reassign != "direct":
                target(reassign, "reassign", "ru")
            model.reassign_ru_via(st, gid, reassign)    # T15 Q1: to «direct» or another Russian group, explicitly
        elif reassign and (refs["users"] or refs["devices"] or refs.get("default")):
            if reassign != "direct":
                target(reassign, "reassign", "abroad")
            via = "direct" if reassign == "direct" else ("pool" if reassign == P.MAIN else model.GROUP_VIA + reassign)
            gw = model.gw(st)
            for kind, objs in (("users", gw["users"]), ("devices", gw["devices"])):
                for o in objs:
                    if o.get("id") in refs[kind]:
                        o["route"] = dict(o["route"], via=via)
            if refs.get("default"):
                gw["routes"]["default"] = dict(gw["routes"]["default"], via=via)
        if "tg_proxy" in uses:
            target(tg_proxy_to, "tg_proxy_to", "abroad")
            doc["tg_proxy_group"] = tg_proxy_to
        if "new_exits" in uses:
            target(new_exits_to, "new_exits_to", "abroad")
            doc["new_exits_group"] = new_exits_to
        if "subs" in uses:
            target(subs_to, "subs_to", role)
            box["subs"] = (sub_ids, subs_to)
        for eid in members:                              # the hint of a key for the gone group goes too
            p = model.peek(st)["exits"].get(eid)
            if isinstance(p, dict) and p.get("group") == gid:
                p.pop("group", None)
        doc["groups"] = [x for x in doc["groups"] if not (isinstance(x, dict) and x.get("id") == gid)]
        _new, warnings = _save(st, doc, P.stored(st), pool)
        model.prune_exit_groups(st, [x.get("id") for x in doc["groups"] if isinstance(x, dict)])   # T-q Н-state
        return {"refs": {"users": refs["users"], "devices": refs["devices"]}, "uses": uses, "warnings": warnings}
    return mutate


def _group_drop(runner, gid, reassign=None, want_rev=None, dry=False, want_moves=None):
    """«Удалить группу» with its keys (решение владельца 8а): the panel sends it for the last Russian group — its keys
    have nowhere to go — with ``reassign=direct`` (the Russian sites of the people who chose it go from the server
    directly). Three steps, as «Удалить ключ» (``ops.exit_del``; §3.1 «Записи через скрипт пула»):

    1. a short ``state.transaction``: a foreign group — 400 ``field`` «exits»; gone — 404 ``gone``; ``rev`` — 409
       ``policy_changed``; what is missing — 409 ``need`` (``refs`` without ``reassign``; ``uses: ["subs"]`` — its
       subscriptions: nowhere to move them, never deleted silently); the keys to drop — the keys of the group in
       upstream-ru.txt — and ``moves`` with them gone and the group deleted. ``dry`` stops here: {ok, dry, rev, moves,
       drop: [ids], refs, uses, warnings} — nothing written but the count of ``rev``;
    2. the keys out of upstream-ru.txt in ONE write of the script, WITHOUT the state lock (``_drop_ru_keys``); then
       ``after_pool`` — their ids out of the groups, ``rev`` counted;
    3. the group out of the policy (``run``; the people to ``reassign``) — no ``rev`` compared: step 2 changed it, the
       checks of step 1 are made again on the state as it is now.

    Keys first: a key never stays in upstream-ru.txt without its group (with no Russian group left — out of every group,
    seen nowhere). Step 2 failed — nothing written (the script writes the file whole or not at all): the group and its
    keys as they were, the error as it is. Step 3 failed (the state busy, the gateway not applied, the group given to a
    person meanwhile) — the keys are gone, the group stays, empty: the error carries ``data.dropped`` (the ids gone) and
    ``data.rev`` (the version now), and the same DELETE with that ``rev`` finishes it (nothing left to drop); done —
    404 ``gone``. A key the group got between the steps (a subscription of a deleted Russian group: its keys go to the
    first Russian group) — 409 ``drop_more`` at step 3, the repeat drops it too.
    -> {ok, rev, moves, dropped: [ids], refs, uses, warnings}."""
    box = {}
    plan = _group_out(gid, box, reassign=reassign, want_rev=want_rev, drop="plan")
    with state.transaction() as st0:
        cur = R.note(st0)
        new = copy.deepcopy(st0)
        out = plan(new) or {}
        moves = impact(st0, new) if dry else safe_impact(st0, new)
    drop = list(box.get("drop") or [])
    if dry:
        res = {"ok": True, "dry": True, "rev": cur, "moves": moves, "drop": drop,
               "warnings": list(out.pop("warnings", []))}
        res.update(dict((k, v) for k, v in out.items() if k not in res))
        return res
    if want_moves is not None and moves_sig(moves) != want_moves:
        raise moves_changed()
    dropped, warnings = _drop_ru_keys(runner, drop)
    if dropped:
        after_pool("group drop")
    try:
        ans = run(runner, _group_out(gid, {}, reassign=reassign, drop="final"), None, False, check=False,
                  what="group del %s with %d keys" % (gid, len(dropped)))
    except KaskadError as e:
        if dropped:
            e.data = dict(getattr(e, "data", None) or {}, dropped=dropped, rev=current_rev())
            util.log_action("GW policy: group del %s broke off, its keys are gone: %s" % (gid, ", ".join(dropped)))
        raise
    if moves is not None:
        ans["moves"] = moves                    # counted before the keys went: what the person was shown
    ans["dropped"] = dropped
    ans["warnings"] = warnings + list(ans.get("warnings") or [])
    return ans


def _drop_ru_keys(runner, ids):
    """Step 2 of ``_group_drop``: every line of upstream-ru.txt with a key of ``ids`` — and a repeat of it («e-…-20N»:
    never left to take its place) — out in ONE write (``exitsubs.write_pool``: ``--egress-ru-apply``, its legs and gw
    apply, the bot untouched). The file changed meanwhile (code 3) — read and written again (REPLACE_TRIES), then
    ConflictError. A key no longer in the file is gone already. The script failed and the lines are there — its error
    (nothing written); the lines are gone (cut off after its write) — gone, its error a warning. A node with
    subscriptions — the id of the text of every line to subs-excluded.pending (SE §3.8, as ``ops._exit_del_ru``): no
    subscription brings them back. The journal gets the ids, never a key. -> (ids gone, warnings)."""
    from ..errors import ConflictError
    from . import exitsubs
    want = set(ids or ())
    if not want:
        return [], []

    def lines_of():
        cur = exits.pool_lines("ru")
        kept = exits.kept_ids()
        taken = set(exits.pool_id(l, kept) for l in exits.pool_lines("abroad"))
        rows = exits.line_ids(cur, "ru", taken)
        hit = [(i, n - exits.RU_INDEX_BASE) for i, n in rows if i in want or i.rsplit("-", 1)[0] in want]
        return cur, hit
    for _ in range(exitsubs.REPLACE_TRIES):
        cur, hit = lines_of()
        if not hit:
            return [], []
        places = set(k for _i, k in hit)
        out = [cur[k - 1] for k in sorted(places)]
        try:
            rc, warnings, _notes = exitsubs.write_pool(runner, "ru", cur, [l for k, l in enumerate(cur, 1)
                                                                            if k not in places], set())
        except KaskadError as e:
            if lines_of()[1]:
                raise                               # the lines are there: the script wrote nothing
            rc, warnings = 0, [str(e)]
        if rc != 0:
            continue
        if os.path.exists(exitsubs.path()):
            try:
                with open(exitsubs.pending_path(), "a", encoding="utf-8") as f:
                    f.write("".join(exits.exit_id(l) + "\n" for l in out))
                os.chmod(exitsubs.pending_path(), 0o600)
            except OSError:
                pass
        out = cur = None                            # the keys themselves are not kept longer than needed
        dropped = [i for i, _k in hit if i in want]
        util.log_action("GW group drop: keys out (ru): %s" % ", ".join(dropped))
        return dropped, warnings
    raise ConflictError(tr("gw_ops.exit.pool_changed"))


def _subs_group(ids, gid):
    """The subscriptions ``ids`` now give their keys to ``gid`` (``subs[].group``); the caller holds the lock of the
    subscriptions (``exitsubs._locked``)."""
    from . import exitsubs
    doc = exitsubs.load()
    for s in doc.get("subs") or []:
        if s.get("id") in ids:
            s["group"] = gid
    exitsubs.save(doc)


# ── one exit ───────────────────────────────────────────────────────────────

def _is_dup(eid):
    return isinstance(eid, str) and eid.count("-") == 2


def find_exit(st, eid, want_rev):
    """The order of «Объекта нет» (§7.9): an exit with an id of its own — 404 ``gone`` BEFORE the check of ``rev``; a
    repeat of a key ``e-…-N`` (its id moves with the lines above) — the check of ``rev`` first. -> rev now."""
    ids = pool_ids()
    if _is_dup(eid):
        cur = check_rev(st, want_rev)
        if eid not in ids:
            raise gone("exit", eid)
        return cur
    if eid not in ids:
        raise gone("exit", eid)
    return check_rev(st, want_rev)


def _exit_run(runner, eid, change, want_rev, dry, what, want_moves=None):
    """A button over one exit: gone first, then ``rev``, then ``change(st)``."""
    def mutate(st):
        find_exit(st, eid, want_rev)
        return change(st) or {}
    return run(runner, mutate, None, dry, check=False, what=what, want_moves=want_moves)


def exit_manual(runner, eid, how, want_rev=None, dry=False):
    """«В запас» / «Вернуть» / «Обслуживание» (§7.9 ``/manual``): ``exit_pref.manual``; «Вернуть» clears
    ``manual_why`` too. -> {ok, state, rev, moves, warnings}."""
    if how not in MANUAL_STATES:
        raise bad_field("state")

    def change(st):
        model.set_exit(st, eid, manual=MANUAL_STATES[how])
        return {"state": how}
    return _exit_run(runner, eid, change, want_rev, dry, "exit %s %s" % (eid, how))


def exit_country(runner, eid, want_rev=None, dry=False):
    """«Принять новую страну» (§7.9 ``/country``): the country the exit shows now becomes the expected one
    (``exit_pref.country``: expected, accepted_at, notified emptied). Outside the fingerprint: ``rev`` stays.
    -> {ok, country, rev, moves, warnings}."""
    def change(st):
        rec = (exits.load_snapshot().get("exits") or {}).get(eid) or {}
        loc = ((rec.get("geo") or {}) if isinstance(rec.get("geo"), dict) else {}).get("loc")
        if not loc:
            raise _refuse("no_country", 409, CONFLICT, {"exit": eid})
        model.set_exit(st, eid, country={"expected": loc, "accepted_at": int(time.time()), "notified": []})
        return {"country": loc}
    return _exit_run(runner, eid, change, want_rev, dry, "exit %s country" % eid)


def exit_enabled(runner, eid, on, want_rev=None, dry=False):
    """Вкл/выкл выхода (§7.9 ``/enabled``). -> {ok, enabled, rev, moves, warnings}."""
    def change(st):
        model.set_exit(st, eid, enabled=bool(on))
        return {"enabled": bool(on)}
    return _exit_run(runner, eid, change, want_rev, dry, "exit %s %s" % (eid, "on" if on else "off"))


def _role_target(eid, group, confirm_role, want_rev):
    """T15 Q4 (SPEC-groups §2.2): the role ``group`` asks of exit ``eid`` when it is not the role of the file that holds
    its line (None — the same role, an unknown group or exit: ``change`` says why). Another role without
    ``confirm_role`` — 409 ``confirm_role``. ``rev`` and «gone» are checked here first (a short transaction): the move of
    the line changes the pool, so the later write does not compare ``rev`` again."""
    from . import ops
    with state.transaction() as st0:
        find_exit(st0, eid, want_rev)
        t = P.group(_base(st0), group)
    have = ops.role_of_exit(eid)
    if t is None or have is None or t.get("role", "abroad") == have:
        return None
    if not confirm_role:
        raise _refuse("confirm_role", 409, CONFLICT, {"exit": eid, "from": have, "to": group})
    return t.get("role", "abroad")


def exit_move(runner, eid, group, tier=None, confirm_role=False, want_rev=None, dry=False, want_moves=None):
    """«Перенести в группу» / «Добавить выход» (§7.9 ``/move``): out of its tier into ``tier`` of ``group`` (none —
    the ``new_exits_tier`` of the group). Another role — 409 without ``confirm_role`` (T15). -> {ok, rev, moves,
    warnings}. A change of role (T15 Q4, SPEC-groups §2.2): the line moves between upstream.txt and upstream-ru.txt as
    it is — the id of the exit stays (``ops.exit_role_move``) — then the exit goes into ``group`` and its state, windows,
    expected country and old pins are reset (``ops.role_reset``); ``dry`` moves nothing. Atomic (REV-s1-q4 С2,
    SEC-s1-q4 З-1): the group, the tier and the check of the policy (``_save``) run dry BEFORE the line moves; the
    write of the policy failed after the move — the line goes back (the error of the write is told; the key is where
    it was), the way back failed too — an honest error."""
    from . import ops
    to_role = _role_target(eid, group, confirm_role, want_rev)
    what = "exit %s move %s" % (eid, group)
    moved = None

    def change(st):
        pool = pool_rows()
        if to_role is not None:                         # dry: the exit as it will be (its file, so its role)
            pool = [dict(e, role=to_role) if e.get("id") == eid else e for e in pool]
        doc = _base_w(st, pool)
        t = P.group(doc, group)
        if t is None:
            raise bad_field("group", tr("gw_policy_ops.group_target", field="group", value=group))
        src = P.group_of(doc, eid)
        if src is not None and src.get("role", "abroad") != t.get("role", "abroad") and not confirm_role:
            raise _refuse("confirm_role", 409, CONFLICT, {"exit": eid, "from": src.get("id"), "to": group})
        for g in doc.get("groups") or []:
            for x in (g.get("tiers") or []) if isinstance(g, dict) else []:
                if isinstance(x, dict) and eid in (x.get("exits") or []):
                    x["exits"] = [e for e in x["exits"] if e != eid]
                    (x.get("weights") or {}).pop(eid, None)
        tiers = t.get("tiers") or []
        if not tiers:
            tiers = t["tiers"] = [P.default_tier("t1")]
        if tier:
            dst = next((x for x in tiers if isinstance(x, dict) and x.get("id") == tier), None)
            if dst is None:
                raise bad_field("tier")
        else:
            dst = P.new_tier(t)                         # new_exits_tier; a Russian group — a tier of its own (§6.6)
        dst.setdefault("exits", []).append(eid)
        _new, warnings = _save(st, doc, P.stored(st), pool)
        out = {"group": group, "tier": dst.get("id"), "warnings": warnings}
        if moved:                                       # the files of the reset — after the save (REV-s1-q4-2 З-1)
            out["role"] = {"from": moved["from"], "to": moved["to"], "id": eid,
                           "reset": ops.role_reset_state(st, eid)}
        return out
    if to_role is None or dry:
        return _exit_run(runner, eid, change, want_rev, dry, what, want_moves)
    pre = _exit_run(runner, eid, change, want_rev, True, what)   # С2: every refusal before the line moves
    if want_moves is not None and moves_sig(pre.get("moves") or []) != want_moves:
        raise moves_changed()                           # KT3-9 (``--yes``): the moves a person agreed to — before the line
    moved = ops.exit_role_move(runner, eid, to_role)
    if moved is None:                                   # moved meanwhile: the policy as usual
        return _exit_run(runner, eid, change, None, False, what, want_moves)
    try:
        ans = _exit_run(runner, eid, change, None, False, what, want_moves)    # rev checked above; the line raised it
    except Exception as e:
        if _group_of_exit(eid) == group:                # the policy is written (a later step failed): the move stands
            _reset_files(eid, None)
            raise
        try:
            ops.exit_role_back(runner, moved)           # REV-s1-q4-2 З-1: at its place, not at the end of the file
        except Exception as back:
            util.log_action("GW policy: %s broke off, the key stays in the file of %s" % (what, to_role))
            raise KaskadError(tr("gw_ops.exit.role_move_half", id=eid, err=str(e), back=str(back)))
        util.log_action("GW policy: %s refused, the key back in the file of %s" % (what, moved["from"]))
        raise
    _reset_files(eid, ans)
    return ans


def _reset_files(eid, ans):
    """``ops.role_reset_files`` once the move stands (its state saved); what it reset — into the answer's ``role``."""
    from . import ops
    try:
        if state.held():
            done = ops.role_reset_files(eid)
        else:           # REV-s1-q4-3 П-1: the collector reads and writes the snapshot and pins.json under the state lock
            with state.locked():
                done = ops.role_reset_files(eid)
    except (OSError, ValueError, KaskadError) as e:
        util.log_action("GW exit role: %s — the reset of its files failed: %s" % (eid, util.redact(str(e))))
        return
    role = (ans or {}).get("role")
    if isinstance(role, dict):
        role["reset"] = list(role.get("reset") or []) + done


def _group_of_exit(eid):
    """The id of the group whose tier holds ``eid`` in the saved policy (None — none, or unreadable)."""
    try:
        g = P.group_of(P.load(state.load()), eid)
    except (KaskadError, ValueError, OSError):
        return None
    return g.get("id") if isinstance(g, dict) else None


def _quota_body(q):
    if q is None:
        return None
    if not isinstance(q, dict):
        raise bad_field("quota")
    extra = [k for k in q if k not in QUOTA_KEYS]
    if extra:
        raise bad_field("quota." + sorted(extra)[0])
    return dict(q)


_KEEP = object()


def exit_set(runner, eid, name=None, quota=_KEEP, speed_skip=None, allow_duplicate=False, want_rev=None, dry=False,
             via="panel"):
    """The form of an exit (§7.9 ``PUT /api/gw/exits/<id>``): ``name`` (outside ``rev``), ``quota`` (only its five
    settings; None — removed) and ``speed_skip`` (with ``rev``). The order of the checks is the HTTP layer's (the body),
    then here: gone, the repeat of a key, ``rev``, the name. -> {ok, rev, moves?, id, pref}."""
    if quota is not _KEEP:
        quota = _quota_body(quota)
    if speed_skip is not None and not isinstance(speed_skip, bool):
        raise bad_field("speed_skip")
    pol_part = quota is not _KEEP or speed_skip is not None
    if not pol_part and name is not None and not dry:
        # a name alone (T13, SPEC-stage1-rename §3.2): the light write — no config has it, no apply, ``rev`` unchanged
        from . import rename
        r = rename.rename(runner, "exit", eid, name, allow_duplicate=allow_duplicate, via=via)
        return {"ok": True, "rev": r["rev"], "id": eid, "pref": r["pref"], "warnings": [], "rename": r}

    def mutate(st):
        if _is_dup(eid):
            raise KaskadError(tr("panel.api.dupExit"), code=CONFLICT, data={"exit": eid.rsplit("-", 1)[0]})
        if eid not in pool_ids():
            raise gone("exit", eid)
        if pol_part:
            check_rev(st, want_rev)
        clean = None
        if name is not None:
            clean = _exit_name(st, eid, name, allow_duplicate)
        kw = {}
        if quota is not _KEEP:
            kw["quota"] = quota
        p = _set_exit(st, eid, name=clean, speed_skip=speed_skip, **kw)
        return {"id": eid, "pref": copy.deepcopy(p)}
    out = run(runner, mutate, None, dry, check=False, what="exit %s set" % eid)
    if not pol_part:
        out.pop("moves", None)
    return out


def _exit_name(st, eid, name, allow):
    """The name of an exit: «» — the name by the key again; else ``names.check``, a duplicate of a SHOWN name of another
    exit — 409 ``confirm_duplicate`` without ``allow``."""
    if not isinstance(name, str):
        raise bad_field("name")
    if not name.strip():
        return ""
    try:
        from .. import names
        clean = names.check(name, "exit")
    except KaskadError as e:
        raise Refused(str(e), "field", 400, USAGE, dict(getattr(e, "data", None) or {}, field="name"))
    items = []
    try:
        for e in exits.pool(None):
            if e["id"] != eid:
                items.append((e["id"], exits.label(st, e)))
    except KaskadError:
        pass
    from .. import names
    dup = names.duplicates(items, clean, eid)
    if dup and not allow:
        raise _refuse("confirm_duplicate", 409, CONFLICT, {"need": "confirm_duplicate", "duplicates": dup},
                      name=clean)
    return clean


# ── objects with their own ``ver`` ─────────────────────────────────────────

def route_ver(obj):
    """``ver`` of the route of a person or a device (its own ``route``; with T15 — and ``ru_via``)."""
    return ver_of({"route": (obj or {}).get("route")})


def default_route_ver(st):
    return ver_of((model.peek(st)["routes"] or {}).get("default"))


def set_route(runner, what, ref, via, fail=None, want_ver=None, dry=False):
    """The route of a person (``what`` = "user"), a device ("device") or the node ("default", ``ref`` None) with its
    ``ver`` (§7.9): compared under the lock of the write; ``dry`` — the moves by the policy now and the ``ver`` now.
    -> {ok, ver, moves, warnings, user|device|route}."""
    box = {}

    def mutate(st):
        if what == "default":
            check_ver(default_route_ver(st), want_ver)
            r = model.set_default_route(st, via, fail or model.kept_fail(st, "default"))
            box["ver"] = default_route_ver(st)
            return {"route": copy.deepcopy(r)}
        obj = model.get_user(st, ref) if what == "user" else model.get_device(st, ref)
        check_ver(route_ver(obj), want_ver)
        model.set_route(obj, via or "inherit", fail or model.kept_fail(st, what, obj), st=st)
        box["ver"] = route_ver(obj)
        return {what: copy.deepcopy(obj)}
    if dry:
        with state.transaction() as st:
            cur = R.note(st)
            new = copy.deepcopy(st)
            if what == "default":
                v0 = default_route_ver(st)
            else:
                v0 = route_ver(model.get_user(st, ref) if what == "user" else model.get_device(st, ref))
            mutate(new)
            moves = impact(st, new)
        return {"ok": True, "dry": True, "rev": cur, "ver": v0, "moves": moves, "warnings": []}
    out = run(runner, mutate, None, check=False)
    out["ver"] = box.get("ver")
    obj = out.get(what) or {}
    util.log_action("GW %s route: %s -> %s" % (what, obj.get("id") or "default",
                                                ((obj.get("route") or out.get("route") or {}).get("via") or "inherit")))
    return out


def _speed_now(st):
    return copy.deepcopy((_base(st).get("probes") or {}).get("speed") or {})


def speed_get(st=None):
    """``GET /api/gw/probes/speed``: the speed test common to the node (§3.2) and its ``ver``."""
    st = state.load() if st is None else st
    sp = _speed_now(st)
    out = dict((k, sp.get(k)) for k in SPEED_KEYS)
    out["ver"] = ver_of(sp)
    return out


def speed_set(body, want_ver=None):
    """``PUT /api/gw/probes/speed`` (§7.9): the five settings, checked (§3.2), compared by ``ver``; written in a
    ``state.transaction`` without an apply (the unit of probes reads them every run, §5.7); ``rev`` untouched."""
    if not isinstance(body, dict):
        raise bad_field("speed")
    extra = [k for k in body if k not in SPEED_KEYS and k != "ver"]
    if extra:
        raise bad_field(sorted(extra)[0])
    with state.transaction() as st:
        pool = pool_rows()
        doc = _base_w(st, pool)
        sp = copy.deepcopy((doc.get("probes") or {}).get("speed") or {})
        check_ver(ver_of(sp), want_ver)
        for k in SPEED_KEYS:
            if k in body:
                sp[k] = body[k]
        doc.setdefault("probes", {})["speed"] = sp
        _save(st, doc, P.stored(st), pool)
        R.note(st)
        out = speed_get(st)
    util.log_action("GW policy: speed test %s" % json.dumps(dict((k, out.get(k)) for k in SPEED_KEYS), sort_keys=True))
    return dict(out, ok=True)


def notify_get(st=None):
    """``GET /api/notify`` (§8.1): the switches of the pushes and ``flap_summary`` as they act, and ``ver`` of the whole
    ``gateway.notify``."""
    st = state.load() if st is None else st
    n = model.notify(st)
    return {"push": dict(n.get("push") or {}), "flap_summary": bool(n.get("flap_summary")), "ver": ver_of(n)}


def notify_set(push=None, flap_summary=None, want_ver=None):
    """``PUT /api/notify``: the switches given (the known kinds only, bools) and ``flap_summary``; ``ver`` compared under
    the lock. -> as notify_get + ok."""
    if push is not None:
        if not isinstance(push, dict):
            raise bad_field("push")
        for k, v in push.items():
            if k not in model.NOTIFY_PUSH:
                raise bad_field("push." + str(k))
            if not isinstance(v, bool):
                raise bad_field("push." + str(k))
    if flap_summary is not None and not isinstance(flap_summary, bool):
        raise bad_field("flap_summary")
    with state.transaction() as st:
        check_ver(ver_of(model.notify(st)), want_ver)
        g = model.gw(st)
        n = g.get("notify") if isinstance(g.get("notify"), dict) else {}
        if push is not None:
            n["push"] = dict(n.get("push") or {}, **push)
        if flap_summary is not None:
            n["flap_summary"] = flap_summary
        g["notify"] = n
        out = notify_get(st)
    util.log_action("GW notify: %s" % json.dumps({"push": push, "flap_summary": flap_summary}, sort_keys=True))
    return dict(out, ok=True)


def metrics_get(st=None):
    """The size of metrics.db (``gateway.metrics_max_mb``, 64 by default; T3) and its ``ver``."""
    st = state.load() if st is None else st
    v = model.peek(st).get("metrics_max_mb")
    from . import metrics
    cur = v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None
    return {"max_mb": cur or metrics.MAX_MB, "default": metrics.MAX_MB, "ver": ver_of(cur)}


def metrics_set(max_mb, want_ver=None):
    if isinstance(max_mb, bool) or not isinstance(max_mb, int) or not METRICS_MB[0] <= max_mb <= METRICS_MB[1]:
        raise bad_field("max_mb", tr("gw_policy_ops.metrics_bad", lo=METRICS_MB[0], hi=METRICS_MB[1]))
    with state.transaction() as st:
        check_ver(metrics_get(st)["ver"], want_ver)
        model.gw(st)["metrics_max_mb"] = max_mb
        out = metrics_get(st)
    util.log_action("GW metrics: max %d MB" % max_mb)
    return dict(out, ok=True)


def _activity_ver(st):
    a = model.peek(st).get("activity")
    return ver_of(a if isinstance(a, dict) else None)


def activity_get(st=None):
    """«Хранить историю активности» (SPEC-activity ACT-8, §6.4; «Настройки › Хранение данных», Н1): ``keep`` (on by
    default), its ``ver``, what the history holds now — ``since`` (the first window kept), ``bytes`` (roughly), ``db``
    (sqlite3 and the base are there) — and how long each step is kept."""
    st = state.load() if st is None else st
    from . import metrics
    info = metrics.dev_info()
    return {"keep": model.activity_keep(st), "ver": _activity_ver(st), "db": info["db"], "since": info["since"],
            "bytes": info["bytes"], "kept": {"m5_hours": metrics.KEEP["dev_m5"] // 3600,
                                             "h1_days": metrics.KEEP["dev_h1"] // metrics.DAY,
                                             "d1_days": metrics.KEEP["dev_d1"] // metrics.DAY}}


def activity_set(keep, want_ver=None):
    """Turn the history of activity on or off (``gateway.activity.keep``) by its ``ver`` (409 — changed elsewhere).
    Off: the collector stops writing and erases dev_* within its next round (≤ 5 min, ACT-8)."""
    if not isinstance(keep, bool):
        raise bad_field("keep")
    with state.transaction() as st:
        check_ver(_activity_ver(st), want_ver)
        model.gw(st)["activity"] = {"keep": keep}
    util.log_action("GW activity: history %s" % ("on" if keep else "off"))
    return dict(activity_get(), ok=True)


def activity_wipe(dry=False, now=None):
    """«Удалить историю активности» (№41) — «Хранить» stays as it is: dev_* and their marks erased now
    (``metrics.wipe_dev``: one transaction beside the collector, ``meta.dev_wiped`` = ``now``); the collector writes
    nothing counted before it (the windows that began at or before ``now`` — of its accumulator, /run, a round that was
    under way). «Хранить» off — 409 ``activity_off`` (the history is erased by the switch already); no sqlite3 — 409
    ``nosqlite``; no base yet — 409 ``nodb``; the base locked or failing — 409 ``db_busy``, nothing erased.
    ``dry`` — {ok, dry, bytes, since}: what would go (``dev_info``), nothing erased. -> {ok, freed (bytes erased),
    …activity_get()}."""
    from . import metrics
    if not model.activity_keep(state.load()):
        raise _refuse("activity_off", 409, CONFLICT, text=tr("gw_policy_ops.activity.off"))
    if not metrics.available():
        raise _refuse("nosqlite", 409, CONFLICT, text=tr("gw_policy_ops.activity.nosqlite"))
    info = metrics.dev_info()
    if not info["db"]:
        raise _refuse("nodb", 409, CONFLICT, text=tr("gw_policy_ops.activity.nodb"))
    if dry:
        return {"ok": True, "dry": True, "bytes": info["bytes"], "since": info["since"]}
    res, why = metrics.wipe_dev(int(time.time() if now is None else now))
    if why in ("nosqlite", "nodb"):
        raise _refuse(why, 409, CONFLICT, text=tr("gw_policy_ops.activity." + why))
    if why:
        raise _refuse("db_busy", 409, CONFLICT, text=tr("gw_policy_ops.activity.busy"))
    util.log_action("GW activity: the history of activity wiped — %d rows erased" % (res["rows"] + res["nodes"]))
    return dict(activity_get(), ok=True, freed=res["bytes"])


def tz_get():
    """``GET /api/settings/time`` (§3.7, §7.4): the zone of the node, of the OS, the clock, the offset, NTP (None —
    unknown), the zones of the system database, a warning, ``ver``."""
    from .. import tz
    name = tz.name()
    now = time.time()
    out = {"tz": name, "os_tz": _os_tz(), "now": int(now), "offset": tz.offset(now), "ntp": _ntp(),
           "zones": zones(), "ver": ver_of(name)}
    w = tz.warning()
    if w:
        out["warn"] = w
    return out


def tz_set(name, want_ver=None):
    """``PUT /api/settings/time``: the zone of the node (KASKAD_TZ) compared by ``ver`` under the state lock (the
    writers of the zone take it). A bad name — 400 ``field``."""
    from .. import tz
    with state.locked():
        check_ver(ver_of(tz.name()), want_ver)
        try:
            tz.set_name(name)
        except ValueError as e:
            raise bad_field("tz", str(e))
    util.log_action("TZ: %s" % tz.name())
    _bench_timer()
    return dict(tz_get(), ok=True)


def _bench_timer():
    """A new zone moves the lines of the timer of the schedule (§2.4: the zone is the suffix of OnCalendar): ``apply``.
    A failing systemctl — a line of the journal; the zone is saved."""
    from ..bench import sched_unit
    runner = sched_unit.system_runner()
    if runner is None:
        return
    try:
        r = sched_unit.apply(runner, state.load())
        if not r.get("applied"):
            util.log_tr("bench.log.sched_not_applied", why=r.get("why") or "-")
    except Exception:                                # noqa: BLE001 — the timer never fails the save of the zone
        pass


def _os_tz():
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    raw = util.read_text("/etc/timezone")
    return (raw or "").strip() or None


def _ntp():
    """The clock synchronised (the kernel's flag); None — not known here."""
    try:
        import ctypes
        import ctypes.util

        class _Timex(ctypes.Structure):
            _fields_ = [("modes", ctypes.c_uint)] + [("f%d" % i, ctypes.c_long) for i in range(40)]
        libc = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
        if hasattr(libc, "adjtimex"):
            tx = _Timex()
            rc = libc.adjtimex(ctypes.byref(tx))
            if rc >= 0:
                return rc != 5
    except Exception:                                   # noqa: BLE001 — not Linux
        pass
    return None


ZONE_AREAS = ("Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic", "Australia", "Europe", "Indian",
              "Pacific")


def zones():
    """The names of the zones of the system database (the areas of IANA and «UTC»), sorted."""
    from .. import tz
    out = []
    for area in ZONE_AREAS:
        root = os.path.join(tz.ZONEINFO, area)
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                n = os.path.relpath(os.path.join(dirpath, f), tz.ZONEINFO)
                if tz.valid_name(n):
                    out.append(n)
    if os.path.exists(os.path.join(tz.ZONEINFO, "UTC")):
        out.append("UTC")
    return sorted(set(out))


ERROR_CODE = ERROR                         # (kept for the CLI: a refusal without its own code)


# ── the fields of stage 1 in the rows of the exits (GET /api/gw/exits, ``gw exits --json``) ──

def _reason(code):
    """``rule:<id>`` -> {code: rule, args: {rule: id}}; else {code, args: {}}."""
    c = str(code)
    if ":" in c:
        head, _, tail = c.partition(":")
        return {"code": head, "args": {head: tail}}
    return {"code": c, "args": {}}


LAST_POINT_S = 600          # the last minute point of an exit is «now» while it is at most this old


def rtt_keep_s(grp):
    """How old the last measured response of an exit of ``grp`` may be to stand for «now» (Л3, «Отклик —»): the HTTP
    probe of an idle AmneziaWG exit runs once in max(12P, 120) s (``awg_rtt_period``) — a minute without traffic has
    no rtt_med; at least LAST_POINT_S, else the idle period of the group's own P and a minute of slack."""
    return max(LAST_POINT_S, P.awg_rtt_period(P.freq_p(grp), traffic=False) + 60)


def _last_metrics(ids, now, keep=None):
    """{eid: {"rtt_ms", "loss_pct", "jitter_ms", "ts"}} of the last minute point of metrics.db (≤ LAST_POINT_S old) and
    {eid: {"mbps", "ts"}} of the last good speed test; no base — empty. ``rtt_ms`` — of the last minute with a measured
    response within ``keep[eid]`` s (default LAST_POINT_S) while the last minute has good probes (``ok`` > 0; a dead
    exit keeps «—»): the probe of an idle exit is not in every minute (stand it1 Н-2), «Все выходы › Отклик» said «—»
    for a working exit while «Выходы · час» showed its last value."""
    from . import metrics
    pts, speed = {}, {}
    conn = metrics._reader()
    if conn is None or not ids:
        return pts, speed
    try:
        for eid in ids:
            r = conn.execute("SELECT ts, rtt_med, loss, jitter, ok FROM m1 WHERE exit = ? ORDER BY ts DESC LIMIT 1",
                             (eid,)).fetchone()
            if r and now - r[0] <= LAST_POINT_S:
                rtt = r[1]
                if rtt is None and (r[4] or 0) > 0:     # the exit answers this minute: its response of a minute ago
                    q = conn.execute("SELECT rtt_med FROM m1 WHERE exit = ? AND ts >= ? AND rtt_med IS NOT NULL "
                                     "ORDER BY ts DESC LIMIT 1",
                                     (eid, now - ((keep or {}).get(eid) or LAST_POINT_S))).fetchone()
                    rtt = q[0] if q else None
                pts[eid] = {"ts": r[0], "rtt_ms": metrics._round(rtt), "loss_pct": metrics._round(r[2]),
                            "jitter_ms": metrics._round(r[3])}
            s = conn.execute("SELECT ts, mbps FROM speed WHERE exit = ? AND ok = 1 ORDER BY ts DESC LIMIT 1",
                             (eid,)).fetchone()
            if s:
                speed[eid] = {"mbps": metrics._round(s[1]), "ts": s[0]}
    except Exception:                                   # noqa: BLE001 — a broken base: no history, the rest works
        pass
    finally:
        try:
            conn.close()
        except Exception:                               # noqa: BLE001
            pass
    return pts, speed


def _quota_view(st, eid, q, now):
    """The whole quota of an exit (§7.9: the settings and the counters) or None."""
    if not isinstance(q, dict):
        return None
    from . import pins as PN
    from .. import tz
    out = dict((k, q.get(k)) for k in QUOTA_KEYS)
    try:
        doc = PN.traffic_read()
        z = tz.zone()
        used = PN.used(doc, eid, q, now, z)
        out["used_gb"] = round(used / 1e9, 2)
        lim = P.quota_gb(q)
        out["pct"] = int(min(999, round(100.0 * used / (lim * 1e9)))) if lim is not None else None
        out["reset"] = PN.next_reset(q, now, z, start=PN.period_of(doc, eid, q, now, z))
    except Exception:                                   # noqa: BLE001 — no counters yet
        out.update(used_gb=None, pct=None, reset=None)
    out["paused_until"] = None
    return out


def exit_fields(st, rows, snap=None, now=None):
    """The rows of ``exits.public`` with the fields of stage 1 (§7.9 ``GET /api/gw/exits``, in place): ``health``
    (ok/degraded/standby/off/unknown — the ``state`` of §7.9; ``state`` keeps the words of 3.3.5: alive/dead/unknown),
    ``reasons`` [{code, args}], ``group``, ``tier``, ``devices``, ``rtt_ms``, ``loss_pct``, ``jitter_ms``,
    ``country``, ``speed`` {mbps, ts}, ``quota`` (whole) | None, ``speed_skip``, ``probe_mb_day``, ``last_check_ts``,
    ``recovering``, ``off_until``, ``unstable``, ``unstable_until``, ``manual``, ``manual_why``. -> rows."""
    from . import pins as PN
    now = time.time() if now is None else now
    snap = exits.load_snapshot() if snap is None else snap
    recs = snap.get("exits") or {}
    pool = pool_rows()
    pol = _base(st, pool)
    kinds = P.pool_kinds(pool)
    pins_doc = PN.read()
    on = {}
    for g in (pins_doc.get("groups") or {}).values():
        for p in ((g or {}).get("pins") or {}).values():
            if isinstance(p, dict) and p.get("exit"):
                on[p["exit"]] = on.get(p["exit"], 0) + 1
    ids = [r.get("id") for r in rows if r.get("id")]
    pts, speed = _last_metrics(ids, now, keep=dict((eid, rtt_keep_s(P.tier_of(pol, eid)[0])) for eid in ids))
    for r in rows:
        eid = r.get("id")
        rec = recs.get(eid) if isinstance(recs.get(eid), dict) else {}
        hs, _shown = exits.health_of(rec) if rec else ("unknown", None)
        pref = model.exit_pref(st, eid) if eid and "-" not in eid[2:] else model.exit_pref(st, "")
        grp, ti = P.tier_of(pol, eid)
        m = pts.get(eid) or {}
        geo = rec.get("geo") if isinstance(rec.get("geo"), dict) else {}
        q = _quota_view(st, eid, pref.get("quota"), now)
        if q is not None and hs == "off" and "quota" in (rec.get("reasons") or []) and q.get("action") == "remove":
            q["paused_until"] = q.get("reset")
        kind = kinds.get(eid) or ("xray" if exits.is_xray(r) else "awg")
        r.update({
            "health": hs, "reasons": [_reason(x) for x in rec.get("reasons") or []],
            "group": grp.get("id") if grp else None,
            "tier": grp["tiers"][ti].get("id") if grp is not None else None,
            "devices": on.get(eid, 0),
            "rtt_ms": m.get("rtt_ms", rec.get("rtt")), "loss_pct": m.get("loss_pct"), "jitter_ms": m.get("jitter_ms"),
            "country": geo.get("loc"), "country_expected": (pref.get("country") or {}).get("expected"),
            "speed": speed.get(eid), "quota": q, "speed_skip": bool(pref.get("speed_skip")),
            "probe_mb_day": probe_mb_day(pol, grp, kind, pool) if grp is not None else None,
            "last_check_ts": m.get("ts") or (snap.get("at") if rec else None),
            "recovering": bool(rec.get("recovering")), "off_until": rec.get("off_until"),
            "unstable": bool(rec.get("unstable")), "unstable_until": rec.get("unstable_until"),
            "manual": pref.get("manual"), "manual_why": pref.get("manual_why")})
    return rows
