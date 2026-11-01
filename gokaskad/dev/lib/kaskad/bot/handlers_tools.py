"""Ping (1×, 10× — one message edited at the end, 60 — edited every 10 pings) and the monitoring screens
(add: server -> interval -> threshold (text) -> cooldown; list; remove). Pings are the chat's job: one at a
time per chat, the menu keeps answering meanwhile."""

from . import aliases, data, menus, monitors
from .lang import tr
from .probe import fmt_ms
from .registry import callback, reply, reply_key, state
from .telegram import h, inline

INTERVALS = ((10, "bot.mon.iv_10s"), (60, "bot.mon.iv_1min"), (300, "bot.mon.iv_5min"))
COOLDOWNS = ((10, "bot.mon.iv_10s"), (60, "bot.mon.iv_60s"), (300, "bot.mon.iv_5min"), (900, "bot.mon.iv_15min"))


def _targets(ctx):
    _, rows = data.forwards(ctx.runner)
    return rows, data.targets(rows)


def _ip_keyboard(ips, prefix, start, base, al):
    pg = menus.paginate(ips, start)
    kb = [[(menus.button(menus.ip_short(ip, al)), prefix + ip)] for ip in pg.items]
    kb.append(pg.nav(base))
    kb.append(menus.back_row())
    return inline(kb)


# ── ping ───────────────────────────────────────────────────────────────────

@callback("pm")
@callback("pm~", prefix=True)
def cb_ping(ctx, d):
    _, ips = _targets(ctx)
    if not ips:
        ctx.reply(tr("bot.ping.no_servers"), menus.back())
        return
    ctx.reply(tr("bot.ping.server"), _ip_keyboard(ips, "ps:", menus.split_page(d, "pm"), "pm", aliases.read_all()))


@reply("🏓 Ping")
def rp_ping(ctx, text):
    cb_ping(ctx, "pm")


def _ip_of(ctx, d, prefix):
    ip = d[len(prefix):]
    if not data.valid_ip(ip):
        ctx.reply(tr("bot.ping.bad_ip"), menus.back())
        return None
    return ip


@callback("ps:", prefix=True)
def cb_ping_pick(ctx, d):
    ip = _ip_of(ctx, d, "ps:")
    if ip is None:
        return
    ctx.reply(tr("bot.ping.mode", server=menus.ip_short_html(ip, aliases.read_all())),
              inline([[(tr("bot.ping.once"), "po:" + ip)], [(tr("bot.ping.ten"), "p10:" + ip)],
                      [(tr("bot.ping.minute"), "p60:" + ip)], menus.back_row()]))


def _ping_setup(ctx, d, prefix):
    ip = _ip_of(ctx, d, prefix)
    if ip is None:
        return None
    rows, _ = _targets(ctx)
    port, proto = data.port_for(rows, ip)
    return ip, port, proto, menus.ip_short_html(ip, aliases.read_all())


@callback("po:", prefix=True)
def cb_ping_once(ctx, d):
    got = _ping_setup(ctx, d, "po:")
    if got is None:
        return
    ip, port, proto, label = got
    app = ctx.app

    def run():
        r = app.prober.smart(ip, 3, port, proto)
        if r:
            ctx.send("🏓 <b>%s</b>\n<code>%s ms</code> [%s]" % (label, fmt_ms(r[1]), h(r[0])), menus.back())
        else:
            ctx.send("🏓 <b>%s</b>\n<code>timeout</code>\n\n<i>%s</i>\n%s"
                     % (label, tr("bot.ping.no_answer"), menus.PING_HINT), menus.back())

    ctx.job("ping %s" % ip, run)


def _finish(ctx, mid, text):
    if mid:
        ctx.edit(mid, text, menus.back())
    else:
        ctx.send(text, menus.back())


@callback("p10:", prefix=True)
def cb_ping_10(ctx, d):
    got = _ping_setup(ctx, d, "p10:")
    if got is None:
        return
    ip, port, proto, label = got
    app = ctx.app

    def run():
        msg = ctx.send("🏓 %s (10x)..." % label)
        mid = msg.get("message_id") if isinstance(msg, dict) else None
        res, lines = [], []
        for n in range(1, 11):
            if app.stop_event.is_set():
                return
            r = app.prober.smart(ip, 3, port, proto)
            if r:
                res.append(r[1])
                lines.append("#%d: %sms [%s]" % (n, fmt_ms(r[1]), h(r[0])))
            else:
                lines.append("#%d: timeout" % n)
            if n < 10:
                app.sleep(1)
        text = "🏓 <b>%s (10x)</b>\n%s" % (label, "\n".join(lines))
        if res:
            text += "\n\n" + tr("bot.ping.avg", ms=fmt_ms(sum(res) / len(res)))
        text += "\n" + tr("bot.ping.lost", lost=10 - len(res), total=10)
        if not res:
            text += "\n\n<i>%s</i>\n<code>sysctl -w net.ipv4.icmp_echo_ignore_all=0</code>" % tr("bot.fwd.enable_ping")
        _finish(ctx, mid, text)

    ctx.job("ping 10× %s" % ip, run)


@callback("p60:", prefix=True)
def cb_ping_60(ctx, d):
    got = _ping_setup(ctx, d, "p60:")
    if got is None:
        return
    ip, port, proto, label = got
    app = ctx.app

    def run():
        msg = ctx.send(tr("bot.ping.minute_start", label=label))
        mid = msg.get("message_id") if isinstance(msg, dict) else None
        res, lost = [], 0
        for n in range(1, 61):
            if app.stop_event.is_set():
                return
            r = app.prober.smart(ip, 3, port, proto)
            if r:
                res.append(r[1])
            else:
                lost += 1
            if n % 10 == 0 and n < 60 and mid:
                p = "%s\n%s | Lost: %d" % (tr("bot.ping.minute_progress", label=label, n=n), tr("bot.ping.ok", ok=len(res)),
                                           lost)
                if res:
                    p += "\n" + tr("bot.ping.avg_short", ms=fmt_ms(sum(res) / len(res)))
                app.safe(app.api.edit_message, ctx.chat_id, mid, p)     # progress: no new message if it fails
            if n < 60:
                app.sleep(1)
        text = tr("bot.ping.minute_done", label=label) + "\n"
        if res:
            text += tr("bot.ping.min_max_avg", min=fmt_ms(min(res)), max=fmt_ms(max(res)),
                       avg=fmt_ms(sum(res) / len(res))) + "\n"
        text += tr("bot.ping.lost", lost=lost, total=60)
        if not res:
            text += "\n\n<i>%s</i>\n<code>sysctl -w net.ipv4.icmp_echo_ignore_all=0</code>" % tr("bot.fwd.enable_ping")
        _finish(ctx, mid, text)

    ctx.job(tr("bot.ping.job_minute", ip=ip), run)


# ── monitoring ─────────────────────────────────────────────────────────────

def _times(interval, threshold, *more):
    """«60с | 100мс[ | …]» of a monitor: interval, threshold and what follows («|» joins them here: in the catalogs
    «|» separates the number forms)."""
    return " | ".join([tr("bot.unit.s", n=interval), tr("bot.unit.ms", n=threshold)] + list(more))


def kbd_monitor():
    return inline([[(tr("bot.mon.btn_add"), "ma")], [(tr("bot.mon.btn_list"), "ml")], [(tr("bot.mon.btn_remove"), "md")],
                   menus.back_row()])


@callback("mm")
def cb_monitor(ctx, d):
    ctx.reply(tr("bot.mon.title"), kbd_monitor())


@reply_key("bot.menu.btn_monitor")
def rp_monitor(ctx, text):
    cb_monitor(ctx, "mm")


@callback("ma")
@callback("ma~", prefix=True)
def cb_monitor_add(ctx, d):
    _, ips = _targets(ctx)
    if not ips:
        ctx.reply(tr("bot.mon.no_servers"), menus.back())
        return
    ctx.reply(tr("bot.mon.server"), _ip_keyboard(ips, "ma:", menus.split_page(d, "ma"), "ma", aliases.read_all()))


@callback("ma:", prefix=True)
def cb_monitor_pick(ctx, d):
    ip = _ip_of(ctx, d, "ma:")
    if ip is None:
        return
    kb = [[(tr(label), "mi:%s:%d" % (ip, sec))] for sec, label in INTERVALS] + [[("⬅️", "m")]]
    ctx.reply(tr("bot.mon.interval", server=menus.ip_short_html(ip, aliases.read_all())), inline(kb))


@callback("mi:", prefix=True)
def cb_monitor_interval(ctx, d):
    ip, _, sec = d[3:].rpartition(":")
    if not data.valid_ip(ip) or not sec.isdigit() or not 1 <= int(sec) <= 86400:
        ctx.reply(tr("bot.mon.bad_data"), menus.back())
        return
    ctx.set_state("mon_threshold", {"ip": ip, "interval": int(sec)})
    ctx.reply(tr("bot.mon.threshold", server=menus.ip_short_html(ip, aliases.read_all()), sec=int(sec)), menus.back())


@state("mon_threshold")
def st_threshold(ctx, text, d):
    threshold = monitors.parse_threshold(text)
    if threshold is None:
        ctx.send("❌ " + monitors.threshold_hint(), menus.back())
        return
    ip, interval = d.get("ip"), int(d.get("interval") or 60)
    ctx.clear_state()
    if not data.valid_ip(ip):
        ctx.send(tr("bot.mon.bad_data"), menus.back())
        return
    kb = [[(tr(label), "mc:%s:%d:%d:%d" % (ip, interval, threshold, sec))] for sec, label in COOLDOWNS] + [[("⬅️", "m")]]
    ctx.send("📊 <b>%s</b>\n%s\n\n%s" % (menus.ip_short_html(ip, aliases.read_all()), _times(interval, threshold),
                                           tr("bot.mon.how_often")), inline(kb))


@callback("mc:", prefix=True)
def cb_monitor_cooldown(ctx, d):
    parts = d[3:].split(":")
    ok = len(parts) == 4 and data.valid_ip(parts[0]) and all(p.isdigit() for p in parts[1:])
    if ok:
        ip, interval, threshold, cooldown = parts[0], int(parts[1]), int(parts[2]), int(parts[3])
        ok = 1 <= interval <= 86400 and 1 <= threshold <= 65535 and 0 <= cooldown <= 7 * 86400
    if not ok:
        ctx.reply(tr("bot.mon.bad_data"), menus.back())
        return
    monitors.write(ip, interval, threshold, cooldown)
    if ctx.app.monitor is not None:
        ctx.app.monitor.forget(ip)
    ctx.reply("✅ <b>%s</b>\n%s" % (menus.ip_short_html(ip, aliases.read_all()),
                                    _times(interval, threshold, tr("bot.mon.alerts_every", n=cooldown))), menus.back())


@callback("ml")
@callback("ml~", prefix=True)
def cb_monitor_list(ctx, d):
    mons = monitors.read_all()
    if not mons:
        ctx.reply(tr("bot.mon.list_empty"), kbd_monitor())
        return
    al = aliases.read_all()
    mon = ctx.app.monitor
    items = []
    for m in mons:
        line = "%s\n  %s" % (menus.ip_tg(m["ip"], al), _times(m["interval"], m["threshold"],
                                                             tr("bot.unit.s", n=m["cooldown"])))
        st = mon.state_of(m["ip"]) if mon is not None else None
        if st == "down":
            line += " · " + tr("bot.mon.down")
        elif st == "up":
            line += " · 🟢"
        items.append(line)
    pg = menus.paginate(items, menus.split_page(d, "ml"), size=lambda s: len(s) + 1)
    kb = [pg.nav("ml"), [(tr("bot.mon.btn_add"), "ma")], [(tr("bot.mon.btn_remove"), "md")], menus.back_row()]
    ctx.reply(tr("bot.mon.list_head") + "\n" + "\n".join(pg.items), inline(kb))


@callback("md")
@callback("md~", prefix=True)
def cb_monitor_del_list(ctx, d):
    ips = [m["ip"] for m in monitors.read_all()]
    if not ips:
        ctx.reply(tr("bot.mon.none"), kbd_monitor())
        return
    ctx.reply(tr("bot.mon.remove_pick"), _ip_keyboard(ips, "md:", menus.split_page(d, "md"), "md", aliases.read_all()))


@callback("md:", prefix=True)
def cb_monitor_del(ctx, d):
    ip = d[3:]
    if not data.valid_ip(ip):
        ctx.reply(tr("bot.ping.bad_ip"), kbd_monitor())
        return
    monitors.remove(ip)
    if ctx.app.monitor is not None:
        ctx.app.monitor.forget(ip)
    ctx.reply(tr("bot.mon.removed", server=menus.ip_short_html(ip, aliases.read_all())), kbd_monitor())
