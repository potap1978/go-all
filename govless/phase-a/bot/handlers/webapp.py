# goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
# Licensed under the goVLESS Source-Available License (see the LICENSE file).
# Redistribution, mirroring, or republishing in any form — whole or partial,
# modified or not — is prohibited without prior written permission.

"""
WebApp menu-button watcher.

Background task: every 30s, check mtime of the quick and named tunnel URL
files; on change, fetch the current URL via `tunnel.url_get` RPC, then call
`bot.set_chat_menu_button(chat_id=admin, menu_button=MenuButtonWebApp(...))`
for every admin and send them a short notification.

Also exposes `apply_menu_buttons_once(bot)` which is called on bot startup so
the menu button reflects the current tunnel URL right away.

inotify is preferred when available but pyinotify isn't a hard dep — we fall
back to polling mtime.

Note (Codex 049 → 050 architect revert): the gate that limited
MenuButtonWebApp to Pro only is gone. In Lite the Mini App is now served
by /opt/govless/webapp/server.py (stdlib HTTP + UNIX-socket RPC proxy),
so /api/rpc actually works through the Cloudflare tunnel. We still keep
the `source` field so logging shows where the URL came from.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from typing import Optional

from aiogram import Bot
from aiogram.types import MenuButtonCommands, MenuButtonWebApp, WebAppInfo

from common import CONFIG, get_rpc
from rpc import RpcError, RpcTransportError

LOG = logging.getLogger(__name__)

TUNNEL_URL_PATHS = ("/run/govless/tunnel.url", "/run/govless/tunnel.url.named")
POLL_INTERVAL_SECONDS = 30.0


def _mtimes() -> tuple[Optional[float], ...]:
    out: list[Optional[float]] = []
    for path in TUNNEL_URL_PATHS:
        try:
            out.append(os.path.getmtime(path))
        except FileNotFoundError:
            out.append(None)
        except OSError as exc:
            LOG.debug("%s stat error: %s", path, exc)
            out.append(None)
    return tuple(out)


# Sentinel: the daemon could not be asked. Distinct from "asked, no tunnel" —
# a transport hiccup used to look like "the tunnel is gone", so the bot pulled
# the "Open app" button and told every admin the tunnel had been cleared, on a
# perfectly healthy Pro box (QA BUG-32).
UNKNOWN_URL = object()


async def _fetch_current_url() -> Optional[tuple[str, str]] | object:
    """Return (url, source), None when there is genuinely no tunnel, or
    UNKNOWN_URL when the daemon could not be reached / gave nonsense.

    `source` is informational only (used for log lines / future telemetry).
    Both Pro (nginx) and Lite (server.py RPC proxy) can serve the Mini App
    end-to-end, so we no longer gate the Telegram menu button on it.
    """
    try:
        res = await get_rpc().call("tunnel.url_get", {})
    except (RpcError, RpcTransportError) as exc:
        LOG.warning("tunnel.url_get failed (%s) — keeping the current menu button", exc)
        return UNKNOWN_URL
    if not isinstance(res, dict):
        LOG.warning("tunnel.url_get returned %r — keeping the current menu button", type(res).__name__)
        return UNKNOWN_URL
    url = res.get("url")
    source = res.get("source") or "none"
    if not isinstance(url, str) or not url:
        return None
    return (url, source)


async def _apply_for_url(bot: Bot, url_info: Optional[tuple[str, str]]) -> None:
    """Set the Telegram menu button.

    Any non-empty tunnel URL gets MenuButtonWebApp — Lite and Pro both serve
    /api/rpc now (Lite via the webapp-frontend.service proxy in server.py,
    Pro via nginx). No tunnel URL → MenuButtonCommands as a fallback so the
    user still has a way into the bot via slash commands.
    """
    if not CONFIG.admin_ids:
        return
    if url_info is UNKNOWN_URL:
        return  # state unknown → leave the button exactly as it is
    if url_info is not None:
        url, source = url_info
        # Security (post-052): do NOT log the full tunnel URL — journal is
        # often readable by groups outside govless. Log a short sha256
        # fingerprint so we can still correlate URL changes across the
        # bot + govless-tunnel-url-extract logs without leaking the URL.
        url_fp = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        LOG.info("setting WebApp menu button: source=%s url_fp=%s", source, url_fp)
        button = MenuButtonWebApp(text="Open app", web_app=WebAppInfo(url=url))
    else:
        # No active tunnel → bot inline buttons only.
        button = MenuButtonCommands()
    for tg_id in list(CONFIG.admin_ids):
        try:
            await bot.set_chat_menu_button(chat_id=tg_id, menu_button=button)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("set_chat_menu_button for %s failed: %s", tg_id, exc)


async def apply_menu_buttons_once(bot: Bot) -> None:
    """Called on startup. Best-effort, never raises."""
    try:
        url_info = await _fetch_current_url()
        if url_info is UNKNOWN_URL:
            LOG.warning("startup: govlessctl unreachable — menu button left unchanged")
            return
        await _apply_for_url(bot, url_info)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("initial menu button apply failed: %s", exc)


async def tunnel_watcher(bot: Bot) -> None:
    """
    Long-running background task. Applies the current Mini App URL to every
    admin's menu button and notifies them when it changes.

    Watching only the tunnel files' mtime was not enough: a Pro→Lite switch
    changes the URL without touching /run/govless/tunnel.url*, so the button
    kept a dead Pro URL (https://domain/app/ — port 443 is Reality in Lite)
    until someone restarted the bot (QA BUG-11). The RPC is one cheap local
    call per POLL_INTERVAL_SECONDS, so poll the effective URL itself and act
    on a real change; mtimes stay as a fast path for tunnel rotation logs.
    """
    last_mtimes = _mtimes()
    last_url: Optional[str] = None
    first_pass = True
    LOG.info("tunnel_watcher started; initial mtimes=%s", last_mtimes)
    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            LOG.info("tunnel_watcher cancelled")
            return

        cur = _mtimes()
        mtimes_changed = cur != last_mtimes
        last_mtimes = cur

        url_info = await _fetch_current_url()
        if url_info is UNKNOWN_URL:
            continue  # daemon unreachable this tick — try again next time
        url_now = url_info[0] if url_info is not None else None
        if first_pass:
            # Startup already applied the button (apply_menu_buttons_once);
            # remember the value so the first tick doesn't re-notify admins.
            first_pass = False
            last_url = url_now
            if not mtimes_changed:
                continue
        if url_now == last_url and not mtimes_changed:
            continue
        url_changed = url_now != last_url
        last_url = url_now
        if not url_changed and not mtimes_changed:
            continue

        await _apply_for_url(bot, url_info)
        if not url_changed:
            # Only the tunnel files were touched (same URL) — no need to ping admins.
            continue

        # url_info is (url, source) or None. Show source so admins can tell
        # quick-tunnel vs named-tunnel vs pro nginx apart at a glance.
        if url_info is not None:
            url, source = url_info
            url_fp = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
            msg = (
                f"🔄 Tunnel URL updated ({source}).\n"
                f"Menu button refreshed. URL fp: <code>sha256:{url_fp}</code>"
            )
        else:
            msg = "🔌 Tunnel URL cleared (no active tunnel)."
        for tg_id in list(CONFIG.admin_ids):
            try:
                await bot.send_message(tg_id, msg, parse_mode="HTML")
            except Exception as exc:  # noqa: BLE001
                LOG.debug("notify %s failed: %s", tg_id, exc)


# ---------------------------------------------------------------------------
# Notification delivery
# ---------------------------------------------------------------------------
# The daemon appends events to /run/govless/notify.queue; until now nothing read
# that file, so `notify-admins` answered {"sent": true} into the void and the
# Mini App's notification switches changed nothing (QA BUG-26/27). This watcher
# drains the queue and delivers each line to the admins who kept that event on.

NOTIFY_QUEUE = "/run/govless/notify.queue"
NOTIFY_POLL_SECONDS = 10.0
NOTIFY_PREFS_TEMPLATE = "/etc/govless/notify.{tg}.json"
# Mirrors Methods.NOTIFY_DEFAULTS — a missing prefs file means "these defaults".
NOTIFY_DEFAULTS = {
    "tunnel_url_changed": True,
    "site_template_changed": True,
    "client_added": True,
    "panel_creds_rotated": True,
    "mode_switched": True,
    "daemon_restart": False,
    "manual": True,
}


def _wants_event(tg_id: int, event: str) -> bool:
    """Does this admin want this event? Unknown events are always delivered."""
    default = NOTIFY_DEFAULTS.get(event, True)
    try:
        with open(NOTIFY_PREFS_TEMPLATE.format(tg=tg_id), "r", encoding="utf-8") as f:
            prefs = json.load(f)
        if isinstance(prefs, dict) and event in prefs:
            return bool(prefs[event])
    except (OSError, ValueError):
        pass
    return default


_notify_offset = 0  # fallback position when the queue cannot be truncated


def _drain_queue() -> list[dict]:
    """Read and truncate the queue in one go, so nothing is delivered twice.

    If the file is not writable for us (unexpected ownership), fall back to
    reading from a remembered offset instead of dropping every notification.
    """
    global _notify_offset
    try:
        with open(NOTIFY_QUEUE, "r+", encoding="utf-8") as f:
            raw = f.read()
            f.seek(0)
            f.truncate()
            _notify_offset = 0
    except FileNotFoundError:
        _notify_offset = 0
        return []
    except PermissionError:
        try:
            with open(NOTIFY_QUEUE, "r", encoding="utf-8") as f:
                f.seek(_notify_offset)
                raw = f.read()
                _notify_offset = f.tell()
            LOG.warning("notify queue is read-only for the bot — delivering by offset; "
                        "fix with: chmod 0660 %s && chown root:govless %s", NOTIFY_QUEUE, NOTIFY_QUEUE)
        except OSError as exc:
            LOG.warning("notify queue read failed: %s", exc)
            return []
    except OSError as exc:
        LOG.warning("notify queue read failed: %s", exc)
        return []
    out: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            LOG.debug("skipping malformed notify line: %.80s", line)
            continue
        if isinstance(item, dict) and item.get("message"):
            out.append(item)
    return out


async def notify_watcher(bot: Bot) -> None:
    """Long-running task: deliver queued notifications to admins."""
    LOG.info("notify_watcher started; queue=%s", NOTIFY_QUEUE)
    while True:
        try:
            await asyncio.sleep(NOTIFY_POLL_SECONDS)
        except asyncio.CancelledError:
            LOG.info("notify_watcher cancelled")
            return
        try:
            items = await asyncio.get_running_loop().run_in_executor(None, _drain_queue)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("notify queue drain failed: %s", exc)
            continue
        if not items:
            continue
        for item in items[-20:]:  # a flood must not spam the chat
            event = str(item.get("event") or "manual")
            text = str(item.get("message"))[:3500]
            for tg_id in list(CONFIG.admin_ids):
                if not _wants_event(tg_id, event):
                    continue
                try:
                    await bot.send_message(tg_id, text)
                except Exception as exc:  # noqa: BLE001
                    LOG.debug("notify %s failed: %s", tg_id, exc)
