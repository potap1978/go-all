#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# test_engine.sh — контракт диспетчера движков и рендера конфига dnsdist.
#
# Работает БЕЗ root, без сети, без Ubuntu и без установленного dnsdist:
# проверяется чистая логика. Всё, что требует живого сервера
# (--check-config, systemd, apt), намеренно не трогается.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

export GODNS_DIR="$WORK"
export GODNS_ETC="$WORK/etc"
export GODNS_CONFIG="$WORK/config.json"
export GODNS_CERT_DIR="$WORK/etc/cert"
export GODNS_LIB_DIR="$REPO/lib"

. "$REPO/lib/common.sh"
. "$REPO/lib/engine.sh"

fail=0
ok()   { printf '  [ OK ] %s\n' "$1"; }
bad()  { printf '  [FAIL] %s\n' "$1"; fail=$((fail+1)); }

echo "== 0. загрузка движка =="
engine_load dnsdist || { bad "engine_load"; exit 1; }
ok "engine_load dnsdist  → $(engine_name 2>/dev/null || true)"

echo
echo "== 1. возможности =="
engine_cap_list | sed 's/^/     /'
[ "$(engine_cap can_enforce_doh_path)" = "yes" ] && ok "can_enforce_doh_path=yes" || bad "can_enforce_doh_path"
[ "$(engine_cap can_true_roundrobin)"  = "yes" ] && ok "can_true_roundrobin=yes"  || bad "can_true_roundrobin"
[ "$(engine_cap needs_third_party_repo)" = "yes" ] && ok "needs_third_party_repo=yes" || bad "needs_third_party_repo"
# ГРАБЛИ: здесь стояло «can_doq обязан быть no» — утверждение верное
# только там, где dnsdist НЕ установлен, то есть на машине разработчика.
# На цели dnsdist 2.0.8 умеет dns-over-quic, возможность честно «yes», и
# тест валился на исправном сервере. Проверяем не константу, а СОГЛАСИЕ
# возможности с тем, что говорит установленный бинарник.
_doq_bin=no
if command -v dnsdist >/dev/null 2>&1 && dnsdist --version 2>&1 | grep -q 'dns-over-quic'; then
    _doq_bin=yes
fi
_doq_cap=$(engine_cap can_doq 2>/dev/null) || _doq_cap=no
if [ "$_doq_cap" = "$_doq_bin" ]; then
    ok "can_doq=$_doq_cap — совпадает с установленным бинарником"
else
    bad "can_doq=$_doq_cap, а бинарник говорит $_doq_bin"
fi
v=$(engine_cap nosuchcap 2>/dev/null) || true
[ "$v" = "no" ] && ok "неизвестная возможность → no + предупреждение" || bad "неизвестная возможность"

echo
echo "== 2. диспетчер =="
GODNS_ENGINE_SAVE="$GODNS_ENGINE"
GODNS_ENGINE="nosuch"
rc=0; engine_status >/dev/null 2>&1 || rc=$?
[ "$rc" = "127" ] && ok "диспетчер: нет реализации → rc 127" || bad "диспетчер rc=$rc"
GODNS_ENGINE="$GODNS_ENGINE_SAVE"

echo
echo "== 3. lua-экранирование =="
[ "$(dnsdist_lua_q "abc")" = "'abc'" ] && ok "простая строка" || bad "простая строка"
[ "$(dnsdist_lua_q "a'b")" = "'a\\'b'" ] && ok "кавычка экранирована" || bad "кавычка: $(dnsdist_lua_q "a'b")"
[ "$(dnsdist_lua_q 'a\b')" = "'a\\\\b'" ] && ok "слэш экранирован" || bad "слэш: $(dnsdist_lua_q 'a\b')"
[ "$(dnsdist_lua_q "$(printf 'a\nb')")" = "'ab'" ] && ok "перевод строки выброшен" || bad "перевод строки"

echo
echo "== 4. адреса =="
[ "$(dnsdist_addr 1.1.1.1 853)" = "1.1.1.1:853" ] && ok "v4" || bad "v4"
[ "$(dnsdist_addr 2606:4700:4700::1111 853)" = "[2606:4700:4700::1111]:853" ] && ok "v6 в скобках" || bad "v6"
[ "$(dnsdist_addr 2a09:: 853)" = "[2a09::]:853" ] && ok "v6 короткий" || bad "v6 короткий"

echo
echo "== 5. разбор апстримов =="
export GODNS_UPSTREAMS="cloudflare|dot|1.1.1.1|853|one.one.one.one|;quad9|dot|9.9.9.9||dns.quad9.net;google|doh|8.8.8.8|443|dns.google|/dns-query;cf6|dot|2606:4700:4700::1111||one.one.one.one;controld|doh|76.76.2.0||freedns.controld.com|/p0"
got=$(dnsdist_upstreams_parse 2>/dev/null)
echo "$got" | sed 's/^/     /'
[ "$(printf '%s\n' "$got" | wc -l | tr -d ' ')" = "5" ] && ok "5 записей" || bad "число записей"
printf '%s\n' "$got" | grep -q $'quad9\tdot\t9.9.9.9:853\tdns.quad9.net\t' && ok "порт dot по умолчанию 853" || bad "порт dot"
printf '%s\n' "$got" | grep -q $'cf6\tdot\t\[2606:4700:4700::1111\]:853' && ok "v6 апстрим" || bad "v6 апстрим"
printf '%s\n' "$got" | grep -q $'controld\tdoh\t76.76.2.0:443\tfreedns.controld.com\t/p0' && ok "doh: порт 443 + свой путь" || bad "doh controld"

echo
echo "-- мусор отбрасывается, а не ломает конфиг --"
export GODNS_UPSTREAMS="bad|quic|1.1.1.1|53|x;evil|dot|1.1.1.1'); os.execute('rm -rf /|853|x;noport|dot|1.1.1.1|99999|x;named|dot|dns.google|853|dns.google;good|dot|9.9.9.9|853|dns.quad9.net"
got=$(dnsdist_upstreams_parse 2>/dev/null)
[ "$(printf '%s\n' "$got" | wc -l | tr -d ' ')" = "1" ] && ok "остался только валидный" || bad "фильтрация: [$got]"
printf '%s\n' "$got" | grep -q '^good' && ok "good уцелел" || bad "good"
# Контракт «host — литеральный IP» раньше держался только на каталоге:
# регэксп пропускал имя, и dnsdist получил бы address='dns.google:853'.
printf '%s\n' "$got" | grep -q '^named' && bad "имя вместо IP прошло в записи" || ok "имя вместо IP-литерала отвергнуто"

echo
echo "-- свой обычный апстрим (plain) --"
export GODNS_UPSTREAMS="own|plain|10.0.0.53|||;own6|plain|fd00::53|5353||;ownx|udp|192.168.1.1|53||"
got=$(dnsdist_upstreams_parse 2>/dev/null)
echo "$got" | sed 's/^/     /'
[ "$(printf '%s\n' "$got" | wc -l | tr -d ' ')" = "3" ] && ok "три plain-записи разобраны" || bad "plain: число записей"
printf '%s\n' "$got" | grep -q $'own\tplain\t10.0.0.53:53\t\t' && ok "plain: порт 53 по умолчанию, ни SNI, ни пути" || bad "plain own"
printf '%s\n' "$got" | grep -q $'own6\tplain\t\[fd00::53\]:5353\t\t' && ok "plain: v6 в скобках, свой порт" || bad "plain own6"
printf '%s\n' "$got" | grep -q $'ownx\tplain\t' && ok "udp — синоним plain" || bad "plain ownx"
export GODNS_UPSTREAMS=""

echo
echo "-- рендер: plain без TLS, DoH с путём, DoT как прежде --"
_saved_ups=$(config_get upstreams "")
config_set upstreams "own|plain|10.0.0.53|53||;mine|doh|203.0.113.7|8443|dns.mine.test|/q;cf|dot|1.1.1.1|853|one.one.one.one"
dnsdist_render_config "$WORK/mixed.conf" >/dev/null 2>&1 || bad "рендер смешанного набора"
grep -q "newServer({address='10.0.0.53:53', " "$WORK/mixed.conf" && ok "plain: newServer с адресом" || bad "plain newServer"
if grep "address='10.0.0.53:53'" "$WORK/mixed.conf" | grep -qE "tls=|subjectName|dohPath"; then
    bad "plain: в newServer прокрались TLS-параметры"
else
    ok "plain: ни tls, ни subjectName, ни dohPath"
fi
grep -q "operator-added: NOT encrypted" "$WORK/mixed.conf" && ok "plain помечен в конфиге как незашифрованный" || bad "plain: нет пометки"
grep -q "address='203.0.113.7:8443', tls='openssl', subjectName='dns.mine.test', dohPath='/q', validateCertificates=true" "$WORK/mixed.conf" \
    && ok "doh: порт, SNI, путь, валидация" || bad "doh render"
grep -q "address='1.1.1.1:853', tls='openssl', subjectName='one.one.one.one', validateCertificates=true" "$WORK/mixed.conf" \
    && ok "dot: как прежде" || bad "dot render"
config_set upstreams "$_saved_ups"

echo
echo "-- --check-config: «Unknown key … ignored» — это провал, а не OK --"
# dnsdist 2.0.2 на неизвестный параметр newServer печатает предупреждение и
# ВСЁ РАВНО «Configuration OK!» с кодом 0. Опечатка в validateCertificates
# проходила ворота и молча выключала проверку сертификата. Ворота обязаны
# читать вывод, а не только код.
cat > "$WORK/fake-unk" <<'EOF'
#!/usr/bin/env bash
printf "newServer: Unknown key 'validateCertificate' given - ignored\nConfiguration '%s' OK!\n" "$2"
exit 0
EOF
cat > "$WORK/fake-clean" <<'EOF'
#!/usr/bin/env bash
printf "Configuration '%s' OK!\n" "$2"
exit 0
EOF
chmod 0755 "$WORK/fake-unk" "$WORK/fake-clean"
rc=0; DNSDIST_BIN="$WORK/fake-unk" dnsdist_check_config /dev/null || rc=$?
[ "$rc" -ne 0 ] && ok "Unknown key в выводе → проверка провалена" || bad "Unknown key прошёл как OK"
rc=0; DNSDIST_BIN="$WORK/fake-clean" dnsdist_check_config /dev/null || rc=$?
[ "$rc" -eq 0 ] && ok "чистый вывод → проверка пройдена" || bad "чистый вывод провален"
DNSDIST_BIN=dnsdist

echo
echo "-- пустой список = отказ, а не пустой конфиг --"
export GODNS_UPSTREAMS=""
rc=0; dnsdist_upstreams_parse >/dev/null 2>&1 || rc=$?
[ "$rc" != "0" ] && ok "пустой список → rc!=0" || bad "пустой список"
rc=0; dnsdist_render_config "$WORK/empty.conf" >/dev/null 2>&1 || rc=$?
[ "$rc" != "0" ] && ok "render отказался без апстримов" || bad "render без апстримов"

echo
echo "== 6. валидация путей DoH =="
for p in "/Xk3n2p8Qv7" "/dns-query" "/p0"; do
  dnsdist_valid_doh_path "$p" && ok "путь $p валиден" || bad "путь $p"
done
for p in "no-slash" "/пробел тут" "/a'b" '/a$(id)' "/a\`b\`"; do
  dnsdist_valid_doh_path "$p" && bad "путь [$p] пропущен!" || ok "путь [$p] отвергнут"
done

echo
echo "== 7. рендер: политика «быстрее» =="
mkdir -p "$GODNS_CERT_DIR"
: > "$GODNS_CERT_DIR/fullchain.pem"; : > "$GODNS_CERT_DIR/privkey.pem"
echo '{}' > "$GODNS_CONFIG"
config_set domain "dns.example.ru"
config_set doh_port "8443"
config_set dot_port "8853"
config_set doh_path "/Xk3n2p8Qv7"
config_set policy "fastest"
config_set console "no"
export GODNS_UPSTREAMS=""
config_set upstreams "cloudflare|dot|1.1.1.1|853|one.one.one.one;quad9|dot|9.9.9.9|853|dns.quad9.net;google|doh|8.8.8.8|443|dns.google|/dns-query;dns4eu|dot|86.54.11.100|853|unfiltered.joindns4.eu"

dnsdist_render_config "$WORK/a.conf" || bad "render fastest"
grep -q "setServerPolicy(leastOutstanding)" "$WORK/a.conf" && ok "leastOutstanding" || bad "leastOutstanding"
grep -q "setACL({'0.0.0.0/0', '::/0'})" "$WORK/a.conf" && ok "setACL открыт" || bad "setACL"
grep -q "addDOHLocal('0.0.0.0:8443', '$GODNS_CERT_DIR/fullchain.pem', '$GODNS_CERT_DIR/privkey.pem', { '/Xk3n2p8Qv7' }, { reusePort=true })" "$WORK/a.conf" && ok "addDOHLocal с секретным путём" || bad "addDOHLocal"
# DoT по умолчанию ВЫКЛЮЧЕН: у него нет секретного пути, и включённый он
# открыт любому, кто знает домен. Проверяем оба состояния — «выключен, и
# слушателя нет» ценнее, чем «включён и работает»: молча поднятый DoT это
# и есть та самая дыра.
if grep -q "addTLSLocal" "$WORK/a.conf"; then
    bad "DoT выключен по умолчанию" "addTLSLocal в конфиге при dot_enabled=no"
else
    ok "DoT выключен по умолчанию — слушателя нет"
fi
config_set dot_enabled "yes"
dnsdist_render_config "$WORK/a_dot.conf" >/dev/null 2>&1
grep -q "addTLSLocal('0.0.0.0:8853'" "$WORK/a_dot.conf" \
    && ok "DoT включён — addTLSLocal появился" || bad "DoT включён — addTLSLocal"
# Лимит запросов — поштучный (MaxQPSIPRule), а не бан dynBlockRulesGroup:
# бан выключал на минуту целые квартиры за роутером (0.32.0).
if grep -q "dynBlockRulesGroup\|setQueryRate" "$WORK/a_dot.conf"; then
    bad "бан по частоте (dynBlockRulesGroup) вернулся в конфиг"
else
    ok "бана по частоте нет"
fi
grep -q "MaxQPSIPRule(200, 32, 64, 400)" "$WORK/a_dot.conf" \
    && ok "поштучный лимит: 200 зап/с, ведро 400" || bad "MaxQPSIPRule с умолчаниями"
grep -q "AndRule({NotRule(NetmaskGroupRule(godns_own)), MaxQPSIPRule" "$WORK/a_dot.conf" \
    && ok "исключение своих адресов стоит ПЕРВЫМ в AndRule" || bad "порядок в AndRule"
if grep -q "godns_own:addMask('127.0.0.0/8')" "$WORK/a_dot.conf" \
   && grep -q "godns_own:addMask('::1/128')" "$WORK/a_dot.conf"; then
    ok "loopback исключён из лимита"
else
    bad "loopback в исключениях лимита"
fi
grep -q "DropAction(), {name='godns-ratelimit'}" "$WORK/a_dot.conf" \
    && ok "правило лимита именовано" || bad "имя правила лимита"
config_set ratelimit_qps "0"
dnsdist_render_config "$WORK/a_rl0.conf" >/dev/null 2>&1
if grep -q "MaxQPSIPRule" "$WORK/a_rl0.conf"; then
    bad "ratelimit_qps=0 — лимит всё равно в конфиге"
else
    ok "ratelimit_qps=0 — лимита нет"
fi
config_set ratelimit_qps "abc"
dnsdist_render_config "$WORK/a_rlx.conf" >/dev/null 2>&1
grep -q "MaxQPSIPRule(200, 32, 64, 400)" "$WORK/a_rlx.conf" \
    && ok "мусор в ratelimit_qps → умолчание" || bad "мусор в ratelimit_qps"
config_set ratelimit_qps "500"
dnsdist_render_config "$WORK/a_rl5.conf" >/dev/null 2>&1
grep -q "MaxQPSIPRule(500, 32, 64, 1000)" "$WORK/a_rl5.conf" \
    && ok "ratelimit_qps=500 → ведро 1000" || bad "ratelimit_qps=500"
# Нормализация значения (ревью 0.32.0): ведущие нули и переполнение. «08»
# валил $((08 * 2)) в рендере на каждом применении, «0200» давало ведро от
# восьмеричного 128, двадцать знаков ломали -gt и молча снимали лимит.
config_set ratelimit_qps "08"
if dnsdist_render_config "$WORK/a_rl08.conf" >/dev/null 2>&1 \
   && grep -q "MaxQPSIPRule(8, 32, 64, 16)" "$WORK/a_rl08.conf"; then
    ok "ratelimit_qps=08 → 8, рендер не падает"
else
    bad "ratelimit_qps=08 (восьмеричная мина)"
fi
config_set ratelimit_qps "0200"
dnsdist_render_config "$WORK/a_rl0200.conf" >/dev/null 2>&1
grep -q "MaxQPSIPRule(200, 32, 64, 400)" "$WORK/a_rl0200.conf" \
    && ok "ratelimit_qps=0200 → 200, ведро 400" || bad "ratelimit_qps=0200"
config_set ratelimit_qps "99999999999999999999"
dnsdist_render_config "$WORK/a_rlbig.conf" >/dev/null 2>&1
grep -q "MaxQPSIPRule(100000, 32, 64, 200000)" "$WORK/a_rlbig.conf" \
    && ok "двадцать знаков → потолок 100000, лимит на месте" || bad "переполнение ratelimit_qps"
config_set ratelimit_qps "100001"
[ "$(dnsdist_ratelimit_qps)" = "100000" ] && ok "100001 прижато к 100000" || bad "прижатие к потолку"
# Подпись в «Состоянии»: значение и «-» без консоли; «выключен» при 0.
config_set ratelimit_qps "200"
_lbl=$( t() { printf '%s' "$1"; }; tf() { printf '%s|%s|%s' "$@"; }; dnsdist_stat_value() { return 1; }
        dnsdist_ratelimit_label )
case "$_lbl" in *"eng_ratelimit_val|200|-"*) ok "подпись лимита: 200 и «-» без консоли" ;; *) bad "подпись лимита: [$_lbl]" ;; esac
config_set ratelimit_qps "0"
_lbl=$( t() { printf '%s' "$1"; }; tf() { printf '%s|%s|%s' "$@"; }; dnsdist_ratelimit_label )
case "$_lbl" in *eng_ratelimit_off*) ok "подпись лимита при 0 — «выключен»" ;; *) bad "подпись при 0: [$_lbl]" ;; esac
config_set ratelimit_qps ""
# Строка лимита в «Состоянии» — структурно: экран не исполняется в тестах,
# а снять строку (мутация ревью) значит снова спрятать лимит от оператора.
grep -q 'kv_row "$(t eng_status_ratelimit)" "$(dnsdist_ratelimit_label)"' "$REPO/lib/engines/dnsdist.sh" \
    && ok "«Состояние» показывает лимит" || bad "строки лимита в «Состоянии» нет"
# Контракт диспетчера: у каждой обёртки engine_X есть dnsdist_X. Опечатка в
# engine_dispatch проезжала bash -n, граф вызовов и все наборы (мутация
# ревью): в проде пункт меню показывал «[?]», а stderr — «нет реализации».
_miss=""
while read -r _fn; do
    [ -n "$_fn" ] || continue
    declare -F "dnsdist_${_fn}" >/dev/null 2>&1 || _miss="${_miss} ${_fn}"
done < <(grep -o 'engine_dispatch [a-z_]*' "$REPO/lib/engine.sh" | awk '{print $2}' | sort -u)
[ -z "$_miss" ] && ok "у каждой обёртки engine_* есть реализация dnsdist_*" || bad "нет реализации dnsdist_ для:${_miss}"
# «Конфиг актуален»: совпадает с рендером — не перезапускать; сменили
# настройку — отличается. Заголовок с датой сравнению не мешает.
DNSDIST_CONF_SAVE="$DNSDIST_CONF"; DNSDIST_CONF="$WORK/live.conf"
dnsdist_render_config "$DNSDIST_CONF" >/dev/null 2>&1
sleep 1
if dnsdist_config_is_current; then ok "живой конфиг совпадает с рендером — перезапуск не нужен"; else bad "config_is_current на равном конфиге"; fi
config_set ratelimit_qps "0"
if dnsdist_config_is_current; then bad "config_is_current не видит смену лимита"; else ok "смена настройки — конфиг устарел"; fi
config_set ratelimit_qps ""
DNSDIST_CONF="$WORK/nosuch.conf"
if dnsdist_config_is_current; then bad "нет живого конфига, а «актуален»"; else ok "нет живого конфига — не актуален"; fi
DNSDIST_CONF="$DNSDIST_CONF_SAVE"
config_set dot_enabled "no"
grep -q "setLocal('127.0.0.1:53')" "$WORK/a.conf" && ok "Do53 прибит к loopback" || bad "setLocal"
grep -q "getPool(''):setCache(godns_pc)" "$WORK/a.conf" && ok "пакетный кэш" || bad "кэш"
grep -q "dohPath='/dns-query'" "$WORK/a.conf" && ok "DoH-апстрим с dohPath" || bad "dohPath"
grep -c "validateCertificates=true" "$WORK/a.conf" | grep -q '^4$' && ok "4 апстрима, все с проверкой сертификата" || bad "validateCertificates"
grep -q ":53'" "$WORK/a.conf" && ok "(ожидаемо) только loopback :53" || true
if grep -qE "newServer\(\{address='[0-9a-fA-F:.\[\]]+:53'" "$WORK/a.conf"; then bad "нашёлся plaintext-апстрим :53"; else ok "ни одного plaintext-апстрима"; fi
grep -q "setRoundRobinFailOnNoServer" "$WORK/a.conf" && bad "rr-строка в fastest-конфиге" || ok "rr-строка отсутствует"

echo
echo "== 8. рендер: политика «по кругу» + без кэша + консоль =="
config_set policy "roundrobin"
config_set cache "no"
config_set console "yes"
dnsdist_render_config "$WORK/b.conf" || bad "render roundrobin"
grep -q "setServerPolicy(roundrobin)" "$WORK/b.conf" && ok "roundrobin" || bad "roundrobin"
grep -q "setRoundRobinFailOnNoServer(true)" "$WORK/b.conf" && ok "FailOnNoServer" || bad "FailOnNoServer"
grep -q "newPacketCache" "$WORK/b.conf" && bad "кэш не выключился" || ok "кэш выключен"
grep -q "^setKey(" "$WORK/b.conf" && ok "setKey" || bad "setKey"
grep -q "^controlSocket('127.0.0.1:5199')" "$WORK/b.conf" && ok "controlSocket" || bad "controlSocket"
grep -q "setConsoleACL({'127.0.0.1', '::1'})" "$WORK/b.conf" && ok "console ACL loopback" || bad "console ACL"
[ -n "$(config_get console_key)" ] && ok "ключ консоли сохранён в config.json" || bad "console_key"
# ГРАБЛИ: порядок важен. На Linux `stat -f` — это «сведения о файловой
# системе», и он УСПЕШНО печатает `File: "…"`, поэтому запасной `stat -c`
# не срабатывает вовсе и проверка прав сравнивает мусор. Ошибка невидима
# на macOS, где `stat -f` и правда про режим файла, — она вылезает только
# на цели. GNU первым, BSD запасным.
p=$(stat -c '%a' "$WORK/b.conf" 2>/dev/null || stat -f '%Lp' "$WORK/b.conf" 2>/dev/null)
[ "$p" = "640" ] && ok "права конфига 0640" || bad "права конфига = $p"

echo
echo "== 9. отказ на кривых портах =="
config_set doh_port "99999"
rc=0; dnsdist_render_config "$WORK/c.conf" >/dev/null 2>&1 || rc=$?
[ "$rc" != "0" ] && ok "кривой порт → отказ" || bad "кривой порт"
# Совпадение портов — отказ ТОЛЬКО когда DoT реально поднимается: при
# выключенном DoT его порт ни на что не влияет, и падать из-за него значит
# мешать оператору на ровном месте.
config_set doh_port "8443"; config_set dot_port "8443"; config_set dot_enabled "yes"
rc=0; dnsdist_render_config "$WORK/c.conf" >/dev/null 2>&1 || rc=$?
[ "$rc" != "0" ] && ok "совпадающие порты при включённом DoT → отказ" || bad "совпадающие порты"
config_set dot_enabled "no"
rc=0; dnsdist_render_config "$WORK/c.conf" >/dev/null 2>&1 || rc=$?
[ "$rc" = "0" ] && ok "при выключенном DoT совпадение портов не мешает" \
                || bad "выключенный DoT всё равно требует свободный порт"
config_set doh_port "8443"; config_set dot_port "8853"
config_set dot_port "8853"

echo
echo "== 10. автогенерация секретного пути =="
echo '{}' > "$GODNS_CONFIG"
config_set upstreams "quad9|dot|9.9.9.9|853|dns.quad9.net"
config_set console "no"
config_set cert_fullchain "$GODNS_CERT_DIR/fullchain.pem"
config_set cert_key "$GODNS_CERT_DIR/privkey.pem"
gen=$(dnsdist_cfg_doh_path 2>/dev/null)
case "$gen" in /?*) ok "сгенерирован путь $gen" ;; *) bad "путь [$gen]" ;; esac
# Соглашение: в config.json путь хранится БЕЗ ведущего слэша, слэш живёт
# только в отрендеренном Lua и в собранном URL. Иначе панель склеивала
# «…:порт/» + «/путь» и выдавала «//путь».
[ "$(config_get doh_path)" = "${gen#/}" ] && ok "путь сохранён без слэша" || bad "путь сохранён как [$(config_get doh_path)], ждали [${gen#/}]"
[ "${#gen}" -ge 10 ] && ok "путь достаточно длинный (${#gen})" || bad "путь короткий"

echo
echo "== 11. probe --version (разбор на подставном бинарнике) =="
cat > "$WORK/fake-dnsdist" <<'EOF'
#!/bin/sh
case "$1" in
--version) echo "dnsdist 2.0.8 (Lua 5.4.6 [LuaJIT ...])"
           echo "Enabled features: cdb dns-over-https(nghttp2) dns-over-quic dns-over-tls(gnutls openssl) dnscrypt ebpf fstrm ipcipher libedit libsodium lmdb protobuf re2 recvmmsg/sendmmsg snmp systemd yaml";;
esac
EOF
chmod +x "$WORK/fake-dnsdist"
DNSDIST_BIN="$WORK/fake-dnsdist" dnsdist_probe_capabilities && ok "2.0.8 принят" || bad "2.0.8 отвергнут"
DNSDIST_BIN="$WORK/fake-dnsdist"; dnsdist_probe_capabilities >/dev/null 2>&1 || true
[ "$DNSDIST_VERSION" = "2.0.8" ] && ok "версия 2.0.8" || bad "версия [$DNSDIST_VERSION]"
[ "$DNSDIST_HAS_DOH$DNSDIST_HAS_DOT$DNSDIST_HAS_DOQ$DNSDIST_HAS_CONSOLE" = "yesyesyesyes" ] && ok "фичи разобраны" || bad "фичи: doh=$DNSDIST_HAS_DOH dot=$DNSDIST_HAS_DOT doq=$DNSDIST_HAS_DOQ con=$DNSDIST_HAS_CONSOLE"
dnsdist_refresh_caps
[ "$(engine_cap can_doq)" = "yes" ] && ok "can_doq поднялся до yes по бинарнику" || bad "can_doq"

cat > "$WORK/fake-old" <<'EOF'
#!/bin/sh
case "$1" in
--version) echo "dnsdist 1.6.1 (Lua 5.3)"
           echo "Enabled features: dns-over-https(DOH) dns-over-tls(gnutls openssl) libsodium";;
esac
EOF
chmod +x "$WORK/fake-old"
rc=0; DNSDIST_BIN="$WORK/fake-old" dnsdist_probe_capabilities || rc=$?
[ "$rc" != "0" ] && ok "1.6.1 (jammy) отвергнут — нет исходящего DoT/DoH" || bad "1.6.1 принят!"

cat > "$WORK/fake-noble" <<'EOF'
#!/bin/sh
case "$1" in
--version) echo "dnsdist 1.8.3 (Lua 5.4)"
           echo "Enabled features: dns-over-tls(gnutls openssl) libsodium";;
esac
EOF
chmod +x "$WORK/fake-noble"
rc=0; DNSDIST_BIN="$WORK/fake-noble" dnsdist_probe_capabilities || rc=$?
[ "$rc" != "0" ] && ok "1.8.3 (noble, без DoH) отвергнут" || bad "1.8.3 принят!"

DNSDIST_BIN=dnsdist

echo
echo "== 12. кодовые имена репозитория =="
OS_VERSION_ID="22.04" OS_CODENAME="jammy"; [ "$(dnsdist_repo_suites | head -1)" = "jammy" ] && ok "22.04 → jammy" || bad "22.04"
OS_VERSION_ID="24.04" OS_CODENAME="noble";  [ "$(dnsdist_repo_suites | head -1)" = "noble" ] && ok "24.04 → noble" || bad "24.04"
OS_VERSION_ID="26.04" OS_CODENAME="resolute"
out=$(dnsdist_repo_suites | tr '\n' ' ')
[ "$out" = "resolute noble jammy " ] && ok "26.04 → resolute, затем деградация: $out" || bad "26.04: $out"
[ "$(dnsdist_repo_line)" = "20" ] && ok "линия репозитория 20 (dnsdist 2.0.x)" || bad "линия: $(dnsdist_repo_line)"


echo
echo "== 13. инъекция в Lua: враждебные значения не рвут строку =="
cat > "$WORK/luacheck.py" <<'LUACHECK'
# -*- coding: utf-8 -*-
"""Грубая проверка Lua-синтаксиса без интерпретатора Lua:
   строки в одинарных кавычках с учётом \\-экранирования + баланс скобок.
   Ловит ровно тот класс ошибок, ради которого существует dnsdist_lua_q."""
import sys

def check(path):
    src = open(path, encoding="utf-8").read()
    out = []
    i, n = 0, len(src)
    depth = {"(": 0, "{": 0}
    line = 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1; i += 1; continue
        if c == "-" and src[i:i+2] == "--":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in "'\"":
            q = c
            i += 1
            closed = False
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "\n":
                    break
                if src[i] == q:
                    closed = True
                    i += 1
                    break
                i += 1
            if not closed:
                out.append("строка %d: незакрытый строковый литерал %s" % (line, q))
            continue
        if c in "({":
            depth[c] += 1
        elif c == ")":
            depth["("] -= 1
            if depth["("] < 0:
                out.append("строка %d: лишняя )" % line)
        elif c == "}":
            depth["{"] -= 1
            if depth["{"] < 0:
                out.append("строка %d: лишняя }" % line)
        i += 1
    if depth["("] != 0:
        out.append("несбалансированные (): %+d" % depth["("])
    if depth["{"] != 0:
        out.append("несбалансированные {}: %+d" % depth["{"])
    return out

rc = 0
for p in sys.argv[1:]:
    errs = check(p)
    if errs:
        rc = 1
        print("  [FAIL] %s" % p)
        for e in errs:
            print("         " + e)
    else:
        print("  [ OK ] %s — литералы закрыты, скобки сбалансированы" % p)
sys.exit(rc)
LUACHECK

Q="'"; BS='\'
echo '{}' > "$GODNS_CONFIG"
config_set domain "dns.example.ru"
config_set doh_path "/Xk3n2p8Qv7"
config_set console "yes"
config_set upstreams "cloudflare|dot|1.1.1.1|853|one.one.one.one;google|doh|8.8.8.8|443|dns.google|/dns-query"
config_set cert_fullchain "/etc/godns/cert/it${Q}s${BS}odd/fullchain.pem"
config_set cert_key "/etc/godns/cert/a${BS}${Q}b/privkey.pem"
config_set acl "0.0.0.0/0,::/0,10.0.0.0/8"
dnsdist_render_config "$WORK/hostile.conf" >/dev/null
grep -n "addDOHLocal" "$WORK/hostile.conf" | sed 's/^/     /'
if python3 "$WORK/luacheck.py" "$WORK/hostile.conf"; then
  ok "враждебные пути экранированы, Lua цел"
else
  bad "враждебные пути порвали Lua"
fi

# Тот же контроль на трёх штатных конфигурациях.
config_set cert_fullchain "$GODNS_CERT_DIR/fullchain.pem"
config_set cert_key "$GODNS_CERT_DIR/privkey.pem"
for combo in "fastest yes" "roundrobin no" "roundrobin yes"; do
  set -- $combo
  config_set policy "$1"; config_set cache "$2"
  dnsdist_render_config "$WORK/c_$1_$2.conf" >/dev/null
  if python3 "$WORK/luacheck.py" "$WORK/c_$1_$2.conf" >/dev/null; then
    ok "конфиг policy=$1 cache=$2 синтаксически цел"
  else
    bad "конфиг policy=$1 cache=$2"
  fi
done


echo
echo "== 14. мост «каталог провайдеров → запись апстрима» =="
# На bash 4 (Ubuntu) поднимаем НАСТОЯЩИЙ lib/providers.sh. На bash 3.2 (macOS)
# он не стартует, поэтому три его точки доступа подменяются заглушками,
# читающими те же самые строки каталога прямо из файла: проверяется наш
# разбор, а данные при этом живые.
if [ "${BASH_VERSINFO[0]:-0}" -ge 4 ] && [ -r "$REPO/lib/providers.sh" ]; then
  # shellcheck source=/dev/null
  . "$REPO/lib/providers.sh"
  echo "     каталог: настоящий lib/providers.sh"

  echo
  echo "== свои апстримы (bash 4, настоящий каталог) =="
  # Свои записи живут в config.json и вливаются в ТУ ЖЕ таблицу. Каждая
  # проверка ниже — граница, которую панель или тест обойдут мимо меню.
  _cu_saved=$(config_get custom_upstreams "" 2>/dev/null) || _cu_saved=""
  _ids_saved=$(config_get upstream_ids "" 2>/dev/null) || _ids_saved=""
  _ups_saved=$(config_get upstreams "" 2>/dev/null) || _ups_saved=""
  config_set custom_upstreams "" >/dev/null 2>&1; prov_reload
  _n0=$(prov_count)
  cu_add() { prov_custom_add "$@" >/dev/null 2>&1 && printf '%s' "$PROV_CUSTOM_LAST_ID"; }
  a=$(cu_add dot 1.1.1.1 853 one.one.one.one "" "Свой DoT") ; prov_reload
  b=$(cu_add doh 8.8.8.8 "" dns.google "" "Свой DoH");     prov_reload
  c=$(cu_add plain 10.0.0.53 "" "" "" "Свой обычный");     prov_reload
  [ "$a" = "custom-one-one-one-one" ] && ok "dot добавлен, id из имени" || bad "dot add: [$a]"
  [ "$b" = "custom-dns-google" ] && ok "doh добавлен, порт 443 по умолчанию" || bad "doh add: [$b]"
  [ "$c" = "custom-10-0-0-53" ] && ok "plain добавлен, id из адреса" || bad "plain add: [$c]"
  [ "$(prov_custom_count)" = "3" ] && ok "в таблице три своих" || bad "custom_count: $(prov_custom_count)"
  [ "$(prov_count)" = "$((_n0 + 3))" ] && ok "каталог вырос ровно на три" || bad "prov_count"
  [ "$(prov_field "$b" doh_url)" = "https://dns.google/dns-query" ] && ok "doh: URL собран из имени и пути" || bad "doh_url: $(prov_field "$b" doh_url)"
  [ "$(prov_field "$c" proto)" = "plain" ] && ok "plain: протокол в поле записи" || bad "plain proto"
  # отказы — каждый обязан отказать И ничего не записать
  _before=$(config_get custom_upstreams "")
  cu_add dot dns.google 853 dns.google "" x >/dev/null   && bad "имя вместо IP принято"      || ok "имя вместо IP отвергнуто"
  cu_add quic 1.1.1.1 853 x "" x >/dev/null              && bad "чужой протокол принят"      || ok "чужой протокол отвергнут"
  cu_add dot 1.1.1.1 853 one.one.one.one "" again >/dev/null && bad "дубль принят"          || ok "дубль по диску отвергнут"
  # Живой сервер: то же имя, другой адрес (round-robin у резолвера) —
  # это тот же апстрим, а не новый.
  cu_add doh 8.8.4.4 443 dns.google "" again2 >/dev/null && bad "дубль по имени принят (другой IP)" || ok "дубль по имени отвергнут при другом IP"
  # А другой порт у того же имени — уже другой апстрим (нестандартный DoT).
  cu_add dot 1.1.1.1 8853 one.one.one.one "" other >/dev/null && ok "то же имя, другой порт — отдельная запись" || bad "другой порт отвергнут как дубль"
  prov_reload; prov_custom_remove "custom-one-one-one-one-2" >/dev/null 2>&1; prov_reload
  cu_add doh 9.9.9.9 443 dns.quad9.net '/{id}' x >/dev/null && bad "шаблон в пути принят"  || ok "шаблон {id} отвергнут"
  cu_add doh 9.9.9.9 443 9.9.9.9 /dns-query x >/dev/null && bad "IP вместо имени в SNI принят" || ok "IP вместо имени в SNI отвергнут"
  cu_add dot 1.0.0.1 99999 one.one.one.one "" x >/dev/null && bad "порт 99999 принят"     || ok "порт вне диапазона отвергнут"
  [ "$(config_get custom_upstreams "")" = "$_before" ] && ok "ни один отказ ничего не записал" || bad "отказ оставил след в config"
  # предел
  _k=0; while [ "$(prov_custom_count)" -lt 8 ]; do _k=$((_k + 1)); cu_add plain "10.9.9.${_k}" "" "" "" "n${_k}" >/dev/null; prov_reload; done
  cu_add plain 10.9.9.99 "" "" "" over >/dev/null && bad "девятый свой принят" || ok "предел в 8 своих держится"
  # группы: свои — последними, split_custom = число записей каталога
  _tail=$(prov_menu_ids | tail -n "$(prov_custom_count)" | grep -vc '^custom-' || true)
  [ "${_tail:-1}" = "0" ] && ok "свои стоят последней группой" || bad "в хвосте меню не только свои"
  [ "$(prov_menu_split_custom)" = "$_n0" ] && ok "граница группы своих = размер каталога" || bad "split_custom: $(prov_menu_split_custom) vs $_n0"
  [ "$(prov_menu_ids | wc -l | tr -d ' ')" = "$(prov_count)" ] && ok "меню показывает все записи" || bad "меню теряет записи"
  # записи движка — протокол у КАЖДОЙ свой
  engine_upstreams_store "cloudflare,$a,$b,$c" >/dev/null 2>&1 || bad "store смешанного набора"
  _recs=$(config_get upstreams "")
  printf '%s' "$_recs" | grep -q "${a}|dot|1.1.1.1|853|one.one.one.one" && ok "запись dot" || bad "запись dot: $_recs"
  printf '%s' "$_recs" | grep -q "${b}|doh|8.8.8.8|443|dns.google|/dns-query" && ok "запись doh с путём" || bad "запись doh: $_recs"
  printf '%s' "$_recs" | grep -q "${c}|plain|10.0.0.53|53||" && ok "запись plain без SNI и пути" || bad "запись plain: $_recs"
  # каталог со своими внутри проходит собственную самопроверку
  prov_selftest >/dev/null 2>&1 && ok "prov_selftest молчит при своих записях" || bad "prov_selftest ругается на свои"
  # удаление
  prov_custom_remove "$c" && ok "plain удалён" || bad "remove"
  prov_reload
  prov_exists "$c" && bad "удалённый всё ещё в таблице" || ok "удалённого нет в таблице"
  printf '%s' "$(config_get custom_upstreams "")" | grep -q "^${c}|\|;${c}|" && bad "удалённый остался в config" || ok "удалённого нет в config"
  prov_custom_remove "custom-nope" >/dev/null 2>&1 && bad "удаление несуществующего = успех" || ok "удаление несуществующего отказывает"
  # восстановить состояние
  config_set custom_upstreams "$_cu_saved" >/dev/null 2>&1
  config_set upstream_ids "$_ids_saved" >/dev/null 2>&1
  config_set upstreams "$_ups_saved" >/dev/null 2>&1
  prov_reload
  unset -f cu_add
else
  CAT="$WORK/catalog.txt"
  grep -E '^[a-z0-9][a-z0-9-]*\|[^|]*\|[^|]*\|[^|]*\|[0-9]*\|(https://|-)' \
      "$REPO/lib/providers.sh" > "$CAT" || true
  echo "     каталог: строки из lib/providers.sh через заглушки ($(grep -c . "$CAT" | tr -d ' ') шт.)"
  _row() { grep -m1 "^${1}|" "$CAT" 2>/dev/null || true; }
  prov_field() {
    local row idx
    row=$(_row "$1"); [ -n "$row" ] || { printf ''; return 0; }
    case "$2" in
      id) idx=1;; name_ru) idx=2;; name_en) idx=3;; dot_host) idx=4;;
      dot_port) idx=5;; doh_url) idx=6;; ipv4) idx=7;; ipv6) idx=8;;
      filter) idx=9;; privacy) idx=10;; account) idx=11;; region) idx=12;;
      *) printf ''; return 0;;
    esac
    printf '%s' "$(printf '%s' "$row" | cut -d'|' -f"$idx")"
    return 0
  }
  prov_dot_hostname() { prov_field "$1" dot_host; }
  prov_upstream_doh() {
    local u; u=$(prov_field "$1" doh_url)
    case "$u" in -|'') printf '' ;; *) printf '%s' "$u" ;; esac
    return 0
  }
fi

r=$(engine_upstream_record cloudflare dot 2>/dev/null) || r=""
[ "$r" = "cloudflare|dot|1.1.1.1|853|one.one.one.one|" ] && ok "cloudflare/dot" || bad "cloudflare/dot = $r"
r=$(engine_upstream_record cloudflare doh 2>/dev/null) || r=""
[ "$r" = "cloudflare|doh|1.1.1.1|443|cloudflare-dns.com|/dns-query" ] && ok "cloudflare/doh" || bad "cloudflare/doh = $r"
r=$(engine_upstream_record cloudflare dot 6 2>/dev/null) || r=""
[ "$r" = "cloudflare|dot|2606:4700:4700::1111|853|one.one.one.one|" ] && ok "IPv6-вариант" || bad "v6 = $r"
r=$(engine_upstream_record controld-p0 doh 2>/dev/null) || r=""
case "$r" in *"|/p0") ok "Control D: нештатный путь /p0 сохранён" ;; *) bad "controld = $r" ;; esac
r=$(engine_upstream_record libredns-noads doh 2>/dev/null) || r=""
case "$r" in *"|/noads") ok "LibreDNS NoAds: путь /noads сохранён" ;; *) bad "libredns-noads doh = $r" ;; esac
r=$(engine_upstream_record libredns-noads dot 2>/dev/null) || r=""
case "$r" in *"|854|"*|*"noads.libredns.gr"*) ok "LibreDNS NoAds dot: нештатный порт/хост учтён" ;; *) bad "libredns-noads dot = $r" ;; esac

# Шаблон в адресе (опечатка в записи каталога) обязан быть отбит ДО того,
# как доедет до конфига: dnsdist уронит службу «недопустимым именем
# сертификата», и разбираться оператор будет долго. Проверяем на
# синтетической записи, а не на настоящем провайдере: раньше здесь стоял
# nextdns, а когда его убрали из каталога, тест начал проходить просто
# потому, что такого id больше нет.
# Заглушки каталога — в подоболочке, чтобы не пережить проверку.
#
# ТРОЕ ГРАБЛЕЙ подряд, и все про молчание. (1) Заглушки ставились в
# текущей оболочке, а модуль возвращался через `source`, который под
# `set -euo pipefail` отдавал ненулевой код — файл умирал здесь, не
# напечатав ни строки. (2) Синтаксическая ошибка ВНУТРИ `$( )` убивала
# только подстановку, а внешний `|| r=""` её проглатывал. (3) Заглушки
# отвечали не на те вопросы: engine_upstream_record спрашивает `ipv4` и
# зовёт prov_dot_hostname, а не `dot_host`, — запись не собиралась по
# отсутствию адреса, и «шаблон отбит» доказывало ровно ничего.
#
# Отсюда положительный контроль: без него «пусто» неотличимо от «не
# запускалось». Именно он и поймал третьи грабли.
_ctl=$( (
          prov_exists() { return 0; }
          prov_dot_hostname() { printf 'ok.example.invalid'; }
          prov_field() {
              if   [ "$2" = ipv4 ];     then printf '10.0.0.1'
              elif [ "$2" = dot_port ]; then printf '853'
              fi
              return 0
          }
          engine_upstream_record probe dot 2>/dev/null
        ) ) || _ctl=""
if [ -n "$_ctl" ]; then
    ok "заглушки каталога работают (контроль дал запись)"
else
    bad "заглушки каталога НЕ работают — проверка шаблона ничего не доказывает"
fi
_bad=$( (
          prov_exists() { return 0; }
          prov_dot_hostname() { printf '{id}.example.invalid'; }
          prov_field() {
              if   [ "$2" = ipv4 ];     then printf '10.0.0.1'
              elif [ "$2" = dot_port ]; then printf '853'
              fi
              return 0
          }
          engine_upstream_record probe dot 2>/dev/null
        ) ) || _bad=""
[ -z "$_bad" ] && ok "шаблон в адресе: запись не собрана" || bad "шаблон собрался: $_bad"

# Весь каталог целиком → конфиг → Lua цел.
recs=""
while IFS='|' read -r pid _rest; do
  [ -n "$pid" ] || continue
  case "$pid" in '#'*) continue ;; esac
  r=$(engine_upstream_record "$pid" dot 2>/dev/null) || r=""
  if [ -n "$r" ]; then recs="${recs:+$recs;}$r"; fi
done < <(if declare -F prov_list_ids >/dev/null 2>&1; then prov_list_ids; else cut -d'|' -f1 "$WORK/catalog.txt"; fi)

want=$(printf '%s' "$recs" | tr ';' '\n' | grep -c . | tr -d ' ')
config_set policy roundrobin
config_set cache yes
config_set console no
config_set upstreams "$recs"
dnsdist_render_config "$WORK/catalog.conf" >/dev/null
got=$(grep -c '^newServer' "$WORK/catalog.conf" | tr -d ' ')
if [ "$got" = "$want" ] && [ "$want" -ge 15 ]; then
  ok "весь каталог ($want провайдеров) доехал до конфига"
else
  bad "доехало $got из $want"
fi
if python3 "$WORK/luacheck.py" "$WORK/catalog.conf" >/dev/null; then
  ok "конфиг из всего каталога синтаксически цел"
else
  bad "конфиг из всего каталога"
fi

echo
echo "== 14. живое наблюдение: разбор вывода консоли dnsdist =="
#
# Фикстуры — НАСТОЯЩИЙ вывод dnsdist 2.1.1, снятый с работающего демона
# с тремя DoT/DoH-апстримами. Это не выдумка «как оно примерно выглядит»:
# три вещи здесь контринтуитивны и без живого прогона были бы угаданы неверно.
#   1. Колонка Lat у showServers пуста («-»), а число лежит в колонке TCP —
#      потому что Lat считает ТОЛЬКО UDP-ответы, а у goDNS все апстримы
#      DoT/DoH, то есть TCP. Наивный разбор показал бы «задержки нет».
#   2. Длинное имя ПЕРЕПОЛНЯЕТ свою колонку и сдвигает вправо всё
#      остальное: у `nonexistent-zzz-qqq.example.` (28 символов при
#      ширине 25) Type уезжает со 114-й позиции на 117-ю.
#   3. У строк-запросов колонка Server ПУСТАЯ, поэтому разбор по пробелам
#      съезжает на колонку и приписывает запросу чужой апстрим.
#
cat > "$WORK/fx_servers.txt" <<'FXSRV'
#   Name                 Address                                      State     Qps    Qlim        Ord         Wt    Queries   Drops Drate   Lat   TCP Outstanding Pools
0   cloudflare           1.1.1.1:853                                     up     2.0       0          1          1          6       0   0.0     -  11.3           0 
1   quad9                9.9.9.9:853                                     up     1.0       0          1          1          6       0   0.0     -  12.9           2 
2   google               8.8.8.8:443                                    down     1.0       0          1          1          5       0   0.0     -   5.6           0 
All                                                                             4.0                                       17       0
FXSRV

cat > "$WORK/fx_stats.txt" <<'FXST'
acl-drops                          	          0    latency-tcp-avg100                 	          0.0
cache-hits                         	        812    latency-tcp-avg1000                	          0.0
cache-misses                       	        418    latency0-1                         	          1
downstream-timeouts                	          3    queries                            	       1230
frontend-nxdomain                  	          9    responses                          	       1230
servfail-responses                 	          2    rule-drop                          	          0
FXST

# Лента: переполненное имя, ответ из кэша, запрос БЕЗ ответа (давний —
# значит настоящая потеря, а не «ещё в полёте»), NXDOMAIN и NODATA.
cat > "$WORK/fx_feed.txt" <<'FXFD'
Time    Client                                          Protocol     Server       ID    Name                      Type  Lat.   TC RD AA Rcode
-219.2  127.0.0.1:63954                                 DoUDP                     7309  nonexistent-zzz-qqq.example. A               RD    Question
-219.0  127.0.0.1:63954                                 DoT          9.9.9.9:853  7309  nonexistent-zzz-qqq.example. A     191.9     RD    Non-Existent domain
-160.0  203.0.113.77:41000                              DoH                       4242  lost.example.com.         A               RD    Question
-136.9  198.51.100.9:5300                               DoT                       54199 u1-13800.example.com.     A               RD    Question
-136.7  198.51.100.9:5300                               DoT          1.1.1.1:853  54199 u1-13800.example.com.     A     199.7     RD    No Error. 0 answers
-12.4   127.0.0.1:64972                                 DoUDP                     37948 example.com.              A               RD    Question
-12.4   127.0.0.1:64972                                 -            Cache        37948 example.com.              A     0.0       RD    No Error. 2 answers
FXFD

# Заглушка t() из lib/engine.sh печатает САМ КЛЮЧ, а ключ длиннее колонки
# и обрезается многоточием — тест проверял бы обрезку, а не разбор. Даём
# короткие подписи, как в настоящей локали.
t() {
  case "$1" in
    mon_cache_word) printf 'кэш' ;;
    mon_noanswer)   printf 'нет отв.' ;;
    mon_inflight)   printf '…' ;;
    mon_q_up)       printf 'апстрим' ;;
    *)              printf '%s' "$1" ;;
  esac
  return 0
}

mon_render() {   # <ширина> <строк>
  {
    printf '@@GODNS labels\n'
    dnsdist_mon_labels 2
    printf '@@GODNS servers\n'; cat "$WORK/fx_servers.txt"
    printf '@@GODNS stats\n';   cat "$WORK/fx_stats.txt"
    printf '@@GODNS feed\n';    cat "$WORK/fx_feed.txt"
  } | python3 "$REPO/lib/monitor.py" --width "$1" --rows "$2"
}

# Голый текст без управляющих последовательностей — по нему и проверяем.
mon_plain() { mon_render "$1" "$2" | python3 -c "
import re,sys
a=re.compile('\033\\\\[[0-9;]*[A-Za-z]')
sys.stdout.write('\n'.join(a.sub('',l).rstrip() for l in sys.stdin.read().splitlines()))
"; }

scr=$(mon_plain 78 12)

# ── ширина: требование заказчика — влезать в 80 колонок ────────────────
overflow=$(printf '%s\n' "$scr" | python3 -c "
import sys
print(sum(1 for l in sys.stdin.read().splitlines() if len(l) > 78))
")
[ "$overflow" = "0" ] && ok "экран влезает в 78 колонок (ни одной длинной строки)" \
                      || bad "строк шире 78: $overflow"

# ── апстримы ───────────────────────────────────────────────────────────
printf '%s\n' "$scr" | grep -qE '^  cloudflare +1\.1\.1\.1:853 +up' \
  && ok "апстрим: имя, адрес и состояние на месте" || bad "строка апстрима"
printf '%s\n' "$scr" | grep -q '11.3' \
  && ok "задержка взята из колонки TCP (Lat у DoT-апстрима всегда «-»)" \
  || bad "задержка апстрима не найдена — разбор упал на колонке Lat"
printf '%s\n' "$scr" | grep -qE '^  google .*down' \
  && ok "лежащий апстрим показан как down" || bad "состояние down"
printf '%s\n' "$scr" | grep -qE '^  All' \
  && bad "итоговая строка «All» уехала в таблицу апстримов" \
  || ok "итоговая строка «All» отброшена"

# ── лента ──────────────────────────────────────────────────────────────
# Главная регрессия: переполненное имя не должно ломать разбор соседей.
printf '%s\n' "$scr" | grep -q 'NXDOMAIN' \
  && ok "«Non-Existent domain» → NXDOMAIN" || bad "NXDOMAIN"
printf '%s\n' "$scr" | grep -q 'NODATA' \
  && ok "«No Error. 0 answers» → NODATA (это не то же самое, что NOERROR)" || bad "NODATA"
printf '%s\n' "$scr" | grep -qE 'nonexistent-zzz.*quad9 +191\.9' \
  && ok "переполненное имя: апстрим и задержка разобраны верно" \
  || bad "разбор поехал на длинном имени"
printf '%s\n' "$scr" | grep -qE 'example\.com.*кэш +0\.0' \
  && ok "ответ из кэша помечен как кэш, апстрим не назван" || bad "строка кэша"
printf '%s\n' "$scr" | grep -q 'нет отв.' \
  && ok "давний запрос без ответа показан как потерянный" || bad "запрос без ответа"
printf '%s\n' "$scr" | grep -qE '198\.51\.100\.9 +u1-13800' \
  && ok "клиент показан без порта, запрос и ответ склеены в одну строку" \
  || bad "склейка запрос+ответ"
n=$(printf '%s\n' "$scr" | grep -cE '^  [0-9][0-9]:[0-9][0-9]:[0-9][0-9] ') || n=0
[ "$n" = "4" ] && ok "четыре обмена из семи строк колец — по одной строке на обмен" \
              || bad "обменов в ленте: $n (ожидалось 4)"

# ── счётчики ───────────────────────────────────────────────────────────
printf '%s\n' "$scr" | grep -q '812' && ok "попадания в кэш из dumpStats" || bad "cache-hits"
printf '%s\n' "$scr" | grep -q '66%' && ok "доля попаданий посчитана (812/1230)" || bad "доля кэша"
printf '%s\n' "$scr" | grep -q '1 230' && ok "большие числа разделены на разряды" || bad "разряды"

# ── широкий терминал ───────────────────────────────────────────────────
scr2=$(mon_plain 110 6)
printf '%s\n' "$scr2" | grep -q 'DoUDP' \
  && ok "на широком экране добавляется колонка протокола клиента" || bad "колонка протокола"
overflow=$(printf '%s\n' "$scr2" | python3 -c "
import sys
print(sum(1 for l in sys.stdin.read().splitlines() if len(l) > 110))
")
[ "$overflow" = "0" ] && ok "широкий экран тоже не выходит за свою ширину" || bad "переполнение на 110"

# ── пустые данные не должны валить экран ───────────────────────────────
empty=$({ printf '@@GODNS labels\n'; dnsdist_mon_labels 2;
          printf '@@GODNS servers\n@@GODNS stats\n@@GODNS feed\n'; } \
        | python3 "$REPO/lib/monitor.py" --width 78 --rows 8) || empty=""
[ -n "$empty" ] && ok "консоль молчит → экран рисуется, а не падает" || bad "пустой ввод уронил экран"

echo
echo "== 15. живое наблюдение: помощники движка =="
w=$(dnsdist_mon_width);  case "$w" in ''|*[!0-9]*) bad "ширина: [$w]" ;; *) ok "ширина терминала: $w" ;; esac
r=$(dnsdist_mon_rows 3); [ "$r" -ge 4 ] && ok "строк ленты: $r (не меньше четырёх)" || bad "строк ленты: $r"
n=$(dnsdist_mon_upstream_count); [ "$n" -ge 1 ] && ok "апстримов в конфиге: $n" || bad "счёт апстримов: $n"
# ГРАБЛИ: `dnsdist_mon_labels | grep -q` рвёт трубу — grep выходит на первой
# строке, printf получает SIGPIPE и печатает «write error». Берём вывод целиком.
labels=$(dnsdist_mon_labels 2)
printf '%s\n' "$labels" | grep -q '^title=' && ok "подписи экрана уходят секцией labels" || bad "labels"
# Цвет обязан приехать НАСТОЯЩИМ управляющим байтом, а не текстом «\033[2m»:
# в lib/common.sh это литеральная строка, и без %b вёрстка рассыпается.
printf '%s\n' "$labels" | grep -q "^c_dim=$(printf '\033')" \
  && ok "цвета развёрнуты в управляющие байты (%b, не %s)" || bad "цвета приехали текстом"
case "$(dnsdist_query_log_where)" in
  *journalctl*|*tail*) ok "подсказка «куда смотреть»: $(dnsdist_query_log_where)" ;;
  *) bad "подсказка про лог пуста" ;;
esac
# Без консоли состояние обязано быть «неизвестно», а не выдуманное «выкл».
DNSDIST_CONF_SAVE="$DNSDIST_CONF"; DNSDIST_CONF="$WORK/nosuch.conf"
s=$(dnsdist_query_log_state 2>/dev/null) || true
[ "$s" = "unknown" ] && ok "без консоли состояние лога — «неизвестно», а не выдумка" || bad "состояние без консоли: [$s]"
DNSDIST_CONF="$DNSDIST_CONF_SAVE"

# ── Смена набора обязана менять КАНОНИЧЕСКИЕ записи ────────────────────
# ГРАБЛИ, стоившие дороже всех: движок рендерит только из строки
# `id|proto|host|port|sni|path`. Веб-панель меняла один upstream_ids,
# рапортовала «апстримы применены» — и резолвер продолжал ходить в СТАРЫЙ
# набор. Поймано живым прогоном: панель показывала три апстрима, dnsdist
# держал четыре, включая убранный. Ни один инструмент статической проверки
# такое не видит: обе величины существуют, обе строки, формат разный.
echo
echo "── общая сборка канонических записей ──"
UPS_CFG="$WORK/ups.json"
GODNS_CONFIG_SAVE="${GODNS_CONFIG:-}"
export GODNS_CONFIG="$UPS_CFG"
printf '{"lang":"ru"}\n' > "$UPS_CFG"

skipped=$(engine_upstreams_store "cloudflare,google,quad9-unfiltered") && rc=0 || rc=1
[ "$rc" -eq 0 ] && ok "сборка записей прошла" || bad "сборка записей не прошла"
n=$(config_get upstreams "" | tr ';' '\n' | grep -c . || true); n="${n:-0}"
[ "$n" -eq 3 ] && ok "записей ровно три" || bad "записей: $n (ждали 3)"

# Главное: убираем один — обязаны измениться ОБЕ величины.
engine_upstreams_store "cloudflare,google" >/dev/null || bad "повторная сборка не прошла"
ids=$(config_get upstream_ids "")
n=$(config_get upstreams "" | tr ';' '\n' | grep -c . || true); n="${n:-0}"
[ "$ids" = "cloudflare,google" ] && ok "список идентификаторов обновился" || bad "ids: [$ids]"
[ "$n" -eq 2 ] && ok "канонические записи обновились вслед за списком" \
    || bad "записей: $n (ждали 2) — набор в конфиге разъехался со списком"
if config_get upstreams "" | grep -q 'quad9'; then bad "убранный апстрим остался в записях"; else ok "убранный апстрим исчез из записей"; fi

# ГРАБЛИ: в upstream_ids писался СЫРОЙ список, включая отброшенных. Панель
# показывала апстрим выбранным, а dnsdist его не знал, и расхождение само
# себя поддерживало: сохранённый список уходил в следующее «Применить».
engine_upstream_record() {
    case "$1" in
        cloudflare) printf 'cloudflare|dot|1.1.1.1|853|one.one.one.one|' ;;
        google)     printf 'google|dot|8.8.8.8|853|dns.google|' ;;
        *) return 1 ;;
    esac
}
config_set upstream_ids "" >/dev/null; config_set upstreams "" >/dev/null
skipped=$(engine_upstreams_store "cloudflare,нетакого,google") && rc=0 || rc=1
[ "$rc" -eq 0 ] && ok "набор с одним негодным принят" || bad "набор с негодным отвергнут целиком"
[ "$skipped" = "нетакого" ] && ok "негодный назван в пропущенных" \
    || bad "пропущенные: [$skipped] (ждали «нетакого»)"
ids=$(config_get upstream_ids "")
[ "$ids" = "cloudflare,google" ] && ok "в список идентификаторов негодный НЕ попал" \
    || bad "upstream_ids: [$ids] — в конфиг попал неприменённый апстрим"
n=$(config_get upstreams "" | tr ';' '\n' | grep -c . || true); n="${n:-0}"
[ "$n" -eq 2 ] && ok "записей ровно две" || bad "записей: $n (ждали 2)"

# Ни одного годного — состояние не трогаем.
before=$(config_get upstreams "")
engine_upstreams_store "нетакогоid" >/dev/null 2>&1 && bad "мусорный набор принят" \
    || ok "набор без единого годного отвергнут"
[ "$(config_get upstreams "")" = "$before" ] \
    && ok "при отказе прежние записи не тронуты" || bad "записи затёрты при отказе"

export GODNS_CONFIG="$GODNS_CONFIG_SAVE"

# ── Проверка живости апстримов настроена ───────────────────────────────
# ГРАБЛИ, найденные по журналу живого сервера: настроек не было вовсе, и
# действовали умолчания dnsdist — проверка каждую секунду и «down» после
# ОДНОЙ неудачи. Для DoT это TLS-рукопожатие к каждому апстриму ежесекундно;
# в логе накопилось 10 721 строка «Marking downstream …», по 12 метаний в
# минуту, и все четыре апстрима падали ОДНОВРЕМЕННО. Когда внизу все —
# запросам некуда идти, и клиент получает SERVFAIL на исправных апстримах.
echo
echo "── проверка живости апстримов ──"
grep -q 'maxCheckFailures=' "$WORK/b.conf" \
    && ok "терпимость к неудачам задана явно" \
    || bad "maxCheckFailures не задан — умолчание 1, апстрим падает от одной потери"
if grep -oE 'maxCheckFailures=[0-9]+' "$WORK/b.conf" | head -1 | grep -qE '=([2-9]|[1-9][0-9])'; then
    ok "порог больше единицы"
else
    bad "maxCheckFailures=1 — ровно то умолчание, из-за которого апстримы метались"
fi
grep -q 'checkInterval=' "$WORK/b.conf" \
    && ok "интервал проверки задан явно" \
    || bad "checkInterval не задан — умолчание 1 с, TLS-рукопожатие каждую секунду"
grep -q 'rise=' "$WORK/b.conf" \
    && ok "подъём требует не одной удачи" || bad "rise не задан"
grep -q 'checkTimeout=' "$WORK/b.conf" \
    && ok "таймаут проверки задан" || bad "checkTimeout не задан — умолчание 1 с мало для DoT"

# ── плоский Do53-слушатель уступает чужому резолверу ────────────────────
# ГРАБЛИ (отчёт пользователя): на сервере уже жил Unbound на 127.0.0.1:53.
# `--check-config` сокеты не открывает — проверка проходила, а служба
# падала на старте «Address already in use», и автоустановка не
# завершалась при полностью исправных DoH/DoT. Каждый случай — в
# подоболочке: заглушки не должны пережить свою проверку.
echo
echo "== плоский Do53: конфликт с чужим резолвером =="

_do53_case() {  # <имя-кейса> печатает итоговый plain_listen
    local mode="$1"
    (
        case "$mode" in
            foreign)
                _dnsdist_do53_owner() {
                    case "$1" in 127.0.0.1) printf 'unbound (pid=111)' ;; esac
                    return 0
                }
                ss() { :; } ;;
            ours)
                _dnsdist_do53_owner() {
                    case "$1" in 127.0.0.1) printf 'dnsdist (pid=100)' ;; esac
                    return 0
                }
                systemctl() { printf '100\n'; }
                ss() { :; } ;;
            wildcard)
                _dnsdist_do53_owner() { printf 'dnsmasq (pid=222)'; return 0; }
                ss() {
                    printf 'udp UNCONN 0 0 0.0.0.0:53 0.0.0.0:* users:(("dnsmasq",pid=222,fd=4))\n'
                } ;;
            free)
                _dnsdist_do53_owner() { :; }
                ss() { :; } ;;
            allbusy)
                _dnsdist_do53_owner() { printf 'unbound (pid=111)'; return 0; }
                ss() { :; } ;;
        esac
        if [ -n "${_DO53_SHOW:-}" ]; then
            # Причина отказа — это ВЫВОД функции, и в обычном режиме он
            # намеренно гасится. Для проверки «названа ли причина» его
            # надо пропустить наружу.
            dnsdist_resolve_plain_listen 2>&1
        else
            dnsdist_resolve_plain_listen >/dev/null 2>&1
            config_get plain_listen "по-умолчанию"
        fi
    )
}

# То же, но отдаёт САМ ВЫВОД: нужен там, где итоги разных веток совпадают
# (wildcard и «весь loopback занят» оба дают none) и различить их можно
# только по названной причине.
_do53_case_verbose() {
    _DO53_SHOW=1 _do53_case "$1" || true
}

# «Ключа нет» и «ключ пуст» — разные состояния: config_set с пустым
# значением создаёт ключ, и config_get отдаёт пустоту вместо умолчания.
# Чистая установка — это именно ОТСУТСТВИЕ ключа.
_do53_reset() {
    python3 - "$GODNS_CONFIG" <<'DELPY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    d = {}
d.pop("plain_listen", None)
json.dump(d, open(sys.argv[1], "w"))
DELPY
}

_do53_reset
r=$(_do53_case free)
[ "$r" = "по-умолчанию" ] && ok "свободно: конфиг не трогается" || bad "free: $r"

_do53_reset
r=$(_do53_case foreign)
[ "$r" = "127.0.0.2:53" ] && ok "занято Unbound: переезд на 127.0.0.2 сохранён" || bad "foreign: $r"

_do53_reset
r=$(_do53_case ours)
[ "$r" = "по-умолчанию" ] && ok "занято нашим dnsdist: не трогаем (переустановка)" || bad "ours: $r"

_do53_reset
r=$(_do53_case wildcard)
[ "$r" = "none" ] && ok "чужой wildcard 0.0.0.0:53: слушатель выключен" || bad "wildcard: $r"
# Исход у wildcard и «весь loopback занят» ОДИНАКОВ (none), поэтому одна
# проверка итога не отличает их вовсе: мутация, снёсшая ветку wildcard
# целиком, оставалась зелёной. Проект требует называть причину своим
# именем — её и сверяем.
# Сброс обязателен: предыдущий кейс оставил plain_listen=none, а это уже
# «явный выбор оператора» — функция вышла бы молча, и проверка сравнивала
# бы пустоту, вечно краснея (или, будь условие обратным, вечно зеленея).
_do53_reset
_w_out=$(_do53_case_verbose wildcard)
if printf '%s' "$_w_out" | grep -qE 'do53_wildcard|0\.0\.0\.0'; then
    ok "wildcard назван своим именем, а не «весь loopback занят»"
else
    bad "wildcard: причина не названа" "$(printf '%s' "$_w_out" | tail -2)"
fi

_do53_reset
r=$(_do53_case allbusy)
[ "$r" = "none" ] && ok "весь loopback занят: слушатель выключен, не падаем" || bad "allbusy: $r"

config_set plain_listen "127.0.0.5:53" >/dev/null 2>&1 || true
r=$(_do53_case foreign)
[ "$r" = "127.0.0.5:53" ] && ok "явный выбор оператора уважается" || bad "explicit: $r"
_do53_reset

# Резолвер обязан зваться ИЗ dnsdist_apply до рендера: тесты выше зовут его
# напрямую, поэтому выпадение вызова из apply они не заметят — проверяем
# структурно, по телу функции.
# Комментарии выкидываем: закомментированный вызов — это ОТКЛЮЧЁННЫЙ
# вызов, а страж засчитывал его наравне с настоящим, и мутация `#` перед
# строкой проходила зелёной.
_apply_body=$(sed -n '/^dnsdist_apply()/,/^}/p' "$REPO/lib/engines/dnsdist.sh" \
    | grep -vE '^[[:space:]]*#')
if printf '%s' "$_apply_body" | grep -q 'dnsdist_resolve_plain_listen'; then
    ok "dnsdist_apply зовёт резолвер Do53-конфликта"
else
    bad "dnsdist_apply НЕ зовёт dnsdist_resolve_plain_listen — конфликт с Unbound вернётся"
fi

# ── причины отказа установки названы своими именами ─────────────────────
# ГРАБЛИ: dnsdist_install_fallback печатал «repo.powerdns.com недоступен»
# на ЛЮБОМ провале — в том числе когда репозиторий прекрасно открывался, а
# пакет не встал по несовместимым зависимостям (Ubuntu 26.04: ветки
# resolute нет, деградировали до noble, пакет не подошёл). Оператор шёл
# проверять сеть вместо настоящей причины.
#
# Ключи причин передаются голым словом, поэтому check-str-usage видит лишь
# динамический вызов и проверить их не может. Проверяем здесь.
REASONS=$(grep -oE 'dnsdist_install_fallback[[:space:]]+[a-z0-9_]+' "$REPO/lib/engines/dnsdist.sh" \
          | awk '{print $2}' | sort -u)
n_reasons=$(printf '%s\n' "$REASONS" | grep -c . || true)
if [ "${n_reasons:-0}" -lt 2 ]; then
    bad "причин отказа меньше двух — значит опять одна на все случаи"
else
    miss=""
    while IFS= read -r k; do
        [ -n "$k" ] || continue
        grep -qE "^${k}\b" "$REPO/.i18n-keys/engine.tsv" || miss="${miss} ${k}"
    done <<< "$REASONS"
    if [ -n "$miss" ]; then
        bad "причина отказа без строки перевода:${miss}"
    else
        ok "все ${n_reasons} причины отказа установки переведены"
    fi
fi

echo
echo "================= ИТОГОВЫЙ КОНФИГ (политика «по кругу») ================="
cat "$WORK/b.conf"
echo "======================================================================="

echo
if [ "$fail" -eq 0 ]; then echo "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ"; else echo "ПРОВАЛОВ: $fail"; exit 1; fi
