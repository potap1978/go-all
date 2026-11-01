#!/usr/bin/env python3
"""fake_tg_api.py — подставной Telegram Bot API: живая проверка бота goAmnezia без настоящего Telegram.

    python3 fake_tg_api.py --token-file /root/ga-live/bot/token --state-dir /root/ga-live/bot/state [--port 18081]

Настоящий bot.py на настоящем сервере ходит сюда вместо api.telegram.org (обёртка run_bot.py подменяет base_url
у ApplicationBuilder). Токен — выдуманный (`bot_live.sh` создаёт его на сервере, 0600): ни к какому боту он не
относится, и настоящий Telegram в проверке не участвует вовсе. Здесь три части:

  /bot<токен>/<метод>     — методы Bot API, которые зовёт бот: getMe, getUpdates (длинный опрос), sendMessage,
                            editMessageText, editMessageReplyMarkup, answerCallbackQuery, sendDocument, sendPhoto,
                            sendMediaGroup, deleteMessage, getFile, setMyCommands, setChatMenuButton… Ответы — в форме
                            Telegram, отказы — тоже его: HTML-разметка (неизвестный тег, незакрытый, «<» без тега),
                            длина текста и подписи, callback_data длиннее 64 байт, Web App не по https,
                            «message is not modified», правка текста у фото/файла, повторный ответ на нажатие. Это то,
                            что заглушки юнит-тестов пропускают, а настоящий Telegram — нет. Чужой токен — 401.
  /file/bot<токен>/<путь> — файлы, которые прислал «человек» (getFile), — для «Привязать старый ключ».
  /_test/…                — сторона человека (только 127.0.0.1): сообщение или файл от пользователя, нажатие кнопки,
                            журнал того, что бот отправил, состояние чата, кнопка меню, вход в мини-апп — initData,
                            подписанный тем же токеном, как его подписывает Telegram, и страница-«клиент», которая
                            открывает Web App бота с #tgWebAppData (`/_test/webapp`).

Секреты: тексты бота бывают с ключами (vpn://), адрес панели — с секретным путём. Сырые тексты живут только в памяти
процесса и отдаются только на петле; журнал state-dir/events.jsonl (0600) — уже очищенный (`safe_text`), файлы —
только имя, размер и sha256. Печатать сырой текст наружу нельзя; tg_user.py печатает `text_safe`.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from email.parser import BytesParser
from email.policy import HTTP as HTTP_POLICY
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

TOKEN_RE = re.compile(r"^[0-9]{6,12}:[A-Za-z0-9_-]{30,50}$")
BOT_USERNAME = "ga_live_fake_bot"
BOT_FIRST_NAME = "goAmnezia live (fake)"
MAX_TEXT = 4096
MAX_CAPTION = 1024
MAX_CALLBACK_ANSWER = 200
CALLBACK_TTL = 15.0           # Telegram: ответ на нажатие позже — «query is too old»
HTML_TAGS = {"a", "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span", "tg-spoiler", "code", "pre",
             "tg-emoji", "blockquote"}
ENTITY_RE = re.compile(r"&(#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|lt|gt|amp|quot);")
NAMED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"'}

# ── Очистка текста для журнала и печати: ни ключей, ни секретных путей ───────────────────────────────────────────────
_VPN_RE = re.compile(r"vpn://[A-Za-z0-9_\-+/=.]+")
_URL_RE = re.compile(r"(https?://)([A-Za-z0-9.\-\[\]:]+)(/[^\s<>\"']*)?")
_KEYLINE_RE = re.compile(r"(?i)(\b(?:PrivateKey|PresharedKey|PublicKey)\s*=\s*)\S+")
_LONG_RE = re.compile(r"[A-Za-z0-9+/_\-=]{40,}")
_DIGITS8_RE = re.compile(r"(?<![0-9])[0-9]{4}\s?[0-9]{4}(?![0-9])")


def safe_text(text: Any) -> str:
    """Текст без секретов: vpn:// и длинные base64 — меткой с длиной, путь в адресе — меткой, строки ключей — скрыты."""
    s = str(text or "")
    s = _VPN_RE.sub(lambda m: "vpn://<%d>" % (len(m.group(0)) - 6), s)
    s = _KEYLINE_RE.sub(lambda m: m.group(1) + "<hidden>", s)

    def _url(m: "re.Match[str]") -> str:
        path = m.group(3) or ""
        return m.group(1) + m.group(2) + ("/<path:%d>" % len(path) if len(path) > 1 else path)

    s = _URL_RE.sub(_url, s)
    s = _LONG_RE.sub(lambda m: "<secret:%d>" % len(m.group(0)), s)
    return s


def text_flags(text: Any) -> Dict[str, bool]:
    """Что есть в сыром тексте — для проверок без печати самого текста."""
    s = str(text or "")
    return {
        "vpn_link": bool(_VPN_RE.search(s)),
        "conf_private_key": bool(re.search(r"(?im)^\s*PrivateKey\s*=\s*\S{20,}", s)),
        "url_with_path": any(len(m.group(3) or "") > 1 for m in _URL_RE.finditer(s)),
        "long_secret": bool(_LONG_RE.search(s)),
    }


# ── HTML: как его разбирает Telegram (tdlib parse_html, упрощённо) ─────────────────────────────────────────────────────
def _byte_off(text: str, i: int) -> int:
    return len(text[:i].encode("utf-8"))


def parse_html(text: str) -> Tuple[Optional[str], str]:
    """(видимый текст, "") или (None, причина отказа словами Telegram). Неизвестный тег, «<» без имени, незакрытый или
    чужой закрывающий тег — отказ, как у Telegram; «&» без сущности остаётся буквой (так делает и tdlib)."""
    out: List[str] = []
    stack: List[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "&":
            m = ENTITY_RE.match(text, i)
            if m:
                ent = m.group(1)
                if ent.startswith("#x") or ent.startswith("#X"):
                    out.append(chr(int(ent[2:], 16)))
                elif ent.startswith("#"):
                    out.append(chr(int(ent[1:])))
                else:
                    out.append(NAMED[ent])
                i = m.end()
                continue
            out.append("&")
            i += 1
            continue
        if c != "<":
            out.append(c)
            i += 1
            continue
        begin = i
        i += 1
        if i < n and text[i] == "/":
            j = text.find(">", i)
            if j == -1:
                return None, "Can't parse entities: unclosed end tag at byte offset %d" % _byte_off(text, begin)
            name = text[i + 1:j].strip().lower()
            if not stack:
                return None, "Can't parse entities: unexpected end tag at byte offset %d" % _byte_off(text, begin)
            if name != stack[-1]:
                return None, ('Can\'t parse entities: unmatched end tag at byte offset %d, expected "</%s>", found "</%s>"'
                              % (_byte_off(text, begin), stack[-1], name))
            stack.pop()
            i = j + 1
            continue
        j = i
        while j < n and not text[j].isspace() and text[j] not in ">/":
            j += 1
        name = text[i:j].lower()
        if not name:
            return None, "Can't parse entities: empty tag name at byte offset %d" % _byte_off(text, begin)
        if name not in HTML_TAGS:
            return None, 'Can\'t parse entities: unsupported start tag "%s" at byte offset %d' % (name, _byte_off(text, begin))
        k = text.find(">", j)
        if k == -1:
            return None, "Can't parse entities: unclosed start tag at byte offset %d" % _byte_off(text, begin)
        stack.append(name)
        i = k + 1
    if stack:
        return None, 'Can\'t parse entities: can\'t find end tag corresponding to start tag "%s"' % stack[-1]
    return "".join(out), ""


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


class ApiError(Exception):
    def __init__(self, code: int, description: str, extra: Optional[Dict[str, Any]] = None):
        super().__init__(description)
        self.code = code
        self.description = description
        self.extra = extra or {}


def bad(description: str) -> ApiError:
    return ApiError(400, "Bad Request: " + description)


def to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "yes")


def to_json(v: Any) -> Any:
    if v is None or isinstance(v, (dict, list)):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except ValueError:
        raise bad("can't parse JSON object")


# ── Состояние «Telegram» ──────────────────────────────────────────────────────────────────────────────────────────────
class World:
    def __init__(self, token: str, state_dir: str):
        self.token = token
        self.bot_id = int(token.split(":", 1)[0])
        self.state_dir = state_dir
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.updates: List[Dict[str, Any]] = []
        self.next_update = 100000
        self.confirmed = 0
        self.chats: Dict[int, Dict[str, Any]] = {}
        self.users: Dict[int, Dict[str, Any]] = {}
        self.callbacks: Dict[str, Dict[str, Any]] = {}
        self.next_cb = 1
        self.files: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []
        self.seq = 0
        self.menu_buttons: Dict[str, Any] = {}
        self.commands: Dict[str, Any] = {}
        self.polls = 0
        self.last_poll = 0.0
        self.unknown_methods: Dict[str, int] = {}
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        os.chmod(state_dir, 0o700)
        self.log_path = os.path.join(state_dir, "events.jsonl")
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)
        os.chmod(self.log_path, 0o600)

    # Пользователь Telegram (подставной)
    def user(self, uid: int, first_name: str = "", username: str = "", lang: str = "ru") -> Dict[str, Any]:
        with self.lock:
            u = self.users.get(uid)
            if u is None:
                u = {"id": uid, "is_bot": False, "first_name": first_name or ("User %d" % uid), "language_code": lang}
                if username:
                    u["username"] = username
                self.users[uid] = u
            else:
                if first_name:
                    u["first_name"] = first_name
                if username:
                    u["username"] = username
            return dict(u)

    def bot_user(self) -> Dict[str, Any]:
        return {"id": self.bot_id, "is_bot": True, "first_name": BOT_FIRST_NAME, "username": BOT_USERNAME}

    def chat(self, chat_id: int, ctype: str = "private", title: str = "") -> Dict[str, Any]:
        with self.lock:
            c = self.chats.get(chat_id)
            if c is None:
                c = {"id": chat_id, "type": ctype, "title": title, "next": 1, "messages": {}}
                self.chats[chat_id] = c
            return c

    def chat_json(self, chat_id: int) -> Dict[str, Any]:
        c = self.chat(chat_id)
        if c["type"] == "private":
            u = self.users.get(chat_id) or {"first_name": "User %d" % chat_id}
            out = {"id": chat_id, "type": "private", "first_name": u.get("first_name")}
            if u.get("username"):
                out["username"] = u["username"]
            return out
        return {"id": chat_id, "type": c["type"], "title": c.get("title") or "group"}

    def new_message(self, chat_id: int, sender: str, **fields: Any) -> Dict[str, Any]:
        with self.lock:
            c = self.chat(chat_id)
            mid = c["next"]
            c["next"] += 1
            m = {"chat_id": chat_id, "message_id": mid, "sender": sender, "date": int(time.time()), "deleted": False,
                 "edits": 0, "kind": "text", "text": "", "parse_mode": None, "plain": "", "reply_markup": None,
                 "file": None, "photo_sizes": None, "preview_disabled": False, "user_id": None}
            m.update(fields)
            c["messages"][mid] = m
            return m

    def message_json(self, m: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"message_id": m["message_id"], "date": m["date"], "chat": self.chat_json(m["chat_id"])}
        if m["sender"] == "bot":
            out["from"] = self.bot_user()
        else:
            out["from"] = dict(self.users.get(m["user_id"]) or {"id": m["user_id"], "is_bot": False, "first_name": "?"})
        if m["edits"]:
            out["edit_date"] = int(time.time())
        if m["kind"] == "text":
            out["text"] = m["plain"] if m["sender"] == "bot" else m["text"]
            if m.get("entities"):
                out["entities"] = m["entities"]
        elif m["kind"] == "photo":
            out["photo"] = m["photo_sizes"] or []
            if m["text"]:
                out["caption"] = m["plain"]
        elif m["kind"] == "document":
            out["document"] = m["file"]["tg"] if m.get("file") else {}
            if m["text"]:
                out["caption"] = m["plain"] if m["sender"] == "bot" else m["text"]
        if m.get("reply_markup"):
            out["reply_markup"] = m["reply_markup"]
        return out

    def push_update(self, upd: Dict[str, Any]) -> int:
        with self.cond:
            self.next_update += 1
            upd["update_id"] = self.next_update
            self.updates.append(upd)
            self.cond.notify_all()
            return self.next_update

    def log(self, kind: str, **data: Any) -> Dict[str, Any]:
        with self.lock:
            self.seq += 1
            ev = {"seq": self.seq, "ts": round(time.time(), 3), "kind": kind}
            ev.update(data)
            self.events.append(ev)
            # В файл — без сырого текста (в памяти он нужен проверкам на петле)
            disk = {k: v for k, v in ev.items() if k not in ("text", "markup_raw")}
            try:
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(disk, ensure_ascii=False) + "\n")
            except OSError:
                pass
            self.cond.notify_all()
            return ev


WORLD: Optional[World] = None


def markup_safe(markup: Optional[Dict[str, Any]]) -> Optional[List[List[str]]]:
    if not markup:
        return None
    rows = []
    for row in markup.get("inline_keyboard") or []:
        out = []
        for b in row:
            label = str(b.get("text") or "")
            if "callback_data" in b:
                out.append("%s → %s" % (label, b.get("callback_data")))
            elif "web_app" in b:
                out.append("%s → web_app(%s)" % (label, safe_text((b.get("web_app") or {}).get("url"))))
            elif "url" in b:
                out.append("%s → url(%s)" % (label, safe_text(b.get("url"))))
            else:
                out.append("%s → ?" % label)
        rows.append(out)
    return rows


def check_markup(markup: Any) -> Optional[Dict[str, Any]]:
    """Inline-клавиатура как её проверяет Telegram: callback_data 1–64 байта, текст кнопки есть, Web App — https."""
    if markup is None:
        return None
    if not isinstance(markup, dict):
        raise bad("can't parse reply keyboard markup JSON object")
    if "inline_keyboard" not in markup:
        if markup.get("remove_keyboard") or "keyboard" in markup or "force_reply" in markup:
            return markup
        raise bad("can't parse reply keyboard markup JSON object")
    rows = markup.get("inline_keyboard")
    if not isinstance(rows, list):
        raise bad("field \"inline_keyboard\" must be an Array")
    for row in rows:
        if not isinstance(row, list):
            raise bad("field \"inline_keyboard\" must be an Array of Array")
        for b in row:
            if not isinstance(b, dict) or not str(b.get("text") or "").strip():
                raise bad("text buttons are unallowed in the inline keyboard")
            if "callback_data" in b:
                data = str(b.get("callback_data") or "")
                if not data or len(data.encode("utf-8")) > 64:
                    raise bad("BUTTON_DATA_INVALID")
            elif "web_app" in b:
                url = str((b.get("web_app") or {}).get("url") or "")
                if not url.startswith("https://"):
                    raise bad("inline keyboard button Web App URL '%s' is invalid: Only HTTPS links are allowed"
                              % safe_text(url))
            elif "url" in b:
                url = str(b.get("url") or "")
                if not re.match(r"^(https?|tg)://", url):
                    raise bad("BUTTON_URL_INVALID")
            else:
                raise bad("can't find field \"callback_data\"")
    return markup


def render_text(text: str, parse_mode: Optional[str], limit: int, what: str) -> Tuple[str, List[str]]:
    """Видимый текст и предупреждения; отказ — ApiError, как у Telegram."""
    warns: List[str] = []
    pm = (parse_mode or "").strip()
    if pm.upper() == "HTML":
        plain, err = parse_html(text)
        if plain is None:
            raise bad(err)
    elif pm:
        plain = text   # MarkdownV2/Markdown бот не использует; не разбираем
        warns.append("parse_mode %s not checked" % pm)
    else:
        plain = text
        if re.search(r"</?(b|i|code|pre|u|s|a)( [^>]*)?>", text):
            warns.append("html_tags_in_plain_text")
    if what == "text" and not plain.strip():
        raise bad("message text is empty")
    if utf16_len(plain.strip()) > limit:
        raise bad("message is too long" if what == "text" else "message caption is too long")
    return plain.strip(), warns


def preview_disabled(p: Dict[str, Any]) -> bool:
    if to_bool(p.get("disable_web_page_preview")):
        return True
    lpo = p.get("link_preview_options")
    if lpo:
        try:
            return bool((to_json(lpo) or {}).get("is_disabled"))
        except ApiError:
            return False
    return False


def get_message(chat_id: int, message_id: int, for_what: str) -> Dict[str, Any]:
    c = WORLD.chats.get(chat_id)
    m = c["messages"].get(message_id) if c else None
    if m is None or m["deleted"]:
        raise bad("message to %s not found" % for_what)
    return m


def file_record(name: str, data: bytes, mime: str) -> Dict[str, Any]:
    fid = "F" + hashlib.sha256(data + str(time.time()).encode()).hexdigest()[:30]
    rec = {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()[:16], "mime": mime,
           "tg": {"file_id": fid, "file_unique_id": fid[:16], "file_name": name, "mime_type": mime, "file_size": len(data)}}
    return rec


def photo_sizes(data: bytes) -> List[Dict[str, Any]]:
    w = h = 0
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
    fid = "P" + hashlib.sha256(data).hexdigest()[:30]
    return [{"file_id": fid, "file_unique_id": fid[:16], "width": w or 512, "height": h or 512, "file_size": len(data)}]


# ── Методы Bot API ───────────────────────────────────────────────────────────────────────────────────────────────────
def m_getMe(p, files):
    return WORLD.bot_user() | {"can_join_groups": False, "can_read_all_group_messages": False,
                               "supports_inline_queries": False, "can_connect_to_business": False,
                               "has_main_web_app": False}


def m_logOut(p, files):
    return True


def m_close(p, files):
    return True


def m_deleteWebhook(p, files):
    if to_bool(p.get("drop_pending_updates")):
        with WORLD.cond:
            WORLD.updates.clear()
    WORLD.log("api", method="deleteWebhook", drop=to_bool(p.get("drop_pending_updates")))
    return True


def m_getWebhookInfo(p, files):
    return {"url": "", "has_custom_certificate": False, "pending_update_count": len(WORLD.updates)}


def m_getUpdates(p, files):
    offset = int(p.get("offset") or 0)
    timeout = max(0.0, min(float(p.get("timeout") or 0), 50.0))
    limit = max(1, min(int(p.get("limit") or 100), 100))
    deadline = time.time() + timeout
    with WORLD.cond:
        WORLD.polls += 1
        WORLD.last_poll = time.time()
        if offset > 0:
            WORLD.confirmed = max(WORLD.confirmed, offset)
            WORLD.updates[:] = [u for u in WORLD.updates if u["update_id"] >= offset]
        while True:
            pending = [u for u in WORLD.updates if u["update_id"] >= offset]
            left = deadline - time.time()
            if pending or left <= 0:
                break
            WORLD.cond.wait(timeout=left)
        WORLD.last_poll = time.time()
        return pending[:limit]


def m_setMyCommands(p, files):
    cmds = to_json(p.get("commands")) or []
    lang = str(p.get("language_code") or "")
    WORLD.commands[lang or "*"] = cmds
    WORLD.log("api", method="setMyCommands", lang=lang, commands=[c.get("command") for c in cmds])
    return True


def m_getMyCommands(p, files):
    return WORLD.commands.get(str(p.get("language_code") or "") or "*", [])


def m_deleteMyCommands(p, files):
    WORLD.commands.pop(str(p.get("language_code") or "") or "*", None)
    return True


def m_setChatMenuButton(p, files):
    chat = str(p.get("chat_id") or "") or "default"
    btn = to_json(p.get("menu_button")) or {"type": "default"}
    if btn.get("type") == "web_app":
        url = str((btn.get("web_app") or {}).get("url") or "")
        if not url.startswith("https://"):
            raise bad("BUTTON_URL_INVALID")
    with WORLD.lock:
        WORLD.menu_buttons[chat] = btn
    WORLD.log("api", method="setChatMenuButton", chat=chat, type=btn.get("type"), text_safe=btn.get("text"),
              url_safe=safe_text((btn.get("web_app") or {}).get("url")) if btn.get("type") == "web_app" else None)
    return True


def m_getChatMenuButton(p, files):
    chat = str(p.get("chat_id") or "") or "default"
    return WORLD.menu_buttons.get(chat) or WORLD.menu_buttons.get("default") or {"type": "default"}


def m_sendChatAction(p, files):
    return True


def m_getChat(p, files):
    cid = int(p.get("chat_id"))
    if cid not in WORLD.chats:
        raise bad("chat not found")
    return WORLD.chat_json(cid)


def _chat_for_send(p) -> int:
    try:
        cid = int(str(p.get("chat_id")))
    except (TypeError, ValueError):
        raise bad("chat not found")
    if cid not in WORLD.chats:
        # Telegram не даёт боту писать первым: человек должен был открыть бота
        raise bad("chat not found")
    return cid


def m_sendMessage(p, files):
    cid = _chat_for_send(p)
    text = str(p.get("text") or "")
    plain, warns = render_text(text, p.get("parse_mode"), MAX_TEXT, "text")
    markup = check_markup(to_json(p.get("reply_markup")))
    m = WORLD.new_message(cid, "bot", kind="text", text=text, parse_mode=p.get("parse_mode"), plain=plain,
                          reply_markup=markup, preview_disabled=preview_disabled(p))
    WORLD.log("send", method="sendMessage", chat=cid, mid=m["message_id"], text=text, text_safe=safe_text(plain),
              flags=text_flags(text), buttons=markup_safe(markup), preview_disabled=m["preview_disabled"], warn=warns)
    return WORLD.message_json(m)


def m_editMessageText(p, files):
    if p.get("inline_message_id"):
        raise bad("inline messages are not used by this bot")
    cid = int(str(p.get("chat_id")))
    mid = int(str(p.get("message_id")))
    with WORLD.lock:
        m = get_message(cid, mid, "edit")
        if m["sender"] != "bot":
            raise bad("message can't be edited")
        if m["kind"] != "text":
            raise bad("there is no text in the message to edit")
        text = str(p.get("text") or "")
        plain, warns = render_text(text, p.get("parse_mode"), MAX_TEXT, "text")
        markup = check_markup(to_json(p.get("reply_markup")))
        if plain == m["plain"] and text == m["text"] and json.dumps(markup, sort_keys=True) == json.dumps(m["reply_markup"], sort_keys=True):
            raise bad("message is not modified: specified new message content and reply markup are exactly the same "
                      "as a current content and reply markup of the message")
        m.update(text=text, plain=plain, parse_mode=p.get("parse_mode"), reply_markup=markup, edits=m["edits"] + 1,
                 preview_disabled=preview_disabled(p))
    WORLD.log("send", method="editMessageText", chat=cid, mid=mid, text=text, text_safe=safe_text(plain),
              flags=text_flags(text), buttons=markup_safe(markup), preview_disabled=m["preview_disabled"], warn=warns)
    return WORLD.message_json(m)


def m_editMessageReplyMarkup(p, files):
    cid = int(str(p.get("chat_id")))
    mid = int(str(p.get("message_id")))
    with WORLD.lock:
        m = get_message(cid, mid, "edit")
        markup = check_markup(to_json(p.get("reply_markup")))
        if json.dumps(markup, sort_keys=True) == json.dumps(m["reply_markup"], sort_keys=True):
            raise bad("message is not modified: specified new message content and reply markup are exactly the same "
                      "as a current content and reply markup of the message")
        m.update(reply_markup=markup, edits=m["edits"] + 1)
    WORLD.log("send", method="editMessageReplyMarkup", chat=cid, mid=mid, buttons=markup_safe(markup))
    return WORLD.message_json(m)


def m_editMessageCaption(p, files):
    cid = int(str(p.get("chat_id")))
    mid = int(str(p.get("message_id")))
    with WORLD.lock:
        m = get_message(cid, mid, "edit")
        if m["kind"] == "text":
            raise bad("there is no caption in the message to edit")
        cap = str(p.get("caption") or "")
        plain, warns = render_text(cap, p.get("parse_mode"), MAX_CAPTION, "caption") if cap else ("", [])
        markup = check_markup(to_json(p.get("reply_markup")))
        m.update(text=cap, plain=plain, reply_markup=markup, edits=m["edits"] + 1)
    WORLD.log("send", method="editMessageCaption", chat=cid, mid=mid, text=cap, text_safe=safe_text(plain),
              flags=text_flags(cap), buttons=markup_safe(markup), warn=warns)
    return WORLD.message_json(m)


def m_deleteMessage(p, files):
    cid = int(str(p.get("chat_id")))
    mid = int(str(p.get("message_id")))
    with WORLD.lock:
        m = get_message(cid, mid, "delete")
        m["deleted"] = True
    WORLD.log("send", method="deleteMessage", chat=cid, mid=mid, by=m["sender"])
    return True


def m_answerCallbackQuery(p, files):
    cbid = str(p.get("callback_query_id") or "")
    text = str(p.get("text") or "")
    with WORLD.lock:
        cb = WORLD.callbacks.get(cbid)
        late = cb is not None and (time.time() - cb["ts"]) > CALLBACK_TTL
        if cb is None or cb["answered"] or late:
            WORLD.log("send", method="answerCallbackQuery", cb=cbid, ok=False, error="query too old or answered twice",
                      second=bool(cb and cb["answered"]), late=bool(late))
            raise bad("query is too old and response timeout expired or query ID is invalid")
        if utf16_len(text) > MAX_CALLBACK_ANSWER:
            raise bad("MESSAGE_TOO_LONG")
        cb.update(answered=True, answer_text=text, show_alert=to_bool(p.get("show_alert")),
                  answered_after=round(time.time() - cb["ts"], 3))
    WORLD.log("send", method="answerCallbackQuery", cb=cbid, chat=cb["chat"], mid=cb["mid"], text=text,
              text_safe=safe_text(text), show_alert=to_bool(p.get("show_alert")), after=cb["answered_after"])
    return True


def _upload(p, files, field: str) -> Tuple[str, bytes, str]:
    """Файл из multipart (attach://… или само поле) — имя, байты, тип."""
    ref = p.get(field)
    f = None
    if isinstance(ref, str) and ref.startswith("attach://"):
        f = files.get(ref[len("attach://"):])
    if f is None:
        f = files.get(field)
    if f is None:
        if isinstance(ref, str) and ref:
            raise bad("wrong remote file identifier specified")
        raise bad("there is no %s in the request" % field)
    return f["filename"] or field, f["data"], f.get("content_type") or "application/octet-stream"


def m_sendDocument(p, files):
    cid = _chat_for_send(p)
    name, data, mime = _upload(p, files, "document")
    cap = str(p.get("caption") or "")
    plain, warns = render_text(cap, p.get("parse_mode"), MAX_CAPTION, "caption") if cap else ("", [])
    markup = check_markup(to_json(p.get("reply_markup")))
    rec = file_record(name, data, mime)
    m = WORLD.new_message(cid, "bot", kind="document", text=cap, plain=plain, parse_mode=p.get("parse_mode"),
                          reply_markup=markup, file=rec)
    WORLD.log("send", method="sendDocument", chat=cid, mid=m["message_id"],
              file={"name": name, "size": len(data), "sha256": rec["sha256"],
                    "looks_like": "wg_conf" if b"[Interface]" in data else ("vpn_link" if data.startswith(b"vpn://") else "other")},
              text=cap, text_safe=safe_text(plain), flags=text_flags(cap), buttons=markup_safe(markup), warn=warns)
    return WORLD.message_json(m)


def m_sendPhoto(p, files):
    cid = _chat_for_send(p)
    name, data, mime = _upload(p, files, "photo")
    cap = str(p.get("caption") or "")
    plain, warns = render_text(cap, p.get("parse_mode"), MAX_CAPTION, "caption") if cap else ("", [])
    markup = check_markup(to_json(p.get("reply_markup")))
    sizes = photo_sizes(data)
    m = WORLD.new_message(cid, "bot", kind="photo", text=cap, plain=plain, parse_mode=p.get("parse_mode"),
                          reply_markup=markup, photo_sizes=sizes)
    WORLD.log("send", method="sendPhoto", chat=cid, mid=m["message_id"],
              photo={"size": len(data), "w": sizes[0]["width"], "h": sizes[0]["height"], "png": data[:8] == b"\x89PNG\r\n\x1a\n"},
              text=cap, text_safe=safe_text(plain), flags=text_flags(cap), buttons=markup_safe(markup), warn=warns)
    return WORLD.message_json(m)


def m_sendMediaGroup(p, files):
    cid = _chat_for_send(p)
    media = to_json(p.get("media")) or []
    if not isinstance(media, list) or not 2 <= len(media) <= 10:
        raise bad("wrong number of messages in media group (must be 2-10)")
    out, photos = [], []
    for item in media:
        if item.get("type") != "photo":
            raise bad("media group of this type is not supported by the fake")
        ref = str(item.get("media") or "")
        f = files.get(ref[len("attach://"):]) if ref.startswith("attach://") else None
        if f is None:
            raise bad("wrong file identifier/HTTP URL specified")
        cap = str(item.get("caption") or "")
        plain, _w = render_text(cap, item.get("parse_mode"), MAX_CAPTION, "caption") if cap else ("", [])
        sizes = photo_sizes(f["data"])
        m = WORLD.new_message(cid, "bot", kind="photo", text=cap, plain=plain, photo_sizes=sizes)
        out.append(WORLD.message_json(m))
        photos.append({"mid": m["message_id"], "size": len(f["data"]), "w": sizes[0]["width"],
                       "caption_safe": safe_text(plain), "caption_flags": text_flags(cap)})
    WORLD.log("send", method="sendMediaGroup", chat=cid, photos=photos)
    return out


def m_getFile(p, files):
    fid = str(p.get("file_id") or "")
    rec = WORLD.files.get(fid)
    if rec is None:
        raise bad("invalid file_id")
    WORLD.log("api", method="getFile", file_id=fid)
    return {"file_id": fid, "file_unique_id": fid[:16], "file_size": len(rec["data"]), "file_path": "documents/%s" % fid}


METHODS = {name[2:]: fn for name, fn in list(globals().items()) if name.startswith("m_") and callable(fn)}
METHODS_LOWER = {k.lower(): v for k, v in METHODS.items()}


# ── Сторона человека (/_test) ───────────────────────────────────────────────────────────────────────────────────────
def t_message(body: Dict[str, Any]) -> Dict[str, Any]:
    uid = int(body["from"])
    u = WORLD.user(uid, body.get("first_name", ""), body.get("username", ""), body.get("lang", "ru"))
    chat_id = int(body.get("chat_id") or uid)
    ctype = body.get("chat_type") or ("private" if chat_id == uid else "group")
    WORLD.chat(chat_id, ctype, body.get("title", ""))
    text = str(body.get("text") or "")
    ents = []
    m0 = re.match(r"^/[A-Za-z0-9_]+(@[A-Za-z0-9_]+)?", text)
    if m0:
        ents.append({"type": "bot_command", "offset": 0, "length": len(m0.group(0))})
    m = WORLD.new_message(chat_id, "user", kind="text", text=text, plain=text, user_id=uid, entities=ents)
    upd = {"message": WORLD.message_json(m)}
    upd_id = WORLD.push_update(upd)
    WORLD.log("user", action="message", chat=chat_id, mid=m["message_id"], user=uid, text_safe=safe_text(text),
              update_id=upd_id)
    return {"message_id": m["message_id"], "update_id": upd_id, "seq": WORLD.seq}


def t_document(body: Dict[str, Any]) -> Dict[str, Any]:
    uid = int(body["from"])
    WORLD.user(uid, body.get("first_name", ""), body.get("username", ""))
    WORLD.chat(uid)
    data = base64.b64decode(body.get("content_b64") or "")
    name = str(body.get("file_name") or "file.bin")
    rec = file_record(name, data, body.get("mime") or "application/octet-stream")
    WORLD.files[rec["tg"]["file_id"]] = {"data": data, "name": name}
    m = WORLD.new_message(uid, "user", kind="document", text=str(body.get("caption") or ""), user_id=uid, file=rec)
    upd_id = WORLD.push_update({"message": WORLD.message_json(m)})
    WORLD.log("user", action="document", chat=uid, mid=m["message_id"], user=uid, file={"name": name, "size": len(data)},
              update_id=upd_id)
    return {"message_id": m["message_id"], "update_id": upd_id, "seq": WORLD.seq}


def t_press(body: Dict[str, Any]) -> Dict[str, Any]:
    uid = int(body["from"])
    WORLD.user(uid)
    chat_id = int(body.get("chat_id") or uid)
    mid = int(body["message_id"])
    data = str(body["data"])
    with WORLD.lock:
        m = get_message(chat_id, mid, "press")
        WORLD.next_cb += 1
        cbid = "%d%06d" % (uid % 100000, WORLD.next_cb)
        WORLD.callbacks[cbid] = {"ts": time.time(), "answered": False, "chat": chat_id, "mid": mid, "data": data,
                                 "from": uid}
        msg_json = WORLD.message_json(m)
    upd = {"callback_query": {"id": cbid, "from": dict(WORLD.users[uid]), "message": msg_json,
                              "chat_instance": str(abs(hash((chat_id, WORLD.bot_id))) % 10 ** 18), "data": data}}
    upd_id = WORLD.push_update(upd)
    WORLD.log("user", action="press", chat=chat_id, mid=mid, user=uid, data=data, cb=cbid, update_id=upd_id)
    return {"callback_id": cbid, "update_id": upd_id, "seq": WORLD.seq}


def t_events(q: Dict[str, str]) -> Dict[str, Any]:
    since = int(q.get("since") or 0)
    wait = min(float(q.get("wait") or 0), 30.0)
    deadline = time.time() + wait
    with WORLD.cond:
        while True:
            evs = [e for e in WORLD.events if e["seq"] > since]
            left = deadline - time.time()
            if evs or left <= 0:
                break
            WORLD.cond.wait(timeout=left)
        return {"seq": WORLD.seq, "events": evs}


def t_chat(q: Dict[str, str]) -> Dict[str, Any]:
    cid = int(q.get("chat") or 0)
    with WORLD.lock:
        c = WORLD.chats.get(cid)
        if not c:
            return {"chat": cid, "messages": []}
        msgs = []
        for m in c["messages"].values():
            msgs.append({"mid": m["message_id"], "sender": m["sender"], "kind": m["kind"], "deleted": m["deleted"],
                         "edits": m["edits"], "text": m["text"], "plain": m["plain"], "text_safe": safe_text(m["plain"]),
                         "flags": text_flags(m["text"]), "reply_markup": m["reply_markup"],
                         "buttons": markup_safe(m["reply_markup"]),
                         "file": ({k: v for k, v in m["file"].items() if k != "tg"} if m.get("file") else None),
                         "photo": (m["photo_sizes"][0] if m.get("photo_sizes") else None)})
        return {"chat": cid, "messages": msgs}


def t_callback(q: Dict[str, str]) -> Dict[str, Any]:
    with WORLD.lock:
        cb = WORLD.callbacks.get(str(q.get("id") or ""))
        if not cb:
            return {"found": False}
        out = dict(cb)
        out["answer_text_safe"] = safe_text(out.pop("answer_text", ""))
        return {"found": True, **out}


def t_state(q: Dict[str, str]) -> Dict[str, Any]:
    with WORLD.lock:
        return {"bot": WORLD.bot_user(), "polls": WORLD.polls, "last_poll_ago": round(time.time() - WORLD.last_poll, 1)
                if WORLD.last_poll else None, "pending_updates": len(WORLD.updates), "confirmed_offset": WORLD.confirmed,
                "menu_buttons": {k: {"type": v.get("type"), "text": v.get("text"),
                                     "url_safe": safe_text((v.get("web_app") or {}).get("url"))}
                                 for k, v in WORLD.menu_buttons.items()},
                "commands": {k: [c.get("command") for c in v] for k, v in WORLD.commands.items()},
                "unknown_methods": dict(WORLD.unknown_methods), "seq": WORLD.seq}


def sign_init_data(token: str, user: Dict[str, Any], auth_date: Optional[int] = None, extra: Optional[Dict[str, str]] = None,
                   bad_signature: bool = False) -> str:
    """initData так, как его подписывает Telegram для Web App: HMAC-SHA256(ключ = HMAC-SHA256("WebAppData", токен))
    по строкам «ключ=значение» в порядке ключей, без hash."""
    fields = {"user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
              "auth_date": str(int(auth_date if auth_date is not None else time.time())),
              "chat_instance": str(abs(hash(("webapp", user.get("id")))) % 10 ** 18), "chat_type": "sender"}
    fields.update(extra or {})
    dcs = "\n".join("%s=%s" % (k, fields[k]) for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    sig = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if bad_signature:
        sig = ("0" if sig[0] != "0" else "1") + sig[1:]
    fields["hash"] = sig
    return urllib.parse.urlencode(fields, quote_via=urllib.parse.quote)


def t_initdata(q: Dict[str, str]) -> str:
    """initData для проверок на петле (curl к /api/auth/miniapp). mode: good | badsig | stale | othertoken."""
    uid = int(q.get("user") or 0)
    u = WORLD.users.get(uid) or {"id": uid, "first_name": "User %d" % uid}
    user = {"id": uid, "first_name": u.get("first_name"), "username": u.get("username", ""), "language_code": "ru",
            "allows_write_to_pm": True}
    mode = q.get("mode") or "good"
    token = WORLD.token
    if mode == "othertoken":
        token = "%d:%s" % (WORLD.bot_id + 1, base64.urlsafe_b64encode(os.urandom(27)).decode().rstrip("="))
    auth = int(time.time()) - (25 * 3600 if mode == "stale" else 0)
    return sign_init_data(token, user, auth, bad_signature=(mode == "badsig"))


def webapp_url_for(uid: int, source: str) -> str:
    """Адрес Web App, который увидел бы человек: кнопка меню его чата (setChatMenuButton) или кнопка «Панель» в
    последнем сообщении бота (web_app)."""
    with WORLD.lock:
        if source == "menu":
            btn = WORLD.menu_buttons.get(str(uid)) or WORLD.menu_buttons.get("default") or {}
            if btn.get("type") == "web_app":
                return str((btn.get("web_app") or {}).get("url") or "")
            return ""
        c = WORLD.chats.get(uid)
        if not c:
            return ""
        for m in sorted(c["messages"].values(), key=lambda x: -x["message_id"]):
            if m["deleted"] or m["sender"] != "bot" or not m["reply_markup"]:
                continue
            for row in m["reply_markup"].get("inline_keyboard") or []:
                for b in row:
                    if "web_app" in b:
                        return str(b["web_app"].get("url") or "")
        return ""


THEME = {"bg_color": "#17212b", "text_color": "#f5f5f5", "hint_color": "#708499", "link_color": "#6ab3f3",
         "button_color": "#5288c1", "button_text_color": "#ffffff", "secondary_bg_color": "#232e3c"}


def panel_url_from_config() -> str:
    """Адрес панели на домене по config.json продукта — с ним сверяется кнопка бота."""
    try:
        cfg = json.load(open("/opt/goamnezia/config.json", encoding="utf-8"))
        path = str((cfg.get("panel") or {}).get("miniapp_path") or "").strip("/")
        return "https://%s/%s/" % (cfg.get("domain"), path) if path and cfg.get("domain") else ""
    except (OSError, ValueError):
        return ""


def t_webapp(q: Dict[str, str]) -> Tuple[int, str]:
    """Страница-«клиент Telegram»: открывает Web App бота так, как открывает его Telegram — адрес кнопки и
    #tgWebAppData=<initData>. Ни адрес, ни подпись в ответе наружу не уходят: страница сразу уходит по адресу.
    proxy=http://127.0.0.1:<порт> — тот же адрес через miniapp_proxy.py (секретный путь не попадает в адресную строку
    браузера проверяющего): https://<домен>/<путь>/<хвост> → <proxy>/<хвост>; кнопка при этом обязана вести ровно в
    панель из config.json."""
    uid = int(q.get("user") or 0)
    source = q.get("source") or "menu"
    url = webapp_url_for(uid, source)
    if not url:
        return 404, "<!doctype html><meta charset=utf-8><p>no web_app button for this user (%s)</p>" % html.escape(source)
    u = WORLD.users.get(uid) or {"id": uid, "first_name": "User %d" % uid}
    user = {"id": uid, "first_name": u.get("first_name"), "username": u.get("username", ""), "language_code": "ru",
            "allows_write_to_pm": True}
    init = sign_init_data(WORLD.token, user)
    frag = "tgWebAppData=%s&tgWebAppVersion=8.0&tgWebAppPlatform=android&tgWebAppThemeParams=%s" % (
        urllib.parse.quote(init, safe=""), urllib.parse.quote(json.dumps(THEME, separators=(",", ":")), safe=""))
    panel = panel_url_from_config()
    proxy = str(q.get("proxy") or "")
    WORLD.log("user", action="webapp_open", user=uid, source=source, url_safe=safe_text(url),
              button_is_panel=bool(panel) and url.startswith(panel), via_proxy=bool(proxy))
    if proxy:
        if not re.fullmatch(r"http://(127\.0\.0\.1|localhost):[0-9]{2,5}", proxy) or not panel or not url.startswith(panel):
            return 400, "<!doctype html><meta charset=utf-8><p>web_app button is not the panel from config.json</p>"
        url = proxy + "/" + url[len(panel):]
    target = json.dumps(url + "#" + frag)
    return 200, ("<!doctype html><meta charset=utf-8><title>Telegram Web App</title>"
                 "<script>location.replace(%s);</script>" % target)


# ── HTTP ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "fake-telegram/1.0"

    def log_message(self, fmt, *args):  # noqa: D401 — тишина: журнал свой
        return

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _params(self) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]], bytes]:
        parsed = urllib.parse.urlsplit(self.path)
        params: Dict[str, Any] = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
        files: Dict[str, Dict[str, Any]] = {}
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else b""
        ctype = self.headers.get("Content-Type") or ""
        if body:
            if ctype.startswith("application/json"):
                params.update(json.loads(body.decode("utf-8")))
            elif ctype.startswith("application/x-www-form-urlencoded"):
                params.update({k: v[-1] for k, v in urllib.parse.parse_qs(body.decode("utf-8"), keep_blank_values=True).items()})
            elif ctype.startswith("multipart/form-data"):
                msg = BytesParser(policy=HTTP_POLICY).parsebytes(
                    b"Content-Type: " + ctype.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
                for part in msg.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    fname = part.get_filename()
                    payload = part.get_payload(decode=True) or b""
                    if fname is not None:
                        files[name] = {"filename": fname, "data": payload, "content_type": part.get_content_type()}
                    else:
                        params[name] = payload.decode(part.get_content_charset() or "utf-8", "replace")
        return params, files, body

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def _route(self):
        peer = self.client_address[0] if self.client_address else ""
        if peer not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            self._json(403, {"ok": False, "description": "loopback only"})
            return
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path.startswith("/_test/"):
                self._route_test(path)
                return
            m = re.match(r"^/file/bot([^/]+)/(.+)$", path)
            if m:
                self._route_file(m.group(1), m.group(2))
                return
            m = re.match(r"^/bot([^/]+)/([A-Za-z]+)$", path)
            if not m:
                self._json(404, {"ok": False, "error_code": 404, "description": "Not Found"})
                return
            token, method = m.group(1), m.group(2)
            if not hmac.compare_digest(token.encode(), WORLD.token.encode()):
                self._json(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
                return
            params, files, _ = self._params()
            fn = METHODS_LOWER.get(method.lower())
            if fn is None:
                with WORLD.lock:
                    WORLD.unknown_methods[method] = WORLD.unknown_methods.get(method, 0) + 1
                WORLD.log("api", method=method, ok=False, error="method not implemented by the fake")
                self._json(404, {"ok": False, "error_code": 404, "description": "Not Found: method not found"})
                return
            try:
                result = fn(params, files)
            except ApiError as e:
                WORLD.log("error", method=method, code=e.code, error=e.description,
                          chat=params.get("chat_id"), mid=params.get("message_id"))
                self._json(e.code, {"ok": False, "error_code": e.code, "description": e.description, **e.extra})
                return
            self._json(200, {"ok": True, "result": result})
        except BrokenPipeError:
            return
        except Exception as e:  # noqa: BLE001 — сбой подставного API не должен ронять сервер
            try:
                WORLD.log("fake_crash", path=path.split("/bot")[0], error="%s: %s" % (type(e).__name__, e))
                self._json(500, {"ok": False, "error_code": 500, "description": "Internal Server Error (fake)"})
            except Exception:  # noqa: BLE001
                pass

    def _route_file(self, token: str, fpath: str):
        if not hmac.compare_digest(token.encode(), WORLD.token.encode()):
            self._send(401, b"Unauthorized", "text/plain")
            return
        fid = fpath.rsplit("/", 1)[-1]
        rec = WORLD.files.get(fid)
        if rec is None:
            self._send(404, b"Not Found", "text/plain")
            return
        WORLD.log("api", method="downloadFile", file_id=fid, size=len(rec["data"]))
        self._send(200, rec["data"], "application/octet-stream")

    def _route_test(self, path: str):
        params, _files, body = self._params()
        name = path[len("/_test/"):]
        if name == "message":
            self._json(200, t_message(params))
        elif name == "document":
            self._json(200, t_document(params))
        elif name == "press":
            try:
                self._json(200, t_press(params))
            except ApiError as e:
                self._json(404, {"error": e.description})
        elif name == "events":
            self._json(200, t_events(params))
        elif name == "chat":
            self._json(200, t_chat(params))
        elif name == "callback":
            self._json(200, t_callback(params))
        elif name == "state":
            self._json(200, t_state(params))
        elif name == "user":
            self._json(200, WORLD.user(int(params["id"]), params.get("first_name", ""), params.get("username", ""),
                                       params.get("lang", "ru")))
        elif name == "initdata":
            self._send(200, t_initdata(params).encode(), "text/plain; charset=utf-8")
        elif name == "webapp":
            code, page = t_webapp(params)
            self._send(code, page.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._json(404, {"error": "unknown test endpoint"})


def main() -> None:
    global WORLD
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--port", type=int, default=18081)
    a = ap.parse_args()
    token = open(a.token_file, encoding="utf-8").read().strip()
    if not TOKEN_RE.match(token):
        sys.exit("token file: not a bot token shape")
    WORLD = World(token, a.state_dir)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    WORLD.log("fake_start", port=a.port, bot_id=WORLD.bot_id)
    print("fake Telegram Bot API on 127.0.0.1:%d (bot id %d)" % (a.port, WORLD.bot_id), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
