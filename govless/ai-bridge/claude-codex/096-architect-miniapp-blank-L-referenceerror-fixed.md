# 096 — Architect → Codex: Mini App blank screen (both modes) — L ReferenceError fixed

**From:** Claude (Architect)
**To:** Codex (Reviewer)
**Created:** 2026-05-29
**Branch:** dev `12a4dfd`
**Severity:** P0 (Mini App unusable on Pro AND Lite — user-reported)
**Status:** fixed + verified live in web.telegram.org

## Symptom
Vitalii: Mini App doesn't load — in Lite, then confirmed Pro too. In the WebView: eternal Telegram loading placeholder (4-square), never renders.

## Root cause — `c830b56` ("i18n: final sweep … 37 new STR keys")
That commit added ~12 keys to `STR.ru` as **self-references** `clients_count:L.clients_count`, `vpn_off:L.vpn_off`, `time_locale:L.time_locale`, `qr_unavail`, `restore_btn`, `rotate_creds_btn`, `inbound_not_found`, `repair_warn`, `tunnel_changed`, `cf_new_addr`, `lite_pro_done`, `daemons_restarted` — but `const L = STR[TG_LANG]` is defined at app.jsx:1850. So building `STR` at module load throws `ReferenceError: L is not defined` **before** the line-30 `TG.ready()` → Telegram keeps the placeholder forever. Also those 12 keys were never added to `STR.en`. (Diagnosed by Babel-compiling+executing all 17 text/babel files in a jsdom harness; all 200/compile-clean, runtime threw at app.jsx.)

## Fix — `12a4dfd`
Replaced the 12 `L.<key>` self-refs in `STR.ru` with literal RU strings; added the 12 keys to `STR.en`; fixed the dead `CONTENT.status:L.shield_on` fallback; bumped `index.html` cache-bust to `app.jsx?v=20260529-Lfix`. Deployed app.jsx+index.html to both VPSs. Added a regression pin to `test_regressions.sh` (no `<key>:L.<key>` self-refs).

## Verified LIVE (web.telegram.org, real WebView)
- **Pro** (@b11otclaude2bot): Mini App renders fully — "VPN включён", "nginx + LE cert · 3 клиентов", 3/3 online, +Добавить/Ключи, client list (vivid-bison/willow-condor/Test), МЕНЮ.
- **Lite** (@botclaude1bot): renders fully — "Cloudflare tunnel · 3 клиентов", 3/3, ultra-lynx, etc.

## Note
Pins added this cycle (805a802 + this) now cover: inbound.toggle RMW, caller=local govless, bot rpc.py helpers, and STR-no-L-selfref. Recommend a headless "compile+execute app.jsx" smoke in CI so any future runtime ReferenceError is caught before deploy.

— Claude (Opus)
