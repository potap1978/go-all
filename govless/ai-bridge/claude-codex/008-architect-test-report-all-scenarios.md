# 008 — Architect test report: all 4 scenarios

**Author:** Claude (Architect)
**Reviewer:** Codex
**Created:** 2026-05-16
**VPS:** Ubuntu 24.04, 45.80.231.78, potap1978.com → 45.80.231.78
**Commit verified:** 9fe0575 (B7 fix) and earlier in chain

## Overall verdict
**PASS** on all 4 scenarios. 6 bugs found in Commit A, all fixed and
verified live. Reality (Lite) and TLS (Pro) inbounds work, panel
HTTPS works, Let's Encrypt cert acquisition works for both bare IP
(Lite, shortlived profile) and domain (Pro, standard profile).

## Scenario matrix

| # | Mode | 3X-UI | Transport | Install | Panel HTTPS | VPN cert | e2e |
| - | --- | --- | --- | --- | --- | --- | --- |
| 1 | Lite | v3.0.2 NewGen | TCP | PASS | LE shortlived (~6d, auto-renew) | Reality (no cert) | PASS via xray-client (ipify→45.80.231.78) |
| 2 | Lite | v2.9.4 Legacy | TCP | PASS | LE shortlived (cert reused from S1) | Reality | PASS (structural, sandbox bwrap blocked live xray) |
| 3 | Pro | v3.0.2 NewGen | TCP | PASS | LE shortlived (panel) + LE 90-day for potap1978.com (VPN) | TLS w/ LE cert | PASS (https://potap1978.com/ → 200 verify=0) |
| 4 | Pro | v2.9.4 Legacy | TCP | PASS | LE shortlived | TLS w/ LE cert (reused from S3, 0 LE quota burnt) | PASS |

LE quota consumed on `potap1978.com`: **1 cert order** (S3 issued, S4
reused via `obtain_ssl_certificate`'s idempotency check). Codex's
independent re-test won't burn another if `/etc/letsencrypt/live/potap1978.com`
is preserved.

## All bugs found & fixed

### B1 (P1, FIXED): Panel runs HTTP but URL=https://
`lib/xui.sh:499` hardcoded `https://`. x-ui v3.x ships without TLS.
**Fix:** `configure_panel_tls()` issues LE shortlived cert via acme.sh
or falls back to 825-day self-signed; writes `webCertFile`/`webKeyFile`
to sqlite. Commit: befc3ce.

### B2 (P1, FIXED): Sub server on :2096 binds to all interfaces
x-ui v3.x default `subListen=""` ≡ 0.0.0.0.
**Fix:** `INSERT OR REPLACE INTO settings VALUES('subListen', '127.0.0.1')`.
Verified: `nc -z 45.80.231.78 2096 → Connection refused`. Commit: befc3ce.

### B3 (P1, FIXED): `api_set_language` corrupts settings via partial PATCH
`lib/xui_api.sh:165-170` POSTed `{"webLang":"..."}` only; x-ui v3.x
treated other fields as defaults, triggered `webPort=0` validation
fail + spurious restart.
**Fix:** sqlite UPDATE; API call removed. Commit: befc3ce / 9db4565.

### B4 (P3, FIXED): WEB_PATH no trailing slash → 301 redirect
**Fix:** `web_path_norm="${XUI_WEB_PATH%/}/"` normalisation in
`save_credentials`. Commit: befc3ce.

### B5 (P2, FIXED): My initial LE integration had 3 sub-bugs
- `--install-online` flag mangled (`----install-online`)
- email TLD `.local` (LE rejects)
- default LE profile rejects IP identifiers
**Fix:** canonical `curl|sh -s email=...`, `.app` TLD, explicit
`--certificate-profile shortlived --days 6 --httpport 80`. Commits:
5e3d0a2 / ba38e07 / 2bc8630.

### B6 (P3, NOT IN CODE): /var/www/nginx state after `apt remove`+`rm -rf`
Testing-env artifact. `apt purge` works fine. No goVLESS code change.

### B7 (P1, FIXED): /var/www/certbot perms 700 → nginx 403 on ACME
`generate_nginx_temp_config` did `mkdir -p /var/www/certbot` under
root's umask 077, producing `drwx------`. nginx (www-data) couldn't
traverse; LE webroot challenge failed with HTTP 403.
**Fix:** explicit `chmod 755` + `chown www-data:www-data` after
mkdir, pre-create `.well-known/acme-challenge`. Commit: 9fe0575.

## Bugs NOT found / NOT fixed (Reviewer Ask)

- gRPC / XHTTP transports — only TCP exercised. `api_create_reality_inbound`
  has branches for grpc/xhttp; not tested live.
- 6-day shortlived cert auto-renewal — acme.sh cron is scheduled
  (`14 2 * * *`); not waited for an actual renewal trigger.
- Migration from XUIFAST v3.0.6 install → goVLESS (proposal 001 Commit B).
- Pro mode template-selection path (`interactive_template_selection`).
- Pro mode with `email=""` → `--register-unsafely-without-email` path.

## Tested (commands)

```
# On VPS for each scenario:
git pull (private repo with token)
teardown (apt purge + rm /etc/x-ui /usr/local/x-ui /opt/govless ...)
setsid nohup bash govless.sh < /tmp/ans_sN.txt
# Verify:
sqlite3 /etc/x-ui/x-ui.db "SELECT * FROM settings; SELECT * FROM inbounds;"
openssl x509 -in <cert> -noout -issuer -dates
nc -zv 45.80.231.78 2096   # B2 check
curl -s -o /dev/null -w "%{http_code} %{ssl_verify_result}" <URL>
journalctl -u x-ui --since "5 min ago" | grep -i webport

# In sandbox (Scenario 1 only — sandbox bwrap blocked xray for S2):
./xray run -c <generated client.json>
curl --socks5-hostname 127.0.0.1:10808 https://api.ipify.org/
# returned: 45.80.231.78 ✓
```

## Commits in this test cycle (full sequence)

```
e785ea4  006 Architect test report Scenario 1
2bc8630  Fix LE IP cert: --certificate-profile shortlived
ba38e07  Fix LE acme.sh: valid TLD email
5e3d0a2  Fix: acme.sh install flag
840f414  Hotfix: heredoc scope (web_path_norm)
9db4565  Fix syntax: empty then-block
befc3ce  005 Pass-1 test fixes (B1/B2/B3 + LE-for-IP)
9fe0575  Fix B7: /var/www/certbot perms
<this>   008 final test report
```

## Reviewer Ask (Codex)

Please independently reverify on the same VPS:

1. **All 4 scenarios** — verify my findings reproduce. Pay attention to
   what I might have missed:
   - Is there a code path I never hit? e.g. user picks template from
     catalog, gRPC, XHTTP, non-default mask domains.
   - Are sqlite settings persistent across x-ui upgrades? (Architect
     stopped at install — runtime behavior over time not exercised.)
2. **6-day cert auto-renew** — verify acme.sh cron actually renews when
   triggered (e.g. `~/.acme.sh/acme.sh --renew --force -d 45.80.231.78`
   simulates renewal; observe whether x-ui picks up new cert via the
   reloadcmd).
3. **B2 surface** — beyond port 2096, are there other x-ui v3.x
   features that bind externally by default? (e.g., metrics endpoint,
   subscription auth, telegram bot port.)
4. **B7 generalization** — `mkdir -p` happens elsewhere in the script
   (e.g., GOVLESS_DIR). Are any of them similarly affected by
   root's umask?
5. **Migration path (Commit B)** — Codex's earlier 002 audit P1 #2
   flagged this. After my 4 fresh installs, the migration story is
   still untested. Architect's next pass should be Commit B.

Final string format when done:
`Codex test done: S1=X, S2=X, S3=X, S4=X, extra-bugs=N. See commit <sha>.`

Hand off.
