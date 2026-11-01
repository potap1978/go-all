"""The consent «скрипт не используется на территории РФ» for «Россия напрямую» (docs/k3/SPEC-stage1-rf-mark-builtin.md
§3–§4, task R3): one check of the core and the status every surface reads.

Keys in ``gateway`` (not in ``gateway.rules``: ``model.set_rules_conf`` writes only its three keys there)::

    "rf_consent": {"at", "via": panel|scheme|ssh|cli|mcp|setup|env, "who", "orgs": [...], "registry", "lang"}
    "rf_legacy":  {"at", "from": "gen1", "by": "<version of the code that made the mark>"}
    "rf_gate": 1                      # «the migration is done» — only on an UPDATED node (``gen 1``)

``check(old, new, rf, refuse)`` is the ONLY check of a write: ``gw/ops._change`` calls it right after ``mutate`` and
before anything is applied (nft, xray, dnsmasq) or saved; ``backup`` calls it with ``refuse=False``. The second line of
defence is ``allowed()``: «Россия напрямую» applies only with it (``rules.apply.profile``, R2).

A new node (``gen 2``, ``state.born_new``) has ``all_exit`` as the default of the node (``model.rules_conf``) and is
allowed only by ``rf_consent``. An updated node (``gen 1``) without ``rf_gate`` is allowed while ``ru_direct`` is
selected (it worked before the update — mode ``legacy``); ``migrate`` (``kaskadctl update units``) and the first
``check`` write ``rf_gate`` and, when ``ru_direct`` was selected, ``rf_legacy``: from then on «legacy» is that record,
not the absence of a key.

What is marked in the built-in groups (``rfmark.scan`` runs a process for the ``regexp:`` entries) is counted on a
change of the lists (``rules.update``) or of the registry (``migrate`` after an update of the package) and kept in a
file next to the lists (``SCAN_FILE`` in ``store.root()``), never in ``state.json``: the status, the panel and
``check`` (under the state lock) only read it. A result of other lists or of another registry is shown as it is with
``stale: true``.
"""

import json
import os
import re

from .. import __version__, util
from .. import state as kstate
from ..errors import CONFLICT, KaskadError, LockedError
from ..gw import model
from ..i18n import get_lang, tr
from . import rfmark, store

RU = "ru_direct"
VIAS = ("panel", "scheme", "ssh", "cli", "mcp", "setup", "env")
SCAN_FILE = "rf_scan.json"
SCAN_SCHEMA = 1
MARKED_MAX = 200                     # rows of one group in the status (and in the kept file); the count is whole
KEYS = ("rf_consent", "rf_legacy", "rf_gate")


def _gw(st):
    g = (st or {}).get("gateway") if isinstance(st, dict) else None
    return g if isinstance(g, dict) else {}


def _points(st):
    """Where ``ru_direct`` is chosen explicitly: {("default",), ("user", id), ("device", id)}. The default of the node
    counts as chosen when the effective value (``rules_conf``) is ``ru_direct`` — on an updated node that is the
    default of 3.3.x even without a key."""
    out = set()
    if model.rules_conf(st)["default"] == RU:
        out.add(("default",))
    g = model.peek(st)
    for kind, objs in (("user", g.get("users") or []), ("device", g.get("devices") or [])):
        for o in objs:
            if isinstance(o, dict) and o.get("rules") == RU:
                out.add((kind, o.get("id")))
    return out


def selected(st):
    """``ru_direct`` is chosen somewhere: the node, a person or a device."""
    return bool(_points(st))


def _stamp(v):
    return isinstance(v, str) and bool(v.strip())


def _consent(st):
    """``rf_consent`` of the FORM ``grant`` writes, else None (SEC-s1-r2 З1): a time ``at``, ``via`` of VIAS and a list of
    organisations. Anything else — ``{}``, ``{"x": 1}``, ``{"at": null}`` — is no consent: a failure goes to the block."""
    c = _gw(st).get("rf_consent")
    if not isinstance(c, dict) or not _stamp(c.get("at")) or c.get("via") not in VIAS:
        return None
    orgs = c.get("orgs")
    if not isinstance(orgs, list) or not all(isinstance(o, str) for o in orgs):
        return None
    return c


def _legacy(st):
    """``rf_legacy`` of the FORM ``migrate`` writes (``at``, ``from: gen1``) and only on an UPDATED node: a new one
    (``gen 2``, a missing or broken ``gen``) never worked as 3.3.x (§3.6), so the mark there is no permission."""
    if kstate.born_new(st):
        return None
    c = _gw(st).get("rf_legacy")
    if not isinstance(c, dict) or not _stamp(c.get("at")) or c.get("from") != "gen1":
        return None
    return c


def _unmigrated(st):
    return not kstate.born_new(st) and not _gw(st).get("rf_gate")


def allowed(st):
    """«Россия напрямую» may apply: a consent; or a mark ``rf_legacy``; or an updated node not yet migrated with
    ``ru_direct`` chosen (it worked before the update)."""
    if _consent(st) or _legacy(st):
        return True
    return _unmigrated(st) and selected(st)


def mode(st):
    """full | legacy | need | none (§4)."""
    if not selected(st):
        return "none"
    if _consent(st):
        return "full"
    if allowed(st):
        return "legacy"
    return "need"


# ── the kept result of the scan ────────────────────────────────────────────

def scan_path():
    return os.path.join(store.root(), SCAN_FILE)


def load_scan():
    """The kept result of ``rfmark.scan`` or None (none yet, or unreadable)."""
    raw = util.read_text(scan_path())
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(doc, dict) or doc.get("schema") != SCAN_SCHEMA or not isinstance(doc.get("marked"), dict):
        return None
    return doc


def _registry():
    """The registry of the package, or None when it is broken (``rules.rf.bad`` is said by the status)."""
    try:
        return rfmark.default()
    except KaskadError:
        return None


def _dat_of(v):
    """sha256 of the geosite of a version of the lists, from its meta.json (no hashing; REV-SPEC-stage1-fix-21 П11)."""
    return v.sha256(store.GEOSITE) if v is not None else None


def stale(doc, v=None, reg=None):
    """The kept result is of other lists or of another registry."""
    if doc is None:
        return True
    v = store.active() if v is None else v
    reg = _registry() if reg is None else reg
    return doc.get("dat") != _dat_of(v) or doc.get("registry") != (reg.version if reg is not None else None)


def refresh_scan(v=None, force=False, locked=False):
    """Count the marks of the lists ``v`` (the active ones by default) and keep them, when the kept result is missing or
    stale (``force`` — always). Runs a process for the ``regexp:`` entries — never under the state lock. The file is
    written atomically under ``store.update_lock`` (``locked`` — the caller holds it: ``rules.update``); the lock busy
    — nothing (the update that holds it counts). -> {"ok", "action": counted|kept|no_lists|busy|failed, "error"?}.
    A failure (a broken registry, an unreadable file) is said, never raised: the lists stay, the old result stays."""
    def run():
        cur = store.active() if v is None else v
        if cur is None or not cur.file(store.GEOSITE):
            return {"ok": True, "action": "no_lists"}
        reg = rfmark.default()
        old = load_scan()
        if not force and old is not None and not stale(old, cur, reg):
            return {"ok": True, "action": "kept"}
        from . import dat
        cats = dat.geosite(cur.read(store.GEOSITE), [c for _, cs in rfmark.GROUPS for c in cs])
        stats = {}
        marked = rfmark.scan(cats, reg=reg, stats=stats)
        doc = {"schema": SCAN_SCHEMA, "at": util.now_iso(), "version": cur.id, "dat": _dat_of(cur),
               "registry": reg.version, "stats": stats,
               "marked": dict((g, [list(r) for r in rows[:MARKED_MAX]]) for g, rows in marked.items()),
               "counts": dict((g, len(rows)) for g, rows in marked.items()),
               "orgs": dict((g, _uniq(r[3] for r in rows)) for g, rows in marked.items())}
        util.ensure_dir(store.root(), 0o700)
        util.atomic_write(scan_path(), util.json_text(doc), 0o600)
        return {"ok": True, "action": "counted", "counts": doc["counts"]}
    try:
        if locked:
            return run()
        if (store.active() if v is None else v) is None:
            return {"ok": True, "action": "no_lists"}          # no lists — no lock file made either
        with store.update_lock():
            return run()
    except LockedError:
        return {"ok": True, "action": "busy"}
    except (KaskadError, OSError, ValueError) as e:
        return {"ok": False, "action": "failed", "error": util.redact(str(e))}


def _uniq(items):
    out = []
    for x in items:
        if x not in out:
            out.append(x)
    return out


def marked_orgs(doc=None, group="force"):
    """Organisations marked now in a built-in group, by the kept result (in the order found)."""
    doc = load_scan() if doc is None else doc
    if not doc:
        return []
    got = (doc.get("orgs") or {}).get(group)
    if isinstance(got, list):
        return list(got)
    return _uniq(r[3] for r in (doc.get("marked") or {}).get(group) or [] if isinstance(r, list) and len(r) > 3)


def block_orgs(st=None, doc=None):
    """The organisations the block names (§3.3): marked now in «всегда через выход»; none marked (no lists, no result,
    nothing found) — every organisation of the registry with domains. Reads the kept result only."""
    got = marked_orgs(doc)
    if got:
        return got
    reg = _registry()
    return [o["id"] for o in reg.orgs if o.get("domains")] if reg is not None else []


ORG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def shown_orgs(v):
    """The ids of a shown block as a surface sends them (a list of codes) -> the list, or None when it is not that."""
    if not isinstance(v, list) or len(v) > 32 or not all(isinstance(x, str) and ORG_ID_RE.match(x) for x in v):
        return None
    return list(v)


def _names(orgs, lang=None):
    reg = _registry()
    return reg.names(orgs, lang) if reg is not None else ", ".join(orgs)


def block_text(orgs, lang=None):
    return tr("rules.rf.block", lang, names=_names(orgs, lang))


def need_data(orgs, lang=None):
    """``data`` of a refusal: what a surface shows — the block with the names and the text of the tick."""
    return {"need": "rf_consent", "orgs": list(orgs), "names": _names(orgs, lang), "block": block_text(orgs, lang),
            "consent": rfmark.consent_text(lang)}


# ── the check ──────────────────────────────────────────────────────────────

def need(old, new):
    """A ``ru_direct`` chosen in ``new`` that ``old`` did not have (the node, a person, a device) and no consent in
    ``new``: {"points": [...], "orgs": block_orgs} — else None. «legacy» does not cover a new choice (§3.1)."""
    if _consent(new):
        return None
    fresh = _points(new) - _points(old)
    if not fresh:
        return None
    return {"points": sorted([list(p) for p in fresh]), "orgs": block_orgs(new)}


def _say(line, log):
    """A line of the journal now (``log`` None) or into ``log`` — said by the caller once the state is saved."""
    if log is None:
        util.log_action(line)
    else:
        log.append(line)


def said(log):
    """The lines kept by ``check(..., log=[])`` into the journal — after ``state.save``."""
    for line in log or ():
        util.log_action(line)


def migrate(st, was=None, log=None):
    """The mark of an updated node (``gen 1`` without ``rf_gate``), in place: ``rf_gate: 1`` and, when ``ru_direct`` is
    chosen in ``was`` (``st`` by default), ``rf_legacy``. Idempotent; nothing on ``gen 2``. -> True when it wrote."""
    if not _unmigrated(st):
        return False
    g = st.get("gateway")
    if not isinstance(g, dict):
        g = st["gateway"] = {}
    g["rf_gate"] = 1
    legacy = selected(st if was is None else was)
    if legacy:
        g["rf_legacy"] = {"at": util.now_iso(), "from": "gen1", "by": __version__}
    _say("RULES rf-gate: migrated%s" % (", legacy" if legacy else ""), log)
    return True


def grant(new, via, who, lang, orgs, log=None):
    """Write ``rf_consent`` (the organisations already covered stay, the shown ones are added); ``rf_legacy`` goes."""
    g = new.setdefault("gateway", {})
    was = (_consent(new) or {}).get("orgs") or []
    reg = _registry()
    g["rf_consent"] = {"at": util.now_iso(), "via": via if via in VIAS else "cli", "who": who or None,
                       "orgs": _uniq(list(was) + list(orgs)),
                       "registry": reg.version if reg is not None else None, "lang": get_lang(lang)}
    g.pop("rf_legacy", None)
    _say("RULES rf-consent: via=%s orgs=%s" % (g["rf_consent"]["via"], ",".join(g["rf_consent"]["orgs"])), log)
    return g["rf_consent"]


def _reset(new, log=None):
    g = _gw(new)
    gone = [k for k in ("rf_consent", "rf_legacy") if k in g]
    for k in gone:
        g.pop(k, None)
    if gone:
        _say("RULES rf-consent: reset", log)
    return bool(gone)


def refuse_need(orgs, lang=None, key="rules.rf.need"):
    """KaskadError CONFLICT with the block and the tick (``data.need = "rf_consent"``)."""
    data = need_data(orgs, lang)
    raise KaskadError(tr(key, lang, block=data["block"], consent=data["consent"]), code=CONFLICT, data=data)


def check(old, new, rf=None, refuse=True, log=None):
    """The one check of a write of the state (§3.5) — ``new`` is changed in place, nothing else is touched:

    * an updated node not yet migrated — ``migrate`` first (``rf_legacy`` by ``old``);
    * ``ru_direct`` chosen nowhere — the consent and the legacy mark go (§3.4);
    * ``rf.confirm`` — the consent is written (``orgs`` — what the block names now); the client showed a narrower block
      (``rf.orgs``) — a refusal with the new block; ``rf.must_show`` without ``rf.orgs`` — a refusal with the block,
      unless the consent in force already covers the block: then the tick is spare and ignored (REV-s1-rf-2 П-4);
      the block cannot be named (the registry is broken, no organisation) — a refusal without the tick, no consent;
    * a new ``ru_direct`` without a consent — a refusal (KaskadError CONFLICT, ``data.need = "rf_consent"``) or, with
      ``refuse=False`` (an import), the mode ``need``: chosen, not applied.

    ``rf`` — {"confirm", "via", "who", "lang", "orgs", "must_show"} or None. ``log`` — a list: the lines of the journal
    are kept there for ``said`` after ``state.save`` (a write that fails later says nothing); None — said now. A refusal
    says nothing. -> {"granted"|"need"|"reset": ...} or None."""
    lines = []
    got = _check(old, new, rf or {}, refuse, lines)
    if log is None:
        said(lines)
    else:
        log.extend(lines)
    return got


def _check(old, new, rf, refuse, log):
    lang = rf.get("lang")
    if _unmigrated(new):
        migrate(new, was=old, log=log)
    if not selected(new):
        return {"reset": True} if _reset(new, log) else None
    if rf.get("confirm"):
        now = block_orgs(new)
        if not now or _registry() is None:          # К2-9: the block cannot be named (a broken registry) — a consent
            if refuse:                               # on it would be a consent on nothing shown, at every surface
                util.log_action("RULES rf-consent: refused, the registry %s is unreadable" % rfmark.PATH)
                raise KaskadError(tr("rules.rf.unread", lang), code=CONFLICT,
                                  data={"error": "registry"})
            return _need(old, new, refuse, lang)
        shown = rf.get("orgs")
        if shown is None and rf.get("must_show"):        # MCP, the wizard: what was shown is not said (SEC-s1-r67 №1)
            if set(now) <= set((_consent(new) or {}).get("orgs") or []):
                return _need(old, new, refuse, lang)    # the consent in force covers the block: the tick is spare
            if refuse:
                refuse_need(now, lang)
            return {"need": {"orgs": now}}
        if shown is not None and not set(now) <= set(shown):
            if refuse:
                refuse_need(now, lang, "rules.rf.need_changed")
            return {"need": {"orgs": now}}
        return {"granted": grant(new, rf.get("via") or "cli", rf.get("who"), lang, now, log)}
    return _need(old, new, refuse, lang)


def _need(old, new, refuse, lang):
    n = need(old, new)
    if n is None:
        return None
    if refuse:
        refuse_need(n["orgs"], lang)
    return {"need": n}


def migrate_node():
    """``kaskadctl update units`` (after an update, at the start of the bot): the mark of an updated node under the
    state lock, then the marks of the lists counted again when the registry or the lists changed (outside the state
    lock). Never raises: a failure is a line of the result. -> {"ok", "migrated", "scan"}."""
    out = {"ok": True, "migrated": False}
    try:
        with kstate.locked():
            st = kstate.load()
            if _unmigrated(st):
                lines = []
                migrate(st, log=lines)
                kstate.save(st)
                said(lines)
                out["migrated"] = True
    except (KaskadError, OSError, ValueError) as e:
        out.update(ok=False, error=util.redact(str(e)))
    out["scan"] = refresh_scan()
    return out


# ── the status ─────────────────────────────────────────────────────────────

def _rows(rows):
    out = []
    for r in rows or []:
        if isinstance(r, list) and len(r) >= 4:
            out.append({"code": r[0], "type": r[1], "value": r[2], "org": r[3]})
    return out


def state(st=None, v=None, lang=None):
    """``rf`` of ``rules status`` and ``GET /api/rules`` (§4). Reads the kept result: no process, no lock."""
    st = kstate.load() if st is None else st
    v = store.active() if v is None else v
    reg = _registry()
    doc = load_scan()
    c, leg = _consent(st), _legacy(st)
    m = mode(st)
    orgs = block_orgs(st, doc)
    covered = set((c or {}).get("orgs") or [])
    out = {"required": not c, "mode": m,
           "consent": {"at": c.get("at"), "via": c.get("via"), "orgs": list(c.get("orgs") or [])} if c else None,
           "legacy": {"at": leg.get("at"), "from": leg.get("from")} if leg else (
               {"at": None, "from": "gen1"} if m == "legacy" else None),
           "orgs": [], "marked": {"force": [], "direct": [], "block": []}, "marked_n": {"force": 0, "direct": 0, "block": 0},
           "uncovered": [o for o in marked_orgs(doc) if o not in covered] if c else [],
           "registry": reg.version if reg is not None else None, "scan": None}
    if reg is None:
        out["error"] = tr("rules.rf.bad", lang)
    for oid in orgs:
        o = reg.by_id.get(oid) if reg is not None else None
        out["orgs"].append({"id": oid, "name": reg.names([oid], lang) if reg is not None else oid,
                            "since": (o or {}).get("since")})
    if doc:
        for g in ("force", "direct", "block"):
            out["marked"][g] = _rows((doc.get("marked") or {}).get(g))
            out["marked_n"][g] = int((doc.get("counts") or {}).get(g) or len(out["marked"][g]))
        s = dict(doc.get("stats") or {})
        s.update({"at": doc.get("at"), "dat": doc.get("dat"), "registry": doc.get("registry"),
                  "stale": stale(doc, v, reg)})
        out["scan"] = s
    return out


def notes(rf, lang=None):
    """Lines of the status for people (CLI, SSH): the mode, the incomplete mark (§7 п. 4, п. 6)."""
    lines = []
    names = ", ".join(o["name"] for o in rf.get("orgs") or [])
    m = rf.get("mode")
    if m == "full":
        lines.append(tr("rules.rf.mode.full", lang, names=_names((rf.get("consent") or {}).get("orgs") or [], lang)))
        if rf.get("uncovered"):
            lines.append(tr("rules.rf.uncovered", lang, names=_names(rf["uncovered"], lang)))
    elif m == "legacy":
        lines.append(tr("rules.rf.legacy", lang, names=names))
    elif m == "need":
        lines.append(tr("rules.rf.need_mode", lang))
    s = rf.get("scan")
    if s:
        if s.get("regexp_unchecked"):
            by = s.get("regexp_unchecked_by") or {}
            lines.append(tr("rules.rf.unchecked", lang, n=s["regexp_unchecked"]) + " (" +
                         tr("rules.rf.unchecked_by", lang, **dict((k, by.get(k, 0)) for k in rfmark.UNCHECKED)) + ")")
        if s.get("unknown"):
            lines.append(tr("rules.rf.unknown", lang, n=s["unknown"]))
        if s.get("stale"):
            lines.append(tr("rules.rf.stale", lang))
    return lines
