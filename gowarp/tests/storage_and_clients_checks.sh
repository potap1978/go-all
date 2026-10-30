#!/usr/bin/env bash
# Regressions for durable state failures and Telegram client target isolation.
set -euo pipefail
source <(awk '/^#  ENTRY POINT/{exit} {print}' "${1:-warp.sh}")
case_dir=$(mktemp -d)
trap 'rm -rf "$case_dir"' EXIT
WARP_DIR="$case_dir"; WARP_CONF="$case_dir/config"; WARP_KEYS="$case_dir/keys"
WARP_BOT_STATE="$case_dir/state"; WARP_LOG="$case_dir/log"
CONTAINER=amnezia-awg; MODE=amnezia
printf 'SOCKS_PORT="40000"\nWARP_KEY_MODE_3XUI="free"\nACTIVE_KEY_NAME_3XUI=""\nWARP_KEY_MODE_AWG="plus"\nACTIVE_KEY_NAME_AWG="test"\n' > "$WARP_CONF"
printf 'test|AAAAAAAA-BBBBBBBB-CCCCCCCC|0\n' > "$WARP_KEYS"
source "$WARP_CONF"
fail() { echo "FAIL: $*" >&2; exit 1; }
curl() { fail 'unexpected network call'; }
docker() { fail 'unexpected Docker call'; }
warp-cli() { fail 'unexpected warp-cli call'; }
systemctl() { fail 'unexpected service call'; }
save_config_values WARP_KEY_MODE_AWG free ACTIVE_KEY_NAME_AWG ''
[ "$WARP_KEY_MODE_AWG:$ACTIVE_KEY_NAME_AWG" = free: ] || fail 'mode/key pair not committed'
save_config_values WARP_KEY_MODE_AWG plus ACTIVE_KEY_NAME_AWG test
bot_state_set 101 confirm_apply "nonce:$(date +%s):awg:plus:test" 101
cp "$WARP_CONF" "$case_dir/config-before"; cp "$WARP_KEYS" "$case_dir/keys-before"; cp "$WARP_BOT_STATE" "$case_dir/state-before"
mv() { return 7; }
if save_config_val SOCKS_PORT 41000; then fail 'failed config write reported success'; fi
if delete_key test; then fail 'failed key write reported success'; fi
if bot_state_set 101 changed value 101; then fail 'failed state write reported success'; fi
if bot_state_clear 101 101; then fail 'failed state consumption reported success'; fi
cmp "$WARP_CONF" "$case_dir/config-before"; cmp "$WARP_KEYS" "$case_dir/keys-before"; cmp "$WARP_BOT_STATE" "$case_dir/state-before"
tg_answer_cb() { :; }
tg_edit() { printf '%s\n' "$3" > "$case_dir/message"; printf '%s\n' "${4:-}" > "$case_dir/keyboard"; }
plus_apply_choice() { fail 'mutation executed without consuming confirmation'; }
if bot_handle_callback 101 1 cb k_confirm:nonce 101; then fail 'blocked action reported success'; fi
unset -f mv
printf '400 Bad Request\n' > "$case_dir/cf-error"
[ "$(_classify_license_error "$case_dir/cf-error")" = bad-request ] || fail 'HTTP400 reason hidden'
printf 'storage: atomic pair, write failures, preserved data, nonce consumption OK\n'

awg_discover_containers() { AWG_ALL_CONTAINERS=(amnezia-awg amnezia-awg2); }
awg_load_container_data() { :; }
K1=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
K2=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
awg_get_client_ips() {
    case "$CONTAINER" in
        amnezia-awg) AWG_CLIENT_IPS=(10.77.1.2/32); AWG_CLIENT_KEYS=("$K1") ;;
        amnezia-awg2) AWG_CLIENT_IPS=(10.77.2.2/32); AWG_CLIENT_KEYS=("$K2") ;;
        *) return 1 ;;
    esac
}
awg_parse_clients_table() { AWG_CLIENT_NAMES=(); }
awg_load_clients() {
    awg_get_client_ips || return 1
    AWG_SELECTED_KEYS=(); if [ -f "$case_dir/$CONTAINER" ]; then mapfile -t AWG_SELECTED_KEYS < "$case_dir/$CONTAINER"; fi
    awg_resolve_selected_ips
}
awg_commit_clients() {
    local target="$1"; shift
    printf '%s\n' "$target" >> "$case_dir/commits"
    : > "$case_dir/$target"
    if [ "$#" -gt 0 ]; then printf '%s\n' "$@" > "$case_dir/$target"; fi
}
callback() { if ! bot_handle_callback "${2:-101}" 1 cb "$1" "${2:-101}"; then fail "callback $1"; fi; }
callback cc:amnezia-awg
jq -e 'all(.[][]; (.callback_data | utf8bytelength)<=64)' "$case_dir/keyboard" >/dev/null
button=$(jq -r '.[0][0].callback_data' "$case_dir/keyboard")
[ "$button" = "ct:amnezia-awg:set:${K1:0:16}:1" ] || fail "client button does not carry the key: $button"
callback cc:amnezia-awg2 202
callback "$button"
[ "$(cat "$case_dir/amnezia-awg")" = "$K1" ] || fail 'old keyboard targeted wrong container or stored an address'
[ ! -f "$case_dir/amnezia-awg2" ] || fail 'other admin container changed'
callback "$button"
[ "$(wc -l < "$case_dir/amnezia-awg")" -eq 1 ] || fail 'replay duplicated or toggled selection'
[ "$CONTAINER" = amnezia-awg ] || fail 'callback changed global container'
commits=$(wc -l < "$case_dir/commits")
callback ct:amnezia-awg:set:10.77.1.2/32:1
[ "$(wc -l < "$case_dir/commits")" = "$commits" ] || fail 'address button of an old version mutated routing'
grep -q 'Список клиентов изменился' "$case_dir/message" || fail 'stale button not reported'
callback "ct:amnezia-awg:set:${K2:0:16}:1"
[ "$(wc -l < "$case_dir/commits")" = "$commits" ] || fail 'button of a client from another container mutated routing'
callback ct:0
[ "$(wc -l < "$case_dir/commits")" = "$commits" ] || fail 'legacy index mutated routing'
# Строки удалённых клиентов уходят при следующем сохранении, выбор остальных остаётся.
printf '%s\n10.77.1.99/32\nZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ=\n' "$K1" > "$case_dir/amnezia-awg"
callback "ct:amnezia-awg:set:${K1:0:16}:1"
[ "$(cat "$case_dir/amnezia-awg")" = "$K1" ] || fail 'bot save kept rows of deleted clients'
awg_commit_clients() { return 1; }
callback ct:amnezia-awg:none
grep -q 'Не удалось применить' "$case_dir/message" || fail 'failed routing write reported success'
printf 'clients: key buttons, two admins, replay, stale buttons, legacy callback and failure reporting OK\n'

# Программа awk, которая находит адреса выбранных клиентов по ключам: её выполняют goWARP, блок в start.sh
# и автопроверка. Здесь — awk хоста и, если есть, busybox awk (как в контейнерах Alpine).
cat > "$case_dir/wg0.conf" <<'CONF'
[Interface]
PrivateKey = cHJpdmF0ZXByaXZhdGVwcml2YXRlcHJpdmF0ZXByaXY=
Address = 10.8.1.0/24

[Peer]
PublicKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
AllowedIPs = 10.8.1.2/32

[Peer]
AllowedIPs = 10.8.1.3/32, fd00::3/128
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=

[Peer]
PublicKey=CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=
AllowedIPs = fd00::4/128,10.8.1.4/32

[Peer]
PublicKey = DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=
AllowedIPs = fd00::5/128
CONF
printf 'CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=\n10.8.9.9\n %s \r\nZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ=\n10.8.1.2/32\n' "$K1" > "$case_dir/clients.list"
awks=(awk)
command -v busybox >/dev/null 2>&1 && busybox awk 'BEGIN{}' </dev/null 2>/dev/null && awks+=("busybox awk")
for engine in "${awks[@]}"; do
    peers=$($engine "$AWG_CLIENTS_AWK" "$case_dir/wg0.conf" </dev/null)
    [ "$peers" = "10.8.1.2/32 $K1"$'\n'"10.8.1.3/32 $K2"$'\n'"10.8.1.4/32 CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=" ] \
        || fail "$engine: peers parsed wrong: $peers"
    picked=$($engine -v list="$case_dir/clients.list" "$AWG_CLIENTS_AWK" "$case_dir/wg0.conf" </dev/null | paste -sd' ' -)
    [ "$picked" = '10.8.9.9/32 10.8.1.2/32 10.8.1.4/32' ] || fail "$engine: selected addresses wrong: $picked"
    none=$($engine -v list="$case_dir/missing.list" "$AWG_CLIENTS_AWK" "$case_dir/wg0.conf" </dev/null)
    [ -z "$none" ] || fail "$engine: no client list must select nobody"
done
printf 'clients: key resolver OK with %s\n' "${awks[*]}"

# Список адресов от версий до 2.5.2 переводится в ключи; адрес без клиента остаётся как был.
source <(awk '/^awg_load_clients\(\)/,/^}/; /^awg_get_client_ips\(\)/,/^}/' "${1:-warp.sh}")
CONTAINER=amnezia-awg; AWG_VPN_CONF="$case_dir/wg0.conf"; AWG_WARP_CLIENTS="$case_dir/old.list"
printf '10.8.1.3/32\n10.8.1.2\n10.8.7.7/32\n' > "$AWG_WARP_CLIENTS"
docker() {
    [ "$1" = exec ] || fail "unexpected docker $1"
    case "$3" in
        awk) awk "$4" "$5" ;;
        sh) sh -c "$5" ;;
        *) fail "unexpected docker exec $*" ;;
    esac
}
awg_load_clients || fail 'address list could not be loaded'
[ "$AWG_CLIENTS_MIGRATED" = 1 ] || fail 'address list not marked for conversion'
[ "${AWG_SELECTED_KEYS[*]}" = "$K2 $K1 10.8.7.7/32" ] || fail "addresses not converted to keys: ${AWG_SELECTED_KEYS[*]}"
[ "${AWG_SELECTED_IPS[*]}" = '10.8.7.7/32 10.8.1.2/32 10.8.1.3/32' ] || fail "resolved order differs from start.sh: ${AWG_SELECTED_IPS[*]}"
# Сохранённый после перевода список start.sh разбирает в те же адреса и в том же порядке.
printf '%s\n' "${AWG_SELECTED_KEYS[@]}" > "$case_dir/converted.list"
picked=$(awk -v list="$case_dir/converted.list" "$AWG_CLIENTS_AWK" "$AWG_VPN_CONF" </dev/null | paste -sd' ' -)
[ "$picked" = "${AWG_SELECTED_IPS[*]}" ] || fail "goWARP and start.sh disagree: '$picked' vs '${AWG_SELECTED_IPS[*]}'"
AWG_SELECTED_KEYS=("$K1" 'bad key; rm -rf /'); if awg_validate_client_keys 2>/dev/null; then fail 'invalid key accepted'; fi
AWG_SELECTED_KEYS=("$K1" 10.8.7.7/32); awg_validate_client_keys || fail 'valid key list rejected'
printf 'clients: address list converted to keys, same addresses as start.sh OK\n'
