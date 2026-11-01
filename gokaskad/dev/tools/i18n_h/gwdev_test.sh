# Раздел 49H (подключает tools/k3_install_test.sh): меню 17 на английском — выдача устройству (gwdev), «Протоколы»
# (gwproto), карточка VLESS (gwvless). Код функций — без русского текста; экраны с подделкой ядра — без кириллицы.
# Чужие функции, которые печатают свой текст (названия платформ, таблица входов, движок AmneziaWG, установка
# протокола), подменены латиницей: здесь проверяется только свой код. Данные подделок — латиницей.
echo "  · gwdev, gwproto, gwvless: выдача устройству, «Протоколы», VLESS"
use_root i18n-gwdev; fake_ctl

i18n_code "меню 17: первое и новое устройство, выдача" k3_gw_first_device k3_gw_device_new k3_gw_pick_platform \
    k3_gw_pick_access 'k3_gw_issue_.*' k3_gw_qr_size k3_gw_qr_ready k3_ssh_target k3_pg_pause
i18n_code "меню 17 → 1 (протоколы)" k3_gw_protocols 'k3_gw_proto_.*' k3_gw_pending_ask k3_gw_pki_rotate k3_gw_l2tp_plain k3_gw_socks_udp
i18n_code "меню 17 → 1 → VLESS" k3_gw_mask_color k3_gw_mc_args 'k3_gw_vless_.*'

# подмены чужого текста (их переводят другие исполнители)
h1_stubs() {
    k3_gw_platform_title() { echo "Platform $1"; }
    k3_gw_kind_name() { echo "Phone"; }
    k3_gw_inbounds_table() { echo "  (inbounds table)"; }
    k3_accel_line() { echo " $1) AmneziaWG engine"; }
    k3_gw_install_one() { echo "INSTALL $*"; }
}
h1_proto() { K3_TEST_TTY=1; h1_stubs; k3_gw_protocols; }
h1_card() { K3_TEST_TTY=1; h1_stubs; k3_gw_proto_card "$1"; }

# ── «Протоколы»: список, карточка, выключить, порт, удалить ──
gw_st true '{"wg": {"enabled": true, "port": 51820}, "l2tp": {"enabled": true, "plain": false}, "socks5": {"enabled": true, "udp": true}, "ikev2": {"enabled": true}}' 1 1
gwf inbound_list.json "$(gw_list "wg l2tp socks5 ikev2")"
o=$(printf 'abc\n1\n0\n0\n' | en h1_proto 2>&1 | strip)
en_clean "17 → 1: список и карточка WireGuard" "$o"
printf '%s' "$o" | grep -q 'Gateway: protocols' && printf '%s' "$o" | grep -q 'No such item: a number from 1 to 12, 0 — back\.' \
  && printf '%s' "$o" | grep -q 'State: working · port: 51820/udp · devices: 1 · sessions: 0' \
  && printf '%s\n' "$o" | grep -qx ' 1) Turn off' && printf '%s\n' "$o" | grep -qx ' 2) Change port' \
  && printf '%s\n' "$o" | grep -qx ' 3) Remove protocol' \
  && ok "EN 17 → 1: заголовок, «нет такого пункта», состояние и пункты карточки" || bad "EN 17 → 1: список и карточка" "$o"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n1\n\n0\n' | en h1_card wg 2>&1 | strip)
en_clean "карточка: выключить" "$o"
gw_calls | grep -qx 'gw inbound disable wg --json' && printf '%s' "$o" | grep -q 'Turn off WireGuard?' \
  && printf '%s' "$o" | grep -q '\[OK\] WireGuard turned off\.' && printf '%s' "$o" | flat | grep -q 'Devices on it (1) lose this protocol' \
  && ok "EN карточка: «Turn off» — вопрос цифрами, итог" || bad "EN выключить" "$(gw_seq) | $o"
# 3.3.7.15 F9: gw inbound set … --dry → «What changes» → «Apply» → --yes (the words of the core in its language)
gwf inbound_set.wg.once.json '{"ok": false, "error": "Port 5000/udp is in use: nginx listens on it.", "code": 3, "data": {}}'
gwf inbound_set.wg.once.rc 3
gwf inbound_set.wg.json '{"ok": true, "kind": "wg", "port": 5000, "changed": {"port": {"old": 51820, "new": 5000}}, "via_xray": false, "lines": ["WireGuard: what changes — port 51820 → 5000"], "reissue": [{"id": "d-00000001", "name": "Phone", "user_name": "Mom"}], "text": "Devices with the old settings of WireGuard won’t connect — issue the settings again: Phone (Mom).", "pending": null}'
: > "$CALLS/kaskadctl.log"
o=$(printf '2\nx\n\n2\n5000\n1\n1\n\n0\n' | en h1_card wg 2>&1 | strip)
en_clean "карточка: сменить порт" "$o"
gw_calls | grep -qx 'gw inbound set wg --port 5000 --yes --force --json' && printf '%s' "$o" | grep -q 'The port is a number from 1 to 65535\.' \
  && printf '%s' "$o" | grep -q 'Take port 5000 anyway?' && printf '%s' "$o" | grep -q '^ *1) Apply$' \
  && printf '%s' "$o" | grep -q '\[OK\] WireGuard: port 5000\.$' && printf '%s' "$o" | flat | grep -q 'issue the settings again: Phone (Mom)' \
  && ok "EN «Change port»: неверный порт, занят → «всё равно?» → --force, «Apply», кому выдать заново" || bad "EN смена порта" "$(gw_seq) | $o"
rm -f "$FAKE_DIR/gw/inbound_set.wg.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n1\n\n' | en h1_card wg 2>&1 | strip)
en_clean "карточка: удалить" "$o"
gw_calls | grep -qx 'gw inbound uninstall wg --json' && printf '%s' "$o" | flat | grep -q 'Remove WireGuard: its service and settings' \
  && printf '%s' "$o" | grep -q '\[OK\] WireGuard removed\. Port and server keys are kept' \
  && ok "EN «Remove protocol»: предупреждение, вопрос, итог" || bad "EN удаление" "$(gw_seq) | $o"
# не установленный: установка (порт из плана ядра), PPTP — с предупреждением
gwf inbound_plan.json '{"ok": true, "inbounds": [{"kind": "sstp", "title": "SSTP", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["tcp"], "port": 4433, "default": 4433, "conflicts": [{"detail": "nginx"}], "free": 4434}, "fixed": [], "kept": true}]}'
o=$(printf 'x\n1\n\n\n0\n' | en h1_card sstp 2>&1 | strip)
en_clean "карточка: установить" "$o"
printf '%s' "$o" | grep -q 'Not installed\.' && printf '%s' "$o" | grep -q 'No such item: 1 — install, 0 — back\.' \
  && printf '%s' "$o" | grep -q 'Port 4433 is in use: nginx\. Free: 4434\.' \
  && printf '%s' "$o" | grep -q 'INSTALL sstp 4434' \
  && ok "EN не установленный → «Install»: проверка портов, занятый — свободный взамен" || bad "EN установка" "$o"
o=$(printf '1\n2\n\n0\n' | en h1_card pptp 2>&1 | strip)
en_clean "карточка: PPTP" "$o"
printf '%s' "$o" | grep -q 'PPTP — weak encryption' && printf '%s' "$o" | grep -q 'Install PPTP anyway?' && ! printf '%s' "$o" | grep -q 'INSTALL' \
  && ok "EN PPTP: предупреждение, «2» — не ставится" || bad "EN PPTP" "$o"
# L2TP без шифрования, UDP через SOCKS5, центр сертификации IKEv2
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n1\n\n0\n' | en h1_card l2tp 2>&1 | strip)
en_clean "карточка: L2TP без шифрования" "$o"
gw_calls | grep -qx 'gw inbound option l2tp plain on --json' && printf '%s\n' "$o" | grep -qx ' 2) L2TP without encryption (for routers without IPsec): off → turn on' \
  && printf '%s' "$o" | grep -q '\[OK\] Saved\.' && ok "EN L2TP без шифрования: пункт, предупреждение, итог" || bad "EN L2TP" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n\n0\n' | en h1_card socks5 2>&1 | strip)
en_clean "карточка: UDP через SOCKS5" "$o"
gw_calls | grep -qx 'gw inbound option socks5 udp off --json' && printf '%s\n' "$o" | grep -qx ' 3) UDP over SOCKS5: on → turn off' \
  && printf '%s' "$o" | grep -q '\[OK\] UDP over SOCKS5: off\.' && ok "EN UDP через SOCKS5: пункт и итог" || bad "EN SOCKS5 UDP" "$(gw_seq) | $o"
gwf pki.rotate.json '{"ok": true, "kind": "ikev2", "created": [], "devices": [], "host": "203.0.113.10", "apply": null}'
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n1\n\n0\n' | en h1_card ikev2 2>&1 | strip)
en_clean "карточка: центр сертификации IKEv2" "$o"
gw_calls | grep -qx 'gw pki rotate ikev2 --yes --json' && printf '%s\n' "$o" | grep -qx ' 3) Change IKEv2 certificate authority' \
  && printf '%s' "$o" | flat | grep -q 'New IKEv2 certificate authority: vouches only for 203.0.113.10\.' \
  && ok "EN IKEv2: смена центра сертификации" || bad "EN IKEv2" "$(gw_seq) | $o"

# ── VLESS: режим, сайт маскировки, проверка, ключи узла ──
gw_st true '{"vless": {"enabled": true, "port": 443}}' 2 1
gwf inbound_list.json "$(gw_list "" | jq -c '.inbounds += [{"kind": "vless", "title": "VLESS", "engine": "", "available": true, "installed": true, "enabled": true, "running": true, "ports": ["443/tcp"], "devices": 2, "sessions": 1}]')"
gwf vless.show.json '{"ok": true, "kind": "vless", "enabled": true, "port": 443, "security": "reality", "mask_list": "int", "sni": "www.microsoft.com", "mask": "list", "mask_check": {"ok": true, "tls": "TLSv1.3", "h2": true, "why": ""}, "tls_block": "no site on the domain", "devices": 2, "node_domain": "vpn.example.com", "reason": "no_site", "forced": true, "reason_text": "the site on the domain is off", "fix": {"do": "site_on", "text": "Switch the site on the domain on"}}'
gwf vless.masks.json '{"ok": true, "current": "www.microsoft.com", "list_title": "Foreign sites", "country": {"code": "NL"}, "masks": [
 {"sni": "vpn.example.com", "title": "own", "mask": "own", "check": {"ok": true, "tls": "TLSv1.3", "h2": false}},
 {"sni": "www.microsoft.com", "title": "Microsoft", "mask": "list", "check": {"ok": false}},
 {"sni": "dl.google.com", "title": "Google downloads", "mask": "list", "check": null}]}'
gwf vless.mask.json '{"ok": true, "kind": "vless", "sni": "dl.google.com", "mask": "list", "mask_check": {"ok": true, "h2": true}, "changed": true, "text": "Reissue VLESS links: Phone (Mom).", "apply": null}'
: > "$CALLS/kaskadctl.log"
# карточка → 3 «VLESS masking» → 2 (вынужденный Reality: сразу список) → 3 → пауза → 0 → 4 ключи → 1 → пауза → 0
o=$(printf '3\n2\n3\n\n0\n4\n1\n\n0\n' | en h1_card vless 2>&1 | strip)
en_clean "VLESS: карточка, маскировка, сайт, ключи" "$o"
printf '%s\n' "$o" | grep -qx 'Mode: Reality — masking as someone else’s site (international sites)\.' \
  && printf '%s\n' "$o" | grep -qx 'TLS with own domain isn’t possible now: no site on the domain\.' \
  && printf '%s\n' "$o" | grep -qx 'Masking site: www.microsoft.com (from the list) · check: ✓ TLS 1.3, HTTP/2' \
  && printf '%s\n' "$o" | grep -qx ' 3) VLESS masking' && printf '%s\n' "$o" | grep -qx ' 4) New server keys' \
  && printf '%s\n' "$o" | grep -qx ' 5) Remove protocol' && ! printf '%s' "$o" | grep -q 'Match the mode' \
  && ok "EN карточка VLESS: режим, «TLS нельзя», сайт маскировки с проверкой, пункт «VLESS masking» вместо двух" || bad "EN карточка VLESS" "$o"
printf '%s' "$o" | grep -q 'Gateway: VLESS — masking' && printf '%s\n' "$o" | grep -qx 'Now: Reality, masking site — www.microsoft.com\.' \
  && printf '%s\n' "$o" | grep -qx 'Why not TLS: the site on the domain is off\.' \
  && printf '%s\n' "$o" | grep -qx ' 1) Own certificate (TLS) — recommended   \[domain vpn.example.com\]' \
  && printf '%s\n' "$o" | grep -qx ' 2) Reality — if there is no domain or you choose it on purpose' \
  && printf '%s' "$o" | grep -q 'Choice \[2\]' \
  && ok "EN «VLESS masking»: режим, причина, пункты 1/2 с доменом, по умолчанию — текущий (Reality → 2)" || bad "EN VLESS masking" "$o"
printf '%s' "$o" | grep -q 'Gateway: VLESS — masking site (Reality)' && printf '%s' "$o" | grep -q '^Now: www.microsoft.com$' \
  && printf '%s' "$o" | grep -q '^List: Foreign sites — server country NL\.$' \
  && printf '%s' "$o" | grep -q "^  1) vpn.example.com    the server’s own site · ✓ TLS 1.3 (no HTTP/2)$" \
  && printf '%s' "$o" | grep -q '^  2) www.microsoft.com  ✗ no answer$' && printf '%s' "$o" | grep -q '^  3) dl.google.com      Google downloads$' \
  && printf '%s' "$o" | grep -q '^  4) Check all sites from the server$' && printf '%s' "$o" | grep -q '^  6) Pick automatically$' \
  && printf '%s' "$o" | grep -q 'Checking dl.google.com from the server…' \
  && printf '%s' "$o" | grep -q '\[OK\] Masking site: dl.google.com — ✓ TLS 1.3, HTTP/2\.' \
  && gw_calls | grep -qx 'gw vless mask dl.google.com --json' \
  && ok "EN вынужденный Reality: сразу список сайтов без согласия, выбор, итог" || bad "EN сайт маскировки" "$(gw_seq) | $o"
printf '%s' "$o" | grep -q 'Change the VLESS server keys?' && printf '%s' "$o" | grep -q '\[OK\] New VLESS server keys\.' \
  && gw_calls | grep -qx 'gw vless rekey --yes --json' && ! gw_calls | grep -q 'gw vless sync' \
  && ok "EN «New server keys»: вопрос, итог; «gw vless sync» из меню не зовётся" || bad "EN ключи VLESS" "$(gw_seq) | $o"
# «Pick automatically», когда ядро просит назвать сайт (нужно согласие): сайт подбирает меню и называет (придирка 5)
gwf vless.mask.once.json '{"ok": false, "error": "Pick the masking site first (gw vless masks --check)", "code": 3, "data": {"need": "pick_first", "kind": "vless"}}'
printf '3\n' > "$FAKE_DIR/gw/vless.mask.once.rc"
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n2\n6\n\n0\n0\n' | en h1_card vless 2>&1 | strip)
en_clean "VLESS: подобрать автоматически при согласии" "$o"
printf '%s' "$o" | grep -q 'Picked site: vpn.example.com\.' && ! printf '%s' "$o" | grep -q 'gw vless masks --check' \
  && gw_calls | grep -qx 'gw vless mask auto --json' && gw_calls | grep -qx 'gw vless masks --check --json' \
  && gw_calls | grep -qx 'gw vless mask own --json' \
  && ok "EN «Pick automatically» при нужном согласии: меню подобрало сайт и назвало, без ошибки CLI" || bad "EN авто при согласии" "$(gw_seq) | $o"
# TLS недоступен (нет домена): пункт 1 с причиной; выбор — причина и что сделать, ничего не меняется
gwf vless.show.json '{"ok": true, "kind": "vless", "security": "reality", "sni": "www.microsoft.com", "mask": "list", "mask_check": null, "node_domain": "", "reason": "no_domain", "forced": true, "reason_text": "the server has no domain", "fix": {"do": "domain", "text": "Add a domain: Settings → Domain and site"}}'
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n1\n\n0\n0\n' | en h1_card vless 2>&1 | strip)
en_clean "VLESS: TLS недоступен" "$o"
printf '%s\n' "$o" | grep -qx ' 1) Own certificate (TLS) — not available: the server has no domain' \
  && printf '%s\n' "$o" | grep -qx 'TLS isn’t possible now: the server has no domain\.' \
  && printf '%s\n' "$o" | grep -qx 'What to do: Add a domain: Settings → Domain and site\.' \
  && ! gw_calls | grep -q 'gw vless mode' \
  && ok "EN TLS недоступен: пункт 1 с причиной, выбор — причина и «что сделать», без вызова ядра" || bad "EN TLS недоступен" "$(gw_seq) | $o"
# Reality, TLS можно (устройства): 1 → цена (по умолчанию «Назад») → 1 → тот же переход с --yes
gwf vless.show.json '{"ok": true, "kind": "vless", "security": "reality", "sni": "www.microsoft.com", "mask": "list", "mask_check": null, "node_domain": "vpn.example.com", "reason": "ready", "forced": false, "reason_text": "TLS can be switched on now", "recommend": {"mode": "tls", "devices": 2, "text": "You can switch to TLS with your own domain."}}'
gwf vless.mode.once.json '{"ok": false, "error": "VLESS goes on TLS: give the links again (2 devices: Phone (Mom), Laptop (Mom)).", "code": 3, "data": {"need": "vless_change", "text": "VLESS goes on TLS: give the links again (2 devices: Phone (Mom), Laptop (Mom))."}}'
printf '3\n' > "$FAKE_DIR/gw/vless.mode.once.rc"
gwf vless.mode.json '{"ok": true, "kind": "vless", "changed": true, "security": "tls", "text": "Reissue VLESS links: Phone (Mom), Laptop (Mom)."}'
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n1\n1\n\n0\n0\n' | en h1_card vless 2>&1 | strip)
en_clean "VLESS: переход на TLS с ценой" "$o"
printf '%s' "$o" | grep -q 'You can switch to TLS with your own domain\.' \
  && printf '%s' "$o" | grep -q 'give the links again (2 devices: Phone (Mom), Laptop (Mom))' \
  && printf '%s\n' "$o" | grep -qx ' 1) Switch to TLS and give the keys out again' && printf '%s\n' "$o" | grep -qx ' 2) Back' \
  && printf '%s' "$o" | grep -q '\[OK\] VLESS: TLS with own domain\.' && printf '%s' "$o" | grep -q 'Reissue VLESS links: Phone (Mom), Laptop (Mom)\.' \
  && [ "$(gw_calls | grep -c '^gw vless mode tls --via ssh')" = 2 ] && gw_calls | grep -qx 'gw vless mode tls --via ssh --yes --json' \
  && ok "EN «Own certificate (TLS)»: рекомендация, цена до действия цифрами, --yes, итог и кому заново" || bad "EN переход на TLS" "$(gw_seq) | $o"
# TLS сейчас: 2 → список сайтов → сайт → текст рисков (по умолчанию «Назад») → «Понимаю риски» → согласие флагами
gwf vless.show.json '{"ok": true, "kind": "vless", "security": "tls", "domain": "vpn.example.com", "node_domain": "vpn.example.com", "sni": "", "mask": "own", "mask_check": null, "reason": "", "forced": false}'
gwf vless.mode.once.json '{"ok": false, "error": "Show the risks first", "code": 3, "data": {"need": "reality_consent", "kind": "vless", "title": "Reality risks", "block": "Reality makes the server look like dl.google.com. RISK-BLOCK-TEXT", "risks": "r1a", "site": "dl.google.com"}}'
printf '3\n' > "$FAKE_DIR/gw/vless.mode.once.rc"
gwf vless.mode.json '{"ok": true, "kind": "vless", "changed": true, "security": "reality", "sni": "dl.google.com", "port": 8443, "text": "Reissue VLESS links: Phone (Mom)."}'
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n2\n3\n1\n\n0\n0\n' | en h1_card vless 2>&1 | strip)
en_clean "VLESS: TLS → Reality с согласием" "$o"
printf '%s\n' "$o" | grep -qx 'Mode: TLS with own domain vpn.example.com — port 443; anything else gets the server’s site.' \
  && ! printf '%s' "$o" | grep -q 'Change port' && printf '%s' "$o" | grep -q 'Choice \[1\]' \
  && printf '%s' "$o" | grep -q 'RISK-BLOCK-TEXT' && printf '%s\n' "$o" | grep -qx ' 1) I understand the risks, choose Reality' \
  && printf '%s' "$o" | grep -q '\[OK\] VLESS: Reality, masking site dl.google.com, port 8443\.' \
  && gw_calls | grep -qx 'gw vless mode reality --site dl.google.com --via ssh --json' \
  && gw_calls | grep -qx 'gw vless mode reality --site dl.google.com --via ssh --accept-risks --risks r1a --json' \
  && ! gw_calls | grep -q 'gw vless mask ' \
  && ok "EN TLS → Reality: по умолчанию TLS, сайт, текст рисков, согласие цифрами (--accept-risks --risks), итог" || bad "EN TLS → Reality" "$(gw_seq) | $o"
# отказ от рисков (Enter — «Назад»): ничего не меняется
gwf vless.mode.once.json '{"ok": false, "error": "Show the risks first", "code": 3, "data": {"need": "reality_consent", "kind": "vless", "title": "Reality risks", "block": "RISK-BLOCK-TEXT", "risks": "r1a", "site": "dl.google.com"}}'
printf '3\n' > "$FAKE_DIR/gw/vless.mode.once.rc"
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n2\n3\n\n0\n0\n' | en h1_card vless 2>&1 | strip)
[ "$(gw_calls | grep -c 'gw vless mode reality')" = 1 ] && ! gw_calls | grep -q -- '--accept-risks' \
  && ok "EN текст рисков: Enter — «Назад», согласие не передано" || bad "EN отказ от рисков" "$(gw_seq) | $o"
rm -f "$FAKE_DIR/gw/vless.mode.json"
o=$(printf '2\n1\n\n0\n0\n' | lib h1_card vless 2>&1 | strip)
printf '%s\n' "$o" | grep -qx 'Режим: TLS со своим доменом vpn.example.com — порт 443; всё постороннее открывает сайт узла.' \
  && printf '%s\n' "$o" | grep -qx ' 2) Маскировка VLESS' \
  && printf '%s\n' "$o" | grep -qx ' 1) Свой сертификат (TLS) — рекомендуем   \[домен vpn.example.com\]' \
  && printf '%s\n' "$o" | grep -qx ' 2) Reality — если нет домена или вы сознательно выбираете его' \
  && printf '%s' "$o" | grep -q 'VLESS уже на TLS со своим доменом — ничего не меняется\.' \
  && ok "RU «Маскировка VLESS»: режим TLS, пункты, повтор TLS ничего не меняет" || bad "RU VLESS TLS" "$o"

# ── выдача: шаги, записи, QR, файлы, сохранение ──
gw_st true '{"awg3": {}}' 1 1
GW_ISSUE_EN='{"ok": true, "device": "d-00000001", "name": "Phone", "kind": "awg3", "os": "android", "available": ["awg3", "wg"],
 "howto": ["Install AmneziaVPN from Google Play.", "Copy the link below.", "In AmneziaVPN: “+” → “Paste” → “Connect”."],
 "items": [
 {"format": "vpn", "title": "AmneziaWG 3.1 — vpn:// link", "body": "vpn://SECRETVPN", "filename": "", "notes": ["AmneziaVPN: “+” → “Paste from clipboard”."]},
 {"format": "qr", "title": "AmneziaWG 3.1 — QR for AmneziaVPN", "body": ["vpn://SECRETVPN"], "filename": "", "notes": ["Scan it."]},
 {"format": "conf", "title": "AmneziaWG 3.1 — .conf file", "body": "[Interface]\nPrivateKey = SECRETKEY\n", "filename": "kaskad-awg3.conf", "notes": []},
 {"format": "text", "title": "Certificate", "body": "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----", "filename": "kaskad-ca.crt", "notes": []},
 {"format": "fields", "title": "Connection data", "body": [["Login", "phone-1a2b"], ["Password", "SECRETPASS"]], "filename": "", "notes": []}]}'
gwf issue.json "$GW_ISSUE_EN"
h1_issue() { K3_TEST_TTY=1; h1_stubs; k3_gw_issue_screen d-00000001 phone-1a2b; echo "RC=$?"; }
: > "$CALLS/kaskadctl.log"
o=$(printf '\n\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 en h1_issue 2>&1 | strip)
en_clean "выдача в окне 80×24: шаги, записи, пауза" "$o"
printf '%s' "$o" | grep -q 'Settings: Phone — AmneziaWG 3.1' && printf '%s' "$o" | grep -q 'This is the device key' \
  && printf '%s' "$o" | grep -q 'How to connect (Platform android):' && printf '%s' "$o" | grep -q '────────────── start ───────────────' \
  && printf '%s' "$o" | grep -q '─────────────── end ────────────────' && printf '%s' "$o" | grep -q 'QR code — point your phone camera at it:' \
  && printf '%s' "$o" | grep -q '(file kaskad-ca.crt)' && printf '%s' "$o" | grep -q 'Don’t copy it from the screen' \
  && printf '%s' "$o" | grep -q 'Enter — more, 0 — back to the actions: ' \
  && ok "EN выдача: заголовок, предупреждение, шаги, разделители, QR, файл строкой, пауза" || bad "EN выдача: экран" "$o"
o=$(printf '9\n3\n4\n7\n4\n2\n1\n\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=100 en h1_issue 2>&1 | strip)
en_clean "выдача: действия, текст файлов, другой вход, сохранение" "$o"
printf '%s\n' "$o" | grep -qx ' 1) Save to files — to download to a computer (QR codes as images)' \
  && printf '%s\n' "$o" | grep -qx ' 3) Show file text (kaskad-ca.crt)' && printf '%s\n' "$o" | grep -qx ' 4) Another protocol (AmneziaWG 3.1, WireGuard)' \
  && printf '%s\n' "$o" | grep -qx ' 0) Done' && printf '%s' "$o" | grep -q 'No such item: enter a number from the list, 0 — done\.' \
  && printf '%s' "$o" | grep -q 'File text: Phone' \
  && printf '%s' "$o" | grep -q '^No such item\.$' && gw_calls | grep -qx 'gw issue d-00000001 --inbound wg --json' \
  && ok "EN выдача: действия, «Show file text», «Another protocol» (неверный номер — словами)" || bad "EN выдача: действия" "$(gw_seq) | $o"
D="$KASKAD_ROOT/root/kaskad-issue/phone-1a2b"
printf '%s' "$o" | grep -q 'Files saved: ' && printf '%s' "$o" | flat | grep -q 'Download it to your computer — run this command there' \
  && printf '%s' "$o" | grep -qF "scp -r root@203.0.113.10:$D ." && printf '%s' "$o" | flat | grep -qF "Or open the folder $D in WinSCP" \
  && printf '%s' "$o" | grep -q 'Once downloaded, delete them from the server:' \
  && grep -q "GW: settings of device phone-1a2b saved to $D" "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "EN «Save to files»: итог, команда scp, WinSCP, удаление; журнал — на английском" || bad "EN сохранение" "$o"
# QR больше окна; без qrencode (ставить отказались)
o=$(printf '0\n' | K3_TERM_COLS=80 QR_FAKE_W=117 en h1_issue 2>&1 | strip)
en_clean "выдача: QR не влезает" "$o"
printf '%s\n' "$o" | flat | grep -qF ' The QR code doesn’t fit in the window (needs 117×61, now 80×100) — use the link.' \
  && printf '%s' "$o" | flat | grep -q 'QR codes can be saved as images: “1) Save to files” below' \
  && ok "EN QR больше окна: какое окно нужно и про картинки" || bad "EN широкий QR" "$o"
o=$(printf '2\n0\n' | K3_QRENCODE="$W/no-qrencode" en h1_issue 2>&1 | strip)
en_clean "выдача: нет qrencode" "$o"
printf '%s' "$o" | grep -q 'QR codes need the qrencode package. Install it?' && printf '%s' "$o" | grep -q '2) Don’t install' \
  && printf '%s\n' "$o" | grep -qx '  QR code not shown: qrencode isn’t installed — use the link.' \
  && ok "EN без qrencode: вопрос цифрами, строка вместо кода" || bad "EN без qrencode" "$o"
o=$(printf '2\n0\n' | K3_QRENCODE="$W/no-qrencode" lib h1_issue 2>&1 | strip)
printf '%s\n' "$o" | grep -qx '  QR-код не показан: нет программы qrencode — возьмите ссылку.' \
  && printf '%s' "$o" | grep -q '────────────── начало ──────────────' && printf '%s' "$o" | grep -q '────────────── конец ───────────────' \
  && ok "RU выдача после перевода — прежние строки (QR не показан, разделители)" || bad "RU выдача" "$o"

# ── новое устройство: платформа, имя, входы, выдача; первое устройство ──
gw_st true '{"wg": {}, "awg2": {}}' 0 1
gwf inbound_list.json "$(gw_list "wg")"
gwf offers.json '{"ok": true, "platform": "ios", "notes": ["AmneziaWG 2.0 needs AmneziaVPN 4.8 or newer."], "offers": [
 {"inbound": "awg2", "format": "conf", "note": "AmneziaVPN", "title": "AmneziaWG 2.0", "installed": false, "enabled": false}]}'
gwf device_add.json '{"ok": true, "device": {"id": "d-00000004", "login": "phone-0004", "name": "Phone", "access": ["wg"]}}'
gwf issue.json "$GW_ISSUE_EN"
h1_devnew() { K3_TEST_TTY=1; h1_stubs; k3_gw_device_new u-00000001; echo "RC=$?"; }
: > "$CALLS/kaskadctl.log"
o=$(printf '99\n1\n\n\n5\n1\n\n0\n' | en h1_devnew 2>&1 | strip)
en_clean "новое устройство: платформа, имя, входы" "$o"
printf '%s' "$o" | grep -q 'What kind of device is it?' && printf '%s\n' "$o" | grep -qx '  0) Back' && printf '%s' "$o" | grep -q '^No such item\.$' \
  && printf '%s' "$o" | grep -q 'Device protocols (Platform ios):' \
  && printf '%s' "$o" | flat | grep -q 'The recommended protocols for this platform (AmneziaWG 2.0) aren’t installed' \
  && printf '%s' "$o" | grep -q 'Mark at least one protocol\.' && printf '%s' "$o" | grep -q 'No such item: a number from 1 to 1, Enter — done, 0 — back\.' \
  && printf '%s' "$o" | grep -q '\[OK\] Device “Phone” added, login phone-0004\.' \
  && gw_calls | grep -qx 'gw device add u-00000001 Phone --kind phone --platform ios --access wg --json' && gw_calls | grep -qx 'gw issue d-00000004 --json' \
  && ok "EN новое устройство: платформа (неверный номер — словами), имя по умолчанию, входы (ничего не отмечено — сказано), выдача" \
  || bad "EN новое устройство" "$(gw_seq) | $o"
h1_first() { K3_TEST_TTY=1; h1_stubs; k3_gw_first_device; echo "RC=$?"; }
gw_st true '{"wg": {}}' 0 0
o=$(printf '0\n' | en h1_first 2>&1 | strip)
en_clean "первое устройство: новый пользователь" "$o"
printf '%s' "$o" | grep -q 'Gateway: first device (step 6 of 6)' && printf '%s' "$o" | grep -q 'Create the first user and their device' \
  && ok "EN первое устройство: новый пользователь" || bad "EN первое устройство" "$o"
gw_st true '{"wg": {}}' 2 1
o=$(printf '\n' | en h1_first 2>&1 | strip)
en_clean "первое устройство: остались с прошлой установки" "$o"
printf '%s' "$o" | grep -q 'Users and devices remain from the previous installation (devices: 2)\.' \
  && printf '%s' "$o" | grep -q 'again. New device: “Gateway → Users & devices → Add”\.' \
  && ok "EN первое устройство: пользователи и устройства остались — прежние порты и ключи" || bad "EN первое устройство: остались" "$o"
o=$(printf '\n' | lib h1_first 2>&1 | strip)
printf '%s' "$o" | grep -q 'Пользователи и устройства остались с прошлой установки (устройств: 2)\.' \
  && printf '%s' "$o" | grep -q 'работают. Новое устройство: «Шлюз → Пользователи и устройства → Добавить»\.' \
  && ok "RU первое устройство после перевода — прежние строки" || bad "RU первое устройство" "$o"
# проверка сайта маскировки в карточке VLESS — цветом итога: ✓ зелёным (до 3.3.2 подпись «проверка:» делала его жёлтым)
gw_st true '{"vless": {"enabled": true, "port": 443}}' 2 1
gwf inbound_list.json "$(gw_list "" | jq -c '.inbounds += [{"kind": "vless", "title": "VLESS", "engine": "", "available": true, "installed": true, "enabled": true, "running": true, "ports": ["443/tcp"], "devices": 2, "sessions": 1}]')"
gwf vless.show.json '{"ok": true, "kind": "vless", "security": "reality", "mask_list": "int", "sni": "www.microsoft.com", "mask": "list", "mask_check": {"ok": true, "h2": true}}'
o=$(printf '0\n' | lib h1_card vless 2>&1)
printf '%s' "$o" | grep -q $'(из списка) · \e\\[0;32mпроверка: ✓ TLS 1.3, HTTP/2' \
  && ok "карточка VLESS: «проверка: ✓» — зелёным" || bad "цвет проверки сайта маскировки" "$(printf "%s" "$o" | strip | head -8)"
