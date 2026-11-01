#!/bin/bash
# Живой обход действий моста и MCP на сервере. Чтение — все действия; изменения — только над тестовым
# пользователем (ga-e2e), тестовым профилем (awg1) и тестовым доступом агента, с возвратом прежних
# значений. Пользователей, режим, сайт, панель на домене и доступы владельца не трогает.
# Запуск: ssh root@сервер 'bash -s' < live_actions.sh
set -uo pipefail
export DOCKER_API_VERSION="${DOCKER_API_VERSION:-1.43}"
cd /opt/goamnezia || exit 2
U="${LIVE_USER:-ga-e2e}"
P="${LIVE_PROFILE:-awg1}"
pass=0; fail=0; LAST=""
ERRF=$(mktemp)
trap 'rm -f "$ERRF"' EXIT

bridge() { local a="$1"; shift; GOAMNEZIA_WHO=live bash install.sh "--action=$a" --json "$@" 2>"$ERRF" | tail -1; }
ok()  { pass=$((pass + 1)); printf '✓ %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf '✗ %s\n' "$1"; }
jf()  { jq -r "$1" <<<"$LAST" 2>/dev/null; }

# chk <описание> <ожидание: success | код отказа> <действие> [аргументы…]
chk() {
    local what="$1" want="$2" a="$3" t0 ms st code msg
    shift 3
    t0=$(date +%s%N)
    LAST=$(bridge "$a" "$@")
    ms=$(( ($(date +%s%N) - t0) / 1000000 ))
    st=$(jq -r '.status // "?"' <<<"$LAST" 2>/dev/null) || st="нет JSON"
    code=$(jq -r '.code // ""' <<<"$LAST" 2>/dev/null)
    msg=$(jq -r '.message // ""' <<<"$LAST" 2>/dev/null)
    if { [ "$want" = success ] && [ "$st" = success ]; } || { [ "$want" != success ] && [ "$code" = "$want" ]; }; then
        ok "$(printf '%-50s %6d мс' "$what" "$ms")"
    else
        bad "$(printf '%-50s %6d мс  status=%s code=%s: %s' "$what" "$ms" "$st" "$code" "${msg:0:240}")"
        [ -n "$LAST" ] || tail -n 3 "$ERRF" | sed 's/^/    /'
    fi
}
# is <описание> <команда…> — проверка по последнему ответу (функции ниже)
is() { local what="$1"; shift; if "$@" >/dev/null 2>&1; then ok "  $what"; else bad "  $what: $(jq -c . <<<"$LAST" 2>/dev/null | head -c 300)"; fi; }

c_status()    { [ "$(jf .mode)" = "$(jq -r .mode config.json)" ] && [ "$(jf '[.profiles[]?] | length')" -ge 3 ]; }
c_edge_one()  { [ "$(jf .count)" -ge 1 ] && [ "$(jf '.ips[0].verdict')" != null ]; }
c_vpn()       { [[ "$(jf '.vpn // empty' | head -c 6)" == vpn:// ]]; }
c_qr()        { [[ "$(jf '.qr_conf_png_b64 // empty' | head -c 8)" == iVBORw0K ]]; }
c_site_ok()   { [ "$(jf .verdict)" = ok ]; }
c_disabled()  { [ "$(jf .user.enabled)" = false ] && [ "$(jf '[.user.peers[] | select(.active == true)] | length')" = 0 ]; }
c_enabled()   { [ "$(jf .user.enabled)" = true ] && [ "$(jf '[.user.peers[] | select(.active != true)] | length')" = 0 ]; }
c_noresend()  { [ "$(jf .resend_required)" = false ]; }
c_share()     { [ -n "$SID" ]; }
c_share_seen(){ grep -qF "$SID" <<<"$LAST"; }
c_share_gone(){ ! grep -qF "\"$SID\"" <<<"$LAST" || [ "$(jq -r --arg id "$SID" '[.. | objects | select(.id? == $id) | .state? // .status? // empty] | join(",")' <<<"$LAST")" != active ]; }
c_noreissue() { [ "$(jf .reissue_required)" = false ]; }
c_running()   { [ "$(jq -r --arg p "$P" '[.profiles[]? | select(.profile == $p) | .state][0]' <<<"$LAST")" = running ]; }
c_port()      { [ "$(jq -r --arg p "$P" '.profiles[$p].port' config.json)" = "$1" ]; }

echo "режим $(jq -r .mode config.json), домен «$(jq -r '.domain // ""' config.json)», версия $(jq -r .version config.json)"
echo "── чтение"
chk "status" success status;                                   is "режим и профили на месте" c_status
chk "profiles-status" success profiles-status
chk "quality (diagnose)" success quality
chk "traffic" success traffic
chk "edge-status" success edge-status;                         is "строка на адрес с вердиктом" c_edge_one
chk "logs-status" success logs-status
chk "logs-tail bot" success logs-tail --component=bot --lines=20
chk "logs-tail $P (у контейнеров журнала нет)" logs_driver_unreadable logs-tail "--component=$P" --lines=20
chk "profile-params $P" success profile-params "--profile=$P"
chk "user-list" success user-list
chk "user-show $U" success user-show "--name=$U"
chk "user-key $U awg2 vpn://" success user-key "--name=$U" --profile=awg2 --format=vpn;   is "ссылка vpn://" c_vpn
chk "user-key $U awg3 QR" success user-key "--name=$U" --profile=awg3 --format=qr-conf;  is "QR — PNG" c_qr
chk "backup-list" success backup-list
chk "backup-last" success backup-last
chk "import-scan" success import-scan
chk "upstream-check (сеть)" success upstream-check
if [ "$(jq -r .mode config.json)" != lite ]; then
    chk "site-check (сеть)" success site-check;               is "вердикт сайта ok" c_site_ok
fi
chk "miniapp-status" success miniapp-status
chk "preflight (режимы)" success preflight
chk "agent-list" success agent-list
chk "mcp-http-status" success mcp-http-status
chk "share-list" success share-list

echo "── изменения над пользователем $U"
chk "user-rename" success user-rename "--name=$U" "--label=E2E проверка 2"
chk "user-rename обратно" success user-rename "--name=$U" "--label=E2E проверка"
chk "user-quota 5 ГБ" success user-quota "--name=$U" --quota-gb=5
chk "user-quota снять" success user-quota "--name=$U" --quota-gb=0
chk "user-expiry 30 дней" success user-expiry "--name=$U" --days=30
chk "user-expiry бессрочно" success user-expiry "--name=$U" --days=0
chk "user-disable" success user-disable "--name=$U"
chk "user-show после выключения" success user-show "--name=$U";  is "выключен, пиры сняты из конфигов" c_disabled
chk "user-enable" success user-enable "--name=$U"
chk "user-show после включения" success user-show "--name=$U";   is "включён, все пиры в конфигах" c_enabled
IP=$(jq -r .server_ip config.json)
chk "user-set-address: адрес = общий" success user-set-address "--name=$U" "--ip=$IP";  is "тот же адрес: отправлять заново не нужно" c_noresend
chk "user-set-address: чужой адрес" address_not_here user-set-address "--name=$U" --ip=1.2.3.4
chk "user-set-address: вернуть общий" success user-set-address "--name=$U" --ip=
SID=""
chk "share-create $U awg2" success share-create "--name=$U" --profile=awg2 --format=conf
SID=$(jf '.id // .share.id // empty');                        is "у раздачи есть id" c_share
if [ -n "$SID" ]; then
    chk "share-list: раздача видна" success share-list;       is "id в списке" c_share_seen
    chk "share-revoke" success share-revoke "--id=$SID"
    chk "share-list после снятия" success share-list;         is "раздача снята" c_share_gone
fi
chk "user-del-peer $U awg3" success user-del-peer "--name=$U" --profile=awg3
chk "user-add-peer $U awg3" success user-add-peer "--name=$U" --profile=awg3
chk "user-reissue-peer $U awg2" success user-reissue-peer "--name=$U" --profile=awg2

echo "── тестовый профиль $P"
PORT0=$(jq -r --arg p "$P" '.profiles[$p].port' config.json)
MTU0=$(jq -r --arg p "$P" '.profiles[$p].params.MTU // "1280"' config.json)
JC0=$(jq -r --arg p "$P" '.profiles[$p].params.Jc // empty' config.json)
chk "profile-mtu 1376" success profile-mtu "--profile=$P" --mtu=1376
chk "profile-mtu обратно $MTU0" success profile-mtu "--profile=$P" "--mtu=$MTU0"
if [ -n "$JC0" ]; then
    chk "profile-set-param Jc=7 (клиентский)" success profile-set-param "--profile=$P" --key=Jc --value=7
    is "клиентский параметр: перевыпуск не нужен" c_noreissue
    chk "profile-set-param Jc обратно $JC0" success profile-set-param "--profile=$P" --key=Jc "--value=$JC0"
fi
NEWPORT=$((PORT0 + 1))
chk "profile-port $P → $NEWPORT" success profile-port "--profile=$P" "--port=$NEWPORT";   is "порт в конфиге $NEWPORT" c_port "$NEWPORT"
chk "profile-port $P обратно $PORT0" success profile-port "--profile=$P" "--port=$PORT0"; is "порт в конфиге $PORT0" c_port "$PORT0"
chk "profile-restart $P" success profile-restart "--profile=$P"
sleep 3
chk "profiles-status после рестарта" success profiles-status;   is "$P работает" c_running

echo "── таймеры и копии"
chk "watch-tick" success watch-tick
chk "quota-tick" success quota-tick
chk "stats-tick" success stats-tick
chk "share-tick" success share-tick
chk "backup-create" success backup-create
D1=$(jq -r '.client_dns[0] // "1.1.1.1"' config.json)
D2=$(jq -r '.client_dns[1] // ""' config.json)
chk "dns-set 9.9.9.9" success dns-set --dns1=9.9.9.9 --dns2=149.112.112.112
if [ -n "$D2" ]; then chk "dns-set обратно" success dns-set "--dns1=$D1" "--dns2=$D2"; else chk "dns-set обратно" success dns-set "--dns1=$D1"; fi

echo "── доступ агента (тестовый)"
chk "agent-add e2e-live read 1 день" success agent-add --label=e2e-live --scope=read --days=1 --mode=static
AID=$(jf '.id // empty')
if [ -n "$AID" ]; then
    chk "agent-show" success agent-show "--id=$AID"
    chk "agent-extend 2 дня" success agent-extend "--id=$AID" --days=2
    chk "agent-disable" success agent-disable "--id=$AID"
    chk "agent-enable" success agent-enable "--id=$AID"
    chk "agent-del" success agent-del "--id=$AID"
fi

echo "── MCP (stdio)"
MCP=/opt/goamnezia-mcp/server.py
[ -f "$MCP" ] || MCP=/opt/goamnezia/goamnezia-mcp/server.py
MCP_OUT=$(printf '%s\n' \
    '{"jsonrpc":"2.0","id":"i","method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"live","version":"1"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"edge_status","arguments":{}}}' \
    "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"user_show\",\"arguments\":{\"name\":\"$U\"}}}" \
    "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"user_set_address\",\"arguments\":{\"name\":\"$U\",\"ip\":\"\"}}}" \
    '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"server_status","arguments":{}}}' \
    | GOAMNEZIA_ACTION_BIN=/opt/goamnezia/install.sh GOAMNEZIA_ACTIONS_JSON=/opt/goamnezia/lib/actions.json timeout 180 python3 "$MCP" 2>"$ERRF")
m_tools() { jq -e -s 'map(select(.id == 1))[0].result.tools | map(.name) | (index("edge_status") != null and index("user_set_address") != null)' <<<"$MCP_OUT"; }
m_call()  { jq -e -s --argjson i "$1" 'map(select(.id == $i))[0].result.isError == false' <<<"$MCP_OUT"; }
LAST="$MCP_OUT"
is "MCP tools/list: edge_status и user_set_address опубликованы" m_tools
for i in 2 3 4 5; do is "MCP вызов $i без ошибки" m_call "$i"; done

echo "── службы"
for s in goamnezia-admin goamnezia-bot nginx goamnezia-watch.timer goamnezia-quota.timer goamnezia-stats.timer; do
    st=$(systemctl is-active "$s" 2>/dev/null)
    if [ "$st" = active ]; then ok "$s: active"; else bad "$s: $st"; fi
done
n=$(journalctl -u goamnezia-bot -u goamnezia-admin --since "-30 min" --no-pager 2>/dev/null | grep -ciE "traceback|exception")
if [ "${n:-0}" = 0 ]; then ok "журналы бота и панели за 30 минут без исключений"; else bad "журналы бота и панели: исключений $n"; fi

echo "ИТОГ живого обхода: пройдено $pass, провалено $fail"
