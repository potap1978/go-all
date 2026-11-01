# Раздел 49H, область gw: меню 17 «Шлюз» — подменю, шапка, мастер (вступление, проверка, протоколы, порты, установка),
# включение и выключение, удаление, предложение после установки. Подключается из tools/k3_install_test.sh (49H).
echo "  · gw: меню 17, мастер, установка, удаление"
i18n_code "меню 17: подменю, шапка, мастер, установка, удаление (gw)" \
    'k3_gw_(title|platform_title|device_kind|kind_name|via_text|pause|head|name_arg|counts|is_setup|enabled|present|on_disk|menu_state|show_warn|fold|fold_run|fold1|vis|spin_start|spin_stop|menu|header|toggle|enable_step|wizard|wiz_intro|preflight|pick_protocols|expand|awg_core_ensure|awg_nokey_note|ports_step|plan_fill|ports_table|port_edit|install_step|build_kinds|install_one|apt_gate|apt_wait|inbounds_table|remove_interactive|offer_mark|offer_fresh|offer_reenable|reenable|pool_changed|dead)' \
    k3_awg_first_leg k3_read_num k3_wrap tn

# Меню 17 и шапка: шлюз включён, прерванная установка; 80×24 и на английском
use_root en-gw-menu; fake_ctl; gw_st true '{"wg": {"enabled": true}, "awg2": {}, "l2tp": {"enabled": false}}' 3 2
t_en_gw() { k3_gw_dead() { return 1; }; k3_awg_pending() { return 1; }; k3_mtu_narrow() { :; }; k3_awg_pending_ask() { :; }
            k3_mtu_narrow_ask() { :; }; scr k3_gw_menu; echo "STATE=$(k3_gw_menu_state)"; }
o=$(K3_TERM_COLS=80 K3_TERM_ROWS=24 en t_en_gw <<< $'x\n0' 2>&1 | strip)
en_clean "17: меню шлюза" "$o"
printf '%s' "$o" | grep -qx ' 1) Protocols' && printf '%s' "$o" | grep -qx ' 2) Users & devices' && printf '%s' "$o" | grep -qx ' 6) Turn off gateway' \
  && printf '%s' "$o" | grep -qx ' 0) Back' && printf '%s' "$o" | grep -q 'Gateway on · client address: 203.0.113.10' \
  && printf '%s' "$o" | grep -q 'Protocols: 2 of 3 on · users: 2 · devices: 3' && printf '%s' "$o" | grep -q 'No such item: enter a number from the list, 0 — back.' \
  && printf '%s' "$o" | grep -qx 'STATE=2 protocols, 3 devices' && [ "$(printf '%s' "$o" | sed '/^STATE=/d' | fit)" = "2 ok" ] \
  && ok "17 на английском: пункты, шапка, неверный выбор, строка 17 главного меню («2 protocols, 3 devices»), 80×24" || bad "17 на английском" "$o"
gw_st false '{"wg": {}, "awg2": {"enabled": false}}' 1 1; : > "$KASKAD_DIR/gw-wizard.unfinished"
o=$(en t_en_gw <<< '0' 2>&1 | strip)
printf '%s' "$o" | grep -q 'Gateway off · ' && printf '%s' "$o" | grep -q 'Protocols: 1 of 2 on · users: 1 · devices: 1' \
  && printf '%s' "$o" | grep -q 'Gateway setup was interrupted — press 6 to finish.' && printf '%s' "$o" | grep -qx ' 6) Turn on gateway' \
  && printf '%s' "$o" | grep -qx 'STATE=off' && ok "17 на английском: выключен, «1 of 2 on», прерванная установка" || bad "17 выключен на английском" "$o"
o=$(lib t_en_gw <<< '0' 2>&1 | strip)
printf '%s' "$o" | grep -q 'Протоколов: 1 включено из 2 · пользователей: 1 · устройств: 1' && printf '%s' "$o" | grep -qx ' 6) Включить шлюз' \
  && printf '%s' "$o" | grep -qx 'STATE=выключен' && ok "17 по-русски — прежние строки" || bad "17 по-русски" "$o"

# Мастер на английском: вступление → проверка → протоколы (PPTP с вопросом) → порты (правка строки) → установка
use_root en-gw-wiz; fake_ctl; engine_nft
gwf probe.json "$GW_PROBE_OK"
gwf inbound_plan.json '{"ok": true, "inbounds": [
 {"kind": "wg", "title": "WireGuard", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 51820, "default": 51820, "conflicts": [{"kind": "forward", "detail": "used by forward to 198.51.100.7:51820"}], "free": 38211}, "fixed": []},
 {"kind": "awg1", "title": "AmneziaWG 1.5", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 31001, "default": 31001, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "awg2", "title": "AmneziaWG 2.0", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 41002, "default": 41002, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "awg3", "title": "AmneziaWG 3.1", "available": true, "installed": false, "supported": false, "why": "no core", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 51003, "default": 51003, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "l2tp", "title": "L2TP/IPsec", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "build": "accel-ppp", "port": null, "fixed": [{"proto": "udp", "port": 1701, "conflicts": []}, {"proto": "udp", "port": 500, "conflicts": []}, {"proto": "udp", "port": 4500, "conflicts": []}]},
 {"kind": "pptp", "title": "PPTP", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": null, "fixed": [{"proto": "tcp", "port": 1723, "conflicts": [{"kind": "service", "detail": "pptpd"}]}]}]}'
gwf inbound_install.wg.json '{"ok": true, "kind": "wg", "record": {"enabled": true, "port": 38211}, "notes": []}'
gwf inbound_install.awg1.json '{"ok": true, "kind": "awg1", "record": {"enabled": true, "port": 31001}, "notes": [], "previous": {"restored": true, "devices": ["d-1"]}}'
gwf inbound_install.awg2.json '{"ok": false, "error": "Port 41002/udp is in use", "code": 3, "data": {}}'
gwf inbound_install.awg2.rc 3
gwf inbound_install.l2tp.json '{"ok": true, "kind": "l2tp", "record": {"enabled": true}, "notes": []}'
gwf enable.json '{"ok": true, "base": "10.77.0.0/16", "host": "203.0.113.10", "warnings": []}'
gw_st true '{"wg": {}, "awg1": {}, "l2tp": {}}' 0 0 "$FAKE_DIR/gw/enable.state"
gwf inbound_list.json "$(gw_list "wg awg1 l2tp")"
t_en_wiz() { K3_TEST_TTY=1; clear() { :; }; k3_awg_engine_ask() { :; }; k3_gw_exits_step() { echo "EXITS-STEP"; return 0; }
             k3_gw_noexit_note() { :; }; k3_gw_first_device() { echo "FIRST-DEVICE"; }; k3_gw_wizard; echo "WIZ=$?"; }
# Enter (начать) · протоколы: 10 (PPTP) → 1 (отметить), 4 (отметить IKEv2), 4 (снять), Enter · порты: 1 (WireGuard) →
# 3 (пропустить), 1 → 3 (вернуть), 1 → 2 → 70000 (неверный) → пауза, Enter · установка: awg2 — порт занят → 3
# (пропустить) · пауза
o=$(printf '\n10\n1\n4\n4\n\n1\n3\n1\n3\n1\n2\n70000\n\n\n3\n\n' | en t_en_wiz 2>&1 | strip)
en_clean "мастер: вступление, протоколы, порты, установка" "$o"
printf '%s' "$o" | grep -q 'The gateway is a protocol translator' && printf '%s' "$o" | grep -q 'Gateway: protocols (step 2 of 6)' \
  && printf '%s' "$o" | grep -q 'PPTP encryption is weak' && printf '%s' "$o" | grep -q '1) Check PPTP' \
  && printf '%s' "$o" | grep -q 'WireGuard *51820/udp *in use → will take 38211' && printf '%s' "$o" | grep -q 'AmneziaWG 3.1 does not work here: no core' \
  && printf '%s' "$o" | grep -q 'A port is a number from 1 to 65535.' && printf '%s' "$o" | grep -q 'Skip WireGuard' \
  && printf '%s' "$o" | grep -q '1) Kaskad picks a port  2) Enter a port  3) Skip AmneziaWG 2.0' && printf '%s' "$o" | grep -q 'AmneziaWG 2.0 skipped.' \
  && printf '%s' "$o" | grep -q 'The previous port and keys are back' && printf '%s' "$o" | grep -q 'L2TP/IPsec (accel-ppp is built from source — up to two minutes) … ✓' \
  && printf '%s' "$o" | grep -q '\[OK\] Gateway is on: network 10.77.0.0/16, client address 203.0.113.10.' \
  && printf '%s' "$o" | grep -q 'WireGuard .* working · devices 1, sessions 0' && printf '%s' "$o" | grep -q 'FIRST-DEVICE' \
  && ok "мастер на английском: протоколы, PPTP, порты, правка строки, порт занят при установке, включение, итог" || bad "мастер на английском" "$o"
# Экран протоколов на английском — в 80×24 (десять протоколов и подписи)
t_en_pp() { scr k3_gw_pick_protocols; }
o=$(K3_TERM_COLS=80 K3_TERM_ROWS=24 en t_en_pp <<< '0' 2>&1 | strip)
[ "$(printf '%s' "$o" | fit)" = "1 ok" ] && ok "протоколы на английском — 80×24" || bad "протоколы на английском: 80×24" "$(printf '%s' "$o" | fit) $o"

# Сервер не подходит; движок iptables и отказ от переезда
use_root en-gw-pre; fake_ctl; engine_nft
gwf probe.json '{"ok": false, "supported": false, "code": "os", "reason": "Ubuntu 22.04 is too old.", "engine": "nft"}'
gwf probe.rc 5
o=$(printf '\n\n' | en t_en_wiz 2>&1 | strip)
en_clean "мастер: сервер не подходит" "$o"
printf '%s' "$o" | grep -q 'The gateway is not available here: Ubuntu 22.04 is too old.' && printf '%s' "$o" | grep -q 'Debian 12 or newer' \
  && ok "сервер не подходит — причина и требования по-английски" || bad "сервер не подходит" "$o"

# apt занят: вопрос до установки (k3_gw_apt_gate) и ожидание
t_en_apt() { k3_apt_holder() { echo "unattended-upgr (PID 42)"; }; k3_gw_apt_gate "WireGuard" "$(tf GW_INST_SELF dnsmasq)"; echo "RC=$?"
             K3_GW_APT_WAIT=0; k3_gw_apt_wait; echo "RC2=$?"; }
o=$(printf '7\n2\n' | en t_en_apt 2>&1 | strip)
en_clean "apt занят" "$o"
printf '%s' "$o" | flat | grep -q 'Packages are needed for: WireGuard, the gateway itself (dnsmasq)\.' && printf '%s' "$o" | grep -q 'Enter 1, 2 or 0.' \
  && printf '%s' "$o" | grep -q 'RC=1' && printf '%s' "$o" | grep -q 'RC2=1' && ok "apt занят: вопрос, неверный ввод, «2» — ставить без пакетов" || bad "apt занят" "$o"

# Выключить, включить снова, удалить (с людьми — слово DELETE), предложение после установки
use_root en-gw-rm; fake_ctl; gw_st true '{"wg": {}}' 2 1
gwf purge.json '{"ok": true}'
gwf enable.json '{"ok": true, "base": "10.77.0.0/16", "host": "", "warnings": []}'
t_en_rm() { K3_TEST_TTY=1; clear() { :; }; k3_gw_toggle; k3_gw_remove_interactive; echo "RM=$?"; }
o=$(printf '1\n\n1\nDELETE\n\n' | en t_en_rm 2>&1 | strip)
en_clean "выключить и удалить шлюз" "$o"
printf '%s' "$o" | grep -q 'Turn off the gateway?' && printf '%s' "$o" | grep -q '\[OK\] Gateway is off.' \
  && printf '%s' "$o" | flat | grep -q 'Will be removed: all gateway protocols' && printf '%s' "$o" | grep -q 'Remove users and devices too?' \
  && gw_calls | grep -qx 'gw purge --users --yes --json' && printf '%s' "$o" | grep -q 'RM=0' \
  && ok "выключить и удалить на английском: слово DELETE удаляет и пользователей" || bad "удаление на английском" "$o | $(gw_seq)"
t_ru_rm() { K3_TEST_TTY=1; clear() { :; }; k3_gw_remove_interactive; }
o=$(printf '1\nУДАЛИТЬ\n\n' | lib t_ru_rm 2>&1 | strip)
gw_calls | tail -1 | grep -qx 'gw purge --users --yes --json' && printf '%s' "$o" | grep -q 'Удалить и пользователей с устройствами?' \
  && ok "по-русски — прежнее слово УДАЛИТЬ" || bad "удаление по-русски" "$o"
t_en_re() { K3_TEST_TTY=1; k3_gw_reenable; K3_FRESH=0; : > "$K3_GW_OFFER_MARK"; k3_engine() { echo nft; }; k3_gw_is_setup() { return 1; }
            k3_gw_menu() { echo "GW-MENU"; }; clear() { :; }; K3_PAUSE_SEC=0 k3_gw_offer_fresh; }
o=$(printf '2\n' | en t_en_re 2>&1 | strip)
en_clean "включить снова, предложение после установки" "$o"
printf '%s' "$o" | grep -q 'The gateway is on again.' && printf '%s' "$o" | grep -q "Kaskad $CUR: phones and routers" \
  && printf '%s' "$o" | grep -q '2) No, later' && printf '%s' "$o" | grep -q 'You can connect them later' \
  && ok "включить снова и предложение шлюза после установки — по-английски" || bad "предложение после установки" "$o"

# Перенос по словам постраничного вывода (k3_pg_fold, k3_say) — и для английского текста: фраза переносится по словам,
# «кодовое» (ключ -File, путь .\x.ps1) едет вместе, ссылка и строка без фразы — как есть; русский язык — как раньше
t_en_pgf() { K3_PG_WRAP=40
    k3_say 'Download it to your computer — on Windows run powershell -File .\kaskad-ikev2.ps1 as administrator, then connect.'
    k3_say 'vless://abc@example.com:443?security=reality&sni=www.example.com#Kaskad long link stays whole'
    k3_say 'Login: phone-1a2b'; k3_lang_set ru; k3_say 'Download it to your computer — on Windows run powershell as administrator.'; }
o=$(en t_en_pgf 2>&1 | strip)
[ "$o" = 'Download it to your computer — on
  Windows run powershell
  -File .\kaskad-ikev2.ps1 as
  administrator, then connect.
vless://abc@example.com:443?security=reality&sni=www.example.com#Kaskad long link stays whole
Login: phone-1a2b
Download it to your computer — on Windows run powershell as administrator.' ] \
  && ok "k3_pg_fold на английском: фраза — по словам, «-File .\\x.ps1» вместе, ссылка и «Login: …» целиком; язык ru — латиницу не трогает" \
  || bad "k3_pg_fold на английском" "$o"

# Машинный обмен меню ↔ ядро (I18N-3.3.2.md): решения меню — по полям и меткам ядра, не по его словам
use_root en-gw-xchg; fake_ctl; engine_nft
t_xchg_egr() { K3_MARKS=1 egress_test; echo "RC=$?"; egress_test; }
o=$(EGRESS_TESTURL=http://127.0.0.1:9/ lib eval 'curl() { echo 302; }; t_xchg_egr' 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c '^@K3:egress_test:ok$')" = 1 ] && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "egress_test: метка @K3:egress_test:ok — только с K3_MARKS=1" || bad "egress_test: метка" "$o"
# выходы: «работает, но …» жёлтым — по полям (fails, silent), а не по началу текста; «не проверялся» — без повтора
gwf exits.json '{"ok": true, "exits": [{"id": "e-1", "index": 1, "name": "Vienna", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true, "state": "alive", "fails": 1, "silent": 15, "text": "working, but no answer 15 s (check 1 of 2)"},
 {"id": "e-2", "index": 2, "name": "Oslo", "kind": "vless", "host": "198.51.100.32", "usable": true, "enabled": true, "state": "alive", "fails": 0, "silent": null, "text": "working"},
 {"id": "e-3", "index": 3, "name": "Bern", "kind": "vless", "host": "198.51.100.33", "usable": false, "enabled": true, "state": "unknown", "detail": "not checked yet", "text": "not checked yet"}]}'
t_xchg_ex() { k3_gw exits; k3_gw_exits_table; }
o=$(en t_xchg_ex 2>&1)
printf '%s' "$o" | grep -qF "$(printf 'Vienna (VLESS, 198.51.100.31) — \033[1;33mworking, but')" \
  && printf '%s' "$o" | grep -qF "$(printf 'Oslo (VLESS, 198.51.100.32) — \033[0;32mworking')" \
  && printf '%s' "$o" | strip | grep -q 'Bern (VLESS, 198.51.100.33) — not checked yet$' \
  && ok "выходы на английском: «working, but …» жёлтым по fails/silent, «not checked yet» без повтора в скобках" || bad "выходы: поля ядра" "$o"
# сведение 3.3.2: «не проверялся» — по коду detail_code ядра E1 (detail.not_checked), а не по равенству слов: detail
# на языке сборщика (ru), text — на языке меню (en) — в скобках русского нет
gwf exits.json '{"ok": true, "exits": [{"id": "e-3", "index": 1, "name": "Bern", "kind": "vless", "host": "198.51.100.33", "usable": false, "enabled": true, "state": "unknown", "detail": "ещё не проверялся", "detail_code": "detail.not_checked", "text": "not checked yet"}]}'
o=$(en t_xchg_ex 2>&1 | strip)
printf '%s' "$o" | grep -q 'Bern (VLESS, 198.51.100.33) — not checked yet$' && [ -z "$(printf '%s' "$o" | LC_ALL=en_US.UTF-8 grep '[А-Яа-яЁё]')" ] \
  && ok "выходы: «not checked yet» по detail_code (detail.not_checked), слова сборщика на другом языке не показаны" || bad "выходы: detail_code" "$o"
# подписки: зелёный — последнее обновление удалось (last_ok, error), имя без адреса — домен или номер
gwf exit-sub_list.json '{"ok": true, "subs": [{"id": "s-1", "n": 1, "name": "subscription 1", "domain": "", "auto": true, "stale": false, "text": "refreshed 3 h ago: 4 keys", "last_ok": 100, "last_try": 100, "error": "", "excluded": 0},
 {"id": "s-2", "n": 2, "name": "sub.example.org", "domain": "sub.example.org", "auto": false, "stale": true, "text": "not refreshed for 30 h: HTTP 404.", "last_ok": 100, "last_try": 200, "error": "HTTP 404", "excluded": 0},
 {"id": "s-3", "n": 3, "name": "subscription 3", "domain": "", "auto": false, "stale": true, "text": "not refreshed for 30 h: HTTP 500.", "last_ok": 0, "last_try": 200, "error": "HTTP 500", "excluded": 0}]}'
t_xchg_subs() { k3_exit_subs_load; k3_gw exit-sub list; k3_exit_subs_stale; }
o=$(en t_xchg_subs 2>&1)
printf '%s' "$o" | grep -qF "$(printf 'subscription 1 — \033[0;32mrefreshed')" && printf '%s' "$o" | grep -qF "$(printf 'sub.example.org — \033[0;31mnot refreshed')" \
  && printf '%s' "$o" | strip | grep -q '^! Subscription sub.example.org: not refreshed' && printf '%s' "$o" | strip | grep -q '^! Subscription 3: not refreshed' \
  && ok "подписки на английском: цвет по last_ok/error, имя — домен или номер (поле n)" || bad "подписки: поля ядра" "$o"
# VLESS: отказ проверки сайта — data.kind «mask_check» (любой язык) → «взять без проверки»; другой отказ — нет
gw_st true '{"vless": {"enabled": true, "port": 443}}' 0 0
gwf vless.mask.once.json '{"ok": false, "error": "badsite.example is not fit for masking: TLS 1.2 only.", "code": 2, "data": {"kind": "mask_check"}}'
gwf vless.mask.once.rc 2
gwf vless.mask.json '{"ok": true, "kind": "vless", "sni": "badsite.example", "mask": "custom", "mask_check": {"ok": null}, "changed": true}'
t_xchg_mask() { K3_TEST_TTY=1; k3_gw_vless_after() { echo "AFTER: $1"; }; k3_gw_vless_mask_set badsite.example; echo "RC=$?"; }
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n' | en t_xchg_mask 2>&1 | strip)
gw_calls | grep -qx 'gw vless mask badsite.example --no-check --json' && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "VLESS: отказ проверки (data.kind mask_check) на английском → «2» — gw vless mask … --no-check" || bad "VLESS: mask_check" "$o | $(gw_seq)"
gwf vless.mask.once.json '{"ok": false, "error": "VLESS runs in TLS mode.", "code": 2, "data": {}}'
gwf vless.mask.once.rc 2
: > "$CALLS/kaskadctl.log"
o=$(printf '\n' | en t_xchg_mask 2>&1 | strip)
! gw_calls | grep -q -- '--no-check' && printf '%s' "$o" | grep -q 'RC=1' && ok "VLESS: другой отказ (без data.kind) — без «взять без проверки»" || bad "VLESS: другой отказ" "$o"
