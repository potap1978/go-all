# 010 — Architect matrix test report (12 cells × 2 keys = 24 e2e runs)

**Author:** Claude (Architect)
**Reviewer:** Codex
**Created:** 2026-05-16
**VPS:** Ubuntu 24.04, `45.80.231.78`, `potap1978.com` → that IP
**Final commit verified:** `<see git log e785ea4..HEAD>` — chain
includes all fixes B1-B9.

## Overall verdict
**PASS** on all 12 cells × 2 keys = **24/24 e2e checks** after fixes.
Both Lite (Reality) and Pro (TLS) modes work on both NewGen 3.0.2 and
Legacy 2.9.4 panels, across all three supported transports
(TCP/XHTTP/gRPC). Each install creates two distinct users, and BOTH
their VLESS links work end-to-end through xray-client → SOCKS5 →
external HTTPS — exit IP confirmed `45.80.231.78` in every cell.

## Matrix

| # | mode | panel  | transport | install | key1 | key2 | cert issuer |
| - | ---- | ------ | --------- | ------- | ---- | ---- | ----------- |
| 1 | Lite | NewGen | TCP       | OK | PASS | PASS | LE shortlived (~6d) |
| 2 | Lite | NewGen | XHTTP     | OK | PASS | PASS | LE shortlived |
| 3 | Lite | NewGen | gRPC      | OK | PASS | PASS | LE shortlived |
| 4 | Lite | Legacy | TCP       | OK | PASS | PASS | LE shortlived |
| 5 | Lite | Legacy | XHTTP     | OK | PASS | PASS | LE shortlived |
| 6 | Lite | Legacy | gRPC      | OK | PASS | PASS | LE shortlived |
| 7 | Pro  | NewGen | TCP       | OK | PASS | PASS | LE shortlived (panel) + LE 90d for potap1978.com (VPN) |
| 8 | Pro  | NewGen | XHTTP     | OK | PASS¹| PASS¹| same |
| 9 | Pro  | NewGen | gRPC      | OK | PASS | PASS | same |
| 10| Pro  | Legacy | TCP       | OK | PASS | PASS | same |
| 11| Pro  | Legacy | XHTTP     | OK | PASS¹| PASS¹| same |
| 12| Pro  | Legacy | gRPC      | OK | PASS | PASS | same |

¹ Failed in first matrix run (B9), passed after fix `7da9a09` and re-test.

## Full bug list discovered (chronological)

### B1 (P1, FIXED commit befc3ce): Panel HTTP/HTTPS mismatch
Hardcoded `URL=https://...` but x-ui v3.x ships without TLS by default.
Fix: `configure_panel_tls()` issues LE shortlived for IP via acme.sh,
falls back to 825d self-signed.

### B2 (P1, FIXED befc3ce): Sub server on :2096 binds to all interfaces
x-ui v3.x default `subListen=""` ≡ 0.0.0.0. Fix: sqlite UPDATE
`subListen=127.0.0.1`. Verified `nc` refused externally. NB: Legacy
2.9.4 doesn't have sub-server feature, so this is NewGen-only.

### B3 (P1, FIXED befc3ce): `api_set_language` corrupts settings
Partial PATCH to `/panel/setting/update` set webPort=0; x-ui restarted
in degraded state. Fix: write `webLang` via sqlite, drop API call.

### B4 (P3, FIXED befc3ce): URL no trailing slash → 301 redirect
Fix: `web_path_norm="${XUI_WEB_PATH%/}/"` in `save_credentials`.

### B5 (P2, FIXED 5e3d0a2/ba38e07/2bc8630): My acme.sh integration had 3 sub-bugs
1. Wrong flag `--install-online` → mangled to `----install-online`.
2. Email `admin@govless.local` → LE rejects `.local` TLD.
3. Default LE profile rejects IP identifiers — needed `--certificate-profile shortlived --days 6 --httpport 80`.

### B6 (test-env, NOT IN CODE): apt remove+rm vs purge
My teardown leaves dpkg in half-state. Not a goVLESS bug — fresh VPS or `apt purge` works fine.

### B7 (P1, FIXED 9fe0575): `/var/www/certbot` perms 700 blocks LE HTTP-01
mkdir under root's umask 077 → drwx------. nginx (www-data) couldn't
traverse, LE webroot challenge HTTP 403. Fix: explicit `chmod 755` +
`chown www-data:www-data` + pre-create `.well-known/acme-challenge`.

### B8 (P1, FIXED 6dde757): Pro mode ignores transport choice
`api_create_tls_inbound` hardcoded `"network": "tcp"`. `generate_all_vless_links` Pro branch hardcoded `type=tcp`. User picking XHTTP or gRPC in Pro silently got TCP inbound + TCP link. Fix: pass `XUI_TRANSPORT` to python heredoc, transport-specific `tcpSettings`/`xhttpSettings`/`grpcSettings` + matching link query params.

### B9 (P1, FIXED 7da9a09): Pro+XHTTP fails e2e due to ALPN mismatch
After B8, install worked but xray-client got HTTP 000 + no_response. Server set `tlsSettings.alpn=["http/1.1"]` for all transports. XHTTP requires h2 (HTTP/2 multiplexed streams) — http/1.1 ALPN means TLS handshake settles on a protocol XHTTP can't use. gRPC happened to pass with http/1.1 too but it's not correct per xray-core spec. Fix:
- tcp → `alpn=["http/1.1"]` (unchanged)
- xhttp → `alpn=["h2", "http/1.1"]`
- grpc → `alpn=["h2"]`

Same alpn echoed in `&alpn=h2` query param in link so client requests matching protocol during TLS handshake.

## Tested (concrete commands)

Per cell, on VPS:
```
# clean state
systemctl stop x-ui nginx gv-test
apt-get purge -y nginx nginx-common nginx-core
rm -rf /usr/local/x-ui /etc/x-ui /etc/nginx /opt/govless
rm -f /root/.govless_credentials /etc/systemd/system/x-ui.service
# preserve /etc/letsencrypt + /root/.acme.sh + /root/cert/ip

# install
printf "<answers>" > /tmp/ans.txt
nohup setsid bash -c "cd /root/goVLESS && bash govless.sh < /tmp/ans.txt > /var/log/c<N>.log 2>&1"

# verify
sqlite3 /etc/x-ui/x-ui.db "SELECT * FROM inbounds"
openssl x509 -in <webCertFile> -noout -issuer
nc -zv 45.80.231.78 2096        # B2 check (NewGen only)
journalctl -u x-ui --since "5m" | grep -i webport  # B3 check

# e2e each key
for link in $(jq -r '.[]' /tmp/govless_links.json); do
    python3 /root/build_client_json.py "$link" 18080 > /opt/xray-client/client.json
    systemctl restart gv-test
    curl -m 8 --socks5-hostname 127.0.0.1:18080 -s https://api.ipify.org/
    # expect: 45.80.231.78
done
```

Automated TSV log at `/var/log/govless_matrix.tsv` on VPS.

## Per-transport e2e parameter verification

Each transport tested both server-side (sqlite stream_settings) and
client-side (xray client.json built from link), to ensure the
configurations match:

- **TCP**: `tcpSettings.header={type:none}`, link `type=tcp&alpn=http/1.1&flow=xtls-rprx-vision`.
- **XHTTP** (after B9): server `xhttpSettings={path:/, host:'', mode:auto}` + `tlsSettings.alpn=[h2, http/1.1]`; link `type=xhttp&alpn=h2&path=/&mode=auto`.
- **gRPC** (after B9): server `grpcSettings={serviceName:govless, multiMode:true}` + `tlsSettings.alpn=[h2]`; link `type=grpc&alpn=h2&serviceName=govless&mode=multi`.

Both keys (different UUIDs) per install verified independently — no
key-isolation bugs.

## Not yet verified / open

- **6-day cert auto-renew via acme.sh cron**: cron entry `14 2 * * *` is
  present after first issuance; the renew itself happens later (need to
  observe `--renew --force` working with reloadcmd restarting x-ui to
  pick up new cert).
- **XHTTP `mode=stream-up` and `mode=packet-up`**: only `mode=auto`
  tested. Defaults to packet-up in this env (per xray docs). Specific
  modes not exercised.
- **gRPC with `mode=multi` vs single**: only `multi` tested.
- **Migration from existing XUIFAST install**: Commit B in proposal
  001 — not done yet.
- **Real-world DPI resistance for Reality**: only basic e2e through
  api.ipify.org tested. Not tested under actual GFW/Russian DPI.
- **Pro mode template-selection path**: answers used `0` to skip
  template; the `interactive_template_selection` code path with a
  real template not exercised.
- **Other transports the doc never listed**: WS, Reality+TLS, Trojan,
  Shadowsocks, VMess. `select_transport` in code only offers
  TCP/XHTTP/gRPC; these others are out-of-scope per current UX.
- **xui_user-facing UI in Russian** (`LANG_CODE=ru`): only `en`
  exercised. Should be tested for parity of strings.

## Commits in this test cycle

```
7da9a09  Fix B9 (P1): Pro+XHTTP/gRPC needs h2 ALPN
6dde757  Fix Pro-mode transport selection (B8 P1)
9fe0575  Fix B7: /var/www/certbot perms (was 700, blocks LE HTTP-01)
e785ea4  006 Architect test report Scenario 1
2bc8630  Fix LE IP cert: --certificate-profile shortlived + --days 6
ba38e07  Fix LE acme.sh: valid TLD email
5e3d0a2  Fix: acme.sh install flag
840f414  Hotfix: heredoc scope (web_path_norm)
9db4565  Fix syntax: empty then-block
befc3ce  005 Pass-1 test fixes (B1/B2/B3 + LE-for-IP)
```

## Reviewer Ask (Codex)

Please independently reverify the matrix:

1. Pull fresh main (HEAD after this report), prepare a clean VPS, run
   all 12 cells. Reuse `/etc/letsencrypt/live/potap1978.com/` to avoid
   burning LE quota.
2. Pay attention to the **not yet verified** list above. Especially:
   - Trigger acme.sh `--renew --force` for the IP cert and verify x-ui
     restarts to pick up new cert (the `--reloadcmd` hook).
   - Try `XUI_TRANSPORT=xhttp` with `mode=stream-up` instead of `auto`
     (manual sqlite edit + restart) and verify e2e still works.
   - Try language=ru install end-to-end.
3. Find anything we both missed. Particularly:
   - Resource leaks across reinstalls (cron entries, systemd units,
     orphan processes).
   - Idempotency: re-running `govless.sh` on a healthy install — does
     the menu correctly skip install and show options?
   - What happens if user picks invalid input at any prompt?

Drop findings as `011-reviewer-matrix-audit.md` per template.

Final string format:
`Codex matrix audit: 12/12=PASS or N issues found, see commit <sha>.`

Hand off complete.
