#!/usr/bin/env bash
# Exercise generated container startup edits without touching Docker or host networking.
set -euo pipefail
source <(awk '/^#  ENTRY POINT/{exit} {print}' "${1:-warp.sh}")
case_root=$(mktemp -d)
trap 'rm -rf "$case_root"' EXIT
CONTAINER=test
AWG_START_SH="$case_root/start.sh"
AWG_SELECTED_IPS=(10.77.1.2/32)
docker() {
    [ "$1" = exec ] && [ "$2" = -i ] || return 1
    shift 3
    sed "s|/opt/amnezia/start.sh.final-backup|$case_root/start.sh.backup|g" | "$@"
}
printf '#!/bin/sh\necho vpn-start\ntail -f /dev/null\n' > "$AWG_START_SH"
chmod 700 "$AWG_START_SH"
cp "$AWG_START_SH" "$case_root/original"
awg_patch_start_sh
sh -n "$AWG_START_SH"
# 2.5.2: копия start.sh.final-backup больше не снимается — она устаревала и откатывала поздние правки.
[ ! -e "$case_root/start.sh.backup" ]
[ "$(grep -c '^# --- WARP-MANAGER BEGIN ---$' "$AWG_START_SH")" = 1 ]
# Вторая строка блока — его версия: по ней автопроверка узнаёт блоки до 2.5.2.
[ "$(sed -n '/^# --- WARP-MANAGER BEGIN ---$/{n;p;}' "$AWG_START_SH")" = "$AWG_BLOCK_TAG" ]
[ "$(grep -n 'WARP-MANAGER END' "$AWG_START_SH" | cut -d: -f1)" -lt "$(grep -n '^tail' "$AWG_START_SH" | cut -d: -f1)" ]
cp "$AWG_START_SH" "$case_root/patched"
awg_patch_start_sh
cmp "$case_root/patched" "$AWG_START_SH"
# Блок не зависит от выбора клиентов: адреса он находит сам при запуске.
AWG_SELECTED_IPS=(10.77.1.9/32 10.77.1.10/32)
awg_patch_start_sh
cmp "$case_root/patched" "$AWG_START_SH"
echo 'startup: insertion, no stale backup, syntax and idempotence OK'

# A failed final rename must preserve the previous usable startup script.
mkdir "$case_root/bin"
printf '#!/bin/sh\nexit 1\n' > "$case_root/bin/mv"
chmod +x "$case_root/bin/mv"
if PATH="$case_root/bin:$PATH" awg_patch_start_sh; then
    echo 'FAIL: failed replacement reported success' >&2; exit 1
fi
cmp "$case_root/patched" "$AWG_START_SH"
echo 'startup: failed write preserves original OK'

# Refuse to commit a generated file if its shell syntax is invalid.
printf '\nif\n' >> "$AWG_START_SH"
cp "$AWG_START_SH" "$case_root/invalid"
if awg_patch_start_sh 2>/dev/null; then
    echo 'FAIL: invalid script accepted' >&2; exit 1
fi
cmp "$case_root/invalid" "$AWG_START_SH"
echo 'startup: syntax failure preserves original OK'

for ip in 10.77.1.2 10.77.1.2/32 192.0.2.0/24; do
    AWG_SELECTED_IPS=("$ip")
    awg_validate_client_ips
done
for ip in '10.77.1.2; touch /tmp/unwanted' 256.1.2.3/32 10.1.2.3/33 ::1 10.1.2.3/32junk; do
    AWG_SELECTED_IPS=("$ip")
    if awg_validate_client_ips 2>/dev/null; then
        echo 'FAIL: invalid client address accepted' >&2; exit 1
    fi
    AWG_SELECTED_KEYS=("$ip")
    if awg_save_clients 2>/dev/null; then
        echo 'FAIL: invalid client entry saved' >&2; exit 1
    fi
done
echo 'startup: IPv4 and key validation before mutation OK'

# Блок в start.sh: адреса выбранных по ключам берутся из конфига сервера при каждом запуске контейнера,
# поэтому смена подсети в goAmnezia не ломает выбор. Команды ip, iptables и flock — заглушки с журналом.
K1=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
K2=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
rt="$case_root/rt"; mkdir -p "$rt/bin" "$rt/warp"
AWG_WARP_DIR="$rt/warp"; AWG_WARP_CONF="$rt/warp/warp.conf"; AWG_WARP_CLIENTS="$rt/warp/clients.list"
awg_vpn_conf_candidates() { echo "$rt/missing.conf $rt/wg0.conf"; }
printf '[Interface]\n' > "$AWG_WARP_CONF"
printf '[Interface]\nAddress = 10.9.0.0/24\n\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.9.0.1/32\n\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.9.0.2/32\n' \
    "$K1" "$K2" > "$rt/wg0.conf"
printf '%s\n' "$K2" > "$AWG_WARP_CLIENTS"
printf '#!/bin/sh\necho vpn-start\ntail -f /dev/null\n' > "$rt/start.sh"
AWG_START_SH="$rt/start.sh"
awg_patch_start_sh
printf '#!/bin/sh\necho "ip $*" >> "$RT_LOG"\nexit 0\n' > "$rt/bin/ip"
printf '#!/bin/sh\necho "iptables $*" >> "$RT_LOG"\ncase "$*" in *" -C "*) exit 1 ;; esac\nexit 0\n' > "$rt/bin/iptables"
printf '#!/bin/sh\nexit 0\n' > "$rt/bin/flock"
chmod +x "$rt/bin/"*
refute() { if grep -q "$1" "$rt/log"; then echo "FAIL: unexpected '$1' in the block run" >&2; exit 1; fi; }
run_block() {
    : > "$rt/log"
    sed -n '/^# --- WARP-MANAGER BEGIN ---$/,/^# --- WARP-MANAGER END ---$/p' "$rt/start.sh" |
        sed "s|/tmp/gowarp-runtime.lock|$rt/lock|" | RT_LOG="$rt/log" PATH="$rt/bin:$PATH" sh
}
run_block
grep -qx 'ip rule add from 10.9.0.2/32 table 100 priority 100' "$rt/log"
grep -qx 'iptables -t nat -I POSTROUTING 1 -s 10.9.0.2/32 -o warp -j MASQUERADE' "$rt/log"
grep -q 'iptables -t mangle -A FORWARD -o warp' "$rt/log"
refute '10.9.0.1'
# goAmnezia сменила подсеть и перенумеровала клиентов: блок тот же, адрес — новый.
sed -i 's/10\.9\.0\.2/10.44.0.7/' "$rt/wg0.conf"
run_block
grep -qx 'ip rule add from 10.44.0.7/32 table 100 priority 100' "$rt/log"
refute '10.9.0.2'
# Выбор от версий до 2.5.2 (адрес) работает как раньше; клиентов не выбрано — правил клиентов нет.
printf '10.9.0.1/32\n' > "$AWG_WARP_CLIENTS"; run_block
grep -qx 'ip rule add from 10.9.0.1/32 table 100 priority 100' "$rt/log"
: > "$AWG_WARP_CLIENTS"; run_block
refute 'rule add from'
refute 'mangle'
echo 'startup: block resolves clients by key at run time, follows a subnet change OK'
