#!/usr/bin/env bash
set -euo pipefail

script="${1:-warp.sh}"
failures=0
checks=0
case_dir=""

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    failures=$((failures + 1))
}

pass() {
    checks=$((checks + 1))
}

assert_eq() {
    local expected="$1" actual="$2" message="$3"
    if [ "$expected" != "$actual" ]; then
        fail "$message (expected '$expected', got '$actual')"
    else
        pass
    fi
}

assert_file_contains() {
    local file="$1" pattern="$2" message="$3"
    if ! grep -qE "$pattern" "$file"; then
        fail "$message"
    else
        pass
    fi
}

assert_file_not_contains() {
    local file="$1" pattern="$2" message="$3"
    if grep -qE "$pattern" "$file"; then
        fail "$message"
    else
        pass
    fi
}

expect_success() {
    local message="$1"; shift
    if "$@" >/dev/null 2>&1; then
        pass
    else
        fail "$message"
    fi
}

expect_failure() {
    local message="$1"; shift
    if "$@" >/dev/null 2>&1; then
        fail "$message"
    else
        pass
    fi
}

file_mode() {
    if stat -c '%a' "$1" >/dev/null 2>&1; then
        stat -c '%a' "$1"
    else
        stat -f '%Lp' "$1"
    fi
}

cleanup() {
    if [ -n "${case_dir:-}" ] && [ -d "$case_dir" ]; then
        rm -rf "$case_dir"
    fi
}
trap cleanup EXIT

if [ ! -f "$script" ]; then
    printf 'FAIL: script not found: %s\n' "$script" >&2
    exit 1
fi

if [ -z "${BASH_VERSINFO:-}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    printf 'unit_state_checks: skipped (requires Bash >=4; current %s)\n' "${BASH_VERSION:-unknown}"
    exit 0
fi

if ! command -v flock >/dev/null 2>&1; then
    printf 'unit_state_checks: skipped (requires flock)\n'
    exit 0
fi

# Load functions without running the entry point.
# shellcheck disable=SC1090
source <(awk '/^#  ENTRY POINT/{exit} {print}' "$script")
# Загрузка wgcf и служба автопроверки покрыты tests/health_checks.sh; здесь сеть и systemd не нужны.
awg_install_wgcf() { return 0; }
health_service_install() { return 0; }

sleep() { :; }
clear() { :; }

new_case() {
    cleanup
    case_dir=$(mktemp -d "${TMPDIR:-/tmp}/gowarp-unit.XXXXXX")
    WARP_DIR="$case_dir/warp-manager"
    WARP_CONF="$WARP_DIR/config"
    WARP_KEYS="$WARP_DIR/keys.list"
    WARP_BOT_STATE="$WARP_DIR/bot.state"
    WARP_LOG="$case_dir/warp-manager.log"
    BOT_PID_FILE="$case_dir/warp_bot.pid"
    WGCF_ACCOUNT="$case_dir/wgcf-account.toml"
    WGCF_PROFILE="$case_dir/wgcf-profile.conf"
    WGCF_BIN="$case_dir/wgcf"
    WGCF_LEGACY_DIR="$case_dir/legacy"
    AWG_WGCF_BASE="$case_dir/wgcf-containers"
    HOST_WGCF_BAK_ROOT="$case_dir/rekey-bak"
    HOST_WGCF_BAK_DIR="$HOST_WGCF_BAK_ROOT"
    SOCKS_PORT="$DEFAULT_PORT"
    BOT_TOKEN=""
    BOT_CHAT_ID=""
    BOT_USER_IDS=""
    MODE=""
    WARP_KEY_MODE_3XUI="free"
    WARP_KEY_MODE_AWG="free"
    ACTIVE_KEY_NAME_3XUI=""
    ACTIVE_KEY_NAME_AWG=""
    mkdir -p "$WARP_DIR"
}

parse_trace_case() {
    local body="$1" expected_out="$2" expected_rc="$3" label="$4"
    local out rc
    set +e
    out=$(_parse_warp_trace_status "$body")
    rc=$?
    set -e
    assert_eq "$expected_out" "$out" "trace parser output: $label"
    assert_eq "$expected_rc" "$rc" "trace parser rc: $label"
}

run_account_type_parser_tests() {
    assert_eq "free" "$(_parse_account_type 'Account type: Free')" "warp-cli account type parses (no space before colon)"
    assert_eq "free" "$(_parse_account_type $'Id : x\nAccount type : free\nRole : child')" "wgcf account type parses (space before colon)"
    assert_eq "unlimited" "$(_parse_account_type 'Account type : unlimited')" "wgcf unlimited parses"
    assert_eq "limited" "$(_parse_account_type 'Account type: Limited')" "warp-cli limited parses"
    assert_eq "" "$(_parse_account_type 'Premium data : 0 B')" "unrelated line yields empty"
    assert_eq "" "$(_parse_account_type '')" "empty input yields empty"
    local d; d=$(mktemp -d "${TMPDIR:-/tmp}/gowarp-cls.XXXXXX")
    printf '{"code":1056,"message":"Too many connected devices."}\n' > "$d/e1"
    printf 'Error: 400 Bad Request: invalid license\n' > "$d/e2"
    : > "$d/e3"
    printf 'dial tcp: i/o timeout\n' > "$d/e4"
    printf '400 Bad Request\n' > "$d/e5"
    assert_eq "device-limit" "$(_classify_license_error "$d/e1")" "1056 classified as device-limit"
    assert_eq "invalid" "$(_classify_license_error "$d/e2")" "400 classified as invalid"
    assert_eq "rejected" "$(_classify_license_error "$d/e3")" "empty stderr classified as rejected"
    assert_eq "network" "$(_classify_license_error "$d/e4")" "timeout classified as network"
    assert_eq "bad-request" "$(_classify_license_error "$d/e5")" "generic HTTP 400 is reported without declaring the key invalid"
    assert_eq "rejected" "$(_classify_license_error "$d/none")" "missing file classified as rejected"
    rm -rf "$d"
    # capture first: `producer | grep -q` under pipefail can SIGPIPE the producer
    local txt
    txt=$(plus_reason_text free "3X-UI")
    if grep -q 'сейчас сообщает тип аккаунта Free' <<< "$txt"; then pass; else fail "free reason reports observed account type"; fi
    txt=$(LC_ALL=C plus_reason_text limited "3X-UI" 5368709120)
    if grep -q '5.0 ГБ' <<< "$txt"; then pass; else fail "limited reason text shows quota in GB"; fi
}

run_trace_parser_tests() {
    parse_trace_case $'ip=1.2.3.4\nwarp=plus\ncolo=AMS\n' "plus" "0" "plus"
    parse_trace_case $'warp=on\r\n' "free" "0" "free"
    parse_trace_case $'gateway=ok\nwarp=off\n' "off" "0" "off"
    parse_trace_case $'ip=1.2.3.4\n' "" "1" "missing"
    parse_trace_case "" "" "1" "empty"
}

run_config_migration_tests() {
    new_case
    cat > "$WARP_CONF" <<'CONF'
CONFIG_VERSION="2"
SOCKS_PORT="41000"
BOT_TOKEN=""
BOT_CHAT_ID="12345"
LICENSE_KEY="WARP-PRO-2026-ALPHA"
MODE="both"
CONTAINER="amnezia-awg"
CONTAINERS="amnezia-awg amnezia-awg2"
CONF
    chmod 600 "$WARP_CONF"
    : > "$WARP_KEYS"
    chmod 600 "$WARP_KEYS"
    printf 'device_id = "test"\nlicense_key = "AAAA-BBBB-CCCC-DDDD"\n' > "$WGCF_ACCOUNT"
    chmod 600 "$WGCF_ACCOUNT"

    init_config >/dev/null
    source "$WARP_CONF"

    assert_eq "$CONFIG_VERSION" "5" "migration sets CONFIG_VERSION"
    assert_eq "$SOCKS_PORT" "41000" "migration preserves SOCKS_PORT"
    assert_eq "$MODE" "both" "migration preserves MODE"
    assert_eq "$CONTAINERS" "amnezia-awg amnezia-awg2" "migration preserves CONTAINERS"
    assert_eq "$WARP_KEY_MODE_AWG" "free" "migration defaults AWG mode to free"
    assert_file_contains "$WARP_CONF" '^BOT_USER_IDS=""$' "migration adds BOT_USER_IDS"
    assert_file_contains "$WARP_CONF" '^INSTALLED_VERSION=""$' "migration adds INSTALLED_VERSION (v4)"
    assert_file_contains "$WARP_CONF" '^ACTIVE_KEY_NAME_3XUI=""$' "migration adds active 3X-UI key"
    assert_file_contains "$WGCF_ACCOUNT" '^license_key[[:space:]]*=' "migration preserves pre-existing AWG credentials"
    assert_eq "600" "$(file_mode "$WARP_CONF")" "migrated config is 0600"
    assert_eq "600" "$(file_mode "$WGCF_ACCOUNT")" "preserved wgcf account is 0600"

    local backups_before backups_after
    backups_before=$(find "$WARP_DIR" -name 'config.v*.bak' | wc -l | tr -d ' ')
    migrate_config_if_needed >/dev/null
    backups_after=$(find "$WARP_DIR" -name 'config.v*.bak' | wc -l | tr -d ' ')
    assert_eq "1" "$backups_before" "migration creates one backup"
    assert_eq "$backups_before" "$backups_after" "migration is idempotent"
}

run_legacy_paid_migration_test() (
    new_case
    printf 'MODE="amnezia"\n' > "$WARP_CONF"
    printf 'device_id = "test"\nlicense_key = "AAAA-BBBB-CCCC-DDDD"\n' > "$WGCF_ACCOUNT"
    cp "$WGCF_ACCOUNT" "$case_dir/original"
    cf_account_probe_wgcf() { echo 'unlimited|0|0'; }
    init_config >/dev/null || return 1
    [ "$WARP_KEY_MODE_AWG" = plus ] && [ "$ACTIVE_KEY_NAME_AWG" = imported-DDDD ] || return 1
    [ "$(get_key_by_name imported-DDDD)" = AAAA-BBBB-CCCC-DDDD ] || return 1
    cmp -s "$WGCF_ACCOUNT" "$case_dir/original" || return 1
    init_config >/dev/null || return 1
    [ "$(keys_count)" = 1 ]
)

run_config_write_tests() {
    new_case
    init_config >/dev/null

    local injected1="$case_dir/injected1"
    local injected2="$case_dir/injected2"
    local danger="abc\$(touch ${injected1})\`touch ${injected2}\`\"tail"
    save_config_val "BOT_TOKEN" "$danger"
    source "$WARP_CONF"
    assert_eq "$danger" "$BOT_TOKEN" "save_config_val preserves shell metacharacters literally"
    if [ -e "$injected1" ] || [ -e "$injected2" ]; then
        fail "save_config_val allowed command substitution during source"
    else
        pass
    fi
    assert_eq "600" "$(file_mode "$WARP_CONF")" "save_config_val keeps config 0600"
}

run_key_state_tests() {
    new_case
    init_config >/dev/null
    printf 'device_id = "test"\nlicense_key = "AAAA-BBBB-CCCC-DDDD"\n' > "$WGCF_ACCOUNT"
    chmod 600 "$WGCF_ACCOUNT"

    expect_success "valid key should be added" add_key "paid" "AAAA-BBBB-CCCC-DDDD"
    expect_failure "duplicate key name should fail" add_key "paid" "EEEE-FFFF-GGGG-HHHH"
    expect_failure "bad key name should fail" add_key "bad name" "EEEE-FFFF-GGGG-HHHH"
    expect_failure "pipe-bearing key should fail" add_key "pipe" "EEEE|FFFF"
    assert_eq "1" "$(keys_count)" "only one valid key is stored after duplicate/bad-key attempts"

    save_config_val "ACTIVE_KEY_NAME_3XUI" "paid"
    save_config_val "WARP_KEY_MODE_3XUI" "plus"
    save_config_val "ACTIVE_KEY_NAME_AWG" "paid"
    save_config_val "WARP_KEY_MODE_AWG" "plus"
    expect_success "delete active key should succeed" delete_key "paid"
    source "$WARP_CONF"
    assert_eq "0" "$(keys_count)" "delete_key removes the key"
    assert_eq "free" "$WARP_KEY_MODE_3XUI" "delete active key resets 3X-UI mode"
    assert_eq "" "$ACTIVE_KEY_NAME_3XUI" "delete active key clears 3X-UI active name"
    assert_eq "free" "$WARP_KEY_MODE_AWG" "delete active key resets AWG mode"
    assert_eq "" "$ACTIVE_KEY_NAME_AWG" "delete active key clears AWG active name"
    assert_file_not_contains "$WGCF_ACCOUNT" '^license_key[[:space:]]*=' "delete active AWG key scrubs wgcf license"
}

run_concurrent_add_tests() {
    new_case
    init_config >/dev/null
    set +e
    for _i in 1 2 3 4 5 6 7 8; do
        add_key "race" "AAAA-BBBB-CCCC-DDDD" &
    done
    wait
    set -e
    assert_eq "1" "$(awk -F'|' '$1=="race"{n++} END{print n+0}' "$WARP_KEYS")" "concurrent add_key stores one row"
}

run_plus_rollback_tests() {
    new_case
    init_config >/dev/null
    save_config_val "WARP_KEY_MODE_3XUI" "plus"
    save_config_val "ACTIVE_KEY_NAME_3XUI" ""
    expect_failure "3X-UI plus without active key should fail closed" apply_warp_plus_key_3xui
    source "$WARP_CONF"
    assert_eq "free" "$WARP_KEY_MODE_3XUI" "3X-UI plus-without-active rollback sets free"
    assert_eq "" "$ACTIVE_KEY_NAME_3XUI" "3X-UI plus-without-active clears active key"

    printf 'device_id = "test"\nlicense_key = "AAAA-BBBB-CCCC-DDDD"\n' > "$WGCF_ACCOUNT"
    chmod 600 "$WGCF_ACCOUNT"
    save_config_val "WARP_KEY_MODE_AWG" "plus"
    save_config_val "ACTIVE_KEY_NAME_AWG" "missing"
    expect_failure "AWG plus with missing active key should fail closed" apply_warp_plus_key_awg
    source "$WARP_CONF"
    assert_eq "free" "$WARP_KEY_MODE_AWG" "AWG missing active rollback sets free"
    assert_eq "" "$ACTIVE_KEY_NAME_AWG" "AWG missing active clears active key"
    assert_file_not_contains "$WGCF_ACCOUNT" '^license_key[[:space:]]*=' "AWG missing active rollback scrubs license"
}

run_awg_mode_snapshot_tests() {
    new_case
    init_config >/dev/null

    save_config_val "WARP_KEY_MODE_AWG" "free"
    save_config_val "ACTIVE_KEY_NAME_AWG" ""
    awg_restore_mode_snapshot "plus" "paid"
    source "$WARP_CONF"
    assert_eq "plus" "$WARP_KEY_MODE_AWG" "AWG mode snapshot restore writes plus mode"
    assert_eq "paid" "$ACTIVE_KEY_NAME_AWG" "AWG mode snapshot restore writes active key"

    awg_restore_mode_snapshot "free" ""
    source "$WARP_CONF"
    assert_eq "free" "$WARP_KEY_MODE_AWG" "AWG mode snapshot restore writes free mode"
    assert_eq "" "$ACTIVE_KEY_NAME_AWG" "AWG mode snapshot restore clears active key"
}

run_bot_auth_nonce_state_tests() {
    new_case
    init_config >/dev/null

    BOT_CHAT_ID="123"
    BOT_USER_IDS=""
    expect_success "private chat owner should be allowed" bot_chat_allowed "123" "123"
    expect_failure "wrong private user should be denied" bot_chat_allowed "123" "999"
    expect_failure "empty user id should be denied" bot_chat_allowed "123" ""

    BOT_CHAT_ID="-100777"
    BOT_USER_IDS="111, 222"
    expect_success "allowlisted group user should be allowed in group" bot_chat_allowed "-100777" "111"
    expect_success "allowlisted user should be allowed in private chat" bot_chat_allowed "222" "222"
    expect_failure "non-allowlisted group user should be denied" bot_chat_allowed "-100777" "333"
    expect_failure "non-allowlisted private user should be denied" bot_chat_allowed "333" "333"

    local now old future
    now=$(date +%s)
    old=$((now - NONCE_TTL_SEC - 1))
    future=$((now + 1))
    expect_success "fresh nonce should validate" check_nonce_valid "abc:${now}" "abc"
    expect_failure "wrong nonce should fail" check_nonce_valid "abc:${now}" "def"
    expect_failure "expired nonce should fail" check_nonce_valid "abc:${old}" "abc"
    expect_failure "future nonce should fail" check_nonce_valid "abc:${future}" "abc"
    expect_failure "nonnumeric nonce timestamp should fail" check_nonce_valid "abc:notnum" "abc"

    bot_state_set "-100777" "awaiting_keyname" "" "111"
    bot_state_set "-100777" "awaiting_keyvalue" "paid" "222"
    assert_eq "awaiting_keyname|" "$(bot_state_get "-100777" "111")" "bot state is isolated per user"
    assert_eq "awaiting_keyvalue|paid" "$(bot_state_get "-100777" "222")" "second user state is preserved"
    bot_state_clear "-100777" "111"
    assert_eq "" "$(bot_state_get "-100777" "111")" "bot state clear affects only selected user"
    assert_eq "awaiting_keyvalue|paid" "$(bot_state_get "-100777" "222")" "bot state clear does not remove other user"
    bot_state_clear
    if [ -f "$WARP_BOT_STATE" ]; then
        fail "bot_state_clear without args should remove all state"
    else
        pass
    fi
}

run_verify_wrapper_tests() {
    new_case
    init_config >/dev/null
    local fake_bin="$case_dir/bin"
    mkdir -p "$fake_bin"
    cat > "$fake_bin/curl" <<'SH'
#!/usr/bin/env sh
printf 'ip=198.51.100.10\nwarp=plus\n'
SH
    cat > "$fake_bin/docker" <<'SH'
#!/usr/bin/env sh
if [ "$1" = "exec" ]; then
    printf 'ip=198.51.100.11\nwarp=on\n'
    exit 0
fi
exit 1
SH
    chmod +x "$fake_bin/curl" "$fake_bin/docker"
    local old_path="$PATH"
    PATH="$fake_bin:$PATH"
    assert_eq "plus" "$(verify_warp_plus_3xui)" "3X-UI trace wrapper parses fake curl output"
    assert_eq "free" "$(verify_warp_plus_awg_container "amnezia-awg")" "AWG trace wrapper parses fake docker output"
    PATH="$old_path"
}

run_rekey_failure_test() (
    new_case
    local events="$case_dir/events"
    : > "$events"
    awg_discover_containers() { AWG_ALL_CONTAINERS=(amnezia-awg amnezia-awg2); }
    docker() { printf 'docker %s\n' "$*" >> "$events"; return 0; }
    awg_resolve_endpoint() { echo 192.0.2.1; }
    awg_load_container_data() { return 0; }
    awg_snapshot_container() { printf 'snapshot %s\n' "$1" >> "$events"; }
    awg_snapshot_host_wgcf() { return 0; }
    awg_ensure_account() { return 1; }
    awg_restore_host_wgcf() { return 0; }
    awg_restore_container_snapshot() { printf 'restore %s\n' "$1" >> "$events"; }
    awg_restore_mode_snapshot() { return 0; }
    if rekey_awg_core >/dev/null; then
        echo 'FAIL: failed registrations reported success' >&2; exit 1
    fi
    if grep -q 'wg-quick down' "$events"; then
        echo 'FAIL: active tunnels stopped before registration' >&2; exit 1
    fi
    for c in amnezia-awg amnezia-awg2; do
        grep -q "snapshot $c$" "$events" || exit 1
    done
)
expect_success "failed rekey preserves both tunnels" run_rekey_failure_test

run_awg_interface_discovery_test() (
    # В контейнере есть только awg0.conf: поиск идёт по кандидатам в том же порядке, что у блока start.sh.
    docker() {
        local f
        case "$5" in
            'for f in $1;'*)
                for f in $7; do
                    [ "$f" != /opt/amnezia/awg/awg0.conf ] || { echo "$f"; return 0; }
                done
                return 1 ;;
            *sed*) echo 10.77.3.1/24 ;;
            *) return 1 ;;
        esac
    }
    CONTAINER=amnezia-awg3
    awg_load_container_data || return 1
    [ "$AWG_VPN_CONF" = /opt/amnezia/awg/awg0.conf ] && [ "$AWG_VPN_IF" = awg0 ] && [ "$AWG_VPN_QUICK_CMD" = awg-quick ] || return 1
    # amnezia-awg2 (AmneziaWG 3.1) сначала ищет awg0.conf, остальные — wg0.conf.
    [ "$(awg_vpn_conf_candidates amnezia-awg2 | cut -d' ' -f1)" = /opt/amnezia/awg/awg0.conf ] || return 1
    [ "$(awg_vpn_conf_candidates amnezia-awg | cut -d' ' -f1)" = /opt/amnezia/awg/wg0.conf ] || return 1
    docker() { return 1; }
    ! awg_load_container_data >/dev/null
)
expect_success "AWG 3.1 interface comes from discovered config" run_awg_interface_discovery_test

run_trace_parser_tests
run_account_type_parser_tests
run_config_migration_tests
expect_success "v2.2 paid account survives migration without credential changes" run_legacy_paid_migration_test
run_config_write_tests
run_key_state_tests
run_concurrent_add_tests
run_plus_rollback_tests
run_awg_mode_snapshot_tests
run_bot_auth_nonce_state_tests
run_verify_wrapper_tests

if [ "$failures" -ne 0 ]; then
    printf 'unit_state_checks: %d failure(s), %d passed checks\n' "$failures" "$checks" >&2
    exit 1
fi

printf 'unit_state_checks: ok (%d checks)\n' "$checks"
