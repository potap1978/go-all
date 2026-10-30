#!/usr/bin/env bash
# Exercise real callback handlers with Telegram and network mutations replaced.
set -euo pipefail
script="${1:-warp.sh}"
source <(awk '/^#  ENTRY POINT/{exit} {print}' "$script")
case_dir=$(mktemp -d)
trap 'rm -rf "$case_dir"' EXIT
WARP_DIR="$case_dir"
WARP_CONF="$case_dir/config"
WARP_KEYS="$case_dir/keys.list"
WARP_BOT_STATE="$case_dir/state"
WARP_LOG="$case_dir/log"
MODE=both
SOCKS_PORT=40000
WARP_KEY_MODE_3XUI=free
WARP_KEY_MODE_AWG=free
ACTIVE_KEY_NAME_3XUI=""
ACTIVE_KEY_NAME_AWG=""
printf 'key-1234|AAAAAAAA-BBBBBBBB-CCCC1234|0\n' > "$WARP_KEYS"
: > "$WARP_BOT_STATE"
: > "$case_dir/applied"
tg_answer_cb() { :; }
tg_edit() { printf '%s\n' "$3" > "$case_dir/message"; }
tg_send() { echo 'FAIL: unexpected Telegram send' >&2; return 1; }
curl() { echo 'FAIL: unexpected network call' >&2; return 1; }
docker() { echo 'FAIL: unexpected Docker call' >&2; return 1; }
warp-cli() { echo 'FAIL: unexpected warp-cli call' >&2; return 1; }
plus_apply_choice() { printf '%s|%s|%s\n' "$1" "$2" "$3" >> "$case_dir/applied"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
callback() {
    if ! bot_handle_callback "${2:-101}" 1 cb "$1" "${3:-101}"; then
        fail "callback $1"
    fi
}

keyboard=$(kbd_keys_main)
jq -e 'all(.[][]; (.callback_data | utf8bytelength) <= 64)' <<< "$keyboard" >/dev/null
jq -e '[.[][] | .callback_data] | contains(["k_apply:3xui:key:key-1234", "k_apply:awg:key:key-1234", "k_apply:3xui:free", "k_apply:awg:free"])' <<< "$keyboard" >/dev/null
[[ "$keyboard" != *AAAAAAAA* ]] || fail 'key leaked in keyboard'

callback k_apply:awg:key-1234
state=$(bot_state_get 101 101)
[[ "$state" = confirm_apply\|* ]] || fail 'missing confirmation state'
extra=${state#*|}; nonce=${extra%%:*}
[ ! -s "$case_dir/applied" ] || fail 'applied without confirmation'
callback k_confirm:wrong
[ ! -s "$case_dir/applied" ] || fail 'wrong nonce applied'
callback "k_confirm:$nonce" 102 102
[ ! -s "$case_dir/applied" ] || fail 'another user consumed confirmation'
callback "k_confirm:$nonce"
[ "$(cat "$case_dir/applied")" = 'awg|plus|key-1234' ] || fail 'wrong apply arguments'
callback "k_confirm:$nonce"
[ "$(wc -l < "$case_dir/applied")" -eq 1 ] || fail 'replayed confirmation applied twice'

bot_state_set 101 confirm_apply "expired:$(($(date +%s) - NONCE_TTL_SEC - 1)):awg:plus:key-1234" 101
callback k_confirm:expired
[ "$(wc -l < "$case_dir/applied")" -eq 1 ] || fail 'expired confirmation applied'

callback k_apply:3xui:free
state=$(bot_state_get 101 101); extra=${state#*|}; nonce=${extra%%:*}
callback "k_confirm:$nonce"
[ "$(tail -1 "$case_dir/applied")" = '3xui|free|' ] || fail 'Free did not clear key argument'

callback k_set_awg_plus
callback k_use_awg_key-1234
[ "$(wc -l < "$case_dir/applied")" -eq 2 ] || fail 'legacy keyboard changed networking'
callback k_add -100 101
grep -q 'private' "$case_dir/message" || fail 'group key entry accepted'
echo 'bot_flow_checks: OK (keyboard, confirmation, isolation, replay, expiry, Free, legacy, private chat)'
