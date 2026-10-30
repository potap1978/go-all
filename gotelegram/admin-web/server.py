#!/usr/bin/env python3
"""
goTelegram Pro local web admin.

The service is intentionally bound to 127.0.0.1:1984. Operators reach it
through an SSH tunnel; it must never be exposed directly on the public network.
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import hmac
import io
import json
import traceback
import mimetypes
import os
import re
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ADMIN_DIR = Path(os.getenv("GOTELEGRAM_ADMIN_DIR", "/opt/gotelegram-admin"))
STATIC_DIR = Path(os.getenv("GOTELEGRAM_ADMIN_STATIC", str(ADMIN_DIR / "static")))

GOTELEGRAM_CONFIG = Path(os.getenv("GOTELEGRAM_CONFIG", "/opt/gotelegram/config.json"))
TELEMT_CONFIG = Path(os.getenv("TELEMT_CONFIG", "/etc/telemt/config.toml"))
HISTORY_FILE = Path(os.getenv("GOTELEGRAM_STATS_HISTORY", "/opt/gotelegram/stats_history.csv"))
USER_HISTORY_FILE = Path(os.getenv("GOTELEGRAM_USER_STATS_HISTORY", "/opt/gotelegram/user_stats_history.csv"))
CURRENT_STATS = Path(os.getenv("GOTELEGRAM_STATS_CURRENT", "/run/gotelegram/stats_current.json"))
BACKUP_DIR = Path(os.getenv("GOTELEGRAM_BACKUP_DIR", "/opt/gotelegram/backups"))
INSTALL_DIR = Path(os.getenv("GOTELEGRAM_DIR", "/opt/gotelegram"))
BOT_DIR = Path(os.getenv("GOTELEGRAM_BOT_DIR", "/opt/gotelegram-bot"))
DISABLED_USERS_FILE = Path(os.getenv("GOTELEGRAM_DISABLED_USERS", "/opt/gotelegram/disabled_users.json"))
USER_LOCK_FILE = Path(os.getenv("GOTELEGRAM_USER_LOCK", "/run/gotelegram/admin-users.lock"))
# Тот же файл, что берёт бот: config.json пишут оба, и без общего лока
# одновременная смена языка и шаблона теряла одно из изменений.
CONFIG_JSON_LOCK = Path(os.getenv("GOTELEGRAM_CONFIG_LOCK", "/run/gotelegram/admin-config.lock"))
SHARED_443_CONFIG = Path(os.getenv("GOTELEGRAM_SHARED_443", "/opt/gotelegram/shared-443.json"))
BACKUP_SCHEDULE_FILE = Path(os.getenv("GOTELEGRAM_BACKUP_SCHEDULE", "/opt/gotelegram/backup_schedule.json"))
BACKUP_RESTORE_LOG = Path(os.getenv("GOTELEGRAM_BACKUP_RESTORE_LOG", "/var/log/gotelegram-restore.log"))

ADMIN_TOKEN_FILE = Path(os.getenv("GOTELEGRAM_ADMIN_TOKEN_FILE", str(ADMIN_DIR / "token")))

HOST = os.getenv("GOTELEGRAM_ADMIN_HOST", "127.0.0.1")
PORT = int(os.getenv("GOTELEGRAM_ADMIN_PORT", "1984"))
VERSION = "2.10.50"  # запасное значение: живое берётся из config.json / lib/common.sh
ADMIN_TOKEN_HEADER = "X-GoTelegram-Token"
ADMIN_TOKEN_PARAM = "token"
ADMIN_TOKEN_ENV = "GOTELEGRAM_ADMIN_TOKEN"
# Accept hex / urlsafe-base64 / uuid style tokens written by an operator,
# but nothing that would be mangled inside a ?token=... query string.
ADMIN_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.~=-]{16,256}$")
# DNS-rebinding guard: a browser page on any other origin resolves its own
# hostname to 127.0.0.1 and would otherwise reach this panel through the
# operator's SSH tunnel. Only real loopback names are accepted.
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
# Files served before the token check so the UI can bootstrap and read the
# token out of ?token=... . They contain no secrets.
# Ассеты без секретов остаются публичными — их тянет и сама страница входа.
# А вот корень и index.html теперь под охраной: неавторизованному нужно показать
# форму входа, иначе он видит пустую панель, которая молча получает 401 по API
# и выглядит сломанной.
PUBLIC_STATIC_PATHS = {"/app.js", "/styles.css", "/favicon.ico"}


class BadRequest(ValueError):
    """Плохой запрос от клиента: отвечаем 400, а не падаем в 502."""

ACCESS_LOG = os.getenv("GOTELEGRAM_ADMIN_ACCESS_LOG", "0") == "1"
# Хотя бы один буквоцифровой символ: старая маска пропускала имена из одних точек ("."/"..").
USER_RE = re.compile(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9_.-]{1,48}$")
LANG_RE = re.compile(r"^(en|ru)$")
WARP_KEY_RE = re.compile(r"^(?:[A-Za-z0-9]{4}(?:-[A-Za-z0-9]{4}){3}|[A-Za-z0-9]{8}(?:-[A-Za-z0-9]{8}){2})$")
SENSITIVE_CONFIG_KEYS = {"secret", "warp_key"}
BACKUP_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.tar\.gz(\.enc)?$")
MAX_UNIQUE_IP_LIMIT = 1000000
MAX_UNIQUE_IPS_MIN_MESSAGE = (
    "Лимит в один адрес рвёт обычный телефон: при переходе Wi-Fi ↔ LTE адрес меняется, старые соединения ещё живы, и клиент упирается в потолок. Замер на боевом сервере: связь пропадала в среднем на 20 секунд, в худшем случае на 6 минут. Минимум — 2. Ноль снимает персональный лимит."
)
TELEMT_RESTART_DEBOUNCE_SECONDS = float(os.getenv("GOTELEGRAM_TELEMT_RESTART_DEBOUNCE", "8"))
_LAST_TELEMT_RESTART = 0.0
# Дебаунс обязан коалесцировать правки, а не глотать их: иначе ключ, созданный
# в окно, оставался мёртвым, а удалённый продолжал пускать до следующего рестарта.
_TELEMT_RESTART_LOCK = threading.Lock()
_TELEMT_RESTART_TIMER: threading.Timer | None = None
TRAFFIC_WINDOWS = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
    "month": 30 * 24 * 60 * 60,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the whole process group, not just the direct child.

    `bash -lc "... wgcf ... wg-quick ..."` spawns grandchildren. Killing only
    bash leaves them orphaned, still holding the WARP flock, so every later
    WARP action fails. Same pattern as sh() in gotelegram-bot/bot.py.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run(cmd: list[str], timeout: int = 8) -> tuple[int, str, str]:
    try:
        proc = subprocess.Popen(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except Exception as exc:  # pragma: no cover - system dependent
        return 125, "", str(exc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode or 0, stdout or "", stderr or ""
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
        note = f"command timed out after {timeout}s"
        return 124, stdout or "", ((stderr or "") + "\n" + note).strip()
    except Exception as exc:  # pragma: no cover - system dependent
        _kill_process_tree(proc)
        return 125, "", str(exc)


def run_bytes(cmd: list[str], timeout: int = 8) -> tuple[int, bytes, str]:
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except Exception as exc:  # pragma: no cover - system dependent
        return 125, b"", str(exc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode or 0, stdout or b"", (stderr or b"").decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        return 124, b"", f"command timed out after {timeout}s"
    except Exception as exc:  # pragma: no cover - system dependent
        _kill_process_tree(proc)
        return 125, b"", str(exc)


# --------------------------------------------------------------------------
# Tiny process-wide cache. The panel auto-refreshes every 5 seconds, so any
# uncached external call (curl, full CSV parse) turns into a self-inflicted
# DoS. Every entry is keyed by an explicit stamp (deadline or file identity).
# --------------------------------------------------------------------------
_CACHE_LOCK = threading.RLock()
_CACHES: dict[str, Any] = {}
_MISS = object()

PUBLIC_IP_TTL = 300.0
PUBLIC_IP_RETRY_TTL = 30.0
SITE_STATUS_TTL = 60.0
CSV_TAIL_MIN_BYTES = 96 * 1024
CSV_TAIL_MAX_BYTES = 4 * 1024 * 1024


def _cache_lookup(key: str, stamp: Any) -> Any:
    with _CACHE_LOCK:
        entry = _CACHES.get(key)
        if entry is not None and entry[0] == stamp:
            return entry[1]
    return _MISS


def _cache_store(key: str, stamp: Any, value: Any) -> Any:
    with _CACHE_LOCK:
        if len(_CACHES) > 64:
            _CACHES.clear()
        _CACHES[key] = (stamp, value)
    return value


def _stat_key(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _tail_budget(limit: int | None) -> int:
    if not limit or limit <= 0:
        return CSV_TAIL_MAX_BYTES
    return max(CSV_TAIL_MIN_BYTES, min(CSV_TAIL_MAX_BYTES, int(limit) * 128))


def read_csv_tail(path: Path, max_bytes: int = CSV_TAIL_MAX_BYTES) -> list[dict[str, Any]]:
    """Parse only the tail of a CSV file, keeping the header line.

    stats_history.csv reaches ~52k rows; parsing it whole several times per
    /api/overview (every 5s) burned CPU for nothing. The first, possibly
    truncated, line of the tail chunk is dropped.
    """
    try:
        with path.open("rb") as fh:
            header = fh.readline()
            if not header.strip():
                return []
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            body_start = len(header)
            if size - body_start <= max_bytes:
                fh.seek(body_start)
                chunk = fh.read()
            else:
                fh.seek(size - max_bytes)
                chunk = fh.read()
                cut = chunk.find(b"\n")
                chunk = chunk[cut + 1:] if cut >= 0 else b""
    except OSError:
        return []
    text = header.decode("utf-8", errors="replace") + chunk.decode("utf-8", errors="replace")
    try:
        return list(csv.DictReader(io.StringIO(text)))
    except csv.Error:
        return []



# ════════════════════════════════════════════════════════════════════════════
# Telegram Mini App: проверка подписи initData
#
# Мини-апп отдаётся с СОБСТВЕННОГО домена пользователя через nginx по секретному
# пути, а не через сторонний туннель. Значит панель становится достижима из
# интернета, и одного «неугадываемого пути» мало. Пускаем только запросы с
# валидной подписью Telegram: HMAC по схеме WebAppData, свежий auth_date и
# user.id из списка админов бота. Токен панели (SSH-туннель) продолжает работать
# как раньше — это второй, независимый путь входа.
# ════════════════════════════════════════════════════════════════════════════

MINIAPP_INITDATA_HEADER = "X-Telegram-Init-Data"
MINIAPP_MAX_AGE_SECONDS = 24 * 3600


# Разбор .env был по голому startswith("BOT_TOKEN="): отступ перед именем или
# привычное `export BOT_TOKEN=...` — и панель молча считала, что бот не настроен.
# Сообщение при этом выдавалось то же, что и при реально отсутствующем боте,
# так что отличить одно от другого было нельзя.
def _env_value(name: str) -> str:
    try:
        raw = (BOT_DIR / ".env").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep or key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value.strip()
    return ""


def _bot_token_from_env() -> str:
    return _env_value("BOT_TOKEN")


def _bot_admin_ids() -> set[str]:
    ids: set[str] = set()
    for part in re.split(r"[,\s]+", _env_value("ALLOWED_IDS")):
        if part.isdigit():
            ids.add(part)
    return ids


def _public_panel_url(config: dict[str, Any] | None = None) -> str:
    """Адрес панели на домене — тот же, что открывает мини-апп. Пусто = не опубликована.

    Путь и домен лежат в config.json; проверку формата повторяем здесь же,
    потому что в разметку это попадает как ссылка, и мусор из конфига в ней
    оказаться не должен.
    """
    cfg = config if config is not None else (load_json(GOTELEGRAM_CONFIG, {}) or {})
    path = str(cfg.get("miniapp_path") or "").strip().strip("/")
    domain = str(cfg.get("domain") or "").strip()
    if not path or not domain:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._-]{4,64}", path):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9.-]{3,253}", domain):
        return ""
    return f"https://{domain}/{path}/"


def tfa_available() -> bool:
    """Есть ли кому и чем слать код. Панель обязана знать это ДО показа формы,
    иначе человек упирается в кнопку, которая не сработает никогда."""
    return bool(_bot_token_from_env()) and bool(_bot_admin_ids())


# str | None вместо Optional: Optional тут не был импортирован и жил только за счёт future-импорта.
def verify_telegram_init_data(init_data: str) -> str | None:
    """Вернуть telegram user_id, если подпись верна и пользователь — админ."""
    token = _bot_token_from_env()
    if not token or not init_data:
        return None
    try:
        pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    except Exception:
        return None
    data = dict(pairs)
    received = data.pop("hash", "")
    if not received:
        return None
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    # Протухшая подпись = перехваченная ссылка. Telegram кладёт auth_date всегда.
    try:
        if time.time() - int(data.get("auth_date", "0")) > MINIAPP_MAX_AGE_SECONDS:
            return None
    except Exception:
        return None
    try:
        uid = str(json.loads(data.get("user", "{}")).get("id", ""))
    except Exception:
        return None
    if not uid:
        return None
    admins = _bot_admin_ids()
    # Пока админов нет (бот не настроен) — не пускаем никого: иначе первый
    # встречный получил бы панель.
    if not admins or uid not in admins:
        return None
    return uid



# ════════════════════════════════════════════════════════════════════════════
# Двухфакторный вход в панель через Telegram-бота
#
# Панель на домене доступна из интернета, поэтому длинного пути мало. Схема:
#   • из мини-аппа Telegram — пускает подпись initData, код не нужен: Telegram
#     уже доказал, кто пришёл;
#   • из браузера — страница входа, код приходит в личку админу ботом, живёт
#     5 минут, одноразовый; после ввода браузер получает сессионную cookie;
#   • по SSH-туннелю — прежний токен, аварийный путь на случай, если бот лежит.
#
# Всё состояние в памяти процесса: рестарт панели разлогинивает всех, и это
# осознанно — так меньше поверхности, чем хранить сессии на диске.
# ════════════════════════════════════════════════════════════════════════════

TFA_CODE_TTL = 300          # код живёт 5 минут
TFA_MAX_ATTEMPTS = 5        # столько попыток на один код
TFA_REQUEST_COOLDOWN = 30   # не чаще одного кода в 30 секунд
TFA_REQUESTS_PER_HOUR = 5   # и не больше пяти в час с одного адреса
# В Pro-режиме реального адреса клиента не существует: соединение доходит до
# nginx от telemt по петле, и X-Real-IP у всех равен 127.0.0.1. Значит корзина
# одна на весь сервер — пять запросов в час запирали вход ВСЕМ администраторам,
# причём на час. Для такой общей корзины лимит должен быть выше: защиту от
# перебора несёт TFA_REQUEST_COOLDOWN (30 с между кодами), а часовой потолок
# здесь нужен лишь как страховка от долбёжки.
TFA_REQUESTS_PER_HOUR_SHARED = 30
_TFA_SHARED_IPS = {"127.0.0.1", "::1", "[::1]", "localhost", "?", ""}


# Пока панель жила только за SSH-туннелем, послабление до 30 было безобидным:
# корзину делили между собой одни администраторы. С публикацией на домене в ту
# же корзину попадает кто угодно из интернета — и получает как раз ослабленный
# лимит. Разводим корзины: публичный вход считается отдельно и по строгому
# счёту, туннельный — по прежнему. Так интернет-долбёжка не ослабляет защиту и
# заодно не запирает вход администратору, который придёт по туннелю.
#
# Отличить одно от другого можно без адреса клиента: наружу панель отдаёт
# только location мини-аппа, и он единственный ставит X-Forwarded-Host.
TFA_BUCKET_PUBLIC = "net"


def _tfa_bucket(client_ip: str, public: bool) -> str:
    return TFA_BUCKET_PUBLIC if public else client_ip


# Продление — не то же самое, что вход с улицы. При сессии в 15 минут человек
# за рабочий час просит код четырежды, а публичный лимит — пять в час: без
# отдельной корзины панель заперла бы саму себя через час работы. В эту корзину
# попадают только запросы с живой или продлеваемой сессией, то есть тот, у кого
# сессии нет, до неё не дотянется.
TFA_RENEW_PER_HOUR = 12
TFA_BUCKET_RENEW = "renew"


def _tfa_hour_limit(bucket: str) -> int:
    if bucket == TFA_BUCKET_RENEW:
        return TFA_RENEW_PER_HOUR
    if bucket == TFA_BUCKET_PUBLIC:
        return TFA_REQUESTS_PER_HOUR
    return TFA_REQUESTS_PER_HOUR_SHARED if bucket in _TFA_SHARED_IPS else TFA_REQUESTS_PER_HOUR
# Сессия короткая и продлевается кодом. Двенадцать часов означали, что забытая
# открытой вкладка остаётся входом в панель до конца суток — на опубликованной
# наружу панели это слишком щедро.
SESSION_TTL = int(os.getenv("GOTELEGRAM_SESSION_TTL", str(15 * 60)))
# Окно, в которое сессию ещё можно продлить кодом. За его пределами вход
# начинается заново. Данные в это окно уже НЕ отдаются: 15 минут истекли,
# и единственное, что разрешено, — доказать, что за экраном прежний человек.
SESSION_GRACE = int(os.getenv("GOTELEGRAM_SESSION_GRACE", "60"))
# Сама cookie живёт дольше сессии — и это не послабление: доступ даёт запись в
# памяти службы, а не наличие cookie, и к этому моменту запись уже удалена.
# Нужна она ради ОДНОГО: чтобы сервер узнал вернувшегося и сказал «сессия
# закончена, войдите заново», а не «нужен токен админки». Истекай cookie ровно
# вместе с окном продления, браузер переставал бы её слать в ту же секунду —
# и человек, отошедший на пять минут, получал бы самый бесполезный из ответов.
SESSION_COOKIE_SLACK = int(os.getenv("GOTELEGRAM_SESSION_COOKIE_SLACK", str(6 * 3600)))
SESSION_COOKIE = "gt_session"
# Пускать ли без второго фактора того, кто пришёл прямо на петлевой слушатель
# (то есть по SSH-туннелю). По умолчанию да — см. is_local_console().
#
# Выключатель нужен по двум причинам. Первая: на 127.0.0.1:1984 может постучаться
# и НЕпривилегированный процесс с той же машины — под www-data работает nginx,
# отдающий сайт-маскировку. Кому эта поверхность важнее удобства, ставит 0 и
# возвращает прежний порядок с токеном. Вторая: стенд tests/live_session_life.py
# проверяет именно жизнь сессии, ходит на петлю и без выключателя проверял бы
# пустоту.
TRUST_LOCAL_CONSOLE = os.getenv("GOTELEGRAM_TRUST_TUNNEL", "1") == "1"
# Билет ?s= оседает в access-log nginx и истории браузера, поэтому он одноразовый,
# короткоживущий и никогда не совпадает с самой сессией.
TICKET_TTL = 60

_TFA_LOCK = threading.Lock()
# Код хранится ПО КОРЗИНЕ, а не одним слотом на всю панель. Единственный слот
# означал, что любой запрос кода гасит предыдущий: посторонний, знающий секретный
# путь, дёргал /api/auth/request и код администратора становился недействителен —
# тот вводил его и получал «код истёк». Корзины же разведены по происхождению
# ("net" для интернета, "renew" для продления, адрес для туннеля), поэтому чужой
# запрос теперь тушит только свой собственный код.
_TFA_CODES: dict[str, dict[str, Any]] = {}
_TFA_REQUESTS: dict[str, list[float]] = {}
# ThreadingHTTPServer правит _SESSIONS из разных потоков: без лока параллельный
# вход ронял GC «dictionary changed size during iteration», причём до _safe.
_SESSION_LOCK = threading.Lock()
_SESSIONS: dict[str, float] = {}
_TICKETS: dict[str, float] = {}


def _tfa_hash(code: str) -> str:
    return hashlib.sha256(("gt2fa:" + code).encode()).hexdigest()


def _sessions_gc() -> None:
    # Чистим только те, у кого прошло и окно продления: удалить сессию сразу по
    # истечении значило бы, что продлевать уже нечего — состояние «renew»
    # существовать не смогло бы вовсе.
    now = time.time()
    with _SESSION_LOCK:
        for tok in [t for t, exp in _SESSIONS.items() if exp + SESSION_GRACE <= now]:
            _SESSIONS.pop(tok, None)


def session_state(token: str) -> tuple[str, int]:
    """Состояние сессии и сколько секунд до следующего рубежа.

    ok    — работает, секунды до истечения;
    renew — 15 минут прошли, идут 60 секунд на продление кодом;
    dead  — окно упущено, вход заново.
    """
    if not token:
        return "dead", 0
    _sessions_gc()
    with _SESSION_LOCK:
        exp = _SESSIONS.get(token)
    if not exp:
        return "dead", 0
    now = time.time()
    if now < exp:
        return "ok", max(0, int(exp - now))
    if now < exp + SESSION_GRACE:
        return "renew", max(0, int(exp + SESSION_GRACE - now))
    return "dead", 0


def session_valid(token: str) -> bool:
    return session_state(token)[0] == "ok"


def session_renewable(token: str) -> bool:
    return session_state(token)[0] == "renew"


def session_create() -> str:
    tok = secrets.token_urlsafe(32)
    with _SESSION_LOCK:
        _SESSIONS[tok] = time.time() + SESSION_TTL
    return tok


def ticket_create() -> str:
    """Одноразовый короткий пропуск для перехода со страницы входа на панель."""
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with _SESSION_LOCK:
        for old in [t for t, exp in _TICKETS.items() if exp <= now]:
            _TICKETS.pop(old, None)
        _TICKETS[tok] = now + TICKET_TTL
    return tok


def ticket_redeem(token: str) -> bool:
    # pop, а не get: билет виден в логах, поэтому годен строго один раз.
    if not token:
        return False
    now = time.time()
    with _SESSION_LOCK:
        exp = _TICKETS.pop(token, None)
    return bool(exp and exp > now)


# username бота нужен, чтобы в отказе назвать, КУДА идти нажимать Start.
# Значение не меняется, поэтому запрашиваем один раз за жизнь процесса.
_BOT_USERNAME_CACHE: dict[str, str] = {}


def _bot_username() -> str:
    token = _bot_token_from_env()
    if not token:
        return ""
    if token in _BOT_USERNAME_CACHE:
        return _BOT_USERNAME_CACHE[token]
    name = ""
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getMe", timeout=10
        ) as resp:
            name = str((json.load(resp).get("result") or {}).get("username") or "")
    except Exception:
        name = ""
    _BOT_USERNAME_CACHE[token] = name
    return name


# Возвращает (успех, причина). Раньше отдавал только bool, а описание ошибки от
# Telegram выбрасывалось: в лог уходило голое «HTTP Error 400: Bad Request», а
# человек видел «не удалось отправить код через бота» — по этой фразе причину
# установить нельзя. Между тем самая частая причина ровно одна и лечится в два
# касания: администратор не открывал бота, а Telegram не даёт боту написать
# первым и отвечает «chat not found».
def _telegram_send(chat_id: str, text: str, html_mode: bool = False) -> tuple[bool, str]:
    token = _bot_token_from_env()
    if not token:
        return False, "BOT_TOKEN не задан"
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps(
                {"chat_id": chat_id, "text": text,
                 **({"parse_mode": "HTML"} if html_mode else {})}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200, ""
    except urllib.error.HTTPError as exc:
        # Тело ответа Telegram несёт description — ради него всё и затевалось.
        desc = ""
        try:
            desc = str(json.loads(exc.read().decode("utf-8", "ignore")).get("description") or "")
        except Exception:
            desc = ""
        sys.stderr.write(f"2FA: Telegram отказал для {chat_id}: {exc} — {desc}\n")
        return False, desc or f"HTTP {exc.code}"
    except Exception as exc:
        sys.stderr.write(f"2FA: не удалось отправить код для {chat_id}: {exc}\n")
        return False, str(exc)


def tfa_request(client_ip: str, public: bool = False, renew: bool = False) -> tuple[bool, str]:
    """Сгенерировать код и отправить его админам в Telegram."""
    admins = _bot_admin_ids()
    if not admins:
        return False, ("вход по коду недоступен: Telegram-бот не установлен или в его "
                       "настройках нет ни одного администратора (ALLOWED_IDS). "
                       "Войдите по токену через SSH-туннель — команда на странице входа.")
    if not _bot_token_from_env():
        return False, ("вход по коду недоступен: у бота не задан BOT_TOKEN. "
                       "Войдите по токену через SSH-туннель — команда на странице входа.")
    now = time.time()
    bucket = TFA_BUCKET_RENEW if renew else _tfa_bucket(client_ip, public)
    with _TFA_LOCK:
        hist = [t for t in _TFA_REQUESTS.get(bucket, []) if now - t < 3600]
        if hist and now - hist[-1] < TFA_REQUEST_COOLDOWN:
            return False, f"код уже отправлен, повторите через {int(TFA_REQUEST_COOLDOWN - (now - hist[-1]))} с"
        if len(hist) >= _tfa_hour_limit(bucket):
            return False, "слишком много запросов кода, попробуйте через час"
        code = f"{secrets.randbelow(1000000):06d}"
        # Заодно выметаем протухшие корзины, чтобы словарь не рос вечно.
        for _b in [b for b, s in _TFA_CODES.items() if s.get("expires", 0.0) <= now]:
            _TFA_CODES.pop(_b, None)
        _TFA_CODES[bucket] = {"hash": _tfa_hash(code), "expires": now + TFA_CODE_TTL,
                              "attempts": 0, "sent_at": now}
        hist.append(now)
        _TFA_REQUESTS[bucket] = hist
    # В Pro-режиме соединение доходит до nginx не напрямую, а от telemt по
    # петле: :443 занят прокси, и он передаёт «чужой SNI» на локальный nginx
    # обычным TCP, без всяких заголовков. Поэтому реальный адрес клиента там
    # физически недоступен, и раньше в сообщении всегда стояло 127.0.0.1 —
    # то есть строчка, которая должна помогать заметить чужой вход, врала.
    # Молчать о происхождении нельзя: строка нужна как раз чтобы заметить чужой
    # вход. Адреса за маскировкой нет, но САМ ФАКТ «пришли из интернета, а не с
    # вашего туннеля» — уже то, по чему видно неладное.
    if public:
        _ip_line = "Действует 5 минут. Запрошен из интернета — по адресу панели на домене.\n"
    elif client_ip in {"127.0.0.1", "::1", "?", ""}:
        _ip_line = "Действует 5 минут. Запрошен через SSH-туннель.\n"
    else:
        _ip_line = f"Действует 5 минут. Запрошен с адреса {client_ip}.\n"
    # <code> в Telegram — не украшение: по касанию он копируется целиком.
    # Раньше шесть цифр внутри обычной строки приходилось выделять вручную,
    # причём на телефоне, где выделение цепляет соседние слова.
    text = (f"Код входа в панель: <code>{code}</code>\n"
            + _ip_line
            + "Если это не вы — просто не вводите его.")
    # any() останавливается на первом успехе — код получал только первый админ,
    # а второй ждал бы впустую, хотя войти имеет право. Шлём всем и считаем
    # успехом, если дошло хоть кому-то.
    sent = False
    reasons: list[str] = []
    for admin in sorted(admins):
        ok_send, why = _telegram_send(admin, text, html_mode=True)
        if ok_send:
            sent = True
        elif why:
            reasons.append(why)
    if not sent:
        with _TFA_LOCK:
            _TFA_CODES.pop(bucket, None)
            # Код никому не ушёл — возвращаем квоту: в Pro корзина одна на всех
            # (петлевой адрес), и лежащий бот запирал вход на час без единого кода.
            # Ключ обязан совпадать с тем, на который расход и списан: у публичного
            # входа это "net", у продления — "renew", и только у туннельного —
            # client_ip. Возврат по client_ip промахивался мимо первых двух, то
            # есть именно там, где вход и запирался.
            hist = _TFA_REQUESTS.get(bucket)
            if hist and now in hist:
                hist.remove(now)
        # «chat not found» = админ ни разу не открывал бота. Telegram запрещает
        # боту писать первым, и никакая настройка на сервере это не обойдёт —
        # человек должен сам нажать Start. Раньше отказ выглядел как поломка
        # сервера, и люди искали причину не там.
        joined = "; ".join(dict.fromkeys(reasons))
        if any("chat not found" in r.lower() or "bot was blocked" in r.lower()
               or "user is deactivated" in r.lower() for r in reasons):
            uname = _bot_username()
            where = f"@{uname}" if uname else "вашего бота"
            return False, (f"Telegram не доставил код: администратор не начинал переписку с ботом. "
                           f"Откройте {where} в Telegram и нажмите Start, затем запросите код заново. "
                           f"Бот не может написать первым — это правило Telegram, а не настройка сервера.")
        return False, f"не удалось отправить код через бота: {joined}" if joined else \
                      "не удалось отправить код через бота"
    return True, "код отправлен в Telegram"


def tfa_verify(code: str) -> tuple[bool, str]:
    code = (code or "").strip()
    now = time.time()
    given = _tfa_hash(code)
    with _TFA_LOCK:
        # Живые коды всех корзин. Проверяющий не знает, из какой корзины пришёл
        # его код (продление и обычный вход попадают в разные), поэтому годится
        # любой невыдохшийся — но попытка списывается с каждого, и перебор так
        # же ограничен пятью попытками, как раньше.
        for b in [b for b, s in _TFA_CODES.items() if s.get("expires", 0.0) <= now]:
            _TFA_CODES.pop(b, None)
        live = {b: s for b, s in _TFA_CODES.items() if s.get("hash")}
        if not live:
            return False, "код истёк — запросите новый"
        if all(s["attempts"] >= TFA_MAX_ATTEMPTS for s in live.values()):
            for b in list(live):
                _TFA_CODES.pop(b, None)
            return False, "слишком много попыток — запросите новый код"
        left = 0
        for b, st in live.items():
            if st["attempts"] >= TFA_MAX_ATTEMPTS:
                continue
            st["attempts"] += 1
            if hmac.compare_digest(given, st["hash"]):
                # одноразовый: гасим сразу после успеха
                _TFA_CODES.pop(b, None)
                return True, "ok"
            left = max(left, TFA_MAX_ATTEMPTS - st["attempts"])
    return False, f"неверный код, осталось попыток: {max(0, left)}"



# raw-строка: в JS-регэкспах есть \/ — в обычной строке это DeprecationWarning, с Python 3.12 SyntaxError.
LOGIN_PAGE_HTML = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход</title>
<!-- defer обязателен: без него браузер держит отрисовку страницы, пока не
     скачает скрипт. У нашей аудитории telegram.org заблокирован и пакеты
     обычно дропаются молча, поэтому человек десятки секунд смотрел в пустое
     окно и решал, что панель сломана. Со defer страница рисуется сразу, а
     вход по коду не зависит от этого скрипта вовсе. -->
<script defer src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
 body{background:#12141a;color:#e6e8ee;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
      display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
 .card{background:#1b1e26;border:1px solid #2a2e3a;border-radius:14px;padding:28px;max-width:360px;width:92%}
 h1{font-size:18px;margin:0 0 6px}
 p{color:#9aa1b1;font-size:13px;margin:0 0 18px}
 input{width:100%;box-sizing:border-box;background:#12141a;border:1px solid #2f3442;color:#e6e8ee;
       border-radius:9px;padding:12px;font-size:20px;letter-spacing:5px;text-align:center}
 button{width:100%;margin-top:10px;background:#4b6bfb;color:#fff;border:0;border-radius:9px;
        padding:12px;font-size:15px;cursor:pointer}
 button.ghost{background:#252a36}
 pre{background:#12141a;border:1px solid #2f3442;border-radius:9px;padding:10px;
      font-size:12px;overflow-x:auto;color:#cfd4e0;white-space:pre-wrap;word-break:break-all}
 code{background:#12141a;border-radius:5px;padding:1px 5px;font-size:12px}
 .msg{margin-top:12px;font-size:13px;min-height:18px}
 .err{color:#ff8a8a} .ok{color:#7ee0a8}
</style></head><body>
<div class="card">
  <h1>Панель goTelegram</h1>
  <div id="tfa">
    <p>Код придёт в Telegram администратору. Действует 5 минут.</p>
    <button id="req" class="ghost">Прислать код</button>
    <div style="height:10px"></div>
    <input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000">
    <button id="go">Войти</button>
  </div>
  <div id="nofa" style="display:none">
    <p>Вход по коду недоступен: Telegram-бот не установлен или в его настройках
       нет администраторов. Войдите по токену через SSH-туннель:</p>
    <pre id="tun">ssh -L __ADMIN_PORT__:127.0.0.1:__ADMIN_PORT__ root@SERVER_IP</pre>
    <p>Токен: <code>cat /opt/gotelegram-admin/token</code> — затем откройте
       <code>http://127.0.0.1:__ADMIN_PORT__/?token=…</code></p>
  </div>
  <div class="msg" id="msg"></div>
</div>
<script>
const TFA_OK=__TFA_AVAILABLE__;
if(!TFA_OK){document.getElementById('tfa').style.display='none';
            document.getElementById('nofa').style.display='';}
const msg=document.getElementById('msg');
function show(t,cls){msg.textContent=t;msg.className='msg '+(cls||'');}
// Страница живёт под секретным путём мини-аппа, поэтому абсолютный '/api/...'
// уходил на корень домена (сайт-маскировку) и возвращал 404 — кнопка «Прислать
// код» отвечала «сервер ответил не по-человечески». Клеим путь текущей страницы.
const BASE=location.pathname.replace(/\/[^/]*$/,'').replace(/\/$/,'');
async function post(url,body){
  const r=await fetch(BASE+url,{method:'POST',headers:{'Content-Type':'application/json',
    'X-GoTelegram-Admin':'1'},body:JSON.stringify(body||{})});
  return r.json().catch(()=>({ok:false,error:'сервер ответил не по-человечески'}));
}
// Открыли из Telegram — подпись уже есть, спрашивать код незачем.
// Ждём DOMContentLoaded: defer-скрипты выполняются до него, поэтому
// window.Telegram уже на месте. Раньше объект читался сразу и при defer
// был бы пустым.
window.addEventListener('DOMContentLoaded',()=>{(async()=>{
  try{
    const wa=window.Telegram&&window.Telegram.WebApp;
    if(!wa||!wa.initData) return;
    wa.ready();
    show('Вхожу через Telegram…');
    const r=await post('/api/auth/miniapp',{init_data:wa.initData});
    if(r.ok){
      show('Готово, входим…','ok');
      location.replace(BASE+'/'+(r.ticket?('?s='+encodeURIComponent(r.ticket)):''));
      return;
    }
    show(r.error||'подпись не подошла — введите код','err');
  }catch(e){/* обычный браузер: остаётся вход по коду */}
})();});
document.getElementById('req').onclick=async()=>{
  show('Отправляю…');
  const r=await post('/api/auth/request');
  show(r.ok?'Код отправлен в Telegram':(r.error||'не вышло'), r.ok?'ok':'err');
};
document.getElementById('go').onclick=async()=>{
  const code=document.getElementById('code').value.trim();
  if(!/^[0-9]{6}$/.test(code)){show('Нужны шесть цифр','err');return;}
  show('Проверяю…');
  const r=await post('/api/auth/verify',{code});
  if(r.ok){show('Готово, входим…','ok');location.reload();}
  else show(r.error||'неверный код','err');
};
document.getElementById('code').addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('go').click();});
</script></body></html>"""


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("w", encoding="utf-8")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.handle:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def load_json(path: Path, fallback: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return fallback


def _fsync_dir(path: Path) -> None:
    # fsync каталога после rename: без него при обрыве питания сам rename мог не доехать до диска.
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_json(path: Path, data: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # flush+fsync до rename: иначе rename мог доехать раньше данных и оставить пустой файл.
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=4) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    tmp.replace(path)
    _fsync_dir(path.parent)


def read_language(config: dict[str, Any] | None = None) -> str:
    config = config or load_json(GOTELEGRAM_CONFIG, {}) or {}
    lang = str(config.get("language") or config.get("lang") or "").strip().lower()
    marker = INSTALL_DIR / ".language"
    if lang not in {"en", "ru"} and marker.exists():
        try:
            lang = marker.read_text(encoding="utf-8", errors="ignore").strip().lower()[:2]
        except OSError:
            lang = ""
    return lang if lang in {"en", "ru"} else "en"


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if key not in SENSITIVE_CONFIG_KEYS}


def write_language(lang: str) -> dict[str, Any]:
    lang = str(lang or "").strip().lower()
    if not LANG_RE.match(lang):
        raise ValueError("unsupported language")
    with FileLock(CONFIG_JSON_LOCK):
        config = load_json(GOTELEGRAM_CONFIG, {}) or {}
        if not isinstance(config, dict):
            config = {}
        config["language"] = lang
        config["updated_at"] = utc_now()
        save_json(GOTELEGRAM_CONFIG, config)
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    (INSTALL_DIR / ".language").write_text(lang + "\n", encoding="utf-8")
    bot_env = BOT_DIR / ".env"
    if bot_env.exists():
        lines = bot_env.read_text(encoding="utf-8", errors="ignore").splitlines()
        found = False
        out = []
        for line in lines:
            if line.startswith("BOT_LANG="):
                out.append(f"BOT_LANG={lang}")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"BOT_LANG={lang}")
        bot_env.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
        os.chmod(bot_env, 0o600)
    return {"language": lang}


def read_telemt_users() -> dict[str, str]:
    if not TELEMT_CONFIG.exists():
        return {}
    users: dict[str, str] = {}
    in_users = False
    for raw in TELEMT_CONFIG.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line == "[access.users]":
            in_users = True
            continue
        if in_users and line.startswith("["):
            break
        if not in_users or not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = parse_toml_key(name)
        value = value.strip().split("#", 1)[0].strip()
        if value.startswith('"') and '"' in value[1:]:
            value = value[1:].split('"', 1)[0]
        elif value.startswith("'") and "'" in value[1:]:
            value = value[1:].split("'", 1)[0]
        if USER_RE.match(name) and value:
            users[name] = value
    return users


def read_toml_int_table(table: str) -> dict[str, int]:
    if not TELEMT_CONFIG.exists():
        return {}
    values: dict[str, int] = {}
    section = f"[{table}]"
    in_table = False
    for raw in TELEMT_CONFIG.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line == section:
            in_table = True
            continue
        if in_table and line.startswith("["):
            break
        if not in_table or not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = parse_toml_key(name)
        if not USER_RE.match(name):
            continue
        raw_value = value.strip().split("#", 1)[0].strip().strip('"').strip("'")
        try:
            number = int(raw_value)
        except ValueError:
            continue
        values[name] = max(0, number)
    return values


def read_user_max_unique_ips() -> dict[str, int]:
    return read_toml_int_table("access.user_max_unique_ips")


def read_disabled_users() -> dict[str, str]:
    raw = load_json(DISABLED_USERS_FILE, {}) or {}
    if not isinstance(raw, dict):
        return {}
    users = raw.get("users") if isinstance(raw.get("users"), dict) else raw
    if not isinstance(users, dict):
        return {}
    clean: dict[str, str] = {}
    for name, secret in users.items():
        if name in {"version", "updated_at"}:
            continue
        name_s = str(name).strip()
        secret_s = str(secret or "").strip()
        if USER_RE.match(name_s) and secret_s:
            clean[name_s] = secret_s
    return clean


def write_disabled_users(users: dict[str, str]) -> None:
    # Имена, не прошедшие USER_RE (правка руками, другая версия), панель не
    # видит — без переноса из старого файла перезапись молча теряла их секреты.
    raw = load_json(DISABLED_USERS_FILE, {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    stored = raw.get("users") if isinstance(raw.get("users"), dict) else raw
    merged = dict(users)
    for name, secret in stored.items():
        name_s = str(name).strip()
        secret_s = str(secret or "").strip()
        if name_s in {"version", "updated_at"} or name_s in merged:
            continue
        if not USER_RE.match(name_s) and secret_s:
            merged[name_s] = secret_s
    payload = {
        "version": 1,
        "updated_at": utc_now(),
        "users": {name: merged[name] for name in sorted(merged)},
    }
    save_json(DISABLED_USERS_FILE, payload)


def read_user_records() -> dict[str, dict[str, Any]]:
    active = read_telemt_users()
    disabled = read_disabled_users()
    ip_limits = read_user_max_unique_ips()
    records: dict[str, dict[str, Any]] = {}
    for name, secret in disabled.items():
        records[name] = {"secret": secret, "enabled": False, "max_unique_ips": ip_limits.get(name, 0)}
    for name, secret in active.items():
        records[name] = {"secret": secret, "enabled": True, "max_unique_ips": ip_limits.get(name, 0)}
    return records


def _ordered_user_lines(users: dict[str, str]) -> list[str]:
    names = []
    if "main" in users:
        names.append("main")
    names.extend(sorted(n for n in users if n != "main"))
    return [f'{quote_toml_key(name)} = "{users[name]}"' for name in names]


def _ordered_user_int_lines(values: dict[str, int]) -> list[str]:
    positive: dict[str, int] = {}
    for name, value in values.items():
        name_s = str(name)
        if not USER_RE.match(name_s):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            positive[name_s] = number
    names = []
    if "main" in positive:
        names.append("main")
    names.extend(sorted(n for n in positive if n != "main"))
    return [f'{quote_toml_key(name)} = {positive[name]}' for name in names]


def parse_toml_key(raw: str) -> str:
    key = raw.strip()
    if len(key) >= 2 and key[0] == key[-1] == '"':
        try:
            return json.loads(key)
        except json.JSONDecodeError:
            return key[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(key) >= 2 and key[0] == key[-1] == "'":
        return key[1:-1]
    return key


def quote_toml_key(name: str) -> str:
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _foreign_section_lines(lines: list[str], header: str) -> list[str]:
    """Строки секции с именами, не прошедшими USER_RE (правка руками, другая версия).

    Панель такие имена не видит, и раньше любая операция с ключами молча
    выкидывала их при перезаписи секции — чужой ключ переставал пускать.
    """
    preserved: list[str] = []
    in_section = False
    for raw in lines:
        stripped = raw.strip()
        if stripped == header:
            in_section = True
            continue
        if in_section and stripped.startswith("["):
            in_section = False
        if not in_section or not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if not USER_RE.match(parse_toml_key(stripped.split("=", 1)[0])):
            preserved.append(raw)
    return preserved


WEB_SECRET_MODE = "dd"


def _web_profile_lines(user_lines: list[str]) -> list[str]:
    """Профили WEB — по одному на каждый ключ, тем же секретом.

    Ровно та же логика, что у telemt_web_sync_profiles в lib/telemt_config.sh
    и у бота: профиль, ссылающийся на удалённого пользователя, делает конфиг
    невалидным, и следующий перезапуск telemt оставил бы прокси лежать. Поэтому
    профили пересобираются в той же записи, что и сами ключи.
    """
    out: list[str] = []
    for raw in user_lines:
        m = re.match(r'^\s*"?([A-Za-z0-9_.-]+)"?\s*=', raw)
        if m:
            out += ["", "[[web.vhosts.profiles]]", f'user = "{m.group(1)}"', f'secret_mode = "{WEB_SECRET_MODE}"']
    return out


WEB_MAX_PROFILES = 4096


def _ensure_web_limits(lines):
    """[web.limits].max_profiles в конфиге, собранном до 2.10.46: у ядра по умолчанию
    32 профиля, профиль — на каждый ключ, и 33-й ключ ронял ядро при рестарте."""
    if not any(l.strip() == "[[web.vhosts]]" for l in lines) or any(l.strip() == "[web.limits]" for l in lines):
        return lines
    out = []
    done = False
    for l in lines:
        if not done and l.strip() == "[[web.vhosts]]":
            out.extend(["[web.limits]", f"max_profiles = {WEB_MAX_PROFILES}", ""])
            done = True
        out.append(l)
    return out


def _strip_web_profiles(lines: list[str]) -> list[str]:
    out: list[str] = []
    skip = False
    for raw in lines:
        stripped = raw.strip()
        if stripped == "[[web.vhosts.profiles]]":
            skip = True
            continue
        if skip and stripped.startswith("["):
            skip = False
        if skip:
            continue
        out.append(raw)
    while out and not out[-1].strip():
        out.pop()
    return out


def web_configured_in_core() -> bool:
    """WEB реально собран в конфиге ядра (есть WEB-listener), а не только включён флагом.

    Флаг мог остаться от ядра, которое WEB не умеет, — ссылка была бы мёртвой.
    """
    try:
        return '\ntransport = "web"' in ("\n" + TELEMT_CONFIG.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return False


def web_link(secret: str, config: dict[str, Any] | None = None) -> str:
    """WEB-ссылка (Telegram Desktop 7.1+): тот же секрет в представлении dd."""
    config = config if config is not None else (load_json(GOTELEGRAM_CONFIG, {}) or {})
    if not isinstance(config, dict) or not secret:
        return ""
    if str(config.get("web_enabled", "")).lower() != "true" or not web_configured_in_core():
        return ""
    domain = str(config.get("domain", "") or "")
    if str(config.get("mode", "lite")) != "pro" or not domain:
        return ""
    return f"tg://webproxy?server={domain}&secret=dd{secret}"


def write_telemt_users(users: dict[str, str]) -> None:
    TELEMT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    lines = TELEMT_CONFIG.read_text(encoding="utf-8", errors="ignore").splitlines() if TELEMT_CONFIG.exists() else []
    # Неизвестные панели имена сохраняем как есть, а не выкидываем при перезаписи.
    rendered = _ordered_user_lines(users) + _foreign_section_lines(lines, "[access.users]")
    out: list[str] = []
    in_users = False
    found = False

    for raw in lines:
        if raw.strip() == "[access.users]":
            found = True
            in_users = True
            out.append(raw)
            out.extend(rendered)
            continue
        if in_users and raw.strip().startswith("["):
            in_users = False
        if in_users:
            continue
        out.append(raw)

    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append("[access.users]")
        out.extend(rendered)

    # Ключи изменились — профили WEB обязаны им соответствовать
    out = _strip_web_profiles(out)
    if any(line.strip() == "[[web.vhosts]]" for line in out):
        out.extend(_web_profile_lines(rendered))
    out = _ensure_web_limits(out)

    tmp = TELEMT_CONFIG.with_name(TELEMT_CONFIG.name + ".tmp")
    # flush+fsync до rename: обрыв питания иначе мог оставить пустой config.toml — без единого ключа.
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(out).rstrip() + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    tmp.replace(TELEMT_CONFIG)
    _fsync_dir(TELEMT_CONFIG.parent)


def write_toml_int_table(table: str, values: dict[str, int]) -> None:
    TELEMT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    lines = TELEMT_CONFIG.read_text(encoding="utf-8", errors="ignore").splitlines() if TELEMT_CONFIG.exists() else []
    header = f"[{table}]"
    # Неизвестные панели имена сохраняем как есть, а не выкидываем при перезаписи.
    rendered = _ordered_user_int_lines(values) + _foreign_section_lines(lines, header)
    out: list[str] = []
    in_table = False
    found = False

    for raw in lines:
        if raw.strip() == header:
            found = True
            in_table = True
            if rendered:
                out.append(raw)
                out.extend(rendered)
            continue
        if in_table and raw.strip().startswith("["):
            in_table = False
        if in_table:
            continue
        out.append(raw)

    if not found and rendered:
        if out and out[-1].strip():
            out.append("")
        out.append(header)
        out.extend(rendered)

    tmp = TELEMT_CONFIG.with_name(TELEMT_CONFIG.name + ".tmp")
    # flush+fsync до rename: обрыв питания иначе мог оставить пустой config.toml — без единого ключа.
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(out).rstrip() + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    tmp.replace(TELEMT_CONFIG)
    _fsync_dir(TELEMT_CONFIG.parent)


def write_user_max_unique_ips(values: dict[str, int]) -> None:
    write_toml_int_table("access.user_max_unique_ips", values)


def normalize_max_unique_ips(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("max_unique_ips must be an integer") from None
    if number < 0 or number > MAX_UNIQUE_IP_LIMIT:
        raise ValueError(f"max_unique_ips must be between 0 and {MAX_UNIQUE_IP_LIMIT}")
    # Единица запрещена во всех трёх интерфейсах: телефон при смене Wi-Fi ↔ LTE меняет
    # адрес, старые соединения ещё живы, и клиент отбивается собственным лимитом.
    # 0 — снять персональный лимит, минимальное осмысленное значение — 2.
    if number == 1:
        raise ValueError(MAX_UNIQUE_IPS_MIN_MESSAGE)
    return number


def restart_service(name: str) -> bool:
    code, _, _ = run(["systemctl", "restart", name], timeout=25)
    if code != 0:
        return False
    if name == "telemt":
        return wait_tcp_port(read_telemt_port(), timeout=90)
    return True


def _telemt_deferred_restart(armed_last: float) -> None:
    """Отложенный одиночный рестарт: подхватывает все правки, попавшие в окно дебаунса."""
    global _LAST_TELEMT_RESTART, _TELEMT_RESTART_TIMER
    with _TELEMT_RESTART_LOCK:
        _TELEMT_RESTART_TIMER = None
        # Пока таймер ждал, кто-то уже рестартовал заново — конфиг перечитан, второй рестарт лишний.
        if _LAST_TELEMT_RESTART != armed_last:
            return
        run(["systemctl", "reset-failed", "telemt"], timeout=5)
        _LAST_TELEMT_RESTART = time.monotonic()
        run(["systemctl", "--no-block", "restart", "telemt"], timeout=5)


def request_service_restart(name: str) -> bool:
    global _LAST_TELEMT_RESTART, _TELEMT_RESTART_TIMER
    if name == "telemt":
        with _TELEMT_RESTART_LOCK:
            now = time.monotonic()
            remaining = TELEMT_RESTART_DEBOUNCE_SECONDS - (now - _LAST_TELEMT_RESTART)
            if _LAST_TELEMT_RESTART > 0 and remaining > 0:
                status = service_status(name)
                if status in {"running", "activating"}:
                    # Раньше здесь был голый return True без рестарта: config.toml уже
                    # переписан, а telemt его не перечитал — свежий ключ мёртв,
                    # отозванный продолжал пускать. Коалесцируем: один отложенный
                    # рестарт на остаток окна вместо потери правки.
                    if _TELEMT_RESTART_TIMER is not None:
                        return True
                    timer = threading.Timer(remaining, _telemt_deferred_restart,
                                            args=(_LAST_TELEMT_RESTART,))
                    timer.daemon = True
                    try:
                        timer.start()
                    except RuntimeError:
                        pass  # поток не создался — ниже рестартуем немедленно, лишь бы не потерять правку
                    else:
                        _TELEMT_RESTART_TIMER = timer
                        return True
            run(["systemctl", "reset-failed", name], timeout=5)
            _LAST_TELEMT_RESTART = now
            code, _, _ = run(["systemctl", "--no-block", "restart", name], timeout=5)
            return code == 0
    code, _, _ = run(["systemctl", "--no-block", "restart", name], timeout=5)
    return code == 0


def service_status(name: str) -> str:
    code, stdout, _ = run(["systemctl", "is-active", name], timeout=3)
    value = stdout.strip()
    if code == 0 and value == "active":
        return "running"
    code, stdout, _ = run(["systemctl", "list-unit-files", f"{name}.service", "--no-legend"], timeout=3)
    if code != 0 or not stdout.strip():
        return "not_installed"
    if value in {"failed", "inactive", "activating", "deactivating"}:
        return value
    return "stopped"


def read_telemt_port() -> int:
    if not TELEMT_CONFIG.exists():
        return 443
    in_server = False
    for raw in TELEMT_CONFIG.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line == "[server]":
            in_server = True
            continue
        if in_server and line.startswith("["):
            break
        if in_server and line.startswith("port") and "=" in line:
            try:
                return int(line.split("=", 1)[1].strip().split("#", 1)[0])
            except ValueError:
                return 443
    return 443


def _is_port_addr(value: str, port: int) -> bool:
    token = value.strip()
    if token.startswith("[") and "]:" in token:
        return token.rsplit(":", 1)[-1] == str(port)
    return token.rsplit(":", 1)[-1] == str(port) if ":" in token else False


def _process_role(process: str) -> str:
    lowered = process.lower()
    if "telemt" in lowered or "mtproto" in lowered:
        return "mtproxy"
    if "nginx" in lowered or "apache" in lowered or "caddy" in lowered:
        return "site"
    if "xray" in lowered or "x-ui" in lowered or "3x-ui" in lowered or "xui" in lowered:
        return "xray"
    if "amnezia" in lowered or "awg" in lowered or "wireguard" in lowered or re.search(r"\bwg\b", lowered):
        return "amneziawg"
    return "other"


def parse_ss_listeners(output: str, proto: str, port: int = 443) -> list[dict[str, Any]]:
    listeners: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for line in output.splitlines():
        parts = line.split()
        address = next((part for part in parts if _is_port_addr(part, port)), "")
        if not address:
            continue
        matches = re.findall(r'\("([^"]+)",pid=(\d+)', line)
        if matches:
            process_names = []
            pids = []
            for proc, pid in matches:
                if proc not in process_names:
                    process_names.append(proc)
                if pid not in pids:
                    pids.append(pid)
            process = ", ".join(process_names)
            pid_text = ", ".join(pids)
        else:
            process = "unknown"
            pid_text = ""
        key = (proto, address, process)
        if key in seen:
            continue
        seen.add(key)
        listeners.append({
            "proto": proto.upper(),
            "address": address,
            "process": process,
            "pid": pid_text,
            "role": _process_role(process),
        })
    return listeners


def collect_port_listeners(port: int) -> tuple[list[dict[str, Any]], list[str]]:
    listeners: list[dict[str, Any]] = []
    errors: list[str] = []
    for proto, args in {
        "tcp": ["ss", "-H", "-ltnp"],
        "udp": ["ss", "-H", "-lunp"],
    }.items():
        code, stdout, stderr = run(args, timeout=2)
        if code == 0:
            listeners.extend(parse_ss_listeners(stdout, proto, port))
        elif stderr.strip():
            errors.append(stderr.strip())
    listeners.sort(key=lambda item: (item["proto"], item["address"], item["process"]))
    return listeners, errors


def read_telemt_edge_settings() -> dict[str, Any]:
    settings: dict[str, Any] = {"tls_domain": "", "mask_port": 0, "dns_overrides": []}
    if not TELEMT_CONFIG.exists():
        return settings
    section = ""
    for raw in TELEMT_CONFIG.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]")
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().split("#", 1)[0].strip()
        if section == "censorship" and key == "tls_domain":
            settings["tls_domain"] = value.strip('"').strip("'")
        elif section == "censorship" and key == "mask_port":
            try:
                settings["mask_port"] = int(value)
            except ValueError:
                settings["mask_port"] = 0
        elif section == "network" and key == "dns_overrides":
            settings["dns_overrides"] = re.findall(r'"([^"]+)"', value)
    return settings


def load_shared443_config() -> dict[str, Any]:
    raw = load_json(SHARED_443_CONFIG, {}) or {}
    if not isinstance(raw, dict):
        return {}
    routes = raw.get("xray_routes") if isinstance(raw.get("xray_routes"), list) else []
    clean_routes = []
    for item in routes:
        if not isinstance(item, dict):
            continue
        public = str(item.get("public") or item.get("domain") or "").strip()
        target = str(item.get("target") or "").strip()
        if public and target:
            clean_routes.append({"public": public, "target": target})
    return {
        "enabled": bool(raw.get("enabled")),
        "dispatcher": str(raw.get("dispatcher") or "nginx-stream"),
        "public_port": _int_value(raw.get("public_port") or 443) or 443,
        "telemt_target": str(raw.get("telemt_target") or "127.0.0.1:7443"),
        "site_target": str(raw.get("site_target") or ""),
        "xray_routes": clean_routes,
        "updated_at": str(raw.get("updated_at") or ""),
    }


def listener_for_target(target: str) -> dict[str, Any] | None:
    try:
        port = int(target.rsplit(":", 1)[-1])
    except ValueError:
        return None
    listeners, _ = collect_port_listeners(port)
    return listeners[0] if listeners else None


def routed_behind_443() -> list[dict[str, Any]]:
    config = load_json(GOTELEGRAM_CONFIG, {}) or {}
    mode = str(config.get("mode") or "")
    domain = str(config.get("domain") or "")
    settings = read_telemt_edge_settings()
    shared = load_shared443_config()
    mask_port = int(settings.get("mask_port") or 0)
    tls_domain = str(settings.get("tls_domain") or domain)
    routes: list[dict[str, Any]] = []
    if shared.get("enabled"):
        telemt_target = str(shared.get("telemt_target") or "127.0.0.1:7443")
        telemt_listener = listener_for_target(telemt_target)
        routes.append({
            "role": "mtproxy",
            "proto": "MTProxy",
            "public": f"{domain or tls_domain or 'default'}:443",
            "target": telemt_target,
            "process": (telemt_listener or {}).get("process") or "telemt",
            "pid": (telemt_listener or {}).get("pid") or "",
            "status": service_status("telemt"),
            "via": "nginx stream ssl_preread",
            "tls_domain": tls_domain,
            "details": ["default -> telemt"] if not shared.get("xray_routes") else [],
        })
        for item in shared.get("xray_routes", []):
            target = item.get("target", "")
            listener = listener_for_target(target)
            public = item.get("public", "")
            if public and ":" not in public:
                public = f"{public}:443"
            routes.append({
                "role": "xray",
                "proto": "VLESS",
                "public": public or "xray:443",
                "target": target,
                "process": (listener or {}).get("process") or "xray",
                "pid": (listener or {}).get("pid") or "",
                "status": "running" if listener else "not_installed",
                "via": "nginx stream ssl_preread",
                "tls_domain": public.split(":", 1)[0] if public else "",
                "details": [],
            })
    if mode == "pro" and domain and mask_port and mask_port != 443:
        internal, _ = collect_port_listeners(mask_port)
        site_listener = next((item for item in internal if item.get("role") == "site"), None)
        routes.append({
            "role": "site",
            "proto": "HTTPS",
            "public": f"{domain}:443",
            "target": f"127.0.0.1:{mask_port}",
            "process": (site_listener or {}).get("process") or "nginx",
            "pid": (site_listener or {}).get("pid") or "",
            "status": service_status("nginx"),
            "via": "telemt dns_overrides",
            "tls_domain": tls_domain,
            "details": settings.get("dns_overrides") or [],
        })
    return routes


def edge_ips_status() -> dict[str, Any]:
    """Распределение клиентов по внешним адресам сервера.

    Пустой ответ — норма: на машине с одним адресом модуль молчит, и панель
    просто не показывает карточку.
    """
    try:
        out = subprocess.run(
            ["bash", str(INSTALL_DIR / "install.sh"), "--action=edgeips-status", "--json"],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
    except Exception as exc:
        sys.stderr.write(f"edge_ips_status: {exc}\n")
    return {"ips": [], "count": 0}


def port_443_status() -> dict[str, Any]:
    listeners, errors = collect_port_listeners(443)
    shared = load_shared443_config()
    if shared.get("enabled"):
        for item in listeners:
            if item.get("role") == "site" and "nginx" in str(item.get("process", "")).lower():
                item["role"] = "edge"
                item["details"] = "nginx stream ssl_preread"
    return {
        "checked_at": int(time.time()),
        "configured_port": read_telemt_port(),
        "listeners": listeners,
        "routes": routed_behind_443(),
        "shared_443": shared,
        "ok": not errors,
        "error": "; ".join(errors[:2]),
    }


def wait_tcp_port(port: int, timeout: int = 90) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service_status("telemt") not in {"running", "activating"}:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.6):
                return True
        except OSError:
            time.sleep(1)
    return False


def _is_public_ipv4(value: str) -> bool:
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", value):
        return False
    parts = [int(part) for part in value.split(".")]
    if any(part > 255 for part in parts):
        return False
    first, second = parts[0], parts[1]
    if first in {0, 10, 127} or first >= 224:
        return False
    if first == 172 and 16 <= second <= 31:
        return False
    if first == 192 and second == 168:
        return False
    if first == 169 and second == 254:
        return False
    if first == 100 and 64 <= second <= 127:
        return False
    return True


def public_ip(force: bool = False) -> str:
    """Public IPv4 of this host, cached for PUBLIC_IP_TTL seconds.

    proxy_link() is called once per key and /api/users is polled every 5s, so
    the uncached version fired one curl to api.ipify.org per key per poll.
    Once rate-limited it fell back to `hostname -I`, which put a PRIVATE
    address into the tg:// links. Now the last good value is kept instead.
    """
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHES.get("public_ip")
        previous = entry[1] if entry else ""
        if entry and not force and now < entry[0]:
            return previous

    value = ""
    code, stdout, _ = run(["curl", "-s", "-4", "--max-time", "3", "https://api.ipify.org"], timeout=5)
    candidate = stdout.strip()
    if code == 0 and _is_public_ipv4(candidate):
        value = candidate
    if not value:
        code, stdout, _ = run(["hostname", "-I"], timeout=3)
        if code == 0:
            for token in stdout.split():
                if _is_public_ipv4(token):
                    value = token
                    break

    ttl = PUBLIC_IP_TTL
    if not value:
        # Never downgrade a working link to a private/unknown address.
        value = previous
        ttl = PUBLIC_IP_RETRY_TTL
    if not value:
        code, stdout, _ = run(["hostname", "-I"], timeout=3)
        parts = stdout.split()
        value = parts[0] if code == 0 and parts else "0.0.0.0"
        ttl = PUBLIC_IP_RETRY_TTL

    with _CACHE_LOCK:
        _CACHES["public_ip"] = (time.monotonic() + ttl, value)
    return value


def proxy_link(secret: str, config: dict[str, Any] | None = None) -> str:
    config = config if config is not None else (load_json(GOTELEGRAM_CONFIG, {}) or {})
    if not isinstance(config, dict):
        config = {}
    mode = str(config.get("mode", "lite"))
    try:
        port = int(config.get("port", 443) or 443)
    except (TypeError, ValueError):
        port = 443
    domain = str(config.get("domain", "") or "")
    mask_host = str(config.get("mask_host", "") or "")

    if mode == "pro" and domain:
        host_hex = domain.encode().hex()
        return f"tg://proxy?server={domain}&port={port}&secret=ee{secret}{host_hex}"

    server = public_ip()
    # Пустой mask_host в config.json (старые установки) давал ссылку без ee —
    # ядро в fake-TLS её отвергает. Источник правды — tls_domain конфига ядра.
    mask_host = mask_host or read_tls_domain() or domain
    if mask_host:
        host_hex = mask_host.encode().hex()
        return f"tg://proxy?server={server}&port={port}&secret=ee{secret}{host_hex}"
    return f"tg://proxy?server={server}&port={port}&secret={secret}"


def read_tls_domain() -> str:
    """[censorship] tls_domain из конфига ядра."""
    try:
        in_cens = False
        with open(TELEMT_CONFIG, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if s.startswith("["):
                    in_cens = (s == "[censorship]")
                    continue
                if in_cens and s.startswith("tls_domain"):
                    m = re.match(r'tls_domain\s*=\s*"([^"]*)"', s)
                    return m.group(1).strip() if m else ""
    except OSError:
        pass
    return ""


def telemt_api(path: str) -> Any:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:9091{path}", timeout=1.8) as resp:
            payload = resp.read(256 * 1024)
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return None


def site_status(config: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    """Masquerade-site reachability, cached for SITE_STATUS_TTL seconds.

    The curl below may block for 8 seconds and used to run inside every
    /api/overview, i.e. every 5 seconds. In lite mode there is no site to
    check at all.
    """
    config = config if config is not None else (load_json(GOTELEGRAM_CONFIG, {}) or {})
    if not isinstance(config, dict):
        config = {}
    host = str(config.get("domain") or "").strip()
    mode = str(config.get("mode") or "lite").strip().lower()
    if not host:
        return {"host": "", "url": "", "http_code": 0, "ok": False, "checked": False, "error": "domain_missing"}
    if not re.match(r"^[A-Za-z0-9.-]{1,253}$", host) or ".." in host or host.startswith(".") or host.endswith("."):
        return {"host": host, "url": "", "http_code": 0, "ok": False, "checked": False, "error": "invalid_domain"}
    url = f"https://{host}/"
    if mode != "pro":
        return {"host": host, "url": url, "http_code": 0, "ok": False, "checked": False, "error": ""}

    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHES.get("site_status")
        if entry and not force and entry[0][0] == host and now < entry[0][1]:
            return dict(entry[1])

    code, stdout, stderr = run(["curl", "-k", "-L", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "8", url], timeout=10)
    raw_code = stdout.strip()
    try:
        http_code = int(raw_code)
    except ValueError:
        http_code = 0
    payload = {
        "host": host,
        "url": url,
        "http_code": http_code,
        "ok": code == 0 and http_code == 200,
        "checked": True,
        "error": "" if code == 0 else (stderr.strip() or f"curl exit {code}"),
        "checked_at": int(time.time()),
    }
    with _CACHE_LOCK:
        _CACHES["site_status"] = ((host, time.monotonic() + SITE_STATUS_TTL), payload)
    return dict(payload)


def load_stats_history(limit: int | None = 240) -> list[dict[str, int]]:
    stamp = _stat_key(HISTORY_FILE)
    if stamp is None:
        return []
    cache_key = f"stats_history:{limit}"
    cached = _cache_lookup(cache_key, stamp)
    if cached is not _MISS:
        return cached

    rows: list[dict[str, int]] = []
    for row in read_csv_tail(HISTORY_FILE, _tail_budget(limit)):
        try:
            rows.append({
                "epoch": int(row.get("epoch") or 0),
                "proxy_bytes": int(row.get("proxy_bytes") or 0),
                "site_bytes": int(row.get("site_bytes") or 0),
            })
        except (TypeError, ValueError):
            continue
    if limit:
        rows = rows[-limit:]
    previous = None
    enriched: list[dict[str, int]] = []
    for row in rows:
        item = dict(row)
        if previous:
            item["proxy_delta"] = max(0, row["proxy_bytes"] - previous["proxy_bytes"])
            item["site_delta"] = max(0, row["site_bytes"] - previous["site_bytes"])
        else:
            item["proxy_delta"] = 0
            item["site_delta"] = 0
        enriched.append(item)
        previous = row
    return _cache_store(cache_key, stamp, enriched)


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def load_user_stats_history(name: str | None = None, limit: int | None = 240) -> list[dict[str, Any]]:
    stamp = _stat_key(USER_HISTORY_FILE)
    if stamp is None:
        return []
    cache_key = f"user_stats_history:{name}:{limit}"
    cached = _cache_lookup(cache_key, stamp)
    if cached is not _MISS:
        return cached

    # Rows of all keys are interleaved, so a per-user window needs a much
    # bigger slice of the file than `limit` rows.
    budget = _tail_budget(limit if limit is None else limit * 8)
    rows: list[dict[str, Any]] = []
    for row in read_csv_tail(USER_HISTORY_FILE, budget):
        user = str(row.get("user") or "").strip()
        if name is not None and user != name:
            continue
        if not USER_RE.match(user):
            continue
        rows.append({
            "epoch": _int_value(row.get("epoch")),
            "user": user,
            "total_octets": _int_value(row.get("total_octets")),
            "current_connections": _int_value(row.get("current_connections")),
            "active_unique_ips": _int_value(row.get("active_unique_ips")),
            "recent_unique_ips": _int_value(row.get("recent_unique_ips")),
        })
    rows.sort(key=lambda item: (item["user"], item["epoch"]))
    if limit and name is not None:
        rows = rows[-limit:]

    previous_by_user: dict[str, dict[str, Any]] = {}
    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        previous = previous_by_user.get(row["user"])
        item["total_delta"] = max(0, row["total_octets"] - previous["total_octets"]) if previous else 0
        enriched.append(item)
        previous_by_user[row["user"]] = row
    if limit and name is None:
        enriched = enriched[-limit:]
    return _cache_store(cache_key, stamp, enriched)


def latest_user_stats() -> dict[str, dict[str, Any]]:
    stamp = _stat_key(USER_HISTORY_FILE)
    if stamp is None:
        return {}
    cached = _cache_lookup("latest_user_stats", stamp)
    if cached is not _MISS:
        return cached

    latest: dict[str, dict[str, Any]] = {}
    for row in read_csv_tail(USER_HISTORY_FILE, CSV_TAIL_MAX_BYTES):
        user = str(row.get("user") or "").strip()
        if not USER_RE.match(user):
            continue
        item = {
            "epoch": _int_value(row.get("epoch")),
            "user": user,
            "total_octets": _int_value(row.get("total_octets")),
            "current_connections": _int_value(row.get("current_connections")),
            "active_unique_ips": _int_value(row.get("active_unique_ips")),
            "recent_unique_ips": _int_value(row.get("recent_unique_ips")),
        }
        if item["epoch"] >= latest.get(user, {}).get("epoch", 0):
            latest[user] = item
    return _cache_store("latest_user_stats", stamp, latest)


def runtime_user_traffic(name: str, enabled: bool = True) -> dict[str, Any]:
    if not enabled:
        return {"ok": False, "enabled": False, "total_octets": 0, "current_connections": 0, "active_unique_ips": 0, "recent_unique_ips": 0}
    payload = telemt_api(f"/v1/users/{urllib.parse.quote(name, safe='')}")
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = {}
    return {
        "ok": bool(payload),
        "enabled": True,
        "total_octets": _int_value(data.get("total_octets")),
        "current_connections": _int_value(data.get("current_connections")),
        "active_unique_ips": _int_value(data.get("active_unique_ips")),
        "recent_unique_ips": _int_value(data.get("recent_unique_ips")),
        "in_runtime": bool(data.get("in_runtime")) if data else False,
    }


def current_user_traffic_snapshot(
    name: str,
    enabled: bool,
    history_snapshot: dict[str, Any] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Return live counters for key cards, preserving only total bytes from history.

    History rows are minute snapshots. They are useful for charts, but stale
    connection/IP values make the keys list look like users are still online.
    """
    history_snapshot = history_snapshot or {}
    fallback = {
        "epoch": _int_value(history_snapshot.get("epoch")),
        "total_octets": _int_value(history_snapshot.get("total_octets")),
        "current_connections": 0,
        "active_unique_ips": 0,
        "recent_unique_ips": 0,
    }
    if not enabled:
        return fallback
    runtime = runtime_user_traffic(name, enabled)
    if not runtime.get("ok"):
        return fallback
    return {
        "epoch": _int_value(now if now is not None else time.time()),
        "total_octets": _int_value(runtime.get("total_octets")),
        "current_connections": _int_value(runtime.get("current_connections")),
        "active_unique_ips": _int_value(runtime.get("active_unique_ips")),
        "recent_unique_ips": _int_value(runtime.get("recent_unique_ips")),
    }


def history_limit_for_range(range_key: str) -> int:
    return {
        "15m": 180,
        "1h": 240,
        "24h": 1800,
        "month": 50000,
    }.get(range_key, 240)


def normalize_range(range_key: str) -> str:
    return range_key if range_key in TRAFFIC_WINDOWS else "1h"


def filter_history_by_range(rows: list[dict[str, int]], range_key: str) -> list[dict[str, int]]:
    if not rows:
        return []
    seconds = TRAFFIC_WINDOWS[normalize_range(range_key)]
    latest = max(row.get("epoch", 0) for row in rows)
    cutoff = latest - seconds
    return [row for row in rows if row.get("epoch", 0) >= cutoff]


def traffic_interval_summaries(rows: list[dict[str, int]]) -> list[dict[str, Any]]:
    if not rows:
        return [
            {"range": key, "points": 0, "from": 0, "to": 0, "proxy_delta": 0, "site_delta": 0, "proxy_total": 0, "site_total": 0}
            for key in TRAFFIC_WINDOWS
        ]
    latest = max(row.get("epoch", 0) for row in rows)
    summaries = []
    for key, seconds in TRAFFIC_WINDOWS.items():
        window = [row for row in rows if row.get("epoch", 0) >= latest - seconds]
        if not window:
            summaries.append({"range": key, "points": 0, "from": 0, "to": latest, "proxy_delta": 0, "site_delta": 0, "proxy_total": 0, "site_total": 0})
            continue
        first = window[0]
        last = window[-1]
        summaries.append({
            "range": key,
            "points": len(window),
            "from": first.get("epoch", 0),
            "to": last.get("epoch", 0),
            "proxy_delta": sum(max(0, int(item.get("proxy_delta", 0))) for item in window),
            "site_delta": sum(max(0, int(item.get("site_delta", 0))) for item in window),
            "proxy_total": int(last.get("proxy_bytes", 0)),
            "site_total": int(last.get("site_bytes", 0)),
        })
    return summaries


def user_traffic_interval_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return [
            {"range": key, "points": 0, "from": 0, "to": 0, "total_delta": 0, "total_octets": 0}
            for key in TRAFFIC_WINDOWS
        ]
    latest = max(row.get("epoch", 0) for row in rows)
    summaries = []
    for key, seconds in TRAFFIC_WINDOWS.items():
        window = [row for row in rows if row.get("epoch", 0) >= latest - seconds]
        if not window:
            summaries.append({"range": key, "points": 0, "from": 0, "to": latest, "total_delta": 0, "total_octets": 0})
            continue
        first = window[0]
        last = window[-1]
        summaries.append({
            "range": key,
            "points": len(window),
            "from": first.get("epoch", 0),
            "to": last.get("epoch", 0),
            "total_delta": sum(max(0, int(item.get("total_delta", 0))) for item in window),
            "total_octets": int(last.get("total_octets", 0)),
        })
    return summaries


def count_history_rows() -> int:
    stamp = _stat_key(HISTORY_FILE)
    if stamp is None:
        return 0
    cached = _cache_lookup("count_history_rows", stamp)
    if cached is not _MISS:
        return cached
    try:
        with HISTORY_FILE.open("r", encoding="utf-8", errors="ignore") as fh:
            total = sum(1 for line in fh if line and line[0].isdigit())
    except OSError:
        return 0
    return _cache_store("count_history_rows", stamp, total)


def count_user_history_rows(name: str | None = None) -> int:
    stamp = _stat_key(USER_HISTORY_FILE)
    if stamp is None:
        return 0
    cache_key = f"count_user_history_rows:{name}"
    cached = _cache_lookup(cache_key, stamp)
    if cached is not _MISS:
        return cached
    try:
        with USER_HISTORY_FILE.open("r", encoding="utf-8", errors="ignore") as fh:
            if name is None:
                total = sum(1 for line in fh if line and line[0].isdigit())
            else:
                needle = f",{name},"
                total = sum(1 for line in fh if line and line[0].isdigit() and needle in line)
    except OSError:
        return 0
    return _cache_store(cache_key, stamp, total)


def stats_status(current: dict[str, Any] | None = None, history: list[dict[str, int]] | None = None) -> dict[str, Any]:
    current = current if current is not None else (load_json(CURRENT_STATS, {}) or {})
    history = history if history is not None else load_stats_history(limit=2)
    service = service_status("gotelegram-stats")
    now = int(time.time())
    ts = int(current.get("ts") or 0) if isinstance(current, dict) else 0
    age = max(0, now - ts) if ts else None
    error = str(current.get("error") or "") if isinstance(current, dict) else ""
    history_rows = count_history_rows()
    if error:
        health = "error"
    elif service == "running" and current and age is not None and age <= 180:
        health = "ok"
    elif service == "running":
        health = "stale"
    elif service == "not_installed":
        health = "not_installed"
    else:
        health = "stopped"
    return {
        "health": health,
        "service": service,
        "current_exists": CURRENT_STATS.exists(),
        "history_exists": HISTORY_FILE.exists(),
        "history_rows": history_rows,
        "history_points": len(history or []),
        "last_ts": ts,
        "age_seconds": age,
        "error": error,
    }


def run_stats_action(action: str) -> tuple[bool, str, dict[str, Any]]:
    if action == "repair":
        body = (
            "source /opt/gotelegram/lib/common.sh; "
            "source /opt/gotelegram/lib/i18n.sh; "
            "source /opt/gotelegram/lib/stats.sh; "
            "load_language \"$(detect_language 2>/dev/null || echo en)\"; "
            "install_stats_collector; "
            "stats_collect"
        )
        timeout = 180
    else:
        body = (
            "source /opt/gotelegram/lib/common.sh; "
            "source /opt/gotelegram/lib/stats.sh; "
            "stats_init >/dev/null 2>&1 || true; "
            "stats_collect"
        )
        timeout = 30
    code, stdout, stderr = run(["bash", "-lc", body], timeout=timeout)
    message = (stdout.strip().splitlines()[-1:] or stderr.strip().splitlines()[-1:] or [""])[0]
    current = load_json(CURRENT_STATS, {}) or {}
    history = load_stats_history()
    return code == 0, message, {"current": current, "history": history, "status": stats_status(current, history)}


def list_backups() -> list[dict[str, Any]]:
    if not BACKUP_DIR.exists():
        return []
    entries = []
    # stat собираем до сортировки и под try: cleanup_old_backups мог удалить файл
    # между glob и stat, и один пропавший файл ронял весь /api/overview.
    for path in BACKUP_DIR.glob("*.tar.gz*"):
        if path.name.endswith(".sha256"):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        entries.append((path, st))
    entries.sort(key=lambda item: item[1].st_mtime, reverse=True)
    items = []
    for path, st in entries:
        items.append({
            "name": path.name,
            "path": str(path),
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "encrypted": path.name.endswith(".enc"),
        })
    return items[:30]


def backup_schedule_calendar(frequency: str) -> str | None:
    calendars = {
        "off": None,
        "daily": "*-*-* 03:20:00",
        "weekly": "Sun 03:20:00",
        "monthly": "*-*-01 03:20:00",
    }
    if frequency not in calendars:
        raise ValueError("unsupported backup schedule")
    return calendars[frequency]


def backup_schedule_status() -> dict[str, Any]:
    raw = load_json(BACKUP_SCHEDULE_FILE, {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    frequency = str(raw.get("frequency") or "off")
    try:
        calendar = backup_schedule_calendar(frequency)
    except ValueError:
        frequency = "off"
        calendar = None
    active_code, active, _ = run(["systemctl", "is-active", "gotelegram-backup.timer"], timeout=5)
    enabled_code, enabled, _ = run(["systemctl", "is-enabled", "gotelegram-backup.timer"], timeout=5)
    _, next_run, _ = run(["systemctl", "show", "gotelegram-backup.timer", "--property=NextElapseUSecRealtime", "--value"], timeout=5)
    return {
        "frequency": frequency,
        "calendar": calendar,
        "enabled": enabled_code == 0 and enabled.strip() == "enabled",
        "active": active_code == 0 and active.strip() == "active",
        "next": next_run.strip(),
        "updated_at": raw.get("updated_at") or "",
    }


def set_backup_schedule(frequency: str) -> tuple[bool, str, dict[str, Any]]:
    backup_schedule_calendar(frequency)
    script = (
        "source /opt/gotelegram/lib/common.sh; "
        "source /opt/gotelegram/lib/i18n.sh; "
        "source /opt/gotelegram/lib/backup.sh; "
        "load_language \"$(detect_language 2>/dev/null || echo en)\"; "
        f"set_backup_schedule {shlex.quote(frequency)}"
    )
    code, stdout, stderr = run(["bash", "-lc", script], timeout=120)
    message = (stdout.strip().splitlines()[-1:] or stderr.strip().splitlines()[-1:] or [""])[0]
    return code == 0, message, backup_schedule_status()


def create_backup() -> tuple[bool, str]:
    script = (
        "source /opt/gotelegram/lib/common.sh; "
        "source /opt/gotelegram/lib/i18n.sh; "
        "source /opt/gotelegram/lib/telemt.sh; "
        "source /opt/gotelegram/lib/website.sh; "
        "source /opt/gotelegram/lib/backup.sh; "
        "load_language \"$(detect_language 2>/dev/null || echo en)\"; "
        "create_backup \"\"; "
        "cleanup_old_backups 30"
    )
    code, stdout, stderr = run(["bash", "-lc", script], timeout=180)
    text = (stdout.strip().splitlines()[-1:] or stderr.strip().splitlines()[-1:] or [""])[0]
    return code == 0, text


def safe_backup_path(name: str) -> Path:
    raw = str(name or "").strip()
    if not raw or raw != os.path.basename(raw) or not BACKUP_NAME_RE.match(raw) or raw.endswith(".sha256"):
        raise ValueError("invalid backup name")
    candidate = (BACKUP_DIR / raw).resolve()
    base = BACKUP_DIR.resolve()
    if base != candidate.parent:
        raise ValueError("invalid backup path")
    if not candidate.exists():
        raise FileNotFoundError("backup not found")
    return candidate


def launch_restore_backup(name: str, password: str = "") -> dict[str, Any]:
    backup_path = safe_backup_path(name)
    if backup_path.name.endswith(".enc") and not password:
        raise ValueError("password required for encrypted backup")
    BACKUP_RESTORE_LOG.parent.mkdir(parents=True, exist_ok=True)
    # Каталог замка мог не существовать после ребута — exec 9> в скрипте иначе упал бы.
    USER_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    quoted_path = shlex.quote(str(backup_path))
    quoted_log = shlex.quote(str(BACKUP_RESTORE_LOG))
    quoted_lock = shlex.quote(str(USER_LOCK_FILE))
    # Пароль НЕ подставляем в текст команды: он стал бы элементом argv процесса
    # bash, а /proc/<pid>/cmdline читается любым локальным пользователем (под
    # www-data работает nginx) — то есть пароль от всех бэкапов был бы виден в
    # `ps` всё время восстановления. lib/backup.sh:168 это правило уже соблюдает,
    # передавая пароль в openssl файлом; здесь оно нарушалось.
    # Кладём пароль во временный файл 0600 (mkstemp даёт такие права) и
    # подставляем в команду только ПУТЬ. Значение попадает в bash подстановкой
    # $(cat …) и дальше живёт аргументом shell-функции — то есть внутри процесса,
    # а не в его командной строке. Файл удаляется сразу после чтения.
    pass_file = ""
    if password:
        # Каталог берём тот же, что у замка (он настраивается переменной среды и
        # только что создан выше), а не зашитый /run/gotelegram — иначе на стенде
        # с другими путями mkstemp упал бы на несуществующем каталоге.
        fd, pass_file = tempfile.mkstemp(prefix=".gt-restore-pass.",
                                         dir=str(USER_LOCK_FILE.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(password)
        except Exception:
            os.unlink(pass_file)
            raise
    quoted_pass_file = shlex.quote(pass_file)
    pass_expr = f'"$(cat -- {quoted_pass_file})"' if pass_file else '""'
    pass_cleanup = f"rm -f -- {quoted_pass_file}; " if pass_file else ""
    script = (
        "sleep 1; "
        "source /opt/gotelegram/lib/common.sh; "
        "source /opt/gotelegram/lib/i18n.sh; "
        "source /opt/gotelegram/lib/telemt.sh; "
        "source /opt/gotelegram/lib/website.sh; "
        "source /opt/gotelegram/lib/backup.sh; "
        "load_language \"$(detect_language 2>/dev/null || echo en)\"; "
        "create_backup \"\" >/dev/null 2>&1 || true; "
        # Тот же замок, что у панели и бота: без него cp из архива затирал ключ,
        # созданный во время восстановления, либо сам затирался его записью.
        f"exec 9>{quoted_lock}; flock 9; "
        f"restore_backup {quoted_path} {pass_expr} yes; "
        # Пароль на диске не переживает восстановление ни на секунду дольше нужного.
        + pass_cleanup +
        "cleanup_old_backups 30"
    )
    try:
        with BACKUP_RESTORE_LOG.open("ab") as log:
            log.write(f"\n[{utc_now()}] restore requested for {backup_path.name}\n".encode("utf-8"))
            subprocess.Popen(
                ["bash", "-lc", f"{script} >> {quoted_log} 2>&1"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except Exception:
        # Скрипт не стартовал — удалить файл с паролем больше некому.
        if pass_file:
            try:
                os.unlink(pass_file)
            except OSError:
                pass
        raise
    return {"name": backup_path.name, "started": True, "log": str(BACKUP_RESTORE_LOG)}


def user_qr_png(name: str, web: bool = False) -> tuple[bytes, str]:
    users = read_user_records()
    record = users.get(name)
    if not record:
        raise FileNotFoundError("user not found")
    secret = str(record.get("secret", ""))
    link = web_link(secret) if web else proxy_link(secret)
    if not link:
        raise RuntimeError("web proxy is off")
    code, image, error = run_bytes(["qrencode", "-t", "PNG", "-s", "8", "-m", "2", "-o", "-", link], timeout=8)
    if code != 0 or not image:
        raise RuntimeError(error.strip() or "qrencode is not installed")
    return image, link


def read_log_payload(service: str) -> dict[str, Any]:
    allowed = {"telemt", "nginx", "gotelegram-bot", "gotelegram-stats", "gotelegram-admin"}
    if service not in allowed:
        raise ValueError("unsupported service")
    code, stdout, stderr = run(["journalctl", "-u", service, "-n", "180", "--no-pager", "-o", "short-iso"], timeout=10)
    text = stdout if code == 0 else stderr
    lines = text.splitlines()
    if code == 0 and not lines:
        text = f"No journal entries for {service}."
        lines = [text]
    return {
        "service": service,
        "ok": code == 0,
        "exit_code": code,
        "line_count": len(lines),
        "text": text,
    }


def user_payload(
    name: str,
    secret: str,
    enabled: bool = True,
    max_unique_ips: int = 0,
    include_runtime: bool = False,
    traffic_snapshot: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name,
        "secret": secret,
        "link": proxy_link(secret, config),
        "web_link": web_link(secret, config),
        "main": name == "main",
        "enabled": bool(enabled),
        "max_unique_ips": _int_value(max_unique_ips),
    }
    if traffic_snapshot:
        item["traffic"] = {
            "epoch": traffic_snapshot.get("epoch", 0),
            "total_octets": traffic_snapshot.get("total_octets", 0),
            "current_connections": traffic_snapshot.get("current_connections", 0),
            "active_unique_ips": traffic_snapshot.get("active_unique_ips", 0),
            "recent_unique_ips": traffic_snapshot.get("recent_unique_ips", 0),
        }
    if include_runtime and enabled:
        item["runtime"] = telemt_api(f"/v1/users/{urllib.parse.quote(name, safe='')}")
    return item


def detect_version(config: dict[str, Any] | None = None) -> str:
    config = config if config is not None else (load_json(GOTELEGRAM_CONFIG, {}) or {})
    if isinstance(config, dict):
        for candidate in (config.get("version"), config.get("gotelegram_version")):
            value = str(candidate or "").strip()
            if re.match(r"^\d+\.\d+", value):
                return value
    try:
        common = (INSTALL_DIR / "lib" / "common.sh").read_text(encoding="utf-8", errors="ignore")
        match = re.search(r'^GOTELEGRAM_VERSION="?([0-9][0-9A-Za-z._-]*)"?', common, re.M)
        if match:
            return match.group(1)
    except OSError:
        pass
    return VERSION


def overview_payload() -> dict[str, Any]:
    config = load_json(GOTELEGRAM_CONFIG, {}) or {}
    language = read_language(config)
    users = read_user_records()
    current = load_json(CURRENT_STATS, {}) or {}
    history = load_stats_history()
    summary = telemt_api("/v1/stats/summary")
    services = {
        "telemt": service_status("telemt"),
        "nginx": service_status("nginx"),
        "bot": service_status("gotelegram-bot"),
        "stats": service_status("gotelegram-stats"),
        "admin": service_status("gotelegram-admin"),
    }
    return {
        "version": detect_version(config),
        "time": utc_now(),
        "language": language,
        # Сокет — не единственный путь внутрь, и показывать только его значит
        # оставлять человека при впечатлении, что панель доступна лишь по
        # туннелю. Отдаём и публичный адрес, и готовность входа по коду:
        # решать, что нарисовать, должен интерфейс, а не догадка.
        "admin_bind": {"host": HOST, "port": PORT,
                       "public_url": _public_panel_url(config),
                       "tfa": tfa_available()},
        "config": public_config(config),
        "site_status": site_status(config),
        "users_count": len(users),
        "services": services,
        "port_443": port_443_status(),
        # Учёт по адресам считает ядро, панель только показывает. Ходим за ним
        # через тот же --action=, что и бот: два независимых чтения одних и тех
        # же счётчиков разошлись бы при первой же правке.
        "edge_ips": edge_ips_status(),
        "stats_current": current,
        "stats_history": history,
        "stats_status": stats_status(current, history),
        "runtime_summary": summary,
        "backups": list_backups(),
        "backup_schedule": backup_schedule_status(),
    }


# --------------------------------------------------------------------------
# Admin token. Without it every local process (and, through the operator's
# SSH tunnel, every page open in their browser) could read the MTProto
# secrets of all keys and run privileged actions as root.
# --------------------------------------------------------------------------
_TOKEN_LOCK = threading.Lock()
_ADMIN_TOKEN: str | None = None


def _read_token_file() -> str:
    try:
        raw = ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return raw if ADMIN_TOKEN_RE.match(raw) else ""


def _create_token_file() -> str:
    token = secrets.token_urlsafe(32)
    try:
        ADMIN_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(ADMIN_TOKEN_FILE.parent, 0o700)
        except OSError:
            pass
        fd = os.open(str(ADMIN_TOKEN_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
        os.chmod(ADMIN_TOKEN_FILE, 0o600)
    except OSError as exc:  # pragma: no cover - depends on filesystem
        print(f"[admin] cannot persist token to {ADMIN_TOKEN_FILE}: {exc}; using an in-memory one", flush=True)
    return token


def admin_token() -> str:
    global _ADMIN_TOKEN
    with _TOKEN_LOCK:
        if _ADMIN_TOKEN:
            return _ADMIN_TOKEN
        env_token = os.getenv(ADMIN_TOKEN_ENV, "").strip()
        if ADMIN_TOKEN_RE.match(env_token):
            _ADMIN_TOKEN = env_token
            return _ADMIN_TOKEN
        _ADMIN_TOKEN = _read_token_file() or _create_token_file()
        return _ADMIN_TOKEN


def host_name_only(raw: str) -> str:
    value = raw.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[:end + 1] if end > 0 else value
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


WEB_CARRIERS = {"https", "https-lanes", "websocket", "websocket-lanes"}


def web_cli(action: str, carrier: str = "", timeout: int = 30) -> tuple[dict[str, Any], str]:
    """Run one install.sh --action=web-* and return (data, error) — как warp_cli."""
    cmd = ["bash", str(INSTALL_DIR / "install.sh"), f"--action={action}", "--json"]
    if carrier:
        cmd.append(f"--carrier={carrier}")
    code, out, err = run(cmd, timeout=timeout)
    data: dict[str, Any] = {}
    for line in reversed((out or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            if isinstance(parsed, dict):
                data = parsed
                break
    if data:
        return data, ""
    detail = ((err or "").strip().splitlines() or (out or "").strip().splitlines() or [""])[-1].strip()
    if code == 124:
        message = f"{action}: timed out after {timeout}s"
    elif code != 0:
        message = f"{action}: exit code {code}"
    else:
        message = f"{action}: no JSON result"
    return {}, f"{message}" + (f" — {detail}" if detail else "")


def warp_cli(action: str, key: str = "", timeout: int = 30) -> tuple[dict[str, Any], str]:
    """Run one install.sh --action=warp-* and return (data, error).

    The exit code used to be discarded: a timeout or a killed bash produced
    an empty dict that the UI happily rendered as "WARP is off".
    """
    cmd = ["bash", str(INSTALL_DIR / "install.sh"), f"--action={action}", "--json"]
    if key:
        cmd.append(f"--key={key}")
    code, out, err = run(cmd, timeout=timeout)
    data: dict[str, Any] = {}
    for line in reversed((out or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            if isinstance(parsed, dict):
                data = parsed
                break
    if data:
        return data, ""
    detail = ((err or "").strip().splitlines() or (out or "").strip().splitlines() or [""])[-1].strip()
    if code == 124:
        message = f"{action}: timed out after {timeout}s"
    elif code != 0:
        message = f"{action}: exit code {code}"
    else:
        message = f"{action}: no JSON in output"
    if detail:
        message = f"{message} ({detail[:200]})"
    return {}, message


# Мини-приложение Telegram открывается ВО ФРЕЙМЕ клиента Telegram и требует
# его SDK. Прежняя политика запрещала и то, и другое: frame-ancestors 'none'
# не давал встроить страницу, а script-src 'self' резал telegram-web-app.js.
# Кнопка «Панель» в боте открывалась и вечно крутила загрузку. Разрешаем
# ровно два источника Telegram и ничего сверх того.
TELEGRAM_FRAME_ANCESTORS = "https://web.telegram.org https://*.telegram.org"
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    f"frame-ancestors {TELEGRAM_FRAME_ANCESTORS}; "
    "form-action 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline' https://telegram.org"
)

SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    # X-Frame-Options не умеет списки источников и в браузерах перебивает
    # frame-ancestors — с ним мини-апп во фрейме Telegram не откроется.
    # Защиту от кликджекинга целиком несёт CSP выше.
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
)


class AdminHandler(BaseHTTPRequestHandler):
    timeout = 15
    # VERSION — только запасное значение; живое читается из config.json, иначе
    # заголовок годами показывал версию, зашитую в файл при выпуске.
    server_version = f"goTelegramProAdmin/{detect_version()}"

    def log_message(self, fmt: str, *args: Any) -> None:
        # journald used to collect 50-70k lines a day from the 5s auto-refresh.
        if ACCESS_LOG:
            print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def handle_one_request(self) -> None:
        self._security_headers_sent = False
        super().handle_one_request()

    def end_headers(self) -> None:
        if not getattr(self, "_security_headers_sent", False):
            self._security_headers_sent = True
            for name, value in SECURITY_HEADERS:
                self.send_header(name, value)
        super().end_headers()

    # Заголовок сессии, если guard_request только что её завёл. Ставится в
    # ОБОИХ путях ответа: страница панели уходит через send_bytes, а первый
    # запрос данных — через send_json, и пропусти мы один, cookie не появилась
    # бы ровно в половине случаев.
    _issue_session: str | None = None

    def _session_cookie(self, tok: str, same_site: str = "Strict") -> str:
        """Строка Set-Cookie для сессии.

        Max-Age обязан пережить окно продления: истеки cookie ровно в 15 минут,
        браузер выбросил бы её именно тогда, когда она нужна для продления.

        Secure ставим там, где соединение и правда по TLS, то есть когда панель
        открыта с домена через nginx. Через SSH-туннель это обычный
        http://127.0.0.1: браузеры принимают Secure-cookie на localhost, но
        стоит пробросить туннель на другое имя — и она молча пропадает, а вход
        выглядит как «панель меня не запоминает». Внутри туннеля трафик и так
        зашифрован SSH.

        Исключение — SameSite=None: спецификация требует к нему Secure всегда,
        и без него браузер отбросит cookie. Так ходит только мини-апп, а он
        всегда открыт по HTTPS с домена.
        """
        secure = "; Secure" if (same_site == "None" or self.is_public_request()) else ""
        return (f"{SESSION_COOKIE}={tok}; Path=/; "
                f"Max-Age={SESSION_TTL + SESSION_GRACE + SESSION_COOKIE_SLACK}; "
                f"HttpOnly{secure}; SameSite={same_site}")

    def _maybe_set_session_cookie(self) -> None:
        tok = getattr(self, "_issue_session", None)
        if not tok:
            return
        self._issue_session = None
        self.send_header("Set-Cookie", self._session_cookie(tok))

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._maybe_set_session_cookie()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self._maybe_set_session_cookie()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def read_json_body(self) -> Any:
        # Кривое тело — это обычный ввод из интернета, а не сбой сервера. Раньше
        # json.loads выбрасывал JSONDecodeError прямо в обработчик, тот падал,
        # соединение обрывалось, и nginx отдавал наружу 502 Bad Gateway. Любой,
        # кто знает секретный путь, мог так засорять логи трейсбеками.
        length_raw = self.headers.get("Content-Length", "0") or "0"
        try:
            length = int(length_raw)
        except (TypeError, ValueError):
            raise BadRequest("bad Content-Length")
        if length > 1024 * 1024:
            raise BadRequest("request body too large")
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length)
        except OSError:
            raise BadRequest("body read failed")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BadRequest("malformed JSON body")
        # Валидный JSON, но не объект (null/список/строка) ронял body.get() в 500
        # с трейсбеком — в том числе на неаутентифицированном /api/auth/*.
        if not isinstance(data, dict):
            raise BadRequest("JSON body must be an object")
        return data

    def require_write_guard(self) -> bool:
        if self.command in {"POST", "PUT", "PATCH", "DELETE"} and self.headers.get("X-GoTelegram-Admin") != "1":
            self.send_error_json(403, "missing write guard")
            return False
        return True

    def host_allowed(self) -> bool:
        raw = (self.headers.get("Host") or "").strip()
        if not raw:
            # Browsers always send Host; plain HTTP/1.0 tooling may not.
            return True
        return host_name_only(raw) in ALLOWED_HOSTS

    def request_token(self, parsed: urllib.parse.ParseResult) -> str:
        header = (self.headers.get(ADMIN_TOKEN_HEADER) or "").strip()
        if header:
            return header
        try:
            values = urllib.parse.parse_qs(parsed.query).get(ADMIN_TOKEN_PARAM) or []
        except Exception:
            return ""
        return values[0].strip() if values else ""

    def token_ok(self, parsed: urllib.parse.ParseResult) -> bool:
        expected = admin_token()
        provided = self.request_token(parsed)
        if not expected or not provided:
            return False
        return hmac.compare_digest(
            expected.encode("utf-8", "ignore"),
            provided.encode("utf-8", "ignore"),
        )

    def miniapp_ok(self) -> bool:
        """Вход из Telegram Mini App: подпись вместо токена панели."""
        raw = (self.headers.get(MINIAPP_INITDATA_HEADER) or "").strip()
        if not raw:
            return False
        uid = verify_telegram_init_data(raw)
        if uid:
            self._miniapp_uid = uid
            return True
        return False

    def request_session(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return value.strip()
        return ""

    def is_public_request(self) -> bool:
        """Пришли ли из интернета (по адресу панели на домене), а не по туннелю.

        Реального адреса клиента в Pro-режиме не существует: :443 занят прокси,
        он передаёт «чужой SNI» на локальный nginx обычным TCP, и для nginx
        клиент — сам telemt. Зато наружу панель отдаёт единственный location —
        мини-аппа, — и только он ставит X-Forwarded-Host. По нему и различаем.
        """
        return bool((self.headers.get("X-Forwarded-Host") or "").strip())

    def client_ip(self) -> str:
        # За nginx реальный адрес приходит заголовком; напрямую — из сокета.
        fwd = (self.headers.get("X-Real-IP") or "").strip()
        return fwd or (self.client_address[0] if self.client_address else "?")

    def is_local_console(self) -> bool:
        """Пришли ли ПРЯМО на петлевой слушатель, а не через nginx с домена.

        Это и есть «через localhost»: `ssh -L 1984:127.0.0.1:1984`, то есть тот,
        кто уже доказал право на сервер входом по SSH.

        Двойная проверка не для красоты:
          * ни одного заголовка обратного прокси — публичный вход отсечён;
          * реальный адрес сокета петлевой — на случай, если панель подняли не
            на 127.0.0.1 (GOTELEGRAM_ADMIN_HOST можно переопределить). Без этой
            половины панель, выставленная на внешний интерфейс напрямую, мимо
            nginx, пускала бы кого угодно без единой проверки.

        Про заголовки. Достаточно было бы одного X-Forwarded-Host — его nginx
        ставит безусловно (lib/website.sh), клиентское значение затирается,
        подделать снаружи нельзя. Но тогда ВЕСЬ вход в панель держался бы на
        одной строке конфига: сотрись она при правке шаблона — и улица получает
        права туннеля молча. Поэтому смотрим на все заголовки, которые ставит
        обратный прокси: наш ставит три (Host, X-Forwarded-Host, X-Real-IP),
        чужой почти наверняка поставит хоть один из перечисленных. Браузер в
        туннеле не шлёт ни одного. Ошибка в любую сторону здесь безопасна:
        лишний заголовок означает лишь просьбу ввести код.
        """
        for h in ("X-Forwarded-Host", "X-Real-IP", "X-Forwarded-For", "X-Forwarded-Proto"):
            if (self.headers.get(h) or "").strip():
                return False
        peer = self.client_address[0] if self.client_address else ""
        # ::ffff:127.0.0.1 — та же петля, если слушатель подняли на «::».
        return peer in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}

    def guard_request(self, parsed: urllib.parse.ParseResult) -> bool:
        if not self.host_allowed():
            self.send_error_json(403, "host not allowed")
            return False
        # Вход по коду: сами эти маршруты обязаны быть открыты, иначе войти
        # невозможно в принципе.
        if parsed.path in {"/api/auth/request", "/api/auth/verify", "/api/auth/miniapp",
                           "/api/session", "/login", "/login.html"}:
            return True
        if self.command == "GET" and parsed.path in PUBLIC_STATIC_PATHS:
            return True
        # Билет из адреса: единственный способ открыть панель, если браузер
        # режет сторонние cookie внутри фрейма Telegram. Гасим его ДО проверки
        # cookie: билет оседает в access-log nginx, и непогашенным он оставался
        # бы там рабочим пропуском.
        if self.command == "GET" and not parsed.path.startswith("/api/"):
            _q = urllib.parse.parse_qs(parsed.query or "")
            _ticket = (_q.get("s") or [""])[0].strip()
            if _ticket and ticket_redeem(_ticket):
                return True
        if session_valid(self.request_session()):
            return True
        if self.miniapp_ok():
            return True
        # Вход через localhost — без второго фактора. Право на сервер уже
        # доказано входом по SSH: туннель `ssh -L 1984:127.0.0.1:1984` не
        # построить, не имея доступа к машине, где и так лежит root-only файл
        # токена. Требовать сверх этого код или токен — просить доказать то, что
        # уже доказано; при ненастроенном боте это к тому же запирало панель
        # совсем, потому что кода взять неоткуда.
        # Наличие бота ничего здесь не меняет: есть он — работают ОБА пути
        # (локальный и код в Telegram), нет — остаётся локальный.
        # Публичного входа с домена это не касается: там по-прежнему нужен код.
        if TRUST_LOCAL_CONSOLE and self.is_local_console():
            # Сессию заводим только на загрузке страницы, а не на любом запросе:
            # cookie сохранит браузер, а клиент, который её игнорирует (curl в
            # цикле, опрос из скрипта), иначе плодил бы по записи в _SESSIONS на
            # КАЖДЫЙ запрос. Доступа это не меняет — сюда мы уже вошли; сессия
            # нужна лишь чтобы интерфейс показывал осмысленный остаток времени.
            if (self.command == "GET" and not parsed.path.startswith("/api/")
                    and not session_valid(self.request_session())):
                self._issue_session = session_create()
            return True
        if not self.token_ok(parsed):
            # Браузеру отдаём страницу входа, а не голый 401: иначе человек
            # видит пустоту и не понимает, что делать.
            if self.command == "GET" and not parsed.path.startswith("/api/"):
                self.send_login_page()
                return False
            # Причину отказа обязан различать интерфейс: «продлите» и
            # «входите заново» — разные экраны, и раньше оба выглядели как
            # требование токена из файла на сервере.
            st, left = session_state(self.request_session())
            if st == "renew":
                self.send_json({"ok": False, "error": "session renewal required",
                                "state": "renew", "seconds": left}, status=401)
            elif st == "dead" and self.request_session():
                self.send_json({"ok": False, "error": "session expired",
                                "state": "expired", "seconds": 0}, status=401)
            else:
                self.send_json({"ok": False, "error": "admin token required",
                                "state": "none", "seconds": 0}, status=401)
            return False
        # Токен верен. Заводим сессию и дальше живём по ней: без этого правило
        # «15 минут» токена не касалось бы вовсе — браузер держит его в
        # sessionStorage и шлёт с каждым запросом, то есть открытая вкладка
        # оставалась бы входом в панель, пока её не закроют.
        if not session_valid(self.request_session()):
            self._issue_session = session_create()
        return True

    def send_login_page(self) -> None:
        # Страница обязана знать, работает ли вход по коду: без бота кнопка
        # «Прислать код» не сработает никогда, и человеку надо показать
        # запасной путь, а не мёртвую кнопку.
        body = LOGIN_PAGE_HTML.replace(
            "__TFA_AVAILABLE__", "true" if tfa_available() else "false"
        ).replace("__ADMIN_PORT__", str(PORT)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def route_get_api(self, parsed: urllib.parse.ParseResult) -> None:
        path = parsed.path
        if path == "/api/session":
            # Клиент спрашивает про сессию сам, не дожидаясь первой неудачи:
            # иначе окно продления в 60 секунд человек замечает по сломанной
            # кнопке, а не по предупреждению, и половину окна уже потерял.
            #
            # Для входа через localhost сессия НЕ является пропуском: guard
            # пускает такого клиента независимо от неё. Отвечать ему настоящим
            # состоянием значило бы врать в лицо — фронтенд (app.js: state
            # renew/dead → showRenewGate/showAuthGate) через 15 минут закрыл бы
            # рабочую панель окном «продлите кодом», а при ненастроенном боте
            # это тупик: кода взять неоткуда. Поэтому здесь отвечаем состоянием
            # ДОСТУПА, а оно у локального клиента всегда «ok».
            if TRUST_LOCAL_CONSOLE and self.is_local_console():
                st, left = "ok", SESSION_TTL
            else:
                st, left = session_state(self.request_session())
            self.send_json({"ok": True, "data": {
                "state": st, "seconds": left,
                "ttl": SESSION_TTL, "grace": SESSION_GRACE,
                "tfa": tfa_available(),
            }})
        elif path == "/api/overview":
            self.send_json({"ok": True, "data": overview_payload()})
        elif path == "/api/users":
            users = read_user_records()
            latest = latest_user_stats()
            config = load_json(GOTELEGRAM_CONFIG, {}) or {}
            items = []
            for name in sorted(users, key=lambda item: (item != "main", item)):
                record = users[name]
                items.append(user_payload(
                    name,
                    record["secret"],
                    record["enabled"],
                    record.get("max_unique_ips", 0),
                    traffic_snapshot=current_user_traffic_snapshot(name, record["enabled"], latest.get(name)),
                    config=config,
                ))
            self.send_json({"ok": True, "data": items})
        elif path.startswith("/api/users/") and path.endswith("/qr"):
            name = urllib.parse.unquote(path[len("/api/users/"):-len("/qr")])
            want_web = urllib.parse.parse_qs(parsed.query).get("web", [""])[0] == "1"
            try:
                png, link = user_qr_png(name, web=want_web)
            except FileNotFoundError:
                self.send_error_json(404, "user not found")
                return
            except Exception as exc:
                self.send_error_json(503, str(exc))
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Proxy-Link", urllib.parse.quote(link, safe=""))
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
        elif path.startswith("/api/users/") and path.endswith("/traffic"):
            name = urllib.parse.unquote(path[len("/api/users/"):-len("/traffic")])
            users = read_user_records()
            if name not in users:
                self.send_error_json(404, "user not found")
                return
            qs = urllib.parse.parse_qs(parsed.query)
            range_key = normalize_range(qs.get("range", ["1h"])[0])
            all_history = load_user_stats_history(name, limit=history_limit_for_range("month"))
            history = filter_history_by_range(all_history[-history_limit_for_range(range_key):], range_key)
            current = runtime_user_traffic(name, bool(users[name].get("enabled")))
            self.send_json({
                "ok": True,
                "data": {
                    "name": name,
                    "range": range_key,
                    "current": current,
                    "history": history,
                    "summary_rows": user_traffic_interval_summaries(all_history),
                    "status": {
                        "history_exists": USER_HISTORY_FILE.exists(),
                        "history_rows": count_user_history_rows(name),
                        "history_points": len(history),
                        "last_ts": history[-1]["epoch"] if history else 0,
                        "runtime_ok": current.get("ok", False),
                    },
                },
            })
        elif path.startswith("/api/users/"):
            name = urllib.parse.unquote(path[len("/api/users/"):])
            users = read_user_records()
            if name not in users:
                self.send_error_json(404, "user not found")
                return
            record = users[name]
            self.send_json({"ok": True, "data": user_payload(
                name,
                record["secret"],
                record["enabled"],
                record.get("max_unique_ips", 0),
                include_runtime=True,
                traffic_snapshot=current_user_traffic_snapshot(name, record["enabled"], latest_user_stats().get(name)),
            )})
        elif path == "/api/backups":
            self.send_json({"ok": True, "data": list_backups()})
        elif path == "/api/backups/schedule":
            self.send_json({"ok": True, "data": backup_schedule_status()})
        elif path == "/api/stats":
            qs = urllib.parse.parse_qs(parsed.query)
            range_key = normalize_range(qs.get("range", ["1h"])[0])
            current = load_json(CURRENT_STATS, {}) or {}
            all_history = load_stats_history(limit=history_limit_for_range("month"))
            history = filter_history_by_range(all_history[-history_limit_for_range(range_key):], range_key)
            self.send_json({
                "ok": True,
                "data": {
                    "range": range_key,
                    "current": current,
                    "history": history,
                    "summary_rows": traffic_interval_summaries(all_history),
                    "status": stats_status(current, history),
                },
            })
        elif path == "/api/site/check":
            force = urllib.parse.parse_qs(parsed.query).get("force", ["0"])[0] not in {"", "0", "false"}
            self.send_json({"ok": True, "data": site_status(force=force)})
        elif path == "/api/logs":
            qs = urllib.parse.parse_qs(parsed.query)
            service = qs.get("service", ["telemt"])[0]
            try:
                payload = read_log_payload(service)
            except ValueError:
                self.send_error_json(400, "unsupported service")
                return
            self.send_json({"ok": True, "data": payload})
        elif path == "/api/warp":
            data, error = warp_cli("warp-status", timeout=30)
            self.send_json({"ok": bool(data), "data": data, "error": error}, 200 if data else 503)
        elif path == "/api/warp/probe":
            data, error = warp_cli("warp-probe", timeout=30)
            self.send_json({"ok": bool(data), "data": data, "error": error}, 200 if data else 503)
        elif path == "/api/web":
            # Читающее действие; самопроверка bridge внутри кэшируется на пять минут.
            # Ответ со status=error (занят общий замок) — не «выключен», а ошибка:
            # иначе фронт рисовал кнопку «Включить» на включённом WEB.
            data, error = web_cli("web-status", timeout=40)
            failed = not data or str(data.get("status") or "") == "error"
            if failed and not error:
                error = str(data.get("message") or "web-status returned an error")
            self.send_json({"ok": not failed, "data": data, "error": error}, 200 if not failed else 503)
        else:
            self.send_error_json(404, "not found")

    def route_post_api(self, parsed: urllib.parse.ParseResult) -> None:
        # Предохранитель от CSRF стоит ПЕРЕД всеми ветками, включая /api/auth/*.
        # Раньше три маршрута входа отвечали до него, и это была дыра: заголовок
        # X-GoTelegram-Admin — единственное, что отличает запрос нашего фронтенда
        # от кросс-сайтового, а POST с Content-Type: text/plain уходит без
        # префлайта. Чужая страница дёргала /api/auth/request и слала админу код
        # от его же панели, попутно выжигая часовую квоту. Свои страницы
        # заголовок ставят обе — и app.js, и форма входа внутри server.py.
        if not self.require_write_guard():
            return
        # ── вход по коду из Telegram ──
        if parsed.path == "/api/auth/request":
            _tok = self.request_session()
            ok, message = tfa_request(
                self.client_ip(), public=self.is_public_request(),
                renew=session_state(_tok)[0] in {"ok", "renew"})
            self.send_json({"ok": ok, "error": None if ok else message,
                            "message": message if ok else None},
                           status=200 if ok else 429)
            return
        if parsed.path == "/api/auth/miniapp":
            # Обычный переход по ссылке не несёт заголовков, поэтому при
            # открытии из Telegram сервер видел безымянного гостя и отдавал
            # форму с кодом — то есть мини-апп просил код, хотя Telegram уже
            # доказал, кто пришёл. Здесь страница присылает подпись явно и
            # получает сессионную cookie, дальше всё как после ввода кода.
            body = self.read_json_body() or {}
            raw = str(body.get("init_data") or "").strip()
            uid = verify_telegram_init_data(raw) if raw else None
            if not uid:
                self.send_error_json(401, "подпись Telegram не подтверждена")
                return
            if str(uid) not in _bot_admin_ids():
                sys.stderr.write(f"2FA: мини-апп отклонён, telegram_id={uid} не в ALLOWED_IDS\n")
                self.send_error_json(403, "этот Telegram-аккаунт не админ бота")
                return
            sys.stderr.write(f"2FA: успешный вход из мини-аппа, telegram_id={uid}\n")
            token = session_create()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            # SameSite=Strict внутри фрейма Telegram не отдаётся — для браузера
            # это сторонний контекст, и панель после успешного входа снова
            # показывала форму кода. Для мини-аппа кладём None+Secure, а если
            # браузер режет сторонние cookie вовсе — страница получает ещё и
            # одноразовый билет в адресе.
            self.send_header(
                "Set-Cookie", self._session_cookie(token, same_site="None"),
            )
            # Билет отдельный от сессии: раньше в адресную строку (и access-log
            # nginx) уезжал сам 12-часовой сессионный токен.
            payload = json.dumps({"ok": True, "ticket": ticket_create()}).encode()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path == "/api/auth/verify":
            body = self.read_json_body() or {}
            ok, message = tfa_verify(str(body.get("code", "")))
            # Журнал входов: раньше писались только сбои доставки, а сам факт
            # входа — ни успешный, ни неудачный — нигде не отражался. Для
            # админки это дыра в аудите: вход с чужого адреса и перебор кода
            # не оставляли следа вовсе. Сам код не пишем никогда.
            if not ok:
                sys.stderr.write(
                    f"2FA: неудачная попытка входа "
                    f"{'из интернета' if self.is_public_request() else 'по туннелю'} "
                    f"({self.client_ip()}): {message}\n")
                self.send_json({"ok": False, "error": message}, status=401)
                return
            sys.stderr.write(
                f"2FA: успешный вход по коду "
                f"{'из интернета' if self.is_public_request() else 'по туннелю'} "
                f"({self.client_ip()})\n")
            token = session_create()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header(
                "Set-Cookie", self._session_cookie(token),
            )
            payload = json.dumps({"ok": True}).encode()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        path = parsed.path
        try:
            body = self.read_json_body()
        except Exception as exc:
            self.send_error_json(400, str(exc))
            return

        if path == "/api/web":
            act = str(body.get("action") or "")
            carrier = str(body.get("carrier") or "").strip()
            amap = {"on": "web-on", "off": "web-off", "carrier": "web-carrier"}
            if act not in amap:
                self.send_error_json(400, "action must be on|off|carrier")
                return
            if act == "carrier" and carrier not in WEB_CARRIERS:
                self.send_error_json(400, "carrier must be one of " + ", ".join(sorted(WEB_CARRIERS)))
                return
            # включение перезапускает telemt и ждёт его готовности до 90 с
            data, error = web_cli(amap[act], carrier if act == "carrier" else "", timeout=300)
            if not data:
                self.send_json({"ok": False, "data": {}, "error": error or "WEB action returned no result"}, 502)
                return
            ok = str(data.get("status") or "") != "error"
            self.send_json({"ok": ok, "data": data, "error": "" if ok else str(data.get("message") or "")}, 200 if ok else 502)
            return

        if path == "/api/warp":
            act = str(body.get("action") or "")
            key = str(body.get("key") or "").strip()
            amap = {"on": "warp-on", "off": "warp-off", "key": "warp-key"}
            if act not in amap:
                self.send_error_json(400, "action must be on|off|key")
                return
            if act not in {"on", "key"}:
                key = ""
            if key and not WARP_KEY_RE.match(key):
                self.send_error_json(400, "invalid WARP+ key format (XXXX-XXXX-XXXX-XXXX or XXXXXXXX-XXXXXXXX-XXXXXXXX)")
                return
            data, error = warp_cli(amap[act], key, timeout=240)
            if not data:
                # An empty dict used to pass as success: None != "error".
                self.send_json({"ok": False, "data": {}, "error": error or "WARP action returned no result"}, 502)
                return
            status = str(data.get("status") or "")
            ok = status != "error"
            self.send_json({
                "ok": ok,
                "data": data,
                "error": "" if ok else str(data.get("message") or "WARP action failed"),
            }, 200)
            return

        if path == "/api/users":
            name = str(body.get("name", "")).strip()
            if not USER_RE.match(name):
                self.send_error_json(400, "invalid user name")
                return
            try:
                with FileLock(USER_LOCK_FILE):
                    records = read_user_records()
                    if name in records:
                        self.send_error_json(409, "user already exists")
                        return
                    users = read_telemt_users()
                    seed = f"{name}:{time.time()}:{secrets.token_hex(32)}".encode()
                    secret = hashlib.sha256(seed).hexdigest()[:32]
                    users[name] = secret
                    write_telemt_users(users)
            except Exception as exc:
                self.send_error_json(500, f"failed to save config: {exc}")
                return
            restart_requested = request_service_restart("telemt")
            self.send_json({"ok": True, "data": user_payload(name, secret, True, 0), "restart": {"mode": "async", "requested": restart_requested}})
        elif path.startswith("/api/users/") and path.endswith("/max-ips"):
            name = urllib.parse.unquote(path[len("/api/users/"):-len("/max-ips")])
            try:
                limit = normalize_max_unique_ips(body.get("max_unique_ips"))
            except ValueError as exc:
                self.send_error_json(400, str(exc))
                return
            try:
                with FileLock(USER_LOCK_FILE):
                    records = read_user_records()
                    if name not in records:
                        self.send_error_json(404, "user not found")
                        return
                    limits = read_user_max_unique_ips()
                    if limit > 0:
                        limits[name] = limit
                    else:
                        limits.pop(name, None)
                    write_user_max_unique_ips(limits)
                    record = read_user_records()[name]
            except Exception as exc:
                self.send_error_json(500, f"failed to save config: {exc}")
                return
            restart_requested = request_service_restart("telemt")
            self.send_json({"ok": True, "data": user_payload(
                name,
                record["secret"],
                record["enabled"],
                record.get("max_unique_ips", 0),
                traffic_snapshot=current_user_traffic_snapshot(name, record["enabled"], latest_user_stats().get(name)),
            ), "restart": {"mode": "async", "requested": restart_requested}})
        elif path.startswith("/api/users/") and path.endswith("/enabled"):
            name = urllib.parse.unquote(path[len("/api/users/"):-len("/enabled")])
            if name == "main":
                self.send_error_json(400, "main user cannot be disabled")
                return
            enabled = bool(body.get("enabled"))
            try:
                with FileLock(USER_LOCK_FILE):
                    active = read_telemt_users()
                    disabled = read_disabled_users()
                    records = read_user_records()
                    if name not in records:
                        self.send_error_json(404, "user not found")
                        return
                    if enabled:
                        secret = disabled.pop(name, records[name]["secret"])
                        active[name] = secret
                    else:
                        secret = active.pop(name, records[name]["secret"])
                        disabled[name] = secret
                    if enabled:
                        write_telemt_users(active)
                        write_disabled_users(disabled)
                    else:
                        write_disabled_users(disabled)
                        write_telemt_users(active)
            except Exception as exc:
                self.send_error_json(500, f"failed to save config: {exc}")
                return
            restart_requested = request_service_restart("telemt")
            self.send_json({"ok": True, "data": user_payload(
                name,
                secret,
                enabled,
                records[name].get("max_unique_ips", 0),
                traffic_snapshot=current_user_traffic_snapshot(name, enabled, latest_user_stats().get(name)),
            ), "restart": {"mode": "async", "requested": restart_requested}})
        elif path == "/api/backups":
            ok, result = create_backup()
            self.send_json({"ok": ok, "data": {"path": result, "backups": list_backups()}}, 200 if ok else 500)
        elif path == "/api/backups/schedule":
            try:
                frequency = str(body.get("frequency") or "off").strip().lower()
                ok, message, status = set_backup_schedule(frequency)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
                return
            self.send_json({"ok": ok, "data": {"message": message, "schedule": status}}, 200 if ok else 500)
        elif path == "/api/backups/restore":
            try:
                payload = launch_restore_backup(str(body.get("name") or ""), str(body.get("password") or ""))
            except FileNotFoundError:
                self.send_error_json(404, "backup not found")
                return
            except ValueError as exc:
                self.send_error_json(400, str(exc))
                return
            except Exception as exc:
                self.send_error_json(500, str(exc))
                return
            self.send_json({"ok": True, "data": payload}, 202)
        elif path == "/api/stats/collect":
            ok, message, payload = run_stats_action("collect")
            payload["message"] = message
            self.send_json({"ok": ok, "data": payload}, 200 if ok else 500)
        elif path == "/api/stats/repair":
            ok, message, payload = run_stats_action("repair")
            payload["message"] = message
            self.send_json({"ok": ok, "data": payload}, 200 if ok else 500)
        elif path == "/api/settings/language":
            try:
                lang_payload = write_language(str(body.get("language", "")))
            except Exception as exc:
                self.send_error_json(400, str(exc))
                return
            self.send_json({"ok": True, "data": lang_payload})
        elif path.startswith("/api/services/") and path.endswith("/restart"):
            service = path[len("/api/services/"):-len("/restart")]
            allowed = {"telemt", "nginx", "gotelegram-bot", "gotelegram-stats"}
            if service not in allowed:
                self.send_error_json(400, "unsupported service")
                return
            ok = restart_service(service)
            self.send_json({"ok": ok, "status": service_status(service)}, 200 if ok else 500)
        else:
            self.send_error_json(404, "not found")

    def route_delete_api(self, parsed: urllib.parse.ParseResult) -> None:
        if not self.require_write_guard():
            return
        path = parsed.path
        if not path.startswith("/api/users/"):
            self.send_error_json(404, "not found")
            return
        name = urllib.parse.unquote(path[len("/api/users/"):])
        if name == "main":
            self.send_error_json(400, "main user cannot be deleted")
            return
        try:
            with FileLock(USER_LOCK_FILE):
                active = read_telemt_users()
                disabled = read_disabled_users()
                records = read_user_records()
                if name not in records:
                    self.send_error_json(404, "user not found")
                    return
                active.pop(name, None)
                disabled.pop(name, None)
                limits = read_user_max_unique_ips()
                limits.pop(name, None)
                write_telemt_users(active)
                write_disabled_users(disabled)
                write_user_max_unique_ips(limits)
        except Exception as exc:
            self.send_error_json(500, f"failed to save config: {exc}")
            return
        restart_requested = request_service_restart("telemt")
        self.send_json({"ok": True, "restart": {"mode": "async", "requested": restart_requested}})

    def send_static(self, parsed: urllib.parse.ParseResult) -> None:
        rel = parsed.path.lstrip("/") or "index.html"
        if rel.startswith("api/") or ".." in rel.split("/"):
            self.send_error(404)
            return
        try:
            base = STATIC_DIR.resolve()
            path = (base / rel).resolve()
        except (OSError, RuntimeError):
            self.send_error(404)
            return
        if path != base and not path.is_relative_to(base):
            self.send_error(404)
            return
        if path.is_dir():
            path = path / "index.html"
        if not path.exists():
            path = base / "index.html"
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        # Страница отдаётся своим путём, мимо send_bytes: без этого вход по
        # ?token=… не заводил сессию на самой странице, и cookie появлялась
        # лишь на первом запросе данных.
        self._maybe_set_session_cookie()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _safe(self, fn, parsed) -> None:
        """Один общий предохранитель на все методы.

        Раньше исключение внутри маршрута обрывало соединение, и наружу
        уходил 502 Bad Gateway от nginx — то есть клиент видел «сервер лёг»
        вместо честной ошибки, а в журнал сыпались трейсбеки.
        """
        try:
            fn(parsed)
        except BadRequest as exc:
            self.send_error_json(400, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            print(f"[admin] необработанная ошибка на {self.command} {parsed.path}:\n"
                  + traceback.format_exc(), flush=True)
            try:
                self.send_error_json(500, "internal error")
            except Exception:
                pass

    def _guarded(self, parsed: urllib.parse.ParseResult) -> bool:
        # guard_request работает ДО _safe: его исключение рвало соединение без
        # ответа, и nginx показывал наружу 502 вместо честной ошибки.
        try:
            return self.guard_request(parsed)
        except BadRequest as exc:
            self.send_error_json(400, str(exc))
            return False
        except (BrokenPipeError, ConnectionResetError):
            return False
        except Exception:
            print(f"[admin] необработанная ошибка на {self.command} {parsed.path}:\n"
                  + traceback.format_exc(), flush=True)
            try:
                self.send_error_json(500, "internal error")
            except Exception:
                pass
            return False

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if not self._guarded(parsed):
            return
        if parsed.path.startswith("/api/"):
            self._safe(self.route_get_api, parsed)
        else:
            self._safe(self.send_static, parsed)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if not self._guarded(parsed):
            return
        if parsed.path.startswith("/api/"):
            self._safe(self.route_post_api, parsed)
        else:
            self.send_error(404)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if not self._guarded(parsed):
            return
        if parsed.path.startswith("/api/"):
            self._safe(self.route_delete_api, parsed)
        else:
            self.send_error(404)


def main() -> None:
    if not STATIC_DIR.exists():
        raise SystemExit(f"static dir not found: {STATIC_DIR}")
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    token = admin_token()
    httpd = ThreadingHTTPServer((HOST, PORT), AdminHandler)
    print(f"goTelegram Pro admin v{detect_version()} listening on http://{HOST}:{PORT}", flush=True)
    print(f"[admin] token file: {ADMIN_TOKEN_FILE} (mode 0600, root only)", flush=True)
    print(f"[admin] SSH tunnel: ssh -L {PORT}:127.0.0.1:{PORT} root@SERVER_IP", flush=True)
    print("[admin] token: cat /opt/gotelegram-admin/token", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
