#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон lib/share.sh (временная ссылка на файл)
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_share_dryrun.sh
# Нужны: bash ≥ 5, jq, openssl, python3; curl — для настоящего скачивания (без
# него живые разделы пропускаются, а не проваливаются). Docker, systemd и nginx
# не нужны: профиль ставится в dry-run, сертификат настоящий (openssl) в
# $GOAMNEZIA_DIR/tls, а слушатель раздачи поднимается ПО-НАСТОЯЩЕМУ на петле —
# иначе проверять в этом модуле нечего: он весь про то, что файл реально
# скачивается по ссылке.
#
# Что проверяем (docs/SHARE.md):
#   отказы до старта (формат, имя, чужой пользователь, нет пира — каталог
#   раздачи не остаётся) → создание раздачи: JSON с url/path/port/expires/
#   fingerprint, порт из диапазона, права 700/600, секрета нет в meta.json →
#   настоящее скачивание curl -k: 200, байт в байт тот же .conf, заголовки
#   attachment + no-store + Server: nginx → повторное скачивание в пределах
#   срока → HEAD не считается скачиванием → неизвестный путь → 404 с телом
#   nginx → чужое имя файла при верном секрете → 404 → бан после серии неудач
#   (даже верный адрес отвечает 404) → share_list без секретов → форматы vpn,
#   qr-conf, all → GOAMNEZIA_SHARE_ONCE=1 гасит после первого скачивания →
#   share_revoke снимает немедленно (порт закрыт, каталог снят) →
#   share_revoke_all → истечение TTL гасит слушатель и стирает файл →
#   share_tick → аудит: есть share-create/share-download/share-expire/
#   share-revoke и НИ ОДНОГО секрета ни в аудите, ни в логах, ни в stderr →
#   статические проверки самого модуля (bash -n, py_compile, паритет словаря).
#
# Настоящие lib/common.sh, lib/i18n.sh, lib/docker.sh, lib/amnezia.sh,
# lib/users.sh, lib/export.sh, lib/website.sh, lib/preflight.sh, lib/share.sh.
# Ничего вне $TMP не пишем; слушатели — только на 127.0.0.1.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-share.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
ALL_ERR="$TMP/all-stderr.log"
: >"$ALL_ERR"
# Аварийный выход: показываем последний stderr и обязательно гасим всё, что
# успели поднять, — «забытый» слушатель пережил бы сам тест.
cleanup() {
    local d
    for d in "$TMP"/run/share/s-*; do
        [ -f "$d/pid" ] || continue
        kill "$(tr -d '[:space:]' <"$d/pid" 2>/dev/null)" 2>/dev/null || true
    done
    rm -rf "$TMP"
}
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; cleanup' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_DIR="$TMP/goamnezia"
export GOAMNEZIA_LOCK_DIR="$TMP/run"
export GOAMNEZIA_LOG_FILE="$TMP/goamnezia.log"
export GOAMNEZIA_AUDIT_LOG="$TMP/audit.log"
export GOAMNEZIA_NOTIFY_DIR="$TMP/run/notify"
export GOAMNEZIA_BUILD_ROOT="$TMP/build"
export GOAMNEZIA_SERVER_IP="203.0.113.10"
export GOAMNEZIA_NGINX_DIR="$TMP/nginx"
export GOAMNEZIA_SITE_ROOT="$TMP/site"
export GOAMNEZIA_LE_DIR="$TMP/letsencrypt"
export UPSTREAM_DIR="$TMP/upstream"
# Слушатель только на петле: тест не должен открывать порт наружу на машине
# разработчика. Адрес в самой ссылке при этом остаётся «внешним» (server_ip) —
# ровно как на сервере, и проверка ссылки идёт по её пути и порту.
export GOAMNEZIA_SHARE_BIND="127.0.0.1"
unset GOAMNEZIA_SHARE_TTL GOAMNEZIA_SHARE_ONCE GOAMNEZIA_SHARE_MAX_DOWNLOADS GOAMNEZIA_SHARE_DIR
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR"

# Временные файлы с ключами — в каталоге стенда (L1-19). Без переменной продукт кладёт их в /dev/shm,
# а он есть только на Linux: там они жили бы на машине прогона, и оборванный прогон оставлял бы ключи.
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"
# Петлю — мимо прокси (L1-10): CI и облачные песочницы задают HTTP(S)_PROXY, и curl к 127.0.0.1
# ушёл бы на прокси, а не к службе стенда.
export NO_PROXY="127.0.0.1,localhost,::1${NO_PROXY:+,$NO_PROXY}" no_proxy="127.0.0.1,localhost,::1${no_proxy:+,$no_proxy}"

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 700; return 0; }
skipped() { skip=$((skip + 1)); printf '  – %s (пропущено: %s)\n' "$1" "$2"; }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
is_json() { jq -e . <<<"$1" >/dev/null 2>&1; }
# Права восьмерично: сначала GNU (-c), потом BSD (-f). Не наоборот: у GNU «-f» — сведения
# о файловой системе, он печатал их перед правами, и на Linux краснела каждая проверка 600/700.
perm() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }
# run <функция> [аргументы…] → rc, out (stdout), $ERR (stderr); коды ошибок
# остаются в этой же оболочке (в $( ) они терялись бы вместе с подоболочкой).
run() {
    GA_ERR_CODE=""; GA_ERR_MSG=""; GA_LAST_ERROR_CODE=""
    "$@" >"$OUT" 2>"$ERR"; rc=$?
    out=$(<"$OUT")
    cat "$ERR" >>"$ALL_ERR"
    return 0
}
errs() { cat "$ERR" 2>/dev/null; }
jget() { jq -r "$1" <<<"$out" 2>/dev/null; }
one_line_json() { [ "$(wc -l <"$OUT" | tr -d ' ')" -le 1 ] && is_json "$out"; }

SHARE_DIR="$TMP/run/share"
SECRET_RE='^[A-Za-z0-9_-]{43}$'
ID_RE='^s-[a-f0-9]{4,8}$'
HDR="$TMP/hdr.txt"
BODY="$TMP/body.bin"

# get <порт> <путь> [ожидаемый ip] → код ответа в $code, тело в $BODY, заголовки в $HDR
code=""
get() {
    code=$(curl -sk --max-time 10 -D "$HDR" -o "$BODY" -w '%{http_code}' "https://127.0.0.1:$1$2" 2>/dev/null) || code="000"
}
head_req() {
    code=$(curl -sk --max-time 10 -I -D "$HDR" -o /dev/null -w '%{http_code}' "https://127.0.0.1:$1$2" 2>/dev/null) || code="000"
}

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
for f in lib/common.sh lib/i18n.sh lib/docker.sh lib/amnezia.sh lib/users.sh lib/export.sh \
         lib/website.sh lib/preflight.sh lib/share.sh lib/share_server.py \
         lib/lang/ru.d/share.sh lib/lang/en.d/share.sh; do
    [ -f "$ROOT/$f" ] || { printf '!! нет %s\n' "$f"; exit 2; }
done
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia users export website preflight share; do
    # shellcheck disable=SC1090
    source "$ROOT/lib/$_m.sh"
done
check "модуль подключён: share_create, share_list, share_revoke, share_revoke_all, share_tick" \
    'type share_create >/dev/null && type share_list >/dev/null && type share_revoke >/dev/null && type share_revoke_all >/dev/null && type share_tick >/dev/null'
check "словарь share подхвачен движком i18n (share_err_format)" '[ -n "${I18N[share_err_format]:-}" ]'
for _b in jq openssl python3; do
    command -v "$_b" >/dev/null 2>&1 || { printf '!! нет %s\n' "$_b"; exit 2; }
done
HAVE_CURL=0; command -v curl >/dev/null 2>&1 && HAVE_CURL=1

mkdir -p "$UPSTREAM_DIR/awg"
cp "$ROOT"/lib/upstream/awg/* "$UPSTREAM_DIR/awg/" 2>/dev/null
cp "$ROOT/lib/upstream/images.json" "$UPSTREAM_DIR/images.json" 2>/dev/null
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOC
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}, "panel": {"port": 1985}, "site": {"template_id": "", "listen": "public"},
 "options": {"drop_icmp": false}, "mcp": {"http_enabled": false, "http_port": 1986}}
EOC
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"
chmod 600 "$USERS_FILE"
mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR"
amnezia_profile_install awg2 40000 default >/dev/null 2>>"$ALL_ERR"
users_add anna "Анна" awg2 >/dev/null 2>>"$ALL_ERR"
users_add bob "Боб" awg2 >/dev/null 2>>"$ALL_ERR"
# У bob убираем пира: нужен пользователь, у которого файла для этого профиля нет
jq -c '(.users[] | select(.name == "bob") | .peers) = {}' "$USERS_FILE" >"$TMP/u.json" && mv "$TMP/u.json" "$USERS_FILE"
check "фикстура готова: профиль awg2 установлен, у anna есть пир, у bob — нет" \
    '[ -n "$(ga_config_get .profiles.awg2.server_pubkey)" ] && [ -n "$(ga_users_get anna | jq -r ".peers.awg2.privkey // empty")" ] && [ -z "$(ga_users_get bob | jq -r ".peers.awg2 // empty")" ]'
run export_conf anna awg2
CONF_TEXT="$out"
check "export_conf anna awg2 работает — есть с чем сравнивать скачанное" '[ "$rc" = 0 ] && [ -n "$CONF_TEXT" ]' "$(errs)"

# ── 1. Отказы до старта слушателя ────────────────────────────────────────────
section "отказы до старта: порт не открывается зря"
run share_create anna awg2 bogus
check "неизвестный формат → invalid_format (rc=2), stdout пуст, каталога раздач нет" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_format ] && [ -z "$out" ] && [ ! -d "$SHARE_DIR" ]' "rc=$rc code=$GA_ERR_CODE"
run share_create "../../etc/passwd" awg2 conf
check "имя с ../ → invalid_argument (rc=2): из имени собирается путь и заголовок Content-Disposition" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_argument ]' "rc=$rc code=$GA_ERR_CODE"
run share_create anna "awg2; rm -rf /" conf
check "профиль с посторонними символами → invalid_argument (rc=2)" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_argument ]' "rc=$rc code=$GA_ERR_CODE"
run share_create ghost awg2 conf
check "чужой пользователь → user_not_found (rc=3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = user_not_found ]' "rc=$rc code=$GA_ERR_CODE"
run share_create bob awg2 conf
check "нет пира в профиле → peer_not_found (rc=3), stdout пуст" \
    '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = peer_not_found ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE"
check "после неудачной сборки файла каталог раздачи не остаётся" \
    '[ -z "$(find "$SHARE_DIR" -maxdepth 1 -name "s-*" 2>/dev/null)" ]' "$(ls -la "$SHARE_DIR" 2>/dev/null)"
# A3-5: выключенному ссылку не выдаём — пира нет, файл не подключится (тот же код, что у страницы).
cp "$USERS_FILE" "$TMP/users.a35"
jq -c '(.users[] | select(.name == "anna") | .enabled) = false' "$TMP/users.a35" >"$USERS_FILE"
run share_create anna awg2 conf
check "выключенный пользователь → user_disabled (rc=3), stdout пуст, раздачи нет" \
    '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = user_disabled ] && [ -z "$out" ] && [ -z "$(find "$SHARE_DIR" -maxdepth 1 -name "s-*" 2>/dev/null)" ]' "rc=$rc code=$GA_ERR_CODE"
cp "$TMP/users.a35" "$USERS_FILE"; chmod 600 "$USERS_FILE"

# ── 2. Создание раздачи ──────────────────────────────────────────────────────
section "share_create conf"
# SEC46-02: ссылка раздачи — сам ключ человека, а argv любого процесса читает каждый локальный
# пользователь (/proc/<pid>/cmdline). Шпион jq пишет свои аргументы в журнал.
SPYBIN="$TMP/spybin"; mkdir -p "$SPYBIN"; : >"$TMP/argv.log"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\nexec "%s" "$@"\n' "$TMP/argv.log" "$(command -v jq)" >"$SPYBIN/jq"
chmod +x "$SPYBIN/jq"
PATH="$SPYBIN:$PATH" run share_create anna awg2 conf
check "share_create anna awg2 conf: rc=0, одна строка JSON" '[ "$rc" = 0 ] && one_line_json' "rc=$rc $(errs)"
S_ID=$(jget .id); S_PORT=$(jget .port); S_PATH=$(jget .path); S_URL=$(jget .url)
S_FILE=$(jget .file_name); S_FP=$(jget .fingerprint); S_EXP=$(jget .expires)
S_SECRET=$(sed -E 's#^/d-([A-Za-z0-9_-]{43})/.*#\1#' <<<"$S_PATH")
check "SEC46-02 секрет раздачи (url, path) не прошёл через argv jq, а шпион сработал" \
    '[[ "$S_SECRET" =~ $SECRET_RE ]] && [ "$(wc -l <"$TMP/argv.log" | tr -d " ")" -gt 3 ] && ! grep -qF -- "$S_SECRET" "$TMP/argv.log"' \
    "$(grep -F -- "$S_SECRET" "$TMP/argv.log" | head -3 | cut -c1-200)"
check "id вида s-<hex>" '[[ "$S_ID" =~ $ID_RE ]]' "id=$S_ID"
check "порт из диапазона 30000–49999" '[ "$S_PORT" -ge 30000 ] && [ "$S_PORT" -le 49999 ]' "port=$S_PORT"
check "путь /d-<43 символа base64url>/<имя файла>" '[[ "$S_PATH" =~ ^/d-[A-Za-z0-9_-]{43}/anna-2_0\.conf$ ]]' "path=$S_PATH"
check "секрет в пути — 43 символа base64url" '[[ "$S_SECRET" =~ $SECRET_RE ]]' "secret len=${#S_SECRET}"
check "url — https с внешним адресом и портом, БЕЗ http" \
    '[ "$S_URL" = "https://203.0.113.10:${S_PORT}${S_PATH}" ] && [[ "$S_URL" != http://* ]]' "url=$S_URL"
check "имя файла осмысленное: anna-2_0.conf (линия читаемо, без точки внутри и без имени места — A2-7, B3-6)" '[ "$S_FILE" = "anna-2_0.conf" ]' "file=$S_FILE"
# П-2: с разовым адресом — то же имя, что без него и у user-key (адрес сделал бы имя туннеля длиннее 15 знаков);
# адрес — в самом ключе. Проверку адреса заменяет заглушка: здесь важно только имя.
EP_FILE=$( ga_export_key_host_check() { printf '%s' "$1"; }
           ga_share_payload anna awg2 conf "$TMP/payload-ep" 198.51.100.20 2>/dev/null )
check "П-2 разовый адрес: файл anna-2_0.conf (без адреса, ≤15 знаков до .conf), в ключе Endpoint 198.51.100.20" \
    '[ "$EP_FILE" = "anna-2_0.conf" ] && grep -q "^Endpoint = 198.51.100.20:" "$TMP/payload-ep/anna-2_0.conf"' \
    "file=$EP_FILE $(ls "$TMP/payload-ep" 2>&1)"
check "срок: ttl=300 по умолчанию, expires в ISO-8601 UTC" \
    '[ "$(jget .ttl)" = 300 ] && [[ "$S_EXP" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]' "ttl=$(jget .ttl) exp=$S_EXP"
check "сертификат самоподписанный, отпечаток SHA-256 в ответе" \
    '[ "$(jget .cert)" = selfsigned ] && [ "$(jget .selfsigned)" = true ] && [[ "$S_FP" =~ ^([0-9A-F]{2}:){31}[0-9A-F]{2}$ ]]' "cert=$(jget .cert) fp=$S_FP"
check "отпечаток совпадает с сертификатом сайта на диске" \
    '[ "$S_FP" = "$(website_cert_fingerprint)" ]' "fp=$S_FP"
check "предупреждение про самоподписанный сертификат — в stderr, из словаря" \
    'grep -qF "$S_FP" "$ERR" && grep -q "амоподписан" "$ERR"' "$(errs)"
check "по умолчанию скачивать можно не один раз (max_downloads=0)" '[ "$(jget .max_downloads)" = 0 ]' "maxd=$(jget .max_downloads)"

SD="$SHARE_DIR/$S_ID"
check "каталог раздач 700, каталог раздачи 700" '[ "$(perm "$SHARE_DIR")" = 700 ] && [ "$(perm "$SD")" = 700 ]' "$(perm "$SHARE_DIR") $(perm "$SD")"
check "meta.json, secret и сам файл — 600" \
    '[ "$(perm "$SD/meta.json")" = 600 ] && [ "$(perm "$SD/secret")" = 600 ] && [ "$(perm "$SD/payload/$S_FILE")" = 600 ]' \
    "$(ls -la "$SD" "$SD/payload" 2>/dev/null)"
check "секрета нет в meta.json — он лежит отдельным файлом" \
    '! grep -qF -- "$S_SECRET" "$SD/meta.json"' "$(cat "$SD/meta.json")"
check "процесс раздачи живёт своей сессией и переживёт выход из меню" \
    '[ -f "$SD/pid" ] && kill -0 "$(cat "$SD/pid")" 2>/dev/null'

# ── 3. Настоящее скачивание ──────────────────────────────────────────────────
section "скачивание по ссылке (curl -k на петле)"
if [ "$HAVE_CURL" = 1 ]; then
    get "$S_PORT" "$S_PATH"
    check "GET по ссылке → 200" '[ "$code" = 200 ]' "code=$code"
    check "скачано ровно то, что отдаёт export_conf" '[ "$(cat "$BODY")" = "$CONF_TEXT" ]' "$(head -c 200 "$BODY")"
    check "заголовки: attachment с именем файла, no-store, nosniff, Server как у nginx" \
        'grep -qi "content-disposition: attachment; filename=\"anna-2_0.conf\"" "$HDR" && grep -qi "cache-control: no-store" "$HDR" && grep -qi "x-content-type-options: nosniff" "$HDR" && grep -qi "^server: nginx" "$HDR"' \
        "$(cat "$HDR")"
    get "$S_PORT" "$S_PATH"
    check "повторное скачивание в пределах срока работает (по умолчанию ссылка не одноразовая)" \
        '[ "$code" = 200 ] && [ "$(cat "$BODY")" = "$CONF_TEXT" ]' "code=$code"
    head_req "$S_PORT" "$S_PATH"
    check "HEAD отвечает 200 и не тратит скачивание" '[ "$code" = 200 ]' "code=$code"
    get "$S_PORT" "/d-$S_SECRET/anna-awg9.conf"
    check "верный секрет, но чужое имя файла → 404" '[ "$code" = 404 ]' "code=$code"
    get "$S_PORT" "/"
    check "корень → 404" '[ "$code" = 404 ]' "code=$code"
    check "тело 404 — как у nginx по умолчанию, без подсказок" \
        'grep -q "404 Not Found" "$BODY" && grep -q "<center>nginx</center>" "$BODY" && ! grep -qi "share\|goamnezia\|d-" "$BODY"' "$(cat "$BODY")"
    # Решение владельца В1 (26.09.2026): на истёкшую, снятую и чужую ссылку — «404», как у любого сайта. Заголовки
    # тоже как у 404 nginx по умолчанию: no-store/nosniff/Referrer-Policy — только у ответа с файлом.
    check "заголовки 404 — как у nginx по умолчанию: Server, Date, Content-Type, Content-Length, Connection (без no-store и nosniff)" \
        '[ "$(head -1 "$HDR" | tr -d "\r")" = "HTTP/1.1 404 Not Found" ] && [ "$(tr -d "\r" <"$HDR" | sed -n "2,\$p" | sed "/^$/d" | cut -d: -f1 | paste -sd, -)" = "Server,Date,Content-Type,Content-Length,Connection" ] && grep -qi "^server: nginx" "$HDR" && grep -qi "^content-length: 146" "$HDR"' \
        "$(cat "$HDR")"
    get "$S_PORT" "/d-$S_SECRET/anna-awg9.conf"
    check "…и тот же 404 на верный секрет с чужим именем файла (ничего не подтверждает)" \
        '[ "$code" = 404 ] && [ "$(tr -d "\r" <"$HDR" | sed -n "2,\$p" | sed "/^$/d" | cut -d: -f1 | paste -sd, -)" = "Server,Date,Content-Type,Content-Length,Connection" ]' "$(cat "$HDR")"
    run share_list
    check "share_list: два скачивания учтены, HEAD не посчитан" \
        '[ "$(jq -r --arg id "$S_ID" ".shares[] | select(.id == \$id) | .downloads" <<<"$out")" = 2 ]' "$out"
    # A4-2: выключили пользователя — живая ссылка отвечает 404 (как подписка), включили — снова отдаёт файл
    cp "$USERS_FILE" "$TMP/users.a42"
    jq -c '(.users[] | select(.name == "anna") | .enabled) = false' "$TMP/users.a42" >"$USERS_FILE"
    get "$S_PORT" "$S_PATH"; A42_GET=$code; head_req "$S_PORT" "$S_PATH"; A42_HEAD=$code
    check "A4-2 выключенный пользователь: GET и HEAD по живой ссылке → 404, тело как у nginx" \
        '[ "$A42_GET" = 404 ] && [ "$A42_HEAD" = 404 ] && grep -q "<center>nginx</center>" "$BODY"' "get=$A42_GET head=$A42_HEAD"
    cp "$TMP/users.a42" "$USERS_FILE"; chmod 600 "$USERS_FILE"
    get "$S_PORT" "$S_PATH"
    check "A4-2 включили — та же ссылка снова отдаёт файл (срок не вышел)" '[ "$code" = 200 ] && [ "$(cat "$BODY")" = "$CONF_TEXT" ]' "code=$code"
    run share_list
    check "A4-2 отказ выключенному не считается скачиванием" \
        '[ "$(jq -r --arg id "$S_ID" ".shares[] | select(.id == \$id) | .downloads" <<<"$out")" = 3 ]' "$out"
    check "A4-2 процесс раздачи знает учёт пользователей (--users)" \
        'ps -o args= -p "$(cat "$SD/pid")" | grep -qF -- "--users $USERS_FILE"' "$(ps -o args= -p "$(cat "$SD/pid")" 2>&1)"
else
    skipped "живое скачивание" "нет curl"
fi

# ── 4. share_list без секретов ───────────────────────────────────────────────
section "share_list"
run share_list
check "share_list: одна строка JSON, count=1" '[ "$rc" = 0 ] && one_line_json && [ "$(jget .count)" = 1 ]' "$out"
check "в списке есть id, пользователь, профиль, формат, порт, остаток срока" \
    '[ "$(jq -r ".shares[0].id" <<<"$out")" = "$S_ID" ] && [ "$(jq -r ".shares[0].name" <<<"$out")" = anna ] && [ "$(jq -r ".shares[0].profile" <<<"$out")" = awg2 ] && [ "$(jq -r ".shares[0].format" <<<"$out")" = conf ] && [ "$(jq -r ".shares[0].port" <<<"$out")" = "$S_PORT" ] && [ "$(jq -r ".shares[0].seconds_left" <<<"$out")" -gt 0 ]' "$out"
check "в списке НЕТ ни секрета, ни пути, ни ссылки" \
    '! grep -qF -- "$S_SECRET" <<<"$out" && [ "$(jq -r ".shares[0].path // \"\"" <<<"$out")" = "" ] && [ "$(jq -r ".shares[0].url // \"\"" <<<"$out")" = "" ]' "$out"

# ── 5. Бан по неудачным попыткам ─────────────────────────────────────────────
# Отдельная раздача: бан живёт в её процессе и после проверки закрывает ссылку
# целиком — портить ради этого раздачу, которую мы ещё смотрим, незачем.
section "бан после серии неудач"
if [ "$HAVE_CURL" = 1 ]; then
    run share_create anna awg2 conf
    B_ID=$(jget .id); B_PORT=$(jget .port); B_PATH=$(jget .path)
    check "вторая раздача поднялась на своём порту" '[ "$rc" = 0 ] && [ "$B_PORT" != "$S_PORT" ]' "rc=$rc $(errs)"
    get "$B_PORT" "$B_PATH"
    check "до бана верный адрес отдаёт файл" '[ "$code" = 200 ]' "code=$code"
    for _i in $(seq 1 10); do get "$B_PORT" "/d-$(printf 'A%.0s' $(seq 1 43))/x.conf"; done
    check "каждая попытка мимо — 404 (не 401 и не 429: подбирающий не должен знать, что его заметили)" '[ "$code" = 404 ]' "code=$code"
    get "$B_PORT" "$B_PATH"
    check "после 10 неудач с одного IP забанен даже верный адрес → 404" '[ "$code" = 404 ]' "code=$code"
    run share_list
    check "бан не тронул соседнюю раздачу — она в списке живая" \
        '[ "$(jq -r --arg id "$S_ID" ".shares[] | select(.id == \$id) | .seconds_left" <<<"$out")" -gt 0 ]' "$out"
    if [ "$HAVE_CURL" = 1 ]; then
        get "$S_PORT" "$S_PATH"
        check "и продолжает отдавать файл (у каждой раздачи свой счётчик неудач)" '[ "$code" = 200 ]' "code=$code"
    fi
    run share_revoke "$B_ID"
    check "раздача с баном снята" '[ "$rc" = 0 ]' "$(errs)"
else
    skipped "бан по неудачам" "нет curl"
fi

# ── 6. Форматы ───────────────────────────────────────────────────────────────
section "форматы файла"
GA_SHARE_QUIET=1 run share_create anna awg2 vpn
V_ID=$(jget .id); V_PORT=$(jget .port); V_PATH=$(jget .path)
check "формат vpn: имя anna-2_0.vpn" '[ "$rc" = 0 ] && [ "$(jget .file_name)" = "anna-2_0.vpn" ]' "rc=$rc $(errs)"
# NC-11: экран SSH говорит о ссылке сам — минутами и временем сервера; модуль с GA_SHARE_QUIET=1 не повторяет
# «создана: порт, срок N секунд» и «сверьте отпечаток», а ответ тот же
check "NC-11: GA_SHARE_QUIET=1 — без «срок N секунд» и «Сверьте отпечаток» в stderr, JSON полный" \
    '! grep -q "секунд" "$ERR" && ! grep -q "Сверьте отпечаток" "$ERR" && [ -n "$(jget .url)" ] && [ "$(jget .ttl)" = 300 ]' "$(errs)"
if [ "$HAVE_CURL" = 1 ]; then
    get "$V_PORT" "$V_PATH"
    check "скачанный .vpn — это ключ приложения Amnezia (vpn://…)" \
        '[ "$code" = 200 ] && grep -q "^vpn://" "$BODY"' "code=$code $(head -c 80 "$BODY")"
fi
run share_revoke "$V_ID"

# archive_list <файл> — имена внутри архива раздачи, zip или tar.gz (L1-08): продукт собирает zip, если
# есть zip, иначе tar.gz, а на Linux бывает zip без unzip — и tar -tzf на zip-архиве ничего не видел.
archive_list() {
    python3 -c 'import sys, zipfile, tarfile
p = sys.argv[1]
names = zipfile.ZipFile(p).namelist() if zipfile.is_zipfile(p) else tarfile.open(p).getnames()
print("\n".join(names))' "$1" 2>/dev/null
}
if command -v qrencode >/dev/null 2>&1; then
    run share_create anna awg2 qr-conf
    Q_ID=$(jget .id); Q_PORT=$(jget .port); Q_PATH=$(jget .path)
    check "формат qr-conf: PNG с именем anna-2_0-qr.png" \
        '[ "$rc" = 0 ] && [ "$(jget .file_name)" = "anna-2_0-qr.png" ]' "rc=$rc $(errs)"
    if [ "$HAVE_CURL" = 1 ]; then
        get "$Q_PORT" "$Q_PATH"
        check "скачанный файл — настоящий PNG (сигнатура)" \
            '[ "$code" = 200 ] && [ "$(head -c 4 "$BODY" | od -An -tx1 | tr -d " ")" = "89504e47" ]' "code=$code"
    fi
    run share_revoke "$Q_ID"
    run share_create anna awg2 all
    A_ID=$(jget .id); A_PORT=$(jget .port); A_PATH=$(jget .path)
    check "формат all: один архив (zip или tar.gz)" \
        '[ "$rc" = 0 ] && [[ "$(jget .file_name)" =~ ^anna-2_0\.(zip|tar\.gz)$ ]]' "rc=$rc file=$(jget .file_name) $(errs)"
    if [ "$HAVE_CURL" = 1 ]; then
        get "$A_PORT" "$A_PATH"
        check "архив скачивается и внутри есть .conf и .vpn" \
            '[ "$code" = 200 ] && lst=$(archive_list "$BODY") && grep -q "anna-2_0.conf" <<<"$lst" && grep -q "anna-2_0.vpn" <<<"$lst"' "code=$code"
    fi
    run share_revoke "$A_ID"
else
    skipped "форматы qr-conf и all" "нет qrencode"
fi

# ── 7. Одноразовая ссылка ────────────────────────────────────────────────────
section "GOAMNEZIA_SHARE_ONCE=1"
if [ "$HAVE_CURL" = 1 ]; then
    GOAMNEZIA_SHARE_ONCE=1 run share_create anna awg2 conf
    O_ID=$(jget .id); O_PORT=$(jget .port); O_PATH=$(jget .path)
    check "в ответе max_downloads=1" '[ "$rc" = 0 ] && [ "$(jget .max_downloads)" = 1 ]' "rc=$rc $(errs)"
    get "$O_PORT" "$O_PATH"
    check "первое скачивание проходит" '[ "$code" = 200 ]' "code=$code"
    # Второй запрос СРАЗУ, не дожидаясь гашения. Гашение по «предел выбран» —
    # отдельный шаг, и он занимает время: на стенде в это окно входил второй
    # запрос и получал ВТОРУЮ копию ключа (DEFECTS §4.7). Одноразовая ссылка
    # обязана отдать файл ровно один раз, чего бы ни успело случиться.
    get "$O_PORT" "$O_PATH"
    check "второе скачивание сразу же — отказ, а не вторая копия" \
        '[ "$code" = 404 ] || [ "$code" = 000 ]' "code=$code"
    for _i in $(seq 1 30); do [ -d "$SHARE_DIR/$O_ID" ] || break; sleep 0.1; done
    check "после единственного скачивания слушатель погашен, каталог и файл сняты" \
        '[ ! -d "$SHARE_DIR/$O_ID" ]' "$(ls -la "$SHARE_DIR" 2>/dev/null)"
    get "$O_PORT" "$O_PATH"
    check "порт больше не отвечает" '[ "$code" != 200 ]' "code=$code"
else
    skipped "GOAMNEZIA_SHARE_ONCE=1" "нет curl"
fi

# ── 8. Немедленный отзыв ─────────────────────────────────────────────────────
section "share_revoke и share_revoke_all"
R_PID=$(cat "$SD/pid" 2>/dev/null)
run share_revoke "$S_ID"
check "share_revoke: rc=0, JSON {id, revoked}" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .id)" = "$S_ID" ] && [ "$(jget .revoked)" = 1 ]' "$out"
check "процесс раздачи погашен, каталог и файл сняты немедленно" \
    '! kill -0 "$R_PID" 2>/dev/null && [ ! -d "$SD" ]' "pid=$R_PID"
if [ "$HAVE_CURL" = 1 ]; then
    get "$S_PORT" "$S_PATH"
    check "ссылка перестала работать сразу (порт закрыт)" '[ "$code" != 200 ]' "code=$code"
fi
run share_revoke "$S_ID"
check "повторный отзыв → share_not_found (rc=3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = share_not_found ]' "rc=$rc code=$GA_ERR_CODE"
run share_revoke "не-идентификатор"
check "мусор вместо id → invalid_share_id (rc=2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_share_id ]' "rc=$rc code=$GA_ERR_CODE"

run share_create anna awg2 conf
run share_create anna awg2 vpn
run share_revoke_all
check "share_revoke_all снял обе раздачи" '[ "$rc" = 0 ] && [ "$(jget .revoked)" = 2 ]' "$out"
run share_list
check "после revoke_all список пуст" '[ "$(jget .count)" = 0 ]' "$out"

# ── 8a. Живая раздача НЕ держит общий замок ─────────────────────────────────
# Самая дорогая находка дня, и найдена она была только на живом сервере: процесс
# раздачи наследовал описатель файла замка и держал flock, пока жил, а живёт он
# столько же, сколько действует ссылка. После КАЖДОЙ выданной ссылки бот, панель,
# агент и меню пять минут отвечали «другое действие уже выполняется».
#
# Проверяем не косвенно, а прямо: пока слушатель работает, замок обязан браться.
section "живая раздача не держит общий замок"
L_ID=$(jget .id); L_PID=""
# ПОД ЗАМКОМ — как это делает мост: именно там процесс наследовал описатель.
# Без замка дефект не воспроизводится, и проверка была бы зелёной на сломанном
# коде, то есть бесполезной.
run ga_with_config_lock share_create anna awg2 conf
L_ID=$(jget .id)
L_PID=$(cat "$SHARE_DIR/$L_ID/pid" 2>/dev/null || true)
check "раздача создана и слушатель работает" \
    '[ -n "$L_PID" ] && kill -0 "$L_PID" 2>/dev/null' "id=$L_ID pid=$L_PID"

# Смотрим на сам инвариант: держит ли процесс раздачи ОПИСАТЕЛЬ файла замка.
# flock как утилита есть не везде (на macOS её нет), а описатели видны всегда.
# Именно это и было дефектом: python3 раздачи висел на config.lock.
: > "$GOAMNEZIA_LOCK_DIR/config.lock"
# Пропуск честный, а не маскировка: без flock ga_with_config_lock замок НЕ БЕРЁТ
# (утилиты нет — нечего и наследовать), поэтому на такой машине дефект
# невоспроизводим в принципе, и зелёная строка здесь ничего не значила бы. На
# сервере, где flock есть, проверка работает и ловит возврат регрессии.
if ! command -v flock >/dev/null 2>&1; then
    skipped "описатели раздачи" "нет flock — замок не берётся, дефект невоспроизводим"
elif command -v lsof >/dev/null 2>&1; then
    # Спрашиваем по САМОМУ ФАЙЛУ: так виден любой держатель, даже если номер
    # процесса в pid-файле не совпал с тем, что реально слушает.
    holders=$(lsof "$GOAMNEZIA_LOCK_DIR/config.lock" 2>/dev/null | grep -c "python" || true)
    check "процесс раздачи НЕ держит описатель файла замка" '[ "${holders:-0}" = 0 ]' \
        "$(lsof "$GOAMNEZIA_LOCK_DIR/config.lock" 2>/dev/null | tail -n +2 | head -3)"
else
    skipped "описатели раздачи" "нет lsof"
fi

# И само действие продукта, которое берёт замок, проходит.
run share_revoke "$L_ID"
check "действие под замком проходит, пока раздача жива (до отзыва)" '[ "$rc" = 0 ]' "rc=$rc code=$GA_ERR_CODE"

# ── 9. Истечение срока ───────────────────────────────────────────────────────
# Нижнюю границу срока трогаем ТОЛЬКО здесь и только в тесте: ждать минуту в
# прогоне нельзя, а проверить, что слушатель гаснет сам, обязательно.
section "истечение TTL"
GA_SHARE_TTL_MIN=2
GOAMNEZIA_SHARE_TTL=2 run share_create anna awg2 conf
E_ID=$(jget .id); E_PORT=$(jget .port); E_PATH=$(jget .path)
E_PID=$(cat "$SHARE_DIR/$E_ID/pid" 2>/dev/null)
check "раздача на 2 секунды создана" '[ "$rc" = 0 ] && [ "$(jget .ttl)" = 2 ]' "rc=$rc $(errs)"
# Ждём ИМЕННО ТО, что проверяем, — гибель процесса. Прежде цикл ждал
# исчезновения каталога, а проверял процесс: между этими двумя событиями есть
# зазор, и под нагрузкой проверка успевала в него попасть, из-за чего набор
# краснел в общем прогоне и был зелёным в одиночку. Запас 30 секунд: срок здесь
# две, всё остальное — планировщик занятой машины.
check "номер процесса раздачи записан" '[ -n "$E_PID" ]' "pid=$E_PID"
for _i in $(seq 1 300); do [ -n "$E_PID" ] && kill -0 "$E_PID" 2>/dev/null || break; sleep 0.1; done
check "по истечении срока процесс погашен сам, без таймера" '! kill -0 "$E_PID" 2>/dev/null' "pid=$E_PID"
check "каталог раздачи убран вместе с процессом" '[ ! -d "$SHARE_DIR/$E_ID" ]' "$(ls "$SHARE_DIR" 2>/dev/null | tr "\n" " ")"
check "каталог, файл и секрет стёрты" '[ ! -d "$SHARE_DIR/$E_ID" ]' "$(ls -la "$SHARE_DIR" 2>/dev/null)"
if [ "$HAVE_CURL" = 1 ]; then
    get "$E_PORT" "$E_PATH"
    check "по истёкшей ссылке файла нет" '[ "$code" != 200 ]' "code=$code"
fi
run share_list
check "истёкшая раздача пропала из share_list" '[ "$(jget .count)" = 0 ]' "$out"
GA_SHARE_TTL_MIN=60
run share_create anna awg2 conf
check "GOAMNEZIA_SHARE_TTL вне границ прижимается к границе с предупреждением" \
    'GOAMNEZIA_SHARE_TTL=99999 ga_share_ttl 2>/dev/null | grep -qx 3600 && GOAMNEZIA_SHARE_TTL=abc ga_share_ttl 2>/dev/null | grep -qx 300'
run share_revoke_all

# ── 9a. Локальный файрвол: открыли — закрыли ─────────────────────────────────
# Раздача живёт минуты, а правило файрвола пережило бы её и накапливалось бы по
# одному на каждую ссылку. Поэтому проверяем ПАРУ: allow при создании и delete
# allow при уборке, причём тем же инструментом, каким открывали.
section "правило файрвола снимается вместе с раздачей"
export GOAMNEZIA_UFW_FIXTURE="$ROOT/tests/fixtures/preflight/ufw_active_no_web.txt"
run share_create anna awg2 conf
F_ID=$(jget .id); F_PORT=$(jget .port)
check "порт открыт через ufw (в dry-run — строка DRY)" \
    '[ "$rc" = 0 ] && grep -qF "DRY: ufw allow ${F_PORT}/tcp" "$ERR"' "$(grep DRY "$ERR" | head -3)"
check "в meta.json записано, ЧЕМ открыли — иначе закрывать будет нечем" \
    '[ "$(jq -r .fw "$SHARE_DIR/$F_ID/meta.json")" = ufw ]' "$(jq -c .fw "$SHARE_DIR/$F_ID/meta.json" 2>/dev/null)"
run share_revoke "$F_ID"
check "при отзыве правило снято тем же ufw" \
    'grep -qF "DRY: ufw delete allow ${F_PORT}/tcp" "$ERR"' "$(grep DRY "$ERR" | head -3)"
unset GOAMNEZIA_UFW_FIXTURE

# ── 10. Уборка таймером ──────────────────────────────────────────────────────
section "share_tick"
run share_tick
check "share_tick: одна строка JSON {removed, active}" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .active)" = 0 ] && [[ "$(jget .removed)" =~ ^[0-9]+$ ]]' "$out"
# Обломок неудачного старта (каталог без meta.json) уборка тоже забирает
mkdir -p "$SHARE_DIR/s-dead" && run share_tick
check "каталог-обломок без meta.json убран" '[ ! -d "$SHARE_DIR/s-dead" ] && [ "$(jget .removed)" -ge 1 ]' "$out"

# ── 10a. Предел одновременных раздач ─────────────────────────────────────────
# Раньше предела не было вовсе: каждое нажатие «получить файлом» поднимало ещё
# один процесс, ещё один порт и ещё одно правило файрвола, и остановить это
# было нечем.
section "предел одновременных раздач"
run share_revoke_all
export GOAMNEZIA_SHARE_MAX_ACTIVE=2
run share_create anna awg2 conf
L1=$(jget .id)
run share_create anna awg2 conf
L2=$(jget .id)
check "две раздачи в пределах лимита созданы" \
    '[[ "$L1" =~ $ID_RE ]] && [[ "$L2" =~ $ID_RE ]] && [ "$L1" != "$L2" ]' "l1=$L1 l2=$L2"
L_BEFORE=$(find "$SHARE_DIR" -maxdepth 1 -name 's-*' 2>/dev/null | wc -l | tr -d ' ')
run share_create anna awg2 conf
check "третья раздача на пределе → share_limit (rc 2), stdout пуст" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = share_limit ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE out=$out"
check "отказ внятный: назван предел и сказано, что делать" \
    'grep -q "2" "$ERR" && { grep -q "share-revoke" "$ERR" || grep -q "Активные раздачи" "$ERR"; }' "$(errs)"
check "на отказе ни каталога, ни слушателя не прибавилось" \
    '[ "$(find "$SHARE_DIR" -maxdepth 1 -name "s-*" 2>/dev/null | wc -l | tr -d " ")" = "$L_BEFORE" ]' \
    "$(ls "$SHARE_DIR" 2>/dev/null)"
run share_revoke "$L1"
run share_create anna awg2 conf
check "после снятия одной раздачи место освободилось" '[ "$rc" = 0 ]' "rc=$rc $(errs)"
run share_revoke_all
unset GOAMNEZIA_SHARE_MAX_ACTIVE
check "мусор и ноль в GOAMNEZIA_SHARE_MAX_ACTIVE → умолчание 10, разумное значение принимается" \
    '[ "$(GOAMNEZIA_SHARE_MAX_ACTIVE=abc ga_share_max_active)" = 10 ] && [ "$(GOAMNEZIA_SHARE_MAX_ACTIVE=0 ga_share_max_active)" = 10 ] && [ "$(GOAMNEZIA_SHARE_MAX_ACTIVE=999 ga_share_max_active)" = 10 ] && [ "$(GOAMNEZIA_SHARE_MAX_ACTIVE=3 ga_share_max_active)" = 3 ]'

# ── 10b. Уборка не рвёт идущую отдачу ────────────────────────────────────────
# Раньше ga_share_sweep сносил каталог раздачи, пока файл ещё ехал клиенту:
# истёк TTL — уборка убила процесс и сделала rm -rf, а человек получил
# обрезанный .conf и считал, что продукт выдал битый ключ.
#
# Просрочиваем раздачу правкой meta.json: сторож ВНУТРИ процесса прочитал срок
# при старте и правки не увидит, поэтому снести каталог может только уборка —
# проверяем ровно её, без гонок со временем.
section "уборка не рвёт идущую отдачу"
run share_create anna awg2 conf
B_ID=$(jget .id); BD="$SHARE_DIR/$B_ID"; B_PID=$(cat "$BD/pid" 2>/dev/null)
jq '.expires_epoch = 1' "$BD/meta.json" >"$TMP/meta.tmp" && mv "$TMP/meta.tmp" "$BD/meta.json" && chmod 600 "$BD/meta.json"
printf '%s\n' "$(date -u +%s)" >"$BD/busy"          # файл прямо сейчас едет клиенту
run ga_share_sweep
check "просроченная раздача с идущей отдачей уборкой НЕ сносится" \
    '[ -d "$BD" ] && kill -0 "$B_PID" 2>/dev/null && [ "$out" = 0 ]' \
    "removed=$out dir=$([ -d "$BD" ] && echo есть || echo нет)"
run share_list
check "и share_list её тоже не сносит (уборку зовёт каждая функция модуля)" \
    '[ -d "$BD" ] && kill -0 "$B_PID" 2>/dev/null'
rm -f "$BD/busy"
run ga_share_sweep
check "отдача кончилась — просроченная раздача убирается как прежде" \
    '[ ! -d "$BD" ] && ! kill -0 "$B_PID" 2>/dev/null && [ "$out" -ge 1 ]' "removed=$out"

run share_create anna awg2 conf
C_ID=$(jget .id); CD="$SHARE_DIR/$C_ID"; C_PID=$(cat "$CD/pid" 2>/dev/null)
jq '.expires_epoch = 1' "$CD/meta.json" >"$TMP/meta.tmp" && mv "$TMP/meta.tmp" "$CD/meta.json" && chmod 600 "$CD/meta.json"
printf '%s\n' "$(( $(date -u +%s) - GA_SHARE_DRAIN_MAX - 10 ))" >"$CD/busy"
run ga_share_sweep
check "маркер отдачи от убитого процесса не делает раздачу бессмертной" \
    '[ ! -d "$CD" ] && ! kill -0 "$C_PID" 2>/dev/null' "removed=$out"

# Вторая половина починки — в самом процессе раздачи: гашение и затирание файла
# обязаны дождаться начатой отдачи. Проверяем напрямую, без гонки на секундах.
cat >"$TMP/drain_check.py" <<'PYEOF'
"""Начатая отдача дорезается: busy появляется и снимается, wipe ждёт."""
import os, sys, json, time, threading, tempfile

sys.path.insert(0, os.environ["SHARE_LIB"])
import share_server as ss


class Null(object):
    def info(self, text): pass
    def warn(self, text): pass
    def error(self, text): pass
    def write(self, action, details): pass


d = tempfile.mkdtemp()
os.makedirs(os.path.join(d, "payload"))
name = "x.conf"
with open(os.path.join(d, "payload", name), "w", encoding="utf-8") as fh:
    fh.write("payload" * 200)
with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
    json.dump({"id": "s-test", "file_name": name,
               "expires_epoch": time.time() + 60, "max_downloads": 0}, fh)
with open(os.path.join(d, "secret"), "w", encoding="utf-8") as fh:
    fh.write("A" * 43)

share = ss.Share(d, Null(), Null())
assert share.valid() == [], share.valid()

busy = os.path.join(d, "busy")
share.begin_send()
assert os.path.exists(busy), "маркер busy не появился на время отдачи"
assert share.wait_idle(0.2) is False, "wait_idle вернулся, пока отдача идёт"

held = 0.6
threading.Thread(target=lambda: (time.sleep(held), share.end_send()), daemon=True).start()
started = time.monotonic()
share.wipe("ttl")
spent = time.monotonic() - started
assert spent >= held - 0.1, "wipe не дождался конца отдачи (%.2f с)" % spent
assert not os.path.exists(busy), "маркер busy не снят"
assert not os.path.isdir(d), "каталог раздачи не снят"
print("ok")
PYEOF
export SHARE_LIB="$ROOT/lib"
check "share_server: busy ставится на время отдачи, а wipe её дожидается" \
    "python3 '$TMP/drain_check.py'" "$(python3 "$TMP/drain_check.py" 2>&1 | tail -3)"
unset SHARE_LIB
run share_revoke_all

# ── 10c. Уборка щадит фазу запуска; идентификатор занимается атомарно ────────
# Две находки охоты по пути ключей (docs/DEFECTS.md §1b):
#   1b.6  раздача рождается в три приёма (каталог → файл и meta.json → процесс
#         с pid), и до последнего приёма каталог выглядел для уборки мёртвым:
#         нет meta.json — «обломок», есть meta.json без живого pid — «умерла».
#         А уборку зовёт КАЖДАЯ публичная функция модуля, поэтому параллельный
#         share-list сносил каталог из-под рождающейся раздачи вместе с уже
#         собранным файлом ключа;
#   1b.12 проверка занятости идентификатора и занятие каталога были разнесены,
#         а занимал каталог mkdir -p, который на существующем молчит: два
#         одновременных создания получали один id, и второй ложился в чужую
#         раздачу — с чужим секретом и общим концом по первому же отзыву.
section "уборка щадит фазу запуска, идентификатор занимается атомарно"

# Первая фаза: файл ключа собран, meta.json ещё нет.
mkdir -p "$SHARE_DIR/s-boot/payload"
printf 'PrivateKey = SECRETKEY\n' >"$SHARE_DIR/s-boot/payload/x.conf"
date -u +%s >"$SHARE_DIR/s-boot/starting"
run ga_share_sweep
check "1b.6 рождающаяся раздача (meta.json ещё нет) уборкой не сносится" \
    '[ -f "$SHARE_DIR/s-boot/payload/x.conf" ] && [ "$out" = 0 ]' "removed=$out"
run share_list
check "1b.6 и другие функции модуля её не сносят (уборку зовут все)" \
    '[ -f "$SHARE_DIR/s-boot/payload/x.conf" ]' "$(ls "$SHARE_DIR" 2>/dev/null | tr '\n' ' ')"
# Вторая фаза: meta.json записан, слушатель ещё не написал pid.
jq -nc --argjson e "$(( $(date -u +%s) + 300 ))" \
    '{id:"s-boot", port:65000, bind:"0.0.0.0", fw:"none", expires_epoch:$e, max_downloads:0}' \
    >"$SHARE_DIR/s-boot/meta.json"
run ga_share_sweep
check "1b.6 раздача с meta.json, но ещё без pid, тоже переживает уборку" \
    '[ -d "$SHARE_DIR/s-boot" ] && [ "$out" = 0 ]' "removed=$out"
# Маркер состарился — слушатель так и не встал, это настоящий обломок.
printf '%s\n' "$(( $(date -u +%s) - GA_SHARE_START_GRACE - 10 ))" >"$SHARE_DIR/s-boot/starting"
run ga_share_sweep
check "1b.6 маркер запуска состарился — обломок убран, каталог не бессмертен" \
    '[ ! -d "$SHARE_DIR/s-boot" ] && [ "$out" -ge 1 ]' "removed=$out"

run share_create anna awg2 conf
S_ID=$(jget .id)
check "1b.6 после удачного старта маркер запуска снят" \
    '[ -n "$S_ID" ] && [ ! -f "$SHARE_DIR/$S_ID/starting" ]' "id=$S_ID"
run ga_share_sweep
check "1b.6 живую раздачу уборка по-прежнему не трогает" \
    '[ -d "$SHARE_DIR/$S_ID" ] && [ "$out" = 0 ]' "removed=$out"

# ── 10d. Экран раздач без сырых строк оболочки ───────────────────────────────
# Находка живого стенда: «Пользователи → s) Активные раздачи» при живой раздаче
# печатал поверх списка строку самой ОБОЛОЧКИ
#     lib/share.sh: line 592: /run/goamnezia/share/s-XXXX/starting: No such file
# Причина — не гонка с уборщиком, а обычный ход дел: после удачного старта
# маркер starting снимают, и ga_share_starting читает файл, которого больше нет.
# Ошибку ПЕРЕНАПРАВЛЕНИЯ печатает сама оболочка ДО запуска команды, поэтому
# `2>/dev/null` у tr её не гасил — гасят только фигурные скобки вокруг всей
# конструкции. Проверяем не текст сообщения, а САМ ФАКТ: stderr модуля на
# обычном пути обязан быть пуст, иначе любая строка уедет человеку на экран
# мимо словаря (§0 договора: ни одной видимой строки вне t/tf).
: >"$ERR"
run ga_share_sweep
check "уборка при живой раздаче (маркер запуска уже снят) не пишет в stderr ни строки" \
    '[ ! -s "$ERR" ]' "$(errs)"
run share_list
check "экран «Активные раздачи» чист: сырых строк оболочки в списке нет" \
    '[ ! -s "$ERR" ] && [ "$(jget .count)" -ge 1 ]' "$(errs)"
check "и в общем stderr прогона нет ни одного «No such file or directory»" \
    '! grep -q "No such file or directory" "$ALL_ERR"' \
    "$(grep -n "No such file or directory" "$ALL_ERR" | head -3)"
run share_revoke "$S_ID"

# Каталог раздачи снимают и уборщик, и сам процесс отдачи, поэтому маркеры
# читают по пути, которого может уже не быть. Ответ обязан быть осмысленным
# («маркера нет» = rc 1), а не молчаливым нулём: ноль означал бы «маркер
# поставлен в 1970», и рождающаяся раздача читалась бы как протухшая.
GONE="$SHARE_DIR/s-gone"
rm -rf "$GONE"
run ga_share_starting "$GONE"
check "ga_share_starting на исчезнувшем каталоге: rc 1 и молча" \
    '[ "$rc" = 1 ] && [ ! -s "$ERR" ]' "rc=$rc $(errs)"
run ga_share_busy "$GONE"
check "ga_share_busy на исчезнувшем каталоге: rc 1 и молча" \
    '[ "$rc" = 1 ] && [ ! -s "$ERR" ]' "rc=$rc $(errs)"
run ga_share_alive "$GONE"
check "ga_share_alive на исчезнувшем каталоге: rc 1 и молча" \
    '[ "$rc" = 1 ] && [ ! -s "$ERR" ]' "rc=$rc $(errs)"
run ga_share_kill "$GONE"
check "ga_share_kill идемпотентна: rc 0 и молча (гасить нечего)" \
    '[ "$rc" = 0 ] && [ ! -s "$ERR" ]' "rc=$rc $(errs)"
# Каталог есть, а маркера в нём нет — ровно то состояние живой раздачи, на
# котором вылезала строка со стенда.
mkdir -p "$GONE"
run ga_share_starting "$GONE"
check "каталог есть, маркера запуска нет: rc 1 и молча — это и был случай со стенда" \
    '[ "$rc" = 1 ] && [ ! -s "$ERR" ]' "rc=$rc $(errs)"
printf '   \n' >"$GONE/starting"
run ga_share_starting "$GONE"
check "пустой маркер запуска — не «раздача из 1970», а rc 1" \
    '[ "$rc" = 1 ] && [ ! -s "$ERR" ]' "rc=$rc $(errs)"
printf 'мусор\n' >"$GONE/busy"
run ga_share_busy "$GONE"
check "нечисло в маркере отдачи — rc 1, а не арифметика по мусору" \
    '[ "$rc" = 1 ] && [ ! -s "$ERR" ]' "rc=$rc $(errs)"
# Затирание файла ключа в ga_share_drop читает и пишет по тому же пути: файл
# мог исчезнуть между `[ -f ]` и перезаписью, и там были те же голые
# перенаправления.
mkdir -p "$GONE/payload"
printf 'PrivateKey = SECRETKEY\n' >"$GONE/payload/x.conf"
run ga_share_drop "$GONE" revoked
check "ga_share_drop снимает каталог молча, без сырых строк оболочки" \
    '[ "$rc" = 0 ] && [ ! -d "$GONE" ] && [ ! -s "$ERR" ]' "rc=$rc $(errs)"

# Гонка: генератор всегда предлагает один и тот же id, а каталог уже занят
# «чужой» раздачей. Занять его повторно нельзя ни при каких условиях.
_orig_newid=$(declare -f ga_share_new_id)
ga_share_new_id() { printf 's-race'; }
mkdir -p "$SHARE_DIR/s-race"
printf 'чужой секрет' >"$SHARE_DIR/s-race/secret"
# Чужая раздача ровно в той же фазе, что и наша: каталог занят, файл ещё
# собирается. Именно так выглядит настоящая гонка двух share_create.
date -u +%s >"$SHARE_DIR/s-race/starting"
run share_create anna awg2 conf
eval "$_orig_newid"
check "1b.12 занятый идентификатор не переиспользуется: отказ write_failed" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = write_failed ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE out=$out"
check "1b.12 чужая раздача цела: её секрет на месте, meta.json не подложен" \
    '[ "$(cat "$SHARE_DIR/s-race/secret" 2>/dev/null)" = "чужой секрет" ] && [ ! -f "$SHARE_DIR/s-race/meta.json" ]' \
    "$(ls "$SHARE_DIR/s-race" 2>/dev/null | tr '\n' ' ')"
rm -rf "$SHARE_DIR/s-race"
run share_revoke_all

# ── 11. Аудит и логи ─────────────────────────────────────────────────────────
section "аудит: события есть, секретов нет"
check "аудит — построчный JSON: каждая строка разбирается и несёт ts, iface, action" \
    'jq -e -s "all(has(\"ts\") and has(\"iface\") and has(\"action\"))" "$AUDIT_LOG" >/dev/null' "$(tail -2 "$AUDIT_LOG")"
check "в аудите есть share-create с id, пользователем, профилем и портом" \
    'jq -e -s "map(select(.action == \"share-create\")) | length > 0 and (.[0].details | test(\"user=anna\") and test(\"profile=awg2\") and test(\"port=\"))" "$AUDIT_LOG" >/dev/null' "$(grep share "$AUDIT_LOG" | head -3)"
if [ "$HAVE_CURL" = 1 ]; then
    # Журнал — построчный JSON одного формата на весь продукт (CONTRACT §7.1):
    # разбираем полем, а не подстрокой.
    check "в аудите есть share-download с id и адресом скачавшего" \
        'jq -e -s "map(select(.action == \"share-download\")) | length > 0 and (.[0].details | test(\"id=s-[a-f0-9]+ ip=127\\\\.0\\\\.0\\\\.1\"))" "$AUDIT_LOG" >/dev/null' "$(grep share-download "$AUDIT_LOG" | head -2)"
fi
check "в аудите есть share-revoke и share-expire" \
    'grep -q "share-revoke" "$AUDIT_LOG" && grep -q "share-expire" "$AUDIT_LOG"' "$(grep -c share "$AUDIT_LOG")"
check "в аудите НЕТ ни одного секрета и ни одного пути /d-…" \
    '! grep -qF -- "$S_SECRET" "$AUDIT_LOG" && ! grep -qE "/d-[A-Za-z0-9_-]{43}" "$AUDIT_LOG"' "$(grep -oE "/d-[A-Za-z0-9_-]{10,}" "$AUDIT_LOG" | head -3)"
check "секрета нет и в общем логе продукта" \
    '[ ! -f "$GOAMNEZIA_LOG_FILE" ] || { ! grep -qF -- "$S_SECRET" "$GOAMNEZIA_LOG_FILE" && ! grep -qE "/d-[A-Za-z0-9_-]{43}" "$GOAMNEZIA_LOG_FILE"; }'
check "секрета нет в stderr модуля (его копируют в чаты и тикеты)" \
    '! grep -qE "/d-[A-Za-z0-9_-]{43}" "$ALL_ERR"' "$(grep -oE "/d-[A-Za-z0-9_-]{10,}" "$ALL_ERR" | head -3)"

# ── 11а. Имя файла — как у .conf в SSH (R-6) ─────────────────────────────────
section "имя файла: как в SSH — имя до 10 знаков, номер места у двух профилей одной линии (R-6), линия читаемо (B3-6)"
check "ga_key_file_base: anna/awg2 → anna-2_0 (B3-6: «-20» не читалось как 2.0)" '[ "$(ga_key_file_base anna awg2)" = anna-2_0 ]' "$(ga_key_file_base anna awg2)"
K1=$(ga_key_file_base verylongusername1 awg2); K2=$(ga_key_file_base verylongusername2 awg2)
check "ga_key_file_base: длинное имя — 7 знаков и 3 знака отпечатка, имя туннеля ≤ 15; общие первые 10 знаков — разные файлы (RR-3)" \
    '[[ "$K1" =~ ^verylon[0-9a-f]{3}-2_0$ ]] && [[ "$K2" =~ ^verylon[0-9a-f]{3}-2_0$ ]] && [ "$K1" != "$K2" ] && [ "${#K1}" -le 15 ] && [ "$(ga_key_file_base abcdefghij awg2)" = abcdefghij-2_0 ]' "$K1 $K2"
check "B3-6: страница (key_file_base в lib/pages_server.py) называет файлы так же, как SSH и временная ссылка" \
    'for n in anna verylongusername1 verylongusername2 abcdefghij abcdefghijk "Ан-на_1" "" -x --verylongname- -; do
        b=$(ga_key_file_base "$n" awg2); p=$(python3 -c "import sys; sys.path.insert(0, \"$ROOT/lib\"); import pages_server as ps; print(ps.key_file_base(sys.argv[1], \"awg2\", \"2.0\"))" "$n")
        [ "$b" = "$p" ] || { echo "$n: $b != $p"; exit 1; }
        p=$(python3 -c "import sys; sys.path.insert(0, \"$ROOT/lib\"); import pages_server as ps; print(ps.key_file_base(sys.argv[1], \"awg3\", \"2.0\", True))" "$n")
        [ "$p" != "" ] || exit 1
    done' "$(ga_key_file_base verylongusername1 awg2)"
check "Н-6: ведущие дефисы имени снимаются — туннель не начинается с «-» (wg-quick передал бы его ip link ключом)" \
    '[ "$(ga_key_file_base -x awg2)" = x-2_0 ] && [ "$(ga_key_file_base --a-b awg2)" = a-b-2_0 ] && [ "$(ga_key_file_base - awg2)" = key-2_0 ]' \
    "$(ga_key_file_base -x awg2) $(ga_key_file_base --a-b awg2) $(ga_key_file_base - awg2)"
cp "$CONFIG_FILE" "$TMP/config.r6"
jq -c '.profiles.awg3 = (.profiles.awg2 + {line: "2.0"}) | .profiles.awg2.line = "2.0"' "$TMP/config.r6" >"$CONFIG_FILE"
check "две 2.0 (awg2 и awg3) — имена разные: anna-2_0-2 и anna-2_0-3; имя из 10 знаков — на знак короче, ≤ 15 (страница — так же)" \
    '[ "$(ga_key_file_base anna awg2)" = anna-2_0-2 ] && [ "$(ga_key_file_base anna awg3)" = anna-2_0-3 ] && b=$(ga_key_file_base abcdefghij awg3) && [[ "$b" =~ ^abcdef[0-9a-f]{3}-2_0-3$ ]] && [ "${#b}" = 15 ] && [ "$b" = "$(python3 -c "import sys; sys.path.insert(0, \"$ROOT/lib\"); import pages_server as ps; print(ps.key_file_base(\"abcdefghij\", \"awg3\", \"2.0\", True))")" ]' \
    "$(ga_key_file_base anna awg2) $(ga_key_file_base anna awg3)"
rm -rf "$TMP/r6"
f=$(ga_share_payload anna awg2 conf "$TMP/r6" 2>>"$ALL_ERR")
check "ga_share_payload при двух 2.0 — файл anna-2_0-2.conf" '[ "$f" = anna-2_0-2.conf ] && [ -s "$TMP/r6/anna-2_0-2.conf" ]' "file=$f"
cp "$TMP/config.r6" "$CONFIG_FILE"; chmod 600 "$CONFIG_FILE"; rm -rf "$TMP/r6"

# ── 12. Статические проверки самого модуля ───────────────────────────────────
section "модуль не ломает статические проверки"
check "bash -n lib/share.sh" "\"${BASH:-bash}\" -n '$ROOT/lib/share.sh'"
check "python3 -m py_compile lib/share_server.py" "python3 -m py_compile '$ROOT/lib/share_server.py'"
check "паритет ключей словаря ru/en" \
    'diff <(grep -oE "^I18N\[[a-z_]+\]" "$ROOT/lib/lang/ru.d/share.sh" | sort) <(grep -oE "^I18N\[[a-z_]+\]" "$ROOT/lib/lang/en.d/share.sh" | sort) >/dev/null'
check "каждый ключ share_*, запрошенный модулем, есть в обоих словарях" \
    'miss=0; for k in $(grep -oE "\\\$\((t|tf) share_[a-z_]+" "$ROOT/lib/share.sh" | awk "{print \$2}" | sort -u); do grep -q "^I18N\[$k\]" "$ROOT/lib/lang/ru.d/share.sh" || miss=1; grep -q "^I18N\[$k\]" "$ROOT/lib/lang/en.d/share.sh" || miss=1; done; [ "$miss" = 0 ]'
check "модуль и сервер перечислены в FILES загрузчика" \
    'grep -q "\"lib/share.sh\"" "$ROOT/bootstrap.sh" && grep -q "\"lib/share_server.py\"" "$ROOT/bootstrap.sh" && grep -q "\"lib/lang/ru.d/share.sh\"" "$ROOT/bootstrap.sh" && grep -q "\"lib/lang/en.d/share.sh\"" "$ROOT/bootstrap.sh"'
# Отдельной функцией, а не строкой в check: программа jq с кавычками внутри
# eval внутри кавычек читается хуже, чем ошибка, которую она прячет.
actions_ok() {
    local f="$ROOT/lib/actions.json"
    jq -e '.actions | has("share-create") and has("share-list") and has("share-revoke")
           and has("share-revoke-all") and has("share-tick")' "$f" >/dev/null || return 1
    # Все действия share-* скрыты от MCP: агент не должен уметь вынести
    # приватный ключ пользователя по ссылке наружу
    [ "$(jq -r '[.actions | to_entries[] | select(.key | startswith("share-")) | .value.mcp | tostring] | unique | join(",")' "$f")" = "null" ] || return 1
    [ "$(jq -r '.actions["share-create"].secret' "$f")" = true ] || return 1
    [ "$(jq -r '.param_types.share_id.pattern' "$f")" = '^s-[a-f0-9]{4,8}$' ] || return 1
    return 0
}
check "действия share-* описаны в lib/actions.json, скрыты от MCP, share-create помечен secret" 'actions_ok'
# Страж образца, а не одного случая: голое `<"$путь" 2>/dev/null` гасит stderr
# КОМАНДЫ, но не ошибку перенаправления, которую печатает сама оболочка. Всё,
# что пишет или читает файл в каталоге раздачи, обязано быть в фигурных скобках.
# Отдельной функцией, а не строкой в check: регулярка с кавычками и $ внутри
# eval внутри кавычек читается хуже, чем ошибка, которую она прячет (тот же
# приём, что у actions_ok ниже).
naked_redir() { grep -nE '[<>]"\$[^"]+" +2>/dev/null' "$ROOT/lib/share.sh"; }
check "ни одного перенаправления в переменный путь с голым 2>/dev/null (нужны фигурные скобки)" \
    '! naked_redir' "$(naked_redir | head -3)"
check "ни одной видимой строки вне t/tf: log_* в модуле зовут только словарь" \
    '! grep -nE "log_(info|warning|error|success|dim|step) +\"[^\$]" "$ROOT/lib/share.sh"' \
    "$(grep -nE "log_(info|warning|error|success|dim|step) +\"[^\$]" "$ROOT/lib/share.sh" | head -3)"

# ── Итог ─────────────────────────────────────────────────────────────────────
run share_revoke_all
DONE=1
printf '\n══ ИТОГ: пройдено %s, провалено %s, пропущено %s\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
