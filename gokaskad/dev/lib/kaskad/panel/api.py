"""HTTP API of the web panel (docs/k3/SPEC-wave3.md §3.2): JSON under /api, handlers of kaskad.panel.routes.

Reads never run a command: state.json, live.json (the collector), exits.json and the pool are read in this
process through kaskad.snapshot (parsed once per change of the file); a view built from them (overview, people,
…) is kept until one of its files changes or this panel changes something (``snapshot.derived``). Unknown is not
zero: what the collector did not measure (it is stopped, live.json older than 30 s) is ``null``.

Changes call the same core as ``kaskadctl`` (kaskad.forwards, kaskad.gw.ops — they take the state lock
themselves, apply first, save after); after each one the generation rises (``snapshot.bump``) and the SSE hub
looks at once (``state`` event to every tab). A KaskadError becomes ``{"ok": false, "error": <its Russian
text>, "code": <kaskad.errors code>, "data"?}`` with HTTP 400 / 409 / 500 (routes.core_error).

Secrets: only ``POST /api/gw/devices/<id>/issue`` returns them (``Cache-Control: no-store``, never cached, never
in a view, never in an SSE event); the issue runs under the state lock (the core's address context is global).

Endpoints (auth "admin" unless said; every POST/PUT/DELETE of an admin route needs ``X-Kaskad-Admin: 1``):

    GET  session (public)              who am I: {"auth", "via": tunnel|password|link, "password", "public", …}
    POST login (public, X-Kaskad-Admin) {"login"?, "password"} -> cookie; 401 / 429 (Retry-After) / 503
    POST logout (public, X-Kaskad-Admin)
    GET  /login?t=<token> (public)     one-time link -> 303 "/" (+ cookie) or "/?link=expired" | "/?link=limit"
    GET  health (public)               {"ok", "version", "uptime", "commands": commands this panel ran}
    GET  overview                      gateway, inbounds, exits, devices (live, null when unknown), forwards (count,
                                       traffic), host, events, warnings, live {ts, age, fresh, starting?}, gen
                                       (sse.state_gen); a silent collector on a gateway that is on: exits «nodata»
    GET  forwards · POST forwards {proto, port, to, to_port, name?, force?} · DELETE forwards/<proto>/<port>
    GET  gw · POST gw/enable {base?, host?} · POST gw/disable · PUT gw/host {host? | detect: true | to_ip: true}
         · PUT gw/route {via, fail?}      (GET gw: ``clients`` — the domain, the IP and the address in the settings
         by inbound, _client_addresses)
    GET  gw/addresses (?refresh=1; runs=True — ip, the echo)   the document, mip, page (codes of the rows), ver
         · PUT gw/addresses/in {ips | all, yes?, ver} · PUT gw/addresses/in6 {ips | all | none, yes?, ver}
         · PUT gw/addresses/exit {id | group, ip, ver}   (409 need: "confirm" — again with yes; 400 ver_required)
    GET  gw/inbounds · POST gw/inbounds/<kind>/install {port?, force?} (-> record, notes, previous: what a
         reinstall kept — ops.inbound_install) | uninstall | enable | disable
         · PUT gw/inbounds/<kind>/port {port, proto?, force?} · PUT gw/inbounds/<kind>/option {key, value}
         · GET gw/inbounds/<kind>/port?port=N&random=1   the port of «Установить …?» (read-only, runs ss: what to
           put in the field, the recommended ports, is N free — ops.port_offer; круг 1 №14)
         · GET gw/inbounds/<kind>/settings · PUT gw/inbounds/<kind>/settings {port?, proto?, mtu?, tls?, dry?, yes?,
           force?, safe?, ver} · POST gw/inbounds/<kind>/settings/undo   «Настройки протокола» one action (F2, F3)
    GET  gw/reissue                    «ждут новых настроек» of every protocol: {kind: {title, hard, soft}} (F1)
    GET  gw/net · PUT gw/net/base {base, ver, dry?, yes?} · POST gw/net/undo   «Сеть шлюза» /16 (F6; the safe apply F7)
    GET  gw/vless/masks                the cover sites of VLESS Reality to choose from (not checked: nothing runs)
         · POST gw/vless/masks/check   the same, each one checked from the node now (TLS 1.3; seconds per site)
         · PUT gw/vless/mask {site: name | "own" | "auto", check?: true}   the cover site (-> reissue: whom to give the
         links again) · POST gw/vless/rekey   a new key and shortId of the node (-> reissue)
         · POST gw/vless/sync   the mode by the node's site: TLS with its domain or Reality (-> security, reissue)
         · POST gw/vless/mode {mode: tls | reality, site?, accept_risks?, risks?, yes?}   the mode chosen by the person
           (409 reality_consent / vless_change: show data.block / data.text, then again with the answer)
         · GET gw/vless/ask {do: tls | reality | site_on | site_off | clear, site?}   what that action asks — read
           before it (T-3378 Н-2): 200 with need reality_consent | vless_change and its data, need null — nothing
           (no lock, nothing written; runs=True — ss, nginx -T)
    GET  gw/people · POST gw/users {name, via?, fail?} · DELETE gw/users/<id>?force=1
         · PUT gw/users/<id>/route {via ("inherit" clears), fail?} · PUT gw/users/<id>/enabled {enabled}
         · GET|POST gw/users/<id>/page {action: enable|rotate|disable}   the person's page (userpage.py)
         · GET|POST gw/users/<id>/sub {action: enable|rotate|disable}    his subscription for apps (userpage.py)
         · POST gw/devices {user, name, kind?, platform?, access?: [kinds], via?, fail?} · DELETE gw/devices/<id>
         · PUT gw/devices/<id>/access {access: [kinds]} | {kind, on} · PUT gw/devices/<id>/route {via, fail?}
         · PUT gw/devices/<id>/enabled {enabled} · POST gw/devices/<id>/reset {inbound?}
    POST gw/devices/<id>/issue {inbound?, os?}   settings with secrets (no-store)
    GET  gw/offers?platform=<p>        what to give a device of the platform first (spec §4)
    GET  gw/exits                      the rows + ``add`` (where to add a key: the panel, the SSH menu with the right
                                       number — exits.add_hint), ``offline`` (the pool is empty and the default route
                                       blocks: no internet — exits.offline_empty) and its ``offline_text``
         · PUT gw/exits/<id> {enabled?, name?}   (a repeated key, id e-<10 hex>-<line>: 409, no settings)
         · POST gw/exits {key}   a key into the pool (≤ 64 KB; the script on stdin, the way the SSH menu adds it —
         ops.exit_add): -> added [{id, name}], results (per key: added | in_pool | not_added + why), warnings; the
         key in the pool already — 409; never echoed, never logged. A file goes the same way: the browser reads it
         (FileReader) and sends its text; nothing is stored
         · POST gw/exits/preview {key}   what the text holds, nothing written (ops.exit_preview, the menu's parser in
         this process): items [{id, kind, title «AmneziaWG 2.0» | «WireGuard» | «VLESS», where, name, in_pool,
         problem?}], subscription (a link is not downloaded), text; no keys found — 400 with the reason
         · DELETE gw/exits/<id>  (a repeated key too) its line out of the pool (ops.exit_del): -> left, warnings;
           one key twice — twin, moved and text («Маршруты перешли на …», as the CLI and MCP);
         no such exit — 404, the pool changed meanwhile — 409
         · rows of GET gw/exits carry ``source`` ({sub, name} — the subscription the key came from, null — by hand)
         and the answer ``subs`` (exitsubs.listing: id, name «подписка N» | домен, auto, keys, count, full, stale, text)
    POST gw/exit-subs {url, auto?}     a subscription: downloaded now (a failure — nothing kept), its keys into the pool
         · POST gw/exit-subs/refresh | gw/exit-subs/<id>/refresh   download again, change only its keys
         · POST gw/exit-subs/<id>/restore   «Вернуть исключённые»: keys removed by hand come back (downloaded again)
         · PUT gw/exit-subs/<id> {auto} · DELETE gw/exit-subs/<id>[?keep_keys=1]   (its keys out of the pool too)
    GET  gw/awg/engine                 the engine of AmneziaWG (gw/awgengine.status: files, the pool parsed here — nothing
                                       runs): want, active, reason, offer {show, blocked, pending}, warnings, text
         · POST gw/awg/check (the host is asked: uname, apt-cache — an explicit «Подробнее») · POST gw/awg/engine
         {engine: kernel|program, force?} (long: build, self-test, the clients; a rollback answers ok with
         rolled_back and the reason in warnings) · POST gw/awg/export (secrets, no-store) · POST gw/awg/import {doc}
    POST gw/mtu {kind, action: reissue|s4|keep}   §6.1 (``mtu`` of GET gw — card, budget, server and client MTU per
                                       inbound, legs, ``ask``, per row ``offer`` and the ``devices`` to name first): a
                                       narrow card — new settings with a smaller MTU; a server below 1232 (S4 of 2.0) —
                                       new parameters with a smaller S4 (the old settings stop connecting); or as it is
    GET  events                        SSE (sse.py)
    GET  appearance · PUT appearance {field: value, …}   «Вид», a white list (goAmnezia's fields)
    GET  intro -> {due, done, setup_finished, device} · PUT intro {done}   «Знакомство с панелью» after the wizard
    POST lang {lang: ru|en}            the language of the server (KASKAD_LANG, 3.3.2): panel, wizard, SSH menu, bot
    GET  panel · PUT panel/password {password, current?, login?} · POST panel/link {ttl?}
         · PUT panel/public {on, port?, force?} · PUT panel/settings {require_password}
    GET  backup/summary                what a copy would hold (counts, no secret)
         · POST backup/export {password, parts?, without?} -> {file: the text of the copy, name, parts, summary}
         (no-store) · POST backup/preview {file, password, parts?} -> {preview: what comes, what it replaces, the
         address of the devices, warnings, blockers} · POST backup/import {file, password, parts?} -> {parts, backup
         (the snapshot taken first), warnings, notes, text} · POST backup/undo — the snapshot back (kaskad.backup)
    GET  github · PUT github {token, force?} · DELETE github · POST github/check    the GitHub key (kaskad.ghkey):
         its state, never the key; PUT checks the format and that GitHub gives the core with it

ids in paths: users ``u-<8 hex>``, devices ``d-<8 hex>``, exits ``e-<10 hex>`` (names are for bodies).
"""

import hashlib
import json
import os
import re
import threading
import time

from .. import __version__, backup, errors, forwards, ghkey, paths, snapshot, state, sysrun, util
from ..errors import KaskadError
from ..gw import addr, awgengine, exits, exitsubs, inlog, live, model, ops, registry
from ..gw import policy_ops as pops
from ..gw import rev as grev
from ..i18n import tr
from . import auth, conf, routes, server, sse
from . import lang as weblang
from .once import Once
from .routes import HttpError, json_response, route

RUNNER = None                       # serve(): the counting (and scoped) runner of this process
STARTED = time.time()
_HOST = live.Host()                 # CPU of the node when the collector does not write live.json


def runner():
    return RUNNER or sysrun.default_runner()


def ok(**kw):
    body = {"ok": True}
    body.update(kw)
    return json_response(body)


def _changed():
    snapshot.bump()
    sse.HUB.poke()


def _call(fn, *args, **kw):
    """A change through the core; its error -> HttpError; the generation rises whatever happened (a failed
    change may still have done something: packages, a stopped unit)."""
    try:
        return fn(*args, **kw)
    except KaskadError as e:
        raise routes.core_error(e)
    finally:
        _changed()


def _warnings(res):
    out = []
    for src in (res, (res or {}).get("apply") if isinstance(res, dict) else None):
        if isinstance(src, dict):
            out.extend(util.redact(w) for w in src.get("warnings") or [] if isinstance(w, str))
    return out


# ── bodies ─────────────────────────────────────────────────────────────────

def _str(body, key, required=False, max_len=200):
    v = body.get(key)
    if v is None or v == "":
        if required:
            raise HttpError(400, tr("panel.api.fieldMissing", field=key))
        return None
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        raise HttpError(400, tr("panel.api.fieldString", field=key))
    v = str(v)
    if len(v) > max_len:
        raise HttpError(400, tr("panel.api.fieldTooLong", field=key, max=max_len))
    return v


_PORT_RE = re.compile(r"^[0-9]{1,5}$")


def _port(v, key, required=False):
    """A port of the body or of the query, strictly (круг 1 №14): an integer (not true/false) or a string of 1–5 ASCII
    digits, 1–65535 — not «+80», « 80», «8_0», «٨٠» or 80.0 (``int()`` takes them). None — not given."""
    if v is None or v == "":
        if required:
            raise HttpError(400, tr("panel.api.fieldMissing", field=key))
        return None
    if isinstance(v, bool) or not isinstance(v, (int, str)) or (isinstance(v, str) and not _PORT_RE.match(v)):
        raise HttpError(400, tr("panel.api.badPort", field=key))
    n = int(v)
    if not 1 <= n <= 65535:
        raise HttpError(400, tr("panel.api.badPort", field=key))
    return n


def _bool(body, key, default=None, required=False):
    v = body.get(key)
    if v is None:
        if required:
            raise HttpError(400, tr("panel.api.fieldMissingBool", field=key))
        return default
    if not isinstance(v, bool):
        raise HttpError(400, tr("panel.api.fieldBool", field=key))
    return v


def _kinds(value, key="access"):
    if value is None:
        return []
    if isinstance(value, str):
        value = [k.strip() for k in value.split(",") if k.strip()]
    if not isinstance(value, list) or not all(isinstance(k, str) and len(k) <= 16 for k in value):
        raise HttpError(400, tr("panel.api.fieldKinds", field=key, example='["wg", "awg2"]'))
    return value


def _route_args(body):
    via = _str(body, "via") or _str(body, "route")
    return via, _str(body, "fail")


# stage 1 (T7, SPEC-stage1-exits §7.9): the version of the policy ``rev`` and of an object ``ver``. HTTP is called only by
# the panel (CLI and MCP go through kaskadctl), so a write of the list without ``rev`` — or of an object without ``ver``
# — comes only from an old tab out of the cache: 400 ``rev_required`` / ``ver_required``, nothing written

def _dry(req):
    return (req.query.get("dry") or "").lower() in ("1", "true", "yes", "on")


def _refused(e):
    raise routes.core_error(e)


def _need_rev(req, body=None, dry=False):
    """``rev`` of the body or of ``?rev=``; a write without it — 400 ``rev_required`` (a dry run needs none)."""
    v = (body or {}).get("rev") if isinstance(body, dict) else None
    if v is None:
        v = req.query.get("rev")
    if v in (None, "") and not dry:
        _refused(pops.rev_required())
    return None if v in (None, "") else v


def _need_ver(req, body=None, dry=False):
    v = (body or {}).get("ver") if isinstance(body, dict) else None
    if v is None:
        v = req.query.get("ver")
    if v in (None, "") and not dry:
        _refused(pops.ver_required())
    return None if v in (None, "") else str(v)


def _pcall(fn, *args, **kw):
    """A write of policy_ops; a dry run changes nothing the tabs show (no generation)."""
    if kw.get("dry"):
        try:
            return fn(*args, **kw)
        except KaskadError as e:
            raise routes.core_error(e)
    return _call(fn, *args, **kw)


# ── reads ──────────────────────────────────────────────────────────────────

def _st():
    try:
        return snapshot.derived("state", [paths.state()], state.load)
    except KaskadError as e:
        raise routes.core_error(e)


def _live_now():
    doc, age = live.read()
    return doc, age, live.fresh(age)


def _live_info(doc, age, fresh):
    return {"ts": (doc or {}).get("ts"), "age": round(age, 1) if age is not None else None, "fresh": fresh}


def _view_files():
    return [paths.state(), paths.live_json(), exits.snapshot_path(), paths.egress_up(), paths.egress_parser(),
            inlog.seen_path()]                     # 3.3.3: «closed» of the inbounds (gw.inlog)


def _title(kind):
    ad = registry.get(kind)
    return (getattr(ad, "title", "") if ad is not None else "") or kind


def _public_record(rec):
    return dict((k, v) for k, v in (rec or {}).items() if "priv" not in k.lower() and "psk" not in k.lower())


def _exits_now(rows, gw_on, fresh):
    """The exit rows as they can be told now. Their state comes from the collector too (its watchdog writes
    exits.json): while it is silent on a gateway that is on, the snapshot is the last check, not the present —
    «нет данных» (``exits.no_data``), not «работает» (stand T-G1b, N2)."""
    return rows if fresh or not gw_on else [exits.no_data(r) for r in rows]


def _alive_count(rows):
    """How many exits work now — None while that is unknown (``_exits_now``)."""
    if any(r.get("state") == exits.NO_DATA_STATE for r in rows):
        return None
    return len([r for r in rows if r.get("usable")])


def _exit_rows(st, roles=("abroad",)):
    """([public rows of the pool + usable + text], error text or None) — the egress parser in this process.
    ``roles`` — as ``exits.pool``: the overview and ``GET /api/gw`` count the foreign exits; ``GET /api/gw/exits``
    asks both (T15): a Russian exit is a row there (``role`` «ru») — «Российские группы» name it and list it, «Все
    выходы» show the foreign ones (круг 1 №31г: without it the panel showed the id «e-…» of a Russian exit)."""
    try:
        pool = exits.pool(None, roles=roles)
    except KaskadError as e:
        return [], util.redact(str(e))
    snap = exits.load_snapshot()
    alive = exits.alive_ids(snap)
    src = exitsubs.owners()
    gw_off = not model.enabled(st)                      # the collector checks the exits only while the gateway is on
    rows = []
    for e in pool:
        row = exits.public(e, st, snap)
        row["gw_off"] = gw_off
        row["source"] = src.get(e["id"])                  # the subscription the key came from (None — by hand)
        row["usable"] = bool(exits.parsed(e) and row["enabled"] and e["id"] in alive)
        row["text"] = exits.state_text(row)
        row["since"] = live.epoch(row.get("since"))
        rows.append(row)
    return rows, None


def _inbound_rows(st, lv):
    g = model.peek(st)
    rows = ops.inbound_list(runner(), st, with_status=False)            # no status: nothing runs
    lin = (lv or {}).get("inbounds") or {}
    for r in rows:
        rec = model.inbound(st, r["kind"])
        l = lin.get(r["kind"]) if lv is not None else None
        r["why"] = None
        if r["installed"] and r["enabled"] and g["enabled"]:
            r["running"] = l.get("running") if l else None
            r["sessions"] = l.get("sessions") if l else None
            # what the adapter said in the collector's last round (live.json): its warnings and, for an inbound
            # that does not run, why — nothing runs for a request (inbound_list above is without status)
            r["warnings"] = [w for w in (l or {}).get("warnings") or [] if isinstance(w, str)]
            r["why"] = (l or {}).get("why") if r["running"] is False else None
        elif not r["installed"]:
            r["running"], r["sessions"] = False, 0
        else:
            r["running"], r["sessions"] = False, 0                   # switched off: known not to run
        r["rx"] = (l or {}).get("rx")
        r["tx"] = (l or {}).get("tx")
        r["record"] = _public_record(rec) if rec is not None else None
        if r["kind"] == "vless" and rec is not None:
            r["record"].update(ops.vless_view(st))          # the mode now: TLS with the domain or Reality
        if r["kind"] == "hy2" and rec is not None:
            r["record"].update(_hy2_view(st) or {})         # the range, the certificate now, its pin and warnings
        r["options"] = [{"key": k, "title": t, "value": bool((rec or {}).get(k))}
                        for (kind, k), t in sorted(ops.OPTIONS.items()) if kind == r["kind"]]
        r["fail"] = _inbound_fail(st, r, g["enabled"])
    # 3.3.3: «Перезапустить» of a shared unit breaks the others for a few seconds — the card asks first
    on = [r["kind"] for r in rows if r["installed"] and r["enabled"]]
    titles = dict((r["kind"], r.get("title") or r["kind"]) for r in rows)
    for r in rows:
        try:
            s = inlog.shared(r["kind"], on) if r["installed"] else None
        except Exception:                               # noqa: BLE001 — a hint on a card, never a broken list
            s = None
        r["shared"] = {"gateway": s["gateway"], "with": [titles[k] for k in s["with"]]} if s else None
    return rows


def _hy2_view(st):
    try:
        v = ops.hy2_show(None, st)
    except (KaskadError, OSError, ValueError):
        return None
    return dict((k, v.get(k)) for k in ("port", "hop", "obfs", "cert", "cert_mode", "pin", "domain", "domain_ready",
                                         "ports_view", "warnings", "note"))


def _inbound_fail(st, r, gw_on):
    """3.3.3: why an installed inbound does not work (kaskad.gw.inlog.fail_info): the journal only for one that does
    not run, «closed» — the file of issues only. Never a broken view."""
    if not (r["installed"] and r["enabled"] and gw_on) or r.get("running") is None:
        return None
    try:
        return inlog.fail_info(runner(), r["kind"], r, st)
    except Exception:                                   # noqa: BLE001 — a hint on a card, never a broken list
        return None


def _needs_key(kind):
    fn = getattr(registry.get(kind), "needs_key", None)
    try:
        return bool(fn()) if callable(fn) else False
    except Exception:                                   # noqa: BLE001 — a hint for a button, never a broken view
        return False


def _with_keys(rows):
    """The rows of the inbounds with ``needs_key``: AmneziaWG cannot be installed until a GitHub key is given (no core
    on the disk, no key to download one) — the panel shows «Ввести ключ GitHub» on the card. Per request, outside the
    cached view: the key and the core change outside state.json (the menu, goInstaller, a copy of bin/). Files only:
    no command runs."""
    return [dict(r, needs_key=_needs_key(r["kind"]) if r.get("available") and not r.get("installed") else False)
            for r in rows]


def _overview_core(fresh):
    st = _st()
    g = model.peek(st)
    doc, _ = live.read()
    lv = doc if fresh else None
    inb = []
    lin = (lv or {}).get("inbounds") or {}
    for kind in model.installed_kinds(st):
        rec = model.inbound(st, kind) or {}
        ad = registry.get(kind)
        on = bool(rec.get("enabled", True))
        l = lin.get(kind) if lv is not None else None
        try:
            ports = ", ".join(ops.ports_shown(ad, st)) if ad is not None else ""
        except Exception:                                   # noqa: BLE001 — an adapter of another work package
            ports = ""
        running = (l or {}).get("running") if (on and g["enabled"] and lv is not None) else (
            None if on and g["enabled"] else False)
        sessions = (l or {}).get("sessions") if (on and g["enabled"] and lv is not None) else (
            None if on and g["enabled"] else 0)
        inb.append({"kind": kind, "title": _title(kind), "enabled": on, "running": running, "sessions": sessions,
                    "ports": ports})
    rows, err = _exit_rows(st)
    rows = _exits_now(rows, g["enabled"], fresh)
    items = [{"id": r["id"], "label": r["name"], "kind": r["kind"], "state": r.get("state"), "alive": r.get("alive"),
              "enabled": r["enabled"], "usable": r["usable"]} for r in rows]
    devs = (lv or {}).get("devices") or {}
    fw = st.get("forwards") or []
    lf = (lv or {}).get("forwards") if lv is not None else None
    packets = nbytes = None
    if lf:
        pk = [v.get("packets") for v in lf.values() if isinstance(v, dict) and v.get("packets") is not None]
        by = [v.get("bytes") for v in lf.values() if isinstance(v, dict) and v.get("bytes") is not None]
        packets = sum(pk) if pk else None
        nbytes = sum(by) if by else None
    elif lv is not None and not fw:
        packets = nbytes = 0
    warnings = []
    if st.get("engine") != "nft":
        warnings.append(tr("panel.api.oldEngine"))
    if err:
        warnings.append(err)
    if exits.offline_empty(st):
        warnings.append(tr(exits.NO_EXITS_OFFLINE))
    return {"ok": True, "version": __version__, "engine": st.get("engine"),
            "gateway": {"enabled": bool(g["enabled"]), "host": model.public_host(st), "users": len(g["users"]),
                        "devices": len(g["devices"]),
                        "online": sum(1 for d in devs.values() if isinstance(d, dict) and d.get("online"))
                        if lv is not None else None,
                        "problems": list(((lv or {}).get("gateway") or {}).get("problems") or [])
                        if g["enabled"] else []},
            "inbounds": inb,
            "exits": {"total": len(rows), "alive": _alive_count(rows), "items": items},
            # the devices of live.json (every device: online or not) — the page without SSE (and its scheme) knows
            # who is online from this answer too; null without fresh data
            "devices": dict(devs) if lv is not None else None,
            "forwards": {"count": len(fw), "packets": packets, "bytes": nbytes},
            "events": live.feed_events((doc or {}).get("events") or [])[-20:],     # Н9: an outage — one line
            "warnings": warnings}


STALE_TEXT = "panel.api.stale"     # a key of the catalog (3.3.2): stale_text() is the text in the current language


def stale_text():
    """The warning about a silent collector (STALE_TEXT) in the language of this moment."""
    return tr(STALE_TEXT)


START_GRACE = 120           # s after «Включить шлюз» or a boot: the collector's first round may still be coming


def _starting(st, now=None):
    """Seconds left of the start of the collector, or None. The gateway was switched on (``gateway.enabled_at`` of
    state.json, ops.enable) or the node booted (/proc/uptime) less than START_GRACE s ago: no fresh live.json yet is
    no silence then — the page says «сборщик запускается», without the alarm of STALE_TEXT (stand T-G1b, N2/O).
    Files only, no command."""
    now = time.time() if now is None else now
    ages = []
    at = live.epoch(model.peek(st).get("enabled_at"))
    if at:
        ages.append(now - at)
    up = live.uptime()
    if up is not None:
        ages.append(up)
    left = [START_GRACE - a for a in ages if 0 <= a < START_GRACE]
    return int(max(left)) + 1 if left else None


def _with_live(core, doc, age, fresh, host=False):
    """A cached view + what changes with the clock: the age of live.json, the host (from /proc when the
    collector does not write), the warning about a silent collector — or, right after «Включить шлюз» or a boot,
    ``live.starting`` (seconds the page waits calmly for the collector's first round)."""
    out = dict(core)
    out["live"] = _live_info(doc, age, fresh)
    out["now"] = int(time.time())
    if host:
        out["host"] = (doc or {}).get("host") if fresh else _HOST.sample()
        # 3.3.7.10: the marks of the lines (the open window of activity, the minute of m1) — the page without SSE
        # updates «Пользователи» and «Выходы» from its poll too
        for k in ("activity", "metrics"):
            if fresh and isinstance((doc or {}).get(k), dict):
                out[k] = doc[k]
    gw = core.get("gateway")
    gw_on = gw.get("enabled") if isinstance(gw, dict) else core.get("enabled")
    if gw_on and not fresh:
        starting = _starting(_st())
        if starting:
            out["live"]["starting"] = starting
        else:
            out["warnings"] = list(core.get("warnings") or []) + [stale_text()]
    return out


# ── session and login ──────────────────────────────────────────────────────

def _cfg():
    return server.PANEL.cfg if server.PANEL is not None else conf.load()


def _admin_from_domain(req):
    """An admin's session that came through nginx (the domain) — never the port, never the SSH tunnel."""
    return req.session is not None and req.listener == "proxy"


def _domain_sign_in():
    """3.3.7.5 круг 3: an admin's session on the listener for nginx — the panel opened from outside through the domain.
    On a new install that is the moment the port may go off by itself (``portoff``); the decision runs aside (ufw
    takes a moment), the open mode closes on the reload after it."""
    from . import portoff
    if not portoff.pending():
        return

    def run():
        if portoff.on_domain_login() == "off" and server.PANEL is not None:
            server.PANEL.reload()
    threading.Thread(target=run, name="port-off", daemon=True).start()


@route("GET", r"/api/session", auth="public")
def session_info(req):
    s = req.session
    if _admin_from_domain(req):
        _domain_sign_in()
    rec = auth.record()
    cfg = _cfg()
    # listener — local | public | proxy: which way this tab came (3.3.7.5: the open mode is under the secret prefix
    # too, so the prefix no longer tells the domain apart; the page asks the server instead)
    # has — what this server can answer of the optional parts of the panel (T-3377 Н-2): the panel asks for none of
    # them it does not have — no 404 in the console on every load. Only to the admin; by the routes themselves, so a
    # version with the routes (activity — 3.3.7.9) turns it on without a word here
    more = {"has": {"activity": _has_route("GET", "/api/gw/activity")}} if s is not None else {}
    return ok(auth=s is not None, via=(s or {}).get("via"), scheme=req.scheme, listener=req.listener,
              public=cfg["public"]["on"],
              password=bool(rec), login=rec.get("login") if s is not None else None,
              require_password=cfg["require_password"], version=server.STATIC.version,
              expires_in=(s or {}).get("expires_in"), **more)


def _has_route(method, path):
    r, _m = routes.find(method, path)
    return isinstance(r, routes.Route)


@route("GET", r"/api/health", auth="public")
def health(req):
    return ok(version=__version__, uptime=int(time.time() - STARTED),
              commands=getattr(RUNNER, "count", None), streams=sse.counts()["streams"])


@route("POST", r"/api/login", auth="public", csrf=True)
def login(req):
    b = req.json()
    pw = b.get("password")
    name = b.get("login") or ""
    if not isinstance(pw, str) or not isinstance(name, str) or len(pw) > auth.PASSWORD_MAX or len(name) > 64:
        raise HttpError(400, tr("panel.api.loginStrings"))
    try:
        good, left = auth.login(req.client_ip, name, pw)
    except auth.LoginRefused as e:
        raise HttpError(e.status, str(e), data={"retry_after": e.retry_after, "auth": "limit"})
    if not good:
        if left == 0:
            util.log_tr("journal.panel.login_banned", n=int(auth.ATTEMPTS), ip=req.client_ip, min=int(auth.WINDOW // 60))
        if auth.password_set():
            text = tr("panel.api.badLogin")
        else:
            text = tr("panel.api.noPassword")
        raise HttpError(401, text, data={"left": left, "auth": "failed"})
    old = (req.session or {}).get("token")
    if old:
        auth.SESSIONS.drop(old)
    token = auth.SESSIONS.create("password", req.client_ip)
    util.log_tr("journal.panel.login_password", ip=req.client_ip)
    return json_response({"ok": True, "via": "password"},
                         headers={"Set-Cookie": auth.set_cookie(token, req.scheme == "https")})


@route("POST", r"/api/logout", auth="public", csrf=True)
def logout(req):
    tok = (req.session or {}).get("token")
    if tok:
        auth.SESSIONS.drop(tok)
    return json_response({"ok": True, "via": (req.session or {}).get("via") if not tok else None},
                         headers={"Set-Cookie": auth.clear_cookie(req.scheme == "https")})


@route("GET", r"/login", auth="public")
def login_link(req):
    h = {"Cache-Control": "no-store", "Location": "/"}
    t = req.query.get("t")
    if not t:
        return routes.Response(303, b"", h)
    # the links' own count (auth.LINKS): wrong passwords from this address — over the SSH tunnel it is everybody's
    # 127.0.0.1 — never close the way in by a link the menu or the bot has just given (stand T-G1b, N5)
    key = auth.bucket(req.client_ip)
    allowed, _, _ = auth.LINKS.begin(key)
    if not allowed:
        h["Location"] = "/?link=limit"
        return routes.Response(303, b"", h)
    if not auth.redeem_link(t):
        h["Location"] = "/?link=expired"
        return routes.Response(303, b"", h)
    auth.LINKS.clear(key)
    old = (req.session or {}).get("token")
    if old:
        auth.SESSIONS.drop(old)
    token = auth.SESSIONS.create("link", req.client_ip)
    h["Set-Cookie"] = auth.set_cookie(token, req.scheme == "https")
    util.log_tr("journal.panel.login_link", ip=req.client_ip)
    return routes.Response(303, b"", h)


# ── overview ───────────────────────────────────────────────────────────────

@route("GET", r"/api/overview")
def overview(req):
    sse.poll_seen()                         # 3.3.7.11: the fallback poll watches too — the collector keeps ≤ 5 s for it
    doc, age, fresh = _live_now()
    core = snapshot.derived(("overview", fresh, weblang.current()), _view_files(), lambda: _overview_core(fresh))
    out = _with_live(core, doc, age, fresh, host=True)
    out["gen"] = sse.state_gen()            # changes with every SSE «state»: the fallback poll re-reads on it
    return json_response(out)


# ── forwards ───────────────────────────────────────────────────────────────

def _forwards_view(fresh):
    st = _st()
    doc, _ = live.read()
    if st.get("engine") != "nft":
        return {"ok": True, "engine": st.get("engine"), "managed": False, "forwards": []}
    lf = ((doc or {}).get("forwards") or {}) if fresh else None
    rows = []
    for f in state.sort_forwards(list(st.get("forwards") or [])):
        r = dict(f)
        c = (lf or {}).get("%s:%d" % (f["proto"], f["port"])) if lf is not None else None
        r["packets"] = (c or {}).get("packets") if c else None
        r["bytes"] = (c or {}).get("bytes") if c else None
        rows.append(r)
    return {"ok": True, "engine": "nft", "managed": True, "forwards": rows}


@route("GET", r"/api/forwards")
def forwards_list(req):
    doc, age, fresh = _live_now()
    core = snapshot.derived(("forwards", fresh, weblang.current()), [paths.state(), paths.live_json()], lambda: _forwards_view(fresh))
    out = dict(core)
    out["live"] = _live_info(doc, age, fresh)
    return json_response(out)


@route("POST", r"/api/forwards")
def forwards_add(req):
    b = req.json()
    res = _call(forwards.add_forward, _str(b, "proto", True, 3), _str(b, "port", True, 5), _str(b, "to", True, 64),
                _str(b, "to_port", True, 5), name=_str(b, "name", max_len=64) or "",
                force=bool(_bool(b, "force", False)), runner=runner())
    return ok(forward=res["forward"], replaced=res["replaced"], warnings=_warnings(res))


@route("DELETE", r"/api/forwards/(?P<proto>tcp|udp)/(?P<port>[0-9]{1,5})")
def forwards_del(req):
    res = _call(forwards.delete_forward, req.match.group("proto"), req.match.group("port"), runner=runner())
    return ok(removed=res["removed"], warnings=_warnings(res))


# ── the gateway ────────────────────────────────────────────────────────────

def _client_addresses(st):
    """3.3.2 (the owner's №15): the domain of the node, its IP and what really goes into the settings of the devices —
    files only. ``hosts``: [{"host", "kinds"}] by installed inbound, the address of ``gateway.host`` first; VLESS in
    TLS mode names the domain (its certificate), SSTP and OpenConnect switched to the domain's certificate
    (``site use``) — the domain; everything else (WireGuard, AmneziaWG, OpenVPN, IKEv2, L2TP, PPTP, SOCKS5, VLESS
    Reality) — ``gateway.host`` («Адрес для клиентов»: an IP, or the domain after «Подключать устройства по домену»).
    ``ip`` — the IPv4 of the node: ``gateway.host`` when it is one, else the A records of the domain that are the
    node's own (the last DNS check of the site), else None."""
    import ipaddress
    from ..site import model as smodel
    host = model.public_host(st)
    d = smodel.domain_of(st)
    hosts = [{"host": host, "kinds": []}]
    for k in model.installed_kinds(st):
        h = host
        try:
            if k == "vless":
                h = ops.vless_view(st).get("domain") or host
            elif k == "hy2":                            # Hysteria2 with the domain's certificate names the domain
                h = (d if (model.inbound(st, k) or {}).get("cert_mode") == "domain" and d else host)
            elif k in smodel.USE_KINDS:
                h = smodel.use_for(st, k) or host
        except (KaskadError, OSError, ValueError):
            h = host
        row = next((x for x in hosts if x["host"] == h), None)
        if row is None:
            row = {"host": h, "kinds": []}
            hosts.append(row)
        row["kinds"].append(k)
    ip = None
    try:
        ip = str(ipaddress.IPv4Address(host))
    except ValueError:
        dns = smodel.peek(st)["dns"] or {}
        own = [x for x in (dns.get("own") or []) if isinstance(x, str) and ":" not in x]
        mine = [x for x in (dns.get("a") or []) if x in own]
        ip = (mine or own or [None])[0]
    return {"domain": d or None, "domain_unicode": smodel.unicode(d) if d else None, "ip": ip,
            "hosts": [x for x in hosts if x["kinds"] or x["host"] == host]}


def _gw_view(fresh):
    st = _st()
    g = model.peek(st)
    doc, _ = live.read()
    lv = doc if fresh else None
    rows, err = _exit_rows(st)
    try:
        net = {"base": str(addr.base(st)), "dns": addr.dns_ip(st)}
    except KaskadError:
        net = {"base": None, "dns": None}
    services = dict(((lv or {}).get("gateway") or {}).get("services") or {}) if lv is not None else {}
    rows = _exits_now(rows, g["enabled"], fresh)
    out = {"ok": True, "enabled": bool(g["enabled"]), "engine": st.get("engine"), "net": net,
           "host": model.public_host(st), "host_auto": bool(g.get("host_auto", True)),
           "routes": {"default": model.route_of(st, {"user": None})},
           "users": len(g["users"]), "devices": len(g["devices"]),
           "inbounds": _inbound_rows(st, lv), "exits": rows,
           "services": {"dns": services.get("dns"), "xray": services.get("xray")},
           "problems": list(((lv or {}).get("gateway") or {}).get("problems") or []) if g["enabled"] else []}
    if err:
        out["exits_error"] = err
    out["clients"] = _client_addresses(st)
    try:
        out["mtu"] = ops.mtu_view(st)                  # files only: the plan in the state and /sys of the card
    except (KaskadError, OSError, ValueError):
        out["mtu"] = None
    return out


@route("GET", r"/api/gw")
def gw_status(req):
    doc, age, fresh = _live_now()
    core = snapshot.derived(("gw", fresh, weblang.current()), _view_files(), lambda: _gw_view(fresh))
    out = _with_live(core, doc, age, fresh)
    out["inbounds"] = _with_keys(core["inbounds"])
    return json_response(out)


def _known_host(fn, *args, **kw):
    """Ш2 (Л3) in the panel: the refusal «not an address of this server» names the way of the SSH menu — not the keys
    of kaskadctl the panel does not have (REV З-5)."""
    try:
        return fn(*args, **kw)
    except errors.ConflictError as e:
        d = e.data if isinstance(e.data, dict) else {}
        if d.get("reason") != "address_not_here":
            raise
        raise errors.ConflictError(tr("gw_addresses.err.not_here_panel", ip=d.get("ip") or "?",
                                      known=", ".join(d.get("known") or []) or tr("gw_addresses.none")), data=d)


@route("POST", r"/api/gw/enable")
def gw_enable(req):
    b = req.json()
    res = _call(_known_host, ops.enable, runner(), base=_str(b, "base", max_len=32), host=_str(b, "host", max_len=253),
                known_only=True)                    # PLAN-multi-ip Ш2 (Л3): an IPv4 only one of «Адреса сервера»
    return ok(base=res.get("base"), host=res.get("host"), installed=res.get("installed") or [],
              warnings=_warnings(res))


@route("POST", r"/api/gw/disable")
def gw_disable(req):
    res = _call(ops.disable, runner())
    return ok(warnings=_warnings(res))


@route("GET", r"/api/gw/addresses", runs=True)      # ip, curl (the echo with ?refresh) — the addresses of the machine
def gw_addresses(req):
    """«Адреса сервера» (3.3.7.15 F10): the document of ``kaskadctl gw addresses --json`` (``addresses`` IPv4, ``v6``,
    ``addresses6``), where the inbounds take connections (``mip``: ``in``, ``in6``; ``route_out`` — the exits without an
    address of their own that go from the route address while it is not «для выхода», as ``kaskadctl gw addresses``),
    the rows of the page with the codes of their states (``page``) and ``ver`` — the version every write of
    ``/api/gw/addresses/*`` sends back. The probes of the network (NAT, the echo of IPv6) at most once in 10 minutes,
    ``?refresh=1`` — now."""
    from ..gw import addresses, mip, xray
    refresh = (req.query.get("refresh") or "").lower() in ("1", "true", "yes", "on")
    try:
        pool = exits.pool(None, roles=exits.ROLES)
    except KaskadError:
        pool = []                                       # an unreadable pool: no ``route_out``, the page still opens
    try:
        st = _st()
        doc = addresses.collect(runner(), st, refresh=refresh)
        md = mip.status(st, pool_exits=pool)            # REV-3315-b3 З-2: the pool, as the CLI — ``route_out`` here too
    except KaskadError as e:
        raise routes.core_error(e)
    out = dict(doc, ok=True, mip=md, page=addresses.page(st, doc), ver=mip.addresses_ver(st))
    failed = xray.after_failed()                        # REV-3315-p3 З-1: a write said «готово», xray did not follow
    out["warnings"] = [tr("gw_addresses.warn.xray_after_failed", err=failed["err"])] if failed else []
    return json_response(out)


def _xray_after(req, fn, *args, **kw):
    """3.3.7.15 П3 (T-3315 п. 1.7): a write of «Адреса сервера» through the core (``_call``) whose apply restarts
    xray — and the panel may be reached through that very xray (the domain on 443: VLESS TLS in front of the site):
    the restart inside the request cut its own answer («Empty reply from server», the change applied). Here the
    restart waits for the answer (``xray.after_answer``: the file not replaced, ``xray -test`` first, the collector
    held back) and runs right after it has left (``req.after`` -> ``ops.xray_after_answer``); the result gets
    ``xray_after`` True (the page reads the state again a moment later), a failure — the journal and a warning of
    ``GET /api/gw/addresses``, the collector applies on its next tick. The listener ``local`` (the SSH tunnel, never
    behind xray) — the restart inside the request as before, its failure in the answer (REV-3315-p3 З-1). No restart
    needed — nothing owed."""
    if req.listener == "local":
        res = _call(fn, *args, **kw)
        res["xray_after"] = False
        return res
    from ..gw import xray
    with xray.after_answer() as owed:
        try:
            res = _call(fn, *args, **kw)
            res["xray_after"] = bool(owed["token"])
            return res
        finally:
            token = owed["token"]
            if token:
                req.after.append(lambda: ops.xray_after_answer(runner(), token))


def _addr_body(b, allowed):
    """F11: a body of ``/api/gw/addresses/*`` — a field not of ``allowed`` (and ``ver``) is 400 (SPEC-R1 §3 «Общее»)."""
    extra = sorted(k for k in b if k not in allowed and k != "ver")
    if extra:
        _refused(pops.bad_field(extra[0]))


def _addr_ips(b):
    ips = b.get("ips")
    if ips is None:
        return []
    if not isinstance(ips, list) or len(ips) > 64 or not all(isinstance(x, str) and len(x) <= 64 for x in ips):
        raise HttpError(400, tr("panel.api.badIps"))
    return ips


@route("PUT", r"/api/gw/addresses/in")
def gw_addresses_in(req):
    """PLAN-multi-ip Ш3: ``{"ips": [IP, …]}`` — the inbounds take connections only on these addresses, ``{"all": true}``
    — on every one again (IPv6 too, F11). The core checks «Защита доступа» (the address for clients, of the panel and
    the subscriptions, and the one this request came to — a refusal 409); F11: the domain of the panel leading to a
    closed address — 409 ``need: "confirm"`` with ``consequences``, the same body with ``"yes": true`` writes. ``ver``
    of ``GET /api/gw/addresses`` — required (400 ``ver_required``; another — 409 ``object_changed``)."""
    from ..gw import mip
    b = req.json()
    _addr_body(b, ("ips", "all", "yes"))
    every = bool(_bool(b, "all", False))
    ips = _addr_ips(b)
    want = _need_ver(req, b)
    # the address the request came to: the open mode — its socket, behind nginx — the header of our vhost (С-2)
    res = _xray_after(req, mip.set_in, runner(), ips=ips, every=every, yes=bool(_bool(b, "yes", False)),
                local=req.local_ip if req.listener in ("public", "proxy") else None, env={}, back=tr("gw_mip.back_panel"),
                want_ver=want, panel=True)
    return ok(**{"in": res["in"], "text": res["text"], "warnings": _warnings(res),
                 "ver": mip.addresses_ver(state.load()), "xray_after": res["xray_after"]})


@route("PUT", r"/api/gw/addresses/in6")
def gw_addresses_in6(req):
    """3.3.7.15 F11: ``{"ips": [IPv6, …]}`` — only these IPv6 take connections, ``{"none": true}`` — no IPv6,
    ``{"all": true}`` — every IPv6 again; ``yes`` — the locks confirmed (409 ``need: "confirm"`` without it: the panel
    opened over the IPv6 it closes, an AAAA record of a name the devices get); ``ver`` required."""
    from ..gw import mip
    b = req.json()
    _addr_body(b, ("ips", "all", "none", "yes"))
    every, none = bool(_bool(b, "all", False)), bool(_bool(b, "none", False))
    ips = _addr_ips(b)
    want = _need_ver(req, b)
    res = _xray_after(req, mip.set_in6, runner(), ips=ips, every=every, none=none, yes=bool(_bool(b, "yes", False)),
                local=req.local_ip if req.listener in ("public", "proxy") else None, env={}, back=tr("gw_mip.back_panel"),
                want_ver=want, panel=True)
    return ok(**{"in6": res["in6"], "text": res["text"], "warnings": _warnings(res),
                 "ver": mip.addresses_ver(state.load()), "xray_after": res["xray_after"]})


@route("PUT", r"/api/gw/addresses/give")
def gw_addresses_give(req):
    """3.3.7.15 F13: ``{"ip6": IPv6 | null, "yes"?, "ver"}`` — «принимать и выдавать» of an IPv6: the subscription gives
    it as an extra line after the IPv4 ones (null — no IPv6 line). A restart of xray (VLESS Reality listens on IPv6 by
    this choice) — 409 ``need: "confirm"`` with ``consequences``, the same body with ``"yes": true`` writes; an IPv4 —
    refused (the next release); ``ver`` of ``GET /api/gw/addresses`` required."""
    from ..gw import mip
    b = req.json()
    _addr_body(b, ("ip6", "yes"))
    if "ip6" not in b or not (b["ip6"] is None or isinstance(b["ip6"], str) and 0 < len(b["ip6"]) <= 64):
        raise HttpError(400, tr("gw_mip.err.give6_body"))
    want = _need_ver(req, b)
    res = _xray_after(req, mip.set_give6, runner(), ip=b["ip6"], yes=bool(_bool(b, "yes", False)), want_ver=want,
                      panel=True)
    return ok(give6=res["give6"], text=res["text"], warnings=_warnings(res), ver=mip.addresses_ver(state.load()),
              xray_after=res["xray_after"])


@route("PUT", r"/api/gw/addresses/direct")
def gw_addresses_direct(req):
    """3.3.7.15 F12а: ``{"ip": IPv4 | null, "ver"}`` — «Напрямую — с адреса»: the traffic «напрямую» of every inbound
    leaves from this address (null — from the address of the route, as before). Refused in words with the way back:
    not here, not «для выхода», another way out than the default route, an IPv6."""
    from ..gw import mip
    b = req.json()
    _addr_body(b, ("ip",))
    if "ip" not in b or not (b["ip"] is None or isinstance(b["ip"], str) and 0 < len(b["ip"]) <= 64):
        raise HttpError(400, tr("gw_mip.err.direct_body"))
    want = _need_ver(req, b)
    res = _xray_after(req, mip.set_direct, runner(), ip=b["ip"], want_ver=want)
    return ok(direct=res["direct"], text=res["text"], warnings=_warnings(res), ver=mip.addresses_ver(state.load()),
              xray_after=res["xray_after"])


@route("PUT", r"/api/gw/addresses/out")
def gw_addresses_out(req):
    """3.3.7.15 F12а: ``{"ip": IPv4, "on": bool, "yes"?, "ver"}`` — «Для выхода: да / нет». «Нет» on the address
    «напрямую» or exits go from — 409 ``need: "confirm"`` with the plan of the move (``consequences``), the same body
    with ``"yes": true`` moves them in the same write; nowhere to move — 409 in words."""
    from ..gw import mip
    b = req.json()
    _addr_body(b, ("ip", "on", "yes"))
    ip = _str(b, "ip", max_len=64)
    if not ip or not isinstance(b.get("on"), bool):
        raise HttpError(400, tr("gw_mip.err.out_body"))
    want = _need_ver(req, b)
    res = _xray_after(req, mip.set_out, runner(), ip, b["on"], yes=bool(_bool(b, "yes", False)), want_ver=want)
    return ok(out=res["out"], direct=res["direct"], moved=res["moved"], text=res["text"], warnings=_warnings(res),
              ver=mip.addresses_ver(state.load()), xray_after=res["xray_after"])


@route("POST", r"/api/gw/addresses/reset")
def gw_addresses_reset(req):
    """3.3.7.15 F12а: «Сбросить выбор» — ``out``, ``direct``, ``give`` back to none (the address of the route, no IPv6
    line); «Приём» stays. ``{"yes"?, "ver"}`` — an IPv6 given while Reality listens on it: 409 ``need: "confirm"``
    first (xray restarts, F13 С-5)."""
    from ..gw import mip
    b = req.json()
    _addr_body(b, ("yes",))
    want = _need_ver(req, b)
    res = _xray_after(req, mip.reset_roles, runner(), yes=bool(_bool(b, "yes", False)), want_ver=want)
    return ok(text=res["text"], warnings=_warnings(res), ver=mip.addresses_ver(state.load()),
              xray_after=res["xray_after"])


@route("PUT", r"/api/gw/addresses/exit")
def gw_addresses_exit(req):
    """Ш4: ``{"id": exit | "group": gid, "ip": IP | "main", "ver"}`` — the address the exit (each exit of the group) is
    reached from; ``ver`` of ``GET /api/gw/addresses`` required (F11, З-3)."""
    from ..gw import mip
    b = req.json()
    _addr_body(b, ("id", "group", "ip"))
    eid, gid, ip = _str(b, "id", max_len=40), _str(b, "group", max_len=40), _str(b, "ip", max_len=64)
    if bool(eid) == bool(gid) or not ip:
        raise HttpError(400, tr("gw_mip.err.exit_or_group"))
    want = _need_ver(req, b)
    res = _xray_after(req, mip.set_src, runner(), [eid] if eid else [], ip=ip, group=gid or None, want_ver=want)
    return ok(ids=res["ids"], src=res["src"], text=res["text"], warnings=_warnings(res),
              ver=mip.addresses_ver(state.load()), xray_after=res["xray_after"])


@route("POST", r"/api/gw/addresses/spread")
def gw_addresses_spread(req):
    """3.3.7.15 F12б (КТ): «Разнести выходы по адресам» (``gw/mip_spread.py``) — the exits without an address of their
    own get one of the addresses «для выхода». ``{"dry"?, "yes"?, "ver"}`` (or ``?dry=1``): the dry run — the plan
    (``moves``, ``skipped``, ``consequences``), nothing written, ``ver`` not needed; the write — ``ver`` required, without
    ``yes`` 409 ``need: "confirm"`` with ``consequences``. Fewer than two addresses «для выхода» — 409 in words. The way
    back — ``PUT …/exit`` with ``"ip": "main"``."""
    from ..gw import mip, mip_spread
    b = req.json()
    _addr_body(b, ("dry", "yes"))
    dry = bool(_bool(b, "dry", False)) or _dry(req)
    want = _need_ver(req, b, dry=dry)
    yes = bool(_bool(b, "yes", False))
    if dry:                                             # a dry run changes nothing the tabs show (no generation)
        try:
            res = mip_spread.spread_exits(runner(), dry=True, want_ver=want)
        except KaskadError as e:
            raise routes.core_error(e)
    else:
        res = _xray_after(req, mip_spread.spread_exits, runner(), yes=yes, want_ver=want)
    return ok(dry=res["dry"], moves=res["moves"], skipped=res["skipped"], consequences=res["consequences"],
              flushed=res["flushed"], text=res["text"], warnings=_warnings(res), ver=mip.addresses_ver(state.load()),
              xray_after=bool(res.get("xray_after")))


@route("PUT", r"/api/gw/host")
def gw_host(req):
    """The address for clients: ``host`` (an IP or a name), ``detect`` (the address of the route, as before) or
    ``to_ip`` (круг 1 №39 «Вернуть на IP»: the detected PUBLIC IPv4 of the node instead of the domain; a private one —
    409, the address stays)."""
    b = req.json()
    host = _str(b, "host", max_len=253)
    detect = bool(_bool(b, "detect", False))
    to_ip = bool(_bool(b, "to_ip", False))
    if not host and not detect and not to_ip:
        raise HttpError(400, tr("panel.api.hostMissing"))
    if host:
        res = _call(_known_host, ops.host, runner(), value=host, known_only=True)   # Ш2 (Л3): another IPv4 — 409
    else:
        res = _call(ops.host, runner(), detect=True, public=to_ip)
    return ok(host=res["host"], auto=res["auto"], how=res.get("how"), warnings=_warnings(res))


@route("PUT", r"/api/gw/route")
def gw_route(req):
    """The default route of the node (§7.9, проверка 19, З-4): ``{via, fail, ver}``, ``?dry=1``; without ``ver`` — 400
    ``ver_required``, another — 409 ``object_changed``."""
    b = req.json()
    via, fail = _route_args(b)
    if not via:
        raise HttpError(400, tr("panel.api.routeMissing"))
    dry = _dry(req)
    want = _need_ver(req, b, dry)
    res = _pcall(pops.set_route, runner(), "default", None, via, fail, want_ver=want, dry=dry)
    if dry:
        return ok(**dict((k, v) for k, v in res.items() if k != "ok"))
    return ok(route=res["route"], ver=res["ver"], moves=res.get("moves"), warnings=_warnings(res))


@route("GET", r"/api/gw/inbounds")
def gw_inbounds(req):
    doc, age, fresh = _live_now()

    def view():
        st = _st()
        d, _ = live.read()
        return {"ok": True, "inbounds": _inbound_rows(st, d if fresh else None)}
    core = snapshot.derived(("inbounds", fresh, weblang.current()),
                            [paths.state(), paths.live_json(), inlog.seen_path()], view)
    out = dict(core)
    out["inbounds"] = _with_keys(core["inbounds"])
    out["live"] = _live_info(doc, age, fresh)
    return json_response(out)


_KIND = r"(?P<kind>[a-z0-9]{2,8})"


@route("POST", r"/api/gw/inbounds/%s/(?P<verb>install|uninstall|enable|disable)" % _KIND)
def gw_inbound_action(req):
    kind, verb = req.match.group("kind"), req.match.group("verb")
    b = req.json()
    if verb == "install":
        port = _port(b.get("port"), "port")
        res = _call(ops.inbound_install, runner(), kind, port=port, force=bool(_bool(b, "force", False)))
        return ok(kind=kind, record=res.get("record"), notes=res.get("notes") or [], previous=res.get("previous"),
                  warnings=_warnings(res))
    if verb == "uninstall":
        res = _call(ops.inbound_uninstall, runner(), kind)
    else:
        res = _call(ops.inbound_enable, runner(), kind, verb == "enable")
    return ok(kind=kind, warnings=_warnings(res))


@route("GET", r"/api/gw/inbounds/%s/port" % _KIND, runs=True)      # ss, iptables — what holds the port
def gw_inbound_port_offer(req):
    """The port field of «Установить …?» (круг 1 №14), read-only: ``port`` — what to put in it at once, ``recommended``
    — the ports of the protocol with what holds them; ``?port=N`` — is N free (``check``, nothing installed);
    ``?random=1`` — a random free port. -> ops.port_offer."""
    port = _port(req.query.get("port"), "port")
    rnd = req.query.get("random")
    if rnd not in (None, "", "0", "1"):
        raise HttpError(400, tr("panel.api.badFlag", field="random"))
    try:
        res = ops.port_offer(runner(), req.match.group("kind"), port=port, shuffle=rnd == "1")
    except KaskadError as e:
        raise routes.core_error(e)
    return ok(**res)


@route("PUT", r"/api/gw/inbounds/%s/port" % _KIND)
def gw_inbound_port(req):
    b = req.json()
    proto = _str(b, "proto", max_len=3)
    if proto not in (None, "udp", "tcp"):
        raise HttpError(400, tr("panel.api.badProto"))
    res = _call(ops.inbound_port, runner(), req.match.group("kind"), _port(b.get("port"), "port", True), proto=proto,
                force=bool(_bool(b, "force", False)), who={"via": "panel", "ip": req.client_ip})
    return ok(kind=res["kind"], field=res["field"], old=res["old"], port=res["port"], reissue=res.get("reissue") or [],
              text=res.get("text") or "", warnings=_warnings(res))


SETTINGS_FORM = ("port", "proto", "mtu", "tls", "dry", "yes", "force", "safe", "ver")


def _settings_mtu(b):
    """``mtu`` of the body: a string of digits -> int; anything else as it is — the core refuses a non-integer (true,
    1300.5, «abc») with the range of the protocol (400, ``gwmtu.manual_check``). None — not given."""
    v = b.get("mtu")
    if v is None or v == "":
        return None
    return int(v) if isinstance(v, str) and _PORT_RE.match(v) else v


@route("GET", r"/api/gw/inbounds/%s/settings" % _KIND, runs=True)      # ss — what holds the ports (port_offer)
def gw_inbound_settings_view(req):
    """«Настройки протокола» (3.3.7.15 F3): the port, the MTU with its range (F2), whom to give the settings again (F1),
    the last application («Вернуть прежние»), «порт закрыт?», ``ver`` for the write. -> ops.inbound_settings_view."""
    try:
        res = ops.inbound_settings_view(runner(), req.match.group("kind"))
    except KaskadError as e:
        raise routes.core_error(e)
    return ok(**dict((k, v) for k, v in res.items() if k != "ok"))


@route("PUT", r"/api/gw/inbounds/%s/settings" % _KIND)
def gw_inbound_settings(req):
    """F3: ``{port?, proto?, mtu?, tls?, dry?, yes?, force?, safe?, ver}`` — one application. ``dry`` (body or
    ``?dry=1``) — «Что изменится», nothing written, no ``ver`` needed; otherwise ``ver`` of ``GET …/settings`` is
    required (400 ``ver_required``; another — 409 ``object_changed``). A change that needs a confirmation without
    ``yes`` — 409 with the plan in ``data``. A field the form does not know — 400; ``net`` — refused with words."""
    b = req.json()
    extra = sorted(k for k in b if k not in SETTINGS_FORM and k != "net")
    if extra:
        _refused(pops.bad_field(extra[0]))
    proto = _str(b, "proto", max_len=3)
    if proto not in (None, "udp", "tcp"):
        raise HttpError(400, tr("panel.api.badProto"))
    tls = _str(b, "tls", max_len=8)
    dry = bool(_bool(b, "dry", False)) or _dry(req)
    want = _need_ver(req, b, dry)
    res = _call(ops.inbound_settings, runner(), req.match.group("kind"), port=_port(b.get("port"), "port"), proto=proto,
                mtu=_settings_mtu(b), tls=tls, net=b.get("net"), dry=dry, yes=bool(_bool(b, "yes", False)),
                force=bool(_bool(b, "force", False)), safe=bool(_bool(b, "safe", False)),
                who={"via": "panel", "ip": req.client_ip}, ver=want)
    out = dict((k, v) for k, v in res.items() if k not in ("ok", "apply"))
    out["warnings"] = _warnings(res)
    return ok(**out)


@route("POST", r"/api/gw/inbounds/%s/settings/undo" % _KIND)
def gw_inbound_settings_undo(req):
    """F3 «Вернуть прежние настройки»: the last application of the protocol undone (409 — its old port is taken now).
    ``{yes?}`` (3.3.7.15 П6): a restart of a service other protocols share (accel-ppp with live sessions, the xray of
    the gateway) or the cut of the link of the administrator himself (П7: his address in a pool of the protocol) without
    ``yes`` — 409 ``need: "yes"`` with the plan in ``data`` (the page asks, then sends ``yes``)."""
    b = req.json()
    res = _call(ops.inbound_settings_undo, runner(), req.match.group("kind"), yes=bool(_bool(b, "yes", False)),
                who={"via": "panel", "ip": req.client_ip})
    out = dict((k, v) for k, v in res.items() if k not in ("ok", "apply"))
    out["warnings"] = _warnings(res)
    return ok(**out)


@route("PUT", r"/api/gw/inbounds/%s/option" % _KIND)
def gw_inbound_option(req):
    b = req.json()
    value = b.get("value")
    if isinstance(value, bool):
        value = "on" if value else "off"
    res = _call(ops.inbound_option, runner(), req.match.group("kind"), _str(b, "key", True, 32),
                _str({"value": value}, "value", True, 8))
    return ok(kind=res["kind"], key=res["key"], value=res["value"], reissue=res.get("reissue") or [],
              text=res.get("text") or "", warnings=_warnings(res))


@route("GET", r"/api/gw/reissue")
def gw_reissue(req):
    """F1 (3.3.7.15): «ждут новых настроек» по всем протоколам — {kind: {title, hard: [{id, name, what}], soft}}
    (``reissue.view``; files only, nothing runs). Жёсткие — «не подключится — выдайте заново», мягкие — «желательно»."""
    from ..gw import reissue
    return ok(reissue=reissue.view(_st()))


@route("POST", r"/api/gw/hy2/apply")
def gw_hy2_apply(req):
    """Hysteria2, «Применить настройки» (REV-3373 З-3): the gateway from state, the copy of the certificate written
    again, xray restarted when it changed. ``reissue`` — only when a new pair had to be made."""
    res = _call(ops.hy2_apply, runner())
    return ok(kind="hy2", restarted=bool(res.get("restarted")), reissue=res.get("reissue") or [],
              text=res.get("text") or "", warnings=_warnings(res))


@route("PUT", r"/api/gw/hy2")
def gw_hy2_set(req):
    """Hysteria2: port hopping ``hop`` ("20000-20099" | "" — off) and the certificate it prefers ``cert``
    ("domain" | "self"). A busy range — 409 with the list of the busy ports; ``reissue`` — whom to give the links again."""
    b = req.json()
    hop = _str(b, "hop", max_len=24) if "hop" in b else None
    if "hop" in b and hop is None:
        hop = "off"
    cert = _str(b, "cert", max_len=8) if "cert" in b else None
    if hop is None and cert is None:
        raise HttpError(400, tr("panel.api.hy2Nothing"))
    res = _call(ops.hy2_set, runner(), hop=hop, cert=cert, force=bool(_bool(b, "force", False)))
    return ok(kind="hy2", hop=res.get("hop"), cert=res.get("cert"), cert_mode=res.get("cert_mode"),
              changed=res.get("changed") or [], reissue=res.get("reissue") or [], text=res.get("text") or "",
              warnings=_warnings(res))


# ── VLESS: the mode, the cover site and the keys of the node ───────────────

def _vless_masks(check):
    try:                                       # read-only: no generation bump (nothing changed)
        return json_response(ops.vless_masks(runner(), check=check))
    except KaskadError as e:
        raise routes.core_error(e)


@route("GET", r"/api/gw/vless/masks")
def gw_vless_masks(req):
    return _vless_masks(False)


@route("POST", r"/api/gw/vless/masks/check")
def gw_vless_masks_check(req):
    return _vless_masks(True)


@route("PUT", r"/api/gw/vless/mask")
def gw_vless_mask(req):
    from ..rules.panel import who
    b = req.json()
    accept = bool(_bool(b, "accept_risks", False))          # SEC-vless-sh12 №7: r1b -> a foreign cover asks r1a
    risks = _str(b, "risks", False, 8)
    if accept and not risks:
        raise HttpError(400, tr("gw_in_vless.risks_changed"), code=errors.USAGE)
    res = _call(ops.vless_mask, runner(), _str(b, "site", True, 253), check=bool(_bool(b, "check", True)),
                accept=accept, risks=risks or None, via="panel", who=who())
    return ok(sni=res["sni"], mask=res["mask"], mask_check=res["mask_check"], changed=res["changed"],
              reissue=res["reissue"], text=res["text"], warnings=_warnings(res))


@route("POST", r"/api/gw/vless/rekey")
def gw_vless_rekey(req):
    res = _call(ops.vless_rekey, runner())
    return ok(pub=res["pub"], short_id=res["short_id"], reissue=res["reissue"], text=res["text"],
              warnings=_warnings(res))


@route("POST", r"/api/gw/vless/mode")
def gw_vless_mode(req):
    """``gw vless mode`` (SPEC-vless-tls-default §3.5, §4.2): {mode: "tls" | "reality", site?, accept_risks?, risks?,
    yes?}. Reality with a domain needs ``accept_risks`` true with ``risks`` and ``site`` of the text shown (else 400 /
    409 «reality_consent» with data.block); the price of new links — 409 «vless_change» until ``yes``. ``who`` — the
    login of the panel."""
    from ..rules.panel import who
    b = req.json()
    mode = _str(b, "mode", True, 10)
    if mode not in ("tls", "reality"):
        raise HttpError(400, tr("gw_in_vless.bad_mode", mode=mode), code=errors.USAGE)
    accept = bool(_bool(b, "accept_risks", False))
    risks, site = _str(b, "risks", False, 8), _str(b, "site", False, 253)
    if accept and (not risks or not site):
        raise HttpError(400, tr("gw_in_vless.risks_changed"), code=errors.USAGE)
    res = _call(ops.vless_mode, runner(), mode, site=site or None, accept=accept, risks=risks or None, via="panel",
                who=who(), yes=bool(_bool(b, "yes", False)), no_check=not _bool(b, "check", True))
    return ok(changed=res.get("changed", False), security=res.get("security"), port=res.get("port"),
              want=res.get("want"), error=res.get("error") or "", restored=res.get("restored"),
              note=res.get("note") or "", reissue=res.get("reissue") or [], text=res.get("text") or "",
              warnings=_warnings(res))


_ASK = Once()                                   # GET /api/gw/vless/ask: one count at a time (panel.once)
_ASK_SITE_RX = re.compile(r"[A-Za-z0-9.-]{1,253}")


@route("GET", r"/api/gw/vless/ask", runs=True)      # ss, nginx -T, systemctl is-active — what the action asks with
def gw_vless_ask(req):
    """T-3378 Н-2: the windows of the consent and of the price read what the action asks — 200 with ``need``
    («reality_consent» | «vless_change» | null) and the data the core refuses the action with (``ops.vless_ask``);
    the action — POST/PUT/DELETE with the answer, as before (its refusal stays the last word). ``do`` — tls | reality |
    site_on | site_off | clear, ``site`` — the cover of Reality (a host name, own, auto)."""
    q = req.query or {}
    for k, v in q.items():
        if k not in ("do", "site"):
            raise HttpError(400, tr("panel.bench.badQuery", field=k[:32]), code=errors.USAGE, data={"field": "query"})
        if not isinstance(v, str) or len(v) > 253:
            raise HttpError(400, tr("panel.bench.badValue", field=k), code=errors.USAGE, data={"field": k})
    do, site = q.get("do") or "", q.get("site") or ""
    if do not in ops.ASK_DOS:
        raise HttpError(400, tr("gw_in_vless.bad_mode", mode=do[:16]), code=errors.USAGE, data={"field": "do"})
    if site and not _ASK_SITE_RX.fullmatch(site):
        raise HttpError(400, tr("panel.bench.badValue", field="site"), code=errors.USAGE, data={"field": "site"})
    return json_response(_ASK((do, site), lambda: ops.vless_ask(runner(), do, site=site or None)))


@route("POST", r"/api/gw/vless/sync")
def gw_vless_sync(req):
    """The mode of VLESS by the node's site now: TLS with its domain or Reality (site.front.sync) -> reissue."""
    res = _call(ops.vless_sync, runner())
    return ok(changed=res.get("changed", False), security=res.get("security"), port=res.get("port"),
              why=res.get("why") or "", error=res.get("error") or "", reissue=res.get("reissue") or [],
              text=res.get("text") or "", warnings=_warnings(res))


# ── people ─────────────────────────────────────────────────────────────────

def _people_view(fresh):
    st = _st()
    g = model.peek(st)
    doc, _ = live.read()
    ldev = ((doc or {}).get("devices") or {}) if fresh else None
    names = dict((u.get("id"), u.get("name")) for u in g["users"])
    pages = _pages()
    subs = _subs()
    # every route with ``fail_eff`` — the fail step as it acts (its own policy or its group's) — and the name of its
    # group: the panel says «при отказе — напрямую» where the traffic goes from the node's address (REV-s1-b4 З-1)
    pol = exits.local_policy(st)
    try:
        pool_ids = [eid for eid, _ in exits.line_ids()]
    except (OSError, ValueError):
        pool_ids = []

    def rv(obj, key):
        if isinstance(obj.get(key), dict):
            obj[key] = exits.route_view(st, obj[key], pol)
        return obj

    users = [rv(dict(u, devices=len([d for d in g["devices"] if d.get("user") == u.get("id")]),
                     page=bool(pages.get(u.get("id"))), sub=bool(subs.get(u.get("id"))),
                     route_ver=pops.route_ver(u)), "route") for u in g["users"]]
    nets = _nets(st, doc if fresh else None)
    ru_nets = _ru_nets(st, doc if fresh else None)
    devs = []
    for d in g["devices"]:
        r = rv(dict(d, route_ver=pops.route_ver(d)), "route")
        r["route_effective"] = exits.route_view(st, model.route_of(st, d), pol)
        r["user_name"] = names.get(d.get("user"), "")
        r["live"] = ldev.get(d.get("id")) if ldev is not None else None
        r["net"] = nets.get(d.get("id"))
        r.update(_ru_fields(st, d, ru_nets.get(d.get("id"))))
        devs.append(r)
    for u in users:                                     # a person: the step of the worst of his devices
        mine = [nets.get(d.get("id")) for d in g["devices"] if d.get("user") == u.get("id") and nets.get(d.get("id"))]
        u["net"] = sorted(mine, key=lambda n: _STEP_ORDER.get(n["step"], 0))[-1] if mine else None
        ru_mine = [ru_nets.get(d.get("id")) for d in g["devices"] if d.get("user") == u.get("id")
                   and (ru_nets.get(d.get("id")) or {}).get("step") in _STEP_ORDER]
        u.update(_ru_fields(st, {"user": u.get("id")}, sorted(ru_mine, key=lambda n: _STEP_ORDER[n["step"]])[-1]
                            if ru_mine else None))
    return {"ok": True, "users": users, "devices": devs,
            "routes": {"default": exits.route_view(st, model.route_of(st, {"user": None}), pol)},
            "via_fails": exits.via_fails(st, pol, pool_ids),
            "inbounds": [{"kind": k, "title": _title(k), "enabled": k in model.enabled_kinds(st)}
                         for k in model.installed_kinds(st)],
            "platforms": list(model.PLATFORMS), "device_kinds": list(model.DEVICE_KINDS)}


_STEP_ORDER = {"exit": 0, "last_direct": 1, "last_block": 2}


def _nets(st, lv):
    """{device id: {step, group, since, missing}} (§7.9 ``GET /api/gw/people``, §4.4, §7.3): the step of the device by
    the decision of the collector (``live.no_exit``: on the last step of its group — since when; else ``exit``), its
    group, ``missing`` — its own route goes to an exit gone from the pool (§2.2). Files only."""
    from ..gw import policy as gpol
    out = {}
    no_exit = ((lv or {}).get("no_exit") or {}) if isinstance(lv, dict) else {}
    try:
        dec = exits.decision(st)
    except (KaskadError, OSError, ValueError):
        dec = {"states": {}, "policy": gpol.load(st)}
    for d in model.devices(st):
        r = model.route_of(st, d)
        if r.get("via") in ("direct", "block"):
            out[d["id"]] = None                         # its own route: not through the exits
            continue
        try:
            gid, _grp, missing = exits.route_group(st, r, dec)
        except (KaskadError, ValueError):
            gid, missing = None, False
        ne = no_exit.get(d["id"]) if isinstance(no_exit.get(d["id"]), dict) else None
        out[d["id"]] = {"step": ne.get("step") if ne else "exit", "group": (ne or {}).get("group") or gid,
                        "since": (ne or {}).get("since"), "missing": bool(missing)}
    return out


def _ru_nets(st, lv):
    """{device id: {step, group, since, exit}} — «Российские сайты» of the device by the decision of the collector (T15
    Q6, SPEC-groups §8.3, §8.9 ``ru_net``). Files only (state, live.json, pins.json):
        direct      ``ru_via`` direct (the default): directly from the node, as before;
        exit        pinned in its Russian group — ``exit``, the Russian exit its Russian sites go through;
        last_direct / last_block   the group on its last step (``live.no_exit_ru``) — since when;
        none        a group is chosen but the collector holds no place there: «Россия напрямую» does not act for the
                    device (the profile, the consent ⚠РФ, the lists, a pause) or its first tick has not come."""
    from ..gw import pins as PN
    no_ru = ((lv or {}).get("no_exit_ru") or {}) if isinstance(lv, dict) else {}
    try:
        held = PN.read().get("groups") or {}
    except (KaskadError, OSError, ValueError):
        held = {}
    out = {}
    for d in model.devices(st):
        via = model.ru_via_of(st, d)["via"]
        if via == model.RU_VIA_DIRECT:
            out[d["id"]] = {"step": "direct", "group": None, "since": None, "exit": None}
            continue
        ne = no_ru.get(d["id"]) if isinstance(no_ru.get(d["id"]), dict) else None
        if ne and ne.get("group") == via and ne.get("step") in ("last_direct", "last_block"):
            out[d["id"]] = {"step": ne["step"], "group": via, "since": ne.get("since"), "exit": None}
            continue
        pin = (((held.get(via) or {}) if isinstance(held.get(via), dict) else {}).get("pins") or {}).get(d["id"])
        if isinstance(pin, dict) and pin.get("exit"):
            out[d["id"]] = {"step": "exit", "group": via, "since": None, "exit": pin["exit"]}
        else:
            out[d["id"]] = {"step": "none", "group": via, "since": None, "exit": None}
    return out


def _ru_fields(st, obj, ru_net):
    """``ru_via`` (the own value, ``inherit`` when none), ``ru`` ({via, from} — ``model.ru_via_of``; for a person —
    what a device of theirs without its own value gets) and ``ru_net`` of a person or a device (§8.9)."""
    own = obj.get("ru_via") if obj.get("id") else (model.find_user(st, obj.get("user")) or {}).get("ru_via")
    return {"ru_via": own if own not in (None, "") else model.RU_VIA_INHERIT, "ru": model.ru_via_of(st, obj),
            "ru_net": ru_net}


def _pages():
    """{user id: True} of the people whose page is on (kaskad.panel.userpage; {} without it)."""
    try:
        from . import userpage
    except ImportError:
        return {}
    return userpage.page_users()


def _subs():
    """{user id: True} of the people whose subscription for apps is on (kaskad.panel.userpage; {} without it)."""
    try:
        from . import userpage
    except ImportError:
        return {}
    return userpage.sub_users()


@route("GET", r"/api/gw/people")
def gw_people(req):
    doc, age, fresh = _live_now()
    core = snapshot.derived(("people", fresh, weblang.current()), [paths.state(), paths.live_json(), paths.secrets_file(),
                                                                   paths.egress_up(), exitsubs.path(), paths.pins_json(),
                                                                   exits.snapshot_path()],
                            lambda: _people_view(fresh))
    out = dict(core)
    out["live"] = _live_info(doc, age, fresh)
    return json_response(out)


_UID = r"(?P<id>u-[0-9a-f]{8})"
_DID = r"(?P<id>d-[0-9a-f]{8})"


@route("POST", r"/api/gw/users")
def gw_user_add(req):
    b = req.json()
    via, fail = _route_args(b)
    u = _call(ops.user_add, runner(), _str(b, "name", True, 64), via, fail)
    return ok(user=u)


@route("DELETE", r"/api/gw/users/%s" % _UID)
def gw_user_del(req):
    force = (req.query.get("force") or "").lower() in ("1", "true", "yes", "on")
    # missing_ok: a repeated DELETE (the browser re-sends it when the connection broke before the answer) — «уже удалён»
    res = _call(ops.user_del, runner(), req.match.group("id"), force=force, missing_ok=True)
    return ok(user=res["user"], devices=res["devices"], gone=bool(res.get("gone")), text=res.get("text") or "",
              warnings=_warnings(res))


@route("PUT", r"/api/gw/users/%s/route" % _UID)
def gw_user_route(req):
    """The route of a person with its ``ver`` (§7.9 «Версия объекта»), ``?dry=1``."""
    b = req.json()
    via, fail = _route_args(b)
    dry = _dry(req)
    want = _need_ver(req, b, dry)
    res = _pcall(pops.set_route, runner(), "user", req.match.group("id"), via or "inherit", fail, want_ver=want,
                 dry=dry)
    if dry:
        return ok(**dict((k, v) for k, v in res.items() if k != "ok"))
    return ok(user=res["user"], ver=res["ver"], moves=res.get("moves"), warnings=_warnings(res))


@route("PUT", r"/api/gw/users/%s/enabled" % _UID)
def gw_user_enabled(req):
    on = _bool(req.json(), "enabled", required=True)
    res = _call(ops.user_enable, runner(), req.match.group("id"), on)
    return ok(user=res["user"], warnings=_warnings(res))


@route("POST", r"/api/gw/devices")
def gw_device_add(req):
    b = req.json()
    via, fail = _route_args(b)
    res = _call(ops.device_add, runner(), _str(b, "user", True, 64), _str(b, "name", True, 64),
                kind=_str(b, "kind", max_len=16) or "other", platform=_str(b, "platform", max_len=16) or "other",
                access=_kinds(b.get("access")), via=via, fail=fail)
    return ok(device=res["device"], warnings=_warnings(res))


@route("DELETE", r"/api/gw/devices/%s" % _DID)
def gw_device_del(req):
    # missing_ok: a repeated DELETE (the browser re-sends it when the connection broke before the answer: the change
    # restarted xray that carried the panel) — «уже удалено», 200, not «Устройства d-… нет» (T-M6-qs330b №6)
    res = _call(ops.device_del, runner(), req.match.group("id"), missing_ok=True)
    return ok(device=res["device"], gone=bool(res.get("gone")), text=res.get("text") or "", warnings=_warnings(res))


@route("PUT", r"/api/gw/devices/%s/access" % _DID)
def gw_device_access(req):
    b = req.json()
    did = req.match.group("id")
    changes = []
    if "access" in b:
        want = _kinds(b.get("access"))
        try:
            d = model.get_device(state.load(), did)
        except KaskadError as e:
            raise routes.core_error(e)
        have = list(d.get("access") or [])
        changes = [(k, True) for k in want if k not in have] + [(k, False) for k in have if k not in want]
    elif "kind" in b:
        changes = [(_str(b, "kind", True, 16), _bool(b, "on", True))]
    else:
        raise HttpError(400, tr("panel.api.accessMissing"))
    warnings, dev = [], None
    for kind, on in changes:
        res = _call(ops.device_grant, runner(), did, kind, on=on)
        warnings.extend(_warnings(res))
        dev = res["device"]
    if dev is None:
        dev = model.find_device(state.load(), did)
    return ok(device=dev, changed=[{"kind": k, "on": on} for k, on in changes], warnings=warnings)


@route("PUT", r"/api/gw/devices/%s/route" % _DID)
def gw_device_route(req):
    """The route of a device with its ``ver`` (§7.9 «Версия объекта»), ``?dry=1``."""
    b = req.json()
    via, fail = _route_args(b)
    dry = _dry(req)
    want = _need_ver(req, b, dry)
    res = _pcall(pops.set_route, runner(), "device", req.match.group("id"), via or "inherit", fail, want_ver=want,
                 dry=dry)
    if dry:
        return ok(**dict((k, v) for k, v in res.items() if k != "ok"))
    return ok(device=res["device"], ver=res["ver"], moves=res.get("moves"), warnings=_warnings(res))


@route("PUT", r"/api/gw/devices/%s/enabled" % _DID)
def gw_device_enabled(req):
    on = _bool(req.json(), "enabled", required=True)
    res = _call(ops.device_enable, runner(), req.match.group("id"), on)
    return ok(device=res["device"], warnings=_warnings(res))


@route("POST", r"/api/gw/devices/%s/reset" % _DID)
def gw_device_reset(req):
    b = req.json()
    res = _call(ops.device_reset, runner(), req.match.group("id"), inbound=_str(b, "inbound", max_len=16))
    return ok(device=res["device"], kinds=res["kinds"], also=res["also"], warnings=_warnings(res))


@route("POST", r"/api/gw/devices/%s/issue" % _DID)
def gw_device_issue(req):
    """Settings with secrets: never cached, never in a view or an event. Under the state lock: the core's
    address context (addr.using) is global to the process and a change may run in another thread."""
    b = req.json()
    try:
        with state.locked(timeout=5):
            res = ops.issue(runner(), req.match.group("id"), inbound=_str(b, "inbound", max_len=16),
                            os_name=_str(b, "os", max_len=16), where="panel")      # «Скачать», «Скопировать»
    except KaskadError as e:
        raise routes.core_error(e)
    return json_response(res, headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})


# ── renames (T13, SPEC-stage1-rename §4.1): the name changes, the id never; a light write — no apply, no ``rev`` ──────

def _name_of(b, key="name"):
    if key not in b:
        raise HttpError(400, tr("panel.api.fieldMissing", field=key))
    v = b.get(key)
    if v is not None and not isinstance(v, str):
        raise HttpError(400, tr("panel.api.nameString"))
    return v or ""


def _renamed(res):
    return ok(what=res["what"], id=res["id"], name=res["name"], old=res["old"], shown=res["shown"],
              duplicates=res.get("duplicates") or [], note=res.get("note") or "")


def _rename(what, ref, name, b):
    from ..gw import rename
    return _renamed(_call(rename.rename, runner(), what, ref, name,
                          allow_duplicate=bool(_bool(b, "allow_duplicate", False)), via="panel"))


@route("PUT", r"/api/gw/users/%s/name" % _UID)
def gw_user_rename(req):
    b = req.json()
    return _rename("user", req.match.group("id"), _name_of(b), b)


@route("PUT", r"/api/gw/devices/%s/name" % _DID)
def gw_device_rename(req):
    b = req.json()
    return _rename("device", req.match.group("id"), _name_of(b), b)


@route("PUT", r"/api/gw/inbounds/%s/label" % _KIND)
def gw_inbound_label(req):
    b = req.json()
    return _rename("inbound", req.match.group("kind"), _name_of(b, "label" if "label" in b else "name"), b)


@route("PUT", r"/api/forwards/(?P<proto>tcp|udp)/(?P<port>[0-9]{1,5})/name")
def forwards_rename(req):
    b = req.json()
    name = _name_of(b)
    try:
        res = forwards.rename(req.match.group("proto"), req.match.group("port"), name,
                              allow_duplicate=bool(_bool(b, "allow_duplicate", False)), via="panel")
    except KaskadError as e:
        if (getattr(e, "data", None) or {}).get("gone"):
            raise HttpError(404, str(e), code="gone", data={"gone": True})
        raise routes.core_error(e)
    finally:
        _changed()
    return _renamed(res)


@route("GET", r"/api/gw/offers")
def gw_offers(req):
    p = (req.query.get("platform") or "other")[:16]
    if p not in model.PLATFORMS:
        raise HttpError(400, tr("panel.api.badPlatform", list=", ".join(model.PLATFORMS)))
    try:
        return json_response(ops.offers(p, _st()))
    except KaskadError as e:
        raise routes.core_error(e)


# ── exits ──────────────────────────────────────────────────────────────────

@route("GET", r"/api/gw/exits")
def gw_exits(req):
    def view():
        # stage 1 (§7.9, проверка 19, З-3): the version and the rows of ONE read — ``rev.current`` first, the rows of
        # the ``st`` it gives, the pool read after it
        rv, st = grev.current(_st())
        rows, err = _exit_rows(st, roles=exits.ROLES)
        pops.exit_fields(st, rows)
        offline = exits.offline_empty(st)
        out = {"ok": True, "exits": rows, "add": exits.add_hint(st), "offline": offline,
               "offline_text": tr(exits.NO_EXITS_OFFLINE) if offline else "", "rev": rv,
               "default_route": exits.route_view(st, exits.default_route(st), exits.local_policy(st)),
               "default_route_ver": pops.default_route_ver(st)}
        if err:
            out["error"] = err
        return out
    files = [paths.state(), exits.snapshot_path(), paths.egress_up(), paths.egress_parser(), exitsubs.path(),
             grev.ru_pool_path(), paths.pins_json(), paths.traffic_json(), paths.metrics_db()]
    _, _, fresh = _live_now()
    out = dict(snapshot.derived(("exits", weblang.current()), files, view))
    out["subs"] = exitsubs.listing()                  # «обновлена 3 ч назад» moves with the clock: never cached
    out["exits"] = _exits_now(out["exits"], model.enabled(_st()), fresh)
    return json_response(out)


DUP_EXIT_TEXT = "panel.api.dupExit"     # a key of the catalog (3.3.2)


EXIT_FORM = ("name", "quota", "speed_skip", "rev", "allow_duplicate")


@route("PUT", r"/api/gw/exits/(?P<id>e-[0-9a-f]{10})(?P<dup>-[0-9]{1,4})?")
def gw_exit_set(req):
    """The form of an exit (§7.9, D-080): ``name`` (no ``rev``), ``quota`` / ``speed_skip`` (with ``rev``), ``?dry=1``.
    The order of the checks — the row of §7.9: the body (empty — 400; ``enabled`` — 400 ``rev_required`` / ``use_enabled``;
    ``group``/``tier`` — 400 ``use_move``; ``quota``/``speed_skip`` without ``rev`` — 400 ``rev_required``), the exit
    (gone — 404, a repeat of a key — 409), ``rev``, the name."""
    b = req.json()
    dry = _dry(req)
    if not b:
        raise HttpError(400, tr("panel.api.exitSetMissing"))
    if "enabled" in b:
        if b.get("rev") in (None, ""):
            _refused(pops.rev_required())
        _refused(pops._refuse("use_enabled", 400, errors.USAGE))
    if "group" in b or "tier" in b:
        _refused(pops._refuse("use_move", 400, errors.USAGE))
    extra = sorted(k for k in b if k not in EXIT_FORM)
    if extra:
        _refused(pops.bad_field(extra[0]))
    pol = "quota" in b or "speed_skip" in b
    want = _need_rev(req, b, dry) if pol else None
    if not pol and "name" not in b:
        raise HttpError(400, tr("panel.api.exitSetMissing"))
    if req.match.group("dup"):
        # the same key twice (kaskad.gw.exits.pool): its id carries the line number, so a name or a switch would be
        # lost with the next line removed above — and the gateway never uses it anyway. A clear answer, not 404
        raise HttpError(409, tr(DUP_EXIT_TEXT), errors.CONFLICT, data={"exit": req.match.group("id")})
    name = b.get("name")
    if name is not None and not isinstance(name, str):
        raise HttpError(400, tr("panel.api.nameString"))
    kw = {}
    if "quota" in b:
        kw["quota"] = b.get("quota")
    res = _pcall(pops.exit_set, runner(), req.match.group("id"), name=name,
                 speed_skip=_bool(b, "speed_skip"), allow_duplicate=bool(_bool(b, "allow_duplicate", False)),
                 want_rev=want, dry=dry, **kw)
    if dry:
        return ok(**dict((k, v) for k, v in res.items() if k != "ok"))
    extra = {}
    if res.get("rename"):                           # a name alone (T13): what is shown now and the hint
        r = res["rename"]
        extra = {"name": r["name"], "old": r["old"], "shown": r["shown"], "duplicates": r.get("duplicates") or [],
                 "note": r.get("note") or ""}
    return ok(id=res["id"], pref=res["pref"], rev=res["rev"], warnings=_warnings(res),
              **dict(extra, **({"moves": res["moves"]} if "moves" in res else {})))


@route("POST", r"/api/gw/exits")
def gw_exit_add(req):
    """A key of an exit into the pool (ops.exit_add): the text goes to the script on stdin and nowhere else — not into
    the answer, not into the log, not into an error."""
    key = _key_body(req)
    group = _str(req.json(), "group", max_len=16)
    pin = _str(req.json(), "pin_tofu", max_len=128)          # «Закрепить» of the preview (§9.1): the fingerprint shown
    res = _call(ops.exit_add, runner(), key, group=group, pin_tofu=pin)
    key = None
    return ok(added=res["added"], results=res.get("results") or [],
              warnings=[util.redact(w) for w in res.get("warnings") or []],
              notes=[util.redact(w) for w in res.get("notes") or []], text=res.get("text"), rev=res.get("rev"))


def _key_body(req):
    key = req.json().get("key")
    if not isinstance(key, str) or not key.strip():
        raise HttpError(400, tr("panel.api.pasteKey"))
    if len(key) > ops.KEY_MAX:
        raise HttpError(400, tr("panel.api.keyTooLong", kb=ops.KEY_MAX // 1024))
    return key


@route("POST", r"/api/gw/exits/preview")
def gw_exit_preview(req):
    """What a pasted text or a file holds — nothing written, the generation stays (a read in a POST: the key is a
    secret and never goes into a URL)."""
    key = _key_body(req)
    try:
        res = ops.exit_preview(runner(), key, tofu_by="panel" if _bool(req.json(), "tofu", False) else None)
    except KaskadError as e:
        raise routes.core_error(e)
    finally:
        key = None
    # module_build: adding these keys builds the AmneziaWG kernel module (1–3 min) — the panel says so before the wait
    return ok(items=res["items"], subscription=res["subscription"], text=res["text"],
              module_build=bool(res.get("module_build")))


@route("DELETE", r"/api/gw/exits/(?P<id>e-[0-9a-f]{10}(?:-[0-9]{1,4})?)")
def gw_exit_del(req):
    """«Удалить ключ» (§7.9, D-098): ``?rev=`` (400 ``rev_required`` without it), ``?dry=1``; the three steps of a write
    through the script of the pool (ops.exit_del); gone — 404 ``gone`` with ``data.gone``."""
    dry = _dry(req)
    want = _need_rev(req, None, dry)
    try:
        res = ops.exit_del(runner(), req.match.group("id"), want_rev=want, dry=dry)
    except KaskadError as e:
        raise routes.core_error(e)
    finally:
        if not dry:
            _changed()
    if dry:
        return ok(dry=True, id=res["id"], rev=res["rev"], moves=res["moves"], warnings=[])
    # one key twice (REV-335-2): where the routes went, in the words of the CLI and MCP (twin, moved, text)
    twin = {k: res[k] for k in ("twin", "moved", "text") if k in res}
    return ok(id=res["id"], left=res["left"], warnings=[util.redact(w) for w in res.get("warnings") or []],
              notes=[util.redact(w) for w in res.get("notes") or []], rev=res.get("rev"), moves=res.get("moves"),
              **twin)


# ── subscriptions of the exits (gw/exitsubs.py) ────────────────────────────
# The address is a secret: it comes in a POST body, is never answered, logged or put into an error.

@route("POST", r"/api/gw/exit-subs")
def gw_exit_sub_add(req):
    b = req.json()
    url = b.get("url")
    if not isinstance(url, str) or not url.strip():
        raise HttpError(400, tr(exitsubs.URL_TEXT))
    if len(url) > exitsubs.URL_MAX:
        raise HttpError(400, tr("panel.api.subUrlTooLong", max=exitsubs.URL_MAX))
    auto = _bool(b, "auto", True)
    res = _call(exitsubs.add, runner(), url, auto=auto)
    url = None
    return ok(sub=res["sub"], result=res["result"], note=res.get("note") or "", text=res["text"],
              warnings=[util.redact(w) for w in res.get("warnings") or []],
              notes=[util.redact(w) for w in res.get("notes") or []], rev=res.get("rev"))


@route("POST", r"/api/gw/exit-subs/refresh")
def gw_exit_subs_refresh(req):
    res = _call(exitsubs.refresh, runner(), None)
    return ok(all_ok=res["ok"], results=res["results"], text=res["text"], subs=exitsubs.listing(), rev=res.get("rev"))


@route("POST", r"/api/gw/exit-subs/(?P<id>s-[0-9a-f]{6})/refresh")
def gw_exit_sub_refresh(req):
    res = _call(exitsubs.refresh, runner(), req.match.group("id"))
    r = (res["results"] or [{}])[0]
    return ok(all_ok=res["ok"], result=r, text=res["text"], subs=exitsubs.listing(), rev=res.get("rev"))


@route("POST", r"/api/gw/exit-subs/(?P<id>s-[0-9a-f]{6})/restore")
def gw_exit_sub_restore(req):
    res = _call(exitsubs.restore, runner(), req.match.group("id"))
    return ok(all_ok=res["ok"], restored=res["restored"], result=res["result"], text=res["text"],
              subs=exitsubs.listing(), rev=res.get("rev"))


@route("PUT", r"/api/gw/exit-subs/(?P<id>s-[0-9a-f]{6})")
def gw_exit_sub_set(req):
    """``{auto}`` — daily or by hand; ``{label, allow_duplicate?}`` — the label of the subscription (T13; "" — the
    domain again). Both in one body — the label first."""
    b = req.json()
    if "label" not in b and "auto" not in b:
        raise HttpError(400, tr("panel.api.fieldMissing", field="auto"))
    out = {}
    if "label" in b:
        from ..gw import rename
        res = _call(rename.rename, runner(), "exitsub", req.match.group("id"), _name_of(b, "label"),
                    allow_duplicate=bool(_bool(b, "allow_duplicate", False)), via="panel")
        out.update(what=res["what"], id=res["id"], name=res["name"], old=res["old"], shown=res["shown"],
                   duplicates=res.get("duplicates") or [], note=res.get("note") or "")
    if "auto" in b:
        auto = _bool(b, "auto", required=True)
        out["sub"] = _call(exitsubs.set_auto, runner(), req.match.group("id"), auto)["sub"]
    return ok(rev=pops.current_rev(), **out)


@route("DELETE", r"/api/gw/exit-subs/(?P<id>s-[0-9a-f]{6})")
def gw_exit_sub_del(req):
    keep = (req.query.get("keep_keys") or "") in ("1", "true", "yes")
    try:
        res = exitsubs.delete(runner(), req.match.group("id"), keep_keys=keep)
    except KaskadError as e:
        if (getattr(e, "data", None) or {}).get("gone"):
            # §7.9 «Повтор DELETE» (D-132): 404 with the code ``gone`` and ``data.gone`` — the panel counts it a success
            raise HttpError(404, str(e), code="gone", data={"sub": req.match.group("id"), "gone": True})
        raise routes.core_error(e)
    finally:
        _changed()
    return ok(id=res["id"], removed=res["removed"], text=res["text"], rev=res.get("rev"))


# ── the exit policy: groups, tiers, buttons over one exit (stage 1, T7; SPEC-stage1-exits §7.9) ──────────────────────
# The writes of the list of «Версия политики» need ``rev`` (400 ``rev_required``), compare it under the lock of their
# own write (409 ``policy_changed``) and answer the new one; ``?dry=1`` — the same ``moves`` without a write. One exit,
# one group gone — 404 ``gone`` (``data.gone``) before the check of ``rev`` (a repeat of a key ``e-…-N`` — after it).

_EXIT_ID = r"(?P<id>e-[0-9a-f]{10})(?P<dup>-[0-9]{1,4})?"
_GROUP_ID = r"(?P<gid>main|g-[0-9a-f]{6})"


def _no_dup(req):
    if req.match.group("dup"):
        raise HttpError(409, tr(DUP_EXIT_TEXT), errors.CONFLICT, data={"exit": req.match.group("id")})
    return req.match.group("id")


def _answer(res):
    return ok(**dict((k, v) for k, v in res.items() if k != "ok"))


@route("GET", r"/api/gw/policy")
def gw_policy_get(req):
    try:
        return json_response(pops.view(_st()))
    except KaskadError as e:
        raise routes.core_error(e)


@route("PUT", r"/api/gw/policy")
def gw_policy_set(req):
    b = req.json()
    dry = _dry(req)
    want = _need_rev(req, b, dry)
    doc = b.get("policy") if isinstance(b.get("policy"), dict) else dict((k, v) for k, v in b.items() if k != "rev")
    return _answer(_pcall(pops.set_policy, runner(), doc, want_rev=want, dry=dry))


@route("POST", r"/api/gw/policy/reset")
def gw_policy_reset(req):
    b = req.json()
    dry = _dry(req)
    want = _need_rev(req, b, dry)
    return _answer(_pcall(pops.reset, runner(), preset=_str(b, "preset", max_len=16), group=_str(b, "group", max_len=16),
                          want_rev=want, dry=dry))


@route("POST", r"/api/gw/policy/hint-seen")
def gw_policy_hint_seen(req):
    """The banner «Появились группы выходов» closed (§7.1): ``hint_seen`` only, without ``rev`` and without an apply."""
    req.json()
    return ok(**dict((k, v) for k, v in _call(pops.hint_seen).items() if k != "ok"))


@route("POST", r"/api/gw/policy/check-url")
def gw_policy_check_url(req):
    """«Проверить ссылку» (§5.4 п. 7): 1 MB through the first working exit, at most once in 30 s per session and 20
    times an hour per node; the answer has only ``host/path`` of the final address."""
    from ..gw.probe import fetch
    url = _str(req.json(), "url", True, 2000)
    sid = (req.session or {}).get("id") or (req.session or {}).get("sid") or req.client_ip or "-"
    allowed, code, wait = fetch.LinkLimiter().allow(sid)
    if not allowed:
        raise HttpError(429, fetch.why_text(code, {"s": wait}), code=code, data={"wait": wait})
    st = _st()
    try:
        pool = exits.pool(None)
    except KaskadError as e:
        raise routes.core_error(e)
    alive = exits.alive_ids(exits.load_snapshot())
    via = next((fetch.via_of(e) for e in pool if e["id"] in alive and exits.parsed(e) and exits.role_of(e) == "abroad"
                and model.exit_pref(st, e["id"])["enabled"] and fetch.via_of(e) is not None), None)
    if via is None:
        return ok(code=None, mbps=None, final=fetch.shown(url), error_code="no_exit",
                  error=fetch.why_text("no_exit"))
    try:
        res = fetch.safe_get(url, via, lim=fetch.limits(1.0, 10.0), guard=fetch.guard_for(st, runner(), pool))
    except fetch.FetchError as e:
        return ok(code=None, mbps=None, final=fetch.shown(url), error_code=e.code, error=e.text())
    return ok(code=res.get("status"), mbps=res.get("mbps"), final=res.get("shown") or fetch.shown(url), error_code=None)


@route("POST", r"/api/gw/groups")
def gw_group_add(req):
    b = req.json()
    want = _need_rev(req, b)
    name = b.get("name")
    if not isinstance(name, str):
        raise HttpError(400, tr("panel.api.nameString"))
    res = _call(pops.group_add, runner(), name, role=_str(b, "role", max_len=8) or "abroad", fail=_str(b, "fail", max_len=8),
                allow_duplicate=bool(_bool(b, "allow_duplicate", False)), want_rev=want)
    return ok(id=res["id"], rev=res["rev"], warnings=_warnings(res))


@route("PUT", r"/api/gw/groups/%s" % _GROUP_ID)
def gw_group_rename(req):
    """The name of a group in place (D-086): ``{name, allow_duplicate?}``, without ``rev``."""
    b = req.json()
    if "name" not in b:
        raise HttpError(400, tr("panel.api.fieldMissing", field="name"))
    res = _call(pops.group_rename, runner(), req.match.group("gid"), b.get("name"),
                allow_duplicate=bool(_bool(b, "allow_duplicate", False)))
    return ok(id=res["id"], name=res["name"], label=res["label"], old=res["old"])


@route("DELETE", r"/api/gw/groups/%s" % _GROUP_ID)
def gw_group_delete(req):
    """«Удалить группу» (policy_ops.group_delete): ``?rev=`` (a dry run needs none), ``?dry=1``, where its parts go —
    ``exits_to``, ``reassign``, ``tg_proxy_to``, ``new_exits_to``, ``subs_to``. ``exits=drop`` (8а, a Russian group
    only) — its keys out of the pool with it: the dry run answers ``drop`` (the ids to delete), the write — ``dropped``;
    it runs the script of the pool (long — the panel waits as for «Удалить ключ»). Warnings of the script redacted.
    ``ask=1`` (a dry run only; пачка А, «лишняя 409 в консоли»): what the delete needs is the answer, not a refusal —
    the 409 ``need`` / ``ru_last`` of the core comes back as 200 ``{ask: {code, data}}`` (the window of the panel asks
    it first, and the browser logs every 4xx); other refusals stay as they are; without ``dry`` — 400 ``field``."""
    q = req.query
    dry = _dry(req)
    ask = q.get("ask")
    if ask is not None and (ask != "1" or not dry):
        _refused(pops.bad_field("ask"))
    want = _need_rev(req, None, dry)
    try:
        res = _pcall(pops.group_delete, runner(), req.match.group("gid"), exits_to=q.get("exits_to") or None,
                     reassign=q.get("reassign") or None, tg_proxy_to=q.get("tg_proxy_to") or None,
                     new_exits_to=q.get("new_exits_to") or None, subs_to=q.get("subs_to") or None,
                     want_rev=want, dry=dry, exits=q.get("exits") or None)
    except routes.HttpError as e:
        if ask and e.status == 409 and getattr(e, "code", None) in ("need", "ru_last"):
            return ok(ask={"code": e.code, "data": getattr(e, "data", None) or {}})
        raise
    res["warnings"] = [util.redact(w) for w in res.get("warnings") or [] if isinstance(w, str)]
    return _answer(res)


@route("POST", r"/api/gw/groups/%s/regroup" % _GROUP_ID)
def gw_group_regroup(req):
    """F14 «Разложить заново» (``policy_ops.group_regroup``): ``{dry?}`` (or ``?dry=1``), no other field; no ``rev`` or
    ``ver`` — the policy does not change, a repeat is harmless. The dry run — the moves of the next round (approximate);
    the write — ``{accepted: true}`` at once, the result comes as ``group_regrouped`` in the feed."""
    b = req.json()
    extra = sorted(k for k in b if k != "dry")
    if extra:
        _refused(pops.bad_field(extra[0]))
    dry = bool(_bool(b, "dry", False)) or _dry(req)
    return _answer(_pcall(pops.group_regroup, runner(), req.match.group("gid"), dry=dry))


# ── F7 (3.3.7.15, полоса Г): the safe apply — the wait, its confirmation, «Скачать мои новые настройки» ──────

@route("GET", r"/api/gw/pending")
def gw_pending_get(req):
    """The live wait of a safe apply: ``{pending: {scope, left, deadline, token, via} | null}`` (the token is no
    secret — confirming needs this session; a reloaded page confirms with it)."""
    from ..gw import pending
    return ok(pending=pending.view())


@route("POST", r"/api/gw/pending/confirm")
def gw_pending_confirm(req):
    """«Подтвердить» ``{token}``: 409 ``pending_gone`` — reverted already (the old settings work)."""
    from ..gw import pending
    b = req.json()
    extra = sorted(k for k in b if k != "token")
    if extra:
        _refused(pops.bad_field(extra[0]))
    token = _str(b, "token", required=True, max_len=16)
    return _answer(_call(pending.confirm, runner(), token))


_PREVIEW_KEYS = ("device", "port", "proto", "mtu", "tls", "base")       # base: F6 «Сеть шлюза»


@route("POST", r"/api/gw/inbounds/%s/settings/issue" % _KIND)
def gw_inbound_settings_issue(req):
    """«Скачать мои новые настройки» (F7): ``{device?, port?, proto?, mtu?, tls?, base?}`` — the settings of the device as
    they will be after the change, no state or mark written (only the note ``ops._preview_note``: the application of
    the same change counts it as «выдали», П5 З-2); no ``device`` — the one whose address in the pool is the address
    of this session. Secrets: as ``POST …/devices/<id>/issue`` — never cached, under the state lock (addr.using)."""
    b = req.json()
    extra = sorted(k for k in b if k not in _PREVIEW_KEYS)
    if extra:
        _refused(pops.bad_field(extra[0]))
    kind = req.match.group("kind")
    if registry.get(kind) is None:
        _refused(pops.bad_field("kind"))
    changes = {}
    if b.get("port") is not None:
        changes["port"] = _port(b.get("port"), "port")
    if b.get("mtu") is not None:
        if isinstance(b["mtu"], bool) or not isinstance(b["mtu"], int):
            _refused(pops.bad_field("mtu"))
        changes["mtu"] = b["mtu"]
    for key, n in (("proto", 3), ("tls", 8)):
        v = _str(b, key, max_len=n)
        if v is not None:
            changes[key] = v
    if "base" in b:                                     # F6: foreign input — a string only, never empty (400)
        v = b["base"]
        if not isinstance(v, str) or not v.strip() or len(v) > 32:
            _refused(pops.bad_field("base"))
        changes["base"] = v
    try:
        with state.locked(timeout=5):
            res = ops.issue_preview(runner(), _str(b, "device", max_len=64), kind, changes,
                                    who={"via": "panel", "ip": req.client_ip})
    except KaskadError as e:
        raise routes.core_error(e)
    return json_response(res, headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})


# ── F6 (3.3.7.15, полоса Г): «Настройки › Шлюз › Сеть шлюза» ──────────────────

_NET_FORM = ("base", "ver", "dry", "yes")


@route("GET", r"/api/gw/net", runs=True)                 # ip route / ip addr — the networks of the node
def gw_net_get(req):
    """«Сеть шлюза» (``ops.net_view``): the network, the candidates free or taken, what of the node holds private
    networks, ``suggest_base`` (Е3), the change «Вернуть прежнюю сеть» undoes, the live wait (F7), ``ver``."""
    try:
        res = ops.net_view(runner())
    except KaskadError as e:
        raise routes.core_error(e)
    return _answer(res)


@route("PUT", r"/api/gw/net/base")
def gw_net_base(req):
    """«Сменить сеть шлюза» ``{base, ver, dry?, yes?}`` (``ops.net_base``): ``dry`` (or ``?dry=1``) — «Что изменится»,
    nothing written, no ``ver``; otherwise ``ver`` of ``GET /api/gw/net`` (400 ``ver_required``; another — 409
    ``object_changed``). Without ``yes`` where it is needed — 409 with the plan; a live wait of F7 — 409
    ``pending_busy``; the answer carries ``pending`` (the administrator in the old or the new network). Any other
    field — 400."""
    b = req.json()
    extra = sorted(k for k in b if k not in _NET_FORM)
    if extra:
        _refused(pops.bad_field(extra[0]))
    dry = bool(_bool(b, "dry", False)) or _dry(req)
    want = _need_ver(req, b, dry)
    base = _str(b, "base", required=True, max_len=32)
    kw = dict(dry=dry, yes=bool(_bool(b, "yes", False)), who={"via": "panel", "ip": req.client_ip}, ver=want)
    if dry:
        try:
            res = ops.net_base(runner(), base, **kw)
        except KaskadError as e:
            raise routes.core_error(e)
    else:
        res = _call(ops.net_base, runner(), base, **kw)
    out = dict((k, v) for k, v in res.items() if k not in ("ok", "apply"))
    out["warnings"] = _warnings(res)
    return ok(**out)


@route("POST", r"/api/gw/net/undo")
def gw_net_undo(req):
    """«Вернуть прежнюю сеть» (``ops.net_base_undo``): no field; 400 — nothing to restore, 409 — the network changed
    since or the old one is taken now. Also 409 ``pending_busy`` — the wait of another protocol is live (F7: its timer
    reverts a port or MTU, not the network; ``data.pending`` {scope, left}); 400 with ``data.why = "rfc1918"`` — the old
    network is outside 10/8, 172.16/12, 192.168/16 (an older version took it; only its own wait brings it back)."""
    b = req.json()
    extra = sorted(b)
    if extra:
        _refused(pops.bad_field(extra[0]))
    res = _call(ops.net_base_undo, runner())
    out = dict((k, v) for k, v in res.items() if k not in ("ok", "apply"))
    out["warnings"] = _warnings(res)
    return ok(**out)


@route("POST", r"/api/gw/exits/%s/manual" % _EXIT_ID)
def gw_exit_manual(req):
    b = req.json()
    eid = _no_dup(req)
    dry = _dry(req)
    want = _need_rev(req, b, dry)
    return _answer(_pcall(pops.exit_manual, runner(), eid, _str(b, "state", True, 16), want_rev=want, dry=dry))


@route("POST", r"/api/gw/exits/%s/country" % _EXIT_ID)
def gw_exit_country(req):
    b = req.json()
    eid = _no_dup(req)
    dry = _dry(req)
    if b.get("accept") is not True:
        _refused(pops.bad_field("accept"))
    want = _need_rev(req, b, dry)
    return _answer(_pcall(pops.exit_country, runner(), eid, want_rev=want, dry=dry))


@route("POST", r"/api/gw/exits/%s/enabled" % _EXIT_ID)
def gw_exit_enabled(req):
    b = req.json()
    eid = _no_dup(req)
    dry = _dry(req)
    on = _bool(b, "on", required=True)
    want = _need_rev(req, b, dry)
    return _answer(_pcall(pops.exit_enabled, runner(), eid, on, want_rev=want, dry=dry))


@route("POST", r"/api/gw/exits/%s/move" % _EXIT_ID)
def gw_exit_move(req):
    b = req.json()
    eid = _no_dup(req)
    dry = _dry(req)
    want = _need_rev(req, b, dry)
    return _answer(_pcall(pops.exit_move, runner(), eid, _str(b, "group", True, 16), tier=_str(b, "tier", max_len=16),
                          confirm_role=bool(_bool(b, "confirm_role", False)), want_rev=want, dry=dry))


@route("POST", r"/api/gw/exits/%s/speedtest" % _EXIT_ID)
def gw_exit_speedtest(req):
    """«Проверить скорость сейчас» (§5.3): a job of the unit of probes; the result — an event and ``speed``."""
    from ..gw.probe import unit as probe_unit
    eid = _no_dup(req)
    if eid not in pops.pool_ids():
        _refused(pops.gone("exit", eid))
    force = bool(_bool(req.json(), "force", False))
    try:
        probe_unit.manual(runner(), eid, force=force)
    except KaskadError as e:
        raise routes.core_error(e)
    return json_response({"ok": True, "job": "%s%s" % (eid, ":force" if force else "")}, status=202)


def _int_q(req, key, default, lo, hi):
    v = req.query.get(key)
    if v in (None, ""):
        return default
    try:
        n = int(v)
    except ValueError:
        _refused(pops.bad_field(key))
    if not lo <= n <= hi:
        _refused(pops.bad_field(key))
    return n


@route("GET", r"/api/gw/exits/%s/history" % _EXIT_ID)
def gw_exit_history(req):
    from ..gw import metrics
    rng = req.query.get("range") or "1d"
    if rng not in metrics.RANGES:
        _refused(pops.bad_field("range"))
    return ok(**metrics.history(req.match.group("id") + (req.match.group("dup") or ""), rng))


_EXA = {"key": None, "body": None}                 # the line of «Выходы» of this process: one query a minute of m1
_EXA_LOCK = threading.Lock()


def _exa_mark(now):
    """The minute of m1 written last (live.json → metrics.ts, the collector) while live.json is fresh — the end the
    line shows; else the clock (a stopped collector writes nothing, the line ends «now»). Files only."""
    doc, age = live.read()
    m = (doc or {}).get("metrics") if doc is not None and live.fresh(age) else None
    ts = m.get("ts") if isinstance(m, dict) else None
    if isinstance(ts, int) and not isinstance(ts, bool) and 0 <= now - ts < 900:
        return ts + 60
    return None


@route("GET", r"/api/gw/exits/activity")
def gw_exits_activity(req):
    """3.3.7.10: the line of «Выходы» for all exits at once (``metrics.exit_summary``: 48 windows of 30 min, one query)
    — instead of the history of a day of every exit (1440 points each). Kept in the process by the minute of m1 the
    collector wrote last (``metrics.ts`` of live.json; without it — the minute of the clock): recounted at most once
    a minute whatever the number of tabs; ``ETag`` of that key, ``If-None-Match`` equal -> 304 without a body."""
    from ..gw import metrics
    now = int(time.time())
    mark = _exa_mark(now)
    end = mark if mark is not None else now // 60 * 60
    key = "%s|%s" % ("m" if mark is not None else "c", end)
    etag = '"exa-%s"' % hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    inm = req.headers.get("if-none-match") or ""
    if etag in [x.strip() for x in inm.split(",")]:
        return routes.Response(304, b"", {"ETag": etag, "Cache-Control": "no-store"})
    with _EXA_LOCK:
        if _EXA["key"] != key:
            _EXA.update(key=key, body=metrics.exit_summary(end))
        body = _EXA["body"]
    return _act_json(body, {"ETag": etag})


_EXH = {"key": None, "body": None}                 # «Выходы · час» of this process: one query a minute of m1
_EXH_LOCK = threading.Lock()


@route("GET", r"/api/gw/exits/hour")
def gw_exits_hour(req):
    """Круг 1, Т1: «Выходы · час» on the Сводка — the last 60 minutes of every exit by the minute (delay, loss,
    traffic, state; ``metrics.exit_hour``) in ONE answer. No parameters (any — 400 data.field); kept in the process by
    the minute of m1 written last, as /api/gw/exits/activity; ``ETag``, ``If-None-Match`` equal -> 304."""
    from ..gw import metrics
    for k in (req.query or {}):
        _refused(pops.bad_field(k if isinstance(k, str) and len(k) <= 32 else "query"))
    now = int(time.time())
    mark = _exa_mark(now)
    end = mark if mark is not None else now
    key = "%s|%s" % ("m" if mark is not None else "c", end // 60)
    etag = '"exh-%s"' % hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    inm = req.headers.get("if-none-match") or ""
    if etag in [x.strip() for x in inm.split(",")]:
        return routes.Response(304, b"", {"ETag": etag, "Cache-Control": "no-store"})
    with _EXH_LOCK:
        if _EXH["key"] != key:
            _EXH.update(key=key, body=metrics.exit_hour(end))
        body = _EXH["body"]
    return _act_json(body, {"ETag": etag})


@route("GET", r"/api/gw/exits/%s/transitions" % _EXIT_ID)
def gw_exit_transitions(req):
    from ..gw import metrics
    days = _int_q(req, "days", 7, 1, 90)
    limit = _int_q(req, "limit", 100, 1, 500)
    before = _int_q(req, "before", None, 0, 1 << 40)
    return ok(**metrics.transitions(req.match.group("id") + (req.match.group("dup") or ""), days=days, limit=limit,
                                    before=before))


@route("GET", r"/api/gw/probes/speed")
def gw_speed_get(req):
    return ok(**pops.speed_get(_st()))


@route("PUT", r"/api/gw/probes/speed")
def gw_speed_set(req):
    b = req.json()
    want = _need_ver(req, b)
    return _answer(_call(pops.speed_set, b, want_ver=want))


@route("GET", r"/api/gw/metrics")
def gw_metrics_get(req):
    return ok(**pops.metrics_get(_st()))


@route("PUT", r"/api/gw/metrics")
def gw_metrics_set(req):
    b = req.json()
    want = _need_ver(req, b)
    return _answer(_call(pops.metrics_set, b.get("max_mb"), want_ver=want))


# ── the activity of devices (k3/handoff/SPEC-activity.md §5, AC4): only the admin (``auth="admin"``, the default),
# only GET with nothing run on the node; an id that is not in the state now never comes out (deleted — gone at once,
# the collector erases its rows within 5 min). The page of a person (/p/) shows none of it (ACT-7). The history off ->
# {activity: false, why: "off"}, no sqlite3 -> why "nosqlite". The switch — GET/PUT /api/gw/activity/settings (ver).

_ACT = {"key": None, "body": None, "etag": None}   # the summary of «Люди» of this process: one recount a window
_ACT_LOCK = threading.Lock()


def _act_off(st):
    from ..gw import metrics
    if not model.activity_keep(st):
        return {"activity": False, "why": "off"}
    if not metrics.available():
        return {"activity": False, "why": "nosqlite"}
    return None


def _act_ids(st):
    return set(d.get("id") for d in model.devices(st) if isinstance(d.get("id"), str) and d.get("id"))


def _act_json(doc, headers=None):
    """Compact JSON (the summary of 150 devices ≈ 80 KB, not 105 with spaces), never cached by the browser."""
    h = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
    h.update(headers or {})
    return routes.Response(200, json.dumps(dict(doc, ok=True), ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode("utf-8"), h)


@route("GET", r"/api/gw/activity")
def gw_activity(req):
    """§5.1: the summary of «Люди» — the last 24 h, 48 windows of 30 min a device (``metrics.dev_summary``). Kept in
    the process by its key — the newest window written (``meta.dev_flush``), the end shown and the ids of the state:
    recounted at most once a window; ``ETag`` of that key, ``If-None-Match`` equal -> 304 without a body."""
    from ..gw import metrics
    st = _st()
    off = _act_off(st)
    if off is not None:
        return _act_json(off)
    ids = _act_ids(st)
    now = int(time.time())
    flush = metrics.dev_flush()
    anchor = metrics.dev_anchor(flush, now)
    key = "%s|%s|%s" % (flush, anchor, hashlib.sha1("\n".join(sorted(ids)).encode("utf-8")).hexdigest())
    etag = '"act-%s"' % hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    inm = req.headers.get("if-none-match") or ""
    if etag in [x.strip() for x in inm.split(",")]:
        return routes.Response(304, b"", {"ETag": etag, "Cache-Control": "no-store"})
    with _ACT_LOCK:
        if _ACT["key"] != key:
            _ACT.update(key=key, etag=etag, body=metrics.dev_summary(ids, now, flush=flush))
        body = _ACT["body"]
    return _act_json(body, {"ETag": etag})


def _act_range(req):
    from ..gw import metrics
    rng = req.query.get("range") or "1d"
    if rng not in metrics.DEV_RANGES:
        _refused(pops.bad_field("range"))
    return rng


def _act_history(st, ids, rng, per_device=False):
    from ..gw import metrics
    off = _act_off(st)
    if off is not None:
        return _act_json(off)
    h = metrics.dev_history(ids, rng, per_device=per_device)
    if h is None:
        return _act_json({"activity": False, "why": "nosqlite"})
    return _act_json(dict(h, activity=True))


@route("GET", r"/api/gw/devices/%s/activity" % _DID)
def gw_device_activity(req):
    """§5.2: the history of a device — 1d (288 × 5 min), 7d (168 × 1 h), 30d (720 × 1 h); arrays without stamps."""
    rng = _act_range(req)
    st = _st()
    did = req.match.group("id")
    if did not in _act_ids(st):
        _refused(pops.gone("device", did))
    return _act_history(st, {did}, rng)


@route("GET", r"/api/gw/users/%s/activity" % _UID)
def gw_user_activity(req):
    """§5.2: a person — the bytes of its devices summed, «на связи» the most of them (ACT-11), and ``devices`` — the
    totals of the period of each device (the share of «График»)."""
    rng = _act_range(req)
    st = _st()
    uid = req.match.group("id")
    if not any(u.get("id") == uid for u in model.users(st)):
        _refused(pops.gone("user", uid))
    ids = set(d.get("id") for d in model.devices(st, user=uid) if d.get("id"))
    return _act_history(st, ids, rng, per_device=True)


@route("DELETE", r"/api/gw/activity")
def gw_activity_wipe(req):
    """№41 «Удалить историю активности» (policy_ops.activity_wipe): «Хранить» stays; ``?dry=1`` — {dry, bytes, since}
    (what would go), else {freed, …settings}. 409 ``activity_off`` / ``nosqlite`` / ``nodb`` / ``db_busy``. The admin
    with X-Kaskad-Admin, as every change; nothing run on the node."""
    if _dry(req):
        return _answer(_pcall(pops.activity_wipe, dry=True))
    try:
        return _answer(_call(pops.activity_wipe))
    finally:
        with _ACT_LOCK:                         # the summary of this process is recounted at the next GET
            _ACT.update(key=None, body=None, etag=None)


@route("GET", r"/api/gw/activity/settings")
def gw_activity_settings_get(req):
    """«Хранить историю активности» (ACT-8, Н1): keep, ver, what is kept now (since, bytes) and how long."""
    return ok(**pops.activity_get(_st()))


@route("PUT", r"/api/gw/activity/settings")
def gw_activity_settings_set(req):
    b = req.json()
    want = _need_ver(req, b)
    return _answer(_call(pops.activity_set, b.get("keep"), want_ver=want))


@route("GET", r"/api/settings/time")
def settings_time_get(req):
    return ok(**pops.tz_get())


@route("PUT", r"/api/settings/time")
def settings_time_set(req):
    b = req.json()
    want = _need_ver(req, b)
    name = _str(b, "tz", True, 64)
    return _answer(_call(pops.tz_set, name, want_ver=want))


@route("GET", r"/api/notify")
def notify_get(req):
    return ok(**pops.notify_get(_st()))


@route("PUT", r"/api/notify")
def notify_set(req):
    b = req.json()
    want = _need_ver(req, b)
    return _answer(_call(pops.notify_set, push=b.get("push"), flap_summary=b.get("flap_summary"), want_ver=want))

# ── a copy of the settings (kaskad.backup) ─────────────────────────────────
# The file and its password come in POST bodies only, are never logged, never answered back (but the file of an
# export, no-store) and never put into an error.

def _backup_parts(b, key="parts"):
    v = b.get(key)
    if v is None:
        return None
    if not isinstance(v, (list, str)):
        raise HttpError(400, tr("panel.api.backupParts", field=key, list=", ".join(backup.PARTS)))
    try:
        return backup._parts(v)
    except KaskadError as e:
        raise routes.core_error(e)


def _backup_agents(b):
    """SEC-3372 З-3: the keys of the AI agents of a copy only by the person's explicit tick — ``"agents": true``."""
    return b.get("agents") is True


def _backup_doc(b):
    text, pw = b.get("file"), b.get("password")
    if not isinstance(text, str) or not text.strip():
        raise HttpError(400, tr("panel.api.backupFile"))
    if not isinstance(pw, str) or not pw:
        raise HttpError(400, tr("panel.api.backupPassword"))
    try:
        return backup.unseal(runner(), text, pw)
    except KaskadError as e:
        raise routes.core_error(e)


@route("GET", r"/api/backup/summary")
def backup_summary(req):
    try:
        return json_response(backup.summary_doc(runner()))
    except KaskadError as e:
        raise routes.core_error(e)


@route("POST", r"/api/backup/export")
def backup_export(req):
    b = req.json()
    pw = b.get("password")
    if not isinstance(pw, str):
        raise HttpError(400, tr("panel.api.backupPasswordMissing"))
    parts = _backup_parts(b)
    without = _backup_parts(b, "without")
    parts = tuple(p for p in (parts or backup.PARTS) if p not in (without or ()))
    if not parts:
        raise HttpError(400, tr("panel.api.backupNothing"))
    try:
        res = backup.export(runner(), pw, parts)
    except KaskadError as e:
        raise routes.core_error(e)
    finally:
        pw = None
    return json_response({"ok": True, "file": res["text"], "name": res["name"], "parts": res["parts"],
                          "summary": res["summary"], "size": res["size"]},
                         headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})


@route("POST", r"/api/backup/preview")
def backup_preview(req):
    b = req.json()
    doc = _backup_doc(b)
    try:
        pv = backup.preview(runner(), doc, _backup_parts(b), agents=_backup_agents(b))
    except KaskadError as e:
        raise routes.core_error(e)
    return ok(preview=pv, parts=doc["manifest"]["parts"])


@route("POST", r"/api/backup/import")
def backup_import(req):
    b = req.json()
    doc = _backup_doc(b)
    res = _call(backup.apply, runner(), doc, _backup_parts(b), agents=_backup_agents(b))
    return ok(parts=res["parts"], backup=res["backup"], warnings=[util.redact(w) for w in res["warnings"]],
              notes=res["notes"], address=res["address"], text=res["text"])


@route("POST", r"/api/backup/undo")
def backup_undo(req):
    res = _call(backup.undo, runner())
    return ok(parts=res["parts"], backup=res.get("backup"), warnings=[util.redact(w) for w in res["warnings"]],
              notes=res["notes"], agents_cut=res.get("agents_cut", 0), text=res["text"], restored_from=res.get("from"))


# ── safe MTU (§6.1): a narrow card ─────────────────────────────────────────

@route("POST", r"/api/gw/mtu")
def gw_mtu(req):
    b = req.json()
    kind = _str(b, "kind", True, 8)
    action = _str(b, "action", True, 8)
    if action not in ("reissue", "s4", "keep"):
        raise HttpError(400, tr("panel.api.badMtuAction"))
    if action == "s4":                     # the panel asked first, with the devices of the inbound (mtu of GET gw)
        res = _call(ops.mtu_s4, runner(), kind, yes=True)
        return ok(kind=kind, changed=bool(res.get("done")), was=res.get("was"), s4=res.get("s4"),
                  server=res.get("server"), text=res.get("text") or "", devices=res.get("devices") or [],
                  warnings=_warnings(res))
    if action == "reissue":
        res = _call(ops.mtu_reissue, runner(), kind)
        return ok(kind=kind, changed=bool(res.get("changed")), was=res.get("was"), client=res.get("client"),
                  server=res.get("server"), text=res.get("text") or "", devices=res.get("devices") or [],
                  warnings=_warnings(res))
    res = _call(ops.mtu_keep, runner(), kind)
    return ok(kind=kind, kept=True, nic=res.get("nic"))


# ── the engine of AmneziaWG ────────────────────────────────────────────────

@route("GET", r"/api/gw/awg/engine")
def gw_awg_engine(req):
    try:
        doc = awgengine.status(None, _st())
    except KaskadError as e:
        raise routes.core_error(e)
    return json_response(dict(doc, ok=True))


@route("POST", r"/api/gw/awg/check")
def gw_awg_check(req):
    try:
        return json_response(awgengine.check(runner(), _st()))
    except KaskadError as e:
        raise routes.core_error(e)


@route("POST", r"/api/gw/awg/engine")
def gw_awg_engine_set(req):
    b = req.json()
    eng = _str(b, "engine", True, 8)
    if eng not in awgengine.ENGINES:
        raise HttpError(400, tr("panel.api.badEngine"))
    res = _call(awgengine.switch, runner(), eng, force=bool(_bool(b, "force", False)), why="person:panel")
    warns = _warnings(res)
    if res.get("rolled_back"):
        warns.insert(0, res.get("reason") or "")
    return ok(engine=res["engine"], was=res.get("was"), rolled_back=bool(res.get("rolled_back")),
              unchanged=bool(res.get("unchanged")), backup=res.get("backup"), warnings=[w for w in warns if w])


@route("POST", r"/api/gw/awg/export")
def gw_awg_export(req):
    """People, devices, the AmneziaWG inbounds and their keys — secrets: never cached, never in a view."""
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix="kaskad-awg-export.", dir=paths.run_dir() if os.path.isdir(paths.run_dir())
                               else None)
    os.close(fd)
    try:
        awgengine.export(tmp, _st())
        with open(tmp, encoding="utf-8") as f:
            doc = json.load(f)
    except KaskadError as e:
        raise routes.core_error(e)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    util.log_tr("journal.panel.awg_export")
    return json_response({"ok": True, "doc": doc}, headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})


@route("POST", r"/api/gw/awg/import")
def gw_awg_import(req):
    import tempfile
    b = req.json()
    doc = b.get("doc")
    if not isinstance(doc, dict):
        raise HttpError(400, tr("panel.api.awgDocMissing"))
    fd, tmp = tempfile.mkstemp(prefix="kaskad-awg-import.", dir=paths.run_dir() if os.path.isdir(paths.run_dir())
                               else None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        res = _call(awgengine.import_, runner(), tmp, tr("panel.api.awgImportFile"))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return ok(inbounds=res["inbounds"], users=res["users"], devices=res["devices"], warnings=_warnings(res))


# ── events ─────────────────────────────────────────────────────────────────

def _tunnel_admin(req):
    """Is the connection of ``req`` still the SSH tunnel's admin by the panel's current settings (server.py: the same
    ``auth.tunnel`` a request is let in by)?"""
    cfg = _cfg()
    return auth.tunnel(req.headers, req.client_ip, req.listener == "local", public=cfg["public"]["on"],
                       require_password=cfg["require_password"])


@route("GET", r"/api/events")
def events(req):
    # a stream of a cookie session ends as soon as that session is gone («Выйти» in any tab, a new password, its
    # time); the tunnel's stream — as soon as the tunnel is no admin any more (F19b: «always ask the password» on,
    # the open mode on), by the settings of that moment, like every REST request of the tunnel
    tok = (req.session or {}).get("token")
    if tok:
        alive = lambda: auth.SESSIONS.alive(tok)        # noqa: E731
    else:
        alive = lambda: _tunnel_admin(req)              # noqa: E731
    stream = sse.open_stream(alive)
    if stream is None:
        raise HttpError(503, tr("panel.api.tooManyTabs", max=sse.MAX_STREAMS), data={"streams": sse.MAX_STREAMS})
    return stream


# ── язык сервера (3.3.2) ────────────────────────────────────────────────────
# One language per server (KASKAD_LANG): the SSH menu, the bot, the panel, the wizard. «Вид» → Язык changes it; the
# page reloads in it (panel/lang.py).

@route("POST", r"/api/lang")
def lang_put(req):
    from . import lang as weblang
    out = weblang.change(req.json())
    _changed()
    return json_response(out)


# ── «Вид» ──────────────────────────────────────────────────────────────────
# The owner's choice lives on the server: it survives a change of browser. Only the white list of goAmnezia
# pro (its server.py «Вид»): an unknown field or value refuses the whole request. "kaskad" — the accent of
# Kaskad besides goAmnezia's. "auto" — the default since 3.3.0 (the owner): mint in the dark theme, violet in the light,
# as the quick setup; "ga" — the blue «Каскад», a ready theme. The theme by default is dark since 3.3.2 (the owner), not
# the system's; "system" stays a choice. A saved choice is never changed.

APPEARANCE_ENUMS = {
    "theme": ("light", "dark", "system"),
    "accent": ("auto", "ga", "kaskad", "blue", "sky", "lavender", "mint", "coral", "graphite", "custom"),
    "tint": ("neutral", "warm", "cool"), "density": ("min", "balanced", "detailed"), "btn": ("pill", "round"),
    "cards": ("shadow", "border", "flat"), "nav": ("full", "icons", "top"), "fs": ("s", "m", "l"),
}
APPEARANCE_INTS = {"hue": (0, 359), "radius": (0, 16)}
APPEARANCE_BOOLS = ("vibrant", "motion")
APPEARANCE_DEFAULTS = {"theme": "dark", "accent": "auto", "hue": 200, "tint": "neutral", "density": "detailed",
                       "radius": 8, "btn": "pill", "cards": "shadow", "nav": "full", "fs": "m", "vibrant": False,
                       "motion": True}


def appearance_clean(raw, base=None):
    """``base`` (or the defaults) with the fields of ``raw``; (None, bad field) when anything is off."""
    if not isinstance(raw, dict) or len(raw) > 32:
        return None, "?"
    out = dict(APPEARANCE_DEFAULTS)
    out.update(base or {})
    for k, v in raw.items():
        if k in APPEARANCE_ENUMS:
            if v not in APPEARANCE_ENUMS[k]:
                return None, k
        elif k in APPEARANCE_INTS:
            lo, hi = APPEARANCE_INTS[k]
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                return None, k
        elif k in APPEARANCE_BOOLS:
            if not isinstance(v, bool):
                return None, k
        else:
            return None, k
        out[k] = v
    return out, None


APPEARANCE_V = 3      # 2 — 3.3.0: the default accent is «auto»; 3 — 3.3.2: the default theme is dark


def _appearance_v(doc):
    v = doc.get("v")
    return v if isinstance(v, int) and not isinstance(v, bool) else 1


def appearance_read():
    """The saved «Вид». The old defaults were saved along with any other change, so they are read as the new ones
    (the owner's decisions; the same in boot.js and migrateLook of app.js — the key of the browser is shared with the
    quick setup):
      * saved before 3.3.0 (``v`` below 2) with the blue «Каскад» ("ga") — the accent "auto" (mint / violet);
      * saved before 3.3.2 (``v`` below 3) with the theme "system" — the theme "dark".
    Every other field and value stays. A PUT writes ``"v": 3``: the blue or «as the system» chosen after it stays."""
    doc = snapshot.read_json(conf.appearance_path())
    if not isinstance(doc, dict):
        return None
    ap, _ = appearance_clean(doc.get("appearance"))
    if ap is None:
        return None
    v = _appearance_v(doc)
    if v < 2 and ap.get("accent") == "ga":
        ap["accent"] = APPEARANCE_DEFAULTS["accent"]
    if v < 3 and ap.get("theme") == "system":
        ap["theme"] = APPEARANCE_DEFAULTS["theme"]
    return ap


@route("GET", r"/api/appearance")
def appearance_get(req):
    saved = appearance_read()
    return ok(appearance=saved or dict(APPEARANCE_DEFAULTS), saved=saved is not None,
              defaults=dict(APPEARANCE_DEFAULTS),
              fields={"enums": dict((k, list(v)) for k, v in APPEARANCE_ENUMS.items()),
                      "ints": dict((k, list(v)) for k, v in APPEARANCE_INTS.items()),
                      "bools": list(APPEARANCE_BOOLS)})


_ap_lock = threading.Lock()


@route("PUT", r"/api/appearance")
def appearance_put(req):
    b = req.json()
    raw = b.get("appearance") if isinstance(b.get("appearance"), dict) else b
    with _ap_lock:
        ap, bad = appearance_clean(raw, appearance_read())
        if ap is None:
            raise HttpError(400, tr("panel.api.appearanceBad", field=bad), data={"field": bad})
        util.ensure_dir(paths.panel_dir(), 0o700)
        util.atomic_write(conf.appearance_path(), json.dumps({"schema": 1, "v": APPEARANCE_V, "appearance": ap,
                                                               "updated": util.now_iso()},
                                                              ensure_ascii=False, sort_keys=True) + "\n", 0o600)
    _changed()
    return ok(appearance=ap)


# ── «Знакомство» (3.3.2, the owner's №14) ──────────────────────────────────
# The tour of the panel and the path of the traffic left the quick setup: the first visit of the panel after it shows a
# short tour over the real panel and ends on «Схема» with the path of the device the wizard made. Once per node, not
# per browser: the mark lives here. «Показать знакомство снова» (Settings) only shows it — the mark stays.

def _intro_done():
    doc = snapshot.read_json(conf.intro_path())
    return isinstance(doc, dict) and doc.get("done") is True


def _intro_device(st):
    """The device of the wizard (setup.json answers.device) while it exists, else the first device, else None."""
    from ..setup import model as setup_model
    ids = [d.get("id") for d in model.devices(st) if isinstance(d, dict) and d.get("id")]
    want = setup_model.load()["answers"].get("device")
    return want if want in ids else (ids[0] if ids else None)


@route("GET", r"/api/intro")
def intro_get(req):
    from ..setup import model as setup_model
    finished = setup_model.load()["status"] == "finished"
    done = _intro_done()
    return ok(due=finished and not done, done=done, setup_finished=finished, device=_intro_device(_st()))


@route("PUT", r"/api/intro")
def intro_put(req):
    b = req.json()
    if not isinstance(b.get("done"), bool):
        raise HttpError(400, tr("panel.api.fieldBool", field="done"), data={"field": "done"})
    util.ensure_dir(paths.panel_dir(), 0o700)
    util.atomic_write(conf.intro_path(), json.dumps({"schema": 1, "done": b["done"], "updated": util.now_iso()},
                                                    sort_keys=True) + "\n", 0o600)
    return ok(done=b["done"])


# ── the panel itself ───────────────────────────────────────────────────────

def _panel_address():
    """3.3.7.5 — the one address of the panel for «Адрес панели»: {"kind": "domain"|"public", "masked", "fallback":
    the open mode beside the domain (masked) or None, "port"}; None — the panel is not open from the internet. The
    full address only by GET /api/site/panel-url («Показать полностью», «Скопировать»)."""
    from ..site import ops as sops
    try:
        r = sops.panel_url(show=False)
    except (KaskadError, OSError, ValueError):
        return None
    pub = _cfg()["public"]
    return {"kind": r["kind"], "masked": r["masked"], "fallback": r["fallback_masked"],
            "port": pub["port"] if pub["on"] else None}


def _panel_doc():
    p = server.PANEL
    cfg = _cfg()
    rec = auth.record()
    st = p.status() if p is not None else {}
    return {"ok": True, "public": dict(cfg["public"], url=conf.public_url(cfg), url_masked=conf.public_url_masked(cfg)),
            "address": _panel_address(), "listen": st.get("listen") or [],
            "fingerprint": conf.fingerprint() if cfg["public"]["on"] else "", "sessions": auth.SESSIONS.count(),
            "password": bool(rec), "login": rec.get("login"), "require_password": cfg["require_password"],
            "tunnel": conf.tunnel_command(), "version": server.STATIC.version}


@route("GET", r"/api/panel")
def panel_get(req):
    doc = _panel_doc()
    doc["you"] = {"via": (req.session or {}).get("via")}
    return json_response(doc)


@route("PUT", r"/api/panel/password")
def panel_password(req):
    b = req.json()
    new = b.get("password")
    login_name = _str(b, "login", max_len=32)
    if not isinstance(new, str):
        raise HttpError(400, tr("panel.api.newPasswordString"))
    via = (req.session or {}).get("via")
    if auth.password_set() and via != "tunnel":
        cur = b.get("current")
        if not isinstance(cur, str):
            raise HttpError(400, tr("panel.api.currentMissing"))
        if not cur:                              # an empty field is no try: not a wrong password, not counted
            raise HttpError(400, tr("panel.api.currentEmpty"), data={"field": "current"})
        try:
            good, left = auth.login(req.client_ip, auth.record().get("login"), cur)
        except auth.LoginRefused as e:
            raise HttpError(e.status, str(e), data={"retry_after": e.retry_after})
        if not good:
            raise HttpError(403, tr("panel.api.currentWrong"), data={"left": left})
    try:
        res = auth.set_password(new, login_name)
    except KaskadError as e:
        raise routes.core_error(e)
    old = (req.session or {}).get("token")
    gone = auth.SESSIONS.drop_all()
    headers = {}
    if old or via in ("password", "link"):
        headers["Set-Cookie"] = auth.set_cookie(auth.SESSIONS.create("password", req.client_ip), req.scheme == "https")
    util.log_tr("journal.panel.password_changed", ip=req.client_ip, n=int(max(0, gone - (1 if old else 0))))
    _changed()
    return json_response({"ok": True, "login": res["login"], "updated": res["updated"]}, headers=headers)


@route("POST", r"/api/panel/link")
def panel_link(req):
    b = req.json()
    ttl = b.get("ttl", auth.LINK_TTL)
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        raise HttpError(400, tr("panel.api.badTtl"))
    token = auth.issue_link(by="panel", ttl=ttl)
    base = conf.public_url(_cfg())          # the open mode: the address people type (as the links of the bot, menu)
    public = base is not None
    if req.listener == "proxy":             # behind nginx: the domain and the secret prefix this page came by
        base, public = "%s://%s%s/" % (req.scheme, req.headers.get("host"), req.base), True
    elif base is None:                      # only this way in: the SSH tunnel (or whatever the browser came by)
        host = req.headers.get("host") or "%s:%d" % (conf.LOCAL_HOST, conf.LOCAL_PORT)
        base = "%s://%s/" % (req.scheme, host)
    return json_response({"ok": True, "url": base + "login?t=" + token, "expires_in": max(60, min(ttl, 86400)),
                          "public": public}, headers={"Cache-Control": "no-store, max-age=0"})


@route("PUT", r"/api/panel/public")
def panel_public(req):
    b = req.json()
    on = _bool(b, "on", required=True)
    port = _str(b, "port", max_len=5)
    if on and not auth.password_set():
        raise HttpError(400, tr("panel.api.publicNeedsPassword"))
    try:
        cfg = conf.set_public(runner(), on, port=port, force=bool(_bool(b, "force", False)))
    except KaskadError as e:
        raise routes.core_error(e)
    finally:
        _changed()
    if server.PANEL is not None:
        # after this answer has left: switching the open mode off from the open mode closes its listener
        threading.Timer(0.3, server.PANEL.reload).start()
    return ok(public=dict(cfg["public"], url=conf.public_url(cfg)), fingerprint=conf.fingerprint() if on else "")


@route("PUT", r"/api/panel/settings")
def panel_settings(req):
    on = _bool(req.json(), "require_password", required=True)
    try:
        cfg = conf.set_require_password(on)
    except KaskadError as e:
        raise routes.core_error(e)
    if server.PANEL is not None:
        server.PANEL.reload()
    util.log_tr("journal.panel.tunnel_password_on" if on else "journal.panel.tunnel_password_off")
    _changed()
    return ok(require_password=cfg["require_password"])


# ── the GitHub key (kaskad.ghkey) ──────────────────────────────────────────
# The core of AmneziaWG (and the updates of gokaskad) come from a private repository: without a key the panel could
# only send people to the SSH menu (8 → 6). The key never leaves the server: answers carry its state only.

@route("GET", r"/api/github")
def github_get(req):
    return ok(**ghkey.status())


@route("PUT", r"/api/github")
def github_put(req):
    b = req.json()
    key = b.get("token")
    if not isinstance(key, str) or len(key) > 300:
        raise HttpError(400, tr("panel.api.githubKeyString"))
    key = key.strip()
    if not ghkey.valid(key):
        raise HttpError(400, tr(ghkey.BAD_FORMAT), data={"reason": "format"})
    force = bool(_bool(b, "force", False))
    res = ghkey.check(key)
    if not res["ok"] and not (force and res["reason"] == "net"):
        # «net»: GitHub did not answer — the page may save the key unchecked (force); a refused key is not saved
        raise HttpError(409 if res["reason"] == "net" else 400, res["error"], data={"reason": res["reason"]})
    ghkey.save(key, res)
    util.log_tr("journal.panel.gh_saved" if res["ok"] else "journal.panel.gh_saved_unchecked", ip=req.client_ip)
    _changed()
    return ok(**ghkey.status())


@route("DELETE", r"/api/github")
def github_delete(req):
    had = ghkey.delete()
    if had:
        util.log_tr("journal.panel.gh_removed", ip=req.client_ip)
    _changed()
    return ok(removed=had, **ghkey.status())


@route("POST", r"/api/github/check")
def github_check(req):
    key, _ = ghkey.current()
    if not key:
        raise HttpError(400, tr("panel.api.githubKeyMissing"))
    ghkey.remember(key, ghkey.check(key))
    _changed()
    return ok(**ghkey.status())
