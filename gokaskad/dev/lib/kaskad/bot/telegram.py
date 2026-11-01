"""Telegram Bot API over ``kaskad.net.socks`` (HTTPS, verified; BOT_PROXY when set).

The token lives only in this object and in the path of each request — never in argv (2.x passed it to curl),
never in a log line: every message of ApiError/NetworkError goes through ``clean()`` (the token replaced,
``util.redact`` on top). Texts are HTML (``parse_mode=HTML``): anything that came from a person or a file
goes through ``h()``.

    api = Api(token, proxy=socks.parse_proxy(cfg.proxy))
    api.send_message(chat, "<b>Kaskad</b>", inline([[("Menu", "m")]]))

``ApiError`` — Telegram said ``ok: false`` (``code``, ``description``, ``retry_after`` of a 429);
``NetworkError`` — no answer (proxy, TLS, timeout, broken HTTP). Sends are retried once after ``retry_after``
(≤ 60 s); a network failure of a send is not retried (the message may have gone).
"""

import json
import re
import time

from .. import util
from ..net import socks
from .lang import tr

API_BASE = "https://api.telegram.org"
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
CALLBACK_LIMIT = 64             # bytes of callback_data
RETRY_MAX = 60                  # a 429 asking for longer is not waited for inside a send
ALLOWED_UPDATES = ["message", "callback_query"]

_TAG = re.compile(r"<(/?)(b|i|u|s|code|pre|a)(?:\s[^>]*)?>")


def h(value):
    """HTML text for parse_mode=HTML: ``&``, ``<``, ``>`` escaped (quotes too, for attributes)."""
    return (str("" if value is None else value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def fit(text, limit=TEXT_LIMIT):
    """``text`` cut to Telegram's limit on a line boundary, with its open tags closed (a safety net: the
    screens paginate long lists themselves)."""
    if len(text) <= limit:
        return text
    cut = text[:limit - 64]
    nl = cut.rfind("\n")
    if nl > limit // 2:
        cut = cut[:nl]
    amp = cut.rfind("&")
    if amp >= 0 and ";" not in cut[amp:]:
        cut = cut[:amp]
    lt = cut.rfind("<")
    if lt >= 0 and ">" not in cut[lt:]:
        cut = cut[:lt]
    stack = []
    for m in _TAG.finditer(cut):
        if m.group(1):
            if stack and stack[-1] == m.group(2):
                stack.pop()
        else:
            stack.append(m.group(2))
    return cut + "".join("</%s>" % t for t in reversed(stack)) + "\n…"


def inline(rows):
    """[[(text, callback_data), ...], ...] -> InlineKeyboardMarkup."""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows if row]}


def reply_keyboard(rows):
    return {"keyboard": [[{"text": t} for t in row] for row in rows], "resize_keyboard": True,
            "one_time_keyboard": False}


def remove_keyboard():
    return {"remove_keyboard": True}


class ApiError(Exception):
    """Telegram answered ``ok: false``."""

    def __init__(self, method, code, description, retry_after=None):
        self.method = method
        self.code = int(code or 0)
        self.description = description or ""
        self.retry_after = retry_after
        super(ApiError, self).__init__("%s: %s %s" % (method, self.code, self.description))

    def _has(self, *parts):
        d = self.description.lower()
        return any(p in d for p in parts)

    @property
    def too_old(self):
        """answerCallbackQuery for a press Telegram no longer waits for."""
        return self._has("query is too old", "query id is invalid")

    @property
    def not_modified(self):
        return self._has("message is not modified")

    @property
    def cant_edit(self):
        return self._has("message to edit not found", "message can't be edited", "there is no text in the message",
                         "message_id_invalid", "message identifier is not specified")


class NetworkError(Exception):
    """No usable answer from Telegram (proxy, TLS, timeout, not HTTP, not JSON)."""

    def __init__(self, method, text):
        self.method = method
        super(NetworkError, self).__init__("%s: %s" % (method, text))


class Api(object):
    def __init__(self, token, proxy=None, base=None, timeout=20.0, context=None, sleep=None):
        self.token = str(token or "")
        self.proxy = proxy
        self.base = (base or API_BASE).rstrip("/")
        self.timeout = float(timeout)
        self.context = context
        self._sleep = sleep or time.sleep

    @property
    def bot_id(self):
        return self.token.split(":", 1)[0]

    def clean(self, text):
        s = str(text)
        if self.token:
            s = s.replace(self.token, "<token>")
        return util.redact(s)

    # ── the one request ──
    def call(self, method, params=None, files=None, timeout=None):
        """POST /bot<token>/<method>. ``files`` [(field, filename, bytes, content type)] -> multipart (other
        params JSON-encoded unless str). Returns ``result``; raises ApiError / NetworkError."""
        params = dict(params or {})
        url = "%s/bot%s/%s" % (self.base, self.token, method)
        if files:
            fields = [(k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) for k, v in params.items()
                      if v is not None]
            body, ctype = socks.multipart(fields, files)
        else:
            body = json.dumps(dict((k, v) for k, v in params.items() if v is not None), ensure_ascii=False).encode("utf-8")
            ctype = "application/json"
        try:
            resp = socks.request(url, "POST", body, {"Content-Type": ctype}, proxy=self.proxy,
                                 timeout=timeout or self.timeout, context=self.context)
        except OSError as e:
            raise NetworkError(method, self.clean(_net_text(e)))
        try:
            doc = json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise NetworkError(method, tr("bot.net.not_json", status=resp.status))
        if not isinstance(doc, dict):
            raise NetworkError(method, tr("bot.net.not_object", status=resp.status))
        if not doc.get("ok"):
            extra = doc.get("parameters") if isinstance(doc.get("parameters"), dict) else {}
            ra = extra.get("retry_after")
            raise ApiError(method, doc.get("error_code") or resp.status, self.clean(doc.get("description") or ""),
                           int(ra) if isinstance(ra, (int, float)) and not isinstance(ra, bool) else None)
        return doc.get("result")

    def _send(self, method, params=None, files=None, timeout=None):
        """call() that waits once for a 429's retry_after (≤ RETRY_MAX s) and repeats."""
        try:
            return self.call(method, params, files, timeout)
        except ApiError as e:
            if e.retry_after is None or e.retry_after > RETRY_MAX:
                raise
            self._sleep(max(1, e.retry_after))
            return self.call(method, params, files, timeout)

    # ── methods ──
    def get_me(self, timeout=15):
        return self.call("getMe", timeout=timeout)

    def get_updates(self, offset, timeout=30, limit=100):
        """Long polling: the socket waits ``timeout`` + 5 s (35 s for 30)."""
        return self.call("getUpdates", {"offset": int(offset), "timeout": int(timeout), "limit": int(limit),
                                        "allowed_updates": ALLOWED_UPDATES}, timeout=timeout + 5) or []

    def send_message(self, chat_id, text, markup=None, preview=True, protect=False):
        """``protect`` — ``protect_content``: Telegram neither forwards nor saves the message (C11: a link with the
        secret prefix of the panel)."""
        params = {"chat_id": chat_id, "text": fit(text), "parse_mode": "HTML", "reply_markup": markup}
        if not preview:
            params["link_preview_options"] = {"is_disabled": True}
        if protect:
            params["protect_content"] = True
        return self._send("sendMessage", params)

    def edit_message(self, chat_id, message_id, text, markup=None, preview=True):
        """editMessageText; «message is not modified» is success (returns None)."""
        params = {"chat_id": chat_id, "message_id": int(message_id), "text": fit(text), "parse_mode": "HTML",
                  "reply_markup": markup}
        if not preview:
            params["link_preview_options"] = {"is_disabled": True}
        try:
            return self._send("editMessageText", params)
        except ApiError as e:
            if e.not_modified:
                return None
            raise

    def delete_message(self, chat_id, message_id):
        """deleteMessage (the bot's own message, within 48 hours)."""
        return self.call("deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)}, timeout=10)

    def answer_callback(self, callback_id, text="", alert=False):
        return self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190] or None,
                                                 "show_alert": bool(alert) or None}, timeout=10)

    def send_document(self, chat_id, filename, data, caption=None, markup=None):
        params = {"chat_id": str(chat_id), "caption": fit(caption, CAPTION_LIMIT) if caption else None,
                  "parse_mode": "HTML" if caption else None, "reply_markup": markup}
        return self._send("sendDocument", params, [("document", filename, data, "application/octet-stream")],
                          timeout=60)

    def send_photo(self, chat_id, data, caption=None, filename="qr.png", markup=None):
        params = {"chat_id": str(chat_id), "caption": fit(caption, CAPTION_LIMIT) if caption else None,
                  "parse_mode": "HTML" if caption else None, "reply_markup": markup}
        return self._send("sendPhoto", params, [("photo", filename, data, "image/png")], timeout=60)

    def set_my_commands(self, commands, language_code=None):
        """setMyCommands of the default scope; ``language_code`` — the list for the people with that language of
        Telegram (None — for everyone without a list of their own)."""
        return self.call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands],
                                           "language_code": language_code})

    def set_chat_menu_button(self):
        return self.call("setChatMenuButton", {"menu_button": {"type": "commands"}})


def _net_text(e):
    if isinstance(e, socks.NetError):
        return str(e)
    name = type(e).__name__
    if "timeout" in name.lower() or "timed out" in str(e).lower():
        return tr("bot.net.timeout")
    text = getattr(e, "strerror", None) or getattr(e, "reason", None) or str(e) or name
    return "%s: %s" % (name, text)
