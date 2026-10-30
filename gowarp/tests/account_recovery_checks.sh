#!/usr/bin/env bash
# Local failure-injection tests; no Docker daemon, WARP client or network needed.
# Usage: bash tests/account_recovery_checks.sh [path/to/warp.sh]
# Exit 77 means the required Bash/flock runtime is unavailable, not a pass.
# Fixture globals are consumed by the dynamically sourced production functions.
# shellcheck disable=SC2034
set -uo pipefail

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    printf 'account_recovery_checks: SKIP (requires Bash >=4; current %s)\n' "$BASH_VERSION"
    exit 77
fi
if ! command -v flock >/dev/null 2>&1; then
    printf 'account_recovery_checks: SKIP (requires flock)\n'
    exit 77
fi

script="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/warp.sh}"
if [ ! -f "$script" ]; then
    printf 'FAIL: script not found: %s\n' "$script" >&2
    exit 1
fi
case_root=$(mktemp -d "${TMPDIR:-/tmp}/gowarp-account-recovery.XXXXXX") || exit 1
trap 'rm -rf "$case_root"' EXIT
umask 077

# Cut at the entry-point marker, including neither startup nor CLI dispatch.
if ! awk '/^#  ENTRY POINT/{found=1; exit} {print} END {if (!found) exit 1}' \
    "$script" > "$case_root/functions.sh"; then
    printf 'FAIL: entry-point marker not found in %s\n' "$script" >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$case_root/functions.sh" || exit 1

# Block even command-qualified/subprocess invocations of external backends.
# Individual cases supply shell stubs for the interactions they expect.
mkdir "$case_root/bin" || exit 1
for backend in curl wget docker warp-cli wgcf systemctl; do
    cat > "$case_root/bin/$backend" <<'STUB'
#!/bin/sh
printf 'unexpected executable: %s\n' "$0" >> "$ACCOUNT_RECOVERY_UNEXPECTED_CALLS"
exit 99
STUB
    chmod 700 "$case_root/bin/$backend" || exit 1
done
export PATH="$case_root/bin:$PATH"

unexpected_external() {
    printf 'unexpected call: %s\n' "$*" >> "$ACCOUNT_RECOVERY_UNEXPECTED_CALLS"
    return 99
}
curl() { unexpected_external curl; }
wget() { unexpected_external wget; }
docker() { unexpected_external docker; }
warp-cli() { unexpected_external warp-cli; }
wgcf() { unexpected_external wgcf; }
systemctl() { unexpected_external systemctl; }
sleep() { :; }
cloudflare_log_scrub() { :; }
# wgcf считается уже закреплённым, служба автопроверки в этих сценариях не ставится.
awg_install_wgcf() { return 0; }
health_service_install() { return 0; }

fail() { printf 'FAIL: %s\n' "$*" >&2; return 1; }
assert_eq() {
    [ "$1" = "$2" ] || fail "$3 (expected '$1', got '$2')"
}
assert_event() {
    grep -qFx "$1" "$events" || fail "expected event: $1"
}

new_case() {
    trial="$case_root/$1"
    mkdir -p "$trial/config" "$trial/legacy" || return 1
    export ACCOUNT_RECOVERY_UNEXPECTED_CALLS="$trial/unexpected"
    : > "$ACCOUNT_RECOVERY_UNEXPECTED_CALLS"
    events="$trial/events"; : > "$events"
    WARP_DIR="$trial/config"; WARP_CONF="$WARP_DIR/config"
    WARP_KEYS="$WARP_DIR/keys.list"; WARP_LOG="$trial/warp.log"
    WARP_BOT_STATE="$trial/bot.state"; BOT_PID_FILE="$trial/bot.pid"
    WGCF_LEGACY_DIR="$trial/legacy"; WGCF_DIR="$WGCF_LEGACY_DIR"
    WGCF_ACCOUNT="$WGCF_DIR/wgcf-account.toml"
    WGCF_PROFILE="$WGCF_DIR/wgcf-profile.conf"
    WGCF_BIN="$case_root/bin/wgcf"
    AWG_WGCF_BASE="$trial/devices"
    HOST_WGCF_BAK_ROOT="$trial/snapshots"
    HOST_WGCF_BAK_DIR="$HOST_WGCF_BAK_ROOT"
    AWG_WARP_DIR="$trial/container/warp"; AWG_WARP_CONF="$AWG_WARP_DIR/warp.conf"
    AWG_WARP_CLIENTS="$AWG_WARP_DIR/clients.list"
    AWG_START_SH="$trial/container/start.sh"
    AWG_WATCH_BIN="$trial/watcher"; AWG_WATCH_SERVICE="$trial/watcher.service"
    CF_API_BASE="https://account-recovery.invalid"
    : > "$WARP_LOG"
    cat > "$WARP_CONF" <<'CONF'
CONFIG_VERSION="5"
SOCKS_PORT="40000"
BOT_TOKEN=""
BOT_CHAT_ID=""
BOT_USER_IDS=""
LICENSE_KEY=""
MODE="both"
CONTAINER=""
CONTAINERS=""
WARP_KEY_MODE_3XUI="plus"
ACTIVE_KEY_NAME_3XUI="old"
WARP_KEY_MODE_AWG="free"
ACTIVE_KEY_NAME_AWG=""
INSTALLED_VERSION=""
CONF
    printf 'old|AAAAAAAA-BBBBBBBB-CCCCCCCC|test\nnew|DDDDDDDD-EEEEEEEE-FFFFFFFF|test\n' > "$WARP_KEYS"
    # shellcheck disable=SC1090
    source "$WARP_CONF"
}

case_unregister_http500() {
    curl() {
        local arg is_delete=0 is_fake_device=0
        for arg in "$@"; do
            [ "$arg" != DELETE ] || is_delete=1
            [ "$arg" != "$CF_API_BASE/reg/test-old" ] || is_fake_device=1
        done
        [ "$is_delete$is_fake_device" = 11 ] || { unexpected_external curl-arguments; return 99; }
        echo delete-http500 >> "$events"
        printf '500'
        return 0  # HTTP failure with a successful curl transport.
    }
    local rc=0
    awg_unregister_device_id test-old fake-token > "$trial/output" 2>&1 || rc=$?
    assert_event delete-http500 || return 1
    [ "$rc" -ne 0 ] || fail 'HTTP 500 deletion returned success' || return 1
    if grep -Ei 'unregistered via API|successfully (unregistered|deleted)|retired successfully' \
        "$WARP_LOG" "$trial/output"; then
        fail 'HTTP 500 deletion was logged as successful'; return 1
    fi
}

case_3xui_registration_failure() {
    is_warp_installed_3xui() { return 0; }
    warp-cli() {
        case "$*" in
            '--accept-tos registration new') echo registration-failed >> "$events"; return 1 ;;
            '--accept-tos registration show') printf 'License: AAAAAAAA-BBBBBBBB-CCCCCCCC\nAccount type: Unlimited\n' ;;
            '--accept-tos disconnect'|'--accept-tos registration delete'|'--accept-tos connect') return 0 ;;
            *) unexpected_external warp-cli-arguments ;;
        esac
    }
    local rc=0
    rekey_3xui_core > "$trial/output" 2>&1 || rc=$?
    assert_event registration-failed || return 1
    [ "$rc" -ne 0 ] || fail 'failed registration returned success' || return 1
    assert_eq plus "$WARP_KEY_MODE_3XUI" 'in-memory paid mode survives registration failure' || return 1
    assert_eq old "$ACTIVE_KEY_NAME_3XUI" 'in-memory key survives registration failure' || return 1
    # Check the durable state as well as the variables in this invocation.
    # shellcheck disable=SC1090
    source "$WARP_CONF"
    assert_eq plus "$WARP_KEY_MODE_3XUI" 'saved paid mode survives registration failure' || return 1
    assert_eq old "$ACTIVE_KEY_NAME_3XUI" 'saved key survives registration failure'
}

case_3xui_failed_license_restore() {
    local remote_license=old
    is_warp_installed_3xui() { return 0; }
    apply_warp_plus_key_3xui() {
        remote_license=new; PLUS_LAST_REASON=ok
        echo candidate-applied >> "$events"
        return 0
    }
    verify_warp_plus_3xui() { echo trace-failed >> "$events"; return 1; }
    warp-cli() {
        case "$*" in
            '--accept-tos registration show') printf 'License: AAAAAAAA-BBBBBBBB-CCCCCCCC\nAccount type: Unlimited\n' ;;
            '--accept-tos registration license AAAAAAAA-BBBBBBBB-CCCCCCCC')
                echo license-restore-failed >> "$events"; return 1 ;;
            '--accept-tos mode proxy'|'--accept-tos proxy port 40000'|'--accept-tos connect') return 0 ;;
            *) unexpected_external warp-cli-arguments ;;
        esac
    }
    local rc=0
    plus_apply_choice 3xui plus new > "$trial/output" 2>&1 || rc=$?
    assert_event candidate-applied && assert_event trace-failed && assert_event license-restore-failed || return 1
    assert_eq new "$remote_license" 'failed restore leaves the external candidate applied' || return 1
    [ "$rc" -ne 0 ] || fail 'failed license restoration returned success' || return 1
    if grep -Ei '(^|; |: )(восстановлена предыдущая настройка|прежний режим восстановлен|previous (settings|configuration) restored|rollback (succeeded|successful))' \
        "$trial/output" "$WARP_LOG"; then
        fail 'failed license restoration falsely reported the previous settings restored'; return 1
    fi
}

case_awg_mode_write_failure() {
    # The atomic helper is the parent's new contract. Also fail the legacy helper
    # so this test catches the old implementation masking its failed writes.
    save_config_values() { echo config-write-failed >> "$events"; return 7; }
    save_config_val() { echo config-write-failed >> "$events"; return 7; }
    cp "$WARP_CONF" "$trial/config-before" || return 1
    local rc=0
    awg_restore_mode_snapshot plus new > "$trial/output" 2>&1 || rc=$?
    assert_event config-write-failed || return 1
    [ "$rc" -ne 0 ] || fail 'AWG mode persistence failure returned success' || return 1
    cmp -s "$trial/config-before" "$WARP_CONF" || fail 'failed write changed persisted settings' || return 1
    assert_eq free "$WARP_KEY_MODE_AWG" 'failed write preserves in-memory mode' || return 1
    assert_eq '' "$ACTIVE_KEY_NAME_AWG" 'failed write preserves in-memory key'
}

case_awg_recovery_failure() {
    local broken_restore="$1" target=amnezia-awg
    awg_wgcf_ctx "$target" || return 1
    printf 'device_id = "test-old"\naccess_token = "fake-old-token"\n' > "$WGCF_ACCOUNT"
    printf 'old-profile\n' > "$WGCF_PROFILE"
    cp "$WGCF_ACCOUNT" "$trial/expected-old-account" || return 1
    printf 'old-network\n' > "$trial/live-network"
    awg_discover_containers() { AWG_ALL_CONTAINERS=(amnezia-awg); return 0; }
    awg_load_container_data() { return 0; }
    awg_prepare_warp_tools() { return 0; }
    awg_adopt_legacy_account() { return 0; }
    # Expected Docker reads and recovery marking are simulated locally.
    docker() {
        case "$*" in
            "exec amnezia-awg sh -c [ -f '$AWG_WARP_CONF' ]") return 0 ;;
            "exec amnezia-awg sh -c sed -n 's/^Endpoint = //p' '$AWG_WARP_CONF'") echo 192.0.2.1:2408 ;;
            'exec amnezia-awg touch /tmp/warp-manager-rekey-backup/recovery-required')
                touch "$trial/container-recovery-required" ;;
            *) unexpected_external docker-arguments ;;
        esac
    }
    awg_snapshot_container() { cp "$trial/live-network" "$trial/container-snapshot"; }
    awg_clear_container_snapshot() { rm -f "$trial/container-snapshot"; }
    awg_restore_container_snapshot() {
        echo restore-container >> "$events"
        [ "$broken_restore" != container ] || return 1
        cp "$trial/container-snapshot" "$trial/live-network"
    }
    if [ "$broken_restore" = host ]; then
        awg_restore_host_wgcf() { echo restore-host-failed >> "$events"; return 1; }
    fi
    awg_ensure_account() {
        printf 'device_id = "test-candidate"\naccess_token = "fake-candidate-token"\n' > "$WGCF_ACCOUNT"
    }
    apply_warp_plus_key_awg() { PLUS_LAST_REASON=ok; return 0; }
    awg_generate_profile() { printf 'candidate-profile\n' > "$WGCF_PROFILE"; }
    awg_build_warp_conf() {
        printf 'candidate-network\n' > "$trial/live-network"
        echo candidate-switched >> "$events"
    }
    awg_warp_up() { return 0; }
    awg_load_clients() { return 0; }
    awg_apply_rules() { return 0; }
    verify_warp_plus_awg_container() { echo trace-failed >> "$events"; return 1; }
    awg_patch_start_sh() { unexpected_external patch-start-on-failed-transaction; }
    awg_install_host_watcher() { unexpected_external watcher-on-failed-transaction; }
    awg_unregister_device_id() { printf 'revoke:%s\n' "$1" >> "$events"; return 0; }
    # Reset only the test context, never to the real /root account paths.
    awg_wgcf_ctx_reset() {
        WGCF_DIR="$WGCF_LEGACY_DIR"
        WGCF_ACCOUNT="$WGCF_DIR/wgcf-account.toml"; WGCF_PROFILE="$WGCF_DIR/wgcf-profile.conf"
        HOST_WGCF_BAK_DIR="$HOST_WGCF_BAK_ROOT"
    }
    if [ "$broken_restore" = remove ]; then
        rm() {
            if [ "$#" = 3 ] && [ "$1" = -f ] && [ "$2" = "$WGCF_ACCOUNT" ] &&
                [ "$3" = "$WGCF_PROFILE" ] && [ ! -f "$trial/removal-failed" ]; then
                # A partially successful rm: the profile disappears but the old
                # account survives. Real host restoration must repair this pair.
                command rm -f "$WGCF_PROFILE" || return 1
                touch "$trial/removal-failed"
                echo account-removal-failed >> "$events"
                return 1
            fi
            command rm "$@"
        }
        awg_ensure_account() { echo api-entered:register >> "$events"; unexpected_external account-registration; }
        apply_warp_plus_key_awg() { echo api-entered:apply >> "$events"; unexpected_external license-application; }
    fi
    local rc=0
    rekey_awg_core plus new > "$trial/output" 2>&1 || rc=$?
    if [ "$broken_restore" = remove ]; then
        assert_event account-removal-failed || return 1
        [ "$rc" -ne 0 ] || fail 'failed account removal returned success' || return 1
        if grep -qE '^(api-entered:|revoke:|candidate-switched$)' "$events"; then
            fail 'failed account removal entered an API path, revoked an account, or changed the tunnel'; return 1
        fi
        cmp -s "$trial/expected-old-account" "$AWG_WGCF_BASE/$target/wgcf-account.toml" ||
            { fail 'failed account removal lost the working account'; return 1; }
        assert_eq old-profile "$(cat "$AWG_WGCF_BASE/$target/wgcf-profile.conf")" \
            'failed account removal restores the partially deleted host profile' || return 1
        assert_eq old-network "$(cat "$trial/live-network")" \
            'failed account removal preserves the working tunnel'
        return $?
    fi
    assert_event candidate-switched && assert_event trace-failed || return 1
    if [ "$broken_restore" = host ]; then
        assert_event restore-host-failed || return 1
    else
        assert_event restore-container || return 1
    fi
    [ "$rc" -ne 0 ] || fail 'failed AWG recovery returned success' || return 1
    if grep -q '^revoke:' "$events"; then
        fail 'unresolved recovery revoked an account'; return 1
    fi
    cmp -s "$trial/expected-old-account" "$HOST_WGCF_BAK_ROOT/$target/wgcf-account.toml" ||
        { fail 'unresolved recovery lost the old account snapshot'; return 1; }
    assert_eq old-profile "$(cat "$HOST_WGCF_BAK_ROOT/$target/wgcf-profile.conf")" \
        'unresolved recovery preserves host profile snapshot' || return 1
    assert_eq old-network "$(cat "$trial/container-snapshot")" \
        'unresolved recovery preserves container snapshot'
}

# Заглушка wgcf: первые WGCF_STUB_FAILS вызовов отвечают 429, дальше создают устройство.
write_wgcf_stub() {
    cat > "$trial/wgcf" <<'STUB'
#!/bin/sh
n=$(cat "$WGCF_STUB_COUNTER" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$WGCF_STUB_COUNTER"
case "$WGCF_STUB_MODE" in
    429) if [ "$n" -le "$WGCF_STUB_FAILS" ]; then echo 'Wraps: (3) 429 Too Many Requests' >&2; exit 1; fi ;;
    other) echo 'Error: registration failed' >&2; exit 1 ;;
esac
printf 'device_id = "test-new"\naccess_token = "fake-new-token"\n' > wgcf-account.toml
STUB
    chmod 700 "$trial/wgcf" || return 1
    WGCF_BIN="$trial/wgcf"
    export WGCF_STUB_COUNTER="$trial/wgcf-calls"
}

case_awg_register_waits_after_429() {
    awg_wgcf_ctx amnezia-awg2 || return 1
    write_wgcf_stub || return 1
    export WGCF_STUB_MODE=429 WGCF_STUB_FAILS=2
    sleep() { echo "sleep:$1" >> "$events"; }
    date +%s > "$WARP_DIR/.wgcf-last-register"
    local rc=0
    awg_ensure_account > "$trial/output" 2> "$trial/stderr" || rc=$?
    assert_eq 0 "$rc" 'registration recovers after 429 answers' || return 1
    [ -f "$WGCF_ACCOUNT" ] || fail 'device file was not created' || return 1
    assert_eq 3 "$(cat "$WGCF_STUB_COUNTER")" 'two 429 answers, then a registration' || return 1
    grep -qE '^sleep:(6[0-5]|5[0-9])$' "$events" || fail 'no pause after a registration a moment ago' || return 1
    assert_eq 3 "$(grep -c '^sleep:' "$events")" 'every attempt waits for the Cloudflare window' || return 1
    grep -q 'не чаще раза в минуту' "$trial/stderr" || fail 'the wait was not explained' || return 1
    grep -qE '^[0-9]+$' "$WARP_DIR/.wgcf-last-register" || fail 'registration time not recorded'
}

case_awg_register_gives_up() {
    awg_wgcf_ctx amnezia-awg2 || return 1
    write_wgcf_stub || return 1
    export WGCF_STUB_MODE=429 WGCF_STUB_FAILS=99
    local rc=0
    awg_ensure_account > "$trial/output" 2> "$trial/stderr" || rc=$?
    [ "$rc" -ne 0 ] || fail 'persistent 429 returned success' || return 1
    assert_eq 4 "$(cat "$WGCF_STUB_COUNTER")" 'gives up after four attempts' || return 1
    case "$AWG_REGISTER_ERROR" in
        *'слишком частые регистрации'*) ;;
        *) fail "unclear failure reason: $AWG_REGISTER_ERROR"; return 1 ;;
    esac
    grep -q 'AWG REGISTER: Cloudflare не выдал новое устройство' "$WARP_LOG" || fail 'failure not logged' || return 1
    [ ! -e "$WARP_DIR/.wgcf-last-register" ] || fail 'failed registration recorded as a success'
}

case_awg_register_other_error_no_retry() {
    awg_wgcf_ctx amnezia-awg2 || return 1
    write_wgcf_stub || return 1
    export WGCF_STUB_MODE=other WGCF_STUB_FAILS=0
    local rc=0
    awg_ensure_account > "$trial/output" 2>&1 || rc=$?
    [ "$rc" -ne 0 ] || fail 'failed registration returned success' || return 1
    assert_eq 1 "$(cat "$WGCF_STUB_COUNTER")" 'a non-429 error is not retried'
}

case_resume_interrupted_rekey() {
    local target=amnezia-awg2 mode="$1" snap
    awg_wgcf_ctx "$target" || return 1
    printf 'device_id = "test-candidate"\naccess_token = "fake-candidate-token"\n' > "$WGCF_ACCOUNT"
    printf '[Interface]\nPrivateKey = candidate-private\n' > "$WGCF_PROFILE"
    mkdir -p "$HOST_WGCF_BAK_DIR" "$WGCF_DIR/.candidate.test"
    printf 'device_id = "test-old"\naccess_token = "fake-old-token"\n' > "$HOST_WGCF_BAK_DIR/wgcf-account.toml"
    printf '[Interface]\nPrivateKey = old-private\n' > "$HOST_WGCF_BAK_DIR/wgcf-profile.conf"
    touch "$HOST_WGCF_BAK_DIR/recovery-required"
    cp "$WGCF_ACCOUNT" "$WGCF_DIR/.candidate.test/wgcf-account.toml"
    cp "$WGCF_PROFILE" "$WGCF_DIR/.candidate.test/wgcf-profile.conf"
    # 2.5.2: снимок контейнера лежит на хосте. legacy — снимок остался в /tmp контейнера от прежней версии;
    # nosnap — снимка нет нигде: контейнер пересоздан, пока перевыпуск ждал; halfback — прошлый откат уже
    # вернул в контейнер прежнее устройство и сорвался позже.
    snap=$(awg_ct_snap_dir "$target")
    case "$mode" in
        legacy|nosnap) ;;
        *) mkdir -p "$snap" && touch "$snap/ready" "$snap/was-up" ;;
    esac
    mkdir -p "$trial/legacy-snap/warp" && printf 'old-network\n' > "$trial/legacy-snap/warp/warp.conf"
    docker() {
        case "$*" in
            "exec $target sh -c cd '$AWG_LEGACY_SNAP' 2>/dev/null && [ -f ready ] && ls")
                [ "$mode" = legacy ] || return 1
                printf 'ready\nrecovery-required\nstart.sh\nwarp\nwas-up\n' ;;
            "exec $target tar -C $AWG_LEGACY_SNAP -cf - warp") command tar -C "$trial/legacy-snap" -cf - warp ;;
            "exec $target rm -rf $AWG_LEGACY_SNAP") echo legacy-removed >> "$events" ;;
            "exec $target sh -c sed -n 's/^Endpoint = //p' '$AWG_WARP_CONF'") echo 162.159.192.1:4500 ;;
            "exec $target sh -c sed -n 's/^PrivateKey[[:space:]]*=[[:space:]]*//p' '$AWG_WARP_CONF'")
                if [ "$mode" = halfback ]; then echo old-private; else echo candidate-private; fi ;;
            *) unexpected_external "docker $*" ;;
        esac
    }
    awg_load_container_data() { return 0; }
    awg_warp_up() { echo "up:${1:-restart}" >> "$events"; }
    awg_load_clients() { return 0; }
    awg_apply_rules() { return 0; }
    awg_patch_start_sh() { echo patch-start >> "$events"; }
    awg_build_warp_conf() { echo "build:$WGCF_PROFILE:$1" >> "$events"; touch "$trial/restored"; }
    # forward: новое устройство WARP+ при режиме Free в конфиге; back: после отката проходит прежнее;
    # dead и broken: не проходит ничего.
    verify_warp_plus_awg_container() {
        echo "verify:$1" >> "$events"
        case "$mode" in
            forward) echo plus ;;
            back|legacy|nosnap) [ ! -f "$trial/restored" ] || echo free ;;
            # Прежнее устройство уже в контейнере и работает: «вперёд» его приняло бы за новое.
            halfback) echo free ;;
        esac
    }
    awg_retire_snapshot_account() { echo "retire-old:$(sed -n 's/^device_id = //p' "$HOST_WGCF_BAK_DIR/wgcf-account.toml")" >> "$events"; }
    awg_restore_container_snapshot() {
        echo "restore-container:$1" >> "$events"
        # Восстанавливать есть из чего: снимок на хосте, перенесённый из контейнера — вместе с архивом /opt/warp.
        [ -f "$snap/ready" ] || { echo restore-without-snapshot >> "$events"; return 1; }
        if [ "$mode" = legacy ]; then
            [ -f "$snap/was-up" ] && [ -f "$snap/recovery-required" ] &&
                [ "$(command tar -xOf "$snap/warp.tar" warp/warp.conf)" = old-network ] && echo legacy-imported >> "$events"
        fi
        [ "$mode" != broken ] || return 1
        touch "$trial/restored"
    }
    awg_unregister_device_id() { printf 'revoke:%s\n' "$1" >> "$events"; return 0; }
    local rc=0
    awg_resume_interrupted_rekey "$target" > "$trial/output" 2>&1 || rc=$?
    if [ "$mode" = halfback ]; then
        if grep -q '^up:' "$events"; then fail 'went forward although the container already runs the old device'; return 1; fi
    else
        assert_event "up:restart" || return 1
    fi
    assert_event "verify:$target" || return 1
    case "$mode" in
        forward)
            assert_eq 0 "$rc" 'a working new device completes the interrupted rekey' || return 1
            assert_eq forward "$AWG_RESUME_RESULT" 'forward completion reported' || return 1
            assert_event 'retire-old:"test-old"' && assert_event patch-start || return 1
            if grep -qE '^(restore-container|revoke):' "$events"; then fail 'forward completion restored or revoked a device'; return 1; fi
            grep -q 'device_id = "test-candidate"' "$WGCF_ACCOUNT" || fail 'new account not kept' || return 1
            ;;
        back|dead|legacy|nosnap|halfback)
            assert_eq 0 "$rc" 'a failed new device rolls back to the old one' || return 1
            case "$mode" in dead) assert_eq dead "$AWG_RESUME_RESULT" 'rollback result reported' ;;
                *) assert_eq back "$AWG_RESUME_RESULT" 'rollback result reported' ;; esac || return 1
            if [ "$mode" = nosnap ]; then
                # Без снимка прежний WARP собирается из хостовой копии прежнего устройства на прежнем порту.
                assert_event "build:$HOST_WGCF_BAK_DIR/wgcf-profile.conf:162.159.192.1:4500" && assert_event patch-start || return 1
                if grep -q '^restore-container:' "$events"; then fail 'restored a snapshot that does not exist'; return 1; fi
            else
                assert_event "restore-container:$target" || return 1
            fi
            if [ "$mode" = legacy ]; then assert_event legacy-imported && assert_event legacy-removed || return 1; fi
            assert_event revoke:test-candidate || return 1
            if grep -q '^revoke:test-old' "$events"; then fail 'rollback revoked the restored device'; return 1; fi
            grep -q 'device_id = "test-old"' "$WGCF_ACCOUNT" || fail 'old account not restored' || return 1
            ;;
        broken)
            [ "$rc" -ne 0 ] || fail 'a failed rollback reported success' || return 1
            [ -f "$HOST_WGCF_BAK_DIR/recovery-required" ] || fail 'a failed resume removed the recovery marker' || return 1
            [ -f "$snap/ready" ] || fail 'a failed resume removed the container snapshot' || return 1
            if grep -q '^revoke:' "$events"; then fail 'a failed resume revoked a device'; return 1; fi
            return 0
            ;;
    esac
    [ ! -e "$HOST_WGCF_BAK_DIR" ] || fail 'host snapshot not cleared' || return 1
    [ ! -e "$WGCF_DIR/.candidate.test" ] || fail 'candidate copy not cleared' || return 1
    [ ! -e "$snap" ] || fail 'container snapshot not cleared'
}

checks=0
failures=0
run_case() {
    local name="$1"; shift
    if (
        new_case "$name" || exit 1
        local rc=0
        "$@" || rc=$?
        if [ -s "$ACCOUNT_RECOVERY_UNEXPECTED_CALLS" ]; then
            cat "$ACCOUNT_RECOVERY_UNEXPECTED_CALLS" >&2
            fail 'unexpected backend interaction was blocked'
            rc=1
        fi
        exit "$rc"
    ) > "$case_root/$name.result" 2>&1; then
        printf 'account_recovery: %s OK\n' "$name"
        checks=$((checks + 1))
    else
        printf 'account_recovery: %s FAIL\n' "$name" >&2
        cat "$case_root/$name.result" >&2
        failures=$((failures + 1))
    fi
}

run_case unregister_http500 case_unregister_http500
run_case registration_preserves_paid_selection case_3xui_registration_failure
run_case rejected_license_restore case_3xui_failed_license_restore
run_case mode_write_failure case_awg_mode_write_failure
run_case container_recovery_failure case_awg_recovery_failure container
run_case host_recovery_failure case_awg_recovery_failure host
run_case local_removal_failure case_awg_recovery_failure remove
run_case register_waits_after_429 case_awg_register_waits_after_429
run_case register_gives_up_after_429 case_awg_register_gives_up
run_case register_other_error_no_retry case_awg_register_other_error_no_retry
run_case resume_rekey_forward case_resume_interrupted_rekey forward
run_case resume_rekey_rollback case_resume_interrupted_rekey back
run_case resume_rekey_rollback_dead case_resume_interrupted_rekey dead
run_case resume_rekey_rollback_fails case_resume_interrupted_rekey broken
run_case resume_rekey_legacy_snapshot case_resume_interrupted_rekey legacy
run_case resume_rekey_without_snapshot case_resume_interrupted_rekey nosnap
run_case resume_rekey_old_device_running case_resume_interrupted_rekey halfback
printf 'account_recovery_checks: %s passed, %s failed\n' "$checks" "$failures"
[ "$failures" -eq 0 ]
