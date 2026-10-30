#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон lib/import.sh без Docker (GOAMNEZIA_DRY_RUN=1).
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_import_dryrun.sh
# Нужны: bash ≥ 5, jq, python3. Контейнеры изображают фикстуры
# tests/fixtures/import: docker ps (GOAMNEZIA_DOCKER_PS_FIXTURE) и файловая
# система контейнеров (GOAMNEZIA_CONTAINER_FS_DIR — копия во временном каталоге,
# чтобы сценарий сверки мог «менять контейнер»).
#
# Что проверяем (PLAN §6.5, CONTRACT §4.10):
#   import_scan находит amnezia-awg2 (AmneziaWG 2.0 приложения, два пира) и
#   legacy amnezia-awg (1.0, wg0.conf), amnezia-xray — не поддерживается,
#   nginx-proxy не виден; секретов в выводе нет → import_adopt amnezia-awg2:
#   профиль awg2 (линейка 2.0, порт, подсеть, параметры, pubkey сервера,
#   preset imported), state/ с конфигом и ключом сервера 0600, пользователи
#   «iPhone_Anny» и «office-router» с пометкой imported и без приватных ключей,
#   export_conf им отказывает кодом no_private_key, повторный подхват →
#   already_managed → legacy amnezia-awg → профиль awg1 с container amnezia-awg,
#   iface wg0, wg-утилитами и путём wg0.conf → серверный конфиг из users.json
#   даёт те же пиры → сверка: контейнер получил третьего пира (Laptop) →
#   in_sync=false, new; apply → пользователь Laptop; пир пропал → gone, apply →
#   active=false → ошибки (имя, неизвестный контейнер, чужой amnezia-xray) → сторож видит
#   контейнеры работающими → словарь.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-import.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
ALL="$TMP/all.log"
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
FX="$ROOT/tests/fixtures/import"
export GOAMNEZIA_DOCKER_PS_FIXTURE="$FX/docker_ps.txt"
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/cfs"
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$UPSTREAM_DIR" "$GOAMNEZIA_CONTAINER_FS_DIR"
cp -R "$ROOT/lib/upstream/." "$UPSTREAM_DIR/"
cp -R "$FX/fs/." "$GOAMNEZIA_CONTAINER_FS_DIR/"

# Временные файлы с ключами — в каталоге стенда (L1-19). Без переменной продукт кладёт их в /dev/shm,
# а он есть только на Linux: там они жили бы на машине прогона, и оборванный прогон оставлял бы ключи.
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 700; return 0; }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
# audit_has <действие> [начало details] [who] — есть ли запись в журнале аудита.
# Журнал — один объект JSON на строку (CONTRACT §7.1); прежние проверки искали
# поля через табуляцию и после смены формата молча не находили ничего.
audit_has() {
    jq -R -e -n --arg a "$1" --arg d "${2:-}" --arg w "${3:-}" \
        '[inputs | fromjson? // empty | select(.action == $a and ($w == "" or .who == $w)
          and ((.details // "") | startswith($d)))] | length > 0' "$GOAMNEZIA_AUDIT_LOG" >/dev/null 2>&1
}
section() { printf '\n── %s\n' "$1"; }
is_json() { jq -e . <<<"$1" >/dev/null 2>&1; }
# Права восьмерично: сначала GNU (-c), потом BSD (-f). Не наоборот: у GNU «-f» — сведения
# о файловой системе, он печатал их перед правами, и на Linux краснела каждая проверка 600/700.
perm() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }
run() {
    GA_ERR_CODE=""; GA_ERR_MSG=""; GA_LAST_ERROR_CODE=""
    "$@" >"$OUT" 2>"$ERR"; rc=$?
    out=$(<"$OUT")
    cat "$OUT" "$ERR" >>"$ALL"
    return 0
}
errs() { cat "$ERR" 2>/dev/null; }
jget() { jq -r "$1" <<<"$out" 2>/dev/null; }

section "окружение"
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia users export import; do
    # shellcheck disable=SC1090
    source "$ROOT/lib/$_m.sh"
done
mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR"
# Версия в фикстуре — ТЕКУЩАЯ из lib/common.sh, а не литерал: со старым номером
# каждый подъём версии включал бы миграцию, а она делает предмиграционный бэкап —
# лишний архив ломал проверки списка бэкапов.
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOF
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"], "profiles": {}}
EOF
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"
P1=$(jq -r '.awg2[0]' "$FX/pubkeys.json"); P2=$(jq -r '.awg2[1]' "$FX/pubkeys.json"); P3=$(jq -r '.awg2[2]' "$FX/pubkeys.json")
Q1=$(jq -r '.awg[0]' "$FX/pubkeys.json"); SRV2_PUB=$(jq -r '.srv2_pub' "$FX/pubkeys.json")
SRV2_PRIV=$(awk -F' = ' '/^PrivateKey/ {print $2; exit}' "$FX/fs/amnezia-awg2/opt/amnezia/awg/awg0.conf")
PSK2=$(awk -F' = ' '/^PresharedKey/ {print $2; exit}' "$FX/fs/amnezia-awg2/opt/amnezia/awg/awg0.conf")
check "фикстуры: ключи прочитаны" '[ "${#P1}" = 44 ] && [ "${#SRV2_PRIV}" = 44 ] && [ "${#PSK2}" = 44 ]'

# ── 1. Поиск ─────────────────────────────────────────────────────────────────
section "import_scan"
run import_scan
check "import_scan: rc=0, JSON-массив из 3 контейнеров amnezia-* (nginx-proxy отброшен)" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jget length)" = 3 ] && [ "$(jget "[.[].container] | join(\",\")")" = "amnezia-awg2,amnezia-awg,amnezia-xray" ]' "rc=$rc $(errs | tail -3) $out"
check "amnezia-awg2: kind awg, порт 38123/udp, линейка 2.0, подсеть 10.8.1.0/24, iface awg0, pubkey сервера, 2 пира, adoptable → awg2" \
    '[ "$(jget ".[0].kind")" = awg ] && [ "$(jget ".[0].port")" = 38123 ] && [ "$(jget ".[0].proto")" = udp ] && [ "$(jget ".[0].line")" = 2.0 ] && [ "$(jget ".[0].subnet")" = 10.8.1.0/24 ] && [ "$(jget ".[0].iface")" = awg0 ] && [ "$(jget ".[0].server_pubkey")" = "$SRV2_PUB" ] && [ "$(jget ".[0].peers")" = 2 ] && [ "$(jget ".[0].adoptable")" = true ] && [ "$(jget ".[0].suggested_profile")" = awg2 ] && [ "$(jget ".[0].managed")" = false ]' "$(jget ".[0] | del(.clients)")"
check "amnezia-awg2: клиенты из clientsTable с именами и адресами, known=false" \
    '[ "$(jget ".[0].clients[0].name")" = "iPhone Анны" ] && [ "$(jget ".[0].clients[0].ip")" = 10.8.1.1 ] && [ "$(jget ".[0].clients[1].name")" = office-router ] && [ "$(jget "[.[0].clients[].known] | any")" = false ]' "$(jget ".[0].clients")"
check "amnezia-awg (legacy): kind legacy, wg0, линейка 1.0, порт 51820, подсеть 10.8.5.0/24, adoptable → awg1, образ amnezia-awg" \
    '[ "$(jget ".[1].kind")" = legacy ] && [ "$(jget ".[1].iface")" = wg0 ] && [ "$(jget ".[1].line")" = 1.0 ] && [ "$(jget ".[1].listen_port")" = 51820 ] && [ "$(jget ".[1].subnet")" = 10.8.5.0/24 ] && [ "$(jget ".[1].suggested_profile")" = awg1 ] && [ "$(jget ".[1].adoptable")" = true ] && [ "$(jget ".[1].image")" = amnezia-awg ]' "$(jget ".[1] | del(.clients)")"
check "amnezia-xray: kind unsupported, adoptable=false, reason unsupported_container, порт 443/tcp" '[ "$(jget ".[2].kind")" = unsupported ] && [ "$(jget ".[2].adoptable")" = false ] && [ "$(jget ".[2].reason")" = unsupported_container ] && [ "$(jget ".[2].port")" = 443 ]' "$(jget ".[2]")"
check "секретов в выводе нет: ни приватного ключа сервера, ни PSK" '! grep -qF "$SRV2_PRIV" <<<"$out" && ! grep -qF "$PSK2" <<<"$out" && ! grep -q "psk\"" <<<"$out"'
run amnezia_scan_containers
check "короткий amnezia_scan_containers: 3 записи, managed=false везде" '[ "$rc" = 0 ] && [ "$(jget length)" = 3 ] && [ "$(jget "[.[].managed] | any")" = false ]' "$out"

# ── 1a. Установка профиля поверх контейнера приложения ──────────────────────
# Самый частый путь к беде: сервер ставили приложением, потом запустили наш
# установщик и выбрали awg2. Раньше установка сносила amnezia-awg2 как
# «остаток» — вместе с ключами всех, кто через него ходит.
section "установка профиля поверх контейнера приложения"
cp "$CONFIG_FILE" "$TMP/config.before"
run amnezia_profile_install awg2
check "profile-install awg2 → foreign_container, контейнер и config.json не тронуты" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = foreign_container ] && ! grep -q "docker build\|docker rm\|docker stop\|docker run" "$ERR" && [ ! -e "$STATE_DIR/amnezia-awg2" ] && cmp -s "$CONFIG_FILE" "$TMP/config.before" && [ "$(grep -c "^\[Peer\]" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf")" = 2 ]' \
    "rc=$rc code=$GA_ERR_CODE $(errs | tail -3)"
check "TK-4 отказ не утверждает «поставило приложение», а говорит, что контейнера нет в config.json, и что стоящий тоже подхватывается" \
    'grep -qF "в config.json goAmnezia его нет" "$ERR" && grep -qF "и работающий, и остановленный" "$ERR"' "$(errs | tail -2)"
# UP-5: неразбираемый config.json — отказ config_corrupt, а не «контейнер поставило приложение».
cp "$CONFIG_FILE" "$TMP/config.good"
head -c 40 "$TMP/config.good" >"$CONFIG_FILE"
run amnezia_profile_install awg2
check "UP-5 битый config.json: profile-install отвечает config_corrupt, а не foreign_container" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = config_corrupt ] && ! grep -q "docker build\|docker rm\|docker run" "$ERR"' "rc=$rc code=$GA_ERR_CODE $(errs | tail -2)"
cp "$TMP/config.good" "$CONFIG_FILE"; chmod 600 "$CONFIG_FILE"
run ga_amn_foreign_peers amnezia-awg
check "legacy amnezia-awg: пир в wg0.conf тоже виден стражу" '[ "$out" = 1 ]' "out=$out"
run ga_amn_foreign_peers amnezia-xray
check "amnezia-xray (без конфига AmneziaWG): 0 — не наш случай" '[ "$out" = 0 ]' "out=$out"

# ── 2. Подхват amnezia-awg2 ──────────────────────────────────────────────────
section "import_adopt amnezia-awg2"
# Универсальный I1 (KEENETIC-I1): <b 0x{ID 2 байта}{DNS-ответ icloud.com}>.
I1U_RE='^<b 0x[0-9a-f]{4}858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>$'
# KMOD-02: сигнатурный пакет из конфига контейнера уходит в ключи людей, и с отрицательным размером модуль ядра
# AmneziaWG у клиента падает (#237). Подхват — отказ словами до всякого изменения; исправили — подхват идёт как обычно.
A2C="$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
cp "$A2C" "$TMP/awg2.conf.good"
sed 's/^# I1 = .*/# I1 = <rc -5><b 0x01>/' "$TMP/awg2.conf.good" >"$A2C"
run import_adopt amnezia-awg2
check "KMOD-02: I1 с <rc -5> в конфиге контейнера → отказ params_invalid словами, профиля и state/ нет" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = params_invalid ] && grep -qF "положительным числом" <<<"$GA_ERR_MSG" && grep -qF "amnezia-awg2" <<<"$GA_ERR_MSG" && [ "$(jq -c ".profiles.awg2 // null" "$CONFIG_FILE")" = null ] && [ ! -d "$STATE_DIR/amnezia-awg2" ] && ! grep -q "docker cp\|docker exec -d" "$ERR"' \
    "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
cp "$TMP/awg2.conf.good" "$A2C"
# Дефект 1a.11: профиль попадает в config.json раньше, чем добавлены пиры и
# заполнено состояние. Отказ дальше оставлял запись без пиров, а повторный
# подхват отвечал «профиль уже есть» — штатного выхода из полусостояния не было.
_orig_users_write=$(declare -f ga_users_write)
ga_users_write() { ga_err users_write_failed "stend: users.json не записывается"; return 1; }
run import_adopt amnezia-awg2
check "сорвавшийся подхват: rc≠0, записи профиля в config.json не осталось, пользователей нет" \
    '[ "$rc" != 0 ] && [ "$(jq -c ".profiles.awg2 // null" "$CONFIG_FILE")" = null ] && [ "$(jq -r ".users | length" "$USERS_FILE")" = 0 ]' \
    "rc=$rc code=$GA_ERR_CODE $(jq -c .profiles "$CONFIG_FILE")"
check "каталог state/, заведённый подхватом, убран" '[ ! -d "$STATE_DIR/amnezia-awg2" ]' "$(ls -A "$STATE_DIR" 2>/dev/null)"
eval "$_orig_users_write"
run import_scan
check "после отката контейнер снова adoptable — повтор возможен, а не «профиль уже есть»" \
    '[ "$(jget ".[0].adoptable")" = true ] && [ "$(jget ".[0].reason")" = "" ]' "$(jget ".[0] | {adoptable, reason}")"
run import_adopt amnezia-awg2
check "import_adopt: rc=0, JSON: profile awg2, line 2.0, port 38123, subnet 10.8.1.0/24, users_added 2, peers 2, hint" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jget .profile)" = awg2 ] && [ "$(jget .line)" = 2.0 ] && [ "$(jget .port)" = 38123 ] && [ "$(jget .subnet)" = 10.8.1.0/24 ] && [ "$(jget .users_added)" = 2 ] && [ "$(jget .peers)" = 2 ] && [ -n "$(jget .hint)" ]' "rc=$rc code=$GA_ERR_CODE $(errs | tail -3) $out"
check "контейнер не пересоздавался: в stderr нет docker rm/run/build" '! grep -q "docker rm\|docker run\|docker build" "$ERR"' "$(grep DRY "$ERR")"
# SEC-NET-1: люди одного профиля не достают друг до друга через сервер — у контейнера приложения тоже. Правило —
# командой в его сетевом пространстве; start.sh приложения не переписывается (дальше правило держит сторож).
iso2_exec="docker exec -i amnezia-awg2 sh -c '$(ga_amn_isolation_cmd awg0 on)'"
check "SEC-NET-1 работающий контейнер приложения: изоляция клиентов командой (awg0), start.sh приложения не тронут" \
    'grep -qF "$iso2_exec" "$ERR" && ! grep -qE "docker cp [^ ]+ amnezia-awg2:/opt/amnezia/start.sh" "$ERR"' "$(grep DRY "$ERR" | tail -3)"
# Снимок контейнера как он был у приложения — до того, как мы начнём писать в
# его конфиг и clientsTable. Лежит вне backups/: список и ротация бэкапов его
# не видят, восстановление не примет за архив.
SNAP=$(jget .snapshot)
check "снимок до подхвата: import-snapshots/amnezia-awg2-…, 0700, конфиг и clientsTable как в контейнере, файлы 0600" \
    '[[ "$SNAP" == "$GOAMNEZIA_DIR/import-snapshots/amnezia-awg2-"* ]] && [ "$(perm "$SNAP")" = 700 ] && cmp -s "$SNAP/awg0.conf" "$FX/fs/amnezia-awg2/opt/amnezia/awg/awg0.conf" && cmp -s "$SNAP/clientsTable" "$FX/fs/amnezia-awg2/opt/amnezia/awg/clientsTable" && [ "$(perm "$SNAP/awg0.conf")" = 600 ] && [ ! -e "$SNAP/wg0.conf" ]' \
    "snap=$SNAP $(ls -la "$SNAP" 2>&1)"
check "в выводе снимок назван, в список бэкапов он не попал" \
    'grep -qF "$SNAP" "$ERR" && [ -z "$(find "$BACKUP_DIR" -name "*.tar.gz*" 2>/dev/null)" ]' "$(errs | tail -3)"
check "config.json awg2: container amnezia-awg2, iface awg0, preset imported, imported=true, image amneziavpn/amneziawg-go:latest, server_pubkey, params S3=12 и H1 диапазон, I1 прежнего умолчания стал универсальным (KEENETIC-I1)" \
    '[ "$(jq -r .profiles.awg2.container "$CONFIG_FILE")" = amnezia-awg2 ] && [ "$(jq -r .profiles.awg2.iface "$CONFIG_FILE")" = awg0 ] && [ "$(jq -r .profiles.awg2.preset "$CONFIG_FILE")" = imported ] && [ "$(jq -r .profiles.awg2.imported "$CONFIG_FILE")" = true ] && [ "$(jq -r .profiles.awg2.image "$CONFIG_FILE")" = amneziavpn/amneziawg-go ] && [ "$(jq -r .profiles.awg2.image_tag "$CONFIG_FILE")" = latest ] && [ "$(jq -r .profiles.awg2.server_pubkey "$CONFIG_FILE")" = "$SRV2_PUB" ] && [ "$(jq -r .profiles.awg2.params.S3 "$CONFIG_FILE")" = 12 ] && [ "$(jq -r .profiles.awg2.params.H1 "$CONFIG_FILE")" = 1000000-1004000 ] && [[ "$(jq -r .profiles.awg2.params.I1 "$CONFIG_FILE")" =~ $I1U_RE ]]' "$(jq -c ".profiles.awg2 | del(.params)" "$CONFIG_FILE")"
check "state/amnezia-awg2: awg0.conf — конфиг контейнера, где I1 прежнего умолчания стал универсальным (остальное как было), ключ сервера 0600, clientsTable, каталог 0700" \
    'diff <(grep -v "^# I1 = " "$STATE_DIR/amnezia-awg2/awg0.conf") <(grep -v "^# I1 = " "$FX/fs/amnezia-awg2/opt/amnezia/awg/awg0.conf") >/dev/null && [[ "$(sed -n "s/^# I1 = //p" "$STATE_DIR/amnezia-awg2/awg0.conf")" =~ $I1U_RE ]] && [ "$(cat "$STATE_DIR/amnezia-awg2/wireguard_server_private_key.key")" = "$SRV2_PRIV" ] && [ "$(perm "$STATE_DIR/amnezia-awg2/wireguard_server_private_key.key")" = 600 ] && [ "$(perm "$STATE_DIR/amnezia-awg2")" = 700 ] && jq -e "length == 2" "$STATE_DIR/amnezia-awg2/clientsTable" >/dev/null' "$(diff "$STATE_DIR/amnezia-awg2/awg0.conf" "$FX/fs/amnezia-awg2/opt/amnezia/awg/awg0.conf" | grep "I1 = " | cut -c1-40)"
# KEENETIC-I1: I1 по умолчанию приложения Amnezia (<r 2><b 0x…>) KeeneticOS 5.1 не принимает — подхват ставит универсальный
# тем же путём, что profile-set-param для клиентского параметра: строка «# I1» в конфиге контейнера, без рестарта; о нём
# больше не предупреждаем (предупреждения — по тому, что стало), в журнал — profile-i1-universal.
check "KEENETIC-I1 подхват: конфиг с универсальным I1 — в контейнер (docker cp), без рестарта, ipacket_warnings пуст, журнал profile-i1-universal" \
    'grep -qF "docker cp <stdin> amnezia-awg2:/opt/amnezia/awg/awg0.conf" "$ERR" && ! grep -q "docker restart" "$ERR" && [ "$(jget ".ipacket_warnings | length")" = 0 ] && audit_has profile-i1-universal "awg2 I1 legacy-default->universal"' \
    "$(grep DRY "$ERR" | grep -E "docker cp|restart" | tail -3) $(jget .ipacket_warnings) $(tail -2 "$GOAMNEZIA_AUDIT_LOG" 2>&1)"
check "users.json: iPhone_Anny и office-router — imported=true, label оригинальный, privkey пуст, psk из конфига, ip .1/.2, created из clientsTable" \
    '[ "$(jq -r ".users | length" "$USERS_FILE")" = 2 ] && [ "$(jq -r ".users[0].name" "$USERS_FILE")" = iPhone_Anny ] && [ "$(jq -r ".users[0].label" "$USERS_FILE")" = "iPhone Анны" ] && [ "$(jq -r ".users[0].imported" "$USERS_FILE")" = true ] && [ "$(jq -r ".users[0].peers.awg2.privkey" "$USERS_FILE")" = "" ] && [ "$(jq -r ".users[0].peers.awg2.psk" "$USERS_FILE")" = "$PSK2" ] && [ "$(jq -r ".users[0].peers.awg2.ip" "$USERS_FILE")" = 10.8.1.1 ] && [ "$(jq -r ".users[0].peers.awg2.imported" "$USERS_FILE")" = true ] && [ "$(jq -r ".users[0].created" "$USERS_FILE")" = 2026-08-30T09:00:00Z ] && [ "$(jq -r ".users[1].name" "$USERS_FILE")" = office-router ] && [ "$(jq -r ".users[1].peers.awg2.pubkey" "$USERS_FILE")" = "$P2" ]' "$(jq -c ".users | map({name, label: .label, imported, ip: .peers.awg2.ip})" "$USERS_FILE")"
run users_show iPhone_Anny
check "users_show iPhone_Anny: карточка без privkey/psk, imported виден" '[ "$rc" = 0 ] && [ "$(jget .imported)" = true ] && ! grep -q "privkey\|\"psk\"" <<<"$out" && [ "$(jget ".peers.awg2.imported")" = true ]' "$out"
# Признак для бота и панели: по этому пиру выдача ключа заведомо откажет, и
# кнопки выдачи показывать нельзя — вместо них перевыпуск (договор §пользователи).
check "users_show iPhone_Anny: у пира has_key=false — интерфейсам видно, что выдавать нечего" \
    '[ "$(jget ".peers.awg2.has_key")" = false ]' "$(jget ".peers.awg2")"
run export_conf iPhone_Anny awg2
check "export_conf для импортированного → no_private_key (rc 3), stdout пуст" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = no_private_key ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE"
check "в отказе сказано, где ключ и что делать: приложение, привязка, перевыпуск" \
    'grep -q "выдан в приложении Amnezia" "$ERR" && grep -q "Привяжите старый ключ" "$ERR" && grep -q "перевыпустите" "$ERR"' "$(errs | tail -2)"

# ── 2a. Привязка старого ключа ───────────────────────────────────────────────
# Приложение не хранит ключи людей (и в его бэкапе их нет): приватный ключ
# остаётся только в ссылке vpn:// или .conf, которые админ отправил человеку.
# Стенд: открытый ключ iPhone_Anny и office-router подменяем парами, приватную часть
# которых «отправляли», — так выглядит пир, подхваченный из приложения.
section "привязка старого ключа (users_attach_key)"
cp -p "$USERS_FILE" "$TMP/users.orig"
KP_A=$(python3 "$ROOT/lib/awgkeys.py" genkey); KP_A_PUB=$(python3 "$ROOT/lib/awgkeys.py" pubkey "$KP_A")
KP_B=$(python3 "$ROOT/lib/awgkeys.py" genkey); KP_B_PUB=$(python3 "$ROOT/lib/awgkeys.py" pubkey "$KP_B")
KP_X=$(python3 "$ROOT/lib/awgkeys.py" genkey); SRV_X=$(python3 "$ROOT/lib/awgkeys.py" pubkey "$(python3 "$ROOT/lib/awgkeys.py" genkey)")
jq --arg a "$KP_A_PUB" --arg b "$KP_B_PUB" '.users |= map(
    if .name == "iPhone_Anny" then .peers.awg2.pubkey = $a elif .name == "office-router" then .peers.awg2.pubkey = $b else . end)' \
    "$USERS_FILE" >"$TMP/users.swap" && cat "$TMP/users.swap" >"$USERS_FILE"
client_conf() { printf '[Interface]\r\nPrivateKey = %s\r\nAddress = 10.8.1.1/32\r\n\r\n[Peer]\r\nPublicKey = %s\r\nEndpoint = 203.0.113.10:38123\r\nAllowedIPs = 0.0.0.0/0\r\n' "$1" "$2"; }
USERS_BEFORE=$(cat "$USERS_FILE")
run users_attach_key iPhone_Anny "" ""
check "пусто → no_config (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = no_config ]' "rc=$rc code=$GA_ERR_CODE"
run users_attach_key iPhone_Anny "" "просто текст без ключа"
check "не ссылка и не .conf → config_unreadable (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = config_unreadable ]' "rc=$rc code=$GA_ERR_CODE"
run users_attach_key iPhone_Anny "" "$(client_conf "$KP_X" "$SRV_X")"
check "ключ от другого сервера → server_mismatch" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = server_mismatch ]' "rc=$rc code=$GA_ERR_CODE $(errs | tail -2)"
run users_attach_key iPhone_Anny "" "$(client_conf "$KP_X" "$SRV2_PUB")"
check "чужой ключ этого сервера → key_mismatch" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = key_mismatch ]' "rc=$rc code=$GA_ERR_CODE"
run users_attach_key iPhone_Anny "" "$(client_conf "$KP_B" "$SRV2_PUB")"
check "ключ office-router в карточке iPhone_Anny → key_mismatch с именем владельца" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = key_mismatch ] && grep -q "office-router" <<<"$GA_ERR_MSG"' "code=$GA_ERR_CODE msg=$GA_ERR_MSG"
check "отказы ничего не записали" '[ "$(cat "$USERS_FILE")" = "$USERS_BEFORE" ]'
run users_attach_key iPhone_Anny "" "Держи ключ:
$(client_conf "$KP_A" "$SRV2_PUB")
пиши, если что"
check "свой ключ (.conf с CRLF и подписью вокруг) → rc 0, профиль найден сам: awg2" \
    '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg2 ] && [ "$(jget .name)" = iPhone_Anny ] && [ "$(jget .line)" = 2.0 ]' "rc=$rc $out $(errs | tail -3)"
check "has_key стал true, ключ в users.json тот самый, права 600" \
    '[ "$(users_show iPhone_Anny | jq -r .peers.awg2.has_key)" = true ] && [ "$(jq -r ".users[] | select(.name==\"iPhone_Anny\") | .peers.awg2.privkey" "$USERS_FILE")" = "$KP_A" ] && [ "$(perm "$USERS_FILE")" = 600 ]'
# Мимо run: выданный .conf несёт PSK, а общий журнал прогона ниже проверяется
# на отсутствие секретов.
EXP_A=$(export_conf iPhone_Anny awg2 2>/dev/null)
check "теперь export_conf выдаёт ключ: PrivateKey тот самый" 'grep -qF "PrivateKey = $KP_A" <<<"$EXP_A"'
EXP_A=""
check "в журнал аудита — кто и какой профиль, ключа там нет" \
    'audit_has user-attach-key "name=iPhone_Anny profile=awg2" && ! grep -qF "$KP_A" "$GOAMNEZIA_AUDIT_LOG"'
check "контейнер не тронут: ни docker exec, ни записи конфига" '! grep -q "docker exec\|docker cp" "$ERR"' "$(grep DRY "$ERR" | head -3)"
run users_attach_key iPhone_Anny awg2 "$(client_conf "$KP_A" "$SRV2_PUB")"
check "повторно → already_has_key" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = already_has_key ]' "rc=$rc code=$GA_ERR_CODE"
# Ссылка vpn:// посреди сообщения — так её обычно и вставляют из переписки.
VPN_A=$(export_vpn iPhone_Anny awg2 2>/dev/null | jq -r '.vpn // empty' 2>/dev/null)
[ -n "$VPN_A" ] || VPN_A=$(export_vpn iPhone_Anny awg2 2>/dev/null)
jq '.users |= map(if .name == "iPhone_Anny" then .peers.awg2.privkey = "" else . end)' "$USERS_FILE" >"$TMP/users.swap" && cat "$TMP/users.swap" >"$USERS_FILE"
run users_attach_key iPhone_Anny "" "Анна, вот ссылка: $VPN_A — импортируй в AmneziaVPN"
check "ссылка vpn:// внутри сообщения → привязана (профиль awg2)" '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg2 ]' "rc=$rc code=$GA_ERR_CODE vpn=${VPN_A:0:20} $(errs | tail -2)"
run users_attach_key iPhone_Anny awg3 "$(client_conf "$KP_A" "$SRV2_PUB")"
check "профиль, которого нет → profile_not_installed" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = profile_not_installed ]' "rc=$rc code=$GA_ERR_CODE"
# TK-8: вставили серверный конфиг профиля (state/ или снимок подхвата) — это не «ключ от другого сервера».
run users_attach_key iPhone_Anny "" "$(cat "$STATE_DIR/amnezia-awg2/awg0.conf")"
check "TK-8 серверный awg0.conf → config_unreadable «это конфиг самого сервера», а не server_mismatch" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = config_unreadable ] && [ "$GA_ERR_MSG" = "$(t users_err_server_conf)" ]' "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
# TK-1: .conf из Блокнота Windows — с BOM в начале и CRLF.
jq '.users |= map(if .name == "iPhone_Anny" then .peers.awg2.privkey = "" else . end)' "$USERS_FILE" >"$TMP/users.swap" && cat "$TMP/users.swap" >"$USERS_FILE"
run users_attach_key iPhone_Anny "" "$(printf '\xEF\xBB\xBF')$(client_conf "$KP_A" "$SRV2_PUB")"
check "TK-1 .conf с BOM (Блокнот Windows) → привязан, а не config_unreadable" '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg2 ]' "rc=$rc code=$GA_ERR_CODE"
# TK-5: ключ этого же человека, но с другого его профиля (вкладка не та) — раньше «ключ iPhone_Anny, а не
# iPhone_Anny». Теперь он привязывается к профилю, чей он, и это сказано словами.
KP_C=$(python3 "$ROOT/lib/awgkeys.py" genkey); KP_C_PUB=$(printf '%s\n' "$KP_C" | python3 "$ROOT/lib/awgkeys.py" pubkey -)
jq --arg c "$KP_C_PUB" '.users |= map(if .name == "iPhone_Anny" then .peers.awg1 = (.peers.awg2 | .pubkey = $c | .privkey = "" | .ip = "10.8.9.2") else . end)' \
    "$USERS_FILE" >"$TMP/users.swap" && cat "$TMP/users.swap" >"$USERS_FILE"
run users_attach_key iPhone_Anny awg2 "$(client_conf "$KP_C" "$SRV2_PUB")"
check "TK-5 ключ с другого профиля того же человека → привязан к тому профилю (awg1), сказано словами" \
    '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg1 ] && grep -qF "$(tf users_key_other_profile iPhone_Anny "$(ga_amn_profile_label awg1)" "$(ga_amn_profile_label awg2)" "$(ga_amn_profile_label awg1)")" "$ERR" && grep -qF "AmneziaWG 2.0 · порт 38123 · из приложения" "$ERR" && [ "$(jq -r ".users[] | select(.name==\"iPhone_Anny\") | .peers.awg1.privkey" "$USERS_FILE")" = "$KP_C" ]' \
    "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
run users_attach_key iPhone_Anny awg2 "$(client_conf "$KP_C" "$SRV2_PUB")"
check "TK-5 тот же ключ ещё раз → already_has_key на awg1, без «ключ iPhone_Anny, а не iPhone_Anny»" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = already_has_key ] && grep -q awg1 <<<"$GA_ERR_MSG"' "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
KP_C=""
VPN_A=""
# Дальше сверка и серверный конфиг сравниваются с пирами фикстуры — возвращаем их.
cat "$TMP/users.orig" >"$USERS_FILE"
run amnezia_profile_status awg2
check "amnezia_profile_status awg2: state running (контейнер-фикстура), line 2.0, peers 2, port 38123" \
    '[ "$rc" = 0 ] && [ "$(jget .state)" = running ] && [ "$(jget .line)" = 2.0 ] && [ "$(jget .peers)" = 2 ] && [ "$(jget .port)" = 38123 ]' "$out"
run import_adopt amnezia-awg2
check "повторный подхват → already_managed" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = already_managed ]' "rc=$rc code=$GA_ERR_CODE"
run import_scan
check "import_scan после подхвата: amnezia-awg2 managed=true → awg2, adoptable=false reason managed, клиенты known=true" \
    '[ "$(jget ".[0].managed")" = true ] && [ "$(jget ".[0].profile")" = awg2 ] && [ "$(jget ".[0].reason")" = managed ] && [ "$(jget "[.[0].clients[].known] | all")" = true ]' "$(jget ".[0] | {managed, profile, reason}")"
check "аудит: import-adopt amnezia-awg2 profile=awg2 users=2" 'audit_has import-adopt "amnezia-awg2 profile=awg2 line=2.0 users=2"' "$(tail -1 "$GOAMNEZIA_AUDIT_LOG")"

# ── 3. Legacy amnezia-awg → awg1 ─────────────────────────────────────────────
section "import_adopt amnezia-awg (legacy 1.x)"
run import_adopt amnezia-awg
check "import_adopt amnezia-awg: rc=0, profile awg1, line 1.0, iface wg0, port 51820, users_added 1" \
    '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg1 ] && [ "$(jget .line)" = 1.0 ] && [ "$(jget .iface)" = wg0 ] && [ "$(jget .port)" = 51820 ] && [ "$(jget .users_added)" = 1 ]' "rc=$rc code=$GA_ERR_CODE $(errs | tail -3)"
isol_exec="docker exec -i amnezia-awg sh -c '$(ga_amn_isolation_cmd wg0 on)'"
check "SEC-NET-1 legacy-контейнер приложения: изоляция клиентов на wg0, start.sh приложения не тронут" \
    'grep -qF "$isol_exec" "$ERR" && ! grep -qF "amnezia-awg:/opt/amnezia/start.sh" "$ERR"' "$(grep DRY "$ERR" | tail -3)"
check "config.json awg1: container amnezia-awg (не amnezia-awg1), image amneziavpn/amnezia-wg, subnet 10.8.5.0/24" \
    '[ "$(jq -r .profiles.awg1.container "$CONFIG_FILE")" = amnezia-awg ] && [ "$(jq -r .profiles.awg1.image "$CONFIG_FILE")" = amneziavpn/amnezia-wg ] && [ "$(jq -r .profiles.awg1.subnet "$CONFIG_FILE")" = 10.8.5.0/24 ] && [ "$(jq -r .profiles.awg1.iface "$CONFIG_FILE")" = wg0 ]' "$(jq -c ".profiles.awg1 | del(.params)" "$CONFIG_FILE")"
check "помощники профиля: iface wg0, утилита wg, путь /opt/amnezia/awg/wg0.conf; для awg2 — awg0/awg" \
    '[ "$(ga_amn_iface awg1)" = wg0 ] && [ "$(ga_amn_tool awg1)" = wg ] && [ "$(ga_amn_conf_path awg1)" = /opt/amnezia/awg/wg0.conf ] && [ "$(ga_amn_iface awg2)" = awg0 ] && [ "$(ga_amn_tool awg2)" = awg ]'
check "пользователь Keenetic: имя из «Keenetic дома», label с кириллицей, imported, ip 10.8.5.1" \
    '[ "$(jq -r ".users[] | select(.peers.awg1 != null) | .name" "$USERS_FILE")" = Keenetic_doma ] && [ "$(jq -r ".users[] | select(.name == \"Keenetic_doma\") | .label" "$USERS_FILE")" = "Keenetic дома" ] && [ "$(jq -r ".users[] | select(.name == \"Keenetic_doma\") | .peers.awg1.ip" "$USERS_FILE")" = 10.8.5.1 ]' "$(jq -c ".users | map({name, label: .label})" "$USERS_FILE")"
check "ga_amn_image_key: awg1 → amnezia-wg (legacy), awg2 → amneziawg-go" '[ "$(ga_amn_image_key awg1)" = amnezia-wg ] && [ "$(ga_amn_image_key awg2)" = amneziawg-go ]'
run amnezia_show awg1
check "amnezia_show awg1 (dry): DRY docker exec wg show wg0 dump" '[ "$rc" = 0 ] && grep -q "wg show wg0 dump" "$ERR"' "$(errs)"

# ── 4. Серверный конфиг из users.json совпадает по пирам ─────────────────────
section "конфиг из users.json после подхвата"
run amnezia_write_server_conf awg2
check "amnezia_write_server_conf awg2: те же два пира (pubkey, ip) в state/awg0.conf; параметры 2.0 на месте; DRY cp в /opt/amnezia/awg/awg0.conf" \
    '[ "$rc" = 0 ] && [ "$(grep -c "^\[Peer\]" "$STATE_DIR/amnezia-awg2/awg0.conf")" = 2 ] && grep -qF "PublicKey = $P1" "$STATE_DIR/amnezia-awg2/awg0.conf" && grep -q "^AllowedIPs = 10.8.1.2/32$" "$STATE_DIR/amnezia-awg2/awg0.conf" && grep -q "^S3 = 12$" "$STATE_DIR/amnezia-awg2/awg0.conf" && grep -q "^H1 = 1000000-1004000$" "$STATE_DIR/amnezia-awg2/awg0.conf" && grep -q "amnezia-awg2:/opt/amnezia/awg/awg0.conf" "$ERR"' "$(errs) $(cat "$STATE_DIR/amnezia-awg2/awg0.conf")"
run amnezia_write_server_conf awg1
check "amnezia_write_server_conf awg1: DRY cp в /opt/amnezia/awg/wg0.conf, пир Keenetic" \
    '[ "$rc" = 0 ] && grep -q "amnezia-awg:/opt/amnezia/awg/wg0.conf" "$ERR" && grep -qF "PublicKey = $Q1" "$STATE_DIR/amnezia-awg/awg0.conf"' "$(errs)"
run amnezia_syncconf awg1
check "amnezia_syncconf awg1: wg syncconf wg0 <(wg-quick strip …wg0.conf)" 'grep -q "wg syncconf wg0 <(wg-quick strip /opt/amnezia/awg/wg0.conf)" "$ERR"' "$(errs)"
# Шестой круг: смена подсети у подхваченного legacy-профиля на wg0 пересоздавала контейнер с нашим start.sh
# (он знает только awg0) — профиль не поднимался, отката не было. Теперь отказ до всякого изменения.
run amnezia_profile_set_subnet awg1 10.8.9.0/24
check "legacy-профиль на wg0: смена подсети — отказ subnet_legacy, подсеть прежняя, контейнер не пересоздавался" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = subnet_legacy ] && [ "$(jq -r .profiles.awg1.subnet "$CONFIG_FILE")" = 10.8.5.0/24 ] && ! grep -qE "docker (rm|run)" "$ERR"' "rc=$rc code=$GA_ERR_CODE $(errs | tail -3)"

# Регрессия стенда: ключи пиров контейнера сверяются с users.json, и обрезанный
# хвостовой «=» делал СВОИХ пиров чужими — предупреждение «подхватите его через
# import-adopt» вылезало на каждой записи конфига и на удалении своего же пира.
# Маркер — текст предупреждения о чужом пире: с 25.09.2026 он советует import-reconcile (T15).
check "свои пиры не объявляются чужими при записи конфига" \
    '! grep -qE "import-(adopt|reconcile)" "$ERR"' "$(errs)"

# ── 5. Сверка: приложение добавило пира ──────────────────────────────────────
section "import_reconcile: новый пир в контейнере"
run import_reconcile awg2
check "сразу после подхвата: in_sync=true, new=[], gone=[], applied=false" '[ "$rc" = 0 ] && [ "$(jget .in_sync)" = true ] && [ "$(jget ".new|length")" = 0 ] && [ "$(jget ".gone|length")" = 0 ] && [ "$(jget .applied)" = false ]' "$out"
cp "$FX/awg2_conf_with_third_peer.txt" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
cp "$FX/awg2_clients_with_third_peer.json" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/clientsTable"
run import_reconcile awg2
check "третий пир появился: in_sync=false, new=[{Laptop, 10.8.1.3}], gone=[], без записи (applied=false, users.json прежний)" \
    '[ "$rc" = 0 ] && [ "$(jget .in_sync)" = false ] && [ "$(jget ".new|length")" = 1 ] && [ "$(jget ".new[0].name")" = Laptop ] && [ "$(jget ".new[0].ip")" = 10.8.1.3 ] && [ "$(jget ".new[0].pubkey")" = "$P3" ] && [ "$(jget .applied)" = false ] && [ "$(jq -r ".users|length" "$USERS_FILE")" = 3 ]' "$out"
check "сверка без apply не печатает секретов (PSK)" '! grep -qF "$PSK2" <<<"$out"'
run import_reconcile awg2 apply
check "apply: added=1, пользователь Laptop imported с psk и без privkey; in_sync после apply" \
    '[ "$rc" = 0 ] && [ "$(jget .applied)" = true ] && [ "$(jget .added)" = 1 ] && [ "$(jget ".users[0]")" = Laptop ] && [ "$(jq -r ".users[] | select(.name == \"Laptop\") | .peers.awg2.psk" "$USERS_FILE")" = "$PSK2" ] && [ "$(jq -r ".users[] | select(.name == \"Laptop\") | .peers.awg2.privkey" "$USERS_FILE")" = "" ] && [ "$(jq -r ".users[] | select(.name == \"Laptop\") | .imported" "$USERS_FILE")" = true ]' "$out $(errs | tail -2)"
run import_reconcile awg2
check "повторная сверка: in_sync=true" '[ "$(jget .in_sync)" = true ]' "$out"
check "аудит: import-reconcile awg2 added=1 deactivated=0" 'audit_has import-reconcile "awg2 added=1 deactivated=0"' "$(tail -2 "$GOAMNEZIA_AUDIT_LOG")"

section "import_reconcile: приложение удалило пиров"
cp "$FX/awg2_conf_without_second_peer.txt" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
run import_reconcile awg2
check "office-router и Laptop пропали из контейнера: gone=2, new=0" '[ "$(jget .in_sync)" = false ] && [ "$(jget ".gone|length")" = 2 ] && [ "$(jget "[.gone[].name] | sort | join(\",\")")" = "Laptop,office-router" ] && [ "$(jget ".new|length")" = 0 ]' "$out"
run import_reconcile awg2 apply
check "apply: deactivated=2, у обоих peers.awg2.active=false, запись и enabled остались" \
    '[ "$rc" = 0 ] && [ "$(jget .deactivated)" = 2 ] && [ "$(jq -r ".users[] | select(.name == \"office-router\") | .peers.awg2.active" "$USERS_FILE")" = false ] && [ "$(jq -r ".users[] | select(.name == \"Laptop\") | .peers.awg2.active" "$USERS_FILE")" = false ] && [ "$(jq -r ".users[] | select(.name == \"office-router\") | .enabled" "$USERS_FILE")" = true ] && [ "$(jq -r ".users|length" "$USERS_FILE")" = 4 ]' "$out"
run import_reconcile awg2
check "после apply контейнер и users.json в согласии" '[ "$(jget .in_sync)" = true ]' "$out"
run amnezia_profile_status awg2
check "amnezia_profile_status awg2: активных пиров 1" '[ "$(jget .peers)" = 1 ]' "$out"

# ── 5a. ACC-2: перенос порта профиля с людьми приложения без ключа ───────────
# Смена режима на «Сайт и VPN на 443» из SSH молча уводила на UDP/443 профиль awg2 — а на сервере из
# приложения это и есть контейнер приложения: ключи людей выдало приложение, на сервере их нет, и после
# переноса переслать им новые конфиги нечем. Теперь модуль без согласия такой перенос не делает, а меню
# ставит на 443 свой профиль, если он есть, иначе объясняет и просит набрать имя профиля.
section "ACC-2: перенос порта профиля с людьми приложения без ключа"
awg2_port=$(jq -r .profiles.awg2.port "$CONFIG_FILE")
keyless2=$(ga_profile_keyless_count awg2)
check "ACC-2 людей приложения без ключа на awg2 — iPhone_Anny (убранных приложением не считаем)" '[ "$keyless2" = 1 ]' "keyless=$keyless2"
run amnezia_profile_set_port awg2 40555
check "ACC-2 перенос без согласия: rc=2, adopted_keyless, в тексте — сколько людей и почему им не переслать; порт и контейнер прежние" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = adopted_keyless ] && [ "$GA_ERR_MSG" = "$(tf amnezia_err_port_adopted awg2 1 "$awg2_port" 40555 awg2)" ] && [ "$(jq -r .profiles.awg2.port "$CONFIG_FILE")" = "$awg2_port" ] && ! grep -qE "docker (rm|run)" "$ERR"' \
    "rc=$rc code=$GA_ERR_CODE $(errs | tail -2)"
run amnezia_profile_set_port awg2 40555 awg3
check "ACC-2 согласие именем другого профиля не считается" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = adopted_keyless ]' "rc=$rc code=$GA_ERR_CODE"
run amnezia_profile_set_port awg2 40555 awg2
check "ACC-2 с согласием (имя профиля) — перенос выполнен" '[ "$rc" = 0 ] && [ "$(jq -r .profiles.awg2.port "$CONFIG_FILE")" = 40555 ]' "rc=$rc $(errs | tail -3)"
run amnezia_profile_set_port awg2 "$awg2_port" awg2
check "ACC-2 и обратно" '[ "$rc" = 0 ] && [ "$(jq -r .profiles.awg2.port "$CONFIG_FILE")" = "$awg2_port" ]' "rc=$rc $(errs | tail -3)"
# SSH-меню «Режим и сайт → Сайт и VPN на 443»: install.sh как библиотека (без последней строки main "$@").
# Вопросы с терминала подменены: confirm — «да», confirm_phrase — набрано то, что в SVP_TYPED.
mkdir -p "$TMP/menu" && ln -sfn "$ROOT/lib" "$TMP/menu/lib" && sed '$d' "$ROOT/install.sh" >"$TMP/menu/install.sh"
# Текст меню переносится по словам (_ui_w): сравниваем, склеив пробелы и переводы строк.
flat() { tr -s '[:space:]' ' '; }
svp_plan() {
    SVP_TYPED="${1:-}" "${BASH5:-$BASH}" -c 'source "$1" >/dev/null 2>&1 || exit 9
        confirm() { return 0; }
        confirm_phrase() { [ "${SVP_TYPED:-}" = "$1" ]; }
        _site_vpn_port_plan pro; echo "rc=$? P=$_SVP_P WANT=$_SVP_WANT CONFIRM=$_SVP_CONFIRM"' _ "$TMP/menu/install.sh" \
        2>"$TMP/svp.err" </dev/null
}
# NB2-2: отказ — «нет» на вопросе y/n или не та фраза (заглушка печатает «Отменено.», как настоящий confirm_phrase
# после LH-3). Об отказе — ровно одна строка «Отменено.»: план говорит сам, вызывающие молчат.
# Второй аргумент — _SVP_CANCEL_KEY (RB3-3: при Lite → Pro сайт уже поднят — отменён только перенос порта).
svp_plan_no() {
    SVP_TYPED="${1:-}" SVP_KEY="${2:-}" "${BASH5:-$BASH}" -c 'source "$1" >/dev/null 2>&1 || exit 9
        confirm() { return 1; }
        confirm_phrase() { [ "${SVP_TYPED:-}" = "$1" ] || { log_info "$(t cancelled)"; return 1; }; }
        _SVP_CANCEL_KEY="${SVP_KEY:-}" _site_vpn_port_plan pro; echo "rc=$? P=$_SVP_P WANT=$_SVP_WANT"' _ "$TMP/menu/install.sh" \
        2>"$TMP/svp.err" </dev/null
}
n_cancelled() { grep -cF "$(t cancelled)" "$TMP/svp.err"; }
check "NB2-2 вызывающие _site_vpn_port_plan своё «Отменено.» не печатают" \
    '! grep -nE "_site_vpn_port_plan .*t cancelled" "$ROOT/install.sh"' "$(grep -nE '_site_vpn_port_plan .*t cancelled' "$ROOT/install.sh")"
check "ACC-2 оба установленных профиля подхвачены у приложения с пользователями без ключа (awg1, awg2), awg3 не стоит" \
    '[ "$(ga_profile_keyless_count awg1)" -gt 0 ] && [ "$(ga_profile_keyless_count awg2)" -gt 0 ] && ! ga_profile_installed awg3'
svp=$(svp_plan "")
check "ACC-2 меню, своих профилей нет: объяснение (сколько людей, почему не переслать), имя не набрано — отказ, переносить нечего" \
    '[[ "$svp" == "rc=1 P= WANT= CONFIRM=" ]] && flat <"$TMP/svp.err" | grep -qF "$(tf pr_port_adopted_warn "AmneziaWG 2.0 · порт $awg2_port · из приложения" 1 "$awg2_port" 443 | flat)"' "$svp $(tail -4 "$TMP/svp.err")"
svp=$(svp_plan "$awg2_port")
check "ACC-2 меню: набран порт профиля (D-5) — awg2 на 443 с согласием для модуля" '[[ "$svp" == "rc=0 P=awg2 WANT=443 CONFIRM=awg2" ]]' "$svp $(tail -3 "$TMP/svp.err")"
# Профиль, у всех людей которого ключи на сервере (как после привязки старых ключей), — на 443 едет он, а
# awg2 с людьми приложения без ключа остаётся на месте, и сказано почему.
cp "$USERS_FILE" "$TMP/users.acc2"
jq '.users |= map(if .peers.awg1 != null then .peers.awg1.privkey = "stand-attached-key" else . end)' "$TMP/users.acc2" >"$USERS_FILE"
svp=$(svp_plan "")
check "ACC-2 меню: есть профиль без людей приложения без ключа (awg1) — на 443 едет он, обычный вопрос, awg2 не трогаем" \
    '[[ "$svp" == "rc=0 P=awg1 WANT=443 CONFIRM=" ]] && flat <"$TMP/svp.err" | grep -qF "$(tf ms_vpn_443_native "AmneziaWG 1.0 · порт 51820 · из приложения" "AmneziaWG 2.0 · порт $awg2_port · из приложения" | flat)"' "$svp $(tail -3 "$TMP/svp.err")"
svp=$(svp_plan_no "")
check "NB2-2 «нет» на вопросе о переносе — rc=1 и ровно одна строка «Отменено.»" \
    '[[ "$svp" == rc=1* ]] && [ "$(n_cancelled)" = 1 ]' "$svp n=$(n_cancelled) $(tail -3 "$TMP/svp.err")"
svp=$(svp_plan_no "" ms_port_move_cancelled)
check "RB3-3 Lite → Pro: «нет» на вопросе о переносе — без «Отменено.», одна строка «перенос порта отменён»" \
    '[[ "$svp" == rc=1* ]] && [ "$(n_cancelled)" = 0 ] && [ "$(grep -cF "$(t ms_port_move_cancelled)" "$TMP/svp.err")" = 1 ]' \
    "$svp n=$(n_cancelled) $(tail -3 "$TMP/svp.err")"
check "RB3-3 переход из Lite зовёт план со своей строкой отказа" \
    'grep -qE "_SVP_CANCEL_KEY=ms_port_move_cancelled _site_vpn_port_plan" "$ROOT/install.sh"'
cp "$TMP/users.acc2" "$USERS_FILE"
svp=$(svp_plan_no "")
check "NB2-2 не та фраза (профиль из приложения) — rc=1 и ровно одна строка «Отменено.»" \
    '[[ "$svp" == "rc=1 P= WANT=" ]] && [ "$(n_cancelled)" = 1 ]' "$svp n=$(n_cancelled) $(tail -3 "$TMP/svp.err")"
# RB4-1: тот же путь с фразой при Lite → Pro — настоящий confirm_phrase (без терминала ответ пуст) говорит строкой
# плана «перенос порта отменён», а не «Отменено.»; вне перехода — прежнее «Отменено.».
svp_plan_phrase() {
    SVP_KEY="${1:-}" "${BASH5:-$BASH}" -c 'source "$1" >/dev/null 2>&1 || exit 9
        confirm() { return 1; }
        _SVP_CANCEL_KEY="${SVP_KEY:-}" _site_vpn_port_plan pro; echo "rc=$? P=$_SVP_P WANT=$_SVP_WANT"' _ "$TMP/menu/install.sh" \
        2>"$TMP/svp.err" </dev/null
}
svp=$(svp_plan_phrase ms_port_move_cancelled)
check "RB4-1 Lite → Pro, путь с фразой: отказ — одна строка «перенос порта VPN отменён», без «Отменено.»" \
    '[[ "$svp" == "rc=1 P= WANT=" ]] && [ "$(n_cancelled)" = 0 ] && [ "$(grep -cF "$(t ms_port_move_cancelled)" "$TMP/svp.err")" = 1 ]' \
    "$svp n=$(n_cancelled) $(tail -3 "$TMP/svp.err")"
svp=$(svp_plan_phrase "")
check "RB4-1 вне перехода путь с фразой — прежнее «Отменено.» одной строкой" \
    '[[ "$svp" == "rc=1 P= WANT=" ]] && [ "$(n_cancelled)" = 1 ] && ! grep -qF "$(t ms_port_move_cancelled)" "$TMP/svp.err"' \
    "$svp n=$(n_cancelled) $(tail -3 "$TMP/svp.err")"

# ── 6. Ошибки ────────────────────────────────────────────────────────────────
section "ошибки"
# Дефект 1a.17: подсеть контейнера разбиралась своей регуляркой по четыре цифры
# на октет (CONTRACT §0.8 запрещает именно этот образец) — «999.999.999.0/24»
# проходило как подсеть и уезжало в config.json профиля.
check "в import.sh нет своей регулярки под IPv4 (только _valid_ip)" \
    '! grep -nE "^[^#]*\[0-9\]\{1,3\}" "$ROOT/lib/import.sh"' "$(grep -nE '^[^#]*\[0-9\]\{1,3\}' "$ROOT/lib/import.sh" | head -3)"
check "ga_import_network: 10.8.1.1/24 → сеть; 999.х, ведущий ноль, /33 и адрес без маски — отказ" \
    '[ "$(ga_import_network 10.8.1.1/24)" = 10.8.1.0/24 ] && [ "$(ga_import_network 10.8.5.0/24)" = 10.8.5.0/24 ] && ! ga_import_network 999.999.999.0/24 && ! ga_import_network 010.8.1.0/24 && ! ga_import_network 10.8.1.0 && ! ga_import_network 10.8.1.0/33 && ! ga_import_network ""' \
    "999→$(ga_import_network 999.999.999.0/24 2>&1)"
run import_adopt "nginx-proxy"
check "имя без amnezia- → invalid_container (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_container ]' "rc=$rc code=$GA_ERR_CODE"
run import_adopt amnezia-nope
check "неизвестный контейнер → container_not_found (rc 3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = container_not_found ]' "rc=$rc code=$GA_ERR_CODE"
run import_adopt amnezia-xray
check "amnezia-xray → unsupported_container с причиной из словаря" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = unsupported_container ] && grep -q "AmneziaWG" "$ERR"' "rc=$rc code=$GA_ERR_CODE $(errs)"
run import_reconcile awg3
check "сверка неустановленного профиля → profile_not_installed" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = profile_not_installed ]' "rc=$rc code=$GA_ERR_CODE"
mkdir -p "$TMP/cfs_empty"
GOAMNEZIA_CONTAINER_FS_DIR="$TMP/cfs_empty" run import_reconcile awg2
check "контейнер исчез → container_missing" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = container_missing ]' "rc=$rc code=$GA_ERR_CODE"
# Профиль awg2 наш, а приложение поставило бы второй контейнер с тем же именем —
# невозможно физически; проверяем зато конфликт слота: подхват в занятый профиль.
run amnezia_profile_remove awg1
check "профиль awg1 удалён (для проверки конфликта подсети)" '[ "$rc" = 0 ]' "$(errs)"
run amnezia_profile_install awg3 40003 default
check "профиль awg3 наш установлен на 10.8.3.0/24" '[ "$rc" = 0 ]'
ga_config_merge '{"profiles":{"awg3":{"subnet":"10.8.5.0/24"}}}' >/dev/null 2>&1
run import_adopt amnezia-awg
# SUBNET-01: совпавшая подсеть больше не отказ — контейнер приложения берётся как есть (люди ходят, как ходили), а в
# ответе — с кем совпала и выход: перенести наш awg3 (ему подсеть сменить можно, людей приложения на нём нет) на
# свободную; старый amnezia-awg на wg0 не трогаем (fixed).
check "SUBNET-01 подсеть контейнера совпала с нашим профилем → подхват как есть; subnet_overlap: с awg3, выход — awg3 на свободную 10.8.3.0/24" \
    '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg1 ] && [ "$(jget .subnet)" = 10.8.5.0/24 ] && [ "$(jget ".subnet_overlap.with | join(\",\")")" = awg3 ] && [ "$(jget .subnet_overlap.fixed)" = true ] && [ "$(jget .subnet_overlap.move.profile)" = awg3 ] && [ "$(jget .subnet_overlap.move.subnet)" = 10.8.3.0/24 ] && [ "$(jget .subnet_overlap.move.keyless)" = 0 ]' \
    "rc=$rc code=$GA_ERR_CODE $(jget .subnet_overlap) $(errs | tail -2)"

# ── 5b. KMOD-02: подхват с тегами I1–I5, которые понимает не каждая реализация ──
# I-пакеты шлёт клиент. Тег, которого не знает одна из реализаций (<c> — amneziawg-go, <d> <ds> <dz> — модуль ядра),
# — не повод отказать в подхвате: люди, которые подключаются сейчас, остались бы без сервиса. Подхватываем и говорим
# словами. Отрицательный размер, незнакомый тег, пакет больше 1280 байт — отказ, контейнер не тронут.
section "KMOD-02: подхват с <c> или <d> — подхвачен с предупреждением; плохой I — отказ"
kmod_reset() {   # kmod_reset <I1> — профилей и людей нет, у amnezia-awg2 приложения в конфиге «# I1 = <I1>»
    printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"
    ga_config_set '.profiles' '{}' >/dev/null 2>&1
    rm -rf "$TMP/cfs"; mkdir -p "$TMP/cfs"; cp -R "$FX/fs/." "$TMP/cfs/"
    export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/cfs"
    sed "s|^# I1 = .*|# I1 = $1|" "$FX/fs/amnezia-awg2/opt/amnezia/awg/awg0.conf" \
        >"$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
}
for _c in "<b 0x01><c><r 5>|<c>|только модуль ядра AmneziaWG|клиенты AmneziaVPN на телефонах" \
          "<b 0x01><d><r 5>|<d>|только программа amneziawg-go|роутеры и Linux с модулем ядра"; do
    IFS='|' read -r _i1 _tag _who _whom <<<"$_c"
    kmod_reset "$_i1"
    run import_adopt amnezia-awg2
    check "I1 = $_i1: подхвачен (rc=0, профиль awg2, I1 сохранён как есть)" \
        '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg2 ] && [ "$(jq -r .profiles.awg2.params.I1 "$CONFIG_FILE")" = "$_i1" ]' \
        "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
    check "… предупреждение словами в ответе (ipacket_warnings) и в SSH: $_tag — $_who, кто не подключится, где заменить" \
        '[ "$(jget ".ipacket_warnings | length")" = 1 ] && w=$(jget ".ipacket_warnings[0]") && grep -qF "I1 содержит $_tag" <<<"$w" && grep -qF "$_who" <<<"$w" && grep -qF "$_whom" <<<"$w" && grep -qF "Замените I1 в «Изменить один параметр» карточки профиля (в панели — «Параметры маскировки» → «Изменить параметр»)." <<<"$w" && grep -qF "I1 содержит $_tag" "$ERR"' \
        "$(jget .ipacket_warnings) | $(errs | tail -2)"
done
kmod_reset "<b 0x0102><r 2>"
run import_adopt amnezia-awg2
check "общие теги, пакет начинается с <b> — подхвачен без предупреждений (ipacket_warnings пуст)" \
    '[ "$rc" = 0 ] && [ "$(jget ".ipacket_warnings | length")" = 0 ]' "rc=$rc $(jget .ipacket_warnings)"
# KEENETIC-I1: свой I1 человека, который начинается не с <b>, — не отказ и не замена, а предупреждение словами.
kmod_reset "<r 2><b 0x0102>"
run import_adopt amnezia-awg2
check "KEENETIC-I1: I1 = <r 2><b 0x0102> (свой, не прежнее умолчание) — подхвачен, I1 как есть, предупреждение про Keenetic; где заменить — «Изменить один параметр» (Q4), как в строке о пропуске" \
    '[ "$rc" = 0 ] && [ "$(jq -r .profiles.awg2.params.I1 "$CONFIG_FILE")" = "<r 2><b 0x0102>" ] && [ "$(jget ".ipacket_warnings | length")" = 1 ] && w=$(jget ".ipacket_warnings[0]") && grep -qF "I1 начинается с <r>" <<<"$w" && grep -qF "Keenetic (KeeneticOS 5.1)" <<<"$w" && grep -qF "замените I1 в «Изменить один параметр» карточки профиля (в панели — «Параметры маскировки» → «Изменить параметр»): начните с <b 0x…>" <<<"$w" && grep -qF "I1 начинается с <r>" "$ERR"' \
    "rc=$rc $(jget .ipacket_warnings) I1=$(jq -r .profiles.awg2.params.I1 "$CONFIG_FILE")"
# KEENETIC-I1-NITS (S4): эти три строки — ipacket_warnings ответа подхвата — доходят до панели и бота. В SSH и боте
# пункт «Изменить один параметр» есть, на карточке панели — нет: там раздел «Параметры маскировки» и в нём кнопка
# «Изменить параметр». Строки называют и путь в панели — теми же подписями, что в app.js (prParams, prEditParam; ru — первый
# словарь, en — второй).
_s4_lbl() { grep -oE "$1: \"[^\"]+\"" "$ROOT/admin-web/static/app.js" | sed -n "${2}p" | cut -d'"' -f2; }
_s4_ru="(в панели — «$(_s4_lbl prParams 1)» → «$(_s4_lbl prEditParam 1)»)"
_s4_en="(in the panel — «$(_s4_lbl prParams 2)» → «$(_s4_lbl prEditParam 2)»)"
_s4_ok() {
    local k
    for k in kernel_only program_only b_first; do
        grep "^I18N\[amnezia_warn_ipacket_$k\]=" "$ROOT/lib/lang/ru.d/amnezia.sh" | grep -qF "карточки профиля $_s4_ru" || return 1
        grep "^I18N\[amnezia_warn_ipacket_$k\]=" "$ROOT/lib/lang/en.d/amnezia.sh" | grep -qF "on the profile card $_s4_en" || return 1
    done
}
check "S4: строки ipacket (kernel_only, program_only, b_first; ru и en) называют путь в панели подписями app.js: $_s4_ru, $_s4_en" \
    '[ "$(_s4_lbl prParams 1)" = "Параметры маскировки" ] && [ "$(_s4_lbl prEditParam 1)" = "Изменить параметр" ] && [ "$(_s4_lbl prParams 2)" = "Obfuscation parameters" ] && [ "$(_s4_lbl prEditParam 2)" = "Change a parameter" ] && _s4_ok' \
    "ru=$_s4_ru en=$_s4_en"
# KEENETIC-I1-N (N5): Keenetic 5.1 ключи 3.1 не берёт при любом I1 (B-4) — совет «чтобы подходили и Keenetic» уточняет
# «ключи 1.5 и 2.0», иначе для профиля 3.1 он вёл в тупик. Без уточнения в ru или en — провал.
_n5_ok() {
    local k
    for k in amnezia_warn_ipacket_b_first amnezia_i1u_skip; do
        grep "^I18N\[$k\]=" "$ROOT/lib/lang/ru.d/amnezia.sh" | grep -qF "Чтобы подходили и Keenetic (ключи 1.5 и 2.0), " || return 1
        grep "^I18N\[$k\]=" "$ROOT/lib/lang/en.d/amnezia.sh" | grep -qF "To make keys fit Keenetic too (1.5 and 2.0 keys), " || return 1
    done
    ! grep -n "подходили и Keenetic" "$ROOT"/lib/lang/*/amnezia.sh | grep -vF "(ключи 1.5 и 2.0)" | grep -q .
}
check "N5: b_first и i1u_skip (ru и en) — «Чтобы подходили и Keenetic (ключи 1.5 и 2.0)», без уточнения строк нет" '_n5_ok' \
    "$(grep -n "подходили и Keenetic\|fit Keenetic too" "$ROOT"/lib/lang/*/amnezia.sh | cut -c1-120)"
# KEENETIC-I1-R (R1): подхват на модуле ядра — I1 прежнего умолчания в конфиге контейнера с «#» (как у приложения) и без
# него (ручная правка, чужая сборка). Перевод в универсальный — syncconf через фильтр: без рестарта, пиры те же.
I1_LEG='<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>'
_peers() { awk '/^\[Peer\]/ { p = 1 } p && /^(PublicKey|PresharedKey|AllowedIPs)/ { gsub(/[[:space:]]/, ""); print }' "$1" | LC_ALL=C sort; }
for _m in commented uncommented; do
    kmod_reset "$I1_LEG"
    _a2="$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
    if [ "$_m" = uncommented ]; then sed 's/^# I1 = /I1 = /' "$_a2" >"$TMP/a2.unc" && cp "$TMP/a2.unc" "$_a2"; fi
    printf '4: awg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280\n    amneziawg addrgenmode none\nDEVTYPE=amneziawg\nINTERFACE=awg0\n' \
        >"$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/.iplink"
    GA_AMN_MASK_DIRTY=""
    run import_adopt amnezia-awg2
    check "R1 подхват на модуле ядра, I1 прежнего умолчания ($_m): универсальный, syncconf через фильтр, рестарта нет, слов о перезапуске нет" \
        '[ "$rc" = 0 ] && [[ "$(jq -r .profiles.awg2.params.I1 "$CONFIG_FILE")" =~ $I1U_RE ]] && grep -qF "awg syncconf awg0 <(awg-quick strip /opt/amnezia/awg/awg0.conf | awk " "$ERR" && ! grep -q "docker restart" "$ERR" && ! grep -qF "новая маскировка применяется перезапуском" "$ERR" && audit_has profile-i1-universal "awg2 I1 legacy-default->universal"' \
        "rc=$rc code=$GA_ERR_CODE $(grep DRY "$ERR" | grep -E "syncconf|restart" | tail -3)"
    check "… ($_m) пиры в новом конфиге те же, что у приложения; строка I1 одна и с «#»" \
        '[ -n "$(_peers "$_a2")" ] && [ "$(_peers "$_a2")" = "$(_peers "$STATE_DIR/amnezia-awg2/awg0.conf")" ] && [ "$(grep -c "I1 = " "$STATE_DIR/amnezia-awg2/awg0.conf")" = 1 ] && grep -q "^# I1 = <b 0x" "$STATE_DIR/amnezia-awg2/awg0.conf"' \
        "$(diff <(_peers "$_a2") <(_peers "$STATE_DIR/amnezia-awg2/awg0.conf") | head -4) $(grep -n "I1 = " "$STATE_DIR/amnezia-awg2/awg0.conf" | cut -c1-24)"
done
for _c in "<x 1>|тег <x> не знают" "<rc 1000><rd 281>|1281 байт" "<rc -5>|положительным числом"; do
    IFS='|' read -r _i1 _why <<<"$_c"
    kmod_reset "$_i1"
    run import_adopt amnezia-awg2
    check "I1 = $_i1 → отказ params_invalid словами, профиля нет, контейнер не тронут" \
        '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = params_invalid ] && grep -qF "$_why" <<<"$GA_ERR_MSG" && grep -qF "amnezia-awg2" <<<"$GA_ERR_MSG" && [ "$(jq -c ".profiles.awg2 // null" "$CONFIG_FILE")" = null ] && ! grep -q "docker cp\|docker exec -d" "$ERR"' \
        "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
done

# ── 5c. 1.0.13: подхват с тем, что продукт сам так не записал бы ─────────────
# Пределы того, что пишем мы (живые проверки 08.10: PARAM-03, 05, 08, 09) — у чужого работающего конфига не отказ, а
# предупреждение словами: люди, которые подключаются сейчас, не должны остаться без сервиса. Значения — как в контейнере.
section "1.0.13: подхват с таймингом вне коридора, всплеском мусора, хвостами при разных S и широких H — с предупреждением"
kmod_reset "<r 2>"
awk '{
       if ($0 ~ /^RekeyTimeout = /) $0 = "RekeyTimeout = 0-7"
       else if ($0 ~ /^Jc = /) $0 = "Jc = 122"
       else if ($0 ~ /^Jmin = /) $0 = "Jmin = 1200"
       else if ($0 ~ /^Jmax = /) $0 = "Jmax = 1280"
       else if ($0 ~ /^S1 = /) $0 = "S1 = 40"
       else if ($0 ~ /^H1 = /) $0 = "H1 = 5-268435460"
       else if ($0 ~ /^H2 = /) $0 = "H2 = 268435461-268440000"
       else if ($0 ~ /^H3 = /) $0 = "H3 = 268440001-268450000"
       else if ($0 ~ /^H4 = /) $0 = "H4 = 268450001-268460000"
       print
       if ($0 ~ /^MaxHandshakeAttempts = /) print "ContentPaddingAddition = 70000"
     }' "$FX/awg2_conf_line31.txt" >"$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
run import_adopt amnezia-awg2
check "подхвачен (rc=0, профиль awg2, линия 3.1), значения — как в контейнере (RekeyTimeout 0-7, ContentPaddingAddition 70000, Jc 122)" \
    '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg2 ] && [ "$(jget .line)" = 3.1 ] && [ "$(jq -r ".profiles.awg2.params | [.RekeyTimeout, .ContentPaddingAddition, .Jc] | join(\" \")" "$CONFIG_FILE")" = "0-7 70000 122" ]' \
    "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
check "… param_warnings — по строке словами: коридор RekeyTimeout и ContentPaddingAddition, всплеск Jc × Jmax, хвосты с разными S и широкими H" \
    '[ "$(jget ".param_warnings | length")" = 4 ] && grep -qF "RekeyTimeout = 0-7 — вне коридора 3–10 (у новых профилей — 3-7)" <<<"$(jget ".param_warnings[0]")" && grep -qF "ContentPaddingAddition = 70000 — вне коридора 0–1400" <<<"$(jget ".param_warnings[1]")" && grep -qF "Jc × Jmax = 122 × 1280 = 156160 байт мусора" <<<"$(jget ".param_warnings[2]")" && grep -qF "разные S (S1 при S4 = 12) и широкие H (H1 — вместе 268435456 значений)" <<<"$(jget ".param_warnings[3]")"' \
    "$(jget .param_warnings)"
check "… где поправить — «Изменить один параметр» карточки профиля (имя пункта меню, как в строке о пропуске I1); те же слова — в SSH (stderr)" \
    '[ "$(jget "[.param_warnings[] | select(contains(\"в «Изменить один параметр» карточки профиля\"))] | length")" = 4 ] && grep -qF "RekeyTimeout = 0-7 — вне коридора" "$ERR" && grep -qF "Jc × Jmax = 122 × 1280" "$ERR"' \
    "$(errs)"
run amnezia_profile_set_param awg2 Jmin 1100
check "… подхваченное с чужими значениями не мешает менять другое (Jmin 1100 — всплеск тот же)" '[ "$rc" = 0 ]' "rc=$rc $GA_ERR_MSG"
run amnezia_profile_set_param awg2 RekeyTimeout 0-5
check "… а записать вне коридора снова — нельзя: отказ словами" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = params_invalid ] && grep -qF "RekeyTimeout = 0-5 не принят: допустимо от 3 до 10" <<<"$GA_ERR_MSG"' "rc=$rc $GA_ERR_MSG"

# ── 6a. Линейка подхваченного профиля берётся из его конфига, не из имени ────
# Приложение Amnezia ставит контейнер amnezia-awg2 какой угодно линейки: имя
# у него одно, а параметры внутри — те, что выбрал человек. Профиль awg2,
# подхваченный с 3.x-конфигом, обязан жить в 3.1 (иначе profile-set-param
# отвергал бы собственные параметры контейнера), а свежий awg2 — в 2.0.
section "подхваченный профиль с чужой линейкой"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"
ga_config_set '.profiles' '{}' >/dev/null 2>&1
rm -rf "$TMP/cfs"; mkdir -p "$TMP/cfs"; cp -R "$FX/fs/." "$TMP/cfs/"
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/cfs"
cp "$FX/awg2_conf_line31.txt" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
run import_adopt amnezia-awg2
check "подхват amnezia-awg2 с 3.x-параметрами → профиль awg2, line 3.1" \
    '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg2 ] && [ "$(jget .line)" = 3.1 ]' "rc=$rc $out $(errs)"
check "значения приложения Amnezia — в пределах: param_warnings пуст (1.0.13)" '[ "$(jget ".param_warnings | length")" = 0 ]' \
    "$(jget .param_warnings)"
check "в config.json у awg2 записана линейка 3.1, а не 2.0 по имени" \
    '[ "$(ga_config_get ".profiles.awg2.line")" = 3.1 ]' "line=$(ga_config_get '.profiles.awg2.line')"
check "ga_profile_line/ga_profile_line_base отвечают 3.1, ga_profile_default — по-прежнему 2.0" \
    '[ "$(ga_profile_line awg2)" = 3.1 ] && [ "$(ga_profile_line_base awg2)" = 3.1 ] && [ "$(ga_profile_default awg2 line)" = 2.0 ]' \
    "line=$(ga_profile_line awg2) base=$(ga_profile_line_base awg2)"
run amnezia_profile_param_list awg2
check "список параметров подхваченного awg2 содержит 3.x-ключи" \
    '[ "$rc" = 0 ] && [ "$(jget "[.[] | select(.key == \"HeaderProtectionKey\")] | length")" = 1 ] && [ "$(jget "[.[] | select(.key == \"RandomTrailers\")] | length")" = 1 ]' "$out"
HPK_NEW=$(python3 "$ROOT/lib/awgkeys.py" genkey | { read -r k; python3 "$ROOT/lib/awgkeys.py" pubkey "$k"; })
run amnezia_profile_set_param awg2 HeaderProtectionKey "$HPK_NEW"
check "profile-set-param HeaderProtectionKey на подхваченном awg2 проходит (scope server, reissue)" \
    '[ "$rc" = 0 ] && [ "$(jget .line)" = 3.1 ] && [ "$(jget .scope)" = server ] && [ "$(jget .reissue_required)" = true ]' "rc=$rc $out $(errs)"
run amnezia_profile_regen awg2 default
check "profile-regen подхваченного awg2 остаётся в 3.1" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get ".profiles.awg2.line")" = 3.1 ]' "rc=$rc line=$(ga_config_get '.profiles.awg2.line') $(errs)"

# Тот же ключ на СВЕЖЕМ awg2 — отказ с прежним кодом: линейки несовместимы,
# и имя профиля тут ни при чём, решают его фактические параметры.
run amnezia_profile_remove awg2
check "подхваченный awg2 удалён (освобождаем слот под свежую установку)" '[ "$rc" = 0 ]' "$(errs)"
# На сервере profile-remove удаляет и контейнер, а установка ставит новый;
# стенд каталог-фикстуру не трогает. Убираем её на время установки — иначе
# страж увидел бы «чужой контейнер с людьми» (foreign_container) — и
# возвращаем: дальше сторож и сверка смотрят на «работающий» контейнер.
mv "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2" "$TMP/awg2.removed"
ga_config_merge '{"profiles":{"awg3":{"subnet":"10.8.3.0/24"}}}' >/dev/null 2>&1
run amnezia_profile_install awg2 40002 default
mv "$TMP/awg2.removed" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2"
check "свежий awg2 установлен в линейке 2.0" \
    '[ "$rc" = 0 ] && [ "$(jget .line)" = 2.0 ] && [ "$(ga_profile_line awg2)" = 2.0 ] && [ "$(ga_profile_line_base awg2)" = 2.0 ]' "rc=$rc $out $(errs)"
run amnezia_profile_set_param awg2 HeaderProtectionKey "$HPK_NEW"
check "profile-set-param HeaderProtectionKey на свежем awg2 отвергнут кодом params_invalid (rc 2)" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = params_invalid ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
run amnezia_profile_param_list awg2
check "список параметров свежего awg2 без 3.x-ключей" \
    '[ "$rc" = 0 ] && [ "$(jget "[.[] | select(.key == \"HeaderProtectionKey\")] | length")" = 0 ]' "$out"

# Старая установка без поля line: миграция выводит её по фактическим params.
ga_config_set '.profiles.awg2.line' '""' >/dev/null 2>&1
check "поле line затёрто — ga_profile_line всё равно отвечает 2.0 (по params)" \
    '[ "$(ga_config_get ".profiles.awg2.line")" = "" ] && [ "$(ga_profile_line awg2)" = 2.0 ]' "line=$(ga_profile_line awg2)"
run ga_migrate_profile_line
check "ga_migrate_profile_line вернул awg2=2.0 и записал линейку обратно в config.json" \
    '[ "$rc" = 0 ] && grep -q "^awg2=2.0$" <<<"$out" && [ "$(ga_config_get ".profiles.awg2.line")" = 2.0 ]' "out=$out line=$(ga_config_get '.profiles.awg2.line')"
run ga_migrate_profile_line
check "повторная миграция ничего не пишет (идемпотентна)" '[ "$rc" = 0 ] && [ -z "$out" ]' "out=$out"

# ── 7. Сторож видит контейнеры-фикстуры ──────────────────────────────────────
section "сторож с контейнерами приложения"
run amnezia_watch_tick
check "amnezia_watch_tick: awg2 running, action none, ok; событий 0" '[ "$rc" = 0 ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .state")" = running ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .action")" = none ]' "$out"

# Тот же сюжет на удалении: пир, который мы сами убираем, перечислен в
# GA_EXPECT_DROP — предупреждать о нём нельзя. Пользователь одноразовый, чтобы
# не двигать состояние соседних проверок.
run users_add tmp-drop "tmp" awg2
check "временный пользователь для проверки удаления заведён" '[ "$rc" = 0 ]' "$(errs)"
DROP_PK=$(jq -r '.users[] | select(.name == "tmp-drop") | .peers.awg2.pubkey' "$USERS_FILE")
run users_del_peer tmp-drop awg2
# В контейнере-фикстуре к этому месту есть и правда чужие пиры (их добавляли
# сценарии сверки) — о них предупреждать надо. Проверяем именно то, что НАШ
# удаляемый ключ в список чужих не попал.
check "users_del_peer: удаляемый пир не объявляется чужим (GA_EXPECT_DROP сработал)" \
    '[ "$rc" = 0 ] && [ -n "$DROP_PK" ] && ! grep -qF "${DROP_PK:0:12}" "$ERR"' "pk=${DROP_PK:0:12} $(errs)"
run users_del tmp-drop
check "временный пользователь удалён" '[ "$rc" = 0 ]' "$(errs)"

# ── 7a. Регрессия стенда: docker exec не должен съедать stdin цикла ──────────
# Настоящий `docker exec -i` читает stdin вызывающего. import_scan перебирает
# контейнеры циклом `while read … done < <(docker ps …)`, и первый же вызов
# docker_read_file высасывал остаток списка — на живом сервере с тремя
# контейнерами находка состояла из одного. Проверяем с поддельным docker,
# который стдин читает так же жадно; dry-run на время выключаем.
section "docker exec и stdin цикла (без dry-run, поддельный docker)"
FAKEBIN="$TMP/fakebin"; mkdir -p "$FAKEBIN"
cat >"$FAKEBIN/docker" <<'EOS'
#!/bin/sh
# Поддельный docker: ps отдаёт фикстуру, exec читает stdin (как настоящий -i),
# inspect отвечает по запрошенному формату, cat и cp достают файлы из
# каталога-фикстуры, image inspect дайджеста не знает.
case "$1" in
  # ps -a (поиск и остановленных, OWN-1/TK-4) отвечает «имя<TAB>статус<TAB>порты», как настоящий docker
  # с --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'; без -a — прежний формат «имя порты».
  ps) if [ "${2:-}" = "-a" ]; then
          awk '{ n = $1; $1 = ""; sub(/^ /, ""); printf "%s\tUp 2 hours\t%s\n", n, $0 }' "$GOAMNEZIA_DOCKER_PS_FIXTURE"
      else
          cat "$GOAMNEZIA_DOCKER_PS_FIXTURE"
      fi
      exit 0 ;;
  image) exit 1 ;;
  inspect)
      shift; fmt=""
      while [ "$1" = "-f" ] || [ "$1" = "--format" ]; do fmt="$2"; shift 2; done
      case "$fmt" in
        *State.Status*) printf 'running\n' ;;
        *) printf '%s\n' "$1" ;;
      esac
      exit 0 ;;
  cp)
      shift
      src="$1"; dst="$2"
      n=${src%%:*}; path=${src#*:}
      case "$path" in
        */.) cp -R "$GOAMNEZIA_CONTAINER_FS_DIR/$n${path%/.}/." "$dst" 2>/dev/null || exit 1 ;;
        *)   cp "$GOAMNEZIA_CONTAINER_FS_DIR/$n$path" "$dst" 2>/dev/null || exit 1 ;;
      esac
      exit 0 ;;
  exec)
      cat >/dev/null                    # ← то самое чтение stdin
      shift
      while [ "$1" = "-i" ] || [ "$1" = "-d" ]; do shift; done
      n="$1"; shift
      case "$1" in
        cat)  f="$GOAMNEZIA_CONTAINER_FS_DIR/$n$2"; [ -f "$f" ] && cat "$f" ;;
        test) exit 1 ;;                 # каталога warp в контейнере нет
      esac
      exit 0 ;;
esac
exit 0
EOS
chmod +x "$FAKEBIN/docker"
( export GOAMNEZIA_DRY_RUN=0 PATH="$FAKEBIN:$PATH"; import_scan ) >"$OUT" 2>"$ERR" </dev/null
out=$(<"$OUT")
check "import_scan видит все три контейнера amnezia-* из docker ps, а не первый" \
    '[ "$(jq -r "length" <<<"$out")" = 3 ] && [ "$(jq -r "[.[].container] | index([\"nginx-proxy\"])" <<<"$out")" = null ]' \
    "$(jq -c "[.[].container]" <<<"$out" 2>/dev/null)"
check "у amnezia-awg (второй в списке) прочитан конфиг: линейка и порт на месте" \
    '[ "$(jq -r ".[] | select(.container == \"amnezia-awg\") | .line" <<<"$out")" = 1.0 ] && [ "$(jq -r ".[] | select(.container == \"amnezia-awg\") | .listen_port" <<<"$out")" != 0 ]' "$out"

# ── 7b. Регрессия стенда: подхват не должен терять ключи и awg0.conf ─────────
# amnezia_state_sync подменяет state/<контейнер> целиком тем, что лежит в
# контейнере. В import_adopt он шёл ПОСЛЕ записи канонических файлов и стирал
# их: у legacy-контейнера приложения конфиг называется wg0.conf, а отдельных
# файлов с ключами сервера может не быть вовсе — профиль оставался без
# awg0.conf и без приватного ключа, и любая операция, переписывающая конфиг
# (set-param, subnet, mtu, user-add, бэкап), падала с state_missing.
section "подхват legacy: state/ переживает state_sync (без dry-run)"
jq 'del(.profiles.awg1)' "$CONFIG_FILE" > "$CONFIG_FILE.tmp" && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
rm -rf "$STATE_DIR/amnezia-awg"
( export GOAMNEZIA_DRY_RUN=0 PATH="$FAKEBIN:$PATH"; import_adopt amnezia-awg ) >"$OUT" 2>"$ERR" </dev/null
rc=$?; out=$(<"$OUT")
SDL="$STATE_DIR/amnezia-awg"
check "import_adopt legacy без dry-run: rc=0, профиль awg1" '[ "$rc" = 0 ] && [ "$(jq -r .profile <<<"$out")" = awg1 ]' "$(errs)"
check "state/: awg0.conf на месте (а не только wg0.conf из контейнера)" \
    '[ -f "$SDL/awg0.conf" ] && grep -q "^ListenPort = 51820$" "$SDL/awg0.conf"' "$(ls "$SDL" 2>/dev/null | tr "\n" " ")"
check "state/: приватный и публичный ключи сервера восстановлены из конфига" \
    '[ -s "$SDL/wireguard_server_private_key.key" ] && [ -s "$SDL/wireguard_server_public_key.key" ]' "$(ls "$SDL" 2>/dev/null | tr "\n" " ")"
check "state/: права 700 на каталог и 600 на ключ" \
    '[ "$(perm "$SDL")" = 700 ] && [ "$(perm "$SDL/wireguard_server_private_key.key")" = 600 ]'
run amnezia_write_server_conf awg1
check "после подхвата конфиг профиля переписывается (нет state_missing)" \
    '[ "$rc" = 0 ] && [ -z "$GA_ERR_CODE" ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
# ACC-4: копия конфига legacy-профиля — awg0.conf после ЛЮБОЙ синхронизации, а не только после подхвата.
# Прежде следующий же state_sync (добавили человека, перезапустили профиль) клал в state/ каталог контейнера
# как есть — wg0.conf, — а восстановление, починка и обновление ядра ищут awg0.conf (state_missing).
check "ACC-4 после подхвата в state/ одна копия конфига — awg0.conf (0600), дубля wg0.conf нет" \
    '[ -f "$SDL/awg0.conf" ] && [ ! -e "$SDL/wg0.conf" ] && [ "$(perm "$SDL/awg0.conf")" = 600 ]' "$(ls "$SDL" 2>/dev/null | tr "\n" " ")"
LEG_CONF="$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg/opt/amnezia/awg/wg0.conf"
LEG_PRIV=$(awk -F' = ' '/^PrivateKey/ {print $2; exit}' "$LEG_CONF")
rm -f "$SDL/wireguard_server_private_key.key" "$SDL/wireguard_server_public_key.key"
( export GOAMNEZIA_DRY_RUN=0 PATH="$FAKEBIN:$PATH"; amnezia_state_sync awg1 ) >"$OUT" 2>"$ERR" </dev/null
rc=$?
check "ACC-4 state_sync legacy без dry-run: rc=0, wg0.conf контейнера лёг в state/ под awg0.conf (0600), wg0.conf нет" \
    '[ "$rc" = 0 ] && cmp -s "$SDL/awg0.conf" "$LEG_CONF" && [ ! -e "$SDL/wg0.conf" ] && [ "$(perm "$SDL/awg0.conf")" = 600 ]' \
    "rc=$rc $(ls "$SDL" 2>/dev/null | tr "\n" " ") $(errs | tail -3)"
check "ACC-4 ключей сервера файлами у контейнера нет — синхронизация берёт приватный из PrivateKey конфига, открытый из него" \
    '[ "$(cat "$SDL/wireguard_server_private_key.key")" = "$LEG_PRIV" ] && [ "$(cat "$SDL/wireguard_server_public_key.key")" = "$(jq -r .profiles.awg1.server_pubkey "$CONFIG_FILE")" ] && [ "$(perm "$SDL/wireguard_server_private_key.key")" = 600 ]' \
    "$(ls "$SDL" 2>/dev/null | tr "\n" " ")"
LEG_PRIV=""

# ── 7c. Остановленные контейнеры, контейнеры без людей и другие протоколы приложения (OWN-1, TK-4) ──
# Живая жалоба владельца 25.09.2026: установщик «не увидел» контейнеры приложения. Поиск шёл по docker ps без
# -a — остановленный amnezia-awg2 с людьми был невидим (установка отказывала, подхват не находил: тупик), а
# прочие контейнеры приложения назывались «неизвестный тип контейнера».
section "поиск: остановленные контейнеры и контейнеры других протоколов приложения (OWN-1, TK-4)"
PS2="$TMP/docker_ps2.txt"
cp "$FX/docker_ps.txt" "$PS2"
printf 'amnezia-dns \namnezia-openvpn-cloak 0.0.0.0:8443->8443/tcp\n' >>"$PS2"
mkdir -p "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-dns" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-openvpn-cloak"
# Остановленный amnezia-awg3 «приложения», которого нет в docker ps вовсе: свой человек, своя подсеть и порт.
A3="$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg3"; mkdir -p "$A3/opt/amnezia/awg"
A3_PUB=$(python3 "$ROOT/lib/awgkeys.py" genkey | python3 "$ROOT/lib/awgkeys.py" pubkey -)
awk '/^\[Peer\]/ { exit } { sub(/^Address = .*/, "Address = 10.8.3.0/24"); sub(/^ListenPort = .*/, "ListenPort = 38333"); print }' \
    "$FX/fs/amnezia-awg2/opt/amnezia/awg/awg0.conf" >"$A3/opt/amnezia/awg/awg0.conf"
printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = 10.8.3.1/32\n' "$A3_PUB" "$PSK2" >>"$A3/opt/amnezia/awg/awg0.conf"
# clientsTable — ровно как её пишет приложение (стенд C1, 27.09.2026): Qt toJson (отступ 4, ключи по алфавиту),
# creationDate — Qt TextDate, у первого клиента allowed_ips из awg show. По ней проверяется ACC-1 ниже.
sed "s#@PUB@#$A3_PUB#" "$FX/app_clientsTable_qt.json" >"$A3/opt/amnezia/awg/clientsTable"
printf 'amnezia-awg3\n' >"$A3/.image"; printf 'exited\n' >"$A3/.state"
GOAMNEZIA_DOCKER_PS_FIXTURE="$PS2" run docker_ps_all
check "docker_ps_all: работающие из docker ps, остановленный amnezia-awg3 из фикстуры — exited, без портов" \
    '[ "$rc" = 0 ] && grep -qx "amnezia-awg3	exited	" <<<"$out" && grep -q "^amnezia-awg2	running	0.0.0.0:38123" <<<"$out"' "$out"
GOAMNEZIA_DOCKER_PS_FIXTURE="$PS2" run import_scan
row() { jq -c --arg c "$1" '.[] | select(.container == $c)' <<<"$out"; }
check "import_scan: остановленный amnezia-awg3 виден — state exited, порт из конфига, adoptable → awg3, 1 человек" \
    '[ "$(row amnezia-awg3 | jq -r .state)" = exited ] && [ "$(row amnezia-awg3 | jq -r .port)" = 38333 ] && [ "$(row amnezia-awg3 | jq -r .proto)" = udp ] && [ "$(row amnezia-awg3 | jq -r .adoptable)" = true ] && [ "$(row amnezia-awg3 | jq -r .suggested_profile)" = awg3 ] && [ "$(row amnezia-awg3 | jq -r .peers)" = 1 ]' \
    "$(row amnezia-awg3 | jq -c 'del(.clients)')"
check "import_scan: DNS приложения назван DNS, а не «неизвестный тип» (протокол dns, причина словами)" \
    '[ "$(row amnezia-dns | jq -r .protocol)" = dns ] && [ "$(row amnezia-dns | jq -r .kind)" = unsupported ] && [ "$(row amnezia-dns | jq -r .reason)" = unsupported_container ] && [ "$(row amnezia-dns | jq -r .reason_text)" = "$(t import_reason_app_dns)" ]' \
    "$(row amnezia-dns)"
check "import_scan: OpenVPN+Cloak приложения — протокол cloak, не подхватывается с причиной «только AmneziaWG»" \
    '[ "$(row amnezia-openvpn-cloak | jq -r .protocol)" = cloak ] && [ "$(row amnezia-openvpn-cloak | jq -r .reason_text)" = "$(t import_reason_unsupported_container)" ] && [ "$(row amnezia-openvpn-cloak | jq -r .port)" = 8443 ]' \
    "$(row amnezia-openvpn-cloak)"
check "import_scan: у каждого контейнера есть state и protocol; управляемые названы с профилем" \
    '[ "$(jq -r "[.[] | select((.state // \"\") == \"\" or (.protocol // \"\") == \"\")] | length" <<<"$out")" = 0 ] && [ "$(row amnezia-awg2 | jq -r .reason_text)" = "$(tf import_reason_managed_as awg2)" ]' "$out"
# SR-38: подхват не пишет два профиля на один порт.
awg2_port=$(jq -r .profiles.awg2.port "$CONFIG_FILE")
jq '.profiles.awg2.port = 38333' "$CONFIG_FILE" >"$CONFIG_FILE.tmp" && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
GOAMNEZIA_DOCKER_PS_FIXTURE="$PS2" run import_adopt amnezia-awg3
check "SR-38 порт подхватываемого контейнера уже у профиля awg2 в config.json → port_busy, профиль awg3 не записан" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = port_busy ] && ! ga_profile_installed awg3 && [ -f "$A3/.state" ]' \
    "rc=$rc code=$GA_ERR_CODE $(errs | tail -3)"
jq --argjson p "$awg2_port" '.profiles.awg2.port = $p' "$CONFIG_FILE" >"$CONFIG_FILE.tmp" && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
GOAMNEZIA_DOCKER_PS_FIXTURE="$PS2" run import_adopt amnezia-awg3
check "TK-4 остановленный контейнер подхватывается и запускается: rc=0, started=true, человек записан" \
    '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg3 ] && [ "$(jget .state_before)" = exited ] && [ "$(jget .started)" = true ] && grep -q "DRY: docker start amnezia-awg3" "$ERR" && [ ! -f "$A3/.state" ] && jq -e ".users[] | select(.name == \"Mama\") | .peers.awg3.imported" "$USERS_FILE" >/dev/null' \
    "rc=$rc $(errs | tail -4) $out"
check "TK-4 state/ подхваченного остановленного: awg0.conf и ключ сервера прочитаны (docker cp)" \
    '[ -s "$STATE_DIR/amnezia-awg3/awg0.conf" ] && [ -s "$STATE_DIR/amnezia-awg3/wireguard_server_private_key.key" ]'
check "TK-4 сказано, что контейнер запущен" 'grep -qF "$(tf import_started amnezia-awg3 awg3)" "$ERR"' "$(errs | tail -3)"
iso3_exec="docker exec -i amnezia-awg3 sh -c '$(ga_amn_isolation_cmd awg0 on)'"
check "SEC-NET-1 остановленный контейнер приложения: изоляция клиентов — сразу после подъёма" \
    'grep -qF "$iso3_exec" "$ERR"' "$(grep DRY "$ERR" | tail -3)"
# ACC-1: первая же запись clientsTable подхваченного контейнера (новый человек на профиле) ставила людям
# приложения вместо их даты время подхвата и теряла allowed_ips — приложение показывало чужую дату.
A3_UD=$(jq -c '.[0].userData' "$A3/opt/amnezia/awg/clientsTable")
ct3() { jq -c --arg k "$1" '.[] | select(.clientId == $k) | .userData' "$STATE_DIR/amnezia-awg3/clientsTable" 2>/dev/null; }
check "ACC-1 после подхвата: в users.json created — ISO-метка, state/clientsTable — таблица приложения байт в байт" \
    '[[ "$(jq -r ".users[] | select(.name == \"Mama\") | .created" "$USERS_FILE")" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] && cmp -s "$STATE_DIR/amnezia-awg3/clientsTable" "$A3/opt/amnezia/awg/clientsTable" && [ "$(jq -r .creationDate <<<"$A3_UD")" = "Sun Sep 27 08:33:56 2026" ]' \
    "$(jq -c ".users[] | select(.name == \"Mama\") | {created, label: .label}" "$USERS_FILE")"
GOAMNEZIA_DOCKER_PS_FIXTURE="$PS2" run users_add acc1-new "Новый" awg3
NEW3_PUB=$(jq -r '.users[] | select(.name == "acc1-new") | .peers.awg3.pubkey' "$USERS_FILE")
check "ACC-1 новый человек на подхваченном профиле: запись приложения о Маме дословно — дата Qt TextDate, allowed_ips, имя" \
    '[ "$rc" = 0 ] && [ -n "$(ct3 "$A3_PUB")" ] && [ "$(ct3 "$A3_PUB" | jq -cS .)" = "$(jq -cS . <<<"$A3_UD")" ]' \
    "rc=$rc $(ct3 "$A3_PUB") $(errs | tail -2)"
check "ACC-1 у нового — своя подпись и ISO-дата из users.json" \
    '[ "$(ct3 "$NEW3_PUB" | jq -r .clientName)" = "Новый" ] && [ "$(ct3 "$NEW3_PUB" | jq -r .creationDate)" = "$(jq -r ".users[] | select(.name == \"acc1-new\") | .created" "$USERS_FILE")" ]' \
    "$(ct3 "$NEW3_PUB")"
GOAMNEZIA_DOCKER_PS_FIXTURE="$PS2" run users_rename Mama "Мама на даче"
check "ACC-1 переименование у нас меняет только имя: дата приложения и allowed_ips на месте" \
    '[ "$rc" = 0 ] && [ "$(ct3 "$A3_PUB" | jq -r .clientName)" = "Мама на даче" ] && [ "$(ct3 "$A3_PUB" | jq -r .creationDate)" = "Sun Sep 27 08:33:56 2026" ] && [ "$(ct3 "$A3_PUB" | jq -r .allowed_ips)" = 10.8.3.1/32 ]' \
    "rc=$rc $(ct3 "$A3_PUB")"
GOAMNEZIA_DOCKER_PS_FIXTURE="$PS2" run users_del acc1-new
check "ACC-1 удаление соседа: запись приложения на месте, удалённого в таблице нет" \
    '[ "$rc" = 0 ] && [ "$(ct3 "$A3_PUB" | jq -r .creationDate)" = "Sun Sep 27 08:33:56 2026" ] && [ -z "$(ct3 "$NEW3_PUB")" ]' \
    "rc=$rc $(cat "$STATE_DIR/amnezia-awg3/clientsTable" 2>&1)"
# Подпись из имени приложения режется по знакам, а не по байтам (cut -c резал кириллицу посреди буквы), и
# по тому же определению запись таблицы узнаёт, что длинное имя у нас не меняли, — и оставляет его целым.
long_name="Телефон бабушки Анны Сергеевны на даче под Тверью, старый"
check "ACC-1 подпись из имени приложения: 48 знаков, а не байтов, без управляющих" \
    '[ "$(jq -rn --arg n "$long_name" "$GA_AMN_LABEL_JQ"" \$n | ga_app_label")" = "Телефон бабушки Анны Сергеевны на даче под Тверь" ] && [ "$(jq -rn --arg n "a	b" "$GA_AMN_LABEL_JQ"" \$n | ga_app_label")" = ab ]'
# Демон не отвечает — отказ docker_unreachable, а не «контейнеров нет».
_ps_all_saved=$(declare -f docker_ps_all)
docker_ps_all() { return 1; }
run import_scan
check "import_scan при молчащем демоне: отказ docker_unreachable, а не пустой список" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = docker_unreachable ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE out=$out"
eval "$_ps_all_saved"
rm -rf "$A3" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-dns" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-openvpn-cloak"

# ── 7a. Битый users.json: подхват не врёт про своих пиров ───────────────────
# Раньше «пусто» означало «своих пиров нет», и сверка объявляла чужими всех до
# единого — с советом подхватить собственных же пользователей.
section "битый users.json: подхват и сверка отказываются, а не выдумывают"
cp "$USERS_FILE" "$USERS_FILE.ok"
head -c 30 "$USERS_FILE.ok" > "$USERS_FILE"

run import_scan
check "import_scan на битом users.json: отказ users_corrupt, а не список «новых» пиров" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = users_corrupt ]' "rc=$rc code=$GA_ERR_CODE $(errs)"

run import_reconcile awg2
check "import_reconcile на битом users.json: отказ, а не «все пиры чужие»" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = users_corrupt ]' "rc=$rc code=$GA_ERR_CODE $(errs)"

check "битый файл не переписан ни одним из них" \
    '[ "$(wc -c <"$USERS_FILE" | tr -d " ")" = 30 ]' "$(wc -c <"$USERS_FILE")"

cp "$USERS_FILE.ok" "$USERS_FILE"
run import_scan
check "после починки файла подхват снова работает" '[ "$rc" = 0 ]' "rc=$rc $(errs)"

# ── 7d. ACC-4: подхваченный legacy-контейнер (wg0) — починка, миграция, восстановление из бэкапа ──
# Живая приёмка 27.09.2026 (C1): у подхваченного amnezia-awg копия конфига в state/ после синхронизаций
# прежних версий называлась wg0.conf, а восстановление и «Поднять контейнер заново» требовали awg0.conf —
# отказ state_missing, контейнер не пересоздавался, и пиры, которых нет в восстановленном users.json,
# оставались живыми.
section "ACC-4: подхваченный legacy-контейнер (wg0) — починка, миграция, восстановление из бэкапа"
SDL="$STATE_DIR/amnezia-awg"
# Копия «как у прежней версии»: wg0.conf вместо awg0.conf, ключи, clientsTable и start.sh приложения (его
# снимает синхронизация с /opt/amnezia/start.sh контейнера).
printf '#!/bin/bash\nwg-quick down /opt/amnezia/awg/wg0.conf\nif [ -f /opt/amnezia/awg/wg0.conf ]; then (wg-quick up /opt/amnezia/awg/wg0.conf); fi\niptables -A FORWARD -i wg0 -j ACCEPT\ntail -f /dev/null\n' \
    | ga_amn_write_secret "$SDL/start.sh"
acc4_old() { mv -f "$SDL/awg0.conf" "$SDL/wg0.conf"; }
acc4_old
cp -p "$SDL/wg0.conf" "$TMP/acc4.wg0.conf"
check "ACC-4 стенд: копия прежней версии — wg0.conf без awg0.conf, ключ сервера на месте" \
    '[ -f "$SDL/wg0.conf" ] && [ ! -e "$SDL/awg0.conf" ] && [ -s "$SDL/wireguard_server_private_key.key" ]' "$(ls "$SDL" | tr "\n" " ")"
# Чтение (диагностика, выдача адресов) копию прежней версии видит и ничего не переименовывает.
run ga_amn_keys_consistent awg1
check "ACC-4 сверка ключей видит копию прежней версии (wg0.conf) и ничего не переименовывает" \
    '[ "$(jget .ok)" = true ] && [ -n "$(jget .conf)" ] && [ -f "$SDL/wg0.conf" ] && [ ! -e "$SDL/awg0.conf" ]' "$out"
printf '\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.8.5.2/32\n' "$P3" >>"$SDL/wg0.conf"
run users_alloc_ip awg1
check "ACC-4 выдача адресов видит пира приложения в копии прежней версии: 10.8.5.2 занят → 10.8.5.3" \
    '[ "$rc" = 0 ] && [ "$out" = 10.8.5.3 ]' "rc=$rc out=$out"
cp -p "$TMP/acc4.wg0.conf" "$SDL/wg0.conf"

run amnezia_profile_repair awg1
check "ACC-4 «Поднять контейнер заново» на копии прежней версии: rc=0, конфиг ушёл в контейнер под wg0.conf, wg syncconf wg0" \
    '[ "$rc" = 0 ] && grep -qF "DRY: docker cp <stdin> amnezia-awg:/opt/amnezia/awg/wg0.conf (chmod 0600)" "$ERR" && grep -qF "wg syncconf wg0 <(wg-quick strip /opt/amnezia/awg/wg0.conf)" "$ERR" && ! grep -q "amnezia-awg:/opt/amnezia/awg/awg0.conf" "$ERR"' \
    "rc=$rc code=$GA_ERR_CODE $(errs | tail -4)"
check "ACC-4 после починки копия приведена: awg0.conf байт в байт прежняя wg0.conf, 0600, wg0.conf нет; start.sh приложения цел" \
    'cmp -s "$SDL/awg0.conf" "$TMP/acc4.wg0.conf" && [ ! -e "$SDL/wg0.conf" ] && [ "$(perm "$SDL/awg0.conf")" = 600 ] && grep -qF "wg-quick up /opt/amnezia/awg/wg0.conf" "$SDL/start.sh"' \
    "$(ls "$SDL" | tr "\n" " ")"
check "ACC-4 починка записана в журнал действий" 'audit_has profile-repair awg1' "$(tail -2 "$GOAMNEZIA_AUDIT_LOG")"

# Миграция 0.4.6 (install.sh, _migrate_state_conf) — ga_amn_state_canon_all: один раз и без потерь.
acc4_old
run ga_amn_state_canon_all
check "ACC-4 миграция: копия legacy-профиля переименована в awg0.conf (0600, байт в байт), назван только awg1 — родные не тронуты" \
    '[ "$rc" = 0 ] && [ "$out" = awg1 ] && cmp -s "$SDL/awg0.conf" "$TMP/acc4.wg0.conf" && [ ! -e "$SDL/wg0.conf" ] && [ "$(perm "$SDL/awg0.conf")" = 600 ]' \
    "rc=$rc out=$out $(ls "$SDL" | tr "\n" " ")"
run ga_amn_state_canon_all
check "ACC-4 миграция идемпотентна: второй проход ничего не делает" \
    '[ "$rc" = 0 ] && [ -z "$out" ] && cmp -s "$SDL/awg0.conf" "$TMP/acc4.wg0.conf"' "out=$out"
printf '# устаревшая копия\n' >"$SDL/wg0.conf"
run ga_amn_state_canon_all
check "ACC-4 обе копии (awg0.conf новее — её пишет запись конфига после синхронизации): дубль wg0.conf убран, awg0.conf прежняя" \
    '[ "$out" = awg1 ] && [ ! -e "$SDL/wg0.conf" ] && cmp -s "$SDL/awg0.conf" "$TMP/acc4.wg0.conf"' "out=$out $(ls "$SDL" | tr "\n" " ")"

# Восстановление из бэкапа — сценарий приёмки: копия снята, пока state/ был «как у прежней версии» (wg0.conf),
# после неё на профиль добавили человека. Восстановление той же копии обязано убрать его пира, а человека
# приложения без ключа на сервере (Keenetic_doma) — оставить.
# shellcheck disable=SC1091
source "$ROOT/lib/backup.sh"
BOT_DIR="$TMP/bot" ADMIN_DIR="$TMP/admin" MCP_DIR="$TMP/mcp"
NGINX_SITE_CONF="$TMP/etc-nginx/sites-available/goamnezia" NGINX_SITE_LINK="$TMP/etc-nginx/sites-enabled/goamnezia"
acc4_old
run create_backup ""
ACC4_ARCH="$out"
check "ACC-4 копия снята: в архиве state/amnezia-awg/wg0.conf, как у прежней версии" \
    '[ "$rc" = 0 ] && [ -f "$ACC4_ARCH" ] && tar tzf "$ACC4_ARCH" | grep -q "/state/amnezia-awg/wg0.conf$" && ! tar tzf "$ACC4_ARCH" | grep -q "/state/amnezia-awg/awg0.conf$"' \
    "rc=$rc out=$out $(errs | tail -3)"
run users_add acc4-late "Поздний" awg1
LATE_PUB=$(jq -r '.users[] | select(.name == "acc4-late") | .peers.awg1.pubkey' "$USERS_FILE")
check "ACC-4 после копии на legacy-профиль добавлен человек: его пир в живой копии awg0.conf, дубля wg0.conf нет" \
    '[ "$rc" = 0 ] && [ -n "$LATE_PUB" ] && grep -qF "PublicKey = $LATE_PUB" "$SDL/awg0.conf" && [ ! -e "$SDL/wg0.conf" ]' \
    "rc=$rc code=$GA_ERR_CODE $(errs | tail -3)"
run restore_backup "$ACC4_ARCH" "" yes
check "ACC-4 восстановление копии: rc=0, профили подняты (не profiles=failed)" \
    '[ "$rc" = 0 ] && ! grep -q "profiles=failed" "$GOAMNEZIA_AUDIT_LOG" && audit_has backup-restore "$ACC4_ARCH"' \
    "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG $(errs | tail -5)"
check "ACC-4 users.json из копии: acc4-late нет, Keenetic_doma на месте — подхвачен, ключа на сервере нет" \
    '! jq -e ".users[] | select(.name == \"acc4-late\")" "$USERS_FILE" >/dev/null && [ "$(jq -r ".users[] | select(.name == \"Keenetic_doma\") | .peers.awg1.imported" "$USERS_FILE")" = true ] && [ "$(jq -r ".users[] | select(.name == \"Keenetic_doma\") | .peers.awg1.privkey" "$USERS_FILE")" = "" ]' \
    "$(jq -c "[.users[].name]" "$USERS_FILE")"
check "ACC-4 копия конфига — из архива: Keenetic_doma есть, пира acc4-late нет; awg0.conf 0600, wg0.conf нет" \
    'grep -qF "PublicKey = $Q1" "$SDL/awg0.conf" && ! grep -qF "$LATE_PUB" "$SDL/awg0.conf" && cmp -s "$SDL/awg0.conf" "$TMP/acc4.wg0.conf" && [ "$(perm "$SDL/awg0.conf")" = 600 ] && [ ! -e "$SDL/wg0.conf" ]' \
    "$(ls "$SDL" | tr "\n" " ")"
check "ACC-4 amnezia-awg пересоздан на своём образе и получил конфиг под wg0.conf и start.sh приложения" \
    'grep -qF -- "--name amnezia-awg amnezia-awg" "$ERR" && grep -q "^FROM amneziavpn/amnezia-wg" "$GOAMNEZIA_BUILD_ROOT/amnezia-awg/Dockerfile" && grep -qF "DRY: docker cp <stdin> amnezia-awg:/opt/amnezia/awg/wg0.conf (chmod 0600)" "$ERR" && grep -qF "DRY: docker cp <stdin> amnezia-awg:/opt/amnezia/start.sh (chmod 0755)" "$ERR" && ! grep -q "amnezia-awg:/opt/amnezia/awg/awg0.conf" "$ERR" && grep -qF "wg-quick up /opt/amnezia/awg/wg0.conf" "$SDL/start.sh"' \
    "$(grep DRY "$ERR" | grep -E "amnezia-awg[: ]|amnezia-awg$" | head -8)"
check "ACC-4 родной профиль awg2 — прежним путём: копия awg0.conf уходит в контейнер под awg0.conf" \
    'grep -qF "DRY: docker cp <stdin> amnezia-awg2:/opt/amnezia/awg/awg0.conf (chmod 0600)" "$ERR" && [ -f "$STATE_DIR/amnezia-awg2/awg0.conf" ] && [ ! -e "$STATE_DIR/amnezia-awg2/wg0.conf" ]' \
    "$(grep DRY "$ERR" | grep amnezia-awg2 | head -5)"
LATE_PUB=""

# ── 7e. SUBNET-01: подсети подхваченных профилей и новых ─────────────────────
# Жалоба владельца 06.10: «при подхвате старого протокола и попытке добавить новый почему-то пересекаются подсети».
# Приложение Amnezia ставит ЛЮБОЙ свой контейнер AmneziaWG — и старый amnezia-awg (wg0), и amnezia-awg2 — на одну
# 10.8.1.0/24 (protocols::wireguard::defaultSubnetAddress в amnezia-client). Было: подхват второго отказывал
# subnet_overlap, а старому сменить подсеть нельзя (subnet_legacy) — тупик без выхода; Ленивый Pro и мастер на месте
# 2.0 с людьми останавливались. Подхваченный профиль без поля subnet (или с устаревшим) свободной считал свою же сеть,
# а не подхваченный ещё контейнер приложения выбор подсети не видел вовсе — новый профиль вставал на чужую сеть.
# Сценарии: а) подхвачен старый amnezia-awg, новый профиль — через мост и меню; б) два контейнера приложения на одной
# подсети — подхват в обоих порядках, выход (перенести тот, кому смена разрешена, с согласием за людей приложения),
# мост и SSH-меню; в) в config.json подсеть не записана или не та, что в его wg0.conf.
section "SUBNET-01: подсети подхваченных и новых профилей"
export BOT_DIR="$TMP/sn-bot" ADMIN_DIR="$TMP/sn-admin" GOAMNEZIA_DOCKER_SOCK="$TMP/sn-nosuch.sock"
export NGINX_SITE_CONF="$TMP/sn-nginx/sites-available/goamnezia" NGINX_SITE_LINK="$TMP/sn-nginx/sites-enabled/goamnezia"
export GOAMNEZIA_SYSTEMD_DIR="$TMP/sn-systemd"
SN_BIN="$TMP/sn-hostbin"; mkdir -p "$SN_BIN"; printf '#!/bin/sh\nexit 1\n' >"$SN_BIN/systemctl"; chmod +x "$SN_BIN/systemctl"
SN_CFS="$TMP/sn-cfs"
# sn_reset <контейнеры через пробел> — чистый сервер приложения: профилей и людей нет, копий в state/ нет; на машине —
# названные контейнеры приложения из фикстур, оба AmneziaWG на 10.8.1.0/24 (как ставит приложение).
sn_reset() {
    local c
    ga_config_set '.profiles' '{}' >/dev/null 2>&1
    printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"
    rm -rf "$STATE_DIR"/amnezia-awg "$STATE_DIR"/amnezia-awg1 "$STATE_DIR"/amnezia-awg2 "$STATE_DIR"/amnezia-awg3 "$SN_CFS"
    mkdir -p "$SN_CFS"
    : >"$TMP/sn_ps.txt"
    for c in $1; do
        cp -R "$FX/fs/$c" "$SN_CFS/"
        case "$c" in
            amnezia-awg)  printf 'amnezia-awg 0.0.0.0:51820->51820/udp\n' >>"$TMP/sn_ps.txt" ;;
            amnezia-awg2) printf 'amnezia-awg2 0.0.0.0:38123->38123/udp, :::38123->38123/udp\n' >>"$TMP/sn_ps.txt" ;;
        esac
    done
    if [ -f "$SN_CFS/amnezia-awg/opt/amnezia/awg/wg0.conf" ]; then
        sed 's#10\.8\.5\.#10.8.1.#g' "$SN_CFS/amnezia-awg/opt/amnezia/awg/wg0.conf" >"$TMP/sn.wg0" \
            && cat "$TMP/sn.wg0" >"$SN_CFS/amnezia-awg/opt/amnezia/awg/wg0.conf"
    fi
    export GOAMNEZIA_CONTAINER_FS_DIR="$SN_CFS" GOAMNEZIA_DOCKER_PS_FIXTURE="$TMP/sn_ps.txt"
}
# sn_act <действие> [--k=v …] — мост новым процессом, как у бота и панели; out — последняя строка ответа.
sn_act() {
    local a="$1"; shift
    GA_ERR_CODE=""; GA_ERR_MSG=""
    PATH="$SN_BIN:$PATH" "${BASH5:-$BASH}" "$ROOT/install.sh" --action="$a" --json "$@" >"$OUT" 2>"$ERR" </dev/null; rc=$?
    out=$(tail -n 1 "$OUT")
    cat "$OUT" "$ERR" >>"$ALL"
    return 0
}
sn_sub() { jq -r --arg p "$1" '.profiles[$p].subnet // ""' "$CONFIG_FILE"; }
# sn_menu <сценарий bash> — install.sh как библиотека (без main): вопросы подменены — confirm «да», набор фразы —
# SN_TYPED; stdin пуст. Весь вывод — в $OUT.
sn_menu() {
    SN_TYPED="${SN_TYPED:-}" PATH="$SN_BIN:$PATH" "${BASH5:-$BASH}" -c 'source "$1" >/dev/null 2>&1 || exit 9
        confirm() { return 0; }
        confirm_phrase() { [ "${SN_TYPED:-}" = "$1" ]; }
        eval "$2"' _ "$TMP/menu/install.sh" "$1" >"$OUT" 2>&1 </dev/null; rc=$?
    cat "$OUT" >>"$ALL"
    return 0
}

# а) Подхвачен старый amnezia-awg (wg0, 10.8.1.0/24) — новые профили встают на свои подсети: мостом и из меню.
sn_reset "amnezia-awg"
run import_adopt amnezia-awg
check "SUBNET-01 а: старый amnezia-awg подхвачен как awg1 на 10.8.1.0/24 (wg0), пересечений нет" \
    '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg1 ] && [ "$(jget .subnet)" = 10.8.1.0/24 ] && [ "$(jget .iface)" = wg0 ] && [ "$(jget .subnet_overlap)" = null ]' \
    "rc=$rc $(jget '{profile, subnet, iface, subnet_overlap}') $(errs | tail -2)"
sn_menu 'printf "\n\n" | _profile_install_interactive awg3; echo "rc=$?"'
check "SUBNET-01 а: меню «Протоколы → 3.1 → Установить», Enter на подсети — awg3 на 10.8.3.0/24, а не на подсети подхваченного" \
    'grep -q "rc=0" "$OUT" && [ "$(sn_sub awg3)" = 10.8.3.0/24 ]' "$(tail -5 "$OUT") awg3=$(sn_sub awg3)"
run amnezia_profile_remove awg3
sn_act profile-install --profile=awg2
check "SUBNET-01 а: мост profile-install awg2 (2.0) — 10.8.2.0/24, не 10.8.1.0/24" \
    '[ "$rc" = 0 ] && [ "$(sn_sub awg2)" = 10.8.2.0/24 ]' "rc=$rc $out"
sn_act profile-install --profile=awg3 --subnet=10.8.1.0/24
check "SUBNET-01 а: своя подсеть, совпадающая с подхваченным, — subnet_overlap со словами, с кем, и свободной подсетью" \
    '[ "$rc" != 0 ] && [ "$(jq -r .code <<<"$out")" = subnet_overlap ] && grep -qF "AmneziaWG 1.0 · порт 51820 · из приложения" <<<"$out" && grep -qF "$(tf amnezia_subnet_free_hint 10.8.3.0/24)" <<<"$out" && [ -z "$(sn_sub awg3)" ]' "rc=$rc $out"
sn_act status
check "SUBNET-01 а: status — subnet_overlaps пуст" '[ "$rc" = 0 ] && [ "$(jq -c .subnet_overlaps <<<"$out")" = "[]" ]' "$(jq -c .subnet_overlaps <<<"$out" 2>/dev/null)"

# а2) Контейнер приложения ещё не подхвачен (2.0 на 10.8.1.0/24, с людьми): новый 1.5 не встаёт на его подсеть —
# иначе подхват потом упирался бы в пересечение.
sn_reset "amnezia-awg2"
sn_act profile-install --profile=awg1
check "SUBNET-01 а2: 1.5 при не подхваченном amnezia-awg2 на 10.8.1.0/24 — встаёт не на 10.8.1.0/24 (на 10.8.2.0/24)" \
    '[ "$rc" = 0 ] && [ "$(sn_sub awg1)" = 10.8.2.0/24 ]' "rc=$rc awg1=$(sn_sub awg1) $out"
run import_adopt amnezia-awg2
check "SUBNET-01 а2: подхват amnezia-awg2 после этого — без пересечения" \
    '[ "$rc" = 0 ] && [ "$(jget .subnet_overlap)" = null ]' "rc=$rc $(jget .subnet_overlap) $(errs | tail -2)"

# б1) Оба контейнера приложения на 10.8.1.0/24, сначала подхвачен старый (как у владельца на снимке).
sn_reset "amnezia-awg amnezia-awg2"
run import_adopt amnezia-awg
run import_adopt amnezia-awg2
check "SUBNET-01 б1: amnezia-awg2 на той же 10.8.1.0/24 подхвачен (а не отказ subnet_overlap), контейнер не пересоздан" \
    '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg2 ] && [ "$(jget .subnet)" = 10.8.1.0/24 ] && ! grep -qE "docker (rm|run|build)" "$ERR"' \
    "rc=$rc code=$GA_ERR_CODE $(errs | tail -3)"
check "SUBNET-01 б1: в ответе subnet_overlap — с awg1; выход: awg2 на свободную 10.8.2.0/24, у него 2 человека приложения без ключа" \
    '[ "$(jget ".subnet_overlap.with | join(\",\")")" = awg1 ] && [ "$(jget .subnet_overlap.fixed)" = false ] && [ "$(jget .subnet_overlap.move.profile)" = awg2 ] && [ "$(jget .subnet_overlap.move.subnet)" = 10.8.2.0/24 ] && [ "$(jget .subnet_overlap.move.keyless)" = 2 ]' \
    "$(jget .subnet_overlap)"
check "SUBNET-01 б1: подхват сказал словами — с кем пересеклось и кого перенести" \
    'grep -qF "$(t import_adopted_overlap)" "$ERR" && grep -qF "$(tf amnezia_subnet_overlap_move "AmneziaWG 2.0 · порт 38123 · из приложения" 10.8.2.0/24)" "$ERR"' "$(errs | tail -3)"
sn_act status
check "SUBNET-01 б1: status.subnet_overlaps — awg1 (fixed, выход через awg2) и awg2 (выход — он сам на 10.8.2.0/24)" \
    '[ "$(jq -r "[.subnet_overlaps[].profile] | sort | join(\",\")" <<<"$out")" = awg1,awg2 ] && [ "$(jq -r ".subnet_overlaps[] | select(.profile == \"awg1\") | .fixed" <<<"$out")" = true ] && [ "$(jq -r ".subnet_overlaps[] | select(.profile == \"awg1\") | .move.profile" <<<"$out")" = awg2 ] && [ "$(jq -r ".subnet_overlaps[] | select(.profile == \"awg2\") | .move.subnet" <<<"$out")" = 10.8.2.0/24 ]' \
    "$(jq -c .subnet_overlaps <<<"$out" 2>/dev/null) $out"
sn_act profiles-status
check "SUBNET-01 б1: profiles-status — subnet_fixed у awg1, нет у awg2; subnet_overlaps те же" \
    '[ "$(jq -r ".profiles[] | select(.profile == \"awg1\") | .subnet_fixed" <<<"$out")" = true ] && [ "$(jq -r ".profiles[] | select(.profile == \"awg2\") | .subnet_fixed" <<<"$out")" = false ] && [ "$(jq -r ".subnet_overlaps | length" <<<"$out")" = 2 ]' "$out"
sn_act profile-subnet --profile=awg1 --subnet=10.8.2.0/24
check "SUBNET-01 б1: старому awg1 сменить подсеть нельзя (subnet_legacy), но отказ ведёт к выходу: перенести AmneziaWG 2.0 · порт 38123 · из приложения на 10.8.2.0/24" \
    '[ "$(jq -r .code <<<"$out")" = subnet_legacy ] && grep -qF "AmneziaWG 2.0 · порт 38123 · из приложения" <<<"$out" && grep -qF "10.8.2.0/24" <<<"$out" && [ "$(sn_sub awg1)" = 10.8.1.0/24 ]' "$out"
sn_act profile-subnet --profile=awg2 --subnet=10.8.2.0/24
check "SUBNET-01 б1: перенос awg2 без согласия — adopted_keyless (у людей приложения ключ с адресом 10.8.1.x), ничего не тронуто" \
    '[ "$(jq -r .code <<<"$out")" = adopted_keyless ] && [ "$(sn_sub awg2)" = 10.8.1.0/24 ] && ! grep -qE "docker (rm|run)" "$ERR"' "$out"
sn_act profile-subnet --profile=awg2 --subnet=10.8.2.0/24 --adopted-confirm=awg2
check "SUBNET-01 б1: с согласием — awg2 на 10.8.2.0/24, пользователи перенумерованы в 10.8.2.x, awg1 не тронут" \
    '[ "$rc" = 0 ] && [ "$(sn_sub awg2)" = 10.8.2.0/24 ] && [ "$(sn_sub awg1)" = 10.8.1.0/24 ] && [ "$(jq -r "[.users[] | .peers.awg2.ip // empty | select(startswith(\"10.8.2.\"))] | length" "$USERS_FILE")" = 2 ]' "rc=$rc $out"
sn_act status
check "SUBNET-01 б1: после переноса пересечений нет" '[ "$(jq -c .subnet_overlaps <<<"$out")" = "[]" ]' "$(jq -c .subnet_overlaps <<<"$out" 2>/dev/null)"

# б2) Тот же сервер, порядок другой: сначала amnezia-awg2, потом старый — выход тот же (переносится awg2, старый не трогаем).
sn_reset "amnezia-awg amnezia-awg2"
run import_adopt amnezia-awg2
run import_adopt amnezia-awg
check "SUBNET-01 б2: старый amnezia-awg после amnezia-awg2 на той же подсети — подхвачен; fixed, выход — awg2 на 10.8.2.0/24" \
    '[ "$rc" = 0 ] && [ "$(jget .profile)" = awg1 ] && [ "$(jget .subnet_overlap.fixed)" = true ] && [ "$(jget .subnet_overlap.move.profile)" = awg2 ] && [ "$(jget .subnet_overlap.move.subnet)" = 10.8.2.0/24 ]' \
    "rc=$rc code=$GA_ERR_CODE $(jget .subnet_overlap) $(errs | tail -2)"
# SSH: карточка старого профиля и его пункт «Подсеть» — отказ сразу, с выходом; «да» и набранное имя — перенос awg2.
sn_menu '_profile_card awg1 <<<"0"'
check "SUBNET-01 б2: SSH, карточка awg1 говорит о пересечении и кого перенести" \
    'flat <"$OUT" | grep -qF "$(tf amnezia_subnet_overlap_move "AmneziaWG 2.0 · порт 38123 · из приложения" 10.8.2.0/24 | flat)"' "$(tail -12 "$OUT")"
SN_TYPED=38123 sn_menu '_profile_set_subnet_interactive awg1; echo "rc=$?"'
check "SUBNET-01 б2: SSH «Подсеть» у awg1 — отказ с выходом сразу, перенос awg2 на 10.8.2.0/24 после «да» и набранного имени" \
    'grep -q "rc=0" "$OUT" && flat <"$OUT" | grep -qF "$(tf amnezia_err_subnet_legacy_overlap awg1 wg0 10.8.1.0/24 "AmneziaWG 2.0 · порт 38123 · из приложения" 10.8.2.0/24 "AmneziaWG 2.0 · порт 38123 · из приложения" | flat)" && [ "$(sn_sub awg2)" = 10.8.2.0/24 ] && [ "$(sn_sub awg1)" = 10.8.1.0/24 ]' \
    "$(tail -8 "$OUT") awg2=$(sn_sub awg2)"

# б3) SSH «Подсеть» у переносимого профиля: свободная подставлена — Enter, «да», имя профиля; без имени — ничего.
sn_reset "amnezia-awg amnezia-awg2"
run import_adopt amnezia-awg
run import_adopt amnezia-awg2
SN_TYPED="" sn_menu 'printf "\n" | _profile_set_subnet_interactive awg2; echo "rc=$?"'
check "SUBNET-01 б3: SSH «Подсеть» у awg2 — Enter берёт свободную 10.8.2.0/24; без набранного имени пользователи приложения не отрезаны" \
    'grep -q "rc=1" "$OUT" && grep -qF "10.8.2.0/24" "$OUT" && [ "$(sn_sub awg2)" = 10.8.1.0/24 ]' "$(tail -8 "$OUT")"
SN_TYPED=38123 sn_menu 'printf "\n" | _profile_set_subnet_interactive awg2; echo "rc=$?"'
check "SUBNET-01 б3: с набранным именем — awg2 на 10.8.2.0/24" 'grep -q "rc=0" "$OUT" && [ "$(sn_sub awg2)" = 10.8.2.0/24 ]' "$(tail -8 "$OUT")"

# в) В config.json подсеть подхваченного не записана или записана не та, что в его конфиге (Address).
sn_reset "amnezia-awg"
run import_adopt amnezia-awg
jq 'del(.profiles.awg1.subnet)' "$CONFIG_FILE" >"$TMP/sn.cfg" && cat "$TMP/sn.cfg" >"$CONFIG_FILE"
sn_act profile-install --profile=awg3 --subnet=10.8.1.0/24
check "SUBNET-01 в: подсеть подхваченного не записана — его 10.8.1.0/24 (Address копии) всё равно занята: subnet_overlap" \
    '[ "$(jq -r .code <<<"$out")" = subnet_overlap ] && [ -z "$(sn_sub awg3)" ]' "rc=$rc $out"
jq '.profiles.awg1.subnet = "10.8.7.0/24"' "$CONFIG_FILE" >"$TMP/sn.cfg" && cat "$TMP/sn.cfg" >"$CONFIG_FILE"
sn_act profile-install --profile=awg3 --subnet=10.8.1.0/24
check "SUBNET-01 в: в config.json 10.8.7.0/24, а контейнер на 10.8.1.0/24 — 10.8.1.0/24 тоже занята" \
    '[ "$(jq -r .code <<<"$out")" = subnet_overlap ] && [ -z "$(sn_sub awg3)" ]' "rc=$rc $out"
sn_act profile-install --profile=awg3 --subnet=10.8.7.0/24
check "SUBNET-01 в: и записанная 10.8.7.0/24 занята" '[ "$(jq -r .code <<<"$out")" = subnet_overlap ]' "rc=$rc $out"
# Подхваченный 2.0 приложения на 10.8.3.0/24 (подсеть меняли в приложении) без записи в config.json: новый 3.1 по
# умолчанию встал бы на 10.8.3.0/24 — на его же сеть.
sn_reset "amnezia-awg2"
sed 's#10\.8\.1\.#10.8.3.#g' "$SN_CFS/amnezia-awg2/opt/amnezia/awg/awg0.conf" >"$TMP/sn.awg0" && cat "$TMP/sn.awg0" >"$SN_CFS/amnezia-awg2/opt/amnezia/awg/awg0.conf"
run import_adopt amnezia-awg2
jq 'del(.profiles.awg2.subnet)' "$CONFIG_FILE" >"$TMP/sn.cfg" && cat "$TMP/sn.cfg" >"$CONFIG_FILE"
sn_act profile-install --profile=awg3
check "SUBNET-01 в: подхваченный awg2 на 10.8.3.0/24 без записи — новый 3.1 встаёт не на 10.8.3.0/24" \
    '[ "$rc" = 0 ] && [ -n "$(sn_sub awg3)" ] && [ "$(sn_sub awg3)" != 10.8.3.0/24 ]' "rc=$rc awg3=$(sn_sub awg3) $out"
unset GOAMNEZIA_SYSTEMD_DIR

# ── 8. Словарь ───────────────────────────────────────────────────────────────
section "словарь и секреты"
check "приватный ключ сервера и PSK не попали ни в один stdout/stderr прогона (кроме users.json/state)" '! grep -qF "$SRV2_PRIV" "$ALL" && ! grep -qF "$PSK2" "$ALL"'
ru=$(grep -o '^I18N\[[a-z_0-9]*\]' "$ROOT/lib/lang/ru.d/import.sh" | sort); en=$(grep -o '^I18N\[[a-z_0-9]*\]' "$ROOT/lib/lang/en.d/import.sh" | sort)
check "словарь import: ru и en совпадают ($(wc -l <<<"$ru" | tr -d ' '))" '[ "$ru" = "$en" ] && [ -n "$ru" ]'
used=$(grep -oE '\$\((t|tf) import_[a-z_0-9]+' "$ROOT/lib/import.sh" | sed 's/.* //' | sort -u)
miss=""; for k in $used; do grep -q "^I18N\[$k\]" "$ROOT/lib/lang/ru.d/import.sh" || miss="$miss $k"; done
check "все ключи t/tf модуля есть в словаре" '[ -z "$miss" ]' "нет: $miss"

DONE=1
printf '\n══ ИТОГ: пройдено %d, провалено %d, пропущено %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
