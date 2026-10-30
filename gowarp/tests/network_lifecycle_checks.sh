#!/usr/bin/env bash
# Local fault-injection tests: no real Docker, services, networking, or host removal.
# The watcher/OFF scheduling repro is deliberately separate from this deterministic suite.
# Globals below are consumed by the dynamically sourced production functions.
# shellcheck disable=SC2034
set -uo pipefail

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    echo "network_lifecycle_checks: skipped (requires Bash >=4; current $BASH_VERSION)"
    exit 0
fi
if ! command -v flock >/dev/null 2>&1; then
    echo 'network_lifecycle_checks: skipped (requires flock)'
    exit 0
fi

script="${1:-warp.sh}"
[ -f "$script" ] || { echo "FAIL: script not found: $script" >&2; exit 1; }
# shellcheck disable=SC1090
source <(awk '/^#  ENTRY POINT/{exit} {print}' "$script")
production_cleanup_rules=$(declare -f awg_cleanup_rules)
case_root=$(mktemp -d) || exit 1
trap 'command rm -rf -- "$case_root"' EXIT
failures=0
checks=0

fail() { echo "FAIL: $*" >&2; return 1; }
unexpected() {
    printf '%s\n' "$*" >> "$trial/unexpected"
    fail "unexpected external operation: $*"
}
clear() { :; }
sleep() { :; }
log_action() { :; }
systemctl() { printf 'systemctl %s\n' "$*" >> "$trial/events"; }
kill() { unexpected "kill $*"; }
curl() { unexpected "curl $*"; }
wget() { unexpected "wget $*"; }
ssh() { unexpected "ssh $*"; }
ip() { unexpected "ip $*"; }
iptables() { unexpected "iptables $*"; }

# Keep historical hard-coded uninstall paths testable without touching the host.
# Every other removal must be beneath this suite's temporary directory.
rm() {
    local arg
    local -a args=()
    for arg in "$@"; do
        case "$arg" in
            /usr/local/bin/gowarp) arg="$GOWARP_BIN" ;;
            /etc/systemd/system/warp-bot.service) arg="$trial/warp-bot.service" ;;
            -f|-r|-rf|-fr|--) ;;
            "$case_root"/*) ;;
            *) unexpected "rm $*"; return 1 ;;
        esac
        args+=("$arg")
    done
    command rm "${args[@]}"
}

new_case() {
    trial="$case_root/$1"
    mkdir -p "$trial/tmp" "$trial/containers/amnezia-awg" "$trial/containers/amnezia-awg2"
    export TMPDIR="$trial/tmp"
    WARP_DIR="$trial/manager"; WARP_CONF="$WARP_DIR/config"
    WARP_LOG="$trial/manager.log"; GOWARP_BIN="$trial/gowarp"
    BOT_PID_FILE="$trial/no-bot.pid"
    AWG_WGCF_BASE="$trial/devices"; HOST_WGCF_BAK_ROOT="$trial/backups"
    WGCF_BIN="$trial/wgcf"; WGCF_ACCOUNT="$trial/legacy-account"; WGCF_PROFILE="$trial/legacy-profile"
    AWG_WATCH_BIN="$trial/watcher"; AWG_WATCH_SERVICE="$trial/watcher.service"
    HEALTH_SERVICE="$trial/gowarp-health.service"
    AWG_WARP_DIR=/opt/warp; AWG_WARP_CONF=/opt/warp/warp.conf
    AWG_WARP_CLIENTS=/opt/warp/clients.list; AWG_START_SH=/opt/amnezia/start.sh
    MODE=amnezia; CONTAINER=amnezia-awg
    mkdir -p "$WARP_DIR" "$AWG_WGCF_BASE/amnezia-awg2" "$HOST_WGCF_BAK_ROOT"
    local file c
    for file in "$WARP_CONF" "$WARP_LOG" "$GOWARP_BIN" "$WGCF_BIN" "$WGCF_ACCOUNT" \
        "$WGCF_PROFILE" "$AWG_WATCH_BIN" "$AWG_WATCH_SERVICE" \
        "$AWG_WGCF_BASE/amnezia-awg2/wgcf-profile.conf"; do
        printf 'synthetic preserved data\n' > "$file"
    done
    for c in amnezia-awg amnezia-awg2; do
        printf 'synthetic warp configuration\n' > "$trial/containers/$c/warp.conf"
    done
    : > "$trial/events"; : > "$trial/unexpected"
    scenario="${2:-normal}"
}

awg_discover_containers() {
    AWG_ALL_CONTAINERS=(amnezia-awg)
    [ "${1:-}" != all ] || AWG_ALL_CONTAINERS+=(amnezia-awg2)
    return 0
}
awg_pick_targets() { AWG_TARGETS=(amnezia-awg); }
awg_load_container_data() { return 0; }
awg_cleanup_rules() { return 0; }
awg_remove_from_start_sh() { return 0; }
awg_remove_host_watcher() { rm -f "$AWG_WATCH_BIN" "$AWG_WATCH_SERVICE"; }
# Account retirement has its own suite; keep this lifecycle suite offline.
awg_retire_unused_account() { return 0; }
# The fixtures model containers with the required runtime binaries already installed.
awg_prepare_warp_tools() { return 0; }
is_warp_installed_awg() { return 0; }
is_warp_running_awg() { return 0; }

docker() {
    printf 'docker %s\n' "$*" >> "$trial/events"
    local verb="$1" c payload src dst file
    shift
    case "$verb" in
        inspect)
            c="${!#}"
            if [ "$c" = amnezia-awg ]; then echo true; else echo false; fi ;;
        start) return 1 ;; # The stopped sibling cannot be started for cleanup.
        stop) return 0 ;;
        cp)
            while [[ "${1:-}" = -* ]] && [ "$1" != - ]; do shift; done
            src="$1"; dst="$2"; c="${src%%:*}"
            [[ "$src" = "$c:$AWG_WARP_CONF" ]] || { unexpected "docker cp $*"; return 1; }
            [ "$scenario" != offline_unknown ] || return 1
            src="$trial/containers/$c/warp.conf"
            [ -f "$src" ] || return 1
            if [ "$dst" = - ]; then
                command tar -cf - -C "${src%/*}" warp.conf
            else
                [[ "$dst" = "$case_root"/* ]] || { unexpected "docker cp destination $dst"; return 1; }
                command cp "$src" "$dst"
            fi ;;
        exec)
            [ "${1:-}" != -i ] || shift
            c="$1"; shift
            # Unlike docker cp, exec cannot inspect an existing stopped container.
            [ "$c" != amnezia-awg2 ] || return 1
            payload="$*"
            if [ "${1:-}" = sh ] && [ "${2:-}" = -s ]; then
                # Recognize the runtime UP/DOWN heredocs, never execute their commands.
                payload=$(cat)
                case "$payload" in
                    *'touch /opt/warp/disabled'*)
                        touch "$trial/disabled"
                        : > "$trial/runtime"
                        return 0 ;;
                    *'rm -f /opt/warp/disabled'*'wg-quick up'*)
                        rm -f "$trial/disabled"
                        return 0 ;;
                    *) unexpected 'docker exec runtime heredoc'; return 1 ;;
                esac
            fi
            case "$payload" in
                # 2.5.2: снимок /opt/warp уходит на хост архивом; clients.list контейнера — файл $trial/clients.
                *"tar -C '/opt' -cf - 'warp'"*)
                    command rm -rf "$trial/ct" && mkdir -p "$trial/ct/warp" || return 1
                    cp "$trial/clients" "$trial/ct/warp/clients.list" || return 1
                    command tar -C "$trial/ct" -cf - warp ;;
                *"tar -C '/opt' -xf -"*)
                    # Restore persistent state only. The real restore function must
                    # still re-add the start.sh block, bring the tunnel up and reapply routes.
                    command rm -rf "$trial/ct" && mkdir -p "$trial/ct" || return 1
                    command tar -C "$trial/ct" -xf - || return 1
                    cp "$trial/ct/warp/clients.list" "$trial/clients" ;;
                'ip link show warp') return 0 ;;
                "test -f $AWG_WARP_DIR/disabled") [ -f "$trial/disabled" ] ;;
                *"rm -rf '$AWG_WARP_DIR'"*)
                    [ "$scenario" != cleanup_fail ] || return 1
                    rm -f "$trial/containers/$c/warp.conf" ;;
                *start.sh.final-backup*) return 1 ;;
                "test ! -f $AWG_WARP_CONF")
                    [ ! -f "$trial/containers/$c/warp.conf" ] ;;
                *"test -f $AWG_WARP_CONF"*|*"[ -f '$AWG_WARP_CONF' ]"*)
                    [ -f "$trial/containers/$c/warp.conf" ] ;;
                *"$AWG_WARP_DIR/disabled"*) return 0 ;;
                *) unexpected "docker exec $c $payload" ;;
            esac ;;
        *) unexpected "docker $verb $*" ;;
    esac
}

assert_preserved_host_files() {
    local file
    for file in "$WGCF_BIN" "$WGCF_ACCOUNT" "$WGCF_PROFILE" "$AWG_WATCH_BIN" \
        "$AWG_WATCH_SERVICE" "$AWG_WGCF_BASE/amnezia-awg2/wgcf-profile.conf"; do
        [ "$(cat "$file" 2>/dev/null)" = 'synthetic preserved data' ] || {
            fail "surviving container lost host file: $file"; return 1;
        }
    done
}

run_offline_case() (
    new_case "$1" "$1"
    local rc=0
    uninstall_awg > "$trial/output" 2>&1 || rc=$?
    [ ! -f "$trial/containers/amnezia-awg/warp.conf" ] || { fail 'selected container was not cleaned'; return 1; }
    [ -f "$trial/containers/amnezia-awg2/warp.conf" ] || { fail 'stopped sibling was changed'; return 1; }
    assert_preserved_host_files || return 1
    if [ "$scenario" = offline_survivor ]; then
        [ "$rc" = 0 ] || { fail 'successful selective cleanup reported failure'; return 1; }
    fi
    # Unknown offline inspection may return failure, but must preserve shared state.
    [ ! -s "$trial/unexpected" ] || { cat "$trial/unexpected" >&2; return 1; }
)

run_incomplete_uninstall() (
    new_case "$1-$2" "$2"
    if [ "$scenario" = cleanup_fail ]; then
        # Isolate exec/cleanup failure from the separately tested docker-start failure.
        awg_discover_containers() { AWG_ALL_CONTAINERS=(amnezia-awg); }
    fi
    local rc=0
    if [ "$1" = full ]; then
        # full_uninstall can exit the shell; inspect its result from outside it.
        ( full_uninstall ) > "$trial/output" 2>&1 <<'INPUT' || rc=$?
y

INPUT
    else
        uninstall_awg all > "$trial/output" 2>&1 || rc=$?
    fi
    [ "$rc" -ne 0 ] || { fail "$1 uninstall reported success after $scenario"; return 1; }
    local file
    for file in "$WARP_CONF" "$WARP_LOG" "$GOWARP_BIN"; do
        [ "$(cat "$file" 2>/dev/null)" = 'synthetic preserved data' ] || {
            fail "incomplete uninstall deleted manager file: $file"; return 1;
        }
    done
    assert_preserved_host_files || return 1
    [ ! -s "$trial/unexpected" ] || { cat "$trial/unexpected" >&2; return 1; }
)

# Ключи клиентов: K1 → 10.77.1.2, K2 → 10.77.1.3, K3 → 10.77.1.4 (адрес берётся из конфига сервера).
K1=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
K2=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
K3=CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=

run_client_commit() (
    new_case "client-$1" "$1"
    declare -F awg_commit_clients >/dev/null || { fail 'missing awg_commit_clients(container, desired_key...)'; return 1; }
    local file snap
    printf '%s\n' "$K1" > "$trial/clients"
    printf '10.77.1.2/32\n' > "$trial/runtime"
    printf 'block\n' > "$trial/startup"
    for file in clients runtime startup; do cp "$trial/$file" "$trial/$file.before"; done
    snap=$(awg_ct_snap_dir amnezia-awg)
    awg_get_client_ips() { AWG_CLIENT_KEYS=("$K1" "$K2" "$K3"); AWG_CLIENT_IPS=(10.77.1.2/32 10.77.1.3/32 10.77.1.4/32); }
    awg_load_clients() {
        awg_get_client_ips
        mapfile -t AWG_SELECTED_KEYS < "$trial/clients"
        awg_resolve_selected_ips
    }
    inject_failure() {
        [ "$scenario" = "$1" ] && [ ! -f "$trial/fault-fired" ] || return 1
        touch "$trial/fault-fired"
    }
    awg_save_clients() {
        if inject_failure save; then
            printf 'partial write\n' > "$trial/clients"
            return 1
        fi
        printf '%s\n' "${AWG_SELECTED_KEYS[@]}" > "$trial/clients"
    }
    awg_apply_rules() {
        : > "$trial/runtime" # Model real cleanup occurring before a failed replacement.
        if [ "$scenario" = rollback ] || { [ "$scenario" = retry ] && [ ! -f "$trial/retry-armed" ]; }; then
            touch "$trial/fault-fired"; return 4
        fi
        inject_failure apply && return 4
        printf '%s\n' "${AWG_SELECTED_IPS[@]}" > "$trial/runtime"
    }
    # Блок start.sh от выбора не зависит: адреса он находит по ключам сам.
    awg_patch_start_sh() {
        inject_failure patch && { : > "$trial/startup"; return 1; }
        printf 'block\n' > "$trial/startup"
    }
    local rc=0
    awg_commit_clients amnezia-awg "$K1" "$K2" > "$trial/output" 2>&1 || rc=$?
    if [ "$scenario" = success ]; then
        [ "$rc" = 0 ] || { cat "$trial/output" >&2; fail 'valid client commit failed'; return 1; }
        [ "$(cat "$trial/clients")" = "$K1"$'\n'"$K2" ] || { fail 'success did not store the keys'; return 1; }
        [ "$(cat "$trial/runtime")" = $'10.77.1.2/32\n10.77.1.3/32' ] || { fail 'success did not route the keys by address'; return 1; }
        cmp -s "$trial/startup.before" "$trial/startup" || { fail 'success lost the start.sh block'; return 1; }
        [ ! -e "$snap" ] || { fail 'success left the snapshot behind'; return 1; }
    elif [ "$scenario" = rollback ] || [ "$scenario" = retry ]; then
        [ "$rc" -ne 0 ] && [ -f "$trial/fault-fired" ] || { fail 'unresolved rollback reported success'; return 1; }
        [ -f "$snap/recovery-required" ] && [ -f "$snap/ready" ] || {
            cat "$trial/output" "$trial/unexpected" >&2
            fail 'unresolved rollback did not retain a protected host snapshot'; return 1;
        }
        command tar -xOf "$snap/warp.tar" warp/clients.list | cmp -s "$trial/clients.before" - \
            || { fail 'rollback lost the snapshot of the selection'; return 1; }
        if [ "$scenario" = retry ]; then
            # Следующая смена клиентов сначала довозит откат, затем применяет новый выбор.
            touch "$trial/retry-armed"
            rc=0; awg_commit_clients amnezia-awg "$K3" > "$trial/output" 2>&1 || rc=$?
            [ "$rc" = 0 ] || { cat "$trial/output" >&2; fail 'commit after an unresolved rollback failed'; return 1; }
            [ "$(cat "$trial/clients")" = "$K3" ] && [ "$(cat "$trial/runtime")" = 10.77.1.4/32 ] \
                || { fail 'commit after an unresolved rollback did not apply'; return 1; }
            [ ! -e "$snap" ] || { fail 'resolved rollback left the snapshot behind'; return 1; }
        fi
    else
        [ -f "$trial/fault-fired" ] || { cat "$trial/output" >&2; fail "$scenario fault was not reached"; return 1; }
        [ "$rc" -ne 0 ] || { fail "$scenario failure was reported as success"; return 1; }
        for file in clients runtime startup; do
            cmp -s "$trial/$file.before" "$trial/$file" || {
                cat "$trial/output" "$trial/unexpected" >&2
                fail "$scenario failure did not restore $file"; return 1;
            }
        done
        [ ! -e "$snap" ] || { fail "$scenario rollback left the snapshot behind"; return 1; }
    fi
    [ ! -s "$trial/unexpected" ] || { cat "$trial/unexpected" >&2; return 1; }
)

run_cleanup_failure() (
    new_case cleanup-rpc-failure
    # Restore the production helper hidden by the default fixture stub.
    eval "$production_cleanup_rules"
    AWG_SELECTED_IPS=()
    docker() {
        if [ ! -f "$trial/cleanup-attempt" ]; then
            touch "$trial/cleanup-attempt"
            return 75
        fi
        touch "$trial/continued-after-cleanup-error"
        return 0
    }
    local rc=0
    awg_cleanup_rules > "$trial/output" 2>&1 || rc=$?
    [ "$rc" -ne 0 ] || { fail 'cleanup swallowed Docker failure'; return 1; }
    rm -f "$trial/cleanup-attempt"
    rc=0
    awg_apply_rules > "$trial/output" 2>&1 || rc=$?
    [ "$rc" -ne 0 ] || { fail 'empty client selection swallowed cleanup failure'; return 1; }
    [ ! -f "$trial/continued-after-cleanup-error" ] || { fail 'apply continued after failed cleanup'; return 1; }
)

run_uninstall_guard() (
    new_case "guard-$1" "$1"
    awg_discover_containers() { AWG_ALL_CONTAINERS=(amnezia-awg); }
    local protected="" rc operation_fd mode file
    case "$scenario" in
        discovery)
            # Docker не ответил: неизвестно, что в контейнерах.
            awg_discover_containers() { AWG_ALL_CONTAINERS=(); return 2; } ;;
        lock)
            exec {operation_fd}>"$WARP_DIR/.awg-operation.lock" || return 1
            flock -n "$operation_fd" || return 1 ;;
        recovery)
            protected="$HOST_WGCF_BAK_ROOT/amnezia-awg"
            mkdir -p "$protected"
            touch "$protected/recovery-required" ;;
        candidate)
            protected="$AWG_WGCF_BASE/amnezia-awg/.candidate.synthetic"
            mkdir -p "$protected" ;;
    esac
    if [ -n "$protected" ]; then printf 'synthetic recovery identity\n' > "$protected/wgcf-account.toml"; fi
    for mode in awg full; do
        rc=0
        if [ "$mode" = full ]; then
            ( full_uninstall ) > "$trial/output" 2>&1 <<'INPUT' || rc=$?
y

INPUT
        else
            uninstall_awg all > "$trial/output" 2>&1 || rc=$?
        fi
        [ "$rc" -ne 0 ] || { fail "$mode uninstall bypassed $scenario guard"; return 1; }
        for file in "$WARP_CONF" "$WARP_LOG" "$GOWARP_BIN" "$trial/containers/amnezia-awg/warp.conf"; do
            [ -s "$file" ] || { fail "$scenario guard lost $file"; return 1; }
        done
        assert_preserved_host_files || return 1
        if [ -n "$protected" ]; then
            [ "$(cat "$protected/wgcf-account.toml" 2>/dev/null)" = 'synthetic recovery identity' ] || {
                fail "$scenario recovery identity was deleted"; return 1;
            }
        fi
        [ ! -s "$trial/events" ] || { cat "$trial/events" >&2; fail "$scenario guard ran service/container mutations"; return 1; }
    done
    [ "$scenario" != lock ] || exec {operation_fd}>&-
)

# goAmnezia (или человек) убрал контейнеры amnezia-awg*: удаление доходит до конца и чистит хост. Данные
# восстановления контейнеров, которых больше нет, удаление не останавливают: их устройства снимаются.
run_uninstall_no_containers() (
    new_case "no-containers-$1" "$1"
    awg_discover_containers() { AWG_ALL_CONTAINERS=(); return 1; }
    local retired="$trial/retired"
    awg_retire_unused_account() { printf '%s\n' "$1" >> "$retired"; return 0; }
    mkdir -p "$HOST_WGCF_BAK_ROOT/amnezia-awg2" "$AWG_WGCF_BASE/amnezia-awg2/.candidate.synthetic"
    touch "$HOST_WGCF_BAK_ROOT/amnezia-awg2/recovery-required"
    printf 'old device\n' > "$HOST_WGCF_BAK_ROOT/amnezia-awg2/wgcf-account.toml"
    printf 'candidate\n' > "$AWG_WGCF_BASE/amnezia-awg2/.candidate.synthetic/wgcf-account.toml"
    printf 'device\n' > "$AWG_WGCF_BASE/amnezia-awg2/wgcf-account.toml"
    local rc=0
    if [ "$scenario" = full ]; then
        ( full_uninstall ) > "$trial/output" 2>&1 <<'INPUT' || rc=$?
y

INPUT
    else
        uninstall_awg all > "$trial/output" 2>&1 || rc=$?
    fi
    [ "$rc" = 0 ] || { cat "$trial/output" "$trial/unexpected" >&2; fail "$scenario uninstall without containers failed"; return 1; }
    local file
    for file in "$WGCF_BIN" "$WGCF_PROFILE" "$AWG_WATCH_BIN" "$AWG_WATCH_SERVICE" "$AWG_WGCF_BASE" "$HOST_WGCF_BAK_ROOT"; do
        [ ! -e "$file" ] || { fail "$scenario uninstall left $file"; return 1; }
    done
    for file in "$AWG_WGCF_BASE/amnezia-awg2/wgcf-account.toml" "$AWG_WGCF_BASE/amnezia-awg2/.candidate.synthetic/wgcf-account.toml" \
                "$HOST_WGCF_BAK_ROOT/amnezia-awg2/wgcf-account.toml" "$WGCF_ACCOUNT"; do
        grep -qxF "$file" "$retired" || { fail "$scenario uninstall did not retire $file"; return 1; }
    done
    if [ "$scenario" = full ]; then
        [ ! -e "$WARP_DIR" ] && [ ! -e "$GOWARP_BIN" ] || { fail 'full uninstall kept the manager without containers'; return 1; }
    fi
    grep -q 'systemctl stop warp-bot' "$trial/events" || { fail "$scenario uninstall did not stop the bot"; return 1; }
    ! grep -q '^docker ' "$trial/events" || { cat "$trial/events" >&2; fail "$scenario uninstall touched Docker without containers"; return 1; }
    [ ! -s "$trial/unexpected" ] || { cat "$trial/unexpected" >&2; return 1; }
)

run_full_uninstall_lock_lifetime() (
    new_case full-lock-lifetime
    awg_discover_containers() { AWG_ALL_CONTAINERS=(amnezia-awg); }
    eval "$(declare -f rm | sed '1s/^rm /fixture_rm /')"
    rm() {
        local arg
        for arg in "$@"; do
            if [ "$arg" = "$WARP_DIR" ]; then
                touch "$trial/manager-removal-checked"
                (
                    local probe_fd
                    exec {probe_fd}>"$WARP_DIR/.awg-operation.lock" || exit 1
                    if flock -n "$probe_fd"; then touch "$trial/lock-released-too-soon"; fi
                ) || return 1
            fi
        done
        fixture_rm "$@"
    }
    local rc=0
    ( full_uninstall ) > "$trial/output" 2>&1 <<'INPUT' || rc=$?
y

INPUT
    [ "$rc" = 0 ] || { cat "$trial/output" "$trial/unexpected" >&2; fail 'successful full uninstall failed'; return 1; }
    [ -f "$trial/manager-removal-checked" ] && [ ! -e "$WARP_DIR" ] || { fail 'manager removal was not exercised'; return 1; }
    [ ! -f "$trial/lock-released-too-soon" ] || { fail 'operation lock released before manager removal'; return 1; }
    [ ! -s "$trial/unexpected" ] || { cat "$trial/unexpected" >&2; return 1; }
)

run_test() {
    local label="$1"; shift
    checks=$((checks + 1))
    if "$@"; then
        echo "network lifecycle: $label OK"
    else
        failures=$((failures + 1))
        echo "FAIL: network lifecycle: $label" >&2
    fi
}

for phase in success save apply patch rollback retry; do
    run_test "client commit $phase" run_client_commit "$phase"
done
for state in offline_survivor offline_unknown; do
    run_test "$state retains shared resources" run_offline_case "$state"
done
for fault in start_fail cleanup_fail; do
    run_test "uninstall propagates $fault" run_incomplete_uninstall awg "$fault"
    run_test "full uninstall retains manager after $fault" run_incomplete_uninstall full "$fault"
done
run_test 'cleanup error stops empty selection' run_cleanup_failure
for guard in discovery lock recovery candidate; do
    run_test "uninstall guard $guard (AWG and full)" run_uninstall_guard "$guard"
done
run_test 'full uninstall holds lock through manager removal' run_full_uninstall_lock_lifetime
for mode in awg full; do
    run_test "uninstall without amnezia-awg containers completes ($mode)" run_uninstall_no_containers "$mode"
done
[ "$failures" -eq 0 ] || exit 1
echo "network_lifecycle_checks: OK ($checks cases)"
