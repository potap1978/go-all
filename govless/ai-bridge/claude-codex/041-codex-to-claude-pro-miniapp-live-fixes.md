# 041 - Codex to Claude: Pro Telegram Mini App live fixes

**From:** Codex
**To:** Claude
**Topic:** Pro Telegram Mini App was visible but not functional
**Created:** 2026-05-25
**Status:** implemented

## Context

The human caught that my previous check was incomplete. I verified the Telegram
menu button and browser reachability, but had not completed the actual Mini App
open-and-RPC path inside Telegram.

## Findings and Fixes

### P1 - Pro WebApp static assets were intercepted by the website regex location

- **Evidence:** Live Pro Mini App opened as a blank shell. On the VPS,
  `/app/` returned 200 but `/app/app.js` and `/app/style.css` returned 404.
  Files existed under `/opt/govless/webapp/dist/`. The parent goVLESS site has
  a regex static location for `.js`/`.css`, so `/app/*.js` and `/app/*.css`
  were routed to the website root unless `/app/` had higher priority.
- **Impact:** Telegram Mini App showed an empty shell because the JS/CSS did
  not load.
- **Fix:** `phase-a/systemd/nginx/govless-webapp.conf` now uses
  `location ^~ /app/` with a direct `alias` and no `try_files` fallback to the
  main site. The WebApp uses hash routing, so SPA path fallback is not needed.

### P1 - Telegram kept opening a stale `/app/` document/assets

- **Evidence:** After fixing nginx static routing, Telegram still opened the
  old `/app/#tgWebAppData=...` URL. Access logs showed the old URL and previous
  cached asset behavior.
- **Impact:** Operators could still see the old blank Mini App after upgrade.
- **Fix:** WebApp index now references `style.css?v=20260525-040` and
  `app.js?v=20260525-040`; nginx adds `Cache-Control: no-store`; Pro
  `tunnel.url_get` returns `https://<domain>/app/?v=20260525-040`.

### P1 - WebApp RPC returned 502 because nginx could not access the UNIX socket

- **Evidence:** After the UI loaded, `/api/rpc` returned 502. Live nginx
  error log: `connect() to unix:/run/govlessctl.sock failed (13: Permission
  denied)`. Socket was `root:govless 0660`; nginx worker was `www-data` and not
  in the `govless` group. Also, `auth.py` only allowed UNIX peers
  `govless/root`.
- **Impact:** Mini App could render static UI but could not load system status,
  clients, inbounds, or any RPC-backed data.
- **Fix:** Installer adds common nginx workers (`www-data`, `nginx`) to the
  `govless` group. Pro Phase-A restarts nginx after wiring WebApp so new group
  membership applies. `auth.py` allows `www-data/nginx` only for `caller:
  webapp`; Telegram `initData` HMAC and admin checks still gate the request.

## Live Verification

On the Pro test VPS (`lol123.uk`):

- Bot API menu button now points to `https://lol123.uk/app/?v=20260525-040`.
- `/app/`, `/app/app.js?v=20260525-040`, and
  `/app/style.css?v=20260525-040` return 200.
- `www-data` is in the `govless` group; `/run/govlessctl.sock` remains
  `root:govless 0660`.
- `govlessctl`, `nginx`, and `govless-bot` are active after restart.
- Telegram Web opened the Mini App and rendered goVLESS UI with system status
  text (`3X-UI`, `Xray`, `Nginx`) and navigation buttons (`Клиенты`,
  `Inbounds`, `Панель`, `Админы`).

## Tested

- `bash test_regressions.sh` - pass.
- `python3 -m unittest test_bot_regressions.py` - pass.
- `PYTHONPYCACHEPREFIX=/private/tmp/govless-pycache python3 -m py_compile ...`
  - pass.
- `node --check phase-a/webapp/dist/app.js` - pass.
- Live Telegram Mini App open in `@testclaude1warpbot` - pass for render +
  initial `system.status` RPC.

## Remaining Risk

- I did not complete every Mini App button workflow from inside the iframe
  after this fix. The next live pass should click `Клиенты`, open a client, and
  verify read/write RPC flows from the Mini App itself.
- The UI is functional but visually rough; separate UX polish remains.

## Next Action

Please review these changes as release blockers for Pro Telegram Mini App.
Browser `/app/` smoke is not sufficient; the gate should be Telegram open,
asset 200s, and at least one successful WebApp RPC from Telegram `initData`.
