"""The scheme of the web panel (docs/k3/SPEC-wave3.md §3.5, work package P3): the graph «люди → устройства →
входы → правила → пул → выходы» for ``static/graph.js``, its layout and «Применить».

    GET  /api/scheme         {ok, version, layout_version, nodes, edges, default_route, profiles, gateway, kinds,
                              live}
    PUT  /api/scheme/layout  {version, positions, orders?, reset?} -> {ok, version}; 409 — another tab saved first
    POST /api/scheme/apply   {intents, dry_run, version?} -> {ok, results, changes, affected, warnings, graph?}
    POST /api/gw/trace       {who: "d-…"|"u-…"|"-", target: "youtube.com"|"8.8.8.8", inbound?: "l2tp"} -> «Куда пойдёт
                             трафик?» through ONE inbound of the device (default: the first enabled)
                             (kaskad.gw.trace: verdict, summary, steps, notes, highlight — ids of this graph);
                             nothing changes, no command runs (the node's resolver is asked, read-only)

The graph has the shape of React Flow (node ``{id, type, position, data}``, edge ``{id, type, source,
sourceHandle, target, targetHandle, data}``) and is built from state.json (read through ``kaskad.snapshot``), the
PUBLIC part of the pool of exits (upstream.txt without its keys), the snapshot of the exits watchdog (liveness)
and /etc/kaskad/panel/layout.json (positions — apart from the model, with a version):

    nodes  u-<8 hex>        user; its devices are rows inside (``data.devices``), the handle of a row is the
                            device id, the handle of the header is "user"
           in:<kind>        an installed inbound
           rules:<profile>  rules profile («всё через выход», «Россия напрямую» — WP-R2; until the model has
                            profiles everybody is «всё через выход»)
           pool, exit:<id>, direct, block   — route targets: the id of a target IS the route's ``via``
           rug:<gid>        a Russian group (T15 Q6, SPEC-groups §8.4): its keys inside, never a route target;
                            only on a node that has Russian groups
    edges  access      device -> inbound (grant / revoke)
           route       device -> its effective route target through the rules node of its profile;
                       ``data.from`` device | user | default (inherited routes are drawn dashed / dotted)
           user_route  user header -> the user's own route
           member      pool -> exit (state of the exit)
           rule        rules:ru_direct -> direct («Россия») and -> block («реклама»)
           ru_via      device -> rug:<gid> — ONLY where the choice acts (``exits.ru_target``: «Россия напрямую»
                       applies to the device and its «Российские сайты» resolve to that group)
           ru_link     rules:ru_direct (row «ru») -> rug:<gid>: the last link of those lines

«Применить» takes intents ``grant {device, kind}``, ``revoke {device, kind}``, ``route {device|user|default,
via, fail?}``, ``rules {device|user, profile}``, ``ru_via {device|user, via: inherit|direct|<gid>}`` and runs them IN ORDER on one copy of the state with the core's
own checks (``model.get_device``, ``model.grant``/``revoke``, ``model.parse_route``/``set_route`` and the
secrets of the inbound the way ``ops.device_grant`` drops them) plus what only the graph knows: the inbound is
installed, the exit is in the pool. ``dry_run`` does it on a deep copy in memory and never writes; a real apply
first does the same dry pass, then the whole list under the state lock as ONE change of the core
(``ops._change``: one apply of the gateway, nothing saved unless every intent passes again).
"""

import copy
import hashlib
import json
import math
import os
import re
import threading

from .. import paths, snapshot, state, sysrun, util
from ..i18n import get_lang, tr
from ..errors import CONFLICT, ERROR, LOCKED, UNSUPPORTED, USAGE, KaskadError
from ..gw import exits, model, ops, registry, rename
from ..gw import live as gw_live
from .routes import HttpError, json_response, route

LAYOUT_NAME = "layout.json"
MAX_INTENTS = 200
MAX_POSITIONS = 5000
COORD_LIMIT = 100000.0

FILTER = "filter"
FORCE_NOTE = "panel.scheme.forceNote"         # catalog keys: translated where the answer is built (tr)
PROFILE_ALL = "all_exit"
PROFILE_RU = "ru_direct"
PROFILE_TITLES = {PROFILE_ALL: "panel.scheme.profileAllExit", PROFILE_RU: "panel.scheme.profileRuDirect"}
RULES_SOON = "panel.scheme.rulesSoon"


def profile_title(p):
    """The title of a rules profile in the language of the request (an unknown one — its name)."""
    key = PROFILE_TITLES.get(p)
    return tr(key) if key else p
TARGET_TYPES = ("pool", "exit", "direct", "block")

_NODE_ID = re.compile(r"^(u-[0-9a-f]{8}|in:[a-z0-9]{2,8}|rules:[a-z0-9_]{2,24}|pool|direct|block|filter"
                      r"|exit:e-[0-9a-f]{10}(-[0-9]{1,4})?|rug:g-[0-9a-f]{6})$")
RU_NODE = "rug:"                    # the node of a Russian group (T15 Q6, SPEC-groups §8.4): «rug:<gid>»
_DEV_ID = re.compile(r"^d-[0-9a-f]{8}$")

_lock = threading.Lock()            # the caches below
_pool_cache = {}
_ru_pool_cache = {}
_graph_cache = {}
_layout_lock = threading.Lock()     # read-modify-write of layout.json inside this process
_flight = snapshot.SingleFlight()


class _IntentsFailed(Exception):
    def __init__(self, results, status=409, text=None, code=CONFLICT):
        Exception.__init__(self, text or "")
        self.results = results
        self.status = status
        self.text = text
        self.code = code


class _Stale(Exception):
    pass


class _RulesUnavailable(KaskadError):
    code = UNSUPPORTED


# ── inputs ─────────────────────────────────────────────────────────────────

def layout_path():
    return os.path.join(paths.panel_dir(), LAYOUT_NAME)


def read_state():
    """state.json through the read cache (never mutated here); a missing file is an empty state."""
    p = paths.state()
    if snapshot.signature(p) is None:
        return state.empty()
    st = snapshot.read_json(p)
    if not isinstance(st, dict):
        raise HttpError(500, tr("panel.scheme.stateUnreadable", path=p))
    return state.as_loaded(st)          # ``gen`` as ``load`` sets it (D-135; REV-s1-b5 З1)


_PUBLIC_EXIT = ("id", "index", "tag", "kind", "proto", "role", "mark", "iface", "version", "host", "name", "where",
                "problem")


def pool_exits():
    """The pool without its secrets (``line`` and ``outbound`` are dropped right away), cached by the signatures
    of upstream.txt and of the parser. Reads never run commands (spec §1: heavy work is the collector's): a
    parser that cannot be loaded gives the ids and kinds from the lines (``exits.pool_fallback``). ``where`` stays:
    a key without a name of its own is called by it («AmneziaWG 2.0 · 203.0.113.141», ``exits.label``) — as in
    «Выходы», «Обзор» and the menu, not by its id (stand T-G1b, N1)."""
    key = (snapshot.signature(paths.egress_up()), snapshot.signature(paths.egress_parser()))
    with _lock:
        hit = _pool_cache.get("pool")
        if hit is not None and hit[0] == key:
            return hit[1]
    try:
        raw = exits.pool(None)
    except KaskadError:
        raw = exits.pool_fallback()
    out = [dict((k, e[k]) for k in _PUBLIC_EXIT if k in e) for e in raw]
    with _lock:
        _pool_cache["pool"] = (key, out)
    return out


def ru_pool_exits():
    """The Russian keys (upstream-ru.txt, T15) without their secrets — as ``pool_exits``, cached by the signatures of
    the file and of the parser. Only the scheme's nodes of the Russian groups read them (never a route target)."""
    key = (snapshot.signature(paths.egress_up_ru()), snapshot.signature(paths.egress_parser()))
    with _lock:
        hit = _ru_pool_cache.get("pool")
        if hit is not None and hit[0] == key:
            return hit[1]
    try:
        raw = exits.pool(None, roles=("ru",))
    except KaskadError:
        raw = exits.pool_fallback(roles=("ru",))
    out = [dict((k, e[k]) for k in _PUBLIC_EXIT if k in e) for e in raw]
    with _lock:
        _ru_pool_cache["pool"] = (key, out)
    return out


def exits_snapshot():
    """The watchdog's snapshot of the exits (alive / dead / not checked yet), read through the cache."""
    doc = snapshot.read_json(exits.snapshot_path())
    if not isinstance(doc, dict) or not isinstance(doc.get("exits"), dict):
        return {"at": 0, "pool": [], "exits": {}}
    return doc


def load_layout():
    """{"version", "positions": {node id: [x, y]}, "orders": {user id: [device ids]}}; a missing or broken
    file is version 0 (the next save writes a good one)."""
    doc = snapshot.read_json(layout_path())
    out = {"version": 0, "positions": {}, "orders": {}}
    if not isinstance(doc, dict):
        return out
    v = doc.get("version")
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        out["version"] = v
    pos = doc.get("positions")
    if isinstance(pos, dict):
        for k, xy in pos.items():
            p = _xy(xy)
            if p is not None and _NODE_ID.match(str(k)):
                out["positions"][str(k)] = p
    orders = doc.get("orders")
    if isinstance(orders, dict):
        for k, ids in orders.items():
            if _NODE_ID.match(str(k)) and isinstance(ids, list):
                out["orders"][str(k)] = [str(i) for i in ids if _DEV_ID.match(str(i))]
    return out


def live_part():
    """What the client needs of /run/kaskad/live.json (sessions, counters, liveness) — or None without FRESH data:
    a file older than ``live.STALE`` (the collector is stopped or hangs) is unknown, not «0 online» (the rule of
    the REST views and of SSE)."""
    doc, age = gw_live.read()
    if doc is None or not gw_live.fresh(age):
        return None
    out = {"ts": doc.get("ts"), "seq": doc.get("seq")}
    for sec in ("devices", "exits", "inbounds", "groups"):      # groups — the step of each group (T15 Q6, §8.9)
        v = doc.get(sec)
        out[sec] = v if isinstance(v, dict) else {}
    return out


def rules_lists_ready():
    """Can «Россия напрямую» work: the networks of Russia are stored — ``rules.apply.ready()``, what
    ``GET /api/rules`` says as «ready» (a version of the store with its files, read like ``rules.ops.status``; no
    command runs). Not «any file in /var/lib/kaskad/rules»: status.json and update.lock are written by every
    attempt to download, a failed one too."""
    try:
        from ..rules import apply as rules_apply
        return bool(rules_apply.ready())
    except (ImportError, OSError, ValueError, KaskadError):
        return False


def ru_state(st=None):
    """Does «Россия напрямую» work NOW — by the table of the kernel, not by «the lists are stored» (no command runs):

        {"state": "ok" | "no_lists" | "no_consent" | "not_loaded" | "unknown", "domains": True | False | None, "error",
         "next"}

    ``no_lists`` — nothing to apply (a profile ru_direct behaves as all_exit); ``no_consent`` — the lists are there, but
    «Россия напрямую» is not allowed on ``st`` (``rules.apply.ru_ok``: no consent ⚠РФ, рубеж 2 — the profile is chosen,
    it acts as all_exit; R4); ``not_loaded`` — the last load of the
    networks of Russia into the table failed (``rules.apply.load_state``: its error, ``next`` — when it tries again);
    ``unknown`` — no load yet (after a reboot until kaskad-gw-rules-load.timer runs, or the gateway is off); ``ok`` —
    the networks are in the table (``old`` — of the previous lists while the new ones load). ``domains`` — dnsmasq has
    nftset (what the last apply saved): without it the domains «напрямую» do not count, only the networks."""
    out = {"state": "no_lists", "domains": None, "error": None, "next": None}
    try:
        from ..gw import dns, net
        from ..rules import apply as rules_apply
        if not rules_apply.ready():
            return out
        if st is not None and not rules_apply.ru_ok(st, is_ready=True):
            out["state"] = "no_consent"
            return out
        wanted = net.ru_wanted()
        load = rules_apply.load_state()
        feats = dns.saved_features()
    except (ImportError, OSError, ValueError, KaskadError):
        return out
    if isinstance(feats.get("nftset"), bool):
        out["domains"] = feats["nftset"]
    if load.get("ok") is True and load.get("ver"):
        out["state"] = "ok"
        if load.get("ver") != wanted:
            out["old"] = True
    elif load.get("ver") == wanted and load.get("ok") is False:
        out["state"] = "not_loaded"
        out["error"] = util.redact(str(load.get("error") or "")) or None
        out["next"] = load.get("next") if isinstance(load.get("next"), (int, float)) else None
    else:
        out["state"] = "unknown"
    return out


def rf_view(st):
    """The tick ⚠РФ for the scheme (SPEC-stage1-rf-mark-builtin §5.1, R4): ``mode`` (full | legacy | need | none),
    ``required`` (no consent yet: a new «Россия напрямую» asks the tick) and ``need`` — the block to show under a choice
    of «Россия напрямую» (``rfgate.need_data`` of ``block_orgs``: the same text a refusal carries). Reads the kept
    result of the scan only (no process)."""
    try:
        from ..rules import rfgate
        s = rfgate.state(st)
        return {"mode": s["mode"], "required": bool(s["required"]), "uncovered": list(s.get("uncovered") or []),
                "need": rfgate.need_data(rfgate.block_orgs(st))}
    except (ImportError, OSError, ValueError, KeyError, KaskadError):
        return {"mode": "none", "required": True, "uncovered": [], "need": None}


def _rf_off(st, obj, what):
    """The chosen profile of a device or a person (``what``) does not act for want of the consent ⚠РФ (mode ``need``):
    the mark «не действует: нет подтверждения ⚠РФ» next to it (§3.5, R4)."""
    try:
        from ..rules import apply as rules_apply
        dev = obj if what == "device" else {"user": obj.get("id")}
        return rules_apply.profile(st, dev, is_ready=True)["why"] == "no_consent"
    except (ImportError, OSError, ValueError, KaskadError):
        return False


def _ru_allowed(st):
    """«Россия напрямую» may act on this node as to the consent ⚠РФ (``rules.apply.ru_ok`` with the lists given)."""
    try:
        from ..rules import apply as rules_apply
        return rules_apply.ru_ok(st, is_ready=True)
    except (ImportError, OSError, ValueError, KaskadError):
        return False


# ── the Russian groups (T15 Q6, SPEC-groups §8.4) ──

_RU_GID = re.compile(r"^g-[0-9a-f]{6}$")


def ru_groups(st):
    """The Russian groups of the STORED policy — the ones ``model.ru_via_of`` resolves to. None (a node of 3.3.7.9 and
    before, or one without Russian groups): the scheme as it was, no node and no line of them."""
    from ..gw import policy as P
    return [g for g in (P.stored(st) or {}).get("groups") or []
            if isinstance(g, dict) and g.get("role", "abroad") == "ru" and _RU_GID.match(str(g.get("id") or ""))]


def _own_ru_via(obj):
    """``ru_via`` set on a person or a device itself (``direct`` or a group id); None — inherited."""
    v = (obj or {}).get("ru_via")
    return v if isinstance(v, str) and v.strip() and v != model.RU_VIA_INHERIT else None


def _ru_acts(st, device, ready):
    """«Россия напрямую» acts for the device (``rules.apply.device_rd``: active, the profile, the lists, the consent ⚠РФ,
    not «блок» by choice) — only then its Russian group carries anything."""
    try:
        from ..rules import apply as rules_apply
        return bool(rules_apply.device_rd(st, device, is_ready=ready))
    except (ImportError, OSError, ValueError, KaskadError):
        return False


def _ru_now(grp, dec):
    """Where the Russian sites of a group go now, by the decision of the collector (pins.json + the snapshot):
    {"step": "exit" | "last_direct" | "last_block" | "unknown", "exit", "since", "empty"}. No keys, or the collector
    put it on its last step — the last step; else its first fit key (of the working tier first); no fit key — the last
    step; nothing checked yet — «unknown» (not «недоступны»). ``since`` — only when the decision has it (pins.json
    does not keep it): the page takes «с какого времени» from ``live.groups`` (``live_part``)."""
    fail = "block" if grp.get("fail") == "block" else "direct"
    tiers = [t for t in grp.get("tiers") or [] if isinstance(t, dict)]
    ids = [e for t in tiers for e in t.get("exits") or [] if isinstance(e, str)]
    g = (dec.get("groups") or {}).get(grp.get("id"))
    g = g if isinstance(g, dict) else {}
    out = {"step": "last_" + fail, "exit": None, "since": None, "empty": not ids}
    if not ids:
        return out
    since = g.get("last_since") if isinstance(g.get("last_since"), (int, float)) else None
    if g.get("last_step") in ("direct", "block"):
        return dict(out, step="last_" + g["last_step"], since=since)
    states = dec.get("states") or {}
    first = [e for t in tiers if t.get("id") == g.get("active_tier") for e in t.get("exits") or []]
    for eid in first + ids:
        if states.get(eid) in exits.USABLE:
            return dict(out, step="exit", exit=eid)
    if all(states.get(e) in (None, "unknown") for e in ids):
        return dict(out, step="unknown")
    return dict(out, since=since)


def _ru_node(st, grp, by_id, snap, dec, layout, devices, chosen):
    """The node «rug:<gid>» of a Russian group: its keys in the order of its steps (each key — its own step), its last
    step and where it goes now; ``devices`` — the active devices whose Russian sites go there (the lines), ``chosen`` —
    every device whose «Российские сайты» resolve to it (acting or not)."""
    from ..gw import policy as P
    states = dec.get("states") or {}
    rows = []
    for i, t in enumerate(t for t in grp.get("tiers") or [] if isinstance(t, dict)):
        for eid in t.get("exits") or []:
            if not isinstance(eid, str):
                continue
            e = by_id.get(eid)
            if e is None:                        # in the group, its line is gone from upstream-ru.txt
                pref = model.exit_pref(st, eid)
                rows.append({"id": eid, "name": pref["name"] or eid, "kind": "missing", "proto": "", "step": i + 1,
                             "tier": t.get("id"), "missing": True, "enabled": pref["enabled"], "usable": False,
                             "state": "missing", "problem": None})
                continue
            row = exits.public(e, st, snap)
            rows.append({"id": eid, "name": row["name"], "kind": row.get("kind"), "proto": row.get("proto") or "",
                         "step": i + 1, "tier": t.get("id"), "missing": False, "enabled": row["enabled"],
                         "usable": bool(row["enabled"]) and states.get(eid) in exits.USABLE,
                         "state": row.get("state"), "problem": row.get("problem")})
    gid = grp["id"]
    nid = RU_NODE + gid
    return {"id": nid, "type": "ru_group", "position": _position(layout, nid),
            "data": {"group": gid, "name": P.label(grp), "fail": "block" if grp.get("fail") == "block" else "direct",
                     "exits": rows, "now": _ru_now(grp, dec), "devices": devices.get(gid, 0),
                     "chosen": chosen.get(gid, 0)}}


def default_profile(st):
    """The profile of the node (``gateway.rules.default``) — what a user or a device without its own follows."""
    if not profiles_supported():
        return PROFILE_ALL
    try:
        return model.rules_conf(st)["default"]
    except (AttributeError, KaskadError):
        return PROFILE_ALL


# ── rules profiles (WP-R2 adds them to the model; until then — «всё через выход») ──

def _model_fn(*names):
    for n in names:
        fn = getattr(model, n, None)
        if callable(fn):
            return fn
    return None


def profiles_supported():
    return _model_fn("set_rules", "set_profile") is not None


def profile_names():
    names = (getattr(model, "RULE_PROFILES", None) or getattr(model, "RULES_PROFILES", None)
             or getattr(model, "PROFILES", None) or ())
    out = [str(n) for n in names if str(n) and str(n) != "inherit"]
    return out or [PROFILE_ALL, PROFILE_RU]


def _own_profile(obj):
    """The profile set on a user or a device itself; None — inherited."""
    for key in ("rules", "profile"):
        v = (obj or {}).get(key)
        if isinstance(v, dict):
            v = v.get("profile") or v.get("rules")
        if isinstance(v, str) and v.strip() and v.strip() != "inherit":
            return v.strip()
    return None


def profile_of(st, device, user=None):
    """(profile, from) of a device: the model's own answer when it has one, else device -> user -> «всё через
    выход». Without the model's support everybody is «всё через выход» (nobody can set anything else)."""
    if not profiles_supported():
        return PROFILE_ALL, "default"
    fn = _model_fn("rules_of", "profile_of")
    if fn is not None:
        try:
            r = fn(st, device)
        except KaskadError:
            r = None
        if isinstance(r, dict):
            p = r.get("profile") or r.get("rules") or r.get("value")
            if p:
                return str(p), str(r.get("from") or "default")
        elif isinstance(r, str) and r:
            return r, "default"
    if user is None:
        user = model.find_user(st, device.get("user")) or {}
    for src, obj in (("device", device), ("user", user)):
        p = _own_profile(obj)
        if p:
            return p, src
    return PROFILE_ALL, "default"


def _set_profile(obj, profile):
    put = _model_fn("set_rules", "set_profile")
    if put is None:
        raise _RulesUnavailable(tr(RULES_SOON))
    return put(obj, profile)


# ── the graph ──────────────────────────────────────────────────────────────

def graph_version(st):
    """Fingerprint of the gateway part of the state: the client sends it back with «Применить» — another tab,
    the bot or the menu changed something in between -> 409, look at the changes again."""
    raw = json.dumps(model.peek(st), sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def kind_title(kind):
    return registry.title(kind)


def _suits(platform):
    """Inbound kinds the matrix offers this platform (spec §4) — a hint for drawing, never a check."""
    try:
        from ..gw import issue_os
        return [o["inbound"] for o in issue_os.offers(platform)]
    except (ImportError, KeyError):
        return None


def _route_or_none(obj):
    """The own route of a user/device as {"via", "fail"}; None — inherited (or broken: the core falls through)."""
    if obj is None:
        return None
    try:
        if isinstance(obj, str):
            return model.parse_route(obj)
        if isinstance(obj, dict) and obj.get("via"):
            return model.parse_route(obj.get("via"), obj.get("fail"))
    except KaskadError:
        return None
    return None


def default_route(st):
    return _route_or_none(model.peek(st)["routes"].get("default")) or dict(model.DEFAULT_ROUTE)


def _exit_name(st, eid, pool):
    e = next((x for x in pool if x["id"] == eid), None)
    if e is not None:
        return exits.label(st, e)                  # «AmneziaWG 2.0 · 203.0.113.141» when the key has no name
    return model.exit_pref(st, eid)["name"] or eid


def _now(st, device, pool, alive, enabled=None, inbound=None, decision=None):
    """Where the traffic of a device goes NOW — ``exits.now_of``, the same answer as «Куда пойдёт трафик?» (Н-1): the
    path goes through ``inbound`` (one of the device's enabled ones — «Через» of trace), else through its first enabled
    inbound (as trace does without a choice); through SOCKS5 or VLESS xray picks among every live exit («any» — pinned
    to none), through the kernel the pool is the hash of the device (an exit, or the balancer of the VLESS exits).
    ``decision`` — the decision of the collector, one per request (``exits.decision``: «в запасе», «ухудшен», the quota —
    as gw_route and trace see them, REV-s1-t4 С2); None — the form before stage 1 (``alive``).
    -> {"target", "exit", "balancer": False|"xray"|"any", "exits", "inbound", "why"}."""
    from ..gw import trace
    if not model.device_active(st, device):
        return {"target": None, "exit": None, "balancer": False, "exits": [], "inbound": None,
                "why": tr("panel.scheme.whyDeviceOff")}
    kinds = set(model.enabled_kinds(st)) if enabled is None else enabled
    inbound = trace.path_inbound(device, kinds, wanted=inbound)
    if decision is not None:
        n = exits.now_of(st, device, pool, socks=inbound in trace.XRAY_IN, decision=decision)
    else:
        n = exits.now_of(st, device, pool, alive, socks=inbound in trace.XRAY_IN)
    route = n["route"]
    fall = tr("panel.scheme.direct") if route.get("fail") == "direct" else tr("panel.scheme.block")
    out = {"target": n["target"], "exit": n["exit"], "balancer": n["balancer"], "exits": list(n["exits"]),
           "inbound": inbound}
    if n["balancer"] == "any":
        out["why"] = tr("panel.scheme.whyAny", via={"vless": "VLESS", "hy2": "Hysteria2"}.get(inbound, "SOCKS5"))
    elif n["balancer"] in (exits.XRAY, exits.OLD_XRAY):
        out["why"] = tr("panel.scheme.whyVless")
    elif n["exit"] and n["dead"]:            # stage 1 (§2.2): «exit:E», E unfit or gone — another exit of its group
        out["why"] = tr("panel.scheme.whyEvicted", name=_exit_name(st, n["dead"][0], pool),
                        to=_exit_name(st, n["exit"], pool))
    elif n["exit"]:
        name = _exit_name(st, n["exit"], pool)
        out["why"] = tr("panel.scheme.whyPoolExit", name=name) if route["via"] == "pool" \
            else tr("panel.scheme.exitNamed", name=name)
    elif route["via"] in ("direct", "block"):
        out["why"] = tr("panel.scheme.whyRoute", via=tr("panel.scheme." + route["via"]))
    elif n["dead"]:
        out["why"] = tr("panel.scheme.whyDead", name=_exit_name(st, n["dead"][0], pool), fall=fall)
    else:
        out["why"] = tr("panel.scheme.whyNoAlive", fall=fall)
    return out


def _now_by(st, device, pool, alive, enabled=None, decision=None):
    """``_now`` for EVERY enabled inbound of the device: {kind: now}. The path through the kernel and through xray differ
    (an exit by the hash of the device / any live exit), so the scheme takes the answer of the inbound it draws — the
    one chosen in «Через», or the one the device is online through — from here, never computing the hash itself."""
    kinds = set(model.enabled_kinds(st)) if enabled is None else enabled
    if not model.device_active(st, device):
        return {}
    acc = [k for k in device.get("access") or [] if isinstance(k, str) and k in kinds]
    return dict((k, _now(st, device, pool, alive, kinds, inbound=k, decision=decision)) for k in acc)


def _position(layout, nid):
    p = layout["positions"].get(nid)
    return {"x": p[0], "y": p[1]} if p else None


def _ordered(devs, order):
    """Devices of a user in the saved order of the layout (new ones after it, in state order)."""
    if not order:
        return devs
    rank = dict((d, i) for i, d in enumerate(order))
    return sorted(devs, key=lambda d: (rank.get(d.get("id"), len(rank)), devs.index(d)))


def build(st, pool, snap, layout, lists_ready=None, ru=None, decision=None, ru_pool=None):
    """The graph of ``st`` (no commands). ``pool`` — ``pool_exits()``, ``snap`` — the exits
    snapshot, ``layout`` — ``load_layout()``, ``lists_ready`` — the lists are stored, ``ru`` — ``ru_state()`` (what
    «Россия напрямую» does now; without it: «ok» when the lists are stored), ``decision`` — ``exits.decision(st,
    snap, pool)`` (stage 1, §2.2: «сейчас» of every device by the decision of the collector, as gw_route and trace;
    None — read here: pins.json). ``ru_pool`` — ``ru_pool_exits()`` (None — read here when there are Russian groups).

    The Russian groups (T15 Q6, SPEC-groups §8.4): a node «rug:<gid>» each (its keys inside: they are never a route
    target); the line ``ru_via`` device -> its Russian group ONLY where the choice acts — ``exits.ru_target``, the one
    answer of the kernel, xray and the trace: «Россия напрямую» applies to the device (``rules.apply.device_rd``) and
    its «Российские сайты» resolve to that group (``model.ru_via_of``). A node without Russian groups — the graph of
    3.3.7.9 (rows carry ``ru`` with ``acts`` None, nothing is drawn)."""
    g = model.peek(st)
    installed = model.installed_kinds(st)
    enabled = set(model.enabled_kinds(st))
    alive = exits.alive_ids(snap)
    dec = exits.decision(st, snap, pool) if decision is None else decision
    rec_of = snap.get("exits") or {}
    pool_ids = [e["id"] for e in pool]
    supported = profiles_supported()
    droute = default_route(st)
    dprof = default_profile(st)
    if ru is None:
        ru = {"state": "ok" if lists_ready else "no_lists", "domains": None, "error": None, "next": None}
        if lists_ready and not _ru_allowed(st):
            ru["state"] = "no_consent"
    rf = rf_view(st) if supported else {"mode": "none", "required": False, "uncovered": [], "need": None}
    nodes, edges = [], []
    counts = {}                                  # node id -> devices routed there (configured)
    now_counts = {}                              # node id -> devices whose traffic is there now
    prof_count = {}
    ghosts = []
    rug = ru_groups(st)
    if rug and ru_pool is None:
        ru_pool = ru_pool_exits()
    # the decision with the Russian keys too (as the collector takes it): where the Russian sites go now
    ru_dec = exits.decision(st, snap, list(pool) + list(ru_pool or [])) if rug else None
    ru_devices, ru_chosen = {}, {}

    def target_id(via):
        if via.startswith("exit:") and via[5:] not in pool_ids and via[5:] not in ghosts:
            ghosts.append(via[5:])
        return via

    # users and their devices
    users = [u for u in g["users"] if isinstance(u, dict)]
    known_users = set(u.get("id") for u in users)
    devices = [d for d in g["devices"] if isinstance(d, dict)]
    for u in users:
        uid = str(u.get("id"))
        own = _route_or_none(u.get("route"))
        mine = [d for d in devices if d.get("user") == uid]
        rows = []
        for d in _ordered(mine, layout["orders"].get(uid)):
            did = str(d.get("id"))
            eff = model.route_of(st, d)
            prof, pfrom = profile_of(st, d, u)
            prof_count[prof] = prof_count.get(prof, 0) + 1
            active = model.device_active(st, d)
            now = _now(st, d, pool, alive, enabled, decision=dec)
            access = [k for k in d.get("access") or [] if isinstance(k, str)]
            via = target_id(eff["via"])
            if active:
                counts[via] = counts.get(via, 0) + 1
                if now["target"]:
                    now_counts[now["target"]] = now_counts.get(now["target"], 0) + 1
            rv = model.ru_via_of(st, d)
            ru_row = {"via": rv["via"], "from": rv["from"], "acts": None, "now": None}
            if rv.get("broken"):
                ru_row["broken"] = rv["broken"]
            rt = None
            if rug:
                ru_row["acts"] = _ru_acts(st, d, lists_ready)
                if rv["via"] != model.RU_VIA_DIRECT:
                    ru_chosen[rv["via"]] = ru_chosen.get(rv["via"], 0) + 1
                    rt = exits.ru_target(st, d, ru_dec, is_ready=lists_ready) if ru_row["acts"] else None
                    if not (rt and rt.get("group") == rv["via"]):
                        rt = None
                if rt:
                    ru_row["now"] = {"step": rt["step"], "exit": rt.get("exit")}
                    ru_devices[rv["via"]] = ru_devices.get(rv["via"], 0) + 1
            rows.append({
                "id": did, "name": d.get("name") or did, "kind": d.get("kind") or "other",
                "platform": d.get("platform") or "other", "enabled": bool(d.get("enabled", True)),
                "active": active, "login": d.get("login") or "", "access": access,
                "missing": [k for k in access if k not in installed],
                "ips": dict((k, v) for k, v in (d.get("ips") or {}).items() if isinstance(v, str)),
                "route": _route_or_none(d.get("route")), "rules": _own_profile(d),
                "effective": {"via": eff["via"], "fail": eff["fail"], "from": eff["from"]},
                "profile": prof, "profile_from": pfrom, "now": now,
                "now_by": _now_by(st, d, pool, alive, enabled, dec),
                "suits": _suits(d.get("platform")),
                "ru_via": _own_ru_via(d), "ru": ru_row,
            })
            if rt:                               # the choice acts: the line device -> its Russian group
                edges.append({"id": "ruvia:" + did, "type": "ru_via", "source": uid, "sourceHandle": did,
                              "target": RU_NODE + rv["via"], "targetHandle": "in",
                              "data": {"device": did, "user": uid, "group": rv["via"], "from": rv["from"],
                                       "rules": "rules:" + PROFILE_RU, "step": rt["step"], "exit": rt.get("exit")}})
            for k in access:
                if k in installed:
                    edges.append({"id": "access:%s:%s" % (did, k), "type": "access", "source": uid,
                                  "sourceHandle": did, "target": "in:" + k, "targetHandle": "in",
                                  "data": {"device": did, "kind": k, "ip": _ip_of(d, k),
                                           "enabled": k in enabled}})
            edges.append({"id": "route:" + did, "type": "route", "source": uid, "sourceHandle": did,
                          "target": via, "targetHandle": "in",
                          "data": {"device": did, "user": uid, "via": eff["via"], "fail": eff["fail"],
                                   "from": eff["from"], "inherited": eff["from"] != "device",
                                   "rules": "rules:" + prof, "active": active}})
        uprof = _own_profile(u) if supported else None
        nodes.append({"id": uid, "type": "user", "position": _position(layout, uid),
                      "data": {"name": u.get("name") or uid, "enabled": bool(u.get("enabled", True)),
                               "route": own, "rules": uprof, "profile": uprof or dprof,
                               "profile_from": "user" if uprof else "default", "devices": rows,
                               "ru_via": _own_ru_via(u)}})
        if own is not None:
            # a user without a profile of his own follows the node's one («Россия напрямую» out of the box)
            edges.append({"id": "uroute:" + uid, "type": "user_route", "source": uid, "sourceHandle": "user",
                          "target": target_id(own["via"]), "targetHandle": "in",
                          "data": {"user": uid, "via": own["via"], "fail": own["fail"],
                                   "rules": "rules:" + (uprof or dprof)}})
    orphans = [d for d in devices if d.get("user") not in known_users]

    # inbounds: a device connects through ONE of its inbounds; the path goes on from the inbound (flow:<kind> -> filter)
    for kind in installed:
        rec = model.inbound(st, kind) or {}
        ad = registry.get(kind)
        ports = ops.ports_shown(ad, st) if ad is not None else []
        if not ports and isinstance(rec.get("port"), int):
            ports = [str(rec["port"])]
        nid = "in:" + kind
        nodes.append({"id": nid, "type": "inbound", "position": _position(layout, nid),
                      # T13: the label of the inbound only shown («AmneziaWG 2.0 — для роутеров»; gw/rename.py)
                      "data": {"kind": kind, "title": rename.inbound_shown(st, kind), "label": rename.inbound_label(st, kind),
                               "enabled": kind in enabled,
                               "ports": ports, "subnet": rec.get("subnet") or "", "adapter": ad is not None,
                               "devices": sum(1 for d in devices if kind in (d.get("access") or []))}})
        edges.append({"id": "flow:" + kind, "type": "flow", "source": nid, "sourceHandle": "out", "target": FILTER,
                      "targetHandle": "in", "data": {"kind": kind}})

    # the filter of the node: dnsmasq answers every client the same (and xray every SOCKS5 user) — before any profile
    conf = model.rules_conf(st) if supported else {"block_ads": False, "block_torrent": False}
    nodes.append({"id": FILTER, "type": "filter", "position": _position(layout, FILTER),
                  "data": {"ads": bool(conf.get("block_ads")), "torrent": bool(conf.get("block_torrent")),
                           "lists": lists_ready,
                           "rows": [{"id": "ads", "title": tr("panel.scheme.rowAds"), "on": bool(conf.get("block_ads")),
                                     "target": "block"},
                                    {"id": "torrent", "title": tr("panel.scheme.rowTorrent"),
                                     "on": bool(conf.get("block_torrent")), "target": "block"},
                                    {"id": "pass", "title": tr("panel.scheme.rowRest"), "on": True, "target": "rules"}]}})
    # «реклама» и «торренты» отсеивает сам фильтр (ответ DNS «нет адреса»): путь кончается его строкой — линии в
    # «Блок» нет, тот узел — маршрут «блок» и отказ «блок» (ревизия М2)
    # rules profiles: every one the model knows when it has profiles, else the one everybody is on
    names = profile_names() if supported else [PROFILE_ALL]
    for p in sorted(prof_count):
        if p not in names:
            names.append(p)
    for p in names:
        if not supported and not prof_count.get(p):
            continue
        nid = "rules:" + p
        # «всегда через выход» (YouTube, Telegram…) goes by the route like the rest — even on a Russian address:
        # one row, said on it (a line of its own would be a second line to the same place)
        cats = [{"id": "ru", "title": tr("panel.scheme.catRu"), "target": "direct"},
                {"id": "rest", "title": tr("panel.scheme.rowRest"), "note": tr(FORCE_NOTE), "target": "route"}] \
            if p == PROFILE_RU else [{"id": "rest", "title": tr("panel.scheme.catAll"), "target": "route"}]
        nodes.append({"id": nid, "type": "rules", "position": _position(layout, nid),
                      "data": {"profile": p, "title": profile_title(p), "devices": prof_count.get(p, 0),
                               "in_use": bool(prof_count.get(p)), "supported": supported, "default": p == dprof,
                               "lists": (lists_ready if p == PROFILE_RU else None),
                               "ru": (dict(ru) if p == PROFILE_RU else None), "categories": cats}})
        edges.append({"id": "pass:" + p, "type": "pass", "source": FILTER, "sourceHandle": "pass", "target": nid,
                      "targetHandle": "in", "data": {"profile": p}})
        if p == PROFILE_RU:
            edges.append({"id": "cat:%s:ru" % p, "type": "rule", "source": nid, "sourceHandle": "ru",
                          "target": "direct", "targetHandle": "in", "data": {"category": "ru"}})

    # the pool and its exits
    usable_ids = set(e["id"] for e in exits.usable(st, pool, alive))
    hint = exits.add_hint(st)
    nodes.append({"id": "pool", "type": "pool", "position": _position(layout, "pool"),
                  "data": {"exits": pool_ids, "total": len(pool_ids), "usable": len(usable_ids),
                           "alive": sum(1 for i in pool_ids if i in alive), "default": droute["via"] == "pool",
                           "devices": counts.get("pool", 0),
                           # an empty pool: where to add a key (the SSH menu with the right number) and whether the
                           # devices of the default route are without internet now (exits.add_hint, offline_empty)
                           "add_menu": hint["menu"], "add_what": hint["what"],
                           "offline": exits.offline_empty(st, len(pool) or None)}})
    for e in pool:
        row = exits.public(e, st, snap)
        row["usable"] = e["id"] in usable_ids
        row["missing"] = False
        nid = "exit:" + e["id"]
        row["default"] = droute["via"] == nid
        row["devices"] = counts.get(nid, 0)
        row["now_devices"] = now_counts.get(nid, 0)
        nodes.append({"id": nid, "type": "exit", "position": _position(layout, nid), "data": row})
        edges.append({"id": "member:" + e["id"], "type": "member", "source": "pool", "sourceHandle": e["id"],
                      "target": nid, "targetHandle": "in",
                      "data": {"exit": e["id"], "state": row.get("state"), "enabled": row["enabled"],
                               "usable": row["usable"], "problem": row.get("problem")}})
    if droute["via"].startswith("exit:"):
        target_id(droute["via"])
    for eid in ghosts:                       # a route to an exit that is no longer in the pool
        nid = "exit:" + eid
        pref = model.exit_pref(st, eid)
        nodes.append({"id": nid, "type": "exit", "position": _position(layout, nid),
                      "data": {"id": eid, "name": pref["name"] or eid, "kind": "missing", "enabled": pref["enabled"],
                               "missing": True, "usable": False, "alive": False, "state": "missing",
                               "detail": tr("panel.scheme.exitGone"),
                               "since": (rec_of.get(eid) or {}).get("since"), "default": droute["via"] == nid,
                               "devices": counts.get(nid, 0), "now_devices": 0}})
    for nid, typ in (("direct", "direct"), ("block", "block")):
        nodes.append({"id": nid, "type": typ, "position": _position(layout, nid),
                      "data": {"default": droute["via"] == nid, "devices": counts.get(nid, 0),
                               "now_devices": now_counts.get(nid, 0)}})
    ru_by_id = dict((e["id"], e) for e in ru_pool or [])
    for grp in rug:
        nodes.append(_ru_node(st, grp, ru_by_id, snap, ru_dec, layout, ru_devices, ru_chosen))
    # the last links of a path: the «rest» row of a profile -> the route's target, or -> the fail policy's
    # target while the exits of the route are dead (every possible one: the client draws those with devices)
    vias = ["pool"] + ["exit:" + i for i in pool_ids] + ["exit:" + i for i in ghosts] + ["direct", "block"]
    for p in [n["data"]["profile"] for n in nodes if n["type"] == "rules"]:
        for via in vias:
            edges.append({"id": "route:%s:%s" % (p, via), "type": "route_link", "source": "rules:" + p,
                          "sourceHandle": "rest", "target": via, "targetHandle": "in",
                          "data": {"profile": p, "via": via}})
            if via not in ("direct", "block"):
                for fall in ("direct", "block"):
                    edges.append({"id": "fail:%s:%s:%s" % (p, via, fall), "type": "fail", "source": "rules:" + p,
                                  "sourceHandle": "rest", "target": fall, "targetHandle": "in",
                                  "data": {"profile": p, "via": via, "fail": fall}})
    # the row «Сайты и сети России» of «Россия напрямую» -> a Russian group: the client draws it for the devices whose
    # choice acts (the lines ``ru_via`` above), as it draws «route_link» for the routes
    if any(n["type"] == "rules" and n["data"]["profile"] == PROFILE_RU for n in nodes):
        for grp in rug:
            edges.append({"id": "rulink:" + grp["id"], "type": "ru_link", "source": "rules:" + PROFILE_RU,
                          "sourceHandle": "ru", "target": RU_NODE + grp["id"], "targetHandle": "in",
                          "data": {"group": grp["id"]}})
    return {
        "ok": True,
        "version": graph_version(st),
        "layout_version": layout["version"],
        "gateway": {"enabled": model.enabled(st), "engine": st.get("engine") or "", "host": model.public_host(st)},
        "default_route": droute,
        "profiles": {"supported": supported, "names": names, "titles": dict((p, profile_title(p)) for p in names),
                     "default": dprof, "lists": lists_ready, "ru": dict(ru), "rf": rf,
                     "soon": "" if supported else tr(RULES_SOON)},
        "kinds": dict((k, kind_title(k)) for k in model.INBOUND_KINDS),
        "orphans": [str(d.get("id")) for d in orphans],
        "nodes": nodes,
        "edges": edges,
    }


def _ip_of(device, kind):
    from ..gw import addr
    ips = device.get("ips") or {}
    for pool in addr.pools_of(kind):
        if ips.get(pool):
            return ips[pool]
    return None


def _inputs_key():
    return (snapshot.generation(), snapshot.signature(paths.state()), snapshot.signature(exits.snapshot_path()),
            snapshot.signature(paths.egress_up()), snapshot.signature(paths.egress_parser()),
            snapshot.signature(paths.pins_json()),          # the decision of the collector (stage 1: «сейчас»)
            snapshot.signature(paths.egress_up_ru()),       # the keys of the Russian groups (T15 Q6)
            snapshot.signature(layout_path()), snapshot.signature(paths.rules_dir()), _ru_signatures(),
            get_lang())                     # the graph holds texts: one build per language


def _ru_signatures():
    """What ``ru_state`` reads besides the store: the state of the last load into the table, dnsmasq's abilities."""
    try:
        from ..gw import dns
        from ..rules import apply as rules_apply
        return (snapshot.signature(rules_apply.load_state_path()), snapshot.signature(dns.features_path()))
    except ImportError:
        return None


def current_graph():
    """The graph of what is on disk now — built once per change of its inputs; identical concurrent requests
    share one build (single-flight). The result is shared: callers never mutate it."""
    key = _inputs_key()
    with _lock:
        hit = _graph_cache.get("graph")
        if hit is not None and hit[0] == key:
            return hit[1]

    def make():
        st = read_state()
        doc = build(st, pool_exits(), exits_snapshot(), load_layout(), rules_lists_ready(), ru_state(st))
        with _lock:
            _graph_cache["graph"] = (key, doc)
        return doc
    return _flight.do(("scheme",) + key, make)


def scheme_doc():
    doc = dict(current_graph())
    doc["live"] = live_part()
    return doc


# ── layout ─────────────────────────────────────────────────────────────────

def _xy(v):
    if isinstance(v, dict):
        v = [v.get("x"), v.get("y")]
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    out = []
    for c in v:
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) \
                or abs(c) > COORD_LIMIT:
            return None
        out.append(round(float(c), 1))
    return out


def save_layout(body):
    """Merge the positions (and device orders) a tab sends into layout.json — only on top of the version the
    tab has seen: otherwise 409 with what is saved now (the tab merges and retries). Ids of nodes that are not in
    the graph any more are dropped."""
    ver = body.get("version")
    if isinstance(ver, bool) or not isinstance(ver, int) or ver < 0:
        raise HttpError(400, tr("panel.scheme.layoutVersion"), USAGE)
    raw_pos = body.get("positions")
    raw_orders = body.get("orders")
    raw_pos = {} if raw_pos is None else raw_pos
    raw_orders = {} if raw_orders is None else raw_orders
    if not isinstance(raw_pos, dict) or not isinstance(raw_orders, dict):
        raise HttpError(400, tr("panel.scheme.layoutObjects"), USAGE)
    if len(raw_pos) > MAX_POSITIONS or len(raw_orders) > MAX_POSITIONS:
        raise HttpError(400, tr("panel.scheme.layoutTooMany"), USAGE)
    positions = {}
    for k, v in raw_pos.items():
        p = _xy(v)
        if p is None or not _NODE_ID.match(str(k)):
            raise HttpError(400, tr("panel.scheme.layoutBadPos", id=repr(str(k)[:80])), USAGE)
        positions[str(k)] = p
    orders = {}
    for k, ids in raw_orders.items():
        if not _NODE_ID.match(str(k)) or not str(k).startswith("u-") or not isinstance(ids, list) \
                or len(ids) > MAX_POSITIONS or not all(_DEV_ID.match(str(i)) for i in ids):
            raise HttpError(400, tr("panel.scheme.layoutBadOrder", id=repr(str(k)[:80])), USAGE)
        orders[str(k)] = [str(i) for i in ids]
    graph = current_graph()
    ids = set(n["id"] for n in graph["nodes"])
    devs_of = dict((n["id"], set(d["id"] for d in n["data"]["devices"])) for n in graph["nodes"]
                   if n["type"] == "user")
    with _layout_lock:
        cur = load_layout()
        if ver != cur["version"]:
            raise HttpError(409, tr("panel.scheme.layoutStale"),
                            CONFLICT, data={"version": cur["version"], "positions": cur["positions"],
                                            "orders": cur["orders"]})
        merged = {} if body.get("reset") else dict(cur["positions"])
        merged.update(positions)
        merged = dict((k, v) for k, v in merged.items() if k in ids)
        new_orders = {} if body.get("reset") else dict(cur["orders"])
        new_orders.update(orders)
        new_orders = dict((k, [d for d in v if d in devs_of.get(k, ())]) for k, v in new_orders.items()
                          if k in devs_of)
        doc = {"version": cur["version"] + 1, "positions": merged, "orders": new_orders, "updated": util.now_iso()}
        util.atomic_write(layout_path(), util.json_text(doc), 0o600)
        snapshot.forget(layout_path())
    return {"ok": True, "version": doc["version"]}


# ── «Применить» ────────────────────────────────────────────────────────────

def _intents(body):
    raw = body.get("intents")
    if not isinstance(raw, list) or not raw:
        raise HttpError(400, tr("panel.scheme.nothingToApply"), USAGE)
    if len(raw) > MAX_INTENTS:
        raise HttpError(400, tr("panel.scheme.tooManyIntents", max=MAX_INTENTS), USAGE)
    for it in raw:
        if not isinstance(it, dict):
            raise HttpError(400, tr("panel.scheme.intentNotObject"), USAGE)
    return raw


def _type(it):
    return str(it.get("type") or it.get("op") or "")


def _ref(it, key):
    v = it.get(key)
    if v is None or isinstance(v, (dict, list, bool)) or not str(v).strip():
        raise KaskadError(tr({"device": "panel.scheme.refDevice", "user": "panel.scheme.refUser"}[key],
                             type=_type(it) or tr("panel.scheme.intentWord")), code=USAGE)
    return str(v).strip()


def _dev_label(st, d):
    u = model.find_user(st, d.get("user")) or {}
    return tr("panel.scheme.devLabel", name=d.get("name") or d.get("id"), user=u.get("name") or tr("panel.scheme.noUser"))


def _via_text(st, via, pool):
    if via in ("pool", "direct", "block"):
        return tr("panel.scheme." + via)
    eid = model.exit_of({"via": via})
    if eid:
        return tr("panel.scheme.exitNamed", name=_exit_name(st, eid, pool))
    gid = model.group_of_route({"via": via})
    if gid:                                      # stage 1: the name of the group, not its id (REV-s1-b4)
        from ..gw import policy as P
        return tr("panel.scheme.groupNamed", name=P.label(P.group(P.load(st, pool), gid) or {"id": gid}))
    return str(via)


def _route_text(st, r, pool, acts=False):
    """«пул (при отказе — блок)»: the fail policy is said always where it acts — a change of it alone is a change too
    (stand T-scheme-3.2.15, Н1, Н11). ``acts`` — the route as it ACTS now (the default of the node, the route of a
    device): the fail step as the decision takes it — its own policy or its group's (``exits.fail_of``, REV-s1-b4
    З-1); without — the policy the route itself names («было → стало» of an edit)."""
    s = _via_text(st, r["via"], pool)
    if r["via"] not in ("direct", "block"):
        fail = r.get("fail")
        if acts:
            from ..gw import policy as P
            fail = exits.fail_of(st, r, {"policy": P.load(st, pool)}) or fail
        s = tr("panel.scheme.routeWithFail", via=s, fail=tr("panel.scheme.failDirect") if fail == "direct"
               else tr("panel.scheme.block"))
    return s


def _eff_text(st, r, pool):
    """The route a device follows now and where it comes from — with the fail step that ACTS (REV-s1-b4 З-1)."""
    src = {"device": "panel.scheme.srcOwn", "user": "panel.scheme.srcUser",
           "default": "panel.scheme.srcDefault"}.get(r.get("from"))
    return tr("panel.scheme.effText", src=tr(src), route=_route_text(st, r, pool, acts=True)) if src \
        else _route_text(st, r, pool, acts=True)


def _eff_of(st, what, obj):
    """The route a device or a person follows now and where it comes from, for people: ({"via", "fail"}, «свой» |
    «как у пользователя «…»» | «по умолчанию узла»)."""
    if what == "device":
        r = model.route_of(st, obj)
        if r["from"] == "user":
            u = model.find_user(st, obj.get("user")) or {}
            return r, tr("panel.scheme.srcUserNamed", name=u.get("name") or u.get("id") or "")
        return r, tr("panel.scheme.srcOwn") if r["from"] == "device" else tr("panel.scheme.srcDefault")
    own = _route_or_none(obj.get("route"))
    return (own, tr("panel.scheme.srcOwn")) if own else (default_route(st), tr("panel.scheme.srcDefault"))


def _route_change(st_old, obj_old, st_new, obj_new, what, label, pool):
    """«было → станет» of a route of a device or a person, with what it inherited (T-scheme-3.2.16, Н-7): a route of
    its own equal to the inherited one changes no path — said so («путь не меняется: маршрут закрепляется за …»); a
    removed one — «→ по умолчанию узла: …». -> (text, the path is the same)."""
    rb, sb = _eff_of(st_old, what, obj_old)
    ra, sa = _eff_of(st_new, what, obj_new)
    own = _route_or_none(obj_new.get("route"))
    same = (rb["via"], rb.get("fail")) == (ra["via"], ra.get("fail")) or \
        (rb["via"] == ra["via"] and rb["via"] in ("direct", "block"))
    if own and same:
        return tr("panel.scheme.changeSameDevice" if what == "device" else "panel.scheme.changeSameUser",
                  label=label, route=_route_text(st_new, ra, pool)), True
    if own:
        return tr("panel.scheme.changeOwn", label=label, src=sb, before=_route_text(st_old, rb, pool),
                  after=_route_text(st_new, ra, pool)), same
    return tr("panel.scheme.changeRemovedSame" if same else "panel.scheme.changeRemoved", label=label, src=sa,
              route=_route_text(st_new, ra, pool)), same


class _Ctx(object):
    """What one pass over the intents knows besides the state: the pool, liveness, the secrets (real apply only:
    a dry pass has none and drops nothing), the device the admin opened the panel from."""

    def __init__(self, pool, snap, sec=None, client_device=None, live=None):
        self.pool = pool
        self.pool_ids = set(e["id"] for e in pool)
        self.by_id = dict((e["id"], e) for e in pool)
        self.alive = exits.alive_ids(snap)
        self.sec = sec
        self.client_device = client_device
        self.live = live or {}


def _exit_warnings(st, ctx, via, fail):
    from ..gw import policy as P
    eid = model.exit_of({"via": via})
    pol = P.load(st, ctx.pool)
    # the fail step as the decision takes it: the route's own policy or its group's — «main» for the pool, the group
    # of E for exit:E (REV-s1-b2 З1, the same as the group branch below)
    fall = tr("panel.scheme.direct") if exits.fail_of(st, {"via": via, "fail": fail}, {"policy": pol}) == "direct" \
        else tr("panel.scheme.block")
    if eid:
        e = ctx.by_id.get(eid)
        if e is not None and not exits.parsed(e):
            return [tr("panel.scheme.warnKeyBroken", problem=e.get("problem") or "?", fall=fall)]
        if not model.exit_pref(st, eid)["enabled"]:
            return [tr("panel.scheme.warnExitOff", fall=fall)]
        if eid not in ctx.alive:
            return [tr("panel.scheme.warnExitDead", fall=fall)]
        return []
    if via == "pool" and not exits.usable(st, ctx.pool, ctx.alive):
        return [tr("panel.scheme.warnPoolDead", fall=fall)]
    if str(via or "").startswith(model.GROUP_VIA):      # stage 1 (REV-s1-t4 З6): the whole group of the route dead
        gid = via[len(model.GROUP_VIA):]
        grp = P.group(pol, gid)
        mine = set(P.exits_of(grp)) if grp else set()
        alive = [e for e in ctx.pool if e["id"] in mine and exits.parsed(e) and e["id"] in ctx.alive
                 and model.exit_pref(st, e["id"])["enabled"]]
        if not alive:
            if (grp or {}).get("fail") == "direct":
                fall = tr("panel.scheme.direct")
            return [tr("panel.scheme.warnGroupDead", name=P.label(grp or {"id": gid}), fall=fall)]
    return []


def _leak_warnings(label, r):
    """A route that lets the traffic out past the exits (ревизия С3): «напрямую» — always, a fail policy «напрямую» —
    when the exits die. Said in «Применить», not discovered later."""
    if not r:
        return []
    if r["via"] == "direct":
        return [tr("panel.scheme.warnLeakDirect", label=label)]
    if r["via"] != "block" and r.get("fail") == "direct":
        return [tr("panel.scheme.warnLeakFail", label=label)]
    return []


def _xray_carried(st, d, ctx):
    """The device is online now through xray of the gateway: SOCKS5 or the VLESS inbound, or a VLESS exit (the balancer or
    its own)."""
    lv = (ctx.live.get("devices") or {}).get(d.get("id")) if ctx.live else None
    if not isinstance(lv, dict) or not lv.get("online") or not model.device_active(st, d):
        return False
    if lv.get("inbound") in model.XRAY_KINDS:
        return True
    mark = exits.target(st, d, ctx.pool, ctx.alive)["mark"]
    return mark == exits.MARK_XRAY or exits.VLESS_MARK_BASE < mark <= exits.VLESS_MARK_BASE + exits.MAX_VLESS_INDEX


# xray inbounds whose new users come live (VLESS — ``adu``, Hysteria2 — gRPC AlterInbound): only a user leaving restarts
_HOT_USER_KINDS = ("vless", "hy2")


def disruptions(old, new, ctx, ready=None):
    """What the change restarts for EVERYBODY (ревизия С5), without a command: xray of the gateway — its config holds
    the SOCKS5 accounts with their routes and profiles (``rules.apply.xray_rules``, the adapters' routing; VLESS users
    change live through xray's API, no restart):
    every connection through SOCKS5, the VLESS inbound and the VLESS exits reconnects; dnsmasq — its config changes with «a device may
    go directly» (``exits.direct_needed``) and «a client of the kernel is ru_direct» (``rules.apply.kernel_clients``),
    SIGHUP does not re-read nftset: names do not resolve for a moment. [{"what": "xray"|"dns", "devices", "text"}]."""
    if not model.enabled(new):
        return []
    from ..rules import apply as rules_apply
    ready = rules_lists_ready() if ready is None else ready
    out = []

    def socks(st):
        # VLESS: its users and their routes go live through xray's API (docs/k3/SPEC-vless-inbound.md §2.1) — no restart.
        # SOCKS5, stage 1 (§2.4, REV-s1-t45 З2): its routes and profiles go live too (``xray.hot_plan``); a restart only
        # when an account comes or goes (SOCKS5 is no UserManager) or one goes to «block» by choice (its connections)
        kinds = [k for k in model.XRAY_KINDS if k not in _HOT_USER_KINDS and k in model.enabled_kinds(st)]
        accounts, blocked = set(), set()
        for d in model.devices(st):
            for k in kinds:
                if k in (d.get("access") or []) and model.device_active(st, d):
                    accounts.add((k, d.get("id")))
                    if model.route_of(st, d)["via"] == "block":
                        blocked.add((k, d.get("id")))
        return accounts, blocked
    (ao, bo), (an, bn) = socks(old), socks(new)
    if ao != an or (bn - bo) & ao:
        n = sum(1 for d in model.devices(old) if _xray_carried(old, d, ctx))
        out.append({"what": "xray", "devices": n,
                    "text": tr("panel.scheme.disruptXrayN", n=n) if n else tr("panel.scheme.disruptXray")})
    def vless(st):                     # VLESS / Hysteria2 devices on the gateway xray and whether their route is «block»
        on = [k for k in _HOT_USER_KINDS if k in model.enabled_kinds(st)]
        return dict(((k, d.get("id")), model.route_of(st, d)["via"] == "block") for d in model.devices(st)
                    for k in on if k in (d.get("access") or []) and model.device_active(st, d))
    vo, vn = vless(old), vless(new)
    gone = [i for i in vo if i not in vn or (vn[i] and not vo[i])]
    if gone and not out:              # a device leaves VLESS: xray restarts on purpose (its open connections go)
        out.append({"what": "xray", "devices": len(vo),
                    "text": tr("panel.scheme.disruptVless")})
    try:
        dns_old = (exits.direct_needed(old), rules_apply.kernel_clients(old, ready))
        dns_new = (exits.direct_needed(new), rules_apply.kernel_clients(new, ready))
    except (KaskadError, OSError, ValueError):
        dns_old = dns_new = None
    if dns_old != dns_new:
        out.append({"what": "dns", "devices": None,
                    "text": tr("panel.scheme.disruptDns")})
    return out


def _check_exit(via, ctx):
    eid = model.exit_of({"via": via})
    if eid and eid not in ctx.pool_ids:
        raise KaskadError(tr("panel.scheme.exitNotInPool", id=eid), code=CONFLICT)


def _run_intent(st, it, ctx):
    """One intent on ``st`` (a copy) with the core's checks. -> {"type", "changed", "text", "warnings", ...};
    raises KaskadError (USAGE — a bad request, CONFLICT — the scheme does not allow it)."""
    t = _type(it)
    pool = ctx.pool
    if t in ("grant", "revoke"):
        d = model.get_device(st, _ref(it, "device"))
        kind = str(it.get("kind") or "").strip()
        if kind not in model.INBOUND_KINDS:
            raise KaskadError(tr("panel.scheme.unknownInbound", kind=repr(kind), kinds=", ".join(model.INBOUND_KINDS)),
                              code=USAGE)
        title = kind_title(kind)
        had = kind in (d.get("access") or [])
        out = {"type": t, "device": d["id"], "kind": kind, "warnings": []}
        if t == "grant":
            rec = model.inbound(st, kind)
            if rec is None:
                raise KaskadError(tr("panel.scheme.inboundNotInstalled", title=title), code=CONFLICT)
            ips = model.grant(st, d, kind)
            out.update(changed=not had, ips=ips,
                       text=tr("panel.scheme.grantHad", label=_dev_label(st, d), title=title) if had else
                       tr("panel.scheme.grantText", label=_dev_label(st, d), title=title,
                          addr=tr("panel.scheme.addrSuffix", ip=", ".join(sorted(ips.values()))) if ips else ""))
            if rec.get("enabled", True) is False:
                out["warnings"].append(tr("panel.scheme.warnInboundOff", title=title))
            suits = _suits(d.get("platform"))
            if not had and suits is not None and kind not in suits and d.get("platform") not in (None, "", "other"):
                out["warnings"].append(tr("panel.scheme.warnUnsuited", title=title))
            return out
        gone = model.revoke(st, d, kind)
        if had and ctx.sec is not None:
            for p in ops._secret_prefixes(kind, d["id"]):
                ctx.sec.delete_prefix(p)
        out.update(changed=had, released=gone,
                   text=tr("panel.scheme.revokeText", label=_dev_label(st, d), title=title,
                           addr=tr("panel.scheme.revokeAddr", ip=", ".join(gone)) if gone else "")
                   if had else tr("panel.scheme.revokeNone", label=_dev_label(st, d), title=title))
        if had and not d.get("access"):
            out["warnings"].append(tr("panel.scheme.warnNoInboundsLeft"))
        live = (ctx.live.get("devices") or {}).get(d["id"]) or {}
        if had and ctx.client_device == d["id"] and live.get("inbound") == kind:
            out["warnings"].append(tr("panel.scheme.warnPanelInbound"))
        return out
    if t == "route":
        via = it.get("via")
        via = "inherit" if via in (None, "", "inherit") else str(via).strip()
        fail = it.get("fail")
        if fail is not None and fail not in model.FAILS:
            raise KaskadError(tr("panel.scheme.failBad", fail=repr(fail)), code=USAGE)
        if it.get("default"):
            if via == "inherit":
                raise KaskadError(tr("panel.scheme.defaultNoInherit"), code=USAGE)
            r = model.parse_route(via, fail or model.kept_fail(st, "default"))
            _check_exit(r["via"], ctx)
            before = default_route(st)
            model.set_default_route(st, r["via"], r["fail"], pool=pool)
            return {"type": t, "default": True, "changed": before != r, "route": r,
                    "warnings": _exit_warnings(st, ctx, r["via"], r["fail"]) +
                    (_leak_warnings(tr("panel.scheme.defaultRoute"), r) if before != r else []),
                    "text": tr("panel.scheme.defaultRouteText", route=_route_text(st, r, pool, acts=True))}
        if it.get("device") is not None:
            obj = model.get_device(st, _ref(it, "device"))
            label, what = _dev_label(st, obj), "device"
        elif it.get("user") is not None:
            obj = model.get_user(st, _ref(it, "user"))
            label, what = obj.get("name") or obj["id"], "user"
        else:
            raise KaskadError(tr("panel.scheme.routeNeedTarget"), code=USAGE)
        before = _route_or_none(obj.get("route"))
        st_old, obj_old = copy.deepcopy(st), copy.deepcopy(obj)
        if via == "inherit":
            model.set_route(obj, None)
            after = None
        else:
            if fail is None:                     # a new target keeps the policy the object had or inherited (Н1)
                fail = model.kept_fail(st, what, obj)
            r = model.parse_route(via, fail)
            _check_exit(r["via"], ctx)
            after = model.set_route(obj, r["via"], r["fail"], st=st, pool=pool)
        text, same = _route_change(st_old, obj_old, st, obj, what, label, pool)
        # «мимо выхода» — только если путь правда становится таким (свой = унаследованному ничего не ухудшает, Н-7)
        out = {"type": t, what: obj["id"], "changed": before != after, "route": after, "text": text, "same_path": same,
               "warnings": _leak_warnings(label, after) if (after and before != after and not same) else []}
        if not before and not after:
            out["text"] = tr("panel.scheme.noOwnRoute", label=label,
                             eff=_eff_text(st, model.route_of(st, obj), pool) if what == "device" else
                             tr("panel.scheme.effText", src=tr("panel.scheme.srcDefault"),
                                route=_route_text(st, default_route(st), pool, acts=True)))
        if what == "device":
            eff = model.route_of(st, obj)
            out["warnings"] += [] if same else _exit_warnings(st, ctx, eff["via"], eff["fail"])
            if ctx.client_device == obj["id"] and eff["via"] == "block":
                out["warnings"].append(tr("panel.scheme.warnPanelBlock"))
        else:
            r = after or default_route(st)
            out["warnings"] += [] if same else _exit_warnings(st, ctx, r["via"], r["fail"])
        return out
    if t == "rules":
        if not profiles_supported():
            raise _RulesUnavailable(tr(RULES_SOON))
        prof = str(it.get("profile") or "").strip() or "inherit"
        if prof != "inherit" and prof not in profile_names():
            raise KaskadError(tr("panel.scheme.rulesProfileBad", names=", ".join(profile_names())), code=USAGE)
        if it.get("device") is not None:
            obj = model.get_device(st, _ref(it, "device"))
            label, what = _dev_label(st, obj), "device"
        elif it.get("user") is not None:
            obj = model.get_user(st, _ref(it, "user"))
            label, what = obj.get("name") or obj["id"], "user"
        else:
            raise KaskadError(tr("panel.scheme.rulesNeedTarget"), code=USAGE)
        before = _own_profile(obj)
        _set_profile(obj, prof)
        after = _own_profile(obj)
        return {"type": t, what: obj["id"], "changed": before != after, "profile": prof, "warnings": [],
                "text": tr("panel.scheme.rulesText", label=label,
                           what=tr("panel.scheme.profileQuoted", title=profile_title(prof)) if prof != "inherit"
                           else _inherited_rules_text(st, obj, what))}
    if t == "ru_via":
        return _run_ru_via(st, it)
    raise KaskadError(tr("panel.scheme.unknownIntent", type=repr(t)), code=USAGE)


def _ru_value_text(st, via):
    """«напрямую с сервера» | «через «Дом»» — a value of «Российские сайты» (a group by its shown name)."""
    if via in (None, model.RU_VIA_DIRECT):
        return tr("panel.scheme.ruDirect")
    from ..gw import policy as P
    return tr("panel.scheme.ruGroup", name=P.label(P.group(P.stored(st) or {}, via) or {"id": via}))


def _ru_via_text(st, obj, what, label):
    """«<кто>: российские сайты → через «Дом»» / «→ как у пользователя: …» / «→ по умолчанию: напрямую с сервера»."""
    own = _own_ru_via(obj)
    if own is not None:
        what_t = _ru_value_text(st, own)
    elif what == "device":
        u = model.find_user(st, obj.get("user")) or {}
        uv = _own_ru_via(u)
        what_t = tr("panel.scheme.ruAsUser", what=_ru_value_text(st, model.ru_via_of(st, obj)["via"])) if uv \
            else tr("panel.scheme.ruDefault")
    else:
        what_t = tr("panel.scheme.ruDefault")
    return tr("panel.scheme.ruText", label=label, what=what_t)


def _run_ru_via(st, it):
    """``ru_via {device|user, via}`` (SPEC-groups §8.3–8.4): «Российские сайты» of a device or a person — ``inherit``,
    ``direct`` or a Russian group of the stored policy (``model.set_ru_via``: another value — USAGE, nothing changes).
    It never asks the consent ⚠РФ and gives none. A group chosen where «Россия напрямую» does not act — a warning, the
    choice is kept (it acts once the profile does)."""
    via = it.get("via")
    if via is not None and not isinstance(via, str):
        raise KaskadError(tr("gw_model.err.ru_via_bad", value=repr(via)), code=USAGE)
    if it.get("device") is not None:
        obj = model.get_device(st, _ref(it, "device"))
        label, what = _dev_label(st, obj), "device"
    elif it.get("user") is not None:
        obj = model.get_user(st, _ref(it, "user"))
        label, what = obj.get("name") or obj["id"], "user"
    else:
        raise KaskadError(tr("panel.scheme.ruNeedTarget"), code=USAGE)
    before = _own_ru_via(obj)
    got = model.set_ru_via(st, obj, via)
    after = _own_ru_via(obj)
    out = {"type": "ru_via", what: obj["id"], "changed": before != after, "ru_via": got, "warnings": [],
           "text": _ru_via_text(st, obj, what, label)}
    devs = [obj] if what == "device" else [d for d in model.devices(st) if d.get("user") == obj["id"]
                                           and _own_ru_via(d) is None]
    groups = [d for d in devs if model.ru_via_of(st, d)["via"] != model.RU_VIA_DIRECT]
    if out["changed"] and groups and not any(_ru_acts(st, d, None) for d in groups):
        out["warnings"].append(tr("panel.scheme.ruNotActing", label=label))
    return out


def _inherited_rules_text(st, obj, what):
    """«как у пользователя: «Россия напрямую»» / «по умолчанию узла: «…»» — what a removed own profile becomes."""
    off = tr("panel.scheme.rfOff") if _rf_off(st, obj, what) else ""
    if what == "device":
        p, src = profile_of(st, obj)
        if src == "user":
            return tr("panel.scheme.inheritUser", title=profile_title(p)) + off
    p = default_profile(st)
    return tr("panel.scheme.inheritDefault", title=profile_title(p)) + off


def _err(e):
    return {"ok": False, "error": util.redact(str(e)), "code": getattr(e, "code", ERROR)}


def _pass(st, intents, ctx, stop=False):
    """All intents in order on ``st``. ``stop``: the first failure raises _IntentsFailed (the real apply);
    else every failure is reported and the pass goes on (the dry run shows every problem at once)."""
    results = []
    for i, it in enumerate(intents):
        try:
            r = _run_intent(st, it, ctx)
            r.update(i=i, ok=True)
        except _RulesUnavailable as e:
            r = dict(_err(e), i=i, type=_type(it))
            if stop:
                raise _IntentsFailed(results + [r] + _skipped(intents, i + 1), status=409, text=str(e),
                                     code=UNSUPPORTED)
            results.append(r)
            continue
        except KaskadError as e:
            r = dict(_err(e), i=i, type=_type(it))
            if stop:
                raise _IntentsFailed(results + [r] + _skipped(intents, i + 1))
        results.append(r)
    return results


def _skipped(intents, start):
    return [{"i": i, "type": _type(intents[i]), "ok": False, "skipped": True, "error": tr("panel.scheme.notApplied"),
             "code": CONFLICT} for i in range(start, len(intents))]


def diff(old, new, pool, live=None, ready=None):
    """What changes between two states, for people: access, own routes, rules, the default route — and the
    devices it touches (``_affected``: their path — route and profile —, their inbounds). ``live`` — ``live_part()``
    (who is online through what), ``ready`` — the lists are stored (None: read them)."""
    out = []
    od = dict((d.get("id"), d) for d in model.devices(old))
    for d in model.devices(new):
        o = od.get(d.get("id")) or {}
        label = _dev_label(new, d)
        oa, na = list(o.get("access") or []), list(d.get("access") or [])
        for k in na:
            if k not in oa:
                ip = _ip_of(d, k)
                out.append({"op": "+", "what": "access", "device": d["id"], "kind": k,
                            "text": tr("panel.scheme.diffAdd", label=label, title=kind_title(k),
                                       addr=tr("panel.scheme.addrSuffix", ip=ip) if ip else "")})
        for k in oa:
            if k not in na:
                out.append({"op": "-", "what": "access", "device": d["id"], "kind": k,
                            "text": tr("panel.scheme.diffRemove", label=label, title=kind_title(k))})
        ro, rn = _route_or_none(o.get("route")), _route_or_none(d.get("route"))
        if ro != rn:
            out.append({"op": "~", "what": "route", "device": d["id"],
                        "text": _route_change(old, o, new, d, "device", label, pool)[0]})
        po, pn = _own_profile(o), _own_profile(d)
        if po != pn:
            out.append({"op": "~", "what": "rules", "device": d["id"],
                        "text": tr("panel.scheme.rulesText", label=label,
                                   what=tr("panel.scheme.profileQuoted", title=profile_title(pn)) if pn
                                   else _inherited_rules_text(new, d, "device"))})
        if _own_ru_via(o) != _own_ru_via(d):
            out.append({"op": "~", "what": "ru_via", "device": d["id"], "text": _ru_via_text(new, d, "device", label)})
    ou = dict((u.get("id"), u) for u in model.users(old))
    for u in model.users(new):
        o = ou.get(u.get("id")) or {}
        ro, rn = _route_or_none(o.get("route")), _route_or_none(u.get("route"))
        if ro != rn:
            out.append({"op": "~", "what": "route", "user": u["id"],
                        "text": _route_change(old, o, new, u, "user", u.get("name") or u["id"], pool)[0]})
        po, pn = _own_profile(o), _own_profile(u)
        if po != pn:
            out.append({"op": "~", "what": "rules", "user": u["id"],
                        "text": tr("panel.scheme.rulesText", label=u.get("name"),
                                   what=tr("panel.scheme.profileQuoted", title=profile_title(pn)) if pn
                                   else _inherited_rules_text(new, u, "user"))})
        if _own_ru_via(o) != _own_ru_via(u):
            out.append({"op": "~", "what": "ru_via", "user": u["id"],
                        "text": _ru_via_text(new, u, "user", u.get("name") or u["id"])})
    do, dn = default_route(old), default_route(new)
    if do != dn:
        out.append({"op": "~", "what": "default",
                    "text": tr("panel.scheme.defaultChange", before=_route_text(old, do, pool),
                               after=_route_text(new, dn, pool))})
    return out, _affected(old, new, od, pool, live, ready)


def _applied_profile(st, device, ready):
    """The profile that works for a device: «Россия напрямую» without the lists or without the consent ⚠РФ is «всё
    через выход» (``rules.apply.ru_ok``, рубеж 2 — R2; the mark «не действует» of the chosen profile — R4)."""
    from ..rules import apply as rules_apply
    p = profile_of(st, device)[0]
    return PROFILE_ALL if (p == PROFILE_RU and not rules_apply.ru_ok(st, is_ready=ready)) else p


def _path_text(st, route, prof, pool, with_rules):
    s = _route_text(st, route, pool)
    return tr("panel.scheme.pathText", title=profile_title(prof), route=s) if (with_rules and prof) else s


def _profile_on(route, prof):
    """The profile where it decides something: under the route «напрямую» everything goes directly anyway, under «блок»
    by choice nothing goes (Russia too: such a device is never in gw_rd) — there it changes no path (None). Stage 1
    (SE §2.3, Б1): the fallback block of a group (its exits unfit) keeps Russia directly — that is no route here."""
    return None if route["via"] in ("direct", "block") else prof


def _leak_now(before, after):
    """The path now goes out past the exits where it did not: «direct» (always) or the fail policy «direct»."""
    def leaks(r):
        return "always" if r["via"] == "direct" else "fail" if (r["via"] != "block" and r.get("fail") == "direct") else ""
    a = leaks(after)
    return a if a and a != leaks(before) else ""


def _affected(old, new, od, pool, live, ready):
    """Devices the change touches: ``kind`` «path» — the way of their traffic (the route, its fail policy, the profile
    that applies: a profile of a user moves every device that follows it), «ru» — where their Russian sites go, when
    a Russian group comes into it or leaves it (T15 Q6: through somebody's router or not), «access» — an inbound given
    (the device needs the new settings) or taken (its keys go; online through it now — the connection drops)."""
    if ready is None:
        ready = rules_lists_ready()
    online = (live or {}).get("devices") or {}
    affected = []
    has_ru = bool(ru_groups(old) or ru_groups(new))      # «kind: ru» — where a Russian group comes or goes (§8.4)
    for d in model.devices(new):
        o = od.get(d.get("id"))
        if o is None:
            continue
        name = d.get("name") or d["id"]
        before, after = model.route_of(old, o), model.route_of(new, d)
        pb = _profile_on(before, _applied_profile(old, o, ready))
        pa = _profile_on(after, _applied_profile(new, d, ready))
        moved = (before["via"], before["fail"]) != (after["via"], after["fail"])
        if moved or pb != pa:
            affected.append({"kind": "path", "device": d["id"], "name": name,
                             "before": _path_text(old, before, pb, pool, pb != pa),
                             "after": _path_text(new, after, pa, pool, pb != pa),
                             "from": after["from"], "profile_before": pb, "profile_after": pa,
                             "leak": _leak_now(before, after)})
        rb = _ru_target_of(old, o, ready) if has_ru else None
        ra = _ru_target_of(new, d, ready) if has_ru else None
        if rb != ra and any(v not in (None, model.RU_VIA_DIRECT) for v in (rb, ra)):
            affected.append({"kind": "ru", "device": d["id"], "name": name, "before": _ru_path_text(old, rb),
                             "after": _ru_path_text(new, ra), "from": model.ru_via_of(new, d)["from"]})
        oa, na = list(o.get("access") or []), list(d.get("access") or [])
        for k in na:
            if k not in oa:
                affected.append({"kind": "access", "op": "+", "device": d["id"], "name": name, "inbound": k,
                                 "text": tr("panel.scheme.accessNew", title=kind_title(k))})
        for k in oa:
            if k not in na:
                lv = online.get(d["id"]) if isinstance(online.get(d["id"]), dict) else {}
                text = tr("panel.scheme.accessRevoked", title=kind_title(k))
                if lv.get("online") and lv.get("inbound") == k:
                    text += tr("panel.scheme.accessRevokedOnline")
                if not na:
                    text += tr("panel.scheme.accessRevokedLast")
                affected.append({"kind": "access", "op": "-", "device": d["id"], "name": name, "inbound": k,
                                 "text": text})
    return affected


def _ru_target_of(st, device, ready):
    """Where the Russian sites of a device go by the settings: None — «Россия напрямую» does not act (they go by the
    route), ``direct`` or the id of its Russian group (``model.ru_via_of``)."""
    if not _ru_acts(st, device, ready):
        return None
    return model.ru_via_of(st, device)["via"]


def _ru_path_text(st, target):
    return tr("panel.scheme.ruByRoute") if target is None else \
        tr("panel.scheme.ruPath", what=_ru_value_text(st, target))


def _client_device(st, client_ip):
    if not client_ip:
        return None
    d = model.find_device_by_ip(st, str(client_ip))
    return d["id"] if d else None


def rf_of(intents):
    """``rf`` of a batch (R4, §5.1): a rules intent with ``confirm_not_rf: true`` — the tick under the block in the
    inspector or in the window «Применить» (``via = scheme``, ``orgs`` — of the block shown). A tick that is not
    true/false, ``orgs`` that are not a list of names — 400."""
    from ..rules import panel as rules_panel
    rf = None
    for it in intents:
        if _type(it) != "rules" or ("confirm_not_rf" not in it and "orgs" not in it):
            continue
        got = rules_panel.rf_of(it, via="scheme")
        rf = rf or got
    return rf


def _rf_need(old, new, rf, results):
    """The dry run of the check of ``_change`` (``rfgate.check`` on a copy, nothing written): the tick is needed —
    ``results[].need`` of the rules intents that chose «Россия напрямую» (all rules intents when the block widened
    under a tick); ``results[].ok`` stays true (П13). -> ``data`` of the refusal or None."""
    from ..rules import rfgate
    try:
        rfgate.check(old, copy.deepcopy(new), rf, refuse=True, log=[])
        return None
    except KaskadError as e:
        data = getattr(e, "data", None)
        if not isinstance(data, dict) or data.get("need") != "rf_consent":
            raise
    n = rfgate.need(old, new) or {}
    ids = set(p[1] for p in n.get("points") or [] if len(p) > 1)
    rules = [r for r in results if r.get("ok") and r.get("type") == "rules"]
    hit = [r for r in rules if (r.get("device") or r.get("user")) in ids] or rules
    for r in hit:
        r["need"] = dict(data)
    return data


def dry_run(intents, client_ip=None, rf=None):
    """Every intent on a deep copy of the state in memory: per-intent results, the changes, the affected devices.
    Never writes anything (state.json, secrets, layout stay byte for byte). ``rf`` — the tick ⚠РФ of the batch
    (``rf_of``); without it where it is needed — ``need`` (R4)."""
    st = state.load()
    new = copy.deepcopy(st)
    pool = pool_exits()
    ctx = _Ctx(pool, exits_snapshot(), sec=None, client_device=_client_device(st, client_ip), live=live_part())
    results = _pass(new, intents, ctx)
    need = _rf_need(st, new, rf, results) if profiles_supported() else None
    changes, affected = diff(st, new, pool, ctx.live)
    if not profiles_supported() and any(_type(i) == "rules" for i in intents):
        raise HttpError(409, tr(RULES_SOON), UNSUPPORTED, data={"results": results})
    warnings = [w for r in results for w in r.get("warnings") or []]
    for a in affected:
        if a.get("leak") and a.get("from") != "device":
            w = tr("panel.scheme.leakAlways" if a["leak"] == "always" else "panel.scheme.leakFail",
                   name=a["name"], after=a["after"])
            if w not in warnings:
                warnings.append(w)
    return {"ok": True, "dry_run": True, "valid": all(r["ok"] for r in results), "version": graph_version(st),
            "noop": graph_version(new) == graph_version(st), "results": results, "changes": changes,
            "affected": affected, "disruptions": disruptions(st, new, ctx), "warnings": warnings, "need": need}


def _log(results, st):
    for r in results:
        if not r.get("changed"):
            continue
        t = r["type"]
        if t in ("grant", "revoke"):
            d = model.find_device(st, r["device"]) or {"name": r["device"], "id": r["device"]}
            util.log_tr("journal.scheme.device", act=t, name=d.get("name"), id=d.get("id"), kind=r["kind"])
        elif t == "route" and r.get("default"):
            rr = r["route"]
            util.log_tr("journal.scheme.route_default", via=rr["via"],
                        fail=(" (%s)" % rr["fail"]) if rr["via"] not in ("block", "direct") else "")
        elif t == "route":
            via = (r.get("route") or {}).get("via", "inherit")
            if "device" in r:
                d = model.find_device(st, r["device"]) or {}
                util.log_tr("journal.scheme.device_route", name=d.get("name"), id=r["device"], via=via)
            else:
                u = model.find_user(st, r["user"]) or {}
                util.log_tr("journal.scheme.user_route", name=u.get("name"), via=via)
        elif t == "rules":
            util.log_tr("journal.scheme.rules", who="device" if "device" in r else "user",
                        id=r.get("device") or r.get("user"), profile=r["profile"])
        elif t == "ru_via":
            util.log_tr("journal.scheme.ru_via", who="device" if "device" in r else "user",
                        id=r.get("device") or r.get("user"), via=r["ru_via"])


def apply(body, client_ip=None, runner=None):
    """``POST /api/scheme/apply``. Dry run: ``dry_run()``. Real: the same dry pass first (nothing locked, a bad
    intent costs no apply), then under the state lock — the version the client saw, every intent again in order on
    one copy, ONE apply of the gateway, save (``ops._change``); then the new graph."""
    intents = _intents(body)
    rf = rf_of(intents)
    if body.get("dry_run"):
        return dry_run(intents, client_ip, rf)
    version = body.get("version")
    if version is not None and not isinstance(version, str):
        raise HttpError(400, tr("panel.scheme.versionString"), USAGE)
    pre = dry_run(intents, client_ip, rf)
    if version is not None and pre["version"] != version:
        raise HttpError(409, tr("panel.scheme.stale"), CONFLICT, data={"reason": "stale", "version": pre["version"]})
    if not pre["valid"]:
        bad = [r for r in pre["results"] if not r["ok"]]
        raise HttpError(409, tr("panel.scheme.nothingApplied", error=bad[0]["error"]), bad[0].get("code") or CONFLICT,
                        data={"results": pre["results"]})
    if pre["noop"]:                          # nothing in the state would change: no apply of the gateway for it
        return {"ok": True, "applied": False, "results": pre["results"], "changes": [], "affected": [],
                "warnings": pre["warnings"], "graph": scheme_doc()}
    pool = pool_exits()
    snap = exits_snapshot()
    live = live_part()
    box = {}

    def mutate(new, sec):
        if version is not None and graph_version(new) != version:
            raise _Stale()
        old = copy.deepcopy(new)
        box["results"] = _pass(new, intents, _Ctx(pool, snap, sec=sec, client_device=_client_device(new, client_ip),
                                                  live=live), stop=True)
        box["old"] = old
        box["new"] = new
        return box["results"]

    runner = runner or sysrun.default_runner()
    try:
        results, res = ops._change(runner, mutate, rf=rf)        # the check ⚠РФ: right after mutate, before _apply
    except _Stale:
        raise HttpError(409, tr("panel.scheme.stale"), CONFLICT, data={"reason": "stale"})
    except _IntentsFailed as e:
        bad = next((r for r in e.results if not r["ok"] and not r.get("skipped")), {})
        raise HttpError(e.status, e.text or tr("panel.scheme.nothingApplied", error=bad.get("error", "")), e.code,
                        data={"results": e.results})
    except KaskadError as e:
        status = 400 if e.code == USAGE else 409 if e.code in (CONFLICT, LOCKED, UNSUPPORTED) else 500
        # ``data`` of the core reaches the answer: the refusal ⚠РФ carries the block (data.need = rf_consent, R4)
        raise HttpError(status, util.redact(str(e)), e.code, data=getattr(e, "data", None) or None)
    snapshot.bump()
    _log(results, box["new"])
    changes, affected = diff(box["old"], box["new"], pool, live)
    warnings = [w for r in results for w in r.get("warnings") or []] + [util.redact(w) for w in
                                                                         ((res or {}).get("warnings") or [])]
    util.log_tr("journal.scheme.applied", n=len(changes))
    return {"ok": True, "applied": True, "results": results, "changes": changes, "affected": affected,
            "warnings": warnings, "gateway_applied": res is not None, "graph": scheme_doc()}


# ── HTTP ───────────────────────────────────────────────────────────────────

@route("GET", r"/api/scheme")
def http_scheme(req):
    return json_response(scheme_doc())


@route("PUT", r"/api/scheme/layout")
def http_layout(req):
    return json_response(save_layout(req.json()))


@route("POST", r"/api/scheme/apply")
def http_apply(req):
    return json_response(apply(req.json(), client_ip=req.client_ip))


TRACE_MAX = 512                     # characters of «who» and «target»


def trace_doc(body):
    """«Куда пойдёт трафик?» of the scheme: the same answer as ``kaskadctl gw trace`` (kaskad.gw.trace), read from
    the caches of the scheme (state, both public pools — upstream.txt and upstream-ru.txt, the watchdog's snapshot)."""
    from ..gw import trace
    who, target, inbound = body.get("who"), body.get("target"), body.get("inbound")
    if who is not None and not isinstance(who, str) or not isinstance(target, str):
        raise HttpError(400, tr("panel.scheme.traceNeed"), USAGE)
    if inbound is not None and (not isinstance(inbound, str) or inbound not in model.INBOUND_KINDS):
        raise HttpError(400, tr("panel.scheme.traceInbound", kinds=", ".join(model.INBOUND_KINDS)), USAGE)
    if len(target) > TRACE_MAX or len(who or "") > TRACE_MAX:
        raise HttpError(400, tr("panel.scheme.traceTooLong"), USAGE)
    st = read_state()
    try:
        # both files, as ``exits.current`` of the CLI and ``ru_dec`` of the scheme (stand it1 Н-3): without
        # upstream-ru.txt the decision saw no live exit of a Russian group and said «с сервера напрямую»
        ctx = trace.context(st, pool=list(pool_exits()) + list(ru_pool_exits()), snap=exits_snapshot())
        return trace.trace(st, who, target, ctx=ctx, inbound=inbound)
    except KaskadError as e:
        raise HttpError(400 if getattr(e, "code", None) == USAGE else 500, str(e), getattr(e, "code", ERROR))


@route("POST", r"/api/gw/trace")
def http_trace(req):
    return json_response(trace_doc(req.json()))
