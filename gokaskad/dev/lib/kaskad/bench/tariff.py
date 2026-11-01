"""The tariff of the server — only what the health and the price of the schedule take (k3/handoff/SPEC-node-score.md
NS-D34, решение владельца 23.10: the input of the tariff for the score C1 and the comparison with it C2 «за свои деньги»
are gone). The CORE (process/BLOCKS.md): pure functions, no file, no clock, no ``gw``/``panel``; the time comes as a
parameter, the texts are msgs of the catalog ``tariff`` ({"key", "args"}), the reader says them.

Two values, each with its source (``src``): ``user`` — typed in by a person, ``catalog`` — from a catalog of the hoster
(none is built in yet; the rule «port» takes it as it was). The server never guesses them (ethtool: vmxnet3 always says
10 Gbit/s).

The stored document (``gateway.hostmon.tariff`` of state.json, the binding writes it)::

    {"v": 1, "at": unix seconds of the last change, "mid": the machine (the binding),
     "vals": {field: {"v": value, "src": "user" | "catalog"}}}      only the fields that have a value

    FIELDS        type and range (after ``parse_value``)          who takes it
    port_mbit     integer 1–100000 (Mbit/s)                       the health rule «port» (C7: ``gw.model.hostmon`` ->
                                                                  ``gw.hostmon``); its change — a «new base» (NS-S17)
    traffic_gb    number 1–10000000 (GB a month) or "unlimited"   the price of the schedule «% of the tariff traffic»
                                                                  (``host_sched.cost_ctx`` -> ``bench.cost``, B10)

    The fields of the former tariff (cores, memory, disk, price, currency, hoster, name — ``LEGACY``) are read past
    silently: not a value, not «dropped»; the next write leaves them out.

    load(raw)                         -> (doc, dropped)  a stored document checked again at every read; a bad
                                         field is dropped (its name in ``dropped``), never the whole tariff
    parse_value(field, x)             -> (value, None) | (None, error)  one value of a person (a number or a text
                                         of digits, «1,5» too); None / "" — «clear»; another field — ``unknown``
    merge(vals, values, clear, src)   -> (new vals, errors)  a change of a person over the stored values
    rev(vals) / code(vals, new)       -> short hex: the version of the stored values (If-Match of the panel) and
                                         the code of one change (the confirmation of the agent, as T10/B7)
    view(doc)                         -> {"v", "at", "fields": {f: {"v", "src"}}, "rev"} — what a reader shows
    port_mbit(doc) / traffic_limit_gb(doc)   -> the numbers other blocks take (C7, B10)
    epoch_args(old, new)              -> {…} | None — the args of a «new base» (NS-S17) when the port changed or
                                         was typed in first
"""

import hashlib
import json
import math
import re

V = 1
SOURCES = ("user", "catalog")
FIELDS = ("port_mbit", "traffic_gb")
MACHINE = ("port_mbit",)                 # the value of the machine: its change is a new base (NS-S17)
LEGACY = ("vcpu", "ram_gb", "disk_type", "price", "currency", "hoster", "name")   # stored before 23.10: read past
UNLIMITED = "unlimited"
NUM_TEXT_MAX = 24                        # signs of a number typed as a text
# field -> (kind, min, max, decimals); kind: int | traffic (a number or «unlimited»)
SPEC = {
    "port_mbit": ("int", 1, 100000, 0),
    "traffic_gb": ("traffic", 1, 10000000, 2),
}
_NUM = re.compile(r"[0-9]{1,12}([.,][0-9]{1,6})?")
_CODE_LEN = 10


def msg(key, **args):
    return {"key": key, "args": args}


_FIELD_NAME = re.compile(r"[a-z_]{1,16}")


def field_name(f):
    """A field name said back in an error: a name of FIELDS or of its shape, else «?» (no sign of a person's input)."""
    return f if isinstance(f, str) and _FIELD_NAME.fullmatch(f) else "?"


def _err(field, code, **args):
    return {"field": field_name(field), "code": code, "msg": msg("tariff.err." + code, **args)}


# ── one value ──────────────────────────────────────────────────────────────

def _number(x):
    """A number of a person: int/float (not bool, finite) or a text of digits with «.» or «,». -> float | None."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, str):
        t = x.strip()
        if len(t) > NUM_TEXT_MAX or not _NUM.fullmatch(t):
            return None
        return float(t.replace(",", "."))
    return None


def _fmt_range(v):
    return int(v) if float(v) == int(v) else v


def parse_value(field, x):
    """One value of a person -> (value, None) | (None, error) | (None, None) for «clear» (None or an empty text)."""
    if field not in SPEC:
        return None, _err(field, "unknown")
    if x is None or (isinstance(x, str) and not x.strip()):
        return None, None
    kind, lo, hi, dec = SPEC[field]
    if kind == "traffic" and isinstance(x, str) and x.strip().lower() == UNLIMITED:
        return UNLIMITED, None
    n = _number(x)
    if n is None:
        return None, _err(field, "number")
    if kind == "int":
        if n != int(n):
            return None, _err(field, "int")
        n = int(n)
    else:
        n = round(n, dec)
        n = int(n) if n == int(n) else n
    if n < lo or n > hi:
        return None, _err(field, "range", min=_fmt_range(lo), max=_fmt_range(hi))
    return n, None


# ── the stored document ────────────────────────────────────────────────────

def _stored_value(field, x):
    """A value read back from the state: exactly what ``parse_value`` gives (no text of digits, no «,»)."""
    if field not in SPEC or x is None or isinstance(x, bool):
        return None
    if isinstance(x, str) and not (SPEC[field][0] == "traffic" and x == UNLIMITED):
        return None
    v, e = parse_value(field, x)
    return None if e is not None else v


def load(raw):
    """``gateway.hostmon.tariff`` -> (doc, dropped): every field checked again (a .kbk, a hand edit of state.json);
    a bad one — dropped and named, the others stay; a field of the former tariff (``LEGACY``) — read past, not
    named. No tariff — an empty doc."""
    doc = {"v": V, "at": None, "vals": {}}
    dropped = []
    if not isinstance(raw, dict):
        return doc, ([] if raw is None else ["tariff"])
    at = raw.get("at")
    if isinstance(at, int) and not isinstance(at, bool) and 0 < at < 10 ** 10:
        doc["at"] = at
    vals = raw.get("vals")
    if not isinstance(vals, dict):
        return doc, ([] if vals is None else ["vals"])
    for f in sorted(vals, key=str):
        if f in LEGACY:
            continue                                      # NS-D34: no longer a field, not an error either
        item = vals[f]
        if f not in SPEC:
            dropped.append(field_name(f))
            continue
        if not isinstance(item, dict) or item.get("src") not in SOURCES:
            dropped.append(f)
            continue
        v = _stored_value(f, item.get("v"))
        if v is None:
            dropped.append(f)
            continue
        doc["vals"][f] = {"v": v, "src": item["src"]}
    return doc, dropped


def merge(vals, values, clear=(), src="user"):
    """A change of a person over ``vals`` (the stored {field: {"v", "src"}}): ``values`` {field: value} — set (None
    or "" — clear), ``clear`` — fields to clear («all» — every field). -> (new vals, errors); with errors the
    new vals are the old ones. ``src`` of a typed value is always ``user`` (a catalog fills values itself)."""
    errors = []
    new = dict((f, dict(x)) for f, x in (vals or {}).items())
    values = values if isinstance(values, dict) else {}
    clear = list(clear or ())
    if "all" in clear:
        clear = list(FIELDS)
    for f in clear:
        if f not in SPEC:
            errors.append(_err(f, "unknown"))
        else:
            new.pop(f, None)
    for f in sorted(values, key=str):
        v, e = parse_value(f, values[f])
        if e is not None:
            errors.append(dict(e, field=field_name(f)))
        elif v is None:
            new.pop(f, None)
        else:
            new[f] = {"v": v, "src": src}
    if errors:
        return dict((f, dict(x)) for f, x in (vals or {}).items()), errors
    return new, []


def _canon(vals):
    return json.dumps(dict((f, [x["v"], x["src"]]) for f, x in sorted((vals or {}).items())),
                      sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def rev(vals):
    """The version of the stored values (the panel's If-Match, two windows): 10 hex signs."""
    return hashlib.sha256(("rev:" + _canon(vals)).encode()).hexdigest()[:_CODE_LEN]


def code(vals, new):
    """The code of ONE change — from these stored values to these new ones (the agent shows the change to the
    person and repeats the code; another change or a change meanwhile — another code). Not a secret."""
    return hashlib.sha256(("code:" + _canon(vals) + ">" + _canon(new)).encode()).hexdigest()[:_CODE_LEN]


def changed(vals, new):
    """The fields whose value or source differs, in the order of FIELDS."""
    a, b = vals or {}, new or {}
    return [f for f in FIELDS if a.get(f) != b.get(f)]


def epoch_args(vals, new):
    """NS-S17: the port typed in or changed — the args of the file ``epoch-<ts>-tariff.json`` ({"port_mbit": n,
    "fields": 1}; ``bench.spool.args_strict`` takes them); else None (the traffic does not start a new base). A value
    cleared is not a change of the machine (REV-ns-c1 П-4, решение координатора 19.10: «unknown» is not «changed», as
    the rule of B6); only the value counts, not its source."""
    a, b = vals or {}, new or {}
    if not any(b.get(f) is not None and (a.get(f) or {}).get("v") != b[f]["v"] for f in MACHINE):
        return None
    out = {}
    for f in MACHINE:
        x = b.get(f)
        if x is not None:
            out[f] = x["v"]
    out["fields"] = len(out)
    return out


# ── what readers and other blocks take ─────────────────────────────────────

def _value(doc, field):
    """The value of a field when its source counts (typed in or a catalog), else None."""
    x = ((doc or {}).get("vals") or {}).get(field)
    if isinstance(x, dict) and x.get("src") in SOURCES and x.get("v") is not None:
        return x["v"]
    return None


def port_mbit(doc):
    """The port of the tariff (Mbit/s) for the health rule «port» (C7); unknown — None."""
    return _value(doc, "port_mbit")


def traffic_limit_gb(doc):
    """The traffic of the tariff a month (GB) for ``bench.cost`` (``tariff_traffic_gb``); unlimited or unknown —
    None."""
    v = _value(doc, "traffic_gb")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def view(doc):
    """What a reader shows: every field with its value and source (None, None — not entered) and the version."""
    vals = (doc or {}).get("vals") or {}
    fields = {}
    for f in FIELDS:
        x = vals.get(f)
        fields[f] = {"v": x["v"], "src": x["src"]} if isinstance(x, dict) else {"v": None, "src": None}
    return {"v": V, "at": (doc or {}).get("at"), "fields": fields, "rev": rev(vals)}
