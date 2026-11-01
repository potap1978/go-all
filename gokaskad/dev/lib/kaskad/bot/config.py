"""/etc/kaskad/config of 2.x for the bot: parsed, never sourced; written the way ``save_config_val`` does.

    BOT_TOKEN="123:…"      the token (only kaskad.bot.telegram.Api holds it)
    BOT_CHAT_ID="…"        the owner's chat; empty — the bot is not bound yet (SSH menu 8)
    BOT_ADMINS="1,2"       optional: more chats allowed to use the bot (alerts still go to BOT_CHAT_ID)
    BOT_PROXY="socks5h://127.0.0.1:10808"   «Прокси для API Telegram»
    MENU_STYLE="inline"    inline | reply
    BOT_IMPL="python"      which bot the unit runs (install.sh decides; the bot only shows it)

Values: double quotes with the escapes of save_config_val (``\\\\``, ``\\"``, ``\\$``, ``\\```), single quotes as
is, bare words up to a blank or ``#``. A later line wins (as ``source``).
"""

import os
import re

from .. import paths, snapshot, util

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
STYLES = ("inline", "reply")


def _value(raw):
    raw = raw.strip()
    if raw.startswith('"'):
        out = []
        i = 1
        while i < len(raw):
            c = raw[i]
            if c == "\\" and i + 1 < len(raw) and raw[i + 1] in '\\"$`':
                out.append(raw[i + 1])
                i += 2
                continue
            if c == '"':
                break
            out.append(c)
            i += 1
        return "".join(out)
    if raw.startswith("'"):
        end = raw.find("'", 1)
        return raw[1:end] if end > 0 else raw[1:]
    words = raw.split()
    return "" if not words or words[0].startswith("#") else words[0]


def parse(text):
    out = {}
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = _value(m.group(2))
    return out


def _ids(text):
    out = []
    for part in re.split(r"[\s,;]+", str(text or "")):
        part = part.strip()
        if re.match(r"^-?\d{1,20}$", part) and part not in out:
            out.append(part)
    return out


class Config(object):
    """What the bot needs from config; ``signature`` — of the file it came from (to notice changes)."""

    def __init__(self, values=None, signature=None):
        v = values or {}
        self.values = v
        self.signature = signature
        self.token = v.get("BOT_TOKEN", "").strip()
        cid = v.get("BOT_CHAT_ID", "").strip()
        self.chat_id = cid if re.match(r"^-?\d{1,20}$", cid) else ""
        self.admins = [a for a in _ids(v.get("BOT_ADMINS", "")) if a != self.chat_id]
        self.proxy = v.get("BOT_PROXY", "").strip()
        style = v.get("MENU_STYLE", "").strip().lower()
        self.menu_style = style if style in STYLES else "inline"
        self.impl = v.get("BOT_IMPL", "").strip()

    @property
    def bound(self):
        return bool(self.chat_id)

    def allowed(self, chat_id):
        c = str(chat_id)
        return bool(self.chat_id) and (c == self.chat_id or c in self.admins)


def load(path=None):
    path = path or paths.config()
    sig = snapshot.signature(path)
    return Config(parse(util.read_text(path) or ""), sig)


def changed(cfg, path=None):
    return snapshot.signature(path or paths.config()) != cfg.signature


def _escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def save_value(key, value, path=None):
    """``save_config_val`` of install.sh: every line of ``key`` out, ``KEY="value"`` appended; 0600, atomic."""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
        raise ValueError("config key %r" % (key,))
    value = util.EXTRA_BREAKS.sub("", re.sub(r"[\x00-\x1f\x7f]", "", str(value)))     # SEC-3372 П-3: U+2028 & Co too
    path = path or paths.config()
    with util.config_locked():                 # one writer of the file at a time (util.config_locked)
        text = util.read_text(path) or ""
        rx = re.compile(r"^\s*%s=" % re.escape(key))
        lines = [l for l in util.config_lines(text) if not rx.match(l)]
        lines.append('%s="%s"' % (key, _escape(value)))
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            util.ensure_dir(d, 0o700)
        util.atomic_write(path, "\n".join(lines) + "\n", 0o600)
