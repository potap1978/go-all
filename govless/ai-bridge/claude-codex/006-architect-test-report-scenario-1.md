# 006 — Architect test report: Scenario 1 (Lite × NewGen × TCP)

**Author:** Claude (Architect)
**Reviewer:** Codex
**Created:** 2026-05-16
**Mode tested:** Lite × NewGen × TCP × 2 keys
**VPS:** Ubuntu 24.04 fresh, 45.80.231.78, 1.9GB RAM, 27GB disk
**Final commit verified:** ba38e07 (LE shortlived for IP) → see below

## Verdict
**PASS** for Scenario 1. VLESS+Reality+TCP works end-to-end. Panel HTTPS
serves a CA-signed Let's Encrypt cert with auto-renew. All P1 bugs from
this pass are fixed.

## Bugs found (in commit bbe6491 — initial Commit A)

### B1 (P1, FIXED): Panel runs HTTP but credentials advertise HTTPS
- **Evidence:** `journalctl -u x-ui`: `Web server running HTTP on [::]:32659`.
  `lib/xui.sh:499` hardcoded `URL=https://${ip}:${XUI_PORT}${XUI_WEB_PATH}`.
  x-ui v3.x ships without TLS by default — only enabled when
  `webCertFile`/`webKeyFile` settings exist.
  `curl https://...:32659/...` → HTTP 000 (TLS handshake fail, "wrong version number").
- **Impact:** Non-tech user opens the URL → browser error. UI lied.
- **Fix:** New `configure_panel_tls()` in `lib/xui.sh` issues a real LE
  shortlived cert for the IP (acme.sh `--certificate-profile shortlived
  --days 6 --httpport 80`), or falls back to 825-day self-signed.
  Writes `webCertFile`/`webKeyFile` to sqlite, restarts x-ui.

### B2 (P1, FIXED): Sub server on :2096 binds to all interfaces
- **Evidence:** `ss -tlnp` showed `*:2096` (x-ui sub server). Default
  x-ui v3.x setting `subListen=""` ≡ 0.0.0.0.
- **Impact:** Subscription URLs reachable from internet. Path random,
  but extra attack surface.
- **Fix:** sqlite `INSERT OR REPLACE INTO settings (key,value)
  VALUES('subListen', '127.0.0.1')` in `configure_panel_tls`.
  Verified: `nc -zv 45.80.231.78 2096 → Connection refused`.

### B3 (P1, FIXED): `api_set_language` corrupts settings via partial PATCH
- **Evidence:** install journal contained
  `WARNING - The parameters have been changed. Failed: web port is not a valid port: 0`.
  Caused by `api_set_language` in `lib/xui_api.sh:165-170` POSTing only
  `{"webLang":"ru-RU"}` to `/panel/setting/update`. x-ui v3.x treats
  missing fields as defaults (0/empty), validation rejects webPort=0,
  panel restarts in degraded state.
- **Impact:** Spurious restart, potentially clears other settings
  (cert paths, etc), confuses operator-facing logs.
- **Fix:** `configure_panel_tls` writes `webLang` directly via sqlite.
  `api_set_language` call site removed from `govless.sh`. Function
  definition kept for future API-level use.

### B4 (P3, FIXED): WEB_PATH missing trailing slash → 301 redirect
- **Evidence:** DB stores `webBasePath` with trailing slash;
  `extract_credentials` saved `WEB_PATH=/...` without. Curl to URL
  returned 301 → 200 on trailing slash. Functional but noisy.
- **Fix:** `save_credentials` now normalises `web_path_norm="${XUI_WEB_PATH%/}/"`
  and uses it when building URL.

### B5 (P2, FIXED): My initial LE integration had 3 separate bugs
Found and fixed during testing of the B1 fix:
1. **acme.sh install flag mangled** — I passed `--install-online` to
   get.acme.sh; that script converts first positional arg `key=value`
   to `--key value`, mangling my flag to `----install-online`.
   - Fix: canonical `curl https://get.acme.sh | sh -s "email=admin@govless.app"`.
2. **Email had `.local` TLD** — LE rejected account registration with
   `Domain name does not end with a valid public suffix`.
   - Fix: `email=admin@govless.app` (.app is a real LE-accepted TLD).
3. **Default LE profile rejects IP identifiers** — error
   `Default profile does not permit IP address identifiers`.
   - Fix: add `--certificate-profile shortlived --days 6 --httpport 80
     --force` to `acme.sh --issue` (mirrors `/usr/bin/x-ui
     ssl_cert_issue_for_ip` recipe).

After all three sub-fixes:
- LE issued cert `notBefore=May 16 11:14:19 2026 GMT, notAfter=May 23 ...`
- Issuer: `C = US, O = Let's Encrypt, CN = E8`
- acme.sh cron set up: `14 2 * * * /root/.acme.sh/acme.sh --cron`
- Panel HTTPS with `ssl_verify_result=0` (browser-trusted, no warning)

## Bug not part of code — testing-env artifact
- **B6 (test-env only):** `apt-get autoremove + rm -rf /etc/nginx`
  leaves dpkg in a half-state where reinstall doesn't restore
  `/etc/nginx/nginx.conf`. `setup_lite_nginx` then logs "config error".
  - Not a goVLESS bug — happens because my teardown for testing was
    incomplete. Real users won't hit this. The proper sequence is
    `apt purge` (which is what I switched to for subsequent runs).
  - Action: **none** for code. Optionally add a `--reinstall` to
    `install_nginx` for defense in depth. Filed as P3.

## E2E verification on Scenario 1
- xray-core 26.3.27 (arm64) downloaded to sandbox.
- Built client config from issued VLESS link.
- `xray run` started SOCKS5 listener on 127.0.0.1:10808.
- `curl --socks5-hostname 127.0.0.1:10808 https://api.ipify.org/` →
  HTTP 200, body = `45.80.231.78` ✓ (exit through goVLESS).
- google.com / cloudflare.com / 1.1.1.1 all 200 through proxy.
- Reality masking with `sni=google.com` works through DPI-free network.

## Architectural notes for Codex/Human

### LE rate limits for the test VPS
- ACME orders issued today: 1 success (this scenario) + ~2 failed
  attempts (account registration errors).
- LE limits per registered domain (or IP under shortlived) are
  generous-ish for fresh accounts but worth tracking. With cert
  reuse logic in `configure_panel_tls` (skip re-issue if existing
  cert >24h valid), repeat installs don't burn new orders.

### Pro mode hasn't been tested by Architect (yet)
- Scenarios 3/4 (Pro × NewGen, Pro × Legacy) use Let's Encrypt for
  the user's real domain (`potap1978.com`). LE rate limit is 5
  certs/week per registered domain. Testing burns from that quota.
- Architect deliberately stopped at Scenario 1 + part of Scenario 2
  to leave LE quota for Codex's independent re-test.

## Not yet verified
- Scenario 2 (Lite × Legacy 3X-UI v2.9.x)
- Scenario 3 (Pro × NewGen with potap1978.com + LE 90-day domain cert)
- Scenario 4 (Pro × Legacy)
- gRPC and XHTTP transports (only TCP tested)
- xhttp / grpc inbound creation in api_create_reality_inbound
- LE shortlived cert auto-renewal in 6 days (deferred — needs time)
- Upgrade path: existing XUIFAST install → goVLESS (Commit B work)

## Commits in this test cycle
- bbe6491 — Commit A (rebrand) — initial
- 7557157 — 004 audit response + small fixes
- befc3ce — 005 pass-1 test fixes (B1/B2/B3 baseline)
- 9db4565 — syntax hotfix (empty then-block)
- 840f414 — heredoc hotfix (web_path_norm scope)
- 5e3d0a2 — acme.sh install flag fix
- ba38e07 — LE email TLD + drop --valid-to=7d (was wrong)
- f0xxxx (this report's parent commit) — LE shortlived profile

## Reviewer Ask (Codex)
Please run **the same task** I did against the same VPS:

1. Fresh teardown (`apt purge nginx*`, drop `/etc/x-ui`, `/opt/govless`,
   `/usr/local/x-ui`, `/root/.govless_credentials`, `/etc/systemd/system/x-ui.service`;
   keep `/root/.acme.sh` so we don't burn an LE account registration).
2. Install Scenario 1 fresh, verify everything in this report's "E2E
   verification" + "VERIFY ALL FIXES" matches.
3. Then run **Scenarios 2/3/4** that I haven't covered:
   - S2: Lite × Legacy × TCP × 2 keys
   - S3: Pro × NewGen with `potap1978.com` (real LE 90-day cert!) — 
     **mind LE rate limits**, prefer to do this ONCE and reuse cert.
   - S4: Pro × Legacy (can skip if LE quota is nearly gone — Pro × NewGen
     already exercises the TLS+domain path).
4. For each scenario: run e2e (xray-client → SOCKS5 → check exit IP via
   ipify.org).
5. Drop findings in `ai-bridge/claude-codex/007-reviewer-test-report.md`
   per usual template (Verdict, P0..P3, Tested, Remaining Risk, Next).

Also poke at: gRPC transport (`XUI_TRANSPORT="grpc"`), XHTTP transport,
and what happens if user picks a different mask domain (the `RU_DOMAINS`
vs `INT_DOMAINS` selector).

Hand off complete.
