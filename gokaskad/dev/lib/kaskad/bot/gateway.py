"""The gateway in the bot (docs/k3/SPEC-wave3.md §4.2).

Reading — state.json and live.json through kaskad.snapshot (no forks, no kaskadctl); changing — the same
``kaskad.gw.ops`` calls as ``kaskadctl gw`` (under the state lock), then ``snapshot.bump()``.

    gw                     the gateway and its inbounds (live.json: running, sessions), exits, people, problems
    gwp / gwu:<user>       people and their devices (paginated)
    gwd:<dev>              a device: platform, inbounds, addresses, route, online now
    gwa → user → platform → name → inbounds (preselected by ops.offers) → gw device add → «Выдать»
    gwi:<dev>[/gwis]       issuing — the chat's job: files (.conf, .ovpn, .mobileconfig, .ps1, certificates) as
                           documents, QR codes as PNG (``qrencode -t PNG -o -``, the text on stdin, never argv),
                           vpn:// and links as <code> text, fields as «name: value»
    gwr:<d|u>:<id>         route: inherit / pool / exit / direct / block, then the fail policy for pool and exits
    gwk / gwdl             new keys and passwords / delete — both with a confirmation
    gwx                    exits alive / dead / not checked yet
    gwt:<d-…|u-…|->        «Куда пойдёт трафик?»: then every text is a domain or an IP — the path step by step
                           (kaskad.gw.trace: the answer of the panel's scheme and of ``kaskadctl gw trace``)
    gwe / gwek / gwep      the engine of AmneziaWG (gw/awgengine.py): what runs and why; «Перейти на модуль ядра» /
                           «на программу» — a confirmation, then the switch as the chat's job (backup, self-test, the
                           clients — up to 5 minutes); the gateway screen carries the advice after an update (pending)
    gwmr / gwms / gwmk:<k> §6.1: a narrow card — «Выдать новые настройки с меньшим MTU» (a confirmation, then the devices
                           to give them to); a server below 1232 (S4 of 2.0) — «Новые настройки с меньшим S4» (the devices
                           first: the old settings stop connecting, then a confirmation); «Оставить как есть». The gateway
                           screen carries the warnings and the devices that wait for new settings
    gwl                    a one-time login link of the web panel (kaskad.panel.auth, when it is there); on the
                           certificate of Let's Encrypt of the domain — its name and date instead of the fingerprint

Secrets appear only in what «Выдать» sends to the chat; nothing of an issue goes to a log.
"""

import importlib
import os
import shutil
import subprocess

from .. import snapshot, util
from ..errors import KaskadError
from . import data, menus
from .lang import fmt_bytes, tr, trn
from .registry import callback, reply_key, state
from .telegram import ApiError, h, inline

QRENCODE = "qrencode"
QR_TIMEOUT = 20
FILE_FORMATS = ("conf", "ovpn", "mobileconfig", "ps1")
EXITS_SHOWN = 30
PANEL_PORT = 1980                   # the panel's default 127.0.0.1:1980 (the way in: an SSH tunnel)
PANEL_TTL = 3600                    # s a one-time link lives (§3.1)


def qr_missing():
    return tr("bot.gw.qr_missing")


def _ops():
    from ..gw import ops
    return ops


def _model():
    from ..gw import model
    return model


def _st():
    return data.state_doc()


def _back_gw():
    return [(tr("bot.gw.btn_back_gw"), "gw")]


def _cancel_row():
    return [menus.cancel_btn("gw")]


# ── the gateway screen ─────────────────────────────────────────────────────

def _age(live, now):
    ts = live.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return max(0, int(now - ts))
    return None


def _group_name(st, gid):
    """The shown name of a group (``policy.label``, §3.1): «Основная»/«Main» for «main» with no name of its own, its name
    after a rename; the policy not readable — the label of the id."""
    from ..gw import policy as P
    try:
        grp = P.group(P.load(st if isinstance(st, dict) else {}), gid)
    except Exception:                               # noqa: BLE001 — a name only: the label of the id then
        grp = None
    return P.label(grp or {"id": gid})


def _route_text(r, st):
    if not r:
        return ""
    via = r.get("via") or ""
    if via == "pool":                               # stage 1 (§7.10): the pool is the group «main» — by its name
        s = tr("bot.gw.route_pool", name=_group_name(st, "main"))
    elif via == "direct":
        s = tr("bot.gw.route_direct")
    elif via == "block":
        s = tr("bot.gw.route_block")
    elif via.startswith("exit:"):
        eid = via[5:]
        ex = data.live_doc().get("exits")
        rec = ex.get(eid) if isinstance(ex, dict) else None
        label = rec.get("label") if isinstance(rec, dict) else None
        name = _model().exit_pref(st, eid).get("name") or (label if isinstance(label, str) else "") or eid
        s = tr("bot.gw.route_exit", name=name)
    elif via.startswith("group:"):                  # stage 1: the name of the group, not its id (REV-s1-b4)
        s = tr("bot.gw.route_group", name=_group_name(st, via[len("group:"):]))
    else:
        s = via
    from ..gw import exits                          # the fail step as the decision takes it (REV-s1-b2 З1)
    if exits.fail_of(st if isinstance(st, dict) else {}, r) == "direct":
        s += tr("bot.gw.route_fail_direct")
    return s


def status_text(st, live, now):
    model = _model()
    g = model.peek(st)
    installed = model.installed_kinds(st)
    if not g["enabled"] and not installed and not g["users"]:
        return tr("bot.gw.not_set")
    lines = [tr("bot.gw.title_on" if g["enabled"] else "bot.gw.title_off")]
    host = model.public_host(st)
    if host:
        lines.append(tr("bot.gw.client_host", host=h(host)))
    ib_live = live.get("inbounds") if isinstance(live.get("inbounds"), dict) else {}
    lines += ["", tr("bot.gw.inbounds_head")]
    if not installed:
        lines.append(tr("bot.gw.inbounds_none"))
    for kind in installed:
        rec = model.inbound(st, kind) or {}
        title = menus.inbound_title(kind)
        if isinstance(rec.get("label"), str) and rec["label"]:     # T13: the owner's label — only shown
            title = tr("gw_rename.inbound_shown", title=title, label=rec["label"])
        info = ib_live.get(kind) if isinstance(ib_live.get(kind), dict) else None
        if not rec.get("enabled", True):
            word = tr("bot.gw.ib_off")
        elif not g["enabled"]:
            word = tr("bot.gw.ib_gw_off")
        elif info is None:
            word = tr("bot.gw.ib_no_data")
        elif info.get("running"):
            n = info.get("sessions")
            word = tr("bot.gw.ib_running") + (tr("bot.gw.ib_sessions", n=n)
                                              if isinstance(n, int) and not isinstance(n, bool) else "")
        else:
            word = tr("bot.gw.ib_down")
        ports = (info or {}).get("ports") or ("%s" % rec.get("port") if rec.get("port") else "")
        lines.append("• %s — %s%s" % (h(title), word, (" (%s)" % h(ports)) if ports else ""))
        if kind == "vless":                     # TLS or Reality and why, the recommendation (§4.2): no consent here
            from . import site as bsite
            lines += ["   %s" % x for x in bsite.vless_lines(st)]
    ex_live = live.get("exits") if isinstance(live.get("exits"), dict) else None
    if ex_live is not None:
        alive = sum(1 for e in ex_live.values() if isinstance(e, dict) and e.get("alive"))
        lines.append("")
        lines.append(tr("bot.gw.exits_line", alive=alive, total=len(ex_live)))
    ndev = len(g["devices"])
    online = sum(1 for d in (live.get("devices") or {}).values() if isinstance(d, dict) and d.get("online")) \
        if isinstance(live.get("devices"), dict) else None
    lines.append(tr("bot.gw.people_line", users=len(g["users"]), devices=ndev,
                    online=tr("bot.gw.online_part", n=online) if online is not None else ""))
    gw_live = live.get("gateway") if isinstance(live.get("gateway"), dict) else {}
    probs = gw_live.get("problems") if isinstance(gw_live.get("problems"), list) else []
    for p in probs[:5]:
        lines.append("⚠️ %s" % h(util.redact(p)))
    if g["enabled"]:
        age = _age(live, now)
        if age is None:
            lines.append("\n" + tr("bot.gw.collector_none"))
        elif age > 60:
            lines.append("\n" + tr("bot.gw.collector_stale", min=age // 60))
    return "\n".join(lines)


def _engine(st):
    """The engine of AmneziaWG for the screens (files and the pool parsed here: nothing runs), None on a failure."""
    try:
        from ..gw import awgengine
        return awgengine.status(None, st)
    except (KaskadError, OSError, ValueError):
        return None


def engine_advice(eng):
    """The line of the gateway screen: the advice «Перейти на модуль ядра» (after an update), or why not yet."""
    if not eng or not eng["offer"]["show"]:
        if eng and eng["active"] == "kernel" and eng["warnings"]:
            return "⚠️ %s" % h(eng["warnings"][0])
        return ""
    if eng["offer"]["blocked"]:
        return tr("bot.gw.engine_blocked", why=h(eng["offer"]["blocked"][0]))
    return tr("bot.gw.engine_advice")


@callback("gw")
def cb_gateway(ctx, d):
    st = _st()
    text = status_text(st, data.live_doc(), ctx.app.clock())
    kb = [[(tr("bot.gw.btn_people_devices"), "gwp")],
          [(tr("bot.gw.btn_exits"), "gwx"), (tr("bot.gw.btn_panel"), "gwl")],
          [(tr("bot.gw.btn_trace"), "gwt:-")],
          [(tr("bot.menu.btn_domain"), "sd"), (tr("bot.gw.btn_refresh"), "gw")], menus.back_row()]
    rows, wait = _mtu_rows(st)
    for row in rows:
        text += "\n\n⚠️ %s" % h(row["text"])
        if row["offer"] == "s4":
            first = (tr("bot.gw.mtu_btn_s4", n=row["new_s4_max"]), "gwms:" + row["kind"])
        else:
            first = (tr("bot.gw.mtu_btn_mtu", n=row["new_client"]), "gwmr:" + row["kind"])
        kb.insert(0, [first, (tr("bot.gw.btn_keep"), "gwmk:" + row["kind"])])
    for row in wait:
        text += "\n\n" + tr("bot.gw.waiting", title=h(row["title"]), devices=h(", ".join(row["waiting"])))
    eng = _engine(st)
    if eng and eng["has_awg"]:
        line = engine_advice(eng)
        if line:
            text += "\n\n" + line
        if eng["offer"]["show"] and not eng["offer"]["blocked"]:
            kb.insert(0, [(tr("bot.gw.btn_to_kernel"), "gwek")])
        kb.insert(-1, [(tr("bot.gw.btn_engine"), "gwe")])
    ctx.reply(text, inline(kb))


# ── safe MTU (§6.1): a narrow card ─────────────────────────────────────────

def _mtu_rows(st):
    """(inbounds that wait for a decision — an offer "s4" or "mtu", inbounds with devices that wait for new settings) —
    from the state, nothing runs. F1 (3.3.7.15): the second list — every protocol, the hard marks only («не
    подключится»; «желательно выдать заново» stays in the panel), by ``reissue.view``: [{"kind", "title", "waiting"}]."""
    try:
        from ..gw import mtu as gwmtu
        v = gwmtu.view(st)
        ask = [r for r in v["inbounds"] if r["kind"] in v["ask"]]
    except (KaskadError, OSError, ValueError, TypeError):
        ask = []
    try:
        from ..gw import model as gmodel, reissue
        order = dict((k, n) for n, k in enumerate(gmodel.INBOUND_KINDS))
        wait = [{"kind": k, "title": r["title"], "waiting": [d["name"] for d in r["hard"]]}
                for k, r in sorted(reissue.view(st).items(), key=lambda kv: (order.get(kv[0], 99), kv[0])) if r["hard"]]
    except (KaskadError, OSError, ValueError, TypeError):
        wait = []
    return ask, wait


def _narrow(st):
    return [r for r in _mtu_rows(st)[0] if r["offer"] == "mtu"]


def _mtu_kind(d, n):
    from ..gw import mtu as gwmtu
    kind = d[n:]
    return kind if kind in gwmtu.KINDS else None


@callback("gwmr:", prefix=True)
def cb_mtu_reissue_ask(ctx, d):
    kind = _mtu_kind(d, 5)
    row = next((r for r in _narrow(_st()) if r["kind"] == kind), None)
    if row is None:
        return ctx.reply(tr("bot.gw.mtu_nothing"), inline([_back_gw()]))
    ctx.reply(tr("bot.gw.mtu_ask", title=h(row["title"]), new=row["new_client"], old=row["client"]),
              inline([[(tr("bot.gw.btn_yes"), "gwmry:" + kind), menus.cancel_btn("gw")]]))


@callback("gwmry:", prefix=True)
def cb_mtu_reissue(ctx, d):
    kind = _mtu_kind(d, 6)
    if kind is None:
        return ctx.reply(tr("bot.gw.unknown_inbound"), inline([_back_gw()]))
    try:
        res = _ops().mtu_reissue(ctx.runner, kind)
    except KaskadError as e:
        snapshot.bump()
        return ctx.reply("❌ %s" % h(util.redact(str(e))), inline([_back_gw()]))
    snapshot.bump()
    ctx.reply(("✅ " if res.get("changed") else "") + h(res.get("text") or "") + _warnings(res),
              inline([[(tr("bot.gw.btn_people_devices"), "gwp")], _back_gw()]))


@callback("gwms:", prefix=True)
def cb_mtu_s4_ask(ctx, d):
    kind = _mtu_kind(d, 5)
    row = next((r for r in _mtu_rows(_st())[0] if r["kind"] == kind and r["offer"] == "s4"), None)
    if row is None:
        return ctx.reply(tr("bot.gw.s4_nothing"), inline([_back_gw()]))
    who = row.get("devices") or []
    lines = [tr("bot.gw.s4_ask_title", title=h(row["title"]), n=row["new_s4_max"]), tr("bot.gw.s4_ask_warn")]
    if who:
        lines += ["", tr("bot.gw.s4_reissue_all")] + ["• %s" % h(n) for n in who]
    else:
        lines.append(tr("bot.gw.s4_no_devices"))
    ctx.reply("\n".join(lines), inline([[(tr("bot.gw.btn_yes_new_params"), "gwmsy:" + kind),
                                         menus.cancel_btn("gw")]]))


@callback("gwmsy:", prefix=True)
def cb_mtu_s4(ctx, d):
    kind = _mtu_kind(d, 6)
    if kind is None:
        return ctx.reply(tr("bot.gw.unknown_inbound"), inline([_back_gw()]))
    try:
        res = _ops().mtu_s4(ctx.runner, kind, yes=True)
    except KaskadError as e:
        snapshot.bump()
        return ctx.reply("❌ %s" % h(util.redact(str(e))), inline([_back_gw()]))
    snapshot.bump()
    ctx.reply(("✅ " if res.get("done") else "") + h(res.get("text") or "") + _warnings(res),
              inline([[(tr("bot.gw.btn_people_devices"), "gwp")], _back_gw()]))


@callback("gwmk:", prefix=True)
def cb_mtu_keep(ctx, d):
    kind = _mtu_kind(d, 5)
    if kind is None:
        return ctx.reply(tr("bot.gw.unknown_inbound"), inline([_back_gw()]))
    try:
        _ops().mtu_keep(ctx.runner, kind)
    except KaskadError as e:
        return ctx.reply("❌ %s" % h(util.redact(str(e))), inline([_back_gw()]))
    snapshot.bump()
    ctx.reply(tr("bot.gw.kept"), inline([_back_gw()]))


# ── the engine of AmneziaWG ────────────────────────────────────────────────

@callback("gwe")
def cb_engine(ctx, d):
    eng = _engine(_st())
    if eng is None:
        return ctx.reply(tr("bot.gw.engine_unread"), inline([_back_gw()]))
    lines = [tr("bot.gw.engine_title"), h(eng["text"])]
    lines += ["⚠️ %s" % h(w) for w in eng["warnings"] + (eng["mix"] if eng["active"] == "kernel" else [])]
    lines.append("")
    lines.append(tr("bot.gw.engine_about"))
    if eng["offer"]["blocked"]:
        lines += ["", tr("bot.gw.engine_blocked_head")] + ["• %s" % h(b) for b in eng["offer"]["blocked"]]
    kb = []
    if eng["active"] == "kernel":
        kb.append([(tr("bot.gw.btn_to_program"), "gwep")])
    elif not eng["offer"]["blocked"]:
        kb.append([(tr("bot.gw.btn_to_kernel"), "gwek")])
    kb.append(_back_gw())
    ctx.reply("\n".join(lines), inline(kb))


def _awg_reconnect():
    from ..gw import awgengine
    return awgengine.RECONNECT


@callback("gwek")
def cb_engine_kernel_ask(ctx, d):
    ctx.reply(tr("bot.gw.kernel_ask", reconnect=_awg_reconnect()),
              inline([[(tr("bot.gw.btn_yes_switch"), "gweky"), menus.cancel_btn("gwe")]]))


@callback("gwep")
def cb_engine_program_ask(ctx, d):
    ctx.reply(tr("bot.gw.program_ask", reconnect=_awg_reconnect()),
              inline([[(tr("bot.gw.btn_yes_switch"), "gwepy"), menus.cancel_btn("gwe")]]))


def _engine_job(ctx, target):
    from ..gw import awgengine
    app = ctx.app

    def run():
        try:
            res = awgengine.switch(app.runner, target, why="person:bot")      # a code of the core (awgengine), not a text
        except KaskadError as e:
            snapshot.bump()
            ctx.send("❌ %s" % h(util.redact(str(e))), inline([[(tr("bot.gw.btn_engine"), "gwe")], _back_gw()]))
            return
        snapshot.bump()
        if res.get("rolled_back"):
            text = tr("bot.gw.engine_rolled_back", reason=h(res.get("reason") or ""))
        elif res.get("unchanged"):
            text = tr("bot.gw.engine_unchanged", engine=awgengine.TITLE[res["engine"]])
        else:
            text = tr("bot.gw.engine_switched", engine=awgengine.TITLE[res["engine"]])
        for w in res.get("warnings") or []:
            text += "\n⚠️ %s" % h(util.redact(w))
        ctx.send(text, inline([[(tr("bot.gw.btn_engine"), "gwe")], _back_gw()]))

    ctx.reply(tr("bot.gw.engine_wait"))
    ctx.job(tr("bot.gw.job_engine"), run)


@callback("gweky")
def cb_engine_kernel(ctx, d):
    _engine_job(ctx, "kernel")


@callback("gwepy")
def cb_engine_program(ctx, d):
    _engine_job(ctx, "program")


@reply_key("bot.menu.btn_gateway")
def rp_gateway(ctx, text):
    cb_gateway(ctx, "gw")


# ── people and devices ─────────────────────────────────────────────────────

def _online(live, did):
    devs = live.get("devices") if isinstance(live.get("devices"), dict) else {}
    rec = devs.get(did)
    return rec if isinstance(rec, dict) else None


@callback("gwp")
@callback("gwp~", prefix=True)
def cb_people(ctx, d):
    model = _model()
    st = _st()
    users = sorted(model.users(st), key=lambda u: str(u.get("name", "")).casefold())
    if not users:
        ctx.reply(tr("bot.gw.people_none"), inline([[(tr("bot.gw.btn_add_device"), "gwa")], _back_gw()]))
        return
    live = data.live_doc()
    pg = menus.paginate(users, menus.split_page(d, "gwp"))
    lines = [tr("bot.gw.people_title")]
    kb = []
    for u in pg.items:
        devs = model.devices(st, u["id"])
        on = sum(1 for x in devs if (_online(live, x["id"]) or {}).get("online"))
        extra = ""
        if u.get("route"):
            extra += tr("bot.gw.user_route", route=h(_route_text(u["route"], st)))
        if not u.get("enabled", True):
            extra += tr("bot.gw.user_off")
        lines.append(tr("bot.gw.people_row", name=h(u["name"]), n=len(devs),
                        online=tr("bot.gw.online_part", n=on) if on else "", extra=extra))
        kb.append([(menus.button("👤 %s (%d)" % (u["name"], len(devs))), "gwu:" + u["id"])])
    kb.append(pg.nav("gwp"))
    kb.append([(tr("bot.gw.btn_add_device"), "gwa")])
    kb.append(_back_gw())
    ctx.reply("\n".join(lines), inline(kb))


def _user_id(s):
    return s if len(s) == 10 and s.startswith("u-") else None


def _dev_id(s):
    return s if len(s) == 10 and s.startswith("d-") else None


@callback("gwu:", prefix=True)
def cb_user(ctx, d):
    rest = d[4:]
    uid, _, page = rest.partition("~")
    model = _model()
    st = _st()
    u = model.find_user(st, _user_id(uid)) if _user_id(uid) else None
    if u is None:
        ctx.reply(tr("bot.gw.user_gone"), inline([[(tr("bot.gw.btn_people"), "gwp")], _back_gw()]))
        return
    live = data.live_doc()
    devs = sorted(model.devices(st, u["id"]), key=lambda x: str(x.get("name", "")).casefold())
    route = _route_text(u.get("route"), st) or tr("bot.gw.route_as_default_is",
                                                  route=_route_text(model.peek(st)["routes"]["default"], st))
    lines = ["👤 <b>%s</b>" % h(u["name"]), tr("bot.gw.route_line", route=h(route))]
    pg = menus.paginate(devs, int(page) if page.isdigit() else 0)
    kb = []
    for x in pg.items:
        on = (_online(live, x["id"]) or {}).get("online")
        acc = ", ".join(menus.inbound_title(k) for k in x.get("access") or []) or tr("bot.gw.no_inbounds")
        lines.append("%s %s — %s · %s%s%s" % (menus.KIND_ICONS.get(x.get("kind"), "🔹"), h(x["name"]),
                                             h(menus.platform_title(x.get("platform"))), h(acc),
                                             tr("bot.gw.dev_online") if on else "",
                                             "" if x.get("enabled", True) else tr("bot.gw.dev_off")))
        kb.append([(menus.button("%s %s" % (menus.KIND_ICONS.get(x.get("kind"), "🔹"), x["name"])), "gwd:" + x["id"])])
    if not devs:
        lines.append(tr("bot.gw.no_devices"))
    kb.append(pg.nav("gwu:" + u["id"]))
    kb.append([(tr("bot.gw.btn_device"), "gwau:" + u["id"]), (tr("bot.gw.btn_route"), "gwr:u:" + u["id"])])
    kb.append([(tr("bot.gw.btn_people"), "gwp")])
    ctx.reply("\n".join(lines), inline(kb))


def device_text(st, live, dev):
    model = _model()
    u = model.find_user(st, dev.get("user")) or {}
    lines = ["%s <b>%s</b> · %s" % (menus.KIND_ICONS.get(dev.get("kind"), "🔹"), h(dev["name"]), h(u.get("name", "?"))),
             tr("bot.gw.dev_platform", platform=h(menus.platform_title(dev.get("platform"))),
                login=h(dev.get("login", "")))]
    acc = []
    ips = dev.get("ips") or {}
    from ..gw import addr
    for k in dev.get("access") or []:
        a = [ips[p] for p in addr.pools_of(k) if ips.get(p)]
        acc.append("%s%s" % (menus.inbound_title(k), (" (%s)" % ", ".join(a)) if a else ""))
    lines.append(tr("bot.gw.dev_inbounds", list=h(", ".join(acc) or tr("bot.gw.none"))))
    r = model.route_of(st, dev)
    src = {"user": "bot.gw.route_as_owner", "default": "bot.gw.route_as_default"}.get(r.get("from"))
    lines.append(tr("bot.gw.route_line", route=h(_route_text(r, st)) + ((" (%s)" % tr(src)) if src else "")))
    if not dev.get("enabled", True):
        lines.append(tr("bot.gw.dev_disabled"))
    on = _online(live, dev["id"])
    if on is None:
        lines.append(tr("bot.gw.now_no_data"))
    elif on.get("online"):
        traffic = ""
        if isinstance(on.get("rx"), int) and isinstance(on.get("tx"), int):
            # глазами устройства: ↓ — сколько оно скачало (tx узла), ↑ — сколько отправило (rx узла, live.py)
            traffic = ", ↓ %s ↑ %s" % (fmt_bytes(on["tx"]), fmt_bytes(on["rx"]))
        via = menus.inbound_title(on.get("inbound")) if on.get("inbound") else ""
        lines.append(tr("bot.gw.now_online", via=(" (%s)" % h(via)) if via else "", traffic=h(traffic)))
    else:
        lines.append(tr("bot.gw.now_offline"))
    return "\n".join(lines)


@callback("gwd:", prefix=True)
def cb_device(ctx, d):
    did = _dev_id(d[4:])
    model = _model()
    st = _st()
    dev = model.find_device(st, did) if did else None
    if dev is None:
        ctx.reply(tr("bot.gw.device_gone"), inline([[(tr("bot.gw.btn_people"), "gwp")], _back_gw()]))
        return
    kb = [[(tr("bot.gw.btn_issue"), "gwi:" + dev["id"])],
          [(tr("bot.gw.btn_route"), "gwr:d:" + dev["id"]), (tr("bot.gw.btn_new_keys"), "gwk:" + dev["id"])],
          [(tr("bot.gw.btn_trace_device"), "gwt:" + dev["id"])],
          [(tr("bot.gw.btn_delete"), "gwdl:" + dev["id"])],
          [("⬅️ %s" % menus.button((model.find_user(st, dev.get("user")) or {}).get("name") or tr("bot.gw.people")),
            "gwu:" + str(dev.get("user")))]]
    ctx.reply(device_text(st, data.live_doc(), dev), inline(kb))


def _device_or_say(ctx, did):
    dev = _model().find_device(_st(), did) if did else None
    if dev is None:
        ctx.reply(tr("bot.gw.device_gone"), inline([[(tr("bot.gw.btn_people"), "gwp")], _back_gw()]))
    return dev


# ── delete, new keys ───────────────────────────────────────────────────────

@callback("gwdl:", prefix=True)
def cb_device_del_ask(ctx, d):
    dev = _device_or_say(ctx, _dev_id(d[5:]))
    if dev is None:
        return
    ctx.reply(tr("bot.gw.del_ask", name=h(dev["name"])),
              inline([[(tr("bot.gw.btn_yes_delete"), "gwdly:" + dev["id"]),
                       menus.cancel_btn("gwd:" + dev["id"])]]))


@callback("gwdly:", prefix=True)
def cb_device_del(ctx, d):
    dev = _device_or_say(ctx, _dev_id(d[6:]))
    if dev is None:
        return
    res = _ops().device_del(ctx.runner, dev["id"])
    snapshot.bump()
    text = tr("bot.gw.deleted", name=h(dev["name"])) + _warnings(res)
    ctx.reply(text, inline([[("👤 %s" % menus.button(_user_name(dev)), "gwu:" + str(dev.get("user")))], _back_gw()]))


def _user_name(dev):
    return (_model().find_user(_st(), dev.get("user")) or {}).get("name") or tr("bot.gw.people")


def _warnings(res):
    ws = list((res or {}).get("warnings") or []) + list(((res or {}).get("apply") or {}).get("warnings") or [])
    return "".join("\n⚠️ %s" % h(util.redact(w)) for w in ws[:5])


@callback("gwk:", prefix=True)
def cb_device_reset_ask(ctx, d):
    dev = _device_or_say(ctx, _dev_id(d[4:]))
    if dev is None:
        return
    ctx.reply(tr("bot.gw.reset_ask", name=h(dev["name"])),
              inline([[(tr("bot.gw.btn_yes"), "gwky:" + dev["id"]), menus.cancel_btn("gwd:" + dev["id"])]]))


@callback("gwky:", prefix=True)
def cb_device_reset(ctx, d):
    dev = _device_or_say(ctx, _dev_id(d[5:]))
    if dev is None:
        return
    res = _ops().device_reset(ctx.runner, dev["id"])
    snapshot.bump()
    lines = [tr("bot.gw.reset_done", name=h(dev["name"]),
                kinds=h(", ".join(menus.inbound_title(k) for k in res.get("kinds") or [])))]
    if res.get("also"):
        lines.append(tr("bot.gw.reset_also", kinds=h(", ".join(menus.inbound_title(k) for k in res["also"]))))
    lines.append(tr("bot.gw.reissue_hint"))
    ctx.reply("\n".join(lines) + _warnings(res),
              inline([[(tr("bot.gw.btn_issue"), "gwi:" + dev["id"])],
                      [(tr("bot.gw.btn_back_device"), "gwd:" + dev["id"])]]))


# ── route ──────────────────────────────────────────────────────────────────

def exit_rows(ctx):
    """[{"id", "name", "kind", "state", "text"}] — ops.exits_view (the pool parsed in-process + the exits
    snapshot); live.json when the pool cannot be read."""
    try:
        rows = _ops().exits_view(ctx.runner, _st())
        from ..gw import exits as gexits
        return [{"id": r["id"], "name": r.get("name") or r["id"], "kind": r.get("kind") or "",
                 "version": r.get("version") or "", "title": gexits.kind_title(r), "enabled": r.get("enabled", True),
                 "state": "alive" if r.get("usable") else ("problem" if r.get("problem") else r.get("state") or "unknown"),
                 "text": gexits.state_text(r)} for r in rows]
    except KaskadError:
        pass
    from ..gw import exits as gexits
    out = []
    ex = data.live_doc().get("exits")
    for eid, e in sorted((ex or {}).items()) if isinstance(ex, dict) else []:
        if not isinstance(e, dict):
            continue
        st = e.get("state") or ("alive" if e.get("alive") else "unknown")
        st = "alive" if st in ("ok", "alive") else st
        # the text of the core (exits.state_text — the reader's language, the codes detail_code/problem_code)
        out.append({"id": eid, "name": e.get("label") or eid, "kind": e.get("kind") or "", "version": "",
                    "title": gexits.kind_title(e), "enabled": True, "state": st,
                    "text": gexits.state_text(dict(e, state=st, usable=bool(e.get("alive"))))})
    return out


def _state_icon(row):
    if not row.get("enabled", True):
        return "⏸"
    return {"alive": "🟢", "dead": "🔴", "problem": "🔴"}.get(row["state"], "⚪")


def _target(ctx, kind, oid):
    model = _model()
    st = _st()
    if kind == "d" and _dev_id(oid):
        obj = model.find_device(st, oid)
    elif kind == "u" and _user_id(oid):
        obj = model.find_user(st, oid)
    else:
        obj = None
    if obj is None:
        ctx.reply(tr("bot.gw.gone_reopen"), inline([[(tr("bot.gw.btn_people"), "gwp")], _back_gw()]))
    return obj


def _back_of(kind, oid):
    return (tr("bot.gw.btn_back"), ("gwd:" if kind == "d" else "gwu:") + oid)


@callback("gwr:", prefix=True)
def cb_route(ctx, d):
    parts = d.split(":")
    if len(parts) != 3:
        return ctx.reply(menus.old_button(), menus.back())
    kind, oid = parts[1], parts[2]
    obj = _target(ctx, kind, oid)
    if obj is None:
        return
    st = _st()
    rows = [[(menus.button(_inherit_label(st, kind, obj)), "gwrs:%s:%s:inherit" % (kind, oid))],
            # stage 1 (§7.10): the pool is the group «main» — the button by its name, as the screen (REV-s1-t10-2 п. 3)
            [(menus.button(tr("bot.gw.btn_route_pool", name=_group_name(st, "main"))), "gwrs:%s:%s:pool" % (kind, oid))]]
    from ..gw import exits as gexits
    exits = [e for e in exit_rows(ctx) if gexits.parsed(e)]
    for e in exits[:EXITS_SHOWN]:
        rows.append([(menus.button(tr("bot.gw.btn_route_exit", icon=_state_icon(e), name=e["name"], state=e["text"])),
                      "gwrs:%s:%s:%s" % (kind, oid, e["id"]))])
    rows.append([(tr("bot.gw.btn_direct"), "gwrs:%s:%s:direct" % (kind, oid)),
                 (tr("bot.gw.btn_block"), "gwrs:%s:%s:block" % (kind, oid))])
    rows.append([_back_of(kind, oid)])
    now = _route_text(obj.get("route"), st) or _inherit_label(st, kind, obj)
    more = ("\n" + tr("bot.gw.exits_shown", shown=EXITS_SHOWN, total=len(exits))) if len(exits) > EXITS_SHOWN else ""
    more += "\n" + tr("bot.gw.inherit_hint_d" if kind == "d" else "bot.gw.inherit_hint_u")
    ctx.reply(tr("bot.gw.route_ask", name=h(obj["name"]), now=h(now), more=more), inline(rows))


def _inherit_label(st, kind, obj):
    """«Как у человека «Мама» — сейчас: пул выходов» (у человека своего нет — «… — сейчас: по умолчанию сервера: …»),
    у человека — «Как по умолчанию — сейчас: …» (владелец, 3.3.2: «Как у человека» без имени и маршрута непонятно)."""
    model = _model()
    if kind == "d":
        u = model.find_user(st, obj.get("user")) or {}
        r = model.route_of(st, {"user": obj.get("user")})          # what the device gets with no route of its own
        return tr("bot.gw.inherit_owner_now" if r.get("from") == "user" else "bot.gw.inherit_owner_def",
                  name=u.get("name") or "?", route=_route_text(r, st))
    return tr("bot.gw.inherit_default_now", route=_route_text(model.route_of(st, {}), st))


def _via(code):
    if code in ("inherit", "pool", "direct", "block"):
        return code
    if code.startswith("e-") and len(code) == 12:
        return "exit:" + code
    return None


@callback("gwrs:", prefix=True)
def cb_route_set(ctx, d):
    parts = d.split(":")
    if len(parts) != 4 or _via(parts[3]) is None:
        return ctx.reply(menus.old_button(), menus.back())
    kind, oid, code = parts[1], parts[2], parts[3]
    obj = _target(ctx, kind, oid)
    if obj is None:
        return
    via = _via(code)
    if via == "pool" or via.startswith("exit:"):
        ctx.reply(tr("bot.gw.fail_ask"),
                  inline([[(_fail_group_button(via), "gwrf:%s:%s:%s:b" % (kind, oid, code))],
                          [(tr("bot.gw.btn_fail_direct"), "gwrf:%s:%s:%s:d" % (kind, oid, code))],
                          [_back_of(kind, oid)]]))
        return
    _set_route(ctx, kind, obj, via, None)


def _fail_group_button(via):
    """«Как у группы «Основная» — блок (по умолчанию)» (T10, D-003): «block» of a route through the exits is the step of
    its group (``exits.fail_of``: the pool — «main», exit:E — the group of E); the words say which and what it does."""
    from ..gw import exits, policy as P
    st = _st()
    try:
        pol = exits.local_policy(st)
        _gid, grp, _missing = exits.route_group(st, {"via": via}, {"policy": pol, "states": {}})
        how = exits.fail_of(st, {"via": via, "fail": "block"}, {"policy": pol})
    except Exception:                               # noqa: BLE001 — the words only: «Основная», «блок» then
        grp, how = None, "block"
    return tr("bot.gw.btn_fail_group", group=P.label(grp or {"id": P.MAIN}),
              how=tr("bot.gw.fail_how_direct" if how == "direct" else "bot.gw.fail_how_block"))


@callback("gwrf:", prefix=True)
def cb_route_fail(ctx, d):
    parts = d.split(":")
    if len(parts) != 5 or _via(parts[3]) is None or parts[4] not in ("b", "d"):
        return ctx.reply(menus.old_button(), menus.back())
    kind, oid = parts[1], parts[2]
    obj = _target(ctx, kind, oid)
    if obj is None:
        return
    _set_route(ctx, kind, obj, _via(parts[3]), "direct" if parts[4] == "d" else "block")


def _set_route(ctx, kind, obj, via, fail):
    ops = _ops()
    if kind == "d":
        res = ops.device_route(ctx.runner, obj["id"], via, fail)
        new = res["device"].get("route")
    else:
        res = ops.user_route(ctx.runner, obj["id"], via, fail)
        new = res["user"].get("route")
    snapshot.bump()
    st = _st()
    text = _route_text(new, st) or _inherit_label(st, kind, dict(obj, route=None))
    ctx.reply(tr("bot.gw.route_set", name=h(obj["name"]), route=h(text)) + _warnings(res),
              inline([[_back_of(kind, obj["id"])], _back_gw()]))


# ── adding a device ────────────────────────────────────────────────────────

@callback("gwa")
@callback("gwa~", prefix=True)
def cb_add(ctx, d):
    model = _model()
    users = sorted(model.users(_st()), key=lambda u: str(u.get("name", "")).casefold())
    if not users:
        ctx.set_state("gw_user", {})
        ctx.reply(tr("bot.gw.add_first_user"), inline([_cancel_row()]))
        return
    pg = menus.paginate(users, menus.split_page(d, "gwa"))
    kb = [[(menus.button("👤 " + u["name"]), "gwau:" + u["id"])] for u in pg.items]
    kb.append(pg.nav("gwa"))
    kb.append([(tr("bot.gw.btn_new_user"), "gwan")])
    kb.append(_cancel_row())
    ctx.reply(tr("bot.gw.add_whose"), inline(kb))


def _bad_name(name, what):
    """The words of a refusal of ``names.check`` (T13: the same rule as the panel, the CLI and the menu), or ""."""
    from .. import names
    try:
        names.check(name, what)
    except KaskadError as e:
        return str(e)
    return ""


@callback("gwan")
def cb_add_user(ctx, d):
    ctx.set_state("gw_user", {})
    ctx.reply(tr("bot.gw.user_name_ask"), inline([_cancel_row()]))


@state("gw_user")
def st_user(ctx, text, d):
    name = text.strip().lstrip("-").strip()
    if not name:
        ctx.send(tr("bot.gw.name_empty"), inline([_cancel_row()]))
        return
    bad = _bad_name(name, "user")
    if bad:                                          # T13: 1–40 знаков, без управляющих — спросить снова
        ctx.send("❌ %s\n%s" % (h(bad), tr("bot.gw.user_name_ask")), inline([_cancel_row()]))
        return
    u = _ops().user_add(ctx.runner, name)
    snapshot.bump()
    ctx.new = True
    _ask_platform(ctx, u["id"], tr("bot.gw.user_added", name=h(u["name"])) + "\n")


def _ask_platform(ctx, uid, head=""):
    ctx.set_state("gw_dev", {"user": uid})
    rows = []
    row = []
    for p, title in menus.platform_titles():
        row.append((title, "gwpl:" + p))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(_cancel_row())
    ctx.reply(head + tr("bot.gw.platform_ask"), inline(rows))


@callback("gwau:", prefix=True)
def cb_add_for(ctx, d):
    uid = _user_id(d[5:])
    if not uid or _model().find_user(_st(), uid) is None:
        ctx.reply(tr("bot.gw.user_gone"), inline([[(tr("bot.gw.btn_people"), "gwp")], _back_gw()]))
        return
    _ask_platform(ctx, uid)


def _dialog(ctx, want):
    """The live dialog of the chat if it is ``want``, else says so and returns None."""
    st, d, expired = ctx.app.dialogs.get(ctx.chat_id)
    if st != want:
        ctx.reply(menus.dialog_expired() if expired else menus.stale_button(),
                  inline([[(tr("bot.gw.btn_add_device"), "gwa")], _back_gw()]))
        return None
    return d


@callback("gwpl:", prefix=True, dialog=True)
def cb_add_platform(ctx, d):
    p = d[5:]
    if p not in menus.PLATFORMS:
        return ctx.reply(menus.old_button(), menus.back())
    dd = _dialog(ctx, "gw_dev")
    if dd is None:
        return
    dd["platform"] = p
    ctx.set_state("gw_dev_name", dd)
    default = menus.kind_name(menus.KIND_OF_PLATFORM.get(p, "other"))
    ctx.reply(tr("bot.gw.dev_name_ask", platform=h(menus.platform_title(p)), default=h(default)),
              inline([_cancel_row()]))


def _candidates(platform, st):
    """(kinds in the order to show, preselected kinds, offer notes {kind: note}, platform notes)."""
    model = _model()
    doc = _ops().offers(platform, st)
    installed = model.installed_kinds(st)
    enabled = model.enabled_kinds(st)
    kinds, notes, first = [], {}, None
    for o in doc.get("offers") or []:
        k = o["inbound"]
        if k in installed and k not in kinds:
            kinds.append(k)
            notes[k] = o.get("note") or ""
            if first is None and k in enabled:
                first = k
    for k in installed:
        if k not in kinds:
            kinds.append(k)
    return kinds, ([first] if first else []), notes, list(doc.get("notes") or [])


def _access_screen(ctx, dd, edit=True):
    st = _st()
    enabled = _model().enabled_kinds(st)
    kinds, _, notes, pnotes = _candidates(dd["platform"], st)
    lines = [tr("bot.gw.access_title", name=h(dd["name"]), platform=h(menus.platform_title(dd["platform"])))]
    kb = []
    for k in kinds:
        mark = "✅" if k in dd["access"] else "⬜"
        off = "" if k in enabled else tr("bot.gw.inbound_off")
        lines.append("%s %s%s%s" % (mark, h(menus.inbound_title(k)), off, (" — " + h(notes[k])) if notes.get(k) else ""))
        kb.append([("%s %s%s" % (mark, menus.inbound_title(k), off), "gwat:" + k)])
    if not dd["access"]:
        lines.append(tr("bot.gw.mark_one"))
    for n in pnotes:
        lines.append("• %s" % h(n))
    kb.append([(tr("bot.gw.btn_done"), "gwag"), menus.cancel_btn("gw")])
    if edit:
        ctx.reply("\n".join(lines), inline(kb))
    else:
        ctx.send("\n".join(lines), inline(kb))


@state("gw_dev_name")
def st_dev_name(ctx, text, d):
    p = d.get("platform") or "other"
    name = text.strip()
    if name == "-" or not name:
        name = menus.kind_name(menus.KIND_OF_PLATFORM.get(p, "other"))
    name = name.lstrip("-").strip() or menus.kind_name("")
    bad = _bad_name(name, "device")
    if bad:                                          # T13: проверка имени до выбора протоколов, не после
        ctx.send("❌ %s" % h(bad), inline([_cancel_row()]))
        return
    st = _st()
    kinds, pre, _, _ = _candidates(p, st)
    if not kinds:
        ctx.clear_state()
        ctx.send(tr("bot.gw.no_protocols"), inline([_back_gw()]))
        return
    d.update(name=name, access=pre)
    ctx.set_state("gw_dev_access", d)
    _access_screen(ctx, d, edit=False)


@callback("gwat:", prefix=True, dialog=True)
def cb_add_toggle(ctx, d):
    k = d[5:]
    dd = _dialog(ctx, "gw_dev_access")
    if dd is None:
        return
    kinds, _, _, _ = _candidates(dd.get("platform") or "other", _st())
    if k not in kinds:
        return _access_screen(ctx, dd)
    acc = list(dd.get("access") or [])
    if k in acc:
        acc.remove(k)
    else:
        acc.append(k)
    dd["access"] = [x for x in kinds if x in acc]
    ctx.set_state("gw_dev_access", dd)
    _access_screen(ctx, dd)


@callback("gwag", dialog=True)
def cb_add_go(ctx, d):
    dd = _dialog(ctx, "gw_dev_access")
    if dd is None:
        return
    if not dd.get("access"):
        return _access_screen(ctx, dd)
    p = dd.get("platform") or "other"
    ctx.clear_state()
    res = _ops().device_add(ctx.runner, dd["user"], dd["name"], kind=menus.KIND_OF_PLATFORM.get(p, "other"),
                            platform=p, access=dd["access"])
    snapshot.bump()
    dev = res["device"]
    ctx.reply(tr("bot.gw.added", name=h(dev["name"]), login=h(dev["login"]),
                 kinds=h(", ".join(menus.inbound_title(k) for k in dev.get("access") or [])))
              + _warnings(res),
              inline([[(tr("bot.gw.btn_issue"), "gwi:" + dev["id"])],
                      [(tr("bot.gw.btn_back_device"), "gwd:" + dev["id"])]]))


# ── issuing ────────────────────────────────────────────────────────────────

class QrMissing(Exception):
    pass


def qrencode_path():
    return shutil.which(QRENCODE, path=os.pathsep.join([os.environ.get("PATH", ""), "/usr/bin", "/usr/local/bin"]))


def qr_png(text):
    """PNG of a QR code: ``qrencode -t PNG -o -`` with the payload on stdin (a secret never sits in argv)."""
    exe = qrencode_path()
    if not exe:
        raise QrMissing()
    try:
        p = subprocess.run([exe, "-t", "PNG", "-s", "6", "-m", "2", "-o", "-"], input=text.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=QR_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise KaskadError(tr("bot.gw.qr_failed", err=type(e).__name__))
    if p.returncode != 0 or not p.stdout.startswith(b"\x89PNG"):
        why = util.redact((p.stderr or b"").decode("utf-8", "replace").strip()[:200]) or \
            tr("bot.gw.qr_exit_code", code=p.returncode)
        raise KaskadError(tr("bot.gw.qr_not_drawn", why=why))
    return p.stdout


def _notes(it):
    return "".join("\n• %s" % h(n) for n in it.get("notes") or [] if n)


def _looks_like_commands(body):
    for line in str(body).splitlines():
        s = line.strip()
        if s.startswith(("sudo ", "nmcli ", "openconnect ", "sstpc ", "ipsec ", "install ", "/", "$ ")):
            return True
    return False


def send_item(ctx, it, kind):
    """One record of an issue (§4 of the gateway spec) to the chat, by its format."""
    api, chat = ctx.api, ctx.chat_id
    fmt = str(it.get("format") or "")
    title = "<b>%s</b>" % h(it.get("title") or "")
    body = it.get("body")
    fname = it.get("filename") or ""
    if fmt == "qr":
        payloads = body if isinstance(body, list) else [body]
        payloads = [str(x) for x in payloads if x]
        for i, payload in enumerate(payloads):
            try:
                png = qr_png(payload)
            except QrMissing:
                api.send_message(chat, title + "\n" + qr_missing())
                return
            except KaskadError as e:
                api.send_message(chat, "%s\n❌ %s" % (title, h(str(e))))
                return
            cap = title + (tr("bot.gw.qr_part", i=i + 1, n=len(payloads)) if len(payloads) > 1 else "")
            api.send_photo(chat, png, cap + (_notes(it) if i == 0 else ""), filename="kaskad-%s-qr%d.png" % (kind, i + 1))
        return
    if fname or fmt in FILE_FORMATS:
        name = fname or "kaskad-%s.%s" % (kind, fmt)
        api.send_document(chat, name, str(body or "").encode("utf-8"), title + _notes(it))
        return
    if fmt == "fields":
        rows = []
        for pair in body if isinstance(body, list) else []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                rows.append("%s: <code>%s</code>" % (h(pair[0]), h(pair[1])))
            else:
                rows.append(h(pair))
        api.send_message(chat, title + "\n" + "\n".join(rows) + _notes(it), preview=False)
        return
    text = str(body if not isinstance(body, list) else "\n".join(str(x) for x in body))
    if fmt in ("vpn", "link"):
        msg = "%s\n<code>%s</code>%s" % (title, h(text), _notes(it))
        if len(msg) > 4000:
            api.send_document(chat, "kaskad-%s-%s.txt" % (kind, fmt), (text + "\n").encode("utf-8"), title + _notes(it))
        else:
            api.send_message(chat, msg, preview=False)
        return
    if _looks_like_commands(text):
        api.send_message(chat, "%s\n<pre>%s</pre>%s" % (title, h(text), _notes(it)), preview=False)
    else:
        api.send_message(chat, "%s\n%s%s" % (title, h(text), _notes(it)), preview=False)


# inbounds the bot never issues (D-063, the coordinator 15.10): their keys only in the panel and on the page /p/
NOT_IN_BOT = ("hy2",)


def _issue(ctx, did, inbound=None):
    dev = _device_or_say(ctx, did)
    if dev is None:
        return
    app = ctx.app

    def run():
        # where="bot": the steps and the guides speak Telegram — a link is a message (a tap copies it), a file is a
        # document (stand T-G1b, N4); «Как подключить» goes with the header, before the records it talks about
        res = _ops().issue(app.runner, dev["id"], inbound=inbound, where="bot")
        if res.get("kind") in NOT_IN_BOT:
            # D-063: Hysteria2 is issued in the panel or on the person's page /p/ — no link, no QR here; the other
            # inbounds of the device as buttons (none — «Назад» alone)
            kb = [[(menus.button(tr("bot.gw.btn_other_inbound", inbound=menus.inbound_title(k))),
                    "gwis:%s:%s" % (dev["id"], k))] for k in res.get("available") or [] if k not in NOT_IN_BOT]
            kb.append([(tr("bot.gw.btn_back_device"), "gwd:" + dev["id"])])
            ctx.send(tr("bot.gw.hy2_elsewhere"), inline(kb))
            return
        steps = [s for s in res.get("howto") or [] if isinstance(s, str) and s]
        howto = ("\n\n" + tr("bot.gw.howto_head") + "\n" +
                 "\n".join("%d. %s" % (i, h(s)) for i, s in enumerate(steps, 1))) if steps else ""
        # таблица поддержки (SPEC-issue-fallbacks.md): строки подсказки — как есть; платформа протокол не умеет (none)
        # — ни записей, ни шагов: только «не поддерживает … Подойдёт:» и другие протоколы
        sup = res.get("support") if isinstance(res.get("support"), dict) else {}
        hints = [x for x in sup.get("hints") or [] if isinstance(x, str) and x]
        hint = ("\n\n" + "\n".join("⚠️ " + h(x) if i == 0 else h(x) for i, x in enumerate(hints))) if hints else ""
        none = sup.get("level") == "none"
        ctx.send(tr("bot.gw.issue_head", name=h(res["name"]), inbound=h(menus.inbound_title(res["kind"])),
                    os=h(menus.platform_title(res["os"])), howto="" if none else howto) + hint)
        for it in [] if none else res["items"]:
            try:
                send_item(ctx, it, res["kind"])
            except ApiError as e:                       # one refused record must not hide the others
                app.note_error(tr("bot.log.issue", why=e))
                app.safe(ctx.send, tr("bot.gw.not_sent", title=h(it.get("title") or ""), why=h(e.description)))
        others = [k for k in res.get("available") or [] if k != res["kind"] and k not in NOT_IN_BOT]
        ready = [a.get("proto") for a in res.get("alternatives") or [] if isinstance(a, dict) and a.get("state") == "ready"]
        others = [k for k in ready if k in others] + [k for k in others if k not in ready]
        kb = [[(menus.button(tr("bot.gw.btn_other_inbound", inbound=menus.inbound_title(k))),
                "gwis:%s:%s" % (dev["id"], k))] for k in others]
        kb.append([(tr("bot.gw.btn_back_device"), "gwd:" + dev["id"])])
        ctx.send(trn("bot.gw.issue_done", 0 if none else len(res["items"])), inline(kb))

    ctx.job(tr("bot.gw.job_issue"), run)


@callback("gwi:", prefix=True)
def cb_issue(ctx, d):
    _issue(ctx, _dev_id(d[4:]))


@callback("gwis:", prefix=True)
def cb_issue_inbound(ctx, d):
    parts = d.split(":")
    if len(parts) != 3 or parts[2] not in menus.INBOUND_TITLES:
        return ctx.reply(menus.old_button(), menus.back())
    _issue(ctx, _dev_id(parts[1]), parts[2])


# ── «Куда пойдёт трафик?» ──────────────────────────────────────────────────

def _trace_back(who):
    if _dev_id(who):
        return [(tr("bot.gw.btn_back_device"), "gwd:" + who)]
    return _back_gw()


@callback("gwt:", prefix=True)
def cb_trace(ctx, d):
    """Who: a device (from its screen) or «-» — a new device with the defaults (from the gateway screen). Then the
    chat's dialog «gw_trace»: every text is a domain or an IP."""
    who = d[4:]
    model = _model()
    st = _st()
    if who != "-" and not _dev_id(who):
        return ctx.reply(menus.old_button(), menus.back())
    dev = model.find_device(st, who) if who != "-" else None
    if who != "-" and dev is None:
        ctx.reply(tr("bot.gw.device_gone"), inline([[(tr("bot.gw.btn_people"), "gwp")], _back_gw()]))
        return
    name = tr("bot.gw.quoted", name=h(dev["name"])) if dev else tr("bot.gw.trace_new_device")
    ctx.set_state("gw_trace", {"who": who})
    ctx.reply(tr("bot.gw.trace_ask", who=name), inline([_trace_back(who)]))


@state("gw_trace")
def st_trace(ctx, text, d):
    from ..gw import trace
    who = str((d or {}).get("who") or "-")
    kb = inline([_trace_back(who)])
    ctx.set_state("gw_trace", {"who": who})            # the next text — the next address
    try:
        res = trace.trace(_st(), who, text)
    except KaskadError as e:
        ctx.send(tr("bot.gw.trace_error", err=h(str(e))), kb)
        return
    ctx.send(trace.text_html(res, h) + "\n\n" + tr("bot.gw.trace_more"), kb)


# ── exits ──────────────────────────────────────────────────────────────────

@callback("gwx")
@callback("gwx~", prefix=True)
def cb_exits(ctx, d):
    rows = exit_rows(ctx)
    if not rows:
        from ..gw import exits as gexits
        st = _st()
        text = tr("bot.gw.exits_empty") + " " + h(gexits.add_hint(st)["text"])         # the panel and the menu, the right number
        if gexits.offline_empty(st):
            text += "\n⚠️ " + h(gexits.NO_EXITS_OFFLINE)
        ctx.reply(text, inline([_back_gw()]))
        return
    items = []
    for e in rows:
        # the kind of the exit key by the name of its protocol («AmneziaWG 3.1», «VLESS», «WireGuard» — exits.kind_title),
        # never the code (awg 3.1, xray/vless — T-M6-332 №2); a name that already says it — not repeated
        kind = e.get("title") or ""
        if kind and kind.lower() in str(e["name"]).lower():
            kind = ""
        items.append("%s %s%s — %s" % (_state_icon(e), h(e["name"]), (" (%s)" % h(kind)) if kind.strip() else "",
                                      h(e["text"])))     # К2-7: an exit off for the gateway — its text says so alone
    pg = menus.paginate(items, menus.split_page(d, "gwx"), size=lambda s: len(s) + 1)
    alive = sum(1 for e in rows if e["state"] == "alive" and e.get("enabled", True))
    ctx.reply(tr("bot.gw.exits_title", alive=alive, total=len(rows)) + "\n" + "\n".join(pg.items),
              inline([pg.nav("gwx"), [(tr("bot.gw.btn_refresh"), "gwx")], _back_gw()]))


# ── the panel's one-time login link ────────────────────────────────────────

def panel_link():
    """The one-time login link of the web panel — the contract with WP-P1:
    ``kaskad.panel.auth.login_link(ttl=3600, by="bot")`` -> {"url": str or None, "token": str, "ttl": int};
    ``url`` None — the panel's service is not installed. Returns {"url", "minutes", "local", "port",
    "fingerprint"}, or None when there is no panel (no module or function, or ``url`` None)."""
    try:
        mod = importlib.import_module("kaskad.panel.auth")
    except ImportError as e:
        if getattr(e, "name", None) in ("kaskad.panel.auth", "kaskad.panel"):
            return None
        raise
    fn = getattr(mod, "login_link", None)
    if not callable(fn):
        return None
    got = fn(ttl=PANEL_TTL, by="bot")
    if not isinstance(got, dict):
        got = {"url": got} if isinstance(got, str) and "://" in got else {}
    url = str(got.get("url") or "")
    if not url:
        return None
    ttl = got.get("ttl")
    ok_ttl = isinstance(ttl, (int, float)) and not isinstance(ttl, bool) and ttl > 0
    port = got.get("port")
    port = port if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 else PANEL_PORT
    local = "://127.0.0.1" in url or "://localhost" in url
    if local:
        tail = url.split("://", 1)[1].split("/", 1)[0]
        port = int(tail.rsplit(":", 1)[1]) if ":" in tail and tail.rsplit(":", 1)[1].isdigit() else port
    return {"url": url, "minutes": max(1, int(ttl) // 60) if ok_ttl else PANEL_TTL // 60, "local": local,
            "port": port, "fingerprint": str(got.get("fingerprint") or "")}


@callback("gwl")
def cb_panel(ctx, d):
    link = panel_link()
    if link is None:
        ctx.reply(tr("bot.gw.panel_none"), inline([_back_gw()]))
        return
    util.log_action(tr("bot.log.panel_link"))
    lines = [tr("bot.gw.panel_title")]
    if link["local"]:
        host = _model().public_host(_st()) or ctx.app.my_ip or tr("bot.gw.server_address")
        lines += [tr("bot.gw.panel_local"),
                  tr("bot.gw.panel_tunnel", port=link["port"], host=h(host)),
                  tr("bot.gw.panel_link_tunnel", min=link["minutes"], url=h(link["url"]))]
    else:
        lines.append(tr("bot.gw.panel_link", min=link["minutes"], url=h(link["url"])))
        from .site import cert_le
        le = cert_le(_st())
        if le:                  # the open mode serves the domain's certificate (SSH-меню 19): the browser trusts it
            lines.append(tr("bot.gw.panel_le", domain=h(le[0]), until=h(le[1])))
        else:
            lines.append(tr("bot.gw.panel_self_signed"))
            if link["fingerprint"]:
                lines.append(tr("bot.gw.panel_fingerprint", fp=h(link["fingerprint"])))
    sent = ctx.send("\n".join(lines), inline([_back_gw()]), preview=False)
    # 3.3.7.5: the link carries the panel's secret path and a sign-in token — the message leaves the chat after
    # SECRET_TTL (5 min, as «Показать адрес»), or sooner when the link itself lives less; the link lives its own time
    from .site import SECRET_TTL
    mid = sent.get("message_id") if isinstance(sent, dict) else None
    if mid is not None:
        ctx.app.expire_later(ctx.chat_id, mid, max(60, min(SECRET_TTL, int(link.get("minutes") or 60) * 60)))
