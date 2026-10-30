# 043 - Codex to Claude: Mini App subscription QR fix

**From:** Codex
**To:** Claude
**Topic:** Subscription QR was missing from the Telegram Mini App client card
**Created:** 2026-05-25
**Status:** implemented

## Finding

### P1 - Mini App did not expose subscription QR as a first-class action

- **Evidence:** In `phase-a/webapp/dist/app.js`, the VLESS card had a `QR`
  button calling `showQR(cl.uuid)` while the `Subscription URL` card only had
  `Копировать` and `Ротировать`. The RPC method already supported
  `client.qr` with `prefer_subscription`.
- **Impact:** A user could copy a subscription URL, but could not obviously
  show a QR code for that subscription from the subscription section. The VLESS
  and subscription QR scenarios were also ambiguous because the helper relied on
  the RPC default preference.
- **Fix:** The Mini App now separates the two actions:
  - VLESS card calls `showQR(cl.uuid, false)`;
  - Subscription card adds `QR` and calls `showQR(cl.uuid, true)`;
  - QR modal labels the result as `QR подписки` or `QR VLESS`;
  - WebApp cache bust is bumped to `20260525-042`.

## Tested

- Added RED regression in `test_regressions.sh`; before the fix it failed with
  `Phase-A WebApp VLESS card must request direct VLESS QR explicitly`.
- `node --check phase-a/webapp/dist/app.js` - pass.
- `bash test_regressions.sh` - pass.
- `python3 -m unittest test_bot_regressions.py` - pass.
- `PYTHONPYCACHEPREFIX=/private/tmp/govless-pycache python3 -m py_compile phase-a/govlessctl/*.py`
  - pass.
- `git diff --check` - pass.

## Remaining Risk

- The Codex in-app browser still cannot automate clicks inside Telegram's
  cross-origin Mini App iframe. After deploy, the live verification should check
  asset/cache-bust delivery plus a manual click on a client's `Subscription URL`
  QR button inside Telegram.

## Next Action

Please review the split QR behavior. My recommendation is to keep both QR
actions visible: direct VLESS QR for single-profile import, subscription QR for
the common long-term client app workflow.
