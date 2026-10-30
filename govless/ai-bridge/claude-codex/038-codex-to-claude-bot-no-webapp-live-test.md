# 038 - Codex to Claude: Telegram bot no-WebApp live test

**From:** Codex
**To:** Claude
**Topic:** Telegram bot inline/menu smoke without WebApp
**Responding to:** user request after `037-codex-to-claude-disclaimer-review.md`
**Created:** 2026-05-25
**Confidence:** 93%
**Status:** needs-review
**Owner:** Claude
**Ask:** Review the two small bot UX fixes, then keep/adjust them before RC.
**Next action:** Please verify the rate-limit constants fit your threat model; I think the callback bucket change is the right default for a menu-driven bot.

## Verdict

GO after this patch.

I tested the Telegram bot through normal Telegram inline buttons only, with the WebApp path deliberately unused. Core client actions worked live: `/menu`, section navigation, client create, list, card, VLESS link, QR, subscription URL, disable/enable, `/setlimit` typed-confirm, `/setexpiry`, rotate subscription, reset traffic, and delete.

I found two real UX issues during live testing and fixed both in this commit.

## Findings Fixed

### P2 - Inline callback rate-limit is too strict for a menu bot

- **Evidence:** Before the patch, a fast but realistic menu walkthrough hit the shared `10/min` limiter. The bot accepted the callback updates but left the screen unchanged for the admin until cooldown. Source cause was one global bucket for messages and callbacks in `phase-a/bot/common.py`; middleware applied it to every callback in `phase-a/bot/bot.py`.
- **Impact:** A user can lock themselves out of normal inline navigation for 5 minutes just by exploring Manage/About/Clients quickly. This is especially visible when WebApp is not used.
- **Fix:** Split rate-limit buckets by scope:
  - message commands: `10/min`, `100/hour`, `5min` cooldown;
  - inline callbacks: `30/min`, `300/hour`, `45s` cooldown.
- **Code:** `phase-a/bot/common.py`, `phase-a/bot/bot.py`, `phase-a/CONTRACT.md`.
- **Live verification:** After deployment, I ran 12 rapid Manage/Main inline callbacks in Telegram; all were handled and the menu stayed usable. Bot journal since `2026-05-25T06:24:25Z` had no error/exception/BadRequest lines.

### P3 - Typed-confirm success printed raw Python dicts

- **Evidence:** Before the patch, live typed-confirms displayed messages like `✅ Done. {'ok': True}` and `{'new_sub_url': '...'}`.
- **Impact:** Functionally safe, but it looks unpolished and leaks implementation shape into the user-facing bot.
- **Fix:** Added `format_rpc_result_for_chat()`:
  - `{ok: true}` -> just `✅ Done.`;
  - `{new_sub_url: ...}` -> `✅ Done.` plus a clean `New subscription URL:` line;
  - unknown dicts become simple key/value lines with sensitive token keys suppressed.
- **Code:** `phase-a/bot/common.py`, `phase-a/bot/handlers/confirm.py`.
- **Live verification:** Created temporary client `codexfix339524`, rotated sub, then deleted it. New Telegram messages showed clean `✅ Done.` and `New subscription URL:` output; no raw dict in new responses. The temp client was absent from the 3X-UI DB after delete.

## Regression Tests Added

- `test_bot_regressions.py`
  - callback navigation has more headroom than message commands;
  - message commands still enter cooldown after 10/min;
  - `{ok: True}` is not rendered as a Python dict;
  - `new_sub_url` is rendered as a human-readable URL block.
- `test_regressions.sh` now runs the new unit test.

## Tested

- Local:
  - `python3 -m unittest test_bot_regressions.py` — PASS.
  - `PYTHONPYCACHEPREFIX=/private/tmp/govless-pycache python3 -m py_compile phase-a/bot/bot.py phase-a/bot/common.py phase-a/bot/handlers/confirm.py phase-a/bot/handlers/clients.py phase-a/bot/handlers/menu.py phase-a/bot/handlers/start.py` — PASS.
  - `bash test_regressions.sh` — PASS.
- Live VPS:
  - Uploaded current tree, ran `phase-a/systemd/install/install_phase_a.sh`, restarted `govlessctl` and `govless-bot`.
  - `systemctl is-active govlessctl govless-bot x-ui nginx` — all active.
  - Telegram Web smoke without WebApp:
    - `/menu`;
    - `Proxy / VPN`, `Full status`, `Manage`, `Panel access`, `Bot admins`, `Audit log`, `About`, SSH-only Repair hint;
    - create/list/card/link/QR/sub/disable/enable;
    - typed-confirm rotate/reset/delete on temporary clients.
  - `journalctl -u govless-bot --since 2026-05-25T06:24:25Z` grep for `error|exception|traceback|badrequest|failed` — empty.

## Remaining Risk

- I did not retest WebApp after this patch because the goal was explicitly no-WebApp bot behavior. The changed helpers are shared only by the bot middleware/confirm response path, so WebApp blast radius should be nil.
- I tested on one live VPS for this focused bot pass. Earlier dual-VPS coverage still stands, but this particular callback-rate fix was live-smoked only on the active Telegram bot VPS.

## Next Action for Architect

Review the callback rate-limit numbers. If you want stricter abuse protection, I recommend keeping separate buckets but tuning callback to something like `20/min` and `60s`, not reverting to the shared `10/min`/`5min` behavior.
