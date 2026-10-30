#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — параметры профилей 1.0.13 по живым проверкам 08.10 (.claude/work/progress/PARAMS-AUDIT.md, учёт
# PARAM-03…11): пределы того, что пишем мы, откат неудачного применения, честный ответ «кому нужен новый конфиг».
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_params_dryrun.sh
# Нужны: bash ≥ 5, jq, python3. Ничего вне $TMP не пишем, к Docker и серверам не ходим (GOAMNEZIA_DRY_RUN=1).
#
# Что проверяем (вживую 08.10, стенд и клиент, оба движка):
#   1  PARAM-04, PARAM-07: новые 3.1 — ContentPaddingAddition 10-100 и PersistentKeepalive 25 (сервер и ключ людей);
#      2.0 — как было; выданные 3.1 (25-35, без добавки) никто не трогает;
#   2  PARAM-03: тайминги 3.1 и ContentPaddingAddition вне коридора — отказ словами с диапазоном (RekeyTimeout 0-7 —
#      поток рукопожатий, RekeyAfterTime 9999999999 — профиль не поднимается, ContentPaddingAddition 70000 — модуль
#      держит 4464); связка таймингов с подхваченным значением вне коридора; другие параметры такого профиля меняются;
#   3  PARAM-08: всплеск мусора Jc × Jmax больше 16 КБ — отказ (Jc 122 × Jmax 1280 — рукопожатие 6,7 с); уменьшать
#      у подхваченного можно и по шагу;
#   4  PARAM-09: Jc + число I больше 127 — отказ (клиент на программе не поднимается: «Protocol error»);
#   5  PARAM-05 (#226): хвосты 3.1 + разные S + широкие H — отказ словами (вживую 6,5 % потерь);
#   6  PARAM-10: применение не удалось (подставной контейнер не поднимается) — прежнее значение в config.json и awg0.conf,
#      профиль снова поднят, отказ с именем параметра и причиной словами; не поднялся и с прежним — так и сказано;
#   7  PARAM-11: после смены параметра — кому нужен новый конфиг: подписки обновятся сами, ключи vpn:// и .conf —
#      выдать заново (по людям и словами); то же значение — никому ничего; людей определить не удалось — общая фраза;
#   8  проверка пределов сама упала (awgkeys.py) — отказ params_failed словами, ничего не записано; набор и прежнее
#      значение — проверке через stdin, не argv.
# Подхват с такими значениями — предупреждение, не отказ: tests/test_import_dryrun.sh (раздел 1.0.13).
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-params.XXXXXX") || exit 2
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
export GOAMNEZIA_SYS_MODULE_DIR="$TMP/sysmodule"
# Контейнеры профилей «есть и работают» (каталог-фикстура), движок — по фикстуре .iplink (нет — программа).
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
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 1500; return 0; }
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
run() {
    GA_ERR_CODE=""; GA_ERR_MSG=""; GA_LAST_ERROR_CODE=""
    "$@" >"$OUT" 2>"$ERR"; rc=$?
    out=$(<"$OUT")
    return 0
}
errs() { tail -n 6 "$ERR" 2>/dev/null; }
jget() { jq -r "$1" <<<"$out" 2>/dev/null; }
# act <действие> [--k=v …] — мост, как его зовут бот, панель и MCP: последняя строка stdout — в out.
act() {
    "$BASH5" "$INSTALL" --action="$1" --json "${@:2}" >"$OUT" 2>"$ERR"; rc=$?
    out=$(tail -n 1 "$OUT")
    return 0
}
param() { jq -r --arg p "$1" --arg k "$2" '.profiles[$p].params[$k] // ""' "$CONFIG_FILE"; }
conf_has() { grep -qxF -- "$2" "$STATE_DIR/amnezia-$1/awg0.conf"; }
setparams() {   # setparams <профиль> <JSON-объект параметров> — правка config.json «как у выданного или подхваченного»
    jq --arg p "$1" --argjson v "$2" '.profiles[$p].params += $v' "$CONFIG_FILE" >"$TMP/cfg.json" && mv "$TMP/cfg.json" "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
}
# refused <ключ> <значение> <слова…> — отказ params_invalid словами, значение прежнее (ядро: SSH и мост — один путь).
refused() {
    local p="$1" k="$2" v="$3" before w
    shift 3
    before=$(param "$p" "$k")
    run amnezia_profile_set_param "$p" "$k" "$v"
    if [ "$rc" != 2 ] || [ "$GA_ERR_CODE" != params_invalid ] || [ "$(param "$p" "$k")" != "$before" ]; then
        bad "$p $k = $v — отказ params_invalid, значение прежнее ($before)" "rc=$rc code=$GA_ERR_CODE now=$(param "$p" "$k") $GA_ERR_MSG"
        return 0
    fi
    for w in "$@"; do
        grep -qF -- "$w" <<<"$GA_ERR_MSG" || { bad "$p $k = $v — в отказе «${w}»" "$GA_ERR_MSG"; return 0; }
    done
    ok "$p $k = $v — отказ словами: «${GA_ERR_MSG:0:150}…»"
}
accepted() {   # accepted <профиль> <ключ> <значение> — принят, записан
    _ap="$1"; _ak="$2"; _av="$3"
    run amnezia_profile_set_param "$_ap" "$_ak" "$_av"
    check "$_ap $_ak = $_av — принят" '[ "$rc" = 0 ] && [ "$(param "$_ap" "$_ak")" = "$_av" ]' \
        "rc=$rc code=$GA_ERR_CODE $GA_ERR_MSG now=$(param "$_ap" "$_ak")"
}

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia awg_params users export edgeips import website pages subs; do
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
run amnezia_profile_install awg2 40000 default
check "awg2 (2.0) поставлен: rc=0" '[ "$rc" = 0 ]' "$(errs)"
run amnezia_profile_install awg3 40001
check "awg3 (3.1) поставлен: rc=0" '[ "$rc" = 0 ] && [ "$(ga_profile_line awg3)" = 3.1 ]' "$(errs)"

# ── 1. Новые 3.1: ContentPaddingAddition 10-100 и PersistentKeepalive 25 ───────
section "1: PARAM-04, PARAM-07 — новые 3.1: ContentPaddingAddition 10-100, PersistentKeepalive 25; выданные не трогаем"
check "awg3: ContentPaddingAddition = 10-100 и PersistentKeepalive = 25 в config.json" \
    '[ "$(param awg3 ContentPaddingAddition)" = 10-100 ] && [ "$(param awg3 PersistentKeepalive)" = 25 ]' \
    "$(jq -c '.profiles.awg3.params | {ContentPaddingAddition, PersistentKeepalive}' "$CONFIG_FILE")"
check "… и в awg0.conf сервера: ContentPaddingAddition = 10-100 (сервер тоже добивает свои пакеты так)" \
    'conf_has awg3 "ContentPaddingAddition = 10-100"' "$(grep -n "Padding\|Keepalive" "$STATE_DIR/amnezia-awg3/awg0.conf")"
check "… тайминги — как у приложения Amnezia (100-120, 3-7, 150-180, 5-15, 15-20), хвосты и cookie — on" \
    '[ "$(jq -r ".profiles.awg3.params | [.RekeyAfterTime, .RekeyTimeout, .RejectAfterTime, .KeepaliveTimeout, .MaxHandshakeAttempts, .RandomTrailers, .DisableCookies] | join(\" \")" "$CONFIG_FILE")" = "100-120 3-7 150-180 5-15 15-20 on on" ]' \
    "$(jq -c .profiles.awg3.params "$CONFIG_FILE")"
check "awg2 (2.0): добавки нет, keepalive 25 — как было" \
    '[ "$(param awg2 ContentPaddingAddition)" = "" ] && [ "$(param awg2 PersistentKeepalive)" = 25 ]'
run users_add anna "" awg2,awg3
act user-key --name=anna --profile=awg3 --format=conf
check "ключ человека 3.1 (.conf): ContentPaddingAddition = 10-100 и PersistentKeepalive = 25" \
    'grep -qx "ContentPaddingAddition = 10-100" <<<"$(jget .conf)" && grep -qx "PersistentKeepalive = 25" <<<"$(jget .conf)"' \
    "$(jget .conf | grep -n "Padding\|Keepalive")"
# Выданный до 1.0.13 профиль 3.1: keepalive 25-35, добавки нет — так и живёт; смена другого параметра его не мигрирует.
setparams awg3 '{"ContentPaddingAddition": "", "PersistentKeepalive": "25-35"}'
accepted awg3 Jc 5
check "… выданный 3.1 (25-35, без добавки): после смены Jc — по-прежнему 25-35 и без добавки" \
    '[ "$(param awg3 PersistentKeepalive)" = 25-35 ] && [ "$(param awg3 ContentPaddingAddition)" = "" ] && ! grep -q "^ContentPaddingAddition" "$STATE_DIR/amnezia-awg3/awg0.conf"'
setparams awg3 '{"ContentPaddingAddition": "10-100", "PersistentKeepalive": "25"}'
run amnezia_write_server_conf awg3

# ── 2. Тайминги 3.1 и ContentPaddingAddition: коридор ───────────────────────
section "2: PARAM-03 — тайминги 3.1 и ContentPaddingAddition вне коридора — отказ словами с диапазоном"
refused awg3 RekeyTimeout 0-7 "RekeyTimeout = 0-7 не принят: допустимо от 3 до 10 (у новых профилей — 3-7)" "поток рукопожатий" "16,5 раза"
refused awg3 RekeyAfterTime 9999999999 "RekeyAfterTime = 9999999999 не принят: допустимо от 60 до 120 (у новых профилей — 100-120)" "профиль не поднимется"
refused awg3 ContentPaddingAddition 70000 "ContentPaddingAddition = 70000 не принят: допустимо от 0 до 1400 (у новых профилей — 10-100)" "70000 стало 4464"
refused awg3 RejectAfterTime 150-190 "допустимо от 150 до 180" "не дольше 180 с"
refused awg3 RejectAfterTime 100 "допустимо от 150 до 180"
refused awg3 KeepaliveTimeout 0-100 "допустимо от 5 до 15"
refused awg3 MaxHandshakeAttempts 0-1 "допустимо от 10 до 30"
check "отказы ничего не записали: в awg0.conf нет ни 9999999999, ни 70000; перезапусков не было" \
    '! grep -q "9999999999\|70000\|= 0-7" "$STATE_DIR/amnezia-awg3/awg0.conf"' "$(grep -n "Rekey\|Padding" "$STATE_DIR/amnezia-awg3/awg0.conf")"
accepted awg3 RekeyAfterTime 90-110
accepted awg3 RekeyTimeout 5
accepted awg3 ContentPaddingAddition 0-200
accepted awg3 MaxHandshakeAttempts 10-30
run amnezia_profile_set_param awg3 RekeyTimeout ""
check "убрать тайминг (пусто) — можно: у реализаций будет значение WireGuard (5), оно в коридоре" '[ "$rc" = 0 ] && [ "$(param awg3 RekeyTimeout)" = "" ]' "rc=$rc $GA_ERR_MSG"
act profile-set-param --profile=awg3 --key=RekeyTimeout --value=0-7
check "мост (бот, панель, MCP — тот же путь): params_invalid, слова с диапазоном в message" \
    '[ "$(jget .status)" = error ] && [ "$(jget .code)" = params_invalid ] && grep -qF "RekeyTimeout = 0-7 не принят: допустимо от 3 до 10" <<<"$(jget .message)"' "${out:0:400}"
GOAMNEZIA_CALL_LANG=en "$BASH5" "$INSTALL" --action=profile-set-param --json --profile=awg3 --key=ContentPaddingAddition --value=70000 \
    >"$OUT" 2>"$ERR"; out=$(tail -n 1 "$OUT")
check "… по-английски — тоже словами: «allowed from 0 to 1400»" \
    'grep -qF "ContentPaddingAddition = 70000 rejected: allowed from 0 to 1400 (new profiles get 10-100)" <<<"$(jget .message)"' "${out:0:400}"
accepted awg3 RekeyTimeout 3-7
accepted awg3 RekeyAfterTime 100-120
accepted awg3 ContentPaddingAddition 10-100
accepted awg3 MaxHandshakeAttempts 15-20
# Связка: подхваченный RekeyAfterTime вне коридора (130-140). Новый ключ по приёму запрашивается через RejectAfterTime −
# KeepaliveTimeout − RekeyTimeout = 150 − 5 − 3 = 142 с — пока это больше 140, ключ меняют вовремя.
setparams awg3 '{"RekeyAfterTime": "130-140"}'
refused awg3 KeepaliveTimeout 15 "KeepaliveTimeout = 15 не принят: новый ключ сессии запросится только через 132 с" "RekeyAfterTime у профиля — до 140 с" "Сначала верните в коридор: RekeyAfterTime = 130-140 (60–120)"
accepted awg3 Jc 6
check "… подхваченный тайминг вне коридора не мешает менять другое (Jc), сам он прежний" '[ "$(param awg3 RekeyAfterTime)" = 130-140 ]'
accepted awg3 RekeyAfterTime 100-120
accepted awg3 KeepaliveTimeout 15
accepted awg3 KeepaliveTimeout 5-15

# ── 3. Всплеск мусора ────────────────────────────────────────────────────────
section "3: PARAM-08 — Jc × Jmax больше 16 КБ — отказ словами; генератор — не больше 300 байт"
check "генератор (awg2, awg3): Jc 4–6 × Jmax 50 — не больше 300 байт" \
    '[ $(( $(param awg2 Jc) * $(param awg2 Jmax) )) -le 300 ] && [ $(( $(param awg3 Jc) * $(param awg3 Jmax) )) -le 300 ]'
accepted awg2 Jc 4
accepted awg2 Jmax 1280
accepted awg2 Jmin 1200
refused awg2 Jc 122 "Jc = 122 не принят: перед каждой попыткой рукопожатия уйдёт Jc × Jmax = 122 × 1280 = 156160 байт мусора — больше 16384 (16 КБ)" "6,7 с вместо 0,09"
accepted awg2 Jc 12
refused awg2 Jc 13 "Jc × Jmax = 13 × 1280 = 16640 байт"
# Подхваченный с большим всплеском (как живой стенд L-08): уменьшать можно и по шагу, увеличивать — нет.
setparams awg2 '{"Jc": "122"}'
accepted awg2 Jmax 1250
refused awg2 Jmax 1280 "Jmax = 1280 не принят" "122 × 1280 = 156160"
accepted awg2 Jc 4
accepted awg2 Jmin 10
accepted awg2 Jmax 50

# ── 4. Jc + число I ───────────────────────────────────────────────────────────
section "4: PARAM-09 — Jc + число заданных I1–I5 больше 127 — отказ (клиент на программе не поднимется)"
check "у awg2 задан I1 (пресет default)" '[ -n "$(param awg2 I1)" ]'
refused awg2 Jc 127 "Jc = 127 не принят: Jc + число заданных I1–I5 = 127 + 1 = 128" "поднимаются, только пока это не больше 127" "Protocol error"
accepted awg2 Jc 126
refused awg2 I2 "<b 0x04>" "I2 = <b 0x04> не принят: Jc + число заданных I1–I5 = 126 + 2 = 128"
run amnezia_profile_set_param awg2 I1 ""
check "убрать I1 при Jc 126 — можно (сумма меньше)" '[ "$rc" = 0 ] && [ "$(param awg2 I1)" = "" ]' "rc=$rc $GA_ERR_MSG"
accepted awg2 Jc 127
refused awg2 Jc 128 "Jc + число заданных I1–I5 = 128 + 0 = 128"
accepted awg2 Jc 4
I1_DEF=$(GA_LIB="$ROOT/lib" python3 -c 'import os, sys; sys.path.insert(0, os.environ["GA_LIB"]); import awgkeys; print(awgkeys.default_i1())')
run amnezia_profile_set_param awg2 I1 "$I1_DEF"
check "I1 по умолчанию — обратно" '[ "$rc" = 0 ] && [ -n "$I1_DEF" ] && [ "$(param awg2 I1)" = "$I1_DEF" ]' "rc=$rc $GA_ERR_MSG $(param awg2 I1)"

# ── 5. Хвосты 3.1 + разные S + широкие H ─────────────────────────────────────
section "5: PARAM-05 (#226) — хвосты 3.1, разные S и широкие H — отказ словами; по шагу — можно"
# Порядок, как у живой проверки L-05: сначала H (S все равны — потерь нет), потом S1 — и тут отказ.
accepted awg3 H4 268450001-268460000
accepted awg3 H3 268440001-268450000
accepted awg3 H2 268435461-268440000
accepted awg3 H1 5-268435460
refused awg3 S1 40 "S1 = 40 не принят: при хвостах (RandomTrailers) разные S (S1 при S4 = 12) и широкие H (H1 — вместе 268435456 значений)" "примерно 1 из 16 пакетов данных" "6,5 % пакетов"
act profile-set-param --profile=awg3 --key=S1 --value=41
check "мост: S1 = 41 — тот же отказ словами («1 из 16»), S1 прежний" \
    '[ "$(jget .code)" = params_invalid ] && grep -qF "разные S (S1 при S4 = 12)" <<<"$(jget .message)" && grep -qF "1 из 16" <<<"$(jget .message)" && [ "$(param awg3 S1)" = 12 ]' "${out:0:400}"
accepted awg3 S2 30
check "… S2 = 30 при узком H2 (4540 значений) — потерь нет, принят" '[ "$(param awg3 S2)" = 30 ]'
accepted awg3 S2 12
accepted awg3 RandomTrailers off
accepted awg3 S1 40
refused awg3 RandomTrailers on "RandomTrailers = on не принят" "S1 при S4 = 12"
accepted awg3 S1 12
accepted awg3 RandomTrailers on
accepted awg3 H1 1
accepted awg3 H2 2
accepted awg3 H3 3
accepted awg3 H4 4
accepted awg3 S1 13
check "… узкие одиночные H и разные S — без потерь, принят (S1 = 13)" '[ "$(param awg3 S1)" = 13 ]'
accepted awg3 S1 12

# ── 6. Откат неудачного применения ───────────────────────────────────────────
section "6: PARAM-10 — не применилось (подставной контейнер не поднимается) — прежнее назад, отказ словами"
# Подставной контейнер: с «ядовитой» строкой в awg0.conf интерфейс не поднимается — как вживую с RekeyAfterTime
# 9999999999 (`awg setconf` отказывал, container_not_up). P10_DOWN=1 — не поднимается вовсе.
eval "$(declare -f _ga_amn_wait_up_iface | sed '1s/^_ga_amn_wait_up_iface/_p10_real_wait_up/')"
_ga_amn_wait_up_iface() {
    if [ "${P10_DOWN:-0}" = 1 ] || { [ -n "${P10_POISON:-}" ] && grep -qxF -- "$P10_POISON" "$(ga_amn_state_conf "$1")" 2>/dev/null; }; then
        ga_err container_not_up "$(tf amnezia_err_not_up "$(ga_profile_field "$1" container)" "$(ga_amn_iface "$1")")"
        return 1
    fi
    _p10_real_wait_up "$@"
}
eval "$(declare -f docker_exec_bash | sed '1s/^docker_exec_bash/_p10_real_exec_bash/')"
docker_exec_bash() {   # syncconf программы не принимает «ядовитую» строку (как awg setconf — десятизначное число)
    if [ -n "${P10_SYNC_POISON:-}" ] && grep -qxF -- "$P10_SYNC_POISON" "$(ga_amn_state_conf awg3)" 2>/dev/null; then return 1; fi
    _p10_real_exec_bash "$@"
}
cp "$STATE_DIR/amnezia-awg3/awg0.conf" "$TMP/awg3.before"
CFG0=$(jq -cS '.profiles.awg3' "$CONFIG_FILE")
NOT_UP="Контейнер amnezia-awg3 не поднял интерфейс awg0 за 20 с"
# а) Серверный параметр на программе: рестарт → интерфейс не поднялся.
P10_POISON="H1 = 77" run amnezia_profile_set_param awg3 H1 77
check "серверный H1 = 77, контейнер не поднялся: отказ param_rolled_back с именем, причиной и прежним значением" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = param_rolled_back ] && [ "$GA_ERR_MSG" = "H1 = 77 не применился: $NOT_UP. Вернул прежнее значение (1) в настройки и конфиг сервера — профиль как до правки, ключи пользователей прежние." ]' \
    "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
check "… config.json — запись профиля прежняя байт в байт, awg0.conf — прежний (H1 = 1)" \
    '[ "$(jq -cS ".profiles.awg3" "$CONFIG_FILE")" = "$CFG0" ] && cmp -s "$TMP/awg3.before" "$STATE_DIR/amnezia-awg3/awg0.conf"' \
    "$(diff <(echo "$CFG0") <(jq -cS '.profiles.awg3' "$CONFIG_FILE") | head -4)"
check "… прежний конфиг — и в контейнер, и профиль поднят снова (второй перезапуск)" \
    '[ "$(grep -c "DRY: docker restart amnezia-awg3" "$ERR")" = 2 ] && grep -qF "docker cp <stdin> amnezia-awg3:/opt/amnezia/awg/awg0.conf" "$ERR"' "$(grep "docker restart\|docker cp" "$ERR")"
check "… журнал действий: profile-set-param «awg3 H1 failed step=up code=container_not_up rolled_back=yes»" \
    'grep -q "\"action\":\"profile-set-param\",\"details\":\"awg3 H1 failed step=up code=container_not_up rolled_back=yes\"" "$GOAMNEZIA_AUDIT_LOG"' \
    "$(grep profile-set-param "$GOAMNEZIA_AUDIT_LOG" | tail -2)"
# б) Клиентский параметр на модуле ядра: применяется рестартом (KMOD-01) → не поднялся.
printf '4: awg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280\n    amneziawg addrgenmode none\nDEVTYPE=amneziawg\n' >"$CFS/amnezia-awg3/.iplink"
P10_POISON="ContentPaddingAddition = 20-200" run amnezia_profile_set_param awg3 ContentPaddingAddition 20-200
check "клиентский ContentPaddingAddition на модуле ядра (рестартом) не поднялся — откат, прежнее 10-100" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = param_rolled_back ] && grep -qF "ContentPaddingAddition = 20-200 не применился: $NOT_UP. Вернул прежнее значение (10-100)" <<<"$GA_ERR_MSG" && [ "$(param awg3 ContentPaddingAddition)" = 10-100 ] && cmp -s "$TMP/awg3.before" "$STATE_DIR/amnezia-awg3/awg0.conf"' \
    "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
rm -f "$CFS/amnezia-awg3/.iplink"
# в) Клиентский параметр на программе: syncconf не принял (программа применяет строки по одной — перезапуск с прежним).
P10_SYNC_POISON="MaxHandshakeAttempts = 20-30" run amnezia_profile_set_param awg3 MaxHandshakeAttempts 20-30
check "клиентский на программе: syncconf не прошёл — откат, причина словами («awg syncconf … не удался»), профиль перезапущен с прежним" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = param_rolled_back ] && grep -qF "MaxHandshakeAttempts = 20-30 не применился: awg syncconf в профиле awg3 не удался. Вернул прежнее значение (15-20)" <<<"$GA_ERR_MSG" && [ "$(param awg3 MaxHandshakeAttempts)" = 15-20 ] && grep -q "DRY: docker restart amnezia-awg3" "$ERR"' \
    "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
# г) Не поднялся и с прежним значением — так и сказать: профиль лежит, как поднять.
P10_DOWN=1 run amnezia_profile_set_param awg3 H2 78
check "не поднялся и с прежним: param_apply_failed — прежнее вернул, профиль лежит, «Поднять контейнер заново»" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = param_apply_failed ] && grep -qF "H2 = 78 не применился: $NOT_UP. Прежнее значение (2) вернул в настройки и конфиг сервера, но профиль не поднялся — поднимите контейнер amnezia-awg3 заново" <<<"$GA_ERR_MSG" && [ "$(param awg3 H2)" = 2 ] && cmp -s "$TMP/awg3.before" "$STATE_DIR/amnezia-awg3/awg0.conf"' \
    "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
# д) Мост (так его видят бот, панель и MCP): Docker поднял контейнер без опубликованного порта (фикстура .unpublished) —
# откат, и подъём, как у «Перезапустить»: пересоздание из копии состояния, в ней уже прежнее значение.
: >"$CFS/amnezia-awg3/.unpublished"
act profile-set-param --profile=awg3 --key=H3 --value=79
check "мост: Docker не опубликовал порт — откат, контейнер пересоздан из копии, status=error, code=param_rolled_back словами; config.json прежний" \
    '[ "$(jget .status)" = error ] && [ "$(jget .code)" = param_rolled_back ] && grep -qF "H3 = 79 не применился: Контейнер amnezia-awg3 работает, но Docker не опубликовал его порт UDP/40001" <<<"$(jget .message)" && grep -qF "Вернул прежнее значение (3)" <<<"$(jget .message)" && [ "$(param awg3 H3)" = 3 ] && grep -qF "пересоздаю его из копии состояния" "$ERR"' \
    "rc=$rc ${out:0:500} $(grep -n "пересозда\|DRY: docker run" "$ERR" | head -3)"
rm -f "$CFS/amnezia-awg3/.unpublished"
mkdir -p "$CFS/amnezia-awg3"
unset -f _ga_amn_wait_up_iface docker_exec_bash
eval "$(declare -f _p10_real_wait_up | sed '1s/^_p10_real_wait_up/_ga_amn_wait_up_iface/')"
eval "$(declare -f _p10_real_exec_bash | sed '1s/^_p10_real_exec_bash/docker_exec_bash/')"
accepted awg3 H1 1

# ── 7. Кому нужен новый конфиг ───────────────────────────────────────────────
section "7: PARAM-11 — после смены параметра: кому нужен новый конфиг (подписки — сами, ключи — выдать заново)"
# На awg3: anna — выданный ключ; boris — страница подключения; vera — подписка, которую забирали; gleb — подхвачен у
# приложения Amnezia (приватного ключа на сервере нет). awg2 — anna тоже; сначала там никого, кроме неё, нет.
for _u in boris vera gleb; do run users_add "$_u" "" awg3; done
jq '(.users[] | select(.name == "gleb") | .peers.awg3) |= (.privkey = "" | .imported = true) | (.users[] | select(.name == "gleb")) |= (.imported = true)' \
    "$USERS_FILE" >"$TMP/u" && mv "$TMP/u" "$USERS_FILE" && chmod 600 "$USERS_FILE"
mkdir -p "$(ga_pages_dir)/p-1a2b3c" "$(ga_subs_dir)/vera"
printf '{"id":"p-1a2b3c","user":"boris","profiles":["awg3"],"expires_epoch":0,"forever":true}\n' >"$(ga_pages_dir)/p-1a2b3c/meta.json"
printf '%s\n' "$(printf 'x%.0s' $(seq 1 43))" >"$(ga_pages_dir)/p-1a2b3c/secret"
printf '{"user":"vera","created":"2026-10-01T10:00:00Z","generation":1}\n' >"$(ga_subs_dir)/vera/meta.json"
printf '%s\n' "$(printf 'y%.0s' $(seq 1 43))" >"$(ga_subs_dir)/vera/secret"
printf '{"fetches": 2, "last_fetch": "2026-10-07T10:00:00Z", "last_suffix": "all.yaml", "last_client": "flclash"}\n' >"$(ga_subs_dir)/vera/state.json"
act profile-set-param --profile=awg3 --key=Jc --value=5
check "клиентский Jc: reissue_required=false, но clients_update=true (новое значение — только с новым конфигом)" \
    '[ "$(jget .status)" = success ] && [ "$(jq -c .reissue_required <<<"$out")" = false ] && [ "$(jq -c .clients_update <<<"$out")" = true ]' "${out:0:400}"
check "… кто: подписка — vera, страница — boris, ключ — anna, приложение — gleb" \
    '[ "$(jget ".result.audience.subscription | join(\",\")")" = vera ] && [ "$(jget ".result.audience.page | join(\",\")")" = boris ] && [ "$(jget ".result.audience.key | join(\",\")")" = anna ] && [ "$(jget ".result.audience.app | join(\",\")")" = gleb ]' \
    "$(jget .result.audience)"
check "… шаги словами: подписка «придёт само (до 12 ч, у FlClash — до 24 ч)», ключ «прежний работает со старым значением … выдайте ключ vpn:// или файл .conf ещё раз»" \
    '[ "$(jget "[.result.steps[].kind] | join(\",\")")" = "subscription,page,key,app" ] && grep -qF "С подпиской (vera): новое значение придёт само (до 12 ч, у FlClash — до 24 ч) или по «Обновить» в приложении." <<<"$(jget ".result.steps[0].text")" && grep -qF "(anna): прежний работает со старым значением; новое — только в новом: выдайте ключ vpn:// или файл .conf ещё раз" <<<"$(jget ".result.steps[2].text")" && grep -qF "(gleb): прежний работает со старым значением" <<<"$(jget ".result.steps[3].text")"' \
    "$(jget .result.steps)"
check "… message (его показывает бот): итог и одна фраза «кому нужен новый конфиг»" \
    '[ "$(jget .message)" = "Профиль awg3: параметр Jc теперь 5. Выданные ключи работают со старым значением, новое — только с новым конфигом: подписки обновятся сами, выданные ключи vpn:// и .conf — выдать заново." ]' \
    "$(jget .message)"
act profile-set-param --profile=awg3 --key=S1 --value=13
check "серверный S1: reissue_required=true; шаги — перезапуск, подписка «обновится сама», страница, ключ «импортировать заново», приложение «больше не подключится»" \
    '[ "$(jq -c .reissue_required <<<"$out")" = true ] && [ "$(jget "[.result.steps[].kind] | join(\",\")")" = "restart,subscription,page,key,app" ] && grep -qF "С подпиской (vera): обновится сама (до 12 ч, у FlClash — до 24 ч) или по «Обновить» в приложении." <<<"$(jget ".result.steps[1].text")" && grep -qF "(anna): импортировать заново" <<<"$(jget ".result.steps[3].text")" && grep -qF "(gleb): прежний больше не подключится" <<<"$(jget ".result.steps[4].text")"' \
    "$(jget .result.steps)"
check "… message: «Старые конфиги профиля больше не подключатся: подписки обновятся сами, выданные ключи vpn:// и .conf — выдать заново.»" \
    'grep -qF "Старые конфиги профиля больше не подключатся: подписки обновятся сами, выданные ключи vpn:// и .conf — выдать заново." <<<"$(jget .message)"' "$(jget .message)"
act profile-set-param --profile=awg3 --key=S1 --value=13
check "то же значение ещё раз — никому ничего: clients_update=false, шагов нет, message без фразы" \
    '[ "$(jq -c .clients_update <<<"$out")" = false ] && [ "$(jget ".result.steps | length")" = 0 ] && [ "$(jget .message)" = "Профиль awg3: параметр S1 теперь 13." ]' "${out:0:400}"
run users_del_peer anna awg2
act profile-set-param --profile=awg2 --key=Jc --value=5
check "на профиле никого — «Пользователей на профиле нет — больше ничего делать не нужно.»" \
    '[ "$(jget ".result.steps[-1].kind")" = nobody ] && grep -qF "Пользователей на профиле нет — больше ничего делать не нужно." <<<"$(jget .message)"' "${out:0:400}"
run amnezia_profile_set_param awg3 Jmin 11
check "SSH (та же функция): итог одной строкой и фраза «кому нужен новый конфиг» — по разу" \
    '[ "$(grep -c "Профиль awg3: параметр Jmin = 11" "$ERR")" = 1 ] && [ "$(grep -c "Выданные ключи работают со старым значением" "$ERR")" = 1 ] && [ "$(jq -r ".steps | length" <<<"$out")" = 4 ]' \
    "$(errs)"

# Проверяльщик 15.10 (REVIEW-CORE-1013, находка 6): людей профиля определить не удалось — не «людей нет, ничего делать
# не нужно», а общая фраза для всех (шаг unknown).
_p11_aud=$(declare -f ga_amn_s4_audience)
ga_amn_s4_audience() { return 1; }
run amnezia_profile_set_param awg3 Jmin 12
eval "$_p11_aud"
# REVIEW2-CORE-1013 (R2-5): что делать — в summary (мост дописывает его в message); шаг unknown его не повторяет, иначе
# бот и панель показывали одну фразу дважды.
check "пользователей определить не удалось: шаг unknown «определить не удалось — это касается всех», что делать — один раз (в summary), а не «Людей на профиле нет»" \
    '[ "$rc" = 0 ] && [ "$(jq -r "[.steps[].kind] | join(\",\")" <<<"$out")" = unknown ] && grep -qF "определить не удалось" <<<"$(jq -r ".steps[0].text" <<<"$out")" && grep -qF "это касается всех" <<<"$(jq -r ".steps[0].text" <<<"$out")" && ! grep -qF "Выданные ключи работают со старым значением" <<<"$(jq -r ".steps[0].text" <<<"$out")" && [ "$(jq -r .summary <<<"$out")" = "$(t amnezia_param_summary_client)" ] && ! grep -qF "Людей на профиле нет" <<<"$out$(cat "$ERR")"' \
    "rc=$rc ${out:0:600}"

# ── 8. Проверка пределов сама упала ──────────────────────────────────────────
# Проверяльщик 15.10 (находка 4): awgkeys.py limits упал (исключение, кривой вход) — значение без проверки не пишем,
# отказ params_failed словами «не удалось проверить значение — <причина>»; (находка 5): набор параметров (в нём
# HeaderProtectionKey) и прежнее значение идут проверке через stdin, не argv.
section "8: проверка пределов упала — отказ словами, ничего не записано; значения — не в argv"
cat >"$TMP/awgkeys_broken.py" <<PYEOF
import os, runpy, sys
if len(sys.argv) > 1 and sys.argv[1] == "limits":
    if os.environ.get("BROKEN_MODE") == "badinput":
        sys.stderr.write("awgkeys: limits: params must be a JSON object\n")
        sys.exit(2)
    raise RuntimeError("limits exploded")
sys.argv[0] = "$ROOT/lib/awgkeys.py"
runpy.run_path("$ROOT/lib/awgkeys.py", run_name="__main__")
PYEOF
_jc_before=$(param awg3 Jc)
GOAMNEZIA_AWGKEYS="$TMP/awgkeys_broken.py" run amnezia_profile_set_param awg3 Jc 7
check "awgkeys.py limits упал исключением: rc=1, params_failed, «Jc = 7 не записан: не удалось проверить значение — RuntimeError: limits exploded», Jc прежний" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = params_failed ] && grep -qF "Jc = 7 не записан: не удалось проверить значение — RuntimeError: limits exploded" <<<"$GA_ERR_MSG" && [ "$(param awg3 Jc)" = "$_jc_before" ] && ! conf_has awg3 "Jc = 7"' \
    "rc=$rc code=$GA_ERR_CODE $GA_ERR_MSG Jc=$(param awg3 Jc)"
BROKEN_MODE=badinput GOAMNEZIA_AWGKEYS="$TMP/awgkeys_broken.py" run amnezia_profile_set_param awg3 Jc 7
check "… код 2 без строки беды (кривой вход): тоже отказ с причиной из awgkeys, не «бед нет»" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = params_failed ] && grep -qF "не удалось проверить значение — params must be a JSON object" <<<"$GA_ERR_MSG" && [ "$(param awg3 Jc)" = "$_jc_before" ]' \
    "rc=$rc code=$GA_ERR_CODE $GA_ERR_MSG"
GOAMNEZIA_AWGKEYS="$TMP/awgkeys_broken.py" act profile-set-param --profile=awg3 --key=Jc --value=7
check "… мост (бот, панель, MCP): status=error, code=params_failed, те же слова в message" \
    '[ "$(jget .status)" = error ] && [ "$(jget .code)" = params_failed ] && grep -qF "не удалось проверить значение" <<<"$(jget .message)" && [ "$(param awg3 Jc)" = "$_jc_before" ]' "${out:0:400}"
SPY="$TMP/spy"; mkdir -p "$SPY"; : >"$SPY/argv.log"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s/argv.log"\nexec "%s" "$@"\n' "$SPY" "$(command -v jq)" >"$SPY/jq"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s/argv.log"\nexec "%s" "$@"\n' "$SPY" "$(command -v python3)" >"$SPY/python3"
chmod +x "$SPY/jq" "$SPY/python3"
_lr=$(PATH="$SPY:$PATH" ga_amn_limits_reason 3.1 \
    "$(jq -c '. + {HeaderProtectionKey: "SeCrEtHpK0000000000000000000000000000000000="}' <<<"$(ga_config_get .profiles.awg3.params)")" \
    RekeyTimeout "OlDvAlUe-3-7"; echo "rc=$?")
check "… набор (HeaderProtectionKey) и прежнее значение — не в argv jq и python3 (stdin), проверка прошла" \
    '[ "$_lr" = "rc=0" ] && [ -s "$SPY/argv.log" ] && ! grep -qF "SeCrEtHpK" "$SPY/argv.log" && ! grep -qF "OlDvAlUe" "$SPY/argv.log"' \
    "$_lr $(cat "$SPY/argv.log")"
# REVIEW2-CORE-1013 (R2-6): stderr проверки (текст исключения может нести значения набора) — во временный файл
# ga_mktemp_secret с затиранием ga_wipe_file, а не обычным mktemp.
: >"$SPY/tmp.log"
_r6_mk=$(declare -f ga_mktemp_secret); _r6_wf=$(declare -f ga_wipe_file)
eval "_r6_orig_mk() $(declare -f ga_mktemp_secret | tail -n +2)"; eval "_r6_orig_wf() $(declare -f ga_wipe_file | tail -n +2)"
ga_mktemp_secret() { local f; f=$(_r6_orig_mk "$@") || return 1; printf 'mk %s %s\n' "$1" "$f" >>"$SPY/tmp.log"; printf '%s' "$f"; }
ga_wipe_file() { printf 'wipe %s\n' "$*" >>"$SPY/tmp.log"; _r6_orig_wf "$@"; }
_r6=$(GOAMNEZIA_AWGKEYS="$TMP/awgkeys_broken.py" ga_amn_limits_reason 3.1 "$(ga_config_get .profiles.awg3.params)" Jc "5"; echo "rc=$?")
eval "$_r6_mk"; eval "$_r6_wf"
_r6_f=$(awk '$1 == "mk" && $2 == "galim" {print $3}' "$SPY/tmp.log" | head -n 1)
check "R2-6: stderr проверки пределов — файл ga_mktemp_secret (galim) и затирание ga_wipe_file; причина дошла, файла после нет" \
    'grep -qF "rc=1" <<<"$_r6" && grep -qF "RuntimeError: limits exploded" <<<"$_r6" && [ -n "$_r6_f" ] && grep -qxF "wipe $_r6_f" "$SPY/tmp.log" && [ ! -e "$_r6_f" ]' \
    "$_r6 | $(cat "$SPY/tmp.log")"
# То же у проверки набора валидатором при записи параметра: stderr — ga_mktemp_secret (gaval) и ga_wipe_file — и при
# отказе, и при успехе.
cat >"$TMP/awgkeys_badvalidate.py" <<PYEOF
import runpy, sys
if len(sys.argv) > 1 and sys.argv[1] == "validate":
    sys.stderr.write("awgkeys: validate: SeCrEtVaL rejected\n")
    sys.exit(2)
sys.argv[0] = "$ROOT/lib/awgkeys.py"
runpy.run_path("$ROOT/lib/awgkeys.py", run_name="__main__")
PYEOF
: >"$SPY/tmp.log"
_jc_before=$(param awg3 Jc)
ga_mktemp_secret() { local f; f=$(_r6_orig_mk "$@") || return 1; printf 'mk %s %s\n' "$1" "$f" >>"$SPY/tmp.log"; printf '%s' "$f"; }
ga_wipe_file() { printf 'wipe %s\n' "$*" >>"$SPY/tmp.log"; _r6_orig_wf "$@"; }
GOAMNEZIA_AWGKEYS="$TMP/awgkeys_badvalidate.py" run amnezia_profile_set_param awg3 Jc 6
_v_bad=$(awk '$1 == "mk" && $2 == "gaval" {print $3}' "$SPY/tmp.log" | tail -n 1)
_v_bad_st="rc=$rc code=$GA_ERR_CODE Jc=$(param awg3 Jc) wiped=$(grep -cxF "wipe $_v_bad" "$SPY/tmp.log")"
: >"$SPY/tmp.log"
run amnezia_profile_set_param awg3 Jc 6
_v_ok=$(awk '$1 == "mk" && $2 == "gaval" {print $3}' "$SPY/tmp.log" | tail -n 1)
_v_ok_rc=$rc
eval "$_r6_mk"; eval "$_r6_wf"
check "stderr валидатора при записи параметра — ga_mktemp_secret (gaval) и затирание: отказ (params_invalid, Jc прежний) и успех; файлов после нет" \
    '[ -n "$_v_bad" ] && [ ! -e "$_v_bad" ] && [ "$_v_bad_st" = "rc=2 code=params_invalid Jc=$_jc_before wiped=1" ] && [ -n "$_v_ok" ] && [ ! -e "$_v_ok" ] && grep -qxF "wipe $_v_ok" "$SPY/tmp.log" && [ "$_v_ok_rc" = 0 ] && [ "$(param awg3 Jc)" = 6 ]' \
    "bad=$_v_bad $_v_bad_st ok=$_v_ok rc=$_v_ok_rc Jc=$(param awg3 Jc) before=$_jc_before $(cat "$SPY/tmp.log")"

# ── 9. KEENETIC-I1: шаг миграции — I1 прежнего умолчания → универсальный ─────────
# KeeneticOS 5.1 не принимает ключ, где I1 начинается не с <b>; прежнее умолчание — <r 2><b 0x…>. Шаг обновления
# (_migrate_i1_universal → ga_amn_i1_universal_all) меняет ровно его тем же путём, что profile-set-param для клиентского
# параметра: config.json, строка «# I1» в конфиге контейнера, syncconf без рестарта; свой I1 и I2–I5 не трогает, ключи
# не перевыпускает, повтор ничего не меняет. Docker не отвечает или в контейнере чужой пир — пропуск молча.
section "9: KEENETIC-I1 — миграция I1 прежнего умолчания: универсальный I1, свой не тронут, повтор ничего не меняет"
eval "$(sed -n '/^_migrate_i1_universal() {$/,/^}$/p' "$INSTALL")"
I1_LEG='<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>'
I1U_RE='^<b 0x[0-9a-f]{4}858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>$'
I1_OWN='<b 0x0102><r 8>'
setparams awg3 "$(jq -cn --arg v "$I1_LEG" '{I1: $v, I2: "<b 0x0a0b><r 4>"}')"
setparams awg2 "$(jq -cn --arg v "$I1_OWN" '{I1: $v}')"
# Конфиги сервера и «живые» конфиги контейнеров — как у выданных профилей прежних версий.
for _p in awg2 awg3; do
    run amnezia_write_server_conf "$_p"
    mkdir -p "$CFS/amnezia-$_p/opt/amnezia/awg"
    cp "$STATE_DIR/amnezia-$_p/awg0.conf" "$CFS/amnezia-$_p/opt/amnezia/awg/awg0.conf"
done
_i1_users=$(jq -cS . "$USERS_FILE"); _i1_srvkey=$(cat "$STATE_DIR/amnezia-awg3/wireguard_server_private_key.key")
_i1_aud() { grep -c '"action":"profile-i1-universal"' "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null || true; }
# Пропуск — не молча (KEENETIC-I1-R, R2): строка-предупреждение (профиль, причина, что делать) и запись в журнал.
_i1_skip() { jq -R -r --arg d "$1" 'fromjson? // empty | select(.action == "profile-i1-universal-skipped" and .details == $d) | .details' "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null | wc -l | tr -d ' '; }
I1_SKIP_HEAD="Профиль awg3: I1 по умолчанию не заменён на универсальный"
# Готовое значение — следующей строкой после строки о пропуске, целиком (KEENETIC-I1-Q, Q1): внутри <b 0x…> пробел, и
# перенос по словам на терминале разрывал значение на две строки — скопировать его целиком было нельзя.
I1V_RE='^    <b 0x[0-9a-f]{4}8580[0-9a-f]+>$'
_i1_skip_val() { grep -A 1 -F "I1 по умолчанию не заменён" "$ERR" | sed -n 2p; }
# Docker не отвечает — пропуск, обновление не падает; человеку — причина и готовое значение для ручной замены.
_i1_dcs=$(declare -f docker_container_state)
docker_container_state() { echo "$GA_DOCKER_STATE_UNREACHABLE"; }
run _migrate_i1_universal
eval "$_i1_dcs"
check "Docker не отвечает: rc 0, I1 прежний, ни записи, ни ошибки, profile-i1-universal в журнале нет" \
    '[ "$rc" = 0 ] && [ "$(param awg3 I1)" = "$I1_LEG" ] && ! grep -q "docker cp <stdin>" "$ERR" && [ -z "$GA_ERR_CODE$GA_LAST_ERROR_CODE" ] && [ "$(_i1_aud)" = 0 ]' \
    "rc=$rc I1=$(param awg3 I1) code=$GA_ERR_CODE/$GA_LAST_ERROR_CODE $(errs)"
check "… строка пропуска: профиль, «Docker не ответил», Keenetic, совет начинается с «когда Docker заработает», «Изменить один параметр», готовое <b 0x…> — следующей строкой целиком; журнал — profile-i1-universal-skipped «awg3 reason=docker_unreachable»" \
    '[ "$(grep -c "I1 по умолчанию не заменён" "$ERR")" = 1 ] && grep "$I1_SKIP_HEAD — Docker не ответил." "$ERR" | grep -F "роутер Keenetic (KeeneticOS 5.1) не примет" | grep -qF "Чтобы подходили и Keenetic (ключи 1.5 и 2.0), когда Docker заработает, замените I1 в «Изменить один параметр» карточки профиля на значение ниже:" && [[ "$(_i1_skip_val)" =~ $I1V_RE ]] && [ "$(_i1_skip "awg3 reason=docker_unreachable")" = 1 ] && ! grep -q "awg2" "$ERR"' \
    "$(errs) | $(tail -1 "$GOAMNEZIA_AUDIT_LOG")"
# На настоящем терминале (pty) шириной 80 и 120: текст переносится по окну, а значение — одной строкой, как в
# ответе, и ни одна строка не кончается обрывком «<b» (Q1; так видит человек при обновлении в SSH).
read -r -d '' _I1_PTY_PY <<'PY'
import fcntl, os, re, struct, subprocess, sys, termios
bash, root, cols, val = sys.argv[1:5]
src = ('source "$R/lib/common.sh"; source "$R/lib/i18n.sh"; load_language ru >/dev/null 2>&1; '
       'source "$R/lib/docker.sh"; source "$R/lib/amnezia.sh"; _ga_amn_i1u_skip awg3 amnezia-awg3 docker_unreachable "$V"')
m, s = os.openpty()
fcntl.ioctl(s, termios.TIOCSWINSZ, struct.pack("HHHH", 50, int(cols), 0, 0))
env = dict(os.environ, R=root, V=val, COLUMNS=cols)
p = subprocess.Popen([bash, "-c", src], stdin=s, stdout=s, stderr=s, env=env, close_fds=True)
os.close(s)
out = b""
while True:
    try:
        d = os.read(m, 65536)
    except OSError:
        break
    if not d:
        break
    out += d
p.wait()
os.close(m)
sys.stdout.write(re.sub(r"\x1b\[[0-9;]*m", "", out.decode("utf-8", "replace").replace("\r", "")))
PY
_I1_PV='<b 0x1234858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>'
for _cols in 80 120; do
    _i1_pty=$(GOAMNEZIA_AUDIT_LOG="$TMP/audit-pty.log" python3 -c "$_I1_PTY_PY" "$BASH5" "$ROOT" "$_cols" "$_I1_PV" 2>&1)
    check "терминал $_cols колонок: строка о пропуске перенесена по окну, «<b 0x…>» — отдельной строкой целиком, обрывка «<b» в конце строки нет" \
        '[ "$(grep -c "" <<<"$_i1_pty")" -ge 3 ] && grep -qxF "    $_I1_PV" <<<"$_i1_pty" && ! grep -qE "<b$" <<<"$_i1_pty"' \
        "$_i1_pty"
done
# Приложение Amnezia добавило человека в контейнер после подхвата — перезапись конфига его бы сняла: пропуск.
printf '\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.8.3.250/32\n' "$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")" \
    >>"$CFS/amnezia-awg3/opt/amnezia/awg/awg0.conf"
run _migrate_i1_universal
check "в контейнере пир, которого нет в users.json: пропуск — I1 прежний, конфиг не переписан, rc 0" \
    '[ "$rc" = 0 ] && [ "$(param awg3 I1)" = "$I1_LEG" ] && ! grep -q "docker cp <stdin>" "$ERR" && [ "$(_i1_aud)" = 0 ]' \
    "rc=$rc I1=$(param awg3 I1) $(errs)"
check "… строка пропуска: люди приложения, совет — сверка с контейнером, после неё I1 заменится сам; журнал — «awg3 reason=foreign_peers»" \
    'grep "$I1_SKIP_HEAD — в контейнере есть пользователи, которых приложение Amnezia добавило" "$ERR" | grep -F "закройте меню goAmnezia и откройте снова" | grep -F "«Протоколы → Импорт установки Amnezia → Найти контейнеры приложения → Сверить с приложением»" | grep -F "«Обслуживание → Импорт из Amnezia → Сверить … с контейнером»" | grep -qF "I1 заменится сам" && ! grep -qF "Импорт существующей установки" "$ERR" && [ "$(_i1_skip "awg3 reason=foreign_peers")" = 1 ]' \
    "$(errs) | $(tail -1 "$GOAMNEZIA_AUDIT_LOG")"
# Совет «закройте меню и откройте снова» — правда: при старте меню SSH сверка с контейнером (_import_reconcile_prompt)
# сама находит этого человека и предлагает подхватить (KEENETIC-I1-Q, Q2). Ответ «нет» — ничего не меняется.
_q2_ct="$CFS/amnezia-awg3/opt/amnezia/awg/clientsTable"
[ -f "$_q2_ct" ] && cp "$_q2_ct" "$TMP/q2-clientsTable" || printf '[]' >"$_q2_ct"
_q2_confirm=$(declare -f confirm)
eval "$(sed -n '/^_import_reconcile_interactive() {$/,/^}$/p' "$INSTALL")"
eval "$(sed -n '/^_import_reconcile_prompt() {$/,/^}$/p' "$INSTALL")"
_profile_installed() { ga_profile_installed "$1"; }
_profile_state() { echo running; }
_pr_name() { printf '%s' "$1"; }
confirm() { printf 'ASK:%s\n' "$1" >&2; return 1; }
run _import_reconcile_prompt
check "SSH, старт меню: сверка видит человека приложения в awg3 и спрашивает «Подхватить изменения…» (ответ «нет» — I1 и список прежние)" \
    '[ "$rc" = 0 ] && grep -qF "Профиль awg3: контейнер разошёлся со списком пользователей — новых ключей: 1, пропавших: 0" "$ERR" && grep -qxF "ASK:Подхватить изменения из контейнера профиля awg3?" "$ERR" && [ "$(param awg3 I1)" = "$I1_LEG" ] && ! grep -qF "10.8.3.250" "$USERS_FILE"' \
    "rc=$rc $(errs)"
unset -f _import_reconcile_interactive _import_reconcile_prompt _profile_installed _profile_state _pr_name
eval "$_q2_confirm"
# Тот же путь — в предупреждении о чужом ключе при записи конфига (amnezia_warn_foreign_peer).
run ga_amn_warn_foreign_peers awg3 amnezia-awg3 "$(cat "$CFS/amnezia-awg3/opt/amnezia/awg/awg0.conf")"
check "… предупреждение о чужом ключе: сверка с контейнером — тот же путь (SSH, панель, бот)" \
    '[ "$(grep -c "добавлен приложением" "$ERR")" = 1 ] && grep -F "Такие ключи подхватывают заранее сверкой с контейнером (import-reconcile): в SSH — закройте меню goAmnezia и откройте снова" "$ERR" | grep -F "Сверить с приложением" | grep -qF "Сверить … с контейнером"' \
    "rc=$rc $(errs)"
if [ -f "$TMP/q2-clientsTable" ]; then mv "$TMP/q2-clientsTable" "$_q2_ct"; else rm -f "$_q2_ct"; fi
cp "$STATE_DIR/amnezia-awg3/awg0.conf" "$CFS/amnezia-awg3/opt/amnezia/awg/awg0.conf"
run _migrate_i1_universal
_i1_new=$(param awg3 I1)
check "прежнее умолчание (awg3, 3.1) → универсальный <b 0x{ID}{DNS-ответ}> в config.json и строкой «# I1» в конфиге сервера" \
    '[ "$rc" = 0 ] && [[ "$_i1_new" =~ $I1U_RE ]] && conf_has awg3 "# I1 = $_i1_new" && ! grep -qF "$I1_LEG" "$STATE_DIR/amnezia-awg3/awg0.conf"' \
    "rc=$rc I1=$_i1_new $(grep "I1" "$STATE_DIR/amnezia-awg3/awg0.conf") $(errs)"
check "тем же путём, что клиентский параметр: конфиг в контейнер (docker cp), syncconf на лету, рестарта нет" \
    'grep -qF "docker cp <stdin> amnezia-awg3:/opt/amnezia/awg/awg0.conf" "$ERR" && grep -q "awg syncconf" "$ERR" && ! grep -q "docker restart\|docker stop" "$ERR"' \
    "$(grep DRY "$ERR" | grep -E "cp <stdin>|syncconf|restart|stop" | tail -4)"
check "свой I1 (awg2) и I2 не тронуты; ключи людей и сервера не перевыпущены (users.json и ключ сервера те же)" \
    '[ "$(param awg2 I1)" = "$I1_OWN" ] && conf_has awg2 "# I1 = $I1_OWN" && ! grep -q "amnezia-awg2" "$ERR" && [ "$(param awg3 I2)" = "<b 0x0a0b><r 4>" ] && [ "$(jq -cS . "$USERS_FILE")" = "$_i1_users" ] && [ "$(cat "$STATE_DIR/amnezia-awg3/wireguard_server_private_key.key")" = "$_i1_srvkey" ]' \
    "awg2 I1=$(param awg2 I1) I2=$(param awg3 I2) $(grep amnezia-awg2 "$ERR" | head -2)"
check "в журнал — profile-i1-universal «awg3 I1 legacy-default->universal» (без значения); человеку — строка миграции" \
    '[ "$(_i1_aud)" = 1 ] && grep -qF "\"details\":\"awg3 I1 legacy-default->universal\"" "$GOAMNEZIA_AUDIT_LOG" && grep -qF "Сигнатурный пакет I1 по умолчанию заменён на универсальный (awg3)" "$ERR"' \
    "aud=$(_i1_aud) $(tail -1 "$GOAMNEZIA_AUDIT_LOG") $(errs)"
# K3-2 (живой стенд 29.10, решение владельца B-4): Keenetic 5.1 берёт ключи 1.5 и 2.0, 3.1 — только dev-прошивка. Строка
# миграции перечисляет профили (здесь awg3 — 3.1) и не обещает, что «новые ключи» любого из них примет Keenetic.
check "K3-2: строка миграции — Keenetic принимает ключи профилей 1.5 и 2.0, 3.1 — только dev-прошивка (ru и en)" \
    'grep -F "заменён на универсальный (awg3)" "$ERR" | grep -F "ключи профилей 1.5 и 2.0 теперь принимают и роутеры Keenetic" | grep -qF "3.1 — только в dev-прошивке" && ! grep -qF "новые ключи принимают и роутеры Keenetic" "$ERR" && grep -F "I18N[mig_i1_universal]=" "$ROOT/lib/lang/en.d/menu.sh" | grep -F "accept 1.5 and 2.0 keys" | grep -qF "3.1 — dev firmware only"' \
    "$(grep -F "универсальный" "$ERR")"
run _migrate_i1_universal
check "повтор ничего не меняет: I1 тот же, ни записи, ни журнала, ни строки" \
    '[ "$rc" = 0 ] && [ "$(param awg3 I1)" = "$_i1_new" ] && ! grep -q "docker cp <stdin>" "$ERR" && [ "$(_i1_aud)" = 1 ] && [ ! -s "$ERR" ]' \
    "rc=$rc I1=$(param awg3 I1) aud=$(_i1_aud) $(errs)"
check "шаг — в обоих списках миграции (_ga_migrate_steps и _ga_migrate_reapply)" \
    '[ "$(sed -n "/^_ga_migrate_steps() {$/,/^}$/p" "$INSTALL" | grep -c "^    _migrate_i1_universal$")" = 1 ] && [ "$(sed -n "/^_ga_migrate_reapply() {$/,/^}$/p" "$INSTALL" | grep -c "^    _migrate_i1_universal$")" = 1 ]'

# ── 10. KEENETIC-I1-R (R1): I1–I5 без «#» на модуле ядра ─────────────────────────
# I-пакеты шлёт клиент: строка I1–I5 в [Interface] сервера — не маскировка интерфейса, даже без «#» (ручная правка или
# чужая сборка). Перевод I1 на универсальный и смена I-пакета на модуле ядра идут syncconf'ом через фильтр, без рестарта
# и без разрыва сессий; настоящая маскировка (Jc, S, H, поля 3.1) на модуле — по-прежнему рестартом (KMOD-01).
section "10: KEENETIC-I1-R (R1) — I1–I5 без «#» на модуле ядра: I-пакеты без рестарта, маскировка — рестартом"
LIVE3="$CFS/amnezia-awg3/opt/amnezia/awg/awg0.conf"
# i1_live <commented|uncommented> — в config.json awg3 I1 прежнего умолчания; живой конфиг контейнера — как его записал
# бы прежний код, строки I1–I5 с «#» или без. Пометку о маскировке от самой подготовки снимаем: она не часть проверки.
i1_live() {
    setparams awg3 "$(jq -cn --arg v "$I1_LEG" '{I1: $v}')"
    run amnezia_write_server_conf awg3
    if [ "$1" = uncommented ]; then
        sed 's/^# \(I[1-5]\) = /\1 = /' "$STATE_DIR/amnezia-awg3/awg0.conf" >"$LIVE3"
    else
        cp "$STATE_DIR/amnezia-awg3/awg0.conf" "$LIVE3"
    fi
    GA_AMN_MASK_DIRTY=""
}
# Пиры конфига: ключ, общий секрет, адреса — строками по алфавиту.
peers_of() { awk '/^\[Peer\]/ { p = 1 } p && /^(PublicKey|PresharedKey|AllowedIPs)/ { gsub(/[[:space:]]/, ""); print }' "$1" | LC_ALL=C sort; }
KSYNC3="awg syncconf awg0 <(awg-quick strip /opt/amnezia/awg/awg0.conf | awk "
_mk() { printf '[Interface]\nPrivateKey = k\nJc = %s\n%s\n\n[Peer]\nPublicKey = p\n' "$1" "$2"; }
check "разница только в I1–I5 (с «#» и без) — не маскировка интерфейса (ga_amn_mask_diff rc 1)" \
    '! ga_amn_mask_diff "$(_mk 4 "I1 = <r 2><b 0x01>
I2 = <b 0x02>
i5=<b 0x05>")" "$(_mk 4 "# I1 = <b 0x0301>")"'
_mk_ok=1
for _k in Jc Jmin Jmax S1 S4 H1 H4 HeaderProtectionKey RekeyAfterTime; do
    ga_amn_mask_diff "$(printf '[Interface]\n%s = 1\nI1 = <b 0x01>\n' "$_k")" "$(printf '[Interface]\n%s = 2\n# I1 = <b 0x01>\n' "$_k")" || _mk_ok="$_k"
done
check "настоящая маскировка (Jc, Jmin, Jmax, S1, S4, H1, H4, HeaderProtectionKey, RekeyAfterTime) — разница (rc 0)" '[ "$_mk_ok" = 1 ]' "не увидел: $_mk_ok"
printf '4: awg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280\n    amneziawg addrgenmode none\nDEVTYPE=amneziawg\n' >"$CFS/amnezia-awg3/.iplink"
i1_live uncommented
check "подготовка: в живом конфиге awg3 I1 и I2 без «#», пиры есть" \
    'grep -q "^I1 = <r 2>" "$LIVE3" && grep -q "^I2 = " "$LIVE3" && [ -n "$(peers_of "$LIVE3")" ]' "$(grep -n "I[1-5] = " "$LIVE3" | cut -c1-30)"
run _migrate_i1_universal
_i1_k=$(param awg3 I1)
check "миграция на модуле ядра, I1 без «#»: I1 универсальный, «# I1» в конфиге, docker cp, syncconf через фильтр, рестарта нет" \
    '[ "$rc" = 0 ] && [[ "$_i1_k" =~ $I1U_RE ]] && conf_has awg3 "# I1 = $_i1_k" && grep -qF "docker cp <stdin> amnezia-awg3:/opt/amnezia/awg/awg0.conf" "$ERR" && grep -qF "$KSYNC3" "$ERR" && ! grep -q "docker restart\|docker stop" "$ERR" && ! grep -qF "новая маскировка применяется перезапуском" "$ERR"' \
    "rc=$rc I1=$_i1_k $(grep DRY "$ERR" | grep -E "cp <stdin>|syncconf|restart" | tail -3) $(errs)"
check "… пиры в новом конфиге те же, что в живом (ключ, общий секрет, адреса); I2 не тронут" \
    '[ "$(peers_of "$LIVE3")" = "$(peers_of "$STATE_DIR/amnezia-awg3/awg0.conf")" ] && [ "$(param awg3 I2)" = "<b 0x0a0b><r 4>" ]' \
    "$(diff <(peers_of "$LIVE3") <(peers_of "$STATE_DIR/amnezia-awg3/awg0.conf") | head -4)"
i1_live commented
run _migrate_i1_universal
_i1_k=$(param awg3 I1)
check "миграция на модуле ядра, I1 с «#»: I1 универсальный, syncconf через фильтр, рестарта нет" \
    '[ "$rc" = 0 ] && [[ "$_i1_k" =~ $I1U_RE ]] && grep -qF "$KSYNC3" "$ERR" && ! grep -q "docker restart\|docker stop" "$ERR" && [ "$(peers_of "$LIVE3")" = "$(peers_of "$STATE_DIR/amnezia-awg3/awg0.conf")" ]' \
    "rc=$rc I1=$_i1_k $(grep DRY "$ERR" | grep -E "syncconf|restart" | tail -3)"
# Было до правки: та же причина у ручной смены I-пакета («Изменить один параметр» в SSH, панели и боте).
i1_live uncommented
run amnezia_profile_set_param awg3 I2 "<b 0x0c0d><r 4>"
check "«Изменить один параметр» I2 на модуле ядра, I2 в живом конфиге без «#»: syncconf через фильтр, рестарта нет, restarted=false" \
    '[ "$rc" = 0 ] && [ "$(param awg3 I2)" = "<b 0x0c0d><r 4>" ] && conf_has awg3 "# I2 = <b 0x0c0d><r 4>" && grep -qF "$KSYNC3" "$ERR" && ! grep -q "docker restart\|docker stop" "$ERR" && [ "$(jget .restarted)" = false ]' \
    "rc=$rc code=$GA_ERR_CODE $GA_ERR_MSG $(grep DRY "$ERR" | grep -E "syncconf|restart" | tail -3)"
_jc=$(param awg3 Jc); _jc2=7; [ "$_jc" = 7 ] && _jc2=6
run amnezia_profile_set_param awg3 Jc "$_jc2"
check "настоящая маскировка (Jc = $_jc2) на модуле ядра — по-прежнему рестартом, syncconf нет, restarted=true" \
    '[ "$rc" = 0 ] && grep -q "DRY: docker restart amnezia-awg3" "$ERR" && ! grep -q "awg syncconf" "$ERR" && grep -qF "новая маскировка применяется перезапуском" "$ERR" && [ "$(jget .restarted)" = true ]' \
    "rc=$rc code=$GA_ERR_CODE $GA_ERR_MSG $(grep DRY "$ERR" | grep -E "syncconf|restart" | tail -3)"
# KEENETIC-I1-Q (P3): Jc на остановленном контейнере — применять нечего (при старте он прочтёт конфиг целиком), и пометки
# о смене маскировки не остаётся: иначе следующая замена I1 в том же процессе на модуле ядра перезапустила бы контейнер.
# Ветку «контейнер не работает» в стенде открывает только ga_dry = «нет» для самой set_param (фикстура .state = exited).
_p3_dry=$(declare -f ga_dry)
ga_dry() { [ "${FUNCNAME[1]}" = _amnezia_profile_set_param_locked ] && return 1; [ "${GOAMNEZIA_DRY_RUN:-0}" = 1 ]; }
echo exited >"$CFS/amnezia-awg3/.state"
GA_AMN_MASK_DIRTY=""
# Новый Jc — не равный ни записанному, ни живому: стенд не переносит docker cp в фикстуру, живой конфиг — от i1_live.
_jc3=$(param awg3 Jc); _jc_live=$(sed -n "s/^Jc = //p" "$LIVE3" | head -n 1)
for _jc4 in 5 6 7 8; do [ "$_jc4" != "$_jc3" ] && [ "$_jc4" != "$_jc_live" ] && break; done
run amnezia_profile_set_param awg3 Jc "$_jc4"
_p3_dirty="$GA_AMN_MASK_DIRTY"
eval "$_p3_dry"
rm -f "$CFS/amnezia-awg3/.state"
check "P3: Jc = $_jc4 на остановленном awg3 (в живом конфиге другой — маскировка разошлась) — записан, ни рестарта, ни syncconf, пометки о маскировке не осталось" \
    '[ -n "$_jc_live" ] && [ "$_jc_live" != "$_jc4" ] && [ "$rc" = 0 ] && [ "$(param awg3 Jc)" = "$_jc4" ] && ! grep -q "docker restart\|awg syncconf" "$ERR" && [[ " $_p3_dirty " != *" amnezia-awg3 "* ]]' \
    "rc=$rc live Jc=$_jc_live code=$GA_ERR_CODE $GA_ERR_MSG dirty=[$_p3_dirty] $(grep DRY "$ERR" | grep -E "syncconf|restart" | tail -3)"
i1_live commented
GA_AMN_MASK_DIRTY="$_p3_dirty"
run _migrate_i1_universal
check "… затем в том же процессе замена I1 на модуле ядра (контейнер уже работает) — syncconf через фильтр, рестарта нет" \
    '[ "$rc" = 0 ] && [[ "$(param awg3 I1)" =~ $I1U_RE ]] && grep -qF "$KSYNC3" "$ERR" && ! grep -q "docker restart\|docker stop" "$ERR"' \
    "rc=$rc I1=$(param awg3 I1) $(grep DRY "$ERR" | grep -E "syncconf|restart" | tail -3)"
rm -f "$CFS/amnezia-awg3/.iplink"

# ── 11. KEENETIC-I1-R (R2): пропуск — словами и в журнал; сверка с контейнером доводит I1 сама ─────────────────────
# Профиль с I1 прежнего умолчания пропущен (контейнера нет, смена не прошла и откатилась, люди приложения в контейнере) —
# одна строка: профиль, причина, что делать; в журнал — profile-i1-universal-skipped без значений. Сообщения неудачной
# смены стираются, строка о пропуске — нет. После import_reconcile … apply люди приложения свои — I1 заменяется сам,
# без рестарта; не вышло — сверка всё равно прошла, остаётся строка-предупреждение.
section "11: KEENETIC-I1-R (R2) — пропуск словами и в журнал; после сверки с контейнером I1 заменяется сам"
_i1r_aud0=$(_i1_aud)
i1_live commented
echo missing >"$CFS/amnezia-awg3/.state"
run _migrate_i1_universal
rm -f "$CFS/amnezia-awg3/.state"
check "контейнера нет: rc 0, I1 прежний, строка — «контейнера amnezia-awg3 нет», совет начинается с «сначала поднимите контейнер» («Поднять контейнер заново»), готовое <b 0x…>; журнал «awg3 reason=container_missing»" \
    '[ "$rc" = 0 ] && [ "$(param awg3 I1)" = "$I1_LEG" ] && grep "$I1_SKIP_HEAD — контейнера amnezia-awg3 нет." "$ERR" | grep -qF "Чтобы подходили и Keenetic (ключи 1.5 и 2.0), сначала поднимите контейнер amnezia-awg3 заново из копии состояния («Протоколы → карточка профиля → Поднять контейнер заново»), затем замените I1 в «Изменить один параметр» карточки профиля на значение ниже:" && [[ "$(_i1_skip_val)" =~ $I1V_RE ]] &&[ "$(_i1_skip "awg3 reason=container_missing")" = 1 ] && [ -z "$GA_ERR_CODE$GA_LAST_ERROR_CODE" ]' \
    "rc=$rc I1=$(param awg3 I1) $(errs) | $(tail -1 "$GOAMNEZIA_AUDIT_LOG")"
# Смена не прошла: syncconf отказал, set_param вернул прежнее (param_rolled_back) или и профиль не поднялся (param_apply_failed).
eval "$(declare -f amnezia_syncconf | sed '1s/^amnezia_syncconf/_i1r_real_syncconf/')"
amnezia_syncconf() {
    if [ "${I1R_SYNC_FAIL:-0}" = 1 ]; then ga_err syncconf_failed "$(tf amnezia_err_syncconf "$1")"; return 1; fi
    _i1r_real_syncconf "$@"
}
eval "$(declare -f _ga_amn_param_wait_up | sed '1s/^_ga_amn_param_wait_up/_i1r_real_param_wait_up/')"
_ga_amn_param_wait_up() {
    if [ "${I1R_DOWN:-0}" = 1 ]; then ga_err container_not_up "stend: не поднялся"; return 1; fi
    _i1r_real_param_wait_up "$@"
}
i1_live commented
I1R_SYNC_FAIL=1 run _migrate_i1_universal
check "смена не прошла и откатилась: rc 0, I1 прежний в config.json и в «# I1», кода ошибки не осталось" \
    '[ "$rc" = 0 ] && [ "$(param awg3 I1)" = "$I1_LEG" ] && conf_has awg3 "# I1 = $I1_LEG" && [ -z "$GA_ERR_CODE$GA_ERR_MSG$GA_LAST_ERROR_CODE" ] && [ "$(_i1_aud)" = "$_i1r_aud0" ]' \
    "rc=$rc I1=$(param awg3 I1) code=$GA_ERR_CODE $(errs)"
check "… строка пропуска не стёрта вместе с сообщениями смены: «замена не применилась, прежний I1 вернулся, профиль работает как прежде»; сообщений смены нет" \
    'grep "$I1_SKIP_HEAD — замена не применилась, прежний I1 вернулся, профиль работает как прежде." "$ERR" | grep -qF "Чтобы подходили и Keenetic (ключи 1.5 и 2.0), замените I1 в «Изменить один параметр» карточки профиля на значение ниже:" && [[ "$(_i1_skip_val)" =~ $I1V_RE ]] && ! grep -qF "не применился:" "$ERR"' "$(errs)"
check "… журнал: profile-set-param «awg3 I1 failed step=sync … rolled_back=yes» и profile-i1-universal-skipped «awg3 reason=apply_failed code=param_rolled_back»" \
    'grep -qF "\"details\":\"awg3 I1 failed step=sync code=syncconf_failed rolled_back=yes\"" "$GOAMNEZIA_AUDIT_LOG" && [ "$(_i1_skip "awg3 reason=apply_failed code=param_rolled_back")" = 1 ] && ! grep -F "i1-universal-skipped" "$GOAMNEZIA_AUDIT_LOG" | grep -qF "<b 0x"' \
    "$(grep -E "profile-set-param|i1-universal" "$GOAMNEZIA_AUDIT_LOG" | tail -3)"
i1_live commented
I1R_SYNC_FAIL=1 I1R_DOWN=1 run _migrate_i1_universal
check "смена не прошла, и профиль после возврата не поднялся: строка говорит и это — сначала «Поднять контейнер заново»; журнал «reason=apply_failed_down code=param_apply_failed»" \
    '[ "$rc" = 0 ] && [ "$(param awg3 I1)" = "$I1_LEG" ] && grep "$I1_SKIP_HEAD — замена не применилась, прежний I1 вернулся, но профиль не поднялся." "$ERR" | grep -qF "Чтобы подходили и Keenetic (ключи 1.5 и 2.0), сначала поднимите контейнер amnezia-awg3 заново из копии состояния («Протоколы → карточка профиля → Поднять контейнер заново»), затем замените I1" && [[ "$(_i1_skip_val)" =~ $I1V_RE ]] && [ "$(_i1_skip "awg3 reason=apply_failed_down code=param_apply_failed")" = 1 ] && [ -z "$GA_ERR_CODE$GA_LAST_ERROR_CODE" ]' \
    "rc=$rc $(errs) | $(tail -1 "$GOAMNEZIA_AUDIT_LOG")"
# Люди приложения в контейнере: миграция пропускает; сверка с контейнером (import_reconcile … apply) делает их своими и
# сразу доводит I1 — тем же путём, без рестарта; ответ сверки — прежний JSON.
i1_live commented
_i1r_pub=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = 10.8.3.251/32\n' "$_i1r_pub" "$(ga_awgkeys genpsk)" >>"$LIVE3"
# Пользователь — как его добавляет приложение: с PresharedKey и записью в clientsTable. Без PSK и без clientsTable —
# в конце раздела (KEENETIC-I1-Q, P1 и P2).
jq -cn --arg k "$_i1r_pub" '[{clientId: $k, userData: {clientName: "Phone (app)"}}]' >"$CFS/amnezia-awg3/opt/amnezia/awg/clientsTable"
run _migrate_i1_universal
check "подготовка: в контейнере человек приложения — миграция пропустила (reason=foreign_peers), I1 прежний" \
    '[ "$(param awg3 I1)" = "$I1_LEG" ] && [ "$(_i1_skip "awg3 reason=foreign_peers")" = 2 ]' "$(errs)"
_i1r_aud=$(_i1_aud)
run import_reconcile awg3 apply
_i1_r=$(param awg3 I1)
check "import_reconcile awg3 apply: rc 0, ответ — JSON сверки (applied, added 1), человек приложения стал своим" \
    '[ "$rc" = 0 ] && jq -e . <<<"$out" >/dev/null && [ "$(jget .applied)" = true ] && [ "$(jget .added)" = 1 ] && jq -e --arg k "$_i1r_pub" "[.users[]?.peers[]?.pubkey?] | index(\$k) != null" "$USERS_FILE" >/dev/null' \
    "rc=$rc out=${out:0:300} $(errs)"
check "… I1 стал универсальным: config.json и «# I1» в конфиге; человек приложения в новом конфиге остался" \
    '[[ "$_i1_r" =~ $I1U_RE ]] && conf_has awg3 "# I1 = $_i1_r" && grep -qF "PublicKey = $_i1r_pub" "$STATE_DIR/amnezia-awg3/awg0.conf"' \
    "I1=$_i1_r $(grep -c "^\[Peer\]" "$STATE_DIR/amnezia-awg3/awg0.conf")"
check "… без рестарта: конфиг в контейнер, syncconf; в журнал — import-reconcile и profile-i1-universal; человеку — строка о замене" \
    'grep -qF "docker cp <stdin> amnezia-awg3:/opt/amnezia/awg/awg0.conf" "$ERR" && grep -q "awg syncconf" "$ERR" && ! grep -q "docker restart\|docker stop" "$ERR" && grep -q "\"action\":\"import-reconcile\"" "$GOAMNEZIA_AUDIT_LOG" && [ "$(_i1_aud)" = $((_i1r_aud + 1)) ] && grep -qF "Сигнатурный пакет I1 по умолчанию заменён на универсальный (awg3)" "$ERR" && ! grep -qF "I1 по умолчанию не заменён" "$ERR"' \
    "aud=$(_i1_aud)/$_i1r_aud $(grep DRY "$ERR" | grep -E "cp <stdin>|syncconf|restart" | tail -3) $(errs)"
run import_reconcile awg3 apply
check "повторная сверка: I1 уже не прежнее умолчание — ни замены, ни строки, ни записи" \
    '[ "$rc" = 0 ] && [ "$(param awg3 I1)" = "$_i1_r" ] && [ "$(_i1_aud)" = $((_i1r_aud + 1)) ] && ! grep -q "I1 по умолчанию" "$ERR" && ! grep -q "docker cp <stdin>" "$ERR"' \
    "rc=$rc aud=$(_i1_aud) $(errs)"
# Замена после сверки не прошла — сверка всё равно прошла: rc 0, тот же JSON, остаётся строка-предупреждение.
i1_live commented
_i1r_pub2=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = 10.8.3.252/32\n' "$_i1r_pub2" "$(ga_awgkeys genpsk)" >>"$LIVE3"
jq -cn --arg k "$_i1r_pub" --arg k2 "$_i1r_pub2" '[{clientId: $k, userData: {clientName: "Phone (app)"}}, {clientId: $k2, userData: {clientName: "Tablet (app)"}}]' \
    >"$CFS/amnezia-awg3/opt/amnezia/awg/clientsTable"
I1R_SYNC_FAIL=1 run import_reconcile awg3 apply
check "сверка прошла, замена I1 после неё нет: rc 0, JSON сверки (applied, added 1), I1 прежний, кода ошибки нет, строка пропуска" \
    '[ "$rc" = 0 ] && jq -e . <<<"$out" >/dev/null && [ "$(jget .applied)" = true ] && [ "$(jget .added)" = 1 ] && [ "$(param awg3 I1)" = "$I1_LEG" ] && [ -z "$GA_ERR_CODE$GA_LAST_ERROR_CODE" ] && grep -qF "$I1_SKIP_HEAD — замена не применилась" "$ERR"' \
    "rc=$rc out=${out:0:300} code=$GA_ERR_CODE $(errs)"
unset -f amnezia_syncconf _ga_amn_param_wait_up
eval "$(declare -f _i1r_real_syncconf | sed '1s/^_i1r_real_syncconf/amnezia_syncconf/')"
eval "$(declare -f _i1r_real_param_wait_up | sed '1s/^_i1r_real_param_wait_up/_ga_amn_param_wait_up/')"
check "в журнале пропусков нет значений I1 (ни <b, ни <r)" '! grep -F "i1-universal-skipped" "$GOAMNEZIA_AUDIT_LOG" | grep -qE "<b|<r"'
rm -f "$CFS/amnezia-awg3/opt/amnezia/awg/clientsTable"
# KEENETIC-I1-Q (P2): в контейнере нет clientsTable (приложение таблицу не писало или её стёрли) — сверка не падает на
# пустой карте имён (было rc 2): пользователь подхвачен с именем по ключу (client-…), I1 заменён после сверки.
i1_live commented
rm -f "$CFS/amnezia-awg3/opt/amnezia/awg/clientsTable"
_p2_pub=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = 10.8.3.253/32\n' "$_p2_pub" "$(ga_awgkeys genpsk)" >>"$LIVE3"
run import_reconcile awg3 apply
_p2_u=$(jq -c --arg k "$_p2_pub" '[.users[]? | select(.peers.awg3.pubkey? == $k)] | first // empty' "$USERS_FILE")
check "P2: clientsTable нет — сверка rc 0, JSON (applied, added 1), пользователь подхвачен с именем по ключу (client-…), адрес 10.8.3.253, I1 универсальный" \
    '[ "$rc" = 0 ] && [ "$(jget .applied)" = true ] && [ "$(jget .added)" = 1 ] && [ -n "$_p2_u" ] && [[ "$(jq -r .name <<<"$_p2_u")" =~ ^client- ]] && [ "$(jq -r .peers.awg3.ip <<<"$_p2_u")" = 10.8.3.253 ] && [[ "$(param awg3 I1)" =~ $I1U_RE ]] && [ -z "$GA_ERR_CODE$GA_LAST_ERROR_CODE" ]' \
    "rc=$rc out=${out:0:300} code=$GA_ERR_CODE $(errs)"
# KEENETIC-I1-Q (P1): пользователь приложения без PresharedKey — подхват кладёт пустой psk и верный адрес (было: @tsv и
# IFS=таб склеивали пустое поле — адрес уезжал в psk, ip пустой, конфиг сервера не собирался).
i1_live commented
_p1_pub=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
printf '\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.8.3.254/32\n' "$_p1_pub" >>"$LIVE3"
jq -cn --arg k "$_p1_pub" '[{clientId: $k, userData: {clientName: "Laptop (app)"}}]' >"$CFS/amnezia-awg3/opt/amnezia/awg/clientsTable"
run import_reconcile awg3 apply
_p1_u=$(jq -c --arg k "$_p1_pub" '[.users[]? | select(.peers.awg3.pubkey? == $k)] | first // empty' "$USERS_FILE")
# peer_blk <ключ> — строки блока [Peer] этого ключа в конфиге сервера (от PublicKey до следующего [Peer]).
peer_blk() { awk -v k="PublicKey = $1" '/^\[Peer\]/ { p = 0 } $0 == k { p = 1 } p' "$STATE_DIR/amnezia-awg3/awg0.conf"; }
_p1_blk=$(peer_blk "$_p1_pub")
check "P1: без PresharedKey — сверка rc 0, added 1; в users.json psk пустой, ip 10.8.3.254, подпись из clientsTable; I1 универсальный" \
    '[ "$rc" = 0 ] && [ "$(jget .added)" = 1 ] && [ -n "$_p1_u" ] && [ "$(jq -r .peers.awg3.psk <<<"$_p1_u")" = "" ] && [ "$(jq -r .peers.awg3.ip <<<"$_p1_u")" = 10.8.3.254 ] && [ "$(jq -r .label <<<"$_p1_u")" = "Laptop (app)" ] && [[ "$(param awg3 I1)" =~ $I1U_RE ]]' \
    "rc=$rc out=${out:0:300} user=$(jq -c "{name, label, ip: .peers.awg3.ip, psk_len: (.peers.awg3.psk | length)}" <<<"$_p1_u" 2>/dev/null) $(errs)"
check "… в конфиге сервера у него AllowedIPs = 10.8.3.254/32 и нет строки PresharedKey; у пользователя P2 PresharedKey на месте" \
    'grep -qxF "AllowedIPs = 10.8.3.254/32" <<<"$_p1_blk" && ! grep -q "PresharedKey" <<<"$_p1_blk" && peer_blk "$_p2_pub" | grep -q "^PresharedKey = "' \
    "$_p1_blk"
rm -f "$CFS/amnezia-awg3/opt/amnezia/awg/clientsTable"
# KEENETIC-I1-NITS (S1): пир контейнера — чужой ввод. \x1f внутри PresharedKey сдвигал поля подхвата: в users.json
# уходили psk «X» и адрес «<PSK>\x1f10.8.3.245», а строка «импортирован пользователь … (<PSK>…)» печатала ключ. Теперь
# такой пир пропущен со строкой-предупреждением (начало его PublicKey и причина, без значений); годный рядом — как обычно.
i1_live commented
_s1_pub=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
_s1_psk=$(ga_awgkeys genpsk)
_s1_ok=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
printf '\n[Peer]\nPublicKey = %s\nPresharedKey = X\037%s\nAllowedIPs = 10.8.3.245/32\n' "$_s1_pub" "$_s1_psk" >>"$LIVE3"
printf '\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.8.3.246/32\n' "$_s1_ok" >>"$LIVE3"
run import_reconcile awg3 apply
_s1_ip=$(jq -r --arg k "$_s1_ok" '[.users[]? | .peers.awg3? // empty | select(.pubkey == $k) | .ip] | first // empty' "$USERS_FILE")
check "S1: \\x1f внутри PresharedKey — сверка rc 0, added 1: годный пир подхвачен с адресом 10.8.3.246, негодного в users.json нет, users.json — JSON" \
    '[ "$rc" = 0 ] && [ "$(jget .applied)" = true ] && [ "$(jget .added)" = 1 ] && [ "$_s1_ip" = 10.8.3.246 ] && jq -e . "$USERS_FILE" >/dev/null && ! grep -qF "$_s1_pub" "$USERS_FILE"' \
    "rc=$rc out=${out:0:300} ip=$_s1_ip $(errs | tr -d '\037')"
check "… строка «Пир <12 знаков ключа>… в конфиге контейнера пропущен: PublicKey или PresharedKey — не ключ WireGuard»; PSK нет ни на экране, ни в users.json" \
    'grep -qF "Пир ${_s1_pub:0:12}… в конфиге контейнера пропущен: PublicKey или PresharedKey — не ключ WireGuard" "$ERR" && ! grep -qF "${_s1_psk:0:20}" "$ERR" && ! grep -qF "${_s1_psk:0:20}" "$USERS_FILE" && ! grep -q $'"'"'\037'"'"' "$ERR"' \
    "$(errs | tr -d '\037')"
# S2: пир без AllowedIPs подхватывался с ip "" — и каждая запись конфига профиля падала («each peer needs pubkey and ip»),
# пока этого пользователя не удалят. Теперь пропущен со строкой-предупреждением; следующая смена параметра проходит.
i1_live commented
_s2_pub=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\n' "$_s2_pub" "$(ga_awgkeys genpsk)" >>"$LIVE3"
run import_reconcile awg3 apply
check "S2: пир без AllowedIPs — сверка rc 0, added 0, его нет в users.json; строка «Пир <ключ>… в конфиге контейнера пропущен: в AllowedIPs нет адреса IPv4»" \
    '[ "$rc" = 0 ] && [ "$(jget .added)" = 0 ] && ! grep -qF "$_s2_pub" "$USERS_FILE" && grep -qF "Пир ${_s2_pub:0:12}… в конфиге контейнера пропущен: в AllowedIPs нет адреса IPv4" "$ERR"' \
    "rc=$rc out=${out:0:300} $(errs)"
_s2_jc=7; [ "$(param awg3 Jc)" = 7 ] && _s2_jc=8
run amnezia_profile_set_param awg3 Jc "$_s2_jc"
check "… следующая смена параметра профиля проходит: rc 0, Jc = $_s2_jc в config.json и в конфиге сервера" \
    '[ "$rc" = 0 ] && [ "$(param awg3 Jc)" = "$_s2_jc" ] && conf_has awg3 "Jc = $_s2_jc" && ! grep -qF "each peer needs pubkey and ip" "$ERR"' \
    "rc=$rc Jc=$(param awg3 Jc) code=$GA_ERR_CODE $(errs)"
# S3: clientsTable «JSON + хвост» — карта имён давала два JSON-текста ({…} и {} от `|| echo`), и сверка падала на
# --argjson с rc 2. Теперь хвост — та же пустая карта, что и у битой таблицы: подхват с именем по ключу.
i1_live commented
_s3_pub=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = 10.8.3.247/32\n' "$_s3_pub" "$(ga_awgkeys genpsk)" >>"$LIVE3"
{ jq -cn --arg k "$_s3_pub" '[{clientId: $k, userData: {clientName: "Watch (app)"}}]'; printf ' garbage\n'; } \
    >"$CFS/amnezia-awg3/opt/amnezia/awg/clientsTable"
run import_reconcile awg3 apply
_s3_u=$(jq -c --arg k "$_s3_pub" '[.users[]? | select(.peers.awg3.pubkey? == $k)] | first // empty' "$USERS_FILE")
check "S3: clientsTable с мусором после JSON — сверка rc 0 (было rc 2), JSON (applied, added 1), пользователь подхвачен с именем по ключу (client-…), адрес 10.8.3.247" \
    '[ "$rc" = 0 ] && [ "$(jget .applied)" = true ] && [ "$(jget .added)" = 1 ] && [ -n "$_s3_u" ] && [[ "$(jq -r .name <<<"$_s3_u")" =~ ^client- ]] && [ "$(jq -r .peers.awg3.ip <<<"$_s3_u")" = 10.8.3.247 ]' \
    "rc=$rc out=${out:0:300} $(errs)"
rm -f "$CFS/amnezia-awg3/opt/amnezia/awg/clientsTable"
# KEENETIC-I1-N (N1): адрес пира — первый IPv4 из AllowedIPs, а не первый элемент. «fd00::2/128, 10.8.3.240/32» (IPv6
# первым — ручная правка конфига) пропускался словами «нет адреса IPv4», хотя IPv4 там есть. Пир только с IPv6 —
# по-прежнему пропущен, с той же верной причиной.
i1_live commented
_n1_pub=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
_n1_v6=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = fd00::2/128, 10.8.3.240/32\n' "$_n1_pub" "$(ga_awgkeys genpsk)" >>"$LIVE3"
printf '\n[Peer]\nPublicKey = %s\nAllowedIPs = fd00::5/128\n' "$_n1_v6" >>"$LIVE3"
run import_reconcile awg3 apply
_n1_ip=$(jq -r --arg k "$_n1_pub" '[.users[]? | .peers.awg3? // empty | select(.pubkey == $k) | .ip] | first // empty' "$USERS_FILE")
check "N1: AllowedIPs «fd00::2/128, 10.8.3.240/32» — сверка rc 0, added 1, адрес 10.8.3.240, строки о пропуске для него нет" \
    '[ "$rc" = 0 ] && [ "$(jget .added)" = 1 ] && [ "$_n1_ip" = 10.8.3.240 ] && ! grep -qF "Пир ${_n1_pub:0:12}… в конфиге контейнера пропущен" "$ERR"' \
    "rc=$rc out=${out:0:300} ip=$_n1_ip $(errs)"
check "… пир только с IPv6 (fd00::5/128) пропущен: «в AllowedIPs нет адреса IPv4», в users.json его нет" \
    'grep -qF "Пир ${_n1_v6:0:12}… в конфиге контейнера пропущен: в AllowedIPs нет адреса IPv4" "$ERR" && ! grep -qF "$_n1_v6" "$USERS_FILE"' \
    "$(errs)"
# KEENETIC-I1-N (N2): пир, которого не подхватить (нет IPv4, не ключ, ключ пользователя другого профиля), — не
# расхождение. Прежде сверка звала его новым (in_sync=false), подхват пропускал (added 0 без причины в ответе), меню SSH
# на каждом старте спрашивало «Подхватить?», а миграция I1 советовала сверку, после которой I1 «заменится сам», — и не
# заменялся. Теперь: skipped с причиной, без вопроса, I1 заменяется.
_n2_ip=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
_n2_key=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
_n2_psk=$(ga_awgkeys genpsk)
run users_add n2other "" awg2
_n2_taken=$(jq -r '[.users[]? | select(.name == "n2other") | .peers.awg2?.pubkey? // empty] | first // empty' "$USERS_FILE")
_n2_add_bad() {
    printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\n' "$_n2_ip" "$_n2_psk" >>"$LIVE3"
    printf '\n[Peer]\nPublicKey = %s\nPresharedKey = not-a-key\nAllowedIPs = 10.8.3.241/32\n' "$_n2_key" >>"$LIVE3"
}
_n2_add_taken() { printf '\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.8.3.242/32\n' "$_n2_taken" >>"$LIVE3"; }
_n2_add() { _n2_add_bad; _n2_add_taken; }
_n2_why() { jq -r --arg k "${1:0:12}" '[.skipped[]? | select(.key == $k) | .reason] | join(",")' <<<"$out"; }
check "N2, подготовка: у пользователя awg2 есть ключ (для пира «ключ другого профиля»)" '[ -n "$_n2_taken" ]'
i1_live commented
_n2_add
run import_reconcile awg3
check "N2: в контейнере только пиры, которых не подхватить, — сверка rc 0, in_sync=true, new и gone пусты; skipped: ip, key, taken по началам ключей" \
    '[ "$rc" = 0 ] && [ "$(jget .in_sync)" = true ] && [ "$(jget ".new | length")" = 0 ] && [ "$(jget ".gone | length")" = 0 ] && [ "$(jget ".skipped | length")" = 3 ] && [ "$(_n2_why "$_n2_ip")" = ip ] && [ "$(_n2_why "$_n2_key")" = key ] && [ "$(_n2_why "$_n2_taken")" = taken ]' \
    "rc=$rc out=${out:0:600} $(errs)"
check "… skipped[].message — строка о пропуске с причиной, та же в stderr; общего секрета нет ни в ответе, ни в stderr" \
    '_m=$(jq -r ".skipped[].message" <<<"$out") && grep -qF "Пир ${_n2_ip:0:12}… в конфиге контейнера пропущен: в AllowedIPs нет адреса IPv4" <<<"$_m" && grep -qF "Пир ${_n2_key:0:12}… в конфиге контейнера пропущен: PublicKey или PresharedKey — не ключ WireGuard" <<<"$_m" && grep -qF "Пир ${_n2_taken:0:12}… в конфиге контейнера пропущен: этот ключ уже у пользователя другого профиля goAmnezia" <<<"$_m" && [ "$(grep -c "в конфиге контейнера пропущен" "$ERR")" = 3 ] && ! grep -qF "$_n2_psk" "$OUT" "$ERR"' \
    "$(errs)"
_n2_confirm=$(declare -f confirm)
eval "$(sed -n '/^_import_reconcile_interactive() {$/,/^}$/p' "$INSTALL")"
eval "$(sed -n '/^_import_reconcile_skipped_show() {$/,/^}$/p' "$INSTALL")"
eval "$(sed -n '/^_import_reconcile_prompt() {$/,/^}$/p' "$INSTALL")"
_profile_installed() { ga_profile_installed "$1"; }
_profile_state() { echo running; }
_pr_name() { printf '%s' "$1"; }
# Ответ «нет»: у awg2 в сухом прогоне свой «пропавший» (n2other не попал в его живой конфиг) — его не трогаем.
confirm() { printf 'ASK:%s\n' "$1" >&2; return 1; }
_n2_users=$(cat "$USERS_FILE")
run _import_reconcile_prompt
check "N2, SSH, старт меню: про awg3 вопроса «Подхватить…» нет — строка «ключи совпадают» и три строки о пропуске с причиной; users.json прежний" \
    '[ "$rc" = 0 ] && ! grep -qF "ASK:Подхватить изменения из контейнера профиля awg3?" "$ERR" && ! grep -qF "Профиль awg3: контейнер разошёлся" "$ERR" && grep -qF "Профиль awg3: ключи в контейнере совпадают со списком пользователей" "$ERR" && [ "$(grep -c "в конфиге контейнера пропущен" "$ERR")" = 3 ] && [ "$(cat "$USERS_FILE")" = "$_n2_users" ]' \
    "rc=$rc $(errs)"
# KEENETIC-I1-N (V4): после «да» на «Подхватить?» строки о пропуске печатались второй раз — сверка с apply повторяла то,
# что SSH показал до вопроса. Теперь — один раз (GA_RECONCILE_QUIET_SKIPPED).
i1_live commented
_v4_new=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = 10.8.3.243/32\n' "$_v4_new" "$(ga_awgkeys genpsk)" >>"$LIVE3"
_n2_add_taken
confirm() { printf 'ASK:%s\n' "$1" >&2; return 0; }
run _import_reconcile_interactive awg3
check "V4: SSH «Подхватить?» → «да»: новый пир подхвачен, строка о пропуске пира «ключ другого профиля» — одна (до вопроса), а не две" \
    '[ "$rc" = 0 ] && grep -qF "ASK:Подхватить изменения из контейнера профиля awg3?" "$ERR" && grep -qF "$_v4_new" "$USERS_FILE" && [ "$(grep -cF "Пир ${_n2_taken:0:12}… в конфиге контейнера пропущен" "$ERR")" = 1 ]' \
    "rc=$rc $(errs)"
unset -f _import_reconcile_interactive _import_reconcile_skipped_show _import_reconcile_prompt _profile_installed _profile_state _pr_name
eval "$_n2_confirm"
# Миграция I1: пиры, с которыми клиент не подключится (нет IPv4, не ключ), — не «люди приложения»: I1 заменён, запись
# конфига снимает их — со строкой о причине, без совета «подхватывают сверкой» (V3: сверка их не подхватит). Начало
# ключа — только знаки base64: конфиг контейнера — чужой ввод.
i1_live commented
_n2_add_bad
printf '\n[Peer]\nPublicKey = \033[31mV3-not-a-key-0000\nAllowedIPs = 10.8.3.245/32\n' >>"$LIVE3"
_n2_fp=$(_i1_skip "awg3 reason=foreign_peers")
run _migrate_i1_universal
check "N2, миграция I1: пиры без IPv4 и не ключ — не «люди приложения»: I1 заменён на универсальный, пропуска foreign_peers нет" \
    '[ "$rc" = 0 ] && [[ "$(param awg3 I1)" =~ $I1U_RE ]] && [ "$(_i1_skip "awg3 reason=foreign_peers")" = "$_n2_fp" ]' \
    "rc=$rc I1=$(param awg3 I1) $(errs)"
check "V3: строка о снятом пире — с причиной, без «подхватывают сверкой»: «… Сверка его не подхватит: в AllowedIPs нет адреса IPv4» и «…: PublicKey или PresharedKey — не ключ WireGuard»; начало ключа с ESC — «??31mV3?not?», управляющих символов нет; общего секрета нет" \
    'grep -qF "В контейнере есть ключ ${_n2_ip:0:12}…, которого нет в списке пользователей goAmnezia, — он пропадёт из конфига профиля awg3. Сверка его не подхватит: в AllowedIPs нет адреса IPv4" "$ERR" && grep -qF "В контейнере есть ключ ${_n2_key:0:12}…, которого нет в списке пользователей goAmnezia, — он пропадёт из конфига профиля awg3. Сверка его не подхватит: PublicKey или PresharedKey — не ключ WireGuard" "$ERR" && grep -qF "В контейнере есть ключ ??31mV3?not?…" "$ERR" && ! grep -q $'"'"'\033'"'"' "$ERR" && ! grep -qF "подхватывают заранее сверкой" "$ERR" && ! grep -qF "$_n2_psk" "$ERR"' \
    "$(errs | tr -d '\033')"
# KEENETIC-I1-N (V1): пир с ключом пользователя другого профиля — годный (ключ, IPv4), клиент с ним работает, а сверка его
# не подхватит. После N2 миграция I1 его чужим не считала и при обновлении молча снимала. Теперь — пропуск со своей
# причиной и советом без сверки; I1 прежний, пир в конфиге контейнера остаётся.
i1_live commented
_n2_add_taken
_v1_c=$(ga_profile_field awg3 container)
_v1_rc=0; _ga_amn_foreign_peer_any awg3 "$_v1_c" 2>/dev/null || _v1_rc=$?
check "V1: в конфиге awg3 только годный пир с ключом пользователя awg2 — «есть чужие» (rc 3), начало ключа — в GA_AMN_FOREIGN_TAG" \
    '[ "$_v1_rc" = 3 ] && [ "$GA_AMN_FOREIGN_TAG" = "${_n2_taken:0:12}" ]' "rc=$_v1_rc tag=${GA_AMN_FOREIGN_TAG:-}"
_v1_tp=$(_i1_skip "awg3 reason=taken_peer")
run _migrate_i1_universal
check "… миграция I1 пропускает профиль: rc 0, I1 прежний, пир в конфиге контейнера остался, строки «пропадёт из конфига» нет; журнал — «awg3 reason=taken_peer»" \
    '[ "$rc" = 0 ] && [ "$(param awg3 I1)" = "$I1_LEG" ] && grep -qF "PublicKey = $_n2_taken" "$LIVE3" && ! grep -qF "пропадёт из конфига" "$ERR" && [ "$(_i1_skip "awg3 reason=taken_peer")" = $((_v1_tp + 1)) ]' \
    "rc=$rc I1=$(param awg3 I1) $(errs)"
check "… строка пропуска: «в контейнере $_v1_c есть ключ <начало>… пользователя другого профиля goAmnezia», совет «уберите этот ключ в приложении Amnezia, затем замените I1» без «подхватите сверкой», готовое <b 0x…> следующей строкой" \
    '_l=$(grep -F "I1 по умолчанию не заменён" "$ERR") && grep -F "$I1_SKIP_HEAD — в контейнере $_v1_c есть ключ ${_n2_taken:0:12}… пользователя другого профиля goAmnezia" <<<"$_l" | grep -qF "Чтобы подходили и Keenetic (ключи 1.5 и 2.0), уберите этот ключ в приложении Amnezia, затем замените I1 в «Изменить один параметр» карточки профиля на значение ниже:" && ! grep -qF "Подхватите" <<<"$_l" && ! grep -qF "закройте меню goAmnezia" <<<"$_l" && [[ "$(_i1_skip_val)" =~ $I1V_RE ]]' \
    "$(errs)"
# KEENETIC-I1-N (V2): разбор пиров сбился (здесь — битый список пользователей на входе: первый jq падает, второй — нет).
# Прежде у недостающих пиров выходило «не ключ», человек приложения считался не чужим — миграция I1 сняла бы его.
# Теперь ga_import_peers_sort — отказ, _ga_amn_foreign_peer_any — «не узнать» (rc 2), миграция — пропуск conf_unreadable.
i1_live commented
_v2_pub=$(ga_awgkeys_pubkey "$(ga_awgkeys genkey)")
printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = 10.8.3.244/32\n' "$_v2_pub" "$(ga_awgkeys genpsk)" >>"$LIVE3"
_v2_peers=$(jq -cn --arg k "$_v2_pub" '[{pubkey: $k, ip: "10.8.3.244", psk: ""}]')
_v2_rc=0; _v2_out=$(ga_import_peers_sort awg3 "$_v2_peers" '{"users": [ broken' 2>/dev/null) || _v2_rc=$?
check "V2: ga_import_peers_sort на сбое разбора — отказ (rc ≠ 0) и пустой ответ, а не «не ключ» всем пирам" \
    '[ "$_v2_rc" != 0 ] && [ -z "$_v2_out" ]' "rc=$_v2_rc out=$_v2_out"
_v2_ul=$(declare -f ga_users_load)
ga_users_load() { printf -v "$1" '%s' '{"users": [ broken'; }
_v2_frc=0; _ga_amn_foreign_peer_any awg3 "$_v1_c" 2>/dev/null || _v2_frc=$?
_v2_cu=$(_i1_skip "awg3 reason=conf_unreadable")
run _migrate_i1_universal
eval "$_v2_ul"
check "… _ga_amn_foreign_peer_any — «не узнать» (rc 2); миграция I1 пропускает профиль (conf_unreadable): I1 прежний, человек приложения в конфиге контейнера остался" \
    '[ "$_v2_frc" = 2 ] && [ "$rc" = 0 ] && [ "$(param awg3 I1)" = "$I1_LEG" ] && grep -qF "PublicKey = $_v2_pub" "$LIVE3" && [ "$(_i1_skip "awg3 reason=conf_unreadable")" = $((_v2_cu + 1)) ]' \
    "frc=$_v2_frc rc=$rc I1=$(param awg3 I1) $(errs)"
i1_live commented
_n2_add
act import-reconcile --profile=awg3 --apply=on
check "N2, мост import-reconcile --apply=on: success, in_sync, added 0, skipped — три; сообщение — «совпадают» и причины пропуска словами, а не «добавлено 0» без слов" \
    '[ "$(jget .status)" = success ] && [ "$(jget .in_sync)" = true ] && [ "$(jget .added)" = 0 ] && [ "$(jget ".skipped | length")" = 3 ] && _m=$(jget .message) && grep -qF "Профиль awg3: подключения контейнера совпадают со списком пользователей. Пир ${_n2_ip:0:12}… в конфиге контейнера пропущен: в AllowedIPs нет адреса IPv4" <<<"$_m" && grep -qF "не ключ WireGuard" <<<"$_m" && grep -qF "другого профиля goAmnezia" <<<"$_m" && ! grep -qF "$_n2_psk" "$OUT" "$ERR"' \
    "out=${out:0:700} $(errs)"
check "V1, мост «Подхватить» (apply): пир с ключом другого профиля после сверки остался в конфиге контейнера, I1 прежний (миграция пропустила профиль)" \
    'grep -qF "PublicKey = $_n2_taken" "$LIVE3" && [ "$(param awg3 I1)" = "$I1_LEG" ] && [ "$(_i1_skip "awg3 reason=taken_peer")" = $((_v1_tp + 2)) ]' \
    "I1=$(param awg3 I1) $(errs)"
run users_del n2other
cp "$STATE_DIR/amnezia-awg3/awg0.conf" "$LIVE3"

section "12: K5-1 — проверка значения без записи (amnezia_profile_param_check): те же отказы, что у записи, и ничего не пишет"
# SSH спрашивает «Записать …?» только после этой проверки (живой стенд 29.10: подтверждали S4 = 41 и I1 с <r>, а отказ
# приходил после «1»). Отказ — тот же код и те же слова, что у amnezia_profile_set_param; годное значение — код 0 и ни
# байта в config.json и конфиге сервера, ни шага Docker.
_k51_sum() { cat "$CONFIG_FILE" "$STATE_DIR/amnezia-awg2/awg0.conf" 2>/dev/null | shasum | cut -d' ' -f1; }
_k51_before=$(_k51_sum)
_k51_jc=$(param awg2 Jc)
_k51_new=6; [ "$_k51_jc" = 6 ] && _k51_new=7   # годное и не равное нынешнему
for _k51 in "S4|41|S4 = 41 не принят: при стандартном MTU 1280" "I1|<r 2>|I1 не принят: пакет начинается с <r>" \
            "Jc|128|Jc + число заданных I1–I5 = 128" "MTU|9000|" "Nope|1|"; do
    IFS='|' read -r _k51_k _k51_v _k51_w <<<"$_k51"
    run amnezia_profile_param_check awg2 "$_k51_k" "$_k51_v"
    _k51_rc=$rc; _k51_code=$GA_ERR_CODE; _k51_msg=$GA_ERR_MSG
    run amnezia_profile_set_param awg2 "$_k51_k" "$_k51_v"
    check "проверка awg2 $_k51_k = $_k51_v — отказ (rc 2) с тем же кодом и словами, что у записи (${_k51_code:-?})" \
        '[ "$_k51_rc" = 2 ] && [ "$rc" = 2 ] && [ -n "$_k51_code" ] && [ "$_k51_code" = "$GA_ERR_CODE" ] && [ "$_k51_msg" = "$GA_ERR_MSG" ] && { [ -z "$_k51_w" ] || grep -qF -- "$_k51_w" <<<"$_k51_msg"; }' \
        "rc=$_k51_rc/$rc code=$_k51_code/$GA_ERR_CODE msg=${_k51_msg:0:200}"
done
check "… после отказов config.json и awg0.conf те же" '[ "$(_k51_sum)" = "$_k51_before" ]'
run amnezia_profile_param_check awg2 Jc "$_k51_new"
check "проверка awg2 Jc = $_k51_new — код 0, вывода нет, Docker не трогали, config.json и awg0.conf те же, Jc прежний ($_k51_jc)" \
    '[ "$rc" = 0 ] && [ -z "$out" ] && ! grep -q "DRY:" "$ERR" && [ "$(_k51_sum)" = "$_k51_before" ] && [ "$(param awg2 Jc)" = "$_k51_jc" ] && [ -z "$GA_ERR_CODE$GA_LAST_ERROR_CODE" ]' \
    "rc=$rc out=${out:0:200} $(errs)"
# Лишний аргумент записи не включает режим проверки: запись передаёт дальше три аргумента, а не «$@» (иначе
# «check-only» четвёртым словом молча выключил бы запись).
run amnezia_profile_set_param awg2 Jc "$_k51_new" check-only
check "amnezia_profile_set_param awg2 Jc $_k51_new check-only — записано (четвёртое слово запись не выключает)" \
    '[ "$rc" = 0 ] && [ "$(param awg2 Jc)" = "$_k51_new" ]' "rc=$rc code=$GA_ERR_CODE $GA_ERR_MSG now=$(param awg2 Jc)"
run amnezia_profile_set_param awg2 Jc "$_k51_jc"

section "13: KEENETIC-I1-N N3/N4 — HeaderProtectionKey: значения нет ни в выводе, ни в ответе моста, ни в журнале"
# Прежде строка «Профиль …: параметр HeaderProtectionKey = <ключ>», отказ «… = <ключ> не применился … (<прежний>)», поле
# value/old_value ответа и сообщение моста печатали ключ целиком. Теперь одна функция (ga_amn_param_shown): «задан
# (скрыт)» словами и «***» в полях. Ключи — тестовые (base64 строк «N3-test-only-…» по 32 байта), не настоящие.
_n3_k1=TjMtdGVzdC1vbmx5LW5vdC1hLXJlYWwta2V5LTAwMDE=
_n3_k2=TjMtdGVzdC1vbmx5LW5vdC1hLXJlYWwta2V5LTAwMDI=
_n3_bad=c2hvcnQ=   # base64, но 5 байт — валидатор отвергнет
_n3_hid="задан (скрыт)"
# _n3_clean <значение…> — ни одного из значений (по первым 12 знакам) нет в stdout, stderr, сообщении ошибки и журнале.
_n3_clean() {
    local v
    for v in "$@"; do
        ! grep -qF -- "${v:0:12}" "$OUT" "$ERR" "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null || return 1
        ! grep -qF -- "${v:0:12}" <<<"$GA_ERR_MSG $out" || return 1
    done
}
_n3_where() {   # где нашлось — без самого значения (только имена мест)
    local v w=""
    for v in "$@"; do
        grep -qF -- "${v:0:12}" "$OUT" && w+=" stdout"; grep -qF -- "${v:0:12}" "$ERR" && w+=" stderr"
        grep -qF -- "${v:0:12}" "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null && w+=" журнал"; grep -qF -- "${v:0:12}" <<<"$GA_ERR_MSG" && w+=" GA_ERR_MSG"
    done
    echo "rc=$rc code=$GA_ERR_CODE нашлось:${w:- нигде}"
}
check "ga_amn_param_shown: ключ — «задан (скрыт)» / «***», пустой — «-» / «», остальное — как есть" \
    '[ "$(ga_amn_param_shown HeaderProtectionKey "$_n3_k1")" = "$_n3_hid" ] && [ "$(ga_amn_param_shown HeaderProtectionKey "$_n3_k1" json)" = "***" ] && [ "$(ga_amn_param_shown HeaderProtectionKey "")" = - ] && [ -z "$(ga_amn_param_shown HeaderProtectionKey "" json)" ] && [ "$(ga_amn_param_shown Jc 4)" = 4 ] && [ "$(ga_amn_param_shown Jc 4 json)" = 4 ] && [ "$(ga_amn_param_shown Jc "")" = - ]'
# а) Успех (SSH и ядро): строка итога и ответ.
run amnezia_profile_set_param awg3 HeaderProtectionKey "$_n3_k1"
check "записан: «Профиль awg3: параметр HeaderProtectionKey = задан (скрыт)», value «***», ключа нет нигде" \
    '[ "$rc" = 0 ] && [ "$(param awg3 HeaderProtectionKey)" = "$_n3_k1" ] && grep -qF "Профиль awg3: параметр HeaderProtectionKey = $_n3_hid" "$ERR" && [ "$(jq -r .value <<<"$out")" = "***" ] && _n3_clean "$_n3_k1"' \
    "$(_n3_where "$_n3_k1")"
# б) Успех через мост (бот, панель, MCP): сообщение, value, result.value и result.old_value (прежний — тоже ключ).
act profile-set-param --profile=awg3 --key=HeaderProtectionKey --value="$_n3_k2"
check "мост: status success, сообщение «… теперь задан (скрыт).», value, result.value и result.old_value — «***», ни нового, ни прежнего ключа" \
    '[ "$(jget .status)" = success ] && [ "$(param awg3 HeaderProtectionKey)" = "$_n3_k2" ] && grep -qF "Профиль awg3: параметр HeaderProtectionKey теперь $_n3_hid." <<<"$(jget .message)" && [ "$(jget .value)" = "***" ] && [ "$(jget .result.value)" = "***" ] && [ "$(jget .result.old_value)" = "***" ] && GA_ERR_MSG= _n3_clean "$_n3_k1" "$_n3_k2"' \
    "status=$(jget .status) $(_n3_where "$_n3_k1" "$_n3_k2")"
# в) Откат (серверный параметр применяется рестартом — подставной контейнер с новым ключом не поднимается; с прежним
# поднимается) и «не применился» (не поднимается вовсе): в отказе ни нового, ни прежнего ключа.
eval "$(declare -f _ga_amn_wait_up_iface | sed '1s/^_ga_amn_wait_up_iface/_n3_real_wait_up/')"
_ga_amn_wait_up_iface() {
    if [ "${N3_DOWN:-0}" = 1 ] || grep -qxF -- "HeaderProtectionKey = $_n3_k1" "$(ga_amn_state_conf "$1")" 2>/dev/null; then
        ga_err container_not_up "$(tf amnezia_err_not_up "$(ga_profile_field "$1" container)" "$(ga_amn_iface "$1")")"
        return 1
    fi
    _n3_real_wait_up "$@"
}
run amnezia_profile_set_param awg3 HeaderProtectionKey "$_n3_k1"
check "не поднялся с новым ключом — param_rolled_back: «HeaderProtectionKey = задан (скрыт) не применился … (задан (скрыт))», прежний ключ на месте, ключей нет нигде" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = param_rolled_back ] && grep -qF "HeaderProtectionKey = $_n3_hid не применился:" <<<"$GA_ERR_MSG" && grep -qF "Вернул прежнее значение ($_n3_hid)" <<<"$GA_ERR_MSG" && [ "$(param awg3 HeaderProtectionKey)" = "$_n3_k2" ] && _n3_clean "$_n3_k1" "$_n3_k2"' \
    "$(_n3_where "$_n3_k1" "$_n3_k2")"
N3_DOWN=1 run amnezia_profile_set_param awg3 HeaderProtectionKey "$_n3_k1"
check "не поднялся и с прежним — param_apply_failed: «… = задан (скрыт) не применился … Прежнее значение (задан (скрыт))», ключей нет нигде" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = param_apply_failed ] && grep -qF "HeaderProtectionKey = $_n3_hid не применился:" <<<"$GA_ERR_MSG" && grep -qF "Прежнее значение ($_n3_hid)" <<<"$GA_ERR_MSG" && _n3_clean "$_n3_k1" "$_n3_k2"' \
    "$(_n3_where "$_n3_k1" "$_n3_k2")"
unset -f _ga_amn_wait_up_iface
eval "$(declare -f _n3_real_wait_up | sed '1s/^_n3_real_wait_up/_ga_amn_wait_up_iface/')"
# г) Откат через мост: Docker поднял контейнер без опубликованного порта (фикстура .unpublished, как в §6 д).
: >"$CFS/amnezia-awg3/.unpublished"
act profile-set-param --profile=awg3 --key=HeaderProtectionKey --value="$_n3_k1"
check "мост, откат: status error, param_rolled_back, в сообщении «задан (скрыт)» дважды, ни нового, ни прежнего ключа" \
    '[ "$(jget .status)" = error ] && [ "$(jget .code)" = param_rolled_back ] && [ "$(grep -oF "$_n3_hid" <<<"$(jget .message)" | wc -l | tr -d " ")" = 2 ] && [ "$(param awg3 HeaderProtectionKey)" = "$_n3_k2" ] && GA_ERR_MSG= _n3_clean "$_n3_k1" "$_n3_k2"' \
    "status=$(jget .status) code=$(jget .code) $(_n3_where "$_n3_k1" "$_n3_k2")"
rm -f "$CFS/amnezia-awg3/.unpublished"
mkdir -p "$CFS/amnezia-awg3"
# д) Отказ валидатора (не 32 байта): ни в отказе, ни в журнале, ни в ответе моста — ни отвергнутого, ни нынешнего.
run amnezia_profile_set_param awg3 HeaderProtectionKey "$_n3_bad"
check "ядро: «не 32 байта» — отказ rc 2, значение прежнее, ни отвергнутого, ни нынешнего ключа нигде" \
    '[ "$rc" = 2 ] && [ -n "$GA_ERR_CODE" ] && [ "$(param awg3 HeaderProtectionKey)" = "$_n3_k2" ] && _n3_clean "$_n3_bad" "$_n3_k2"' \
    "$(_n3_where "$_n3_bad" "$_n3_k2")"
act profile-set-param --profile=awg3 --key=HeaderProtectionKey --value="$_n3_bad"
check "мост: тот же отказ (status error), ни отвергнутого, ни нынешнего ключа ни в ответе, ни в выводе, ни в журнале" \
    '[ "$(jget .status)" = error ] && [ "$(param awg3 HeaderProtectionKey)" = "$_n3_k2" ] && GA_ERR_MSG= _n3_clean "$_n3_bad" "$_n3_k2"' \
    "status=$(jget .status) code=$(jget .code) $(_n3_where "$_n3_bad" "$_n3_k2")"

DONE=1
printf '\nИТОГ: %d пройдено, %d провалено\n' "$pass" "$fail"
[ "$fail" = 0 ]
