"""Commands, the main menu (inline or reply keyboard), «Система», «Хостинг», the menu style, the language."""

from .. import __version__, util
from . import config as bcfg
from . import menus, sysinfo
from .lang import LANGS, normalize, set_lang, tr, use_lang
from .registry import callback, command, reply_key
from .telegram import h, inline, remove_keyboard, reply_keyboard

# The names of the languages are always written in their own language (docs/k3/GLOSSARY-en.md).
LANG_NAMES = {"ru": "Русский", "en": "English"}  # i18n: skip — a language is named in itself


def main_text(app):
    return tr("bot.menu.main", version=h(__version__), ip=h(app.my_ip or "N/A"))


def main_menu(ctx):
    ctx.clear_state()
    text = main_text(ctx.app)
    if ctx.app.cfg.menu_style == "reply":
        ctx.send(text, reply_keyboard(menus.reply_rows()))
    else:
        ctx.reply(text, inline(menus.main_inline()))


def _style(ctx, style):
    bcfg.save_value("MENU_STYLE", style)
    ctx.app.refresh_config()
    util.log_action(tr("bot.log.menu_style", style=style))


@command("/start", "/menu")
def cmd_start(ctx, text):
    main_menu(ctx)


@command("/id")
def cmd_id(ctx, text):
    ctx.send(tr("bot.menu.your_id", chat=h(ctx.chat_id)))


@command("/help")
def cmd_help(ctx, text):
    ctx.send(menus.help_text(), menus.back())


@command("/inline")
def cmd_inline(ctx, text):
    _style(ctx, "inline")
    ctx.send(tr("bot.menu.inline_on"), remove_keyboard())
    main_menu(ctx)


@callback("m")
def cb_menu(ctx, data):
    main_menu(ctx)


@callback("sw_reply")
def cb_reply(ctx, data):
    _style(ctx, "reply")
    main_menu(ctx)


@callback("sw_inline")
def cb_inline(ctx, data):
    _style(ctx, "inline")
    ctx.new = True
    main_menu(ctx)


@callback("noop", dialog=True)
def cb_noop(ctx, data):
    return None


@callback("sys")
def cb_sys(ctx, data):
    from .share import sys_markup                    # «📤 Поделиться проверкой» (C11)
    ctx.reply(sysinfo.render(sysinfo.stats()), sys_markup())


@reply_key("bot.menu.btn_system")
def rp_sys(ctx, text):
    cb_sys(ctx, "sys")


@callback("promo")
def cb_promo(ctx, data):
    ctx.reply(menus.promo(), menus.back())


@reply_key("bot.menu.btn_hosting")
def rp_promo(ctx, text):
    cb_promo(ctx, "promo")


# ── the language of the server («🌍 Язык / Language», /lang [ru|en]) ──────────

def lang_screen(ctx):
    now = ctx.app.lang()
    rows = [[(("✅ " if lang == now else "") + LANG_NAMES[lang], "lang:" + lang) for lang in LANGS],
            menus.back_row()]
    ctx.reply(tr("bot.lang.title", name=LANG_NAMES[now]), inline(rows))


def change_lang(ctx, lang):
    """The server's language (the same as ``kaskadctl lang set``) — only from the owner's chat; then the menu of
    commands and the main menu in the new language."""
    if str(ctx.chat_id) != ctx.app.cfg.chat_id:
        ctx.reply(tr("bot.lang.owner_only"), menus.back())
        return
    try:
        lang = set_lang(lang)
    except (ValueError, OSError) as e:
        ctx.reply(tr("bot.lang.failed", why=h(util.redact(str(e)))), menus.back())
        return
    ctx.app.refresh_config()
    util.log_action(tr("bot.log.lang", lang, name=lang))
    with use_lang(lang):
        ctx.app.sync_commands(force=True)
        ctx.send(tr("bot.lang.changed", name=LANG_NAMES[lang]))
        ctx.new = True
        main_menu(ctx)


@command("/lang")
def cmd_lang(ctx, text):
    parts = text.split()
    if len(parts) < 2:
        lang_screen(ctx)
        return
    lang = normalize(parts[1])
    if lang is None:
        ctx.send(tr("bot.lang.bad"), menus.back())
        return
    change_lang(ctx, lang)


@callback("lang")
def cb_lang(ctx, data):
    lang_screen(ctx)


@callback("lang:", prefix=True)
def cb_lang_set(ctx, data):
    lang = data[5:]
    if lang not in LANGS:
        ctx.reply(menus.old_button(), menus.back())
        return
    change_lang(ctx, lang)


@reply_key("bot.menu.btn_lang")
def rp_lang(ctx, text):
    lang_screen(ctx)
