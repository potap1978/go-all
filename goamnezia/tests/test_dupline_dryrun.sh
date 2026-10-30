#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — DUP-LINE-01 (решение владельца 15.10): «Установить» ставит недостающую линию, порт в подписи двух
# одинаковых линий, «нет 2.0» словами и «Переставить на 2.0».
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_dupline_dryrun.sh
# Нужны: bash ≥ 5, jq, python3. Ничего вне $TMP не пишем, к Docker и серверам не ходим (GOAMNEZIA_DRY_RUN=1).
#
# У пользователя на 1.0.11: amnezia-awg2 приложения Amnezia (3.1, порт 46567) подхвачен, «Установить» свободного awg3
# поставило по месту ещё одну 3.1 (порт 6714) — две «AmneziaWG 3.1» в выборе и ни одной 2.0. Здесь тот же сервер из
# фикстуры приложения (tests/fixtures/import: amnezia-awg2 с конфигом 3.1 на 38123, люди приложения без ключей):
#   1  свободный сервер: по месту — awg2 2.0, awg3 3.1 (free_lines); правило общее с Ленивым Pro (ga_lines_need);
#   2  после подхвата 3.1 в awg2: свободный awg3 заранее подписан «AmneziaWG 2.0 (awg3)» (SSH и free_lines в status),
#      profile-install без line ставит 2.0; явная line=3.1 работает (так и вышло у пользователя), 1.x на awg3 — invalid_line;
#   3  две 3.1: подписи с портом («AmneziaWG 3.1 (awg2 · UDP 38123)»), line_shared и title в status; lines_check —
#      no_20, shared, reline {awg3 → 2.0} и текст «старые версии приложений и роутеры Keenetic не подключатся»;
#      то же в brief; сообщения ядра называют профиль с портом;
#   4  «Переставить на 2.0»: профиль приложения — reline_adopted, ничего не тронуто; свой awg3 — 2.0 на прежних
#      порту и подсети, люди с ключом на нём получили новые ключи, люди приложения в awg2 и его контейнер целы,
#      страница подключения осталась; итог по шагам (подписка, страница, ключ); повтор — reline_needless;
#   5  подписка (имена в all.txt и all.yaml уникальны и с портом) — tests/test_pages_server.py, панель — test_panel_dom.py,
#      бот — test_bot_features.py, SSH через pty — test_dupline_pty.py.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-dupline.XXXXXX") || exit 2
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
unset DOCKER_HOST DOCKER_CONTEXT
export GOAMNEZIA_DOCKER_SOCK="$TMP/nosuch.sock"
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$GOAMNEZIA_SECRET_TMPDIR" "$GOAMNEZIA_SYS_MODULE_DIR"
HOSTBIN="$TMP/hostbin"; mkdir -p "$HOSTBIN"
printf '#!/bin/sh\nexit 1\n' >"$HOSTBIN/systemctl"; chmod +x "$HOSTBIN/systemctl"
export PATH="$HOSTBIN:$PATH"
BASH5="${BASH5:-bash}"
INSTALL="$ROOT/install.sh"
FX="$ROOT/tests/fixtures"

pass=0; fail=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 1500; return 0; }
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
# act <действие> [--k=v …] — мост, как его зовут бот, панель и страницы: последняя строка stdout — в out.
act() {
    "$BASH5" "$INSTALL" --action="$1" --json "${@:2}" >"$OUT" 2>"$ERR"; rc=$?
    out=$(tail -n 1 "$OUT")
    return 0
}
jget() { jq -r "$1" <<<"$out" 2>/dev/null; }
cfgv() { jq -r "$1" "$GOAMNEZIA_DIR/config.json" 2>/dev/null; }
# sh_fn '<тело>' — функции install.sh (и всех модулей) без запуска меню: так их зовёт меню SSH.
MENU="$TMP/menu"; mkdir -p "$MENU"
src=$(<"$INSTALL"); printf '%s\n' "${src%main \"\$@\"*}" >"$MENU/install.sh"; ln -s "$ROOT/lib" "$MENU/lib"
sh_fn() {
    # stdin — пустой: меню, которое ждёт ввода (_pick_user_profile), иначе висело бы на терминале прогона.
    NO_COLOR=1 "$BASH5" -c 'source "$1/install.sh" >/dev/null 2>&1; eval "$2"' sh "$MENU" "$1" >"$OUT" 2>"$ERR" </dev/null; rc=$?
    out=$(sed 's/\x1b\[[0-9;]*m//g' "$OUT")
    return 0
}

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
mkdir -p "$UPSTREAM_DIR" "$GOAMNEZIA_DIR/state"
cp -R "$ROOT/lib/upstream/." "$UPSTREAM_DIR/"
chmod 700 "$GOAMNEZIA_DIR/state"
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$GOAMNEZIA_DIR/config.json" <<EOF
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}, "options": {"drop_icmp": false}}
EOF
chmod 600 "$GOAMNEZIA_DIR/config.json"
printf '{"schema": 1, "users": []}\n' >"$GOAMNEZIA_DIR/users.json"; chmod 600 "$GOAMNEZIA_DIR/users.json"
printf '  bash %s, jq %s\n' "$("$BASH5" -c 'echo $BASH_VERSION')" "$(jq --version 2>/dev/null)"

# ── 1. Свободный сервер ──────────────────────────────────────────────────────
section "1: свободный сервер — линии по месту; правило общее с Ленивым Pro"
act status
check "free_lines: awg2 → 2.0, awg3 → 3.1, awg1 → 1.5; lines_check без предупреждений (профилей нет)" \
    '[ "$(jget .free_lines.awg2)" = 2.0 ] && [ "$(jget .free_lines.awg3)" = 3.1 ] && [ "$(jget .free_lines.awg1)" = 1.5 ] && [ "$(jget .lines_check.no_20)" = false ] && [ "$(jget .lines_check.reline)" = null ]' \
    "rc=$rc ${out:0:600}"
sh_fn 'printf "%s|%s|%s\n" "$(_pr_name awg2)" "$(_pr_name awg3)" "$(_pr_name awg1)"'
check "SSH: свободные места подписаны линией, которая встанет, без имени места: «AmneziaWG 2.0 — не установлена»" \
    '[ "$out" = "AmneziaWG 2.0 — не установлена|AmneziaWG 3.1 — не установлена|AmneziaWG 1.5 — не установлена" ]' "$out $(tail -3 "$ERR")"
sh_fn 'declare -A L=([awg2]=3.1) B=(); declare -a A=() S=(); ga_lines_need L B A S; echo "${A[*]}|${S[*]}"; _GM_SLOT_LINE=([awg2]=3.1); _GM_SLOT_BUSY=(); _ga_modes_lines_need; echo "${_GM_ADD[*]}"; type _ga_modes_lines_need | grep -c ga_lines_need'
check "одно правило: ga_lines_need (awg2 держит 3.1 → 2.0 на awg3), режимы зовут его же, а не копию" \
    '[ "$(sed -n 1p <<<"$out")" = "2.0:awg3|" ] && [ "$(sed -n 2p <<<"$out")" = "2.0:awg3" ] && [ "$(sed -n 3p <<<"$out")" = 1 ]' "$out $(tail -3 "$ERR")"

# ── 2. Подхват 3.1 приложения в awg2 и «Установить» awg3 ─────────────────────
section "2: подхвачен amnezia-awg2 приложения с 3.1 — «Установить» awg3 ставит 2.0"
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/cfs"
mkdir -p "$GOAMNEZIA_CONTAINER_FS_DIR"
cp -R "$FX/import/fs/amnezia-awg2" "$GOAMNEZIA_CONTAINER_FS_DIR/"
cp "$FX/import/awg2_conf_line31.txt" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
grep -E '^amnezia-awg2 ' "$FX/import/docker_ps.txt" >"$TMP/docker_ps.txt"
export GOAMNEZIA_DOCKER_PS_FIXTURE="$TMP/docker_ps.txt"
act import-adopt --container=amnezia-awg2
check "amnezia-awg2 подхвачен: awg2 — 3.1 на 38123, imported" \
    '[ "$rc" = 0 ] && [ "$(cfgv .profiles.awg2.line)" = 3.1 ] && [ "$(cfgv .profiles.awg2.port)" = 38123 ] && [ "$(cfgv .profiles.awg2.imported)" = true ]' \
    "rc=$rc ${out:0:500} $(tail -3 "$ERR")"
APP_PEERS=$(jq -c '[.users[] | {name, pub: .peers.awg2.pubkey}]' "$GOAMNEZIA_DIR/users.json")
check "люди приложения в awg2 — без ключей на сервере" \
    '[ "$(jq "[.users[] | select(.peers.awg2 != null and (.peers.awg2.privkey // \"\") == \"\")] | length" "$GOAMNEZIA_DIR/users.json")" -ge 1 ]' "$APP_PEERS"
act status
check "status: free_lines.awg3 = 2.0 — свободное место заранее подписано недостающей линией" \
    '[ "$(jget .free_lines.awg3)" = 2.0 ]' "${out:0:400}"
sh_fn 'printf "%s\n" "$(_pr_name awg3)"'
check "SSH: свободный awg3 — «AmneziaWG 2.0 — не установлена», а не 3.1" '[ "$out" = "AmneziaWG 2.0 — не установлена" ]' "$out"
act status
check "WA-3: «нет 2.0» говорит, где её поставить — install {awg3, 2.0} и «Протоколы» → «AmneziaWG 2.0 — не установлена» → «Установить»" \
    '[ "$(jget .lines_check.no_20)" = true ] && [ "$(jget .lines_check.install.profile)" = awg3 ] && [ "$(jget .lines_check.install.line)" = 2.0 ] && grep -qF "Поставить её: «Протоколы» → «AmneziaWG 2.0 — не установлена» → «Установить»." <<<"$(jget .lines_check.text)"' \
    "$(jget .lines_check)"
printf '1\n' >"$TMP/in1w"
NO_COLOR=1 "$BASH5" -c 'source "$1/install.sh" >/dev/null 2>&1; _pro_line_pick; echo "rc=$? p=$_PL_P"' sh "$MENU" <"$TMP/in1w" >"$OUT" 2>&1
WA1=$(sed 's/\x1b\[[0-9;]*m//g' "$OUT" | tr -s ' \n' ' ')
check "WA-1: «Линия на UDP 443» при одной линии — «Поставить AmneziaWG 2.0 — сразу на UDP 443 — рекомендуем», без «вторую ставит «Протоколы»»; выбор 1 — свободное место" \
    'grep -qF "1) Поставить AmneziaWG 2.0 — сразу на UDP 443 — рекомендуем" <<<"$WA1" && grep -qF "2) AmneziaWG 3.1 · порт 38123 · из приложения" <<<"$WA1" && grep -q "rc=0 p=awg3" <<<"$WA1" && ! grep -qF "вторую ставит" <<<"$WA1"' "$WA1"
act profile-install --profile=awg3 --port=6714
check "profile-install awg3 без line — встала 2.0 (ответ и config.json), порт 6714" \
    '[ "$rc" = 0 ] && [ "$(jget .result.line)" = 2.0 ] && [ "$(cfgv .profiles.awg3.line)" = 2.0 ] && [ "$(cfgv .profiles.awg3.port)" = 6714 ]' \
    "rc=$rc ${out:0:500} $(tail -3 "$ERR")"
act status
check "две линии — 2.0 и 3.1: no_20 нет, reline нет, повторов нет, подписи без порта" \
    '[ "$(jget .lines_check.no_20)" = false ] && [ "$(jget .lines_check.reline)" = null ] && [ "$(jget ".lines_check.shared | length")" = 0 ] && [ "$(jget ".profiles[] | select(.profile == \"awg3\") | .title")" = "AmneziaWG 2.0 · порт 6714" ]' \
    "${out:0:800}"
act profile-remove --profile=awg3
act profile-install --profile=awg3 --port=6714 --line=1.5
check "line=1.5 на awg3 — invalid_line, ничего не поставлено" \
    '[ "$rc" != 0 ] && [ "$(jget .code)" = invalid_line ] && [ "$(cfgv ".profiles.awg3 // empty")" = "" ]' "rc=$rc ${out:0:300}"
act profile-install --profile=awg3 --port=6714 --line=3.1
check "явная line=3.1 работает (так на сервере пользователя): awg3 — 3.1" \
    '[ "$rc" = 0 ] && [ "$(jget .result.line)" = 3.1 ] && [ "$(cfgv .profiles.awg3.line)" = 3.1 ]' "rc=$rc ${out:0:300} $(tail -3 "$ERR")"

# ── 3. Две 3.1: подписи с портом и предупреждение ────────────────────────────
section "3: две 3.1 — порт в подписи; «нет 2.0» и «Переставить на 2.0» в status и brief"
act status
check "status.profiles: line_shared и title с портом у обоих; imported у awg2" \
    '[ "$(jget ".profiles[] | select(.profile == \"awg2\") | .title")" = "AmneziaWG 3.1 · порт 38123 · из приложения" ] && [ "$(jget ".profiles[] | select(.profile == \"awg3\") | .title")" = "AmneziaWG 3.1 · порт 6714" ] && [ "$(jget "[.profiles[] | .line_shared] | all")" = true ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .imported")" = true ] && [ "$(jget ".profiles[] | select(.profile == \"awg3\") | .imported")" = false ]' \
    "$(jget '.profiles')"
check "lines_check: no_20, shared 3.1 [awg2, awg3], missing [2.0], reline {awg3, 3.1 → 2.0, 6714}" \
    '[ "$(jget .lines_check.no_20)" = true ] && [ "$(jget ".lines_check.shared[0].line")" = 3.1 ] && [ "$(jget ".lines_check.shared[0].profiles | join(\",\")")" = awg2,awg3 ] && [ "$(jget ".lines_check.missing | join(\",\")")" = 2.0 ] && [ "$(jget .lines_check.reline.profile)" = awg3 ] && [ "$(jget .lines_check.reline.from)" = 3.1 ] && [ "$(jget .lines_check.reline.line)" = 2.0 ] && [ "$(jget .lines_check.reline.port)" = 6714 ] && [ "$(jget .lines_check.reline.title)" = "AmneziaWG 3.1 · порт 6714" ]' \
    "$(jget '.lines_check')"
check "текст: «старые версии приложений … и роутеры Keenetic не подключатся» и предложение переставить awg3 на 2.0" \
    'grep -qF "старые версии приложений" <<<"$(jget .lines_check.text)" && grep -qF "роутеры Keenetic на обычной прошивке не подключатся ни к одному профилю (3.1 Keenetic понимает пока только в dev-прошивке)" <<<"$(jget .lines_check.text)" && grep -qF "AmneziaWG 3.1 · порт 6714 повторяет линию другого профиля — его можно переставить на AmneziaWG 2.0" <<<"$(jget .lines_check.text)"' \
    "$(jget .lines_check.text)"
act status --brief=on
check "brief (Обзор панели, главный экран бота): lines_check и free_lines тоже есть" \
    '[ "$(jget .lines_check.no_20)" = true ] && [ "$(jget .lines_check.reline.profile)" = awg3 ] && [ "$(jget ".free_lines | has(\"awg1\")")" = true ]' "${out:0:400}"
sh_fn 'printf "%s|%s|%s\n" "$(_pr_name awg2)" "$(_pr_name awg3)" "$(_pr_name awg3 noport)"; ga_amn_profile_label awg3'
check "SSH и сообщения ядра без имени места: «AmneziaWG 3.1 · порт 38123 · из приложения», «AmneziaWG 3.1 · порт 6714»; noport — где порт рядом колонкой" \
    '[ "$(sed -n 1p <<<"$out")" = "AmneziaWG 3.1 · порт 38123 · из приложения|AmneziaWG 3.1 · порт 6714|AmneziaWG 3.1" ] && [ "$(sed -n 2p <<<"$out")" = "AmneziaWG 3.1 · порт 6714" ]' "$out"
act status
check "до миграции флага нет: dupline_fix = null (обычное предупреждение lines_check — есть)" '[ "$(jget .dupline_fix)" = null ]' "$(jget .dupline_fix)"
sh_fn 'type _ga_migrate_steps | grep -c _migrate_dupline; type _ga_migrate_reapply | grep -c _migrate_dupline; ga_amn_dupline_mark; echo "--"; ga_amn_dupline_mark'
check "миграция: шаг _migrate_dupline в обеих ветках; флаг ставится один раз (второй вызов молчит)" \
    '[ "$(sed -n 1p <<<"$out")" = 1 ] && [ "$(sed -n 2p <<<"$out")" = 1 ] && [ "$(sed -n 3p <<<"$out")" = awg3 ] && [ "$(sed -n 4p <<<"$out")" = "--" ] && [ -z "$(sed -n 5p <<<"$out")" ] && [ "$(cfgv .dupline_bug.profile)" = awg3 ]' "$out"
check "миграция ничего не переставила: awg3 — по-прежнему 3.1" '[ "$(cfgv .profiles.awg3.line)" = 3.1 ]'
check "одно событие dupline_bug для бота: профиль, линии, порт, текст" \
    '[ "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | jq -s "[.[] | select(.event == \"dupline_bug\")] | length")" = 1 ] && cat "$GOAMNEZIA_NOTIFY_DIR"/*.json | jq -se "[.[] | select(.event == \"dupline_bug\")][0] | .profile == \"awg3\" and .from == \"3.1\" and .line == \"2.0\" and .port == 6714 and (.text | test(\"На сервере нет AmneziaWG 2.0\"))"' \
    "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | tail -c 600)"

# ── 4. Переставить на 2.0 ────────────────────────────────────────────────────
section "4: «Переставить на 2.0» — только свой профиль; пользователи получают ключи; итог по шагам"
for _u in anna boris vera; do act user-add --name="$_u" --profiles=awg3; done
check "три человека с ключом на awg3" '[ "$(jq "[.users[] | select(.peers.awg3 != null)] | length" "$GOAMNEZIA_DIR/users.json")" = 3 ]' "$(tail -3 "$ERR")"
# Страница подключения boris и подписка vera (забирали) — как их кладут модули.
PG="$GOAMNEZIA_DIR/pages"; SB="$GOAMNEZIA_DIR/subs"
PGDIR=$(sh_fn 'ga_pages_dir'; printf '%s' "$out"); SBDIR=$(sh_fn 'ga_subs_dir'; printf '%s' "$out")
[ -n "$PGDIR" ] && PG="$PGDIR"; [ -n "$SBDIR" ] && SB="$SBDIR"
mkdir -p "$PG/p-1a2b3c" "$SB/vera"
printf '{"id":"p-1a2b3c","user":"boris","profiles":["awg3"],"expires_epoch":0,"forever":true}\n' >"$PG/p-1a2b3c/meta.json"
printf '%s\n' "$(printf 'x%.0s' $(seq 1 43))" >"$PG/p-1a2b3c/secret"
printf '{"user":"vera","created":"2026-10-01T10:00:00Z","generation":1}\n' >"$SB/vera/meta.json"
printf '%s\n' "$(printf 'y%.0s' $(seq 1 43))" >"$SB/vera/secret"
printf '{"fetches": 3, "auto_fetches": 3, "last_fetch": "2026-10-07T10:00:00Z", "last_suffix": "all.yaml", "last_client": "clash-verge"}\n' >"$SB/vera/state.json"
act status --brief=on
check "status (и brief): dupline_fix — профиль, 3.1 → 2.0, порт, подпись, пользователей с ключом 3, текст владельца" \
    '[ "$(jget .dupline_fix.profile)" = awg3 ] && [ "$(jget .dupline_fix.from)" = 3.1 ] && [ "$(jget .dupline_fix.line)" = 2.0 ] && [ "$(jget .dupline_fix.port)" = 6714 ] && [ "$(jget .dupline_fix.users)" = 3 ] && [ "$(jget .dupline_fix.title)" = "AmneziaWG 3.1 · порт 6714" ] && [ -n "$(jget .dupline_fix.detected_at)" ] && grep -qF "На сервере нет AmneziaWG 2.0: старые версии приложений (AmneziaVPN до 5.0.1.5, DefaultVPN) не подключатся к 3.1, а роутеры Keenetic на обычной прошивке — только к AmneziaWG 1.5 или 1.0, если она стоит (3.1 Keenetic понимает пока только в dev-прошивке). Исправление переставит AmneziaWG 3.1 · порт 6714 на 2.0: порт и подсеть останутся прежними. Пользователям на нём (3) goAmnezia pro сразу выдаст новые ключи. Подписка подтянет их сама только там, где приложение само её обновляет; страницу подключения нужно открыть заново и импортировать новый ключ, а тем, кому ключ или файл отправляли вручную, — заново отсканировать QR-код или импортировать ключ: старый перестанет работать. Профиль приложения не тронем." <<<"$(jget .dupline_fix.text)"' \
    "$(jget .dupline_fix)"
check "N-3: краткий status (Обзор раз в 10 с) кому что делать не считает — steps нет ни у dupline_fix, ни у lines_check.reline" \
    '[ "$(jget ".dupline_fix | has(\"steps\")")" = false ] && [ "$(jget ".lines_check.reline | has(\"steps\")")" = false ]' \
    "$(jget '{d: .dupline_fix, l: .lines_check.reline}')"
act status
check "status: у dupline_fix и lines_check.reline — steps до перестановки: подписка (vera), страница (boris), ключ (anna)" \
    '[ "$(jget "[.dupline_fix.steps[].kind] | join(\",\")")" = "subscription,page,key" ] && grep -qF "С подпиской (vera): обновится сама (до 12 ч, у FlClash — до 24 ч) или по «Обновить» в приложении." <<<"$(jget ".dupline_fix.steps[0].text")" && grep -qF "Со страницей подключения (boris)" <<<"$(jget ".dupline_fix.steps[1].text")" && grep -qF "Выдавали ключ или файл (anna)" <<<"$(jget ".dupline_fix.steps[2].text")" && [ "$(jget "[.lines_check.reline.steps[].kind] | join(\",\")")" = "subscription,page,key" ]' \
    "$(jget '{d: .dupline_fix.steps, l: .lines_check.reline.steps}')"
CNT="$TMP/reline_preview_calls"; : >"$CNT"
sh_fn 'eval "$(declare -f ga_amn_s4_audience | sed "1s/ga_amn_s4_audience/_orig_aud/")"; ga_amn_s4_audience() { echo x >>"'"$CNT"'"; _orig_aud "$@"; }; ARG_brief=on _action_dispatch_locked status >/dev/null 2>&1; echo "brief=$(wc -l <"'"$CNT"'" | tr -d " ")"; ARG_brief= _action_dispatch_locked status >/dev/null 2>&1; echo "full=$(wc -l <"'"$CNT"'" | tr -d " ")"'
check "N-3: список «кому что делать» — один расчёт на оба поля в полном status, ни одного в кратком" \
    '[ "$(grep -c "^brief=0$" <<<"$out")" = 1 ] && [ "$(grep -c "^full=1$" <<<"$out")" = 1 ]' "$out $(tail -3 "$ERR")"
sh_fn '_profile_reline_interactive awg3 2.0 2>&1'
WHO=$(tr -s ' \n' ' ' <<<"$out")
check "P-5: окно согласия SSH — «Кому что сделать после:» и у каждого пользователя его действие; согласие не набрано — отмена" \
    'grep -qF "Кому что сделать после: • С подпиской (vera)" <<<"$WHO" && ! grep -qF "скажу по итогу" <<<"$WHO" && [ "$(cfgv .profiles.awg3.line)" = 3.1 ]' "$WHO"
sh_fn 'ga_amn_reline_preview() { printf "[]"; }; _profile_reline_interactive awg3 2.0 2>&1'
WHO=$(tr -s ' \n' ' ' <<<"$out")
check "P-5: списка нет (пользователей не прочитать) — честная фраза «скажу по итогу перестановки», а не двоеточие в пустоту" \
    'grep -qF "Кому что сделать после, скажу по итогу перестановки." <<<"$WHO" && ! grep -qF "после:" <<<"$WHO"' "$WHO"
OLD31=$(jq -c '[.users[] | select(.peers.awg3 != null) | {name, pub: .peers.awg3.pubkey}]' "$GOAMNEZIA_DIR/users.json")
cp "$GOAMNEZIA_DIR/config.json" "$TMP/cfg.before"
act profile-reline --profile=awg2
check "профиль приложения (awg2) — отказ reline_adopted словами, ничего не тронуто" \
    '[ "$rc" != 0 ] && [ "$(jget .code)" = reline_adopted ] && grep -qF "подхвачен у приложения Amnezia — переставлять его нельзя" <<<"$(jget .message)" && cmp -s "$TMP/cfg.before" "$GOAMNEZIA_DIR/config.json"' \
    "rc=$rc ${out:0:400}"
act profile-reline --profile=awg3 --line=3.1
check "line не та (3.1 на 3.1) — invalid_line, ничего не тронуто" \
    '[ "$rc" != 0 ] && [ "$(jget .code)" = invalid_line ] && cmp -s "$TMP/cfg.before" "$GOAMNEZIA_DIR/config.json"' "rc=$rc ${out:0:300}"
SUB3=$(cfgv .profiles.awg3.subnet)
act profile-reline --profile=awg3
RL="$out"
check "awg3 переставлен: success, 3.1 → 2.0, порт 6714 и подсеть прежние" \
    '[ "$rc" = 0 ] && [ "$(jget .status)" = success ] && [ "$(jget .line_old)" = 3.1 ] && [ "$(jget .line)" = 2.0 ] && [ "$(cfgv .profiles.awg3.line)" = 2.0 ] && [ "$(cfgv .profiles.awg3.port)" = 6714 ] && [ "$(cfgv .profiles.awg3.subnet)" = "$SUB3" ]' \
    "rc=$rc ${out:0:600} $(tail -5 "$ERR")"
check "профиль приложения цел: awg2 — 3.1 на 38123, imported, пиры людей приложения те же; контейнер не снимался" \
    '[ "$(cfgv .profiles.awg2.line)" = 3.1 ] && [ "$(cfgv .profiles.awg2.port)" = 38123 ] && [ "$(cfgv .profiles.awg2.imported)" = true ] && [ "$(jq -c "[.users[] | select(.peers.awg2 != null) | {name, pub: .peers.awg2.pubkey}]" "$GOAMNEZIA_DIR/users.json")" = "$(jq -c "[.[] | select(.pub != null)]" <<<"$APP_PEERS")" ] && ! grep -q "docker rm.*amnezia-awg2\b" "$ERR"' \
    "$(cfgv .profiles.awg2 | head -c 300)"
NEW31=$(jq -c '[.users[] | select(.peers.awg3 != null) | {name, pub: .peers.awg3.pubkey}]' "$GOAMNEZIA_DIR/users.json")
check "люди получили ключи на новом профиле: anna, boris, vera — новые открытые ключи, приватные на сервере" \
    '[ "$(jq -r "[.[].name] | sort | join(\",\")" <<<"$NEW31")" = "anna,boris,vera" ] && [ "$(jq -r "[.[].pub] | sort | join(\",\")" <<<"$NEW31")" != "$(jq -r "[.[].pub] | sort | join(\",\")" <<<"$OLD31")" ] && [ "$(jq "[.users[] | select(.peers.awg3 != null and (.peers.awg3.privkey // \"\") != \"\")] | length" "$GOAMNEZIA_DIR/users.json")" = 3 ] && [ "$(jget ".users | sort | join(\",\")")" = "anna,boris,vera" ]' \
    "old=$OLD31 new=$NEW31 users=$(jget .users)"
check "DUPLINE-KEYS-01: без --keys-to-others — как прежде: полей others нет, пользователям приложения ключ на awg3 не выдан" \
    '[ "$(jget "has(\"others\") or has(\"others_failed\")")" = false ] && [ "$(jq "[.users[] | select(.peers.awg3 != null)] | length" "$GOAMNEZIA_DIR/users.json")" = 3 ] && ! jq -e "[.steps[].kind] | index(\"others\")" <<<"$out" >/dev/null' \
    "$(jget '{others, steps: [.steps[].kind]}')"
check "страница подключения boris осталась (при перестановке не снимается)" '[ -f "$PG/p-1a2b3c/meta.json" ]'
check "итог по шагам: ключи выданы; подписка (vera) обновится сама; страница (boris) — открыть заново; ключ (anna) — выдать заново" \
    '[ "$(jget "[.steps[].kind] | join(\",\")")" = "users,subscription,page,key" ] && [ "$(jget ".subscription | join(\",\")")" = vera ] && [ "$(jget ".page | join(\",\")")" = boris ] && [ "$(jget ".key | join(\",\")")" = anna ] && grep -qF "обновится сама" <<<"$(jget ".steps[1].text")" && grep -qF "открыть страницу заново" <<<"$(jget ".steps[2].text")" && grep -qF "выдайте ключ vpn:// или файл .conf заново" <<<"$(jget ".steps[3].text")"' \
    "$(jget '.steps')"
check "message — итог словами: «AmneziaWG 3.1 · порт 6714 переставлен на AmneziaWG 2.0: порт 6714 …»" \
    'grep -qF "переставлен на AmneziaWG 2.0: порт 6714" <<<"$(jget .message)"' "$(jget .message)"
check "журнал аудита: profile-reline awg3 3.1->2.0" 'grep -q "profile-reline.*awg3 3.1->2.0" "$GOAMNEZIA_AUDIT_LOG"' "$(tail -3 "$GOAMNEZIA_AUDIT_LOG")"
act status
check "после перестановки: 2.0 и 3.1, предупреждений нет, сообщение об ошибке снято (dupline_fix null, fixed_at)" \
    '[ "$(jget .lines_check.no_20)" = false ] && [ "$(jget .lines_check.reline)" = null ] && [ "$(jget .lines_check.text)" = "" ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .title")" = "AmneziaWG 3.1 · порт 38123 · из приложения" ] && [ "$(jget .dupline_fix)" = null ] && [ -n "$(cfgv .dupline_bug.fixed_at)" ]' \
    "$(jget .lines_check)"
act profile-reline --profile=awg3
check "повтор — reline_needless" '[ "$rc" != 0 ] && [ "$(jget .code)" = reline_needless ]' "rc=$rc ${out:0:300}"
# R-1 (проверка 15.10): «обновится сама» — только подписка, которую забирало приложение всеми линиями сразу (all.*).
# Просмотр в браузере (и по короткой ссылке), curl и ключ одной линии awgNN.* (после смены линии — 404) — не она.
mkdir -p "$SB/boris" "$SB/anna"
printf '{"user":"boris","created":"2026-10-01T10:00:00Z","generation":1}\n' >"$SB/boris/meta.json"
printf '%s\n' "$(printf 'b%.0s' $(seq 1 43))" >"$SB/boris/secret"
printf '{"fetches": 0}\n' >"$SB/boris/state.json"
printf '{"user":"anna","created":"2026-10-01T10:00:00Z","generation":1}\n' >"$SB/anna/meta.json"
printf '%s\n' "$(printf 'a%.0s' $(seq 1 43))" >"$SB/anna/secret"
printf '{"fetches": 4, "last_suffix": "awg31.vpn", "last_client": "incy"}\n' >"$SB/anna/state.json"
printf '{"fetches": 1, "auto_fetches": 0, "last_suffix": "all.txt", "last_client": "browser"}\n' >"$SB/vera/state.json"
sh_fn 'ga_amn_s4_audience awg3'
check "R-1: страница и подписка без заборов (boris) — «страницу открыть заново»; один просмотр в браузере (vera) и ключ одной линии awg31.vpn (anna) — не «обновится сама»" \
    '[ "$(jq -r ".subscription | join(\",\")" <<<"$out")" = "" ] && [ "$(jq -r ".page | join(\",\")" <<<"$out")" = boris ] && [ "$(jq -r ".key | join(\",\")" <<<"$out")" = "anna,vera" ]' "$out $(tail -3 "$ERR")"
printf '{"fetches": 2, "last_suffix": "all.yaml", "last_client": "clash-verge"}\n' >"$SB/vera/state.json"
printf '{"fetches": 9, "last_suffix": "all.txt", "last_client": "curl"}\n' >"$SB/boris/state.json"
sh_fn 'ga_amn_s4_audience awg3'
check "R-1: state.json прежней службы (без auto_fetches) — по последнему забору: all.yaml приложением (vera) — подписка; all.txt через curl (boris) — нет" \
    '[ "$(jq -r ".subscription | join(\",\")" <<<"$out")" = vera ] && [ "$(jq -r ".page | join(\",\")" <<<"$out")" = boris ]' "$out $(tail -3 "$ERR")"
printf '{"fetches": 5, "last_suffix": "all.vpn", "last_client": "other"}\n' >"$SB/vera/state.json"
sh_fn 'ga_amn_s4_audience awg3'
check "N-1: all.vpn — разовый импорт пула продуктом серии (Python-urllib → other), сам не обновляется: vera — не «обновится сама»" \
    '[ "$(jq -r ".subscription | join(\",\")" <<<"$out")" = "" ] && jq -e ".key | index(\"vera\")" <<<"$out" >/dev/null' "$out $(tail -3 "$ERR")"
rm -rf "$SB/boris" "$SB/anna"
printf '{"fetches": 3, "auto_fetches": 3, "last_suffix": "all.yaml", "last_client": "clash-verge"}\n' >"$SB/vera/state.json"
out="$RL"

# ── 5. Советы без 2.0, список людей, KEY-APP-01/02 ───────────────────────────
section "5: две 3.1 снова — советы не ведут в 2.0, которой нет; список людей линиями; ошибка ключа приложения — один раз"
act profile-remove --profile=awg3
act profile-install --profile=awg3 --port=6714 --line=3.1
act user-add-peer --name=anna --profile=awg3
act user-add-peer --name=anna --profile=awg2
check "anna — ключи на обеих 3.1 (awg3 своя, awg2 приложения)" \
    '[ "$(jq -r ".users[] | select(.name == \"anna\") | .peers | keys | sort | join(\",\")" "$GOAMNEZIA_DIR/users.json")" = awg2,awg3 ]' "$(tail -3 "$ERR")"
sh_fn '_pick_user_profile anna'
printf '0\n' >"$TMP/in0"
NO_COLOR=1 "$BASH5" -c 'source "$1/install.sh" >/dev/null 2>&1; _pick_user_profile anna; echo "rc=$?"' sh "$MENU" <"$TMP/in0" >"$OUT" 2>"$ERR"
PICK=$(sed 's/\x1b\[[0-9;]*m//g' "$ERR" | tr -s ' \n' ' ')
check "выбор ключа: две 3.1 с портом; совет «2.0 на сервере нет», а не «возьмите 2.0» (R3, R4)" \
    'grep -qF "AmneziaWG 3.1 · порт 6714" <<<"$PICK" && grep -qF "AmneziaWG 3.1 · порт 38123 · из приложения" <<<"$PICK" && grep -qF "AmneziaWG 2.0 на сервере нет" <<<"$PICK" && ! grep -qF "возьмите 2.0" <<<"$PICK" && grep -qF "«Переставить на 2.0»" <<<"$PICK"' \
    "$PICK"
NO_COLOR=1 "$BASH5" -c 'source "$1/install.sh" >/dev/null 2>&1; _pro_line_pick; echo "rc=$? p=$_PL_P"' sh "$MENU" <"$TMP/in0" >"$OUT" 2>&1
PL=$(sed 's/\x1b\[[0-9;]*m//g' "$OUT" | tr -s ' \n' ' ')
check "«Линия на UDP 443»: две 3.1 с портом; «AmneziaWG 2.0 на сервере нет …», а не «Рекомендуем 2.0» (R3, R4)" \
    'grep -qF "AmneziaWG 3.1 · порт 38123 · из приложения" <<<"$PL" && grep -qF "AmneziaWG 3.1 · порт 6714" <<<"$PL" && grep -qF "AmneziaWG 2.0 на сервере нет" <<<"$PL" && ! grep -qF "Рекомендуем 2.0" <<<"$PL"' "$PL"
sh_fn 'COLUMNS=120 _users_render_list 1'
check "список людей: колонка профилей — линиями с портом («3.1:6714 3.1:38123»), без голых awgN" \
    'grep -qE "anna +.*3\.1:6714 3\.1:38123" <<<"$out" && ! grep -qE "anna .*awg[123]" <<<"$out"' "$out"
# D-2 (проверка 15.10): «3.1:6714 3.1:38123» шире прежних 14 знаков — колонка «Трафик» у всех строк начинается там же,
# где её заголовок (раньше у anna съезжала вправо).
cat >"$TMP/cols.py" <<'PY'
import re, sys
lines = sys.stdin.read().split("\n")
head = [l for l in lines if "Трафик" in l][0]
at = head.index("Трафик")
rows = [l for l in lines if re.match(r"^\s+\d+\) ", l)]
bad = [l for l in rows if not (l[at - 1] == " " and re.match(r"\d+(,\d)? \S+", l[at:]))]
print("rows=%d bad=%d" % (len(rows), len(bad)))
for l in bad:
    print(repr(l))
PY
COLS=$(python3 "$TMP/cols.py" <<<"$out")
check "D-2: колонка «Трафик» выровнена у всех строк, и у anna с «3.1:6714 3.1:38123»" \
    'grep -q "bad=0" <<<"$COLS" && ! grep -q "rows=0" <<<"$COLS"' "$COLS"
APPU=$(jq -r '[.users[] | select(.peers.awg2 != null and (.peers.awg2.privkey // "") == "" and (.peers | length) == 1)][0].name' "$GOAMNEZIA_DIR/users.json")
printf '1\n' >"$TMP/in1"
NO_COLOR=1 "$BASH5" -c 'source "$1/install.sh" >/dev/null 2>&1; _user_share "$2" "" awg2; _user_key "$2" conf "" awg2; _user_key "$2" vpn "" awg2' sh "$MENU" "$APPU" <"$TMP/in1" >"$OUT" 2>"$ERR"
check "KEY-APP-01: «Ключ … выдан в приложении Amnezia» — ровно один раз на каждый отказ (ссылка, .conf, vpn://), а не дважды" \
    '[ "$(grep -c "выдан в приложении Amnezia и хранится только там" "$ERR")" = 3 ]' "$APPU: $(grep -c "выдан в приложении" "$ERR") $(tail -6 "$ERR")"
NO_COLOR=1 "$BASH5" -c 'source "$1/install.sh" >/dev/null 2>&1; _pick_user_profile "$2" keyed; echo "rc=$?"' sh "$MENU" "$APPU" </dev/null >"$OUT" 2>"$ERR"
check "KEY-APP-02: у человека только ключи приложения — выбор для выдачи ключа отвечает rc 3 (нечего выдать)" 'grep -q "rc=3" "$OUT"' "$(cat "$OUT") $(tail -3 "$ERR")"
jq '(.users[] | select(.name == "anna") | .peers.awg2) |= (.privkey = "" | .imported = true)' "$GOAMNEZIA_DIR/users.json" >"$TMP/u" \
    && mv "$TMP/u" "$GOAMNEZIA_DIR/users.json" && chmod 600 "$GOAMNEZIA_DIR/users.json"
NO_COLOR=1 "$BASH5" -c 'source "$1/install.sh" >/dev/null 2>&1; p=$(_pick_user_profile anna keyed); echo "rc=$? p=$p"' sh "$MENU" </dev/null >"$OUT" 2>"$ERR"
check "KEY-APP-02 смешанно: awg2 (ключ приложения) в выбор не идёт — выбран awg3, строкой сказано, что убрано и почему" \
    'grep -q "rc=0 p=awg3" "$OUT" && grep -qF "Нет в списке — ключ выдан в приложении Amnezia: AmneziaWG 3.1 · порт 38123 · из приложения" <<<"$(tr -s " \n" " " <"$ERR")"' \
    "$(cat "$OUT") $(tail -3 "$ERR")"

# WA-2: ключи Ленивого Pro прошлого захода получают и линию, поставленную этим заходом («у каждого обе линии»)
jq '.lazy = {keys: ["vera"]}' "$GOAMNEZIA_DIR/config.json" >"$TMP/c" && mv "$TMP/c" "$GOAMNEZIA_DIR/config.json" && chmod 600 "$GOAMNEZIA_DIR/config.json"
jq '(.users[] | select(.name == "vera") | .peers) |= del(.awg2)' "$GOAMNEZIA_DIR/users.json" >"$TMP/u" \
    && mv "$TMP/u" "$GOAMNEZIA_DIR/users.json" && chmod 600 "$GOAMNEZIA_DIR/users.json"
sh_fn '_GM_ADD=(); _ga_modes_keys 1; echo "rc=$?"'
check "WA-2: линию, которую этот заход не ставил, ключу прошлого захода не возвращаем (пир снят админом — так и остаётся)" \
    'grep -q "rc=0" <<<"$out" && [ "$(jq -r ".users[] | select(.name == \"vera\") | .peers | has(\"awg2\")" "$GOAMNEZIA_DIR/users.json")" = false ]' "$out $(tail -3 "$ERR")"
sh_fn '_GM_ADD=("3.1:awg2"); _ga_modes_keys 1; echo "rc=$?"'
check "WA-2: ключ прошлого захода Ленивого Pro (vera) получил ключ на линии, поставленной этим заходом — awg2" \
    'grep -q "rc=0" <<<"$out" && [ "$(jq -r ".users[] | select(.name == \"vera\") | .peers | has(\"awg2\")" "$GOAMNEZIA_DIR/users.json")" = true ]' "$out $(tail -3 "$ERR")"

# Сбой перестановки (находка проверки 15.10): пользователи не остаются без ключей, слова — честные
OLDKEY=$(jq -r '.users[] | select(.name == "anna") | .peers.awg3.pubkey' "$GOAMNEZIA_DIR/users.json")
# Флаг «Ошибка прошлых версий» снимается только когда 2.0 правда встала (в т.ч. не поднявшейся): на время проверок
# сбоев — «не исправлено», и каждая ветка сверяет его.
jq '.dupline_bug.fixed_at = ""' "$GOAMNEZIA_DIR/config.json" >"$TMP/c" && mv "$TMP/c" "$GOAMNEZIA_DIR/config.json" && chmod 600 "$GOAMNEZIA_DIR/config.json"
sh_fn 'ga_amn_validate_params() { [ "$1" != 2.0 ]; }; amnezia_profile_reline awg3 >/dev/null; echo "rc=$? code=$GA_ERR_CODE"'
check "параметры новой линии не проходят проверку — отказ params_failed ДО удаления: awg3 и ключ anna не тронуты" \
    'grep -q "rc=1 code=params_failed" <<<"$out" && [ "$(cfgv .profiles.awg3.line)" = 3.1 ] && [ "$(jq -r ".users[] | select(.name == \"anna\") | .peers.awg3.pubkey" "$GOAMNEZIA_DIR/users.json")" = "$OLDKEY" ] && [ "$(cfgv .dupline_bug.fixed_at)" = "" ]' \
    "$out $(tail -3 "$ERR")"
sh_fn '_dn=0; docker_ensure_network() { _dn=$((_dn + 1)); [ "$_dn" -gt 1 ]; }; amnezia_profile_reline awg3 >/dev/null; echo "rc=$? code=$GA_ERR_CODE"; echo "msg=$GA_ERR_MSG"'
check "новая линия не встала (до записи профиля) — откат на прежнюю: reline_rolled_back, awg3 — 3.1 на 6714, у anna снова ключ, сказано кому выдан" \
    'grep -q "rc=1 code=reline_rolled_back" <<<"$out" && [ "$(cfgv .profiles.awg3.line)" = 3.1 ] && [ "$(cfgv .profiles.awg3.port)" = 6714 ] && [ "$(jq -r ".users[] | select(.name == \"anna\") | .peers | has(\"awg3\")" "$GOAMNEZIA_DIR/users.json")" = true ] && grep -qF "вернули прежнюю линию 3.1 на тот же порт и подсеть." <<<"$out" && grep -qF "Новые ключи выданы: anna. Прежние ключи больше не работают: подписка подтянет новые сама только там, где приложение само её обновляет" <<<"$out" && ! grep -qF "install_failed" <<<"$out" && [ "$(cfgv .dupline_bug.fixed_at)" = "" ]' \
    "$out $(tail -3 "$ERR")"
# Новая линия не встала, прежняя вернулась, но её контейнер не поднялся — тоже reline_partial, словами «вернули прежнюю»
sh_fn '_dn=0; docker_ensure_network() { _dn=$((_dn + 1)); [ "$_dn" -gt 1 ]; }; ga_amn_wait_up() { return 1; }; amnezia_syncconf() { ga_err syncconf_failed down; return 1; }; amnezia_profile_reline awg3 >/dev/null; echo "rc=$? code=$GA_ERR_CODE"; echo "msg=$GA_ERR_MSG"'
check "сбой и после отката: reline_partial — «с линией 2.0 не встал … вернули прежнюю линию 3.1, но её контейнер не поднялся», кому выдать ключи; флаг не снят" \
    'grep -q "rc=1 code=reline_partial" <<<"$out" && [ "$(cfgv .profiles.awg3.line)" = 3.1 ] && grep -qF "с линией 2.0 не встал" <<<"$out" && grep -qF "вернули прежнюю линию 3.1, но её контейнер не поднялся" <<<"$out" && grep -qF "Ключи не выданы — выдайте их, когда профиль поднимется: anna." <<<"$out" && [ "$(cfgv .dupline_bug.fixed_at)" = "" ]' \
    "$out $(tail -3 "$ERR")"
sh_fn 'amnezia_profile_repair awg3 >/dev/null 2>&1; users_add_peer anna awg3 >/dev/null 2>&1; echo "rc=$?"'
# Контейнер не поднялся — выдача ключа (применение к контейнеру) тоже не проходит: не врём «выданы», а говорим кому выдать
sh_fn 'ga_amn_wait_up() { return 1; }; amnezia_syncconf() { ga_err syncconf_failed down; return 1; }; amnezia_profile_reline awg3 >/dev/null; echo "rc=$? code=$GA_ERR_CODE"; echo "msg=$GA_ERR_MSG"'
check "профиль записан с новой линией, а контейнер не поднялся — reline_partial: «поднимите из копии состояния», ключ anna не выдан — так и сказано, «выдайте, когда профиль поднимется»" \
    'grep -q "rc=1 code=reline_partial" <<<"$out" && [ "$(cfgv .profiles.awg3.line)" = 2.0 ] && [ "$(jq -r ".users[] | select(.name == \"anna\") | .peers | has(\"awg3\")" "$GOAMNEZIA_DIR/users.json")" = false ] && grep -qF "Поднимите его заново из копии состояния" <<<"$out" && grep -qF "Ключи не выданы — выдайте их, когда профиль поднимется: anna." <<<"$out" && ! grep -qF "Новые ключи выданы" <<<"$out" && ! grep -qF "install_failed" <<<"$out" && [ -n "$(cfgv .dupline_bug.fixed_at)" ]' \
    "$out $(tail -3 "$ERR")"


# ── 6. Ключи остальным — тем же действием (DUPLINE-KEYS-01) ─────────────────
section "6: --keys-to-others=on — ключ на новой линии и остальным, на сервере, в том же действии"
reset31() {
    act profile-remove --profile=awg3
    act profile-install --profile=awg3 --port=6714 --line=3.1
    act user-add-peer --name=anna --profile=awg3
    jq '.dupline_bug.fixed_at = ""' "$GOAMNEZIA_DIR/config.json" >"$TMP/c" && mv "$TMP/c" "$GOAMNEZIA_DIR/config.json" && chmod 600 "$GOAMNEZIA_DIR/config.json"
}
# RV-4: пользователь, заведённый последним, но первый по имени, — «остальные» в итоге по имени, а не по порядку заведения
act user-add --name=aaron --profiles=awg2
reset31
REST=$(jq -r '[.users[] | select(.peers.awg3 == null) | .name] | sort | join(",")' "$GOAMNEZIA_DIR/users.json")
check "до перестановки: ключ на awg3 только у anna, остальные — без него" '[ -n "$REST" ] && [ "$(jq "[.users[] | select(.peers.awg3 != null)] | length" "$GOAMNEZIA_DIR/users.json")" = 1 ]' "$REST"
act profile-reline --profile=awg3 --keys-to-others=on
check "перестановка с ключами остальным: success, users — anna, others — все остальные, others_failed пуст" \
    '[ "$rc" = 0 ] && [ "$(jget ".users | join(\",\")")" = anna ] && [ "$(jget ".others | sort | join(\",\")")" = "$REST" ] && [ "$(jget ".others_failed | length")" = 0 ]' \
    "rc=$rc ${out:0:600} $(tail -3 "$ERR")"
check "RV-4: «остальные» в ответе — admin первым, остальные по имени (как в окне согласия панели и бота), aaron — первым" \
    '[ "$(jget ".others | join(\",\")")" = "$(jget ".others | sort_by([(. != \"admin\"), ascii_downcase]) | join(\",\")")" ] && [ "$(jget ".others[0]")" = aaron ]' \
    "$(jget '.others')"
check "ключ на awg3 (2.0) у всех пользователей, приватные на сервере" \
    '[ "$(cfgv .profiles.awg3.line)" = 2.0 ] && [ "$(jq "[.users[] | select((.peers.awg3.privkey // \"\") == \"\")] | length" "$GOAMNEZIA_DIR/users.json")" = 0 ]' \
    "$(jq -c "[.users[] | {name, k: (.peers.awg3 != null)}]" "$GOAMNEZIA_DIR/users.json")"
check "итог словами: шаг others — «Ключи на AmneziaWG 2.0 выданы и остальным пользователям: …», «нет ни у кого» нет" \
    '[ "$(jget "[.steps[] | select(.kind == \"others\")] | length")" = 1 ] && grep -qF "Ключи на AmneziaWG 2.0 выданы и остальным пользователям:" <<<"$(jget ".steps[] | select(.kind == \"others\") | .text")" && ! jq -e "[.steps[].kind] | index(\"nobody\")" <<<"$out" >/dev/null' \
    "$(jget .steps)"
check "журнал аудита: others=N failed_others=0" 'grep -qE "profile-reline.*awg3 3\.1->2\.0.* others=[1-9][0-9]* failed_others=0" "$GOAMNEZIA_AUDIT_LOG"' "$(tail -2 "$GOAMNEZIA_AUDIT_LOG")"
# R-5: несколько имён — во множественном числе; одна причина на всех — она одна, разные — по группам «причина (имена)»
reset31
sh_fn 'eval "$(declare -f users_add_peer | sed "1s/users_add_peer/_orig_uap/")"; users_add_peer() { case "$1" in vera|boris) ga_err peer_apply_failed "контейнер не принял ключ."; return 1 ;; esac; _orig_uap "$@"; }; amnezia_profile_reline awg3 "" on'
RLO=$(tail -n 1 <<<"$out")
OFT=$(jq -r ".steps[] | select(.kind == \"others_failed\") | .text" <<<"$RLO")
check "R-5: двоим не выдано по одной причине — «Ключи … не выданы (…): причина. Выдайте их…», причина одна" \
    '[ "$(jq -r ".others_failed | map(.name) | sort | join(\",\")" <<<"$RLO")" = boris,vera ] && grep -qE "^Ключи на AmneziaWG 2\.0 не выданы \((boris, vera|vera, boris)\): контейнер не принял ключ\. Выдайте их в карточках пользователей\.$" <<<"$OFT"' \
    "$OFT $(tail -3 "$ERR")"
reset31
sh_fn 'eval "$(declare -f users_add_peer | sed "1s/users_add_peer/_orig_uap/")"; users_add_peer() { case "$1" in vera) ga_err peer_apply_failed "контейнер не принял ключ."; return 1 ;; boris) GA_ERR_CODE=""; GA_ERR_MSG=""; return 1 ;; esac; _orig_uap "$@"; }; amnezia_profile_reline awg3 "" on'
RLO=$(tail -n 1 <<<"$out")
OFT=$(jq -r ".steps[] | select(.kind == \"others_failed\") | .text" <<<"$RLO")
check "R-5/NB2-3: причины разные — каждая со своими именами, общего списка в скобках нет; пустая — «причина — в журнале»" \
    'grep -qF "контейнер не принял ключ (vera)" <<<"$OFT" && grep -qF "причина — в журнале goAmnezia pro (boris)" <<<"$OFT" && grep -qF "Ключи на AmneziaWG 2.0 не выданы: " <<<"$OFT" && [ "$(grep -o vera <<<"$OFT" | wc -l)" -eq 1 ] && [ "$(grep -o boris <<<"$OFT" | wc -l)" -eq 1 ]' \
    "$OFT $(tail -3 "$ERR")"
# R-5: отказ перестановки при выбранном «ключ остальным» говорит, что остальным ключ не выдан
reset31
sh_fn '_dn=0; docker_ensure_network() { _dn=$((_dn + 1)); [ "$_dn" -gt 1 ]; }; amnezia_profile_reline awg3 "" on >/dev/null; echo "rc=$? code=$GA_ERR_CODE"; echo "msg=$GA_ERR_MSG"'
check "R-5: откат с --keys-to-others=on — reline_rolled_back и «Остальным пользователям ключ на этом профиле не выдан.»; у boris ключа на awg3 нет" \
    'grep -q "rc=1 code=reline_rolled_back" <<<"$out" && grep -qF "Остальным пользователям ключ на этом профиле не выдан." <<<"$out" && [ "$(jq -r ".users[] | select(.name == \"boris\") | .peers | has(\"awg3\")" "$GOAMNEZIA_DIR/users.json")" = false ]' \
    "$out $(tail -3 "$ERR")"
sh_fn '_dn=0; docker_ensure_network() { _dn=$((_dn + 1)); [ "$_dn" -gt 1 ]; }; amnezia_profile_reline awg3 >/dev/null; echo "rc=$? code=$GA_ERR_CODE"; echo "msg=$GA_ERR_MSG"'
check "R-5: откат без выбора «остальным» — про остальных ни слова" \
    'grep -q "rc=1 code=reline_rolled_back" <<<"$out" && ! grep -qF "Остальным пользователям" <<<"$out"' "$out"
reset31
sh_fn 'eval "$(declare -f users_add_peer | sed "1s/users_add_peer/_orig_uap/")"; users_add_peer() { if [ "$1" = vera ]; then ga_err peer_apply_failed "контейнер не принял ключ."; return 1; fi; _orig_uap "$@"; }; amnezia_profile_reline awg3 "" on'
RLO=$(tail -n 1 <<<"$out")
check "у кого выдача не вышла — others_failed {name, code, message}, шаг others_failed словами, остальным выдано" \
    '[ "$(jq -r ".others_failed[0].name" <<<"$RLO")" = vera ] && [ "$(jq -r ".others_failed[0].code" <<<"$RLO")" = peer_apply_failed ] && [ "$(jq -r ".others_failed[0].message" <<<"$RLO")" = "контейнер не принял ключ." ] && ! jq -e ".others | index(\"vera\")" <<<"$RLO" >/dev/null && grep -qF "Ключ на AmneziaWG 2.0 не выдан (vera): контейнер не принял ключ. Выдайте его в карточке пользователя." <<<"$(jq -r ".steps[] | select(.kind == \"others_failed\") | .text" <<<"$RLO")"' \
    "$RLO $(tail -3 "$ERR")"


# ── 7. Удаление профиля называет страницы, которые перестанут работать (PROFILE-REMOVE-PAGES) ─────────────────
section "7: profile-remove-preview — чьи страницы подключения перестанут работать; правило то же, что у снятия"
mkpage() {  # mkpage <id> <пользователь> <профили JSON>
    mkdir -p "$PG/$1"
    printf '{"id":"%s","user":"%s","profiles":%s,"expires_epoch":0,"forever":true}\n' "$1" "$2" "$3" >"$PG/$1/meta.json"
    printf '%s\n' "$(printf 'z%.0s' $(seq 1 43))" >"$PG/$1/secret"
}
act user-add --name=gleb --profiles=awg2,awg3
act user-add --name=dina --profiles=awg2
mkpage p-anna3 anna '["awg3"]'
mkpage p-anna32 anna '["awg3","awg2"]'
mkpage p-1a2b3c boris '["awg3"]'
mkpage p-gleb32 gleb '["awg3","awg2"]'
mkpage p-dina3 dina '["awg3"]'
KEYED=$(jq -c '[.users[] | select(.name == "anna" or .name == "boris" or .name == "gleb" or .name == "dina") | {key: .name, value: ((.peers // {}) | map_values((.privkey // "") != ""))}] | from_entries' "$GOAMNEZIA_DIR/users.json")
check "раскладка: anna — awg3 свой ключ, awg2 только в приложении; boris — awg3; gleb — свои ключи на awg2 и awg3; dina — без awg3" \
    '[ "$(jq -c ".anna" <<<"$KEYED")" = "{\"awg2\":false,\"awg3\":true}" ] && [ "$(jq -r ".boris.awg3" <<<"$KEYED")" = true ] && [ "$(jq -c ".gleb" <<<"$KEYED")" = "{\"awg2\":true,\"awg3\":true}" ] && [ "$(jq -r ".dina | has(\"awg3\")" <<<"$KEYED")" = false ]' "$KEYED"
cp "$GOAMNEZIA_DIR/config.json" "$TMP/cfg.rp"; cp "$GOAMNEZIA_DIR/users.json" "$TMP/users.rp"
act profile-remove-preview --profile=awg3
check "превью: 3 страницы — p-anna3, p-anna32 (ключ awg2 у anna только в приложении — не в счёт), p-1a2b3c (boris); p-gleb32 (есть awg2) и p-dina3 (без ключей и сейчас) — нет" \
    '[ "$rc" = 0 ] && [ "$(jget .pages)" = 3 ] && [ "$(jget ".page_users | sort | join(\",\")")" = "anna,boris" ] && [ "$(jget .holders)" -ge 3 ] && [ "$(jget .title)" = "AmneziaWG 2.0 · порт 6714" ]' \
    "rc=$rc ${out:0:500} $(tail -3 "$ERR")"
check "превью словами: «Страницы подключения у anna, boris перестанут работать: в них был только этот профиль.»; адресов страниц нет" \
    '[ "$(jget ".page_users | sort | join(\", \")")" = "anna, boris" ] && grep -qE "^Страницы подключения у (anna, boris|boris, anna) перестанут работать: в них был только этот профиль\.$" <<<"$(jget .text)" && [ "$(jget .message)" = "$(jget .text)" ] && ! grep -q "p-anna3\|p-1a2b3c\|zzzz" <<<"$out"' "$out"
check "превью ничего не меняет: config.json и users.json те же, страницы на месте" \
    'cmp -s "$TMP/cfg.rp" "$GOAMNEZIA_DIR/config.json" && cmp -s "$TMP/users.rp" "$GOAMNEZIA_DIR/users.json" && [ -f "$PG/p-anna3/meta.json" ] && [ -f "$PG/p-1a2b3c/meta.json" ]'
GOAMNEZIA_CALL_LANG=en act profile-remove-preview --profile=awg3
check "по-английски — «The connection pages of anna, boris stop working: they had only this profile.»" \
    'grep -qE "^The connection pages of (anna, boris|boris, anna) stop working: they had only this profile\.$" <<<"$(jget .text)"' "$(jget .text)"
act profile-remove-preview --profile=awg2
check "awg2 (профиль приложения): у всех со страницей остаётся awg3 — страниц, где был только awg2, нет: pages 0, text пуст" \
    '[ "$rc" = 0 ] && [ "$(jget .pages)" = 0 ] && [ "$(jget .text)" = "" ] && [ "$(jget ".page_users | length")" = 0 ]' "rc=$rc ${out:0:400}"
act profile-remove-preview --profile=awg1
check "профиль не установлен — отказ profile_not_installed" '[ "$rc" != 0 ] && [ "$(jget .code)" = profile_not_installed ]' "rc=$rc ${out:0:300}"
sh_fn '_profile_remove_interactive awg3 2>&1'
RMW=$(tr -s ' \n' ' ' <<<"$out")
check "SSH: до согласия на удаление — «Страницы подключения у … перестанут работать …»; без согласия — ничего не удалено" \
    'grep -qE "Страницы подключения у (anna, boris|boris, anna) перестанут работать: в них был только этот профиль\." <<<"$RMW" && [ "$(cfgv .profiles.awg3.line)" = 2.0 ]' "$RMW"
rm -rf "$PG/p-anna3" "$PG/p-anna32"
act profile-remove-preview --profile=awg3
check "одна страница — «Страница подключения у boris перестанет работать: в ней был только этот профиль.»" \
    '[ "$(jget .pages)" = 1 ] && [ "$(jget .text)" = "Страница подключения у boris перестанет работать: в ней был только этот профиль." ]' "$out"
mkpage p-anna3 anna '["awg3"]'
mkpage p-anna32 anna '["awg3","awg2"]'
act profile-remove --profile=awg3
check "удаление сняло ровно названные страницы: p-anna3, p-anna32 и p-1a2b3c сняты, p-gleb32 жива" \
    '[ "$rc" = 0 ] && [ ! -f "$PG/p-anna3/meta.json" ] && [ ! -f "$PG/p-anna32/meta.json" ] && [ ! -f "$PG/p-1a2b3c/meta.json" ] && [ -f "$PG/p-gleb32/meta.json" ]' \
    "rc=$rc $(ls "$PG" 2>&1 | tr '\n' ' ')"


# ── 8. Шапка главного меню SSH о линиях (F-2): каждая ветка — своё короткое предложение (R-6) ─────────────────
section "8: шапка SSH о линиях — повтор без 2.0, повтор без 3.1, нет 2.0 и некого переставлять, нет 2.0 и некуда ставить"
# lines_check подменён: ветка выбирается по no_20, reline.profile и install.profile; флаг миграции — не стоит.
dash() {  # dash '<JSON lines_check>'
    sh_fn "ga_amn_dupline_fix_json() { echo null; }; ga_amn_lines_check() { printf '%s\n' '$1'; }; _pr_name() { echo \"\$1\"; }; _dash_lines_warn 2>&1"
    out=$(tr -s ' \n' ' ' <<<"$out")
}
dash '{"text":"ОБЩИЙ","no_20":true,"reline":{"profile":"awg3","line":"2.0"}}'
check "нет 2.0, есть что переставить — dash_lines_no20_reline: куда идти, без общего абзаца" \
    'grep -qF "Нет AmneziaWG 2.0: старые приложения и роутеры Keenetic на обычной прошивке не подключатся — «Протоколы» → awg3 → «Переставить на 2.0»." <<<"$out" && ! grep -qF "ОБЩИЙ" <<<"$out"' "$out"
dash '{"text":"ОБЩИЙ","no_20":false,"reline":{"profile":"awg2","line":"3.1"}}'
check "линия повторяется, нет 3.1 — dash_lines_reline_short: «Линия повторяется, а AmneziaWG 3.1 нет — … «Переставить на 3.1»»" \
    'grep -qF "Линия повторяется, а AmneziaWG 3.1 нет — «Протоколы» → awg2 → «Переставить на 3.1»." <<<"$out" && ! grep -qF "ОБЩИЙ" <<<"$out"' "$out"
dash '{"text":"ОБЩИЙ","no_20":true,"install":{"profile":"awg3"}}'
check "нет 2.0, переставлять нечего, есть свободное место — dash_lines_no20_install: «Протоколы» → «…» → «Установить»" \
    'grep -qF "Нет AmneziaWG 2.0: старые приложения и роутеры Keenetic на обычной прошивке не подключатся — «Протоколы» → «awg3» → «Установить»." <<<"$out" && ! grep -qF "ОБЩИЙ" <<<"$out"' "$out"
dash '{"text":"ОБЩИЙ","no_20":true}'
check "нет 2.0, ни переставить, ни поставить — dash_lines_no20_short: «ни к одному профилю»" \
    'grep -qF "Нет AmneziaWG 2.0: старые приложения и роутеры Keenetic на обычной прошивке не подключатся ни к одному профилю (3.1 Keenetic понимает пока только в dev-прошивке)." <<<"$out" && ! grep -qF "ОБЩИЙ" <<<"$out"' "$out"
dash '{"text":"ОБЩИЙ","no_20":false}'
check "иначе — общий текст lines_check как есть" 'grep -qF "ОБЩИЙ" <<<"$out"' "$out"


# ── 9. «Что нового»: пункты «Из <версии>:» тому, кто уже на ней, не показываются (LH-1) ─────────────────
section "9: «Что нового» — «Из 1.0.11: …» при переходе с 1.0.11 не показывается, с 1.0.10 — показывается"
# Словарь подменён своими строками: проверка не зависит от текста «Что нового» текущего выпуска.
chg() {  # chg <с какой версии>
    sh_fn "for _i in \$(seq 1 12); do unset 'I18N[upd_l'\$_i']'; done; I18N[upd_l1]='• goAmnezia pro 9.9.9 — НОВОЕ'; I18N[upd_l2]='• Из 1.0.11: СТАРОЕ'; I18N[upd_l3]='• Из 1.0.12: СРЕДНЕЕ'; I18N[upd_l4]='• Ещё НОВОЕ2'; show_upgrade_changelog '$1'"
    out=$(tr -s ' \n' ' ' <<<"$out")
}
chg 1.0.11
check "с 1.0.11: пункта «Из 1.0.11:» нет, «Из 1.0.12:» и новые есть" \
    '! grep -qF "СТАРОЕ" <<<"$out" && grep -qF "Из 1.0.12: СРЕДНЕЕ" <<<"$out" && grep -qF "НОВОЕ" <<<"$out" && grep -qF "НОВОЕ2" <<<"$out"' "$out"
chg 1.0.12
check "с 1.0.12: нет ни «Из 1.0.11:», ни «Из 1.0.12:»" '! grep -qF "СТАРОЕ" <<<"$out" && ! grep -qF "СРЕДНЕЕ" <<<"$out" && grep -qF "НОВОЕ2" <<<"$out"' "$out"
chg 1.0.10
check "с 1.0.10: «Из 1.0.11:» и «Из 1.0.12:» показаны" 'grep -qF "Из 1.0.11: СТАРОЕ" <<<"$out" && grep -qF "Из 1.0.12: СРЕДНЕЕ" <<<"$out"' "$out"
chg "?"
check "прежняя версия неизвестна — показано всё" 'grep -qF "СТАРОЕ" <<<"$out" && grep -qF "СРЕДНЕЕ" <<<"$out"' "$out"
# Настоящий словарь (DOCS-1018): переход с main 1.0.11 называет главное этих дней — «нет 2.0», отзыв страницы с
# подпиской и ключами, имена файлов и временную ссылку выключенного, срок подписки FlClash и поиск — на ru и en;
# пунктов «Из 1.0.11:» нет, «Из 1.0.12:» есть; строк не больше двенадцати (столько читает show_upgrade_changelog).
sh_fn "show_upgrade_changelog 1.0.11"
out=$(tr -s ' \n' ' ' <<<"$out")
check "ru, с 1.0.11: «нет AmneziaWG 2.0», отзыв страницы, anna-2_0.conf, временная ссылка, FlClash, поиск; без «Из 1.0.11:»" \
    'grep -qF "нет AmneziaWG 2.0" <<<"$out" && grep -qF "Отзыв страницы подключения" <<<"$out" && grep -qF "anna-2_0.conf" <<<"$out" && grep -qF "временная ссылка выключенного" <<<"$out" && grep -qF "FlClash" <<<"$out" && grep -qF "Ctrl+K" <<<"$out" && grep -qF "Из 1.0.12:" <<<"$out" && ! grep -qF "Из 1.0.11:" <<<"$out"' "$out"
# K3-1 (живой стенд 29.10): пункт про Keenetic, приклеенный к TCP MSS через «;», легко пропустить — он своей строкой
# «• Keenetic 5.1 …», а строка про TCP MSS о нём не говорит. Место — за счёт двух строк «Из 1.0.11:», слитых в одну.
check "ru: ключи для Keenetic 5.1 (KEENETIC-I1) — своим пунктом «• Keenetic 5.1 …», не хвостом строки про TCP MSS" \
    'grep -qF "• Keenetic 5.1 принимает ключи 1.5 и 2.0: I1 по умолчанию теперь начинается с <b>" <<<"$out" && grep -q "^I18N\[upd_l[0-9]*\]=\"• Keenetic 5.1 " "$ROOT/lib/lang/ru.d/menu.sh" && ! grep "^I18N\[upd_l[0-9]*\]=" "$ROOT/lib/lang/ru.d/menu.sh" | grep "TCP MSS" | grep -qF "Keenetic 5.1"' "$out"
sh_fn "load_language en >/dev/null 2>&1; show_upgrade_changelog 1.0.11"
out=$(tr -s ' \n' ' ' <<<"$out")
check "en, с 1.0.11: те же пункты по-английски; без «From 1.0.11:»" \
    'grep -qF "no AmneziaWG 2.0" <<<"$out" && grep -qF "Revoking a connection page" <<<"$out" && grep -qF "anna-2_0.conf" <<<"$out" && grep -qF "temporary link" <<<"$out" && grep -qF "FlClash" <<<"$out" && grep -qF "From 1.0.12:" <<<"$out" && ! grep -qF "From 1.0.11:" <<<"$out"' "$out"
check "en: Keenetic 5.1 accepts 1.5 and 2.0 keys (KEENETIC-I1) — своим пунктом, не хвостом строки про TCP MSS" \
    'grep -qF "• Keenetic 5.1 accepts 1.5 and 2.0 keys: the default I1 now starts with <b>" <<<"$out" && ! grep "^I18N\[upd_l[0-9]*\]=" "$ROOT/lib/lang/en.d/menu.sh" | grep "TCP MSS" | grep -qF "Keenetic 5.1"' "$out"
# Две строки «Из 1.0.11:» слиты в одну (K3-1): тому, кто пришёл с 1.0.10, видно обе половины, и префикс — один раз.
sh_fn "show_upgrade_changelog 1.0.10"
out=$(tr -s ' \n' ' ' <<<"$out")
check "ru, с 1.0.10: «Из 1.0.11:» одной строкой — подписка на пользователя и панель на слабом сервере, префикс один" \
    'grep -qF "Из 1.0.11: подписка на пользователя" <<<"$out" && grep -qF "панель на слабом сервере — за доли секунды" <<<"$out" && [ "$(grep -o "Из 1.0.11:" <<<"$out" | wc -l | tr -d " ")" = 1 ]' "$out"
check "строк «Что нового» в словарях не больше двенадцати" \
    '[ "$(grep -c "^I18N\[upd_l[0-9]*\]=" "$ROOT/lib/lang/ru.d/menu.sh")" -le 12 ] && [ "$(grep -c "^I18N\[upd_l[0-9]*\]=" "$ROOT/lib/lang/en.d/menu.sh")" -le 12 ]'


# ── 10. Карточка профиля в SSH: на UDP/443 пункта «Занять UDP/443» нет (LH-2) ─────────────────
section "10: карточка профиля SSH — «Занять UDP/443» у профиля на 443 серым; номера остальных прежние"
# Порт профиля подменён чтением настроек: ставить профиль на 443 в прогоне незачем.
P443='eval "$(declare -f _cfg | sed "1s/_cfg/_orig_cfg/")"; _cfg() { [ "$1" = ".profiles.awg2.port" ] && { echo 443; return 0; }; _orig_cfg "$@"; }; '
sh_fn "${P443}"'_profile_card awg2 <<<"0"'
# A-7 (CRITICS-FIX-1): пункт 2 не пропадает дыркой 1), 3), а стоит серым «— профиль уже на нём».
check "профиль на 443: «2) Занять UDP/443 — профиль уже на нём», «1) Сменить порт» и «3)» на месте" \
    'grep -qE "^ *2\) Занять UDP/443 — профиль уже на нём" <<<"$out" && grep -qE "^ *1\) " <<<"$out" && grep -qE "^ *3\) " <<<"$out"' "$(tail -25 <<<"$out")"
sh_fn "${P443}"'_profile_card awg2 <<<$'"'"'2\n\n0'"'"
check "профиль на 443: набранное «2» — «Профиль уже на UDP/443.», к смене порта не ведёт" \
    'grep -qF "Профиль уже на UDP/443." <<<"$out$(cat "$ERR")" && ! grep -qF "Перенести " <<<"$out$(cat "$ERR")"' "$(tail -5 "$ERR")"
sh_fn '_profile_card awg2 <<<"0"'
check "профиль не на 443: «2) Занять UDP/443» есть" 'grep -qE "^ *2\) Занять UDP/443" <<<"$out"' "$(tail -25 <<<"$out")"
# LH-8: линия 2.0 на сборке ядра 3.x — рядом с «Версия ядра» сказано, что сборка обслуживает и 2.0; у 3.1 — без приписки
VER31='eval "$(declare -f _cfg | sed "1s/_cfg/_orig_cfg/")"; _cfg() { [ "$1" = ".profiles.awg2.image_tag" ] && { echo 3.1.20260828; return 0; }; _orig_cfg "$@"; }; '
sh_fn "${VER31}"'_profile_line() { echo 2.0; }; _profile_card awg2 <<<"0"'
check "LH-8: профиль 2.0 на сборке 3.1 — «Версия ядра: 3.1.20260828 (эта сборка ядра обслуживает и линию 2.0)»" \
    'grep -qF "Версия ядра: 3.1.20260828 (эта сборка ядра обслуживает и линию 2.0)" <<<"$out"' "$(grep -F "Версия ядра" <<<"$out")"
sh_fn "${VER31}"'_profile_line() { echo 3.1; }; _profile_card awg2 <<<"0"'
check "LH-8: профиль 3.1 — без приписки" 'grep -qF "Версия ядра: 3.1.20260828" <<<"$out" && ! grep -qF "обслуживает и линию 2.0" <<<"$out"' "$(grep -F "Версия ядра" <<<"$out")"

DONE=1
printf '\nИТОГ: %s прошло, %s не прошло\n' "$pass" "$fail"
[ "$fail" = 0 ]
