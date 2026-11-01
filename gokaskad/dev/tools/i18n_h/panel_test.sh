# Раздел 49H (подключает tools/k3_install_test.sh): меню 18 «Веб-панель» (ключи ИИ-агентов, F1) и меню 19 «Домен и
# сайт» (до шаблонов) на английском — области каталогов panel (PNL_…) и site (SITE_…). Данные подделок — латиницей.
echo "  · panel/site: меню 18 «Веб-панель», ключи ИИ-агентов, F1, меню 19 «Домен и сайт» (H4)"
use_root i18n-h4

# Помощники — свои (h4_…): раздел должен работать и в быстром прогоне run_section.sh 49H.
h4_scr() { clear() { printf '\f'; }; K3_TEST_TTY=1; K3_PAUSE_SEC=0; "$@"; }
h4_sitef() { mkdir -p "$FAKE_DIR/site"; printf '%s\n' "$2" > "$FAKE_DIR/site/$1"; }
h4_panelf() { mkdir -p "$FAKE_DIR/panel"; printf '%s\n' "$2" > "$FAKE_DIR/panel/$1"; }
h4_flat() { tr '\n' ' ' | tr -s ' '; }
# Подделке kaskadctl нужна группа panel (её добавляет раздел WP-I); в быстром прогоне — добавить так же.
grep -q '"panel "\*' "$W/kaskadctl.fake" || python3 - "$W/kaskadctl.fake" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read(); mark = '  "gw "*)\n'
add = ('  "panel "*)\n    f="$FAKE_DIR/$1/$2"\n    [ -f "$f.err" ] && cat "$f.err" >&2\n'
       '    if [ -f "$f.json" ]; then cat "$f.json"; else echo \'{"ok": true}\'; fi\n'
       '    exit "$(cat "$f.rc" 2>/dev/null || echo 0)" ;;\n')
open(p, "w").write(s.replace(mark, add + mark, 1))
PY
H4_DNS_OK='{"state": "ok", "a": ["203.0.113.10"], "aaaa": [], "own": ["203.0.113.10"], "cloudflare": false, "ready": true, "advice": null}'
H4_DNS_NONE='{"state": "none", "a": [], "aaaa": [], "own": ["203.0.113.10"], "cloudflare": false, "ready": false, "advice": "The domain vpn.example.com has no A record: add one with the value 203.0.113.10."}'
H4_LE='{"kind": "letsencrypt", "names": ["vpn.example.com"], "not_after": "2026-12-30T10:00:00Z", "days_left": 83, "staging": false}'
H4_SELF='{"kind": "selfsigned", "names": ["203.0.113.10"], "not_after": "2036-10-01T00:00:00Z", "days_left": 3646, "staging": false}'
H4_AFF='[{"device": "d-00000001", "name": "Phone", "kind": "sstp", "user_name": "Anna"}, {"device": "d-00000002", "name": "Laptop", "kind": "sstp", "user_name": "Anna"}]'
H4_F2='SSTP: reissue the settings of Phone (Anna), Laptop (Anna).'
# h4_st <cert> <dns> <use.sstp> <host_is_domain> [use.panel] [mode] [email]
h4_st() {
    printf '{"ok": true, "domain": "vpn.example.com", "domain_unicode": "vpn.example.com", "mode": "%s", "cert": %s, "dns": %s, "port80": {"free": true, "who": null}, "use": {"panel": %s, "pages": %s, "sstp": %s, "ocserv": false}, "use_impact": {"sstp": 2, "ocserv": 0}, "use_impact_total": 2, "host_is_domain": %s, "email": "%s", "site": {"template": {"id": "t-1", "name": "Coffee House"}, "panel_url_masked": "https://vpn.example.com/app-AbCd••••/", "port443": {"free": true}}}' \
        "${6:-domain}" "$1" "$2" "${5:-false}" "${5:-false}" "$3" "$4" "${7:-}"
}
h4_gw() {   # state.json: шлюз настроен, SSTP стоит, $1 устройств
    local devs="" i
    for ((i = 1; i <= ${1:-0}; i++)); do devs+="${devs:+, }{\"id\": \"d-0000000$i\"}"; done
    printf '{\n  "engine": "nft",\n  "gateway": {"enabled": true, "host": "203.0.113.10", "inbounds": {"sstp": {}}, "devices": [%s], "users": [{"id": "u-00000001"}], "routes": {"default": {"via": "pool", "fail": "block"}}},\n  "schema": 1\n}\n' "$devs" > "$KASKAD_DIR/state.json"
}

# ── статика: в коде функций меню 18 и 19 нет русского текста
i18n_code "меню 18 (веб-панель, ключи ИИ-агентов, F1)" 'k3_panel_(menu|public_on|password|reveal)' 'k3_mcp_keys_menu'
i18n_code "меню 19 (домен и сайт, до шаблонов)" \
    'k3_site_(uname|copy|cert_end|cert_within|menu_state|has_le|pkgs_ours|dns_word|title|fail|warns|load|cert_text|users_text|head_body|menu|wizard|dns_show|dns_offer_wait|dns_wait|dns_step|issue_ask|issue|cert_menu|use_wanted|host_wanted|offers_wanted|after_issue|offer_host|use_menu|use_toggle|email|clear)'

# ── 18: ядра нет — и у панели, и у домена
t_h4_nocore() { k3_lib_ok() { return 1; }; K3_LIB_ERR="no core"; k3_panel_menu; k3_site_menu; }
o=$(printf '\n\n' | en t_h4_nocore 2>&1 | strip)
en_clean "18 и 19 без ядра" "$o"
printf '%s' "$o" | grep -qx 'The Kaskad 3 core is unavailable (no core) — the panel needs it. Update Kaskad (item 11).' \
  && printf '%s' "$o" | grep -q '— the domain settings need it' && ok "18 и 19 без ядра — причина по-английски" || bad "18/19 без ядра" "$o"

# ── 18: панель не установлена → установить; ключи ИИ-агентов из этого же экрана
use_root i18n-h4-p1; fake_ctl; engine_nft; h4_panelf status.json '{"ok": true, "installed": false}'
t_h4_panel() { h4_scr k3_panel_menu; }
o=$(printf '7\n1\n\n0\n' | en t_h4_panel 2>&1 | strip)
en_clean "18: панели нет" "$o"
printf '%s' "$o" | grep -qx 'Status: not installed.' && printf '%s' "$o" | grep -qx ' 1) Install and start' \
  && printf '%s' "$o" | grep -qx ' 2) 🤖 AI agent keys (MCP)' && printf '%s' "$o" | grep -q 'No such item: 1 — install, 2 — AI agent keys, 0 — back.' \
  && ctl_log | grep -qx 'panel install' && ok "18, панели нет: «Status: not installed.», «1) Install and start» → panel install, 7 — «No such item»" \
  || bad "18: панели нет (en)" "$(ctl_log) | $o"

# ── 18: панель работает, открыта из интернета, пароля нет: ссылка, пароль, доступ, пароль в туннеле, удаление (DELETE)
use_root i18n-h4-p2; fake_ctl; engine_nft
h4_panelf status.json '{"ok": true, "installed": true, "active": true, "public": {"on": true, "port": 41234}, "require_password": true,
  "password": false, "url": "https://203.0.113.10:41234/", "fingerprint": "AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89",
  "tunnel": "ssh -L 1980:127.0.0.1:1980 root@203.0.113.10"}'
o=$(printf '1\n\n2\n9\n0\n\n3\n1\n\n4\n1\n\n9\n5\n1\nDELETE\n\n' | en t_h4_panel 2>&1 | strip)   # 3 → «1) Yes, turn it off» (T-M6-332b №13); 4 → «1) Yes, stop asking» (M6 332c №3)
en_clean "18: панель работает" "$o"
printf '%s' "$o" | grep -qx 'Status: running' && printf '%s' "$o" | grep -qx 'Through an SSH tunnel — on your computer:' \
  && printf '%s' "$o" | grep -qx '  then in a browser: http://127.0.0.1:1980/' && printf '%s' "$o" | grep -qx 'From the internet: https://203.0.113.10:41234/' \
  && printf '%s' "$o" | grep -qx '  SHA-256 certificate fingerprint:' && printf '%s' "$o" | grep -qx 'Password: not set · required in the tunnel too' \
  && printf '%s' "$o" | grep -qx ' 3) Access from the internet: on → turn off' && printf '%s' "$o" | grep -qx " 4) Password in the SSH tunnel: required → don’t ask" \
  && printf '%s' "$o" | grep -q "The link works like a password: don’t send it in the clear." \
  && printf '%s' "$o" | grep -q 'No such item: enter 1 or 2 (Enter — cancel).' && printf '%s' "$o" | grep -q 'No such item: a number from 1 to 6, 0 — back.' \
  && ctl_log | grep -qx 'panel link' && ctl_log | grep -qx 'panel public off' && ctl_log | grep -qx 'panel require-password off' \
  && printf '%s' "$o" | grep -q '^ *1) Yes, stop asking$' \
  && ctl_log | grep -qx 'panel remove --purge' \
  && ok "18 по-английски: состояние, туннель, отпечаток, пароль; 1–5 зовут ядро как раньше; удаление — слово DELETE (--purge)" \
  || bad "18: панель работает (en)" "$(ctl_log) | $o"
# слово на другом языке — без --purge (русское УДАЛИТЬ в английском меню — не подтверждение)
: > "$CALLS/kaskadctl.log"; o=$(printf '5\n1\nУДАЛИТЬ\n\n' | en t_h4_panel 2>&1 | strip)
ctl_log | grep -qx 'panel remove' && ! ctl_log | grep -q 'purge' && ok "18 → 5 на английском: УДАЛИТЬ — не слово подтверждения, панель удалена без --purge" \
  || bad "18 → 5: слово подтверждения" "$(ctl_log)"
# русский экран — прежний (слово УДАЛИТЬ, «Пароль: не задан · нужен и в туннеле»)
: > "$CALLS/kaskadctl.log"; o=$(printf '5\n1\nУДАЛИТЬ\n\n' | lib t_h4_panel 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Пароль: не задан · нужен и в туннеле' && printf '%s' "$o" | grep -qx ' 3) Доступ из интернета: включён → выключить' \
  && ctl_log | grep -qx 'panel remove --purge' \
  && ok "18 по-русски — прежний текст и слово УДАЛИТЬ (--purge)" || bad "18: русский экран" "$(ctl_log) | $o"

# ── 18 → 3: доступ из интернета без пароля — сначала пароль
use_root i18n-h4-p3; fake_ctl; engine_nft
h4_panelf status.json '{"ok": true, "installed": true, "active": false, "public": {"on": false}, "password": false, "tunnel": "ssh -L 1980:127.0.0.1:1980 root@x"}'
o=$(printf '3\n1\n\n0\n' | en t_h4_panel 2>&1 | strip)
en_clean "18 → 3: доступ из интернета" "$o"
printf '%s' "$o" | grep -q '^Status: not running (systemctl status ' && printf '%s' "$o" | grep -qx ' 3) Access from the internet: off → turn on' \
  && printf '%s' "$o" | grep -qx 'The panel opens on the public address over HTTPS: self-signed certificate,' \
  && printf '%s' "$o" | grep -qx 'Turn on access from the internet?' && printf '%s' "$o" | grep -qx ' 1) Turn on access from the internet' && printf '%s' "$o" | grep -qx ' 0) Back' \
  && printf '%s' "$o" | grep -q 'No password yet — generating one first' \
  && ctl_log | grep -qx 'panel password --generate' && ctl_log | grep -qx 'panel public on' \
  && ok "18 → 3 по-английски: что откроется, вопрос цифрами с «0) Back», пароль, потом public on" || bad "18 → 3 (en)" "$(ctl_log) | $o"

# ── 18 → 6: ключи ИИ-агентов (ядро — подмена k3ctl): список, добавить, удалить, как подключить, закрытый ключ
t_h4_keys() {
    k3ctl() {
        printf '%s\n' "$*" >> "$CALLS/kaskadctl.log"
        case "$1 $2 $3" in
            "mcp key list") printf '%s' '{"ok": true, "keys": [{"fingerprint": "SHA256:abcdefghijklmnopqrstuvwxyz0123456789", "type": "ssh-ed25519", "label": "laptop"}],
                "connect": {"claude_code": "claude mcp add kaskad -- ssh root@203.0.113.10 kaskad-mcp", "claude_desktop": {"mcpServers": {"kaskad": {"command": "ssh"}}}}}' ;;
            "mcp key add") cat >/dev/null; echo "Key OK: ssh-ed25519" ;;
            "mcp key del") echo "Key deleted." ;;
        esac
    }
    h4_scr k3_mcp_keys_menu
}
use_root i18n-h4-k; engine_nft
o=$(printf '9\n1\nssh-ed25519 AAAATEST me\nlaptop\n\n2\n1\n1\n\n3\n\n1\n-----BEGIN OPENSSH PRIVATE KEY-----\n' | en t_h4_keys 2>&1 | strip)
en_clean "18 → 6: ключи ИИ-агентов" "$o"
w=$(printf '%s' "$o" | h4_flat)
printf '%s' "$o" | grep -q '━━━ 🤖 AI agent keys (MCP) ━━━' && printf '%s' "$o" | grep -qx 'An agent key runs only kaskad-mcp: no shell, no tunnels.' \
  && printf '%s' "$o" | grep -qx 'Agent keys:' && printf '%s' "$o" | grep -qx ' 1) Add an agent key' && printf '%s' "$o" | grep -qx ' 2) Delete an agent key' \
  && printf '%s' "$w" | grep -q 'No such item: pick a number from the list, 0 — back.' \
  && printf '%s' "$o" | grep -qx 'Create a key on your computer:' && printf '%s' "$o" | grep -q '^  ssh-keygen -t ed25519' \
  && printf '%s' "$w" | grep -q 'Delete agent key SHA256:abcdefghijklmnopqrst…? The agent with it will disconnect right away and won’t connect again. If the server can’t tell which connection is whose, other agents may disconnect too' \
  && printf '%s' "$o" | grep -qx 'Claude Code — on your computer, one command:' && printf '%s' "$o" | grep -qx 'Claude Desktop — in claude_desktop_config.json, section "mcpServers":' \
  && printf '%s' "$w" | grep -qF "Output in root’s ~/.bashrc (greeting, echo) breaks the agent link — remove it or wrap it in if [ -t 1 ]." \
  && printf '%s' "$o" | grep -q 'This is a PRIVATE key — never share it.' \
  && ctl_log | grep -q '^mcp key add --label laptop' && ctl_log | grep -qx 'mcp key del SHA256:abcdefghijklmnopqrstuvwxyz0123456789' \
  && ok "18 → 6 по-английски: список, добавить (подпись), удалить (вопрос), как подключить (jq --arg), закрытый ключ — отказ" \
  || bad "18 → 6 (en)" "$(ctl_log) | $o"
t_h4_keys0() { k3ctl() { echo junk; }; h4_scr k3_mcp_keys_menu; }
o=$(printf '2\n0\n' | en t_h4_keys0 2>&1 | strip)
en_clean "18 → 6: ключей нет" "$o"
printf '%s' "$o" | grep -qx 'Agent keys: none.' && printf '%s' "$o" | grep -qx ' 2) Delete an agent key (no keys)' \
  && printf '%s' "$o" | grep -q 'No agent keys — nothing to delete.' && ok "18 → 6: ключей нет — «Agent keys: none.», 2 — «nothing to delete»" || bad "18 → 6: ключей нет" "$o"
o=$(printf '3\n\n0\n' | lib t_h4_keys0 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Ключей агентов: нет.' && printf '%s' "$o" | grep -qx 'Ядро не ответило — kaskadctl mcp key list.' \
  && ok "18 → 6 по-русски — прежний текст («Ключей агентов: нет.», ядро не ответило)" || bad "18 → 6: русский экран" "$o"

# ── F1 в главном меню (k3_panel_reveal): K3_MENU_NOTE по-английски
t_h4_reveal() {
    k3_head_load() { K3_H_PMASK=""; }; k3_panel_reveal; echo "N1=$K3_MENU_NOTE"
    k3_head_load() { K3_H_PMASK="x"; }; k3_site() { K3_GW_ERR="core is down"; return 1; }; k3_panel_reveal; echo "N2=$K3_MENU_NOTE"
    k3_site() { K3_GW_OUT='{"url": "javascript:x"}'; return 0; }; k3_panel_reveal; echo "N3=$K3_MENU_NOTE"
}
o=$(en t_h4_reveal 2>&1)
en_clean "F1: адрес панели" "$o"
printf '%s' "$o" | grep -qx 'N1=The panel is not open from the internet — nothing to show (items 18, 19).' \
  && printf '%s' "$o" | grep -qx "N2=Address not received: core is down" \
  && printf '%s' "$o" | grep -qx 'N3=The core returned an unclear address — open 19 → 8.' \
  && ok "F1 по-английски: панель не открыта из интернета, ядро не ответило, непонятный адрес" || bad "F1 (en)" "$o"
o=$(lib t_h4_reveal 2>&1)
printf '%s' "$o" | grep -qx 'N1=Панель не открыта из интернета — показывать нечего (пункты 18, 19).' && printf '%s' "$o" | grep -qx 'N2=Адрес не получен: core is down' \
  && ok "F1 по-русски — прежний текст" || bad "F1: русский" "$o"

# ── 19: строка главного меню без снимка ядра (k3_site_menu_state) и DNS словами
t_h4_state() { k3_site_menu_state; for s in ok partial elsewhere none x; do k3_site_dns_word "$s"; done; echo "D1=$(tn SITE_DAYS 1) D5=$(tn SITE_DAYS 5)"; }
use_root i18n-h4-s0; engine_nft
o=$(en t_h4_state 2>&1); en_clean "19: строка меню, DNS словами" "$o"
[ "$o" = "not set
all A records point to this server
some A records point to other addresses
A records don’t point to this server
no A record
couldn’t check
D1=day D5=days" ] && ok "19 по-английски: «not set», DNS словами, day|days" || bad "19: строка меню (en)" "$o"
o=$(lib t_h4_state 2>&1 | head -1); [ "$o" = "не задан" ] && ok "19 по-русски: «не задан» — прежний (его сравнивает шапка главного меню)" || bad "19: «не задан»" "$o"
printf '{\n  "engine": "nft",\n  "site": {"domain": "vpn.example.com", "mode": "site", "cert": ""},\n  "schema": 1\n}\n' > "$KASKAD_DIR/state.json"
o=$(en t_h4_state 2>&1 | head -1); [ "$o" = "vpn.example.com · site · no certificate" ] && ok "19 по-английски: «vpn.example.com · site · no certificate»" || bad "19: строка с доменом (en)" "$o"

# ── 19: сквозной путь — домен → DNS не готов → ждать → выпуск → SSTP на домене → адрес для клиентов
use_root i18n-h4-w; fake_ctl; h4_gw 2
h4_sitef domain.json "{\"ok\": true, \"site\": $(h4_st "$H4_SELF" "$H4_DNS_NONE" false false), \"dns\": $H4_DNS_NONE, \"affected\": []}"
h4_sitef domain.status "$(h4_st "$H4_SELF" "$H4_DNS_NONE" false false)"
h4_sitef dns_wait.json "$H4_DNS_OK"; h4_sitef dns_wait.status "$(h4_st "$H4_SELF" "$H4_DNS_OK" false false)"
h4_sitef cert_issue.json '{"ok": true, "issued": true, "cert": '"$H4_LE"', "warnings": ["No email for Let’s Encrypt."], "consumers": [{"name": "panel"}], "text": "Let’s Encrypt certificate for vpn.example.com issued."}'
h4_sitef cert_issue.status "$(h4_st "$H4_LE" "$H4_DNS_OK" false false true)"
h4_sitef use_sstp_on_dry-run.json "{\"ok\": true, \"use\": {\"sstp\": true}, \"affected\": $H4_AFF, \"text\": \"$H4_F2\", \"warnings\": []}"
h4_sitef use_sstp_on.json "{\"ok\": true, \"use\": {\"sstp\": true}, \"affected\": $H4_AFF, \"text\": \"$H4_F2\", \"warnings\": []}"
h4_sitef use_sstp_on.status "$(h4_st "$H4_LE" "$H4_DNS_OK" true false true)"
gwf host.json '{"ok": true, "host": "vpn.example.com", "auto": false, "apply": null}'
t_h4_site() { h4_scr k3_site_menu; }
o=$(printf 'vpn.example.com\n\n\n\n1\n\n1\n\n0\n' | K3_TERM_COLS=80 en t_h4_site 2>&1 | strip | sed 's/.*\r//')
en_clean "19: сквозной путь" "$o"
w=$(printf '%s' "$o" | h4_flat)
printf '%s' "$w" | grep -q 'A domain gives Kaskad a real Let.s Encrypt certificate' && printf '%s' "$w" | grep -q 'value — 203.0.113.10 (this server.s address)' \
  && printf '%s' "$w" | grep -q '✓ Domain vpn.example.com is set.' && printf '%s' "$w" | grep -q '! DNS: no A record (A: none; server addresses: 203.0.113.10).' \
  && printf '%s' "$o" | grep -q '^ 1) Wait up to 5 minutes$' && printf '%s' "$w" | grep -q 'Checking every 15 s, up to 5 min.' \
  && printf '%s' "$w" | grep -q 'Waiting for DNS: [0-9]* s — check finished.' && printf '%s' "$w" | grep -q '✓ DNS is ready: all A records point to this server (203.0.113.10).' \
  && printf '%s' "$w" | grep -q 'Issue a Let.s Encrypt certificate for vpn.example.com?' \
  && printf '%s' "$w" | grep -q 'Valid until Dec 30, 2026 (83 days left), renews by itself.' && printf '%s' "$w" | grep -q 'The web panel and personal pages already use it.' \
  && printf '%s' "$w" | grep -q 'SSTP on the domain certificate' && printf '%s' "$w" | grep -q 'Devices that will need new settings: 2.' \
  && printf '%s' "$w" | grep -q '✓ SSTP: domain certificate.' && printf '%s' "$w" | grep -q 'Settings already issued (devices: 2) point to the old address' \
  && printf '%s' "$w" | grep -q '✓ Client address: vpn.example.com. Reissue settings to the devices.' \
  && printf '%s' "$o" | grep -q '━━━ 🌐 Domain and site ━━━' \
  && ok "19 по-английски: сквозной путь — домен, DNS (ожидание), выпуск, SSTP на домене, адрес для клиентов" || bad "19: сквозной путь (en)" "$(ctl_log) | $o"

# ── 19: подменю — шапка (сертификат, кто пользуется), DNS, почта, SSTP обратно, убрать домен
use_root i18n-h4-sub; fake_ctl; h4_gw 2
h4_sitef status.json "$(h4_st "$H4_LE" "$H4_DNS_OK" true true true)"
h4_sitef dns.json '{"state": "elsewhere", "a": ["198.51.100.7"], "own": ["203.0.113.10"], "ready": false, "advice": "Fix the A record."}'
h4_sitef email.json '{"ok": true, "email": "admin@example.com", "warnings": []}'
o=$(printf '1\n2\n\n4\nadmin@example.com\n\n0\n' | K3_TERM_COLS=80 en t_h4_site 2>&1 | strip)
en_clean "19: подменю, DNS, почта" "$o"
w=$(printf '%s' "$o" | h4_flat)
printf '%s' "$o" | grep -qx "Certificate: Let’s Encrypt, until Dec 30, 2026 (83 days left)" && printf '%s' "$o" | grep -qx 'Used by: web panel, personal pages, SSTP' \
  && printf '%s' "$o" | grep -qx 'Masking site: off — certificate without a site' && printf '%s' "$o" | grep -qx 'DNS: all A records point to this server' \
  && printf '%s' "$o" | grep -qx ' 2) Certificate: check or renew' && printf '%s' "$o" | grep -qx ' 4) Email for Let’s Encrypt: not set' \
  && printf '%s' "$o" | grep -qx ' 6) Masking site: turn on' && printf '%s' "$o" | grep -qx ' 0) Back' \
  && printf '%s' "$w" | grep -q '\[\*\] Checking DNS for vpn.example.com...' && printf '%s' "$w" | grep -q "! DNS: A records don’t point to this server" \
  && printf '%s' "$o" | grep -q '^ 2) Don.t wait$' && printf '%s' "$w" | grep -q 'Current: not set' \
  && printf '%s' "$w" | grep -q "✓ Email for Let’s Encrypt: admin@example.com." \
  && ok "19 подменю по-английски: шапка, пункты, DNS без ожидания, почта" || bad "19: подменю (en)" "$(ctl_log) | $o"
h4_sitef use_sstp_off_dry-run.json "{\"ok\": true, \"use\": {\"sstp\": false}, \"affected\": $H4_AFF, \"text\": \"$H4_F2\", \"warnings\": []}"
h4_sitef use_sstp_off.json "{\"ok\": true, \"use\": {\"sstp\": false}, \"affected\": $H4_AFF, \"text\": \"$H4_F2\", \"warnings\": []}"
o=$(printf '3\n1\n1\n\n2\n2\n\n0\n0\n' | K3_TERM_COLS=80 en t_h4_site 2>&1 | strip)
en_clean "19 → 3: SSTP и OpenConnect" "$o"
w=$(printf '%s' "$o" | h4_flat)
printf '%s' "$o" | grep -qx ' 1) SSTP: on the domain → back to self-signed (devices: 2)' \
  && printf '%s' "$o" | grep -qx ' 2) OpenConnect: self-signed → move to the domain (devices: 0)' \
  && printf '%s' "$w" | grep -q 'Settings issued earlier for this protocol will have to be reissued.' \
  && printf '%s' "$w" | grep -q 'Move SSTP back to the self-signed certificate?' && printf '%s' "$w" | grep -q '✓ SSTP: self-signed certificate.' \
  && printf '%s' "$w" | grep -q 'No devices have OpenConnect settings issued — nothing to reissue.' && printf '%s' "$o" | grep -qx 'Canceled.' \
  && ok "19 → 3 по-английски: состояние SSTP/OpenConnect с числом устройств, вернуть SSTP" || bad "19 → 3 (en)" "$(ctl_log) | $o"
h4_sitef domain_clear.json "{\"ok\": true, \"site\": {\"ok\": true, \"domain\": null}, \"affected\": $H4_AFF, \"kept\": [\"/etc/letsencrypt/live/vpn.example.com\"], \"text\": \"$H4_F2\"}"
h4_sitef domain_clear.status '{"ok": true, "domain": null}'
o=$(printf '5\n1\n\n' | K3_TERM_COLS=80 en t_h4_site 2>&1 | strip)
en_clean "19 → 5: убрать домен" "$o"
w=$(printf '%s' "$o" | h4_flat)
printf '%s' "$w" | grep -q 'Remove the domain vpn.example.com? What happens:' \
  && printf '%s' "$w" | grep -q 'SSTP will go back to the self-signed certificate and the server’s address — settings must be reissued (devices: 2; SSTP: 2);' \
  && printf '%s' "$w" | grep -q "the client address stays the domain" && printf '%s' "$w" | grep -q "✓ Domain removed: everything is on the server’s self-signed certificate." \
  && printf '%s' "$w" | grep -q "The Let’s Encrypt certificate remains: /etc/letsencrypt/live/vpn.example.com (not renewed)." \
  && ctl_log | grep -qx 'site domain --clear --json' && ok "19 → 5 по-английски: последствия, вопрос, итог" || bad "19 → 5 (en)" "$(ctl_log) | $o"
# цена VLESS (REV-vless4 «было до» 2): ядро отказало «vless_change» — текст ядра и вопрос цифрами по-английски, «1» — --yes
h4_sitef status.json "$(h4_st "$H4_LE" "$H4_DNS_OK" false false)"          # домен снова есть (прошлый шаг его убрал)
h4_sitef domain_clear.once.json '{"ok": false, "error": "PRICE-TEXT.", "code": 3, "data": {"need": "vless_change", "kind": "site", "text": "The site goes off, VLESS goes to Reality as dl.google.com. Give VLESS links again (1 device: Phone).", "site": "dl.google.com", "need_pick": false}}'
printf '3\n' > "$FAKE_DIR/site/domain_clear.once.rc"
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n1\n1\n\n' | K3_TERM_COLS=80 en t_h4_site 2>&1 | strip)
en_clean "19 → 5: убрать домен, цена VLESS" "$o"
w=$(printf '%s' "$o" | h4_flat)
printf '%s' "$w" | grep -q 'Give VLESS links again (1 device: Phone)\.' && printf '%s' "$w" | grep -q '1) Remove the domain and give the VLESS keys out again 2) Cancel' \
  && ctl_log | grep -qx 'site domain --clear --yes --json' && printf '%s' "$w" | grep -q "✓ Domain removed" \
  && ok "19 → 5 по-английски: цена VLESS — текст ядра, вопрос цифрами, «1» — --yes" || bad "19 → 5 (en): цена VLESS" "$(ctl_log | tr '\n' ';') | $w"

# ── 19 → 2: выпуск — отказ (порт 80); продление — срок не подошёл; сайт-маскировка в шапке
use_root i18n-h4-c; fake_ctl; engine_nft
h4_sitef status.json "$(h4_st "$H4_SELF" "$H4_DNS_OK" false false false site)"
h4_sitef cert_issue.json '{"ok": false, "reason": "port80", "text": "Port 80/tcp is used by nginx (pid 812).", "error": "Port 80/tcp is used by nginx (pid 812).", "code": 3}'
h4_sitef cert_issue.rc 3
o=$(printf '2\n\n\n0\n' | K3_TERM_COLS=80 en t_h4_site 2>&1 | strip)
en_clean "19 → 2: выпуск, отказ" "$o"
w=$(printf '%s' "$o" | h4_flat)
printf '%s' "$o" | grep -qx 'Masking site: on, template “Coffee House”' && printf '%s' "$o" | grep -qx 'Panel on the domain: https://vpn.example.com/app-AbCd••••/' \
  && printf '%s' "$o" | grep -qx 'Certificate: self-signed — browsers warn; to issue one: 2' && printf '%s' "$o" | grep -qx ' 6) Masking site: turn off' \
  && printf '%s' "$o" | grep -qx ' 9) Check the site' && printf '%s' "$w" | grep -q '✗ Certificate not issued: port 80 is in use.' \
  && printf '%s' "$w" | grep -q 'Once fixed, issue it again: 19 → 2.' \
  && ok "19 по-английски: шапка с сайтом-маскировкой, выпуск — отказ «port 80 is in use»" || bad "19 → 2: отказ (en)" "$(ctl_log) | $o"
h4_sitef status.json "$(h4_st "$H4_LE" "$H4_DNS_OK" false false)"
h4_sitef cert_renew.json '{"ok": true, "renewed": false, "cert": {"not_after": "2026-12-30T10:00:00Z", "days_left": 1}}'
o=$(printf '2\n9\n2\n\n0\n' | K3_TERM_COLS=80 en t_h4_site 2>&1 | strip)
en_clean "19 → 2: продление" "$o"
printf '%s' "$o" | grep -qx ' 2) Renew (certbot renew — only when it’s due)' && printf '%s' "$o" | grep -q 'No such item: a number from 1 to 2, 0 — back.' \
  && printf '%s' "$o" | grep -qx "Renewal isn’t due yet — certbot changed nothing." && printf '%s' "$o" | grep -qx '  Valid until Dec 30, 2026 (1 day left).' \
  && ok "19 → 2 → 2 по-английски: срок не подошёл, «1 day left»" || bad "19 → 2 → 2 (en)" "$(ctl_log) | $o"
o=$(printf '2\n2\n\n0\n' | K3_TERM_COLS=80 lib t_h4_site 2>&1 | strip)
printf '%s' "$o" | grep -qx '  Действует до 30.12.2026 (осталось 1 дн.).' && printf '%s' "$o" | grep -qx 'Срок продления ещё не подошёл — certbot ничего не менял.' \
  && ok "19 → 2 → 2 по-русски — прежний текст («осталось 1 дн.»)" || bad "19 → 2 → 2: русский" "$o"
