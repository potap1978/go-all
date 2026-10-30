#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон lib/agents.sh без сервера (GOAMNEZIA_DRY_RUN=1)
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_agents_dryrun.sh
# Нужны: bash ≥ 5, jq, openssl, python3; curl — для живой проверки отзыва
# (без него этот раздел пропускается, а не проваливается). Ни systemd, ни nginx,
# ни сеть не нужны: systemctl печатается как «DRY: …», юнит пишется в
# GOAMNEZIA_SYSTEMD_DIR, vhost — в GOAMNEZIA_NGINX_DIR, сертификат — настоящий
# (openssl) в $GOAMNEZIA_DIR/tls.
#
# Что проверяем (CONTRACT §5.1, PLAN §6.10):
#   lite без порта: agents_add и agents_http_on → needs_site, agents.json не
#   создаётся → agents_http_on 40000 (свой TLS-порт): сертификат, юнит с
#   --bind 0.0.0.0 --port 40000 --tls, DRY systemctl enable/restart, config
#   mcp.http_enabled/public_port, статус listen=public_port с отпечатком →
#   agents_add: agents.json 0600, секрет и ТОКЕН по 43 символа base64url,
#   require_token=true, id a-<hex>, адрес https://IP:40000/a-<secret>/mcp,
#   срок по умолчанию 30 дней (пустой аргумент), команда подключения с
#   заголовком Authorization, allow_ips →
#   server.py --check читает этот agents.json и считает доступы → живой
#   server.py --http на петле с этим agents.json: верный секрет → tools/list по
#   уровню, БЕЗ заголовка Authorization → 404, с чужим токеном → 404, со своим →
#   200, старый доступ без require_token работает по одному пути, привязка к
#   адресу (первый принят, второй 404, снятие вернуло доступ), уведомления в
#   очереди бота, disable → 404 на следующем запросе, rotate → старый 404,
#   новый 200 (и токен сменился) → agents_harden старому доступу →
#   ошибки валидации (label, scope, days, ips, порт занят/зарезервирован) →
#   agents_list без секрета, state → agents_show → rotate меняет секрет и адрес →
#   disable/enable → истёкший = expired → del → agents_http_off убирает юнит →
#   lite_site: agents_http_on включает location в vhost, юнит на петле без TLS,
#   адрес https://IP/a-…/mcp с отпечатком → pro: адрес https://домен/a-…/mcp без
#   отпечатка, смена режима pro → lite останавливает службу (DRY) →
#   аудит только с id, секретов нет ни в аудите, ни в stderr → паритет словаря.
#
# Настоящие lib/common.sh, lib/i18n.sh, lib/website.sh, lib/preflight.sh,
# lib/agents.sh. Ничего вне $TMP не пишем. Захват результата — через run
# (stdout → файл), а не $(…): в подоболочке терялся бы GA_ERR_CODE.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-agents.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
ALL_ERR="$TMP/all-stderr.log"
SRV_PID=""
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; [ -n "$SRV_PID" ] && kill "$SRV_PID" 2>/dev/null; rm -rf "$TMP"' EXIT

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
export GOAMNEZIA_NOTIFY_DIR="$TMP/notify"
export GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min"
export MCP_DIR="$TMP/goamnezia-mcp"
unset GOAMNEZIA_LE_STAGING GOAMNEZIA_AGENTS_JSON GOAMNEZIA_TLS_DIR
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR"

# host_sig <путь> — след файла машины: «absent» или контрольная сумма. «Живая система не тронута» —
# это след до прогона и после одинаков, а не «файла нет»: на машине с установленным продуктом он есть
# по праву (Linux-сервер), и проверка краснела бы, хотя прогон ничего не писал.
host_sig() { if [ -e "$1" ]; then cksum <"$1" 2>/dev/null || echo unreadable; else echo absent; fi; }
HOST_MCP_UNIT_SIG=$(host_sig /etc/systemd/system/goamnezia-mcp-http.service)
HOST_AGENTS_SIG=$(host_sig /opt/goamnezia/agents.json)

# Временные файлы с ключами — в каталоге стенда (L1-19). Без переменной продукт кладёт их в /dev/shm,
# а он есть только на Linux: там они жили бы на машине прогона, и оборванный прогон оставлял бы ключи.
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"
# Занятость порта: без lib/docker.sh продукт спрашивал настоящий ss -ltn (ga_agents_port_free), и
# слушатель машины на 40000/40100/40500 давал бы port_busy (L1-12). Теперь в сухом прогоне он ss не зовёт
# (TAIL-9); подставной ss остаётся ловушкой: слушателей не знает, а каждый вызов пишет в SS_CALLS — в конце
# набора журнал обязан быть пуст.
SSBIN="$TMP/ssbin"; mkdir -p "$SSBIN"; SS_CALLS="$TMP/ss.calls"; : >"$SS_CALLS"
printf '#!/bin/sh\necho "ss $*" >>"%s"\nexit 0\n' "$SS_CALLS" >"$SSBIN/ss"; chmod +x "$SSBIN/ss"
export PATH="$SSBIN:$PATH"
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
run() {
    GA_ERR_CODE=""; GA_ERR_MSG=""; GA_LAST_ERROR_CODE=""
    "$@" >"$OUT" 2>"$ERR"; rc=$?
    out=$(<"$OUT")
    cat "$ERR" >>"$ALL_ERR"
    return 0
}
errs() { cat "$ERR" 2>/dev/null; }
# jget <jq-выражение> — поле из последнего JSON в out
jget() { jq -r "$1" <<<"$out" 2>/dev/null; }
one_line_json() { [ "$(wc -l <"$OUT" | tr -d ' ')" -le 1 ] && is_json "$out"; }

AGENTS="$GOAMNEZIA_DIR/agents.json"
NOTIFY="$GOAMNEZIA_NOTIFY_DIR"
UNIT="$GOAMNEZIA_SYSTEMD_DIR/goamnezia-mcp-http.service"
CONF="$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia"
SERVER="$ROOT/goamnezia-mcp/server.py"
FAKE_BIN="$ROOT/tests/fixtures/fake_action.sh"
SECRET_RE='^[A-Za-z0-9_-]{43}$'
ID_RE='^a-[a-f0-9]{4,8}$'

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
for f in lib/common.sh lib/i18n.sh lib/website.sh lib/preflight.sh lib/agents.sh lib/lang/ru.d/agents.sh lib/lang/en.d/agents.sh goamnezia-mcp/server.py; do
    [ -f "$ROOT/$f" ] || { printf '!! нет %s\n' "$f"; exit 2; }
done
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
# shellcheck disable=SC1091
source "$ROOT/lib/website.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/preflight.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/agents.sh"
check "модуль подключён: agents_add, agents_http_on, agents_http_status, agents_list" \
    'type agents_add >/dev/null && type agents_http_on >/dev/null && type agents_http_status >/dev/null && type agents_list >/dev/null'
check "словарь agents подхвачен движком i18n (agents_err_needs_site)" '[ -n "${I18N[agents_err_needs_site]:-}" ]'
mkdir -p "$MCP_DIR"; cp "$SERVER" "$MCP_DIR/server.py"
# Версия в фикстуре — ТЕКУЩАЯ из lib/common.sh, а не литерал: со старым номером
# каждый подъём версии включал бы миграцию с предмиграционным бэкапом.
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOC
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}, "panel": {"port": 1985, "miniapp_path": "", "trust_tunnel": true},
 "site": {"template_id": "", "template_source": "", "listen": "public", "mcp_location": false, "panel_path": ""},
 "options": {"drop_icmp": false, "alt_svc": false},
 "mcp": {"http_enabled": false, "http_port": 1986}}
EOC
chmod 600 "$CONFIG_FILE"

# ── 1. Lite без порта: адреса нет ────────────────────────────────────────────
section "lite без порта → needs_site"
run agents_http_status
check "agents_http_status: одна JSON-строка, enabled=false, listen=none, mode=lite, agents=0, hint про needs_site" \
    'one_line_json && [ "$(jget .enabled)" = false ] && [ "$(jget .listen)" = none ] && [ "$(jget .mode)" = lite ] && [ "$(jget .agents)" = 0 ] && [ -n "$(jget .hint)" ]' "$out"
run agents_add "Claude Code" write 0 "" ""
check "agents_add в lite без порта → rc≠0, код needs_site, stdout пуст, agents.json не создан" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = needs_site ] && [ -z "$out" ] && [ ! -e "$AGENTS" ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
run agents_http_on
check "agents_http_on без порта в lite → needs_site, юнита и флага нет" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = needs_site ] && [ ! -e "$UNIT" ] && [ "$(ga_config_get .mcp.http_enabled)" = false ]' "rc=$rc code=$GA_ERR_CODE"
run agents_http_on 80
check "agents_http_on 80 → invalid_port (привилегированные порты не предлагаем), rc=2" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_port ]' "rc=$rc code=$GA_ERR_CODE"
run agents_http_on 1985
check "agents_http_on 1985 → port_busy (порт панели)" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = port_busy ]' "rc=$rc code=$GA_ERR_CODE"
run agents_http_on 123
check "agents_http_on 123 → invalid_port (ниже 1024), rc=2" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_port ]' "rc=$rc code=$GA_ERR_CODE"
# TAIL-9: занятость порта в сухом прогоне без lib/docker.sh — из сохранённого вывода ss, настоящий ss не зовётся.
printf '%s\n' "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process" \
    'LISTEN 0      4096   0.0.0.0:40777      0.0.0.0:*     users:(("nginx",pid=1,fd=6))' >"$TMP/ss-tcp.txt"
check "TAIL-9: сухой прогон, слушатель 40777 в GOAMNEZIA_SS_FIXTURE_TCP → 40777 занят, 40778 свободен; без фикстуры — свободен; ss не зван" \
    '! GOAMNEZIA_SS_FIXTURE_TCP="$TMP/ss-tcp.txt" ga_agents_port_free 40777 2>/dev/null && GOAMNEZIA_SS_FIXTURE_TCP="$TMP/ss-tcp.txt" ga_agents_port_free 40778 2>/dev/null && ga_agents_port_free 40777 2>/dev/null && [ ! -s "$SS_CALLS" ]' \
    "$(cat "$SS_CALLS")"
GOAMNEZIA_SS_FIXTURE_TCP="$TMP/ss-tcp.txt" run agents_http_on 40777
check "…и agents_http_on 40777 при таком слушателе → port_busy" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = port_busy ]' "rc=$rc code=$GA_ERR_CODE"
# Предлагаемый порт — из низкого диапазона, как у профилей: решение владельца
# «ближе к трём-пяти тысячам, не выше десяти тысяч» (DEFECTS §1.5). Прежние
# 30000–49999 у части провайдеров режутся блоком, и доступ не открывался у того
# же человека, который его завёл. Двадцать бросков: одиночный мог бы совпасть
# случайно и на старом диапазоне.
rnd_bad=0; rnd_wk=0
for _ in $(seq 20); do
    p=$(ga_agents_random_port 2>/dev/null) || { rnd_bad=$((rnd_bad + 1)); continue; }
    { [ "$p" -ge "$GA_PORT_LOW_MIN" ] && [ "$p" -le "$GA_PORT_LOW_MAX" ]; } || rnd_bad=$((rnd_bad + 1))
    ga_port_wellknown "$p" && rnd_wk=$((rnd_wk + 1))
done
check "ga_agents_random_port предлагает порт 1024–9999, не выше десяти тысяч (20 бросков)" \
    '[ "$rnd_bad" = 0 ]' "мимо диапазона: $rnd_bad из 20"
check "и не предлагает общеизвестные порты (3128, 8080, 1985 и прочие соседи)" \
    '[ "$rnd_wk" = 0 ]' "общеизвестных: $rnd_wk из 20"

# ── 2. Lite со своим TLS-портом ──────────────────────────────────────────────
section "lite: agents_http_on 40000 — свой TLS-порт на самоподписанном сертификате"
run agents_http_on 40000
check "agents_http_on 40000: rc=0, одна JSON-строка статуса" '[ "$rc" = 0 ] && one_line_json' "rc=$rc $(errs)"
check "статус: enabled=true, listen=public_port, bind=0.0.0.0, port=40000, tls=true, public_port=40000, via=port, reachable=true" \
    '[ "$(jget .enabled)" = true ] && [ "$(jget .listen)" = public_port ] && [ "$(jget .bind)" = 0.0.0.0 ] && [ "$(jget .port)" = 40000 ] && [ "$(jget .tls)" = true ] && [ "$(jget .public_port)" = 40000 ] && [ "$(jget .via)" = port ] && [ "$(jget .reachable)" = true ]' "$out"
check "статус: base_url=https://203.0.113.10:40000, отпечаток SHA-256 совпадает с website_cert_fingerprint" \
    '[ "$(jget .base_url)" = "https://203.0.113.10:40000" ] && [ "$(jget .cert_fingerprint_sha256)" = "$(website_cert_fingerprint)" ]' "$out"
fp=$(website_cert_fingerprint)
check "самоподписанный сертификат создан по-настоящему: tls/site.crt и site.key 0600, каталог 0700" \
    '[ -f "$GOAMNEZIA_DIR/tls/site.crt" ] && [ -f "$GOAMNEZIA_DIR/tls/site.key" ] && [ "$(perm "$GOAMNEZIA_DIR/tls/site.key")" = 600 ] && [ "$(perm "$GOAMNEZIA_DIR/tls")" = 700 ]'
check "юнит записан в GOAMNEZIA_SYSTEMD_DIR: ExecStart python3 server.py --http --bind 0.0.0.0 --port 40000 --tls site.crt,site.key; Restart=always; User=root" \
    '[ -f "$UNIT" ] && grep -qF "ExecStart=/usr/bin/env python3 $MCP_DIR/server.py --http --bind 0.0.0.0 --port 40000 --tls $GOAMNEZIA_DIR/tls/site.crt,$GOAMNEZIA_DIR/tls/site.key" "$UNIT" && grep -q "^Restart=always" "$UNIT" && grep -q "^User=root" "$UNIT" && grep -qF "Environment=GOAMNEZIA_DIR=$GOAMNEZIA_DIR" "$UNIT"' "$(cat "$UNIT" 2>/dev/null)"
check "stderr: DRY systemctl daemon-reload, enable --now и restart goamnezia-mcp-http.service; DRY ufw/iptables на 40000/tcp" \
    'grep -q "DRY: systemctl daemon-reload" "$ERR" && grep -q "DRY: systemctl enable --now goamnezia-mcp-http.service" "$ERR" && grep -q "DRY: systemctl restart goamnezia-mcp-http.service" "$ERR"' "$(grep DRY "$ERR")"
check "config.json: mcp.http_enabled=true (булево), mcp.public_port=40000 (число), http_port=1986" \
    '[ "$(jq -c .mcp.http_enabled "$CONFIG_FILE")" = true ] && [ "$(jq -c .mcp.public_port "$CONFIG_FILE")" = 40000 ] && [ "$(jq -c .mcp.http_port "$CONFIG_FILE")" = 1986 ]' "$(jq -c .mcp "$CONFIG_FILE")"
check "аудит: mcp-http-on с mode=lite и listen" 'grep -q "mcp-http-on" "$GOAMNEZIA_AUDIT_LOG" && grep -q "listen=0.0.0.0:40000:1" "$GOAMNEZIA_AUDIT_LOG"' "$(tail -n 3 "$GOAMNEZIA_AUDIT_LOG")"
run agents_http_on 40000
check "повтор agents_http_on 40000 (ремонт): rc=0, тот же порт, юнит не изменился" \
    '[ "$rc" = 0 ] && [ "$(jget .public_port)" = 40000 ] && grep -qF -- "--port 40000 --tls" "$UNIT"' "$(errs)"

# ── 3. Выдача доступа ────────────────────────────────────────────────────────
section "agents_add"
run agents_add "Claude Code на ноутбуке" write 30 "203.0.113.0/24, 198.51.100.7"
check "agents_add: rc=0, одна JSON-строка карточки" '[ "$rc" = 0 ] && one_line_json' "rc=$rc $(errs)"
id1=$(jget .id); url1=$(jget .url); secret1="${url1#*/a-}"; secret1="${secret1%/mcp}"
check "карточка: id a-<hex>, label, scope=write, enabled=true, calls=0, port=0, allow_ips из списка" \
    '[[ "$id1" =~ $ID_RE ]] && [ "$(jget .label)" = "Claude Code на ноутбуке" ] && [ "$(jget .scope)" = write ] && [ "$(jget .enabled)" = true ] && [ "$(jget .calls)" = 0 ] && [ "$(jget .port)" = 0 ] && [ "$(jget ".allow_ips|join(\",\")")" = "203.0.113.0/24,198.51.100.7" ]' "$out"
check "адрес: https://203.0.113.10:40000/a-<43 символа base64url>/mcp, via=port, reachable=true, transport_enabled=true" \
    '[[ "$url1" =~ ^https://203\.0\.113\.10:40000/a-[A-Za-z0-9_-]{43}/mcp$ ]] && [ "$(jget .via)" = port ] && [ "$(jget .reachable)" = true ] && [ "$(jget .transport_enabled)" = true ]' "$url1"
check "карточка: отпечаток сертификата, cert_hint, hint с командой claude mcp add --transport http, warning «это пароль»" \
    '[ "$(jget .cert_fingerprint_sha256)" = "$fp" ] && [ -n "$(jget .cert_hint)" ] && grep -qF "claude mcp add --transport http goamnezia $url1" <<<"$(jget .hint)" && [ -n "$(jget .warning)" ]' "$out"
# Второй фактор: токен едет заголовком, а не адресом, поэтому в url его нет и быть не должно.
token1=$(jget .token)
check "карточка: require_token=true, токен 43 символа base64url, в url токена НЕТ, weak_hint отсутствует" \
    '[ "$(jget .require_token)" = true ] && [[ "$token1" =~ $SECRET_RE ]] && [ "$token1" != "$secret1" ] && ! grep -qF -e "$token1" <<<"$url1" && [ "$(jget .weak_hint)" = null ]' "$out"
check "карточка: connect — одна готовая команда с заголовком Authorization: Bearer <токен>" \
    'grep -qF "claude mcp add --transport http goamnezia $url1 --header \"Authorization: Bearer $token1\"" <<<"$(jget .connect)"' "$(jget .connect)"
check "карточка: pin_ip=false по умолчанию, pinned_ip пуст" '[ "$(jget .pin_ip)" = false ] && [ "$(jget .pinned_ip)" = "" ]' "$out"
# Умолчание — static: одноразовый ключ требует клиента, который сам его хранит,
# и выдавать его молча значило бы выдавать нерабочий доступ.
check "карточка: mode=static по умолчанию, ключа цепочки нет, compromised=false" \
    '[ "$(jget .mode)" = static ] && [ "$(jget .key)" = null ] && [ "$(jget .compromised)" = false ] && [ "$(jq -r ".agents[0].key_hash" "$AGENTS")" = "" ]' "$out"
check "срок: expires ≈ сегодня + 30 дней (ISO-8601 Z)" \
    '[ "$(jget .expires)" = "$(ga_agents_iso_in_days 30 | cut -c1-13)$(jget .expires | cut -c14-)" ] && [[ "$(jget .expires)" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]' "$(jget .expires)"
check "agents.json: 0600, schema=1, одна запись, секрет и токен по 43 символа base64url, require_token=true, last_used пуст" \
    '[ "$(perm "$AGENTS")" = 600 ] && [ "$(jq -r .schema "$AGENTS")" = 1 ] && [ "$(jq -r ".agents|length" "$AGENTS")" = 1 ] && [[ "$(jq -r ".agents[0].secret" "$AGENTS")" =~ $SECRET_RE ]] && [ "$(jq -r ".agents[0].secret" "$AGENTS")" = "$secret1" ] && [[ "$(jq -r ".agents[0].token" "$AGENTS")" =~ $SECRET_RE ]] && [ "$(jq -r ".agents[0].require_token" "$AGENTS")" = true ] && [ "$(jq -r ".agents[0].last_used" "$AGENTS")" = "" ]' "$(jq -c ".agents[0] | del(.secret, .token)" "$AGENTS")"
check "stderr: ни секрета, ни токена; аудит: agent-add id=… с pin_ip и без секретов" \
    '! grep -qF -e "$secret1" "$ERR" && ! grep -qF -e "$token1" "$ERR" && grep -q "agent-add" "$GOAMNEZIA_AUDIT_LOG" && grep -q "id=$id1 scope=write mode=static days=30" "$GOAMNEZIA_AUDIT_LOG" && grep -q "pin_ip=false" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF -e "$secret1" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF -e "$token1" "$GOAMNEZIA_AUDIT_LOG"' "$(errs)"
# Срок по умолчанию: пустой аргумент days — это «не указали», и тогда 30 дней.
run agents_add "Срок по умолчанию" read
check "agents_add без срока → 30 дней (GA_AGENTS_DEFAULT_DAYS), а не бессрочно" \
    '[ "$rc" = 0 ] && [ "$(jget .expires)" = "$(ga_agents_iso_in_days "$GA_AGENTS_DEFAULT_DAYS" | cut -c1-13)$(jget .expires | cut -c14-)" ] && [ -n "$(jget .expires)" ]' "$(jget .expires)"
id_def=$(jget .id)
run agents_del "$id_def"
# Уровень по умолчанию: не указали — значит read (MCP_ROTATION §5, наименьшие права).
# Прежнее умолчание write отдавало приватные ключи ВСЕХ клиентов доступу, который
# владелец выдал не глядя: украденный доступ успевает один запрос, и этим запросом
# не должен быть чужой рабочий конфиг (DEFECTS §1d.1 №2).
run agents_add "Уровень по умолчанию"
check "agents_add без уровня → read (GA_AGENTS_DEFAULT_SCOPE), а не write" \
    '[ "$rc" = 0 ] && [ "$(jget .scope)" = read ] && [ "$GA_AGENTS_DEFAULT_SCOPE" = read ]' "$(jget .scope)"
check "то же умолчание в lib/actions.json: мост, панель и бот зовут agent-add через него" \
    '[ "$(jq -r ".actions[\"agent-add\"].params.scope.default" "$ROOT/lib/actions.json")" = read ]' \
    "$(jq -r '.actions["agent-add"].params.scope.default' "$ROOT/lib/actions.json")"
# Строка выбора обязана называть СОСТАВ уровня в обоих языках, а не одно свойство:
# write не только выдаёт приватные ключи клиентов, но и одним вызовом без всякого
# подтверждения сносит сайт-маскировку, панель на домене и весь транспорт агентов —
# включая тот, по которому пришёл сам агент. Владелец читает это при выдаче.
check "SSH-словарь: write вслух говорит про выдачу приватных ключей (RU и EN)" \
    'grep -q "ВЫДАЁТ приватный ключ любого клиента" "$ROOT/lib/lang/ru.d/agents.sh" && grep -q "HANDS OUT the private key of any client" "$ROOT/lib/lang/en.d/agents.sh"' \
    "$(grep -h "ag_scope_write" "$ROOT/lib/lang/ru.d/agents.sh" "$ROOT/lib/lang/en.d/agents.sh")"
check "SSH-словарь: write вслух говорит, что сносит сайт и транспорт агентов, включая свой" \
    'grep -q "включая собственный" "$ROOT/lib/lang/ru.d/agents.sh" && grep -q "its own included" "$ROOT/lib/lang/en.d/agents.sh"' \
    "$(grep -h "ag_scope_write" "$ROOT/lib/lang/ru.d/agents.sh" "$ROOT/lib/lang/en.d/agents.sh")"
check "SSH-словарь: read и admin тоже названы составом, а не одним словом" \
    'grep -q "ag_scope_read\]=\"read — смотреть" "$ROOT/lib/lang/ru.d/agents.sh" && grep -q "ag_scope_admin\]=\"admin — всё из write" "$ROOT/lib/lang/ru.d/agents.sh"' \
    "$(grep -h "ag_scope_read\|ag_scope_admin" "$ROOT/lib/lang/ru.d/agents.sh")"
id_def=$(jget .id)
run agents_del "$id_def"
run agents_add "Второй агент" read 0 "" "" on
check "второй доступ (scope=read, days=0 — явно бессрочный, pin_ip=on): id другой, expires пуст, pin_ip=true, две записи" \
    '[ "$rc" = 0 ] && [ "$(jget .id)" != "$id1" ] && [ "$(jget .scope)" = read ] && [ "$(jget .expires)" = "" ] && [ "$(jget .pin_ip)" = true ] && [ "$(jq -r ".agents|length" "$AGENTS")" = 2 ]' "$out"
check "бессрочный доступ выдан с предупреждением в stderr" 'grep -q "$(t agents_warn_forever | head -c 40)" "$ERR"' "$(errs)"
id2=$(jget .id); url2=$(jget .url); token2=$(jget .token)
run agents_add "Кривая привязка" read 30 "" "" maybe
check "pin_ip не on/off → invalid_state, rc=2" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_state ]' "rc=$rc code=$GA_ERR_CODE"

# ── 4. server.py читает этот agents.json ─────────────────────────────────────
section "server.py --check и живой --http на этом agents.json"
mcp_env=(env GOAMNEZIA_DIR="$GOAMNEZIA_DIR" GOAMNEZIA_ACTION_BIN="$FAKE_BIN" GOAMNEZIA_ACTIONS_JSON="$ROOT/lib/actions.json"
         GOAMNEZIA_LOCK_DIR="$GOAMNEZIA_LOCK_DIR" GOAMNEZIA_AUDIT_LOG="$TMP/mcp-audit.log" GOAMNEZIA_MCP_LOG=info)
if command -v python3 >/dev/null 2>&1; then
    "${mcp_env[@]}" python3 "$SERVER" --check >"$OUT" 2>"$ERR"; rc=$?
    check "server.py --check: rc=0, видит http включённым с публичным портом 40000, доступов 2 (действующих 2)" \
        '[ "$rc" = 0 ] && grep -q "http         : включён" "$ERR" && grep -q "публичный TLS-порт 40000" "$ERR" && grep -q "доступов 2 (действующих 2" "$ERR"' "$(errs)"
    check "--check не печатает секретов" '! grep -qF -e "$secret1" "$ERR"'
else
    skipped "server.py --check" "нет python3"
fi
if command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
    "${mcp_env[@]}" python3 "$SERVER" --http --bind 127.0.0.1 --port "$port" --extra-ports none >"$TMP/srv.log" 2>&1 &
    SRV_PID=$!
    for _ in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$port/" 2>/dev/null && break; sleep 0.1; done
    # post <секрет> [ip] [токен] — без третьего аргумента заголовка Authorization нет вовсе
    post() {
        local hdr=()
        [ -n "${3:-}" ] && hdr=(-H "Authorization: Bearer $3")
        curl -s -o "$OUT" -w '%{http_code}' -H 'Content-Type: application/json' -H "X-Real-IP: ${2:-203.0.113.5}" \
             "${hdr[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' "http://127.0.0.1:$port/a-$1/mcp"
    }
    code=$(post "$secret1" 203.0.113.5 "$token1"); out=$(<"$OUT")
    check "живой сервер: верный секрет и верный токен (IP из allow_ips) → 200, tools/list содержит user_get_key (write), но не user_delete" \
        '[ "$code" = 200 ] && grep -q "\"user_get_key\"" <<<"$out" && ! grep -q "\"user_delete\"" <<<"$out"' "code=$code $(head -c 300 "$OUT")"
    # Второй фактор: адрес без заголовка бесполезен — и отличить это от «нет такого пути» нельзя
    code=$(post "$secret1")
    body_no_token=$(<"$OUT")
    check "верный адрес БЕЗ заголовка Authorization → 404" '[ "$code" = 404 ]' "code=$code"
    code=$(post "$secret1" 203.0.113.5 "$token2")
    check "верный адрес с ЧУЖИМ токеном → 404, тело как у неизвестного пути" \
        '[ "$code" = 404 ] && [ "$(<"$OUT")" = "$body_no_token" ]' "code=$code"
    code=$(post "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" 203.0.113.5 "$token1")
    check "неизвестный адрес с верным токеном → тот же 404 (существование пути не подтверждаем)" \
        '[ "$code" = 404 ] && [ "$(<"$OUT")" = "$body_no_token" ]' "code=$code"
    code=$(post "$secret1" 198.51.100.200 "$token1")
    check "чужой IP (не из allow_ips) → 404" '[ "$code" = 404 ]' "code=$code"
    secret2="${url2#*/a-}"; secret2="${secret2%/mcp}"
    # Второй доступ выдан с pin_ip=on: первый адрес закрепится, второй получит 404
    code=$(post "$secret2" 203.0.113.5 "$token2"); out=$(<"$OUT")
    check "второй доступ (read) → 200, в списке нет user_get_key и user_add" \
        '[ "$code" = 200 ] && grep -q "\"server_status\"" <<<"$out" && ! grep -q "\"user_get_key\"" <<<"$out" && ! grep -q "\"user_add\"" <<<"$out"' "code=$code"
    check "привязка: сервер закрепил адрес первого вызова (pinned_ip=203.0.113.5)" \
        '[ "$(jq -r --arg id "$id2" ".agents[] | select(.id == \$id) | .pinned_ip" "$AGENTS")" = 203.0.113.5 ]' "$(jq -c --arg id "$id2" ".agents[] | select(.id == \$id) | {pin_ip, pinned_ip}" "$AGENTS")"
    code=$(post "$secret2" 203.0.113.99 "$token2")
    check "привязка: тот же доступ с ДРУГОГО адреса → 404 (верны и адрес, и токен)" '[ "$code" = 404 ]' "code=$code"
    run agents_pin "$id2"
    check "agents_pin без состояния → invalid_state (забытый аргумент не должен снимать привязку)" \
        '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_state ]' "rc=$rc code=$GA_ERR_CODE"
    run agents_pin "$id2" off
    code=$(post "$secret2" 203.0.113.99 "$token2")
    check "agents_pin off → привязка снята, доступ снова 200 с любого адреса" \
        '[ "$rc" = 0 ] && [ "$(jget .pin_ip)" = false ] && [ "$code" = 200 ]' "code=$code $out"
    # Уведомления владельцу: первое использование и вызов с нового адреса
    check "очередь уведомлений: agent_first_use по обоим доступам, agent_new_ip по второму, без секретов" \
        '[ -d "$NOTIFY" ] && [ "$(cat "$NOTIFY"/*.json | jq -rs "[.[] | select(.event == \"agent_first_use\")] | length")" -ge 2 ] && cat "$NOTIFY"/*.json | jq -rs "[.[] | select(.event == \"agent_new_ip\") | .agent]" | grep -qF "$id2" && ! grep -qF -e "$secret1" "$NOTIFY"/*.json && ! grep -qF -e "$token1" "$NOTIFY"/*.json' \
        "$(cat "$NOTIFY"/*.json 2>/dev/null | head -c 500)"
    check "очередь уведомлений: тревога agent_token_failed о запросе по верному адресу без токена" \
        'cat "$NOTIFY"/*.json | jq -rs "[.[] | select(.event == \"agent_token_failed\") | .agent]" | grep -qF "$id1"' \
        "$(cat "$NOTIFY"/*.json 2>/dev/null | jq -rs "[.[] | .event] | unique | join(\",\")")"
    run agents_disable "$id2"
    code=$(post "$secret2" 203.0.113.5 "$token2")
    check "agents_disable → следующий запрос этого доступа получает 404 (отзыв без перезапуска)" '[ "$rc" = 0 ] && [ "$code" = 404 ]' "code=$code"
    run agents_enable "$id2"
    code=$(post "$secret2" 203.0.113.5 "$token2")
    check "agents_enable → снова 200" '[ "$rc" = 0 ] && [ "$code" = 200 ]' "code=$code"
    # Старый доступ без require_token (выдан до второго фактора) — правим файл руками
    jq --arg id "$id2" '.agents |= map(if .id == $id then del(.token, .require_token) else . end)' "$AGENTS" >"$AGENTS.tmp" && mv "$AGENTS.tmp" "$AGENTS"
    code=$(post "$secret2" 203.0.113.5)
    check "доступ без require_token (выдан до второго фактора) работает по одному адресу — совместимость не сломана" \
        '[ "$code" = 200 ]' "code=$code"
    run agents_list
    check "agents_list помечает такой доступ: weak=true, require_token=false" \
        '[ "$(jget ".[] | select(.id == \"$id2\") | .weak")" = true ] && [ "$(jget ".[] | select(.id == \"$id2\") | .require_token")" = false ]' "$out"
    run agents_http_status
    check "agents_http_status: agents_weak=1" '[ "$(jget .agents_weak)" = 1 ]' "$out"
    # Усиление: выдать токен старому доступу
    run agents_harden "$id2"
    token2b=$(jget .token)
    check "agents_harden: rc=0, require_token=true, токен 43 символа, id и секрет прежние, аудит agent-harden" \
        '[ "$rc" = 0 ] && [ "$(jget .require_token)" = true ] && [[ "$token2b" =~ $SECRET_RE ]] && [ "$(jget .id)" = "$id2" ] && [ "$(jget .url)" = "$url2" ] && grep -q "agent-harden" "$GOAMNEZIA_AUDIT_LOG"' "$out"
    code=$(post "$secret2" 203.0.113.5)
    check "после усиления прежний способ (один адрес) → 404" '[ "$code" = 404 ]' "code=$code"
    code=$(post "$secret2" 203.0.113.5 "$token2b")
    check "после усиления адрес с новым токеном → 200" '[ "$code" = 200 ]' "code=$code"
    run agents_harden "$id2"
    check "повторное усиление → already_strong (токен уже есть)" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = already_strong ]' "rc=$rc code=$GA_ERR_CODE"
    token2="$token2b"
    run agents_rotate "$id1"
    url1b=$(jget .url); secret1b="${url1b#*/a-}"; secret1b="${secret1b%/mcp}"; token1b=$(jget .token)
    code_old=$(post "$secret1" 203.0.113.5 "$token1"); code_new=$(post "$secret1b" 203.0.113.5 "$token1b")
    code_mix=$(post "$secret1b" 203.0.113.5 "$token1")
    check "agents_rotate → старый секрет 404, новый 200; токен тоже сменился (новый адрес со старым токеном → 404)" \
        '[ "$rc" = 0 ] && [ "$code_old" = 404 ] && [ "$code_new" = 200 ] && [ "$token1b" != "$token1" ] && [ "$code_mix" = 404 ]' "old=$code_old new=$code_new mix=$code_mix"
    token1="$token1b"
    check "сервер обновил учёт: last_used, last_ip=203.0.113.5, calls>0 у первого доступа" \
        '[ -n "$(jq -r --arg id "$id1" ".agents[] | select(.id == \$id) | .last_used" "$AGENTS")" ] && [ "$(jq -r --arg id "$id1" ".agents[] | select(.id == \$id) | .last_ip" "$AGENTS")" = 203.0.113.5 ] && [ "$(jq -r --arg id "$id1" ".agents[] | select(.id == \$id) | .calls" "$AGENTS")" -ge 1 ]' "$(jq -c ".agents[] | del(.secret)" "$AGENTS")"
    check "agents.json после записей сервера остался 0600 и читается модулем" '[ "$(perm "$AGENTS")" = 600 ] && agents_read >/dev/null'
    run agents_list
    check "agents_list после учёта сервера: calls и last_ip видны, секрета нет" \
        '[ "$(jget ".[] | select(.id == \"$id1\") | .last_ip")" = 203.0.113.5 ] && ! grep -qF -e "$secret1b" <<<"$out"' "$out"
    check "лог сервера не содержит ни секретов, ни токенов (путь маскируется, заголовок не логируется)" \
        '! grep -qF -e "$secret1" "$TMP/srv.log" && ! grep -qF -e "$secret1b" "$TMP/srv.log" && ! grep -qF -e "$token1" "$TMP/srv.log" && ! grep -qF -e "$token2" "$TMP/srv.log"' "$(tail -n 5 "$TMP/srv.log")"
    kill "$SRV_PID" 2>/dev/null; wait "$SRV_PID" 2>/dev/null; SRV_PID=""
    secret1="$secret1b"; url1="$url1b"
else
    skipped "живой server.py --http" "нет python3 или curl"
fi

# ── 5. Проверки входа ────────────────────────────────────────────────────────
section "проверки входа"
run agents_add "" write
check "пустое название → invalid_label, rc=2" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_label ]' "rc=$rc code=$GA_ERR_CODE"
run agents_add "Тест" root
check "уровень root → invalid_scope, rc=2" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_scope ]' "rc=$rc code=$GA_ERR_CODE"
run agents_add "Тест" admin 9999
check "срок 9999 → invalid_days" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_days ]' "rc=$rc code=$GA_ERR_CODE"
run agents_add "Тест" admin 0 "300.1.1.1"
check "октет 300 → invalid_ips" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_ips ]' "rc=$rc code=$GA_ERR_CODE"
run agents_add "Тест" admin 0 "10.0.0.0/33"
check "префикс /33 → invalid_ips" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_ips ]' "rc=$rc code=$GA_ERR_CODE"
# 1c.8. Ведущий ноль: bash читает 010 как восьмеричное 8 и пропускал запись, а python
# ipaddress на ней падает — агент оказывался заперт своим же белым списком молча.
run agents_add "Тест" admin 0 "010.0.0.1"
check "ведущий ноль 010.0.0.1 → invalid_ips (python такую запись не разбирает)" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_ips ]' "rc=$rc code=$GA_ERR_CODE"
run agents_add "Тест" admin 0 "203.0.113.0/08"
check "ведущий ноль в префиксе /08 → invalid_ips" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_ips ]' "rc=$rc code=$GA_ERR_CODE"
# /0 выглядит как «только мой адрес», а означает «любой»: опечатка в один символ
# молча снимала ограничение целиком.
run agents_add "Тест" admin 0 "203.0.113.10/0"
check "префикс /0 → invalid_ips (это «любой адрес», а не «мой»)" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_ips ]' "rc=$rc code=$GA_ERR_CODE"
run agents_add "Тест" admin 0 "10.0.0.1,,10.0.0.2"
check "пустая часть списка → invalid_ips" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_ips ]' "rc=$rc code=$GA_ERR_CODE"
check "годные записи по-прежнему проходят: адрес, подсеть /32 и /1" \
    'ga_agents_ips_valid "203.0.113.7, 198.51.100.0/24" && ga_agents_ips_valid "10.0.0.1/32" && ga_agents_ips_valid "0.0.0.0/1" && ga_agents_ips_valid ""'
run agents_add "Тест" admin 0 "" 40000
check "персональный порт = публичный порт транспорта → port_busy" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = port_busy ]' "rc=$rc code=$GA_ERR_CODE"
run agents_add "Тест" admin 0 "" 1986
check "персональный порт = петля MCP → port_busy" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = port_busy ]' "rc=$rc code=$GA_ERR_CODE"
check "неудачные вызовы записей не добавили: по-прежнему 2" '[ "$(jq -r ".agents|length" "$AGENTS")" = 2 ]'
run agents_show "a-zzzz"
check "agents_show a-zzzz → invalid_agent_id, rc=2" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_agent_id ]' "rc=$rc code=$GA_ERR_CODE"
run agents_show "a-0000"
check "agents_show a-0000 → agent_not_found" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = agent_not_found ]' "rc=$rc code=$GA_ERR_CODE"
run agents_del "a-0000"
check "agents_del a-0000 → agent_not_found" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = agent_not_found ]' "rc=$rc code=$GA_ERR_CODE"

# ── 5a. Одноразовый ключ (mode=rolling): выдача, цепочка, клон, перевыпуск ───
# Здесь проверяется bash-сторона: что выдаётся, что ложится в файл и что
# исчезает при перевыпуске и отзыве. Поведение сервера на горячем пути —
# tests/test_mcp_http.py, класс RollingKeyTests.
section "agents_add mode=rolling: ключ, отпечаток, состояние цепочки"
run agents_add "Одноразовый ключ" write 30 "" "" off rolling
rid=$(jget .id); rkey=$(jget .key); rsecret=$(jget .url); rsecret="${rsecret#*/a-}"; rsecret="${rsecret%/mcp}"
rtoken=$(jget .token)
check "agents_add rolling: rc=0, mode=rolling, ключ 43 символа base64url, подсказка «показан один раз»" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .mode)" = rolling ] && [[ "$rkey" =~ $SECRET_RE ]] && [ -n "$(jget .key_hint)" ]' "$out"
check "карточка rolling: правило «один доступ — один клиент» и предупреждение про синхронизируемые папки" \
    'grep -qF "один клиент" <<<"$(jget .one_client_hint)" && grep -qiF "dropbox" <<<"$(jget .one_client_hint)"' "$(jget .one_client_hint)"
check "карточка rolling: команды нет (обычный клиент так не умеет), пояснение с заголовками X-Ga-Key / X-Ga-Next-Key — в connect_note" \
    '[ -z "$(jget .connect)" ] && grep -qF "X-Ga-Key" <<<"$(jget .connect_note)" && grep -qF "X-Ga-Next-Key" <<<"$(jget .connect_note)" && ! grep -qF "claude mcp add" <<<"$(jget .connect_note)"' "$(jget .connect_note)"
check "agents.json: mode=rolling, key_hash — sha256 ключа, САМОГО ключа в файле нет" \
    '[ "$(jq -r --arg id "$rid" ".agents[] | select(.id == \$id) | .mode" "$AGENTS")" = rolling ] && [ "$(jq -r --arg id "$rid" ".agents[] | select(.id == \$id) | .key_hash" "$AGENTS")" = "$(ga_agents_key_hash "$rkey")" ] && ! grep -qF -e "$rkey" "$AGENTS"' \
    "$(jq -c --arg id "$rid" ".agents[] | select(.id == \$id) | {mode, key_hash}" "$AGENTS")"
check "ключ не попал ни в stderr, ни в аудит; аудит помечает вид доступа" \
    '! grep -qF -e "$rkey" "$ERR" && ! grep -qF -e "$rkey" "$GOAMNEZIA_AUDIT_LOG" && grep -q "id=$rid scope=write mode=rolling" "$GOAMNEZIA_AUDIT_LOG"' "$(errs)"
run agents_list
check "agents_list: mode=rolling, weak=false (второй фактор — сам ключ), state=active, отпечатка ключа в списке нет" \
    '[ "$(jget ".[] | select(.id == \"$rid\") | .mode")" = rolling ] && [ "$(jget ".[] | select(.id == \"$rid\") | .weak")" = false ] && [ "$(jget ".[] | select(.id == \"$rid\") | .state")" = active ] && [ "$(jget "[.[] | has(\"key_hash\")] | any")" = false ]' "$out"
run agents_http_status
check "agents_http_status: agents_rolling=1, agents_compromised=0" \
    '[ "$(jget .agents_rolling)" = 1 ] && [ "$(jget .agents_compromised)" = 0 ]' "$out"
run agents_add "Кривой вид" write 30 "" "" off carousel
check "неизвестный вид доступа → invalid_mode, rc=2" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_mode ]' "rc=$rc code=$GA_ERR_CODE"
run agents_show "$rid"
check "agents_show: ключ повторно НЕ показывается (на сервере его нет — только отпечаток)" \
    '[ "$rc" = 0 ] && [ "$(jget .mode)" = rolling ] && [ "$(jget .key)" = null ]' "$out"

# L-08: «заморозить не удалось» — клон обнаружен, а agents.json не записался. Отметку пишет server.py рядом с
# цепочкой (ChainStore.mark_freeze_failed), agents_list отдаёт её полями freeze_failed / _at / _ip.
ff_file=$(ga_agents_freeze_failed_file "$rid")
mkdir -p "$(dirname "$ff_file")"
printf '{"id":"%s","at":"2026-09-27T10:00:00Z","ip":"198.51.100.66"}\n' "$rid" >"$ff_file"
printf 'not json\n' >"$(ga_agents_state_dir)/a-dead.freeze_failed"
run agents_list
check "agents_list: freeze_failed=true с временем и адресом клона; у остальных false; испорченная отметка не роняет список" \
    '[ "$rc" = 0 ] && [ "$(jget ".[] | select(.id == \"$rid\") | .freeze_failed")" = true ] && [ "$(jget ".[] | select(.id == \"$rid\") | .freeze_failed_ip")" = 198.51.100.66 ] && [ "$(jget ".[] | select(.id == \"$rid\") | .freeze_failed_at")" = 2026-09-27T10:00:00Z ] && [ "$(jget "[.[] | select(.id != \"$rid\") | .freeze_failed] | any")" = false ]' "$out"
run agents_disable "$rid"
check "выключение доступа — ответ владельца: отметка «заморозить не удалось» снята" '[ "$rc" = 0 ] && [ ! -f "$ff_file" ]' "rc=$rc"
run agents_enable "$rid"
rm -f "$(ga_agents_state_dir)/a-dead.freeze_failed"

# Живой сервер: цепочка вращается, клон замораживает доступ, мусор — нет.
if command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    rport=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
    "${mcp_env[@]}" python3 "$SERVER" --http --bind 127.0.0.1 --port "$rport" --extra-ports none >"$TMP/srv-roll.log" 2>&1 &
    SRV_PID=$!
    for _ in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$rport/" 2>/dev/null && break; sleep 0.1; done
    # roll <ключ> <номер> — код ответа в $ROLL_CODE, выданный ключ в $NEXT_KEY.
    # Не через $(...): в подоболочке присвоение NEXT_KEY потерялось бы, и вся
    # цепочка ходила бы по кругу первым ключом.
    NEXT_KEY=""; ROLL_CODE=""
    roll() {
        ROLL_CODE=$(curl -s -o "$OUT" -D "$TMP/hdr.txt" -w '%{http_code}' -H 'Content-Type: application/json' \
               -H "X-Real-IP: 203.0.113.5" -H "Authorization: Bearer $rtoken" \
               -H "X-Ga-Key: $1" -H "X-Ga-Nonce: $2" \
               -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' "http://127.0.0.1:$rport/a-$rsecret/mcp")
        NEXT_KEY=$(tr -d '\r' <"$TMP/hdr.txt" | grep -i '^X-Ga-Next-Key:' | head -1 | cut -d' ' -f2-)
    }
    roll "$rkey" nonce0001; code="$ROLL_CODE"; k1="$NEXT_KEY"
    check "живой сервер: первый ключ цепочки → 200 и следующий ключ заголовком X-Ga-Next-Key" \
        '[ "$code" = 200 ] && [[ "$k1" =~ $SECRET_RE ]] && [ "$k1" != "$rkey" ] && ! grep -qF -e "$k1" "$OUT"' "code=$code next=${k1:0:6}…"
    roll "$k1" nonce0002; code="$ROLL_CODE"; k2="$NEXT_KEY"
    check "второй запрос новым ключом → 200, ключ снова сменился" '[ "$code" = 200 ] && [ -n "$k2" ] && [ "$k2" != "$k1" ]' "code=$code"
    roll "qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq" nonce0003; code="$ROLL_CODE"
    check "мусорный ключ → 404 и доступ НЕ заморожен (иначе всякий, кто узнал путь, гасил бы агента)" \
        '[ "$code" = 404 ] && [ "$(jq -r --arg id "$rid" ".agents[] | select(.id == \$id) | .compromised" "$AGENTS")" != true ]' "code=$code"
    roll "$k2" nonce0004; code="$ROLL_CODE"; k3="$NEXT_KEY"
    check "после мусора честный агент работает прежним ключом" '[ "$code" = 200 ] && [ -n "$k3" ]' "code=$code"
    roll "$rkey" nonce0005; code="$ROLL_CODE"
    check "ключ из истории выданных (копия на чужой машине) → 404 и доступ заморожен" \
        '[ "$code" = 404 ] && [ "$(jq -r --arg id "$rid" ".agents[] | select(.id == \$id) | .compromised" "$AGENTS")" = true ] && [ -n "$(jq -r --arg id "$rid" ".agents[] | select(.id == \$id) | .compromised_ip" "$AGENTS")" ]' "code=$code"
    roll "$k3" nonce0006; code="$ROLL_CODE"
    check "замороженный доступ не впускает и честного агента — решение за владельцем" '[ "$code" = 404 ]' "code=$code"
    clone_ev=$(cat "$NOTIFY"/*.json 2>/dev/null | jq -sc --arg id "$rid" '[.[] | select(.event == "agent_compromised" and .agent == $id)] | first // {}')
    check "владельцу ушло уведомление agent_compromised с двумя адресами и двумя временами, без ключей" \
        '[ -n "$(jq -r ".clone_ip // \"\"" <<<"$clone_ev")" ] && [ -n "$(jq -r ".clone_at // \"\"" <<<"$clone_ev")" ] && [ -n "$(jq -r ".last_ip // \"\"" <<<"$clone_ev")" ] && [ -n "$(jq -r ".last_used // \"\"" <<<"$clone_ev")" ] && ! grep -qF -e "$rkey" <<<"$clone_ev" && ! grep -qF -e "$k3" <<<"$clone_ev"' \
        "$clone_ev"
    run agents_list
    check "agents_list: состояние заморожённого — compromised, видны время и адрес клона" \
        '[ "$(jget ".[] | select(.id == \"$rid\") | .state")" = compromised ] && [ -n "$(jget ".[] | select(.id == \"$rid\") | .compromised_ip")" ]' "$out"
    state_file=$(ga_agents_state_file "$rid")
    check "состояние цепочки лежит отдельным файлом 0600 в постоянном каталоге, а не в /run" \
        '[ -f "$state_file" ] && [ "$(perm "$state_file")" = 600 ] && [[ "$state_file" != "$GOAMNEZIA_LOCK_DIR"* ]]' "$state_file"
    check "заморозка удалась — отметки «заморозить не удалось» нет, в списке freeze_failed=false (L-08)" \
        '[ ! -f "$(ga_agents_freeze_failed_file "$rid")" ] && [ "$(jget ".[] | select(.id == \"$rid\") | .freeze_failed")" = false ]' "$out"
    # Отметка прошлой неудачной заморозки (L-08) перевыпуск тоже снимает: это ответ владельца на карточку
    printf '{"id":"%s","at":"2026-09-27T10:00:00Z","ip":"198.51.100.66"}\n' "$rid" >"$(ga_agents_freeze_failed_file "$rid")"
    # «Это был я, перевыдать»: новая цепочка, снятая заморозка, мёртвая старая
    run agents_rotate "$rid"
    rkey2=$(jget .key); rsecret2=$(jget .url); rsecret2="${rsecret2#*/a-}"; rsecret2="${rsecret2%/mcp}"; rtoken=$(jget .token)
    check "agents_rotate у rolling: новый ключ, новый секрет, заморозка снята, файл прежней цепочки и отметка «заморозить не удалось» удалены" \
        '[ "$rc" = 0 ] && [[ "$rkey2" =~ $SECRET_RE ]] && [ "$rkey2" != "$rkey" ] && [ "$(jget .compromised)" = false ] && [ "$(jq -r --arg id "$rid" ".agents[] | select(.id == \$id) | .compromised" "$AGENTS")" = false ] && [ ! -e "$state_file" ] && [ ! -e "$(ga_agents_freeze_failed_file "$rid")" ]' "$out"
    rsecret="$rsecret2"
    roll "$rkey2" nonce0007; code="$ROLL_CODE"
    check "после перевыпуска новая цепочка работает" '[ "$code" = 200 ]' "code=$code"
    roll "$rkey" nonce0008; code="$ROLL_CODE"
    check "ключ мёртвой цепочки — просто неизвестный ключ (404), а не новый повод для заморозки" \
        '[ "$code" = 404 ] && [ "$(jq -r --arg id "$rid" ".agents[] | select(.id == \$id) | .compromised" "$AGENTS")" = false ]' "code=$code"
    check "лог сервера не содержит ни одного ключа цепочки" \
        '! grep -qF -e "$rkey" "$TMP/srv-roll.log" && ! grep -qF -e "$rkey2" "$TMP/srv-roll.log" && ! grep -qF -e "$k1" "$TMP/srv-roll.log" && ! grep -qF -e "$k3" "$TMP/srv-roll.log"' "$(tail -n 5 "$TMP/srv-roll.log")"
    kill "$SRV_PID" 2>/dev/null; wait "$SRV_PID" 2>/dev/null; SRV_PID=""
else
    skipped "живая цепочка одноразового ключа" "нет python3 или curl"
fi
run agents_del "$rid"
check "agents_del у rolling: rc=0, файл состояния цепочки удалён вместе с доступом" \
    '[ "$rc" = 0 ] && [ ! -e "$(ga_agents_state_file "$rid")" ]' "$(errs)"

# ── 5b. Переходник (mode=connect): код привязки, привязка, плавающий путь ────
# bash-сторона выдачи и перевыпуска плюс сквозной прогон НАСТОЯЩЕГО переходника
# goamnezia-mcp/connect.py против живого server.py на этом agents.json. Формулы
# путей по отдельности — tests/test_connect.py и tests/test_mcp_http.py.
section "agents_add mode=connect: код привязки вместо секретов, привязка переходником"
CONNECT_PY="$ROOT/goamnezia-mcp/connect.py"
CODE_RE='^GA-[A-HJKMNP-TV-Z2-9]{5}-[A-HJKMNP-TV-Z2-9]{5}-[A-HJKMNP-TV-Z2-9]{5}-[A-HJKMNP-TV-Z2-9]{5}$'
crec() { jq -c --arg id "$cid" '.agents[] | select(.id == $id)' "$AGENTS" 2>/dev/null; }
# Код живёт 10 минут: при выключенном транспорте он сгорел бы раньше, чем переходнику
# станет куда привязываться, — выдача отказывает сразу и ничего не пишет.
cnt_before=$(jq '.agents | length' "$AGENTS")
ga_config_merge '{"mcp":{"http_enabled":false}}' >/dev/null 2>&1
run agents_add "Переходник" read 30 "" "" off connect
check "транспорт выключен → agents_add connect: transport_off, rc=1, запись не создана" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = transport_off ] && [ "$(jq ".agents | length" "$AGENTS")" = "$cnt_before" ]' "rc=$rc code=$GA_ERR_CODE"
ga_config_merge '{"mcp":{"http_enabled":true}}' >/dev/null 2>&1
run agents_add "Переходник" read 30 "" "" off connect
cid=$(jget .id); ccode=$(jget .enroll_code)
check "agents_add connect: rc=0, mode=connect, код GA-XXXXX-…, ни адреса, ни токена, ни ключа в карточке" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .mode)" = connect ] && [[ "$ccode" =~ $CODE_RE ]] && [ -z "$(jget .url)" ] && [ -z "$(jget .token)" ] && [ "$(jget .key)" = null ] && [ "$(jget .enroll_pending)" = true ]' "$out"
check "карточка connect: команда привязки с адресом сервера и отпечатком, подсказка, откуда взять переходник" \
    'grep -qF "goamnezia-connect enroll $ccode --server https://203.0.113.10:40000 --fingerprint " <<<"$(jget .connect)" && grep -qF "scp root@203.0.113.10:$GOAMNEZIA_DIR/goamnezia-mcp/connect.py " <<<"$(jget .connector_hint)" && [ -n "$(jget .enroll_hint)" ]' \
    "$(jget .connect) | $(jget .connector_hint)"
cpath=$(jq -r '.enroll_path // ""' <<<"$(crec)")
check "agents.json: transport=connect, mode=rolling, путь привязки 43 знака, токена и отпечатка ключа нет, САМОГО кода в файле нет" \
    '[ "$(jq -r .transport <<<"$(crec)")" = connect ] && [ "$(jq -r .mode <<<"$(crec)")" = rolling ] && [[ "$cpath" =~ $SECRET_RE ]] && [ "$(jq -r .token <<<"$(crec)")" = "" ] && [ "$(jq -r .key_hash <<<"$(crec)")" = "" ] && ! grep -qF -e "$ccode" "$AGENTS"' "$(crec)"
cexp=$(jq -r '.enroll_expires // ""' <<<"$(crec)")
check "код живёт 10 минут: enroll_expires = сейчас + 600 с (±10)" \
    'left=$(( $(ga_agents_iso_epoch "$cexp") - $(date +%s) )); [ "$left" -ge 590 ] && [ "$left" -le 600 ]' "$cexp"
py_path=$(printf '%s' "$ccode" | python3 -c 'import importlib.util, sys
spec = importlib.util.spec_from_file_location("gaconnect", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.enroll_path(sys.stdin.read()))' "$CONNECT_PY" 2>/dev/null)
check "путь привязки в agents.json = формуле переходника; код строчными и с пробелами даёт тот же путь" \
    '[ -n "$cpath" ] && [ "$cpath" = "$py_path" ] && [ "$(ga_agents_enroll_path "$(tr "[:upper:]" "[:lower:]" <<<"${ccode//-/ }")")" = "$cpath" ]' "bash=$cpath py=$py_path"
check "код не попал ни в stderr, ни в аудит; аудит помечает вид доступа" \
    '! grep -qF -e "$ccode" "$ALL_ERR" && ! grep -qF -e "$ccode" "$GOAMNEZIA_AUDIT_LOG" && grep -q "id=$cid scope=read mode=connect" "$GOAMNEZIA_AUDIT_LOG"' "$(tail -n 3 "$GOAMNEZIA_AUDIT_LOG")"
run agents_list
check "agents_list: mode=connect, enroll_pending=true; ни rot_seed, ни пути привязки в списке нет" \
    '[ "$(jget ".[] | select(.id == \"$cid\") | .mode")" = connect ] && [ "$(jget ".[] | select(.id == \"$cid\") | .enroll_pending")" = true ] && [ "$(jget "[.[] | has(\"rot_seed\") or has(\"enroll_path\")] | any")" = false ] && ! grep -qF -e "$cpath" <<<"$out"' "$out"
run agents_show "$cid"
check "agents_show до привязки: код повторно не показывается (на сервере только отпечаток), вместо команды — объяснение" \
    '[ "$rc" = 0 ] && [ "$(jget .enroll_code)" = null ] && [ -z "$(jget .connect)" ] && [ -n "$(jget .connect_note)" ] && [ "$(jget .enroll_expired)" = false ]' "$out"

if command -v python3 >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    cport=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
    "${mcp_env[@]}" python3 "$SERVER" --http --bind 127.0.0.1 --port "$cport" --extra-ports none >"$TMP/srv-connect.log" 2>&1 &
    SRV_PID=$!
    for _ in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$cport/" 2>/dev/null && break; sleep 0.1; done
    CDIR="$TMP/connector"
    # connect <аргументы переходника…> — stdout в $OUT, stderr в $ERR, код в $rc
    connector() { python3 "$CONNECT_PY" "$@" >"$OUT" 2>"$ERR"; rc=$?; cat "$ERR" >>"$ALL_ERR"; return 0; }
    connector enroll "$ccode" --server "http://127.0.0.1:$cport" --storage file --dir "$CDIR"
    check "переходник привязался кодом: rc=0, файл настроек у переходника 0600" \
        '[ "$rc" = 0 ] && [ -f "$CDIR/$cid.json" ] && [ "$(perm "$CDIR/$cid.json")" = 600 ]' "rc=$rc $(errs)"
    crec_after=$(crec)
    ctoken=$(jq -r '.token // ""' <<<"$crec_after"); cseed=$(jq -r '.rot_seed // ""' <<<"$crec_after")
    check "agents.json после привязки: rot_seed и токен по 43 знака, key_hash, key_storage=file; пути и срока кода больше нет; файл 0600" \
        '[[ "$cseed" =~ $SECRET_RE ]] && [[ "$ctoken" =~ $SECRET_RE ]] && [[ "$(jq -r .key_hash <<<"$crec_after")" =~ ^[0-9a-f]{64}$ ]] && [ "$(jq -r .key_storage <<<"$crec_after")" = file ] && [ "$(jq -r "has(\"enroll_path\") or has(\"enroll_expires\")" <<<"$crec_after")" = false ] && [ "$(perm "$AGENTS")" = 600 ]' "$crec_after"
    enr_ev=$(cat "$NOTIFY"/*.json 2>/dev/null | jq -sc --arg id "$cid" '[.[] | select(.event == "agent_enrolled" and .agent == $id)] | first // {}')
    check "владельцу ушло уведомление agent_enrolled с адресом и местом хранения, без секретов" \
        '[ "$(jq -r ".storage // \"\"" <<<"$enr_ev")" = file ] && [ -n "$(jq -r ".ip // \"\"" <<<"$enr_ev")" ] && ! grep -qF -- "$ctoken" <<<"$enr_ev" && ! grep -qF -e "$cseed" <<<"$enr_ev"' "$enr_ev"
    printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"ping"}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
        '{"jsonrpc":"2.0","id":3,"method":"ping"}' >"$TMP/connect-in.txt"
    connector --grant "$cid" --dir "$CDIR" <"$TMP/connect-in.txt"
    check "клиент MCP через переходник: три запроса подряд (ключ вращается) → три ответа result, ни одной ошибки" \
        '[ "$rc" = 0 ] && [ "$(grep -c "\"result\"" "$OUT")" = 3 ] && ! grep -q "\"error\"" "$OUT" && jq -se "any(.[]; .id == 2 and (.result.tools | length > 0))" "$OUT"' \
        "rc=$rc $(head -c 400 "$OUT") $(errs)"
    connector status --grant "$cid" --dir "$CDIR"
    check "status переходника: ключ на месте, хранение file, секретов в выводе нет" \
        '[ "$rc" = 0 ] && [ "$(jq -r .key_present "$OUT")" = true ] && [ "$(jq -r .storage "$OUT")" = file ] && ! grep -qF -- "$ctoken" "$OUT" && ! grep -qF -e "$cseed" "$OUT"' "$(cat "$OUT")"
    connector enroll "$ccode" --server "http://127.0.0.1:$cport" --storage file --dir "$TMP/connector2"
    check "тот же код второй раз → отказ (сгорел с первой привязкой), вторая машина ничего не получила, привязка прежняя" \
        '[ "$rc" = 3 ] && [ ! -e "$TMP/connector2/$cid.json" ] && [ "$(jq -r .rot_seed <<<"$(crec)")" = "$cseed" ]' "rc=$rc $(errs)"
    run agents_show "$cid"
    check "agents_show после привязки: строка для клиента MCP с --grant, хранение file, токена в карточке нет" \
        '[ "$rc" = 0 ] && [ -z "$(jget .connect)" ] && grep -qF "status --grant $cid" <<<"$(jget .connect_note)" && [ "$(jget .key_storage)" = file ] && [ "$(jget .enroll_pending)" = false ] && [ -z "$(jget .token)" ] && [ -z "$(jget .warning)" ] && ! grep -qF -- "$ctoken" <<<"$out"' "$out"
    # Привязка к адресу осталась от прежней машины: перевыпуск обязан её снять, иначе новая
    # машина после привязки упиралась бы в 404 на каждом вызове.
    jq --arg id "$cid" '.agents |= map(if .id == $id then .pin_ip = true | .pinned_ip = "198.18.1.1" else . end)' "$AGENTS" >"$AGENTS.tmp" \
        && chmod 600 "$AGENTS.tmp" && mv "$AGENTS.tmp" "$AGENTS"
    run agents_rotate "$cid"
    ccode2=$(jget .enroll_code); crec_rot=$(crec)
    check "agents_rotate у connect снимает прежнюю привязку к адресу (pinned_ip пуст)" \
        '[ "$(jq -r ".pinned_ip // \"\"" <<<"$crec_rot")" = "" ]' "$crec_rot"
    check "agents_rotate у connect: новый код, прежние rot_seed, токен, ключ и хранение стёрты, путь привязки новый, файла цепочки нет" \
        '[ "$rc" = 0 ] && [[ "$ccode2" =~ $CODE_RE ]] && [ "$ccode2" != "$ccode" ] && [ "$(jq -r "(.rot_seed // \"\") + (.token // \"\") + (.key_hash // \"\") + (.key_storage // \"\")" <<<"$crec_rot")" = "" ] && [[ "$(jq -r .enroll_path <<<"$crec_rot")" =~ $SECRET_RE ]] && [ "$(jq -r .enroll_path <<<"$crec_rot")" != "$cpath" ] && [ ! -e "$(ga_agents_state_file "$cid")" ]' \
        "$crec_rot"
    printf '%s\n' '{"jsonrpc":"2.0","id":7,"method":"ping"}' >"$TMP/connect-in.txt"
    connector --grant "$cid" --dir "$CDIR" <"$TMP/connect-in.txt"
    check "прежний переходник после перевыпуска получает ошибку JSON-RPC, а не ответ" \
        '! grep -q "\"result\"" "$OUT" && grep -q "\"error\"" "$OUT"' "rc=$rc $(head -c 300 "$OUT")"
    # Истёкший код: срок в прошлом пишем прямо в файл, сервер перечитывает его сам
    jq --arg id "$cid" '.agents |= map(if .id == $id then .enroll_expires = "2020-01-01T00:00:00Z" else . end)' "$AGENTS" >"$AGENTS.tmp" \
        && chmod 600 "$AGENTS.tmp" && mv "$AGENTS.tmp" "$AGENTS"
    connector enroll "$ccode2" --server "http://127.0.0.1:$cport" --storage file --dir "$CDIR"
    check "истёкший код → отказ, привязки не случилось" \
        '[ "$rc" = 3 ] && [ -z "$(jq -r ".rot_seed // \"\"" <<<"$(crec)")" ]' "rc=$rc $(errs)"
    run agents_list
    check "agents_list: истёкший код виден (enroll_expired=true), а не «ждёт привязки» навсегда" \
        '[ "$(jget ".[] | select(.id == \"$cid\") | .enroll_expired")" = true ]' "$out"
    run agents_show "$cid"
    check "agents_show: при истёкшем коде команды нет, пояснение говорит про перевыпуск" \
        '[ -z "$(jget .connect)" ] && [ -n "$(jget .connect_note)" ] && [ "$(jget .enroll_expired)" = true ]' "$out"
    jq --arg id "$cid" --arg e "$(ga_agents_iso_in_seconds 600)" '.agents |= map(if .id == $id then .enroll_expires = $e else . end)' "$AGENTS" >"$AGENTS.tmp" \
        && chmod 600 "$AGENTS.tmp" && mv "$AGENTS.tmp" "$AGENTS"
    connector enroll "$ccode2" --server "http://127.0.0.1:$cport" --storage file --dir "$CDIR"
    enroll_rc=$rc
    connector --grant "$cid" --dir "$CDIR" <"$TMP/connect-in.txt"
    check "новый код в сроке привязывает заново, и переходник снова работает" \
        '[ "$enroll_rc" = 0 ] && grep -q "\"result\"" "$OUT"' "enroll_rc=$enroll_rc rc=$rc $(head -c 300 "$OUT") $(errs)"
    check "лог сервера не содержит ни кодов, ни токена, ни rot_seed" \
        '! grep -qF -e "$ccode" "$TMP/srv-connect.log" && ! grep -qF -e "$ccode2" "$TMP/srv-connect.log" && ! grep -qF -- "$ctoken" "$TMP/srv-connect.log" && ! grep -qF -e "$cseed" "$TMP/srv-connect.log"' \
        "$(tail -n 5 "$TMP/srv-connect.log")"
    kill "$SRV_PID" 2>/dev/null; wait "$SRV_PID" 2>/dev/null; SRV_PID=""
else
    skipped "живая привязка переходника" "нет python3 или curl"
fi
run agents_del "$cid"
check "agents_del у connect: rc=0, записи и файла цепочки нет" \
    '[ "$rc" = 0 ] && [ -z "$(crec)" ] && [ ! -e "$(ga_agents_state_file "$cid")" ]' "$(errs)"

# ── 6. Список, показ, перевыпуск, включение, истечение, удаление ──────────────
section "agents_list / show / rotate / disable / enable / expired / del"
run agents_list
check "agents_list: JSON-массив из 2 без поля secret, state=active, expired=false у обоих" \
    'one_line_json && [ "$(jget length)" = 2 ] && [ "$(jget "[.[] | has(\"secret\")] | any")" = false ] && [ "$(jget "[.[] | .state] | unique | join(\",\")")" = active ] && [ "$(jget "[.[] | .expired] | any")" = false ]' "$out"
check "agents_list: секретов нет и текстом" '! grep -qF -e "$secret1" <<<"$out"'
run agents_show "$id1"
check "agents_show: тот же адрес, что выдан (после перевыпуска — новый), аудит agent-show id" \
    '[ "$rc" = 0 ] && [ "$(jget .url)" = "$url1" ] && grep -q "agent-show" "$GOAMNEZIA_AUDIT_LOG"' "$out"
run agents_rotate "$id1"
url1c=$(jget .url)
check "agents_rotate: id прежний, адрес новый, секрет в файле = секрету из нового адреса, старого секрета в файле нет" \
    '[ "$rc" = 0 ] && [ "$(jget .id)" = "$id1" ] && [ "$url1c" != "$url1" ] && [[ "$url1c" =~ ^https://203\.0\.113\.10:40000/a-[A-Za-z0-9_-]{43}/mcp$ ]] && [ "$(jq -r --arg id "$id1" ".agents[] | select(.id == \$id) | .secret" "$AGENTS")" = "$(sed -E "s#.*/a-([A-Za-z0-9_-]{43})/mcp#\1#" <<<"$url1c")" ] && ! grep -qF -e "$secret1" "$AGENTS"' "$out"
url1="$url1c"; secret1=$(sed -E "s#.*/a-([A-Za-z0-9_-]{43})/mcp#\1#" <<<"$url1")
run agents_disable "$id1"
check "agents_disable: stdout {id, enabled:false}; в файле enabled=false; agents_list → state=disabled" \
    '[ "$rc" = 0 ] && [ "$(jget .enabled)" = false ] && [ "$(jq -r --arg id "$id1" ".agents[] | select(.id == \$id) | .enabled" "$AGENTS")" = false ] && [ "$(agents_list | jq -r --arg id "$id1" ".[] | select(.id == \$id) | .state")" = disabled ]' "$out"
run agents_enable "$id1"
check "agents_enable: enabled=true, state=active" \
    '[ "$rc" = 0 ] && [ "$(jget .enabled)" = true ] && [ "$(agents_list | jq -r --arg id "$id1" ".[] | select(.id == \$id) | .state")" = active ]' "$out"
# Истёкший доступ: срок в прошлом пишем прямо в файл (как сделал бы старый agent-add)
jq --arg id "$id2" '.agents |= map(if .id == $id then .expires = "2020-01-01T00:00:00Z" else . end)' "$AGENTS" >"$AGENTS.tmp" && mv "$AGENTS.tmp" "$AGENTS"
run agents_list
check "истёкший срок → expired=true, state=expired (включённый, но просроченный)" \
    '[ "$(jget ".[] | select(.id == \"$id2\") | .expired")" = true ] && [ "$(jget ".[] | select(.id == \"$id2\") | .state")" = expired ]' "$out"
run agents_http_status
check "agents_http_status: agents=2, agents_active=1 (истёкший не считается)" '[ "$(jget .agents)" = 2 ] && [ "$(jget .agents_active)" = 1 ]' "$out"
# ── Продление срока (agents_extend): срок нельзя было продлить вовсе ─────────
section "agents_extend: продление вместо перевыпуска"
# Длительность обязана меняться вместе со сроком: скользящее продление двигает
# срок именно на неё, и без этого владелец, продливший на неделю, получал бы
# первоначальную длительность обратно с первого же вызова агента.

exp_before=$(jq -r --arg id "$id1" '.agents[] | select(.id == $id) | .expires' "$AGENTS")
sec_before=$(jq -r --arg id "$id1" '.agents[] | select(.id == $id) | .secret' "$AGENTS")
run agents_extend "$id1"
check "agents_extend без дней → invalid_days, rc=2 (забытый аргумент не снимает срок)" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_days ] && [ "$(jq -r --arg id "$id1" ".agents[] | select(.id == \$id) | .expires" "$AGENTS")" = "$exp_before" ]' "rc=$rc code=$GA_ERR_CODE"
run agents_extend "$id1" 99999
check "agents_extend 99999 → invalid_days (предел 3650)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_days ]' "rc=$rc code=$GA_ERR_CODE"
run agents_extend "a-0000" 30
check "agents_extend несуществующему → agent_not_found" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = agent_not_found ]' "rc=$rc code=$GA_ERR_CODE"
run agents_extend "$id1" 30
check "agents_extend 30: rc=0, одна JSON-строка {id, expires, old_expires, days}, секрет и токен НЕ тронуты" \
    '[ "$rc" = 0 ] && one_line_json && [ "$(jget .id)" = "$id1" ] && [ "$(jget .days)" = 30 ] && [ "$(jget .old_expires)" = "$exp_before" ] && [ "$(jq -r --arg id "$id1" ".agents[] | select(.id == \$id) | .secret" "$AGENTS")" = "$sec_before" ]' "$out"
check "новый срок на 30 дней ПОЗЖЕ прежнего, а не «заново от сегодня»" \
    '[ "$(( $(ga_agents_iso_epoch "$(jget .expires)") - $(ga_agents_iso_epoch "$exp_before") ))" = "$((30 * 86400))" ]' "$(jget .expires) ← $exp_before"
check "срок в файле совпадает с ответом, аудит agent-extend с id и днями" \
    '[ "$(jq -r --arg id "$id1" ".agents[] | select(.id == \$id) | .expires" "$AGENTS")" = "$(jget .expires)" ] && grep -q "agent-extend" "$GOAMNEZIA_AUDIT_LOG" && grep -q "id=${id1} days=30" "$GOAMNEZIA_AUDIT_LOG"' "$(tail -n 2 "$GOAMNEZIA_AUDIT_LOG")"
# Истёкший продлевается ОТ СЕГОДНЯ, а не от прошлогодней даты — иначе остался бы истёкшим
jq --arg id "$id1" '.agents |= map(if .id == $id then .expires = "2020-01-01T00:00:00Z" else . end)' "$AGENTS" >"$AGENTS.tmp" && mv "$AGENTS.tmp" "$AGENTS"
run agents_extend "$id1" 7
check "истёкший доступ продлевается от сегодня и снова действует" \
    '[ "$rc" = 0 ] && [ "$(( $(ga_agents_iso_epoch "$(jget .expires)") - $(date -u +%s) ))" -gt "$((6 * 86400))" ] && [ "$(agents_list | jq -r --arg id "$id1" ".[] | select(.id == \$id) | .state")" = active ]' "$out"
run agents_extend "$id1" 0
check "agents_extend 0 → срок снят (бессрочно), предупреждение в stderr" \
    '[ "$rc" = 0 ] && [ -z "$(jget .expires)" ] && [ -z "$(jq -r --arg id "$id1" ".agents[] | select(.id == \$id) | .expires" "$AGENTS")" ] && grep -q "бессрочно" "$ERR"' "$out $(errs)"
run agents_extend "$id1" 10
check "бессрочный продлевается от сегодня (снова со сроком)" \
    '[ "$rc" = 0 ] && [ -n "$(jget .expires)" ] && [ "$(( $(ga_agents_iso_epoch "$(jget .expires)") - $(date -u +%s) ))" -gt "$((9 * 86400))" ]' "$out"

run agents_del "$id2"
check "agents_del: stdout {id, deleted:true}, в файле осталась одна запись, аудит agent-del" \
    '[ "$rc" = 0 ] && [ "$(jget .deleted)" = true ] && [ "$(jq -r ".agents|length" "$AGENTS")" = 1 ] && grep -q "agent-del" "$GOAMNEZIA_AUDIT_LOG"' "$out"
run agents_del "$id2"
check "повторное удаление → agent_not_found" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = agent_not_found ]' "rc=$rc code=$GA_ERR_CODE"
# Битый файл: не «пусто», а ошибка — иначе следующая запись затёрла бы доступы
cp "$AGENTS" "$AGENTS.bak"; printf '{"agents": "oops"' >"$AGENTS"
run agents_add "Тест" write
check "битый agents.json → agents_corrupt, запись не делается" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = agents_corrupt ]' "rc=$rc code=$GA_ERR_CODE"
run agents_disable "$id1"
check "битый agents.json → agents_corrupt и у agents_disable (а не agent_not_found)" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = agents_corrupt ]' "rc=$rc code=$GA_ERR_CODE"
mv "$AGENTS.bak" "$AGENTS"

# ── 7. Персональный порт в lite ──────────────────────────────────────────────
run agents_extend "$id1" 7
check "agents_extend 7 меняет И длительность, а не только дату" \
    '[ "$rc" = 0 ] && [ "$(jq -r --arg id "$id1" ".agents[]|select(.id==\$id)|.term_days" "$AGENTS")" = 7 ]' \
    "term_days=$(jq -r --arg id "$id1" ".agents[]|select(.id==\$id)|.term_days" "$AGENTS")"
run agents_extend "$id1" 0
check "agents_extend 0 снимает и срок, и длительность" \
    '[ "$rc" = 0 ] && [ "$(jq -r --arg id "$id1" ".agents[]|select(.id==\$id)|.term_days" "$AGENTS")" = 0 ] && [ -z "$(jq -r --arg id "$id1" ".agents[]|select(.id==\$id)|.expires" "$AGENTS")" ]' \
    "term=$(jq -r --arg id "$id1" ".agents[]|select(.id==\$id)|.term_days" "$AGENTS")"

section "персональный порт доступа (lite)"
run agents_add "Агент со своим портом" admin 0 "" 40100
id3=$(jget .id)
check "agents_add с портом 40100: адрес https://IP:40100/a-…/mcp, via=grant_port, port=40100" \
    '[ "$rc" = 0 ] && [[ "$(jget .url)" =~ ^https://203\.0\.113\.10:40100/a-[A-Za-z0-9_-]{43}/mcp$ ]] && [ "$(jget .via)" = grant_port ] && [ "$(jget .port)" = 40100 ]' "$out"
check "юнит: основной слушатель прежний, персональные порты открывает --extra-ports auto (в ExecStart нет --extra-ports none); DRY restart — порт открывается при старте" \
    'grep -qF -- "--port 40000 --tls" "$UNIT" && ! grep -qF -- "--extra-ports none" "$UNIT" && grep -q "DRY: systemctl restart goamnezia-mcp-http.service" "$ERR"' "$(grep ExecStart "$UNIT")"
run agents_add "Тот же порт" admin 0 "" 40100
check "второй доступ на тот же порт → port_busy" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = port_busy ]' "rc=$rc code=$GA_ERR_CODE"
run agents_http_status
check "статус: agents_with_port=1" '[ "$(jget .agents_with_port)" = 1 ]' "$out"
run agents_del "$id3"
check "удаление доступа с портом: rc=0, DRY restart юнита" '[ "$rc" = 0 ] && grep -q "DRY: systemctl restart goamnezia-mcp-http.service" "$ERR"' "$(errs)"

# ── 8. Выключение транспорта ─────────────────────────────────────────────────
section "agents_http_off"
run agents_http_off
check "agents_http_off: rc=0, статус enabled=false, юнит удалён, DRY systemctl disable --now, mcp.http_enabled=false, доступ сохранён" \
    '[ "$rc" = 0 ] && [ "$(jget .enabled)" = false ] && [ ! -e "$UNIT" ] && grep -q "DRY: systemctl disable --now goamnezia-mcp-http.service" "$ERR" && [ "$(jq -c .mcp.http_enabled "$CONFIG_FILE")" = false ] && [ "$(jq -r ".agents|length" "$AGENTS")" = 1 ]' "$(errs)"
run agents_show "$id1"
check "agents_show при выключенном транспорте: адрес есть, transport_enabled=false, reachable=false, hint про mcp-http-on" \
    '[ "$rc" = 0 ] && [ "$(jget .url)" = "$url1" ] && [ "$(jget .transport_enabled)" = false ] && [ "$(jget .reachable)" = false ] && grep -q "mcp-http-on" <<<"$(jget .hint)"' "$out"
run agents_http_off
check "повторное выключение идемпотентно: rc=0, enabled=false" '[ "$rc" = 0 ] && [ "$(jget .enabled)" = false ]' "$(errs)"

# ── 9. Lite + сайт: nginx на IP ──────────────────────────────────────────────
section "lite_site: nginx-location и адрес https://IP/a-…/mcp"
run website_enable_site_selfsigned
check "website_enable_site_selfsigned: rc=0, mode=lite_site, vhost записан" '[ "$rc" = 0 ] && [ "$(ga_config_get .mode)" = lite_site ] && [ -f "$CONF" ]' "$(errs)"
check "в vhost маршрута агентов пока нет (транспорт выключен)" '! grep -qF "^/[ar]-[A-Za-z0-9_-]{43}/" "$CONF"'
run agents_http_on
check "agents_http_on в lite_site: rc=0, listen=nginx, bind=127.0.0.1, port=1986, tls=false, via=ip, base_url=https://203.0.113.10, nginx_location=true, reachable=true" \
    '[ "$rc" = 0 ] && [ "$(jget .listen)" = nginx ] && [ "$(jget .bind)" = 127.0.0.1 ] && [ "$(jget .port)" = 1986 ] && [ "$(jget .tls)" = false ] && [ "$(jget .via)" = ip ] && [ "$(jget .base_url)" = "https://203.0.113.10" ] && [ "$(jget .nginx_location)" = true ] && [ "$(jget .reachable)" = true ]' "$out"
# Регулярка обязана быть в кавычках: nginx читает `{43}` как конец блока и
# отвергает конфиг («unknown directive "43}/"») — транспорт не включался вовсе.
check "vhost: location ~ \"^/[ar]-[A-Za-z0-9_-]{43}/\" → proxy_pass 127.0.0.1:1986, access_log off; config site.mcp_location=true" \
    'grep -qF "location ~ \"^/[ar]-[A-Za-z0-9_-]{43}/\" {" "$CONF" && grep -qF "proxy_pass http://127.0.0.1:1986;" "$CONF" && grep -q "access_log off" "$CONF" && [ "$(jq -c .site.mcp_location "$CONFIG_FILE")" = true ]' "$(grep -n "\[ar\]-" "$CONF")"
check "юнит: --bind 127.0.0.1 --port 1986 без --tls (TLS терминирует nginx)" \
    'grep -qF "ExecStart=/usr/bin/env python3 $MCP_DIR/server.py --http --bind 127.0.0.1 --port 1986" "$UNIT" && ! grep -qF -- "--tls" "$UNIT"' "$(grep ExecStart "$UNIT")"
check "статус: отпечаток самоподписанного сертификата совпадает с website_cert_fingerprint" '[ "$(jget .cert_fingerprint_sha256)" = "$(website_cert_fingerprint)" ]' "$out"
run agents_add "Cloud-агент" write
check "agents_add в lite_site: адрес https://203.0.113.10/a-…/mcp (без порта), via=ip, отпечаток в карточке, cert_hint" \
    '[ "$rc" = 0 ] && [[ "$(jget .url)" =~ ^https://203\.0\.113\.10/a-[A-Za-z0-9_-]{43}/mcp$ ]] && [ "$(jget .via)" = ip ] && [ "$(jget .cert_fingerprint_sha256)" = "$(website_cert_fingerprint)" ] && [ -n "$(jget .cert_hint)" ]' "$out"
id4=$(jget .id)
run agents_show "$id1"
check "старый доступ (выдан в lite на порту) теперь показывает адрес по режиму: https://IP/a-…/mcp" \
    '[[ "$(jget .url)" =~ ^https://203\.0\.113\.10/a-[A-Za-z0-9_-]{43}/mcp$ ]]' "$out"
# Адрес выводится из ГОТОВНОСТИ ДОМЕНА, а не из названия режима (docs/DEFECTS.md
# §1.1). Тот же lite_site, но сайт уже переехал на домен с Let's Encrypt: nginx
# отвечает по server_name <домен>, и ссылка на IP с отпечатком запасного
# самоподписанного там просто не работает.
ga_config_set .domain agent-domain.example >/dev/null 2>&1
ga_config_set .site.cert letsencrypt >/dev/null 2>&1
run agents_show "$id4"
check "lite_site НА ДОМЕНЕ: адрес https://agent-domain.example/a-…/mcp, via=domain, отпечатка запасного сертификата нет" \
    '[ "$rc" = 0 ] && [[ "$(jget .url)" =~ ^https://agent-domain\.example/a-[A-Za-z0-9_-]{43}/mcp$ ]] && [ "$(jget .via)" = domain ] && [ "$(jget .cert_fingerprint_sha256)" = "" ]' "$out"
run agents_http_status
check "статус там же: via=domain, base_url=https://agent-domain.example, отпечаток пуст" \
    '[ "$(jget .via)" = domain ] && [ "$(jget .base_url)" = "https://agent-domain.example" ] && [ "$(jget .cert_fingerprint_sha256)" = "" ]' "$out"
ga_config_set .site.cert selfsigned >/dev/null 2>&1
ga_config_set .domain "" >/dev/null 2>&1
run agents_show "$id4"
check "сайт вернулся на запасной сертификат — адрес снова на IP с отпечатком" \
    '[[ "$(jget .url)" =~ ^https://203\.0\.113\.10/a- ]] && [ "$(jget .via)" = ip ] && [ -n "$(jget .cert_fingerprint_sha256)" ]' "$out"
run agents_http_on 40500
check "порт в режиме с сайтом игнорируется с предупреждением: rc=0, listen=nginx, public_port не изменился" \
    '[ "$rc" = 0 ] && [ "$(jget .listen)" = nginx ] && [ "$(jget .public_port)" = 40000 ] && grep -q "40500\|lite_site" "$ERR"' "$(errs)"

# ── 10. Pro: домен ───────────────────────────────────────────────────────────
section "pro: адрес https://домен/a-…/mcp"
run website_enable_pro example.com
check "website_enable_pro: rc=0, mode=pro; маршрут агентов сохранился в vhost (транспорт включён)" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .mode)" = pro ] && grep -qF "^/[ar]-[A-Za-z0-9_-]{43}/" "$CONF"' "$(errs)"
check "смена режима lite_site → pro не перезапускала юнит (слушатель тот же: петля 1986)" \
    '! grep -q "DRY: systemctl restart goamnezia-mcp-http.service" "$ERR" && grep -qF -- "--bind 127.0.0.1 --port 1986" "$UNIT"' "$(grep DRY "$ERR")"
run agents_show "$id4"
check "agents_show в pro: https://example.com/a-…/mcp, via=domain, отпечатка нет, cert_hint нет" \
    '[ "$rc" = 0 ] && [[ "$(jget .url)" =~ ^https://example\.com/a-[A-Za-z0-9_-]{43}/mcp$ ]] && [ "$(jget .via)" = domain ] && [ "$(jget .cert_fingerprint_sha256)" = "" ] && [ "$(jget .cert_hint)" = null ]' "$out"
run agents_http_status
check "статус в pro: listen=nginx, via=domain, base_url=https://example.com, cert_fingerprint пуст, domain=example.com" \
    '[ "$(jget .listen)" = nginx ] && [ "$(jget .via)" = domain ] && [ "$(jget .base_url)" = "https://example.com" ] && [ "$(jget .cert_fingerprint_sha256)" = "" ] && [ "$(jget .domain)" = example.com ]' "$out"
run agents_http_off
check "agents_http_off в pro: маршрут убран из vhost, якорь остался, site.mcp_location=false, юнит удалён" \
    '[ "$rc" = 0 ] && ! grep -qF "^/[ar]-[A-Za-z0-9_-]{43}/" "$CONF" && grep -qF "# GOAMNEZIA_MCP_LOCATION" "$CONF" && [ "$(jq -c .site.mcp_location "$CONFIG_FILE")" = false ] && [ ! -e "$UNIT" ]' "$(errs)"
run agents_http_on
check "agents_http_on снова в pro: rc=0, маршрут вернулся, юнит записан" '[ "$rc" = 0 ] && grep -qF "^/[ar]-[A-Za-z0-9_-]{43}/" "$CONF" && [ -f "$UNIT" ]' "$(errs)"

# Регрессия стенда: ExecStart юнита указывал на рабочую копию /opt/goamnezia-mcp,
# которую создаёт только интерактивный install.sh. После установки целиком через
# мост её нет, и служба падала в цикле «can't open file …/server.py», а
# mcp-http-on рапортовал успех.
REF_MCP="$GOAMNEZIA_DIR/goamnezia-mcp"; mkdir -p "$REF_MCP"
printf '#!/usr/bin/env python3\n' >"$REF_MCP/server.py"
NOWORK="$TMP/mcp-absent"
out=$(MCP_DIR="$NOWORK" ga_agents_server_py)
check "без рабочей копии путь к серверу берётся из эталона (юнит запустится)" \
    '[ "$out" = "$REF_MCP/server.py" ]' "out=$out"
WORK_MCP="$TMP/mcpwork"; mkdir -p "$WORK_MCP"; printf '#!/usr/bin/env python3\n' >"$WORK_MCP/server.py"
out=$(MCP_DIR="$WORK_MCP" ga_agents_server_py)
check "рабочая копия, когда она есть, остаётся в приоритете" \
    '[ "$out" = "$WORK_MCP/server.py" ]' "out=$out"
rm -rf "$REF_MCP" "$WORK_MCP"
# Смена режима pro → lite при включённом транспорте: слушать нечего (публичный
# порт 40000 остался в конфиге → юнит уходит на него с TLS)
run website_disable_pro lite
check "website_disable_pro lite: rc=0, mode=lite; юнит пересобран на публичный порт 40000 с --tls (DRY restart)" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .mode)" = lite ] && grep -qF -- "--bind 0.0.0.0 --port 40000 --tls" "$UNIT" && grep -q "DRY: systemctl restart goamnezia-mcp-http.service" "$ERR"' "$(errs)"
ga_config_set .mcp.public_port 0 >/dev/null 2>&1
run website_enable_site_selfsigned
run website_disable_site
check "lite_site → lite без публичного порта при включённом транспорте: rc=0, служба остановлена (DRY disable --now), предупреждение, флаг http_enabled остался" \
    '[ "$rc" = 0 ] && grep -q "DRY: systemctl disable --now goamnezia-mcp-http.service" "$ERR" && [ "$(jq -c .mcp.http_enabled "$CONFIG_FILE")" = true ]' "$(errs)"
run agents_http_status
# Ровно то место, где продукт врал сам себе: флаг в конфиге остался от Pro, а
# слушать транспорту негде — и статус рапортовал «включено» (DEFECTS §1.8).
# Теперь enabled — факт, configured — запись, reason называет причину, а
# nginx_location не выдаёт след прежнего режима за живой маршрут.
check "статус после этого: enabled=false — включать нечего, listen=none, reachable=false" \
    '[ "$(jget .enabled)" = false ] && [ "$(jget .listen)" = none ] && [ "$(jget .reachable)" = false ]' "$out"
check "и говорит почему: configured=true (запись в конфиге жива), available=false, reason=no_site, hint не пуст" \
    '[ "$(jget .configured)" = true ] && [ "$(jget .available)" = false ] && [ "$(jget .reason)" = no_site ] && [ -n "$(jget .hint)" ]' "$out"
check "nginx_location=false: vhost'а в lite нет, флаг site.mcp_location — след прежнего режима, а не факт" \
    '[ "$(jget .nginx_location)" = false ]' "$out mcp_location=$(ga_config_get .site.mcp_location)"
# Перевыпуск там, где слушать негде: прежде запись переписывалась (секрет, у переходника —
# привязка, у rolling — цепочка), а карточка потом падала на needs_site, и человек видел
# ошибку при уже стёртом доступе. Теперь адрес проверяется ДО записи, как при выдаче.
rid_any=$(jq -r '.agents[0].id // ""' "$AGENTS" 2>/dev/null)
if [ -n "$rid_any" ]; then
    sec_before_rot=$(jq -r --arg id "$rid_any" '.agents[] | select(.id == $id) | .secret' "$AGENTS")
    run agents_rotate "$rid_any"
    check "agents_rotate без места для адреса → needs_site, запись не тронута (секрет прежний)" \
        '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = needs_site ] && [ "$(jq -r --arg id "$rid_any" ".agents[] | select(.id == \$id) | .secret" "$AGENTS")" = "$sec_before_rot" ]' \
        "rc=$rc code=$GA_ERR_CODE"
else
    skipped "перевыпуск без места для адреса" "к этому месту доступов не осталось"
fi
run agents_http_off
check "финал: agents_http_off в lite: rc=0" '[ "$rc" = 0 ]' "$(errs)"

# ── SR-10: мост внутри службы транспорта не убивает себя на полпути ─────────────
# Агент по HTTP зовёт mode_set / mcp-http-*: мост — процесс в cgroup службы goamnezia-mcp-http, и её
# остановка или перезапуск обрывали его посередине (режим записан, страницы не отозваны, ответа нет).
section "SR-10: остановка и перезапуск службы транспорта — после выхода моста"
printf '0::/system.slice/goamnezia-mcp-http.service\n' >"$TMP/cgroup.mcp"
GA_PROC_CGROUP="$TMP/cgroup.mcp" run _agents_unit_ctl restart
check "SR-10 изнутри службы перезапуск отложен до выхода моста (временная служба ждёт его pid), не выполнен сразу" \
    '[ "$rc" = 0 ] && grep -q "DRY: systemd-run --unit=goamnezia-mcp-http-ctl-.*after bridge pid [0-9]* exits, systemctl restart goamnezia-mcp-http.service" "$ERR" && ! grep -q "DRY: systemctl restart" "$ERR"' "$(errs)"
GA_PROC_CGROUP="$TMP/cgroup.mcp" run _agents_unit_ctl disable --now
check "SR-10 disable --now изнутри: ссылки юнита сняты сразу, остановка — после выхода моста" \
    'grep -q "DRY: systemctl disable goamnezia-mcp-http.service" "$ERR" && grep -q "after bridge pid [0-9]* exits, systemctl stop goamnezia-mcp-http.service" "$ERR" && ! grep -q "disable --now" "$ERR"' "$(errs)"
GA_PROC_CGROUP="$TMP/cgroup.mcp" run _agents_unit_ctl enable --now
check "SR-10 enable --now изнутри: включение сразу, запуск не нужен (служба и так работает — мы в ней)" \
    'grep -q "DRY: systemctl enable goamnezia-mcp-http.service" "$ERR" && ! grep -q "systemd-run" "$ERR"' "$(errs)"
printf '0::/system.slice/goamnezia-bot.service\n' >"$TMP/cgroup.bot"
GA_PROC_CGROUP="$TMP/cgroup.bot" run _agents_unit_ctl restart
check "SR-10 из другой службы (бот) — как прежде, сразу" 'grep -q "DRY: systemctl restart goamnezia-mcp-http.service" "$ERR" && ! grep -q systemd-run "$ERR"' "$(errs)"
check "SR-10 выключение транспорта изнутри не отвечает «служба осталась» на отложенную остановку" \
    'grep -q "GA_AGENTS_UNIT_DEFERRED\" = 1 \] && return 0" "$ROOT/lib/agents.sh"'
sr10_pages=$(grep -n "_pages_mode_off_locked || true" "$ROOT/lib/website.sh" | cut -d: -f1 | tr '\n' ' ')
sr10_modes=$(grep -n '_ga_site_set_mode lite "" || return 1' "$ROOT/lib/website.sh" | cut -d: -f1 | tr '\n' ' ')
check "SR-10 уход в «Только VPN» (оба пути): страницы отзываются ДО записи режима" \
    'set -- $sr10_pages; p1=$1 p2=$2; set -- $sr10_modes; [ "$p1" -lt "$1" ] && [ "$p2" -lt "$2" ]' "pages=[$sr10_pages] modes=[$sr10_modes]"

# ── 11. Гигиена: секреты, stdout, словари ────────────────────────────────────
section "секреты, stdout, словари"
# Ни один секрет и ни один токен не должны оказаться в журналах: там их не
# ждут и оттуда их не вычистить — журналы уезжают в бэкапы и в чужие руки.
all_secrets=$(jq -r '.agents[] | .secret, (.token // empty)' "$AGENTS")
leak=0
for sct in $all_secrets $secret1 ${token1:-} ${token2:-}; do
    [ -n "$sct" ] || continue
    grep -qF -e "$sct" "$ALL_ERR" "$GOAMNEZIA_AUDIT_LOG" "$GOAMNEZIA_LOG_FILE" 2>/dev/null && leak=1
done
check "ни один секрет и ни один токен не попали в stderr, аудит или лог" '[ "$leak" = 0 ]'
# Аудит — JSON по строке (CONTRACT §7.1). Прежняя проверка искала поля через
# табуляцию и выходила из eval через exit 1: после смены формата она молча
# обрывала весь прогон ДО итога, и сбой выглядел как «проверки кончились».
audit_miss=""
for a in agent-add agent-show agent-rotate agent-harden agent-pin agent-enable agent-disable agent-del mcp-http-on mcp-http-off; do
    jq -R -e -n --arg a "$a" '[inputs | fromjson? // empty | select(.action == $a)] | length > 0' "$GOAMNEZIA_AUDIT_LOG" >/dev/null 2>&1 \
        || audit_miss="$audit_miss $a"
done
audit_noid=$(jq -R -r -n '[inputs | fromjson? // empty
    | select((.action // "") | test("^agent-(add|show|rotate|harden|pin|enable|disable|del)$"))
    | select(((.details // "") | test("id=a-")) | not) | .action] | unique | join(",")' "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null)
check "аудит: agent-add, agent-show, agent-rotate, agent-harden, agent-pin, agent-enable, agent-disable, agent-del, mcp-http-on, mcp-http-off — все с id" \
    '[ -z "$audit_miss" ] && [ -z "$audit_noid" ] && [ -s "$GOAMNEZIA_AUDIT_LOG" ]' "нет:${audit_miss:- —}; без id: ${audit_noid:-—}"
check "проектные каталоги не тронуты: /etc/systemd/system/goamnezia-mcp-http.service и /opt/goamnezia/agents.json не создавались и не менялись" \
    '[ "$(host_sig /etc/systemd/system/goamnezia-mcp-http.service)" = "$HOST_MCP_UNIT_SIG" ] && [ "$(host_sig /opt/goamnezia/agents.json)" = "$HOST_AGENTS_SIG" ]'
miss=""
for k in $(grep -oE '\$\((t|tf) [A-Za-z_0-9]+' "$ROOT/lib/agents.sh" | awk '{print $2}' | sort -u); do
    [ -n "${I18N[$k]:-}" ] || miss="$miss $k"
done
check "все ключи t/tf из lib/agents.sh есть в словаре ru" '[ -z "$miss" ]' "$miss"
load_language en >/dev/null 2>&1
miss=""
for k in $(grep -oE '\$\((t|tf) [A-Za-z_0-9]+' "$ROOT/lib/agents.sh" | awk '{print $2}' | sort -u); do
    [ -n "${I18N[$k]:-}" ] || miss="$miss $k"
done
check "все ключи t/tf из lib/agents.sh есть в словаре en" '[ -z "$miss" ]' "$miss"
load_language ru >/dev/null 2>&1
# Строки продления были написаны и не запрашивались НИ ОДНОЙ строкой кода: пункта
# в SSH-меню не существовало, хотя тревоги бота ведут человека именно туда
# («Настройки → Доступы агентов → карточка доступа → Продлить»). Существования
# ключа в словаре мало — проверяем, что экран его спрашивает.
miss=""
for k in ag_item_extend ag_ask_extend_days ag_extend_ask ag_extend_note; do
    grep -qE "\\\$\((t|tf) $k\b" "$ROOT/install.sh" || miss="$miss $k"
done
check "строки продления запрашиваются экраном install.sh, а не лежат мёртвым грузом" '[ -z "$miss" ]' "$miss"
miss=""
for lang in ru en; do
    load_language "$lang" >/dev/null 2>&1
    for k in ag_item_extend ag_ask_extend_days ag_extend_ask ag_extend_note; do
        [ -n "${I18N[$k]:-}" ] || miss="$miss $lang:$k"
    done
done
load_language ru >/dev/null 2>&1
check "строки продления есть и в ru, и в en (паритет)" '[ -z "$miss" ]' "$miss"
check "TAIL-9: за весь сухой прогон настоящий ss не зван ни разу (журнал ловушки пуст)" '[ ! -s "$SS_CALLS" ]' "$(head -n 5 "$SS_CALLS")"

# ── Итог ─────────────────────────────────────────────────────────────────────
DONE=1
printf '\n══ ИТОГ: пройдено %d, провалено %d, пропущено %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
