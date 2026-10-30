# goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
# Licensed under the goVLESS Source-Available License (see the LICENSE file).
# Redistribution, mirroring, or republishing in any form — whole or partial,
# modified or not — is prohibited without prior written permission.

"""
Cover site (Pro) template picker — the same 33 000+ verified static sites as
the SSH menu (`govless → 1 → 5`), driven through govlessctl RPC:

  site.template_status / categories / list / get / random / apply / job

Applying runs as a background job in the daemon (a clone can take 30-180 s,
longer than the 30 s RPC timeout), so the bot polls `site.template_job` and
edits one message with progress until the job finishes.

Callback-data scheme (each ≤ 64 bytes):
  m:site                    → status screen (Pro only; Lite gets a hint)
  st:cat                    → category list
  st:l:<cat>:<page>         → templates of a category, page N (8 per page)
  st:v:<key>:<cat>:<page>   → template card; key = sha1(id)[:12], cat "_" = none
  st:a:<key>                → apply the template
  st:rnd                    → random template card
  st:stub                   → ask before installing the built-in stub page
  st:stubgo                 → install the stub page
  st:bk                     → site backups
  st:bk:<n>                 → ask before restoring backup #n
  st:bkgo:<n>               → restore backup #n
  st:job                    → refresh the running job

Commands:
  /site                     → status
  /site <word>              → search templates by name / id
  /siteurl <https url>      → deploy a custom template from a public git repo

Stateless: every screen re-fetches from govlessctl; short keys are resolved
by the daemon (site.template_get {key}), nothing is cached in the bot.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from common import admin_only, get_rpc
from rpc import RpcError, RpcTransportError

LOG = logging.getLogger(__name__)

router = Router(name="site")

PAGE_SIZE = 8
JOB_POLL_SECONDS = 3
JOB_EDIT_EVERY = 6          # seconds between progress edits (Telegram flood-safe)
JOB_MAX_WAIT = 480          # daemon kills the job at 420 s; give it a little slack

STALE = "Меню устарело — отправьте /start"


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _code(value: Any) -> str:
    return f"<code>{_esc(value)}</code>"


def _alert(prefix: str, exc: Exception) -> str:
    text = f"{prefix}: {exc}"
    return text if len(text) <= 190 else text[:187] + "..."


def _nav_rows() -> List[List[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text="🌐 Cover site", callback_data="m:site")],
        [InlineKeyboardButton(text="🏠 Main menu", callback_data="m:main")],
    ]


# ---------------------------------------------------------------------------
# RPC helpers
# ---------------------------------------------------------------------------


async def _status() -> Dict[str, Any]:
    res = await get_rpc().call("site.template_status", {})
    return res if isinstance(res, dict) else {}


def _current_line(st: Dict[str, Any]) -> str:
    cur = st.get("current") or {}
    kind = cur.get("kind")
    if not cur:
        return "no site (web root is empty)"
    if kind == "stub":
        return "built-in stub page"
    if kind == "custom":
        return f"custom from Git: {_esc(cur.get('source_url') or cur.get('id'))}"
    if kind == "catalog":
        t = cur.get("template") or {}
        if t:
            return f"{_esc(t.get('name'))} <i>[{_esc(t.get('source'))}]</i>\n{_code(t.get('id'))}"
        return _code(cur.get("id"))
    return f"unknown ({_esc(cur.get('id'))})"


def _job_line(job: Optional[Dict[str, Any]]) -> str:
    if not job:
        return ""
    state = job.get("state")
    if state == "running":
        el = int(time.time()) - int(job.get("started_ts") or time.time())
        return f"\n⏳ Applying {_esc(job.get('name') or job.get('target'))}… {el}s"
    if state == "done":
        return f"\n✅ Last change: {_esc(job.get('name') or job.get('template_id'))}"
    if state == "failed":
        return (
            f"\n❌ Last change failed ({_esc(job.get('error_code') or 'error')}): "
            f"{_esc(job.get('error') or '')}"
        )
    return ""


def _status_text(st: Dict[str, Any]) -> str:
    if not st.get("available"):
        return (
            "<b>🌐 Cover site</b>\n"
            f"Mode: {_code(st.get('mode', '?'))}\n\n"
            "The cover (masquerade) site exists only in <b>Pro</b> mode — your own "
            "domain + TLS with a real website behind it.\n"
            "Switch modes in SSH: <code>govless → 1 → 4</code>."
        )
    cat = st.get("catalog") or {}
    lines = [
        "<b>🌐 Cover site</b>",
        f"Domain: {_code(st.get('domain'))}",
        f"Site: {_esc(st.get('site_url'))}",
        f"Template: {_current_line(st)}",
    ]
    if cat.get("ok"):
        lines.append(f"Catalog: {_esc(cat.get('total'))} verified templates · {_esc(cat.get('categories'))} categories")
    else:
        lines.append(f"Catalog: ❌ {_esc(cat.get('error') or 'not available')}")
    lines.append(_job_line(st.get("job")))
    lines.append("\nPick a category, try a random one, or send <code>/site word</code> to search.")
    return "\n".join(l for l in lines if l is not None)


def _status_kb(st: Dict[str, Any]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if st.get("available"):
        job = st.get("job") or {}
        if job.get("state") == "running":
            rows.append([InlineKeyboardButton(text="⏳ Refresh job status", callback_data="st:job")])
        rows.append([
            InlineKeyboardButton(text="📂 Categories", callback_data="st:cat"),
            InlineKeyboardButton(text="🎲 Random", callback_data="st:rnd"),
        ])
        rows.append([InlineKeyboardButton(text="⏭ Built-in stub page", callback_data="st:stub")])
        if st.get("backups"):
            rows.append([InlineKeyboardButton(text="↩️ Previous sites (backups)", callback_data="st:bk")])
        rows.append([InlineKeyboardButton(text="🔄 Refresh", callback_data="m:site")])
    rows.append([InlineKeyboardButton(text="🏠 Main menu", callback_data="m:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_status(call: CallbackQuery) -> None:
    if call.message is None:
        await call.answer(STALE, show_alert=True)
        return
    try:
        st = await _status()
    except (RpcError, RpcTransportError) as exc:
        await call.message.edit_text(  # type: ignore[union-attr]
            f"<b>🌐 Cover site</b>\nCould not fetch status: {_code(exc)}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_nav_rows()),
        )
        return
    try:
        await call.message.edit_text(  # type: ignore[union-attr]
            _status_text(st), parse_mode="HTML", reply_markup=_status_kb(st),
            disable_web_page_preview=True,
        )
    except Exception as exc:  # noqa: BLE001
        # Refresh with nothing new → Telegram rejects the identical edit; that is
        # not an error worth a WARNING per tap (QA E13: 29 warnings for 30 taps).
        if "message is not modified" in str(exc):
            return
        raise


BUSY_TEXT = "Busy: a template change is already running — wait for it to finish, then try again."


def _rpc_error_text(prefix: str, exc: RpcError) -> str:
    if exc.code == 409:
        return BUSY_TEXT
    return _alert(prefix, exc)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@router.callback_query(F.data.in_({"m:site", "st:job"}))
@admin_only
async def cb_site(call: CallbackQuery) -> None:
    await call.answer()
    await _render_status(call)


@router.message(Command("site"))
@admin_only
async def cmd_site(message: Message, command: CommandObject) -> None:
    word = (command.args or "").strip()
    if not word:
        try:
            st = await _status()
        except (RpcError, RpcTransportError) as exc:
            await message.answer(f"Could not fetch status: {_code(exc)}", parse_mode="HTML")
            return
        await message.answer(_status_text(st), parse_mode="HTML", reply_markup=_status_kb(st),
                             disable_web_page_preview=True)
        return
    await _send_search(message, word)


async def _send_search(message: Message, word: str) -> None:
    try:
        res = await get_rpc().call("site.template_list", {"q": word[:64], "page": 0, "per": PAGE_SIZE})
    except (RpcError, RpcTransportError) as exc:
        await message.answer(_alert("Search failed", exc))
        return
    items = res.get("items") or []
    if not items:
        await message.answer(
            f"Nothing found for {_code(word)}. Try another word or browse categories.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📂 Categories", callback_data="st:cat")],
                *_nav_rows(),
            ]),
        )
        return
    rows = [
        [InlineKeyboardButton(
            text=f"{t.get('name')} [{t.get('source')}]"[:60],
            callback_data=f"st:v:{t.get('key')}:_:0",
        )]
        for t in items
    ]
    rows.append([InlineKeyboardButton(text="📂 Categories", callback_data="st:cat")])
    rows.extend(_nav_rows())
    more = "" if res.get("total", 0) <= PAGE_SIZE else f" (first {PAGE_SIZE} of {res.get('total')} — refine the word)"
    await message.answer(
        f"<b>Search:</b> {_code(word)}{_esc(more)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ---------------------------------------------------------------------------
# Categories / list
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "st:cat")
@admin_only
async def cb_categories(call: CallbackQuery) -> None:
    if call.message is None:
        await call.answer(STALE, show_alert=True)
        return
    await call.answer()
    try:
        cats = await get_rpc().call("site.template_categories", {})
    except (RpcError, RpcTransportError) as exc:
        await call.message.edit_text(  # type: ignore[union-attr]
            f"Could not fetch categories: {_code(exc)}", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_nav_rows()),
        )
        return
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for c in cats or []:
        row.append(InlineKeyboardButton(
            text=f"{c.get('name_en') or c.get('name')} ({c.get('count')})"[:40],
            callback_data=f"st:l:{c.get('id')}:0",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.extend(_nav_rows())
    await call.message.edit_text(  # type: ignore[union-attr]
        "<b>📂 Template categories</b>\nPick one — the list is paged, 8 per page.\n"
        "Search by name: <code>/site word</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("st:l:"))
@admin_only
async def cb_list(call: CallbackQuery) -> None:
    if call.message is None:
        await call.answer(STALE, show_alert=True)
        return
    parts = (call.data or "").split(":")
    cat = parts[2] if len(parts) > 2 else ""
    try:
        page = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        page = 0
    await call.answer()
    try:
        res = await get_rpc().call("site.template_list", {"category": cat, "page": page, "per": PAGE_SIZE})
    except (RpcError, RpcTransportError) as exc:
        await call.message.edit_text(  # type: ignore[union-attr]
            f"Could not fetch templates: {_code(exc)}", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📂 Categories", callback_data="st:cat")], *_nav_rows(),
            ]),
        )
        return
    items = res.get("items") or []
    page = int(res.get("page") or 0)
    pages = int(res.get("pages") or 1)
    rows = [
        [InlineKeyboardButton(
            text=f"{t.get('name')} [{t.get('source')}]"[:60],
            callback_data=f"st:v:{t.get('key')}:{cat}:{page}",
        )]
        for t in items
    ]
    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="« Prev", callback_data=f"st:l:{cat}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=f"st:l:{cat}:{page}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="Next »", callback_data=f"st:l:{cat}:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="📂 Categories", callback_data="st:cat")])
    rows.extend(_nav_rows())
    name = ""
    if items:
        name = items[0].get("category_en") or cat
    await call.message.edit_text(  # type: ignore[union-attr]
        f"<b>📂 {_esc(name or cat)}</b> · {_esc(res.get('total'))} templates · page {page + 1}/{pages}\n"
        "Tap a template to see its description, preview link and license.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ---------------------------------------------------------------------------
# Template card + apply
# ---------------------------------------------------------------------------


def _card_text(t: Dict[str, Any]) -> str:
    lines = [
        f"<b>{_esc(t.get('name'))}</b>",
        f"Source: {_esc(t.get('source'))} · Category: {_esc(t.get('category_en') or t.get('category'))}",
    ]
    if t.get("license"):
        lines.append(f"License: {_esc(t.get('license'))}")
    if t.get("stars"):
        lines.append(f"Stars: {_esc(t.get('stars'))}")
    if t.get("description"):
        lines.append(f"\n{_esc(t.get('description'))}")
    if t.get("preview_url"):
        lines.append(f"\n👁 Preview: {_esc(t.get('preview_url'))}")
    if t.get("repo_url"):
        lines.append(f"📦 Repo: {_esc(t.get('repo_url'))}")
    lines.append(f"\nid: {_code(t.get('id'))}")
    lines.append("\nApplying downloads the site and replaces the current one (the old site is backed up).")
    return "\n".join(lines)


def _card_kb(t: Dict[str, Any], cat: str, page: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="✅ Apply this template", callback_data=f"st:a:{t.get('key')}")]]
    if t.get("preview_url"):
        rows.append([InlineKeyboardButton(text="👁 Open preview", url=str(t.get("preview_url")))])
    if cat and cat != "_":
        rows.append([InlineKeyboardButton(text="« Back to list", callback_data=f"st:l:{cat}:{page}")])
    rows.append([InlineKeyboardButton(text="🎲 Another random", callback_data="st:rnd")])
    rows.append([InlineKeyboardButton(text="📂 Categories", callback_data="st:cat")])
    rows.extend(_nav_rows())
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("st:v:"))
@admin_only
async def cb_view(call: CallbackQuery) -> None:
    if call.message is None:
        await call.answer(STALE, show_alert=True)
        return
    parts = (call.data or "").split(":")
    key = parts[2] if len(parts) > 2 else ""
    cat = parts[3] if len(parts) > 3 else "_"
    try:
        page = int(parts[4]) if len(parts) > 4 else 0
    except ValueError:
        page = 0
    await call.answer()
    try:
        res = await get_rpc().call("site.template_get", {"key": key})
    except (RpcError, RpcTransportError) as exc:
        await call.message.edit_text(  # type: ignore[union-attr]
            f"Template not available: {_code(exc)}", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📂 Categories", callback_data="st:cat")], *_nav_rows(),
            ]),
        )
        return
    t = res.get("template") or {}
    await call.message.edit_text(  # type: ignore[union-attr]
        _card_text(t), parse_mode="HTML", reply_markup=_card_kb(t, cat, page),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "st:rnd")
@admin_only
async def cb_random(call: CallbackQuery) -> None:
    if call.message is None:
        await call.answer(STALE, show_alert=True)
        return
    await call.answer()
    try:
        res = await get_rpc().call("site.template_random", {})
    except (RpcError, RpcTransportError) as exc:
        await call.answer(_alert("Random failed", exc), show_alert=True)
        return
    t = res.get("template") or {}
    await call.message.edit_text(  # type: ignore[union-attr]
        "🎲 <b>Random pick</b>\n\n" + _card_text(t), parse_mode="HTML",
        reply_markup=_card_kb(t, "_", 0), disable_web_page_preview=True,
    )


async def _apply_and_wait(call: CallbackQuery, params: Dict[str, Any], label: str) -> None:
    """Start the daemon job, then poll it and edit the message until it finishes."""
    if call.message is None:
        await call.answer(STALE, show_alert=True)
        return
    params = dict(params, lang="en")  # bot UI is English → installer messages in English too
    try:
        res = await get_rpc().call("site.template_apply", params)
    except RpcError as exc:
        await call.answer(_rpc_error_text("Apply failed", exc), show_alert=True)
        return
    except RpcTransportError as exc:
        await call.answer(_alert("govlessctl unreachable", exc), show_alert=True)
        return
    await call.answer("Started…")
    job = (res or {}).get("job") or {}
    job_id = job.get("id")
    started = time.time()
    last_edit = 0.0
    msg = call.message

    async def _edit(text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
        try:
            await msg.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — "message is not modified" etc.
            LOG.debug("site progress edit failed: %s", exc)

    await _edit(f"⏳ <b>Applying {_esc(label)}</b>\nDownloading and verifying the site… this takes 10–90 s.")
    while True:
        await asyncio.sleep(JOB_POLL_SECONDS)
        try:
            j = (await get_rpc().call("site.template_job", {})).get("job") or {}
        except (RpcError, RpcTransportError) as exc:
            LOG.debug("site.template_job poll failed: %s", exc)
            j = {}
        elapsed = int(time.time() - started)
        if j.get("id") and j.get("id") != job_id:
            await _edit("⚠️ Another template job replaced this one. Check the status screen.",
                        InlineKeyboardMarkup(inline_keyboard=_nav_rows()))
            return
        state = j.get("state")
        if state == "running" or not j:
            if elapsed >= JOB_MAX_WAIT:
                await _edit("⚠️ Still running after 8 minutes — check the status screen later.",
                            InlineKeyboardMarkup(inline_keyboard=_nav_rows()))
                return
            if time.time() - last_edit >= JOB_EDIT_EVERY:
                last_edit = time.time()
                await _edit(f"⏳ <b>Applying {_esc(label)}</b>\nDownloading and verifying the site… {elapsed}s")
            continue
        break

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Another random", callback_data="st:rnd"),
         InlineKeyboardButton(text="📂 Categories", callback_data="st:cat")],
        *_nav_rows(),
    ])
    if j.get("ok"):
        text = (
            f"✅ <b>Template deployed:</b> {_esc(j.get('name') or j.get('template_id'))}\n"
            f"HTTP check: {_code(j.get('http_code') or '?')}\n"
        )
        if j.get("backup"):
            text += f"Previous site saved: {_code(j.get('backup'))}\n"
        text += "\nOpen your domain in a browser (hard-refresh: Ctrl+F5 / Cmd+Shift+R)."
    else:
        text = (
            f"❌ <b>Template change failed</b> ({_esc(j.get('error_code') or 'error')})\n"
            f"{_esc(j.get('error') or 'unknown error')}\n\n"
            "The previous site is untouched. Pick another template."
        )
    await _edit(text, kb)


@router.callback_query(F.data.startswith("st:a:"))
@admin_only
async def cb_apply(call: CallbackQuery) -> None:
    key = (call.data or "").split(":", 2)[-1]
    name = key
    try:
        res = await get_rpc().call("site.template_get", {"key": key})
        name = (res.get("template") or {}).get("name") or key
    except (RpcError, RpcTransportError) as exc:
        await call.answer(_alert("Template not available", exc), show_alert=True)
        return
    await _apply_and_wait(call, {"key": key}, name)


# ---------------------------------------------------------------------------
# Stub page
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "st:stub")
@admin_only
async def cb_stub_ask(call: CallbackQuery) -> None:
    if call.message is None:
        await call.answer(STALE, show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(  # type: ignore[union-attr]
        "⏭ <b>Built-in stub page</b>\nReplace the current site with the plain "
        "“Server is running” page? The current site is backed up first.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, install the stub", callback_data="st:stubgo")],
            *_nav_rows(),
        ]),
    )


@router.callback_query(F.data == "st:stubgo")
@admin_only
async def cb_stub_go(call: CallbackQuery) -> None:
    await _apply_and_wait(call, {"stub": True}, "built-in stub page")


# ---------------------------------------------------------------------------
# Backups (previous sites)
# ---------------------------------------------------------------------------


async def _backups() -> List[Dict[str, Any]]:
    st = await _status()
    return list(st.get("backups") or [])


def _fmt_size(n: Any) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} GB"


@router.callback_query(F.data == "st:bk")
@admin_only
async def cb_backups(call: CallbackQuery) -> None:
    if call.message is None:
        await call.answer(STALE, show_alert=True)
        return
    await call.answer()
    try:
        backups = await _backups()
    except (RpcError, RpcTransportError) as exc:
        await call.answer(_alert("Backups", exc), show_alert=True)
        return
    if not backups:
        await call.message.edit_text(  # type: ignore[union-attr]
            "No site backups yet — one is created on every template change.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_nav_rows()),
        )
        return
    rows = [
        [InlineKeyboardButton(
            text=f"↩️ {b.get('name')} · {_fmt_size(b.get('size'))}"[:60],
            callback_data=f"st:bk:{i}",
        )]
        for i, b in enumerate(backups[:10])
    ]
    rows.extend(_nav_rows())
    await call.message.edit_text(  # type: ignore[union-attr]
        "<b>↩️ Previous sites</b>\nNewest first. Restoring replaces the current site (which is backed up too).",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.regexp(r"^st:bk:\d+$"))
@admin_only
async def cb_backup_ask(call: CallbackQuery) -> None:
    if call.message is None:
        await call.answer(STALE, show_alert=True)
        return
    idx = int((call.data or "").rsplit(":", 1)[-1])
    await call.answer()
    try:
        backups = await _backups()
    except (RpcError, RpcTransportError) as exc:
        await call.answer(_alert("Backups", exc), show_alert=True)
        return
    if idx >= len(backups):
        await call.answer("Backup list changed — open it again.", show_alert=True)
        return
    b = backups[idx]
    await call.message.edit_text(  # type: ignore[union-attr]
        f"Bring the site back from {_code(b.get('name'))} ({_esc(_fmt_size(b.get('size')))})?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, restore", callback_data=f"st:bkgo:{idx}")],
            [InlineKeyboardButton(text="« Backups", callback_data="st:bk")],
            *_nav_rows(),
        ]),
    )


@router.callback_query(F.data.regexp(r"^st:bkgo:\d+$"))
@admin_only
async def cb_backup_go(call: CallbackQuery) -> None:
    idx = int((call.data or "").rsplit(":", 1)[-1])
    try:
        backups = await _backups()
    except (RpcError, RpcTransportError) as exc:
        await call.answer(_alert("Backups", exc), show_alert=True)
        return
    if idx >= len(backups):
        await call.answer("Backup list changed — open it again.", show_alert=True)
        return
    b = backups[idx]
    await _apply_and_wait(call, {"restore": b.get("path")}, str(b.get("name")))


# ---------------------------------------------------------------------------
# Custom git URL
# ---------------------------------------------------------------------------


@router.message(Command("siteurl"))
@admin_only
async def cmd_siteurl(message: Message, command: CommandObject) -> None:
    url = (command.args or "").strip()
    if not url:
        await message.answer(
            "Usage: <code>/siteurl https://github.com/user/repo</code>\n"
            "Any public https git repository with a ready static site (index.html in the root, "
            "dist/, public/ or build/). Max 100 MB. Optional branch: <code>…/repo@branch</code>.",
            parse_mode="HTML",
        )
        return
    if not url.startswith("https://") or any(ch in url for ch in " \t;`$()<>|\\&'\""):
        await message.answer("Only a plain https:// git URL is accepted.")
        return
    try:
        res = await get_rpc().call("site.template_apply", {"custom_url": url, "lang": "en"})
    except RpcError as exc:
        await message.answer(_rpc_error_text("Apply failed", exc))
        return
    except RpcTransportError as exc:
        await message.answer(_alert("govlessctl unreachable", exc))
        return
    job = (res or {}).get("job") or {}
    sent = await message.answer(
        f"⏳ <b>Applying custom template</b>\n{_esc(url)}\nCloning and verifying… this takes 10–90 s.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    started = time.time()
    while True:
        await asyncio.sleep(JOB_POLL_SECONDS)
        try:
            j = (await get_rpc().call("site.template_job", {})).get("job") or {}
        except (RpcError, RpcTransportError):
            j = {}
        if (j.get("state") == "running" or not j) and time.time() - started < JOB_MAX_WAIT:
            continue
        break
    if j.get("id") != job.get("id"):
        text = "⚠️ Another template job replaced this one. Check /site."
    elif j.get("ok"):
        text = (
            f"✅ <b>Custom template deployed</b>\nHTTP check: {_code(j.get('http_code') or '?')}\n"
            + (f"Previous site saved: {_code(j.get('backup'))}\n" if j.get("backup") else "")
            + "\nOpen your domain in a browser (hard-refresh: Ctrl+F5 / Cmd+Shift+R)."
        )
    elif j.get("state") == "running":
        text = "⚠️ Still running after 8 minutes — check /site later."
    else:
        text = (
            f"❌ <b>Custom template failed</b> ({_esc(j.get('error_code') or 'error')})\n"
            f"{_esc(j.get('error') or 'unknown error')}\n\nThe previous site is untouched."
        )
    try:
        await sent.edit_text(text, parse_mode="HTML", disable_web_page_preview=True,
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=_nav_rows()))
    except Exception as exc:  # noqa: BLE001
        LOG.debug("siteurl final edit failed: %s", exc)
