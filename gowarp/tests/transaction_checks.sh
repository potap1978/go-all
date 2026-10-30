#!/usr/bin/env bash
set -uo pipefail
source <(awk '/^#  ENTRY POINT/{exit} {print}' "${1:-warp.sh}")
case_root=$(mktemp -d)
trap 'rm -rf "$case_root"' EXIT
sleep() { :; }
run_case() (
    local scenario="$1" trial="$case_root/$1"
    mkdir -p "$trial"
    WARP_DIR="$trial/configdir"; WARP_CONF="$WARP_DIR/config"; WARP_KEYS="$WARP_DIR/keys.list"
    WARP_LOG="$trial/log"; WARP_BOT_STATE="$trial/bot.state"
    WGCF_ACCOUNT="$trial/legacy-account"; WGCF_PROFILE="$trial/legacy-profile"
    WGCF_LEGACY_DIR="$trial/legacy"; AWG_WGCF_BASE="$trial/devices"
    HOST_WGCF_BAK_ROOT="$trial/snapshots"; HOST_WGCF_BAK_DIR="$HOST_WGCF_BAK_ROOT"
    init_config >/dev/null
    save_config_val WARP_KEY_MODE_AWG free
    save_config_val ACTIVE_KEY_NAME_AWG ""
    add_key main AAAAAAAA-BBBBBBBB-CCCCCCCC
    local events="$trial/events" c
    : > "$events"
    for c in amnezia-awg amnezia-awg2; do
        awg_wgcf_ctx "$c"
        echo "old-$c" > "$WGCF_ACCOUNT"
        echo "old-profile-$c" > "$WGCF_PROFILE"
        echo "old-network-$c" > "$trial/$c.network"
    done
    awg_discover_containers() { AWG_ALL_CONTAINERS=(amnezia-awg amnezia-awg2); }
    awg_load_container_data() { return 0; }
    awg_prepare_warp_tools() { return 0; }
    awg_adopt_legacy_account() { return 0; }
    docker() { return 0; }
    awg_resolve_endpoint() { echo 192.0.2.1; }
    awg_snapshot_container() { cp "$trial/$1.network" "$trial/$1.saved"; }
    awg_clear_container_snapshot() { return 0; }
    awg_restore_container_snapshot() { cp "$trial/$1.saved" "$trial/$1.network"; echo "restore:$1" >> "$events"; }
    awg_ensure_account() { echo "new-$CONTAINER" > "$WGCF_ACCOUNT"; }
    awg_generate_profile() { echo "new-profile-$CONTAINER" > "$WGCF_PROFILE"; }
    apply_warp_plus_key_awg() { PLUS_LAST_REASON=ok; return 0; }
    awg_build_warp_conf() { echo "new-network-$CONTAINER" > "$trial/$CONTAINER.network"; }
    awg_warp_up() { return 0; }
    awg_load_clients() { return 0; }
    awg_apply_rules() { return 0; }
    awg_patch_start_sh() { return 0; }
    awg_install_host_watcher() { return 0; }
    awg_install_wgcf() { return 0; }
    health_service_install() { return 0; }
    verify_warp_plus_awg_container() {
        echo "verify:$1" >> "$events"
        if [ "$scenario" = all_fail ] || { [ "$scenario" = partial ] && [ "$1" = amnezia-awg2 ]; }; then return 1; fi
        echo plus
    }
    awg_unregister_account_file() { echo "discard:$(cat "$1")" >> "$events"; }
    awg_retire_snapshot_account() { echo "retire:$CONTAINER" >> "$events"; }
    local rc=0
    if [ "$scenario" = subset ]; then
        # Обновление разводит общий ключ: перевыпуск только для перечисленных контейнеров.
        AWG_REKEY_ONLY=amnezia-awg2 rekey_awg_core plus main >/dev/null || rc=$?
        [ "$rc" = 0 ] || exit 1
        [ "$(cat "$AWG_WGCF_BASE/amnezia-awg/wgcf-account.toml")" = old-amnezia-awg ] || exit 1
        [ "$(cat "$AWG_WGCF_BASE/amnezia-awg2/wgcf-account.toml")" = new-amnezia-awg2 ] || exit 1
        ! grep -q 'verify:amnezia-awg$' "$events" || exit 1
        exit 0
    fi
    rekey_awg_core plus main >/dev/null || rc=$?
    if [ "$scenario" = success ]; then
        [ "$rc" = 0 ] && [ "$WARP_KEY_MODE_AWG" = plus ] || exit 1
    else
        [ "$rc" != 0 ] && [ "$WARP_KEY_MODE_AWG" = free ] || exit 1
    fi
    for c in amnezia-awg amnezia-awg2; do
        if [ "$scenario" = all_fail ] || { [ "$scenario" = partial ] && [ "$c" = amnezia-awg2 ]; }; then
            [ "$(cat "$AWG_WGCF_BASE/$c/wgcf-account.toml")" = "old-$c" ] || exit 1
            [ "$(cat "$trial/$c.network")" = "old-network-$c" ] || exit 1
            ! grep -q "retire:$c" "$events" || exit 1
            grep -q "discard:new-$c" "$events" || exit 1
        else
            [ "$(cat "$AWG_WGCF_BASE/$c/wgcf-account.toml")" = "new-$c" ] || exit 1
            local v r
            v=$(grep -n "verify:$c$" "$events" | head -1 | cut -d: -f1)
            r=$(grep -n "retire:$c$" "$events" | cut -d: -f1)
            [ "$v" -lt "$r" ] || exit 1
        fi
    done
)
for scenario in success partial all_fail subset; do
    if run_case "$scenario"; then echo "transaction: $scenario OK"; else echo "FAIL: $scenario" >&2; exit 1; fi
done
