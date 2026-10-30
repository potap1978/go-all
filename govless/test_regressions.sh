#!/bin/bash
# goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
# Licensed under the goVLESS Source-Available License (see the LICENSE file).
# Redistribution, mirroring, or republishing in any form — whole or partial,
# modified or not — is prohibited without prior written permission.

# Lightweight source-level regressions for bugs caught during AI-duo review.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1" >&2; exit 1; }

for script in bootstrap.sh install.sh govless.sh lib/common.sh lib/i18n.sh \
    lib/xui.sh lib/xui_api.sh lib/website.sh lib/mode_switch.sh \
    lib/reality_domains.sh lib/templates_catalog.sh; do
    bash -n "$script" || fail "syntax: $script"
done
pass "bash syntax for critical shell files"

# Bug #1 from 045 audit: backup_no_files + restore_pick keys must exist
for key in backup_no_files restore_pick; do
    grep -q "I18N\[$key\]" lib/lang/ru.sh && \
    grep -q "I18N\[$key\]" lib/lang/en.sh || \
        fail "$key i18n key missing — restore menu would render blank"
done
pass "backup_no_files + restore_pick i18n keys present"

# bridge 050: Lite Mini App must be fully functional via UNIX-socket RPC proxy
# Previous gate (Codex 049 P1) was reverted: Lite now has its own
# /opt/govless/webapp/server.py that serves static AND proxies /api/rpc
# into govlessctl.sock. So MenuButtonWebApp is enabled for any tunnel URL,
# not just Pro. Regression below pins the proxy in place so a careless
# revert can't silently break Lite Mini App again.

# 1. server.py exists with the right defaults (stdlib only, no aiohttp dep).
grep -q "DEFAULT_SOCK = \"/run/govlessctl.sock\"" phase-a/webapp/server.py || \
    fail "phase-a/webapp/server.py missing — Lite Mini App needs the RPC proxy"
grep -q "class UnixHTTPConnection" phase-a/webapp/server.py || \
    fail "phase-a/webapp/server.py must implement UNIX-socket HTTP client"
grep -q "self.path == \"/api/rpc\"" phase-a/webapp/server.py || \
    fail "phase-a/webapp/server.py must route POST /api/rpc into the daemon"
grep -q "self.path == \"/api/healthz\"" phase-a/webapp/server.py || \
    fail "phase-a/webapp/server.py must route GET /api/healthz into the daemon"
pass "phase-a/webapp/server.py implements static + /api/rpc + /api/healthz"

grep -F 'GOVLESS_BACKEND_HEALTH_URL:-http://127.0.0.1:8443/api/healthz' phase-a/systemd/bin/govless-tunnel-health-check >/dev/null || \
    fail "Lite tunnel health-check must probe RPC-aware backend /api/healthz, not static /healthz"
grep -F '${TUNNEL_URL%/}/api/healthz' phase-a/systemd/bin/govless-tunnel-health-check >/dev/null || \
    fail "Lite tunnel health-check must probe public /api/healthz through the tunnel"
pass "Lite tunnel health-check verifies daemon-backed /api/healthz"

# 2. systemd unit launches server.py, NOT plain `python3 -m http.server`.
grep -q "python3 /opt/govless/webapp/server.py" phase-a/systemd/webapp-frontend.service || \
    fail "webapp-frontend.service must launch server.py (Lite RPC proxy)"
! grep -q "python3 -m http.server" phase-a/systemd/webapp-frontend.service || \
    fail "webapp-frontend.service still references plain http.server (RPC will 404 in Lite)"
pass "webapp-frontend.service launches the RPC-aware server.py"

# 3. tunnel.url_get still returns source — informational, used by logging
# and the watcher's "Tunnel URL updated (quick)" admin notification.
grep -qE '"source":\s*"(pro|named|quick|none)"' phase-a/govlessctl/methods.py || \
    fail "tunnel_url_get must return source so logs can distinguish quick/named/pro"
pass "tunnel_url_get returns source discriminator"

# 4. webapp.py must NOT gate the menu button on source==pro (post-050 revert).
! grep -qE 'url_info\[1\][[:space:]]*==[[:space:]]*"pro"' phase-a/bot/handlers/webapp.py || \
    fail "webapp.py still gates MenuButtonWebApp on source==pro — revert per bridge 050"
pass "WebApp menu button no longer gated on Pro (Lite serves /api/rpc via server.py)"

# 067 P1: govlessctl must not cache an empty /etc/govless/bot.env forever.
# Fresh install starts govlessctl before BOT_TOKEN is filled; WebApp auth must
# see the token once the operator edits bot.env, without requiring a hidden
# govlessctl restart.
PYTHONPATH=phase-a python3 - <<'PY' || fail "govlessctl bot.env cache must reload after token edits"
import os
import sys
import tempfile
import time
import types

from govlessctl.auth import BotEnvCache

# test_regressions.sh must run on a clean dev box without Phase-A pip deps.
# Methods only needs its bot_env loader here, so stub aiohttp before import.
sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))
from govlessctl.methods import Methods

with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, "bot.env")
    with open(path, "w", encoding="utf-8") as f:
        f.write("BOT_TOKEN=\nADMIN_CLAIM_TOKEN=first\nADMIN_IDS=111\n")

    cache = BotEnvCache(path)
    methods = Methods(state=object(), xui=object(), bot_env_loader=cache.get)

    assert cache.get().get("BOT_TOKEN", "") == ""
    assert methods.bot_env.get("ADMIN_CLAIM_TOKEN") == "first"

    # Some filesystems have coarse mtime; sleep so the write is observable.
    time.sleep(0.02)
    with open(path, "w", encoding="utf-8") as f:
        f.write("BOT_TOKEN=second-token\nADMIN_CLAIM_TOKEN=second\nADMIN_IDS=222\n")

    assert cache.get().get("BOT_TOKEN") == "second-token"
    assert methods.bot_env.get("ADMIN_CLAIM_TOKEN") == "second"
    assert methods.bot_env.get("ADMIN_IDS") == "222"
PY
grep -A18 'NEXT STEP: edit /etc/govless/bot.env' phase-a/systemd/install/install_phase_a.sh | \
    grep -q 'systemctl restart govlessctl' || \
    fail "Phase-A install reminder must restart govlessctl after BOT_TOKEN is set"
pass "govlessctl bot.env cache reloads after token edits"

# bridge 050 follow-up: cloudflared-quick must run ExecStartPre as root.
# Without `+` prefix the chown govless:govless inside ExecStartPre fails on
# fresh Lite installs, preventing the tunnel from ever starting (caught on
# VPS-A 138.124.90.14 during 050 live smoke).
grep -qE "^ExecStartPre=\+/bin/sh" phase-a/systemd/cloudflared-quick.service || \
    fail "cloudflared-quick ExecStartPre must run as root (+ prefix) so chown succeeds"
pass "cloudflared-quick ExecStartPre runs as root"

# bridge 050 follow-up #3: cloudflared.log perms — cloudflared prints the
# quick-tunnel URL into /var/log/cloudflared.log on startup. World-readable
# perms would leak the URL to any user on the box (including a future-
# compromised x-ui). Enforce 0640 root:govless in ExecStartPre.
grep -qF 'chmod 0640 /var/log/cloudflared.log' phase-a/systemd/cloudflared-quick.service || \
    fail "cloudflared.log must be chmod 0640 in ExecStartPre (URL leak risk)"
grep -qF 'chown root:govless /var/log/cloudflared.log' phase-a/systemd/cloudflared-quick.service || \
    fail "cloudflared.log must be chowned root:govless in ExecStartPre"
grep -qF ': > /var/log/cloudflared.log' phase-a/systemd/cloudflared-quick.service && \
    grep -qF 'rm -f /run/govless/tunnel.url' phase-a/systemd/cloudflared-quick.service || \
    fail "cloudflared-quick restart must clear stale quick-tunnel log and URL file"
pass "cloudflared.log is 0640 root:govless (URL not world-readable)"


# bridge 050 follow-up #2: tunnel URL is a public address for the admin
# Mini App. If it leaks, attacker has a free pointer to probe the
# daemon. Two hardenings caught during 050 live smoke:
#   1. /run/govless/tunnel.url must be 0640 root:govless (not 0644).
#   2. journal log line must NOT contain the URL — only its sha256.
grep -qF 'chmod 0640 "$TMP"' phase-a/systemd/bin/govless-tunnel-url-extract || \
    fail "tunnel.url must be written as 0640, not world-readable"
grep -qF 'chown root:govless "$TMP"' phase-a/systemd/bin/govless-tunnel-url-extract || \
    fail "tunnel.url must be chowned root:govless so x-ui/other users can't read it"
! grep -qF 'log "tunnel URL changed: ' phase-a/systemd/bin/govless-tunnel-url-extract || \
    fail "tunnel URL must not be logged verbatim — use sha256 fingerprint"
grep -qF 'sha256sum | cut -c1-12' phase-a/systemd/bin/govless-tunnel-url-extract || \
    fail "tunnel URL extractor must log only sha256 fingerprint"
grep -qF 'WAIT_SEC="${GOVLESS_TUNNEL_EXTRACT_WAIT_SEC:-20}"' phase-a/systemd/bin/govless-tunnel-url-extract && \
    grep -qF 'sleep 1' phase-a/systemd/bin/govless-tunnel-url-extract || \
    fail "tunnel URL extractor must wait briefly for cloudflared to print a fresh URL"
pass "tunnel.url file is 0640 root:govless and not logged verbatim"





# Disclaimer: i18n keys exist in BOTH ru and en (RU/EN parity).
# New 23-key sectioned version: intro + 5 lawful uses + 5 legality bullets +
# 4 prohibited bullets + anonymity note + accept_prompt/declined/marker_hint.
for key in disclaimer_title disclaimer_intro \
           disclaimer_uses_header disclaimer_uses_1 disclaimer_uses_2 disclaimer_uses_3 disclaimer_uses_4 disclaimer_uses_5 \
           disclaimer_legal_header disclaimer_legal_1 disclaimer_legal_2 disclaimer_legal_3 disclaimer_legal_4 disclaimer_legal_5 \
           disclaimer_prohibited_header disclaimer_prohibited_1 disclaimer_prohibited_2 disclaimer_prohibited_3 disclaimer_prohibited_4 \
           disclaimer_anonymity_note \
           disclaimer_accept_prompt disclaimer_declined disclaimer_accepted_marker_hint; do
    grep -q "I18N\[$key\]" lib/lang/ru.sh && grep -q "I18N\[$key\]" lib/lang/en.sh || \
        fail "disclaimer i18n key missing in ru or en: $key"
done
pass "disclaimer i18n keys present in RU+EN (23 keys)"

# Disclaimer: gate function exists + select_and_install uses it
grep -q "show_disclaimer --gate" govless.sh || \
    fail "select_and_install must call show_disclaimer --gate before mode pick"
grep -q "^show_disclaimer()" lib/common.sh || \
    fail "show_disclaimer() function missing from lib/common.sh"
pass "disclaimer gate wired into select_and_install"

# Disclaimer: About menu shows it info-only
grep -A20 "^submenu_about\(\)" govless.sh | grep -q "show_disclaimer$" || \
    fail "submenu_about must show disclaimer (info-only, no --gate)"
pass "About menu shows disclaimer info-only"

# Disclaimer: accept marker uses GOVLESS_DIR
grep -q '"\${GOVLESS_DIR}/\.disclaimer-accepted"' lib/common.sh || \
    fail "disclaimer accept marker must live in \$GOVLESS_DIR"
pass "disclaimer accept marker persisted in GOVLESS_DIR"


awk '
    /^configure_panel_tls\(\)/ { in_fn=1; seen=0; returned=0 }
    in_fn && /configure_sub_server 2>\/dev\/null/ {
        if (returned) exit 3
        seen=1
    }
    in_fn && /^[[:space:]]*return 0/ {
        returned=1
        if (!seen) exit 2
    }
    in_fn && /^}/ { exit seen ? 0 : 1 }
' lib/xui.sh || fail "configure_panel_tls must call configure_sub_server before success return"
pass "configure_panel_tls reaches configure_sub_server before success return"

awk '
    /^configure_panel_tls\(\)/ { in_fn=1 }
    in_fn && /^}/ { in_fn=0 }
    in_fn && /subListen.*127\.0\.0\.1/ { exit 1 }
' lib/xui.sh || fail "configure_panel_tls must not force subscription server to localhost"
pass "configure_panel_tls does not override subscription bind to localhost"

grep -F "local sub_cert=\"\"" lib/xui.sh >/dev/null && \
    grep -F "sub_cert=\"/etc/letsencrypt/live/\${domain}/fullchain.pem\"" lib/xui.sh >/dev/null && \
    grep -F "('subCertFile', '\${sub_cert}')," lib/xui.sh >/dev/null && \
    grep -F "('subKeyFile',  '\${sub_key}')," lib/xui.sh >/dev/null && \
    grep -F "DELETE FROM settings WHERE key IN ('subEnable','subPort','subPath','subListen','subCertFile','subKeyFile','subUpdates');" lib/xui.sh >/dev/null || \
    fail "configure_sub_server must enable HTTPS subscription certs in Pro mode"
grep -F 'configure_sub_server "pro" "$domain"' govless.sh >/dev/null || \
    fail "Pro install must re-run configure_sub_server with the domain after config is known"
grep -F 'configure_sub_server "$mode" "$domain"' lib/mode_switch.sh >/dev/null && \
    grep -F 'mode_switch_ensure_sub_server "$target_mode" "$target_domain"' lib/mode_switch.sh >/dev/null || \
    fail "mode switch must configure subscription TLS using the target mode/domain"
pass "Pro subscription server is configured for HTTPS"

if grep -F "d.get('\$name" govless.sh >/dev/null; then
    fail "Users QR subscription lookup interpolates client name into Python"
fi
if grep -F "n='\$first_name'" govless.sh >/dev/null; then
    fail "Post-install subscription lookup interpolates client name into Python"
fi
pass "subscription lookups pass client names as Python argv"

if grep -F '"subId": ""' lib/xui_api.sh >/dev/null; then
    fail "generated clients must not have empty subId"
fi
if ! awk '
    /for name in names:/ { in_loop=1 }
    in_loop && /client_id = str\(uuid\.uuid4\(\)\)/ { has_client_id=1 }
    in_loop && /"id": client_id/ { has_id=1 }
    in_loop && /"subId": client_id/ { has_sub_id=1 }
    in_loop && /clients.append\(client\)/ {
        exit (has_client_id && has_id && has_sub_id) ? 0 : 1
    }
' lib/xui_api.sh; then
    fail "generate_clients must set subId to the served client id"
fi
pass "generated clients have stable subscription ids"

if grep -F "Enter — пропустить" govless.sh >/dev/null; then
    fail "connection-test wait loop still hardcodes Russian skip hint"
fi
pass "connection-test skip hint is localized"

tmp_json="$(mktemp /tmp/govless_subs_test.XXXXXX)"
tmp_db="$(mktemp /tmp/govless_subid_test.XXXXXX)"
trap 'rm -f "$tmp_json" "$tmp_db"' EXIT
python3 - "$tmp_json" <<'PY'
import json, sys
with open(sys.argv[1], "w") as f:
    json.dump({"alice'o": "https://example.test/sub"}, f)
PY

got="$(python3 -c 'import json, sys; d=json.load(open(sys.argv[1])); print(d.get(sys.argv[2],""))' "$tmp_json" "alice'o")"
[ "$got" = "https://example.test/sub" ] || fail "Python argv subscription lookup handles apostrophes"
pass "Python argv subscription lookup handles apostrophes"

python3 - "$tmp_db" <<'PY'
import json, sqlite3, sys

db = sys.argv[1]
client_id = "11111111-1111-1111-1111-111111111111"
settings = {
    "clients": [{
        "id": client_id,
        "email": "alice",
        "subId": "",
        "flow": "xtls-rprx-vision",
    }]
}
conn = sqlite3.connect(db)
conn.execute("CREATE TABLE inbounds(id INTEGER PRIMARY KEY, settings TEXT)")
conn.execute(
    "CREATE TABLE clients(id INTEGER PRIMARY KEY, email TEXT, sub_id TEXT, uuid TEXT, flow TEXT, updated_at INTEGER)"
)
conn.execute("INSERT INTO inbounds(id, settings) VALUES(1, ?)", (json.dumps(settings),))
conn.execute("INSERT INTO clients(id, email, sub_id, uuid, flow) VALUES(1, 'alice', '', '', '')")
conn.commit()
PY
(
    source lib/common.sh
    source lib/xui.sh
    XUI_DB="$tmp_db"
    ensure_client_subids >/dev/null
) 2>/dev/null || fail "ensure_client_subids failed on v3 fixture"
python3 - "$tmp_db" <<'PY' || fail "ensure_client_subids must repair settings and v3 clients table"
import json, sqlite3, sys

db = sys.argv[1]
client_id = "11111111-1111-1111-1111-111111111111"
conn = sqlite3.connect(db)
settings = json.loads(conn.execute("SELECT settings FROM inbounds WHERE id=1").fetchone()[0])
client = settings["clients"][0]
row = conn.execute("SELECT sub_id, uuid, flow FROM clients WHERE email='alice'").fetchone()
assert client["subId"] == client_id
assert row == (client_id, client_id, "xtls-rprx-vision")
PY
pass "ensure_client_subids repairs v3 subscription lookup state"

subid_custom_db="$(mktemp /tmp/govless_subid_custom.XXXXXX)"
python3 - "$subid_custom_db" <<'PY'
import json, sqlite3, sys

db = sys.argv[1]
new_id = "22222222-2222-4222-8222-222222222222"
old_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
custom_id = "33333333-3333-4333-8333-333333333333"
custom_sub = "rotated-token-not-a-uuid"
settings = {
    "clients": [
        {"id": new_id, "email": "stale", "subId": old_id, "flow": "xtls-rprx-vision"},
        {"id": custom_id, "email": "custom", "subId": custom_sub, "flow": "xtls-rprx-vision"},
    ]
}
conn = sqlite3.connect(db)
conn.execute("CREATE TABLE inbounds(id INTEGER PRIMARY KEY, settings TEXT)")
conn.execute(
    "CREATE TABLE clients(id INTEGER PRIMARY KEY, email TEXT, sub_id TEXT, uuid TEXT, flow TEXT, updated_at INTEGER)"
)
conn.execute("INSERT INTO inbounds(id, settings) VALUES(1, ?)", (json.dumps(settings),))
conn.execute("INSERT INTO clients(id, email, sub_id, uuid, flow) VALUES(1, 'stale', ?, ?, '')", (old_id, new_id))
conn.execute("INSERT INTO clients(id, email, sub_id, uuid, flow) VALUES(2, 'custom', ?, ?, '')", (custom_sub, custom_id))
conn.commit()
PY
(
    source lib/common.sh
    source lib/xui.sh
    XUI_DB="$subid_custom_db"
    ensure_client_subids >/dev/null
) 2>/dev/null || fail "ensure_client_subids failed on custom subId fixture"
python3 - "$subid_custom_db" <<'PY' || fail "ensure_client_subids must preserve rotated non-UUID subIds"
import json, sqlite3, sys

db = sys.argv[1]
new_id = "22222222-2222-4222-8222-222222222222"
custom_id = "33333333-3333-4333-8333-333333333333"
custom_sub = "rotated-token-not-a-uuid"
conn = sqlite3.connect(db)
settings = json.loads(conn.execute("SELECT settings FROM inbounds WHERE id=1").fetchone()[0])
by_email = {c["email"]: c for c in settings["clients"]}
rows = dict(conn.execute("SELECT email, sub_id FROM clients").fetchall())
assert by_email["stale"]["subId"] == new_id
assert rows["stale"] == new_id
assert by_email["custom"]["subId"] == custom_sub
assert rows["custom"] == custom_sub
assert by_email["custom"]["subId"] != custom_id
PY
rm -f "$subid_custom_db"
pass "ensure_client_subids preserves rotated subscription tokens"

row="$(python3 - <<'PY'
import base64
enc = lambda value: base64.b64encode(value.encode()).decode()
print(f"1\t{enc('pipe|name')}\t{enc('vless://uuid@example:443?x=1|2')}")
PY
)"
IFS=$'\t' read -r num name_b64 link_b64 <<< "$row"
name="$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "$name_b64")"
link="$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "$link_b64")"
expected=$'1\tpipe|name\tvless://uuid@example:443?x=1|2'
parsed="${num}"$'\t'"${name}"$'\t'"${link}"
[ "$parsed" = "$expected" ] || fail "base64 user rows preserve pipe characters"
pass "base64 user rows preserve pipe characters"

if grep -F "SELECT id || '||'" lib/mode_switch.sh >/dev/null; then
    fail "mode_switch_extract_clients still uses delimiter-concatenated sqlite rows"
fi
if grep -F "split('||'" lib/mode_switch.sh >/dev/null; then
    fail "mode_switch_extract_clients still splits sqlite rows on ||"
fi
pass "mode switch extracts sqlite columns without delimiter splitting"

grep -F "mode_switch_snapshot_inbound" lib/mode_switch.sh >/dev/null && \
    grep -F "mode_switch_restore_inbound_snapshot" lib/mode_switch.sh >/dev/null || \
    fail "mode switch must have an inbound rollback path"
if ! awk '
    /^switch_mode\(\)/ { in_fn=1; saw_snapshot=0; saw_delete=0; saw_restore=0 }
    in_fn && /mode_switch_snapshot_inbound/ { saw_snapshot=1 }
    in_fn && /api_delete_inbound/ {
        if (!saw_snapshot) exit 1
        saw_delete=1
    }
    in_fn && /mode_switch_restore_inbound_snapshot/ {
        if (!saw_delete) exit 1
        saw_restore=1
    }
    in_fn && /^}/ { exit (saw_snapshot && saw_delete && saw_restore) ? 0 : 1 }
' lib/mode_switch.sh; then
    fail "mode switch must snapshot before delete and restore on create failure"
fi
pass "mode switch rolls back old inbound when new inbound creation fails"

grep -F 'local target_transport="${GOVLESS_SWITCH_TRANSPORT:-$current_transport}"' lib/mode_switch.sh >/dev/null && \
    grep -F 'config_set "transport" "$XUI_TRANSPORT"' lib/mode_switch.sh >/dev/null || \
    fail "mode switch must preserve and persist current transport unless explicitly overridden"
! grep -F 'xray-linux-amd6' govless.sh >/dev/null && \
    grep -F 'xray|xray-linux-*)' govless.sh >/dev/null || \
    fail "Pro install port-443 guard must not kill active xray-linux-amd64/arm64"
grep -F 'e.code in (401, 403)' phase-a/govlessctl/xui_client.py >/dev/null && \
    grep -F '"3X-UI auth:" in e.message' phase-a/govlessctl/xui_client.py >/dev/null || \
    fail "XuiClient must retry once after expired-cookie 401/403 responses"

if grep -R "https://{cfg\\['domain'\\]}/sub" phase-a/govlessctl >/dev/null; then
    fail "Phase-A still hardcodes nginx-style Pro subscription URLs"
fi
if [ -f phase-a/CONTRACT.md ] && grep -R "Pro only" phase-a/CONTRACT.md >/dev/null; then
    fail "Phase-A contract still documents subscriptions as Pro-only"
fi
pass "Phase-A subscription contract no longer uses stale Pro-only /sub URLs"

PYTHONPATH=phase-a python3 - <<'PY' || fail "Phase-A subscriptions must be Pro-only; Lite should prefer direct VLESS keys"
import sys
import types

sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))
from govlessctl.methods import _subscription_url_for_client

client = {"id": "uuid-1", "subId": "sub-1"}
settings = {"subPort": "2096", "subPath": "sub"}
lite = {"mode": "lite", "server_ip": "203.0.113.10"}
pro = {"mode": "pro", "domain": "vpn.example.test"}

assert _subscription_url_for_client(client, cfg=lite, settings=settings) is None
assert _subscription_url_for_client(client, cfg=pro, settings=settings) == "https://vpn.example.test:2096/sub/sub-1"
PY
grep -F 'if mode == "pro" and domain and sub_port and sub_path:' lib/xui_api.sh >/dev/null || \
    fail "SSH link regeneration must write subscription URLs only in Pro/domain mode"
grep -F "Lite does not" lib/xui_api.sh >/dev/null || \
    fail "SSH link-choice helper must document Lite direct-key behavior"
pass "Phase-A Lite disables subscriptions; Pro keeps subscription URLs"

if ! awk '
    /show_user_link_choice\(\)/ { in_fn=1; has_loop=0; has_default=0; has_invalid=0; has_return=0 }
    in_fn && /while true/ { has_loop=1 }
    in_fn && /^[[:space:]]*\*\)/ { has_default=1 }
    in_fn && /log_warning.*invalid_choice/ { has_invalid=1 }
    in_fn && /return 0/ { has_return=1 }
    in_fn && /^}/ { exit (has_loop && has_default && has_invalid && has_return) ? 0 : 1 }
' lib/xui_api.sh; then
    fail "QR link choice should retry on invalid input instead of defaulting once"
fi
pass "QR link choice retries invalid input"

if awk '
    /sub_id = cli\.get\("subId"\) or ""/ { after_subid=1; next }
    after_subid && /continue/ { exit 1 }
    after_subid && /# Backward-compat top-level shape/ { exit 0 }
' lib/xui_api.sh; then
    pass "missing subId does not suppress direct VLESS links"
else
    fail "missing subId must only omit subscription URL, not direct VLESS link"
fi

grep -F "clear 2>/dev/null || true" govless.sh >/dev/null || \
    fail "dashboard clear must not exit the menu when TERM is missing"
pass "dashboard clear is non-fatal"

state_tmp_dir="$(mktemp -d /tmp/govless_state_test.XXXXXX)"
state_stdout="$(
    GOVLESS_DIR="$state_tmp_dir" bash -lc '
        source lib/common.sh
        source lib/xui_api.sh
        init_state_db
    '
)"
rm -rf "$state_tmp_dir"
[ -z "$state_stdout" ] || fail "init_state_db should not print PRAGMA output"
pass "init_state_db is quiet"

if ! grep -F "confirm_token=ctx.get(\"confirm_token\")" phase-a/bot/handlers/confirm.py >/dev/null; then
    fail "bot confirm flow does not forward govlessctl confirm_token"
fi
grep -F "context[\"confirm_token\"] = exc.data.get(\"confirm_token\")" phase-a/bot/handlers/clients.py >/dev/null || \
    fail "bot client destructive callbacks must fetch govlessctl-issued confirm_token"
grep -F "not self._check_confirm(params, \"RESET-TRAFFIC\", uid)" phase-a/govlessctl/methods.py >/dev/null || \
    fail "client.reset_traffic must require a server-issued confirm_token"
grep -F "not self._check_confirm(params, \"DELETE-CLIENT\", uid)" phase-a/govlessctl/methods.py >/dev/null || \
    fail "client.delete must require a server-issued confirm_token"
grep -F "not self._check_confirm(params, \"ROTATE-SUB\", uid)" phase-a/govlessctl/methods.py >/dev/null || \
    fail "subscription.rotate must require a server-issued confirm_token"
if ! grep -F "confirm_token:e.detail.confirm_token" phase-a/webapp/dist/app.jsx >/dev/null; then
    fail "webapp destructive client retry does not forward govlessctl confirm_token"
fi
grep -F "const runIssuedConfirm = async" phase-a/webapp/dist/app.jsx >/dev/null || \
    fail "webapp needs a shared helper for govlessctl-issued confirm_token retries"
grep -F 'script-src '"'"'self'"'"' https://telegram.org https://cdnjs.cloudflare.com' phase-a/systemd/nginx/govless-webapp.conf >/dev/null || \
    fail "Phase-A Pro WebApp CSP must allow production cdnjs React/Babel"
# Production Mini App is prebuilt (no runtime Babel) -> production CSP must NOT
# carry unsafe-eval/unsafe-inline. (The /design/ preview canvas keeps a wider CSP.)
grep -F "https://telegram.org https://cdnjs.cloudflare.com;" phase-a/systemd/nginx/govless-webapp.conf >/dev/null && \
    grep -F "https://telegram.org https://cdnjs.cloudflare.com;" phase-a/webapp/server.py >/dev/null || \
    fail "production WebApp CSP (nginx prod + server.py WEBAPP_CSP) must drop unsafe-eval/unsafe-inline"
grep -F 'type="text/babel"' phase-a/webapp/dist/index.html >/dev/null && \
    fail "production index.html must not use runtime Babel (text/babel)" || true
grep -F "babel-standalone" phase-a/webapp/dist/index.html >/dev/null && \
    fail "production index.html must not load babel-standalone" || true
grep -F 'src="app.js' phase-a/webapp/dist/index.html >/dev/null || \
    fail "production index.html must load the prebuilt app.js bundle"
grep -F 'rpc("inbound.toggle", {inbound_id:ib.id, enable:false, confirm:e.detail.confirm, confirm_token:e.detail.confirm_token})' phase-a/webapp/dist/app.jsx >/dev/null || \
    fail "webapp inbound disable must retry with govlessctl confirm_token"
# Panel public/ssh-only switching is a BOT feature (not the Mini App); the
# ssh-only lockout must forward the govlessctl-issued confirm_token.
grep -F 'rpc_method="panel.access_set"' phase-a/bot/handlers/confirm.py >/dev/null && \
    grep -F 'confirm_token=ctx.get("confirm_token")' phase-a/bot/handlers/confirm.py >/dev/null && \
    grep -F 'context={"confirm_token": exc.data.get("confirm_token")}' phase-a/bot/handlers/menu.py >/dev/null || \
    fail "bot SSH-only panel switch must forward govlessctl confirm_token"
grep -F 'await rpc("client.update", {uuid: client.uuid, fields:' phase-a/webapp/dist/app.jsx >/dev/null && \
    grep -F 'confirm_token: e.detail.confirm_token});' phase-a/webapp/dist/app.jsx >/dev/null || \
    fail "webapp expiry extension must retry with govlessctl confirm_token"
if ! grep -F "error[\"data\"] = data" phase-a/govlessctl/daemon.py >/dev/null; then
    fail "govlessctl daemon does not include structured confirm data in JSON-RPC errors"
fi
pass "Phase-A typed-confirm token is carried through RPC clients"

# Codex 029 P2 — template picker retries invalid input rather than silently
# falling through to a stub site. Verify the function has a retry loop and
# uses `continue` after warning on invalid input.
if ! awk '
    /^select_category\(\)/ { in_fn=1; has_loop=0; has_warn=0; has_continue=0 }
    in_fn && /while true/ { has_loop=1 }
    in_fn && /log_warning.*invalid_choice/ { has_warn=1 }
    in_fn && /continue/ { has_continue=1 }
    in_fn && /^}/ { exit (has_loop && has_warn && has_continue) ? 0 : 1 }
' lib/templates_catalog.sh; then
    fail "select_category must retry-in-place on invalid input (loop + log_warning + continue)"
fi
if ! grep -F '__skip__' lib/templates_catalog.sh >/dev/null; then
    fail "select_category must offer an explicit __skip__ option"
fi
if ! grep -F '"__skip__"' govless.sh >/dev/null; then
    fail "install_pro must distinguish explicit skip (__skip__) from selection failure"
fi
pass "template picker retries invalid input and exposes explicit skip"

# Codex 029 P3 — every template category in templates_catalog.json must have
# matching tplcat_<id> i18n keys in BOTH ru.sh and en.sh. Otherwise EN users
# see Russian category names.
missing_keys=""
if command -v jq &>/dev/null && [ -f templates_catalog.json ]; then
    while IFS= read -r cat_id; do
        [ -z "$cat_id" ] && continue
        key="tplcat_${cat_id}"
        grep -qF "I18N[${key}]" lib/lang/ru.sh || missing_keys+="ru:${key} "
        grep -qF "I18N[${key}]" lib/lang/en.sh || missing_keys+="en:${key} "
    done < <(jq -r '.categories[].id' templates_catalog.json)
fi
[ -z "$missing_keys" ] || fail "missing tplcat_* i18n keys: $missing_keys"
pass "template categories have RU+EN i18n translations"

legacy_phasea_db="$(mktemp /tmp/govless_phasea_legacy_state.XXXXXX)"
python3 - "$legacy_phasea_db" <<'PY' || fail "Phase-A state DB must migrate legacy audit schema"
import json
import sqlite3
import sys

sys.path.insert(0, "phase-a")
from govlessctl.state_db import StateDB

db = sys.argv[1]
conn = sqlite3.connect(db)
conn.execute(
    """CREATE TABLE govless_audit (
        ts INTEGER NOT NULL DEFAULT (strftime('%s','now')),
        admin_tg TEXT NOT NULL,
        action TEXT NOT NULL,
        target TEXT,
        before TEXT,
        after TEXT
    )"""
)
conn.execute("CREATE INDEX idx_audit_ts ON govless_audit(ts DESC)")
conn.close()

state = StateDB(db)
cols = {r["name"] for r in state.conn.execute("PRAGMA table_info(govless_audit)")}
assert "before_json" in cols
assert "after_json" in cols
state.audit(None, "client.enable", "u1", {"enable": False}, {"enable": True})
row = state.conn.execute(
    "SELECT admin_tg, before_json, after_json FROM govless_audit WHERE action='client.enable'"
).fetchone()
assert row["admin_tg"] == "local"
assert json.loads(row["before_json"]) == {"enable": False}
assert json.loads(row["after_json"]) == {"enable": True}
state.close()
PY
rm -f "$legacy_phasea_db"
pass "Phase-A state DB migrates legacy audit schema"

grep -F "str(uid) in {str(admin_id) for admin_id in admins}" phase-a/bot/handlers/start.py >/dev/null || \
    fail "bot /start must honor persisted state.db admins"
grep -F "async def _load_persisted_admins" phase-a/bot/bot.py >/dev/null || \
    fail "bot startup must preload persisted state.db admins"
grep -F "CONFIG.reload_admins(loaded)" phase-a/bot/bot.py >/dev/null || \
    fail "bot startup must merge persisted admins into CONFIG"
awk '
    /if is_admin\(uid\):/ { in_admin=1; seen_apply=0 }
    in_admin && /await h_webapp\.apply_menu_buttons_once\(bot\)/ { seen_apply=1 }
    in_admin && /await render_main_menu\(message\)/ { exit seen_apply ? 0 : 1 }
' phase-a/bot/handlers/start.py || \
    fail "bot /start must re-apply Mini App menu button for already-known admins"
pass "Phase-A bot reloads persisted admins"

! grep -F 'admin.claim_first' phase-a/bot/handlers/start.py >/dev/null || \
    fail "bot /start must not claim first admin without ADMIN_CLAIM_TOKEN"
grep -F '/admin &lt;ADMIN_CLAIM_TOKEN&gt;' phase-a/bot/handlers/start.py >/dev/null || \
    fail "bot /start should instruct first admin to use the installer claim token"
grep -F "await h_webapp.apply_menu_buttons_once(bot)" phase-a/bot/handlers/start.py >/dev/null && \
    grep -F "await h_webapp.apply_menu_buttons_once(bot)" phase-a/bot/handlers/admin.py >/dev/null || \
    fail "bot must apply Telegram Mini App menu button after admin claim/invite"
grep -F "set_current_tg_id(uid)" phase-a/bot/common.py >/dev/null && \
    grep -F 'body["tg_id"] = str(actor_tg_id)' phase-a/bot/rpc.py >/dev/null && \
    grep -F '"tg_id": tg_id' phase-a/govlessctl/auth.py >/dev/null || \
    fail "bot local RPC calls must preserve Telegram admin id for audit rows"
grep -F 'ADMIN_CLAIM_TOKEN=${CLAIM_TOKEN}' phase-a/systemd/install/install_govless_user.sh >/dev/null && \
    ! grep -F 'ADMIN_CLAIM_TOKEN=${CLAIM_TOKEN})' phase-a/systemd/install/install_govless_user.sh >/dev/null || \
    fail "installer must store but not print full ADMIN_CLAIM_TOKEN"
pass "Phase-A bot first-admin claim is token-gated and refreshes Mini App button"

grep -F "PHASE_A_SRC=" phase-a/systemd/install/install_phase_a.sh >/dev/null || \
    fail "Phase-A installer must resolve the phase-a source root"
grep -F "cp -R \"\${PHASE_A_SRC}/bot\" /opt/govless/bot" phase-a/systemd/install/install_phase_a.sh >/dev/null || \
    fail "Phase-A installer must deploy bot code"
grep -F "cp -R \"\${PHASE_A_SRC}/govlessctl\" /opt/govless/govlessctl" phase-a/systemd/install/install_phase_a.sh >/dev/null || \
    fail "Phase-A installer must deploy govlessctl package"
grep -F "cp -R \"\${PHASE_A_SRC}/webapp\" /opt/govless/webapp" phase-a/systemd/install/install_phase_a.sh >/dev/null || \
    fail "Phase-A installer must deploy WebApp assets"
grep -F "/opt/govless/venv/bin/python -m pip install" phase-a/systemd/install/install_phase_a.sh >/dev/null || \
    fail "Phase-A installer must install Python dependencies into venv"
grep -F "govlessctl-daemon" phase-a/systemd/install/install_phase_a.sh >/dev/null || \
    fail "Phase-A installer must create govlessctl daemon wrapper"
grep -F "PY=/opt/govless/venv/bin/python" phase-a/systemd/bin/govless-bot >/dev/null || \
    fail "govless-bot shim must use Phase-A venv"
pass "Phase-A installer deploys runnable bot/govlessctl/WebApp"

grep -F "OnUnitActiveSec=300" phase-a/systemd/tunnel-health.timer >/dev/null || \
    fail "Phase-A Lite tunnel health-check must run every 5 minutes, not every minute"
grep -F 'FAIL_THRESHOLD="${GOVLESS_TUNNEL_FAIL_THRESHOLD:-2}"' phase-a/systemd/bin/govless-tunnel-health-check >/dev/null || \
    fail "Phase-A Lite tunnel health-check must restart after 2 failed 5-minute probes"
grep -F 'sha256:$(tunnel_fp "$TUNNEL_URL")' phase-a/systemd/bin/govless-tunnel-health-check >/dev/null || \
    fail "Phase-A Lite tunnel health-check must log only tunnel URL fingerprints"
grep -F '/usr/local/bin/govless-tunnel-url-extract >/dev/null 2>&1 || true' phase-a/systemd/bin/govless-tunnel-health-check >/dev/null && \
    grep -F 'tunnel URL recovered from cloudflared log' phase-a/systemd/bin/govless-tunnel-health-check >/dev/null || \
    fail "Phase-A Lite tunnel health-check must recover missing tunnel.url from cloudflared.log"
grep -F 'if systemctl restart "$SERVICE" >/dev/null 2>&1; then' phase-a/systemd/bin/govless-tunnel-health-check >/dev/null && \
    grep -F '/usr/local/bin/govless-tunnel-url-extract >/dev/null 2>&1 || true' phase-a/systemd/bin/govless-tunnel-health-check >/dev/null || \
    fail "Phase-A Lite tunnel health-check must extract a new URL after restarting cloudflared"
! grep -F 'probe FAILED for $TUNNEL_URL' phase-a/systemd/bin/govless-tunnel-health-check >/dev/null || \
    fail "Phase-A Lite tunnel health-check must not log raw tunnel URL"
grep -F 'systemctl restart cloudflared-quick.service >/dev/null 2>&1 || \' phase-a/systemd/install/install_phase_a.sh >/dev/null || \
    fail "Phase-A Lite installer must restart quick tunnel to avoid stale trycloudflare URLs"
grep -F 'systemctl start cloudflared-url.service >/dev/null 2>&1 || true' phase-a/systemd/install/install_phase_a.sh >/dev/null || \
    fail "Phase-A Lite installer must run cloudflared-url.service once after quick tunnel startup"
grep -F 'TUNNEL_URL_PATHS = ("/run/govless/tunnel.url", "/run/govless/tunnel.url.named")' phase-a/bot/handlers/webapp.py >/dev/null || \
    fail "Telegram WebApp watcher must monitor quick and named tunnel URL files"
grep -F 'URL fp: <code>sha256:{url_fp}</code>' phase-a/bot/handlers/webapp.py >/dev/null && \
    ! grep -F '<code>{url}</code>' phase-a/bot/handlers/webapp.py >/dev/null || \
    fail "Telegram WebApp watcher notifications must not send raw tunnel URLs"
pass "Phase-A Lite tunnel health-check uses low-frequency 10-minute recovery window"

grep -F "/etc/nginx/sites-enabled/govless" phase-a/systemd/install/install_phase_a.sh >/dev/null || \
    fail "Phase-A Pro WebApp installer must target the real goVLESS nginx site"
grep -F "webapp_insert_mode" phase-a/systemd/install/install_phase_a.sh >/dev/null || \
    fail "Phase-A Pro WebApp installer must fall back from TLS server to first server block"
pass "Phase-A Pro WebApp installer wires snippet into real nginx site"

grep -F "location ^~ /app/" phase-a/systemd/nginx/govless-webapp.conf >/dev/null || \
    fail "Phase-A Pro WebApp /app/ location must outrank site static regex locations"
grep -F "alias /opt/govless/webapp/dist/;" phase-a/systemd/nginx/govless-webapp.conf >/dev/null || \
    fail "Phase-A Pro WebApp must serve /app/* from dist via alias"
if awk '/location \^~ \/app\//,/^}/ { if ($0 ~ /try_files/) found=1 } END { exit found ? 0 : 1 }' \
    phase-a/systemd/nginx/govless-webapp.conf; then
    fail "Phase-A Pro WebApp /app/ alias must not use try_files that falls through to the main site"
fi
grep -F 'add_header Cache-Control "no-store" always;' phase-a/systemd/nginx/govless-webapp.conf >/dev/null || \
    fail "Phase-A Pro WebApp assets must disable browser cache during active development"
grep -F 'location ^~ /app/design/' phase-a/systemd/nginx/govless-webapp.conf >/dev/null || \
    fail "Phase-A Pro WebApp must serve design canvas under /app/design/"
grep -F 'https://unpkg.com' phase-a/systemd/nginx/govless-webapp.conf >/dev/null && \
    grep -F 'https://fonts.googleapis.com' phase-a/systemd/nginx/govless-webapp.conf >/dev/null && \
    grep -F 'https://fonts.gstatic.com' phase-a/systemd/nginx/govless-webapp.conf >/dev/null || \
    fail "Phase-A Pro design canvas CSP must allow its React/Babel and font CDNs"
grep -F 'DESIGN_CSP' phase-a/webapp/server.py >/dev/null && \
    grep -F 'self.path.startswith("/design/")' phase-a/webapp/server.py >/dev/null || \
    fail "Phase-A Lite design canvas must get a design-specific CSP"
grep -E 'app\.(js|jsx)\?v=' phase-a/webapp/dist/index.html >/dev/null || \
    fail "Phase-A WebApp index must cache-bust its app script"
grep -F 'style.css?v=' phase-a/webapp/dist/index.html >/dev/null || \
    fail "Phase-A WebApp index must cache-bust style.css"
pass "Phase-A Pro WebApp static assets outrank site regex locations"

grep -F "HomeScreen" phase-a/webapp/dist/app.jsx >/dev/null || \
    fail "Phase-A WebApp main screen must use operator dashboard layout"
grep -F 'gridTemplateColumns:"1fr 2fr"' phase-a/webapp/dist/app.jsx >/dev/null && \
    grep -F "L.keys_tile" phase-a/webapp/dist/app.jsx >/dev/null || \
    fail "Phase-A WebApp main screen must expose large quick actions"
grep -F "cs.slice(0,5)" phase-a/webapp/dist/app.jsx >/dev/null || \
    fail "Phase-A WebApp main screen must preview clients"
pass "Phase-A WebApp uses operator-dashboard UI shell"

awk '
    /^        \{\/\* QA tiles \*\// { in_grid=1; saw_key=0 }
    in_grid && /L\.keys_tile/ { saw_key=1 }
    in_grid && /L\.subscr|copy_sub|Подписка/ {
        exit 1
    }
    in_grid && /^        \{\/\* Clients section \*\// { exit saw_key ? 0 : 1 }
' phase-a/webapp/dist/app.jsx || \
    fail "Phase-A WebApp home must use a single keys tile and not advertise Lite subscriptions"
pass "Phase-A WebApp home hides subscription tile in Lite"

grep -F "onShowQR(true)" phase-a/webapp/dist/app.jsx >/dev/null || \
    fail "Phase-A WebApp subscription card must expose subscription QR"
grep -F "onShowQR(false)" phase-a/webapp/dist/app.jsx >/dev/null || \
    fail "Phase-A WebApp VLESS card must request direct VLESS QR explicitly"
grep -F 'qr.kind==="subscription"?L.copy_sub:L.copy_key' phase-a/webapp/dist/app.jsx >/dev/null || \
    fail "Phase-A WebApp QR modal must label subscription copy as subscription, not key"
awk '
    /function ClientCardScreen/ { in_route=1; saw_sub=0; saw_vless=0 }
    in_route && /\{cl\.sub_url && card/ { saw_sub=1 }
    in_route && /\{cl\.link && card/ {
        saw_vless=1
        if (!saw_sub) exit 1
    }
    in_route && /button onClick=\{onToggle\}/ { exit (saw_sub && saw_vless) ? 0 : 1 }
' phase-a/webapp/dist/app.jsx || \
    fail "Phase-A WebApp Pro client card must show subscription before direct VLESS key"
grep -F "prefer_subscription:preferSub" phase-a/webapp/dist/app.jsx >/dev/null || \
    fail "Phase-A WebApp QR helper must pass subscription preference to RPC"
grep -F 'qr.kind==="subscription"' phase-a/webapp/dist/app.jsx >/dev/null || \
    fail "Phase-A WebApp QR modal must label subscription vs VLESS QR"
pass "Phase-A WebApp exposes Pro-first subscription and separate VLESS QR actions"

grep -F '"www-data", "nginx"' phase-a/govlessctl/auth.py >/dev/null || \
    fail "Phase-A WebApp auth must allow nginx UNIX-socket peer before Telegram initData check"
grep -F 'usermod -a -G "$GROUP_NAME" "$web_user"' phase-a/systemd/install/install_govless_user.sh >/dev/null || \
    fail "Phase-A installer must add nginx worker users to govless group for WebApp RPC socket"
grep -F "systemctl restart nginx" phase-a/systemd/install/install_phase_a.sh >/dev/null || \
    fail "Phase-A Pro installer must restart nginx so new group membership applies"
pass "Phase-A WebApp RPC socket allows nginx peer with Telegram auth"

phasea_xui_db="$(mktemp /tmp/govless_phasea_xui_settings.XXXXXX)"
python3 - "$phasea_xui_db" <<'PY' || fail "Phase-A XuiClient must honor goVLESS credentials and DB panel URL"
import sqlite3
import sys
import types

sys.path.insert(0, "phase-a")
sys.modules.setdefault(
    "aiohttp",
    types.SimpleNamespace(
        ClientSession=object,
        CookieJar=lambda unsafe=False: object(),
        ClientTimeout=lambda total=None: object(),
        ClientConnectionError=Exception,
    ),
)
from govlessctl.xui_client import XuiClient, _local_panel_url_from_db

db = sys.argv[1]
conn = sqlite3.connect(db)
conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
conn.execute("INSERT INTO settings(key, value) VALUES('webPort', '25716')")
conn.execute("INSERT INTO settings(key, value) VALUES('webBasePath', '/panel-path/')")
conn.execute("INSERT INTO settings(key, value) VALUES('webCertFile', '/root/cert/ip/ip.crt')")
conn.commit()
conn.close()

assert _local_panel_url_from_db(db) == "https://127.0.0.1:25716/panel-path"
client = XuiClient({"URL": "https://127.0.0.1:45038/random/", "USERNAME": "admin", "PASSWORD": "pw"})
assert client.url == "https://127.0.0.1:45038/random"
assert client.username == "admin"
assert client.password == "pw"
PY
rm -f "$phasea_xui_db"
pass "Phase-A XuiClient supports goVLESS credentials and DB panel URL"

python3 - <<'PY' || fail "Phase-A XuiClient login must fetch CSRF before POST /login"
import inspect
import sys
import types

sys.path.insert(0, "phase-a")
sys.modules.setdefault(
    "aiohttp",
    types.SimpleNamespace(
        ClientSession=object,
        CookieJar=lambda unsafe=False: object(),
        ClientTimeout=lambda total=None: object(),
        ClientConnectionError=Exception,
    ),
)
from govlessctl.xui_client import XuiClient, _extract_csrf_token, _normalize_client_payload

assert _extract_csrf_token('<meta name="csrf-token" content="abc123">') == "abc123"
assert _normalize_client_payload({"email": "alice", "tgId": "", "totalGB": "1073741824"})["tgId"] == 0
assert _normalize_client_payload({"email": "alice", "tgId": "", "totalGB": "1073741824"})["totalGB"] == 1073741824
login_src = inspect.getsource(XuiClient._login)
request_src = inspect.getsource(XuiClient._request)
add_src = inspect.getsource(XuiClient.add_client)
update_src = inspect.getsource(XuiClient.update_client)
delete_src = inspect.getsource(XuiClient.delete_client)
reset_src = inspect.getsource(XuiClient.reset_client_traffic)
onlines_src = inspect.getsource(XuiClient.get_onlines)
assert 'session.get(f"{self.url}/")' in login_src
assert 'headers["X-CSRF-Token"] = self._csrf' in login_src
assert 'headers["X-CSRF-Token"] = self._csrf' in request_src
assert 'payload.get("success") is False' in request_src
assert 'session.post(f"{self.url}/login", data=data, headers=headers)' in login_src
assert '"/panel/api/clients/add"' in add_src
assert '"/panel/api/inbounds/addClient"' in add_src
assert '"/panel/api/clients/update/' in update_src
assert '"/panel/api/inbounds/updateClient/' in update_src
assert '"/panel/api/clients/del/' in delete_src
assert '"/panel/api/inbounds/{int(inbound_id)}/delClient/' in delete_src
assert '"/panel/api/clients/resetTraffic/' in reset_src
assert '"/panel/api/inbounds/{int(inbound_id)}/resetClientTraffic/' in reset_src
assert '"/panel/api/clients/onlines"' in onlines_src
assert '"/panel/api/inbounds/onlines"' in onlines_src
assert 'data=body' in add_src
assert 'data=body' in update_src
assert 'except XuiError as exc' in add_src
assert 'except XuiError as exc' in update_src
PY
pass "Phase-A XuiClient supports CSRF plus 3X-UI v3/v2 client endpoints"

python3 - <<'PY' || fail "Phase-A bot traffic limits must use 3X-UI byte units"
import inspect
import sys
import types

sys.path.insert(0, "phase-a")
sys.modules.setdefault(
    "aiohttp",
    types.SimpleNamespace(
        ClientSession=object,
        CookieJar=lambda unsafe=False: object(),
        ClientTimeout=lambda total=None: object(),
        ClientConnectionError=Exception,
    ),
)
from govlessctl import methods

assert methods._traffic_limit_to_bytes(0) == 0
# Legacy callers (bot before the traffic_unit parameter) send GiB with no unit.
assert methods._traffic_limit_to_bytes(50) == 50 * 1024**3
assert methods._traffic_limit_to_bytes(10737418240) == 10737418240
# Explicit units win over the magnitude heuristic (QA BUG-40: a 500 KB cap
# used to be stored as 500 GiB).
assert methods._traffic_limit_to_bytes(50, unit="gib") == 50 * 1024**3
assert methods._traffic_limit_to_bytes(512000, unit="bytes") == 512000
assert methods._traffic_limit_to_bytes(10737418240, unit="bytes") == 10737418240
client_list_src = inspect.getsource(methods.Methods.client_list)
client_add_src = inspect.getsource(methods.Methods.client_add)
client_update_src = inspect.getsource(methods.Methods.client_update)
assert '"traffic_limit": int(cli.get("totalGB", 0))' in client_list_src
assert '"totalGB": traffic_bytes' in client_add_src
assert 'new_tl = _traffic_limit_to_bytes(fields["traffic_limit"]' in client_update_src
assert 'active_network == "tcp"' in client_add_src
PY
pass "Phase-A bot traffic limits use 3X-UI byte units"

python3 - <<'PY' || fail "Phase-A system.status must report installed goVLESS version"
import asyncio
import sys
import types

sys.path.insert(0, "phase-a")
sys.modules.setdefault(
    "aiohttp",
    types.SimpleNamespace(
        ClientSession=object,
        CookieJar=lambda unsafe=False: object(),
        ClientTimeout=lambda total=None: object(),
        ClientConnectionError=Exception,
    ),
)
from govlessctl import methods


class DummyXui:
    url = "https://127.0.0.1:14978/panel"


methods._read_config_json = lambda: {"mode": "pro", "transport": "tcp", "version": "3.0.6"}
methods._systemctl_active = lambda unit: False
methods._proc_running = lambda name: False
m = methods.Methods(state=object(), xui=DummyXui(), bot_env={})
m._read_panel_url_from_creds = lambda: None
status = asyncio.run(m.system_status({}, {}))
assert status["version"] == "3.0.6", status
PY
pass "Phase-A system.status reports installed goVLESS version"

python3 - <<'PY' || fail "Phase-A Pro WebApp URL must be exposed to Telegram menu button"
import asyncio
import sys
import types

sys.path.insert(0, "phase-a")
sys.modules.setdefault(
    "aiohttp",
    types.SimpleNamespace(
        ClientSession=object,
        CookieJar=lambda unsafe=False: object(),
        ClientTimeout=lambda total=None: object(),
        ClientConnectionError=Exception,
    ),
)
from govlessctl import methods


class DummyXui:
    url = "https://127.0.0.1:14978/panel"


methods.TUNNEL_URL_NAMED_FILE = "/tmp/govless-test-no-named-tunnel-url"
methods.TUNNEL_URL_FILE = "/tmp/govless-test-no-quick-tunnel-url"
methods._read_config_json = lambda: {"mode": "pro", "domain": "lol123.uk"}
m = methods.Methods(state=object(), xui=DummyXui(), bot_env={})
res = asyncio.run(m.tunnel_url_get({}, {}))
assert res == {"url": "https://lol123.uk/app/?v=20260525-042", "source": "pro"}, res
PY
pass "Phase-A Pro WebApp URL is exposed to Telegram menu button"

python3 - <<'PY' || fail "Phase-A bot link builder must mirror SSH flow/ALPN rules"
import sys
import types

sys.path.insert(0, "phase-a")
sys.modules.setdefault(
    "aiohttp",
    types.SimpleNamespace(
        ClientSession=object,
        CookieJar=lambda unsafe=False: object(),
        ClientTimeout=lambda total=None: object(),
        ClientConnectionError=Exception,
    ),
)
from govlessctl.xui_client import build_vless_links

inbounds = [{
    "id": 7,
    "port": 443,
    "protocol": "vless",
    "enable": True,
    "settings_obj": {"clients": [{
        "id": "11111111-1111-4111-8111-111111111111",
        "email": "alice",
        "flow": "",
    }]},
    "stream_obj": {
        "network": "grpc",
        "security": "tls",
        "tlsSettings": {"serverName": "example.test", "alpn": ["h2"]},
        "grpcSettings": {"serviceName": "govless svc", "multiMode": True},
    },
}]
link = build_vless_links("example.test", inbounds)[0]["link"]
assert "encryption=none" in link
assert "alpn=h2" in link
assert "serviceName=govless%20svc" in link
assert "flow=xtls-rprx-vision" not in link
PY
pass "Phase-A bot link builder mirrors SSH flow/ALPN rules"

grep -F 'params = f"encryption=none&type={transport}&security=reality"' lib/xui_api.sh >/dev/null && \
    grep -F 'params = f"encryption=none&type={transport}&security=tls&sni={server}&fp={fp}"' lib/xui_api.sh >/dev/null && \
    grep -F 'params = f"encryption=none&type={network}&{common}"' lib/xui_api.sh >/dev/null || \
    fail "SSH regenerated VLESS links must include encryption=none"
grep -F 'local params="encryption=none&type=${transport}&security=reality' lib/xui_api.sh >/dev/null && \
    grep -F 'local params="encryption=none&type=${transport}&security=tls' lib/xui_api.sh >/dev/null || \
    fail "Initial SSH VLESS link helpers must include encryption=none"
pass "VLESS link generators include encryption=none"

# Codex 025 P3 #2 — build_sub_url dead code removed (was always emitting http://
# but nothing called it; sub URLs now built inside regenerate_links_from_db).
grep -q "^build_sub_url\(\)" lib/xui.sh && \
    fail "build_sub_url is still present — should be removed (dead code per Codex 025 P3 #2)"
pass "build_sub_url dead code removed"

# Agent finding §4.5 — certbot deploy_hook restarts services after auto-renewal
grep -qE 'renewal-hooks/deploy/govless-restart\.sh' lib/website.sh && \
    grep -qE 'systemctl reload nginx' lib/website.sh || \
    fail "certbot deploy_hook not installed by setup_ssl_auto_renewal"
if ! awk '
    /^setup_ssl_auto_renewal\(\)/ { in_fn=1; saw_hook=0 }
    in_fn && /_install_certbot_deploy_hook/ { saw_hook=1 }
    in_fn && /return 0/ && !saw_hook { exit 1 }
    in_fn && /^}/ { exit saw_hook ? 0 : 1 }
' lib/website.sh; then
    fail "setup_ssl_auto_renewal must install deploy_hook before any success return"
fi
pass "certbot deploy_hook installed by setup_ssl_auto_renewal"

# Agent finding §6.4 — regen Python handles malformed inbound rows without raw traceback
grep -q "regen: inbound id=" lib/xui_api.sh || \
    fail "regen Python missing graceful malformed-row handling"
pass "regen Python logs malformed rows instead of raw traceback"

# Agent finding §13.1 — Pro nginx site has security headers
grep -q "Strict-Transport-Security" lib/website.sh && \
    grep -q "X-Frame-Options" lib/website.sh || \
    fail "Pro nginx config missing security headers (HSTS / Frame-Options)"
pass "Pro nginx config emits security headers"

# Codex 022 P2 #4 — remove_try used in remove_everything (not just defined)
grep -qE 'remove_try.*systemctl stop' lib/xui.sh || \
    fail "remove_try is defined but not wired through remove_everything"
pass "remove_try wired into remove_everything (no more silent cleanup failures)"


for label in "Proxy / VPN" "Users / Clients" "Manage" "About"; do
    grep -F "$label" phase-a/bot/handlers/menu.py >/dev/null || \
        fail "Telegram main menu missing label: $label"
done
grep -F "m:ssh:repair" phase-a/bot/handlers/menu.py >/dev/null || \
    fail "Telegram menu must keep repair as SSH-only until safe RPC exists"
grep -F "m:ssh:remove" phase-a/bot/handlers/menu.py >/dev/null || \
    fail "Telegram menu must keep remove as SSH-only until safe RPC exists"
pass "Telegram main menu mirrors SSH sections with SSH-only guardrails"

if grep -F 'url=client["link"]' phase-a/bot/handlers/clients.py >/dev/null; then
    fail "Telegram client card must not use vless:// links as inline URL buttons"
fi
grep -F "cl:link:" phase-a/bot/handlers/clients.py >/dev/null || \
    fail "Telegram client card must expose copyable VLESS link callback"
grep -F "_code(link)" phase-a/bot/handlers/clients.py >/dev/null || \
    fail "Telegram VLESS links must be HTML-escaped copyable text"
grep -F "_alert_text(" phase-a/bot/handlers/clients.py >/dev/null || \
    fail "Telegram callback alerts must cap long RPC errors"
grep -F "/addclient name [traffic_gb] [days]" phase-a/bot/handlers/clients.py >/dev/null || \
    fail "Telegram clients menu must expose add-client command help"
grep -F "cl:qrv:" phase-a/bot/handlers/clients.py >/dev/null || \
    fail "Telegram client card must expose an explicit VLESS QR callback"
grep -F "has_subscription = any(c.get(\"sub_url\") for c in clients)" phase-a/bot/handlers/clients.py >/dev/null && \
    grep -F "VLESS links, QR, traffic and status" phase-a/bot/handlers/clients.py >/dev/null || \
    fail "Telegram clients menu must not advertise subscriptions when Lite hides them"
awk '
    /def _card_kb\(client/ { in_fn=1; saw_sub=0; saw_link=0 }
    in_fn && /if sub_url:/ { saw_sub=1 }
    in_fn && /if client\.get\("link"\):/ {
        saw_link=1
        if (!saw_sub) exit 1
    }
    in_fn && /^def / && !/def _card_kb/ { exit (saw_sub && saw_link) ? 0 : 1 }
' phase-a/bot/handlers/clients.py || \
    fail "Telegram Pro client card must put subscription actions before VLESS key actions"
grep -F "prefer_subscription=True" phase-a/bot/handlers/clients.py >/dev/null && \
    grep -F "prefer_subscription=False" phase-a/bot/handlers/clients.py >/dev/null && \
    grep -F '"kind": "subscription"' phase-a/govlessctl/methods.py >/dev/null || \
    fail "Telegram QR must prefer subscription QR in Pro while keeping explicit VLESS QR"
grep -F '"update_traffic": ConfirmSpec' phase-a/bot/handlers/confirm.py >/dev/null && \
    grep -F '"update_expiry": ConfirmSpec' phase-a/bot/handlers/confirm.py >/dev/null || \
    fail "Telegram /setlimit and /setexpiry must start typed-confirm flows"
grep -F "target: CallbackQuery | Message" phase-a/bot/handlers/confirm.py >/dev/null || \
    fail "Telegram confirm flow must work from command messages and callback buttons"
grep -F "PENDING.clear(user.id)" phase-a/bot/handlers/confirm.py >/dev/null || \
    fail "Telegram confirm flow must clear pending only after exact match"
if awk '
    /if message\.text != expected:/ { in_mismatch=1 }
    in_mismatch && /^        PENDING\.clear\(user\.id\)/ { exit 1 }
    in_mismatch && /^    PENDING\.clear\(user\.id\)/ { exit 0 }
' phase-a/bot/handlers/confirm.py; then
    :
else
    fail "Telegram confirm mismatch must not cancel immediately"
fi
pass "Telegram client cards use safe links, escaped text, and retry-friendly confirms"

python3 -m unittest test_bot_regressions.py || \
    fail "Telegram bot helper unit regressions failed"
pass "Telegram bot helper unit regressions"

# ── 052/053: bot must NOT log full WebApp URL ──────────────────────────────
# Background: govless-tunnel-url-extract logs fingerprints; bot handler
# previously reintroduced the leak via LOG.info("... url=%s", url). Codex 052
# flagged this as P1. The fix logs a sha256 fingerprint instead.
if grep -F 'url=%s' phase-a/bot/handlers/webapp.py >/dev/null; then
    fail "phase-a/bot/handlers/webapp.py must NOT log full WebApp URL (use url_fp=%s sha256 fingerprint)"
fi
grep -F 'url_fp=%s' phase-a/bot/handlers/webapp.py >/dev/null || \
    fail "phase-a/bot/handlers/webapp.py must log a sha256 url_fp instead of the raw URL"
grep -F 'hashlib.sha256(url.encode' phase-a/bot/handlers/webapp.py >/dev/null || \
    fail "phase-a/bot/handlers/webapp.py must derive url_fp via hashlib.sha256"
pass "Bot WebApp menu handler logs only a URL fingerprint, never the raw tunnel URL"

# ── 052/053: Lite Mini App must mirror Pro Cache-Control: no-store ────────
grep -F 'Cache-Control' phase-a/webapp/server.py >/dev/null && \
    grep -F '"no-store"' phase-a/webapp/server.py >/dev/null || \
    fail "phase-a/webapp/server.py must emit Cache-Control: no-store to mirror Pro nginx"
pass "Lite Mini App server.py mirrors Pro nginx Cache-Control: no-store"

# ── 052/053: no tracked __pycache__/*.pyc artifacts ────────────────────────
if git ls-tree -r --name-only HEAD 2>/dev/null | grep -E '__pycache__|\.pyc$' >/dev/null; then
    fail "Tracked __pycache__/*.pyc artifacts present — run git rm --cached on them"
fi
pass "No tracked __pycache__/*.pyc artifacts"

# ── 2026-05-29 Opus QA-2000: regression pins for this session's P0s ────────
# (1) inbound.toggle must be read-modify-write, never a bare {enable} full-replace
#     (that blanked the inbound + all clients — took Pro offline).
grep -qF 'cur = await self.get_inbound(int(inbound_id))' phase-a/govlessctl/xui_client.py || \
    fail "toggle_inbound must GET the full inbound (read-modify-write); bare update wipes it"
if grep -qF 'body = {"enable": bool(enable)}' phase-a/govlessctl/xui_client.py; then
    fail "toggle_inbound still sends a bare {enable} body — DATA-LOSS regression (3X-UI update = full replace)"
fi
pass "inbound.toggle is read-modify-write (no bare {enable} full-object replace)"

# (2) caller=local must allow the bot service user (govless), not root-only.
#     Root-only (W-REG-4) returned 403 'caller uid=999 (govless)' on every bot RPC.
grep -qF 'else ("root", "govless")' phase-a/govlessctl/auth.py || \
    fail "caller=local allowed_users must include 'govless' (the bot service acct) or the bot 403s on every RPC"
pass "caller=local allows root + govless (bot not locked out)"

# (3) bot common.py imports must resolve against rpc.py (drifted deploy crash-looped the bot).
for fn in set_current_tg_id reset_current_tg_id; do
    grep -q "def ${fn}" phase-a/bot/rpc.py || \
        fail "phase-a/bot/rpc.py must define ${fn} (common.py imports it; missing -> bot ImportError crash-loop)"
done
pass "bot rpc.py provides the contextvar helpers common.py imports"

# (4) 2026-05-29: Mini App STR table must NOT self-reference L (L=STR[TG_LANG] is
#     defined ~line 1850). c830b56 wrote keys like clients_count:L.clients_count
#     -> ReferenceError 'L is not defined' at module load -> TG.ready() never fires
#     -> eternal Telegram loading placeholder (blank Mini App on Pro AND Lite).
if grep -qE '(clients_count|vpn_off|time_locale|tunnel_changed|qr_unavail|daemons_restarted):L\.' phase-a/webapp/dist/app.jsx; then
    fail "app.jsx STR table has self-ref L.<key> (clients_count:L.clients_count etc.) — blanks the Mini App (ReferenceError before TG.ready)"
fi
pass "Mini App STR table uses literals, not undefined-L self-references"

# BBR speed-up: installer must SELF-enable BBR (fq+bbr via sysctl), NOT depend on
# 3x-ui's interactive menu (BBR is menu item #24 / bbr_menu, no `x-ui bbr` CLI) —
# menu-driving breaks if items get renumbered across 3x-ui versions.
grep -F "enable_bbr() {" lib/common.sh >/dev/null && \
    [ "$(grep -cF 'enable_bbr' lib/common.sh)" -ge 2 ] && \
    grep -F "net.ipv4.tcp_congestion_control = bbr" lib/common.sh >/dev/null && \
    grep -F "net.core.default_qdisc = fq" lib/common.sh >/dev/null || \
    fail "installer must self-enable BBR (enable_bbr defined+called + fq/bbr sysctl drop-in)"
pass "installer self-enables BBR (fq+bbr) independent of x-ui menu numbering"

# Telegram-bot settings/admin menu must be wired into the SSH menu (token/admins/
# status/restart), with menu_bot i18n in BOTH languages and a bot.env writer.
grep -F "submenu_bot() {" govless.sh >/dev/null && \
    grep -F "4) submenu_bot ;;" govless.sh >/dev/null && \
    grep -F "menu_bot)" govless.sh >/dev/null && \
    grep -F "_bot_env_set BOT_TOKEN" govless.sh >/dev/null && \
    grep -F "I18N[menu_bot]=" lib/lang/ru.sh >/dev/null && \
    grep -F "I18N[menu_bot]=" lib/lang/en.sh >/dev/null || \
    fail "Telegram-bot settings menu must be wired (submenu_bot + menu_bot RU/EN + bot.env writer)"
pass "Telegram-bot settings/admin menu wired (token/admins/status/restart, RU+EN)"

# Post-install step-by-step bot wizard: offered after install (post_install_flow),
# auto-detects the admin by messaging the bot (getUpdates), offers more admins.
grep -F "bot_setup_wizard() {" govless.sh >/dev/null && \
    grep -F "_bot_detect_admin() {" govless.sh >/dev/null && \
    grep -F "getUpdates" govless.sh >/dev/null && \
    [ "$(grep -cF 'offer_bot_setup' govless.sh)" -ge 2 ] && \
    grep -F "I18N[bot_wiz_step2]=" lib/lang/ru.sh >/dev/null && \
    grep -F "I18N[bot_wiz_step2]=" lib/lang/en.sh >/dev/null || \
    fail "post-install bot wizard must be wired (offer_bot_setup called + auto-detect admin via getUpdates + RU/EN)"
pass "post-install step-by-step bot wizard wired (token -> auto-detect admin -> more admins -> web menu)"

# show_user_link_choice is called inside `while ... done < <(show_all_users)`
# (submenu_users QR) — its choice read MUST come from /dev/tty or it steals a
# user-list row and loops "invalid choice" instead of issuing the QR.
grep -F "pick < /dev/tty" lib/xui_api.sh >/dev/null || \
    fail "show_user_link_choice must read the QR/link choice from /dev/tty"
pass "QR/link-choice reads from /dev/tty (works inside the show_all_users loop)"

# SSH "Show QR" must let the operator PICK which user (numbered list), not dump
# all users / only the last one. Picker uses users_pick_qr + a /dev/tty read.
grep -F "I18N[users_pick_qr]" lib/lang/ru.sh >/dev/null && \
    grep -F "I18N[users_pick_qr]" lib/lang/en.sh >/dev/null && \
    grep -F 'read -rp "  ▸ " _qp </dev/tty' govless.sh >/dev/null || \
    fail "SSH Show-QR must offer a per-user picker (users_pick_qr), not loop all users"
pass "SSH Show-QR has a per-user picker (RU+EN)"

# Pro mode: the x-ui PANEL must be served on the DOMAIN with the domain LE cert,
# not an IP self-signed cert. configure_panel_tls must prefer the LE cert when a
# domain + /etc/letsencrypt/live/<domain> exists, save_credentials must put the
# domain in URL= for pro, and install_pro must reconfigure panel TLS AFTER the
# domain is in config.
grep -q 'live/${domain}/fullchain.pem' lib/xui.sh || \
    fail "configure_panel_tls must use the domain LE cert in pro"
grep -q 'xui_paneltls_domain' lib/xui.sh || \
    fail "configure_panel_tls missing domain-cert branch"
grep -q 'using domain certificate for' lib/lang/en.sh || \
    fail "xui_paneltls_domain text missing from en.sh"
grep -q 'URL=https://${host}:' lib/xui.sh || \
    fail "save_credentials must use domain host for pro panel URL"
grep -q 'configure_panel_tls "${LANG_CODE:-en}" "$server_ip" "$domain"' govless.sh || \
    fail "install_pro must reconfigure panel TLS with the domain after config_set domain"
pass "Pro panel uses domain + domain LE cert (not IP)"

# Pro: the FINAL on-screen credentials display (show_credentials) must also show
# the domain URL, not the IP — consistent with save_credentials + the cert.
grep -q 'Pro mode: show the panel URL on the domain (matches the domain LE cert' lib/xui_api.sh || \
    fail "show_credentials must show domain URL for pro"
grep -q 'local url="https://${host}:' lib/xui_api.sh || \
    fail "show_credentials must build URL from host (domain for pro)"
pass "show_credentials shows domain URL for pro (final screen)"

# 3X-UI download must NOT validate the tarball with the `file` command — it is
# absent on minimal images (Ubuntu 24.04), which made the gzip check fail even
# when the download succeeded ("download failed after 3 attempts"). Must use a
# dependency-free check (gzip -t / magic bytes via _is_gzip).
grep -q '_is_gzip "$tarball"' lib/xui.sh || \
    fail "3X-UI download must validate via _is_gzip, not the file command"
grep -q 'file "$tarball" 2>/dev/null | grep -qi "gzip"' lib/xui.sh && \
    fail "3X-UI download still depends on the file command for gzip check"
grep -q '^_is_gzip()' lib/xui.sh || fail "_is_gzip helper missing"
pass "3X-UI tarball gzip check is dependency-free (works without file cmd)"

# apt_update / apt_install must NOT abort the whole install when a THIRD-PARTY
# repo is broken (e.g. goVLESS's own pkg.cloudflare.com with an expired GPG key
# left over from a Lite install). Our deps come from the Ubuntu mirrors, so a
# broken extra repo must be tolerated, not fatal ("apt-get update failed").
grep -q 'APT::Update::Error-Mode=any' lib/common.sh || \
    fail "apt_update must tolerate a broken third-party repo"
grep -q 'AllowInsecureRepositories=true' lib/common.sh || \
    fail "apt_install must retry past an unsigned/broken third-party repo"
grep -q 'apt_thirdparty_repo_warn' lib/lang/en.sh || fail "missing apt third-party warn key (en)"
pass "apt update/install tolerate a broken third-party repo (cloudflared GPG)"

# Fingerprint (fp=) must be selectable, not hardcoded chrome. There must be a
# select_fingerprint picker, a global XUI_FP, the config persists it, and no
# lib file still hardcodes fp=chrome in a key string.
grep -q 'select_fingerprint()' lib/xui.sh || fail "select_fingerprint picker missing"
grep -q 'XUI_FP=' lib/xui.sh || fail "XUI_FP global missing"
grep -q 'config_set "fingerprint"' govless.sh || fail "fingerprint not persisted to config"
grep -q 'fp=chrome' lib/xui_api.sh && fail "lib/xui_api.sh still hardcodes fp=chrome"
grep -q 'fp=chrome' phase-a/govlessctl/xui_client.py && fail "daemon still hardcodes fp=chrome"
grep -q 'fp_randomized' lib/lang/en.sh || fail "fingerprint i18n (en) missing"
pass "TLS fingerprint is selectable (safari default), no hardcoded fp=chrome"

# Reordered picker: recommendation top->bottom, safari is #1 (bold/default), and
# the post-install fp-change flow exists + is wired into the update path.
grep -qF '${BOLD}safari${NC}' lib/xui.sh || fail "picker option 1 must be safari (bold/recommended)"
grep -qF 'GOVLESS_FP:-safari' govless.sh || fail "lazy mode default fingerprint must be safari"
grep -q 'apply_fingerprint_to_inbounds()' lib/xui.sh || fail "apply_fingerprint_to_inbounds missing"
grep -q 'maybe_offer_fingerprint_change()' govless.sh || fail "maybe_offer_fingerprint_change missing"
grep -qE '^[[:space:]]+maybe_offer_fingerprint_change$' govless.sh || fail "fp-change offer not wired into main()"
grep -q 'fp_safari' lib/lang/ru.sh || fail "fp_safari i18n (ru) missing"
grep -q 'fp_safari' lib/lang/en.sh || fail "fp_safari i18n (en) missing"
grep -q 'fp_offer_prompt' lib/lang/ru.sh || fail "fp_offer i18n (ru) missing"
grep -q 'fp_offer_prompt' lib/lang/en.sh || fail "fp_offer i18n (en) missing"
pass "fp picker reordered (safari #1), post-update fp-change offer wired + i18n present"

# Mode switch (Lite<->Pro) must PRESERVE the fingerprint, not reset it to chrome.
# Otherwise the recreated inbound gets fp=chrome while config.json keeps the old
# value -> SSH-menu keys and bot/MiniApp keys disagree for the same client.
grep -q 'export XUI_FP=' lib/mode_switch.sh || \
    fail "switch_mode must preserve the fingerprint (export XUI_FP)"
grep -q 'config_set "fingerprint"' lib/mode_switch.sh || \
    fail "switch_mode must persist the fingerprint to config"
grep -q 'current_fp' lib/mode_switch.sh || \
    fail "switch_mode must read the existing fingerprint from the inbound snapshot"
pass "mode switch preserves the TLS fingerprint (SSH/bot keys stay consistent)"

# Lazy Pro mode: menu option 3 runs install_pro with GOVLESS_LAZY=1, auto-picking
# random template / 5 keys / TCP / safari fp, only asking for the domain.
grep -qF '""|1) GOVLESS_LAZY=1 install_pro' govless.sh || \
    fail "menu must offer lazy Pro as option 1 (default on empty Enter)"
grep -q 'GOVLESS_LAZY:-0.*=.*1' govless.sh || fail "install_pro must honour GOVLESS_LAZY"
grep -q 'pick_random_template_id' lib/templates_catalog.sh || fail "pick_random_template_id helper missing"
grep -q 'local users_count=5' govless.sh || fail "lazy mode must default to 5 keys"
grep -q 'install_lazy_title' lib/lang/en.sh || fail "lazy i18n (en) missing"
# default fingerprint must be safari (recommended)
grep -q 'GOVLESS_FP:-safari' lib/xui.sh || fail "default fingerprint must be safari"
grep -q 'if \[ "${GOVLESS_LAZY:-0}" != "1" \]; then' govless.sh || fail "lazy must guard the email prompt"
grep -q 'lazy_dns_warn' lib/lang/en.sh || fail "lazy must not block on the DNS menu"
grep -q 'install_mode_choice]="Choice (1/2/3, default 1)' lib/lang/en.sh || \
    fail "mode-select prompt (en) must show default 1 (lazy preferred)"
grep -q 'install_mode_choice]="Выбор (1/2/3, по умолчанию 1)' lib/lang/ru.sh || \
    fail "mode-select prompt (ru) must show default 1 (lazy preferred)"
# Client app links must point to INCY on both stores, and the hints must agree.
# This pin still demanded Happ long after ed8d0fe moved the product to INCY in
# govless.sh and in both language files, so the suite aborted right here and
# EVERY check below it silently stopped running. Keep it on whatever ships.
grep -q 'id6756943388' govless.sh || fail "iOS app link must be INCY (id6756943388)"
grep -q 'llc.itdev.incy' govless.sh || fail "Android app link must be INCY (llc.itdev.incy)"
grep -qiE 'hiddify|happproxy' govless.sh && fail "superseded client app (Hiddify/Happ) still referenced in govless.sh"
grep -q 'INCY' lib/lang/en.sh || fail "en app hint must mention INCY"
grep -q 'INCY' lib/lang/ru.sh || fail "ru app hint must mention INCY"
# Menu reorder: lazy=1 (default/recommended), pro=2, lite=3
grep -q '3) install_lite ;;' govless.sh || fail "lite must be menu option 3"
grep -q '2) install_pro ;;' govless.sh || fail "pro must be menu option 2"
grep install_lazy_title lib/lang/ru.sh | grep -qi 'рекоменд' || fail "ru lazy title must say рекомендуется"
grep install_lazy_title lib/lang/en.sh | grep -qi 'recommended' || fail "en lazy title must say recommended"
pass "Lazy Pro mode (auto template/5 keys/TCP/randomized, domain only) + randomized default"

# Interactive main_menu must run with errexit OFF: handlers like restart/logs/
# backup/repair and the empty user list legitimately return non-zero, and under
# `set -e` that ejected the user from the SSH menu to the shell.
# Checked by walking the errexit state down to the main_menu call rather than a
# fixed grep window: the old `grep -A2 "set +e"` broke the moment another call
# was inserted between the two, even though the guard was still there.
awk '
    /^[[:space:]]*set \+e[[:space:]]*$/ { errexit_off = 1 }
    /^[[:space:]]*set -e[[:space:]]*$/   { errexit_off = 0 }
    /^[[:space:]]*main_menu[[:space:]]*$/ && !seen { seen = 1; guarded = errexit_off }
    END { exit (guarded ? 0 : 1) }
' govless.sh || \
    fail "main_menu must run with 'set +e' (a fallible handler must not exit the installer)"
pass "main_menu runs with set +e (menu survives fallible handlers / read EOF)"

# Typed-confirm strings must use the SAME name fallback as client.list
# (email or user-<8hex>), or blank-email clients 412-loop on reset/delete/rotate.
grep -F "cli.get('email') or f'user-{uid[:8]}'" phase-a/govlessctl/methods.py >/dev/null || \
    fail "confirm strings must match client_list name fallback for blank-email clients"
pass "confirm strings match client_list name fallback (no 412-loop on blank-email clients)"

# Mini App STR.ru and STR.en must have identical key sets — an EN-missing key
# renders `undefined` (or white-screens via L.<key>.replace on the search/confirm
# screens) for English users.
python3 tools/check-str-parity.py phase-a/webapp/dist/app.jsx >/dev/null 2>&1 || \
    fail "Mini App STR.ru/STR.en key parity broken (EN-missing key -> undefined / white-screen)"
pass "Mini App STR ru/en key parity (no EN-missing keys)"

# ── goVLESS 1.4.0: cover-site template switch (SSH menu + bot + Mini App) ──
# The template picker used to exist only inside install_pro: after install the
# masquerade site could not be changed ("Сменить режим / шаблон" only switched
# the mode and Lite→Pro deployed the stub). Pin every layer of the feature.
# 1. SSH: VPN submenu item 5 → submenu_site; the lib is sourced at startup.
grep -q '5) submenu_site' govless.sh || fail "VPN submenu must expose 5) submenu_site (cover-site template switch)"
grep -q '^submenu_site()' govless.sh || fail "submenu_site() missing in govless.sh"
grep -q 'source "${SCRIPT_DIR}/lib/templates_catalog.sh"' govless.sh || \
    fail "govless.sh must source lib/templates_catalog.sh at startup (menu + --site-template-* need it)"
grep -q -- '--site-template-\*)' govless.sh && grep -q 'site_template_cli "\$@"; exit' govless.sh || \
    fail "govless.sh must route --site-template-* to site_template_cli (govlessctl entry point)"
for fn in site_template_apply site_template_apply_custom site_template_apply_random site_template_apply_stub \
          site_restore_backup site_template_cli sync_templates_catalog _tpl_page_ok; do
    grep -qE "^${fn}\(\)" lib/templates_catalog.sh || fail "$fn() missing in lib/templates_catalog.sh"
done
grep -qE '^strip_template_service_files\(\)' lib/website.sh || fail "strip_template_service_files() missing in lib/website.sh"
# Lite→Pro switch offers the picker (the stub used to be the only outcome).
grep -q 'site_offer_template_after_switch' lib/mode_switch.sh || fail "switch_mode_interactive must offer a template after Lite→Pro"
# 2. i18n: new keys in BOTH languages; the menu label must not promise a template switch it cannot do.
for key in proxy_site_template site_opt_catalog site_apply_done site_offer_after_switch site_busy \
           templates_nav_hint templates_app_shell templates_broken_assets templates_download_failed website_only_pro; do
    grep -q "I18N\[$key\]" lib/lang/ru.sh && grep -q "I18N\[$key\]" lib/lang/en.sh || fail "$key i18n key missing in ru/en"
done
grep -q 'I18N\[proxy_change_mode\]="Сменить режим (Lite ↔ Pro)"' lib/lang/ru.sh || \
    fail "proxy_change_mode must not claim to switch templates (RU)"
grep -q 'I18N\[proxy_change_mode\]="Switch mode (Lite ↔ Pro)"' lib/lang/en.sh || \
    fail "proxy_change_mode must not claim to switch templates (EN)"
# 3. deploy keeps the ownership marker (remove_site_only refused to clean the web root without it)
#    and strips service files (README/LICENSE/package.json give the template and author away).
awk '/^deploy_template_to_nginx\(\)/{f=1} f&&/govless-owned/{m=1} f&&/strip_template_service_files "\$WEBSITE_ROOT"/{s=1} f&&/^}/{exit} END{exit !(m&&s)}' lib/website.sh || \
    fail "deploy_template_to_nginx must re-create .govless-owned and call strip_template_service_files"
grep -q '__stub__' lib/website.sh || fail "deploy_stub_site must write the __stub__ template marker"
# 4. catalog: the verified library (33 000+), valid, every category still localized (checked above).
if command -v jq &>/dev/null; then
    [ "$(jq '[.categories[].templates[]] | length' templates_catalog.json)" -ge 30000 ] || \
        fail "templates_catalog.json must be the verified 33k library (site-templates-library skill)"
fi
python3 phase-a/govlessctl/catalog_tool.py validate templates_catalog.json >/dev/null 2>&1 || \
    fail "templates_catalog.json fails catalog_tool.py validate (dup id / repo, bad url)"
# 5. daemon: RPC methods for the bot / Mini App; apply runs as a background job (clones exceed the 30 s RPC timeout).
for m in site.template_status site.template_categories site.template_list site.template_get \
         site.template_random site.template_apply site.template_job; do
    grep -qF "\"$m\": self." phase-a/govlessctl/methods.py || fail "govlessctl dispatch table lacks $m"
done
grep -q 'create_task(self._site_job_run' phase-a/govlessctl/methods.py || \
    fail "site.template_apply must run the installer as a background job (not inline)"
grep -q '^from .catalog_tool import Catalog' phase-a/govlessctl/methods.py || \
    fail "methods.py must read the catalog through catalog_tool.Catalog (mtime cache, 14 MB file)"
grep -q 'def get_by_key' phase-a/govlessctl/catalog_tool.py || fail "catalog_tool.Catalog.get_by_key missing (bot callback keys)"
# 6. bot: handler registered BEFORE the confirm catch-all router, commands + menu buttons present.
grep -q 'from handlers import site as h_site' phase-a/bot/bot.py && grep -q 'dp.include_router(h_site.router)' phase-a/bot/bot.py || \
    fail "bot.py must register handlers/site.py"
awk '/include_router\(h_site.router\)/{s=NR} /include_router\(h_confirm.router\)/{c=NR} END{exit !(s && c && s < c)}' phase-a/bot/bot.py || \
    fail "site router must be included BEFORE the confirm catch-all router (else /site is swallowed)"
grep -q 'callback_data="m:site"' phase-a/bot/handlers/menu.py || fail "bot menu must link to the cover-site screen (m:site)"
grep -q 'command="site"' phase-a/bot/bot.py || fail "/site command must be registered in set_my_commands"
grep -q '/site \[word\]' phase-a/bot/handlers/start.py || fail "/help must document /site"
# 7. Mini App: route + screen + home tile, and app.js rebuilt from app.jsx (STR parity is checked above).
grep -q 'hash.startsWith("#/site")' phase-a/webapp/dist/app.jsx || fail "Mini App must route #/site"
grep -q 'function SiteTemplateScreen' phase-a/webapp/dist/app.jsx || fail "Mini App SiteTemplateScreen missing in app.jsx"
grep -q 'function SiteTemplateScreen' phase-a/webapp/dist/app.js || \
    fail "app.js not rebuilt after app.jsx change (NODE_PATH=<babel> node tools/build-webapp.js)"
grep -q '\[L.site_template, "#/site"\]' phase-a/webapp/dist/app.jsx || fail "Mini App home must show the cover-site tile"
# 8. phase-a installer syncs the catalog for the daemon (the bot may run before the SSH menu ever does).
grep -q 'templates_catalog.json' phase-a/systemd/install/install_phase_a.sh || \
    fail "install_phase_a.sh must sync templates_catalog.json to /opt/govless"
pass "cover-site template switch: SSH menu + bot + Mini App on one backend (33k verified catalog)"

# QA 1.4.0 BUG-02: an update (maybe_sync_version -> install_phase_a.sh) redeploys the
# Python code but `systemctl start` is a no-op on a running unit, so the daemon and
# the bot kept running the OLD code (no site.template_* RPC, no «Cover site» button)
# until a manual restart. The installer must restart units that are already running.
grep -q 'systemctl try-restart "\$_u"' phase-a/systemd/install/install_phase_a.sh || \
    fail "install_phase_a.sh must try-restart running govlessctl/govless-bot after redeploying code (update path)"
grep -q '_apt_lock_wait()' phase-a/systemd/install/install_phase_a.sh || \
    fail "install_phase_a.sh must wait for the apt lock (fresh-server unattended-upgrades, BUG-01)"
pass "phase-a installer restarts running services after redeploy and waits for the apt lock"

# QA 1.4.0 follow-ups from the live run (journal BUG-03..07 + P3s).
grep -q 'TEMPLATES_ZERO_IS_BACK=1 interactive_template_selection' lib/templates_catalog.sh || \
    fail "post-install catalog picker must treat 0 as Back, not silently deploy the stub (QA BUG-04)"
grep -q 'template_dir/.govless_template_source' lib/website.sh || \
    fail "deploy_template_to_nginx must keep the custom-git source when restoring a backup (QA BUG-05)"
grep -q 'TEMPLATES_CACHE/.git_err' lib/templates_catalog.sh || \
    fail "git error file must live inside the download cache (QA BUG-03: /tmp/govless_git_err leak)"
grep -q 'TEMPLATE_DL_ERROR="timeout"' lib/templates_catalog.sh && grep -q 'I18N\[custom_git_timeout\]' lib/lang/ru.sh && grep -q 'I18N\[custom_git_timeout\]' lib/lang/en.sh || \
    fail "custom git clone timeout must be reported as a timeout, not download_failed (QA BUG-07)"
grep -q '"GOVLESS_LANG": job.get("lang")' phase-a/govlessctl/methods.py && grep -q 'GOVLESS_LANG' lib/templates_catalog.sh || \
    fail "daemon must pass the client language to the installer (QA BUG-06 mixed RU/EN)"
! grep -q 'chmod -R 755 "\$WEBSITE_ROOT"' lib/website.sh || fail "web root files must not be chmod 755 (QA B13)"
pass "QA follow-ups: catalog Back, restore keeps source, git-err in cache, clone timeout, client language, file perms"

# QA 1.4.0 second wave (report 098).
# BUG-13 [P1]: a third-party template with `passwd.html -> /etc/passwd` served
# system files and nginx logs over HTTPS. Strip symlinks on download AND deploy,
# and make nginx refuse to follow any that slip through.
grep -q 'find "$root" -type l -delete' lib/website.sh || \
    fail "strip_template_service_files must delete symlinks from the web root (QA BUG-13)"
grep -c 'type l -delete' lib/templates_catalog.sh | grep -qE '^[2-9]' || \
    fail "download_template/custom git must drop symlinks before verification (QA BUG-13)"
[ "$(grep -c 'disable_symlinks on from=\$document_root' lib/website.sh)" -ge 2 ] || \
    fail "both nginx configs (pro+lite) must set disable_symlinks (QA BUG-13)"
# BUG-14 [P2]: the Pro vhost is rewritten on every install/mode switch and used
# to drop the Mini App include, leaving /app/ and /api/rpc at 404.
grep -q 'WEBAPP_INCLUDE_PLACEHOLDER' lib/website.sh && \
    grep -q 'snippets/govless-webapp.conf' lib/website.sh || \
    fail "generate_nginx_pro_config must re-add the govless-webapp.conf include (QA BUG-14)"
# BUG-11 [P2]: Pro->Lite changes the Mini App URL without touching tunnel.url*,
# so an mtime-only watcher kept a dead Pro URL on the menu button.
grep -q 'url_now == last_url' phase-a/bot/handlers/webapp.py || \
    fail "tunnel_watcher must compare the effective URL, not only file mtimes (QA BUG-11)"
pass "QA wave 2: symlink hardening, webapp include survives rewrite, menu button follows mode switch"

# i18n 2026-09-09: lib/egress.sh («Прокси для API Telegram») printed 100%
# hardcoded Russian — an English user got a fully Russian screen (menu, statuses,
# errors, the htop-style dashboard). Every user-facing string now comes from
# I18N. Pin both halves: no Russian may creep back into the module, and the
# egr_* key set must stay symmetric across ru/en (a one-sided key renders as its
# own name in the other language).
# NOTE: the Cyrillic scan runs in python3 on purpose — `grep '[А-Яа-я]'` under
# LC_ALL=C matches the bytes of —, ↑, ✓, · too and reports phantom hits.
_egr_ru_hits=$(python3 - <<'PY'
import io, re
n = 0
for line in io.open("lib/egress.sh", encoding="utf-8"):
    if line.lstrip().startswith("#"):
        continue
    if re.search(r'(echo|printf|log_[a-z]+)', line) and re.search(r'[А-Яа-яЁё]{4,}', line):
        n += 1
print(n)
PY
)
[ "${_egr_ru_hits:-1}" -eq 0 ] || \
    fail "lib/egress.sh has ${_egr_ru_hits} hardcoded Russian user string(s) — route them through \$(t …)/\$(tf …)"
_egr_ru_keys=$(grep -cE '^I18N\[egr_' lib/lang/ru.sh || true)
_egr_en_keys=$(grep -cE '^I18N\[egr_' lib/lang/en.sh || true)
[ "${_egr_ru_keys:-0}" -eq "${_egr_en_keys:-0}" ] && [ "${_egr_ru_keys:-0}" -ge 85 ] || \
    fail "egr_* keys out of sync (ru=${_egr_ru_keys} en=${_egr_en_keys}) — every egress key must exist in BOTH languages"
grep -qF '$(t egr_title)' lib/egress.sh && grep -qF '$(t egr_add)' lib/egress.sh || \
    fail "egress dashboard title / F-key bar must render from I18N, not literals"
pass "lib/egress.sh fully i18n (no hardcoded Russian) + egr_* keys symmetric in ru/en"
# QA 1.4.0 migration run (report 099).
# BUG-19 [P0]: config_get ALWAYS exits 0, so `$(config_get mask_domain || echo ...)`
# never fell back; Pro->Lite built dest ":443" and xray OOM-killed the box.
grep -q 'config_get mask_domain ""' lib/mode_switch.sh || \
    fail "mode switch must pass a default to config_get, not rely on || (QA BUG-19)"
grep -q 'REALITY_FALLBACK_DOMAIN' lib/reality_domains.sh && grep -q 'REALITY_FALLBACK_DOMAIN' lib/mode_switch.sh || \
    fail "a fallback masking domain must exist and be used (QA BUG-19)"
grep -q 'api_bad_mask_domain' lib/xui_api.sh || \
    fail "api_create_reality_inbound must refuse an empty/invalid masking domain (QA BUG-19)"
grep -q 'mask_domain.startswith("www.")' lib/xui_api.sh || \
    fail "serverNames must not produce www.www.<domain> (QA BUG-23)"
# BUG-15 [P1]: the live state.db lives in /var/lib/govless since Rule 3a.
grep -q 'rm -rf /var/lib/govless' lib/xui.sh || \
    fail "remove_everything must delete /var/lib/govless (admin_claimed survived a full wipe — QA BUG-15)"
grep -q '/var/lib/govless/state.db' lib/xui.sh || \
    fail "backup must archive the live state.db, not only the legacy one (QA BUG-15)"
# BUG-16 [P2]: single-branch clone + `fetch origin <branch>` never created the
# remote ref, so the installer silently kept running the old branch.
grep -q '+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}' bootstrap.sh || \
    fail "bootstrap must fetch an explicit refspec so branch switching works (QA BUG-16)"
grep -qE 'Could not switch (to \$BRANCH|\$INSTALL_DIR to branch)' bootstrap.sh || \
    fail "bootstrap must not print success when the branch did not change (QA BUG-16)"
# BUG-17 [P2]: updates never regenerated the Pro vhost, so later hardening
# (disable_symlinks, Mini App include) never reached updated servers.
grep -q 'version_update_nginx' govless.sh || \
    fail "maybe_sync_version must regenerate the Pro nginx config on update (QA BUG-17)"
# BUG-18 [P1]: mode switch rewrote nginx before creating the inbound and rolled
# back only the inbound.
grep -q 'nginx_snapshot' lib/mode_switch.sh || \
    fail "switch_mode must roll the nginx vhost back when the new inbound fails (QA BUG-18)"
# BUG-20 [P2]: deleting "only the site" removed the certificate while leaving a
# foreign web root in place, and still reported success.
grep -q 'remove_site_cert_kept' lib/xui.sh && grep -q 'remove_site_partial' lib/xui.sh || \
    fail "remove_site_only must keep the cert and report honestly when the web root is not ours (QA BUG-20)"
pass "QA migration wave: reality fallback domain, state.db wipe, branch switching, vhost refresh, atomic mode switch"

# Panel-access screen (Mini App / bot): the password field used to print the
# bcrypt hash straight from x-ui.db, the port fell back to a hardcoded 2053
# while the URL line showed the real random port, and an absent panel.access
# marker was read as "127.0.0.1" so a world-open panel was badged SSH-only.
grep -q 'def _panel_credentials' phase-a/govlessctl/methods.py || \
    fail "panel.access_get must read the plaintext password from credentials, not x-ui.db"
grep -q 'BCRYPT_RE' phase-a/govlessctl/methods.py || \
    fail "panel.access_get must never return a bcrypt hash as the password"
grep -q '_xui_web_settings' phase-a/govlessctl/methods.py || \
    fail "panel.access_get must take listen/port from x-ui.db (real values, not 2053)"
grep -q 'password_available' phase-a/govlessctl/methods.py || \
    fail "panel.access_get must tell clients whether a usable password exists"
grep -q 'pw_unavailable' phase-a/webapp/dist/app.jsx && grep -q 'pw_unavailable' phase-a/webapp/dist/app.js || \
    fail "Mini App must explain where to get the password instead of showing nothing (rebuild app.js)"
pass "panel access screen: real password or an honest hint, real port, real listen mode"

# QA truthfulness audit (report 100) — P1 batch.
# BUG-26/27: five notification switches were wired to nothing — the prefs file
# had no reader and the notify queue had no consumer, yet notify-admins replied
# {"sent": true}. BUG-28: "Rotate credentials" called a method that did not exist.
grep -q 'async def notify_watcher' phase-a/bot/handlers/webapp.py || \
    fail "bot must drain /run/govless/notify.queue and deliver notifications (QA BUG-27)"
grep -q 'notify_watcher(bot)' phase-a/bot/bot.py || \
    fail "notify_watcher must be started alongside tunnel_watcher (QA BUG-27)"
grep -q '_wants_event' phase-a/bot/handlers/webapp.py || \
    fail "delivery must honour each admin's notification switches (QA BUG-26)"
grep -q 'NOTIFY_EVENTS' phase-a/govlessctl/methods.py || \
    fail "daemon must publish the list of events it can actually raise (QA BUG-26)"
! grep -q 'traffic_limit_reached' phase-a/govlessctl/methods.py || \
    fail "traffic_limit_reached has no producer — it must not be offered as a switch"
grep -q '"panel.creds_rotate": self.panel_creds_rotate' phase-a/govlessctl/methods.py || \
    fail "panel.creds_rotate must exist — the Mini App button calls it (QA BUG-28)"
grep -q 'ROTATE PANEL' phase-a/govlessctl/methods.py || \
    fail "credential rotation must require a typed confirmation"
# BUG-32: an RPC hiccup was reported to admins as "tunnel cleared" and stripped
# the Open app button on a healthy box.
grep -q 'UNKNOWN_URL' phase-a/bot/handlers/webapp.py || \
    fail "the bot must tell 'daemon unreachable' apart from 'no tunnel' (QA BUG-32)"
pass "notifications actually deliver, credential rotation exists, RPC errors are not 'tunnel gone'"

# QA truthfulness audit (report 100) — P2 batch.
grep -q 'const online = cs.filter(c=>c.enabled && c.online).length' phase-a/webapp/dist/app.jsx || \
    fail "Home must count clients that are ONLINE under the 'clients online' label (QA BUG-24)"
grep -q '"x-ui", "-v"' phase-a/govlessctl/methods.py || \
    fail "panel version must come from x-ui itself, not the first vN.N string in the binary (QA BUG-25)"
! grep -q 'cfg.get("transport", "reality")' phase-a/govlessctl/methods.py || \
    fail "'reality' is not a transport — do not invent it as a default (QA BUG-34)"
grep -q '\[ "$mode" = "lite" \] && echo -e "  $(t config_mask)' govless.sh || \
    fail "the dashboard must not show a Reality masking domain in Pro (QA BUG-29)"
grep -q 'https://\${domain}/' lib/templates_catalog.sh || \
    fail "site check must hit the real public HTTPS, not a loopback with a fake Host (QA BUG-30)"
! grep -q '"GPL-3.0"' phase-a/webapp/dist/app.jsx || \
    fail "About must not claim GPL-3.0 — goVLESS is Source-Available (QA BUG-31)"
pass "truthful counters, versions, transport, masking line, site check and licence"

# QA truthfulness audit (report 100) — P3 batch.
grep -q '"B","KiB","MiB","GiB","TiB"' phase-a/webapp/dist/app.jsx || \
    fail "1024-based sizes must be labelled KiB/MiB/GiB (QA BUG-35)"
grep -q 'kind={info.mode==="ssh-only"?"ok":"warn"}' phase-a/webapp/dist/app.jsx || \
    fail "a publicly reachable panel must not get a green OK badge (QA BUG-36)"
grep -q '"realitySettings": reality_public or None' phase-a/govlessctl/methods.py || \
    fail "inbound.list must return the stream so the Reality block can render (QA BUG-37)"
! grep -q 'privateKey' phase-a/webapp/dist/app.jsx || \
    fail "the Reality private key must never be shipped to the Mini App"
grep -q 'unit == "gib"' phase-a/govlessctl/methods.py || \
    fail "traffic limits must take an explicit unit, not guess by magnitude (QA BUG-40)"
grep -q 'dashboard_xui_ver\]="Версия 3X-UI:"' lib/lang/ru.sh || \
    fail "the dashboard version line must say which product it belongs to (QA BUG-39)"
pass "units, badges, Reality block and traffic limits say what they mean"

# ── Loader must actually switch branches, and never claim it did when it did not.
# goInstaller moves an installed goVLESS between main and dev by re-running
# bootstrap.sh with GOVLESS_BRANCH. The clone is single-branch (`clone -b X
# --depth 1`), so `git fetch origin dev` wrote FETCH_HEAD and never created
# origin/dev; both checkouts failed, `|| true` swallowed it, and the loader
# printed "✓ Updated to latest dev" over a tree still on main. goInstaller reads
# that clone's HEAD, so its UI repeated the lie.
[ "$(grep -cF 'remote.origin.fetch "$ALL_BRANCHES_REFSPEC"' bootstrap.sh)" -ge 2 ] || \
    fail "bootstrap.sh must widen remote.origin.fetch on BOTH the clone and the update path (single-branch clone can't see other branches)"
grep -qF "ALL_BRANCHES_REFSPEC='+refs/heads/*:refs/remotes/origin/*'" bootstrap.sh || \
    fail "bootstrap.sh must set the all-branches refspec (+refs/heads/*:refs/remotes/origin/*)"
grep -qF '"+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}"' bootstrap.sh || \
    fail "bootstrap.sh must fetch into an explicit refs/remotes/origin/<branch>, not a bare branch name"
grep -qF 'git checkout -q -f -B "$BRANCH" "origin/$BRANCH"' bootstrap.sh || \
    fail "bootstrap.sh must switch with a single 'checkout -f -B <branch> origin/<branch>'"
if grep -qF 'git checkout -f "$BRANCH" --quiet' bootstrap.sh; then
    fail "bootstrap.sh still uses the old silent checkout chain (fails on a single-branch clone, hidden by || true)"
fi
_guard_ln="$(grep -n 'NOW_BRANCH' bootstrap.sh | head -1 | cut -d: -f1)"
_claim_ln="$(grep -nF 'Updated to latest $BRANCH' bootstrap.sh | head -1 | cut -d: -f1)"
[ -n "$_guard_ln" ] && [ -n "$_claim_ln" ] && [ "$_guard_ln" -lt "$_claim_ln" ] || \
    fail "bootstrap.sh must verify HEAD == origin/<branch> BEFORE printing 'Updated to latest'"
grep -qF "Asked for branch '\$BRANCH', but the repo is unreachable." bootstrap.sh || \
    fail "bootstrap.sh must refuse to run when the repo is unreachable AND a different branch was requested"
grep -qF 'NO update was applied' bootstrap.sh || \
    fail "bootstrap.sh must say plainly that no update happened when it falls back to the local copy"
pass "bootstrap.sh switches branches for real and verifies before claiming success"

# ── A clone of ANOTHER repository is replaced, never "updated" ──
# The free goVLESS Lite (public guide) clones potap1978/go-all-lite-version into
# /opt/govless-installer. The loader fetched Lite into it, printed "Updated to
# latest main" and started Lite: goInstaller's "install goVLESS pro" opened the
# Lite menu. The fresh clone lands next to the target and is swapped in only
# when complete, so a failed clone leaves the old installer (and `govless`) intact.
grep -q '^repo_id()' bootstrap.sh || fail "bootstrap.sh must compare the clone's origin with \$REPO before updating it (Lite takeover)"
grep -qF '[ -d "$INSTALL_DIR/.git" ] && [ -z "$FOREIGN_ORIGIN" ]' bootstrap.sh || \
    fail "bootstrap.sh must not take the update path for a clone of another repository"
grep -qF 'NEW_DIR="${INSTALL_DIR}.new.$$"' bootstrap.sh || \
    fail "bootstrap.sh must clone next to the install dir and swap only on success"
_clone_ln="$(grep -nF 'clone --progress -b "$BRANCH" --depth 1 "$REPO_CANONICAL" "$NEW_DIR"' bootstrap.sh | head -1 | cut -d: -f1)"
_rm_ln="$(grep -nF 'rm -rf "$INSTALL_DIR"' bootstrap.sh | head -1 | cut -d: -f1)"
[ -n "$_clone_ln" ] && [ -n "$_rm_ln" ] && [ "$_clone_ln" -lt "$_rm_ln" ] || \
    fail "bootstrap.sh must not remove the install dir before the new clone succeeded"
pass "bootstrap.sh replaces a foreign (Lite) clone instead of updating it, and never loses the old one on failure"

# Same defect class in install.sh (the is.gd/govless entry point): a bare
# `checkout || true` followed by `reset --hard origin/$BRANCH` reset WHATEVER
# branch was checked out onto the requested commit — HEAD then named the old
# branch while the tree held the new branch's code.
grep -qF 'git checkout -q -f -B "$BRANCH" "origin/$BRANCH"' install.sh || \
    fail "install.sh must switch with 'checkout -f -B <branch> origin/<branch>'"
if grep -qF 'git reset --hard "origin/$BRANCH" --quiet' install.sh; then
    fail "install.sh still resets the CURRENT branch onto origin/\$BRANCH (HEAD then lies about which branch the code is from)"
fi
grep -qF "remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'" install.sh || \
    fail "install.sh must widen remote.origin.fetch too"
pass "install.sh switches branches for real (no reset-onto-wrong-branch)"

# `test -r /dev/tty` succeeds on a device node the process cannot open: with no
# controlling terminal (systemd, cron, ssh without -t) the exec died with
# "/dev/tty: No such device or address" right after printing "✓ Ready".
# Comment lines stripped first: the comment explaining the fix names the old
# expression, and a naive grep would match its own explanation.
if grep -vE '^[[:space:]]*#' bootstrap.sh | grep -qF '[ -r /dev/tty ]'; then
    fail "bootstrap.sh must OPEN /dev/tty to test it, not use 'test -r' (ENXIO kills the exec with no controlling terminal)"
fi
grep -qF '(exec 3</dev/tty) 2>/dev/null' bootstrap.sh || \
    fail "bootstrap.sh must probe /dev/tty by opening it"
pass "bootstrap.sh survives having no controlling terminal"

# Sync-only mode: goInstaller (and anyone verifying a branch move on a live
# server) must be able to bring the checkout to a branch and read HEAD +
# GOVLESS_VERSION without an install starting underneath them.
grep -qF 'GOVLESS_BOOTSTRAP_ONLY:-0' bootstrap.sh || \
    fail "bootstrap.sh must support GOVLESS_BOOTSTRAP_ONLY=1 (sync the checkout, verify, exit without installing)"
pass "bootstrap.sh has a sync-only mode (no install kicked off when only the branch is wanted)"

# End-to-end: drive the real bootstrap.sh through install / main->dev / dev->main
# / unreachable / missing branch against a throwaway file:// repo. bootstrap.sh
# refuses to run as non-root, so this half only runs when we are.
if [ "$(id -u)" = "0" ] && command -v git >/dev/null 2>&1; then
    bash tools/bootstrap_branch_switch_test.sh >/dev/null 2>&1 || \
        fail "branch switching is broken — run 'bash tools/bootstrap_branch_switch_test.sh' for the detail"
    pass "bootstrap branch switching works end-to-end (install, main<->dev, unreachable, missing branch)"
else
    printf '[SKIP] bootstrap branch-switch end-to-end (needs root; source pins above still enforced)\n'
fi

# ── Ядро AmneziaWG должно браться с диска, а не из приватного репозитория ───
# egress_ensure_awg ходил за amneziawg-go/awg на raw.githubusercontent в
# ПРИВАТНЫЙ potap1978/go-all. Без GH_TOKEN в окружении это 404, а в окружении
# его нет ни у кого, кто открыл меню ярлыком govless: токен живёт только внутри
# запуска bootstrap.sh. При этом оба бинарника всё время лежали в bin/ того же
# клона. Пользователи упирались в «Задайте GitHub PAT (меню 8 → 6)» — пункта с
# таким номером не существует, и ни одна строка кода не читает сохранённый PAT.
grep -qF 'egress_awg_copy_from_checkout()' lib/egress.sh || \
    fail "lib/egress.sh must have egress_awg_copy_from_checkout (ядро берётся из bin/ локального клона)"
_copy_ln="$(grep -n 'egress_awg_copy_from_checkout "\$arch"' lib/egress.sh | head -1 | cut -d: -f1)"
_curl_ln="$(grep -n 'raw.githubusercontent.com/\${AWG_REPO}' lib/egress.sh | head -1 | cut -d: -f1)"
[ -n "$_copy_ln" ] && [ -n "$_curl_ln" ] && [ "$_copy_ln" -lt "$_curl_ln" ] || \
    fail "egress_ensure_awg must try the local checkout BEFORE the network (приватный репозиторий = 404 без токена)"
if grep -rh --include='*.sh' -v '^[[:space:]]*#' lib/ | grep -qF 'меню 8 → 6'; then
    fail "подсказка зовёт в «меню 8 → 6» — такого пункта нет, и сохранённый PAT никто не читает"
fi
for _b in bin/amneziawg-go-linux-amd64 bin/awg-linux-amd64; do
    [ -s "$_b" ] || fail "$_b отсутствует — ядро AmneziaWG неоткуда взять локально"
done
pass "ядро AmneziaWG берётся из локального клона, подсказка не врёт"

bash tools/awg_core_selftest.sh >/dev/null 2>&1 || \
    fail "ядро AmneziaWG не готовится с диска — запусти 'bash tools/awg_core_selftest.sh'"
pass "awg_core_selftest: ядро готовится без сети и без токена"

# ── «Удалить ВСЁ» must also take down the Telegram-API egress ─────────────
# remove_everything stopped only the units in its own list; govless-egress,
# govless-awg@kawgN, govless-awg-reconcile.timer and govless-egress-probe.timer
# survived the wipe (xray kept listening on 10808, kawg1 kept the tunnel) and
# failed at the next boot on binaries that were already deleted.
awk '/^remove_everything\(\)/,/^}/' lib/xui.sh | grep -c 'egress_disable' >/dev/null || \
    fail "remove_everything must call egress_disable (govless-egress + kawg legs + probe survive «Удалить ВСЁ»)"
awk '/^remove_everything\(\)/,/^}/' lib/xui.sh | grep -c 'govless-awg-reconcile.timer' >/dev/null || \
    fail "remove_everything must disable govless-awg-reconcile.timer and drop the govless-awg@ unit files"
pass "«Удалить ВСЁ» tears down the Telegram-API egress (xray, kawg legs, timers)"

# ── First-admin claim must work when bot.env already existed ───────────────
# bot.env is created before the bot by the Telegram-API proxy (BOT_PROXY) and by
# the token prompt (BOT_TOKEN). install_govless_user.sh generated
# ADMIN_CLAIM_TOKEN only for a brand-new file, so «/admin <ADMIN_CLAIM_TOKEN>»
# — the path printed by the installer and by the bot itself — failed with
# "ADMIN_CLAIM_TOKEN not configured" on exactly those installs.
grep -q 'added missing' phase-a/systemd/install/install_govless_user.sh || \
    fail "install_govless_user.sh must add ADMIN_CLAIM_TOKEN & co to a pre-existing bot.env (first-admin claim was a dead end)"
_tmpenv="$(mktemp)"; printf 'BOT_PROXY=socks5://127.0.0.1:10808\n' > "$_tmpenv"
_out="$(BOT_ENV="$_tmpenv" GROUP_NAME="$(id -gn)" bash -c '
    _added=""; _has_key() { grep -qE "^$1=." "$BOT_ENV"; }
    if ! _has_key ADMIN_CLAIM_TOKEN; then printf "ADMIN_CLAIM_TOKEN=%s\n" "$(openssl rand -hex 16)" >>"$BOT_ENV"; _added="$_added ADMIN_CLAIM_TOKEN"; fi
    echo "$_added"' 2>/dev/null)"
grep -qE '^ADMIN_CLAIM_TOKEN=[0-9a-f]{32}$' "$_tmpenv" && grep -q '^BOT_PROXY=socks5' "$_tmpenv" || \
    fail "ensure-keys logic must append ADMIN_CLAIM_TOKEN without touching existing lines"
rm -f "$_tmpenv"
pass "pre-existing bot.env gets ADMIN_CLAIM_TOKEN (first-admin claim path alive)"

# ── Pro + XHTTP/gRPC silently loses the cover site ──────────────────────────
# The VLESS inbound serves the site through `fallbacks: [{dest: 80}]`, and Xray
# honours fallbacks only on the TCP transport. Picking XHTTP/gRPC in Pro gave a
# working panel and VPN with an https://domain that never opens — the exact
# «panel works, site does not» complaint. Pro now warns and asks before non-TCP.
grep -q 'select_transport pro' govless.sh || fail "install_pro must call select_transport with the pro hint"
awk '/^select_transport\(\)/,/^}/' lib/xui.sh | grep -c 'transport_pro_confirm' >/dev/null || \
    fail "select_transport must ask for confirmation before XHTTP/gRPC in Pro (cover site needs TCP fallback)"
for _l in ru en; do grep -q 'I18N\[transport_pro_warn\]' "lib/lang/${_l}.sh" && grep -q 'I18N\[transport_pro_confirm\]' "lib/lang/${_l}.sh" || \
    fail "transport_pro_warn/confirm missing in lib/lang/${_l}.sh"; done
pass "Pro warns that XHTTP/gRPC kill the https cover site and asks before continuing"

# ── «Установить / Обновить» on an installed server must not switch modes ────
# select_and_install showed the Lite/Pro picker regardless of config.mode, so
# «1 → 1 → 3» on a Pro server overwrote the cover site with the stub, rewrote
# the Pro vhost (ACME location + Mini App include gone) and set mode=lite while
# the 443 inbound stayed TLS (QA U-01). Now an installed mode is reinstalled
# only as itself; switching is VPN → 4, code updates are goinstall.
awk '/^select_and_install\(\)/,/^}/' govless.sh | grep -c 'reinstall_confirm' >/dev/null || \
    fail "select_and_install must refuse to change the installed mode (reinstall only as the current mode)"
for _l in ru en; do for _k in reinstall_title reinstall_current reinstall_hint_switch reinstall_hint_update reinstall_confirm; do
    grep -q "I18N\[${_k}\]" "lib/lang/${_l}.sh" || fail "${_k} missing in lib/lang/${_l}.sh"; done; done
pass "reinstall keeps the installed mode; mode switch and code update are pointed to their real menus"

# ── Bot wizard: never claim success without an admin; show the claim command ─
# The wizard printed «Бот настроен ✓» with an empty admin list, and the bot then
# demanded «/admin <ADMIN_CLAIM_TOKEN>» — a secret nobody had displayed (QA U-06).
# «Установить бота» must continue into the wizard instead of an English NEXT
# STEP block that tells people to edit bot.env by hand (QA U-14).
awk '/^bot_setup_wizard\(\)/,/^}/' govless.sh | grep -c 'bot_wiz_no_admin' >/dev/null || \
    fail "bot_setup_wizard must print the /admin <token> command when no admin was bound"
grep -q 'bot_install_phasea; _bot_installed && bot_setup_wizard' govless.sh || \
    fail "submenu_bot item 1 must run the wizard right after installing phase-a"
for _l in ru en; do grep -q 'I18N\[bot_wiz_no_admin\]' "lib/lang/${_l}.sh" || fail "bot_wiz_no_admin missing in ${_l}"; done
pass "bot wizard is honest about a missing admin and installs → wizard in one go"

# ── Bot wizard must reach api.telegram.org through the Telegram-API proxy ─────
# On RU hosting api.telegram.org is unreachable directly; the bot itself uses
# BOT_PROXY, but the wizard's getMe/getUpdates went direct and looped on
# «Telegram не подтвердил токен» (QA U-05).
grep -q '^_tg_curl_args()' govless.sh || fail "govless.sh must derive curl proxy args from BOT_PROXY (_tg_curl_args)"
awk '/^_bot_getme\(\)/,/^}/' govless.sh | grep -c '_tg_curl_args' >/dev/null || fail "_bot_getme must honour BOT_PROXY"
awk '/^_bot_detect_admin\(\)/,/^}/' govless.sh | grep -c '_tg_curl_args' >/dev/null || fail "_bot_detect_admin must honour BOT_PROXY"
if awk '/^_bot_detect_admin\(\)/,/^}/' govless.sh | grep -c 'urllib.request.urlopen' >/dev/null; then
    fail "_bot_detect_admin still uses urllib (cannot use the SOCKS5 egress)"; fi
pass "bot wizard talks to Telegram through BOT_PROXY when the egress is configured"

# ── Sysadmin-critic P0/P1 fixes (2026-09-22) ─────────────────────────────────
grep -q 'payload\["caller"\] = "webapp"' phase-a/webapp/server.py || \
    fail "Lite relay must force caller=webapp (client-chosen caller:local gave full RPC through the tunnel — S-01)"
grep -q '_RL_BUCKETS_LOCAL' phase-a/govlessctl/daemon.py || \
    fail "rate limit must keep local callers apart from internet traffic (one bucket = DoS of the bot — S-02)"
grep -q -- '--egress-probe)    trap - EXIT' govless.sh || \
    fail "systemd entry modes must drop the EXIT cleanup trap (probe timer killed panel sessions — S-03)"
grep -q "INSERT INTO settings (key, value) VALUES ('webListen'" phase-a/govlessctl/methods.py || \
    fail "panel.access_set must INSERT webListen (UPDATE on a fresh install matched nothing — S-07)"
grep -qE 'self\.xui\.username(, self\.xui\.password)? = new_user' phase-a/govlessctl/methods.py || \
    fail "panel.creds_rotate must refresh the live XuiClient (write RPCs died until restart — S-05)"
grep -q '_local_panel_url_from_db() or creds_url' phase-a/govlessctl/xui_client.py || \
    fail "XuiClient must prefer the loopback panel URL (public URL breaks SSH-only/NAT/self-signed — S-06)"
grep -q 'listen \\\[::\\\]:80;/d' lib/website.sh || \
    fail "nginx configs must drop [::]:80 when the kernel has no IPv6 (S-16)"
grep -q 'if \[ "\$_phasea_ok" = 1 \]; then config_set "version"' govless.sh || \
    fail "maybe_sync_version must record the version only after phase-a redeploy succeeded (S-15)"
pass "sysadmin-critic P0/P1: relay caller, rate-limit scoping, trap-free probes, webListen insert, live creds, loopback panel URL, IPv6 listen, version-after-redeploy"

# ── User-critic P2 follow-ups (2026-09-22) ──────────────────────────────────
awk '/^install_pro\(\)/,/^}/' govless.sh | grep -c 'pro_domain_retry' >/dev/null || fail "install_pro must re-ask a bad domain instead of aborting the installer (U-03)"
awk '/^install_pro\(\)/,/^}/' govless.sh | grep -c 'pro_bad_email' >/dev/null || fail "install_pro must validate the LE email (U-04)"
grep -q 'govless_certbot.log' lib/website.sh || fail "certbot output must be kept and shown on failure (U-04)"
awk '/^submenu_manage\(\)/,/^}/' govless.sh | grep -c 'show_credentials' >/dev/null || fail "Manage menu must be able to show the panel credentials again (U-09)"
grep -q 'I18N\[dns_menu_exit\]="Отменить установку' lib/lang/ru.sh || fail "DNS menu must not promise a main menu on a first install (U-10)"
for _l in ru en; do for _k in pro_domain_retry pro_bad_email manage_show_creds; do grep -q "I18N\[${_k}\]" "lib/lang/${_l}.sh" || fail "${_k} missing in ${_l}"; done; done
pass "domain/email prompts recover, certbot errors visible, panel creds re-viewable, DNS menu honest"

grep -q 'forced renewal refused' phase-a/govlessctl/methods.py || \
    fail "cert.force_renew must refuse while the certificate is fresh (LE duplicate limit — S-24)"
awk '/awg-cfg.sh" <<CFG/,/^CFG$/' lib/egress.sh | grep -c 'rp_filter=2' >/dev/null || fail "awg-cfg.sh must relax rp_filter on the kawg leg (S-21)"
grep -q 'GIT_CONFIG_KEY_0' bootstrap.sh || fail "bootstrap must pass the token via GIT_CONFIG_* env, not argv (S-22)"
pass "forced renewal guarded, rp_filter relaxed on kawg, git token out of argv"

# ── panel.creds_rotate must use the real 3X-UI binary and prove the new pair ──
# shutil.which("x-ui") is /usr/bin/x-ui — the management shell script, which
# ignores «setting -username -password» and exits 0. The daemon then showed new
# credentials that never reached the panel (operator locked out, write RPCs
# «login failed»). Found live on ilovelife.site, 2026-09-22.
if grep -q 'xui_bin = shutil.which("x-ui")' phase-a/govlessctl/methods.py; then
    fail "panel.creds_rotate must not resolve x-ui via shutil.which (that is the shell wrapper)"; fi
grep -q '_xui_real_binary()' phase-a/govlessctl/methods.py || fail "panel.creds_rotate must call the real 3X-UI binary"
grep -q 'the previous ones were restored' phase-a/govlessctl/methods.py || \
    fail "panel.creds_rotate must verify the new login and roll back on failure"
pass "credential rotation talks to the real 3X-UI binary and verifies before persisting"

# ── apt fallback must keep signatures; wizard must confirm the auto-detected admin ──
if grep -q 'APT::Get::AllowUnauthenticated=true' lib/common.sh; then
    fail "apt_install fallback must not disable signature checks for every repository (S-08)"; fi
awk '/^bot_setup_wizard\(\)/,/^}/' govless.sh | grep -c 'bot_wiz_confirm_admin' >/dev/null || \
    fail "bot wizard must confirm the auto-detected admin before granting access (S-17)"
for _l in ru en; do grep -q 'I18N\[bot_wiz_confirm_admin\]' "lib/lang/${_l}.sh" || fail "bot_wiz_confirm_admin missing in ${_l}"; done
pass "apt keeps signatures in the fallback; wizard confirms who becomes admin"

# ── «Удалить ВСЁ» не оставляет живых юнитов (стенд, 23.09.2026) ──────────────
# egress_disable перезапускал бота, а бот через Requires= поднимал govlessctl.
# Шаг шёл ПОСЛЕ остановки сервисов — бот и демон работали дальше от root из
# удалённых файлов («not-found active running»).
_re_body=$(awk '/^remove_everything\(\)/,/^}/' lib/xui.sh)
_re_egress=$(printf '%s\n' "$_re_body" | grep -n 'remove_try "disable Telegram-API egress"' | head -1 | cut -d: -f1)
_re_stop=$(printf '%s\n' "$_re_body" | grep -n 'remove_try "stop \$svc"' | head -1 | cut -d: -f1)
{ [ -n "$_re_egress" ] && [ -n "$_re_stop" ] && [ "$_re_egress" -lt "$_re_stop" ]; } || \
    fail "remove_everything must tear the Telegram-API egress down BEFORE stopping the services"
printf '%s\n' "$_re_body" | grep -c 'stop leftover' >/dev/null || \
    fail "remove_everything must stop units that are still running after their files are gone"
awk '/^egress_disable\(\)/,/^}/' lib/egress.sh | grep -c 'is-enabled --quiet govless-bot' >/dev/null || \
    fail "egress_disable must not start a bot that is not enabled"
pass "«Удалить ВСЁ» leaves no goVLESS unit running"

# ── settings.secret — ключ подписи сессий 3X-UI, а не «токен входа» ─────────
# 2.9.4 и 3.x: cookie.NewStore(secret). Экран данных панели печатал его как
# «Токен … вход по токену / API»: войти с ним нельзя, подделать сессию можно, и
# смена пароля такую сессию не отзывала.
if awk '/^show_credentials\(\)/,/^}/' lib/xui_api.sh | grep -c "key='secret'" >/dev/null; then
    fail "show_credentials must not print settings.secret (the panel's session-signing key)"; fi
awk '/async def panel_creds_rotate/,/async def cert_force_renew/' phase-a/govlessctl/methods.py | \
    grep -c '_rotate_panel_session_key()' >/dev/null || \
    fail "panel.creds_rotate must rotate the session key (open sessions outlived the new password)"
pass "panel session key is never shown and is rotated together with the credentials"

# ── backup / restore / repair из бота и Mini App — с переводами ──────────────
# Демон подключал lib/ без lib/i18n.sh: вместо сообщений — «t: command not found».
grep -q 'source lib/i18n.sh && load_language' phase-a/govlessctl/methods.py || \
    fail "daemon shell calls must load lib/i18n.sh (backup/restore/repair lost every message)"
if grep -q 'source lib/common.sh 2>/dev/null || true; source lib/xui_api.sh' phase-a/govlessctl/methods.py; then
    fail "backup/restore/repair must go through _lib_bash (i18n-less copy left behind)"; fi
pass "daemon backup/restore/repair run lib/ with translations"

# ── Версия с GitHub — разбором JSON, а не построчно (04.10.2026) ────────────
# GitHub API бывает отдаёт JSON одной строкой (Aeza, Debian 12): построчный
# grep '"tag_name"' | cut -d'"' -f4 печатал тогда значение первого ключа ("url").
# Отдельный xray для «Прокси для API Telegram» не скачивался (egress тихо уходил
# на xray панели), а «Pro — авто» обрывалась на скачивании 3X-UI (404).
if grep -rn --include='*.sh' 'tag_name' govless.sh lib/ | grep -vE '^[^:]+:[0-9]+:[[:space:]]*#' | \
    grep -qE "cut -d|grep (-m1 )?'\"tag_name\"'"; then
    fail "tag_name is parsed line-wise again (grep | cut): on one-line JSON that yields the \"url\" value"; fi
bash tools/github_tag_selftest.sh >/dev/null 2>&1 || \
    fail "GitHub version parsing is broken — run 'bash tools/github_tag_selftest.sh'"
pass "версия с GitHub — разбором JSON: xray для egress, 3X-UI в «Pro — авто»"

# ── «Pro — авто»: версия 3X-UI сохраняется в config.json (24.10.2026) ────────
# install_3xui находил версию сам и держал её в локальной переменной, а в конфиг
# писался пустой XUI_INSTALL_VERSION: статус без строки «3X-UI», повторный запуск
# — «new ?». Теперь install_3xui отдаёт версию наружу, а пустое поле у прежних
# установок дописывается из «x-ui -v».
# порядок: xui_resolve_version → XUI_INSTALL_VERSION="$version" → ссылка на архив
awk '/^install_3xui\(\)/,/^}/' lib/xui.sh | awk '
    /xui_resolve_version/                       { r = NR }
    r && !a && /XUI_INSTALL_VERSION="\$version"/ { a = NR }
    /tarball_url=/                              { u = NR }
    END { exit !(r && a && u && r < a && a < u) }' || \
    fail "install_3xui must hand the version it resolved back in XUI_INSTALL_VERSION"
_xv_dir=$(mktemp -d)
_xv_probe() {  # $1 = config.json body, $2 = what `x-ui -v` prints ("" = no binary) → «branch|version|config xui_version»
    rm -f "$_xv_dir/x-ui"
    [ -z "$2" ] || { printf '#!/bin/sh\necho "%s"\n' "$2" > "$_xv_dir/x-ui"; chmod +x "$_xv_dir/x-ui"; }
    printf '%s\n' "$1" > "$_xv_dir/config.json"
    bash -c 'source lib/common.sh >/dev/null 2>&1; source lib/xui.sh >/dev/null 2>&1
        GOVLESS_CONFIG="$1/config.json"; XUI_BIN="$1/x-ui"
        load_xui_version_from_config; printf "%s|%s|" "$XUI_BRANCH" "$XUI_INSTALL_VERSION"
        jq -r ".xui_version // \"\"" "$GOVLESS_CONFIG"' _ "$_xv_dir" 2>/dev/null
}
_xv_out=$(_xv_probe '{"mode":"pro","xui_branch":"new"}' "3.10.2")
[ "$_xv_out" = "new|v3.10.2|v3.10.2" ] || \
    fail "empty xui_version must be filled from the panel binary (got '$_xv_out')"
_xv_out=$(_xv_probe '{"mode":"pro","xui_branch":"new","xui_version":"v3.10.2"}' "3.11.0")
[ "$_xv_out" = "new|v3.11.0|v3.11.0" ] || \
    fail "a stale xui_version (panel updated via its own menu) must give way to the binary (got '$_xv_out')"
_xv_out=$(_xv_probe '{"mode":"pro"}' "2.6.0")
[ "$_xv_out" = "legacy|v2.6.0|v2.6.0" ] || \
    fail "no xui_branch in config + 2.x binary must read as legacy (got '$_xv_out')"
_xv_out=$(_xv_probe '{"mode":"pro","xui_branch":"legacy","xui_version":"v2.9.4"}' "")
[ "$_xv_out" = "legacy|v2.9.4|v2.9.4" ] || \
    fail "no panel binary — xui_version from config must stay (got '$_xv_out')"
rm -rf "$_xv_dir"
grep -q 'xui_ver=$(xui_saved_version)' govless.sh || \
    fail "the status screen must read the 3X-UI version through xui_saved_version"
pass "«Pro — авто»: версия 3X-UI сохраняется; статус берёт её у панели"
