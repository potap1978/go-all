#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон lib/pages.sh и lib/pages_server.py
# (страница подключения, docs/CLIENTS.md §5–§9)
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_pages_dryrun.sh
# Нужны: bash ≥ 5, jq, openssl, python3, curl; qrencode — для QR (без него
# разделы про QR пропускаются, а не проваливаются). Docker, systemd и nginx не
# нужны: профили ставятся в dry-run, vhost пишется во временный
# GOAMNEZIA_NGINX_DIR, юнит — в GOAMNEZIA_SYSTEMD_DIR, systemctl печатается
# как «DRY: …». Сама служба страниц поднимается ПО-НАСТОЯЩЕМУ на петле и ходит
# за ключами в настоящий install.sh --action=user-key — иначе проверять здесь
# нечего: модуль весь про то, что человек открывает страницу и получает ключ.
#
# Что проверяем:
#   Lite без домена → pages_need_domain → отказы (срок, пользователь, профили,
#   зеркало on/off, выключенный пользователь, предел) → создание: JSON с url и
#   секретом, каталог 700, meta/secret 600, секрета нет в meta.json и в аудите,
#   флаг site.pages_location, локация и internal-локация зеркала в vhost, юнит
#   goamnezia-pages → бессрочная страница и страница на часть профилей →
#   page-list без адресов, page-url отдаёт адрес и пишет аудит → живая служба:
#   страница 200 с CSP/noindex/no-store/Server nginx и без внешних ресурсов,
#   app.js, key/<profиль>.conf с верным Endpoint, QR ключей и ссылок каталога,
#   состояние и заявка зеркала, X-Accel-Redirect готовой копии, 404 nginx-вида
#   на чужой секрет, корень и после отзыва, редирект на косую черту, бан по
#   промахам (по X-Real-IP с петли — соседние адреса не задеты), визиты и
#   page-visit, выключенный пользователь → 200 «приостановлен» без ключей (файл
#   ключа — 404) и снова ключи после включения →
#   истечение (pages_tick, page-expire) → ключи из приложения Amnezia (has_key
#   false): страница без единого ключа не создаётся (no_private_key), смешанная —
#   с предупреждением и справкой вместо ключа, файлы и QR такого профиля — 404,
#   старая страница из одних таких профилей — справка, а не 404 → отзыв всех: маршрут и служба сняты →
#   clients_list с фильтрами → мост install.sh --action=page-*/clients-list →
#   статические проверки модуля и словарей.
#   Находки безопасности 25.09.2026: ключей в первом HTML нет, они — в keys.json, визит считается там
#   (SEC50-05: keys.json — только POST с одноразовым значением страницы, после действия человека)
#   (SEC-G2); страница до 0.4.3 без метки записи не отдаёт ключи тёзке (SEC-G3); 404 службы — байт в байт
#   404 nginx, а в vhost 404 перехватывает nginx с заголовками сайта (SEC-G4); limit_req/limit_conn и
#   503 «занят» мимо перехвата (SEC-G1; сама очередь к мосту — tests/test_pages_server.py).
#
# Ничего вне $TMP не пишем; слушатель — только на 127.0.0.1.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL="$ROOT/install.sh"
BASH5="${BASH5:-$BASH}"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-pages.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
ALL_ERR="$TMP/all-stderr.log"
SERVER_PID=""
: >"$ALL_ERR"
cleanup() {
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
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
export GOAMNEZIA_ACME_ROOT="$TMP/acme"
export GOAMNEZIA_LE_DIR="$TMP/letsencrypt"
export GOAMNEZIA_SYSTEMD_DIR="$TMP/systemd"
export GOAMNEZIA_TLS_DIR="$TMP/tls"
export GOAMNEZIA_MIRROR_DIR="$TMP/mirror"
# Бота на стенде нет — коротких ссылок тоже (их прогон — tests/test_short_dryrun.sh); .env бота машины не задевает
export BOT_DIR="$TMP/bot" GOAMNEZIA_BOT_DIR="$TMP/bot" GOAMNEZIA_SHORT_CONFIRM_DIR="$TMP/run/short-confirm"
export UPSTREAM_DIR="$TMP/upstream"
unset GOAMNEZIA_PAGES_DIR GOAMNEZIA_CLIENTS_JSON GOAMNEZIA_MIRROR_CURL
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$GOAMNEZIA_MIRROR_DIR"

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
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
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
audit_has() {   # audit_has <действие> [подстрока details]
    jq -R -e -n --arg a "$1" --arg d "${2:-}" \
        '[inputs | fromjson? // empty | select(.action == $a and ($d == "" or ((.details // "") | contains($d))))] | length > 0' \
        "$GOAMNEZIA_AUDIT_LOG" >/dev/null 2>&1
}

PAGES_DIR="$GOAMNEZIA_DIR/pages"
CONF="$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia"
UNIT="$GOAMNEZIA_SYSTEMD_DIR/goamnezia-pages.service"
SECRET_RE='^[A-Za-z0-9_-]{43}$'
ID_RE='^p-[a-f0-9]{4,8}$'
has()   { grep -qE -- "$1" "$CONF"; }
hasnt() { ! grep -qE -- "$1" "$CONF"; }
HDR="$TMP/hdr.txt"
BODY="$TMP/body.bin"
code=""
# get <путь> [доп. аргументы curl] → код в $code, тело в $BODY, заголовки в $HDR
get() {
    local path="$1"; shift
    code=$(curl -s --max-time 20 -D "$HDR" -o "$BODY" -w '%{http_code}' "$@" "http://127.0.0.1:$PORT$path" 2>/dev/null) || code="000"
}
post() {
    local path="$1"; shift
    code=$(curl -s --max-time 20 -X POST -D "$HDR" -o "$BODY" -w '%{http_code}' "$@" "http://127.0.0.1:$PORT$path" 2>/dev/null) || code="000"
}
hdr() { grep -i "^$1:" "$HDR" | head -1 | tr -d '\r'; }
# reveal <секрет> [доп. аргументы curl] — ключи так, как их берёт app.js после действия человека (SEC50-05):
# страница → одноразовое значение из её данных (REVEAL_NONCE) → POST keys.json с ним; код в $code, тело в $BODY,
# токен на файл и QR ключа из ответа (SEC50-10) — в REVEAL_GRANT
REVEAL_NONCE=""
REVEAL_GRANT=""
reveal() {
    local secret="$1"; shift
    get "/p-$secret/" "$@"
    REVEAL_NONCE=$(ga_data "$BODY" | jq -r '.reveal // ""' 2>/dev/null) || REVEAL_NONCE=""
    post "/p-$secret/keys.json" -H 'Content-Type: application/json' --data "{\"nonce\":\"$REVEAL_NONCE\"}" "$@"
    REVEAL_GRANT=$(jq -r '.grant // ""' "$BODY" 2>/dev/null) || REVEAL_GRANT=""
}

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
for f in lib/common.sh lib/i18n.sh lib/docker.sh lib/amnezia.sh lib/users.sh lib/export.sh lib/website.sh \
         lib/preflight.sh lib/share.sh lib/pages.sh lib/mirror.sh lib/pages_server.py lib/clients.json \
         lib/lang/ru.d/pages.sh lib/lang/en.d/pages.sh lib/nginx/goamnezia.conf.tpl; do
    [ -f "$ROOT/$f" ] || { printf '!! нет %s\n' "$f"; exit 2; }
done
for _b in jq openssl python3 curl; do
    command -v "$_b" >/dev/null 2>&1 || { printf '!! нет %s\n' "$_b"; exit 2; }
done
HAVE_QR=0; command -v qrencode >/dev/null 2>&1 && HAVE_QR=1
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia users export website preflight share pages mirror; do
    # shellcheck disable=SC1090
    source "$ROOT/lib/$_m.sh"
done
check "модуль подключён: pages_create, pages_list, pages_url, pages_revoke, pages_revoke_all, pages_tick, clients_list" \
    'type pages_create >/dev/null && type pages_list >/dev/null && type pages_url >/dev/null && type pages_revoke >/dev/null && type pages_revoke_all >/dev/null && type pages_tick >/dev/null && type clients_list >/dev/null'
check "словарь pages подхвачен движком i18n (pages_err_ttl)" '[ -n "${I18N[pages_err_ttl]:-}" ]'
check "lib/website.sh: website_pages_location_on/off и блок _ga_site_pages_block на месте" \
    'type website_pages_location_on >/dev/null && type website_pages_location_off >/dev/null && type _ga_site_pages_block >/dev/null'

mkdir -p "$UPSTREAM_DIR/awg"
cp "$ROOT"/lib/upstream/awg/* "$UPSTREAM_DIR/awg/" 2>/dev/null
cp "$ROOT/lib/upstream/images.json" "$UPSTREAM_DIR/images.json" 2>/dev/null
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOC
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}, "panel": {"port": 1985, "miniapp_path": ""}, "site": {"template_id": "", "template_source": "", "cert": ""},
 "options": {"drop_icmp": false, "alt_svc": false}, "mcp": {"http_enabled": false, "http_port": 1986}}
EOC
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"
chmod 600 "$USERS_FILE"
mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR"
amnezia_profile_install awg2 40000 default >/dev/null 2>>"$ALL_ERR"
amnezia_profile_install awg3 40001 default >/dev/null 2>>"$ALL_ERR"
users_add anna "Анна" awg2,awg3 >/dev/null 2>>"$ALL_ERR"
users_add bob "Боб" awg2 >/dev/null 2>>"$ALL_ERR"
check "фикстура: профили awg2 (2.0) и awg3 (3.1), у anna пиры в обоих, у bob — только awg2" \
    '[ "$(ga_profile_line awg2)" = 2.0 ] && [ "$(ga_profile_line awg3)" = 3.1 ] && [ "$(ga_users_get anna | jq -r ".peers | keys | join(\",\")")" = awg2,awg3 ] && [ "$(ga_users_get bob | jq -r ".peers | keys | join(\",\")")" = awg2 ]'

# ── 1. Только домен с доверенным сертификатом ───────────────────────────────
section "Lite без домена → pages_need_domain"
run pages_create anna 24h
check "в lite страница не создаётся: pages_need_domain (rc 2), stdout пуст, каталога страниц нет" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = pages_need_domain ] && [ -z "$out" ] && [ ! -d "$PAGES_DIR" ]' "rc=$rc code=$GA_ERR_CODE"
# NC-09: подсказка — словами (временная ссылка на файл), без глаголов моста change-mode / share-create
check "отказ подсказывает временную ссылку на файл словами, без команд моста" \
    'grep -q "временной ссылкой на файл" "$ERR" && ! grep -qE "share-create|change-mode|--mode=" "$ERR"' "$(errs)"
ga_config_merge '{"mode": "lite_site", "domain": "example.com", "site": {"cert": "selfsigned"}}' >/dev/null 2>&1
run pages_create anna 24h
check "сайт на запасном самоподписанном — тоже pages_need_domain: браузеру с телефона его не показать" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = pages_need_domain ]' "rc=$rc code=$GA_ERR_CODE"
ga_config_merge '{"mode": "pro", "domain": "example.com", "site": {"cert": "letsencrypt"}}' >/dev/null 2>&1
check "стенд: сайт на домене example.com с доверенным сертификатом (ga_site_domain_ready)" 'ga_site_domain_ready'

# ── 2. Отказы до записи ─────────────────────────────────────────────────────
section "отказы: срок, пользователь, профили, зеркало, предел"
for bad_ttl in "" "0h" "721h" "366d" "12" "1w" "forever1" "-1d"; do
    run pages_create anna "$bad_ttl"
    [ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_ttl ] || { bad "срок «$bad_ttl» → invalid_ttl (rc 2)" "rc=$rc code=$GA_ERR_CODE"; continue; }
done
ok "недопустимые сроки (пусто, 0h, 721h, 366d, 12, 1w, forever1, -1d) → invalid_ttl"
run pages_create ghost 24h
check "чужой пользователь → user_not_found (rc 3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = user_not_found ]' "rc=$rc code=$GA_ERR_CODE"
run pages_create "../etc" 24h
check "имя с посторонними символами → invalid_name (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_name ]' "rc=$rc code=$GA_ERR_CODE"
run pages_create anna 24h "awg2;rm"
check "профили не по образцу → invalid_profiles (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_profiles ]' "rc=$rc code=$GA_ERR_CODE"
run pages_create bob 24h awg3
check "профиль без ключа у пользователя → peer_not_found (rc 3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = peer_not_found ]' "rc=$rc code=$GA_ERR_CODE"
run pages_create anna 24h "" maybe
check "зеркало не on/off → invalid_toggle (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_toggle ]' "rc=$rc code=$GA_ERR_CODE"
users_disable bob >/dev/null 2>>"$ALL_ERR"
run pages_create bob 24h
check "выключенный пользователь → user_disabled (rc 3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = user_disabled ]' "rc=$rc code=$GA_ERR_CODE"
users_enable bob >/dev/null 2>>"$ALL_ERR"
check "после отказов ни каталога страниц, ни флага маршрута, ни юнита" \
    '[ -z "$(find "$PAGES_DIR" -maxdepth 1 -name "p-*" 2>/dev/null)" ] && [ "$(ga_config_get .site.pages_location)" != true ] && [ ! -f "$UNIT" ]'
GA_PAGES_MAX=0
run pages_create anna 24h
check "предел страниц (GA_PAGES_MAX=0) → page_limit (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = page_limit ]' "rc=$rc code=$GA_ERR_CODE"
GA_PAGES_MAX=200

# ── 3. Создание ─────────────────────────────────────────────────────────────
section "pages_create"
run pages_create anna 24h
check "pages_create anna 24h: rc=0, одна строка JSON" '[ "$rc" = 0 ] && one_line_json' "rc=$rc $(errs)"
P_ID=$(jget .id); P_URL=$(jget .url); P_EXP=$(jget .expires)
P_SECRET=$(sed -E 's#^https://example\.com/p-([A-Za-z0-9_-]{43})/$#\1#' <<<"$P_URL")
check "id вида p-<hex>, url https://example.com/p-<43 символа base64url>/" \
    '[[ "$P_ID" =~ $ID_RE ]] && [[ "$P_SECRET" =~ $SECRET_RE ]] && [[ "$P_URL" =~ ^https://example\.com/p-[A-Za-z0-9_-]{43}/$ ]]' "id=$P_ID url=$P_URL"
check "ответ: user=anna, expires ISO-8601 UTC, forever=false, ttl=24h, profiles=[awg3,awg2] (3.1 первым, NC-27), mirror=true" \
    '[ "$(jget .user)" = anna ] && [[ "$P_EXP" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] && [ "$(jget .forever)" = false ] && [ "$(jget .ttl)" = 24h ] && [ "$(jget ".profiles | join(\",\")")" = awg3,awg2 ] && [ "$(jget .mirror)" = true ]' "$out"
PD="$PAGES_DIR/$P_ID"
check "каталог страниц 700, каталог страницы 700, meta.json и secret 600" \
    '[ "$(perm "$PAGES_DIR")" = 700 ] && [ "$(perm "$PD")" = 700 ] && [ "$(perm "$PD/meta.json")" = 600 ] && [ "$(perm "$PD/secret")" = 600 ]' "$(ls -la "$PD" 2>&1)"
check "секрет лежит отдельным файлом и в meta.json его нет" \
    '[ "$(cat "$PD/secret")" = "$P_SECRET" ] && ! grep -qF -- "$P_SECRET" "$PD/meta.json"' "$(cat "$PD/meta.json")"
check "meta.json: id, user, expires_epoch в будущем, profiles, mirror" \
    '[ "$(jq -r .id "$PD/meta.json")" = "$P_ID" ] && [ "$(jq -r .user "$PD/meta.json")" = anna ] && [ "$(jq -r .expires_epoch "$PD/meta.json")" -gt "$(date -u +%s)" ] && [ "$(jq -r ".profiles | length" "$PD/meta.json")" = 2 ] && [ "$(jq -r .mirror "$PD/meta.json")" = true ]' "$(cat "$PD/meta.json")"
check "config.json: site.pages_location=true" '[ "$(ga_config_get .site.pages_location)" = true ]'
check "vhost: regex-локация /p-<43>/ в кавычках → proxy_pass 127.0.0.1:1987, access_log off" \
    'has "location ~ \"\^/p-\[A-Za-z0-9_-\]\{43\}\(/\.\*\)\?\\$\" \{" && has "proxy_pass http://127\.0\.0\.1:1987;"' "$(grep -n "p-\[" "$CONF")"
# LS-15: служба шлёт свои заголовки безопасности, а серверные add_header наследовались поверх — по два
# противоречивых X-Frame-Options и Referrer-Policy. Свои add_header в локации отключают наследование.
check "LS-15 локация страниц: заголовки службы скрыты, свои — DENY, no-referrer, nosniff, HSTS; server_tokens off" \
    'awk "/location ~ \"\\^\/p-/{f=1} f&&/^    }/{exit} f" "$CONF" > "$TMP/pages_loc" && grep -q "proxy_hide_header X-Frame-Options;" "$TMP/pages_loc" && grep -q "proxy_hide_header Referrer-Policy;" "$TMP/pages_loc" && grep -q "add_header X-Frame-Options \"DENY\" always;" "$TMP/pages_loc" && grep -q "add_header Referrer-Policy \"no-referrer\" always;" "$TMP/pages_loc" && grep -q "Strict-Transport-Security" "$TMP/pages_loc" && has "server_tokens off;"' "$(cat "$TMP/pages_loc" 2>&1 | head -30)"
check "vhost: упавшая служба и 404 службы не выдают себя — proxy_intercept_errors, 404/502/504 → 404 через @ga_pages_down, 503 «занят» проходит" \
    'has "proxy_intercept_errors on;" && has "error_page 404 502 504 =404 @ga_pages_down;" && hasnt "error_page [0-9 ]*503[0-9 ]* =404 @ga_pages_down" && has "location @ga_pages_down \{" && awk "/location @ga_pages_down/{f=1} f&&/return 404;/{print; exit}" "$CONF" | grep -q "return 404"' "$(grep -n "ga_pages_down\|intercept" "$CONF")"
# SEC-G4: 404 страниц отдаёт @ga_pages_down, 404 сайта — location / (try_files … =404). Своих add_header ни
# у той, ни у другой нет — обе наследуют заголовки уровня server, и ответы совпадают заголовок в заголовок
# (раньше 404 службы шёл через add_header локации страниц: DENY и no-referrer против SAMEORIGIN у сайта).
# Строки-комментарии не берём: шаблон сам поясняет в этом блоке, что add_header и expires здесь быть не
# должно, — и проверка ниже краснела на этом пояснении, а не на директиве.
awk '/location @ga_pages_down/{f=1} f && !/^[ \t]*#/{print} f&&/^    }/{exit}' "$CONF" >"$TMP/pages_down"
# location / сайта — тот, что с try_files (в server на 80-м порту есть свой location / с редиректом)
awk '/^    location \/ \{/{f=1; buf=""} f{buf = buf $0 "\n"} f && /^    }/{f=0; if (buf ~ /try_files/) {printf "%s", buf; exit}}' "$CONF" >"$TMP/site_root"
check "SEC-G4: в @ga_pages_down и в location / сайта нет своих заголовков (add_header, expires, proxy_hide_header) — 404 одинаковы" \
    '[ -s "$TMP/pages_down" ] && [ -s "$TMP/site_root" ] && ! grep -qE "add_header|expires|proxy_hide_header|more_set_headers" "$TMP/pages_down" && grep -q "try_files .* =404;" "$TMP/site_root" && ! grep -q "add_header" "$TMP/site_root" && has "add_header X-Frame-Options \"SAMEORIGIN\" always;"' \
    "$(cat "$TMP/pages_down" "$TMP/site_root" 2>&1)"
# SEC-G1: всплеск гасит сам nginx. Зоны — на уровне http (до первого server), отказ — 404 как у сайта.
check "SEC-G1: limit_req/limit_conn в локации страниц, отказы — 404; зоны goamnezia_pages* на уровне http, до первого server" \
    'grep -q "limit_req zone=goamnezia_pages burst=40 nodelay;" "$TMP/pages_loc" && grep -q "limit_req_status 404;" "$TMP/pages_loc" && grep -q "limit_conn goamnezia_pages_conn 16;" "$TMP/pages_loc" && grep -q "limit_conn_status 404;" "$TMP/pages_loc" && awk "/^server \{/{exit} /^limit_req_zone \\\$binary_remote_addr zone=goamnezia_pages:1m rate=5r\/s;/{a=1} /^limit_conn_zone \\\$binary_remote_addr zone=goamnezia_pages_conn:1m;/{b=1} END{exit !(a&&b)}" "$CONF"' \
    "$(grep -n "limit_" "$CONF")"
check "HSTS в локации страниц — только на доверенном сертификате (вариант pro), как у сайта" \
    '_ga_site_pages_block pro | grep -q "Strict-Transport-Security" && ! _ga_site_pages_block selfsigned | grep -q "Strict-Transport-Security"'
check "vhost: внутренняя локация зеркала /_ga_mirror/ → internal, alias на каталог зеркала" \
    'has "location \^~ /_ga_mirror/ \{" && has "^\s*internal;" && has "alias $GOAMNEZIA_MIRROR_DIR/;"' "$(grep -n "_ga_mirror" -A 3 "$CONF")"
# access_log off не закрывает error_log: при упавшей службе nginx писал бы
# «connect() failed … request: GET /p-<секрет>/» — секрет в error.log.
# Ещё две строки — глушилка секретных путей на 80 и 443 (lib/website.sh, _ga_site_secret_paths_block): она в vhost
# всегда.
check "vhost: error_log /dev/null crit во всех трёх блоках страниц (локация, @ga_pages_down, /_ga_mirror/) и в глушилке секретных путей" \
    '[ "$(grep -c "error_log /dev/null crit;" "$CONF")" = 5 ] && awk "/location ~ \"\^\/p-/{f=1} f&&/error_log \/dev\/null crit;/{print; exit}" "$CONF" | grep -q error_log && awk "/location @ga_pages_down/{f=1} f&&/error_log \/dev\/null crit;/{print; exit}" "$CONF" | grep -q error_log && awk "/location \^~ \/_ga_mirror\//{f=1} f&&/error_log \/dev\/null crit;/{print; exit}" "$CONF" | grep -q error_log' \
    "$(grep -n "error_log" "$CONF")"
check "vhost: адрес посетителя уходит службе только заголовками от nginx (X-Real-IP, X-Forwarded-For)" \
    'has "proxy_set_header X-Real-IP \\\$remote_addr;" && has "proxy_set_header X-Forwarded-For \\\$remote_addr;"'
check "vhost: все регулярные location закавычены, если содержат { } ;" \
    'awk "/^[[:space:]]*location[[:space:]]+~\\*?[[:space:]]/ { re = \$3; if (re ~ /^[\"\x27]/) next; if (re ~ /[{};]/) { print NR\": \"\$0; bad = 1 } } END { exit bad ? 1 : 0 }" "$CONF"' "$(grep -n "location ~" "$CONF")"
check "vhost: маршрут MCP и панель не задеты (якоря на месте), хвостов подстановок нет" \
    'has "# GOAMNEZIA_MCP_LOCATION" && has "# GOAMNEZIA_PANEL_LOCATION" && has "# GOAMNEZIA_PAGES_LOCATION" && hasnt "_PLACEHOLDER"'
check "stderr: DRY nginx -t и reload" 'grep -q "DRY: nginx -t" "$ERR" && grep -q "DRY: systemctl reload nginx" "$ERR"' "$(errs)"
check "юнит goamnezia-pages.service записан: python3 pages_server.py --bind 127.0.0.1 --port 1987, User=root, Restart=always" \
    '[ -f "$UNIT" ] && grep -q "ExecStart=/usr/bin/env python3 .*pages_server.py --dir $GOAMNEZIA_DIR --bind 127.0.0.1 --port 1987" "$UNIT" && grep -q "^User=root" "$UNIT" && grep -q "^Restart=always" "$UNIT" && grep -q "GOAMNEZIA_ACTION_BIN=$GOAMNEZIA_DIR/install.sh" "$UNIT"' "$(cat "$UNIT" 2>&1)"
check "stderr: DRY systemctl daemon-reload и enable --now goamnezia-pages.service" \
    'grep -q "DRY: systemctl daemon-reload" "$ERR" && grep -q "DRY: systemctl enable --now goamnezia-pages.service" "$ERR"' "$(errs)"
# TAIL-6 (класс L2-09): в сухом прогоне настоящие службы машины не читаются и не трогаются. Ловушка — свой
# systemctl первым в PATH: любой настоящий вызов оставил бы строку в журнале ловушки.
mkdir -p "$TMP/trapbin"; : >"$TMP/systemctl.real"
printf '#!/bin/sh\necho "REAL systemctl $*" >>"%s"\nexit 0\n' "$TMP/systemctl.real" >"$TMP/trapbin/systemctl"
chmod +x "$TMP/trapbin/systemctl"
check "TAIL-6: в сухом прогоне состояние юнита — «dry», тик страниц (служба уже включена) — только строки DRY, настоящий systemctl не зван ни разу" \
    '[ "$(PATH="$TMP/trapbin:$PATH" ga_pages_unit_state)" = dry ] && PATH="$TMP/trapbin:$PATH" pages_tick >/dev/null 2>&1 && [ ! -s "$TMP/systemctl.real" ]' \
    "$(cat "$TMP/systemctl.real" 2>/dev/null)"
check "аудит page-create: id, пользователь, срок, профили; секрета нет" \
    'audit_has page-create "id=$P_ID user=anna ttl=24h profiles=awg3,awg2 mirror=true" && ! grep -qF -- "$P_SECRET" "$GOAMNEZIA_AUDIT_LOG"' "$(grep page-create "$GOAMNEZIA_AUDIT_LOG")"
check "секрета нет в stderr и в общем логе" \
    '! grep -qF -- "$P_SECRET" "$ALL_ERR" && { [ ! -f "$GOAMNEZIA_LOG_FILE" ] || ! grep -qF -- "$P_SECRET" "$GOAMNEZIA_LOG_FILE"; }'

run pages_create anna forever "" on
F_ID=$(jget .id); F_SECRET=$(sed -E 's#^https://example\.com/p-([A-Za-z0-9_-]{43})/$#\1#' <<<"$(jget .url)")
check "бессрочная: forever=true, expires пусто, предупреждение в stderr" \
    '[ "$rc" = 0 ] && [ "$(jget .forever)" = true ] && [ "$(jget .expires)" = "" ] && grep -qF "$(t pages_warn_forever)" "$ERR"' "rc=$rc $(errs)"
check "повторное создание не перезаписывает юнит и не перезапускает службу (текст тот же)" \
    '! grep -q "DRY: systemctl restart goamnezia-pages.service" "$ERR"' "$(grep DRY "$ERR")"
run pages_create anna 3d awg2 off
S_ID=$(jget .id); S_SECRET=$(sed -E 's#^https://example\.com/p-([A-Za-z0-9_-]{43})/$#\1#' <<<"$(jget .url)")
check "страница на один профиль без зеркала: profiles=[awg2], mirror=false, ttl=3d" \
    '[ "$rc" = 0 ] && [ "$(jget ".profiles | join(\",\")")" = awg2 ] && [ "$(jget .mirror)" = false ] && [ "$(jget .ttl)" = 3d ]' "$out"
check "три страницы — три разных id и три разных секрета" \
    '[ "$P_ID" != "$F_ID" ] && [ "$F_ID" != "$S_ID" ] && [ "$P_SECRET" != "$F_SECRET" ] && [ "$F_SECRET" != "$S_SECRET" ]'

# ── 4. Список и адрес ───────────────────────────────────────────────────────
section "pages_list и pages_url"
run pages_list
check "pages_list: одна строка JSON, count=3" '[ "$rc" = 0 ] && one_line_json && [ "$(jget .count)" = 3 ]' "$out"
check "в строке: id, user, created, expires, forever, ttl, profiles, mirror, visits=0, last_visit" \
    '[ "$(jq -r --arg id "$P_ID" ".pages[] | select(.id == \$id) | [.user, .forever, .ttl, (.profiles | join(\",\")), .mirror, .visits] | join(\" \")" <<<"$out")" = "anna false 24h awg3,awg2 true 0" ]' "$out"
check "в списке НЕТ ни адреса, ни секрета" \
    '! grep -qF -- "$P_SECRET" <<<"$out" && ! grep -qF -- "$F_SECRET" <<<"$out" && [ "$(jq -r ".pages[0].url // \"\"" <<<"$out")" = "" ]' "$out"
run pages_list anna
check "pages_list anna: все три — её" '[ "$(jget .count)" = 3 ]' "$out"
run pages_list bob
check "pages_list bob: пусто" '[ "$rc" = 0 ] && [ "$(jget .count)" = 0 ]' "$out"
run pages_list "bad name"
check "pages_list с негодным именем → invalid_name (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_name ]' "rc=$rc"
run pages_url "$P_ID"
check "pages_url: тот же адрес, что при создании; аудит page-url без секрета" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .url)" = "$P_URL" ] && audit_has page-url "id=$P_ID" && ! grep -qF -- "$P_SECRET" "$GOAMNEZIA_AUDIT_LOG"' "$out"
run pages_url "мусор"
check "pages_url с мусором → invalid_page_id (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_page_id ]' "rc=$rc"
run pages_url p-ffff
check "pages_url неизвестной → page_not_found (rc 3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = page_not_found ]' "rc=$rc code=$GA_ERR_CODE"

# ── 5. Живая служба на петле ────────────────────────────────────────────────
section "служба pages_server.py: страница, ключи, QR, зеркало, 404, бан"
# Мост для службы — настоящий install.sh тем же интерпретатором, что и тест:
# на macOS /bin/bash — 3.2, и прямой запуск по shebang упал бы на declare -A.
ACTION_BIN="$TMP/goamnezia-action"
# Каждый вызов моста — строка в $CALLS: так видно, сколько раз служба запускала install.sh.
CALLS="$TMP/bridge.calls"
: >"$CALLS"
printf '#!/bin/sh\necho "$1 $3 $4" >>"%s"\nexec "%s" "%s" "$@"\n' "$CALLS" "$BASH5" "$INSTALL" >"$ACTION_BIN"
chmod 755 "$ACTION_BIN"
ncalls() { wc -l <"$CALLS" | tr -d ' '; }
# ga_data <файл> → JSON из <script id="ga-data"> (пусто, если его нет)
ga_data() {
    python3 -c '
import re,sys
m=re.search(r"<script type=.application/json. id=.ga-data.>(.*?)</script>", open(sys.argv[1],encoding="utf-8").read(), re.S)
print(m.group(1) if m else "")' "$1"
}
# Отрисовка app.js в node на игрушечном DOM: render_js <страница> [en] [keys.json] [нажать «текст|текст»] → текст
# страницы в $JSOUT. Ключи страница берёт отдельным запросом keys.json (SEC-G2) — POST с одноразовым значением
# страницы и только после действия человека (SEC50-05): с третьим аргументом fetch отдаёт этот файл (значение не
# то — 409), без него запрос «висит» — так видно состояние «ключи ещё грузятся». Четвёртый — что нажать по порядку:
# кнопка, первая строка текста которой совпадает («Показать ключ», выбор приложения, вкладка устройства);
# «~текст» — нажатие из скрипта (element.click(): событие с isTrusted: false), как у сканера с JavaScript;
# «событие@текст» — событие (mouseenter, focus, keydown, click) узлу с таким обработчиком, в тексте которого есть
# этот текст (так «наводят» и «фокусируют» QR пары по его подписи; keydown — клавиша Enter).
# Перед ней — строка «TIME=<datetime>|<текст>» на каждый <time> страницы (срок в шапке, TAIL-21); последняя строка
# вывода — «FETCHES=<сколько раз просили ключи>».
# Без node (на сервере его нет) проверки отрисовки пропускаются, а не проваливаются.
HAVE_NODE=0; command -v node >/dev/null 2>&1 && HAVE_NODE=1
cat >"$TMP/render.js" <<'EOJS'
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const KEYS = process.argv[5] ? JSON.parse(fs.readFileSync(process.argv[5], 'utf8')) : null;
const CLICKS = process.argv[6] ? process.argv[6].split('|') : [];
// Ответ sub.json (вкладка «Подписка»): с ним fetch отдаёт его на POST со своим значением страницы (reveal_sub), без
// него запрос «висит»; вкладку, которую страница «помнит», задаёт GA_TAB
const SUBJSON = process.argv[7] ? JSON.parse(fs.readFileSync(process.argv[7], 'utf8')) : null;
let SUBFETCHES = 0;
// Ключ по ссылке («Скопировать» на вкладке «Подписка»): GET адреса .vpn отвечает «vpn://FAKE-<awgNN>», .conf —
// «[Interface]» с «PrivateKey = FAKE-<awgNN>», или кодом GA_VPN_STATUS; буфер обмена — GA_CLIP: text —
// navigator.clipboard.writeText, item — ClipboardItem с обещанием (как в Safari), без него буфера нет вовсе (копирование
// не выходит). Что ушло в буфер — строки COPIED= (перевод строки — «\n»), запросы — VPNFETCH=
const VPNFETCH = [], COPIED = [];
const m = html.match(/<script type="application\/json" id="ga-data">([\s\S]*?)<\/script>/);
if (!m) { console.log('NO-DATA'); process.exit(0); }
const DATA = JSON.parse(m[1]);
let FETCHES = 0;
function N(tag) { this.tagName = tag; this.kids = []; this.attrs = {}; this.style = {}; this.t = ''; this.ls = {}; this.parentNode = null; }
N.prototype.appendChild = function (c) { if (c.parentNode) { c.parentNode.removeChild(c); } this.kids.push(c); c.parentNode = this; return c; };
N.prototype.insertBefore = function (c, ref) {
  if (c.parentNode) { c.parentNode.removeChild(c); }
  var i = this.kids.indexOf(ref);
  if (i < 0) { this.kids.push(c); } else { this.kids.splice(i, 0, c); }
  c.parentNode = this; return c;
};
N.prototype.removeChild = function (c) { this.kids = this.kids.filter(function (k) { return k !== c; }); c.parentNode = null; return c; };
N.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
N.prototype.getAttribute = function (k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; };
N.prototype.removeAttribute = function (k) { delete this.attrs[k]; };
N.prototype.addEventListener = function (ev, fn) { (this.ls[ev] = this.ls[ev] || []).push(fn); };
N.prototype.focus = function () {};
Object.defineProperty(N.prototype, 'textContent', {
  get: function () { return [this.t].concat(this.kids.map(function (k) { return k.textContent; })).filter(Boolean).join('\n'); },
  set: function (v) { this.t = String(v); this.kids = []; }});
Object.defineProperty(N.prototype, 'innerHTML', {get: function () { return ''; }, set: function () { this.t = ''; this.kids = []; }});
Object.defineProperty(N.prototype, 'firstChild', {get: function () { return this.kids[0]; }});
Object.defineProperty(N.prototype, 'nextSibling', {get: function () { var p = this.parentNode; return p ? p.kids[p.kids.indexOf(this) + 1] || null : null; }});
const root = new N('div');
const store = {ga_lang: process.argv[4] || null, ga_tab: process.env.GA_TAB || null};
const clip = process.env.GA_CLIP || '';
const nav = {}, win = {localStorage: {getItem: function (k) { return store[k] || null; }, setItem: function (k, v) { store[k] = String(v); }},
           btoa: function (s) { return Buffer.from(s, 'binary').toString('base64'); }, addEventListener: function () {},
           history: {pushState: function () {}}, location: {reload: function () {}}};
if (clip === 'text') { nav.clipboard = {writeText: function (s) { COPIED.push(s); return Promise.resolve(); }}; }
if (clip === 'item') {
  win.ClipboardItem = function (items) { this.items = items; };
  nav.clipboard = {write: function (list) { return Promise.resolve(list[0].items['text/plain']).then(function (b) { COPIED.push(b.parts.join('')); }); },
                   writeText: function () { return Promise.reject(new Error('NotAllowedError')); }};
}
var INTERVALS = [];
function BlobMock(parts, opts) { this.parts = parts; this.type = (opts || {}).type; }
vm.runInNewContext(fs.readFileSync(process.argv[3], 'utf8'), {
  document: {getElementById: function (id) { return id === 'ga-data' ? {textContent: m[1]} : (id === 'app' ? root : null); },
             createElement: function (tag) { return new N(tag); },
             createTextNode: function (s) { var n = new N('#text'); n.t = String(s); return n; }, body: new N('body'),
             documentElement: new N('html'), addEventListener: function () {}, removeEventListener: function () {}, title: ''},
  window: win, Blob: BlobMock,
  navigator: nav, setTimeout: function () { return 0; }, clearTimeout: function () {},
  // Смена частей QR vpn:// (MINIAPP-QR-01): интервалы копятся, шаг «@tick» вызывает живые по разу
  setInterval: function (fn, ms) { INTERVALS.push({fn: fn, ms: ms, on: true}); return INTERVALS.length; },
  clearInterval: function (id) { if (INTERVALS[id - 1]) { INTERVALS[id - 1].on = false; } },
  fetch: function (url, opts) {
    if (/\.(vpn|conf)$/.test(url)) {
      var status = Number(process.env.GA_VPN_STATUS || 200), token = url.replace(/^.*\/(awg[0-9]+)\.(vpn|conf)$/, '$1');
      var body = /\.conf$/.test(url) ? '[Interface]\nPrivateKey = FAKE-' + token + '\n' : 'vpn://FAKE-' + token + '\n';
      VPNFETCH.push(url + '|' + ((opts || {}).method || 'GET') + '|' + (opts || {}).cache + '|' + (opts || {}).credentials);
      return Promise.resolve({status: status, ok: status === 200, headers: {get: function () { return null; }},
                              text: function () { return Promise.resolve(status === 200 ? body : '<html>404</html>'); }});
    }
    if (url === 'keys.json') { FETCHES += 1; }
    if (url === 'keys.json' && KEYS) {
      var sent = '';
      try { sent = JSON.parse((opts || {}).body || '{}').nonce; } catch (e) { sent = ''; }
      if ((opts || {}).method !== 'POST' || !sent || sent !== DATA.reveal) {
        return Promise.resolve({status: 409, ok: false, headers: {get: function () { return null; }},
                                json: function () { return Promise.resolve({error: 'reveal'}); }});
      }
      return Promise.resolve({status: 200, ok: true, headers: {get: function () { return null; }},
                              json: function () { return Promise.resolve(KEYS); }});
    }
    if (url === 'sub.json') { SUBFETCHES += 1; }
    if (url === 'sub.json' && SUBJSON) {
      var subSent = '';
      try { subSent = JSON.parse((opts || {}).body || '{}').nonce; } catch (e) { subSent = ''; }
      if ((opts || {}).method !== 'POST' || !subSent || subSent !== DATA.reveal_sub) {
        return Promise.resolve({status: 409, ok: false, headers: {get: function () { return null; }},
                                json: function () { return Promise.resolve({error: 'reveal'}); }});
      }
      return Promise.resolve({status: 200, ok: true, headers: {get: function () { return null; }},
                              json: function () { return Promise.resolve(SUBJSON); }});
    }
    return new Promise(function () {});
  }
});
// Нажатие: первый узел с обработчиком click, первая строка текста которого — нужный текст
function find(node, text) {
  if (node.ls && node.ls.click && node.textContent.split('\n')[0] === text) { return node; }
  for (var i = 0; i < node.kids.length; i++) { var f = find(node.kids[i], text); if (f) { return f; } }
  return null;
}
// Событие ev: первый узел с таким обработчиком, в тексте которого есть нужный текст
function findOn(node, ev, text) {
  if (node.ls && node.ls[ev] && node.textContent.indexOf(text) >= 0) { return node; }
  for (var i = 0; i < node.kids.length; i++) { var f = findOn(node.kids[i], ev, text); if (f) { return f; } }
  return null;
}
// Ответ keys.json приходит микрозадачами — нажимаем по одному, давая им дойти между нажатиями (кнопки ключа
// появляются только после ответа: «Показать ключ|Скачать .conf»), и печатаем после них
setTimeout(function () {
  var i = 0;
  (function next() {
    if (i >= CLICKS.length) {
      setTimeout(function () {
        console.log(root.textContent);
        (function times(n) {
          if (n.tagName === 'time') { console.log('TIME=' + n.getAttribute('datetime') + '|' + n.textContent); }
          n.kids.forEach(times);
        })(root);
        console.log('FETCHES=' + FETCHES);
        console.log('SUBFETCHES=' + SUBFETCHES);
        console.log('TAB=' + (store.ga_tab || ''));
        // Ссылки в приложения, картинки QR, нажатые и неактивные кнопки, переход страницы
        (function marks(n) {
          var first = n.textContent.split('\n')[0];
          if (n.tagName === 'a' && /install-config|^incy:|^wg:/.test(n.getAttribute('href') || '')) { console.log('HREF=' + n.getAttribute('href')); }
          if (n.tagName === 'img' && /^qr\//.test(n.getAttribute('src') || '')) { console.log('IMG=' + n.getAttribute('src')); }
          if (n.tagName === 'img' && /^qr\//.test(n.getAttribute('src') || '') && n.getAttribute('hidden') === null) { console.log('SHOWN=' + n.getAttribute('src')); }
          if (n.tagName === 'button' && n.getAttribute('aria-pressed') === 'true') { console.log('PRESSED=' + first); }
          if (n.tagName === 'button' && n.getAttribute('disabled') !== null) { console.log('DISABLED=' + first); }
          // Пара QR (qrPair): активен ли, роль, подпись для чтеца, скрыта ли картинка, есть ли фокус клавиатуры
          if (n.getAttribute('data-qr') !== null) {
            var img = (function q(x) { if (x.tagName === 'img') { return x; } for (var k = 0; k < x.kids.length; k++) { var f = q(x.kids[k]); if (f) { return f; } } return null; })(n);
            console.log('QR=' + n.getAttribute('data-qr') + '|' + n.getAttribute('role') + '|' + n.getAttribute('tabindex') + '|' +
                        (img ? img.getAttribute('aria-hidden') || '' : '?') + '|' + n.getAttribute('aria-label'));
          }
          n.kids.forEach(marks);
        })(root);
        if (win.location.href) { console.log('LOCATION=' + win.location.href); }
        VPNFETCH.forEach(function (v) { console.log('VPNFETCH=' + v); });
        COPIED.forEach(function (v) { console.log('COPIED=' + v.replace(/\n/g, '\\n')); });
      }, 20);
      return;
    }
    var text = CLICKS[i], synthetic = text.charAt(0) === '~', n, ev = /^([a-z]+)@(.+)$/.exec(text);
    i += 1;
    if (text === '@tick') {
      INTERVALS.forEach(function (x) { if (x.on) { console.log('INTERVAL=' + x.ms); x.fn(); } });
      setTimeout(next, 5);
      return;
    }
    if (/^@aria=/.test(text)) {
      n = (function q(x, l) { if (x.ls && x.ls.click && x.getAttribute('aria-label') === l) { return x; } for (var k = 0; k < x.kids.length; k++) { var f = q(x.kids[k], l); if (f) { return f; } } return null; })(root, text.slice(6));
      if (!n) { console.log('NO-CLICK ' + text); } else { n.ls.click.forEach(function (fn) { fn({}); }); }
      setTimeout(next, 5);
      return;
    }
    if (ev) {
      n = findOn(root, ev[1], ev[2]);
      if (!n) { console.log('NO-EVENT ' + text); } else {
        n.ls[ev[1]].forEach(function (fn) { fn({key: 'Enter', keyCode: 13, preventDefault: function () {}}); });
      }
      setTimeout(next, 5);
      return;
    }
    if (synthetic) { text = text.slice(1); }
    n = find(root, text);
    if (!n) { console.log('NO-CLICK ' + text); } else { n.ls.click.forEach(function (fn) { fn(synthetic ? {isTrusted: false} : {}); }); }
    setTimeout(next, 5);
  })();
}, 20);
EOJS
JSOUT=""
render_js() { JSOUT=$(node "$TMP/render.js" "$1" "$TMP/app.js" "${2:-}" "${3:-}" "${4:-}" "${5:-}" 2>&1); }
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
READY="$TMP/ready"
python3 "$ROOT/lib/pages_server.py" --dir "$GOAMNEZIA_DIR" --bind 127.0.0.1 --port "$PORT" \
    --action-bin "$ACTION_BIN" --audit "$GOAMNEZIA_AUDIT_LOG" --ready-file "$READY" >"$TMP/server.log" 2>&1 &
SERVER_PID=$!
for _i in $(seq 1 100); do [ -f "$READY" ] && break; sleep 0.1; done
check "служба поднялась на 127.0.0.1:$PORT (маркер готовности)" '[ -f "$READY" ] && kill -0 "$SERVER_PID" 2>/dev/null' "$(cat "$TMP/server.log")"
CONF_TEXT=$(export_conf anna awg2 2>/dev/null)

get "/p-$P_SECRET/"
check "GET страницы → 200 text/html" '[ "$code" = 200 ] && hdr content-type | grep -q "text/html"' "code=$code $(tail -5 "$TMP/server.log")"
check "заголовки: CSP default-src none + script-src self, X-Robots-Tag noindex, Referrer-Policy no-referrer, Cache-Control no-store, nosniff, Server nginx" \
    'hdr content-security-policy | grep -q "default-src '"'"'none'"'"'" && hdr content-security-policy | grep -q "script-src '"'"'self'"'"'" && hdr x-robots-tag | grep -qi "noindex, nofollow" && hdr referrer-policy | grep -qi "no-referrer" && hdr cache-control | grep -qi "no-store" && hdr x-content-type-options | grep -qi nosniff && hdr server | grep -qi "^server: nginx"' "$(cat "$HDR")"
check "на странице нет внешних ресурсов: скрипт только app.js, ни <link>, ни src=\"http" \
    '[ "$(grep -o "<script src=\"[^\"]*\"" "$BODY" | sort -u)" = "<script src=\"app.js\"" ] && ! grep -q "<link " "$BODY" && ! grep -q "src=\"http" "$BODY" && ! grep -q "@import" "$BODY"' "$(grep -o "<script[^>]*>" "$BODY" | head -3)"
check "страница несёт данные: пользователь Анна, профили awg2 и awg3 с линейками 2.0 и 3.1, каталог приложений, обе линейки в lines" \
    'grep -q "ga-data" "$BODY" && python3 -c "
import re,sys,json
html=open(sys.argv[1],encoding=\"utf-8\").read()
m=re.search(r\"<script type=.application/json. id=.ga-data.>(.*?)</script>\", html, re.S)
d=json.loads(m.group(1).replace(\"<\\\\/\",\"</\"))
assert d[\"label\"]==\"Анна\" and d[\"user\"]==\"anna\", d[\"label\"]
ids=[p[\"id\"] for p in d[\"profiles\"]]; assert ids==[\"awg3\",\"awg2\"], ids
assert sorted(d[\"lines\"])==[\"2.0\",\"3.1\"], d[\"lines\"]
assert all(\"conf\" not in p and \"vpn\" not in p for p in d[\"profiles\"]), d[\"profiles\"]
names=[a[\"id\"] for a in d[\"apps\"]]; assert \"amneziavpn\" in names and \"amneziawg\" in names, names
assert names[0] in (\"amneziavpn\",\"amneziawg\",\"defaultvpn\",\"amneziawg-tools\"), names
assert d[\"mirror_enabled\"] is True and d[\"device\"] in d[\"platforms\"]
" "$BODY"' "$(head -c 300 "$BODY")"
cp "$BODY" "$TMP/first.html"
# SEC-G2: ключи — только отдельным запросом keys.json. Первый HTML забирают и роботы превью (Telegram, VK,
# Max), и проверялки почты — полный приватный ключ уезжал к ним вместе с разметкой.
get "/p-$P_SECRET/keys.json"
check "SEC50-05: GET keys.json → 405 (Allow: POST), ключей в ответе нет" \
    '[ "$code" = 405 ] && hdr allow | grep -q POST && ! grep -q "PrivateKey" "$BODY"' "code=$code $(head -c 200 "$BODY")"
cp "$TMP/first.html" "$BODY"
check "SEC50-05: в данных страницы — одноразовое значение для ключей (32 символа base64url), у каждой отрисовки своё" \
    'n1=$(ga_data "$TMP/first.html" | jq -r .reveal) && [[ "$n1" =~ ^[A-Za-z0-9_-]{32}$ ]] && get "/p-$P_SECRET/" && [ "$(ga_data "$BODY" | jq -r .reveal)" != "$n1" ]' "$(ga_data "$TMP/first.html" | jq -c .reveal)"
reveal "$P_SECRET"
cp "$BODY" "$TMP/keys.json"
check "SEC-G2, SEC50-05: POST keys.json с одноразовым значением страницы → 200 JSON, у awg2 и awg3 conf ([Interface], PrivateKey) и vpn://…; conf awg2 байт в байт как export_conf" \
    '[ "$code" = 200 ] && hdr content-type | grep -q "application/json" && [ "$(jq -r ".keys | keys | join(\",\")" "$TMP/keys.json")" = awg2,awg3 ] && jq -e "[.keys[] | (.conf | startswith(\"[Interface]\") and contains(\"PrivateKey\")) and (.vpn | startswith(\"vpn://\"))] | all" "$TMP/keys.json" >/dev/null && [ "$(jq -r .keys.awg2.conf "$TMP/keys.json")" = "$CONF_TEXT" ]' \
    "code=$code $(head -c 200 "$TMP/keys.json")"
check "SEC-G2: keys.json с защитными заголовками (no-store, nosniff, CSP), как у страницы" \
    'hdr cache-control | grep -qi "no-store" && hdr x-content-type-options | grep -qi nosniff && hdr content-security-policy | grep -q "default-src"' "$(cat "$HDR")"
check "SEC-G2: в первом HTML нет ни одного ключа — ни PrivateKey, ни ключей vpn:// из keys.json, ни ссылок на key/…" \
    '! grep -q "PrivateKey" "$TMP/first.html" && ! grep -q "\[Interface\]" "$TMP/first.html" && ! grep -qF "$(jq -r .keys.awg2.vpn "$TMP/keys.json")" "$TMP/first.html" && ! grep -qF "$(jq -r .keys.awg3.vpn "$TMP/keys.json")" "$TMP/first.html" && ! grep -qE "vpn://[A-Za-z0-9_-]{40}" "$TMP/first.html" && ! grep -q "href=\"key/" "$TMP/first.html"' \
    "$(grep -o "PrivateKey.\{0,20\}\|href=.key/[^>]*" "$TMP/first.html" | head -3)"
check "noscript: вместо ключей и ссылок на файлы — одна заметка «откройте с включённым JavaScript» (карточка макета)" \
    'grep -qF "<section class=\"ga-card pg-ns\">" "$TMP/first.html" && grep -qF "<b>Откройте страницу в браузере с включённым JavaScript</b><span>Ключи и приложения показываются только с включённым JavaScript" "$TMP/first.html" && [ "$(grep -o "pg-ns\"" "$TMP/first.html" | wc -l | tr -d " ")" = 1 ]' \
    "$(grep -o "<noscript>.*</noscript>" "$TMP/first.html" | head -c 600)"
post "/p-$P_SECRET/keys.json" -H 'Content-Type: application/json' --data "{\"nonce\":\"$REVEAL_NONCE\"}"
check "SEC50-05: то же значение второй раз → 409, ключей нет" '[ "$code" = 409 ] && ! grep -q "PrivateKey" "$BODY"' "code=$code $(head -c 200 "$BODY")"
reveal "$P_SECRET" -A "TelegramBot (like TwitterBot)"
check "SEC-G2: keys.json роботу превью → 404 (ключ ему — никогда)" '[ "$code" = 404 ]' "code=$code"
cp "$TMP/first.html" "$BODY"     # проверки ниже читают первую страницу
# Шапка (ревью продукта 25.09.2026): трафик и срок доступа, а срок СТРАНИЦЫ не выдаёт себя за срок VPN.
# TAIL-21: пояса посетителя без JavaScript не узнать — UTC с подписью, момент в <time datetime> и «через …» (страница
# на 24 ч выдана только что — «через 23 ч»).
check "шапка без JavaScript: «Доступ работает · доступ бессрочный, без лимита · 0 МБ»; «Страница открывается до <time>… UTC</time> (через 23 ч) — добавьте ключ заранее, VPN будет работать и после»" \
    'grep -qF "<div class=\"pg-status__title\">Доступ работает</div><div class=\"pg-status__sub\">доступ бессрочный, без лимита</div>" "$BODY" && grep -qF "<span class=\"pg-status__num\">0 <span>МБ</span></span>" "$BODY" && grep -qE "Страница открывается до <time datetime=\"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\">[0-9]{2}\.[0-9]{2}\.[0-9]{4} [0-9]{2}:[0-9]{2} UTC</time> \(через 23 ч\) — добавьте ключ заранее, VPN будет работать и после" "$BODY" && ! grep -qF "страница работает до" "$BODY"' \
    "$(grep -o "<noscript>.*</noscript>" "$BODY" | head -c 600)"
check "без JavaScript RU/EN — ссылки ?lang=ru и ?lang=en (текущий язык отмечен aria-current); ?lang=en — та же оболочка по-английски, и app.js начнёт с английского" \
    'grep -qF "<a class=\"pg-lang__btn\" href=\"?lang=ru\" hreflang=\"ru\" aria-current=\"true\">RU</a><a class=\"pg-lang__btn\" href=\"?lang=en\" hreflang=\"en\">EN</a>" "$BODY" && get "/p-$P_SECRET/?lang=en" && [ "$code" = 200 ] && grep -qF "<html lang=\"en\" data-hero>" "$BODY" && grep -qF "Open this page in a browser with JavaScript on" "$BODY" && [ "$(ga_data "$BODY" | jq -r .lang)" = en ] && get "/p-$P_SECRET/?lang=xx" && [ "$(ga_data "$BODY" | jq -r .lang)" = ru ]' \
    "code=$code $(grep -o "<noscript>.\{0,400\}" "$BODY" | head -c 500)"
cp "$TMP/first.html" "$BODY"
check "данные страницы: access {used 0, quota 0, expires пусто}, now — часы сервера, у ключей seen = 0 (рукопожатий ещё не было)" \
    '[ "$(ga_data "$BODY" | jq -c "[.access.used, .access.quota, .access.expires, (.now > 0), [.profiles[].seen]]")" = "[0,0,\"\",true,[0,0]]" ]' \
    "$(ga_data "$BODY" | jq -c "{access, now, seen: [.profiles[].seen]}" 2>&1)"
# Адаптивность от телефона до телевизора (дополнение владельца, CLIENTS.md §7):
# проверяем вёрстку статически — по HTML и CSS, которые отдаёт служба.
cp "$BODY" "$TMP/page.html"
check "адаптивность: meta viewport width=device-width, initial-scale=1" \
    'grep -q "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" "$TMP/page.html"'
# Вид — макет handoff/iteration3/Client.dc.html на токенах heroui-tokens.css (0.5.0): телефон — одна колонка, от
# 900 px — две; тёмная тема по системе; цели касания 44 px; телевизор — свой крупный список (цели 76 px).
check "вид: одна колонка на телефоне, две от 900 px (шаг 1 | шаг 2), без прокрутки вбок — сетки minmax(0,1fr)" \
    'grep -q "@media (max-width:899px)" "$TMP/page.html" && grep -q "@media (min-width:900px){\[data-hero\] .pg{max-width:1120px" "$TMP/page.html" && grep -q ".pg-cols{grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:32px}" "$TMP/page.html" && grep -q "^.pg{max-width:860px;margin:0 auto;padding:20px 16px 28px;display:grid;grid-template-columns:minmax(0,1fr)" "$TMP/page.html"'
check "вид: токены макета (акцент goAmnezia, скругления 8/12/24 px, кнопки-«таблетки»), тёмная тема — по prefers-color-scheme, без JavaScript тоже" \
    'grep -q "\[data-hero\]{--font:" "$TMP/page.html" && grep -q -- "--accent:#0090b7" "$TMP/page.html" && grep -q -- "--r-card:24px" "$TMP/page.html" && grep -q -- "--r-btn:9999px" "$TMP/page.html" && grep -q "@media (prefers-color-scheme:dark){\[data-hero\]{color-scheme:dark" "$TMP/page.html" && grep -q "<html lang=\"ru\" data-hero>" "$TMP/page.html" && ! grep -q "oklch(\|color-mix(" "$TMP/page.html"'
check "вид: :focus-visible (у пульта — рамка 4 px), hover только под (hover:hover), лишние анимации гасит prefers-reduced-motion" \
    'grep -q "\[data-hero\] :focus-visible{outline:2px solid var(--focus)" "$TMP/page.html" && grep -q "\[data-tv=\"page\"\] :focus-visible{outline-width:4px" "$TMP/page.html" && grep -q "@media (hover:hover)" "$TMP/page.html" && [ "$(grep -c ":hover{" "$TMP/page.html")" = "$(grep -c "@media (hover:hover){[^@]*:hover{" "$TMP/page.html")" ] && grep -q "@media (prefers-reduced-motion:reduce)" "$TMP/page.html"'
check "вид: длинное переносится (overflow-wrap:anywhere), картинки не шире блока, цели касания 44 px (вкладки, кнопки, RU/EN, листание QR, выбор приложения — строка 22 px плюс 2 × 11 px полей карточки)" \
    'grep -q "overflow-wrap:anywhere" "$TMP/page.html" && grep -q "img{max-width:100%;height:auto}" "$TMP/page.html" && grep -q "^.pg-dev{[^}]*height:44px" "$TMP/page.html" && grep -q "^.pg-btn{[^}]*min-height:44px" "$TMP/page.html" && grep -q "^.pg-kbtn{[^}]*min-height:44px" "$TMP/page.html" && grep -q "^.pg-lang__btn{[^}]*height:44px;min-width:48px" "$TMP/page.html" && grep -q "^.pg-page{width:44px;height:44px" "$TMP/page.html" && grep -q "^.pg-app__head{[^}]*margin:-11px 0;padding:11px 0" "$TMP/page.html" && grep -q "^.pg-app__name b{font:600 16px/22px" "$TMP/page.html" && grep -q "^.pg-app{[^}]*padding:14px 16px" "$TMP/page.html"'
check "вид: кнопки ключа — сеткой равных ячеек (auto-fill 150 px), ссылки — строкой «кнопка + QR»; телевизор — пункты 76 px и рамка фокуса 4 px" \
    'grep -q "^.pg-kbtns{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr))" "$TMP/page.html" && grep -q "^.pg-link{display:grid" "$TMP/page.html" && grep -q "^.tv__item{[^}]*min-height:76px" "$TMP/page.html" && grep -q "^.tv__item:focus,\[data-hero\] .tv__item:focus-visible{[^}]*outline:4px solid var(--accent)" "$TMP/page.html"'
check "иконки Gravity UI — встроенными контурами (без сети), с пометкой лицензии MIT" \
    'grep -q "<span class=\"ic\" aria-hidden=\"true\"><svg width=\"14\" height=\"14\" viewBox=\"0 0 16 16\" focusable=\"false\"><path fill=\"currentColor\"" "$TMP/page.html" && grep -q "Gravity UI (MIT, © 2022 YANDEX LLC" "$ROOT/lib/pages_server.py" && grep -q "Apache-2.0" "$TMP/page.html"'
# Ссылка «Открыть в AmneziaVPN»: профиль.vpn уже начинается с vpn://, шаблон
# каталога — «{vpn}» целиком; «vpn://{vpn}» давал бы vpn://vpn://… и мёртвую кнопку.
check "deeplink AmneziaVPN на Android — «{vpn}»: собранная ссылка начинается с vpn:// ровно один раз; incy — incy://import/<base64>" \
    'python3 -c "
import re,sys,json
html=open(sys.argv[1],encoding=\"utf-8\").read()
m=re.search(r\"<script type=.application/json. id=.ga-data.>(.*?)</script>\", html, re.S)
d=json.loads(m.group(1))
apps={a[\"id\"]: a for a in d[\"apps\"]}
tpl=apps[\"amneziavpn\"][\"import\"][\"deeplink\"][\"android\"]
assert tpl == \"{vpn}\", tpl
vpn=json.load(open(sys.argv[2],encoding=\"utf-8\"))[\"keys\"][\"awg2\"][\"vpn\"]
href=tpl.replace(\"{vpn}\", vpn)
assert href.startswith(\"vpn://\") and not href.startswith(\"vpn://vpn://\"), href
assert href.count(\"vpn://\") == 1, href
assert apps[\"incy\"][\"import\"][\"deeplink\"][\"android\"] == \"incy://import/{conf_base64}\"
" "$BODY" "$TMP/keys.json"' "$(python3 -c "import re,sys;print(re.search(r'\"deeplink\": \{[^}]*\}', open(sys.argv[1]).read()).group(0))" "$BODY" 2>/dev/null)"
get "/p-$P_SECRET/" -A "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
check "устройство по User-Agent: iPhone → device=ios" 'grep -q "\"device\": \"ios\"\|\"device\":\"ios\"" "$BODY"' "$(grep -o '"device": *"[a-z_]*"' "$BODY")"
get "/p-$P_SECRET/" -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
check "Windows → device=windows" 'grep -q "\"device\": *\"windows\"" "$BODY"'
get "/p-$P_SECRET/app.js"
check "app.js → 200 application/javascript, в нём переключатель языка и запрос копии" \
    '[ "$code" = 200 ] && hdr content-type | grep -q "javascript" && grep -q "ga_lang" "$BODY" && grep -q "/request" "$BODY"' "code=$code"
# Устройства и приложения — radiogroup на каждой из двух вкладок («Статический ключ» и «Подписка»), сами вкладки —
# tablist из кнопок role=tab.
check "app.js: всё интерактивное — настоящие <button> и <a> (обработчик нажатия — только у них, ни role=button), устройства и приложения — radiogroup из кнопок role=radio, вкладки — tablist из кнопок role=tab" \
    '! grep -qE "on\(h\(.(div|span|section|nav|header|footer|p|b)[^a-z]" "$BODY" && [ "$(grep "addEventListener(.click." "$BODY" | grep -vc "document.addEventListener(.click., onHuman")" = 1 ] && ! grep -q "role=\"button\"\|role: .button" "$BODY" && [ "$(grep -c "h(.button., {type: .button." "$BODY")" -ge 10 ] && [ "$(grep -c "role: .radiogroup." "$BODY")" = 4 ] && grep -q "role: .radio., .aria-checked." "$BODY" && grep -q "{cls: .pg-cols.}" "$BODY" && grep -q "role: .tablist." "$BODY" && grep -q "role: .tab., id: .pg-tab-. + id," "$BODY" && grep -q "role: .tabpanel." "$BODY"' \
    "$(grep -nE "on\(h\(.(div|span|section|nav|header|footer|p|b)[^a-z]" "$BODY" | head -3)"
check "app.js: вкладки устройств — 8 подписей макета («iPhone и iPad» … «Android TV» … «Роутер»/«Router»), выбранная — aria-checked" \
    'grep -qF "\"android_tv\": \"Android TV\"" "$BODY" && grep -qF "\"router\": \"Роутер\"" "$BODY" && grep -qF "\"router\": \"Router\"" "$BODY" && grep -qF "\"ios\": \"iPhone и iPad\"" "$BODY" && grep -q "aria-checked" "$BODY"'
# Нужная версия приложения — нижняя граница из каталога (support линейки у выбранного приложения); «не старше»
# говорило бы обратное.
check "словарь: «%s: нужна версия %s или новее» (ru) и «%s: version %s or newer is needed» (en), «не старше» нет" \
    'grep -qF "\"need_version\": \"%s: нужна версия %s или новее\"" "$ROOT/lib/pages_server.py" && grep -qF "\"need_version\": \"%s: version %s or newer is needed\"" "$ROOT/lib/pages_server.py" && ! grep -q "не старше" "$ROOT/lib/pages_server.py"'
check "словарь UI: ru и en несут одни и те же ключи, строки состояний макета на месте, app.js получает его целиком" \
    'python3 -c "
import sys, json
sys.path.insert(0, sys.argv[1] + \"/lib\")
import pages_server as ps
assert set(ps.UI[\"ru\"]) == set(ps.UI[\"en\"]), set(ps.UI[\"ru\"]) ^ set(ps.UI[\"en\"])
for k in (\"until\",\"until_short\",\"forever\",\"st_ok\",\"st_until\",\"st_left\",\"st_forever\",\"st_nolimit\",\"suspended\",\"sus_quota_title\",\"sus_expired_title\",\"sus_admin_title\",\"seen_now\",\"never\",\"keys_loading\",\"keys_busy\",\"keys_failed\",\"gone_title\",\"tvos_title\",\"tvos_btn\",\"imported\",\"busy_title\",\"ns_title\",\"tv_title\",\"tv_remote\"):
    assert k in ps.UI[\"ru\"], k
js = open(sys.argv[2], encoding=\"utf-8\").read()
assert json.dumps(ps.UI, ensure_ascii=False) in js and \"__UI__\" not in js and \"__ICONS__\" not in js
" "$ROOT" "$BODY"'
cp "$BODY" "$TMP/app.js"
if [ "$HAVE_NODE" = 1 ]; then
    check "app.js, отданный службой (словарь и иконки уже подставлены), — синтаксически верный JavaScript (node --check)" \
        'node --check "$TMP/app.js"' "$(node --check "$TMP/app.js" 2>&1 | head -5)"
fi
check "app.js: вкладка Apple TV подписана «Apple TV» в обоих языках; ключи — POST keys.json с одноразовым значением (SEC50-05) и повтором по Retry-After на 503" \
    '[ "$(grep -o "\"tvos\": \"Apple TV\"" "$TMP/app.js" | wc -l | tr -d " ")" = 2 ] && grep -q "fetch(.keys.json., {method: .POST." "$TMP/app.js" && grep -q "nonce: String(data.reveal" "$TMP/app.js" && grep -q "Retry-After" "$TMP/app.js" && grep -q "r.status === 503" "$TMP/app.js"'
check "данные страницы: имена приложений по-английски (name_en) и пометка платного (price) — из каталога" \
    '[ "$(ga_data "$TMP/page.html" | jq -r ".apps[] | select(.id == \"mikrotik\") | .name_en")" = "MikroTik (container)" ] && [ "$(ga_data "$TMP/page.html" | jq -r ".apps[] | select(.id == \"wgconnect\") | .price.en")" = "paid after a trial" ] && [ "$(ga_data "$TMP/page.html" | jq -r ".domain")" = example.com ]' \
    "$(ga_data "$TMP/page.html" | jq -c "[.apps[] | {id, name_en, price}]" 2>&1 | head -c 400)"
if [ "$HAVE_NODE" = 1 ]; then
    render_js "$TMP/page.html"
    # QR vpn:// — картинки со службы, для кнопки ключ не нужен; без qrencode их нет вовсе
    n_qr=0; [ "$HAVE_QR" = 1 ] && n_qr=2
    check "SEC50-05, app.js (node), до действия человека: ключи не запрошены (FETCHES=0); у обоих ключей «Показать ключ», ни «Скачать .conf», ни QR, ни «Скопировать», ни «Открыть в …»" \
        'grep -qx "FETCHES=0" <<<"$JSOUT" && [ "$(grep -c "^Показать ключ$" <<<"$JSOUT")" = 2 ] && ! grep -q "^Скачать \.conf$" <<<"$JSOUT" && ! grep -q "^QR " <<<"$JSOUT" && ! grep -qF "Скопировать vpn://" <<<"$JSOUT" && ! grep -q "^Открыть в " <<<"$JSOUT" && ! grep -qF "Загружаю ключи…" <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
    # TAIL-21: срок страницы — в поясе посетителя с подписью пояса (Intl.DateTimeFormat), момент — в <time datetime>.
    # Пояс задаёт TZ у node; Екатеринбург — без летнего времени, всегда UTC+5.
    exp_iso=$(ga_data "$TMP/page.html" | jq -r .expires)
    exp_at() { python3 -c 'import sys, calendar, time
e = calendar.timegm(time.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ")) + int(sys.argv[2]) * 3600
print(time.strftime("%d.%m.%Y %H:%M", time.gmtime(e)))' "$exp_iso" "$1"; }
    TZ=Asia/Yekaterinburg render_js "$TMP/page.html"
    check "TAIL-21, app.js (node, посетитель в UTC+5): «Страница открывается до $(exp_at 5) GMT+5 — добавьте ключ заранее…», момент — <time datetime=\"$exp_iso\">, без «UTC» и без «через»" \
        'grep -qx "TIME=$exp_iso|$(exp_at 5) GMT+5" <<<"$JSOUT" && grep -qx "Страница открывается до " <<<"$JSOUT" && grep -qx " — добавьте ключ заранее, VPN будет работать и после" <<<"$JSOUT" && ! grep -qE "UTC|через [0-9]" <<<"$JSOUT"' \
        "$(grep -E "^TIME=|открывается|добавьте" <<<"$JSOUT")"
    TZ=UTC render_js "$TMP/page.html" en
    check "…посетитель в UTC, по-английски: «This page opens until $(exp_at 0) UTC — add the key in advance…»" \
        'grep -qx "TIME=$exp_iso|$(exp_at 0) UTC" <<<"$JSOUT" && grep -qx "This page opens until " <<<"$JSOUT"' \
        "$(grep -E "^TIME=|opens" <<<"$JSOUT")"
    render_js "$TMP/page.html" "" "$TMP/keys.json" "~Показать ключ"
    check "ACC5-4: «Показать ключ» нажата из скрипта (element.click(), isTrusted: false) — ключи не запрошены, кнопки на месте" \
        'grep -qx "FETCHES=0" <<<"$JSOUT" && [ "$(grep -c "^Показать ключ$" <<<"$JSOUT")" = 2 ] && ! grep -q "^Скачать \.conf$" <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
    render_js "$TMP/page.html" "" "" "Показать ключ"
    check "…нажато «Показать ключ», ответа ещё нет: один запрос, «Загружаю ключи…», кнопок ключа пока нет" \
        'grep -qx "FETCHES=1" <<<"$JSOUT" && grep -qF "Загружаю ключи…" <<<"$JSOUT" && ! grep -q "^Показать ключ$" <<<"$JSOUT" && ! grep -q "^Скачать \.conf$" <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
    # На Android по умолчанию AmneziaWG (NC-16) — AmneziaVPN с vpn:// и «Открыть в …» выбирают нажатием
    render_js "$TMP/page.html" "" "$TMP/keys.json" "Показать ключ|AmneziaVPN"
    check "…ключи пришли (значение страницы принято): у обоих ключей «Скачать .conf» и QR vpn:// ($n_qr)" \
        'grep -qx "FETCHES=1" <<<"$JSOUT" && [ "$(grep -c "^Скачать \.conf$" <<<"$JSOUT")" = 2 ] && [ "$(grep -c "^QR vpn://$" <<<"$JSOUT")" = "$n_qr" ]' \
        "$(head -c 900 <<<"$JSOUT")"
    check "app.js (node), keys.json пришёл: у обоих ключей «Скопировать vpn://» и «Открыть в AmneziaVPN», строки «Загружаю ключи…» нет; шапка, «Доступ работает» и шаги макета на месте" \
        '! grep -qF "Загружаю ключи…" <<<"$JSOUT" && [ "$(grep -c "^Скопировать vpn://$" <<<"$JSOUT")" = 2 ] && [ "$(grep -c "^Открыть в AmneziaVPN$" <<<"$JSOUT")" = 2 ] && grep -qx "Личная страница" <<<"$JSOUT" && grep -qx "Подключение: Анна" <<<"$JSOUT" && grep -qx "Доступ работает" <<<"$JSOUT" && grep -qx "Устройство и приложение" <<<"$JSOUT" && grep -qx "Ключ для AmneziaVPN" <<<"$JSOUT" && grep -qxE "Профиль 2\.0 · порт [0-9]+" <<<"$JSOUT" && grep -qxE "Профиль 3\.1 · порт [0-9]+" <<<"$JSOUT" && grep -qx "Не пересылайте ссылку: по ней можно получить ваши ключи." <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
    # Приложение выбирается нажатием: у AmneziaWG нет vpn:// — «Скопировать vpn://» и «Открыть в …» пропадают
    render_js "$TMP/page.html" "" "$TMP/keys.json" "Показать ключ|AmneziaWG"
    check "app.js (node), выбрано AmneziaWG: «Ключ для AmneziaWG», у ключей только .conf и QR .conf (vpn:// это приложение не понимает)" \
        'grep -qx "Ключ для AmneziaWG" <<<"$JSOUT" && ! grep -qF "Скопировать vpn://" <<<"$JSOUT" && ! grep -q "^Открыть в " <<<"$JSOUT" && [ "$(grep -c "^Скачать \.conf$" <<<"$JSOUT")" = 2 ] && ! grep -q "^QR vpn://$" <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
    # Вкладку Apple TV по User-Agent не выбрать (на Apple TV браузера нет) — её выбирают нажатием;
    # здесь устройство подменено в данных страницы, как после нажатия на вкладку.
    sed 's/"device": "[a-z_]*"/"device": "tvos"/' "$TMP/page.html" >"$TMP/page_tvos.html"
    render_js "$TMP/page_tvos.html" "" "$TMP/keys.json" "Показать ключ"
    check "app.js (node), вкладка Apple TV: честная пометка и кнопка «VPN на роутере», WG Connect (неофициальное, платное после пробы) с тремя шагами отправки ключа, без QR и «Открыть в …»" \
        'grep -qF "Apple TV" <<<"$JSOUT" && grep -qF "Официального приложения AmneziaWG для Apple TV нет" <<<"$JSOUT" && grep -qx "VPN на роутере" <<<"$JSOUT" && grep -qx "WG Connect" <<<"$JSOUT" && grep -qx "неофициальное" <<<"$JSOUT" && grep -qx "платное после пробы" <<<"$JSOUT" && grep -qF "Неофициальное, платное после пробы" <<<"$JSOUT" && grep -qx "incy" <<<"$JSOUT" && ! grep -q "^Открыть в " <<<"$JSOUT" && ! grep -qx "QR vpn://" <<<"$JSOUT" && ! grep -qx "QR .conf" <<<"$JSOUT" && grep -qx "Как отправить ключ на Apple TV" <<<"$JSOUT" && grep -qx "Откройте WG Connect на Apple TV" <<<"$JSOUT"' \
        "$(head -c 1200 <<<"$JSOUT")"
    check "SEC50-09: на вкладке Apple TV WG Connect — первым (раньше incy), и у него нет строки про сервер incy" \
        '[ "$(grep -nx "WG Connect" <<<"$JSOUT" | head -1 | cut -d: -f1)" -lt "$(grep -nx "incy" <<<"$JSOUT" | head -1 | cut -d: -f1)" ] && ! grep -qF "incy передаёт ключ через свой сервер" <<<"$JSOUT"' \
        "$(head -c 1200 <<<"$JSOUT")"
    render_js "$TMP/page_tvos.html" "" "$TMP/keys.json" "Показать ключ|incy"
    check "SEC50-09: под шагами incy — строка-предупреждение: не в одной сети Wi-Fi ключ идёт через сервер incy (зашифрованным)" \
        'grep -qx "Не в одной сети Wi-Fi incy передаёт ключ через свой сервер (зашифрованным) — держите iPhone и Apple TV в одной сети." <<<"$JSOUT" && [ "$(grep -nx "В incy на iPhone введите код или отсканируйте QR" <<<"$JSOUT" | cut -d: -f1)" -lt "$(grep -n "incy передаёт ключ через свой сервер" <<<"$JSOUT" | cut -d: -f1)" ]' \
        "$(head -c 1200 <<<"$JSOUT")"
    check "…выбрано incy: подсказка платформы «TV relay», а не «Открыть в приложении»; шаги — через iPhone" \
        'grep -qF "откройте «TV relay»" <<<"$JSOUT" && ! grep -qF "«Открыть в приложении»" <<<"$JSOUT" && grep -qx "На Apple TV откройте incy → «TV relay»" <<<"$JSOUT" && ! grep -q "^Открыть в " <<<"$JSOUT"' \
        "$(head -c 1200 <<<"$JSOUT")"
    render_js "$TMP/page_tvos.html" en "$TMP/keys.json" "Show the key|incy"
    check "…по-английски: «There is no official AmneziaWG app for Apple TV», «paid after a trial», «VPN on the router», «TV relay»" \
        'grep -qF "There is no official AmneziaWG app for Apple TV" <<<"$JSOUT" && grep -qx "paid after a trial" <<<"$JSOUT" && grep -qx "VPN on the router" <<<"$JSOUT" && grep -qF "open «TV relay» in incy on Apple TV" <<<"$JSOUT" && grep -qx "Without a shared Wi-Fi, incy sends the key through its own server (encrypted) — keep the iPhone and Apple TV on the same network." <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
    render_js "$TMP/page_tvos.html" "" "$TMP/keys.json" "Показать ключ|VPN на роутере"
    check "…«VPN на роутере» ведёт на вкладку «Роутер»: роутерные приложения каталога (Keenetic / Netcraze, OpenWrt)" \
        'grep -qx "Keenetic / Netcraze" <<<"$JSOUT" && grep -qx "OpenWrt (amneziawg)" <<<"$JSOUT" && ! grep -qF "Официального приложения AmneziaWG для Apple TV нет" <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
    render_js "$TMP/page.html" en "$TMP/keys.json" "Show the key|Router"
    # Кириллицу ищет python: у grep класс [А-Я] зависит от локали и в C-локали ловит байты «—» и «·»
    cyr_lines() { python3 -c 'import re,sys; print("\n".join(l for l in sys.stdin.read().split("\n") if "Анна" not in l and re.search("[А-Яа-яЁё]", l)))'; }
    check "английский без русских слов (кроме подписи человека): вкладка «Router», «MikroTik (container)», «OPNsense (AmneziaWG plugin)»" \
        'grep -qx "MikroTik (container)" <<<"$JSOUT" && grep -qx "OPNsense (AmneziaWG plugin)" <<<"$JSOUT" && [ -z "$(cyr_lines <<<"$JSOUT")" ]' \
        "$(cyr_lines <<<"$JSOUT" | head -5)"
    # NC-16: на телефоне по умолчанию — приложение из российских магазинов (по пометкам каталога — AmneziaWG) с пометкой
    # «рекомендуем», первым в списке; на компьютере — как прежде, первое по каталогу. Таблица версий — под «Подробнее»,
    # фраза «На Android …» — только на Android. Устройство подменено в данных страницы, как после нажатия на вкладку.
    sed 's/"device": "[a-z_]*"/"device": "ios"/' "$TMP/page.html" >"$TMP/page_ios.html"
    sed 's/"device": "[a-z_]*"/"device": "windows"/' "$TMP/page.html" >"$TMP/page_win.html"
    render_js "$TMP/page_ios.html"
    check "NC-16, iPhone: по умолчанию AmneziaWG — первым, выбран («Ключ для AmneziaWG»), с пометкой «рекомендуем» (одна на странице); у AmneziaVPN, скрытого в российском App Store, пометки нет" \
        'grep -qx "Ключ для AmneziaWG" <<<"$JSOUT" && [ "$(grep -cx "рекомендуем" <<<"$JSOUT")" = 1 ] && [ "$(grep -A2 -x "AmneziaWG" <<<"$JSOUT" | sed -n 3p)" = "рекомендуем" ] && [ "$(grep -nx "AmneziaWG" <<<"$JSOUT" | head -1 | cut -d: -f1)" -lt "$(grep -nx "AmneziaVPN" <<<"$JSOUT" | head -1 | cut -d: -f1)" ] && [ "$(grep -A1 -x "AmneziaVPN" <<<"$JSOUT" | sed -n 2p)" = "официальное" ] && [ "$(grep -A2 -x "AmneziaVPN" <<<"$JSOUT" | sed -n 3p)" != "рекомендуем" ]' \
        "$(head -c 1200 <<<"$JSOUT")"
    check "NC-16: таблицы версий в карточках нет — только кнопка «Подробнее» у выбранного приложения" \
        '[ "$(grep -cx "Подробнее" <<<"$JSOUT")" = 1 ] && ! grep -qx "с 3.1.4" <<<"$JSOUT" && ! grep -qx "с 5.0.1" <<<"$JSOUT" && ! grep -qx "Линейки AmneziaWG в этом приложении" <<<"$JSOUT"' \
        "$(head -c 1200 <<<"$JSOUT")"
    render_js "$TMP/page_ios.html" "" "" "Подробнее"
    check "…нажато «Подробнее»: «Линейки AmneziaWG в этом приложении» и версии («с 3.1.4»)" \
        'grep -qx "Линейки AmneziaWG в этом приложении" <<<"$JSOUT" && grep -qx "с 3.1.4" <<<"$JSOUT" && grep -qx "3.1" <<<"$JSOUT"' \
        "$(head -c 1200 <<<"$JSOUT")"
    render_js "$TMP/page.html"
    check "NC-16, Android: тоже AmneziaWG с «рекомендуем» (у ссылок Google Play пометок о российском магазине нет — правило владельца); в данных страницы recommend — ios и android" \
        'grep -qx "Ключ для AmneziaWG" <<<"$JSOUT" && [ "$(grep -A2 -x "AmneziaWG" <<<"$JSOUT" | sed -n 3p)" = "рекомендуем" ] && [ "$(ga_data "$TMP/page.html" | jq -c .recommend)" = "{\"ios\":\"amneziawg\",\"android\":\"amneziawg\"}" ]' \
        "$(ga_data "$TMP/page.html" | jq -c .recommend 2>&1) $(head -c 600 <<<"$JSOUT")"
    render_js "$TMP/page_win.html"
    check "NC-16, Windows: рекомендаций нет — по умолчанию первое по каталогу (AmneziaVPN), пометки «рекомендуем» нет" \
        'grep -qx "Ключ для AmneziaVPN" <<<"$JSOUT" && ! grep -qx "рекомендуем" <<<"$JSOUT"' "$(head -c 900 <<<"$JSOUT")"
    render_js "$TMP/page_ios.html" "" "" "AmneziaVPN"
    check "NC-16: на iPhone у AmneziaVPN подсказка без «На Android ссылка vpn:// открывает приложение сама»…" \
        'grep -qx "Ключ для AmneziaVPN" <<<"$JSOUT" && grep -qF "vpn:// — скопировать или показать QR vpn://" <<<"$JSOUT" && ! grep -qF "На Android" <<<"$JSOUT"' \
        "$(grep -F "vpn:// —" <<<"$JSOUT")"
    render_js "$TMP/page.html" "" "" "AmneziaVPN"
    check "…а на Android — с ней" 'grep -qF "На Android ссылка vpn:// открывает приложение сама" <<<"$JSOUT"' "$(grep -F "vpn:// —" <<<"$JSOUT")"
    render_js "$TMP/page_ios.html" en "" "Details"
    check "NC-16 по-английски: «recommended», «Details» → «AmneziaWG lines in this app», «from 3.1.4»; без русских слов" \
        'grep -qx "recommended" <<<"$JSOUT" && grep -qx "AmneziaWG lines in this app" <<<"$JSOUT" && grep -qx "from 3.1.4" <<<"$JSOUT" && [ -z "$(cyr_lines <<<"$JSOUT")" ]' \
        "$(cyr_lines <<<"$JSOUT" | head -5)"
    # NC-17: после «Скачать .conf» и «Скопировать» — «Дальше — в <приложение>»: 2–3 шага, куда нажать (подписи кнопок
    # сверены с исходниками приложений); под кнопками ключа — «QR — для второго устройства»; над ключами — какой брать.
    render_js "$TMP/page_ios.html" "" "$TMP/keys.json" "Показать ключ"
    check "NC-17: ключи открыты, действий ещё не было — шагов нет; «QR — для второго устройства» под кнопками каждого ключа с QR ($n_qr); над ключами «Берите ключ 3.1; не подключился — 2.0.»" \
        '! grep -q "^Дальше — в " <<<"$JSOUT" && [ "$(grep -cx "QR — для второго устройства" <<<"$JSOUT")" = "$n_qr" ] && grep -qx "Берите ключ 3.1; не подключился — 2.0." <<<"$JSOUT" && [ "$(grep -nx "Берите ключ 3.1; не подключился — 2.0." <<<"$JSOUT" | cut -d: -f1)" -lt "$(grep -nxE "Профиль 2\.0 · порт [0-9]+" <<<"$JSOUT" | cut -d: -f1)" ]' \
        "$(head -c 1500 <<<"$JSOUT")"
    render_js "$TMP/page_ios.html" "" "$TMP/keys.json" "Показать ключ|Скачать .conf"
    check "NC-17, iPhone + AmneziaWG, нажато «Скачать .conf»: «Дальше — в AmneziaWG» — «Загрузить», «+» → «Создать из файла или архива», переключатель; карточка одна и сразу под этим ключом" \
        'grep -qx "Нажмите «Загрузить»" <<<"$JSOUT" && grep -qx "Файл anna-3_1.conf попадёт в «Файлы» → «Загрузки»" <<<"$JSOUT" && grep -qx "В AmneziaWG: «+» → «Создать из файла или архива»" <<<"$JSOUT" && grep -qx "Включите туннель переключателем" <<<"$JSOUT" && [ "$(grep -cx "Дальше — в AmneziaWG" <<<"$JSOUT")" = 1 ] && [ "$(grep -nxE "Профиль 3\.1 · порт [0-9]+" <<<"$JSOUT" | cut -d: -f1)" -lt "$(grep -nx "Дальше — в AmneziaWG" <<<"$JSOUT" | cut -d: -f1)" ] && [ "$(grep -nx "Дальше — в AmneziaWG" <<<"$JSOUT" | cut -d: -f1)" -lt "$(grep -nxE "Профиль 2\.0 · порт [0-9]+" <<<"$JSOUT" | cut -d: -f1)" ]' \
        "$(head -c 1500 <<<"$JSOUT")"
    render_js "$TMP/page.html" "" "$TMP/keys.json" "Показать ключ|Скачать .conf"
    check "NC-17, Android + AmneziaWG: «+» → «Импорт из файла или архива», файл в «Загрузках», переключатель и разрешение VPN" \
        'grep -qx "В AmneziaWG: «+» → «Импорт из файла или архива»" <<<"$JSOUT" && grep -qx "Выберите anna-3_1.conf в «Загрузках»" <<<"$JSOUT" && grep -qx "Android попросит разрешить VPN — согласитесь" <<<"$JSOUT"' \
        "$(head -c 1500 <<<"$JSOUT")"
    render_js "$TMP/page_win.html" "" "$TMP/keys.json" "Показать ключ|Скопировать vpn://"
    check "NC-17, Windows + AmneziaVPN, «Скопировать vpn://»: «+» (при первом запуске «Приступим») → «Вставить» → «Продолжить» → «Подключиться»" \
        'grep -qx "Дальше — в AmneziaVPN" <<<"$JSOUT" && grep -qx "Откройте AmneziaVPN и нажмите «+»" <<<"$JSOUT" && grep -qx "При первом запуске — «Приступим»" <<<"$JSOUT" && grep -qx "«Вставить» → «Продолжить»" <<<"$JSOUT" && grep -qx "«Подключиться»" <<<"$JSOUT" && grep -qx "Статус станет «Подключено»" <<<"$JSOUT"' \
        "$(head -c 1500 <<<"$JSOUT")"
    render_js "$TMP/page_win.html" "" "$TMP/keys.json" "Показать ключ|Скачать .conf"
    check "NC-17, Windows + AmneziaVPN, «Скачать .conf»: «Файл с настройками подключения» и файл в «Загрузках»" \
        'grep -qx "«Файл с настройками подключения»" <<<"$JSOUT" && grep -qx "Выберите anna-3_1.conf в «Загрузках»" <<<"$JSOUT" && ! grep -qx "«Вставить» → «Продолжить»" <<<"$JSOUT"' \
        "$(head -c 1500 <<<"$JSOUT")"
    render_js "$TMP/page_win.html" "" "$TMP/keys.json" "WireSock Secure Connect|Показать ключ|Скачать .conf"
    check "NC-17, приложение без своих шагов (WireSock): общие — «Файл скачан», «Добавьте его в WireSock Secure Connect» с подсказкой каталога; 3.1 WireSock не понимает — совета о линейке нет" \
        'grep -qx "Дальше — в WireSock Secure Connect" <<<"$JSOUT" && grep -qx "Файл скачан" <<<"$JSOUT" && grep -qx "anna-2_0.conf — в «Загрузках»" <<<"$JSOUT" && grep -qx "Добавьте его в WireSock Secure Connect" <<<"$JSOUT" && ! grep -q "^Берите ключ" <<<"$JSOUT"' \
        "$(head -c 1500 <<<"$JSOUT")"
    render_js "$TMP/page.html" en "$TMP/keys.json" "Show the key|Download .conf"
    check "NC-17 по-английски: «Next, in AmneziaWG», «Import from file or archive», «QR is for a second device», «Take the 3.1 key…»; без русских слов" \
        'grep -qx "Next, in AmneziaWG" <<<"$JSOUT" && grep -qx "In AmneziaWG: “+” → “Import from file or archive”" <<<"$JSOUT" && { [ "$HAVE_QR" != 1 ] || grep -qx "QR is for a second device" <<<"$JSOUT"; } && grep -qx "Take the 3.1 key; if it does not connect — 2.0." <<<"$JSOUT" && [ -z "$(cyr_lines <<<"$JSOUT")" ]' \
        "$(cyr_lines <<<"$JSOUT" | head -5) $(head -c 600 <<<"$JSOUT")"
    render_js "$TMP/page_tvos.html" "" "$TMP/keys.json" "Показать ключ|Скачать .conf"
    check "NC-17, Apple TV: свои три шага отправки ключа, без «Дальше — в …» и без подписи про QR (QR там нет)" \
        'grep -qx "Как отправить ключ на Apple TV" <<<"$JSOUT" && ! grep -q "^Дальше — в " <<<"$JSOUT" && ! grep -qx "QR — для второго устройства" <<<"$JSOUT"' \
        "$(head -c 1200 <<<"$JSOUT")"
    # Телевизор (Android TV по User-Agent): крупный список под пульт
    sed 's/"device": "[a-z_]*"/"device": "android_tv"/' "$TMP/page.html" >"$TMP/page_atv.html"
    render_js "$TMP/page_atv.html" "" ""
    check "SEC50-05, телевизор до нажатия: ключи не запрошены, в списке «Показать ключ» вместо ключей, файла и QR" \
        'grep -qx "FETCHES=0" <<<"$JSOUT" && grep -qx "Показать ключ" <<<"$JSOUT" && ! grep -qx "Открыть ключ в AmneziaVPN" <<<"$JSOUT" && ! grep -qx "Скачать файл .conf" <<<"$JSOUT" && ! grep -qx "Показать QR ключа" <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
    render_js "$TMP/page_atv.html" "" "$TMP/keys.json" "~Показать ключ"
    check "ACC5-4, телевизор: пункт «Показать ключ», нажатый из скрипта, ключей не открывает" \
        'grep -qx "FETCHES=0" <<<"$JSOUT" && grep -qx "Показать ключ" <<<"$JSOUT" && ! grep -qx "Скачать файл .conf" <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
    # ── Вкладки «Статический ключ» | «Подписка» (решение владельца 04.10.2026) ──
    # Подписка в данных страницы включена (как у человека с подпиской: sub.on и своё значение reveal_sub); адрес
    # приходит только ответом sub.json по первому нажатию «QR-код» или «Скопировать» — здесь это файл $TMP/sub.json
    # ($TMP/sub_short.json — то же с полем short; третья кнопка «Короткая ссылка» — только когда в данных страницы
    # sub.short: короткие ссылки работают лишь с 2FA через Telegram-бота, $TMP/page_sub_short.html).
    python3 - "$TMP/page.html" "$TMP/page_sub.html" "$TMP/page_nosub.html" "$TMP/page_sub_ios.html" "$TMP/sub.json" \
        "$TMP/sub_short.json" "$TMP/page_sub_short.html" "$TMP/page_suboff.html" <<'EOPY'
import json, re, sys
src, with_sub, no_sub, ios, answer, short, short_page, sub_off = sys.argv[1:9]
html = open(src, encoding="utf-8").read()
m = re.search(r'(<script type="application/json" id="ga-data">)(.*?)(</script>)', html, re.S)
def put(path, data):
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    open(path, "w", encoding="utf-8").write(html[:m.start(2)] + blob + html[m.end(2):])
data = json.loads(m.group(2))
data["sub"] = {"on": True, "hours": 12, "lines": ["2.0", "3.1"]}
data["reveal_sub"] = "R" * 32
put(with_sub, data)
put(short_page, dict(data, sub=dict(data["sub"], short=True), reveal_short="T" * 32))
put(ios, dict(data, device="ios"))
off = dict(data, sub={"on": False, "hours": 12, "lines": ["2.0", "3.1"]})
off.pop("reveal_sub")
put(no_sub, off)
put(sub_off, dict(off, sub=dict(off["sub"], was=True)))
base = "https://example.com/s-" + "A" * 43 + "/"
sub = {"mihomo": base + "all.yaml", "txt": base + "all.txt",
       "conf": {"2.0": base + "awg20.conf", "3.1": base + "awg31.conf"},
       "vpn": {"2.0": base + "awg20.vpn", "3.1": base + "awg31.vpn"},
       "lines": ["2.0", "3.1"], "hours": 12, "title": "Анна"}
json.dump({"sub": sub, "grant": "G" * 32}, open(answer, "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"sub": dict(sub, short={"mihomo": "https://example.com/K7QX2M9A"}), "grant": "G" * 32},
          open(short, "w", encoding="utf-8"), ensure_ascii=False)
EOPY
    SUB_PATH="/s-$(printf 'A%.0s' {1..43})"
    SUB_URL="https://example.com$SUB_PATH/all.yaml"
    G32=$(printf 'G%.0s' {1..32})
    render_js "$TMP/page_sub.html"
    check "вкладки (в подвале — «Не пересылайте ссылку»): по умолчанию «Статический ключ» (подпись «добавить один раз, любое приложение»), рядом «Подписка» («ключ обновляется сам»); на статической — прежние шаги и строка «Сменится ключ или порт — откройте эту страницу снова.»" \
        'grep -qx "Не пересылайте ссылку: по ней можно получить ваши ключи." <<<"$JSOUT" && grep -qx "Статический ключ" <<<"$JSOUT" && grep -qx "добавить один раз, любое приложение" <<<"$JSOUT" && grep -qx "Подписка" <<<"$JSOUT" && grep -qx "ключ обновляется сам" <<<"$JSOUT" && grep -qx "Устройство и приложение" <<<"$JSOUT" && [ "$(grep -c "^Показать ключ$" <<<"$JSOUT")" = 2 ] && grep -qx "Сменится ключ или порт — откройте эту страницу снова." <<<"$JSOUT" && ! grep -qx "QR-код" <<<"$JSOUT" && grep -qx "TAB=" <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
    render_js "$TMP/page_sub.html" "" "" "Подписка"
    check "нажата «Подписка»: вкладка запомнена (ga_tab=sub); Android — FlClash «рекомендуем» первым, «обновляется сам»; карточка «Профиль Clash (mihomo)», ряд «QR-код» (для сканера в приложении) и «Скопировать» (ссылку), третьей кнопки нет; адрес скрыт (s-••••/all.yaml), строка выбора; «Открыть в FlClash» — строкой под рядом; запросов адреса нет; «не пересылайте» — одно, у ссылки подписки (подвал его не повторяет, PAGE-COS-01)" \
        'grep -qx "TAB=sub" <<<"$JSOUT" && ! grep -qx "Не пересылайте ссылку: по ней можно получить ваши ключи." <<<"$JSOUT" && [ "$(grep -ci "пересылайте" <<<"$JSOUT")" = 1 ] && grep -qx "Подписка для FlClash" <<<"$JSOUT" && [ "$(grep -A3 -x "FlClash" <<<"$JSOUT" | sed -n 3,4p | tr "\n" "|")" = "рекомендуем|обновляется сам|" ] && grep -qx "Профиль Clash (mihomo)" <<<"$JSOUT" && grep -qx "Все линии в одной подписке, приложение выбирает само; свежий ключ — раз в сутки." <<<"$JSOUT" && [ "$(grep -A1 -x "QR-код" <<<"$JSOUT" | sed -n 2p)" = "для сканера в приложении" ] && [ "$(grep -A1 -x "Скопировать" <<<"$JSOUT" | sed -n 2p)" = "ссылку, чтобы вставить" ] && ! grep -qx "Короткая ссылка" <<<"$JSOUT" && grep -qx "https://example.com/s-••••/all.yaml" <<<"$JSOUT" && grep -qx "Выберите, как удобнее: QR — навести камеру, «Скопировать» — вставить в приложение." <<<"$JSOUT" && [ "$(grep -A1 -x "Приложение уже стоит на этом устройстве?" <<<"$JSOUT" | sed -n 2p)" = "Открыть в FlClash" ] && grep -qx "Ссылка постоянная и даёт ваш ключ — не пересылайте её." <<<"$JSOUT" && grep -qx "SUBFETCHES=0" <<<"$JSOUT" && ! grep -q "/s-AAAA" <<<"$JSOUT" && ! grep -q "^HREF=" <<<"$JSOUT" && ! grep -qx "Показать ключ" <<<"$JSOUT" && ! grep -qx "Показать ссылку" <<<"$JSOUT"' \
        "$(head -c 1600 <<<"$JSOUT")"
    check "копия сборки на вкладке «Подписка»: у FlClash на Android (файлы с GitHub) — блок «Запросить копию на наш сервер», как на «Статическом ключе»" \
        'grep -qx "FlClash" <<<"$JSOUT" && grep -qx "Копии на нашем сервере ещё нет" <<<"$JSOUT" && grep -qx "Запросить копию на наш сервер" <<<"$JSOUT"' \
        "$(grep -n "Копи\|копи" <<<"$JSOUT" | head -5)"
    # MIRROR-01: пока копия в очереди или загружается, кнопки «Запросить копию на наш сервер» нет — состояние
    # заголовком и полосой; на «Статическом ключе» (AmneziaVPN, в очереди) и на «Подписке» (FlClash, 50 %)
    python3 - "$TMP/page_sub.html" "$TMP/page_mir.html" <<'EOPY'
import json, re, sys
src, dst = sys.argv[1:3]
html = open(src, encoding="utf-8").read()
m = re.search(r'(<script type="application/json" id="ga-data">)(.*?)(</script>)', html, re.S)
data = json.loads(m.group(2))
data["mirror_enabled"] = True
for a in data["apps"]:
    if a["id"] == "amneziavpn":
        a["platforms"]["android"]["mirror"] = {"state": "queued", "available": True}
for a in data["sub_apps"]:
    if a["id"] == "flclash":
        a["platforms"]["android"]["mirror"] = {"state": "downloading", "available": True, "bytes": 5242880,
                                               "total": 10485760, "version": "v0.8.99",
                                               "file": "FlClash-0.8.99-android-arm64-v8a.apk"}
blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
open(dst, "w", encoding="utf-8").write(html[:m.start(2)] + blob + html[m.end(2):])
EOPY
    render_js "$TMP/page_mir.html" "" "" "Android|AmneziaVPN"
    mir_static="$JSOUT"
    GA_TAB=sub render_js "$TMP/page_mir.html" "" "" "Android|FlClash"
    check "MIRROR-01: копия в очереди («Статический ключ», AmneziaVPN) и загружается («Подписка», FlClash, 50 %) — кнопки «Запросить копию на наш сервер» нет, состояние словами" \
        'grep -qx "В очереди на загрузку" <<<"$mir_static" && grep -qx "Страница обновит состояние сама" <<<"$mir_static" && ! grep -qx "Запросить копию на наш сервер" <<<"$mir_static" && grep -qx "Загружается, 50 %" <<<"$JSOUT" && ! grep -qx "Запросить копию на наш сервер" <<<"$JSOUT"' \
        "$(grep -n "очеред\|Загружается\|Запросить" <<<"$mir_static$JSOUT" | head -6)"
    JSOUT=""
    render_js "$TMP/page_sub.html" "" "" "Подписка"
    JSOUT_SUB="$JSOUT"
    render_js "$TMP/page_sub.html" "" "" "Подписка|Статический ключ"
    check "назад на «Статический ключ» — в подвале снова «Не пересылайте ссылку» (одна строка)" \
        '[ "$(grep -cx "Не пересылайте ссылку: по ней можно получить ваши ключи." <<<"$JSOUT")" = 1 ]' "$(tail -c 600 <<<"$JSOUT")"
    JSOUT="$JSOUT_SUB"
    check "…список Android: FlClash, Clash Meta, incy, AmneziaVPN, AmneziaWG, WG Tunnel (обновляется сам — первым, разовое — после)" \
        'p=0; for n in FlClash "Clash Meta for Android" incy AmneziaVPN AmneziaWG "WG Tunnel"; do c=$(grep -nxF "$n" <<<"$JSOUT" | head -1 | cut -d: -f1); [ -n "$c" ] && [ "$c" -gt "$p" ] || exit 1; p=$c; done' \
        "$(grep -nx "FlClash\|Clash Meta for Android\|incy\|AmneziaVPN\|AmneziaWG\|WG Tunnel" <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub.html" "" "" "~Скопировать|~QR-код" "$TMP/sub.json"
    check "запомненная вкладка «Подписка» открывается сразу; «Скопировать» и «QR-код», нажатые из скрипта (isTrusted: false), адреса не просят и в буфер ничего не кладут" \
        'grep -qx "Подписка для FlClash" <<<"$JSOUT" && grep -qx "SUBFETCHES=0" <<<"$JSOUT" && ! grep -q "^COPIED=" <<<"$JSOUT" && grep -qx "https://example.com/s-••••/all.yaml" <<<"$JSOUT" && ! grep -q "/s-AAAA" <<<"$JSOUT"' "$(head -c 900 <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub.html" "" "" "Скопировать" "$TMP/sub.json"
    check "«Скопировать» (FlClash): один POST sub.json своим значением, в буфер — адрес all.yaml; зелёная строка «Скопировано: ссылка подписки. Теперь вставьте в FlClash: …», под ней адрес; кнопка — «Скопировано»; «Открыть в FlClash» — ссылка flclash://install-config (адрес закодирован)" \
        'grep -qx "SUBFETCHES=1" <<<"$JSOUT" && grep -qxF "COPIED=$SUB_URL" <<<"$JSOUT" && grep -qx "Скопировано: ссылка подписки. Теперь вставьте в FlClash: «Профили» → «+» → «URL», затем «Отправить»." <<<"$JSOUT" && grep -qxF "$SUB_URL" <<<"$JSOUT" && grep -qx "Скопировано" <<<"$JSOUT" && grep -qxF "HREF=flclash://install-config?url=https%3A%2F%2Fexample.com%2Fs-$(printf "A%.0s" {1..43})%2Fall.yaml" <<<"$JSOUT" && ! grep -qx "Короткая ссылка" <<<"$JSOUT"' \
        "$(head -c 1800 <<<"$JSOUT") $(grep -E "^(HREF|COPIED|SUBFETCHES)=" <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub.html" "" "" "Скопировать|Короткая ссылка" "$TMP/sub_short.json"
    check "короткие ссылки работают только с 2FA через Telegram-бота: бот не настроен (sub.short нет) — третьей кнопки нет, даже если в ответе sub.json есть short" \
        '! grep -qx "Короткая ссылка" <<<"$JSOUT" && grep -qx "NO-CLICK Короткая ссылка" <<<"$JSOUT" && ! grep -q "K7QX2M9A" <<<"$JSOUT"' "$(head -c 1500 <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub_short.html" "" "" "Скопировать|Короткая ссылка" "$TMP/sub_short.json"
    check "бот настроен (sub.short): в ряду третья кнопка «Короткая ссылка» (набрать руками, 2FA в Telegram); код уже есть в ответе sub.json — по нажатию блок: домен и код, ↻, «Скопировать», плашка «2FA через Telegram-бота», три шага и риск" \
        'grep -qx "Короткая ссылка" <<<"$JSOUT" && grep -qx "набрать руками, 2FA в Telegram" <<<"$JSOUT" && grep -qx "example.com/" <<<"$JSOUT" && grep -qx "K7QX2M9A" <<<"$JSOUT" && grep -qx "Короткие ссылки работают с 2FA через Telegram-бота." <<<"$JSOUT" && grep -qx "Каждый новый вход и замену короткой ссылки подтверждает администратор в боте." <<<"$JSOUT" && grep -qx "Как войти по короткой ссылке — 2FA через Telegram-бота" <<<"$JSOUT" && grep -qx "Откройте её в браузере этого устройства — на экране появится число." <<<"$JSOUT" && grep -qF "Три неверных кода подряд — бан адреса на сутки." <<<"$JSOUT" && grep -qx "PRESSED=Короткая ссылка" <<<"$JSOUT"' "$(head -c 2500 <<<"$JSOUT")"
    if [ "$HAVE_QR" = 1 ]; then
        GA_TAB=sub render_js "$TMP/page_sub.html" "" "" "QR-код" "$TMP/sub.json"
        check "«QR-код» (FlClash на телефоне): один POST sub.json, QR адреса all.yaml (qr/sub/mihomo.png с токеном из ответа), «Отсканируйте в приложении на другом устройстве»; кнопка нажата" \
            'grep -qx "SUBFETCHES=1" <<<"$JSOUT" && grep -qxF "IMG=qr/sub/mihomo.png?g=$G32" <<<"$JSOUT" && grep -qx "Отсканируйте в приложении на другом устройстве" <<<"$JSOUT" && grep -qx "PRESSED=QR-код" <<<"$JSOUT" && ! grep -qx "https://example.com/s-••••/all.yaml" <<<"$JSOUT"' \
            "$(grep -E "^(IMG|PRESSED|SUBFETCHES)=" <<<"$JSOUT")"
        GA_TAB=sub render_js "$TMP/page_sub.html" "" "" "QR-код|QR-код" "$TMP/sub.json"
        check "…второе нажатие «QR-код» прячет QR — снова скрытый адрес, без нового запроса" \
            'grep -qx "SUBFETCHES=1" <<<"$JSOUT" && ! grep -q "^IMG=" <<<"$JSOUT" && grep -qx "https://example.com/s-••••/all.yaml" <<<"$JSOUT"' "$(grep -E "^(IMG|SUBFETCHES)=" <<<"$JSOUT")"
    fi
    GA_TAB=sub render_js "$TMP/page_sub.html" "" "" "Clash Meta for Android"
    check "Clash Meta for Android: QR неактивен — «сканера нет» (у приложения его нет)" \
        '[ "$(grep -A1 -x "QR-код" <<<"$JSOUT" | sed -n 2p)" = "сканера нет" ] && grep -qx "DISABLED=QR-код" <<<"$JSOUT"' "$(grep -A1 -x "QR-код" <<<"$JSOUT") $(grep "^DISABLED=" <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub.html" "" "" "Windows|QR-код|Скопировать" "$TMP/sub.json"
    check "Windows: Clash Verge Rev «рекомендуем»; QR неактивен — «на компьютере не нужен» (нажатие ничего не просит), строка «Нажмите «Скопировать» …»; «Скопировать» — адрес и путь в Clash Verge Rev; ссылка clash-verge://install-config — name до url; свежий ключ — раз в 12 часов" \
        'grep -qx "Подписка для Clash Verge Rev" <<<"$JSOUT" && grep -qx "NO-CLICK QR-код" <<<"$JSOUT" && [ "$(grep -A1 -x "QR-код" <<<"$JSOUT" | sed -n 2p)" = "на компьютере не нужен" ] && grep -qx "SUBFETCHES=1" <<<"$JSOUT" && grep -qx "Скопировано: ссылка подписки. Теперь вставьте в Clash Verge Rev: «Профили» → поле «URL профиля» → «Импортировать»." <<<"$JSOUT" && grep -qxF "HREF=clash-verge://install-config?name=%D0%90%D0%BD%D0%BD%D0%B0&url=https%3A%2F%2Fexample.com%2Fs-$(printf "A%.0s" {1..43})%2Fall.yaml" <<<"$JSOUT" && grep -qx "Все линии в одной подписке, приложение выбирает само; свежий ключ — раз в 12 часов." <<<"$JSOUT"' \
        "$(grep -E "^HREF=|^NO-CLICK|Подписка для|свежий|Скопировано:" <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_sub.html" "" "" "Windows|FlClash"
    check "…FlClash на Windows: QR у приложения есть, но на компьютере — «на компьютере не нужен»; строка выбора без QR; порядок: Clash Verge Rev, FlClash, Clash Party, AmneziaVPN, AmneziaWG" \
        '[ "$(grep -A1 -x "QR-код" <<<"$JSOUT" | sed -n 2p)" = "на компьютере не нужен" ] && grep -qx "Нажмите «Скопировать» и вставьте, как написано ниже." <<<"$JSOUT" && p=0 && for n in "Clash Verge Rev" FlClash "Clash Party (Mihomo Party)" AmneziaVPN AmneziaWG; do c=$(grep -nxF "$n" <<<"$JSOUT" | head -1 | cut -d: -f1); [ -n "$c" ] && [ "$c" -gt "$p" ] || exit 1; p=$c; done' \
        "$(grep -nx "Clash Verge Rev\|FlClash\|Clash Party (Mihomo Party)\|AmneziaVPN\|AmneziaWG\|QR-код" <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub.html" "" "" "Роутер|Скопировать" "$TMP/sub.json"
    check "Роутер: Keenetic / Netcraze первым, AWG Manager — вторым; «Ключ для роутера», разово; QR неактивен — «роутеру не нужен»; «Скопировать» кладёт готовую команду import с адресом awg20.conf (3.1 KeeneticOS не умеет — линии одной, переключателя нет); зелёная строка — куда вставить" \
        'k=$(grep -nx "Keenetic / Netcraze" <<<"$JSOUT" | head -1 | cut -d: -f1) && w=$(grep -nx "AWG Manager" <<<"$JSOUT" | head -1 | cut -d: -f1) && [ "$k" -lt "$w" ] && grep -qx "Ключ для Keenetic / Netcraze по ссылке" <<<"$JSOUT" && grep -qx "Ключ для роутера" <<<"$JSOUT" && grep -qx "Разово: Keenetic / Netcraze сам по ссылке не обновляется — сменится ключ или порт, добавьте ключ заново." <<<"$JSOUT" && [ "$(grep -A1 -x "QR-код" <<<"$JSOUT" | sed -n 2p)" = "роутеру не нужен" ] && [ "$(grep -A1 -x "Скопировано" <<<"$JSOUT" | sed -n 2p)" = "команду, чтобы вставить" ] && grep -qxF "COPIED=interface Wireguard9 wireguard import https://example.com$SUB_PATH/awg20.conf" <<<"$JSOUT" && grep -qx "Скопировано: команда для роутера. Теперь вставьте в командную строку роутера (my.keenetic.net/a), затем выполните system configuration save." <<<"$JSOUT" && grep -qxF "interface Wireguard9 wireguard import https://example.com$SUB_PATH/awg20.conf" <<<"$JSOUT" && ! grep -qx "Линия" <<<"$JSOUT" && ! grep -q "awg31.conf" <<<"$JSOUT" && grep -qF "KeeneticOS 5.2" <<<"$JSOUT"' \
        "$(head -c 2000 <<<"$JSOUT") $(grep "^COPIED=" <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_sub.html" "" "" "Роутер"
    check "…до нажатия у роутера — команда со скрытым адресом" \
        'grep -qx "interface Wireguard9 wireguard import https://example.com/s-••••/awg20.conf" <<<"$JSOUT" && grep -qx "SUBFETCHES=0" <<<"$JSOUT"' "$(head -c 1500 <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_sub.html" "" "" "Роутер|AWG Manager"
    check "AWG Manager: переключатель «Формат» — «Все линии .txt», «Ключ vpn://», «Файл .conf»; по умолчанию all.txt, обновляется сам; QR — «роутеру не нужен»" \
        'grep -qx "Подписка для AWG Manager" <<<"$JSOUT" && grep -qx "Формат" <<<"$JSOUT" && grep -qx "Все линии .txt" <<<"$JSOUT" && grep -qx "Ключ vpn://" <<<"$JSOUT" && grep -qx "Файл .conf" <<<"$JSOUT" && grep -qx "PRESSED=Все линии .txt" <<<"$JSOUT" && grep -qx "Подписка AWG Manager" <<<"$JSOUT" && grep -qx "Все линии в одной подписке; свежий ключ — раз в 12 часов." <<<"$JSOUT" && grep -qx "https://example.com/s-••••/all.txt" <<<"$JSOUT" && [ "$(grep -A1 -x "QR-код" <<<"$JSOUT" | sed -n 2p)" = "роутеру не нужен" ] && [ "$(grep -A2 -x "AWG Manager" <<<"$JSOUT" | sed -n 3p)" = "обновляется сам" ]' \
        "$(head -c 1800 <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub.html" "" "" "Роутер|AWG Manager|Ключ vpn://|Скопировать" "$TMP/sub.json"
    check "…«Ключ vpn://»: разово, переключатель линий 3.1 / 2.0 с «Для 3.1 — режим Kernel, 2.0 работает в любом»; «Скопировать» — сам ключ vpn:// линии 3.1 (GET адреса awg31.vpn с этой же страницы), куда вставить в AWG Manager" \
        'grep -qx "Ключ для AWG Manager по ссылке" <<<"$JSOUT" && grep -qx "Линия" <<<"$JSOUT" && grep -qx "Для 3.1 — режим Kernel, 2.0 работает в любом" <<<"$JSOUT" && grep -qx "PRESSED=3.1" <<<"$JSOUT" && grep -qxF "VPNFETCH=$SUB_PATH/awg31.vpn|GET|no-store|same-origin" <<<"$JSOUT" && grep -qx "COPIED=vpn://FAKE-awg31" <<<"$JSOUT" && grep -qx "Скопировано: ключ vpn://. Теперь вставьте в AWG Manager: «Новый туннель» → «Вставить ссылку» → «Импортировать»." <<<"$JSOUT" && grep -qx "vpn://FAKE-awg31" <<<"$JSOUT" && [ "$(grep -A2 -x "AWG Manager" <<<"$JSOUT" | sed -n 3p)" = "разово" ]' \
        "$(head -c 2000 <<<"$JSOUT") $(grep -E "^(VPNFETCH|COPIED|PRESSED)=" <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub.html" "" "" "Роутер|AWG Manager|Файл .conf|2.0|Скопировать" "$TMP/sub.json"
    check "…«Файл .conf», линия 2.0: «Скопировать» кладёт сам текст .conf (GET адреса awg20.conf), под зелёной строкой — текст для ручного копирования" \
        'grep -qxF "VPNFETCH=$SUB_PATH/awg20.conf|GET|no-store|same-origin" <<<"$JSOUT" && grep -qxF "COPIED=[Interface]\\nPrivateKey = FAKE-awg20\\n" <<<"$JSOUT" && grep -qx "Скопировано: текст .conf. Теперь вставьте в AWG Manager: «Новый туннель» → «Вставить текст» → «Импортировать»." <<<"$JSOUT" && grep -qx "PrivateKey = FAKE-awg20" <<<"$JSOUT" && grep -qx "PRESSED=2.0" <<<"$JSOUT"' \
        "$(grep -E "^(VPNFETCH|COPIED|PRESSED)=|Скопировано:" <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_nosub.html"
    check "подписки нет: вкладка есть, вместо ряда — «Подписку для вас ещё не включили» и «Напишите тому, кто прислал эту страницу.»; выбора приложения с «рекомендуем» нет — он вёл в тупик (A3-7)" \
        'grep -qx "Подписку для вас ещё не включили" <<<"$JSOUT" && grep -qx "Напишите тому, кто прислал эту страницу." <<<"$JSOUT" && ! grep -qx "QR-код" <<<"$JSOUT" && ! grep -qx "FlClash" <<<"$JSOUT" && ! grep -qx "рекомендуем" <<<"$JSOUT"' "$(head -c 900 <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_suboff.html"
    check "A3-7: подписка была при создании страницы, а сейчас её нет — «Подписка для вас сейчас выключена», а не «ещё не включили»" \
        'grep -qx "Подписка для вас сейчас выключена" <<<"$JSOUT" && ! grep -qx "Подписку для вас ещё не включили" <<<"$JSOUT" && grep -qx "Напишите тому, кто прислал эту страницу." <<<"$JSOUT"' "$(head -c 900 <<<"$JSOUT")"
    # iPhone и iPad: incy «рекомендуем» — подписка all.txt, обновляется сама, два QR рядом (обычной камерой телефона —
    # ссылка incy://add/…, сканером в incy — голый адрес); после неё — ключ vpn:// по ссылке в AmneziaVPN и DefaultVPN:
    # «Скопировать» кладёт сам ключ (GET адреса .vpn с этой же страницы), QR — QR ключа, как на «Статическом ключе».
    GA_TAB=sub render_js "$TMP/page_sub_ios.html"
    check "iPhone: incy «рекомендуем» первым, затем AmneziaVPN, DefaultVPN, AmneziaWG; «Подписка incy», QR — «камерой или сканером incy»; адрес скрыт (s-••••/all.txt), «Открыть в incy» строкой; адрес не запрошен" \
        'grep -qx "Подписка для incy" <<<"$JSOUT" && [ "$(grep -A2 -x "incy" <<<"$JSOUT" | sed -n 3p)" = "рекомендуем" ] && p=0 && for n in incy AmneziaVPN DefaultVPN AmneziaWG; do c=$(grep -nxF "$n" <<<"$JSOUT" | head -1 | cut -d: -f1); [ -n "$c" ] && [ "$c" -gt "$p" ] || exit 1; p=$c; done && grep -qx "Подписка incy" <<<"$JSOUT" && grep -qx "Все линии в одной подписке; свежий ключ — раз в 12 часов." <<<"$JSOUT" && [ "$(grep -A1 -x "QR-код" <<<"$JSOUT" | sed -n 2p)" = "камерой или сканером incy" ] && grep -qx "https://example.com/s-••••/all.txt" <<<"$JSOUT" && grep -qx "Открыть в incy" <<<"$JSOUT" && grep -qx "SUBFETCHES=0" <<<"$JSOUT" && ! grep -q "/s-AAAA" <<<"$JSOUT"' \
        "$(head -c 1800 <<<"$JSOUT")"
    if [ "$HAVE_QR" = 1 ]; then
        GA_TAB=sub render_js "$TMP/page_sub_ios.html" "" "" "QR-код" "$TMP/sub.json"
        check "«QR-код» у incy: два QR рядом — «Камерой телефона» (qr/sub/incy.png: incy://add/<адрес>) и «Сканером в incy» (qr/sub/txt.png: голый адрес); «Открыть в incy» — ссылка incy://add/<адрес как есть>" \
            'grep -qx "SUBFETCHES=1" <<<"$JSOUT" && grep -qxF "IMG=qr/sub/incy.png?g=$G32" <<<"$JSOUT" && grep -qxF "IMG=qr/sub/txt.png?g=$G32" <<<"$JSOUT" && grep -qx "Камерой телефона: наведите обычную камеру → «Открыть в incy»" <<<"$JSOUT" && grep -qx "Сканером в incy: «Добавить» → значок QR → наведите на этот код" <<<"$JSOUT" && grep -qxF "HREF=incy://add/https://example.com$SUB_PATH/all.txt" <<<"$JSOUT"' \
            "$(grep -E "^(IMG|HREF|SUBFETCHES)=|Камерой|Сканером" <<<"$JSOUT")"
        # Два QR рядом мешают друг другу (qrPair): активен один — по умолчанию «Камерой телефона», второй размыт с
        # подписью поверх; размытый для экранного чтеца — кнопка, его картинка скрыта; оба — в порядке Tab
        check "два QR (qrPair): активен «Камерой телефона» (group, картинка видна чтецу), «Сканером в incy» размыт — кнопка «Показать этот код: …» с картинкой aria-hidden; поверх обоих — «Нажмите, чтобы показать этот код» (видна у размытого)" \
            '[ "$(grep -c "^QR=" <<<"$JSOUT")" = 2 ] && grep -qxF "QR=on|group|0||Камерой телефона: наведите обычную камеру → «Открыть в incy»" <<<"$JSOUT" && grep -qxF "QR=off|button|0|true|Показать этот код: Сканером в incy: «Добавить» → значок QR → наведите на этот код" <<<"$JSOUT" && [ "$(grep -cx "Нажмите, чтобы показать этот код" <<<"$JSOUT")" = 2 ]' \
            "$(grep "^QR=" <<<"$JSOUT")"
        qr_state() { grep "^QR=" <<<"$JSOUT" | cut -d"|" -f1 | tr "\n" " "; }
        GA_TAB=sub render_js "$TMP/page_sub_ios.html" "" "" "QR-код|mouseenter@Сканером в incy" "$TMP/sub.json"; qr_hover=$(qr_state)
        GA_TAB=sub render_js "$TMP/page_sub_ios.html" "" "" "QR-код|mouseenter@Сканером в incy|focus@Камерой телефона" "$TMP/sub.json"; qr_focus=$(qr_state)
        GA_TAB=sub render_js "$TMP/page_sub_ios.html" "" "" "QR-код|click@Сканером в incy" "$TMP/sub.json"; qr_click=$(qr_state)
        GA_TAB=sub render_js "$TMP/page_sub_ios.html" "" "" "QR-код|keydown@Сканером в incy" "$TMP/sub.json"; qr_key=$(qr_state)
        check "…наведение мыши, фокус клавиатуры, щелчок и Enter делают QR активным, другой размывается" \
            '[ "$qr_hover" = "QR=off QR=on " ] && [ "$qr_focus" = "QR=on QR=off " ] && [ "$qr_click" = "QR=off QR=on " ] && [ "$qr_key" = "QR=off QR=on " ]' \
            "наведение=$qr_hover фокус=$qr_focus щелчок=$qr_click Enter=$qr_key"
    fi
    GA_TAB=sub render_js "$TMP/page_sub_ios.html" "" "" "AmneziaVPN"
    check "iPhone, AmneziaVPN: «Ключ для AmneziaVPN по ссылке», «Ключ vpn://» и честная строка владельца; «Линия» 3.1 / 2.0 с «Берите ключ 3.1; не подключился — 2.0.», версия для 3.1; QR — для сканера в приложении, «Скопировать» — ключ; адрес awg31.vpn скрыт; «Открыть в …» нет; адрес не запрошен" \
        'grep -qx "Ключ для AmneziaVPN по ссылке" <<<"$JSOUT" && grep -qx "Ключ vpn://" <<<"$JSOUT" && grep -qx "Разово по ссылке: AmneziaVPN сам по ссылке не обновляется — такой функции в приложении нет. Ссылка постоянная и всегда отдаёт текущий ключ: сменится ключ или порт — откройте её снова и добавьте ключ заново." <<<"$JSOUT" && grep -qx "Линия" <<<"$JSOUT" && grep -qx "Берите ключ 3.1; не подключился — 2.0." <<<"$JSOUT" && grep -qx "AmneziaVPN: нужна версия 5.0.1 или новее" <<<"$JSOUT" && [ "$(grep -A1 -x "QR-код" <<<"$JSOUT" | sed -n 2p)" = "для сканера в приложении" ] && [ "$(grep -A1 -x "Скопировать" <<<"$JSOUT" | sed -n 2p)" = "ключ, чтобы вставить" ] && grep -qx "https://example.com/s-••••/awg31.vpn" <<<"$JSOUT" && ! grep -q "^Открыть в " <<<"$JSOUT" && grep -qx "SUBFETCHES=0" <<<"$JSOUT" && ! grep -q "/s-AAAA" <<<"$JSOUT" && [ "$(grep -A3 -x "AmneziaVPN" <<<"$JSOUT" | sed -n 3p)" = "разово" ]' \
        "$(head -c 1800 <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub_ios.html" "" "" "AmneziaVPN|Скопировать" "$TMP/sub.json"
    check "«Скопировать» (AmneziaVPN, буфер как в Safari — ClipboardItem с обещанием): POST sub.json, затем один GET адреса awg31.vpn с этой же страницы (путь, без кеша, same-origin); в буфер — сам ключ vpn://, «Скопировано: ключ vpn://. Теперь вставьте в AmneziaVPN: …», ключ — текстом под строкой" \
        'grep -qx "SUBFETCHES=1" <<<"$JSOUT" && grep -qxF "VPNFETCH=$SUB_PATH/awg31.vpn|GET|no-store|same-origin" <<<"$JSOUT" && [ "$(grep -c "^VPNFETCH=" <<<"$JSOUT")" = 1 ] && grep -qx "COPIED=vpn://FAKE-awg31" <<<"$JSOUT" && grep -qx "Скопировано" <<<"$JSOUT" && grep -qx "Скопировано: ключ vpn://. Теперь вставьте в AmneziaVPN: «+» → «Вставить» → «Продолжить»." <<<"$JSOUT" && grep -qx "vpn://FAKE-awg31" <<<"$JSOUT"' \
        "$(grep -E "^(VPNFETCH|COPIED|SUBFETCHES)=|^Скопировано" <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=text render_js "$TMP/page_sub_ios.html" "" "" "AmneziaVPN|Скопировать" "$TMP/sub.json"
    check "…без ClipboardItem — обычное копирование по приходу ключа (navigator.clipboard.writeText)" \
        'grep -qx "COPIED=vpn://FAKE-awg31" <<<"$JSOUT" && grep -qx "Скопировано" <<<"$JSOUT"' "$(grep -E "^(VPNFETCH|COPIED)=" <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_sub_ios.html" "" "" "AmneziaVPN|Скопировать" "$TMP/sub.json"
    check "…буфер недоступен вовсе — ключ текстом для ручного копирования, как на «Статическом ключе»" \
        'grep -qx "Не скопировалось — выделите ключ целиком и скопируйте вручную:" <<<"$JSOUT" && grep -qx "vpn://FAKE-awg31" <<<"$JSOUT" && ! grep -q "^COPIED=" <<<"$JSOUT" && ! grep -q "^Скопировано: " <<<"$JSOUT"' "$(head -c 1500 <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item GA_VPN_STATUS=404 render_js "$TMP/page_sub_ios.html" "" "" "AmneziaVPN|Скопировать" "$TMP/sub.json"
    check "…адрес ответил 404 (отозвали, перевыпустили, выключили) — «Ключ не загрузился — обновите страницу и нажмите ещё раз.», в буфере ничего" \
        'grep -qx "Ключ не загрузился — обновите страницу и нажмите ещё раз." <<<"$JSOUT" && ! grep -q "^COPIED=" <<<"$JSOUT" && ! grep -qx "Скопировано" <<<"$JSOUT"' "$(head -c 1500 <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item GA_VPN_STATUS=503 render_js "$TMP/page_sub_ios.html" "" "" "AmneziaVPN|Скопировать" "$TMP/sub.json"
    check "…мост занят (503) — «Сервер занят — нажмите ещё раз через несколько секунд.»" \
        'grep -qx "Сервер занят — нажмите ещё раз через несколько секунд." <<<"$JSOUT" && ! grep -q "^COPIED=" <<<"$JSOUT"' "$(head -c 1500 <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub_ios.html" "" "" "AmneziaVPN|2.0|Скопировать" "$TMP/sub.json"
    check "…линия 2.0: ключ awg20 и версия AmneziaVPN для 2.0" \
        'grep -qxF "VPNFETCH=$SUB_PATH/awg20.vpn|GET|no-store|same-origin" <<<"$JSOUT" && grep -qx "COPIED=vpn://FAKE-awg20" <<<"$JSOUT" && grep -qx "AmneziaVPN: нужна версия 4.8.14 или новее" <<<"$JSOUT" && grep -qx "PRESSED=2.0" <<<"$JSOUT"' \
        "$(grep -E "^(VPNFETCH|COPIED|PRESSED)=|нужна версия" <<<"$JSOUT")"
    if [ "$HAVE_QR" = 1 ]; then
        GA_TAB=sub render_js "$TMP/page_sub_ios.html" "" "" "AmneziaVPN|QR-код|2.0" "$TMP/sub.json"
        check "…«QR-код» у AmneziaVPN — QR самого ключа vpn:// (панель со страницами, как на «Статическом ключе», токен из sub.json); смена линии — сразу QR ключа 2.0" \
            'grep -qx "SUBFETCHES=1" <<<"$JSOUT" && grep -qxF "IMG=qr/key/awg2/vpn-1.png?g=$G32" <<<"$JSOUT" && ! grep -q "^IMG=qr/key/awg3/" <<<"$JSOUT" && ! grep -q "^IMG=qr/sub/" <<<"$JSOUT"' "$(grep -E "^(IMG|SUBFETCHES)=" <<<"$JSOUT")"
    fi
    # MINIAPP-QR-01: QR ключа vpn:// из нескольких частей сменяются сами, по кругу (приложение Amnezia сканирует их по
    # очереди); стрелки остаются и тоже ходят по кругу. Все части загружены сразу, видна одна (SHOWN). У профилей — по
    # три части.
    python3 - "$TMP/page_sub_ios.html" "$TMP/page_sub_qr3.html" <<'EOPY'
import json, re, sys
src, dst = sys.argv[1:3]
html = open(src, encoding="utf-8").read()
m = re.search(r'(<script type="application/json" id="ga-data">)(.*?)(</script>)', html, re.S)
data = json.loads(m.group(2))
for p in data["profiles"]:
    if not p.get("imported"):
        p["qr_vpn"] = 3
blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
open(dst, "w", encoding="utf-8").write(html[:m.start(2)] + blob + html[m.end(2):])
EOPY
    QR3="qr/key/awg2/vpn-%s.png?g=$G32"
    GA_TAB=sub render_js "$TMP/page_sub_qr3.html" "" "" "AmneziaVPN|QR-код|2.0" "$TMP/sub.json"
    check "MINIAPP-QR-01 три части: загружены все три, видна первая, «QR 1 из 3», смена раз в 1,5 с" \
        '[ "$(grep -c "^IMG=qr/key/awg2/vpn-[123]\.png" <<<"$JSOUT")" = 3 ] && [ "$(grep -c "^SHOWN=" <<<"$JSOUT")" = 1 ] && grep -qxF "SHOWN=$(printf "$QR3" 1)" <<<"$JSOUT" && grep -qx "1 / 3" <<<"$JSOUT" && grep -q "^QR 1 из 3 " <<<"$JSOUT"' \
        "$(grep -E "^(IMG|SHOWN|INTERVAL)=|из 3|/ 3" <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_sub_qr3.html" "" "" "AmneziaVPN|QR-код|2.0|@tick" "$TMP/sub.json"
    check "…сама через шаг — вторая часть, «QR 2 из 3», шаг 1500 мс" \
        'grep -qxF "SHOWN=$(printf "$QR3" 2)" <<<"$JSOUT" && [ "$(grep -c "^SHOWN=" <<<"$JSOUT")" = 1 ] && grep -qx "2 / 3" <<<"$JSOUT" && grep -q "^QR 2 из 3 " <<<"$JSOUT" && grep -qx "INTERVAL=1500" <<<"$JSOUT"' \
        "$(grep -E "^(SHOWN|INTERVAL)=|из 3|/ 3" <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_sub_qr3.html" "" "" "AmneziaVPN|QR-код|2.0|@tick|@tick|@tick" "$TMP/sub.json"
    check "…по кругу: после третьей — снова первая" \
        'grep -qxF "SHOWN=$(printf "$QR3" 1)" <<<"$JSOUT" && grep -qx "1 / 3" <<<"$JSOUT"' "$(grep -E "^SHOWN=|/ 3" <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_sub_qr3.html" "" "" "AmneziaVPN|QR-код|2.0|@aria=Предыдущий QR" "$TMP/sub.json"
    check "…стрелка «назад» с первой — третья (по кругу), смена продолжается" \
        'grep -qxF "SHOWN=$(printf "$QR3" 3)" <<<"$JSOUT" && grep -qx "3 / 3" <<<"$JSOUT"' "$(grep -E "^SHOWN=|/ 3|NO-CLICK" <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_sub_qr3.html" "" "" "AmneziaVPN|QR-код|2.0|@aria=Следующий QR|@tick" "$TMP/sub.json"
    check "…«вперёд» — вторая, через шаг — третья" \
        'grep -qxF "SHOWN=$(printf "$QR3" 3)" <<<"$JSOUT"' "$(grep -E "^SHOWN=|/ 3|NO-CLICK" <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_sub_ios.html" "" "" "DefaultVPN"
    check "DefaultVPN: пометка каталога «Официальный форк AmneziaVPN для российского App Store», «Ключ для DefaultVPN по ссылке», версия DefaultVPN для 3.1, честная строка с DefaultVPN" \
        'grep -qx "Официальный форк AmneziaVPN для российского App Store" <<<"$JSOUT" && grep -qx "Ключ для DefaultVPN по ссылке" <<<"$JSOUT" && grep -qx "DefaultVPN: нужна версия 2.0.1 или новее" <<<"$JSOUT" && grep -q "^Разово по ссылке: DefaultVPN сам по ссылке не обновляется" <<<"$JSOUT"' \
        "$(head -c 1500 <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub.html" "" "" "WG Tunnel|Скопировать" "$TMP/sub.json"
    check "Android, WG Tunnel: «Файл .conf», «Скопировать» — ссылка на файл awg31.conf и путь «+» → «Добавить из URL»; «Открыть в WG Tunnel» — ссылка wg://<хост>/<путь>" \
        'grep -qx "Ключ для WG Tunnel по ссылке" <<<"$JSOUT" && grep -qx "Файл .conf" <<<"$JSOUT" && grep -qxF "COPIED=https://example.com$SUB_PATH/awg31.conf" <<<"$JSOUT" && grep -qx "Скопировано: ссылка на файл .conf. Теперь вставьте в WG Tunnel: «+» → «Добавить из URL»." <<<"$JSOUT" && grep -qxF "HREF=wg://example.com$SUB_PATH/awg31.conf" <<<"$JSOUT"' \
        "$(grep -E "^(COPIED|HREF)=|Скопировано:" <<<"$JSOUT")"
    if [ "$HAVE_QR" = 1 ]; then
        GA_TAB=sub render_js "$TMP/page_sub.html" "" "" "AmneziaWG|QR-код" "$TMP/sub.json"
        check "Android, AmneziaWG: «QR-код» — QR самого ключа .conf (qr/key/<профиль 3.1>/conf.png)" \
            'grep -qxF "IMG=qr/key/awg3/conf.png?g=$G32" <<<"$JSOUT" && [ "$(grep -A1 -x "Скопировать" <<<"$JSOUT" | sed -n 2p)" = "ссылку на файл, чтобы вставить" ]' "$(grep -E "^IMG=" <<<"$JSOUT")"
    fi
    GA_TAB=sub render_js "$TMP/page_sub.html" "" "" "Android TV"
    check "Android TV: incy «рекомендуем» и WG Tunnel; у incy QR есть и на телевизоре (камерой или сканером incy), «Открыть в …» на телевизоре нет; пометка каталога об «Отправить на ТВ»" \
        'grep -qx "Подписка для incy" <<<"$JSOUT" && [ "$(grep -A2 -x "incy" <<<"$JSOUT" | sed -n 3p)" = "рекомендуем" ] && grep -qx "WG Tunnel" <<<"$JSOUT" && ! grep -qx "AmneziaVPN" <<<"$JSOUT" && [ "$(grep -A1 -x "QR-код" <<<"$JSOUT" | sed -n 2p)" = "камерой или сканером incy" ] && ! grep -qx "Приложение уже стоит на этом устройстве?" <<<"$JSOUT" && grep -qx "На телевизоре — «Отправить на ТВ» из incy на телефоне" <<<"$JSOUT"' \
        "$(head -c 1500 <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_sub.html" "" "" "Android TV|WG Tunnel"
    check "…WG Tunnel на Android TV: QR неактивен — «на телевизоре сканировать нечем»" \
        '[ "$(grep -A1 -x "QR-код" <<<"$JSOUT" | sed -n 2p)" = "на телевизоре сканировать нечем" ] && grep -qx "DISABLED=QR-код" <<<"$JSOUT"' "$(grep -A1 -x "QR-код" <<<"$JSOUT")"
    GA_TAB=sub render_js "$TMP/page_sub.html" "" "" "Apple TV"
    check "Apple TV: incy «рекомендуем» — единственный, с QR и пометкой «не подтверждена»" \
        'grep -qx "Подписка для incy" <<<"$JSOUT" && [ "$(grep -A2 -x "incy" <<<"$JSOUT" | sed -n 3p)" = "рекомендуем" ] && ! grep -qx "WG Tunnel" <<<"$JSOUT" && [ "$(grep -A1 -x "QR-код" <<<"$JSOUT" | sed -n 2p)" = "камерой или сканером incy" ] && grep -qF "Apple TV источником не подтверждена" <<<"$JSOUT"' \
        "$(head -c 1500 <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub_ios.html" en "" "AmneziaVPN|Copy" "$TMP/sub.json"
    check "…по-английски (iPhone, AmneziaVPN): «Key for AmneziaVPN by link», «vpn:// key», «One-time by link: …», «Copied: vpn:// key. Now paste it in AmneziaVPN: …»; без русских слов" \
        'grep -qx "Key for AmneziaVPN by link" <<<"$JSOUT" && grep -qx "vpn:// key" <<<"$JSOUT" && grep -q "^One-time by link: AmneziaVPN does not refresh" <<<"$JSOUT" && grep -qx "Copied: vpn:// key. Now paste it in AmneziaVPN: “+” → “Insert” → “Continue”." <<<"$JSOUT" && grep -qx "Copied" <<<"$JSOUT" && [ -z "$(cyr_lines <<<"$JSOUT")" ]' \
        "$(cyr_lines <<<"$JSOUT" | head -5) $(head -c 600 <<<"$JSOUT")"
    GA_TAB=sub GA_CLIP=item render_js "$TMP/page_sub.html" en "" "Copy" "$TMP/sub.json"
    check "…по-английски: «Static key» | «Subscription», «Subscription for FlClash», «Clash (mihomo) profile», «QR code», «Is the app already on this device?», «Open in FlClash», «once a day»; без русских слов" \
        'grep -qx "Static key" <<<"$JSOUT" && grep -qx "Subscription" <<<"$JSOUT" && grep -qx "Subscription for FlClash" <<<"$JSOUT" && grep -qx "Clash (mihomo) profile" <<<"$JSOUT" && grep -qx "QR code" <<<"$JSOUT" && grep -qx "Is the app already on this device?" <<<"$JSOUT" && grep -qx "Open in FlClash" <<<"$JSOUT" && grep -qx "All lines in one subscription, the app picks by itself; a fresh key — once a day." <<<"$JSOUT" && grep -qx "Copied: subscription link. Now paste it in FlClash: Profiles → + → URL, then Submit." <<<"$JSOUT" && [ -z "$(cyr_lines <<<"$JSOUT")" ]' \
        "$(cyr_lines <<<"$JSOUT" | head -5) $(head -c 900 <<<"$JSOUT")"
    render_js "$TMP/page_atv.html" "" "$TMP/keys.json" "Показать ключ"
    check "app.js (node), телевизор: «Подключите телевизор», пункты «AmneziaVPN из Google Play», «Открыть ключ в AmneziaVPN» (по профилю), «Скачать файл .conf», «Показать QR ключа», «Другие приложения»; справа — пульт" \
        'grep -qx "Подключите телевизор" <<<"$JSOUT" && grep -qx "AmneziaVPN из Google Play" <<<"$JSOUT" && [ "$(grep -cx "Открыть ключ в AmneziaVPN" <<<"$JSOUT")" = 2 ] && [ "$(grep -cx "Скачать файл .conf" <<<"$JSOUT")" = 2 ] && { [ "$HAVE_QR" != 1 ] || grep -qx "Показать QR ключа" <<<"$JSOUT"; } && grep -qx "Другие приложения" <<<"$JSOUT" && grep -qx "Пульт" <<<"$JSOUT" && grep -qx "▲ ▼" <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
else
    skipped "отрисовка app.js: ключи из keys.json, выбор приложения, Apple TV и телевизор" "нет node"
fi
check "app.js: крупный список под пульт — только телевизору (data.device === android_tv); кнопки ключа — .pg-kbtns, ссылки — .pg-link" \
    'grep -q "tvMode = data.device === .android_tv." "$TMP/app.js" && grep -q "{cls: .pg-kbtns.}" "$TMP/app.js" && grep -q "{cls: .pg-link.}" "$TMP/app.js"'
get "/p-$P_SECRET/" -A "Mozilla/5.0 (Linux; Android 11; SHIELD Android TV Build/RQ1A) AppleWebKit/537.36"
check "устройство по User-Agent: телевизор (SHIELD Android TV) → device=android_tv" 'grep -q "\"device\": *\"android_tv\"" "$BODY"' "$(grep -o '"device": *"[a-z_]*"' "$BODY")"
# SEC50-10: файл и QR ключа — только с токеном из ответа POST keys.json (его выдают вместе с ключами, после действия
# человека); по одному адресу страницы и предсказуемому имени профиля ключ не забрать
reveal "$S_SECRET"; S_GRANT=$REVEAL_GRANT
reveal "$P_SECRET"; P_GRANT=$REVEAL_GRANT
check "SEC50-10: POST keys.json отдаёт вместе с ключами токен на файл и QR (32 символа base64url), у каждой страницы свой" \
    '[[ "$P_GRANT" =~ ^[A-Za-z0-9_-]{32}$ ]] && [[ "$S_GRANT" =~ ^[A-Za-z0-9_-]{32}$ ]] && [ "$P_GRANT" != "$S_GRANT" ]' "${#P_GRANT} ${#S_GRANT}"
get "/p-$P_SECRET/key/awg2.conf"; c1=$code; cp "$BODY" "$TMP/nogrant.bin"
get "/p-$P_SECRET/key/awg2.conf?g=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"; c2=$code
get "/p-$P_SECRET/key/awg2.conf?g=$S_GRANT"; c3=$code
curl -s -o /dev/null -I --max-time 20 "http://127.0.0.1:$PORT/p-$P_SECRET/key/awg2.conf" -w '%{http_code}' >"$TMP/head.code"
check "SEC50-10: key/awg2.conf без токена, с чужим и с токеном другой страницы (и HEAD без него) → 404 nginx-вида" \
    '[ "$c1$c2$c3" = 404404404 ] && [ "$(cat "$TMP/head.code")" = 404 ] && grep -q "<center>nginx</center>" "$TMP/nogrant.bin"' "$c1 $c2 $c3 HEAD $(cat "$TMP/head.code")"
if [ "$HAVE_QR" = 1 ]; then
    get "/p-$P_SECRET/qr/key/awg2/conf.png"; c1=$code
    get "/p-$P_SECRET/qr/key/awg2/vpn.png"; c2=$code
    check "SEC50-10: QR ключа (conf.png, vpn.png) без токена → 404" '[ "$c1$c2" = 404404 ]' "$c1 $c2"
fi
post "/p-$P_SECRET/keys.json" -H 'Content-Type: application/json' --data "{\"grant\":\"$P_GRANT\"}"
P_GRANT2=$(jq -r '.grant // ""' "$BODY" 2>/dev/null); c1=$code; has_keys=$(jq -r 'has("keys")' "$BODY" 2>/dev/null)
post "/p-$P_SECRET/keys.json" -H 'Content-Type: application/json' --data "{\"grant\":\"$S_GRANT\"}"; c2=$code
check "SEC50-10: открытая страница продлевает токен (POST keys.json с ним → новый, без ключей); токен другой страницы → 409" \
    '[ "$c1$c2" = 200409 ] && [[ "$P_GRANT2" =~ ^[A-Za-z0-9_-]{32}$ ]] && [ "$P_GRANT2" != "$P_GRANT" ] && [ "$has_keys" = false ]' "$c1 $c2 keys=$has_keys"
get "/p-$P_SECRET/key/awg2.conf?g=$P_GRANT2"
check "key/awg2.conf с токеном → 200, attachment anna-2_0.conf, байт в байт как export_conf" \
    '[ "$code" = 200 ] && hdr content-disposition | grep -q "attachment; filename=\"anna-2_0.conf\"" && [ "$(cat "$BODY")" = "$CONF_TEXT" ]' "code=$code $(head -c 200 "$BODY")"
check "Endpoint в ключе — домен сервера (Pro): example.com:40000" 'grep -q "^Endpoint = example.com:40000$" "$BODY"' "$(grep Endpoint "$BODY")"
get "/p-$P_SECRET/key/awg9.conf?g=$P_GRANT"
check "ключ чужого профиля → 404" '[ "$code" = 404 ]' "code=$code"
get "/p-$S_SECRET/key/awg3.conf?g=$S_GRANT"
check "профиль не из списка страницы (awg3 на странице только с awg2) → 404" '[ "$code" = 404 ]' "code=$code"
if [ "$HAVE_QR" = 1 ]; then
    get "/p-$P_SECRET/qr/key/awg2/conf.png?g=$P_GRANT"
    check "QR .conf с токеном → PNG (сигнатура)" '[ "$code" = 200 ] && [ "$(head -c 4 "$BODY" | od -An -tx1 | tr -d " ")" = "89504e47" ] && hdr content-type | grep -q "image/png"' "code=$code"
    get "/p-$P_SECRET/qr/key/awg2/vpn.png?g=$P_GRANT"
    check "QR vpn:// (первый чанк) с токеном → PNG" '[ "$code" = 200 ] && [ "$(head -c 4 "$BODY" | od -An -tx1 | tr -d " ")" = "89504e47" ]' "code=$code"
    get "/p-$P_SECRET/qr/key/awg2/vpn-99.png?g=$P_GRANT"
    check "несуществующий чанк → 404" '[ "$code" = 404 ]' "code=$code"
    get "/p-$P_SECRET/qr/link/1.png"
    check "QR ссылки каталога №1 → PNG" '[ "$code" = 200 ] && [ "$(head -c 4 "$BODY" | od -An -tx1 | tr -d " ")" = "89504e47" ]' "code=$code"
    get "/p-$P_SECRET/qr/link/9999.png"
    check "QR несуществующего номера → 404 (произвольный текст в QR не принимается)" '[ "$code" = 404 ]' "code=$code"
else
    skipped "QR ключей и ссылок" "нет qrencode"
fi

# Зеркало: состояние, заявка, готовая копия через X-Accel-Redirect
get "/p-$P_SECRET/mirror/amneziavpn/android/state"
check "state зеркала → JSON: available=true, state=none" \
    '[ "$code" = 200 ] && [ "$(jq -r .available "$BODY")" = true ] && [ "$(jq -r .state "$BODY")" = none ]' "code=$code $(cat "$BODY")"
get "/p-$P_SECRET/mirror/amneziavpn/android"
check "скачать копию, которой нет → 404" '[ "$code" = 404 ]' "code=$code"
post "/p-$P_SECRET/mirror/amneziavpn/android/request"
check "POST request → 200, state=queued, файл заявки записан, в аудите mirror-request с id страницы" \
    '[ "$code" = 200 ] && [ "$(jq -r .state "$BODY")" = queued ] && [ -f "$GOAMNEZIA_MIRROR_DIR/amneziavpn/android/request" ] && [ "$(jq -r .page "$GOAMNEZIA_MIRROR_DIR/amneziavpn/android/request")" = "$P_ID" ] && audit_has mirror-request "id=$P_ID app=amneziavpn platform=android"' "code=$code $(cat "$BODY")"
REQ1=$(cat "$GOAMNEZIA_MIRROR_DIR/amneziavpn/android/request")
post "/p-$P_SECRET/mirror/amneziavpn/android/request"
check "MIRROR-01: повторная заявка, пока копия в очереди, — 200 «уже в работе» (already: in_progress), state=queued; заявка та же (страница и время), в аудите одна строка mirror-request" \
    '[ "$code" = 200 ] && [ "$(jq -r .already "$BODY")" = in_progress ] && [ "$(jq -r .state "$BODY")" = queued ] && [ "$(cat "$GOAMNEZIA_MIRROR_DIR/amneziavpn/android/request")" = "$REQ1" ] && [ "$(grep -c "\"mirror-request\"" "$GOAMNEZIA_AUDIT_LOG")" = 1 ]' \
    "code=$code $(cat "$BODY") аудит=$(grep -c "mirror-request" "$GOAMNEZIA_AUDIT_LOG")"
post "/p-$P_SECRET/mirror/incy/android/request"
check "заявка на закрытое приложение → 404 с error=not_mirrorable" '[ "$code" = 404 ] && [ "$(jq -r .error "$BODY")" = not_mirrorable ]' "code=$code $(cat "$BODY")"
post "/p-$P_SECRET/mirror/amneziavpn/ios/request"
check "заявка на iOS → 404 (сборки для iPhone только в App Store)" '[ "$code" = 404 ]' "code=$code"
post "/p-$S_SECRET/mirror/amneziavpn/android/request"
check "страница с mirror=off: заявка → 404 (маршрута для неё нет, а не спрятанная кнопка)" '[ "$code" = 404 ]' "code=$code $(cat "$BODY")"
mkdir -p "$GOAMNEZIA_MIRROR_DIR/wgtunnel/android"
printf 'apk-bytes' >"$GOAMNEZIA_MIRROR_DIR/wgtunnel/android/wgtunnel-1.0.apk"
jq -nc '{app:"wgtunnel", platform:"android", version:"v1.0", file:"wgtunnel-1.0.apk", size:9, sha256:"x", fetched:"2026-09-17T00:00:00Z"}' >"$GOAMNEZIA_MIRROR_DIR/wgtunnel/android/meta.json"
jq -nc '{state:"ready", version:"v1.0", file:"wgtunnel-1.0.apk", bytes:9, total:9}' >"$GOAMNEZIA_MIRROR_DIR/wgtunnel/android/state.json"
get "/p-$P_SECRET/mirror/wgtunnel/android"
check "готовая копия: 200 с X-Accel-Redirect на /_ga_mirror/wgtunnel/android/<файл> и attachment, тело пустое" \
    '[ "$code" = 200 ] && hdr x-accel-redirect | grep -q "/_ga_mirror/wgtunnel/android/wgtunnel-1.0.apk" && hdr content-disposition | grep -q "attachment; filename=\"wgtunnel-1.0.apk\"" && [ ! -s "$BODY" ]' "code=$code $(cat "$HDR")"
get "/p-$P_SECRET/mirror/wgtunnel/android/state"
check "state готовой копии: ready, версия v1.0, размер 9" '[ "$(jq -r .state "$BODY")" = ready ] && [ "$(jq -r .version "$BODY")" = v1.0 ] && [ "$(jq -r .size "$BODY")" = 9 ]' "$(cat "$BODY")"
if [ "$HAVE_QR" = 1 ]; then
    get "/p-$P_SECRET/qr/mirror/wgtunnel/android.png"
    check "QR адреса копии на нашем сервере → PNG" '[ "$code" = 200 ] && [ "$(head -c 4 "$BODY" | od -An -tx1 | tr -d " ")" = "89504e47" ]' "code=$code"
    get "/p-$S_SECRET/qr/mirror/wgtunnel/android.png"
    check "страница с mirror=off: QR копии → 404" '[ "$code" = 404 ]' "code=$code"
fi
get "/p-$S_SECRET/mirror/wgtunnel/android"
check "страница с mirror=off: скачивание готовой копии → 404 (настройку не обойти прямым адресом)" '[ "$code" = 404 ]' "code=$code"
# Опубликованная копия остаётся ready, что бы ни лежало в state.json: провал
# суточной сверки новой версии — поле error, а не состояние.
jq -nc '{state:"failed", updated:"2026-09-17T10:00:00Z", error:"mirror_fetch_failed", message:"stend"}' >"$GOAMNEZIA_MIRROR_DIR/wgtunnel/android/state.json"
get "/p-$P_SECRET/mirror/wgtunnel/android/state"
check "state.json failed при meta.json и файле на диске → state=ready с error (провал сверки не прячет копию)" \
    '[ "$(jq -r .state "$BODY")" = ready ] && [ "$(jq -r .error "$BODY")" = mirror_fetch_failed ] && [ "$(jq -r .version "$BODY")" = v1.0 ]' "$(cat "$BODY")"
get "/p-$P_SECRET/mirror/wgtunnel/android"
check "и скачивание такой копии работает (X-Accel-Redirect)" '[ "$code" = 200 ] && hdr x-accel-redirect | grep -q "wgtunnel-1.0.apk"' "code=$code"
jq -nc '{state:"ready", version:"v1.0", file:"wgtunnel-1.0.apk", bytes:9, total:9}' >"$GOAMNEZIA_MIRROR_DIR/wgtunnel/android/state.json"
# Брошенная загрузка: downloading без живого замка (воркер убит, перезагрузка)
# — failed/stale, а не вечное «загружается 0 %»; заявка принимается снова.
mkdir -p "$GOAMNEZIA_MIRROR_DIR/amneziawg/android"
jq -nc '{state:"downloading", updated:"2026-09-17T10:00:00Z", version:"v3", file:"awg.apk", bytes:0, total:4096}' >"$GOAMNEZIA_MIRROR_DIR/amneziawg/android/state.json"
get "/p-$P_SECRET/mirror/amneziawg/android/state"
check "downloading без замка → state=failed, error=stale" '[ "$(jq -r .state "$BODY")" = failed ] && [ "$(jq -r .error "$BODY")" = stale ]' "$(cat "$BODY")"
post "/p-$P_SECRET/mirror/amneziawg/android/request"
check "заявка на брошенную пару принимается: 200, state=queued, файл request записан" \
    '[ "$code" = 200 ] && [ "$(jq -r .state "$BODY")" = queued ] && [ -f "$GOAMNEZIA_MIRROR_DIR/amneziawg/android/request" ]' "code=$code $(cat "$BODY")"
mkdir -p "$GOAMNEZIA_MIRROR_DIR/amneziawg/android/.lock"; printf '%s\n' "$$" >"$GOAMNEZIA_MIRROR_DIR/amneziawg/android/.lock/pid"
rm -f "$GOAMNEZIA_MIRROR_DIR/amneziawg/android/request"
jq -nc '{state:"downloading", updated:"2026-09-17T10:00:00Z", version:"v3", file:"awg.apk", bytes:0, total:4096}' >"$GOAMNEZIA_MIRROR_DIR/amneziawg/android/state.json"
head -c 1024 /dev/zero >"$GOAMNEZIA_MIRROR_DIR/amneziawg/android/.awg.apk.part"
get "/p-$P_SECRET/mirror/amneziawg/android/state"
check "downloading с живым замком (pid жив) — идёт: state=downloading, bytes по .part = 1024, total 4096" \
    '[ "$(jq -r .state "$BODY")" = downloading ] && [ "$(jq -r .bytes "$BODY")" = 1024 ] && [ "$(jq -r .total "$BODY")" = 4096 ]' "$(cat "$BODY")"
post "/p-$P_SECRET/mirror/amneziawg/android/request"
check "MIRROR-01: заявка, пока копия загружается, — 200 «уже в работе», state=downloading; новой заявки (файла request) нет" \
    '[ "$code" = 200 ] && [ "$(jq -r .already "$BODY")" = in_progress ] && [ "$(jq -r .state "$BODY")" = downloading ] && [ ! -f "$GOAMNEZIA_MIRROR_DIR/amneziawg/android/request" ]' \
    "code=$code $(cat "$BODY")"
rm -rf "$GOAMNEZIA_MIRROR_DIR/amneziawg/android"
get "/p-$P_SECRET/mirror/wgtunnel/android/../../../etc/passwd"
check "путь с ../ под верным секретом → 404" '[ "$code" = 404 ]' "code=$code"

# 404 nginx-вида
get "/"
check "корень → 404 с телом nginx по умолчанию" '[ "$code" = 404 ] && grep -q "<center>nginx</center>" "$BODY" && ! grep -qi "goamnezia\|page" "$BODY"' "code=$code $(cat "$BODY")"
# У 404 сайта нет ни CSP, ни X-Robots-Tag, ни no-store: по таким заголовкам
# сканер отличил бы /p-<43>/ от любого другого пути.
check "404 службы без защитных заголовков: только Server, Date, Content-Type, Content-Length, Connection" \
    '! grep -qi "^content-security-policy:" "$HDR" && ! grep -qi "^x-robots-tag:" "$HDR" && ! grep -qi "^cache-control:" "$HDR" && ! grep -qi "^pragma:" "$HDR" && ! grep -qi "^referrer-policy:" "$HDR" && ! grep -qi "^x-frame-options:" "$HDR" && ! grep -qi "^x-content-type-options:" "$HDR" && [ "$(grep -ciE "^(server|date|content-type|content-length|connection):" "$HDR")" = "$(grep -c ":" "$HDR")" ]' "$(cat "$HDR")"
check "заголовки 404: Server nginx, Content-Type text/html" 'hdr server | grep -qi "^server: nginx" && hdr content-type | grep -qi "text/html"' "$(cat "$HDR")"
# SEC-G4: снаружи 404 службы подменяет своим сам nginx (@ga_pages_down — проверено по vhost выше). Здесь —
# что и без nginx служба отвечает ровно как 404 nginx по умолчанию (server_tokens off): строка статуса,
# набор и порядок заголовков, тело байт в байт; и одинаково на любой промах.
NGINX404_REF="$TMP/nginx404.ref"
printf '<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n<center><h1>404 Not Found</h1></center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n' >"$NGINX404_REF"
# shape404 → ответ без строки Date (она меняется каждую секунду) и контрольная сумма тела
shape404() { tr -d '\r' <"$HDR" | grep -v '^Date: '; cksum <"$BODY"; }
check "SEC-G4: 404 — «HTTP/1.1 404 Not Found», заголовки Server, Date, Content-Type, Content-Length, Connection ровно в этом порядке, тело — 404 nginx байт в байт (146 байт)" \
    '[ "$(head -1 "$HDR" | tr -d "\r")" = "HTTP/1.1 404 Not Found" ] && [ "$(tr -d "\r" <"$HDR" | sed -n "2,\$p" | sed "/^$/d" | cut -d: -f1 | paste -sd, -)" = "Server,Date,Content-Type,Content-Length,Connection" ] && cmp -s "$BODY" "$NGINX404_REF" && hdr content-length | grep -qi "^content-length: 146$" && hdr date | grep -qE "^Date: [A-Z][a-z]{2}, [0-9]{2} [A-Z][a-z]{2} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT$"' \
    "$(cat "$HDR"; od -c "$BODY" | head -3)"
shape404 >"$TMP/404.root"
# Промахи — с отдельного адреса (X-Real-IP с петли), чтобы не приближать бан настоящего адреса теста
get "/some/unknown/path.html" -H "X-Real-IP: 203.0.113.90"; shape404 >"$TMP/404.unknown"
get "/p-$(printf 'A%.0s' $(seq 1 43))/"
check "чужой секрет → 404 nginx-вида" '[ "$code" = 404 ] && grep -q "404 Not Found" "$BODY"' "code=$code"
shape404 >"$TMP/404.secret"
get "/p-$P_SECRET/nonexistent"
check "неизвестный подпуть под верным секретом → 404" '[ "$code" = 404 ]' "code=$code"
shape404 >"$TMP/404.subpath"
check "SEC-G4: неизвестный путь, чужой секрет и неизвестный подпуть под верным секретом — один и тот же 404 (кроме Date)" \
    'cmp -s "$TMP/404.root" "$TMP/404.unknown" && cmp -s "$TMP/404.root" "$TMP/404.secret" && cmp -s "$TMP/404.root" "$TMP/404.subpath"' \
    "$(diff "$TMP/404.root" "$TMP/404.secret"; diff "$TMP/404.root" "$TMP/404.subpath")"
get "/p-$P_SECRET"
check "адрес без косой черты → 301 на адрес с косой чертой (относительные ссылки страницы)" \
    '[ "$code" = 301 ] && hdr location | grep -q "/p-$P_SECRET/$"' "code=$code $(hdr location)"
# Бан: промахи с «чужого» адреса через X-Real-IP (с петли служба ему верит,
# как заголовку от nginx) — сосед с настоящего адреса теста не задет.
for _i in $(seq 1 10); do get "/p-$(printf 'B%.0s' $(seq 1 43))/" -H "X-Real-IP: 203.0.113.77"; done
check "каждая попытка мимо — 404 (не 401 и не 429)" '[ "$code" = 404 ]' "code=$code"
get "/p-$P_SECRET/" -H "X-Real-IP: 203.0.113.77"
check "после 10 промахов адрес 203.0.113.77 забанен: верный секрет тоже 404 — тот же, что на любой промах" \
    '[ "$code" = 404 ] && shape404 | cmp -s - "$TMP/404.root"' "code=$code $(shape404 | diff "$TMP/404.root" - )"
get "/p-$P_SECRET/"
check "сосед (настоящий адрес теста) не задет — страница отвечает 200" '[ "$code" = 200 ]' "code=$code"
check "в логе службы нет ни секрета страницы, ни чужого пути целиком: отказ пишется как «путь N символов, начало /p-»" \
    '! grep -qF -- "$P_SECRET" "$TMP/server.log" && ! grep -q "BBBBBBBB" "$TMP/server.log" && grep -q "путь 47 символов, начало /p-" "$TMP/server.log"' "$(grep "отказ" "$TMP/server.log" | head -2)"

# Визиты и аудит page-visit. SEC-G2, SEC50-05: визит — это POST keys.json с одноразовым значением страницы
# (app.js шлёт его только после действия человека), а не HTML: страницу забирают и роботы превью, и проверялки
# почты, и сканеры ссылок с JavaScript.
visits() { jq -r '.visits // 0' "$PD/state.json" 2>/dev/null || echo 0; }
v0=$(visits)
get "/p-$P_SECRET/"
get "/p-$P_SECRET/" -A "TelegramBot (like TwitterBot)"
curl -s -o /dev/null -I --max-time 20 "http://127.0.0.1:$PORT/p-$P_SECRET/keys.json"
get "/p-$P_SECRET/keys.json"
post "/p-$P_SECRET/keys.json" -H 'Content-Type: application/json' --data '{"nonce":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}'
v1=$(visits)
check "SEC-G2, SEC50-05: HTML страницы, робот превью, GET и HEAD keys.json и чужое значение визитом не считаются" '[ "$v1" = "$v0" ]' "было $v0, стало $v1"
reveal "$P_SECRET"; c1=$code
post "/p-$P_SECRET/keys.json" -H 'Content-Type: application/json' --data "{\"nonce\":\"$REVEAL_NONCE\"}"; c2=$code
reveal "$P_SECRET"; c3=$code
v2=$(visits)
check "SEC50-05: визит — POST keys.json с новым значением страницы (+2 за два открытия), повтор значения — 409 и не визит" \
    '[ "$c1$c2$c3" = 200409200 ] && [ "$v2" = "$((v1 + 2))" ]' "$c1 $c2 $c3; было $v1, стало $v2"
run pages_list
check "визиты сосчитаны в state.json и видны в pages_list (≥ 3 открытия), last_visit заполнен" \
    '[ "$(jq -r --arg id "$P_ID" ".pages[] | select(.id == \$id) | .visits" <<<"$out")" -ge 3 ] && [ -n "$(jq -r --arg id "$P_ID" ".pages[] | select(.id == \$id) | .last_visit" <<<"$out")" ]' "$out"
check "аудит page-visit — раз в час на страницу: ровно одна запись при нескольких открытиях" \
    '[ "$(jq -R -n --arg id "$P_ID" "[inputs | fromjson? // empty | select(.action == \"page-visit\" and ((.details // \"\") | contains(\$id)))] | length" "$GOAMNEZIA_AUDIT_LOG")" = 1 ]' "$(grep page-visit "$GOAMNEZIA_AUDIT_LOG")"
check "адреса посетителя нет ни в state.json (только visits и last_visit), ни в аудите page-visit (договор §7)" \
    '[ "$(jq -r "keys | join(\",\")" "$PD/state.json")" = audited,last_visit,visits ] && ! grep "page-visit" "$GOAMNEZIA_AUDIT_LOG" | grep -q "ip="' "$(cat "$PD/state.json") $(grep page-visit "$GOAMNEZIA_AUDIT_LOG")"
# Подпись пользователя с HTML внутри не должна рвать JSON в <script>: «<», «>»
# и «&» уходят как <, >, &.
users_rename anna 'Анна <!--<script>&</script>' >/dev/null 2>>"$ALL_ERR"
get "/p-$P_SECRET/"
check "подпись «Анна <!--<script>&</script>» не ломает страницу: 200, ровно два </script> в HTML, JSON разбирается и несёт подпись целиком" \
    '[ "$code" = 200 ] && [ "$(grep -o "</script>" "$BODY" | wc -l | tr -d " ")" = 2 ] && python3 -c "
import re,sys,json
html=open(sys.argv[1],encoding=\"utf-8\").read()
m=re.search(r\"<script type=.application/json. id=.ga-data.>(.*?)</script>\", html, re.S)
assert \"<\" not in m.group(1) and \">\" not in m.group(1) and \"&\" not in m.group(1)
d=json.loads(m.group(1))
assert d[\"label\"] == \"Анна <!--<script>&</script>\", d[\"label\"]
" "$BODY"' "$(grep -o "ga-data.\{0,80\}" "$BODY" | head -1)"
users_rename anna 'Анна' >/dev/null 2>>"$ALL_ERR"

# Выключенный пользователь: вместо голого 404 — та же страница со словами «доступ приостановлен» и без ключей
# (находка критика продукта 25.09.2026: 404 читалось как «сломан и VPN, и страница»); ключи — по-прежнему 404.
users_disable anna >/dev/null 2>>"$ALL_ERR"
get "/p-$P_SECRET/"
check "пользователь выключен → страница 200 с блоком «приостановлен», без ключей и QR (кеш сброшен по mtime users.json)" \
    '[ "$code" = 200 ] && grep -q "suspended" "$BODY" && ! grep -q "PrivateKey" "$BODY" && ! grep -q "vpn://" "$BODY"' "code=$code"
get "/p-$P_SECRET/key/awg2.conf?g=$P_GRANT"
check "и ключ выключенного не отдаётся и с токеном → 404" '[ "$code" = 404 ]' "code=$code"
reveal "$P_SECRET"
check "SEC-G2: keys.json выключенного — {suspended: …} без единого ключа (страница перерисуется в «приостановлен»)" \
    '[ "$code" = 200 ] && [ "$(jq -r ".suspended.reason" "$BODY")" = admin ] && [ "$(jq -r "has(\"keys\")" "$BODY")" = false ] && ! grep -q "PrivateKey" "$BODY"' "code=$code $(cat "$BODY")"
users_enable anna >/dev/null 2>>"$ALL_ERR"
get "/p-$P_SECRET/"
check "пользователь снова включён → страница снова 200" '[ "$code" = 200 ]' "code=$code"

# Отзыв: адрес мёртв сразу
run pages_revoke "$S_ID"
check "pages_revoke: rc=0, JSON {id}, каталог снят, аудит page-revoke" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .id)" = "$S_ID" ] && [ ! -d "$PAGES_DIR/$S_ID" ] && audit_has page-revoke "id=$S_ID"' "$out"
get "/p-$S_SECRET/"
check "отозванная страница → 404 без перезапуска службы — тот же, что на любой промах (SEC-G4)" \
    '[ "$code" = 404 ] && shape404 | cmp -s - "$TMP/404.root"' "code=$code $(shape404 | diff "$TMP/404.root" -)"
check "остальные страницы живы: маршрут и служба на месте" \
    '[ "$(ga_config_get .site.pages_location)" = true ] && [ -f "$UNIT" ]'
run pages_revoke "$S_ID"
check "повторный отзыв → page_not_found (rc 3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = page_not_found ]' "rc=$rc"

# Истечение: срок в прошлом → 404 сразу, watch-tick снимает каталог
run pages_create anna 1h
E_ID=$(jget .id); E_SECRET=$(sed -E 's#^https://example\.com/p-([A-Za-z0-9_-]{43})/$#\1#' <<<"$(jget .url)")
jq '.expires_epoch = 1 | .expires = "1970-01-01T00:00:01Z"' "$PAGES_DIR/$E_ID/meta.json" >"$TMP/meta.tmp" && mv "$TMP/meta.tmp" "$PAGES_DIR/$E_ID/meta.json" && chmod 600 "$PAGES_DIR/$E_ID/meta.json"
get "/p-$E_SECRET/"
check "истёкшая страница → 404 ещё до уборки" '[ "$code" = 404 ]' "code=$code"
# Чтение (page-list, page-url — readonly, без замка) истёкшую не показывает, но
# и не снимает: rm -rf с аудитом — только под замком, в pages_tick.
run pages_list
check "pages_list: истёкшая не показывается, но каталог на месте (readonly не убирает)" \
    '[ "$rc" = 0 ] && [ -z "$(jq -r --arg id "$E_ID" ".pages[] | select(.id == \$id) | .id" <<<"$out")" ] && [ -d "$PAGES_DIR/$E_ID" ]' "$out"
run pages_url "$E_ID"
check "pages_url истёкшей → page_not_found, каталог на месте, page-expire в аудите ещё нет" \
    '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = page_not_found ] && [ -d "$PAGES_DIR/$E_ID" ] && ! audit_has page-expire "id=$E_ID"' "rc=$rc code=$GA_ERR_CODE"
run pages_tick
check "pages_tick: {expired: 1, active: 2}, каталог снят, аудит page-expire" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .expired)" = 1 ] && [ "$(jget .active)" = 2 ] && [ ! -d "$PAGES_DIR/$E_ID" ] && audit_has page-expire "id=$E_ID"' "$out"
run pages_list
check "истёкшая пропала из списка, живых две" '[ "$(jget .count)" = 2 ]' "$out"

# ── 5а. Ключи, выданные в приложении Amnezia до подхвата ────────────────────
# У подхваченного пира на сервере только открытая часть ключа: в users.json privkey пуст и imported=true
# (так пишет import-adopt), в карточке — has_key=false, user-key отвечает no_private_key. Раньше служба
# молча выкидывала такой профиль, а страница, где все профили такие, отвечала 404 — человек решал, что
# сломана ссылка. Теперь на месте такого ключа — справка, а страницу совсем без ключей не создаём.
section "подхваченные ключи: справка вместо ключа, страница без единого ключа не создаётся"
users_add ivan "Иван" awg2 >/dev/null 2>>"$ALL_ERR"
users_add mira "Мира" awg2,awg3 >/dev/null 2>>"$ALL_ERR"
jq '.users |= map(if .name == "ivan" or .name == "mira" then .imported = true | .peers.awg2 += {privkey: "", imported: true} else . end)' \
    "$USERS_FILE" >"$TMP/u.json" && mv -f "$TMP/u.json" "$USERS_FILE" && chmod 600 "$USERS_FILE"
check "фикстура: у ivan пир awg2 без приватного ключа, у mira awg2 — без него, awg3 — с ним (has_key в карточке)" \
    '[ "$(users_show ivan 2>/dev/null | jq -c "[.peers.awg2.has_key]")" = "[false]" ] && [ "$(users_show mira 2>/dev/null | jq -c "[.peers.awg2.has_key, .peers.awg3.has_key]")" = "[false,true]" ]'
# page_shape <страница> → «профиль:imported:линейка:есть_ли_ключ…» по профилям, линейки каталога и есть ли приложения
page_shape() {
    ga_data "$1" | jq -r '[.profiles[] | "\(.id):\(.imported // false):\(.line):\(has("conf") or has("vpn") or has("qr_conf") or has("qr_vpn"))"]
        + ["lines=" + (.lines | join(",")), "apps=\(.apps | length > 0)"] | join(" ")' 2>/dev/null
}
npages() { find "$PAGES_DIR" -maxdepth 1 -name "p-*" | wc -l | tr -d ' '; }
n_before=$(npages)
run pages_create ivan 24h
check "все профили без приватного ключа (ivan) → no_private_key (rc 3), stdout пуст, страница не создана" \
    '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = no_private_key ] && [ -z "$out" ] && [ "$(npages)" = "$n_before" ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
check "отказ говорит, почему, и называет выход: профиль awg2, «Привязать старый ключ» или перевыпуск" \
    'grep -qF "$(tf pages_err_no_privkey ivan awg2)" "$ERR" && grep -qF "«Привязать старый ключ»" "$ERR" && grep -q "перевыпустите" "$ERR"' "$(errs)"
# UI-9: этот текст бот и панель показывают как есть — пути SSH-меню («Ключ» → …, «в карточке») в нём быть не
# должно: в боте и панели таких пунктов нет. Где лежит действие в SSH, говорит сам экран SSH (pg_attach_where).
check "UI-9: текст отказа no_private_key без пути SSH-меню (ни «Ключ» →, ни «карточке»), в обоих языках" \
    'for l in ru en; do v=$(sed -n "s/^I18N\[pages_err_no_privkey\]=\"\(.*\)\"$/\1/p" "$ROOT/lib/lang/$l.d/pages.sh"); [ -n "$v" ] || exit 1; case "$v" in *"→"*|*"«Ключ»"*|*"«Key»"*|*"карточке"*|*"card"*) exit 1 ;; esac; done' \
    "$(grep -h "pages_err_no_privkey" "$ROOT"/lib/lang/*.d/pages.sh)"
check "UI-9: путь в SSH называет экран SSH (pg_attach_where при no_private_key), и путь — из настоящих подписей: «$(t user_keys_menu)» → «Привязать старый ключ»" \
    'grep -q "= no_private_key \]; then" "$ROOT/install.sh" && grep -q "log_dim \"\$(t pg_attach_where)\"" "$ROOT/install.sh" && [[ "$(t pg_attach_where)" == *"«$(t user_keys_menu)» → «${I18N[user_attach_item]%% (*}»"* ]]' \
    "$(t pg_attach_where) | $(t user_keys_menu) | ${I18N[user_attach_item]}"
run pages_create mira 24h awg2
check "выбраны только профили без приватного ключа (mira, awg2) → тоже no_private_key" \
    '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = no_private_key ] && [ "$(npages)" = "$n_before" ]' "rc=$rc code=$GA_ERR_CODE"
last=$("$BASH5" "$INSTALL" --action=page-create --json --name=ivan --ttl=24h 2>>"$ALL_ERR" | tail -n 1)
check "мост page-create для ivan: status=error, code=no_private_key, message — тот же понятный текст" \
    '[ "$(jq -r .status <<<"$last")" = error ] && [ "$(jq -r .code <<<"$last")" = no_private_key ] && jq -r .message <<<"$last" | grep -qF "«Привязать старый ключ»"' "$last"
run pages_create mira 24h
MX_SECRET=$(sed -E 's#^https://example\.com/p-([A-Za-z0-9_-]{43})/$#\1#' <<<"$(jget .url)")
check "часть профилей без приватного ключа (mira) → страница создана: profiles=[awg3,awg2], imported_profiles=[awg2]" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget ".profiles | join(\",\")")" = awg3,awg2 ] && [ "$(jget ".imported_profiles | join(\",\")")" = awg2 ] && [[ "$MX_SECRET" =~ $SECRET_RE ]]' "rc=$rc $out $(errs)"
check "…и предупреждение в stderr называет awg2: там страница покажет объяснение вместо ключа" \
    'grep -qF "$(tf pages_warn_imported mira awg2)" "$ERR"' "$(errs)"
run pages_create mira 24h awg3
check "страница только на свой ключ (mira, awg3): без предупреждения, imported_profiles пуст" \
    '[ "$rc" = 0 ] && [ "$(jget ".imported_profiles | length")" = 0 ] && ! grep -qF "объяснение вместо ключа" "$ERR"' "rc=$rc $out $(errs)"

get "/p-$MX_SECRET/"
cp "$BODY" "$TMP/mix.html"
check "страница mira → 200: awg2 — справка (imported, линейка 2.0, без conf/vpn/QR), awg3 — ключ; линейки и приложения — только по awg3" \
    '[ "$code" = 200 ] && [ "$(page_shape "$TMP/mix.html")" = "awg3:false:3.1:true awg2:true:2.0:false lines=3.1 apps=true" ]' \
    "code=$code $(page_shape "$TMP/mix.html")"
check "без JavaScript: ссылок на key/ нет (ключи — только keys.json), справка «Ключ для AmneziaWG 2.0 у вас уже есть…» — основным текстом карточки «Профиль 2.0», а не мелкой сноской (UI-10)" \
    '! grep -q "href=\"key/" "$TMP/mix.html" && grep -qE "<b class=\"pg-key__title\">Профиль 2\.0 · порт [0-9]+</b>" "$TMP/mix.html" && grep -qF "<p class=\"pg-key__explain\">Ключ для AmneziaWG 2.0 у вас уже есть: его выдали раньше в приложении Amnezia" "$TMP/mix.html" && ! grep -qF "note\">Ключ для AmneziaWG" "$TMP/mix.html" && grep -qF "добавьте ключ заранее" "$TMP/mix.html" && grep -qF "только с включённым JavaScript" "$TMP/mix.html"' \
    "$(grep -o "<noscript>.*</noscript>" "$TMP/mix.html" | head -c 700)"
reveal "$MX_SECRET"; MX_GRANT=$REVEAL_GRANT
check "keys.json страницы mira: ключ только awg3, по подхваченному awg2 ключа нет" \
    '[ "$code" = 200 ] && [ "$(jq -r ".keys | keys | join(\",\")" "$BODY")" = awg3 ] && [ "$(jq -r .keys.awg3.conf "$BODY")" = "$(export_conf mira awg3 2>/dev/null)" ]' "code=$code $(head -c 300 "$BODY")"
get "/p-$MX_SECRET/key/awg2.conf?g=$MX_GRANT"
check "key/awg2.conf подхваченного профиля (и с токеном) → 404 nginx-вида" '[ "$code" = 404 ] && grep -q "<center>nginx</center>" "$BODY"' "code=$code"
get "/p-$MX_SECRET/qr/key/awg2/conf.png?g=$MX_GRANT"; c1=$code
get "/p-$MX_SECRET/qr/key/awg2/vpn.png?g=$MX_GRANT"; c2=$code
get "/p-$MX_SECRET/qr/key/awg2/vpn-1.png?g=$MX_GRANT"; c3=$code
check "QR подхваченного профиля (conf.png, vpn.png, vpn-1.png) → 404" '[ "$c1$c2$c3" = 404404404 ]' "$c1 $c2 $c3"
get "/p-$MX_SECRET/key/awg3.conf?g=$MX_GRANT"
check "свой ключ той же страницы отдаётся: key/awg3.conf → 200, байт в байт как export_conf" \
    '[ "$code" = 200 ] && [ "$(cat "$BODY")" = "$(export_conf mira awg3 2>/dev/null)" ]' "code=$code $(head -c 200 "$BODY")"
check "за ключом awg2 служба к мосту не ходила: user-key у mira — только awg3" \
    '! grep -q -- "--action=user-key --name=mira --profile=awg2" "$CALLS" && grep -q -- "--action=user-key --name=mira --profile=awg3" "$CALLS"' "$(grep mira "$CALLS")"
if [ "$HAVE_NODE" = 1 ]; then
    render_js "$TMP/mix.html"
    # Шаг 2 — «Ключ для AmneziaWG»: на Android по умолчанию рекомендуемое приложение (NC-16)
    check "app.js (node), страница mira: у «Профиль 2.0» справка (линейка 2.0) и ни одной кнопки ключа, у «Профиль 3.1» — «Показать ключ» (SEC50-05); шаги 1 и 2 на месте" \
        'grep -qxE "Профиль 2\.0 · порт [0-9]+" <<<"$JSOUT" && grep -qx "линейка 2.0" <<<"$JSOUT" && grep -qF "Ключ для AmneziaWG 2.0 у вас уже есть: его выдали раньше в приложении Amnezia" <<<"$JSOUT" && grep -qxE "Профиль 3\.1 · порт [0-9]+" <<<"$JSOUT" && [ "$(grep -c "^Показать ключ$" <<<"$JSOUT")" = 1 ] && ! grep -q "^Скачать \.conf$" <<<"$JSOUT" && grep -qx "Устройство и приложение" <<<"$JSOUT" && grep -qx "Ключ для AmneziaWG" <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
    render_js "$TMP/mix.html" en
    check "…по-английски: «You already have a key for AmneziaWG 2.0: it was issued earlier in the Amnezia app…»" \
        'grep -qF "You already have a key for AmneziaWG 2.0: it was issued earlier in the Amnezia app" <<<"$JSOUT"' "$(head -c 600 <<<"$JSOUT")"
else
    skipped "отрисовка app.js страницы со справкой" "нет node"
fi

# Страница, где подхвачены все профили, созданная до этой версии (meta.json — как писал прежний pages_create).
OLD_ID=$(ga_pages_new_id); OLD_SECRET=$(ga_pages_new_secret)
OLD_EXP=$(( $(date -u +%s) + 86400 ))
mkdir -m 700 "$PAGES_DIR/$OLD_ID"
(umask 077; printf '%s' "$OLD_SECRET" >"$PAGES_DIR/$OLD_ID/secret")
(umask 077; jq -nc --arg id "$OLD_ID" --arg uc "$(ga_users_get ivan | jq -r .created)" --arg iso "$(ga_pages_iso_at "$OLD_EXP")" \
    --argjson exp "$OLD_EXP" '{id: $id, user: "ivan", user_created: $uc, created: "2026-09-20T00:00:00Z", expires: $iso,
    expires_epoch: $exp, forever: false, ttl: "24h", profiles: ["awg2"], mirror: true}' >"$PAGES_DIR/$OLD_ID/meta.json")
get "/p-$OLD_SECRET/"
cp "$BODY" "$TMP/old.html"
check "страница, где подхвачены все профили (создана раньше): 200 со справкой, а не 404; каталог приложений ей не нужен" \
    '[ "$code" = 200 ] && [ "$(page_shape "$TMP/old.html")" = "awg2:true:2.0:false lines= apps=false" ]' "code=$code $(page_shape "$TMP/old.html")"
# Рукопожатия сервер ещё не видел (last_handshake 0 — так пишет подхват): ключом пользовались и раньше,
# поэтому «Ещё не подключался» здесь было бы неправдой — seen «не знаем» (null), строки нет.
check "рукопожатия ещё не видно → seen null, а не 0 («ещё не подключался» у подхваченного ключа — неправда)" \
    '[ "$(ga_data "$TMP/old.html" | jq -c "[.profiles[].seen]")" = "[null]" ]' "$(ga_data "$TMP/old.html" | jq -c "[.profiles[] | {id, seen}]")"
check "без JavaScript: шапка, «Доступ работает» и справка; ни ссылок на key/, ни «добавьте ключ заранее», ни заметки про JavaScript (добавлять здесь нечего)" \
    'ns=$(grep -o "<noscript>.*</noscript>" "$TMP/old.html") && grep -qF "<div class=\"pg-status__title\">Доступ работает</div>" <<<"$ns" && grep -qF "<p class=\"pg-key__explain\">Ключ для AmneziaWG 2.0 у вас уже есть" <<<"$ns" && ! grep -q "href=\"key/" "$TMP/old.html" && ! grep -qF "добавьте ключ заранее" "$TMP/old.html" && ! grep -qF "pg-ns" <<<"$ns" && ! grep -qF "pg-seen" <<<"$ns"' \
    "$(grep -o "<noscript>.*</noscript>" "$TMP/old.html" | head -c 700)"
reveal "$OLD_SECRET"; OLD_GRANT=$REVEAL_GRANT
get "/p-$OLD_SECRET/key/awg2.conf?g=$OLD_GRANT"; c1=$code
get "/p-$OLD_SECRET/qr/key/awg2/conf.png?g=$OLD_GRANT"; c2=$code
get "/p-$OLD_SECRET/qr/key/awg2/vpn.png?g=$OLD_GRANT"; c3=$code
check "файл и QR ключа на такой странице → 404" '[ "$c1$c2$c3" = 404404404 ]' "$c1 $c2 $c3"
if [ "$HAVE_NODE" = 1 ]; then
    render_js "$TMP/old.html"
    check "app.js (node), страница из одних справок: шапка и справка — без вкладок устройств, шагов, кнопок и «добавьте ключ заранее»" \
        'grep -qF "Ключ для AmneziaWG 2.0 у вас уже есть" <<<"$JSOUT" && grep -qx "доступ бессрочный, без лимита" <<<"$JSOUT" && grep -qx "Доступ работает" <<<"$JSOUT" && ! grep -qF "Устройство" <<<"$JSOUT" && ! grep -qF "Ключ для Amnezia" <<<"$(grep -v "у вас уже есть" <<<"$JSOUT")" && ! grep -qF "Скачать .conf" <<<"$JSOUT" && ! grep -qF "добавьте ключ заранее" <<<"$JSOUT" && ! grep -qF "не подключался" <<<"$JSOUT"' \
        "$(head -c 900 <<<"$JSOUT")"
fi
# Линейка в справке — та же, что у ключа этого профиля: из config.json (amnezia-awg2 приложения на 3.1 после
# подхвата — 3.1, а не 2.0 по имени), а если её пишет у пира ядро — оттуда.
ga_config_merge '{"profiles": {"awg2": {"line": "3.1"}}}' >/dev/null 2>&1
get "/p-$OLD_SECRET/"
check "линейка из config.json: профиль awg2 на 3.1 → «Ключ для AmneziaWG 3.1…»" \
    '[ "$code" = 200 ] && [ "$(page_shape "$BODY")" = "awg2:true:3.1:false lines= apps=false" ] && grep -qF "Ключ для AmneziaWG 3.1 у вас уже есть" "$BODY"' "code=$code $(page_shape "$BODY")"
HS=$(( $(date -u +%s) - 600 ))
jq --argjson hs "$HS" '.users |= map(if .name == "ivan" then .peers.awg2 += {line: "1.5", last_handshake: $hs} else . end)' \
    "$USERS_FILE" >"$TMP/u.json" && mv -f "$TMP/u.json" "$USERS_FILE" && chmod 600 "$USERS_FILE"
get "/p-$OLD_SECRET/"
check "линейка у пира в карточке важнее config.json; увиденное рукопожатие справка показывает (seen)" \
    '[ "$(page_shape "$BODY")" = "awg2:true:1.5:false lines= apps=false" ] && [ "$(ga_data "$BODY" | jq -c "[.profiles[].seen]")" = "[$HS]" ]' \
    "$(page_shape "$BODY") $(ga_data "$BODY" | jq -c "[.profiles[].seen]")"
jq '.users |= map(if .name == "ivan" then .peers.awg2 |= (del(.line) | .last_handshake = 0) else . end)' "$USERS_FILE" >"$TMP/u.json" && mv -f "$TMP/u.json" "$USERS_FILE" && chmod 600 "$USERS_FILE"
ga_config_merge '{"profiles": {"awg2": {"line": "2.0"}}}' >/dev/null 2>&1
users_disable ivan >/dev/null 2>>"$ALL_ERR"
get "/p-$OLD_SECRET/"
check "ivan выключен → «доступ приостановлен», справки о ключе нет" \
    '[ "$code" = 200 ] && grep -q "suspended" "$BODY" && ! grep -qF "Ключ для AmneziaWG" "$BODY"' "code=$code"
users_enable ivan >/dev/null 2>>"$ALL_ERR"
check "в журнале службы нет ни секретов этих страниц, ни «ключ awg2 не выдан» (за ним и не ходили)" \
    '! grep -qF -- "$MX_SECRET" "$TMP/server.log" && ! grep -qF -- "$OLD_SECRET" "$TMP/server.log" && ! grep -q "ключ awg2 не выдан" "$TMP/server.log"'
# SEC-G3: страница 0.4.0–0.4.2 без метки user_created. Тёзка, заведённый после удаления владельца ссылки
# (запись с этим именем моложе самой страницы), ключей по ней не получает; настоящий владелец — получает:
# страницу создают только на уже заведённого человека, его запись всегда старше страницы.
users_add frank "Фрэнк" awg2 >/dev/null 2>>"$ALL_ERR"
legacy_page() {   # legacy_page <пользователь> <created страницы, ISO> → LG_ID, LG_SECRET (meta.json как до 0.4.3)
    local exp
    exp=$(( $(date -u +%s) + 86400 ))
    LG_ID=$(ga_pages_new_id); LG_SECRET=$(ga_pages_new_secret)
    mkdir -m 700 "$PAGES_DIR/$LG_ID"
    (umask 077; printf '%s' "$LG_SECRET" >"$PAGES_DIR/$LG_ID/secret")
    (umask 077; jq -nc --arg id "$LG_ID" --arg u "$1" --arg c "$2" --arg iso "$(ga_pages_iso_at "$exp")" --argjson exp "$exp" \
        '{id: $id, user: $u, created: $c, expires: $iso, expires_epoch: $exp, forever: false, ttl: "24h", profiles: ["awg2"], mirror: true}' \
        >"$PAGES_DIR/$LG_ID/meta.json")
}
F_CREATED=$(ga_users_get frank | jq -r .created)
legacy_page frank "$(jq -rn --arg c "$F_CREATED" '$c | fromdateiso8601 + 60 | todateiso8601')"; OWN_ID=$LG_ID; OWN_SECRET=$LG_SECRET
legacy_page frank "$(jq -rn --arg c "$F_CREATED" '$c | fromdateiso8601 - 3600 | todateiso8601')"; NS_ID=$LG_ID; NS_SECRET=$LG_SECRET
check "фикстура SEC-G3: у обеих страниц frank нет user_created, created — на минуту позже и на час раньше записи" \
    '! grep -q user_created "$PAGES_DIR/$OWN_ID/meta.json" && ! grep -q user_created "$PAGES_DIR/$NS_ID/meta.json" && [ "$(jq -r .created "$PAGES_DIR/$OWN_ID/meta.json")" \> "$F_CREATED" ] && [ "$(jq -r .created "$PAGES_DIR/$NS_ID/meta.json")" \< "$F_CREATED" ]' \
    "$F_CREATED $(cat "$PAGES_DIR/$OWN_ID/meta.json" "$PAGES_DIR/$NS_ID/meta.json" 2>&1)"
get "/p-$OWN_SECRET/"
check "SEC-G3: страница без метки, запись владельца старше страницы → 200, ключ отдаётся" '[ "$code" = 200 ]' "code=$code"
get "/p-$NS_SECRET/"; c1=$code
reveal "$NS_SECRET"; c2=$code
get "/p-$NS_SECRET/key/awg2.conf"; c3=$code
check "SEC-G3: страница без метки, а запись с этим именем моложе страницы (тёзка) → 404 на страницу, keys.json и файл ключа" \
    '[ "$c1$c2$c3" = 404404404 ]' "$c1 $c2 $c3"
check "SEC-G3: отказ тёзке — в журнале службы, без секрета" \
    'grep -q "заведён позже страницы — отказ" "$TMP/server.log" && ! grep -qF -- "$NS_SECRET" "$TMP/server.log"' "$(grep "позже страницы" "$TMP/server.log" | head -2)"
# Уборка тика согласна со службой: страницу тёзки она снимает (в списках страниц она висела бы «живой»,
# а открывалась бы 404), страницу настоящего владельца и страницы с меткой записи — не трогает. users.json,
# который не читается, — не повод рушить страницы.
cp "$USERS_FILE" "$TMP/users.good"
printf '{broken' >"$USERS_FILE"
run pages_tick
check "SEC-G3: битый users.json — уборка тика страниц не трогает (ни тёзкину, ни чужие)" \
    '[ -d "$PAGES_DIR/$NS_ID" ] && [ -d "$PAGES_DIR/$OWN_ID" ] && [ -d "$PAGES_DIR/$OLD_ID" ] && ! audit_has page-revoke "id=$NS_ID"' "rc=$rc $(errs)"
cp "$TMP/users.good" "$USERS_FILE"
run pages_tick
check "SEC-G3: pages_tick снимает страницу без метки, где запись моложе страницы (аудит page-revoke), а страницы владельца frank и ivan (с меткой) оставляет" \
    '[ "$rc" = 0 ] && [ ! -d "$PAGES_DIR/$NS_ID" ] && [ -d "$PAGES_DIR/$OWN_ID" ] && [ -d "$PAGES_DIR/$OLD_ID" ] && audit_has page-revoke "id=$NS_ID" && ! audit_has page-revoke "id=$OWN_ID"' \
    "rc=$rc $(ls "$PAGES_DIR" 2>&1) $(errs)"
run pages_revoke_all frank
run pages_revoke_all mira
run pages_revoke_all ivan
run pages_list
check "страницы mira и ivan отозваны — живых снова две" '[ "$rc" = 0 ] && [ "$(jget .count)" = 2 ]' "$out"

# ── 6. Отзыв всех: маршрут и служба снимаются ──────────────────────────────
section "pages_revoke_all → обвязка снята"
run pages_revoke_all bob
check "pages_revoke_all bob: revoked=0, чужие страницы не тронуты" '[ "$rc" = 0 ] && [ "$(jget .revoked)" = 0 ] && [ -d "$PAGES_DIR/$P_ID" ]' "$out"
run pages_revoke_all
check "pages_revoke_all: revoked=2" '[ "$rc" = 0 ] && one_line_json && [ "$(jget .revoked)" = 2 ]' "$out"
check "страниц не осталось: site.pages_location=false, локации в vhost нет, якорь на месте" \
    '[ "$(ga_config_get .site.pages_location)" = false ] && hasnt "proxy_pass http://127\.0\.0\.1:1987;" && hasnt "location \^~ /_ga_mirror/" && has "# GOAMNEZIA_PAGES_LOCATION"' "$(grep -n 1987 "$CONF")"
check "служба снята: DRY systemctl disable --now goamnezia-pages.service, файл юнита удалён" \
    'grep -q "DRY: systemctl disable --now goamnezia-pages.service" "$ERR" && [ ! -f "$UNIT" ]' "$(errs)"
get "/p-$P_SECRET/"
check "после отзыва всех бывший адрес → 404" '[ "$code" = 404 ]' "code=$code"
run pages_tick
check "pages_tick на пустом каталоге: {expired: 0, active: 0}, без единой строки DRY systemctl (снимать нечего)" \
    '[ "$rc" = 0 ] && [ "$(jget .expired)" = 0 ] && [ "$(jget .active)" = 0 ] && ! grep -q "DRY: systemctl" "$ERR"' "$out $(errs)"
kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null; SERVER_PID=""

# ── 6a. Обвязка по факту и уход сайта с домена ─────────────────────────────
section "pages_tick чинит обвязку; website_disable_site отзывает страницы"
run pages_create anna 24h
M_ID=$(jget .id)
check "страница создана заново, юнит и флаг на месте" '[ "$rc" = 0 ] && [ -f "$UNIT" ] && [ "$(ga_config_get .site.pages_location)" = true ]' "$(errs)"
rm -f "$UNIT"
ga_config_set .site.pages_location false >/dev/null 2>&1
run pages_tick
check "юнит стёрт руками, флаг сброшен → pages_tick восстанавливает и юнит, и маршрут (reconcile раз в тик)" \
    '[ "$rc" = 0 ] && [ -f "$UNIT" ] && [ "$(ga_config_get .site.pages_location)" = true ] && grep -q "DRY: systemctl enable --now goamnezia-pages.service" "$ERR"' "$(errs)"
run website_disable_site
check "website_disable_site (→ lite): страницы отозваны с аудитом page-revoke reason=mode, каталог пуст" \
    '[ "$rc" = 0 ] && [ ! -d "$PAGES_DIR/$M_ID" ] && audit_has page-revoke "id=$M_ID user=anna reason=mode" && grep -qF "$(tf pages_revoked_mode 1)" "$ERR"' "rc=$rc $(errs)"
check "…служба снята и флаг маршрута сброшен: goamnezia-pages не остаётся включённым в lite" \
    '[ ! -f "$UNIT" ] && [ "$(ga_config_get .site.pages_location)" = false ] && grep -q "DRY: systemctl disable --now goamnezia-pages.service" "$ERR"' "$(errs)"
# Снятие флага шло пересборкой vhost при ещё не записанном lite — и сайт со страницей возвращался (03.10.2026)
check "…и vhost сайта остаётся снятым: отзыв страниц его не пересобирает" '[ ! -f "$CONF" ]' "$(ls -la "$(dirname "$CONF")" 2>&1)"
ga_config_merge '{"mode": "pro", "domain": "example.com", "site": {"cert": "letsencrypt"}}' >/dev/null 2>&1

# ── 6б. Удаление пользователя снимает его страницы (SR-05, 25.09.2026) ────────
# Служба страниц выдаёт ключи по ИМЕНИ: страница удалённого переживала удаление, и тёзка,
# заведённый позже, получал по старой ссылке СВОИ ключи. Теперь удаление снимает страницы,
# страница помнит метку создания записи, а сироту от прежних версий снимает уборка.
section "удаление пользователя снимает его страницы; сирота от прежней версии снимается уборкой"
users_add carol "Кэрол" awg2 >/dev/null 2>>"$ALL_ERR"
run pages_create carol forever
C_ID=$(jget .id)
check "страница carol привязана к записи пользователя (user_created в meta.json)" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".user_created // empty" "$PAGES_DIR/$C_ID/meta.json")" = "$(ga_users_get carol | jq -r .created)" ]' "rc=$rc $(errs)"
run users_del carol
check "users_del carol: страница снята сразу, в аудите page-revoke" \
    '[ "$rc" = 0 ] && [ ! -d "$PAGES_DIR/$C_ID" ] && audit_has page-revoke "id=$C_ID user=carol reason=revoked"' "rc=$rc $(errs)"
users_add dave "Дэйв" awg2 >/dev/null 2>>"$ALL_ERR"
run pages_create dave 24h
D_ID=$(jget .id)
# Удаление «как до 0.4.3»: запись ушла из users.json, страница осталась
jq '.users |= map(select(.name != "dave"))' "$GOAMNEZIA_DIR/users.json" >"$TMP/u.json" && mv -f "$TMP/u.json" "$GOAMNEZIA_DIR/users.json"
run pages_tick
check "страницу пользователя, которого нет в users.json, снимает уборка тика" \
    '[ "$rc" = 0 ] && [ ! -d "$PAGES_DIR/$D_ID" ]' "rc=$rc $(ls "$PAGES_DIR" 2>&1) $(errs)"

# ── 7. Каталог приложений ───────────────────────────────────────────────────
section "clients_list"
run clients_list
check "clients_list: одна строка JSON, checked — дата сверки каталога из lib/clients.json, не меньше 10 приложений, у каждого id/name/developer/official/platforms/import" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .checked)" = "$(jq -r .checked "$ROOT/lib/clients.json")" ] && [ "$(jget ".apps | length")" -ge 10 ] && [ "$(jget "[.apps[] | select(has(\"id\") and has(\"name\") and has(\"developer\") and has(\"official\") and has(\"platforms\") and has(\"import\"))] | length")" = "$(jget ".apps | length")" ]' "$out"
check "официальные идут первыми, AmneziaVPN и AmneziaWG — официальные, incy — нет" \
    '[ "$(jget ".apps[0].official")" = true ] && [ "$(jget ".apps[] | select(.id == \"amneziavpn\") | .official")" = true ] && [ "$(jget ".apps[] | select(.id == \"incy\") | .official")" = false ]'
check "зеркало только у открытых: amneziavpn (android, windows, macos, linux), amneziawg (android, windows), wgtunnel (android); у incy и defaultvpn — null" \
    '[ "$(jget ".apps[] | select(.id == \"amneziavpn\") | .mirror.assets | keys | join(\",\")")" = android,linux,macos,windows ] && [ "$(jget ".apps[] | select(.id == \"amneziawg\") | .mirror.assets | keys | join(\",\")")" = android,windows ] && [ "$(jget ".apps[] | select(.id == \"wgtunnel\") | .mirror.assets | keys | join(\",\")")" = android ] && [ "$(jget ".apps[] | select(.id == \"incy\") | .mirror")" = null ] && [ "$(jget ".apps[] | select(.id == \"defaultvpn\") | .mirror")" = null ]' "$out"
check "у AmneziaWG на Windows сборка из своего репозитория (asset объектом с repo)" \
    '[ "$(jget ".apps[] | select(.id == \"amneziawg\") | .mirror.assets.windows.repo")" = amnezia-vpn/amneziawg-windows-client ]'
run clients_list ios
check "фильтр platform=ios: только платформа ios у каждого, WireSock и роутеров нет" \
    '[ "$rc" = 0 ] && [ "$(jget "[.apps[].platforms | keys[]] | unique | join(\",\")")" = ios ] && [ -z "$(jget ".apps[] | select(.id == \"wiresock\" or .id == \"keenetic\") | .id")" ]' "$out"
run clients_list "" 3.1
check "фильтр line=3.1: WireSock, Keenetic и GL.iNet (3.1: no) выпали, AmneziaWG с версией 3.1.4 на iOS остался" \
    '[ "$rc" = 0 ] && [ -z "$(jget ".apps[] | select(.id == \"wiresock\" or .id == \"keenetic\" or .id == \"glinet\") | .id")" ] && [ "$(jget ".apps[] | select(.id == \"amneziawg\") | .platforms.ios.support[\"3.1\"]")" = 3.1.4 ]' "$out"
run clients_list router 2.0
check "router + 2.0: Keenetic (с 5.1 Alpha 3) и GL.iNet (с 4.9) есть, MikroTik (unknown) остаётся" \
    '[ "$rc" = 0 ] && [ -n "$(jget ".apps[] | select(.id == \"keenetic\") | .id")" ] && [ -n "$(jget ".apps[] | select(.id == \"glinet\") | .id")" ] && [ -n "$(jget ".apps[] | select(.id == \"mikrotik\") | .id")" ]' "$out"
run clients_list fridge
check "неизвестная платформа → invalid_platform (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_platform ]' "rc=$rc"
run clients_list "" 9.9
check "неизвестная линейка → invalid_line (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_line ]' "rc=$rc"
check "каталог: у каждой ссылки kind из списка и https-адрес, у поддержки только yes/no/unknown/версия" \
    'python3 -c "
import json,re,sys
d=json.load(open(sys.argv[1]))
kinds={\"appstore\",\"googleplay\",\"github\",\"site\",\"izzy\"}
for a in d[\"apps\"]:
    for p,pd in a[\"platforms\"].items():
        assert p in d[\"platforms\"], p
        for l in pd[\"links\"]:
            assert l[\"kind\"] in kinds and l[\"url\"].startswith(\"https://\"), (a[\"id\"], l)
            assert set(l[\"note\"]) == {\"ru\",\"en\"}, (a[\"id\"], l)
        for line in d[\"lines\"]:
            v=pd[\"support\"][line]
            assert v in (\"yes\",\"no\",\"unknown\") or re.match(r\"^[0-9][0-9A-Za-z. ]*$\", v), (a[\"id\"], p, line, v)
        if \"hint\" in pd:
            assert set(pd[\"hint\"]) == {\"ru\",\"en\"} and all(pd[\"hint\"].values()), (a[\"id\"], p)
    assert set(a[\"import\"][\"hint\"]) == {\"ru\",\"en\"}, a[\"id\"]
" "$ROOT/lib/clients.json"'

# Apple TV (tvos): официального AmneziaWG для приставки нет (сверка 25.09.2026, clients-audit): в каталоге —
# WG Connect (неофициальное, платное после пробы) и incy (поддержка на Apple TV не подтверждена), у incy —
# своя подсказка платформы вместо общей «Открыть в приложении». Платформа одна и та же везде: каталог,
# вкладки страницы, тип platform моста и текст отказа invalid_platform.
run clients_list tvos
check "Apple TV: clients_list tvos → только WG Connect и incy, в ответе у обоих одна платформа tvos" \
    '[ "$rc" = 0 ] && [ "$(jget "[.apps[].id] | sort | join(\",\")")" = incy,wgconnect ] && [ "$(jget "[.apps[].platforms | keys[]] | unique | join(\",\")")" = tvos ]' "$out"
check "WG Connect: неофициальное, только tvos, App Store id6760960925, 1.0 и 2.0 — с 1.2.4, 3.1 — с 1.4.0, пометка «Неофициальное, платное после пробы»" \
    '[ "$(jget ".apps[] | select(.id == \"wgconnect\") | .official")" = false ] && [ "$(jget ".apps[] | select(.id == \"wgconnect\") | .platforms.tvos.links[0].url")" = https://apps.apple.com/app/id6760960925 ] && [ "$(jget ".apps[] | select(.id == \"wgconnect\") | .platforms.tvos.support | [.[\"1.0\"], .[\"2.0\"], .[\"3.1\"]] | join(\",\")")" = 1.2.4,1.2.4,1.4.0 ] && [ "$(jget ".apps[] | select(.id == \"wgconnect\") | .platforms.tvos.note.ru")" = "Неофициальное, платное после пробы" ] && [ "$(jq -r ".apps[] | select(.id == \"wgconnect\") | .platforms | keys | join(\",\")" "$ROOT/lib/clients.json")" = tvos ]' "$out"
check "incy на Apple TV: тот же листинг App Store, что у iPhone; поддержка всех линеек — unknown; своя подсказка про «TV relay» в обоих языках" \
    'c="$ROOT/lib/clients.json"; [ "$(jq -r ".apps[] | select(.id == \"incy\") | .platforms.tvos.links[0].url" "$c")" = "$(jq -r ".apps[] | select(.id == \"incy\") | .platforms.ios.links[0].url" "$c")" ] && [ "$(jq -r ".apps[] | select(.id == \"incy\") | [.platforms.tvos.support[]] | unique | join(\",\")" "$c")" = unknown ] && jq -e ".apps[] | select(.id == \"incy\") | .platforms.tvos.hint | (.ru | contains(\"TV relay\")) and (.en | contains(\"TV relay\"))" "$c" >/dev/null' \
    "$(jq -c ".apps[] | select(.id == \"incy\") | .platforms.tvos" "$ROOT/lib/clients.json" 2>&1 | head -c 600)"
cat >"$TMP/platforms_check.py" <<'EOPY'
import json, re, sys
root = sys.argv[1]
cat = json.load(open(root + "/lib/clients.json", encoding="utf-8"))["platforms"]
act = json.load(open(root + "/lib/actions.json", encoding="utf-8"))["param_types"]["platform"]["enum"]
src = open(root + "/lib/pages_server.py", encoding="utf-8").read()
tabs = re.findall(r'"([a-z_]+)"', re.search(r"^PLATFORMS = \(([^)]*)\)", src, re.M).group(1))
assert cat == act == tabs, (cat, act, tabs)
assert "tvos" in cat, cat
for lang in ("ru", "en"):
    text = open(root + "/lib/lang/%s.d/pages.sh" % lang, encoding="utf-8").read()
    listed = re.search(r'^I18N\[pages_err_platform\]=".*\((.*)\)"$', text, re.M).group(1)
    assert [x.strip() for x in listed.split(",")] == cat, (lang, listed)
# Подписи вкладок — PLATFORM_NAMES службы (их же получает app.js): те же платформы в том же порядке
sys.path.insert(0, root + "/lib")
import pages_server
for lang in ("ru", "en"):
    assert list(pages_server.PLATFORM_NAMES[lang]) == cat, (lang, pages_server.PLATFORM_NAMES[lang])
print("ok")
EOPY
check "платформы согласованы: каталог = вкладки страницы (PLATFORMS и подписи PLATFORM_NAMES) = тип platform моста = список в pages_err_platform (ru, en)" \
    'python3 "$TMP/platforms_check.py" "$ROOT"' "$(python3 "$TMP/platforms_check.py" "$ROOT" 2>&1 | tail -3)"

# ── 8. Мост: настоящий install.sh --action ──────────────────────────────────
section "мост install.sh --action=page-*/clients-list"
act() {   # act <действие> [--k=v …] → rc, last, nlines
    local action="$1"; shift
    "$BASH5" "$INSTALL" --action="$action" --json "$@" >"$OUT" 2>"$ERR"; rc=$?
    last=$(tail -n 1 "$OUT"); nlines=$(wc -l <"$OUT" | tr -d ' ')
    cat "$ERR" >>"$ALL_ERR"
    return 0
}
lget() { jq -r "$1" <<<"$last" 2>/dev/null; }
act page-create --name=anna --ttl=24h
check "page-create: одна строка JSON, status=success, url с секретом, profiles" \
    '[ "$nlines" = 1 ] && is_json "$last" && [ "$(lget .status)" = success ] && [[ "$(lget .url)" =~ ^https://example\.com/p-[A-Za-z0-9_-]{43}/$ ]] && [ "$(lget ".profiles | length")" = 2 ]' "rc=$rc last=$last $(tail -3 "$ERR")"
B_ID=$(lget .id); B_URL=$(lget .url)
act page-create --name=anna --ttl=8x
check "page-create с негодным сроком отвергает валидатор моста: bad_arg" '[ "$(lget .status)" = error ] && [ "$(lget .code)" = bad_arg ]' "$last"
act page-create --name=anna --ttl=24h --mirror=maybe
check "mirror не on/off → bad_arg (enum)" '[ "$(lget .status)" = error ] && [ "$(lget .code)" = bad_arg ]' "$last"
act page-list
check "page-list: pages без url, одна страница" \
    '[ "$(lget .status)" = success ] && [ "$(lget ".pages | length")" = 1 ] && [ "$(lget ".pages[0].url // \"\"")" = "" ] && [ "$(lget .count)" = 1 ]' "$last"
act page-url --id="$B_ID"
check "page-url: тот же адрес" '[ "$(lget .status)" = success ] && [ "$(lget .url)" = "$B_URL" ]' "$last"
# SEC46-02: адрес страницы — ключ к приватным ключам человека, а argv любого процесса читает каждый
# локальный пользователь (/proc/<pid>/cmdline). Шпион jq пишет свои аргументы: ни ответ page-create и
# page-url, ни экран «Показать ссылку» SSH-меню (_pages_card) не должны класть адрес в argv.
SPYBIN="$TMP/spybin"; mkdir -p "$SPYBIN"; : >"$TMP/argv.log"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\nexec "%s" "$@"\n' "$TMP/argv.log" "$(command -v jq)" >"$SPYBIN/jq"
chmod +x "$SPYBIN/jq"
PATH="$SPYBIN:$PATH" act page-create --name=anna --ttl=24h
SPY_ID=$(lget .id); SPY_SECRET=$(sed -E 's#^https://example\.com/p-([A-Za-z0-9_-]{43})/$#\1#' <<<"$(lget .url)")
PATH="$SPYBIN:$PATH" act page-url --id="$SPY_ID"
spy_again=$(lget .url)
# Экран SSH-меню — из install.sh как библиотеки: копия без последней строки (main "$@") рядом с lib/.
mkdir -p "$TMP/menu" && ln -sfn "$ROOT/lib" "$TMP/menu/lib" && sed '$d' "$INSTALL" >"$TMP/menu/install.sh"
printf '1\n\n0\n' | PATH="$SPYBIN:$PATH" "$BASH5" -c 'source "$1" && _pages_card "$2" anna' _ "$TMP/menu/install.sh" "$SPY_ID" \
    >"$TMP/menu.out" 2>&1
check "SEC46-02 шпион сработал: page-create и page-url отдали адрес, экран SSH «Показать ссылку» его напечатал" \
    '[[ "$SPY_SECRET" =~ $SECRET_RE ]] && [ "$spy_again" = "https://example.com/p-$SPY_SECRET/" ] && grep -qF "p-$SPY_SECRET/" "$TMP/menu.out" && [ "$(wc -l <"$TMP/argv.log" | tr -d " ")" -gt 10 ]' \
    "id=$SPY_ID again=${spy_again:0:30} $(tail -n 5 "$TMP/menu.out")"
check "SEC46-02 адрес страницы (page-create, page-url, SSH «Показать ссылку») не прошёл через argv jq" \
    '! grep -qF -- "$SPY_SECRET" "$TMP/argv.log"' "$(grep -F -- "$SPY_SECRET" "$TMP/argv.log" | head -3 | cut -c1-200)"
act page-revoke --id="$SPY_ID"
act page-url --id=p-zzzz
check "page-url с id не по образцу → bad_arg" '[ "$(lget .code)" = bad_arg ]' "$last"
act page-url --id=p-ffff
check "page-url неизвестной → page_not_found" '[ "$(lget .status)" = error ] && [ "$(lget .code)" = page_not_found ]' "$last"
act clients-list --platform=ios
check "clients-list --platform=ios: apps только с ios" '[ "$(lget .status)" = success ] && [ "$(lget "[.apps[].platforms | keys[]] | unique | join(\",\")")" = ios ]' "$last"
act watch-tick
# Заявка со страницы (POST …/request выше) доезжает до ядра тем же тиком: на
# стенде без сети загрузка честно проваливается кодом mirror_fetch_failed.
check "watch-tick несёт блоки pages и mirror: страница одна, заявка на amneziavpn/android подхвачена (requests=1, started=1)" \
    '[ "$(lget .status)" = success ] && [ "$(lget ".pages.active")" = 1 ] && [ "$(lget ".mirror.requests")" = 1 ] && [ "$(lget ".mirror.started")" = 1 ]' "$last"
check "заявка снята, а пара без сети — failed с кодом mirror_fetch_failed (в сеть на стенде не ходим)" \
    '[ ! -f "$GOAMNEZIA_MIRROR_DIR/amneziavpn/android/request" ] && [ "$(jq -r .state "$GOAMNEZIA_MIRROR_DIR/amneziavpn/android/state.json")" = failed ] && [ "$(jq -r .error "$GOAMNEZIA_MIRROR_DIR/amneziavpn/android/state.json")" = mirror_fetch_failed ]' "$(cat "$GOAMNEZIA_MIRROR_DIR/amneziavpn/android/state.json" 2>&1)"
act page-revoke --id="$B_ID"
check "page-revoke: success, id" '[ "$(lget .status)" = success ] && [ "$(lget .id)" = "$B_ID" ]' "$last"
act page-revoke-all
check "page-revoke-all на пустом: revoked=0" '[ "$(lget .status)" = success ] && [ "$(lget .revoked)" = 0 ]' "$last"
ga_config_merge '{"mode": "lite", "domain": "", "site": {"cert": ""}}' >/dev/null 2>&1
act page-create --name=anna --ttl=24h
check "мост в lite → pages_need_domain" '[ "$(lget .status)" = error ] && [ "$(lget .code)" = pages_need_domain ]' "$last"
check "секретов страниц нет ни в аудите, ни в stderr прогона" \
    '! grep -qE "p-[A-Za-z0-9_-]{43}" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qE "/p-[A-Za-z0-9_-]{43}/" "$ALL_ERR"' "$(grep -oE "p-[A-Za-z0-9_-]{20,}" "$GOAMNEZIA_AUDIT_LOG" | head -2)"

# ── 9. Статические проверки самого модуля ────────────────────────────────────
section "модуль не ломает статические проверки"
check "bash -n lib/pages.sh" "\"${BASH:-bash}\" -n '$ROOT/lib/pages.sh'"
check "python3 -m py_compile lib/pages_server.py" "python3 -m py_compile '$ROOT/lib/pages_server.py'"
check "lib/clients.json разбирается" "jq -e '.apps | length > 0' '$ROOT/lib/clients.json'"
check "паритет ключей словаря ru/en" \
    'diff <(grep -oE "^I18N\[[a-z_]+\]" "$ROOT/lib/lang/ru.d/pages.sh" | sort) <(grep -oE "^I18N\[[a-z_]+\]" "$ROOT/lib/lang/en.d/pages.sh" | sort) >/dev/null'
check "каждый ключ pages_*, запрошенный модулем, есть в обоих словарях" \
    'miss=0; for k in $(grep -oE "\\\$\((t|tf) pages_[a-z_]+" "$ROOT/lib/pages.sh" | awk "{print \$2}" | sort -u); do grep -q "^I18N\[$k\]" "$ROOT/lib/lang/ru.d/pages.sh" || miss=1; grep -q "^I18N\[$k\]" "$ROOT/lib/lang/en.d/pages.sh" || miss=1; done; [ "$miss" = 0 ]'
check "модуль, сервер, каталог и словари перечислены в FILES загрузчика" \
    'for f in lib/pages.sh lib/pages_server.py lib/clients.json lib/mirror.sh lib/lang/ru.d/pages.sh lib/lang/en.d/pages.sh; do grep -q "\"$f\"" "$ROOT/bootstrap.sh" || exit 1; done'
actions_ok() {
    local f="$ROOT/lib/actions.json"
    jq -e '.actions | has("clients-list") and has("page-create") and has("page-list") and has("page-url")
           and has("page-revoke") and has("page-revoke-all") and has("mirror-list") and has("mirror-fetch") and has("mirror-remove")' "$f" >/dev/null || return 1
    [ "$(jq -r '.actions["page-create"].secret' "$f")" = true ] || return 1
    [ "$(jq -r '.actions["page-url"].secret' "$f")" = true ] || return 1
    [ "$(jq -r '.actions["page-list"].readonly' "$f")" = true ] || return 1
    [ "$(jq -r '.actions["page-revoke-all"].destructive' "$f")" = true ] || return 1
    [ "$(jq -r '.actions["mirror-remove"].destructive' "$f")" = true ] || return 1
    [ "$(jq -r '.actions["page-create"].mcp' "$f")" = page_create ] || return 1
    [ "$(jq -r '.param_types.page_id.pattern' "$f")" = '^p-[a-f0-9]{4,8}$' ] || return 1
    [ "$(jq -r '.param_types.ttl.pattern' "$f")" = '^([1-9][0-9]{0,2}h|[1-9][0-9]{0,2}d|forever)$' ] || return 1
    return 0
}
check "действия page-*/mirror-*/clients-list в lib/actions.json: secret у page-create и page-url, destructive у page-revoke-all и mirror-remove" 'actions_ok'
check "ни одной видимой строки вне t/tf: log_* в модуле зовут только словарь" \
    '! grep -nE "log_(info|warning|error|success|dim|step) +\"[^\$]" "$ROOT/lib/pages.sh"' \
    "$(grep -nE "log_(info|warning|error|success|dim|step) +\"[^\$]" "$ROOT/lib/pages.sh" | head -3)"
check "в pages.sh нет своих регулярок под IPv4 (адреса — только функциями продукта)" \
    '! grep -nE "^[^#]*\[0-9\]\{1,3\}" "$ROOT/lib/pages.sh"'

# ── Итог ─────────────────────────────────────────────────────────────────────
DONE=1
printf '\n══ ИТОГ: пройдено %s, провалено %s, пропущено %s\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
