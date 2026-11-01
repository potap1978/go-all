"""Users, devices, accesses and routes of the gateway — plain functions on the dict state
(docs/k3/SPEC-wave2-gateway.md §2). Callers hold the state lock (``state.locked()``/``transaction()``)
and save; nothing here runs a command or touches secrets (passwords are made by the callers).

    st["gateway"] = {
      "enabled": bool,
      "enabled_at": "2026-10-07T12:00:00Z",   the last ``gw enable`` (the panel: «сборщик запускается», not «молчит»)
      "net": {"base": "10.77.0.0/16", "dns": "10.77.0.1"},
      "inbounds": {kind: {"enabled": bool, "port": int, "subnet": "...", ...adapter fields}},
      "users":   [{"id": "u-1a2b3c4d", "name", "route": null|{via, fail}, "enabled", "added"}],
      "devices": [{"id": "d-1a2b3c4d", "user", "name", "kind", "platform", "route", "enabled", "added",
                   "login", "ips": {pool: ip}, "access": [kind, ...]}],
      "routes":  {"default": {"via": "pool", "fail": "block"}},
      "exits":   {"e-3fa9c1d2b4": {"enabled": bool, "name": "Вена", "group", "manual", "manual_why", "speed_skip",
                                   "country", "quota"}},   (exit_pref, SPEC-stage1-exits §3.1)
      "exit_policy": {...},   the exit policy (groups, tiers, rules): kaskad.gw.policy; NOT filled by gw()/peek() —
                 «no policy in the file» is a state of its own (an updated node, §1.3)
      "notify": {"push": {...}, "flap_summary": bool},   the push switches (§8.1): notify(st) with the defaults
      "removed_inbounds": {kind: {"port": int, ...}}   the record of a removed inbound (without "enabled"): a
                 reinstall takes its port, parameters and options back — with the server keys, which stay in
                 secrets.json, the settings given to the devices work again; ``gw purge`` forgets it
    }

Route of a device: ``device.route`` -> ``user.route`` -> ``routes.default``. ``via``: ``pool``,
``group:<gid>`` (a foreign group of the exit policy; ``group:main`` is written as ``pool``), ``exit:<id>``,
``direct``, ``block``; a route to a group that is not there (or not foreign) is broken: the next level (stage 1,
SPEC-stage1-exits §3.1); ``check_route`` — the check with the state every writer of a route calls; ``fail`` (what if the exits of the route are dead): ``block``
(default, fail-closed) or ``direct``.

Rules «Россия напрямую» (wave 3, docs/k3/SPEC-wave3.md §2): ``rules`` of a device and of a user is
``inherit`` | ``all_exit`` | ``ru_direct`` (missing = inherit); the profile of a device is ``device.rules`` ->
``user.rules`` -> ``gateway.rules.default``. ``gateway.rules = {"default": "ru_direct"|"all_exit", "block_ads":
true, "block_torrent": false}`` — missing keys (states of 3.1) are these defaults. Whether the lists are there
(a ``ru_direct`` without lists behaves as ``all_exit``) is ``kaskad.rules.apply``'s business, not the model's.

``peek(st)`` reads without changing ``st`` (renderers run on copies and must not add keys);
``gw(st)`` fills the missing structure in place (writers).
"""

import copy
import random
import re

from .. import state, util
from ..errors import USAGE, KaskadError
from ..i18n import tr
from . import addr

INBOUND_KINDS = ("wg", "awg1", "awg2", "awg3", "l2tp", "pptp", "sstp", "ikev2", "openvpn", "ocserv", "socks5",
                 "vless", "hy2")
# Inbounds of the gateway xray (no address, no interface): xray routes their users by the device route and the rules
# (docs/k3/SPEC-vless-inbound.md §5); every other inbound goes through the kernel (gw_pre, gw_route, dnsmasq).
XRAY_KINDS = ("socks5", "vless", "hy2")
# Inbounds whose links apps take by the subscription of /p/ -> the schemes of those links (panel/userpage.py; the hint
# «Имя обновится … при следующем обновлении подписки» of gw/rename.py — REV-3371 З-2). SOCKS5 gives no link apps take.
SUB_KINDS = {"vless": ("vless://",), "hy2": ("hysteria2://",)}
# Inbounds of the WireGuard family in the same subscription: the .conf of the device as a line
# «amneziawg://<base64url .conf>#<name>» — the line of Incy (docs.incy.cc «subscription format»), as goAmnezia Pro all.txt;
# after the links of SUB_KINDS, within a device 3.1 → 2.0 → 1.5 → WireGuard (owner 24.10, panel/userpage.py).
SUB_CONF_KINDS = ("awg3", "awg2", "awg1", "wg")
SUB_CONF_SCHEME = "amneziawg://"
DEVICE_KINDS = ("phone", "tablet", "laptop", "pc", "tv", "router", "other")
PLATFORMS = ("ios", "android", "androidtv", "windows", "macos", "linux", "keenetic", "mikrotik", "openwrt",
             "tplink", "huawei", "xiaomi", "asus", "other")
VIA_SIMPLE = ("pool", "direct", "block")
GROUP_VIA = "group:"                 # route.via «group:<gid>» (stage 1, SPEC-groups §2.5)
FAILS = ("block", "direct")
DEFAULT_ROUTE = {"via": "pool", "fail": "block"}
RULE_PROFILES = ("inherit", "all_exit", "ru_direct")
DEFAULT_PROFILES = ("all_exit", "ru_direct")
RULES_DEFAULTS = {"default": "ru_direct", "block_ads": True, "block_torrent": False}

_EXIT_ID = re.compile(r"^e-[0-9a-f]{10}$")
_GROUP_ID = re.compile(r"^(main|g-[0-9a-f]{6})$")
_USER_ID = re.compile(r"^u-[0-9a-f]{8}$")
_DEV_ID = re.compile(r"^d-[0-9a-f]{8}$")
_LOGIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_rng = random.SystemRandom()

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",   # i18n: skip — transliteration of names, not words
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
                      "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def _err(msg):
    return KaskadError(msg, code=USAGE)


class NotFound(KaskadError):
    """No such person or device (``get_user``, ``get_device``): callers tell it by the class (the panel answers 404),
    never by the words of the message."""


# ── structure ──────────────────────────────────────────────────────────────

def _defaults():
    return {"enabled": False, "net": {}, "inbounds": {}, "users": [], "devices": [],
            "routes": {"default": dict(DEFAULT_ROUTE)}, "exits": {}}


def gw(st):
    """The gateway part of ``st`` with every key present (fills in place)."""
    g = st.get("gateway")
    if not isinstance(g, dict):
        g = {}
        st["gateway"] = g
    for k, v in _defaults().items():
        if not isinstance(g.get(k), type(v)):
            g[k] = v
    if not isinstance(g["routes"].get("default"), dict):
        g["routes"]["default"] = dict(DEFAULT_ROUTE)
    return g


def peek(st):
    """The gateway part with defaults, WITHOUT changing ``st`` (a shallow merged copy)."""
    g = (st or {}).get("gateway")
    out = _defaults()
    if isinstance(g, dict):
        for k, v in g.items():
            if k not in out or isinstance(v, type(out[k])):
                out[k] = v
    if not isinstance(out["routes"].get("default"), dict):
        out["routes"] = dict(out["routes"], default=dict(DEFAULT_ROUTE))
    return out


def enabled(st):
    return bool(peek(st)["enabled"])


def activity_keep(st):
    """``gateway.activity.keep`` (SPEC-activity ACT-8): the history of activity of devices is kept — on by default
    (decision 2 of the coordinator 20.10); only an explicit ``false`` turns it off."""
    a = peek(st).get("activity")
    return not (isinstance(a, dict) and a.get("keep") is False)


def public_host(st):
    """The address clients connect to — ``st["gateway"]["host"]``, a public IPv4 or a DNS name, detected
    at ``gw enable`` or set with ``gw host`` (``host_auto`` false). Adapters put it into issued configs
    (Endpoint, server, the SAN of certificates). "" if unknown."""
    return str(peek(st).get("host") or "")


def clean_name(value, what="name"):
    """A NEW name of a person or a device (stage 1, T13: ``names.check`` — 1..40 characters, no control or invisible
    ones, a refusal, never a silent replacement; SPEC-stage1-rename §2). ``what``: "name" | "user" | "device" — whose
    name the refusal speaks of. Old names (longer, made before stage 1) stay as they are: only a new value is checked."""
    from .. import names
    return names.check(value, what if what in ("user", "device") else "name")


# ── routes ─────────────────────────────────────────────────────────────────

def parse_route(via, fail=None):
    """Validated route object {"via", "fail"}. ``via`` is pool | direct | block | exit:<id> | group:<gid>;
    ``group:main`` is written as ``pool`` (3.3.3 understands it after a rollback, SPEC §3.1). The form only — whether
    the group is there and foreign, ``check_route``."""
    v = str(via or "").strip()
    if v.startswith(GROUP_VIA) and _GROUP_ID.match(v[len(GROUP_VIA):]):
        v = "pool" if v == GROUP_VIA + "main" else v
    elif v not in VIA_SIMPLE and not (v.startswith("exit:") and _EXIT_ID.match(v[5:])):
        raise _err(tr("gw_model.err.bad_route", value=repr(via)))
    f = str(fail or "block").strip()
    if f not in FAILS:
        raise _err(tr("gw_model.err.bad_fail", value=repr(fail)))
    return {"via": v, "fail": f}


def _route_obj(obj):
    if obj is None:
        return None
    if isinstance(obj, str):
        return parse_route(obj)
    if isinstance(obj, dict) and obj.get("via"):
        return parse_route(obj.get("via"), obj.get("fail"))
    return None


def group_of_route(route):
    """The group a route goes to: ``pool`` -> "main", ``group:<gid>`` -> gid; else None (``exit:``, direct, block)."""
    v = (route or {}).get("via") or ""
    if v == "pool":
        return "main"
    return v[len(GROUP_VIA):] if v.startswith(GROUP_VIA) else None


def _foreign_group(st, gid):
    """The group ``gid`` is in the stored exit policy with role abroad («main» always is)."""
    if gid == "main":
        return True
    pol = peek(st).get("exit_policy")
    pol = pol if isinstance(pol, dict) else {}
    for grp in pol.get("groups") or []:
        if isinstance(grp, dict) and grp.get("id") == gid:
            return grp.get("role", "abroad") == "abroad"
    return False


def route_of(st, device):
    """The effective route of a device: {"via", "fail", "from": "device"|"user"|"default"}. A route to a group that
    is not in the policy (or not foreign) is broken, as a broken route of 3.3.3: the next level (SPEC §3.1)."""
    g = peek(st)
    user = find_user(st, device.get("user")) or {}
    for src, obj in (("device", device.get("route")), ("user", user.get("route")),
                     ("default", g["routes"].get("default"))):
        try:
            r = _route_obj(obj)
        except KaskadError:
            r = None                       # a broken route in state: fall through to the next level
        if r and (r["via"] or "").startswith(GROUP_VIA) and not _foreign_group(st, r["via"][len(GROUP_VIA):]):
            r = None                       # the group was deleted (an import, a hand edit) or is Russian
        if r:
            r["from"] = src
            return r
    return dict(DEFAULT_ROUTE, **{"from": "default"})


def check_route(st, route, pool=None):
    """The route ``route`` ({"via", "fail"} or a via text) checked with the state, for EVERY writer of a route (panel,
    scheme, bot, CLI, MCP, SSH): ``group:<gid>`` — a group of the policy with role abroad
    (``gw_model.err.route_ru_group``, ``gw_model.err.route_no_group``); ``exit:<id>`` — not an exit of a Russian group
    or of ``upstream-ru.txt`` (``gw_model.err.route_ru_exit``). ``pool`` — the roles of the exits (rows or
    {"abroad": ids, "ru": ids}); None — read from the files of the pool. -> the parsed route."""
    from . import policy
    r = parse_route(route.get("via"), route.get("fail")) if isinstance(route, dict) else parse_route(route)
    gid = group_of_route(r)
    if gid and gid != "main":
        doc = policy.stored(st) or {}
        grp = policy.group(doc, gid)
        if grp is None:
            raise _err(tr("gw_model.err.route_no_group", group=gid))
        if grp.get("role", "abroad") != "abroad":
            raise _err(tr("gw_model.err.route_ru_group", group=policy.label(grp)))
    eid = exit_of(r)
    if eid:
        grp = policy.group_of(policy.stored(st) or {}, eid)
        role = grp.get("role", "abroad") if grp else None
        if role is None:
            if pool is None:
                from . import rev
                pool = rev.pool_ids()
            role = (policy.pool_roles(pool) or {}).get(eid)
        if role == "ru":
            raise _err(tr("gw_model.err.route_ru_exit", exit=exit_pref(st, eid)["name"] or eid))
    return r


def _checked(st, route, pool=None):
    """``_route_obj`` of a route being WRITTEN, checked with the state (``check_route``): a group that is not there
    (or is Russian), a Russian exit — refused at the write, as 3.3.5 refused an unknown «via» (SPEC §3.1)."""
    r = _route_obj(route)
    return check_route(st, r, pool) if r else r


def set_default_route(st, via, fail=None, pool=None):
    gw(st)["routes"]["default"] = check_route(st, {"via": via, "fail": fail}, pool)
    return st["gateway"]["routes"]["default"]


def kept_fail(st, what, obj=None):
    """The fail policy a new target keeps when none is given: the object's own route's, else the one it inherits
    (a device — its person's, then the default's; a person — the default's; the default — its own). A new target never
    flips «напрямую» to «блок» (or back) silently (stand T-scheme-3.2.15, Н1)."""
    if what == "default":
        r = _route_obj(peek(st)["routes"].get("default"))
        return (r or DEFAULT_ROUTE)["fail"]
    try:
        own = _route_obj((obj or {}).get("route"))
    except KaskadError:
        own = None
    if own:
        return own["fail"]
    if what == "device":
        return route_of(st, obj)["fail"]
    return kept_fail(st, "default")


def exit_of(route):
    """Exit id of a route ``exit:<id>``, else None."""
    v = (route or {}).get("via") or ""
    return v[5:] if v.startswith("exit:") else None


# ── rules profiles (wave 3) ────────────────────────────────────────────────

def parse_profile(value, allow_inherit=True):
    """``inherit`` | ``all_exit`` | ``ru_direct`` (the default of the node: without ``inherit``)."""
    v = str(value or "").strip()
    allowed = RULE_PROFILES if allow_inherit else DEFAULT_PROFILES
    if v not in allowed:
        raise _err(tr("gw_model.err.bad_profile", allowed=", ".join(allowed), value=repr(value)))
    return v


def rules_conf(st):
    """``gateway.rules`` with the defaults (a state of 3.1 has none): {"default", "block_ads", "block_torrent"}.
    Never changes ``st``. The default of the node without a stored one goes by the generation of the state
    (SPEC-stage1-rf-mark-builtin §3.6, D-019): a new node (``gen 2``) — ``all_exit`` («Россия напрямую» only with the
    tick ⚠РФ), an updated one — RULES_DEFAULTS as in 3.3.x. Computed here; on a new node ``pin_default`` also writes it
    (for a manual rollback to 3.3.5) — the value is the same."""
    raw = peek(st).get("rules")
    out = dict(RULES_DEFAULTS)
    if state.born_new(st):
        out["default"] = "all_exit"
    if isinstance(raw, dict):
        if raw.get("default") in DEFAULT_PROFILES:
            out["default"] = raw["default"]
        for k in ("block_ads", "block_torrent"):
            if isinstance(raw.get(k), bool):
                out[k] = raw[k]
    return out


def pin_default(st):
    """A NEW node (``state.born_new``) without a stored default of the node: write ``gateway.rules.default`` =
    ``all_exit`` (SEC-s1-r2 П3) — 3.3.5 put back by hand reads a missing key as RULES_DEFAULTS («Россия напрямую» to
    everyone); the code of stage 1 renders the same either way. ``gw enable`` and every change of the gateway
    (``ops._change``). Nothing on an updated node. -> True when it wrote."""
    if not state.born_new(st):
        return False
    raw = peek(st).get("rules")
    if isinstance(raw, dict) and raw.get("default") in DEFAULT_PROFILES:
        return False
    g = gw(st)
    g["rules"] = dict(raw if isinstance(raw, dict) else {}, default="all_exit")
    return True


def set_rules_conf(st, default=None, block_ads=None, block_torrent=None):
    """Change the profile of the node and the switches; returns the whole ``gateway.rules``."""
    cur = rules_conf(st)
    if default is not None:
        cur["default"] = parse_profile(default, allow_inherit=False)
    if block_ads is not None:
        cur["block_ads"] = bool(block_ads)
    if block_torrent is not None:
        cur["block_torrent"] = bool(block_torrent)
    gw(st)["rules"] = cur
    return dict(cur)


def set_profile(obj, value):
    """The profile of a user or a device (``inherit`` — from the next level)."""
    obj["rules"] = parse_profile(value)
    return obj["rules"]


def _profile_value(v):
    return v if v in DEFAULT_PROFILES else None


def profile_of(st, device):
    """The profile a device asks for: {"profile": "all_exit"|"ru_direct", "from": "device"|"user"|"default"}
    (``device.rules`` -> ``user.rules`` -> ``gateway.rules.default``; a broken value falls through)."""
    user = find_user(st, device.get("user")) or {}
    for src, v in (("device", device.get("rules")), ("user", user.get("rules"))):
        p = _profile_value(v)
        if p:
            return {"profile": p, "from": src}
    return {"profile": rules_conf(st)["default"], "from": "default"}


# ── the Russian group of «Россия напрямую» (stage 1, T15 Q1; SPEC-groups §2.5) ──
#
# ``ru_via`` of a person and of a device — next to ``route`` and ``rules`` in their object (never in
# ``gateway.rules``): ``"direct"`` | ``"<gid>"`` of a group with role ``ru``; no field — inherit (device -> person ->
# the node, which in stage 1 is always ``direct``: no field of the node). It ACTS only with «Россия напрямую»
# (``rules.apply.device_rd``: the profile, the lists, the consent ⚠РФ, a route not «block» by choice) — that is the
# readers' business (Q2: ``exits.ru_target``); here it is only stored and resolved. It never asks or gives the
# consent ⚠РФ (``rfgate.selected`` looks at the profiles only). A reference to a group that is not there (or is not
# Russian) is ``direct`` — the safe side, «как сейчас» — never the next level.

RU_VIA_DIRECT = "direct"
RU_VIA_INHERIT = "inherit"


def _ru_group(st, gid):
    """The group ``gid`` of the stored exit policy if its role is ``ru``, else None."""
    from . import policy
    grp = policy.group(policy.stored(st) or {}, gid)
    return grp if grp is not None and grp.get("role", "abroad") == "ru" else None


def parse_ru_via(st, value):
    """``inherit`` | ``direct`` | the id of a group with role ``ru`` of the stored policy (checked with ``st``);
    None and "" are ``inherit`` (as ``set_route``). A refusal (USAGE): another form ``gw_model.err.ru_via_bad``, no such
    group ``gw_model.err.ru_via_no_group``, a foreign group (``main`` too) ``gw_model.err.ru_via_abroad``."""
    v = "" if value is None else str(value).strip()
    if v in ("", RU_VIA_INHERIT):
        return RU_VIA_INHERIT
    if v == RU_VIA_DIRECT:
        return v
    if not _GROUP_ID.match(v):
        raise _err(tr("gw_model.err.ru_via_bad", value=repr(value)))
    from . import policy
    grp = policy.group(policy.stored(st) or {}, v)
    if grp is None and v != "main":
        raise _err(tr("gw_model.err.ru_via_no_group", group=v))
    if grp is None or grp.get("role", "abroad") != "ru":
        raise _err(tr("gw_model.err.ru_via_abroad", group=policy.label(grp or {"id": v})))
    return v


def set_ru_via(st, obj, value):
    """``ru_via`` of a person or a device ``obj`` (``parse_ru_via``): ``inherit`` removes the field. -> the value
    written (``inherit`` when removed)."""
    v = parse_ru_via(st, value)
    if v == RU_VIA_INHERIT:
        obj.pop("ru_via", None)
    else:
        obj["ru_via"] = v
    return v


def ru_via_of(st, device):
    """The Russian group a device asks for: {"via": "direct"|"<gid>", "from": "device"|"user"|"default"}
    (``device.ru_via`` -> ``user.ru_via`` -> ``direct``). A stored value that is neither ``inherit`` nor ``direct`` nor
    a Russian group of the policy (deleted at an import, a hand edit, a foreign group) is ``direct`` from its level with
    ``"broken": <the value>`` — the panel warns; the next level is NOT asked (a broken choice never sends Russia
    through somebody's router). Whether it acts — ``rules.apply.device_rd`` (the caller's)."""
    user = find_user(st, device.get("user")) or {}
    for src, v in (("device", device.get("ru_via")), ("user", user.get("ru_via"))):
        if v is None or v in ("", RU_VIA_INHERIT):          # "" stored by hand — inherit, as parse_ru_via reads it
            continue
        if v == RU_VIA_DIRECT:
            return {"via": RU_VIA_DIRECT, "from": src}
        if isinstance(v, str) and _GROUP_ID.match(v) and _ru_group(st, v) is not None:
            return {"via": v, "from": src}
        return {"via": RU_VIA_DIRECT, "from": src, "broken": v}
    return {"via": RU_VIA_DIRECT, "from": "default"}


def ru_via_refs(st, gid):
    """Who chose the Russian group ``gid`` by its own ``ru_via``: {"users": [ids], "devices": [ids]}; a group that is
    not Russian — nobody (a foreign group is never a value of ``ru_via``)."""
    out = {"users": [], "devices": []}
    if _ru_group(st, gid) is None:
        return out
    g = peek(st)
    for kind in ("users", "devices"):
        for o in g[kind]:
            if isinstance(o, dict) and o.get("ru_via") == gid:
                out[kind].append(o.get("id"))
    return out


def reassign_ru_via(st, gid, to):
    """«Удалить группу» (§8.5): every own ``ru_via`` = ``gid`` becomes ``to`` — ``direct`` or another Russian group
    (``parse_ru_via``; ``inherit`` and ``gid`` itself are refused: the choice is explicit). Call it BEFORE the group
    leaves the policy. -> {"users": [ids], "devices": [ids]} changed."""
    refs = ru_via_refs(st, gid)
    v = parse_ru_via(st, to)
    if v in (RU_VIA_INHERIT, gid):
        raise _err(tr("gw_model.err.ru_via_bad", value=repr(to)))
    g = gw(st)
    for kind in ("users", "devices"):
        for o in g[kind]:
            if isinstance(o, dict) and o.get("id") in refs[kind] and o.get("ru_via") == gid:
                o["ru_via"] = v
    return refs


# ── users ──────────────────────────────────────────────────────────────────

def _new_id(prefix, taken):
    while True:
        i = "%s-%08x" % (prefix, _rng.randrange(1 << 32))
        if i not in taken:
            return i


def users(st):
    return list(peek(st)["users"])


def find_user(st, ref):
    """By id, else by name (exact, then case-insensitive); None if absent or ambiguous."""
    if ref is None:
        return None
    ref = str(ref)
    us = peek(st)["users"]
    for u in us:
        if u.get("id") == ref:
            return u
    for match in (lambda u: u.get("name") == ref, lambda u: str(u.get("name", "")).casefold() == ref.casefold()):
        found = [u for u in us if match(u)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            return None
    return None


def get_user(st, ref):
    u = find_user(st, ref)
    if u is None:
        raise NotFound(tr("gw_model.err.no_user", ref=ref))
    return u


def add_user(st, name, route=None):
    g = gw(st)
    name = clean_name(name, "user")
    from .. import names
    if names.duplicates([(u.get("id"), u.get("name")) for u in g["users"]], name):
        raise _err(tr("gw_model.err.user_exists", name=name))      # «Вена» = «вена» (names.same, §2)
    u = {"id": _new_id("u", set(x.get("id") for x in g["users"])), "name": name,
         "route": _checked(st, route), "enabled": True, "added": util.now_iso()}
    g["users"].append(u)
    return u


def del_user(st, ref, cascade=False):
    """Remove a user; with ``cascade`` also the devices. Returns (user, [removed devices])."""
    g = gw(st)
    u = get_user(st, ref)
    devs = [d for d in g["devices"] if d.get("user") == u["id"]]
    if devs and not cascade:
        raise KaskadError(tr("gw_model.err.user_has_devices", name=u["name"], count=len(devs)), code=USAGE)
    g["devices"] = [d for d in g["devices"] if d.get("user") != u["id"]]
    g["users"] = [x for x in g["users"] if x is not u]
    return u, devs


def set_route(obj, via, fail=None, st=None, pool=None):
    """Route of a user or device; ``via`` None/"inherit" clears it (inherit from the next level). ``st`` — the state
    the route is checked with (``check_route``: a group that is not there, a Russian group or exit — refused); every
    writer of the product passes it (``tests/gw/test_model_s1.py`` holds them to it); None — the form only."""
    if via in (None, "", "inherit"):
        obj["route"] = None
    elif st is not None:
        obj["route"] = check_route(st, {"via": via, "fail": fail}, pool)
    else:
        obj["route"] = parse_route(via, fail)
    return obj["route"]


# ── devices ────────────────────────────────────────────────────────────────

def devices(st, user=None):
    out = list(peek(st)["devices"])
    if user is not None:
        out = [d for d in out if d.get("user") == user]
    return out


def find_device(st, ref):
    """By id, then login, then name (exact, case-insensitive); None if absent or ambiguous."""
    if ref is None:
        return None
    ref = str(ref)
    ds = peek(st)["devices"]
    for key in ("id", "login"):
        for d in ds:
            if d.get(key) == ref:
                return d
    for match in (lambda d: d.get("name") == ref, lambda d: str(d.get("name", "")).casefold() == ref.casefold()):
        found = [d for d in ds if match(d)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            return None
    return None


def get_device(st, ref):
    d = find_device(st, ref)
    if d is None:
        raise NotFound(tr("gw_model.err.no_device", ref=ref))
    return d


def find_device_by_login(st, login):
    for d in peek(st)["devices"]:
        if d.get("login") == login:
            return d
    return None


def find_device_by_ip(st, ip):
    for d in peek(st)["devices"]:
        if ip in (d.get("ips") or {}).values():
            return d
    return None


def _slug(name):
    out = []
    for ch in str(name).lower():
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
        elif ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        else:
            out.append("-")
    s = re.sub(r"-+", "-", "".join(out)).strip("-")
    return s[:12].strip("-") or "dev"


def new_login(st, name):
    """``<slug of the name>-<4 hex>`` (latin letters, digits, dashes): unique among devices."""
    taken = set(d.get("login") for d in peek(st)["devices"])
    base = _slug(name)
    while True:
        login = "%s-%04x" % (base, _rng.randrange(1 << 16))
        if login not in taken:
            return login


def _check_kind(kind):
    if kind not in INBOUND_KINDS:
        raise _err(tr("gw_model.err.unknown_inbound", kind=repr(kind), kinds=", ".join(INBOUND_KINDS)))
    return kind


def add_device(st, user_ref, name, kind="other", platform="other", access=(), route=None):
    """New device of a user; ``access`` kinds are granted at once (addresses allocated)."""
    g = gw(st)
    u = get_user(st, user_ref)
    name = clean_name(name, "device")
    if kind not in DEVICE_KINDS:
        raise _err(tr("gw_model.err.bad_device_kind", kinds=", ".join(DEVICE_KINDS)))
    if platform not in PLATFORMS:
        raise _err(tr("gw_model.err.bad_platform", platforms=", ".join(PLATFORMS)))
    for k in access:
        _check_kind(k)
    d = {"id": _new_id("d", set(x.get("id") for x in g["devices"])), "user": u["id"], "name": name,
         "kind": kind, "platform": platform, "route": _checked(st, route), "enabled": True,
         "added": util.now_iso(), "login": new_login(st, name), "ips": {}, "access": []}
    g["devices"].append(d)
    for k in access:
        grant(st, d, k)
    return d


def del_device(st, ref):
    g = gw(st)
    d = get_device(st, ref)
    g["devices"] = [x for x in g["devices"] if x is not d]
    return d


def grant(st, device, kind):
    """Give ``device`` the inbound ``kind`` (idempotent); allocates its addresses. Returns {pool: ip}."""
    _check_kind(kind)
    acc = device.setdefault("access", [])
    if kind not in acc:
        acc.append(kind)
    return addr.allocate_kind(st, device, kind)


def revoke(st, device, kind):
    """Take the inbound away; frees addresses no other inbound of the device uses. Returns them."""
    _check_kind(kind)
    acc = device.get("access") or []
    if kind not in acc:
        return []
    gone = addr.release(st, device, kind)
    device["access"] = [k for k in acc if k != kind]
    return gone


def device_active(st, device):
    """Enabled device of an enabled user."""
    if not device.get("enabled", True):
        return False
    u = find_user(st, device.get("user"))
    return u is not None and u.get("enabled", True)


def devices_for(st, kind):
    """Active devices with ``kind`` in their access — the peers/accounts of an inbound."""
    return [d for d in peek(st)["devices"] if kind in (d.get("access") or []) and device_active(st, d)]


def device_ips(device):
    """Static addresses of a device (every pool), sorted."""
    return sorted(set(ip for ip in (device.get("ips") or {}).values() if ip))


def live_ips(st, device, kinds=None):
    """Static addresses of a device that may carry traffic now: the pools of the ENABLED inbounds in its
    access (``kinds`` — enabled_kinds(st), when the caller has it). An address of a disabled or removed
    inbound stays in the device (it comes back with the inbound) but not in gw_route: gw_pre drops it."""
    kinds = enabled_kinds(st) if kinds is None else kinds
    pools = set()
    for k in device.get("access") or []:
        if k in kinds:
            pools.update(addr.pools_of(k))
    return sorted(set(ip for p, ip in (device.get("ips") or {}).items() if ip and p in pools))


# ── inbounds ───────────────────────────────────────────────────────────────

def inbound(st, kind):
    rec = peek(st)["inbounds"].get(kind)
    return rec if isinstance(rec, dict) else None


def set_inbound(st, kind, rec):
    _check_kind(kind)
    gw(st)["inbounds"][kind] = rec
    return rec


def remove_inbound(st, kind):
    return gw(st)["inbounds"].pop(kind, None)


KEPT = "removed_inbounds"


def kept_inbound(st, kind):
    """The record ``keep_inbound`` kept for a removed inbound, None if there is none."""
    kept = peek(st).get(KEPT)
    rec = kept.get(kind) if isinstance(kept, dict) else None
    return rec if isinstance(rec, dict) else None


def keep_inbound(st, kind, rec):
    """Remember the record of a removed inbound (its port, parameters, options; not ``enabled``)."""
    _check_kind(kind)
    g = gw(st)
    if not isinstance(g.get(KEPT), dict):
        g[KEPT] = {}
    g[KEPT][kind] = dict((k, v) for k, v in (rec or {}).items() if k != "enabled")
    return g[KEPT][kind]


def forget_kept(st, kind=None):
    """Drop the kept record of ``kind`` (a reinstall took it), or every kept record (``kind`` None)."""
    g = gw(st)
    kept = g.get(KEPT)
    if kind is None or not isinstance(kept, dict):
        g.pop(KEPT, None)
        return
    kept.pop(kind, None)
    if not kept:
        g.pop(KEPT, None)


def installed_kinds(st):
    ib = peek(st)["inbounds"]
    return [k for k in INBOUND_KINDS if isinstance(ib.get(k), dict)]


def enabled_kinds(st):
    ib = peek(st)["inbounds"]
    return [k for k in installed_kinds(st) if ib[k].get("enabled", True)]


# ── exits (preferences of the gateway; the pool itself is upstream.txt) ────

EXIT_PREF = {"enabled": True, "name": "", "group": None, "manual": None, "manual_why": None, "speed_skip": False,
             "country": None, "quota": None}
_KEEP = object()


def exit_pref(st, eid):
    """The record of an exit with every field (SPEC §3.1): enabled, name, group (the hint for a new exit), manual
    (None | standby | maintenance), manual_why (None | sub_confirm), speed_skip, country ({expected, accepted_at,
    notified} | None), quota (| None)."""
    p = peek(st)["exits"].get(eid)
    p = p if isinstance(p, dict) else {}
    out = copy.deepcopy(EXIT_PREF)
    out["enabled"] = bool(p.get("enabled", True))
    out["name"] = str(p.get("name") or "")
    for k in ("group", "manual", "manual_why", "country", "quota"):
        if p.get(k) not in (None, "", {}):
            out[k] = copy.deepcopy(p[k])
    out["speed_skip"] = bool(p.get("speed_skip"))
    return out


def prune_exit_groups(st, gids, drop=()):
    """T-q Н-state: the hint ``group`` of the records of exits (``gateway.exits``) that points at a group no longer
    there (``gids`` — the ids of the groups now), or of an exit in ``drop`` (its line deleted: the hint was for a new
    exit); a record left empty goes. In place. -> True when changed."""
    recs = gw(st)["exits"]
    gids, drop = set(gids or ()), set(drop or ())
    changed = False
    for eid in list(recs):
        r = recs[eid]
        if not isinstance(r, dict) or r.get("group") is None:
            continue
        if eid in drop or r["group"] not in gids:
            r.pop("group")
            changed = True
            if not r:
                del recs[eid]
    return changed


def set_exit(st, eid, enabled=None, name=None, manual=_KEEP, manual_why=_KEEP, speed_skip=None, country=_KEEP,
             quota=_KEEP, group=_KEEP):
    """Change the record of an exit: ``None`` of enabled/name/speed_skip and an omitted manual/manual_why/country/
    quota/group — unchanged; manual=None — «Вернуть» (manual_why goes with it); quota=None — the quota removed. The
    fields are checked by ``policy.validate_exit`` (KaskadError USAGE, ``data["errors"]``). -> the record."""
    from . import policy
    if not _EXIT_ID.match(str(eid)):
        raise _err(tr("gw_model.err.bad_exit_id", value=repr(eid)))
    p = gw(st)["exits"].get(eid)
    p = p if isinstance(p, dict) else {}
    new = copy.deepcopy(p)
    if enabled is not None:
        new["enabled"] = bool(enabled)
    if name is not None:
        if state.clean_name(name):
            new["name"] = state.clean_name(name)
        else:
            new.pop("name", None)            # «» — the name by the key again (T13: the reset)
    if speed_skip is not None:
        new["speed_skip"] = speed_skip
    for key, val in (("manual", manual), ("manual_why", manual_why), ("country", country), ("quota", quota),
                     ("group", group)):
        if val is not _KEEP:
            new[key] = copy.deepcopy(val)
    if manual is not _KEEP and manual is None and manual_why is _KEEP:
        new["manual_why"] = None           # «Вернуть» clears the reason with the state
    if isinstance(new.get("quota"), dict):
        new["quota"] = dict({"period": "month", "reset_day": 1, "warn_pct": 80, "action": "remove"}, **new["quota"])
    errs = policy.validate_exit(new)
    if errs:
        raise KaskadError("\n".join(e["text"] for e in errs), code=USAGE, data={"errors": errs})
    for key in ("manual", "manual_why", "country", "quota", "group"):
        if new.get(key) is None:
            new.pop(key, None)
    if new.get("speed_skip") is False:
        new.pop("speed_skip")
    p.clear()
    p.update(new)
    gw(st)["exits"][eid] = p                 # a new id is written only once its fields passed the check
    return p


# ── defaults of the exit policy and of the pushes (stage 1) ────────────────

NOTIFY_PUSH = {"exit_dead": True, "exit_alive": True, "exit_degraded": True, "exit_recovered": True,
               "exit_standby": True, "exit_removed": True, "exit_rule_push": True, "exit_failover": True,
               "exit_quota": True, "exit_country_changed": True, "node_offline": True, "group_last_step": True,
               "group_restored": True, "speedtest_budget": False, "coalesce": True,
               "node_ru_offline": True}     # T15 Q5 (SPEC-groups §10): the summary of an outage of the Russian addresses
# the health of the node (SPEC-node-score §3.5, A6): a switch per kind of ``hostmon.HOST_KINDS`` (a test compares
# them), on by default; what a switch on sends is decided by ``hostmon`` (``args.push``: the alarm or ``host_level``)
NOTIFY_PUSH.update((k, True) for k in ("host_cpu", "host_steal", "host_io", "host_mem", "host_disk", "host_net",
                                       "host_conntrack", "host_clock", "host_cert", "host_loss"))
NOTIFY_PUSH["bench_done"] = True        # B7: a check of the server ended (its total) — a switch of its own, on
NOTIFY_PUSH["bench_skipped_day"] = True     # the check of the server: skips of a day (SS §2.5 п. 7, B11)


def notify_defaults(st):
    """``gateway.notify`` of a node that has none (SPEC §8.1): every push on as the table, ``flap_summary`` by the
    generation of the state (§1.3) — an updated node false (as 3.3.5), a new one true; not a part of a preset."""
    return {"push": dict(NOTIFY_PUSH), "flap_summary": state.born_new(st)}


def notify(st):
    """The effective ``gateway.notify`` (a copy): the stored values over ``notify_defaults``; a write of the policy
    never changes it (the switches live outside ``exit_policy``)."""
    out = notify_defaults(st)
    n = peek(st).get("notify")
    if isinstance(n, dict):
        if isinstance(n.get("push"), dict):
            out["push"].update(dict((k, bool(v)) for k, v in n["push"].items()))
        if isinstance(n.get("flap_summary"), bool):
            out["flap_summary"] = n["flap_summary"]
        for k, v in n.items():
            if k not in out:
                out[k] = copy.deepcopy(v)
    return out


# ── the light health of the node (SPEC-node-score §3.7; A5) ────────────────

_HM_TOLD = [None]           # the last set of anchors whose bad ones were logged (a line per set, not per round)


def own_addrs(st):
    """The node's own addresses known to the state (``gateway.host`` when it is an address): an anchor may not be one.
    The collector adds the addresses of the interfaces (``hostprobe.own_addrs``) when the probes are on."""
    h = str(peek(st).get("host") or "").strip()
    return [h] if h and (h.count(".") == 3 or ":" in h) else []


def _anchors_sig(values):
    """A cheap mark of the set of anchors (one journal line per set): never the repr of a huge list or string."""
    from . import hostping
    if isinstance(values, (list, tuple)):
        return (len(values),) + tuple(v[:64] if isinstance(v, str) else type(v).__name__
                                      for v in values[:hostping.SCAN_MAX])
    return (type(values).__name__, values[:64] if isinstance(values, str) else None)


def dev_rule(st):
    """``gateway.hostmon.dev`` (NS-D25: {"pct": 5…30, "use_spread": bool}) checked by ``score.valid_dev_rule``; none
    or broken — the default rule (the score is never refused for a bad setting)."""
    from .. import score
    hm = peek(st).get("hostmon")
    d = hm.get("dev") if isinstance(hm, dict) else None
    try:
        return score.valid_dev_rule(d) if d is not None else dict(score.DEV_RULE)
    except ValueError:
        return dict(score.DEV_RULE)


def hostmon_flags(st):
    """-> (enabled, probes) of ``gateway.hostmon`` (the rules of ``hostmon``; no anchors parsed)."""
    g = peek(st).get("hostmon")
    g = g if isinstance(g, dict) else {}
    return g.get("enabled", True) is not False, g.get("probes") is True


def hostmon(st, own=None):
    """``gateway.hostmon`` with its defaults (§3.7, §10.1 п. 20, 22), checked at every read: ``enabled`` — true unless
    false; ``probes`` — true ONLY when the key says true (no key — off, whatever ``state.born_new``: a new install
    writes it, A8); ``probes_key`` — the key is there (an updated node without it gets the hint once, A5 п. 10);
    ``anchors`` — ``hostping.working_anchors`` (the one list of the probes and of the rule; checked, ≤ 5, empty — the
    defaults): the bad ones (a ``.kbk``, a hand edit of state.json) dropped with one line in the journal per set."""
    from . import hostping
    g = peek(st).get("hostmon")
    g = g if isinstance(g, dict) else {}
    values = g.get("anchors")
    anchors, bad = hostping.working_anchors(values, own_addrs(st) if own is None else own)
    if bad:
        sig = _anchors_sig(values)
        if _HM_TOLD[0] != sig:
            _HM_TOLD[0] = sig
            try:
                util.log_tr("gw_host.log.anchors_bad", n=bad, addrs=", ".join(anchors))
            except Exception:                        # noqa: BLE001 — the journal never stops the round
                pass
    on, probes = hostmon_flags(st)
    return {"enabled": on, "probes": probes, "probes_key": isinstance(g.get("probes"), bool), "anchors": anchors,
            "port_mbit": _tariff_port(g)}


def _tariff_port(g):
    """The port of the tariff for the rule «port» (C7, NS-D6): typed in or from a catalog, else None."""
    from ..bench import tariff as btariff
    try:
        return btariff.port_mbit(btariff.load(g.get("tariff"))[0])
    except Exception:                                # noqa: BLE001 — a broken tariff never stops the round
        return None


def tariff(st):
    """``gateway.hostmon.tariff`` checked at every read (C1, ``bench.tariff.load``) -> (doc, dropped)."""
    from ..bench import tariff as btariff
    g = peek(st).get("hostmon")
    return btariff.load(g.get("tariff") if isinstance(g, dict) else None)


_BENCH_HERE = object()


def bench(st, here=_BENCH_HERE):
    """``gateway.hostmon.bench`` with its defaults, checked at every read (SPEC-node-score-schedule-share §2.1): PURE —
    writes nothing, takes no lock (it is called inside the transactions of the PUT of the schedule, ``apply`` and
    the restore of a .kbk). ``schedule`` — after ``bench.schedule.validate`` (a bad entry ``on: false`` with
    ``off_why``, past 12 dropped); ``fix`` — what ``bench.fix_state``/``fix_in`` must save; the marks and ``armed``
    as THIS machine sees them (``bench.machine.status``; ``here`` — its ``mid``, read from /etc/machine-id when not
    given)."""
    from ..bench import machine, schedule
    hm = peek(st).get("hostmon")
    hm = hm if isinstance(hm, dict) else {}
    b = hm.get("bench")
    b = b if isinstance(b, dict) else {}
    if here is _BENCH_HERE:
        here = machine.mid()
    norm, fix = schedule.check_saved(b.get("schedule"))
    out = {"schedule": norm, "fix": fix, "retry": b.get("retry") is not False,
           "mid": b.get("mid") if isinstance(b.get("mid"), str) else None,
           "tz_seen": b.get("tz_seen") if isinstance(b.get("tz_seen"), str) else None,
           "warn": [] if here else ["no_machine_id"]}
    out.update(machine.status(b, here))
    return out


def exit_policy(st, pool=None, subs=None):
    """The effective exit policy (``policy.load``): none stored — «main» in «compat»."""
    from . import policy
    return policy.load(st, pool, subs)


def snapshot(st):
    """Deep copy of the gateway part (for "apply a copy first, save after")."""
    return copy.deepcopy(peek(st))


def validate(st):
    """Problems of the gateway part (list of strings) — ids, references, addresses."""
    g = peek(st)
    probs = []
    uids = set()
    for u in g["users"]:
        if not _USER_ID.match(str(u.get("id"))):
            probs.append(tr("gw_model.check.user_bad_id", id=repr(u.get("id"))))
        if u.get("rules") is not None and u.get("rules") not in RULE_PROFILES:
            probs.append(tr("gw_model.check.user_bad_rules", id=u.get("id"), rules=repr(u.get("rules"))))
        uids.add(u.get("id"))
    seen_ip = {}
    logins = set()
    for d in g["devices"]:
        if not _DEV_ID.match(str(d.get("id"))):
            probs.append(tr("gw_model.check.device_bad_id", id=repr(d.get("id"))))
        if d.get("user") not in uids:
            probs.append(tr("gw_model.check.device_no_user", id=d.get("id"), user=d.get("user")))
        if d.get("rules") is not None and d.get("rules") not in RULE_PROFILES:
            probs.append(tr("gw_model.check.device_bad_rules", id=d.get("id"), rules=repr(d.get("rules"))))
        lg = d.get("login")
        if not _LOGIN.match(str(lg)) or lg in logins:
            probs.append(tr("gw_model.check.device_bad_login", id=d.get("id"), login=repr(lg)))
        logins.add(lg)
        for pool, ip in (d.get("ips") or {}).items():
            if not addr.valid_client_ip(st, ip, pool):
                probs.append(tr("gw_model.check.device_ip_pool", id=d.get("id"), ip=ip, pool=pool))
            elif ip in seen_ip:
                probs.append(tr("gw_model.check.ip_twice", ip=ip, first=seen_ip[ip], second=d.get("id")))
            seen_ip[ip] = d.get("id")
    return probs
