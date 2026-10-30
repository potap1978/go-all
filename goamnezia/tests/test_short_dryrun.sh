#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон коротких ссылок подписки с 2FA через Telegram-бота: lib/shortlinks.py, обёртки
# short-* в lib/subs.sh, мост install.sh --action=short-*, маршрут /<КОД> службы lib/pages_server.py, кнопки страницы
# подключения и локация nginx (docs/SUBSCRIPTION.md §11)
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_short_dryrun.sh
# Нужны: bash ≥ 5, jq, openssl, python3, curl. Docker, systemd и nginx не нужны: профили ставятся в dry-run, vhost
# пишется во временный GOAMNEZIA_NGINX_DIR. Служба поднимается ПО-НАСТОЯЩЕМУ на петле и ходит за ключами в настоящий
# install.sh --action=user-key; адрес клиента — заголовок X-Real-IP (с петли служба ему верит, как за nginx).
# Время для окон 15 минут, 30 дней и лестницы банов двигаем правкой subs/.short/state.json (ждать сутки нельзя);
# пороги во времени по часам проверяют юнит-тесты модуля (tests/test_shortlinks.py).
#
# Что проверяем:
#   без бота коротких ссылок нет (short_need_bot, short_available=false, /<КОД> — 404) → бот настроен: short-ensure
#   (алфавит, 1–3 тире, short.json 600, повтор — тот же код), short-list, sub-show (items[].short_url,
#   short_available) → нормализация ввода → служба: приложению без доверия 404, браузеру — страница подтверждения
#   (число, CSP с хешем скрипта), событие short_confirm без верного числа, short-pending без числа, один ожидающий
#   запрос, опрос ?r=, неверное и верное число (short-decide), полная ссылка после «Вход подтверждён» (POST /<КОД>
#   {request, nonce}: повтор — 409, выдуманный номер — 404 без баллов), окно 15 минут, первый забор — доверие 30 дней
#   со сдвигом, тело как у полного адреса → баны: новый неверный код — балл, повтор и кладбище — нет, три подряд — сутки, дальше
#   месяц, верный код между ними, IPv6 /64, свой клиент во время бана, short-bans/short-unban, тревога >100 за час →
#   5 запросов в час, истечение — не отказ → заморозка (сутки, месяц), снятие → «Это не я» — бан адреса → «Заменить
#   ссылку» и замена кнопкой ↻ (мост и страница) → гашение при перевыпуске, отзыве, удалении человека (и массовом) →
#   секретов нет в журналах и аудите → nginx: файлы сайта раньше кода, без журналов, роботам 404, пересборка vhost
#   шагом миграции.
#
# Ничего вне $TMP не пишем; слушатель — только на 127.0.0.1.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL="$ROOT/install.sh"
BASH5="${BASH5:-$BASH}"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-short.XXXXXX") || exit 2
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
export GOAMNEZIA_SHORT_CONFIRM_DIR="$TMP/run/short-confirm"
export GOAMNEZIA_BUILD_ROOT="$TMP/build"
export GOAMNEZIA_SERVER_IP="203.0.113.10"
export GOAMNEZIA_NGINX_DIR="$TMP/nginx"
export GOAMNEZIA_SITE_ROOT="$TMP/site"
export GOAMNEZIA_ACME_ROOT="$TMP/acme"
export GOAMNEZIA_LE_DIR="$TMP/letsencrypt"
export GOAMNEZIA_SYSTEMD_DIR="$TMP/systemd"
export GOAMNEZIA_TLS_DIR="$TMP/tls"
export GOAMNEZIA_MIRROR_DIR="$TMP/mirror"
export BOT_DIR="$TMP/bot" GOAMNEZIA_BOT_DIR="$TMP/bot"
export UPSTREAM_DIR="$TMP/upstream"
unset GOAMNEZIA_PAGES_DIR GOAMNEZIA_SUBS_DIR GOAMNEZIA_CLIENTS_JSON GOAMNEZIA_MIRROR_CURL
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$GOAMNEZIA_MIRROR_DIR"
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"
export NO_PROXY="127.0.0.1,localhost,::1${NO_PROXY:+,$NO_PROXY}" no_proxy="127.0.0.1,localhost,::1${no_proxy:+,$no_proxy}"

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 1200; return 0; }
skipped() { skip=$((skip + 1)); printf '  – %s (пропущено: %s)\n' "$1" "$2"; }
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
is_json() { jq -e . <<<"$1" >/dev/null 2>&1; }
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
audit_has() {   # audit_has <действие> [подстрока details]
    jq -R -e -n --arg a "$1" --arg d "${2:-}" \
        '[inputs | fromjson? // empty | select(.action == $a and ($d == "" or ((.details // "") | contains($d))))] | length > 0' \
        "$GOAMNEZIA_AUDIT_LOG" >/dev/null 2>&1
}

SUBS_DIR="$GOAMNEZIA_DIR/subs"
STATE="$SUBS_DIR/.short/state.json"
CONFIRM="$GOAMNEZIA_SHORT_CONFIRM_DIR"
NOTIFY="$GOAMNEZIA_NOTIFY_DIR"
CONF="$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia"
SHORT_PY="$ROOT/lib/shortlinks.py"
CODE_RE='^[0-9ABCDEFGHJKMNPQRSTVWXYZ]{8}$'
HDR="$TMP/hdr.txt"
BODY="$TMP/body.bin"
code=""
# get <путь> [доп. аргументы curl] → код в $code, тело в $BODY, заголовки в $HDR
get() {
    local path="$1"; shift
    code=$(curl -s --max-time 30 -D "$HDR" -o "$BODY" -w '%{http_code}' "$@" "http://127.0.0.1:$PORT$path" 2>/dev/null) || code="000"
}
hdr() { grep -i "^$1:" "$HDR" | head -1 | tr -d '\r' | sed -E 's/^[^:]+: ?//'; }
UA_IPHONE="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
HTML_ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
# browser <путь> <адрес> — как браузер (Accept: text/html); app <путь> <адрес> — как приложение подписки
browser() { get "$1" -H "X-Real-IP: $2" -H "Accept: $HTML_ACCEPT" -A "$UA_IPHONE"; }
app() { get "$1" -H "X-Real-IP: $2" -H "Accept: */*" -A "clash-verge/v2.4.2"; }
poll() { get "$1?r=$2" -H "X-Real-IP: ${3:-192.0.2.200}" -H "Accept: application/json" -A "$UA_IPHONE"; }
# Мост — настоящий install.sh --action=… --json (как его зовут бот, панель и SSH)
ACTION_BIN="$TMP/goamnezia-action"
printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$BASH5" "$INSTALL" >"$ACTION_BIN"
chmod 755 "$ACTION_BIN"
bridge() { run "$ACTION_BIN" "$@" --json; out=$(tail -n 1 "$OUT"); }
# Состояние: python поверх state.json (сдвиг времени — правкой сроков, под тем же замком, что у службы и моста)
state() { python3 -c 'import json,sys; print(json.dumps(eval(sys.argv[2], {"s": json.load(open(sys.argv[1]))})))' "$STATE" "$1" 2>/dev/null; }
state_edit() {  # state_edit '<python: s — состояние>'
    python3 - "$STATE" "$1" "$ROOT/lib" <<'EOPY'
import fcntl, json, os, sys, time
path, code, lib = sys.argv[1:4]
lock = os.open(os.path.join(os.path.dirname(path), ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(lock, fcntl.LOCK_EX)
s = json.load(open(path))
now = int(time.time())
exec(code, {"s": s, "now": now})
tmp = path + ".edit"
json.dump(s, open(tmp, "w"))
os.chmod(tmp, 0o600)
os.replace(tmp, path)
EOPY
}
events() { cat "$1"/*.json 2>/dev/null; }      # events <каталог очереди> → строки событий
nev() { find "$1" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' '; }
last_event() { local f; f=$(find "$1" -maxdepth 1 -name '*.json' 2>/dev/null | sort | tail -n 1); [ -n "$f" ] && cat "$f"; }
sc_number() { sed -n 's/.*<b class="pg-sc__num">\([0-9]*\)<\/b>.*/\1/p' "$BODY" | head -1; }
sc_request() { sed -n 's/.*data-r="\([A-Za-z0-9_-]*\)".*/\1/p' "$BODY" | head -1; }
sc_state() { sed -n 's/.*data-state="\([a-z]*\)".*/\1/p' "$BODY" | head -1; }
# Неверное число запроса: любое из options, кроме верного
wrong_of() { jq -r --arg r "$1" --argjson n "$2" '.requests[] | select(.request == $r) | .options | map(select(. != $n)) | .[0]' <<<"$3"; }
NGINX_404_FILE="$TMP/nginx404.html"
python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import pages_server as p; open(sys.argv[2], "wb").write(p.NGINX_404)' "$ROOT/lib" "$NGINX_404_FILE"
is_nginx_404() { [ "$code" = 404 ] && cmp -s "$BODY" "$NGINX_404_FILE"; }

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
for f in lib/common.sh lib/i18n.sh lib/docker.sh lib/amnezia.sh lib/users.sh lib/export.sh lib/website.sh lib/pages.sh \
         lib/subs.sh lib/backup.sh lib/pages_server.py lib/shortlinks.py lib/awgkeys.py lib/lang/ru.d/short.sh \
         lib/lang/en.d/short.sh lib/nginx/goamnezia.conf.tpl; do
    [ -f "$ROOT/$f" ] || { printf '!! нет %s\n' "$f"; exit 2; }
done
for _b in jq openssl python3 curl; do
    command -v "$_b" >/dev/null 2>&1 || { printf '!! нет %s\n' "$_b"; exit 2; }
done
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia users export website preflight share pages subs mirror backup; do
    # shellcheck disable=SC1090
    source "$ROOT/lib/$_m.sh"
done
check "обёртки коротких ссылок в lib/subs.sh: subs_short_ensure, _list, _replace_request, _decide, _status, _pending, _bans, _unban" \
    'for f in subs_short_ensure subs_short_list subs_short_replace_request subs_short_decide subs_short_status subs_short_pending subs_short_bans subs_short_unban; do type "$f" >/dev/null || exit 1; done'
check "словарь short подхвачен (short_err_need_bot)" '[ -n "${I18N[short_err_need_bot]:-}" ]'

mkdir -p "$UPSTREAM_DIR/awg"
cp "$ROOT"/lib/upstream/awg/* "$UPSTREAM_DIR/awg/" 2>/dev/null
cp "$ROOT/lib/upstream/images.json" "$UPSTREAM_DIR/images.json" 2>/dev/null
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOC
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "pro", "lang": "ru", "domain": "example.com",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}, "panel": {"port": 1985, "miniapp_path": ""}, "site": {"template_id": "", "template_source": "", "cert": "letsencrypt"},
 "options": {"drop_icmp": false, "alt_svc": false}, "mcp": {"http_enabled": false, "http_port": 1986}}
EOC
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"
chmod 600 "$USERS_FILE"
mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR"
amnezia_profile_install awg2 40000 default >/dev/null 2>>"$ALL_ERR"
amnezia_profile_install awg3 40001 default >/dev/null 2>>"$ALL_ERR"
amnezia_profile_install awg1 40002 default >/dev/null 2>>"$ALL_ERR"
users_add anna "Анна" awg3,awg2,awg1 >/dev/null 2>>"$ALL_ERR"
users_add bob "Боб" awg2 >/dev/null 2>>"$ALL_ERR"
run subs_create anna
A_BASE=$(jget .base_url)
run subs_create bob
check "фикстура: Pro на example.com с Let's Encrypt, у anna линии 3.1/2.0/1.5, у bob — 2.0; подписки у обоих" \
    '[ -n "$A_BASE" ] && [ -f "$SUBS_DIR/anna/secret" ] && [ -f "$SUBS_DIR/bob/secret" ] && ga_site_domain_ready' "$A_BASE $(errs)"

# ── 1. Без бота коротких ссылок нет ─────────────────────────────────────────
section "без бота коротких ссылок нет: short_need_bot, short_available=false"
check "модуль: бот не настроен — available=false (нет каталога бота)" \
    '[ "$(python3 "$SHORT_PY" available | jq -r .available)" = false ]'
bridge --action=short-ensure --name=anna --suffix=all.yaml
check "мост short-ensure без бота → error short_need_bot, кода нет, short.json не заведён" \
    '[ "$(jget .status)" = error ] && [ "$(jget .code)" = short_need_bot ] && [ ! -f "$SUBS_DIR/anna/short.json" ] && grep -qF "2FA через Telegram-бота" <<<"$(jget .message)"' "$out"
mkdir -p "$BOT_DIR"
printf 'BOT_TOKEN=123456:ABCDEF\nALLOWED_IDS=\n' >"$BOT_DIR/.env"
bridge --action=short-ensure --name=anna --suffix=all.yaml
check "токен есть, а администраторов нет — бот не настроен: всё ещё short_need_bot" '[ "$(jget .code)" = short_need_bot ]' "$out"
bridge --action=sub-show --name=anna
check "sub-show без бота: short_available=false, у всех пунктов clients short_url пустой" \
    '[ "$(jget .status)" = success ] && [ "$(jget .short_available)" = false ] && [ "$(jget "[.clients[].items[].short_url] | unique | join(\",\")")" = "" ] && [ "$(jget "[.clients[].items[] | has(\"short_url\")] | all")" = true ]' \
    "$(jq -c '{short_available, s: [.clients[].items[].short_url] | unique}' <<<"$out" 2>&1)"

# ── 2. Бот настроен: коды ───────────────────────────────────────────────────
section "бот настроен: short-ensure, short-list, sub-show"
printf '# бот goAmnezia\nexport BOT_TOKEN="123456:ABCDEF"\nALLOWED_IDS=111111111, 222222222  # админы\n' >"$BOT_DIR/.env"
check "модуль: токен и администраторы в .env (export, кавычки, комментарий) — available=true" \
    '[ "$(python3 "$SHORT_PY" available | jq -r .available)" = true ]'
bridge --action=short-ensure --name=anna --suffix=all.yaml
A_YAML="$out"
A_CODE=$(jget .code); A_DISP=$(jget .display)
check "short-ensure anna all.yaml: success, код 8 знаков из 32 (без I, L, O, U), new=true, url https://example.com/<показ>" \
    '[ "$(jget .status)" = success ] && [[ "$A_CODE" =~ $CODE_RE ]] && [ "$(jget .new)" = true ] && [ "$(jget .url)" = "https://example.com/$A_DISP" ] && [ "$(jget .suffix)" = all.yaml ]' "$out"
check "показ — тот же код с 1–3 тире в промежутках (не в начале и не в конце)" \
    '[ "${A_DISP//-/}" = "$A_CODE" ] && n=$(tr -cd - <<<"$A_DISP" | wc -c | tr -d " ") && [ "$n" -ge 1 ] && [ "$n" -le 3 ] && [[ "$A_DISP" != -* ]] && [[ "$A_DISP" != *- ]] && [[ "$A_DISP" != *--* ]]' "$A_DISP"
check "short.json подписки: 600, суффикс → {code, display, created, generation: 1}; state.json и каталог .short — 600/700" \
    '[ "$(perm "$SUBS_DIR/anna/short.json")" = 600 ] && [ "$(jq -r ".[\"all.yaml\"] | [.code, .display, (.generation | tostring)] | join(\" \")" "$SUBS_DIR/anna/short.json")" = "$A_CODE $A_DISP 1" ] && jq -e ".[\"all.yaml\"].created | test(\"^[0-9]{4}-\")" "$SUBS_DIR/anna/short.json" >/dev/null && [ "$(perm "$SUBS_DIR/.short")" = 700 ] && [ "$(perm "$STATE")" = 600 ]' \
    "$(cat "$SUBS_DIR/anna/short.json" 2>&1) $(ls -la "$SUBS_DIR/.short" 2>&1)"
bridge --action=short-ensure --name=anna --suffix=all.yaml
check "повтор — тот же код и показ, new=false" '[ "$(jget .code)" = "$A_CODE" ] && [ "$(jget .display)" = "$A_DISP" ] && [ "$(jget .new)" = false ]' "$out"
bridge --action=short-ensure --name=anna --suffix=awg20.conf
A20_CODE=$(jget .code); A20_DISP=$(jget .display)
bridge --action=short-ensure --name=anna --suffix=awg10.conf
check "линии 1.0 у anna нет — short_no_line (код не создаётся); у awg20.conf свой код" \
    '[ "$(jget .code)" = short_no_line ] && [ "$(jq -r "has(\"awg10.conf\")" "$SUBS_DIR/anna/short.json")" = false ] && [[ "$A20_CODE" =~ $CODE_RE ]] && [ "$A20_CODE" != "$A_CODE" ]' "$out"
bridge --action=short-ensure --name=anna --suffix=mihomo.yaml
check "синоним (mihomo.yaml) и чужой суффикс — bad_arg моста: коды только у канонических суффиксов" '[ "$(jget .code)" = bad_arg ]' "$out"
bridge --action=short-ensure --name=ghost --suffix=all.yaml
check "подписки нет — sub_not_found" '[ "$(jget .code)" = sub_not_found ]' "$out"
bridge --action=short-list --name=anna
check "short-list anna: только созданные (all.yaml, awg20.conf) с code, display, url, created; short_available=true" \
    '[ "$(jget "[.codes[].suffix] | join(\",\")")" = "all.yaml,awg20.conf" ] && [ "$(jget ".codes[0] | keys | join(\",\")")" = "code,created,display,suffix,url" ] && [ "$(jget .short_available)" = true ]' "$out"
bridge --action=sub-show --name=anna
check "sub-show: short_available=true; short_url у пунктов all.yaml и awg20.conf — их короткие адреса, у остальных — пусто" \
    '[ "$(jget .short_available)" = true ] && [ "$(jget "[.clients[].items[] | select(.suffix == \"all.yaml\") | .short_url] | unique | join(\",\")")" = "https://example.com/$A_DISP" ] && [ "$(jget "[.clients[].items[] | select(.suffix == \"awg20.conf\") | .short_url] | unique | join(\",\")")" = "https://example.com/$A20_DISP" ] && [ "$(jget "[.clients[].items[] | select(.suffix != \"all.yaml\" and .suffix != \"awg20.conf\") | .short_url] | unique | join(\",\")")" = "" ]' \
    "$(jq -c '[.clients[].items[] | {suffix, short_url}] | unique' <<<"$out" 2>&1)"
cat >"$TMP/gen.py" <<'EOPY'
import collections, math, sys
sys.path.insert(0, sys.argv[1])
import shortlinks as s
codes = [s.new_code() for _ in range(10000)]
for code in codes:
    d = sum(1 for ch in code if ch.isdigit())
    assert d >= 2 and 8 - d >= 2, code                              # не меньше двух цифр и двух букв
c = collections.Counter("".join(codes))
assert set(c) == set(s.ALPHABET) and len(s.ALPHABET) == 32 and not set("ILOU") & set(c), sorted(c)
# Отбор без подгонки: доля цифр — как у равновероятного выбора из допустимых кодов (E[цифр] ≈ 3,01 из 8)
n = s.code_space()
share = sum(d * math.comb(8, d) * 10 ** d * 22 ** (8 - d) for d in range(2, 7)) / n / 8
for ch, v in c.items():
    want = 80000 * (share / 10 if ch.isdigit() else (1 - share) / 22)
    assert abs(v - want) < 0.1 * want, (ch, v, want)                 # больше 4 сигм запаса
assert n == 843227123200
for _ in range(200):
    code = s.new_code()
    shown = s.display_of(code)
    assert shown.replace("-", "") == code and 1 <= shown.count("-") <= 3 and "--" not in shown, shown
    assert not shown.startswith("-") and not shown.endswith("-"), shown
EOPY
check "генератор: 10 000 кодов — у каждого не меньше двух цифр и двух букв; только алфавит из 32, все 32 знака, ни одного I, L, O, U; частоты — как у равновероятного выбора из 843 227 123 200 допустимых кодов; показ — 1–3 тире в промежутках" \
    'python3 "$TMP/gen.py" "$ROOT/lib"' "$(python3 "$TMP/gen.py" "$ROOT/lib" 2>&1 | tail -2)"
cat >"$TMP/norm.py" <<'EOPY'
import sys
sys.path.insert(0, sys.argv[1])
import shortlinks as s
assert s.normalize("nv43-9bkx") == "NV439BKX" and s.normalize("-NV-43-9B-KX-") == "NV439BKX"
assert s.normalize("oo1lI0ab") == "001110AB" and s.normalize("NV4O9BKX") == "NV409BKX"
assert s.as_code("NV43-9BKX") == "NV439BKX" and s.as_code("nv43 9bkx") == "NV439BKX"
for bad in ("NV43-9BKU", "NV43.9BKX", "NV439BK", "NV439BKXZ", ""):
    assert s.normalize(bad) == "", bad
for word in ("download", "wp-admin", "phpmyadmin", "robots", "contacts", "database", "password", "OOIIABCD"):
    assert s.as_code(word) == "", word                                # слово сайта — не код
assert s.normalize("download") == "D0WN10AD"                         # …хотя после O → 0, L → 1 стало бы им
for num in ("12345678", "2024-10-01", "20241001", "2024 10 01", "1234567A", "OOII1234", "1o1o-1o1o", "0000-0000"):
    assert s.as_code(num) == "", num                                  # дата, номер: букв меньше двух — не код
assert s.as_code("123456AB") == "123456AB" and s.as_code("OAIB2345") == "0A1B2345" and s.as_code("nv4o-9bkx") == "NV409BKX"
import random
rnd = random.Random(5)
for _ in range(2000):
    code = s.new_code()
    keep = rnd.choice([i for i, ch in enumerate(code) if ch.isdigit()])
    typed = "".join(ch if i == keep else rnd.choice("Oo") if ch == "0" else rnd.choice("IiLl") if ch == "1"
                    else ch.lower() for i, ch in enumerate(code))
    assert s.as_code(typed[:4] + " " + typed[4:]) == code, (code, typed)   # честный ввод с O, I, L — тот же код
EOPY
check "нормализация ввода: тире, пробелы и регистр не важны, O → 0, I и L → 1 (NV43-9BKX и nv43 9bkx — код); U, точка и другая длина — не код; слова сайта (download, wp-admin, phpmyadmin, robots…) — не код: ни одной цифры, набранной цифрой; даты и номера (2024-10-01, 12345678, 1234567A) — не код: букв меньше двух; 2000 выданных кодов, набранных с O, I, L вместо 0 и 1, — коды" \
    'python3 "$TMP/norm.py" "$ROOT/lib"' "$(python3 "$TMP/norm.py" "$ROOT/lib" 2>&1 | tail -2)"

# ── 3. Служба: вход по ссылке ───────────────────────────────────────────────
section "служба: вход по короткой ссылке"
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
READY="$TMP/ready"
python3 "$ROOT/lib/pages_server.py" --dir "$GOAMNEZIA_DIR" --bind 127.0.0.1 --port "$PORT" \
    --action-bin "$ACTION_BIN" --audit "$GOAMNEZIA_AUDIT_LOG" --ready-file "$READY" >"$TMP/server.log" 2>&1 &
SERVER_PID=$!
for _i in $(seq 1 100); do [ -f "$READY" ] && break; sleep 0.1; done
check "служба поднялась на 127.0.0.1:$PORT" '[ -f "$READY" ] && kill -0 "$SERVER_PID" 2>/dev/null' "$(cat "$TMP/server.log")"
IP_A=198.51.100.10
app "/$A_DISP" "$IP_A"
check "приложение с недоверенного адреса — 404 байт в байт как у nginx, запроса и события нет" \
    'is_nginx_404 && [ "$(nev "$CONFIRM")" = 0 ]' "code=$code"
browser "/$(tr '[:upper:]' '[:lower:]' <<<"$A_DISP")" "$IP_A"
cp "$BODY" "$TMP/sc1.html"; cp "$HDR" "$TMP/sc1.hdr"
N1=$(sc_number); R1=$(sc_request)
check "браузер (код строчными) — 200, страница подтверждения: «Назовите администратору число», число 10–99, таймер 5:00, «2FA через Telegram-бота»" \
    '[ "$code" = 200 ] && [ "$(sc_state)" = pending ] && [ "$N1" -ge 10 ] && [ "$N1" -le 99 ] && grep -qF "Назовите администратору число" "$BODY" && grep -qF "<span id=\"sc-left\">5:00</span>" "$BODY" && grep -qF "Короткие ссылки работают с 2FA через Telegram-бота" "$BODY" && [[ "$R1" =~ ^[A-Za-z0-9_-]{16}$ ]]' \
    "code=$code $(head -c 600 "$BODY")"
cat >"$TMP/csp.py" <<'EOPY'
import base64, hashlib, re, sys
html = open(sys.argv[1], encoding="utf-8").read()
hdr = open(sys.argv[2], encoding="utf-8").read()
csp = re.search(r"(?im)^content-security-policy: (.*)$", hdr).group(1).strip()
scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
assert len(scripts) == 1, len(scripts)
digest = base64.b64encode(hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode("ascii")
assert "script-src 'unsafe-inline' 'sha256-%s'" % digest in csp, csp
assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp, csp
assert re.search(r"(?im)^cache-control: no-store", hdr) and re.search(r"(?im)^x-robots-tag: noindex", hdr)
assert "http://" not in html and "https://" not in html, "внешних ресурсов нет"
EOPY
check "CSP страницы: скрипт внутри разрешён своим sha256 (у CSP 2 — только им), без внешних источников; Cache-Control no-store, noindex" \
    'python3 "$TMP/csp.py" "$TMP/sc1.html" "$TMP/sc1.hdr"' "$(python3 "$TMP/csp.py" "$TMP/sc1.html" "$TMP/sc1.hdr" 2>&1 | tail -2)"
EV1=$(last_event "$CONFIRM")
check "событие short_confirm в своей очереди (каталог 700, файл 600, имя как у ga_notify): request, kind login, name, label, suffix, device по User-Agent, ip, geo, 10 разных чисел 10–99, expires" \
    '[ "$(nev "$CONFIRM")" = 1 ] && [ "$(perm "$CONFIRM")" = 700 ] && [ "$(perm "$(find "$CONFIRM" -name "*.json" | head -1)")" = 600 ] && find "$CONFIRM" -name "*.json" | grep -qE "/[0-9]{8}T[0-9]{6}Z_[0-9]+_[0-9]{4}\.json$" && jq -e --arg r "$R1" --arg ip "$IP_A" ".event == \"short_confirm\" and .request == \$r and .kind == \"login\" and .name == \"anna\" and .label == \"Анна\" and .suffix == \"all.yaml\" and .device == \"iPhone · Safari\" and .ip == \$ip and .geo == \"\" and (.options | length) == 10 and (.options | unique | length) == 10 and all(.options[]; . >= 10 and . <= 99) and (.expires | test(\"^[0-9]{4}-.*Z$\")) and (.ts | test(\"Z$\"))" <<<"$EV1" >/dev/null' "$EV1"
check "верного числа в событии нет: ни поля number, ни других чисел, кроме 10 кнопок (оно — одна из них)" \
    '! jq -e "has(\"number\")" <<<"$EV1" >/dev/null && jq -e --argjson n "$N1" "(.options | index(\$n)) != null" <<<"$EV1" >/dev/null && [ "$(jq -c "del(.options, .ts, .expires, .created, .left) | [.. | numbers]" <<<"$EV1")" = "[]" ]' "$EV1"
bridge --action=short-pending
PEND="$out"
check "short-pending (мост): запрос с options, без верного числа (поля number нет)" \
    '[ "$(jget .status)" = success ] && [ "$(jget ".requests | length")" = 1 ] && [ "$(jget ".requests[0].request")" = "$R1" ] && [ "$(jget ".requests[0] | has(\"number\")")" = false ] && [ "$(jget ".requests[0].options | length")" = 10 ] && [ "$(jget ".requests[0].kind")" = login ]' "$out"
browser "/$A_DISP" "$IP_A"
check "тот же адрес открыл ссылку снова — тот же запрос и число, нового события нет (один ожидающий на код)" \
    '[ "$(sc_request)" = "$R1" ] && [ "$(sc_number)" = "$N1" ] && [ "$(nev "$CONFIRM")" = 1 ]' "$(sc_request) $(sc_number)"
browser "/$A_DISP" 198.51.100.11
check "другой адрес, пока запрос ждёт, — «Уже ждём подтверждения» без нового запроса" \
    '[ "$code" = 200 ] && [ "$(sc_state)" = busy ] && grep -qF "Уже ждём подтверждения" "$BODY" && [ "$(nev "$CONFIRM")" = 1 ]' "$(sc_state)"
poll "/$A_DISP" "$R1"
check "опрос ?r=<запрос> (Accept: JSON) — {decision: pending, left}; кода и числа в ответе нет" \
    '[ "$code" = 200 ] && jq -e ".decision == \"pending\" and .left > 0 and .left <= 300" "$BODY" >/dev/null && ! grep -q "number" "$BODY" && ! grep -qF "$A_CODE" "$BODY"' "$(cat "$BODY")"
W1=$(wrong_of "$R1" "$N1" "$PEND")
bridge --action=short-decide --request="$R1" --answer="$W1" --who=bot:111111111
check "short-decide неверным числом → decision=wrong; status корня — конверт моста success" \
    '[ "$(jget .status)" = success ] && [ "$(jget .decision)" = wrong ]' "$out"
check "событие short_decided {request, status: wrong, by — кто нажал (who моста, bot:<id>)}" \
    'jq -e --arg r "$R1" ".event == \"short_decided\" and .request == \$r and .status == \"wrong\" and .by == \"bot:111111111\"" <<<"$(last_event "$CONFIRM")" >/dev/null' "$(last_event "$CONFIRM")"
"$ACTION_BIN" --action=short-decide --request="$R1" --answer="$N1" --who="Иван Петров" --json >"$OUT" 2>"$ERR"; who_bad=$(tail -n 1 "$OUT")
check "who моста — page, panel, ssh, bot или bot:<id>; прочее — bad_arg (до модуля)" '[ "$(jq -r .code <<<"$who_bad")" = bad_arg ]' "$who_bad"
bridge --action=short-decide --request="$R1" --answer="$N1" --who=bot:222222222
check "решает первый: повторное нажатие (уже верным числом) — прежний итог wrong, already, by первого" \
    '[ "$(jget .decision)" = wrong ] && [ "$(jget .already)" = true ] && [ "$(jget .by)" = "bot:111111111" ]' "$out"
poll "/$A_DISP" "$R1"
check "опрос: wrong, 1 из 3 подряд" 'jq -e ".decision == \"wrong\" and .strikes == 1" "$BODY" >/dev/null' "$(cat "$BODY")"
browser "/$A_DISP?r=$R1" "$IP_A"
check "без JavaScript (переход по ?r=) — та же страница с итогом «Неверное число … (1 из 3 подряд)» и «Новый запрос»" \
    '[ "$code" = 200 ] && [ "$(sc_state)" = wrong ] && grep -qF "<div data-sc=\"wrong\"><b class=\"pg-sc__big\">Неверное число</b>" "$BODY" && grep -qF "<span class=\"sc-strikes\">1</span> из 3 подряд" "$BODY" && grep -qF "Новый запрос" "$BODY"' "$(head -c 800 "$BODY")"
browser "/$A_DISP" "$IP_A"
N2=$(sc_number); R2=$(sc_request)
check "новый запрос — новый номер и число" '[ "$R2" != "$R1" ] && [[ "$R2" =~ ^[A-Za-z0-9_-]{16}$ ]] && [ -n "$N2" ]' "$R2 $N2"
bridge --action=short-decide --request="$R2" --answer="$N2" --who=bot:111111111
check "верное число → ok; адрес открыт для этого кода на 15 минут (state: open, срок ≈ now+900)" \
    '[ "$(jget .decision)" = ok ] && [ "$(state "s[\"access\"][\"$A_CODE $IP_A\"][\"s\"]")" = "\"open\"" ] && d=$(( $(state "s[\"access\"][\"$A_CODE $IP_A\"][\"u\"]") - $(date +%s) )) && [ "$d" -gt 880 ] && [ "$d" -le 900 ]' \
    "$out $(state 's["access"]')"
poll "/$A_DISP" "$R2"
check "опрос: ok («Вход подтверждён»), open_left ≈ 900" 'jq -e ".decision == \"ok\" and .open_left > 880" "$BODY" >/dev/null' "$(cat "$BODY")"
NO_STORE="no-store, no-cache, must-revalidate, private"
browser "/$A_DISP?r=$R2" "$IP_A"
SN=$(sed -n 's/.*data-n="\([A-Za-z0-9_-]*\)".*/\1/p' "$BODY" | head -1)
check "SHORT-03: итог «Вход подтверждён» (по ?r= с адреса, начинавшего вход): «Добавьте в приложение полную ссылку…», строка про короткую для телевизора и роутера, «Показать полную ссылку» (её открывает скрипт), одноразовое значение в разметке, «Показать содержимое» — ?go=<запрос> только по нажатию; автоперехода нет (ни скрипта, ни meta refresh), полного адреса подписки в HTML нет; no-store" \
    '[ "$code" = 200 ] && [ "$(sc_state)" = ok ] && [[ "$SN" =~ ^[A-Za-z0-9_-]{32}$ ]] && grep -qF "id=\"sc-reveal\" hidden>" "$BODY" && grep -qF "<span>Показать полную ссылку</span>" "$BODY" && grep -qF "Добавьте в приложение полную ссылку — она работает из любой сети без подтверждения." "$BODY" && grep -qF "Телевизору и роутеру можно оставить короткую: добавьте её в течение 15 минут — дальше из этой сети она продлевается с каждым обновлением." "$BODY" && grep -qF "<a class=\"pg-sc__more\" id=\"sc-content\" href=\"/$A_DISP?go=$R2\">Показать содержимое</a>" "$BODY" && ! grep -q "http-equiv" "$BODY" && ! grep -qF "location.replace" "$BODY" && ! grep -qF "/s-" "$BODY" && [ "$(hdr cache-control)" = "$NO_STORE" ]' \
    "code=$code $(sc_state) n=$SN $(hdr cache-control)"
# Полная ссылка — POST /<КОД> {request, nonce}: номер подтверждённого запроса и одноразовое значение отрисовки; телефон
# мог уже сменить сеть — адрес не проверяется (номер знает лишь страница, начавшая вход, значение служба дала только ей)
fpost() { get "/$A_DISP" -X POST -H 'Content-Type: application/json' -H 'Accept: application/json' -H "X-Real-IP: $3" -A "$UA_IPHONE" --data "{\"request\":\"$1\",\"nonce\":\"$2\"}"; }
EVN_FULL=$(nev "$CONFIRM")
fpost "$R2" "$SN" 198.51.100.14
cp "$BODY" "$TMP/full1.json"; c_full="$code"; cc_full=$(hdr cache-control)
check "SHORT-03: POST /<КОД> {request, nonce} — 200: kind link, url = полный адрес all.yaml, приложения и команды роутера (списки), блок html с адресом, next — следующее значение; no-store; из другой сети — тоже; в бот — ничего" \
    '[ "$c_full" = 200 ] && jq -e --arg u "${A_BASE}all.yaml" ".kind == \"link\" and .suffix == \"all.yaml\" and .url == \$u and (.next | test(\"^[A-Za-z0-9_-]{32}$\")) and (.html | contains(\"<div class=\\\"pg-addr\\\" id=\\\"sc-url\\\">\" + \$u + \"</div>\")) and (.apps | type) == \"array\" and (.commands | type) == \"array\"" "$TMP/full1.json" >/dev/null && [ "$cc_full" = "$NO_STORE" ] && [ "$(nev "$CONFIRM")" = "$EVN_FULL" ]' \
    "code=$c_full $cc_full $(head -c 600 "$TMP/full1.json")"
SN2=$(jq -r .next "$TMP/full1.json")
fpost "$R2" "$SN" "$IP_A"
check "…повтор того же значения — 409 {error: reveal}, без следующего" \
    '[ "$code" = 409 ] && jq -e ". == {\"error\": \"reveal\"}" "$BODY" >/dev/null' "code=$code $(head -c 300 "$BODY")"
fpost AAAAAAAAAAAAAAAA "$SN2" 203.0.113.90
check "…выдуманный номер запроса — 404 байт в байт как у nginx, без баллов (адреса нет в учёте промахов)" \
    'is_nginx_404 && [ "$(state "s[\"addrs\"].get(\"203.0.113.90\")")" = null ]' "code=$code $(state 's["addrs"]')"
fpost "$R2" "$SN2" "$IP_A"
check "…следующее значение из ответа — снова полная ссылка" \
    '[ "$code" = 200 ] && jq -e --arg u "${A_BASE}all.yaml" ".url == \$u" "$BODY" >/dev/null' "code=$code $(head -c 300 "$BODY")"
EVN_GO=$(nev "$CONFIRM")
browser "/$A_DISP?go=$R2" 198.51.100.13
cp "$BODY" "$TMP/moved.html"; c_moved="$code"; cc_moved=$(hdr cache-control)
bridge --action=short-pending
check "«Показать содержимое» (?go=<запрос>) с другого адреса (браузер сменил IPv4 ↔ IPv6 или сеть) — «Нужна новая проверка» и «Новый запрос» на адрес без метки; без самообновления, без кнопки полной ссылки и одноразового значения, без нового запроса в бот (short-pending пуст), no-store" \
    '[ "$c_moved" = 200 ] && grep -qF "data-state=\"moved\"" "$TMP/moved.html" && grep -qF "<b class=\"pg-sc__big\">Нужна новая проверка</b>" "$TMP/moved.html" && grep -qF "href=\"/$A_DISP\">Новый запрос</a>" "$TMP/moved.html" && ! grep -q "http-equiv" "$TMP/moved.html" && ! grep -qF "id=\"sc-reveal\"" "$TMP/moved.html" && ! grep -qF "data-n=" "$TMP/moved.html" && [ "$(nev "$CONFIRM")" = "$EVN_GO" ] && [ "$(jget ".requests | length")" = 0 ] && [ "$cc_moved" = "$NO_STORE" ]' \
    "code=$c_moved events=$(nev "$CONFIRM")/$EVN_GO $out"
app "/$A_DISP" 198.51.100.12
check "другой адрес так и не доверен — приложению 404" 'is_nginx_404' "code=$code"
get "$(sed -E 's#^https://example\.com##' <<<"$A_BASE")all.yaml" -A "clash-verge/v2.4.2"
cp "$BODY" "$TMP/full.yaml"; cp "$HDR" "$TMP/full.hdr"
FETCH0=$(jq -r .fetches "$SUBS_DIR/anna/state.json")
app "/$A_DISP" "$IP_A"
check "первый забор приложением в окне 15 минут — 200, тело и заголовки подписки — как у полного адреса all.yaml; забор учтён" \
    '[ "$code" = 200 ] && cmp -s "$BODY" "$TMP/full.yaml" && for h in content-type content-disposition profile-title profile-update-interval subscription-userinfo; do [ "$(hdr "$h")" = "$(grep -i "^$h:" "$TMP/full.hdr" | head -1 | tr -d "\r" | sed -E "s/^[^:]+: ?//")" ] || exit 1; done && [ "$(jq -r .fetches "$SUBS_DIR/anna/state.json")" -gt "$FETCH0" ]' \
    "code=$code $(head -c 200 "$BODY")"
check "…адрес стал доверенным на 30 дней (state: trust, срок ≈ now+30 сут)" \
    '[ "$(state "s[\"access\"][\"$A_CODE $IP_A\"][\"s\"]")" = "\"trust\"" ] && d=$(( $(state "s[\"access\"][\"$A_CODE $IP_A\"][\"u\"]") - $(date +%s) )) && [ "$d" -gt 2591000 ] && [ "$d" -le 2592000 ]' "$(state 's["access"]')"
state_edit "s['access']['$A_CODE $IP_A']['u'] = now + 10 * 86400"
app "/$A_DISP" "$IP_A"
check "окно доверия сдвигается с каждым забором: было 10 дней — снова 30" \
    '[ "$code" = 200 ] && d=$(( $(state "s[\"access\"][\"$A_CODE $IP_A\"][\"u\"]") - $(date +%s) )) && [ "$d" -gt 2591000 ]' "code=$code $(state 's["access"]')"
state_edit "s['access']['$A_CODE $IP_A']['u'] = now - 1"
app "/$A_DISP" "$IP_A"
check "30 дней без забора — доверие кончилось: приложению снова 404" 'is_nginx_404' "code=$code"
state_edit "s['access']['$A_CODE $IP_A'] = {'c': '$A_CODE', 'a': '$IP_A', 's': 'open', 'u': now - 1}"
app "/$A_DISP" "$IP_A"
check "15 минут после подтверждения прошли без забора — окно закрыто, приложению 404" 'is_nginx_404' "code=$code"
state_edit "s['access']['$A_CODE $IP_A'] = {'c': '$A_CODE', 'a': '$IP_A', 's': 'trust', 'u': now + 30 * 86400}"
app "/$A_DISP/" "$IP_A"; c_slash="$code"
A_SPACE=$(tr '[:upper:]' '[:lower:]' <<<"$A_DISP")
app "/${A_SPACE//-/%20}" "$IP_A"; c_space="$code"; cmp -s "$BODY" "$TMP/full.yaml" && s_space=1 || s_space=0
app "/${A_DISP//-/%2D}" "$IP_A"
check "код набран с косой в конце, с тире как %2D или строчными с пробелом (%20) вместо тире (nginx сравнивает раскодированный путь) — тот же код" \
    '[ "$c_slash" = 200 ] && [ "$c_space" = 200 ] && [ "$s_space" = 1 ] && [ "$code" = 200 ] && cmp -s "$BODY" "$TMP/full.yaml"' \
    "slash=$c_slash space=$c_space/$s_space enc=$code"
# Обрамление копирования из мессенджера и разметки: «КОД.», «(КОД)», ««КОД»», «*КОД*», «КОД…», «КОД%20» — тот же код
TAILS_BAD=""
for t in . ',' ';' : '!' %3F ')' %5D %22 "'" %C2%BB %E2%80%A6 %E2%80%9D %E2%80%99 %E2%80%93 %E2%80%94 %C2%A0 %20 \
         ').' '...' '.%20' './' '*' '>' '+' '~' %E2%80%A2 %09; do
    app "/$A_DISP$t" "$IP_A"
    { [ "$code" = 200 ] && cmp -s "$BODY" "$TMP/full.yaml"; } || TAILS_BAD="$TAILS_BAD [$t]=$code"
done
for f in '(|)' '%5B|%5D' '%C2%AB|%C2%BB' '%22|%22' '%E2%80%9C|%E2%80%9D' '*|*' '_|_' '%60|%60' '%3C|%3E' ':|' '.|' \
         '..|.' '%E2%80%A2%20|'; do
    app "/${f%%|*}$A_DISP${f#*|}" "$IP_A"
    { [ "$code" = 200 ] && cmp -s "$BODY" "$TMP/full.yaml"; } || TAILS_BAD="$TAILS_BAD [$f]=$code"
done
app "/${A_SPACE//-/%20}%E2%80%A6" "$IP_A"
{ [ "$code" = 200 ] && cmp -s "$BODY" "$TMP/full.yaml"; } || TAILS_BAD="$TAILS_BAD [строчными+пробелы+…]=$code"
check "обрамление копирования из мессенджера и разметки: хвост (. , ; : ! ? ) ] \" ' » …, типографские кавычки и апостроф, тире, * > + ~ •, таб, неразрывный пробел, %20 и их пары), знаки в начале и в конце ((КОД), [КОД], «КОД», \"КОД\", *КОД*, _КОД_, <КОД>, :КОД, .КОД, • КОД); строчными с пробелами вместо тире и многоточием — тот же код: доверенный адрес забирает подписку" \
    '[ -z "$TAILS_BAD" ]' "$TAILS_BAD"
FULL_DISP=$(grep -i "^content-disposition:" "$TMP/full.hdr" | head -1 | tr -d "\r" | sed -E "s/^[^:]+: ?//")
browser "/$A_DISP?go=$R2" "$IP_A"
check "«Показать содержимое» с доверенного адреса — тело как у полного адреса all.yaml, браузеру текстом (text/plain, inline с тем же именем файла), no-store" \
    '[ "$code" = 200 ] && cmp -s "$BODY" "$TMP/full.yaml" && [ "$(hdr content-type)" = "text/plain; charset=utf-8" ] && [ "${FULL_DISP%%;*}" = attachment ] && [ "$(hdr content-disposition)" = "inline${FULL_DISP#attachment}" ] && [ "$(hdr cache-control)" = "$NO_STORE" ]' \
    "code=$code $(hdr content-type) | $(hdr content-disposition) | $FULL_DISP"

# ── 4. Баны за подбор ────────────────────────────────────────────────────────
section "баны: балл — новый неверный код; повтор не считается; лестница сутки → месяц; IPv6 /64; свой клиент"
IP_W=203.0.113.77
LOG0=$(grep -c " -> " "$TMP/server.log")
MISS0=$(state 'sum(s["misses"].values())')
WORDS_OK=1
for w in download wp-admin phpmyadmin contacts database password Download WP-ADMIN 12345678 2024-10-01 20241001 \
         2024%2010%2001 1234567A; do
    browser "/$w" "$IP_W"
    is_nginx_404 || WORDS_OK=0
done
check "слова сайта (/download → D0WN10AD, /wp-admin, /phpmyadmin, /contacts…), даты и номера (/2024-10-01, /12345678, /1234567A) — не коды: обычный 404, ни балла, ни счёта промахов, ни строки в журнале службы" \
    '[ "$WORDS_OK" = 1 ] && [ "$(state "\"$IP_W\" in s[\"addrs\"]")" = false ] && [ "$(state "sum(s[\"misses\"].values())")" = "$MISS0" ] && [ "$(grep -c " -> " "$TMP/server.log")" = "$LOG0" ]' \
    "ok=$WORDS_OK $(state "s[\"addrs\"].get(\"$IP_W\")") misses=$(state 'sum(s["misses"].values())')/$MISS0 log=$(grep -c " -> " "$TMP/server.log")/$LOG0"
IP_C=203.0.113.50
browser "/ZZZZ2222" "$IP_C"; c_w1="$code"
browser "/ZZZZ2222" "$IP_C"
browser "/zzzz-2222" "$IP_C"
check "неверный код — 404 байт в байт как у nginx; повтор того же кода (в любом виде) балла не даёт: 1 балл" \
    '[ "$c_w1" = 404 ] && is_nginx_404 && [ "$(state "s[\"addrs\"][\"$IP_C\"][\"p\"]")" = 1 ] && [ "$(state "len(s[\"addrs\"][\"$IP_C\"][\"seen\"])")" = 1 ]' \
    "$(state "s[\"addrs\"].get(\"$IP_C\")")"
browser "/ZZZZ3333" "$IP_C"
app "/$A_DISP" "$IP_C"
check "второй новый — 2 балла; верный код между промахами (приложение получило 404) — счёт сначала" \
    'is_nginx_404 && [ "$(state "s[\"addrs\"][\"$IP_C\"][\"p\"]")" = 0 ]' "$(state "s[\"addrs\"].get(\"$IP_C\")")"
browser "/ZZZZ4444" "$IP_C"; browser "/ZZZZ5555" "$IP_C"
check "два новых после верного — 2 балла, бана ещё нет" \
    '[ "$(state "s[\"addrs\"][\"$IP_C\"][\"p\"]")" = 2 ] && [ "$(state "s[\"addrs\"][\"$IP_C\"].get(\"u\", 0)")" = 0 ]'
browser "/ZZZZ6666" "$IP_C"
check "третий подряд — бан адреса на сутки (уровень 1, причина misses), баллы сначала" \
    'd=$(( $(state "s[\"addrs\"][\"$IP_C\"][\"u\"]") - $(date +%s) )) && [ "$d" -gt 86000 ] && [ "$d" -le 86400 ] && [ "$(state "s[\"addrs\"][\"$IP_C\"][\"lv\"]")" = 1 ] && [ "$(state "s[\"addrs\"][\"$IP_C\"][\"why\"]")" = "\"misses\"" ] && [ "$(state "s[\"addrs\"][\"$IP_C\"][\"p\"]")" = 0 ]' \
    "$(state "s[\"addrs\"].get(\"$IP_C\")")"
EVN=$(nev "$CONFIRM")
browser "/$A20_DISP" "$IP_C"; c_b1="$code"
app "/$A_DISP" "$IP_C"
check "забаненному адресу — 404 и на живой код (и браузеру, и приложению): ни страницы, ни запроса в бот" \
    '[ "$c_b1" = 404 ] && is_nginx_404 && [ "$(nev "$CONFIRM")" = "$EVN" ]' "c=$c_b1"
state_edit "s['addrs']['$IP_C']['u'] = now - 1"
for x in ZZZZ7777 ZZZZ8888 ZZZZ9999; do browser "/$x" "$IP_C"; done
check "бан кончился, ещё три новых — следующая ступень: месяц (уровень 2)" \
    'd=$(( $(state "s[\"addrs\"][\"$IP_C\"][\"u\"]") - $(date +%s) )) && [ "$d" -gt 2591000 ] && [ "$d" -le 2592000 ] && [ "$(state "s[\"addrs\"][\"$IP_C\"][\"lv\"]")" = 2 ]' \
    "$(state "s[\"addrs\"].get(\"$IP_C\")")"
state_edit "s['addrs']['$IP_C']['u'] = now - 1"
for x in YYYY2222 YYYY3333 YYYY4444; do browser "/$x" "$IP_C"; done
check "…и дальше снова месяц (уровень 3)" \
    'd=$(( $(state "s[\"addrs\"][\"$IP_C\"][\"u\"]") - $(date +%s) )) && [ "$d" -gt 2591000 ] && [ "$(state "s[\"addrs\"][\"$IP_C\"][\"lv\"]")" = 3 ]'
for i in 1 2 3; do browser "/XXXX222$i" "2001:db8:1:2::1$i"; done
browser "/XXXX2224" "2001:db8:1:3::1"
check "IPv6 — подсеть /64: три адреса одной /64 — один бан «2001:db8:1:2::/64»; соседняя /64 — свой счёт (1 балл)" \
    '[ "$(state "s[\"addrs\"][\"2001:db8:1:2::/64\"][\"lv\"]")" = 1 ] && [ "$(state "s[\"addrs\"][\"2001:db8:1:3::/64\"][\"p\"]")" = 1 ] && [ "$(state "\"2001:db8:1:2::11\" in s[\"addrs\"]")" = false ]' \
    "$(state 'sorted(s["addrs"])')"
for x in WWWW2222 WWWW3333 WWWW4444; do browser "/$x" "$IP_A"; done
app "/$A_DISP" "$IP_A"; c_own="$code"; cp "$BODY" "$TMP/own.yaml"
app "/$A20_DISP" "$IP_A"
check "свой клиент: адрес IP_A забанен, но код, который он уже забирал, работает (200, то же тело); другой код с него — 404" \
    '[ "$(state "s[\"addrs\"][\"$IP_A\"][\"lv\"]")" = 1 ] && [ "$c_own" = 200 ] && cmp -s "$TMP/own.yaml" "$TMP/full.yaml" && is_nginx_404' "own=$c_own c=$code"
bridge --action=short-bans
check "short-bans: баны адресов (ip, target, level, until, reason: misses) — IP_C уровень 3, /64 и IP_A уровень 1" \
    '[ "$(jget .status)" = success ] && [ "$(jget "[.bans[] | select(.kind == \"ip\" and .target == \"$IP_C\") | .level] | first")" = 3 ] && [ "$(jget "[.bans[] | select(.target == \"2001:db8:1:2::/64\") | .level] | first")" = 1 ] && [ "$(jget "[.bans[] | select(.target == \"$IP_A\") | .reason] | first")" = misses ] && jget ".bans[0].until" | grep -qE "^[0-9]{4}-.*Z$"' "$out"
bridge --action=short-unban --target="$IP_A"
bridge2="$out"
bridge --action=short-unban --target="2001:db8:1:2::abcd"
check "short-unban: адрес (unbanned true, kind ip) и IPv6 любым адресом своей /64 — баны сняты, лестница сначала" \
    '[ "$(jq -r .unbanned <<<"$bridge2")" = true ] && [ "$(jq -r .kind <<<"$bridge2")" = ip ] && [ "$(jget .unbanned)" = true ] && [ "$(state "\"$IP_A\" in s[\"addrs\"]")" = false ] && [ "$(state "\"2001:db8:1:2::/64\" in s[\"addrs\"]")" = false ]' "$bridge2 $out"
bridge --action=short-unban --target=198.51.100.99
check "снимать нечего — unbanned false (status корня — success)" '[ "$(jget .unbanned)" = false ] && [ "$(jget .status)" = success ]' "$out"

section "тревога: неверных кодов со всех адресов больше 100 за час — short_alarm в обычную очередь, раз в час"
for i in $(seq 1 100); do browser "/VVVV$((2000 + i))" "10.9.$((i / 200)).$((i % 200 + 1))"; done
check "short_alarm {misses_hour > 100} — в очереди notify (а не short-confirm), одно событие" \
    '[ "$(events "$NOTIFY" | jq -s "[.[] | select(.event == \"short_alarm\")] | length")" = 1 ] && events "$NOTIFY" | jq -s -e "[.[] | select(.event == \"short_alarm\")][0].misses_hour > 100" >/dev/null && ! events "$CONFIRM" | grep -q short_alarm' \
    "$(events "$NOTIFY")"
for i in 1 2 3; do browser "/VVVV900$i" "10.8.0.$i"; done
check "…и не чаще раза в час, сколько бы промахов ни шло" '[ "$(events "$NOTIFY" | jq -s "[.[] | select(.event == \"short_alarm\")] | length")" = 1 ]'

# ── 5. Пять запросов в час, истечение — не отказ ─────────────────────────────
section "запросы: не больше 5 в час на код; истечение пяти минут отказом не считается"
bridge --action=short-ensure --name=bob --suffix=all.yaml
B_CODE=$(jget .code); B_DISP=$(jget .display)
IP_B=198.51.100.20
for i in 1 2 3 4 5; do
    browser "/$B_DISP" "$IP_B"
    rq=$(sc_request)
    [ -n "$rq" ] && state_edit "s['requests']['$rq']['expires'] = now - 1"
done
browser "/$B_DISP" "$IP_B"
check "шестой запрос за час — «Слишком много запросов», нового события нет; пять прежних истекли" \
    '[ "$code" = 200 ] && [ "$(sc_state)" = limit ] && grep -qF "Слишком много запросов" "$BODY" && [ "$(events "$CONFIRM" | jq -s --arg c "bob" "[.[] | select(.event == \"short_confirm\" and .name == \$c)] | length")" = 5 ]' \
    "$(sc_state)"
check "истёкшие запросы закрыты событием short_decided {status: expired, by: \"\"}; серия неверных чисел кода не растёт" \
    '[ "$(events "$CONFIRM" | jq -s "[.[] | select(.event == \"short_decided\" and .status == \"expired\" and .by == \"\")] | length")" -ge 4 ] && [ "$(state "s[\"codes\"][\"$B_CODE\"].get(\"w\", 0)")" = 0 ]' \
    "$(state "s[\"codes\"].get(\"$B_CODE\")")"
state_edit "s['codes']['$B_CODE']['rq'] = []"
run subs_tick
check "тик моста закрывает ждущий истёкший запрос (как и служба раз в 5 с): событие expired" \
    '[ "$rc" = 0 ] && [ "$(state "[r for r, q in s[\"requests\"].items() if q[\"status\"] == \"pending\"]")" = "[]" ]' "$out $(errs)"

# ── 6. Заморозка кода ────────────────────────────────────────────────────────
section "заморозка: неверное число 3 раза подряд — сутки, следующие три — месяц; снятие"
IP_F=198.51.100.30
freeze_round() {   # freeze_round <адрес> — три запроса подряд, на каждый — неверное число; печатает итоги решений
    local n r w p
    for _ in 1 2 3; do
        browser "/$A20_DISP" "$1"
        n=$(sc_number); r=$(sc_request)
        p=$("$ACTION_BIN" --action=short-pending --json | tail -n 1)
        w=$(wrong_of "$r" "$n" "$p")
        "$ACTION_BIN" --action=short-decide --request="$r" --answer="$w" --who=panel --json | tail -n 1 | jq -r .decision
    done
}
FR=$(freeze_round "$IP_F" | tr '\n' ' ')
F_REQ=$(sc_request)
check "три неверных числа подряд: wrong, wrong, frozen; код заморожен на сутки (уровень 1)" \
    '[ "$FR" = "wrong wrong frozen " ] && d=$(( $(state "s[\"codes\"][\"$A20_CODE\"][\"fu\"]") - $(date +%s) )) && [ "$d" -gt 86000 ] && [ "$(state "s[\"codes\"][\"$A20_CODE\"][\"fl\"]")" = 1 ]' \
    "$FR $(state "s[\"codes\"].get(\"$A20_CODE\")")"
poll "/$A20_DISP" "$F_REQ"
check "опрос: frozen, на сутки" 'jq -e ".decision == \"frozen\" and .days == 1" "$BODY" >/dev/null' "$(cat "$BODY")"
EVN=$(nev "$CONFIRM")
browser "/$A20_DISP" "$IP_F"
check "замороженный код браузеру — страница «Короткая ссылка заморожена на сутки», запроса в бот нет" \
    '[ "$code" = 200 ] && [ "$(sc_state)" = frozen ] && grep -qF "Короткая ссылка заморожена <span data-days=\"1\">на сутки</span>" "$BODY" && [ "$(nev "$CONFIRM")" = "$EVN" ]' "$(head -c 600 "$BODY")"
state_edit "s['access']['$A20_CODE 198.51.100.31'] = {'c': '$A20_CODE', 'a': '198.51.100.31', 's': 'trust', 'u': now + 86400}"
app "/$A20_DISP" 198.51.100.31
check "доверенный адрес забирает и замороженный код (заморозка закрывает только новые входы)" '[ "$code" = 200 ] && grep -q "^\[Interface\]" "$BODY"' "code=$code"
bridge --action=short-bans
check "short-bans: заморозка кода — kind code, target — показ кода, level 1, reason wrong, name anna, suffix awg20.conf" \
    '[ "$(jget "[.bans[] | select(.kind == \"code\") | [.target, (.level | tostring), .reason, .name, .suffix] | join(\" \")] | first")" = "$A20_DISP 1 wrong anna awg20.conf" ]' "$out"
bridge --action=short-unban --target="$(tr '[:upper:]-' '[:lower:] ' <<<"$A20_DISP")"
check "short-unban кодом (строчными, пробел вместо тире) — заморозка снята (unbanned, kind code)" \
    '[ "$(jget .unbanned)" = true ] && [ "$(jget .kind)" = code ] && [ "$(state "s[\"codes\"][\"$A20_CODE\"].get(\"fu\", 0)")" = 0 ]' "$out"
state_edit "s['codes']['$A20_CODE']['rq'] = []"
browser "/$A20_DISP" "$IP_F"
R_ok=$(sc_request); N_ok=$(sc_number)
bridge --action=short-decide --request="$R_ok" --answer="$N_ok" --who=panel
FR=$(freeze_round 198.51.100.32 | tr '\n' ' ')
check "верное число обнуляет серию; снова три неверных подряд — заморозка на сутки (снятие начинает лестницу заново)" \
    '[ "$FR" = "wrong wrong frozen " ] && [ "$(state "s[\"codes\"][\"$A20_CODE\"][\"fl\"]")" = 1 ]' "$FR $(state "s[\"codes\"].get(\"$A20_CODE\")")"
state_edit "s['codes']['$A20_CODE']['fu'] = now - 1; s['codes']['$A20_CODE']['rq'] = []"
FR=$(freeze_round 198.51.100.33 | tr '\n' ' ')
F_REQ=$(sc_request)
poll "/$A20_DISP" "$F_REQ"
check "следующие три — заморозка на месяц (уровень 2), опрос: frozen, days 30" \
    '[ "$FR" = "wrong wrong frozen " ] && d=$(( $(state "s[\"codes\"][\"$A20_CODE\"][\"fu\"]") - $(date +%s) )) && [ "$d" -gt 2591000 ] && [ "$(state "s[\"codes\"][\"$A20_CODE\"][\"fl\"]")" = 2 ] && jq -e ".days == 30" "$BODY" >/dev/null' \
    "$FR $(state "s[\"codes\"].get(\"$A20_CODE\")") $(cat "$BODY")"
state_edit "s['codes']['$A20_CODE']['rq'] = []"
bridge --action=short-replace-request --name=anna --suffix=awg20.conf --who=panel
check "замена разрешена и у замороженного кода (её просит владелец ссылки)" '[ "$(jget .status)" = success ] && [[ "$(jget .request)" =~ ^[A-Za-z0-9_-]{16}$ ]]' "$out"
RP_REQ=$(jget .request); RP_NUM=$(jget .number)
bridge --action=short-decide --request="$RP_REQ" --answer="$RP_NUM" --who=panel
A20_NEW=$(jget .display)
check "…верное число — новый код (display и url рядом с decision), старый — в кладбище, заморозка ушла вместе с ним" \
    '[ "$(jget .decision)" = ok ] && [ "${A20_NEW//-/}" != "$A20_CODE" ] && [ "$(jget .url)" = "https://example.com/$A20_NEW" ] && [ "$(state "\"$A20_CODE\" in s[\"graveyard\"]")" = true ] && [ "$(state "\"$A20_CODE\" in s[\"codes\"]")" = false ] && [ "$(jq -r ".[\"awg20.conf\"].display" "$SUBS_DIR/anna/short.json")" = "$A20_NEW" ]' "$out"

# ── 7. «Это не я» ────────────────────────────────────────────────────────────
section "«Это не я»: запрос отклонён, адрес закрыт для коротких ссылок по лестнице банов"
IP_G=198.51.100.40
state_edit "s['codes'].setdefault('$B_CODE', {})['rq'] = []"
browser "/$B_DISP" "$IP_G"
G_REQ=$(sc_request)
bridge --action=short-decide --request="$G_REQ" --answer=deny --who=bot:111111111
check "deny → decision denied; адрес забанен на сутки (уровень 1, причина denied)" \
    '[ "$(jget .decision)" = denied ] && [ "$(state "s[\"addrs\"][\"$IP_G\"][\"why\"]")" = "\"denied\"" ] && [ "$(state "s[\"addrs\"][\"$IP_G\"][\"lv\"]")" = 1 ]' "$out"
poll "/$B_DISP" "$G_REQ" "$IP_G"
check "опрос страницы с этого же адреса работает и в бане: denied, на сутки" 'jq -e ".decision == \"denied\" and .days == 1" "$BODY" >/dev/null' "$(cat "$BODY")"
browser "/$B_DISP" "$IP_G"
check "дальше этому адресу короткие ссылки — 404" 'is_nginx_404' "code=$code"
get "$(sed -E 's#^https://example\.com##' <<<"$A_BASE")awg20.conf" -H "X-Real-IP: $IP_G"
check "бан закрывает только короткие ссылки: полный адрес подписки с того же адреса — 200" '[ "$code" = 200 ]' "code=$code"

# ── 8. «↻ Заменить ссылку» из бота ───────────────────────────────────────────
section "«↻ Заменить ссылку» при входе: новый код, старый — в кладбище на 30 дней"
IP_H=198.51.100.50
browser "/$B_DISP" "$IP_H"
H_REQ=$(sc_request)
bridge --action=short-decide --request="$H_REQ" --answer=swap --who=bot:111111111
B2_DISP=$(jget .display)
check "swap → decision swapped, новый код (display, url) — администратору; short.json bob — новый код" \
    '[ "$(jget .decision)" = swapped ] && [ "${B2_DISP//-/}" != "$B_CODE" ] && [ "$(jget .url)" = "https://example.com/$B2_DISP" ] && [ "$(jq -r ".[\"all.yaml\"].display" "$SUBS_DIR/bob/short.json")" = "$B2_DISP" ]' "$out"
poll "/$B_DISP" "$H_REQ" "$IP_H"
check "опрос страницы: swapped — и без нового кода (открывший ссылку мог быть чужим)" \
    'jq -e ".decision == \"swapped\"" "$BODY" >/dev/null && ! grep -qF "${B2_DISP//-/}" "$BODY" && ! grep -qF "$B2_DISP" "$BODY"' "$(cat "$BODY")"
check "старый код — в кладбище до now+30 сут (why swap)" \
    'd=$(( $(state "s[\"graveyard\"][\"$B_CODE\"][\"until\"]") - $(date +%s) )) && [ "$d" -gt 2591000 ] && [ "$(state "s[\"graveyard\"][\"$B_CODE\"][\"why\"]")" = "\"swap\"" ]'
IP_D=198.51.100.60
for _ in 1 2 3 4; do browser "/$B_DISP" "$IP_D"; done
check "стук по кладбищу — 404 и ни одного балла (старый клиент не банится)" 'is_nginx_404 && [ "$(state "\"$IP_D\" in s[\"addrs\"]")" = false ]' "$(state "s[\"addrs\"].get(\"$IP_D\")")"

# SL-UX-03: чужой ожидающий вход не мешает администратору — замена из панели закрывает его («заменён», by = who)
bridge --action=short-ensure --name=bob --suffix=all.txt
T_DISP=$(jget .display)
browser "/$T_DISP" 198.51.100.77
T_RID=$(sed -n 's/.*data-r="\([A-Za-z0-9_-]*\)".*/\1/p' "$BODY" | head -1)
bridge --action=short-replace-request --name=bob --suffix=all.txt --who=page
T_PAGE=$(jget .code)
bridge --action=short-replace-request --name=bob --suffix=all.txt --who=panel
check "SL-UX-03: при ожидающем входе замена из панели проходит, вход закрыт событием short_decided (swapped, by panel); со страницы — short_pending, как раньше" \
    '[ -n "$T_RID" ] && [ "$T_PAGE" = short_pending ] && [ "$(jget .status)" = success ] && [ -n "$(jget .request)" ] && [ "$(state "s[\"requests\"][\"$T_RID\"][\"status\"]")" = "\"swapped\"" ] && [ "$(state "s[\"requests\"][\"$T_RID\"][\"by\"]")" = "\"panel\"" ] && [ "$(cat "$CONFIRM"/*.json | jq -s --arg r "$T_RID" "[.[] | select(.event == \"short_decided\" and .request == \$r and .status == \"swapped\" and .by == \"panel\")] | length")" = 1 ]' \
    "rid=$T_RID page=$T_PAGE $out"
T_REQ=$(jget .request)
bridge --action=short-replace-request --name=bob --suffix=all.txt --who=page
T_PAGE2=$(jget .code)
bridge --action=short-replace-request --name=bob --suffix=all.txt --who=ssh
check "SL-UX-03: ожидающая замена тоже не держит администратора — замена из SSH проходит, прежняя закрыта (swapped, by ssh); со страницы — short_pending" \
    '[ "$T_PAGE2" = short_pending ] && [ "$(jget .status)" = success ] && [ "$(jget .request)" != "$T_REQ" ] && [ "$(state "s[\"requests\"][\"$T_REQ\"][\"status\"]")" = "\"swapped\"" ] && [ "$(state "s[\"requests\"][\"$T_REQ\"][\"by\"]")" = "\"ssh\"" ]' \
    "req=$T_REQ page=$T_PAGE2 $out"

# ── 9. Замена кнопкой ↻ через мост (панель, SSH) ─────────────────────────────
section "замена через бота (кнопка ↻ панели и SSH): число у просящего, 11 кнопок у администратора"
bridge --action=short-replace-request --name=anna --suffix=all.yaml --who=ssh
RQ="$out"; RQ_ID=$(jget .request); RQ_N=$(jget .number)
check "short-replace-request: {request, number 10–99, expires, left}" \
    '[ "$(jget .status)" = success ] && [ "$RQ_N" -ge 10 ] && [ "$RQ_N" -le 99 ] && jget .expires | grep -qE "Z$" && [ "$(jget .left)" -gt 0 ]' "$out"
check "событие short_confirm: kind replace, who ssh, без адреса и устройства, без верного числа" \
    'jq -e --arg r "$RQ_ID" ".event == \"short_confirm\" and .request == \$r and .kind == \"replace\" and .who == \"ssh\" and .ip == \"\" and .device == \"\" and (has(\"number\") | not) and (.options | length) == 10" <<<"$(last_event "$CONFIRM")" >/dev/null' "$(last_event "$CONFIRM")"
bridge --action=short-replace-request --name=anna --suffix=all.yaml --who=page
check "второй запрос на тот же код со страницы, пока первый ждёт, — short_pending (минуты в словах)" '[ "$(jget .code)" = short_pending ] && grep -qF "мин" <<<"$(jget .message)"' "$out"
bridge --action=short-status --request="$RQ_ID"
check "short-status (опрос панели и SSH): {decision: pending, kind: replace, suffix, expires, left}" \
    '[ "$(jget .decision)" = pending ] && [ "$(jget .kind)" = replace ] && [ "$(jget .suffix)" = all.yaml ] && [ "$(jget .status)" = success ]' "$out"
bridge --action=short-decide --request="$RQ_ID" --answer=swap --who=panel
check "у замены кнопки «Заменить ссылку» нет: swap — short_bad_answer" '[ "$(jget .code)" = short_bad_answer ]' "$out"
# Общий замок моста занят долгой операцией (подставной flock всегда отвечает «занято»): изменяющее short-ensure ждёт и
# получает lock_timeout, а решение по короткой ссылке идёт мимо общего замка и миграции — под своим замком состояния.
mkdir -p "$TMP/busybin"
printf '#!/bin/sh\necho "$*" >>"%s/flock.calls"\nexit 1\n' "$TMP" >"$TMP/busybin/flock"
chmod 755 "$TMP/busybin/flock"
PATH="$TMP/busybin:$PATH" "$ACTION_BIN" --action=short-ensure --name=anna --suffix=awg15.vpn --json >"$OUT" 2>"$ERR"
busy_ensure=$(tail -n 1 "$OUT")
: >"$TMP/flock.calls"
PATH="$TMP/busybin:$PATH" "$ACTION_BIN" --action=short-decide --request="$RQ_ID" --answer="$RQ_N" --who=panel --json >"$OUT" 2>"$ERR"
out=$(tail -n 1 "$OUT")
check "замок: при занятом общем замке short-ensure — lock_timeout, а short-decide проходит и общего замка не берёт (ни миграции, ни flock -w; проверка зависимостей flock --help — не замок)" \
    '[ "$(jq -r .code <<<"$busy_ensure")" = lock_timeout ] && [ "$(jget .status)" = success ] && [ "$(jget .decision)" = ok ] && ! grep -qE "(^| )-w " "$TMP/flock.calls"' \
    "$busy_ensure | $out | $(cat "$TMP/flock.calls" 2>/dev/null)"
A2_DISP=$(jget .display)
check "верное число → новый код; старый — в кладбище, доверие старого кода снято (IP_A приложению — 404 по старому)" \
    '[ "$(jget .decision)" = ok ] && [ "${A2_DISP//-/}" != "$A_CODE" ] && [ "$(state "s[\"graveyard\"][\"$A_CODE\"][\"why\"]")" = "\"replace\"" ] && [ "$(state "[k for k in s[\"access\"] if k.startswith(\"$A_CODE\")]")" = "[]" ]' "$out"
app "/$A_DISP" "$IP_A"
check "…по старому коду — 404" 'is_nginx_404' "code=$code"
bridge --action=short-status --request="$RQ_ID"
check "short-status после решения: ok и новый код (display, url) — тому, кто просил" \
    '[ "$(jget .decision)" = ok ] && [ "$(jget .display)" = "$A2_DISP" ] && [ "$(jget .url)" = "https://example.com/$A2_DISP" ]' "$out"
bridge --action=sub-show --name=anna
check "sub-show: у all.yaml — новый короткий адрес" \
    '[ "$(jget "[.clients[].items[] | select(.suffix == \"all.yaml\") | .short_url] | unique | join(\",\")")" = "https://example.com/$A2_DISP" ]' "$out"

# ── 10. Страница подключения: кнопки «Короткая ссылка» и ↻ ───────────────────
section "страница подключения: «Короткая ссылка» (POST short.json) и ↻ (POST short-replace.json)"
page_data() { python3 -c '
import re,sys
m=re.search(r"<script type=.application/json. id=.ga-data.>(.*?)</script>", open(sys.argv[1],encoding="utf-8").read(), re.S)
print(m.group(1) if m else "{}")' "$1"; }
run pages_create anna 24h
PG=$(jget .url | sed -E 's#^https://example\.com/p-([A-Za-z0-9_-]{43})/$#\1#')
get "/p-$PG/" -A "Mozilla/5.0 (Linux; Android 14)"
PD=$(page_data "$BODY")
RS=$(jq -r '.reveal_short // ""' <<<"$PD")
check "страница: sub.short=true (бот настроен), своё одноразовое значение reveal_short; кодов в HTML нет" \
    '[ "$code" = 200 ] && [ "$(jq -r .sub.short <<<"$PD")" = true ] && [[ "$RS" =~ ^[A-Za-z0-9_-]{32}$ ]] && [ "$RS" != "$(jq -r .reveal_sub <<<"$PD")" ] && ! grep -qF "$A2_DISP" "$BODY" && ! grep -qF "${A2_DISP//-/}" "$BODY"' "$(jq -c .sub <<<"$PD")"
spost() { get "/p-$PG/$1" -X POST -H 'Content-Type: application/json' -H "X-Real-IP: ${3:-198.51.100.70}" -A "$UA_IPHONE" --data "$2"; }
spost short.json "{\"nonce\":\"$RS\",\"suffix\":\"awg31.vpn\"}"
cp "$BODY" "$TMP/sj1.json"
NX=$(jq -r '.next // ""' "$TMP/sj1.json")
check "POST short.json (awg31.vpn): код создан — {short: {suffix, display, url, created}, next}; short.json anna — с awg31.vpn" \
    '[ "$code" = 200 ] && jq -e ".short.suffix == \"awg31.vpn\" and (.short.url | test(\"^https://example.com/[0-9A-Z-]{9,11}$\"))" "$TMP/sj1.json" >/dev/null && [[ "$NX" =~ ^[A-Za-z0-9_-]{32}$ ]] && [ "$NX" != "$RS" ] && [ "$(jq -r ".[\"awg31.vpn\"].display" "$SUBS_DIR/anna/short.json")" = "$(jq -r .short.display "$TMP/sj1.json")" ]' \
    "code=$code $(cat "$TMP/sj1.json")"
spost short.json "{\"nonce\":\"$RS\",\"suffix\":\"awg31.vpn\"}"
check "то же одноразовое значение второй раз — 409 «обновите страницу»" '[ "$code" = 409 ] && jq -e ".error == \"reveal\"" "$BODY" >/dev/null' "code=$code $(cat "$BODY")"
spost short.json "{\"nonce\":\"$NX\",\"suffix\":\"awg31.vpn\"}"
NX2=$(jq -r '.next // ""' "$BODY")
check "следующее значение (next) — тот же код (уже создан) и новое next" \
    '[ "$code" = 200 ] && [ "$(jq -r .short.display "$BODY")" = "$(jq -r .short.display "$TMP/sj1.json")" ] && [[ "$NX2" =~ ^[A-Za-z0-9_-]{32}$ ]]' "code=$code $(cat "$BODY")"
spost short.json "{\"nonce\":\"$NX2\",\"suffix\":\"awg10.conf\"}"
NX3=$(jq -r '.next // ""' "$BODY")
check "линии 1.0 у anna нет — 409 bad_suffix (и next, чтобы нажать снова)" '[ "$code" = 409 ] && jq -e ".error == \"bad_suffix\"" "$BODY" >/dev/null && [ -n "$NX3" ]' "code=$code $(cat "$BODY")"
get "/p-$PG/short.json" -X POST -H 'Content-Type: application/json' -A "TelegramBot (like TwitterBot)" --data "{\"nonce\":\"$NX3\",\"suffix\":\"all.yaml\"}"
check "роботу превью — 404" 'is_nginx_404' "code=$code"
post_sub() { get "/p-$PG/sub.json" -X POST -H 'Content-Type: application/json' -A "$UA_IPHONE" --data "{\"nonce\":\"$1\"}"; }
post_sub "$(jq -r .reveal_sub <<<"$PD")"
cp "$BODY" "$TMP/subj.json"
check "POST sub.json: поле short в форме адресов и только уже созданные коды — {mihomo, conf: {2.0}, vpn: {3.1}}; all.vpn и не созданных нет" \
    '[ "$code" = 200 ] && [ "$(jq -c ".sub.short | {k: (keys), conf: (.conf | keys), vpn: (.vpn | keys)}" "$TMP/subj.json")" = "{\"k\":[\"conf\",\"mihomo\",\"vpn\"],\"conf\":[\"2.0\"],\"vpn\":[\"3.1\"]}" ] && [ "$(jq -r .sub.short.mihomo "$TMP/subj.json")" = "https://example.com/$A2_DISP" ] && [ "$(jq -r ".sub.short.conf[\"2.0\"]" "$TMP/subj.json")" = "https://example.com/$A20_NEW" ]' \
    "code=$code $(jq -c .sub.short "$TMP/subj.json" 2>&1)"
spost short-replace.json "{\"nonce\":\"$NX3\",\"suffix\":\"awg31.vpn\"}" 198.51.100.71
cp "$BODY" "$TMP/pr1.json"
PR_ID=$(jq -r .request "$TMP/pr1.json"); PR_N=$(jq -r .number "$TMP/pr1.json"); NX4=$(jq -r .next "$TMP/pr1.json")
check "POST short-replace.json: {request, number, expires, left, next}; событие — kind replace, who page, адрес и устройство страницы" \
    '[ "$code" = 200 ] && [[ "$PR_ID" =~ ^[A-Za-z0-9_-]{16}$ ]] && [ "$PR_N" -ge 10 ] && [ "$PR_N" -le 99 ] && [ -n "$NX4" ] && jq -e --arg r "$PR_ID" ".request == \$r and .kind == \"replace\" and .who == \"page\" and .ip == \"198.51.100.71\" and .device == \"iPhone · Safari\" and .suffix == \"awg31.vpn\"" <<<"$(last_event "$CONFIRM")" >/dev/null' \
    "code=$code $(cat "$TMP/pr1.json") $(last_event "$CONFIRM")"
get "/p-$PG/short-status.json?r=$PR_ID" -A "$UA_IPHONE"
check "GET short-status.json — pending" '[ "$code" = 200 ] && jq -e ".decision == \"pending\" and .kind == \"replace\"" "$BODY" >/dev/null' "code=$code $(cat "$BODY")"
get "/p-$PG/short-status.json?r=$G_REQ" -A "$UA_IPHONE"
check "чужой запрос (вход по ссылке bob) со страницы anna — 404" 'is_nginx_404' "code=$code"
bridge --action=short-decide --request="$PR_ID" --answer="$PR_N" --who=bot:111111111
get "/p-$PG/short-status.json?r=$PR_ID" -A "$UA_IPHONE"
A31_NEW=$(jq -r .display "$BODY")
check "администратор нажал верное число — статус ok и новый код (display, url) для страницы" \
    '[ "$code" = 200 ] && jq -e ".decision == \"ok\" and (.url | test(\"^https://example.com/\"))" "$BODY" >/dev/null && [ "$A31_NEW" != "$(jq -r .short.display "$TMP/sj1.json")" ] && [ "$(jq -r ".[\"awg31.vpn\"].display" "$SUBS_DIR/anna/short.json")" = "$A31_NEW" ]' \
    "code=$code $(cat "$BODY")"

# ── 10а. Скрипт вкладки в node: «Короткая ссылка» и ↻ ───────────────────────
section "скрипт страницы (node, игрушечный DOM): «Короткая ссылка», ↻, телевизор, роутер"
if command -v node >/dev/null 2>&1; then
    get "/p-$PG/" -A "Mozilla/5.0 (Linux; Android 14)"; cp "$BODY" "$TMP/page_short.html"
    get "/p-$PG/app.js" -A "Mozilla/5.0 (Linux; Android 14)"; cp "$BODY" "$TMP/app_short.js"
    cat >"$TMP/sub_answer.json" <<'EOJ'
{"sub": {"mihomo": "https://example.com/s-AAAA/all.yaml", "txt": "https://example.com/s-AAAA/all.txt",
         "conf": {"2.0": "https://example.com/s-AAAA/awg20.conf"}, "vpn": {"2.0": "https://example.com/s-AAAA/awg20.vpn"},
         "lines": ["1.5", "2.0", "3.1"], "hours": 12, "title": "Анна", "short": {}}, "grant": "GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG"}
EOJ
    # Игрушечный DOM: тот же приём, что у tests/test_pages_dryrun.sh, плюс управляемые таймеры (опрос short-status) и
    # ответы short.json, short-replace.json, short-status.json. Нажатие «@метка» — кнопка по aria-label (↻ без текста),
    # «~текст» — нажатие из скрипта (isTrusted: false), «*» — дать сработать таймерам (опрос раз в 2 секунды).
    cat >"$TMP/render_short.js" <<'EOJS'
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const SUBJSON = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const CLICKS = (process.argv[5] || '').split('|').filter(Boolean);
const SHORT_FAIL = process.env.GA_SHORT_FAIL || '';
const m = html.match(/<script type="application\/json" id="ga-data">([\s\S]*?)<\/script>/);
const DATA = JSON.parse(m[1]);
if (process.env.GA_DEVICE) { DATA.device = process.env.GA_DEVICE; }
const LOG = [], COPIED = [];
let nonce = DATA.reveal_short, nseq = 0, polls = 0;
function N(tag) { this.tagName = tag; this.kids = []; this.attrs = {}; this.style = {}; this.t = ''; this.ls = {}; this.parentNode = null; this.id = ''; }
N.prototype.appendChild = function (c) { if (c.parentNode) { c.parentNode.removeChild(c); } this.kids.push(c); c.parentNode = this; return c; };
N.prototype.insertBefore = function (c, ref) { if (c.parentNode) { c.parentNode.removeChild(c); } var i = this.kids.indexOf(ref); if (i < 0) { this.kids.push(c); } else { this.kids.splice(i, 0, c); } c.parentNode = this; return c; };
N.prototype.removeChild = function (c) { this.kids = this.kids.filter(function (k) { return k !== c; }); c.parentNode = null; return c; };
N.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); if (k === 'id') { this.id = String(v); } };
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
function byId(n, id) { if (n.id === id) { return n; } for (var i = 0; i < n.kids.length; i++) { var f = byId(n.kids[i], id); if (f) { return f; } } return null; }
const store = {ga_tab: 'sub'};
let timers = [], tid = 0;
function later(fn) { tid += 1; timers.push({id: tid, fn: fn}); return tid; }
function cancel(id) { timers = timers.filter(function (t) { return t.id !== id; }); }
function reply(status, obj) {
  return Promise.resolve({status: status, ok: status === 200, headers: {get: function () { return null; }},
                          json: function () { return Promise.resolve(obj); }});
}
const win = {localStorage: {getItem: function (k) { return store[k] || null; }, setItem: function (k, v) { store[k] = String(v); }},
             btoa: function (s) { return Buffer.from(s, 'binary').toString('base64'); }, addEventListener: function () {},
             history: {pushState: function () {}}, location: {reload: function () {}}};
const nav = {clipboard: {writeText: function (s) { COPIED.push(s); return Promise.resolve(); }}};
vm.runInNewContext(fs.readFileSync(process.argv[3], 'utf8'), {
  document: {getElementById: function (id) { return id === 'ga-data' ? {textContent: JSON.stringify(DATA)} : (id === 'app' ? root : byId(root, id)); },
             createElement: function (tag) { return new N(tag); },
             createTextNode: function (s) { var n = new N('#text'); n.t = String(s); return n; }, body: new N('body'),
             documentElement: new N('html'), addEventListener: function () {}, removeEventListener: function () {}, title: ''},
  window: win, navigator: nav, Blob: function () {}, Date: Date,
  setTimeout: later, clearTimeout: cancel, setInterval: later, clearInterval: cancel,
  fetch: function (url, opts) {
    var body = {};
    try { body = JSON.parse((opts || {}).body || '{}'); } catch (e) { body = {}; }
    LOG.push('FETCH=' + url + '|' + ((opts || {}).method || 'GET') + '|' + (body.nonce === nonce ? 'nonce-ok' : body.nonce === DATA.reveal_sub ? 'sub-nonce' : 'nonce-' + (body.nonce || '')) + '|' + (body.suffix || ''));
    if (url === 'sub.json') { return reply(200, SUBJSON); }
    if (url === 'short.json' || url === 'short-replace.json') {
      if (body.nonce !== nonce) { return reply(409, {error: 'reveal'}); }
      nseq += 1; nonce = ('N' + nseq + 'nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn').slice(0, 32);
      if (SHORT_FAIL) { return reply(409, {error: SHORT_FAIL, next: nonce, left: 120}); }
      if (url === 'short.json') {
        return reply(200, {short: {suffix: body.suffix, display: 'K7QX-2M9A', url: 'https://example.com/K7QX-2M9A', created: ''}, next: nonce});
      }
      return reply(200, {request: 'RRRRRRRRRRRRRRRR', number: 42, expires: '', left: 300, next: nonce});
    }
    if (/^short-status\.json\?r=/.test(url)) {
      polls += 1;
      return reply(200, polls < 2 ? {decision: 'pending', left: 290} : {decision: 'ok', display: 'NEW2-CODE', url: 'https://example.com/NEW2-CODE'});
    }
    return new Promise(function () {});
  }
});
function find(node, text) {
  if (node.ls && node.ls.click) {
    if (text.charAt(0) === '@' ? node.getAttribute('aria-label') === text.slice(1) : node.textContent.split('\n')[0] === text) { return node; }
  }
  for (var i = 0; i < node.kids.length; i++) { var f = find(node.kids[i], text); if (f) { return f; } }
  return null;
}
setTimeout(function () {
  var i = 0;
  (function next() {
    if (i >= CLICKS.length) {
      setTimeout(function () {
        console.log(root.textContent);
        LOG.forEach(function (l) { console.log(l); });
        COPIED.forEach(function (c) { console.log('COPIED=' + c); });
        console.log('POLLS=' + polls);
      }, 20);
      return;
    }
    var text = CLICKS[i], synthetic = text.charAt(0) === '~', n, run;
    i += 1;
    if (text === '*') {
      run = timers; timers = [];
      run.forEach(function (t) { t.fn(); });
      setTimeout(next, 10);
      return;
    }
    if (synthetic) { text = text.slice(1); }
    n = find(root, text);
    if (!n) { console.log('NO-CLICK ' + text); } else { n.ls.click.forEach(function (fn) { fn(synthetic ? {isTrusted: false} : {}); }); }
    setTimeout(next, 10);
  })();
}, 20);
EOJS
    sjs() { JSOUT=$(node "$TMP/render_short.js" "$TMP/page_short.html" "$TMP/app_short.js" "$TMP/sub_answer.json" "$1" 2>&1); }
    sjs "Короткая ссылка"
    check "«Короткая ссылка» (FlClash): сначала POST sub.json, потом POST short.json своим значением страницы и суффиксом all.yaml; блок — домен и код, ↻, «Скопировать», «Тире и регистр…», плашка 2FA, три шага (третий — полная ссылка со страницы подтверждения), риск; строки «можно оставить короткую» у телефона нет" \
        'grep -qx "FETCH=sub.json|POST|sub-nonce|" <<<"$JSOUT" && grep -qx "FETCH=short.json|POST|nonce-ok|all.yaml" <<<"$JSOUT" && grep -qx "example.com/" <<<"$JSOUT" && grep -qx "K7QX-2M9A" <<<"$JSOUT" && grep -qx "Тире и регистр не важны, буква O читается как 0, I и L — как 1." <<<"$JSOUT" && grep -qx "Короткие ссылки работают с 2FA через Telegram-бота." <<<"$JSOUT" && grep -qx "Назовите число администратору — он нажмёт его в боте." <<<"$JSOUT" && grep -qx "Добавьте в FlClash полную ссылку кнопкой на странице подтверждения — она работает из любой сети." <<<"$JSOUT" && ! grep -qF "можно оставить короткую" <<<"$JSOUT" && grep -qF "Три неверных кода подряд — бан адреса на сутки." <<<"$JSOUT"' \
        "$(head -c 2500 <<<"$JSOUT")"
    sjs "~Короткая ссылка"
    check "нажатие из скрипта (isTrusted: false) ничего не просит" '! grep -q "^FETCH=" <<<"$JSOUT"' "$(grep "^FETCH" <<<"$JSOUT")"
    sjs "Короткая ссылка|@Скопировать короткую ссылку"
    check "«Скопировать» в блоке (своя подпись для читалки экрана: их на экране две) — короткий адрес в буфер" \
        'grep -qx "COPIED=https://example.com/K7QX-2M9A" <<<"$JSOUT" && [ "$(grep -c "^COPIED=" <<<"$JSOUT")" = 1 ]' "$(grep -E "^(COPIED|FETCH)" <<<"$JSOUT")"
    sjs "Короткая ссылка|@Заменить короткую ссылку"
    check "↻: POST short-replace.json следующим значением (из ответа short.json) — на странице «Замена короткой ссылки. Назовите администратору число», 42 и «Ждём подтверждения · 5:00»" \
        'grep -qx "FETCH=short-replace.json|POST|nonce-ok|all.yaml" <<<"$JSOUT" && grep -qx "Замена короткой ссылки. Назовите администратору число" <<<"$JSOUT" && grep -qx "42" <<<"$JSOUT" && grep -qx "Ждём подтверждения · 5:00" <<<"$JSOUT"' \
        "$(head -c 2500 <<<"$JSOUT")"
    sjs "Короткая ссылка|@Заменить короткую ссылку|*|*|*"
    check "…опрос short-status.json раз в 2 с: pending, затем ok — «Ссылка заменена…» и в блоке новый код" \
        'grep -qx "POLLS=2" <<<"$JSOUT" && grep -qx "Ссылка заменена: новая — выше, старая больше не работает." <<<"$JSOUT" && grep -qx "NEW2-CODE" <<<"$JSOUT" && ! grep -qx "K7QX-2M9A" <<<"$JSOUT" && grep -qx "FETCH=short-status.json?r=RRRRRRRRRRRRRRRR|GET|nonce-|" <<<"$JSOUT"' \
        "$(head -c 2500 <<<"$JSOUT")"
    GA_SHORT_FAIL=short_need_bot sjs "Короткая ссылка"
    check "бота сняли, пока страница открыта: «Короткие ссылки сейчас выключены…»" 'grep -qF "Короткие ссылки сейчас выключены: они работают только с 2FA через Telegram-бота" <<<"$JSOUT"' "$(head -c 1500 <<<"$JSOUT")"
    GA_DEVICE=tvos sjs ""
    check "телевизор: подпись кнопки «пультом, 2FA в Telegram», под рядом — «На телевизоре удобнее короткая ссылка…»" \
        'grep -qx "пультом, 2FA в Telegram" <<<"$JSOUT" && grep -qx "На телевизоре удобнее короткая ссылка: её легко набрать пультом, вход подтвердит администратор." <<<"$JSOUT"' "$(head -c 1500 <<<"$JSOUT")"
    GA_DEVICE=tvos sjs "Короткая ссылка"
    check "…шаг 1 на телевизоре — «в браузере телевизора или телефона в той же сети Wi-Fi»; под шагами — телевизору можно оставить и короткую (15 минут на первое обновление)" \
        'grep -qx "Откройте её в браузере телевизора или телефона в той же сети Wi-Fi — на экране появится число." <<<"$JSOUT" && grep -qx "Телевизору и роутеру можно оставить короткую: добавьте её в течение 15 минут после подтверждения — дальше из этой сети она продлевается с каждым обновлением." <<<"$JSOUT"' "$(head -c 2000 <<<"$JSOUT")"
    GA_DEVICE=router sjs "Короткая ссылка"
    check "роутер (Keenetic): в блоке — команда с коротким адресом и «Скопировать команду», шаг 3 — команда с полной ссылкой со страницы подтверждения, под шагами — роутеру можно оставить и короткую" \
        'grep -qx "interface Wireguard9 wireguard import https://example.com/K7QX-2M9A" <<<"$JSOUT" && grep -qx "Скопировать команду" <<<"$JSOUT" && grep -qx "Выполните на роутере команду с полной ссылкой — она на странице подтверждения." <<<"$JSOUT" && grep -qx "Телевизору и роутеру можно оставить короткую: добавьте её в течение 15 минут после подтверждения — дальше из этой сети она продлевается с каждым обновлением." <<<"$JSOUT" && grep -qx "FETCH=short.json|POST|nonce-ok|awg20.conf" <<<"$JSOUT"' \
        "$(grep -E "^FETCH|interface|Скопировать" <<<"$JSOUT") $(head -c 800 <<<"$JSOUT")"
else
    skipped "скрипт страницы в node" "нет node"
fi

# ── 11. Гашение: перевыпуск, отзыв, удаление человека ───────────────────────
section "гашение кодов: перевыпуск, отзыв, удаление пользователя (и массовое)"
browser "/$A2_DISP" 198.51.100.80
PEND_ROT=$(sc_request)
A_ALL=$(jq -r '[.[].code] | join(" ")' "$SUBS_DIR/anna/short.json")
bridge --action=sub-rotate --name=anna
check "sub-rotate: short.json пуст, все коды anna — в кладбище (why rotate); short_url пустые" \
    '[ "$(jget .status)" = success ] && [ "$(jq -c . "$SUBS_DIR/anna/short.json")" = "{}" ] && for c in $A_ALL; do [ "$(state "s[\"graveyard\"][\"$c\"][\"why\"]")" = "\"rotate\"" ] || exit 1; done && [ "$(jget "[.clients[].items[].short_url] | unique | join(\",\")")" = "" ] && [ "$(jget .short_available)" = true ]' \
    "$A_ALL $(cat "$SUBS_DIR/anna/short.json")"
check "ждущий вход по погашенному коду закрыт: short_decided expired" \
    'events "$CONFIRM" | jq -s -e --arg r "$PEND_ROT" "[.[] | select(.event == \"short_decided\" and .request == \$r and .status == \"expired\")] | length == 1" >/dev/null'
app "/$A2_DISP" "$IP_A"; c_rot="$code"
browser "/$A2_DISP" 198.51.100.81
check "погашенный код — 404 и браузеру, без балла" '[ "$c_rot" = 404 ] && is_nginx_404 && [ "$(state "\"198.51.100.81\" in s[\"addrs\"]")" = false ]' "c=$c_rot"
bridge --action=short-ensure --name=anna --suffix=all.yaml
check "новый код после перевыпуска — поколение 2" '[ "$(jget .new)" = true ] && [ "$(jq -r ".[\"all.yaml\"].generation" "$SUBS_DIR/anna/short.json")" = 2 ]' "$out"
B_ALL=$(jq -r '[.[].code] | join(" ")' "$SUBS_DIR/bob/short.json")
bridge --action=sub-revoke --name=bob
check "sub-revoke: коды bob — в кладбище (why revoked), каталог снят" \
    '[ "$(jget .status)" = success ] && [ ! -d "$SUBS_DIR/bob" ] && for c in $B_ALL; do [ "$(state "s[\"graveyard\"][\"$c\"][\"why\"]")" = "\"revoked\"" ] || exit 1; done' "$B_ALL $out"
users_add carol "Кэрол" awg2 >/dev/null 2>>"$ALL_ERR"
run subs_create carol
bridge --action=short-ensure --name=carol --suffix=awg20.vpn
C_CODE=$(jget .code)
run users_del carol
check "удаление человека гасит его коды (why user-del)" \
    '[ "$rc" = 0 ] && [ ! -d "$SUBS_DIR/carol" ] && [ "$(state "s[\"graveyard\"][\"$C_CODE\"][\"why\"]")" = "\"user-del\"" ]' "rc=$rc $(errs)"
users_add dave "Дэйв" awg2 >/dev/null 2>>"$ALL_ERR"
run subs_create dave
bridge --action=short-ensure --name=dave --suffix=all.txt
D_CODE=$(jget .code)
run users_del_bulk 1 dave "" ""
check "массовое удаление гасит коды каждого (why user-del)" \
    '[ "$rc" = 0 ] && [ ! -d "$SUBS_DIR/dave" ] && [ "$(state "s[\"graveyard\"][\"$D_CODE\"][\"why\"]")" = "\"user-del\"" ]' "rc=$rc $out $(errs)"

# ── 12. Секретов нет в журналах и аудите ─────────────────────────────────────
section "секретов нет в журналах и аудите"
ALL_CODES=$(python3 -c '
import json, sys, glob
codes = set(json.load(open(sys.argv[1]))["graveyard"])
for f in glob.glob(sys.argv[2] + "/*/short.json"):
    codes.update(e["code"] for e in json.load(open(f)).values())
print(" ".join(sorted(codes)))' "$STATE" "$SUBS_DIR")
check "в журнале службы, аудите, stderr ядра и журнале продукта нет ни одного кода (ни сплошного, ни показа с тире)" \
    'n=0; for c in $ALL_CODES; do for f in "$TMP/server.log" "$GOAMNEZIA_AUDIT_LOG" "$ALL_ERR" "$GOAMNEZIA_LOG_FILE"; do [ -f "$f" ] || continue; if grep -qF "$c" "$f"; then echo "$c in $f"; n=1; fi; done; done; [ "$n" = 0 ] && [ "$(wc -w <<<"$ALL_CODES" | tr -d " ")" -ge 8 ] && ! grep -qF "$A2_DISP" "$TMP/server.log" "$GOAMNEZIA_AUDIT_LOG"' \
    "$(for c in $ALL_CODES; do grep -lF "$c" "$TMP/server.log" "$GOAMNEZIA_AUDIT_LOG" "$ALL_ERR" 2>/dev/null; done | sort -u)"
check "аудит коротких ссылок: short-ensure, short-request, short-decide, short-ban, short-freeze, short-replace, short-kill, short-unban, short-trust, short-alarm — без чисел, кнопок и адресов" \
    'for a in short-ensure short-request short-decide short-ban short-freeze short-replace short-kill short-unban short-trust short-alarm; do audit_has "$a" || { echo "нет $a"; exit 1; }; done && ! jq -r "select(.action | startswith(\"short-\")) | .details" "$GOAMNEZIA_AUDIT_LOG" | grep -qE "number|options|198\.51\.100|203\.0\.113|2001:db8|10\.9\."' \
    "$(jq -r 'select(.action | startswith("short-")) | "\(.action) \(.details)"' "$GOAMNEZIA_AUDIT_LOG" | sort | uniq -c | head -30)"
check "в журнале службы нет адресов коротких ссылок и номеров запросов в строках запроса" \
    '! grep -qE "(GET|HEAD) /[0-9A-Za-z-]{8,12}" "$TMP/server.log" && ! grep -qF "$R1" "$TMP/server.log"' "$(grep -E "GET|HEAD" "$TMP/server.log" | head -5)"

# ── 13. nginx ────────────────────────────────────────────────────────────────
section "nginx: файлы сайта раньше кода, без журналов, роботам 404; пересборка vhost шагом миграции"
_ga_site_render_vhost pro example.com >"$TMP/vh.conf" 2>/dev/null
SHORT_LOC="location ~ \"${GA_SITE_SHORT_PATH_RE:-}\" {"
DOT_LOC="location ~ \"${GA_SITE_SHORT_DOT_RE:-}\" {"
HTTP_LOC="location ~ \"${GA_SITE_SHORT_HTTP_RE:-}\" {"
# ENVIRON, а не awk -v: в регулярке есть \x22 и \], а -v разбирает обратную косую
LOC="$SHORT_LOC" awk 'index($0, ENVIRON["LOC"]){c++} c==1{print} c==1&&/^    }/{exit}' "$TMP/vh.conf" >"$TMP/short443"
LOC="$DOT_LOC" awk 'index($0, ENVIRON["LOC"]){c++} c==1{print} c==1&&/^    }/{exit}' "$TMP/vh.conf" >"$TMP/shortdot"
REWRITE_LINE='rewrite "^/\.+(.*)$" "/$1" last;'
check "443: «/.КОД» — своя локация (GA_SITE_SHORT_DOT_RE) без журналов и без try_files: точки снимает rewrite, путь ищется заново — ни 403 сайта, ни строки в error.log, скрытый файл не отдаётся" \
    'grep -q "access_log off;" "$TMP/shortdot" && grep -q "error_log /dev/null crit;" "$TMP/shortdot" && grep -qF "$REWRITE_LINE" "$TMP/shortdot" && ! grep -q "try_files" "$TMP/shortdot"' \
    "$(cat "$TMP/shortdot")"
check "443: регулярная локация /<КОД> (GA_SITE_SHORT_PATH_RE: 8–14 знаков [0-9A-Za-z -] в обрамлении копирования, цифра и две буквы) — сначала файлы сайта (try_files \$uri \$uri/), потом служба (@ga_short); без журналов; if рядом с try_files нет" \
    'grep -qF "try_files \$uri \$uri/ @ga_short;" "$TMP/short443" && grep -q "access_log off;" "$TMP/short443" && grep -q "error_log /dev/null crit;" "$TMP/short443" && ! grep -q "if (" "$TMP/short443"' \
    "$(cat "$TMP/short443")"
awk '/location @ga_short \{/{f=1} f{print} f&&/^    }/{exit}' "$TMP/vh.conf" >"$TMP/shortpx"
check "@ga_short: роботам превью — 404 до службы (if с одним return); → 127.0.0.1:1987 с X-Real-IP; 404/502/504 службы → @ga_short_down (404 сайта); свой предел запросов; без журналов; защитные заголовки по одному" \
    'grep -qF "if (\$ga_short_bot) {" "$TMP/shortpx" && grep -A1 -F "if (\$ga_short_bot) {" "$TMP/shortpx" | grep -q "return 404;" && grep -q "proxy_pass http://127.0.0.1:1987;" "$TMP/shortpx" && grep -qF "proxy_set_header X-Real-IP \$remote_addr;" "$TMP/shortpx" && grep -q "error_page 404 502 504 =404 @ga_short_down;" "$TMP/shortpx" && grep -q "limit_req zone=goamnezia_short burst=20 nodelay;" "$TMP/shortpx" && grep -q "limit_req_status 404;" "$TMP/shortpx" && grep -q "access_log off;" "$TMP/shortpx" && grep -q "error_log /dev/null crit;" "$TMP/shortpx" && grep -q "add_header X-Frame-Options \"DENY\" always;" "$TMP/shortpx"' \
    "$(cat "$TMP/shortpx")"
check "@ga_short_down: return 404 без своих заголовков (SEC-G4) и без журнала" \
    'awk "/location @ga_short_down/{f=1} f && !/^[ \t]*#/{print} f&&/^    }/{exit}" "$TMP/vh.conf" | { ! grep -qE "add_header|expires"; } && awk "/location @ga_short_down/{f=1} f{print} f&&/^    }/{exit}" "$TMP/vh.conf" | grep -q "access_log off;"'
check "уровень http (до первого server): зоны goamnezia_short и карта \$ga_short_bot (telegrambot, whatsapp в начале строки…)" \
    'awk "/^server \{/{exit} {print}" "$TMP/vh.conf" | grep -q "limit_req_zone \$binary_remote_addr zone=goamnezia_short:1m rate=2r/s;" && awk "/^server \{/{exit} {print}" "$TMP/vh.conf" | grep -q "map \$http_user_agent \$ga_short_bot {" && awk "/^server \{/{exit} {print}" "$TMP/vh.conf" | grep -q "telegrambot" && awk "/^server \{/{exit} {print}" "$TMP/vh.conf" | grep -qF "\"~*^whatsapp/\" 1;"' \
    "$(awk '/^server \{/{exit} {print}' "$TMP/vh.conf")"
S2=$(grep -n '^server {' "$TMP/vh.conf" | sed -n 2p | cut -d: -f1)
L80=$(grep -nF "$HTTP_LOC" "$TMP/vh.conf" | head -1 | cut -d: -f1)
L443=$(grep -nF "$SHORT_LOC" "$TMP/vh.conf" | head -1 | cut -d: -f1)
LDOT=$(grep -nF "$DOT_LOC" "$TMP/vh.conf" | head -1 | cut -d: -f1)
SUBL=$(grep -nF 'location ~ "^/s-[A-Za-z0-9_-]{43}/(' "$TMP/vh.conf" | cut -d: -f1)
SITE=$(grep -n '^    location / {' "$TMP/vh.conf" | tail -1 | cut -d: -f1)
DENY=$(grep -nF '    location ~ /\. { deny all; }' "$TMP/vh.conf" | tail -1 | cut -d: -f1)
check "80: та же регулярка с точкой в начале (GA_SITE_SHORT_HTTP_RE) — редирект на HTTPS без строки в журнале; 443 — код и «/.КОД» после локации подписок и до location / сайта и запрета скрытых файлов" \
    '[ -n "$L80" ] && [ "$L80" -lt "$S2" ] && sed -n "$L80,$((L80 + 4))p" "$TMP/vh.conf" | grep -q "access_log off;" && sed -n "$L80,$((L80 + 4))p" "$TMP/vh.conf" | grep -qF "return 301 https://\$server_name\$request_uri;" && [ "$L443" -gt "$SUBL" ] && [ "$LDOT" -gt "$L443" ] && [ "$SITE" -gt "$LDOT" ] && [ "$DENY" -gt "$LDOT" ]' \
    "L80=$L80 S2=$S2 L443=$L443 LDOT=$LDOT SUBL=$SUBL SITE=$SITE DENY=$DENY"
cat >"$TMP/re.py" <<'EOPY'
import random, re, sys, urllib.parse
conf, lib, want_raw, dot_raw, http_raw = sys.argv[1:6]
sys.path.insert(0, lib)
import shortlinks as s
import pages_server as ps


def unescape(tok):
    """Как ngx_conf_read_token: \\" \\' \\\\ — сам знак, \\t \\r \\n — управляющие, прочее (\\x22, \\]) — как есть."""
    out, i = [], 0
    while i < len(tok):
        if tok[i] == "\\" and i + 1 < len(tok) and tok[i + 1] in "\"'\\trn":
            out.append({"t": "\t", "r": "\r", "n": "\n"}.get(tok[i + 1], tok[i + 1]))
            i += 2
        else:
            out.append(tok[i])
            i += 1
    return "".join(out)


# Локации обоих server в порядке файла: регулярные — PCRE без UTF-8, побайтно по раскодированному пути, как nginx
LOC = re.compile(r'^\s*location\s+(=|\^~|~\*|~)?\s*("(?:[^"\\]|\\.)*"|[^\s{]+)\s*\{')
servers = []
for line in open(conf, encoding="utf-8"):
    if re.match(r"^server\s*\{", line):
        servers.append({"rx": [], "pre": [], "eq": []})
        continue
    m = LOC.match(line)
    if not servers or not m or m.group(2).startswith("@"):
        continue
    tok = unescape(m.group(2)[1:-1] if m.group(2).startswith('"') else m.group(2))
    mod = m.group(1) or ""
    if mod in ("~", "~*"):
        servers[-1]["rx"].append((re.compile(tok.encode("utf-8"), re.I if mod == "~*" else 0), tok))
    else:
        servers[-1]["eq" if mod == "=" else "pre"].append((mod, tok))
assert len(servers) == 2, len(servers)
want, dot, http = unescape(want_raw), unescape(dot_raw), unescape(http_raw)
assert [t for _r, t in servers[0]["rx"]].count(http) == 1, [t for _r, t in servers[0]["rx"]]
assert [t for _r, t in servers[1]["rx"]].count(want) == 1 and [t for _r, t in servers[1]["rx"]].count(dot) == 1


def chosen(srv, path):
    """Кто ответит на путь: точная локация или ^~ — "prefix", иначе первая подходящая регулярная, None — location /."""
    if any(t == path for _m, t in srv["eq"]) or any(m == "^~" and path.startswith(t) for m, t in srv["pre"]):
        return "prefix"
    raw = path.encode("utf-8")
    for rx, tok in srv["rx"]:
        if rx.search(raw):
            return tok
    return None


def final443(path):
    """443: «/.КОД» — локация с rewrite "^/\.+(.*)$" "/$1" last: точки сняты, поиск локации заново. → (локация, путь)."""
    tok = chosen(servers[1], path)
    if tok == dot:
        path = "/" + re.match(r"^/\.+(.*)$", path).group(1)
        tok = chosen(servers[1], path)
    return tok, path


# Код в обрамлении копирования и с пробелами — на 443 в локации кода (без строки в журнале; «/.КОД» — через rewrite),
# на 80 — в редиректе без журнала; служба берёт то, что ей дойдёт, как тот же код
frames = list(s.FRAME)
tails = [""] + frames + [").", "\u00bb.", "...", ". ", "  ", "\u2026)", "\u201d.", "\u00a0\u2014", "*_", "\u2022\t"]
leads = [""] + [f for f in frames if f != "."] + ["((", "\u00ab\u201c", "*_`", ".", "..", ".(", "\u2022 ", "...", ".((("]
cases = []
for disp in ("NV43-9BKX", "nv43 9bkx", "NV439BKX", "Nv43 - 9bKx", "nv4o-9bkx"):
    cases += ["/" + disp + t + end for t in tails for end in ("", "/")]
    cases += ["/" + lead + disp + t for lead in leads for t in ("", ")", ").", "\u00bb")]
for path in cases:
    assert chosen(servers[0], path) == http, ("80", path, chosen(servers[0], path))
    tok, served = final443(path)
    assert tok == want and not served.startswith("/."), ("443", path, tok)
    got = ps.short_path(urllib.parse.quote(served, safe="/"))
    assert got == served and s.as_code(got.strip("/")) in ("NV439BKX", "NV409BKX"), (path, served, got)
for ok in ("/7Q-K2T-M9PZ", "/AB-CDEF-G1", "/9ABCDEFG", "/OAIB2345", "/123456AB"):
    assert final443(ok)[0] == want, ok
# Скрытый файл с именем «как код» сайт по-прежнему не отдаёт: после rewrite путь уже без точки
assert final443("/.htpasswd1") == (want, "/htpasswd1")
for no in ("/p-" + "A" * 43 + "/", "/s-" + "A" * 43 + "/all.yaml", "/a-" + "A" * 43 + "/mcp", "/app-0123456789ab",
           "/robots.txt", "/favicon.ico", "/index.html", "/NV43-9BKX/x", "/NV43-9BKX//", "/short", "/.well-known/acme",
           "/A_B_C_D_E", "/download", "/wp-admin", "/phpmyadmin", "/contacts", "/database", "/password",
           "/download/", "/about-us", "/about us", "/contact us", "/12345678", "/2024-10-01", "/20241001",
           "/2024 10 01", "/1234567A", "/OOII1234", "/12345678/", "/2024-10-01.", "/download.", "/12345678,",
           "/NV43-9BKX.html", "/NV43-9BKX.css", "/NV43.9BKX", "/NV43-9BKX/.", "/NV43-9BKX\u00a7", "/(download)",
           "/\u00ab2024-10-01\u00bb", "/.env", "/.git/config", "/.htaccess", "/((((NV43-9BKX", "/#NV43-9BKX",
           "/N(V43-9BKX"):
    assert final443(no)[0] != want and chosen(servers[0], no) != http, no
# Ни одного кода nginx не теряет: всё, что служба считает кодом (в обрамлении и без), он ведёт к ней без журнала
rnd = random.Random(3)
body = list("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz -")
seen = 0
for _ in range(100000):
    path = "/" + "".join(rnd.choice(frames) for _ in range(rnd.randint(0, 4))) + \
        "".join(rnd.choice(body) for _ in range(rnd.randint(8, 14))) + \
        "".join(rnd.choice(frames) for _ in range(rnd.randint(0, 7)))
    if ps.SHORT_PATH_RE.match(path) and s.as_code(path.strip("/")):
        seen += 1
        assert chosen(servers[0], path) == http and final443(path)[0] == want, path
assert seen > 100, seen
EOPY
check "nginx-конфиг (побайтно, по порядку локаций обоих server, с rewrite «/.КОД»): код в обрамлении копирования — знаки в начале ((КОД), «КОД», *КОД*, :КОД, .КОД, • КОД) и в конце (. , ) » … * > + ~ • таб, %20 и пары) — на 443 в локации кода без журнала (скрытые файлы не отдаются), на 80 — редирект без журнала, служба берёт его как код; секретные пути, файлы, подкаталоги, скрытые файлы, слова, даты и номера — не туда; 100 000 случайных путей — всё, что служба считает кодом, nginx ведёт к ней" \
    'python3 "$TMP/re.py" "$TMP/vh.conf" "$ROOT/lib" "${GA_SITE_SHORT_PATH_RE:-}" "${GA_SITE_SHORT_DOT_RE:-}" "${GA_SITE_SHORT_HTTP_RE:-}"' \
    "$(python3 "$TMP/re.py" "$TMP/vh.conf" "$ROOT/lib" "${GA_SITE_SHORT_PATH_RE:-}" "${GA_SITE_SHORT_DOT_RE:-}" "${GA_SITE_SHORT_HTTP_RE:-}" 2>&1 | tail -2)"
_ga_site_render_vhost selfsigned >"$TMP/vh-self.conf" 2>/dev/null
ga_config_set .site.subs_location false >/dev/null 2>&1
_ga_site_render_vhost pro example.com >"$TMP/vh-nosubs.conf" 2>/dev/null
ga_config_set .site.subs_location true >/dev/null 2>&1
check "без маршрута подписок и на самоподписанном — ни локации кода, ни карты, ни зон" \
    '! grep -q "ga_short\|goamnezia_short\|{8,14}" "$TMP/vh-self.conf" && ! grep -q "ga_short\|goamnezia_short\|{8,14}" "$TMP/vh-nosubs.conf"'
ga_with_config_lock _website_reapply_conf_locked >/dev/null 2>>"$ALL_ERR"
python3 - "$CONF" "$SHORT_LOC" <<'EOPY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
loc = sys.argv[2]
text = re.sub(r"\n    " + re.escape(loc) + r".*?\n    \}", "", text, flags=re.S)
text = re.sub(r"\n    location @ga_short(_down)? \{.*?\n    \}", "", text, flags=re.S)
open(sys.argv[1], "w", encoding="utf-8").write(text)
EOPY
had_old=$(grep -c "ga_short" "$CONF")
ga_with_config_lock _website_reapply_conf_locked >/dev/null 2>>"$ALL_ERR"
check "обновление: vhost прежнего кода (без локаций коротких ссылок) пересобирает шаг миграции — _website_reapply_conf_locked; он же в повторе при новом коде той же версии (_ga_migrate_reapply → _migrate_site_vhost reapply, отпечаток — lib/*.sh и lib/nginx/*)" \
    '[ "$had_old" -le 2 ] && grep -q "location @ga_short {" "$CONF" && grep -qF "try_files \$uri \$uri/ @ga_short;" "$CONF" && grep -q "_website_reapply_conf_locked" <<<"$(awk "/^_migrate_site_vhost\(\)/,/^}/" "$INSTALL")" && grep -q "_migrate_site_vhost reapply" <<<"$(awk "/^_ga_migrate_reapply\(\)/,/^}/" "$INSTALL")" && grep -qF "\"\$LIB_DIR\"/*.sh \"\$LIB_DIR\"/nginx/*" <<<"$(awk "/^_ga_code_fingerprint\(\)/,/^}/" "$INSTALL")"' \
    "had_old=$had_old $(grep -n "ga_short" "$CONF" | head -5)"
check "служба после обновления перезапускается и при правке lib/shortlinks.py; юнит знает, где .env бота (GOAMNEZIA_BOT_DIR)" \
    'grep -qF "\"\$SCRIPT_DIR/lib/awgkeys.py\" \"\$SCRIPT_DIR/lib/shortlinks.py\"" "$INSTALL" && _pages_unit_render | grep -qx "Environment=GOAMNEZIA_BOT_DIR=$BOT_DIR"'

# ── 14. Бот снят — коротких ссылок нет ───────────────────────────────────────
section "бот снят — коротких ссылок нет нигде"
bridge --action=short-ensure --name=anna --suffix=all.yaml
A3_DISP=$(jget .display)
state_edit "s['access']['${A3_DISP//-/} $IP_A'] = {'c': '${A3_DISP//-/}', 'a': '$IP_A', 's': 'trust', 'u': now + 86400}"
app "/$A3_DISP" "$IP_A"; c_on="$code"
rm -f "$BOT_DIR/.env"
app "/$A3_DISP" "$IP_A"
check "доверенный адрес забирал (200), бота сняли — та же ссылка отвечает 404" '[ "$c_on" = 200 ] && is_nginx_404' "on=$c_on off=$code"
get "/p-$PG/" -A "Mozilla/5.0 (Linux; Android 14)"
check "страница: sub.short=false и без reveal_short — третьей кнопки нет" \
    '[ "$(page_data "$BODY" | jq -r .sub.short)" = false ] && [ "$(page_data "$BODY" | jq -r "has(\"reveal_short\")")" = false ]'
bridge --action=sub-show --name=anna
check "sub-show: short_available=false и пустые short_url (код есть, но не откроется)" \
    '[ "$(jget .short_available)" = false ] && [ "$(jget "[.clients[].items[].short_url] | unique | join(\",\")")" = "" ]' "$out"
bridge --action=short-replace-request --name=anna --suffix=all.yaml --who=panel
check "замена без бота — short_need_bot" '[ "$(jget .code)" = short_need_bot ]' "$out"
check "служба отработала весь прогон без единого исключения (Traceback, «цикл обслуживания прерван», «уборка не прошла»)" \
    '! grep -qE "Traceback|прерван|уборка не прошла|ошибка" "$TMP/server.log"' "$(grep -E -B2 -A8 "Traceback|прерван|уборка" "$TMP/server.log" | head -40)"

DONE=1
printf '\nИТОГ: %d ✓  %d ✗  %d –\n' "$pass" "$fail" "$skip"
[ "$fail" = 0 ]
