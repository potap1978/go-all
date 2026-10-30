#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — аварийный ключ панели (SEC50-08, решение владельца 28.09.2026): «на домене — только пароль или код
# из бота; аварийный ключ — только формировать в SSH полной строкой». Без Docker и без сети (GOAMNEZIA_DRY_RUN=1),
# через НАСТОЯЩИЙ install.sh: `install.sh --panel-emergency-key` — тот же _panel_emergency_key, что у пункта меню
# «Панель и мини-апп → Аварийный ключ» (сам пункт — tests/test_wizard_pty.py, PanelEmergencyKeyItem).
#
# Проверяется: ключ — одна строка base64url из 43 знаков (как secrets.token_urlsafe(32) у admin-web/server.py), файл
# 0600; рядом token.meta 0600 — ровно {created, expires}, разница — час; времянок не осталось; каждый вызов — новый
# ключ, прежний (и постоянный без срока) из файла уходит; полной строкой напечатаны команда туннеля (с -p, если SSH не
# на 22-м порту) и адрес с ключом, порт — из config.json; ключа нет ни в журнале, ни в аудите, ни в stderr; без службы
# панели — отказ, файлы не созданы; не записалось — прежний ключ не тронут; английский; в мосте такого действия нет;
# свой путь к файлу ключа — срок в <файл>.meta.
# LZ-07: обновление на 0.6.0 (шаг миграции) убирает старый постоянный ключ без .meta — идемпотентно, с записью в аудит
# без самого ключа; ключ со сроком не трогает; серверы, получившие 0.6.0 раньше шага, чистятся повтором шагов.
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_panel_key_dryrun.sh
# Нужны: bash ≥ 5, jq.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL="$ROOT/install.sh"
BASH5="${BASH5:-$BASH}"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-pkey.XXXXXX") || exit 2
DONE=""
OUT="$TMP/stdout.log"
ERR="$TMP/stderr.log"
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; chmod -R u+w "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_SERVER_IP="203.0.113.10" GOAMNEZIA_EDGE_IPS_STUB=""
export GOAMNEZIA_DIR="$TMP/goamnezia" GOAMNEZIA_LOCK_DIR="$TMP/run" GOAMNEZIA_LOG_FILE="$TMP/goamnezia.log"
export GOAMNEZIA_AUDIT_LOG="$TMP/audit.log" GOAMNEZIA_NOTIFY_DIR="$TMP/run/notify"
export BOT_DIR="$TMP/bot" ADMIN_DIR="$TMP/admin" MCP_DIR="$TMP/mcp"
# Для миграции (LZ-07) — всё продукта во временном каталоге: юниты, сборка, копия upstream, сокет Docker, которого нет.
export GOAMNEZIA_SYSTEMD_DIR="$TMP/systemd" GOAMNEZIA_BUILD_ROOT="$TMP/build" UPSTREAM_DIR="$TMP/upstream"
export GOAMNEZIA_DOCKER_SOCK="$TMP/nosuch.sock" GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"
unset GOAMNEZIA_ADMIN_TOKEN_FILE SSH_CONNECTION SSH_CLIENT SSH_TTY NO_COLOR
mkdir -p "$GOAMNEZIA_DIR" "$TMP/run" "$TMP/systemd" "$TMP/build" "$TMP/upstream" "$TMP/shm"
cp -R "$ROOT/lib/upstream/." "$TMP/upstream/"
# systemctl машины прогону не нужен: подставной отвечает отказом, как машина без systemd.
mkdir -p "$TMP/hostbin"; printf '#!/bin/sh\nexit 1\n' >"$TMP/hostbin/systemctl"; chmod +x "$TMP/hostbin/systemctl"
export PATH="$TMP/hostbin:$PATH"
printf 'ru\n' >"$GOAMNEZIA_DIR/.language"
TOKEN="$ADMIN_DIR/token"
META="$ADMIN_DIR/token.meta"

pass=0; fail=0; rc=0
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 900; return 0; }
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
# run_key — новый процесс, как у человека в SSH: stdout (две строки для копирования) и stderr (слова) — раздельно.
run_key() { "$BASH5" "$INSTALL" --panel-emergency-key >"$OUT" 2>"$ERR" </dev/null; rc=$?; return 0; }
# plain — stdout без цветов (в файл их и так нет, но проверка не должна от этого зависеть)
plain() { sed $'s/\033\\[[0-9;]*m//g' "$OUT"; }
flat() { cat "$OUT" "$ERR" | sed $'s/\033\\[[0-9;]*m//g' | tr -s '[:space:]' ' '; }
mode_of() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }
key_now() { head -n 1 "$TOKEN" 2>/dev/null; }

section "окружение"
printf '  bash: %s (%s)\n' "$BASH5" "$("$BASH5" -c 'echo $BASH_VERSION')"
check "в мосте такого действия нет: бот, панель и MCP ключ не выдают" \
    '! jq -e ".actions | keys | map(select(test(\"emergency\"))) | length > 0" "$ROOT/lib/actions.json"'
"$BASH5" "$INSTALL" --help >"$OUT" 2>"$ERR" </dev/null
check "--help называет --panel-emergency-key" 'grep -qF -- "--panel-emergency-key" "$OUT"' "$(cat "$OUT")"

# ── Панели нет ───────────────────────────────────────────────────────────────
section "служба панели не установлена"
run_key
check "rc 1, «Веб-панель не установлена», файлы не созданы" \
    '[ "$rc" = 1 ] && flat | grep -q "Веб-панель не установлена" && [ ! -e "$TOKEN" ] && [ ! -e "$META" ]' "rc=$rc $(tail -3 "$ERR")"

# ── Выпуск ───────────────────────────────────────────────────────────────────
section "выпуск ключа"
mkdir -p "$ADMIN_DIR"
printf '# служба панели стоит\n' >"$ADMIN_DIR/server.py"
# Прежний постоянный ключ без срока — как его заводил install_admin.sh (hex 64): служба 0.6.0 его не примет.
printf '%s\n' "$(printf 'deadbeef%.0s' 1 2 3 4 5 6 7 8)" >"$TOKEN"
old=$(key_now)
t0=$(date +%s)
run_key
key=$(key_now)
check "rc 0; ключ — одна строка base64url из 43 знаков (secrets.token_urlsafe(32) у server.py)" \
    '[ "$rc" = 0 ] && [ "$(wc -l <"$TOKEN" | tr -d " ")" = 1 ] && [[ "$key" =~ ^[A-Za-z0-9_-]{43}$ ]]' \
    "rc=$rc len=${#key} $(tail -3 "$ERR")"
check "ключ подходит под ADMIN_TOKEN_RE службы панели" \
    'python3 -c "import re,sys; sys.exit(0 if re.match(r\"^[A-Za-z0-9_.~=-]{16,256}$\", sys.argv[1]) else 1)" "$key"'
check "прежний постоянный ключ из файла ушёл" '[ "$key" != "$old" ]'
check "token — 0600" '[ "$(mode_of "$TOKEN")" = 600 ]' "$(mode_of "$TOKEN")"
check "token.meta — 0600, ровно {created, expires}, целые, expires − created = 3600, created — сейчас" \
    '[ "$(mode_of "$META")" = 600 ] && jq -e --argjson t0 "$t0" "(keys == [\"created\",\"expires\"]) and (.created | type) == \"number\" and (.created | floor) == .created and (.expires - .created) == 3600 and .created >= \$t0 - 5 and .created <= \$t0 + 60" "$META"' \
    "$(cat "$META" 2>/dev/null) mode=$(mode_of "$META")"
check "времянок в каталоге панели не осталось" '[ -z "$(find "$ADMIN_DIR" -name ".token*" 2>/dev/null | head -1)" ]' "$(ls -la "$ADMIN_DIR")"
check "команда туннеля — одной строкой: ssh -L 1985:127.0.0.1:1985 root@203.0.113.10" \
    'plain | grep -qxE "[[:space:]]*ssh -L 1985:127\.0\.0\.1:1985 root@203\.0\.113\.10"' "$(cat "$OUT")"
check "адрес с ключом — одной строкой: http://127.0.0.1:1985/?token=<ключ>" \
    'plain | grep -qxE "[[:space:]]*http://127\.0\.0\.1:1985/\?token=$key"' "$(cat "$OUT")"
check "сначала туннель, потом адрес" \
    '[ "$(plain | grep -nE "ssh -L|/\?token=" | cut -d: -f1 | paste -sd, -)" = "$(plain | grep -nE "ssh -L" | cut -d: -f1),$(plain | grep -nE "/\?token=" | cut -d: -f1)" ] && [ "$(plain | grep -nE "ssh -L" | cut -d: -f1)" -lt "$(plain | grep -nE "/\?token=" | cut -d: -f1)" ]'
check "сказано: прежний не действует; 1 час, после входа сгорает, с домена не работает; до скольки" \
    'flat | grep -q "прежний больше не действует" && flat | grep -q "Ключ живёт 1 час, после входа сгорает и с домена не работает — только через SSH-туннель" && flat | grep -qE "Действует до [0-9]{2}:[0-9]{2} по времени сервера"' \
    "$(cat "$OUT" "$ERR")"
check "ключа нет ни в журнале, ни в аудите, ни в stderr; в аудите — panel-emergency-key со сроком" \
    '! grep -qF -e "$key" "$GOAMNEZIA_LOG_FILE" 2>/dev/null && ! grep -qF -e "$key" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF -e "$key" "$ERR" && grep -q "\"action\":\"panel-emergency-key\".*\"details\":\"ttl=3600 expires=" "$GOAMNEZIA_AUDIT_LOG"' \
    "$(tail -2 "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null)"

# ── Повтор, SSH не на 22-м порту, порт панели ────────────────────────────────
section "повтор: новый ключ; SSH на 2222; порт панели из config.json"
SSH_CONNECTION="198.51.100.7 50000 203.0.113.10 2222" run_key
key2=$(key_now)
check "каждый вызов — новый ключ: прежний из файла ушёл, срок переписан" \
    '[ "$rc" = 0 ] && [[ "$key2" =~ ^[A-Za-z0-9_-]{43}$ ]] && [ "$key2" != "$key" ] && jq -e "(.expires - .created) == 3600" "$META"' "rc=$rc"
check "SSH на 2222: в команде туннеля -p 2222" \
    'plain | grep -qxE "[[:space:]]*ssh -p 2222 -L 1985:127\.0\.0\.1:1985 root@203\.0\.113\.10"' "$(cat "$OUT")"
printf '{"panel":{"port":2985}}\n' >"$GOAMNEZIA_DIR/config.json"
run_key
key3=$(key_now)
check "порт панели 2985 из config.json — и в туннеле, и в адресе" \
    'plain | grep -qxE "[[:space:]]*ssh -L 2985:127\.0\.0\.1:2985 root@203\.0\.113\.10" && plain | grep -qxE "[[:space:]]*http://127\.0\.0\.1:2985/\?token=$key3"' "$(cat "$OUT")"
rm -f "$GOAMNEZIA_DIR/config.json"

# ── Не записалось ────────────────────────────────────────────────────────────
section "каталог панели не записывается — прежний ключ не тронут"
if [ "$(id -u)" = 0 ]; then
    ok "пропущено: под root права каталога не мешают записи (проверяется на стенде без root)"
else
    before=$(key_now); meta_before=$(cat "$META")
    chmod 500 "$ADMIN_DIR"
    run_key
    chmod 700 "$ADMIN_DIR"
    check "rc 1, «выпустить не удалось … прежний не тронут», ключ и срок прежние, адреса с ключом нет" \
        '[ "$rc" = 1 ] && flat | grep -q "Прежний ключ не тронут" && [ "$(key_now)" = "$before" ] && [ "$(cat "$META")" = "$meta_before" ] && ! grep -q "token=" "$OUT"' \
        "rc=$rc $(tail -3 "$ERR")"
fi

# ── Свой путь к файлу ключа ──────────────────────────────────────────────────
section "свой путь к файлу ключа: срок — рядом, в <файл>.meta (так его ищет служба: ADMIN_TOKEN_META)"
GOAMNEZIA_ADMIN_TOKEN_FILE="$ADMIN_DIR/emergency" run_key
check "ключ — в emergency, срок — в emergency.meta, оба 0600" \
    '[ "$rc" = 0 ] && [[ "$(head -n 1 "$ADMIN_DIR/emergency")" =~ ^[A-Za-z0-9_-]{43}$ ]] && jq -e "(.expires - .created) == 3600" "$ADMIN_DIR/emergency.meta" && [ "$(mode_of "$ADMIN_DIR/emergency.meta")" = 600 ]' \
    "rc=$rc $(ls -la "$ADMIN_DIR")"

# ── Английский ───────────────────────────────────────────────────────────────
section "английский"
printf 'en\n' >"$GOAMNEZIA_DIR/.language"
run_key
key4=$(key_now)
check "те же вещи по-английски, адрес с новым ключом" \
    '[ "$rc" = 0 ] && flat | grep -q "A new emergency key is ready — the previous one no longer works" && flat | grep -q "The key lives 1 hour, burns after sign-in and does not work from the domain" && plain | grep -qxE "[[:space:]]*http://127\.0\.0\.1:1985/\?token=$key4"' \
    "$(cat "$OUT" "$ERR")"
printf 'ru\n' >"$GOAMNEZIA_DIR/.language"

# ── LZ-07: миграция 0.6.0 убирает старый постоянный ключ ─────────────────────
section "LZ-07: обновление убирает старый постоянный ключ (без .meta), ключ со сроком не трогает"
GA_VER=$(sed -n -E 's/^GOAMNEZIA_VERSION="([^"]+)".*/\1/p' "$ROOT/lib/common.sh" | head -n 1)
# mig <версия> — config.json «от прежней версии» без отметок прошлой миграции: следующий пишущий вызов моста (как тик
# сторожа) проводит миграцию целиком.
mig() {
    jq -n --arg v "$1" '{version: $v, installed: true, lang: "ru", profiles: {}}' >"$GOAMNEZIA_DIR/config.json"
    chmod 600 "$GOAMNEZIA_DIR/config.json"
    rm -f "$GOAMNEZIA_DIR"/.migrated_* "$GOAMNEZIA_DIR"/.migrating_* "$GOAMNEZIA_DIR"/.preupgrade_* "$GOAMNEZIA_DIR/.changelog_pending"
}
act() { "$BASH5" "$INSTALL" --action="$1" --json "${@:2}" >"$OUT" 2>"$ERR" </dev/null; rc=$?; return 0; }
legacy=$(printf 'deadbeef%.0s' 1 2 3 4 5 6 7 8)
rm -f "$TOKEN" "$META" "$ADMIN_DIR"/emergency "$ADMIN_DIR"/emergency.meta
printf '%s\n' "$legacy" >"$TOKEN"; chmod 600 "$TOKEN"
mig 0.5.1
act lang-set --lang=ru
check "миграция 0.5.1 → $GA_VER прошла (версия записана), ключ без .meta удалён, служба панели на месте" \
    '[ "$rc" = 0 ] && [ "$(jq -r .version "$GOAMNEZIA_DIR/config.json")" = "$GA_VER" ] && [ ! -e "$TOKEN" ] && [ -f "$ADMIN_DIR/server.py" ]' \
    "rc=$rc $(tail -5 "$ERR")"
check "аудит: panel-legacy-key-removed с путём; самого ключа нет ни в аудите, ни в журнале, ни в выводе" \
    'grep -q "\"action\":\"panel-legacy-key-removed\".*\"details\":\"file=$TOKEN\"" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF "$legacy" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF "$legacy" "$GOAMNEZIA_LOG_FILE" 2>/dev/null && ! grep -qF "$legacy" "$OUT" "$ERR"' \
    "$(grep legacy "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null)"
check "человеку — строка: ключ удалён и где взять ключ на час" \
    'tr -s "[:space:]" " " <"$ERR" | grep -q "Старый постоянный аварийный ключ панели удалён: панель его больше не принимает"' "$(tail -6 "$ERR")"
n1=$(grep -c "panel-legacy-key-removed" "$GOAMNEZIA_AUDIT_LOG")
mig 0.5.1
act lang-set --lang=ru
check "повтор миграции (идемпотентно): ключа нет — делать нечего, новой записи в аудите нет" \
    '[ "$rc" = 0 ] && [ "$(jq -r .version "$GOAMNEZIA_DIR/config.json")" = "$GA_VER" ] && [ "$(grep -c "panel-legacy-key-removed" "$GOAMNEZIA_AUDIT_LOG")" = "$n1" ]' "rc=$rc"
run_key
key6=$(key_now)
mig 0.5.1
act lang-set --lang=ru
check "ключ со сроком (рядом .meta) миграция не трогает" '[ "$rc" = 0 ] && [ -n "$key6" ] && [ "$(key_now)" = "$key6" ] && [ -f "$META" ]' "rc=$rc"
# Серверы, получившие 0.6.0 раньше этого шага: та же версия, код на диске сменился (отметка с другим отпечатком) —
# идемпотентные шаги повторяются, и старый ключ уходит и там.
rm -f "$META"; printf '%s\n' "$legacy" >"$TOKEN"; chmod 600 "$TOKEN"
printf 'stale\n' >"$GOAMNEZIA_DIR/.migrated_$GA_VER"
act lang-set --lang=ru
check "та же версия, код сменился (повтор шагов): ключ без .meta удалён и здесь, отметка — новый отпечаток" \
    '[ "$rc" = 0 ] && [ ! -e "$TOKEN" ] && [ "$(cat "$GOAMNEZIA_DIR/.migrated_$GA_VER")" != stale ]' "rc=$rc $(tail -5 "$ERR")"

DONE=1
printf '\nИТОГ: пройдено %s, не прошло %s\n' "$pass" "$fail"
[ "$fail" = 0 ]
