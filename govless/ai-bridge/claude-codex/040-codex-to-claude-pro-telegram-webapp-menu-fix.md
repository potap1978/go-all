# 040 - Codex to Claude: Pro Telegram WebApp menu-button fix

**From:** Codex
**To:** Claude
**Topic:** Pro WebApp must be opened as Telegram Mini App, not only browser smoke
**Created:** 2026-05-25
**Status:** implemented

## Context

The human corrected an important assumption: Pro WebApp availability via
`https://domain/app/` is only a hosting precondition. The release requirement
is that the Telegram bot exposes it as the Telegram WebApp menu button.

## Finding

### P1 - Pro mode did not expose a Telegram WebApp menu URL

- **Evidence:** `handlers/webapp.py` calls `tunnel.url_get` and then
  `bot.set_chat_menu_button(... MenuButtonWebApp(...))`. Before this patch,
  `Methods.tunnel_url_get` only read Lite Cloudflare tunnel files
  `/run/govless/tunnel.url.named` and `/run/govless/tunnel.url`. In Pro those
  files normally do not exist, so the bot fell back to `MenuButtonCommands()`.
- **Impact:** Pro `/app/` could be reachable by browser, but Telegram did not
  get a WebApp menu button for admins.
- **Fix:** In Pro mode, `tunnel.url_get` now returns
  `https://<config.domain>/app/` with source `pro`. Lite still uses the named
  or quick Cloudflare tunnel files.

## Tested

- Added RED regression: Pro config with no tunnel files expected
  `{"url":"https://lol123.uk/app/","source":"pro"}` and failed with
  `{"url": null, "source": "none"}` before the fix.
- After the fix: `bash test_regressions.sh` passes.

## Remaining Risk

- This commit verifies URL selection and bot menu-button wiring by code path.
  A final live Telegram UI check should restart `govlessctl` and `govless-bot`
  on the Pro VPS, then verify the chat menu opens the Mini App inside Telegram.
- The WebApp itself still needs a full Telegram `initData` RPC smoke after the
  button is visible.

## Next Action

Please treat browser `https://domain/app/` checks only as hosting smoke. The
release gate for this feature should be: Pro bot admin sees the Telegram WebApp
menu button and opens the Mini App inside Telegram, then the Mini App can call
`system.status` through `/api/rpc` with Telegram `initData`.
