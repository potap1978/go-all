"""Renames of the gateway (docs/k3/SPEC-stage1-rename.md §3; T13 N2): people, devices, exits (own and of the
subscriptions), inbounds (their label) and subscriptions of exits. Forwards — ``forwards.rename``, monitored servers —
``bot.aliases.rename``.

    rename(runner, what, ref, name, allow_duplicate=False, via="cli", lang=None) -> dict
        what: user | device | exit | inbound | exitsub; ``name`` "" — the reset (not for user and device)
        -> {"ok", "what", "id", "old", "name", "shown", "duplicates", "note"}
    inbound_shown(st, kind, lang=None) -> «AmneziaWG 2.0 — для роутеров» (the title alone without a label)
    inbound_label(st, kind) -> the label of an inbound ("" — none; a removed one keeps it in ``removed_inbounds``)
    sub_label(sub) -> the label of a subscription of exits ("" — none)

A name changes no config of a daemon (WireGuard, AmneziaWG, xray, nft, PPP, ocserv): a rename is a light write of the
state — under ``state.transaction`` (subscriptions — the lock of exitsubs), without ``gsys.hold`` and without
``gw apply`` — then ``snapshot.bump``. What never changes: the ids, ``login`` of a device (the ``email`` of xray, the
login of SOCKS5/PPP/ocserv), the keys and passwords, the tokens of /p/, the line of the pool and its ``#fragment``,
``exit:<id>`` in the routes, the layout of the scheme. The version of the policy ``rev`` does not move (names are
outside its fingerprint, gw/rev.py); the ``ver`` of a route neither (only the route is in it).

Refusals are ``policy_ops.Refused`` (the word of the API and the HTTP status): «field» 400 USAGE — the name did not
pass ``names.check``; «user_exists» 409 CONFLICT — the name of another person (no override: a reference by name in
the CLI and the bot would become ambiguous); «confirm_duplicate» 409 CONFLICT with ``data.need = "confirm_duplicate"``
and ``data.duplicates`` — the same name elsewhere, saved with ``allow_duplicate``; «gone» 404 — nothing under ``ref``.
The journal: ``RENAME <what> <id>: «old» -> «new» (via=…)`` — a name is no secret, a line break never passes the check.
"""

import copy

from .. import names, snapshot, state, util
from ..errors import CONFLICT, ERROR, USAGE, KaskadError
from ..i18n import tr
from . import model

WHATS = ("user", "device", "exit", "inbound", "exitsub")
VIA = ("panel", "ssh", "cli", "mcp", "bot")
_NAMES_WHAT = {"user": "user", "device": "device", "exit": "exit", "inbound": "inbound", "exitsub": "sub"}
SUB_LOCK_WAIT = 10                  # s: a refresh of the subscriptions holds their lock — «занято», not a long wait
_WG = ("wg", "awg1", "awg2", "awg3")
_PROFILE = ("openvpn", "ikev2", "l2tp", "pptp", "sstp", "ocserv", "socks5")


def _pops():
    from . import policy_ops
    return policy_ops


def _field(e):
    P = _pops()
    return P.Refused(str(e), "field", 400, USAGE, dict(getattr(e, "data", None) or {}, field="name"))


def _check(what, name, lang=None, reset=False):
    """The clean name (``names.check``) — or "" for a reset when ``reset`` is allowed and the value is empty or only
    spaces (a «\n» or a «\t» alone is no reset: refused as the check says). Refusal — 400 «field»."""
    try:
        return names.check(name, _NAMES_WHAT[what], lang)
    except KaskadError as e:
        if reset and (getattr(e, "data", None) or {}).get("reason") == "empty":
            return ""
        raise _field(e)


def _dup(items, clean, self_id, allow, conflict_text=None):
    """[id] of the same name (``names.duplicates``); a duplicate without ``allow`` — 409 «confirm_duplicate»."""
    dup = names.duplicates(items, clean, self_id) if clean else []
    if dup and not allow:
        P = _pops()
        raise P.Refused(conflict_text or tr("gw_policy_ops.confirm_duplicate", name=clean), "confirm_duplicate", 409,
                        CONFLICT, {"need": "confirm_duplicate", "duplicates": dup})
    return dup


def _gone(what, ref):
    P = _pops()
    key = {"user": "gw_model.err.no_user", "device": "gw_model.err.no_device", "exit": "gw_ops.exit.gone",
           "inbound": "gw_rename.gone.inbound", "exitsub": "gw_rename.gone.sub"}[what]
    return P.Refused(tr(key, ref=ref), "gone", 404, ERROR, {"gone": True, what: ref})


def _log(what, oid, old, new, via):
    via = via if via in VIA else "cli"
    util.log_action("RENAME %s %s: «%s» -> «%s» (via=%s)" % (what, oid, old or "", new or "", via))


# ── labels shown ────────────────────────────────────────────────────────────

def inbound_label(st, kind):
    rec = model.inbound(st, kind) or model.kept_inbound(st, kind) or {}
    v = rec.get("label")
    return v if isinstance(v, str) else ""


def _inbound_title(kind):
    from . import registry
    ad = registry.get(kind)
    return (getattr(ad, "title", "") if ad is not None else "") or kind


def inbound_shown(st, kind, lang=None):
    """«<title> — <label>» or the title alone: ONLY for showing (the panel, the scheme, the SSH menu, the bot, the card
    of /p/). Never in what is issued (issue_os.TITLES, app_label, conf_filename, vless://#…, vpn://, .ovpn) — that
    would change without an apply and part from what people already have (SPEC §3.1)."""
    title = _inbound_title(kind)
    lab = inbound_label(st, kind)
    return tr("gw_rename.inbound_shown", lang, title=title, label=lab) if lab else title


def sub_label(sub):
    v = (sub or {}).get("label")
    return v if isinstance(v, str) else ""


# ── what people see after it (§5) ─────────────────────────────────────────

def note(what, st=None, obj=None, lang=None):
    """The hint of §5 by the kind and the accesses: one text, the parts joined."""
    if what == "device":
        acc = set((obj or {}).get("access") or [])
        groups = [(k, kinds) for k, kinds in (("wg", _WG), ("sub", model.SUB_KINDS), ("profile", _PROFILE))
                  if acc & set(kinds)]
        parts = [tr("gw_rename.note." + k, lang) for k, _ in groups]
        if len(parts) > 1:      # КТ3 П-2: several kinds — each hint says whose it is, never two «names» side by side
            parts = [tr("gw_rename.note.for", lang, kinds=", ".join(_inbound_title(x) for x in kinds if x in acc),
                        text=t) for (_, kinds), t in zip(groups, parts)]
        return " ".join(parts) or tr("gw_rename.note.device", lang)
    if what == "user":
        parts = [tr("gw_rename.note.user", lang)]
        devs = model.devices(st, user=(obj or {}).get("id")) if st is not None else []
        if any(set(d.get("access") or []) & set(model.SUB_KINDS) for d in devs):
            parts.append(tr("gw_rename.note.sub", lang))
        return " ".join(parts)
    if what in ("inbound", "forward"):              # КТ3 П-1: a forward has no key; an inbound — its settings
        return tr("gw_rename.note." + what, lang)
    return tr("gw_rename.note.same", lang)


# ── the renames ────────────────────────────────────────────────────────────

def _user(st, ref, clean, allow, lang):
    u = model.find_user(st, ref)
    if u is None:
        raise _gone("user", ref)
    dup = names.duplicates([(x.get("id"), x.get("name")) for x in model.users(st)], clean, u.get("id"))
    if dup:
        P = _pops()
        raise P.Refused(tr("gw_model.err.user_exists", name=clean), "user_exists", 409, CONFLICT,
                        {"duplicates": dup})
    old = u.get("name") or ""
    u["name"] = clean
    return u["id"], old, clean, [], note("user", st, u, lang)


def _device(st, ref, clean, allow, lang):
    d = model.find_device(st, ref)
    if d is None:
        raise _gone("device", ref)
    mine = [(x.get("id"), x.get("name")) for x in model.devices(st, user=d.get("user"))]
    dup = _dup(mine, clean, d.get("id"), allow)
    old = d.get("name") or ""
    d["name"] = clean
    return d["id"], old, clean, dup, note("device", st, d, lang)


def _exit(st, eid, clean, allow, lang):
    from . import exits
    P = _pops()
    if P._is_dup(eid):
        raise KaskadError(tr("panel.api.dupExit"), code=CONFLICT, data={"exit": str(eid).rsplit("-", 1)[0]})
    try:
        pool = exits.pool(None, roles=("abroad", "ru"))
    except KaskadError:
        pool = []
    if eid not in P.pool_ids():
        raise _gone("exit", eid)
    items = [(e["id"], exits.label(st, e)) for e in pool if e.get("id") != eid]
    dup = _dup(items, clean, eid, allow)
    old = model.exit_pref(st, eid)["name"]
    P._set_exit(st, eid, name=clean)
    row = next((e for e in pool if e.get("id") == eid), None)
    shown = exits.label(st, row) if row is not None else (clean or eid)
    return eid, old, shown, dup, note("exit")


def checked_exit_name(eid, name, allow_duplicate=False, lang=None):
    """The name of an exit written WITH its switch (``ops.exit_set`` with ``on``): checked here the same way as a
    rename (``names.check``, the reset, duplicates) on a read of the state; the write itself goes with the apply."""
    clean = _check("exit", name, lang, reset=True)
    probe = copy.deepcopy(state.load())
    _exit(probe, eid, clean, allow_duplicate, lang)
    return clean


def _inbound(st, kind, clean, allow, lang):
    if kind not in model.INBOUND_KINDS:
        raise _gone("inbound", kind)
    rec = model.inbound(st, kind)
    if rec is None:
        rec = model.kept_inbound(st, kind)
        if rec is None:
            raise _gone("inbound", kind)
        rec = model.gw(st)[model.KEPT][kind]
    items = [(k, inbound_label(st, k)) for k in model.INBOUND_KINDS if k != kind and inbound_label(st, k)]
    dup = _dup(items, clean, kind, allow)
    old = rec.get("label") or ""
    if clean:
        rec["label"] = clean
    else:
        rec.pop("label", None)
    return kind, old, inbound_shown(st, kind, lang), dup, note("inbound")       # КТ3 П-1: «настройки входа»


def find_sub(doc, ref):
    """A subscription of exits by its id, its number, its domain, then its label (the same name by ``names.same``);
    two by one label — refused with the list (USAGE). -> the record."""
    from . import exitsubs
    ref = str(ref or "").strip()
    for s in doc["subs"]:
        if s["id"] == ref or (ref.isdigit() and int(ref) == s.get("n")) or \
                (exitsubs.domain_of(s) and exitsubs.domain_of(s) == ref.lower()):
            return s
    found = [s for s in doc["subs"] if sub_label(s) and names.same(sub_label(s), ref)]
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise KaskadError(tr("gw_rename.sub_ambiguous", ref=ref, ids=", ".join(s["id"] for s in found)),
                          code=USAGE, data={"ambiguous": [s["id"] for s in found]})
    return None


def _exitsub(ref, clean, allow, lang):
    from . import exitsubs
    with exitsubs._locked(SUB_LOCK_WAIT):
        doc = exitsubs.load()
        if doc.get("broken"):
            raise KaskadError(tr("gw_rename.subs_broken"), code=CONFLICT)
        sub = find_sub(doc, ref)
        if sub is None:
            raise _gone("exitsub", ref)
        items = [(s["id"], exitsubs.name_of(s)) for s in doc["subs"] if s["id"] != sub["id"]]
        dup = _dup(items, clean, sub["id"], allow)
        old = sub_label(sub)
        if clean:
            sub["label"] = clean
        else:
            sub.pop("label", None)
        exitsubs.save(doc)
        shown = exitsubs.name_of(sub)
    return sub["id"], old, shown, dup, note("exitsub")


def rename(runner, what, ref, name, allow_duplicate=False, via="cli", lang=None):
    """See the module. ``runner`` is not used (no command runs) — kept for the calling convention of ``ops``."""
    if what not in WHATS:
        raise KaskadError(tr("gw_rename.bad_what", what=repr(what), whats=", ".join(WHATS)), code=USAGE)
    clean = _check(what, name, lang, reset=what not in ("user", "device"))
    extra = {}
    if what == "exitsub":
        oid, old, shown, dup, hint = _exitsub(ref, clean, allow_duplicate, lang)
    else:
        with state.transaction() as st:
            fn = {"user": _user, "device": _device, "exit": _exit, "inbound": _inbound}[what]
            oid, old, shown, dup, hint = fn(st, ref, clean, allow_duplicate, lang)
            if what == "exit":
                from . import rev as R
                # the stored record, as ``model.set_exit`` returns it (the answer of PUT /api/gw/exits/<id> before T13)
                extra = {"rev": R.note(st), "pref": copy.deepcopy(model.peek(st)["exits"].get(oid) or {})}
    snapshot.bump()
    if old != clean:
        _log(what, oid, old, clean, via)
    out = {"ok": True, "what": what, "id": oid, "old": old, "name": clean, "shown": shown, "duplicates": dup,
           "note": hint}
    out.update(extra)
    return out


def said(res, lang=None):
    """The lines for people of a rename result (CLI, SSH menu): what is saved, the duplicates kept, the hint of §5."""
    if res.get("name") or res.get("what") in ("user", "device"):
        lines = [tr("gw_rename.done", lang, shown=res.get("shown") or res.get("name"), id=res.get("id"))]
    else:
        lines = [tr("gw_rename.done_reset", lang, shown=res.get("shown") or res.get("id"), id=res.get("id"))]
    if res.get("duplicates"):
        lines.append(tr("gw_rename.done_dup", lang, ids=", ".join(str(x) for x in res["duplicates"])))
    if res.get("note"):
        lines.append(res["note"])
    return lines
