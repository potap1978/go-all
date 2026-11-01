#!/usr/bin/env python3
"""tg_user.py — «человек в Telegram» для подставного Bot API (fake_tg_api.py): написать боту, нажать кнопку,
дождаться ответа, посмотреть экран. Печатает только очищенный текст (text_safe): ни ключей vpn://, ни секретных путей
панели и страниц в выводе нет — файлы и фото только именем и размером.

    python3 tg_user.py send "/start" [--as owner|stranger|invited|<id>] [--group -100…]
    python3 tg_user.py press "Пользователи"                       # кнопка в самом свежем сообщении бота, где она есть
    python3 tg_user.py press "^5$" --until "Добавлено" --wait 300
    python3 tg_user.py press x --mid 9 --data u_view~anna          # то же нажатие по callback_data
    python3 tg_user.py show [--n 2]
    python3 tg_user.py state | events --since N

Модулем: bot_scenario.py берёт отсюда send / press / settle / bot_messages.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

API = os.environ.get("GA_FAKE_TG", "http://127.0.0.1:18081")
# Подставные люди: id вне диапазона настоящих аккаунтов не нужен — ни один запрос до Telegram не доходит.
PEOPLE = {
    "owner": (900000001, "Владелец Тестов", "ga_live_owner"),
    "stranger": (900000002, "Чужой Человек", "ga_live_stranger"),
    "invited": (900000003, "Приглашённый Админ", "ga_live_invited"),
}


def who_id(who: Any) -> int:
    if isinstance(who, int):
        return who
    if str(who) in PEOPLE:
        return PEOPLE[str(who)][0]
    return int(who)


def call(path: str, payload: Optional[Dict[str, Any]] = None, query: Optional[Dict[str, Any]] = None,
         timeout: float = 60, raw: bool = False) -> Any:
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        if raw:
            return body.decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError:
            return {"error": "HTTP %d" % e.code}
    return body.decode("utf-8", "replace") if raw else json.loads(body)


def seq_now() -> int:
    return int(call("/_test/state")["seq"])


def ensure_person(who: Any) -> int:
    uid = who_id(who)
    for key, (pid, first, uname) in PEOPLE.items():
        if pid == uid:
            call("/_test/user", {"id": uid, "first_name": first, "username": uname})
    return uid


def send(who: Any, text: str, chat_id: Optional[int] = None, chat_type: str = "") -> Dict[str, Any]:
    uid = ensure_person(who)
    before = seq_now()
    body: Dict[str, Any] = {"from": uid, "text": text}
    if chat_id:
        body.update(chat_id=chat_id, chat_type=chat_type or "group", title="ga-live group")
    r = call("/_test/message", body)
    r["since"] = before
    return r


def chat(who: Any, chat_id: Optional[int] = None) -> List[Dict[str, Any]]:
    return call("/_test/chat", query={"chat": chat_id or who_id(who)})["messages"]


def bot_messages(who: Any, chat_id: Optional[int] = None) -> List[Dict[str, Any]]:
    return [m for m in chat(who, chat_id) if m["sender"] == "bot" and not m["deleted"]]


def find_button(who: Any, label_re: str, mid: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Кнопка с подписью по регулярному выражению — в сообщении mid или в самом свежем сообщении бота, где она есть.
    Сначала точное совпадение подписи, потом поиск внутри."""
    msgs = sorted(bot_messages(who), key=lambda m: -m["mid"])
    if mid:
        msgs = [m for m in msgs if m["mid"] == mid]
    rx = re.compile(label_re)
    for exact in (True, False):
        for m in msgs:
            for row in (m.get("reply_markup") or {}).get("inline_keyboard") or []:
                for b in row:
                    text = str(b.get("text") or "")
                    hit = rx.fullmatch(text) if exact else rx.search(text)
                    if hit and "callback_data" in b:
                        return {"mid": m["mid"], "text": text, "data": b["callback_data"]}
    return None


def press(who: Any, label_re: str, mid: Optional[int] = None, data: Optional[str] = None) -> Dict[str, Any]:
    uid = ensure_person(who)
    if data is not None and mid:
        btn = {"mid": mid, "text": "<data>", "data": data}
    else:
        btn = find_button(who, label_re, mid)
    if not btn:
        raise SystemExit("no button matching %r%s" % (label_re, " in #%d" % mid if mid else ""))
    before = seq_now()
    r = call("/_test/press", {"from": uid, "message_id": btn["mid"], "data": btn["data"]})
    r.update(since=before, button=btn)
    return r


def _visible(e: Dict[str, Any]) -> bool:
    """Реакция, которую видит человек: сообщение, правка, файл, удаление, отказ API — но не пустой ответ на нажатие."""
    if e.get("kind") in ("error", "fake_crash"):
        return True
    if e.get("kind") != "send":
        return False
    if e.get("method") == "answerCallbackQuery":
        return bool(e.get("text") or e.get("show_alert"))
    return True


def settle(since: int, cb: Optional[str] = None, quiet: float = 3.0, timeout: float = 60.0,
           until: Optional[str] = None, who: Any = None) -> List[Dict[str, Any]]:
    """События после since, когда бот затих: нажатие отвечено (cb), есть видимая реакция, quiet секунд без новых
    отправок и — если задано — сообщение бота с текстом по until (долгие операции: сначала «⏳», итог позже). Реакция
    только всплывающим ответом на нажатие — ждём дольше (за ним бывает правка экрана после моста)."""
    deadline = time.time() + timeout
    events: List[Dict[str, Any]] = []
    last_change = time.time()
    seq = since
    rx = re.compile(until, re.S) if until else None
    uid = who_id(who) if who is not None else None
    while time.time() < deadline:
        r = call("/_test/events", query={"since": seq, "wait": 1.0}, timeout=40)
        new = r.get("events") or []
        if new:
            events.extend(new)
            seq = new[-1]["seq"]
            if any(e["kind"] in ("send", "error", "fake_crash") for e in new):
                last_change = time.time()
        answered = True
        if cb:
            answered = bool(call("/_test/callback", query={"id": cb}).get("answered"))
        matched = True
        if rx:
            matched = any(e.get("kind") == "send" and (uid is None or e.get("chat") == uid) and rx.search(str(e.get("text") or ""))
                          for e in events)
        visible = [e for e in events if _visible(e)]
        need = quiet if any(e.get("method") != "answerCallbackQuery" for e in visible) else quiet * 2
        if answered and matched and visible and time.time() - last_change >= need:
            break
    return events


def fmt_buttons(buttons: Optional[List[List[str]]], indent: str = "    ") -> List[str]:
    out = []
    for row in buttons or []:
        out.append(indent + " | ".join("[%s]" % b for b in row))
    return out


def fmt_event(ev: Dict[str, Any], show_buttons: bool = True) -> List[str]:
    k, m = ev.get("kind"), ev.get("method")
    lines: List[str] = []
    if k == "user":
        a = ev.get("action")
        if a == "message":
            lines.append("<< %s: %s" % (ev.get("user"), ev.get("text_safe")))
        elif a == "press":
            lines.append("<< %s нажал %s (#%s)" % (ev.get("user"), ev.get("data"), ev.get("mid")))
        else:
            lines.append("<< %s %s" % (ev.get("user"), a))
        return lines
    if k == "error":
        return ["!! %s → %s (chat %s, #%s)" % (m, ev.get("error"), ev.get("chat"), ev.get("mid"))]
    if k == "fake_crash":
        return ["!! FAKE CRASH %s" % ev.get("error")]
    if k == "api":
        if m in ("setChatMenuButton", "setMyCommands", "deleteWebhook", "getFile", "downloadFile"):
            return ["   · %s %s" % (m, json.dumps({x: ev.get(x) for x in ("chat", "type", "text_safe", "url_safe", "lang", "commands") if ev.get(x) is not None}, ensure_ascii=False))]
        return ["   · %s%s" % (m, " (%s)" % ev.get("error") if ev.get("error") else "")]
    if k != "send":
        return ["   · %s" % json.dumps({x: y for x, y in ev.items() if x not in ("text",)}, ensure_ascii=False)]
    warn = ev.get("warn") or []
    flags = {x: y for x, y in (ev.get("flags") or {}).items() if y}
    tail = (" {%s}" % ", ".join(sorted(flags))) if flags else ""
    if m in ("sendMessage", "editMessageText", "editMessageCaption"):
        tag = "new" if m == "sendMessage" else "edit"
        prev = "" if not ev.get("preview_disabled") else " no-preview"
        lines.append(">> [#%s %s%s]%s %s" % (ev.get("mid"), tag, prev, tail, ev.get("text_safe")))
    elif m == "editMessageReplyMarkup":
        lines.append(">> [#%s buttons]" % ev.get("mid"))
    elif m == "answerCallbackQuery":
        if ev.get("text_safe") or ev.get("show_alert"):
            lines.append(">> (ответ на нажатие%s: %s)" % (", alert" if ev.get("show_alert") else "", ev.get("text_safe")))
        return lines
    elif m == "sendDocument":
        f = ev.get("file") or {}
        lines.append(">> [#%s документ] %s (%s Б, %s)%s %s" % (ev.get("mid"), f.get("name"), f.get("size"),
                                                                  f.get("looks_like"), tail, ev.get("text_safe") or ""))
    elif m == "sendPhoto":
        p = ev.get("photo") or {}
        lines.append(">> [#%s фото %sx%s, %s Б]%s %s" % (ev.get("mid"), p.get("w"), p.get("h"), p.get("size"), tail,
                                                       ev.get("text_safe") or ""))
    elif m == "sendMediaGroup":
        ph = ev.get("photos") or []
        lines.append(">> [альбом: %d фото, %s Б] %s" % (len(ph), "+".join(str(x.get("size")) for x in ph),
                                                        " / ".join(x.get("caption_safe") or "" for x in ph if x.get("caption_safe"))))
    elif m == "deleteMessage":
        lines.append(">> [#%s удалено]" % ev.get("mid"))
    else:
        lines.append(">> %s" % m)
    for w in warn:
        lines.append("!! WARN %s" % w)
    if show_buttons:
        lines.extend(fmt_buttons(ev.get("buttons")))
    return lines


def print_events(events: List[Dict[str, Any]], show_buttons: bool = True) -> None:
    for ev in events:
        for line in fmt_event(ev, show_buttons):
            print(line)


def fmt_message(m: Dict[str, Any]) -> List[str]:
    head = "[#%s %s%s]" % (m["mid"], m["kind"], " edits=%d" % m["edits"] if m.get("edits") else "")
    lines = [head + " " + (m.get("text_safe") or "")]
    if m.get("file"):
        lines.append("    файл: %s (%s Б)" % (m["file"].get("name"), m["file"].get("size")))
    lines.extend(fmt_buttons(m.get("buttons")))
    return lines


def main() -> None:
    # Общие ключи — у каждой команды: tg_user.py send "/start" --as stranger --wait 20
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--as", dest="who", default="owner")
    common.add_argument("--wait", type=float, default=60.0)
    common.add_argument("--quiet", type=float, default=3.0)
    common.add_argument("--until", default=None)
    common.add_argument("--no-buttons", action="store_true")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send", parents=[common])
    s.add_argument("text")
    s.add_argument("--group", type=int, default=0, help="написать в группу с этим id (отрицательный)")
    p = sub.add_parser("press", parents=[common])
    p.add_argument("label")
    p.add_argument("--mid", type=int, default=None)
    p.add_argument("--data", default=None)
    sh = sub.add_parser("show", parents=[common])
    sh.add_argument("--n", type=int, default=1)
    e = sub.add_parser("events", parents=[common])
    e.add_argument("--since", type=int, default=0)
    sub.add_parser("state", parents=[common])
    a = ap.parse_args()
    if a.cmd == "send":
        r = send(a.who, a.text, chat_id=a.group or None)
        evs = settle(r["since"], quiet=a.quiet, timeout=a.wait, until=a.until, who=a.who)
        print_events(evs, not a.no_buttons)
    elif a.cmd == "press":
        r = press(a.who, a.label, a.mid, a.data)
        evs = settle(r["since"], cb=r["callback_id"], quiet=a.quiet, timeout=a.wait, until=a.until, who=a.who)
        print_events(evs, not a.no_buttons)
        cbs = call("/_test/callback", query={"id": r["callback_id"]})
        if not cbs.get("answered"):
            print("!! нажатие не отвечено (answerCallbackQuery не пришёл)")
    elif a.cmd == "show":
        for m in sorted(bot_messages(a.who), key=lambda x: x["mid"])[-a.n:]:
            for line in fmt_message(m):
                print(line)
    elif a.cmd == "events":
        print_events(call("/_test/events", query={"since": a.since})["events"])
    elif a.cmd == "state":
        print(json.dumps(call("/_test/state"), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
