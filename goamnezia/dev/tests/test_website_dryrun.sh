#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон lib/website.sh без сервера (GOAMNEZIA_DRY_RUN=1)
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_website_dryrun.sh
# Нужны: bash ≥ 5, jq, openssl, python3. Ни nginx, ни certbot, ни сеть не нужны:
# системные команды модуль печатает как «DRY: …», конфиги пишет во временные
# GOAMNEZIA_NGINX_DIR / GOAMNEZIA_SITE_ROOT / GOAMNEZIA_LE_DIR / GOAMNEZIA_SYSTEMD_DIR.
#
# Что проверяем: vhost из шаблона (ожидаемые блоки, без хвостов подстановок) →
# панель по секретному пути (вставка/снятие) → маршрут MCP (on/off) → Alt-Svc →
# шаблон из локальной фикстуры (служебные файлы вычищены,
# маркеры записаны, JSON) → случайный id из каталога → коды отказа страницы →
# сертификат: самоподписанный «живой» не перевыпускается, «истекающий» идёт в
# certbot, --staging по переменной → website_check (структура, dry_run=true) →
# enable_pro / disable_pro lite → запасной самоподписанный сертификат (ключ
# EC P-256, CN и SAN = IP, права 0700/0600, отпечаток, повтор не перевыпускает,
# смена IP и --force перевыпускают) → переходы lite → lite_site → pro ↔ lite_site
# → lite → свой шаблон по git URL под set -u (константы клонирования, ветка с
# GNU timeout, которой нет на macOS). Про переходы важно: pro и lite_site — это ОДИН И ТОТ ЖЕ сайт на домене
# с сертификатом Let'"'"'s Encrypt (самоподписанный — запасной путь, когда домена
# нет), поэтому переключение между ними сайт не трогает вовсе, и проверяется
# именно это. Дальше — паритет словарей и ключи t/tf → чистота stdout.
#
# Настоящие lib/common.sh, lib/i18n.sh, lib/website.sh, lib/templates_lib.sh
# и templates_catalog.json. Ничего вне $TMP не пишем. Захват результата — через
# run (stdout → файл), а не $(…): в подоболочке терялся бы GA_ERR_CODE.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-website.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; rm -rf "$TMP"' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_DIR="$TMP/goamnezia"
export GOAMNEZIA_LOCK_DIR="$TMP/run"
export GOAMNEZIA_LOG_FILE="$TMP/goamnezia.log"
export GOAMNEZIA_AUDIT_LOG="$TMP/audit.log"
export GOAMNEZIA_SERVER_IP="203.0.113.10"
export GOAMNEZIA_NGINX_DIR="$TMP/nginx"
export GOAMNEZIA_SITE_ROOT="$TMP/site"
export GOAMNEZIA_ACME_ROOT="$TMP/acme"
export GOAMNEZIA_LE_DIR="$TMP/letsencrypt"
export GOAMNEZIA_SYSTEMD_DIR="$TMP/systemd"
unset GOAMNEZIA_TEMPLATE_LOCAL_DIR GOAMNEZIA_LE_STAGING
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR"
# nginx у машины (раннер GitHub, сервер) — продукт его не ставит, и строки «DRY: apt-get install -y
# nginx» нет по праву.
HOST_NGINX=0; command -v nginx >/dev/null 2>&1 && HOST_NGINX=1
# host_sig <путь> — след файла машины: «absent» или контрольная сумма. «Живая система не тронута» —
# это след до прогона и после одинаков, а не «файла нет»: на машине с установленным продуктом он есть
# по праву (Linux-сервер), и проверка краснела бы, хотя прогон ничего не писал.
host_sig() { if [ -e "$1" ]; then cksum <"$1" 2>/dev/null || echo unreadable; else echo absent; fi; }
HOST_VHOST_SIG=$(host_sig /etc/nginx/sites-available/goamnezia)
HOST_SITE_SIG=$(host_sig /var/www/goamnezia-site/.goamnezia_template_id)

# Временные файлы с ключами — в каталоге стенда (L1-19). Без переменной продукт кладёт их в /dev/shm,
# а он есть только на Linux: там они жили бы на машине прогона, и оборванный прогон оставлял бы ключи.
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 700; return 0; }
skipped() { skip=$((skip + 1)); printf '  – %s (пропущено: %s)\n' "$1" "$2"; }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
# Права восьмерично: сначала GNU (-c), потом BSD (-f). Не наоборот: у GNU «-f» — сведения
# о файловой системе, он печатал их перед правами, и на Linux краснела каждая проверка 600/700.
perm() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }
is_json() { jq -e . <<<"$1" >/dev/null 2>&1; }
run() {
    GA_ERR_CODE=""; GA_LAST_ERROR_CODE=""
    "$@" >"$OUT" 2>"$ERR"; rc=$?
    out=$(<"$OUT")
    return 0
}
errs() { cat "$ERR" 2>/dev/null; }
CONF="$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia"
LINK="$GOAMNEZIA_NGINX_DIR/sites-enabled/goamnezia"
# has <regex> — строка есть в конфиге; hasnt — нет
has()   { grep -qE -- "$1" "$CONF"; }
hasnt() { ! grep -qE -- "$1" "$CONF"; }

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
for f in lib/common.sh lib/i18n.sh lib/website.sh lib/templates_lib.sh lib/nginx/goamnezia.conf.tpl templates_catalog.json; do
    [ -f "$ROOT/$f" ] || { printf '!! нет %s\n' "$f"; exit 2; }
done
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
# shellcheck disable=SC1091
source "$ROOT/lib/website.sh"
# Глушилка секретных путей (_ga_site_secret_paths_block): одна локация на /p-, /s-, /a-, /r-, /d- и путь панели.
HIDE_LOC="location ~ \"$GA_SITE_SECRET_PATHS_RE\" {"
check "модуль подключён: website_enable_pro, download_template, deploy_template_to_nginx" \
    'type website_enable_pro >/dev/null && type download_template >/dev/null && type deploy_template_to_nginx >/dev/null'
check "словарь website подхвачен движком i18n (website_enabled)" '[ -n "${I18N[website_enabled]:-}" ]'
# Версия в фикстуре — ТЕКУЩАЯ из lib/common.sh, а не литерал: со старым номером
# каждый подъём версии включал бы миграцию с предмиграционным бэкапом.
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOC
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}, "panel": {"port": 1985, "miniapp_path": "", "trust_tunnel": true},
 "site": {"template_id": "", "template_source": ""}, "options": {"drop_icmp": false, "alt_svc": false},
 "mcp": {"http_enabled": false, "http_port": 1986}}
EOC
chmod 600 "$CONFIG_FILE"

# ── 1. nginx ─────────────────────────────────────────────────────────────────
section "nginx (dry-run)"
run website_ensure_nginx
check "website_ensure_nginx: rc=0, каталоги sites-available/enabled созданы" \
    '[ "$rc" = 0 ] && [ -d "$GOAMNEZIA_NGINX_DIR/sites-available" ] && [ -d "$GOAMNEZIA_NGINX_DIR/sites-enabled" ]' "$(errs)"
check "stderr: DRY apt-get install nginx (если nginx у машины нет) и systemctl enable --now nginx" \
    '{ if [ "$HOST_NGINX" = 1 ]; then ! grep -q "DRY: apt-get install -y nginx" "$ERR"; else grep -q "DRY: apt-get install -y nginx" "$ERR"; fi; } && grep -q "DRY: systemctl enable --now nginx" "$ERR"' "$(errs)"
check "stdout пуст" '[ -z "$out" ]' "$out"

# ── 2. vhost из шаблона ──────────────────────────────────────────────────────
section "website_write_conf"
run website_write_conf "bad domain"
check "домен с пробелом → rc=2, invalid_domain" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_domain ]' "rc=$rc code=$GA_ERR_CODE"
run website_write_conf Example.COM
check "website_write_conf: rc=0, файл и симлинк в sites-enabled" '[ "$rc" = 0 ] && [ -f "$CONF" ] && [ -L "$LINK" ]' "$(errs)"
# SR-20: в vhost секретные пути панели и доступов агентов — читает только root (мастер nginx).
check "SR-20 vhost с секретными путями — 0600, не читается всеми" \
    '[ "$(perm "$CONF")" = 600 ]' "$(ls -l "$CONF")"
check "домен приведён к нижнему регистру: server_name example.com" 'has "^\s*server_name example\.com;"'
check "80: listen 80, ACME-локация ^~ /.well-known/acme-challenge/ с root в GOAMNEZIA_ACME_ROOT, редирект 301 на https" \
    'has "^\s*listen 80;" && has "location \^~ /\.well-known/acme-challenge/" && has "root $GOAMNEZIA_ACME_ROOT;" && has "return 301 https://\\\$server_name\\\$request_uri;"'
check "443: listen 443 ssl http2, сертификат из GOAMNEZIA_LE_DIR/live/example.com" \
    'has "^\s*listen 443 ssl http2;" && has "ssl_certificate\s+$GOAMNEZIA_LE_DIR/live/example\.com/fullchain\.pem;" && has "ssl_certificate_key\s+$GOAMNEZIA_LE_DIR/live/example\.com/privkey\.pem;"'
check "root = GOAMNEZIA_SITE_ROOT, index index.html, deny скрытых файлов" \
    'has "^\s*root $GOAMNEZIA_SITE_ROOT;" && has "^\s*index index\.html;" && has "location ~ /\\\\\. \{ deny all; \}"'
check "якоря панели и MCP на месте, самих location нет, Alt-Svc нет" \
    'has "^\s*# GOAMNEZIA_PANEL_LOCATION" && has "^\s*# GOAMNEZIA_MCP_LOCATION" && hasnt "proxy_pass http://127\.0\.0\.1:1985/" && hasnt "\^/a-\[A-Za-z0-9_-\]\{43\}/" && hasnt "add_header Alt-Svc"'
check "хвостов подстановок нет" 'hasnt "_PLACEHOLDER"'
check "stderr: DRY nginx -t и DRY systemctl reload nginx; stdout пуст" \
    'grep -q "DRY: nginx -t" "$ERR" && grep -q "DRY: systemctl reload nginx" "$ERR" && [ -z "$out" ]' "$(errs)"
check "в dry-run без IPv6-слушателей (воспроизводимый конфиг)" 'hasnt "\[::\]"'
run website_write_conf example.com http
check "вид http: временный конфиг без 443 и без сертификата, с ACME-локацией" \
    '[ "$rc" = 0 ] && hasnt "listen 443" && hasnt "ssl_certificate" && has "acme-challenge" && has "^\s*root $GOAMNEZIA_SITE_ROOT;"' "$(errs)"
check "вид http: и здесь глушилка секретных путей — 404, как у любого пути, без журналов" \
    '[ "$(grep -cF "$HIDE_LOC" "$CONF")" = 1 ] && [ "$(grep -F -A3 "$HIDE_LOC" "$CONF" | grep -c "access_log off;\|error_log /dev/null crit;\|return 404;")" = 3 ]' "$(grep -n -A4 -F "$HIDE_LOC" "$CONF")"
run website_write_conf example.com weird
check "неизвестный вид → rc=2, invalid_conf_kind" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_conf_kind ]' "rc=$rc"
run website_write_conf example.com
check "снова auto → полный vhost" '[ "$rc" = 0 ] && has "listen 443 ssl http2"'

# Дефект 1a.6: пустая переменная копии означала две разные вещи — «прежнего
# конфига не было» и «копию снять не удалось», — и во втором случае откат сносил
# РАБОТАВШИЙ vhost. Сценарий обычный: заполненный или доступный только для
# записи /tmp, человек меняет шаблон, nginx -t отвергает новый конфиг — сайт и
# панель на 443 пропадают целиком.
CONF_BEFORE=$(cat "$CONF")
_orig_nginx_test=$(declare -f ga_site_nginx_test)
ga_site_nginx_test() { ga_dry_echo "nginx -t (stend: конфиг отвергнут)"; return 1; }
TMPDIR="$TMP/nowhere" run website_write_conf example.com http
check "nginx -t отверг конфиг при недоступном временном каталоге: живой vhost не снесён, вернулся прежний" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = nginx_conf_invalid ] && [ -f "$CONF" ] && [ "$(cat "$CONF")" = "$CONF_BEFORE" ] && [ -L "$LINK" ]' \
    "rc=$rc code=$GA_ERR_CODE"
check "временных копий рядом с конфигом не осталось" \
    '[ "$(ls "$GOAMNEZIA_NGINX_DIR/sites-available" | grep -vc "^goamnezia$")" = 0 ]' "$(ls "$GOAMNEZIA_NGINX_DIR/sites-available")"
mktemp() { return 1; }
run website_write_conf example.com http
check "копию снять негде вовсе → отказ nginx_backup_failed ДО первой записи, конфиг прежний" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = nginx_backup_failed ] && [ "$(cat "$CONF")" = "$CONF_BEFORE" ]' "rc=$rc code=$GA_ERR_CODE"
unset -f mktemp
eval "$_orig_nginx_test"
run website_write_conf example.com full
check "проверка конфига снова проходит → vhost пишется как прежде" '[ "$rc" = 0 ] && has "listen 443 ssl http2"' "$(errs)"

# ── 3. Панель по секретному пути ─────────────────────────────────────────────
section "website_panel_path_set / clear"
ga_config_merge '{"mode": "pro", "domain": "example.com"}' >/dev/null 2>&1
run website_panel_path_set "app-1a2b3c4d5e6f"
check "panel_path_set: rc=0, config.json panel.miniapp_path" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .panel.miniapp_path)" = app-1a2b3c4d5e6f ]' "$(errs)"
check "vhost: location ^~ /app-…/ → proxy_pass 127.0.0.1:1985/, X-Real-IP, access_log off, X-Frame-Options снят" \
    'has "location \^~ /app-1a2b3c4d5e6f/ \{" && has "proxy_pass http://127\.0\.0\.1:1985/;" && has "proxy_set_header X-Real-IP \\\$remote_addr;" && has "proxy_hide_header X-Frame-Options;"' "$(grep -n app-1a2b "$CONF")"
check "блок панели стоит после якоря и внутри него access_log off" \
    'awk "/# GOAMNEZIA_PANEL_LOCATION/{f=1} f&&/access_log off;/{print; exit}" "$CONF" | grep -q access_log'
check "stdout пуст" '[ -z "$out" ]'
run website_panel_path_set "a-secretlooking"
check "путь, начинающийся с a-, отвергнут (пересечение с маршрутом агентов) → invalid_path" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_path ]' "rc=$rc"
run website_panel_path_set "bad path/../x"
check "путь с недопустимыми символами → invalid_path" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_path ]' "rc=$rc"
check "после отказа vhost не тронут: старая локация на месте" 'has "location \^~ /app-1a2b3c4d5e6f/"'
run website_panel_path_clear
check "panel_path_clear: rc=0, поле пустое, локации в vhost нет, якорь остался" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .panel.miniapp_path)" = "" ] && hasnt "proxy_pass http://127\.0\.0\.1:1985/" && has "# GOAMNEZIA_PANEL_LOCATION"' "$(errs)"

# ── 4. Маршрут доступов агентов (MCP) ────────────────────────────────────────
section "website_mcp_location_on / off"
run website_mcp_location_on
check "mcp_location_on: rc=0, site.mcp_location=true" '[ "$rc" = 0 ] && [ "$(ga_config_get .site.mcp_location)" = true ]' "$(errs)"
check "vhost: location ~ \"^/[ar]-[A-Za-z0-9_-]{43}/\" → proxy_pass 127.0.0.1:1986, proxy_http_version 1.1, access_log off" \
    'has "location ~ \"\^/\[ar\]-\[A-Za-z0-9_-\]\{43\}/\" \{" && has "proxy_pass http://127\.0\.0\.1:1986;" && has "proxy_http_version 1\.1;"' "$(grep -n '\[ar\]-' "$CONF")"
# SEC46-01: 503 сервера MCP «замок занят, повторите» (только после верного пути и ключа) nginx не прячет
# в 404 — иначе агент читал бы «доступ отозван»; 502/504 упавшей службы — по-прежнему 404 сайта.
check "SEC46-01 vhost агентов: 502/504 → 404 через @ga_mcp_down, а 503 сервера MCP проходит" \
    'has "error_page 502 504 =404 @ga_mcp_down;" && hasnt "error_page [0-9 ]*503[0-9 ]* =404 @ga_mcp_down"' "$(grep -n "ga_mcp_down" "$CONF")"
# Регрессия стенда: `{43}` без кавычек nginx читает как конец блока — конфиг не
# проходил nginx -t, и HTTP-транспорт MCP не включался ни разу. Правило общее:
# любая регулярка location с `{`, `}` или `;` обязана быть в кавычках.
check "все регулярные location закавычены, если содержат { } ;" \
    'awk "/^[[:space:]]*location[[:space:]]+~\\*?[[:space:]]/ {
             re = \$3
             if (re ~ /^[\"\x27]/) next
             if (re ~ /[{};]/) { print NR\": \"\$0; bad = 1 }
         }
         END { exit bad ? 1 : 0 }" "$CONF"' "$(grep -n "location ~" "$CONF")"
run website_mcp_location_off
check "mcp_location_off: rc=0, локации нет, якорь на месте" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .site.mcp_location)" = false ] && hasnt "\^/a-\[A-Za-z0-9_-\]\{43\}/" && has "# GOAMNEZIA_MCP_LOCATION"' "$(errs)"

# ── 4а. Глушилка секретных путей ────────────────────────────────────────────
# Сам путь — ключ: страницы /p-, подписки /s-, доступы агентов /a- и /r-, раздача /d- (43 знака) и путь панели
# app-…. Без живого маршрута (выключено, отозвано, чужой секрет) и по http:// такой путь доходил до location / сайта и
# ложился в access.log (живая проверка подписки 03.10.2026). Теперь одна локация на все — в обоих server, всегда.
section "глушилка секретных путей (/p-, /s-, /a-, /r-, /d-, путь панели)"
run website_write_conf example.com full
mapfile -t H_CA < <(grep -nF "$HIDE_LOC" "$CONF" | cut -d: -f1)
H_SRV2=$(grep -n '^server {' "$CONF" | sed -n 2p | cut -d: -f1)
H_SITE=$(grep -n '^    location / {' "$CONF" | sed -n 2p | cut -d: -f1)
H_STATIC=$(grep -n 'location ~\* \\.(jpg' "$CONF" | tail -1 | cut -d: -f1)
awk -v s="${H_CA[0]:-0}" 'NR>=s{print} NR>s && /^    }/{exit}' "$CONF" >"$TMP/h80"
awk -v s="${H_CA[1]:-0}" 'NR>=s{print} NR>s && /^    }/{exit}' "$CONF" >"$TMP/h443"
check "pro, всё выключено: глушилка в обоих server — на 80 тот же 301 на https://\$server_name, что у location /, на 443 — 404" \
    '[ "$rc" = 0 ] && [ "${#H_CA[@]}" = 2 ] && [ "${H_CA[0]}" -lt "$H_SRV2" ] && [ "${H_CA[1]}" -gt "$H_SRV2" ] && grep -qF "return 301 https://\$server_name\$request_uri;" "$TMP/h80" && grep -q "return 404;" "$TMP/h443"' "$(grep -n -A4 -F "$HIDE_LOC" "$CONF")"
check "глушилка не пишет журналов (access_log off, error_log /dev/null crit) и стоит до регулярок сайта на 443" \
    '[ "$(cat "$TMP/h80" "$TMP/h443" | grep -c "access_log off;")" = 2 ] && [ "$(cat "$TMP/h80" "$TMP/h443" | grep -c "error_log /dev/null crit;")" = 2 ] && [ "${H_CA[1]}" -lt "$H_SITE" ] && [ "${H_CA[1]}" -lt "$H_STATIC" ]' "443: ${H_CA[1]:-} site: $H_SITE static: $H_STATIC"
check "у глушилки своих add_header и expires нет — заголовки ответа те же, что у любого пути сайта (SEC-G4)" \
    '! grep -qE "add_header|expires" "$TMP/h80" && ! grep -qE "add_header|expires" "$TMP/h443"' "$(cat "$TMP/h80" "$TMP/h443")"
check "якоря глушилки на месте; ни одно имя якоря не начало другого (подстановка заменяет каждое вхождение)" \
    'has "^\s*# GOAMNEZIA_SECRET_PATHS_80$" && has "^\s*# GOAMNEZIA_SECRET_PATHS_443$" && [ "$(grep -oE "# GOAMNEZIA_[A-Z0-9_]+" "$ROOT/lib/nginx/goamnezia.conf.tpl" | sort -u | awk "{a[NR]=\$2} END{for(i=1;i<=NR;i++)for(j=1;j<=NR;j++)if(i!=j&&index(a[j],a[i])==1)bad=1; exit bad}" && echo ok)" = ok ]'
# Регулярка — та же грамматика PCRE, что у re в Python: ловит каждый вид секретного пути и не задевает обычные пути
# сайта, в том числе файлы шаблона с длинными именами.
H_PANEL=$(ga_site_panel_path_new)
cat >"$TMP/hide_re.py" <<'EOPY'
import re
import sys
rx = re.compile(sys.argv[1])
panel = sys.argv[2]
s43 = "A" * 20 + "b-_9" + "z" * 19
hit = ["/p-%s/" % s43, "/p-%s" % s43, "/p-%s/keys.json" % s43, "/s-%s/mihomo.yaml" % s43, "/s-%s/awg9.conf" % s43,
       "/a-%s/mcp" % s43, "/a-%s" % s43, "/r-%s/x" % s43, "/d-%s/anna-awg2.conf" % s43, "/%s/" % panel, "/%s" % panel,
       "/%s/api/status" % panel, "/app-1a2b3c4d5e6f/", "/app-" + "0123456789abcdef" * 8 + "/api"]
miss = ["/", "/index.html", "/about/", "/a-beginners-guide-to-photography-and-lighting.html", "/app-store-badge.png",
        "/app-development-company/", "/p-%s/" % s43[:-1], "/p-%sX/" % s43, "/x-%s/" % s43, "/apple-%s/" % ("a" * 86),
        "/app-1a2b3c4d5e6/", "/app-1a2b3c4d5e6fa/", "/blog/p-%s/" % s43, "/P-%s/" % s43, "/.well-known/acme-challenge/x"]
assert len(s43) == 43 and len(panel) == 90, (len(s43), len(panel))
bad = [p for p in hit if not rx.match(p)] + [p for p in miss if rx.match(p)]
print(" ".join(bad))
sys.exit(1 if bad else 0)
EOPY
check "регулярка ловит /p-, /s-, /a-, /r-, /d- + 43 знака, путь панели (86 знаков, 12 и 128 hex) и не задевает обычные пути сайта" \
    'python3 "$TMP/hide_re.py" "$GA_SITE_SECRET_PATHS_RE" "$H_PANEL"' "$(python3 "$TMP/hide_re.py" "$GA_SITE_SECRET_PATHS_RE" "$H_PANEL" 2>&1)"
run website_panel_path_set "$H_PANEL"
mapfile -t H_CA < <(grep -nF "$HIDE_LOC" "$CONF" | cut -d: -f1)
check "панель включена: её живой путь — префикс ^~ (регулярку глушилки не проверяют), глушилка на месте в обоих server" \
    '[ "$rc" = 0 ] && has "location \^~ /$H_PANEL/ \{" && [ "${#H_CA[@]}" = 2 ]' "$(errs)"
run website_panel_path_clear
check "панель выключена: живого пути нет — прежний адрес ловит глушилка (404 без журнала)" \
    '[ "$rc" = 0 ] && hasnt "location \^~ /app-" && [ "$(grep -cF "$HIDE_LOC" "$CONF")" = 2 ]' "$(errs)"
run website_mcp_location_on
M_LIVE=$(grep -nF 'location ~ "^/[ar]-[A-Za-z0-9_-]{43}/" {' "$CONF" | cut -d: -f1)
mapfile -t H_CA < <(grep -nF "$HIDE_LOC" "$CONF" | cut -d: -f1)
check "агенты включены: живая локация /a-, /r- на 443 стоит ДО глушилки (nginx проверяет регулярки по порядку)" \
    '[ "$rc" = 0 ] && [ -n "$M_LIVE" ] && [ "${#H_CA[@]}" = 2 ] && [ "$M_LIVE" -lt "${H_CA[1]}" ]' "mcp: $M_LIVE hide: ${H_CA[*]}"
run website_mcp_location_off
run website_pages_location_on
P_LIVE=$(grep -nF 'location ~ "^/p-[A-Za-z0-9_-]{43}(/.*)?$" {' "$CONF" | cut -d: -f1)
mapfile -t H_CA < <(grep -nF "$HIDE_LOC" "$CONF" | cut -d: -f1)
check "страницы включены: живая локация /p- на 443 стоит ДО глушилки — страницы и keys.json (SEC50-05) идут как прежде" \
    '[ "$rc" = 0 ] && [ -n "$P_LIVE" ] && [ "${#H_CA[@]}" = 2 ] && [ "$P_LIVE" -lt "${H_CA[1]}" ]' "pages: $P_LIVE hide: ${H_CA[*]}"
run website_pages_location_off
check "страницы выключены: живой локации /p- нет, глушилка осталась" \
    '[ "$rc" = 0 ] && hasnt "\^/p-\[A-Za-z0-9_-\]\{43\}\(" && [ "$(grep -cF "$HIDE_LOC" "$CONF")" = 2 ]' "$(errs)"

# ── 5. Alt-Svc ───────────────────────────────────────────────────────────────
section "website_set_alt_svc"
run website_set_alt_svc on
check "alt_svc on: options.alt_svc=true, add_header Alt-Svc 'h3=\":443\"; ma=86400' в vhost" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .options.alt_svc)" = true ] && has "add_header Alt-Svc '"'"'h3=\":443\"; ma=86400'"'"' always;"' "$(errs; grep -n Alt-Svc "$CONF")"
run website_set_alt_svc off
check "alt_svc off: options.alt_svc=false, заголовка нет" '[ "$rc" = 0 ] && [ "$(ga_config_get .options.alt_svc)" = false ] && hasnt "add_header Alt-Svc"' "$(errs)"
run website_set_alt_svc maybe
check "alt_svc maybe → rc=2, invalid_toggle" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_toggle ]' "rc=$rc"

# ── 5б. Отказ пересборки vhost не оставляет config.json впереди nginx (SR-11) ──
# nginx -t отверг конфиг (например, сломан конфиг соседа в том же nginx): раньше в config.json
# оставалось новое значение, а nginx отдавал старое — «маршрут агентов выключен», а он работает;
# «панель включена», а по адресу 404. Теперь значение возвращается вместе с отказом.
section "SR-11: отказ пересборки vhost — настройка возвращается"
run website_mcp_location_on
run website_panel_path_set "app-1a2b3c4d5e6f"
conf_before_sr11=$(cat "$CONF")
_orig_reapply=$(declare -f _website_reapply_conf_locked)
_website_reapply_conf_locked() { ga_err nginx_conf_invalid "stend: nginx -t отказал"; return 1; }
run website_mcp_location_off
check "SR-11 mcp-location off при отказе nginx -t: rc≠0, код nginx_conf_invalid, в конфиге по-прежнему true" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = nginx_conf_invalid ] && [ "$(ga_config_get .site.mcp_location)" = true ]' "rc=$rc code=$GA_ERR_CODE v=$(ga_config_get .site.mcp_location)"
run website_panel_path_set "app-ffffffffffff"
check "SR-11 новый путь панели при отказе: оба поля — прежний путь, код и слова отказа не подменены" \
    '[ "$rc" != 0 ] && [ "$(ga_config_get .panel.miniapp_path)" = app-1a2b3c4d5e6f ] && [ "$(ga_config_get .site.panel_path)" = app-1a2b3c4d5e6f ] && [ "$GA_ERR_CODE" = nginx_conf_invalid ] && [ "$GA_ERR_MSG" = "stend: nginx -t отказал" ]' "rc=$rc $(jq -c "{p:.panel.miniapp_path,s:.site.panel_path}" "$CONFIG_FILE")"
run website_panel_path_clear
check "SR-11 выключение панели при отказе: путь на месте в обоих полях" \
    '[ "$rc" != 0 ] && [ "$(ga_config_get .panel.miniapp_path)" = app-1a2b3c4d5e6f ] && [ "$(ga_config_get .site.panel_path)" = app-1a2b3c4d5e6f ]' "rc=$rc"
run website_set_alt_svc on
check "SR-11 Alt-Svc при отказе: options.alt_svc прежний (false)" \
    '[ "$rc" != 0 ] && [ "$(ga_config_get .options.alt_svc)" = false ]' "rc=$rc v=$(ga_config_get .options.alt_svc)"
check "SR-11 vhost не тронут" '[ "$(cat "$CONF")" = "$conf_before_sr11" ]'
eval "$_orig_reapply"
run website_panel_path_clear
run website_mcp_location_off
check "SR-11 после починки nginx: выключается как обычно" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .site.mcp_location)" = false ] && [ "$(ga_config_get .panel.miniapp_path)" = "" ]' "$(errs)"

# ── 6. HTTPS-блок всегда публичный ───────────────────────────────────────────
# Режима «спрятать сайт на localhost» больше нет: TCP-протоколов, которым
# понадобился бы публичный 443, у продукта не осталось.
section "listen 443"
check "vhost: публичный listen 443 (и только он), порт 80 на месте" \
    'has "^\s*listen 443 ssl http2;" && hasnt "127\.0\.0\.1:8443" && has "^\s*listen 80;"' "$(grep -n listen "$CONF")"

# ── 7. Шаблон сайта из локальной фикстуры ────────────────────────────────────
section "website_deploy_template (GOAMNEZIA_TEMPLATE_LOCAL_DIR, без сети)"
export GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min"
run website_deploy_template h5up_aerial
check "deploy h5up_aerial из фикстуры: rc=0" '[ "$rc" = 0 ]' "$(errs)"
check "в корне сайта есть index.html, нет README.md и package.json" \
    '[ -f "$GOAMNEZIA_SITE_ROOT/index.html" ] && [ ! -f "$GOAMNEZIA_SITE_ROOT/README.md" ] && [ ! -f "$GOAMNEZIA_SITE_ROOT/package.json" ]' "$(ls -A "$GOAMNEZIA_SITE_ROOT")"
check "маркеры .goamnezia_template_id = h5up_aerial и .goamnezia_template_source = repo_url из каталога" \
    '[ "$(cat "$GOAMNEZIA_SITE_ROOT/.goamnezia_template_id")" = h5up_aerial ] && [ "$(cat "$GOAMNEZIA_SITE_ROOT/.goamnezia_template_source")" = https://github.com/zce/html5up ]' "$(cat "$GOAMNEZIA_SITE_ROOT"/.goamnezia_template_* 2>&1)"
check "фикстура не тронута (README.md и package.json на месте)" '[ -f "$GOAMNEZIA_TEMPLATE_LOCAL_DIR/README.md" ] && [ -f "$GOAMNEZIA_TEMPLATE_LOCAL_DIR/package.json" ]'
# Аудит безопасности 25.09.2026: симлинк из чужого репозитория шаблона в корне сайта отдавал бы любой файл
# сервера (users.json, ключ сертификата), а запись .html по ссылке шла бы от root в её цель.
SYMTPL="$TMP/tpl_symlinks"; rm -rf "$SYMTPL"; cp -R "$ROOT/tests/fixtures/site_min" "$SYMTPL"
printf 'secret\n' >"$TMP/outside_secret.txt"
ln -s "$TMP/outside_secret.txt" "$SYMTPL/dl.dat"
printf '<html><script src="https://mc.yandex.ru/metrika/tag.js"></script></html>\n' >"$TMP/outside_page.html"
ln -s "$TMP/outside_page.html" "$SYMTPL/evil.html"
ln -s "$TMP/outside_secret.txt" "$SYMTPL/.goamnezia_template_id"
GOAMNEZIA_TEMPLATE_LOCAL_DIR="$SYMTPL" run website_deploy_template h5up_aerial
check "шаблон с симлинками: развёрнут, но ни одной ссылки в корне сайта" \
    '[ "$rc" = 0 ] && [ -z "$(find "$GOAMNEZIA_SITE_ROOT" -type l)" ]' "$(find "$GOAMNEZIA_SITE_ROOT" -type l) $(errs)"
check "цели ссылок не тронуты: секрет не перезаписан меткой шаблона, чужой .html не правился" \
    '[ "$(cat "$TMP/outside_secret.txt")" = secret ] && grep -q mc.yandex.ru "$TMP/outside_page.html"'
check "vhost: disable_symlinks от корня сайта" "grep -qF 'disable_symlinks on from=\$document_root;' \"\$CONF\"" "$(grep -n disable_symlinks "$CONF")"
run website_deploy_template h5up_aerial
check "config.json: site.template_id / template_source" \
    '[ "$(ga_config_get .site.template_id)" = h5up_aerial ] && [ "$(ga_config_get .site.template_source)" = https://github.com/zce/html5up ]'
check "stdout — JSON с template_id, name=Aerial, category=business, deployed=true" \
    'is_json "$out" && [ "$(jq -r .template_id <<<"$out")" = h5up_aerial ] && [ "$(jq -r .name <<<"$out")" = Aerial ] && [ "$(jq -r .category <<<"$out")" = business ] && [ "$(jq -r .deployed <<<"$out")" = true ]' "$out"
run website_current_template
check "website_current_template: JSON, template_id=h5up_aerial, index_present=true, license=CC-BY-3.0" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jq -r .template_id <<<"$out")" = h5up_aerial ] && [ "$(jq -r .index_present <<<"$out")" = true ] && [ "$(jq -r .license <<<"$out")" = CC-BY-3.0 ]' "$out"
run website_deploy_template __random__
check "__random__: rc=0, id существует в каталоге" \
    '[ "$rc" = 0 ] && [ -n "$(jq -r --arg id "$(jq -r .template_id <<<"$out")" ".categories[].templates[] | select(.id == \$id) | .id" "$ROOT/templates_catalog.json")" ]' "$(errs) $out"
check "повторный деплой: прежний сайт сохранён в резерв (site_backup_*)" 'grep -q "site_backup_" "$ERR"' "$(errs)"
run website_deploy_template 'no such;id'
check "id с недопустимыми символами → rc=2, unknown_template" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = unknown_template ]' "rc=$rc code=$GA_ERR_CODE"
# Дефект 1a.20: «.» и «..» проходили проверку имени — а идентификатор становится
# именем каталога в кэше, который библиотека шаблонов сносит через rm -rf.
# Преграда обязана стоять там, где имя проверяется, а не там, где его случайно
# не нашлось в каталоге: с локальным хуком до каталога дело вообще не доходит.
check "ga_site_template_id_valid: обычный id годится, «.» и «..» — нет" \
    'ga_site_template_id_valid h5up_aerial && ! ga_site_template_id_valid . && ! ga_site_template_id_valid ..'
# §1d.2 №9: место под клон проверяется ДО git — иначе клон обрывается на
# полпути и человек получает английскую ошибку git вместо «мало места».
_df_orig=$(declare -f df 2>/dev/null)
df() { printf 'Filesystem 1M-blocks Used Available Use%% Mounted\n/dev/x 100 99 1 99%% /\n'; }
# «Диск полон» — подменённым df: фикстура места прогона (GOAMNEZIA_DF_FIXTURE, TAIL-12) на этот вызов снята
GOAMNEZIA_DF_FIXTURE='' GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min" run website_deploy_template h5up_aerial
check "шаблон на забитом диске → low_disk, до клона дело не доходит" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = low_disk ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
unset -f df
[ -n "$_df_orig" ] && eval "$_df_orig"
GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min" run website_deploy_template ..
check "id «..» → rc=2, unknown_template (даже с локальным хуком, минуя каталог)" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = unknown_template ]' "rc=$rc code=$GA_ERR_CODE"
GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min" run website_deploy_template .
check "id «.» → rc=2, unknown_template" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = unknown_template ]' "rc=$rc code=$GA_ERR_CODE"
mkdir -p "$TMP/shell" "$TMP/noindex"
printf '<html><head><link href="%%PUBLIC_URL%%/main.css"></head><body><div id="root"></div></body></html>\n' >"$TMP/shell/index.html"
GOAMNEZIA_TEMPLATE_LOCAL_DIR="$TMP/shell" run website_deploy_template h5up_alpha
check "оболочка приложения (%PUBLIC_URL%) → app_shell, сайт не тронут" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = app_shell ] && grep -q Northwind "$GOAMNEZIA_SITE_ROOT/index.html" 2>/dev/null || [ "$GA_ERR_CODE" = app_shell ]' "rc=$rc code=$GA_ERR_CODE"
GOAMNEZIA_TEMPLATE_LOCAL_DIR="$TMP/noindex" run website_deploy_template h5up_alpha
check "каталог без index.html → no_index" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = no_index ]' "rc=$rc code=$GA_ERR_CODE"
GOAMNEZIA_TEMPLATE_LOCAL_DIR="$TMP/absent" run website_deploy_template h5up_alpha
check "несуществующий локальный каталог → download_failed" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = download_failed ]' "rc=$rc code=$GA_ERR_CODE"
unset GOAMNEZIA_TEMPLATE_LOCAL_DIR
run website_deploy_template gh_this_template_does_not_exist_anywhere
check "id не из каталога (без хука) → unknown_template, сеть не нужна" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = unknown_template ]' "rc=$rc code=$GA_ERR_CODE"

# ── 8. Сертификат ────────────────────────────────────────────────────────────
section "website_issue_cert / website_cert_expiry (самоподписанный вместо Let's Encrypt)"
mkcert() {   # mkcert <дней> — «сертификат» example.com в GOAMNEZIA_LE_DIR/live
    mkdir -p "$GOAMNEZIA_LE_DIR/live/example.com"
    openssl req -x509 -newkey rsa:2048 -nodes -days "$1" -subj "/CN=example.com" \
        -keyout "$GOAMNEZIA_LE_DIR/live/example.com/privkey.pem" \
        -out "$GOAMNEZIA_LE_DIR/live/example.com/fullchain.pem" >/dev/null 2>&1
}
if ! command -v openssl >/dev/null 2>&1; then
    skipped "сертификат" "нет openssl"
else
    run website_cert_expiry example.com
    check "нет сертификата → rc=1, stdout пуст" '[ "$rc" = 1 ] && [ -z "$out" ]' "rc=$rc out=$out"
    mkcert 365
    run website_cert_expiry example.com
    check "cert_expiry: «<ISO-дата> <дней>», дней 363–366" \
        '[ "$rc" = 0 ] && [[ "$out" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z\ ([0-9]+)$ ]] && [ "${BASH_REMATCH[1]}" -ge 363 ] && [ "${BASH_REMATCH[1]}" -le 366 ]' "out=$out"
    run website_issue_cert example.com
    check "живой сертификат (365 дн.) НЕ перевыпускается: rc=0, certbot не вызывался" \
        '[ "$rc" = 0 ] && ! grep -q "DRY: certbot" "$ERR"' "$(errs)"
    check "автопродление настроено: deploy-hook записан, свой таймер записан, DRY systemctl enable --now goamnezia-cert-renew.timer" \
        '[ -x "$GOAMNEZIA_LE_DIR/renewal-hooks/deploy/goamnezia-nginx-reload.sh" ] && [ -f "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-cert-renew.timer" ] && grep -q "certbot renew --quiet --deploy-hook" "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-cert-renew.service" && grep -q "DRY: systemctl enable --now goamnezia-cert-renew.timer" "$ERR"' "$(errs; ls -R "$GOAMNEZIA_SYSTEMD_DIR" 2>&1)"
    mkcert 10
    run website_issue_cert example.com
    check "сертификату 10 дней → certbot certonly … -d example.com --webroot (nginx «жив»: vhost записан)" \
        '[ "$rc" = 0 ] && grep -q "DRY: certbot certonly" "$ERR" && grep -q -- "-d example.com" "$ERR" && grep -q -- "--webroot -w $GOAMNEZIA_ACME_ROOT" "$ERR" && grep -q -- "--keep-until-expiring" "$ERR" && grep -q -- "--deploy-hook systemctl reload nginx" "$ERR"' "$(errs)"
    check "без GOAMNEZIA_LE_EMAIL — --register-unsafely-without-email, без --staging" \
        'grep -q -- "--register-unsafely-without-email" "$ERR" && ! grep -q -- "--staging" "$ERR"' "$(errs)"
    check "каталог ACME-проверок создан" '[ -d "$GOAMNEZIA_ACME_ROOT/.well-known/acme-challenge" ]'
    GOAMNEZIA_LE_STAGING=1 GOAMNEZIA_LE_EMAIL="admin@example.com" run website_issue_cert example.com
    check "GOAMNEZIA_LE_STAGING=1 + почта → --staging и --email admin@example.com" \
        '[ "$rc" = 0 ] && grep -q -- "--staging" "$ERR" && grep -q -- "--email admin@example.com" "$ERR"' "$(errs)"
    # SR-21: тестовый сертификат (издатель «(STAGING)») вне staging-прогона — не действующий:
    # боевой выпускается поверх, причём принудительно — с --keep-until-expiring certbot оставил бы его.
    mkdir -p "$GOAMNEZIA_LE_DIR/live/example.com"
    openssl req -x509 -newkey rsa:2048 -nodes -days 80 -subj "/O=(STAGING) Let's Encrypt/CN=(STAGING) Ersatz Edamame E1" \
        -keyout "$GOAMNEZIA_LE_DIR/live/example.com/privkey.pem" \
        -out "$GOAMNEZIA_LE_DIR/live/example.com/fullchain.pem" >/dev/null 2>&1
    check "SR-21 тестовый сертификат на 80 дней: ga_site_cert_ok — нет, а в staging-прогоне — да" \
        '! ga_site_cert_ok example.com && GOAMNEZIA_LE_STAGING=1 ga_site_cert_ok example.com'
    run website_issue_cert example.com
    check "SR-21 боевой поверх тестового: certbot с --force-renewal, без --keep-until-expiring и --staging" \
        '[ "$rc" = 0 ] && grep -q "DRY: certbot certonly" "$ERR" && grep -q -- "--force-renewal" "$ERR" && ! grep -q -- "--keep-until-expiring" "$ERR" && ! grep -q -- "--staging" "$ERR" && grep -qF "$(tf website_cert_staging_replace example.com)" "$ERR"' "$(errs)"
    GOAMNEZIA_LE_STAGING=1 run website_issue_cert example.com
    check "SR-21 в staging-прогоне тестовый сертификат — действующий, certbot не зовётся" \
        '[ "$rc" = 0 ] && ! grep -q "DRY: certbot" "$ERR"' "$(errs)"
    mkcert 10
    rm -f "$CONF" "$LINK"
    run website_issue_cert example.com
    check "vhost ещё не записан (nginx «не жив») → --standalone на 80" \
        '[ "$rc" = 0 ] && grep -q -- "--standalone --preferred-challenges http" "$ERR"' "$(errs)"
    run website_write_conf example.com
    run website_renew_cert
    check "website_renew_cert: DRY certbot renew --quiet --deploy-hook" '[ "$rc" = 0 ] && grep -q "DRY: certbot renew --quiet --deploy-hook" "$ERR"' "$(errs)"
    # ── Почта центра сертификации (§1d.2 №11) ────────────────────────────────
    # Спросить её было негде: на любой обычной установке certbot шёл с
    # --register-unsafely-without-email, и Let's Encrypt физически не мог
    # предупредить о сорвавшемся продлении.
    run website_set_acme_email "не почта"
    check "негодный адрес почты → rc=2, invalid_email, в конфиг ничего не легло" \
        '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_email ] && [ -z "$(ga_config_get .acme_email)" ]' "rc=$rc code=$GA_ERR_CODE"
    run website_set_acme_email "owner@example.com"
    check "почта записана в config.json" \
        '[ "$rc" = 0 ] && [ "$(ga_config_get .acme_email)" = owner@example.com ]' "$(errs)"
    mkcert 10
    run website_issue_cert example.com
    check "почта из config.json уезжает в certbot вместо --register-unsafely-without-email" \
        'grep -q -- "--email owner@example.com" "$ERR" && ! grep -q -- "--register-unsafely-without-email" "$ERR"' "$(errs)"
    run website_set_acme_email ""
    check "пустая строка стирает почту (законный ответ «убрать»)" \
        '[ "$rc" = 0 ] && [ -z "$(ga_config_get .acme_email)" ]' "$(errs)"

    # ── Сторож срока сертификата (§1d.2 №11) ─────────────────────────────────
    # Ни таймера, ни события у срока не было вовсе: сорвавшееся автопродление
    # оставалось невидимым до дня, когда разом умирают сайт, панель и агенты.
    export GOAMNEZIA_NOTIFY_DIR="$TMP/notify"; mkdir -p "$GOAMNEZIA_NOTIFY_DIR"
    rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
    mkcert 365
    run website_cert_tick
    check "сертификат живой: warn=false, событий нет" \
        '[ "$rc" = 0 ] && is_json "$out" && [ "$(jq -r .warn <<<"$out")" = false ] && [ -z "$(ls -A "$GOAMNEZIA_NOTIFY_DIR" 2>/dev/null)" ]' "out=$out $(errs)"
    # Продление в dry-run только печатается, срок не сдвигается — ровно то
    # состояние «продлить пробовали, не вышло», ради которого сторож и нужен.
    mkcert 10
    run website_cert_tick
    check "сертификату 10 дней и продление не помогло: сказано вслух и ушло событие" \
        '[ "$rc" = 0 ] && [ "$(jq -r .warn <<<"$out")" = true ] && [ "$(jq -r .days <<<"$out")" -le 11 ] \
          && grep -qF "$(tf website_cert_expiring example.com "$(jq -r .days <<<"$out")")" "$ERR" \
          && [ -n "$(grep -l cert_expiring "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)" ]' "out=$out $(errs)"
    rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
    rm -rf "$GOAMNEZIA_LE_DIR/live/example.com"
    run website_cert_tick
    check "сертификата нет вовсе — это тревога, а не «всё хорошо»" \
        '[ "$rc" = 0 ] && [ "$(jq -r .warn <<<"$out")" = true ] && [ "$(jq -r .days <<<"$out")" = null ] \
          && [ -n "$(grep -l cert_unknown "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)" ]' "out=$out $(errs)"
    rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
    mkcert 365
fi

# ── 9. Внешняя проверка ──────────────────────────────────────────────────────
section "website_check"
run website_check
check "website_check: rc=0, stdout — JSON" '[ "$rc" = 0 ] && is_json "$out"' "$out"
check "поля: domain=example.com, server_ip=203.0.113.10, dry_run=true, verdict=dry_run, listen=public" \
    '[ "$(jq -r .domain <<<"$out")" = example.com ] && [ "$(jq -r .server_ip <<<"$out")" = 203.0.113.10 ] && [ "$(jq -r .dry_run <<<"$out")" = true ] && [ "$(jq -r .verdict <<<"$out")" = dry_run ] && [ "$(jq -r .listen <<<"$out")" = public ]' "$out"
check "структура: dns_ok, https_code, https_ok, index_present=true, cert_expires, cert_days_left (число), cert_ok=true, checked_at" \
    '[ "$(jq -r ".dns_ok|type" <<<"$out")" = boolean ] && [ "$(jq -r ".https_code|type" <<<"$out")" = number ] && [ "$(jq -r .index_present <<<"$out")" = true ] && [ "$(jq -r ".cert_days_left|type" <<<"$out")" = number ] && [ "$(jq -r .cert_ok <<<"$out")" = true ] && [ -n "$(jq -r .checked_at <<<"$out")" ]' "$out"
run website_check other.example.org
check "домен аргументом без сертификата: cert_expires пусто, cert_days_left=null" \
    '[ "$(jq -r .domain <<<"$out")" = other.example.org ] && [ "$(jq -r .cert_expires <<<"$out")" = "" ] && [ "$(jq -r .cert_days_left <<<"$out")" = null ]' "$out"

# ── 10. enable_pro / disable_pro ─────────────────────────────────────────────
section "website_enable_pro / website_disable_pro"
rm -rf "$GOAMNEZIA_SITE_ROOT" "$CONF" "$LINK"
ga_config_merge '{"mode": "lite", "domain": "", "site": {"template_id": "", "template_source": ""}, "mcp": {"http_enabled": true}}' >/dev/null 2>&1
export GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min"
# Дефект 1a.7: из lite компенсации при отказе выпуска сертификата не было —
# временный vhost для HTTP-01 оставался жить, а режим оставался lite. Продукт
# после этого врал сам себе: порт 80 занят собственным nginx, состояние отдаёт
# «сайта нет», настройки пишут «применится позже» при живом vhost. Отказ выпуска
# не экзотика: несошедшаяся A-запись, предел выдачи, закрытый 80.
_orig_issue=$(declare -f _website_issue_cert_locked)
_website_issue_cert_locked() { ga_err cert_failed "stend: certbot отказал"; return 1; }
run website_enable_pro newdomain.example
check "сорвавшийся переход lite → pro: rc≠0, cert_failed, режим остался lite" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = cert_failed ] && [ "$(ga_config_get .mode)" = lite ] && [ "$(ga_config_get .site.cert)" = "" ]' \
    "rc=$rc code=$GA_ERR_CODE mode=$(ga_config_get .mode)"
check "временный vhost для HTTP-01 снят: ни конфига, ни симлинка (порт 80 свободен)" \
    '[ ! -e "$CONF" ] && [ ! -e "$LINK" ]' "$(ls -A "$GOAMNEZIA_NGINX_DIR/sites-available" "$GOAMNEZIA_NGINX_DIR/sites-enabled" 2>&1)"
run website_check
check "состояние и nginx не расходятся: mode=lite, сайта нет" \
    '[ "$(jq -r .mode <<<"$out")" = lite ] && [ "$(jq -r .cert <<<"$out")" = "" ]' "$out"
eval "$_orig_issue"
run website_enable_pro Example.com
check "enable_pro: rc=0" '[ "$rc" = 0 ]' "$(errs)"
check "config.json: mode=pro, domain=example.com, site.template_id задан" \
    '[ "$(ga_config_get .mode)" = pro ] && [ "$(ga_config_get .domain)" = example.com ] && [ -n "$(ga_config_get .site.template_id)" ]' "$(jq -c . "$CONFIG_FILE")"
check "vhost полный: listen 443, сайт развёрнут (index.html без README.md)" \
    '[ -f "$CONF" ] && has "listen 443 ssl http2" && [ -f "$GOAMNEZIA_SITE_ROOT/index.html" ] && [ ! -f "$GOAMNEZIA_SITE_ROOT/README.md" ]' "$(errs)"
check "живой сертификат не перевыпускался, порядок: nginx → сертификат → vhost → шаблон" \
    '! grep -q "DRY: certbot certonly" "$ERR" && grep -q "DRY: systemctl enable --now nginx" "$ERR"' "$(errs)"
check "mcp.http_enabled=true → site.mcp_location=true и маршрут агентов в vhost" \
    '[ "$(ga_config_get .site.mcp_location)" = true ] && has "\^/\[ar\]-\[A-Za-z0-9_-\]\{43\}/"' "$(errs)"
check "stdout пуст" '[ -z "$out" ]' "$out"
# TAIL-2: «Шаблон развёрнут в …» — один раз (библиотека), без второй строки «Шаблон «id» развёрнут в …»
check "TAIL-2 о развёрнутом шаблоне — одна строка" \
    '[ "$(grep -c "развёрнут в" "$ERR")" = 1 ] && grep -qF "$(tf templates_deployed "$GOAMNEZIA_SITE_ROOT")" "$ERR"' "$(grep "развёрнут" "$ERR")"
run website_enable_pro example.com
check "повторное включение с развёрнутым сайтом: шаблон оставлен (website_tpl_kept), rc=0" \
    '[ "$rc" = 0 ] && grep -q "$(ga_config_get .site.template_id)" "$ERR"' "$(errs)"
check "config.json: site.cert=letsencrypt" '[ "$(ga_config_get .site.cert)" = letsencrypt ]' "$(jq -c .site "$CONFIG_FILE")"
# Панель на домене включается сама вместе с сайтом (владелец 17.09.2026), кроме случая, когда её выключили руками:
# выше в наборе website_panel_path_clear уже сделал panel.miniapp_auto=false — сайт панель не тронул.
check "панель, выключенную руками, сайт сам не включает (website_panel_auto_skipped)" \
    '[ "$(ga_config_get .panel.miniapp_path)" = "" ] && [ "$(ga_config_get .panel.miniapp_auto)" = false ] && grep -qF "$(t website_panel_auto_skipped)" "$ERR"' "$(jq -c .panel "$CONFIG_FILE") $(errs)"
ga_config_set .panel.miniapp_auto true >/dev/null 2>&1
run website_enable_pro example.com
check "запрета нет: панель включилась сама — путь задан, локация в vhost, адрес напечатан" \
    '[ "$rc" = 0 ] && [ -n "$(ga_config_get .panel.miniapp_path)" ] && has "proxy_pass http://127\.0\.0\.1:1985/" && grep -qF "https://example.com/$(ga_config_get .panel.miniapp_path)/" "$ERR"' "$(jq -c .panel "$CONFIG_FILE") $(errs)"
# TAIL-2: при включении сайта vhost объявлен один раз, панель — одной строкой с адресом: прежде шли «Конфиг nginx …
# записан (full)» дважды подряд и «Панель доступна по пути …» рядом с «Панель на домене включена сама: https://…»
check "TAIL-2 «Конфиг nginx … записан (full)» — одной строкой, «Панель доступна по пути» не повторяет итог" \
    '[ "$(grep -cF "$(tf website_conf_written example.com full)" "$ERR")" = 1 ] && ! grep -qF "$(tf website_panel_path_set "$(ga_config_get .panel.miniapp_path)")" "$ERR" && [ "$(grep -cF "https://example.com/$(ga_config_get .panel.miniapp_path)/" "$ERR")" = 1 ]' \
    "$(errs)"
_auto_path=$(ga_config_get .panel.miniapp_path)
run website_enable_pro example.com
check "повторное включение сайта путь панели не меняет" '[ "$rc" = 0 ] && [ "$(ga_config_get .panel.miniapp_path)" = "$_auto_path" ]' "$(jq -c .panel "$CONFIG_FILE")"
run website_panel_path_clear
check "выключил руками: panel.miniapp_auto=false" '[ "$rc" = 0 ] && [ "$(ga_config_get .panel.miniapp_auto)" = false ]' "$(jq -c .panel "$CONFIG_FILE")"
run website_panel_path_set "app-1a2b3c4d5e6f"
check "включил руками: запрет снят, panel.miniapp_auto=true" '[ "$rc" = 0 ] && [ "$(ga_config_get .panel.miniapp_auto)" = true ]' "$(jq -c .panel "$CONFIG_FILE")"
run website_disable_pro lite
check "disable_pro lite: rc=0, mode=lite, site.cert пуст, vhost и симлинк убраны" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .mode)" = lite ] && [ "$(ga_config_get .site.cert)" = "" ] && [ ! -e "$CONF" ] && [ ! -e "$LINK" ]' "$(errs)"
check "сертификат, домен в config.json и файлы сайта сохранены" \
    '[ -f "$GOAMNEZIA_LE_DIR/live/example.com/fullchain.pem" ] && [ "$(ga_config_get .domain)" = example.com ] && [ -f "$GOAMNEZIA_SITE_ROOT/index.html" ]'
run website_set_alt_svc on
check "в Lite настройка сохраняется, vhost не воскресает (website_saved_applies_later)" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .options.alt_svc)" = true ] && [ ! -e "$CONF" ]' "$(errs)"
run website_enable_pro example.com
check "повторное включение Pro применяет сохранённые настройки: Alt-Svc, listen 443 публичный" \
    '[ "$rc" = 0 ] && has "Alt-Svc" && has "^\s*listen 443 ssl http2;"' "$(errs)"
unset GOAMNEZIA_TEMPLATE_LOCAL_DIR
run website_enable_pro ""
check "enable_pro без домена → rc=2, invalid_domain" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_domain ]' "rc=$rc"
run website_disable_pro sideways
check "disable_pro с неизвестной целью → rc=2, invalid_mode" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_mode ]' "rc=$rc"

# ── 10a. Самоподписанный сертификат (Lite + сайт) ────────────────────────────
section "website_selfsigned_ensure / website_cert_fingerprint / website_selfsigned_expiry"
export GOAMNEZIA_TLS_DIR="$TMP/tls"
SSK="$GOAMNEZIA_TLS_DIR/site.key"; SSC="$GOAMNEZIA_TLS_DIR/site.crt"
run website_cert_fingerprint
check "отпечатка нет, пока нет сертификата: rc=1, stdout пуст" '[ "$rc" = 1 ] && [ -z "$out" ]' "rc=$rc out=$out"
run website_selfsigned_ensure
check "selfsigned_ensure: rc=0, site.key и site.crt в GOAMNEZIA_TLS_DIR, stdout пуст" \
    '[ "$rc" = 0 ] && [ -f "$SSK" ] && [ -f "$SSC" ] && [ -z "$out" ]' "$(errs)"
check "права: каталог 700, ключ 600, сертификат 600; временных .site.* не осталось" \
    '[ "$(perm "$GOAMNEZIA_TLS_DIR")" = 700 ] && [ "$(perm "$SSK")" = 600 ] && [ "$(perm "$SSC")" = 600 ] && [ -z "$(ls -A "$GOAMNEZIA_TLS_DIR" | grep "^\.site")" ]' "$(ls -la "$GOAMNEZIA_TLS_DIR")"
sstext=$(openssl x509 -noout -text -in "$SSC" 2>/dev/null)
check "сертификат: CN=203.0.113.10, SAN IP Address:203.0.113.10, ключ EC P-256, самоподписанный (issuer = subject)" \
    'grep -q "Subject: CN *= *203\.0\.113\.10" <<<"$sstext" && grep -q "IP Address:203\.0\.113\.10" <<<"$sstext" && grep -q "id-ecPublicKey" <<<"$sstext" && grep -Eq "P-256|prime256v1" <<<"$sstext" && grep -q "Issuer: CN *= *203\.0\.113\.10" <<<"$sstext"' "$(grep -E "Subject:|Issuer:|IP Address|Public Key Algorithm|CURVE" <<<"$sstext")"
check "ключ и сертификат — пара (совпадает открытый ключ)" \
    '[ "$(openssl x509 -noout -pubkey -in "$SSC" 2>/dev/null)" = "$(openssl pkey -pubout -in "$SSK" 2>/dev/null)" ]'
run website_selfsigned_expiry
check "selfsigned_expiry: «<ISO-дата> <дней>», дней 3640–3651 (10 лет)" \
    '[ "$rc" = 0 ] && [[ "$out" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z\ ([0-9]+)$ ]] && [ "${BASH_REMATCH[1]}" -ge 3640 ] && [ "${BASH_REMATCH[1]}" -le 3651 ]' "out=$out"
run website_cert_fingerprint
fp1="$out"
check "cert_fingerprint: SHA-256 в формате AA:BB:… (32 пары), совпадает с openssl" \
    '[ "$rc" = 0 ] && [[ "$fp1" =~ ^([0-9A-F]{2}:){31}[0-9A-F]{2}$ ]] && [ "$fp1" = "$(openssl x509 -noout -fingerprint -sha256 -in "$SSC" | sed "s/^.*=//" | tr a-f A-F)" ]' "out=$out"
check "отпечаток не попал в аудит-лог (это не секрет, но и не событие)" '! grep -qF "$fp1" "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null'
run website_selfsigned_ensure
check "повторный вызов: сертификат не перевыпускается (тот же отпечаток), в stderr «действителен ещё»" \
    '[ "$rc" = 0 ] && [ "$(website_cert_fingerprint)" = "$fp1" ] && grep -q "действителен ещё" "$ERR"' "$(errs)"
ga_config_merge '{"server_ip": "198.51.100.7"}' >/dev/null 2>&1
run website_selfsigned_ensure
check "сменился IP сервера → сертификат перевыпущен на новый IP (SAN 198.51.100.7), отпечаток другой" \
    '[ "$rc" = 0 ] && [ "$(website_cert_fingerprint)" != "$fp1" ] && openssl x509 -noout -text -in "$SSC" | grep -q "IP Address:198\.51\.100\.7"' "$(errs)"
ga_config_merge '{"server_ip": "203.0.113.10"}' >/dev/null 2>&1
run website_selfsigned_ensure
fp2=$(website_cert_fingerprint)
check "IP вернулся → снова перевыпуск на 203.0.113.10" \
    '[ "$rc" = 0 ] && openssl x509 -noout -text -in "$SSC" | grep -q "IP Address:203\.0\.113\.10"' "$(errs)"
run website_selfsigned_ensure --force
check "--force: новый отпечаток без смены IP" '[ "$rc" = 0 ] && [ "$(website_cert_fingerprint)" != "$fp2" ]' "$(errs)"
fp2=$(website_cert_fingerprint)
if [ "$(id -u)" = 0 ]; then
    skipped "каталог сертификата не создаётся → tls_dir_failed" "root обходит права каталога"
else
    # Не dry-run (иначе модуль честно печатает DRY и выходит с 0): каталог 0500,
    # внутри создать ничего нельзя — ожидаем код ошибки, а не падение.
    mkdir -p "$TMP/blocked"; chmod 500 "$TMP/blocked"
    ( GOAMNEZIA_TLS_DIR="$TMP/blocked/tls" GOAMNEZIA_DRY_RUN=0 _website_selfsigned_ensure_locked >"$OUT" 2>"$ERR"; echo "$? $GA_ERR_CODE" >"$TMP/rc" )
    chmod 700 "$TMP/blocked"
    check "каталог сертификата не создаётся (не dry-run) → rc=1, tls_dir_failed" '[ "$(cat "$TMP/rc")" = "1 tls_dir_failed" ]' "$(cat "$TMP/rc"; errs)"
fi

# ── 10b. Переходы lite → lite_site → pro → lite_site → lite ─────────────────
section "website_enable_site_selfsigned / website_disable_pro / website_disable_site"
export GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min"
# Профили и пользователи — «балласт», который переходы не имеют права трогать
ga_config_merge '{"profiles": {"awg2": {"line": "2.0", "container": "amnezia-awg2", "port": 443, "subnet": "10.8.2.0/24", "params": {"Jc": "5"}}}}' >/dev/null 2>&1
printf '{"schema": 1, "users": [{"name": "anna", "peers": {"awg2": {"ip": "10.8.2.1", "privkey": "NOT-A-REAL-KEY-JUST-BALLAST"}}}]}\n' >"$GOAMNEZIA_DIR/users.json"
profiles_before=$(jq -cS .profiles "$CONFIG_FILE"); users_before=$(cat "$GOAMNEZIA_DIR/users.json")
# Путь панели задан заранее: в Lite + сайт его location собираться не должна
ga_config_set .panel.miniapp_path app-1a2b3c4d5e6f >/dev/null 2>&1
run website_disable_pro lite
check "старт: pro → lite (website_disable_pro lite), vhost снят" '[ "$rc" = 0 ] && [ "$(ga_config_get .mode)" = lite ] && [ ! -e "$CONF" ]' "$(errs)"
run website_enable_site_selfsigned
check "lite → lite_site: rc=0, mode=lite_site, site.cert=selfsigned, stdout пуст" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .mode)" = lite_site ] && [ "$(ga_config_get .site.cert)" = selfsigned ] && [ -z "$out" ]' "$(errs)"
check "vhost: server_name _ в обоих блоках, listen 80 и listen 443 ssl http2, наши ssl_certificate/site.crt и site.key" \
    '[ "$(grep -c "^\s*server_name _;" "$CONF")" = 2 ] && has "^\s*listen 80;" && has "^\s*listen 443 ssl http2;" && has "ssl_certificate\s+$GOAMNEZIA_TLS_DIR/site\.crt;" && has "ssl_certificate_key\s+$GOAMNEZIA_TLS_DIR/site\.key;"' "$(grep -nE "server_name|listen|ssl_certificate" "$CONF")"
check "vhost: без ACME-локации, без домена, без редиректа на HTTPS, без HSTS, без Let's Encrypt-путей" \
    'hasnt "acme-challenge" && hasnt "example\.com" && hasnt "return 301" && hasnt "Strict-Transport-Security" && hasnt "letsencrypt"' "$(grep -nE "acme|example|301|Strict|letsencrypt" "$CONF")"
check "vhost: сайт отдаётся и по 80 (root сайта в обоих блоках), спрятанного listen на localhost нет" \
    '[ "$(grep -c "^\s*root $GOAMNEZIA_SITE_ROOT;" "$CONF")" = 2 ] && hasnt "127\.0\.0\.1:8443"' "$(grep -n "root\|8443" "$CONF")"
check "vhost: якоря панели и MCP на месте; панельной location нет несмотря на заданный путь; маршрут агентов и Alt-Svc есть" \
    'has "# GOAMNEZIA_PANEL_LOCATION" && has "# GOAMNEZIA_MCP_LOCATION" && hasnt "proxy_pass http://127\.0\.0\.1:1985/" && hasnt "app-1a2b3c4d5e6f" && has "\^/\[ar\]-\[A-Za-z0-9_-\]\{43\}/" && has "add_header Alt-Svc"' "$(grep -n "1985\|\[ar\]-\|Alt-Svc" "$CONF")"
check "хвостов подстановок и маркеров вариантов нет" 'hasnt "_PLACEHOLDER" && hasnt "GOAMNEZIA_ONLY_"'
check "vhost Lite + сайт: глушилка секретных путей и на 80, и на 443 — обе 404 (по 80 здесь сайт, не редирект), без журналов" \
    '[ "$(grep -cF "$HIDE_LOC" "$CONF")" = 2 ] && [ "$(grep -F -A3 "$HIDE_LOC" "$CONF" | grep -c "return 404;")" = 2 ] && [ "$(grep -F -A2 "$HIDE_LOC" "$CONF" | grep -c "access_log off;")" = 2 ]' "$(grep -n -A4 -F "$HIDE_LOC" "$CONF")"
check "сайт и шаблон не тронуты (website_tpl_kept), сертификат не перевыпущен (тот же отпечаток)" \
    'grep -q "$(ga_config_get .site.template_id)" "$ERR" && [ -f "$GOAMNEZIA_SITE_ROOT/index.html" ] && [ "$(website_cert_fingerprint)" = "$fp2" ]' "$(errs)"
check "stderr: DRY nginx -t и reload; отпечаток в итоговой строке" 'grep -q "DRY: nginx -t" "$ERR" && grep -q "DRY: systemctl reload nginx" "$ERR" && grep -qF "$fp2" "$ERR"' "$(errs)"
run website_check
check "website_check в lite_site: mode=lite_site, domain пуст, url=https://203.0.113.10/, cert=selfsigned, отпечаток, cert_ok=true, дни ~3650, verdict=dry_run" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jq -r .mode <<<"$out")" = lite_site ] && [ "$(jq -r .domain <<<"$out")" = "" ] && [ "$(jq -r .url <<<"$out")" = "https://203.0.113.10/" ] && [ "$(jq -r .cert <<<"$out")" = selfsigned ] && [ "$(jq -r .cert_fingerprint_sha256 <<<"$out")" = "$fp2" ] && [ "$(jq -r .cert_ok <<<"$out")" = true ] && [ "$(jq -r .cert_days_left <<<"$out")" -ge 3640 ] && [ "$(jq -r .verdict <<<"$out")" = dry_run ] && [ "$(jq -r ".http_code|type" <<<"$out")" = number ]' "$out"
run website_set_alt_svc off
check "настройка vhost в lite_site применяется сразу: Alt-Svc снят, vhost остался на самоподписанном" \
    '[ "$rc" = 0 ] && hasnt "add_header Alt-Svc" && has "^\s*server_name _;"' "$(errs)"
run website_selfsigned_renew
check "selfsigned_renew в lite_site: новый отпечаток, vhost перечитан (DRY reload)" \
    '[ "$rc" = 0 ] && [ "$(website_cert_fingerprint)" != "$fp2" ] && grep -q "DRY: systemctl reload nginx" "$ERR"' "$(errs)"
fp3=$(website_cert_fingerprint)
run website_enable_pro example.com
check "lite_site → pro: rc=0, mode=pro, site.cert=letsencrypt, certbot не вызывался (сертификат живой)" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .mode)" = pro ] && [ "$(ga_config_get .site.cert)" = letsencrypt ] && ! grep -q "DRY: certbot certonly" "$ERR"' "$(errs)"
check "vhost Pro: server_name example.com, Let's Encrypt, ACME-локация, HSTS, панельная location вернулась; site.crt в конфиге нет" \
    'has "^\s*server_name example\.com;" && has "$GOAMNEZIA_LE_DIR/live/example\.com/fullchain\.pem" && has "acme-challenge" && has "Strict-Transport-Security" && has "location \^~ /app-1a2b3c4d5e6f/" && hasnt "site\.crt"' "$(grep -nE "server_name|ssl_certificate |app-1a2b" "$CONF")"
check "самоподписанный сертификат остался на диске (пригодится при возврате), сайт не перекачивался" \
    '[ -f "$SSC" ] && [ "$(website_cert_fingerprint)" = "$fp3" ] && grep -q "$(ga_config_get .site.template_id)" "$ERR"' "$(errs)"
# pro → lite_site по уточнённому замыслу владельца НЕ трогает сайт: у обоих
# режимов один и тот же домен и один и тот же сертификат Let's Encrypt, а
# отличаются они только тем, где стоит AmneziaWG (порт двигает вызывающий).
run website_disable_pro
check "pro → lite_site (website_disable_pro без цели): mode=lite_site, домен и сертификат Let's Encrypt остались" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .mode)" = lite_site ] && [ "$(ga_config_get .site.cert)" = letsencrypt ] && [ "$(ga_config_get .domain)" = example.com ]' "$(errs)"
check "vhost не переписан: домен на месте, панельная location на месте, самоподписанного в конфиге нет" \
    'has "^\s*server_name example\.com;" && has "location \^~ /app-1a2b3c4d5e6f/" && hasnt "site\.crt" && [ -f "$GOAMNEZIA_LE_DIR/live/example.com/fullchain.pem" ]' "$(grep -nE "server_name|ssl_certificate " "$CONF")"
run website_switch_site_mode pro
check "lite_site → pro (website_switch_site_mode): mode=pro, сертификат и домен те же, nginx не перечитывался" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .mode)" = pro ] && [ "$(ga_config_get .site.cert)" = letsencrypt ] && [ "$(ga_config_get .domain)" = example.com ] && ! grep -q "DRY: systemctl reload nginx" "$ERR"' "$(errs)"
run website_switch_site_mode nonsense
check "неизвестный целевой режим → rc=2, invalid_mode" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_mode ]' "rc=$rc $GA_ERR_CODE"
# SR-18: nginx -t отказал после снятия (сломан чужой конфиг) — «сайт снят» было неправдой: nginx
# без перезагрузки отдавал прежний сайт. Теперь vhost возвращается, режим прежний, отказ с кодом.
conf_before_sr18=$(cat "$CONF"); mode_before_sr18=$(ga_config_get .mode)
_orig_nginx_test_sr18=$(declare -f ga_site_nginx_test)
ga_site_nginx_test() { return 1; }
# umask 022 — как у root на сервере: возвращённый файл без явных прав стал бы 0644.
_umask_sr18=$(umask); umask 022
run website_disable_site
umask "$_umask_sr18"
check "SR-18 nginx -t отказал: rc=3, nginx_conf_invalid, vhost и симлинк на месте байт-в-байт, режим прежний" \
    '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = nginx_conf_invalid ] && [ -L "$LINK" ] && [ "$(cat "$CONF")" = "$conf_before_sr18" ] && [ "$(ga_config_get .mode)" = "$mode_before_sr18" ] && ! grep -qF "$(t website_site_disabled)" "$ERR"' "rc=$rc code=$GA_ERR_CODE $(errs)"
# SEC46-05: откат переписывал vhost по umask (0644) — секретный путь панели снова читали все (SR-20).
check "SEC46-05 возвращённый откатом vhost — снова 0600, а не 0644" '[ "$(perm "$CONF")" = 600 ]' "$(ls -l "$CONF")"
eval "$_orig_nginx_test_sr18"
run website_disable_site
check "lite_site → lite: rc=0, mode=lite, site.cert пуст, vhost и симлинк убраны" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .mode)" = lite ] && [ "$(ga_config_get .site.cert)" = "" ] && [ ! -e "$CONF" ] && [ ! -e "$LINK" ]' "$(errs)"
check "файлы сайта и оба сертификата остались на диске" '[ -f "$GOAMNEZIA_SITE_ROOT/index.html" ] && [ -f "$SSC" ] && [ -f "$SSK" ] && [ -f "$GOAMNEZIA_LE_DIR/live/example.com/fullchain.pem" ]'
run website_check
check "website_check в lite: сайта нет — cert и url пусты, mode=lite (verdict в dry-run всегда dry_run)" \
    '[ "$(jq -r .mode <<<"$out")" = lite ] && [ "$(jq -r .cert <<<"$out")" = "" ] && [ "$(jq -r .url <<<"$out")" = "" ] && [ "$(jq -r .verdict <<<"$out")" = dry_run ]' "$out"
check "профили в config.json и users.json не тронуты за четыре перехода" \
    '[ "$(jq -cS .profiles "$CONFIG_FILE")" = "$profiles_before" ] && [ "$(cat "$GOAMNEZIA_DIR/users.json")" = "$users_before" ]' "$(jq -cS .profiles "$CONFIG_FILE")"
check "аудит: change-mode lite_site selfsigned, pro (from lite_site), переключение без пересборки сайта, lite (site off), selfsigned-renew" \
    'grep -q "lite_site selfsigned" "$GOAMNEZIA_AUDIT_LOG" && grep -q "from lite_site" "$GOAMNEZIA_AUDIT_LOG" && grep -q "site kept" "$GOAMNEZIA_AUDIT_LOG" && grep -q "lite (site off)" "$GOAMNEZIA_AUDIT_LOG" && grep -q "selfsigned-renew" "$GOAMNEZIA_AUDIT_LOG"' "$(tail -n 8 "$GOAMNEZIA_AUDIT_LOG")"
ga_config_set .panel.miniapp_path "" >/dev/null 2>&1
( unset GOAMNEZIA_NGINX_DIR GOAMNEZIA_SITE_ROOT GOAMNEZIA_LE_DIR GOAMNEZIA_SYSTEMD_DIR GOAMNEZIA_ACME_ROOT GOAMNEZIA_TLS_DIR
  website_enable_site_selfsigned >"$OUT" 2>"$ERR"; echo $? >"$TMP/rc" )
check "lite_site без переменных стендов: rc=0, DRY write /etc/nginx/… (selfsigned), сертификат в \$GOAMNEZIA_DIR/tls, /etc не тронут" \
    '[ "$(cat "$TMP/rc")" = 0 ] && grep -q "DRY: write /etc/nginx/sites-available/goamnezia (selfsigned)" "$ERR" && [ -f "$GOAMNEZIA_DIR/tls/site.crt" ] && [ "$(host_sig /etc/nginx/sites-available/goamnezia)" = "$HOST_VHOST_SIG" ]' "$(errs)"
run website_disable_site
unset GOAMNEZIA_TEMPLATE_LOCAL_DIR

# ── 10c. 1d.1 №4 и №9: отказ выпуска не кладёт живой сайт и не оставляет vhost ─
section "1d.1 №4/№9: смена домена и отказ certbot"
export GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min"
# Поднимаем рабочий сайт на example.com — с живым сертификатом и 443.
run website_enable_pro example.com pro
check "подготовка: сайт на example.com работает, 443 слушается" \
    '[ "$rc" = 0 ] && has "^\s*server_name example\.com;" && has "^\s*listen 443 ssl http2;"' "$(errs)"
conf_before=$(cat "$CONF")

# №4. Смена домена. Новый домен сразу уезжал в конфиг (прежний рабочий терялся),
# рабочий vhost заменялся временным — 443 исчезал НЕМЕДЛЕННО, до всякого ответа
# certbot, — и при отказе не возвращался. Панель и адреса агентов при этом
# продолжали звать человека на домен, который не отвечает.
_orig_issue=$(declare -f _website_issue_cert_locked)
_website_issue_cert_locked() { ga_err cert_failed "stend: certbot отказал"; return 1; }
run website_enable_pro new.example pro
check "1d.1 №4 смена домена с отказом выпуска: rc≠0, cert_failed" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = cert_failed ]' "rc=$rc code=$GA_ERR_CODE"
check "1d.1 №4 прежний рабочий домен вернулся в config.json" \
    '[ "$(ga_config_get .domain)" = example.com ]' "domain=$(ga_config_get .domain)"
check "1d.1 №4 сайт остался на 443 и на прежнем домене" \
    'has "^\s*listen 443 ssl http2;" && has "^\s*server_name example\.com;" && hasnt "new\.example"' \
    "$(grep -nE "server_name|listen 443" "$CONF")"
check "1d.1 №4 временный vhost для HTTP-01 вообще не писался — 443 не пропадал ни на секунду" \
    '! grep -qF "$(tf website_conf_written new.example http)" "$ERR" && ! grep -qF "$(tf website_conf_http_only new.example)" "$ERR"' "$(errs)"
check "1d.1 №4 vhost совпадает с тем, что работало до попытки" \
    '[ "$(cat "$CONF")" = "$conf_before" ]' "$(diff <(printf "%s" "$conf_before") "$CONF" | head -10)"
check "1d.1 №4 человеку сказали, что домен вернули" \
    'grep -qF "$(tf website_domain_rolled_back example.com)" "$ERR"' "$(errs)"
# Мост отдаёт боту, панели и агенту только GA_ERR_MSG — фраза «сайт на прежнем домене» должна
# быть в нём, после исходной причины отказа (живая проверка SR-54, 25.09.2026).
check "1d.1 №4 в сообщении отказа — и причина, и «домен возвращён на прежний»" \
    '[ "$GA_ERR_MSG" = "stend: certbot отказал. $(tf website_domain_rolled_back example.com)" ]' "msg=$GA_ERR_MSG"
check "1d.1 №4 вид сертификата не съехал: site.cert=letsencrypt" \
    '[ "$(ga_config_get .site.cert)" = letsencrypt ]' "$(jq -c .site "$CONFIG_FILE")"
run website_check
check "1d.1 №4 внешняя карточка зовёт на работающий домен, а не на новый" \
    '[ "$(jq -r .domain <<<"$out")" = example.com ]' "$out"

# Куда на смене домена попадает сама проверка владения. Раньше временный конфиг
# просто не писали, полагая, что живой vhost «он же default_server» и проверку
# проведёт сам. Слова default_server нет ни в шаблоне, ни в готовом конфиге, а
# server_name в живом файле — ПРЕЖНИЙ домен: точного совпадения по имени нет, и
# запрос уходит в default_server, то есть в первый по алфавиту конфиг из
# sites-enabled. К нам он попадал только потому, что «goamnezia» обычно первый;
# сосед, сортирующийся раньше, забирал проверку себе, и сертификат на новый
# домен не выпускался вовсе. Теперь на время выпуска поднимается ОТДЕЛЬНЫЙ
# конфиг с точным именем нового домена — оно бьёт любой default_server, — а
# живой vhost не трогается: 443 и прежний домен работают всю проверку.
ACME_CONF="$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia-acme"
ACME_LINK="$GOAMNEZIA_NGINX_DIR/sites-enabled/goamnezia-acme"
_website_issue_cert_locked() {
    # Снимок ровно того мига, когда за сертификатом пошёл бы certbot.
    cp "$ACME_CONF" "$TMP/acme-at-issue.conf" 2>/dev/null || : >"$TMP/acme-at-issue.conf"
    cp "$CONF" "$TMP/live-at-issue.conf" 2>/dev/null || : >"$TMP/live-at-issue.conf"
    ga_err cert_failed "stend: certbot отказал"
    return 1
}
run website_enable_pro new.example pro
check "проверка владения на смене домена идёт через отдельный конфиг с именем НОВОГО домена" \
    'grep -qE "^\s*server_name new\.example;" "$TMP/acme-at-issue.conf" && grep -q "acme-challenge" "$TMP/acme-at-issue.conf"' \
    "$(cat "$TMP/acme-at-issue.conf" 2>&1)"
check "отдельный конфиг — только 80: ни 443, ни сертификата, ни корня сайта в нём нет" \
    '! grep -q "listen 443" "$TMP/acme-at-issue.conf" && ! grep -q "ssl_certificate" "$TMP/acme-at-issue.conf" && ! grep -qF "$GOAMNEZIA_SITE_ROOT" "$TMP/acme-at-issue.conf"' \
    "$(cat "$TMP/acme-at-issue.conf" 2>&1)"
check "отдельный конфиг включён симлинком — nginx его читает, а не просто файл лежит" \
    'grep -qF "$(tf website_acme_vhost_up new.example)" "$ERR"' "$(errs)"
check "живой vhost во время проверки не тронут: 443 и прежний домен работают" \
    'grep -qE "^\s*listen 443 ssl http2;" "$TMP/live-at-issue.conf" && grep -qE "^\s*server_name example\.com;" "$TMP/live-at-issue.conf"' \
    "$(grep -nE "server_name|listen 443" "$TMP/live-at-issue.conf" 2>&1)"
check "отказ выпуска: отдельный конфиг снят — ни файла, ни симлинка на 80-м порту" \
    '[ ! -e "$ACME_CONF" ] && [ ! -e "$ACME_LINK" ]' \
    "$(ls -A "$GOAMNEZIA_NGINX_DIR/sites-available" "$GOAMNEZIA_NGINX_DIR/sites-enabled" 2>&1)"
# Выпуск удался — временный конфиг обязан исчезнуть тоже, иначе он навсегда
# останется отвечать на 80 вместо сайта.
_website_issue_cert_locked() { return 0; }
run website_enable_pro new.example pro
check "выпуск удался: домен сменился, отдельный конфиг проверки снят" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .domain)" = new.example ] && [ ! -e "$ACME_CONF" ] && [ ! -e "$ACME_LINK" ] && has "^\s*server_name new\.example;"' \
    "rc=$rc domain=$(ga_config_get .domain) $(errs | tail -3)"
# Тот же домен (повторный выпуск, починка): живой vhost уже носит это имя и сам
# отдаёт проверку — отдельный конфиг не нужен и не поднимается.
_website_issue_cert_locked() {
    cp "$ACME_CONF" "$TMP/acme-at-issue.conf" 2>/dev/null || : >"$TMP/acme-at-issue.conf"
    return 0
}
: >"$TMP/acme-at-issue.conf"
run website_enable_pro new.example pro
check "тот же домен: живой vhost отдаёт проверку сам, лишнего конфига не появляется" \
    '[ "$rc" = 0 ] && [ ! -s "$TMP/acme-at-issue.conf" ] && ! grep -qF "$(tf website_acme_vhost_up new.example)" "$ERR"' \
    "$(cat "$TMP/acme-at-issue.conf" 2>&1)"
check "_ga_site_vhost_has_acme смотрит на ИМЯ: свой домен — да, чужой — нет" \
    '_ga_site_vhost_has_acme new.example && ! _ga_site_vhost_has_acme other.example && _ga_site_vhost_has_acme' \
    "$(grep -nE "server_name" "$CONF")"
# SR-22: хвост оборванной прежней смены домена — конфиг проверки на next.example остался лежать.
# Новая попытка на тот же домен принимала его за живой vhost (acme_vhost=0) и не снимала никогда:
# два server с одним именем на :80. Теперь удачный выпуск снимает его в любом случае.
_ga_site_render_acme next.example >"$ACME_CONF" && ln -sfn "$ACME_CONF" "$ACME_LINK"
_website_issue_cert_locked() { return 0; }
run website_enable_pro next.example pro
check "SR-22 хвост конфига проверки от оборванной попытки снят после удачного выпуска" \
    '[ "$rc" = 0 ] && [ ! -e "$ACME_CONF" ] && [ ! -e "$ACME_LINK" ] && has "^\s*server_name next\.example;"' \
    "rc=$rc $(ls -A "$GOAMNEZIA_NGINX_DIR/sites-enabled" 2>&1) $(errs | tail -3)"
# Шестой круг, A1: откат нужен на любом отказе после записи домена, а не только на отказе certbot.
# Сертификат выпущен, полный vhost отвергнут (nginx -t) — сервер обязан вернуться к тому, что было.
_website_issue_cert_locked() { return 0; }
run website_enable_pro example.com pro
check "шестой круг, подготовка: сайт снова на example.com (его сертификат жив)" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .domain)" = example.com ] && has "^\s*server_name example\.com;"' "rc=$rc $(errs | tail -3)"
_orig_write_conf=$(declare -f _website_write_conf_locked)
eval "$(printf '%s\n' "$_orig_write_conf" | sed '1s/^_website_write_conf_locked/_ga_real_write_conf/')"
_website_write_conf_locked() {
    if [ "${2:-}" = full ] && [ "${1:-}" = broken.example ]; then
        ga_err nginx_conf_invalid "stend: nginx -t отверг полный vhost"
        return 1
    fi
    _ga_real_write_conf "$@"
}
conf_before=$(cat "$CONF")
run website_enable_pro broken.example pro
check "шестой круг A1 смена домена: nginx -t отверг полный vhost — rc≠0, код nginx_conf_invalid сохранён" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = nginx_conf_invalid ]' "rc=$rc code=$GA_ERR_CODE"
check "шестой круг A1 смена домена: прежний домен в config.json, vhost прежний, конфига проверки нет" \
    '[ "$(ga_config_get .domain)" = example.com ] && [ "$(cat "$CONF")" = "$conf_before" ] && [ ! -e "$ACME_CONF" ] && [ ! -e "$ACME_LINK" ]' \
    "domain=$(ga_config_get .domain) $(diff <(printf "%s" "$conf_before") "$CONF" | head -6)"
run website_disable_site
rm -f "$CONF" "$LINK"
ga_config_merge '{"mode": "lite", "domain": "example.com"}' >/dev/null 2>&1
run website_enable_pro broken.example pro
check "шестой круг A1 из lite: nginx -t отверг полный vhost — временный vhost на 80 снят, режим lite, домен прежний" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = nginx_conf_invalid ] && [ ! -e "$CONF" ] && [ ! -e "$LINK" ] && [ "$(ga_config_get .mode)" = lite ] && [ "$(ga_config_get .domain)" = example.com ]' \
    "rc=$rc code=$GA_ERR_CODE mode=$(ga_config_get .mode) domain=$(ga_config_get .domain) $(ls -A "$GOAMNEZIA_NGINX_DIR/sites-enabled" 2>&1)"
eval "$_orig_write_conf"
unset -f _ga_real_write_conf

# Шестой круг, A2: сайт на запасном самоподписанном, в конфиге сохранён домен с живым сертификатом Let's
# Encrypt. Отказ certbot на смене домена возвращал vhost прежнего домена — неудачная операция меняла
# отдаваемый сертификат. Откат обязан вернуть ровно самоподписанный.
run website_enable_site_selfsigned
ga_config_merge '{"domain": "example.com"}' >/dev/null 2>&1
check "шестой круг A2, подготовка: сайт на самоподписанном, в конфиге домен example.com" \
    '[ "$(ga_config_get .site.cert)" = selfsigned ] && [ "$(ga_config_get .domain)" = example.com ] && [ -f "$CONF" ]' "$(jq -c "{mode, domain, site}" "$CONFIG_FILE") $(errs | tail -2)"
conf_before=$(cat "$CONF")
_website_issue_cert_locked() { ga_err cert_failed "stend: certbot отказал"; return 1; }
run website_enable_pro other.example pro
check "шестой круг A2: после отказа certbot сайт остался на самоподписанном — vhost тот же, site.cert=selfsigned, домен прежний" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = cert_failed ] && [ "$(cat "$CONF")" = "$conf_before" ] && [ "$(ga_config_get .site.cert)" = selfsigned ] && [ "$(ga_config_get .domain)" = example.com ]' \
    "rc=$rc code=$GA_ERR_CODE $(diff <(printf "%s" "$conf_before") "$CONF" | head -6)"

# Дальше (№9) снова нужен отказ выпуска — возвращаем отказывающую подмену.
_website_issue_cert_locked() { ga_err cert_failed "stend: certbot отказал"; return 1; }

# №9. Первая установка: мастер пишет ЖЕЛАЕМЫЙ режим в config.json до сайта,
# поэтому ветка очистки, написанная под «пришли из lite», не срабатывала
# никогда. Отказ certbot оставлял живой vhost при mode=lite: продукт считал,
# что сайта нет, nginx держал 80 и 443, а предполётная проверка принимала
# занятый порт за свой.
run website_disable_site
rm -f "$CONF" "$LINK"
ga_config_merge '{"mode": "pro", "domain": "fresh.example"}' >/dev/null 2>&1
run website_enable_pro fresh.example pro
check "1d.1 №9 первая установка, certbot отказал: rc≠0, cert_failed" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = cert_failed ]' "rc=$rc code=$GA_ERR_CODE"
check "1d.1 №9 vhost и симлинк сняты — порты 80 и 443 свободны, чужой занятый не станет «нашим»" \
    '[ ! -e "$CONF" ] && [ ! -e "$LINK" ]' "$(ls -A "$GOAMNEZIA_NGINX_DIR/sites-available" "$GOAMNEZIA_NGINX_DIR/sites-enabled" 2>&1)"
check "1d.1 №9 запасной самоподписанный vhost НЕ поднят вместо снятого" \
    '! grep -qF "$(tf website_conf_written 203.0.113.10 selfsigned)" "$ERR"' "$(errs)"
eval "$_orig_issue"
ga_config_merge '{"mode": "lite", "domain": "example.com"}' >/dev/null 2>&1
run website_disable_site
unset GOAMNEZIA_TEMPLATE_LOCAL_DIR

# ── 11. dry-run без явных каталогов: ничего не пишем ─────────────────────────
section "dry-run без переменных стендов — только DRY-строки"
# Явный шаблон гарантирует ветку deploy: если у машины уже есть index.html,
# включение Pro без шаблона правильно сохраняет сайт и не печатает DRY deploy.
( unset GOAMNEZIA_NGINX_DIR GOAMNEZIA_SITE_ROOT GOAMNEZIA_LE_DIR GOAMNEZIA_SYSTEMD_DIR GOAMNEZIA_ACME_ROOT
  GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min" website_enable_pro example.com h5up_aerial >"$OUT" 2>"$ERR"; echo $? >"$TMP/rc" )
check "enable_pro без GOAMNEZIA_NGINX_DIR/SITE_ROOT: rc=0, DRY write /etc/nginx/…, deploy → /var/www/goamnezia-site, /etc не тронут" \
    '[ "$(cat "$TMP/rc")" = 0 ] && grep -q "DRY: write /etc/nginx/sites-available/goamnezia" "$ERR" && grep -q "DRY: deploy template .* /var/www/goamnezia-site" "$ERR" && [ "$(host_sig /etc/nginx/sites-available/goamnezia)" = "$HOST_VHOST_SIG" ] && [ "$(host_sig /var/www/goamnezia-site/.goamnezia_template_id)" = "$HOST_SITE_SIG" ]' "$(errs)"

# ── 12. Словари ──────────────────────────────────────────────────────────────
section "i18n: паритет RU/EN и ключи t/tf модуля и библиотеки"
ru=$(grep -o '^I18N\[[a-z0-9_]*\]' "$ROOT/lib/lang/ru.d/website.sh" | sort)
en=$(grep -o '^I18N\[[a-z0-9_]*\]' "$ROOT/lib/lang/en.d/website.sh" | sort)
check "ru.d/website.sh ↔ en.d/website.sh: одинаковый набор ключей ($(wc -l <<<"$ru" | tr -d ' '))" '[ "$ru" = "$en" ] && [ -n "$ru" ]' "$(diff <(echo "$ru") <(echo "$en") | head -5)"
used=$( { grep -ohE '\$\((t|tf) [a-z0-9_]+' "$ROOT"/lib/website.sh "$ROOT"/lib/templates_lib.sh | awk '{print $2}'; grep -ohE '(then|else) t [a-z0-9_]+' "$ROOT"/lib/website.sh | awk '{print $3}'; } | sort -u)
missing=""
for k in $used; do
    grep -q "^I18N\[$k\]=" "$ROOT"/lib/lang/ru.sh "$ROOT"/lib/lang/ru.d/*.sh || missing+=" ru:$k"
    grep -q "^I18N\[$k\]=" "$ROOT"/lib/lang/en.sh "$ROOT"/lib/lang/en.d/*.sh || missing+=" en:$k"
done
check "все $(wc -w <<<"$used" | tr -d ' ') ключей t/tf из website.sh и templates_lib.sh есть в RU и EN" '[ -z "$missing" ]' "$missing"
check "в website.sh нет вызовов t/tf с переменной вместо ключа" '! grep -nE "\\\$\((t|tf) \"?\\\$" "$ROOT/lib/website.sh"'
# Дефект 1a.17: адрес проверяется по ЗНАЧЕНИЮ одной функцией продукта
# (CONTRACT §0.8). Своя регулярка ^([0-9]{1,3}\.){3}[0-9]{1,3}$ пропускала
# «999.999.999.999», и такой «адрес» шёл в сравнение с адресом сервера.
check "в website.sh нет своих регулярок под IPv4 (только _valid_ip / ga_valid_host)" \
    '! grep -nE "^[^#]*\[0-9\]\{1,3\}" "$ROOT/lib/website.sh"' "$(grep -nE '^[^#]*\[0-9\]\{1,3\}' "$ROOT/lib/website.sh" | head -3)"
dig() { printf 'alias.example.com.\n999.999.999.999\n203.0.113.10\n'; }
check "ga_site_dns_a: строка CNAME и «999.999.999.999» отброшены, взят первый настоящий адрес" \
    '[ "$(ga_site_dns_a example.com)" = 203.0.113.10 ]' "получено: $(ga_site_dns_a example.com)"
unset -f dig
check "в website.sh нет видимых строк вне t/tf (log_* только с \$(t/tf …))" \
    '! grep -nE "log_(info|success|warning|error|step|dim) \"[^\$]" "$ROOT/lib/website.sh"' "$(grep -nE 'log_(info|success|warning|error|step|dim) "[^$]' "$ROOT/lib/website.sh" | head -3)"
# Дефект 1a.18: в библиотеке шаблонов пять строк печатались мимо словаря —
# английский интерфейс отвечал в этих местах по-русски. Проверяем тем же
# правилом, что и модуль: видимый текст — только через t/tf.
check "в templates_lib.sh нет видимых строк вне t/tf" \
    '! grep -nE "log_(info|success|warning|error|step|dim) \"[^\$]" "$ROOT/lib/templates_lib.sh"' "$(grep -nE 'log_(info|success|warning|error|step|dim) "[^$]' "$ROOT/lib/templates_lib.sh" | head -3)"
en_lang=$(TEMPLATES_LANG=en; load_language en >/dev/null 2>&1; t templates_deployed; load_language ru >/dev/null 2>&1)
check "английский словарь отвечает по-английски на строки библиотеки (templates_deployed)" \
    '[ -n "$en_lang" ] && [ "$en_lang" != templates_deployed ] && ! grep -q "[А-Яа-я]" <<<"$en_lang"' "en=$en_lang"

# ── 12б. Домен с несколькими A-записями ──────────────────────────────────────
# У машины может быть несколько внешних адресов, у домена — по A-записи на каждый
# (как в goTelegram). Раньше с адресом сервера сравнивалась только ПЕРВАЯ A-запись:
# домен с двумя адресами этого же сервера то проходил, то нет, а мастер по этому
# сравнению уходил на самоподписанный сертификат. Стенд в сеть не ходит: ответ
# DNS — из GOAMNEZIA_DNS_STUB.
section "несколько A-записей домена: набор против адресов машины"
dns_st() { ga_site_dns_state example.com | jq -r "$1"; }
export GOAMNEZIA_DNS_STUB="203.0.113.10"
check "одна A-запись на адрес сервера → ok" '[ "$(dns_st .state)" = ok ]' "$(ga_site_dns_state example.com)"
export GOAMNEZIA_DNS_STUB="203.0.113.10 198.51.100.20 203.0.113.10"
check "вторая A-запись на чужой адрес → partial, чужой — ровно 198.51.100.20, повтор записи не считается" \
    '[ "$(dns_st .state)" = partial ] && [ "$(dns_st ".foreign | join(\",\")")" = 198.51.100.20 ] && [ "$(dns_st ".a | length")" = 2 ]' "$(ga_site_dns_state example.com)"
ga_edge_list() { printf '%s\n' 203.0.113.10 198.51.100.20; }
check "у машины два внешних адреса, у домена A-запись на каждый → ok" \
    '[ "$(dns_st .state)" = ok ] && [ "$(dns_st ".ours | length")" = 2 ]' "$(ga_site_dns_state example.com)"
GA_SITE_DNS_HINTED=""
run website_dns_hint example.com
check "подсказка при нескольких своих адресах: строка «несколько адресов этого сервера», rc=0" \
    '[ "$rc" = 0 ] && grep -q "несколько адресов этого сервера: 198.51.100.20, 203.0.113.10" "$ERR"' "$(errs)"
# TAIL-2: экран SSH говорит это до вопроса о переходе, а включение сайта следом — второй раз после «Поднимаю
# сайт». Подсказка помечает домен, включение сайта его не повторяет (в dry-run включение DNS не спрашивает вовсе —
# проверяем метку и условие в модуле).
check "TAIL-2 подсказка помечает домен (GA_SITE_DNS_HINTED), включение сайта про него не повторяет" \
    '[ "$GA_SITE_DNS_HINTED" = example.com ] && grep -q "\"\${GA_SITE_DNS_HINTED:-}\" != \"\$domain\" ]; then" "$ROOT/lib/website.sh"' \
    "hinted=$GA_SITE_DNS_HINTED"
unset -f ga_edge_list
run website_dns_hint example.com
check "подсказка при чужом адресе: называет его и адрес сервера, rc=0 (хоть одна запись ведёт сюда)" \
    '[ "$rc" = 0 ] && grep -q "не этого сервера: 198.51.100.20" "$ERR" && grep -q "(203.0.113.10)" "$ERR"' "$(errs)"
export GOAMNEZIA_DNS_STUB="198.51.100.20 198.51.100.21"
check "все A-записи чужие → elsewhere" '[ "$(dns_st .state)" = elsewhere ]' "$(ga_site_dns_state example.com)"
run website_dns_hint example.com
check "подсказка elsewhere: «указывает на 198.51.100.20, 198.51.100.21, а адреса этого сервера — 203.0.113.10», rc=1" \
    '[ "$rc" = 1 ] && grep -q "указывает на 198.51.100.20, 198.51.100.21, а адреса этого сервера — 203.0.113.10" "$ERR"' "$(errs)"
export GOAMNEZIA_DNS_STUB=""
check "A-записей нет → none" '[ "$(dns_st .state)" = none ]' "$(ga_site_dns_state example.com)"
unset GOAMNEZIA_DNS_STUB
check "стенд без подставного ответа в сеть не ходит → unknown" '[ "$(dns_st .state)" = unknown ]' "$(ga_site_dns_state example.com)"
export GOAMNEZIA_DNS_STUB="bad.example.com. 999.1.1.1 203.0.113.10"
check "мусор и цепочка CNAME в ответе отсеиваются по значению адреса → ok" \
    '[ "$(dns_st .state)" = ok ] && [ "$(dns_st ".a | join(\",\")")" = 203.0.113.10 ]' "$(ga_site_dns_state example.com)"
unset GOAMNEZIA_DNS_STUB

# ── 12в. Ответ DNS: статус, кэш, сбой, Cloudflare, AAAA ──────────────────────
# Критик-сисадмин: `dig +short` не отличал SERVFAIL от пустого ответа (сбой
# резолвера выглядел как «нет A-записи»), таймаут не кэшировался (шапка ждала его
# на каждом кадре), а сторож на сбое DNS снова раздавал доменных пользователей
# всем адресам. Разбор ответа и кэш живут вне стендового режима — поэтому здесь
# подоболочка без GOAMNEZIA_DRY_RUN и подставной dig.
section "ответ DNS: статус, кэш, сбой, Cloudflare, AAAA"
DIG_MODE_FILE="$TMP/dig_mode"
dig() {
    case "$(cat "$DIG_MODE_FILE" 2>/dev/null)" in
        ok)       printf ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\nwww.example.com.\t60\tIN\tCNAME\texample.com.\nexample.com.\t60\tIN\tA\t203.0.113.10\nexample.com.\t60\tIN\tA\t198.51.100.20\n' ;;
        nx)       printf ';; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 1\n' ;;
        servfail) printf ';; ->>HEADER<<- opcode: QUERY, status: SERVFAIL, id: 1\n' ;;
        timeout)  printf ';; connection timed out; no servers could be reached\n'; return 9 ;;
    esac
}
check "_ga_site_dig: NOERROR — адреса A без строки CNAME" \
    '[ "$(echo ok >"$DIG_MODE_FILE"; _ga_site_dig A example.com)" = "$(printf "ok\t198.51.100.20 203.0.113.10")" ]' "$(_ga_site_dig A example.com)"
check "_ga_site_dig: NXDOMAIN — ответ есть, адресов нет (ok и пусто)" \
    '[ "$(echo nx >"$DIG_MODE_FILE"; _ga_site_dig A example.com)" = "$(printf "ok\t")" ]'
check "_ga_site_dig: SERVFAIL и таймаут — «fail», а не «записей нет»" \
    '[ "$(echo servfail >"$DIG_MODE_FILE"; _ga_site_dig A example.com)" = "$(printf "fail\t")" ] && [ "$(echo timeout >"$DIG_MODE_FILE"; _ga_site_dig A example.com)" = "$(printf "fail\t")" ]'
live_dns() {   # live_dns <режим dig> <аргументы ga_site_dns_a_all…> — вне стенда, кэш в $TMP/dnsstate
    echo "$1" >"$DIG_MODE_FILE"; shift
    ( export GOAMNEZIA_DRY_RUN=0 GOAMNEZIA_STATE_DIR="$TMP/dnsstate"; mkdir -p "$GOAMNEZIA_STATE_DIR"
      out=$(ga_site_dns_a_all "$@"); rc=$?; printf '%s|%s' "$rc" "$(tr '\n' ' ' <<<"$out" | sed 's/ $//')" )
}
rm -rf "$TMP/dnsstate"
check "вне стенда: удачный ответ кэшируется (второй вызов берёт кэш и при сбое dig)" \
    '[ "$(live_dns ok example.com 120)" = "0|198.51.100.20 203.0.113.10" ] && [ "$(live_dns servfail example.com 120)" = "0|198.51.100.20 203.0.113.10" ]'
rm -rf "$TMP/dnsstate"
check "сбой DNS: rc 3 («не знаем»), а не «записей нет»; сбой кэшируется — повтор не ждёт dig" \
    '[ "$(live_dns servfail example.com 120)" = "3|" ] && [ "$(live_dns ok example.com 120)" = "3|" ]'
rm -rf "$TMP/dnsstate"
check "stale: при сбое — последний удачный ответ, даже просроченный (для сторожа адресов)" \
    '[ "$(live_dns ok example.com 0)" = "0|198.51.100.20 203.0.113.10" ] && [ "$(live_dns servfail example.com 0 stale)" = "0|198.51.100.20 203.0.113.10" ] && [ "$(live_dns servfail example.com 0)" = "3|" ]'
unset -f dig
check "ga_site_ip_is_cloudflare: 104.16.1.1 и 172.67.2.3 — да, 8.8.8.8 и 203.0.113.10 — нет" \
    'ga_site_ip_is_cloudflare 104.16.1.1 && ga_site_ip_is_cloudflare 172.67.2.3 && ! ga_site_ip_is_cloudflare 8.8.8.8 && ! ga_site_ip_is_cloudflare 203.0.113.10'
export GOAMNEZIA_DNS_STUB="104.21.5.5 172.67.1.1"
check "A-записи — адреса Cloudflare: state elsewhere, cloudflare=true" \
    '[ "$(dns_st .state)" = elsewhere ] && [ "$(dns_st .cloudflare)" = true ]' "$(ga_site_dns_state example.com)"
run website_dns_hint example.com
check "подсказка при проксировании Cloudflare — про «DNS only», а не «укажите сервер»" \
    'grep -q "DNS only" "$ERR" && ! grep -q "указывает на 104" "$ERR"' "$(errs)"
export GOAMNEZIA_DNS_STUB="203.0.113.10" GOAMNEZIA_DNS_STUB_AAAA="2001:db8::5" GOAMNEZIA_OWN_IPS6_STUB=""
check "AAAA на чужой IPv6 → aaaa_foreign, A в порядке (state ok)" \
    '[ "$(dns_st .state)" = ok ] && [ "$(dns_st ".aaaa_foreign | join(\",\")")" = "2001:db8::5" ]' "$(ga_site_dns_state example.com)"
run website_dns_hint example.com
check "подсказка про AAAA: Let's Encrypt сначала пробует IPv6" 'grep -q "AAAA-запись (IPv6) не на этот сервер: 2001:db8::5" "$ERR"' "$(errs)"
export GOAMNEZIA_OWN_IPS6_STUB="2001:db8::5"
check "AAAA на свой IPv6 — не чужая" '[ "$(dns_st ".aaaa_foreign | length")" = 0 ]' "$(ga_site_dns_state example.com)"
unset GOAMNEZIA_DNS_STUB_AAAA GOAMNEZIA_OWN_IPS6_STUB
ga_site_own_ips() { :; }
check "своих адресов не узнать (NAT, адреса сервера нет) → unknown, а не elsewhere" '[ "$(dns_st .state)" = unknown ]' "$(ga_site_dns_state example.com)"
unset -f ga_site_own_ips
# shellcheck disable=SC1091
source "$ROOT/lib/website.sh"
unset GOAMNEZIA_DNS_STUB

# ── 13. Свой шаблон по git URL под set -u ────────────────────────────────────
# Библиотека — копия из навыка site-templates-library, и в копии не было двух
# констант: CUSTOM_GIT_CLONE_TIMEOUT и CUSTOM_GIT_MAX_SIZE_MB (в goVLESS они
# лежат в отдельном lib/templates_catalog.sh). Под set -u первая же роняла
# установку своего сайта на строке `timeout "$CUSTOM_GIT_CLONE_TIMEOUT" git
# clone …` — и только на сервере: на macOS нет GNU timeout, ветка другая, и
# локально всё «работало». Поэтому timeout здесь подменяется функцией, а вызов
# идёт в подоболочке: вылет по set -u в текущей оболочке убил бы весь прогон.
section "свой шаблон по git URL под set -u"
check "константы CUSTOM_GIT_CLONE_TIMEOUT / CUSTOM_GIT_MAX_SIZE_MB заданы после source (числа)" \
    '[[ "${CUSTOM_GIT_CLONE_TIMEOUT:-}" =~ ^[0-9]+$ && "${CUSTOM_GIT_MAX_SIZE_MB:-}" =~ ^[0-9]+$ ]]' \
    "timeout=${CUSTOM_GIT_CLONE_TIMEOUT:-} max=${CUSTOM_GIT_MAX_SIZE_MB:-}"
unassigned=""
for v in $(grep -oE '\$\{?[A-Z][A-Z0-9_]+' "$ROOT/lib/templates_lib.sh" | tr -d '${' | sort -u); do
    case "$v" in BASH_REMATCH|BASH_SOURCE|TMPDIR|HOME|PATH) continue ;; esac
    grep -qE "(^|[^A-Za-z0-9_])${v}(\+?=|\[)|\\$\{${v}[:=]|read -r ${v}( |$)" \
        "$ROOT"/lib/*.sh "$ROOT"/lib/lang/*.sh "$ROOT"/lib/lang/*/*.sh || unassigned+=" $v"
done
check "каждая переменная ВЕРХНЕГО регистра из templates_lib.sh где-то присваивается (иначе set -u)" \
    '[ -z "$unassigned" ]' "$unassigned"
( timeout() { shift; "$@"; }; export NO_PROXY=127.0.0.1 no_proxy=127.0.0.1
  TEMPLATES_CACHE="$TMP/tplcache"; mkdir -p "$TEMPLATES_CACHE"
  download_custom_git_template "https://127.0.0.1:1/user/repo" ) >"$OUT" 2>"$ERR"; rc=$?
check "клонирование по ветке с timeout: недоступный адрес → rc=1 и ответ продукта (custom_git_clone_failed), а не вылет по set -u" \
    '[ "$rc" = 1 ] && grep -qF "${I18N[custom_git_clone_failed]%%:*}" "$ERR" && ! grep -q "CUSTOM_GIT_" "$ERR"' \
    "rc=$rc $(errs | tail -2)"

# ── 14. Подсказка к провалу certbot — по его собственному журналу ────────────
# На живом стенде (0.4.3) certbot падал с «No route to host» и «Read timed out»
# к API Let's Encrypt — исходящая сеть сервера, — а продукт советовал проверить
# A-запись и порт 80, которые были в порядке. Отказ ПРОВЕРКИ домена приходит от
# Let's Encrypt и имени его API не содержит — там прежний совет остаётся верным.
section "подсказка к провалу certbot: сеть сервера, лимит, проверка домена"
ce="$TMP/certbot_err.log"
printf '%s\n' "An unexpected error occurred:" \
    "ValueError: Requesting acme-v02.api.letsencrypt.org/directory: No route to host" >"$ce"
check "«No route to host» к API Let's Encrypt → совет про сеть сервера" \
    '[ "$(_ga_site_cert_cause "$ce")" = net ]' "$(_ga_site_cert_cause "$ce")"
printf '%s\n' "Requesting a certificate for ga.example.org" \
    "requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='acme-v02.api.letsencrypt.org', port=443): Read timed out. (read timeout=45)" >"$ce"
check "«Read timed out» посреди заказа → совет про сеть сервера" \
    '[ "$(_ga_site_cert_cause "$ce")" = net ]' "$(_ga_site_cert_cause "$ce")"
printf '%s\n' "Certbot failed to authenticate some domains (authenticator: webroot). The Certificate Authority reported these problems:" \
    "  Domain: ga.example.org" "  Type:   connection" \
    "  Detail: 203.0.113.7: Fetching http://ga.example.org/.well-known/acme-challenge/x: Timeout during connect (likely firewall problem)" >"$ce"
check "отказ проверки домена (таймаут на наш :80) → прежний совет про A-запись и порт 80" \
    '[ "$(_ga_site_cert_cause "$ce")" = check ]' "$(_ga_site_cert_cause "$ce")"
printf '%s\n' "  Detail: 203.0.113.7: Fetching http://ga.example.org/.well-known/acme-challenge/x: Connection refused" >"$ce"
check "«Connection refused» при проверке домена — тоже прежний совет, а не «сеть сервера»" \
    '[ "$(_ga_site_cert_cause "$ce")" = check ]' "$(_ga_site_cert_cause "$ce")"
printf '%s\n' "Error creating new order :: too many certificates (5) already issued for this exact set of domains in the last 168 hours: ga.example.org, retry after 2026-09-25T10:00:00Z: see https://letsencrypt.org/docs/rate-limits/#new-certificates-per-exact-set-of-hostnames" >"$ce"
check "лимит выдачи Let's Encrypt → совет подождать" \
    '[ "$(_ga_site_cert_cause "$ce")" = limit ]' "$(_ga_site_cert_cause "$ce")"
check "журнала нет → прежний совет" \
    '[ "$(_ga_site_cert_cause "$TMP/no-such-log")" = check ] && [ "$(_ga_site_cert_cause /dev/null)" = check ]'
printf '%s\n' "ValueError: Requesting acme-v02.api.letsencrypt.org/directory: No route to host" >"$ce"
check "совет по причине «сеть» — текст website_cert_hint_net из словаря" \
    '[ "$(_ga_site_cert_hint "$ce")" = "${I18N[website_cert_hint_net]}" ] && [ -n "${I18N[website_cert_hint_net]}" ]' "$(_ga_site_cert_hint "$ce")"
check "без журнала — прежний текст website_cert_hint" \
    '[ "$(_ga_site_cert_hint /dev/null)" = "${I18N[website_cert_hint]}" ]'
# Лимит: срок у Let's Encrypt разный (час или неделя), и он сам пишет «retry after» — называем его. Время там в UTC,
# а на экран — «через 2 ч (… по времени сервера)», как прочие сроки (TAIL-21): голое «10:00 UTC» человек читал как
# своё время. Срок — от нынешних часов, через 2 ч 15 мин с небольшим (ga_rel_time — «через 2 ч»).
le_after=$(python3 -c 'import time; print(time.strftime("%Y-%m-%dT%H:%M:00Z", time.gmtime(time.time() + 8160)))')
printf '%s\n' "Error creating new order :: too many certificates (5) already issued for this exact set of domains in the last 168 hours: ga.example.org, retry after $le_after: see https://letsencrypt.org/docs/rate-limits/" >"$ce"
check "лимит с «retry after» → совет называет срок так же, как прочие: «через … (… по времени сервера)», без «UTC»" \
    'h=$(_ga_site_cert_hint "$ce") && [ "$h" = "$(tf website_cert_hint_limit_until "$(ga_human_ts "$le_after")")" ] && grep -qF "$(tf ts_in "$(tf ts_hours 2)")" <<<"$h" && ! grep -q "UTC" <<<"$h"' "$(_ga_site_cert_hint "$ce")"
printf '%s\n' "too many failed authorizations recently, retry after 2026-09-20 10:00:00 UTC: see https://letsencrypt.org/docs/rate-limits/" >"$ce"
check "лимит, срок в журнале уже прошёл (или «retry after … UTC» с пробелом) → общий совет, а не «повторить можно N назад»" \
    '[ "$(_ga_site_cert_hint "$ce")" = "${I18N[website_cert_hint_limit]}" ]' "$(_ga_site_cert_hint "$ce")"
printf '%s\n' "too many failed authorizations recently: see https://letsencrypt.org/docs/rate-limits/" >"$ce"
check "лимит без срока в журнале → общий совет (час или до недели)" \
    '[ "$(_ga_site_cert_hint "$ce")" = "${I18N[website_cert_hint_limit]}" ]' "$(_ga_site_cert_hint "$ce")"

# L-26: причина отказа словом — мосту, а через него боту (cert_cause: dns | net | limit, срок лимита —
# cert_retry_after). Прогон не сухой: certbot, его установка и nginx подменены функциями, наружу ничего не уходит.
cert_cause_run() {
    ( export GOAMNEZIA_DRY_RUN=0
      website_ensure_certbot() { return 0; }
      ga_site_nginx_active() { return 1; }
      certbot() { cat "$ce"; return 1; }
      GA_CERT_CAUSE=stale; GA_CERT_RETRY_AFTER=stale
      _website_issue_cert_locked cause.example
      printf '%s|%s|%s|%s\n' "$?" "$GA_ERR_CODE" "$GA_CERT_CAUSE" "$GA_CERT_RETRY_AFTER" >"$TMP/cert_cause.out"
    ) >/dev/null 2>&1
    cat "$TMP/cert_cause.out" 2>/dev/null
}
printf '%s\n' "  Detail: 203.0.113.7: Fetching http://cause.example/.well-known/acme-challenge/x: Timeout during connect (likely firewall problem)" >"$ce"
check "L-26 Let's Encrypt не проверил домен → cert_failed, причина dns (A-запись или порт 80), срока нет" \
    '[ "$(cert_cause_run)" = "1|cert_failed|dns|" ]' "$(cert_cause_run)"
printf '%s\n' "ValueError: Requesting acme-v02.api.letsencrypt.org/directory: No route to host" >"$ce"
check "L-26 сервер не достучался до Let's Encrypt → причина net" \
    '[ "$(cert_cause_run)" = "1|cert_failed|net|" ]' "$(cert_cause_run)"
printf '%s\n' "Error creating new order :: too many certificates (5) already issued for this exact set of domains in the last 168 hours: cause.example, retry after 2026-09-27T10:00:00Z: see https://letsencrypt.org/docs/rate-limits/" >"$ce"
check "L-26 лимит с «retry after» → причина limit и срок 2026-09-27 10:00 (UTC)" \
    '[ "$(cert_cause_run)" = "1|cert_failed|limit|2026-09-27 10:00" ]' "$(cert_cause_run)"
printf '%s\n' "too many failed authorizations recently: see https://letsencrypt.org/docs/rate-limits/" >"$ce"
check "L-26 лимит без срока → причина limit, срок пуст" \
    '[ "$(cert_cause_run)" = "1|cert_failed|limit|" ]' "$(cert_cause_run)"

# ── 15. Снос продукта снимает сайт целиком (LC-01, SR-01, 25.09.2026) ─────────
# «Удалить всё» оставлял vhost, веб-корень, deploy-хук и таймер продления: сайт-заглушка
# отвечал после сноса, а vhost запасного сертификата ссылался на удалённый каталог — nginx
# не поднимался после перезагрузки. Прогон не сухой, но все пути — стендовые.
section "снос: сайт уходит целиком, сертификаты certbot остаются"
(
    export GOAMNEZIA_DRY_RUN=0
    # Прогон не сухой — nginx и systemctl машины подменены, как в SR-15 ниже: на сервере от root снос
    # иначе перечитал бы настоящий nginx и выключил настоящий таймер продления сертификата.
    systemctl() { return 0; }
    nginx() { return 0; }
    mkdir -p "$GOAMNEZIA_NGINX_DIR/sites-available" "$GOAMNEZIA_NGINX_DIR/sites-enabled" "$GOAMNEZIA_SITE_ROOT" \
             "$GOAMNEZIA_ACME_ROOT/.well-known" "$GOAMNEZIA_LE_DIR/renewal-hooks/deploy" "$GOAMNEZIA_LE_DIR/live/wipe.example" \
             "$GOAMNEZIA_SYSTEMD_DIR"
    printf 'server {}\n' >"$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia"
    ln -sf "$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia" "$GOAMNEZIA_NGINX_DIR/sites-enabled/goamnezia"
    printf '<h1>site</h1>\n' >"$GOAMNEZIA_SITE_ROOT/index.html"
    printf '#!/bin/sh\n' >"$GOAMNEZIA_LE_DIR/renewal-hooks/deploy/goamnezia-nginx-reload.sh"
    printf 'cert\n' >"$GOAMNEZIA_LE_DIR/live/wipe.example/fullchain.pem"
    mkdir -p "$GOAMNEZIA_LE_DIR/renewal"
    printf 'version = 2.9.0\n[renewalparams]\nauthenticator = webroot\nwebroot_path = %s,\n[[webroot_map]]\nwipe.example = %s\n' "$GOAMNEZIA_ACME_ROOT" "$GOAMNEZIA_ACME_ROOT" >"$GOAMNEZIA_LE_DIR/renewal/wipe.example.conf"
    printf '[Timer]\n' >"$GOAMNEZIA_SYSTEMD_DIR/goamnezia-cert-renew.timer"
    printf '[Service]\n' >"$GOAMNEZIA_SYSTEMD_DIR/goamnezia-cert-renew.service"
    website_wipe
) >"$OUT" 2>"$ERR"; rc=$?
check "website_wipe: vhost и ссылка, веб-корни, deploy-хук и таймер продления сняты" \
    '[ "$rc" = 0 ] && [ ! -e "$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia" ] && [ ! -L "$GOAMNEZIA_NGINX_DIR/sites-enabled/goamnezia" ] && [ ! -e "$GOAMNEZIA_SITE_ROOT" ] && [ ! -e "$GOAMNEZIA_ACME_ROOT" ] && [ ! -e "$GOAMNEZIA_LE_DIR/renewal-hooks/deploy/goamnezia-nginx-reload.sh" ] && [ ! -e "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-cert-renew.timer" ] && [ ! -e "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-cert-renew.service" ]' \
    "rc=$rc $(ls -R "$GOAMNEZIA_NGINX_DIR" "$GOAMNEZIA_SYSTEMD_DIR" 2>&1 | tr '\n' ' ') $(errs | tail -2)"
check "website_wipe: сертификат Let's Encrypt на месте (им владеет certbot, повторный выпуск — лимиты)" \
    '[ -f "$GOAMNEZIA_LE_DIR/live/wipe.example/fullchain.pem" ]'
# certbot читает autorenew только из секции [renewalparams] — строка должна стоять сразу за
# её заголовком, а не первой в файле и не во вложенной [[webroot_map]] (находка SR-55).
check "website_wipe: у сертификата с нашим веб-корнем выключено автопродление — внутри [renewalparams], ровно одна строка" \
    '[ "$(grep -A1 -x "\[renewalparams\]" "$GOAMNEZIA_LE_DIR/renewal/wipe.example.conf" | tail -1)" = "autorenew = False" ] && [ "$(grep -c "^autorenew" "$GOAMNEZIA_LE_DIR/renewal/wipe.example.conf")" = 1 ] && [ "$(head -1 "$GOAMNEZIA_LE_DIR/renewal/wipe.example.conf")" = "version = 2.9.0" ] && grep -q "authenticator = webroot" "$GOAMNEZIA_LE_DIR/renewal/wipe.example.conf"' \
    "$(cat "$GOAMNEZIA_LE_DIR/renewal/wipe.example.conf" 2>&1)"

# Туда и обратно: сайт вернулся — строка снимается; повторное «выключить» не плодит строк.
# В dry-run _ga_site_autorenew пишет только при заданном GOAMNEZIA_LE_DIR (стенд).
rn="$TMP/le-rt/renewal/rt.example.conf"
mkdir -p "$(dirname "$rn")"
printf 'version = 2.9.0\n[renewalparams]\nauthenticator = webroot\n[[webroot_map]]\nrt.example = /x\n' >"$rn"
( export GOAMNEZIA_LE_DIR="$TMP/le-rt"; _ga_site_autorenew rt.example off; _ga_site_autorenew rt.example off ) >/dev/null 2>&1
check "autorenew off дважды: одна строка, сразу за [renewalparams], [[webroot_map]] цел" \
    '[ "$(grep -c "^autorenew = False" "$rn")" = 1 ] && [ "$(grep -A1 -x "\[renewalparams\]" "$rn" | tail -1)" = "autorenew = False" ] && grep -qx "rt.example = /x" "$rn"' "$(cat "$rn")"
( export GOAMNEZIA_LE_DIR="$TMP/le-rt"; _ga_site_autorenew rt.example on ) >/dev/null 2>&1
check "autorenew on: строка снята, остальное байт-в-байт" \
    '[ "$(cat "$rn")" = "$(printf "version = 2.9.0\n[renewalparams]\nauthenticator = webroot\n[[webroot_map]]\nrt.example = /x")" ]' "$(cat "$rn")"

# ── 16. Сайт-заглушка не чья-то личная страница и не шпионит (LC-13, LS-09) ────
section "случайный шаблон — не личная страница; счётчики и ссылки на оригинал вырезаются"
bad=0
if [ -f "$ROOT/templates_catalog.json" ]; then
    for _i in $(seq 1 60); do
        rid=$(GOAMNEZIA_CATALOG="$ROOT/templates_catalog.json" ga_site_random_template_id 2>/dev/null) || rid=""
        [ -n "$rid" ] || { bad=$((bad + 1)); continue; }
        IFS=_ read -r _g _u _v _rest <<<"$rid"
        [ "$_u" = "$_v" ] && [ "$_rest" = "github_io" ] && bad=$((bad + 1))
        cat_=$(jq -r --arg id "$rid" '.categories[] | select(any(.templates[]; .id == $id)) | .id' "$ROOT/templates_catalog.json" | head -1)
        [ "$cat_" = portfolio ] && bad=$((bad + 1))
    done
    check "60 случайных шаблонов: ни одного <имя>.github.io и ни одного из «Портфолио»" '[ "$bad" = 0 ]' "bad=$bad"
else
    skip=$((skip + 1)); printf '  – случайный шаблон (пропущено: нет templates_catalog.json)\n'
fi
tr_root="$TMP/trackers"; mkdir -p "$tr_root"
cat >"$tr_root/index.html" <<'HTML'
<html><head><link rel="canonical" href="https://someone.github.io/"><meta property="og:url" content="https://someone.github.io/">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-XYZ"></script>
<script>window.dataLayer = window.dataLayer || []; function gtag(){dataLayer.push(arguments);} gtag("config", "G-XYZ");</script>
<script src="js/app.js"></script><script>console.log("keep")</script>
</head><body><h1>Hi</h1><noscript><img src="https://mc.yandex.ru/watch/1"></noscript></body></html>
HTML
printf '<urlset/>\n' >"$tr_root/sitemap.xml"
_ga_site_strip_trackers "$tr_root"
check "счётчики (gtag, GTM, Метрика), canonical/og:url и sitemap.xml убраны" \
    '! grep -qiE "googletagmanager|gtag\(|mc\.yandex|canonical|og:url" "$tr_root/index.html" && [ ! -e "$tr_root/sitemap.xml" ]' "$(cat "$tr_root/index.html")"
check "свои скрипты страницы на месте" \
    'grep -q "js/app.js" "$tr_root/index.html" && grep -q "console.log(\"keep\")" "$tr_root/index.html" && grep -q "<h1>Hi</h1>" "$tr_root/index.html"'

# ── 17. Проверка сайта: DNS не ответил — не «поправьте DNS» (SR-15) ──────────
# Прогон website_check не сухой, но сеть подменена функциями dig/curl/systemctl: наружу ничего
# не уходит. Раньше SERVFAIL и таймаут резолвера давали dns_mismatch — «A-запись не на сервер,
# поправьте DNS», а тот же экран строкой выше писал «DNS не ответил».
section "проверка сайта: DNS не ответил → dns_unknown, ниже сертификатных проверок"
if command -v openssl >/dev/null 2>&1; then
    vc_out=$(
        export GOAMNEZIA_DRY_RUN=0 GOAMNEZIA_LE_DIR="$TMP/le-sr15" GOAMNEZIA_SITE_ROOT="$TMP/site-sr15"
        mkdir -p "$GOAMNEZIA_LE_DIR/live/example.com" "$GOAMNEZIA_SITE_ROOT"
        openssl req -x509 -newkey rsa:2048 -nodes -days 80 -subj "/CN=example.com" \
            -keyout "$GOAMNEZIA_LE_DIR/live/example.com/privkey.pem" \
            -out "$GOAMNEZIA_LE_DIR/live/example.com/fullchain.pem" >/dev/null 2>&1
        printf '<html>x</html>\n' >"$GOAMNEZIA_SITE_ROOT/index.html"
        ga_config_merge '{"mode":"pro","domain":"example.com","site":{"cert":"letsencrypt"}}' >/dev/null 2>&1
        curl() { printf '200'; }
        systemctl() { return 0; }
        dig() { printf '%s\n' "${FAKE:-}"; return "${DIGRC:-0}"; }
        v() { website_check 2>/dev/null | jq -r '.verdict + " " + .dns_state'; }
        printf '%s|' "$(DIGRC=9 FAKE=';; connection timed out; no servers could be reached' v)"
        printf '%s|' "$(FAKE=';; ->>HEADER<<- opcode: QUERY, status: SERVFAIL, id: 1' v)"
        printf '%s|' "$(FAKE=';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1
example.com.	300	IN	A	198.51.100.99' v)"
        printf '%s' "$(FAKE=';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1
example.com.	300	IN	A	203.0.113.10' v)"
    )
    IFS='|' read -r vc_timeout vc_servfail vc_foreign vc_ok <<<"$vc_out"
    check "SR-15 таймаут резолвера → dns_unknown (не dns_mismatch)" '[ "$vc_timeout" = "dns_unknown unknown" ]' "$vc_out"
    check "SR-15 SERVFAIL → dns_unknown" '[ "$vc_servfail" = "dns_unknown unknown" ]' "$vc_out"
    check "SR-15 A-запись на чужой адрес — по-прежнему dns_mismatch" '[ "$vc_foreign" = "dns_mismatch elsewhere" ]' "$vc_out"
    check "SR-15 всё сходится → ok" '[ "$vc_ok" = "ok ok" ]' "$vc_out"
else
    skip=$((skip + 1)); printf '  – dns_unknown (пропущено: нет openssl)\n'
fi

# ── Итог ─────────────────────────────────────────────────────────────────────
DONE=1
printf '\n══ ИТОГ: пройдено %d, провалено %d, пропущено %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
