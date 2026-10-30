#!/usr/bin/env python3
"""goDNS — веб-панель управления (godns-web).

Один процесс, два слушателя:
  • публичный — 0.0.0.0:<webui_port>, TLS тем же сертификатом Let's Encrypt,
    что у dnsdist (только чтение, копий приватного ключа НЕ делается);
    панель живёт под секретным путём, всё остальное — 404 без подсказок;
  • локальный — 127.0.0.1:<webui_local_port>, без TLS: аварийный вход по
    SSH-туннелю. Токен принимается ТОЛЬКО здесь.

nginx не используется вовсе: conf.d — общий с соседями каталог, а на
чистом goDNS-сервере nginx и не установлен. Свой порт, свой сокет.

Действия выполняются через bridge.sh — те же функции lib/*.sh, что зовёт
SSH-меню; сюда возвращается их захваченный вывод. Никаких секретов в argv:
/proc/<pid>/cmdline читает любой локальный пользователь.
"""

from __future__ import annotations

import hashlib
import html
import hmac
import json
import mimetypes
import os
import re
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import concurrent.futures
import threading
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# ── Пути. Всё перекрывается окружением — так же, как ${VAR:-…} в lib/*.sh:
# иначе тесты молча читали бы настоящий /etc/godns. ────────────────────────
GODNS_ETC = Path(os.getenv("GODNS_ETC", "/etc/godns"))
GODNS_DIR = Path(os.getenv("GODNS_DIR", "/opt/godns"))
GODNS_CONFIG = Path(os.getenv("GODNS_CONFIG", str(GODNS_DIR / "config.json")))
WEB_DIR = Path(os.getenv("GODNS_WEB_DIR", str(GODNS_DIR / "web")))
STATIC_DIR = Path(os.getenv("GODNS_WEB_STATIC", str(WEB_DIR / "static")))
BRIDGE = Path(os.getenv("GODNS_WEB_BRIDGE", str(WEB_DIR / "bridge.sh")))
PROGRAM_DIR = Path(os.getenv("GODNS_PROGRAM_DIR", "/opt/godns-installer"))
STATS_FILE = Path(os.getenv("GODNS_STATS_FILE", "/var/lib/godns/stats.tsv"))
TOKEN_FILE = Path(os.getenv("GODNS_WEB_TOKEN_FILE", str(GODNS_ETC / "webui" / "token")))
BOT_ENV = Path(os.getenv("GODNS_WEB_BOT_ENV", str(GODNS_ETC / "bot.env")))
LE_ROOT = Path(os.getenv("LE_ROOT", "/etc/letsencrypt"))
BACKUP_DIR = Path(os.getenv("GODNS_BACKUP_DIR", "/root/godns-backups"))

# Показывается, только если в config.json нет godns_version. Отставал на
# 27 выпусков (0.5.3) и молча врал бы на экране «О программе». Держится
# равным GODNS_VERSION структурным стражем — иначе снова отстанет.
VERSION_FALLBACK = "1.0.1"  # живое значение читается из config.json

TOKEN_HEADER = "X-GoDNS-Token"
TOKEN_PARAM = "token"
# hex / urlsafe-base64 / uuid — всё, что не искалечится в ?token=…
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.~=-]{16,256}$")

MINIAPP_INITDATA_HEADER = "X-Telegram-Init-Data"
MINIAPP_MAX_AGE_SECONDS = 24 * 3600

ACCESS_LOG = os.getenv("GODNS_WEB_ACCESS_LOG", "0") == "1"


class BadRequest(ValueError):
    """Плохой запрос от клиента: отвечаем 400, а не падаем в 500."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════
# config.json — читаем сами (python3 есть везде, jq в системе нет).
# Кэш по mtime: панель опрашивается каждые 10 секунд.
# ═══════════════════════════════════════════════════════════════════════
_CFG_LOCK = threading.Lock()
_CFG_STAMP: tuple[int, int] | None = None
_CFG: dict[str, Any] = {}


def load_config() -> dict[str, Any]:
    global _CFG_STAMP, _CFG
    try:
        st = GODNS_CONFIG.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    with _CFG_LOCK:
        if stamp is not None and stamp == _CFG_STAMP:
            return _CFG
        data: dict[str, Any] = {}
        try:
            with GODNS_CONFIG.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                data = raw
        except Exception:
            data = {}
        _CFG = data
        _CFG_STAMP = stamp
        return _CFG


def cfg(key: str, default: str = "") -> str:
    v = load_config().get(key, default)
    if v is None:
        return default
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


# ═══════════════════════════════════════════════════════════════════════
# Запуск внешних команд: убиваем всю группу процессов, не только bash —
# иначе внуки (dig, curl, python3) переживают таймаут и держат ресурсы.
# ═══════════════════════════════════════════════════════════════════════
def _kill_process_tree(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run(cmd: list[str], timeout: int = 30, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    try:
        proc = subprocess.Popen(
            cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, env=env,
        )
    except Exception as exc:  # pragma: no cover — зависит от системы
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
        return 124, stdout or "", ((stderr or "") + "\n" + wt("web_cmd_timeout", timeout)).strip()
    except Exception as exc:  # pragma: no cover
        _kill_process_tree(proc)
        return 125, "", str(exc)


# ── ANSI и спиннеры: вывод bash-функций идёт в браузер чистым текстом ────
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    out_lines = []
    for line in (text or "").split("\n"):
        # run_with_progress рисует спиннер через \r — оставляем последний кадр
        if "\r" in line:
            line = line.split("\r")[-1]
        out_lines.append(_ANSI_RE.sub("", line))
    return "\n".join(out_lines).rstrip() + ("\n" if text else "")


# ═══════════════════════════════════════════════════════════════════════
# Мост в bash: те же функции lib/*.sh, что зовёт SSH-меню.
# stdout и stderr склеиваются по порядку появления невозможно — поэтому
# служебные log_* (stderr) показываем ПОСЛЕ полезного stdout.
# ═══════════════════════════════════════════════════════════════════════
def bridge_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GODNS_PROGRAM_DIR"] = str(PROGRAM_DIR)
    env["GODNS_CONFIG"] = str(GODNS_CONFIG)
    env["GODNS_DIR"] = str(GODNS_DIR)
    env["GODNS_ETC"] = str(GODNS_ETC)
    return env


_SETKEY_RE = re.compile(r"setKey\((['\"]).*?\1\)")


def _scrub_console_key(text: str) -> str:
    """Ключ консоли dnsdist не должен доезжать до браузера НИ ОДНИМ путём.

    Маскировка была только в /api/service/config, а инвариант «ключ в
    браузер не отдаём даже админу» нарушался обходной дорогой: провал
    применения эхом печатает строки конфига (dnsdist_dump_lines), и
    setKey('…') уезжал в toast как есть. Демон бота чистит ЛЮБОЙ вывод —
    панель теперь тоже, на единственной горловине bridge()."""
    text = _SETKEY_RE.sub("setKey('***')", text)
    key = cfg("console_key", "")
    if key:
        text = text.replace(key, "***")
    return text


def bridge(action: str, *args: str, timeout: int = 60,
           env: dict[str, str] | None = None) -> tuple[int, str]:
    """(код, текст-для-оператора). Секретов в argv не бывает по построению:
    единственные аргументы — идентификаторы (порт, id провайдера, имя файла).

    env — пары поверх bridge_env(): так ездит путь DoH своего апстрима
    (GODNS_CUSTOM_PATH). /proc/<pid>/environ читает только владелец
    процесса, cmdline — любой локальный пользователь; у чужого резолвера
    путь может быть чужим секретом."""
    full = bridge_env()
    if env:
        full.update({k: str(v) for k, v in env.items()})
    code, out, err = run(["bash", str(BRIDGE), action, *args], timeout=timeout, env=full)
    text = strip_ansi(out)
    tail = strip_ansi(err)
    if tail.strip():
        text = (text.rstrip() + "\n" + tail).lstrip("\n")
    return code, _scrub_console_key(text)


def bridge_data(action: str, *args: str, timeout: int = 30) -> tuple[int, str]:
    """Машинный вывод: stdout как есть (там TSV/JSON), stderr отбрасывается."""
    code, out, _err = run(["bash", str(BRIDGE), action, *args], timeout=timeout, env=bridge_env())
    return code, out


# ═══════════════════════════════════════════════════════════════════════
# Telegram Mini App: проверка подписи initData.
#
# Панель достижима из интернета, и одного неугадываемого пути мало.
# Пускаем только запросы с валидной подписью Telegram: HMAC по схеме
# WebAppData, свежий auth_date и user.id из списка админов бота.
# ═══════════════════════════════════════════════════════════════════════
def _bot_env_lines() -> list[str]:
    try:
        return BOT_ENV.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []


def _bot_token_from_env() -> str:
    # bot.env пишет коллега-бот; принимаем оба употребимых имени ключа.
    for line in _bot_env_lines():
        line = line.strip()
        for key in ("BOT_TOKEN=", "TOKEN="):
            if line.startswith(key):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# Порядок и правило «первый непустой ключ побеждает» повторяют
# botd.ADMIN_ID_KEYS дословно. Раньше панель складывала id из ВСЕХ
# ключей, а демон брал только первый, — и на «грязном» bot.env,
# доставшемся от соседней сборки, человек оказывался админом панели
# (root!), но не бота. Привилегированная сторона тут демон, значит
# семантика его, а не панели.
ADMIN_ID_KEYS = ("ADMIN_IDS", "ALLOWED_IDS", "ADMIN_ID", "CHAT_ID")


def _backup_keep() -> int:
    """Сколько копий оставляет уборка. Источник истины — lib/backup.sh."""
    try:
        raw = (PROGRAM_DIR / "lib" / "backup.sh").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 10
    m = re.search(r'GODNS_BACKUP_KEEP="\$\{GODNS_BACKUP_KEEP:-(\d+)\}"', raw)
    return int(m.group(1)) if m else 10


def _custom_max() -> int:
    """Предел своих апстримов. Источник истины — lib/providers.sh
    (PROV_CUSTOM_MAX): панель лишь прячет форму заранее, а проверяет
    prov_custom_add."""
    try:
        raw = (PROGRAM_DIR / "lib" / "providers.sh").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 8
    m = re.search(r"^PROV_CUSTOM_MAX=(\d+)", raw, re.M)
    return int(m.group(1)) if m else 8


def _bot_admin_ids() -> set[str]:
    env: dict[str, str] = {}
    for line in _bot_env_lines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for key in ADMIN_ID_KEYS:
        raw = env.get(key, "")
        if not raw:
            continue
        ids = {p for p in re.split(r"[,\s]+", raw) if p.isdigit()}
        if ids:
            return ids
    return set()


def verify_telegram_init_data(init_data: str, now: float | None = None) -> str | None:
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
    # По БАЙТАМ: received приходит из init_data, который целиком задаёт
    # клиент. hash=%D0%9F… давал TypeError вместо честного None, а тот
    # всплывал общим 500 с полной трассировкой в журнал — на каждый
    # запрос. Функция проверки подписи обязана возвращать None, а не
    # спотыкаться о враждебный ввод.
    if not hmac.compare_digest(calc.encode("utf-8", "ignore"),
                               received.encode("utf-8", "ignore")):
        return None
    # Протухшая подпись = перехваченная ссылка. Telegram кладёт auth_date всегда.
    try:
        if (now if now is not None else time.time()) - int(data.get("auth_date", "0")) > MINIAPP_MAX_AGE_SECONDS:
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


# ═══════════════════════════════════════════════════════════════════════
# Двухфакторный вход в панель через Telegram-бота.
#
# Схема перенесена из goTelegram (admin-web/server.py) целиком:
#   • из мини-аппа Telegram — пускает подпись initData, код не нужен:
#     Telegram уже доказал, кто пришёл;
#   • из браузера по секретному пути — страница входа, код приходит в личку
#     админам ботом, живёт 5 минут, одноразовый; после ввода — сессионная
#     cookie на 12 часов;
#   • по SSH-туннелю на localhost — токен, аварийный путь на случай, если
#     бот лежит.
#
# Всё состояние в памяти процесса: рестарт панели разлогинивает всех, и это
# осознанно — меньше поверхности, чем хранить сессии на диске.
# ═══════════════════════════════════════════════════════════════════════
TFA_CODE_TTL = 300          # код живёт 5 минут
TFA_MAX_ATTEMPTS = 5        # столько попыток на один код
TFA_REQUEST_COOLDOWN = 30   # не чаще одного кода в 30 секунд
TFA_REQUESTS_PER_HOUR = 5   # и не больше пяти в час с одного адреса
# ГЛОБАЛЬНЫЙ потолок, поверх поадресного. Лимит по client_ip снимался
# сменой адреса: у атакующего с IPv6-подсетью их сколько угодно, и каждый
# «новый» адрес проходил и кулдаун, и часовую квоту — админа заваливало
# кодами в Telegram. Код 2FA один на всю панель, поэтому и потолок общий.
TFA_GLOBAL_PER_HOUR = 30
# Сколько адресов помним. Раньше словарь рос без предела: запись
# вычищалась только при ПОВТОРНОМ заходе того же адреса, а одноразовые
# оставались навсегда.
TFA_IPS_MAX = 512
SESSION_TTL = 12 * 3600
SESSION_COOKIE = "gd_session"
# Билет ?s= оседает в истории браузера, поэтому он одноразовый,
# короткоживущий и никогда не совпадает с самой сессией.
TICKET_TTL = 60

_TFA_LOCK = threading.Lock()
_TFA_STATE: dict[str, Any] = {"hash": "", "expires": 0.0, "attempts": 0, "sent_at": 0.0}
_TFA_REQUESTS: dict[str, list[float]] = {}
# ThreadingHTTPServer правит сессии из разных потоков — без лока параллельный
# вход ронял бы уборку истёкших на «dictionary changed size during iteration».
_SESSION_LOCK = threading.Lock()
# Сессия помнит НЕ только срок. Раньше здесь лежало «токен → срок», и
# guard_request на живой сессии больше ни с чем не сверялся: админа
# убирали из bot.env, чтобы отобрать доступ, а его открытая вкладка
# панели — от root — работала ещё двенадцать часов. Отозвать можно было
# только перезапуском службы. Теперь с сессией хранится ПОКОЛЕНИЕ
# доступа (состав админов + отпечаток аварийного токена) и, если он
# известен, telegram-id хозяина: смена любого из них гасит вкладку на
# следующем же запросе.
_SESSIONS: dict[str, dict] = {}
_TICKETS: dict[str, float] = {}
_GEN_LOCK = threading.Lock()
_GEN_CACHE: dict[str, Any] = {"value": "", "at": 0.0}
_GEN_TTL = 5.0


def auth_generation(now: float | None = None) -> str:
    """Отпечаток того, КОМУ сейчас разрешён вход.

    Читается с диска, поэтому кэшируется на несколько секунд: guard_request
    зовётся на каждый запрос, включая статику.
    """
    now = now if now is not None else time.time()
    with _GEN_LOCK:
        if _GEN_CACHE["value"] and (now - _GEN_CACHE["at"]) < _GEN_TTL:
            return str(_GEN_CACHE["value"])
    ids = ",".join(sorted(_bot_admin_ids()))
    try:
        tok = TOKEN_FILE.read_bytes()
    except OSError:
        tok = b""
    gen = hashlib.sha256(b"gdgen:" + ids.encode() + b"\x00" + tok).hexdigest()[:32]
    with _GEN_LOCK:
        _GEN_CACHE["value"] = gen
        _GEN_CACHE["at"] = now
    return gen


def _tfa_hash(code: str) -> str:
    return hashlib.sha256(("gd2fa:" + code).encode()).hexdigest()


def _sessions_gc(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    with _SESSION_LOCK:
        for tok in [t for t, rec in _SESSIONS.items() if rec.get("exp", 0.0) <= now]:
            _SESSIONS.pop(tok, None)


def session_valid(token: str, now: float | None = None) -> bool:
    if not token:
        return False
    _sessions_gc(now)
    now = now if now is not None else time.time()
    with _SESSION_LOCK:
        rec = _SESSIONS.get(token)
    if not rec or rec.get("exp", 0.0) <= now:
        return False
    if rec.get("gen") != auth_generation(now):
        # Состав админов или аварийный токен изменились — вкладка гаснет.
        with _SESSION_LOCK:
            _SESSIONS.pop(token, None)
        return False
    tg = rec.get("tg") or ""
    if tg and tg not in _bot_admin_ids():
        with _SESSION_LOCK:
            _SESSIONS.pop(token, None)
        return False
    return True


def session_create(now: float | None = None, tg_id: str = "") -> str:
    tok = secrets.token_urlsafe(32)
    now = now if now is not None else time.time()
    with _SESSION_LOCK:
        _SESSIONS[tok] = {
            "exp": now + SESSION_TTL,
            "gen": auth_generation(now),
            "tg": str(tg_id or ""),
        }
    return tok


def ticket_create(now: float | None = None) -> str:
    """Одноразовый короткий пропуск со страницы входа на панель."""
    tok = secrets.token_urlsafe(32)
    now = now if now is not None else time.time()
    with _SESSION_LOCK:
        for old in [t for t, exp in _TICKETS.items() if exp <= now]:
            _TICKETS.pop(old, None)
        _TICKETS[tok] = now + TICKET_TTL
    return tok


def ticket_redeem(token: str, now: float | None = None) -> bool:
    # pop, а не get: билет виден в адресной строке, поэтому годен строго раз.
    if not token:
        return False
    now = now if now is not None else time.time()
    with _SESSION_LOCK:
        exp = _TICKETS.pop(token, None)
    return bool(exp and exp > now)


def _telegram_send(chat_id: str, text: str) -> bool:
    """Код шлёт САМ веб-сервер через Telegram API — не процесс бота.
    Токен уходит в URL запроса к api.telegram.org (стандарт Bot API),
    в argv никаких процессов не попадает."""
    token = _bot_token_from_env()
    if not token:
        return False
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as exc:
        sys.stderr.write(wt("web_2fa_send_failed", exc) + "\n")
        return False


def tfa_request(client_ip: str, now: float | None = None) -> tuple[bool, str]:
    """Сгенерировать код и отправить его админам в Telegram."""
    admins = _bot_admin_ids()
    if not admins:
        return False, wt("web_2fa_no_bot")
    now = now if now is not None else time.time()
    with _TFA_LOCK:
        # Общая уборка на каждом заходе, а не только для своего адреса:
        # иначе одноразовые адреса копятся вечно.
        for ip in [k for k, v in _TFA_REQUESTS.items()
                   if not v or now - v[-1] >= 3600]:
            _TFA_REQUESTS.pop(ip, None)
        hist = [t for t in _TFA_REQUESTS.get(client_ip, []) if now - t < 3600]
        if hist and now - hist[-1] < TFA_REQUEST_COOLDOWN:
            return False, wt("web_2fa_cooldown",
                             int(TFA_REQUEST_COOLDOWN - (now - hist[-1])))
        if len(hist) >= TFA_REQUESTS_PER_HOUR:
            return False, wt("web_2fa_hourly")
        # Потолок поверх поадресного: смена адреса его не обходит.
        total = sum(len(v) for v in _TFA_REQUESTS.values())
        if total >= TFA_GLOBAL_PER_HOUR:
            return False, wt("web_2fa_hourly")
        # И жёсткий предел на размер словаря — на случай шквала с тысяч
        # адресов быстрее, чем истекает час.
        if client_ip not in _TFA_REQUESTS and len(_TFA_REQUESTS) >= TFA_IPS_MAX:
            return False, wt("web_2fa_hourly")
        code = f"{secrets.randbelow(1000000):06d}"
        _TFA_STATE.update({"hash": _tfa_hash(code), "expires": now + TFA_CODE_TTL,
                           "attempts": 0, "sent_at": now})
        hist.append(now)
        _TFA_REQUESTS[client_ip] = hist
    _ip_line = (wt("web_2fa_msg_ttl") if client_ip in {"127.0.0.1", "::1", "?", ""}
                else wt("web_2fa_msg_ttl_ip", client_ip))
    text = (wt("web_2fa_msg_head", code) + "\n"
            + _ip_line + "\n"
            + wt("web_2fa_msg_tail"))
    # Шлём ВСЕМ админам и считаем успехом, если дошло хоть кому-то: код,
    # ушедший только первому, оставлял бы второго админа без права входа.
    #
    # Параллельно и с ОБЩИМ потолком. Последовательная рассылка держала
    # HTTP-поток до 15 с НА КАЖДОГО админа — а маршрут открыт до
    # аутентификации, и вместе с отсутствием потолка потоков у
    # ThreadingHTTPServer это множитель для забивания сервера. Теперь
    # запрос держится не дольше ~12 с при любом числе админов.
    sent = False
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=min(8, len(admins)))
    futs = [pool.submit(_telegram_send, a, text) for a in sorted(admins)]
    try:
        for f in concurrent.futures.as_completed(futs, timeout=12):
            try:
                if f.result():
                    sent = True
            except Exception:
                pass
    except concurrent.futures.TimeoutError:
        pass
    # Не ждём отставших: их потоки сами умрут по таймауту urlopen.
    pool.shutdown(wait=False, cancel_futures=True)
    if not sent:
        with _TFA_LOCK:
            _TFA_STATE.update({"hash": "", "expires": 0.0})
            # Код никому не ушёл — возвращаем квоту: лежащий бот не должен
            # запирать вход на час без единого отправленного кода.
            hist = _TFA_REQUESTS.get(client_ip)
            if hist and now in hist:
                hist.remove(now)
        return False, wt("web_2fa_not_sent")
    return True, wt("web_2fa_sent")


def tfa_verify(code: str, now: float | None = None) -> tuple[bool, str]:
    code = (code or "").strip()
    now = now if now is not None else time.time()
    with _TFA_LOCK:
        st = _TFA_STATE
        if not st["hash"] or st["expires"] <= now:
            return False, wt("web_2fa_expired")
        if st["attempts"] >= TFA_MAX_ATTEMPTS:
            st["hash"] = ""
            return False, wt("web_2fa_too_many")
        st["attempts"] += 1
        if not hmac.compare_digest(_tfa_hash(code).encode("ascii", "ignore"),
                                   str(st["hash"]).encode("ascii", "ignore")):
            left = TFA_MAX_ATTEMPTS - st["attempts"]
            return False, wt("web_2fa_wrong", max(0, left))
        # одноразовый: гасим сразу после успеха
        st["hash"] = ""
        st["expires"] = 0.0
    return True, "ok"


# ═══════════════════════════════════════════════════════════════════════
# Токен аварийного входа (SSH-туннель). Файл 0600, каталог 0700.
# ═══════════════════════════════════════════════════════════════════════
_TOKEN_LOCK = threading.Lock()
_ADMIN_TOKEN: str | None = None


def _read_token_file() -> str:
    try:
        raw = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return raw if TOKEN_RE.match(raw) else ""


def _create_token_file() -> str:
    token = secrets.token_urlsafe(32)
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(TOKEN_FILE.parent, 0o700)
        except OSError:
            pass
        fd = os.open(str(TOKEN_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
        os.chmod(TOKEN_FILE, 0o600)
    except OSError as exc:  # pragma: no cover — зависит от ФС
        print(wt("web_token_save_failed", TOKEN_FILE, exc), flush=True)
    return token


def admin_token() -> str:
    global _ADMIN_TOKEN
    with _TOKEN_LOCK:
        if _ADMIN_TOKEN:
            return _ADMIN_TOKEN
        _ADMIN_TOKEN = _read_token_file() or _create_token_file()
        return _ADMIN_TOKEN


# ═══════════════════════════════════════════════════════════════════════
# Защита от DNS-rebinding: страница на чужом origin может резолвить своё
# имя в наш адрес и ходить в панель через туннель оператора или напрямую.
# Пускаем только настоящие loopback-имена и наш собственный домен.
# ═══════════════════════════════════════════════════════════════════════
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def host_name_only(raw: str) -> str:
    value = raw.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[:end + 1] if end > 0 else value
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def allowed_hosts() -> set[str]:
    hosts = set(LOOPBACK_HOSTS)
    dom = cfg("domain").strip().lower()
    if dom:
        hosts.add(dom)
    return hosts


# ═══════════════════════════════════════════════════════════════════════
# Страница входа. Инлайновая: ей не нужны ассеты панели, а стиль — те же
# токены «зелёного терминала», что в static/theme.css.
# raw-строка: в JS-регэкспах есть \/ — в обычной строке это SyntaxWarning.
# ═══════════════════════════════════════════════════════════════════════
LOGIN_PAGE_HTML = r"""<!doctype html>
<html lang="{{__lang__}}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{{web_login_title}}</title>
<meta name="robots" content="noindex, nofollow">
<script>
 // SDK мини-аппа — только внутри Telegram. Безусловная загрузка сообщала
 // сторонней стороне, что страницу входа открыли, и с какого адреса.
 (function(){var inTg=/[?#].*tgWebApp/.test(location.href)
   ||typeof window.TelegramWebviewProxy!=='undefined'
   ||(window.top!==window.self&&document.referrer.indexOf('telegram.org')!==-1);
  if(!inTg)return;var s=document.createElement('script');
  s.src='https://telegram.org/js/telegram-web-app.js';s.async=false;
  document.head.appendChild(s);})();
</script>
<style>
 :root{--bg:#080b0a;--panel:#0e1512;--panel-2:#0a100d;--line:#1c2a24;--line-2:#2a4033;
   --fg:#c9d6cf;--hi:#eaf3ee;--dim:#8b9f94;--ok:#3fb950;--bad:#ff5f56;--cyan:#56d4dd;
   --accent-ink:#05130a;--font:'JetBrains Mono',ui-monospace,'SFMono-Regular',Menlo,Consolas,monospace}
 body{background:var(--bg);color:var(--fg);font:400 14px/1.55 var(--font);
      display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
 .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
       padding:26px;max-width:380px;width:92%;box-sizing:border-box}
 h1{font-size:15px;margin:0 0 6px;color:var(--hi)}
 h1 b{color:var(--ok)}
 p{color:var(--dim);font-size:12.5px;margin:0 0 16px}
 input{width:100%;box-sizing:border-box;background:var(--panel-2);border:1px solid var(--line-2);
       color:var(--hi);border-radius:8px;padding:12px;font:700 20px var(--font);
       letter-spacing:6px;text-align:center;outline:none}
 input:focus{border-color:var(--ok)}
 button{width:100%;margin-top:10px;border-radius:8px;padding:12px;font:700 13px var(--font);
        cursor:pointer;text-transform:uppercase;letter-spacing:.4px}
 #go{background:var(--ok);color:var(--accent-ink);border:0}
 #req{background:transparent;color:var(--fg);border:1px solid var(--line-2)}
 #req:hover{border-color:var(--ok);color:var(--hi)}
 .msg{margin-top:12px;font-size:12.5px;min-height:18px}
 .err{color:var(--bad)} .ok{color:var(--ok)}
</style></head><body>
<div class="card">
  <h1>▸ go<b>DNS</b> · {{ui_title_panel}}</h1>
  <p>{{web_login_hint}}</p>
  <button id="req" type="button">{{web_login_send}}</button>
  <div style="height:10px"></div>
  <input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000">
  <button id="go" type="button">{{web_login_go}}</button>
  <div class="msg" id="msg"></div>
</div>
<script>
const msg=document.getElementById('msg');
function show(t,cls){msg.textContent=t;msg.className='msg '+(cls||'');}
// Страница живёт под секретным путём, поэтому абсолютный '/api/…' ушёл бы
// мимо префикса — на голый 404. Клеим путь текущей страницы.
const BASE=location.pathname.replace(/\/[^/]*$/,'').replace(/\/$/,'');
async function post(url,body){
  const r=await fetch(BASE+url,{method:'POST',headers:{'Content-Type':'application/json',
    'X-GoDNS-Admin':'1'},body:JSON.stringify(body||{})});
  return r.json().catch(()=>({ok:false,error:'{{web_login_badjson}}'}));
}
// Открыли из Telegram — подпись уже есть, спрашивать код незачем.
(async()=>{
  try{
    const wa=window.Telegram&&window.Telegram.WebApp;
    if(!wa||!wa.initData) return;
    wa.ready();
    show('{{web_login_via_tg}}');
    const r=await post('/api/auth/miniapp',{init_data:wa.initData});
    if(r.ok){
      show('{{web_login_ok}}','ok');
      location.replace(BASE+'/'+(r.ticket?('?s='+encodeURIComponent(r.ticket)):''));
      return;
    }
    show(r.error||'{{web_login_sig_fail}}','err');
  }catch(e){/* обычный браузер: остаётся вход по коду */}
})();
document.getElementById('req').onclick=async()=>{
  show('{{web_login_sending}}');
  const r=await post('/api/auth/request');
  show(r.ok?'{{web_2fa_sent}}':(r.error||'{{web_login_failed}}'), r.ok?'ok':'err');
};
document.getElementById('go').onclick=async()=>{
  const code=document.getElementById('code').value.trim();
  if(!/^[0-9]{6}$/.test(code)){show('{{web_login_six}}','err');return;}
  show('{{web_login_checking}}');
  const r=await post('/api/auth/verify',{code});
  if(r.ok){show('{{web_login_ok}}','ok');location.reload();}
  else show(r.error||'{{web_login_wrong}}','err');
};
document.getElementById('code').addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('go').click();});
</script></body></html>"""


# ═══════════════════════════════════════════════════════════════════════
# Данные разделов (чтение — прямо здесь, действия — через bridge).
# ═══════════════════════════════════════════════════════════════════════
WINDOW_KEYS = ("1m", "15m", "1h", "1d", "7d")
POLICY_KEYS = ("roundrobin", "fastest")


def window_label(name: str) -> str:
    # Ключи перечислены явно, а не собраны из кусков: собранный ключ не
    # виден ни grep'ом, ни проверкой строк, и пропажу перевода замечает
    # уже пользователь.
    return {
        "1m": lambda: wt("web_win_1m"),
        "15m": lambda: wt("web_win_15m"),
        "1h": lambda: wt("web_win_1h"),
        "1d": lambda: wt("web_win_1d"),
        "7d": lambda: wt("web_win_7d"),
    }.get(name, lambda: name)()


def policy_label(name: str) -> str:
    return wt("web_pol_rr") if name == "roundrobin" else (
        wt("web_pol_fastest") if name == "fastest" else name)


def stats_payload() -> dict[str, Any] | None:
    """python3 lib/stats.py report — тот же расчёт, что у SSH-панели."""
    stats_py = PROGRAM_DIR / "lib" / "stats.py"
    if not stats_py.is_file() or not STATS_FILE.is_file():
        return None
    code, out, _err = run([sys.executable or "python3", str(stats_py), "report", str(STATS_FILE)], timeout=15)
    if code != 0 or not out.strip():
        return None
    windows = []
    series: dict[str, Any] = {}
    spark_1h = spark_24h = None
    peak_1h = peak_24h = None
    last_ago: int | None = None
    for line in out.splitlines():
        f = line.split("\t")
        if not f:
            continue
        if f[0] == "w" and len(f) >= 8:
            name = f[1]
            windows.append({
                "name": name,
                "label": window_label(name),
                "count": None if f[2] == "-" else int(f[2]),
                "human": f[3],
                "mark": f[4],
                "rate": None if f[5] == "-" else f[5],
                "cache": None if f[6] == "-" else int(f[6]),
                "fails": None if f[7] == "-" else int(f[7]),
            })
        elif f[0] == "spark" and len(f) >= 3:
            spark_1h = f[1]
            peak_1h = None if f[2] == "-" else int(f[2])
        elif f[0] == "spark24" and len(f) >= 3:
            spark_24h = f[1]
            peak_24h = None if f[2] == "-" else int(f[2])
        elif f[0] == "series" and len(f) >= 6:
            # Числа ряда для графика. Терминалу они не нужны — он рисует
            # блочную строку, — но панели нужен именно ряд, иначе график
            # пришлось бы восстанавливать из символов, что невозможно:
            # блок несёт восемь уровней, а не значение.
            name, step, end, body, peak = f[1], f[2], f[3], f[4], f[5]
            if step != "-" and body != "-":
                series[name] = {
                    "step": int(step),
                    "end": int(end),
                    "values": [None if v == "-" else int(v) for v in body.split(",")],
                    "peak": None if peak == "-" else int(peak),
                }
        elif f[0] == "last" and len(f) >= 2 and f[1] != "-":
            last_ago = int(f[1])
    return {
        "windows": windows,
        "spark_1h": spark_1h, "peak_1h": peak_1h,
        "spark_24h": spark_24h, "peak_24h": peak_24h,
        "series": series,
        "last_ago": last_ago,
    }


def _systemctl_show(unit: str, props: list[str]) -> dict[str, str]:
    code, out, _err = run(["systemctl", "show", unit, "-p", ",".join(props)], timeout=10)
    res: dict[str, str] = {}
    if code == 0:
        for line in out.splitlines():
            k, _, v = line.partition("=")
            if k:
                res[k] = v
    return res


def service_state() -> str:
    show = _systemctl_show("dnsdist", ["ActiveState", "UnitFileState"])
    if show.get("ActiveState") == "active":
        return "running"
    # юнита нет вовсе → not-found / пустой UnitFileState
    if show.get("UnitFileState") in ("", "not-found", None):
        # бинарник может быть установлен, но служба не заведена
        code, _o, _e = run(["bash", "-lc", "command -v dnsdist"], timeout=5)
        return "stopped" if code == 0 else "not_installed"
    return "stopped"


def cert_paths() -> tuple[str, str]:
    """(fullchain, privkey) — та же логика, что dnsdist_cert_file/key_file."""
    dom = cfg("domain")
    cert = cfg("cert_path") or cfg("cert_fullchain")
    key = cfg("key_path") or cfg("cert_key")
    if not (cert and Path(cert).is_file()) and dom:
        cand = LE_ROOT / "live" / dom / "fullchain.pem"
        if cand.is_file():
            cert = str(cand)
    if not (key and Path(key).is_file()) and dom:
        cand = LE_ROOT / "live" / dom / "privkey.pem"
        if cand.is_file():
            key = str(cand)
    return cert or "", key or ""


def cert_days_left(cert: str) -> int | None:
    if not cert or not Path(cert).is_file():
        return None
    code, out, _err = run(["openssl", "x509", "-in", cert, "-noout", "-enddate"], timeout=10)
    if code != 0:
        return None
    m = re.search(r"notAfter=(.+)", out)
    if not m:
        return None
    try:
        exp = datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int((exp - datetime.now(timezone.utc)).total_seconds() // 86400)


def cert_meta(cert: str) -> dict[str, Any]:
    meta: dict[str, Any] = {"san": "", "issuer": ""}
    if not cert or not Path(cert).is_file():
        return meta
    code, out, _err = run(["openssl", "x509", "-in", cert, "-noout", "-issuer", "-ext", "subjectAltName"], timeout=10)
    if code == 0:
        m = re.search(r"issuer=(.+)", out)
        if m:
            meta["issuer"] = m.group(1).strip()
        sans = re.findall(r"DNS:([^,\s]+)", out)
        meta["san"] = ", ".join(sans)
    return meta


def renew_hook_state() -> str:
    if (LE_ROOT / "renewal-hooks" / "deploy" / "50-godns.sh").is_file():
        return "certbot"
    show = _systemctl_show("godns-cert-reload.path", ["ActiveState"])
    if show.get("ActiveState") == "active":
        return "acme_path_unit"
    return "none"


_DRIFT_LOCK = threading.Lock()
_DRIFT: tuple[Any, bool] | None = None  # (штамп config.json, ответ)


def drift() -> bool:
    """Есть ли непринятые изменения. Считает bash (godns_config_fingerprint) —
    в питоне дублировать формулу нельзя: разъедутся молча. Кэш по mtime
    конфига: панель опрашивается каждые 10 секунд, а ответ меняется только
    когда меняется config.json."""
    global _DRIFT
    try:
        st = GODNS_CONFIG.stat()
        stamp: Any = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    with _DRIFT_LOCK:
        if _DRIFT is not None and _DRIFT[0] == stamp:
            return _DRIFT[1]
    code, _out = bridge_data("drifted", timeout=20)
    val = code == 0
    with _DRIFT_LOCK:
        _DRIFT = (stamp, val)
    return val


def doh_url() -> str:
    dom, port, path = cfg("domain"), cfg("doh_port"), cfg("doh_path").lstrip("/")
    if not dom or not port:
        return ""
    return f"https://{dom}:{port}/{path}"


def upstream_names() -> list[dict[str, str]]:
    ids = [i for i in cfg("upstream_ids").split(",") if i.strip()]
    if not ids:
        return []
    catalog = {c["id"]: c for c in upstream_catalog()}
    out = []
    for i in ids:
        c = catalog.get(i.strip())
        out.append({"id": i.strip(), "name": (c or {}).get("name", i.strip()),
                    "filter": (c or {}).get("filter", "")})
    return out


_CATALOG_LOCK = threading.Lock()
_CATALOG: list[dict[str, Any]] = []
_CATALOG_AT = 0.0


def upstream_catalog() -> list[dict[str, Any]]:
    """Каталог провайдеров из lib/providers.sh (bridge). Кэш на 10 минут:
    таблица статична в пределах запуска — пока свой апстрим не добавили
    или не удалили; те пути зовут _catalog_invalidate()."""
    global _CATALOG, _CATALOG_AT
    with _CATALOG_LOCK:
        if _CATALOG and time.time() - _CATALOG_AT < 600:
            return _CATALOG
    code, out = bridge_data("catalog", timeout=30)
    items: list[dict[str, Any]] = []
    if code == 0:
        for line in out.splitlines():
            f = line.split("\t")
            if len(f) >= 5:
                # Поля 5–8 (custom, proto, addr, sni) — необязательные:
                # старый мост печатает пять, и запись каталога без них —
                # обычный DoT-провайдер. Путь DoH сюда не приходит никогда.
                items.append({
                    "id": f[0], "name": f[1], "filter": f[2],
                    "filter_label": f[3], "region": f[4],
                    "custom": len(f) > 5 and f[5] == "yes",
                    "proto": (f[6] if len(f) > 6 and f[6] else "dot"),
                    "addr": f[7] if len(f) > 7 else "",
                    "sni": f[8] if len(f) > 8 else "",
                })
    with _CATALOG_LOCK:
        if items:
            _CATALOG = items
            _CATALOG_AT = time.time()
        return _CATALOG


def _catalog_invalidate() -> None:
    """Сбросить кэш каталога. Без этого новая своя запись не появлялась бы
    в таблице десять минут, а POST /api/upstreams отвергал бы её как
    web_ups_unknown — «добавил, а включить нельзя»."""
    global _CATALOG_AT
    with _CATALOG_LOCK:
        _CATALOG_AT = 0.0



# Лок обязателен: обработчики ThreadingHTTPServer параллельны, и кэш без
# лока давал «стадо» при протухании (несколько bridge-подпроцессов разом).
_EXPIRY_LOCK = threading.Lock()
_EXPIRY_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
EXPIRY_TTL = 6 * 3600


def expiry_dates() -> dict[str, str]:
    """Два РАЗНЫХ срока: до какого числа оплачен домен и годен сертификат.

    Раньше панель показывала одно «89 дн.» — число без даты, да ещё рядом
    с именем домена, отчего читалось как срок ДОМЕНА. Сроки не связаны и
    чинятся по-разному: сертификат — перевыпуском за минуту, регистрация —
    у регистратора и деньгами.

    Сети не касаемся: мост читает кэш WHOIS, а протухший обновляет в фоне.
    """
    # ГРАБЛИ: раздел «Состояние» опрашивается каждые десять секунд, и без
    # кэша КАЖДЫЙ опрос запускал bash-мост с openssl. Сроки меняются раз в
    # сутки — держать ради них подпроцесс в горячем пути незачем. Это тот
    # же класс, что и сотня подпроцессов на нажатие клавиши в списке
    # провайдеров: поодиночке дёшево, в цикле — заметно.
    now = time.time()
    # Лок держим и на ПРОМАХЕ целиком: без этого несколько потоков разом
    # видели протухший кэш и запускали несколько bash-мостов («стадо») —
    # ровно то, от чего кэш заводили.
    with _EXPIRY_LOCK:
        cached = _EXPIRY_CACHE.get("value")
        if cached is not None and (now - _EXPIRY_CACHE["at"]) < EXPIRY_TTL:
            return dict(cached)

        out = {"registered": "", "reg_status": "pending", "cert": ""}
        code, text = bridge_data("expiry", timeout=15)
        if code != 0:
            # Неудачу тоже запоминаем, но ненадолго: иначе отказ моста
            # означал бы попытку на каждый опрос.
            _EXPIRY_CACHE.update({"at": now - EXPIRY_TTL + 300, "value": dict(out)})
            return out
        for line in text.splitlines():
            if "\t" not in line:
                continue
            key, _, value = line.partition("\t")
            value = value.strip()
            if key == "cert":
                out["cert"] = value
            elif key == "registered":
                parts = value.split()
                out["reg_status"] = parts[0] if parts else "pending"
                out["registered"] = parts[1] if len(parts) > 1 else ""
        # «pending» — ответ WHOIS ещё в пути; перечитываем скоро, а не через
        # шесть часов, иначе дата появится только назавтра.
        ttl_at = now if out["reg_status"] != "pending" else now - EXPIRY_TTL + 60
        _EXPIRY_CACHE.update({"at": ttl_at, "value": dict(out)})
        return out


def parse_flag(value: Any) -> bool | None:
    """Строгий разбор булева из тела запроса; None — не разобрали.

    ГРАБЛИ: было `bool(body.get("enabled"))`, а в Python непустая строка
    истинна — то есть `{"enabled": "no"}` означало ДА. Клиент, пишущий
    скрипт против этого API, выключал бы DoT и молча его ВКЛЮЧАЛ, получая
    в ответ бодрое 200. Фронт шлёт настоящий булев и не страдал, но API
    обязан быть однозначным для всех, а не только для своей же страницы.
    Непонятное значение — отказ, а не догадка.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("yes", "true", "on", "1"):
            return True
        if v in ("no", "false", "off", "0"):
            return False
    return None


# ── Свой апстрим: синтаксический фильтр на границе процесса ─────────────
# Те же регэкспы, что в godns-bot/botd.py (их тождество держит test_bot.sh).
# Граница отсекает мусор и разделители записи; окончательно судит lib:
# valid_ip, valid_domain, valid_port, _prov_valid_sni, _prov_valid_path.
CU_PROTO = ("dot", "doh", "plain")
CU_HOST_RE = re.compile(r"^[A-Za-z0-9:][A-Za-z0-9.:-]{0,252}$")   # имя или IP-литерал
CU_PORT_RE = re.compile(r"^[1-9][0-9]{0,4}$")                      # плюс int(port) <= 65535
CU_PATH_RE = re.compile(r"^/[A-Za-z0-9._~%/-]{0,127}$")            # = _prov_valid_path
CU_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f|;]{1,40}$")             # | и ; — разделители записи
CU_ID_RE = re.compile(r"^custom-[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")

# Проба — синхронный POST, и одна за раз: ей нужен структурированный ответ
# (ip / private / needs_ack), а задание отдаёт только текст. Занято → 409.
_PROBE_LOCK = threading.Lock()

_KV_LINE_RE = re.compile(r"^([a-z_]+)\t(.*)$")


def _cu_fields(body: dict[str, Any]) -> tuple[str, str, str, str]:
    """proto, host, port, path из тела запроса; негодное — BadRequest (400).

    Порт и путь пустыми быть могут (умолчание подставит lib), хост — нет.
    Значение читаем через `is None`, а не `or ""`: JSON-число 0 в порту —
    это негодный порт, а не «по умолчанию».
    """
    def field(name: str) -> str:
        v = body.get(name)
        return "" if v is None else str(v).strip()

    proto = field("proto").lower()
    if proto not in CU_PROTO:
        raise BadRequest(wt("web_cu_bad_proto"))
    host = field("host")
    if not CU_HOST_RE.match(host):
        raise BadRequest(wt("web_cu_bad_host"))
    port = field("port")
    if port and (not CU_PORT_RE.match(port) or int(port) > 65535):
        raise BadRequest(wt("web_cu_bad_port"))
    path = field("path")
    if path and not CU_PATH_RE.match(path):
        raise BadRequest(wt("web_cu_bad_path"))
    if proto != "doh":
        path = ""
    return proto, host, port, path


def _kv_lines(text: str) -> tuple[dict[str, str], str]:
    """Машинные строки «ключ<TAB>значение» — в словарь, остальное — текст
    для оператора. Один вызов bridge() отдаёт и то и другое: bridge_data()
    выбросил бы stderr, а причина отказа (log_error) живёт именно там."""
    kv: dict[str, str] = {}
    rest: list[str] = []
    for line in (text or "").splitlines():
        m = _KV_LINE_RE.match(line)
        if m:
            kv[m.group(1)] = m.group(2).strip()
        else:
            rest.append(line)
    out = "\n".join(rest).strip("\n")
    return kv, (out + "\n") if out else ""


# ── Язык панели ─────────────────────────────────────────────────────────
# Панель обязана говорить на том языке, который выбрали при установке.
# Маркер /opt/godns/.language читается напрямую (сервер работает от root, а
# сам файл 0644 — его же читает и бот). Словарь собирается из
# .i18n-keys/webui-ui*.tsv в static/lang/{ru,en}.json генератором проекта.
#
# Словарь ВПЕЧАТЫВАЕТСЯ в index.html, а не загружается отдельным запросом:
# иначе между отрисовкой и ответом человек видит непереведённый интерфейс,
# а на секретном пути остаётся лишняя строка в журнале доступа.
LANG_MARKER = Path(os.getenv("GODNS_LANG_MARKER", str(GODNS_DIR / ".language")))
LANG_DIR = STATIC_DIR / "lang"
_LANG_LOCK = threading.Lock()
_LANG_CACHE: dict[str, Any] = {"code": "", "dict": {}, "at": 0.0}
LANG_TTL = 30.0


def panel_lang() -> str:
    """Код языка панели: ru | en. Незнакомое значение — русский."""
    try:
        code = LANG_MARKER.read_text(encoding="utf-8").strip().lower()[:2]
    except OSError:
        code = ""
    if code not in ("ru", "en"):
        code = cfg("language", "ru")
    return code if code in ("ru", "en") else "ru"


def panel_dict() -> tuple[str, dict[str, str]]:
    """(код языка, словарь). Кэш на TTL: файл читается не на каждый запрос,
    но смена языка доезжает без перезапуска панели."""
    now = time.time()
    with _LANG_LOCK:
        if _LANG_CACHE["dict"] and (now - _LANG_CACHE["at"]) < LANG_TTL:
            return _LANG_CACHE["code"], _LANG_CACHE["dict"]
    code = panel_lang()
    data: dict[str, str] = {}
    try:
        data = json.loads((LANG_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    with _LANG_LOCK:
        _LANG_CACHE.update(code=code, **{"dict": data}, at=now)
    return code, data


def wt(key: str, *args: Any) -> str:
    """Строка панели для сообщений, которые печатает САМ сервер.

    Ключа нет — возвращаем ключ: пропущенный перевод видно сразу, а не
    «пусто вместо фразы». Тот же приём, что в оболочке и в боте.
    """
    _code, data = panel_dict()
    out = data.get(key)
    if out is None:
        # ГРАБЛИ: раньше возвращался просто ключ — и аргументы ПРОПАДАЛИ.
        # Для подписи это терпимо, а для сообщения с данными смертельно:
        # «Код входа: 123456» превращался в «web_2fa_msg_head», и человек
        # получал письмо БЕЗ кода. Потерять перевод можно, потерять данные
        # нельзя, поэтому в запасном виде дописываем аргументы.
        return " ".join([key] + [str(a) for a in args])
    for i, a in enumerate(args):
        out = out.replace("{%d}" % i, str(a))
    return out


def overview_payload() -> dict[str, Any]:
    dom = cfg("domain")
    cert, _key = cert_paths()
    policy = cfg("policy")
    dot_on = cfg("dot_enabled", "no") == "yes"
    _exp = expiry_dates() if dom else {"registered": "", "reg_status": "pending", "cert": ""}
    return {
        "service": service_state(),
        "engine": "dnsdist",
        "version": cfg("godns_version", VERSION_FALLBACK),
        "domain": dom,
        "cert_days": cert_days_left(cert) if dom else None,
        # Даты, а не только «сколько осталось»: число без даты не отвечает
        # на вопрос «а когда именно», и рядом с доменом читается как его срок.
        "cert_until": _exp["cert"],
        "domain_paid_until": _exp["registered"],
        "domain_paid_status": _exp["reg_status"],
        "doh_url": doh_url(),
        # Инвариант проекта: адрес DoT не показывается, пока dot_enabled=no.
        "dot_enabled": dot_on,
        "dot_addr": (f"{dom}:{cfg('dot_port', '853')}" if (dot_on and dom) else ""),
        "policy": policy,
        "policy_label": policy_label(policy),
        "upstreams": upstream_names(),
        "drift": drift(),
        "stats": stats_payload(),
    }


# ── Замер задержек: результат последнего прогона держим в памяти ─────────
_MEASURE_LOCK = threading.Lock()
_MEASURE: dict[str, Any] = {}


def parse_bench_json(raw: str) -> dict[str, Any]:
    res: dict[str, Any] = {}
    try:
        data = json.loads(raw)
    except Exception:
        return res
    for pid, r in (data.get("results") or {}).items():
        if not isinstance(r, dict):
            continue
        res[pid] = {
            "warm": int(round(r["warm_ms"])) if isinstance(r.get("warm_ms"), (int, float)) else None,
            "cold": int(round(r["cold_ms"])) if isinstance(r.get("cold_ms"), (int, float)) else None,
            "probes": r.get("probes", 0),
            "probes_ok": r.get("probes_ok", 0),
            "degraded": bool(r.get("degraded")),
            "error": str(r.get("error_class") or "") or None,
        }
    return res


# ═══════════════════════════════════════════════════════════════════════
# Задания: долгие действия (замер, полная проверка) идут в фоне,
# фронт опрашивает /api/jobs/<id>. Вывод копится в файле спула.
# ═══════════════════════════════════════════════════════════════════════
_JOBS_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}


def job_get(jid: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        j = _JOBS.get(jid)
        return dict(j) if j else None


def job_try_start(kind: str, worker) -> str | None:
    """Одна критическая секция: занято → None, свободно → зарезервировать
    и запустить. Проверка и вставка под ОДНИМ захватом лока."""
    return _job_start_locked(kind, worker)


def _job_start_locked(kind: str, worker) -> str | None:
    jid = secrets.token_urlsafe(8)
    with _JOBS_LOCK:
        if any(j["kind"] == kind and j["state"] == "running"
               for j in _JOBS.values()):
            return None
        cutoff = time.time() - 3600
        for old in [k for k, v in _JOBS.items()
                    if v.get("done_at", time.time()) < cutoff]:
            _JOBS.pop(old, None)
        _JOBS[jid] = {"kind": kind, "state": "running",
                      "output": "", "started": time.time()}

    def _run() -> None:
        try:
            state, output = worker()
        except Exception as exc:
            state, output = "failed", wt("web_job_internal", exc)
        with _JOBS_LOCK:
            j = _JOBS.get(jid)
            if j is not None:
                j["state"] = state
                j["output"] = output
                j["done_at"] = time.time()

    threading.Thread(target=_run, daemon=True).start()
    return jid


# ── QR и раздача iOS-профиля через сам порт панели ──────────────────────
# Панель уже слушает открытый HTTPS-порт с валидным сертификатом — значит
# профиль iPhone можно отдать через неё, без эфемерного порта и без правки
# файрвола (на nftables мы чужой ruleset не трогаем, и там отдельный порт
# пришлось бы открывать руками). Телефон сканирует QR → Safari скачивает
# профиль по одноразовому токену.
_PROFILE_LOCK = threading.Lock()
_PROFILE_TOKENS: dict[str, dict[str, Any]] = {}
PROFILE_TTL = 600.0
# Потолок: каждый токен держит целый .mobileconfig в памяти, а истёкшие
# убирались только при СОЗДАНИИ следующего — всплеск и тишина оставляли
# мегабайты висеть до TTL.
PROFILE_TOKENS_MAX = 32


def _qr_svg(text: str) -> str:
    """SVG-код QR через qrencode. Текст — ЧЕРЕЗ stdin, не argv: в нём URL
    с секретным путём панели, а /proc/<pid>/cmdline читает любой локальный
    пользователь. Пусто — qrencode не установлен."""
    try:
        proc = subprocess.Popen(
            ["qrencode", "-t", "SVG", "-m", "2", "-o", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        try:
            out, _ = proc.communicate(text, timeout=10)
        except subprocess.TimeoutExpired:
            # Зависший qrencode надо ДОБИТЬ: общий except его не реапил, и
            # процесс с пайпом оставался висеть (в run() группа убивается,
            # здесь — забыли).
            _kill_process_tree(proc)
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return ""
        return out if proc.returncode == 0 and "<svg" in out else ""
    except Exception:
        return ""


def _profile_token_new(data: bytes, now: float | None = None) -> str:
    now = now if now is not None else time.time()
    tok = secrets.token_urlsafe(24)
    with _PROFILE_LOCK:
        for k in [k for k, v in _PROFILE_TOKENS.items() if v["exp"] <= now]:
            _PROFILE_TOKENS.pop(k, None)
        # Переполнение — выбрасываем самый старый: свежий QR нужнее.
        while len(_PROFILE_TOKENS) >= PROFILE_TOKENS_MAX:
            oldest = min(_PROFILE_TOKENS, key=lambda k: _PROFILE_TOKENS[k]["exp"])
            _PROFILE_TOKENS.pop(oldest, None)
        _PROFILE_TOKENS[tok] = {"data": data, "exp": now + PROFILE_TTL}
    return tok


def _profile_token_take(tok: str, now: float | None = None) -> bytes | None:
    now = now if now is not None else time.time()
    with _PROFILE_LOCK:
        rec = _PROFILE_TOKENS.get(tok)
        if not rec or rec["exp"] <= now:
            _PROFILE_TOKENS.pop(tok, None)
            return None
        # Одноразовый: снимаем при первой выдаче. Один скан = одна отдача.
        return _PROFILE_TOKENS.pop(tok)["data"]


# ═══════════════════════════════════════════════════════════════════════
# HTTP-обработчик
# ═══════════════════════════════════════════════════════════════════════
# Мини-апп открывается ВО ФРЕЙМЕ клиента Telegram и требует его SDK —
# разрешаем ровно два источника Telegram и ничего сверх того.
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
    # X-Frame-Options не умеет списки источников и перебивает frame-ancestors —
    # с ним мини-апп во фрейме Telegram не открылся бы. Кликджекинг закрывает CSP.
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
)

# /api/phone/profile открыт для СЕССИИ (телефон её не имеет), но заперт
# одноразовым токеном из QR — как билет ?s=, только отдаёт файл.
OPEN_AUTH_PATHS = {"/api/auth/request", "/api/auth/verify", "/api/auth/miniapp",
                   "/api/phone/profile"}
# Ассеты без секретов отдаются до проверки сессии: их тянет и страница
# входа (favicon), и мини-апп до получения cookie. А вот корень и
# index.html — под охраной: неавторизованному показывается форма входа.
GUARDED_STATIC = {"/", "/index.html"}


# ── Слова подтверждения ─────────────────────────────────────────────────
# ГРАБЛИ: слова были ТОЛЬКО русские, а страница выбирала язык оператора.
# Англоязычный оператор физически не мог подтвердить опасное действие: он
# видел «type DISABLE», а сервер ждал «ВЫКЛЮЧИТЬ». Панель становилась для
# него тупиком ровно там, где нужнее всего быть понятой.
#
# Принимаем ОБА языка независимо от выбранного: слово — это защита от
# случайного нажатия, а не проверка знания языка. Сравнение точное, с
# учётом регистра — иначе смысл «набери именно это» теряется.
CONFIRM_WORDS = {
    "dot_off": ("ВЫКЛЮЧИТЬ", "DISABLE"),
    # Слово то же, что в боте (TYPED_WORDS["rotate_path"]) и в SSH-меню:
    # три интерфейса просили три разных слова за одно и то же действие,
    # и мышечная память не переносилась между ними.
    "rotate":  ("СМЕНИТЬ", "ROTATE"),
    "external": ("СОГЛАСЕН", "I AGREE"),
    "restore": ("ВОССТАНОВИТЬ", "RESTORE"),
    "prune":   ("ПРИБРАТЬ", "PRUNE"),
    # Удаление своего апстрима: если он в рабочем наборе — служба
    # перечитает конфигурацию без него.
    "custom_remove": ("УБРАТЬ", "REMOVE"),
}


def confirm_ok(kind: str, value: Any) -> bool:
    return str(value or "").strip() in CONFIRM_WORDS[kind]


def confirm_word(kind: str) -> str:
    """Слово для сообщения об отказе — на языке панели."""
    ru, en = CONFIRM_WORDS[kind]
    return en if panel_lang() == "en" else ru


def apply_lang(text: str, code: str, data: dict) -> str:
    """Подставить строки языка в шаблон `{{ключ}}`.

    Один и тот же приём для index.html и для страницы входа: обе рисуются
    ДО того, как на странице появится словарь, поэтому переводит их сервер.
    `{{__lang__}}` — код языка для атрибута html lang.
    """
    text = text.replace("{{__lang__}}", code)
    return re.sub(r"\{\{([a-z0-9_]+)\}\}",
                  lambda m: html.escape(data.get(m.group(1), m.group(1)), quote=True),
                  text)


def inject_lang(body: bytes) -> bytes:
    """Впечатать язык и словарь в страницу до первой отрисовки."""
    code, data = panel_dict()
    blob = ("<script>window.__GODNS_LANG__=%s;window.__GODNS_I18N__=%s;</script>"
            % (json.dumps(code), json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")))
    text = body.decode("utf-8", "replace")
    text = text.replace('<html lang="ru">', '<html lang="%s">' % code, 1)
    # Строки, которые видны ДО запуска скриптов: заголовок вкладки, «меню»,
    # «загружаю…» и текст для тех, у кого JavaScript выключен. Их подставляет
    # сервер, потому что к моменту их показа словаря на странице ещё нет.
    text = apply_lang(text, code, data)
    # Перед первым модулем: словарь обязан существовать раньше, чем
    # выполнится любой import, который его читает.
    text = text.replace('<script type="module"', blob + '\n<script type="module"', 1)
    return text.encode("utf-8")


class WebHandler(BaseHTTPRequestHandler):
    timeout = 20
    server_version = "goDNSWeb"
    # Версию python в заголовке Server не печатаем: она называет
    # злоумышленнику ровно тот набор известных уязвимостей, который стоит
    # пробовать, и ничего не даёт добросовестному клиенту.
    sys_version = ""

    # ── обвязка ─────────────────────────────────────────────────────────
    def log_message(self, fmt: str, *args: Any) -> None:
        if ACCESS_LOG:
            print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def handle_one_request(self) -> None:
        self._security_headers_sent = False
        # Соединение keep-alive обслуживает НЕСКОЛЬКО запросов одним
        # обработчиком — флаг выдачи cookie обязан сбрасываться на каждом.
        self._grant_session = False
        super().handle_one_request()

    def end_headers(self) -> None:
        if not getattr(self, "_security_headers_sent", False):
            self._security_headers_sent = True
            for name, value in SECURITY_HEADERS:
                self.send_header(name, value)
        super().end_headers()

    def send_json(self, payload: Any, status: int = 200, extra_headers: list[tuple[str, str]] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def send_ok(self, data: Any = None) -> None:
        self.send_json({"ok": True, "data": data})

    def read_json_body(self) -> dict[str, Any]:
        # Кривое тело — обычный ввод из интернета, а не сбой сервера: 400.
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
        except RecursionError:
            # Тело вида [[[[…]]]] в пределах мегабайта кладёт разборщик в
            # RecursionError. Ловим здесь: иначе он всплывал до общего
            # обработчика и превращался в 500 «сбой сервера», хотя это
            # обычный кривой ввод. Открытые эндпоинты входа читают тело ДО
            # проверки, значит вход в это доступен и без сессии.
            raise BadRequest("JSON body nested too deeply")
        # Валидный JSON, но не объект (null/список) ронял бы .get() в 500.
        if not isinstance(data, dict):
            raise BadRequest("JSON body must be an object")
        return data

    # ── кто пришёл ──────────────────────────────────────────────────────
    def is_local_listener(self) -> bool:
        return bool(getattr(self.server, "godns_local", False))

    def peer_is_loopback(self) -> bool:
        try:
            ip = self.client_address[0]
        except Exception:
            return False
        return ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def client_ip(self) -> str:
        return (self.client_address[0] if self.client_address else "?")

    def host_allowed(self) -> bool:
        raw = (self.headers.get("Host") or "").strip()
        if not raw:
            # Host шлют все браузеры; голым HTTP/1.0-инструментам не мешаем.
            return True
        return host_name_only(raw) in allowed_hosts()

    def request_session(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return value.strip()
        return ""

    def request_token(self, parsed: urllib.parse.ParseResult) -> str:
        header = (self.headers.get(TOKEN_HEADER) or "").strip()
        if header:
            return header
        try:
            values = urllib.parse.parse_qs(parsed.query).get(TOKEN_PARAM) or []
        except Exception:
            return ""
        return values[0].strip() if values else ""

    def token_ok(self, parsed: urllib.parse.ParseResult) -> bool:
        # Токен — аварийный путь ЧЕРЕЗ SSH-туннель: принимается только от
        # настоящего loopback-пира. Из интернета токен не работает вовсе —
        # там либо код из Telegram, либо подпись мини-аппа.
        if not self.peer_is_loopback():
            return False
        expected = admin_token()
        provided = self.request_token(parsed)
        if not expected or not provided:
            return False
        return hmac.compare_digest(expected.encode("utf-8", "ignore"),
                                   provided.encode("utf-8", "ignore"))

    def miniapp_ok(self) -> bool:
        raw = (self.headers.get(MINIAPP_INITDATA_HEADER) or "").strip()
        if not raw:
            return False
        return verify_telegram_init_data(raw) is not None

    def require_write_guard(self) -> bool:
        if self.command in {"POST", "PUT", "PATCH", "DELETE"} and self.headers.get("X-GoDNS-Admin") != "1":
            self.send_error_json(403, "missing write guard")
            return False
        return True

    def session_cookie(self, token: str, samesite: str = "Strict") -> str:
        # Secure-флаг на петлевом слушателе опускается: он ходит по
        # http://127.0.0.1 через SSH-туннель, и Safari молча выбросил бы
        # Secure-cookie — оператор входил бы по токену на каждый клик.
        secure = "" if self.is_local_listener() else "Secure; "
        # SameSite=None без Secure браузеры отвергают по спецификации.
        if samesite == "None":
            secure = "Secure; "
        return (f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_TTL}; "
                f"HttpOnly; {secure}SameSite={samesite}")

    # ── секретный путь ──────────────────────────────────────────────────
    def strip_secret_prefix(self, path: str) -> str | None:
        """None — запрос не под секретным путём и не имеет права жить."""
        secret = cfg("webui_path").strip("/")
        prefix = "/" + secret if secret else ""
        if prefix and (path == prefix or path.startswith(prefix + "/")):
            rest = path[len(prefix):]
            return rest or "/"
        if self.is_local_listener():
            # localhost: префикс не обязателен — это аварийный вход
            return path
        # публичный слушатель без префикса: молчаливый 404, как у dnsdist
        return None

    # ── страж ───────────────────────────────────────────────────────────
    def guard_request(self, parsed: urllib.parse.ParseResult, path: str) -> bool:
        if not self.host_allowed():
            self.send_error_json(403, "host not allowed")
            return False
        # Маршруты входа обязаны быть открыты, иначе войти нельзя в принципе.
        if path in OPEN_AUTH_PATHS:
            return True
        # Статика без секретов (css/js/svg) — до проверки: её тянет страница
        # входа и мини-апп до получения cookie. Индекс — под охраной.
        if self.command == "GET" and not path.startswith("/api/") and path not in GUARDED_STATIC:
            return True
        # Билет из адреса гасим ДО проверки cookie: он одноразовый и уже
        # засветился в истории браузера. Вместе со страницей уходит
        # сессионная cookie — иначе первая же перезагрузка выкидывала бы
        # на форму входа: билет-то погашен.
        if self.command == "GET" and not path.startswith("/api/"):
            q = urllib.parse.parse_qs(parsed.query or "")
            ticket = (q.get("s") or [""])[0].strip()
            if ticket and ticket_redeem(ticket):
                self._grant_session = True
                return True
        if session_valid(self.request_session()):
            return True
        if self.miniapp_ok():
            return True
        if self.token_ok(parsed):
            # Тот же урок с ?token=…: адресную строку панель тут же чистит
            # (история браузера — не место для токена), и без cookie
            # перезагрузка страницы означала бы выход.
            if self.command == "GET" and not path.startswith("/api/"):
                self._grant_session = True
            return True
        # Браузеру — страницу входа, а не голый 401: пустой экран без
        # объяснений выглядит как поломка.
        if self.command == "GET" and not path.startswith("/api/"):
            self.send_login_page()
            return False
        self.send_error_json(401, "unauthorized")
        return False

    def send_login_page(self) -> None:
        _code, _dict = panel_dict()
        body = apply_lang(LOGIN_PAGE_HTML, _code, _dict).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── статика ─────────────────────────────────────────────────────────
    def send_static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        if rel.startswith("api/") or ".." in rel.split("/"):
            self.send_error_json(404, "not found")
            return
        try:
            base = STATIC_DIR.resolve()
            target = (base / rel).resolve()
        except (OSError, RuntimeError):
            self.send_error_json(404, "not found")
            return
        if target != base and not target.is_relative_to(base):
            self.send_error_json(404, "not found")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self.send_error_json(404, "not found")
            return
        try:
            body = target.read_bytes()
        except OSError:
            self.send_error_json(404, "not found")
            return
        if target.name == "index.html":
            body = inject_lang(body)
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            mime = "text/javascript"
        elif target.suffix == ".svg":
            mime = "image/svg+xml"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "_grant_session", False):
            self._grant_session = False
            self.send_header("Set-Cookie", self.session_cookie(session_create()))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── API: чтение ─────────────────────────────────────────────────────
    def route_get_api(self, parsed: urllib.parse.ParseResult, path: str) -> None:
        if path == "/api/phone/profile":
            # Открытый маршрут, запертый одноразовым токеном из QR. Отдаёт
            # НЕ JSON, а сам профиль — его скачивает Safari на iPhone.
            q = urllib.parse.parse_qs(parsed.query)
            tok = (q.get("t") or [""])[0]
            data = _profile_token_take(tok) if tok else None
            if not data:
                self.send_error_json(404, "not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-apple-aspen-config")
            self.send_header("Content-Disposition",
                             'attachment; filename="godns-doh.mobileconfig"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except Exception:
                pass
            return
        if path == "/api/overview":
            self.send_ok(overview_payload())
        elif path == "/api/cert":
            cert, _key = cert_paths()
            dom = cfg("domain")
            meta = cert_meta(cert)
            self.send_ok({
                "domain": dom,
                "cert_path": cert,
                "days": cert_days_left(cert) if dom else None,
                # Дата, а не только «сколько осталось»: раздел о сертификате
                # обязан называть день, до которого он годен, — иначе
                # оператору нечем свериться с письмом от Let's Encrypt.
                "until": expiry_dates()["cert"] if dom else "",
                "san": meta["san"],
                "issuer": meta["issuer"],
                "renew_hook": renew_hook_state(),
            })
        elif path == "/api/upstreams":
            with _MEASURE_LOCK:
                measure = dict(_MEASURE)
            self.send_ok({
                "catalog": upstream_catalog(),
                "current": [i for i in cfg("upstream_ids").split(",") if i.strip()],
                "policy": cfg("policy", "roundrobin"),
                "measure": measure,
                "custom_max": _custom_max(),
            })
        elif path == "/api/ports":
            self.send_ok({
                "doh_port": cfg("doh_port"),
                "dot_port": cfg("dot_port", "853"),
                "dot_enabled": cfg("dot_enabled", "no") == "yes",
                "doh_path": cfg("doh_path"),
            })
        elif path == "/api/check":
            self.send_ok({"domain": cfg("domain")})
        elif path == "/api/service":
            state = service_state()
            show = _systemctl_show("dnsdist", ["MainPID", "ActiveEnterTimestamp"])
            listen = []
            dom = cfg("domain")
            if cfg("doh_port"):
                listen.append(f"DoH :{cfg('doh_port')}")
            if cfg("dot_enabled", "no") == "yes":
                listen.append(f"DoT :{cfg('dot_port', '853')}")
            code, out, _err = run(["dnsdist", "--version"], timeout=10)
            ver = (out or "").splitlines()[0].strip() if code == 0 and out else ""
            self.send_ok({
                "service": state,
                "engine": "dnsdist",
                "engine_version": ver,
                "main_pid": show.get("MainPID", "") if state == "running" else "",
                "since": show.get("ActiveEnterTimestamp", "") if state == "running" else "",
                "listen": listen,
                "drift": drift(),
                "domain": dom,
            })
        elif path == "/api/service/config":
            code, text = bridge("show_config", timeout=30)
            # Ключ консоли — секрет: в браузер не отдаём даже админу.
            text = re.sub(r'setKey\("[^"]*"\)', 'setKey("***")', text)
            text = re.sub(r"setKey\('[^']*'\)", "setKey('***')", text)
            self.send_ok({"text": text, "rc": code})
        elif path == "/api/service/logs":
            q = urllib.parse.parse_qs(parsed.query)
            lines = q.get("lines", ["60"])[0]
            if not lines.isdigit() or not (1 <= int(lines) <= 1000):
                lines = "60"
            code, text = bridge("logs", lines, timeout=30)
            self.send_ok({"text": text, "rc": code})
        elif path == "/api/backups":
            code, out = bridge_data("backup_list", timeout=30)
            items = []
            if code == 0:
                for line in out.splitlines():
                    f = line.split("\t")
                    if len(f) >= 6:
                        items.append({"name": f[0], "version": f[1], "created": f[2],
                                      "domain": f[3], "size": f[4], "encrypted": f[5] == "yes"})
            self.send_ok({
                "items": items,
                "dir": str(BACKUP_DIR),
                "schedule": cfg("backup_schedule", "off"),
                # Число берём у самой уборки, а не выдумываем: подпись у
                # кнопки называет его пользователю, а прибиралось до 10 —
                # панель говорила «останутся 5» и не сверялась ни с чем.
                "keep": _backup_keep(),
            })
        elif path == "/api/connect":
            dom = cfg("domain")
            dot_on = cfg("dot_enabled", "no") == "yes"
            self.send_ok({
                "domain": dom,
                "doh_url": doh_url(),
                "dot_enabled": dot_on,
                "dot_addr": (f"{dom}:{cfg('dot_port', '853')}" if (dot_on and dom) else ""),
            })
        elif path == "/api/connect/snippets":
            q = urllib.parse.parse_qs(parsed.query)
            which = q.get("which", ["routers"])[0]
            if which not in {"routers", "os", "browsers"}:
                self.send_error_json(400, "unknown snippet set")
                return
            code, text = bridge("snip_" + which, timeout=40)
            self.send_ok({"text": text, "rc": code})
        elif path == "/api/connect/qr":
            dom = cfg("domain")
            if not dom:
                self.send_error_json(400, wt("web_no_domain"))
                return
            out: dict[str, Any] = {"domain": dom}
            # Android: системный «Частный DNS» — это DoT, жёстко порт 853.
            dot_on = cfg("dot_enabled", "no") == "yes"
            dot_port = cfg("dot_port", "853")
            if dot_on and dot_port == "853":
                out["android"] = {"state": "ok", "host": dom, "svg": _qr_svg(dom)}
            elif dot_on:
                out["android"] = {"state": "badport", "port": dot_port}
            else:
                out["android"] = {"state": "nodot"}
            # iPhone: профиль отдаём через сам порт панели по одноразовому
            # токену. Профиль генерит тот же код, что в SSH и боте.
            code, ppath = bridge("phone_profile", timeout=30)
            ppath = (ppath or "").strip().splitlines()[-1] if ppath.strip() else ""
            if code == 0 and ppath and os.path.isfile(ppath):
                try:
                    data = open(ppath, "rb").read()
                except OSError:
                    data = b""
                if data:
                    tok = _profile_token_new(data)
                    # ГРАБЛИ: host_name_only РЕЖЕТ порт, и ссылка выходила
                    # на :443 — где живёт dnsdist, а не панель. Телефон
                    # получал 404 всегда: панель на 443 не стоит никогда,
                    # 443 занят резолвером. Порт берём из заголовка Host,
                    # а если его там нет — из конфига.
                    raw_host = (self.headers.get("Host") or "").strip()
                    host = host_name_only(raw_host) or dom
                    port = ""
                    if ":" in raw_host and not raw_host.endswith("]"):
                        tail = raw_host.rsplit(":", 1)[1]
                        if tail.isdigit():
                            port = tail
                    if not port:
                        port = str(cfg("webui_port", "") or "")
                    hostport = "%s:%s" % (host, port) if port and port != "443" else host
                    secret = cfg("webui_path").strip("/")
                    prefix = ("/" + secret) if secret else ""
                    url = "https://%s%s/api/phone/profile?t=%s" % (hostport, prefix, tok)
                    out["ios"] = {"url": url, "svg": _qr_svg(url)}
            if "ios" not in out:
                out["ios"] = {"error": wt("web_qr_profile_fail")}
            self.send_ok(out)
        elif path.startswith("/api/jobs/"):
            jid = path[len("/api/jobs/"):]
            j = job_get(jid)
            if not j:
                self.send_error_json(404, "job not found")
                return
            self.send_ok({"state": j["state"], "output": j.get("output", ""), "kind": j["kind"]})
        else:
            self.send_error_json(404, "not found")

    # ── API: действия ───────────────────────────────────────────────────
    def route_post_api(self, parsed: urllib.parse.ParseResult, path: str) -> None:
        # Вход — до общего стража записи не добрался бы никто: эти маршруты
        # уже пропущены guard_request, но X-GoDNS-Admin им тоже нужен?
        # Нет: страницу входа рисуем мы сами, и она шлёт заголовок. Однако
        # требовать его здесь нельзя — заголовок и так стоит, а ломать вход
        # из-за отсутствия свистка глупо. Поэтому auth-маршруты идут первыми.
        if path == "/api/auth/request":
            ok, message = tfa_request(self.client_ip())
            self.send_json({"ok": ok, "error": None if ok else message,
                            "message": message if ok else None},
                           status=200 if ok else 429)
            return
        if path == "/api/auth/miniapp":
            body = self.read_json_body()
            raw = str(body.get("init_data") or "").strip()
            uid = verify_telegram_init_data(raw) if raw else None
            if not uid:
                self.send_error_json(401, wt("web_sig_bad"))
                return
            # Мини-апп — единственный вход, который ЗНАЕТ хозяина: подпись
            # initData назвала его telegram-id. Записываем в сессию, чтобы
            # исключение этого человека из админов гасило именно её.
            token = session_create(tg_id=uid)
            # SameSite=None + Secure: внутри фрейма Telegram cookie считается
            # сторонней, Strict там не отдаётся. Если браузер режет сторонние
            # cookie вовсе — страница получает ещё и одноразовый билет, а
            # каждый API-запрос из мини-аппа несёт подпись initData заголовком.
            self.send_json(
                {"ok": True, "ticket": ticket_create()},
                extra_headers=[("Set-Cookie", self.session_cookie(token, samesite="None"))],
            )
            return
        if path == "/api/auth/verify":
            body = self.read_json_body()
            ok, message = tfa_verify(str(body.get("code", "")))
            if not ok:
                self.send_json({"ok": False, "error": message}, status=401)
                return
            token = session_create()
            self.send_json(
                {"ok": True},
                extra_headers=[("Set-Cookie", self.session_cookie(token))],
            )
            return

        if not self.require_write_guard():
            return
        try:
            body = self.read_json_body()
        except BadRequest as exc:
            self.send_error_json(400, str(exc))
            return

        if path == "/api/cert/rehook":
            code, text = bridge("cert_rehook", timeout=60)
            self._action_result(code, text)
        elif path == "/api/cert/reload":
            code, text = bridge("reload_certs", timeout=60)
            self._action_result(code, text)
        elif path == "/api/upstreams":
            ids = body.get("ids")
            policy = str(body.get("policy") or "roundrobin")
            if not isinstance(ids, list) or not ids:
                self.send_error_json(400, wt("web_ups_none"))
                return
            if policy not in POLICY_KEYS:
                self.send_error_json(400, wt("web_pol_unknown"))
                return
            catalog = {c["id"]: c for c in upstream_catalog()}
            clean = []
            for i in ids:
                i = str(i).strip()
                if i not in catalog:
                    self.send_error_json(400, wt("web_ups_unknown", i))
                    return
                # ГРАБЛИ: провайдер, которому нужен ID профиля (NextDNS),
                # проходил проверку, а движок его молча отбрасывал —
                clean.append(i)
            code, text = bridge("set_upstreams", ",".join(clean), policy, timeout=240)
            self._action_result(code, text)
        elif path == "/api/upstreams/measure":

            def worker() -> tuple[str, str]:
                code, out = bridge_data("bench", timeout=420)
                if code != 0 or not out.strip():
                    return "failed", wt("web_bench_failed")
                parsed_res = parse_bench_json(out)
                if not parsed_res:
                    return "failed", wt("web_bench_empty")
                with _MEASURE_LOCK:
                    _MEASURE.clear()
                    _MEASURE.update(parsed_res)
                okc = sum(1 for r in parsed_res.values() if r.get("warm") is not None)
                return "done", wt("web_bench_summary", len(parsed_res), okc)

            jid = job_try_start("bench", worker)
            if jid is None:
                self.send_error_json(409, wt("web_bench_busy"))
                return
            self.send_ok({"job": jid})
        elif path == "/api/upstreams/report":
            code, text = bridge("upstream_report", timeout=60)
            self._action_result(code, text)
        elif path == "/api/upstreams/custom/probe":
            # Живая проба кандидата ДО записи — тот же шаг, что в SSH.
            # ok:false (rc 3) — результат, а не HTTP-ошибка; needs_ack (rc 4) —
            # открытый DNS к публичному адресу без подтверждения: пробы не
            # было, ни один пакет не ушёл. Путь DoH — окружением, не argv.
            proto, host, port, cu_path = _cu_fields(body)
            ack = parse_flag(body.get("ack_plain", False))
            if ack is None:
                self.send_error_json(400, wt("web_flag_bad_field", "ack_plain"))
                return
            if not _PROBE_LOCK.acquire(blocking=False):
                self.send_error_json(409, wt("web_cu_probe_busy"))
                return
            try:
                code, text = bridge("custom_probe", proto, host, port, "yes" if ack else "no",
                                    timeout=60, env={"GODNS_CUSTOM_PATH": cu_path})
            finally:
                _PROBE_LOCK.release()
            kv, out = _kv_lines(text)
            if code not in (0, 3, 4):
                self._action_result(code, out)
                return
            private = kv.get("private")
            self.send_ok({
                "ok": code == 0,
                "needs_ack": code == 4,
                "ip": kv.get("ip", ""),
                "sni": kv.get("sni", ""),
                "port": kv.get("port", port),
                "private": None if private not in ("yes", "no") else private == "yes",
                "latency": kv.get("latency", ""),
                "output": out,
            })
        elif path == "/api/upstreams/custom":
            proto, host, port, cu_path = _cu_fields(body)
            name_raw = body.get("name")
            name = "" if name_raw is None else str(name_raw).strip()
            if name and not CU_NAME_RE.match(name):
                self.send_error_json(400, wt("web_cu_bad_name"))
                return
            enable = parse_flag(body.get("enable", False))
            ack = parse_flag(body.get("ack_plain", False))
            if enable is None or ack is None:
                self.send_error_json(400, wt("web_flag_bad_field", "enable / ack_plain"))
                return
            try:
                code, text = bridge("custom_add", proto, host, port, name,
                                    "yes" if enable else "no", "yes" if ack else "no",
                                    timeout=240, env={"GODNS_CUSTOM_PATH": cu_path})
            finally:
                # После ЛЮБОГО исхода: даже отказ мог случиться после записи.
                _catalog_invalidate()
            kv, out = _kv_lines(text)
            if code != 0:
                self._action_result(code, out)
                return
            self.send_ok({"id": kv.get("id", ""), "output": out})
        elif path == "/api/upstreams/custom/remove":
            # POST, не DELETE: сервер маршрутизирует только GET/POST.
            # Слово проверяется ПЕРВЫМ — как у /api/backups/restore.
            if not confirm_ok("custom_remove", body.get("confirm")):
                self.send_error_json(400, wt("web_need_confirm", confirm_word("custom_remove")))
                return
            cid_raw = body.get("id")
            cid = "" if cid_raw is None else str(cid_raw).strip()
            if not CU_ID_RE.match(cid):
                self.send_error_json(400, wt("web_cu_bad_id"))
                return
            try:
                code, text = bridge("custom_remove", cid, timeout=240)
            finally:
                _catalog_invalidate()
            if code == 0:
                # Замер удалённой записи забываем; при отказе (единственный
                # в наборе) запись остаётся, и её задержка — тоже.
                with _MEASURE_LOCK:
                    _MEASURE.pop(cid, None)
            self._action_result(code, text)
        elif path == "/api/ports":
            doh = str(body.get("doh_port") or "").strip()
            dot = str(body.get("dot_port") or "").strip()
            for name, val in (("DoH", doh), ("DoT", dot)):
                if not val.isdigit() or not (1 <= int(val) <= 65535):
                    self.send_error_json(400, wt("web_port_range", name))
                    return
            code, text = bridge("set_ports", doh, dot, timeout=240)
            self._action_result(code, text)
        elif path == "/api/ports/dot":
            enabled = parse_flag(body.get("enabled"))
            if enabled is None:
                self.send_error_json(400, wt("web_flag_bad"))
                return
            if not enabled and not confirm_ok("dot_off", body.get("confirm")):
                self.send_error_json(400, wt("web_need_confirm", confirm_word("dot_off")))
                return
            code, text = bridge("set_dot", "yes" if enabled else "no", timeout=240)
            self._action_result(code, text)
        elif path == "/api/path/rotate":
            if not confirm_ok("rotate", body.get("confirm")):
                self.send_error_json(400, wt("web_need_confirm", confirm_word("rotate")))
                return
            code, text = bridge("rotate_path", timeout=240)
            self._action_result(code, text)
        elif path in {"/api/check/quick", "/api/check/full", "/api/check/leak", "/api/check/external"}:
            kind = path.rsplit("/", 1)[1]
            if kind == "external" and not confirm_ok("external", body.get("confirm")):
                self.send_error_json(400, wt("web_need_agree", confirm_word("external")))
                return
            action = {"quick": "check_quick", "full": "check_full",
                      "leak": "check_leak", "external": "check_external"}[kind]
            timeout = 420 if kind in {"full", "leak"} else 180

            def worker(action: str = action, timeout: int = timeout) -> tuple[str, str]:
                code, text = bridge(action, timeout=timeout)
                return ("done" if code == 0 else "failed"), text

            jid = job_try_start("check", worker)
            if jid is None:
                self.send_error_json(409, wt("web_check_busy"))
                return
            self.send_ok({"job": jid})
        elif path == "/api/service/restart":
            code, text = bridge("service_restart", timeout=240)
            self._action_result(code, text)
        elif path == "/api/backups/create":
            label = str(body.get("label") or "").strip()
            # Хотя бы одна буква или цифра: «..» проходило прежнюю проверку
            # и давало копию с бессмысленным именем. Обхода пути тут нет
            # (слэш запрещён, а восстановление режет `..` отдельно) — это
            # про читаемость имён, по которым потом выбирают, что
            # разворачивать.
            if label and not re.match(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9._-]{1,40}$", label):
                self.send_error_json(400, wt("web_label_bad"))
                return
            code, text = bridge("backup_create", *( [label] if label else [] ), timeout=180)
            self._action_result(code, text)
        elif path == "/api/backups/restore":
            name = str(body.get("name") or "").strip()
            if not confirm_ok("restore", body.get("confirm")):
                self.send_error_json(400, wt("web_need_confirm", confirm_word("restore")))
                return
            if not re.match(r"^[A-Za-z0-9._-]+\.tar\.gz(\.enc)?$", name):
                self.send_error_json(400, wt("web_backup_name_bad"))
                return
            code, text = bridge("backup_restore", name, timeout=300)
            self._action_result(code, text)
        elif path == "/api/backups/prune":
            if not confirm_ok("prune", body.get("confirm")):
                self.send_error_json(400, wt("web_need_confirm", confirm_word("prune")))
                return
            code, text = bridge("backup_prune", timeout=120)
            self._action_result(code, text)
        elif path == "/api/backups/schedule":
            freq = str(body.get("freq") or "")
            if freq not in {"daily", "weekly", "monthly", "off"}:
                self.send_error_json(400, "freq: daily|weekly|monthly|off")
                return
            code, text = bridge("backup_schedule_set", freq, timeout=60)
            self._action_result(code, text)
        else:
            self.send_error_json(404, "not found")

    def _action_result(self, code: int, text: str) -> None:
        if code == 0:
            self.send_ok({"output": text})
        else:
            # Текст вывода полезнее голого «exit N»: там log_error с причиной.
            self.send_json({"ok": False, "error": wt("web_action_rc", code),
                            "data": {"output": text}}, status=502)

    # ── диспетчер ───────────────────────────────────────────────────────
    def _safe(self, fn, *args) -> None:
        try:
            fn(*args)
        except BadRequest as exc:
            self.send_error_json(400, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            print(wt("web_unhandled", self.command, self.path) + "\n"
                  + traceback.format_exc(), flush=True)
            try:
                self.send_error_json(500, "internal error")
            except Exception:
                pass

    def _dispatch(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = self.strip_secret_prefix(parsed.path)
        if path is None:
            # молчаливый 404: публичный слушатель не выдаёт, что тут панель
            self.send_error_json(404, "not found")
            return
        try:
            if not self.guard_request(parsed, path):
                return
        except BadRequest as exc:
            self.send_error_json(400, str(exc))
            return
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            print(wt("web_guard_failed", self.command, parsed.path) + "\n" + traceback.format_exc(), flush=True)
            try:
                self.send_error_json(500, "internal error")
            except Exception:
                pass
            return
        if path.startswith("/api/"):
            if self.command == "GET":
                self._safe(self.route_get_api, parsed, path)
            elif self.command == "POST":
                self._safe(self.route_post_api, parsed, path)
            else:
                self.send_error_json(405, "method not allowed")
        elif self.command == "GET":
            self._safe(self.send_static, path)
        else:
            self.send_error_json(405, "method not allowed")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()


# ═══════════════════════════════════════════════════════════════════════
# Слушатели. Публичный оборачивает КАЖДОЕ принятое соединение в TLS с
# актуальным сертификатом: контекст пересобирается, когда меняется mtime
# fullchain.pem — то есть перевыпуск подхватывается сам, без рестарта.
# ═══════════════════════════════════════════════════════════════════════
class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    godns_local = True


class PublicTLSServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    godns_local = False

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self._tls_lock = threading.Lock()
        self._tls_ctx: ssl.SSLContext | None = None
        self._tls_stamp: tuple[str, int] | None = None

    def _current_context(self) -> ssl.SSLContext | None:
        cert, key = cert_paths()
        if not cert or not key:
            return None
        try:
            mtime = os.stat(cert).st_mtime_ns
        except OSError:
            return None
        stamp = (cert, mtime)
        with self._tls_lock:
            if self._tls_ctx is not None and self._tls_stamp == stamp:
                return self._tls_ctx
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            try:
                ctx.load_cert_chain(cert, key)
            except (OSError, ssl.SSLError) as exc:
                print(wt("web_cert_load_failed", exc), flush=True)
                return self._tls_ctx  # работаем со старым, если был
            self._tls_ctx = ctx
            self._tls_stamp = stamp
            return ctx

    def get_request(self):
        sock, addr = self.socket.accept()
        ctx = self._current_context()
        if ctx is None:
            sock.close()
            raise OSError("no certificate for TLS listener")
        sock.settimeout(15)
        try:
            tls = ctx.wrap_socket(sock, server_side=True)
        except (ssl.SSLError, OSError, socket.timeout):
            # сканер или голый HTTP на TLS-порту: рвём молча.
            try:
                sock.close()
            except OSError:
                pass
            raise OSError("TLS handshake failed")
        tls.settimeout(None)
        return tls, addr


def main() -> None:
    if not STATIC_DIR.is_dir():
        raise SystemExit(wt("web_no_static", STATIC_DIR))
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    token = admin_token()
    local_port = int(os.getenv("GODNS_WEB_LOCAL_PORT", "") or cfg("webui_local_port", "0") or 0)
    public_port = int(os.getenv("GODNS_WEB_PORT", "") or cfg("webui_port", "0") or 0)
    secret = cfg("webui_path").strip("/")

    servers: list[ThreadingHTTPServer] = []
    if local_port:
        srv = LocalHTTPServer(("127.0.0.1", local_port), WebHandler)
        servers.append(srv)
        print(wt("web_local_entry", local_port, TOKEN_FILE), flush=True)
        print(wt("web_ssh_tunnel", local_port), flush=True)
    if public_port:
        cert, key = cert_paths()
        if cert and key:
            srv = PublicTLSServer(("0.0.0.0", public_port), WebHandler)
            servers.append(srv)
            dom = cfg("domain") or wt("web_server_placeholder")
            print(wt("web_public_entry", dom, public_port, secret), flush=True)
        else:
            print(wt("web_no_cert_listener"), flush=True)
    if not servers:
        raise SystemExit(wt("web_no_ports"))

    # токен в журнал не пишем — только путь к файлу; сам токен печатает
    # lib/webui.sh в защищённой сессии оператора
    _ = token

    threads = []
    for srv in servers:
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        threads.append(th)
    print(wt("web_started", cfg("godns_version", VERSION_FALLBACK), len(servers)),
          flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
