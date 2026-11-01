# Раздел 49H (tools/k3_install_test.sh): меню 17 → 3 «Выходы» (и шаг 4 мастера, подписки выходов — область gwexit) и
# 17 → 2 «Пользователи и устройства» (область gwppl) на английском. Подключается из 49H; помощники — его и начала теста.
echo "  · gwexit/gwppl: выходы, подписки выходов, пользователи и устройства (17 → 3, 17 → 2, шаг 4 мастера)"
use_root h2-en; fake_ctl
i18n_code "меню 17 → 3 и шаг 4 мастера (выходы, подписки выходов)" 'k3_gw_exits_step' 'k3_gw_add_exit' 'k3_gw_egr_words' \
    'k3_gw_pool_sum' 'k3_gw_exits_table' 'k3_gw_exit_kind' 'k3_gw_noexit_note' 'k3_gw_exits_menu' 'k3_gw_exit_toggle' \
    'k3_exit_subs?_.*' 'k3_sub_url_.*'
i18n_code "меню 17 → 2 (пользователи и устройства, выбор маршрута)" 'k3_gw_people.*' 'k3_gw_device_(reset|add_menu|access|del)' \
    'k3_gw_pick_(device|user|route)' 'k3_gw_user_(add_menu|del)' 'k3_gw_route_set'
# Чужие тексты, которые эти экраны только показывают (их переводят другие): название маршрута k3_gw_via_text и
# route_text из K3_GW_JQ_ROUTE («пул выходов», «напрямую», «блок», «выход «…»», «при отказе — напрямую»).
H2_FOREIGN='пул выходов|, при отказе — напрямую|напрямую|блок|выход «[^»]*»'
H2_EXITS='{"ok": true, "exits": [
 {"id": "e-1111111111", "index": 1, "name": "Vienna", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true, "state": "alive"},
 {"id": "e-2222222222", "index": 2, "name": "Riga", "kind": "awg", "version": "2.0", "host": "198.51.100.32", "usable": false, "enabled": true, "state": "dead", "detail": "no handshake"},
 {"id": "e-3333333333", "index": 3, "name": "Oslo", "kind": "awg", "version": "3.1", "host": "198.51.100.33", "usable": false, "enabled": true, "state": "unknown"},
 {"id": "e-4444444444", "index": 4, "name": "Bern", "kind": "vless", "host": "198.51.100.34", "usable": false, "enabled": false, "state": "unknown"}],
 "subs": [{"id": "s-aaaaaa", "n": 1, "name": "sub.example.org", "domain": "sub.example.org", "auto": true, "stale": true, "error": "HTTP 404", "text": "not refreshed for 30 h: HTTP 404"}]}'

# ── шаг 4 мастера: ключи есть / нет; ключ VPN — итог проверки словами; ссылка подписки ──
gw_st false '{"wg": {}}' 0 0
gwf exits.json "$H2_EXITS"
h2_step() { clear() { :; }; K3_TEST_TTY=1; egress_awg_runs() { return 0; }; k3_gw_exits_step; echo "RC=$? ROUTE=$K3_GW_ROUTE"; }
o=$(printf 'x\n\n' | en h2_step 2>&1 | strip)
en_clean "шаг 4 мастера (ключи есть)" "$o"
printf '%s' "$o" | grep -q 'Gateway: internet exit (step 4 of 6)' && printf '%s' "$o" | grep -q '1) Vienna (VLESS, 198.51.100.31) — working$' \
  && printf '%s' "$o" | grep -q '2) Riga (AmneziaWG 2.0, 198.51.100.32) — down (no handshake)$' \
  && printf '%s' "$o" | grep -q '3) Oslo (AmneziaWG 3.1, 198.51.100.33) — not checked yet$' \
  && printf '%s' "$o" | grep -q '4) Bern (VLESS, 198.51.100.34) — off for the gateway$' \
  && printf '%s' "$o" | grep -q 'No such item: enter 1, 2, 3 or 0 (Enter — via VPN)\.' && printf '%s' "$o" | grep -q 'RC=0 ROUTE=pool' \
  && ok "шаг 4 на английском: таблица ключей (working / down / not checked yet / off), неверный ввод — словами, Enter — через VPN" \
  || bad "шаг 4 на английском" "$o"
h2_nocore() { clear() { :; }; K3_TEST_TTY=1; egress_awg_runs() { return 1; }; k3_gw exits && k3_gw_exits_table; }
o=$(en h2_nocore 2>&1 | strip)
printf '%s' "$o" | grep -q "Oslo (AmneziaWG 3.1, 198.51.100.33) — won’t work: no AmneziaWG core (needs a GitHub key — menu 8 → 6, then this step again)" \
  && en_clean "таблица выходов: нет ядра AmneziaWG" "$o" || bad "выход AWG без ядра на английском" "$o"
gwf exits.json '{"ok": true, "exits": []}'
h2_step0() { clear() { :; }; K3_TEST_TTY=1; k3_gw_exits_step; echo "RC=$? ROUTE=$K3_GW_ROUTE"; k3_gw_noexit_note; }
o=$(printf '\n7\n3\n' | en h2_step0 2>&1 | strip)
en_clean "шаг 4 мастера (ключей нет) и строка «нет ключа»" "$o"
printf '%s' "$o" | grep -q 'No VPN key yet\.' && printf '%s' "$o" | grep -q '3) No internet for now' \
  && printf '%s' "$o" | flat | grep -q 'Pick an option: 1 — paste a VPN key, 2 — without VPN, 3 — no internet for now, 0 — back\.' \
  && printf '%s' "$o" | grep -q 'No such item: enter 1, 2, 3 or 0\.$' && printf '%s' "$o" | grep -q 'RC=0 ROUTE=pool' \
  && printf '%s' "$o" | flat | grep -q 'its address 10.77.0.1 answers ping and DNS' \
  && ok "шаг 4 без ключей: Enter и неверный ввод — словами, 3 — пока без интернета, честная строка про адрес шлюза" || bad "шаг 4 без ключей на английском" "$o"
# ключ VPN: строки проверки бота (сейчас их пишет egress_add_key по-русски) — словами шлюза по-английски
h2_add() { clear() { :; }; K3_TEST_TTY=1; egress_add_key() { echo "@K3:egress_test:ok"; echo -e "${GREEN}✓ Telegram доступен через выход (HTTP 302).${NC}"; }; k3_gw_add_exit; }
h2_add_bad() { clear() { :; }; K3_TEST_TTY=1; egress_add_key() { echo "@K3:egress_test:fail"; echo -e "${RED}✗ Через выход Telegram недоступен.${NC}"; }; k3_gw_add_exit; }
o=$(printf 'vless://H2KEY\n.\n\n' | en h2_add 2>&1 | strip)
o2=$(printf 'vless://H2KEY\n.\n\n' | en h2_add_bad 2>&1 | strip)
o3=$(printf '\n\n' | en h2_add 2>&1 | strip)
printf '%s' "$o" | grep -q 'Exit — your VPN abroad. Paste its key (vless, hy2, ss, trojan, vmess, vpn://):' \
  && printf '%s' "$o" | grep -q '✓ The exit works: the internet opens through it\.' \
  && printf '%s' "$o2" | grep -q "✗ The exit didn’t respond: check the key" && printf '%s' "$o3" | grep -q 'Nothing pasted — no key added\.' \
  && en_clean "«Добавить ключ выхода»" "$o
$o2
$o3" && ok "ключ выхода на английском: вставка, «exit works» / «didn't respond», пусто — не добавлен" || bad "ключ выхода на английском" "$o | $o2 | $o3"
: > "$CALLS/stdin.log"; : > "$CALLS/kaskadctl.log"
o=$(printf 'https://sub.example.org/s/H2TOKEN\n.\n\n\n' | en h2_add 2>&1 | strip)
gw_calls | grep -qx 'gw exit-sub add --json' && printf '%s' "$o" | grep -q 'This is a subscription link. How should it be added?' \
  && printf '%s' "$o" | grep -q '1) As a subscription (refresh once a day)' && printf '%s' "$o" | grep -q '\[OK\] Subscription added\.' \
  && printf '%s' "$o" | flat | grep -q 'Refreshes itself once a day; to refresh now — “Exit subscriptions” → “Refresh”\.' \
  && ! ctl_log | grep -q H2TOKEN && en_clean "ключ выхода: ссылка подписки" "$o" \
  && ok "ссылка подписки в «Добавить ключ»: вопрос цифрами, по умолчанию — подпиской (адрес — на вход ядра)" || bad "ссылка подписки на английском" "$(gw_seq) | $o"

# ── меню 17 → 3: таблица, маршрут по умолчанию, «если выходы не работают», выключить выход, неверный пункт ──
gw_st true '{"wg": {}}' 1 1
gwf exits.json "$H2_EXITS"
h2_exits() { clear() { :; }; K3_TEST_TTY=1; k3_gw_exits_menu; }
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n1\n1\n\n2\n1\n\n4\n1\n\n9\n0\n' | en h2_exits 2>&1 | strip)
en_clean "меню 17 → 3 (выходы)" "$o" "$H2_FOREIGN"
gw_calls | grep -qx 'gw route pool --fail block --json' && gw_calls | grep -qx 'gw route pool --fail direct --json' \
  && gw_calls | grep -qx 'gw exit e-1111111111 --disable --json' \
  && printf '%s' "$o" | grep -q 'Gateway: exits and default route' && printf '%s' "$o" | grep -q '! Subscription sub.example.org: not refreshed for 30 h: HTTP 404' \
  && printf '%s' "$o" | grep -q '^Default route: .*; if the exits are down — as its group (block)\.$' \
  && printf '%s' "$o" | grep -q 'A user or device can have its own route — “Users & devices”\.' \
  && printf '%s' "$o" | grep -q '^ 2) If the exits are down: as its group (block) → switch to “direct”$' \
  && printf '%s' "$o" | grep -q '^ 5) Exit subscriptions — keys from a link, refreshed daily$' \
  && printf '%s' "$o" | grep -q '1) Exit pool — a live exit of the pool; if it dies, another one' \
  && printf '%s' "$o" | grep -q 'Only exit “Riga” (awg, down)' && printf '%s' "$o" | grep -q 'Only exit “Oslo” (awg, not checked yet)' \
  && printf '%s' "$o" | grep -q 'If the exits of this route are down:' \
  && printf '%s' "$o" | grep -q "When the exits are down, devices will go online from this server’s address\." \
  && printf '%s' "$o" | grep -qx 'Switch “if the exits are down” to “direct”?' && printf '%s' "$o" | grep -qx ' 1) Yes, switch to “direct”' \
  && printf '%s' "$o" | grep -qx ' 2) No, keep “as its group”' && printf '%s' "$o" | grep -q '^Choice \[2\]: ' \
  && printf '%s' "$o" | grep -qx '\[OK\] Saved: on failure — direct\.' && printf '%s' "$o" | grep -q '\[OK\] Exit “Vienna”: off for the gateway\.' \
  && printf '%s' "$o" | grep -q 'No such item: a number from 1 to 7, 0 — back\.' \
  && printf '%s' "$o" | grep -q '^ 6) State and groups — view only$' \
  && printf '%s' "$o" | grep -q '^ 7) Rename an exit (a readable name)$' \
  && ok "17 → 3 на английском: маршрут по умолчанию (пул, блок), «если выходы не работают» → напрямую, выход выключен, неверный пункт" \
  || bad "17 → 3 на английском" "$(gw_seq) | $o"
# 17 → 3 → 2 (живая проверка 3.3.2, №12): «блок» → «напрямую» — только после «1) Да» (по умолчанию «2) Нет»); отказ и
# Enter — ничего не меняется, на экране — какой режим остался; обратно «напрямую» → «блок» — сразу, с новым режимом
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n\n\n2\n2\n\n0\n' | en h2_exits 2>&1 | strip)
o2=$(printf '2\n\n\n2\n2\n\n0\n' | lib h2_exits 2>&1 | strip)
! gw_calls | grep -q -- '--fail' && [ "$(printf '%s\n' "$o" | grep -cx 'Not changed: on failure — as its group (block)\.')" = 2 ] \
  && [ "$(printf '%s\n' "$o2" | grep -cx 'Не изменено: при отказе — как у группы (блок)\.')" = 2 ] \
  && printf '%s' "$o2" | grep -qx 'Переключить «если выходы недоступны» на «напрямую»?' && printf '%s' "$o2" | grep -qx ' 1) Да, переключить на «напрямую»' \
  && printf '%s' "$o2" | grep -qx ' 2) Нет, оставить «как у группы»' \
  && ok "17 → 3 → 2: «блок» → «напрямую» без «да» (2 или Enter) — ядро не зовётся, «Not changed / Не изменено: … — block / блок»" \
  || bad "17 → 3 → 2: отказ" "$(gw_seq) | $o | $o2"
jq '.gateway.routes.default = {"via": "pool", "fail": "direct"}' "$KASKAD_DIR/state.json" > "$KASKAD_DIR/st.tmp" && mv "$KASKAD_DIR/st.tmp" "$KASKAD_DIR/state.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n\n0\n' | en h2_exits 2>&1 | strip); o2=$(printf '2\n\n0\n' | lib h2_exits 2>&1 | strip)
gw_calls | grep -qx 'gw route pool --fail block --json' && ! printf '%s' "$o" | grep -q 'Switch “if the exits are down”' \
  && printf '%s' "$o" | grep -q '^ 2) If the exits are down: direct → switch to “as its group (block)”$' \
  && printf '%s' "$o" | grep -qx '\[OK\] Saved: on failure — as its group (block)\.' && printf '%s' "$o2" | grep -qx '\[OK\] Сохранено: при отказе — как у группы (блок)\.' \
  && ok "17 → 3 → 2: «напрямую» → «блок» — без вопроса, «[OK] Saved: on failure — block.» / «Сохранено: при отказе — блок.»" \
  || bad "17 → 3 → 2: обратно в блок" "$(gw_seq) | $o | $o2"
jq '.gateway.routes.default = {"via": "pool", "fail": "block"}' "$KASKAD_DIR/state.json" > "$KASKAD_DIR/st.tmp" && mv "$KASKAD_DIR/st.tmp" "$KASKAD_DIR/state.json"
gwf exits.json '{"ok": true, "exits": []}'
o=$(printf '0\n' | en h2_exits 2>&1 | strip)
printf '%s' "$o" | grep -q 'No exits in the pool: add a key (item 3)\.' && en_clean "17 → 3 без выходов" "$o" "$H2_FOREIGN" \
  || bad "17 → 3 без выходов на английском" "$o"

# ── подписки выходов ──
gwf exit-sub_list.json '{"ok": true, "subs": [{"id": "s-aaaaaa", "n": 1, "name": "subscription 1", "auto": true, "stale": true, "text": "not refreshed for 30 h", "excluded": 2},
 {"id": "s-bbbbbb", "n": 2, "name": "vpn.example.net", "auto": false, "stale": false, "text": "refreshed 3 h ago: 4 keys"}]}'
gwf exit-sub_refresh.json '{"ok": false, "results": [{"id": "s-aaaaaa", "ok": false, "text": "subscription 1: HTTP 404."},
 {"id": "s-bbbbbb", "ok": true, "warnings": ["pool rebuilt"], "text": "vpn.example.net: added 1, removed 0."}], "text": "-"}'
gwf exit-sub_refresh.rc 1
h2_subs() { clear() { :; }; K3_TEST_TTY=1; k3_exit_subs_menu; }
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n\n3\n2\n\n4\n1\n\n7\n1\n\n5\n2\n\n5\n1\n2\n\n8\n0\n' | en h2_subs 2>&1 | strip)
en_clean "подписки выходов: список, обновление, раз в сутки, вернуть, удалить" "$o"
gw_calls | grep -qx 'gw exit-sub refresh --json' && gw_calls | grep -qx 'gw exit-sub refresh s-bbbbbb --json' \
  && gw_calls | grep -qx 'gw exit-sub auto s-aaaaaa off --json' && gw_calls | grep -qx 'gw exit-sub restore s-aaaaaa --json' \
  && gw_calls | grep -qx 'gw exit-sub del s-aaaaaa --keep-keys --json' && [ "$(gw_calls | grep -c 'exit-sub del')" = 1 ] \
  && printf '%s' "$o" | grep -q 'Gateway: exit subscriptions' \
  && printf '%s' "$o" | grep -q '1) subscription 1 — not refreshed for 30 h (daily)$' && printf '%s' "$o" | grep -q '2) vpn.example.net — refreshed 3 h ago: 4 keys (manual)$' \
  && printf '%s' "$o" | grep -q '^ 6) Rename a subscription$' && printf '%s' "$o" | grep -q '^ 7) Restore excluded keys (2)$' && printf '%s' "$o" | grep -q '\[\*\] Downloading the subscriptions… this may take a minute or two\.' \
  && printf '%s' "$o" | grep -q '✗ subscription 1: HTTP 404\.' && printf '%s' "$o" | grep -q '! pool rebuilt' \
  && printf '%s' "$o" | grep -q '\[OK\] subscription 1: refreshes manually only\.' && printf '%s' "$o" | grep -q '\[OK\] Excluded keys restored\.' \
  && printf '%s' "$o" | grep -q 'Delete subscription “vpn.example.net”?' && printf '%s' "$o" | grep -q '0) Cancel' \
  && printf '%s' "$o" | grep -q '2) Delete it, keep the keys in the pool' && printf '%s' "$o" | grep -q '\[OK\] Subscription deleted\.' \
  && printf '%s' "$o" | grep -q 'No such item: a number from 1 to 7, 0 — back\.' \
  && ok "подписки на английском: обновить все/одну, раз в сутки ⇄ вручную, вернуть исключённые, удалить (Enter — отмена), неверный пункт" \
  || bad "подписки на английском" "$(gw_seq) | $o"
gwf exit-sub_add.json '{"ok": false, "error": "HTTP 404", "data": {"same_server": true, "cert_domain": "vpn.example.org"}}'; gwf exit-sub_add.rc 2
: > "$CALLS/kaskadctl.log"
o=$(printf '1\nhttps://203.0.113.10/s/H2TOKEN\n2\n2\n\n0\n' | en h2_subs 2>&1 | strip)
gw_calls | grep -qx 'gw exit-sub add --manual --json' && printf '%s' "$o" | grep -q 'Paste the subscription link (https://…) and press Enter\. Empty — back\.' \
  && printf '%s' "$o" | grep -q 'The link is like a password: it is not shown on screen\.' && printf '%s' "$o" | grep -q 'Link accepted: https://203.0.113.10/…' \
  && printf '%s' "$o" | grep -q '2) Manually only' && printf '%s' "$o" | grep -q '\[ERROR\] Subscription not added: HTTP 404' \
  && printf '%s' "$o" | grep -q 'Add the same subscription via the domain vpn.example.org?' && printf '%s' "$o" | grep -q '2) No' \
  && ! ctl_log | grep -q H2TOKEN && en_clean "«Добавить подписку»: вручную, отказ ядра, тот же адрес по домену" "$o" \
  && ok "«Добавить подписку» на английском: адрес скрыт, вручную (--manual), ошибка ядра, вопрос «по домену» — цифрами" || bad "добавить подписку на английском" "$(gw_seq) | $o"
gwf exit-sub_list.json '{"ok": true, "subs": []}'
o=$(printf '2\n0\n' | en h2_subs 2>&1 | strip)
printf '%s' "$o" | grep -q 'No subscriptions yet\.' && printf '%s' "$o" | grep -q 'No such item: number 1, 0 — back\.' \
  && en_clean "подписок нет" "$o" || bad "подписок нет на английском" "$o"

# ── 17 → 2 «Пользователи и устройства» ──
use_root h2-ppl; fake_ctl; gw_st true '{"wg": {}, "awg3": {}}' 3 2
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Anna", "route": null, "enabled": true}, {"id": "u-00000002", "name": "Dacha", "route": {"via": "pool", "fail": "block"}, "enabled": false}]}'
gwf device_list.json '{"ok": true, "devices": [
 {"id": "d-00000001", "user": "u-00000001", "name": "Phone", "kind": "phone", "platform": "android", "login": "phone-0001", "access": ["awg3"], "route": null, "enabled": true},
 {"id": "d-00000002", "user": "u-00000001", "name": "Laptop", "kind": "pc", "platform": "linux", "login": "laptop-0002", "access": ["wg", "awg3"], "route": {"via": "exit:e-1111111111", "fail": "direct"}, "enabled": false},
 {"id": "d-00000003", "user": "u-zzzzzzzz", "name": "Spare", "kind": "pc", "platform": "linux", "login": "spare-0003", "access": [], "route": null, "enabled": true}]}'
gwf exits.json "$H2_EXITS"
gwf inbound_list.json "$(gw_list "wg awg3")"
gwf device_reset.json '{"ok": true, "device": {"id": "d-00000002", "name": "Laptop"}, "kinds": ["wg"], "also": ["ikev2"], "apply": null}'
gwf user_add.json '{"ok": true, "user": {"id": "u-00000003", "name": "Bob"}}'
h2_people() { clear() { :; }; K3_TEST_TTY=1; k3_gw_people; }
o=$(printf '0\n' | en h2_people 2>&1 | strip)
en_clean "17 → 2: список и меню" "$o"
printf '%s' "$o" | grep -q 'Gateway: Users & devices' && printf '%s' "$o" | grep -q '👤 Anna · route: as default$' \
  && printf '%s' "$o" | grep -q '1) Phone — Android · protocols: AmneziaWG 3.1 · route: same as the user$' \
  && printf '%s' "$o" | flat | grep -q '2) Laptop — Linux · protocols: WireGuard, AmneziaWG 3.1 · route: exit Vienna, on failure — direct · off' \
  && printf '%s' "$o" | grep -q '👤 Dacha · route: exit pool · off$' && printf '%s' "$o" | grep -q '   no devices$' \
  && printf '%s' "$o" | grep -q '👤 no user$' && printf '%s' "$o" | grep -q '3) Spare — Linux · protocols: none · route: same as the user$' \
  && printf '%s' "$o" | grep -q '^ 4) Device protocols (grant or revoke)$' && printf '%s' "$o" | grep -q '^10) Routing rules “Russian sites direct”: device$' \
  && printf '%s' "$o" | grep -q '^12) Personal page and subscription for apps$' \
  && ok "17 → 2 на английском: пользователи, их устройства, входы, маршрут «как по умолчанию / как у пользователя», выключенные; пункты меню" \
  || bad "17 → 2 на английском: список" "$o"
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n2\n2\n1\n1\nx\n0\n5\n3\n\n4\n1\n1\n9\n0\n6\n9\n0\n0\n' | en h2_people 2>&1 | strip)
en_clean "17 → 2: смена ключей, входы, выбор устройства" "$o"
gw_calls | grep -qx 'gw device reset d-00000002 --inbound wg --json' && gw_calls | grep -qx 'gw device grant d-00000001 wg --json' \
  && printf '%s' "$o" | flat | grep -q 'New keys, passwords and certificates for device “Laptop”' \
  && printf '%s' "$o" | grep -q '1) All protocols (WireGuard, AmneziaWG 3.1)' \
  && printf '%s' "$o" | grep -q 'Change keys and passwords of “Laptop” for WireGuard?' \
  && printf '%s' "$o" | grep -q '\[OK\] Keys and passwords of “Laptop” changed, its connections dropped\.' \
  && printf '%s' "$o" | grep -q 'The shared password also changed for: IKEv2 — issue settings for them too\.' \
  && printf '%s' "$o" | grep -q 'No such item: Enter — issue new settings, 0 — later\.' \
  && printf '%s' "$o" | grep -q 'The device has no protocols — nothing to change\.' \
  && printf '%s' "$o" | grep -q 'Protocols of device “Phone” (number — grant or revoke):' && printf '%s' "$o" | grep -q '\[x\] 2) AmneziaWG 3.1' \
  && printf '%s' "$o" | grep -q '\[OK\] Protocol WireGuard granted — issue settings to the device\.' \
  && printf '%s' "$o" | grep -q 'No such item: a number from 1 to 2, 0 — back\.' && printf '%s' "$o" | grep -q 'No such item: a number from the list above (1–3), 0 — cancel\.' \
  && ok "17 → 2 на английском: смена ключей (один вход, общий пароль), входов нет; входы устройства; неверный номер" \
  || bad "17 → 2 на английском: смена ключей и входы" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '6\n1\n1\n\n7\n2\n2\nx\n\n\n8\n3\n1\n\n9\n1\n1\n\n9\n2\n1\n\n3\nBob\n\n0\n' | en h2_people 2>&1 | strip)
en_clean "17 → 2: маршруты, удаление, добавление" "$o" "$H2_FOREIGN"
gw_calls | grep -qx 'gw device route d-00000001 inherit --json' && gw_calls | grep -qx 'gw user route u-00000002 pool --json' \
  && gw_calls | grep -qx 'gw device del d-00000003 --json' && gw_calls | grep -qx 'gw user del u-00000001 --force --json' \
  && gw_calls | grep -qx 'gw user del u-00000002 --json' && gw_calls | grep -qx 'gw user add Bob --json' \
  && printf '%s' "$o" | grep -q 'Route for “Phone”:' && printf '%s' "$o" | grep -q '1) Same as user “Anna” — now: server default: exit pool' \
  && printf '%s' "$o" | grep -q 'Only exit “Vienna” (vless, working)' && printf '%s' "$o" | grep -q 'Direct — from this server' \
  && printf '%s' "$o" | grep -q 'Block — no internet' && printf '%s' "$o" | grep -q '\[OK\] Route of “Phone”: Same as user “Anna” — now: server default: exit pool\.' \
  && printf '%s' "$o" | grep -q '1) Same as default — now: exit pool' \
  && printf '%s' "$o" | grep -q 'No such item: enter 1 or 2 (Enter — keep as is)\.' \
  && printf '%s' "$o" | grep -q 'The keys and password of device “Spare” will be lost\.' && printf '%s' "$o" | grep -q '\[OK\] Device deleted\.' \
  && printf '%s' "$o" | flat | grep -q 'Deleting user “Anna” also deletes their devices (2), with their keys and passwords\.' \
  && printf '%s' "$o" | grep -q '1) Delete the user and their devices' && printf '%s' "$o" | grep -q 'Delete user “Dacha”?' \
  && printf '%s' "$o" | grep -q '\[OK\] User deleted\.' && printf '%s' "$o" | grep -q '\[OK\] User “Bob” added\.' \
  && ok "17 → 2 на английском: маршрут устройства и пользователя, удалить устройство / пользователя (с устройствами и без), добавить пользователя" \
  || bad "17 → 2 на английском: маршруты и удаление" "$(gw_seq) | $o"
gwf user_list.json '{"ok": true, "users": []}'; gwf device_list.json '{"ok": true, "devices": []}'
o=$(printf '1\n\n11\n\n2\n0\n0\n' | en h2_people 2>&1 | strip)
printf '%s' "$o" | grep -q 'No users yet\.$' && printf '%s' "$o" | grep -q 'No devices yet: “2) Add a device”\.' \
  && printf '%s' "$o" | grep -q 'No users yet: “3) Add a user”\.' && printf '%s' "$o" | grep -q 'A device belongs to a user — add a user first\.' && en_clean "17 → 2: пользователей нет" "$o" || bad "17 → 2 без пользователей на английском" "$o"
gwf inbound_list.json "$(gw_list "")"
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Anna", "route": null, "enabled": true}]}'
gwf device_list.json '{"ok": true, "devices": [{"id": "d-00000001", "user": "u-00000001", "name": "Phone", "kind": "phone", "platform": "android", "login": "phone-0001", "access": [], "route": null, "enabled": true}]}'
o=$(printf '4\n1\n\n0\n' | en h2_people 2>&1 | strip)
printf '%s' "$o" | grep -q 'No gateway protocols installed: “Gateway → Protocols”\.' && en_clean "17 → 2: протоколов нет" "$o" \
  || bad "входы устройства без протоколов на английском" "$o"
# по-русски — прежние строки (каталог ru + код = тот же текст)
o=$(printf '0\n' | lib h2_people 2>&1 | strip)
printf '%s' "$o" | grep -q '^10) Правила «Россия напрямую» устройства$' && printf '%s' "$o" | grep -q '👤 Anna · маршрут: как по умолчанию$' \
  && printf '%s' "$o" | grep -q '1) Phone — Android · протоколы: нет · маршрут: как у пользователя$' \
  && ok "17 → 2 по-русски — прежние строки" || bad "17 → 2 по-русски" "$o"
gwf exits.json "$H2_EXITS"
o=$(printf '0\n' | lib h2_exits 2>&1 | strip)
printf '%s' "$o" | grep -q '^ 5) Подписки выходов — ключи по ссылке, обновление раз в сутки$' \
  && printf '%s' "$o" | grep -q '4) Bern (VLESS, 198.51.100.34) — выключен для шлюза$' \
  && printf '%s' "$o" | grep -q 'Маршрут по умолчанию: пул выходов; если выходы недоступны — как у группы (блок)\.' \
  && ok "17 → 3 по-русски — прежние строки" || bad "17 → 3 по-русски" "$o"
