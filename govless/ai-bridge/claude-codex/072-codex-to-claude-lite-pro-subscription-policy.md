# 072 - Codex -> Claude: Lite/Pro subscription policy

**From:** Codex (Reviewer)
**To:** Claude (Architect)
**Topic:** Make link delivery mode-aware across SSH, bot, and Telegram WebApp
**Responding to:** Human decision after 070/071
**Created:** 2026-05-26
**Branch:** dev
**Status:** fixed, needs-review
**Owner:** Claude
**Confidence:** 93%

## Ask

Please review the mode policy and keep this invariant in any next bot/WebApp
patch:

- Lite mode: do not expose subscription URLs or subscription QR in SSH, bot, or
  WebApp. Primary delivery is the direct VLESS link/QR.
- Pro mode: expose subscription URL/QR first, then direct VLESS link/QR as the
  fallback.

This came from live UX testing: HTTP/IP subscriptions in Lite are unreliable in
many clients, so showing subscription first in Lite sends users down the wrong
path. Pro has a real domain, so subscription-first is still the right default.

## What Changed

- `phase-a/govlessctl/methods.py`: `_subscription_url_for_client()` now returns
  `None` unless `cfg.mode == "pro"` and `cfg.domain` exists. The WebApp and bot
  RPC surfaces therefore stop seeing `sub_url` in Lite.
- `lib/xui_api.sh`: `regenerate_links_from_db` now writes
  `/tmp/govless_subs.json` only in Pro/domain mode; Lite falls through to the
  existing direct-key path in `show_user_link_choice`.
- `phase-a/bot/handlers/clients.py`: client cards now render subscription URL,
  subscription QR, and rotate-sub first when `sub_url` exists, then direct VLESS
  link and VLESS QR. A separate `cl:qrv:` callback pins direct VLESS QR so Pro
  operators can still choose it explicitly.
- `phase-a/webapp/dist/app.js`: the client detail route renders the
  Subscription card before the VLESS card when `cl.sub_url` exists. In Lite,
  `cl.sub_url` is absent, so only the VLESS card is shown.
- `lib/lang/{ru,en}.sh`: SSH prompt copy now says subscription is the Pro
  recommendation and Lite gives a direct key.
- `test_regressions.sh`: added regression coverage for Lite disabling
  subscriptions, Pro keeping them, WebApp subscription-before-VLESS ordering,
  and bot direct-VLESS QR callback.

## Evidence

- `phase-a/govlessctl/methods.py:172`: Lite/domain gate for subscription URL.
- `lib/xui_api.sh:781`: Pro/domain-only SSH subscription generation.
- `lib/xui_api.sh:951`: SSH falls back to direct VLESS when `sub_url` is empty.
- `phase-a/bot/handlers/clients.py:195`: bot renders Pro subscription actions
  before direct VLESS actions.
- `phase-a/bot/handlers/clients.py:726`: subscription QR callback keeps
  `prefer_subscription=True`.
- `phase-a/bot/handlers/clients.py:732`: direct VLESS QR callback uses
  `prefer_subscription=False`.
- `phase-a/webapp/dist/app.js:357`: WebApp Subscription card is conditional on
  `cl.sub_url` and precedes the VLESS card.

## Tested

- `bash test_regressions.sh` - pass.
- `PYTHONPYCACHEPREFIX=/private/tmp/govless-pycache-072 python3 -m py_compile phase-a/govlessctl/methods.py phase-a/bot/handlers/clients.py phase-a/bot/handlers/start.py phase-a/govlessctl/auth.py phase-a/govlessctl/daemon.py phase-a/webapp/server.py` - pass.
- `bash -n lib/xui_api.sh govless.sh test_regressions.sh phase-a/systemd/install/install_phase_a.sh` - pass.
- `git diff --check` - pass.

## Remaining Risk

- Live Lite/Pro smoke is still required after deploy to prove Telegram button
  layout and WebApp cards match the source-level invariant on real bots.
- Your 071 `client.add` fuzz proposal is not executed in this patch. I am
  keeping it as a separate test task so we do not mix validation hardening with
  the mode-specific link policy.

## Next Action

Review this patch, then keep bot/WebApp design work aligned with the invariant:
Lite operators see direct VLESS first/only; Pro operators see subscription first
and direct VLESS second.
