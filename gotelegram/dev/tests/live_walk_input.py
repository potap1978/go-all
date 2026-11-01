# -*- coding: utf-8 -*-
"""То, что обходом кнопок не проверить: ввод текста, команды, Reply-клавиатура.

Кнопка либо есть, либо нет. Ввод — сложнее: важно не только что правильное
значение принимается, но и что мусор отбивается внятным ответом, а не молчанием
и не исключением. Молчание в Telegram неотличимо от «бот умер».

Мутирующие потоки прогоняются на одноразовом ключе, который тут же создаётся и
в конце удаляется. Ключи владельца не трогаются.
"""
import asyncio, json, os, sys, traceback
BOT_DIR = os.environ.get("GT_BOT_DIR", "/opt/gotelegram-bot")
sys.path.insert(0, BOT_DIR); os.chdir(BOT_DIR)
import bot as B

ADMIN = int(sys.argv[1])
THROW = "zz_audit_tmp"

class FUser:
    id = ADMIN; is_bot = False; first_name = "audit"; username = "audit"
    language_code = "ru"; full_name = "audit"
class FChat:
    id = ADMIN; type = "private"; title = None; username = "audit"
class FMsg:
    message_id = 1; chat = FChat(); chat_id = ADMIN; date = None
    def __init__(s, sink, text=""): s.sink = sink; s.text = text
    async def reply_text(s, t, **kw):        s.sink.append(("reply", t, kw)); return s
    async def reply_html(s, t, **kw):        s.sink.append(("reply", t, kw)); return s
    async def reply_photo(s, p, **kw):       s.sink.append(("photo", kw.get("caption",""), kw)); return s
    async def reply_document(s, d, **kw):    s.sink.append(("doc", kw.get("caption",""), kw)); return s
    async def reply_media_group(s, m, **kw): s.sink.append(("group", f"<{len(m)}>", kw)); return [s]
    async def delete(s):                     s.sink.append(("delete", "", {}))
    async def edit_text(s, t, **kw):         s.sink.append(("edit", t, kw)); return s
class FBot:
    def __init__(s, sink): s.sink = sink; s.username = "gt_audit_bot"
    async def send_message(s, chat_id, text, **kw): s.sink.append(("send", text, kw)); return FMsg(s.sink)
    async def set_my_commands(s, *a, **kw): pass
    async def set_chat_menu_button(s, *a, **kw): pass
    def __getattr__(s, n):
        async def _stub(*a, **kw): s.sink.append((f"bot.{n}", "", {})); return FMsg(s.sink)
        return _stub
class FUpdate:
    def __init__(s, msg):
        s.message = msg; s.callback_query = None
        s.effective_user = FUser(); s.effective_chat = FChat(); s.effective_message = msg
class FContext:
    def __init__(s, sink, ud):
        s.user_data = ud; s.chat_data = {}; s.bot_data = {}
        s.bot = FBot(sink); s.args = []; s.application = None; s.job_queue = None
    def __getattr__(s, n): return None


async def say(text, ud, fn=None):
    """Отправить боту текст. Возвращает (ошибка, ответы)."""
    sink = []
    msg = FMsg(sink, text)
    upd, ctx = FUpdate(msg), FContext(sink, ud)
    try:
        await asyncio.wait_for((fn or B.handle_text_message)(upd, ctx), timeout=90)
    except Exception:
        return traceback.format_exc(limit=3), sink
    return None, sink


def answered(sink):
    return [t for k, t, _ in sink if k in ("reply", "send", "edit", "photo", "doc") and t]


R = []
def rec(name, err, sink, want_reply=True, want_sub=None):
    reps = answered(sink)
    joined = " ".join(reps)
    if err:                       verdict = "✗ ИСКЛЮЧЕНИЕ"
    elif want_reply and not reps: verdict = "✗ молчит"
    elif want_sub and want_sub not in joined.lower(): verdict = f"⚠ ответ не по делу: {joined[:60]!r}"
    else:                         verdict = "✓"
    R.append((name, verdict, err, joined[:150]))


async def main():
    B._WAITING_FOR_ADMIN = False
    ud = {}

    # ── 1. команды ───────────────────────────────────────────────────────────
    for cmd, fn in (("/start", B.cmd_start), ("/help", B.cmd_help),
                    ("/status", B.cmd_status), ("/logs", B.cmd_logs),
                    ("/lang", B.cmd_lang), ("/keyboard", B.cmd_keyboard)):
        err, sink = await say(cmd, ud, fn)
        rec(f"команда {cmd}", err, sink)

    # ── 2. Reply-клавиатура ──────────────────────────────────────────────────
    # Клавиатура живёт в чате, а не в сообщении: после смены языка у человека
    # остаются кнопки со СТАРЫМИ подписями. Диспетчер это обещает учитывать —
    # проверяем подписи во всех языках, а не только в текущем.
    for key, action in getattr(B, "REPLY_KB_BUTTONS", []) or []:
        for lang in B.SUPPORTED_LANGS:
            label = B._t_in(lang, key)
            if not label:
                R.append((f"клавиатура {key}/{lang}", "✗ нет подписи", None, "")); continue
            resolved = B._reply_kb_action(label)
            if resolved != action:
                R.append((f"клавиатура «{label[:16]}» ({lang})",
                          f"✗ не опознана (дала {resolved!r})", None, "")); continue
            err, sink = await say(label, ud)
            rec(f"клавиатура «{label[:16]}» ({lang})", err, sink)

    # ── 3. имя нового ключа: мусор и нормальное ──────────────────────────────
    BAD_NAMES = ["../../etc/passwd", "a b c", "x" * 200, "", "имя;rm -rf /", "a\nb"]
    for bad in BAD_NAMES:
        ud["awaiting_user_name"] = True
        err, sink = await say(bad, ud)
        rec(f"имя ключа {bad[:22]!r}", err, sink)
        ud.pop("awaiting_user_name", None)

    ud["awaiting_user_name"] = True
    err, sink = await say(THROW, ud)
    rec(f"создание ключа {THROW}", err, sink)
    ud.pop("awaiting_user_name", None)
    created = THROW in B.load_telemt_users()
    R.append((f"ключ {THROW} появился в конфиге", "✓" if created else "✗ не создан", None, ""))

    if created:
        # ── 4. лимит устройств: мусор и нормальное ───────────────────────────
        for bad in ["abc", "-5", "0", "99999999", "1;ls"]:
            ud["awaiting_user_ip_limit"] = THROW
            err, sink = await say(bad, ud)
            rec(f"лимит {bad!r}", err, sink)
            ud.pop("awaiting_user_ip_limit", None)
        ud["awaiting_user_ip_limit"] = THROW
        err, sink = await say("3", ud)
        rec("лимит 3 (корректный)", err, sink)
        ud.pop("awaiting_user_ip_limit", None)

        # ── 5. кнопки карточки ключа на одноразовом ключе ────────────────────
        h = B.user_cb_key(THROW)      # тот же токен, что рисует сам бот
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from live_walk_menu import FQuery, FUpdate as CBUpd, FContext as CBCtx  # noqa
        for cb in (f"user_view~{h}", f"user_qr~{h}", f"user_toggle~{h}",
                   f"user_view~{h}", f"user_toggle~{h}",
                   f"user_del~{h}", f"user_delok~{h}"):
            sink = []
            q = FQuery(cb, sink); u = CBUpd(q); c = CBCtx(sink, ud)
            try:
                await asyncio.wait_for(B.handle_callback(u, c), timeout=90)
                err = None
            except Exception:
                err = traceback.format_exc(limit=3)
            rec(f"карточка {cb.split('~')[0]}", err, sink, want_reply=False)

        gone = THROW not in B.load_telemt_users()
        R.append((f"ключ {THROW} удалён", "✓" if gone else "✗ ОСТАЛСЯ В КОНФИГЕ", None, ""))
        if not gone:
            u = B.load_telemt_users(); u.pop(THROW, None); B.save_telemt_users(u)
            R.append(("  убран принудительно", "·", None, ""))

    # ── 6. свой git-шаблон: мусорные URL ────────────────────────────────────
    for bad in ["javascript:alert(1)", "не ссылка", "file:///etc/passwd",
                "http://127.0.0.1/x.git", "https://github.com/a/b.git"]:
        B._CUSTOM_GIT_WAITERS[ADMIN] = True
        err, sink = await say(bad, ud)
        rec(f"git-URL {bad[:26]!r}", err, sink)
        B._CUSTOM_GIT_WAITERS.pop(ADMIN, None)

    # ── 7. ключ WARP+ ────────────────────────────────────────────────────────
    for bad in ["мусор", "x" * 300]:
        ud["awaiting_warp_key"] = True
        err, sink = await say(bad, ud)
        rec(f"WARP+ ключ {bad[:16]!r}", err, sink)
        ud.pop("awaiting_warp_key", None)

    # ── 8. текст без ожидания — бот не должен отвечать на всё подряд ─────────
    err, sink = await say("просто болтовня", ud)
    R.append(("текст вне потока", "✓ (молчит)" if not answered(sink) else
              f"⚠ отвечает: {answered(sink)[0][:50]!r}", err, ""))

    print(f"\n{'проверка':44} итог")
    print("─" * 100)
    for name, verdict, err, reply in R:
        print(f"  {name[:42]:42} {verdict}")
        if verdict.startswith("✓") and reply:
            print(f"  {'':42} └ {reply[:52]}")
    bad = [r for r in R if r[1].startswith(("✗", "⚠"))]
    print("─" * 100)
    print(f"  проверок: {len(R)}   чисто: {len(R)-len(bad)}   проблем: {len(bad)}")
    for name, verdict, err, _ in R:
        if err:
            print(f"\n══ {name}\n{err}")

asyncio.run(main())
