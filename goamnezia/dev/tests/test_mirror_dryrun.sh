#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон lib/mirror.sh (зеркало сборок приложений,
# docs/CLIENTS.md §8) на подставном GitHub
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_mirror_dryrun.sh
# Нужны: bash ≥ 5, jq, python3, sha256sum или shasum. Сеть не нужна: вместо
# curl модулю подставляется программа GOAMNEZIA_MIRROR_CURL, которая отдаёт
# файлы из каталога фикстур по адресу (API релиза, файл сборки, LICENSE), а
# адреса GitHub заменены GOAMNEZIA_MIRROR_API_BASE / GOAMNEZIA_MIRROR_RAW_BASE.
# В dry-run загрузка идёт в текущем процессе, поэтому итог виден сразу.
#
# Что проверяем: каталог (spec, unknown_app, not_mirrorable, форма аргументов)
# → dry-run без подставного curl в сеть не ходит (mirror_fetch_failed) →
# удачная загрузка: релиз → файл по regex → размер и sha256 по digest из API →
# атомарная публикация, meta.json, LICENSE с тега, SOURCE.txt, права 755/644,
# state.json ready, аудит → та же версия повторно не качается → без digest —
# mirror_unverified и ничего не опубликовано → неверный sha256 — то же, .part
# убран → размер не сошёлся → предел файла (mirror_too_big), предел каталога и
# свободного места (mirror_no_space) → замок пары (mirror_busy) → нет файла по
# образцу → заявки со страниц через mirror_tick: готовая пара — заявка снята,
# новая — загружена, предел «одна на пару в 10 минут» и «6 в час» откладывают
# → суточная сверка версий подтягивает новую → mirror_remove → мост
# install.sh --action=mirror-*/watch-tick/upstream-tick → запуск файлом
# (--worker) → статические проверки.
#
# Ничего вне $TMP не пишем.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL="$ROOT/install.sh"
BASH5="${BASH5:-$BASH}"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-mirror.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
ALL_ERR="$TMP/all-stderr.log"
: >"$ALL_ERR"
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; rm -rf "$TMP"' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_DIR="$TMP/goamnezia"
export GOAMNEZIA_LOCK_DIR="$TMP/run"
export GOAMNEZIA_LOG_FILE="$TMP/goamnezia.log"
export GOAMNEZIA_AUDIT_LOG="$TMP/audit.log"
export GOAMNEZIA_NOTIFY_DIR="$TMP/run/notify"
export GOAMNEZIA_BUILD_ROOT="$TMP/build"
export GOAMNEZIA_SERVER_IP="203.0.113.10"
export GOAMNEZIA_SYSTEMD_DIR="$TMP/systemd"
export GOAMNEZIA_NGINX_DIR="$TMP/nginx"
export GOAMNEZIA_SITE_ROOT="$TMP/site"
export GOAMNEZIA_LE_DIR="$TMP/letsencrypt"
export GOAMNEZIA_MIRROR_DIR="$TMP/mirror"
export GOAMNEZIA_MIRROR_API_BASE="https://fixture.invalid/repos"
export GOAMNEZIA_MIRROR_RAW_BASE="https://fixture.invalid/raw"
export UPSTREAM_DIR="$TMP/upstream"
export FAKECURL_ROOT="$TMP/fixture"
export FAKECURL_LOG="$TMP/curl.log"
unset GOAMNEZIA_MIRROR_CURL GOAMNEZIA_MIRROR_FOREGROUND GOAMNEZIA_UPSTREAM_FIXTURE_DIR
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$FAKECURL_ROOT" "$UPSTREAM_DIR"
cp -R "$ROOT/lib/upstream/." "$UPSTREAM_DIR/"
: >"$FAKECURL_LOG"

# Временные файлы с ключами — в каталоге стенда (L1-19). Без переменной продукт кладёт их в /dev/shm,
# а он есть только на Linux: там они жили бы на машине прогона, и оборванный прогон оставлял бы ключи.
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 700; return 0; }
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
is_json() { jq -e . <<<"$1" >/dev/null 2>&1; }
# Права восьмерично: сначала GNU (-c), потом BSD (-f). Не наоборот: у GNU «-f» — сведения
# о файловой системе, он печатал их перед правами, и на Linux краснела каждая проверка 600/700.
perm() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }
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
audit_has() {
    jq -R -e -n --arg a "$1" --arg d "${2:-}" \
        '[inputs | fromjson? // empty | select(.action == $a and ($d == "" or ((.details // "") | contains($d))))] | length > 0' \
        "$GOAMNEZIA_AUDIT_LOG" >/dev/null 2>&1
}
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
# grep -c печатает 0 и выходит с 1, когда совпадений нет: «|| printf 0» здесь
# дал бы «0\n0».
curl_calls() { grep -c "$1" "$FAKECURL_LOG" 2>/dev/null; return 0; }
pair() { printf '%s/%s/%s' "$GOAMNEZIA_MIRROR_DIR" "$1" "$2"; }

# ── Подставной curl: файлы по адресу из каталога фикстур ─────────────────────
FAKECURL="$TMP/fakecurl"
cat >"$FAKECURL" <<'EOF'
#!/usr/bin/env bash
# curl-заглушка: -o <файл> и адрес; всё остальное игнорируется. Адрес
# https://fixture.invalid/<путь> отдаётся из $FAKECURL_ROOT/<путь>, иначе — 22.
out=""; url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        --max-filesize) printf 'maxfilesize=%s\n' "$2" >>"${FAKECURL_LOG:-/dev/null}"; shift 2 ;;
        --max-time|-A|-H|--proto) shift 2 ;;
        http*) url="$1"; shift ;;
        *) shift ;;
    esac
done
printf '%s\n' "$url" >>"${FAKECURL_LOG:-/dev/null}"
rel="${url#https://fixture.invalid/}"
[ -f "$FAKECURL_ROOT/$rel" ] || exit 22
cp "$FAKECURL_ROOT/$rel" "$out"
EOF
chmod 755 "$FAKECURL"
# mkrelease <owner/repo> <tag> <файл> <байт> <digest: ok|none|wrong> [объявленный размер]
mkrelease() {
    local repo="$1" tag="$2" name="$3" size="$4" mode="$5" declared="${6:-$4}" digest hash
    mkdir -p "$FAKECURL_ROOT/dl" "$FAKECURL_ROOT/repos/$repo/releases" "$FAKECURL_ROOT/raw/$repo/$tag"
    head -c "$size" /dev/urandom >"$FAKECURL_ROOT/dl/$name"
    hash=$(sha "$FAKECURL_ROOT/dl/$name")
    case "$mode" in
        ok)    digest="\"sha256:$hash\"" ;;
        wrong) digest="\"sha256:$(printf '%064d' 7)\"" ;;
        *)     digest="null" ;;
    esac
    jq -nc --arg tag "$tag" --arg name "$name" --argjson size "$declared" --argjson digest "$digest" \
        '{tag_name: $tag, prerelease: false, assets: [{name: "README.txt", size: 3, browser_download_url: "https://fixture.invalid/dl/README.txt", digest: null},
                                                       {name: $name, size: $size, browser_download_url: ("https://fixture.invalid/dl/" + $name), digest: $digest}]}' \
        >"$FAKECURL_ROOT/repos/$repo/releases/latest"
    printf 'MIT License (fixture %s %s)\n' "$repo" "$tag" >"$FAKECURL_ROOT/raw/$repo/$tag/LICENSE"
    printf '%s' "$hash"
}

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
for f in lib/common.sh lib/i18n.sh lib/mirror.sh lib/clients.json lib/lang/ru.d/mirror.sh lib/lang/en.d/mirror.sh; do
    [ -f "$ROOT/$f" ] || { printf '!! нет %s\n' "$f"; exit 2; }
done
for _b in jq python3; do command -v "$_b" >/dev/null 2>&1 || { printf '!! нет %s\n' "$_b"; exit 2; }; done
command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 || { printf '!! нет sha256sum/shasum\n'; exit 2; }
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia website pages mirror; do
    # shellcheck disable=SC1090
    source "$ROOT/lib/$_m.sh"
done
check "модуль подключён: mirror_list, mirror_fetch, mirror_remove, mirror_tick, ga_mirror_worker" \
    'type mirror_list >/dev/null && type mirror_fetch >/dev/null && type mirror_remove >/dev/null && type mirror_tick >/dev/null && type ga_mirror_worker >/dev/null'
check "словарь mirror подхвачен движком i18n (mirror_err_unverified…)" '[ -n "${I18N[mirror_err_no_digest]:-}" ] && [ -n "${I18N[mirror_err_sha]:-}" ]'
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOC
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}, "panel": {"port": 1985}, "site": {"template_id": ""}, "options": {}, "mcp": {"http_enabled": false, "http_port": 1986}}
EOC
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"

# ── 1. Каталог ──────────────────────────────────────────────────────────────
section "каталог: что зеркалить можно, а что нет"
run ga_mirror_spec wgtunnel android
check "spec wgtunnel/android: repo wgtunnel/android, regex на .apk, лицензия MIT" \
    '[ "$rc" = 0 ] && [ "$(jget .repo)" = wgtunnel/android ] && [ "$(jget .pattern)" != "" ] && [ "$(jget .license)" = MIT ]' "rc=$rc $out"
run ga_mirror_spec amneziawg windows
check "spec amneziawg/windows: сборка из своего репозитория amnezia-vpn/amneziawg-windows-client" \
    '[ "$rc" = 0 ] && [ "$(jget .repo)" = amnezia-vpn/amneziawg-windows-client ]' "$out"
run ga_mirror_spec nosuchapp android
check "неизвестное приложение → unknown_app (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = unknown_app ]' "rc=$rc code=$GA_ERR_CODE"
run ga_mirror_spec incy android
check "закрытое приложение → not_mirrorable (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = not_mirrorable ]' "rc=$rc code=$GA_ERR_CODE"
run ga_mirror_spec amneziavpn android_tv
check "платформа без сборки в каталоге (amneziavpn/android_tv) → not_mirrorable" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = not_mirrorable ]' "rc=$rc code=$GA_ERR_CODE"
run mirror_fetch amneziavpn ios
check "iOS не зеркалится никогда → invalid_argument (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_argument ]' "rc=$rc code=$GA_ERR_CODE"
run mirror_fetch "../x" android
check "приложение не по образцу → invalid_argument" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_argument ]' "rc=$rc"
run mirror_list
check "mirror_list на пустом зеркале: items=[], used_mb=0, пределы 400/3072 по умолчанию" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .count)" = 0 ] && [ "$(jget .used_mb)" = 0 ] && [ "$(jget .max_file_mb)" = 400 ] && [ "$(jget .max_total_mb)" = 3072 ]' "$out"

# ── 2. Dry-run без подставного curl: в сеть не ходим ────────────────────────
section "dry-run без сети"
run mirror_fetch wgtunnel android
check "без GOAMNEZIA_MIRROR_CURL загрузка отвечает mirror_fetch_failed, state.json failed, замка нет" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = mirror_fetch_failed ] && [ "$(jq -r .state "$(pair wgtunnel android)/state.json")" = failed ] && [ "$(jq -r .error "$(pair wgtunnel android)/state.json")" = mirror_fetch_failed ] && [ ! -d "$(pair wgtunnel android)/.lock" ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
check "в stderr — DRY curl … и ни одного файла сборки" 'grep -q "DRY: curl https://fixture.invalid/repos/wgtunnel/android/releases/latest" "$ERR" && [ -z "$(find "$(pair wgtunnel android)" -name "*.apk" 2>/dev/null)" ]' "$(errs)"

# ── 3. Удачная загрузка ─────────────────────────────────────────────────────
section "загрузка с подставного GitHub: sha256 сверяется, публикация атомарная"
export GOAMNEZIA_MIRROR_CURL="$FAKECURL"
WG_HASH=$(mkrelease wgtunnel/android v1.2.3 wgtunnel-standalone-v1.2.3.apk 4096 ok)
run mirror_fetch wgtunnel android
check "mirror_fetch wgtunnel android: rc=0, JSON {app, platform, state=ready} (dry-run ведёт загрузку в этом процессе)" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .app)" = wgtunnel ] && [ "$(jget .platform)" = android ] && [ "$(jget .state)" = ready ]' "rc=$rc $out $(errs)"
WD=$(pair wgtunnel android)
check "файл сборки опубликован, .part не осталось, sha256 совпал с digest из API" \
    '[ -f "$WD/wgtunnel-standalone-v1.2.3.apk" ] && [ -z "$(ls -A "$WD" | grep "\.part$")" ] && [ "$(sha "$WD/wgtunnel-standalone-v1.2.3.apk")" = "$WG_HASH" ]' "$(ls -la "$WD")"
check "meta.json: version v1.2.3, file, size 4096, sha256, fetched, source_url на тег, repo" \
    '[ "$(jq -r .version "$WD/meta.json")" = v1.2.3 ] && [ "$(jq -r .file "$WD/meta.json")" = wgtunnel-standalone-v1.2.3.apk ] && [ "$(jq -r .size "$WD/meta.json")" = 4096 ] && [ "$(jq -r .sha256 "$WD/meta.json")" = "$WG_HASH" ] && [ -n "$(jq -r .fetched "$WD/meta.json")" ] && [ "$(jq -r .source_url "$WD/meta.json")" = https://github.com/wgtunnel/android/tree/v1.2.3 ]' "$(cat "$WD/meta.json")"
check "LICENSE — с того же тега репозитория, SOURCE.txt — ссылка на исходники версии" \
    'grep -q "fixture wgtunnel/android v1.2.3" "$WD/LICENSE" && grep -q "^https://github.com/wgtunnel/android/tree/v1.2.3$" "$WD/SOURCE.txt"' "$(cat "$WD/LICENSE" "$WD/SOURCE.txt" 2>&1)"
check "права для nginx: каталоги 755, файл сборки, meta.json, LICENSE, SOURCE.txt, state.json — 644" \
    '[ "$(perm "$GOAMNEZIA_MIRROR_DIR")" = 755 ] && [ "$(perm "$WD")" = 755 ] && [ "$(perm "$WD/wgtunnel-standalone-v1.2.3.apk")" = 644 ] && [ "$(perm "$WD/meta.json")" = 644 ] && [ "$(perm "$WD/LICENSE")" = 644 ] && [ "$(perm "$WD/state.json")" = 644 ]' "$(ls -la "$WD")"
check "state.json: ready, bytes=total=4096, version" \
    '[ "$(jq -r .state "$WD/state.json")" = ready ] && [ "$(jq -r .bytes "$WD/state.json")" = 4096 ] && [ "$(jq -r .total "$WD/state.json")" = 4096 ] && [ "$(jq -r .version "$WD/state.json")" = v1.2.3 ]' "$(cat "$WD/state.json")"
check "замок пары снят, request отсутствует" '[ ! -d "$WD/.lock" ] && [ ! -f "$WD/request" ]'
check "подставной curl звали за релизом, файлом и LICENSE" \
    '[ "$(curl_calls "repos/wgtunnel/android/releases/latest")" -ge 1 ] && [ "$(curl_calls "dl/wgtunnel-standalone-v1.2.3.apk")" = 1 ] && [ "$(curl_calls "raw/wgtunnel/android/v1.2.3/LICENSE")" = 1 ]' "$(cat "$FAKECURL_LOG")"
check "curl получает предел размера ВО ВРЕМЯ загрузки: --max-filesize <размер из API> у файла сборки" \
    '[ "$(curl_calls "maxfilesize=4096")" = 1 ]' "$(cat "$FAKECURL_LOG")"
check "аудит: mirror-fetch … start и … ok с версией и размером" \
    'audit_has mirror-fetch "app=wgtunnel platform=android version=v1.2.3 file=wgtunnel-standalone-v1.2.3.apk size=4096 start" && audit_has mirror-fetch "size=4096 ok"' "$(grep mirror-fetch "$GOAMNEZIA_AUDIT_LOG")"
run mirror_list
check "mirror_list: одна пара ready с версией, sha256, размером; used_mb=0 (4 КБ)" \
    '[ "$(jget .count)" = 1 ] && [ "$(jget ".items[0].state")" = ready ] && [ "$(jget ".items[0].version")" = v1.2.3 ] && [ "$(jget ".items[0].sha256")" = "$WG_HASH" ] && [ "$(jget ".items[0].size")" = 4096 ] && [ "$(jget ".items[0].published")" = true ] && [ "$(jget .used_bytes)" = 4096 ]' "$out"
# Идущая загрузка: байты в mirror_list — по размеру .<файл>.part, а не по
# state.json (там 0 в начале и size в конце). Пара подставная, после проверки убирается.
PD=$(pair amneziawg android); mkdir -p "$PD"
printf '{"state":"downloading","updated":"2026-09-17T10:00:00Z","version":"v9","file":"awg.apk","bytes":0,"total":4096}\n' >"$PD/state.json"
head -c 1024 /dev/zero >"$PD/.awg.apk.part"
run mirror_list
check "mirror_list при downloading: bytes = размер .part (1024), size = total (4096), state.json не тронут" \
    '[ "$(jq -r ".items[] | select(.app == \"amneziawg\") | .bytes" <<<"$out")" = 1024 ] && [ "$(jq -r ".items[] | select(.app == \"amneziawg\") | .size" <<<"$out")" = 4096 ] && [ "$(jq -r .bytes "$PD/state.json")" = 0 ]' "$out"
rm -rf "$PD"; rmdir "$(dirname "$PD")" 2>/dev/null || true
: >"$FAKECURL_LOG"
run mirror_fetch wgtunnel android
check "та же версия повторно: ready без загрузки файла (curl звали только за релизом)" \
    '[ "$rc" = 0 ] && [ "$(jget .state)" = ready ] && [ "$(curl_calls "dl/")" = 0 ] && grep -qF "$(tf mirror_current wgtunnel android v1.2.3)" "$ERR"' "$(cat "$FAKECURL_LOG") $(errs)"

# ── 4. Отказы проверки подлинности ──────────────────────────────────────────
section "без digest и с неверным sha256 сборка не публикуется"
mkrelease amnezia-vpn/amneziawg-android v3.1.1 AmneziaWG-3.1.1.apk 2048 none >/dev/null
run mirror_fetch amneziawg android
AD=$(pair amneziawg android)
check "релиз без digest → mirror_unverified (rc≠0), файла нет, state failed с этим кодом, curl за файлом не ходил" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = mirror_unverified ] && [ -z "$(find "$AD" -name "*.apk" 2>/dev/null)" ] && [ ! -f "$AD/meta.json" ] && [ "$(jq -r .error "$AD/state.json")" = mirror_unverified ] && [ "$(curl_calls "dl/AmneziaWG-3.1.1.apk")" = 0 ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
mkrelease amnezia-vpn/amneziawg-windows-client v1.0.0 amneziawg-amd64-1.0.0.msi 2048 wrong >/dev/null
run mirror_fetch amneziawg windows
WD2=$(pair amneziawg windows)
check "digest не совпал с файлом → mirror_unverified, .part и файл выброшены" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = mirror_unverified ] && [ -z "$(ls -A "$WD2" | grep -E "\.msi|\.part")" ] && [ ! -f "$WD2/meta.json" ] && [ "$(jq -r .error "$WD2/state.json")" = mirror_unverified ]' "rc=$rc code=$GA_ERR_CODE $(ls -la "$WD2")"
mkrelease amnezia-vpn/amnezia-client v4.8.8.1 AmneziaVPN_4.8.8.1_linux_x64.run 2048 ok 4000 >/dev/null
run mirror_fetch amneziavpn linux
check "размер файла не совпал с заявленным → mirror_fetch_failed, ничего не опубликовано" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = mirror_fetch_failed ] && [ ! -f "$(pair amneziavpn linux)/meta.json" ] && [ -z "$(ls -A "$(pair amneziavpn linux)" | grep -E "\.run|\.part")" ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
mkrelease amnezia-vpn/amnezia-client v4.8.8.1 AmneziaVPN_4.8.8.1_x64.exe 1024 ok >/dev/null
run mirror_fetch amneziavpn android
check "в релизе нет файла по образцу платформы (android11+ arm64) → mirror_fetch_failed с образцом в сообщении" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = mirror_fetch_failed ] && grep -q "android11" "$ERR"' "rc=$rc code=$GA_ERR_CODE $(errs)"

# ── 5. Пределы места ────────────────────────────────────────────────────────
section "пределы: файл, каталог, свободное место"
mkrelease amnezia-vpn/amnezia-client v4.8.8.1 AmneziaVPN_4.8.8.1_macos_x64.pkg $((1536 * 1024)) ok >/dev/null
ga_config_merge '{"mirror": {"max_file_mb": 1}}' >/dev/null 2>&1
run mirror_fetch amneziavpn macos
check "файл 1,5 МБ при mirror.max_file_mb=1 → mirror_too_big, curl за файлом не ходил" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = mirror_too_big ] && [ "$(curl_calls "dl/AmneziaVPN_4.8.8.1_macos_x64.pkg")" = 0 ] && [ "$(jq -r .error "$(pair amneziavpn macos)/state.json")" = mirror_too_big ]' "rc=$rc code=$GA_ERR_CODE"
ga_config_merge '{"mirror": {"max_file_mb": 400, "max_total_mb": 1}}' >/dev/null 2>&1
run mirror_fetch amneziavpn macos
check "итог каталога 4 КБ + 1,5 МБ при max_total_mb=1 → mirror_no_space" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = mirror_no_space ] && [ "$(curl_calls "dl/AmneziaVPN_4.8.8.1_macos_x64.pkg")" = 0 ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
ga_config_merge '{"mirror": {"max_file_mb": 400, "max_total_mb": 3072}}' >/dev/null 2>&1
_orig_free=$(declare -f ga_mirror_free_kb)
ga_mirror_free_kb() { printf '1024'; }
run mirror_fetch amneziavpn macos
check "свободного места меньше размера + 1 ГиБ → mirror_no_space (сказано, сколько есть и сколько нужно)" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = mirror_no_space ] && grep -q "1025" "$ERR"' "rc=$rc code=$GA_ERR_CODE $(errs)"
eval "$_orig_free"
run mirror_fetch amneziavpn macos
check "пределы сняты — та же сборка публикуется, used_bytes = 4096 + 1,5 МБ" \
    '[ "$rc" = 0 ] && [ "$(jget .state)" = ready ] && [ -f "$(pair amneziavpn macos)/AmneziaVPN_4.8.8.1_macos_x64.pkg" ] && [ "$(jq -r .used_bytes <<<"$(mirror_list 2>/dev/null)")" = $((4096 + 1536 * 1024)) ]' "rc=$rc $(errs)"

# ── 6. Замок пары ───────────────────────────────────────────────────────────
section "замок пары"
mkdir -p "$WD/.lock" && printf '%s\n' "$$" >"$WD/.lock/pid"
run mirror_fetch wgtunnel android
check "пара с живым замком → mirror_busy (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = mirror_busy ]' "rc=$rc code=$GA_ERR_CODE"
run mirror_remove wgtunnel android
check "удалить качающуюся пару нельзя → mirror_busy" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = mirror_busy ] && [ -f "$WD/meta.json" ]' "rc=$rc"
run mirror_list
check "mirror_list показывает busy=true у пары под замком" '[ "$(jq -r ".items[] | select(.app == \"wgtunnel\") | .busy" <<<"$out")" = true ]' "$out"
# Номер выше PID_MAX_LIMIT Linux (4194304) и предела macOS (99999): такого процесса не бывает (L1-13).
printf '4194305\n' >"$WD/.lock/pid"
check "замок мёртвого процесса протух и снимается сам" '! ga_mirror_lock_alive "$WD" && [ ! -d "$WD/.lock" ]'

# ── 7. Заявки со страниц и пределы §8 ───────────────────────────────────────
section "mirror_tick: заявки, одна на пару в 10 минут, не больше 6 в час"
jq -nc '{requested: "2026-09-17T00:00:00Z", page: "p-0001"}' >"$WD/request"
run mirror_tick
check "заявка на уже готовую пару снимается, загрузка не стартует: requests=1, started=0" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .requests)" = 1 ] && [ "$(jget .started)" = 0 ] && [ ! -f "$WD/request" ]' "$out"
mkrelease amnezia-vpn/amnezia-client v4.8.8.1 AmneziaVPN_4.8.8.1_android11+_arm64-v8a.apk 3000 ok >/dev/null
rm -rf "$(pair amneziavpn android)"; mkdir -p "$(pair amneziavpn android)"
jq -nc '{requested: "2026-09-17T00:00:00Z", page: "p-0001"}' >"$(pair amneziavpn android)/request"
rm -f "$GOAMNEZIA_MIRROR_DIR/.fetches"
run mirror_tick
check "новая заявка: started=1, пара ready, заявка снята" \
    '[ "$rc" = 0 ] && [ "$(jget .started)" = 1 ] && [ "$(jq -r .state "$(pair amneziavpn android)/state.json")" = ready ] && [ ! -f "$(pair amneziavpn android)/request" ] && [ -f "$(pair amneziavpn android)/AmneziaVPN_4.8.8.1_android11+_arm64-v8a.apk" ]' "$out $(errs)"
# Предел на пару: последняя загрузка была только что, meta нет — заявка ждёт
LD=$(pair amneziavpn linux); mkdir -p "$LD"; rm -f "$LD/meta.json"
date -u +%s >"$LD/.last_fetch"
jq -nc '{requested: "2026-09-17T00:00:00Z", page: "p-0002"}' >"$LD/request"
run mirror_tick
check "заявка на пару, которую качали меньше 10 минут назад, откладывается: deferred=1, state queued (deferred), заявка остаётся" \
    '[ "$(jget .deferred)" = 1 ] && [ "$(jget .started)" = 0 ] && [ "$(jq -r .state "$LD/state.json")" = queued ] && [ "$(jq -r .deferred "$LD/state.json")" = true ] && [ -f "$LD/request" ]' "$out"
rm -f "$LD/request" "$LD/.last_fetch"
# Предел на сервер: шесть стартов за час — седьмая заявка ждёт
now=$(date -u +%s); : >"$GOAMNEZIA_MIRROR_DIR/.fetches"
for _i in 1 2 3 4 5 6; do printf '%s\n' "$((now - _i * 60))" >>"$GOAMNEZIA_MIRROR_DIR/.fetches"; done
mkrelease amnezia-vpn/amneziawg-windows-client v1.0.1 amneziawg-amd64-1.0.1.msi 2048 ok >/dev/null
rm -rf "$WD2"; mkdir -p "$WD2"
jq -nc '{requested: "2026-09-17T00:00:00Z", page: "p-0003"}' >"$WD2/request"
run mirror_tick
check "шесть загрузок за последний час → заявка отложена (deferred=1), файла нет" \
    '[ "$(jget .deferred)" = 1 ] && [ "$(jget .started)" = 0 ] && [ ! -f "$WD2/meta.json" ] && [ -f "$WD2/request" ]' "$out"
: >"$GOAMNEZIA_MIRROR_DIR/.fetches"
run mirror_tick
check "час прошёл (журнал стартов пуст) → та же заявка выполняется: started=1, пара ready" \
    '[ "$(jget .started)" = 1 ] && [ "$(jq -r .state "$WD2/state.json")" = ready ] && [ ! -f "$WD2/request" ]' "$out $(errs)"
check "старты загрузок пишутся в .fetches (для предела в час)" '[ "$(wc -l <"$GOAMNEZIA_MIRROR_DIR/.fetches" | tr -d " ")" -ge 1 ]'

# ── 8. Суточная сверка версий ───────────────────────────────────────────────
section "mirror_tick daily: новая версия подтягивается сама"
run mirror_tick daily
check "версии те же → checked ≥ 1, updates=0" '[ "$rc" = 0 ] && [ "$(jget .checked)" -ge 1 ] && [ "$(jget .updates)" = 0 ]' "$out"
WG_HASH2=$(mkrelease wgtunnel/android v1.3.0 wgtunnel-standalone-v1.3.0.apk 4096 ok)
: >"$GOAMNEZIA_MIRROR_DIR/.fetches"
run mirror_tick daily
check "у wgtunnel/android вышла v1.3.0 → updates=1, meta.json v1.3.0, новый файл на месте, старый удалён" \
    '[ "$rc" = 0 ] && [ "$(jget .updates)" = 1 ] && [ "$(jq -r .version "$WD/meta.json")" = v1.3.0 ] && [ -f "$WD/wgtunnel-standalone-v1.3.0.apk" ] && [ ! -f "$WD/wgtunnel-standalone-v1.2.3.apk" ] && [ "$(jq -r .sha256 "$WD/meta.json")" = "$WG_HASH2" ]' "$out $(ls "$WD")"
check "LICENSE и SOURCE.txt переписаны на новый тег" 'grep -q "v1.3.0" "$WD/LICENSE" && grep -q "tree/v1.3.0" "$WD/SOURCE.txt"'
# Провал сверки у ОПУБЛИКОВАННОЙ пары не прячет готовый файл: state остаётся
# ready, ошибка — отдельным полем; страница и mirror_list видят копию.
mkrelease wgtunnel/android v1.4.0 wgtunnel-standalone-v1.4.0.apk 4096 none >/dev/null
: >"$GOAMNEZIA_MIRROR_DIR/.fetches"
run mirror_tick daily
check "релиз v1.4.0 без digest: updates=1, но копия v1.3.0 на месте, state.json ready + error=mirror_unverified" \
    '[ "$rc" = 0 ] && [ "$(jget .updates)" = 1 ] && [ -f "$WD/wgtunnel-standalone-v1.3.0.apk" ] && [ "$(jq -r .version "$WD/meta.json")" = v1.3.0 ] && [ "$(jq -r .state "$WD/state.json")" = ready ] && [ "$(jq -r .error "$WD/state.json")" = mirror_unverified ]' "$out $(cat "$WD/state.json")"
run mirror_list
check "mirror_list: пара ready (published) с error=mirror_unverified, версия v1.3.0" \
    '[ "$(jq -r ".items[] | select(.app == \"wgtunnel\") | .state" <<<"$out")" = ready ] && [ "$(jq -r ".items[] | select(.app == \"wgtunnel\") | .error" <<<"$out")" = mirror_unverified ] && [ "$(jq -r ".items[] | select(.app == \"wgtunnel\") | .version" <<<"$out")" = v1.3.0 ]' "$out"
mkrelease wgtunnel/android v1.3.0 wgtunnel-standalone-v1.3.0.apk 4096 ok >/dev/null
# Заявка на опубликованную пару при state.json ≠ ready: тик снимает заявку и
# возвращает ready — «в очереди навсегда» быть не должно.
jq -nc '{state:"queued", updated:"2026-09-17T10:00:00Z", requested:"2026-09-17T10:00:00Z"}' >"$WD/state.json"
jq -nc '{requested: "2026-09-17T10:00:00Z", page: "p-0009"}' >"$WD/request"
run mirror_tick
check "заявка на опубликованную пару: снята, state.json снова ready с версией и файлом, загрузка не стартовала" \
    '[ "$rc" = 0 ] && [ ! -f "$WD/request" ] && [ "$(jq -r .state "$WD/state.json")" = ready ] && [ "$(jq -r .file "$WD/state.json")" = wgtunnel-standalone-v1.3.0.apk ] && [ "$(jget .started)" = 0 ]' "$out $(cat "$WD/state.json")"

# ── 8a. Брошенная загрузка ──────────────────────────────────────────────────
# downloading без живого замка (воркер убит, сервер перезагружен) — не вечное
# «загружается 0 %»: failed/stale, заявка принимается и тик её ведёт.
section "брошенная загрузка: downloading без замка → failed, заявка снова принимается"
SD=$(pair amneziavpn linux); rm -rf "$SD"; mkdir -p "$SD"
jq -nc '{state:"downloading", updated:"2026-09-17T10:00:00Z", version:"v9", file:"AmneziaVPN_9_linux_x64.run", bytes:0, total:4096}' >"$SD/state.json"
run mirror_list
check "mirror_list: downloading без замка → state=failed, error=stale (state.json не тронут: чтение)" \
    '[ "$(jq -r ".items[] | select(.app == \"amneziavpn\" and .platform == \"linux\") | .state" <<<"$out")" = failed ] && [ "$(jq -r ".items[] | select(.app == \"amneziavpn\" and .platform == \"linux\") | .error" <<<"$out")" = stale ] && [ "$(jq -r .state "$SD/state.json")" = downloading ]' "$out"
mkdir -p "$SD/.lock"; printf '%s\n' "$$" >"$SD/.lock/pid"
run mirror_list
check "тот же downloading с живым замком — идёт: state=downloading" \
    '[ "$(jq -r ".items[] | select(.app == \"amneziavpn\" and .platform == \"linux\") | .state" <<<"$out")" = downloading ]' "$out"
rm -rf "$SD/.lock"
jq -nc '{state:"queued", updated:"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"}' >"$SD/state.json"
run mirror_list
check "queued без заявки моложе двух минут — загрузка только что поставлена, ещё не stale: state=queued" \
    '[ "$(jq -r ".items[] | select(.app == \"amneziavpn\" and .platform == \"linux\") | .state" <<<"$out")" = queued ]' "$out"
jq -nc '{state:"downloading", updated:"2026-09-17T10:00:00Z", version:"v9", file:"AmneziaVPN_9_linux_x64.run", bytes:0, total:4096}' >"$SD/state.json"
run mirror_tick
check "mirror_tick без заявки: брошенная пара приведена к failed/stale в state.json, сказано вслух" \
    '[ "$rc" = 0 ] && [ "$(jq -r .state "$SD/state.json")" = failed ] && [ "$(jq -r .error "$SD/state.json")" = stale ] && grep -qF "$(tf mirror_warn_stale amneziavpn linux)" "$ERR"' "$(cat "$SD/state.json") $(errs)"
mkrelease amnezia-vpn/amnezia-client v4.8.8.1 AmneziaVPN_4.8.8.1_linux_x64.run 2048 ok >/dev/null
jq -nc '{state:"downloading", updated:"2026-09-17T10:00:00Z", version:"v9", file:"AmneziaVPN_9_linux_x64.run", bytes:0, total:4096}' >"$SD/state.json"
jq -nc '{requested: "2026-09-17T00:00:00Z", page: "p-0010"}' >"$SD/request"
: >"$GOAMNEZIA_MIRROR_DIR/.fetches"; rm -f "$SD/.last_fetch"
run mirror_tick
check "заявка на брошенную пару выполняется тем же тиком: started=1, пара ready" \
    '[ "$rc" = 0 ] && [ "$(jget .started)" = 1 ] && [ "$(jq -r .state "$SD/state.json")" = ready ] && [ -f "$SD/AmneziaVPN_4.8.8.1_linux_x64.run" ]' "$out $(errs)"

# ── 9. Удаление ─────────────────────────────────────────────────────────────
section "mirror_remove"
run mirror_remove wgtunnel android
check "mirror_remove: rc=0, JSON {app, platform}, каталог пары снят, аудит mirror-remove" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .app)" = wgtunnel ] && [ ! -d "$WD" ] && audit_has mirror-remove "app=wgtunnel platform=android"' "$out"
run mirror_remove wgtunnel android
check "повторное удаление — идемпотентно (rc 0)" '[ "$rc" = 0 ]' "rc=$rc"
run mirror_list
check "в списке пары wgtunnel больше нет" '[ -z "$(jq -r ".items[] | select(.app == \"wgtunnel\") | .app" <<<"$out")" ]' "$out"

# ── 10. Мост ────────────────────────────────────────────────────────────────
section "мост install.sh --action=mirror-*, watch-tick, upstream-tick"
act() {
    local action="$1"; shift
    "$BASH5" "$INSTALL" --action="$action" --json "$@" >"$OUT" 2>"$ERR"; rc=$?
    last=$(tail -n 1 "$OUT"); nlines=$(wc -l <"$OUT" | tr -d ' ')
    cat "$ERR" >>"$ALL_ERR"
    return 0
}
lget() { jq -r "$1" <<<"$last" 2>/dev/null; }
: >"$GOAMNEZIA_MIRROR_DIR/.fetches"
act mirror-fetch --app=wgtunnel --platform=android
check "mirror-fetch через мост: одна строка JSON, success, state=ready" \
    '[ "$nlines" = 1 ] && is_json "$last" && [ "$(lget .status)" = success ] && [ "$(lget .state)" = ready ]' "rc=$rc $last $(tail -3 "$ERR")"
act mirror-fetch --app=incy --platform=android
check "mirror-fetch закрытого → error not_mirrorable" '[ "$(lget .status)" = error ] && [ "$(lget .code)" = not_mirrorable ]' "$last"
act mirror-fetch --app=amneziavpn --platform=ios
check "mirror-fetch на ios → bad_arg? нет: платформа в enum, отказ модуля invalid_argument" '[ "$(lget .status)" = error ] && [ "$(lget .code)" = invalid_argument ]' "$last"
act mirror-fetch --app=nosuchapp --platform=android
check "mirror-fetch неизвестного → unknown_app" '[ "$(lget .status)" = error ] && [ "$(lget .code)" = unknown_app ]' "$last"
act mirror-list
check "mirror-list: items с wgtunnel/android ready, used_mb и пределы" \
    '[ "$(lget .status)" = success ] && [ -n "$(lget ".items[] | select(.app == \"wgtunnel\" and .state == \"ready\") | .app")" ] && [ "$(lget .max_total_mb)" = 3072 ]' "$last"
act upstream-tick
check "upstream-tick несёт блок mirror (суточная сверка версий: checked ≥ 1)" \
    '[ "$(lget .status)" = success ] && [ "$(lget ".mirror.checked")" -ge 1 ]' "$last"
act watch-tick
check "watch-tick несёт блоки mirror и pages" '[ "$(lget .status)" = success ] && [ "$(lget ".mirror.requests")" = 0 ] && [ "$(lget ".pages.active")" = 0 ]' "$last"
act mirror-remove --app=wgtunnel --platform=android
check "mirror-remove через мост: success" '[ "$(lget .status)" = success ] && [ "$(lget .app)" = wgtunnel ] && [ ! -d "$WD" ]' "$last"

# ── 11. Запуск файлом (фоновая загрузка) ────────────────────────────────────
section "bash lib/mirror.sh --worker"
"$BASH5" "$ROOT/lib/mirror.sh" >"$OUT" 2>"$ERR"; rc=$?
check "без аргументов — подсказка и rc 2" '[ "$rc" = 2 ] && grep -q -- "--worker" "$ERR"' "rc=$rc $(errs)"
: >"$FAKECURL_LOG"
"$BASH5" "$ROOT/lib/mirror.sh" --worker wgtunnel android >"$OUT" 2>"$ERR"; rc=$?
check "--worker wgtunnel android: rc 0, сборка опубликована заново, stdout пуст" \
    '[ "$rc" = 0 ] && [ -f "$WD/meta.json" ] && [ "$(jq -r .state "$WD/state.json")" = ready ] && [ ! -s "$OUT" ]' "rc=$rc $(errs)"
check "слова о ходе дела — из словаря (mirror_ready)" 'grep -qF "$(tf mirror_ready wgtunnel android v1.3.0)" "$ERR"' "$(errs)"

# ── 11а. Запасной путь без systemd-run не держит замок моста (L3-13 / TAIL-7) ──
# Мост держит config.lock на fd 9 всё действие (install.sh _action_dispatch). Фоновая загрузка через nohup —
# процесс на минуты: унаследуй он fd 9, каждый пишущий вызов бота, панели и агента получал бы lock_timeout, пока
# качается сборка. Не сухой прогон, но рабочий — подставной: он пишет, какие дескрипторы выше 2 унаследовал и
# стоит ли флаг «замок у нас», и выходит — в сеть никто не ходит.
section "L3-13 / TAIL-7: фоновая загрузка без systemd-run — без замка моста (fd 9)"
FAKELIB="$TMP/fakelib"; mkdir -p "$FAKELIB"
cat >"$FAKELIB/mirror.sh" <<'EOS'
#!/bin/bash
# Открытые дескрипторы — через /dev/fd проверкой [ -e ] внутри самой оболочки: пробное перенаправление «>&N»
# врало бы для N ≥ 10 — bash сам сохраняет туда stdout на время перенаправления.
: >"$GA_FAKE_FDS.tmp"
for fd in $(seq 3 30); do
    [ -e "/dev/fd/$fd" ] && printf '%s\n' "$fd" >>"$GA_FAKE_FDS.tmp"
done
[ -n "${GA_CONFIG_LOCK_HELD:-}" ] && printf 'held\n' >>"$GA_FAKE_FDS.tmp"
mv "$GA_FAKE_FDS.tmp" "$GA_FAKE_FDS"
EOS
FDS="$TMP/worker-fds.txt"; rm -f "$FDS"
mkdir -p "$(pair fdtest android)"
: >"$TMP/bridge.lock"
(
    export GOAMNEZIA_DRY_RUN=0 GA_FAKE_FDS="$FDS" GA_CONFIG_LOCK_HELD=1
    systemd-run() { return 1; }          # systemd-run «не смог» — запасной путь через nohup
    _GA_MIRROR_LIB_DIR="$FAKELIB"
    exec 9<"$TMP/bridge.lock"            # как у моста: замок на fd 9 на всё действие
    _mirror_spawn fdtest android
    for _i in $(seq 1 50); do [ -f "$FDS" ] && break; sleep 0.1; done
) >"$OUT" 2>"$ERR"
check "фоновый рабочий запущен запасным путём (nohup) и отработал" '[ -f "$FDS" ] && [ -f "$(pair fdtest android)/worker.pid" ]' "$(cat "$FDS" 2>/dev/null) $(errs)"
check "у рабочего нет ни fd 9 (замок моста), ни других дескрипторов выше 2, ни флага GA_CONFIG_LOCK_HELD" \
    '[ -f "$FDS" ] && [ ! -s "$FDS" ]' "унаследовал: $(tr '\n' ' ' <"$FDS" 2>/dev/null)"
rm -rf "$(pair fdtest android)"

# ── 12. Статические проверки ────────────────────────────────────────────────
section "модуль не ломает статические проверки"
check "bash -n lib/mirror.sh" "\"${BASH:-bash}\" -n '$ROOT/lib/mirror.sh'"
check "паритет ключей словаря ru/en" \
    'diff <(grep -oE "^I18N\[[a-z_]+\]" "$ROOT/lib/lang/ru.d/mirror.sh" | sort) <(grep -oE "^I18N\[[a-z_]+\]" "$ROOT/lib/lang/en.d/mirror.sh" | sort) >/dev/null'
check "каждый ключ mirror_*, запрошенный модулем, есть в обоих словарях" \
    'miss=0; for k in $(grep -oE "\\\$\((t|tf) mirror_[a-z_]+" "$ROOT/lib/mirror.sh" | awk "{print \$2}" | sort -u); do grep -q "^I18N\[$k\]" "$ROOT/lib/lang/ru.d/mirror.sh" || miss=1; grep -q "^I18N\[$k\]" "$ROOT/lib/lang/en.d/mirror.sh" || miss=1; done; [ "$miss" = 0 ]'
check "модуль и словари перечислены в FILES загрузчика" \
    'grep -q "\"lib/mirror.sh\"" "$ROOT/bootstrap.sh" && grep -q "\"lib/lang/ru.d/mirror.sh\"" "$ROOT/bootstrap.sh" && grep -q "\"lib/lang/en.d/mirror.sh\"" "$ROOT/bootstrap.sh"'
check "ни одной видимой строки вне t/tf" \
    '! grep -nE "log_(info|warning|error|success|dim|step) +\"[^\$]" "$ROOT/lib/mirror.sh"' \
    "$(grep -nE "log_(info|warning|error|success|dim|step) +\"[^\$]" "$ROOT/lib/mirror.sh" | head -3)"
# Имена файлов релизов, зафиксированные по GitHub API 17.09.2026 (AmneziaVPN
# 5.0.1.5, amneziawg-android v3.1.20260814, amneziawg-windows-client 3.1.0,
# WG Tunnel 5.7.2) и 05.10.2026 (вкладка «Подписка»: FlClash v0.8.99,
# ClashMetaForAndroid v2.11.35, Clash Verge Rev v2.5.7, Clash Party v2.0.3 —
# все файлы выпусков, с подписями, суммами и прежними именами mihomo-party-…):
# каждый regex каталога обязан совпадать РОВНО с одним из них — иначе зеркало
# берёт первый попавшийся файл или не находит ничего.
assets_ok() {
    local names f="$ROOT/lib/clients.json" app p spec repo pat n
    names='["AmneziaVPN_5.0.1.5_android11+_arm64-v8a.apk","AmneziaVPN_5.0.1.5_android11+_armeabi-v7a.apk","AmneziaVPN_5.0.1.5_android11+_x86.apk","AmneziaVPN_5.0.1.5_android11+_x86_64.apk","AmneziaVPN_5.0.1.5_android9-10_arm64-v8a.apk","AmneziaVPN_5.0.1.5_android9-10_armeabi-v7a.apk","AmneziaVPN_5.0.1.5_android9-10_x86.apk","AmneziaVPN_5.0.1.5_android9-10_x86_64.apk","AmneziaVPN_5.0.1.5_linux_x64.run","AmneziaVPN_5.0.1.5_macos_x64.pkg","AmneziaVPN_5.0.1.5_windows_x64.exe","AmneziaWG-3.1.202060814.apk","amneziawg-amd64-3.1.0.msi","amneziawg-arm64-3.1.0.msi","amneziawg-x86-3.1.0.msi","wgtunnel-fdroid-v5.7.2-arm64.apk","wgtunnel-fdroid-v5.7.2-armv7.apk","wgtunnel-fdroid-v5.7.2.apk","wgtunnel-standalone-v5.7.2-arm64.apk","wgtunnel-standalone-v5.7.2-armv7.apk","wgtunnel-standalone-v5.7.2.apk","FlClash-0.8.99-android-arm64-v8a.apk","FlClash-0.8.99-android-armeabi-v7a.apk","FlClash-0.8.99-android-x86_64.apk","FlClash-0.8.99-linux-amd64.AppImage","FlClash-0.8.99-linux-amd64.deb","FlClash-0.8.99-linux-amd64.rpm","FlClash-0.8.99-linux-arm64.AppImage","FlClash-0.8.99-linux-arm64.deb","FlClash-0.8.99-linux-arm64.rpm","FlClash-0.8.99-macos-amd64.dmg","FlClash-0.8.99-macos-arm64.dmg","FlClash-0.8.99-windows-amd64-setup.exe","FlClash-0.8.99-windows-amd64.zip","FlClash-0.8.99-windows-arm64-setup.exe","FlClash-0.8.99-windows-arm64.zip","SHA256SUMS","cmfa-2.11.35-meta-arm64-v8a-release.apk","cmfa-2.11.35-meta-armeabi-v7a-release.apk","cmfa-2.11.35-meta-universal-release.apk","cmfa-2.11.35-meta-x86-release.apk","cmfa-2.11.35-meta-x86_64-release.apk","output-metadata.json","Clash.Verge-2.5.7-1.aarch64.rpm","Clash.Verge-2.5.7-1.armhfp.rpm","Clash.Verge-2.5.7-1.x86_64.rpm","Clash.Verge-2.5.7-1.x86_64.rpm.sig","Clash.Verge_2.5.7_aarch64.app.tar.gz","Clash.Verge_2.5.7_aarch64.app.tar.gz.sig","Clash.Verge_2.5.7_aarch64.dmg","Clash.Verge_2.5.7_amd64.deb","Clash.Verge_2.5.7_amd64.deb.sig","Clash.Verge_2.5.7_arm64-setup.exe","Clash.Verge_2.5.7_arm64-setup.exe.sig","Clash.Verge_2.5.7_arm64.deb","Clash.Verge_2.5.7_arm64_fixed_webview2-setup.exe","Clash.Verge_2.5.7_arm64_fixed_webview2-setup.exe.sig","Clash.Verge_2.5.7_armhf.deb","Clash.Verge_2.5.7_x64-setup.exe","Clash.Verge_2.5.7_x64-setup.exe.sig","Clash.Verge_2.5.7_x64.app.tar.gz","Clash.Verge_2.5.7_x64.app.tar.gz.sig","Clash.Verge_2.5.7_x64.dmg","Clash.Verge_2.5.7_x64_fixed_webview2-setup.exe","Clash.Verge_2.5.7_x64_fixed_webview2-setup.exe.sig","latest.json","clash-party-catalina-2.0.3-arm64.pkg","clash-party-catalina-2.0.3-arm64.pkg.sha256","clash-party-catalina-2.0.3-x64.pkg","clash-party-catalina-2.0.3-x64.pkg.sha256","clash-party-linux-2.0.3-aarch64.pkg.tar.zst","clash-party-linux-2.0.3-aarch64.pkg.tar.zst.sha256","clash-party-linux-2.0.3-aarch64.rpm","clash-party-linux-2.0.3-aarch64.rpm.sha256","clash-party-linux-2.0.3-amd64.deb","clash-party-linux-2.0.3-amd64.deb.sha256","clash-party-linux-2.0.3-arm64.deb","clash-party-linux-2.0.3-arm64.deb.sha256","clash-party-linux-2.0.3-x64.pkg.tar.zst","clash-party-linux-2.0.3-x64.pkg.tar.zst.sha256","clash-party-linux-2.0.3-x86_64.rpm","clash-party-linux-2.0.3-x86_64.rpm.sha256","clash-party-macos-2.0.3-arm64.pkg","clash-party-macos-2.0.3-arm64.pkg.sha256","clash-party-macos-2.0.3-x64.pkg","clash-party-macos-2.0.3-x64.pkg.sha256","clash-party-win7-2.0.3-ia32-portable.7z","clash-party-win7-2.0.3-ia32-portable.7z.sha256","clash-party-win7-2.0.3-ia32-setup.exe","clash-party-win7-2.0.3-ia32-setup.exe.sha256","clash-party-win7-2.0.3-x64-portable.7z","clash-party-win7-2.0.3-x64-portable.7z.sha256","clash-party-win7-2.0.3-x64-setup.exe","clash-party-win7-2.0.3-x64-setup.exe.sha256","clash-party-windows-2.0.3-arm64-portable.7z","clash-party-windows-2.0.3-arm64-portable.7z.sha256","clash-party-windows-2.0.3-arm64-setup.exe","clash-party-windows-2.0.3-arm64-setup.exe.sha256","clash-party-windows-2.0.3-ia32-portable.7z","clash-party-windows-2.0.3-ia32-portable.7z.sha256","clash-party-windows-2.0.3-ia32-setup.exe","clash-party-windows-2.0.3-ia32-setup.exe.sha256","clash-party-windows-2.0.3-x64-portable.7z","clash-party-windows-2.0.3-x64-portable.7z.sha256","clash-party-windows-2.0.3-x64-setup.exe","clash-party-windows-2.0.3-x64-setup.exe.sha256","latest.yml","mihomo-party-catalina-2.0.3-arm64.pkg","mihomo-party-catalina-2.0.3-arm64.pkg.sha256","mihomo-party-catalina-2.0.3-x64.pkg","mihomo-party-catalina-2.0.3-x64.pkg.sha256","mihomo-party-linux-2.0.3-aarch64.pkg.tar.zst","mihomo-party-linux-2.0.3-aarch64.pkg.tar.zst.sha256","mihomo-party-linux-2.0.3-aarch64.rpm","mihomo-party-linux-2.0.3-aarch64.rpm.sha256","mihomo-party-linux-2.0.3-amd64.deb","mihomo-party-linux-2.0.3-amd64.deb.sha256","mihomo-party-linux-2.0.3-arm64.deb","mihomo-party-linux-2.0.3-arm64.deb.sha256","mihomo-party-linux-2.0.3-x64.pkg.tar.zst","mihomo-party-linux-2.0.3-x64.pkg.tar.zst.sha256","mihomo-party-linux-2.0.3-x86_64.rpm","mihomo-party-linux-2.0.3-x86_64.rpm.sha256","mihomo-party-macos-2.0.3-arm64.pkg","mihomo-party-macos-2.0.3-arm64.pkg.sha256","mihomo-party-macos-2.0.3-x64.pkg","mihomo-party-macos-2.0.3-x64.pkg.sha256","mihomo-party-win7-2.0.3-ia32-portable.7z","mihomo-party-win7-2.0.3-ia32-portable.7z.sha256","mihomo-party-win7-2.0.3-ia32-setup.exe","mihomo-party-win7-2.0.3-ia32-setup.exe.sha256","mihomo-party-win7-2.0.3-x64-portable.7z","mihomo-party-win7-2.0.3-x64-portable.7z.sha256","mihomo-party-win7-2.0.3-x64-setup.exe","mihomo-party-win7-2.0.3-x64-setup.exe.sha256","mihomo-party-windows-2.0.3-arm64-portable.7z","mihomo-party-windows-2.0.3-arm64-portable.7z.sha256","mihomo-party-windows-2.0.3-arm64-setup.exe","mihomo-party-windows-2.0.3-arm64-setup.exe.sha256","mihomo-party-windows-2.0.3-ia32-portable.7z","mihomo-party-windows-2.0.3-ia32-portable.7z.sha256","mihomo-party-windows-2.0.3-ia32-setup.exe","mihomo-party-windows-2.0.3-ia32-setup.exe.sha256","mihomo-party-windows-2.0.3-x64-portable.7z","mihomo-party-windows-2.0.3-x64-portable.7z.sha256","mihomo-party-windows-2.0.3-x64-setup.exe","mihomo-party-windows-2.0.3-x64-setup.exe.sha256"]'
    local pairs=0
    while IFS=$'\t' read -r app p; do
        spec=$(ga_mirror_spec "$app" "$p" 2>/dev/null) || return 1
        pat=$(jq -r '.pattern' <<<"$spec")
        # Тем же движком, что и модуль: jq test (Oniguruma)
        n=$(jq --arg re "$pat" '[.[] | select(test($re))] | length' <<<"$names")
        [ "$n" = 1 ] || { printf '%s/%s: %s совпадений с образцом %s\n' "$app" "$p" "$n" "$pat" >&2; return 1; }
        pairs=$((pairs + 1))
    done < <(jq -r '.apps[] | select(.mirror != null) | .id as $a | .mirror.assets | keys[] | [$a, .] | @tsv' "$f")
    [ "$pairs" = 18 ]
}
check "каждый regex зеркала каталога совпадает ровно с одним настоящим именем файла релиза (18 пар: AmneziaVPN android/windows/macos/linux, AmneziaWG android/windows, WG Tunnel android, FlClash android/windows/macos/linux, Clash Meta android, Clash Verge Rev и Clash Party windows/macos/linux)" \
    'assets_ok' "$(assets_ok 2>&1)"
check "curl всегда с --proto '=https' (ни редиректа на http, ни file://)" \
    '[ "$(grep -c "\-\-proto .=https." "$ROOT/lib/mirror.sh")" -ge 1 ]'

# ── Итог ─────────────────────────────────────────────────────────────────────
DONE=1
printf '\n══ ИТОГ: пройдено %s, провалено %s, пропущено %s\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
