"""Forwards in the bot: the dialogs AWG (udp) / VLESS (tcp) / MTProto (tcp) / Custom, the list, deleting by
the key ``proto:port`` with a confirmation, «Сбросить всё».

Dialog: IP -> port (Custom: incoming, then outgoing) -> server check (GeoIP + 3 pings) -> name (``-`` skips)
-> note (``-`` skips) -> ``kaskad.forwards.add_forward`` (engine nft; conflicts refuse with the reason —
confirming is for the SSH menu). The name, note, country and provider go to /etc/kaskad/aliases only after
the forward was created (2.x wrote them at once — a refused forward left a name behind).

Engine legacy (iptables, 2.x): adding, deleting and «Сбросить всё» are refused politely (the bot does not
write iptables); the list, ping and monitoring work.
"""

from .. import forwards as kfwd
from .. import snapshot, util
from ..errors import ConflictError, KaskadError, UnsupportedError
from . import aliases, data, menus, monitors
from .lang import fmt_bytes, tr
from .registry import callback, reply, reply_key, state
from .telegram import h, inline

KINDS = {"a_u": ("udp", "AmneziaWG", "🔀 <b>AmneziaWG (UDP)</b>"),
         "a_t": ("tcp", "VLESS", "🔀 <b>VLESS (TCP)</b>"),
         "a_mt": ("tcp", "MTProto", "🔀 <b>MTProto (TCP)</b>")}


def legacy(ctx, new=False):
    text = "⛔ " + data.legacy_text()
    if new:
        ctx.send(text, menus.back())
    else:
        ctx.reply(text, menus.back())


def my_ip(ctx):
    return ctx.app.my_ip or "*"


# ── starting a dialog ──────────────────────────────────────────────────────

@callback("a_u")
@callback("a_t")
@callback("a_mt")
def cb_add(ctx, d):
    proto, name, title = KINDS[d]
    if data.engine() != "nft":
        return legacy(ctx)
    ctx.set_state("fwd_ip", {"proto": proto, "name": name, "custom": False})
    ctx.reply("%s\n\n%s" % (title, tr("bot.fwd.enter_ip")), menus.back())


@reply("🔀 AWG/WG")
def rp_awg(ctx, text):
    cb_add(ctx, "a_u")


@reply("🔀 VLESS")
def rp_vless(ctx, text):
    cb_add(ctx, "a_t")


@reply("🔀 MTProto")
def rp_mtproto(ctx, text):
    cb_add(ctx, "a_mt")


@callback("a_c")
def cb_custom(ctx, d):
    if data.engine() != "nft":
        return legacy(ctx)
    ctx.reply(tr("bot.fwd.custom_proto"), menus.kbd_proto())


@reply("🛠 Custom")
def rp_custom(ctx, text):
    if data.engine() != "nft":
        return legacy(ctx, new=True)
    ctx.send(tr("bot.fwd.custom_rule_proto"), menus.kbd_proto())


@callback("a_cp_tcp")
@callback("a_cp_udp")
def cb_custom_proto(ctx, d):
    proto = d[len("a_cp_"):]
    if data.engine() != "nft":
        return legacy(ctx)
    ctx.set_state("fwd_ip", {"proto": proto, "name": "Custom", "custom": True})
    ctx.reply("🛠 <b>Custom (%s)</b>\n\n%s" % (proto, tr("bot.fwd.enter_ip")), menus.back())


# ── the steps ──────────────────────────────────────────────────────────────

@state("fwd_ip")
def st_ip(ctx, text, d):
    ip = text.strip()
    if not data.valid_ip(ip):
        ctx.send(tr("bot.fwd.bad_ip"), menus.back())
        return
    why = data.target_ok(ip)
    if why:
        ctx.send(tr("bot.fwd.ip_refused", why=h(why)), menus.back())
        return
    d["ip"] = ip
    if d.get("custom"):
        ctx.set_state("fwd_in_port", d)
        ctx.send(tr("bot.fwd.ask_in_port", ip=h(ip)), menus.back())
    else:
        ctx.set_state("fwd_port", d)
        ctx.send(tr("bot.fwd.ask_port", ip=h(ip)), menus.back())


@state("fwd_port")
def st_port(ctx, text, d):
    if not data.valid_port(text):
        ctx.send(tr("bot.fwd.bad_port"), menus.back())
        return
    port = int(text.strip())
    d["in_port"] = d["out_port"] = port
    check_server(ctx, d, "<code>%s:%d</code>" % (h(d["ip"]), port))


@state("fwd_in_port")
def st_in_port(ctx, text, d):
    if not data.valid_port(text):
        ctx.send(tr("bot.fwd.bad_port"), menus.back())
        return
    d["in_port"] = int(text.strip())
    ctx.set_state("fwd_out_port", d)
    ctx.send(tr("bot.fwd.ask_out_port", port=d["in_port"]), menus.back())


@state("fwd_out_port")
def st_out_port(ctx, text, d):
    if not data.valid_port(text):
        ctx.send(tr("bot.fwd.bad_port"), menus.back())
        return
    d["out_port"] = int(text.strip())
    check_server(ctx, d, tr("bot.fwd.custom_header", ip=h(d["ip"]), inp=d["in_port"], out=d["out_port"]))


def probe_text(res):
    """probe_server_tg of 2.x."""
    out = []
    geo = res.get("geo")
    if geo:
        loc = geo["country"] + (", " + geo["city"] if geo.get("city") else "")
        out.append("🌍 <b>GeoIP:</b> %s | %s" % (h(loc), h(geo.get("isp") or "")))
    from .probe import fmt_ms
    for n, p in enumerate(res["pings"], 1):
        out.append("  #%d: %s" % (n, ("%sms [%s]" % (fmt_ms(p[1]), h(p[0]))) if p else "timeout"))
    if res.get("avg") is not None:
        out.append(tr("bot.ping.avg", ms=fmt_ms(res["avg"])) + " | "
                   + tr("bot.ping.lost", lost=res["lost"], total=len(res["pings"])))
    else:
        out.append(tr("bot.fwd.probe_none"))
        out.append(tr("bot.fwd.enable_ping") + "\n" + menus.PING_HINT)
    return "\n".join(out)


def check_server(ctx, d, header):
    ip, port = d["ip"], d["out_port"]
    msg = ctx.send(tr("bot.fwd.checking", ip=h(ip), port=port))
    res = ctx.app.prober.server(ip, port, d["proto"])
    geo = res.get("geo")
    d["geo"] = {"country": geo["country"], "isp": geo["isp"]} if geo else None
    ctx.set_state("fwd_name", d)
    text = "%s\n%s\n%s" % (header, probe_text(res), tr("bot.fwd.ask_name"))
    mid = msg.get("message_id") if isinstance(msg, dict) else None
    if mid:
        ctx.edit(mid, text, menus.back())
    else:
        ctx.send(text, menus.back())


@state("fwd_name")
def st_name(ctx, text, d):
    t = text.strip()
    if t != "-":
        try:
            aliases.check_name(t)                    # T13: 1–40 знаков, без «|», «\\» и управляющих — спросить снова
        except KaskadError as e:
            ctx.send("❌ %s\n%s" % (h(str(e)), tr("bot.fwd.ask_name")), menus.back())
            return
    d["alias"] = "" if t == "-" else aliases.check_name(t)
    ctx.set_state("fwd_note", d)
    ctx.send(tr("bot.fwd.ask_note"), menus.back())


@state("fwd_note")
def st_note(ctx, text, d):
    t = text.strip()
    d["note"] = "" if t == "-" else aliases.sanitize(t)
    ctx.clear_state()
    apply_forward(ctx, d)


def refusal(proto, in_port, ip, out_port, err):
    t = tr("bot.fwd.not_added") + "\n<code>%s :%d → %s:%d</code>\n\n" % (proto, in_port, h(ip), out_port)
    if isinstance(err, ConflictError):
        found = (err.data or {}).get("conflicts") or []
        why = "; ".join(str(c.get("detail") or "") for c in found if isinstance(c, dict)) or str(err)
        t += tr("bot.fwd.port_busy", why=h(why))
    else:
        t += tr("bot.fwd.error", why=h(util.redact(str(err))))
    return t


def apply_forward(ctx, d):
    proto, ip = d["proto"], d["ip"]
    in_port, out_port = int(d["in_port"]), int(d["out_port"])
    if data.engine() != "nft":
        return legacy(ctx, new=True)
    try:
        res = kfwd.add_forward(proto, in_port, ip, out_port, name=d.get("name") or "", runner=ctx.runner)
    except UnsupportedError:
        return legacy(ctx, new=True)
    except KaskadError as e:
        ctx.send(refusal(proto, in_port, ip, out_port, e), menus.back())
        return
    snapshot.bump()
    geo = d.get("geo") or {}
    fields = dict(name=d.get("alias") or None, note=d.get("note") or None,
                  country=geo.get("country") or None, isp=geo.get("isp") or None)
    warn = []
    if any(v is not None for v in fields.values()):
        try:
            if fields["name"] is not None:           # T13: the name through the one rename of a server (journal, checks)
                aliases.rename(ip, fields.pop("name"), allow_duplicate=True, via="bot")
            if any(v is not None for v in fields.values()):
                aliases.update(ip, **fields)
        except (OSError, ValueError, KaskadError) as e:
            warn.append(tr("bot.fwd.name_not_saved", why=e))
    title = "Custom" if d.get("custom") else (d.get("name") or tr("bot.fwd.forward"))
    lines = ["✅ <b>%s</b>" % h(title),
             "<code>%s %s:%d → %s:%d</code>" % (proto, h(my_ip(ctx)), in_port, h(ip), out_port),
             menus.ip_tg(ip, aliases.read_all())]
    old = res.get("replaced")
    if old and (old.get("to"), old.get("to_port")) != (ip, out_port):
        lines.append(tr("bot.fwd.replaced", to=h(old.get("to")), to_port=h(old.get("to_port"))))
    for w in ((res.get("apply") or {}).get("warnings") or []) + warn:
        lines.append("⚠️ %s" % h(util.redact(w)))
    ctx.send("\n".join(lines), menus.back())


# ── list ───────────────────────────────────────────────────────────────────

def _traffic(live, f):
    c = (live.get("forwards") or {}).get(data.forward_key(f)) if isinstance(live.get("forwards"), dict) else None
    b = c.get("bytes") if isinstance(c, dict) else None
    return fmt_bytes(b) if isinstance(b, (int, float)) and not isinstance(b, bool) else None


@callback("lr")
@callback("lr~", prefix=True)
def cb_list(ctx, d):
    eng, rows = data.forwards(ctx.runner)
    if not rows:
        ctx.reply(tr("bot.fwd.list_empty"), menus.back())
        return
    al = aliases.read_all()
    live = data.live_doc()
    me = h(my_ip(ctx))
    items = []
    for f in rows:
        line = "<code>%s:%d (%s) → %s:%d</code>" % (me, f["port"], f["proto"], h(f["to"]), f["to_port"])
        t = _traffic(live, f)
        if t:
            line += " · %s" % t
        items.append(line + "\n  " + menus.ip_tg(f["to"], al))
    pg = menus.paginate(items, menus.split_page(d, "lr"), size=lambda s: len(s) + 1)
    head = tr("bot.fwd.list_head", ip=h(ctx.app.my_ip or "N/A"),
              engine=tr("bot.fwd.engine_iptables") if eng != "nft" else "") + "\n\n"
    ctx.reply(head + "\n".join(pg.items), inline([pg.nav("lr"), menus.back_row()]))


@reply_key("bot.menu.btn_rules")
def rp_list(ctx, text):
    cb_list(ctx, "lr")


# ── delete by key ──────────────────────────────────────────────────────────

def _key_data(f):
    return "%s:%d:%s:%d" % (f["proto"], f["port"], f["to"], f["to_port"])


def _parse_key(rest):
    """``proto:port:to:to_port`` -> dict or None."""
    parts = rest.split(":")
    if len(parts) != 4 or parts[0] not in ("tcp", "udp") or not data.valid_port(parts[1]) \
            or not data.valid_ip(parts[2]) or not data.valid_port(parts[3]):
        return None
    return {"proto": parts[0], "port": int(parts[1]), "to": parts[2], "to_port": int(parts[3])}


def _current(ctx, want):
    """(engine, the forward now at want's key or None)."""
    eng, rows = data.forwards(ctx.runner)
    for f in rows:
        if (f["proto"], f["port"]) == (want["proto"], want["port"]):
            return eng, f
    return eng, None


@callback("dr")
@callback("dr~", prefix=True)
def cb_delete_list(ctx, d):
    eng, rows = data.forwards(ctx.runner)
    if eng != "nft":
        return legacy(ctx)
    if not rows:
        ctx.reply(tr("bot.fwd.no_rules"), menus.back())
        return
    al = aliases.read_all()
    pg = menus.paginate(rows, menus.split_page(d, "dr"))
    kb = []
    for f in pg.items:
        name = (al.get(f["to"]) or {}).get("name")
        label = ("❌ :%d → %s" % (f["port"], name)) if name else ("❌ :%d (%s) → %s:%d" % (f["port"], f["proto"], f["to"], f["to_port"]))
        kb.append([(menus.button(label), "drk:" + _key_data(f))])
    kb.append(pg.nav("dr"))
    kb.append(menus.back_row())
    ctx.reply(tr("bot.fwd.pick_rule"), inline(kb))


@reply_key("bot.menu.btn_delete")
def rp_delete(ctx, text):
    cb_delete_list(ctx, "dr")


@callback("drk:", prefix=True)
def cb_delete_ask(ctx, d):
    want = _parse_key(d[4:])
    if want is None:
        ctx.reply(tr("bot.fwd.bad_button"), menus.back())
        return
    eng, cur = _current(ctx, want)
    if eng != "nft":
        return legacy(ctx)
    if cur is None:
        ctx.reply(tr("bot.fwd.not_found"), menus.back())
        return
    if (cur["to"], cur["to_port"]) != (want["to"], want["to_port"]):
        ctx.reply(tr("bot.fwd.changed"), inline([[(tr("bot.menu.btn_delete"), "dr")], menus.back_row()]))
        return
    ctx.reply(tr("bot.fwd.delete_ask") + "\n<code>%s :%d → %s:%d</code>\n%s"
              % (cur["proto"], cur["port"], h(cur["to"]), cur["to_port"], menus.ip_tg(cur["to"], aliases.read_all())),
              inline([[(tr("bot.fwd.btn_yes_delete"), "dry:" + _key_data(cur)), menus.cancel_btn("dr")]]))


@callback("dry:", prefix=True)
def cb_delete_yes(ctx, d):
    want = _parse_key(d[4:])
    if want is None:
        ctx.reply(tr("bot.fwd.bad_button"), menus.back())
        return
    eng, cur = _current(ctx, want)
    if eng != "nft":
        return legacy(ctx)
    if cur is None:
        ctx.reply(tr("bot.fwd.not_found"), menus.back())
        return
    if (cur["to"], cur["to_port"]) != (want["to"], want["to_port"]):
        ctx.reply(tr("bot.fwd.changed"), inline([[(tr("bot.menu.btn_delete"), "dr")], menus.back_row()]))
        return
    try:
        kfwd.delete_forward(cur["proto"], cur["port"], runner=ctx.runner)
    except KaskadError as e:
        ctx.reply(tr("bot.fwd.delete_failed", why=h(util.redact(str(e)))), menus.back())
        return
    snapshot.bump()
    ufw_forget(ctx.runner, [cur])
    util.log_action("BOT DELETE: %s :%d -> %s:%d" % (cur["proto"], cur["port"], cur["to"], cur["to_port"]))
    _, rows = data.forwards(ctx.runner)
    if not any(f["to"] == cur["to"] for f in rows):
        monitors.remove(cur["to"])                 # nothing goes there any more (2.x: remove_monitor)
        if ctx.app.monitor is not None:
            ctx.app.monitor.forget(cur["to"])
    ctx.reply(tr("bot.fwd.deleted", rule="<code>%s :%d → %s:%d</code>" % (cur["proto"], cur["port"], h(cur["to"]),
                                                                         cur["to_port"])), menus.back())


@callback("dr_", prefix=True)
@callback("dry_", prefix=True)
def cb_delete_by_number(ctx, d):
    """Buttons of the bash bot carried a row NUMBER — never acted on (the list may have changed)."""
    ctx.reply(tr("bot.fwd.old_delete_button"), inline([[(tr("bot.menu.btn_delete"), "dr")], menus.back_row()]))


def ufw_forget(runner, rows):
    """k3_ufw_forget of install.sh: forwards brought over from 2.x had ``ufw allow PORT/proto``; 2.x removed it
    with the rule, so does the bot (only when UFW is active)."""
    if not rows or not runner.which("ufw"):
        return
    res = runner.run(["ufw", "status"], timeout=20)
    if not (res.ok and "Status: active" in res.out):
        return
    for f in rows:
        runner.run(["ufw", "delete", "allow", "%d/%s" % (int(f["port"]), f["proto"])], timeout=20)


# ── «Сбросить всё» ─────────────────────────────────────────────────────────

@callback("fa")
def cb_flush_ask(ctx, d):
    ctx.reply(tr("bot.fwd.flush_ask"), inline([[(tr("bot.fwd.btn_yes"), "fa_y"), (tr("bot.fwd.btn_no"), "m")]]))


@reply_key("bot.menu.btn_flush")
def rp_flush(ctx, text):
    cb_flush_ask(ctx, "fa")


@callback("fa_y")
def cb_flush(ctx, d):
    if data.engine() != "nft":
        return legacy(ctx)
    try:
        res = kfwd.flush_forwards(runner=ctx.runner)
    except KaskadError as e:
        ctx.reply(tr("bot.fwd.flush_failed", why=h(util.redact(str(e)))), menus.back())
        return
    snapshot.bump()
    ufw_forget(ctx.runner, res.get("forwards") or [])
    monitors.remove_all()
    if ctx.app.monitor is not None:
        ctx.app.monitor.forget()
    util.log_action("BOT FLUSH")
    ctx.reply(tr("bot.fwd.flushed"), menus.back())
