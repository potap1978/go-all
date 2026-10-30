# goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
# Licensed under the goVLESS Source-Available License (see the LICENSE file).
# Redistribution, mirroring, or republishing in any form — whole or partial,
# modified or not — is prohibited without prior written permission.

"""methods.py — JSON-RPC method implementations for govlessctl.

Each WRITE method calls state_db.audit(...) BEFORE returning. Typed-confirm
tokens are stored in state.db pending_confirms (single-use, 5min TTL).
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import secrets
import shutil
import subprocess
import time
import uuid as uuid_mod
from typing import Any, Callable
from urllib.parse import urlsplit

from .auth import load_bot_env
from .catalog_tool import Catalog
from .state_db import StateDB
from .xui_client import (
    XUI_DB_PATH,
    XuiClient,
    XuiError,
    build_vless_links,
    read_client_traffics_from_db,
    read_inbounds_from_db,
    read_xui_settings_from_db,
)

import logging
log = logging.getLogger("govlessctl.methods")


# ── Constraints / paths ───────────────────────────────────────────────────
TUNNEL_URL_FILE = "/run/govless/tunnel.url"
TUNNEL_URL_NAMED_FILE = "/run/govless/tunnel.url.named"
GOVLESS_CONFIG = "/opt/govless/config.json"  # mode + server_ip + domain
PANEL_ACCESS_FILE = "/etc/govless/panel.access"  # "public" or "ssh-only"
NOTIFY_QUEUE = "/run/govless/notify.queue"  # daemon writes, bot delivers
VERSION = "1.4.0"
WEBAPP_CACHE_BUST = "20260525-042"
GIB = 1024**3
TRAFFIC_BYTES_COMPAT_THRESHOLD = 1024 * 1024


# ── Validators ────────────────────────────────────────────────────────────
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-@+]{1,64}$")


class MethodError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _require_uuid(u: Any) -> str:
    if not isinstance(u, str) or not UUID_RE.match(u):
        raise MethodError(400, "invalid uuid format")
    return u.lower()


def _require_name(n: Any) -> str:
    if not isinstance(n, str) or not n.strip():
        raise MethodError(400, "name required")
    n = n.strip()
    if len(n) > 32:
        raise MethodError(400, "name too long (max 32 chars)")
    if not NAME_RE.match(n):
        raise MethodError(400, "name contains illegal characters (allowed: A-Z a-z 0-9 _ . - @ +)")
    return n


def _require_int(v: Any, field: str, *, min_v: int | None = None) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise MethodError(400, f"{field} must be an integer")
    if min_v is not None and v < min_v:
        raise MethodError(400, f"{field} must be >= {min_v}")
    return int(v)


def _traffic_limit_to_bytes(v: Any, field: str = "traffic_limit", unit: str | None = None) -> int:
    """Traffic limit in bytes.

    `unit="gib"` means the caller sends gibibytes (the bot's /setlimit does).
    Without it a number is bytes. The old heuristic — "anything below 1 MiB must
    be gibibytes" — turned a deliberate 500 KB cap into 500 GiB (QA BUG-40);
    it stays only as a compatibility path for values that cannot be a byte cap.
    """
    value = _require_int(v, field, min_v=0)
    if value == 0:
        return 0
    if unit == "gib":
        return value * GIB
    if unit in ("bytes", "b"):
        return value          # explicit bytes: never second-guess the caller
    if value < TRAFFIC_BYTES_COMPAT_THRESHOLD:
        # No unit given — legacy callers (bot before traffic_unit) sent GiB.
        return value * GIB
    return value


# ── Helpers ───────────────────────────────────────────────────────────────
def _systemctl_active(unit: str) -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() == "active"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _read_config_json() -> dict:
    if not os.path.exists(GOVLESS_CONFIG):
        return {}
    try:
        import json
        with open(GOVLESS_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _valid_public_ip(ip: Any) -> bool:
    if not isinstance(ip, str):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    if any(o < 0 or o > 255 for o in octets):
        return False
    if octets[0] in (0, 10, 127):
        return False
    if octets[0] == 172 and 16 <= octets[1] <= 31:
        return False
    if octets[0] == 192 and octets[1] == 168:
        return False
    if octets[0] == 169 and octets[1] == 254:
        return False
    if octets[0] >= 224:
        return False
    return True


def _qr_text(payload: str) -> str:
    """Render UTF-8 QR via /usr/bin/qrencode -t UTF8. Returns text or fallback."""
    qrencode = shutil.which("qrencode")
    if not qrencode:
        return f"[qrencode not installed]\n{payload}"
    try:
        out = subprocess.run(
            [qrencode, "-t", "UTF8", "-m", "1", payload],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout
        return f"[qrencode failed: {out.stderr.strip()}]\n{payload}"
    except subprocess.TimeoutExpired:
        return f"[qrencode timeout]\n{payload}"


def _qr_svg(payload: str) -> str:
    """Render QR as inline SVG via /usr/bin/qrencode -t SVG. Returns SVG markup
    (no <?xml?> prolog) for direct embedding into a HTML page, or "" on failure.
    SVG scales perfectly at any size and renders crisp at any DPI — fixes the
    half-block UTF-8 QR distortion bug (aspect ratio of block glyphs in mono fonts
    is not 1:1, so UTF8 QRs look stretched)."""
    qrencode = shutil.which("qrencode")
    if not qrencode:
        return ""
    try:
        out = subprocess.run(
            [qrencode, "-t", "SVG", "-m", "1", "--inline", "--svg-path", payload],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout
        # Fallback to non-inline if --inline/--svg-path unsupported (qrencode < 4)
        out = subprocess.run(
            [qrencode, "-t", "SVG", "-m", "1", payload],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            # Strip <?xml ...?> prolog so it embeds inline cleanly
            svg = out.stdout
            if svg.startswith("<?xml"):
                svg = svg[svg.index("?>") + 2 :].lstrip()
            return svg
        return ""
    except subprocess.TimeoutExpired:
        return ""


def _server_host(cfg: dict | None = None) -> str:
    cfg = cfg or _read_config_json()
    mode = cfg.get("mode", "lite")
    if mode == "pro" and cfg.get("domain"):
        return cfg["domain"]
    for key in ("server_ip", "server"):
        host = cfg.get(key)
        if _valid_public_ip(host):
            return host
    raise MethodError(503, "no valid public server address in config")


def _subscription_url_for_client(cli: dict, *, cfg: dict | None = None, settings: dict | None = None) -> str | None:
    # T2875/T2879: don't expose subscription for disabled clients
    if cli.get("enable") is False or cli.get("enabled") is False:
        return None
    cfg = cfg or _read_config_json()
    settings = settings or read_xui_settings_from_db()
    if cfg.get("mode") != "pro" or not cfg.get("domain"):
        return None
    sub_port = str(cfg.get("sub_port") or settings.get("subPort") or "")
    sub_path = str(cfg.get("sub_path") or settings.get("subPath") or "").strip("/")
    if not sub_port or not sub_path:
        return None
    sub_id = cli.get("subId") or cli.get("id")
    if not sub_id:
        return None
    return f"https://{_server_host(cfg)}:{sub_port}/{sub_path}/{sub_id}"


def _govless_version(cfg: dict) -> str:
    version = cfg.get("version") or VERSION
    return str(version)


def _pro_webapp_url(cfg: dict) -> str | None:
    if cfg.get("mode") != "pro":
        return None
    raw_domain = str(cfg.get("domain") or "").strip()
    if not raw_domain:
        return None
    if "://" in raw_domain:
        host = urlsplit(raw_domain).netloc
    else:
        host = raw_domain.split("/", 1)[0]
    host = host.strip().strip("/")
    if not host:
        return None
    return f"https://{host}/app/?v={WEBAPP_CACHE_BUST}"


def _find_active_inbound(inbounds: list[dict]) -> dict:
    for ib in inbounds:
        if ib.get("enable") and (ib.get("protocol") == "vless"):
            return ib
    raise MethodError(404, "no enabled VLESS inbound found")


def _client_by_uuid(inbounds: list[dict], uuid_str: str) -> tuple[dict, dict]:
    """Locate (inbound, client_obj) for a given UUID across all inbounds."""
    uuid_lc = uuid_str.lower()
    for ib in inbounds:
        for cli in (ib.get("settings_obj") or {}).get("clients", []) or []:
            if str(cli.get("id", "")).lower() == uuid_lc:
                return ib, cli
    raise MethodError(404, f"client {uuid_str} not found")


def _new_typed_token() -> str:
    return secrets.token_urlsafe(16)


# ── Methods class ─────────────────────────────────────────────────────────

def _proc_running(name):
    import subprocess
    try:
        return subprocess.run(["pgrep","-af",name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    except Exception:
        return False

def _xray_version() -> str | None:
    """Best-effort: read xray binary -version, return short version string."""
    import subprocess, re as _re
    for path in ("/usr/local/x-ui/bin/xray-linux-amd64",
                 "/usr/local/x-ui/bin/xray-linux-arm64-v8a",
                 "/usr/local/x-ui/bin/xray"):
        try:
            r = subprocess.run([path, "-version"], capture_output=True, timeout=3, text=True)
            if r.returncode == 0:
                m = _re.match(r"Xray\s+(\S+)", r.stdout)
                if m:
                    return m.group(1)
        except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
            continue
    return None


def _xui_panel_version() -> str | None:
    """3X-UI panel version — ask the panel, don't guess from its binary.

    The old code took the FIRST `vN.N` string inside the binary and reported
    "v1.2" for a 3.7.0 panel (QA BUG-25): those strings are library versions in
    whatever order the linker left them. `x-ui -v` is authoritative; the config
    written at install time is the next best thing.
    """
    import subprocess, re as _re
    for cmd in (["x-ui", "-v"], ["/usr/local/x-ui/x-ui", "-v"]):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=5, text=True)
            out = f"{r.stdout} {r.stderr}"
            m = _re.search(r"\bv?(\d+\.\d+(?:\.\d+)?)\b", out)
            if m:
                return "v" + m.group(1)
        except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError, OSError):
            continue
    ver = _read_config_json().get("xui_version")
    if isinstance(ver, str) and ver.strip():
        return ver if ver.startswith("v") else "v" + ver
    return None



# ── Cover site (Pro masquerade site) templates ──────────────────────────
# The picker itself lives in bash (lib/templates_catalog.sh); the daemon only
# reads the catalog and runs `govless --site-template-* --json` as a background
# job, so the bot / Mini App never block on a 30-180 s clone.
SITE_WEBROOT = "/var/www/html"
SITE_BACKUP_DIR = "/opt/govless/site-backups"
SITE_JOB_TIMEOUT = 420  # clone + verify + deploy on a slow VPS
SITE_CATALOG_FILE = "/opt/govless/templates_catalog.json"
SITE_STUB_ID = "__stub__"
SITE_TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
SITE_CATEGORY_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
SITE_KEY_RE = re.compile(r"^[0-9a-f]{12}$")
SITE_CUSTOM_URL_RE = re.compile(r"^https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{1,500}$")
# EN names mirror lib/lang/en.sh tplcat_* (catalog names are Russian)
SITE_CATEGORY_EN = {
    "business": "Business & corporate", "ecommerce": "E-commerce & online stores",
    "medical": "Medical & fitness", "education": "Education", "portfolio": "Portfolio & creative",
    "realestate": "Real estate & interior", "restaurant": "Restaurants & food",
    "landing": "Landing pages", "technology": "Technology & IT", "blog": "Blogs & magazines",
    "travel": "Travel", "beauty": "Beauty & weddings", "automotive": "Automotive & transport",
    "entertainment": "Entertainment & events", "nonprofit": "Non-profit & charity",
    "photography": "Photography", "legal": "Legal", "other": "Other",
}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _installer_script() -> str | None:
    """Locate govless.sh. The `govless` shortcut always points at the freshest
    checkout (1.3.4+), so parse it first; then the known install dirs."""
    candidates: list[str] = []
    try:
        with open("/usr/local/bin/govless", "r", encoding="utf-8") as f:
            for line in f:
                m = re.search(r'exec bash "([^"]+/govless\.sh)"', line)
                if m:
                    candidates.append(m.group(1))
    except OSError:
        pass
    candidates += [
        os.environ.get("GOVLESS_INSTALLER", ""),
        "/opt/govless-installer/govless.sh",
        "/root/goVLESS/govless.sh",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _xui_real_binary() -> str | None:
    """The 3X-UI *binary* (accepts `setting -username …`), never /usr/bin/x-ui,
    which is the interactive management shell script."""
    for c in ("/usr/local/x-ui/x-ui", shutil.which("x-ui") or ""):
        if c and os.path.isfile(c):
            try:
                with open(c, "rb") as f:
                    if f.read(2) == b"#!":
                        continue  # shell wrapper, not the binary
            except OSError:
                continue
            return c
    return None


def _rotate_panel_session_key(db_path: str = XUI_DB_PATH) -> bool:
    """Give 3X-UI a new settings.secret — the key its session cookies are
    signed with (2.9.4 and 3.x: cookie.NewStore(secret)). The cookie carries the
    user object and is never re-checked against the password, so a new password
    alone left every open session — and anyone who had seen the key — logged in.
    Applies on the next x-ui restart."""
    if not os.path.isfile(db_path):
        return False
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        with conn:
            conn.execute("DELETE FROM settings WHERE key='secret'")
            conn.execute("INSERT INTO settings (key, value) VALUES ('secret', ?)",
                         (secrets.token_hex(16),))
        return True
    except sqlite3.Error as exc:
        log.warning("panel session key not rotated: %s", exc)
        return False
    finally:
        conn.close()


def _govless_root() -> str:
    """Directory that holds govless.sh + lib/ on THIS server.

    goinstall/bootstrap installs put the tree into /opt/govless-installer; the
    legacy install.sh used /root/goVLESS. The backup/restore/repair shell
    snippets used to `cd /root/goVLESS || cd /opt/govless`, so on every
    goinstall-based server they sourced nothing and failed with «rc!=0» (found
    on the ilovelife.site stand, 2026-09-22)."""
    env = os.environ.get("GOVLESS_INSTALLER", "")
    candidates = [os.path.dirname(env)] if env else []
    candidates += ["/opt/govless-installer", "/root/goVLESS", "/opt/govless"]
    for d in candidates:
        if d and os.path.isfile(os.path.join(d, "lib", "xui.sh")):
            return d
    return "/opt/govless-installer"


def _lib_bash(call: str) -> list[str]:
    """argv for `bash -c` that loads goVLESS lib/ — i18n included, so t/tf exist
    and messages come out in the server's language — and then runs `call`.

    backup/restore/repair used to skip lib/i18n.sh: every log line reached the
    Mini App as «t: command not found» instead of the message (stand,
    23.09.2026)."""
    return ["bash", "-c",
            "set -e; cd " + shlex.quote(_govless_root()) + "; "
            "source lib/common.sh 2>/dev/null || true; "
            "{ source lib/i18n.sh && load_language \"$(detect_language)\"; } 2>/dev/null || true; "
            "source lib/xui_api.sh 2>/dev/null || true; "
            "source lib/xui.sh 2>/dev/null && " + call]

def _site_catalog_path() -> str | None:
    if os.path.isfile(SITE_CATALOG_FILE):
        return SITE_CATALOG_FILE
    inst = _installer_script()
    if inst:
        p = os.path.join(os.path.dirname(inst), "templates_catalog.json")
        if os.path.isfile(p):
            return p
    return None


def _site_current() -> dict:
    """What is deployed in the web root right now (markers written by lib/website.sh)."""
    tid = ""
    src = ""
    try:
        with open(os.path.join(SITE_WEBROOT, ".govless_template_id"), "r", encoding="utf-8") as f:
            tid = f.readline().strip()
    except OSError:
        pass
    try:
        with open(os.path.join(SITE_WEBROOT, ".govless_template_source"), "r", encoding="utf-8") as f:
            src = f.readline().strip()
    except OSError:
        pass
    if not tid:
        idx = os.path.join(SITE_WEBROOT, "index.html")
        if os.path.isfile(idx):
            try:
                with open(idx, "r", encoding="utf-8", errors="replace") as f:
                    tid = SITE_STUB_ID if "This server is configured and operating normally" in f.read(65536) else "__unknown__"
            except OSError:
                tid = "__unknown__"
        else:
            tid = "__none__"
    return {"id": tid, "source_url": src}


def _site_public(t: dict) -> dict:
    """Catalog record → API shape (no internal fields, EN category name added)."""
    cat_id = t.get("category") or ""
    return {
        "id": t.get("id"),
        "key": Catalog.short_key(str(t.get("id", ""))),
        "name": t.get("name"),
        "source": t.get("source"),
        "description": t.get("description") or "",
        "repo_url": t.get("repo_url"),
        "preview_url": t.get("preview_url") or None,
        "license": t.get("license") or None,
        "stars": t.get("stars"),
        "category": cat_id,
        "category_en": SITE_CATEGORY_EN.get(cat_id, cat_id),
    }


CREDENTIALS_FILE = "/root/.govless_credentials"
BCRYPT_RE = re.compile(r"^\$2[aby]?\$\d{2}\$")


def _xui_web_settings() -> dict:
    """webListen / webPort straight from x-ui.db (the panel's own truth)."""
    out: dict = {}
    try:
        import sqlite3 as _sqlite3
        with _sqlite3.connect("/etc/x-ui/x-ui.db", timeout=3) as conn:
            for key in ("webListen", "webPort"):
                row = conn.execute("SELECT value FROM settings WHERE key=? LIMIT 1", (key,)).fetchone()
                if row is not None:
                    out[key] = row[0]
    except Exception:  # noqa: BLE001 — panel may be absent/locked
        pass
    return out


def _port_from_url(url: str | None) -> int | None:
    if not url:
        return None
    try:
        port = urlsplit(url).port
        return int(port) if port else None
    except (ValueError, TypeError):
        return None


def _panel_credentials() -> tuple[str | None, str | None]:
    """Login + PLAINTEXT password for the 3X-UI panel.

    x-ui.db stores the password as a bcrypt hash, and returning that made the
    Mini App print `$2a$10$…` in the "Password" field — unusable, and it reads
    like the real secret. The installer keeps the plaintext in
    /root/.govless_credentials (0600); use it, and never hand back a hash.
    """
    username = password = None
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if not sep:
                    continue
                value = value.strip().strip('"').strip("'")
                if key == "USERNAME" and value:
                    username = value
                elif key == "PASSWORD" and value:
                    password = value
    except OSError:
        pass
    if password and BCRYPT_RE.match(password):
        password = None  # a hash slipped into the file — still not a password
    if not username:
        try:
            import sqlite3 as _sqlite3
            with _sqlite3.connect("/etc/x-ui/x-ui.db", timeout=3) as conn:
                row = conn.execute("SELECT username FROM users LIMIT 1").fetchone()
                if row:
                    username = row[0]
        except Exception:  # noqa: BLE001
            pass
    return username, password


def _site_backups() -> list[dict]:
    out: list[dict] = []
    try:
        names = [n for n in os.listdir(SITE_BACKUP_DIR) if n.startswith("site_") and n.endswith(".tgz")]
    except OSError:
        return out
    for n in names:
        p = os.path.join(SITE_BACKUP_DIR, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"path": p, "name": n, "size": st.st_size, "ts": int(st.st_mtime)})
    out.sort(key=lambda b: b["ts"], reverse=True)
    return out


class Methods:
    """Dispatch table holder. Methods are async; daemon awaits them."""

    def __init__(self, *, state: StateDB | None = None, xui: XuiClient | None = None,
                 bot_env: dict[str, str] | None = None,
                 bot_env_loader: Callable[[], dict[str, str]] | None = None):
        self.state = state or StateDB()
        self.xui = xui or XuiClient()
        # cover-site template picker state (one background job at a time)
        self._site_catalog: Catalog | None = None
        self._site_job: dict | None = None
        self._site_task: asyncio.Task | None = None
        if bot_env_loader is not None:
            self._bot_env_loader = bot_env_loader
        elif bot_env is not None:
            self._bot_env_loader = lambda: dict(bot_env)
        else:
            self._bot_env_loader = load_bot_env

    @property
    def bot_env(self) -> dict[str, str]:
        return self._bot_env_loader()

    # ── system / read ────────────────────────────────────────────────
    def _read_panel_url_from_creds(self):
        try:
            with open("/root/.govless_credentials") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("URL="):
                        return line[len("URL="):]
        except OSError:
            pass
        return None

    async def system_status(self, params: dict, ctx: dict) -> dict:
        cfg = _read_config_json()
        # B-A3/B-A4 fix: panel_url from creds (preferred) or x-ui sqlite;
        # xray_active via process check (xray runs as x-ui child, not own systemd)
        panel_url = self._read_panel_url_from_creds() or cfg.get("panel_url") or self.xui.url
        # T3344: status probes shell out (systemctl/pgrep/xray -version/strings).
        # Run them in a worker thread so concurrent status polls don't serialize
        # on the event loop (was p95~10s, 78/100 timeouts at 100 concurrent).
        def _probe() -> dict:
            return {
                "xui_active": _systemctl_active("x-ui"),
                "xray_active": _proc_running("xray-linux-amd64") or _proc_running("xray"),
                "nginx_active": _systemctl_active("nginx"),
                "xray_version": _xray_version(),
                "xui_version": _xui_panel_version(),
            }
        probed = await asyncio.get_running_loop().run_in_executor(None, _probe)
        return {
            **probed,
            "panel_url": panel_url,
            "mode": cfg.get("mode", "lite"),
            # No invented default: "reality" is not even a valid transport
            # (tcp/xhttp/grpc) and it showed up on Pro/TCP boxes (QA BUG-34).
            "transport": cfg.get("transport") or None,
            "version": _govless_version(cfg),
        }

    async def client_list(self, params: dict, ctx: dict) -> list[dict]:
        inbound_id = params.get("inbound_id") if isinstance(params, dict) else None
        inbounds = read_inbounds_from_db()
        if inbound_id is not None:
            inbounds = [ib for ib in inbounds if ib.get("id") == int(inbound_id)]
        server = _server_host()
        traffics = read_client_traffics_from_db()
        cfg = _read_config_json()
        xui_settings = read_xui_settings_from_db()
        onlines: set[str] = set()
        try:
            onlines = set(await self.xui.get_onlines())
        except XuiError:
            pass  # offline: leave online flag as False

        links = build_vless_links(server, inbounds, only_enabled=True)
        index = {l["uuid"]: l for l in links}

        out: list[dict] = []
        for ib in inbounds:
            for cli in (ib.get("settings_obj") or {}).get("clients", []) or []:
                uid = cli.get("id")
                if not uid:
                    continue
                name = cli.get("email") or f"user-{str(uid).lower()[:8]}"
                tf = traffics.get(name) or {}
                link_info = index.get(uid, {})
                sub_url = _subscription_url_for_client(cli, cfg=cfg, settings=xui_settings)
                out.append(
                    {
                        "name": name,
                        "uuid": uid,
                        "inbound_id": ib.get("id"),
                        "enabled": bool(cli.get("enable", True)),
                        "online": name in onlines,
                        "traffic_used": int(tf.get("up", 0)) + int(tf.get("down", 0)),
                        "traffic_limit": int(cli.get("totalGB", 0)),
                        "expiry_ts": int(cli.get("expiryTime", 0)) // 1000 if cli.get("expiryTime") else 0,
                        "flow": cli.get("flow", ""),
                        "link": link_info.get("link", ""),
                        "sub_url": sub_url,
                    }
                )
        return out

    async def inbound_list(self, params: dict, ctx: dict) -> list[dict]:
        inbounds = read_inbounds_from_db()
        out = []
        for ib in inbounds:
            stream = ib.get("stream_obj") or {}
            client_count = len((ib.get("settings_obj") or {}).get("clients", []) or [])
            # Clients need the Reality parameters to configure an app by hand,
            # and the Mini App has a "Reality keys" block that could never render
            # because this method returned no stream at all (QA BUG-37).
            # privateKey is deliberately NOT exposed: it is the server's identity
            # and nothing outside xray has any use for it.
            reality = stream.get("realitySettings") or {}
            reality_public = {}
            if reality:
                inner = reality.get("settings") or {}
                reality_public = {
                    "publicKey": inner.get("publicKey"),
                    "fingerprint": inner.get("fingerprint"),
                    "dest": reality.get("dest"),
                    "serverNames": reality.get("serverNames") or [],
                    "shortIds": reality.get("shortIds") or [],
                }
            out.append(
                {
                    "id": ib.get("id"),
                    "port": ib.get("port"),
                    "protocol": ib.get("protocol"),
                    "network": stream.get("network"),
                    "security": stream.get("security"),
                    "enable": bool(ib.get("enable")),
                    "remark": ib.get("remark"),
                    "client_count": client_count,
                    "stream": {
                        "network": stream.get("network"),
                        "security": stream.get("security"),
                        "realitySettings": reality_public or None,
                    },
                }
            )
        return out

    async def panel_access_get(self, params: dict, ctx: dict) -> dict:
        cfg = _read_config_json()
        settings = _xui_web_settings()

        # Where the panel really listens. The marker file is only a hint left by
        # panel.access_set — an absent/empty one used to be read as "127.0.0.1"
        # and the screen showed a false "SSH-only" badge on a panel open to the
        # world. x-ui.db is the source of truth; empty webListen = all interfaces.
        listen = settings.get("webListen")
        if listen is None:
            listen = ""
            if os.path.exists(PANEL_ACCESS_FILE):
                try:
                    listen = open(PANEL_ACCESS_FILE).read().strip()
                except OSError:
                    listen = ""
        listen = (listen or "").strip()
        mode = "ssh-only" if listen in ("127.0.0.1", "localhost", "::1") else "public"

        # Real port: x-ui.db, then the saved panel URL, then the config. It used
        # to fall back to a hardcoded 2053 and the screen contradicted its own
        # URL line (which carries the real, random port).
        panel_url = self._read_panel_url_from_creds() or cfg.get("panel_url") or self.xui.url
        port = settings.get("webPort") or _port_from_url(panel_url) or cfg.get("panel_port") or 2053
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 2053

        username, password = _panel_credentials()
        return {
            "listen": listen or "0.0.0.0",
            "port": port,
            "url": panel_url,
            "mode": mode,
            "username": username,
            "password": password,
            # False → the plaintext password is not on this box any more (only a
            # bcrypt hash lives in x-ui.db); clients must say so instead of
            # printing a hash into the "Password" field.
            "password_available": bool(password),
        }

    async def audit_tail(self, params: dict, ctx: dict) -> list[dict]:
        limit = int(params.get("limit", 50)) if isinstance(params, dict) else 50
        return self.state.audit_tail(limit)

    async def admin_list(self, params: dict, ctx: dict) -> list[str]:
        # state ∪ env
        env_admins = set()
        for chunk in (self.bot_env.get("ADMIN_IDS", "") or "").replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                env_admins.add(chunk)
        all_ids = list(env_admins.union(set(self.state.get_admin_ids())))
        all_ids.sort()
        return all_ids

    async def tunnel_url_get(self, params: dict, ctx: dict) -> dict:
        pro_url = _pro_webapp_url(_read_config_json())
        if pro_url:
            return {"url": pro_url, "source": "pro"}
        for path, source in ((TUNNEL_URL_NAMED_FILE, "named"), (TUNNEL_URL_FILE, "quick")):
            if os.path.exists(path):
                try:
                    url = open(path).read().strip()
                    if url:
                        return {"url": url, "source": source}
                except OSError:
                    continue
        return {"url": None, "source": "none"}

    # ── client writes ────────────────────────────────────────────────
    async def client_add(self, params: dict, ctx: dict) -> dict:
        name = _require_name(params.get("name"))
        traffic_limit = params.get("traffic_limit")
        expiry_ts = params.get("expiry_ts")
        label = params.get("label")

        traffic_bytes = 0
        if traffic_limit is not None:
            traffic_bytes = _traffic_limit_to_bytes(traffic_limit, unit=params.get("traffic_unit"))
        expiry_ms = 0
        if expiry_ts:
            expiry_ms = _require_int(expiry_ts, "expiry_ts", min_v=0) * 1000

        inbounds = read_inbounds_from_db()
        active = _find_active_inbound(inbounds)
        # T3848: reject duplicate client name (3X-UI keys on email; silent dupes otherwise)
        _existing = {str(c.get("email", "")).strip().lower()
                     for c in (active.get("settings_obj") or {}).get("clients", []) or []}
        if name.strip().lower() in _existing:
            raise MethodError(409, f"client name already exists: {name}")
        active_stream = active.get("stream_obj") or {}
        active_security = active_stream.get("security")
        active_network = active_stream.get("network", "tcp")
        new_uuid = str(uuid_mod.uuid4())
        sub_id = secrets.token_urlsafe(12)
        client_obj = {
            "id": new_uuid,
            "email": name,
            "flow": "xtls-rprx-vision"
            if active_network == "tcp" and active_security in ("tls", "reality")
            else "",
            "limitIp": 0,
            "totalGB": traffic_bytes,
            "expiryTime": expiry_ms,
            "enable": True,
            "tgId": 0,
            "subId": sub_id,
            "comment": label or "",
        }
        await self.xui.add_client(active["id"], client_obj)

        # rebuild link
        refreshed = read_inbounds_from_db()
        server = _server_host()
        links = build_vless_links(server, refreshed)
        link = next((l["link"] for l in links if l["uuid"] == new_uuid), "")

        self.state.audit(
            ctx.get("tg_id"),
            "client.add",
            new_uuid,
            None,
            {
                "name": name,
                "inbound_id": active["id"],
                "traffic_bytes": traffic_bytes,
                "expiry_ms": expiry_ms,
            },
        )
        await self._notify("client_added", f"👤 Добавлен клиент: {name}")
        return {"uuid": new_uuid, "link": link, "qr_text": _qr_text(link) if link else ""}

    async def client_update(self, params: dict, ctx: dict) -> dict:
        uid = _require_uuid(params.get("uuid"))
        fields = params.get("fields") or {}
        if not isinstance(fields, dict) or not fields:
            raise MethodError(400, "fields required")
        inbounds = read_inbounds_from_db()
        ib, cli = _client_by_uuid(inbounds, uid)
        before = dict(cli)
        updated = dict(cli)

        # typed-confirm gate per CONTRACT
        if "traffic_limit" in fields:
            new_tl = _traffic_limit_to_bytes(fields["traffic_limit"], unit=fields.get("traffic_unit"))
            old_tl = int(cli.get("totalGB", 0))
            if old_tl > 0 and abs(new_tl - old_tl) / max(old_tl, 1) > 0.5:
                if not self._check_confirm(params, "UPDATE-TRAFFIC", uid):
                    return self._issue_confirm("UPDATE-TRAFFIC", uid, f"UPDATE-TRAFFIC {(cli.get('email') or f'user-{uid[:8]}')}")
            updated["totalGB"] = new_tl
        if "expiry_ts" in fields:
            new_ts = _require_int(fields["expiry_ts"], "expiry_ts", min_v=0)
            old_ts = int(cli.get("expiryTime", 0)) // 1000
            if new_ts and old_ts and (new_ts - old_ts) > 30 * 86400:
                if not self._check_confirm(params, "UPDATE-EXPIRY", uid):
                    return self._issue_confirm("UPDATE-EXPIRY", uid, f"UPDATE-EXPIRY {(cli.get('email') or f'user-{uid[:8]}')}")
            updated["expiryTime"] = new_ts * 1000 if new_ts else 0
        if "name" in fields:
            updated["email"] = _require_name(fields["name"])
        if "label" in fields:
            label = fields["label"]
            if not isinstance(label, str) or len(label) > 256:
                raise MethodError(400, "label must be string ≤256 chars")
            updated["comment"] = label

        await self.xui.update_client(ib["id"], uid, updated)
        self.state.audit(ctx.get("tg_id"), "client.update", uid, before, updated)
        return {"ok": True}

    async def client_enable(self, params: dict, ctx: dict) -> dict:
        uid = _require_uuid(params.get("uuid"))
        inbounds = read_inbounds_from_db()
        ib, cli = _client_by_uuid(inbounds, uid)
        before = {"enable": cli.get("enable", True)}
        updated = dict(cli, enable=True)
        await self.xui.update_client(ib["id"], uid, updated)
        self.state.audit(ctx.get("tg_id"), "client.enable", uid, before, {"enable": True})
        return {"ok": True}

    async def client_disable(self, params: dict, ctx: dict) -> dict:
        uid = _require_uuid(params.get("uuid"))
        inbounds = read_inbounds_from_db()
        ib, cli = _client_by_uuid(inbounds, uid)
        # check if this is the last admin's client → typed-confirm
        admin_ids = set(self.state.get_admin_ids())
        if cli.get("tgId") and str(cli["tgId"]) in admin_ids:
            # count enabled clients owned by admins
            owned_enabled = 0
            for ib2 in inbounds:
                for c in (ib2.get("settings_obj") or {}).get("clients", []) or []:
                    if str(c.get("tgId", "")) in admin_ids and c.get("enable") and c.get("id") != uid:
                        owned_enabled += 1
            if owned_enabled == 0:
                if not self._check_confirm(params, "DISABLE-LAST", uid):
                    return self._issue_confirm("DISABLE-LAST", uid, "DISABLE LAST")

        before = {"enable": cli.get("enable", True)}
        updated = dict(cli, enable=False)
        await self.xui.update_client(ib["id"], uid, updated)
        self.state.audit(ctx.get("tg_id"), "client.disable", uid, before, {"enable": False})
        return {"ok": True}

    async def client_reset_traffic(self, params: dict, ctx: dict) -> dict:
        uid = _require_uuid(params.get("uuid"))
        inbounds = read_inbounds_from_db()
        ib, cli = _client_by_uuid(inbounds, uid)
        expected = f"RESET {(cli.get('email') or f'user-{uid[:8]}')}"
        if not self._check_confirm(params, "RESET-TRAFFIC", uid):
            return self._issue_confirm("RESET-TRAFFIC", uid, expected)
        await self.xui.reset_client_traffic(ib["id"], cli.get("email", ""))
        self.state.audit(ctx.get("tg_id"), "client.reset_traffic", uid, None, {"email": cli.get("email")})
        return {"ok": True}

    async def client_delete(self, params: dict, ctx: dict) -> dict:
        uid = _require_uuid(params.get("uuid"))
        inbounds = read_inbounds_from_db()
        ib, cli = _client_by_uuid(inbounds, uid)
        expected = f"DELETE {(cli.get('email') or f'user-{uid[:8]}')}"
        if not self._check_confirm(params, "DELETE-CLIENT", uid):
            return self._issue_confirm("DELETE-CLIENT", uid, expected)
        await self.xui.delete_client(ib["id"], uid, cli.get("email", ""))
        self.state.audit(ctx.get("tg_id"), "client.delete", uid, dict(cli), None)
        return {"ok": True}

    async def client_qr(self, params: dict, ctx: dict) -> dict:
        uid = _require_uuid(params.get("uuid"))
        prefer_subscription = bool(params.get("prefer_subscription", True))
        inbounds = read_inbounds_from_db()
        _, cli = _client_by_uuid(inbounds, uid)
        sub_url = _subscription_url_for_client(cli)
        if prefer_subscription and sub_url:
            return {
                "qr_text": _qr_text(sub_url),
                "qr_svg": _qr_svg(sub_url),
                "link": sub_url,
                "kind": "subscription",
                "sub_url": sub_url,
            }
        server = _server_host()
        links = build_vless_links(server, inbounds)
        link = next((l["link"] for l in links if l["uuid"] == uid), None)
        if not link:
            raise MethodError(404, f"no link for client {uid}")
        return {"qr_text": _qr_text(link), "qr_svg": _qr_svg(link), "link": link, "kind": "vless", "sub_url": sub_url}

    async def client_sub_url(self, params: dict, ctx: dict) -> dict:
        uid = _require_uuid(params.get("uuid"))
        inbounds = read_inbounds_from_db()
        _, cli = _client_by_uuid(inbounds, uid)
        return {"sub_url": _subscription_url_for_client(cli)}

    async def subscription_rotate(self, params: dict, ctx: dict) -> dict:
        uid = _require_uuid(params.get("uuid"))
        inbounds = read_inbounds_from_db()
        ib, cli = _client_by_uuid(inbounds, uid)
        expected = f"ROTATE SUB {(cli.get('email') or f'user-{uid[:8]}')}"
        if not self._check_confirm(params, "ROTATE-SUB", uid):
            return self._issue_confirm("ROTATE-SUB", uid, expected)
        new_sub = secrets.token_urlsafe(12)
        updated = dict(cli, subId=new_sub)
        await self.xui.update_client(ib["id"], uid, updated)
        self.state.audit(ctx.get("tg_id"), "subscription.rotate", uid,
                         {"subId": cli.get("subId")}, {"subId": new_sub})
        return {"new_sub_url": _subscription_url_for_client(updated)}

    # ── inbound write ────────────────────────────────────────────────
    async def inbound_toggle(self, params: dict, ctx: dict) -> dict:
        ib_id = _require_int(params.get("inbound_id"), "inbound_id", min_v=1)
        enable = bool(params.get("enable"))
        if not enable:
            if not self._check_confirm(params, "DISABLE-INBOUND", str(ib_id)):
                return self._issue_confirm("DISABLE-INBOUND", str(ib_id), f"DISABLE INBOUND {ib_id}")
        await self.xui.toggle_inbound(ib_id, enable)
        self.state.audit(ctx.get("tg_id"), "inbound.toggle", str(ib_id),
                         None, {"enable": enable})
        return {"ok": True}

    # ── panel access ─────────────────────────────────────────────────
    async def panel_access_set(self, params: dict, ctx: dict) -> dict:
        mode = params.get("mode")
        if mode not in ("public", "ssh-only"):
            raise MethodError(400, "mode must be 'public' or 'ssh-only'")
        if mode == "ssh-only":
            if not self._check_confirm(params, "PANEL-ACCESS", mode):
                return self._issue_confirm("PANEL-ACCESS", mode, "LOCKOUT-RISK PROCEED")

        os.makedirs(os.path.dirname(PANEL_ACCESS_FILE), exist_ok=True)
        listen = "127.0.0.1" if mode == "ssh-only" else "0.0.0.0"
        with open(PANEL_ACCESS_FILE, "w", encoding="utf-8") as f:
            f.write(listen)
        # Agent-03: actually push to 3X-UI sqlite (webListen) + restart x-ui.
        try:
            import sqlite3 as _sqlite3
            current = None
            with _sqlite3.connect("/etc/x-ui/x-ui.db", timeout=5) as conn:
                _row = conn.execute(
                    "SELECT value FROM settings WHERE key='webListen' LIMIT 1"
                ).fetchone()
                if _row is not None:
                    current = _row[0]
                # A fresh install has no webListen row at all (3X-UI writes it
                # only when settings are saved from its UI), so UPDATE matched
                # nothing, the panel stayed on 0.0.0.0 and the UI still showed
                # «ssh-only» (QA S-07). Replace the row the way configure_panel_tls does.
                conn.execute("DELETE FROM settings WHERE key='webListen'")
                conn.execute("INSERT INTO settings (key, value) VALUES ('webListen', ?)", (listen,))
                conn.commit()
            # No-op debounce: if webListen already matches the requested value,
            # skip the restart entirely. Repeated restarts trip systemd's
            # start-limit and leave x-ui in `failed` (caused a real outage).
            if current != listen:
                import subprocess as _sp
                # Clear any prior start-limit/failed state so the restart isn't
                # rejected by systemd ("start request repeated too quickly").
                _sp.run(["systemctl", "reset-failed", "x-ui"], check=False, timeout=10)
                _sp.run(["systemctl", "restart", "x-ui"], check=False, timeout=10)
        except Exception as _e:
            # don't fail the RPC — marker file is still authoritative
            log.warning("panel.access_set: sqlite/x-ui update failed: %s", _e)
        self.state.audit(ctx.get("tg_id"), "panel.access_set", mode, None, {"listen": listen})
        return {"ok": True}

    async def panel_creds_rotate(self, params: dict, ctx: dict) -> dict:
        """Issue a new panel login+password and write them everywhere.

        The Mini App has had a "Rotate credentials" button since 1.3, but the
        method behind it never existed — pressing it answered `-32601 method not
        found` (QA BUG-28). Implemented via the panel's own CLI so the password
        is hashed exactly the way 3X-UI expects.
        """
        if not self._check_confirm(params, "ROTATE-PANEL", "panel"):
            return self._issue_confirm(
                "ROTATE-PANEL", "panel", "ROTATE PANEL",
                description="Выдать новые логин и пароль панели? Старые перестанут работать сразу, "
                            "открытые сессии закроются. / Issue a new panel login and password? "
                            "The old ones stop working immediately and open sessions are dropped.",
            )
        # NOT shutil.which("x-ui"): that resolves to /usr/bin/x-ui, the 3X-UI
        # management *shell script*, which does not implement «setting
        # -username -password» and exits 0 without changing anything. The old
        # code then wrote fresh credentials into the file and showed them to the
        # operator while the panel kept the previous ones — lockout for the human,
        # «login failed» for every write RPC (found live on the stand, 22.09.2026).
        xui_bin = _xui_real_binary()
        if not xui_bin:
            raise MethodError(500, "x-ui binary not found — cannot rotate panel credentials")
        old_user, old_pass = self.xui.username, self.xui.password

        alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        new_user = "".join(secrets.choice(alphabet) for _ in range(10))
        new_pass = "".join(secrets.choice(alphabet) for _ in range(16))

        proc = await asyncio.create_subprocess_exec(
            xui_bin, "setting", "-username", new_user, "-password", new_pass,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            raise MethodError(504, "x-ui setting timed out")
        if proc.returncode != 0:
            raise MethodError(500, f"x-ui setting failed: {err.decode(errors='replace')[:200]}")
        # «Open sessions are dropped» (the confirm text) only holds with a new
        # session key; the restart below applies it.
        _rotate_panel_session_key()

        # Prove the new pair works BEFORE anything is persisted or shown: the
        # panel re-reads users after a restart, so restart, log in over loopback,
        # and roll back to the previous pair if the login fails.
        subprocess.run(["systemctl", "restart", "x-ui"], check=False, timeout=30)
        await asyncio.sleep(3)
        self.xui.username, self.xui.password = new_user, new_pass
        self.xui.creds.update({"USERNAME": new_user, "PASSWORD": new_pass})
        self.xui._logged_in_at = 0.0
        await self.xui.close()
        try:
            await self.xui._login()
        except Exception as exc:  # noqa: BLE001 — roll back, then report
            self.xui.username, self.xui.password = old_user, old_pass
            self.xui.creds.update({"USERNAME": old_user, "PASSWORD": old_pass})
            self.xui._logged_in_at = 0.0
            await self.xui.close()
            subprocess.run([xui_bin, "setting", "-username", old_user, "-password", old_pass],
                           capture_output=True, text=True, timeout=60)
            subprocess.run(["systemctl", "restart", "x-ui"], check=False, timeout=30)
            raise MethodError(500, f"new panel credentials did not work ({exc}); the previous ones were restored")

        # Keep /root/.govless_credentials in step — it is what the SSH menu
        # prints and what panel.access_get hands to the Mini App.
        try:
            lines: list[str] = []
            seen = set()
            if os.path.exists(CREDENTIALS_FILE):
                with open(CREDENTIALS_FILE, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        key = line.split("=", 1)[0].strip()
                        if key == "USERNAME":
                            lines.append(f"USERNAME={new_user}\n"); seen.add("USERNAME")
                        elif key == "PASSWORD":
                            lines.append(f"PASSWORD={new_pass}\n"); seen.add("PASSWORD")
                        else:
                            lines.append(line if line.endswith("\n") else line + "\n")
            for key, value in (("USERNAME", new_user), ("PASSWORD", new_pass)):
                if key not in seen:
                    lines.append(f"{key}={value}\n")
            tmp = CREDENTIALS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(lines)
            os.chmod(tmp, 0o600)
            os.replace(tmp, CREDENTIALS_FILE)
        except OSError as exc:
            log.warning("panel.creds_rotate: could not update %s: %s", CREDENTIALS_FILE, exc)


        subprocess.run(["systemctl", "restart", "x-ui"], check=False, timeout=30)
        self.state.audit(ctx.get("tg_id"), "panel.creds_rotate", "panel", None, {"username": new_user})
        await self._notify("panel_creds_rotated",
                           f"🔐 Панель: выданы новые логин и пароль (логин {new_user}). "
                           f"Старые больше не работают.")
        cfg = _read_config_json()
        return {
            "ok": True,
            "username": new_user,
            "password": new_pass,
            "url": self._read_panel_url_from_creds() or cfg.get("panel_url") or self.xui.url,
        }

    # ── cert ─────────────────────────────────────────────────────────
    async def cert_force_renew(self, params: dict, ctx: dict) -> dict:
        target = params.get("domain_or_ip")
        if not isinstance(target, str) or not target.strip():
            raise MethodError(400, "domain_or_ip required")
        target = target.strip()
        # very tight allow-list: letters/digits/dot/dash/colon (for IPv6)
        if not re.match(r"^[A-Za-z0-9_.:\-]{1,253}$", target):
            raise MethodError(400, "domain_or_ip contains illegal characters")
        if params.get("confirm") != f"RENEW {target}":
            raise MethodError(412, f"typed-confirm required: RENEW {target}")

        # Let's Encrypt issues at most 5 duplicate certificates per week: a few
        # «just in case» clicks in the Mini App burn the quota and then even the
        # scheduled renewal fails (QA S-24). Refuse while the certificate still
        # has more than 30 days; it renews itself before that anyway.
        if not params.get("force_even_if_fresh"):
            pem = f"/etc/letsencrypt/live/{target}/cert.pem"
            if os.path.exists(pem):
                try:
                    import datetime as _dt
                    out = subprocess.run(["openssl", "x509", "-in", pem, "-noout", "-enddate"],
                                         capture_output=True, text=True, timeout=10).stdout.strip()
                    end = _dt.datetime.strptime(out.split("=", 1)[1], "%b %d %H:%M:%S %Y %Z")
                    left = (end - _dt.datetime.utcnow()).days
                    if left > 30:
                        raise MethodError(409, f"certificate for {target} is still valid for {left} days — "
                                               "forced renewal refused (Let's Encrypt allows 5 duplicates a week); "
                                               "it renews automatically ~30 days before expiry")
                except MethodError:
                    raise
                except Exception as exc:  # noqa: BLE001 — never block on a parsing hiccup
                    log.warning("cert.force_renew: freshness check skipped: %s", exc)

        # Try acme.sh first, fall back to certbot.
        acme = shutil.which("acme.sh")
        cmd = None
        if acme:
            cmd = [acme, "--renew", "-d", target, "--force"]
        else:
            certbot = shutil.which("certbot")
            if certbot:
                cmd = [certbot, "renew", "--cert-name", target, "--force-renewal", "--non-interactive"]
        if not cmd:
            raise MethodError(500, "no ACME client available (acme.sh or certbot)")

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        ok = proc.returncode == 0
        new_expiry = None
        try:
            from datetime import datetime, timezone
            # best-effort: read /etc/letsencrypt/live/<target>/cert.pem
            cert_path = f"/etc/letsencrypt/live/{target}/cert.pem"
            if os.path.exists(cert_path):
                import ssl
                der_data = ssl._ssl._test_decode_cert(cert_path)  # type: ignore[attr-defined]
                exp = der_data.get("notAfter")
                if exp:
                    new_expiry = exp
        except Exception:
            new_expiry = None

        self.state.audit(ctx.get("tg_id"), "cert.force_renew", target,
                         None, {"ok": ok, "stderr": stderr.decode(errors="replace")[:500]})
        if not ok:
            raise MethodError(500, f"renew failed: {stderr.decode(errors='replace')[:300]}")
        return {"ok": True, "new_expiry": new_expiry}

    # ── admin ────────────────────────────────────────────────────────
    async def admin_claim(self, params: dict, ctx: dict) -> dict:
        token = params.get("token")
        if not isinstance(token, str) or not token:
            raise MethodError(400, "token required")
        expected = self.bot_env.get("ADMIN_CLAIM_TOKEN", "")
        if not expected:
            raise MethodError(500, "ADMIN_CLAIM_TOKEN not configured")
        # For local caller without tg_id, allow param tg_id; otherwise use ctx.
        tg_id = ctx.get("tg_id") or str(params.get("tg_id") or "")
        if not tg_id:
            raise MethodError(400, "tg_id missing")
        ok = self.state.admin_claim_atomic(token, expected, tg_id)
        if not ok:
            raise MethodError(409, "admin already claimed or token mismatch")
        self.state.audit(tg_id, "admin.claim", tg_id, None, {"tg_id": tg_id})
        return {"ok": True}


    async def admin_claim_first(self, params, ctx):
        """First /start claims admin without token (UX simplification).
        Atomic at SQLite level — concurrent /start are race-safe."""
        tg_id = ctx.get("tg_id") or str(params.get("tg_id") or "")
        if not tg_id:
            raise MethodError(400, "tg_id required")
        ok = self.state.admin_claim_first_atomic(tg_id)
        if not ok:
            raise MethodError(409, "admin already claimed")
        self.state.audit(tg_id, "admin.claim_first", tg_id, None, {"tg_id": tg_id, "via": "first-start"})
        return {"ok": True}

    async def admin_invite(self, params: dict, ctx: dict) -> dict:
        tg_id = params.get("tg_id")
        if not (isinstance(tg_id, (str, int)) and str(tg_id).isdigit()):
            raise MethodError(400, "tg_id must be a positive integer")
        added = self.state.invite_admin(str(tg_id), invited_by=ctx.get("tg_id"))
        # idempotent: success either way
        if added:
            self.state.audit(ctx.get("tg_id"), "admin.invite", str(tg_id), None, {"tg_id": str(tg_id)})
        return {"ok": True, "added": added}

    # ── notify-admins ────────────────────────────────────────────────
    async def notify_admins(self, params: dict, ctx: dict) -> dict:
        message = params.get("message")
        severity = params.get("severity", "info")
        if not isinstance(message, str) or not message.strip():
            raise MethodError(400, "message required")
        # Daemon writes the request to a queue file; bot's local-socket
        # handler reads & forwards. We do NOT call Telegram directly from
        # the daemon (separation of concerns per CONTRACT).
        event = params.get("event") if isinstance(params.get("event"), str) else "manual"
        await self._notify(event, message, severity)
        self.state.audit(ctx.get("tg_id"), "notify-admins", severity, None, {"message": message[:200]})
        return {"sent": True}

    # ── BACKUP / REPAIR / RESTART / NOTIFICATIONS (Etap 2+3) ──────────
    async def backup_create(self, params, ctx):
        import subprocess, glob
        try:
            res = subprocess.run(_lib_bash("backup_govless"),
                capture_output=True, text=True, timeout=120)
            if res.returncode != 0:
                raise MethodError(500, "backup failed: " + (res.stderr[-200:] or res.stdout[-200:] or "rc!=0"))
            files = sorted(glob.glob("/root/govless-backups/*.tgz"), key=lambda f: os.path.getmtime(f), reverse=True)
            if not files:
                raise MethodError(500, "backup ran but no .tgz produced")
            self.state.audit(ctx.get("tg_id"), "backup.create", files[0], None, {"path": files[0]})
            return {"path": files[0], "name": os.path.basename(files[0]),
                    "size": os.path.getsize(files[0]), "ts": int(os.path.getmtime(files[0]))}
        except subprocess.TimeoutExpired:
            raise MethodError(504, "backup timed out after 120s")

    async def backup_list(self, params, ctx):
        import glob
        files = sorted(glob.glob("/root/govless-backups/*.tgz"), key=lambda f: os.path.getmtime(f), reverse=True)
        out = []
        for f in files:
            try:
                st = os.stat(f)
                out.append({"path": f, "name": os.path.basename(f), "size": st.st_size, "ts": int(st.st_mtime)})
            except OSError:
                pass
        return {"backups": out}

    async def backup_restore(self, params, ctx):
        import subprocess
        path = params.get("path")
        if not isinstance(path, str) or not path.startswith("/root/govless-backups/") or ".." in path:
            raise MethodError(400, "invalid backup path")
        if not os.path.isfile(path):
            raise MethodError(404, "backup file not found")
        name = os.path.basename(path)
        if not self._check_confirm(params, "RESTORE", path):
            return self._issue_confirm("RESTORE", path, "RESTORE " + name,
                description="Восстановить из «" + name + "»? Текущее состояние будет перезаписано.")
        try:
            res = subprocess.run(_lib_bash("restore_govless " + shlex.quote(path)),
                capture_output=True, text=True, timeout=180)
            if res.returncode != 0:
                raise MethodError(500, "restore failed: " + (res.stderr[-200:] or res.stdout[-200:] or "rc!=0"))
            self.state.audit(ctx.get("tg_id"), "backup.restore", path, None, {"path": path})
            return {"ok": True, "stdout": res.stdout[-500:]}
        except subprocess.TimeoutExpired:
            raise MethodError(504, "restore timed out after 180s")

    async def repair_run(self, params, ctx):
        import subprocess
        try:
            res = subprocess.run(_lib_bash("repair_user_facing"),
                capture_output=True, text=True, timeout=60)
            self.state.audit(ctx.get("tg_id"), "repair.run", None, None, {"rc": res.returncode})
            return {"ok": res.returncode == 0, "stdout": res.stdout[-800:], "stderr": res.stderr[-400:]}
        except subprocess.TimeoutExpired:
            raise MethodError(504, "repair timed out after 60s")

    async def system_restart(self, params, ctx):
        import subprocess
        if not self._check_confirm(params, "RESTART", "system"):
            return self._issue_confirm("RESTART", "system", "RESTART",
                description="Перезапустить x-ui, govlessctl и govless-bot? Mini App станет недоступен ~10 секунд.")
        subprocess.Popen(["bash","-c", "sleep 2 && systemctl restart x-ui govlessctl govless-bot"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.state.audit(ctx.get("tg_id"), "system.restart", None, None, {"scheduled": True})
        return {"ok": True, "delay_s": 2}

    # ── notifications ────────────────────────────────────────────────
    # Only events that something actually raises may appear here: the Mini App
    # used to show five switches (four ON by default) that changed nothing —
    # nobody read the prefs file and nobody read the queue (QA BUG-26/27).
    NOTIFY_EVENTS = (
        "tunnel_url_changed",     # bot: Mini App URL changed
        "site_template_changed",  # daemon: cover-site template applied/failed
        "client_added",           # daemon: client.add
        "panel_creds_rotated",    # daemon: panel.creds_rotate
        "mode_switched",          # installer: Lite <-> Pro (via `govlessctl notify-admins`)
        "daemon_restart",         # daemon: startup
    )
    NOTIFY_DEFAULTS = {
        "tunnel_url_changed": True,
        "site_template_changed": True,
        "client_added": True,
        "panel_creds_rotated": True,
        "mode_switched": True,
        "daemon_restart": False,
    }

    async def _notify(self, event: str, message: str, severity: str = "info") -> None:
        """Queue a notification for the bot to deliver. Never raises."""
        try:
            import json as _json
            os.makedirs("/run/govless", exist_ok=True)
            with open(NOTIFY_QUEUE, "a", encoding="utf-8") as f:
                f.write(_json.dumps({"ts": int(time.time()), "event": event,
                                     "severity": severity, "message": message}) + "\n")
            os.chmod(NOTIFY_QUEUE, 0o660)   # group govless must truncate after delivery
            try:
                import grp
                os.chown(NOTIFY_QUEUE, 0, grp.getgrnam("govless").gr_gid)
            except (KeyError, PermissionError, OSError):
                pass
        except OSError as exc:
            log.warning("notify queue write failed: %s", exc)

    async def notifications_get(self, params, ctx):
        tg = str(ctx.get("tg_id") or "")
        if not tg:
            # No Telegram identity (CLI / local call): no personal prefs, but the
            # catalogue of events and the defaults still make sense to return.
            return {"prefs": dict(self.NOTIFY_DEFAULTS), "events": list(self.NOTIFY_EVENTS)}
        path = "/etc/govless/notify." + tg + ".json"
        prefs = dict(self.NOTIFY_DEFAULTS)
        try:
            import json
            with open(path) as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                prefs.update({k: bool(v) for k, v in saved.items() if k in self.NOTIFY_DEFAULTS})
        except (FileNotFoundError, ValueError, OSError):
            pass
        # `events` lets the client render exactly what the server can deliver
        # instead of hardcoding a list that drifts out of sync.
        return {"prefs": prefs, "events": list(self.NOTIFY_EVENTS)}

    async def notifications_set(self, params, ctx):
        tg = str(ctx.get("tg_id") or "")
        if not tg:
            raise MethodError(401, "auth required")
        prefs = params.get("prefs")
        if not isinstance(prefs, dict):
            raise MethodError(400, "prefs object required")
        clean = {k: bool(v) for k, v in prefs.items() if k in self.NOTIFY_DEFAULTS}
        if not clean:
            raise MethodError(400, "no known notification keys in prefs")
        os.makedirs("/etc/govless", exist_ok=True)
        path = "/etc/govless/notify." + tg + ".json"
        import json
        with open(path, "w") as f:
            json.dump(clean, f)
        os.chmod(path, 0o640)
        self.state.audit(ctx.get("tg_id"), "notifications.set", tg, None, clean)
        merged = dict(self.NOTIFY_DEFAULTS); merged.update(clean)
        return {"ok": True, "prefs": merged, "events": list(self.NOTIFY_EVENTS)}

    # ── cover site templates (Pro) ───────────────────────────────────
    def _catalog(self) -> Catalog:
        path = _site_catalog_path()
        if not path:
            raise MethodError(503, "templates catalog not found on this server — run `govless` once (it syncs the catalog)")
        if self._site_catalog is None or self._site_catalog.path != path:
            self._site_catalog = Catalog(path)
        return self._site_catalog

    def _site_job_public(self) -> dict | None:
        job = self._site_job
        if not job:
            return None
        return {k: v for k, v in job.items() if k not in ("admin_tg",)}

    def _site_available(self, cfg: dict | None = None) -> tuple[bool, dict]:
        cfg = cfg or _read_config_json()
        return (cfg.get("mode") == "pro" and bool(cfg.get("domain"))), cfg

    async def site_template_status(self, params: dict, ctx: dict) -> dict:
        available, cfg = self._site_available()
        domain = str(cfg.get("domain") or "")
        cur = _site_current()
        current: dict | None = None
        if cur["id"] != "__none__":
            kind = ("stub" if cur["id"] == SITE_STUB_ID else
                    "custom" if cur["id"].startswith("custom_") else
                    "unknown" if cur["id"] == "__unknown__" else "catalog")
            current = {"id": cur["id"], "kind": kind, "source_url": cur["source_url"] or None, "template": None}
            if kind == "catalog":
                try:
                    rec = self._catalog().get(cur["id"])
                    if rec:
                        current["template"] = _site_public(rec)
                except MethodError:
                    pass
        try:
            cat = self._catalog()
            catalog = {"ok": True, "total": cat.total(), "categories": len(cat.categories())}
        except MethodError as e:
            catalog = {"ok": False, "total": 0, "categories": 0, "error": e.message}
        return {
            "available": available,
            "mode": cfg.get("mode", "lite"),
            "domain": domain or None,
            "site_url": f"https://{domain}" if (available and domain) else None,
            "current": current,
            "catalog": catalog,
            "job": self._site_job_public(),
            "backups": _site_backups(),
        }

    async def site_template_categories(self, params: dict, ctx: dict) -> list[dict]:
        cat = self._catalog()
        return [
            {"id": cid, "name": name, "name_en": SITE_CATEGORY_EN.get(cid, name), "count": n}
            for cid, name, n in cat.categories()
        ]

    async def site_template_list(self, params: dict, ctx: dict) -> dict:
        cat = self._catalog()
        category = params.get("category")
        q = params.get("q")
        page = _require_int(params.get("page", 0), "page", min_v=0)
        per = max(1, min(_require_int(params.get("per", 24), "per", min_v=1), 50))
        if category is not None:
            if not isinstance(category, str) or not SITE_CATEGORY_RE.match(category):
                raise MethodError(400, "invalid category")
            if category not in {c[0] for c in cat.categories()}:
                raise MethodError(404, f"category not found: {category}")
        if q is not None:
            if not isinstance(q, str):
                raise MethodError(400, "q must be a string")
            q = q.strip()[:64]
            if not q:
                q = None
        if q:
            found = cat.search(q, category, limit=500)
            total = len(found)
            pages = max(1, (total + per - 1) // per)
            page = min(page, pages - 1)
            items = found[page * per:(page + 1) * per]
        else:
            if not category:
                raise MethodError(400, "category or q required")
            items, pages = cat.page(category, page, per)
            total = len(cat.templates(category))
            page = min(page, pages - 1)
            items = [dict(t, category=category) for t in items]
        return {
            "category": category, "q": q, "page": page, "pages": pages, "per": per, "total": total,
            "items": [_site_public(t) for t in items],
        }

    def _site_resolve(self, params: dict) -> dict:
        cat = self._catalog()
        tid = params.get("template_id")
        key = params.get("key")
        rec = None
        if isinstance(tid, str) and tid:
            if not SITE_TEMPLATE_ID_RE.match(tid):
                raise MethodError(400, "invalid template_id")
            rec = cat.get(tid)
        elif isinstance(key, str) and key:
            if not SITE_KEY_RE.match(key):
                raise MethodError(400, "invalid key")
            rec = cat.get_by_key(key)
        else:
            raise MethodError(400, "template_id or key required")
        if not rec:
            raise MethodError(404, "template not found in the catalog")
        return rec

    async def site_template_get(self, params: dict, ctx: dict) -> dict:
        return {"template": _site_public(self._site_resolve(params))}

    async def site_template_random(self, params: dict, ctx: dict) -> dict:
        cat = self._catalog()
        category = params.get("category")
        if category is not None:
            if not isinstance(category, str) or not SITE_CATEGORY_RE.match(category):
                raise MethodError(400, "invalid category")
            if category not in {c[0] for c in cat.categories()}:
                raise MethodError(404, f"category not found: {category}")
        rec = cat.random(category or None)
        if not rec:
            raise MethodError(404, "catalog is empty")
        if "category" not in rec:
            rec = dict(rec, category=category or "")
        return {"template": _site_public(rec)}

    async def site_template_apply(self, params: dict, ctx: dict) -> dict:
        available, cfg = self._site_available()
        if not available:
            raise MethodError(409, "cover site is available in Pro mode only (own domain + TLS)")
        if self._site_job and self._site_job.get("state") == "running":
            raise MethodError(409, "a template change is already running", {"job": self._site_job_public()})
        installer = _installer_script()
        if not installer:
            raise MethodError(500, "govless.sh not found (expected /opt/govless-installer or /root/goVLESS)")

        # Client language for the installer's messages (bot = en, Mini App = the
        # user's): without it the bot showed RU error texts inside EN screens (QA BUG-06).
        lang = params.get("lang")
        if lang not in ("ru", "en"):
            lang = None

        action = "apply"
        target = ""
        name = ""
        args: list[str]
        if params.get("stub"):
            action, target, name = "stub", SITE_STUB_ID, "built-in stub page"
            args = ["--site-template-stub"]
        elif params.get("random"):
            action, target, name = "random", "random", "random catalog template"
            args = ["--site-template-random"]
        elif params.get("custom_url"):
            url = params.get("custom_url")
            if not isinstance(url, str) or not SITE_CUSTOM_URL_RE.match(url.strip()) or any(ch in url for ch in " \t\r\n;`$()<>|\\&'\""):
                raise MethodError(400, "custom_url must be a public https:// git repository URL")
            url = url.strip()
            action, target, name = "custom", url, url
            args = ["--site-template-apply-url", url]
        elif params.get("restore"):
            path = params.get("restore")
            if (not isinstance(path, str) or ".." in path
                    or not path.startswith(SITE_BACKUP_DIR + "/site_") or not path.endswith(".tgz")
                    or not os.path.isfile(path)):
                raise MethodError(400, "invalid backup path")
            action, target, name = "restore", path, os.path.basename(path)
            args = ["--site-template-restore", path]
        else:
            rec = self._site_resolve(params)
            action, target, name = "apply", str(rec["id"]), str(rec.get("name") or rec["id"])
            args = ["--site-template-apply", target]

        job = {
            "id": secrets.token_hex(6),
            "state": "running",
            "action": action,
            "target": target,
            "name": name,
            "started_ts": int(time.time()),
            "finished_ts": None,
            "ok": None,
            "template_id": None,
            "error_code": None,
            "error": None,
            "http_code": None,
            "backup": None,
            "log": "",
            "lang": lang,
            "admin_tg": ctx.get("tg_id"),
        }
        self._site_job = job
        self._site_task = asyncio.get_running_loop().create_task(self._site_job_run(job, installer, args))
        self.state.audit(ctx.get("tg_id"), "site.template_apply", target,
                         {"current": _site_current()["id"]}, {"job_id": job["id"], "action": action})
        return {"job": self._site_job_public()}

    async def _site_job_run(self, job: dict, installer: str, args: list[str]) -> None:
        cmd = ["/bin/bash", installer, *args, "--json"]
        env = dict(os.environ)
        env.update({
            "GOVLESS_NONINTERACTIVE": "1",
            "GOVLESS_LANG": job.get("lang") or "",
            "TERM": "dumb",
            "LANG": env.get("LANG") or "C.UTF-8",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        })
        stdout = b""
        stderr = b""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=os.path.dirname(installer),
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SITE_JOB_TIMEOUT)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                job.update(state="failed", ok=False, error_code="timeout",
                           error=f"template job timed out after {SITE_JOB_TIMEOUT}s")
                job["finished_ts"] = int(time.time())
                self.state.audit(job.get("admin_tg"), "site.template_result", job["target"], None,
                                 {"ok": False, "error_code": "timeout", "job_id": job["id"]})
                return
        except Exception as exc:  # noqa: BLE001 — spawn failure
            job.update(state="failed", ok=False, error_code="spawn", error=str(exc)[:300])
            job["finished_ts"] = int(time.time())
            return

        import json as _json
        result: dict | None = None
        for line in reversed(stdout.decode("utf-8", errors="replace").splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    parsed = _json.loads(line)
                    if isinstance(parsed, dict):
                        result = parsed
                        break
                except ValueError:
                    continue
        log_tail = _ANSI_RE.sub("", stderr.decode("utf-8", errors="replace")).strip()[-1500:]
        job["log"] = log_tail
        if result is None:
            job.update(state="failed", ok=False, error_code="no_result",
                       error=(log_tail.splitlines()[-1] if log_tail else f"installer exited with code {proc.returncode}")[:300])
        else:
            ok = bool(result.get("ok"))
            name = result.get("name") or job.get("name")
            if job.get("action") == "stub":
                # The installer localizes this name; pin it to the caller's
                # language here so an English screen can't show a Russian one
                # (QA BUG-06 leftover) regardless of the server's saved locale.
                name = "стандартная заглушка" if job.get("lang") == "ru" else "built-in stub page"
            job.update(
                state="done" if ok else "failed",
                ok=ok,
                template_id=result.get("template_id") or result.get("current_id") or None,
                name=name,
                error_code=(result.get("error_code") or None) if not ok else None,
                error=(result.get("error") or None) if not ok else None,
                http_code=result.get("http_code") or None,
                backup=result.get("backup") or None,
            )
        job["finished_ts"] = int(time.time())
        self.state.audit(job.get("admin_tg"), "site.template_result", job.get("template_id") or job["target"], None,
                         {"ok": job["ok"], "error_code": job["error_code"], "job_id": job["id"]})
        if job["ok"]:
            await self._notify("site_template_changed",
                               f"🌐 Сайт-маскировка обновлена: {job.get('name') or job.get('template_id')}")
        else:
            await self._notify("site_template_changed",
                               f"🌐 Не удалось сменить сайт-маскировку ({job.get('error_code') or 'ошибка'}): "
                               f"{job.get('error') or ''}", severity="warning")

    async def site_template_job(self, params: dict, ctx: dict) -> dict:
        return {"job": self._site_job_public()}

    # ── typed-confirm helpers ────────────────────────────────────────
    def _check_confirm(self, params: dict, action: str, target: str) -> bool:
        """Look for params['confirm_token']+params['confirm'] in pending_confirms."""
        token = params.get("confirm_token")
        submitted = params.get("confirm")
        if not token or not submitted:
            return False
        row = self.state.consume_pending_confirm(str(token), str(submitted))
        if not row:
            return False
        return row["action"] == action and (row["target"] or "") == (target or "")

    def _issue_confirm(self, action: str, target: str, expected_string: str, description: str = "") -> None:
        token = _new_typed_token()
        self.state.store_pending_confirm(token, action, target, expected_string, ttl=300)
        raise MethodError(412, "typed-confirm required", {
            "needs_confirm": True,
            "confirm_token": token,
            "confirm": expected_string,
            "description": description or f"{action} requires typed confirmation",
            "ttl": 300,
            "action": action,
            "target": target,
        })

    # ── dispatch table ───────────────────────────────────────────────
    def dispatch_table(self) -> dict[str, Any]:
        return {
            "system.status": self.system_status,
            "client.list": self.client_list,
            "client.add": self.client_add,
            "client.update": self.client_update,
            "client.enable": self.client_enable,
            "client.disable": self.client_disable,
            "client.reset_traffic": self.client_reset_traffic,
            "client.delete": self.client_delete,
            "client.qr": self.client_qr,
            "client.sub_url": self.client_sub_url,
            "subscription.rotate": self.subscription_rotate,
            "inbound.list": self.inbound_list,
            "inbound.toggle": self.inbound_toggle,
            "panel.access_get": self.panel_access_get,
            "panel.access_set": self.panel_access_set,
            "panel.creds_rotate": self.panel_creds_rotate,
            "audit.tail": self.audit_tail,
            "cert.force_renew": self.cert_force_renew,
            "admin.claim": self.admin_claim,
            "admin.claim_first": self.admin_claim_first,
            "admin.list": self.admin_list,
            "admin.invite": self.admin_invite,
            "tunnel.url_get": self.tunnel_url_get,
            "backup.create": self.backup_create,
            "backup.list": self.backup_list,
            "backup.restore": self.backup_restore,
            "repair.run": self.repair_run,
            "system.restart": self.system_restart,
            "notifications.get": self.notifications_get,
            "notifications.set": self.notifications_set,
            "notify-admins": self.notify_admins,
            # cover-site (Pro) templates — bot + Mini App template picker
            "site.template_status": self.site_template_status,
            "site.template_categories": self.site_template_categories,
            "site.template_list": self.site_template_list,
            "site.template_get": self.site_template_get,
            "site.template_random": self.site_template_random,
            "site.template_apply": self.site_template_apply,
            "site.template_job": self.site_template_job,
        }
