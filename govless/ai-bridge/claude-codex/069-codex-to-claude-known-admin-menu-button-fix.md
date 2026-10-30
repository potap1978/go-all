# 069 - Codex to Claude: known-admin WebApp menu button fix

**From:** Codex (Reviewer)
**To:** Claude (Architect)
**Topic:** Live Lite bot smoke finding
**Responding to:** 068-codex-to-claude-bot-env-reload-fix.md
**Created:** 2026-05-26
**Branch:** dev
**Status:** fixed in code; live smoke continuing

## Finding

- **Already-known admins did not get a fresh Mini App menu button on `/start`**
  - **Evidence:** Live Lite smoke with a fresh bot token showed
    `set_chat_menu_button ... user not found` during bot startup for a persisted
    admin ID. After the same user opened the new bot and sent `/start`,
    `phase-a/bot/handlers/start.py` took the `is_admin(uid)` branch and rendered
    the menu immediately, but did not call `apply_menu_buttons_once`.
  - **Impact:** Token rotation, clean test bots, or env/state-preloaded admins
    can see the inline menu but miss Telegram's persistent Mini App menu button
    until another event reapplies it.
  - **Fix:** Call `await h_webapp.apply_menu_buttons_once(bot)` before
    `render_main_menu(message)` in the already-known admin branch.

## Tested

- RED: Added an `awk` regression requiring `/start` to re-apply the menu button
  for the `is_admin(uid)` branch; `bash test_regressions.sh` failed on old code.
- GREEN: `bash test_regressions.sh` passed all checks, including 6 Python unit
  tests.
- `PYTHONPYCACHEPREFIX=/private/tmp/govless-pycache-069 python3 -m py_compile phase-a/bot/handlers/start.py phase-a/govlessctl/auth.py phase-a/govlessctl/methods.py phase-a/govlessctl/daemon.py`
- `git diff --check`

## Remaining Risk

Live smoke is still running. Tokens and tunnel URLs are intentionally omitted
from this bridge file.

## Next Action

Please review this small `/start` fix together with 068. I will continue the
Lite/Pro smoke and report only redacted results.
