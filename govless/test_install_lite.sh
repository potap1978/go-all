#!/bin/bash
# goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
# Licensed under the goVLESS Source-Available License (see the LICENSE file).
# Redistribution, mirroring, or republishing in any form — whole or partial,
# modified or not — is prohibited without prior written permission.

# Test: Full Lite mode install (non-interactive)
set +e

SCRIPT_DIR="/root/goVLESS"

export GOVLESS_DIR="/etc/govless"
export XUI_DIR="/usr/local/x-ui"
export XUI_BIN="/usr/local/x-ui/x-ui"
export XUI_DB="/usr/local/x-ui/db/x-ui.db"
export XUI_SERVICE="x-ui"
export XRAY_BIN="/usr/local/x-ui/bin/xray-linux-amd64"
export CREDENTIALS_FILE="/root/.govless_credentials"
export NGINX_SITE_CONF="/etc/nginx/sites-available/govless"
export NGINX_SITE_LINK="/etc/nginx/sites-enabled/govless"
export WEBSITE_ROOT="/var/www/govless"

cd "$SCRIPT_DIR" || exit 1
source lib/common.sh
source lib/i18n.sh
source lib/lang/en.sh
source lib/xui.sh
source lib/xui_api.sh
source lib/reality_domains.sh
source lib/website.sh
mkdir -p "$GOVLESS_DIR"

XUI_BRANCH="new"
XUI_INSTALL_VERSION="v3.0.1"
XUI_TRANSPORT="tcp"

echo "=== STEP 1: install_3xui ==="
install_3xui "v3.0.1"
echo "EXIT_CODE=$?"

echo "=== STEP 2: extract_credentials ==="
extract_credentials
echo "USER=$XUI_USER PASS=$XUI_PASS PORT=$XUI_PORT WEBPATH=$XUI_WEB_PATH"

echo "=== STEP 3: systemd status ==="
systemctl status x-ui --no-pager 2>&1 | head -10

echo "=== STEP 4: generate_reality_keypair ==="
generate_reality_keypair
echo "PRIVKEY=${REALITY_PRIVATE_KEY:0:10}..."
echo "PUBKEY=${REALITY_PUBLIC_KEY:0:10}..."

echo "=== STEP 5: test_reality_domain ==="
test_reality_domain "www.google.com" && echo "google.com OK" || echo "google.com FAIL"

echo "=== STEP 6: setup_api_base ==="
setup_api_base
echo "API_BASE=$API_BASE"

echo "=== STEP 6.5: wait_for_api ==="
wait_for_api 30
echo "WAIT_EXIT=$?"

echo "=== STEP 7: api_login ==="
api_login
echo "LOGIN_EXIT=$?"

echo "=== STEP 8: config save ==="
config_set "mode" "lite"
config_set "xui_branch" "$XUI_BRANCH"
config_set "xui_version" "$XUI_INSTALL_VERSION"
config_set "transport" "$XUI_TRANSPORT"
config_set "reality_domain" "www.google.com"

echo "=== STEP 9: generate users ==="
generate_clients 2
echo "USERS_EXIT=$?"
echo "Clients file:"
cat /tmp/govless_clients.json 2>/dev/null | head -5 || echo "NO CLIENTS FILE"

echo "=== STEP 10: create inbound ==="
REALITY_DEST="www.google.com"
REALITY_SNI="www.google.com"
api_create_reality_inbound
echo "INBOUND_EXIT=$?"

echo "=== STEP 11: generate vless links ==="
SERVER_IP=$(get_server_ip)
generate_all_vless_links "lite" "$SERVER_IP" "www.google.com"
echo "LINKS_EXIT=$?"
echo "VLESS_LINKS_JSON:"
cat /tmp/govless_links.json 2>/dev/null | head -10 || echo "NO LINKS JSON"

echo "=== STEP 12: save credentials ==="
save_credentials
echo "CREDS_EXIT=$?"
cat "$CREDENTIALS_FILE" 2>/dev/null

echo "=== ALL STEPS COMPLETE ==="
