"""``kaskadctl bot ...`` — the Telegram bot on Python (docs/k3/SPEC-wave3.md §4, §9.1).

    bot run                 the daemon of kaskad-bot.service (until SIGTERM); monitoring runs inside it
    bot status [--json]     configuration (never the token), the daemon's last poll and error, the unit
    bot test [--json]       getMe through BOT_PROXY: is Telegram reachable, is the token accepted, the bot's name
    bot send-test [--json]  a test message to BOT_CHAT_ID
    bot proxy [--json]      how the bot reaches Telegram now (bot/proxy.path, SPEC-tg-proxy Р4); read only — the choice
                            is the panel's («Настройки → Бот и ИИ-агент»)

Exit codes: 0 — fine, 1 — Telegram unreachable / token refused / not configured, 2 — usage.
"""

import os

from .. import errors, paths, sysrun, util
from ..cli import add_json_flag, emit
from ..errors import KaskadError
from ..net import socks
from . import config as bcfg
from . import monitors, store
from .lang import fmt_datetime, tr
from .telegram import Api, ApiError, NetworkError

UNIT = "kaskad-bot.service"


def _api(cfg):
    if not cfg.token:
        raise KaskadError(tr("bot.cli.no_token"))
    try:
        proxy = socks.parse_proxy(cfg.proxy)
    except ValueError as e:
        raise KaskadError(str(e))
    return Api(cfg.token, proxy=proxy, base=os.environ.get("KASKAD_TG_API") or None)


def _telegram_error(api, e):
    if isinstance(e, ApiError) and e.code in (401, 404):
        return KaskadError(tr("bot.cli.token_refused", code=e.code))
    via = tr("bot.cli.via_proxy", proxy=api.proxy) if api.proxy else ""
    return KaskadError(tr("bot.cli.no_link", via=via, why=api.clean(e)))


def cmd_run(args):
    from .app import App, setup_logging
    setup_logging()
    return App(manage_units=True).run()


def cmd_status(args):
    cfg = bcfg.load()
    daemon = store.read_json(paths.P("/run/kaskad/bot.json"), None)
    off = store.read_json(store.path("offset.json"), None)
    unit = ""
    runner = sysrun.default_runner()
    if runner.which("systemctl"):
        unit = runner.run(["systemctl", "is-active", UNIT]).out.strip()
    proxy_error = ""
    try:
        socks.parse_proxy(cfg.proxy)
    except ValueError as e:
        proxy_error = str(e)
    doc = {"ok": True, "token": bool(cfg.token), "bound": cfg.bound, "chat_id": cfg.chat_id, "admins": cfg.admins,
           "proxy": cfg.proxy, "proxy_error": proxy_error, "menu_style": cfg.menu_style, "impl": cfg.impl,
           "unit": unit, "daemon": daemon if isinstance(daemon, dict) else None,
           "offset": (off or {}).get("offset") if isinstance(off, dict) else None,
           "monitors": len(monitors.read_all())}
    d = doc["daemon"] or {}
    doc["path"] = _path(cfg, d)
    lines = [tr("bot.cli.st_bot", state=tr("bot.cli.st_running") if unit == "active"
                else (unit or tr("bot.cli.st_no_unit"))),
             tr("bot.cli.st_token", state=tr("bot.cli.st_token_set") if cfg.token else tr("bot.cli.st_token_unset")),
             tr("bot.cli.st_chat", chat=cfg.chat_id or tr("bot.cli.st_chat_unset"),
                more=tr("bot.cli.st_more_chats", n=len(cfg.admins)) if cfg.admins else ""),
             tr("bot.cli.st_proxy", proxy=cfg.proxy or tr("bot.cli.st_proxy_none"),
                error=(" — %s" % proxy_error) if proxy_error else ""),
             tr("bot_proxy.line", state=doc["path"]["text"]) if doc["path"] else "",
             tr("bot.cli.st_menu", style=cfg.menu_style),
             tr("bot.cli.st_monitors", n=doc["monitors"])]
    if d:
        if d.get("username"):
            lines.append(tr("bot.cli.st_name", name=d["username"]))
        if isinstance(d.get("last_poll"), (int, float)):
            lines.append(tr("bot.cli.st_last_poll", when=fmt_datetime(d["last_poll"], seconds=True)))
        if d.get("last_error"):
            lines.append(tr("bot.cli.st_last_error", why=util.redact(d["last_error"])))
    emit(args, doc, [ln for ln in lines if ln])
    return errors.OK


def _path(cfg, daemon):
    from . import proxy as bproxy
    try:
        return bproxy.path(cfg=cfg, daemon=daemon)
    except Exception:                                   # noqa: BLE001 — a status line, never a failed status
        return None


def cmd_proxy(args):
    from . import proxy as bproxy
    p = bproxy.path(cfg=bcfg.load())
    emit(args, dict({"ok": True}, **p), tr("bot_proxy.line", state=p["text"]))
    return errors.OK


def cmd_test(args):
    cfg = bcfg.load()
    api = _api(cfg)
    try:
        me = api.get_me() or {}
    except (ApiError, NetworkError) as e:
        raise _telegram_error(api, e)
    name = str(me.get("username") or "")
    doc = {"ok": True, "username": name, "id": me.get("id"), "proxy": str(api.proxy) if api.proxy else ""}
    emit(args, doc, tr("bot.cli.test_ok", via=tr("bot.cli.via_proxy", proxy=api.proxy) if api.proxy else "",
                       name=name or "?", id=me.get("id")))
    return errors.OK


def cmd_send_test(args):
    cfg = bcfg.load()
    api = _api(cfg)
    if not cfg.chat_id:
        raise KaskadError(tr("bot.cli.no_chat"))
    try:
        api.send_message(cfg.chat_id, tr("bot.cli.test_message"))
    except (ApiError, NetworkError) as e:
        raise _telegram_error(api, e)
    emit(args, {"ok": True, "chat_id": cfg.chat_id}, tr("bot.cli.sent", chat=cfg.chat_id))
    return errors.OK


def register(subparsers):
    p = subparsers.add_parser("bot", help=tr("bot.cli.h_bot"))
    sub = p.add_subparsers(dest="bot_cmd", metavar=tr("bot.cli.h_command"))
    sub.required = True
    sub.add_parser("run", help=tr("bot.cli.h_run")).set_defaults(func=cmd_run)
    add_json_flag(sub.add_parser("status", help=tr("bot.cli.h_status"))).set_defaults(func=cmd_status)
    add_json_flag(sub.add_parser("test", help=tr("bot.cli.h_test"))).set_defaults(func=cmd_test)
    add_json_flag(sub.add_parser("send-test", help=tr("bot.cli.h_send_test"))).set_defaults(func=cmd_send_test)
    add_json_flag(sub.add_parser("proxy", help=tr("bot_proxy.cli_help"))).set_defaults(func=cmd_proxy)
