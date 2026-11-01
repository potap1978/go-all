#!/usr/bin/env python3
"""
goTelegram Pro Bot - MTProxy Management for Linux
Manages telemt engine via Telegram interface with full CLI feature parity
Uses python-telegram-bot v21+
Supports EN/RU UI with per-user language preferences.
"""

import asyncio
import csv
import fcntl
import base64
import hashlib
import io
import html
import json
import logging
import os
import re
import signal
import shlex
import shutil
import subprocess
import sys
import time
import toml
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Tuple, Optional, List, Dict, Any
from urllib.parse import quote

from dotenv import load_dotenv
from telegram import (
    ReplyKeyboardRemove,
    ReplyKeyboardMarkup,
    KeyboardButton,
    MenuButtonWebApp,
    MenuButtonCommands,
    BotCommand,
    WebAppInfo,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError, BadRequest

# i18n — loaded from the bot directory next to this file
_BOT_DIR = Path(__file__).resolve().parent
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))
try:
    from i18n import (
        t as _t,
        tf as _tf,
        t_in as _t_in,
        get_user_lang,
        set_user_lang,
        get_language_name,
        SUPPORTED_LANGS,
    )
except Exception as _i18n_err:  # pragma: no cover — defensive fallback
    logging.warning("i18n module not available: %s", _i18n_err)

    def _t(user_id, key, default=None):
        return default if default is not None else key

    def _t_in(lang, key, default=None):
        return default if default is not None else key

    def _tf(user_id, key, *args, default=None):
        template = default if default is not None else key
        try:
            return template % args if args else template
        except Exception:
            return template

    def get_user_lang(user_id):
        return "en"

    def set_user_lang(user_id, code):
        return False

    def get_language_name(code):
        return code

    SUPPORTED_LANGS = ("en",)


def _uid(update: Optional[Update]) -> Optional[int]:
    """Extract user id from an update (if any)."""
    if update is None:
        return None
    user = getattr(update, "effective_user", None)
    return user.id if user else None

# Load environment variables
load_dotenv()

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
# httpx на уровне INFO печатает полный URL запроса, а в URL Bot API лежит ТОКЕН.
# В журнале он оказывался десятками раз, а веб-панель умеет показывать логи этого
# юнита — то есть токен бота утекал любому, кто дошёл до просмотра логов.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)

# ============================================================================
# CONFIGURATION
# ============================================================================

GOTELEGRAM_CONFIG = "/opt/gotelegram/config.json"


def _read_gotelegram_version() -> str:
    """Версия goTelegram — из config.json (единый источник правды), fallback-константа."""
    try:
        with open(GOTELEGRAM_CONFIG, encoding="utf-8") as _f:
            _v = json.load(_f).get("version")
            if _v:
                return str(_v)
    except Exception:
        pass
    return "2.9.0"


GOTELEGRAM_VERSION = _read_gotelegram_version()
DISABLED_USERS_FILE = "/opt/gotelegram/disabled_users.json"
USER_STATS_HISTORY = "/opt/gotelegram/user_stats_history.csv"
USER_LOCK_FILE = "/run/gotelegram/admin-users.lock"
# config.json пишут и бот, и панель, и install.sh. Запись атомарна (tmp+replace),
# но без общего лока два одновременных изменения (язык из панели, шаблон из бота)
# теряют одно из них — классический lost update.
CONFIG_JSON_LOCK = "/run/gotelegram/admin-config.lock"
TELEMT_CONFIG = "/etc/telemt/config.toml"
TELEMT_SERVICE = "telemt"
WEBSITE_ROOT = "/var/www/gotelegram-site"
BACKUP_DIR = "/opt/gotelegram/backups"
BACKUP_SCHEDULE_FILE = "/opt/gotelegram/backup_schedule.json"
TEMPLATES_CATALOG = "/opt/gotelegram/templates_catalog.json"
INSTALL_SH = "/opt/gotelegram/install.sh"

PROMO_LINK_1 = "https://vk.cc/ct29NQ"
PROMO_LINK_2 = "https://vk.cc/cUxAhj"
TIP_LINK = "https://pay.cloudtips.ru/p/7410814f"
YOUTUBE_LINK = os.getenv("GOTELEGRAM_YOUTUBE_LINK", "").strip()
PROMO_STAMP_FILE = "/opt/gotelegram/.promo_bot_last_shown"

BOT_TOKEN = os.getenv("BOT_TOKEN")
ENV_FILE = "/opt/gotelegram-bot/.env"
ADMIN_WEB_SERVICE = "gotelegram-admin"
ADMIN_WEB_PORT = 1984


def format_bytes_human(value: int) -> str:
    value = max(0, int(value or 0))
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    if value < 1024 * 1024 * 1024:
        return f"{value / 1024 / 1024:.1f} MB"
    return f"{value / 1024 / 1024 / 1024:.1f} GB"

# ── Загрузка ALLOWED_IDS ────────────────────────────────────────────────────
# Поддерживает запятую, пробел, или их комбинацию как разделитель
ALLOWED_IDS: set = set()
_WAITING_FOR_ADMIN = False  # True если список пуст → ждём первого админа

# Бот с пустым ALLOWED_IDS раньше ждал первого админа БЕССРОЧНО: любой, кто
# первым напишет /start, получал root-контроль над сервером. Теперь заявку
# принимаем только в короткое окно после старта процесса.
_BOT_START_MONOTONIC = time.monotonic()
try:
    ADMIN_CLAIM_WINDOW_SECONDS = max(
        60, int(os.getenv("GOTELEGRAM_ADMIN_CLAIM_WINDOW", "600"))
    )
except ValueError:
    ADMIN_CLAIM_WINDOW_SECONDS = 600


def _load_allowed_ids() -> None:
    """Загрузить ALLOWED_IDS из переменной окружения."""
    global ALLOWED_IDS, _WAITING_FOR_ADMIN
    raw = os.getenv("ALLOWED_IDS", "")
    ALLOWED_IDS = set()
    # Разделители: запятая, пробел, или оба
    for part in re.split(r'[,\s]+', raw):
        part = part.strip()
        if part:
            try:
                ALLOWED_IDS.add(int(part))
            except ValueError:
                logging.warning(f"Invalid ALLOWED_IDS entry: {part}")
    _WAITING_FOR_ADMIN = len(ALLOWED_IDS) == 0


def _save_allowed_ids() -> bool:
    """Сохранить ALLOWED_IDS в .env файл и обновить os.environ."""
    global _WAITING_FOR_ADMIN
    ids_str = ",".join(str(i) for i in sorted(ALLOWED_IDS))
    os.environ["ALLOWED_IDS"] = ids_str
    _WAITING_FOR_ADMIN = len(ALLOWED_IDS) == 0

    if not os.path.exists(ENV_FILE):
        # без .env изменение не переживёт рестарт — вызывающий должен об этом сказать
        return False

    tmp = f"{ENV_FILE}.tmp.{os.getpid()}"
    try:
        with open(ENV_FILE, "r") as f:
            lines = f.readlines()

        found = False
        new_lines = []
        for line in lines:
            if line.strip().startswith("ALLOWED_IDS="):
                if ids_str:
                    new_lines.append(f"ALLOWED_IDS={ids_str}\n")
                # Если пусто — удаляем строку
                found = True
            else:
                new_lines.append(line)

        if not found and ids_str:
            new_lines.append(f"ALLOWED_IDS={ids_str}\n")

        # атомарно, как save_json: запись «на месте» при сбое усекала .env вместе с BOT_TOKEN
        with open(tmp, "w") as f:
            f.writelines(new_lines)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, ENV_FILE)

        logger.info(f"ALLOWED_IDS updated in .env: {ids_str or '(empty)'}")
        return True
    except OSError as e:
        logger.error(f"Failed to update .env: {e}")
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


_load_allowed_ids()

LITE_DOMAINS = [
    "google.com",
    "microsoft.com",
    "cloudflare.com",
    "apple.com",
    "amazon.com",
    "github.com",
    "stackoverflow.com",
    "medium.com",
    "wikipedia.org",
    "coursera.org",
    "udemy.com",
    "habr.com",
    "stepik.org",
    "duolingo.com",
    "khanacademy.org",
    "bbc.com",
    "reuters.com",
    "nytimes.com",
    "ted.com",
    "zoom.us",
]

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


async def sh(*args, timeout: int = 60) -> Tuple[int, str, str]:
    """Execute shell command asynchronously.

    Args:
        *args: Command and arguments
        timeout: Timeout in seconds

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
        return (
            process.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        # Сначала мягко: SIGTERM всей группе (не только bash — иначе внуки
        # apt/curl/wgcf/wg-quick остаются сиротами и держат dpkg-lock), даём
        # до 5 с на корректное завершение и только потом SIGKILL. Жёсткий kill
        # посреди записи /etc/telemt/config.toml оставлял битый конфиг.
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except Exception:
            logger.warning("sh(): process group did not exit on SIGTERM, sending SIGKILL")
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            try:
                await process.wait()
            except Exception:
                pass
        return (-1, "", f"Command timeout after {timeout}s")
    except Exception as e:
        return (-1, "", str(e))


# Per-host mutex preventing concurrent install.sh --action invocations. Two
# admins hitting "change template" at the same second could race each other
# and corrupt /var/www/gotelegram-site. One global lock is fine — these are
# rare operations and should serialize cleanly.
_BOT_ACTION_LOCK = asyncio.Lock()

# asyncio.create_task() возвращает "слабую" ссылку: без хранения задача может
# быть собрана GC до завершения. Держим их в множестве до done-callback.
_BACKGROUND_TASKS: set = set()


def spawn_task(coro) -> "asyncio.Task":
    """Запустить фоновую корутину, не теряя ссылку на задачу."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task

# Allowed template-id shape: catalog ids are [a-zA-Z0-9_-], never longer than 64.
# This is a defense-in-depth check before we hand the value to subprocess.
_TPL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Allowed Lite mask domain shape — simple DNS hostname, up to 253 chars total.
# Each label 1–63 chars, labels separated by dots, alphanumerics + hyphens.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:(?!-)[A-Za-z0-9-]{1,63}(?<!-)\.)+"
    r"(?!-)[A-Za-z0-9-]{2,63}(?<!-)$"
)
# Совпадает с USER_RE панели: имя из одних точек/дефисов панель отвергала,
# а бот принимал — ключ появлялся, но в веб-интерфейсе был невидим.
_USER_NAME_RE = re.compile(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9_.-]{1,48}$")
MAX_UNIQUE_IP_LIMIT = 1000000
MAX_UNIQUE_IPS_MIN_MESSAGE = (
    "Лимит в один адрес рвёт обычный телефон: при переходе Wi-Fi ↔ LTE адрес меняется, старые соединения ещё живы, и клиент упирается в потолок. Замер на боевом сервере: связь пропадала в среднем на 20 секунд, в худшем случае на 6 минут. Минимум — 2. Ноль снимает персональный лимит."
)
TELEMT_RESTART_DEBOUNCE_SECONDS = float(os.getenv("GOTELEGRAM_TELEMT_RESTART_DEBOUNCE", "8"))
_LAST_TELEMT_RESTART = 0.0
# True, пока висит отложенный (коалесцирующий) рестарт telemt — второй не нужен
_TELEMT_RESTART_PENDING = False
# True — в конфиг попало то, что ядро читает только при старте ([web.limits])
_TELEMT_RESTART_REQUIRED = False


class FileLockTimeout(Exception):
    """Межпроцессный лок не удалось взять за отведённое время."""


class FileLock:
    """Межпроцессный лок на файле (совместим с веб-админкой по flock).

    Раньше делал блокирующий fcntl.flock(LOCK_EX) прямо в event loop: пока
    веб-админка держала лок, вставал ВЕСЬ бот (все хендлеры, включая /start).
    Теперь LOCK_EX|LOCK_NB с ретраями: `async with` спит через asyncio.sleep
    и не блокирует loop, `with` оставлен для синхронного кода.
    """

    def __init__(self, path: str, timeout: float = 10.0, poll: float = 0.15):
        self.path = Path(path)
        self.timeout = float(timeout)
        self.poll = float(poll)
        self.handle = None

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # "a" вместо "w": не усекаем файл до того, как взяли лок.
        self.handle = self.path.open("a", encoding="utf-8")

    def _try_acquire(self) -> bool:
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            return False

    def _release(self) -> None:
        if self.handle:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self.handle.close()
            except Exception:
                pass
            self.handle = None

    def __enter__(self):
        self._open()
        deadline = time.monotonic() + self.timeout
        while True:
            if self._try_acquire():
                return self
            if time.monotonic() >= deadline:
                self._release()
                raise FileLockTimeout(str(self.path))
            time.sleep(self.poll)

    def __exit__(self, exc_type, exc, tb):
        self._release()

    async def __aenter__(self):
        self._open()
        deadline = time.monotonic() + self.timeout
        while True:
            if self._try_acquire():
                return self
            if time.monotonic() >= deadline:
                self._release()
                raise FileLockTimeout(str(self.path))
            await asyncio.sleep(self.poll)

    async def __aexit__(self, exc_type, exc, tb):
        self._release()


async def run_bot_action(action: str, timeout: int = 300, **params) -> Dict:
    """Invoke install.sh --action=X --json and parse the JSON result.

    Args:
        action: action name (e.g. "change-template", "change-lite-domain")
        timeout: seconds to wait for completion (long ops: template download can take time)
        **params: arbitrary key→value pairs, each passed as --key=value

    Returns:
        dict with at least {"status": "ok|warn|error", "message": "..."}.
        Transport errors are mapped to {"status":"error","message":..., "code":"transport"}
    """
    cmd = ["bash", INSTALL_SH, f"--action={action}", "--json"]
    for k, v in params.items():
        if v is None:
            continue
        cmd.append(f"--{k.replace('_', '-')}={v}")

    code, stdout, stderr = await sh(*cmd, timeout=timeout)
    stdout = (stdout or "").strip()

    # install.sh may print multiple log lines to stderr; the JSON is on stdout.
    # Pick the last non-empty line that looks like JSON (robust to any stray output).
    json_line = None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            json_line = line
            break

    if json_line:
        try:
            data = json.loads(json_line)
            if isinstance(data, dict) and "status" in data:
                return data
        except json.JSONDecodeError as e:
            logger.warning(f"run_bot_action: JSON parse failed: {e} | line={json_line!r}")

    # No JSON from install.sh — synthesize an error result
    tail = (stderr or "")[-300:] if stderr else ""
    logger.error(
        f"run_bot_action({action}): no JSON output, rc={code}, "
        f"stdout={stdout[-300:]!r}, stderr={tail!r}"
    )
    return {
        "status": "error",
        "message": "install.sh did not return a JSON result",
        "code": "transport",
        "rc": str(code),
    }


def load_json(path: str) -> Optional[Dict]:
    """Load JSON file."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        # Отсутствие файла — норма (например, disabled_users.json есть только
        # если кого-то отключали). Раньше это писало WARNING на каждую операцию
        # с ключами и засоряло журнал, маскируя настоящие ошибки.
        return None
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
        return None


def load_toml(path: str) -> Optional[Dict]:
    """Load TOML file."""
    try:
        with open(path, "r") as f:
            return toml.load(f)
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
        return None


def save_json(path: str, data: Dict) -> bool:
    """Атомарно сохранить JSON: tmp + fsync + os.replace + chmod 600.

    Прямой open(path, "w") усекал реальный файл: конкурирующий читатель
    (веб-админка, install.sh) мог прочитать пустой/битый конфиг.
    """
    directory = os.path.dirname(path) or "."
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        os.makedirs(directory, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.error(f"Failed to save {path}: {e}")
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def template_display_name(template_id: str) -> str:
    """Resolve a template id to a human-friendly name from catalog/config."""
    if not template_id:
        return ""
    if template_id in ("deployed_site", "existing_site"):
        return "Existing deployed site"
    if template_id.startswith("custom_"):
        config = load_json(GOTELEGRAM_CONFIG) or {}
        source = config.get("template_source", "")
        return f"{template_id} ({source})" if source else template_id
    catalog = load_json(TEMPLATES_CATALOG) or {}
    for cat in catalog.get("categories", []):
        for tpl in cat.get("templates", []):
            if tpl.get("id") == template_id:
                return f"{tpl.get('name', template_id)} ({template_id})"
    return template_id


def pro_template_map(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, str]:
    """Return the short callback key -> template id map for this chat."""
    mapping = context.user_data.setdefault("pro_template_map", {})
    if not isinstance(mapping, dict):
        mapping = {}
        context.user_data["pro_template_map"] = mapping
    return mapping


def resolve_pro_template_id(context: ContextTypes.DEFAULT_TYPE, key_or_id: str) -> str:
    """Resolve a short Telegram callback key back to the real template id."""
    mapped = pro_template_map(context).get(key_or_id)
    if mapped:
        return str(mapped)

    catalog = load_json(TEMPLATES_CATALOG) or {}
    for cat in catalog.get("categories", []):
        for tpl in cat.get("templates", []):
            template_id = str(tpl.get("id", ""))
            if hashlib.sha1(template_id.encode("utf-8")).hexdigest()[:12] == key_or_id:
                return template_id

    return str(key_or_id)


def pro_template_key_for_id(context: ContextTypes.DEFAULT_TYPE, template_id: str) -> str:
    """Store a template id behind a short key that fits Telegram callback limits."""
    mapping = pro_template_map(context)
    template_id = str(template_id)
    for key, stored_id in mapping.items():
        if stored_id == template_id:
            return str(key)
    key = hashlib.sha1(template_id.encode("utf-8")).hexdigest()[:12]
    mapping[key] = template_id
    return key


async def safe_edit_message(
    query,
    text: str,
    reply_markup=None,
    parse_mode=None,
    disable_web_page_preview: Optional[bool] = None,
) -> bool:
    """Safely edit message, handling cases where message was deleted or not modified.

    `disable_web_page_preview` is forwarded to edit_message_text when set; omitting
    it keeps Telegram's default (enabled).
    """
    kwargs = {"reply_markup": reply_markup, "parse_mode": parse_mode}
    if disable_web_page_preview is not None:
        kwargs["disable_web_page_preview"] = disable_web_page_preview
    try:
        await query.edit_message_text(text, **kwargs)
        return True
    except BadRequest as e:
        err_msg = str(e).lower()
        if "message is not modified" in err_msg:
            return True  # No change needed, not an error
        if "message to edit not found" in err_msg or "message can't be edited" in err_msg:
            logger.warning(f"Cannot edit message: {e}")
            return False
        raise  # Re-raise unexpected BadRequest


async def _action_busy(query, user_id: Optional[int] = None) -> bool:
    """True + сообщение пользователю, если другое мутирующее действие уже идёт."""
    if not _BOT_ACTION_LOCK.locked():
        return False
    await safe_edit_message(
        query,
        f"<b>{_t(user_id, 'action_busy_title')}</b>\n\n{_t(user_id, 'action_busy_body')}",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_main")]]
        ),
        parse_mode="HTML",
    )
    return True


async def _delete_message_after(message, delay: int = 30) -> None:
    """Delete a Telegram message after `delay` seconds. Errors are swallowed
    (message may already be deleted by the user). Used for ephemeral content
    like promo blocks that should auto-cleanup."""
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception as e:
        logger.debug(f"_delete_message_after: {e}")


async def check_service_status(service: str) -> bool:
    """Check if systemd service is running."""
    code, _, _ = await sh("systemctl", "is-active", service)
    return code == 0


async def get_telemt_version() -> str:
    """Get telemt version."""
    for command in ("telemt", "/usr/local/bin/telemt", "/usr/bin/telemt"):
        for args in (("--version",), ("-V",)):
            code, stdout, _ = await sh(command, *args, timeout=5)
            if code == 0 and stdout.strip():
                return stdout.strip().split()[-1]
    return "unknown"


def _is_docker_running_sync() -> bool:
    """Check if Docker daemon is running."""
    try:
        subprocess.run(
            ["docker", "ps"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return True
    except Exception:
        return False


async def is_docker_running() -> bool:
    """Обёртка: docker ps — синхронный вызов с таймаутом 5с. Внутри async-хендлера
    он вешал весь event loop aiogram, и бот на эти секунды переставал отвечать
    ВСЕМ. Уводим в поток."""
    try:
        return await asyncio.to_thread(_is_docker_running_sync)
    except Exception:
        return False


async def check_old_container() -> Optional[str]:
    """Check for old mtg Docker container (v1 migration)."""
    if not await is_docker_running():
        return None
    code, stdout, _ = await sh("docker", "ps", "-a", "--format", "{{.Names}}")
    if code == 0 and "mtg" in stdout:
        return "mtg"
    return None



def miniapp_url() -> Optional[str]:
    """Публичный адрес мини-аппа на СВОЁМ домене (или None, если выключен).

    Никаких сторонних туннелей: путь лежит в config.json, домен — тот же, что у
    прокси, сертификат уже валидный. Пустой miniapp_path = кнопки нет.
    """
    cfg = load_json(GOTELEGRAM_CONFIG) or {}
    path = str(cfg.get("miniapp_path") or "").strip().strip("/")
    domain = str(cfg.get("domain") or "").strip()
    if not path or not domain:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]{4,64}", path):
        return None
    return f"https://{domain}/{path}/"


# ============================================================================
# ACCESS CONTROL
# ============================================================================


def admin_claim_seconds_left() -> int:
    """Сколько секунд осталось у окна авто-регистрации первого админа."""
    if not _WAITING_FOR_ADMIN:
        return 0
    left = ADMIN_CLAIM_WINDOW_SECONDS - (time.monotonic() - _BOT_START_MONOTONIC)
    return int(left) if left > 0 else 0


ADMIN_CLAIMED_MARKER = "/opt/gotelegram-bot/.admin_claimed"


def admin_claim_open() -> bool:
    """True, если сейчас можно назначить себя первым админом.

    Окно завязано на time.monotonic() от старта процесса, поэтому КАЖДЫЙ рестарт
    юнита (ребут VPS, crash-loop) открывал новые 10 минут «первый написавший —
    root-админ». Если владелец поставил бота, но не нажал /start сразу, любой,
    кто знает @username, мог перехватить сервер. Одноразовый маркер это закрывает.
    """
    try:
        if os.path.exists(ADMIN_CLAIMED_MARKER):
            return False
    except Exception:
        pass
    return _WAITING_FOR_ADMIN and admin_claim_seconds_left() > 0


def is_user_allowed(user_id: int) -> bool:
    """Check if user ID is in ALLOWED_IDS. If list is empty — waiting for admin."""
    if _WAITING_FOR_ADMIN:
        return False  # Никому не даём доступ пока не назначен админ
    return user_id in ALLOWED_IDS


def add_admin(user_id: int) -> bool:
    """Добавить администратора и сохранить в .env."""
    ALLOWED_IDS.add(user_id)
    # bool наружу: «✅ Добавлены» при незаписанном .env был ложью — после рестарта админ пропадал
    saved = _save_allowed_ids()
    logger.info(f"Admin added: {user_id}")
    try:
        Path(ADMIN_CLAIMED_MARKER).parent.mkdir(parents=True, exist_ok=True)
        Path(ADMIN_CLAIMED_MARKER).write_text("1")
    except Exception:
        pass
    return saved


def remove_admin(user_id: int) -> bool:
    """Убрать администратора и сохранить в .env."""
    ALLOWED_IDS.discard(user_id)
    saved = _save_allowed_ids()
    logger.info(f"Admin removed: {user_id}")
    return saved


async def require_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check authorization and send error if not allowed."""
    user_id = update.effective_user.id

    # Режим ожидания первого админа — обрабатывается в cmd_start
    if _WAITING_FOR_ADMIN:
        return False

    if not is_user_allowed(user_id):
        if update.message:
            await update.message.reply_text(
                f"⛔ Доступ запрещён.\nВаш ID: <code>{user_id}</code>",
                parse_mode="HTML",
            )
        logger.warning(f"Unauthorized access attempt from user {user_id}")
        return False
    return True


# ============================================================================
# MAIN MENU
# ============================================================================


# Telegram обрезает подпись, которая не влезает в кнопку, и делает это молча:
# при двух кнопках в ряд «⚡ Сменить режим/шаблон» превращалось в «⚡ Сме...шаблон»,
# а «📡 Обновить telemt» — в «📡 Обн...ь telemt». Понять по такому названию, что
# делает кнопка, нельзя. Ширина зависит от экрана, но в половинную кнопку
# помещается примерно 16 символов; замер по обоим языкам показал, что длиннее
# только эти две подписи (22 и 17 в русском, 22 в английском), остальные парные
# укладываются. Длинную подпись кладём на свою строку целиком: лишний ряд лучше
# нечитаемого названия. Порог общий, поэтому перевод любой длины тоже не сломает
# меню — просто получит отдельную строку.
MENU_PAIR_MAX_CHARS = 16


def _menu_rows(rows: list) -> list:
    """Длинную подпись — на всю ширину, короткие — по две в ряд.

    Ряды из одной кнопки автор сделал такими намеренно (WARP, режим работы,
    мини-апп) — их не трогаем. В парном ряду длинная подпись уезжает на свою
    строку, а оставшаяся короткая не занимает целый ряд впустую, а ждёт
    следующую короткую и встаёт с ней в пару. Порядок кнопок при этом
    сохраняется, поэтому «Бекап» и «Восстановить» оказываются рядом, а не
    порознь.
    """
    out: list = []
    pending = None  # короткая кнопка, оставшаяся без пары

    def flush():
        nonlocal pending
        if pending is not None:
            out.append([pending])
            pending = None

    for row in rows:
        if len(row) != 2:
            flush()
            out.append(row)
            continue
        if all(len(b.text) <= MENU_PAIR_MAX_CHARS for b in row):
            flush()
            out.append(row)
            continue
        for btn in row:
            if len(btn.text) > MENU_PAIR_MAX_CHARS:
                flush()
                out.append([btn])
            elif pending is None:
                pending = btn
            else:
                out.append([pending, btn])
                pending = None
    flush()
    return out


# ── Разделы главного экрана ─────────────────────────────────────────────────
#
# Раньше главный экран был плоским списком из 23 кнопок: каждая новая
# возможность добавляла ещё одну, и структуру никто не перекраивал. Отсюда и
# обрезка подписей — на половину ширины «⚡ Сменить режим/шаблон» не влезает.
#
# Разделы собраны по ЧАСТОТЕ обращения, а не по темам:
#   Ключи        — ядро работы, каждый день;
#   Состояние    — посмотреть, всё ли хорошо;
#   Анти-DPI     — переключают в момент блокировки, поэтому на виду, не в глубине;
#   Обслуживание — действия, которые делают руками изредка;
#   Настройки    — то, что задают один раз.
#
# Порядок и названия совпадают с SSH-меню до буквы: человек, привыкший к одному
# интерфейсу, находит нужное в другом вслепую.
def _back_row(user_id: Optional[int], to: str = "menu_main") -> list:
    return [InlineKeyboardButton(_t(user_id, "btn_back"), callback_data=to)]


def _hum_bytes(n, user_id: Optional[int] = None) -> str:
    """Байты по-человечески. Своя, а не из статистики: там формат завязан на
    показ скоростей, здесь нужны короткие подписи для узкой колонки.

    Единицы берём из словаря: раньше они были вшиты по-русски и в английском
    интерфейсе рядом с «Server addresses» стояло «171.2 КБ»."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    units = (
        (1073741824, _t(user_id, "unit_gb"), 1073741824),
        (1048576, _t(user_id, "unit_mb"), 1048576),
        (1024, _t(user_id, "unit_kb"), 1024),
    )
    for lim, suf, div in units:
        if n >= lim:
            return f"{n / div:.1f} {suf}"
    return f"{n} {_t(user_id, 'unit_b')}"


# Сколько адресов показывать на экране. Сорок строк — это около 2.6 КБ текста,
# втрое ниже потолка Telegram даже с предупреждением о запасном входе.
EDGE_MAX_ROWS = 40
# С отметками DNS строка длиннее на ~15 знаков, и снизу встают плашки о
# пропущенных адресах: строк меньше, адресов в плашке — не больше DNS_LIST_MAX.
EDGE_MAX_ROWS_DNS = 30
DNS_LIST_MAX = 8


def _dns_ip_list(raw: Any) -> List[str]:
    """Список адресов из ответа install.sh: массив или строка через пробел."""
    if isinstance(raw, str):
        raw = raw.split()
    if not isinstance(raw, list):
        return []
    return [str(ip).strip() for ip in raw if str(ip).strip()]


def _dns_missing_text(user_id: Optional[int], name: str, ips: List[str]) -> str:
    """Плашка «адрес X не указан в DNS имени»: клиенты по ссылке туда не попадут.

    Не ошибка сама по себе — адрес мог быть оставлен запасным для SSH, — поэтому
    текст объясняет последствие, а не требует чинить.
    """
    key = "dns_missing_one" if len(ips) == 1 else "dns_missing_many"
    listed = ", ".join(ips[:DNS_LIST_MAX]) + (f" (+{len(ips) - DNS_LIST_MAX})" if len(ips) > DNS_LIST_MAX else "")
    return (_t(user_id, key)
            .replace("{ips}", html.escape(listed))
            .replace("{name}", html.escape(name)))


def _edge_dns_tag(entry: Dict[str, Any]) -> str:
    """Отметка адреса в DNS: «DNS ✓», при заданном имени WEB ещё «WEB ✓/✗»."""
    parts = []
    if isinstance(entry.get("dns_domain"), bool):
        parts.append("DNS " + ("✓" if entry["dns_domain"] else "✗"))
    if isinstance(entry.get("dns_web"), bool):      # null — имя WEB не задано
        parts.append("WEB " + ("✓" if entry["dns_web"] else "✗"))
    return " ".join(parts)


async def cb_edgeips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Распределение клиентов по адресам сервера.

    Экран имеет смысл только на машине с несколькими внешними адресами: там
    домен раздаётся несколькими A-записями, и вопрос «какой адрес замолчал»
    возникает первым. На одном адресе пункт в меню не показывается вовсе.
    """
    query = update.callback_query
    await query.answer()
    if not await require_auth(update, context):
        return
    user_id = _uid(update)
    r = await run_bot_action("edgeips-status", timeout=60)
    ips = r.get("ips") or []
    # DNS сверяли (dig ответил) — только тогда утверждаем «есть/нет в DNS».
    # Старый install.sh поля dns не отдаёт: тогда отметок нет вовсе.
    dns = r.get("dns") if isinstance(r.get("dns"), dict) else {}
    dns_checked = dns.get("checked") is True

    lines = [f"<b>{_t(user_id, 'edge_title')}</b>", ""]
    if not ips:
        lines.append(_t(user_id, "edge_nodata"))
    else:
        head = (f"{_t(user_id, 'edge_addr'):<16}{_t(user_id, 'edge_w15'):>9}"
                f"{_t(user_id, 'edge_w1h'):>9}{_t(user_id, 'edge_w1d'):>9}")
        lines.append(f"<code>{html.escape(head)}  ⎯</code>")
        # Потолок строк: сообщение Telegram обрывается на 4096 символах, и на
        # машине с несколькими десятками адресов экран падал целиком с
        # «message is too long» — ошибкой, которую safe_edit_message не ловит.
        shown = ips[:EDGE_MAX_ROWS_DNS if dns_checked else EDGE_MAX_ROWS]
        for e in shown:
            reach = str(e.get("reach") or "")
            mark = "✓" if reach == "ok" else ("✗" if reach == "down" else "⚠")
            # ip и conns приходят из ядра и из счётчиков, но экранируем их так
            # же, как warning: защита не должна зависеть от того, во что мы
            # верим про источник.
            addr = html.escape(str(e.get("ip", ""))[:16])
            conns = html.escape(str(e.get("conns", 0)))
            dns_tag = _edge_dns_tag(e) if dns_checked else ""
            lines.append(
                f"<code>{addr:<16}"
                f"{_hum_bytes(e.get('b15m'), user_id):>9}"
                f"{_hum_bytes(e.get('b1h'), user_id):>9}"
                f"{_hum_bytes(e.get('b1d'), user_id):>9}</code>  {mark} "
                f"<i>{_t(user_id, 'edge_conns')}: {conns}</i>"
                + (f" · <code>{dns_tag}</code>" if dns_tag else "")
            )
        if len(ips) > len(shown):
            lines.append(f"<i>{_t(user_id, 'edge_more').format(n=len(ips) - len(shown))}</i>")
        lines += ["", _t(user_id, "edge_hint")]
        if dns_checked:
            lines.append(_t(user_id, "edge_dns_legend"))
            domain = str(r.get("domain") or (load_json(GOTELEGRAM_CONFIG) or {}).get("domain") or "")
            web_host = str(r.get("web_host") or "")
            for name, missing in ((domain, _dns_ip_list(dns.get("domain_missing"))),
                                  (web_host, _dns_ip_list(dns.get("web_missing")))):
                if name and missing:
                    lines += ["", "⚠️ " + _dns_missing_text(user_id, name, missing)]

    # Запасной вход по SSH. Показываем только когда он сломан: несколько адресов
    # защищают клиентов, но администратора — лишь до тех пор, пока по запасному
    # адресу можно войти самому.
    ssh = r.get("ssh") or {}
    if ssh and not ssh.get("ok", True):
        warning = html.escape(str(ssh.get("warning") or "").strip())
        if warning:
            lines += ["", f"⚠️ <b>{_t(user_id, 'edge_ssh_bad')}</b>", f"<i>{warning}</i>"]

    rows = [[InlineKeyboardButton(_t(user_id, "btn_refresh"), callback_data="edgeips")],
            _back_row(user_id, "sec_state")]
    await safe_edit_message(query, "\n".join(lines),
                            reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")


async def cb_sec_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Раздел «Состояние» — только смотреть, ничего не менять."""
    query = update.callback_query
    await query.answer()
    if not await require_auth(update, context):
        return
    user_id = _uid(update)
    rows = [
        [InlineKeyboardButton(_t(user_id, "menu_status"), callback_data="menu_status")],
        [InlineKeyboardButton(_t(user_id, "menu_stats"), callback_data="menu_stats")],
        [InlineKeyboardButton(_t(user_id, "menu_logs"), callback_data="menu_logs")],
    ]
    # Пункт появляется только на машине с несколькими адресами — иначе он
    # рассказывал бы об одной строке, которая и так видна в статусе.
    try:
        _ei = await run_bot_action("edgeips-status", timeout=40)
        if int(_ei.get("count") or 0) > 1:
            rows.append([InlineKeyboardButton(_t(user_id, "edge_title"), callback_data="edgeips")])
    except Exception as exc:
        logger.debug("edgeips count: %s", exc)
    rows += [
        _back_row(user_id),
    ]
    await safe_edit_message(
        query,
        f"<b>{_t(user_id, 'sec_state')}</b>\n\n{_t(user_id, 'sec_state_hint')}",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML",
    )


async def cb_sec_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Раздел «Обслуживание» — действия, которые делают изредка и руками."""
    query = update.callback_query
    await query.answer()
    if not await require_auth(update, context):
        return
    user_id = _uid(update)
    rows = [
        [InlineKeyboardButton(_t(user_id, "menu_restart"), callback_data="menu_restart")],
        [InlineKeyboardButton(_t(user_id, "menu_update"), callback_data="menu_update")],
        [InlineKeyboardButton(_t(user_id, "menu_backup"), callback_data="menu_backup")],
        [InlineKeyboardButton(_t(user_id, "menu_restore"), callback_data="menu_restore")],
        _back_row(user_id),
    ]
    await safe_edit_message(
        query,
        f"<b>{_t(user_id, 'sec_service')}</b>\n\n{_t(user_id, 'sec_service_hint')}",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML",
    )


async def cb_sec_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Раздел «Настройки» — то, что задают один раз. Удаление последним."""
    query = update.callback_query
    await query.answer()
    if not await require_auth(update, context):
        return
    user_id = _uid(update)
    rows = [
        [InlineKeyboardButton(_t(user_id, "sub_site"), callback_data="sub_site")],
        [InlineKeyboardButton(_t(user_id, "warp_btn_menu"), callback_data="menu_warp")],
        [InlineKeyboardButton(_t(user_id, "web_btn_menu"), callback_data="menu_web")],
        [InlineKeyboardButton(_t(user_id, "sub_panel"), callback_data="sub_panel")],
        [
            InlineKeyboardButton(_t(user_id, "menu_admins"), callback_data="menu_admins"),
            InlineKeyboardButton(_t(user_id, "menu_language"), callback_data="menu_lang"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "menu_credits"), callback_data="menu_credits"),
            InlineKeyboardButton(_t(user_id, "menu_promo"), callback_data="menu_promo"),
        ],
        # Разрушительное — последним и отдельной строкой, подальше от случайного касания.
        [InlineKeyboardButton(_t(user_id, "menu_remove"), callback_data="menu_remove")],
        _back_row(user_id),
    ]
    await safe_edit_message(
        query,
        f"<b>{_t(user_id, 'sec_settings')}</b>\n\n{_t(user_id, 'sec_settings_hint')}",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML",
    )


async def cb_sub_site(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Режим и сайт: смена режима, шаблон, SSL."""
    query = update.callback_query
    await query.answer()
    if not await require_auth(update, context):
        return
    user_id = _uid(update)
    rows = [
        [InlineKeyboardButton(_t(user_id, "menu_change"), callback_data="menu_change")],
        [InlineKeyboardButton(_t(user_id, "menu_website"), callback_data="menu_website")],
        _back_row(user_id, "sec_settings"),
    ]
    await safe_edit_message(
        query,
        f"<b>{_t(user_id, 'sub_site')}</b>\n\n{_t(user_id, 'sub_site_hint')}",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML",
    )


async def cb_sub_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Панель: мини-апп и доступ по туннелю — это одна и та же панель.

    До перекроя они были двумя разными кнопками на главном экране: одна
    запускала мини-апп, вторая показывала инструкцию по SSH-туннелю к тому же
    самому. Из-за этого в них и путались.
    """
    query = update.callback_query
    await query.answer()
    if not await require_auth(update, context):
        return
    user_id = _uid(update)
    rows = []
    url = miniapp_url()
    if url:
        rows.append([InlineKeyboardButton(_t(user_id, "btn_miniapp"), web_app=WebAppInfo(url=url))])
    rows.append([InlineKeyboardButton(_t(user_id, "ma_title"), callback_data="menu_miniapp")])
    rows.append([InlineKeyboardButton(_t(user_id, "menu_admin_web"), callback_data="menu_admin_web")])
    rows.append(_back_row(user_id, "sec_settings"))
    hint = _t(user_id, "sub_panel_on") if url else _t(user_id, "sub_panel_off")
    await safe_edit_message(
        query,
        f"<b>{_t(user_id, 'sub_panel')}</b>\n\n{hint}",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML",
    )


def get_main_menu(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """Главный экран: пять разделов вместо плоского списка."""
    rows = [
        [
            InlineKeyboardButton(_t(user_id, "sec_keys"), callback_data="menu_users"),
            InlineKeyboardButton(_t(user_id, "sec_state"), callback_data="sec_state"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "btn_antidpi"), callback_data="menu_antidpi"),
            InlineKeyboardButton(_t(user_id, "sec_service"), callback_data="sec_service"),
        ],
        [InlineKeyboardButton(_t(user_id, "sec_settings"), callback_data="sec_settings")],
    ]
    # Мини-апп — отдельной кнопкой: WebApp нельзя положить в подменю так, чтобы
    # он открывался одним касанием, а это самый частый вход в панель.
    _ma = miniapp_url()
    if _ma:
        rows.append([InlineKeyboardButton(_t(user_id, "btn_miniapp"), web_app=WebAppInfo(url=_ma))])
    # «Установить» внизу и только пока прокси не установлен: на настроенном
    # сервере кнопка бессмысленна и лишь занимает место.
    if not _is_proxy_installed():
        rows.append([InlineKeyboardButton(_t(user_id, "menu_install"), callback_data="menu_install")])
    rows.append([InlineKeyboardButton(_t(user_id, "menu_close"), callback_data="close_menu")])
    return InlineKeyboardMarkup(rows)


def _get_main_menu_legacy(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """Прежний плоский экран. Оставлен на случай отката, из меню не вызывается."""
    buttons = [
        [
            InlineKeyboardButton(_t(user_id, "menu_install"), callback_data="menu_install"),
            InlineKeyboardButton(_t(user_id, "menu_status"), callback_data="menu_status"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "menu_link"), callback_data="menu_link"),
            InlineKeyboardButton(_t(user_id, "menu_share"), callback_data="menu_share"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "menu_restart"), callback_data="menu_restart"),
            InlineKeyboardButton(_t(user_id, "menu_logs"), callback_data="menu_logs"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "menu_change"), callback_data="menu_change"),
            InlineKeyboardButton(_t(user_id, "menu_backup"), callback_data="menu_backup"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "menu_restore"), callback_data="menu_restore"),
            InlineKeyboardButton(_t(user_id, "menu_update"), callback_data="menu_update"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "menu_website"), callback_data="menu_website"),
            InlineKeyboardButton(_t(user_id, "menu_promo"), callback_data="menu_promo"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "warp_btn_menu"), callback_data="menu_warp"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "menu_stats"), callback_data="menu_stats"),
            InlineKeyboardButton(_t(user_id, "menu_users"), callback_data="menu_users"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "btn_antidpi"), callback_data="menu_antidpi"),
        ],
        *([[InlineKeyboardButton(_t(user_id, "btn_miniapp"), web_app=WebAppInfo(url=_MA_URL))]]
          if (_MA_URL := miniapp_url()) else []),
        [
            InlineKeyboardButton(_t(user_id, "menu_admin_web"), callback_data="menu_admin_web"),
            InlineKeyboardButton(_t(user_id, "menu_admins"), callback_data="menu_admins"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "menu_remove"), callback_data="menu_remove"),
            InlineKeyboardButton(_t(user_id, "menu_credits"), callback_data="menu_credits"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "menu_language"), callback_data="menu_lang"),
            InlineKeyboardButton(_t(user_id, "menu_close"), callback_data="close_menu"),
        ],
    ]
    return InlineKeyboardMarkup(_menu_rows(buttons))


def _is_proxy_installed() -> bool:
    """Установлен ли прокси. Ошибка чтения трактуется как «установлен»: лишняя
    кнопка безобиднее, чем предложение переустановить рабочий сервер."""
    try:
        return bool((load_json(GOTELEGRAM_CONFIG) or {}).get("mode"))
    except Exception:
        return True


# ============================================================================
# COMMANDS
# ============================================================================



KEYBOARD_CLEANED_FILE = "/opt/gotelegram-bot/.keyboard_cleaned.json"

# ── Reply-клавиатура: постоянные кнопки под полем ввода ─────────────────────
#
# Отличие первое: она живёт НЕ у сообщения, а в самом чате, и держится там,
# пока кто-то явно не пришлёт ReplyKeyboardRemove. Отсюда соседний
# _drop_foreign_keyboard, убирающий клавиатуру прежнего владельца токена. Наша
# пользуется тем же свойством: показать достаточно один раз, дальше Telegram
# держит её сам, и слать её при каждом сообщении не нужно.
#
# Отличие второе: её кнопки присылают ОБЫЧНЫЙ ТЕКСТ, а не callback_data.
# Поэтому опереться на cb_*-обработчики нельзя — они требуют
# update.callback_query. Берём команды, которые и так работают от сообщений:
# cmd_status, cmd_logs, cmd_help; для «Меню» шлём новое сообщение с обычной
# inline-клавиатурой.
REPLY_KB_STATE_FILE = "/opt/gotelegram-bot/.reply_keyboard.json"

# ключ i18n → действие. Порядок задаёт раскладку: по две кнопки в ряд.
REPLY_KB_BUTTONS = [
    ("rkb_menu", "menu"),
    ("rkb_status", "status"),
    ("rkb_logs", "logs"),
    ("rkb_help", "help"),
]


def _reply_kb_enabled(chat_id: int) -> bool:
    """По умолчанию включена; выключается персонально командой /keyboard."""
    try:
        off = {str(x) for x in (load_json(REPLY_KB_STATE_FILE) or {}).get("off", [])}
    except Exception:
        off = set()
    return str(chat_id) not in off


def _reply_kb_shown(chat_id: int) -> bool:
    """Показывали ли уже клавиатуру этому чату.

    Одно сообщение не может нести сразу inline- и reply-разметку, поэтому на
    /start клавиатуру приходится слать отдельным сообщением. Делать это каждый
    раз — засорять чат: Telegram и так держит её в чате сам. Отмечаем показ и
    больше не повторяем.
    """
    try:
        seen = {str(x) for x in (load_json(REPLY_KB_STATE_FILE) or {}).get("shown", [])}
    except Exception:
        seen = set()
    return str(chat_id) in seen


def _reply_kb_mark_shown(chat_id: int) -> None:
    try:
        data = load_json(REPLY_KB_STATE_FILE) or {}
    except Exception:
        data = {}
    seen = {str(x) for x in data.get("shown", [])}
    seen.add(str(chat_id))
    data["shown"] = sorted(seen)
    try:
        save_json(REPLY_KB_STATE_FILE, data)
    except Exception:
        pass


def _reply_kb_set(chat_id: int, enabled: bool) -> None:
    try:
        off = {str(x) for x in (load_json(REPLY_KB_STATE_FILE) or {}).get("off", [])}
    except Exception:
        off = set()
    off.discard(str(chat_id)) if enabled else off.add(str(chat_id))
    try:
        data = load_json(REPLY_KB_STATE_FILE) or {}
    except Exception:
        data = {}
    data["off"] = sorted(off)          # "shown" не трогаем: он про показ, не про выбор
    try:
        save_json(REPLY_KB_STATE_FILE, data)
    except Exception:
        pass


def get_reply_keyboard(user_id: Optional[int] = None):
    """resize_keyboard — чтобы не занимала пол-экрана на телефоне;
    is_persistent — чтобы Telegram не сворачивал её после каждого сообщения."""
    labels = [_t(user_id, k) for k, _ in REPLY_KB_BUTTONS]
    rows = [labels[i:i + 2] for i in range(0, len(labels), 2)]
    return ReplyKeyboardMarkup(
        [[KeyboardButton(x) for x in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
    )


def _reply_kb_action(text: str) -> Optional[str]:
    """Подпись → действие. Ищем во ВСЕХ языках: клавиатура остаётся в чате со
    старыми подписями даже после смены языка, и прежние кнопки обязаны
    продолжать работать, а не превращаться в мёртвый текст."""
    probe = (text or "").strip()
    if not probe:
        return None
    for key, action in REPLY_KB_BUTTONS:
        for lang in SUPPORTED_LANGS:
            if _t_in(lang, key) == probe:
                return action
    return None


async def _reply_kb_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             action: str) -> None:
    """Выполнить действие reply-кнопки. Нажатие кнопки — однозначное намерение,
    поэтому сначала снимаем ожидание ввода: иначе подпись кнопки улетела бы
    в waiter как ответ (напр. вместо ключа WARP+)."""
    user_id = update.effective_user.id
    _clear_text_waiters(context, user_id)
    if action == "status":
        await cmd_status(update, context)
    elif action == "logs":
        await cmd_logs(update, context)
    elif action == "help":
        await cmd_help(update, context)
    else:  # menu
        await update.message.reply_text(
            _t(user_id, "rkb_menu_title"),
            reply_markup=get_main_menu(user_id),
            parse_mode="HTML",
        )


async def _drop_foreign_keyboard(update: Update) -> None:
    """Убрать «постоянную клавиатуру», оставшуюся от прежнего хозяина токена.

    Reply-клавиатура живёт не у бота, а В САМОМ ЧАТЕ и держится там, пока кто-то
    явно не пришлёт ReplyKeyboardRemove. goTelegram использует только inline-
    кнопки, поэтому чужую клавиатуру не заменял никогда: у пользователя, чей
    токен раньше использовал другой проект, под полем ввода висели его кнопки
    (VLESS, AWG/WG, Монитор…), и нажатия ожидаемо ничего не делали.

    Чистим один раз на чат. Удалять это сообщение НЕЛЬЗЯ: снятие клавиатуры
    привязано к самому сообщению, и вместе с его удалением Telegram возвращает
    прежнюю клавиатуру обратно (проверено — первый заход именно так и провалился).
    Поэтому оставляем короткую строку-пояснение.
    """
    chat_id = update.effective_chat.id
    try:
        done = set((load_json(KEYBOARD_CLEANED_FILE) or {}).get("chats", []))
    except Exception:
        done = set()
    if str(chat_id) in {str(x) for x in done}:
        return
    try:
        # Bot API не умеет проверить, была ли клавиатура, поэтому текст условный —
        # утверждение «убрал клавиатуру прежнего бота» на свежем токене было враньём
        await update.get_bot().send_message(
            chat_id=chat_id,
            text=_t(
                _uid(update),
                "kbd_cleanup",
                default="\U0001f9f9 Если под полем ввода оставалась клавиатура от прежнего бота на этом токене — убрал её.",
            ),
            # Если наша клавиатура включена, чужую не просто убираем, а
            # ЗАМЕНЯЕМ своей: два отдельных сообщения подряд («убрал» и «вот
            # ваша») выглядели бы мусором, а Remove после показа снял бы её же.
            reply_markup=(get_reply_keyboard(_uid(update))
                          if _reply_kb_enabled(chat_id) else ReplyKeyboardRemove()),
        )
    except Exception as exc:
        logger.warning("drop foreign keyboard: %s", exc)
        return
    try:
        done.add(str(chat_id))
        save_json(KEYBOARD_CLEANED_FILE, {"chats": sorted(done)})
    except Exception:
        pass


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command - show main menu, promo once per day.

    Если ALLOWED_IDS пуст — режим авто-регистрации первого админа.
    """
    user = update.effective_user
    user_id = user.id
    _clear_text_waiters(context, user_id)

    # ── Режим ожидания первого админа ──
    if _WAITING_FOR_ADMIN:
        if not admin_claim_open():
            logger.warning(
                "Admin claim window closed; ignoring /start from user %s", user_id
            )
            await update.message.reply_text(
                _tf(user_id, "admin_claim_expired", ADMIN_CLAIM_WINDOW_SECONDS // 60),
                parse_mode="HTML",
            )
            return
        logger.warning(
            "Admin claim window open (%ss left), offering setup to user %s",
            admin_claim_seconds_left(), user_id,
        )
        name = user.full_name or user.username or str(user_id)
        title = _tf(user_id, "waiting_admin_title", html.escape(name))
        body = _tf(user_id, "waiting_admin_body", user_id)
        note = _tf(user_id, "admin_claim_window", max(1, admin_claim_seconds_left() // 60))
        text = f"<b>{title}</b>\n\n{body}\n\n{note}"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(_t(user_id, "btn_yes"), callback_data=f"admin_confirm_{user_id}"),
                InlineKeyboardButton(_t(user_id, "btn_no"), callback_data="admin_cancel"),
            ]
        ])
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    # ── Проверка доступа ──
    if not is_user_allowed(user_id):
        await update.message.reply_text(
            _tf(user_id, "access_denied", user_id),
            parse_mode="HTML",
        )
        return

    # чистка клавиатуры — только СВОИМ: раньше шла до авторизации, и посторонний
    # получал служебное сообщение, а его chat_id оседал в .keyboard_cleaned.json
    await _drop_foreign_keyboard(update)

    welcome = (
        f"<b>{_tf(user_id, 'welcome_title', GOTELEGRAM_VERSION)}</b>\n\n"
        f"{_t(user_id, 'welcome_subtitle')}\n"
        f"{_t(user_id, 'welcome_powered')}\n\n"
        f"{_t(user_id, 'welcome_prompt')}"
    )
    if _reply_kb_enabled(update.effective_chat.id) and not _reply_kb_shown(update.effective_chat.id):
        # Отдельным сообщением и ровно один раз — см. _reply_kb_shown.
        try:
            await update.message.reply_text(
                _t(user_id, "rkb_on"), reply_markup=get_reply_keyboard(user_id)
            )
            _reply_kb_mark_shown(update.effective_chat.id)
        except Exception as exc:
            logger.warning("reply keyboard: %s", exc)
    await update.message.reply_text(
        welcome, reply_markup=get_main_menu(user_id), parse_mode="HTML"
    )

    # Промо раз в сутки — сообщение само удаляется через 30 секунд
    if should_show_promo_bot():
        mark_promo_shown_bot()
        promo_msg = await update.message.reply_text(
            get_promo_text(), parse_mode="HTML", disable_web_page_preview=True
        )
        spawn_task(_delete_message_after(promo_msg, 30))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Help command - show available commands."""
    if not await require_auth(update, context):
        return
    user_id = _uid(update)
    _clear_text_waiters(context, user_id)
    help_text = (
        f"<b>{_t(user_id, 'help_title')}</b>\n\n"
        f"{_t(user_id, 'help_lines')}"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show language picker."""
    if not await require_auth(update, context):
        return
    user_id = _uid(update)
    _clear_text_waiters(context, user_id)
    current = get_user_lang(user_id)
    title = _t(user_id, "lang_title")
    curr_line = _tf(user_id, "lang_current", get_language_name(current))
    prompt = _t(user_id, "lang_choose")
    text = f"<b>{title}</b>\n\n{curr_line}\n\n{prompt}"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_set_en"),
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_set_ru"),
        ],
        [InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_main")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick status check."""
    if not await require_auth(update, context):
        return
    user_id = _uid(update)
    _clear_text_waiters(context, user_id)
    await update.message.reply_text(_t(user_id, "status_checking"), parse_mode="HTML")
    status_text = await get_status_text(user_id)
    await update.message.reply_text(status_text, parse_mode="HTML")


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent logs."""
    if not await require_auth(update, context):
        return
    user_id = _uid(update)
    _clear_text_waiters(context, user_id)

    code, stdout, stderr = await sh(
        "journalctl", "-u", TELEMT_SERVICE, "-n", "20", "--no-pager"
    )
    if code == 0:
        log_text = stdout[-1500:] if len(stdout) > 1500 else stdout
        await update.message.reply_text(
            f"<pre>{html.escape(log_text)}</pre>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(_t(user_id, "logs_failed"))


# ============================================================================
# STATUS
# ============================================================================


async def get_status_text(user_id: Optional[int] = None) -> str:
    """Generate status report (localized)."""
    lines = [f"<b>{_t(user_id, 'status_title')}</b>\n"]

    # Service status
    is_running = await check_service_status(TELEMT_SERVICE)
    running = _t(user_id, "status_running") if is_running else _t(user_id, "status_stopped")
    lines.append(f"<b>{_t(user_id, 'status_service')}:</b> {running}")

    # Telemt version
    version = await get_telemt_version()
    lines.append(f"<b>{_t(user_id, 'status_telemt')}:</b> v{version}")

    # Config status
    config = load_json(GOTELEGRAM_CONFIG)
    if config:
        lines.append(f"<b>{_t(user_id, 'status_mode')}:</b> {html.escape(str(config.get('mode', 'unknown')))}")
        # install.sh/save_gotelegram_config uses "template_id" (not "template")
        tpl = config.get("template_id") or config.get("template")
        if tpl:
            lines.append(f"<b>{_t(user_id, 'status_template')}:</b> {html.escape(template_display_name(str(tpl)))}")
        if config.get("domain"):
            lines.append(f"<b>{_t(user_id, 'status_domain')}:</b> {html.escape(str(config['domain']))}")
        if config.get("port"):
            lines.append(f"<b>{_t(user_id, 'status_port')}:</b> {html.escape(str(config['port']))}")

    # Telemt config (v3: [server] port = ..., [censorship] tls_domain = ...)
    telemt_cfg = load_toml(TELEMT_CONFIG)
    if telemt_cfg:
        server_cfg = telemt_cfg.get("server", {})
        if "port" in server_cfg:
            lines.append(f"<b>{_t(user_id, 'status_listen_port')}:</b> {server_cfg['port']}")
        censor_cfg = telemt_cfg.get("censorship", {})
        if "tls_domain" in censor_cfg:
            lines.append(f"<b>{_t(user_id, 'status_tls_domain')}:</b> {html.escape(str(censor_cfg['tls_domain']))}")

    # Backups
    backup_count = 0
    try:
        backup_count = count_backups()
    except Exception:
        pass
    lines.append(f"<b>{_t(user_id, 'status_backups')}:</b> {backup_count}")

    # Old container check
    old_container = await check_old_container()
    if old_container:
        lines.append(f"\n⚠️ <b>Found old container:</b> {html.escape(old_container)}")
        lines.append("Run 'Install' to migrate")

    return "\n".join(lines)


async def get_traffic_stats() -> str:
    """Get formatted traffic statistics."""
    await sh(
        "bash",
        "-lc",
        "source /opt/gotelegram/lib/common.sh; "
        "source /opt/gotelegram/lib/stats.sh; "
        "stats_init >/dev/null 2>&1 || true; stats_collect >/dev/null 2>&1 || true",
        timeout=15,
    )

    # Read current snapshot
    current_file = "/run/gotelegram/stats_current.json"
    history_file = "/opt/gotelegram/stats_history.csv"

    try:
        with open(current_file, "r") as f:
            current = json.load(f)
    except Exception:
        return "📊 <b>Статистика</b>\n\n<i>Данные недоступны. Убедитесь что модуль статистики включён.</i>"

    # Read history
    history = []
    try:
        with open(history_file, "r") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 3:
                    if not row[0].isdigit():
                        continue
                    history.append({
                        "ts": int(row[0]),
                        "proxy": int(row[1]),
                        "site": int(row[2]),
                    })
    except Exception:
        pass

    now = int(time.time())

    def format_bytes(b):
        if b < 1024:
            return f"{b} B"
        if b < 1048576:
            return f"{b/1024:.1f} KB"
        if b < 1073741824:
            return f"{b/1048576:.1f} MB"
        return f"{b/1073741824:.1f} GB"

    def format_rate(bps):
        if bps < 1024:
            return f"{bps:.0f} B/s"
        if bps < 1048576:
            return f"{bps/1024:.1f} KB/s"
        return f"{bps/1048576:.1f} MB/s"

    def calc_for_period(secs, key):
        target_ts = now - secs
        # Find closest snapshot to target_ts
        closest = None
        for h in history:
            if h["ts"] <= target_ts:
                if closest is None or h["ts"] > closest["ts"]:
                    closest = h
        if closest is None:
            return "—", "—"

        current_val = current.get(f"{key}_bytes", 0)
        # Не «последнее минус первое»: счётчики iptables обнуляются при
        # перезагрузке и при смене порта учёта (WEB переводит сайт на 8444), и
        # разница краёв уходила в минус — окно показывало 0. Суммируем приросты
        # между снимками, а после сброса берём накопленное с нуля — как SSH.
        diff = 0
        prev = closest[key]
        for h in history:
            if h["ts"] <= closest["ts"]:
                continue
            cur = h[key]
            diff += cur if cur < prev else cur - prev
            prev = cur
        diff += current_val if current_val < prev else current_val - prev
        elapsed = now - closest["ts"]
        if elapsed <= 0:
            elapsed = 1
        rate = diff / elapsed
        return format_bytes(diff), format_rate(rate)

    periods = [
        ("1 мин", 60),
        ("5 мин", 300),
        ("60 мин", 3600),
        ("1 день", 86400),
        ("7 дней", 604800),
        ("30 дней", 2592000),
        ("365 дней", 31536000),
    ]

    lines = ["📊 <b>Статистика трафика</b>\n"]

    for label, key in [("Proxy (telemt)", "proxy"), ("Сайт (nginx)", "site")]:
        lines.append(f"\n<b>{label}:</b>")
        lines.append("<pre>")
        lines.append(f"{'Период':<10} │ {'Трафик':>10} │ {'Скорость':>10}")
        lines.append("─" * 36)
        for name, secs in periods:
            total, rate = calc_for_period(secs, key)
            lines.append(f"{name:<10} │ {total:>10} │ {rate:>10}")
        lines.append("</pre>")

    return "\n".join(lines)


async def cb_menu_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show traffic statistics."""
    query = update.callback_query
    await query.answer()

    stats_text = await get_traffic_stats()

    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data="menu_stats")],
        [InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="sec_state")],
    ]

    await safe_edit_message(
        query,
        stats_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def cb_menu_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Status callback — show detailed proxy/server status."""
    query = update.callback_query
    await query.answer()

    if not await require_auth(update, context):
        return

    text = await get_status_text(_uid(update))
    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data="menu_status")],
        [InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="sec_state")],
    ]
    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


# ============================================================================
# INSTALL
# ============================================================================


def get_install_mode_menu(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """Install mode selection menu."""
    buttons = [
        [InlineKeyboardButton(_t(user_id, "install_mode_lite"), callback_data="install_mode_lite")],
        [InlineKeyboardButton(_t(user_id, "install_mode_pro"), callback_data="install_mode_pro")],
        [InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(buttons)


async def cb_menu_install(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Install menu callback."""
    query = update.callback_query
    await query.answer()

    # Check for old container
    old_container = await check_old_container()
    if old_container:
        text = (
            f"⚠️ <b>Migration from v1 detected</b>\n\n"
            f"Found Docker container: {html.escape(old_container)}\n\n"
            f"Would you like to:\n"
            f"1. Migrate from v1 (recommended)\n"
            f"2. Fresh install (will remove old container)\n\n"
            f"<i>Select below or choose install mode</i>"
        )
        buttons = [
            [InlineKeyboardButton("🔄 Migrate from v1", callback_data="install_migrate")],
            [InlineKeyboardButton("✨ Fresh Install", callback_data="install_mode_lite")],
            [InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_main")],
        ]
        keyboard = InlineKeyboardMarkup(buttons)
    else:
        text = f"<b>{_t(_uid(update), 'install_title')}</b>\n\n{_t(_uid(update), 'install_pick_mode')}"
        keyboard = get_install_mode_menu(_uid(update))

    await safe_edit_message(query,
        text, reply_markup=keyboard, parse_mode="HTML"
    )


async def cb_install_mode_lite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lite mode domain selection."""
    query = update.callback_query
    await query.answer()

    # Show domains with pagination (4 per row, 2 rows)
    buttons = []
    for i in range(0, len(LITE_DOMAINS), 2):
        row = []
        for j in range(2):
            if i + j < len(LITE_DOMAINS):
                domain = LITE_DOMAINS[i + j]
                row.append(
                    InlineKeyboardButton(
                        domain, callback_data=f"lite_dom_{i+j}"
                    )
                )
        buttons.append(row)

    buttons.append([InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_install")])

    text = "Select a domain for Lite mode:"
    keyboard = InlineKeyboardMarkup(buttons)
    await safe_edit_message(query,text, reply_markup=keyboard)


async def cb_lite_domain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lite domain selection callback — real implementation (v2.4.2+).

    Branches on current mode:
      * lite mode (active): invoke `install.sh --action=change-lite-domain`
        which regenerates the telemt TOML with a new fake-TLS mask domain and
        restarts the service. Preserves secret/port.
      * any other mode: route to CLI. Fresh Lite install is interactive.
    """
    query = update.callback_query
    data = query.data
    try:
        domain_idx = int(data.split("_")[-1])
        domain = LITE_DOMAINS[domain_idx]
    except (ValueError, IndexError):
        await query.answer("Invalid domain selection")
        return

    # Defense-in-depth: LITE_DOMAINS is trusted, but validate the shape anyway
    # in case someone extends the list with garbage later.
    if not _DOMAIN_RE.match(domain):
        logger.warning(f"cb_lite_domain: rejecting malformed domain {domain!r}")
        await query.answer("Invalid domain")
        return

    await query.answer()

    config = load_json(GOTELEGRAM_CONFIG) or {}
    current_mode = config.get("mode", "")

    if current_mode != "lite":
        text = (
            "<b>⚠️ Установка Lite из бота пока не поддерживается</b>\n\n"
            f"Выбранный домен: <code>{html.escape(domain)}</code>\n\n"
            "Чтобы установить Lite, запустите на сервере:\n"
            "<code>gotelegram</code> → <b>1) Прокси → 1) Установить/Обновить → Lite</b>\n\n"
            "Существующая конфигурация <b>не была изменена</b>."
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_main")]]
        )
        await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")
        return

    # Lite active — switch fake-TLS mask domain in place
    if _BOT_ACTION_LOCK.locked():
        await safe_edit_message(
            query,
            "<b>⏳ Другая операция уже выполняется</b>\n\n"
            "Дождитесь завершения предыдущей смены шаблона/домена и повторите.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_main")]]
            ),
            parse_mode="HTML",
        )
        return

    progress_text = (
        "<b>⏳ Меняю маскировочный домен...</b>\n\n"
        f"Новый домен: <code>{html.escape(domain)}</code>\n\n"
        "Перегенерирую конфиг telemt и перезапускаю сервис."
    )
    await safe_edit_message(query, progress_text, parse_mode="HTML")

    async with _BOT_ACTION_LOCK:
        # install.sh внутри берёт flock -w 30 и может доставить зависимости /
        # прогнать миграцию. Таймаут 30 с обрывал запись /etc/telemt/config.toml
        # на полуслове и telemt после этого не стартовал.
        result = await run_bot_action("change-lite-domain", timeout=180, domain=domain)

    if result.get("status") == "success":
        text = (
            "<b>✅ Маскировочный домен обновлён</b>\n\n"
            f"Новый домен: <code>{html.escape(domain)}</code>\n\n"
            "telemt перезапущен. <b>Важно:</b> старые ссылки подключения больше "
            "не будут работать — нужно заново раздать новые."
        )
    else:
        err_msg = result.get("message", "unknown error")
        err_code = result.get("code", "")
        text = (
            "<b>❌ Не удалось сменить домен</b>\n\n"
            f"Домен: <code>{html.escape(domain)}</code>\n"
            f"Причина: <code>{html.escape(err_msg)}</code>"
            + (f" (<code>{html.escape(err_code)}</code>)" if err_code else "")
            + "\n\n"
            "Существующая конфигурация <b>не была изменена</b>."
        )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_main")]]
    )
    await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")


async def cb_install_mode_pro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pro mode - show template categories."""
    query = update.callback_query
    await query.answer()

    catalog = load_json(TEMPLATES_CATALOG)
    if not catalog or "categories" not in catalog:
        await safe_edit_message(query,
            "❌ Template catalog not found",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_install")]]
            ),
        )
        return

    user_id = _uid(update)
    buttons = []
    # First item: custom git template (matches CLI behaviour)
    buttons.append([InlineKeyboardButton(
        _t(user_id, "cg_title"), callback_data="pro_custom_git"
    )])
    for cat in catalog.get("categories", []):
        buttons.append(
            [
                InlineKeyboardButton(
                    f"📁 {cat['name']}", callback_data=f"pro_cat_{cat['id']}"
                )
            ]
        )
    buttons.append([InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_install")])

    text = "Pro Mode — Select Template Category:"
    keyboard = InlineKeyboardMarkup(buttons)
    await safe_edit_message(query, text, reply_markup=keyboard)


# Telegram отклоняет слишком большие inline-клавиатуры — режем каталог на
# страницы (в категории "other" 1015 шаблонов).
PRO_TEMPLATES_PER_PAGE = 24

# ── Custom git template input flow ──────────────────────────────────────────
_CUSTOM_GIT_WAITERS: Dict[int, bool] = {}

# WARP+ ключ: 4-4-4-4 или 8-8-8 (оба формата принимает wgcf).
_WARP_KEY_RE = re.compile(r"^(?:[A-Za-z0-9]{4}(?:-[A-Za-z0-9]{4}){3}|[A-Za-z0-9]{8}(?:-[A-Za-z0-9]{8}){2})$")


# Сколько живёт ожидание ввода, если человек не нажимает кнопок и не пишет.
_WAITER_TTL = 600  # 10 минут


def _arm_text_waiter(context) -> None:
    """Отметить момент, когда ожидание ввода взведено."""
    context.user_data["_waiter_since"] = time.monotonic()


def _text_waiter_expired(context) -> bool:
    since = context.user_data.get("_waiter_since")
    if since is None:
        return False
    return (time.monotonic() - since) > _WAITER_TTL


def _clear_text_waiters(context, user_id: int) -> None:
    """Любое нажатие кнопки отменяет ожидание ввода текста, чтобы следующий
    текст не улетел не в тот обработчик (напр. ключ WARP+ вместо имени юзера)."""
    for _k in ("awaiting_warp_key", "awaiting_user_name", "awaiting_user_ip_limit",
               "awaiting_web_host", "_waiter_since"):
        context.user_data.pop(_k, None)
    _CUSTOM_GIT_WAITERS.pop(user_id, None)
_CUSTOM_GIT_URL_RE = re.compile(r'^https://[A-Za-z0-9._~:/\-?#\[\]@!$&\'()*+,;=%]+(@[A-Za-z0-9._\-/]+)?$')
_CUSTOM_GIT_MAX_MB = 100
_CUSTOM_GIT_CLONE_TIMEOUT = 90


def _validate_custom_git_url(url: str) -> bool:
    if not url or len(url) > 512:
        return False
    # Block shell metacharacters explicitly
    for bad in (" ", "`", "$", "(", ")", "<", ">", "|", "\\", "\t", "\n", "\r", ";", "&", "'", '"'):
        if bad in url:
            return False
    if not url.lower().startswith("https://"):
        return False
    # Reject embedded userinfo (https://user:pass@host/...) to prevent credential leakage.
    # We look at the netloc — anything between https:// and the first '/'.
    rest = url[len("https://"):]
    netloc_end = rest.find("/")
    netloc = rest if netloc_end == -1 else rest[:netloc_end]
    if "@" in netloc:
        return False
    # Hostname sanity: no empty host, no whitespace already blocked above
    if not netloc or netloc.startswith(":") or netloc.endswith(":"):
        return False
    return True


async def _download_custom_git_template(url_with_branch: str) -> Tuple[bool, str, str]:
    """Clone a custom git repo and stage its static site under WEBSITE_ROOT.

    Returns (ok, tpl_id, message_key_or_path).
    """
    # Parse @branch suffix (only when the branch appears on the last path segment)
    branch = None
    url = url_with_branch
    if "@" in url_with_branch.rsplit("/", 1)[-1]:
        base, _, maybe_branch = url_with_branch.rpartition("@")
        if (
            base.lower().startswith("https://")
            and maybe_branch  # reject empty branch after `@`
            and "/" not in maybe_branch
            and re.match(r'^[A-Za-z0-9._/\-]+$', maybe_branch)
        ):
            url = base
            branch = maybe_branch
        elif not maybe_branch and base.lower().startswith("https://"):
            # Trailing `@` with no branch — drop it so git doesn't treat it as userinfo
            url = base

    tpl_id = "custom_" + hashlib.md5(url_with_branch.encode("utf-8")).hexdigest()[:10]
    target_dir = f"/opt/gotelegram/custom_templates/{tpl_id}"

    # Clean previous copy
    if os.path.isdir(target_dir):
        shutil.rmtree(target_dir, ignore_errors=True)

    tmp_dir = f"/tmp/{tpl_id}_clone"
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, tmp_dir]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=_CUSTOM_GIT_CLONE_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return False, tpl_id, "cg_timeout"
        if proc.returncode != 0:
            return False, tpl_id, "cg_invalid"
    except Exception as e:
        logger.warning("custom git clone failed: %s", e)
        return False, tpl_id, "cg_invalid"

    # Remove .git to enforce size guard and avoid leaking repo history
    git_dir = os.path.join(tmp_dir, ".git")
    if os.path.isdir(git_dir):
        shutil.rmtree(git_dir, ignore_errors=True)

    # Size guard
    total = 0
    for root, _dirs, files in os.walk(tmp_dir):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    if total > _CUSTOM_GIT_MAX_MB * 1024 * 1024:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, tpl_id, "cg_too_big"

    # Locate index.html in priority order
    found_root = None
    for sub in ("", "dist", "public", "build", "_site", "site", "docs", "out", "www"):
        cand = os.path.join(tmp_dir, sub) if sub else tmp_dir
        if os.path.isfile(os.path.join(cand, "index.html")):
            found_root = cand
            break
    if not found_root:
        # Fallback: search maxdepth 4
        for root, _dirs, files in os.walk(tmp_dir):
            depth = root[len(tmp_dir):].count(os.sep)
            if depth > 4:
                continue
            if "index.html" in files:
                found_root = root
                break
    if not found_root:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, tpl_id, "cg_no_index"

    # Stage final template dir
    os.makedirs(os.path.dirname(target_dir), exist_ok=True)
    shutil.copytree(found_root, target_dir)
    try:
        with open(os.path.join(target_dir, ".custom_git_source"), "w", encoding="utf-8") as f:
            f.write(url_with_branch + "\n")
    except OSError:
        pass
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return True, tpl_id, target_dir


async def cb_pro_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать шаблоны категории постранично.

    Раньше строилась кнопка на КАЖДЫЙ шаблон: в категории "other" их 1015 →
    Telegram отклонял разметку (BadRequest), ошибка уходила в error_handler,
    и кнопка категории выглядела мёртвой (половина каталога недоступна).
    """
    query = update.callback_query
    user_id = _uid(update)
    raw = query.data.removeprefix("pro_cat_")
    # Формат: pro_cat_{id} (старые сообщения) или pro_cat_{id}~{page}.
    # "~" не встречается в id категорий, поэтому разбор однозначен.
    cat_id, sep, page_raw = raw.rpartition("~")
    if not sep:
        cat_id, page = raw, 0
    else:
        try:
            page = max(0, int(page_raw))
        except ValueError:
            page = 0

    await query.answer()

    catalog = load_json(TEMPLATES_CATALOG)
    if not catalog:
        await safe_edit_message(query, "❌ Template catalog not found")
        return

    # Find category and templates
    category = None
    templates = []
    for cat in catalog.get("categories", []):
        if cat["id"] == cat_id:
            category = cat
            templates = cat.get("templates", [])
            break

    if not category:
        await safe_edit_message(query, "❌ Category not found")
        return

    total = len(templates)
    pages = max(1, (total + PRO_TEMPLATES_PER_PAGE - 1) // PRO_TEMPLATES_PER_PAGE)
    page = min(page, pages - 1)
    start = page * PRO_TEMPLATES_PER_PAGE
    chunk = templates[start:start + PRO_TEMPLATES_PER_PAGE]

    buttons = []
    for tpl in chunk:
        key = pro_template_key_for_id(context, tpl["id"])
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🎨 {tpl['name']}"[:60], callback_data=f"pro_tpl_{key}"
                )
            ]
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("‹", callback_data=f"pro_cat_{cat_id}~{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("›", callback_data=f"pro_cat_{cat_id}~{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="install_mode_pro")])

    text = (
        f"{_tf(user_id, 'pro_pick_template', html.escape(category['name']))}\n"
        f"{_tf(user_id, 'pro_page_of', page + 1, pages, total)}"
    )
    keyboard = InlineKeyboardMarkup(buttons)
    await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")


async def _warp_read_status() -> dict:
    """Читает JSON-статус WARP из install.sh --action=warp-status."""
    code, stdout, _ = await sh("bash", INSTALL_SH, "--action=warp-status", "--json", timeout=30)
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return {}


async def _warp_read_probe() -> dict:
    """Пинг/потери к Telegram через WARP (install.sh --action=warp-probe)."""
    code, stdout, _ = await sh("bash", INSTALL_SH, "--action=warp-probe", "--json", timeout=30)
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return {}


async def _render_warp_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, probe: dict = None) -> None:
    """Рисует экран WARP-egress (без query.answer — зовётся и после действий)."""
    query = update.callback_query
    uid = _uid(update)
    st = await _warp_read_status()
    on = st.get("warp_egress") == "on"
    tier = st.get("tier") or "?"
    egress = st.get("egress") or "?"
    routing = st.get("routing") == "on"
    colo = st.get("colo") or "?"
    hs = st.get("handshake", -1)
    try:
        hs = int(hs)
    except Exception:
        hs = -1
    hs_str = _tf(uid, "warp_hs_ago", hs) if hs >= 0 else _t(uid, "warp_hs_none")
    state = _t(uid, "warp_on") if on else _t(uid, "warp_off")
    text = (
        f"<b>{_t(uid, 'warp_title')}</b>\n\n"
        f"{_t(uid, 'warp_desc')}\n\n"
        f"{_t(uid, 'warp_state')}: <b>{state}</b>\n"
        f"{_t(uid, 'warp_tier')}: {html.escape(str(tier))}\n"
        f"{_t(uid, 'warp_egress_line')}: <code>{html.escape(str(egress))}</code> ({html.escape(str(colo))})\n"
        f"{_t(uid, 'warp_hs_line')}: {html.escape(str(hs_str))}\n"
        f"{_t(uid, 'warp_route_line')}: {_t(uid, 'warp_yes') if routing else _t(uid, 'warp_no')}"
    )
    if probe is not None:
        if probe.get("reachable"):
            text += "\n\n" + _tf(uid, "warp_ping_line", probe.get("ping_avg", "?"), probe.get("loss", "?"))
        else:
            text += "\n\n" + _t(uid, "warp_unreachable")
    if on:
        rows = [
            [InlineKeyboardButton(_t(uid, "warp_btn_probe"), callback_data="warp_probe")],
            [InlineKeyboardButton(_t(uid, "warp_btn_off"), callback_data="warp_off")],
            [InlineKeyboardButton(_t(uid, "warp_btn_setkey"), callback_data="warp_key")],
            [InlineKeyboardButton(_t(uid, "warp_btn_refresh"), callback_data="menu_warp")],
        ]
    else:
        rows = [
            [InlineKeyboardButton(_t(uid, "warp_btn_on_free"), callback_data="warp_on_free")],
            [InlineKeyboardButton(_t(uid, "warp_btn_on_key"), callback_data="warp_key")],
            [InlineKeyboardButton(_t(uid, "warp_btn_refresh"), callback_data="menu_warp")],
        ]
    rows.append([InlineKeyboardButton(_t(uid, "btn_back"), callback_data="sec_settings")])
    await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")


# ── WEB-прокси (Telegram Desktop 7.1+) ─────────────────────────────────────
WEB_CARRIERS = ["https", "websocket", "https-lanes", "websocket-lanes"]
# Отдельное имя WEB (www.домен): полную проверку (DNS, сертификат) делает
# install.sh --action=web-host, здесь отсекаем только очевидный мусор, чтобы
# не гонять certbot ради опечатки с пробелом.
# Последняя метка — не одни цифры: «1.2.3.45» certbot'у не нужен.
_WEB_HOST_RE = re.compile(r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+(?![0-9]+$)[a-z0-9-]{2,63}$')
# Худший путь web-host: два certbot по ≤100 с, перестройка WEB с ожиданием
# старта ядра до 90 с и откат на прежнее имя. Убить install.sh посреди отката
# хуже, чем подождать.
_WEB_HOST_TIMEOUT = 420
# Имя бесполезно без Pro-домена и его сертификата, без ядра с WEB и без
# публичного IPv4: web_set_host откажет или выпустит сертификат впустую.
_WEB_HOST_BLOCKERS = ("need_pro", "need_cert", "need_core", "need_addr")


def _web_host_is_sub(host: str, domain: str) -> bool:
    """Имя WEB — поддомен основного домена (www.домен). Домен не известен — не судим."""
    domain = (domain or "").strip().lower().rstrip(".")
    return not domain or host.endswith("." + domain)


def _web_host_normalize(text: str) -> str:
    """Имя WEB из ввода: нижний регистр; пусто — ввод не похож на имя домена."""
    host = (text or "").strip().lower().rstrip(".")
    return host if _WEB_HOST_RE.match(host) else ""


def _web_error_text(user_id: Optional[int], r: Dict) -> str:
    """Текст отказа install.sh: у блокеров отдельного имени — свои строки бота.

    Сообщение install.sh про need_web_host отсылает к пункту SSH-меню, а в боте
    имя задаётся кнопкой — поэтому код переводим сами, остальное как есть.
    """
    code = str(r.get("code") or "")
    if code == "need_web_host":
        return _t(user_id, "web_block_need_web_host")
    if code == "need_no_shared443":
        return _t(user_id, "web_block_need_no_shared443")
    if code == "web_host_not_sub":
        domain = str((load_json(GOTELEGRAM_CONFIG) or {}).get("domain") or "")
        return _t(user_id, "web_host_not_sub").replace(
            "{d}", html.escape(domain or _t(user_id, "web_host_example_domain")))
    if code == "transport":
        # таймаут или ответ без JSON: install.sh мог не закончить — итог не известен
        return _t(user_id, "web_result_unknown")
    return html.escape(str(r.get("message") or _t(user_id, "web_failed")))


def _web_block_text(user_id: Optional[int], blocker: str, min_core: str) -> str:
    """Почему WEB нельзя включить — по коду блокера из web-status."""
    texts = {
        "need_pro": _t(user_id, "web_block_need_pro"),
        "need_core": _t(user_id, "web_block_need_core").replace("{v}", html.escape(min_core)),
        "need_cert": _t(user_id, "web_block_need_cert"),
        "need_addr": _t(user_id, "web_block_need_addr"),
        "need_web_host": _t(user_id, "web_block_need_web_host"),
        "need_no_shared443": _t(user_id, "web_block_need_no_shared443"),
    }
    return texts.get(blocker) or html.escape(blocker)


async def _render_web_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, note: str = "") -> None:
    query = update.callback_query
    user_id = _uid(update)
    r = await run_bot_action("web-status", timeout=60)
    lines = [f"<b>{_t(user_id, 'web_title')}</b>", "", _t(user_id, "web_desc"), ""]
    # Ответ без status=ok (занят общий замок, установщик не ответил) — это не
    # «выключен»: кнопка «Включить» на включённом WEB перезапустила бы telemt.
    if r.get("status") != "ok":
        lines.append("⚠️ " + html.escape(str(r.get("message") or _t(user_id, "web_status_failed"))))
        if note:
            lines += ["", note]
        rows_err = [[InlineKeyboardButton(_t(user_id, "btn_refresh"), callback_data="menu_web")],
                    _back_row(user_id, "sec_settings")]
        await safe_edit_message(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(rows_err), parse_mode="HTML")
        return
    on = bool(r.get("enabled"))
    blocker = str(r.get("blocker") or "")
    web_host = str(r.get("web_host") or "")
    # экран ввода имени предупредит, что старые WEB-ссылки перестанут работать
    context.user_data["web_legacy"] = on and r.get("legacy") is True
    lines.append(f"{_t(user_id, 'web_state')}: <b>{_t(user_id, 'web_on') if on else _t(user_id, 'web_off')}</b>")
    if web_host:
        lines.append(f"{_t(user_id, 'web_host_label')}: <code>{html.escape(web_host)}</code>")
    if on:
        bridge = str(r.get("bridge") or "")
        mark = "✅" if bridge == "ok" else ("❌" if bridge == "fail" else "—")
        lines.append(f"carrier: <code>{html.escape(str(r.get('carrier') or ''))}</code> · bridge: {mark}")
        # WEB включён до 2.10.50 на основном домене: работает, но душит обычные
        # ee-ссылки за тем же Wi-Fi — владелец должен задать имя и раздать ссылки
        if r.get("legacy") is True:
            lines += ["", "⚠️ " + _t(user_id, "web_legacy_warn")]
        if r.get("link"):
            lines += ["", f"<code>{html.escape(str(r.get('link')))}</code>"]
    elif blocker:
        lines.append(_web_block_text(user_id, blocker, str(r.get("min_core") or "")))
    # Имя WEB смотрит не на все адреса сервера: клиенты по WEB-ссылке на
    # пропущенные адреса не попадут. Нет поля (старый install.sh) — молчим.
    web_missing = _dns_ip_list(r.get("dns_web_missing"))
    if web_host and web_missing:
        lines += ["", "⚠️ " + _dns_missing_text(user_id, web_host, web_missing)]
    lines += ["", f"<i>{_t(user_id, 'web_link_hint')}</i>"]
    if note:
        lines += ["", note]

    rows: List[List[InlineKeyboardButton]] = []
    if on:
        rows.append([
            InlineKeyboardButton(_t(user_id, "web_btn_off"), callback_data="web_off"),
            InlineKeyboardButton(_t(user_id, "web_btn_carrier"), callback_data="web_carrier"),
        ])
    elif r.get("configured"):
        # Настроен, но не активен (ядро без WEB, нет адреса): выключить надо
        # дать — иначе владелец заперт в этом состоянии до SSH.
        rows.append([InlineKeyboardButton(_t(user_id, "web_btn_off"), callback_data="web_off")])
    elif not blocker:
        rows.append([InlineKeyboardButton(_t(user_id, "web_btn_on"), callback_data="web_on")])
    if blocker not in _WEB_HOST_BLOCKERS:
        rows.append([InlineKeyboardButton(_t(user_id, "web_btn_host"), callback_data="web_host")])
    rows.append([InlineKeyboardButton(_t(user_id, "btn_refresh"), callback_data="menu_web")])
    rows.append(_back_row(user_id, "sec_settings"))
    await safe_edit_message(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")


async def cb_menu_web(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _render_web_menu(update, context)


async def _ask_web_host(update: Update, context: ContextTypes.DEFAULT_TYPE, note: str = "") -> None:
    """Экран ввода отдельного имени WEB; ответ ловит handle_text_message."""
    query = update.callback_query
    user_id = _uid(update)
    context.user_data["awaiting_web_host"] = True
    _arm_text_waiter(context)
    domain = str((load_json(GOTELEGRAM_CONFIG) or {}).get("domain") or "")
    text = _t(user_id, "web_host_ask").replace("{d}", html.escape(domain or _t(user_id, "web_host_example_domain")))
    if context.user_data.get("web_legacy"):
        # WEB уже работает на основном домене: смена имени перестроит ядро
        text += "\n\n⚠️ " + _t(user_id, "web_host_legacy_warn")
    if note:
        text = note + "\n\n" + text
    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_web")]]),
        parse_mode="HTML",
    )


async def cb_web_host(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _ask_web_host(update, context)


async def _web_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, **params) -> None:
    """Общий путь on/off/carrier: один замок на действие, потом перерисовка."""
    query = update.callback_query
    user_id = _uid(update)
    await query.answer(_t(user_id, "web_applying"))
    if await _warp_busy(query):
        return
    async with _BOT_ACTION_LOCK:
        # включение перезапускает telemt и ждёт его готовности — до полутора минут
        r = await run_bot_action(action, timeout=300, **params)
    note = ""
    if r.get("status") != "ok":
        if action == "web-on" and r.get("code") == "need_web_host":
            # без имени WEB не включить — сразу предлагаем его ввести
            await _ask_web_host(update, context, note="❌ " + _web_error_text(user_id, r))
            return
        note = "❌ " + _web_error_text(user_id, r)
    elif action == "web-carrier" and params.get("carrier") != "https":
        # iPhone-клиент умеет только https — предупреждаем сразу при смене
        note = "⚠️ " + _t(user_id, "web_carrier_ios")
    await _render_web_menu(update, context, note=note)


async def set_web_host_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Ответ на «Отдельное имя WEB»: install.sh --action=web-host и итог."""
    user_id = update.effective_user.id
    host = _web_host_normalize(text)
    if not host:
        # ожидание НЕ снимаем — человек поправит имя и пришлёт снова
        await update.message.reply_text(_t(user_id, "web_host_badfmt"))
        return
    domain = str((load_json(GOTELEGRAM_CONFIG) or {}).get("domain") or "")
    if not _web_host_is_sub(host, domain):
        # не поддомен основного домена — install.sh всё равно откажет; ждём новое имя
        await update.message.reply_text(_web_error_text(user_id, {"code": "web_host_not_sub"}), parse_mode="HTML")
        return
    if _BOT_ACTION_LOCK.locked():
        await update.message.reply_text(_t(user_id, "warp_busy"))
        return
    context.user_data.pop("awaiting_web_host", None)
    # Между проверкой замка и входом в него нет await: второе сообщение с именем
    # не встанет в очередь за первым и не запустит certbot ещё раз.
    async with _BOT_ACTION_LOCK:
        await update.message.reply_text(_t(user_id, "web_host_applying"))
        # DNS + certbot --expand + (при включённом WEB) разводка 443
        r = await run_bot_action("web-host", timeout=_WEB_HOST_TIMEOUT, host=host)
    ok = r.get("status") == "ok"
    rows = [[InlineKeyboardButton(_t(user_id, "web_btn_menu"), callback_data="menu_web")]]
    if not ok:
        # DNS мог ещё не разойтись — повторить ввод одной кнопкой
        rows.insert(0, [InlineKeyboardButton(_t(user_id, "web_btn_host"), callback_data="web_host")])
    if ok:
        msg = "\u2705 " + html.escape(str(r.get("message") or host))
        # имя принято, но смотрит не на все адреса сервера — сказать сразу
        missing = _dns_ip_list(r.get("dns_missing"))
        if missing:
            msg += "\n\n\u26a0\ufe0f " + _dns_missing_text(user_id, host, missing)
    else:
        msg = "\u26a0\ufe0f " + _web_error_text(user_id, r)
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")


async def cb_web_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _web_toggle(update, context, "web-on")


async def cb_web_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _web_toggle(update, context, "web-off")


async def cb_web_carrier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Carrier по кругу: https → websocket → https-lanes → websocket-lanes."""
    st = await run_bot_action("web-status", timeout=60)
    cur = str(st.get("carrier") or "https")
    nxt = WEB_CARRIERS[(WEB_CARRIERS.index(cur) + 1) % len(WEB_CARRIERS)] if cur in WEB_CARRIERS else "https"
    await _web_toggle(update, context, "web-carrier", carrier=nxt)


async def cb_menu_warp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """WARP-egress: статус + управление."""
    await update.callback_query.answer()
    await _render_warp_menu(update, context)


async def cb_warp_probe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Замерить пинг/потери к Telegram через WARP и показать в меню."""
    query = update.callback_query
    await query.answer(_t(_uid(update), "warp_probe_checking"))
    probe = await _warp_read_probe()
    await _render_warp_menu(update, context, probe=probe)


async def _warp_busy(query) -> bool:
    if _BOT_ACTION_LOCK.locked():
        try:
            uid = query.from_user.id if query and query.from_user else None
            await query.message.reply_text(_t(uid, "warp_busy"))
        except Exception:
            pass
        return True
    return False


async def cb_warp_on_free(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer(_t(_uid(update), "warp_enabling"))
    if await _warp_busy(query):
        return
    async with _BOT_ACTION_LOCK:
        r = await run_bot_action("warp-on", timeout=360, key="")
    await _render_warp_menu(update, context)
    stt = r.get("status")
    if stt != "ok":
        try:
            await query.message.reply_text(("\u26a0\ufe0f " if stt == "warn" else "\u274c ") + str(r.get("message", "")))
        except Exception:
            pass


async def cb_warp_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer(_t(_uid(update), "warp_disabling"))
    if await _warp_busy(query):
        return
    async with _BOT_ACTION_LOCK:
        await run_bot_action("warp-off", timeout=90)
    await _render_warp_menu(update, context)


async def cb_warp_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting_warp_key"] = True
    _arm_text_waiter(context)
    await safe_edit_message(
        query,
        _t(_uid(update), "warp_ask_key"),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_warp")]]),
        parse_mode="HTML",
    )


async def cb_pro_template(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show template preview and confirm."""
    query = update.callback_query
    data = query.data
    tpl_key = data.removeprefix("pro_tpl_")
    tpl_id = resolve_pro_template_id(context, tpl_key)

    await query.answer()

    catalog = load_json(TEMPLATES_CATALOG)
    if not catalog:
        await safe_edit_message(query,"❌ Template catalog not found")
        return

    # Find template
    template = None
    for cat in catalog.get("categories", []):
        for tpl in cat.get("templates", []):
            if tpl["id"] == tpl_id:
                template = tpl
                break
        if template:
            break

    if not template:
        await safe_edit_message(query,"❌ Template not found")
        return

    tpl_name = html.escape(template.get('name', 'Unknown'))
    tpl_desc = html.escape(template.get('description', 'N/A'))
    text = (
        f"<b>🎨 Template Preview</b>\n\n"
        f"<b>Name:</b> {tpl_name}\n"
        f"<b>Description:</b> {tpl_desc}\n\n"
    )
    if "preview_url" in template:
        preview_url = html.escape(template['preview_url'], quote=True)
        text += f'<a href="{preview_url}">View Live Preview</a>\n\n'

    text += "Confirm installation?"

    buttons = [
        [
            InlineKeyboardButton(
                "✅ Install", callback_data=f"pro_confirm_{pro_template_key_for_id(context, tpl_id)}"
            )
        ],
        [InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="install_mode_pro")],
    ]
    keyboard = InlineKeyboardMarkup(buttons)
    await safe_edit_message(query,text, reply_markup=keyboard, parse_mode="HTML")


async def cb_pro_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm Pro template selection — real implementation (v2.4.2+).

    Branches on current mode:
      * pro mode (active deployment): invoke `install.sh --action=change-template`
        which downloads the new template and redeploys it to nginx. Reuses the
        existing domain + SSL cert.
      * any other mode (or no install at all): route to CLI. Fresh Pro install
        still requires interactive flow (domain, email, DNS check) — not safe
        to run headless from the bot.

    Historic context: v2.4.1 stub used to overwrite config.json with a fake
    blob; that was replaced with a safe message in v2.4.1 hotfix; now in
    v2.4.2 we wire the real change-template path through install.sh.
    """
    query = update.callback_query
    data = query.data
    tpl_key = data.removeprefix("pro_confirm_")
    tpl_id = resolve_pro_template_id(context, tpl_key)

    await query.answer()

    # Defense-in-depth: even though subprocess.exec uses list args (no shell),
    # we still enforce the catalog id shape before handing it to install.sh.
    if not _TPL_ID_RE.match(tpl_id):
        logger.warning(f"cb_pro_confirm: rejecting malformed tpl_id {tpl_id!r}")
        await safe_edit_message(
            query,
            "<b>❌ Некорректный идентификатор шаблона</b>\n\n"
            "Выбран неподдерживаемый шаблон. Вернитесь в меню и попробуйте снова.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_main")]]
            ),
            parse_mode="HTML",
        )
        return

    # Read current config to decide: in-place change-template vs fresh install
    config = load_json(GOTELEGRAM_CONFIG) or {}
    current_mode = config.get("mode", "")

    if current_mode != "pro":
        # Fresh install / mode switch — still routes to CLI (needs domain, SSL)
        text = (
            "<b>⚠️ Установка Pro из бота пока не поддерживается</b>\n\n"
            f"Выбранный шаблон: <code>{html.escape(tpl_id)}</code>\n\n"
            "Pro-режим требует ввода домена, email и проверки DNS. "
            "Чтобы установить Pro, запустите на сервере:\n"
            "<code>gotelegram</code> → <b>1) Прокси → 1) Установить/Обновить → Pro</b>\n\n"
            "Существующая конфигурация <b>не была изменена</b>."
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_main")]]
        )
        await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")
        return

    # Pro mode is active — perform change-template in place
    if _BOT_ACTION_LOCK.locked():
        await safe_edit_message(
            query,
            "<b>⏳ Другая операция уже выполняется</b>\n\n"
            "Дождитесь завершения предыдущей смены шаблона/домена и повторите.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_main")]]
            ),
            parse_mode="HTML",
        )
        return

    progress_text = (
        "<b>⏳ Меняю шаблон сайта...</b>\n\n"
        f"Шаблон: <code>{html.escape(tpl_id)}</code>\n\n"
        "Скачиваю репозиторий и разворачиваю в nginx. "
        "Это может занять 30–90 секунд."
    )
    await safe_edit_message(query, progress_text, parse_mode="HTML")

    # Template download + git clone can be slow — generous timeout.
    # Mutex serializes with any concurrent change-lite-domain/change-template.
    async with _BOT_ACTION_LOCK:
        result = await run_bot_action("change-template", timeout=180, template=tpl_id)

    if result.get("status") == "success":
        domain = result.get("domain", config.get("domain", ""))
        text = (
            "<b>✅ Шаблон обновлён</b>\n\n"
            f"Новый шаблон: <code>{html.escape(tpl_id)}</code>\n"
            f"Сайт: <a href=\"https://{html.escape(domain, quote=True)}\">https://{html.escape(domain)}</a>\n\n"
            "Прокси продолжает работать без перерыва."
        )
    else:
        err_msg = result.get("message", "unknown error")
        err_code = result.get("code", "")
        # Понятная причина по коду: «в репозитории нет готовой страницы» и «не
        # скачалось» лечатся по-разному.
        _uid_ = _uid(update)
        if err_code == "no_index":
            reason = _t(_uid_, "tpl_err_no_index")
        elif err_code == "download_failed":
            reason = _t(_uid_, "tpl_err_download_failed")
        elif err_code == "app_shell":
            reason = _t(_uid_, "tpl_err_app_shell")
        elif err_code == "broken_assets":
            reason = _t(_uid_, "tpl_err_broken_assets")
        else:
            reason = f"<code>{html.escape(err_msg)}</code>" + (f" (<code>{html.escape(err_code)}</code>)" if err_code else "")
        text = (
            "<b>❌ Не удалось сменить шаблон</b>\n\n"
            f"Шаблон: <code>{html.escape(tpl_id)}</code>\n"
            f"Причина: {reason}\n\n"
            "Существующая конфигурация <b>не была изменена</b>. "
            "Попробуйте другой шаблон или запустите <code>gotelegram</code> из консоли."
        )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_main")]]
    )
    await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)


# ============================================================================
# PROXY LINK & SHARE
# ============================================================================

def quote_toml_key(name: str) -> str:
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def ordered_user_lines(users: Dict[str, str]) -> List[str]:
    names: List[str] = []
    if "main" in users:
        names.append("main")
    names.extend(sorted(name for name in users if name != "main"))
    return [f'{quote_toml_key(name)} = "{users[name]}"' for name in names]


def ordered_user_int_lines(values: Dict[str, int]) -> List[str]:
    positive: Dict[str, int] = {}
    for name, value in values.items():
        name_s = str(name)
        if not _USER_NAME_RE.match(name_s):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            positive[name_s] = number
    names: List[str] = []
    if "main" in positive:
        names.append("main")
    names.extend(sorted(name for name in positive if name != "main"))
    return [f'{quote_toml_key(name)} = {positive[name]}' for name in names]


def load_telemt_users() -> Dict[str, str]:
    """Return users from [access.users] in telemt config."""
    telemt_cfg = load_toml(TELEMT_CONFIG) or {}
    users = telemt_cfg.get("access", {}).get("users", {})
    if not isinstance(users, dict):
        return {}
    return {
        str(name): str(secret)
        for name, secret in users.items()
        if isinstance(name, str) and isinstance(secret, str)
    }


def load_user_max_unique_ips() -> Dict[str, int]:
    telemt_cfg = load_toml(TELEMT_CONFIG) or {}
    limits = telemt_cfg.get("access", {}).get("user_max_unique_ips", {})
    if not isinstance(limits, dict):
        return {}
    clean: Dict[str, int] = {}
    for name, value in limits.items():
        name_s = str(name)
        if not _USER_NAME_RE.match(name_s):
            continue
        try:
            clean[name_s] = max(0, int(value))
        except (TypeError, ValueError):
            continue
    return clean


def load_disabled_users() -> Dict[str, str]:
    raw = load_json(DISABLED_USERS_FILE) or {}
    if not isinstance(raw, dict):
        return {}
    users = raw.get("users") if isinstance(raw.get("users"), dict) else raw
    if not isinstance(users, dict):
        return {}
    clean: Dict[str, str] = {}
    for name, secret in users.items():
        if name in {"version", "updated_at"}:
            continue
        name_s = str(name).strip()
        secret_s = str(secret or "").strip()
        if _USER_NAME_RE.match(name_s) and secret_s:
            clean[name_s] = secret_s
    return clean


def save_disabled_users(users: Dict[str, str]) -> bool:
    payload = {
        "version": 1,
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "users": {name: users[name] for name in sorted(users)},
    }
    ok = save_json(DISABLED_USERS_FILE, payload)
    if ok:
        try:
            os.chmod(DISABLED_USERS_FILE, 0o600)
        except OSError:
            pass
    return ok


def load_user_records() -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    limits = load_user_max_unique_ips()
    for name, secret in load_disabled_users().items():
        records[name] = {"secret": secret, "enabled": False, "max_unique_ips": limits.get(name, 0)}
    for name, secret in load_telemt_users().items():
        records[name] = {"secret": secret, "enabled": True, "max_unique_ips": limits.get(name, 0)}
    return records


def save_toml_int_table(table: str, values: Dict[str, int]) -> bool:
    try:
        os.makedirs(os.path.dirname(TELEMT_CONFIG), exist_ok=True)
        if os.path.exists(TELEMT_CONFIG):
            with open(TELEMT_CONFIG, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()
        else:
            lines = []
        rendered = ordered_user_int_lines(values)
        header = f"[{table}]"
        out: List[str] = []
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
        tmp = f"{TELEMT_CONFIG}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(out).rstrip() + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, TELEMT_CONFIG)
        return True
    except Exception as e:
        logger.error(f"Failed to save telemt int table {table}: {e}")
        return False


def save_user_max_unique_ips(values: Dict[str, int]) -> bool:
    return save_toml_int_table("access.user_max_unique_ips", values)


def normalize_max_unique_ips(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("Лимит должен быть целым числом")
    if number < 0 or number > MAX_UNIQUE_IP_LIMIT:
        raise ValueError(f"Лимит должен быть от 0 до {MAX_UNIQUE_IP_LIMIT}")
    # Единица запрещена во всех трёх интерфейсах: телефон при смене Wi-Fi ↔ LTE меняет
    # адрес, старые соединения ещё живы, и клиент отбивается собственным лимитом.
    # 0 — снять персональный лимит, минимальное осмысленное значение — 2.
    if number == 1:
        raise ValueError(MAX_UNIQUE_IPS_MIN_MESSAGE)
    return number


WEB_SECRET_MODE = "dd"


def _web_profile_lines(user_lines: List[str]) -> List[str]:
    """Профили WEB — по одному на каждый ключ, тем же секретом.

    Та же логика, что у telemt_web_sync_profiles (lib/telemt_config.sh) и у
    панели: профиль, ссылающийся на удалённого пользователя, делает конфиг
    невалидным, и следующий перезапуск telemt оставил бы прокси лежать.
    """
    out: List[str] = []
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


def _strip_web_profiles(lines: List[str]) -> List[str]:
    out: List[str] = []
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


def save_telemt_users(users: Dict[str, str]) -> bool:
    """Persist [access.users] while keeping the rest of the TOML structure."""
    global _TELEMT_RESTART_REQUIRED
    try:
        os.makedirs(os.path.dirname(TELEMT_CONFIG), exist_ok=True)
        if os.path.exists(TELEMT_CONFIG):
            with open(TELEMT_CONFIG, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()
        else:
            lines = []
        rendered = ordered_user_lines(users)
        out: List[str] = []
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
        had_web_limits = any(line.strip() == "[web.limits]" for line in out)
        out = _ensure_web_limits(out)
        if not had_web_limits and any(line.strip() == "[web.limits]" for line in out):
            # [web.limits] ядро читает только при старте — применит его лишь рестарт
            _TELEMT_RESTART_REQUIRED = True
        tmp = f"{TELEMT_CONFIG}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(out).rstrip() + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, TELEMT_CONFIG)
        return True
    except Exception as e:
        logger.error(f"Failed to save telemt users: {e}")
        return False


async def _telemt_deferred_restart(delay: float) -> None:
    """Один отложенный рестарт на все правки, накопившиеся в окне дебаунса."""
    global _LAST_TELEMT_RESTART, _TELEMT_RESTART_PENDING
    try:
        await asyncio.sleep(max(0.5, delay))
    finally:
        # флаг снимаем ДО рестарта: правка, пришедшая позже, обязана запланировать свой
        _TELEMT_RESTART_PENDING = False
    await sh("systemctl", "reset-failed", TELEMT_SERVICE, timeout=5)
    _LAST_TELEMT_RESTART = time.monotonic()
    await sh("systemctl", "--no-block", "restart", TELEMT_SERVICE, timeout=5)


async def _telemt_request_restart() -> bool:
    """Полный рестарт telemt с коалесценцией частых кликов (запасной путь)."""
    global _LAST_TELEMT_RESTART, _TELEMT_RESTART_PENDING
    now = time.monotonic()
    if _LAST_TELEMT_RESTART > 0 and now - _LAST_TELEMT_RESTART < TELEMT_RESTART_DEBOUNCE_SECONDS:
        # рестарт не пропускаем, а откладываем: раньше второе изменение в окне
        # молча терялось — config.toml записан, но работающий telemt его не читал
        if not _TELEMT_RESTART_PENDING:
            _TELEMT_RESTART_PENDING = True
            delay = TELEMT_RESTART_DEBOUNCE_SECONDS - (now - _LAST_TELEMT_RESTART)
            spawn_task(_telemt_deferred_restart(delay))
        return True
    await sh("systemctl", "reset-failed", TELEMT_SERVICE, timeout=5)
    _LAST_TELEMT_RESTART = now
    code, _, _ = await sh("systemctl", "--no-block", "restart", TELEMT_SERVICE, timeout=5)
    return code == 0


# ── Правки ключей без рестарта ────────────────────────────────────────────────
# Рестарт telemt рвёт ВСЕХ клиентов: 443 закрыт ~10 с, ME-пул поднимается заново.
# Ядро же само перечитывает config.toml (inotify-watcher и SIGHUP) и на лету
# применяет [access.users], [access.user_max_unique_ips] и (3.5.x) профили WEB.
# Поэтому: SIGHUP главному процессу и подтверждение через API ядра; не подтвердилось
# за TELEMT_RELOAD_TIMEOUT — прежний рестарт. Та же логика — у панели и в lib/users.sh.
#
# Отзыв ключа (удаление, выключение) горячо — только с 3.5.14: там ядро рвёт живые
# сессии отозванного секрета. В 3.4.18 и 3.5.5 перезагрузка конфига закрывает лишь
# новые подключения, а уже открытые жили бы до разрыва — поэтому там рестарт.
TELEMT_HOT_REVOKE_MIN_VERSION = (3, 5, 14)
TELEMT_RELOAD_TIMEOUT = float(os.getenv("GOTELEGRAM_TELEMT_RELOAD_TIMEOUT", "8"))
# watcher ядра: дебаунс 50 мс, разбор и проверка конфига — с запасом
TELEMT_RELOAD_SETTLE = 0.6
TELEMT_RELOAD_POLL = 0.25
_TELEMT_APPLY_PENDING: Optional[Dict[str, Any]] = None
_TELEMT_APPLY_RUNNING = False


def _telemt_version_tuple(raw: Any) -> Tuple[int, ...]:
    m = re.match(r"\s*v?(\d+)\.(\d+)(?:\.(\d+))?", str(raw or ""))
    return tuple(int(x or 0) for x in m.groups()) if m else ()


def _telemt_api_data(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("ok") is False:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def _telemt_int(data: Optional[Dict[str, Any]], key: str) -> int:
    try:
        return int((data or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _telemt_config_applied(before: Optional[Dict[str, Any]], now: Optional[Dict[str, Any]],
                           written: float, settled: bool, strict: bool = False) -> bool:
    """Видит ли работающее ядро config.toml, записанный в момент written (mtime).

    Признаки из GET /v1/system/info (одинаковы в 3.4.18 и 3.5.x):
    - процесс стартовал в следующую секунду после записи или позже — читал уже новый файл;
    - у того же процесса вырос config_reload_count — ядро опубликовало новый снимок конфига
      (счётчик растёт только на опубликованном снимке, не на каждом SIGHUP);
    - last_config_reload_epoch_secs не раньше записи — снимок успел опубликовать
      inotify-watcher, и SIGHUP уже ничего не меняет. Этот признак берём лишь после паузы
      на разбор (settled): в ту же секунду мог лечь снимок от предыдущей записи.

    strict — before снят ДО записи файла: тогда достаточно роста счётчика, а
    секундный признак по времени не нужен (он путает соседние записи в одну секунду).
    """
    if not now:
        return False
    written_secs = int(written)
    started = _telemt_int(now, "process_started_at_epoch_secs")
    if started > written_secs:
        return True
    if (before and started == _telemt_int(before, "process_started_at_epoch_secs")
            and _telemt_int(now, "config_reload_count") > _telemt_int(before, "config_reload_count")):
        return True
    if strict:
        return False
    return settled and _telemt_int(now, "last_config_reload_epoch_secs") >= written_secs


async def _telemt_api(path: str) -> Optional[Dict[str, Any]]:
    return _telemt_api_data(await telemt_api_get(path))


def _telemt_not_found(payload: Any) -> bool:
    """Ответ API ядра «такого ключа нет» (404, error.code = not_found)."""
    if not isinstance(payload, dict) or payload.get("ok") is not False:
        return False
    err = payload.get("error")
    code = err.get("code") if isinstance(err, dict) else err
    return str(code or "") == "not_found"


async def _telemt_users_in_runtime(names: List[str]) -> bool:
    """Новые ключи видны работающему ядру (поле in_runtime в GET /v1/users/{имя})."""
    for name in names:
        payload = await telemt_api_get(f"/v1/users/{quote(name, safe='')}")
        # Ключа уже нет в config.toml (успели удалить) — проверять нечего. Прочие
        # ok:false (401, 5xx, ядро не разобрало файл) — не подтверждение.
        if _telemt_not_found(payload):
            continue
        data = _telemt_api_data(payload)
        if not data or data.get("in_runtime") is not True:
            return False
    return True


async def telemt_reload_mark() -> Optional[Dict[str, Any]]:
    """Снимок /v1/system/info ДО записи config.toml — точка отсчёта для подтверждения.

    Рост config_reload_count от этого снимка однозначно значит «ядро опубликовало
    конфиг, записанный после него». None — API молчит, подтверждение по-старому.
    """
    return await _telemt_api("/v1/system/info")


async def telemt_hot_reload(revoke: bool = False, names: Optional[List[str]] = None,
                            before: Optional[Dict[str, Any]] = None) -> bool:
    """Применить записанный config.toml без рестарта. False — нужен прежний рестарт.

    before — снимок telemt_reload_mark() до записи файла (или None).
    """
    names = list(names or [])
    strict = bool(before)
    if _TELEMT_RESTART_REQUIRED:
        return False
    code, out, _ = await sh("systemctl", "is-active", TELEMT_SERVICE, timeout=5)
    if code != 0 or out.strip() != "active":
        return False
    try:
        written = os.stat(TELEMT_CONFIG).st_mtime
    except OSError:
        return False
    info = await _telemt_api("/v1/system/info")
    if not info:
        return False  # API ядра молчит — подтвердить нечем
    if revoke and _telemt_version_tuple(info.get("version")) < TELEMT_HOT_REVOKE_MIN_VERSION:
        return False
    deadline = time.monotonic() + TELEMT_RELOAD_TIMEOUT
    # SIGHUP до того, как ядро повесило обработчик, по умолчанию убивает процесс:
    # шлём только ядру, которое уже не меньше секунды в статусе ready
    while True:
        if _telemt_config_applied(before, info, written, False, strict):
            if await _telemt_users_in_runtime(names):
                return True
        init = await _telemt_api("/v1/runtime/initialization") or {}
        ready_at = _telemt_int(init, "ready_at_epoch_secs")
        if init.get("status") == "ready" and ready_at and time.time() - ready_at >= 2:  # ready_at округлён вниз до секунды
            break
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(TELEMT_RELOAD_POLL * 2)
        info = await _telemt_api("/v1/system/info")
        if not info:
            return False
    code, out, _ = await sh("systemctl", "show", "-p", "MainPID", "--value", TELEMT_SERVICE, timeout=5)
    pid = int(out.strip()) if code == 0 and out.strip().isdigit() else 0
    if pid <= 1:
        return False
    # Пока читали MainPID, ядро могли перезапустить (install.sh, панель): новый
    # процесс до регистрации обработчика SIGHUP от него умрёт. Тот же старт
    # процесса до и после чтения PID — значит PID принадлежит готовому ядру.
    again = await _telemt_api("/v1/system/info")
    if (not again or _telemt_int(again, "process_started_at_epoch_secs")
            != _telemt_int(info, "process_started_at_epoch_secs")):
        # без HUP: новый процесс, стартовавший после записи, уже читал свежий файл
        return bool(_telemt_config_applied(before, again, written, False, strict)
                    and await _telemt_users_in_runtime(names))
    try:
        os.kill(pid, signal.SIGHUP)
    except OSError:
        return False
    sent = time.monotonic()
    while time.monotonic() < deadline:
        await asyncio.sleep(TELEMT_RELOAD_POLL)
        now = await _telemt_api("/v1/system/info")
        settled = time.monotonic() - sent >= TELEMT_RELOAD_SETTLE
        if (_telemt_config_applied(before or info, now, written, settled, strict)
                and await _telemt_users_in_runtime(names)):
            return True
    return False


async def _telemt_apply_worker() -> None:
    """Один исполнитель на все правки: пока применяет одну, следующие копятся в одну."""
    global _TELEMT_APPLY_PENDING, _TELEMT_APPLY_RUNNING, _TELEMT_RESTART_REQUIRED
    try:
        while _TELEMT_APPLY_PENDING is not None:
            job, _TELEMT_APPLY_PENDING = _TELEMT_APPLY_PENDING, None
            try:
                applied = await telemt_hot_reload(job["revoke"], sorted(job["names"]), job.get("before"))
            except Exception as e:
                logger.warning(f"telemt hot reload failed: {e}")
                applied = False
            if applied:
                logger.info("telemt: config applied without restart")
                continue
            logger.info("telemt: hot reload not confirmed, restarting")
            _TELEMT_RESTART_REQUIRED = False
            await _telemt_request_restart()
    finally:
        _TELEMT_APPLY_RUNNING = False


async def refresh_telemt_after_user_change(change: str = "add", names: Optional[List[str]] = None,
                                           before: Optional[Dict[str, Any]] = None) -> bool:
    """Применить правку ключей в config.toml: перезагрузка конфига, рестарт — запасной путь.

    change: "add" (новый или включённый ключ — его имя в names), "limit" (лимит IP),
    "revoke" (удалённый или выключенный ключ). Применение идёт фоном, как и прежний
    рестарт с --no-block: ответ в Telegram не ждёт ядро.
    before — telemt_reload_mark(), снятый до записи файла. В объединённом задании
    берём снимок последней записи: рост счётчика от него покрывает и прежние.
    """
    global _TELEMT_APPLY_PENDING, _TELEMT_APPLY_RUNNING
    job = _TELEMT_APPLY_PENDING or {"revoke": False, "names": set()}
    job["revoke"] = job["revoke"] or change == "revoke"
    job["names"].update(n for n in (names or []) if n)
    job["before"] = before
    _TELEMT_APPLY_PENDING = job
    if not _TELEMT_APPLY_RUNNING:
        _TELEMT_APPLY_RUNNING = True
        spawn_task(_telemt_apply_worker())
    return True


async def telemt_api_get(path: str) -> Optional[Dict[str, Any]]:
    """Read telemt local API if it is enabled in config."""
    code, stdout, _ = await sh(
        "curl",
        "-sS",
        "--max-time",
        "3",
        f"http://127.0.0.1:9091{path}",
        timeout=5,
    )
    if code != 0 or not stdout.strip():
        return None
    try:
        data = json.loads(stdout)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_traffic_value(data: Any, keys: List[str]) -> int:
    if isinstance(data, dict):
        total = 0
        for key, value in data.items():
            if key in keys and isinstance(value, (int, float)):
                total += int(value)
            elif isinstance(value, (dict, list)):
                total += _extract_traffic_value(value, keys)
        return total
    if isinstance(data, list):
        return sum(_extract_traffic_value(item, keys) for item in data)
    return 0


def user_traffic_history_summary(name: str, user_id: Optional[int] = None) -> str:
    rows: List[Dict[str, int]] = []
    try:
        with open(USER_STATS_HISTORY, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            previous = None
            for row in reader:
                if row.get("user") != name:
                    continue
                try:
                    item = {
                        "epoch": int(row.get("epoch") or 0),
                        "total_octets": int(row.get("total_octets") or 0),
                    }
                except ValueError:
                    continue
                item["total_delta"] = max(0, item["total_octets"] - previous["total_octets"]) if previous else 0
                rows.append(item)
                previous = item
    except Exception:
        rows = []

    if not rows:
        return "\n<i>" + _t(user_id, "user_history_empty") + "</i>"

    latest = max(row["epoch"] for row in rows)
    periods = [
        (_t(user_id, "user_hist_15m"), 15 * 60),
        (_t(user_id, "user_hist_1h"), 60 * 60),
        (_t(user_id, "user_hist_24h"), 24 * 60 * 60),
        (_t(user_id, "user_hist_month"), 30 * 24 * 60 * 60),
    ]
    col_period = _t(user_id, "user_hist_col_period")
    col_traffic = _t(user_id, "user_hist_col_traffic")
    lines = [
        "\n<b>" + _t(user_id, "user_history_title") + "</b>",
        "<pre>",
        f"{col_period:<8} │ {col_traffic:>10}",
        "─" * 23,
    ]
    for label, seconds in periods:
        window = [row for row in rows if row["epoch"] >= latest - seconds]
        total = sum(max(0, row.get("total_delta", 0)) for row in window)
        lines.append(f"{label:<8} │ {format_bytes_human(total):>10}")
    lines.append("</pre>")
    return "\n".join(lines)


async def get_proxy_link_for_secret(secret: str) -> Optional[str]:
    """Generate a fake-TLS proxy link for an arbitrary telemt user secret."""
    config = load_json(GOTELEGRAM_CONFIG) or {}
    if not secret:
        return None

    mode = config.get("mode", "lite")
    domain = config.get("domain", "")
    port = config.get("port", 443)

    if mode == "pro" and domain:
        domain_hex = str(domain).encode().hex()
        return f"tg://proxy?server={domain}&port={port}&secret=ee{secret}{domain_hex}"

    code, stdout, _ = await sh("curl", "-s", "-4", "--max-time", "5", "https://api.ipify.org")
    server = stdout.strip() if code == 0 and stdout.strip() else "0.0.0.0"
    # Домен маскировки берём из config.json, а если там пусто — из конфига
    # ядра ([censorship] tls_domain): ядро всегда работает в fake-TLS, и «голая»
    # ссылка без ee-префикса к нему не подключается. Пустой mask_host в
    # config.json встречается на старых установках — ссылка выходила короткой.
    mask_host = str(config.get("mask_host", "") or "") or read_tls_domain() or domain
    if mask_host:
        domain_hex = str(mask_host).encode().hex()
        return f"tg://proxy?server={server}&port={port}&secret=ee{secret}{domain_hex}"
    return f"tg://proxy?server={server}&port={port}&secret={secret}"


def read_tls_domain() -> str:
    """[censorship] tls_domain из конфига ядра — источник правды для fake-TLS."""
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


def get_web_link_for_secret(secret: str) -> Optional[str]:
    """WEB-ссылка (Telegram Desktop 7.1+): тот же секрет в представлении dd.

    Пусто, пока WEB-прокси выключен: ссылка, которую нечем обслужить, только
    сбивает с толку.
    """
    config = load_json(GOTELEGRAM_CONFIG) or {}
    if not secret or str(config.get("web_enabled", "")).lower() != "true":
        return None
    domain = str(config.get("domain", "") or "")
    if config.get("mode", "lite") != "pro" or not domain:
        return None
    # Только когда WEB реально собран в конфиге ядра: флаг мог остаться от
    # ядра без WEB, и ссылка была бы мёртвой.
    core = _web_core_info()
    if core is None or not core[0]:
        return None
    # Имя — из WEB-vhost ядра: при отдельном имени WEB capability считается от
    # него, и ссылка с основным доменом была бы мёртвой.
    host = core[1] or domain
    return f"tg://webproxy?server={host}&secret=dd{secret}"


# (mtime_ns, size, inode) → (WEB-listener есть, host vhost). Карточка ключа и
# ряд QR спрашивают ссылку по нескольку раз, а файл с сотнями профилей WEB
# тяжёлый: перечитываем, только когда он сменился.
_WEB_CORE_CACHE: Dict[str, Any] = {"sig": None, "info": None}


def _web_core_info() -> Optional[Tuple[bool, str]]:
    """(WEB собран в конфиге ядра, host первого [[web.vhosts]]); None — не прочитать."""
    try:
        st = os.stat(TELEMT_CONFIG)
        sig = (st.st_mtime_ns, st.st_size, st.st_ino)
        if _WEB_CORE_CACHE["sig"] == sig:
            return _WEB_CORE_CACHE["info"]
        with open(TELEMT_CONFIG, "r", encoding="utf-8", errors="ignore") as f:
            core = f.read()
    except OSError:
        return None
    info = ('\ntransport = "web"' in "\n" + core, _web_vhost_host(core))
    _WEB_CORE_CACHE.update(sig=sig, info=info)
    return info


def _web_vhost_host(core: str) -> str:
    """host первого [[web.vhosts]] из текста конфига ядра; пусто — нет."""
    in_vhost = False
    for raw in core.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_vhost = line == "[[web.vhosts]]"
            continue
        key, eq, value = line.partition("=")
        # ровно ключ host (не hosts/hostname), значение без хвостового комментария
        if in_vhost and eq and key.strip() == "host":
            value = value.strip()
            if value.startswith('"'):
                return value[1:].split('"', 1)[0]
            return value.split("#", 1)[0].strip()
    return ""


def _main_secret() -> str:
    """Секрет ключа main: из config.json, иначе из [access.users] telemt."""
    config = load_json(GOTELEGRAM_CONFIG)
    if not config:
        return ""
    secret = config.get("secret", "")
    if not secret:
        telemt_cfg = load_toml(TELEMT_CONFIG)
        if telemt_cfg:
            users = telemt_cfg.get("access", {}).get("users", {})
            if isinstance(users, dict):
                secret = users.get("main", "")
    return str(secret or "")


async def get_proxy_link() -> Optional[str]:
    """Generate proxy link from config. Pro-mode uses domain + fake-TLS secret."""
    secret = _main_secret()
    if not secret:
        return None
    return await get_proxy_link_for_secret(secret)


def get_web_link() -> Optional[str]:
    """WEB-ссылка главного ключа; пусто, пока WEB-прокси выключен."""
    return get_web_link_for_secret(_main_secret())


def _web_link_block(user_id: Optional[int], link: Optional[str]) -> str:
    """Общий хвост экранов со ссылкой: WEB-вариант и подсказка про предупреждение клиента."""
    if not link:
        return ""
    return (
        f"\n\n<b>{_t(user_id, 'web_link_title')}</b>\n<code>{html.escape(link)}</code>\n"
        f"<i>{_t(user_id, 'web_link_hint')}</i>"
    )


async def cb_menu_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and show proxy link."""
    query = update.callback_query
    await query.answer()

    link = await get_proxy_link()
    if not link:
        text = "❌ " + _t(_uid(update), "link_unavailable")
    else:
        text = (
            f"<b>🔗 Proxy Link</b>\n\n"
            f"<code>{html.escape(link)}</code>\n\n"
            f"Open in Telegram to connect."
            + _web_link_block(_uid(update), get_web_link())
        )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_main")]]
    )
    await safe_edit_message(query,text, reply_markup=keyboard, parse_mode="HTML")


async def cb_menu_share(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Share link as QR code."""
    query = update.callback_query
    await query.answer()

    link = await get_proxy_link()
    if not link:
        text = "❌ " + _t(_uid(update), "link_unavailable")
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_main")]]
        )
        await safe_edit_message(query,text, reply_markup=keyboard)
        return

    # Try to generate QR code
    qr_file = None
    code, _, _ = await sh("which", "qrencode")
    if code == 0:
        qr_path = "/tmp/proxy_qr.png"
        # Здесь имени ключа нет — это общая ссылка прокси, поэтому подписываем
        # тем, что отличает её от чужой: адресом сервера из самой ссылки.
        code, _, _ = await sh("qrencode", "-s", "8", "-o", qr_path, link)
        try:
            from urllib.parse import urlparse, parse_qs
            _srv = (parse_qs(urlparse(link.replace("tg://", "https://tg/")).query)
                    .get("server") or [""])[0]
        except Exception:
            _srv = ""
        qr_add_label(qr_path, _srv)
        if code == 0 and os.path.exists(qr_path):
            qr_file = qr_path

    if qr_file:
        try:
            with open(qr_file, "rb") as f:
                await query.message.reply_photo(
                    photo=f,
                    caption=f"<b>{_t(_uid(update), 'share_title')}</b>\n\n"
                            f"{_t(_uid(update), 'share_body')}\n{html.escape(link)}"
                            + _web_link_block(_uid(update), get_web_link()),
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error(f"Failed to send QR code: {e}")
            await safe_edit_message(query,
                f"<b>🔗 Proxy Link</b>\n\n<code>{html.escape(link)}</code>",
                parse_mode="HTML",
            )
        finally:
            try:
                os.remove(qr_file)
            except OSError:
                pass
    else:
        await safe_edit_message(query,
            f"<b>🔗 Proxy Link</b>\n\n<code>{html.escape(link)}</code>",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_main")]]
            ),
            parse_mode="HTML",
        )


# ============================================================================
# TELEMT USERS
# ============================================================================


# Имя ключа может содержать "_" (см. _USER_NAME_RE), поэтому имя прямо в
# callback_data ломало маршрутизацию: у ключа с именем "yes_bob" кнопка
# «Удалить» давала user_del_yes_bob, и роутер принимал её за ПОДТВЕРЖДЁННОЕ
# удаление ключа "bob" — чужой ключ сносился без подтверждения. Теперь в
# callback уходит короткий hex-токен, разделитель "~" вне алфавита имён,
# а имя всегда сверяется с реальным списком ключей.
_USER_CB_KEYS: Dict[str, str] = {}


def user_cb_key(name: str) -> str:
    """Короткий стабильный токен имени для callback_data (лимит 64 байта)."""
    name = str(name)
    token = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
    _USER_CB_KEYS[token] = name
    return token


def resolve_user_cb_key(token: str, records: Optional[Dict[str, Any]] = None) -> str:
    """Имя ключа по токену. Возвращает "" если такого ключа больше нет."""
    token = str(token or "")
    if records is None:
        records = load_user_records()
    cached = _USER_CB_KEYS.get(token)
    if cached and cached in records:
        return cached
    for candidate in records:
        if hashlib.sha1(str(candidate).encode("utf-8")).hexdigest()[:12] == token:
            _USER_CB_KEYS[token] = str(candidate)
            return str(candidate)
    return ""


USERS_PER_PAGE = 8

def _users_sorted(users: Dict[str, Dict[str, Any]]) -> List[str]:
    return sorted(users, key=lambda item: (item != "main", item))


def _users_keyboard(users: Dict[str, Dict[str, Any]], user_id: Optional[int], page: int = 0) -> InlineKeyboardMarkup:
    # Без страниц список из сотни ключей упирался в лимиты Telegram: текст длиннее
    # 4096 символов или слишком много кнопок → edit_message падал с BadRequest, и
    # раздел «Ключи» становился недоступен вообще. Каталог шаблонов давно листается,
    # ключи почему-то нет.
    names = _users_sorted(users)
    pages = max(1, (len(names) + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    rows = []
    for name in names[page * USERS_PER_PAGE:(page + 1) * USERS_PER_PAGE]:
        enabled = bool(users[name].get("enabled"))
        icon = "🟢" if enabled else "⏸"
        rows.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"user_view~{user_cb_key(name)}")])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("‹", callback_data=f"menu_users~{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("›", callback_data=f"menu_users~{page + 1}"))
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(_t(user_id, "users_btn_add"), callback_data="user_add"),
        InlineKeyboardButton(_t(user_id, "users_btn_export"), callback_data="user_export"),
    ])
    rows.append([
        InlineKeyboardButton(_t(user_id, "btn_refresh"), callback_data="menu_users"),
        InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_main"),
    ])
    return InlineKeyboardMarkup(rows)


# Экспорт всех ключей разом. Поштучно карточка и QR были и раньше, но раздать
# ключи десятку человек значило открыть каждый и переслать вручную.
#
# Отдаём двумя частями, потому что они решают разные задачи:
#   • .txt — полный список (имя, секрет, ссылка). Не упирается в лимит 4096
#     символов на сообщение и переживает любое число ключей;
#   • альбом QR — то, что реально сканируют с экрана. Telegram кладёт в один
#     альбом не больше 10 картинок, поэтому шлём пачками и предупреждаем, если
#     ключей больше потолка: молча обрезать выдачу нельзя, человек решит, что
#     остальные ключи потерялись.
# Имя ключа впечатываем В САМУ картинку, а не только в подпись сообщения.
# Причина простая: в альбоме Telegram показывает подпись лишь при открытии
# картинки, в сетке её не видно вовсе. А сохранённый или пересланный QR теряет
# подпись совсем — и человек получает набор одинаковых квадратов, про которые
# уже не скажешь, где чей. Текст внутри изображения переживает и то и другое.
#
# Pillow не обязателен: без него просто отдаём голый QR, как раньше. Ставить
# зависимость ради подписи и падать без неё было бы неразумно.
_QR_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # кириллица есть
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
)


def _qr_label_font(size: int):
    from PIL import ImageFont
    for path in _QR_FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # Встроенный шрифт мелкий и без кириллицы, но лучше, чем ничего.
    return ImageFont.load_default()


def qr_add_label(qr_path: str, label: str) -> bool:
    """Дорисовать подпись под QR. True — получилось, False — оставили как было."""
    if not label:
        return False
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return False
    try:
        with Image.open(qr_path) as src:
            # qrencode отдаёт палитровый PNG с прозрачностью, и прямой
            # convert("RGB") на нём сыплет предупреждением Pillow в журнал.
            # Кладём на белый холст через альфу — и корректно, и молча.
            if src.mode in ("P", "LA", "RGBA"):
                src = src.convert("RGBA")
                qr = Image.new("RGB", src.size, "white")
                qr.paste(src, mask=src.split()[-1])
            else:
                qr = src.convert("RGB")
            w, h = qr.size
            font_size = max(14, w // 16)
            font = _qr_label_font(font_size)
            pad = max(8, font_size // 2)

            probe = ImageDraw.Draw(qr)
            box = probe.textbbox((0, 0), label, font=font)
            tw, th = box[2] - box[0], box[3] - box[1]
            # Длинное имя не должно вылезать за края: ужимаем шрифт, пока влезает.
            while tw > w - 2 * pad and font_size > 8:
                font_size -= 1
                font = _qr_label_font(font_size)
                box = probe.textbbox((0, 0), label, font=font)
                tw, th = box[2] - box[0], box[3] - box[1]

            strip = th + 2 * pad
            out = Image.new("RGB", (w, h + strip), "white")
            out.paste(qr, (0, 0))
            draw = ImageDraw.Draw(out)
            draw.text(((w - tw) // 2 - box[0], h + pad - box[1]), label,
                      fill="black", font=font)
            out.save(qr_path)
        return True
    except Exception as exc:
        logger.warning("qr label: %s", exc)
        return False


EXPORT_QR_LIMIT = 30          # сверх этого QR не рисуем, .txt отдаём целиком
EXPORT_ALBUM_SIZE = 10        # жёсткий предел Telegram на медиагруппу


async def cb_user_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = _uid(update)
    if not await require_auth(update, context):
        return

    users = load_user_records()
    names = _users_sorted(users)
    if not names:
        await query.answer(_t(user_id, "users_empty"), show_alert=True)
        return

    await safe_edit_message(
        query,
        f"<b>{_t(user_id, 'users_export_working')}</b>",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_users")]]
        ),
        parse_mode="HTML",
    )

    # ── 1. текстовый список ──────────────────────────────────────────────────
    lines, pairs = [], []
    for name in names:
        rec = users.get(name) or {}
        secret = str(rec.get("secret", ""))
        link = await get_proxy_link_for_secret(secret) or ""
        state = "on" if rec.get("enabled") else "off"
        lines.append(f"{name}\t{state}\t{secret}\t{link}")
        if link:
            pairs.append((name, link))

    header = "# goTelegram Pro — ключи\n# имя\tсостояние\tсекрет\tссылка\n"
    blob = (header + "\n".join(lines) + "\n").encode("utf-8")
    try:
        await query.message.reply_document(
            document=InputFile(io.BytesIO(blob), filename="gotelegram-keys.txt"),
            caption=_tf(user_id, "users_export_file", len(names)),
        )
    except Exception as exc:
        logger.warning("export file: %s", exc)
        await query.message.reply_text(_t(user_id, "users_export_failed"))
        return

    # ── 2. QR-коды ───────────────────────────────────────────────────────────
    code, _, _ = await sh("which", "qrencode")
    if code != 0:
        await query.message.reply_text(_t(user_id, "users_export_no_qr"))
        return

    capped = pairs[:EXPORT_QR_LIMIT]
    media, tmp_files = [], []
    try:
        for name, link in capped:
            qr_file = f"/tmp/gt_export_{hashlib.sha256(name.encode()).hexdigest()[:10]}.png"
            # -s 8: крупнее умолчания (3), иначе подпись рядом с мелким QR
            # выглядит несоразмерно, да и сканируется он хуже с экрана.
            rc, _, _ = await sh("qrencode", "-s", "8", "-o", qr_file, link)
            if rc != 0 or not os.path.exists(qr_file):
                continue
            qr_add_label(qr_file, name)
            tmp_files.append(qr_file)
            media.append((name, qr_file))

        for i in range(0, len(media), EXPORT_ALBUM_SIZE):
            chunk = media[i:i + EXPORT_ALBUM_SIZE]
            group = []
            for name, path in chunk:
                with open(path, "rb") as fh:
                    group.append(InputMediaPhoto(media=fh.read(), caption=name))
            try:
                await query.message.reply_media_group(media=group)
            except Exception as exc:
                logger.warning("export album: %s", exc)
    finally:
        for f in tmp_files:
            try:
                os.remove(f)
            except OSError:
                pass

    if len(pairs) > EXPORT_QR_LIMIT:
        # Явно говорим, сколько НЕ нарисовано: молчание тут читается как потеря ключей.
        await query.message.reply_text(
            _tf(user_id, "users_export_qr_capped", EXPORT_QR_LIMIT, len(pairs) - EXPORT_QR_LIMIT)
        )


async def cb_menu_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = _uid(update)
    users = load_user_records()
    raw = (query.data or "")
    try:
        page = int(raw.split("~", 1)[1]) if "~" in raw else 0
    except (ValueError, IndexError):
        page = 0

    if users:
        names = _users_sorted(users)
        shown = names[page * USERS_PER_PAGE:(page + 1) * USERS_PER_PAGE]
        user_lines = "\n".join(
            f"{'🟢' if users[name].get('enabled') else '⏸'} <code>{html.escape(name)}</code>"
            for name in shown
        )
        if len(names) > len(shown):
            user_lines += f"\n<i>… {len(names)} всего</i>"
    else:
        user_lines = "<i>" + _t(user_id, "users_empty") + "</i>"

    api_summary = await telemt_api_get("/v1/stats/summary")
    api_note = ""
    if api_summary and isinstance(api_summary.get("data"), dict):
        data = api_summary["data"]
        configured = data.get("configured_users")
        active = data.get("active_connections") or data.get("connections_active")
        bits = []
        if configured is not None:
            bits.append(f"users: <code>{configured}</code>")
        if active is not None:
            bits.append(f"active: <code>{active}</code>")
        if bits:
            api_note = "\n\nAPI: " + ", ".join(bits)

    text = (
        f"<b>{_t(user_id, 'users_title')}</b>\n\n"
        f"{user_lines}"
        f"{api_note}\n\n"
        f"<i>{_t(user_id, 'users_hint')}</i>"
    )
    await safe_edit_message(query, text, reply_markup=_users_keyboard(users, user_id, page), parse_mode="HTML")


def read_global_ip_limit() -> int:
    """Общий лимит уникальных IP из [access] конфига ядра (0 — не задан)."""
    try:
        with open(TELEMT_CONFIG, "r", encoding="utf-8", errors="ignore") as f:
            m = re.search(r"^user_max_unique_ips_global_each\s*=\s*(\d+)", f.read(), re.M)
        return int(m.group(1)) if m else 0
    except (OSError, ValueError):
        return 0


async def _user_detail_text(
    name: str,
    secret: str,
    enabled: bool = True,
    max_unique_ips: int = 0,
    user_id: Optional[int] = None,
) -> str:
    link = await get_proxy_link_for_secret(secret)
    api = await telemt_api_get(f"/v1/users/{quote(name, safe='')}") if enabled else None
    details = ""
    if api:
        data = api.get("data", api)
        total = int(data.get("total_octets") or 0) if isinstance(data, dict) else 0
        conns = int(data.get("current_connections") or 0) if isinstance(data, dict) else 0
        active_ips = int(data.get("active_unique_ips") or 0) if isinstance(data, dict) else 0
        recent_ips = int(data.get("recent_unique_ips") or 0) if isinstance(data, dict) else 0
        parts = [
            f"{_t(user_id, 'user_traffic_total')}: <b>{format_bytes_human(total)}</b>",
            f"{_t(user_id, 'user_connections')}: <code>{conns}</code>",
            f"{_t(user_id, 'user_active_ips')}: <code>{active_ips}</code>",
        ]
        if recent_ips:
            parts.append(f"{_t(user_id, 'user_recent_ips')}: <code>{recent_ips}</code>")
        details = "\n" + "\n".join(parts)
    elif enabled:
        details = "\n<i>" + _t(user_id, "user_api_unavailable") + "</i>"
    else:
        details = "\n<i>" + _t(user_id, "user_disabled_note") + "</i>"
    details += user_traffic_history_summary(name, user_id)

    link_line = html.escape(link) if link else _t(user_id, "user_link_unavailable")
    web = get_web_link_for_secret(secret)
    web_line = f"<b>{_t(user_id, 'user_web_link_label')}:</b>\n<code>{html.escape(web)}</code>\n" if web else ""
    status_line = _t(user_id, "user_status_enabled") if enabled else _t(user_id, "user_status_disabled")
    # Ноль в персональной таблице не означает «без ограничений»: сверху может
    # действовать общий лимит из [access]. SSH показывает его с пометкой, бот
    # писал «безлимит» и вводил в заблуждение.
    if max_unique_ips:
        limit_line = str(max_unique_ips)
    else:
        glob = read_global_ip_limit()
        limit_line = (_tf(user_id, "user_iplimit_global", glob) if glob
                      else _t(user_id, "user_iplimit_unlimited"))
    return (
        f"<b>👤 {html.escape(name)}</b>\n\n"
        f"{_t(user_id, 'user_status_label')}: <b>{status_line}</b>\n"
        f"{_t(user_id, 'user_iplimit_label')}: <code>{html.escape(limit_line)}</code>\n"
        f"{_t(user_id, 'user_secret_label')}: <code>{html.escape(secret)}</code>\n\n"
        f"<b>{_t(user_id, 'user_link_label')}:</b>\n<code>{link_line}</code>\n"
        f"{web_line}"
        f"{details}"
    )


def _qr_row(user_id: Optional[int], token: str) -> List[InlineKeyboardButton]:
    """QR обычной ссылки; при включённом WEB — рядом QR WEB-ссылки."""
    row = [InlineKeyboardButton(_t(user_id, "user_btn_qr"), callback_data=f"user_qr~{token}")]
    if get_web_link_for_secret("x"):
        row.append(InlineKeyboardButton(_t(user_id, "user_btn_webqr"), callback_data=f"user_webqr~{token}"))
    return row


async def cb_user_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = _uid(update)
    users = load_user_records()
    name = resolve_user_cb_key(query.data.removeprefix("user_view~"), users)
    record = users.get(name) if name else None
    if not record:
        await query.answer(_t(user_id, "user_not_found"), show_alert=True)
        await safe_edit_message(
            query,
            "❌ " + _t(user_id, "user_not_found"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_users")]]),
        )
        return
    await query.answer()
    enabled = bool(record.get("enabled"))
    secret = str(record.get("secret", ""))
    max_unique_ips = int(record.get("max_unique_ips") or 0)
    token = user_cb_key(name)

    if name == "main":
        buttons = [
            [InlineKeyboardButton(_t(user_id, "user_btn_main"), callback_data=f"user_view~{token}")],
            [InlineKeyboardButton(_t(user_id, "user_btn_iplimit"), callback_data=f"user_iplimit~{token}")],
            _qr_row(user_id, token),
            [InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_users")],
        ]
    else:
        buttons = [
            [InlineKeyboardButton(
                _t(user_id, "user_btn_disable") if enabled else _t(user_id, "user_btn_enable"),
                # В callback_data — то состояние, которое человек хотел получить,
                # нажимая эту кнопку. Иначе два апдейта на одну кнопку выключали
                # ключ и тут же включали обратно.
                callback_data=f"user_toggle~{token}~{'off' if enabled else 'on'}",
            )],
            [InlineKeyboardButton(_t(user_id, "user_btn_iplimit"), callback_data=f"user_iplimit~{token}")],
            _qr_row(user_id, token),
            [InlineKeyboardButton(_t(user_id, "user_btn_delete"), callback_data=f"user_del~{token}")],
            [InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_users")],
        ]
    await safe_edit_message(
        query,
        await _user_detail_text(name, secret, enabled, max_unique_ips, user_id),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cb_user_qr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = _uid(update)
    users = load_user_records()
    # Ровно один query.answer(): раньше первый пустой ответ съедал show_alert
    # и пользователь не видел «Ключ не найден».
    want_web = query.data.startswith("user_webqr~")
    name = resolve_user_cb_key(query.data.removeprefix("user_webqr~" if want_web else "user_qr~"), users)
    record = users.get(name) if name else None
    if not record:
        await query.answer(_t(user_id, "user_not_found"), show_alert=True)
        return
    await query.answer()
    token = user_cb_key(name)
    if want_web:
        link = get_web_link_for_secret(str(record.get("secret", "")))
    else:
        link = await get_proxy_link_for_secret(str(record.get("secret", "")))
    if not link:
        await safe_edit_message(
            query,
            "❌ " + _t(user_id, "user_link_unavailable"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data=f"user_view~{token}")]]),
        )
        return

    qr_file = f"/tmp/gotelegram_user_qr_{hashlib.sha256(name.encode()).hexdigest()[:10]}.png"
    code, _, _ = await sh("which", "qrencode")
    if code == 0:
        # -s 8 и подпись именем — как в экспорте: одиночный QR тоже сохраняют
        # и пересылают, а подпись сообщения при этом теряется.
        code, _, _ = await sh("qrencode", "-s", "8", "-o", qr_file, link)
        qr_add_label(qr_file, name)
    if code == 0 and os.path.exists(qr_file):
        try:
            with open(qr_file, "rb") as f:
                await query.message.reply_photo(
                    photo=f,
                    caption=f"<b>📷 QR: {html.escape(name)}</b>\n\n<code>{html.escape(link)}</code>",
                    parse_mode="HTML",
                )
        finally:
            try:
                os.remove(qr_file)
            except OSError:
                pass
    else:
        await safe_edit_message(
            query,
            f"<b>🔗 {html.escape(name)}</b>\n\n<code>{html.escape(link)}</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data=f"user_view~{token}")]]),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def cb_user_ip_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = _uid(update)
    users = load_user_records()
    name = resolve_user_cb_key(query.data.removeprefix("user_iplimit~"), users)
    record = users.get(name) if name else None
    if not record:
        await query.answer(_t(user_id, "user_not_found"), show_alert=True)
        return
    await query.answer()
    current = int(record.get("max_unique_ips") or 0)
    context.user_data["awaiting_user_ip_limit"] = name
    _arm_text_waiter(context)
    text = (
        f"<b>{_tf(user_id, 'user_iplimit_title', html.escape(name))}</b>\n\n"
        f"{_tf(user_id, 'user_iplimit_prompt', current)}"
    )
    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_cancel"), callback_data=f"user_view~{user_cb_key(name)}")]]),
        parse_mode="HTML",
    )


async def set_user_ip_limit_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_value: str, name: str) -> bool:
    """True — обработали (ожидание снять), False — ждём корректный ввод ещё раз."""
    user_id = update.effective_user.id
    try:
        limit = normalize_max_unique_ips(raw_value.strip())
    except ValueError as exc:
        await update.message.reply_text(f"❌ {html.escape(str(exc))}", parse_mode="HTML")
        return False
    # Никаких await к Telegram под межпроцессным локом — только работа с файлами.
    try:
        async with FileLock(USER_LOCK_FILE):
            # снимок счётчика перезагрузок ядра ДО записи — им подтверждается применение
            mark = await telemt_reload_mark()
            records = load_user_records()
            if name not in records:
                saved = None
            else:
                limits = load_user_max_unique_ips()
                # тот же лимит — конфиг не трогаем: ядру нечего применять
                limit_changed = limits.get(name, 0) != limit
                if limit > 0:
                    limits[name] = limit
                else:
                    limits.pop(name, None)
                saved = save_user_max_unique_ips(limits) if limit_changed else True
    except FileLockTimeout:
        await update.message.reply_text(_t(user_id, "user_config_busy"))
        return False          # конфиг занят — пусть повторит ввод
    if saved is None:
        await update.message.reply_text("❌ " + _t(user_id, "user_not_found"))
        return True           # ключа нет — повторный ввод не поможет
    if not saved:
        await update.message.reply_text(_t(user_id, "user_save_failed"))
        return True
    if limit_changed:
        await refresh_telemt_after_user_change("limit", before=mark)
    # Единицу сохранить нельзя — normalize_max_unique_ips отвергает её раньше.
    tail = ""
    await update.message.reply_text(
        _tf(user_id, "user_iplimit_saved", html.escape(name), limit) + tail,
        reply_markup=_users_keyboard(load_user_records(), user_id),
        parse_mode="HTML",
    )
    return True


async def cb_user_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = _uid(update)
    context.user_data["awaiting_user_name"] = True
    _arm_text_waiter(context)
    text = (
        f"<b>{_t(user_id, 'user_add_title')}</b>\n\n"
        f"{_t(user_id, 'user_add_prompt')}"
    )
    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_cancel"), callback_data="menu_users")]]),
        parse_mode="HTML",
    )


async def cb_user_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Направление берём из состояния, которое видел человек в момент нажатия
    # (оно в callback_data), а не «инвертировать текущее»: два апдейта на одну
    # кнопку выключали и тут же включали ключ обратно.
    query = update.callback_query
    user_id = _uid(update)
    records = load_user_records()
    raw = query.data.removeprefix("user_toggle~")
    want = ""
    if "~" in raw:
        raw, want = raw.rsplit("~", 1)
    name = resolve_user_cb_key(raw, records)
    if not name or name not in records:
        await query.answer(_t(user_id, "user_not_found"), show_alert=True)
        return
    if name == "main":
        await query.answer(_t(user_id, "user_toggle_main"), show_alert=True)
        return
    await query.answer()
    try:
        async with FileLock(USER_LOCK_FILE):
            # снимок счётчика перезагрузок ядра ДО записи — им подтверждается применение
            mark = await telemt_reload_mark()
            active = load_telemt_users()
            disabled = load_disabled_users()
            records = load_user_records()
            record = records.get(name)
            changed = False
            if not record:
                saved = None
                enabled = False
            else:
                # Старые сообщения без суффикса — прежнее поведение (инверсия).
                enabled = (want == "on") if want in ("on", "off") else not bool(record.get("enabled"))
                if bool(record.get("enabled")) == enabled:
                    # Уже в нужном состоянии: второй апдейт той же кнопки.
                    saved = True
                    secret = ""
                else:
                    changed = True
                    secret = str(record.get("secret", ""))
                    if enabled:
                        disabled.pop(name, None)
                        active[name] = secret
                        saved = save_telemt_users(active) and save_disabled_users(disabled)
                    else:
                        active.pop(name, None)
                        disabled[name] = secret
                        saved = save_disabled_users(disabled) and save_telemt_users(active)
    except FileLockTimeout:
        await safe_edit_message(query, _t(user_id, "user_config_busy"))
        return
    # Без клавиатуры экран становился тупиком — выйти можно было только /start.
    back = InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_users")]])
    if saved is None:
        await safe_edit_message(query, "❌ " + _t(user_id, "user_not_found"), reply_markup=back)
        return
    if not saved:
        await safe_edit_message(query, _t(user_id, "user_state_save_failed"), reply_markup=back)
        return
    if changed:
        # выключение — отзыв ключа; включение — новый ключ, его ядро обязано увидеть
        await refresh_telemt_after_user_change("add" if enabled else "revoke", [name] if enabled else [], before=mark)
    message = _tf(user_id, "user_enabled_msg" if enabled else "user_disabled_msg", html.escape(name))
    await safe_edit_message(
        query,
        message,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data=f"user_view~{user_cb_key(name)}")]]),
        parse_mode="HTML",
    )


async def cb_user_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = _uid(update)
    records = load_user_records()
    name = resolve_user_cb_key(query.data.removeprefix("user_del~"), records)
    if not name or name not in records:
        await query.answer(_t(user_id, "user_not_found"), show_alert=True)
        return
    if name == "main":
        await query.answer(_t(user_id, "user_del_main"), show_alert=True)
        return
    await query.answer()
    token = user_cb_key(name)
    text = _tf(user_id, "user_del_confirm", html.escape(name))
    buttons = [
        [InlineKeyboardButton(_t(user_id, "user_del_btn_yes"), callback_data=f"user_delok~{token}")],
        [InlineKeyboardButton(_t(user_id, "btn_cancel"), callback_data=f"user_view~{token}")],
    ]
    await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")


async def cb_user_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = _uid(update)
    records = load_user_records()
    name = resolve_user_cb_key(query.data.removeprefix("user_delok~"), records)
    # Имя сверяется с реальным списком ключей — токен-в-callback сам по себе
    # ничего не удаляет, а "main" не удаляется никогда.
    if not name or name not in records:
        await query.answer(_t(user_id, "user_not_found"), show_alert=True)
        return
    if name == "main":
        await query.answer(_t(user_id, "user_del_main"), show_alert=True)
        return
    await query.answer()
    try:
        async with FileLock(USER_LOCK_FILE):
            # снимок счётчика перезагрузок ядра ДО записи — им подтверждается применение
            mark = await telemt_reload_mark()
            active = load_telemt_users()
            disabled = load_disabled_users()
            records = load_user_records()
            was_active = name in active
            had_limit = False
            if name == "main" or name not in records:
                saved = None
            else:
                active.pop(name, None)
                disabled.pop(name, None)
                limits = load_user_max_unique_ips()
                had_limit = limits.pop(name, None) is not None
                saved = save_telemt_users(active) and save_disabled_users(disabled) and save_user_max_unique_ips(limits)
    except FileLockTimeout:
        await safe_edit_message(query, _t(user_id, "user_config_busy"),
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_users")]]))
        return
    if saved is None:
        await safe_edit_message(query, "❌ " + _t(user_id, "user_not_found"))
        return
    if not saved:
        await safe_edit_message(query, _t(user_id, "user_save_failed"))
        return
    # ключ из disabled_users.json ядру не известен — отзывать в нём нечего,
    # а без него и без лимита конфиг ядра не изменился вовсе
    if was_active:
        await refresh_telemt_after_user_change("revoke", before=mark)
    elif had_limit:
        await refresh_telemt_after_user_change("limit", before=mark)
    await safe_edit_message(
        query,
        _tf(user_id, "user_deleted", html.escape(name)),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_users")]]),
        parse_mode="HTML",
    )


async def create_user_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str) -> bool:
    """True — обработали (ожидание снять), False — ждём корректный ввод ещё раз."""
    user_id = update.effective_user.id
    if not _USER_NAME_RE.match(name):
        await update.message.reply_text(_t(user_id, "user_add_bad_name"))
        return False
    try:
        async with FileLock(USER_LOCK_FILE):
            # снимок счётчика перезагрузок ядра ДО записи — им подтверждается применение
            mark = await telemt_reload_mark()
            records = load_user_records()
            if name in records:
                saved = None
                secret = ""
            else:
                users = load_telemt_users()
                secret = hashlib.sha256(f"{name}:{time.time()}:{os.urandom(16).hex()}".encode()).hexdigest()[:32]
                users[name] = secret
                saved = save_telemt_users(users)
    except FileLockTimeout:
        await update.message.reply_text(_t(user_id, "user_config_busy"))
        return False
    if saved is None:
        await update.message.reply_text(_t(user_id, "user_exists"))
        return False
    if not saved:
        await update.message.reply_text(_t(user_id, "user_save_failed"))
        return False
    await refresh_telemt_after_user_change("add", [name], before=mark)
    link = await get_proxy_link_for_secret(secret)
    await update.message.reply_text(
        f"<b>{_t(user_id, 'user_created')}</b>\n\n"
        f"{_t(user_id, 'user_created_name')}: <code>{html.escape(name)}</code>\n"
        f"{_t(user_id, 'user_secret_label')}: <code>{secret}</code>\n\n"
        f"<code>{html.escape(link or '')}</code>",
        reply_markup=_users_keyboard(load_user_records(), user_id),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return True



# ============================================================================
# ANTI-DPI / QUALITY
# ============================================================================


async def cb_menu_antidpi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Экран «Режим работы» — один в один с SSH-меню.

    Раньше здесь были отдельно кнопки версий фикса и отдельно кнопки режимов,
    и человек, переключившись в боте, видел в SSH другое. Теперь один список из
    шести строк в одном порядке: сверху приоритетнее и стабильнее.
    """
    query = update.callback_query
    await query.answer()
    await _render_antidpi_menu(update, context)


async def _render_antidpi_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отрисовка меню без query.answer — как _render_warp_menu: после долгой
    операции повторный answer на тот же callback падает «Query is too old»."""
    query = update.callback_query
    user_id = _uid(update)

    fix = await run_bot_action("mekofix-status")
    pre = await run_bot_action("preset-status")
    qua = await run_bot_action("quality")

    ver = str(fix.get("version") or "")
    limited = bool(fix.get("limited"))
    on = str(fix.get("state")) == "on"
    if on and ver == "v4":
        cur, sel = _t(user_id, "adpi_v4"), "v4"
    elif on and ver in ("v3", "v2"):
        cur = _t(user_id, "adpi_v3_lim" if limited else "adpi_v3_free")
        sel = "v3lim" if limited else "v3free"
    else:
        pid = str(pre.get("current", "4"))
        cur = _t(user_id, {"1": "adpi_p1", "2": "adpi_p2"}.get(pid, "adpi_p4"))
        sel = "p" + pid

    losses = " · ".join(
        f"{lbl} {qua.get(k, '-')}%" if str(qua.get(k, "-")) not in ("-", "None") else f"{lbl} —"
        for lbl, k in (("1м", "loss_1m"), ("5м", "loss_5m"), ("1ч", "loss_1h"), ("1д", "loss_1d"))
    )

    def mark(key: str, label: str) -> str:
        return ("✅ " if sel == key else "") + label

    text = (
        f"<b>{_t(user_id, 'adpi_title')}</b>\n\n"
        f"{_t(user_id, 'adpi_now')}: <b>{html.escape(cur)}</b>\n\n"
        f"<i>{_t(user_id, 'adpi_order')}</i>\n\n"
        f"📉 {_t(user_id, 'adpi_loss')}: {html.escape(losses)}"
    )
    rows = [
        [InlineKeyboardButton(mark("v3free", _t(user_id, "adpi_v3_free")), callback_data="adpi_set~v3free")],
        [InlineKeyboardButton(mark("v3lim", _t(user_id, "adpi_v3_lim")), callback_data="adpi_set~v3lim")],
        [InlineKeyboardButton(mark("v4", _t(user_id, "adpi_v4")), callback_data="adpi_set~v4")],
        [InlineKeyboardButton(mark("p1", _t(user_id, "adpi_p1")), callback_data="adpi_set~p1"),
         InlineKeyboardButton(mark("p2", _t(user_id, "adpi_p2")), callback_data="adpi_set~p2")],
        [InlineKeyboardButton(mark("p4", _t(user_id, "adpi_p4")), callback_data="adpi_set~p4")],
        [InlineKeyboardButton(_t(user_id, "btn_refresh"), callback_data="menu_antidpi"),
         InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_main")],
    ]
    await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")


async def cb_antidpi_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Применение выбранной строки. Пункты 1-3 ставят фикс, 4-6 — только режим."""
    query = update.callback_query
    user_id = _uid(update)
    choice = (query.data or "").split("~", 1)[-1]
    if choice == "v4":
        # SSH перед экспериментальным V4 предупреждает и спрашивает — бот обязан так же,
        # иначе V4 включается случайным тапом (расхождение интерфейсов, §4 ТЗ)
        await query.answer()
        text = (
            f"<b>{_t(user_id, 'adpi_v4')}</b>\n\n"
            f"⚠️ {_t(user_id, 'adpi_v4_warn', default='V4 is marked experimental by its own author. Verify iOS, Android and desktop.')}\n\n"
            f"{_t(user_id, 'adpi_v4_confirm', default='Enable V4?')}"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(_t(user_id, "btn_yes"), callback_data="adpi_set~v4yes"),
             InlineKeyboardButton(_t(user_id, "btn_no"), callback_data="menu_antidpi")],
        ])
        await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")
        return
    if choice == "v4yes":
        # сюда попадаем только кнопкой «Да» с экрана подтверждения выше
        choice = "v4"
    await query.answer(_t(user_id, "adpi_applying"))
    if await _action_busy(query, user_id):
        return
    # прогресс правкой сообщения: тост живёт секунды, а применение V4 — минуты
    await safe_edit_message(query, "⏳ " + _t(user_id, "adpi_applying"))
    async with _BOT_ACTION_LOCK:
        if choice == "v3free":
            r = await run_bot_action("mekofix-apply", timeout=180, version="v3", limited="0")
        elif choice == "v3lim":
            r = await run_bot_action("mekofix-apply", timeout=180, version="v3", limited="1")
        elif choice == "v4":
            r = await run_bot_action("mekofix-apply", timeout=300, version="v4")
        elif choice.startswith("p"):
            # снять внешний фикс, иначе останется от прошлого выбора
            await run_bot_action("mekofix-off", timeout=120)
            r = await run_bot_action("preset-apply", timeout=180, preset=choice[1:])
        else:
            r = {"status": "error", "message": "unknown choice"}
    ok = r.get("status") == "ok"
    # результат отдельным сообщением: второй query.answer на тот же callback после
    # долгой операции падал «Query is too old», и успешный V4 выглядел ошибкой
    try:
        await query.message.reply_text(("✅ " if ok else "⚠️ ") + str(r.get("message", ""))[:300])
    except Exception as e:
        logger.debug("antidpi result notify: %s", e)
    await _render_antidpi_menu(update, context)


# ============================================================================
# RESTART & LOGS
# ============================================================================


async def cb_menu_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart service (мутирующее действие — под общим локом)."""
    query = update.callback_query
    user_id = _uid(update)
    await query.answer()
    if await _action_busy(query, user_id):
        return

    async with _BOT_ACTION_LOCK:
        await safe_edit_message(query, _t(user_id, "restart_progress"))
        code, _, stderr = await sh("systemctl", "restart", TELEMT_SERVICE, timeout=90)

    if code == 0:
        text = _t(user_id, "restart_ok")
    else:
        text = f"{_t(user_id, 'restart_fail')}\n<code>{html.escape(stderr[:500])}</code>"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="sec_service")]]
    )
    await safe_edit_message(query,
        text, reply_markup=keyboard, parse_mode="HTML"
    )


async def cb_menu_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent logs."""
    query = update.callback_query
    await query.answer()

    code, stdout, _ = await sh(
        "journalctl", "-u", TELEMT_SERVICE, "-n", "30", "--no-pager"
    )

    if code == 0:
        log_text = stdout[-1000:] if len(stdout) > 1000 else stdout
        text = f"<b>📋 Recent Logs</b>\n\n<pre>{html.escape(log_text)}</pre>"
    else:
        text = "❌ Failed to retrieve logs"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="sec_state")]]
    )
    await safe_edit_message(query,text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================================
# BACKUP & RESTORE
# ============================================================================

def count_backups() -> int:
    """Сколько всего архивов в каталоге — без обрезки по лимиту показа."""
    try:
        return len([f for f in os.listdir(BACKUP_DIR)
                    if f.endswith((".tar.gz", ".tar.gz.enc")) and not f.endswith(".sha256")])
    except OSError:
        return 0


def list_backup_names(limit: int = 10) -> List[str]:
    try:
        if not os.path.exists(BACKUP_DIR):
            return []
        names = []
        for f in os.listdir(BACKUP_DIR):
            if not f.endswith((".tar.gz", ".tar.gz.enc")) or f.endswith(".sha256"):
                continue
            try:
                # Пустой файл — заведомо обрывок прерванного бэкапа.
                if os.path.getsize(os.path.join(BACKUP_DIR, f)) <= 0:
                    continue
            except OSError:
                continue
            names.append(f)
        # По времени файла, а не по имени: лексикографически «2.7.9» больше
        # «2.10.40», и список из десяти показывал самые СТАРЫЕ архивы — свежий
        # бэкап нельзя было восстановить из бота вовсе.
        def _mtime(name: str) -> float:
            try:
                return os.path.getmtime(os.path.join(BACKUP_DIR, name))
            except OSError:
                return 0.0
        return sorted(names, key=_mtime, reverse=True)[:limit]
    except Exception:
        return []


def safe_backup_path(name: str) -> Optional[str]:
    raw = os.path.basename(str(name or "").strip())
    if raw != name or not raw.endswith((".tar.gz", ".tar.gz.enc")) or raw.endswith(".sha256"):
        return None
    path = os.path.abspath(os.path.join(BACKUP_DIR, raw))
    base = os.path.abspath(BACKUP_DIR)
    if os.path.dirname(path) != base or not os.path.exists(path):
        return None
    return path


def backup_schedule_state() -> Dict[str, Any]:
    raw = load_json(BACKUP_SCHEDULE_FILE) or {}
    if not isinstance(raw, dict):
        raw = {}
    frequency = str(raw.get("frequency") or "off")
    if frequency not in {"off", "daily", "weekly", "monthly"}:
        frequency = "off"
    return {
        "frequency": frequency,
        "calendar": raw.get("calendar") or "",
        "updated_at": raw.get("updated_at") or "",
    }


def _backup_dir_snapshot() -> set:
    try:
        return set(os.listdir(BACKUP_DIR))
    except OSError:
        return set()


async def verify_backup_archive(path: str) -> bool:
    """Проверить целостность архива через `tar -tzf` (обрезанный не пройдёт)."""
    if not path or not os.path.exists(path):
        return False
    if path.endswith(".enc"):
        return True  # зашифрованный архив проверяет CLI при восстановлении
    code, _, _ = await sh("tar", "-tzf", path, timeout=300)
    return code == 0


async def run_full_backup() -> Tuple[bool, str]:
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
    before = _backup_dir_snapshot()
    # 240 с не хватало на большой сайт: по таймауту процесс убивался, а
    # обрезанный .tar.gz оставался в списке и честно предлагался к
    # восстановлению. Теперь таймаут 900 с, а обрывки удаляются.
    code, stdout, stderr = await sh("bash", "-lc", script, timeout=900)
    message = (stdout.strip().splitlines()[-1:] or stderr.strip().splitlines()[-1:] or [""])[0]
    created = [
        name for name in sorted(_backup_dir_snapshot() - before)
        if name.endswith((".tar.gz", ".tar.gz.enc"))
    ]

    if code != 0:
        for name in created:
            try:
                os.remove(os.path.join(BACKUP_DIR, name))
                logger.warning("run_full_backup: removed partial archive %s", name)
            except OSError as e:
                logger.error("run_full_backup: cannot remove partial %s: %s", name, e)
        return False, message or (stderr or "").strip()[-300:]

    for name in created:
        path = os.path.join(BACKUP_DIR, name)
        if not await verify_backup_archive(path):
            try:
                os.remove(path)
            except OSError:
                pass
            logger.error("run_full_backup: archive %s failed tar -tzf, removed", name)
            return False, f"archive {name} is corrupt and was removed"

    return True, message


async def set_full_backup_schedule(frequency: str) -> Tuple[bool, str]:
    if frequency not in {"off", "daily", "weekly", "monthly"}:
        return False, "unsupported schedule"
    script = (
        "source /opt/gotelegram/lib/common.sh; "
        "source /opt/gotelegram/lib/i18n.sh; "
        "source /opt/gotelegram/lib/backup.sh; "
        "load_language \"$(detect_language 2>/dev/null || echo en)\"; "
        f"set_backup_schedule {shlex.quote(frequency)}"
    )
    code, stdout, stderr = await sh("bash", "-lc", script, timeout=120)
    message = (stdout.strip().splitlines()[-1:] or stderr.strip().splitlines()[-1:] or [""])[0]
    return code == 0, message


# Полное восстановление уходит в фоновый процесс и живёт минутами — лока на
# время запуска мало, поэтому дополнительно защищаемся отметкой времени.
_LAST_RESTORE_LAUNCH = 0.0
RESTORE_COOLDOWN_SECONDS = 300.0


async def launch_full_restore(backup_path: str) -> None:
    global _LAST_RESTORE_LAUNCH
    quoted_path = shlex.quote(backup_path)
    script = (
        "sleep 1; "
        "source /opt/gotelegram/lib/common.sh; "
        "source /opt/gotelegram/lib/i18n.sh; "
        "source /opt/gotelegram/lib/telemt.sh; "
        "source /opt/gotelegram/lib/website.sh; "
        "source /opt/gotelegram/lib/backup.sh; "
        "load_language \"$(detect_language 2>/dev/null || echo en)\"; "
        "create_backup \"\" >/dev/null 2>&1 || true; "
        f"restore_backup {quoted_path} \"\" yes; "
        "cleanup_old_backups 30"
    )
    _LAST_RESTORE_LAUNCH = time.monotonic()
    await asyncio.create_subprocess_exec(
        "bash",
        "-lc",
        script,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def restore_recently_launched() -> bool:
    return (
        _LAST_RESTORE_LAUNCH > 0
        and (time.monotonic() - _LAST_RESTORE_LAUNCH) < RESTORE_COOLDOWN_SECONDS
    )


async def cb_menu_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Backup menu."""
    query = update.callback_query
    await query.answer()
    user_id = _uid(update)
    backups = list_backup_names()
    schedule = backup_schedule_state()
    frequency = schedule["frequency"]
    freq_label = _t(user_id, f"backup_freq_{frequency}")

    buttons = [
        [InlineKeyboardButton(_t(user_id, "backup_btn_create"), callback_data="backup_create")],
        [
            InlineKeyboardButton(_t(user_id, "backup_btn_off"), callback_data="backup_schedule_off"),
            InlineKeyboardButton(_t(user_id, "backup_btn_daily"), callback_data="backup_schedule_daily"),
        ],
        [
            InlineKeyboardButton(_t(user_id, "backup_btn_weekly"), callback_data="backup_schedule_weekly"),
            InlineKeyboardButton(_t(user_id, "backup_btn_monthly"), callback_data="backup_schedule_monthly"),
        ],
    ]

    if backups:
        buttons.append([InlineKeyboardButton(_t(user_id, "backup_btn_list"), callback_data="backup_list")])
        buttons.append([InlineKeyboardButton(_t(user_id, "backup_btn_restore"), callback_data="menu_restore")])

    buttons.append([InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="sec_service")])

    text = (
        f"<b>{_t(user_id, 'backup_title')}</b>\n\n"
        f"{_tf(user_id, 'backup_files_count', len(backups))}\n"
        f"{_tf(user_id, 'backup_schedule_line', freq_label)}\n\n"
        f"{_t(user_id, 'backup_contents')}"
    )
    keyboard = InlineKeyboardMarkup(buttons)
    await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")


async def cb_backup_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create backup (мутирующее действие — под общим локом)."""
    query = update.callback_query
    await query.answer()
    user_id = _uid(update)
    if await _action_busy(query, user_id):
        return

    await safe_edit_message(query, _t(user_id, "backup_creating"))

    async with _BOT_ACTION_LOCK:
        ok, message = await run_full_backup()

    if ok:
        text = _tf(user_id, "backup_created_fmt", f"<code>{html.escape(message)}</code>")
    else:
        text = f"{_t(user_id, 'backup_failed')}\n<code>{html.escape(message[:500])}</code>"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_backup")]]
    )
    await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")


async def cb_backup_schedule_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = _uid(update)
    if await _action_busy(query, user_id):
        return
    frequency = query.data.removeprefix("backup_schedule_")
    await safe_edit_message(query, _t(user_id, "backup_schedule_saving"))

    async with _BOT_ACTION_LOCK:
        ok, message = await set_full_backup_schedule(frequency)

    if ok:
        text = _t(user_id, "backup_schedule_saved")
    else:
        text = f"{_t(user_id, 'backup_schedule_failed')}\n<code>{html.escape(message[:500])}</code>"
    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_backup")]]),
        parse_mode="HTML",
    )


async def cb_backup_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List backups."""
    query = update.callback_query
    await query.answer()
    user_id = _uid(update)

    backups = list_backup_names()

    if not backups:
        text = _t(user_id, "backup_none")
    else:
        text = f"<b>{_t(user_id, 'backup_list_title')}</b>\n\n"
        for backup in backups[:10]:
            path = os.path.join(BACKUP_DIR, backup)
            try:
                size = os.path.getsize(path) / (1024 * 1024)
            except OSError:
                continue
            text += f"<code>{html.escape(backup)}</code> ({size:.2f} MB)\n"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_backup")]]
    )
    await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")


async def cb_menu_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restore menu."""
    query = update.callback_query
    await query.answer()
    user_id = _uid(update)

    backups = list_backup_names()

    if not backups:
        text = _t(user_id, "backup_none")
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="sec_service")]]
        )
    else:
        text = f"{_t(user_id, 'backup_restore_title')}\n\n{_t(user_id, 'restore_pick')}"
        buttons = []
        for i, backup in enumerate(backups[:10]):
            buttons.append(
                [
                    InlineKeyboardButton(
                        backup, callback_data=f"restore_idx_{i}"
                    )
                ]
            )
        buttons.append([InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="sec_service")])
        keyboard = InlineKeyboardMarkup(buttons)
        # Store backup list in user_data for retrieval
        context.user_data["backup_list"] = backups[:10]

    await safe_edit_message(query, text, reply_markup=keyboard)


async def cb_restore_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm or execute backup restoration."""
    query = update.callback_query
    data = query.data
    user_id = _uid(update)

    try:
        if data.startswith("restore_yes_"):
            idx = int(data.removeprefix("restore_yes_"))
            confirmed = True
        else:
            idx = int(data.removeprefix("restore_idx_"))
            confirmed = False
    except ValueError:
        await query.answer("Invalid backup selection", show_alert=True)
        return

    backup_list = context.user_data.get("backup_list", [])
    if idx < 0 or idx >= len(backup_list):
        await query.answer(_t(user_id, "restore_file_missing"), show_alert=True)
        return

    backup_name = backup_list[idx]
    await query.answer()

    back_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_restore")]]
    )

    safe_path = safe_backup_path(backup_name)
    if not safe_path:
        await safe_edit_message(query, _t(user_id, "restore_file_missing"), reply_markup=back_keyboard)
        return
    if safe_path.endswith(".enc"):
        await safe_edit_message(query, _t(user_id, "restore_encrypted_cli"), reply_markup=back_keyboard)
        return

    if not confirmed:
        # Проверяем архив ДО того, как предложить восстановление: обрезанный
        # бэкап (например от прерванного создания) не должен попадать в
        # разрушительный сценарий.
        await safe_edit_message(query, _t(user_id, "restore_verifying"))
        if not await verify_backup_archive(safe_path):
            await safe_edit_message(
                query,
                _tf(user_id, "restore_corrupt", html.escape(backup_name)),
                reply_markup=back_keyboard,
                parse_mode="HTML",
            )
            return
        text = _tf(user_id, "restore_confirm_fmt", html.escape(backup_name))
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(_t(user_id, "restore_btn_yes"), callback_data=f"restore_yes_{idx}")],
            [InlineKeyboardButton(_t(user_id, "btn_cancel"), callback_data="menu_restore")],
        ])
        await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if restore_recently_launched():
        await safe_edit_message(query, _t(user_id, "restore_already_running"), reply_markup=back_keyboard)
        return
    if await _action_busy(query, user_id):
        return

    async with _BOT_ACTION_LOCK:
        if restore_recently_launched():
            await safe_edit_message(query, _t(user_id, "restore_already_running"), reply_markup=back_keyboard)
            return
        await safe_edit_message(query, _tf(user_id, "restore_starting", html.escape(backup_name)), parse_mode="HTML")
        await launch_full_restore(safe_path)

    text = _tf(user_id, "restore_started_fmt", html.escape(backup_name))
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_main")]]
    )
    await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================================
# UPDATE & MODE/TEMPLATE CHANGE
# ============================================================================


async def cb_menu_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update telemt by re-running the install script's update logic."""
    query = update.callback_query
    await query.answer()

    await safe_edit_message(query, f"<b>{_t(_uid(update), 'update_title')}</b>\n\n⏳ Checking for telemt updates...")

    current = await get_telemt_version()

    # Check latest release from GitHub
    code, stdout, stderr = await sh(
        "curl", "-s", "--max-time", "10",
        "https://api.github.com/repos/telemt/telemt/releases/latest",
    )

    if code != 0 or not stdout.strip():
        text = "❌ Failed to check for updates"
    else:
        try:
            release = json.loads(stdout)
            latest = str(release.get("tag_name") or "")
            # рейт-лимит GitHub отвечает JSON-ом без tag_name: это «не удалось
            # проверить», а не «доступно обновление до unknown»
            if not latest:
                text = "❌ Failed to check for updates"
            elif latest.lstrip("v") == current.lstrip("v"):
                text = f"✅ telemt is already up to date ({html.escape(current)})"
            else:
                text = (
                    f"ℹ️ Update available: {html.escape(current)} → {html.escape(latest)}\n\n"
                    f"{_t(_uid(update), 'update_via_cli')}"
                )
        except json.JSONDecodeError:
            text = "❌ Failed to parse release info"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="sec_service")]]
    )
    await safe_edit_message(query,text, reply_markup=keyboard, parse_mode="HTML")


async def cb_menu_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Change mode or template."""
    query = update.callback_query
    await query.answer()

    user_id = _uid(update)
    buttons = [
        [InlineKeyboardButton(_t(user_id, "change_to_lite"), callback_data="change_lite")],
        [InlineKeyboardButton(_t(user_id, "change_to_pro"), callback_data="change_pro")],
        [InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="sub_site")],
    ]
    keyboard = InlineKeyboardMarkup(buttons)
    await safe_edit_message(query,
        _t(user_id, "change_title"), reply_markup=keyboard
    )


async def cb_change_lite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch to lite mode — show domain selection."""
    query = update.callback_query
    await query.answer()
    # Reuse the lite mode domain selection flow
    await cb_install_mode_lite(update, context)


async def cb_change_pro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch to pro mode — show template categories."""
    query = update.callback_query
    await query.answer()
    # Reuse the pro mode template selection flow
    await cb_install_mode_pro(update, context)


async def cb_install_migrate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Migrate from v1 (mtg Docker) to v2 (telemt)."""
    query = update.callback_query
    user_id = _uid(update)
    await query.answer()
    if await _action_busy(query, user_id):
        return

    await safe_edit_message(query,"⏳ Migrating from v1...")

    async with _BOT_ACTION_LOCK:
        # Stop old mtg container
        code, _, stderr = await sh("docker", "stop", "mtproto-proxy", timeout=30)
        if code != 0:
            code, _, stderr = await sh("docker", "stop", "mtg", timeout=30)

        # Remove old container
        await sh("docker", "rm", "mtproto-proxy", timeout=15)
        await sh("docker", "rm", "mtg", timeout=15)

    text = (
        "✅ <b>v1 container stopped and removed</b>\n\n"
        "Now select installation mode for v2:"
    )
    keyboard = get_install_mode_menu(_uid(update))
    await safe_edit_message(query,text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================================
# WEBSITE & SSL
# ============================================================================


async def _website_only_pro_guard(query, user_id: Optional[int]) -> bool:
    """True + отказ, если режим не pro: SSH в Lite отказывает (website_only_pro),
    а бот молча выполнял certbot renew, который без сертификатов даёт rc=0 и «✅»."""
    config = load_json(GOTELEGRAM_CONFIG) or {}
    if config.get("mode") == "pro":
        return False
    await safe_edit_message(
        query,
        _t(user_id, "website_only_pro", default="Website management is available in pro mode only"),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_main")]]
        ),
    )
    return True


async def cb_menu_website(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Website and SSL management."""
    query = update.callback_query
    await query.answer()
    if await _website_only_pro_guard(query, _uid(update)):
        return

    buttons = [
        [InlineKeyboardButton("🔄 Renew SSL Certificate", callback_data="ssl_renew")],
        [InlineKeyboardButton("📊 SSL Status", callback_data="ssl_status")],
        [InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="sub_site")],
    ]
    keyboard = InlineKeyboardMarkup(buttons)
    await safe_edit_message(query,
        _t(_uid(update), "website_title"), reply_markup=keyboard
    )


async def cb_ssl_renew(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Renew SSL certificate (мутирующее действие — под общим локом)."""
    query = update.callback_query
    user_id = _uid(update)
    await query.answer()
    # прямой callback достижим из старых сообщений — guard нужен и здесь, не только в подменю
    if await _website_only_pro_guard(query, user_id):
        return
    if await _action_busy(query, user_id):
        return

    await safe_edit_message(query, _t(user_id, "ssl_renew_progress"))

    async with _BOT_ACTION_LOCK:
        code, stdout, stderr = await sh("certbot", "renew", timeout=300)

    if code == 0:
        text = _t(user_id, "ssl_renewed")
    else:
        text = f"{_t(user_id, 'ssl_renew_fail')}\n<code>{html.escape(stderr[:500])}</code>"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_website")]]
    )
    await safe_edit_message(query,text, reply_markup=keyboard, parse_mode="HTML")


async def cb_ssl_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show SSL status."""
    query = update.callback_query
    await query.answer()
    # тот же guard: в Lite сертификатов нет, «статус SSL» вводил бы в заблуждение
    if await _website_only_pro_guard(query, _uid(update)):
        return

    code, stdout, _ = await sh("certbot", "certificates")

    if code == 0:
        text = f"<b>{_t(_uid(update), 'ssl_status_title')}</b>\n\n<pre>{html.escape(stdout[:1000])}</pre>"
    else:
        text = "❌ Failed to get SSL status"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="menu_website")]]
    )
    await safe_edit_message(query,text, reply_markup=keyboard, parse_mode="HTML")


async def admin_web_host_hint() -> str:
    config = load_json(GOTELEGRAM_CONFIG) or {}
    domain = str(config.get("domain") or "")
    if domain:
        return domain
    code, stdout, _ = await sh("curl", "-s", "-4", "--max-time", "5", "https://api.ipify.org", timeout=7)
    return stdout.strip() if code == 0 and stdout.strip() else "SERVER_IP"


# ── Мини-приложение ─────────────────────────────────────────────────────────
#
# Пункт существовал только в SSH (Управление → 8), а экран Web Admin при этом
# советовал «включить мини-приложение в меню бота» — то есть отправлял туда,
# чего в боте не было. Отсюда и вопрос «почему нет ссылки на панель»: ссылки
# нет, пока мини-апп выключен, а выключен он потому, что включить его из бота
# было нельзя.
#
# Набор действий совпадает с SSH до пункта: включить, выключить, сменить адрес.
async def _render_miniapp_menu(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               note: str = "") -> None:
    query = update.callback_query
    user_id = _uid(update)
    st = await run_bot_action("miniapp-status", timeout=40)
    url = str(st.get("url") or "").strip()
    blocker = str(st.get("blocker") or "").strip()

    lines = [f"<b>{_t(user_id, 'ma_title')}</b>", ""]
    if url:
        # Адрес — главное на экране, поэтому он идёт сразу под состоянием и
        # <code>: так его можно взять одним касанием, не выделяя руками.
        lines += [f"{_t(user_id, 'ma_state')}: <b>{_t(user_id, 'ma_on')}</b>", "",
                  f"<code>{html.escape(url)}</code>", "",
                  _t(user_id, "ma_howto"), "",
                  _t(user_id, "ma_hint")]
    else:
        lines += [f"{_t(user_id, 'ma_state')}: {_t(user_id, 'ma_off')}", "",
                  _t(user_id, "ma_security")]
    if blocker:
        # Причину показываем сразу, а не после нажатия: иначе кнопка выглядит
        # сломанной, хотя не хватает Pro-режима или установленного бота.
        lines += ["", "⚠️ " + _t(user_id, f"ma_{blocker}")]
    if note:
        lines += ["", note]

    rows = []
    if url:
        rows.append([InlineKeyboardButton(_t(user_id, "btn_miniapp"), web_app=WebAppInfo(url=url))])
        rows.append([
            InlineKeyboardButton(_t(user_id, "ma_opt_rotate"), callback_data="ma_rotate"),
            InlineKeyboardButton(_t(user_id, "ma_opt_off"), callback_data="ma_off"),
        ])
    elif not blocker:
        rows.append([InlineKeyboardButton(_t(user_id, "ma_opt_on"), callback_data="ma_on")])
    rows.append([InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="sub_panel")])

    await safe_edit_message(query, "\n".join(lines),
                            reply_markup=InlineKeyboardMarkup(rows),
                            parse_mode="HTML", disable_web_page_preview=True)


async def cb_menu_miniapp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not await require_auth(update, context):
        return
    await _render_miniapp_menu(update, context)


async def cb_miniapp_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Включить / выключить / сменить адрес. Смена адреса — с подтверждением."""
    query = update.callback_query
    user_id = _uid(update)
    what = (query.data or "").removeprefix("ma_")

    if what == "rotate":
        # Смена адреса ломает уже разосланную ссылку — спрашиваем, как SSH
        # спрашивает перед необратимым.
        await query.answer()
        await safe_edit_message(
            query,
            f"<b>{_t(user_id, 'ma_opt_rotate')}</b>\n\n⚠️ {_t(user_id, 'ma_rotate_warn')}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(_t(user_id, "btn_yes"), callback_data="ma_rotateyes"),
                InlineKeyboardButton(_t(user_id, "btn_no"), callback_data="menu_miniapp"),
            ]]), parse_mode="HTML")
        return
    if what == "rotateyes":
        what = "rotate"

    await query.answer(_t(user_id, "ma_applying"))
    if await _action_busy(query, user_id):
        return
    await safe_edit_message(query, "⏳ " + _t(user_id, "ma_applying"))
    async with _BOT_ACTION_LOCK:
        r = await run_bot_action(f"miniapp-{what}", timeout=180)
    # Кнопка меню Telegram зависит от состояния мини-аппа. Раньше её обновлял
    # перезапуск бота; изнутри бота так нельзя — переставляем сами.
    try:
        await refresh_menu_button(context.bot)
    except Exception as exc:
        logger.warning("menu button after miniapp-%s: %s", what, exc)
    ok = r.get("status") == "ok"
    note = ("✅ " if ok else "⚠️ ") + str(r.get("message") or "")[:200]
    await _render_miniapp_menu(update, context, note=note)


async def cb_menu_admin_web(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = _uid(update)
    running = await check_service_status(ADMIN_WEB_SERVICE)
    host = await admin_web_host_hint()
    local_url = f"http://127.0.0.1:{ADMIN_WEB_PORT}/"
    ssh_cmd = f"ssh -L {ADMIN_WEB_PORT}:127.0.0.1:{ADMIN_WEB_PORT} root@{host}"

    # Заключительная строка ВРАЛА: тут стояло «не публикуется наружу» без единой
    # проверки. Стоит включить мини-апп — и nginx начинает проксировать домен на
    # этот же порт, то есть панель доступна из интернета по секретному пути, а
    # сообщение продолжало уверять в обратном.
    #
    # Различать надо две разные вещи, которые прежняя фраза смешивала:
    #   • сокет действительно слушает только 127.0.0.1 — это правда всегда;
    #   • доступность снаружи зависит от того, включён ли мини-апп.
    # Туннель остаётся рабочим путём в обоих случаях: он не зависит ни от
    # публикации, ни от бота, и выручает, когда всё остальное недоступно.
    public_url = miniapp_url()
    # Готовность входа по коду спрашиваем у сервера, а не выводим из того, что
    # мы — бот: код шлёт ПАНЕЛЬ, читая .env бота, и она может его не прочитать.
    _st = await run_bot_action("miniapp-status", timeout=40)
    tfa_ok = str(_st.get("tfa") or "").strip() == "on"
    if public_url:
        _t2_ru = ("✅ Бот привязан — вход по коду работает."
                  if tfa_ok else
                  "⚠️ Вход по коду не сработает: панель не видит привязанного бота "
                  "с админами. Останутся мини-апп и туннель.")
        _t2_en = ("✅ The bot is linked — code sign-in works."
                  if tfa_ok else
                  "⚠️ Code sign-in will not work: the panel sees no linked bot with admins. "
                  "The mini app and the tunnel still work.")
        tail_ru = (
            "<b>С домена — в обычном браузере</b>\n"
            f"<code>{html.escape(public_url)}</code>\n"
            "Откройте адрес → «Прислать код» → код придёт в этот чат.\n"
            f"{_t2_ru}\n"
            "Адрес секретный — не публикуйте его.\n\n"
            f"Сам сокет слушает только <code>127.0.0.1:{ADMIN_WEB_PORT}</code>, "
            "наружу его отдаёт nginx по секретному пути. Туннель выше работает и без этого."
        )
        tail_en = (
            "<b>From the domain — in any browser</b>\n"
            f"<code>{html.escape(public_url)}</code>\n"
            "Open the address → “Send code” → the code arrives in this chat.\n"
            f"{_t2_en}\n"
            "The path is secret — do not share it.\n\n"
            f"The socket itself listens on <code>127.0.0.1:{ADMIN_WEB_PORT}</code> only; "
            "nginx exposes it at that secret path. The tunnel above works without it."
        )
    else:
        tail_ru = (
            f"Админка слушает только <code>127.0.0.1:{ADMIN_WEB_PORT}</code> и наружу не отдаётся — "
            "поэтому ссылки на вход здесь и нет.\n"
            "Чтобы открыть её в обычном браузере по секретному адресу: "
            f"<b>{_t(user_id, 'sub_panel')} → {_t(user_id, 'ma_title')}</b>.\n"
            + ("Вход будет по коду в этот чат — бот привязан." if tfa_ok else
               "⚠️ Учтите: вход по коду требует привязанного бота с админами, "
               "сейчас панель его не видит.")
        )
        tail_en = (
            f"The admin listens on <code>127.0.0.1:{ADMIN_WEB_PORT}</code> only and is not exposed — "
            "which is why there is no sign-in link here.\n"
            "To open it in any browser at a secret path: "
            f"<b>{_t(user_id, 'sub_panel')} → {_t(user_id, 'ma_title')}</b>.\n"
            + ("Sign-in will use a code in this chat — the bot is linked." if tfa_ok else
               "⚠️ Note: code sign-in needs a linked bot with admins; the panel does not see one.")
        )

    if get_user_lang(user_id) == "ru":
        status = "запущена" if running else "не запущена"
        text = (
            f"<b>🖥 Web Admin</b>\n\n"
            f"Статус: <code>{status}</code>\n\n"
            "<b>Termius</b>\n"
            "1. Откройте сервер → Port Forwarding.\n"
            f"2. Добавьте Local tunnel: <code>127.0.0.1:{ADMIN_WEB_PORT}</code> → "
            f"<code>127.0.0.1:{ADMIN_WEB_PORT}</code>.\n"
            "3. Запустите tunnel и откройте в браузере:\n"
            f"<code>{html.escape(local_url)}</code>\n\n"
            "<b>Обычный SSH</b>\n"
            f"<code>{html.escape(ssh_cmd)}</code>\n\n"
            + tail_ru
        )
    else:
        status = "running" if running else "not running"
        text = (
            f"<b>🖥 Web Admin</b>\n\n"
            f"Status: <code>{status}</code>\n\n"
            "<b>Termius</b>\n"
            "1. Open the server → Port Forwarding.\n"
            f"2. Add a Local tunnel: <code>127.0.0.1:{ADMIN_WEB_PORT}</code> → "
            f"<code>127.0.0.1:{ADMIN_WEB_PORT}</code>.\n"
            "3. Start the tunnel and open:\n"
            f"<code>{html.escape(local_url)}</code>\n\n"
            "<b>Regular SSH</b>\n"
            f"<code>{html.escape(ssh_cmd)}</code>\n\n"
            + tail_en
        )

    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="sub_panel")]]),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ============================================================================
# ADMIN MANAGEMENT
# ============================================================================


async def cb_menu_admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать список админов и кнопки управления."""
    query = update.callback_query
    await query.answer()

    user_id = _uid(update)
    title = _t(user_id, "admins_title")
    if ALLOWED_IDS:
        ids_list = "\n".join(f"  • <code>{uid}</code>" for uid in sorted(ALLOWED_IDS))
        text = f"<b>{title}</b>\n\n{_t(user_id, 'admins_list')}\n{ids_list}\n"
    else:
        # Раньше здесь было «Список пуст — доступ для всех». Это неправда:
        # при пустом списке бот НИКОГО не пускает и ждёт первого админа.
        text = f"<b>{title}</b>\n\n<i>{_t(user_id, 'admins_empty')}</i>\n"

    text += "\n" + _tf(user_id, "admins_hint", len(ALLOWED_IDS))

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="sec_settings")],
    ])
    await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addadmin ID [ID2 ID3 ...] — добавить админа вручную."""
    _clear_text_waiters(context, update.effective_user.id)
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text(
            f"⛔ Доступ запрещён.\nВаш ID: <code>{update.effective_user.id}</code>",
            parse_mode="HTML",
        )
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Использование: <code>/addadmin ID [ID2 ID3 ...]</code>\n"
            "Пример: <code>/addadmin 123456789 987654321</code>",
            parse_mode="HTML",
        )
        return

    added = []
    errors = []
    save_failed = False
    for a in args:
        a = a.strip().replace(",", "")
        if not a:
            continue
        try:
            uid = int(a)
            if not add_admin(uid):
                save_failed = True
            added.append(str(uid))
        except ValueError:
            errors.append(a)

    parts = []
    if added and save_failed:
        # .env не записался (диск/права): доступ выдан только до перезапуска — «✅» здесь ложь
        parts.append(
            f"⚠️ Добавлены ({', '.join(added)}), но записать .env не удалось — "
            f"после перезапуска бота доступ пропадёт. Проверьте место на диске и логи."
        )
    elif added:
        parts.append(f"✅ Добавлены: {', '.join(added)}")
    if errors:
        parts.append(f"❌ Ошибки: {', '.join(errors)}")

    await update.message.reply_text("\n".join(parts), parse_mode="HTML")


async def cmd_deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/deladmin ID — удалить админа."""
    _clear_text_waiters(context, update.effective_user.id)
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text(
            f"⛔ Доступ запрещён.\nВаш ID: <code>{update.effective_user.id}</code>",
            parse_mode="HTML",
        )
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Использование: <code>/deladmin ID</code>",
            parse_mode="HTML",
        )
        return

    removed = []
    save_failed = False
    for a in args:
        a = a.strip().replace(",", "")
        try:
            uid = int(a)
            if uid == update.effective_user.id:
                await update.message.reply_text("⚠️ Нельзя удалить себя!")
                continue
            if uid in ALLOWED_IDS:
                if not remove_admin(uid):
                    save_failed = True
                removed.append(str(uid))
            else:
                await update.message.reply_text(f"ID {uid} не найден в списке")
        except ValueError:
            await update.message.reply_text(f"❌ Некорректный ID: {html.escape(a)}")

    if removed and save_failed:
        # незаписанный .env для удаления опаснее: после рестарта отозванный админ вернётся
        await update.message.reply_text(
            f"⚠️ Удалены ({', '.join(removed)}), но записать .env не удалось — "
            f"после перезапуска бота они вернутся. Проверьте место на диске и логи."
        )
    elif removed:
        await update.message.reply_text(f"✅ Удалены: {', '.join(removed)}")


# ============================================================================
# PROMO & CREDITS
# ============================================================================


def get_promo_text() -> str:
    """Return promo text with 2 hosters, optional YouTube link and donate."""
    text = (
        "<b>💰 Хостинг #1 — скидка до 60%</b>\n"
        f"<a href='{PROMO_LINK_1}'>{PROMO_LINK_1}</a>\n\n"
        "<b>Промокоды:</b>\n"
        "  <code>OFF60</code> — 60% на первый месяц\n"
        "  <code>antenka20</code> — 20% + 3% за 3 мес\n"
        "  <code>antenka6</code> — 15% + 5% за 6 мес\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>💰 Хостинг #2 — скидка до 60%</b>\n"
        f"<a href='{PROMO_LINK_2}'>{PROMO_LINK_2}</a>\n\n"
        "<b>Промокод:</b>\n"
        "  <code>OFF60</code> — 60% на первый месяц\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>☕ Донат / Чаевые</b>\n"
        f"<a href='{TIP_LINK}'>{TIP_LINK}</a>"
    )
    if YOUTUBE_LINK:
        text += (
            "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<b>▶ YouTube-канал</b>\n"
            f"<a href='{html.escape(YOUTUBE_LINK)}'>{html.escape(YOUTUBE_LINK)}</a>"
        )
    return text


def should_show_promo_bot() -> bool:
    """Check if promo should be shown (once per 24h)."""
    try:
        if not os.path.exists(PROMO_STAMP_FILE):
            return True
        with open(PROMO_STAMP_FILE, "r") as f:
            last_ts = int(f.read().strip())
        return (int(time.time()) - last_ts) >= 86400
    except (ValueError, OSError):
        return True


def mark_promo_shown_bot() -> None:
    """Mark promo as shown."""
    try:
        os.makedirs(os.path.dirname(PROMO_STAMP_FILE), exist_ok=True)
        with open(PROMO_STAMP_FILE, "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


async def cb_menu_promo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Promo information — shown as a separate ephemeral message that
    auto-deletes after 30s so it does not clutter the chat. The main menu
    message stays intact (we don't edit it in place)."""
    query = update.callback_query
    await query.answer()

    promo_msg = await query.message.reply_text(
        get_promo_text(), parse_mode="HTML", disable_web_page_preview=True
    )
    spawn_task(_delete_message_after(promo_msg, 30))


async def cb_menu_credits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Credits and acknowledgements."""
    query = update.callback_query
    await query.answer()

    text = (
        f"<b>{_t(_uid(update), 'credits_title')}</b>\n\n"
        f"<b>goTelegram Pro v{GOTELEGRAM_VERSION}</b>\n\n"
        f"Built with love for the Telegram community\n\n"
        f"<b>Special thanks to:</b>\n\n"
        f"🙏 <b>telemt</b> - MTProxy engine\n"
        f"   High-performance proxy core\n\n"
        f"🎨 <b>HTML5UP</b> - Beautiful web templates\n"
        f"   Responsive design & themes\n\n"
        f"📚 <b>Learning Zone</b> - Educational resources\n"
        f"   Community learning support\n\n"
        f"🚀 <b>Start Bootstrap</b> - Bootstrap templates\n"
        f"   Professional design framework\n\n"
        f"💬 <b>Community</b> - Your feedback & support\n\n"
        f"<i>goTelegram Pro is open-source and community-driven</i>"
    )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(_uid(update), "btn_back"), callback_data="sec_settings")]]
    )
    await safe_edit_message(query,text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================================
# REMOVE
# ============================================================================


async def cb_menu_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаление установки — только из SSH-меню.

    Раньше кнопка «Yes, Remove» звала cb_remove_confirm, который делал
    `systemctl stop telemt` + `rm -rf /opt/gotelegram /var/www/gotelegram-site`
    и рапортовал «removed successfully». По факту это сносило ВСЕ бэкапы (они
    лежат в /opt/gotelegram/backups) и сам install.sh — после чего остальные
    функции бота и веб-админки мертвы, — но оставляло /etc/telemt/config.toml
    с секретами, systemd-юниты, nginx, сертификаты, правила SYN-фикса и WARP.
    Полноценное удаление умеет только SSH-меню, поэтому бот теперь только
    объясняет, куда идти, и ничего не удаляет сам.
    """
    query = update.callback_query
    await query.answer()
    user_id = _uid(update)

    text = (
        f"<b>{_t(user_id, 'remove_title')}</b>\n\n"
        f"{_t(user_id, 'remove_ssh_only')}\n\n"
        f"<code>gotelegram</code> → <b>3) {_t(user_id, 'remove_path_manage')}</b> → "
        f"<b>5) {_t(user_id, 'remove_path_remove')}</b>"
    )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="sec_settings")]]
    )
    await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")


# ============================================================================
# CALLBACK ROUTING
# ============================================================================


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all callbacks."""
    query = update.callback_query
    data = query.data

    # ── Авто-регистрация админа (до проверки доступа!) ──
    if data.startswith("admin_confirm_"):
        uid_pressed = update.effective_user.id
        try:
            new_admin_id = int(data.split("_")[-1])
        except (ValueError, IndexError):
            await query.answer()
            await safe_edit_message(query, "❌ Ошибка: некорректный ID")
            return
        # Безопасность: только тот кто нажал кнопку может стать админом.
        # Ответ ровно один — иначе show_alert не доезжает до пользователя.
        if uid_pressed != new_admin_id:
            await query.answer(_t(uid_pressed, "admin_claim_not_yours"), show_alert=True)
            return
        # Race condition: если кто-то уже стал админом
        if not _WAITING_FOR_ADMIN:
            await query.answer()
            await safe_edit_message(query, _t(uid_pressed, "admin_already_set"))
            return
        # Окно авто-регистрации могло закрыться, пока сообщение висело в чате
        if not admin_claim_open():
            await query.answer()
            await safe_edit_message(
                query,
                _tf(uid_pressed, "admin_claim_expired", ADMIN_CLAIM_WINDOW_SECONDS // 60),
                parse_mode="HTML",
            )
            return
        await query.answer()
        add_admin(new_admin_id)
        logger.warning("First admin registered from Telegram: %s", new_admin_id)
        await safe_edit_message(
            query,
            f"✅ <b>Вы назначены администратором!</b>\n\n"
            f"ID: <code>{new_admin_id}</code>\n\n"
            f"Нажмите /start чтобы открыть меню.",
            parse_mode="HTML",
        )
        return

    if data == "admin_cancel":
        await query.answer()
        await safe_edit_message(
            query,
            "👋 Ок. Напишите /start когда будете готовы.",
        )
        return

    # Access control
    if not is_user_allowed(update.effective_user.id):
        await query.answer("Доступ запрещён")
        return

    user_id = update.effective_user.id
    # Кнопка = навигация → отменяем любое ожидание ввода текста (сеттеры ниже
    # выставят нужный флаг заново). Чинит утечку awaiting_warp_key в чужие flow.
    _clear_text_waiters(context, user_id)

    # Main menu
    if data == "menu_main":
        await query.answer()
        buttons = get_main_menu(user_id)
        text = (
            f"<b>{_tf(user_id, 'welcome_title', GOTELEGRAM_VERSION)}</b>\n\n"
            f"{_t(user_id, 'welcome_subtitle')}\n"
            f"{_t(user_id, 'welcome_prompt')}"
        )
        await safe_edit_message(query, text, reply_markup=buttons, parse_mode="HTML")
        return

    if data == "close_menu":
        await query.answer()
        await query.delete_message()
        return

    # Language picker
    if data == "menu_lang":
        await query.answer()
        current = get_user_lang(user_id)
        title = _t(user_id, "lang_title")
        curr_line = _tf(user_id, "lang_current", get_language_name(current))
        prompt = _t(user_id, "lang_choose")
        text = f"<b>{title}</b>\n\n{curr_line}\n\n{prompt}"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🇬🇧 English", callback_data="lang_set_en"),
                InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_set_ru"),
            ],
            [InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="sec_settings")],
        ])
        await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if data.startswith("lang_set_"):
        code = data.replace("lang_set_", "", 1)
        if code in SUPPORTED_LANGS:
            set_user_lang(user_id, code)
            await query.answer(_tf(user_id, "lang_saved", get_language_name(code)))
            # Re-render main menu in the new language
            buttons = get_main_menu(user_id)
            text = (
                f"<b>{_tf(user_id, 'welcome_title', GOTELEGRAM_VERSION)}</b>\n\n"
                f"{_t(user_id, 'welcome_subtitle')}\n"
                f"{_t(user_id, 'welcome_prompt')}"
            )
            await safe_edit_message(query, text, reply_markup=buttons, parse_mode="HTML")
        else:
            await query.answer("Unsupported language")
        return

    # Dispatch to handlers
    handlers = {
        "menu_install": cb_menu_install,
        "menu_status": cb_menu_status,
        "menu_link": cb_menu_link,
        "menu_share": cb_menu_share,
        "menu_restart": cb_menu_restart,
        "menu_logs": cb_menu_logs,
        "menu_warp": cb_menu_warp,
        "menu_web": cb_menu_web,
        "web_on": cb_web_on,
        "web_off": cb_web_off,
        "web_carrier": cb_web_carrier,
        "web_host": cb_web_host,
        "warp_probe": cb_warp_probe,
        "warp_on_free": cb_warp_on_free,
        "warp_off": cb_warp_off,
        "warp_key": cb_warp_key,
        "menu_backup": cb_menu_backup,
        "menu_restore": cb_menu_restore,
        "menu_update": cb_menu_update,
        "menu_change": cb_menu_change,
        "menu_website": cb_menu_website,
        "menu_promo": cb_menu_promo,
        "menu_credits": cb_menu_credits,
        "menu_admin_web": cb_menu_admin_web,
        "menu_admins": cb_menu_admins,
        "menu_users": cb_menu_users,
        "menu_antidpi": cb_menu_antidpi,
        "sec_state": cb_sec_state,
        "edgeips": cb_edgeips,
        "sec_service": cb_sec_service,
        "sec_settings": cb_sec_settings,
        "sub_site": cb_sub_site,
        "sub_panel": cb_sub_panel,
        "menu_miniapp": cb_menu_miniapp,
        "ma_on": cb_miniapp_set,
        "ma_off": cb_miniapp_set,
        "ma_rotate": cb_miniapp_set,
        "ma_rotateyes": cb_miniapp_set,
        "menu_remove": cb_menu_remove,
        "install_mode_lite": cb_install_mode_lite,
        "install_mode_pro": cb_install_mode_pro,
        "backup_create": cb_backup_create,
        "backup_list": cb_backup_list,
        "ssl_renew": cb_ssl_renew,
        "ssl_status": cb_ssl_status,
        "change_lite": cb_change_lite,
        "change_pro": cb_change_pro,
        "install_migrate": cb_install_migrate,
        "menu_stats": cb_menu_stats,
        "backup_schedule_off": cb_backup_schedule_set,
        "backup_schedule_daily": cb_backup_schedule_set,
        "backup_schedule_weekly": cb_backup_schedule_set,
        "backup_schedule_monthly": cb_backup_schedule_set,
    }

    # Custom git template URL prompt
    if data == "pro_custom_git":
        await query.answer()
        # отказ сразу на кнопке, не после ввода URL: в Lite/без установки шаблону
        # некуда деплоиться (тот же ответ, что даёт SSH)
        if (load_json(GOTELEGRAM_CONFIG) or {}).get("mode") != "pro":
            await safe_edit_message(
                query,
                _t(user_id, "change_only_pro", default="Template change is available in pro mode only"),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(_t(user_id, "btn_back"), callback_data="menu_main")]]
                ),
            )
            return
        _CUSTOM_GIT_WAITERS[user_id] = True
        _arm_text_waiter(context)
        title = _t(user_id, "cg_title")
        body = _t(user_id, "cg_ask_url")
        await safe_edit_message(
            query,
            f"<b>{title}</b>\n\n{body}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(_t(user_id, "btn_cancel"), callback_data="menu_main")]]
            ),
            parse_mode="HTML",
        )
        return

    # Pattern-based handlers
    if data.startswith("lite_dom_"):
        await cb_lite_domain(update, context)
    elif data == "user_export":
        await cb_user_export(update, context)
    elif data == "user_add":
        await cb_user_add(update, context)
    elif data.startswith("user_view~"):
        await cb_user_view(update, context)
    elif data.startswith("user_qr~") or data.startswith("user_webqr~"):
        await cb_user_qr(update, context)
    elif data.startswith("user_iplimit~"):
        await cb_user_ip_limit(update, context)
    elif data.startswith("user_toggle~"):
        await cb_user_toggle(update, context)
    # "~" не входит в алфавит имён ключей (_USER_NAME_RE), поэтому
    # user_delok~ и user_del~ больше не могут пересечься ни при каком имени.
    elif data.startswith("user_delok~"):
        await cb_user_delete_confirm(update, context)
    elif data.startswith("user_del~"):
        await cb_user_delete(update, context)
    elif data.startswith("pro_cat_"):
        await cb_pro_category(update, context)
    elif data.startswith("pro_tpl_"):
        await cb_pro_template(update, context)
    elif data.startswith("pro_confirm_"):
        await cb_pro_confirm(update, context)
    elif data.startswith("restore_idx_") or data.startswith("restore_yes_"):
        await cb_restore_backup(update, context)
    elif data.startswith("menu_users~"):
        await cb_menu_users(update, context)
    elif data.startswith("adpi_set~"):
        await cb_antidpi_set(update, context)
    elif data == "noop":
        # кнопка-счётчик страниц: просто гасим «часики» у клиента
        await query.answer()
    elif data in handlers:
        await handlers[data](update, context)
    else:
        await query.answer("Unknown action")


# ============================================================================
# ERROR HANDLERS
# ============================================================================


async def cmd_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать или убрать постоянную клавиатуру под полем ввода."""
    if not is_user_allowed(update.effective_user.id):
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    now_on = _reply_kb_enabled(chat_id)
    _reply_kb_set(chat_id, not now_on)
    if now_on:
        await update.message.reply_text(
            _t(user_id, "rkb_off"), reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text(
            _t(user_id, "rkb_on"), reply_markup=get_reply_keyboard(user_id)
        )


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-text input. Currently used for custom git template URLs."""
    if update.message is None or update.message.text is None:
        return
    if not is_user_allowed(update.effective_user.id):
        return
    user_id = update.effective_user.id
    # Reply-кнопка присылает обычный текст, поэтому разбираем ДО waiter-ов:
    # иначе подпись «📊 Статус» ушла бы в ожидание ввода как ответ.
    _rkb = _reply_kb_action(update.message.text)
    if _rkb:
        await _reply_kb_dispatch(update, context, _rkb)
        return
    # Просроченное ожидание не должно перехватывать ввод. Кнопки его снимают
    # (_clear_text_waiters), но человек может просто ничего не нажимать: нажал
    # «добавить ключ», отвлёкся — и через сутки первое же сообщение боту уходило
    # как имя ключа. Теперь ожидание живёт _WAITER_TTL и дальше молча забывается.
    if _text_waiter_expired(context):
        _clear_text_waiters(context, user_id)
    if context.user_data.get("awaiting_warp_key", False):
        key = update.message.text.strip()
        # пустой ввод тоже через валидатор: key="" установщик толкует как переход на free
        if not _WARP_KEY_RE.match(key):
            # ожидание НЕ снимаем: раньше pop стоял до проверок, и после «неверный
            # формат» правильный ключ, присланный следом, молча терялся (баг 12.7)
            await update.message.reply_text(
                _t(update.effective_user.id, "warp_key_badfmt")
            )
            return
        if _BOT_ACTION_LOCK.locked():
            # «подождите пару секунд» зовёт прислать ключ повторно — ожидание должно жить
            await update.message.reply_text(_t(update.effective_user.id, "warp_busy"))
            return
        # флаг снимаем только когда ключ реально уходит в работу — как у соседних waiter-ов
        context.user_data.pop("awaiting_warp_key", None)
        await update.message.reply_text(_t(update.effective_user.id, "warp_applying_key"))
        async with _BOT_ACTION_LOCK:
            r = await run_bot_action("warp-key", timeout=360, key=key)
        ok = r.get("status") == "ok"
        await update.message.reply_text(("\u2705 " if ok else "\u26a0\ufe0f ") + str(r.get("message", r)))
        return
    if context.user_data.get("awaiting_web_host", False):
        await set_web_host_from_text(update, context, update.message.text)
        return
    # ВАЖНО: ожидание снимаем ТОЛЬКО после успешной обработки. Раньше здесь стоял
    # .pop(), и он срабатывал ДО валидации: пользователь вводил имя с недопустимым
    # символом, получал «Некорректное имя» — и бот переставал слушать совсем.
    # Следующее, уже правильное имя уходило в пустоту, а на экране висел прежний
    # экран «Новый ключ». Выглядело как namертво зависший бот.
    ip_limit_user = context.user_data.get("awaiting_user_ip_limit")
    if ip_limit_user:
        if await set_user_ip_limit_from_text(update, context, update.message.text.strip(), str(ip_limit_user)):
            context.user_data.pop("awaiting_user_ip_limit", None)
        return
    if context.user_data.get("awaiting_user_name", False):
        if await create_user_from_text(update, context, update.message.text.strip()):
            context.user_data.pop("awaiting_user_name", None)
        return

    # Only act when we're explicitly waiting for a custom-git URL
    if not _CUSTOM_GIT_WAITERS.get(user_id, False):
        return
    url = update.message.text.strip()
    if not _validate_custom_git_url(url):
        # ожидание НЕ снимаем — человек поправит ссылку и пришлёт снова
        await update.message.reply_text(_t(user_id, "cg_invalid"), parse_mode="HTML")
        return
    # как в SSH и cb_pro_confirm: смена шаблона только в pro — в Lite клон писал
    # template_id и рапортовал «✅», хотя сайта в Lite нет вовсе
    if (load_json(GOTELEGRAM_CONFIG) or {}).get("mode") != "pro":
        _CUSTOM_GIT_WAITERS.pop(user_id, None)
        await update.message.reply_text(
            _t(user_id, "change_only_pro", default="Template change is available in pro mode only")
        )
        return
    if _BOT_ACTION_LOCK.locked():
        # ожидание не снимаем: человек повторит ссылку, когда операция закончится
        await update.message.reply_text(_t(user_id, "user_config_busy"))
        return
    _CUSTOM_GIT_WAITERS.pop(user_id, None)
    await update.message.reply_text(_tf(user_id, "cg_cloning", html.escape(url)), parse_mode="HTML")
    # клон+деплой под общим локом: без него шла гонка с change-template под flock,
    # ради защиты /var/www от которой этот лок и заводили
    async with _BOT_ACTION_LOCK:
        ok, tpl_id, info = await _download_custom_git_template(url)
        if not ok:
            await update.message.reply_text(_t(user_id, info), parse_mode="HTML")
            return
        # Success — record in goTelegram Pro config. Use "template_id" (canonical
        # field name written by install.sh/save_gotelegram_config).
        # под общим локом: панель может в этот же момент менять язык — без лока
        # одно из двух изменений молча терялось (запись атомарна, но не изолирована)
        try:
            async with FileLock(CONFIG_JSON_LOCK):
                config = load_json(GOTELEGRAM_CONFIG) or {}
                config["template_id"] = tpl_id
                config["template_source"] = url
                save_json(GOTELEGRAM_CONFIG, config)
        except FileLockTimeout:
            await update.message.reply_text(_t(user_id, "user_config_busy"))
            return
        if config.get("mode") == "pro" and os.path.isdir(info):
            try:
                os.makedirs(WEBSITE_ROOT, exist_ok=True)
                for entry in os.listdir(WEBSITE_ROOT):
                    path = os.path.join(WEBSITE_ROOT, entry)
                    if os.path.isdir(path) and not os.path.islink(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                for entry in os.listdir(info):
                    src = os.path.join(info, entry)
                    dst = os.path.join(WEBSITE_ROOT, entry)
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
            except OSError as e:
                logger.error("custom template deploy failed: %s", e)
                # сайт не развёрнут — «✅ загружен» здесь был бы ложным успехом
                await update.message.reply_text(
                    _t(user_id, "cg_deploy_fail", default="❌ Failed to deploy template files to the web root")
                )
                return
    await update.message.reply_text(
        _tf(user_id, "cg_ok_fmt", html.escape(tpl_id)),
        reply_markup=get_main_menu(user_id),
        parse_mode="HTML",
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Залогировать ошибку и сказать об этом пользователю.

    Раньше строка была f-строкой без плейсхолдеров, а пользователю не уходило
    ничего: упавший хендлер выглядел как «кнопка не работает».
    """
    logger.error("Exception while handling update %s", update, exc_info=context.error)

    # PTB отдаёт сюда произвольный объект — работаем через getattr, чтобы
    # уведомление не потерялось из-за неожиданного типа update.
    user_id = _uid(update)
    notice = _t(user_id, "internal_error")
    query = getattr(update, "callback_query", None)
    if query is not None:
        try:
            await query.answer(notice, show_alert=True)
            return
        except Exception:
            pass  # callback мог быть уже отвечен — падаем в reply ниже
    message = getattr(update, "effective_message", None)
    if message is not None:
        try:
            await message.reply_text(notice)
        except Exception as e:
            logger.debug("error_handler: failed to notify user: %s", e)


# ============================================================================
# MAIN APPLICATION
# ============================================================================


async def _announce_self(app) -> None:
    """Заявить в Telegram СВОИ команды и свою кнопку меню.

    goTelegram никогда не вызывал setMyCommands, поэтому в списке команд бота
    висело то, что оставил прежний хозяин токена (у пользователя это был другой
    проект: /inline, /id и кнопка «Open app» на давно умерший трycloudflare-адрес).
    Человек жал по ним и ничего не происходило. Раз бот наш — он и должен
    объявлять свой набор, а не наследовать чужой.
    """
    cmds = [
        BotCommand("start", "Меню"),
        BotCommand("status", "Статус прокси"),
        BotCommand("logs", "Логи"),
        BotCommand("lang", "Язык / Language"),
        BotCommand("addadmin", "Добавить админа"),
        BotCommand("deladmin", "Удалить админа"),
        BotCommand("help", "Справка"),
        BotCommand("keyboard", "Клавиатура под полем ввода"),
    ]
    try:
        await app.bot.set_my_commands(cmds)
    except Exception as exc:
        logger.warning("set_my_commands: %s", exc)
    # Кнопка меню: наш мини-апп, если включён, иначе просто список команд —
    # чтобы затереть чужую кнопку, ведущую в никуда.
    # ВАЖНО: кнопку можно выставить ПЕРСОНАЛЬНО для чата, и такая перекрывает
    # общую. У пользователя именно это и было: прежний проект прописал кнопку
    # в его личный чат, поэтому смена общей ничего не меняла. Проходим ещё и по
    # чатам админов.
    await refresh_menu_button(app.bot)


async def refresh_menu_button(bot) -> None:
    """Переставить кнопку меню под текущее состояние мини-аппа.

    Вынесено из _announce_self, потому что нужно не только на старте: включение
    и выключение мини-аппа меняет кнопку, а раньше ради этого перезапускали
    всего бота. Перезапуск изнутри самого бота означал бы, что он убивает себя,
    не успев ответить на нажатие, — поэтому переставляем в процессе.

    ВАЖНО: кнопку можно выставить ПЕРСОНАЛЬНО для чата, и такая перекрывает
    общую. У пользователя именно это и было: прежний проект прописал кнопку
    в его личный чат, поэтому смена общей ничего не меняла. Проходим ещё и по
    чатам админов.
    """
    url = miniapp_url()
    button = MenuButtonWebApp(text="Панель", web_app=WebAppInfo(url=url)) if url else MenuButtonCommands()
    targets = [None] + [int(a) for a in ALLOWED_IDS if str(a).isdigit()]
    for chat_id in targets:
        try:
            if chat_id is None:
                await bot.set_chat_menu_button(menu_button=button)
            else:
                await bot.set_chat_menu_button(chat_id=chat_id, menu_button=button)
        except Exception as exc:
            logger.warning("set_chat_menu_button(%s): %s", chat_id, exc)



def main() -> None:
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set in .env file")
        return

    # Create the Application
    # concurrent_updates: по умолчанию PTB обрабатывает апдейты строго по одному,
    # и на время бекапа (до 900с) / WARP (360с) бот глохнул для всех, а все
    # busy-экраны «другая операция выполняется» были недостижимым кодом
    application = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()

    # Command handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("logs", cmd_logs))
    application.add_handler(CommandHandler("lang", cmd_lang))
    application.add_handler(CommandHandler("keyboard", cmd_keyboard))
    application.add_handler(CommandHandler("addadmin", cmd_addadmin))
    application.add_handler(CommandHandler("deladmin", cmd_deladmin))

    # Callback query handler (buttons)
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Text message handler (for custom git URL input)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_text_message
    ))

    # Error handler
    application.add_error_handler(error_handler)

    # Заявляем свои команды и кнопку меню при старте
    application.post_init = _announce_self

    # Run the bot
    logger.info(f"goTelegram Pro v{GOTELEGRAM_VERSION} bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
