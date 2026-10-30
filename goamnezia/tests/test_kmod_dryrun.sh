#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — модуль ядра AmneziaWG, этап А (docs/KMOD_PLAN.md): наш код безопасен, когда контейнеры
# профилей оказались на модуле ядра (его поставил сосед или человек вручную).
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_kmod_dryrun.sh
# Нужны: bash ≥ 5, jq, python3. Ничего вне $TMP не пишем, к Docker и серверам не ходим.
#
#   А0  движок профиля по ответу контейнера: `ip -d link` (amneziawg → kernel, tun → program, wireguard → kernel),
#       запасом sysfs (DEVTYPE= / tun_flags); стенд — файл .iplink; один вызов на действие;
#   А1  KMOD-01: фильтр syncconf сверяется с эталоном Kaskad (sync_conf_text) — в [Interface] остаются PrivateKey,
#       ListenPort, FwMark; на модуле syncconf идёт с ним, на программе — прежней командой приложения;
#       профиль-set-param: Jc на модуле — рестарт (restarted=true), на программе — на лету; I1, PersistentKeepalive
#       и MTU рестарта не требуют; «Поднять заново» с другой маскировкой в копии — рестарт на модуле;
#       сквозь подставной docker, который исполняет команду syncconf и отдаёт то, что получил бы awg;
#   А2  KMOD-02: <rc -5>, <rc 99999>, <x 1>, сумма > 1280 — отказ словами; то, что пишем мы, — только теги, понятные
#       и модулю, и amneziawg-go (<c>, <d> <ds> <dz> — отказ на любом движке); подхват — в tests/test_import_dryrun.sh;
#   А3  status.kmod: модуль без метки при профиле 3.1 — warn, с меткой +kaskad-k31 — нет; поле в ответе моста.
#   А4  MTU-01 (S4 ≤ 40 у новых 2.0) — в tests/test_awgkeys.py: там тысячи генераций.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-kmod.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; rm -rf "$TMP"' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_DIR="$TMP/goamnezia"
export GOAMNEZIA_LOCK_DIR="$TMP/run"
export GOAMNEZIA_LOG_FILE="$TMP/goamnezia.log"
export GOAMNEZIA_AUDIT_LOG="$TMP/audit.log"
export GOAMNEZIA_NOTIFY_DIR="$TMP/run/notify"
export GOAMNEZIA_BUILD_ROOT="$TMP/build"
export GOAMNEZIA_SERVER_IP="203.0.113.10"
export UPSTREAM_DIR="$TMP/upstream"
export LIB_DIR="$ROOT/lib"
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"
export BOT_DIR="$TMP/bot" ADMIN_DIR="$TMP/admin" MCP_DIR="$TMP/mcp"
export NGINX_SITE_CONF="$TMP/etc-nginx/sites-available/goamnezia" NGINX_SITE_LINK="$TMP/etc-nginx/sites-enabled/goamnezia"
# Каталог модулей ядра стенда (А3): на машине прогона /sys/module может и быть — исход от неё не зависит.
export GOAMNEZIA_SYS_MODULE_DIR="$TMP/sysmodule"
# Файловая система контейнеров стенда: ответ `ip -d link` — файл .iplink, живой конфиг — opt/amnezia/awg/awg0.conf.
CFS="$TMP/cfs"
export GOAMNEZIA_CONTAINER_FS_DIR="$CFS"
unset DOCKER_HOST DOCKER_CONTEXT
export GOAMNEZIA_DOCKER_SOCK="$TMP/nosuch.sock"
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$GOAMNEZIA_SECRET_TMPDIR" "$GOAMNEZIA_SYS_MODULE_DIR" \
    "$CFS/amnezia-awg2" "$CFS/amnezia-awg3"
HOSTBIN="$TMP/hostbin"; mkdir -p "$HOSTBIN"
printf '#!/bin/sh\nexit 1\n' >"$HOSTBIN/systemctl"; chmod +x "$HOSTBIN/systemctl"
export PATH="$HOSTBIN:$PATH"
BASH5="${BASH5:-bash}"
INSTALL="$ROOT/install.sh"

pass=0; fail=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 900; return 0; }
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
is_json() { jq -e . <<<"$1" >/dev/null 2>&1; }
run() {
    GA_ERR_CODE=""; GA_ERR_MSG=""; GA_LAST_ERROR_CODE=""
    "$@" >"$OUT" 2>"$ERR"; rc=$?
    out=$(<"$OUT")
    return 0
}
errs() { tail -n 6 "$ERR" 2>/dev/null; }
jget() { jq -r "$1" <<<"$out" 2>/dev/null; }

# Ответы `ip -d link show dev awg0` из контейнеров (iproute2 6.6, alpine 3.19, ядро 6.8) — программа и модуль.
IPLINK_TUN='3: awg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280 qdisc fq_codel state UNKNOWN mode DEFAULT group default qlen 500
    link/none  promiscuity 0  allmulti 0 minmtu 68 maxmtu 65535
    tun type tun pi off vnet_hdr off persist off addrgenmode none numtxqueues 1 numrxqueues 1 gso_max_size 65536 gso_max_segs 65535
INTERFACE=awg0
IFINDEX=3
tun_flags=1'
IPLINK_KMOD='4: awg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280 qdisc noqueue state UNKNOWN mode DEFAULT group default qlen 1000
    link/none  promiscuity 0  allmulti 0 minmtu 0 maxmtu 2147483552
    amneziawg addrgenmode none numtxqueues 1 numrxqueues 1 gso_max_size 65536 gso_max_segs 65535
DEVTYPE=amneziawg
INTERFACE=awg0
IFINDEX=4'
engine_fixture() { printf '%s\n' "$2" >"$CFS/$1/.iplink"; }

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia awg_params users export edgeips import; do
    # shellcheck disable=SC1090
    source "$ROOT/lib/$_m.sh"
done
mkdir -p "$UPSTREAM_DIR"
cp -R "$ROOT/lib/upstream/." "$UPSTREAM_DIR/"
mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR"
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOF
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}, "options": {"drop_icmp": false}}
EOF
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"
printf '  bash %s, jq %s\n' "$BASH_VERSION" "$(jq --version 2>/dev/null)"

# ── А0. Движок профиля ───────────────────────────────────────────────────────
section "А0: движок профиля по ответу контейнера"
check "tun (amneziawg-go) → program" '[ "$(printf "%s\n" "$IPLINK_TUN" | ga_amn_engine_parse)" = program ]'
check "amneziawg (модуль ядра) → kernel" '[ "$(printf "%s\n" "$IPLINK_KMOD" | ga_amn_engine_parse)" = kernel ]'
check "wireguard (обычный модуль WireGuard у legacy-контейнера на wg0) → kernel" \
    '[ "$(printf "5: wg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1420\n    link/none  promiscuity 0\n    wireguard addrgenmode none\n" | ga_amn_engine_parse)" = kernel ]'
check "ip без -d (busybox): по sysfs — DEVTYPE=amneziawg → kernel, tun_flags → program" \
    '[ "$(printf "INTERFACE=awg0\nDEVTYPE=amneziawg\n" | ga_amn_engine_parse)" = kernel ] && [ "$(printf "INTERFACE=awg0\ntun_flags=1\n" | ga_amn_engine_parse)" = program ]'
check "пусто (контейнер не ответил, интерфейса нет) → unknown" '[ "$(printf "" | ga_amn_engine_parse)" = unknown ]'
check "тип в конце строки, с пробелами и CR — тоже узнаётся" \
    '[ "$(printf "amneziawg\n" | ga_amn_engine_parse)" = kernel ] && [ "$(printf "  tun  \n" | ga_amn_engine_parse)" = program ] && [ "$(printf "DEVTYPE=amneziawg\r\n" | ga_amn_engine_parse)" = kernel ]'
check "строка «tunnel…» или имя awg0 в первой строке за тип не принимаются" \
    '[ "$(printf "7: tunl0: <NOARP>\n    link/ipip 0.0.0.0\n    tunnel6 x\n" | ga_amn_engine_parse)" = unknown ]'

# ── Установка профилей (dry-run) ─────────────────────────────────────────────
section "установка профилей awg2 (2.0) и awg3 (3.1)"
run amnezia_profile_install awg2 40000 default
check "profile-install awg2: rc=0" '[ "$rc" = 0 ]' "$(errs)"
# А3 до профиля 3.1: модуль без метки, а профиля 3.1 нет — предупреждать не о чем.
mkdir -p "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg"
printf '3.1.20260812\n' >"$GOAMNEZIA_SYS_MODULE_DIR/amneziawg/version"
run ga_amn_kmod_json
check "А3: модуль без метки, профиля 3.1 нет → loaded, patched=false, warn=false" \
    '[ "$(jget .loaded)" = true ] && [ "$(jget .version)" = 3.1.20260812 ] && [ "$(jget .patched)" = false ] && [ "$(jget .warn)" = false ]' "$out"
run amnezia_profile_install awg3 40001
check "profile-install awg3: rc=0, line=3.1, HeaderProtectionKey в awg0.conf" \
    '[ "$rc" = 0 ] && [ "$(jq -r .line <<<"$out")" = 3.1 ] && grep -q "^HeaderProtectionKey = " "$STATE_DIR/amnezia-awg3/awg0.conf"' "$(errs)"
run users_add anna "Анна" awg2,awg3
check "users_add anna (awg2, awg3): rc=0" '[ "$rc" = 0 ]' "rc=$rc $(errs)"

# ── А1. Фильтр syncconf ──────────────────────────────────────────────────────
section "А1: фильтр syncconf против эталона Kaskad (sync_conf_text)"
SAMPLE="$TMP/sample31.conf"
{
    # То, чего наша сборка не пишет, но может оказаться в чужом конфиге: FwMark, незакомментированный I2,
    # ключ строчными без пробелов, заголовок секции с пробелами.
    awk '{ print } /^ListenPort = / { print "FwMark = 0x1"; print "I2 = <r 5>"; print "  jc=7" }' \
        "$STATE_DIR/amnezia-awg3/awg0.conf"
    printf '\n [ peer ] \nPublicKey = R7PrjHHtJGilh+xUME6haQ4KA6Xwev/4ifa5JY7kiG0=\nAllowedIPs = 10.8.3.9/32\n'
} >"$SAMPLE"
awk "${GA_AMN_SYNC_AWK:-}" "$SAMPLE" >"$TMP/sync.awk.txt"
python3 - "$SAMPLE" >"$TMP/sync.ref.txt" <<'EOF'
import sys
def sync_conf_text(text):  # навык Kaskad amneziawg-kernel-module, references/pitfalls.md §2
    keep = ("privatekey", "listenport", "fwmark")
    out, section = [], ""
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s.lower()
        elif section == "[interface]" and s and not s.startswith("#"):
            if s.split("=", 1)[0].strip().lower() not in keep:
                continue
        out.append(line)
    return "\n".join(out) + "\n"
sys.stdout.write(sync_conf_text(open(sys.argv[1], encoding="utf-8").read()))
EOF
check "awk-фильтр даёт ровно то же, что эталон" 'cmp -s "$TMP/sync.awk.txt" "$TMP/sync.ref.txt"' "$(diff "$TMP/sync.ref.txt" "$TMP/sync.awk.txt" | head -8)"
check "в [Interface] остались PrivateKey, ListenPort, FwMark — и ничего из маскировки" \
    'grep -q "^PrivateKey = " "$TMP/sync.awk.txt" && grep -q "^ListenPort = 40001$" "$TMP/sync.awk.txt" && grep -q "^FwMark = 0x1$" "$TMP/sync.awk.txt" && ! grep -Eiq "^[[:space:]]*(HeaderProtectionKey|Jc|Jmin|Jmax|S[1-4]|H[1-4]|I[1-5]|Rekey|Reject|Keepalive|MaxHandshake|RandomTrailers|DisableCookies)[[:space:]]*=" "$TMP/sync.awk.txt"' \
    "$(head -12 "$TMP/sync.awk.txt")"
check "все [Peer] на месте, с ключами и адресами" \
    '[ "$(grep -ci "^[[:space:]]*\[[[:space:]]*peer[[:space:]]*\]" "$TMP/sync.awk.txt")" = "$(grep -ci "^[[:space:]]*\[[[:space:]]*peer[[:space:]]*\]" "$SAMPLE")" ] && [ "$(grep -c "^PublicKey = " "$TMP/sync.awk.txt")" = "$(grep -c "^PublicKey = " "$SAMPLE")" ] && grep -q "^AllowedIPs = 10.8.3.9/32$" "$TMP/sync.awk.txt"'
# I1–I5 — не маскировка интерфейса ни закомментированные, ни без «#» (KEENETIC-I1-R, R1): I-пакеты шлёт клиент.
check "маскировка [Interface] без ключа, порта, комментариев и I1–I5 (I2 без «#» — тоже не в счёт)" \
    'ga_amn_mask_lines <"$SAMPLE" | grep -q "^headerprotectionkey=" && ga_amn_mask_lines <"$SAMPLE" | grep -q "^jc=7$" && ! ga_amn_mask_lines <"$SAMPLE" | grep -Eq "^(privatekey|listenport|fwmark|address|mtu|i[1-5])="'

# ── А1. syncconf по движку (dry-run) ─────────────────────────────────────────
section "А1: amnezia_syncconf — на модуле без маскировки, на программе как прежде"
PLAIN3="awg syncconf awg0 <(awg-quick strip /opt/amnezia/awg/awg0.conf)"
engine_fixture amnezia-awg3 "$IPLINK_KMOD"
engine_fixture amnezia-awg2 "$IPLINK_TUN"
run amnezia_syncconf awg3
check "awg3 на модуле: rc=0, движок спрошен ОДИН раз, syncconf через фильтр" \
    '[ "$rc" = 0 ] && [ "$(grep -c "ip -d link show dev awg0" "$ERR")" = 1 ] && grep -qF "awg syncconf awg0 <(awg-quick strip /opt/amnezia/awg/awg0.conf | awk " "$ERR" && grep -qF "privatekey" "$ERR" && [ "$GA_AMN_SYNC_ENGINE" = kernel ]' \
    "rc=$rc $(grep DRY "$ERR" | head -3)"
check "awg3 на модуле: прежней команды (весь [Interface] с HeaderProtectionKey) нет, рестарта нет" \
    '! grep -qF "$PLAIN3" "$ERR" && ! grep -q "docker restart" "$ERR"' "$(grep DRY "$ERR" | head -3)"
run amnezia_syncconf awg2
check "awg2 на программе: ровно команда приложения, без фильтра" \
    '[ "$rc" = 0 ] && grep -qF "$PLAIN3" "$ERR" && ! grep -qF "| awk" "$ERR" && [ "$GA_AMN_SYNC_ENGINE" = program ]' "$(grep DRY "$ERR" | head -3)"
: >"$CFS/amnezia-awg3/.iplink"
run amnezia_syncconf awg3
check "движок не узнали (unknown) — как на модуле: через фильтр" \
    '[ "$rc" = 0 ] && grep -qF "| awk " "$ERR" && [ "$GA_AMN_SYNC_ENGINE" = unknown ]' "$(grep DRY "$ERR" | head -3)"
run amnezia_syncconf awg3 program
check "движок передан вызывающим — второй раз не спрашивается" \
    '[ "$rc" = 0 ] && ! grep -q "ip -d link" "$ERR" && grep -qF "$PLAIN3" "$ERR"' "$(grep DRY "$ERR" | head -3)"
engine_fixture amnezia-awg3 "$IPLINK_KMOD"
run users_add boris "" awg3
check "люди на модуле (users_add): rc=0, syncconf через фильтр, без рестарта" \
    '[ "$rc" = 0 ] && grep -qF "awg syncconf awg0 <(awg-quick strip /opt/amnezia/awg/awg0.conf | awk " "$ERR" && ! grep -q "docker restart" "$ERR"' \
    "rc=$rc $(grep DRY "$ERR" | grep -E "syncconf|restart" | head -3)"

# ── А1. profile-set-param ────────────────────────────────────────────────────
section "А1: смена параметров — на модуле маскировка рестартом, на программе на лету"
# Jc = 7: генератор даёт 4–6, значит, значение точно новое.
run amnezia_profile_set_param awg3 Jc 7
check "awg3 (модуль) Jc=7: rc=0, DRY docker restart, syncconf не было" \
    '[ "$rc" = 0 ] && grep -q "DRY: docker restart amnezia-awg3" "$ERR" && ! grep -q "awg syncconf" "$ERR"' "rc=$rc $(errs)"
check "… ответ: restarted=true, engine=kernel, reissue_required=false; в config.json и awg0.conf Jc = 7" \
    '[ "$(jget .restarted)" = true ] && [ "$(jget .engine)" = kernel ] && [ "$(jget .reissue_required)" = false ] && [ "$(jq -r .profiles.awg3.params.Jc "$CONFIG_FILE")" = 7 ] && grep -q "^Jc = 7$" "$STATE_DIR/amnezia-awg3/awg0.conf"' "$out"
check "… человеку честно: «клиенты переподключатся сами, обычно за 15–25 секунд»" 'grep -q "15–25 секунд" "$ERR"' "$(errs)"
check "… движок спрошен один раз на всё действие (проверка параметров и применение)" '[ "$(grep -c "ip -d link show dev awg0" "$ERR")" = 1 ]' "$(grep -c "ip -d link" "$ERR")"
run amnezia_profile_set_param awg2 Jc 6
check "awg2 (программа) Jc=6: syncconf прежней командой, рестарта нет, restarted=false, engine=program" \
    '[ "$rc" = 0 ] && grep -qF "$PLAIN3" "$ERR" && ! grep -q "docker restart" "$ERR" && [ "$(jget .restarted)" = false ] && [ "$(jget .engine)" = program ]' "rc=$rc $out $(errs)"
run amnezia_profile_set_param awg3 I1 "<b 0x0102><r 8>"
check "awg3 (модуль) I1: у сервера он закомментирован — рестарта нет, syncconf через фильтр" \
    '[ "$rc" = 0 ] && ! grep -q "docker restart" "$ERR" && grep -qF "| awk " "$ERR" && [ "$(jget .restarted)" = false ] && grep -q "^# I1 = <b 0x0102><r 8>$" "$STATE_DIR/amnezia-awg3/awg0.conf"' "rc=$rc $out $(errs)"
run amnezia_profile_set_param awg3 PersistentKeepalive 30-40
check "awg3 (модуль) PersistentKeepalive — поле клиентов: рестарта нет" \
    '[ "$rc" = 0 ] && ! grep -q "docker restart" "$ERR" && [ "$(jget .restarted)" = false ]' "rc=$rc $out $(errs)"
run amnezia_profile_set_param awg3 MTU 1360
check "awg3 (модуль) MTU — на живой интерфейс, без рестарта" \
    '[ "$rc" = 0 ] && grep -q "ip link set dev awg0 mtu 1360" "$ERR" && ! grep -q "docker restart" "$ERR" && [ "$(jget .restarted)" = false ]' "rc=$rc $(errs)"
run amnezia_profile_set_param awg3 RekeyAfterTime 105-115   # в коридоре 60–120 (PARAM-03, 1.0.13)
check "awg3 (модуль) RekeyAfterTime (клиентское поле 3.x в [Interface]) — рестартом" \
    '[ "$rc" = 0 ] && grep -q "DRY: docker restart amnezia-awg3" "$ERR" && ! grep -q "awg syncconf" "$ERR" && [ "$(jget .restarted)" = true ]' "rc=$rc $(errs)"
run amnezia_profile_set_param awg3 H1 5
check "awg3 (модуль) H1 — серверный, как прежде: рестарт, reissue_required=true, restarted=true" \
    '[ "$rc" = 0 ] && [ "$(grep -c "DRY: docker restart amnezia-awg3" "$ERR")" = 1 ] && [ "$(jget .reissue_required)" = true ] && [ "$(jget .restarted)" = true ]' "rc=$rc $(errs)"
run amnezia_profile_set_param awg3 Jc 7
check "то же значение ещё раз — маскировка не менялась, рестарта нет" \
    '[ "$rc" = 0 ] && ! grep -q "docker restart" "$ERR" && [ "$(jget .restarted)" = false ]' "rc=$rc $(errs)"

# ── А1. Поднять заново: копия меняет маскировку ──────────────────────────────
section "А1: «Поднять заново» на модуле — разошлась маскировка копии с контейнером → рестарт"
mkdir -p "$CFS/amnezia-awg3/opt/amnezia/awg"
sed 's/^Jc = .*/Jc = 9/' "$STATE_DIR/amnezia-awg3/awg0.conf" >"$CFS/amnezia-awg3/opt/amnezia/awg/awg0.conf"
run amnezia_profile_repair awg3
check "живой Jc = 9, в копии Jc = 7: rc=0, DRY docker restart, syncconf не было" \
    '[ "$rc" = 0 ] && grep -q "DRY: docker restart amnezia-awg3" "$ERR" && ! grep -q "awg syncconf" "$ERR"' "rc=$rc $(errs)"
cp "$STATE_DIR/amnezia-awg3/awg0.conf" "$CFS/amnezia-awg3/opt/amnezia/awg/awg0.conf"
run amnezia_profile_repair awg3
check "маскировка совпадает: syncconf через фильтр, без рестарта" \
    '[ "$rc" = 0 ] && ! grep -q "docker restart" "$ERR" && grep -qF "| awk " "$ERR"' "rc=$rc $(errs)"
engine_fixture amnezia-awg2 "$IPLINK_TUN"
mkdir -p "$CFS/amnezia-awg2/opt/amnezia/awg"
sed 's/^Jc = .*/Jc = 9/' "$STATE_DIR/amnezia-awg2/awg0.conf" >"$CFS/amnezia-awg2/opt/amnezia/awg/awg0.conf"
run amnezia_profile_repair awg2
check "на программе та же разница — syncconf прежней командой, без рестарта" \
    '[ "$rc" = 0 ] && ! grep -q "docker restart" "$ERR" && grep -qF "$PLAIN3" "$ERR"' "rc=$rc $(errs)"
rm -rf "$CFS/amnezia-awg2/opt" "$CFS/amnezia-awg3/opt"

# ── А2. Проверка I-пакетов ───────────────────────────────────────────────────
# I-пакеты шлёт клиент (у сервера I1–I5 закомментированы), поэтому годность тега решают реализации у клиентов, а не
# движок сервера: то, что пишем мы, — только теги, понятные и модулю ядра, и amneziawg-go (b t r rc rd).
section "А2: I-пакеты, которые пишем мы, — только общие теги, на любом движке; причина словами"
i_rejected() {   # i_rejected <профиль> <значение> <слово в причине> [ключ, I2] → 0: отказ словами, ключ и контейнер не тронуты
    local before k="${4:-I2}"
    before=$(jq -r '.profiles.'"$1"'.params.'"$k"' // ""' "$CONFIG_FILE")
    run amnezia_profile_set_param "$1" "$k" "$2"
    [ "$rc" = 2 ] && [ "$GA_ERR_CODE" = params_invalid ] && grep -qF -- "$3" <<<"$GA_ERR_MSG" \
        && [ "$(jq -r '.profiles.'"$1"'.params.'"$k"' // ""' "$CONFIG_FILE")" = "$before" ] && ! grep -q "docker restart\|awg syncconf" "$ERR"
}
# irej <описание> <профиль> <значение> <слово> [ключ] — i_rejected в этой оболочке: причина отказа видна в подсказке.
irej() { if i_rejected "$2" "$3" "$4" "${5:-I2}"; then ok "$1"; else bad "$1" "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"; fi; }
irej "<rc -5> → отказ: размер должен быть положительным (модуль ядра падает), ничего не записано" \
    awg3 "<rc -5>" "положительным числом, а здесь «-5»"
irej "<rc 99999> → отказ: пакет 99999 байт, предел 1280" awg3 "<rc 99999>" "99999 байт"
irej "сумма тегов больше 1280 (<rc 1000><rd 281>) → отказ: 1281 байт" awg2 "<rc 1000><rd 281>" "1281 байт"
irej "<x 1> → отказ: тег <x> не знают ни модуль, ни программа" awg3 "<x 1>" "тег <x> не знают"
# <c> — только модуль ядра, d ds dz — только amneziawg-go: отказ на ЛЮБОМ движке сервера (раньше на модуле <c>
# пропускался, а клиенты AmneziaVPN на телефонах — amneziawg-go — его не поймут).
irej "<c> на модуле ядра (awg3) → отказ: клиенты AmneziaVPN на телефонах его не поймут" \
    awg3 "<c><r 5>" "клиенты AmneziaVPN на телефонах с таким I не подключатся"
irej "<c> на программе (awg2) → отказ" awg2 "<c><r 5>" "понимает только модуль ядра"
for _t in "<d><r 5>" "<ds><r 1>" "<dz 2><r 5>"; do
    irej "$_t на программе (awg2) → отказ: роутеры с модулем ядра его не поймут" awg2 "$_t" "роутеры и Linux с модулем ядра"
    irej "$_t на модуле ядра (awg3) → отказ" awg3 "$_t" "понимает только программа amneziawg-go"
done
: >"$CFS/amnezia-awg3/.iplink"
irej "движок не узнан — то же правило: <c> → отказ" awg3 "<c><r 5>" "понимает только модуль ядра"
engine_fixture amnezia-awg3 "$IPLINK_KMOD"
# KEENETIC-I1: пакет, который пишем мы, начинается с <b> — иначе Keenetic 5.1 не примет ключ; в каждом из I1–I5.
for _k in I1 I2 I3 I4 I5; do
    irej "KEENETIC-I1: $_k = <r 2><b 0x0102> → отказ: почему (Keenetic) и как исправить (начните с <b 0x…>)" \
        awg2 "<r 2><b 0x0102>" "$_k не принят: пакет начинается с <r>, а роутеры Keenetic (KeeneticOS 5.1)" "$_k"
done
irej "KEENETIC-I1: <t><b 0x01> → отказ словами «Начните с <b 0x…>»" awg3 "<t><b 0x01>" "Начните с <b 0x…>"
run amnezia_profile_set_param awg2 I2 "<b 0x01><t><rc 8><rd 4><r 2>"
check "общие теги (b t r rc rd) на программе — принято" \
    '[ "$rc" = 0 ] && [ "$(jq -r .profiles.awg2.params.I2 "$CONFIG_FILE")" = "<b 0x01><t><rc 8><rd 4><r 2>" ]' "rc=$rc $(errs)"
run amnezia_profile_set_param awg3 I2 "<b 0x01><t><rc 8><rd 4><r 2>"
check "общие теги на модуле ядра — принято" '[ "$rc" = 0 ]' "rc=$rc $(errs)"
# Подхваченный I с тегом не всех реализаций (там это предупреждение, а не отказ) не мешает менять другое; переписать
# его таким же — нельзя.
ga_config_merge '{"profiles":{"awg3":{"params":{"I3":"<c><r 5>"}}}}' >/dev/null 2>&1
run amnezia_profile_set_param awg3 Jc 8
check "подхваченный I3 = <c><r 5> не мешает сменить Jc" '[ "$rc" = 0 ] && [ "$(jq -r .profiles.awg3.params.Jc "$CONFIG_FILE")" = 8 ]' \
    "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
run amnezia_profile_set_param awg3 I3 "<c><r 6>"
check "… а записать в I3 снова <c> — отказ словами" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = params_invalid ] && grep -qF "I3 не принят" <<<"$GA_ERR_MSG"' \
    "rc=$rc msg=$GA_ERR_MSG"
ga_config_merge '{"profiles":{"awg3":{"params":{"I3":"<rc -5>"}}}}' >/dev/null 2>&1
run amnezia_profile_set_param awg3 Jc 7
check "… а подхваченный I3 = <rc -5> (беда при любом правиле) — отказ словами и при смене Jc" \
    '[ "$rc" = 2 ] && grep -qF "I3 не принят" <<<"$GA_ERR_MSG" && grep -qF "«-5»" <<<"$GA_ERR_MSG"' "rc=$rc msg=$GA_ERR_MSG"
ga_config_merge '{"profiles":{"awg3":{"params":{"I3":""}}}}' >/dev/null 2>&1
load_language en >/dev/null 2>&1
irej "по-английски — тоже словами" awg3 "<rc -5>" "must be a positive number, here it is «-5»"
irej "по-английски: <c> — словами про клиентов на телефонах" awg3 "<c><r 5>" "AmneziaVPN clients on phones will not connect"
irej "по-английски: KEENETIC-I1 — почему и как исправить" awg3 "<r 2><b 0x0102>" "Keenetic routers (KeeneticOS 5.1) reject a key whose signature packet does not start with <b>. Start with <b 0x…>"
load_language ru >/dev/null 2>&1

# ── А3. Модуль ядра на хосте ─────────────────────────────────────────────────
section "А3: status.kmod — модуль без исправлений при профиле 3.1"
run ga_amn_kmod_json
check "модуль 3.1.20260812 без метки, профиль 3.1 есть → warn=true" \
    '[ "$(jget .loaded)" = true ] && [ "$(jget .patched)" = false ] && [ "$(jget .warn)" = true ]' "$out"
printf '3.1.20260906+kaskad-k31.1\n' >"$GOAMNEZIA_SYS_MODULE_DIR/amneziawg/version"
run ga_amn_kmod_json
check "с меткой +kaskad-k31.1 → patched=true, warn=false" \
    '[ "$(jget .version)" = 3.1.20260906+kaskad-k31.1 ] && [ "$(jget .patched)" = true ] && [ "$(jget .warn)" = false ]' "$out"
printf '3.1.20260906+goamnezia-k31.1\n' >"$GOAMNEZIA_SYS_MODULE_DIR/amneziawg/version"
run ga_amn_kmod_json
check "с меткой +goamnezia-k31 → patched=true" '[ "$(jget .patched)" = true ] && [ "$(jget .warn)" = false ]' "$out"
rm -rf "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg"
run ga_amn_kmod_json
check "модуля нет → loaded=false, warn=false" '[ "$(jget .loaded)" = false ] && [ "$(jget .version)" = "" ] && [ "$(jget .warn)" = false ]' "$out"
mkdir -p "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg"
printf '3.1.20260812\n' >"$GOAMNEZIA_SYS_MODULE_DIR/amneziawg/version"
"$BASH5" "$INSTALL" --action=status --json >"$OUT" 2>"$ERR"; rc=$?
out=$(tail -n 1 "$OUT")
check "мост: status.kmod = {loaded: true, version, patched: false, warn: true}" \
    '[ "$rc" = 0 ] && [ "$(jget .status)" = success ] && [ "$(jget .kmod.loaded)" = true ] && [ "$(jget .kmod.version)" = 3.1.20260812 ] && [ "$(jget .kmod.patched)" = false ] && [ "$(jget .kmod.warn)" = true ]' \
    "rc=$rc ${out:0:300} $(errs)"
"$BASH5" "$INSTALL" --action=status --brief=on --json >"$OUT" 2>"$ERR"; rc=$?
out=$(tail -n 1 "$OUT")
check "мост: и в brief=on (по нему «Обзор» панели)" '[ "$rc" = 0 ] && [ "$(jget .kmod.warn)" = true ]' "rc=$rc ${out:0:300}"
"$BASH5" "$INSTALL" --action=profile-set-param --profile=awg3 --key=Jc --value=5 --json >"$OUT" 2>"$ERR"; rc=$?
out=$(tail -n 1 "$OUT")
check "мост: profile-set-param на модуле отдаёт restarted=true и engine=kernel" \
    '[ "$rc" = 0 ] && [ "$(jget .status)" = success ] && [ "$(jget .restarted)" = true ] && [ "$(jget .result.restarted)" = true ] && [ "$(jget .result.engine)" = kernel ]' \
    "rc=$rc ${out:0:400} $(errs)"

# ── А1 сквозь подставной docker ──────────────────────────────────────────────
section "А1 без dry-run: подставной docker исполняет команду syncconf, awg записывает, что получил"
FK="$TMP/fakedocker"; mkdir -p "$FK/bin"
export FK
cat >"$FK/bin/docker" <<'EOF'
#!/usr/bin/env bash
# exec -i <имя> sh -c … — ответ контейнера на вопрос о движке ($FK/<имя>.iplink); exec -i <имя> bash -c <команда> —
# исполнить её здесь: awg-quick strip отдаёт $FK/<имя>.conf, awg syncconf пишет полученное в $FK/<имя>.sync.
printf '%s\n' "$*" >>"$FK/calls"
[ "${1:-}" = exec ] || exit 0
shift; [ "${1:-}" = -i ] && shift
name="${1:-}"; shift
case "${1:-}" in
    sh)   cat "$FK/$name.iplink" 2>/dev/null; exit 0 ;;
    bash) shift; [ "${1:-}" = -c ] && shift
          FK_NAME="$name" PATH="$FK/stub:$PATH" bash -c "$1"; exit $? ;;
esac
exit 0
EOF
mkdir -p "$FK/stub"
printf '#!/bin/sh\n[ "$1" = strip ] && cat "$FK/$FK_NAME.conf"\n' >"$FK/stub/awg-quick"
printf '#!/bin/sh\n[ "$1" = syncconf ] && cat "$3" >"$FK/$FK_NAME.sync"\n' >"$FK/stub/awg"
chmod +x "$FK/bin/docker" "$FK/stub/awg-quick" "$FK/stub/awg"
cp "$STATE_DIR/amnezia-awg3/awg0.conf" "$FK/amnezia-awg3.conf"
cp "$STATE_DIR/amnezia-awg2/awg0.conf" "$FK/amnezia-awg2.conf"
printf '%s\n' "$IPLINK_KMOD" >"$FK/amnezia-awg3.iplink"
printf '%s\n' "$IPLINK_TUN" >"$FK/amnezia-awg2.iplink"
(
    export GOAMNEZIA_DRY_RUN=0 DOCKER_API_VERSION=1.43 PATH="$FK/bin:$PATH"
    GA_DOCKER_API_PINNED=""
    amnezia_syncconf awg3 >"$TMP/fk3.out" 2>&1; echo "$? $GA_AMN_SYNC_ENGINE" >"$TMP/fk3.rc"
    amnezia_syncconf awg2 >"$TMP/fk2.out" 2>&1; echo "$? $GA_AMN_SYNC_ENGINE" >"$TMP/fk2.rc"
)
check "awg3 (amneziawg): rc=0, движок kernel, awg syncconf получил конфиг" \
    '[ "$(cat "$TMP/fk3.rc")" = "0 kernel" ] && [ -s "$FK/amnezia-awg3.sync" ]' "$(cat "$TMP/fk3.rc" "$TMP/fk3.out" 2>/dev/null | head -5)"
check "… в нём ключ, порт и все пиры — и ни HeaderProtectionKey, ни Jc, ни S/H" \
    'grep -q "^PrivateKey = " "$FK/amnezia-awg3.sync" && grep -q "^ListenPort = 40001$" "$FK/amnezia-awg3.sync" && [ "$(grep -c "^\[Peer\]" "$FK/amnezia-awg3.sync")" = "$(grep -c "^\[Peer\]" "$FK/amnezia-awg3.conf")" ] && ! grep -Eq "^(HeaderProtectionKey|Jc|Jmin|Jmax|S[1-4]|H[1-4]|RandomTrailers|DisableCookies|RekeyAfterTime) =" "$FK/amnezia-awg3.sync"' \
    "$(head -8 "$FK/amnezia-awg3.sync" 2>/dev/null | sed 's/= .*/= …/')"
check "awg2 (tun): rc=0, движок program, awg syncconf получил весь конфиг, как прежде" \
    '[ "$(cat "$TMP/fk2.rc")" = "0 program" ] && cmp -s "$FK/amnezia-awg2.sync" "$FK/amnezia-awg2.conf"' "$(cat "$TMP/fk2.rc" "$TMP/fk2.out" 2>/dev/null | head -5)"
check "к контейнеру — один вопрос о движке на syncconf (sh -c) и одно применение (bash -c)" \
    '[ "$(grep -c "exec -i amnezia-awg3 sh -c" "$FK/calls")" = 1 ] && [ "$(grep -c "exec -i amnezia-awg3 bash -c" "$FK/calls")" = 1 ]' "$(cat "$FK/calls")"

DONE=1
printf '\nИТОГ: %d пройдено, %d провалено\n' "$pass" "$fail"
[ "$fail" = 0 ]
