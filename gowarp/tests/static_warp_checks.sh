#!/usr/bin/env bash
set -euo pipefail

script="${1:-warp.sh}"
failures=0

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    failures=$((failures + 1))
}

require_pattern() {
    local pattern="$1" message="$2"
    if ! grep -qE "$pattern" "$script"; then
        fail "$message"
    fi
}

if grep -qE ';&|;;&' "$script"; then
    fail "Bash case fallthrough operators are not allowed; they parse-fail on Bash 3.2 before guards can run"
fi

first_assoc_line=$(awk '/^[[:space:]]*declare[[:space:]]+-A[[:space:]]/{print NR; exit}' "$script")
guard_call_line=$(awk '/^require_bash4$/ {print NR; exit}' "$script")
if [ -z "${first_assoc_line:-}" ]; then
    fail "No associative-array declaration found; update the Bash-version guard test"
elif [ -z "${guard_call_line:-}" ] || [ "$guard_call_line" -ge "$first_assoc_line" ]; then
    fail "require_bash4 must be invoked before Bash-4-only declarations"
fi

require_pattern '^reset_wgcf_account_license[[:space:]]*\(\)' \
    "AWG rollback must have a helper that removes the paid license from wgcf-account.toml"

awg_apply_body=$(awk '/^apply_warp_plus_key_awg\(\)/,/^}/' "$script")
reset_count=$(grep -c 'reset_wgcf_account_license' <<< "$awg_apply_body" || true)
if [ "$reset_count" -lt 2 ]; then
    fail "apply_warp_plus_key_awg must scrub wgcf-account.toml on post-write rollback paths"
fi

add_key_body=$(awk '/^add_key\(\)/,/^}/' "$script")
if ! grep -q 'flock -w 5 -x' <<< "$add_key_body"; then
    fail "add_key must lock keys.list while checking and appending"
fi
if ! grep -q 'flock -w 5 -x 9 2>/dev/null || exit' <<< "$add_key_body"; then
    fail "add_key must fail closed if flock cannot acquire the lock"
fi
if ! grep -Fq '$1==n' <<< "$add_key_body"; then
    fail "add_key must check duplicate names inside the lock"
fi
delete_key_body=$(awk '/^delete_key\(\)/,/^}/' "$script")
save_config_body=$(awk '/^save_config_values\(\)/,/^}/' "$script")
install_3xui_body=$(awk '/^install_warp_3xui\(\)/,/^}/' "$script")
if ! grep -q 'flock -w 5 -x 9 2>/dev/null || exit' <<< "$delete_key_body"; then
    fail "delete_key must fail closed with a bounded flock timeout"
fi
if ! grep -Fq 'changes+=(WARP_KEY_MODE_3XUI free ACTIVE_KEY_NAME_3XUI "")' <<< "$delete_key_body"; then
    fail "Deleting the active 3X-UI key must reset mode and key together"
fi
if ! grep -Fq 'changes+=(WARP_KEY_MODE_AWG free ACTIVE_KEY_NAME_AWG "")' <<< "$delete_key_body"; then
    fail "Deleting the active AWG key must reset mode and key together"
fi
if ! grep -q 'return 0' <<< "$delete_key_body"; then
    fail "delete_key must explicitly return success after active-key cleanup"
fi
if ! grep -q 'flock -w 5 -x 9 2>/dev/null || exit' <<< "$save_config_body"; then
    fail "save_config_val must fail closed with a bounded flock timeout"
fi
if ! grep -q 'return \$rc' <<< "$save_config_body"; then
    fail "save_config_val must propagate flock/write failures instead of masking them with source"
fi

if ! grep -Fq 'for tested in "$native_codename" noble jammy focal' <<< "$install_3xui_body"; then
    fail "install_warp_3xui must try native Cloudflare repo first, then noble/jammy/focal fallbacks"
fi
if ! grep -Fq 'pkg.cloudflareclient.com/dists/${tested}/Release' <<< "$install_3xui_body"; then
    fail "install_warp_3xui must verify Cloudflare repo Release before writing apt source"
fi
if ! grep -q 'install_err' <<< "$install_3xui_body"; then
    fail "install_warp_3xui must capture apt install stderr for dependency diagnostics"
fi

install_body=$(awk '/^install_warp_awg\(\)/,/^}/' "$script")
if ! grep -q 'rekey_awg_core.*install' <<< "$install_body"; then
    fail "AWG install must share the verified transaction used for rekey"
fi
require_pattern '^awg_install_host_watcher[[:space:]]*\(\)' "AWG watcher is required"
require_pattern '^awg_remove_host_watcher[[:space:]]*\(\)' "AWG watcher cleanup is required"

# v2.4: перевыпуск AWG вынесен в rekey_awg_core (rekey_warp_awg — тонкая обёртка).
rekey_body=$(awk '/^rekey_awg_core\(\)/,/^}/' "$script")
if [ -z "$rekey_body" ]; then
    fail "rekey_awg_core must exist (unified AWG rekey logic in v2.4)"
fi
if ! grep -q 'installed_targets' <<< "$rekey_body"; then
    fail "rekey_awg_core must target only containers with existing WARP (each container has its own account)"
fi
if ! grep -q 'awg_restore_container_snapshot' <<< "$rekey_body"; then
    fail "rekey_awg_core must restart/restore old WARP on pre-apply failures"
fi
require_pattern '^awg_snapshot_host_wgcf[[:space:]]*\(\)' \
    "AWG rekey must snapshot host wgcf account/profile before rotation"
require_pattern '^awg_restore_host_wgcf[[:space:]]*\(\)' \
    "AWG rekey must restore host wgcf account/profile on failed rotation"
if ! grep -q 'awg_snapshot_host_wgcf' <<< "$rekey_body"; then
    fail "rekey_awg_core must snapshot host wgcf state before deleting account/profile"
fi
if ! grep -q 'awg_restore_host_wgcf' <<< "$rekey_body"; then
    fail "rekey_awg_core must restore host wgcf state on failure paths"
fi
require_pattern '^awg_restore_mode_snapshot[[:space:]]*\(\)' \
    "AWG rekey must have a helper that restores config mode/key snapshots"
if ! grep -q 'awg_restore_mode_snapshot "$snap_mode" "$snap_active"' <<< "$rekey_body"; then
    fail "failed AWG transactions must preserve previous defaults"
fi
if ! grep -q 'awg_verify_ready' <<< "$rekey_body"; then
    fail "AWG transaction must verify traffic before committing"
fi
if ! grep -q 'awg_install_host_watcher' <<< "$rekey_body"; then
    fail "rekey_awg_core must refresh the host-side restart watcher after successful rekeys"
fi
# v2.4: единое действие «Включить WARP+» — ключ применяется сразу, без ручного
# «выбрать активный» + «переключить режим» + отдельного перевыпуска.
if ! grep -qE '^plus_enable_wizard\(\)' "$script"; then
    fail "v2.4 must provide plus_enable_wizard (single-action Enable WARP+)"
fi
if ! grep -qE '^plus_menu\(\)' "$script"; then
    fail "v2.4 must provide the 2-level plus_menu"
fi
bot_rekey_body=$(awk 'index($0, "rk_y|rk_y:*") {flag=1} flag {print} flag && index($0, "Перевыпуск ключа</b>") {flag=0}' "$script")
for core in rekey_3xui_core rekey_awg_core; do
    if ! grep -q "$core" <<< "$bot_rekey_body"; then
        fail "Telegram must use shared $core instead of duplicating rotation logic"
    fi
done
require_pattern '^awg_snapshot_container[[:space:]]*\(\)' \
    "AWG rekey must snapshot containers before overwriting warp.conf"
require_pattern '^awg_restore_container_snapshot[[:space:]]*\(\)' \
    "AWG rekey must restore failed containers after partial apply failures"

if ! grep -q 'docker ps \$ps_flag' "$script"; then
    fail "awg_discover_containers must support stopped containers for full uninstall"
fi
if awk '!/^[[:space:]]*#/ && /grep -i "amnezia"/ {found=1} END{exit found ? 0 : 1}' "$script"; then
    fail "AWG discovery must not use broad grep -i amnezia matching unrelated containers"
fi
if ! grep -Fq "grep -E '^amnezia-awg[0-9]*$'" "$script"; then
    fail "AWG discovery must use an anchored amnezia-awg whitelist"
fi
uninstall_cleanup_check=$(awk '/# Проверим: остались ли ещё контейнеры с warp.conf\?/,/if \[ \$has_any -eq 0 \]/{print}' "$script")
if ! grep -q 'awg_discover_containers all' <<< "$uninstall_cleanup_check"; then
    fail "uninstall_awg must include stopped containers before deciding host-wide wgcf cleanup"
fi
uninstall_3xui_body=$(awk '/^uninstall_3xui\(\)/,/^}/' "$script")
uninstall_awg_body=$(awk '/^uninstall_awg\(\)/,/^}/' "$script")
full_uninstall_body=$(awk '/^full_uninstall\(\)/,/^}/' "$script")
if ! grep -q 'apt-get purge -y cloudflare-warp' <<< "$uninstall_3xui_body"; then
    fail "uninstall_3xui must purge cloudflare-warp instead of leaving dpkg rc state"
fi
if ! grep -q 'systemctl stop warp-bot' <<< "$uninstall_3xui_body"; then
    fail "uninstall_3xui must stop the Telegram bot service"
fi
if ! grep -q 'docker start "\$c"' <<< "$uninstall_awg_body"; then
    fail "uninstall_awg all must temporarily start stopped containers for cleanup"
fi
# 2.5.2: удаление вырезает только свой блок; устаревшая копия start.sh.final-backup не восстанавливается.
if grep -q 'cp /opt/amnezia/start.sh.final-backup' "$script"; then
    fail "uninstall must not restore the stale start.sh.final-backup over later start.sh edits"
fi
if ! grep -q 'awg_remove_from_start_sh' <<< "$uninstall_awg_body"; then
    fail "uninstall_awg must cut the WARP-MANAGER block out of start.sh"
fi
if grep -q 'final-backup' <<< "$(awk '/^awg_patch_start_sh\(\)/,/^}/' "$script")"; then
    fail "awg_patch_start_sh must not create start.sh.final-backup any more"
fi
remove_block_body=$(awk '/^awg_remove_from_start_sh\(\)/,/^}/' "$script")
grep -q 'AWG_MARKER_END' <<< "$remove_block_body" || fail "start.sh block must be cut only when both markers are present"
grep -q "rm -rf '\$AWG_WARP_DIR' '\$AWG_WARP_TOOLS_DIR'" <<< "$uninstall_awg_body" \
    || fail "uninstall_awg must remove /opt/warp-tools from containers"
host_state_body=$(awk '/^awg_remove_host_state\(\)/,/^}/' "$script")
if ! grep -q 'awg_remove_host_state' <<< "$uninstall_awg_body" || ! grep -q 'awg_remove_host_watcher' <<< "$host_state_body"; then
    fail "uninstall_awg must remove the host-side restart watcher when no WARP containers remain"
fi
grep -q 'awg_retire_leftover_devices' <<< "$host_state_body" \
    || fail "host cleanup must retire WARP devices of containers that no longer exist"
# Пустой список контейнеров — норма, а неответивший Docker останавливает удаление.
grep -q 'return 2' <<< "$(awk '/^awg_discover_containers\(\)/,/^}/' "$script")" \
    || fail "awg_discover_containers must tell a Docker failure (rc=2) from no containers (rc=1)"
grep -Fq '[ "$drc" = 2 ]' <<< "$uninstall_awg_body" || fail "uninstall must stop only when Docker does not answer"
if ! grep -q 'log_action "UNINSTALL: full removal.*about to remove' <<< "$full_uninstall_body"; then
    fail "full_uninstall must log before deleting WARP_DIR/WARP_LOG so it does not recreate logs"
fi
if ! grep -q 'uninstall_awg_locked all' <<< "$full_uninstall_body"; then
    fail "full_uninstall must clean all AWG containers without interactive selection"
fi

require_pattern '^NONCE_TTL_SEC=120$' \
    "Telegram destructive callback nonce must have a short TTL"
require_pattern '^check_nonce_valid[[:space:]]*\(\)' \
    "Telegram destructive callback nonce must be timestamp-validated"
if ! grep -q '/dev/urandom' "$script"; then
    fail "Telegram nonce must use cryptographic randomness"
fi
if grep -q 'callback_data":"rk_y"' "$script"; then
    fail "Telegram rekey confirmation must carry a nonce, not a timeless rk_y callback"
fi
require_pattern 'rk_y:\*' \
    "Telegram rekey handler must validate nonce-bound rk_y callbacks"
require_pattern 'k_del_yes:\*' \
    "Telegram key delete must require a nonce-bound confirmation callback"
if grep -q 'bot_state_clear "\$cci" "\$cui"' "$script"; then
    fail "Dispatcher must not clear callback confirmation state before handlers validate it"
fi
if ! grep -Fq 'bot_show_clients "$chat_id" "$msg_id" "${AWG_ALL_CONTAINERS[0]}"' "$script"; then
    fail "Telegram single-container client menu must pass the explicit discovered container"
fi
require_pattern '^bot_chat_allowed[[:space:]]*\(\)' \
    "Bot auth must allow BOT_USER_IDS users to complete secret flows in private chat when BOT_CHAT_ID is a group"
bot_chat_body=$(awk '/^bot_chat_allowed\(\)/,/^}/' "$script")
bot_daemon_body=$(awk '/^bot_daemon\(\)/,/^}/' "$script")
if ! grep -q 'bot_user_allowed "\$user_id"' <<< "$bot_chat_body"; then
    fail "bot_chat_allowed must validate from.id, not chat.id only"
fi
if ! awk '/^bot_user_allowed\(\)/,/^}/ {print}' "$script" | grep -Fq "tr -d '[:space:]'"; then
    fail "BOT_USER_IDS normalization must remove whitespace"
fi
if ! grep -q 'callback_query.from.id' <<< "$bot_daemon_body"; then
    fail "bot daemon must extract callback from.id"
fi
if ! grep -q 'message.from.id' <<< "$bot_daemon_body"; then
    fail "bot daemon must extract message from.id"
fi
if grep -q 'stderr=\$' "$script" || grep -q 'err_msg' "$script"; then
    fail "WARP+ apply must not log raw wgcf stderr after license_key is written"
fi
if awk '!/^[[:space:]]*#/ && /yes[[:space:]]*\|[[:space:]]*(\.\/)?wgcf/ {bad=1} END{exit bad ? 0 : 1}' "$script"; then
    fail "Do not use yes | wgcf under global pipefail"
fi
if awk '/^awg_generate_profile\(\)/,/^}/ {print}' "$script" | grep -qE '\|[[:space:]]*\./wgcf generate'; then
    fail "wgcf generate must not be piped under pipefail"
fi
if grep -qE '\|.*wgcf[[:space:]]+update' <<< "$awg_apply_body"; then
    fail "wgcf update must not be piped under pipefail"
fi
keyvalue_body=$(awk '/awaiting_keyvalue\)/,/;;/' "$script")
if ! grep -q 'tg_delete "\$chat_id" "\$msg_id"' <<< "$keyvalue_body"; then
    fail "Bot key intake must attempt to delete the secret-bearing Telegram message"
fi
if ! grep -q 'mask_key "\$text"' <<< "$keyvalue_body"; then
    fail "Bot key intake must only display/log masked keys"
fi

apply_3xui_body=$(awk '/^apply_warp_plus_key_3xui\(\)/,/^}/' "$script")
if ! grep -q 'plus mode without active key' <<< "$apply_3xui_body"; then
    fail "3X-UI Plus apply must roll back invalid plus-without-active-key state"
fi
if ! grep -q 'plus mode without active key' <<< "$awg_apply_body"; then
    fail "AWG Plus apply must roll back invalid plus-without-active-key state"
fi
if ! awk '/active key.*not in keys.list/,/return 1/' <<< "$awg_apply_body" | grep -q 'reset_wgcf_account_license'; then
    fail "AWG Plus apply must scrub wgcf-account.toml when active key is missing from keys.list"
fi
if ! grep -q 'wgcf_cmd=.*WGCF_BIN' <<< "$awg_apply_body"; then
    fail "AWG Plus apply must use the actual wgcf executable it detected"
fi
if ! grep -q 'reset_wgcf_account_license' <<< "$delete_key_body"; then
    fail "Deleting the active AWG key must scrub any paid license from wgcf-account.toml"
fi
if ! grep -q 'plus_apply_choice "$sub" "$requested" "$name"' "$script"; then
    fail "Telegram mode selection must use the same verified action as CLI"
fi

# 2.5: автопроверка, самолечение и закреплённый wgcf
require_pattern '^health_daemon[[:space:]]*\(\)' "health daemon is required"
require_pattern '^health_tick[[:space:]]*\(\)' "health tick is required"
grep -q -- '--health-daemon) health_daemon' "$script" || fail "entry point must start the health daemon"
upgrade_hooks_body=$(awk '/^run_upgrade_hooks\(\)/,/^}/' "$script")
grep -q 'health_service_install' <<< "$upgrade_hooks_body" || fail "run_upgrade_hooks must install gowarp-health on existing installs"
grep -q 'awg_install_wgcf' <<< "$upgrade_hooks_body" || fail "run_upgrade_hooks must replace an outdated wgcf on existing installs"
grep -q 'health_service_remove' <<< "$full_uninstall_body" || fail "full_uninstall must remove gowarp-health"
rekey_awg_head=$(awk '/^rekey_awg_core\(\)/,/AWG_TARGETS=/' "$script")
grep -q 'awg_install_wgcf' <<< "$rekey_awg_head" || fail "rekey_awg_core must pin wgcf before registering"
grep -Eq '^WGCF_VERSION="2\.2\.31"$' "$script" || fail "wgcf must stay pinned to 2.2.31"
for arch in AMD64 ARM64 ARMV7; do
    grep -Eq "^WGCF_SHA256_${arch}=\"[0-9a-f]{64}\"$" "$script" || fail "wgcf ${arch} checksum must be pinned"
done
health_3xui_body=$(awk '/^health_tick_3xui\(\)/,/^}/' "$script")
grep -q 'WARP_WANTED_3XUI' <<< "$health_3xui_body" || fail "health must honour manual 3X-UI off"
grep -q '3xui-recovery' <<< "$health_3xui_body" || fail "health must not touch 3X-UI during recovery"
grep -q '/opt/warp/disabled' <<< "$(awk '/^health_awg_probe\(\)/,/^}/' "$script")" || fail "health must honour manual AmneziaWG off"
stop_3xui_body=$(awk '/^stop_warp_3xui\(\)/,/^}/' "$script")
awk '/WARP_WANTED_3XUI off/{a=NR} /_stop_3xui_locked/{b=NR} END{exit !(a && b && a < b)}' <<< "$stop_3xui_body" \
    || fail "manual 3X-UI stop must record intent before disconnecting"

# Проверка API Cloudflare перед новой регистрацией, выбор WARP+ и повтор ключа
health_check_3xui_body=$(awk '/^health_check_3xui\(\)/,/^}/' "$script")
api_check_body=$(awk '/^health_cf_api_reachable\(\)/,/^}/' "$script")
grep -Fq 'https://api.cloudflareclient.com/' <<< "$api_check_body" || fail "health must be able to check the Cloudflare API"
! grep -Eq -- '--proxy|socks' <<< "$api_check_body" || fail "Cloudflare API check must go directly, not through the WARP proxy"
awk '/health_cf_api_reachable/ && !a {a=NR} /_rekey_3xui_core_locked/ && !b {b=NR} END{exit !(a && b && a < b)}' <<< "$health_check_3xui_body" \
    || fail "health must check the Cloudflare API before deleting the 3X-UI registration"
keep_plus_body=$(awk '/^health_keep_plus_3xui\(\)/,/^}/' "$script")
grep -Fq 'save_config_val WARP_KEY_MODE_3XUI plus ACTIVE_KEY_NAME_3XUI' <<< "$keep_plus_body" \
    || fail "health must restore the WARP+ mode and key together"
awk '/_rekey_3xui_core_locked/ && !a {a=NR} /health_keep_plus_3xui/ && !b {b=NR} END{exit !(a && b && a < b)}' <<< "$health_check_3xui_body" \
    || fail "health must restore the WARP+ choice after re-registration"
plus_retry_body=$(awk '/^health_plus_retry_3xui\(\)/,/^}/' "$script")
grep -q 'health_plus_retry_3xui' <<< "$health_check_3xui_body" || fail "health must retry WARP+ when traffic shows Free"
for needle in 'flock -n' apply_warp_plus_key_3xui health_keep_plus_3xui HEALTH_PLUS_BACKOFF_MAX 'unset THREEXUI_LOCK_HELD'; do
    grep -Fq -- "$needle" <<< "$plus_retry_body" || fail "WARP+ retry must use: $needle"
done
grep -Eq '^HEALTH_PLUS_BACKOFF_MIN=3600$' "$script" && grep -Eq '^HEALTH_PLUS_BACKOFF_MAX=86400$' "$script" \
    || fail "WARP+ retry pause must start at 1 h and stop at 24 h"
! grep -Eq 'get_key_by_name|WARP_KEYS' <<< "$plus_retry_body" || fail "WARP+ retry must never read or log the key itself"

ensure_account_body=$(awk '/^awg_ensure_account\(\)/,/^}/' "$script")
grep -Fq 'awg_register_wait' <<< "$ensure_account_body" && grep -Fq '429' <<< "$ensure_account_body" \
    || fail "wgcf registration must pause between devices and retry after 429"
grep -Eq '^AWG_REGISTER_GAP=65$' "$script" || fail "pause between wgcf registrations must be 65 s"
grep -Fq 'AWG_REGISTER_ERROR' <<< "$(awk '/^rekey_awg_core\(\)/,/^}/' "$script")" \
    || fail "AWG rekey must say why Cloudflare refused a new device"

prepare_tools_body=$(awk '/^awg_prepare_warp_tools\(\)/,/^}/' "$script")
for needle in AWG_ALPINE_MIRRORS '--repositories-file /dev/null' '--no-cache' 'apk verify'; do
    grep -Fq -- "$needle" <<< "$prepare_tools_body" || fail "standard WireGuard tools must be fetched with: $needle"
done
! grep -q '\*/awg)' <<< "$prepare_tools_body" || fail "standard tools must not depend on the name of the container's wg"
warp_up_body=$(awk '/^awg_warp_up\(\)/,/^}/' "$script")
warp_down_body=$(awk '/^awg_warp_down\(\)/,/^}/' "$script")
for body_name in warp_up_body warp_down_body; do
    grep -q 'DEVTYPE' <<< "${!body_name}" && grep -q 'ip link del warp' <<< "${!body_name}" && grep -q 'awg-quick' <<< "${!body_name}" \
        || fail "$body_name must take down WARP interfaces of any kind"
done
grep -q 'AWG_WARP_KIND' <<< "$warp_up_body" || fail "awg_warp_up must report the interface kind"
probe_body=$(awk '/^health_awg_probe\(\)/,/^}/' "$script")
for needle in DEVTYPE time_connect '|| awg '; do
    grep -Fq -- "$needle" <<< "$probe_body" || fail "health probe must use: $needle"
done
check_awg_body=$(awk '/^health_check_awg_container\(\)/,/^}/' "$script")
grep -q 'health_awg_fix_kind' <<< "$check_awg_body" && grep -q 'health_awg_rtt' <<< "$check_awg_body" \
    || fail "health must rebuild amneziawg WARP and watch the latency"
shared_body=$(awk '/^awg_shared_key_detected\(\)/,/^}/' "$script")
grep -q 'PrivateKey' <<< "$shared_body" && ! grep -q 'wg show warp' <<< "$shared_body" \
    || fail "shared WARP keys must be compared from the config, not through wg"
upgrade_awg_body=$(awk '/^upgrade_awg_containers\(\)/,/^}/' "$script")
for needle in awg_resume_interrupted_rekey awg_warp_kind awg_shared_key_detected AWG_REKEY_ONLY; do
    grep -q "$needle" <<< "$upgrade_awg_body" || fail "upgrade must use $needle"
done
awk '/systemctl stop "\$health_unit"/ && !a {a=NR} /upgrade_awg_containers/ && !b {b=NR} /systemctl restart "\$health_unit"/ && !c {c=NR} END{exit !(a && b && c && a < b && b < c)}' <<< "$upgrade_hooks_body" \
    || fail "upgrade must stop the health service while it updates the containers"

grep -q 'disabled' <<< "$shared_body" || fail "manually disabled containers must not count as shared-key duplicates"
rekey_awg_body=$(awk '/^rekey_awg_core\(\)/,/^}/' "$script")
grep -q 'awg_resume_interrupted_rekey' <<< "$rekey_awg_body" || fail "rekey must finish an interrupted rekey first"
grep -q 'tools_ok' <<< "$upgrade_awg_body" || fail "upgrade must rebuild amneziawg WARP only with standard tools in place"
grep -q 'is-active' <<< "$upgrade_hooks_body" || fail "startup must restart a stopped health service"
grep -q 'Перевыпуск WARP не завершён' "$script" || fail "menu must show an unfinished rekey"

# 2.5.2: выбор клиентов по ключам, снимок на хосте, пересборка tun
clients_awk=$(awk "/^AWG_CLIENTS_AWK='/,/^END \\{ flush\\(\\) \\}'\$/" "$script")
[ -n "$clients_awk" ] || fail "AWG_CLIENTS_AWK must be defined"
body=${clients_awk#AWG_CLIENTS_AWK=\'}; body=${body%\'}
case "$body" in *\\*|*\'*) fail "AWG_CLIENTS_AWK must hold no backslashes or single quotes: it is embedded in start.sh" ;; esac
patch_body=$(awk '/^awg_patch_start_sh\(\)/,/^}/' "$script")
grep -q 'AWG_CLIENTS_AWK' <<< "$patch_body" || fail "start.sh block must resolve client addresses by key at runtime"
grep -q 'ENVIRON\["GOWARP_BLOCK"\]' <<< "$patch_body" && ! grep -q 'awk -v block' <<< "$patch_body" \
    || fail "start.sh block must reach awk through the environment, not awk -v"
! grep -q 'AWG_SELECTED_IPS' <<< "$patch_body" || fail "start.sh block must not bake client addresses in"
grep -q 'AWG_CLIENTS_AWK' <<< "$probe_body" || fail "health must count selected clients by key, like start.sh"
grep -q 'awg_kind_needs_rebuild' <<< "$check_awg_body" && grep -q 'awg_kind_needs_rebuild' <<< "$upgrade_awg_body" \
    || fail "health and upgrade must share the rebuild rule"
grep -q 'amneziawg|tun)' <<< "$(awk '/^awg_kind_needs_rebuild\(\)/,/}$/' "$script")" \
    || fail "userspace tun WARP must be rebuilt as well as amneziawg"
grep -q 'AWG_CLIENTS_MIGRATED' <<< "$upgrade_awg_body" && grep -q 'awg_save_clients' <<< "$upgrade_awg_body" \
    || fail "upgrade must convert the address-based client list to keys"
for fn in awg_snapshot_container awg_restore_container_snapshot awg_clear_container_snapshot; do
    fn_body=$(awk "/^${fn}\\(\\)/,/^}/" "$script")
    grep -q 'awg_ct_snap_dir' <<< "$fn_body" || fail "$fn must keep the snapshot on the host"
    ! grep -q '/tmp/warp-manager-rekey-backup' <<< "$fn_body" || fail "$fn must not use the container /tmp"
done
grep -q 'awg_import_legacy_snapshot' <<< "$(awk '/^awg_resume_interrupted_rekey\(\)/,/^}/' "$script")" \
    || fail "resume must pick up a snapshot left in the container by earlier versions"
grep -q 'AWG_BLOCK_TAG' <<< "$probe_body" && grep -q 'health_awg_refresh_block' <<< "$check_awg_body" \
    || fail "health must put back a missing or outdated start.sh block"
grep -q 'AWG_BLOCK_TAG' <<< "$patch_body" || fail "start.sh block must carry its version tag"
refresh_block_body=$(awk '/^health_awg_refresh_block\(\)/,/^}/' "$script")
grep -q 'flock -n' <<< "$refresh_block_body" && grep -q 'health_awg_probe' <<< "$refresh_block_body" \
    && grep -q 'awg_save_clients' <<< "$refresh_block_body" \
    || fail "block refresh must take the operation lock, re-check under it and convert address lists"
# Ревью 2.5.2: ядро без wireguard, сверка устройства при возобновлении, Endpoint, ключ не в argv, адреса правил.
fix_kind_body=$(awk '/^health_awg_fix_kind\(\)/,/^}/' "$script")
grep -q 'awg_kernel_wireguard_ok' <<< "$fix_kind_body" && grep -q 'awg_kernel_wireguard_ok' <<< "$upgrade_awg_body" \
    || fail "tun/amneziawg must not be rebuilt on a kernel without wireguard"
resume_body=$(awk '/^awg_resume_interrupted_rekey\(\)/,/^}/' "$script")
grep -q 'run_hash' <<< "$resume_body" || fail "resume must not go forward when the container already runs the old device"
grep -q 'awg_container_endpoint' <<< "$resume_body" && grep -q 'awg_container_endpoint' <<< "$rekey_awg_body" \
    || fail "rekey and resume must take a validated Endpoint"
grep -q 'IP endpoint.*>&2' <<< "$(awk '/^awg_resolve_endpoint\(\)/,/^}/' "$script")" \
    || fail "awg_resolve_endpoint must print its error to stderr, not into the Endpoint"
build_conf_body=$(awk '/^awg_build_warp_conf\(\)/,/^}/' "$script")
grep -q 'docker exec -i' <<< "$build_conf_body" && ! grep -q 'sh -c "set -e' <<< "$build_conf_body" \
    || fail "warp.conf with the private key must reach the container through stdin, not argv"
grep -q 'match' <<< "$probe_body" && grep -q 'match' <<< "$check_awg_body" \
    || fail "health must compare client rule addresses, not only their count"
grep -q 'awg_finish_client_rollback' <<< "$rekey_awg_body" \
    || fail "rekey must finish an unresolved client rollback instead of failing on the snapshot"
bot_ct_body=$(awk '/^        ct:\*\)/,/;;$/' "$script")
grep -q 'AWG_CLIENT_KEYS' <<< "$bot_ct_body" && ! grep -q 'AWG_CLIENT_IPS\[\*\]' <<< "$bot_ct_body" \
    || fail "Telegram client buttons must pick clients by key"

if [ "$failures" -ne 0 ]; then
    exit 1
fi

printf 'static_warp_checks: ok\n'
