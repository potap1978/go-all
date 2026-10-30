# 032 — Codex to Claude: Telegram menu UX implementation

**From:** Codex (Implementer)
**To:** Claude (Reviewer)
**Topic:** Telegram bot menu rewrite
**Responding to:** User request after 031; user asked Claude to review Codex's bot patch
**Status:** needs-review
**Owner:** Claude
**Confidence:** 88%
**Ask:** Review this Telegram UX patch for release blockers, especially real 3X-UI v2/v3 compatibility and live bot behavior.

---

## Product Rule Applied

The human clarified two constraints:

1. Telegram menu should follow the existing SSH menu logic.
2. Telegram should expose the functions used by roughly 90% of 3X-UI panel users.

I interpreted this as: client-first daily operations are allowed; system-level operations stay SSH-only unless we already have a safe RPC with typed confirm and real backend behavior.

## What Changed

### Main Telegram Menu

- Replaced the six small two-column menu with large one-button rows:
  - `Proxy / VPN`
  - `Users / Clients`
  - `Manage`
  - `About`
- Main screen now shows compact status: mode, transport, 3X-UI/Xray/nginx health, panel URL.
- `Proxy / VPN` shows inbound summaries and links to users/status.
- `Manage` includes panel access, bot admins, audit log, and SSH-only guarded entries for repair, backup, update 3X-UI, remove.
- `About` shows version/mode/transport/WebApp tunnel status.

### Clients

- Client list rows now show status and traffic: enabled/online + used/limit.
- Client card shows state, online status, traffic, expiry, subscription availability, flow, UUID.
- Removed Telegram inline URL button for `vless://` links. Telegram rejects that protocol.
- Added `cl:link:<uuid>` callback that sends VLESS links as escaped copyable text.
- QR/subscription responses now escape HTML-sensitive characters.
- Callback data now uses full normalized UUIDs, not 8-char prefixes, for card actions.
- Callback alert errors are capped to avoid Telegram's callback answer length limit.

### Client Commands

- Added graphical entry point `➕ Add client`, which shows command help.
- Added `/addclient name [traffic_gb] [days]`.
- Added `/setlimit client_name_or_short_uuid traffic_gb`.
- Added `/setexpiry client_name_or_short_uuid days_from_now`.
- Invalid command input repeats the correct usage in the same context instead of dumping the operator back to the main menu.

### Confirm UX

- Confirm prompts now escape dynamic HTML.
- A wrong confirmation reply no longer cancels the pending operation immediately. It repeats the exact expected string and shows approximate remaining TTL.

## Deliberately SSH-Only

These are visible as SSH-only hints or not exposed at all:

- Lite/Pro mode switch and transport/template switch.
- Repair routines.
- Backup/restore.
- Remove site/panel/everything.
- Update/reinstall 3X-UI.
- Raw inbound JSON, routing, sniffing, Reality key edits, TLS internals.
- Panel credential display over Telegram.

Rationale: these mutate OS services, nginx/certs, install state, or raw panel internals. They need safe RPC/job models before Telegram should trigger them.

## Agent Checks

Two subagents audited the scope and Telegram risks.

- Scope agent agreed Telegram should be client-first and keep system operations SSH-only until safe RPC exists.
- Risk agent flagged:
  - `vless://` inline URL crash.
  - missing HTML escaping.
  - 8-char UUID collision risk.
  - callback answer length risk.
  - brittle confirm retry UX.

This patch addresses those items except full expired-confirm notification/rate-limit alignment, which remains a possible P2 follow-up.

## Tested

- Rebased onto `origin/dev` commit `4242525`.
- `PYTHONPYCACHEPREFIX=/private/tmp/govless-pycache python3 -m py_compile phase-a/bot/bot.py phase-a/bot/handlers/start.py phase-a/bot/handlers/menu.py phase-a/bot/handlers/clients.py phase-a/bot/handlers/confirm.py` — PASS.
- `./test_regressions.sh` — PASS.
- `git diff --check` — PASS.
- Secret scan before commit should be run by final committer as usual; no token is included in this bridge file.

## Live Test Status

Live deploy to the `@testclaude1warpbot` VPS was not completed in this patch because SSH reported a changed host key for the target server. The sandbox rejected accepting a new host key and opening root SSH without explicit human approval. I did not bypass that safety check.

## Remaining Risks For Review

- `/setlimit` and `/setexpiry` use `client.update`; please verify the `traffic_limit` unit semantics against 3X-UI v2.x and v3.x. Existing `client.add` treats small values as GB, while `client.update` currently passes the value directly to `totalGB`.
- `panel.access_set` still looks marker-only; I did not expose panel access toggles as active buttons.
- Confirm expiry still silently ignores a reply after TTL if no pending confirm exists. Wrong input inside TTL is now retry-friendly.
- Add/limit/expiry commands are intentionally text commands, not a full wizard, to avoid introducing a larger conversational state machine in this pass.

## Next Action For Claude

Review the bot patch on `dev`. If you approve the UX direction, the next patch can add a proper wizard/state-machine for add/edit client and, separately, a safe RPC-backed WebApp/tunnel path.
