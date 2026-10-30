# 042 - Codex to Claude: Telegram Mini App operator UI, variant A

**From:** Codex
**To:** Claude
**Topic:** Pro Telegram Mini App UI pass based on Variant A
**Created:** 2026-05-25
**Status:** implemented

## Context

The human rejected the first rough Mini App surface and selected Variant A from
the visual companion: an operator-style control panel that should feel closer
to the SSH menu and the high-frequency 3X-UI workflows.

This patch is intentionally UI-focused. It does not add new RPC methods or
change the bot command model.

## Implemented

- Reworked the Mini App root screen into an operator dashboard:
  - status hero with mode, transport, version, client count, and service chips;
  - four large quick actions for add client, links/QR, subscriptions, and panel;
  - client preview list with traffic/limit/expiry summary;
  - compact audit preview when audit entries exist.
- Added fixed bottom navigation across the main Mini App sections:
  - `Главная`
  - `Клиенты`
  - `VPN`
  - `Панель`
- Reused existing RPC and route behavior:
  - `client.list`
  - `system.status`
  - `audit.tail`
  - existing client detail, inbound, admin, and system views.
- Bumped the Telegram WebApp cache bust to `20260525-041` so Telegram opens the
  new UI instead of cached `040` assets.

## Live Verification

On the Pro test bot `@testclaude1warpbot` with domain `lol123.uk`:

- `tunnel.url_get` returns `https://lol123.uk/app/?v=20260525-041`.
- `/app/`, `/app/app.js?v=20260525-041`, and
  `/app/style.css?v=20260525-041` return 200.
- `govlessctl.service` and `govless-bot.service` are active after deploy.
- Telegram Web opens the Mini App and renders the Variant A operator screen with
  real RPC-backed data:
  - 2 clients;
  - `pro · tcp`;
  - version `3.0.6`;
  - `3X-UI`, `Xray`, and `Nginx` service chips.

## Tested

- `node --check phase-a/webapp/dist/app.js` - pass.
- `bash test_regressions.sh` - pass.
- `python3 -m unittest test_bot_regressions.py` - pass.
- `PYTHONPYCACHEPREFIX=/private/tmp/govless-pycache python3 -m py_compile ...`
  - pass.
- `git diff --check` - pass.
- Live Pro Telegram Mini App open in Telegram Web - pass for render and
  RPC-backed dashboard data.

## Remaining Risk

- Browser automation cannot click inside Telegram's cross-origin Mini App iframe
  in the Codex in-app browser. I verified live render and data load visually,
  but did not mechanically click every in-frame button after this UI pass.
- `Ссылки и QR` and `Подписки` still route to the existing client list/details
  flow. A later UX pass can add dedicated filtered screens if we decide they
  are worth the extra surface.
- This is still the static Mini App bundle; if we keep expanding the UI, we
  should consider moving it to a small source/build structure before it becomes
  awkward to review.

## Next Action

Please review the Variant A interaction model, especially whether quick actions
should stay as shortcuts into existing sections or become dedicated screens.
For release gating, keep the hard requirement as Telegram Mini App open plus at
least one successful RPC-backed render from Telegram `initData`.
