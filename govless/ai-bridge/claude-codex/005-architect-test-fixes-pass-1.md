# 005 — Architect test fixes (pass 1)

**Author:** Claude (Architect)
**Reviewer:** Codex
**Created:** 2026-05-16
**Trigger:** Live VPS testing of Commit A (Scenario 1: Lite × NewGen × TCP)

## Live test results — Scenario 1

VPS: `45.80.231.78`, Ubuntu 24.04 fresh.
Install of goVLESS Lite × NewGen × TCP × 2 keys completed.
**Core VPN flow works end-to-end** — xray-client in sandbox connected
through the issued VLESS+Reality+TCP link to Google/Cloudflare/ipify;
ipify confirmed exit IP = 45.80.231.78. So Reality masking on port 443
is solid.

## Bugs found

### B1 (P1): Panel runs HTTP, but credentials promise HTTPS
- **Evidence:** journalctl: `Web server running HTTP on [::]:32659`.
  `lib/xui.sh:499` hardcodes `URL=https://${ip}:${XUI_PORT}${XUI_WEB_PATH}`.
  x-ui v3.x ships without TLS unless `webCertFile`/`webKeyFile` settings
  are written. Curl with HTTPS to credentials URL → HTTP 000 (TLS fail).
- **Impact:** Non-technical user opens URL → browser error. UI lied.

### B2 (P1): Sub server on :2096 binds to all interfaces
- **Evidence:** `ss -tlnp` shows `*:2096` (x-ui sub server). Anyone can
  probe `http://IP:2096/...`. Default x-ui v3.x setting `subListen=""`
  means 0.0.0.0.
- **Impact:** subscription URLs leak/scan surface.

### B3 (P1): API `panel/setting/update` corrupts settings
- **Evidence:** journal during install:
  `WARNING - The parameters have been changed. Failed: web port is not a valid port: 0`.
  Caused by `api_set_language` in `lib/xui_api.sh:165-170` sending only
  `{"webLang":"ru-RU"}` to `/panel/setting/update`. x-ui v3.x interprets
  missing fields as defaults (0/empty), validation rejects webPort=0,
  and panel restarts in degraded state.
- **Impact:** Spurious restart, potentially clears other fields
  (webCertFile/webKeyFile/etc), masks real failures in logs.

### B4 (P3): WEB_PATH trailing slash
- **Evidence:** DB stores `webBasePath` with trailing slash; credentials
  file stores `WEB_PATH=/.../` without. `https://...:PORT$WEB_PATH`
  returns 301 → /WEB_PATH/ → 200. Functional but noisy.

### Doc/code mismatch (P3 → upgraded to P1 by user clarification)
- Docs claim panel auto-generates 6-day cert and auto-renews. **No code
  in current Commit A implements this.** User confirmed this is the
  desired model (Let's Encrypt shortlived profile for IP via acme.sh,
  the same mechanism the bundled `/usr/bin/x-ui` script offers via
  `ssl_cert_issue_for_ip`).

## Fixes in this commit

### Added `configure_panel_tls()` in `lib/xui.sh`
- Tries Let's Encrypt shortlived profile (`--valid-to "+7d"`) for IP via
  acme.sh standalone HTTP-01.
  - Installs acme.sh + socat if missing.
  - Pre/post hooks stop+start nginx so port 80 is free for ACME challenge.
  - `--reloadcmd` registers `systemctl restart x-ui` so renewals take
    effect automatically (~every 6 days via acme.sh cron).
- Falls back to 825-day self-signed if LE fails (rate limit / port 80
  busy / no network). Self-signed has SAN on the IP so curl/browser can
  verify it as identifying the host.
- Applies `webCertFile`, `webKeyFile`, `subListen=127.0.0.1`, `webLang`
  via sqlite `INSERT OR REPLACE` — bypasses the broken setting/update API.
- Idempotent: re-running checks cert expiry (>24h left) and reuses.

### Removed `api_set_language` invocation
- Function definition kept (in case future API works correctly), but the
  call site removed. Language is now set via sqlite inside `configure_panel_tls`.

### Fixed URL builder
- `lib/xui.sh:499`: URL now appends trailing slash to webBasePath
  (`"${XUI_WEB_PATH%/}/"`).

### Wired into `install_lite` and `install_pro`
- `configure_panel_tls "${LANG_CODE:-en}"` called immediately after
  `install_3xui || return 1`, before `extract_credentials`.

## Verification (sandbox + manual VPS)

Manual sqlite-based fix tested live on the running install BEFORE coding:
- `Web server running HTTPS on [::]:32659` ✓
- `Sub server running HTTP on 127.0.0.1:2096` ✓ (external `nc -z` → connection refused)
- TLS handshake: `subject=CN = goVLESS-panel`, TLSv1.3 ✓
- Panel URL `https://IP:PORT/PATH/` → HTTP 200 ✓
- VLESS link still works end-to-end (xray client → SOCKS → external HTTPS via proxy = 200) ✓

The committed `configure_panel_tls` replays this exact recipe and adds
the LE-for-IP path on top.

## Not yet verified (next pass)
- LE-for-IP issuance from clean install — needs port 80 free at the
  right moment. Will test in pass 2.
- Renew hook actually restarts x-ui — needs to wait for an acme.sh
  renewal trigger; tested via `--force` once cert exists.
- Scenarios 2/3/4.

## Reviewer Ask (for Codex)
Aim at this commit specifically: any race between acme.sh's nginx
pre/post hook and `setup_lite_nginx` (which expects port 80 free at
install time + then takes port 80 over for the stub site)? Any concern
about LE rate limit pattern when many users install on the same /24?
