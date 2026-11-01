"""``kaskadctl summary`` — what is going on with the node at a glance: the header of the SSH menu (gokaskad) and its
screen «Что не так — проверка сервера» (docs/k3/SPEC-ssh-menu.md).

    summary [--json]           files only, nothing runs (the menu draws it on every return): state.json, live.json of
                               the collector, the last report of the minute watchdog (/run/kaskad/watch.json), the
                               pool and the exits snapshot, the AmneziaWG engine, the panel settings, the certificate
                               copy of the domain, the cache of the last update check (/etc/kaskad/update.json)
    summary --check [--json]   the same, but the watchdog's check runs now (``net check`` without repairs: the
                               forwarding table and ``gw check``) instead of its last report — seconds, not a draw

The problems are not judged here. They are the core's own rules: the collector's (live.json ``gateway.issues`` — the
panel's «Что не в порядке»), the watchdog's (netcheck.check with gw.ops.check: what it could not repair), the red
line of menu 17 (gw.ops.no_exits_problem, as ``gw check``) and the panel's warning about an empty pool
(exits.offline_empty), one server of an exit twice in the pool (exits.same_peer_problems). This module only adds where
a person fixes each: the item of the SSH menu and a short advice.

The answer (``--json``)::

    {"ok": true, "version": "3.3.0", "engine": "nft"|"legacy", "forwards": 4|null,
     "gateway": {"setup", "enabled", "host", "users", "devices", "online": n|null,
                 "inbounds": [{"kind", "title", "short", "state": "run"|"down"|"off"|"unknown"}]},
     "exits": {"total", "alive": n|null, "via": "pool"|"exit:<id>"|"direct"|"block", "fail", "error": str|null},
     "awg": {"has": bool, "active": "kernel"|"program", "offer": bool} | null,
     "site": {"domain", "name", "mode": "domain"|"site", "cert": {"kind", "end", "days_left"}|null} | null,
     "panel": {"installed", "public_url": str|null, "masked": str|null},
     "update": {"latest", "branch", "at"} | null,
     "live": {"age": s|null, "fresh": bool}, "watch": {"age": s|null}, "checked": bool,
     "rf": {"mode": "full"|"legacy"|"need"|"none", "uncovered_names": str} | null,
     "host": {"level": "ok"|"warn"|"crit"|"wait"|"off"} | null,
     "addresses": {"show": ip|"", "more": n, "client_found": bool|null, "count": n} | null,
     "problems": [{"kind", "detail", "item", "advice", "source": "live"|"watch"|"check"|"rule"}]}

``addresses`` — «Адреса сервера» from their cache (gw/addresses.py, PLAN-multi-ip Ш1, Л4): the IP of the header (the
address for clients when it is the server's, else the primary one) and how many more; null before the first look
(the menu looks at its start: ``kaskadctl gw addresses``).

No secrets: the panel's path only masked (conf.mask_path), keys of the pool never (exits rows are public ones).
"""

import json
import os
import re
import time

from . import __version__, errors, netcheck, paths, state, sysrun, util
from .cli import add_json_flag, emit
from .errors import KaskadError, StateError
from .i18n import fmt_date, tr

WATCH_FRESH = 180           # s: the watchdog runs every minute; an older report is no news about now
CERT_SOON = 21              # days: the yellow of menu 19 (certbot renews at 30 — under 21 the renewal does not work)
SHORT = {"awg1": "AWG 1.5", "awg2": "AWG 2.0", "awg3": "AWG 3.1", "l2tp": "L2TP", "vless": "VLESS", "hy2": "Hysteria2"}
_VER = re.compile(r"^[0-9][0-9A-Za-z.-]{0,31}$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,64}$")

# kind of a problem -> item of the SSH menu (0 — none: fixed outside Kaskad); what to do — tr("summary.advice.<kind>").
# Kinds of the watchdog not named here: «gw…» — the gateway (17), «host_…» — the health of the node (22), the rest — the
# forwarding engine (16).
ADVICE = {
    "no_exits": 17,
    "exits_dead": 17,
    "gw_inbound": 17,
    "gw_service": 17,
    "gw_collect": 17,
    "gw_clock": 0,              # no item: nothing in the menu fixes the clock — a time service does; the command is in
    #                             the text of the problem, the advice says only what follows (REV-s1-b4 П-2)
    "gw_conflict": 17,
    "gw_data": 17,
    "gw_unfinished": 17,
    "gw_xray_ru_open": 17,      # B7: not repaired by the watchdog — its own words (REV-s1-b6-sec Н-2)
    "gw": 17,
    "command": 11,
    "policy_rc_d": 16,
    "state": 16,
    "site_cert": 19,
    "site_nginx": 19,
    "net": 16,
    "exits_same": 8,            # the pool is deleted from in 8 → 7 → F3 (stand T-335-release С1), not in 17 → 3
    "gw_host_gone": 17,         # PLAN-multi-ip Ш2 (вопрос 4): the address for clients is not on the server — 17 → 4
    "host": 22,                 # A8: an alarm of the light health of the node (NS-D4) — item 22 «Нагрузка и сеть
    #                             сервера»; a kind ``host_<group>`` of gw.hostmon.HOST_KINDS has its own advice
}
for _g in ("cpu", "steal", "io", "mem", "disk", "net", "conntrack", "clock", "cert", "loss"):
    ADVICE["host_" + _g] = 22


def advice(kind):
    kind = str(kind or "")
    if kind not in ADVICE:
        kind = "gw" if kind.startswith("gw") else ("host" if kind.startswith("host_") else "net")
    return ADVICE[kind], tr("summary.advice." + kind)


def problem(kind, detail, source):
    item, text = advice(kind)
    return {"kind": kind, "detail": util.redact(str(detail)), "item": item, "advice": text, "source": source}


def _norm(text):
    return re.sub(r"[\s.:;,!]+$", "", " ".join(str(text).split())).lower()


def dedupe(probs):
    """One line per trouble: the collector and the watchdog may both see one inbound down («Вход L2TP/IPsec не
    работает» / «вход L2TP/IPsec не работает»), the xray line of the collector says more («…: SOCKS5 и выходы VLESS
    недоступны»). Equal or one the start of the other — the longer stays, at the place of the first."""
    out = []
    for p in probs:
        n = _norm(p["detail"])
        for i, q in enumerate(out):
            m = _norm(q["detail"])
            if n == m or n.startswith(m) or m.startswith(n):
                if len(n) > len(m):
                    out[i] = p
                break
        else:
            out.append(p)
    return out


# ── parts ──────────────────────────────────────────────────────────────────

def _host(st):
    """The light of the health of the node (item 22) from the files of the collector or the bot — never a reading of
    its own: the header must not say «Неполадок не видно» while item 22 says «Есть предупреждения» (К2-3, T-KT2). None —
    nobody has judged it lately."""
    from .gw import hostprobe
    try:
        h, _by = hostprobe.read_health(st=st, instant=lambda *a: None)
    except Exception:                                       # noqa: BLE001 — the header never fails for it
        return None
    lvl = h.get("level") if isinstance(h, dict) else None
    return {"level": lvl} if lvl in ("ok", "warn", "crit", "wait", "off") else None


def _live():
    from .gw import live
    doc, age = live.read()
    return (doc if live.fresh(age) else None), age


def _inbounds(st, lv):
    """The inbounds as the header draws them: run (the collector saw it running), down, off (switched off, or the
    gateway is), unknown (no fresh collector)."""
    from .gw import model, registry
    on = model.enabled(st)
    lin = (lv or {}).get("inbounds") or {}
    out = []
    for kind in model.installed_kinds(st):
        rec = model.inbound(st, kind) or {}
        try:
            ad = registry.get(kind)
        except KaskadError:
            ad = None
        title = (getattr(ad, "title", "") if ad is not None else "") or kind
        if not on or rec.get("enabled", True) is False:
            st_ = "off"
        elif lv is None:
            st_ = "unknown"
        else:
            r = (lin.get(kind) or {}).get("running") if isinstance(lin.get(kind), dict) else None
            st_ = "run" if r is True else ("down" if r is False else "unknown")
        out.append({"kind": kind, "title": title, "short": SHORT.get(kind, title), "state": st_})
    return out


def _exits(st, lv):
    """{"total", "alive", "via", "fail", "error"} and the rows (gw.ops.exits_view: the pool parsed here, health
    from the exits snapshot); ``alive`` — None while the collector is silent (the snapshot is not the present)."""
    from .gw import exits, ops
    r = exits.default_route(st)
    doc = {"total": len(exits.pool_lines()), "alive": None, "via": r.get("via") or "pool", "fail": r.get("fail"),
           "error": None}
    try:
        rows = ops.exits_view(None, st)
    except KaskadError as e:
        doc["error"] = util.redact(str(e))
        return doc, None
    mine = [x for x in rows if exits.parsed(x) and x.get("enabled", True)]
    doc["total"] = len(mine)
    if lv is not None:
        doc["alive"] = sum(1 for x in mine if x.get("usable"))
    return doc, rows


def _awg(st):
    from .gw import awgengine
    try:
        s = awgengine.status(None, st)
    except (KaskadError, OSError, ValueError, KeyError, TypeError):
        return None
    return {"has": bool(s.get("has_awg")), "active": s.get("active"),
            "offer": bool((s.get("offer") or {}).get("show") and not (s.get("offer") or {}).get("blocked"))}


def _site(st):
    """The domain line of the header: the name, the mode and the copy of the certificate the consumers take (as
    menu 19 shows it)."""
    from .site import certinfo, model as sm
    s = sm.peek(st)
    d = s["domain"]
    if not d:
        return None
    cert = None
    try:
        info = sm.copy_info()
    except (KaskadError, OSError, ValueError):
        info = None
    if info is not None and certinfo.covers(info, d):
        day = time.strftime("%Y-%m-%d", time.gmtime(info["not_after_ts"]))      # the UTC date, as everywhere for a certificate
        cert = {"kind": s["cert"] or "other", "end": fmt_date(day, short=True), "end_long": fmt_date(day),
                "days_left": certinfo.days_left(info),
                "staging": bool(info.get("staging"))}
    return {"domain": d, "name": sm.unicode(d), "mode": s["mode"], "cert": cert}


def _panel(st, site):
    from .gw import sys as gsys
    from .panel import conf
    try:
        cfg = conf.load()
    except (KaskadError, OSError, ValueError):
        return {"installed": os.path.exists(gsys.unit_path(conf.UNIT)), "public_url": None, "masked": None}
    masked = None
    path = cfg["proxy"].get("path") or ""
    try:
        # 3.3.7.5: the open mode is under the secret prefix as well — only masked here (the full one: site panel-url
        # --show); the main address is the domain's with the site, else the open mode's
        pub = conf.public_url_masked(cfg)
    except (KaskadError, OSError, ValueError):
        pub = None
    if site and site["mode"] == "site" and path:
        masked = "https://%s/%s/" % (site["domain"], conf.mask_path(path))
    elif pub:
        masked, pub = pub, None
    return {"installed": os.path.exists(gsys.unit_path(conf.UNIT)), "public_url": pub, "masked": masked}


def _rf(st):
    """⚠РФ for the header of the SSH menu (docs/k3/SPEC-stage1-rf-mark-builtin.md §5.2, R6): the mode of the consent
    and the organisations that came after it. Only the state and the kept result of the scan — no process; any trouble
    with the mark is not the header's: None."""
    try:
        from .rules import rfgate
        rf = rfgate.state(st)
    except (KaskadError, OSError, ValueError, KeyError, TypeError):
        return None
    unc = set(rf.get("uncovered") or [])
    return {"mode": rf.get("mode"),
            "uncovered_names": ", ".join(o["name"] for o in rf.get("orgs") or [] if o.get("id") in unc)}


def _update():
    raw = util.read_text(paths.update_cache())
    try:
        doc = json.loads(raw) if raw else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        return None
    latest, branch, at = doc.get("latest"), doc.get("branch"), doc.get("at")
    if not isinstance(latest, str) or not _VER.match(latest):
        return None
    return {"latest": latest, "branch": branch if isinstance(branch, str) and _BRANCH.match(branch) else None,
            "at": at if isinstance(at, (int, float)) and not isinstance(at, bool) else None}


def _gw_unfinished_path():
    return paths.P("/etc/kaskad/gw-wizard.unfinished")      # install.sh K3_GW_WIZ_MARK


# ── the summary ────────────────────────────────────────────────────────────

def summary(check=False, runner=None):
    from .gw import exits, model, ops
    doc = {"ok": True, "version": __version__, "engine": None, "forwards": None, "gateway": None, "exits": None,
           "awg": None, "site": None, "panel": None, "update": _update(), "live": {"age": None, "fresh": False},
           "watch": {"age": None}, "checked": bool(check), "problems": []}
    probs = []
    try:
        st = state.load()
    except StateError as e:
        doc["problems"] = [problem("state", str(e), "rule")]
        return doc
    doc["engine"] = st["engine"]
    doc["forwards"] = len(st.get("forwards") or []) if st["engine"] == "nft" else None
    lv, age = _live()
    doc["live"] = {"age": None if age is None else round(age, 1), "fresh": lv is not None}
    g = model.peek(st)
    on = model.enabled(st)
    devs = (lv or {}).get("devices") or {}
    doc["gateway"] = {"setup": bool(on or model.installed_kinds(st)), "enabled": bool(on),
                      "host": model.public_host(st), "users": len(g["users"]), "devices": len(g["devices"]),
                      "online": sum(1 for d in devs.values() if isinstance(d, dict) and d.get("online"))
                      if lv is not None else None,
                      "inbounds": _inbounds(st, lv)}
    rows = None
    if on:
        doc["exits"], rows = _exits(st, lv)
    doc["awg"] = _awg(st)
    doc["site"] = _site(st)
    doc["panel"] = _panel(st, doc["site"])
    doc["rf"] = _rf(st)
    doc["host"] = _host(st)
    adoc = _addresses(st)
    doc["addresses"] = None if adoc is None else {
        "show": adoc["show"], "more": adoc["more"], "client_found": adoc["client_found"],
        "count": len([a for a in adoc["addresses"] if a["origin"] != "host"])}

    # 1. the collector: what it saw this round (only on a gateway that is on and while it writes)
    if on and lv is not None:
        lg = lv.get("gateway") or {}
        issues = lg.get("issues")
        if not isinstance(issues, list):                       # a collector of a version before the issues
            issues = [{"kind": "gw_live", "detail": t} for t in lg.get("problems") or [] if isinstance(t, str)]
        for i in issues:
            if not isinstance(i, dict) or not isinstance(i.get("detail"), str):
                continue
            if check and i.get("kind") in ("gw_inbound", "gw_service"):
                continue                                       # the check of now says it (gw.ops.check)
            probs.append(problem(i.get("kind") or "gw_live", i["detail"], "live"))
    # 2. the watchdog: its last report (what it could not repair), or its check run now
    if check:
        res = netcheck.check(runner or sysrun.default_runner(), repair=False)
        for p in res.get("problems") or []:
            probs.append(problem(p.get("kind"), p.get("detail") or "", "check"))
    else:
        rep, rage = netcheck.read_report()
        doc["watch"] = {"age": None if rage is None else round(rage, 1)}
        if rep is not None and rage <= WATCH_FRESH:
            for p in rep:
                probs.append(problem(p.get("kind"), p["detail"], "watch"))
    # 3. no internet for the devices — the rule of menu 17 and gw check; the collector's own line about it goes
    if on:
        dead = ops.no_exits_problem(st, rows) if rows is not None else None
        if dead is None and exits.offline_empty(st):
            dead = {"kind": "no_exits", "detail": exits.NO_EXITS_OFFLINE}
        if dead:
            probs = [p for p in probs if p["kind"] != "exits_dead"]
            probs.insert(0, problem("no_exits", dead["detail"], "rule"))
    # 4. an install of the gateway broken off (install.sh K3_GW_WIZ_MARK: the wizard installed, «Включаю шлюз…» died)
    if not on and model.installed_kinds(st) and os.path.exists(_gw_unfinished_path()):
        probs.append(problem("gw_unfinished", tr("summary.gw_unfinished"), "rule"))
    # 5. the certificate of the domain: expired, or Let's Encrypt not renewed in time
    cert = (doc["site"] or {}).get("cert")
    if cert:
        name = doc["site"]["name"]
        if cert["days_left"] < 0:
            probs.append(problem("site_cert", tr("summary.cert_expired", name=name, end=cert["end_long"]), "rule"))
        elif cert["kind"] == "letsencrypt" and cert["days_left"] < CERT_SOON:
            probs.append(problem("site_cert", tr("summary.cert_soon", name=name, end=cert["end_long"],
                                                 days=cert["days_left"]), "rule"))
    # 6. one server of an exit twice in the pool (vpn:// and .conf of one peer: T-X6) — on or off, the pool is used by
    # the bot too; a new one is never added (install.sh egress_add_key, subscriptions), an old one stays until removed
    try:
        for d in exits.same_peer_problems(st, exits.pool(None, roles=exits.ROLES)):
            probs.append(problem("exits_same", d, "rule"))
    except KaskadError:
        pass
    # 7. the address for clients is an IPv4 the server does not have (it went away at the hoster): a warning and the way
    # to change it, never a move by itself (PLAN-multi-ip §5 вопрос 4, решение координатора 29.10)
    gone = _addresses_gone(adoc, st)
    if gone:
        probs.append(problem("gw_host_gone", gone, "rule"))
    doc["problems"] = dedupe(probs)
    return doc


def _addresses(st):
    """«Адреса сервера» from the cache (files only), None before the first look or when it cannot be read."""
    try:
        from .gw import addresses
        return addresses.cached(st)
    except Exception:                                   # noqa: BLE001 — a broken cache never breaks the header
        return None


def _addresses_gone(adoc, st):
    try:
        from .gw import addresses
        return addresses.gone_problem(adoc, st)
    except Exception:                                   # noqa: BLE001
        return None


def text_lines(doc):
    lines = ["Kaskad %s" % doc["version"]]
    if doc["engine"]:
        lines.append(tr("summary.text.forwards", n="—" if doc["forwards"] is None else doc["forwards"],
                        engine="nftables" if doc["engine"] == "nft" else "iptables"))
    g = doc.get("gateway") or {}
    if g.get("setup"):
        marks = {"run": "●", "down": "✗", "off": "○", "unknown": "?"}
        lines.append(tr("summary.text.gateway", state=tr("summary.text.on" if g["enabled"] else "summary.text.off"),
                        inbounds="".join("  %s %s" % (marks[i["state"]], i["short"]) for i in g["inbounds"])))
    ex = doc.get("exits")
    if ex:
        lines.append(tr("summary.text.exits", alive="?" if ex["alive"] is None else ex["alive"], total=ex["total"]))
    if doc["problems"]:
        lines.append(tr("summary.text.problems"))
        for p in doc["problems"]:
            lines.append("  ✗ %s — %s" % (p["detail"], p["advice"]))
    else:
        lines.append(tr("summary.text.no_problems"))
    return lines


def cmd_summary(args):
    doc = summary(check=args.check)
    emit(args, doc, text_lines(doc))
    return errors.OK


def register(subparsers):
    p = add_json_flag(subparsers.add_parser("summary", help=tr("summary.help")))
    p.add_argument("--check", action="store_true",
                   help=tr("summary.help_check"))
    p.set_defaults(func=cmd_summary)
