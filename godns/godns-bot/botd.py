#!/usr/bin/env python3
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
"""goDNS bot — привилегированный демон (godns-botd).

Единственный компонент бота с root. Слушает UNIX-сокет /run/godns-botd.sock
(0660 root:godns-bot) и исполняет ЗАКРЫТЫЙ список методов JSON-RPC 2.0.
Бот (godns-bot.service, непривилегированный) — единственный ожидаемый
клиент; принадлежность проверяется по SO_PEERCRED, а не на честном слове.

Почему так, а не sudo/полномочия боту: бот разбирает недоверенный ввод
(писать ему может кто угодно), и компрометация бота не должна давать root.
Демон исполняет только перечисленные действия с провалидированными
аргументами — это и есть весь привилегированный интерфейс.

В argv действия — только идентификаторы: /proc/<pid>/cmdline читает любой
локальный пользователь. Единственное значение, которое может быть чужим
секретом, — путь DoH своего апстрима (второй goDNS как апстрим) — едет
переменной окружения GODNS_CUSTOM_PATH (/proc/<pid>/environ читает только
владелец процесса, то есть root) и только для действий из ACTION_ENV.

Методы:
    ping    — версия и работоспособность;
    claim   — привязка админа по одноразовому коду из SSH-меню;
    admins  — текущий список админов (из /etc/godns/bot.env);
    action  — одно действие из ACTIONS: запуск actions.sh с аргументами;
              необязательный params["env"] — окружение по allowlist
              ACTION_ENV (чужой ключ или значение вне регэкспа → -32602).

Протокол: одна строка JSON на запрос, одна строка JSON на ответ.

Импорт файла не имеет побочных эффектов — main() запускается только при
прямом старте. Это делает демона тестируемым без root и без сокета.
"""

import asyncio
import fcntl
import hmac
import json
import logging
import os
import pwd
import re
import socket
import struct
import sys
import tempfile
import time

logger = logging.getLogger("godns-botd")

# ── Пути (переопределяемы окружением — для тестов) ──────────────────────
SOCK_PATH = os.environ.get("GODNS_BOTD_SOCK", "/run/godns-botd.sock")
BOT_ENV_PATH = os.environ.get("GODNS_BOT_ENV", "/etc/godns/bot.env")
# actions.sh исполняется ИЗ каталога программы goDNS, не из копии: он
# сорсит lib/*.sh и обязан быть той же версии, что и библиотека. install.sh
# подменяет каталог атомарно, так что путь стабилен.
ACTIONS_SH = os.environ.get(
    "GODNS_BOT_ACTIONS", "/opt/godns-installer/godns-bot/actions.sh")
AUDIT_LOG = os.environ.get(
    "GODNS_BOT_AUDIT", "/var/log/godns/bot-audit.log")
BOT_USER = os.environ.get("GODNS_BOT_USER", "godns-bot")
BOT_GROUP = os.environ.get("GODNS_BOT_GROUP", "godns-bot")

BOTD_VERSION = "1"

# Сколько байт вывода действия возвращаем боту. Telegram всё равно режет
# по 4096 на сообщение; хвост длинного отчёта ценнее середины, поэтому
# при переполнении сохраняем начало и конец.
MAX_OUTPUT = 64_000
MAX_REQUEST = 64_000

# ── Реестр действий ─────────────────────────────────────────────────────
# name -> (mutating, timeout_s, arg_validator)
# Валидатор получает список аргументов и возвращает их же (или бросает
# ValueError). Всё, чего нет в реестре, не исполняется вовсе.

def _no_args(args):
    if args:
        raise ValueError("no arguments expected")
    return []


def _one_of(*allowed):
    def check(args):
        if len(args) != 1 or args[0] not in allowed:
            raise ValueError("expected one of: " + ", ".join(allowed))
        return [args[0]]
    return check


def _int_range(lo, hi, default):
    def check(args):
        if not args:
            return [str(default)]
        if len(args) != 1:
            raise ValueError("expected one integer")
        try:
            v = int(args[0])
        except ValueError:
            raise ValueError("expected an integer") from None
        v = max(lo, min(hi, v))
        return [str(v)]
    return check


# ── Свои апстримы: синтаксический фильтр на границе процесса ────────────
# Те же шесть регэкспов, что в godns-web/server.py (CU_*_RE); тождество
# закрепляет test_bot.sh. Здесь — только синтаксис: окончательно судит
# lib (valid_ip, valid_domain, valid_port, _prov_valid_sni, _prov_valid_path).
# Демон разрешает пустой порт/путь/имя — «по умолчанию» решает lib.
CU_PROTO_RE = re.compile(r"^(dot|doh|plain)$")
CU_HOST_RE = re.compile(r"^[A-Za-z0-9:][A-Za-z0-9.:-]{0,252}$")
CU_PORT_RE = re.compile(r"^([1-9][0-9]{0,4})?$")          # пусто = порт по умолчанию
CU_PATH_RE = re.compile(r"^(/[A-Za-z0-9._~%/-]{0,127})?$")
CU_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f|;]{0,40}$")     # пусто = имя по умолчанию
CU_YESNO_RE = re.compile(r"^(yes|no)$")
CU_ID_RE = re.compile(r"^custom-[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")


def _match(*patterns):
    """Ровно len(patterns) аргументов, каждый — fullmatch своего регэкспа;
    порт ≤ 65535.

    ГРАБЛИ: _int_range выше для порта не годится — он ПРИЖИМАЕТ 70000 к
    границе молча, а порт не число строк журнала: «прижатый» порт — это
    другой сервер. Здесь всё вне диапазона — отказ.

    fullmatch, а не match: у `$` в Python есть поблажка на завершающий
    перевод строки, и «host\\n» проходил бы `^…$` через match(). Такой хвост
    в argv actions.sh никому не нужен.
    """
    def check(args):
        if len(args) != len(patterns):
            raise ValueError("expected %d argument(s)" % len(patterns))
        for a, rx in zip(args, patterns):
            if not rx.fullmatch(a):
                raise ValueError("argument %r rejected" % a[:40])
            if rx is CU_PORT_RE and a and int(a) > 65535:
                raise ValueError("port out of range")
        return list(args)
    return check


ACTIONS = {
    "panel":                  (False,  20, _no_args),
    "stats":                  (False,  30, _no_args),
    "cert_info":              (False,  30, _no_args),
    "cert_hook":              (True,   60, _no_args),
    "upstream_report":        (False,  60, _no_args),
    "measure":                (False, 300, _no_args),
    "policy_set":             (True,  180, _one_of("fastest", "roundrobin")),
    "ports_info":             (False,  20, _no_args),
    "rotate_path":            (True,  180, _no_args),
    "dot_on":                 (True,  180, _no_args),
    "dot_off":                (True,  180, _no_args),
    "test_quick":             (False, 180, _no_args),
    "test_full":              (False, 420, _no_args),
    "test_external":          (False, 180, _no_args),
    "test_leak":              (False, 240, _no_args),
    "snippets":               (False,  30, _one_of(
        "routers", "os", "browsers", "profile", "all")),
    "phone_qr":               (False,  60, _one_of("android", "ios")),
    "svc_status":             (False,  60, _no_args),
    "svc_start":              (True,   60, _no_args),
    "svc_stop":               (True,   60, _no_args),
    "svc_apply":              (True,  180, _no_args),
    "svc_logs":               (False,  30, _int_range(1, 200, 60)),
    "svc_showconf":           (False,  30, _no_args),
    "reload_certs":           (True,   60, _no_args),
    "qlog_state":             (False,  20, _no_args),
    "qlog_on":                (True,   60, _no_args),
    "qlog_off":               (True,   60, _no_args),
    "logs_report":            (False,  60, _no_args),
    "backup_create":          (True,  180, _no_args),
    "backup_list":            (False,  30, _no_args),
    "backup_schedule_status": (False,  30, _no_args),
    "backup_schedule_set":    (True,   60, _one_of(
        "daily", "weekly", "monthly", "off")),
    # Свои апстримы. argv: probe — proto host port ack; add — proto host
    # port name enable ack; remove — id. Путь DoH — НЕ здесь, а в
    # окружении (ACTION_ENV ниже).
    "custom_list":            (False,  30, _no_args),
    "custom_probe":           (False,  75, _match(
        CU_PROTO_RE, CU_HOST_RE, CU_PORT_RE, CU_YESNO_RE)),
    "custom_add":             (True,  180, _match(
        CU_PROTO_RE, CU_HOST_RE, CU_PORT_RE, CU_NAME_RE, CU_YESNO_RE, CU_YESNO_RE)),
    "custom_remove":          (True,  180, _match(CU_ID_RE)),
    "uninstall":              (True,  600, _no_args),
}

# Окружение действия: ключ разрешён только здесь и только с этим регэкспом.
# Всё остальное в params["env"] — отказ -32602, а не молчаливый пропуск:
# окружение actions.sh — это PATH, HOME и пути библиотеки, и подменить их
# из бота не должно быть возможно даже теоретически.
ACTION_ENV = {
    "custom_probe": {"GODNS_CUSTOM_PATH": CU_PATH_RE},
    "custom_add":   {"GODNS_CUSTOM_PATH": CU_PATH_RE},
}


def _check_env(name: str, env_extra) -> dict:
    """params["env"] → проверенные пары для окружения действия.

    Отсутствует — пусто; не объект, чужой ключ, значение не строка или вне
    регэкспа — ValueError (у вызывающего это -32602). Значение в текст
    ошибки не попадает: путь DoH может быть чужим секретом.
    """
    if env_extra is None:
        return {}
    if not isinstance(env_extra, dict):
        raise ValueError("env must be an object")
    allowed = ACTION_ENV.get(name, {})
    out = {}
    for key, value in env_extra.items():
        rx = allowed.get(key) if isinstance(key, str) else None
        if rx is None:
            raise ValueError("env key not allowed for %s: %r" % (name, str(key)[:40]))
        if not isinstance(value, str) or not rx.fullmatch(value):
            raise ValueError("env value rejected: %s" % key)
        out[key] = value
    return out


# Действия, в чьём выводе дополнительно маскируется секретный путь DoH.
# Показ конфига — диагностика, а не раздача адреса: адрес целиком отдают
# panel / ports_info / snippets, там путь — сама суть ответа.
MASK_PATH_ACTIONS = frozenset({"svc_showconf"})

# ── Маскировка секретов ─────────────────────────────────────────────────
# В SSH конфиг dnsdist — текст на терминале оператора; из бота тот же
# текст уезжает на серверы Telegram и остаётся в истории переписки
# навсегда. Ключ консоли (setKey) — полный контроль над dnsdist, поэтому
# он маскируется в ЛЮБОМ выводе любого действия: журнал, отчёт о провале
# engine_apply и показ конфига могут процитировать строку с ним.
_SETKEY_RE = re.compile(r"setKey\((['\"]).*?\1\)")

GODNS_CONFIG_PATH = os.environ.get("GODNS_CONFIG", "/opt/godns/config.json")


def _config_secret(key: str) -> str:
    try:
        with open(GODNS_CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        value = data.get(key)
        return value if isinstance(value, str) else ""
    except (OSError, ValueError):
        return ""


def scrub_secrets(text: str, mask_path: bool = False) -> str:
    text = _SETKEY_RE.sub("setKey('***')", text)
    console_key = _config_secret("console_key")
    if console_key:
        text = text.replace(console_key, "***")
    if mask_path:
        doh_path = _config_secret("doh_path").strip("/")
        if doh_path:
            text = text.replace("/" + doh_path, "/***")
            text = text.replace(doh_path, "***")
    return text


# ── ANSI: снять и настоящие ESC-последовательности, и литеральные ───────
# Цвета в goDNS — литеральные строки '\033[2m', печатаемые через echo -e,
# то есть в захваченном выводе они уже НАСТОЯЩИЕ ESC-байты. Но часть кода
# печатает через printf '%s', и тогда в вывод попадает сам текст «\033[2m».
# Снимаем оба вида; заодно — \r-перерисовки прогресса.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"        # настоящий CSI
    r"|\x1b[()][0-9A-B]"             # charset
    r"|\\033\[[0-9;?]*[A-Za-z]"      # литеральный
    r"|\\e\[[0-9;?]*[A-Za-z]"
)


def strip_ansi(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    # Прогресс-строки перерисовывают себя через \r: оставляем последнюю
    # версию каждой строки, иначе в Telegram приезжает простыня спиннера.
    lines = []
    for raw in text.split("\n"):
        if "\r" in raw:
            raw = raw.split("\r")[-1]
        lines.append(raw.rstrip())
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip("\n")


def clip_output(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    head = text[: MAX_OUTPUT // 2]
    tail = text[-(MAX_OUTPUT // 2):]
    return head + "\n… (вывод сокращён / output truncated) …\n" + tail


# ── bot.env: чтение и атомарная запись ──────────────────────────────────
# Файл читают три стороны: systemd (EnvironmentFile для бота), веб-часть
# и этот демон. Формат нарочно примитивный: KEY=value, без кавычек, без
# подстановок. Никогда не source-ится (значения могут содержать `$`).

def env_read(path: str = None) -> dict:
    path = path or BOT_ENV_PATH
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                data[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return data


def env_write(updates: dict, path: str = None) -> None:
    """Переписать bot.env, сохранив незнакомые ключи и комментарии.

    Атомарно (tempfile + rename в том же каталоге), права 0600. Под
    flock: демон и SSH-мастер могут писать одновременно.
    """
    path = path or BOT_ENV_PATH
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            lines = []
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except FileNotFoundError:
                pass
            seen = set()
            out = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.partition("=")[0].strip()
                    if key in updates:
                        out.append(f"{key}={updates[key]}")
                        seen.add(key)
                        continue
                out.append(line)
            for key, value in updates.items():
                if key not in seen:
                    out.append(f"{key}={value}")
            fd, tmp = tempfile.mkstemp(
                dir=os.path.dirname(path) or ".", prefix=".bot.env.")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(out) + "\n")
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


# Исторические имена ключа со списком админов. ADMIN_IDS — нынешнее;
# остальные встречались в более ранних сборках и у соседей.
ADMIN_ID_KEYS = ("ADMIN_IDS", "ALLOWED_IDS", "ADMIN_ID", "CHAT_ID")


def admins_from_env(env: dict) -> list:
    """Список Telegram-ID админов.

    ГРАБЛИ: бот и панель писались раздельно и разошлись в имени ключа —
    бот знал только ADMIN_IDS, панель читала ещё и ALLOWED_IDS (формат
    goTelegram, откуда скопирована схема входа). Оба пишут в ОДИН файл
    /etc/godns/bot.env, поэтому расхождение немое и жестокое: бот жив,
    отвечает и НЕ СЛУШАЕТСЯ владельца, потому что владельца в его глазах
    не существует. Поймано на живом сервере — в bot.env лежал
    ALLOWED_IDS, а демон отвечал admins: [].

    Канон — ADMIN_IDS, его и пишем. Остальные имена читаем ради файлов,
    оставленных прежними сборками и соседским форматом. Ровно тот же
    список у server.py: если бы терпимость была только на одной стороне,
    панель и бот продолжали бы расходиться в том, кого считать хозяином.
    """
    out = []
    for key in ADMIN_ID_KEYS:
        raw = (env.get(key) or "").strip()
        if not raw:
            continue
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        if out:
            break
    return out


def claim_expires(env: dict) -> int:
    """UNIX-время, до которого код годен; 0 — не задано (= истёк).

    Отсутствующий или кривой ADMIN_CLAIM_EXPIRES трактуется как истёкший
    намеренно: безопасное состояние — закрытое окно, а не вечное.
    """
    raw = (env.get("ADMIN_CLAIM_EXPIRES") or "").strip()
    return int(raw) if raw.isdigit() else 0


def claim_is_open(env: dict) -> bool:
    token = (env.get("ADMIN_CLAIM_TOKEN") or "").strip()
    return bool(token) and time.time() <= claim_expires(env)


# Тормоз перебора кодов. Глобальный, не по tg_id: чужие id нападающий
# выбирает сам, персональный счётчик он обнулял бы новым аккаунтом.
# Состояние в памяти демона — он долгоживущий; рестарт обнуляет, и это
# приемлемо: энтропии кода (62^10) перебор всё равно не берёт, счётчик
# нужен, чтобы промахи были видны и не лились бесплатно.
CLAIM_FAIL_LIMIT = 5
CLAIM_LOCK_SECONDS = 60
_CLAIM_STATE = {"fails": 0, "until": 0.0}


def _claim_register_fail(tg_id: int, reason: str) -> None:
    _CLAIM_STATE["fails"] += 1
    if _CLAIM_STATE["fails"] >= CLAIM_FAIL_LIMIT:
        _CLAIM_STATE["until"] = time.monotonic() + CLAIM_LOCK_SECONDS
    audit("claim_failed", {"tg_id": tg_id, "reason": reason,
                           "fails": _CLAIM_STATE["fails"]})


def claim_admin(code: str, tg_id: int) -> dict:
    """Одноразовый код из SSH-меню → tg_id становится админом.

    Код сравнивается constant-time и ГАСНЕТ после первого успеха: окно
    «кто первый написал — тот и админ» не открывается никогда, даже на
    свежем рестарте (грабли goTelegram: окно от старта процесса
    открывалось заново при каждом ребуте — см. bot.py:754 у соседей).
    Плюс TTL: код, выпущенный и забытый, не ждёт перехвата вечно.
    """
    if not isinstance(tg_id, int) or tg_id <= 0:
        return {"ok": False, "reason": "bad_id"}
    if time.monotonic() < _CLAIM_STATE["until"]:
        audit("claim_failed", {"tg_id": tg_id, "reason": "rate_limited"})
        return {"ok": False, "reason": "rate_limited"}
    code = str(code or "").strip()
    env = env_read()
    expected = (env.get("ADMIN_CLAIM_TOKEN") or "").strip()
    if not expected:
        return {"ok": False, "reason": "closed"}
    if time.time() > claim_expires(env):
        # Истёкший — отдельная причина: оператор должен понять «выпусти
        # новый код», а не «я опечатался». Сам код при этом гасим — он
        # уже осел в истории терминала и годным быть не должен.
        env_write({"ADMIN_CLAIM_TOKEN": "", "ADMIN_CLAIM_EXPIRES": ""})
        _claim_register_fail(tg_id, "expired")
        return {"ok": False, "reason": "expired"}
    # Сравниваем БАЙТЫ, а не строки: hmac.compare_digest на строке с
    # неASCII бросает TypeError, а код привязки приходит из Telegram —
    # любой посторонний мог прислать боту «/start Пароль» и уронить эту
    # задачу в root-демоне. Ответа клиент не получал вовсе, бот показывал
    # «демон недоступен», и попытка не попадала в счётчик перебора.
    if not code or not hmac.compare_digest(
            code.encode("utf-8", "ignore"), expected.encode("utf-8", "ignore")):
        _claim_register_fail(tg_id, "wrong_code")
        return {"ok": False, "reason": "wrong_code"}
    admins = admins_from_env(env)
    if tg_id not in admins:
        admins.append(tg_id)
    env_write({
        "ADMIN_IDS": ",".join(str(a) for a in admins),
        "ADMIN_CLAIM_TOKEN": "",
        "ADMIN_CLAIM_EXPIRES": "",
    })
    _CLAIM_STATE["fails"] = 0
    _CLAIM_STATE["until"] = 0.0
    audit("claim", {"tg_id": tg_id})
    return {"ok": True, "admins": admins}


# ── Аудит ───────────────────────────────────────────────────────────────
def audit(event: str, payload: dict) -> None:
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        record = {"ts": int(time.time()), "event": event}
        record.update(payload)
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.chmod(AUDIT_LOG, 0o600)
    except OSError as exc:
        logger.warning("audit write failed: %s", exc)


# ── Исполнение действия ─────────────────────────────────────────────────
# Мутирующие действия строго по одному: два одновременных engine_apply —
# это два перезапуска dnsdist друг под другом. Читающие — до трёх разом.
_MUTATE_LOCK = asyncio.Lock()
_READ_SEM = asyncio.Semaphore(3)


async def run_action(name: str, args: list, caller: int,
                     env_extra=None) -> dict:
    if name not in ACTIONS:
        raise ValueError(f"unknown action: {name}")
    mutating, timeout, validator = ACTIONS[name]
    args = validator([str(a) for a in (args or [])])
    # Окружение проверяется ЗДЕСЬ, а не только в handle_request: это
    # единственное место, где действие исполняется, и обойти его другим
    # вызывающим нельзя.
    env_extra = _check_env(name, env_extra)

    guard = _MUTATE_LOCK if mutating else _READ_SEM
    if mutating and _MUTATE_LOCK.locked():
        return {"rc": -2, "output": "", "busy": True}

    async with guard:
        started = time.monotonic()
        env = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "HOME": "/root",
            # Прогресс-спиннеры мерят ширину терминала; его нет.
            "TERM": "dumb",
            "COLUMNS": "100",
        }
        for key in ("GODNS_BOT_LIB", "GODNS_DIR", "GODNS_ETC", "GODNS_CONFIG"):
            if key in os.environ:
                env[key] = os.environ[key]
        # Проверенные пары из params["env"] — поверх своего окружения.
        # Ключи — только из ACTION_ENV, так что PATH и пути библиотеки
        # отсюда не подменить.
        env.update(env_extra)
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", ACTIONS_SH, name, *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            # stderr в stdout: отчёты пишут в оба потока вперемешку, и
            # порядок строк важнее их происхождения.
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout)
            rc = proc.returncode
        except asyncio.TimeoutError:
            # Сначала группе — SIGTERM (дети вроде curl/apt должны успеть
            # прибраться), потом SIGKILL. Приём из goTelegram bot.py:sh().
            try:
                os.killpg(os.getpgid(proc.pid), 15)
            except OSError:
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                except OSError:
                    proc.kill()
                await proc.wait()
            audit("action_timeout", {"tg_id": caller, "action": name})
            return {"rc": -1, "output": "", "timeout": timeout}
        output = clip_output(scrub_secrets(
            strip_ansi(raw.decode("utf-8", errors="replace")),
            mask_path=name in MASK_PATH_ACTIONS))
        record = {
            "tg_id": caller, "action": name, "args": args, "rc": rc,
            "ms": int((time.monotonic() - started) * 1000),
        }
        # В журнал — только ИМЕНА ключей окружения, никогда значение:
        # путь DoH своего апстрима может быть чужим секретом, а аудит
        # читают из веб-части.
        if env_extra:
            record["env_keys"] = sorted(env_extra)
        audit("action", record)
        return {"rc": rc, "output": output}


# ── JSON-RPC поверх сокета ──────────────────────────────────────────────
def _peer_allowed(writer: asyncio.StreamWriter) -> bool:
    """SO_PEERCRED: пускаем только root и пользователя бота."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return False
    try:
        creds = sock.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", creds)
    except OSError:
        return False
    if uid == 0:
        return True
    try:
        return uid == pwd.getpwnam(BOT_USER).pw_uid
    except KeyError:
        return False


def _rpc_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


async def handle_request(req: dict) -> dict:
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(req_id, -32602, "params must be an object")

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "pong": True,
            "botd": BOTD_VERSION,
            "claim_open": claim_is_open(env_read()),
        }}

    if method == "claim":
        result = claim_admin(params.get("code"), params.get("tg_id"))
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    if method == "admins":
        return {"jsonrpc": "2.0", "id": req_id,
                "result": {"admins": admins_from_env(env_read())}}

    if method == "action":
        caller = params.get("tg_id") or 0
        # Авторизацию вызывающего демон проверяет САМ, не веря боту:
        # действия исполняются только для известных админов.
        if caller not in admins_from_env(env_read()):
            return _rpc_error(req_id, 403, "caller is not an admin")
        try:
            result = await run_action(
                str(params.get("name") or ""), params.get("args"), caller,
                params.get("env"))
        except ValueError as exc:
            return _rpc_error(req_id, -32602, str(exc))
        except TypeError as exc:
            # ГРАБЛИ: `args` не список и не строка (например число) — и
            # `for a in args` бросал TypeError, который никто не ловил.
            # Соединение закрывалось БЕЗ ответа, а бот показывал «демон не
            # отвечает», хотя демон жив. Любой кривой запрос обязан
            # получить внятный отказ, а не тишину.
            return _rpc_error(req_id, -32602, "args: ожидается список строк")
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    return _rpc_error(req_id, -32601, f"unknown method: {method}")


async def _client_connected(reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter) -> None:
    try:
        if not _peer_allowed(writer):
            writer.close()
            return
        while True:
            try:
                line = await reader.readline()
            # ValueError, а НЕ LimitOverrunError: при переполнении лимита
            # потока readline() бросает именно его, и он не подкласс
            # LimitOverrunError. Из-за этого задуманный ниже отказ
            # «request too large» не отправлялся никогда — соединение просто
            # рвалось, а asyncio писал «Task exception was never retrieved».
            except (ConnectionResetError, ValueError, asyncio.LimitOverrunError):
                break
            if not line:
                break
            if len(line) > MAX_REQUEST:
                writer.write(json.dumps(
                    _rpc_error(None, -32600, "request too large")
                ).encode() + b"\n")
                await writer.drain()
                break
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                writer.write(json.dumps(
                    _rpc_error(None, -32700, "parse error")).encode() + b"\n")
                await writer.drain()
                continue
            resp = await handle_request(req)
            writer.write(json.dumps(resp, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def serve() -> None:
    try:
        os.unlink(SOCK_PATH)
    except FileNotFoundError:
        pass
    server = await asyncio.start_unix_server(
        _client_connected, path=SOCK_PATH, limit=MAX_REQUEST + 4096)
    os.chmod(SOCK_PATH, 0o660)
    try:
        import grp
        gid = grp.getgrnam(BOT_GROUP).gr_gid
        os.chown(SOCK_PATH, 0, gid)
    except (KeyError, OSError) as exc:
        logger.warning("cannot chown socket to %s: %s", BOT_GROUP, exc)
    logger.info("godns-botd listening on %s", SOCK_PATH)
    async with server:
        await server.serve_forever()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if os.geteuid() != 0:
        logger.error("godns-botd must run as root")
        return 1
    if not os.path.isfile(ACTIONS_SH):
        logger.error("actions.sh not found: %s", ACTIONS_SH)
        return 1
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
