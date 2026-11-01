"""Routes of the web panel for the rules «Россия напрямую» (docs/k3/SPEC-wave3.md §2, §3.2, §9.1) — imported by the
server through ``kaskad.panel.MODULES``:

    GET  /api/rules            the lists (date, source, counts), nftset, the profile of the node, the switches,
                               the profiles of users and devices, the last update — no command is run (state.json
                               and the store through kaskad.snapshot, dnsmasq's abilities from the last apply)
    POST /api/rules/update     {"force": false} — download now (synchronously: seconds; 409 while another runs)
    PUT  /api/rules/profile    {"target": "default"|"user"|"device", "id": "u-…|d-…", "profile": "inherit|all_exit|ru_direct",
                                "confirm_not_rf": true?, "orgs": [...]?, "ver": "…" (``target: "default"`` — required)}
    PUT  /api/rules/blocks     {"ads": true|false, "torrent": true|false} (either or both)
    PUT  /api/rules/consent    {"confirm_not_rf": true, "orgs": [...]?} — «Подтвердить» ⚠РФ, no change of the rules
    PUT  /api/rules/ru-via     {"items": [{"user"|"device": "u-…|d-…", "via": "inherit|direct|g-…", "ver": "…"}]} —
                               «Российские сайты» (T15 Q6, SPEC-groups §8.2–8.3, §8.9): one write for several people
                               and devices (the dialog «Выбрать пользователей») or one; all or nothing; no ``rev`` (a
                               field of a person, as the route) — ``ver`` of each row instead (``ru_ver`` of the row of
                               ``GET /api/rules``; REV-3310 П1): none — 400 ``ver_required``, another one — 409
                               ``object_changed`` (``data.id``, ``data.ver``), nothing written; asks no consent ⚠РФ
                               and gives none

The tick ⚠РФ (docs/k3/SPEC-stage1-rf-mark-builtin.md §5.1, R4): ``confirm_not_rf`` — the tick the person put under the
block, ``orgs`` — the organisations of the block shown (a wider block now — 409 again with the new one). Without the
tick where it is needed — 409 with ``data.need = "rf_consent"`` and the block (``data.block``, ``data.consent``,
``data.orgs``, ``data.names``): the page shows them without a reload. ``ver`` — the version of the form «Правила»
(``GET /api/rules`` → ``ver``; SPEC-stage1-exits §7.9 «Версия объекта»): none — 400 ``data.code = "ver_required"``
(an old tab), another one — 409 ``data.code = "object_changed"`` with the current ``data.ver``, nothing written.

Changes go through ``kaskad.rules.ops`` — the code of ``kaskadctl rules`` — and raise the generation of the read
cache (``snapshot.bump``). A change answers what it did, ``warnings`` (of the download and of the apply to the
gateway — like every change of kaskad.panel.api; what the system did otherwise stays in the log) and ``status``.
Errors: ``{"ok": false, "error": "<текст>", "code": <код kaskad.errors>}`` with 400 (arguments), 404 (no such
user/device), 409 (busy: an update or another change), 500.
"""

import re

from .. import errors, paths, snapshot, state, sysrun, util
from ..gw import model
from ..i18n import tr
from ..panel.routes import HttpError, json_response, route
from . import ops, rfgate

ORGS_MAX = 64                       # organisations of a shown block (the registry has a handful)
RU_VIA_MAX = 2000                   # rows of one «Выбрать людей» (people and devices of a node — far fewer)
_ID_RE = {"user": re.compile(r"^u-[0-9a-f]{8}$"), "device": re.compile(r"^d-[0-9a-f]{8}$")}


def _state():
    st = snapshot.read_json(paths.state(), None)
    return state.as_loaded(st) if isinstance(st, dict) else state.empty()      # ``gen`` as ``load`` (D-135)


def _http_error(e):
    code = getattr(e, "code", errors.ERROR)
    status = {errors.USAGE: 400, errors.CONFLICT: 409, errors.LOCKED: 409, errors.UNSUPPORTED: 409}.get(code, 500)
    return HttpError(status, str(e), code=code, data=getattr(e, "data", None) or None)


def who():
    """``rf_consent.who`` of the panel: the login of the panel (one admin); never an address or a key (§3.2)."""
    try:
        from ..panel import auth
        return str((auth.record() or {}).get("login") or "") or "admin"
    except (ImportError, OSError, ValueError, KeyError, errors.KaskadError):
        return "admin"


def rf_of(body, via="panel"):
    """``rf`` of a request: None without ``confirm_not_rf: true``; ``orgs`` — the organisations of the block shown
    (a list of strings). The panel and the scheme always show a block, so a tick without ``orgs`` — 400: the
    consent to «the whole block as it is now» is the CLI's (§5.1), never a page's. Anything else — 400."""
    tick = _bool(body, "confirm_not_rf")
    orgs = body.get("orgs")
    if (tick and orgs is None) or orgs is not None and (not isinstance(orgs, list) or len(orgs) > ORGS_MAX
                                                        or not all(isinstance(o, str) and 0 < len(o) <= 64 for o in orgs)):
        raise HttpError(400, tr("rules.panel.orgs"), code=errors.USAGE)
    return ops.rf_of(confirm=bool(tick), via=via, who=who(), orgs=orgs)


def _status(st):
    """``ops.status`` and, in ``rf``, ``need`` — the block the page shows under a choice of «Россия напрямую» and on
    the plaques (``rfgate.need_data`` of ``block_orgs``: the texts of the owner in the language of the page; the same
    a refusal carries), R4."""
    doc = ops.status(st)
    rf = doc.get("rf")
    if isinstance(rf, dict):
        rf["need"] = rfgate.need_data(rfgate.block_orgs(st))
    return doc


def _bool(body, key):
    v = body.get(key)
    if v is None:
        return None
    if not isinstance(v, bool):
        raise HttpError(400, tr("rules.panel.bool", field=key), code=errors.USAGE)
    return v


def _answer(res):
    """The answer of a change: its result without ``apply`` (what the system did — in the log), the warnings of
    the result and of the apply (the page shows them: «Обратите внимание»), the status of the rules."""
    out = dict(res)
    applied = out.pop("apply", None)
    warnings = []
    for src in (res, applied):
        if isinstance(src, dict):
            warnings.extend(util.redact(w) for w in src.get("warnings") or [] if isinstance(w, str))
    out["warnings"] = warnings
    out["status"] = _status(_state())
    return json_response(out)


@route("GET", r"/api/rules")
def get_rules(req):
    return json_response(_status(_state()))


@route("POST", r"/api/rules/update")
def post_update(req):
    body = req.json()
    force = _bool(body, "force") or False
    try:
        res = ops.update(sysrun.default_runner(), force=force)
    except errors.KaskadError as e:
        raise _http_error(e)
    snapshot.bump()
    return _answer(res)


@route("PUT", r"/api/rules/profile")
def put_profile(req):
    body = req.json()
    target = body.get("target")
    profile = body.get("profile")
    if not isinstance(profile, str):
        raise HttpError(400, tr("rules.panel.profile"), code=errors.USAGE)
    rf = rf_of(body)
    runner = sysrun.default_runner()
    try:
        if target == "default":
            ver = body.get("ver")
            if not isinstance(ver, str) or not ver:
                raise HttpError(400, tr("rules.panel.ver_required"), code=errors.USAGE, data={"code": "ver_required"})
            res = ops.set_default(runner, profile, rf=rf, ver=ver)
        elif target in ops.TARGETS:
            ref = body.get("id")
            st = _state()
            found = model.find_user(st, ref) if target == "user" else model.find_device(st, ref)
            if not isinstance(ref, str) or found is None:
                raise HttpError(404, tr("rules.panel.no_user", id=ref) if target == "user"
                                else tr("rules.panel.no_device", id=ref), code=errors.ERROR)
            res = ops.set_profile(runner, target, found["id"], profile, rf=rf)
        else:
            raise HttpError(400, tr("rules.panel.target"), code=errors.USAGE)
    except errors.KaskadError as e:
        raise _http_error(e)
    snapshot.bump()
    return _answer(res)


@route("PUT", r"/api/rules/blocks")
def put_blocks(req):
    body = req.json()
    ads, torrent = _bool(body, "ads"), _bool(body, "torrent")
    try:
        res = ops.set_blocks(sysrun.default_runner(), ads=ads, torrent=torrent)
    except errors.KaskadError as e:
        raise _http_error(e)
    snapshot.bump()
    return _answer(res)


@route("PUT", r"/api/rules/consent")
def put_consent(req):
    """«Подтвердить» ⚠РФ (modes legacy, need, uncovered — §5.1): the consent alone, through ``_change`` with ``rf``."""
    body = req.json()
    rf = rf_of(body)
    try:
        res = ops.consent(sysrun.default_runner(), rf)
    except errors.KaskadError as e:
        raise _http_error(e)
    snapshot.bump()
    return _answer(res)


@route("PUT", r"/api/rules/ru-via")
def put_ru_via(req):
    """«Российские сайты» of people and devices (``ops.set_ru_via_many``): ``items`` — ids only (``u-…``/``d-…``: a
    page always has them; a name could match another), ``via`` — ``inherit``, ``direct`` or a Russian group, ``ver`` —
    ``ru_ver`` the page read (REV-3310 П1: two tabs never overwrite each other's choice silently). A bad row — 400,
    nothing written; a row without ``ver`` — 400 ``ver_required`` (an old tab); an unknown id — 404; a row changed
    meanwhile — 409 ``object_changed``."""
    body = req.json()
    items = body.get("items")
    if not isinstance(items, list) or not items or len(items) > RU_VIA_MAX:
        raise HttpError(400, tr("rules.ru_via_items"), code=errors.USAGE)
    st = _state()
    for it in items:
        if not isinstance(it, dict) or set(it) - {"user", "device", "via", "ver"} or not isinstance(it.get("via"), str):
            raise HttpError(400, tr("rules.ru_via_items"), code=errors.USAGE)
        keys = [k for k in ops.TARGETS if k in it]
        if len(keys) != 1 or not isinstance(it[keys[0]], str):
            raise HttpError(400, tr("rules.ru_via_items"), code=errors.USAGE)
        ref = it[keys[0]]
        if not _ID_RE[keys[0]].match(ref):
            raise HttpError(400, tr("rules.ru_via_items"), code=errors.USAGE)
        found = model.find_user(st, ref) if keys[0] == "user" else model.find_device(st, ref)
        if found is None or found.get("id") != ref:
            raise HttpError(404, tr("rules.panel.no_user", id=ref) if keys[0] == "user"
                            else tr("rules.panel.no_device", id=ref), code=errors.ERROR)
    if any(not isinstance(it.get("ver"), str) or not it["ver"] for it in items):
        raise HttpError(400, tr("rules.panel.ver_required"), code=errors.USAGE, data={"code": "ver_required"})
    try:
        res = ops.set_ru_via_many(sysrun.default_runner(), items)
    except errors.KaskadError as e:
        raise _http_error(e)
    snapshot.bump()
    return _answer(res)
