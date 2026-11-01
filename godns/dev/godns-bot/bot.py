#!/usr/bin/env python3
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
"""goDNS bot — Telegram-интерфейс управления goDNS.

Непривилегированный клиент: работает от пользователя godns-bot, root не
держит, к системе не прикасается. Всё привилегированное уходит демону
godns-botd через JSON-RPC поверх UNIX-сокета /run/godns-botd.sock —
демон сам проверяет и вызывающего (SO_PEERCRED), и админство (bot.env).

Меню зеркалит SSH-меню goDNS: состояние · статистика · сертификат ·
апстримы · порты и путь · проверка · служба · копии · как подключаться.
Кнопки есть только у действий, которые реально исполнимы без терминала;
редкие и интерактивные (выбор сертификата, набор апстримов, смена портов,
восстановление копии) — честная подсказка «из SSH-меню», а не кнопка,
которая упадёт. Единственный экран со свободным текстом — «свой апстрим»
(адрес, порт, имя): черновик на пользователя с TTL, по образцу
типизированных подтверждений (см. поток cu:*).

Разрушительное — смена секретного пути и удаление goDNS — только через
типизированное подтверждение (точное слово, TTL 2 минуты), как в
SSH-меню и по playbook goVLESS.

Привязка админа: одноразовый код из SSH-меню (`/start КОД`). Окна
«первый написавший — админ» нет вообще: код живёт в bot.env, гаснет
после первого успеха и не возрождается рестартом бота (грабли goTelegram
с окном от старта процесса тут невозможны по построению).

Библиотека — python-telegram-bot v21+ (как у goTelegram: локальный
проверенный образец). Импорт файла не имеет побочных эффектов; токен
нужен только main().
"""

import asyncio
import html
import json
import logging
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    MenuButtonWebApp,
    Update,
    WebAppInfo,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

_BOT_DIR = Path(__file__).resolve().parent
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

from i18n import (  # noqa: E402
    LANG_NAMES,
    SUPPORTED_LANGS,
    get_language_name,
    get_user_lang,
    set_user_lang,
    t as _t,
    tf as _tf,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("godns-bot")
# httpx на INFO печатает полный URL Bot API — с ТОКЕНОМ внутри. Журнал
# юнита читается из веб-части, токен туда попадать не должен (грабли
# goTelegram, перенесены дословно).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── Конфигурация из окружения (systemd EnvironmentFile=/etc/godns/bot.env) ──
SOCK_PATH = os.environ.get("GODNS_BOTD_SOCK", "/run/godns-botd.sock")

ADMIN_IDS: set = set()
for _part in os.environ.get("ADMIN_IDS", "").split(","):
    _part = _part.strip()
    if _part.isdigit():
        ADMIN_IDS.add(int(_part))

# Файл читаем сами, а не полагаемся на окружение юнита: состав админов
# меняется без перезапуска бота (см. _admins_now).
BOT_ENV = os.environ.get("GODNS_BOT_ENV", "/etc/godns/bot.env")
_ADMIN_CACHE: dict = {"ids": None, "at": 0.0}

# ── RPC-клиент ──────────────────────────────────────────────────────────


class RpcTransportError(Exception):
    """Демон недоступен: сокета нет, соединение оборвано."""


class RpcError(Exception):
    """Демон ответил ошибкой протокола/метода."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


_RPC_ID = 0


async def rpc(method: str, params: dict, timeout: float = 30.0) -> dict:
    """Один вызов = одно соединение: сокет локальный, connect дешёвый,
    а вечное соединение пришлось бы чинить после каждого рестарта демона."""
    global _RPC_ID
    _RPC_ID += 1
    req = {"jsonrpc": "2.0", "id": _RPC_ID, "method": method,
           "params": params}
    try:
        reader, writer = await asyncio.open_unix_connection(
            SOCK_PATH, limit=256_000)
    except (OSError, ValueError) as exc:
        raise RpcTransportError(str(exc)) from exc
    try:
        writer.write(json.dumps(req).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
    except asyncio.TimeoutError as exc:
        raise RpcTransportError("rpc timeout") from exc
    except (OSError, ConnectionResetError) as exc:
        raise RpcTransportError(str(exc)) from exc
    finally:
        try:
            writer.close()
        except Exception:
            pass
    if not line:
        raise RpcTransportError("empty reply")
    try:
        resp = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RpcTransportError("bad reply") from exc
    if "error" in resp:
        err = resp["error"] or {}
        raise RpcError(int(err.get("code") or 0),
                       str(err.get("message") or "error"))
    return resp.get("result") or {}


async def rpc_action(name: str, args: Optional[List[str]], tg_id: int,
                     timeout: float,
                     env: Optional[Dict[str, str]] = None) -> dict:
    """`env` — окружение действия по allowlist демона (ACTION_ENV в
    botd.py); кладётся в params только когда задан. Сейчас единственный
    ключ — GODNS_CUSTOM_PATH: путь DoH своего апстрима не ездит в args,
    потому что argv виден любому локальному пользователю."""
    params = {"name": name, "args": args or [], "tg_id": tg_id}
    if env is not None:
        params["env"] = env
    return await rpc("action", params, timeout=timeout)


# ── Реестр действий, которые бот показывает кнопками ────────────────────
# Ключ бота -> (имя действия у демона, args, таймаут с запасом, mutating).
# Согласован с ACTIONS в botd.py; test_bot.sh сверяет включение.
ACTION_SPECS: Dict[str, Tuple[str, List[str], int]] = {
    "cert_info":        ("cert_info", [], 45),
    "cert_hook":        ("cert_hook", [], 75),
    "upstream_report":  ("upstream_report", [], 75),
    "measure":          ("measure", [], 320),
    "ports_info":       ("ports_info", [], 30),
    "rotate_path":      ("rotate_path", [], 200),
    "dot_on":           ("dot_on", [], 200),
    "dot_off":          ("dot_off", [], 200),
    "test_quick":       ("test_quick", [], 200),
    "test_full":        ("test_full", [], 440),
    "test_external":    ("test_external", [], 200),
    "test_leak":        ("test_leak", [], 260),
    "svc_status":       ("svc_status", [], 75),
    "svc_start":        ("svc_start", [], 75),
    "svc_stop":         ("svc_stop", [], 75),
    "svc_apply":        ("svc_apply", [], 200),
    "svc_logs":         ("svc_logs", ["60"], 45),
    "svc_showconf":     ("svc_showconf", [], 45),
    "reload_certs":     ("reload_certs", [], 75),
    "qlog_on":          ("qlog_on", [], 75),
    "qlog_off":         ("qlog_off", [], 75),
    "stats":            ("stats", [], 45),
    "backup_create":    ("backup_create", [], 200),
    "backup_list":      ("backup_list", [], 45),
    "policy_fastest":   ("policy_set", ["fastest"], 200),
    "policy_roundrobin": ("policy_set", ["roundrobin"], 200),
    "snip_routers":     ("snippets", ["routers"], 45),
    "snip_os":          ("snippets", ["os"], 45),
    "snip_browsers":    ("snippets", ["browsers"], 45),
    "snip_profile":     ("snippets", ["profile"], 45),
    "snip_all":         ("snippets", ["all"], 45),
    "qr_android":       ("phone_qr", ["android"], 75),
    "qr_ios":           ("phone_qr", ["ios"], 75),
    "bk_daily":         ("backup_schedule_set", ["daily"], 75),
    "bk_weekly":        ("backup_schedule_set", ["weekly"], 75),
    "bk_monthly":       ("backup_schedule_set", ["monthly"], 75),
    "bk_off":           ("backup_schedule_set", ["off"], 75),
    # Текущее расписание показывается в ЗАГОЛОВКЕ меню (render_bk_sched_menu),
    # а не отдельной кнопкой — тем же способом, что состояние подробного лога
    # в «Проверке». Поэтому в ACTION_SPECS его нет: там только то, у чего
    # есть кнопка. Раньше запись тут была, кнопки не было, и возможность
    # «узнать, снимаются ли копии» оказалась недостижимой.
    # Свои апстримы: кнопки строит поток cu:* (render_ups_menu и экраны
    # шагов), а аргументы приходят из черновика — см. CUSTOM_FLOW_ACTIONS.
    "custom_list":      ("custom_list", [], 45),
    "custom_probe":     ("custom_probe", [], 90),
    "custom_add":       ("custom_add", [], 200),
    "custom_remove":    ("custom_remove", [], 200),
    "uninstall":        ("uninstall", [], 620),
}

# Кнопка после отчёта: куда «назад». action_key -> callback меню.
_BACK_OF = {
    "cert_info": "m:cert", "cert_hook": "m:cert",
    "upstream_report": "m:ups", "measure": "m:ups",
    "policy_fastest": "m:ups", "policy_roundrobin": "m:ups",
    "ports_info": "m:ports", "rotate_path": "m:ports",
    "dot_on": "m:ports", "dot_off": "m:ports",
    "test_quick": "m:test", "test_full": "m:test",
    "test_external": "m:test", "test_leak": "m:test",
    "qlog_on": "m:test", "qlog_off": "m:test",
    "svc_logs": "m:test",
    "svc_status": "m:svc", "svc_start": "m:svc", "svc_stop": "m:svc",
    "svc_apply": "m:svc", "svc_showconf": "m:svc", "reload_certs": "m:svc",
    "stats": "m:main",
    "backup_create": "m:bk", "backup_list": "m:bk",
    "bk_daily": "m:bk_sched", "bk_weekly": "m:bk_sched",
    "bk_monthly": "m:bk_sched", "bk_off": "m:bk_sched",
    "snip_routers": "m:cn", "snip_os": "m:cn", "snip_browsers": "m:cn",
    "snip_profile": "m:cn", "snip_all": "m:cn",
    "qr_android": "m:cn", "qr_ios": "m:cn",
    "custom_list": "m:ups", "custom_probe": "m:ups",
    "custom_add": "m:ups", "custom_remove": "m:ups",
}

# Действия, до которых ведёт поток «свой апстрим» (cu:*). Их кнопки
# собираются в коде — render_ups_menu и экраны шагов, — а не в MENUS, и
# аргументы к ним приходят из черновика, а не из ACTION_SPECS. test_bot.sh
# читает этот набор при проверке «у каждого действия есть кнопка»; диспетчер
# r:/c:/t: их не запускает — без черновика им нечего передать.
CUSTOM_FLOW_ACTIONS = ("custom_list", "custom_probe", "custom_add", "custom_remove")
CU_DEFAULT_PORT = {"dot": "853", "doh": "443", "plain": "53"}


async def render_ports_menu(query, user_id: int) -> None:
    """Порты и путь — с живым состоянием DoT.

    ГРАБЛИ: меню рисовалось общим render_menu и ВСЕГДА показывало обе
    кнопки — «Включить DoT» и «Выключить DoT», — не глядя на то, что
    сейчас. Оператор жал «Включить» на уже включённом и не понимал,
    сделалось ли что-нибудь. Показываем состояние и только уместную
    кнопку. Заодно возвращаем сведения о портах: действие ports_info было
    написано, но кнопки на него не эмитило ни одно меню.
    """
    kv = {}
    try:
        kv = await fetch_panel(user_id)
    except (RpcTransportError, RpcError):
        pass
    dot_on = (kv.get("dot_enabled") or "no") == "yes"
    label = _t(user_id, "dot_state_on") if dot_on else _t(user_id, "dot_state_off")

    items = [("ports_info_btn", "r:ports_info"),
             ("ports_rotate_btn", "t:rotate_path")]
    items.append(("ports_dot_off_btn", "c:dot_off") if dot_on
                 else ("ports_dot_on_btn", "c:dot_on"))

    text = (_t(user_id, "ports_title") + "\n\n"
            + _t(user_id, "dot_label") + " <b>" + label + "</b>"
            + "\n\n<i>" + _t(user_id, "ports_ssh_hint") + "</i>")
    await safe_edit(query, text, reply_markup=_menu_kb(user_id, items))


async def render_bk_sched_menu(query, user_id: int) -> None:
    """Расписание копий — с текущей частотой в заголовке.

    ГРАБЛИ: задать расписание было можно, а УЗНАТЬ текущее — негде:
    действие backup_schedule_status написано и работает, но кнопки на
    него не эмитило ни одно меню. Оператор не видел, копии вообще
    снимаются или нет.
    """
    now = "unknown"
    try:
        res = await rpc_action("backup_schedule_status", [], user_id, 25)
        out = (res.get("output") or "").strip()
        now = out.splitlines()[0] if out else "unknown"
    except (RpcTransportError, RpcError):
        pass
    title_key, items, hint = MENUS["m:bk_sched"]
    # Первая строка вывода — это уже «Сейчас: off» целиком (её печатает
    # godns_backup_schedule_status через ключ bk_sched_current). Своя
    # подпись сверху давала «Сейчас: Сейчас: off». Берём то, что после
    # двоеточия, и подписываем сами — так частота ещё и переводится.
    freq = now.split(":", 1)[1].strip() if ":" in now else now.strip()
    freq_h = _t(user_id, "bk_freq_" + freq) if freq in ("off", "daily", "weekly", "monthly") else freq
    text = (_t(user_id, title_key) + "\n\n"
            + _t(user_id, "bk_sched_now") + " <b>" + _esc(freq_h) + "</b>")
    await safe_edit(query, text, reply_markup=_menu_kb(user_id, items))


async def render_ups_menu(query, user_id: int, note: str = "") -> None:
    """Апстримы — с живым числом своих записей и кнопками потока cu:*.

    По образцу render_ports_menu: набор кнопок зависит от состояния.
    «Добавить» есть, пока своих меньше предела — предел печатает
    custom_list первой строкой (PROV_CUSTOM_MAX из lib), бот число не
    сочиняет; «Удалить» — только когда есть что удалять. MENUS["m:ups"]
    остаётся как есть: диспетчер и контрактный тест читают его, кнопки
    cu:* живут только здесь. Список не прочитался — показываем обе кнопки
    и не врём числом: экраны потока сами скажут, что не так.
    """
    listed = False
    n, cap = 0, 8
    try:
        _n, _a, timeout = ACTION_SPECS["custom_list"]
        res = await rpc_action("custom_list", [], user_id, timeout)
        if res.get("rc") == 0:
            info = _cu_parse_list(res.get("output") or "")
            n, cap, listed = len(info["items"]), info["max"], True
    except (RpcTransportError, RpcError):
        pass
    title_key, items, hint_key = MENUS["m:ups"]
    items = list(items)
    if not listed or n < cap:
        items.append(("ups_custom_add_btn", "cu:add"))
    if not listed or n > 0:
        items.append(("ups_custom_rm_btn", "cu:rm"))
    text = _t(user_id, title_key)
    if note:
        text += "\n\n" + note
    if listed:
        text += "\n\n" + _tf(user_id, "ups_custom_line", n, cap)
        if n >= cap:
            text += "\n" + _tf(user_id, "ups_custom_limit", cap)
    text += "\n\n<i>" + _t(user_id, hint_key) + "</i>"
    await safe_edit(query, text, reply_markup=_menu_kb(user_id, items))


# ── Подтверждения ───────────────────────────────────────────────────────
# Двухшаговые (кнопкой): токен одноразовый, живёт 2 минуты.
_CONFIRMS: Dict[str, dict] = {}
# Типизированные (словом): на пользователя, живёт 2 минуты.
_PENDING_TYPED: Dict[int, dict] = {}
# Черновик «своего апстрима»: на пользователя, живёт 2 минуты с последнего
# принятого ответа. Тот же механизм, что у типизированных подтверждений:
# чистится в _sweep_confirms, гаснет любой навигацией вне cu:* и /start.
_CU_DRAFT: Dict[int, dict] = {}
CONFIRM_TTL = 120

# Слова типизированных подтверждений — на языке пользователя.
TYPED_WORDS = {
    "rotate_path": {"ru": "СМЕНИТЬ", "en": "ROTATE"},
    "uninstall":   {"ru": "УДАЛИТЬ", "en": "DELETE"},
}
TYPED_WARN_KEY = {"rotate_path": "rotate_warn", "uninstall": "uninstall_warn"}

# Двухшаговые: ключ действия -> ключ текста-предупреждения.
CONFIRM_WARN_KEY = {
    "dot_on": "dot_on_warn",
    "dot_off": "dot_off_warn",
    "svc_stop": "svc_stop_warn",
    "svc_apply": "svc_apply_warn",
    "test_external": "ext_warn",
    "qlog_on": "qlog_on_warn",
}

# ── Мелкие помощники ────────────────────────────────────────────────────

_BACKGROUND_TASKS: set = set()


def spawn_task(coro) -> "asyncio.Task":
    """asyncio.create_task без потери ссылки (иначе GC может собрать
    задачу до завершения — грабли goTelegram, перенесены дословно)."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _uid(update: Optional[Update]) -> Optional[int]:
    if update is None:
        return None
    user = getattr(update, "effective_user", None)
    return user.id if user else None


def _admins_now() -> set:
    """Состав админов с диска, кэш на несколько секунд.

    ADMIN_IDS заполняется один раз при импорте из окружения юнита и
    пополняется на привязке. Убрать оттуда никого нельзя было: человека
    вычёркивали из bot.env, чтобы отобрать доступ, а бот до перезапуска
    продолжал показывать ему меню. Действие демон бы отбил — он читает
    файл на каждый вызов, — но человек этого не знал и видел рабочий
    интерфейс. Обратное так же неприятно: дописанный в файл админ до
    перезапуска в меню не попадал.
    """
    now = time.time()
    if _ADMIN_CACHE["ids"] is not None and (now - _ADMIN_CACHE["at"]) < 5.0:
        return _ADMIN_CACHE["ids"]
    env: dict = {}
    try:
        for line in Path(BOT_ENV).read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        # Файл не прочитался — остаёмся на том, что знали. Пустой ответ
        # запер бы хозяина снаружи из-за случайного сбоя чтения.
        return set(ADMIN_IDS)
    ids: set = set()
    for key in ("ADMIN_IDS", "ALLOWED_IDS", "ADMIN_ID", "CHAT_ID"):
        raw = env.get(key, "")
        if not raw:
            continue
        ids = {int(x) for x in re.split(r"[,\s]+", raw) if x.isdigit()}
        if ids:
            break
    if not ids:
        return set(ADMIN_IDS)
    _ADMIN_CACHE["ids"] = ids
    _ADMIN_CACHE["at"] = now
    return ids


def is_admin(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    return user_id in _admins_now() or user_id in ADMIN_IDS


def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


async def safe_edit(query, text: str, reply_markup=None) -> bool:
    """edit_message_text без падений на «not modified» и мёртвых
    сообщениях (приём goTelegram: safe_edit_message)."""
    try:
        await query.edit_message_text(
            text, reply_markup=reply_markup, parse_mode="HTML",
            disable_web_page_preview=True)
        return True
    except BadRequest as exc:
        low = str(exc).lower()
        if "message is not modified" in low:
            return True
        if ("message to edit not found" in low
                or "message can't be edited" in low
                or "there is no text in the message" in low):
            logger.warning("cannot edit message: %s", exc)
            return False
        raise


def _nav_kb(user_id: Optional[int], back_cb: str) -> InlineKeyboardMarkup:
    row = []
    if back_cb and back_cb != "m:main":
        row.append(InlineKeyboardButton(
            _t(user_id, "btn_back"), callback_data=back_cb))
    row.append(InlineKeyboardButton(
        _t(user_id, "btn_main"), callback_data="m:main"))
    return InlineKeyboardMarkup([row])


class _MsgQuery:
    """Обёртка над Message: safe_edit и show_action_result ждут
    query-подобный объект (edit_message_text, message, get_bot).

    Нужна там, где экран рождается не из нажатия кнопки, а из ПРИСЛАННОГО
    текста — слово подтверждения и шаги «своего апстрима»: callback query
    там нет, есть только сообщение, которым мы ответили.
    """

    def __init__(self, message):
        self.message = message

    async def edit_message_text(self, *args, **kwargs):
        return await self.message.edit_text(*args, **kwargs)

    def get_bot(self):
        return self.message.get_bot()

    async def answer(self, text=None, show_alert=False, **_kw):
        # У callback query answer() — подтверждение нажатия, с текстом —
        # всплывашка. У сообщения нажатия нет; текст («занято») отдаём
        # ответным сообщением, чтобы он не потерялся.
        if text:
            await self.message.reply_text(text)


# Предел Telegram — 4096 символов НА ОТПРАВЛЯЕМОЕ сообщение, а отправляем
# мы не сырой текст, а `<pre>` + html-экранирование. Один «&» превращается
# в «&amp;» — рост в пять раз, «<» в «&lt;» — в четыре.
#
# ГРАБЛИ: нарезка считала длину СЫРОГО текста, и на обычном выводе (3478
# символов на проводе при пределе 3500) это было незаметно. Но имя в
# DNS-запросе задаёт клиент, dnsdist пишет его в лог, а оператор смотрит
# лог из чата — то есть текст с угловыми скобками и амперсандами приходит
# СНАРУЖИ. На проверке 4000 таких символов сообщение раздувалось до 17511
# и Telegram отвергал его целиком: оператор видел пустоту вместо журнала.
#
# Поэтому меряем то, что реально уйдёт на провод.
_WIRE_OVERHEAD = len("<pre></pre>")


def _wire_len(text: str) -> int:
    """Длина сообщения после экранирования и обёртки — то, что считает Telegram."""
    return len(_esc(text)) + _WIRE_OVERHEAD


def _chunks(text: str, limit: int = 3500) -> List[str]:
    """Режем по строкам, не по байтам посреди строки.

    `limit` — предел ДЛЯ ОТПРАВЛЯЕМОГО сообщения, а не для сырого текста.
    """
    def split_long(line: str) -> List[str]:
        # Строка, которая одна не влезает. Режем половинным делением по
        # экранированной длине: посимвольный проход по длинному журналу
        # стоил бы квадрата.
        pieces = []
        while _wire_len(line) > limit:
            lo, hi = 1, len(line)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if _wire_len(line[:mid]) <= limit:
                    lo = mid
                else:
                    hi = mid - 1
            pieces.append(line[:lo])
            line = line[lo:]
        if line:
            pieces.append(line)
        return pieces

    out: List[str] = []
    cur = ""
    for raw_line in text.split("\n"):
        for line in split_long(raw_line):
            candidate = cur + "\n" + line if cur else line
            if cur and _wire_len(candidate) > limit:
                out.append(cur)
                cur = line
            else:
                cur = candidate
    if cur:
        out.append(cur)
    return out or [""]


# Простейший антидребезг: не чаще одного нажатия в 0.7 с на пользователя.
_LAST_TAP: Dict[int, float] = {}


def _too_fast(user_id: int) -> bool:
    now = time.monotonic()
    last = _LAST_TAP.get(user_id, 0.0)
    _LAST_TAP[user_id] = now
    return (now - last) < 0.7


# У пользователя — не больше одной операции в полёте: щёлкать по кнопке
# «полная проверка» пять раз подряд не должно давать пять проверок.
_INFLIGHT: set = set()

# ── Panel: разбор TSV от actions.sh panel ───────────────────────────────


def parse_kv(text: str) -> Dict[str, str]:
    data = {}
    for line in text.split("\n"):
        if "\t" in line:
            key, _, value = line.partition("\t")
            data[key.strip()] = value.strip()
    return data


async def fetch_panel(user_id: int) -> Dict[str, str]:
    res = await rpc_action("panel", [], user_id, 25)
    return parse_kv(res.get("output") or "")


def _human_date(iso: str) -> str:
    """ISO → 25.11.2026. Пусто или мусор — прочерк."""
    if isinstance(iso, str) and len(iso) == 10 and iso[4] == "-" and iso[7] == "-":
        return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"
    return iso or "—"


def render_status_text(user_id: int, kv: Dict[str, str]) -> str:
    lines = [_t(user_id, "st_title"), ""]
    state = kv.get("state", "not_installed")
    state_txt = {
        "running": _t(user_id, "st_running"),
        "stopped": _t(user_id, "st_stopped"),
    }.get(state, _t(user_id, "st_not_installed"))
    lines.append(f"{_t(user_id, 'st_service')}: <b>{state_txt}</b>")
    if kv.get("drift") == "yes":
        lines.append(_t(user_id, "st_drift"))

    domain = kv.get("domain") or ""
    if domain:
        # Домен и сертификат — РАЗНЫЕ сроки, и раньше они стояли одной
        # строкой: рядом с именем домена печаталось «(сертификат: осталось
        # 89 дн.)» — число без даты, читавшееся как срок ДОМЕНА. Сроки не
        # связаны и чинятся по-разному: сертификат перевыпускается за
        # минуту, регистрация продлевается у регистратора и деньгами.
        reg = ""
        if kv.get("reg_status") == "ok" and kv.get("reg_until"):
            reg = "  " + _tf(user_id, "st_domain_paid",
                             _human_date(kv["reg_until"]))
        lines.append(
            f"{_t(user_id, 'st_domain')}: <b>{_esc(domain)}</b>{reg}")

        days = kv.get("cert_days") or ""
        until = _human_date(kv.get("cert_until") or "")
        cert = _t(user_id, "st_none")
        if days:
            try:
                if int(days) <= 0:
                    cert = _tf(user_id, "st_cert_expired_on", until)
                else:
                    cert = _tf(user_id, "st_cert_until", until, days)
            except ValueError:
                pass
        lines.append(f"{_t(user_id, 'st_cert')}: {cert}")
    else:
        lines.append(
            f"{_t(user_id, 'st_domain')}: {_t(user_id, 'st_none')}")

    doh = kv.get("doh_url") or ""
    if doh:
        lines.append(f"{_t(user_id, 'st_doh')}: <code>{_esc(doh)}</code>")
        # Адрес DoT — только при dot_enabled=yes: выключенный DoT не имеет
        # права выглядеть живым адресом (железное правило goDNS).
        if kv.get("dot_enabled") == "yes":
            lines.append(
                f"{_t(user_id, 'st_dot')}: <code>"
                f"{_esc(domain)}:{_esc(kv.get('dot_port') or '853')}</code>")
        else:
            lines.append(
                f"{_t(user_id, 'st_dot')}: {_t(user_id, 'st_dot_off')}")

    policy = kv.get("policy") or ""
    if policy:
        pol_txt = _t(user_id, "policy_roundrobin") \
            if policy == "roundrobin" else _t(user_id, "policy_fastest")
        lines.append(f"{_t(user_id, 'st_policy')}: {pol_txt}")
    ups_n = kv.get("upstream_count") or "0"
    ups = kv.get("upstreams") or ""
    if ups:
        lines.append(
            f"{_t(user_id, 'st_upstreams')}: <b>{_esc(ups_n)}</b> "
            f"(<code>{_esc(ups)}</code>)")
    return "\n".join(lines)


# ── Клавиатуры меню ─────────────────────────────────────────────────────


def main_menu_kb(user_id: int, webapp_url: str = "") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_t(user_id, "btn_status"), callback_data="v:status"),
         InlineKeyboardButton(_t(user_id, "btn_stats"), callback_data="r:stats")],
        [InlineKeyboardButton(_t(user_id, "btn_cert"), callback_data="m:cert"),
         InlineKeyboardButton(_t(user_id, "btn_ups"), callback_data="m:ups")],
        [InlineKeyboardButton(_t(user_id, "btn_ports"), callback_data="m:ports"),
         InlineKeyboardButton(_t(user_id, "btn_test"), callback_data="m:test")],
        [InlineKeyboardButton(_t(user_id, "btn_svc"), callback_data="m:svc"),
         InlineKeyboardButton(_t(user_id, "btn_backup"), callback_data="m:bk")],
        [InlineKeyboardButton(_t(user_id, "btn_connect"), callback_data="m:cn"),
         InlineKeyboardButton(_t(user_id, "btn_lang"), callback_data="m:lang")],
    ]
    if webapp_url:
        rows.append([InlineKeyboardButton(
            _t(user_id, "btn_webapp"), web_app=WebAppInfo(url=webapp_url))])
    return InlineKeyboardMarkup(rows)


def _menu_kb(user_id: int, items: List[Tuple[str, str]],
             back_cb: str = "m:main") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(_t(user_id, key), callback_data=cb)]
            for key, cb in items]
    rows.append([InlineKeyboardButton(
        _t(user_id, "btn_main"), callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)


MENUS = {
    "m:cert": ("cert_title", [
        ("cert_show_btn", "r:cert_info"),
        ("cert_hook_btn", "r:cert_hook"),
    ], "cert_ssh_hint"),
    "m:ups": ("ups_title", [
        ("ups_report_btn", "r:upstream_report"),
        ("ups_measure_btn", "r:measure"),
        ("ups_policy_btn", "m:policy"),
    ], "ups_ssh_hint"),
    # Кнопки этих двух меню собираются в render_ports_menu: набор зависит
    # от живого состояния DoT. Запись здесь оставлена, чтобы `data in
    # MENUS` в диспетчере продолжал знать про ключ.
    "m:ports": ("ports_title", [
        ("ports_info_btn", "r:ports_info"),
        ("ports_rotate_btn", "t:rotate_path"),
        ("ports_dot_on_btn", "c:dot_on"),
        ("ports_dot_off_btn", "c:dot_off"),
    ], "ports_ssh_hint"),
    "m:test": ("test_title", [
        ("test_quick_btn", "r:test_quick"),
        ("test_full_btn", "r:test_full"),
        ("test_ext_btn", "c:test_external"),
        ("test_leak_btn", "r:test_leak"),
        ("test_logs_btn", "r:svc_logs"),
        ("qlog_on_btn", "c:qlog_on"),
        ("qlog_off_btn", "r:qlog_off"),
    ], None),
    "m:svc": ("svc_title", [
        ("svc_status_btn", "r:svc_status"),
        ("svc_start_btn", "r:svc_start"),
        ("svc_stop_btn", "c:svc_stop"),
        ("svc_apply_btn", "c:svc_apply"),
        ("svc_logs_btn", "r:svc_logs"),
        ("svc_conf_btn", "r:svc_showconf"),
        ("svc_reload_btn", "r:reload_certs"),
        ("svc_uninstall_btn", "t:uninstall"),
    ], None),
    "m:bk": ("bk_title", [
        ("bk_create_btn", "r:backup_create"),
        ("bk_list_btn", "r:backup_list"),
        ("bk_sched_btn", "m:bk_sched"),
    ], "bk_ssh_hint"),
    "m:bk_sched": ("bk_sched_title", [
        ("bk_daily_btn", "r:bk_daily"),
        ("bk_weekly_btn", "r:bk_weekly"),
        ("bk_monthly_btn", "r:bk_monthly"),
        ("bk_off_btn", "r:bk_off"),
    ], None),
    "m:cn": ("cn_title", [
        ("cn_routers_btn", "r:snip_routers"),
        ("cn_os_btn", "r:snip_os"),
        ("cn_browsers_btn", "r:snip_browsers"),
        ("cn_qr_android_btn", "r:qr_android"),
        ("cn_qr_ios_btn", "r:qr_ios"),
        ("cn_profile_btn", "r:snip_profile"),
        ("cn_all_btn", "r:snip_all"),
    ], None),
}


# ── Отрисовка отчёта действия ───────────────────────────────────────────


def _extract_media(output: str):
    """Вынуть из вывода строки-маркеры TGPHOTO:/TGFILE:.

    Контракт с actions.sh: действие печатает `TGPHOTO:/путь` или
    `TGFILE:/путь`, бот шлёт содержимое фотографией/документом, а
    остальной текст уходит сообщением как обычно. Пути принимаются ТОЛЬКО
    из каталога состояния бота: маркер едет через тот же канал, что и
    журнал dnsdist, а в журнале бывают строки, пришедшие от посторонних
    клиентов, — путь из такой строки читать нельзя.
    """
    allowed = os.path.realpath(
        os.path.join(os.environ.get("GODNS_BOT_STATE", "/opt/godns/bot/state"),
                     "share"))
    media, kept = [], []
    for line in output.splitlines():
        s = line.strip()
        kind = None
        if s.startswith("TGPHOTO:"):
            kind = "photo"
        elif s.startswith("TGFILE:"):
            kind = "file"
        if kind is None:
            kept.append(line)
            continue
        path = s.split(":", 1)[1].strip()
        real = os.path.realpath(path)
        if real == allowed or not real.startswith(allowed + os.sep):
            # Не наш каталог — маркер игнорируем, строку не показываем:
            # в ней путь, который никому не нужен.
            continue
        media.append((kind, real))
    return media, "\n".join(kept).strip()


async def _send_media(bot, chat_id: int, media) -> None:
    for kind, path in media:
        try:
            with open(path, "rb") as fh:
                if kind == "photo":
                    await bot.send_photo(chat_id, fh)
                else:
                    await bot.send_document(
                        chat_id, fh,
                        filename=os.path.basename(path))
        except Exception:
            logger.exception("media send failed: %s", path)


async def show_action_result(query, user_id: int, action_key: str,
                             res: dict) -> None:
    back_cb = _BACK_OF.get(action_key, "m:main")
    kb = _nav_kb(user_id, back_cb)
    rc = res.get("rc")
    output = (res.get("output") or "").strip()
    media, output = _extract_media(output)

    if res.get("busy"):
        await safe_edit(query, _t(user_id, "busy"), reply_markup=kb)
        return
    if res.get("timeout"):
        await safe_edit(
            query, _tf(user_id, "timeout_msg", res["timeout"]),
            reply_markup=kb)
        return

    status = _t(user_id, "ok_done") if rc == 0 else _t(user_id, "fail_done")
    if media:
        await _send_media(query.get_bot(), query.message.chat_id, media)
    if not output:
        await safe_edit(query, status, reply_markup=kb)
        return

    parts = _chunks(output)
    first = f"<pre>{_esc(parts[0])}</pre>"
    if len(parts) == 1:
        await safe_edit(query, f"{first}\n{status}", reply_markup=kb)
        return
    await safe_edit(query, first)
    chat_id = query.message.chat_id
    bot = query.get_bot()
    for part in parts[1:-1]:
        await bot.send_message(
            chat_id, f"<pre>{_esc(part)}</pre>", parse_mode="HTML")
    await bot.send_message(
        chat_id, f"<pre>{_esc(parts[-1])}</pre>\n{status}",
        parse_mode="HTML", reply_markup=kb)


async def _show_rpc_failure(query, user_id: int, exc: BaseException,
                            what: str) -> None:
    """Единый отчёт о сбое запуска: демон недоступен / демон отказал /
    прочее. Само сообщение шлём под try: сбой отчёта о сбое не должен
    ронять задачу с несобранным исключением."""
    if isinstance(exc, RpcTransportError):
        text = _t(user_id, "rpc_down")
    elif isinstance(exc, RpcError):
        text = f"✗ <code>{_esc(exc)}</code>"
    else:
        logger.error("action %s failed", what, exc_info=exc)
        text = _t(user_id, "fail_done")
    try:
        await safe_edit(query, text, reply_markup=_nav_kb(user_id, "m:main"))
    except Exception:
        pass


async def run_and_show(query, user_id: int, action_key: str,
                       args_override: Optional[List[str]] = None) -> None:
    """Запуск действия + отчёт. Вызывается из spawn_task: длинные
    проверки не должны замораживать обработку других сообщений.

    args_override — аргументы, известные только в момент нажатия (id
    своего апстрима из карточки удаления); по умолчанию — из ACTION_SPECS.
    """
    try:
        name, args, timeout = ACTION_SPECS[action_key]
        if args_override is not None:
            args = args_override
        res = await rpc_action(name, args, user_id, timeout)
        await show_action_result(query, user_id, action_key, res)
    except Exception as exc:
        await _show_rpc_failure(query, user_id, exc, action_key)
    finally:
        _INFLIGHT.discard(user_id)


async def launch_action(query, user_id: int, action_key: str,
                        args_override: Optional[List[str]] = None) -> None:
    if user_id in _INFLIGHT:
        await query.answer(_t(user_id, "busy"), show_alert=True)
        return
    _INFLIGHT.add(user_id)
    # ГРАБЛИ: между add() и spawn_task() стоит сетевой вызов. safe_edit
    # гасит только BadRequest, а TimedOut/NetworkError из PTB летят наверх —
    # и user_id оставался в _INFLIGHT НАВСЕГДА. Дальше любое нажатие
    # отвечало «другая операция ещё выполняется», и вылечить это мог только
    # перезапуск бота. Снимаем отметку на любом исходе, кроме успешного
    # запуска задачи: с этого момента её снимет run_and_show.
    try:
        _name, _args, timeout = ACTION_SPECS[action_key]
        if timeout > 90:
            text = _tf(user_id, "working_long", timeout)
        else:
            text = _t(user_id, "working")
        await safe_edit(query, text)
    except Exception:
        _INFLIGHT.discard(user_id)
        raise
    spawn_task(run_and_show(query, user_id, action_key, args_override))


# ── Экраны ──────────────────────────────────────────────────────────────


async def render_main(target, user_id: int, *, edit: bool) -> None:
    webapp = await _webapp_url(user_id)
    kb = main_menu_kb(user_id, webapp)
    text = _t(user_id, "main_title")
    if edit:
        await safe_edit(target, text, reply_markup=kb)
    else:
        await target.reply_text(
            text, reply_markup=kb, parse_mode="HTML",
            disable_web_page_preview=True)


# ГРАБЛИ: отметкой «никогда не спрашивали» здесь стоял ноль, а
# time.monotonic() отсчитывается от НЕОПРЕДЕЛЁННОГО начала: на Linux это
# время с загрузки (большое число), а на macOS — почти ноль. При малом
# значении самый первый вызов попадал в «кэш свеж» и возвращал пустую
# начальную строку, то есть кнопка мини-приложения не появлялась вовсе.
# Отметка «не спрашивали» обязана быть заведомо недостижимой, а не нулём.
_WEBAPP_NEVER = float("-inf")
_WEBAPP_CACHE = {"url": "", "ts": _WEBAPP_NEVER}


WEBAPP_TTL_OK = 300      # удачный ответ живёт 5 минут
WEBAPP_TTL_FAIL = 20     # неудачный — секунды, чтобы не долбить демона


async def _webapp_url(user_id: int) -> str:
    """Адрес мини-аппа — из panel (его собирает root-демон из config.json;
    сам бот config.json прочитать не может и не должен).

    ГРАБЛИ: на старте адрес запрашивается от имени user_id=0 — чтобы
    выставить кнопку меню чата. Демон отвечает 403 «caller is not an
    admin», и раньше эта НЕУДАЧА клалась в кэш на те же пять минут. Итог:
    первые пять минут после каждого перезапуска бота кнопка
    «Мини-приложение» пропадала из меню вовсе, а оператор видел меню без
    панели и не понимал, почему. Неудача больше не затирает удачный
    ответ и живёт секунды, а не минуты.
    """
    now = time.monotonic()
    ttl = WEBAPP_TTL_OK if _WEBAPP_CACHE["url"] else WEBAPP_TTL_FAIL
    if now - _WEBAPP_CACHE["ts"] < ttl:
        return _WEBAPP_CACHE["url"]
    url = ""
    try:
        kv = await fetch_panel(user_id)
        url = kv.get("webui_url") or ""
        if not url.startswith("https://"):
            url = ""
    except (RpcTransportError, RpcError):
        url = ""
    if url:
        _WEBAPP_CACHE.update(url=url, ts=now)
    else:
        # Прежний удачный адрес сохраняем: он вернее пустоты.
        _WEBAPP_CACHE["ts"] = now
    return _WEBAPP_CACHE["url"]


async def render_status(query, user_id: int) -> None:
    try:
        kv = await fetch_panel(user_id)
    except RpcTransportError:
        await safe_edit(query, _t(user_id, "rpc_down"),
                        reply_markup=_nav_kb(user_id, "m:main"))
        return
    except RpcError as exc:
        await safe_edit(query, f"✗ <code>{_esc(exc)}</code>",
                        reply_markup=_nav_kb(user_id, "m:main"))
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(_t(user_id, "btn_refresh"),
                             callback_data="v:status"),
        InlineKeyboardButton(_t(user_id, "btn_main"),
                             callback_data="m:main"),
    ]])
    await safe_edit(query, render_status_text(user_id, kv), reply_markup=kb)


async def render_menu(query, user_id: int, menu_key: str) -> None:
    title_key, items, hint_key = MENUS[menu_key]
    text = _t(user_id, title_key)
    if hint_key:
        text += "\n\n<i>" + _t(user_id, hint_key) + "</i>"
    await safe_edit(query, text, reply_markup=_menu_kb(user_id, items))


async def render_policy_menu(query, user_id: int) -> None:
    try:
        kv = await fetch_panel(user_id)
    except (RpcTransportError, RpcError):
        kv = {}
    policy = kv.get("policy") or "fastest"
    pol_txt = _t(user_id, "policy_roundrobin") \
        if policy == "roundrobin" else _t(user_id, "policy_fastest")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_t(user_id, "ups_policy_fastest_btn"),
                              callback_data="r:policy_fastest")],
        [InlineKeyboardButton(_t(user_id, "ups_policy_rr_btn"),
                              callback_data="r:policy_roundrobin")],
        [InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="m:ups"),
         InlineKeyboardButton(_t(user_id, "btn_main"), callback_data="m:main")],
    ])
    await safe_edit(query, _tf(user_id, "ups_policy_title", pol_txt),
                    reply_markup=kb)


async def render_test_menu(query, user_id: int) -> None:
    """Меню проверки — с живым состоянием подробного лога в заголовке:
    режим, который включается кнопкой и заливает диск, обязан быть виден."""
    state = "unknown"
    try:
        res = await rpc_action("qlog_state", [], user_id, 25)
        state = parse_kv(res.get("output") or "").get("qlog", "unknown")
    except (RpcTransportError, RpcError):
        pass
    title_key, items, _hint = MENUS["m:test"]
    label = {
        "on": _t(user_id, "qlog_on"),
        "off": _t(user_id, "qlog_off"),
    }.get(state, _t(user_id, "qlog_unknown"))
    text = (_t(user_id, title_key) + "\n\n"
            + _t(user_id, "qlog_label") + " <b>" + label + "</b>")
    await safe_edit(query, text, reply_markup=_menu_kb(user_id, items))


# ── Подтверждения ───────────────────────────────────────────────────────


def _sweep_confirms() -> None:
    """Одна функция — три словаря с TTL: токены, типизированные слова,
    черновики «своего апстрима»."""
    now = time.monotonic()
    for token in [k for k, v in _CONFIRMS.items() if v["exp"] < now]:
        _CONFIRMS.pop(token, None)
    for uid in [k for k, v in _PENDING_TYPED.items() if v["exp"] < now]:
        _PENDING_TYPED.pop(uid, None)
    for uid in [k for k, v in _CU_DRAFT.items() if v["exp"] < now]:
        _CU_DRAFT.pop(uid, None)


async def ask_confirm(query, user_id: int, action_key: str,
                      args: Optional[List[str]] = None,
                      warn: Optional[str] = None) -> None:
    """Двухшаговое подтверждение кнопкой.

    args — аргументы действия, известные только сейчас (id своего апстрима);
    они едут в записи токена и попадают в launch_action при «y:». warn —
    готовый текст экрана; без него — ключ из CONFIRM_WARN_KEY.
    """
    _sweep_confirms()
    token = secrets.token_urlsafe(9)
    _CONFIRMS[token] = {
        "uid": user_id, "action": action_key, "args": args,
        "exp": time.monotonic() + CONFIRM_TTL,
    }
    if warn is None:
        warn = _t(user_id, CONFIRM_WARN_KEY[action_key])
    back_cb = _BACK_OF.get(action_key, "m:main")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_t(user_id, "btn_confirm"),
                              callback_data=f"y:{token}")],
        [InlineKeyboardButton(_t(user_id, "btn_cancel"),
                              callback_data=back_cb)],
    ])
    await safe_edit(query, warn, reply_markup=kb)


async def ask_typed(query, user_id: int, action_key: str) -> None:
    _sweep_confirms()
    lang = get_user_lang(user_id)
    word = TYPED_WORDS[action_key].get(lang) or TYPED_WORDS[action_key]["ru"]
    _PENDING_TYPED[user_id] = {
        "action": action_key, "word": word,
        "exp": time.monotonic() + CONFIRM_TTL,
        "chat_id": query.message.chat_id if query.message else None,
    }
    back_cb = _BACK_OF.get(action_key, "m:main")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        _t(user_id, "btn_cancel"), callback_data=back_cb)]])
    await safe_edit(query, _tf(user_id, TYPED_WARN_KEY[action_key], word),
                    reply_markup=kb)


# ── Свой апстрим: поток cu:* ────────────────────────────────────────────
# Единственный экран бота, которому нужен СВОБОДНЫЙ текст (адрес, порт,
# имя). Механизм тот же, что у типизированных подтверждений: черновик на
# пользователя с TTL CONFIRM_TTL, чистится в _sweep_confirms, гаснет любой
# навигацией вне cu:* и /start. ConversationHandler не заводим: один
# словарь рядом с _PENDING_TYPED понятнее второго механизма состояний.
#
# Порядок шагов — тот же, что в SSH (custom_upstream_add), и решает его не
# бот, а lib/webui.sh: предупреждение о plain к публичному адресу приходит
# ДО пробы (rc 4), и без ack ни проба, ни запись не выполняются на стороне
# демона. Бот лишь показывает и переспрашивает.
#
# Экраны шагов рисуются safe_edit в исходном сообщении меню; ответы на
# текст — новым сообщением через _MsgQuery, и дальше кнопки живут на нём.

_CU_HOST_RE = re.compile(r"^[A-Za-z0-9:][A-Za-z0-9.:-]{0,252}$")
_CU_PATH_RE = re.compile(r"^/[A-Za-z0-9._~%/-]{0,127}$")
_CU_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f|;]{1,40}$")
_CU_ID_RE = re.compile(r"^custom-[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")


def _cu_port_ok(port: str) -> bool:
    return bool(re.fullmatch(r"[1-9][0-9]{0,4}", port or "")) and int(port) <= 65535


def _cu_parse_address(proto: str, text: str) -> Optional[Tuple[str, str, str]]:
    """Адрес свободным текстом → (host, port, path) или None.

    Правила — те же, что у SSH-экрана (godns.sh custom_upstream_add):
    снять https:// и http://, для doh отделить путь после первого «/»,
    затем [v6]:port · [v6] · голый v6 (два двоеточия и больше) · host:port
    · host. Хост — только [A-Za-z0-9.:-], регэксп границы демона: бот не
    отправляет то, что демон заведомо отвергнет, — человеку нужна фраза,
    а не «-32602». Порт в адресе — 1..65535, путь — как _prov_valid_path.
    Пустой порт/путь — «по умолчанию», решает lib.

    Пробелы ВНУТРИ адреса — отказ, а не склейка: SSH-экран удаляет их
    (tr -d), и «host with space» молча превращался бы в «hostwithspace».
    Чистая функция: test_bot.sh достаёт её из дерева и гоняет по случаям.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("https://"):
        raw = raw[len("https://"):]
    elif raw.startswith("http://"):
        raw = raw[len("http://"):]
    path = ""
    if proto == "doh" and "/" in raw:
        raw, _, tail = raw.partition("/")
        path = "/" + tail
    host, port = raw, ""
    if raw.startswith("["):
        host, closed, rest = raw[1:].partition("]")
        if not closed:
            return None
        if rest:
            if not rest.startswith(":"):
                return None
            port = rest[1:]
    elif raw.count(":") >= 2:
        host = raw                                   # голый IPv6
    elif ":" in raw:
        host, _, port = raw.partition(":")
    if not host or not _CU_HOST_RE.fullmatch(host):
        return None
    if port and not _cu_port_ok(port):
        return None
    if path and not _CU_PATH_RE.fullmatch(path):
        return None
    return host, port, path


def _cu_valid_name(name: str) -> bool:
    """До 40 знаков, без управляющих символов и без «|» и «;» —
    разделителей записи custom_upstreams."""
    return bool(_CU_NAME_RE.fullmatch(name or ""))


def _cu_parse_list(text: str) -> dict:
    """Вывод custom_list → {"max", "set_count", "items": [...]}.

    Строки различаются числом полей: две — служебные пары (max печатает
    фасад, set_count — lib), шесть — записи id/name/proto/addr/sni/in_set.
    Путь DoH в списке отсутствует по построению: lib его не печатает.
    """
    info = {"max": 8, "set_count": 0, "items": []}
    for line in (text or "").split("\n"):
        f = line.rstrip("\r").split("\t")
        if len(f) == 2:
            if f[0] == "max" and f[1].strip().isdigit():
                info["max"] = int(f[1])
            elif f[0] == "set_count" and f[1].strip().isdigit():
                info["set_count"] = int(f[1])
        elif len(f) >= 6 and f[0].startswith("custom-"):
            info["items"].append({
                "id": f[0], "name": f[1] or f[0], "proto": f[2] or "dot",
                "addr": f[3], "sni": f[4], "in_set": f[5].strip() == "yes",
            })
    return info


def _cu_addr_display(ip: str, port: str) -> str:
    return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"


def _cu_default_name(draft: dict) -> str:
    return draft.get("sni") or draft.get("ip") or draft.get("host") or ""


def _without_kv(text: str, keys=None) -> str:
    """Убрать TSV-строки (ключ<TAB>значение) из вывода: это машинные метки
    для разбора, человеку в <pre> они не нужны. keys=None — все такие."""
    kept = []
    for line in (text or "").split("\n"):
        if "\t" in line and (keys is None or line.split("\t", 1)[0] in keys):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _cu_new_draft(proto: str, chat_id) -> dict:
    return {
        "await": None,        # чего ждём текстом: addr | port | name; None — кнопку
        "proto": proto, "host": "", "port": "", "path": "", "name": "",
        "ip": "", "sni": "", "private": None, "probe_ok": None,
        "latency": "", "ack": False,
        "exp": time.monotonic() + CONFIRM_TTL,
        "chat_id": chat_id, "step_msg": None,
    }


def _cu_touch(draft: dict) -> None:
    """Каждый принятый ответ продлевает черновик."""
    draft["exp"] = time.monotonic() + CONFIRM_TTL


def _cu_cancel_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        _t(user_id, "btn_cancel"), callback_data="cu:cancel")]])


def _cu_retry_kb(user_id: int, proto: str) -> InlineKeyboardMarkup:
    # «Другой адрес» — это шаг 2 с тем же протоколом, то есть cu:proto:<p>:
    # он создаёт свежий черновик, и истёкший старый ему не помеха.
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t(user_id, "cu_retry_btn"),
                              callback_data="cu:proto:" + proto)],
        [InlineKeyboardButton(_t(user_id, "btn_cancel"), callback_data="cu:cancel")],
    ])


def _cu_port_kb(user_id: int, proto: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_tf(user_id, "cu_port_keep_btn", CU_DEFAULT_PORT[proto]),
                              callback_data="cu:port:def")],
        [InlineKeyboardButton(_t(user_id, "cu_port_other_btn"), callback_data="cu:port:ask")],
        [InlineKeyboardButton(_t(user_id, "btn_cancel"), callback_data="cu:cancel")],
    ])


def _cu_port_text(user_id: int, proto: str) -> str:
    return _tf(user_id, "cu_port_ask", _t(user_id, "cu_proto_" + proto),
               CU_DEFAULT_PORT[proto])


def _cu_confirm_text(user_id: int, draft: dict) -> str:
    addr = _cu_addr_display(draft["ip"], draft["port"])
    if draft["host"] != draft["ip"]:
        addr = f"{draft['host']} → {addr}"
    probe = ("✅ " if draft["probe_ok"] else "✗ ") + (draft["latency"] or "?")
    return _tf(user_id, "cu_confirm", _t(user_id, "cu_proto_" + draft["proto"]),
               _esc(addr), _esc(draft["name"]), _esc(probe))


def _cu_confirm_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t(user_id, "cu_save_enable_btn"), callback_data="cu:save:yes")],
        [InlineKeyboardButton(_t(user_id, "cu_save_only_btn"), callback_data="cu:save:no")],
        [InlineKeyboardButton(_t(user_id, "btn_cancel"), callback_data="cu:cancel")],
    ])


async def cu_start(query, user_id: int) -> None:
    """Шаг 1 — протокол."""
    _CU_DRAFT.pop(user_id, None)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_t(user_id, "cu_proto_dot_btn"), callback_data="cu:proto:dot")],
        [InlineKeyboardButton(_t(user_id, "cu_proto_doh_btn"), callback_data="cu:proto:doh")],
        [InlineKeyboardButton(_t(user_id, "cu_proto_plain_btn"), callback_data="cu:proto:plain")],
        [InlineKeyboardButton(_t(user_id, "btn_cancel"), callback_data="cu:cancel")],
    ])
    await safe_edit(query, _t(user_id, "cu_proto_title"), reply_markup=kb)


async def cu_ask_addr(query, user_id: int, proto: str) -> None:
    """Шаг 2 — адрес: черновик рождается здесь, дальше ждём текст."""
    draft = _cu_new_draft(proto, query.message.chat_id if query.message else None)
    draft["await"] = "addr"
    draft["step_msg"] = query.message
    _CU_DRAFT[user_id] = draft
    await safe_edit(query, _t(user_id, "cu_addr_ask_" + proto),
                    reply_markup=_cu_cancel_kb(user_id))


async def cu_launch_probe(query, user_id: int, draft: dict) -> None:
    """Шаг 3b — проба. _INFLIGHT-страж как у launch_action, с теми же
    граблями: между add() и spawn_task() стоит сетевой вызов, и отметку
    до запуска задачи снимает любой сбой здесь, а после — только
    cu_run_probe в finally."""
    if user_id in _INFLIGHT:
        await query.answer(_t(user_id, "busy"), show_alert=True)
        return
    _INFLIGHT.add(user_id)
    try:
        draft["await"] = None
        _cu_touch(draft)
        _n, _a, timeout = ACTION_SPECS["custom_probe"]
        await safe_edit(query, _tf(user_id, "cu_probing", timeout),
                        reply_markup=_cu_cancel_kb(user_id))
    except Exception:
        _INFLIGHT.discard(user_id)
        raise
    spawn_task(cu_run_probe(query, user_id, draft))


async def cu_run_probe(query, user_id: int, draft: dict) -> None:
    try:
        _name, _args, timeout = ACTION_SPECS["custom_probe"]
        args = [draft["proto"], draft["host"], draft["port"],
                "yes" if draft["ack"] else "no"]
        # Путь DoH — ТОЛЬКО окружением, никогда в args: см. botd.py.
        res = await rpc_action("custom_probe", args, user_id, timeout, env={"GODNS_CUSTOM_PATH": draft["path"]})
        if _CU_DRAFT.get(user_id) is not draft:
            # Пока шла проба, человек нажал «Отмена» или начал заново:
            # cu_cancel уже перерисовал экран, результат некому показывать.
            return
        await cu_show_probe(query, user_id, draft, res)
    except Exception as exc:
        _CU_DRAFT.pop(user_id, None)
        await _show_rpc_failure(query, user_id, exc, "custom_probe")
    finally:
        _INFLIGHT.discard(user_id)


async def cu_show_probe(query, user_id: int, draft: dict, res: dict) -> None:
    """Исход пробы → следующий шаг в том же сообщении."""
    proto = draft["proto"]
    if res.get("busy"):
        await safe_edit(query, _t(user_id, "busy"),
                        reply_markup=_cu_retry_kb(user_id, proto))
        return
    if res.get("timeout"):
        await safe_edit(query, _tf(user_id, "timeout_msg", res["timeout"]),
                        reply_markup=_cu_retry_kb(user_id, proto))
        return
    rc = res.get("rc")
    output = (res.get("output") or "").strip()
    kv = parse_kv(output)
    if rc == 4 or kv.get("needs_ack") == "yes":
        # plain к публичному адресу: цена названа ДО пробы, ни один пакет
        # ещё не ушёл — это правило lib, бот только переспрашивает.
        draft["private"] = False
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(_t(user_id, "cu_plain_go_btn"), callback_data="cu:plain:go")],
            [InlineKeyboardButton(_t(user_id, "btn_cancel"), callback_data="cu:cancel")],
        ])
        await safe_edit(query, _t(user_id, "cu_plain_public"), reply_markup=kb)
        return
    if rc not in (0, 3):
        # Отказ до пробы: имя не разрешилось, IP вместо имени в SNI, порт…
        # Причина уже напечатана lib — показываем её, а не свой пересказ.
        text = _without_kv(output) or _t(user_id, "fail_done")
        await safe_edit(query, f"<pre>{_esc(text)}</pre>",
                        reply_markup=_cu_retry_kb(user_id, proto))
        return
    draft["ip"] = kv.get("ip") or draft["host"]
    draft["sni"] = kv.get("sni") or ""
    draft["port"] = kv.get("port") or draft["port"] or CU_DEFAULT_PORT[proto]
    draft["latency"] = (kv.get("latency") or "").strip()
    draft["probe_ok"] = rc == 0
    draft["private"] = {"yes": True, "no": False}.get(kv.get("private") or "")
    lines = []
    if rc == 0:
        lines.append(_tf(user_id, "cu_probe_ok", _esc(draft["latency"] or "?")))
    else:
        lines.append(_tf(user_id, "cu_probe_failed", _esc(draft["latency"] or "?")))
    if draft["ip"] != draft["host"]:
        lines.append(_tf(user_id, "cu_resolved", _esc(draft["host"]), _esc(draft["ip"])))
    if proto == "plain" and draft["private"]:
        lines.append(_t(user_id, "cu_plain_private"))
    # Шаг 4 — имя, в том же сообщении.
    lines += ["", _t(user_id, "cu_name_ask")]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(_tf(user_id, "cu_name_keep_btn", _cu_default_name(draft)),
                              callback_data="cu:name:host")],
        [InlineKeyboardButton(_t(user_id, "btn_cancel"), callback_data="cu:cancel")],
    ])
    draft["await"] = "name"
    _cu_touch(draft)
    await safe_edit(query, "\n".join(lines), reply_markup=kb)


async def cu_launch_add(query, user_id: int, draft: dict, enable: bool) -> None:
    """Шаг 5 → запись. Тот же _INFLIGHT-страж, что у пробы."""
    if user_id in _INFLIGHT:
        await query.answer(_t(user_id, "busy"), show_alert=True)
        return
    _INFLIGHT.add(user_id)
    try:
        _n, _a, timeout = ACTION_SPECS["custom_add"]
        await safe_edit(query, _tf(user_id, "working_long", timeout))
    except Exception:
        _INFLIGHT.discard(user_id)
        raise
    # Черновик отработал: дальше — отчёт действия. Повторное нажатие на
    # старом экране не должно записать апстрим дважды.
    _CU_DRAFT.pop(user_id, None)
    spawn_task(cu_run_add(query, user_id, draft, enable))


async def cu_run_add(query, user_id: int, draft: dict, enable: bool) -> None:
    try:
        _name, _args, timeout = ACTION_SPECS["custom_add"]
        args = [draft["proto"], draft["host"], draft["port"], draft["name"],
                "yes" if enable else "no", "yes" if draft["ack"] else "no"]
        res = await rpc_action("custom_add", args, user_id, timeout, env={"GODNS_CUSTOM_PATH": draft["path"]})
        # Строка id<TAB>… — машинная метка для панели, оператору не нужна.
        res = dict(res, output=_without_kv(res.get("output") or "", keys=("id",)))
        await show_action_result(query, user_id, "custom_add", res)
    except Exception as exc:
        await _show_rpc_failure(query, user_id, exc, "custom_add")
    finally:
        _INFLIGHT.discard(user_id)


async def _cu_fetch_list(query, user_id: int) -> Optional[dict]:
    """custom_list → разобранный список. Отказ уже показан — вернётся None."""
    try:
        _n, _a, timeout = ACTION_SPECS["custom_list"]
        res = await rpc_action("custom_list", [], user_id, timeout)
    except Exception as exc:
        await _show_rpc_failure(query, user_id, exc, "custom_list")
        return None
    if res.get("rc") != 0 or res.get("busy") or res.get("timeout"):
        await show_action_result(query, user_id, "custom_list", res)
        return None
    return _cu_parse_list(res.get("output") or "")


async def _cu_rm_render(query, user_id: int, info: dict) -> None:
    if not info["items"]:
        await safe_edit(query, _t(user_id, "cu_rm_none"),
                        reply_markup=_nav_kb(user_id, "m:ups"))
        return
    rows = []
    for it in info["items"]:
        proto_label = _t(user_id, "cu_proto_" + it["proto"])
        label = f"{it['name']} · {proto_label} · {it['addr']}"
        if it["in_set"]:
            label += " " + _t(user_id, "cu_rm_in_set_mark")
        # callback ≤ 64 байт: «cu:rm:» + id (custom-<slug ≤ 28>-NN) ≤ 45.
        rows.append([InlineKeyboardButton(label, callback_data="cu:rm:" + it["id"])])
    rows.append([InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="m:ups"),
                 InlineKeyboardButton(_t(user_id, "btn_main"), callback_data="m:main")])
    await safe_edit(query, _t(user_id, "cu_rm_title"),
                    reply_markup=InlineKeyboardMarkup(rows))


async def cu_rm_list(query, user_id: int) -> None:
    info = await _cu_fetch_list(query, user_id)
    if info is not None:
        await _cu_rm_render(query, user_id, info)


async def cu_rm_card(query, user_id: int, cid: str) -> None:
    """Карточка удаления → двухшаговое подтверждение с id в записи токена."""
    if not _CU_ID_RE.fullmatch(cid):
        return
    info = await _cu_fetch_list(query, user_id)
    if info is None:
        return
    item = next((it for it in info["items"] if it["id"] == cid), None)
    if item is None:
        # Записи уже нет (сняли из SSH или панели) — список, каков он сейчас.
        await _cu_rm_render(query, user_id, info)
        return
    state = _t(user_id, "cu_rm_in_set") if item["in_set"] else _t(user_id, "cu_rm_not_in_set")
    text = _tf(user_id, "cu_rm_confirm", _esc(item["name"]),
               _t(user_id, "cu_proto_" + item["proto"]), _esc(item["addr"]), state)
    if item["in_set"] and info["set_count"] <= 1:
        # Единственный в наборе не снимаем — правило lib; бот лишь не
        # рисует кнопку, о которую человек ударился бы.
        await safe_edit(query, text + "\n\n" + _t(user_id, "cu_rm_last"),
                        reply_markup=_nav_kb(user_id, "m:ups"))
        return
    await ask_confirm(query, user_id, "custom_remove", args=[cid], warn=text)


async def cu_cancel(query, user_id: int) -> None:
    _CU_DRAFT.pop(user_id, None)
    await render_ups_menu(query, user_id, note=_t(user_id, "cu_cancelled"))


async def cu_on_callback(query, user_id: int, data: str) -> None:
    """Диспетчер кнопок cu:*. Кнопки без черновика (add/proto/rm/cancel)
    работают всегда; остальные — только при живом черновике на нужном
    шаге, иначе «ввод устарел» с дорогой назад."""
    if data == "cu:add":
        await cu_start(query, user_id)
        return
    if data == "cu:cancel":
        await cu_cancel(query, user_id)
        return
    if data.startswith("cu:proto:"):
        proto = data[len("cu:proto:"):]
        if proto in CU_DEFAULT_PORT:
            await cu_ask_addr(query, user_id, proto)
        return
    if data == "cu:rm":
        await cu_rm_list(query, user_id)
        return
    if data.startswith("cu:rm:"):
        await cu_rm_card(query, user_id, data[len("cu:rm:"):])
        return
    draft = _CU_DRAFT.get(user_id)
    if draft is None:
        await safe_edit(query, _t(user_id, "cu_expired"),
                        reply_markup=_nav_kb(user_id, "m:ups"))
        return
    _cu_touch(draft)
    if data == "cu:port:def" and draft["host"] and not draft["port"]:
        draft["port"] = CU_DEFAULT_PORT[draft["proto"]]
        await cu_launch_probe(query, user_id, draft)
        return
    if data == "cu:port:ask" and draft["host"] and not draft["port"]:
        draft["await"] = "port"
        await safe_edit(query, _t(user_id, "cu_port_type"),
                        reply_markup=_cu_cancel_kb(user_id))
        return
    if data == "cu:plain:go" and draft["host"] and draft["probe_ok"] is None:
        draft["ack"] = True
        await cu_launch_probe(query, user_id, draft)
        return
    if data == "cu:name:host" and draft["probe_ok"] is not None:
        draft["name"] = _cu_default_name(draft)
        draft["await"] = None
        await safe_edit(query, _cu_confirm_text(user_id, draft),
                        reply_markup=_cu_confirm_kb(user_id))
        return
    if data in ("cu:save:yes", "cu:save:no") and draft["probe_ok"] is not None and draft["name"]:
        await cu_launch_add(query, user_id, draft, data.endswith(":yes"))
        return
    # Кнопка другого шага (старое сообщение при живом черновике) — молча:
    # живой экран у человека уже есть, и переписывать его «устарело» врало бы.


async def _cu_probe_from_text(message, user_id: int, draft: dict) -> None:
    """Проба, запущенная присланным текстом: своё сообщение под экран."""
    if user_id in _INFLIGHT:
        await message.reply_text(_t(user_id, "busy"))
        return
    _n, _a, timeout = ACTION_SPECS["custom_probe"]
    msg = await message.reply_text(
        _tf(user_id, "cu_probing", timeout), parse_mode="HTML",
        reply_markup=_cu_cancel_kb(user_id))
    draft["step_msg"] = msg
    await cu_launch_probe(_MsgQuery(msg), user_id, draft)


async def _cu_drop_kb(draft: dict) -> None:
    """Снять кнопки с предыдущего экрана шага.

    ГРАБЛИ, увиденные глазами пользователя в Telegram Web: ответ текстом
    приходит НОВЫМ сообщением, а у прошлого экрана («Шаг 2: адрес») его
    «✖ Отмена» остаётся живой навсегда. Через две попытки в ленте висят
    две одинаковые «Отмена», и нажатие любой из них ведёт в никуда.
    """
    msg = draft.get("step_msg")
    if msg is None:
        return
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except Exception:                            # noqa: BLE001 — сообщение уже изменено/удалено
        pass


async def cu_on_text(update: Update, user_id: int, draft: dict) -> None:
    """Текст на шаге адреса / порта / имени."""
    message = update.message
    text = (message.text or "").strip()
    stage = draft.get("await")
    if stage == "addr":
        parsed = _cu_parse_address(draft["proto"], text)
        if parsed is None:
            await message.reply_text(_t(user_id, "cu_addr_bad"),
                                     reply_markup=_cu_cancel_kb(user_id))
            return
        draft["host"], draft["port"], draft["path"] = parsed
        draft["await"] = None
        _cu_touch(draft)
        await _cu_drop_kb(draft)
        if draft["port"]:
            # Порт пришёл в адресе — сразу проба.
            await _cu_probe_from_text(message, user_id, draft)
            return
        msg = await message.reply_text(
            _cu_port_text(user_id, draft["proto"]), parse_mode="HTML",
            reply_markup=_cu_port_kb(user_id, draft["proto"]))
        draft["step_msg"] = msg
        return
    if stage == "port":
        if not _cu_port_ok(text):
            await message.reply_text(_t(user_id, "cu_port_bad"),
                                     reply_markup=_cu_cancel_kb(user_id))
            return
        draft["port"] = text
        draft["await"] = None
        _cu_touch(draft)
        await _cu_drop_kb(draft)
        await _cu_probe_from_text(message, user_id, draft)
        return
    if stage == "name":
        if not _cu_valid_name(text):
            await message.reply_text(_t(user_id, "cu_name_bad"),
                                     reply_markup=_cu_cancel_kb(user_id))
            return
        draft["name"] = text
        draft["await"] = None
        _cu_touch(draft)
        await _cu_drop_kb(draft)
        msg = await message.reply_text(
            _cu_confirm_text(user_id, draft), parse_mode="HTML",
            reply_markup=_cu_confirm_kb(user_id),
            disable_web_page_preview=True)
        draft["step_msg"] = msg


# ── Хендлеры ────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _uid(update)
    if update.message is None or user_id is None:
        return
    if update.effective_chat and update.effective_chat.type != "private":
        return

    if not is_admin(user_id):
        code = (context.args[0].strip() if context.args else "")
        if code:
            try:
                res = await rpc("claim",
                                {"code": code, "tg_id": user_id}, 20)
            except RpcTransportError:
                await update.message.reply_text(
                    _t(user_id, "rpc_down"), parse_mode="HTML")
                return
            if res.get("ok"):
                for admin in res.get("admins") or []:
                    ADMIN_IDS.add(int(admin))
                await update.message.reply_text(
                    _t(user_id, "claim_ok"), parse_mode="HTML")
                await render_main(update.message, user_id, edit=False)
                return
            reason = res.get("reason") or "wrong_code"
            key = {
                "closed": "claim_closed",
                "expired": "claim_expired",
                "rate_limited": "claim_rate_limited",
            }.get(reason, "claim_wrong")
            await update.message.reply_text(
                _t(user_id, key), parse_mode="HTML")
            return
        await update.message.reply_text(
            _t(user_id, "claim_hint") + "\n\n"
            + _tf(user_id, "your_id", user_id),
            parse_mode="HTML")
        return

    _PENDING_TYPED.pop(user_id, None)
    _CU_DRAFT.pop(user_id, None)
    await render_main(update.message, user_id, edit=False)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _uid(update)
    if update.message is None:
        return
    await update.message.reply_text(_t(user_id, "help_text"))


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _uid(update)
    if update.message is None or user_id is None:
        return
    await update.message.reply_text(
        _tf(user_id, "your_id", user_id), parse_mode="HTML")


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _uid(update)
    if update.message is None or not is_admin(user_id):
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(LANG_NAMES[code], callback_data=f"L:{code}")
        for code in SUPPORTED_LANGS
    ]])
    await update.message.reply_text(
        _t(user_id, "lang_title"), reply_markup=kb)


async def on_callback(update: Update,
                      context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = _uid(update)
    if query is None or user_id is None:
        return
    if not is_admin(user_id):
        await query.answer(_tf(user_id, "your_id", user_id)[:180],
                           show_alert=True)
        return
    # Сообщение могло исчезнуть (чат почищен): без guard'а первое же
    # обращение к query.message роняет обработчик (playbook §4).
    if query.message is None:
        await query.answer(_t(user_id, "menu_expired"), show_alert=True)
        return
    if _too_fast(user_id):
        await query.answer(_t(user_id, "too_fast"))
        return

    data = query.data or ""
    await query.answer()
    _sweep_confirms()
    # Любая навигация отменяет ожидание типизированного слова.
    if not data.startswith("y:"):
        _PENDING_TYPED.pop(user_id, None)
    # …и черновик «своего апстрима» — всё, что не cu:*.
    if not data.startswith("cu:"):
        _CU_DRAFT.pop(user_id, None)

    if data == "m:main":
        await render_main(query, user_id, edit=True)
        return
    if data == "v:status":
        await render_status(query, user_id)
        return
    if data == "m:ups":
        await render_ups_menu(query, user_id)
        return
    if data.startswith("cu:"):
        await cu_on_callback(query, user_id, data)
        return
    if data == "m:policy":
        await render_policy_menu(query, user_id)
        return
    if data == "m:test":
        await render_test_menu(query, user_id)
        return
    if data == "m:ports":
        await render_ports_menu(query, user_id)
        return
    if data == "m:bk_sched":
        await render_bk_sched_menu(query, user_id)
        return
    if data == "m:lang":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(LANG_NAMES[code],
                                  callback_data=f"L:{code}")
             for code in SUPPORTED_LANGS],
            [InlineKeyboardButton(_t(user_id, "btn_main"),
                                  callback_data="m:main")],
        ])
        await safe_edit(query, _t(user_id, "lang_title"), reply_markup=kb)
        return
    if data.startswith("L:"):
        code = data[2:]
        if set_user_lang(user_id, code):
            await safe_edit(
                query, _tf(user_id, "lang_saved", get_language_name(code)),
                reply_markup=_nav_kb(user_id, "m:main"))
        return
    if data in MENUS:
        await render_menu(query, user_id, data)
        return
    if data.startswith("r:"):
        action_key = data[2:]
        # ГРАБЛИ: сюда пропускалось ЛЮБОЕ действие из ACTION_SPECS,
        # включая те, что закрыты подтверждением. Крафтовый callback
        # `r:uninstall` исполнил бы удаление в обход типизированного
        # слова: у демона своего подтверждения нет, бот — единственная
        # преграда. Кнопок с такими `r:` нет ни в одном меню, то есть
        # случайно не нажать, — но защита не должна держаться на том,
        # что нужную кнопку никто не нарисовал.
        if action_key in TYPED_WORDS or action_key in CONFIRM_WARN_KEY:
            return
        # Действия потока cu:* берут аргументы из черновика, а не из
        # ACTION_SPECS: голый r:custom_add демон отвергнет по числу
        # аргументов, но и пробовать незачем.
        if action_key in CUSTOM_FLOW_ACTIONS:
            return
        if action_key in ACTION_SPECS:
            await launch_action(query, user_id, action_key)
        return
    if data.startswith("c:"):
        action_key = data[2:]
        if action_key in CONFIRM_WARN_KEY and action_key in ACTION_SPECS:
            await ask_confirm(query, user_id, action_key)
        return
    if data.startswith("t:"):
        action_key = data[2:]
        if action_key in TYPED_WORDS and action_key in ACTION_SPECS:
            await ask_typed(query, user_id, action_key)
        return
    if data.startswith("y:"):
        token = data[2:]
        entry = _CONFIRMS.pop(token, None)
        if entry is None or entry["uid"] != user_id:
            await safe_edit(query, _t(user_id, "confirm_expired"),
                            reply_markup=_nav_kb(user_id, "m:main"))
            return
        await launch_action(query, user_id, entry["action"], entry.get("args"))
        return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Типизированные подтверждения и шаги «своего апстрима»; остальной
    текст игнорируем."""
    user_id = _uid(update)
    if update.message is None or user_id is None or not is_admin(user_id):
        return
    _sweep_confirms()
    entry = _PENDING_TYPED.get(user_id)
    if entry is None:
        # Свободный текст ждёт ровно один экран — черновик «своего
        # апстрима», и только на шаге с текстом. Истёкший черновик уже
        # снят выше, и текст молча игнорируется, как и любой другой.
        draft = _CU_DRAFT.get(user_id)
        if draft is not None and draft.get("await"):
            await cu_on_text(update, user_id, draft)
        return
    _PENDING_TYPED.pop(user_id, None)
    text = (update.message.text or "").strip()
    # Точное совпадение, включая регистр: последняя преграда перед
    # необратимым, её смысл — осознанный набор (как в SSH typed_confirm).
    if text != entry["word"]:
        await update.message.reply_text(
            _tf(user_id, "confirm_mismatch", entry["word"]))
        return

    action_key = entry["action"]
    if user_id in _INFLIGHT:
        await update.message.reply_text(_t(user_id, "busy"))
        return
    _INFLIGHT.add(user_id)
    _name, _args, timeout = ACTION_SPECS[action_key]
    msg = await update.message.reply_text(
        _tf(user_id, "working_long", timeout) if timeout > 90
        else _t(user_id, "working"))
    spawn_task(run_and_show(_MsgQuery(msg), user_id, action_key))


async def _post_init(app: Application) -> None:
    user_id = None
    commands = [
        BotCommand("start", _t(user_id, "btn_main")),
        BotCommand("id", "Telegram ID"),
        BotCommand("lang", _t(user_id, "btn_lang")),
        BotCommand("help", "help"),
    ]
    try:
        await app.bot.set_my_commands(commands)
    except Exception as exc:
        logger.warning("set_my_commands failed: %s", exc)
    # Кнопка меню: мини-апп, если веб-часть настроена; иначе — команды.
    try:
        # ГРАБЛИ: здесь стоял `_webapp_url(0)`. Нулевой идентификатор
        # админом не бывает никогда, демон отвечает 403, и кнопка меню
        # чата не выставлялась вовсе. Спрашиваем от имени первого
        # привязанного админа; если админов нет — спрашивать не у кого,
        # и кнопкой меню будут команды, что и правильно.
        url = await _webapp_url(next(iter(ADMIN_IDS), 0)) if ADMIN_IDS else ""
        if url:
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="goDNS Pro", web_app=WebAppInfo(url=url)))
        else:
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonCommands())
    except Exception as exc:
        logger.warning("set_chat_menu_button failed: %s", exc)


def main() -> int:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        logger.error("BOT_TOKEN is empty — refusing to start "
                     "(set it in /etc/godns/bot.env)")
        return 1
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS is empty — waiting for a claim "
                       "via /start <code>")

    app = Application.builder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_text))

    logger.info("godns-bot starting (admins: %d)", len(ADMIN_IDS))
    # ГРАБЛИ (Ubuntu 26.04, Python 3.14): asyncio.get_event_loop() больше не
    # создаёт цикл неявно, а PTB 21 внутри run_polling зовёт именно его —
    # RuntimeError «There is no current event loop in thread 'MainThread'»,
    # и юнит уходит в петлю рестартов (88 падений за пять минут на живом
    # сервере). Ставим цикл сами; на 3.12/3.13 это безвредно.
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(allowed_updates=["message", "callback_query"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
