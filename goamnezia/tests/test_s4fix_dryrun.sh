#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — S4-FIX (MTU-01, решение владельца 08.10): выданные профили 2.0 с большим S4 и MTU 1280 как стандарт.
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_s4fix_dryrun.sh
# Нужны: bash ≥ 5, jq, python3. Ничего вне $TMP не пишем, к Docker и серверам не ходим (GOAMNEZIA_DRY_RUN=1).
#
# Вживую 08.10 (.claude/work/stands/kmod-0810/report.md): профиль 2.0 с S4 = 142 даёт пакет данных 1482 байт, и на пути
# 1456 (шлюз Aeza) скачивание — 0,0 Мбит/с; генератор до 1.0.12 давал S4 15–150. Что проверяем:
#   1  MTU 1280 — по умолчанию у новых профилей всех линий; перегенерация оставляет выбранный человеком;
#   2  риск пакета (ga_amn_packet_risk) — при стандартном MTU 1280, а не при выбранном: S4 = 142 → риск, S4 = 40 → нет,
#      3.1 (S = 12) → нет, MTU 1420 при S4 ≤ 40 → нет; поле packet_risk в status (и brief) и profiles-status моста;
#   3  profile-fix-s4: у подхваченного с людьми приложения — adopted_keyless, ничего не тронуто; с согласием — S4 12–24 (S4-MARGIN),
#      размеры S1–S4 попарно различны, config.json и awg0.conf, рестарт контейнера, журнал; кому что делать —
#      подписка (забирали), страница, выданный ключ, ключ из приложения; риска больше нет, повтор — s4_safe;
#   4  страницы и подписки отдают новый S4: ключи у них — от моста (user-key), кеш службы страниц сбрасывается по
#      config.json (lib/pages_server.py Bridge);
#   5  отказы: 3.1 и 1.x — s4_safe словами; MTU ниже 1280 — отказ словами в ядре и мосту (туннель не поднимется у
#      клиентов с IPv6, L-02б); profile-mtu предупреждает только о пакете больше 1440 (path, L-02а), 1320 при S4 ≤ 40 —
#      без слов.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-s4fix.XXXXXX") || exit 2
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

pass=0; fail=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 1200; return 0; }
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
# act <действие> [--k=v …] — мост, как его зовут бот, панель и страницы: последняя строка stdout — в out.
act() {
    "$BASH5" "$INSTALL" --action="$1" --json "${@:2}" >"$OUT" 2>"$ERR"; rc=$?
    out=$(tail -n 1 "$OUT")
    return 0
}
param() { jq -r --arg p "$1" --arg k "$2" '.profiles[$p].params[$k] // ""' "$CONFIG_FILE"; }
setparams() {   # setparams <профиль> <JSON-объект параметров> — правка config.json «как было у выданного профиля»
    jq --arg p "$1" --argjson v "$2" '.profiles[$p].params += $v' "$CONFIG_FILE" >"$TMP/cfg.json" && mv "$TMP/cfg.json" "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
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

# ── 1. MTU 1280 — стандарт ───────────────────────────────────────────────────
section "1: MTU 1280 по умолчанию у новых профилей всех линий; перегенерация оставляет выбранный человеком"
run amnezia_profile_install awg2 40000 default
check "awg2 (2.0) поставлен: rc=0" '[ "$rc" = 0 ]' "$(errs)"
run amnezia_profile_install awg3 40001
check "awg3 (3.1) поставлен: rc=0" '[ "$rc" = 0 ]' "$(errs)"
run amnezia_profile_install awg1 40002
check "awg1 (1.x) поставлен: rc=0" '[ "$rc" = 0 ]' "$(errs)"
check "MTU = 1280 у всех трёх (config.json) и строкой MTU в конфиге сервера" \
    '[ "$(param awg1 MTU)" = 1280 ] && [ "$(param awg2 MTU)" = 1280 ] && [ "$(param awg3 MTU)" = 1280 ] && grep -q "^MTU = 1280$" "$STATE_DIR/amnezia-awg2/awg0.conf"' \
    "$(param awg1 MTU) $(param awg2 MTU) $(param awg3 MTU)"
check "новый 2.0 — S4 12–24 (S4-MARGIN), пакет данных 1280 + 80 + S4 не больше 1384 — запас 16 байт до 1400" \
    '[ "$(param awg2 S4)" -ge 12 ] && [ "$(param awg2 S4)" -le 24 ]' "S4=$(param awg2 S4)"
run amnezia_profile_set_mtu awg1 1376
run amnezia_profile_regen awg1 default
check "перегенерация оставляет MTU, выбранный человеком (1376)" '[ "$rc" = 0 ] && [ "$(param awg1 MTU)" = 1376 ]' "rc=$rc MTU=$(param awg1 MTU)"
jq '.profiles.awg1.params.MTU = ""' "$CONFIG_FILE" >"$TMP/c" && mv "$TMP/c" "$CONFIG_FILE"
run amnezia_profile_regen awg1 default
check "перегенерация профиля без MTU (подхваченный) — стандарт 1280" '[ "$rc" = 0 ] && [ "$(param awg1 MTU)" = 1280 ]' "rc=$rc MTU=$(param awg1 MTU)"

# ── 2. Риск пакета ───────────────────────────────────────────────────────────
section "2: риск пакета — при стандартном MTU 1280; поле packet_risk в status и profiles-status"
# Люди awg2: anna — ключ выдан; boris — со страницей подключения; vera — с подпиской, которую забирали; dima — подписка
# заведена, но её ни разу не забирали; gleb — подхвачен у приложения Amnezia (приватного ключа на сервере нет).
for _u in anna boris vera dima gleb; do run users_add "$_u" "" awg2; done
check "пять человек на awg2" '[ "$(jq "[.users[] | select(.peers.awg2 != null)] | length" "$USERS_FILE")" = 5 ]' "$(errs)"
jq '(.users[] | select(.name == "gleb") | .peers.awg2) |= (.privkey = "" | .imported = true) | (.users[] | select(.name == "gleb")) |= (.imported = true)' \
    "$USERS_FILE" >"$TMP/u" && mv "$TMP/u" "$USERS_FILE" && chmod 600 "$USERS_FILE"
# Выданный до 1.0.12 профиль 2.0: S4 = 142, размеры 148+40, 92+60, 64+30, 32+142 различны.
setparams awg2 '{"S1": "40", "S2": "60", "S3": "30", "S4": "142"}'
run ga_amn_packet_risk awg2
check "2.0, S4 = 142, MTU 1280 → риск: пакет 1502 > 1400, безопасный S4 ≤ 40, keyless = 1 (gleb)" \
    '[ "$(jget .packet)" = 1502 ] && [ "$(jget .budget)" = 1400 ] && [ "$(jget .s4)" = 142 ] && [ "$(jget .safe_s4)" = 40 ] && [ "$(jget .mtu)" = 1280 ] && [ "$(jget .keyless)" = 1 ]' "$out"
setparams awg2 '{"S4": "40"}'
run ga_amn_packet_risk awg2
check "S4 = 40 → риска нет (пакет ровно 1400)" '[ "$out" = null ]' "$out"
setparams awg2 '{"S4": "41"}'
run ga_amn_packet_risk awg2
check "S4 = 41 → риск (пакет 1401)" '[ "$(jget .packet)" = 1401 ]' "$out"
setparams awg2 '{"S4": "30", "MTU": "1420"}'
run ga_amn_packet_risk awg2
check "MTU 1420, поднятый человеком, при S4 = 30 → риска нет: считается при стандартном 1280" '[ "$out" = null ]' "$out"
setparams awg2 '{"S4": "142", "MTU": "1420"}'
run ga_amn_packet_risk awg2
check "MTU 1420 и S4 = 142 → риск, пакет при стандартном MTU — 1502" '[ "$(jget .packet)" = 1502 ] && [ "$(jget .mtu)" = 1280 ]' "$out"
setparams awg2 '{"MTU": "1280"}'
run ga_amn_packet_risk awg3
check "3.1 (S = 12) → риска нет" '[ "$out" = null ] && [ "$(param awg3 S4)" = 12 ]' "$out S4=$(param awg3 S4)"
run ga_amn_packet_risk awg1
check "1.x (S4 нет) → риска нет" '[ "$out" = null ]' "$out"
act status
check "мост status: packet_risk у awg2 (s4 142, keyless 1), null у awg3 и awg1" \
    '[ "$rc" = 0 ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .packet_risk.s4")" = 142 ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .packet_risk.keyless")" = 1 ] && [ "$(jget "[.profiles[] | select(.profile != \"awg2\") | .packet_risk] | map(. == null) | all")" = true ]' \
    "rc=$rc ${out:0:600}"
act status --brief=on
check "мост status --brief=on (по нему «Обзор» панели) — тоже с packet_risk" \
    '[ "$rc" = 0 ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .packet_risk.packet")" = 1502 ]' "rc=$rc ${out:0:400}"
act profiles-status
check "мост profiles-status — то же поле" '[ "$rc" = 0 ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .packet_risk.safe_s4")" = 40 ]' "rc=$rc ${out:0:400}"
check "чтение риска не зовёт Docker сверх прежнего: packet_risk — из config.json (в stderr нет docker exec)" \
    '! grep -q "docker exec" "$ERR"' "$(grep -c "docker exec" "$ERR")"

# ── 3. Исправить S4 ──────────────────────────────────────────────────────────
section "3: profile-fix-s4 — согласие за людей приложения, новый S4, кому что делать"
# Страница подключения boris (живая, с awg2) и подписки vera (забирали) и dima (не забирали) — как их кладут модули.
mkdir -p "$(ga_pages_dir)/p-1a2b3c" "$(ga_subs_dir)/vera" "$(ga_subs_dir)/dima"
printf '{"id":"p-1a2b3c","user":"boris","profiles":["awg2"],"expires_epoch":0,"forever":true}\n' >"$(ga_pages_dir)/p-1a2b3c/meta.json"
printf '%s\n' "$(printf 'x%.0s' $(seq 1 43))" >"$(ga_pages_dir)/p-1a2b3c/secret"
for _u in vera dima; do
    printf '{"user":"%s","created":"2026-10-01T10:00:00Z","generation":1}\n' "$_u" >"$(ga_subs_dir)/$_u/meta.json"
    printf '%s\n' "$(printf 'y%.0s' $(seq 1 43))" >"$(ga_subs_dir)/$_u/secret"
done
printf '{"fetches": 3, "auto_fetches": 3, "last_fetch": "2026-10-07T10:00:00Z", "last_suffix": "all.yaml", "last_client": "clash-verge"}\n' >"$(ga_subs_dir)/vera/state.json"
printf '{"fetches": 0}\n' >"$(ga_subs_dir)/dima/state.json"
run ga_amn_s4_audience awg2
check "кому что делать: подписка — vera, страница — boris, ключ — anna и dima, приложение — gleb" \
    '[ "$(jget ".subscription | join(\",\")")" = vera ] && [ "$(jget ".page | join(\",\")")" = boris ] && [ "$(jget ".key | join(\",\")")" = "anna,dima" ] && [ "$(jget ".app | join(\",\")")" = gleb ]' "$out"
cp "$STATE_DIR/amnezia-awg2/awg0.conf" "$TMP/awg0.before"
act profile-fix-s4 --profile=awg2
check "без согласия: отказ adopted_keyless (rc 2), слова — сколько людей и что им делать" \
    '[ "$rc" != 0 ] && [ "$(jget .status)" = error ] && [ "$(jget .code)" = adopted_keyless ] && grep -qF "пользователей на нём (1)" <<<"$(jget .message)" && grep -qF "adopted_confirm=awg2" <<<"$(jget .message)"' \
    "rc=$rc ${out:0:500}"
check "… и ничего не тронуто: S4 = 142, конфиг сервера тот же, рестарта не было" \
    '[ "$(param awg2 S4)" = 142 ] && cmp -s "$TMP/awg0.before" "$STATE_DIR/amnezia-awg2/awg0.conf" && ! grep -q "docker restart" "$ERR"' "S4=$(param awg2 S4)"
act profile-fix-s4 --profile=awg2 --adopted-confirm=awg3
check "согласие за другой профиль — тоже отказ" '[ "$rc" != 0 ] && [ "$(jget .code)" = adopted_keyless ] && [ "$(param awg2 S4)" = 142 ]' "rc=$rc ${out:0:300}"

# Страницы и подписки: ключ от моста (user-key) до исправления — в кеше службы страниц; после — новый S4 сразу.
WRAP="$TMP/action-bin"
printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$BASH5" "$INSTALL" >"$WRAP"; chmod +x "$WRAP"
python3 - "$ROOT" "$GOAMNEZIA_DIR" "$WRAP" >"$TMP/pages.json" 2>"$TMP/pages.err" <<'PY'
import importlib.util, json, os, subprocess, sys
root, gdir, wrap = sys.argv[1:4]
spec = importlib.util.spec_from_file_location("ga_pages_server_s4", os.path.join(root, "lib", "pages_server.py"))
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)
bridge = ps.Bridge(wrap, gdir, ps.Log(sys.stderr))
def s4(answer):
    data = answer[0] or {}
    for line in str(data.get("conf") or "").splitlines():
        if line.startswith("S4 = "):
            return line.split("=", 1)[1].strip()
    return ""
before = s4(bridge.user_key("anna", "awg2"))
cached = s4(bridge.user_key("anna", "awg2"))
fix = subprocess.run([wrap, "--action=profile-fix-s4", "--json", "--profile=awg2", "--adopted-confirm=awg2"],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
last = [l for l in fix.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
after = s4(bridge.user_key("anna", "awg2"))
print(json.dumps({"before": before, "cached": cached, "after": after, "rc": fix.returncode,
                  "fix": json.loads(last[-1]) if last else None, "fix_err": fix.stderr.decode("utf-8", "replace")[-3000:]}))
PY
out=$(cat "$TMP/pages.json")
FIX=$(jq -c '.fix' <<<"$out" 2>/dev/null)
NEW=$(jq -r '.s4 // empty' <<<"$FIX" 2>/dev/null)
check "с согласием (adopted_confirm=awg2): success, s4_old 142 → новый S4 12–24 (S4-MARGIN)" \
    '[ "$(jget .rc)" = 0 ] && [ "$(jq -r .status <<<"$FIX")" = success ] && [ "$(jq -r .s4_old <<<"$FIX")" = 142 ] && [ "$NEW" -ge 12 ] && [ "$NEW" -le 24 ]' \
    "${out:0:800} $(tail -3 "$TMP/pages.err")"
check "config.json и awg0.conf сервера — новый S4; прочие параметры прежние" \
    '[ "$(param awg2 S4)" = "$NEW" ] && grep -q "^S4 = $NEW$" "$STATE_DIR/amnezia-awg2/awg0.conf" && [ "$(param awg2 S1)" = 40 ] && [ "$(param awg2 S2)" = 60 ] && [ "$(param awg2 S3)" = 30 ] && [ "$(param awg2 MTU)" = 1280 ]' \
    "S4=$(param awg2 S4) NEW=$NEW"
check "итоговые размеры 148+S1, 92+S2, 64+S3, 32+S4 попарно различны (валидатор линии 2.0 доволен)" \
    'jq -c ".profiles.awg2.params" "$CONFIG_FILE" | python3 "$ROOT/lib/awgkeys.py" validate --line 2.0 --ipackets adopt' "$(jq -c '.profiles.awg2.params | {S1,S2,S3,S4}' "$CONFIG_FILE")"
check "ответ: пакет 1502 → 1280 + 80 + S4 ≤ 1400, контейнер перезапущен, подписки обновляются раз в 12 часов" \
    '[ "$(jq -r .packet_old <<<"$FIX")" = 1502 ] && [ "$(jq -r .packet <<<"$FIX")" = "$((1360 + NEW))" ] && [ "$(jq -r .budget <<<"$FIX")" = 1400 ] && [ "$(jq -r .restarted <<<"$FIX")" = true ] && [ "$(jq -r .update_hours <<<"$FIX")" = 12 ]' "${FIX:0:500}"
check "ответ: кому что делать — subscription [vera], page [boris], key [anna, dima], app [gleb]" \
    '[ "$(jq -r ".subscription | join(\",\")" <<<"$FIX")" = vera ] && [ "$(jq -r ".page | join(\",\")" <<<"$FIX")" = boris ] && [ "$(jq -r ".key | join(\",\")" <<<"$FIX")" = "anna,dima" ] && [ "$(jq -r ".app | join(\",\")" <<<"$FIX")" = gleb ]' "${FIX:0:600}"
check "шаги словами: перезапуск; подписка «обновится сама (до 12 ч, у FlClash — до 24 ч) или по «Обновить»» (срок FlClash — из ga_subs_update_slow, CRITICS-FIX-8); страница; ключ «импортировать заново»; приложение" \
    '[ "$(jq -r "[.steps[].kind] | join(\",\")" <<<"$FIX")" = "restart,subscription,page,key,app" ] && grep -qF "С подпиской (vera): обновится сама (до 12 ч, у FlClash — до 24 ч) или по «Обновить» в приложении." <<<"$(jq -r ".steps[1].text" <<<"$FIX")" && grep -qF "(anna, dima): импортировать заново" <<<"$(jq -r ".steps[3].text" <<<"$FIX")" && grep -qF "(gleb)" <<<"$(jq -r ".steps[4].text" <<<"$FIX")"' \
    "$(jq -c '.steps' <<<"$FIX")"
check "message моста — итог словами: «S4 профиля AmneziaWG 2.0 · порт 40000: 142 → …»" \
    'grep -qF "S4 профиля AmneziaWG 2.0 · порт 40000: 142 → $NEW, пакет данных 1502 → $((1360 + NEW)) байт" <<<"$(jq -r .message <<<"$FIX")"' "$(jq -r .message <<<"$FIX")"
check "контейнер перезапущен (DRY: docker restart amnezia-awg2) — тем же путём, что серверный параметр" \
    'grep -q "DRY: docker restart amnezia-awg2" <<<"$(jq -r .fix_err <<<"$out")"' "$(jq -r .fix_err <<<"$out" | tail -5)"
check "страницы и подписки: до — старый S4 (и из кеша службы), после — новый сразу (кеш сброшен по config.json)" \
    '[ "$(jget .before)" = 142 ] && [ "$(jget .cached)" = 142 ] && [ "$(jget .after)" = "$NEW" ]' "${out:0:300}"
check "приватный ключ anna прежний: ключ сервера и пиры не перевыпускались" \
    '[ -n "$(jq -r ".users[] | select(.name == \"anna\") | .peers.awg2.privkey" "$USERS_FILE")" ] && grep -qF "$(jq -r ".users[] | select(.name == \"anna\") | .peers.awg2.pubkey" "$USERS_FILE")" "$STATE_DIR/amnezia-awg2/awg0.conf"'
check "журнал: profile-fix-s4 «awg2 S4 142->… adopted_confirm=awg2», а не голый profile-set-param" \
    'jq -R -e -n "[inputs | fromjson? // empty | select(.action == \"profile-fix-s4\" and (.details | test(\"^awg2 S4 142->[0-9]+ .*keyless=1 adopted_confirm=awg2$\")))] | length == 1" "$GOAMNEZIA_AUDIT_LOG" && ! jq -R -e -n "[inputs | fromjson? // empty | select(.action == \"profile-set-param\")] | length > 0" "$GOAMNEZIA_AUDIT_LOG"' \
    "$(grep -E "profile-fix-s4|profile-set-param" "$GOAMNEZIA_AUDIT_LOG" | tail -3)"
act status
check "после исправления риска нет: packet_risk = null у всех" '[ "$rc" = 0 ] && [ "$(jget "[.profiles[].packet_risk] | map(. == null) | all")" = true ]' "${out:0:300}"
act profile-fix-s4 --profile=awg2 --adopted-confirm=awg2
check "повтор — s4_safe словами: «… укладывается в 1400 — исправлять нечего»" \
    '[ "$rc" != 0 ] && [ "$(jget .code)" = s4_safe ] && grep -qF "исправлять нечего" <<<"$(jget .message)"' "rc=$rc ${out:0:300}"

# ── 3а. PARAM-01: S4, который пишем мы ───────────────────────────────────────
section "3а: PARAM-01 — S4, который пишем мы (SSH, бот, панель, MCP — один мост), не больше 40"
act profile-set-param --profile=awg2 --key=S4 --value=41
check "S4 = 41 на 2.0 — отказ словами: пакет 1401 больше 1400, «до 40»; S4 прежний" \
    '[ "$(jget .status)" = error ] && [ "$(jget .code)" = params_invalid ] && grep -qF "S4 = 41 не принят" <<<"$(jget .message)" && grep -qF "1401 байт" <<<"$(jget .message)" && grep -qF "Задайте S4 до 40" <<<"$(jget .message)" && [ "$(param awg2 S4)" = "$NEW" ]' \
    "${out:0:400}"
act profile-set-param --profile=awg2 --key=S4 --value=0041
check "«0041» — то же число 41: отказ" '[ "$(jget .code)" = params_invalid ] && grep -qF "S4 = 41 не принят" <<<"$(jget .message)"' "${out:0:300}"
act profile-set-param --profile=awg2 --key=S4 --value=40
check "S4 = 40 — принят (пакет ровно 1400)" '[ "$(jget .status)" = success ] && [ "$(param awg2 S4)" = 40 ]' "${out:0:300}"
# S4-MARGIN (уточнение координатора 29.10): генератор и «Исправить S4» — 12–24, а вручную по-прежнему до 40. После смены
# S4 всем людям профиля нужны новые ключи — это говорят все три места: окно панели (prParamSrv до записи — по scope
# server из profile-params, prParamDoneSrv после — по reissue_required), бот (summary ядра в message) и SSH
# (pr_param_reissue_note — по тому же scope server).
act profile-set-param --profile=awg2 --key=S4 --value=33
check "вручную S4 = 33 (вне 12–24, до 40) — принят; reissue_required=true, в message — «старые конфиги больше не подключатся … выдать заново» (бот), шаг перезапуска" \
    '[ "$(jget .status)" = success ] && [ "$(param awg2 S4)" = 33 ] && [ "$(jq -c .reissue_required <<<"$out")" = true ] && grep -qF "$(t amnezia_param_summary_server)" <<<"$(jget .message)" && [ "$(jget ".result.steps[0].kind")" = restart ]' \
    "${out:0:500}"
run amnezia_profile_param_list awg2
check "список настроек профиля (SSH-меню): у S4 scope server — по нему SSH (pr_param_reissue_note) предупреждает до записи" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".[] | select(.key == \"S4\") | .scope" <<<"$out")" = server ]' "${out:0:300}"
check "… и в панели S4 — серверный параметр (SERVER_SIDE_PARAMS): по нему окно prParamSrv до записи" \
    'grep -E "^SERVER_SIDE_PARAMS = " "$ROOT/admin-web/server.py" | grep -qF "\"S4\""' \
    "$(grep -E "^SERVER_SIDE_PARAMS = " "$ROOT/admin-web/server.py")"
check "… эти предупреждения на месте: SSH — pr_param_reissue_note при scope server, панель — prParamSrv и prParamDoneSrv, бот — summary ядра" \
    'grep -qF "if [ \"\$scope\" = \"server\" ]; then" "$INSTALL" && grep -A1 -F "if [ \"\$scope\" = \"server\" ]; then" "$INSTALL" | grep -qF "pr_param_reissue_note" && grep -qF "S1-S4" <<<"$(t pr_param_reissue_note)" && grep -qF "const srv = x.scope === \"server\";" "$ROOT/admin-web/static/app.js" && grep -qF "tf(\"prParamSrv\"" "$ROOT/admin-web/static/app.js" && grep -qF "d.reissue_required ? t(\"prParamDoneSrv\")" "$ROOT/admin-web/static/app.js"' \
    "$(grep -n -A1 -F "if [ \"\$scope\" = \"server\" ]; then" "$INSTALL" | head -4)"
act profile-set-param --profile=awg2 --key=S4 --value=40
act profile-set-param --profile=awg3 --key=S4 --value=41
check "3.1: S4 = 41 — тоже отказ (та же формула пакета)" '[ "$(jget .code)" = params_invalid ] && [ "$(param awg3 S4)" = 12 ]' "${out:0:300}"
setparams awg2 '{"S4": "142"}'
act profile-set-param --profile=awg2 --key=Jc --value=6
check "подхваченный с S4 = 142: другие параметры меняются, S4 и пометка остаются" \
    '[ "$(jget .status)" = success ] && [ "$(param awg2 Jc)" = 6 ] && [ "$(param awg2 S4)" = 142 ]' "${out:0:300}"
act status
check "… и в status у него по-прежнему packet_risk (кнопка «Исправить S4»)" \
    '[ "$(jget ".profiles[] | select(.profile == \"awg2\") | .packet_risk.s4")" = 142 ]' "${out:0:300}"
run amnezia_profile_regen awg2 default
check "перегенерация: S4 12–24 и ни один класс пакетов данных не совпадает с рукопожатием (PARAM-06)" \
    '[ "$rc" = 0 ] && [ "$(param awg2 S4)" -ge 12 ] && [ "$(param awg2 S4)" -le 24 ] && python3 -c "import sys; s1, s2, s3, s4 = map(int, sys.argv[1:]); sys.exit(any((s + k - s4) % 16 == 0 for s, k in ((s1, 116), (s2, 60), (s3, 0))))" "$(param awg2 S1)" "$(param awg2 S2)" "$(param awg2 S3)" "$(param awg2 S4)"' \
    "rc=$rc $(jq -c '.profiles.awg2.params | {S1,S2,S3,S4}' "$CONFIG_FILE")"

# ── 4. Профиль не работает, никого нет, 3.1 и 1.x ────────────────────────────
section "4: без людей; 3.1 и 1.x — отказ словами"
run amnezia_profile_remove awg2
run amnezia_profile_install awg2 40000 default
setparams awg2 '{"S1": "40", "S2": "60", "S3": "0", "S4": "150"}'
jq '.users = []' "$USERS_FILE" >"$TMP/u" && mv "$TMP/u" "$USERS_FILE" && chmod 600 "$USERS_FILE"
run amnezia_profile_fix_s4 awg2
check "S3 = 0 (размер 64): S4 = 32 исключён, людей нет — «больше ничего делать не нужно»" \
    '[ "$rc" = 0 ] && [ "$(jget .s4)" != 32 ] && [ "$(jget .s4)" -ge 12 ] && [ "$(jget .s4)" -le 24 ] && [ "$(jget ".steps[-1].kind")" = nobody ] && [ "$(jget "[.subscription, .page, .key, .app] | add | length")" = 0 ]' "rc=$rc $out $(errs)"
check "… и ни один класс пакетов данных (S4 + 32 + 16k) не совпадает с рукопожатием (PARAM-06)" \
    'python3 -c "import sys; s4 = int(sys.argv[1]); sys.exit(any((s + k - s4) % 16 == 0 for s, k in ((40, 116), (60, 60), (0, 0))))" "$(jget .s4)"' "$out"
act profile-fix-s4 --profile=awg3
check "3.1 (S4 = 12) — s4_safe, профиль не тронут" \
    '[ "$rc" != 0 ] && [ "$(jget .code)" = s4_safe ] && grep -qF "AmneziaWG 3.1 · порт 40001 — 12" <<<"$(jget .message)" && [ "$(param awg3 S4)" = 12 ]' "rc=$rc ${out:0:300}"
act profile-fix-s4 --profile=awg1
check "1.x (S4 нет) — s4_safe: «нет S4 — исправлять нечего»" \
    '[ "$rc" != 0 ] && [ "$(jget .code)" = s4_safe ] && grep -qF "нет S4" <<<"$(jget .message)"' "rc=$rc ${out:0:300}"
act profile-fix-s4 --profile=awg9
check "чужой профиль — bad_arg ещё в диспетчере (enum из actions.json)" '[ "$(jget .code)" = bad_arg ]' "${out:0:200}"
LANG_SAVE=$(jq -r .lang "$CONFIG_FILE")
GOAMNEZIA_CALL_LANG=en "$BASH5" "$INSTALL" --action=profile-fix-s4 --json --profile=awg3 >"$OUT" 2>"$ERR"; out=$(tail -n 1 "$OUT")
check "по-английски (язык вызова en) — тоже словами" 'grep -qF "nothing to fix" <<<"$(jget .message)"' "${out:0:300}"
[ "$LANG_SAVE" = "$(jq -r .lang "$CONFIG_FILE")" ] || bad "язык установки не должен меняться" "$LANG_SAVE"

# ── 5. Окно MTU: минимум 1280 и предупреждение моста ────────────────────────
section "5: MTU ниже 1280 — отказ словами (L-02б); предупреждение — только пакет больше 1440 (L-02а)"
setparams awg2 '{"S4": "40", "MTU": "1280"}'
LOW_RU="Ниже 1280 туннель не поднимется у клиентов с IPv6 (AllowedIPs ::/0) — оставьте 1280 или больше."
run amnezia_profile_set_mtu awg2 1279
check "ядро: 1279 — отказ invalid_mtu словами («${LOW_RU}»), MTU прежний" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_mtu ] && grep -qF "$LOW_RU" "$ERR" && [ "$(param awg2 MTU)" = 1280 ]' \
    "rc=$rc code=$GA_ERR_CODE $(errs)"
for _low in 1250 1200 1100 576; do
    run amnezia_profile_set_param awg2 MTU "$_low"
    check "ядро: profile-set-param MTU $_low — тот же отказ теми же словами" \
        '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_mtu ] && grep -qF "$LOW_RU" "$ERR" && [ "$(param awg2 MTU)" = 1280 ]' \
        "rc=$rc code=$GA_ERR_CODE $(errs)"
done
run amnezia_profile_set_mtu awg2 1280
check "ядро: 1280 — да" '[ "$rc" = 0 ] && [ "$(param awg2 MTU)" = 1280 ]' "rc=$rc $(errs)"
act profile-mtu --profile=awg2 --mtu=1279
check "мост: profile-mtu 1279 — отказ (минимум 1280 в actions.json), MTU прежний" \
    '[ "$rc" != 0 ] && [ "$(jget .code)" = bad_arg ] && [ "$(param awg2 MTU)" = 1280 ]' "rc=$rc ${out:0:300}"
act profile-set-param --profile=awg2 --key=MTU --value=1250
check "мост: profile-set-param MTU 1250 — invalid_mtu словами (так его видят бот, панель и MCP)" \
    '[ "$rc" != 0 ] && [ "$(jget .code)" = invalid_mtu ] && [ "$(jget .message)" = "$LOW_RU" ]' "rc=$rc ${out:0:300}"
GOAMNEZIA_CALL_LANG=en "$BASH5" "$INSTALL" --action=profile-set-param --json --profile=awg2 --key=MTU --value=1250 \
    >"$OUT" 2>"$ERR"; out=$(tail -n 1 "$OUT")
check "… по-английски — тоже словами" \
    'grep -qF "Below 1280 the tunnel will not come up for clients with IPv6 (AllowedIPs ::/0)" <<<"$(jget .message)"' "${out:0:300}"
act profile-mtu --profile=awg2 --mtu=1320
check "1320 при S4 = 40 — без предупреждений (пакет 1440 — совет для проводного)" \
    '[ "$rc" = 0 ] && [ "$(jget ".warnings | length")" = 0 ] && [ "$(jget .packet)" = 1440 ] && [ "$(jget .warning)" = "" ] && [ "$(param awg2 MTU)" = 1320 ]' \
    "${out:0:300}"
act profile-mtu --profile=awg2 --mtu=1330
check "1330 при S4 = 40 — path: «Пакет 1450 байт: на части хостеров и сетей скачивание встанет — оставьте 1320 или меньше.»" \
    '[ "$rc" = 0 ] && [ "$(jget ".warnings | join(\",\")")" = path ] && [ "$(jget .warning)" = "Пакет 1450 байт: на части хостеров и сетей скачивание встанет — оставьте 1320 или меньше." ]' \
    "${out:0:400}"
act profile-mtu --profile=awg2 --mtu=1376
check "прежний совет 1376 при S4 = 40 — теперь path: пакет 1496 больше пути Aeza (1456)" \
    '[ "$(jget ".warnings | join(\",\")")" = path ] && [ "$(jget .packet)" = 1496 ]' "${out:0:300}"
act profile-mtu --profile=awg1 --mtu=1360
check "1.x (S4 нет), 1360 — пакет 1440, без предупреждений" \
    '[ "$(jget ".warnings | length")" = 0 ] && [ "$(jget .packet)" = 1440 ]' "${out:0:300}"
act profile-mtu --profile=awg3 --mtu=1460
check "3.1 (S4 = 12), 1460 — path (1552): на 3.1 живьём 0,6 Мбит/с к клиенту (L-02а)" \
    '[ "$(jget ".warnings | join(\",\")")" = path ] && [ "$(jget .packet)" = 1552 ]' "${out:0:300}"
act profile-mtu --profile=awg2 --mtu=1280
check "вернуть 1280 — без предупреждений, MTU 1280 в config.json" '[ "$rc" = 0 ] && [ "$(jget ".warnings | length")" = 0 ] && [ "$(param awg2 MTU)" = 1280 ]' "${out:0:300}"

section "6: срок забора подписки в шагах (_ga_amn_within) — словами окон перевыпуска, через ga_subs_update_slow (CRITICS-FIX-8)"
check "каталог как есть: «до 12 ч, у FlClash — до 24 ч»" '[ "$(_ga_amn_within 12)" = "до 12 ч, у FlClash — до 24 ч" ]' "$(_ga_amn_within 12)"
check "каталог как есть, совет сервера 24: FlClash не дольше — «до 24 ч»" '[ "$(_ga_amn_within 24)" = "до 24 ч" ]' "$(_ga_amn_within 24)"
W3=$(ga_subs_update_slow() { printf '%s' '[{"name":"Box","hours":48},{"name":"FlClash","hours":24},{"name":"Cat","hours":24},{"name":"Fast","hours":6}]'; }; _ga_amn_within 12)
check "несколько приложений: сначала самые долгие, один срок — через запятую, не дольше совета — не называются" \
    '[ "$W3" = "до 12 ч, у Box — до 48 ч, у FlClash, Cat — до 24 ч" ]' "$W3"
W4=$(ga_subs_update_slow() { printf '[]'; }; _ga_amn_within 08)
check "медленных нет, «08» — десятичное: «до 8 ч»" '[ "$W4" = "до 8 ч" ]' "$W4"
W5=$(ga_subs_update_slow() { printf 'мусор'; }; _ga_amn_within abc)
check "ответ не JSON, часы мусор — «до 12 ч»" '[ "$W5" = "до 12 ч" ]' "$W5"
W6=$(unset -f ga_subs_update_slow; _ga_amn_within 12)
check "модуля подписок нет — «до 12 ч»" '[ "$W6" = "до 12 ч" ]' "$W6"
W7=$(load_language en >/dev/null 2>&1; _ga_amn_within 12)
check "en: «within 12 h, FlClash — within 24 h»" '[ "$W7" = "within 12 h, FlClash — within 24 h" ]' "$W7"

section "7: status.subs_update — срок забора подписки без обхода подписок (R9-1, окно «Исправить S4» бота)"
act status
check "мост status: subs_update {update_interval 12, update_slow [FlClash 24]} — как в sub-list" \
    '[ "$rc" = 0 ] && [ "$(jget .subs_update.update_interval)" = 12 ] && [ "$(jget ".subs_update.update_slow | map(.name + \":\" + (.hours|tostring)) | join(\",\")")" = "FlClash:24" ]' \
    "rc=$rc $(jget -c .subs_update)"
act sub-list
SL=$(jq -c '{update_interval, update_slow}' <<<"$out" 2>/dev/null)
act status
check "… то же, что update_interval и update_slow ответа sub-list" '[ -n "$SL" ] && [ "$(jq -c .subs_update <<<"$out")" = "$SL" ]' "$SL / $(jq -c .subs_update <<<"$out")"
act status --brief=on
check "в brief поля subs_update нет (частые опросы Обзора его не считают)" '[ "$rc" = 0 ] && [ "$(jget "has(\"subs_update\")")" = false ]' "rc=$rc"
jq '.subs = ((.subs // {}) + {update_interval: 30})' "$CONFIG_FILE" >"$TMP/cfg.json" && mv "$TMP/cfg.json" "$CONFIG_FILE" && chmod 600 "$CONFIG_FILE"
act status
check "совет сервера 30 ч: update_interval 30, FlClash (24) реже не обновляет — update_slow пуст" \
    '[ "$(jget .subs_update.update_interval)" = 30 ] && [ "$(jget ".subs_update.update_slow | length")" = 0 ]' "$(jget -c .subs_update)"
jq 'del(.subs.update_interval)' "$CONFIG_FILE" >"$TMP/cfg.json" && mv "$TMP/cfg.json" "$CONFIG_FILE" && chmod 600 "$CONFIG_FILE"
# Помощник status — из install.sh как есть (сам install.sh в этот процесс не подключается).
eval "$(sed -n '/^_subs_update_json() {$/,/^}$/p' "$INSTALL")"
S1=$(ga_subs_update_slow() { printf 'мусор'; }; _subs_update_json)
check "update_slow не JSON-список — [] (ответ status остаётся JSON)" \
    '[ "$(jq -c . <<<"$S1")" = "{\"update_interval\":12,\"update_slow\":[]}" ]' "$S1"
S2=$(unset -f ga_subs_update_hours; _subs_update_json)
check "модуля подписок нет — null" '[ "$S2" = null ]' "$S2"

section "8: K5-2 — согласие «Исправить S4» называет то, что будет: S4 из S4_GEN_RANGE и пакет не больше 1280 + 80 + верх"
# Числа — из lib/awgkeys.py (S4_GEN_RANGE, MTU_STANDARD, PACKET_OVERHEAD): сузят или сдвинут диапазон — тексты SSH, бота
# и панели краснеют здесь, а не расходятся с подборщиком молча (живой стенд 29.10: «не больше 40» при итоге 71 → 20).
read -r _lo _hi _pk _mtu <<<"$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import awgkeys as a; lo, hi = a.S4_GEN_RANGE; print(lo, hi, a.MTU_STANDARD + a.PACKET_OVERHEAD + hi, a.MTU_STANDARD)' "$ROOT/lib" 2>/dev/null)"
# KEENETIC-I1-N (N7): пакет 1280 + 80 + верх — только при MTU 1280 (MTU профиля можно поднять до 1320) — так и сказано.
_ru="S4 станет ${_lo}–${_hi} — при MTU ${_mtu} пакет данных не больше ${_pk} байт"
_en="S4 becomes ${_lo}–${_hi} — at MTU ${_mtu} the data packet is at most ${_pk} bytes"
check "awgkeys: диапазон и пакет прочитаны (${_lo}–${_hi}, ${_pk})" '[[ "$_lo" =~ ^[0-9]+$ ]] && [[ "$_hi" =~ ^[0-9]+$ ]] && [[ "$_pk" =~ ^[0-9]+$ ]] && [ "$_mtu" = 1280 ]'
check "SSH, бот и панель (ru и en) — «${_ru}» / «${_en}», без «не больше 40» / «at most 40»" \
    'grep -F "I18N[pr_s4_fix_note]=" "$ROOT/lib/lang/ru.d/menu.sh" | grep -qF "$_ru" && grep -F "I18N[pr_s4_fix_note]=" "$ROOT/lib/lang/en.d/menu.sh" | grep -qF "$_en" && jq -r .pr_s4_note_fmt "$ROOT/goamnezia-bot/lang/ru.json" | grep -qF "$_ru" && jq -r .pr_s4_note_fmt "$ROOT/goamnezia-bot/lang/en.json" | grep -qF "$_en" && [ "$(grep -F "prS4FixD:" "$ROOT/admin-web/static/app.js" | grep -cF -e "$_ru" -e "$_en")" = 2 ] && ! grep -h -F -e "I18N[pr_s4_fix_note]=" -e "\"pr_s4_note_fmt\"" -e "prS4FixD:" "$ROOT/lib/lang/ru.d/menu.sh" "$ROOT/lib/lang/en.d/menu.sh" "$ROOT/goamnezia-bot/lang/ru.json" "$ROOT/goamnezia-bot/lang/en.json" "$ROOT/admin-web/static/app.js" | grep -q -e "не больше 40" -e "at most 40"'
# Экран SSH целиком до вопроса: подстановок столько, сколько в тексте (лишний аргумент printf повторил бы весь абзац).
_s4ssh=$(
    eval "$(sed -n '/^_profile_fix_s4_interactive() {$/,/^}$/p' "$INSTALL")"
    _ui_w() { printf '%s' "$1"; }
    _pr_name() { printf '%s' "$1"; }
    _user_sub_within() { printf 'до 12 ч'; }
    confirm() { return 1; }
    ga_amn_packet_risk() { printf '{"packet":1502,"budget":1400,"s4":142,"safe_s4":40,"mtu":1280,"keyless":0}'; }
    _profile_fix_s4_interactive awg2 2>&1
)
_s4ssh=$(tr -s ' \n' ' ' <<<"$_s4ssh")
check "SSH: до вопроса — «${_ru}, с запасом до 1400.» один раз, срок подписки на месте" \
    'grep -qF "${_ru}, с запасом до 1400. Профиль перезапустится" <<<"$_s4ssh" && [ "$(grep -o "S4 станет" <<<"$_s4ssh" | wc -l | tr -d " ")" = 1 ] && grep -qF "(до 12 ч)" <<<"$_s4ssh" && ! grep -qF "не больше 40" <<<"$_s4ssh"' \
    "$_s4ssh"

DONE=1
printf '\nИТОГ: %d пройдено, %d провалено\n' "$pass" "$fail"
[ "$fail" = 0 ]
