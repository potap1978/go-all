"""Handlers of the bot, registered by the handler modules at import time.

    @callback("lr")                       data == "lr"           (and "lr_new": send instead of edit)
    @callback("dr:", prefix=True)         data starts with "dr:"
    @callback("gwat:", prefix=True, dialog=True)   a step of a dialog: the dialog state is kept
    @command("/start", "/menu")           a command (``/start@bot`` too)
    @reply("🏓 Ping")                     a text of the reply keyboard (matched exactly)
    @reply_key("bot.menu.btn_rules")      a button of the reply keyboard in every language of the catalogs
    @state("fwd_ip")                      the text a dialog waits for: fn(ctx, text, data)

Callback handlers get ``fn(ctx, data)``; command/reply handlers ``fn(ctx, text)``. Any callback that is not
``dialog=True`` and any reply-keyboard text cancels the chat's dialog first («любая кнопка меню отменяет»).
"""

CALLBACKS = []          # (key, prefix, dialog, fn)
COMMANDS = {}
REPLIES = {}
STATES = {}


def callback(key, prefix=False, dialog=False):
    def deco(fn):
        CALLBACKS.append((key, prefix, dialog, fn))
        return fn
    return deco


def command(*names):
    def deco(fn):
        for n in names:
            COMMANDS[n] = fn
        return fn
    return deco


def reply(*texts):
    def deco(fn):
        for t in texts:
            REPLIES[t] = fn
        return fn
    return deco


def reply_key(*keys):
    """``reply`` for catalog keys: the text of each key in ru and in en (a chat may switch the language while its
    reply keyboard of the other one is still on the screen)."""
    from .lang import both

    def deco(fn):
        for k in keys:
            for t in both(k):
                REPLIES[t] = fn
        return fn
    return deco


def state(*names):
    def deco(fn):
        for n in names:
            STATES[n] = fn
        return fn
    return deco


def find_callback(data):
    """(fn, dialog) for callback data, exact keys first, then the longest prefix; (None, False)."""
    for key, prefix, dialog, fn in CALLBACKS:
        if not prefix and data == key:
            return fn, dialog
    best = None
    for key, prefix, dialog, fn in CALLBACKS:
        if prefix and data.startswith(key) and (best is None or len(key) > len(best[0])):
            best = (key, dialog, fn)
    if best is not None:
        return best[2], best[1]
    return None, False


def load():
    """Import the handler modules (they register themselves)."""
    from . import gateway, handlers_fwd, handlers_main, handlers_tools, share, site    # noqa: F401
