"""Panel routes of the light health of the node (k3/handoff/SPEC-node-score.md §3.8, §3.9 п. 4–5; A7).

    GET /api/host/health                    {ok, health, live: {age, fresh}, instant, by} — ``hostprobe.read_health``:
                                            a fresh live.json → a fresh host-health.json of the bot → a snapshot of
                                            the panel (``hostprobe.instant_health``), kept SNAP_TTL s in this process;
                                            every ``checks[rid]`` + ``warn_at``/``crit_at``/``down`` of its rule
    GET /api/host/history?range=1d|7d|90d   {ok, charts, host: metrics.host_history, ping: metrics.ping_history};
                                            no sqlite3 — {ok, charts: false}; another range — 400

    GET /api/host/score                     the node against its own history (SS §4, NS-D30–NS-D34): {ok, charts,
                                            summary: {rel_total, …}, dev_rule, links: [{rel, fresh_raw, usual_raw,
                                            …}], rows: {people, load}, stab, days} — ``bench.hostscore.score_doc``;
                                            no absolute score, band, capacity or «годится для…»
    GET /api/host/synth                     «сухая синтетика» (owner 27.10, круг 1 Т1): {ok, charts, version,
                                            calibrated: false, anchors, margin, fresh: {total, raw, cap, weak,
                                            limited, why, measured}, links: [{id, fresh, usual, lo, hi, n, dev,
                                            why, why_text}],
                                            checks: [{id, ts, day, mode, by, total, …, links}], totals, last_at,
                                            stale} — ``bench.hostscore.synth_doc``; the panel only (not CLI/MCP/bot)
    GET /api/host/day/<YYYY-MM-DD>          the data of one day of the strip (NS §6.2): a real date within the 30
                                            days of the strip (``KASKAD_TZ``), else 404; no base and no checks —
                                            {ok, charts: false}
    GET /api/host/share?tariff=0|1&country=0|1&cpu=0|1   «Поделиться» (C9; SS §5.1–5.2, «Сверка 24.10»): only what
                                            the frame shows — ``bench.share.node_share``; the switches off by default;
                                            another parameter or value — 400 (data.field); no-store
    POST /api/host/dev-rule                 body {"pct": 5…30} — the threshold of ▲/▼ as ``host set --dev-threshold``
                                            (``host_cli.apply_set``, use_spread unchanged) -> {ok, dev_rule: {pct,
                                            use_spread}, changed}; another body — 400 (data.field "pct"), > 256
                                            bytes — 413; X-Kaskad-Admin and the Origin as every admin POST

The schedule and the history of the checks (SPEC-node-score-schedule-share §2.6, §3.4; B13):

    GET  /api/host/bench/schedule           {schedule, retry, armed, armed_why, pkgs, tz, next, cost, limits, timer,
                                            etag} (``ETag`` too); a broken saved entry is saved off by
                                            ``bench.fix_state(timeout=1)`` (the lock busy — the answer only); an
                                            empty list — also ``propose``: the two entries the screen offers
                                            (``schedule.propose``, the quiet hour of host_h1, the minutes of the
                                            node) with their price before adding (B15)
    POST /api/host/bench/schedule/preview   body {"schedule": [...], "retry"?} -> {schedule, errors, cost, next};
                                            saves nothing (the price «live» of the screen)
    PUT  /api/host/bench/schedule           body {"schedule": [...], "confirm"?, "retry"?, "tz"?}, ``If-Match`` —
                                            the etag of GET (none — 428, another — 409 ``stale``); errors of the
                                            validator — 400 ``errors``; a heavy schedule without ``confirm`` — 409
                                            ``need_confirm``; else saved, the timer set again, the answer as GET
    GET  /api/host/bench?from&to&mode&by&status&before&limit    ``history.runs`` (dates YYYY-MM-DD by KASKAD_TZ,
                                            ≤ 400 days; none — the last 30 days) with ``by_text`` of every row (6C)
                                            and ``rel`` — rel_total of the check at it (NS-D33; null — none)
    POST /api/host/bench                    body {"mode": "quick"|"full", "confirm"?: 8 hex, "install"?: bool} — the
                                            check from the panel, as MCP ``host_bench`` (``host_bench_cli.panel_start``):
                                            no key — 409 ``need_confirm`` {price, pkgs}; the price changed — 409
                                            ``confirm_changed`` {price, pkgs}; another check runs — 409 ``running``
                                            {id}; the limits of a day — 429 ``limit`` {limits, today}; else
                                            {ok, id, unit, mode, missing, price}
    GET  /api/host/bench/<id>               ``history.run`` (id — 10 hex by fullmatch, else 404) with ``rel``
                                            {rel_total, tests: {test: {usual, usual_n, rel}}} (null — not good)
    GET  /api/host/bench/compare?a&b        ``history.compare``: the raw values side by side (delta, delta_pct),
                                            rel_a / rel_b and ``better`` only when both have rel_total (NS-D33)
    GET  /api/host/bench/export?format=csv|json&from&to   a file (attachment) without the name of the node,
                                            addresses or keys: a CSV row per test, or {"v": 1, "tz", "runs"}

The body and the query are checked strictly (``schedule.check_body``; unknown parameters, types, lengths, the number
of entries, the dates and the zone) — the texts in the reader's language.

    GET /api/host/tariff                    the tariff — only what the health and the price of the schedule need
                                            (the port: the rule «port»; the traffic: «% of the tariff»):
                                            {ok, tariff: every field {v, src, label, text, src_text}, rev; dropped}
                                            — ``host_tariff.show_doc``
    POST /api/host/tariff                   {values: {field: value | null}, clear: [field] | ["all"], rev, dry} ->
                                            ``host_tariff.apply(panel=True)``: 400 with ``data.errors`` [{field,
                                            code, text}]; a write (not ``dry``) needs ``rev`` of the GET — none or
                                            null 428 ``rev_required``, not 10 hex signs 400, another window 409
                                            ``rev_changed`` (REV-ns-c1 З-3, П-1); X-Kaskad-Admin and the Origin as
                                            every admin POST (server.py)


The browser test of the path «your device → the server» (k3/handoff/SPEC-browser-channel.md §4.2; the logic —
``panel/speed.py``), every route behind the login (a HEAD — 405 after it), a POST with ``X-Kaskad-Admin: 1``:

    POST /api/host/speed/start              {ok, id, until_s, limits, via, vpn, left_day_bytes, tests_left_hour,
                                            tests_left_day}; 409 {busy, retry_after} — another test, or a server
                                            check runs (``what: "check"``); 429 {reason, limit, used, retry_after}
    GET  /api/host/speed/ping?id=&r=        204 with ``Server-Timing: app;dur=…``
    GET  /api/host/speed/down?id=&bytes=N   N random bytes (≤ 256 MiB, reserved before the first one); 413; 429
                                            {reason: budget, left} | {reason: streams}
    POST /api/host/speed/up?id=             the body (Content-Length ≤ 64 MiB) is read here and thrown away; the
                                            server checked its headers, the login, Origin and the header first
    POST /api/host/speed/finish {id}        {ok, bytes_down, bytes_up, up, cpu_limited}
    GET  /api/host/speed/budget             {ok, today_bytes, day_cap_bytes, tests_hour, tests_day, caps, via}

A lease that is not of this session — 403; ended by time — 410. While a lease lives, POST /api/host/bench answers 409
``speed_test`` (``retry_after``).

All — behind the panel's login; the rest read only (the PUT of the schedule and the POST of the tariff write). The texts are said in the reader's language (the header X-Kaskad-Lang, as
every route): ``live.read`` localizes live.json, ``hostmon.health_in`` the bot's file and the panel's snapshot.
``panel/api.py`` is not touched (§3.9 п. 5): its ``host`` of a stale live is ``live.Host.sample()`` without health —
the tile «Сервер» asks this route itself.
"""

import ipaddress
import json
import math
import os
import re
import threading
import time

from .. import host_bench_cli, host_sched, score, state, sysrun
from ..bench import history, hostscore, spool
from ..bench import schedule as bsched
from ..gw import hostmon, hostprobe, live, metrics, model
from ..i18n import tr
from ..errors import KaskadError
from . import speed
from .routes import HttpError, RawStream, Response, core_error, json_response, route

SNAP_TTL = 30.0                 # s: the panel's own snapshot is read once in this time (REV-sec П-6: not /proc,
                                # certificates and systemctl at every request)
VIA = {"icmp": "gw_host.via.icmp", "dns": "gw_host.via.dns"}        # the way of a point (REV-ns-a3 З-2)
HINT = {"bot_python": "gw_host.hint.bot_python"}                    # §3.9 п. 7

_LOCK = threading.Lock()
_SNAP = {"mono": None, "health": None}
_PRICE_LOCK = threading.Lock()
_PRICE = {"flight": None}       # the price of «Проверить сервер» being counted now (_price_once)


def reset():
    """Forget the cached snapshot (tests)."""
    with _LOCK:
        _SNAP["mono"], _SNAP["health"] = None, None
    host_sched.reset()
    hostscore.reset()
    history.reset()


def _snapshot(now, st, mono):
    """``hostprobe.instant_health`` once in SNAP_TTL s (texts as msgs: said again for each reader). Under the lock:
    two requests at once read /proc once."""
    with _LOCK:
        m = time.monotonic()
        if _SNAP["health"] is None or _SNAP["mono"] is None or not 0 <= m - _SNAP["mono"] < SNAP_TTL:
            _SNAP["health"] = hostprobe.instant_health(now, st, mono, lang=False)
            _SNAP["mono"] = m
        h = _SNAP["health"]
    return hostmon.health_in(h)


def _said(h):
    """The words the panel shows besides those of ``health_in``: the hint of the bot on bash, the way of a point."""
    if not isinstance(h, dict):
        return h
    out = dict(h)
    hint = out.get("hint")
    out["hint_text"] = tr(HINT[hint]) if hint in HINT else None
    if isinstance(out.get("checks"), dict):
        out["checks"] = dict((rid, _limits(rid, c)) for rid, c in out["checks"].items())
    pr = out.get("probes")
    if isinstance(pr, dict) and isinstance(pr.get("anchors"), list):
        out["probes"] = dict(pr, anchors=[dict(a, via_text=tr(VIA[a["via"]]) if a.get("via") in VIA else None)
                                          if isinstance(a, dict) else a for a in pr["anchors"]])
    return out


def _limits(rid, c):
    """A check of the health with the thresholds of its rule (круг 1, Т1: the dashed line «внимание» of «Здоровье
    сервера»): ``warn_at`` / ``crit_at`` — where the level turns on (``hostmon.HOST_RULES``: warn / crit, a draft crit
    — ``crit_at`` too, ``crit_draft`` true), ``down`` — the level turns on BELOW them (memory free, days of the disk).
    Only the numbers of the rules the collector already uses; another check (unknown id) — as it is."""
    rule = hostmon.RULES.get(rid) if isinstance(rid, str) else None
    if not isinstance(c, dict) or rule is None:
        return c
    out = dict(c)
    w = rule.get("warn")
    cr = rule.get("crit") or rule.get("crit_draft")
    out["warn_at"] = w[0] if isinstance(w, (list, tuple)) and w else None
    out["crit_at"] = cr[0] if isinstance(cr, (list, tuple)) and cr else None
    out["crit_draft"] = not rule.get("crit") and bool(rule.get("crit_draft"))
    out["down"] = bool(rule.get("down"))
    return out


@route("GET", r"/api/host/health")
def host_health(req):
    now = time.time()
    h, by = hostprobe.read_health(now, instant=_snapshot)
    try:
        _doc, age = live.read(now)
    except Exception:                                # noqa: BLE001 — a broken live.json: «no fresh data»
        age = None
    h = _said(h)
    return json_response({"ok": True, "health": h, "by": by,
                          "instant": bool(h.get("instant")) if isinstance(h, dict) else True,
                          "live": {"age": round(age, 1) if age is not None else None, "fresh": live.fresh(age)}})


@route("GET", r"/api/host/history")
def host_history(req):
    rng = req.query.get("range") or "1d"
    if rng not in metrics.RANGES:
        raise HttpError(400, tr("panel.host.badRange"), data={"field": "range"})
    if not metrics.available():
        return json_response({"ok": True, "charts": False})
    host, ping = metrics.host_history(rng), metrics.ping_history(rng)
    return json_response({"ok": True, "charts": bool(host.get("charts") or ping.get("charts")), "host": host,
                          "ping": ping})


def _rule():
    """The rule of the ▲/▼ marks (NS-D25, ``gateway.hostmon.dev``; ``host set --dev-threshold``)."""
    from .. import state
    from ..gw import model
    return model.dev_rule(state.load())


@route("GET", r"/api/host/score")
def host_score(req):
    return json_response(hostscore.node_doc())


@route("GET", r"/api/host/synth")
def host_synth(req):
    """Круг 1, Т1 (решение владельца 27.10): «сухая синтетика» — балл 0–1000 восьми звеньев и итог для радара «Балл
    сервера» (``bench.hostscore.node_synth``, якоря без калибровки B9). Only the panel; no parameters (any — 400)."""
    for k in (req.query or {}):
        raise HttpError(400, tr("panel.host.badQuery"), data={"field": k if isinstance(k, str) and len(k) <= 32
                                                              else "query"})
    return json_response(hostscore.node_synth())


@route("GET", r"/api/host/day/(?P<day>[^/]{1,32})")
def host_day(req):
    doc = hostscore.day_doc(score.load_spec(), req.match.group("day"), rule=_rule())
    if doc is None:
        raise HttpError(404, tr("panel.host.badDay"), data={"field": "day"})
    return json_response(doc)


SHARE_QUERY = ("tariff", "country", "cpu")      # bench.share.FLAGS


@route("GET", r"/api/host/share")
def host_share(req):
    """«Поделиться» проверкой (C9): the safe answer — only the switches of the query, each "0" or "1"."""
    from ..bench import share
    q = req.query or {}
    for k, v in q.items():
        if k not in SHARE_QUERY or v not in ("0", "1"):
            raise HttpError(400, tr("panel.host.badShare"), data={"field": k if k in SHARE_QUERY else "query"})
    return json_response(share.node_share([k for k in SHARE_QUERY if q.get(k) == "1"]))


DEV_RULE_MAX = 256              # bytes of the body of POST /api/host/dev-rule (it is ≈ 11)


def _dev_pct(body):
    """The body of POST /api/host/dev-rule -> pct: an object of exactly ``pct`` — an int (not bool, not 10.0)
    score.PCT_MIN…PCT_MAX; anything else (not JSON, not an object, other keys, no pct, another type, out of the
    bounds) — 400 with data.field "pct", nothing written."""
    import json
    bad = HttpError(400, tr("panel.host.badDevRule", lo=score.PCT_MIN, hi=score.PCT_MAX), data={"field": "pct"})
    try:
        b = json.loads(body.decode("utf-8")) if body else None
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise bad
    if not isinstance(b, dict) or set(b) != {"pct"}:
        raise bad
    pct = b["pct"]
    if isinstance(pct, bool) or not isinstance(pct, int) or not score.PCT_MIN <= pct <= score.PCT_MAX:
        raise bad
    return pct


@route("POST", r"/api/host/dev-rule")
def host_dev_rule(req):
    """The threshold of the ▲/▼ marks (NS-D25, ``gateway.hostmon.dev.pct``) from the panel: the work of ``host set
    --dev-threshold`` (``host_cli.apply_set``); ``use_spread`` stays as it is."""
    from .. import host_cli
    if len(req.body or b"") > DEV_RULE_MAX:
        return _refused(413, "too_big", tr("panel.host.devRuleTooBig"))
    pct = _dev_pct(req.body)
    try:
        doc = host_cli.apply_set(dev_pct=pct)
    except KaskadError as e:
        raise core_error(e)
    rule = doc["dev_rule"]
    return json_response({"ok": True, "dev_rule": {"pct": rule["pct"], "use_spread": rule["use_spread"]},
                          "changed": "dev" in doc["changed"]})


def _tariff_body(b):
    """The body of POST /api/host/tariff checked for its types (the values themselves — ``bench.tariff``)."""
    from ..bench import tariff as btariff
    values, clear, rev, dry = b.get("values"), b.get("clear"), b.get("rev"), b.get("dry", False)
    if values is not None and (not isinstance(values, dict) or len(values) > len(btariff.FIELDS)):
        raise HttpError(400, tr("panel.host.badTariff"), data={"field": "values"})
    if clear is not None and (not isinstance(clear, list) or len(clear) > len(btariff.FIELDS) + 1 or
                              not all(isinstance(x, str) and len(x) <= 32 for x in clear)):
        raise HttpError(400, tr("panel.host.badTariff"), data={"field": "clear"})
    if rev is not None and (not isinstance(rev, str) or not _REV.fullmatch(rev)):
        raise HttpError(400, tr("panel.host.badTariff"), data={"field": "rev"})
    if not isinstance(dry, bool):
        raise HttpError(400, tr("panel.host.badTariff"), data={"field": "dry"})
    return values or {}, clear or [], rev, dry


@route("GET", r"/api/host/tariff")
def host_tariff_get(req):
    from .. import host_tariff
    return json_response(host_tariff.show_doc())


@route("POST", r"/api/host/tariff")
def host_tariff_post(req):
    from .. import host_tariff
    values, clear, rev, dry = _tariff_body(req.json())
    try:
        doc = host_tariff.apply(values, clear, dry=dry, rev=rev, panel=True)
    except KaskadError as e:
        raise core_error(e)
    return json_response(doc)


# ── the schedule of the checks (§2.6; B13): the work — ``host_sched`` (the same for kaskadctl and MCP, B14) ──

BODY_MAX = host_sched.BODY_MAX
BY, STATUS, RANGE_DAYS, YEARS = host_sched.BY, host_sched.STATUS, host_sched.RANGE_DAYS, host_sched.YEARS
_TS = re.compile(r"[0-9]{1,10}")
_LIMIT = re.compile(r"[0-9]{1,3}")
_REV = re.compile(r"[0-9a-f]{10}")             # bench.tariff.rev: ASCII hex only (REV-ns-c1 П-1)
_QVAL_MAX = 32


def _refused(status, code, text, **more):
    body = {"ok": False, "error": text, "code": code}
    body.update(more)
    return json_response(body, status=status)


def _refusal(r):
    """``host_sched.Refused`` -> the answer of the panel."""
    return _refused(r.status, r.key, str(r), **r.more)


def _body(req):
    """The body of the schedule -> (entries, opts); a refusal — ``host_sched.Refused`` (413, 400)."""
    if len(req.body or b"") > BODY_MAX:
        raise host_sched.Refused(413, "too_big", tr("panel.bench.tooBig", max=BODY_MAX // 1024))
    return host_sched.check(req.json())


def _answer(doc):
    return json_response(doc, headers={"ETag": doc["etag"]})


@route("GET", r"/api/host/bench/schedule")
def bench_schedule(req):
    return _answer(host_sched.read())


@route("POST", r"/api/host/bench/schedule/preview")
def bench_schedule_preview(req):
    try:
        entries, opts = _body(req)
    except host_sched.Refused as r:
        return _refusal(r)
    return json_response(host_sched.preview(entries, opts))


@route("PUT", r"/api/host/bench/schedule")
def bench_schedule_put(req):
    try:
        entries, opts = _body(req)
        got = (req.headers.get("if-match") or "").strip()
        if not got:
            return _refused(428, "need_etag", tr("panel.bench.needEtag"))
        return _answer(host_sched.save(entries, opts, got))
    except host_sched.Refused as r:
        return _refusal(r)


@route("POST", r"/api/host/bench")
def bench_start(req):
    """«Проверить сервер» (6C): the price and the key of consent as MCP ``host_bench``, the limits as the schedule.
    A browser test of the path runs now (its lease) — 409 ``speed_test`` with ``retry_after``: both load the channel
    of the server (the owner's decision 24.10, HANDOFF r1 «409 и проверка сервера»)."""
    if len(req.body or b"") > host_bench_cli.START_MAX:
        return _refused(413, "too_big", tr("panel.bench.tooBig", max=1))
    wait = speed.SPEED.busy(time.time(), time.monotonic())
    if wait:
        return _refused(409, "speed_test", tr("panel.bench.speedTest", s=wait), retry_after=wait)
    try:
        return json_response(host_bench_cli.panel_start(req.json()))
    except host_sched.Refused as r:
        return _refusal(r)


@route("GET", r"/api/host/bench/price", runs=True)          # before /api/host/bench/<rid> (bench_run)
def bench_price(req):
    """The window of the price reads it here (T-3377 Н-15): 200 with the price, the limit and «уже идёт» in the body,
    not a refusal of POST per opening; the start — POST with the key, as before."""
    _query(req, ())
    return json_response(_price_once())


def _price_once():
    """``host_bench_cli.panel_price`` one at a time (REV-3377-stand-fix 1): a request while it is counted waits for
    that count and gets its answer (or its error) — no second round of ``systemctl`` / ``apt-get -s`` /
    ``apt-cache``; the next one after it counts anew (packages may have been installed since). The count ends by the
    timeouts of its commands, so a waiter does not wait for ever."""
    with _PRICE_LOCK:
        f = _PRICE["flight"]
        lead = f is None
        if lead:
            f = _PRICE["flight"] = {"done": threading.Event(), "out": None, "err": None, "n": 0}
        f["n"] += 1                                  # the requests that share this count (tests)
    if lead:
        try:
            f["out"] = host_bench_cli.panel_price()
        except BaseException as e:                   # noqa: BLE001 — the waiters get the same error
            f["err"] = e
        finally:
            with _PRICE_LOCK:
                _PRICE["flight"] = None
            f["done"].set()
    else:
        f["done"].wait()
    if f["err"] is not None:
        raise f["err"]
    return f["out"]


# ── the history of the checks (§3.4; B13) ──────────────────────────────────

def _query(req, allowed):
    """The parameters of the query: only ``allowed``, each ≤ 32 characters; else 400."""
    q = req.query or {}
    for k, v in q.items():
        if k not in allowed:
            raise HttpError(400, tr("panel.bench.badQuery", field=bsched._key_shown(k)), data={"field": "query"})
        if not isinstance(v, str) or len(v) > _QVAL_MAX:
            raise HttpError(400, tr("panel.bench.badValue", field=k), data={"field": k})
    return q


def _period(q, now):
    """from/to of the query -> (frm, to) for ``history`` (``host_sched.period``: real dates, the year 2000–2100,
    ``to`` not before ``from``, ≤ RANGE_DAYS days)."""
    try:
        return host_sched.period(q.get("from"), q.get("to"), now)
    except host_sched.Refused as r:
        raise HttpError(400, str(r), data={"field": r.more.get("field")})


def _pick(q, k, allowed):
    v = q.get(k)
    if v is None or v == "":
        return None
    if v not in allowed:
        raise HttpError(400, tr("panel.bench.badValue", field=k), data={"field": k})
    return v


@route("GET", r"/api/host/bench")
def bench_runs(req):
    q = _query(req, ("from", "to", "mode", "by", "status", "before", "limit"))
    now = int(time.time())
    frm, to = _period(q, now)
    mode, by, status = _pick(q, "mode", bsched.MODES), _pick(q, "by", BY), _pick(q, "status", STATUS)
    before = q.get("before") or None
    if before is not None and not _TS.fullmatch(before):
        raise HttpError(400, tr("panel.bench.badValue", field="before"), data={"field": "before"})
    limit = q.get("limit") or None
    if limit is not None and not (_LIMIT.fullmatch(limit) and 1 <= int(limit) <= history.MAX_LIMIT):
        raise HttpError(400, tr("panel.bench.badValue", field="limit"), data={"field": "limit"})
    doc = history.runs(frm, to, mode=mode, by=by, status=status, limit=int(limit) if limit else 200,
                       before=int(before) if before else None, now=now, rel=True)
    return json_response(dict(doc, ok=True, charts=bool(doc.get("db") or doc["runs"] or metrics.available())))


def _rid(v):
    if not (isinstance(v, str) and spool.ID.fullmatch(v)):
        raise HttpError(404, tr("panel.bench.notFound"), data={"field": "id"})
    return v


@route("GET", r"/api/host/bench/compare")
def bench_compare(req):
    q = _query(req, ("a", "b"))
    doc = history.compare(_rid(q.get("a")), _rid(q.get("b")))
    if doc is None:
        raise HttpError(404, tr("panel.bench.notFound"), data={"field": "id"})
    return json_response(dict(doc, ok=True))


@route("GET", r"/api/host/bench/export")
def bench_export(req):
    q = _query(req, ("format", "from", "to"))
    fmt = _pick(q, "format", ("csv", "json")) or "csv"
    now = int(time.time())
    frm, to = _period(q, now)
    name, ctype, body = host_sched.export_body(fmt, frm, to, now)
    return Response(200, body, {"Content-Type": ctype, "Cache-Control": "no-store",
                                "Content-Disposition": 'attachment; filename="%s"' % name})


@route("GET", r"/api/host/bench/(?P<rid>[^/]{1,64})")      # the last of /api/host/bench/…: schedule, compare, export
def bench_run(req):                                         # are taken above
    _query(req, ())
    doc = history.run(_rid(req.match.group("rid")), rel=True)
    if doc is None:
        raise HttpError(404, tr("panel.bench.notFound"), data={"field": "id"})
    return json_response(dict(doc, ok=True))
# ── the browser test (SPEC-browser-channel §4) ─────────────────────────────

SPEED_TEXTS = {"busy": "panel.speed.err.busy", "hour": "panel.speed.err.hour", "day": "panel.speed.err.day",
               "traffic": "panel.speed.err.traffic", "expired": "panel.speed.err.expired",
               "lease": "panel.speed.err.lease", "streams": "panel.speed.err.streams",
               "budget": "panel.speed.err.budget", "too_big": "panel.speed.err.tooBig",
               "bytes": "panel.speed.err.bytes", "store": "panel.speed.err.store"}
_BYTES_RE = re.compile(r"^[0-9]+$")          # ASCII digits; 20+ of them — too big (413) without int()


def _speed_refused(e):
    """HttpError of a ``speed.Refusal``: its status, the text in the reader's language, ``code`` — its word, ``data``
    — the numbers of the answer (``retry_after`` becomes the header Retry-After)."""
    d = e.data or {}
    key = "panel.speed.err.check" if e.kind == "busy" and d.get("what") == "check" else SPEED_TEXTS[e.kind]   # М-5
    return HttpError(e.status, tr(key, **({"used": d.get("used"), "limit": d.get("limit")}
                                          if e.kind in ("hour", "day") else {})),
                     code=e.kind, data=e.data)


def _state():
    try:
        return state.load()
    except Exception:                                # noqa: BLE001 — a broken state.json: the default limits
        return {}


def _timing(t0):
    return "app;dur=%.1f" % ((time.monotonic() - t0) * 1000.0)


def _own_addrs(st):
    from . import server
    out = list(model.own_addrs(st))
    p = server.PANEL
    if p is not None:
        try:
            out += p._listen_addrs()
        except Exception:                            # noqa: BLE001 — never a reason to refuse the test
            pass
    return out


def _cascade_addrs(st):
    """The addresses of the other nodes this one knows (§4.7 п. 3): the targets of its forwards and the servers of the
    exits of the deployed xray config — only those given as an address (no name is resolved here)."""
    out = []
    for fw in (st.get("forwards") or []) if isinstance(st, dict) else []:
        if isinstance(fw, dict) and fw.get("to"):
            out.append(str(fw["to"]))
    try:
        from ..gw import exits, xray
        with open(xray.config_path(), encoding="utf-8") as f:
            cfg = json.load(f)
        for ob in (cfg.get("outbounds") or []) if isinstance(cfg, dict) else []:
            host, _port = exits.outbound_server(ob)
            if host:
                out.append(host)
            for peer in ((ob.get("settings") or {}).get("peers") or []) if isinstance(ob, dict) else []:
                ep = str((peer or {}).get("endpoint") or "") if isinstance(peer, dict) else ""
                if ep:
                    out.append(ep.rsplit(":", 1)[0].strip("[]"))
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    good = []
    for a in out:
        try:
            ipaddress.ip_address(a.strip().strip("[]"))
            good.append(a)
        except ValueError:
            continue
    return good


def _fronts():
    from ..site import front
    return front.xray_fronts(sysrun.default_runner())


def _fronts_file():
    """The same by the deployed xray config alone — no command (``budget`` is a GET that runs nothing)."""
    from ..site import front
    return front.xray_fronts(None)


BENCH_EST_S = {"quick": 65, "full": 125}         # a server check from its start to its end, about (HANDOFF r1)


def _bench_busy(runner, now):
    """A server check r1 runs now (button, schedule, retry) -> the seconds it is still expected to take; 0 — none.
    The browser test waits for it: both load the channel of the server (the owner's decision 24.10)."""
    from ..bench import run as brun
    busy = brun.active(runner)
    if not busy:
        return 0
    meta = {}
    try:
        with open(os.path.join(brun.work_dir(busy[0]), "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    est = BENCH_EST_S.get(meta.get("mode") if isinstance(meta, dict) else None, BENCH_EST_S["quick"])
    ts = meta.get("ts") if isinstance(meta, dict) else None
    left = est - (now - ts) if type(ts) is int else est
    return max(5, int(math.ceil(left)))


@route("POST", r"/api/host/speed/start", head=False)
def speed_start(req):
    st = _state()
    lim, notes = speed.limits(st)
    speed.note_limits(st, notes)
    runner = sysrun.default_runner()
    wait = _bench_busy(runner, time.time())                  # a server check runs: 409 as another test (§15 п. 6)
    if wait:
        raise _speed_refused(speed.Refusal(409, "busy", {"busy": True, "retry_after": wait, "what": "check"}))
    via = speed.via_of(req.listener, _fronts)                # before the lease: nothing here can leave it hanging
    vpn = speed.via_vpn(req.client_ip, _own_addrs(st), _cascade_addrs(st), runner)
    try:
        out = speed.SPEED.start(speed.bind_key(req.session), lim, time.time(), time.monotonic())
    except speed.Refusal as e:
        raise _speed_refused(e)
    out["via"], out["vpn"] = via, vpn
    return json_response(out)


def _lease(req):
    try:
        return speed.SPEED.lease(speed.bind_key(req.session), req.query.get("id"), time.time(), time.monotonic())
    except speed.Refusal as e:
        raise _speed_refused(e)


@route("GET", r"/api/host/speed/ping", head=False)
def speed_ping(req):
    t0 = time.monotonic()
    _lease(req)
    return Response(204, b"", {"Server-Timing": _timing(t0), "Cache-Control": "no-store"})


@route("GET", r"/api/host/speed/down", head=False)
def speed_down(req):
    t0 = time.monotonic()
    L = _lease(req)
    raw = (req.query.get("bytes") or "").strip()
    if not _BYTES_RE.match(raw):
        raise _speed_refused(speed.Refusal(400, "bytes"))
    n = int(raw) if len(raw) <= 19 else speed.REQ_DOWN_MAX + 1
    if n < 1:
        raise _speed_refused(speed.Refusal(400, "bytes"))
    if n > speed.REQ_DOWN_MAX:
        raise _speed_refused(speed.Refusal(413, "too_big"))
    try:
        speed.SPEED.take(L, "down", n, time.time(), time.monotonic())
    except speed.Refusal as e:
        raise _speed_refused(e)
    return RawStream(200, n, {"Content-Type": "application/octet-stream", "X-Accel-Buffering": "no",
                              "Cache-Control": "no-store", "Server-Timing": _timing(t0)},
                     lambda write: speed.send_down(speed.SPEED, L, n, write, time.monotonic))


@route("POST", r"/api/host/speed/up", body="stream", body_max=speed.REQ_UP_MAX, head=False)
def speed_up(req):
    L = _lease(req)
    try:
        speed.SPEED.take(L, "up", req.length, time.time(), time.monotonic())
    except speed.Refusal as e:
        raise _speed_refused(e)
    got = speed.recv_up(speed.SPEED, L, req.rfile, time.monotonic)
    return json_response({"ok": True, "bytes": got})


@route("POST", r"/api/host/speed/finish", head=False)
def speed_finish(req):
    lid = req.json().get("id")
    try:
        out = speed.SPEED.finish(speed.bind_key(req.session), lid, time.time(), time.monotonic())
    except speed.Refusal as e:
        raise _speed_refused(e)
    return json_response(out)


@route("GET", r"/api/host/speed/budget", head=False)
def speed_budget(req):
    st = _state()
    lim, notes = speed.limits(st)
    speed.note_limits(st, notes)
    out = speed.SPEED.budget(lim, time.time(), time.monotonic())
    out["via"] = speed.via_of(req.listener, _fronts_file)    # «через порт VPN» до старта (§6.4) — без команд
    return json_response(out)
