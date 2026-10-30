#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон подписки: lib/subs.sh и маршрут /s-… службы lib/pages_server.py
# (docs/SUBSCRIPTION.md)
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_subs_dryrun.sh
# Нужны: bash ≥ 5, jq, openssl, python3, curl. Docker, systemd и nginx не нужны: профили ставятся в dry-run, vhost
# пишется во временный GOAMNEZIA_NGINX_DIR, юнит — в GOAMNEZIA_SYSTEMD_DIR, systemctl печатается как «DRY: …».
# Служба поднимается ПО-НАСТОЯЩЕМУ на петле и ходит за ключами в настоящий install.sh --action=user-key, как на
# сервере: подписка вся про то, что Clash и роутер забирают по адресу ключи.
#
# Что проверяем:
#   Lite и запасной самоподписанный → subs_need_domain → отказы (имя, человек, нет ключей, ключи из приложения) →
#   создание: каталог 700, meta/secret 600, секрета нет в meta и аудите, флаг site.subs_location, локация с белым
#   списком суффиксов, глушилка /s-<43>, @ga_subs_down, зоны, HSTS, журналы выключены, юнит службы → повтор — тот же
#   адрес → sub-list без адресов, sub-show → живая служба: mihomo.yaml (три линии, version: 3 у одной, тот же ключ,
#   что .conf), awgNN.conf байт в байт как export_conf, синонимы awgv*, vpn:// и all.vpn, заголовки подписки
#   (Profile-Title, Profile-Update-Interval, Subscription-Userinfo из users.json, имя файла), 404 nginx-вида на чужой
#   секрет, суффикс и линию, роботу превью, выключенному (и 200 снова после включения), бан, учёт заборов без
#   адреса → перевыпуск: прежний адрес 404 сразу, новый 200 → отзыв мостом: обвязка снята → удаление человека и
#   сирота от прежней версии → пауза без домена и возврат → бэкап: subs/ в архиве, восстановление только на чистый
#   сервер → мост install.sh --action=sub-* и шпион argv (SEC46-02) → статические проверки модуля.
#
# Ничего вне $TMP не пишем; слушатель — только на 127.0.0.1.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL="$ROOT/install.sh"
BASH5="${BASH5:-$BASH}"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-subs.XXXXXX") || exit 2
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
# Бота на стенде нет (короткие ссылки — отдельным прогоном tests/test_short_dryrun.sh): .env бота машины не задевает
export BOT_DIR="$TMP/bot" GOAMNEZIA_BOT_DIR="$TMP/bot" GOAMNEZIA_SHORT_CONFIRM_DIR="$TMP/run/short-confirm"
export UPSTREAM_DIR="$TMP/upstream"
unset GOAMNEZIA_PAGES_DIR GOAMNEZIA_SUBS_DIR GOAMNEZIA_CLIENTS_JSON GOAMNEZIA_MIRROR_CURL
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$GOAMNEZIA_MIRROR_DIR"
# Временные файлы с ключами — в каталоге стенда (L1-19), петля — мимо прокси (L1-10).
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"
export NO_PROXY="127.0.0.1,localhost,::1${NO_PROXY:+,$NO_PROXY}" no_proxy="127.0.0.1,localhost,::1${no_proxy:+,$no_proxy}"

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 900; return 0; }
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
one_line_json() { [ "$(wc -l <"$OUT" | tr -d ' ')" -le 1 ] && is_json "$out"; }
audit_has() {   # audit_has <действие> [подстрока details]
    jq -R -e -n --arg a "$1" --arg d "${2:-}" \
        '[inputs | fromjson? // empty | select(.action == $a and ($d == "" or ((.details // "") | contains($d))))] | length > 0' \
        "$GOAMNEZIA_AUDIT_LOG" >/dev/null 2>&1
}

SUBS_DIR="$GOAMNEZIA_DIR/subs"
CONF="$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia"
UNIT="$GOAMNEZIA_SYSTEMD_DIR/goamnezia-pages.service"
SECRET_RE='^[A-Za-z0-9_-]{43}$'
has()   { grep -qE -- "$1" "$CONF"; }
hasnt() { ! grep -qE -- "$1" "$CONF"; }
HDR="$TMP/hdr.txt"
BODY="$TMP/body.bin"
code=""
# get <путь> [доп. аргументы curl] → код в $code, тело в $BODY, заголовки в $HDR
get() {
    local path="$1"; shift
    code=$(curl -s --max-time 30 -D "$HDR" -o "$BODY" -w '%{http_code}' "$@" "http://127.0.0.1:$PORT$path" 2>/dev/null) || code="000"
}
hdr() { grep -i "^$1:" "$HDR" | head -1 | tr -d '\r' | sed -E 's/^[^:]+: ?//'; }
secret_of() { sed -E 's#^https://example\.com/s-([A-Za-z0-9_-]{43})/$#\1#' <<<"$1"; }

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
for f in lib/common.sh lib/i18n.sh lib/docker.sh lib/amnezia.sh lib/users.sh lib/export.sh lib/website.sh lib/pages.sh \
         lib/subs.sh lib/backup.sh lib/pages_server.py lib/awgkeys.py lib/lang/ru.d/subs.sh lib/lang/en.d/subs.sh \
         lib/nginx/goamnezia.conf.tpl; do
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
check "модуль подключён: subs_create, subs_show, subs_rotate, subs_revoke, subs_list, subs_tick, _subs_revoke_locked" \
    'type subs_create && type subs_show && type subs_rotate && type subs_revoke && type subs_list && type subs_tick && type _subs_revoke_locked'
check "словарь subs подхвачен движком i18n (subs_err_need_domain, user_sub_item)" \
    '[ -n "${I18N[subs_err_need_domain]:-}" ] && [ -n "${I18N[user_sub_item]:-}" ]'

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
amnezia_profile_install awg1 40002 default >/dev/null 2>>"$ALL_ERR"
users_add anna "Анна" awg3,awg2,awg1 >/dev/null 2>>"$ALL_ERR"
users_add bob "Боб" awg2 >/dev/null 2>>"$ALL_ERR"
check "фикстура: awg3 (3.1), awg2 (2.0), awg1 (1.5); у anna пиры во всех трёх, у bob — в awg2" \
    '[ "$(ga_profile_line awg3)" = 3.1 ] && [ "$(ga_profile_line awg2)" = 2.0 ] && [ "$(ga_profile_line awg1)" = 1.5 ] && [ "$(ga_users_get anna | jq -r ".peers | keys | join(\",\")")" = awg1,awg2,awg3 ] && [ "$(ga_users_get bob | jq -r ".peers | keys | join(\",\")")" = awg2 ]'

# Глушилка секретных путей (lib/website.sh, _ga_site_secret_paths_block): одна локация на /p-, /s-, /a-, /r-, /d- и
# путь панели — в server на 80 и на 443, всегда.
HIDE_LOC="location ~ \"$GA_SITE_SECRET_PATHS_RE\" {"

# ── 1. Только домен с доверенным сертификатом ───────────────────────────────
section "Lite и самоподписанный → subs_need_domain"
run subs_create anna
check "в lite подписка не создаётся: subs_need_domain (rc 2), stdout пуст, каталога подписок нет" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = subs_need_domain ] && [ -z "$out" ] && [ ! -d "$SUBS_DIR" ]' "rc=$rc code=$GA_ERR_CODE"
check "отказ словами: домен и временная ссылка на файл, без глаголов моста" \
    'grep -q "временной ссылкой на файл" "$ERR" && ! grep -qE "sub-create|change-mode|--mode=" "$ERR"' "$(errs)"
ga_config_merge '{"mode": "lite_site", "domain": "example.com", "site": {"cert": "selfsigned"}}' >/dev/null 2>&1
run subs_create anna
check "сайт на запасном самоподписанном — тоже subs_need_domain: роутер и клиент исключений для сертификата не делают" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = subs_need_domain ]' "rc=$rc code=$GA_ERR_CODE"
# Без конвейеров: «… | grep -q» под pipefail ловит SIGPIPE, и флаг остался бы включённым для всех проверок ниже.
ga_config_set .site.subs_location true >/dev/null 2>&1
_ga_site_subs_block selfsigned >"$TMP/sb-self" 2>/dev/null
_ga_site_subs_block pro >"$TMP/sb-pro" 2>/dev/null
_ga_site_render_vhost selfsigned >"$TMP/vh-self" 2>/dev/null
ga_config_set .site.subs_location false >/dev/null 2>&1
check "в варианте vhost на самоподписанном маршрута подписок нет даже с флагом — только глушилка секретных путей на 80 и 443: 404 без журнала" \
    '[ ! -s "$TMP/sb-self" ] && grep -q proxy_pass "$TMP/sb-pro" && [ "$(grep -cF "$HIDE_LOC" "$TMP/vh-self")" = 2 ] && [ "$(grep -F -A3 "$HIDE_LOC" "$TMP/vh-self" | grep -c "return 404;")" = 2 ] && ! grep -q "goamnezia_subs\|return 301" "$TMP/vh-self"' "$(grep -n -A4 "/s-" "$TMP/vh-self")"
ga_config_merge '{"mode": "pro", "domain": "example.com", "site": {"cert": "letsencrypt"}}' >/dev/null 2>&1
check "стенд: сайт на домене example.com с доверенным сертификатом (ga_site_domain_ready)" 'ga_site_domain_ready'

# ── 2. Отказы до записи ─────────────────────────────────────────────────────
section "отказы: имя, человек, ключи"
run subs_create "../etc"
check "имя с посторонними символами → invalid_name (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_name ]' "rc=$rc code=$GA_ERR_CODE"
run subs_create ghost
check "чужой пользователь → user_not_found (rc 3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = user_not_found ]' "rc=$rc code=$GA_ERR_CODE"
users_add nokey "Без ключей" awg2 >/dev/null 2>>"$ALL_ERR"
jq '.users |= map(if .name == "nokey" then .peers = {} else . end)' "$USERS_FILE" >"$TMP/u.json" && mv -f "$TMP/u.json" "$USERS_FILE"
run subs_create nokey
check "у человека нет ни одного ключа → peer_not_found (rc 3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = peer_not_found ]' "rc=$rc code=$GA_ERR_CODE"
users_add imp "Из приложения" awg2 >/dev/null 2>>"$ALL_ERR"
jq '.users |= map(if .name == "imp" then .peers.awg2.privkey = "" else . end)' "$USERS_FILE" >"$TMP/u.json" && mv -f "$TMP/u.json" "$USERS_FILE"
run subs_create imp
check "все ключи из приложения Amnezia → no_private_key (rc 3) с подсказкой «Привязать старый ключ»" \
    '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = no_private_key ] && grep -q "Привязать старый ключ" "$ERR"' "rc=$rc code=$GA_ERR_CODE $(errs)"
_ga_site_render_vhost pro example.com >"$TMP/vh-empty" 2>/dev/null
check "без подписок в vhost только глушилка секретных путей на 80 и 443 (без журнала), маршрута к службе и зон нет" \
    '[ "$(grep -cF "$HIDE_LOC" "$TMP/vh-empty")" = 2 ] && ! grep -qF "location ~ \"^/s-[A-Za-z0-9_-]{43}/(" "$TMP/vh-empty" && ! grep -q "goamnezia_subs" "$TMP/vh-empty"' "$(grep -n "/s-" "$TMP/vh-empty")"
check "после отказов ни каталога подписок, ни флага маршрута, ни юнита" \
    '[ -z "$(find "$SUBS_DIR" -mindepth 1 -maxdepth 1 2>/dev/null)" ] && [ "$(ga_config_get .site.subs_location)" != true ] && [ ! -f "$UNIT" ]'

# ── 3. Создание ─────────────────────────────────────────────────────────────
section "subs_create"
run subs_create anna
check "subs_create anna: rc=0, одна строка JSON, new=true, generation=1" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .new)" = true ] && [ "$(jget .generation)" = 1 ]' "rc=$rc $(errs)"
A_BASE=$(jget .base_url); A_SECRET=$(secret_of "$A_BASE")
check "base_url https://example.com/s-<43 символа base64url>/, имя и подпись человека" \
    '[[ "$A_SECRET" =~ $SECRET_RE ]] && [ "$(jget .name)" = anna ] && [ "$(jget .label)" = "Анна" ]' "$A_BASE"
check "адреса по порядку: all.yaml (профиль mihomo), all.txt (incy, формат incy), .conf линий 3.1/2.0/1.5, vpn:// линий, all.vpn — каждый на base_url; синонимов (mihomo.yaml, awgv*) в списке нет" \
    '[ "$(jget "[.urls[].suffix] | join(\",\")")" = "all.yaml,all.txt,awg31.conf,awg20.conf,awg15.conf,awg31.vpn,awg20.vpn,awg15.vpn,all.vpn" ] && [ "$(jget ".urls[] | select(.suffix == \"all.yaml\") | .format")" = mihomo ] && [ "$(jq -r --arg b "$A_BASE" "[.urls[] | select(.url == (\$b + .suffix))] | length" <<<"$out")" = 9 ] && [ "$(jget ".urls[] | select(.suffix == \"all.txt\") | [.format, .line] | join(\":\")")" = "incy:" ]' "$out"
check "линии за ссылкой: 3.1 → awg3, 2.0 → awg2, 1.5 → awg1, все с ключом" \
    '[ "$(jget "[.lines[] | \"\(.line):\(.profile):\(.has_key):\(.token)\"] | join(\",\")")" = "3.1:awg3:true:awg31,2.0:awg2:true:awg20,1.5:awg1:true:awg15" ]' "$out"
cat >"$TMP/clients_check.py" <<'EOPY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
base, cl = d["base_url"], d["clients"]
ids = [c["id"] for c in cl]
assert ids == ["flclash", "clash-meta", "clash-verge-rev", "clash-party", "incy", "awgm-txt", "amneziavpn", "defaultvpn",
               "awgm-vpn", "amneziawg", "wgtunnel", "keenetic", "awgm-conf", "egress"], ids
KEYS = ["id", "app", "name", "name_en", "variant", "group", "format", "platforms", "update_hours", "official",
        "recommended", "hint", "items"]
IKEYS = ["line", "suffix", "url", "deeplink", "command", "copy", "qr", "qr_alt", "key_format", "short_url"]
for c in cl:
    assert list(c) == KEYS, (c["id"], list(c))
    assert set(c["hint"]) == {"ru", "en"} and "{minutes}" not in c["hint"]["ru"], c["id"]
    assert (c["app"], c["variant"]) == (c["id"], "") or c["app"] == "awgm", c["id"]
    for it in c["items"]:
        assert list(it) == IKEYS, (c["id"], list(it))
        assert it["url"] == base + it["suffix"], it["suffix"]
        assert it["suffix"] != "mihomo.yaml" and not it["suffix"].startswith("awgv"), it["suffix"]
        assert it["key_format"] == (c["format"] if "key" in (it["qr"], it["copy"]) else ""), (c["id"], it["suffix"])
assert [c["group"] for c in cl] == ["auto"] * 6 + ["once"] * 8
by = {c["id"]: c for c in cl}
sfx = lambda i: [x["suffix"] for x in by[i]["items"]]
# «Скопировать»: сам ключ — AmneziaVPN, DefaultVPN и AWG Manager (vpn:// и .conf), команда — Keenetic, адрес — остальные
assert [i for i in ids if {x["copy"] for x in by[i]["items"]} == {"key"}] == ["amneziavpn", "defaultvpn", "awgm-vpn",
                                                                              "awgm-conf"]
assert [i for i in ids if {x["copy"] for x in by[i]["items"]} == {"command"}] == ["keenetic"]
# AWG Manager — три записи одного приложения: app, variant — подпись формата, all.txt — auto, ключи по линиям — once
assert [(by[i]["app"], by[i]["variant"], by[i]["group"], by[i]["platforms"]) for i in ("awgm-txt", "awgm-vpn", "awgm-conf")] == \
    [("awgm", "Все линии .txt", "auto", ["router"]), ("awgm", "Ключ vpn://", "once", ["router"]),
     ("awgm", "Файл .conf", "once", ["router"])]
assert sfx("awgm-txt") == ["all.txt"] and sfx("awgm-vpn") == ["awg31.vpn", "awg20.vpn", "awg15.vpn"]
assert sfx("awgm-conf") == ["awg31.conf", "awg20.conf", "awg15.conf"]
assert {(x["qr"], x["key_format"]) for i in ("awgm-vpn", "awgm-conf") for x in by[i]["items"]} == {("", "vpn"), ("", "conf")}
# Второй QR — только у incy: голый адрес для сканера внутри incy
assert [i for i in ids if any(x["qr_alt"] for x in by[i]["items"])] == ["incy"] and by["incy"]["items"][0]["qr_alt"] == "url"
assert [by[i]["format"] for i in ("flclash", "incy", "amneziavpn", "amneziawg", "keenetic", "egress")] == \
    ["yaml", "txt", "vpn", "conf", "conf", "vpn"]
assert sfx("flclash") == ["all.yaml"] and by["flclash"]["items"][0]["qr"] == "url" and by["flclash"]["update_hours"] == 24
assert [by[i]["items"][0]["qr"] for i in ("clash-meta", "clash-verge-rev", "clash-party")] == ["", "", ""]
assert by["flclash"]["items"][0]["deeplink"] == "flclash://install-config?url=" + (base + "all.yaml").replace(":", "%3A").replace("/", "%2F")
assert sfx("incy") == ["all.txt"] and by["incy"]["items"][0]["deeplink"] == "incy://add/" + base + "all.txt"
assert by["incy"]["items"][0]["qr"] == "deeplink" and by["incy"]["platforms"] == ["ios", "android", "android_tv", "tvos", "macos"]
assert sfx("amneziavpn") == ["awg31.vpn", "awg20.vpn", "awg15.vpn"]
assert {(x["qr"], x["key_format"]) for x in by["amneziavpn"]["items"]} == {("key", "vpn")}
assert sfx("amneziawg") == ["awg31.conf", "awg20.conf", "awg15.conf"]
assert {(x["qr"], x["key_format"]) for x in by["amneziawg"]["items"]} == {("key", "conf")}
assert by["amneziawg"]["platforms"] == ["ios", "android", "windows", "macos"], "телевизоров нет"
assert sfx("keenetic") == ["awg20.conf", "awg15.conf"], "3.1 KeeneticOS не умеет"
assert [x["command"] for x in by["keenetic"]["items"]] == \
    ["interface Wireguard9 wireguard import " + base + f for f in ("awg20.conf", "awg15.conf")]
assert [x["deeplink"] for x in by["wgtunnel"]["items"]] == ["wg://" + (base + f)[len("https://"):] for f in sfx("wgtunnel")]
assert by["wgtunnel"]["platforms"] == ["android", "android_tv"] and {x["qr"] for x in by["wgtunnel"]["items"]} == {"key"}
assert sfx("egress") == ["all.vpn"] and by["egress"]["items"][0]["qr"] == "" and by["egress"]["name"] == "goVLESS, Kaskad, goTelegram"
assert [i for i in ids if by[i]["recommended"]] == ["flclash", "clash-verge-rev", "incy", "keenetic"]
EOPY
printf '%s' "$out" >"$TMP/anna_sub.json"
check "clients (контракт для панели, бота и SSH): поля по порядку (app и variant, у items — copy и qr_alt), сначала auto (mihomo-клиенты, incy, AWG Manager .txt), потом once (AmneziaVPN, DefaultVPN, AmneziaWG, WG Tunnel, Keenetic, AWG Manager vpn:// и .conf), продукты серии последними; адреса — base_url + суффикс; ссылки «Открыть в …», команда Keenetic, QR и «Скопировать» по каталогу" \
    'python3 "$TMP/clients_check.py" "$TMP/anna_sub.json"' "$(python3 "$TMP/clients_check.py" "$TMP/anna_sub.json" 2>&1 | tail -2)"
SD="$SUBS_DIR/anna"
check "каталог подписок 700, каталог anna 700, meta.json и secret 600" \
    '[ "$(perm "$SUBS_DIR")" = 700 ] && [ "$(perm "$SD")" = 700 ] && [ "$(perm "$SD/meta.json")" = 600 ] && [ "$(perm "$SD/secret")" = 600 ]' "$(ls -la "$SD" 2>&1)"
check "секрет — отдельным файлом, в meta.json его нет; meta: user, user_created записи, created, generation" \
    '[ "$(cat "$SD/secret")" = "$A_SECRET" ] && ! grep -qF -- "$A_SECRET" "$SD/meta.json" && [ "$(jq -r .user "$SD/meta.json")" = anna ] && [ "$(jq -r .user_created "$SD/meta.json")" = "$(ga_users_get anna | jq -r .created)" ] && [ "$(jq -r .generation "$SD/meta.json")" = 1 ]' "$(cat "$SD/meta.json")"
check "config.json: site.subs_location=true; юнит общей службы записан и поднят" \
    '[ "$(ga_config_get .site.subs_location)" = true ] && [ -f "$UNIT" ] && grep -q "DRY: systemctl enable --now goamnezia-pages.service" "$ERR" && grep -q "Description=.*subscriptions" "$UNIT"' "$(errs)"
awk '/location ~ "\^\/s-\[A-Za-z0-9_-\]\{43\}\/\(/{f=1} f&&/^    }/{print; exit} f' "$CONF" >"$TMP/subs_loc"
check "vhost: локация /s-<43>/<белый список> в кавычках → 127.0.0.1:1987; all.yaml, all.txt (incy), синонимы mihomo.yaml и awgv15/awgv2/awgv3 в списке" \
    'grep -qF "location ~ \"^/s-[A-Za-z0-9_-]{43}/((awg(10|15|20|31)|awgv(15|2|3))\.(conf|vpn)|all\.(vpn|txt|yaml)|mihomo\.yaml)$\" {" "$CONF" && grep -q "proxy_pass http://127.0.0.1:1987;" "$TMP/subs_loc"' "$(grep -n "s-\[" "$CONF")"
# Обновление: белый список доезжает до поставленных серверов пересборкой vhost — её делает шаг миграции
# _migrate_site_vhost (install.sh) и при новой версии, и при новом коде той же версии (UP-7). Здесь — vhost прежнего
# кода (без all.txt и all.yaml) и та же пересборка.
sed -i.bak 's/|all\\\.(vpn|txt|yaml)|/|all\\.vpn|/' "$CONF" && rm -f "$CONF.bak"
had_old=$(grep -cF 'all\.vpn|mihomo\.yaml)$' "$CONF")
ga_with_config_lock _website_reapply_conf_locked >/dev/null 2>>"$ALL_ERR"
check "обновление: vhost прежнего кода (без all.txt и all.yaml) пересобирается шагом миграции (_website_reapply_conf_locked) — оба в белом списке" \
    '[ "$had_old" = 1 ] && grep -qF "|all\.(vpn|txt|yaml)|mihomo\.yaml)$\" {" "$CONF" && grep -q "_website_reapply_conf_locked" <<<"$(awk "/^_migrate_site_vhost\(\)/,/^}/" "$INSTALL")"' \
    "had_old=$had_old $(grep -n "s-\[" "$CONF")"
check "vhost: путь не пишется в журналы (access_log off, error_log /dev/null crit), 404/502/504 → @ga_subs_down, 503 проходит" \
    'grep -q "access_log off;" "$TMP/subs_loc" && grep -q "error_log /dev/null crit;" "$TMP/subs_loc" && grep -q "error_page 404 502 504 =404 @ga_subs_down;" "$TMP/subs_loc" && has "location @ga_subs_down \{"' "$(cat "$TMP/subs_loc")"
check "vhost: свой предел запросов (1 r/s, запас 10, 4 соединения, отказ — 404), зоны на уровне http до первого server" \
    'grep -q "limit_req zone=goamnezia_subs burst=10 nodelay;" "$TMP/subs_loc" && grep -q "limit_req_status 404;" "$TMP/subs_loc" && grep -q "limit_conn goamnezia_subs_conn 4;" "$TMP/subs_loc" && awk "/^server \{/{exit} /^limit_req_zone \\\$binary_remote_addr zone=goamnezia_subs:1m rate=1r\/s;/{a=1} /^limit_conn_zone \\\$binary_remote_addr zone=goamnezia_subs_conn:1m;/{b=1} END{exit !(a&&b)}" "$CONF"' "$(grep -n "limit_" "$CONF")"
check "vhost: защитные заголовки по одному (DENY, no-referrer, nosniff) и HSTS" \
    'grep -q "add_header X-Frame-Options \"DENY\" always;" "$TMP/subs_loc" && grep -q "add_header Referrer-Policy \"no-referrer\" always;" "$TMP/subs_loc" && grep -q "Strict-Transport-Security" "$TMP/subs_loc"' "$(cat "$TMP/subs_loc")"
# Номера строк: локация суффиксов, обе глушилки, второй server (HTTPS). Глушилки — одна в server на 80, другая
# в HTTPS после локации суффиксов (регулярки nginx проверяет по порядку).
S_WL=$(grep -nF 'location ~ "^/s-[A-Za-z0-9_-]{43}/(' "$CONF" | cut -d: -f1)
mapfile -t S_CA < <(grep -nF "$HIDE_LOC" "$CONF" | cut -d: -f1)
S_SRV2=$(grep -n '^server {' "$CONF" | sed -n 2p | cut -d: -f1)
awk -v s="${S_CA[0]:-0}" 'NR>=s{print} NR>s && /^    }/{exit}' "$CONF" >"$TMP/ca80"
awk -v s="${S_CA[1]:-0}" 'NR>=s{print} NR>s && /^    }/{exit}' "$CONF" >"$TMP/ca443"
check "vhost: любой другой путь под /s-<43> — 404 без журнала (глушилка секретных путей после локации суффиксов)" \
    '[ "${#S_CA[@]}" = 2 ] && [ "$S_WL" -gt "$S_SRV2" ] && [ "${S_CA[1]}" -gt "$S_WL" ] && grep -q "return 404;" "$TMP/ca443" && grep -q "access_log off;" "$TMP/ca443" && grep -q "error_log /dev/null crit;" "$TMP/ca443"' "$(grep -n "/s-" "$CONF")"
check "vhost: на 80 своя глушилка — тот же 301 на https://\$server_name, но без строки в журнале (адрес http://… руками в роутере)" \
    '[ "${S_CA[0]}" -lt "$S_SRV2" ] && grep -qF "return 301 https://\$server_name\$request_uri;" "$TMP/ca80" && grep -q "access_log off;" "$TMP/ca80" && grep -q "error_log /dev/null crit;" "$TMP/ca80" && has "# GOAMNEZIA_SECRET_PATHS_80" && has "# GOAMNEZIA_SECRET_PATHS_443"' "$(cat "$TMP/ca80")"
check "vhost: в @ga_subs_down нет своих заголовков — 404 как у сайта (SEC-G4); якоря на месте, хвостов подстановок нет" \
    'awk "/location @ga_subs_down/{f=1} f && !/^[ \t]*#/{print} f&&/^    }/{exit}" "$CONF" | { ! grep -qE "add_header|expires"; } && has "# GOAMNEZIA_SUBS_LOCATION" && has "# GOAMNEZIA_SUBS_ZONES" && has "# GOAMNEZIA_PAGES_LOCATION" && hasnt "_PLACEHOLDER"'
check "vhost: все регулярные location закавычены, если содержат { } ;" \
    'awk "/^[[:space:]]*location[[:space:]]+~\\*?[[:space:]]/ { re = \$3; if (re ~ /^[\"\x27]/) next; if (re ~ /[{};]/) { print NR\": \"\$0; bad = 1 } } END { exit bad ? 1 : 0 }" "$CONF"' "$(grep -n "location ~" "$CONF")"
check "аудит sub-create: имя и поколение, секрета нет; в stderr и общем логе его тоже нет" \
    'audit_has sub-create "user=anna generation=1" && ! grep -qF -- "$A_SECRET" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF -- "$A_SECRET" "$ALL_ERR" && { [ ! -f "$GOAMNEZIA_LOG_FILE" ] || ! grep -qF -- "$A_SECRET" "$GOAMNEZIA_LOG_FILE"; }'
run subs_create anna
check "повторное создание — тот же адрес, new=false, аудит existing=yes; юнит не перезаписан" \
    '[ "$rc" = 0 ] && [ "$(jget .base_url)" = "$A_BASE" ] && [ "$(jget .new)" = false ] && audit_has sub-create "user=anna existing=yes" && ! grep -q "DRY: systemctl restart goamnezia-pages.service" "$ERR"' "rc=$rc $(errs)"

# ── 4. Список и показ ───────────────────────────────────────────────────────
section "subs_list и subs_show"
run subs_list
check "subs_list: одна строка JSON, count=1, domain_ready=true, update_interval=12, без адресов и секрета" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .count)" = 1 ] && [ "$(jget .domain_ready)" = true ] && [ "$(jget .update_interval)" = 12 ] && ! grep -qF -- "$A_SECRET" "$OUT" && ! grep -q "base_url\|https://" "$OUT"' "$out"
check "строка списка: name, label, created, fetches=0, линии, disabled=false" \
    '[ "$(jget ".subs[0] | [.name, .label, .fetches, (.lines | length), .disabled] | map(tostring) | join(\" \")")" = "anna Анна 0 3 false" ]' "$out"
run subs_list bob
check "subs_list bob: пусто" '[ "$rc" = 0 ] && [ "$(jget .count)" = 0 ]' "$out"
run subs_list "bad name"
check "subs_list с негодным именем → invalid_name (rc 2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_name ]' "rc=$rc"
run subs_show anna
check "subs_show: тот же адрес, аудит sub-show без секрета" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .base_url)" = "$A_BASE" ] && audit_has sub-show "user=anna" && ! grep -qF -- "$A_SECRET" "$GOAMNEZIA_AUDIT_LOG"' "$out"
run subs_show bob
check "subs_show без подписки → sub_not_found (rc 3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = sub_not_found ]' "rc=$rc"

# ── 5. Живая служба на петле ────────────────────────────────────────────────
section "служба: тела по суффиксам, заголовки подписки, 404, бан, заборы"
ACTION_BIN="$TMP/goamnezia-action"
printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$BASH5" "$INSTALL" >"$ACTION_BIN"
chmod 755 "$ACTION_BIN"
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
READY="$TMP/ready"
python3 "$ROOT/lib/pages_server.py" --dir "$GOAMNEZIA_DIR" --bind 127.0.0.1 --port "$PORT" \
    --action-bin "$ACTION_BIN" --audit "$GOAMNEZIA_AUDIT_LOG" --ready-file "$READY" >"$TMP/server.log" 2>&1 &
SERVER_PID=$!
for _i in $(seq 1 100); do [ -f "$READY" ] && break; sleep 0.1; done
check "служба поднялась на 127.0.0.1:$PORT" '[ -f "$READY" ] && kill -0 "$SERVER_PID" 2>/dev/null' "$(cat "$TMP/server.log")"
CONF20=$(export_conf anna awg2 2>/dev/null); CONF31=$(export_conf anna awg3 2>/dev/null); CONF15=$(export_conf anna awg1 2>/dev/null)
VPN20=$(export_vpn anna awg2 2>/dev/null)
A_PRIV3=$(ga_users_get anna | jq -r .peers.awg3.privkey); A_PRIV2=$(ga_users_get anna | jq -r .peers.awg2.privkey)
A_PRIV1=$(ga_users_get anna | jq -r .peers.awg1.privkey)

get "/s-$A_SECRET/mihomo.yaml" -A "clash-verge/v2.4.2"
cp "$BODY" "$TMP/mihomo.yaml"
check "mihomo.yaml → 200 text/yaml; три прокси «Анна · AmneziaWG 3.1/2.0/1.5», группа goAmnezia, MATCH" \
    '[ "$code" = 200 ] && [ "$(hdr content-type)" = "text/yaml; charset=utf-8" ] && [ "$(grep -c "^  - name: \"Анна · AmneziaWG" "$TMP/mihomo.yaml")" = 3 ] && grep -q "^  - name: \"goAmnezia\"" "$TMP/mihomo.yaml" && grep -q "^  - \"MATCH,goAmnezia\"" "$TMP/mihomo.yaml"' "code=$code $(head -c 400 "$TMP/mihomo.yaml")"
check "mihomo.yaml: version: 3 — ровно у одной линии (3.1); у каждой — приватный ключ её .conf; Endpoint — домен Pro" \
    '[ "$(grep -c "version: 3" "$TMP/mihomo.yaml")" = 1 ] && grep -qF "private-key: \"$A_PRIV3\"" "$TMP/mihomo.yaml" && grep -qF "private-key: \"$A_PRIV2\"" "$TMP/mihomo.yaml" && grep -qF "private-key: \"$A_PRIV1\"" "$TMP/mihomo.yaml" && grep -q "server: \"example.com\"" "$TMP/mihomo.yaml"' "$(head -c 600 "$TMP/mihomo.yaml")"
printf '%s\n' "$CONF31" >"$TMP/c31.conf"; printf '%s\n' "$CONF20" >"$TMP/c20.conf"; printf '%s\n' "$CONF15" >"$TMP/c15.conf"
cat >"$TMP/same.py" <<'EOPY'
import sys
sys.path.insert(0, sys.argv[1])
import awgkeys
text = open(sys.argv[2], encoding="utf-8").read()
for line, path in (("3.1", sys.argv[3]), ("2.0", sys.argv[4]), ("1.5", sys.argv[5])):
    proxy = awgkeys.mihomo_proxy(line, awgkeys.conf_client_data(open(path, encoding="utf-8").read()), "Анна · AmneziaWG " + line)
    block = "\n".join(awgkeys._yaml_pairs(proxy, 4))
    assert ("  - " + block.lstrip()) in text, line
EOPY
check "mihomo.yaml: прокси каждой линии — ровно awgkeys.mihomo_proxy из .conf этой линии (тот же ключ, те же параметры)" \
    'python3 "$TMP/same.py" "$ROOT/lib" "$TMP/mihomo.yaml" "$TMP/c31.conf" "$TMP/c20.conf" "$TMP/c15.conf"'
cp "$HDR" "$TMP/mihomo.hdr"
get "/s-$A_SECRET/all.yaml" -A "clash-verge/v2.4.2"
check "all.yaml — новое имя того же профиля: тело и заголовки подписки — те же, что у прежнего mihomo.yaml" \
    '[ "$code" = 200 ] && cmp -s "$BODY" "$TMP/mihomo.yaml" && for h in content-type content-disposition profile-title profile-update-interval subscription-userinfo; do [ "$(hdr "$h")" = "$(grep -i "^$h:" "$TMP/mihomo.hdr" | head -1 | tr -d "\r" | sed -E "s/^[^:]+: ?//")" ] || exit 1; done' \
    "code=$code $(diff <(sed -n 1,20p "$TMP/mihomo.yaml") <(sed -n 1,20p "$BODY") | head -5)"
check "заголовки подписки: имя профиля (filename* для Clash Verge), Profile-Title, интервал 12 ч, трафик без лимита и срока" \
    '[ "$(hdr content-disposition)" = "attachment; filename=\"anna.yaml\"; filename*=UTF-8'"''"'%D0%90%D0%BD%D0%BD%D0%B0.yaml" ] && [ "$(hdr profile-title)" = "base64:$(printf "Анна" | base64)" ] && [ "$(hdr profile-update-interval)" = 12 ] && [[ "$(hdr subscription-userinfo)" =~ ^upload=[0-9]+\;\ download=[0-9]+\;\ total=0\;\ expire=0$ ]]' "$(cat "$HDR")"
check "защитные заголовки: no-store, nosniff, noindex, no-referrer, CSP sandbox, Server nginx" \
    'hdr cache-control | grep -q "no-store" && hdr x-content-type-options | grep -q nosniff && hdr x-robots-tag | grep -q noindex && hdr referrer-policy | grep -q no-referrer && hdr content-security-policy | grep -q sandbox && hdr server | grep -qx nginx' "$(cat "$HDR")"
get "/s-$A_SECRET/awg20.conf" -A "curl/8.5.0"
check "awg20.conf → 200, байт в байт export_conf anna awg2, файл anna-2_0.conf (Н-2: как у страницы и user-key), подпись с линией" \
    '[ "$code" = 200 ] && [ "$(cat "$BODY")" = "$CONF20" ] && [ "$(hdr content-disposition)" = "attachment; filename=\"anna-2_0.conf\"" ] && [ "$(hdr profile-title)" = "base64:$(printf "Анна · AmneziaWG 2.0" | base64)" ]' "code=$code $(head -c 200 "$BODY")"
get "/s-$A_SECRET/awgv2.conf"
check "синоним владельца awgv2.conf — тот же .conf линии 2.0 и то же имя файла" \
    '[ "$code" = 200 ] && [ "$(cat "$BODY")" = "$CONF20" ] && [ "$(hdr content-disposition)" = "attachment; filename=\"anna-2_0.conf\"" ]'
get "/s-$A_SECRET/awgv3.conf"
c31="$code"; b31=$(cat "$BODY")
get "/s-$A_SECRET/awg15.conf"
check "awgv3.conf → .conf 3.1, awg15.conf → .conf 1.5 (I1 на месте)" \
    '[ "$c31" = 200 ] && [ "$b31" = "$CONF31" ] && [ "$code" = 200 ] && [ "$(cat "$BODY")" = "$CONF15" ] && grep -q "^I1 = " "$BODY"'
get "/s-$A_SECRET/awg20.vpn"
check "awg20.vpn → строка vpn:// кодека ядра (та же, что export_vpn), показом в браузере" \
    '[ "$code" = 200 ] && [ "$(cat "$BODY")" = "$VPN20" ] && [ "$(hdr content-disposition)" = "inline; filename=\"anna-2_0.txt\"" ]' "$(head -c 120 "$BODY")"
# all.txt — подписка incy (docs.incy.cc, «Multiple AmneziaWG servers in one subscription»): строка на линию
get "/s-$A_SECRET/all.txt" -A "INCY/2.1 (Android 14)"
cp "$BODY" "$TMP/all.txt"
cat >"$TMP/incy.py" <<'EOPY'
import base64, sys, urllib.parse
rows = open(sys.argv[1], encoding="utf-8").read().split("\n")
assert rows[-1] == "" and len(rows) == 4, len(rows)
for row, line, path in zip(rows, ("3.1", "2.0", "1.5"), sys.argv[2:5]):
    assert row.startswith("amneziawg://"), row[:20]
    data, name = row[len("amneziawg://"):].split("#", 1)
    assert "=" not in data and "+" not in data and "/" not in data, line
    assert base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8") == open(path, encoding="utf-8").read(), line
    assert name == "AmneziaWG%20" + line and urllib.parse.unquote(name) == "AmneziaWG " + line, name
EOPY
check "all.txt → подписка incy: по строке amneziawg://<base64url без «=»>#<имя> на линию 3.1, 2.0, 1.5 — каждая раскодируется ровно в .conf своей линии (export_conf), имя «AmneziaWG 3.1» в процентной кодировке; text/plain, заголовки подписки, клиент — incy" \
    '[ "$code" = 200 ] && python3 "$TMP/incy.py" "$TMP/all.txt" "$TMP/c31.conf" "$TMP/c20.conf" "$TMP/c15.conf" && [ "$(hdr content-type)" = "text/plain; charset=utf-8" ] && [ "$(hdr profile-title)" = "base64:$(printf "Анна" | base64)" ] && [ "$(hdr profile-update-interval)" = 12 ] && [ -n "$(hdr subscription-userinfo)" ] && [ "$(jq -r .last_client "$SD/state.json")" = incy ]' \
    "code=$code $(python3 "$TMP/incy.py" "$TMP/all.txt" "$TMP/c31.conf" "$TMP/c20.conf" "$TMP/c15.conf" 2>&1 | tail -2) $(cat "$HDR")"
get "/s-$A_SECRET/all.vpn"
check "all.vpn → три строки vpn:// в порядке 3.1, 2.0, 1.5" \
    '[ "$code" = 200 ] && [ "$(wc -l <"$BODY" | tr -d " ")" = 3 ] && [ "$(sed -n 2p "$BODY")" = "$VPN20" ] && [ "$(grep -c "^vpn://" "$BODY")" = 3 ]'
for p in "/s-$A_SECRET/awg10.conf" "/s-$A_SECRET/awg2.conf" "/s-$A_SECRET/mihomo.yml" "/s-$A_SECRET/" "/s-$(printf 'Q%.0s' {1..43})/mihomo.yaml"; do
    get "$p"
    [ "$code" = 404 ] && cmp -s "$BODY" <(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import pages_server as p; sys.stdout.buffer.write(p.NGINX_404)' "$ROOT/lib") \
        || { bad "404 nginx-вида: ${p:0:12}…${p: -12}" "code=$code"; continue; }
done
ok "404 nginx-вида: линии 1.0 у человека нет, суффикс не из списка, голый адрес, чужой секрет"
FETCHES=$(jq -r .fetches "$SD/state.json")
get "/s-$A_SECRET/mihomo.yaml" -A "TelegramBot (like TwitterBot)"
check "роботу превью — 404, и забором это не считается" '[ "$code" = 404 ] && [ "$(jq -r .fetches "$SD/state.json")" = "$FETCHES" ]' "code=$code"
check "учёт заборов: fetches, last_fetch, last_suffix, last_client одним словом; адреса забиравшего нет; файл 600" \
    '[ "$FETCHES" -ge 6 ] && [ "$(jq -r .last_suffix "$SD/state.json")" = all.vpn ] && [ "$(jq -r .last_client "$SD/state.json")" = curl ] && jq -e ".last_fetch | test(\"^[0-9]{4}-\")" "$SD/state.json" >/dev/null && ! grep -q "127.0.0.1" "$SD/state.json" && [ "$(perm "$SD/state.json")" = 600 ]' "$(cat "$SD/state.json")"
check "аудит sub-fetch — раз в час на подписку, без адреса и секрета" \
    '[ "$(grep -c "\"sub-fetch\"" "$GOAMNEZIA_AUDIT_LOG")" = 1 ] && audit_has sub-fetch "user=anna" && ! grep -qF -- "$A_SECRET" "$GOAMNEZIA_AUDIT_LOG"'
run subs_list anna
check "subs_list видит заборы: fetches и last_fetch" '[ "$(jget ".subs[0].fetches")" = "$FETCHES" ] && [ -n "$(jget ".subs[0].last_fetch")" ]' "$out"
jq '.users |= map(if .name == "anna" then .traffic.rx = 1000 | .traffic.tx = 2000 | .quota_bytes = 10737418240 | .expires = "2026-12-01T00:00:00Z" else . end)' \
    "$USERS_FILE" >"$TMP/u.json" && mv -f "$TMP/u.json" "$USERS_FILE"
get "/s-$A_SECRET/awg31.conf"
check "Subscription-Userinfo из users.json: upload=rx, download=tx, total=лимит, expire=срок в секундах" \
    '[ "$code" = 200 ] && [ "$(hdr subscription-userinfo)" = "upload=1000; download=2000; total=10737418240; expire=$(ga_iso_epoch 2026-12-01T00:00:00Z)" ]' "$(hdr subscription-userinfo)"
users_disable anna >/dev/null 2>>"$ALL_ERR"
get "/s-$A_SECRET/awg20.conf"; c_off="$code"
get "/s-$A_SECRET/mihomo.yaml"
check "выключенный — 404 по всем суффиксам (у роутера остаётся прежний конфиг)" '[ "$c_off" = 404 ] && [ "$code" = 404 ]' "$c_off $code"
users_enable anna >/dev/null 2>>"$ALL_ERR"
get "/s-$A_SECRET/awg20.conf"
check "включили — тот же адрес снова 200, без перенастройки" '[ "$code" = 200 ] && [ "$(cat "$BODY")" = "$(export_conf anna awg2 2>/dev/null)" ]' "code=$code"
for i in $(seq 1 10); do get "/s-$(printf 'C%.0s' {1..42})$i/mihomo.yaml" -H "X-Real-IP: 198.51.100.77"; done
get "/s-$A_SECRET/mihomo.yaml" -H "X-Real-IP: 198.51.100.77"; c_ban="$code"
get "/s-$A_SECRET/mihomo.yaml" -H "X-Real-IP: 198.51.100.78"
check "бан перебора: 10 промахов с адреса → 404 и для верного секрета; соседний адрес не задет" '[ "$c_ban" = 404 ] && [ "$code" = 200 ]' "$c_ban $code"
check "журнал службы: ни одного секрета, путь — только длиной и началом" \
    '! grep -qF -- "$A_SECRET" "$TMP/server.log" && ! grep -qE "s-[A-Za-z0-9_-]{43}" "$TMP/server.log"' "$(tail -5 "$TMP/server.log")"

# ── 6. Перевыпуск ───────────────────────────────────────────────────────────
section "перевыпуск: новый адрес сразу, прежний — 404"
run subs_rotate anna
R_BASE=$(jget .base_url); R_SECRET=$(secret_of "$R_BASE")
check "subs_rotate: rc=0, новый секрет, generation=2, rotated записан, аудит sub-rotate без секрета" \
    '[ "$rc" = 0 ] && [[ "$R_SECRET" =~ $SECRET_RE ]] && [ "$R_SECRET" != "$A_SECRET" ] && [ "$(jget .generation)" = 2 ] && [ -n "$(jq -r .rotated "$SD/meta.json")" ] && audit_has sub-rotate "user=anna generation=2" && ! grep -qF -- "$R_SECRET" "$GOAMNEZIA_AUDIT_LOG"' "rc=$rc $(errs)"
check "A3-2: перевыпуск обнулил заборы — fetches=0, auto_fetches=0 ключом, без last_*, state.json 0600" \
    '[ "$(jq -c . "$SD/state.json")" = '"'"'{"fetches":0,"auto_fetches":0}'"'"' ] && [ "$(perm "$SD/state.json")" = 600 ] && [ "$(jq -r .fetches <<<"$(ga_subs_row "$SD")")" = 0 ] && [ -z "$(jq -r .last_client <<<"$(ga_subs_row "$SD")")" ]' "$(cat "$SD/state.json")"
get "/s-$A_SECRET/awg20.conf" -H "X-Real-IP: 192.0.2.1"; c_old="$code"
get "/s-$R_SECRET/awg20.conf" -H "X-Real-IP: 192.0.2.1"
check "прежний адрес — 404 сразу, новый — 200 сразу (служба перечитала каталог по отметке)" '[ "$c_old" = 404 ] && [ "$code" = 200 ]' "$c_old $code"
check "A3-2: счёт заборов — только нового адреса (404 прежнего не считается), ключ одной линии — не «обновится сама»" \
    '[ "$(jq -r .fetches "$SD/state.json")" = 1 ] && [ "$(jq -r .auto_fetches "$SD/state.json")" = 0 ] && [ "$(jq -r .last_suffix "$SD/state.json")" = awg20.conf ]' "$(cat "$SD/state.json")"

# ── 7. Удаление человека и сирота ───────────────────────────────────────────
section "удаление человека снимает подписку; сироту — тик"
run subs_create bob
B_SECRET=$(secret_of "$(jget .base_url)")
run users_del bob
check "users_del bob: подписка снята под тем же замком, аудит sub-revoke reason=user-del" \
    '[ "$rc" = 0 ] && [ ! -d "$SUBS_DIR/bob" ] && audit_has sub-revoke "user=bob reason=user-del"' "rc=$rc $(errs)"
get "/s-$B_SECRET/awg20.conf" -H "X-Real-IP: 192.0.2.2"
check "адрес удалённого — 404" '[ "$code" = 404 ]' "code=$code"
users_add bob "Тёзка" awg2 >/dev/null 2>>"$ALL_ERR"
get "/s-$B_SECRET/awg20.conf" -H "X-Real-IP: 192.0.2.2"
check "тёзка, заведённый позже, по старой ссылке ключей не получает (секрет стёрт)" '[ "$code" = 404 ]' "code=$code"
# Массовое удаление (users-del-bulk, 1.0.9) — тот же сброс подписки, по каждому из пачки, сразу
users_add dave "Дэйв" awg2 >/dev/null 2>>"$ALL_ERR"
run subs_create dave
D_SECRET=$(secret_of "$(jget .base_url)")
run users_del_bulk 1 dave "" ""
check "users_del_bulk names=dave: подписка снята сразу, аудит sub-revoke reason=user-del" \
    '[ "$rc" = 0 ] && [ ! -d "$SUBS_DIR/dave" ] && audit_has sub-revoke "user=dave reason=user-del"' "rc=$rc $out $(errs)"
get "/s-$D_SECRET/awg20.conf" -H "X-Real-IP: 192.0.2.4"
check "адрес удалённого массово — 404" '[ "$code" = 404 ]' "code=$code"
users_add carol "Кэрол" awg2 >/dev/null 2>>"$ALL_ERR"
run subs_create carol
C_SECRET=$(secret_of "$(jget .base_url)")
# «Удаление как в прежней версии»: запись ушла из users.json, подписка осталась
jq '.users |= map(select(.name != "carol"))' "$USERS_FILE" >"$TMP/u.json" && mv -f "$TMP/u.json" "$USERS_FILE"
get "/s-$C_SECRET/awg20.conf" -H "X-Real-IP: 192.0.2.3"; c_orphan="$code"
run subs_list
check "сирота: служба отвечает 404, в списке её нет" '[ "$c_orphan" = 404 ] && [ -z "$(jget ".subs[] | select(.name == \"carol\") | .name")" ]' "$c_orphan $out"
run subs_tick
check "subs_tick снимает сироту (removed=1, аудит owner_gone), действующая подписка остаётся" \
    '[ "$rc" = 0 ] && [ "$(jget .removed)" = 1 ] && [ ! -d "$SUBS_DIR/carol" ] && [ -d "$SUBS_DIR/anna" ] && audit_has sub-revoke "user=carol reason=owner_gone"' "$out $(errs)"

# ── 8. Пауза без домена ──────────────────────────────────────────────────────
section "сайт ушёл с домена — пауза; вернулся — адреса ожили"
run website_disable_site
check "website_disable_site: подписка на паузе — секрет цел, маршрут снят, служба остановлена (страниц нет), отзыва нет" \
    '[ "$rc" = 0 ] && [ "$(cat "$SD/secret")" = "$R_SECRET" ] && [ "$(ga_config_get .site.subs_location)" = false ] && [ ! -f "$UNIT" ] && ! audit_has sub-revoke "user=anna" && grep -qF "$(tf subs_paused_mode 1)" "$ERR"' "rc=$rc $(errs)"
check "…и vhost сайта снят, а не пересобран заново паузой подписок (флаг пишется без пересборки)" '[ ! -f "$CONF" ]' "$(ls -la "$GOAMNEZIA_NGINX_DIR"/sites-available 2>&1)"
run subs_show anna
check "без домена sub-show честно говорит «на паузе» (subs_need_domain), адресов не выдаёт" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = subs_need_domain ] && [ -z "$out" ]' "rc=$rc"
check "на паузе в ответе моста (как у перевыпуска без домена) адресов нет: base_url пуст, urls и clients — []" \
    'a=$(_ga_subs_answer "$SD" false) && [ "$(jq -c "[.base_url, .urls, .clients, .paused]" <<<"$a")" = "[\"\",[],[],true]" ]' \
    "$(_ga_subs_answer "$SD" false 2>&1 | jq -c "{base_url, urls, clients, paused}" 2>&1 | head -c 300)"
run subs_tick
check "тик на паузе ничего не включает" '[ "$rc" = 0 ] && [ ! -f "$UNIT" ] && [ "$(ga_config_get .site.subs_location)" != true ]' "$(errs)"
ga_config_merge '{"mode": "pro", "domain": "example.com", "site": {"cert": "letsencrypt"}}' >/dev/null 2>&1
run subs_tick
check "вернулись на домен → subs_tick включает службу и маршрут, адрес тот же" \
    '[ "$rc" = 0 ] && [ -f "$UNIT" ] && [ "$(ga_config_get .site.subs_location)" = true ] && [ "$(subs_show anna 2>/dev/null | jq -r .base_url)" = "$R_BASE" ]' "$out $(errs)"
ga_config_merge '{"domain": "new.example.com"}' >/dev/null 2>&1
check "смена домена: адрес — с новым хостом, путь тот же" \
    '[ "$(subs_show anna 2>/dev/null | jq -r .base_url)" = "https://new.example.com/s-$R_SECRET/" ]'
ga_config_merge '{"domain": "example.com"}' >/dev/null 2>&1

# ── 9. Бэкап ─────────────────────────────────────────────────────────────────
section "бэкап: subs/ в архиве; восстановление — только на сервер без подписок"
run create_backup ""
ARCH="$out"
mkdir -p "$TMP/unp" && tar xzf "$ARCH" -C "$TMP/unp" 2>/dev/null
check "в архиве subs/anna с секретом и meta" \
    '[ "$rc" = 0 ] && [ "$(cat "$TMP/unp"/*/subs/anna/secret)" = "$R_SECRET" ] && [ -f "$(echo "$TMP/unp"/*/subs/anna/meta.json)" ]' "rc=$rc $(errs)"
LIVE_SAVE="$TMP/subs.live"
mv "$SUBS_DIR" "$LIVE_SAVE"
GA_RESTORE_KEPT=""
_restore_subs_merge "$(echo "$TMP/unp"/*/subs)" 2>>"$ALL_ERR"
check "чистый сервер (каталога нет): подписки из копии, каталоги 700, файлы 600" \
    '[ "$(cat "$SUBS_DIR/anna/secret")" = "$R_SECRET" ] && [ "$(perm "$SUBS_DIR")" = 700 ] && [ "$(perm "$SUBS_DIR/anna/secret")" = 600 ]'
rm -rf "$SUBS_DIR"; mv "$LIVE_SAVE" "$SUBS_DIR"
printf 'X%.0s' {1..43} >"$TMP/unp/old-secret"
mkdir -p "$TMP/arch2/subs/anna" && cp "$TMP/unp/old-secret" "$TMP/arch2/subs/anna/secret"
GA_RESTORE_KEPT=""
_restore_subs_merge "$TMP/arch2/subs" 2>>"$ALL_ERR"
check "подписки на сервере есть — действующие остаются (копия не возвращает перевыпущенный адрес), kept_current: subs" \
    '[ "$(cat "$SD/secret")" = "$R_SECRET" ] && [[ " $GA_RESTORE_KEPT " == *" subs "* ]]'

# ── 9а. Страница подключения: подписка вместе со страницей, вкладка «Подписка» ──
# Решение владельца 04.10.2026: страница — две вкладки («Статический ключ» | «Подписка»), подписка заводится вместе со
# страницей (всеми путями: SSH, бот, панель, мини-апп, Ленивый Pro — все зовут pages_create), существующим людям
# сама не появляется. Адрес — не в HTML, а POST sub.json со своим одноразовым значением страницы (SEC50-05).
section "страница подключения: подписка вместе со страницей и вкладка «Подписка»"
page_data() { python3 -c '
import re,sys
m=re.search(r"<script type=.application/json. id=.ga-data.>(.*?)</script>", open(sys.argv[1],encoding="utf-8").read(), re.S)
print(m.group(1) if m else "{}")' "$1"; }
users_add pat "Пат" awg3,awg2 >/dev/null 2>>"$ALL_ERR"
check "новый человек без страницы — подписки у него нет (сама она не появляется)" '[ ! -e "$SUBS_DIR/pat" ]'
run pages_create pat 24h
PG_SECRET=$(jget .url | sed -E 's#^https://example\.com/p-([A-Za-z0-9_-]{43})/$#\1#')
PAT_SUB=$(cat "$SUBS_DIR/pat/secret" 2>/dev/null)
check "pages_create pat: страница и подписка вместе — subscription=created, каталог 700, секрет 600, аудит sub-create; адреса подписки в ответе нет" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .subscription)" = created ] && [[ "$PAT_SUB" =~ $SECRET_RE ]] && [ "$(perm "$SUBS_DIR/pat")" = 700 ] && [ "$(perm "$SUBS_DIR/pat/secret")" = 600 ] && audit_has sub-create "user=pat generation=1" && ! grep -qF -e "$PAT_SUB" "$OUT" && ! grep -qF -e "$PAT_SUB" "$ERR" && grep -qF "Подписка для pat создана вместе со страницей" "$ERR"' "rc=$rc $out $(errs)"
run pages_create pat 1h
check "вторая страница — та же подписка: subscription=exists, секрет прежний" \
    '[ "$rc" = 0 ] && [ "$(jget .subscription)" = exists ] && [ "$(cat "$SUBS_DIR/pat/secret")" = "$PAT_SUB" ]' "rc=$rc $out"
get "/p-$PG_SECRET/" -A "Mozilla/5.0 (Linux; Android 14)"
PG_DATA=$(page_data "$BODY")
check "страница: sub.on, линии 2.0 и 3.1, 12 ч, своё одноразовое значение reveal_sub; адреса подписки в HTML нет" \
    '[ "$code" = 200 ] && [ "$(jq -c .sub <<<"$PG_DATA")" = "{\"on\":true,\"hours\":12,\"lines\":[\"2.0\",\"3.1\"],\"short\":false,\"was\":false}" ] && [[ "$(jq -r .reveal_sub <<<"$PG_DATA")" =~ ^[A-Za-z0-9_-]{32}$ ]] && ! grep -qF -e "$PAT_SUB" "$BODY" && ! grep -q "/s-" "$BODY"' \
    "code=$code $(jq -c .sub <<<"$PG_DATA")"
check "приложения вкладки «Подписка»: FlClash и Clash Meta for Android на Android, Clash Verge Rev и Clash Party на компьютерах, Keenetic и AWG Manager на роутере, AmneziaWG и WG Tunnel; на «Статическом ключе» mihomo-клиентов нет" \
    '[ "$(jq -r "[.sub_apps[].id] | sort | join(\",\")" <<<"$PG_DATA")" = "amneziavpn,amneziawg,awgm,clash-meta,clash-party,clash-verge-rev,defaultvpn,flclash,incy,keenetic,wgtunnel" ] && [ -z "$(jq -r ".apps[] | select(.id == \"flclash\" or .id == \"clash-verge-rev\") | .id" <<<"$PG_DATA")" ] && [ "$(jq -r .sub_recommend.android <<<"$PG_DATA")" = flclash ] && [ "$(jq -r .sub_recommend.windows <<<"$PG_DATA")" = clash-verge-rev ]' \
    "$(jq -c "{sub: [.sub_apps[].id], rec: .sub_recommend}" <<<"$PG_DATA")"
check "…incy (all.txt) — на всех своих платформах, на iPhone «рекомендуем»; ключ vpn:// по ссылке (формат vpn, разово): AmneziaVPN на iPhone, Android и компьютерах (не на Android TV), DefaultVPN на iPhone; порядок — mihomo, txt (incy, AWG Manager), разовое" \
    '[ "$(jq -r ".sub_apps[] | select(.id == \"amneziavpn\") | [.sub.format, (.sub.once | tostring), (.platforms | keys | join(\"/\"))] | join(\",\")" <<<"$PG_DATA")" = "vpn,true,android/ios/linux/macos/windows" ] && [ "$(jq -r ".sub_apps[] | select(.id == \"defaultvpn\") | (.platforms | keys | join(\"/\"))" <<<"$PG_DATA")" = ios ] && [ "$(jq -r ".sub_apps[] | select(.id == \"incy\") | (.platforms | keys | join(\"/\"))" <<<"$PG_DATA")" = android/android_tv/ios/macos/tvos ] && [ "$(jq -r .sub_recommend.ios <<<"$PG_DATA")" = incy ] && [ "$(jq -r "[.sub_apps[].sub.format] | join(\",\")" <<<"$PG_DATA")" = "mihomo,mihomo,mihomo,mihomo,txt,txt,vpn,vpn,conf,conf,conf" ]' \
    "$(jq -c "[.sub_apps[] | {id, f: .sub.format, p: (.platforms | keys)}]" <<<"$PG_DATA")"
REVEAL_SUB=$(jq -r .reveal_sub <<<"$PG_DATA")
get "/p-$PG_SECRET/sub.json" -X POST -H "Content-Type: application/json" --data "{\"nonce\":\"$REVEAL_SUB\"}" -A "TelegramBot (like TwitterBot)"
c_bot="$code"
get "/p-$PG_SECRET/sub.json" -X POST -H "Content-Type: application/json" --data "{\"nonce\":\"$REVEAL_SUB\"}" -A "Mozilla/5.0 (Linux; Android 14)"
cp "$BODY" "$TMP/sub_answer.json"
check "POST sub.json: роботу превью — 404; человеку со значением страницы — адреса его подписки (all.yaml, all.txt, awg20.conf, .vpn его линий 2.0 и 3.1) и токен на QR" \
    '[ "$c_bot" = 404 ] && [ "$code" = 200 ] && [ "$(jq -r .sub.mihomo "$TMP/sub_answer.json")" = "https://example.com/s-$PAT_SUB/all.yaml" ] && [ "$(jq -r ".sub.conf[\"2.0\"]" "$TMP/sub_answer.json")" = "https://example.com/s-$PAT_SUB/awg20.conf" ] && [ "$(jq -r .sub.txt "$TMP/sub_answer.json")" = "https://example.com/s-$PAT_SUB/all.txt" ] && [ "$(jq -c .sub.vpn "$TMP/sub_answer.json")" = "{\"2.0\":\"https://example.com/s-$PAT_SUB/awg20.vpn\",\"3.1\":\"https://example.com/s-$PAT_SUB/awg31.vpn\"}" ] && [[ "$(jq -r .grant "$TMP/sub_answer.json")" =~ ^[A-Za-z0-9_-]{32}$ ]]' \
    "bot=$c_bot code=$code $(head -c 200 "$TMP/sub_answer.json")"
run subs_show pat
check "clients по линиям человека (у pat 3.1 и 2.0): у AmneziaVPN — awg31.vpn и awg20.vpn, у Keenetic — только awg20.conf (3.1 он не берёт), 1.5 нет нигде" \
    '[ "$rc" = 0 ] && [ "$(jget "[.clients[] | select(.id == \"amneziavpn\") | .items[].suffix] | join(\",\")")" = "awg31.vpn,awg20.vpn" ] && [ "$(jget "[.clients[] | select(.id == \"keenetic\") | .items[].suffix] | join(\",\")")" = "awg20.conf" ] && [ -z "$(jget ".clients[].items[] | select(.line == \"1.5\") | .suffix")" ]' \
    "$(jget "[.clients[] | {id, s: [.items[].suffix]}]" | head -c 700)"
# «Скопировать» у AmneziaVPN на странице берёт путь выданного адреса .vpn с того же источника — так и здесь
get "$(jq -r ".sub.vpn[\"3.1\"]" "$TMP/sub_answer.json" | sed -E 's#^https://[^/]+##')" -A "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
check "по выданному адресу .vpn — ключ vpn:// линии 3.1 (тот же, что export_vpn), из браузера телефона" \
    '[ "$code" = 200 ] && [ "$(cat "$BODY")" = "$(export_vpn pat awg3 2>/dev/null)" ] && grep -q "^vpn://" "$BODY"' "code=$code $(head -c 80 "$BODY")"
get "/p-$PG_SECRET/sub.json" -X POST -H "Content-Type: application/json" --data "{\"nonce\":\"$REVEAL_SUB\"}" -A "Mozilla/5.0"
c_again="$code"
get "/p-$PG_SECRET/sub.json" -A "Mozilla/5.0"
check "то же значение второй раз — 409, GET sub.json — 405" '[ "$c_again" = 409 ] && [ "$code" = 405 ]' "again=$c_again get=$code"
get "/s-$PAT_SUB/all.yaml" -A "FlClash/v0.8.99"
check "по выданному адресу — профиль mihomo обеих линий, первым в группе «Auto» (fallback): приложение выбирает линию само" \
    '[ "$code" = 200 ] && [ "$(grep -c "^  - name: \"Пат · AmneziaWG" "$BODY")" = 2 ] && grep -q "^    type: fallback$" "$BODY" && [ "$(grep -A3 "^  - name: \"goAmnezia\"" "$BODY" | sed -n 4p)" = "      - \"Auto\"" ]' \
    "code=$code $(grep -n "name:\|type:" "$BODY" | head -8)"
check "адрес подписки — ни в журнале службы, ни в аудите" \
    '! grep -qF -e "$PAT_SUB" "$TMP/server.log" && ! grep -qF -e "$PAT_SUB" "$GOAMNEZIA_AUDIT_LOG"' "$(grep -c "" "$TMP/server.log")"
# B2-2/A2-4 (CRITICS-FIX-2): отзыв страницы с галочкой «и подписку» перевыпускает подписку — адрес, который страница
# отдавала, больше не отдаёт ключи; без галочки подписка цела. Чья подписка — в записи страницы (subscription).
run pages_list pat
check "page-list: у страницы, которая завела подписку, subscription=created, у второй — exists" \
    '[ "$rc" = 0 ] && [ "$(jget "[.pages[].subscription] | sort | join(\",\")")" = "created,exists" ]' "$out"
run pages_create pat 1h
PG3_ID=$(jget .id)
run pages_revoke "$PG3_ID" off
get "/s-$PAT_SUB/all.yaml" -A "FlClash/v0.8.99"
check "отзыв без подписки (sub=off): sub_rotated=false, sub_present=null (не спрашивали, RR-2), адрес подписки прежний и отдаёт ключи" \
    '[ "$rc" = 0 ] && [ "$(jget .sub_rotated)" = false ] && [ "$(jget .sub_present)" = null ] && [ "$(cat "$SUBS_DIR/pat/secret")" = "$PAT_SUB" ] && [ "$code" = 200 ]' "rc=$rc code=$code $out $(errs)"
run pages_create pat 1h
PG4_ID=$(jget .id)
run pages_revoke "$PG4_ID" on
PAT_SUB2=$(cat "$SUBS_DIR/pat/secret" 2>/dev/null)
get "/s-$PAT_SUB/all.yaml" -A "FlClash/v0.8.99"; c_old="$code"
get "/s-$PAT_SUB2/all.yaml" -A "FlClash/v0.8.99"
check "отзыв с подпиской (sub=on): sub_rotated=true, прежний адрес подписки — 404 сразу, новый — 200; нового адреса в ответе и в аудите нет; аудит page-revoke и sub-rotate" \
    '[ "$rc" = 0 ] && [ "$(jget .sub_rotated)" = true ] && [ "$(jget .sub_present)" = true ] && [ "$(jget .user)" = pat ] && [[ "$PAT_SUB2" =~ $SECRET_RE ]] && [ "$PAT_SUB2" != "$PAT_SUB" ] && [ "$c_old" = 404 ] && [ "$code" = 200 ] && ! grep -qF -e "$PAT_SUB2" "$OUT" && ! grep -qF -e "$PAT_SUB2" "$ERR" && ! grep -qF -e "$PAT_SUB2" "$GOAMNEZIA_AUDIT_LOG" && audit_has page-revoke "id=$PG4_ID" && audit_has sub-rotate "user=pat generation=2" && grep -qF "Подписка pat перевыпущена" "$ERR"' \
    "rc=$rc old=$c_old new=$code $out $(errs)"
run pages_revoke "$PG3_ID" maybe
check "sub не on/off — invalid_toggle до отзыва" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_toggle ]' "rc=$rc $(errs)"
PAT_SUB="$PAT_SUB2"
run subs_revoke pat
get "/p-$PG_SECRET/" -A "Mozilla/5.0 (Linux; Android 14)"
PG_DATA=$(page_data "$BODY")
check "подписку отозвали — вкладка честно «сейчас выключена» (A3-7: страница отдавала подписку — sub.was): sub.on=false, значения reveal_sub нет" \
    '[ "$code" = 200 ] && [ "$(jq -r .sub.on <<<"$PG_DATA")" = false ] && [ "$(jq -r .sub.was <<<"$PG_DATA")" = true ] && [ "$(jq -r ".reveal_sub // \"none\"" <<<"$PG_DATA")" = none ]' "$(jq -c .sub <<<"$PG_DATA")"
# R-4: подписки нет, а отзыв — с sub=on (в записи страницы осталось created): не отказ, а «перевыпускать нечего».
run pages_create pat 1h
PG5_ID=$(jget .id)
run subs_revoke pat
run pages_revoke "$PG5_ID" on
check "отзыв с sub=on без подписки: rc=0, sub_rotated=false, sub_present=false, «подписки нет — перевыпускать нечего», без предупреждения об отказе" \
    '[ "$rc" = 0 ] && [ "$(jget .sub_rotated)" = false ] && [ "$(jget .sub_present)" = false ] && grep -qF "У pat подписки нет — перевыпускать нечего" "$ERR" && ! grep -qF "не удалось" "$ERR" && [ ! -e "$SUBS_DIR/pat" ]' \
    "rc=$rc $out $(errs)"
# B3-3: отзыв с keys=on перевыпускает ключи пользователя на профилях страницы — только те, что хранит сервер (ключ из
# приложения страница не отдавала); старый ключ больше не подключается, адрес в подсети прежний.
run pages_create pat 1h
PG6_ID=$(jget .id)
ga_users_json | jq '.users |= map(if .name == "pat" then .peers.awg2 |= del(.privkey) else . end)' | ga_users_write
PAT_OLD=$(ga_users_get pat | jq -c '.peers | map_values({pubkey, ip})')
run pages_revoke "$PG6_ID" off on
PAT_NEW=$(ga_users_get pat | jq -c '.peers | map_values({pubkey, ip})')
check "B3-3: отзыв с keys=on — keys_reissued=[awg3] (у awg2 ключа на сервере нет), ключ awg3 новый, адрес тот же, awg2 не тронут; аудит user-reissue-peer" \
    '[ "$rc" = 0 ] && [ "$(jget ".keys_reissued | join(\",\")")" = awg3 ] && [ "$(jget ".keys_failed | length")" = 0 ] && [ "$(jget .sub_present)" = null ] && [ "$(jq -r .awg3.pubkey <<<"$PAT_NEW")" != "$(jq -r .awg3.pubkey <<<"$PAT_OLD")" ] && [ "$(jq -r .awg3.ip <<<"$PAT_NEW")" = "$(jq -r .awg3.ip <<<"$PAT_OLD")" ] && [ "$(jq -c .awg2 <<<"$PAT_NEW")" = "$(jq -c .awg2 <<<"$PAT_OLD")" ] && audit_has user-reissue-peer "pat awg3" && ! audit_has user-reissue-peer "pat awg2" && grep -qF "Ключи pat перевыпущены (awg3)" "$ERR" && [ ! -d "$(ga_pages_dir)/$PG6_ID" ]' \
    "rc=$rc $out $(errs)"
run pages_create pat 1h
PG7_ID=$(jget .id)
run pages_revoke "$PG7_ID" off
check "B3-3: без keys ключи не трогаются — keys_reissued=null" \
    '[ "$rc" = 0 ] && [ "$(jget .keys_reissued)" = null ] && [ "$(ga_users_get pat | jq -c ".peers | map_values({pubkey, ip})")" = "$PAT_NEW" ]' "rc=$rc $out"
run pages_revoke "$PG7_ID" off maybe
check "B3-3: keys не on/off — invalid_toggle" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_toggle ]' "rc=$rc $(errs)"
# Н-1 (CRITICS-FIX-4): страница только на awg2, подписка отдаёт и awg3. Отзыв с подпиской и ключами перевыпускает ключи
# обоих профилей: по адресу подписки получатель ссылки забрал и ключ awg3. Счёт заборов прежнего адреса — в meta.
users_add kim "Ким" awg3,awg2 >/dev/null 2>>"$ALL_ERR"
run subs_create kim
run pages_create kim 1h awg2
PG8_ID=$(jget .id)
KP_OFF=$(ga_pages_revoke_key_profiles kim '["awg2"]' off)
KP_ON=$(ga_pages_revoke_key_profiles kim '["awg2"]' on)
check "Н-1: чьи ключи перевыпустит отзыв — без подписки только awg2 страницы, с подпиской ещё awg3 линии 3.1" \
    '[ "$KP_OFF" = "{\"page\":[\"awg2\"],\"sub\":[]}" ] && [ "$KP_ON" = "{\"page\":[\"awg2\"],\"sub\":[\"awg3\"]}" ]' "off=$KP_OFF on=$KP_ON"
printf '%s\n' '{"fetches": 2, "auto_fetches": 0, "last_fetch": "2026-10-16T08:00:00Z", "last_suffix": "all.yaml"}' >"$SUBS_DIR/kim/state.json"
KIM_OLD=$(ga_users_get kim | jq -c '.peers | map_values(.pubkey)')
run pages_revoke "$PG8_ID" on on
KIM_NEW=$(ga_users_get kim | jq -c '.peers | map_values(.pubkey)')
SUB_ROW=$(subs_list kim 2>/dev/null | jq -c '.subs[0]')
check "Н-1: отзыв с подпиской и ключами — keys_reissued=[awg2, awg3], оба ключа новые; в строке подписки fetches=0, prev_fetches=2 и время последнего забора прежнего адреса" \
    '[ "$rc" = 0 ] && [ "$(jget ".keys_reissued | join(\",\")")" = awg2,awg3 ] && [ "$(jget .sub_rotated)" = true ] && [ "$(jq -r .awg2 <<<"$KIM_NEW")" != "$(jq -r .awg2 <<<"$KIM_OLD")" ] && [ "$(jq -r .awg3 <<<"$KIM_NEW")" != "$(jq -r .awg3 <<<"$KIM_OLD")" ] && [ "$(jq -r "[.fetches, .prev_fetches, .prev_last_fetch] | map(tostring) | join(\",\")" <<<"$SUB_ROW")" = "0,2,2026-10-16T08:00:00Z" ] && grep -qF "Ключи kim перевыпущены (awg2, awg3)" "$ERR"' \
    "rc=$rc $out row=$SUB_ROW $(errs)"
run pages_create kim 1h awg2
PG9_ID=$(jget .id)
KIM_OLD="$KIM_NEW"
run pages_revoke "$PG9_ID" off on
KIM_NEW=$(ga_users_get kim | jq -c '.peers | map_values(.pubkey)')
check "Н-1: без подписки (sub=off) — только профиль страницы, awg3 не тронут" \
    '[ "$rc" = 0 ] && [ "$(jget ".keys_reissued | join(\",\")")" = awg2 ] && [ "$(jq -r .awg3 <<<"$KIM_NEW")" = "$(jq -r .awg3 <<<"$KIM_OLD")" ]' "rc=$rc $out"
# П-4: второй перевыпуск подряд (новый адрес не забирали) не затирает след утёкшего адреса нулём; забирали — след
# нового прежнего адреса.
run subs_rotate kim
SUB_ROW=$(subs_list kim 2>/dev/null | jq -c '.subs[0]')
check "П-4: перевыпуск подряд без заборов — prev_fetches=2 и время прежнего следа сохранены" \
    '[ "$rc" = 0 ] && [ "$(jq -r "[.fetches, .prev_fetches, .prev_last_fetch] | map(tostring) | join(\",\")" <<<"$SUB_ROW")" = "0,2,2026-10-16T08:00:00Z" ]' \
    "rc=$rc row=$SUB_ROW $(errs)"
printf '%s\n' '{"fetches": 1, "auto_fetches": 0, "last_fetch": "2026-10-17T09:30:00Z", "last_suffix": "all.yaml"}' >"$SUBS_DIR/kim/state.json"
run subs_rotate kim
SUB_ROW=$(subs_list kim 2>/dev/null | jq -c '.subs[0]')
check "П-4: забирали новый адрес — след меняется на него: prev_fetches=1, его время" \
    '[ "$rc" = 0 ] && [ "$(jq -r "[.fetches, .prev_fetches, .prev_last_fetch] | map(tostring) | join(\",\")" <<<"$SUB_ROW")" = "0,1,2026-10-17T09:30:00Z" ]' \
    "rc=$rc row=$SUB_ROW $(errs)"
# Н-5: перевыпуск — под замком заборов state.lock (flock), тем же, что берёт служба; на стенде без flock — заглушка.
mkdir -p "$TMP/fakeflock"
printf '#!/bin/sh\necho "$*" >>"%s/flock.log"\nexit 0\n' "$TMP" >"$TMP/fakeflock/flock"; chmod +x "$TMP/fakeflock/flock"
: >"$TMP/flock.log"
rm -f "$SUBS_DIR/kim/state.lock"
LK_OUT=$( umask 022; PATH="$TMP/fakeflock:$PATH" _ga_subs_with_state_lock "$SUBS_DIR/kim" echo locked-body ); LK_RC=$?
LK_UMASK=$( PATH="$TMP/fakeflock:$PATH" _ga_subs_with_state_lock "$SUBS_DIR/kim" true; umask )
PATH="$TMP/fakeflock:$PATH" _ga_subs_with_state_lock "$SUBS_DIR/kim" false; LK_RC2=$?
check "Н-5: _ga_subs_with_state_lock — flock -w 10 на state.lock, тело выполнено, код тела возвращён" \
    '[ "$LK_OUT" = locked-body ] && [ "$LK_RC" = 0 ] && [ "$LK_RC2" = 1 ] && grep -qE "^-w 10 [0-9]+$" "$TMP/flock.log" && [ -f "$SUBS_DIR/kim/state.lock" ]' \
    "out=$LK_OUT rc=$LK_RC rc2=$LK_RC2 $(cat "$TMP/flock.log")"
check "П-5: state.lock создаётся 0600 и при umask 022; umask оболочки после замка прежний" \
    '[ "$(perm "$SUBS_DIR/kim/state.lock")" = 600 ] && [ "$LK_UMASK" = "$(umask)" ]' \
    "perm=$(perm "$SUBS_DIR/kim/state.lock") umask=$LK_UMASK/$(umask)"
chmod 644 "$SUBS_DIR/kim/state.lock"
PATH="$TMP/fakeflock:$PATH" _ga_subs_with_state_lock "$SUBS_DIR/kim" true
check "П-5-2: state.lock 0644 (создан до П-5) — при взятии замка снова 0600" \
    '[ "$(perm "$SUBS_DIR/kim/state.lock")" = 600 ]' \
    "perm=$(perm "$SUBS_DIR/kim/state.lock")"
run users_del kim
users_add lin "Лин" awg2 >/dev/null 2>>"$ALL_ERR"
ga_config_merge '{"mode": "lite_site", "site": {"cert": "selfsigned"}}' >/dev/null 2>&1
run pages_create lin 24h
check "без домена с доверенным сертификатом страница не создаётся — и подписка тоже" \
    '[ "$rc" != 0 ] && [ ! -e "$SUBS_DIR/lin" ]' "rc=$rc $out"
ga_config_merge '{"mode": "pro", "domain": "example.com", "site": {"cert": "letsencrypt"}}' >/dev/null 2>&1
run users_del pat
run users_del lin
check "уборка: pat и lin удалены, страниц и подписок у них нет" '[ ! -e "$SUBS_DIR/pat" ] && [ ! -e "$SUBS_DIR/lin" ]' "$(errs)"

# ── 10. Мост: настоящий install.sh --action ──────────────────────────────────
section "мост install.sh --action=sub-*"
act() {   # act <действие> [--k=v …] → rc, last, nlines
    local action="$1"; shift
    "$BASH5" "$INSTALL" --action="$action" --json "$@" >"$OUT" 2>"$ERR"; rc=$?
    last=$(tail -n 1 "$OUT"); nlines=$(wc -l <"$OUT" | tr -d ' ')
    cat "$ERR" >>"$ALL_ERR"
    return 0
}
lget() { jq -r "$1" <<<"$last" 2>/dev/null; }
act sub-list
check "sub-list: одна строка JSON, success, без адресов" \
    '[ "$nlines" = 1 ] && [ "$(lget .status)" = success ] && [ "$(lget .count)" = 1 ] && ! grep -q "https://" "$OUT"' "$last"
act sub-show --name=anna
check "sub-show: тот же адрес" '[ "$(lget .status)" = success ] && [ "$(lget .base_url)" = "$R_BASE" ]' "$last"
act sub-create --name=dave
check "sub-create чужого → user_not_found" '[ "$(lget .status)" = error ] && [ "$(lget .code)" = user_not_found ]' "$last"
act sub-create --name='a b'
check "sub-create с негодным именем отвергает валидатор моста: bad_arg" '[ "$(lget .code)" = bad_arg ]' "$last"
act sub-rotate --name=bob
check "sub-rotate без подписки → sub_not_found" '[ "$(lget .code)" = sub_not_found ]' "$last"
# SEC46-02: адрес подписки — постоянный ключ к ключам человека; шпион jq пишет свои аргументы.
SPYBIN="$TMP/spybin"; mkdir -p "$SPYBIN"; : >"$TMP/argv.log"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\nexec "%s" "$@"\n' "$TMP/argv.log" "$(command -v jq)" >"$SPYBIN/jq"
chmod +x "$SPYBIN/jq"
PATH="$SPYBIN:$PATH" act sub-create --name=bob
SPY_SECRET=$(secret_of "$(lget .base_url)")
PATH="$SPYBIN:$PATH" act sub-show --name=bob
spy_show=$(lget .base_url)
printf '%s\n' '{"fetches": 4, "last_fetch": "2026-10-15T12:00:00Z", "last_suffix": "awg20.conf"}' >"$SUBS_DIR/bob/state.json"
PATH="$SPYBIN:$PATH" act sub-rotate --name=bob
SPY_SECRET2=$(secret_of "$(lget .base_url)")
mkdir -p "$TMP/menu" && ln -sfn "$ROOT/lib" "$TMP/menu/lib" && sed '$d' "$INSTALL" >"$TMP/menu/install.sh"
# «Показать адреса» открывает выбор приложения (поле clients): номер Keenetic — тем же фильтром, что у экрана; у него
# «Показать, что вставлять» печатает команду import с адресом awg20.conf. Ответ с адресом — через stdin, не argv.
SUBC_KEEN=$(printf '%s' "$last" | "$BASH5" -c 'source "$1" >/dev/null 2>&1; jq -r "${GA_SUBC_APPS_JQ} | map(.key) | index(\"keenetic\") // empty"' _ "$TMP/menu/install.sh")
printf '1\n%s\n2\n\n0\n\n0\n\n0\n' "$(( ${SUBC_KEEN:-98} + 1 ))" | PATH="$SPYBIN:$PATH" "$BASH5" -c 'source "$1" && _subs_screen bob' _ "$TMP/menu/install.sh" >"$TMP/menu.out" 2>&1
check "SEC46-02 шпион сработал: sub-create, sub-show и sub-rotate отдали адрес, экран SSH «Показать адреса» его напечатал" \
    '[[ "$SPY_SECRET" =~ $SECRET_RE ]] && [ "$spy_show" = "https://example.com/s-$SPY_SECRET/" ] && [[ "$SPY_SECRET2" =~ $SECRET_RE ]] && grep -qF "s-$SPY_SECRET2/awg20.conf" "$TMP/menu.out" && [ "$(wc -l <"$TMP/argv.log" | tr -d " ")" -gt 10 ]' \
    "$(tail -n 8 "$TMP/menu.out")"
check "SEC46-02 адрес подписки не прошёл через argv jq" \
    '! grep -qF -- "$SPY_SECRET" "$TMP/argv.log" && ! grep -qF -- "$SPY_SECRET2" "$TMP/argv.log"' "$(grep -F -- "$SPY_SECRET2" "$TMP/argv.log" | head -2 | cut -c1-200)"
check "экран SSH: выбор приложения по группам («Обновляется сам» — FlClash, «Разово» — Keenetic), команда import роутера; предупреждение о ключе" \
    '[[ "$SUBC_KEEN" =~ ^[0-9]+$ ]] && grep -q "Обновляется сам" "$TMP/menu.out" && grep -q "Разово" "$TMP/menu.out" && grep -q "FlClash" "$TMP/menu.out" && grep -q "Keenetic" "$TMP/menu.out" && grep -qF "wireguard import https://example.com/s-$SPY_SECRET2/awg20.conf" "$TMP/menu.out" && grep -q "Кто знает адрес" "$TMP/menu.out"' "keenetic=$SUBC_KEEN $(head -c 1500 "$TMP/menu.out")"
MENU_PLAIN=$(sed $'s/\x1b\\[[0-9;]*m//g' "$TMP/menu.out")
PREV_LINE=$(grep -F "Прежний адрес забирали:" <<<"$MENU_PLAIN" | head -1)
check "Н-1, П-6-2: экран подписки SSH после перевыпуска — «Забирали: ещё ни разу» и след «Прежний адрес забирали: 4 · <когда>» не длиннее 70 знаков" \
    'grep -qF "Забирали: ещё ни разу" <<<"$MENU_PLAIN" && [[ "$PREV_LINE" == "  Прежний адрес забирали: 4 · "*назад ]] && [ "${#PREV_LINE}" -le 70 ]' \
    "len=${#PREV_LINE} $(grep -F "абирали" <<<"$MENU_PLAIN")"
act sub-revoke --name=bob
check "sub-revoke: success, revoked=true, каталог снят" '[ "$(lget .status)" = success ] && [ "$(lget .revoked)" = true ] && [ ! -d "$SUBS_DIR/bob" ]' "$last"
act watch-tick
check "watch-tick несёт блок subs: active=1" '[ "$(lget .status)" = success ] && [ "$(lget .subs.active)" = 1 ]' "$last"
act sub-revoke --name=anna
check "последняя подписка отозвана: маршрут снят, служба остановлена (страниц нет)" \
    '[ "$(lget .status)" = success ] && [ "$(ga_config_get .site.subs_location)" = false ] && [ ! -f "$UNIT" ] && hasnt "location ~ \"\^/s-\[A-Za-z0-9_-\]\{43\}/\(" && hasnt "goamnezia_subs"' "$last $(tail -3 "$ERR")"
check "…а глушилка секретных путей на 80 и 443 осталась: забор по отозванному адресу — 404 без строки в журнале nginx" \
    '[ "$(grep -cF "$HIDE_LOC" "$CONF")" = 2 ] && [ "$(grep -F -A2 "$HIDE_LOC" "$CONF" | grep -c "access_log off;")" = 2 ]' "$(grep -n -A3 "/s-" "$CONF")"
get "/s-$R_SECRET/awg20.conf" -H "X-Real-IP: 192.0.2.9"
check "после отзыва адрес — 404" '[ "$code" = 404 ]' "code=$code"
ga_config_merge '{"mode": "lite", "domain": "", "site": {"cert": ""}}' >/dev/null 2>&1
act sub-create --name=anna
check "мост в lite → subs_need_domain" '[ "$(lget .status)" = error ] && [ "$(lget .code)" = subs_need_domain ]' "$last"
check "секретов подписок нет ни в аудите, ни в stderr прогона (кроме экрана SSH, который их показывает)" \
    '! grep -qE "s-[A-Za-z0-9_-]{43}" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF -- "$A_SECRET" "$ALL_ERR" && ! grep -qF -- "$R_SECRET" "$ALL_ERR"' "$(grep -oE "s-[A-Za-z0-9_-]{20,}" "$GOAMNEZIA_AUDIT_LOG" | head -2)"
kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null; SERVER_PID=""

# ── 11. Статические проверки самого модуля ───────────────────────────────────
section "модуль не ломает статические проверки"
check "bash -n lib/subs.sh" "\"${BASH:-bash}\" -n '$ROOT/lib/subs.sh'"
check "паритет ключей словаря ru/en" \
    'diff <(grep -oE "^I18N\[[a-z_]+\]" "$ROOT/lib/lang/ru.d/subs.sh" | sort) <(grep -oE "^I18N\[[a-z_]+\]" "$ROOT/lib/lang/en.d/subs.sh" | sort) >/dev/null'
check "модуль и словари перечислены в FILES загрузчика" \
    'for f in lib/subs.sh lib/lang/ru.d/subs.sh lib/lang/en.d/subs.sh; do grep -q "\"$f\"" "$ROOT/bootstrap.sh" || exit 1; done'
actions_ok() {
    local f="$ROOT/lib/actions.json" a
    for a in sub-list sub-create sub-show sub-rotate sub-revoke; do jq -e --arg a "$a" '.actions | has($a)' "$f" >/dev/null || return 1; done
    [ "$(jq -r '.actions["sub-list"].mcp' "$f")" = sub_list ] || return 1
    [ "$(jq -r '.actions["sub-list"].readonly' "$f")" = true ] || return 1
    for a in sub-create sub-show sub-rotate sub-revoke; do [ "$(jq -r --arg a "$a" '.actions[$a].mcp' "$f")" = null ] || return 1; done
    for a in sub-create sub-show sub-rotate; do [ "$(jq -r --arg a "$a" '.actions[$a].secret' "$f")" = true ] || return 1; done
    [ "$(jq -r '.actions["sub-rotate"].destructive' "$f")" = true ] && [ "$(jq -r '.actions["sub-revoke"].destructive' "$f")" = true ]
}
check "действия sub-* в lib/actions.json: агенту только sub-list, адреса — secret, перевыпуск и отзыв — destructive" 'actions_ok'
check "ни одной видимой строки вне t/tf: log_* в модуле зовут только словарь" \
    '! grep -nE "log_(info|warning|error|success|dim|step) +\"[^\$]" "$ROOT/lib/subs.sh"'

# ── Итог ─────────────────────────────────────────────────────────────────────
DONE=1
printf '\n══ ИТОГ: пройдено %s, провалено %s, пропущено %s\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
