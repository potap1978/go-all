"""«📤 Поделиться проверкой» in the bot (C11; SPEC-node-score-schedule-share §5.5 and «Сверка 24.10» п. 5; the design
r1 «Из бота — в окно «Поделиться»»): the button of the screen «💻 Система» (``shr``) and ``/share``.

The answer — a NEW message: «Сервер в целом — N от обычного за 30 дней …» (``rel_total`` of the node, the only
number of the node in it), the link to the window «Поделиться» of the panel (``<the panel>#overview/now?open=share``
— no token: the link opens the window, it does not sign in), the hint for the tunnel, the buttons «Открыть окно
«Поделиться»», «🔑 Вход в панель» (the existing ``gwl``) and «⬅️ Меню». The link carries the secret prefix of the
panel, so the message is protected (``protect_content``: Telegram neither forwards nor saves it), without a preview,
and leaves the chat after ``site.SECRET_TTL`` (as the sign-in link of 3.3.7.5). A panel only through the SSH tunnel —
no link at all, only the way «Обзор › Проверка сервера → Поделиться». No sign-in link is made here
(``login_link`` is called only by the button «🔑 Вход в панель», as before).
"""

import importlib
import os

from .. import paths, util
from ..i18n import fmt_num
from . import menus
from .lang import tr
from .registry import callback, command
from .telegram import ApiError, h, inline

ROUTE = "#overview/now?open=share"             # the window «Поделиться» of the panel (app.js SECTIONS, overview.open)


def panel_present():
    """The panel is installed: its module and its service (the same sign as ``login_link``), no token made."""
    try:
        conf = importlib.import_module("kaskad.panel.conf")
    except ImportError:
        return False
    unit = getattr(conf, "UNIT", None)
    return bool(unit) and os.path.exists(os.path.join(paths.systemd_dir(), unit))


def panel_url():
    """The address of the panel from outside (behind nginx or the open mode, both under the secret prefix), or None —
    the panel only through the SSH tunnel (or its settings unread)."""
    try:
        from ..panel import conf
        cfg = conf.load()
        url = conf.proxy_url(cfg) or conf.public_url(cfg)
    except Exception:                                # noqa: BLE001 — unread settings: no link, the way in words
        return None
    return url if isinstance(url, str) and url.startswith("https://") else None


def _doc():
    from ..bench import hostscore
    return hostscore.node_doc()


def text_and_url():
    """(the text, the link or None, the panel is there) of the answer; no panel — the text of «not installed»."""
    if not panel_present():
        return tr("bot.gw.panel_none"), None, False
    try:
        sm = _doc().get("summary") or {}
    except Exception:                                # noqa: BLE001 — no history read: as no check
        sm = {}
    lines = [tr("bot.share.title")]
    rel = sm.get("rel_total")
    if not sm.get("last_at") or sm.get("stale"):
        lines.append(tr("bot.share.stale"))
        return "\n".join(lines), None, True
    if isinstance(rel, int) and not isinstance(rel, bool):
        lines.append(tr("bot.share.total", n=fmt_num(rel)))
    else:
        lines.append(tr("bot.share.few", n=int(sm.get("rel_n") or 0), need=int(sm.get("rel_need") or 3)))
    url = panel_url()
    if url:
        link = url + ROUTE
        lines += ["", '<a href="%s">%s</a>' % (h(link), h(link)), tr("bot.share.tunnel"), tr("bot.share.note")]
        return "\n".join(lines), link, True
    lines += ["", tr("bot.share.tunnel_only")]
    return "\n".join(lines), None, True


def markup(url):
    rows = [[{"text": tr("bot.gw.btn_panel"), "callback_data": "gwl"}],
            [{"text": t, "callback_data": d} for t, d in menus.back_row()]]
    if url:
        rows.insert(0, [{"text": tr("bot.share.open"), "url": url}])
    return {"inline_keyboard": rows}


def send(ctx):
    text, url, present = text_and_url()
    if not present:
        ctx.send(text, menus.back(), preview=False)
        return
    try:
        sent = ctx.send(text, markup(url), preview=False, protect=True)
    except ApiError:
        if not url:
            raise
        sent = ctx.send(text, markup(None), preview=False, protect=True)   # a link Telegram does not take as a button
    if url:
        from .site import SECRET_TTL
        mid = sent.get("message_id") if isinstance(sent, dict) else None
        if mid is not None:
            ctx.app.expire_later(ctx.chat_id, mid, SECRET_TTL)
        util.log_action(tr("bot.log.share_link"))


@callback("shr")
def cb_share(ctx, d):
    send(ctx)


@command("/share")
def cmd_share(ctx, text):
    send(ctx)


def sys_markup():
    """The keyboard of «💻 Система»: «📤 Поделиться проверкой» and «⬅️ Меню»."""
    return inline([[(tr("bot.share.btn"), "shr")], menus.back_row()])
