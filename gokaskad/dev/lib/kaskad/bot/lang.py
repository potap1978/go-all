"""The language of the bot (k3/handoff/I18N-3.3.2.md): the language of the SERVER — ``KASKAD_LANG`` of
/etc/kaskad/config (chosen at the first install), the same for the SSH menu, the panel, the wizard and the bot.

The bot handles every update and every alert inside ``use_lang(server_lang(cfg))``: its own texts
(``tr("bot.…")``) and the texts of the core it shows (gw, site, backup…) speak that language. «🌍 Язык / Language»
of the owner's chat changes the server's language (``kaskad.i18n.set_lang`` — the function of ``kaskadctl lang
set``); the menu of commands (setMyCommands) is made again in the new language — and after a change made
elsewhere (the SSH menu, the panel): the app compares the language with the one of its last setMyCommands.
"""

from .. import i18n
from ..i18n import (bind_lang, fmt_bytes, fmt_date, fmt_datetime, fmt_num, get_lang, normalize,  # noqa: F401
                    set_lang, tr, trn, use_lang)

LANGS = i18n.LANGS


def server_lang(cfg=None):
    """"ru" | "en": KASKAD_LANG of the bot's copy of /etc/kaskad/config (``cfg``, re-read by the app when the
    file changes), else what the core reads from the file. Never the language of a context or of the environment:
    «Язык / Language» writes the file."""
    if cfg is not None:
        v = normalize(cfg.values.get("KASKAD_LANG"))
        if v:
            return v
    return i18n.server_lang()


def both(key, **kw):
    """The text of ``key`` in every language: reply-keyboard buttons are matched in either (a keyboard of the
    previous language may still be on the screen after a switch)."""
    return [tr(key, lang, **kw) for lang in LANGS]
