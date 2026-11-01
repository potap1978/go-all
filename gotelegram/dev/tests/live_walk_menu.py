# -*- coding: utf-8 -*-
"""Обход всего меню бота НАСТОЯЩИМИ обработчиками, на живом сервере.

Клики руками проверяют те кнопки, на которые хватило терпения. Здесь обходим
все: с главного экрана в ширину, каждая найденная кнопка нажимается, из
отрисованного экрана достаются следующие. Что ловим:
  • исключение в обработчике (в Telegram выглядит как «кнопка не отвечает»);
  • экран без единой кнопки возврата — тупик, из которого только /start;
  • непереведённый ключ, просочившийся в текст;
  • превышение лимитов Telegram (4096 символов, 8 кнопок в ряду).

Мутирующие обработчики не запускаются — они перечислены явно и почему.
"""
import asyncio, html, json, os, re, sys, traceback
BOT_DIR = os.environ.get("GT_BOT_DIR", "/opt/gotelegram-bot")
sys.path.insert(0, BOT_DIR)
os.chdir(BOT_DIR)

import bot as B

# Подпись кнопки возврата в текущем языке — по ней и опознаём выход с экрана.
BACK_RE = None

# argv читаем мягко: файл ещё и импортируют ради подделок Update/Context,
# и жёсткое sys.argv[1] делало его неимпортируемым без аргументов.
ADMIN = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("GT_ADMIN_ID", "0"))

# ── что не трогаем и почему ─────────────────────────────────────────────────
FORBIDDEN = {
    "ssl_renew":       "certbot renew — лимиты Let's Encrypt, ТЗ §14 запрещает",
    "menu_restart":    "рестарт telemt — рвёт живые сессии клиентов",
    "warp_on_free":    "переключает WARP, которым владелец пользуется прямо сейчас",
    "warp_off":        "то же",
    "web_on":          "включает WEB-прокси: перегенерирует nginx и перезапускает telemt",
    "web_off":         "выключает WEB-прокси: перегенерирует nginx и перезапускает telemt",
    "web_carrier":     "меняет carrier WEB-прокси и перезапускает telemt",
    "warp_key":        "ждёт ввод ключа WARP+",
    "install_migrate": "миграция установленного сервера",
    "menu_install":    "переустановка поверх рабочего сервера",
    "install_mode_lite": "смена режима",
    "install_mode_pro":  "смена режима",
    # Публикация панели наружу — не то, что стенд вправе включить сам.
    "ma_on":        "публикует админ-панель в интернет по секретному адресу",
    "ma_rotateyes": "меняет адрес панели, ломая уже разосланную ссылку",
    "ma_off":       "снимает публикацию панели",
}
FORBIDDEN_PREFIX = {
    "pro_confirm_":  "применяет шаблон сайта и перезапускает nginx",
    "lite_dom_":     "меняет режим на lite",
    "restore_yes_":  "разворачивает бекап поверх рабочей конфигурации",
    "user_delok~":   "удаляет ключ",
    "user_toggle~":  "выключает ключ у живого клиента",
    "adpi_set~":     "меняет анти-DPI профиль и перезапускает telemt",
    "backup_schedule_": "пишет cron",
    "backup_create": "создаёт бекап (проверяется отдельно, вручную)",
}
# ...кроме экранов подтверждения, которые ничего не делают до «Да»
ALLOW_ANYWAY = {"adpi_set~v4"}

# Каталог шаблонов сайта — это 1800 однотипных экранов, которые рисует одна и та
# же функция. Обходить их все значит утопить отчёт в шуме; берём по два образца
# на семейство — этого хватает, чтобы поймать ошибку в самой функции.
FAMILY = ("pro_tpl_", "pro_confirm_", "pro_cat_other~", "restore_idx_",
          "user_view~", "user_qr~", "user_iplimit~", "user_del~", "menu_users~")
FAMILY_SAMPLES = 2

# ── подделка Telegram ───────────────────────────────────────────────────────
class FUser:
    id = ADMIN; is_bot = False; first_name = "audit"
    username = "audit"; language_code = "ru"
    full_name = "audit"

class FChat:
    id = ADMIN; type = "private"; title = None; username = "audit"

class FMsg:
    message_id = 1; chat = FChat(); chat_id = ADMIN
    text = ""; date = None
    def __init__(s, sink): s.sink = sink
    async def reply_text(s, text, **kw):       s.sink.append(("reply_text", text, kw)); return s
    async def reply_html(s, text, **kw):       s.sink.append(("reply_html", text, kw));  return s
    async def reply_photo(s, photo, **kw):     s.sink.append(("reply_photo", "<bin>", kw)); return s
    async def reply_document(s, document, **kw): s.sink.append(("reply_document", "<bin>", kw)); return s
    async def reply_media_group(s, media, **kw): s.sink.append(("reply_media_group", f"<{len(media)}>", kw)); return [s]
    async def delete(s):                        s.sink.append(("delete", "", {}))
    async def edit_text(s, text, **kw):         s.sink.append(("edit_text", text, kw)); return s

class FQuery:
    def __init__(s, data, sink):
        s.data = data; s.sink = sink; s.id = "1"
        s.from_user = FUser(); s.message = FMsg(sink)
    async def answer(s, text=None, show_alert=False, **kw):
        s.sink.append(("answer", text or "", {"alert": show_alert}))
    async def edit_message_text(s, text, **kw):
        s.sink.append(("EDIT", text, kw)); return s.message
    async def edit_message_reply_markup(s, reply_markup=None, **kw):
        s.sink.append(("EDIT_KB", "", {"reply_markup": reply_markup}))
    async def edit_message_caption(s, caption=None, **kw):
        s.sink.append(("EDIT_CAP", caption or "", kw))
    async def delete_message(s): s.sink.append(("delete", "", {}))

class FUpdate:
    def __init__(s, q):
        s.callback_query = q; s.message = None
        s.effective_user = FUser(); s.effective_chat = FChat()
        s.effective_message = q.message

class FBot:
    def __init__(s, sink): s.sink = sink; s.username = "gt_audit_bot"
    async def send_message(s, chat_id, text, **kw):  s.sink.append(("send", text, kw)); return FMsg(s.sink)
    async def send_photo(s, chat_id, photo, **kw):   s.sink.append(("send_photo", "<bin>", kw)); return FMsg(s.sink)
    async def send_document(s, chat_id, document, **kw): s.sink.append(("send_doc", "<bin>", kw)); return FMsg(s.sink)
    async def send_media_group(s, chat_id, media, **kw): s.sink.append(("send_group", f"<{len(media)}>", kw)); return [FMsg(s.sink)]
    async def delete_message(s, *a, **kw): pass
    async def edit_message_text(s, *a, **kw): pass
    async def answer_callback_query(s, *a, **kw): pass
    def __getattr__(s, n):
        async def _stub(*a, **kw): s.sink.append((f"bot.{n}", "", {}))
        return _stub

class FContext:
    def __init__(s, sink, ud):
        s.user_data = ud; s.chat_data = {}; s.bot_data = {}
        s.bot = FBot(sink); s.args = []; s.application = None
        s.job_queue = None
    def __getattr__(s, n): return None


PAYLOADS = []


def _rows_of(markup):
    """Ряды клавиатуры как списки (callback, подпись, is_webapp)."""
    if markup is None:
        return []
    out = []
    for row in getattr(markup, "inline_keyboard", []) or []:
        out.append([(getattr(b, "callback_data", None), getattr(b, "text", ""),
                     getattr(b, "web_app", None) is not None) for b in row])
    return out


def kb_buttons(markup):
    """callback_data всех кнопок отрисованной клавиатуры."""
    out = []
    if markup is None: return out
    for row in getattr(markup, "inline_keyboard", []) or []:
        for b in row:
            cd = getattr(b, "callback_data", None)
            out.append((cd, getattr(b, "text", ""), getattr(b, "web_app", None) is not None))
    return out


async def press(data, ud):
    """Нажать кнопку. Возвращает (ошибка|None, текст, разметка, побочные отправки)."""
    sink = []
    q = FQuery(data, sink)
    upd = FUpdate(q)
    ctx = FContext(sink, ud)
    try:
        await asyncio.wait_for(B.handle_callback(upd, ctx), timeout=90)
    except Exception:
        return traceback.format_exc(limit=3), None, None, sink
    text, markup = None, None
    for kind, t, kw in sink:
        if kind in ("EDIT", "reply_text", "reply_html", "send"):
            text = t; markup = kw.get("reply_markup") or markup
        elif kind == "EDIT_KB":
            markup = kw.get("reply_markup")
    return None, text, markup, sink


async def main():
    global BACK_RE
    import re as _re
    _back = {B._t(ADMIN, "btn_back"), B._t(ADMIN, "btn_cancel"),
             B._t(ADMIN, "menu_close"), "◀️ Назад", "◀️ Back"}
    BACK_RE = _re.compile("|".join(_re.escape(x) for x in _back if x))
    B._WAITING_FOR_ADMIN = False
    if not B.is_user_allowed(ADMIN):
        print("!! админ не авторизован — обход невозможен"); return

    seen, queue, rows = {}, [("menu_main_root", None)], []
    # стартовая клавиатура — то, что рисует главный экран
    for cd, label, wa in kb_buttons(B.get_main_menu(ADMIN)):
        if cd: queue.append((cd, "главный"))
        elif wa: rows.append(dict(cb="<WebApp>", label=label, parent="главный",
                                  err=None, note="", info="мини-апп, открывается клиентом"))

    ud = {}
    order = 0
    fam_count = {}
    while queue:
        data, parent = queue.pop(0)
        if data in seen or data == "menu_main_root":
            continue
        seen[data] = True
        order += 1

        fam = next((f for f in FAMILY if data.startswith(f)), None)
        if fam:
            fam_count[fam] = fam_count.get(fam, 0) + 1
            if fam_count[fam] > FAMILY_SAMPLES:
                continue                      # та же функция, уже проверена выше

        why = FORBIDDEN.get(data)
        if not why and data not in ALLOW_ANYWAY:
            for p, w in FORBIDDEN_PREFIX.items():
                if data.startswith(p): why = w; break
        if why:
            rows.append(dict(cb=data, label="", parent=parent, err=None, note="ПРОПУЩЕНО: " + why))
            continue

        err, text, markup, sink = await press(data, ud)
        kids = kb_buttons(markup)
        note = []
        if err:
            note.append("ИСКЛЮЧЕНИЕ")
        else:
            if text is None and not sink:
                note.append("нет ответа")
            if text and len(text) > 4096:
                note.append(f"текст {len(text)} > 4096")
            if text and re.search(r'\b(users_|menu_|btn_|sec_|sub_|warp_|adpi_)[a-z_0-9]{3,}\b', text or ""):
                m = re.search(r'\b(?:users_|menu_|btn_|sec_|sub_|warp_|adpi_)[a-z_0-9]{3,}\b', text)
                note.append("похоже на непереведённый ключ: " + m.group(0))
            if markup is not None:
                for r in getattr(markup, "inline_keyboard", []):
                    if len(r) > 8: note.append("в ряду >8 кнопок")
                cds = [c for c, _, _ in kids if c]
                labels = [l for _, l, _ in kids]
                # Выход с экрана — это либо кнопка с подписью «Назад/Отмена»,
                # либо любая кнопка, ведущая на уже пройденный экран: на
                # подтверждении V4 выход подписан «Нет», и это тоже выход.
                back = any(BACK_RE.search(l or "") for l in labels) or \
                       any(c in ("menu_main", "close_menu") for c in cds) or \
                       any(c in seen or c == parent for c in cds)
                if cds and not back:
                    note.append("нет возврата")
            for kind, t, kw in sink:
                if kind == "answer" and kw.get("alert"):
                    note.append("alert: " + (t or "")[:40])

        # Длины, которые Telegram проверяет на своей стороне
        for cd, label, _wa in kids:
            if cd and len(cd.encode()) > 64:
                note.append(f"callback_data {len(cd.encode())}б > 64")
            if label and len(label) > 64:
                note.append(f"подпись кнопки {len(label)} символов")

        rows.append(dict(cb=data, label="", parent=parent, err=err,
                         note="; ".join(note), text=(text or "")[:120],
                         sink=[k for k, _, _ in sink]))
        if text and not err:
            PAYLOADS.append(dict(cb=data, text=text,
                                 kb=[[dict(t=b[1], c=b[0]) for b in row_]
                                     for row_ in _rows_of(markup)]))
        for cd, label, wa in kids:
            if cd and cd not in seen:
                queue.append((cd, data))

    # Пометка «открывается клиентом» — это пояснение, а не изъян: считать её
    # проблемой значит каждый раз врать в итоге, стоит включить мини-апп.
    ok = sum(1 for r in rows if not r["err"] and not r["note"])
    print(f"\n{'кнопка':30} {'откуда':16} итог")
    print("─" * 96)
    for r in rows:
        if r["err"]:
            mark = "✗ ИСКЛЮЧЕНИЕ"
        elif r["note"].startswith("ПРОПУЩЕНО"):
            mark = "· " + r["note"][:56]
        elif r["note"]:
            mark = "⚠ " + r["note"][:56]
        elif r.get("info"):
            mark = "✓ · " + r["info"][:52]
        else:
            mark = "✓"
        print(f"  {r['cb'][:28]:28} {str(r['parent'])[:14]:14} {mark}")
    print("─" * 96)
    for f, n in sorted(fam_count.items()):
        if n > FAMILY_SAMPLES:
            print(f"  · семейство {f}* — {n} экранов, проверено {FAMILY_SAMPLES} (рисует одна функция)")
    print(f"  всего экранов: {len(rows)}   чисто: {ok}   "
          f"проблем: {sum(1 for r in rows if r['err'] or (r['note'] and not r['note'].startswith('ПРОПУЩ')))}   "
          f"пропущено: {sum(1 for r in rows if r['note'].startswith('ПРОПУЩ'))}")
    for r in rows:
        if r["err"]:
            print("\n══ " + r["cb"] + "\n" + r["err"])
    json.dump(rows, open("/tmp/walk.json", "w"), ensure_ascii=False, default=str)
    json.dump(PAYLOADS, open("/tmp/payloads.json", "w"), ensure_ascii=False, default=str)
    print(f"  собрано полезных нагрузок для проверки Telegram: {len(PAYLOADS)}")

if __name__ == "__main__":
    asyncio.run(main())
