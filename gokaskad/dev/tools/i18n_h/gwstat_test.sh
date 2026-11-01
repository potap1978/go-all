# Раздел 49H, помощник H3: меню 17 → 4 «Адрес для клиентов», → 5 «Состояние», → 8 «Правила», → 9 «Куда пойдёт
# трафик?», движок AmneziaWG (17 → Протоколы → Движок), решение по MTU, страница человека и подписка — на английском
# (области gwstat, gwrules, gwtrace, gwawg, gwpage). Подключает tools/k3_install_test.sh (раздел 49H).
echo "  · gwstat, gwrules, gwtrace, gwawg, gwpage (H3): 17 → 4, 5, 8, 9, движок AmneziaWG, MTU, страница — на английском"
use_root h3gw; fake_ctl
i18n_code "17 → 4, 5, 8, 9, движок AmneziaWG, MTU, страница пользователя и подписка (H3)" k3_gw_host_menu \
    'k3_gw_status_(show|summary|pager|inbounds|exits|rules)' 'k3_rules_(profile_text|menu|do|target)' k3_gw_trace_menu \
    'k3_awg_(engine_field|has_inbound|pending|engine_ask|restarts_since|engine_to|export|import|pending_ask)' \
    'k3_accel_(line|menu)' 'k3_mtu_narrow(_ask)?' k3_gw_page_menu k3_gw_sub_menu k3_sub_do 'k3_page_(do|print|closed)'
# state.json шлюза: h3_st '<входы JSON>' [доп. поля gateway, JSON-объект без скобок]
h3_st() {
    printf '{\n  "engine": "nft",\n  "gateway": {"enabled": true, "host": "203.0.113.10", "inbounds": %s, "devices": [], "users": [], "routes": {"default": {"via": "pool", "fail": "block"}}%s},\n  "schema": 1\n}\n' \
        "$1" "${2:+, $2}" > "$KASKAD_DIR/state.json"
}
# Подделка ядра для групп rules, page, panel (в полном прогоне её добавляет раздел WP-I, в быстром — нет): ответ —
# $FAKE_DIR/<группа>/<подкоманда>.{json,rc,err}; остальное — поддельный kaskadctl. Чужие функции, которые переводят
# другие помощники (таблица выходов, маршрут словами, вопрос о доступе панели), — латиницей.
h3_fakes() {
    k3ctl() {
        case "$1" in
            rules|page|panel)
                printf '%s\n' "$*" >> "$CALLS/kaskadctl.log"
                local f="$FAKE_DIR/$1/$2"
                [ -f "$f.err" ] && cat "$f.err" >&2
                if [ -f "$f.json" ]; then cat "$f.json"; else echo '{"ok": true}'; fi
                return "$(cat "$f.rc" 2>/dev/null || echo 0)" ;;
            *) "$K3_OPT/bin/kaskadctl" "$@" ;;
        esac
    }
    k3_gw_via_text() { echo "exit pool"; }
    k3_gw_exits_table() { echo "  1) vie (VLESS, 198.51.100.31) — OK"; }
    k3_panel_public_on() { echo "PUBLIC? $1"; return 1; }
}
h3_en() { clear() { printf '\f'; }; K3_TEST_TTY=1; h3_fakes; k3_lang_set en; k3_log_lang() { echo en; }; "$@"; }
h3_ru() { clear() { printf '\f'; }; K3_TEST_TTY=1; h3_fakes; "$@"; }

# ── 17 → 4: адрес для клиентов ──
h3_st '{"wg": {}}'
gwf host.json '{"ok": true, "host": "203.0.113.10", "auto": true, "apply": null}'
: > "$CALLS/kaskadctl.log"; o=$(printf '1\nvpn.example.org\n\n2\n\n7\n0\n' | lib h3_en k3_gw_host_menu 2>&1)
en_clean "17 → 4 «Адрес для клиентов»" "$o"
printf '%s' "$o" | strip | grep -q '^Now: 203.0.113.10 (detected automatically)$' && printf '%s' "$o" | strip | grep -q 'If it has changed, reissue settings to the devices' \
  && printf '%s' "$o" | strip | grep -q '^ 1) Set an address or name (for example, vpn.example.org)$' && printf '%s' "$o" | strip | grep -q '^ 0) Back$' \
  && ok "17 → 4 на английском: «Now: … (detected automatically)», «reissue settings», пункты" || bad "17 → 4 на английском" "$o"
o=$(printf '0\n' | lib h3_ru k3_gw_host_menu 2>&1 | strip)
printf '%s' "$o" | grep -q '^Сейчас: 203.0.113.10 (определён сам)$' && printf '%s' "$o" | grep -q '^ 2) Определить заново$' \
  && ok "17 → 4 по-русски — прежний текст" || bad "17 → 4 по-русски" "$o"

# ── 17 → 5: состояние (итог, три раздела) ──
gwf status.json '{"ok": true, "enabled": true, "host": "203.0.113.10",
 "inbounds": [{"kind": "awg2", "title": "AmneziaWG 2.0", "installed": true, "enabled": true, "running": true, "ports": ["40850/udp"], "devices": 2, "sessions": 1, "core": "kernel", "warnings": ["port 40850/udp is closed by UFW"]},
  {"kind": "l2tp", "title": "L2TP/IPsec", "installed": true, "enabled": true, "running": false, "ports": ["1701/udp", "500/udp"], "devices": 1, "sessions": 0}],
 "exits": [{"id": "e-1", "name": "vie", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true, "state": "alive"},
  {"id": "e-2", "name": "ams", "kind": "awg", "host": "198.51.100.32", "usable": false, "enabled": true, "state": "dead", "detail": "no handshake"}],
 "dns": {"active": true, "filter_aaaa": true}, "xray": {"active": false}, "collect": {"active": true},
 "rules": {"ok": true, "lists": {"label": "geoip 202610061012", "fallback": true}, "line": "lists geoip 202610061012; default: ru_direct"},
 "net": {"base": "10.77.0.0/16", "dns": "10.77.0.1"}, "users": 3, "devices": 4,
 "problems": [{"kind": "gw_inbound", "detail": "inbound L2TP/IPsec is down"}], "accel": {"enabled": true, "loaded": false}}'
: > "$CALLS/kaskadctl.log"; o=$(printf '\n\n\n\n\n\n' | K3_TERM_COLS=100 K3_TERM_ROWS=40 lib h3_en k3_gw_status_show 2>&1)
en_clean "17 → 5 «Состояние»: итог и три раздела" "$o"
s=$(printf '%s' "$o" | strip)
printf '%s' "$s" | grep -q '^Gateway on · client address: 203.0.113.10$' \
  && printf '%s' "$s" | grep -q 'Protocols: 1 of 2 working · exits: 1 of 2 · “Russian sites direct” lists — previous copy' \
  && printf '%s' "$s" | grep -q '^Problems (3):$' && printf '%s' "$s" | grep -q '• exit “ams” is down: no handshake' \
  && printf '%s' "$s" | grep -q 'AmneziaWG acceleration: kernel module not loaded — amneziawg-go (userspace) is running' \
  && printf '%s' "$s" | grep -q 'Gateway: status — protocols (1 of 3)' && printf '%s' "$s" | grep -q 'working, kernel · devices 2, sessions 1' \
  && printf '%s' "$s" | grep -q 'NOT working · devices 1, sessions 0' && printf '%s' "$s" | grep -q 'Default route: exit pool; if the exits are down — as its group (block).' \
  && printf '%s' "$s" | grep -q 'Gateway DNS: working, AAAA answers are dropped' && printf '%s' "$s" | grep -q '^Gateway xray: not running$' \
  && printf '%s' "$s" | grep -q 'Gateway network 10.77.0.0/16, DNS 10.77.0.1 · users: 3, devices: 4' \
  && ok "17 → 5 на английском: итог, проблемы (выход “ams” is down), разделы протоколов, выходов, DNS и правил" || bad "17 → 5 на английском" "$s"
o=$(printf '0\n' | K3_TERM_COLS=100 K3_TERM_ROWS=40 lib h3_ru k3_gw_status_show 2>&1 | strip)
printf '%s' "$o" | grep -q 'Протоколы: 1 из 2 работают · выходы: 1 из 2 · списки «Россия напрямую» — прежние' \
  && printf '%s' "$o" | grep -q '• выход «ams» не работает: no handshake' && ok "17 → 5 по-русски — прежний итог" || bad "17 → 5 по-русски" "$o"
gwf status.json '{"ok": true, "enabled": false, "inbounds": [{"kind": "wg", "installed": true}], "exits": [], "problems": []}'
o=$(printf '0\n' | lib h3_en k3_gw_status_show 2>&1)
en_clean "17 → 5: шлюз выключен" "$o"
printf '%s' "$o" | strip | grep -q '^Gateway off: devices cannot connect. Turn on — 17 → 6.$' && printf '%s' "$o" | strip | grep -q '^Protocols: 1 — they will start' \
  && ok "17 → 5 на английском: шлюз выключен" || bad "17 → 5 выключен" "$o"

# ── 17 → 8: правила «Россия напрямую» и профиль устройства ──
mkdir -p "$FAKE_DIR/rules"
printf '%s\n' '{"ok": true, "default": "ru_direct", "default_applied": "all_exit", "block_ads": true, "block_torrent": false,
  "lists": {"label": ""}, "ru_direct": 1, "devices": [{"id": "d-00000001", "name": "Phone", "applied": "ru_direct", "from": "user"}],
  "users": [{"id": "u-00000001", "name": "Mom", "rules": "inherit"}]}' > "$FAKE_DIR/rules/status.json"
printf 'Default profile: all via exit.\n' > "$FAKE_DIR/rules/default.json"
: > "$CALLS/kaskadctl.log"; o=$(printf '1\n\n3\n\n9\n4\n\n0\n' | lib h3_en k3_rules_menu 2>&1)
en_clean "17 → 8 «Правила»" "$o"
s=$(printf '%s' "$o" | strip)
ctl_log | grep -qx 'rules default all_exit' && ctl_log | grep -qx 'rules block torrent on' && ctl_log | grep -qx 'rules update' \
  && printf '%s' "$s" | grep -q '^Lists: not downloaded yet — all via the exit for now (item 4 — download now)$' \
  && printf '%s' "$s" | grep -q '^Default: Russian sites direct (no lists yet — all via exit)$' \
  && printf '%s' "$s" | grep -q '^ 1) Default: Russian sites direct → switch to “all via exit”$' \
  && printf '%s' "$s" | grep -q 'No such item: enter a number from 1 to 5, 0 — back.' && printf '%s' "$s" | grep -q '^\[OK\] Default profile: all via exit.$' \
  && ok "17 → 8 на английском: списки, профиль по умолчанию, пункты, «No such item», kaskadctl rules default|block|update" || bad "17 → 8 на английском" "$(ctl_log) | $s"
o=$(printf '0\n' | lib h3_ru k3_rules_menu 2>&1 | strip)
printf '%s' "$o" | grep -q '^По умолчанию: Россия напрямую (пока списков нет — всё через выход)$' && printf '%s' "$o" | grep -q '^ 3) Торренты: по маршруту устройства → блокировать$' \
  && ok "17 → 8 по-русски — прежний текст" || bad "17 → 8 по-русски" "$o"
: > "$CALLS/kaskadctl.log"; o=$(printf '7\n2\n' | lib h3_en k3_rules_target device d-00000001 Phone 2>&1)
en_clean "профиль правил устройства (пользователи → 10)" "$o"
printf '%s' "$o" | strip | grep -q '^Routing rules for “Phone”: now Russian sites direct (from the user)$' && ctl_log | grep -qx 'rules profile device d-00000001 ru_direct' \
  && printf '%s' "$o" | strip | grep -q 'enter a number from 1 to 3, 0 — back' \
  && ok "профиль правил устройства на английском: «Routing rules for “Phone”: now … (from the user)», kaskadctl rules profile" || bad "профиль правил на английском" "$(ctl_log) | $o"
# T-M6-332 №15: «inherit» человека — «same as default», у устройства пункт 1 — «Same as person (or default)», не «owner»
o=$(printf '0\n' | lib h3_en k3_rules_target user u-00000001 Mom 2>&1 | strip)
o2=$(printf '0\n' | lib h3_en k3_rules_target device d-00000001 Phone 2>&1 | strip)
o3=$(printf '0\n' | lib h3_ru k3_rules_target user u-00000001 Мама 2>&1 | strip)
printf '%s' "$o" | grep -q '^Routing rules for “Mom”: now same as default$' && printf '%s' "$o" | grep -q '^ 1) Same as default$' \
  && printf '%s' "$o2" | grep -q '^ 1) Same as user (or default)$' && ! printf '%s\n%s' "$o" "$o2" | grep -qi 'owner' \
  && printf '%s' "$o3" | grep -q 'сейчас как у владельца' \
  && ok "профиль правил пользователя: «now same as default», устройство — «Same as user (or default)», без «owner»; RU — прежний" \
  || bad "профиль правил: owner" "$o | $o2 | $o3"

# ── 17 → 9: куда пойдёт трафик ──
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Mom", "route": null, "enabled": true, "devices": 1}]}'
gwf device_list.json '{"ok": true, "devices": [
 {"id": "d-00000001", "user": "u-00000001", "name": "laptop", "kind": "pc", "platform": "windows", "login": "l-1", "access": ["l2tp", "socks5"], "route": null, "enabled": true}]}'
printf '%s\n' 'Result: via exit “vie”' '1. Device “laptop” (Mom)' > "$FAKE_DIR/gw/trace.json"
: > "$CALLS/kaskadctl.log"; o=$(printf '5\n1\n9\n2\nya.ru\n\n' | K3_TERM_COLS=80 lib h3_en k3_gw_trace_menu 2>&1)
en_clean "17 → 9 «Куда пойдёт трафик?»" "$o"
s=$(printf '%s' "$o" | strip)
ctl_log | grep -qx 'gw trace d-00000001 ya.ru --inbound socks5' && printf '%s' "$s" | grep -q 'No such item: a number from the list, Enter — new with defaults, 0 — back.' \
  && printf '%s' "$s" | grep -q 'A number from the list, or Enter — the first protocol.' && printf '%s' "$s" | grep -q 'Whose traffic: laptop via SOCKS5.' \
  && printf '%s' "$s" | grep -q 'Result: via exit “vie”' \
  && ok "17 → 9 на английском: устройство, вход (laptop via SOCKS5), ответ ядра" || bad "17 → 9 на английском" "$(ctl_log) | $s"
# итог — цветом по метке ядра @K3:verdict (K3_MARKS=1), не по словам; сама метка на экран не попадает
printf '%s\n' '@K3:verdict block' 'Result: Blocked' '1. Device “laptop” (Mom)' > "$FAKE_DIR/gw/trace.json"
o=$(printf '1\n1\nya.ru\n\n' | K3_TERM_COLS=80 lib h3_en k3_gw_trace_menu 2>&1)
printf '%s' "$o" | grep -qF "$(printf '\033[0;31mResult: Blocked')" && ! printf '%s' "$o" | grep -q '@K3:' \
  && ok "17 → 9: итог цветом по @K3:verdict (block — красный) на любом языке, метка не показывается" || bad "17 → 9: @K3:verdict" "$o"
gwf device_list.json '{"ok": true, "devices": []}'
o=$(printf '\n' | lib h3_en k3_gw_trace_menu 2>&1)
en_clean "17 → 9 без устройств" "$o"
printf '%s' "$o" | strip | grep -q 'No devices yet — checking a new device with default settings.' && printf '%s' "$o" | strip | grep -q 'Whose traffic: new device with default settings.' \
  && ok "17 → 9 без устройств: новое устройство с настройками по умолчанию" || bad "17 → 9 без устройств" "$o"
rm -f "$FAKE_DIR/gw/trace.json"

# ── движок AmneziaWG (17 → Протоколы → Движок), экспорт и импорт, совет после обновления, перезапуски, MTU ──
gwf awg.status.json 'AmneziaWG engine: userspace'
gwf awg.check.json 'The kernel module can be turned on here.'
h3_st '{"awg2": {}}'
: > "$CALLS/kaskadctl.log"; o=$(printf '1\n\n\n3\n/root/x.json\n\n4\n0\n\n9\n0\n' | K3_TERM_COLS=80 lib h3_en k3_accel_menu 2>&1)
en_clean "движок AmneziaWG: меню, смена, экспорт" "$o"
s=$(printf '%s' "$o" | strip)
gw_calls_h3() { grep '^gw ' "$CALLS/kaskadctl.log"; }
gw_calls_h3 | grep -qx 'gw awg engine kernel --why person:menu' && gw_calls_h3 | grep -qx 'gw awg export --file /root/x.json' \
  && printf '%s' "$s" | grep -q '^ 2) amneziawg-go (userspace)$' && printf '%s' "$s" | grep -q 'Switch to the kernel module?' \
  && printf '%s' "$s" | flat | grep -q 'keep it like a password' && printf '%s' "$s" | grep -q 'Switching the AmneziaWG engine (up to 5 minutes' \
  && ok "движок AmneziaWG на английском: пункты, «Switch to the kernel module?», экспорт с предупреждением" || bad "движок на английском" "$(gw_calls_h3 | tr '\n' ';') | $s"
o=$(lib h3_en k3_accel_line 12 2>&1); o2=$(lib h3_ru k3_accel_line 12 2>&1)
[ "$o" = " 12) AmneziaWG engine (kernel module / userspace): userspace" ] && [ "$o2" = " 12) Движок AmneziaWG (модуль ядра / программа): программа" ] \
  && ok "строка движка в «Протоколах»: en и ru" || bad "строка движка" "$o | $o2"
gwf awg.import.json 'AmneziaWG export file: inbounds awg2, users 1, devices 2.'
: > "$CALLS/kaskadctl.log"; o=$(printf '/root/x.json\n2\n' | K3_TERM_COLS=80 lib h3_en k3_awg_import 2>&1)
en_clean "импорт AmneziaWG" "$o"
printf '%s' "$o" | strip | grep -q '1) Yes, import' && ! gw_calls_h3 | grep -qx 'gw awg import --file /root/x.json' \
  && ok "импорт AmneziaWG на английском: вопрос цифрами, «2» — ничего" || bad "импорт на английском" "$o"
mkdir -p "$KASKAD_DIR/gw"
printf '{"awg2": {"at": 2000, "why": "unit", "mtu_now": 1216}, "awg1": {"at": 2000, "why": "unit"}}\n' > "$KASKAD_DIR/gw/restarts.json"
o=$(lib h3_en k3_awg_restarts_since 1500 2>&1); o2=$(lib h3_ru k3_awg_restarts_since 1500 2>&1)
printf '%s' "$o" | grep -q "^AmneziaWG protocols restarted (new MTU, new service version) — " \
  && [ "$o2" = "Протоколы AmneziaWG перезапущены (новая версия службы, новый MTU) — клиенты переподключатся сами за 15–25 с." ] \
  && ok "перезапуски входов в итоге обновления: причины из каталога, по алфавиту, en и ru" || bad "перезапуски входов" "$o | $o2"
h3_st '{"awg2": {}}' '"awg_engine": {"pending": true}'
gwf awg.status.json '{"ok": true, "offer": {"show": true, "blocked": [], "pending": true}}'
: > "$CALLS/kaskadctl.log"; o=$(printf '2\n\n' | K3_TERM_COLS=80 lib h3_en k3_awg_pending_ask 2>&1)
en_clean "совет после обновления (движок AmneziaWG)" "$o"
printf '%s' "$o" | strip | grep -q 'After the update: AmneziaWG runs on amneziawg-go (userspace).' && printf '%s' "$o" | strip | grep -q '2) No, keep amneziawg-go (userspace)' \
  && gw_calls_h3 | grep -qx 'gw awg engine program --why person:menu-update' \
  && ok "совет после обновления на английском: «2» — userspace" || bad "совет после обновления" "$(gw_calls_h3) | $o"
rm -f "$FAKE_DIR/gw/awg.status.json" "$FAKE_DIR/gw/awg.import.json" "$FAKE_DIR/gw/awg.check.json"
h3_st '{"awg2": {}}' '"mtu": {"inbounds": {"awg2": {"offer": "mtu"}}}'
gwf mtu.json '{"ok": true, "ask": ["awg2"], "inbounds": [{"kind": "awg2", "offer": "mtu", "text": "AmneziaWG 2.0: the network card is narrower than the client MTU."}]}'
: > "$CALLS/kaskadctl.log"; o=$(printf '2\n\n' | K3_TERM_COLS=80 lib h3_en k3_mtu_narrow_ask 2>&1)
en_clean "решение по MTU (вход в меню 17)" "$o"
printf '%s' "$o" | strip | grep -q 'Gateway: MTU and the server’s network interface' && printf '%s' "$o" | strip | grep -q '2) Keep as is' \
  && gw_calls_h3 | grep -qx 'gw mtu keep awg2' && ok "MTU на английском: «Keep as is» → gw mtu keep" || bad "MTU на английском" "$(gw_calls_h3) | $o"

# ── страница человека и подписка (люди → 12) ──
mkdir -p "$FAKE_DIR/page" "$FAKE_DIR/panel"
printf '{"ok": true, "pages": []}\n' > "$FAKE_DIR/page/list.json"
printf '%s\n' 'Page for “Mom” is on.' 'Link: /p/SECRETLINK' > "$FAKE_DIR/page/on.json"
printf '{"ok": true, "enabled": true, "path": "/p/SECRETLINK", "url": null, "public": false}\n' > "$FAKE_DIR/page/url.json"
printf '{"ok": true, "installed": true, "public": {"on": false}, "password": true, "local_url": "http://127.0.0.1:1980/", "tunnel": "ssh -L 1980:127.0.0.1:1980 root@203.0.113.10"}\n' > "$FAKE_DIR/panel/status.json"
printf '%s\n' 'Subscription for “Mom” is on.' > "$FAKE_DIR/page/sub.json"
: > "$CALLS/kaskadctl.log"; o=$(printf '7\n1\n\n2\n9\n1\n\n0\n0\n' | lib h3_en k3_gw_page_menu u-00000001 Mom 2>&1)
en_clean "страница пользователя и подписка" "$o"
s=$(printf '%s' "$o" | strip)
ctl_log | grep -qx 'page on u-00000001' && ctl_log | grep -qx 'page sub on u-00000001' \
  && printf '%s' "$s" | grep -q 'Gateway: personal page “Mom”' && printf '%s' "$s" | grep -q '^Now: off$' \
  && printf '%s' "$s" | grep -q 'No such item: 1 — turn on, 2 — subscription, 0 — back.' && printf '%s' "$s" | grep -q 'No such item: 1 — turn on, 0 — back.' \
  && printf '%s' "$s" | grep -q 'The link is like a password' && printf '%s' "$s" | grep -q 'On your computer run:' \
  && printf '%s\n' "$s" | grep -qx '  http://127.0.0.1:1980/p/SECRETLINK' && printf '%s' "$s" | grep -q 'PUBLIC? Turn on access from the internet for the panel' \
  && printf '%s' "$s" | grep -q 'Gateway: subscription “Mom”' && printf '%s' "$s" | grep -q 'The subscription link is like a password' \
  && ok "страница и подписка на английском: включить, туннель при закрытой панели, «No such item», подписка" || bad "страница на английском" "$(ctl_log) | $s"
grep -q 'GW: page of user u-00000001 — on (menu)' "$KASKAD_ROOT/var/log/kaskad.log" && ! grep -q 'SECRETLINK' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "журнал на английском: действие и id, без ссылки" || bad "журнал страницы" "$(cat "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null)"
printf '{"ok": true, "subs": [{"user": "u-00000001", "name": "Mom", "enabled": true, "devices": 1}]}\n' > "$FAKE_DIR/page/sub.json"
printf '{"ok": true, "pages": [{"user": "u-00000001"}]}\n' > "$FAKE_DIR/page/list.json"
: > "$CALLS/kaskadctl.log"; o=$(printf '9\n4\n9\n3\n\n0\n0\n' | lib h3_en k3_gw_page_menu u-00000001 Mom 2>&1)
en_clean "страница включена, подписка включена" "$o"
s=$(printf '%s' "$o" | strip)
printf '%s' "$s" | grep -q '^ 4) Subscription for apps (v2RayTun, Hiddify, incy…)$' && printf '%s' "$s" | grep -q 'enter a number from 1 to 4, 0 — back' \
  && printf '%s' "$s" | grep -q 'enter a number from 1 to 3, 0 — back' && printf '%s' "$s" | grep -q 'Revoke the subscription? The app will no longer get keys from it.' \
  && ! ctl_log | grep -q 'page sub off' && ok "включённые страница и подписка на английском: пункты, отзыв — по умолчанию «No»" || bad "страница включена на английском" "$(ctl_log) | $s"
o=$(printf '0\n' | lib h3_ru k3_gw_page_menu u-00000001 Мама 2>&1 | strip)
printf '%s' "$o" | grep -q '^Сейчас: включена$' && printf '%s' "$o" | grep -q '^ 2) Новая ссылка (прежняя перестанет открываться)$' \
  && ok "страница по-русски — прежний текст" || bad "страница по-русски" "$o"
rm -rf "$FAKE_DIR/page" "$FAKE_DIR/panel" "$FAKE_DIR/rules"
