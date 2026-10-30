#!/usr/bin/env bash
# Автопроверка и самолечение (2.5): Docker, warp-cli, сеть и systemd заменены заглушками.
# Каждый сценарий идёт в подоболочке, чтобы подмены функций и состояние не протекали.
set -o pipefail

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    echo "health_checks: skipped (requires Bash >=4; current $BASH_VERSION)"; exit 0
fi
if ! command -v flock >/dev/null 2>&1; then
    echo 'health_checks: skipped (requires flock)'; exit 0
fi
script="${1:-warp.sh}"
[ -f "$script" ] || { echo "FAIL: script not found: $script" >&2; exit 1; }
# shellcheck disable=SC1090
source <(awk '/^#  ENTRY POINT/{exit} {print}' "$script")
root=$(mktemp -d) || exit 1
trap 'rm -rf "$root"' EXIT
export HEALTH_NO_TIMEOUT=1
failures=0; checks=0

sleep() { :; }
fail() { echo "FAIL: $*" >&2; return 1; }
ev() { printf '%s\n' "$*" >> "$trial/events"; }
has_ev() { grep -qxF -- "$1" "$trial/events"; }
no_ev_like() { ! grep -q -- "$1" "$trial/events"; }
log_action() { printf '%s\n' "$*" >> "$trial/log"; }
probe() { printf '%s\n' "$@" >> "$trial/probes"; }
# Выбор WARP+ для 3X-UI: в файл и в переменные, как после source в health_tick.
plus_choice() { save_config_val WARP_KEY_MODE_3XUI "$1" ACTIVE_KEY_NAME_3XUI "$2"; }
# Блокировка операций 3X-UI свободна: служба не забыла её отпустить.
lock_free_3xui() {
    local lk rc=0
    exec {lk}>"$WARP_DIR/.3xui-operation.lock" || return 1
    flock -n "$lk" || rc=1
    exec {lk}>&-
    return "$rc"
}

new_case() {
    trial="$root/$1"; mkdir -p "$trial"
    : > "$trial/events"; : > "$trial/log"; : > "$trial/probes"; : > "$trial/traces"
    WARP_DIR="$trial/manager"; mkdir -p "$WARP_DIR"
    WARP_CONF="$WARP_DIR/config"; WARP_LOG="$trial/log"; WARP_KEYS="$WARP_DIR/keys.list"
    HEALTH_SERVICE="$trial/gowarp-health.service"; GOWARP_BIN="$trial/gowarp"
    printf 'CONFIG_VERSION="%s"\nMODE="%s"\nSOCKS_PORT="40000"\nWARP_WANTED_3XUI="%s"\nWARP_KEY_MODE_3XUI="free"\nACTIVE_KEY_NAME_3XUI=""\n' \
        "$CONFIG_SCHEMA_VERSION" "${2:-both}" "${3:-on}" > "$WARP_CONF"
    # shellcheck disable=SC1090
    source "$WARP_CONF"
    HEALTH_LEVEL=(); HEALTH_NEXT=(); HEALTH_BACKOFF=(); HEALTH_BAD=()
    HEALTH_PROBE_AT=(); HEALTH_PROBE_FAILS=(); HEALTH_RX0=(); HEALTH_WARNED=(); HEALTH_STATUS_BAD=()
    HEALTH_LAST_TOUCH=0; HEALTH_PROBE_EVERY=60; HEALTH_NOW=1000
    HEALTH_KIND_NEXT=(); HEALTH_KIND_BACKOFF=(); HEALTH_RTT_SLOW=(); HEALTH_RTT_WARNED=(); AWG_TOOLS_FAILED_AT=()
    HEALTH_BLOCK_NEXT=(); PATCH_RC=0; KERNEL_WG=0
    awg_patch_start_sh() { ev "patch:$CONTAINER"; return "$PATCH_RC"; }
    # Ядро создаёт интерфейсы wireguard (0) или нет (1, LXC/OpenVZ).
    awg_kernel_wireguard_ok() { ev "kernel-wg:$1"; return "$KERNEL_WG"; }
    TOOLS_RC=0
    awg_prepare_warp_tools() { ev tools; return "$TOOLS_RC"; }
    HEALTH_TRACE_3XUI=""; HEALTH_PLUS_FREE_3XUI=0; HEALTH_PLUS_BACKOFF_3XUI=0; HEALTH_PLUS_NEXT_3XUI=0
    PLUS_LAST_REASON=""
    unset THREEXUI_LOCK_HELD
    is_warp_installed_3xui() { return 1; }
    awg_load_container_data() { ev "load:$CONTAINER"; return 0; }
    awg_warp_up() { ev "up:${1:-restart}"; return 0; }
    awg_load_clients() { return 0; }
    awg_apply_rules() { ev rules; return 0; }
    systemctl() { ev "systemctl $*"; return 0; }
    _rekey_3xui_core_locked() { ev rekey; return 0; }
    # Настоящее применение читает keys.list и чистит журналы Cloudflare — только заглушка.
    apply_warp_plus_key_3xui() { ev apply; return 0; }
}

# Ответ зонда контейнера берётся из очереди: одна строка на один docker exec -i.
docker() {
    case "$1" in
        ps) printf '%s\n' amnezia-awg ;;
        exec)
            if [ "$2" = -i ]; then
                cat > /dev/null
                local line=""
                IFS= read -r line < "$trial/probes"
                sed -i '1d' "$trial/probes"
                ev "probe:$3"
                printf '%s\n' "$line"
            else
                case "$*" in
                    *"sed -i"*) local cmd="${*: -1}"; ev "rotate:${cmd##*Endpoint = }" ;;
                    *"sed -n"*) echo "162.159.192.1:2408" ;;
                esac
            fi ;;
    esac
}
warp-cli() {
    ev "cli:$*"
    case "$*" in *" status") cat "$trial/status" 2>/dev/null ;; esac
    return 0
}
# API Cloudflare: HTTP-код из $trial/api; без файла связи нет (000, как у настоящего curl).
curl() {
    ev "curl:$*"
    if [ -f "$trial/api" ]; then cat "$trial/api"; else echo 000; return 7; fi
}
# Результат проверки трафика 3X-UI из очереди: free/plus — прошла, x — нет.
verify_warp_plus_3xui() {
    local r=""
    IFS= read -r r < "$trial/traces"
    sed -i '1d' "$trial/traces"
    ev "trace:${r:-none}"
    case "$r" in free|plus) echo "$r" ;; esac
}

case_awg_down_heals() {
    new_case awg-down amnezia
    probe "down 0 1000 0 0 0 900 0" "down 0 1000 0 0 0 900 0" "up 1001 1003 10 0 0 900 0"
    health_tick_awg
    has_ev "up:ensure" || fail "down interface was not raised" || return 1
    grep -q 'подъём туннеля, было: интерфейс warp пропал — помогло' "$trial/log" || fail "heal not logged" || return 1
    [ -z "${HEALTH_BAD[awg:amnezia-awg]:-}" ] || fail "state not cleared after success"
}

case_awg_disabled_respected() {
    new_case awg-disabled amnezia
    probe "disabled"
    health_tick_awg
    no_ev_like '^up:' || fail "manual AmneziaWG off was overridden"
}

case_awg_start_grace() {
    new_case awg-grace amnezia
    probe "down 0 1000 0 0 0 10 0"
    health_tick_awg
    no_ev_like '^up:' || fail "healed a container that started 10 s ago"
}

case_awg_stale_backoff_escalation() {
    new_case awg-stale amnezia
    probe "up 700 1000 5 0 0 100 0" "up 700 1000 5 0 0 100 0" \
          "up 0 1003 5 0 0 100 0" "up 0 1006 5 0 0 100 0" "up 0 1009 5 0 0 100 0" \
          "up 0 1012 5 0 0 100 0" "up 0 1015 5 0 0 100 0" "up 0 1018 5 0 0 100 0"
    health_tick_awg
    has_ev "up:restart" || fail "stale tunnel not restarted" || return 1
    [ "${HEALTH_BACKOFF[awg:amnezia-awg]:-}" = 10 ] || fail "backoff did not start at 10 s" || return 1
    [ "${HEALTH_LEVEL[awg:amnezia-awg]:-}" = 2 ] || fail "level not raised after failed heal" || return 1
    : > "$trial/events"
    HEALTH_NOW=1005; probe "up 700 1005 5 0 0 100 0"
    health_tick_awg
    no_ev_like '^up:' || fail "healed during backoff" || return 1
    HEALTH_NOW=1011; probe "up 700 1011 5 0 0 100 0" "up 700 1011 5 0 0 100 0" "up 1012 1014 9 0 0 100 0"
    health_tick_awg
    has_ev "up:restart" || fail "second attempt missing after backoff" || return 1
    grep -q 'повторный перезапуск туннеля.* — помогло' "$trial/log" || fail "escalated heal not logged"
}

case_awg_port_rotation() {
    new_case awg-port amnezia
    HEALTH_LEVEL[awg:amnezia-awg]=3
    probe "up 0 1000 0 0 0 100 0" "up 0 1000 0 0 0 100 0" "up 1001 1003 4 0 0 100 0"
    health_tick_awg
    grep -q '^rotate:162.159.192.1:500' "$trial/events" || fail "port 2408 was not rotated to 500" || return 1
    grep -q 'смена порта Cloudflare, было: handshake не проходит — помогло' "$trial/log" || fail "rotation not logged"
}

case_awg_rules_restored() {
    new_case awg-rules amnezia
    probe "up 995 1000 10 0 2 100 0" "up 995 1000 10 0 2 100 0"
    health_tick_awg
    has_ev rules || fail "missing client rules not restored" || return 1
    no_ev_like '^up:' || fail "tunnel restarted just for rules"
}

case_awg_path_issue_no_restart() {
    new_case awg-path amnezia
    local t
    for t in 1000:100 1010:200 1020:300; do
        HEALTH_NOW=${t%%:*}
        probe "up $((HEALTH_NOW - 5)) $HEALTH_NOW ${t##*:} 1 1 100 2"
        health_tick_awg
    done
    no_ev_like '^up:' || fail "restarted although bytes still arrive" || return 1
    grep -q 'туннель жив, но проверка трафика не проходит' "$trial/log" || fail "path issue not reported"
}

case_awg_dead_traffic_restart() {
    new_case awg-dead amnezia
    local n
    for n in 1000 1010; do HEALTH_NOW=$n; probe "up $((n - 5)) $n 100 1 1 100 2"; health_tick_awg; done
    HEALTH_NOW=1020; probe "up 1015 1020 100 1 1 100 2" "up 1015 1020 100 1 1 100 0" "up 1021 1023 150 1 1 100 0"
    health_tick_awg
    has_ev "up:restart" || fail "tunnel with no incoming bytes not healed" || return 1
    grep -q 'трафик не идёт, байты не приходят — помогло' "$trial/log" || fail "heal reason not logged"
}

case_awg_lock_skips() {
    new_case awg-lock amnezia
    probe "down 0 1000 0 0 0 900 0"
    local lk
    exec {lk}>"$WARP_DIR/.awg-operation.lock"
    flock -n "$lk" || { fail "test could not take the lock"; return 1; }
    health_tick_awg
    exec {lk}>&-
    has_ev "probe:amnezia-awg" || fail "read-only probe must not wait for the operation lock" || return 1
    no_ev_like '^up:' || fail "healed during an AmneziaWG operation"
}

case_3xui_manual_off_respected() {
    new_case x-off 3xui off
    is_warp_installed_3xui() { return 0; }
    printf 'Status update: Disconnected\nReason: Manual Disconnection\n' > "$trial/status"
    health_tick_3xui
    no_ev_like '^cli:' || fail "manual 3X-UI off was overridden"
}

case_3xui_disconnected_reconnects() {
    new_case x-disc 3xui on
    is_warp_installed_3xui() { return 0; }
    printf 'Status update: Disconnected\nReason: Settings Changed\n' > "$trial/status"
    printf 'free\n' > "$trial/traces"
    health_tick_3xui
    no_ev_like 'cli:--accept-tos connect' || fail "reacted to a single Disconnected sample" || return 1
    HEALTH_NOW=1010; health_tick_3xui
    has_ev "cli:--accept-tos mode proxy" && has_ev "cli:--accept-tos proxy port 40000" && has_ev "cli:--accept-tos connect" \
        || fail "level 1 reconnect sequence missing" || return 1
    no_ev_like 'disconnect' || fail "level 1 must not disconnect" || return 1
    grep -q 'переподключение, было: Settings Changed — помогло' "$trial/log" || fail "reconnect not logged"
}

case_3xui_silent_failure_escalates() {
    new_case x-silent 3xui on
    is_warp_installed_3xui() { return 0; }
    printf 'Status update: Connected\nNetwork: healthy\n' > "$trial/status"
    HEALTH_PROBE_EVERY=0
    printf 'x\nx\nx\nx\nx\nx\nx\nx\n' > "$trial/traces"
    local n
    for n in 1000 1010 1020; do HEALTH_NOW=$n; health_tick_3xui; done
    has_ev "cli:--accept-tos connect" || fail "silent traffic failure not healed" || return 1
    [ "${HEALTH_LEVEL[3xui]:-}" = 2 ] || fail "level not raised after failed heal" || return 1
    : > "$trial/events"; printf 'x\nfree\n' > "$trial/traces"
    HEALTH_NOW=1031; health_tick_3xui
    has_ev "cli:--accept-tos disconnect" || fail "level 2 did not disconnect first" || return 1
    grep -q 'разрыв и новое подключение, было: трафик не идёт — помогло' "$trial/log" || fail "level 2 not logged"
}

case_3xui_registration_missing_rekeys() {
    new_case x-reg 3xui on
    is_warp_installed_3xui() { return 0; }
    printf 'Status update: Unable\nReason: Registration Missing due to: Daemon Startup\n' > "$trial/status"
    printf 'free\n' > "$trial/traces"
    local n
    for n in 1000 1010 1020 1030 1040; do HEALTH_NOW=$n; health_tick_3xui; done
    no_ev_like '^rekey' || fail "re-registered on a transient startup state" || return 1
    HEALTH_NOW=1050; health_tick_3xui
    has_ev rekey || fail "missing registration was not re-created" || return 1
    no_ev_like 'cli:--accept-tos connect' || fail "connect alone cannot fix a missing registration"
}

case_3xui_recovery_and_lock_respected() {
    new_case x-guard 3xui on
    is_warp_installed_3xui() { return 0; }
    printf 'Status update: Disconnected\n' > "$trial/status"
    touch "$WARP_DIR/3xui-recovery"
    health_tick_3xui
    no_ev_like '^cli:' || fail "touched 3X-UI during recovery" || return 1
    rm -f "$WARP_DIR/3xui-recovery"
    local lk
    exec {lk}>"$WARP_DIR/.3xui-operation.lock"
    flock -n "$lk" || { fail "test could not take the lock"; return 1; }
    HEALTH_NOW=1000; health_tick_3xui; HEALTH_NOW=1010; health_tick_3xui
    exec {lk}>&-
    ! grep -qE 'cli:--accept-tos (connect|mode|proxy|disconnect)' "$trial/events" || fail "changed 3X-UI during an operation"
}

case_unmigrated_config_skipped() {
    new_case unmigrated both on
    sed -i 's/^CONFIG_VERSION=.*/CONFIG_VERSION="4"/' "$WARP_CONF"
    health_tick_3xui() { ev tick3xui; }
    health_tick_awg() { ev tickawg; }
    health_tick
    no_ev_like '^tick' || fail "health ran on a config the new version has not migrated yet"
}

case_service_install_idempotent() {
    new_case service both on
    health_service_install || fail "install failed" || return 1
    grep -qxF "ExecStart=$GOWARP_BIN --health-daemon" "$HEALTH_SERVICE" || fail "unit has wrong ExecStart" || return 1
    grep -qx 'Restart=always' "$HEALTH_SERVICE" || fail "unit must restart itself" || return 1
    has_ev "systemctl daemon-reload" && has_ev "systemctl enable --now gowarp-health.service" || fail "unit not enabled" || return 1
    : > "$trial/events"
    health_service_install || return 1
    no_ev_like 'daemon-reload' || fail "unchanged unit reloaded systemd" || return 1
    health_service_remove
    [ ! -e "$HEALTH_SERVICE" ] || fail "unit not removed" || return 1
    has_ev "systemctl disable --now gowarp-health.service" || fail "unit not disabled"
}

case_wgcf_pinned_replacement() {
    new_case wgcf both on
    WGCF_BIN="$trial/wgcf"
    uname() { echo x86_64; }
    printf 'good-build\n' > "$trial/good"
    WGCF_SHA256_AMD64=$(file_sha256 "$trial/good")
    printf 'old-2.2.30\n' > "$WGCF_BIN"; chmod 755 "$WGCF_BIN"
    cp "$trial/good" "$trial/payload"
    curl() {
        local out=""
        while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done
        ev download; cp "$trial/payload" "$out"
    }
    awg_install_wgcf || fail "replacement failed" || return 1
    cmp -s "$WGCF_BIN" "$trial/good" || fail "old wgcf was not replaced" || return 1
    : > "$trial/events"
    awg_install_wgcf || return 1
    no_ev_like download || fail "matching wgcf was downloaded again" || return 1
    printf 'old-2.2.30\n' > "$WGCF_BIN"; printf 'tampered\n' > "$trial/payload"
    if awg_install_wgcf 2>/dev/null; then fail "checksum mismatch accepted"; return 1; fi
    grep -qx 'old-2.2.30' "$WGCF_BIN" || fail "checksum mismatch destroyed the previous binary"
}

case_migration_adds_intent() {
    new_case migrate both on
    WARP_KEYS="$WARP_DIR/keys.list"; : > "$WARP_KEYS"
    printf 'CONFIG_VERSION="4"\nSOCKS_PORT="41234"\nMODE="both"\nBOT_CHAT_ID="555"\nBOT_USER_IDS=""\nWARP_KEY_MODE_3XUI="free"\nWARP_KEY_MODE_AWG="plus"\nACTIVE_KEY_NAME_3XUI=""\nACTIVE_KEY_NAME_AWG="k"\nINSTALLED_VERSION="2.4.1"\n' > "$WARP_CONF"
    migrate_config_if_needed >/dev/null 2>&1 || fail "migration failed" || return 1
    grep -qx 'CONFIG_VERSION="5"' "$WARP_CONF" || fail "schema not bumped to 5" || return 1
    grep -qx 'WARP_WANTED_3XUI="on"' "$WARP_CONF" || fail "3X-UI intent not added" || return 1
    grep -qx 'SOCKS_PORT="41234"' "$WARP_CONF" && grep -qx 'BOT_CHAT_ID="555"' "$WARP_CONF" \
        && grep -qx 'ACTIVE_KEY_NAME_AWG="k"' "$WARP_CONF" || fail "migration lost settings" || return 1
    [ "$(find "$WARP_DIR" -name 'config.v*.bak' | wc -l | tr -d ' ')" = 1 ] || fail "backup missing" || return 1
    migrate_config_if_needed >/dev/null 2>&1
    [ "$(grep -c '^WARP_WANTED_3XUI=' "$WARP_CONF")" = 1 ] || fail "migration is not idempotent"
}

case_manual_stop_intent_first() {
    new_case stop 3xui on
    is_warp_installed_3xui() { return 0; }
    is_warp_running_3xui() { return 0; }
    warp-cli() { ev "cli:$* wanted=$(sed -n 's/^WARP_WANTED_3XUI=//p' "$WARP_CONF")"; return 0; }
    stop_warp_3xui < /dev/null > /dev/null 2>&1
    has_ev 'cli:--accept-tos disconnect wanted="off"' || fail "intent must be saved before disconnecting"
}

case_awg_manual_off_race() {
    new_case awg-race amnezia
    probe "down 0 1000 0 0 0 900 0" "disabled"
    health_tick_awg
    no_ev_like '^up:' || fail "raised a tunnel that was switched off while the check was running"
}

case_3xui_manual_off_race() {
    new_case x-race 3xui on
    is_warp_installed_3xui() { return 0; }
    printf 'Status update: Disconnected\nReason: Settings Changed\n' > "$trial/status"
    health_tick_3xui
    sed -i 's/^WARP_WANTED_3XUI=.*/WARP_WANTED_3XUI="off"/' "$WARP_CONF"
    HEALTH_NOW=1010; health_tick_3xui
    ! grep -qE 'cli:--accept-tos (connect|mode|proxy)' "$trial/events" || fail "reconnected right after a manual off"
}

# Тихий обрыв на четвёртой ступени: три провала проверки трафика подряд, затем лечение.
silent_level4_3xui() {
    is_warp_installed_3xui() { return 0; }
    printf 'Status update: Connected\nNetwork: healthy\n' > "$trial/status"
    HEALTH_PROBE_EVERY=0; HEALTH_LEVEL[3xui]=4
    local n
    for n in 1000 1010 1020; do HEALTH_NOW=$n; health_tick_3xui; done
}

case_3xui_api_down_keeps_registration() {
    new_case x-api-down 3xui on
    printf 'x\nx\nx\nx\nx\nx\nx\nx\n' > "$trial/traces"
    silent_level4_3xui
    grep -q '^curl:.*--connect-timeout 4.*--max-time 6.*https://api\.cloudflareclient\.com/' "$trial/events" \
        || fail "Cloudflare API was not checked with short timeouts" || return 1
    ! grep -qE '^curl:.*(--proxy|socks)' "$trial/events" || fail "API check must bypass the WARP proxy" || return 1
    no_ev_like '^rekey' || fail "re-registered while the Cloudflare API is unreachable" || return 1
    has_ev "systemctl restart warp-svc" || fail "warp-svc was not restarted instead" || return 1
    grep -q 'перезапуск службы warp-svc (API Cloudflare недоступен, регистрацию не трогаем), было: трафик не идёт — не помогло' "$trial/log" \
        || fail "API outage not logged" || return 1
    [ "${HEALTH_LEVEL[3xui]:-}" = 4 ] && [ "${HEALTH_BACKOFF[3xui]:-}" = 10 ] || fail "escalation did not continue normally"
}

case_3xui_api_up_rekeys() {
    new_case x-api-up 3xui on
    printf '404\n' > "$trial/api"
    printf 'x\nx\nx\nfree\n' > "$trial/traces"
    silent_level4_3xui
    has_ev rekey || fail "level 4 did not re-register although the API answered" || return 1
    no_ev_like 'systemctl restart' || fail "restarted warp-svc instead of re-registering" || return 1
    grep -q 'новая регистрация, было: трафик не идёт — помогло' "$trial/log" || fail "re-registration not logged"
}

case_3xui_register_skips_api() {
    new_case x-api-reg 3xui on
    is_warp_installed_3xui() { return 0; }
    printf 'Status update: Unable\nReason: Registration Missing\n' > "$trial/status"
    printf 'free\n' > "$trial/traces"
    local n
    for n in 1000 1010 1020 1030 1040 1050; do HEALTH_NOW=$n; health_tick_3xui; done
    has_ev rekey || fail "missing registration must be re-created even without the API" || return 1
    no_ev_like '^curl:' || fail "checked the API although there is no registration to lose"
}

# Перевыпуск, как настоящий, при отказе ключа пишет в конфиг Free и забывает ключ.
rekey_drops_plus() {
    _rekey_3xui_core_locked() {
        ev "rekey:${WARP_KEY_MODE_3XUI}:${ACTIVE_KEY_NAME_3XUI}"
        save_config_val WARP_KEY_MODE_3XUI free ACTIVE_KEY_NAME_3XUI ""
        return 1
    }
}

case_3xui_rekey_keeps_plus_choice() {
    new_case x-keep-plus 3xui on
    plus_choice plus k
    rekey_drops_plus
    printf '200\n' > "$trial/api"
    printf 'x\nx\nx\nfree\n' > "$trial/traces"
    silent_level4_3xui
    has_ev 'rekey:plus:k' || fail "re-registration did not get the WARP+ choice" || return 1
    grep -qx 'WARP_KEY_MODE_3XUI="plus"' "$WARP_CONF" && grep -qx 'ACTIVE_KEY_NAME_3XUI="k"' "$WARP_CONF" \
        || fail "service dropped the WARP+ choice after re-registration" || return 1
    [ "$WARP_KEY_MODE_3XUI" = plus ] && [ "$ACTIVE_KEY_NAME_3XUI" = k ] || fail "WARP+ choice not restored in memory" || return 1
    grep -q 'HEALTH 3X-UI: после новой регистрации WARP+ (k) не подтвердился; выбор WARP+ сохранён, служба повторит ключ позже' "$trial/log" \
        || fail "kept WARP+ choice not logged" || return 1
    grep -q '^last_event=3X-UI: WARP+ (k)' "$WARP_DIR/health.last" || fail "WARP+ warning is not the last header event"
}

case_3xui_rekey_free_untouched() {
    new_case x-keep-free 3xui on
    rekey_drops_plus
    # В файле выбран Free, а переменные тика ещё помнят WARP+.
    WARP_KEY_MODE_3XUI=plus; ACTIVE_KEY_NAME_3XUI=k
    printf '200\n' > "$trial/api"
    printf 'x\nx\nx\nfree\n' > "$trial/traces"
    silent_level4_3xui
    has_ev 'rekey:free:' || fail "re-registration used a stale WARP+ choice" || return 1
    grep -qx 'WARP_KEY_MODE_3XUI="free"' "$WARP_CONF" && grep -qx 'ACTIVE_KEY_NAME_3XUI=""' "$WARP_CONF" \
        || fail "service changed the Free choice" || return 1
    ! grep -q 'WARP+' "$trial/log" || fail "restored or logged a WARP+ choice nobody made"
}

# WARP+ выбран ключом k, WARP подключён; ответы проверки трафика — из очереди.
plus_setup_3xui() {
    is_warp_installed_3xui() { return 0; }
    printf 'Status update: Connected\nNetwork: healthy\n' > "$trial/status"
    plus_choice plus k
}

case_3xui_plus_retry_confirms() {
    new_case x-plus-ok 3xui on
    plus_setup_3xui
    apply_warp_plus_key_3xui() { ev "apply:${WARP_KEY_MODE_3XUI}:${ACTIVE_KEY_NAME_3XUI}:${THREEXUI_LOCK_HELD:-}"; PLUS_LAST_REASON=ok; }
    printf 'free\nfree\n' > "$trial/traces"
    HEALTH_NOW=1000; health_tick_3xui; HEALTH_NOW=1060; health_tick_3xui
    no_ev_like '^apply' || fail "re-applied the key after only two Free probes" || return 1
    printf 'free\nplus\n' > "$trial/traces"
    HEALTH_NOW=1120; health_tick_3xui
    [ "$(grep -c '^apply:' "$trial/events")" = 1 ] || fail "key not re-applied exactly once after three Free probes" || return 1
    has_ev 'apply:plus:k:1' || fail "key re-applied without the 3X-UI lock" || return 1
    grep -q 'HEALTH 3X-UI: WARP+ (k) снова подтверждён' "$trial/log" || fail "confirmed WARP+ not logged" || return 1
    grep -q '^last_event=3X-UI: WARP+ (k) снова подтверждён' "$WARP_DIR/health.last" || fail "header does not show the confirmation" || return 1
    grep -qx 'WARP_KEY_MODE_3XUI="plus"' "$WARP_CONF" && grep -qx 'ACTIVE_KEY_NAME_3XUI="k"' "$WARP_CONF" || fail "WARP+ choice changed" || return 1
    [ "$HEALTH_PLUS_FREE_3XUI" = 0 ] && [ "$HEALTH_PLUS_BACKOFF_3XUI" = 0 ] || fail "retry state not reset after success" || return 1
    [ -z "${THREEXUI_LOCK_HELD:-}" ] && lock_free_3xui || fail "3X-UI lock not released after success"
}

case_3xui_plus_retry_backoff() {
    new_case x-plus-fail 3xui on
    plus_setup_3xui
    apply_warp_plus_key_3xui() {
        ev apply; PLUS_LAST_REASON=free
        save_config_val WARP_KEY_MODE_3XUI free ACTIVE_KEY_NAME_3XUI ""
        return 2
    }
    printf 'free\nfree\nfree\nfree\nfree\nfree\nfree\nfree\n' > "$trial/traces"
    local n
    for n in 1000 1060 1120; do HEALTH_NOW=$n; health_tick_3xui; done
    [ "$(grep -c '^apply$' "$trial/events")" = 1 ] || fail "key not re-applied once after three Free probes" || return 1
    grep -qx 'WARP_KEY_MODE_3XUI="plus"' "$WARP_CONF" && grep -qx 'ACTIVE_KEY_NAME_3XUI="k"' "$WARP_CONF" \
        || fail "WARP+ choice not restored after the key was refused" || return 1
    grep -q 'HEALTH 3X-UI: WARP+ (k) не подтверждается, работает как Free (причина: Cloudflare принял ключ, но сейчас сообщает тип аккаунта Free); следующая попытка через 1 ч' "$trial/log" \
        || fail "failed retry not logged" || return 1
    grep -q '^last_event=3X-UI: WARP+ (k) не подтверждается' "$WARP_DIR/health.last" || fail "header does not show the WARP+ warning" || return 1
    [ -z "${THREEXUI_LOCK_HELD:-}" ] && lock_free_3xui || fail "3X-UI lock not released after a failed retry" || return 1
    : > "$trial/events"
    for n in 1180 4660; do HEALTH_NOW=$n; printf 'free\n' > "$trial/traces"; health_tick_3xui; done
    [ "$(grep -c '^trace:free' "$trial/events")" = 2 ] || fail "Free probes did not run during the pause" || return 1
    no_ev_like '^apply' || fail "re-applied the key during the one-hour pause" || return 1
    HEALTH_NOW=4720; printf 'free\n' > "$trial/traces"; health_tick_3xui
    [ "$(grep -c '^apply$' "$trial/events")" = 1 ] || fail "key not re-applied after the pause" || return 1
    grep -q 'следующая попытка через 2 ч' "$trial/log" || fail "pause did not double" || return 1
    HEALTH_PLUS_BACKOFF_3XUI=57600; HEALTH_NOW=$HEALTH_PLUS_NEXT_3XUI
    printf 'free\n' > "$trial/traces"; health_tick_3xui
    [ "$HEALTH_PLUS_BACKOFF_3XUI" = 86400 ] && grep -q 'следующая попытка через 24 ч' "$trial/log" || fail "pause not capped at 24 h"
}

case_3xui_plus_retry_needs_streak() {
    new_case x-plus-streak 3xui on
    plus_setup_3xui
    printf 'free\nfree\nplus\nfree\n' > "$trial/traces"
    local n
    for n in 1000 1060 1120 1180; do HEALTH_NOW=$n; health_tick_3xui; done
    [ "$(grep -c '^trace:' "$trial/events")" = 4 ] || fail "not every tick checked traffic" || return 1
    no_ev_like '^apply' || fail "a WARP+ trace did not break the Free streak"
}

case_3xui_plus_retry_respects_free() {
    new_case x-plus-free 3xui on
    plus_setup_3xui
    printf 'free\nfree\nfree\nfree\n' > "$trial/traces"
    HEALTH_NOW=1000; health_tick_3xui; HEALTH_NOW=1060; health_tick_3xui
    # Пока шла проверка, в меню выбрали Free: переменные тика ещё помнят WARP+.
    sed -i 's/^WARP_KEY_MODE_3XUI=.*/WARP_KEY_MODE_3XUI="free"/; s/^ACTIVE_KEY_NAME_3XUI=.*/ACTIVE_KEY_NAME_3XUI=""/' "$WARP_CONF"
    HEALTH_NOW=1120; health_tick_3xui
    [ "$(grep -c '^trace:free' "$trial/events")" = 3 ] || fail "third Free probe did not run" || return 1
    no_ev_like '^apply' || fail "re-applied WARP+ after the choice was switched to Free" || return 1
    grep -qx 'WARP_KEY_MODE_3XUI="free"' "$WARP_CONF" || fail "service overwrote the Free choice" || return 1
    lock_free_3xui || fail "3X-UI lock not released after skipping" || return 1
    # Следующий тик читает новый конфиг и забывает счёт Free.
    # shellcheck disable=SC1090
    source "$WARP_CONF"
    HEALTH_NOW=1180; health_tick_3xui
    no_ev_like '^apply' && [ "$HEALTH_PLUS_FREE_3XUI" = 0 ] || fail "Free choice did not reset the WARP+ retry"
}

case_3xui_plus_retry_lock_busy() {
    new_case x-plus-lock 3xui on
    plus_setup_3xui
    printf 'free\nfree\nfree\n' > "$trial/traces"
    local lk n
    exec {lk}>"$WARP_DIR/.3xui-operation.lock"
    flock -n "$lk" || { fail "test could not take the lock"; return 1; }
    for n in 1000 1060 1120; do HEALTH_NOW=$n; health_tick_3xui; done
    exec {lk}>&-
    [ "$(grep -c '^trace:free' "$trial/events")" = 3 ] || fail "Free probes did not run" || return 1
    no_ev_like '^apply' || fail "re-applied WARP+ during a 3X-UI operation" || return 1
    [ ! -s "$trial/log" ] || fail "a busy lock must be skipped silently" || return 1
    printf 'free\nplus\n' > "$trial/traces"
    HEALTH_NOW=1180; health_tick_3xui
    has_ev apply || fail "retry did not happen after the operation finished"
}

case_awg_kind_rebuild() {
    new_case awg-kind amnezia
    probe "up 995 1000 10 1 1 100 0 amneziawg 1 0" "up 995 1000 10 1 1 100 0 amneziawg 1 0" \
          "up 1001 1003 12 1 1 100 0 wireguard 1 0"
    health_tick_awg
    has_ev "up:restart" || fail "amneziawg WARP was not rebuilt" || return 1
    has_ev rules || fail "client rules not reapplied after the rebuild" || return 1
    no_ev_like '^tools' || fail "tools downloaded although present" || return 1
    grep -q 'пересборка на стандартном WireGuard, было: модуль amneziawg (задержка до секунд) — помогло' "$trial/log" \
        || fail "rebuild not logged" || return 1
    [ -z "${HEALTH_KIND_NEXT[awg:amnezia-awg]:-}" ] || fail "retry pause left after success"
}

case_awg_kind_tools_fail() {
    new_case awg-kind-notools amnezia
    TOOLS_RC=1
    probe "up 995 1000 10 1 1 100 0 amneziawg 0 0" "up 995 1000 10 1 1 100 0 amneziawg 0 0"
    health_tick_awg
    has_ev tools || fail "tools download not attempted" || return 1
    no_ev_like '^up:' || fail "rebuilt without standard tools" || return 1
    grep -q 'скачивание стандартных инструментов WireGuard не удалась, следующая попытка через 1 ч' "$trial/log" \
        || fail "failed download not logged" || return 1
    [ "${HEALTH_KIND_NEXT[awg:amnezia-awg]:-}" = 4600 ] || fail "retry not scheduled in an hour" || return 1
    : > "$trial/events"
    HEALTH_NOW=1010; probe "up 1005 1010 12 1 1 100 0 amneziawg 0 0" "up 1005 1010 12 1 1 100 0 amneziawg 0 0"
    health_tick_awg
    no_ev_like '^tools' || fail "download retried during the hour pause" || return 1
    no_ev_like '^up:' || fail "a working slow tunnel was restarted" || return 1
    [ "$(grep -c '^probe:' "$trial/events")" = 1 ] || fail "took the lock during the hour pause"
}

case_awg_kind_tools_then_rebuild() {
    new_case awg-kind-tools amnezia
    probe "up 995 1000 10 1 1 100 0 amneziawg 0 0" "up 995 1000 10 1 1 100 0 amneziawg 0 0" \
          "up 1001 1003 12 1 1 100 0 wireguard 1 0"
    health_tick_awg
    has_ev tools && has_ev "up:restart" || fail "tools download and rebuild expected" || return 1
    grep -q 'пересборка на стандартном WireGuard.* — помогло' "$trial/log" || fail "rebuild after download not logged"
}

case_awg_kind_lock_busy() {
    new_case awg-kind-lock amnezia
    probe "up 995 1000 10 1 1 100 0 amneziawg 1 0" "up 995 1000 10 1 1 100 0 amneziawg 1 0"
    local lk
    exec {lk}>"$WARP_DIR/.awg-operation.lock"
    flock -n "$lk" || { fail "test could not take the lock"; return 1; }
    health_tick_awg
    exec {lk}>&-
    no_ev_like '^up:' || fail "rebuilt during an AmneziaWG operation" || return 1
    [ "$(grep -c '^probe:' "$trial/events")" = 1 ] || fail "probed again under a lock it did not get"
}

case_awg_kind_switched_off() {
    new_case awg-kind-off amnezia
    probe "up 995 1000 10 1 1 100 0 amneziawg 1 0" "disabled"
    health_tick_awg
    no_ev_like '^up:' || fail "rebuilt a tunnel that was switched off meanwhile"
}

# 2.5.2: userspace-интерфейс tun (WARP поднят инструментами контейнера без стандартных) пересобирается так же.
case_awg_kind_tun_rebuild() {
    new_case awg-kind-tun amnezia
    probe "up 995 1000 10 1 1 100 0 tun 0 0" "up 995 1000 10 1 1 100 0 tun 0 0" \
          "up 1001 1003 12 1 1 100 0 wireguard 1 0"
    health_tick_awg
    has_ev tools && has_ev "up:restart" || fail "tun WARP without tools was not rebuilt" || return 1
    has_ev rules || fail "client rules not reapplied after the rebuild" || return 1
    grep -q 'пересборка на стандартном WireGuard, было: userspace-интерфейс tun — помогло' "$trial/log" \
        || fail "tun rebuild not logged" || return 1
    [ -z "${HEALTH_KIND_NEXT[awg:amnezia-awg]:-}" ] || fail "retry pause left after success"
}

case_awg_kind_tun_fails_backoff() {
    new_case awg-kind-tun-fail amnezia
    # Модуля wireguard нет: пересборка снова даёт tun — пауза час, туннель работает дальше.
    probe "up 995 1000 10 1 1 100 0 tun 1 0" "up 995 1000 10 1 1 100 0 tun 1 0" \
          "up 1001 1003 12 1 1 100 0 tun 1 0" "up 1001 1003 12 1 1 100 0 tun 1 0" "up 1001 1003 12 1 1 100 0 tun 1 0" \
          "up 1001 1003 12 1 1 100 0 tun 1 0" "up 1001 1003 12 1 1 100 0 tun 1 0" "up 1001 1003 12 1 1 100 0 tun 1 0"
    health_tick_awg
    has_ev "up:restart" || fail "tun WARP rebuild not attempted" || return 1
    grep -q 'WARP работает через userspace-интерфейс tun; пересборка на стандартном WireGuard не удалась, следующая попытка через 1 ч' "$trial/log" \
        || fail "failed tun rebuild not logged" || return 1
    [ "${HEALTH_KIND_NEXT[awg:amnezia-awg]:-}" = 4600 ] || fail "retry not scheduled in an hour"
}

case_awg_kind_other_untouched() {
    new_case awg-kind-other amnezia
    probe "up 995 1000 10 1 1 100 0 other 1 0"
    health_tick_awg
    no_ev_like '^up:' || fail "an unknown interface kind was rebuilt" || return 1
    [ "$(grep -c '^probe:' "$trial/events")" = 1 ] || fail "took the lock for an unknown interface kind"
}

# 2.5.2: блок goWARP пропал из start.sh (его переписали снаружи) — автопроверка вставляет его заново.
case_awg_block_restored() {
    new_case awg-block amnezia
    probe "up 995 1000 10 1 1 100 0 wireguard 1 0 0" "up 995 1000 10 1 1 100 0 wireguard 1 0 0"
    health_tick_awg
    has_ev "patch:amnezia-awg" || fail "missing start.sh block was not restored" || return 1
    no_ev_like '^up:' || fail "tunnel restarted just for the start.sh block" || return 1
    grep -q 'блок автозапуска WARP в start.sh пропал или устарел — вставлен заново$' "$trial/log" \
        || fail "block restore not logged" || return 1
    [ -z "${HEALTH_BLOCK_NEXT[awg:amnezia-awg]:-}" ] || fail "retry pause left after success"
}

case_awg_block_restore_fails() {
    new_case awg-block-fail amnezia
    PATCH_RC=1
    probe "up 995 1000 10 1 1 100 0 wireguard 1 0 0" "up 995 1000 10 1 1 100 0 wireguard 1 0 0"
    health_tick_awg
    grep -q 'вставить не удалось; следующая попытка через 1 ч' "$trial/log" || fail "failed restore not logged" || return 1
    [ "${HEALTH_BLOCK_NEXT[awg:amnezia-awg]:-}" = 4600 ] || fail "retry not scheduled in an hour" || return 1
    : > "$trial/events"
    HEALTH_NOW=1010; probe "up 1005 1010 12 1 1 100 0 wireguard 1 0 0"
    health_tick_awg
    no_ev_like '^patch' || fail "restore retried during the hour pause" || return 1
    [ "$(grep -c '^probe:' "$trial/events")" = 1 ] || fail "took the lock during the hour pause"
}

case_awg_block_switched_off() {
    new_case awg-block-off amnezia
    # Пока ждали блокировку, WARP выключили руками: блок не трогаем.
    probe "up 995 1000 10 1 1 100 0 wireguard 1 0 0" "disabled"
    health_tick_awg
    no_ev_like '^patch' || fail "restored the block of a container switched off meanwhile" || return 1
    local lk
    new_case awg-block-lock amnezia
    probe "up 995 1000 10 1 1 100 0 wireguard 1 0 0"
    exec {lk}>"$WARP_DIR/.awg-operation.lock"; flock -n "$lk" || { fail "test could not take the lock"; return 1; }
    health_tick_awg
    exec {lk}>&-
    no_ev_like '^patch' || fail "restored the block during an AmneziaWG operation" || return 1
    [ ! -s "$trial/log" ] || fail "a busy lock must be skipped silently"
}

# Блок от версии до 2.5.2 (контейнер был остановлен при обновлении): вставляется заново, адреса — в ключи.
case_awg_block_old_list_converted() {
    new_case awg-block-old amnezia
    awg_load_clients() { AWG_CLIENTS_MIGRATED=1; ev load-clients; }
    awg_save_clients() { ev save-clients; }
    probe "up 995 1000 10 1 1 100 0 wireguard 1 0 0 1" "up 995 1000 10 1 1 100 0 wireguard 1 0 0 1"
    health_tick_awg
    awk '/^save-clients$/ && !a {a=NR} /^patch:amnezia-awg$/ && !b {b=NR} END{exit !(a && b && a < b)}' "$trial/events" \
        || { cat "$trial/events" >&2; fail "old client list not converted before the block was refreshed"; return 1; }
    grep -q 'вставлен заново, выбор клиентов переведён на ключи' "$trial/log" || fail "conversion not logged"
}

# Правила ведут не тех: счёт сходится, адреса — нет (новый клиент занял адрес удалённого выбранного).
case_awg_rules_mismatch() {
    new_case awg-rules-mismatch amnezia
    probe "up 995 1000 10 1 1 100 0 wireguard 1 0 1 0" "up 995 1000 10 1 1 100 0 wireguard 1 0 1 0"
    health_tick_awg
    has_ev rules || fail "rules of the wrong clients were not reapplied" || return 1
    no_ev_like '^up:' || fail "tunnel restarted just for the client rules" || return 1
    grep -q 'восстановление правил клиентов, было: сбились правила клиентов — помогло' "$trial/log" || fail "rules repair not logged"
}

# Ядро без wireguard (LXC/OpenVZ): tun не пересобирать, туннель не трогать, проверять раз в сутки.
case_awg_kind_no_kernel_module() {
    new_case awg-kind-nomod amnezia
    KERNEL_WG=1
    probe "up 995 1000 10 1 1 100 0 tun 0 0" "up 995 1000 10 1 1 100 0 tun 0 0"
    health_tick_awg
    has_ev "kernel-wg:amnezia-awg" || fail "kernel support was not checked" || return 1
    no_ev_like '^up:' || fail "tunnel restarted on a kernel without wireguard" || return 1
    no_ev_like '^tools' || fail "tools downloaded for a kernel without wireguard" || return 1
    grep -q 'ядро не создаёт интерфейсы wireguard — WARP оставлен как есть, проверка раз в сутки' "$trial/log" \
        || fail "kernel limitation not logged" || return 1
    [ "${HEALTH_KIND_NEXT[awg:amnezia-awg]:-}" = 87400 ] || fail "kernel check not paused for a day"
}

# Обновление контейнеров: без контейнеров обновлять нечего, без ответа Docker — повтор при запуске;
# список клиентов из адресов сохраняется ключами, WARP на tun пересобирается.
case_upgrade_containers() {
    new_case upgrade-containers amnezia
    local rc=0
    awg_discover_containers() { AWG_ALL_CONTAINERS=(); return 1; }
    upgrade_awg_containers > "$trial/output" 2>&1 || rc=$?
    [ "$rc" = 0 ] || fail "upgrade without containers reported failure" || return 1
    no_ev_like '^probe' || fail "upgrade without containers touched Docker" || return 1
    awg_discover_containers() { AWG_ALL_CONTAINERS=(); return 2; }
    rc=0; upgrade_awg_containers > "$trial/output" 2>&1 || rc=$?
    [ "$rc" != 0 ] || fail "upgrade without an answer from Docker reported success" || return 1
    # Контейнеры есть, но все остановлены: повторить при следующем запуске.
    awg_discover_containers() { AWG_ALL_CONTAINERS=(); [ "${1:-}" = all ] && { AWG_ALL_CONTAINERS=(amnezia-awg); return 0; }; return 1; }
    rc=0; upgrade_awg_containers > "$trial/output" 2>&1 || rc=$?
    [ "$rc" != 0 ] || fail "upgrade with only stopped containers reported success" || return 1
    awg_discover_containers() { AWG_ALL_CONTAINERS=(amnezia-awg); return 0; }
    awg_resume_interrupted_rekey() { AWG_RESUME_RESULT=none; return 0; }
    awg_load_clients() { AWG_CLIENTS_MIGRATED=1; ev load-clients; }
    awg_save_clients() { ev save-clients; }
    awg_patch_start_sh() { ev patch; }
    awg_warp_kind() { echo tun; }
    awg_warp_up() { ev "up:${1:-restart}"; AWG_WARP_KIND=wireguard; }
    awg_install_host_watcher() { ev watcher; }
    awg_shared_key_detected() { return 1; }
    docker() { return 0; }
    UPGRADE_NOTES=""
    rc=0; upgrade_awg_containers > "$trial/output" 2>&1 || rc=$?
    [ "$rc" = 0 ] || { cat "$trial/output" >&2; fail "upgrade with a container failed"; return 1; }
    has_ev save-clients && has_ev patch && has_ev "up:restart" && has_ev watcher \
        || { cat "$trial/events" >&2; fail "upgrade did not convert clients, refresh start.sh and rebuild tun"; return 1; }
    awk '/^save-clients$/ && !a {a=NR} /^patch$/ && !b {b=NR} END{exit !(a && b && a < b)}' "$trial/events" \
        || fail "client list must be converted before start.sh is refreshed" || return 1
    [[ "$UPGRADE_NOTES" == *"WARP переведён на стандартный WireGuard: amnezia-awg"* ]] || fail "rebuild not reported: $UPGRADE_NOTES" || return 1
    # Ядро без wireguard: tun при обновлении не пересобирается.
    : > "$trial/events"; KERNEL_WG=1; UPGRADE_NOTES=""
    rc=0; upgrade_awg_containers > "$trial/output" 2>&1 || rc=$?
    [ "$rc" = 0 ] || fail "upgrade on a kernel without wireguard failed" || return 1
    no_ev_like '^up:' || fail "upgrade restarted WARP on a kernel without wireguard" || return 1
    grep -q 'ядро не создаёт интерфейсы wireguard: оставлен как есть' "$trial/log" || fail "kernel limitation not logged on upgrade"
}

case_awg_rtt_warn() {
    new_case awg-rtt amnezia
    HEALTH_PROBE_EVERY=0
    local n
    for n in 1000 1010 1020; do
        HEALTH_NOW=$n; probe "up $((n - 5)) $n 10 1 1 100 1 wireguard 1 1500"; health_tick_awg
    done
    [ "$(grep -c 'задержка через WARP 1500 мс' "$trial/log")" = 1 ] || fail "slow WARP not reported once after three probes" || return 1
    grep -q '^last_event=AmneziaWG amnezia-awg: задержка через WARP 1500 мс' "$WARP_DIR/health.last" \
        || fail "menu header does not show the slow WARP" || return 1
    HEALTH_NOW=1030; probe "up 1025 1030 10 1 1 100 1 wireguard 1 1600"; health_tick_awg
    [ "$(grep -c 'задержка через WARP' "$trial/log")" = 1 ] || fail "slow WARP reported again within the hour" || return 1
    HEALTH_NOW=1040; probe "up 1035 1040 10 1 1 100 1 wireguard 1 20"; health_tick_awg
    [ "${HEALTH_RTT_SLOW[awg:amnezia-awg]:-}" = 0 ] || fail "a fast probe did not reset the streak" || return 1
    no_ev_like '^up:' || fail "slow WARP must not restart the tunnel"
}

run() {
    local label="$1"; shift
    checks=$((checks + 1))
    if ( "$@" ); then echo "health: $label OK"
    else failures=$((failures + 1)); echo "FAIL: health: $label" >&2; fi
}

run 'AWG: пропавший интерфейс поднимается сразу' case_awg_down_heals
run 'AWG: ручное выключение не перебивается' case_awg_disabled_respected
run 'AWG: 30 с после старта контейнера не вмешивается' case_awg_start_grace
run 'AWG: мёртвый handshake, пауза и следующая ступень' case_awg_stale_backoff_escalation
run 'AWG: третья ступень меняет порт Cloudflare' case_awg_port_rotation
run 'AWG: сбитые правила клиентов без перезапуска' case_awg_rules_restored
run 'AWG: байты приходят, клиентов не дёргаем' case_awg_path_issue_no_restart
run 'AWG: байты не приходят, перезапуск' case_awg_dead_traffic_restart
run 'AWG: во время операции проверка не чинит и не ждёт' case_awg_lock_skips
run 'AWG: выключили руками между проверкой и лечением' case_awg_manual_off_race
run '3X-UI: выключили руками между проверкой и лечением' case_3xui_manual_off_race
run '3X-UI: ручное выключение не перебивается' case_3xui_manual_off_respected
run '3X-UI: Disconnected, первая ступень' case_3xui_disconnected_reconnects
run '3X-UI: тихий обрыв, три провала и эскалация' case_3xui_silent_failure_escalates
run '3X-UI: пропавшая регистрация перевыпускается' case_3xui_registration_missing_rekeys
run '3X-UI: откат и операция блокируют проверку' case_3xui_recovery_and_lock_respected
run '3X-UI: API Cloudflare недоступен, регистрацию не трогаем' case_3xui_api_down_keeps_registration
run '3X-UI: API Cloudflare отвечает, четвёртая ступень перевыпускает' case_3xui_api_up_rekeys
run '3X-UI: пропавшая регистрация перевыпускается без проверки API' case_3xui_register_skips_api
run '3X-UI: новая регистрация не сбрасывает выбор WARP+' case_3xui_rekey_keeps_plus_choice
run '3X-UI: выбор Free после перевыпуска не трогаем' case_3xui_rekey_free_untouched
run '3X-UI: три проверки Free подряд, ключ WARP+ повторяется' case_3xui_plus_retry_confirms
run '3X-UI: WARP+ не подтвердился, пауза от часа до суток' case_3xui_plus_retry_backoff
run '3X-UI: проверка WARP+ между Free сбрасывает счёт' case_3xui_plus_retry_needs_streak
run '3X-UI: выбрали Free, ключ WARP+ не повторяем' case_3xui_plus_retry_respects_free
run '3X-UI: во время операции ключ WARP+ не повторяем' case_3xui_plus_retry_lock_busy
run 'неперенесённый конфиг не трогаем' case_unmigrated_config_skipped
run 'служба ставится идемпотентно и удаляется' case_service_install_idempotent
run 'wgcf 2.2.30 заменяется на закреплённую сборку' case_wgcf_pinned_replacement
run 'миграция 4 в 5 добавляет намерение 3X-UI' case_migration_adds_intent
run 'ручное выключение пишет намерение до отключения' case_manual_stop_intent_first
run 'AWG: WARP на amneziawg пересобирается на wireguard' case_awg_kind_rebuild
run 'AWG: нет инструментов и не скачались — пауза час, туннель не трогаем' case_awg_kind_tools_fail
run 'AWG: нет инструментов — скачать и пересобрать' case_awg_kind_tools_then_rebuild
run 'AWG: во время операции тип не меняем' case_awg_kind_lock_busy
run 'AWG: выключили руками до пересборки' case_awg_kind_switched_off
run 'AWG: задержка через WARP выше секунды — предупреждение раз в час' case_awg_rtt_warn
run 'AWG: userspace tun без инструментов — скачать и пересобрать' case_awg_kind_tun_rebuild
run 'AWG: tun не пересобрался — пауза час' case_awg_kind_tun_fails_backoff
run 'AWG: неизвестный тип интерфейса не трогаем' case_awg_kind_other_untouched
run 'обновление: без контейнеров — норма, выбор клиентов в ключи, tun пересобирается' case_upgrade_containers
run 'AWG: пропавший блок start.sh вставляется заново' case_awg_block_restored
run 'AWG: блок не вставился — пауза час' case_awg_block_restore_fails
run 'AWG: блок не трогаем при ручном выключении и во время операции' case_awg_block_switched_off
run 'AWG: блок до 2.5.2 обновляется, адреса клиентов — в ключи' case_awg_block_old_list_converted
run 'AWG: правила ведут не тех клиентов — применить заново' case_awg_rules_mismatch
run 'AWG: ядро без wireguard — tun не трогаем' case_awg_kind_no_kernel_module

[ "$failures" -eq 0 ] || exit 1
echo "health_checks: OK ($checks cases)"
