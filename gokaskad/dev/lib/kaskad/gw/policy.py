"""The exit policy — ``state.json -> gateway.exit_policy`` (SPEC-stage1-exits §1.3, §3.1–3.3, §6; SPEC-stage1-exit-groups
§2.1–2.4; T2). Plain functions on dicts: no command, no file, no lock (the callers hold the state lock and save).

    load(st, pool=None) -> doc        the effective policy: the stored one with every default filled in (a deep copy);
                                      none stored — one group «main» in «compat» (a read never turns «Стандарт» on).
                                      ``pool`` (see below) — the unplaced lines of the pool are placed (place_new).
    save(st, doc, pool=None, prev=None, lang=None) -> (doc, warnings)   validate (KaskadError USAGE with
                                      ``data["errors"]``), normalize, write into ``st`` (no ``rev.note``: the caller's
                                      transaction counts it, SPEC §3.1 «Кто зовёт»).
    validate(doc, pool=None, prev=None, lang=None) -> (errors, warnings)   [{"code", "field", "text", ...}]
    validate_exit(pref, lang=None) -> errors                              the fields of exit_pref (§3.1)
    ensure_initial(st) -> bool        no policy -> «main» in «standard» (state.born_new) or «compat»; True if written
    group(doc, gid), group_of(doc, eid), tier_of(doc, eid) -> (group, index) | (None, None)
    place_new(doc, pool, hints=None) -> [(eid, gid, tier id)]   where a new exit goes (SPEC-groups §2.4)
    repeat_id(eid), drop_repeats(doc)  a repeated key of the pool («e-…-201») — no exit of its own, never in a tier
    label(group, lang=None), tier_label(group, i, lang=None)    the shown names (empty — «Основная»/«Уровень N»)
    group_duplicates(doc, name, self_id=None) -> [gid]           the same shown name in either language
    observatory_s(doc, pool=None) -> int | None   the interval of the one observatory of the node (§3.2)
    period(doc, grp, kind, pool=None) -> int      P of an exit: xray abroad — observatory_s, else the group's
    preset_fields(preset, role), apply_preset(grp, preset), preset_of(grp)    the presets (§6)
    hold_s(hold, p_nom), p_nom(doc, grp, rule, pool=None)                  the holds compared in seconds (§3.3, С3)

``pool`` — the exits of the pool: a list of rows ({"id", "role", "kind"}, as ``exits.pool``) or ``{"abroad": [ids],
"ru": [ids]}`` (``rev.pool_ids``). Ids that are not in the pool are read tolerantly (a key gone from a subscription).

The rule «упал» is not in the policy: one on the node, the constants of 3.3.5 (``exits.WINDOW`` …, §3.2, Р1); fields
``die_after``/``live_after`` of old drafts are kept as unknown fields and never act.
"""

import copy
import json
import re

from .. import names, state, util
from ..errors import USAGE, KaskadError
from ..i18n import get_lang, tr

VERSION = 1
MAIN = "main"
# «Прокси для API Telegram» — «Напрямую» (SPEC-tg-proxy Р1): the value of ``tg_proxy_group`` that is no group (ids of
# groups are main|g-xxxxxx); the bot then goes to Telegram from the node's own address
TG_DIRECT = "direct"
ROLES = ("abroad", "ru")
PRESETS = {"abroad": ("compat", "standard", "strict", "economy", "custom"), "ru": ("ru_home", "custom")}
CUSTOM = "custom"
METHODS = ("even", "best_rtt", "weights", "capacity")
# the order inside a tier (PLAN-order §3): «auto» — by its method (the field is not stored), «manual» — the first exit of
# the list of the best class gets new devices, ``method`` kept for the way back; 3.3.7.12–13 keep the field and choose by
# ``method`` (a rollback is safe)
ORDERS = ("auto", "manual")
FAILS = ("block", "direct")
TARGETS = ("abroad", "ru")
FREQ_MODES = ("lazy", "often", "normal", "rare", "custom")
FREQ_P = {"lazy": 15, "often": 10, "normal": 30, "rare": 120}
LAZY_OBSERVATORY = 120             # «Лениво» counts as «Редко — 2 мин» for the observatory (it cannot follow traffic)
LAZY_P = 15                        # P of a lazy group and of the exits of a Russian group (§3.2, SG §6.1)
EVERY_S = (10, 600)
SPEED_EVERY_H = (1, 3, 6, 12, 24)
NEW_TIER = ("last", "first")
MAX_GROUPS = 8
MAX_RULES = 20
MAX_BYTES = 256 * 1024             # the whole policy as JSON (the fullest real one ≈ 160 KB; unknown fields kept by
                                   # ``normalize`` never grow state.json without end — проверка безопасности T7, П-1)
MAX_TIERS = 10                     # tiers in a group (проверка безопасности T7, З-1: one write never holds the lock long)
MAX_CONDS = 5
MAX_SERVICES = 5
MAX_NAME = names.MAX
WEIGHT = (1, 100)
HOLD_MIN = (1, 120)
UNSTABLE_S = 900                   # «нестабилен»: in standby for 15 min (§4.3)
FLAP_FALLS = 3                     # falls of «dead» in UNSTABLE_S that make an exit unstable
THENS = ("degraded", "standby", "remove", "push")
METRICS = {                        # metric -> (units, operators)  (§3.3)
    "rtt": (("ms", "x_norm"), (">", "<")),
    "loss": (("pct",), (">", "<")),
    "jitter": (("ms",), (">", "<")),
    "speed": (("mbps",), (">", "<")),
    "service": (("ok",), ("==", "!=")),
    "country_changed": (("bool",), ("==",)),
    "country": (("iso",), ("==", "!=")),
    "traffic": (("gb_day", "gb_month"), (">", "<")),
}
MIN_FAILS = (1, 10)
MIN_FAILS_DEFAULT = 2
QUOTA_PERIODS = ("month", "day")
QUOTA_GB = (0.1, 1000000)          # the limit of a quota, GB: the step of the panel — to the most of the CLI and MCP (С-1)
QUOTA_ACTIONS = ("remove", "standby", "push")
MANUAL = ("standby", "maintenance")
MANUAL_WHY = ("sub_confirm",)

_GID = re.compile(r"^(main|g-[0-9a-f]{6})$")
_TID = re.compile(r"^[a-z0-9-]{1,16}$")
_RID = re.compile(r"^[a-z0-9-]{1,24}$")
_SID = re.compile(r"^[a-z0-9-]{1,24}$")
_EID = re.compile(r"^e-[0-9a-f]{10}$")
_REPEAT = re.compile(r"^e-[0-9a-f]{10}-[0-9]{1,4}$")   # a repeated key of the pool, «e-<hex>-<line>» (``repeat_id``)
_HM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_ISO = re.compile(r"^[A-Z]{2}$")
_URL = re.compile(r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[^\s]*)?$")

# the keys of a group a preset sets; every other field (tiers, fail, name …) is the owner's, never the preset's
PRESET_KEYS = ("flap_standby", "evict_degraded", "return_to_primary", "night_rebalance", "drop_on_degrade", "rules")
PRESET_CHECKS = ("freq", "speed_enabled", "services", "geo")


# ── defaults ───────────────────────────────────────────────────────────────

def default_probes():
    """``exit_policy.probes`` — common to the node (§3.2): the pause «снят», the window of losses, the speed test."""
    return {"off_backoff": {"base_s": 30, "max_s": 600, "reset_after_s": 3600},
            "window": 10,
            "speed": {"max_mb": 10, "max_s": 8, "only_quiet": True, "budget_day_mb": 200, "budget_month_gb": 3}}


def default_checks(role="abroad"):
    return {"targets": role,
            "freq": {"mode": "often", "every_s": None},     # T-q Н-lazy: a new Russian group — as a foreign one
            "speed": {"enabled": False, "every_h": 6, "url": "default"},
            "services": [],
            "geo": {"enabled": True, "every_h": 24, "confirm": 2, "action": "push",
                    "expect": "RU" if role == "ru" else None}}


def default_tier(tid="t1", exits=None):
    return {"id": tid, "name": "", "exits": list(exits or []), "method": "even", "weights": {},
            "tolerance_ms": 30, "tolerance_pct": 20}


def _cond(metric, op, value, unit, **kw):
    c = {"metric": metric, "op": op, "value": value, "unit": unit}
    c.update(kw)
    return c


def _rule(rid, conds, then, back_conds, leave_min, back_min, match="all"):
    """A rule of a preset: both holds in minutes (§6, С3)."""
    return {"id": rid, "name": "", "enabled": True, "exits": "all",
            "when": {"match": match, "conds": conds, "hold": {"minutes": leave_min}},
            "then": then,
            "back": {"match": "all", "conds": back_conds, "hold": {"minutes": back_min}, "manual": False},
            "push": True}


def _preset_rules(preset):
    if preset == "standard":
        return [_rule("r-rtt", [_cond("rtt", ">", 2, "x_norm")], "degraded", [_cond("rtt", "<", 1.5, "x_norm")], 5, 10)]
    if preset == "strict":
        return [_rule("r-rtt", [_cond("rtt", ">", 1.5, "x_norm")], "degraded", [_cond("rtt", "<", 1.2, "x_norm")], 3, 5),
                _rule("r-loss", [_cond("loss", ">", 10, "pct", min_fails=2)], "degraded",
                      [_cond("loss", "<", 5, "pct")], 2, 5),
                _rule("r-loss-hard", [_cond("loss", ">", 50, "pct", min_fails=3)], "remove",
                      [_cond("loss", "<", 10, "pct")], 1, 5)]
    if preset == "ru_home":
        return [_rule("r-loss", [_cond("loss", ">", 30, "pct", min_fails=3)], "degraded",
                      [_cond("loss", "<", 10, "pct")], 2, 5)]
    return []


def preset_fields(preset, role="abroad"):
    """The fields of a group a preset sets (§6; SPEC-groups §6.6): {key: value}, with ``checks`` partial
    ({"freq", "speed": {"enabled"}, "services", "geo"})."""
    # T-q Н-lazy: «Дом» checks as «Стандарт» does («Часто») — a home router that died is seen in a minute, not when
    # traffic comes; a group of «Дом» stored with «Лениво» before stays «Дом» (``LEGACY_FREQ``) and lazy
    freq = {"economy": "normal"}.get(preset, "often")
    services = []
    if preset == "strict":
        services = [{"id": "yt", "name": "YouTube", "url": "https://www.youtube.com/generate_204", "expect": [204],
                     "max_ms": 3000, "every_min": 30}]
    geo = {"enabled": True, "every_h": 6 if preset == "strict" else 24, "confirm": 2, "action": "push",
           "expect": "RU" if role == "ru" else None}
    return {"flap_standby": preset != "compat",
            "evict_degraded": True,
            "return_to_primary": {"enabled": True, "hold_min": 10},
            "night_rebalance": {"enabled": False, "at": "04:00"},
            "drop_on_degrade": False,
            "rules": _preset_rules(preset),
            "checks": {"freq": {"mode": freq, "every_s": None}, "speed": {"enabled": False},
                       "services": services, "geo": geo}}


def default_group(gid=MAIN, role="abroad", preset=None, name="", exits=None):
    preset = preset or ("ru_home" if role == "ru" else "compat")
    g = {"id": gid, "name": name, "role": role, "preset": preset,
         "tiers": [default_tier("t1", exits)],
         "new_exits_tier": "last",
         "per_connection": False,
         "fail": "direct" if role == "ru" else "block",
         "checks": default_checks(role)}
    apply_preset(g, preset if preset != CUSTOM else ("ru_home" if role == "ru" else "compat"))
    g["preset"] = preset
    return g


def default_doc(preset="compat"):
    """The effective policy of a node with none stored (one «main», ``preset``)."""
    return {"version": VERSION, "hint_seen": False, "probes": default_probes(), "new_exits_group": MAIN,
            "tg_proxy_group": MAIN, "groups": [default_group(MAIN, "abroad", preset)]}


def apply_preset(grp, preset):
    """Write the fields of ``preset`` into the group ``grp`` (in place) and ``grp["preset"]``; the speed test's link
    and period, the tiers, the name, ``fail`` stay. -> grp."""
    f = copy.deepcopy(preset_fields(preset, grp.get("role", "abroad")))
    chk = f.pop("checks")
    grp.update(f)
    c = grp.get("checks") if isinstance(grp.get("checks"), dict) else default_checks(grp.get("role", "abroad"))
    c["freq"] = chk["freq"]
    sp = c.get("speed") if isinstance(c.get("speed"), dict) else {}
    sp = dict(default_checks()["speed"], **sp)
    sp["enabled"] = chk["speed"]["enabled"]
    c["speed"] = sp
    c["services"] = chk["services"]
    c["geo"] = chk["geo"]
    grp["checks"] = c
    grp["preset"] = preset
    return grp


def _preset_view(grp):
    c = grp.get("checks") or {}
    view = dict((k, grp.get(k)) for k in PRESET_KEYS)
    view["checks"] = {"freq": c.get("freq"), "speed": {"enabled": bool((c.get("speed") or {}).get("enabled"))},
                      "services": c.get("services"), "geo": c.get("geo")}
    return view


LEGACY_FREQ = {"ru_home": ("lazy",)}   # the frequencies a preset had before (T-q Н-lazy: «Дом» was «Лениво»)


def matches_preset(grp, preset):
    if preset == CUSTOM or preset not in PRESETS.get(grp.get("role", "abroad"), ()):
        return False
    want = preset_fields(preset, grp.get("role", "abroad"))
    view = _canon(_preset_view(grp))
    if view == _canon(want):
        return True
    for mode in LEGACY_FREQ.get(preset, ()):            # a group of the preset stored before: still that preset
        old = copy.deepcopy(want)
        old["checks"]["freq"] = {"mode": mode, "every_s": None}
        if view == _canon(old):
            return True
    return False


def preset_of(grp):
    """The preset the group really follows: its ``preset`` when every field of it is as the preset sets, else
    «custom» («Свой»: a change of any field of the preset turns it, §1.3)."""
    p = grp.get("preset")
    return p if p != CUSTOM and matches_preset(grp, p) else CUSTOM


def _canon(v):
    """Numbers compared as numbers (3 == 3.0), dicts by keys."""
    if isinstance(v, dict):
        return dict((k, _canon(x)) for k, x in v.items())
    if isinstance(v, list):
        return [_canon(x) for x in v]
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except OverflowError:          # an integer of 400 digits (REV-33714 С-1): compared as it is
            return v
    return v


# ── read ───────────────────────────────────────────────────────────────────

def stored(st):
    """``exit_policy`` as stored (a dict) or None — never a default."""
    g = (st or {}).get("gateway") if isinstance(st, dict) else None
    doc = g.get("exit_policy") if isinstance(g, dict) else None
    return doc if isinstance(doc, dict) else None


def _merge(defaults, value):
    """``value`` over ``defaults`` (dicts deep, unknown keys of ``value`` kept); a value of another type than the
    default is kept as it is (validate tells it)."""
    if not isinstance(value, dict) or not isinstance(defaults, dict):
        return copy.deepcopy(value)
    out = copy.deepcopy(defaults)
    for k, v in value.items():
        out[k] = _merge(out[k], v) if k in out and isinstance(out[k], dict) and isinstance(v, dict) \
            else copy.deepcopy(v)
    return out


def _norm_group(raw):
    """A group with the defaults of its preset filled in (a field missing — the preset's; lists of ``raw`` replace)."""
    role = raw.get("role") if raw.get("role") in ROLES else "abroad"
    preset = raw.get("preset") or ("ru_home" if role == "ru" else "compat")
    base = default_group(str(raw.get("id") or ""), role, preset if preset in PRESETS[role] else CUSTOM)
    g = _merge(base, raw)
    if not isinstance(g.get("tiers"), list) or not g["tiers"]:
        g["tiers"] = [default_tier("t1")]
    g["tiers"] = [_merge(default_tier(str(t.get("id") or "")), t) if isinstance(t, dict) else t for t in g["tiers"]]
    if isinstance(g.get("rules"), list):
        g["rules"] = [_norm_rule(r) for r in g["rules"]]
    return g


def _norm_rule(r):
    if not isinstance(r, dict):
        return r
    r = copy.deepcopy(r)
    r.setdefault("name", "")
    r.setdefault("enabled", True)
    r.setdefault("exits", "all")
    r.setdefault("push", True)
    for side in ("when", "back"):
        s = r.get(side)
        if isinstance(s, dict):
            s.setdefault("match", "all")
            for c in s.get("conds") or []:
                if isinstance(c, dict) and c.get("metric") == "loss" and c.get("op") == ">" and "min_fails" not in c:
                    c["min_fails"] = MIN_FAILS_DEFAULT
    if isinstance(r.get("back"), dict):
        r["back"].setdefault("manual", False)
    return r


def normalize(doc):
    """A copy of ``doc`` with every default filled in (unknown fields kept, nothing refused — that is validate's)."""
    doc = doc if isinstance(doc, dict) else {}
    base = default_doc()
    base.pop("groups")
    out = _merge(base, doc)
    raw = doc.get("groups")
    if isinstance(raw, list) and raw:
        out["groups"] = [_norm_group(g) if isinstance(g, dict) else g for g in raw]
    else:
        out["groups"] = [default_group(MAIN, "abroad", "compat")]
    return out


def load(st, pool=None, subs=None):
    """The effective policy of ``st`` (a deep copy with the defaults); none stored — «main» in «compat»: a read never
    turns «Стандарт» on (§1.3). ``pool`` — the unplaced exits of the pool are placed as place_new would (the readers
    then see every exit in its group, as the collector will place it): the hint ``exit_pref.group``, then the group
    of its subscription — ``subs``, the document of ``subs.json`` (``exitsubs.load()``) or its list ``subs``; the
    caller reads it (SPEC-groups §2.4: a key of a subscription is never a tick in another group)."""
    raw = stored(st)
    doc = normalize(raw) if raw is not None else default_doc("compat")
    if not isinstance(doc.get("groups"), list) or not any(isinstance(g, dict) and g.get("id") == MAIN
                                                          for g in doc["groups"]):
        doc["groups"] = [default_group(MAIN, "abroad", "compat")] + \
            [g for g in (doc.get("groups") or []) if isinstance(g, dict)]
    if doc.get("tg_proxy_group") != TG_DIRECT and (group(doc, doc.get("tg_proxy_group")) is None or
                                                  group(doc, doc["tg_proxy_group"]).get("role") != "abroad"):
        doc["tg_proxy_group"] = MAIN               # no field, a group gone or not abroad -> «main» (§2.9); «direct» stays
    if group(doc, doc.get("new_exits_group")) is None or group(doc, doc["new_exits_group"]).get("role") != "abroad":
        doc["new_exits_group"] = MAIN
    drop_repeats(doc)                              # T-3379 Н-3: one stored by 3.3.7.6–3.3.7.10 — never read, never written
    if pool is not None:
        place_new(doc, pool, hints_of(st, subs))
    return doc


def hints_of(st, subs=None):
    """{eid: [gid, …]} for ``place_new`` (SPEC-groups §2.4): the hint ``exit_pref.group`` of the key, then the group
    of the subscription that owns it (``subs[].group`` of its ``keys``)."""
    g = (st or {}).get("gateway") if isinstance(st, dict) else None
    ex = (g or {}).get("exits") if isinstance(g, dict) else None
    out = {}
    for eid, p in (ex or {}).items() if isinstance(ex, dict) else []:
        if isinstance(p, dict) and isinstance(p.get("group"), str) and p["group"]:
            out.setdefault(eid, []).append(p["group"])
    rows = subs.get("subs") if isinstance(subs, dict) else subs
    for sub in rows if isinstance(rows, list) else []:
        gid = sub.get("group") if isinstance(sub, dict) else None
        if not isinstance(gid, str) or not gid:
            continue
        for eid in sub.get("keys") or []:
            if isinstance(eid, str) and gid not in out.get(eid, []):
                out.setdefault(eid, []).append(gid)
    return out


def ensure_initial(st):
    """``gw/ops.enable`` (T7): no policy in ``st`` -> one group «main» written, «Стандарт» for a node born on the code
    of stage 1 (``state.born_new``), «Как раньше» (compat) for an updated one; a policy there — untouched. The names
    are empty (shown by the language of the node). The caller's transaction counts ``rev``. -> True if written."""
    if stored(st) is not None:
        return False
    doc = default_doc("standard" if state.born_new(st) else "compat")
    g = st.get("gateway")
    if not isinstance(g, dict):
        g = st["gateway"] = {}
    g["exit_policy"] = doc
    return True


def group(doc, gid):
    for g in (doc or {}).get("groups") or []:
        if isinstance(g, dict) and g.get("id") == gid:
            return g
    return None


def tier_of(doc, eid):
    """(group, index of the tier) of the exit ``eid`` or (None, None)."""
    for g in (doc or {}).get("groups") or []:
        if not isinstance(g, dict):
            continue
        for i, t in enumerate(g.get("tiers") or []):
            if isinstance(t, dict) and eid in (t.get("exits") or []):
                return g, i
    return None, None


def group_of(doc, eid):
    """The group of the exit ``eid`` (a dict) or None — an exit is in one group and one tier at the most."""
    return tier_of(doc, eid)[0]


def exits_of(grp):
    """The ids of the exits of a group in the order of its tiers."""
    out = []
    for t in (grp or {}).get("tiers") or []:
        if isinstance(t, dict):
            out.extend(e for e in t.get("exits") or [] if e not in out)
    return out


# ── pool ───────────────────────────────────────────────────────────────────

def pool_roles(pool):
    """{eid: role} of ``pool`` (rows or {"abroad": ids, "ru": ids}); None — unknown."""
    if pool is None:
        return None
    out = {}
    if isinstance(pool, dict):
        for role in ROLES:
            for eid in pool.get(role) or []:
                out[eid] = role
        return out
    for e in pool:
        if isinstance(e, dict) and e.get("id"):
            out[e["id"]] = e.get("role") or "abroad"
        elif isinstance(e, str):
            out[e] = "abroad"
    return out


def pool_kinds(pool):
    """{eid: "awg" | "xray"} of the rows of ``pool`` that tell their kind."""
    out = {}
    if isinstance(pool, (list, tuple)):
        for e in pool:
            if isinstance(e, dict) and e.get("id") and e.get("kind"):
                out[e["id"]] = "xray" if e["kind"] in ("xray", "vless") else e["kind"]
    return out


def repeat_id(eid):
    """True for the id of a repeated key of the pool — «e-<hex>-<line>» (``exits.pool``: ``dup_of``, «e-…-201» while a
    change of role holds one line in both files, ``ops.exit_role_move``). No exit of its own (no leg, no probe: the
    copy above it is the exit) — never in a tier (validate refuses it: one there refused every write of the policy,
    T-3379 Н-3), never a point of the charts."""
    return isinstance(eid, str) and bool(_REPEAT.match(eid))


def drop_repeats(doc):
    """The ids of repeated keys (``repeat_id``) out of every tier and its weights (in place): placed by 3.3.7.6–3.3.7.10
    (T-3379 Н-3). -> [(eid, gid)]."""
    out = []
    for g in (doc or {}).get("groups") or []:
        for t in (g.get("tiers") or []) if isinstance(g, dict) else []:
            if not isinstance(t, dict) or not isinstance(t.get("exits"), list):
                continue
            bad = [e for e in t["exits"] if repeat_id(e)]
            if bad:
                t["exits"] = [e for e in t["exits"] if not repeat_id(e)]
                w = t.get("weights")
                for e in bad:
                    if isinstance(w, dict):
                        w.pop(e, None)
                out.extend((e, g.get("id")) for e in bad)
    return out


def _pool_order(pool):
    """[(eid, role)] of ``pool`` — a repeated key (``dup_of``, ``repeat_id``) is no exit of its own: left out."""
    if isinstance(pool, dict):
        return [(eid, role) for role in ROLES for eid in pool.get(role) or [] if not repeat_id(eid)]
    out = []
    for e in pool or []:
        if isinstance(e, dict) and e.get("id"):
            if not e.get("dup_of") and not repeat_id(e["id"]):
                out.append((e["id"], e.get("role") or "abroad"))
        elif isinstance(e, str) and not repeat_id(e):
            out.append((e, "abroad"))
    return out


def place_new(doc, pool, hints=None):
    """Place every exit of ``pool`` that is in no group (in place; SPEC-groups §2.4) — the same in every process:
    a foreign one — the hint ``exit_pref.group`` (``hints[eid]``: a gid or a list of gids, the first fit wins — the
    hint of the key, then the group of its subscription) -> ``new_exits_group`` -> «main»; a Russian one — its hint ->
    the first Russian group -> none (out of the groups: no traffic goes to it). The tier — ``new_exits_tier`` of the
    group (``last`` | ``first`` | a tier id; an unknown id — the last). -> [(eid, gid, tier id)]."""
    hints = hints or {}
    placed = []
    for eid, role in _pool_order(pool):
        if group_of(doc, eid) is not None:
            continue
        want = hints.get(eid)
        want = [want] if isinstance(want, str) else list(want or [])
        target = None
        for gid in want:
            g = group(doc, gid)
            if g is not None and g.get("role", "abroad") == role:
                target = g
                break
        if target is None:
            if role == "abroad":
                target = group(doc, doc.get("new_exits_group")) or group(doc, MAIN)
                if target is not None and target.get("role", "abroad") != "abroad":
                    target = group(doc, MAIN)
            else:
                target = next((g for g in doc.get("groups") or [] if isinstance(g, dict) and g.get("role") == "ru"),
                              None)
        if target is None:
            continue
        t = new_tier(target)
        t.setdefault("exits", []).append(eid)
        placed.append((eid, target["id"], t.get("id")))
    return placed


def new_tier(grp):
    """The tier a new exit of ``grp`` goes to (in place; SPEC-groups §2.4, §6.6): ``new_exits_tier`` — «first», a tier
    id, «last» (an unknown id — the last). A RUSSIAN group with «last» gives each key a tier of its own after the last
    one (§6.6 «Дом»: «по порядку — каждый ключ свой уровень», the chain «Роутер → Дача → с сервера напрямую», not two
    routers shared evenly) while the last tier holds exits and there is room (MAX_TIERS); the new tier — the first free
    id «tN», no name (shown «Уровень N», SE §3.1). The same in every process (``place_new`` is pure). -> the tier."""
    tiers = grp.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        tiers = grp["tiers"] = [default_tier("t1")]
    where = grp.get("new_exits_tier") or "last"
    if where == "first":
        return tiers[0]
    t = next((x for x in tiers if isinstance(x, dict) and x.get("id") == where), None)
    if t is not None and where != "last":
        return t
    last = tiers[-1]
    if grp.get("role") == "ru" and isinstance(last, dict) and last.get("exits") and len(tiers) < MAX_TIERS:
        ids = set(x.get("id") for x in tiers if isinstance(x, dict))
        n = len(tiers) + 1
        while "t%d" % n in ids:
            n += 1
        last = default_tier("t%d" % n)
        tiers.append(last)
    return last


def prune_gone(doc, known):
    """The ids no file of the pool has any more out of the tiers of every group (in place; KT3-1: ``gw exit-del`` left
    them — «Выходы 20» at 14 in the pool). ``known`` — None (not known: nothing), the ids of both files, or by role
    {"abroad": ids | None, "ru": ids} (``policy_ops.known_ids``): a role None — the groups of that role keep every id
    (an empty upstream.txt — the foreign groups; REV-3371-pre З-2), an id of any known file stays in any group (an
    exit moved to a group of the other role, ``--confirm-role``). A group of an unknown role is left alone. An empty
    tier stays (the form shows it). The id of a repeated key (``repeat_id``) goes whatever ``known`` says: it is no exit
    of its own even while its line is in a file (T-3379 Н-3). -> [(eid, gid)]."""
    rep = drop_repeats(doc)
    if known is None:
        return rep
    if isinstance(known, dict):
        by_role = dict((r, None if known.get(r) is None else set(known.get(r))) for r in ROLES)
        keep_ids = set()
        for ids in by_role.values():
            keep_ids |= ids or set()
    else:
        by_role, keep_ids = None, set(known)
    gone = []
    for g in doc.get("groups") or []:
        if not isinstance(g, dict):
            continue
        if by_role is not None and by_role.get(g.get("role", "abroad")) is None:
            continue
        for t in g.get("tiers") or []:
            if not isinstance(t, dict) or not isinstance(t.get("exits"), list):
                continue
            keep = [e for e in t["exits"] if e in keep_ids]
            if len(keep) != len(t["exits"]):
                gone.extend((e, g.get("id")) for e in t["exits"] if e not in keep_ids)
                t["exits"] = keep
    return rep + gone


# ── names ──────────────────────────────────────────────────────────────────

def label(grp, lang=None):
    """The shown name of a group: its name, or «Основная»/«Main» for «main» with an empty one (the language of the
    node: SPEC §3.1 «Имена по умолчанию»); another group never has an empty name — its id then."""
    nm = str((grp or {}).get("name") or "")
    if nm:
        return nm
    if (grp or {}).get("id") == MAIN:
        return tr("ep.name.main", lang)
    return str((grp or {}).get("id") or "")


def tier_label(grp, i, lang=None):
    """The shown name of the tier ``i`` (0-based) of a group: its name, or «Уровень N»/«Level N» (N = i + 1)."""
    tiers = (grp or {}).get("tiers") or []
    t = tiers[i] if 0 <= i < len(tiers) and isinstance(tiers[i], dict) else {}
    nm = str(t.get("name") or "")
    return nm or tr("ep.name.tier", lang, n=i + 1)


def _labels(grp):
    """The shown names of a group in both languages (a name equal to the default of «main» shows as the default)."""
    if grp.get("id") == MAIN and _default_name_of(grp):
        grp = dict(grp, name="")
    return [label(grp, lang) for lang in ("ru", "en")]


def _tier_labels(grp, i):
    if _default_name_of(grp, i):
        return [tr("ep.name.tier", lang, n=i + 1) for lang in ("ru", "en")]
    return [tier_label(grp, i, lang) for lang in ("ru", "en")]


def group_duplicates(doc, name, self_id=None):
    """[gid] of the groups whose SHOWN name (in either language) is the same as ``name`` (names.same): «Main» and
    «основная» are duplicates of «main» with an empty name (RN §2)."""
    items = []
    for g in (doc or {}).get("groups") or []:
        if isinstance(g, dict):
            for lb in _labels(g):
                items.append((g.get("id"), lb))
    out = []
    for gid in names.duplicates(items, name, self_id):
        if gid not in out:
            out.append(gid)
    return out


def _default_name_of(grp, i=None):
    """Is the name the default one of this place (on any language) -> True."""
    tiers = grp.get("tiers") or []
    if i is not None and not (0 <= i < len(tiers) and isinstance(tiers[i], dict)):
        return False
    nm = (tiers[i] if i is not None else grp).get("name")
    if not isinstance(nm, str) or not nm:
        return False
    if i is None:
        return grp.get("id") == MAIN and any(names.same(nm, tr("ep.name.main", lang)) for lang in ("ru", "en"))
    return any(names.same(nm, tr("ep.name.tier", lang, n=i + 1)) for lang in ("ru", "en"))


# ── periods ────────────────────────────────────────────────────────────────

def freq_p(grp):
    """The base period P of a group's own probes, s (§3.2): lazy 15, often 10, normal 30, rare 120, custom every_s."""
    f = ((grp or {}).get("checks") or {}).get("freq") or {}
    m = f.get("mode")
    if m == "custom":
        v = f.get("every_s")
        return int(v) if _num(v) is not None and v > 0 else FREQ_P["often"]     # int(inf) — OverflowError (С-1)
    if (grp or {}).get("role") == "ru" and m not in FREQ_P:
        return LAZY_P
    return FREQ_P.get(m, FREQ_P["often"])


def is_lazy(grp):
    return (((grp or {}).get("checks") or {}).get("freq") or {}).get("mode") == "lazy"


def observatory_s(doc, pool=None):
    """The interval of the one observatory of the node (§3.2): the most frequent P of the foreign groups that have
    exits (in ``pool`` when given); «Лениво» counts as 120 s; no foreign exit — None (no observatory)."""
    roles = pool_roles(pool)
    best = None
    for g in (doc or {}).get("groups") or []:
        if not isinstance(g, dict) or g.get("role", "abroad") != "abroad":
            continue
        ids = exits_of(g)
        if roles is not None:
            ids = [e for e in ids if roles.get(e) == "abroad"]
        if not ids:
            continue
        p = LAZY_OBSERVATORY if is_lazy(g) else freq_p(g)
        best = p if best is None else min(best, p)
    return best


def period(doc, grp, kind, pool=None):
    """P of an exit of the group ``grp`` (§3.2): xray abroad — the observatory's; AWG and Russian exits — the
    group's (lazy — 15 s)."""
    if (grp or {}).get("role", "abroad") == "abroad" and kind in ("xray", "vless"):
        return observatory_s(doc, pool) or freq_p(grp)
    return LAZY_P if is_lazy(grp) else freq_p(grp)


def awg_rtt_period(p, traffic=True):
    """The period of the HTTP probe of an AWG exit's response: max(3P, 30) under traffic, max(12P, 120) idle."""
    return max(3 * p, 30) if traffic else max(12 * p, 120)


def reaction_s(p, lazy=False):
    """(from, to) s: how long a full outage takes to give «упал» at the period ``p`` (§3.2, for the line of the form)."""
    if lazy:
        return (15, 60)
    if p <= 30:
        return (50, 60 + 10)
    return (p - 2, 2 * p + 10)


def p_nom(doc, grp, rule, pool=None):
    """P_ном of a rule (С3): the largest nominal period of a measure among the kinds of its exits — xray: the
    observatory's; AWG: the response probe under traffic max(3P, 30); a Russian group and «Лениво» — 15 s. The kinds
    unknown (no pool) — both kinds."""
    if (grp or {}).get("role") == "ru" or is_lazy(grp):
        return LAZY_P
    kinds = pool_kinds(pool)
    ids = exits_of(grp)
    sel = (rule or {}).get("exits", "all")
    if isinstance(sel, list):
        ids = [e for e in sel]
    want = set(kinds.get(e) for e in ids if kinds.get(e))
    if isinstance(sel, dict) and sel.get("kind"):
        want = {sel["kind"]}
    if not want:
        want = {"awg", "xray"}
    out = []
    if "xray" in want:
        out.append(observatory_s(doc, pool) or freq_p(grp))
    if "awg" in want:
        out.append(awg_rtt_period(freq_p(grp)))
    return max(out)


def hold_s(hold, pn):
    """A hold in seconds: ``minutes`` × 60, ``samples`` × P_ном; None — not a hold."""
    if not isinstance(hold, dict):
        return None
    if _num(hold.get("minutes")) is not None:
        return float(hold["minutes"]) * 60
    if _int(hold.get("samples")) is not None:
        return float(hold["samples"]) * pn
    return None


# ── validate ───────────────────────────────────────────────────────────────

def _num(v):
    """A number a check takes: not bool, finite, within ±util.NUM_MAX (REV-33714 С-1: inf of ``1e400``, ``1e308`` and an
    integer of 400 digits passed and overflowed in × 10, float() and round() further on); None otherwise."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and -util.NUM_MAX <= v <= util.NUM_MAX \
        else None


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and -util.NUM_MAX <= v <= util.NUM_MAX else None


def quota_gb(quota):
    """The limit of ``quota`` in GB as a float, or None — no limit to count by: not a number, NaN, less than a byte
    (``1e-320`` made the percent inf: int(pct) — OverflowError) or past ±util.NUM_MAX (``10**400``: float() —
    OverflowError). The one test of every reader of a saved quota — the collector, the health step, the probes, the
    panel; a file of an older version counts as no quota instead of failing every round (REV-33714 С-1). What a new
    quota may be is the check's (QUOTA_GB, the step 0.1)."""
    v = quota.get("limit_gb") if isinstance(quota, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 1e-9 <= v <= util.NUM_MAX:
        return None                    # an int and a float compare exactly, never overflow; NaN is out
    return float(v)


class _Out(object):
    def __init__(self, lang):
        self.lang = lang
        self.errors = []
        self.warnings = []

    def err(self, code, field, **kw):
        self.errors.append(dict(kw, code=code, field=field, text=tr(code, self.lang, field=field, **kw)))

    def warn(self, code, field, **kw):
        self.warnings.append(dict(kw, code=code, field=field, text=tr(code, self.lang, field=field, **kw)))


def _check_name(out, value, field, what):
    try:
        names.check(value, what, out.lang)
    except KaskadError as e:
        out.errors.append(dict(e.data, code="ep.err.name", field=field, text=str(e)))


def validate(doc, pool=None, prev=None, lang=None):
    """(errors, warnings) of a policy to be saved (one function for the API, CLI and MCP; §3.1, §3.3, SPEC-groups
    §2.3, §2.6): each — {"code": "ep.err.*"|"ep.warn.*", "field": "groups[1].tiers[0].exits", "text": RU/EN, …}.
    ``pool`` — the roles (and kinds) of the exits; ``prev`` — the policy before (a role never changes)."""
    lang = get_lang(lang)
    out = _Out(lang)
    if not isinstance(doc, dict):
        out.err("ep.err.type", "")
        return out.errors, out.warnings
    size = len(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    if size > MAX_BYTES:
        out.err("ep.err.too_big", "", kb=(size + 1023) // 1024, max=MAX_BYTES // 1024)
        return out.errors, out.warnings
    if doc.get("version", VERSION) != VERSION:
        out.err("ep.err.version", "version", value=repr(doc.get("version")))
    groups = doc.get("groups")
    if not isinstance(groups, list) or not groups:
        out.err("ep.err.main", "groups")
        return out.errors, out.warnings
    if len(groups) > MAX_GROUPS:
        out.err("ep.err.groups_many", "groups", max=MAX_GROUPS)
    roles = pool_roles(pool)
    prev_roles = dict((g.get("id"), g.get("role", "abroad")) for g in (prev or {}).get("groups") or []
                      if isinstance(g, dict))
    seen_g = set()
    where = {}
    for gi, g in enumerate(groups):
        f = "groups[%d]" % gi
        if not isinstance(g, dict):
            out.err("ep.err.type", f)
            continue
        gid = g.get("id")
        if not isinstance(gid, str) or not _GID.match(gid):
            out.err("ep.err.group_id", f + ".id", value=repr(gid))
        elif gid in seen_g:
            out.err("ep.err.group_twice", f + ".id", id=gid)
        seen_g.add(gid)
        role = g.get("role", "abroad")
        if role not in ROLES:
            out.err("ep.err.role", f + ".role", value=repr(role))
            role = "abroad"
        if gid == MAIN and role != "abroad":
            out.err("ep.err.role", f + ".role", value=repr(role))
        if gid in prev_roles and prev_roles[gid] != role:
            out.err("ep.err.role_change", f + ".role", id=gid)
        nm = g.get("name", "")
        if gid == MAIN and nm in ("", None):
            pass
        else:
            _check_name(out, nm, f + ".name", "group")
        if g.get("preset", "compat") not in PRESETS[role]:
            out.err("ep.err.preset", f + ".preset", value=repr(g.get("preset")))
        _validate_group(out, doc, g, gi, role, roles, where, pool)
    if MAIN not in seen_g:
        out.err("ep.err.main", "groups")
    for key in ("tg_proxy_group", "new_exits_group"):
        v = doc.get(key, MAIN)
        if key == "tg_proxy_group" and v == TG_DIRECT:
            continue                                # «Напрямую» (SPEC-tg-proxy Р1)
        tg = group(doc, v)
        if tg is None or tg.get("role", "abroad") != "abroad":
            out.err("ep.err." + key, key, value=repr(v))
    _validate_probes(out, doc.get("probes"))
    for gi, g in enumerate(groups):
        if not isinstance(g, dict):
            continue
        dup = []
        for lb in _labels(g):
            dup += [x for x in group_duplicates(doc, lb, g.get("id")) if x not in dup]
        if dup:
            out.warn("ep.warn.group_dup", "groups[%d].name" % gi, name=label(g, lang), ids=", ".join(dup))
    return out.errors, out.warnings


def _validate_group(out, doc, g, gi, role, roles, where, pool):
    f = "groups[%d]" % gi
    tiers = g.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        out.err("ep.err.tiers", f + ".tiers")
        tiers = []
    if len(tiers) > MAX_TIERS:
        out.err("ep.err.tiers_many", f + ".tiers", max=MAX_TIERS)
        tiers = tiers[:MAX_TIERS]                   # refused already: the rest is not checked
    tids = set()
    shown = []
    gexits = set()
    for ti, t in enumerate(tiers):
        tf = "%s.tiers[%d]" % (f, ti)
        if not isinstance(t, dict):
            out.err("ep.err.type", tf)
            continue
        tid = t.get("id")
        if not isinstance(tid, str) or not _TID.match(tid):
            out.err("ep.err.tier_id", tf + ".id", value=repr(tid))
        elif tid in tids:
            out.err("ep.err.tier_twice", tf + ".id", id=tid)
        tids.add(tid)
        if t.get("name") not in ("", None):
            _check_name(out, t.get("name"), tf + ".name", "level")
        shown.append(_tier_labels(g, ti))
        ex = t.get("exits", [])
        if not isinstance(ex, list):
            out.err("ep.err.type", tf + ".exits")
            ex = []
        for eid in ex:
            if not isinstance(eid, str) or not _EID.match(eid):
                out.err("ep.err.exit_id", tf + ".exits", value=repr(eid))
                continue
            if eid in where:
                out.err("ep.err.exit_twice", tf + ".exits", id=eid, other=where[eid])
                continue
            where[eid] = tf
            gexits.add(eid)
            if roles is not None and eid in roles and roles[eid] != role:
                out.err("ep.err.exit_role", tf + ".exits", id=eid, role=roles[eid], group_role=role)
        m = t.get("method", "even")
        if m not in METHODS:
            out.err("ep.err.method", tf + ".method", value=repr(m))
        if t.get("order", "auto") not in ORDERS:
            out.err("ep.err.order", tf + ".order", value=repr(t.get("order")))
        w = t.get("weights") or {}
        if not isinstance(w, dict):
            out.err("ep.err.type", tf + ".weights")
            w = {}
        for eid, v in w.items():
            if _int(v) is None or not WEIGHT[0] <= v <= WEIGHT[1]:
                out.err("ep.err.weight", tf + ".weights", id=eid, value=repr(v), lo=WEIGHT[0], hi=WEIGHT[1])
        if w and m != "weights":
            out.warn("ep.warn.weights_unused", tf + ".weights")
        tm, tp = t.get("tolerance_ms", 30), t.get("tolerance_pct", 20)
        if _num(tm) is None or tm < 0 or _num(tp) is None or not 0 <= tp <= 100:
            out.err("ep.err.tolerance", tf + ".tolerance_ms")
    first = {}                            # names._key of a shown name -> the first tier that shows it (O(n))
    for ti, s in enumerate(shown):        # the same shown name in either language: a warning, never a refusal
        keys = set(names._key(a) for a in s)
        tj = min((first[k] for k in keys if k in first), default=None)
        for k in keys:
            first.setdefault(k, ti)
        if tj is not None:
            out.warn("ep.warn.tier_dup", "%s.tiers[%d].name" % (f, ti), name=tier_label(g, ti, out.lang), other=tj + 1)
    nt = g.get("new_exits_tier", "last")
    if nt not in NEW_TIER and nt not in tids:
        out.err("ep.err.new_tier", f + ".new_exits_tier", value=repr(nt))
    if g.get("per_connection") and (g.get("id") != MAIN or role != "abroad"):
        out.err("ep.err.per_connection_main", f + ".per_connection")
    for key in ("per_connection", "evict_degraded", "flap_standby", "drop_on_degrade"):
        if key in g and not isinstance(g[key], bool):
            out.err("ep.err.type", f + "." + key)
    rtp = g.get("return_to_primary") or {}
    hm = rtp.get("hold_min", 10)
    if _int(hm) is None or not HOLD_MIN[0] <= hm <= HOLD_MIN[1]:
        out.err("ep.err.hold_min", f + ".return_to_primary.hold_min", value=repr(hm), lo=HOLD_MIN[0], hi=HOLD_MIN[1])
    at = (g.get("night_rebalance") or {}).get("at", "04:00")
    if not isinstance(at, str) or not _HM.match(at):
        out.err("ep.err.night_at", f + ".night_rebalance.at", value=repr(at))
    if g.get("fail", "block") not in FAILS:
        out.err("ep.err.fail", f + ".fail", value=repr(g.get("fail")))
    _validate_checks(out, g.get("checks") or {}, f + ".checks", role)
    rules = g.get("rules") or []
    if not isinstance(rules, list):
        out.err("ep.err.type", f + ".rules")
        rules = []
    if len(rules) > MAX_RULES:
        out.err("ep.err.rules_many", f + ".rules", max=MAX_RULES)
    rids = set()
    for ri, r in enumerate(rules):
        _validate_rule(out, doc, g, r, "%s.rules[%d]" % (f, ri), rids, gexits, pool)
    n = sum(len(((r.get("when") or {}).get("conds") or [])) for r in rules if isinstance(r, dict)
            and isinstance(r.get("when"), dict) and isinstance(r["when"].get("conds"), list))
    if n > MAX_CONDS:                          # §3.3: the conditions of the group (its «when»; «back» returns them)
        out.err("ep.err.conds_group", f + ".rules", max=MAX_CONDS, n=n)


def _validate_checks(out, c, f, role):
    if c.get("targets", role) not in TARGETS or (role == "ru" and c.get("targets", role) != "ru"):
        out.err("ep.err.targets", f + ".targets", value=repr(c.get("targets")))
    fr = c.get("freq") or {}
    mode = fr.get("mode", "often")
    if mode not in FREQ_MODES:
        out.err("ep.err.freq_mode", f + ".freq.mode", value=repr(mode))
    every = fr.get("every_s")
    if mode == "custom":
        if _int(every) is None or not EVERY_S[0] <= every <= EVERY_S[1]:
            out.err("ep.err.freq_every", f + ".freq.every_s", value=repr(every), lo=EVERY_S[0], hi=EVERY_S[1])
    elif every is not None:
        out.err("ep.err.freq_every_custom", f + ".freq.every_s")
    sp = c.get("speed") or {}
    if not isinstance(sp.get("enabled", False), bool):
        out.err("ep.err.type", f + ".speed.enabled")
    if sp.get("every_h", 6) not in SPEED_EVERY_H or isinstance(sp.get("every_h"), bool):
        out.err("ep.err.speed_every", f + ".speed.every_h", value=repr(sp.get("every_h")),
                allowed=", ".join(str(x) for x in SPEED_EVERY_H))
    url = sp.get("url", "default")
    if url != "default" and not (isinstance(url, str) and _URL.match(url)):
        out.err("ep.err.speed_url", f + ".speed.url", value=repr(url))
    svs = c.get("services") or []
    if not isinstance(svs, list):
        out.err("ep.err.type", f + ".services")
        svs = []
    if len(svs) > MAX_SERVICES:
        out.err("ep.err.services_many", f + ".services", max=MAX_SERVICES)
    sids = set()
    for si, s in enumerate(svs):
        sf = "%s.services[%d]" % (f, si)
        if not isinstance(s, dict):
            out.err("ep.err.type", sf)
            continue
        sid = s.get("id")
        if not isinstance(sid, str) or not _SID.match(sid) or sid in sids:
            out.err("ep.err.service_id", sf + ".id", value=repr(sid))
        sids.add(sid)
        if not isinstance(s.get("url"), str) or not _URL.match(s.get("url") or ""):
            out.err("ep.err.service_url", sf + ".url", value=repr(s.get("url")))
        exp = s.get("expect", [200])
        if not isinstance(exp, list) or not exp or any(_int(x) is None or not 100 <= x <= 599 for x in exp):
            out.err("ep.err.service_expect", sf + ".expect", value=repr(exp))
        ms = s.get("max_ms", 3000)
        if _int(ms) is None or not 100 <= ms <= 30000:
            out.err("ep.err.service_ms", sf + ".max_ms", value=repr(ms))
        em = s.get("every_min", 30)
        if _int(em) is None or not 5 <= em <= 1440:
            out.err("ep.err.service_every", sf + ".every_min", value=repr(em))
    geo = c.get("geo") or {}
    if geo.get("action", "push") != "push":
        out.err("ep.err.geo_action", f + ".geo.action", value=repr(geo.get("action")))
    if not isinstance(geo.get("enabled", True), bool):
        out.err("ep.err.type", f + ".geo.enabled")
    eh = geo.get("every_h", 24)
    if _int(eh) is None or not 1 <= eh <= 168:
        out.err("ep.err.geo_every", f + ".geo.every_h", value=repr(eh))
    cf = geo.get("confirm", 2)
    if _int(cf) is None or not 1 <= cf <= 5:
        out.err("ep.err.geo_confirm", f + ".geo.confirm", value=repr(cf))
    ex = geo.get("expect")
    if ex is not None and not (isinstance(ex, str) and _ISO.match(ex)):
        out.err("ep.err.geo_expect", f + ".geo.expect", value=repr(ex))


def _side_ok(out, side, f, svc_ids):
    if not isinstance(side, dict):
        out.err("ep.err.type", f)
        return False
    if side.get("match", "all") not in ("all", "any"):
        out.err("ep.err.rule_match", f + ".match", value=repr(side.get("match")))
    conds = side.get("conds")
    if not isinstance(conds, list) or not conds:
        out.err("ep.err.conds", f + ".conds", max=MAX_CONDS)
        conds = []
    elif len(conds) > MAX_CONDS:
        out.err("ep.err.conds", f + ".conds", max=MAX_CONDS)
    for ci, c in enumerate(conds):
        cf = "%s.conds[%d]" % (f, ci)
        if not isinstance(c, dict):
            out.err("ep.err.type", cf)
            continue
        m = c.get("metric")
        if m not in METRICS:
            out.err("ep.err.cond_metric", cf + ".metric", value=repr(m))
            continue
        units, ops = METRICS[m]
        if c.get("op") not in ops:
            out.err("ep.err.cond_op", cf + ".op", value=repr(c.get("op")), allowed=" ".join(ops))
        if c.get("unit", units[0]) not in units:
            out.err("ep.err.cond_unit", cf + ".unit", value=repr(c.get("unit")), allowed=", ".join(units))
        v = c.get("value")
        if m in ("service", "country_changed"):
            if not isinstance(v, bool):
                out.err("ep.err.cond_value", cf + ".value", value=repr(v))
        elif m == "country":
            if not (isinstance(v, str) and _ISO.match(v)):
                out.err("ep.err.cond_value", cf + ".value", value=repr(v))
        elif _num(v) is None or v < 0 or (m == "loss" and v > 100):
            out.err("ep.err.cond_value", cf + ".value", value=repr(v))
        if m == "service" and c.get("service") not in svc_ids:
            out.err("ep.err.service_ref", cf + ".service", value=repr(c.get("service")))
        if m == "loss" and "min_fails" in c:
            mf = c.get("min_fails")
            if _int(mf) is None or not MIN_FAILS[0] <= mf <= MIN_FAILS[1]:
                out.err("ep.err.min_fails", cf + ".min_fails", value=repr(mf), lo=MIN_FAILS[0], hi=MIN_FAILS[1])
    hold = side.get("hold")
    if hold is not None:
        ok = isinstance(hold, dict) and len([k for k in ("samples", "minutes") if k in hold]) == 1 and (
            (_int(hold.get("samples")) is not None and 1 <= hold["samples"] <= 100) or
            (_num(hold.get("minutes")) is not None and 0 < hold["minutes"] <= 1440))
        if not ok:
            out.err("ep.err.hold", f + ".hold", value=repr(hold))
            return False
    return True


def _validate_rule(out, doc, g, r, f, rids, gexits, pool):
    if not isinstance(r, dict):
        out.err("ep.err.type", f)
        return
    rid = r.get("id")
    if not isinstance(rid, str) or not _RID.match(rid):
        out.err("ep.err.rule_id", f + ".id", value=repr(rid))
    elif rid in rids:
        out.err("ep.err.rule_twice", f + ".id", id=rid)
    rids.add(rid)
    sel = r.get("exits", "all")
    if sel != "all":
        if isinstance(sel, list):
            for eid in sel:
                if eid not in gexits:
                    out.err("ep.err.rule_exits", f + ".exits", id=repr(eid))
        elif not (isinstance(sel, dict) and sel.get("kind") in ("awg", "xray")):
            out.err("ep.err.rule_exits", f + ".exits", id=repr(sel))
    then = r.get("then")
    if then == "off":
        out.err("ep.err.then_off", f + ".then")
    elif then not in THENS:
        out.err("ep.err.then", f + ".then", value=repr(then))
    svc = set(s.get("id") for s in ((g.get("checks") or {}).get("services") or []) if isinstance(s, dict))
    when = r.get("when")
    if not _side_ok(out, when, f + ".when", svc):
        return
    back = r.get("back")
    if back is None:
        if then in ("degraded", "standby", "remove"):
            out.err("ep.err.back_missing", f + ".back")
        return
    manual = isinstance(back, dict) and back.get("manual") is True
    if isinstance(back, dict) and back.get("manual") not in (None, True, False):
        out.err("ep.err.type", f + ".back.manual")
    if not manual or (isinstance(back, dict) and back.get("conds")):
        if not _side_ok(out, back, f + ".back", svc):
            return
    if then == "remove" and not manual and not (isinstance(back, dict) and back.get("hold")):
        out.err("ep.err.remove_back", f + ".back")
    # «back» on the good side of the threshold (for «>» — back.value < when.value)
    for c in when.get("conds") or []:
        if not isinstance(c, dict) or c.get("op") not in (">", "<"):
            continue
        for b in (back or {}).get("conds") or []:
            if not isinstance(b, dict) or b.get("metric") != c.get("metric") or \
                    b.get("unit", c.get("unit")) != c.get("unit"):
                continue
            if _num(b.get("value")) is None or _num(c.get("value")) is None:
                continue
            good = (b.get("op") == "<" and b["value"] < c["value"]) if c["op"] == ">" else \
                (b.get("op") == ">" and b["value"] > c["value"])
            if not good:
                out.err("ep.err.back_side", f + ".back.conds", metric=c.get("metric"), when=c.get("value"),
                        back=b.get("value"))
    pn = p_nom(doc, g, r, pool)
    ls, bs = hold_s(when.get("hold"), pn), hold_s((back or {}).get("hold"), pn)
    if ls is not None and bs is not None and bs < ls:
        out.err("ep.err.back_hold", f + ".back.hold", leave_min=round(ls / 60.0, 1), back_min=round(bs / 60.0, 1))
    # warnings: an absolute rtt over exits of both kinds; «speed» while the speed test of the group is off
    kinds = pool_kinds(pool)
    ids = [e for e in (sel if isinstance(sel, list) else gexits)]
    mixed = len(set(kinds.get(e) for e in ids if kinds.get(e))) > 1
    sp_on = bool((((g.get("checks") or {}).get("speed")) or {}).get("enabled"))
    for side in (when, back or {}):
        for c in side.get("conds") or []:
            if not isinstance(c, dict):
                continue
            if c.get("metric") == "rtt" and c.get("unit") == "ms" and mixed and not isinstance(sel, dict):
                out.warn("ep.warn.rtt_mixed", f + ".when.conds")
            if c.get("metric") == "speed" and not sp_on:
                out.warn("ep.warn.speed_off", f + ".when.conds")


def _validate_probes(out, p):
    if p is None:
        return
    if not isinstance(p, dict):
        out.err("ep.err.type", "probes")
        return
    ob = p.get("off_backoff") or {}
    b, mx, rs = ob.get("base_s", 30), ob.get("max_s", 600), ob.get("reset_after_s", 3600)
    if any(_int(x) is None for x in (b, mx, rs)) or not 5 <= b <= mx <= 3600 or not 60 <= rs <= 86400:
        out.err("ep.err.backoff", "probes.off_backoff")
    w = p.get("window", 10)
    if _int(w) is None or not 3 <= w <= 30:
        out.err("ep.err.window", "probes.window", value=repr(w))
    sp = p.get("speed") or {}
    if _num(sp.get("max_mb", 10)) is None or not 0 < sp.get("max_mb", 10) <= 50:
        out.err("ep.err.speed_mb", "probes.speed.max_mb", value=repr(sp.get("max_mb")))
    if _num(sp.get("max_s", 8)) is None or not 0 < sp.get("max_s", 8) <= 30:
        out.err("ep.err.speed_s", "probes.speed.max_s", value=repr(sp.get("max_s")))
    for k in ("budget_day_mb", "budget_month_gb"):
        if _num(sp.get(k, 1)) is None or sp.get(k, 1) <= 0:
            out.err("ep.err.speed_budget", "probes.speed." + k, value=repr(sp.get(k)))


def validate_exit(pref, lang=None):
    """Errors of the fields of an exit's record ``exit_pref`` (§3.1): manual, manual_why, speed_skip, country, quota.
    -> [{"code", "field", "text"}]."""
    out = _Out(get_lang(lang))
    p = pref if isinstance(pref, dict) else {}
    if p.get("manual") is not None and p.get("manual") not in MANUAL:
        out.err("ep.err.manual", "manual", value=repr(p.get("manual")))
    if p.get("manual_why") is not None and p.get("manual_why") not in MANUAL_WHY:
        out.err("ep.err.manual_why", "manual_why", value=repr(p.get("manual_why")))
    if p.get("manual_why") is not None and p.get("manual") is None:
        out.err("ep.err.manual_why", "manual_why", value=repr(p.get("manual_why")))
    if "speed_skip" in p and not isinstance(p["speed_skip"], bool):
        out.err("ep.err.type", "speed_skip")
    if "group" in p and p["group"] is not None and not (isinstance(p["group"], str) and _GID.match(p["group"])):
        out.err("ep.err.group_id", "group", value=repr(p["group"]))
    c = p.get("country")
    if c is not None:
        if not isinstance(c, dict) or (c.get("expected") is not None and not (isinstance(c["expected"], str) and
                                                                              _ISO.match(c["expected"]))) or \
                any(not (isinstance(x, str) and _ISO.match(x)) for x in c.get("notified") or []):
            out.err("ep.err.country", "country", value=repr(c))
    q = p.get("quota")
    if q is not None:
        if not isinstance(q, dict):
            out.err("ep.err.type", "quota")
        else:
            if q.get("period", "month") not in QUOTA_PERIODS:
                out.err("ep.err.quota_period", "quota.period", value=repr(q.get("period")))
            lim = q.get("limit_gb")
            if quota_gb(q) is None or not QUOTA_GB[0] <= lim <= QUOTA_GB[1] or abs(round(lim * 10) - lim * 10) > 1e-6:
                out.err("ep.err.quota_limit", "quota.limit_gb", value=repr(lim), max="%d" % QUOTA_GB[1])
            rd = q.get("reset_day", 1)
            if _int(rd) is None or not 1 <= rd <= 28:
                out.err("ep.err.quota_reset_day", "quota.reset_day", value=repr(rd))
            wp = q.get("warn_pct", 80)
            if _int(wp) is None or not 50 <= wp <= 99:
                out.err("ep.err.quota_warn", "quota.warn_pct", value=repr(wp))
            act = q.get("action", "remove")
            if act == "off":
                out.err("ep.err.then_off", "quota.action")
            elif act not in QUOTA_ACTIONS:
                out.err("ep.err.quota_action", "quota.action", value=repr(act))
    return out.errors


# ── write ──────────────────────────────────────────────────────────────────

def _store_names(doc):
    """Names equal (names.same) to their default on any language are written ``""`` — only at «main» and the tiers
    (SPEC §3.1 «Имена по умолчанию»); other names — cleaned by names.check."""
    for g in doc.get("groups") or []:
        if not isinstance(g, dict):
            continue
        if g.get("id") == MAIN and (not g.get("name") or _default_name_of(g)):
            g["name"] = ""
        elif g.get("name"):
            g["name"] = names.check(g["name"], "group")
        for i, t in enumerate(g.get("tiers") or []):
            if not isinstance(t, dict):
                continue
            if not t.get("name") or _default_name_of(g, i):
                t["name"] = ""
            else:
                t["name"] = names.check(t["name"], "level")
            t.pop("label", None)
        g.pop("label", None)


def save(st, doc, pool=None, prev=None, lang=None):
    """Validate ``doc`` (KaskadError USAGE, ``data = {"errors": [...], "warnings": [...]}``), bring it to the stored
    form (defaults, names, the preset — «custom» when a field of it changed) and write it into ``st``. The caller
    holds the lock and counts ``rev.note``. ``prev`` — the policy before (None: the one in ``st``). ->
    (doc, warnings)."""
    if prev is None:
        prev = stored(st)
    errors, warnings = validate(doc, pool, prev, lang)
    if errors:
        raise KaskadError("\n".join(e["text"] for e in errors), code=USAGE,
                          data={"errors": errors, "warnings": warnings})
    new = normalize(doc)
    _store_names(new)
    for g in new["groups"]:
        if g.get("preset") != CUSTOM and not matches_preset(g, g.get("preset")):
            g["preset"] = CUSTOM
        for t in g.get("tiers") or []:
            if isinstance(t, dict) and t.get("order") == "auto":
                del t["order"]                      # «auto» is no field: a policy without manual tiers stays as it was
    g = st.get("gateway")
    if not isinstance(g, dict):
        g = st["gateway"] = {}
    g["exit_policy"] = new
    return new, warnings
