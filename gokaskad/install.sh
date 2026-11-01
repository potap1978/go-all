#!/bin/bash
set -o pipefail

# ══════════════════════════════════════════════════════════════
#  KASKAD PRO v2.11 — Cascading VPN / Proxy Manager
#  Telegram Bot · Live Ping · Monitoring · Alerts · GeoIP · System Stats
#  Channel: https://www.youtube.com/@antenkaru
# ══════════════════════════════════════════════════════════════

KASKAD_VERSION="2.13.2"
KASKAD_DIR="/etc/kaskad"
KASKAD_CONF="$KASKAD_DIR/config"
KASKAD_LOG="/var/log/kaskad.log"
MONITOR_DIR="$KASKAD_DIR/monitors"
ALIASES_FILE="$KASKAD_DIR/aliases"
BOT_STATE_DIR="$KASKAD_DIR/bot_state"
BOT_PID_FILE="/var/run/kaskad_bot.pid"
MONITOR_PID_FILE="/var/run/kaskad_monitor.pid"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; MAGENTA='\033[0;35m'; WHITE='\033[1;37m'
BLUE='\033[0;34m'; NC='\033[0m'

IFACE=""
MY_IP=""
BOT_TOKEN=""
BOT_CHAT_ID=""
MENU_STYLE=""

# ─── Config ───────────────────────────────────────────────────

init_config() {
    mkdir -p "$KASKAD_DIR" "$MONITOR_DIR" "$BOT_STATE_DIR"
    chmod 700 "$KASKAD_DIR" "$BOT_STATE_DIR" 2>/dev/null
    touch "$ALIASES_FILE"
    if [ ! -f "$KASKAD_CONF" ]; then
        cat > "$KASKAD_CONF" <<'CONF'
BOT_TOKEN=""
BOT_CHAT_ID=""
MENU_STYLE="inline"
GITHUB_PAT=""
BOT_PROXY=""
CONF
    fi
    # self-healing migration: add missing keys without clobbering existing values
    grep -qE '^[[:space:]]*BOT_TOKEN='   "$KASKAD_CONF" || echo 'BOT_TOKEN=""'         >> "$KASKAD_CONF"
    grep -qE '^[[:space:]]*BOT_CHAT_ID=' "$KASKAD_CONF" || echo 'BOT_CHAT_ID=""'       >> "$KASKAD_CONF"
    grep -qE '^[[:space:]]*MENU_STYLE='  "$KASKAD_CONF" || echo 'MENU_STYLE="inline"'  >> "$KASKAD_CONF"
    grep -qE '^[[:space:]]*GITHUB_PAT='  "$KASKAD_CONF" || echo 'GITHUB_PAT=""'        >> "$KASKAD_CONF"
    grep -qE '^[[:space:]]*BOT_PROXY='   "$KASKAD_CONF" || echo 'BOT_PROXY=""'         >> "$KASKAD_CONF"
    chmod 600 "$KASKAD_CONF" 2>/dev/null
    [ -f "$KASKAD_LOG" ] && chmod 600 "$KASKAD_LOG" 2>/dev/null
    source "$KASKAD_CONF"
}

save_config_val() {
    local key="$1" value="$2"
    # escape for a double-quoted assignment that is safe to `source`
    local esc="${value//\\/\\\\}"; esc="${esc//\"/\\\"}"; esc="${esc//\$/\\\$}"; esc="${esc//\`/\\\`}"
    local tmp; tmp=$(mktemp "${KASKAD_DIR}/.cfg.XXXXXX" 2>/dev/null) || tmp=$(mktemp)
    grep -v "^[[:space:]]*${key}=" "$KASKAD_CONF" 2>/dev/null > "$tmp"
    printf '%s="%s"\n' "$key" "$esc" >> "$tmp"
    mv -f "$tmp" "$KASKAD_CONF"
    chmod 600 "$KASKAD_CONF" 2>/dev/null
    source "$KASKAD_CONF"
}

# ─── Aliases: IP=name|note|country|isp ────────────────────────

set_alias_full() {
    local ip="$1"
    local name; name=$(sanitize_field "$2")
    local note; note=$(sanitize_field "${3:-}")
    local country; country=$(sanitize_field "${4:-}")
    local isp; isp=$(sanitize_field "${5:-}")
    local val="${name}|${note}|${country}|${isp}"
    local tmp; tmp=$(mktemp "${KASKAD_DIR}/.al.XXXXXX" 2>/dev/null) || tmp=$(mktemp)
    grep -v "^${ip//./\\.}=" "$ALIASES_FILE" 2>/dev/null > "$tmp"
    printf '%s=%s\n' "$ip" "$val" >> "$tmp"
    mv -f "$tmp" "$ALIASES_FILE"
}

set_alias() {
    local ip="$1" name="$2"
    local existing
    existing=$(_alias_grep "$ip" | head -1 | cut -d= -f2-)
    local old_note old_country old_isp
    IFS='|' read -r _ old_note old_country old_isp <<< "$existing"
    set_alias_full "$ip" "$name" "${old_note:-}" "${old_country:-}" "${old_isp:-}"
}

set_alias_note() {
    local ip="$1" note="$2"
    local existing
    existing=$(_alias_grep "$ip" | head -1 | cut -d= -f2-)
    local old_name old_note old_country old_isp
    IFS='|' read -r old_name old_note old_country old_isp <<< "$existing"
    set_alias_full "$ip" "${old_name:-}" "$note" "${old_country:-}" "${old_isp:-}"
}

set_alias_geo() {
    local ip="$1" country="$2" isp="$3"
    local existing
    existing=$(_alias_grep "$ip" | head -1 | cut -d= -f2-)
    local old_name old_note old_country old_isp
    IFS='|' read -r old_name old_note old_country old_isp <<< "$existing"
    set_alias_full "$ip" "${old_name:-}" "${old_note:-}" "$country" "$isp"
}

get_alias_field() {
    local ip="$1" field="$2"
    local raw
    raw=$(_alias_grep "$ip" | head -1 | cut -d= -f2-)
    local f_name f_note f_country f_isp
    IFS='|' read -r f_name f_note f_country f_isp <<< "$raw"
    case "$field" in
        name) echo "$f_name" ;; note) echo "$f_note" ;;
        country) echo "$f_country" ;; isp) echo "$f_isp" ;;
        *) echo "$f_name" ;;
    esac
}

get_alias() { get_alias_field "$1" "name"; }

fmt_ip() {
    local ip="$1"
    local name country isp
    name=$(get_alias_field "$ip" "name")
    country=$(get_alias_field "$ip" "country")
    isp=$(get_alias_field "$ip" "isp")
    local result=""
    [ -n "$name" ] && result="${name} " || result=""
    result+="($ip)"
    if [ -n "$country" ] || [ -n "$isp" ]; then
        result+=" "
        [ -n "$country" ] && result+="$country"
        [ -n "$isp" ] && result+=" | $isp"
    fi
    echo "$result"
}

fmt_ip_short() {
    local ip="$1"
    local name
    name=$(get_alias_field "$ip" "name")
    [ -n "$name" ] && echo "$name ($ip)" || echo "$ip"
}

fmt_ip_tg() {
    local ip="$1"
    local name note country isp
    name=$(html_escape "$(get_alias_field "$ip" "name")")
    note=$(html_escape "$(get_alias_field "$ip" "note")")
    country=$(html_escape "$(get_alias_field "$ip" "country")")
    isp=$(html_escape "$(get_alias_field "$ip" "isp")")
    local result=""
    [ -n "$name" ] && result="<b>$name</b> " || result=""
    result+="<code>$ip</code>"
    if [ -n "$country" ] || [ -n "$isp" ]; then
        result+=" "
        [ -n "$country" ] && result+="$country"
        [ -n "$isp" ] && result+=" | $isp"
    fi
    [ -n "$note" ] && result+="\n  <i>$note</i>"
    echo "$result"
}

# ─── Logging ──────────────────────────────────────────────────

log_action() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$KASKAD_LOG"
}

# ─── Escaping helpers ─────────────────────────────────────────

html_escape() {
    local s="${1//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"; printf '%s' "$s"
}

json_escape() {
    local s="${1//\\/\\\\}"; s="${s//\"/\\\"}"; printf '%s' "$s"
}

# strip control chars, the | field-delimiter, and backslash from user label fields
sanitize_field() {
    local s="${1//|/ }"; s="${s//\\/}"; printf '%s' "$s" | tr -d '\000-\037\177'
}

# grep an alias line with the IP matched literally (dots escaped)
_alias_grep() { grep "^${1//./\\.}=" "$ALIASES_FILE" 2>/dev/null; }

# ─── Validation ───────────────────────────────────────────────

validate_ip() {
    local ip="$1" o
    [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
    IFS='.' read -r -a octets <<< "$ip"
    for o in "${octets[@]}"; do
        [[ "$o" =~ ^(0|[1-9][0-9]{0,2})$ ]] || return 1
        (( 10#$o > 255 )) && return 1
    done
    return 0
}

validate_port() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 ))
}

read_validated_ip() {
    local prompt="${1:-Введите IP адрес назначения:}"
    while true; do
        echo -e "$prompt"
        read -p "> " _RET_IP || exit 0
        if validate_ip "$_RET_IP"; then return 0; fi
        echo -e "${RED}Ошибка: некорректный IP-адрес!${NC}"
    done
}

read_validated_port() {
    local prompt="${1:-Введите порт:}"
    while true; do
        echo -e "$prompt"
        read -p "> " _RET_PORT || exit 0
        if validate_port "$_RET_PORT"; then return 0; fi
        echo -e "${RED}Ошибка: порт должен быть числом от 1 до 65535!${NC}"
    done
}

# ─── GeoIP + Probe ────────────────────────────────────────────

geoip_lookup() {
    local ip="$1"
    curl -s --max-time 5 "http://ip-api.com/json/${ip}?fields=status,country,regionName,city,isp,org" 2>/dev/null
}

probe_server_cli() {
    local ip="$1" port="${2:-}" proto="${3:-}"
    echo -e "\n${CYAN}━━━ Проверка сервера $ip ━━━${NC}"

    echo -e "${YELLOW}[*] GeoIP...${NC}"
    local geo
    geo=$(geoip_lookup "$ip")
    local geo_status geo_country geo_region geo_city geo_isp geo_org
    geo_status=$(echo "$geo" | jq -r '.status // "fail"')
    if [ "$geo_status" = "success" ]; then
        geo_country=$(echo "$geo" | jq -r '.country // ""')
        geo_region=$(echo "$geo" | jq -r '.regionName // ""')
        geo_city=$(echo "$geo" | jq -r '.city // ""')
        geo_isp=$(echo "$geo" | jq -r '.isp // ""')
        geo_org=$(echo "$geo" | jq -r '.org // ""')
        local geo_loc="${geo_country}"
        [ -n "$geo_city" ] && geo_loc+=", ${geo_city}"
        local geo_provider="$geo_isp"
        [ -n "$geo_org" ] && [ "$geo_org" != "$geo_isp" ] && geo_provider+=" ($geo_org)"
        echo -e "  ${WHITE}GeoIP:${NC} ${GREEN}${geo_loc}${NC} | ${CYAN}${geo_provider}${NC}"
        set_alias_geo "$ip" "$geo_country" "$geo_isp"
    else
        echo -e "  ${RED}GeoIP: не удалось определить${NC}"
    fi

    echo -e "${YELLOW}[*] Ping (3x)...${NC}"
    local -a pings=()
    local plost=0
    for n in 1 2 3; do
        local raw method="" ms=""
        raw=$(smart_ping "$ip" 3 "$port" "$proto")
        if [ -n "$raw" ]; then
            method="${raw%%|*}"; ms="${raw#*|}"
            pings+=("$ms")
            echo -e "  #$n: ${GREEN}${ms}ms${NC} ${CYAN}[$method]${NC}"
        else
            ((plost++))
            echo -e "  #$n: ${RED}timeout${NC} ${WHITE}[ICMP$([ -n "$port" ] && echo "+TCP:$port")]${NC}"
        fi
        [ "$n" -lt 3 ] && sleep 1
    done
    if [ ${#pings[@]} -gt 0 ]; then
        local pavg
        pavg=$(printf '%s\n' "${pings[@]}" | LC_ALL=C awk '{s+=$1} END {printf "%.2f", s/NR}')
        echo -e "  ${WHITE}Среднее: ${pavg}ms${NC}  Потеряно: $plost/3"
    else
        echo -e "  ${RED}Сервер не отвечает${NC}"
    fi

    echo ""
    local existing_name
    existing_name=$(get_alias "$ip")
    if [ -n "$existing_name" ]; then
        echo -e "Текущее имя: ${GREEN}$existing_name${NC}"
    fi
    echo -e "Введите имя сервера (или Enter — пропустить):"
    read -p "> " _RET_NAME
    [ -n "$_RET_NAME" ] && set_alias "$ip" "$_RET_NAME"

    echo -e "Введите примечание (или Enter — пропустить):"
    read -p "> " _RET_NOTE
    [ -n "$_RET_NOTE" ] && set_alias_note "$ip" "$_RET_NOTE"

    if [ ${#pings[@]} -eq 0 ]; then
        echo -e "\n${YELLOW}━━━ Сервер не ответил ━━━${NC}"
        echo -e "${WHITE}ICMP заблокирован$([ -n "$port" ] && echo " и TCP:$port не удался").${NC}"
        echo ""
        echo -e "${CYAN}Чтобы включить ping на удалённом сервере:${NC}"
        echo -e "  ${WHITE}ssh root@${ip}${NC}"
        echo -e "  ${GREEN}sysctl -w net.ipv4.icmp_echo_ignore_all=0${NC}"
        echo -e "  ${GREEN}echo 'net.ipv4.icmp_echo_ignore_all=0' >> /etc/sysctl.conf${NC}"
        echo -e "  ${GREEN}iptables -A INPUT -p icmp --icmp-type echo-request -j ACCEPT${NC}"
        echo ""
        read -p "Продолжить добавление? (y/n): " ans
        [[ "$ans" != "y" ]] && return 1
    fi
    return 0
}

probe_server_tg() {
    local ip="$1" port="${2:-}" proto="${3:-}"
    local result=""
    local geo
    geo=$(geoip_lookup "$ip")
    local geo_status
    geo_status=$(echo "$geo" | jq -r '.status // "fail"')
    if [ "$geo_status" = "success" ]; then
        local geo_country geo_city geo_isp geo_org
        geo_country=$(echo "$geo" | jq -r '.country // ""')
        geo_city=$(echo "$geo" | jq -r '.city // ""')
        geo_isp=$(echo "$geo" | jq -r '.isp // ""')
        geo_org=$(echo "$geo" | jq -r '.org // ""')
        local geo_loc="$geo_country"
        [ -n "$geo_city" ] && geo_loc+=", $geo_city"
        result+="🌍 <b>GeoIP:</b> $(html_escape "$geo_loc") | $(html_escape "$geo_isp")\n"
        set_alias_geo "$ip" "$geo_country" "$geo_isp"
    fi
    local -a pings=()
    local plost=0
    for n in 1 2 3; do
        local raw method="" ms=""
        raw=$(smart_ping "$ip" 3 "$port" "$proto")
        if [ -n "$raw" ]; then
            method="${raw%%|*}"; ms="${raw#*|}"
            pings+=("$ms")
            result+="  #$n: ${ms}ms [$method]\n"
        else
            ((plost++))
            result+="  #$n: timeout\n"
        fi
        [ "$n" -lt 3 ] && sleep 1
    done
    if [ ${#pings[@]} -gt 0 ]; then
        local pavg
        pavg=$(printf '%s\n' "${pings[@]}" | LC_ALL=C awk '{s+=$1} END {printf "%.2f", s/NR}')
        result+="<b>Среднее: ${pavg}ms</b> | Потеряно: $plost/3\n"
    else
        result+="<b>Сервер не ответил</b>\n"
        result+="Включите ping:\n<code>sysctl -w net.ipv4.icmp_echo_ignore_all=0</code>\n"
        result+="<code>iptables -A INPUT -p icmp --icmp-type echo-request -j ACCEPT</code>\n"
    fi
    echo "$result"
}

# ─── System ───────────────────────────────────────────────────

check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}[ERROR] Запустите скрипт с правами root!${NC}"; exit 1
    fi
}

detect_interface() {
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        IFACE=$(ip route get 8.8.8.8 2>/dev/null | sed -n 's/.*dev \([^ ]*\).*/\1/p' | head -1)
        [ -n "$IFACE" ] && return 0
        sleep 2
    done
    echo -e "${RED}[ERROR] Не удалось определить интерфейс!${NC}"
    return 1
}

get_my_ip() {
    MY_IP=$(curl -s4 --max-time 5 ifconfig.me 2>/dev/null || echo "N/A")
}

save_iptables() {
    if command -v netfilter-persistent &>/dev/null; then
        netfilter-persistent save > /dev/null 2>&1
    elif command -v service &>/dev/null; then
        service iptables save > /dev/null 2>&1
    fi
}

get_system_stats() {
    local cpu_line load_avg mem_info swap_info disk_info uptime_str top_procs cpu_usage
    cpu_line=$(grep -c ^processor /proc/cpuinfo 2>/dev/null || echo "?")
    load_avg=$(cat /proc/loadavg 2>/dev/null | awk '{print $1, $2, $3}')
    mem_info=$(free -m 2>/dev/null | awk '/^Mem:/ {printf "%d/%dMB (%.1f%%)", $3, $2, $3/$2*100}')
    swap_info=$(free -m 2>/dev/null | awk '/^Swap:/ {if($2>0) printf "%d/%dMB", $3, $2; else print "N/A"}')
    disk_info=$(df -h / 2>/dev/null | awk 'NR==2 {printf "%s/%s (%s)", $3, $2, $5}')
    uptime_str=$(uptime -p 2>/dev/null || uptime | sed 's/.*up /up /' | sed 's/,.*load.*//')
    top_procs=$(ps aux --sort=-%cpu 2>/dev/null | head -8 | awk 'NR>1 {printf "%-6s %-4s%% %-4s%% %s\n", $2, $3, $4, $11}')
    top_procs=$(html_escape "$top_procs")
    cpu_usage=$(awk '/^cpu / {u=$2+$4; t=$2+$3+$4+$5+$6+$7+$8; if(t>0) printf "%.1f", u/t*100; else print "0"}' /proc/stat 2>/dev/null)
    local r=""
    r+="<b>📊 Системная информация</b>\n\n"
    r+="<b>Uptime:</b> ${uptime_str}\n"
    r+="<b>CPU:</b> ${cpu_line} ядер | ${cpu_usage}%\n"
    r+="<b>Load:</b> ${load_avg}\n"
    r+="<b>RAM:</b> ${mem_info}\n"
    r+="<b>Swap:</b> ${swap_info}\n"
    r+="<b>Disk /:</b> ${disk_info}\n\n"
    r+="<b>Топ CPU:</b>\n<pre>PID    CPU%  MEM%  CMD\n${top_procs}</pre>"
    echo "$r"
}

# ─── iptables helpers ─────────────────────────────────────────

get_rules_list() {
    iptables -t nat -S PREROUTING 2>/dev/null | grep "DNAT" | grep "kaskad:" | while read -r line; do
        local port proto dest
        port=$(echo "$line" | sed -n 's/.*--dport \([0-9]*\).*/\1/p')
        proto=$(echo "$line" | sed -n 's/.*-p \([a-z]*\).*/\1/p')
        dest=$(echo "$line" | sed -n 's/.*--to-destination \([0-9.:]*\).*/\1/p')
        [ -n "$port" ] && echo "${proto}|${port}|${dest}"
    done
}

get_target_ips() {
    get_rules_list | awk -F'|' '{split($3,a,":"); print a[1]}' | sort -u
}

get_port_for_ip() {
    local ip="$1"
    get_rules_list | awk -F'|' -v ip="$ip" '{split($3,a,":"); if(a[1]==ip){print a[2]; exit}}'
}

get_proto_for_ip() {
    local ip="$1"
    get_rules_list | awk -F'|' -v ip="$ip" '{split($3,a,":"); if(a[1]==ip){print $1; exit}}'
}

tcp_ping() {
    local ip="$1" port="$2" tout="${3:-3}"
    local raw
    raw=$(curl -so /dev/null -w '%{time_connect}' --max-time "$tout" --connect-timeout "$tout" "http://${ip}:${port}/" 2>/dev/null)
    [ -z "$raw" ] && return 1
    local ms
    ms=$(LC_ALL=C awk "BEGIN {v=$raw*1000; if(v<=0) exit 1; printf \"%.2f\", v}" 2>/dev/null) || return 1
    echo "$ms"
}

smart_ping() {
    local ip="$1" tout="${2:-3}" port="${3:-}" proto="${4:-}"
    local ms
    ms=$(ping -c 1 -W "$tout" "$ip" 2>/dev/null | sed -n 's/.*time=\([0-9.]*\).*/\1/p')
    if [ -n "$ms" ]; then echo "ICMP|$ms"; return 0; fi
    [ -z "$proto" ] && proto=$(get_proto_for_ip "$ip")
    [ -z "$port" ] && port=$(get_port_for_ip "$ip")
    # UDP services (WireGuard/AmneziaWG) can't be probed over TCP — ICMP only
    [ "$proto" = "udp" ] && return 1
    [ -z "$port" ] && return 1
    ms=$(tcp_ping "$ip" "$port" "$tout")
    if [ -n "$ms" ]; then echo "TCP:${port}|$ms"; return 0; fi
    return 1
}

remove_rules_for_port() {
    local proto="$1" in_port="$2"
    iptables -t nat -S PREROUTING 2>/dev/null | grep "DNAT" | grep -- "kaskad:${in_port}:${proto}" | while read -r rule; do
        eval "iptables -t nat -D ${rule#-A }" 2>/dev/null
    done
    iptables -S INPUT 2>/dev/null | grep -- "kaskad:${in_port}:${proto}" | while read -r rule; do
        eval "iptables -D ${rule#-A }" 2>/dev/null
    done
    iptables -S FORWARD 2>/dev/null | grep -- "kaskad:${in_port}:${proto}" | while read -r rule; do
        eval "iptables -D ${rule#-A }" 2>/dev/null
    done
}

apply_iptables_rules() {
    local proto="$1" in_port="$2" out_port="$3" target_ip="$4" name="$5"
    echo -e "${YELLOW}[*] Применение правил...${NC}"
    log_action "ADD rule: $proto :$in_port -> $target_ip:$out_port ($name)"
    remove_rules_for_port "$proto" "$in_port"
    local rc=0
    iptables -I INPUT -p "$proto" --dport "$in_port" -m comment --comment "kaskad:${in_port}:${proto}" -j ACCEPT || rc=1
    iptables -t nat -A PREROUTING -p "$proto" --dport "$in_port" -m comment --comment "kaskad:${in_port}:${proto}" -j DNAT --to-destination "$target_ip:$out_port" || rc=1
    if ! iptables -t nat -C POSTROUTING -o "$IFACE" -m comment --comment "kaskad:masq" -j MASQUERADE 2>/dev/null; then
        iptables -t nat -A POSTROUTING -o "$IFACE" -m comment --comment "kaskad:masq" -j MASQUERADE || rc=1
    fi
    iptables -I FORWARD -p "$proto" -d "$target_ip" --dport "$out_port" -m state --state NEW,ESTABLISHED,RELATED -m comment --comment "kaskad:${in_port}:${proto}" -j ACCEPT || rc=1
    iptables -I FORWARD -p "$proto" -s "$target_ip" --sport "$out_port" -m state --state ESTABLISHED,RELATED -m comment --comment "kaskad:${in_port}:${proto}" -j ACCEPT || rc=1
    if [ "$rc" -ne 0 ]; then
        echo -e "${RED}[ERROR] iptables отклонил правило — откат.${NC}"
        remove_rules_for_port "$proto" "$in_port"
        remove_masquerade_if_unused
        log_action "ADD rule FAILED: $proto :$in_port -> $target_ip:$out_port"
        return 1
    fi
    if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow "$in_port/$proto" > /dev/null 2>&1
    fi
    save_iptables
    echo -e "${GREEN}[SUCCESS] $name настроен!${NC}"
    echo -e "$proto: ${MY_IP:-*}:$in_port -> $target_ip:$out_port"
}

# ─── Interactive rule configuration ──────────────────────────

configure_rule() {
    local proto="$1" name="$2"
    echo -e "\n${CYAN}--- Настройка $name ($proto) ---${NC}"
    read_validated_ip "Введите IP адрес назначения:"
    local target_ip="$_RET_IP"
    read_validated_port "Введите Порт (одинаковый для входа и выхода):"
    local port="$_RET_PORT"
    probe_server_cli "$target_ip" "$port" "$proto" || return
    echo -e "\n${YELLOW}Будет создано правило:${NC}"
    echo -e "  $proto: ${MY_IP:-*}:$port -> $(fmt_ip_short "$target_ip"):$port"
    read -p "Применить? (y/n): " confirm
    [[ "$confirm" != "y" ]] && return
    apply_iptables_rules "$proto" "$port" "$port" "$target_ip" "$name"
    read -p "Нажмите Enter для возврата в меню..."
}

configure_custom_rule() {
    echo -e "\n${CYAN}--- Универсальное кастомное правило ---${NC}"
    local proto
    while true; do
        echo -e "Выберите протокол (${YELLOW}tcp${NC} или ${YELLOW}udp${NC}):"
        read -p "> " proto || exit 0
        [[ "$proto" == "tcp" || "$proto" == "udp" ]] && break
        echo -e "${RED}Ошибка: введите tcp или udp!${NC}"
    done
    read_validated_ip "Введите IP адрес назначения:"
    local target_ip="$_RET_IP"
    read_validated_port "Введите ${YELLOW}ВХОДЯЩИЙ Порт${NC} (на этом сервере):"
    local in_port="$_RET_PORT"
    read_validated_port "Введите ${YELLOW}ИСХОДЯЩИЙ Порт${NC} (на конечном сервере):"
    local out_port="$_RET_PORT"
    probe_server_cli "$target_ip" "$out_port" "$proto" || return
    echo -e "\n${YELLOW}Будет создано правило:${NC}"
    echo -e "  $proto: ${MY_IP:-*}:$in_port -> $(fmt_ip_short "$target_ip"):$out_port"
    read -p "Применить? (y/n): " confirm
    [[ "$confirm" != "y" ]] && return
    apply_iptables_rules "$proto" "$in_port" "$out_port" "$target_ip" "Custom Rule"
    read -p "Нажмите Enter для возврата в меню..."
}

# ─── List / Delete / Flush ────────────────────────────────────

list_active_rules() {
    echo -e "\n${CYAN}━━━ Активные переадресации ━━━${NC}"
    echo -e "${WHITE}Сервер каскада: ${GREEN}${MY_IP:-N/A}${NC}\n"
    local rules
    rules=$(get_rules_list)
    if [ -z "$rules" ]; then
        echo -e "${YELLOW}Нет активных правил.${NC}"
    else
        echo "$rules" | while IFS='|' read -r proto port dest; do
            local dest_ip="${dest%:*}"
            echo -e "  ${WHITE}${MY_IP:-*}:${port}${NC} ($proto) → ${GREEN}${dest}${NC} $(fmt_ip "$dest_ip")"
        done
    fi
    echo ""
    read -p "Нажмите Enter..."
}

delete_single_rule() {
    echo -e "\n${CYAN}--- Удаление правила ---${NC}"
    local -a rules_arr=()
    local i=1
    while IFS='|' read -r proto port dest; do
        rules_arr[$i]="$proto|$port|$dest"
        local dest_ip="${dest%:*}"
        echo -e "${YELLOW}[$i]${NC} ${MY_IP:-*}:$port ($proto) -> $(fmt_ip_short "$dest_ip")"
        ((i++))
    done <<< "$(get_rules_list)"
    if [ ${#rules_arr[@]} -eq 0 ]; then
        echo -e "${RED}Нет активных правил.${NC}"; read -p "Нажмите Enter..."; return
    fi
    echo ""
    read -p "Номер для удаления (0 — отмена): " rule_num
    [[ "$rule_num" == "0" || -z "${rules_arr[$rule_num]:-}" ]] && return
    IFS='|' read -r d_proto d_port d_dest <<< "${rules_arr[$rule_num]}"
    iptables -t nat -S PREROUTING 2>/dev/null | grep "DNAT" | grep -- "kaskad:${d_port}:${d_proto}" | while read -r rule; do eval "iptables -t nat -D ${rule#-A }" 2>/dev/null; done
    iptables -S INPUT 2>/dev/null | grep "kaskad:${d_port}:${d_proto}" | while read -r rule; do eval "iptables -D ${rule#-A }" 2>/dev/null; done
    iptables -S FORWARD 2>/dev/null | grep "kaskad:${d_port}:${d_proto}" | while read -r rule; do eval "iptables -D ${rule#-A }" 2>/dev/null; done
    if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw delete allow "$d_port/$d_proto" > /dev/null 2>&1
    fi
    local _dip="${d_dest%:*}"
    [ -z "$(get_port_for_ip "$_dip")" ] && remove_monitor "$_dip" 2>/dev/null
    remove_masquerade_if_unused
    save_iptables
    log_action "DELETE rule: $d_proto :$d_port -> $d_dest"
    echo -e "${GREEN}[OK] Правило удалено.${NC}"; read -p "Нажмите Enter..."
}

flush_all_kaskad_rules() {
    while [ -n "$(iptables -t nat -S PREROUTING 2>/dev/null | grep 'DNAT' | grep 'kaskad:')" ]; do
        local rule; rule=$(iptables -t nat -S PREROUTING | grep "DNAT" | grep "kaskad:" | head -1)
        eval "iptables -t nat -D ${rule#-A }" 2>/dev/null
    done
    local chain
    for chain in INPUT FORWARD; do
        while [ -n "$(iptables -S "$chain" 2>/dev/null | grep 'kaskad')" ]; do
            local rule; rule=$(iptables -S "$chain" | grep "kaskad" | head -1)
            eval "iptables -D ${rule#-A }" 2>/dev/null
        done
    done
}

remove_masquerade_if_unused() {
    [ -n "$(get_rules_list)" ] && return
    iptables -t nat -S POSTROUTING 2>/dev/null | grep -- "kaskad:masq" | while read -r mr; do
        eval "iptables -t nat -D ${mr#-A }" 2>/dev/null
    done
}

# upgrade migration: re-tag pre-v2.5 kaskad DNATs (which had no comment) so they are
# distinguishable from foreign DNATs. Only those with a matching kaskad INPUT marker.
migrate_dnat_comments() {
    # Re-tag pre-v2.5 kaskad DNATs (which had no comment). To avoid adopting a
    # FOREIGN DNAT, identify the exact kaskad mapping from the kaskad FORWARD rule
    # (which DID carry the comment in 2.3): it holds target IP + out_port, and the
    # comment holds in_port:proto. Only the DNAT matching that exact tuple is tagged.
    local fr tag inport proto target outport
    iptables -S FORWARD 2>/dev/null | grep -- "kaskad:" | grep -- "--dport " | while read -r fr; do
        tag=$(echo "$fr" | sed -n 's/.*--comment "\?\(kaskad:[0-9]*:[a-z]*\)"\?.*/\1/p')
        inport=$(echo "$tag" | cut -d: -f2); proto=$(echo "$tag" | cut -d: -f3)
        target=$(echo "$fr" | sed -n 's/.*-d \([0-9.]*\)\/32 .*/\1/p')
        outport=$(echo "$fr" | sed -n 's/.*--dport \([0-9]*\).*/\1/p')
        [ -n "$inport" ] && [ -n "$proto" ] && [ -n "$target" ] && [ -n "$outport" ] || continue
        iptables -t nat -S PREROUTING 2>/dev/null | grep "DNAT" | grep -v "kaskad:" \
            | grep -- "-p ${proto} " | grep -- "--dport ${inport} " | grep -- "--to-destination ${target}:${outport}" \
            | while read -r dr; do
                eval "iptables -t nat -D ${dr#-A }" 2>/dev/null
                iptables -t nat -A PREROUTING -p "$proto" --dport "$inport" -m comment --comment "kaskad:${inport}:${proto}" -j DNAT --to-destination "${target}:${outport}"
            done
    done
}

# ─── Правила бесплатного Kaskad ──────────────────────────────
# Бесплатный Kaskad ставит те же правила, что и
# pro, но без меток «kaskad:», а pro видит только помеченные. Поставленный
# поверх бесплатного, pro показывал «Нет активных правил», старые каскады
# работали мимо его меню, а правило pro на том же порту вставало ПОСЛЕ старого
# DNAT и не срабатывало: трафик шёл по-старому.
# Берём себе ровно такой набор: DNAT без метки в PREROUTING, для которого есть
# FORWARD-разрешение без метки к той же цели и порту. DNAT без такой пары
# (Docker, ручные правила) не трогаем. Сначала собираем все наборы, потом
# снимаем: у двух правил может быть одна цель и одна пара FORWARD.
adopt_free_kaskad_rules() {
    local dr proto inport dest target outport ir fr rec n=0
    local -a found=()
    while read -r dr; do
        proto=$(echo "$dr" | sed -n 's/.* -p \([a-z]*\) .*/\1/p')
        inport=$(echo "$dr" | sed -n 's/.*--dport \([0-9]*\) .*/\1/p')
        dest=$(echo "$dr" | sed -n 's/.*--to-destination \([0-9.]*:[0-9]*\).*/\1/p')
        target="${dest%:*}"; outport="${dest##*:}"
        [ -n "$proto" ] && [ -n "$inport" ] && [ -n "$dest" ] || continue
        iptables -S FORWARD 2>/dev/null | grep -v -- "kaskad:" \
            | grep -- "-d ${target}/32 -p ${proto} " | grep -q -- "--dport ${outport} " || continue
        found+=("${proto}|${inport}|${target}|${outport}|${dr}")
    done < <(iptables -t nat -S PREROUTING 2>/dev/null | grep -- "-j DNAT" | grep -v -- "kaskad:")
    [ "${#found[@]}" -gt 0 ] || return 0
    echo -e "${YELLOW}[*] Правила бесплатного Kaskad: ${#found[@]} — беру их себе, чтобы они были в меню.${NC}"
    for rec in "${found[@]}"; do
        IFS='|' read -r proto inport target outport dr <<<"$rec"
        eval "iptables -t nat -D ${dr#-A }" 2>/dev/null
        iptables -S INPUT 2>/dev/null | grep -v -- "kaskad:" | grep -- "-p ${proto} " \
            | grep -- "--dport ${inport} " | grep -- "-j ACCEPT" | while read -r ir; do
                eval "iptables -D ${ir#-A }" 2>/dev/null
            done
        iptables -S FORWARD 2>/dev/null | grep -v -- "kaskad:" | grep -E -- "-(d|s) ${target}/32 -p ${proto} " \
            | grep -E -- "--(dport|sport) ${outport} " | while read -r fr; do
                eval "iptables -D ${fr#-A }" 2>/dev/null
            done
    done
    for rec in "${found[@]}"; do
        IFS='|' read -r proto inport target outport dr <<<"$rec"
        apply_iptables_rules "$proto" "$inport" "$outport" "$target" "Каскад из бесплатной версии" && n=$((n+1))
    done
    log_action "ADOPT free kaskad rules: ${n} of ${#found[@]}"
}

flush_rules() {
    echo -e "\n${RED}!!! ВНИМАНИЕ !!!${NC}"
    echo "Будут удалены только правила Kaskad."
    read -p "Уверены? (y/n): " confirm
    if [[ "$confirm" == "y" ]]; then
        if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
            iptables -S INPUT 2>/dev/null | grep "kaskad" | while read -r ul; do
                local up; up=$(echo "$ul" | sed -n 's/.*--dport \([0-9]*\).*/\1/p')
                local upr; upr=$(echo "$ul" | sed -n 's/.*-p \([a-z]*\).*/\1/p')
                [ -n "$up" ] && [ -n "$upr" ] && ufw delete allow "$up/$upr" > /dev/null 2>&1
            done
        fi
        flush_all_kaskad_rules
        remove_masquerade_if_unused
        rm -f "$MONITOR_DIR"/*.conf 2>/dev/null; sync_monitoring_service
        save_iptables; log_action "FLUSH all kaskad rules"
        echo -e "${GREEN}[OK] Очищено.${NC}"
    fi
    read -p "Нажмите Enter..."
}

full_uninstall() {
    clear
    echo -e "\n${RED}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║            ⚠  ПОЛНОЕ УДАЛЕНИЕ KASKAD PRO  ⚠               ║${NC}"
    echo -e "${RED}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${WHITE}Будут удалены:${NC}"
    echo -e "  ${RED}•${NC} Все правила каскада (iptables)"
    echo -e "  ${RED}•${NC} Telegram-бот и мониторинг"
    echo -e "  ${RED}•${NC} Конфигурация ${WHITE}/etc/kaskad/${NC}"
    echo -e "  ${RED}•${NC} Команда ${WHITE}gokaskad${NC}"
    echo -e "  ${RED}•${NC} Логи ${WHITE}/var/log/kaskad.log${NC}"
    echo ""
    echo -e "${GREEN}НЕ будет затронуто:${NC}"
    echo -e "  ${GREEN}•${NC} Системные пакеты (iptables, jq, curl, qrencode)"
    echo -e "  ${GREEN}•${NC} Ваши VPN / прокси (WireGuard, XRay и т.д.)"
    echo -e "  ${GREEN}•${NC} Настройки sysctl (ip_forward, bbr)"
    echo ""
    read -p "$(echo -e "${RED}Удалить Kaskad PRO полностью? (y/n): ${NC}")" confirm1
    [[ "$confirm1" != "y" ]] && { echo -e "\n${CYAN}Отменено.${NC}"; read -p "Нажмите Enter..."; return; }
    local words=("УДАЛИТЬ" "СТЕРЕТЬ" "СНЕСТИ" "УНИЧТОЖИТЬ" "ПРОЩАЙ")
    local word="${words[$((RANDOM % ${#words[@]}))]}"
    echo ""
    echo -e "${RED}Последний шанс! Введите слово ${WHITE}${word}${RED} для подтверждения:${NC}"
    read -p "> " confirm2
    if [[ "$confirm2" != "$word" ]]; then
        echo -e "\n${CYAN}Неверное слово. Удаление отменено.${NC}"
        read -p "Нажмите Enter..."
        return
    fi
    echo ""
    echo -e "${YELLOW}Удаление Kaskad PRO...${NC}\n"
    if systemctl is-active kaskad-bot &>/dev/null; then
        systemctl stop kaskad-bot 2>/dev/null; systemctl disable kaskad-bot 2>/dev/null
    fi
    [ -f "$BOT_PID_FILE" ] && { kill "$(cat "$BOT_PID_FILE")" 2>/dev/null; rm -f "$BOT_PID_FILE"; }
    rm -f /etc/systemd/system/kaskad-bot.service
    echo -e "  ${GREEN}✓${NC}  Telegram-бот остановлен"
    if systemctl is-active kaskad-monitor &>/dev/null; then
        systemctl stop kaskad-monitor 2>/dev/null; systemctl disable kaskad-monitor 2>/dev/null
    fi
    [ -f "$MONITOR_PID_FILE" ] && { kill "$(cat "$MONITOR_PID_FILE")" 2>/dev/null; rm -f "$MONITOR_PID_FILE"; }
    rm -f /etc/systemd/system/kaskad-monitor.service
    systemctl daemon-reload 2>/dev/null
    echo -e "  ${GREEN}✓${NC}  Мониторинг остановлен"
    if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
        iptables -S INPUT 2>/dev/null | grep "kaskad" | while read -r ul; do
            local up; up=$(echo "$ul" | sed -n 's/.*--dport \([0-9]*\).*/\1/p')
            local upr; upr=$(echo "$ul" | sed -n 's/.*-p \([a-z]*\).*/\1/p')
            [ -n "$up" ] && [ -n "$upr" ] && ufw delete allow "$up/$upr" > /dev/null 2>&1
        done
        echo -e "  ${GREEN}✓${NC}  Правила UFW очищены"
    fi
    flush_all_kaskad_rules
    iptables -t nat -S POSTROUTING 2>/dev/null | grep -- "kaskad:masq" | while read -r mr; do eval "iptables -t nat -D ${mr#-A }" 2>/dev/null; done
    save_iptables
    echo -e "  ${GREEN}✓${NC}  Правила iptables удалены"
    rm -rf "$KASKAD_DIR"
    echo -e "  ${GREEN}✓${NC}  Конфигурация удалена"
    rm -f "$KASKAD_LOG"
    echo -e "  ${GREEN}✓${NC}  Логи удалены"
    rm -f /usr/local/bin/gokaskad
    echo -e "  ${GREEN}✓${NC}  Команда gokaskad удалена"
    echo ""
    echo -e "${GREEN}══════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Kaskad PRO полностью удалён.${NC}"
    echo -e "${WHITE}  Спасибо, что пользовались!${NC}"
    echo -e "${GREEN}══════════════════════════════════════════${NC}"
    echo ""
    read -p "Нажмите Enter..."
    exit 0
}

manage_aliases_menu() {
    while true; do
        clear
        echo -e "${CYAN}━━━ Имена серверов ━━━${NC}"
        local -a ips=()
        while read -r ip; do [ -n "$ip" ] && ips+=("$ip"); done <<< "$(get_target_ips)"
        if [ ${#ips[@]} -eq 0 ]; then echo -e "${YELLOW}Нет серверов.${NC}"; read -p "Enter..."; return; fi
        for i in "${!ips[@]}"; do
            echo -e "  ${YELLOW}[$((i+1))]${NC} $(fmt_ip "${ips[$i]}")"
            local note; note=$(get_alias_field "${ips[$i]}" "note")
            [ -n "$note" ] && echo -e "       ${WHITE}Примечание:${NC} $note"
        done
        echo -e "  ${YELLOW}[0]${NC} Назад"
        read -p "Сервер: " choice || return
        [[ "$choice" == "0" || -z "$choice" ]] && return
        local idx=$((choice - 1))
        [ -z "${ips[$idx]:-}" ] && continue
        local sel="${ips[$idx]}"
        echo -e "Новое имя для $sel (Enter — оставить):"
        read -p "> " nn; [ -n "$nn" ] && set_alias "$sel" "$nn"
        echo -e "Новое примечание (Enter — оставить):"
        read -p "> " nt; [ -n "$nt" ] && set_alias_note "$sel" "$nt"
        echo -e "${GREEN}[OK]${NC}"; read -p "Enter..."
    done
}

# ─── Auto-update ──────────────────────────────────────────────

self_update() {
    local repo_url="https://raw.githubusercontent.com/potap1978/go-all/main/gokaskad/install.sh"
    source "$KASKAD_CONF" 2>/dev/null
    local update_token; update_token=$(kaskad_gh_token)
    echo -e "${YELLOW}[*] Загрузка обновления...${NC}"
    local upd hdr; upd=$(mktemp /tmp/kaskad_update.XXXXXX) || { echo -e "${RED}mktemp error${NC}"; read -p "Enter..."; return; }
    hdr=$(mktemp /tmp/kaskad_hdr.XXXXXX); chmod 600 "$hdr"
    trap 'rm -f "$upd" "$hdr"' RETURN
    local ok=0 tok
    for tok in "$update_token" ""; do
        if [ -n "$tok" ]; then
            printf 'header = "Authorization: token %s"\n' "$tok" > "$hdr"
            curl -fsSL --config "$hdr" "$repo_url" -o "$upd" 2>/dev/null
        else
            curl -fsSL "$repo_url" -o "$upd" 2>/dev/null
        fi
        head -1 "$upd" 2>/dev/null | grep -q "#!/bin/bash" && { ok=1; break; }
    done
    if [ "$ok" -eq 0 ]; then
        echo -e "${RED}Не удалось скачать. Репозиторий приватный.${NC}"
        echo -e "${WHITE}Введите GitHub PAT (токен доступа) или Enter для отмены:${NC}"
        echo -e "${CYAN}(Создать: GitHub → Settings → Developer settings → Personal access tokens)${NC}"
        read -p "> " new_token
        if [ -n "$new_token" ]; then
            printf 'header = "Authorization: token %s"\n' "$new_token" > "$hdr"
            curl -fsSL --config "$hdr" "$repo_url" -o "$upd" 2>/dev/null
            head -1 "$upd" 2>/dev/null | grep -q "#!/bin/bash" && ok=1
            if [ "$ok" -eq 1 ]; then
                mkdir -p "$BOT_STATE_DIR"
                bot_set_state "system" "UPDATE_TOKEN=$new_token"
                save_config_val "GITHUB_PAT" "$new_token"
                echo -e "${GREEN}Токен сохранён для будущих обновлений.${NC}"
            else
                echo -e "${RED}Токен не подошёл или ошибка сети.${NC}"
            fi
        fi
    fi
    if [ "$ok" -ne 1 ] || [ ! -s "$upd" ]; then
        echo -e "${RED}[ERROR] Не удалось обновить.${NC}"; read -p "Нажмите Enter..."; return
    fi
    if ! bash -n "$upd" 2>/dev/null; then
        echo -e "${RED}[ERROR] Загруженный скрипт повреждён (синтаксис). Отменено.${NC}"; read -p "Нажмите Enter..."; return
    fi
    if ! tail -3 "$upd" | grep -q '^esac'; then
        echo -e "${RED}[ERROR] Файл неполный (обрыв загрузки). Отменено.${NC}"; read -p "Нажмите Enter..."; return
    fi
    local new_ver; new_ver=$(grep -m1 '^KASKAD_VERSION=' "$upd" | cut -d'"' -f2)
    cp -f /usr/local/bin/gokaskad /usr/local/bin/gokaskad.bak 2>/dev/null
    if cp -f "$upd" /usr/local/bin/.gokaskad.new && chmod +x /usr/local/bin/.gokaskad.new && mv -f /usr/local/bin/.gokaskad.new /usr/local/bin/gokaskad; then
        local restarted=""
        if systemctl is-active --quiet kaskad-bot 2>/dev/null; then
            systemctl restart kaskad-bot 2>/dev/null; sleep 1
            if systemctl is-active --quiet kaskad-bot 2>/dev/null; then restarted+=" bot"
            else
                echo -e "${RED}Бот не поднялся на новой версии — откат.${NC}"
                cp -f /usr/local/bin/gokaskad.bak /usr/local/bin/gokaskad 2>/dev/null
                systemctl restart kaskad-bot 2>/dev/null
                log_action "Self-update ROLLED BACK (bot failed)"; read -p "Нажмите Enter..."; return
            fi
        fi
        systemctl is-active --quiet kaskad-monitor 2>/dev/null && { systemctl restart kaskad-monitor 2>/dev/null; restarted+=" monitor"; }
        log_action "Self-update -> v${new_ver:-?} (restarted:${restarted:- none})"
        echo -e "${GREEN}[OK] Обновлено до v${new_ver:-?}.${NC}"
        [ -n "$restarted" ] && echo -e "${GREEN}Перезапущено:${restarted}${NC}"
        echo -e "${WHITE}Бэкап прошлой версии: /usr/local/bin/gokaskad.bak${NC}"
        echo -e "${YELLOW}Откройте меню заново: ${WHITE}gokaskad${NC}"
    else
        echo -e "${RED}[ERROR] Установка не удалась. Бэкап: /usr/local/bin/gokaskad.bak${NC}"
    fi
    read -p "Нажмите Enter..."
}

# ═══════════════════════════════════════════════════════════════
#  LIVE PING with ASCII bar
# ═══════════════════════════════════════════════════════════════

make_ping_bar() {
    local ms_str="$1" width=25
    local ms_int
    ms_int=$(awk "BEGIN {printf \"%d\", $ms_str + 0.5}")
    local filled=$(( ms_int * width / 100 ))
    (( filled > width )) && filled=$width
    (( filled < 1 )) && filled=1
    local empty=$(( width - filled ))

    local color="$GREEN"
    (( ms_int > 50 )) && color="$YELLOW"
    (( ms_int > 100 )) && color="$RED"

    local bar="${color}"
    for (( b=0; b<filled; b++ )); do bar+="▓"; done
    bar+="${NC}"
    for (( b=0; b<empty; b++ )); do bar+="░"; done
    echo "$bar"
}

ping_live() {
    local ip="$1"
    local label
    label=$(fmt_ip_short "$ip")
    local -a results=()
    local count=0 lost=0 running=1

    trap 'running=0' INT

    local _port; _port=$(get_port_for_ip "$ip")
    local _mode="ICMP"
    [ -n "$_port" ] && _mode="ICMP/TCP:${_port}"

    while [ "$running" -eq 1 ]; do
        local raw method="" ms=""
        raw=$(smart_ping "$ip" 3 "${_port:-}")
        if [ -n "$raw" ]; then method="${raw%%|*}"; ms="${raw#*|}"; fi
        ((count++))

        clear
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${CYAN}  Live Ping: ${WHITE}$label${CYAN}  (${_mode})  [Ctrl+C — стоп]${NC}"
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

        if [ -n "$ms" ]; then
            results+=("$ms"); (( ${#results[@]} > 3600 )) && results=("${results[@]: -3600}")
            local bar
            bar=$(make_ping_bar "$ms")
            printf "  ${GREEN}#%-4d %7sms${NC} ${CYAN}[%s]${NC} %b\n" "$count" "$ms" "$method" "$bar"
        else
            ((lost++))
            printf "  ${RED}#%-4d   ------${NC}  " "$count"
            for (( b=0; b<25; b++ )); do echo -ne "${RED}█${NC}"; done
            echo -e " ${RED}TIMEOUT${NC}"
        fi

        echo ""
        if [ ${#results[@]} -gt 0 ]; then
            local stats
            stats=$(printf '%s\n' "${results[@]}" | LC_ALL=C awk '
                BEGIN {mn=999999; mx=0; s=0}
                {s+=$1; if($1<mn)mn=$1; if($1>mx)mx=$1}
                END {printf "%.2f|%.2f|%.2f", mn, mx, s/NR}')
            IFS='|' read -r s_min s_max s_avg <<< "$stats"
            echo -e "  ${WHITE}Мин:${NC} ${s_min}ms ${WHITE}│${NC} ${WHITE}Макс:${NC} ${s_max}ms ${WHITE}│${NC} ${WHITE}Сред:${NC} ${s_avg}ms"
        fi
        echo -e "  ${WHITE}Потеряно:${NC} $lost / $count"
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

        sleep 1
    done

    trap - INT
    echo ""
    if [ ${#results[@]} -eq 0 ] && [ "$count" -gt 0 ]; then
        echo -e "${YELLOW}━━━ Сервер не ответил ни разу ━━━${NC}"
        echo -e "${WHITE}ICMP заблокирован$([ -n "$_port" ] && echo " и TCP:$_port не удался").${NC}"
        echo ""
        echo -e "${CYAN}Чтобы включить ping на удалённом сервере:${NC}"
        echo -e "  ${WHITE}ssh root@${ip}${NC}"
        echo -e "  ${GREEN}sysctl -w net.ipv4.icmp_echo_ignore_all=0${NC}"
        echo -e "  ${GREEN}echo 'net.ipv4.icmp_echo_ignore_all=0' >> /etc/sysctl.conf${NC}"
        echo -e "  ${GREEN}iptables -A INPUT -p icmp --icmp-type echo-request -j ACCEPT${NC}"
        echo ""
    fi
    read -p "Нажмите Enter для возврата в меню..."
}

ping_menu() {
    echo -e "\n${CYAN}--- Ping серверов ---${NC}"
    local -a ips=()
    while read -r ip; do [ -n "$ip" ] && ips+=("$ip"); done <<< "$(get_target_ips)"
    if [ ${#ips[@]} -eq 0 ]; then echo -e "${YELLOW}Нет серверов.${NC}"; read -p "Enter..."; return; fi
    echo -e "Серверы:"
    for i in "${!ips[@]}"; do echo -e "  ${YELLOW}[$((i+1))]${NC} $(fmt_ip "${ips[$i]}")"; done
    echo -e "  ${YELLOW}[0]${NC} Отмена"
    read -p "Выбор: " choice
    [[ "$choice" == "0" || -z "$choice" ]] && return
    local idx=$((choice - 1))
    [ -z "${ips[$idx]:-}" ] && return
    ping_live "${ips[$idx]}"
}

# ═══════════════════════════════════════════════════════════════
#  MONITORING
# ═══════════════════════════════════════════════════════════════

add_monitor() {
    local ip="$1" interval="$2" threshold="$3" cooldown="${4:-300}"
    cat > "$MONITOR_DIR/${ip}.conf" <<EOF
MON_IP="$ip"
MON_INTERVAL=$interval
MON_THRESHOLD=$threshold
MON_COOLDOWN=$cooldown
EOF
    log_action "MONITOR ADD: $ip interval=${interval}s threshold=${threshold}ms cooldown=${cooldown}s"
    sync_monitoring_service
}

remove_monitor() {
    local ip="$1"
    rm -f "$MONITOR_DIR/${ip}.conf" "$MONITOR_DIR/.last_check_${ip}" "$MONITOR_DIR/.last_alert_${ip}"
    log_action "MONITOR REMOVE: $ip"
    sync_monitoring_service
}

has_monitors() {
    for conf in "$MONITOR_DIR"/*.conf; do [ -f "$conf" ] && return 0; done
    return 1
}

sync_monitoring_service() {
    if has_monitors; then
        systemctl is-active kaskad-monitor &>/dev/null 2>&1 || start_monitoring_silent
    else
        systemctl is-active kaskad-monitor &>/dev/null 2>&1 && stop_monitoring_silent
    fi
}

start_monitoring_silent() {
    cat > /etc/systemd/system/kaskad-monitor.service <<EOF
[Unit]
Description=Kaskad Monitoring Daemon
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/local/bin/gokaskad --monitor-daemon
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload; systemctl enable kaskad-monitor > /dev/null 2>&1; systemctl start kaskad-monitor 2>/dev/null
    log_action "Monitoring auto-started"
}

stop_monitoring_silent() {
    systemctl stop kaskad-monitor 2>/dev/null; systemctl disable kaskad-monitor 2>/dev/null; rm -f "$MONITOR_PID_FILE"
    log_action "Monitoring auto-stopped"
}

list_monitors() {
    local found=0
    for conf in "$MONITOR_DIR"/*.conf; do
        [ -f "$conf" ] || continue; found=1
        local MON_IP="" MON_INTERVAL="" MON_THRESHOLD="" MON_COOLDOWN=300; source "$conf"
        echo -e "  ${WHITE}$(fmt_ip_short "$MON_IP")${NC}  инт: ${MON_INTERVAL}s  порог: ${MON_THRESHOLD}ms  уведомл: ${MON_COOLDOWN}s"
    done
    [ "$found" -eq 0 ] && echo -e "  ${YELLOW}Нет мониторов.${NC}"
}

monitor_alert() {
    local ip="$1" ping_ms="$2" threshold="$3" cooldown="${4:-300}"
    local alert_file="$MONITOR_DIR/.last_alert_${ip}" last_alert=0
    [ -f "$alert_file" ] && last_alert=$(cat "$alert_file")
    local now; now=$(date +%s)
    (( now - last_alert < cooldown )) && return
    echo "$now" > "$alert_file"
    log_action "ALERT: $ip ping=${ping_ms}ms threshold=${threshold}ms"
    source "$KASKAD_CONF" 2>/dev/null
    if [ -n "${BOT_TOKEN:-}" ] && [ -n "${BOT_CHAT_ID:-}" ]; then
        local header; header=$(html_escape "$(fmt_ip_short "$ip")")
        local text
        [ "$ping_ms" = "TIMEOUT" ] && text="⚠️ <b>ALERT</b>: ${header}\nPing: TIMEOUT (порог: ${threshold}ms)" \
            || text="⚠️ <b>ALERT</b>: ${header}\nPing: ${ping_ms}ms (порог: ${threshold}ms)"
        tg_send "$BOT_CHAT_ID" "$text" "" > /dev/null 2>&1
    fi
}

monitor_daemon() {
    log_action "Monitor daemon started (PID $$)"; echo $$ > "$MONITOR_PID_FILE"
    while true; do
        local now; now=$(date +%s)
        for cf in "$MONITOR_DIR"/*.conf; do
            [ -f "$cf" ] || continue
            local MON_IP="" MON_INTERVAL="" MON_THRESHOLD="" MON_COOLDOWN=300; source "$cf"
            local ckf="$MONITOR_DIR/.last_check_${MON_IP}" lc=0
            [ -f "$ckf" ] && lc=$(cat "$ckf")
            if (( now - lc >= MON_INTERVAL )); then
                echo "$now" > "$ckf"
                local pr_raw; pr_raw=$(smart_ping "$MON_IP" 3)
                local pr="${pr_raw#*|}"
                if [ -z "$pr" ] || [ -z "$pr_raw" ]; then
                    monitor_alert "$MON_IP" "TIMEOUT" "$MON_THRESHOLD" "$MON_COOLDOWN"
                else
                    local pi; pi=$(awk "BEGIN {printf \"%d\", $pr + 0.5}")
                    (( pi > MON_THRESHOLD )) && monitor_alert "$MON_IP" "$pr" "$MON_THRESHOLD" "$MON_COOLDOWN"
                fi
            fi
        done
        sleep 1
    done
}

monitoring_menu() {
    while true; do
        clear
        local ms="${RED}Остановлен${NC}"
        systemctl is-active kaskad-monitor &>/dev/null 2>&1 && ms="${GREEN}Работает${NC}"
        echo -e "${CYAN}━━━ Мониторинг (авто) ━━━${NC}"
        echo -e "Статус: $ms"; echo ""; list_monitors; echo ""
        echo -e "1) Добавить"; echo -e "2) Удалить"; echo -e "0) Назад"
        read -p "Выбор: " choice || return
        case $choice in
            1)
                local -a ips=()
                while read -r ip; do [ -n "$ip" ] && ips+=("$ip"); done <<< "$(get_target_ips)"
                [ ${#ips[@]} -eq 0 ] && echo -e "${YELLOW}Нет серверов.${NC}" && read -p "Enter..." && continue
                for i in "${!ips[@]}"; do echo -e "  ${YELLOW}[$((i+1))]${NC} $(fmt_ip_short "${ips[$i]}")"; done
                read -p "Сервер: " sc; local si=$((sc-1)); [ -z "${ips[$si]:-}" ] && continue
                echo -e "Интервал: 1) 10с  2) 1мин  3) 5мин"
                read -p "> " ic; local iv=60; case $ic in 1) iv=10;; 3) iv=300;; esac
                read_validated_port "Порог (мс):"; local th="$_RET_PORT"
                echo -e "Уведомления: 1) 10с  2) 60с  3) 5мин  4) 15мин"
                read -p "> " cc; local cd=300; case $cc in 1) cd=10;; 2) cd=60;; 4) cd=900;; esac
                add_monitor "${ips[$si]}" "$iv" "$th" "$cd"
                echo -e "${GREEN}[OK]${NC}"; read -p "Enter..." ;;
            2)
                local -a mi=()
                for c in "$MONITOR_DIR"/*.conf; do [ -f "$c" ] || continue; local MON_IP=""; source "$c"; mi+=("$MON_IP"); done
                [ ${#mi[@]} -eq 0 ] && echo -e "${YELLOW}Нет.${NC}" && read -p "Enter..." && continue
                for i in "${!mi[@]}"; do echo -e "  ${YELLOW}[$((i+1))]${NC} $(fmt_ip_short "${mi[$i]}")"; done
                read -p "Номер: " dc; local di=$((dc-1))
                [ -n "${mi[$di]:-}" ] && remove_monitor "${mi[$di]}" && echo -e "${GREEN}[OK]${NC}"
                read -p "Enter..." ;;
            0) return ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  TELEGRAM BOT
# ═══════════════════════════════════════════════════════════════

tg_api() {
    local px=(); [ -n "${BOT_PROXY:-}" ] && px=(--proxy "$BOT_PROXY")
    curl -s "${px[@]}" -X POST "https://api.telegram.org/bot${BOT_TOKEN}/$1" -H "Content-Type: application/json" -d "$2" 2>/dev/null
}

tg_send() {
    local chat_id="$1" text keyboard="${3:-}"
    text=$(printf '%b' "$2")
    local payload
    if [ -n "$keyboard" ]; then
        payload=$(jq -n --arg c "$chat_id" --arg t "$text" --argjson k "$keyboard" \
            '{chat_id:$c, text:$t, parse_mode:"HTML", reply_markup:{inline_keyboard:$k}}')
    else
        payload=$(jq -n --arg c "$chat_id" --arg t "$text" \
            '{chat_id:$c, text:$t, parse_mode:"HTML"}')
    fi
    tg_api "sendMessage" "$payload"
}

tg_send_reply_kb() {
    local chat_id="$1" text keyboard="$3"
    text=$(printf '%b' "$2")
    local payload
    payload=$(jq -n --arg c "$chat_id" --arg t "$text" --argjson k "$keyboard" \
        '{chat_id:$c, text:$t, parse_mode:"HTML", reply_markup:{keyboard:$k, resize_keyboard:true, one_time_keyboard:false}}')
    tg_api "sendMessage" "$payload"
}

tg_remove_reply_kb() {
    local chat_id="$1" text="$2"
    text=$(printf '%b' "$text")
    local payload
    payload=$(jq -n --arg c "$chat_id" --arg t "$text" \
        '{chat_id:$c, text:$t, parse_mode:"HTML", reply_markup:{remove_keyboard:true}}')
    tg_api "sendMessage" "$payload"
}

tg_edit() {
    local chat_id="$1" msg_id="$2" text keyboard="${4:-}"
    text=$(printf '%b' "$3")
    local payload
    if [ -n "$keyboard" ]; then
        payload=$(jq -n --arg c "$chat_id" --argjson m "$msg_id" --arg t "$text" --argjson k "$keyboard" \
            '{chat_id:$c, message_id:$m, text:$t, parse_mode:"HTML", reply_markup:{inline_keyboard:$k}}')
    else
        payload=$(jq -n --arg c "$chat_id" --argjson m "$msg_id" --arg t "$text" \
            '{chat_id:$c, message_id:$m, text:$t, parse_mode:"HTML"}')
    fi
    tg_api "editMessageText" "$payload"
}

tg_answer_cb() {
    local payload; payload=$(jq -n --arg id "$1" --arg t "${2:-}" '{callback_query_id:$id, text:$t}')
    tg_api "answerCallbackQuery" "$payload"
}

# ─── Bot state ────────────────────────────────────────────────

bot_set_state() { local c="$1"; shift; printf '%s\n' "$@" > "$BOT_STATE_DIR/$c"; }
bot_get_state() { [ -f "$BOT_STATE_DIR/$1" ] && grep "^${2}=" "$BOT_STATE_DIR/$1" | head -1 | cut -d= -f2-; }
bot_clear_state() { rm -f "$BOT_STATE_DIR/$1"; }

# ── Ключ GitHub: ОДИН порядок поиска для обновления скрипта и для ядра AmneziaWG ──
# 1) сохранённый (меню 8 → 6 / при обновлении): bot_state/system, UPDATE_TOKEN
# 2) GITHUB_PAT в /etc/kaskad/config (пишется вместе с UPDATE_TOKEN при обновлении)
# 3) окружение: GH_TOKEN, GITHUB_TOKEN (так ключ передают загрузчики серии go)
# 4) ключ, сохранённый goInstaller: /opt/go-installer/token (0600, первая строка)
kaskad_gh_token() {
    local t="" f
    t=$(bot_get_state "system" "UPDATE_TOKEN" 2>/dev/null)
    [ -n "$t" ] || t=$( [ -r "$KASKAD_CONF" ] && source "$KASKAD_CONF" 2>/dev/null; printf '%s' "${GITHUB_PAT:-}" )
    [ -n "$t" ] || t="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
    if [ -z "$t" ]; then
        f="${GOINST_TOKEN_FILE:-/opt/go-installer/token}"
        [ -r "$f" ] && { IFS= read -r t < "$f" 2>/dev/null || true; t="${t//[[:space:]]/}"; }
    fi
    [ -n "$t" ] && printf '%s' "$t"
}
# curl с ключом: заголовок уходит через конфиг-файл 0600, а не аргументом —
# argv любого процесса читается всеми локальными пользователями (/proc/<pid>/cmdline).
kaskad_gh_curl() {   # <ключ|""> <url> <файл> [max-time]; 0 = скачан непустой файл
    local tok="$1" url="$2" out="$3" mt="${4:-90}" cfg="" rc
    if [ -n "$tok" ]; then
        cfg=$(mktemp "${KASKAD_DIR}/.hdr.XXXXXX" 2>/dev/null) || cfg=$(mktemp) || return 1
        chmod 600 "$cfg"; printf 'header = "Authorization: token %s"\n' "$tok" > "$cfg"
        curl -fsSL --config "$cfg" --connect-timeout 10 --max-time "$mt" -o "$out" "$url" 2>/dev/null; rc=$?
        rm -f "$cfg"
    else
        curl -fsSL --connect-timeout 10 --max-time "$mt" -o "$out" "$url" 2>/dev/null; rc=$?
    fi
    [ "$rc" = "0" ] && [ -s "$out" ]
}

get_menu_style() { source "$KASKAD_CONF" 2>/dev/null; echo "${MENU_STYLE:-inline}"; }

# ─── Bot keyboards ───────────────────────────────────────────

kbd_inline_main() {
    cat <<'JSON'
[
  [{"text":"🔀 AWG","callback_data":"a_u"},{"text":"🔀 VLESS","callback_data":"a_t"},{"text":"🔀 MTProto","callback_data":"a_mt"}],
  [{"text":"🛠 Custom","callback_data":"a_c"},{"text":"📋 Правила","callback_data":"lr"}],
  [{"text":"🏓 Ping","callback_data":"pm"},{"text":"📊 Монитор","callback_data":"mm"}],
  [{"text":"💻 Система","callback_data":"sys"}],
  [{"text":"❌ Удалить","callback_data":"dr"},{"text":"🗑 Сброс","callback_data":"fa"}],
  [{"text":"🏢 Хостинг","callback_data":"promo"}],
  [{"text":"⌨️ Reply-клавиатура","callback_data":"sw_reply"}]
]
JSON
}

reply_kb_json() {
    cat <<'JSON'
[
  ["🔀 AWG/WG", "🔀 VLESS"],
  ["🔀 MTProto", "🛠 Custom"],
  ["📋 Правила", "🏓 Ping"],
  ["📊 Монитор", "💻 Система"],
  ["❌ Удалить", "🗑 Сброс"],
  ["🏢 Хостинг"],
  ["/inline"]
]
JSON
}

kbd_back() { echo '[[{"text":"⬅️ Меню","callback_data":"m"}]]'; }
kbd_proto() { echo '[[{"text":"TCP","callback_data":"a_cp_tcp"},{"text":"UDP","callback_data":"a_cp_udp"}],[{"text":"⬅️ Меню","callback_data":"m"}]]'; }

kbd_ping_opts() {
    local ip="$1"
    jq -n --arg ip "$ip" '[[{"text":"1 раз","callback_data":("po:"+$ip)}],[{"text":"10 раз","callback_data":("p10:"+$ip)}],[{"text":"60 сек","callback_data":("p60:"+$ip)}],[{"text":"⬅️ Меню","callback_data":"m"}]]'
}

kbd_monitor() { cat <<'JSON'
[[{"text":"➕ Добавить","callback_data":"ma"}],[{"text":"📋 Список","callback_data":"ml"}],[{"text":"➖ Удалить","callback_data":"md"}],[{"text":"⬅️ Меню","callback_data":"m"}]]
JSON
}

kbd_intervals() {
    local ip="$1"
    jq -n --arg ip "$ip" '[[{"text":"10с","callback_data":("mi:"+$ip+":10")}],[{"text":"1мин","callback_data":("mi:"+$ip+":60")}],[{"text":"5мин","callback_data":("mi:"+$ip+":300")}],[{"text":"⬅️","callback_data":"m"}]]'
}

kbd_cooldowns() {
    local ip="$1" interval="$2" threshold="$3"
    jq -n --arg ip "$ip" --arg i "$interval" --arg t "$threshold" \
        '[[{"text":"10с","callback_data":("mc:"+$ip+":"+$i+":"+$t+":10")}],[{"text":"60с","callback_data":("mc:"+$ip+":"+$i+":"+$t+":60")}],[{"text":"5мин","callback_data":("mc:"+$ip+":"+$i+":"+$t+":300")}],[{"text":"15мин","callback_data":("mc:"+$ip+":"+$i+":"+$t+":900")}],[{"text":"⬅️","callback_data":"m"}]]'
}

build_ip_kbd() {
    local prefix="$1"; shift; local ips=("$@") rows="" first=1
    for ip in "${ips[@]}"; do
        local label; label=$(get_alias "$ip"); [ -z "$label" ] && label="$ip" || label="$label ($ip)"
        label=$(json_escape "$label")
        [ "$first" -eq 0 ] && rows+=","; rows+="[{\"text\":\"$label\",\"callback_data\":\"${prefix}:${ip}\"}]"; first=0
    done
    echo "[${rows},[{\"text\":\"⬅️ Меню\",\"callback_data\":\"m\"}]]"
}

build_delete_kbd() {
    local rules; rules=$(get_rules_list)
    local rows="" i=1 first=1
    while IFS='|' read -r proto port dest; do
        [ -z "$port" ] && continue
        local dip="${dest%:*}" label; label=$(get_alias "$dip")
        local bt="❌ :$port ($proto) → $dest"; [ -n "$label" ] && bt="❌ :$port → $label"
        bt=$(json_escape "$bt")
        [ "$first" -eq 0 ] && rows+=","; rows+="[{\"text\":\"$bt\",\"callback_data\":\"dr_${i}\"}]"; first=0; ((i++))
    done <<< "$rules"
    [ -z "$rows" ] && echo '[[{"text":"Нет правил","callback_data":"m"}]]' || echo "[${rows},[{\"text\":\"⬅️\",\"callback_data\":\"m\"}]]"
}

# ─── Bot handlers ─────────────────────────────────────────────

bot_main_menu() {
    local chat_id="$1" msg_id="${2:-}"
    bot_clear_state "$chat_id"
    local style; style=$(get_menu_style)
    local text="<b>Kaskad PRO v${KASKAD_VERSION}</b>\nIP: <code>${MY_IP:-N/A}</code>\nВыберите действие:"

    if [ "$style" = "reply" ]; then
        tg_send_reply_kb "$chat_id" "$text" "$(reply_kb_json)" > /dev/null
    else
        local kbd; kbd=$(kbd_inline_main)
        if [ -n "$msg_id" ]; then
            tg_edit "$chat_id" "$msg_id" "$text" "$kbd"
        else
            tg_send "$chat_id" "$text" "$kbd"
        fi
    fi
}

bot_handle_reply_text() {
    local chat_id="$1" text="$2"
    case "$text" in
        "🔀 AWG/WG"|"🔀 VLESS"|"🔀 MTProto"|"🛠 Custom"|"📋 Правила"|"🏓 Ping"|"📊 Монитор"|"💻 Система"|"❌ Удалить"|"🗑 Сброс"|"🏢 Хостинг") bot_clear_state "$chat_id" ;;
    esac
    case "$text" in
        "🔀 AWG/WG")       bot_set_state "$chat_id" "STATE=awaiting_ip" "PROTO=udp" "NAME=AmneziaWG" "CUSTOM=0"; tg_send "$chat_id" "🔀 <b>AmneziaWG (UDP)</b>\n\nВведите IP:" "$(kbd_back)" > /dev/null ;;
        "🔀 VLESS")         bot_set_state "$chat_id" "STATE=awaiting_ip" "PROTO=tcp" "NAME=VLESS" "CUSTOM=0"; tg_send "$chat_id" "🔀 <b>VLESS (TCP)</b>\n\nВведите IP:" "$(kbd_back)" > /dev/null ;;
        "🔀 MTProto")       bot_set_state "$chat_id" "STATE=awaiting_ip" "PROTO=tcp" "NAME=MTProto" "CUSTOM=0"; tg_send "$chat_id" "🔀 <b>MTProto (TCP)</b>\n\nВведите IP:" "$(kbd_back)" > /dev/null ;;
        "🛠 Custom")        tg_send "$chat_id" "🛠 <b>Custom Rule</b>\n\nВыберите протокол:" "$(kbd_proto)" > /dev/null ;;
        "📋 Правила")       bot_handle_callback "$chat_id" "" "" "lr_new" ;;
        "🏓 Ping")          bot_handle_callback "$chat_id" "" "" "pm_new" ;;
        "📊 Монитор")       bot_handle_callback "$chat_id" "" "" "mm_new" ;;
        "💻 Система")       local s; s=$(get_system_stats); tg_send "$chat_id" "$s" "$(kbd_back)" > /dev/null ;;
        "❌ Удалить")       tg_send "$chat_id" "❌ <b>Выберите правило:</b>" "$(build_delete_kbd)" > /dev/null ;;
        "🗑 Сброс")        tg_send "$chat_id" "🗑 <b>Уверены?</b>" '[[{"text":"✅ Да","callback_data":"fa_y"},{"text":"❌ Нет","callback_data":"m"}]]' > /dev/null ;;
        "🏢 Хостинг")      bot_handle_callback "$chat_id" "" "" "promo_new" ;;
        *) return 1 ;;
    esac
    return 0
}

bot_handle_callback() {
    local chat_id="$1" msg_id="$2" cb_id="$3" data="$4"
    [ -n "$cb_id" ] && tg_answer_cb "$cb_id" > /dev/null

    local use_send=0
    [[ "$data" == *_new ]] && use_send=1 && data="${data%_new}"

    case "$data" in
        m) bot_main_menu "$chat_id" "$msg_id" ;;
        sw_reply) save_config_val "MENU_STYLE" "reply"; bot_main_menu "$chat_id" ;;
        sw_inline) save_config_val "MENU_STYLE" "inline"; bot_main_menu "$chat_id" ;;

        a_u) bot_set_state "$chat_id" "STATE=awaiting_ip" "PROTO=udp" "NAME=AmneziaWG" "CUSTOM=0"
             [ "$use_send" -eq 1 ] && tg_send "$chat_id" "🔀 <b>AmneziaWG (UDP)</b>\n\nВведите IP:" "$(kbd_back)" > /dev/null \
                 || tg_edit "$chat_id" "$msg_id" "🔀 <b>AmneziaWG (UDP)</b>\n\nВведите IP:" "$(kbd_back)" ;;
        a_t) bot_set_state "$chat_id" "STATE=awaiting_ip" "PROTO=tcp" "NAME=VLESS" "CUSTOM=0"
             [ "$use_send" -eq 1 ] && tg_send "$chat_id" "🔀 <b>VLESS (TCP)</b>\n\nВведите IP:" "$(kbd_back)" > /dev/null \
                 || tg_edit "$chat_id" "$msg_id" "🔀 <b>VLESS (TCP)</b>\n\nВведите IP:" "$(kbd_back)" ;;
        a_mt) bot_set_state "$chat_id" "STATE=awaiting_ip" "PROTO=tcp" "NAME=MTProto" "CUSTOM=0"
              [ "$use_send" -eq 1 ] && tg_send "$chat_id" "🔀 <b>MTProto (TCP)</b>\n\nВведите IP:" "$(kbd_back)" > /dev/null \
                  || tg_edit "$chat_id" "$msg_id" "🔀 <b>MTProto (TCP)</b>\n\nВведите IP:" "$(kbd_back)" ;;
        a_c) [ "$use_send" -eq 1 ] && tg_send "$chat_id" "🛠 <b>Custom</b>\n\nПротокол:" "$(kbd_proto)" > /dev/null \
                 || tg_edit "$chat_id" "$msg_id" "🛠 <b>Custom</b>\n\nПротокол:" "$(kbd_proto)" ;;
        a_cp_tcp|a_cp_udp)
            local proto="${data#a_cp_}"
            bot_set_state "$chat_id" "STATE=awaiting_ip" "PROTO=$proto" "NAME=Custom" "CUSTOM=1"
            tg_edit "$chat_id" "$msg_id" "🛠 <b>Custom ($proto)</b>\n\nВведите IP:" "$(kbd_back)" ;;

        lr)
            local rules text=""; rules=$(get_rules_list)
            if [ -z "$rules" ]; then text="📋 <b>Нет правил.</b>"
            else
                text="📋 <b>Правила</b>\nСервер: <code>${MY_IP:-N/A}</code>\n\n"
                while IFS='|' read -r proto port dest; do
                    [ -n "$port" ] || continue
                    local dip="${dest%:*}"
                    text+="<code>${MY_IP:-*}:$port ($proto) → $dest</code>\n"
                    text+="  $(fmt_ip_tg "$dip")\n"
                done <<< "$rules"
            fi
            [ "$use_send" -eq 1 ] && tg_send "$chat_id" "$text" "$(kbd_back)" > /dev/null \
                || tg_edit "$chat_id" "$msg_id" "$text" "$(kbd_back)" ;;

        dr) [ "$use_send" -eq 1 ] && tg_send "$chat_id" "❌ <b>Выберите:</b>" "$(build_delete_kbd)" > /dev/null \
                || tg_edit "$chat_id" "$msg_id" "❌ <b>Выберите:</b>" "$(build_delete_kbd)" ;;
        dr_*)
            local idx="${data#dr_}"
            [[ "$idx" =~ ^[0-9]+$ ]] || { tg_edit "$chat_id" "$msg_id" "❌ Неверный номер." "$(kbd_back)"; return; }
            local line; line=$(get_rules_list | sed -n "${idx}p")
            [ -z "$line" ] && { tg_edit "$chat_id" "$msg_id" "Не найдено." "$(kbd_back)"; return; }
            IFS='|' read -r dp dpo dd <<< "$line"
            tg_edit "$chat_id" "$msg_id" "⚠️ <b>Удалить правило?</b>\n<code>$dp :$dpo → $dd</code>" "[[{\"text\":\"✅ Да, удалить\",\"callback_data\":\"dry_${idx}\"},{\"text\":\"⬅️ Отмена\",\"callback_data\":\"dr\"}]]" ;;
        dry_*)
            local idx="${data#dry_}"
            [[ "$idx" =~ ^[0-9]+$ ]] || { tg_edit "$chat_id" "$msg_id" "❌ Неверный номер." "$(kbd_back)"; return; }
            local line; line=$(get_rules_list | sed -n "${idx}p")
            if [ -n "$line" ]; then
                IFS='|' read -r dp dpo dd <<< "$line"
                iptables -t nat -S PREROUTING 2>/dev/null | grep "DNAT" | grep -- "kaskad:${dpo}:${dp}" | while read -r r; do eval "iptables -t nat -D ${r#-A }" 2>/dev/null; done
                iptables -S INPUT 2>/dev/null | grep "kaskad:${dpo}:${dp}" | while read -r r; do eval "iptables -D ${r#-A }" 2>/dev/null; done
                iptables -S FORWARD 2>/dev/null | grep "kaskad:${dpo}:${dp}" | while read -r r; do eval "iptables -D ${r#-A }" 2>/dev/null; done
                local _dip="${dd%:*}"; [ -z "$(get_port_for_ip "$_dip")" ] && remove_monitor "$_dip" 2>/dev/null
                remove_masquerade_if_unused; save_iptables; log_action "BOT DELETE: $dp :$dpo -> $dd"
                tg_edit "$chat_id" "$msg_id" "✅ <code>$dp :$dpo → $dd</code> удалено." "$(kbd_back)"
            else tg_edit "$chat_id" "$msg_id" "Не найдено." "$(kbd_back)"; fi ;;

        fa) tg_edit "$chat_id" "$msg_id" "🗑 <b>Уверены?</b>" '[[{"text":"✅ Да","callback_data":"fa_y"},{"text":"❌ Нет","callback_data":"m"}]]' ;;
        fa_y)
            flush_all_kaskad_rules
            remove_masquerade_if_unused
            rm -f "$MONITOR_DIR"/*.conf 2>/dev/null; sync_monitoring_service
            save_iptables; log_action "BOT FLUSH"
            tg_edit "$chat_id" "$msg_id" "✅ Очищено." "$(kbd_back)" ;;

        sys) local s; s=$(get_system_stats)
             [ "$use_send" -eq 1 ] && tg_send "$chat_id" "$s" "$(kbd_back)" > /dev/null \
                 || tg_edit "$chat_id" "$msg_id" "$s" "$(kbd_back)" ;;

        promo)
            local pt="<b>🏢 Хостинг, который работает</b>\n\n<b>🌍 РФ и Европа</b>\n👉 https://vk.cc/ct29NQ\n\n<code>OFF60</code> — 60% скидка\n<code>antenka20</code> — +20% (3мес)\n<code>antenka6</code> — +15% (6мес)\n<code>antenka12</code> — +5% (12мес)\n\n<b>🇧🇾 Беларусь</b>\n👉 https://vk.cc/cUxAhj\n<code>OFF60</code> — 60% скидка"
            [ "$use_send" -eq 1 ] && tg_send "$chat_id" "$pt" "$(kbd_back)" > /dev/null \
                || tg_edit "$chat_id" "$msg_id" "$pt" "$(kbd_back)" ;;

        pm) local -a ips=()
            while read -r ip; do [ -n "$ip" ] && ips+=("$ip"); done <<< "$(get_target_ips)"
            if [ ${#ips[@]} -eq 0 ]; then
                [ "$use_send" -eq 1 ] && tg_send "$chat_id" "🏓 Нет серверов." "$(kbd_back)" > /dev/null \
                    || tg_edit "$chat_id" "$msg_id" "🏓 Нет серверов." "$(kbd_back)"
            else
                [ "$use_send" -eq 1 ] && tg_send "$chat_id" "🏓 <b>Сервер:</b>" "$(build_ip_kbd "ps" "${ips[@]}")" > /dev/null \
                    || tg_edit "$chat_id" "$msg_id" "🏓 <b>Сервер:</b>" "$(build_ip_kbd "ps" "${ips[@]}")"
            fi ;;
        ps:*) local ip="${data#ps:}"; local lb; lb=$(html_escape "$(fmt_ip_short "$ip")")
              tg_edit "$chat_id" "$msg_id" "🏓 <b>$lb</b>\nРежим:" "$(kbd_ping_opts "$ip")" ;;
        po:*) local ip="${data#po:}"; local lb; lb=$(html_escape "$(fmt_ip_short "$ip")")
              ( local raw; raw=$(smart_ping "$ip" 3)
                if [ -n "$raw" ]; then
                    local mtd="${raw%%|*}" pms="${raw#*|}"
                    tg_send "$chat_id" "🏓 <b>$lb</b>\n<code>${pms} ms</code> [$mtd]" "$(kbd_back)" > /dev/null
                else
                    tg_send "$chat_id" "🏓 <b>$lb</b>\n<code>timeout</code>\n\n<i>Сервер не ответил.\nВключите ping:</i>\n<code>sysctl -w net.ipv4.icmp_echo_ignore_all=0</code>\n<code>iptables -A INPUT -p icmp --icmp-type echo-request -j ACCEPT</code>" "$(kbd_back)" > /dev/null
                fi ) & ;;
        p10:*) local ip="${data#p10:}"; local lb; lb=$(html_escape "$(fmt_ip_short "$ip")")
               ( local resp; resp=$(tg_send "$chat_id" "🏓 $lb (10x)..." "")
                 local mid; mid=$(echo "$resp" | jq -r '.result.message_id // empty')
                 local -a res=(); local lost=0 txt=""
                 for n in $(seq 1 10); do
                     local raw; raw=$(smart_ping "$ip" 3)
                     if [ -n "$raw" ]; then
                         local mtd="${raw%%|*}" pms="${raw#*|}"
                         res+=("$pms"); txt+="#$n: ${pms}ms [$mtd]\n"
                     else ((lost++)); txt+="#$n: timeout\n"; fi
                     sleep 1
                 done
                 local sm="🏓 <b>$lb (10x)</b>\n${txt}"
                 [ ${#res[@]} -gt 0 ] && { local av; av=$(printf '%s\n' "${res[@]}" | LC_ALL=C awk '{s+=$1} END {printf "%.2f",s/NR}'); sm+="\n<b>Среднее: ${av}ms</b>"; }
                 sm+="\nПотеряно: $lost/10"
                 if [ ${#res[@]} -eq 0 ]; then sm+="\n\n<i>Включите ping:</i>\n<code>sysctl -w net.ipv4.icmp_echo_ignore_all=0</code>"; fi
                 [ -n "$mid" ] && tg_edit "$chat_id" "$mid" "$sm" "$(kbd_back)" > /dev/null || tg_send "$chat_id" "$sm" "$(kbd_back)" > /dev/null
               ) & ;;
        p60:*) local ip="${data#p60:}"; local lb; lb=$(html_escape "$(fmt_ip_short "$ip")")
               ( local resp; resp=$(tg_send "$chat_id" "🏓 $lb (60с)..." "")
                 local mid; mid=$(echo "$resp" | jq -r '.result.message_id // empty')
                 local -a res=(); local lost=0
                 for n in $(seq 1 60); do
                     local raw; raw=$(smart_ping "$ip" 3)
                     if [ -n "$raw" ]; then
                         local pms="${raw#*|}"; res+=("$pms")
                     else ((lost++)); fi
                     if (( n % 10 == 0 )) && [ -n "$mid" ]; then
                         local p="🏓 <b>$lb</b>: ${n}/60с\nОК: ${#res[@]} | Lost: $lost"
                         [ ${#res[@]} -gt 0 ] && { local pa; pa=$(printf '%s\n' "${res[@]}" | LC_ALL=C awk '{s+=$1} END {printf "%.2f",s/NR}'); p+="\nСред: ${pa}ms"; }
                         tg_edit "$chat_id" "$mid" "$p" "" > /dev/null
                     fi; sleep 1
                 done
                 local sm="🏓 <b>$lb (60с) — готово</b>\n"
                 if [ ${#res[@]} -gt 0 ]; then
                     local st; st=$(printf '%s\n' "${res[@]}" | LC_ALL=C awk 'BEGIN{mn=999999;mx=0;s=0}{s+=$1;if($1<mn)mn=$1;if($1>mx)mx=$1}END{printf "%.2f|%.2f|%.2f",mn,mx,s/NR}')
                     IFS='|' read -r sn sx sa <<< "$st"; sm+="Мин: ${sn}ms\nМакс: ${sx}ms\nСред: ${sa}ms\n"
                 fi; sm+="Потеряно: $lost/60"
                 if [ ${#res[@]} -eq 0 ]; then sm+="\n\n<i>Включите ping:</i>\n<code>sysctl -w net.ipv4.icmp_echo_ignore_all=0</code>"; fi
                 [ -n "$mid" ] && tg_edit "$chat_id" "$mid" "$sm" "$(kbd_back)" > /dev/null || tg_send "$chat_id" "$sm" "$(kbd_back)" > /dev/null
               ) & ;;

        mm) [ "$use_send" -eq 1 ] && tg_send "$chat_id" "📊 <b>Мониторинг</b>" "$(kbd_monitor)" > /dev/null \
                || tg_edit "$chat_id" "$msg_id" "📊 <b>Мониторинг</b>" "$(kbd_monitor)" ;;
        ma) local -a ips=()
            while read -r ip; do [ -n "$ip" ] && ips+=("$ip"); done <<< "$(get_target_ips)"
            [ ${#ips[@]} -eq 0 ] && tg_edit "$chat_id" "$msg_id" "Нет серверов." "$(kbd_back)" \
                || tg_edit "$chat_id" "$msg_id" "📊 Сервер:" "$(build_ip_kbd "ma" "${ips[@]}")" ;;
        ma:*) local ip="${data#ma:}"; tg_edit "$chat_id" "$msg_id" "📊 <b>$(html_escape "$(fmt_ip_short "$ip")")</b>\nИнтервал:" "$(kbd_intervals "$ip")" ;;
        mi:*) local rest="${data#mi:}"; local ip="${rest%:*}" interval="${rest##*:}"
              { validate_ip "$ip" && [[ "$interval" =~ ^[0-9]+$ ]]; } || { tg_edit "$chat_id" "$msg_id" "❌ Ошибка данных." "$(kbd_back)"; return; }
              bot_set_state "$chat_id" "STATE=awaiting_threshold" "MON_IP=$ip" "MON_INTERVAL=$interval"
              tg_edit "$chat_id" "$msg_id" "📊 <b>$(html_escape "$(fmt_ip_short "$ip")")</b> (${interval}с)\n\nПорог (мс):" "$(kbd_back)" ;;
        mc:*) local rest="${data#mc:}"; IFS=':' read -r ip interval threshold cooldown <<< "$rest"
              { validate_ip "$ip" && [[ "$interval" =~ ^[0-9]+$ ]] && [[ "$threshold" =~ ^[0-9]+$ ]] && [[ "$cooldown" =~ ^[0-9]+$ ]]; } || { tg_edit "$chat_id" "$msg_id" "❌ Ошибка данных." "$(kbd_back)"; return; }
              add_monitor "$ip" "$interval" "$threshold" "$cooldown"
              tg_edit "$chat_id" "$msg_id" "✅ <b>$(html_escape "$(fmt_ip_short "$ip")")</b>\n${interval}с | ${threshold}мс | уведомл: ${cooldown}с" "$(kbd_back)" ;;
        ml) local t="📊 <b>Мониторы:</b>\n"; local found=0
            for c in "$MONITOR_DIR"/*.conf; do
                [ -f "$c" ] || continue; found=1
                local MON_IP="" MON_INTERVAL="" MON_THRESHOLD="" MON_COOLDOWN=300; source "$c"
                t+="$(fmt_ip_tg "$MON_IP")\n  ${MON_INTERVAL}с | ${MON_THRESHOLD}мс | ${MON_COOLDOWN}с\n"
            done
            [ "$found" -eq 0 ] && t+="<i>Нет.</i>"
            tg_edit "$chat_id" "$msg_id" "$t" "$(kbd_monitor)" ;;
        md) local -a mi=()
            for c in "$MONITOR_DIR"/*.conf; do [ -f "$c" ] || continue; local MON_IP=""; source "$c"; mi+=("$MON_IP"); done
            [ ${#mi[@]} -eq 0 ] && tg_edit "$chat_id" "$msg_id" "Нет." "$(kbd_monitor)" \
                || tg_edit "$chat_id" "$msg_id" "📊 Удалить:" "$(build_ip_kbd "md" "${mi[@]}")" ;;
        md:*) local ip="${data#md:}"
              validate_ip "$ip" || { tg_edit "$chat_id" "$msg_id" "❌ Неверный IP." "$(kbd_monitor)"; return; }
              remove_monitor "$ip"
              tg_edit "$chat_id" "$msg_id" "✅ $(html_escape "$(fmt_ip_short "$ip")") удалён." "$(kbd_monitor)" ;;
    esac
}

bot_handle_message() {
    local chat_id="$1" text="$2"

    if [ "$text" = "/start" ] || [ "$text" = "/menu" ]; then bot_main_menu "$chat_id"; return; fi
    if [ "$text" = "/id" ]; then tg_send "$chat_id" "Ваш Chat ID: <code>$chat_id</code>" "" > /dev/null; return; fi
    if [ "$text" = "/help" ]; then tg_send "$chat_id" "<b>Kaskad PRO — команды</b>\n/start, /menu — меню\n/inline — inline-кнопки\n/id — ваш Chat ID\n/help — справка\n\nКаскады, ping, мониторинг и система — через кнопки." "$(kbd_back)" > /dev/null; return; fi
    if [ "$text" = "/inline" ]; then
        save_config_val "MENU_STYLE" "inline"
        tg_remove_reply_kb "$chat_id" "Переключено на inline-кнопки." > /dev/null
        bot_main_menu "$chat_id"; return
    fi

    local style; style=$(get_menu_style)
    if [ "$style" = "reply" ]; then
        # reply-keyboard buttons take priority even mid-flow; nav buttons self-clear stale state
        if bot_handle_reply_text "$chat_id" "$text"; then return; fi
    fi

    local state; state=$(bot_get_state "$chat_id" "STATE")

    case "$state" in
        awaiting_ip)
            if ! validate_ip "$text"; then
                tg_send "$chat_id" "❌ Некорректный IP. Ещё раз:" "$(kbd_back)" > /dev/null; return
            fi
            local proto name custom
            proto=$(bot_get_state "$chat_id" "PROTO"); name=$(bot_get_state "$chat_id" "NAME"); custom=$(bot_get_state "$chat_id" "CUSTOM")
            if [ "$custom" = "1" ]; then
                bot_set_state "$chat_id" "STATE=awaiting_in_port" "PROTO=$proto" "NAME=$name" "CUSTOM=1" "TARGET_IP=$text"
                tg_send "$chat_id" "IP: <code>$text</code> ✅\n\n<b>ВХОДЯЩИЙ</b> порт:" "$(kbd_back)" > /dev/null
            else
                bot_set_state "$chat_id" "STATE=awaiting_port" "PROTO=$proto" "NAME=$name" "CUSTOM=0" "TARGET_IP=$text"
                tg_send "$chat_id" "IP: <code>$text</code> ✅\n\nВведите порт:" "$(kbd_back)" > /dev/null
            fi ;;
        awaiting_port)
            if ! validate_port "$text"; then tg_send "$chat_id" "❌ Порт (1-65535)." "" > /dev/null; return; fi
            local proto name target_ip
            proto=$(bot_get_state "$chat_id" "PROTO"); name=$(bot_get_state "$chat_id" "NAME"); target_ip=$(bot_get_state "$chat_id" "TARGET_IP")
            local port="$text"
            local probe_msg; probe_msg=$(tg_send "$chat_id" "🔍 Проверяю <code>$target_ip:$port</code>..." "")
            local probe_mid; probe_mid=$(echo "$probe_msg" | jq -r '.result.message_id // empty')
            local probe_result; probe_result=$(probe_server_tg "$target_ip" "$port" "$proto")
            local info_text="<code>$target_ip:$port</code>\n${probe_result}\nВведите имя (или <code>-</code> — пропустить):"
            [ -n "$probe_mid" ] && tg_edit "$chat_id" "$probe_mid" "$info_text" "$(kbd_back)" > /dev/null \
                || tg_send "$chat_id" "$info_text" "$(kbd_back)" > /dev/null
            bot_set_state "$chat_id" "STATE=awaiting_name" "PROTO=$proto" "NAME=$name" "CUSTOM=0" "TARGET_IP=$target_ip" "PORT=$port"
            ;;
        awaiting_name)
            local proto name custom target_ip port
            proto=$(bot_get_state "$chat_id" "PROTO"); name=$(bot_get_state "$chat_id" "NAME")
            custom=$(bot_get_state "$chat_id" "CUSTOM"); target_ip=$(bot_get_state "$chat_id" "TARGET_IP")
            port=$(bot_get_state "$chat_id" "PORT")
            if [ "$text" != "-" ] && [ -n "$text" ]; then set_alias "$target_ip" "$text"; fi
            bot_set_state "$chat_id" "STATE=awaiting_note" "PROTO=$proto" "NAME=$name" "CUSTOM=$custom" "TARGET_IP=$target_ip" "PORT=$port" "IN_PORT=$(bot_get_state "$chat_id" "IN_PORT")" "OUT_PORT=$(bot_get_state "$chat_id" "OUT_PORT")"
            tg_send "$chat_id" "Примечание (или <code>-</code> — пропустить):" "$(kbd_back)" > /dev/null
            ;;
        awaiting_note)
            local proto name custom target_ip port
            proto=$(bot_get_state "$chat_id" "PROTO"); name=$(bot_get_state "$chat_id" "NAME")
            custom=$(bot_get_state "$chat_id" "CUSTOM"); target_ip=$(bot_get_state "$chat_id" "TARGET_IP")
            if [ "$text" != "-" ] && [ -n "$text" ]; then set_alias_note "$target_ip" "$text"; fi
            if [ "$custom" = "1" ]; then
                local in_port out_port
                in_port=$(bot_get_state "$chat_id" "IN_PORT"); out_port=$(bot_get_state "$chat_id" "OUT_PORT")
                bot_clear_state "$chat_id"
                apply_iptables_rules "$proto" "$in_port" "$out_port" "$target_ip" "$name"
                tg_send "$chat_id" "✅ <b>Custom</b>\n<code>$proto ${MY_IP:-*}:$in_port → $target_ip:$out_port</code>\n$(fmt_ip_tg "$target_ip")" "$(kbd_back)" > /dev/null
            else
                port=$(bot_get_state "$chat_id" "PORT")
                bot_clear_state "$chat_id"
                apply_iptables_rules "$proto" "$port" "$port" "$target_ip" "$name"
                tg_send "$chat_id" "✅ <b>$name</b>\n<code>$proto ${MY_IP:-*}:$port → $target_ip:$port</code>\n$(fmt_ip_tg "$target_ip")" "$(kbd_back)" > /dev/null
            fi ;;
        awaiting_in_port)
            if ! validate_port "$text"; then tg_send "$chat_id" "❌ Порт." "" > /dev/null; return; fi
            local proto name target_ip
            proto=$(bot_get_state "$chat_id" "PROTO"); name=$(bot_get_state "$chat_id" "NAME"); target_ip=$(bot_get_state "$chat_id" "TARGET_IP")
            bot_set_state "$chat_id" "STATE=awaiting_out_port" "PROTO=$proto" "NAME=$name" "CUSTOM=1" "TARGET_IP=$target_ip" "IN_PORT=$text"
            tg_send "$chat_id" "Вход: <code>$text</code> ✅\n\n<b>ИСХОДЯЩИЙ</b> порт:" "$(kbd_back)" > /dev/null ;;
        awaiting_out_port)
            if ! validate_port "$text"; then tg_send "$chat_id" "❌ Порт." "" > /dev/null; return; fi
            local proto name target_ip in_port
            proto=$(bot_get_state "$chat_id" "PROTO"); name=$(bot_get_state "$chat_id" "NAME")
            target_ip=$(bot_get_state "$chat_id" "TARGET_IP"); in_port=$(bot_get_state "$chat_id" "IN_PORT")
            local probe_msg; probe_msg=$(tg_send "$chat_id" "🔍 Проверяю <code>$target_ip:$text</code>..." "")
            local probe_mid; probe_mid=$(echo "$probe_msg" | jq -r '.result.message_id // empty')
            local probe_result; probe_result=$(probe_server_tg "$target_ip" "$text" "$proto")
            local info_text="<code>$target_ip</code> (вход:$in_port → выход:$text)\n${probe_result}\nВведите имя (или <code>-</code> — пропустить):"
            [ -n "$probe_mid" ] && tg_edit "$chat_id" "$probe_mid" "$info_text" "$(kbd_back)" > /dev/null \
                || tg_send "$chat_id" "$info_text" "$(kbd_back)" > /dev/null
            bot_set_state "$chat_id" "STATE=awaiting_name" "PROTO=$proto" "NAME=$name" "CUSTOM=1" "TARGET_IP=$target_ip" "IN_PORT=$in_port" "OUT_PORT=$text"
            ;;
        awaiting_threshold)
            if ! validate_port "$text"; then tg_send "$chat_id" "❌ Число (1-65535):" "" > /dev/null; return; fi
            local mon_ip mon_interval
            mon_ip=$(bot_get_state "$chat_id" "MON_IP"); mon_interval=$(bot_get_state "$chat_id" "MON_INTERVAL")
            bot_clear_state "$chat_id"
            tg_send "$chat_id" "📊 <b>$(html_escape "$(fmt_ip_short "$mon_ip")")</b>\n${mon_interval}с | ${text}мс\n\nЧастота уведомлений:" "$(kbd_cooldowns "$mon_ip" "$mon_interval" "$text")" > /dev/null ;;
        *) tg_send "$chat_id" "/start или /menu" "" > /dev/null ;;
    esac
}

# ─── Bot daemon ───────────────────────────────────────────────

bot_daemon() {
    log_action "Bot daemon started (PID $$)"; echo $$ > "$BOT_PID_FILE"
    source "$KASKAD_CONF"
    [ -z "$BOT_TOKEN" ] && log_action "BOT ERROR: no token" && exit 1
    detect_interface; get_my_ip
    local _px=(); [ -n "${BOT_PROXY:-}" ] && _px=(--proxy "$BOT_PROXY")
    tg_api "setMyCommands" '{"commands":[{"command":"start","description":"Меню"},{"command":"menu","description":"Меню"},{"command":"help","description":"Справка"},{"command":"id","description":"Мой Chat ID"},{"command":"inline","description":"Inline-кнопки"}]}' > /dev/null 2>&1
    tg_api "setChatMenuButton" '{"menu_button":{"type":"commands"}}' > /dev/null 2>&1
    local offset_file="$BOT_STATE_DIR/.offset"
    local offset=0
    [ -f "$offset_file" ] && offset=$(cat "$offset_file" 2>/dev/null)
    [[ "$offset" =~ ^[0-9]+$ ]] || offset=0
    while true; do
        local response; response=$(curl -s "${_px[@]}" --max-time 35 "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?offset=${offset}&timeout=30" 2>/dev/null)
        [ -z "$response" ] && sleep 2 && continue
        local ok; ok=$(echo "$response" | jq -r '.ok // "false"')
        [ "$ok" != "true" ] && sleep 5 && continue
        local cnt; cnt=$(echo "$response" | jq '.result | length')
        for (( i=0; i<cnt; i++ )); do
            local upd; upd=$(echo "$response" | jq ".result[$i]")
            local uid; uid=$(echo "$upd" | jq -r '.update_id'); offset=$((uid + 1)); echo "$offset" > "$offset_file"
            local cbd; cbd=$(echo "$upd" | jq -r '.callback_query.data // empty')
            if [ -n "$cbd" ]; then
                local cbi cci cmi
                cbi=$(echo "$upd" | jq -r '.callback_query.id')
                cci=$(echo "$upd" | jq -r '.callback_query.message.chat.id')
                cmi=$(echo "$upd" | jq -r '.callback_query.message.message_id')
                if [ -z "$BOT_CHAT_ID" ] || [ "$cci" != "$BOT_CHAT_ID" ]; then tg_answer_cb "$cbi" "⛔ Доступ не настроен" > /dev/null; continue; fi
                bot_handle_callback "$cci" "$cmi" "$cbi" "$cbd"
            else
                local mci mtx
                mci=$(echo "$upd" | jq -r '.message.chat.id // empty')
                mtx=$(echo "$upd" | jq -r '.message.text // empty')
                if [ -n "$mci" ] && [ -n "$mtx" ]; then
                    if [ -z "$BOT_CHAT_ID" ]; then tg_send "$mci" "⛔ Бот ещё не привязан к владельцу.\nВаш Chat ID: <code>$mci</code>\nЗадайте его на сервере: меню 8 → 3." "" > /dev/null; continue; fi
                    [ "$mci" != "$BOT_CHAT_ID" ] && tg_send "$mci" "⛔ Нет доступа.\nChat ID: <code>$mci</code>" "" > /dev/null && continue
                    bot_handle_message "$mci" "$mtx"
                fi
            fi
        done
    done
}

start_bot() {
    source "$KASKAD_CONF"
    [ -z "$BOT_TOKEN" ] && echo -e "${RED}Задайте BOT_TOKEN!${NC}" && return
    [ -z "${BOT_CHAT_ID:-}" ] && echo -e "${YELLOW}[!] Chat ID не задан — пока он не привязан, бот только сообщает свой Chat ID и команды не выполняет (меню 8 → 3).${NC}"
    [ -f "$BOT_PID_FILE" ] && kill -0 "$(cat "$BOT_PID_FILE")" 2>/dev/null && echo -e "${YELLOW}Уже запущен.${NC}" && return
    cat > /etc/systemd/system/kaskad-bot.service <<EOF
[Unit]
Description=Kaskad Telegram Bot
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/local/bin/gokaskad --bot-daemon
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload; systemctl enable kaskad-bot > /dev/null 2>&1; systemctl start kaskad-bot; sleep 1
    systemctl is-active kaskad-bot &>/dev/null && echo -e "${GREEN}[OK] Бот запущен.${NC}" && log_action "Bot started" \
        || echo -e "${RED}[ERROR] journalctl -u kaskad-bot${NC}"
}

stop_bot() {
    systemctl stop kaskad-bot 2>/dev/null; systemctl disable kaskad-bot 2>/dev/null; rm -f "$BOT_PID_FILE"
    echo -e "${GREEN}[OK] Остановлен.${NC}"; log_action "Bot stopped"
}

# ═══════════════════════════════════════════════════════════════
#  TELEGRAM API EGRESS — VLESS via dedicated xray ("Прокси для API Telegram")
# ═══════════════════════════════════════════════════════════════
EGRESS_DIR="$KASKAD_DIR/egress"
EGRESS_XRAY="$EGRESS_DIR/xray"
EGRESS_UP="$EGRESS_DIR/upstream.txt"
EGRESS_CFG="$EGRESS_DIR/config.json"
EGRESS_PARSE_PY="$EGRESS_DIR/egress_parse.py"
EGRESS_SERVICE="kaskad-egress"
EGRESS_SOCKS_PORT="${EGRESS_SOCKS_PORT:-10808}"
EGRESS_API_PORT="${EGRESS_API_PORT:-10851}"
EGRESS_MAX="${EGRESS_MAX:-100}"
EGRESS_MSS="${EGRESS_MSS:-1000}"  # clamp egress MSS (tcpMaxSeg): fixes low-MTU paths where large Reality/Vision segments are dropped. 0 = no clamp.
EGRESS_LOSS="$EGRESS_DIR/loss.log"
EGRESS_PROBE_SVC="kaskad-egress-probe"
AWG_DIR="$EGRESS_DIR/awg"
AWG_GO="$AWG_DIR/amneziawg-go"
AWG_TOOL="$AWG_DIR/awg"
# Репозиторий с ядром AmneziaWG (публичный монорепозиторий potap1978/go-all).
# AWG_BIN_DIR — путь к bin/ внутри него; для одиночного репозитория задайте
# AWG_BIN_DIR=bin. Переопределяется окружением (selftest подставляет несуществующий).
AWG_REPO="${AWG_REPO:-potap1978/go-all}"
AWG_BIN_DIR="${AWG_BIN_DIR:-gokaskad/bin}"
# Observatory health-probe MUST be plain HTTP: xray's internal HTTPS probe can
# fail with "tls: CurvePreferences includes unsupported curve" on go1.26+ builds,
# which would mark every (working) upstream dead. A plain-HTTP 204 avoids it.
EGRESS_OBSPROBE="${EGRESS_OBSPROBE:-http://cp.cloudflare.com/generate_204}"
EGRESS_TESTURL="${EGRESS_TESTURL:-https://api.telegram.org/}"
# Dedicated egress xray (independent of any panel binary). If GitHub "latest"
# (stable, no pre-releases) can't be queried, fall back to a known-good STABLE
# build — some pre-releases break OUTBOUND TLS (the curve bug noted above).
EGRESS_XVER_FALLBACK="${EGRESS_XVER_FALLBACK:-v26.3.27}"
BOLD='\033[1m'; DIM='\033[2m'

# Count non-blank keys as ONE clean integer (grep -c prints "0" AND exits 1 on
# an empty file, so a bare `|| echo 0` yields "0\n0" and breaks integer tests).
_egr_count() { local n; n=$(grep -c . "$EGRESS_UP" 2>/dev/null); echo "${n:-0}"; }

# ── Write the embedded VLESS->xray parser to disk (single-file app) ──────────
# Копия на диске сравнивается со встроенной по СОДЕРЖИМОМУ: отличается (сервер после self_update,
# любая правка парсера) — подменяется целиком. Номер PARSER_VER остаётся для людей — его легко
# забыть поднять. Пишем в свой временный файл и подменяем переименованием: оборванная запись
# (диск полон) или второй запуск рядом не оставят вместо рабочего парсера обрывок.
egress_ensure_parser() {
    mkdir -p "$EGRESS_DIR"; chmod 700 "$EGRESS_DIR" 2>/dev/null
    local tmp; tmp=$(mktemp "$EGRESS_DIR/.parse.XXXXXX" 2>/dev/null) || return 1
    if ! cat > "$tmp" <<'PYEOF'
#!/usr/bin/env python3
# PARSER_VER=9 — Kaskad unified egress parser: VLESS + AmneziaWG in one pool.
# Inputs: vless:// , vless subscription (base64), AWG vpn:// (Amnezia), AWG .conf (lines 1.0/1.5/2.0/3.1).
# Pool storage: one line per key — "vless://..." OR "awg://<base64(json record)>".
import sys, json, base64, argparse, urllib.parse, re

MAX_KEYS = 100
# AmneziaWG [Interface]: what goes to «awg setconf» verbatim (order as in the Amnezia app's template.conf).
AWG_IFACE_KEYS = ["Jc","Jmin","Jmax","S1","S2","S3","S4","H1","H2","H3","H4","I1","I2","I3","I4","I5"]
# AmneziaWG 3.x (line 3.1): values (number, range "N-M", base64 key) and on/off toggles.
# They need amneziawg-go + awg 3.1: the 1.x/2.0 core rejects the WHOLE config on any of them.
AWG3_VALUE_KEYS = ["HeaderProtectionKey","ContentPaddingAddition","RekeyAfterTime","RekeyTimeout",
                   "RejectAfterTime","KeepaliveTimeout","MaxHandshakeAttempts"]
AWG3_TOGGLE_KEYS = ["RandomTrailers","DisableCookies"]
AWG_ALL_KEYS = AWG_IFACE_KEYS + AWG3_VALUE_KEYS + AWG3_TOGGLE_KEYS
AWG_CANON = {k.lower(): k for k in AWG_ALL_KEYS}   # awg matches key names case-insensitively
AWG_MARK_BASE = 256   # xray sockopt mark / route table = AWG_MARK_BASE + index

# ───────────────────────── helpers ─────────────────────────
def _b64e(s): return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
def _b64d(s): return base64.urlsafe_b64decode(s + "="*(-len(s) % 4)).decode("utf-8","replace")

def _maybe_b64_vless(text):
    t = "".join(text.split())
    if "vless://" in text: return text
    for dec in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            out = dec(t + "="*(-len(t)%4)).decode("utf-8","ignore")
            if "vless://" in out or "vpn://" in out or "[Interface]" in out: return out
        except Exception: pass
    return text

def _q1(qs, key, default=""):
    v = qs.get(key); return v[0] if v else default

# The address of a key comes from someone else's link (a subscription too) and goes to the menu, ping and xray: only an IP
# address or a domain in ASCII (IDN — in punycode), a port 1..65535. Anything else — the key is not taken.
_HOST = re.compile(r"^[a-z0-9_](?:[a-z0-9_.-]{0,251}[a-z0-9_.])?$")
def _host(h):
    """The host of a key: an IP address, or a domain in ASCII (IDN — in punycode); None — anything else (spaces, «$(…)»,
    quotes, control bytes)."""
    h = str(h or "").strip().lower()
    if "%" in h: return None                          # an IPv6 zone «fe80::1%eth0»: ip_address takes any tail (3.9+)
    try:
        import ipaddress
        return str(ipaddress.ip_address(h))
    except ValueError: pass
    try: h = h.encode("idna").decode("ascii")
    except Exception: return None
    return h if _HOST.match(h) else None
def _port(p, default=None):
    """A port 1..65535 as int; ``default`` for none; None — anything else."""
    if p is None or p == "": return default
    p = str(p).strip()
    if not re.match(r"^[0-9]{1,5}$", p): return None
    p = int(p)
    return p if 0 < p < 65536 else None
def _vless_addr(u):
    """(host, port) of a vless:// key (urlparse result) — checked (_host, _port); None — the address is not usable."""
    try:
        host = _host(u.hostname)
        hp = u.netloc.rpartition("@")[2]
        if hp.startswith("["):
            tail = hp.partition("]")[2]
            if tail and not tail.startswith(":"): return None
            ptext = tail[1:]
        else:
            ptext = hp.partition(":")[2]                  # as urllib: «2001:db8::1:443», «h:1:2» — no port
    except ValueError: return None
    port = _port(ptext, 443)
    return (host, port) if host and port else None
_AWG_MIN_PREFIX = {4: 8, 6: 64}   # wg-easy and the like give clients «10.8.0.2/24»; shorter — not an address of a client
def _awg_ifaddr(a):
    """The Address of an AmneziaWG key (the first of the list) — an IP address of the client, without a prefix (/32,
    /128) or with one not shorter than /8 (IPv4) or /64 (IPv6): it goes to «ip address replace … noprefixroute» of the
    leg; None — not one."""
    a = str(a or "").strip()
    if not a or "%" in a: return None
    try: i = __import__("ipaddress").ip_interface(a)
    except ValueError: return None
    if i.network.prefixlen < _AWG_MIN_PREFIX[i.version] or i.ip.is_unspecified or i.ip.is_multicast: return None
    return str(i)
def awg_problem(rec):
    """Why an AmneziaWG record is unusable — "bad_link" (no usable Endpoint host:port, or Address) — or ""."""
    try:
        if not _awg_addr((rec.get("peer") or {}).get("endpoint")): return "bad_link"
        if rec.get("address") and not _awg_ifaddr(rec.get("address")): return "bad_link"
    except Exception:
        return "bad_link"
    return ""
def _awg_addr(endpoint):
    """(host, port) of an AmneziaWG «Endpoint = host:port» (IPv6 in brackets) — checked; None — not usable."""
    e = str(endpoint or "").strip()
    host, sep, port = e.rpartition(":")
    if not sep: return None
    host = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    host, port = _host(host), _port(port)
    return (host, port) if host and port else None
_TERM_BAD = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
def _term(s):
    """Text of a key for the terminal of the menu: no control bytes (ESC, BEL, C0/C1 — the terminal would run them)."""
    return _TERM_BAD.sub(" ", str(s if s is not None else "")).strip()

# ───────────────────────── VLESS ─────────────────────────
def vless_outbound(link, tag, mss=0):
    try:
        u = urllib.parse.urlparse(link)
        if u.scheme != "vless": return None
        uuid = urllib.parse.unquote(u.username or ""); addr = _vless_addr(u)
        if not uuid or not addr: return None
        host, port = addr
        qs = urllib.parse.parse_qs(u.query)
        net = _q1(qs,"type","tcp") or "tcp";  net = "tcp" if net=="raw" else net
        sec = _q1(qs,"security","none") or "none"
        if net in ("http","h2"): return None
        if sec=="reality" and net not in ("tcp","grpc","xhttp"): return None
        sni=_q1(qs,"sni") or _q1(qs,"host") or host; fp=_q1(qs,"fp","chrome") or "chrome"
        flow=_q1(qs,"flow",""); alpn=_q1(qs,"alpn","")
        user={"id":uuid,"encryption":"none"}
        if flow and net=="tcp": user["flow"]=flow
        stream={"network":net,"security":sec}
        if sec=="reality":
            stream["realitySettings"]={"serverName":sni,"fingerprint":fp,"publicKey":_q1(qs,"pbk"),"shortId":_q1(qs,"sid"),"spiderX":_q1(qs,"spx","/") or "/"}
        elif sec=="tls":
            tls={"serverName":sni,"fingerprint":fp}
            if _q1(qs,"allowInsecure","0") in ("1","true"): tls["allowInsecure"]=True
            if alpn: tls["alpn"]=[a for a in alpn.replace("%2C",",").split(",") if a]
            stream["tlsSettings"]=tls
        if net=="tcp": stream["tcpSettings"]={"header":{"type":"none"}}
        elif net=="grpc": stream["grpcSettings"]={"serviceName":_q1(qs,"serviceName"),"multiMode":_q1(qs,"mode")=="multi"}
        elif net=="ws": stream["wsSettings"]={"path":_q1(qs,"path","/") or "/","headers":{"Host":_q1(qs,"host") or sni}}
        elif net in ("xhttp","splithttp"):
            stream["network"]="xhttp"; stream["xhttpSettings"]={"path":_q1(qs,"path","/") or "/","host":_q1(qs,"host") or sni,"mode":_q1(qs,"mode","auto") or "auto"}
        stream["sockopt"]={"domainStrategy":"ForceIPv4","tcpKeepAliveIdle":100,"tcpKeepAliveInterval":15,"tcpNoDelay":True}
        if mss and mss>0: stream["sockopt"]["tcpMaxSeg"]=mss
        return {"tag":tag,"protocol":"vless","settings":{"vnext":[{"address":host,"port":port,"users":[user]}]},"streamSettings":stream}
    except Exception as e:
        sys.stderr.write("skip vless (%s)\n"%e); return None

def vless_label(link):
    try:
        u=urllib.parse.urlparse(link); a=_vless_addr(u); hp="%s:%s"%a if a else "?"
        qs=urllib.parse.parse_qs(u.query); name=urllib.parse.unquote(u.fragment) if u.fragment else ""
        return {"name":name or hp,"host":hp,"sec":_q1(qs,"security","none") or "none","net":_q1(qs,"type","tcp") or "tcp"}
    except Exception: return {"name":"?","host":"?","sec":"?","net":"?"}

# ───────────────────────── AmneziaWG ─────────────────────────
def awg_from_vpn(blob):
    s = blob[len("vpn://"):] if blob.startswith("vpn://") else blob
    raw = base64.urlsafe_b64decode(s + "="*(-len(s)%4))
    data=None
    for skip in (4,0):
        try: data=__import__("zlib").decompress(raw[skip:]); break
        except Exception: pass
    if data is None: raise ValueError("vpn decompress")
    j=json.loads(data.decode("utf-8","replace")); name=j.get("description") or "amnezia"
    awg=None
    for c in j.get("containers",[]):
        if isinstance(c,dict) and "awg" in c: awg=c["awg"]; break
    if not awg: raise ValueError("no awg container")
    lc=awg.get("last_config"); conf=""; lj={}
    if lc:
        lj=json.loads(lc); conf=lj.get("config","")
        conf=conf.replace("$PRIMARY_DNS",str(j.get("dns1") or "1.1.1.1")).replace("$SECONDARY_DNS",str(j.get("dns2") or "1.0.0.1"))
    rec=awg_from_conf(conf,name)
    # Client's last_config wins over the text; a key missing in both comes from the container object.
    for k in AWG_ALL_KEYS:
        v=_one(lj.get(k))
        if v: rec["params"][k]=v
        elif k not in rec["params"]:
            v=_one(awg.get(k))
            if v: rec["params"][k]=v
    if not re.search(r"PersistentKeepalive\s*=", conf, re.I) and _one(lj.get("persistent_keep_alive")):
        rec["peer"]["keepalive"]=_one(lj.get("persistent_keep_alive"))
    return rec

def awg_from_conf(text,name=None):
    # Tolerate a paste whose newlines collapsed to spaces and/or lost the section
    # brackets (common over SSH/terminal): re-insert breaks before headers & keys.
    t=re.sub(r"\[?\s*(Interface|Peer)\s*\]", lambda m: "\n["+m.group(1).capitalize()+"]\n", text, flags=re.I)
    _wgk="|".join(["Address","DNS","PrivateKey","PublicKey","PresharedKey","AllowedIPs","Endpoint","PersistentKeepalive","ListenPort","MTU"]+AWG_ALL_KEYS)
    t=re.sub(r"[ \t]+(?=(?:"+_wgk+r")[ \t]*=)", "\n", t, flags=re.I)
    rec={"t":"awg","pv":7,"name":name or "amneziawg","address":"","private_key":"","mtu":"1376","params":{},
         "peer":{"public_key":"","preshared_key":"","allowed_ips":"0.0.0.0/0","endpoint":"","keepalive":"25"}}
    sec=None
    for line in t.splitlines():
        line=line.strip()
        if not line or line.startswith("#"): continue
        ll=line.lower()
        if ll.startswith("[interface]"): sec="i"; continue
        if ll.startswith("[peer]"): sec="p"; continue
        if "=" not in line: continue
        k,_,v=line.partition("="); k=k.strip(); v=v.strip(); kl=k.lower()
        if sec=="i":
            if kl=="address": rec["address"]=v.split(",")[0].strip()
            elif kl=="privatekey": rec["private_key"]=v
            elif kl=="mtu": rec["mtu"]=v
            elif kl in AWG_CANON and v: rec["params"][AWG_CANON[kl]]=v
        elif sec=="p":
            if kl=="publickey": rec["peer"]["public_key"]=v
            elif kl=="presharedkey": rec["peer"]["preshared_key"]=v
            elif kl=="allowedips": rec["peer"]["allowed_ips"]=v
            elif kl=="endpoint": rec["peer"]["endpoint"]=v
            elif kl=="persistentkeepalive": rec["peer"]["keepalive"]=v
    if not rec["private_key"] or not rec["peer"]["endpoint"]: raise ValueError("awg conf missing key/endpoint")
    if awg_problem(rec): raise ValueError("awg endpoint or address is not usable")
    return rec

def _one(v):
    # one value as a string: awg setconf is line-based, so no line breaks inside. A JSON bool is a toggle
    # (as goAmnezia: true -> "on", false -> unset; the Amnezia app reads a bool as ""); lists/objects are junk.
    if v is None or isinstance(v,(list,dict)): return ""
    if isinstance(v,bool): return "on" if v else ""
    return str(v).replace("\r","").replace("\n","").strip()

def _toggle_on(v):
    # as the Amnezia app: a toggle is on when it is non-empty and not "off"
    v=_one(v); return bool(v) and v.lower()!="off"

def awg_version(rec):
    # Line by the Amnezia app's rules: any 3.x field -> 3.1; S3/S4 or a range in H -> 2.0; I1-I5 -> 1.5; else 1.0.
    p=rec.get("params") or {}
    if any(_one(p.get(k)) for k in AWG3_VALUE_KEYS) or any(_toggle_on(p.get(k)) for k in AWG3_TOGGLE_KEYS): return "3.1"
    if _one(p.get("S3")) or _one(p.get("S4")) or any("-" in _one(p.get(h)) for h in ("H1","H2","H3","H4")): return "2.0"
    if any(_one(p.get("I%d"%i)) for i in range(1,6)): return "1.5"
    return "1.0"

def awg_lost3x(rec):
    # A 3.x key saved by Kaskad <= 2.12: that parser dropped the 3.x fields but kept the keepalive
    # range "25-35". It cannot connect; the key has to be added again.
    if rec.get("pv") or awg_version(rec)=="3.1": return False
    p=rec.get("params") or {}
    if "-" in _one((rec.get("peer") or {}).get("keepalive")): return True
    return [_one(p.get(h)) for h in ("H1","H2","H3","H4")]==["1","2","3","4"] and bool(_one(p.get("S3")) or _one(p.get("S4")))

def awg_setconf(rec):
    p=rec["params"]; line=awg_version(rec); out=["[Interface]","PrivateKey = %s"%rec["private_key"]]
    # 3.x lines only for a 3.1 key: an old core rejects the whole config even on "RandomTrailers = off".
    for k in (AWG_ALL_KEYS if line=="3.1" else AWG_IFACE_KEYS):
        v=_one(p.get(k))
        if not v: continue
        if k in AWG3_TOGGLE_KEYS and v.lower() in ("true","yes"): v="on"        # awg takes on/off/0/1 only
        elif k in AWG3_TOGGLE_KEYS and v.lower() in ("false","no"): v="off"
        out.append("%s = %s"%(k,v))
    pe=rec["peer"]; allowed=pe["allowed_ips"].split(",")[0].strip() or "0.0.0.0/0"
    ka=_one(pe["keepalive"]) or "25"
    if ka.lower()=="off": ka="0"                                         # awg 3.1 reads keepalive as a number range
    elif line!="3.1" and "-" in ka: ka=ka.split("-",1)[0].strip() or "25"   # a range needs the 3.x core
    out+=["","[Peer]","PublicKey = %s"%pe["public_key"]]
    if pe["preshared_key"]: out.append("PresharedKey = %s"%pe["preshared_key"])
    out+=["AllowedIPs = %s"%allowed,"Endpoint = %s"%pe["endpoint"],"PersistentKeepalive = %s"%ka]
    return "\n".join(out)

def awg_outbound(rec, tag, idx):
    return {"tag":tag,"protocol":"freedom","settings":{"domainStrategy":"UseIPv4"},
            "streamSettings":{"sockopt":{"mark":AWG_MARK_BASE+idx,"tcpMaxSeg":1160,"tcpNoDelay":True}}}

# ───────────────────────── extract (any input -> pool lines) ─────────────────────────
def extract(raw):
    raw=_maybe_b64_vless(raw); seen=[]; out=[]
    def add(x):
        if x and x not in seen: seen.append(x); out.append(x)
    # AWG .conf (newline- or space-collapsed; tolerate mangled brackets)
    if re.search(r'PrivateKey\s*=', raw, re.I) and re.search(r'Endpoint\s*=', raw, re.I):
        parts=re.split(r'(?=\[?\s*Interface\s*\])', raw, flags=re.I)
        parts=[x for x in parts if re.search(r'PrivateKey\s*=', x, re.I) and re.search(r'Endpoint\s*=', x, re.I)] or [raw]
        for b in parts:
            try: add("awg://"+_b64e(json.dumps(awg_from_conf(b))))
            except Exception as e: sys.stderr.write("skip conf (%s)\n"%e)
    # AWG vpn:// tokens
    for tok in re.findall(r'vpn://[A-Za-z0-9_\-+/=]+', raw):
        try: add("awg://"+_b64e(json.dumps(awg_from_vpn(tok))))
        except Exception as e: sys.stderr.write("skip vpn (%s)\n"%e)
    # VLESS lines
    for line in raw.replace("\r","\n").split("\n"):
        line=line.strip()
        if not line.startswith("vless://"): continue
        try: ok=_vless_addr(urllib.parse.urlparse(line))
        except Exception: ok=None
        if ok: add(line)
        else: sys.stderr.write("skip vless (bad address)\n")
    return out[:MAX_KEYS]

def pool_label(line):
    if line.startswith("awg://"):
        try:
            r=json.loads(_b64d(line[6:]))
            ep=r["peer"]["endpoint"]
            return {"name":r.get("name","awg"),"host":ep if _awg_addr(ep) else "?","sec":"amnezia-"+awg_version(r),"net":"udp"}
        except Exception:
            return {"name":"awg(bad)","host":"?","sec":"amnezia","net":"udp"}
    return vless_label(line)

# ───────────────────────── config (mixed xray) ─────────────────────────
def build_config(lines, socks_port, probe_url, strategy, probe_interval, max_keys, api_port, mss):
    outbounds=[]; tags=[]
    for i,line in enumerate(lines[:max_keys], start=1):
        tag="out_%d"%i
        if line.startswith("awg://"):
            try:
                rec=json.loads(_b64d(line[6:]))
                ob=None if awg_problem(rec) else awg_outbound(rec,tag,i)
                if ob is None: sys.stderr.write("skip awg (bad address)\n")
            except Exception as e: sys.stderr.write("skip awg (%s)\n"%e); ob=None
        else:
            ob=vless_outbound(line,tag,mss)
        if ob: outbounds.append(ob); tags.append(tag)
    if not outbounds: sys.stderr.write("no valid keys\n"); sys.exit(2)
    outbounds.append({"tag":"direct","protocol":"freedom","settings":{"domainStrategy":"UseIPv4"}})
    outbounds.append({"tag":"block","protocol":"blackhole"})
    api_services=["RoutingService","ObservatoryService","StatsService"] if len(tags)>1 else ["StatsService"]
    cfg={"log":{"loglevel":"warning"},"stats":{},"api":{"tag":"api","services":api_services},
         "inbounds":[{"tag":"socks-in","listen":"127.0.0.1","port":socks_port,"protocol":"socks","settings":{"udp":True,"auth":"noauth"},"sniffing":{"enabled":False}},
                     {"tag":"api-in","listen":"127.0.0.1","port":api_port,"protocol":"dokodemo-door","settings":{"address":"127.0.0.1"}}],
         "outbounds":outbounds,
         "policy":{"levels":{"0":{"connIdle":300,"handshake":4,"uplinkOnly":2,"downlinkOnly":5}},"system":{"statsInboundUplink":True,"statsInboundDownlink":True}}}
    routing={"domainStrategy":"AsIs","rules":[{"type":"field","inboundTag":["api-in"],"outboundTag":"api"}]}
    if len(tags)<=1:
        routing["rules"].append({"type":"field","inboundTag":["socks-in"],"outboundTag":tags[0]})
    else:
        cfg["observatory"]={"subjectSelector":["out_"],"probeURL":probe_url,"probeInterval":probe_interval}
        routing["balancers"]=[{"tag":"bot-bal","selector":["out_"],"strategy":{"type":strategy},"fallbackTag":tags[0]}]
        routing["rules"].append({"type":"field","inboundTag":["socks-in"],"balancerTag":"bot-bal"})
    cfg["routing"]=routing
    return cfg, len(tags)

# ───────────────────────── main ─────────────────────────
def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="cmd",required=True)
    sub.add_parser("extract"); sub.add_parser("list"); sub.add_parser("awginfo")
    al=sub.add_parser("awglist")
    ac=sub.add_parser("awgconf"); ac.add_argument("idx",type=int)
    c=sub.add_parser("config")
    c.add_argument("--socks-port",type=int,default=10808); c.add_argument("--api-port",type=int,default=10851)
    c.add_argument("--probe",default="http://cp.cloudflare.com/generate_204"); c.add_argument("--strategy",default="leastPing")
    c.add_argument("--probe-interval",default="10s"); c.add_argument("--max",type=int,default=MAX_KEYS); c.add_argument("--mss",type=int,default=0)
    a=ap.parse_args(); raw=sys.stdin.read()
    if a.cmd=="extract":
        for l in extract(raw): print(l)
    elif a.cmd=="list":
        for l in [x for x in raw.splitlines() if x.strip()]:
            # the fields go to the terminal of the menu (and the host to ping): no control bytes, one word each
            d=pool_label(l.strip()); f=[" ".join(_term(d.get(k)).split()) or "?" for k in ("name","host","sec","net")]
            print("\t".join(f))
    elif a.cmd=="awglist":
        i=0
        for l in [x for x in raw.splitlines() if x.strip()]:
            i+=1; l=l.strip()
            if l.startswith("awg://"):
                try:
                    r=json.loads(_b64d(l[6:]))
                    # no leg for a key without a usable Endpoint/Address: a leg of an older version goes (its address
                    # could take routes of the server); config has no outbound for it, so nothing is marked for it
                    if awg_problem(r): continue
                    host=_awg_addr(r["peer"]["endpoint"])[0]
                    print("%d\tkawg%d\t%d\t%d\t%s\t%s"%(i,i,AWG_MARK_BASE+i,AWG_MARK_BASE+i,_awg_ifaddr(r["address"]) or "10.8.0.2/32",host))
                except Exception: continue
    elif a.cmd=="awginfo":
        # idx<TAB>line<TAB>ok|lost3x per AWG key: core choice (3.1 needs the 3.x core) and menu warnings
        i=0
        for l in [x for x in raw.splitlines() if x.strip()]:
            i+=1; l=l.strip()
            if not l.startswith("awg://"): continue
            try:
                r=json.loads(_b64d(l[6:])); print("%d\t%s\t%s"%(i,awg_version(r),"lost3x" if awg_lost3x(r) else "ok"))
            except Exception: continue
    elif a.cmd=="awgconf":
        i=0
        for l in [x for x in raw.splitlines() if x.strip()]:
            i+=1
            if i==a.idx and l.strip().startswith("awg://"):
                try:
                    r=json.loads(_b64d(l.strip()[6:]))
                    if awg_problem(r): sys.stderr.write("awgconf bad (address)\n"); sys.exit(3)
                    print(awg_setconf(r)); return
                except Exception as e: sys.stderr.write("awgconf bad (%s)\n"%e); sys.exit(3)
        sys.exit(3)
    else:
        lines=[x.strip() for x in raw.splitlines() if x.strip()]  # config consumes normalized pool lines verbatim
        cfg,n=build_config(lines,a.socks_port,a.probe,a.strategy,a.probe_interval,a.max,a.api_port,a.mss)
        sys.stderr.write("parsed %d key(s)\n"%n); print(json.dumps(cfg,indent=2))

if __name__=="__main__": main()
PYEOF
    then rm -f "$tmp"; return 1; fi
    if cmp -s "$tmp" "$EGRESS_PARSE_PY" 2>/dev/null; then rm -f "$tmp"; return 0; fi
    chmod 600 "$tmp" 2>/dev/null; mv -f "$tmp" "$EGRESS_PARSE_PY"
}

# ── Locate an xray binary (prefer our dedicated one) ────────────────────────
egress_xray_bin() {
    [ -x "$EGRESS_XRAY" ] && { echo "$EGRESS_XRAY"; return 0; }
    local c
    for c in /usr/local/bin/xray /usr/bin/xray $(ls /usr/local/x-ui/bin/xray-* 2>/dev/null); do
        [ -x "$c" ] && { echo "$c"; return 0; }
    done
    return 1
}

# ── Ensure a known-good dedicated egress xray (independent of any panel) ─────
egress_ensure_xray() {
    [ -x "$EGRESS_XRAY" ] && return 0
    mkdir -p "$EGRESS_DIR"; chmod 700 "$EGRESS_DIR" 2>/dev/null
    local z
    case "$(uname -m)" in
        x86_64|amd64)  z="Xray-linux-64.zip" ;;
        aarch64|arm64) z="Xray-linux-arm64-v8a.zip" ;;
        armv7l)        z="Xray-linux-arm32-v7a.zip" ;;
        *)             z="Xray-linux-64.zip" ;;
    esac
    local ver
    ver=$(curl -fsSL --connect-timeout 15 --max-time 30 \
        https://api.github.com/repos/XTLS/Xray-core/releases/latest 2>/dev/null \
        | grep -m1 '"tag_name"' | cut -d'"' -f4)
    [ -z "$ver" ] && ver="$EGRESS_XVER_FALLBACK"
    echo -e "${YELLOW}[*] Загрузка отдельного xray для egress (${ver})...${NC}"
    local zf="$EGRESS_DIR/.xray.zip"
    curl -fsSL --retry 3 --connect-timeout 15 --max-time 120 \
        -o "$zf" "https://github.com/XTLS/Xray-core/releases/download/${ver}/${z}" \
        || { echo -e "${RED}Не удалось скачать xray.${NC}"; rm -f "$zf"; return 1; }
    # Extract to a temp dir, then mv — overwriting a *running* binary in place
    # fails with "Text file busy".
    rm -rf "$EGRESS_DIR/.x"; mkdir -p "$EGRESS_DIR/.x"
    if command -v unzip >/dev/null 2>&1; then
        unzip -oq "$zf" xray -d "$EGRESS_DIR/.x" 2>/dev/null
    else
        python3 -c "import zipfile; zipfile.ZipFile('$zf').extract('xray','$EGRESS_DIR/.x')" 2>/dev/null
    fi
    mv -f "$EGRESS_DIR/.x/xray" "$EGRESS_XRAY" 2>/dev/null
    chmod +x "$EGRESS_XRAY" 2>/dev/null
    rm -rf "$EGRESS_DIR/.x" "$zf"
    [ -x "$EGRESS_XRAY" ]
}

# ── Which upstream the balancer is currently using (1-based index) ──────────
egress_active_index() {
    local xray; xray=$(egress_xray_bin) || return 1
    systemctl is-active --quiet "$EGRESS_SERVICE" 2>/dev/null || return 1
    [ "$(_egr_count)" -le 1 ] && { echo 1; return 0; }   # single key -> no balancer, it is #1
    "$xray" api bi --server="127.0.0.1:${EGRESS_API_PORT}" -timeout 3 bot-bal 2>/dev/null \
        | awk '/Selecting Override:/{getline; if(match($0,/out_[0-9]+/)){s=substr($0,RSTART,RLENGTH); gsub(/[^0-9]/,"",s); if(s!=""){print s; exit}} next}
               /Selects:/{getline; if(match($0,/out_[0-9]+/)){s=substr($0,RSTART,RLENGTH); gsub(/[^0-9]/,"",s); print s; exit}}'
}

# ── BOT_PROXY lives in kaskad config (the bash bot sources it for curl) ──────
egress_set_bot_proxy() { save_config_val BOT_PROXY "$1"; }

# ── Write + enable the egress systemd service ───────────────────────────────
egress_write_service() {
    local xray; xray=$(egress_xray_bin) || { echo -e "${RED}Бинарь xray не найден.${NC}"; return 1; }
    cat > "/etc/systemd/system/${EGRESS_SERVICE}.service" <<UNIT
[Unit]
Description=Kaskad Telegram API egress (xray client, failover SOCKS5)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
Type=simple
ExecStartPre=-/usr/local/bin/gokaskad --egress-awg-up
ExecStart=${xray} run -c ${EGRESS_CFG}
ExecStopPost=-/usr/local/bin/gokaskad --egress-awg-down
Restart=always
RestartSec=5
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable "${EGRESS_SERVICE}" >/dev/null 2>&1 || true
    systemctl restart "${EGRESS_SERVICE}"
    egress_probe_install
}

# ── AmneziaWG support: per-key amneziawg-go interface + fwmark route ─────────
awg_go_bin()  { [ -x "$AWG_GO" ] && { echo "$AWG_GO"; return 0; }; [ -x /usr/local/bin/amneziawg-go ] && { echo /usr/local/bin/amneziawg-go; return 0; }; return 1; }
awg_tool_bin(){ [ -x "$AWG_TOOL" ] && { echo "$AWG_TOOL"; return 0; }; [ -x /usr/local/bin/awg ] && { echo /usr/local/bin/awg; return 0; }; return 1; }
egress_pool_has_awg() { grep -q '^awg://' "$EGRESS_UP" 2>/dev/null; }
# Ключи AmneziaWG пула: «номер<TAB>линия<TAB>ok|lost3x» (линия 1.0/1.5/2.0/3.1; lost3x — ключ 3.x,
# сохранённый Kaskad до 2.13 без полей 3.x).
egress_awg_info() { [ -s "$EGRESS_UP" ] && python3 "$EGRESS_PARSE_PY" awginfo < "$EGRESS_UP" 2>/dev/null; }
egress_pool_needs_awg3() { egress_awg_info | awk -F'\t' '$2=="3.1"{f=1} END{exit !f}'; }
# Что сказать человеку про ключи AmneziaWG, которые не подключатся (по строке на ключ; пусто — всё в порядке).
egress_awg_warnings() {
    local info idx line st core3=""
    info=$(egress_awg_info); [ -n "$info" ] || return 0
    egress_awg_is3x && core3=1
    while IFS=$'\t' read -r idx line st; do
        if [ "$st" = "lost3x" ]; then
            echo "Ключ #${idx}: AmneziaWG 3.x добавлен старой версией Kaskad без полей 3.x — не подключится. Удалите его и добавьте заново."
        elif [ "$line" = "3.1" ] && [ -z "$core3" ]; then
            echo "Ключ #${idx}: AmneziaWG 3.1 не подключится — нужно ядро 3.1. Ключ GitHub: меню 8 → 6, потом откройте этот экран снова."
        fi
    done <<< "$info"
}

# Ядро AmneziaWG (amneziawg-go + awg) лежит в bin/ репозитория kaskad-pro. Продукт
# однофайловый (/usr/local/bin/gokaskad), поэтому порядок такой: СНАЧАЛА диск —
# bin/ клона рядом со скриптом или в штатных каталогах, ПОТОМ сеть с ключом GitHub.
# Репозиторий приватный: анонимно raw.githubusercontent отдаёт 404, и это не «нет сети».
# Каталоги, где может лежать bin/ (тест задаёт список целиком через KASKAD_AWG_ROOTS, «:»).
egress_awg_roots() {
    if [ -n "${KASKAD_AWG_ROOTS+x}" ]; then printf '%s\n' "${KASKAD_AWG_ROOTS//:/$'\n'}"; return 0; fi
    local self; self=$(readlink -f "${BASH_SOURCE[0]:-$0}" 2>/dev/null); self="${self%/*}"
    printf '%s\n' "${KASKAD_SRC_DIR:-}" "${self:-}" /opt/kaskad-pro /opt/gokaskad "${HOME:-/root}/kaskad-pro" /root/kaskad-pro
}
# Архитектура для имён в bin/ (тест задаёт KASKAD_ARCH).
egress_awg_arch() { [ -n "${KASKAD_ARCH:-}" ] && { echo "$KASKAD_ARCH"; return 0; }; case "$(uname -m)" in aarch64|arm64) echo arm64;; *) echo amd64;; esac; }
# Пара бинарников по путям ($1 amneziawg-go, $2 awg) должна ЗАПУСКАТЬСЯ: бинарник чужой архитектуры
# проходит [ -x ], а падает уже в «awg setconf» — молча, с мёртвым туннелем. Запуском проверяем только на
# Linux (selftest гоняют и на macOS — там достаточно, что файлы на месте).
egress_awg_pair_runs() {
    [ -s "$1" ] && [ -s "$2" ] || return 1
    [ "$(uname -s 2>/dev/null)" = "Linux" ] || return 0
    "$1" --version >/dev/null 2>&1 && "$2" --version >/dev/null 2>&1
}
# Пара понимает AmneziaWG 3.1. Старые amneziawg-go v0.2.x и awg 1.0.x не знают полей 3.x и отвергают
# такой конфиг целиком; ядро 3.0 знает HeaderProtectionKey, но не RandomTrailers/DisableCookies — а их
# ставит каждый ключ 3.1. Версия не поможет (сборки amneziawg-go апстрима печатают «0.0.20250522»),
# поэтому смотрим, знает ли бинарник ключ 3.1: UAPI disable_cookies у amneziawg-go, DisableCookies у awg.
egress_awg_pair_is3x() { LC_ALL=C grep -aqF disable_cookies "$1" 2>/dev/null && LC_ALL=C grep -aqF DisableCookies "$2" 2>/dev/null; }
# Пара годится: запускается, а при $3 = 3 (в пуле ключ 3.x) ещё и понимает 3.1.
egress_awg_pair_ok() { egress_awg_pair_runs "$1" "$2" && { [ "${3:-}" != "3" ] || egress_awg_pair_is3x "$1" "$2"; }; }
# То же для ядра, которым пользуется продукт (своё в $AWG_DIR или системное в /usr/local/bin).
egress_awg_runs() { local go tool; go=$(awg_go_bin) && tool=$(awg_tool_bin) && egress_awg_pair_runs "$go" "$tool"; }
egress_awg_is3x() { local go tool; go=$(awg_go_bin) && tool=$(awg_tool_bin) && egress_awg_pair_is3x "$go" "$tool"; }
egress_awg_ok()   { local go tool; go=$(awg_go_bin) && tool=$(awg_tool_bin) && egress_awg_pair_ok "$go" "$tool" "${1:-}"; }
# Кандидат лежит в $1/{amneziawg-go,awg}: годится ($2 = 3 — нужен 3.1) — 0. Не годится, но запускается —
# копия в $1.fb: если ядра у продукта нет вовсе, а 3.1 не найдётся, он поднимет хотя бы ключи 1.x/2.0.
egress_awg_try_stage() {
    local d="$1" need="${2:-}" fb="$1.fb"
    chmod 700 "$d/amneziawg-go" "$d/awg" 2>/dev/null
    egress_awg_pair_ok "$d/amneziawg-go" "$d/awg" "$need" && return 0
    if [ ! -e "$fb/awg" ] && egress_awg_pair_runs "$d/amneziawg-go" "$d/awg"; then
        mkdir -p "$fb" && chmod 700 "$fb" && cp -f "$d/amneziawg-go" "$d/awg" "$fb/" 2>/dev/null
    fi
    rm -f "$d/amneziawg-go" "$d/awg" 2>/dev/null; return 1
}
# Диск: bin/ клонов по очереди, ПАРОЙ — первая пара нужной архитектуры, которая годится (устаревший клон
# раньше в списке не заслоняет свежий). Готовится в каталоге $2; ядро продукта не трогается.
egress_awg_stage_disk() {
    local arch="$1" d="$2" need="${3:-}" root
    while IFS= read -r root; do
        [ -n "$root" ] && [ -s "$root/bin/amneziawg-go-linux-$arch" ] && [ -s "$root/bin/awg-linux-$arch" ] || continue
        cp -f "$root/bin/amneziawg-go-linux-$arch" "$d/amneziawg-go" 2>/dev/null && cp -f "$root/bin/awg-linux-$arch" "$d/awg" 2>/dev/null || continue
        egress_awg_try_stage "$d" "$need" && return 0
    done < <(egress_awg_roots)
    return 1
}
# Сеть: bin/ репозитория $AWG_REPO (ветки main → test) в каталог $4. $2 — ключ GitHub ("" = анонимно), $3 = 3 — нужно ядро 3.1.
egress_awg_fetch() {
    local arch="$1" tok="$2" need="${3:-}" d="$4" ref base
    for ref in main test; do
        base="https://raw.githubusercontent.com/${AWG_REPO}/${ref}/${AWG_BIN_DIR}"
        rm -f "$d/amneziawg-go" "$d/awg" 2>/dev/null
        kaskad_gh_curl "$tok" "$base/amneziawg-go-linux-$arch" "$d/amneziawg-go" 90 || continue
        kaskad_gh_curl "$tok" "$base/awg-linux-$arch" "$d/awg" 60 || continue
        egress_awg_try_stage "$d" "$need" && return 0
    done
    rm -f "$d/amneziawg-go" "$d/awg" 2>/dev/null
    return 1
}
# Поставить пару из каталога $1 на место ядра продукта: переименование, работающим amneziawg-go это не мешает.
egress_awg_put() { mv -f "$1/amneziawg-go" "$AWG_GO" && mv -f "$1/awg" "$AWG_TOOL"; }

# $1 = 3 — в пуле ключ AmneziaWG 3.x (egress_pool_needs_awg3), и ядро должно понимать 3.1.
# Новое ядро готовится и проверяется рядом, в своём каталоге $AWG_DIR/.stage.*, и встаёт на место
# переименованием (у каждого запуска свой каталог — два меню в двух SSH-сессиях друг другу не мешают).
# Работающее ядро до этого не трогается: ни Ctrl-C на вопросе о ключе, ни обрыв SSH, ни перезапуск службы
# посреди загрузки не оставят сервер без ядра. Нет ядра 3.1 — остаётся прежнее, ключи 1.x/2.0 работают.
# EGRESS_AWG_NO_NET=1 — только диск (меню между попытками по сети).
egress_ensure_awg() {
    local need="${1:-}" st="" fb="" arch tok="" ok=0
    # Остатки оборванных запусков (старше часа: свежие могут быть чужим запуском, идущим сейчас).
    [ -d "$AWG_DIR" ] && find "$AWG_DIR" -maxdepth 1 -name '.stage.*' -mmin +60 -exec rm -rf {} + 2>/dev/null
    if awg_go_bin >/dev/null && awg_tool_bin >/dev/null; then
        if egress_awg_runs; then
            egress_awg_ok "$need" && return 0
        else
            # Лежит, но не запускается (например, awg amd64 на arm64 от прошлой версии) — заменяем.
            rm -f "$AWG_GO" "$AWG_TOOL" 2>/dev/null
        fi
    fi
    mkdir -p "$AWG_DIR"; chmod 700 "$AWG_DIR" 2>/dev/null
    st=$(mktemp -d "$AWG_DIR/.stage.XXXXXX" 2>/dev/null) || { echo -e "${RED}Не удалось создать каталог в ${AWG_DIR}.${NC}"; return 1; }
    fb="$st.fb"; arch=$(egress_awg_arch)
    echo -e "${YELLOW}[*] Подготовка ядра AmneziaWG${need:+ 3.1} (amneziawg-go + awg)...${NC}"
    # 1) Диск — без сети и без ключа.
    if egress_awg_stage_disk "$arch" "$st" "$need"; then
        egress_awg_put "$st" || { rm -rf "$st" "$fb"; echo -e "${RED}Не удалось поставить ядро в ${AWG_DIR}.${NC}"; return 1; }
        rm -rf "$st" "$fb"; echo -e "  ${GREEN}✓${NC} ядро взято из локальной копии kaskad-pro (bin/)"; return 0
    fi
    # 2) Сеть — с ключом GitHub (тем же, что для обновления: меню 8 → 6, /etc/kaskad/config,
    #    GH_TOKEN в окружении или ключ goInstaller). Анонимно приватный репозиторий отдаёт 404.
    if [ -z "${EGRESS_AWG_NO_NET:-}" ]; then
        tok=$(kaskad_gh_token)
        egress_awg_fetch "$arch" "$tok" "$need" "$st" && ok=1
        # Ключа нигде нет, а человек у терминала — спросить здесь же, как делает обновление,
        # а не отправлять в другое меню и «повторите». Только при живом stdin И stdout:
        # egress_menu вызывает rebuild с выводом в /dev/null, там приглашение никто не увидит.
        if [ "$ok" != "1" ] && [ -z "$tok" ] && [ -t 0 ] && [ -t 1 ]; then
            echo -e "${YELLOW}Не удалось скачать ядро AmneziaWG из ${AWG_REPO} (${AWG_BIN_DIR}).${NC}"
            echo -e "${WHITE}Если репозиторий закрыт форком — введите GitHub PAT, иначе Enter для отмены:${NC}"
            read -r -p "> " tok || tok=""
            if [ -n "$tok" ] && egress_awg_fetch "$arch" "$tok" "$need" "$st"; then
                ok=1; mkdir -p "$BOT_STATE_DIR"; bot_set_state "system" "UPDATE_TOKEN=$tok"; save_config_val "GITHUB_PAT" "$tok"
                echo -e "${GREEN}Ключ сохранён (меню 8 → 6) — пригодится и для обновлений.${NC}"
            fi
        fi
    fi
    if [ "$ok" = "1" ]; then
        egress_awg_put "$st" || { rm -rf "$st" "$fb"; echo -e "${RED}Не удалось поставить ядро в ${AWG_DIR}.${NC}"; return 1; }
        rm -rf "$st" "$fb"; echo -e "  ${GREEN}✓${NC} ядро загружено из GitHub (${AWG_REPO})"; return 0
    fi
    rm -rf "$st"
    # Ядра 3.1 нет. Прежнее работает — пусть работает; своего нет вовсе — ставим найденное ядро до 3.x.
    if ! egress_awg_runs && [ -e "$fb/awg" ]; then egress_awg_put "$fb"; fi
    rm -rf "$fb"
    if [ "$need" = "3" ] && egress_awg_runs; then
        echo -e "${YELLOW}Ключу AmneziaWG 3.x нужно ядро 3.1, а получить его не вышло — ключ 3.x не подключится.${NC}"
        echo -e "${YELLOW}Ключи 1.x/2.0 работают на прежнем ядре. Для 3.x: проверьте сеть и файлы bin в ${AWG_REPO}${NC}"
        echo -e "${YELLOW}с bin/ в /opt/kaskad-pro (есть старый — git -C /opt/kaskad-pro pull), затем снова откройте${NC}"
        echo -e "${YELLOW}«Прокси для API Telegram».${NC}"
        return 0
    fi
    # Честный отказ: какие файлы, откуда, что сделать.
    echo -e "${RED}Не удалось подготовить ядро AmneziaWG (amneziawg-go + awg) для ${arch}.${NC}"
    echo -e "${RED}Нужны bin/amneziawg-go-linux-${arch} и bin/awg-linux-${arch} из репозитория ${AWG_REPO} (${AWG_BIN_DIR}).${NC}"
    echo -e "${RED}Скачать не удалось. Что сделать (любое из):${NC}"
    echo -e "${RED}  1) проверить сеть; для закрытого форка — ключ GitHub (меню 8 → 6 или GH_TOKEN);${NC}"
    echo -e "${RED}  2) клон kaskad-pro с bin/ в /opt/kaskad-pro;${NC}"
    echo -e "${RED}  3) положить файлы вручную: ${AWG_GO} и ${AWG_TOOL}.${NC}"
    return 1
}
# Меню: ключу AmneziaWG 3.x нужно ядро 3.1, а стоит прежнее (при добавлении ключа не было ключа GitHub или
# сети). Пробуем снова молча: диск — при каждом входе, сеть — не чаще раза в час, пока ключ GitHub тот же,
# чтобы медленный или закрытый GitHub не вешал меню. Вышло — пересобираем egress уже на новом ядре.
egress_awg_selfheal3() {
    { egress_pool_needs_awg3 && ! egress_awg_is3x; } || return 0
    local stamp="$AWG_DIR/.core3-try" now h last="" lh="" nonet="" had=""
    now=$(date +%s); h=$(kaskad_gh_token | cksum | cut -d' ' -f1)
    [ -f "$stamp" ] && read -r last lh < "$stamp"
    if [[ "$last" =~ ^[0-9]+$ ]] && [ "$lh" = "$h" ] && [ $((now - last)) -lt 3600 ]; then
        nonet=1
    else
        mkdir -p "$AWG_DIR" && ( umask 077; printf '%s %s\n' "$now" "$h" > "$stamp" ) 2>/dev/null
    fi
    if [ -n "$nonet" ]; then
        echo -e "${YELLOW}[*] Ключу AmneziaWG 3.x нужно ядро 3.1 — ищу его на диске…${NC}"
    else
        echo -e "${YELLOW}[*] Ключу AmneziaWG 3.x нужно ядро 3.1 — пробую получить (диск, затем GitHub)…${NC}"
    fi
    egress_awg_runs && had=1
    EGRESS_AWG_NO_NET="$nonet" egress_ensure_awg 3 </dev/null >/dev/null 2>&1
    # Пересобрать: встало ядро 3.1 — или ядра не было вовсе, а теперь есть хоть какое (поднимет 1.x/2.0).
    if egress_awg_is3x || { [ -z "$had" ] && egress_awg_runs; }; then
        egress_awg_is3x && rm -f "$stamp"
        egress_rebuild >/dev/null 2>&1 || true
    fi
}

# Bring up kawg<i> for each awg key (amneziawg-go + awg setconf + fwmark route table);
# tear down kawg interfaces no longer in the pool. Idempotent.
egress_awg_sync() {
    mkdir -p "$AWG_DIR" 2>/dev/null
    local want="" idx iface mark table addr host go tool
    go=$(awg_go_bin); tool=$(awg_tool_bin)
    while IFS=$'\t' read -r idx iface mark table addr host; do
        [ -z "$iface" ] && continue
        want="$want $iface"
        # Fail closed: если нога умрёт (amneziawg-go упал, интерфейс исчез), её маршрут пропадает вместе
        # с устройством, и помеченные пакеты проваливались бы в main-таблицу — в интернет НАПРЯМУЮ, мимо
        # туннеля; observatory при этом видит «живой» outbound (проба cp.cloudflare.com проходит и напрямую)
        # и не переключает пул (проверено 2026-09-22). Запасной unreachable с большой метрикой остаётся в
        # таблице и делает мёртвую ногу честно недоступной → балансировщик уходит на другой ключ.
        # Ставим его ДО проверки ядра: без ядра (меняется на 3.x или пропало) нога закрыта, а не течёт.
        while ip rule del fwmark "$mark" table "$table" 2>/dev/null; do :; done
        ip rule add fwmark "$mark" table "$table" 2>/dev/null
        ip route replace unreachable default table "$table" metric 1000 2>/dev/null
        [ -n "$go" ] && [ -n "$tool" ] || continue
        ip link del "$iface" 2>/dev/null; pkill -f "amneziawg-go $iface" 2>/dev/null; sleep 0.4
        "$go" "$iface" >/dev/null 2>&1; sleep 0.4
        python3 "$EGRESS_PARSE_PY" awgconf "$idx" < "$EGRESS_UP" > "$AWG_DIR/.$iface.conf" 2>/dev/null; chmod 600 "$AWG_DIR/.$iface.conf"
        "$tool" setconf "$iface" "$AWG_DIR/.$iface.conf" 2>/dev/null
        ip addr add "$addr" dev "$iface" noprefixroute 2>/dev/null   # /24 ключа не забирает сеть сервера
        ip link set "$iface" up mtu 1200 2>/dev/null
        ip route replace default dev "$iface" table "$table" 2>/dev/null
    done < <(python3 "$EGRESS_PARSE_PY" awglist < "$EGRESS_UP" 2>/dev/null)
    local i n
    for i in $(ip -o link show 2>/dev/null | grep -oE 'kawg[0-9]+' | sort -u); do
        case " $want " in *" $i "*) : ;; *) n="${i#kawg}"; ip rule del fwmark "$((256+n))" table "$((256+n))" 2>/dev/null; ip route flush table "$((256+n))" 2>/dev/null; ip link del "$i" 2>/dev/null; pkill -f "amneziawg-go $i" 2>/dev/null;; esac
    done
}

egress_awg_up()   { egress_awg_sync; }
egress_awg_teardown() {
    local i n
    for i in $(ip -o link show 2>/dev/null | grep -oE 'kawg[0-9]+' | sort -u); do
        n="${i#kawg}"; while ip rule del fwmark "$((256+n))" table "$((256+n))" 2>/dev/null; do :; done; ip route flush table "$((256+n))" 2>/dev/null
        ip link del "$i" 2>/dev/null; pkill -f "amneziawg-go $i" 2>/dev/null
    done
}

# ── Collect vless:// links from input (file / URL / inline string) ──────────
egress_collect_links() {
    local input="$1" raw=""
    egress_ensure_parser
    if [ -f "$input" ]; then
        raw="$(cat "$input")"
    elif printf '%s' "$input" | grep -qiE '^https?://'; then
        echo -e "${YELLOW}[*] Скачиваю подписку…${NC}" >&2
        raw="$(curl -fsSL --max-time 25 "$input" 2>/dev/null || true)"
        [ -z "$raw" ] && { echo -e "${RED}Не удалось скачать подписку — вставьте её содержимым.${NC}" >&2; return 1; }
    else
        raw="$input"
    fi
    printf '%s' "$raw" | python3 "$EGRESS_PARSE_PY" extract
}

# ── Rebuild xray config from upstream.txt and (re)start everything ──────────
egress_rebuild() {
    [ -s "$EGRESS_UP" ] || { echo -e "${RED}Нет ключей в пуле.${NC}"; return 1; }
    egress_ensure_parser
    egress_ensure_xray || echo -e "${YELLOW}Не удалось загрузить отдельный xray — использую встроенный.${NC}"
    # xray detects config format by file EXTENSION → temp file must end in .json
    local tmpcfg="$EGRESS_DIR/.config.new.json"
    local poolfile="$EGRESS_UP" need="" w
    egress_pool_needs_awg3 && need=3
    if egress_pool_has_awg && ! egress_ensure_awg "$need"; then
        echo -e "${YELLOW}AWG-ядро недоступно — поднимаю только VLESS-ключи (AWG пропущены).${NC}"
        poolfile="$EGRESS_DIR/.pool.vlessonly"; grep -v '^awg://' "$EGRESS_UP" > "$poolfile" 2>/dev/null
        [ -s "$poolfile" ] || { echo -e "${RED}В пуле только AWG, но ядро AmneziaWG не загрузилось.${NC}"; return 1; }
    fi
    while IFS= read -r w; do echo -e "${YELLOW}⚠ ${w}${NC}"; done < <(egress_awg_warnings)
    if ! python3 "$EGRESS_PARSE_PY" config \
            --socks-port "$EGRESS_SOCKS_PORT" --api-port "$EGRESS_API_PORT" \
            --probe "$EGRESS_OBSPROBE" --strategy leastPing --probe-interval 10s \
            --max "$EGRESS_MAX" --mss "$EGRESS_MSS" < "$poolfile" \
            > "$tmpcfg" 2> "$EGRESS_DIR/parse.log"; then
        echo -e "${RED}Не удалось разобрать ключи (нет валидных vless://):${NC}"
        sed 's/^/    /' "$EGRESS_DIR/parse.log" >&2; rm -f "$tmpcfg"; return 1
    fi
    local xray; xray=$(egress_xray_bin)
    if [ -n "$xray" ] && ! "$xray" -test -c "$tmpcfg" > "$EGRESS_DIR/test.log" 2>&1; then
        echo -e "${RED}Конфиг xray невалиден:${NC}"; tail -3 "$EGRESS_DIR/test.log"; rm -f "$tmpcfg"; return 1
    fi
    mv -f "$tmpcfg" "$EGRESS_CFG"; chmod 600 "$EGRESS_CFG"
    egress_write_service || return 1
    egress_set_bot_proxy "socks5h://127.0.0.1:${EGRESS_SOCKS_PORT}"
    systemctl restart kaskad-bot 2>/dev/null || true
    rm -f "$EGRESS_DIR/.override" 2>/dev/null   # a fresh xray restart clears any bo override
    log_action "EGRESS rebuilt: $(_egr_count) keys"
    return 0
}

# ── Reachability test through the egress SOCKS5 (warms the observatory) ─────
egress_test() {
    # The observatory needs several probe cycles + xray startup to elect a live
    # outbound after a (re)build — up to ~60s cold. Keep trying within a 75s
    # window with a spinner instead of a false early failure. HTTP >=100 = OK.
    local code t0=$SECONDS spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' f=0 j
    while [ $((SECONDS - t0)) -lt 75 ]; do
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 \
            --socks5-hostname "127.0.0.1:${EGRESS_SOCKS_PORT}" "$EGRESS_TESTURL" 2>/dev/null)
        code="${code//[^0-9]/}"
        if [ -n "$code" ] && [ "$code" -ge 100 ] 2>/dev/null; then
            [ -t 2 ] && printf "\r\033[K" >&2
            echo -e "${GREEN}✓ Telegram доступен через выход (HTTP $code).${NC}"; return 0
        fi
        for j in 1 2 3 4 5 6 7; do
            [ -t 2 ] && printf "\r\033[K  \033[0;36m%s\033[0m Проверяю связь через туннель… \033[2m(%sс)\033[0m" \
                "${spin:f++%10:1}" "$((SECONDS-t0))" >&2
            sleep 0.3
        done
    done
    [ -t 2 ] && printf "\r\033[K" >&2
    echo -e "${RED}✗ Через выход Telegram недоступен (journalctl -u ${EGRESS_SERVICE}).${NC}"; return 1
}

# ── Import = REPLACE the whole pool (arg: key / list / subscription) ─────────
egress_import() {
    mkdir -p "$EGRESS_DIR"; chmod 700 "$EGRESS_DIR" 2>/dev/null
    local links; links="$(egress_collect_links "$1")" || return 1
    [ -z "$links" ] && { echo -e "${RED}Не удалось разобрать ключи.${NC}"; return 1; }
    printf '%s\n' "$links" | grep . | head -n "$EGRESS_MAX" > "$EGRESS_UP"; chmod 600 "$EGRESS_UP"
    egress_rebuild || return 1
    echo -e "${GREEN}Импортировано ключей: $(_egr_count). Резерв включён.${NC}"
    echo -e "${YELLOW}Туннель поднят, прогреваю балансировщик и проверяю связь (до ≈60с)…${NC}"
    sleep 2; egress_test || echo -e "${YELLOW}Связь пока не подтвердилась за время прогрева. Откройте панель: если идут пинг и данные — туннель работает. Иначе проверьте ключи/подписку.${NC}"
}

# ── Add one (or more) key(s) to the existing pool ───────────────────────────
egress_add_key() {
    mkdir -p "$EGRESS_DIR"; chmod 700 "$EGRESS_DIR" 2>/dev/null
    local links; links="$(egress_collect_links "$1")" || return 1
    [ -z "$links" ] && { echo -e "${RED}Не удалось разобрать ключи.${NC}"; return 1; }
    touch "$EGRESS_UP"
    local added=0 l
    while IFS= read -r l; do
        [ -z "$l" ] && continue
        grep -qxF "$l" "$EGRESS_UP" && continue
        if [ "$(grep -c . "$EGRESS_UP")" -ge "$EGRESS_MAX" ]; then echo -e "${YELLOW}Достигнут предел ${EGRESS_MAX} ключей.${NC}"; break; fi
        printf '%s\n' "$l" >> "$EGRESS_UP"; added=$((added+1))
    done <<< "$links"
    chmod 600 "$EGRESS_UP"
    [ "$added" -eq 0 ] && { echo -e "${YELLOW}Новых ключей не добавлено (дубликаты или пусто).${NC}"; return 0; }
    egress_rebuild || return 1
    echo -e "${GREEN}Добавлено ключей: $added.${NC}"; sleep 2; egress_test || true
}

# ── Remove a key by its list number ─────────────────────────────────────────
egress_remove_key() {
    local idx="$1" total
    [ -s "$EGRESS_UP" ] || { echo -e "${RED}Ключей пока нет.${NC}"; return 1; }
    total=$(grep -c . "$EGRESS_UP")
    { [[ "$idx" =~ ^[0-9]+$ ]] && [ "$idx" -ge 1 ] && [ "$idx" -le "$total" ]; } \
        || { echo -e "${RED}Неверный номер ключа.${NC}"; return 1; }
    sed -i "${idx}d" "$EGRESS_UP"
    if [ ! -s "$EGRESS_UP" ]; then echo -e "${YELLOW}Список опустел — выход отключён.${NC}"; egress_disable; return 0; fi
    egress_rebuild && echo -e "${GREEN}Ключ удалён.${NC}"
}

# ── List the pool, marking the active key ───────────────────────────────────
egress_list() {
    egress_ensure_parser
    [ -s "$EGRESS_UP" ] || { echo -e "${YELLOW}Ключей пока нет — добавьте или импортируйте.${NC}"; return 0; }
    local active; active="$(egress_active_index)"
    echo ""; echo -e "  ${BOLD}Ключи в пуле:${NC}"
    local i=0 name host sec net mark
    while IFS=$'\t' read -r name host sec net; do
        i=$((i+1))
        if [ "$i" = "$active" ]; then mark="${GREEN}★${NC}"; else mark=" "; fi
        printf "  %b %2d) %s  %-21s %s/%s\n" "$mark" "$i" "$(_egr_pad "$name" 18)" "$host" "$sec" "$net"
    done < <(python3 "$EGRESS_PARSE_PY" list < "$EGRESS_UP")
    local w; while IFS= read -r w; do echo -e "  ${YELLOW}⚠ ${w}${NC}"; done < <(egress_awg_warnings)
    echo ""
}

# ── Turn the egress off (back to direct) ────────────────────────────────────
egress_disable() {
    systemctl disable --now "${EGRESS_SERVICE}" 2>/dev/null || true
    rm -f "/etc/systemd/system/${EGRESS_SERVICE}.service"
    systemctl daemon-reload 2>/dev/null || true
    egress_set_bot_proxy ""
    egress_probe_remove
    egress_awg_teardown
    systemctl restart kaskad-bot 2>/dev/null || true
    echo -e "${GREEN}Выход отключён — бот ходит в Telegram напрямую.${NC}"
    log_action "EGRESS disabled"
}

# ── Text status (CLI) ────────────────────────────────────────────────────────
egress_status() {
    source "$KASKAD_CONF" 2>/dev/null
    local active nkeys act
    active=$(systemctl is-active "${EGRESS_SERVICE}" 2>/dev/null || echo "inactive")
    nkeys=$(_egr_count); act="$(egress_active_index)"
    echo -e "  Служба egress: ${YELLOW}${active}${NC}   Ключей: ${YELLOW}${nkeys}${NC}   SOCKS5: 127.0.0.1:${EGRESS_SOCKS_PORT}"
    [ -n "$act" ] && echo -e "  Активный ключ: ${GREEN}#${act}${NC}"
    echo -e "  BOT_PROXY: ${YELLOW}${BOT_PROXY:-нет}${NC}"
    if [ "$active" = "active" ]; then
        local ip; ip=$(curl -s --max-time 8 --socks5-hostname 127.0.0.1:${EGRESS_SOCKS_PORT} https://api.ipify.org 2>/dev/null)
        echo -e "  Внешний IP через выход: ${GREEN}${ip:-—}${NC}"
    fi
}

# ── Dashboard helpers ───────────────────────────────────────────────────────
egress_human() {
    awk -v b="${1:-0}" 'BEGIN{u[0]="B";u[1]="KB";u[2]="MB";u[3]="GB";u[4]="TB";i=0;
        while(b>=1024 && i<4){b/=1024;i++}
        if(i==0) printf "%d %s", b, u[i]; else printf "%.1f %s", b, u[i]}'
}

egress_ping_ms() {
    local r code tapp ttot t
    r=$(curl -s -o /dev/null -w '%{http_code} %{time_appconnect} %{time_total}' --max-time 2 \
        --socks5-hostname "127.0.0.1:${EGRESS_SOCKS_PORT}" "$EGRESS_TESTURL" 2>/dev/null) || true
    set -- $r; code="${1:-000}"; tapp="${2:-0}"; ttot="${3:-0}"
    t="$tapp"; awk -v x="$tapp" 'BEGIN{exit !(x>0)}' || t="$ttot"
    if awk -v x="$tapp" 'BEGIN{exit !(x>0)}' || { [ -n "$code" ] && [ "$code" != "000" ]; }; then
        awk -v t="$t" 'BEGIN{printf "%.0f", (t>0?t:0)*1000}'
    else
        echo "—"
    fi
}

egress_traffic() {
    local xray; xray=$(egress_xray_bin) || { echo "0 0"; return; }
    systemctl is-active --quiet "$EGRESS_SERVICE" 2>/dev/null || { echo "0 0"; return; }
    "$xray" api statsquery --server="127.0.0.1:${EGRESS_API_PORT}" -timeout 3 \
        -pattern "inbound>>>socks-in" 2>/dev/null | python3 -c '
import sys, json
up = dn = 0
try:
    d = json.load(sys.stdin)
    for s in d.get("stat", []):
        n = s.get("name", "")
        if n.endswith("uplink"): up = s.get("value", 0)
        elif n.endswith("downlink"): dn = s.get("value", 0)
except Exception:
    pass
print(up, dn)' 2>/dev/null || echo "0 0"
}

# Адрес ключа для ping и /dev/tcp: только IP или имя (буквы, цифры, «.», «_», «-», «:» у IPv6), не с «-»; порт — число
# 1..65535. Хост и порт приходят из чужих ссылок и подписок — разборщик их проверяет, это вторая линия.
egress_host_ok() { [[ "$1" =~ ^[A-Za-z0-9_.:]([A-Za-z0-9_.:-]*[A-Za-z0-9_.:])?$ ]] && [ "${#1}" -le 253 ]; }
egress_port_ok() { [[ "$1" =~ ^[0-9]{1,5}$ ]] && [ "$((10#$1))" -ge 1 ] && [ "$((10#$1))" -le 65535 ]; }

# Per-key latency: 5 TCP connects, ignore the first 2 (warm-up), average last 3.
# Хост и порт — аргументы ($1 $2) внутреннего bash, а не текст его команды: в тексте они выполнились бы как команда.
egress_key_ping() {
    local host="$1" port="$2" i rc t0 t1 sum=0 cnt=0 fails=0
    egress_host_ok "$host" && egress_port_ok "$port" || { echo "—"; return; }
    for i in 1 2 3 4 5; do
        t0=$(date +%s%N)
        timeout 1.5 bash -c 'exec 3<>"/dev/tcp/$1/$2"' _ "$host" "$port" 2>/dev/null; rc=$?
        t1=$(date +%s%N)
        [ "$rc" -ne 0 ] && fails=$((fails + 1))
        [ "$i" -eq 2 ] && [ "$fails" -eq 2 ] && { echo "—"; return; }
        if [ "$i" -ge 3 ]; then
            [ "$rc" -ne 0 ] && { echo "—"; return; }
            sum=$(( sum + (t1 - t0) / 1000000 )); cnt=$((cnt + 1))
        fi
    done
    [ "$cnt" -gt 0 ] && echo "$((sum / cnt))" || echo "—"
}

# Alternate-screen + cursor hide (flicker-free, htop-like)
egress_alt_enter() { tput smcup 2>/dev/null || printf '\033[?1049h'; }
egress_alt_leave() { tput rmcup 2>/dev/null || printf '\033[?1049l'; }
egress_cur_hide()  { tput civis 2>/dev/null || printf '\033[?25l'; }
egress_cur_show()  { tput cnorm 2>/dev/null || printf '\033[?25h'; }

# Manually pin / unpin which key is active (balancer override via `xray api bo`)
egress_set_active() {
    local idx="$1" xray; xray=$(egress_xray_bin) || return 1
    "$xray" api bo --server="127.0.0.1:${EGRESS_API_PORT}" -b bot-bal "out_${idx}" >/dev/null 2>&1 \
        && echo "$idx" > "${EGRESS_DIR}/.override"
}
egress_set_auto() {
    local xray; xray=$(egress_xray_bin) || return 1
    "$xray" api bo --server="127.0.0.1:${EGRESS_API_PORT}" -b bot-bal -r >/dev/null 2>&1
    rm -f "${EGRESS_DIR}/.override"
}

# ── Measure all dynamic values into globals (so arrow nav stays instant) ─────
declare -gA EGR_PING
EGR_OPING=""; EGR_UP=0; EGR_DN=0; EGR_ACTIVE=""; EGR_ST="off"; EGR_L30="—"; EGR_L5="—"; EGR_L15="—"; EGR_L60="—"; EGR_WARN=""
# Предупреждения по ключам AmneziaWG: пул меняется только через F2/F3, поэтому считаем при входе
# и после них, а не на каждом такте.
_egr_warn_refresh() { EGR_WARN=$(egress_awg_warnings); }
_egr_measure() {
    case "$(systemctl is-active "$EGRESS_SERVICE" 2>/dev/null)" in
        active) EGR_ST="on" ;;
        activating|deactivating|reloading) EGR_ST="warm" ;;
        *) EGR_ST="off" ;;
    esac
    EGR_ACTIVE=$(egress_active_index)
    [ -z "$EGR_ACTIVE" ] && [ "$(_egr_count)" -ge 1 ] && EGR_ACTIVE=1
    EGR_OPING=$(egress_ping_ms)
    read -r EGR_UP EGR_DN < <(egress_traffic)
    unset EGR_PING; declare -gA EGR_PING
    local i=0 name host sec net hp h p
    while IFS=$'\t' read -r name host sec net; do
        i=$((i+1)); hp="$host"; p="${hp##*:}"; h="${hp%:*}"
        if [[ "$sec" == amnezia* ]]; then EGR_PING[$i]=$(awg_ping_ms "$h"); else EGR_PING[$i]=$(egress_key_ping "$h" "$p"); fi
    done < <(python3 "$EGRESS_PARSE_PY" list < "$EGRESS_UP")
    EGR_L30=$(egress_loss_pct 30); EGR_L5=$(egress_loss_pct 300); EGR_L15=$(egress_loss_pct 900); EGR_L60=$(egress_loss_pct 3600)
}

# Left-pad a string to a DISPLAY width (UTF-8 aware: Cyrillic counts as 1, not 2
# bytes) so the columns line up regardless of name script. Needs a UTF-8 locale,
# which egress_menu sets.
_egr_pad() { local s="$1" w="$2" l=${#1}; [ "$l" -gt "$w" ] && { s="${1:0:$w}"; l="$w"; }; printf '%s%*s' "$s" "$((w-l))" ''; }

# ── Paint from cached values (home + overwrite; selected row highlighted) ────
_egr_paint() {
    local sel="$1" live="$2"
    printf '\033[H'
    echo -e "  ${BOLD}${GREEN}Прокси для API Telegram (бот)${NC}\033[K"
    local stcol mode
    case "$EGR_ST" in
        on)   stcol="${GREEN}● работает${NC}" ;;
        warm) stcol="${CYAN}◐ запускается…${NC}" ;;
        *)    stcol="${YELLOW}○ выключен${NC}" ;;
    esac
    if [ -s "${EGRESS_DIR}/.override" ]; then mode="${YELLOW}режим: ручной${NC}"; else mode="${DIM}режим: авто${NC}"; fi
    echo -e "  Статус: ${stcol}   Telegram API: ${EGR_OPING} ms   ↑ $(egress_human "$EGR_UP")  ↓ $(egress_human "$EGR_DN")   ${mode}\033[K"
    local lc="${GREEN}" l3n="${EGR_L30%\%}"
    if [ "$EGR_L30" = "—" ]; then lc="${DIM}"
    elif awk -v x="$l3n" 'BEGIN{exit !(x>=5)}' 2>/dev/null; then lc="${RED}"
    elif awk -v x="$l3n" 'BEGIN{exit !(x>=1)}' 2>/dev/null; then lc="${YELLOW}"; fi
    echo -e "  ${DIM}Потери трафика:${NC} ${lc}30с ${EGR_L30}${NC} · 5м ${EGR_L5} · 15м ${EGR_L15} · 60м ${EGR_L60}\033[K"
    echo -e "  ${DIM}──────────────────────────────────────────────${NC}\033[K"
    echo -e "  ${BOLD}Ключи в пуле:${NC} ${DIM}(пинг до узла)${NC}\033[K"
    local i=0 name host sec net kp mark m w
    while IFS=$'\t' read -r name host sec net; do
        i=$((i+1))
        kp="${EGR_PING[$i]:-—}"; [ "$kp" != "—" ] && kp="${kp} ms"
        if [ "$i" = "$sel" ]; then
            if [ "$i" = "$EGR_ACTIVE" ]; then m="★"; else m=" "; fi
            printf " \033[7m %s %2d) %s  %-21s %-16s %s \033[0m\033[K\n" "$m" "$i" "$(_egr_pad "$name" 18)" "$host" "$sec/$net" "$kp"
        else
            if [ "$i" = "$EGR_ACTIVE" ]; then mark="${GREEN}★${NC}"; else mark=" "; fi
            printf "  %b %2d) %s  %-21s %-16s %s\033[K\n" "$mark" "$i" "$(_egr_pad "$name" 18)" "$host" "$sec/$net" "$kp"
        fi
    done < <(python3 "$EGRESS_PARSE_PY" list < "$EGRESS_UP")
    [ -n "$EGR_WARN" ] && while IFS= read -r w; do echo -e "  ${YELLOW}⚠ ${w}${NC}\033[K"; done <<< "$EGR_WARN"
    echo -e "  ${DIM}──────────────────────────────────────────────${NC}\033[K"
    _egr_bar "$live"
    printf '\033[J'
}

# htop-style function-key action bar
_egr_bar() {
    local live="$1" p
    [ "$live" = "1" ] && p="Пауза" || p="Продолжить"
    echo -e "  \033[7m F2 \033[0m Добавить ключ  \033[7m F3 \033[0m Удалить ключ  \033[7m F6 \033[0m Выбрать руками  \033[7m F7 \033[0m Автоматический выбор  \033[7m F10 \033[0m Выход\033[K"
    echo -e "  ${DIM}Навигация: ↑/↓ · F4 ${p} · F5 Обновить${NC}\033[K"
    echo -e "  ${DIM}Ручной выбор: ↑/↓ выберите ключ и нажмите Enter (или F6), чтобы назначить активным${NC}\033[K"
}

# Read one keypress: F1-F12, UP/DOWN/LEFT/RIGHT, a literal char, ESC, __timeout__.
egress_readkey() {
    local to="$1" k k2 k3 seq c
    IFS= read -rsn1 -t "$to" k || { echo "__timeout__"; return; }
    if [ -z "$k" ]; then echo "ENTER"; return; fi
    if [ "$k" != $'\033' ]; then echo "$k"; return; fi
    IFS= read -rsn1 -t 0.06 k2 || { echo "ESC"; return; }
    case "$k2" in
        O)  IFS= read -rsn1 -t 0.06 k3
            case "$k3" in P) echo F1;; Q) echo F2;; R) echo F3;; S) echo F4;; *) echo ESC;; esac ;;
        "[") seq=""
            while IFS= read -rsn1 -t 0.06 c; do seq+="$c"; [[ "$c" =~ [A-Z~] ]] && break; done
            case "$seq" in
                A) echo UP;; B) echo DOWN;; C) echo RIGHT;; D) echo LEFT;;
                "11~"|P) echo F1;; "12~"|Q) echo F2;; "13~"|R) echo F3;; "14~"|S) echo F4;;
                "15~") echo F5;; "17~") echo F6;; "18~") echo F7;; "19~") echo F8;;
                "20~") echo F9;; "21~") echo F10;; "23~") echo F11;; "24~") echo F12;;
                *) echo ESC;; esac ;;
        *) echo ESC ;;
    esac
}

# ── Active-key REAL traffic-loss probe (kernel retrans bytes, bg timer) ─────────────
# host:port of the upstream the balancer/route is actually using.
egress_active_endpoint() {
    local idx key hp
    idx=$(egress_active_index 2>/dev/null); [ -z "$idx" ] && idx=1
    key=$(sed -n "${idx}p" "$EGRESS_UP" 2>/dev/null); [ -z "$key" ] && key=$(sed -n '1p' "$EGRESS_UP" 2>/dev/null)
    case "$key" in awg://*) return 1 ;; esac   # AWG endpoint is UDP; loss-probe (TCP retrans) does not apply
    hp="${key#*@}"; hp="${hp%%\?*}"; [ -z "$hp" ] && return 1
    echo "${hp%%:*} ${hp##*:}"
}

# One sample: ping the active node; append "epoch sent recv" to the ring log.
# If the node blocks ICMP entirely but its TCP port is reachable, record it as
# no-loss (we can't measure ICMP loss, so don't report a false 100%).
egress_probe_sample() {
    [ -s "$EGRESS_UP" ] || return 0
    # Pick what to measure by the ACTIVE key's type:
    #  VLESS -> TCP retrans to the node (dst node-ip);
    #  AWG   -> the tunnelled TCP to Telegram egresses via the kawg interface, so
    #           its source IP is the kawg address -> measure retrans on (src kawg-ip).
    local idx key filter
    idx=$(egress_active_index 2>/dev/null); [[ "$idx" =~ ^[0-9]+$ ]] || idx=1
    key=$(sed -n "${idx}p" "$EGRESS_UP" 2>/dev/null)
    if [ "${key:0:6}" = "awg://" ]; then
        local sip; sip=$(python3 "$EGRESS_PARSE_PY" awglist < "$EGRESS_UP" 2>/dev/null | awk -v i="$idx" '$1==i{split($5,a,"/");print a[1];exit}')
        [ -z "$sip" ] && return 0
        filter="src $sip"
    else
        local hp; hp="${key#*@}"; hp="${hp%%\?*}"; [ -z "$hp" ] && return 0
        filter="dst ${hp%%:*}"
    fi
    local snap="$EGRESS_DIR/.lossnap" now; now=$(date +%s)
    # Real traffic loss = retrans/sent over the interval, measured passively.
    ss -tinO "$filter" 2>/dev/null | awk -v snap="$snap" -v now="$now" -v lf="$EGRESS_LOSS" '
      BEGIN{ while((getline l < snap)>0){ split(l,a," "); ps[a[1]]=a[2]; pr[a[1]]=a[3] } close(snap) }
      {
        laddr=$4; np=split(laddr,b,":"); port=b[np]
        s=0; r=0
        if(match($0,/bytes_sent:[0-9]+/))    s=substr($0,RSTART+11,RLENGTH-11)
        if(match($0,/bytes_retrans:[0-9]+/)) r=substr($0,RSTART+14,RLENGTH-14)
        if(port ~ /^[0-9]+$/){ cs[port]=s; cr[port]=r }
      }
      END{
        ds=0; dr=0
        for(pp in cs){ if(pp in ps){ x=cs[pp]-ps[pp]; y=cr[pp]-pr[pp]; if(x>0)ds+=x; if(y>0)dr+=y } }
        out=""; for(pp in cs){ out=out pp" "cs[pp]" "cr[pp]"\n" }
        printf "%s", out > snap
        printf "%s %d %d\n", now, ds, dr >> lf
      }'
    tail -n 400 "$EGRESS_LOSS" > "$EGRESS_LOSS.tmp" 2>/dev/null && mv -f "$EGRESS_LOSS.tmp" "$EGRESS_LOSS"
}

# Loss % over the last <window> seconds → "X.X%" or "—" if no samples yet.
egress_loss_pct() {
    local win="$1" cutoff; cutoff=$(( $(date +%s) - win ))
    [ -s "$EGRESS_LOSS" ] || { echo "—"; return; }
    awk -v c="$cutoff" '$1>=c{s+=$2; r+=$3} END{ if(s>0) printf "%.1f%%", r/s*100; else printf "—" }' "$EGRESS_LOSS" 2>/dev/null
}

# Install / remove the systemd timer that samples loss every 15s.
egress_probe_install() {
    cat > "/etc/systemd/system/${EGRESS_PROBE_SVC}.service" <<U
[Unit]
Description=Kaskad egress active-key loss probe
[Service]
Type=oneshot
ExecStart=/usr/local/bin/gokaskad --egress-probe
U
    cat > "/etc/systemd/system/${EGRESS_PROBE_SVC}.timer" <<U
[Unit]
Description=Kaskad egress loss probe (every 15s)
[Timer]
OnBootSec=20
OnUnitActiveSec=10
AccuracySec=2s
[Install]
WantedBy=timers.target
U
    systemctl daemon-reload
    systemctl enable --now "${EGRESS_PROBE_SVC}.timer" >/dev/null 2>&1 || true
}
egress_probe_remove() {
    systemctl disable --now "${EGRESS_PROBE_SVC}.timer" 2>/dev/null || true
    rm -f "/etc/systemd/system/${EGRESS_PROBE_SVC}.service" "/etc/systemd/system/${EGRESS_PROBE_SVC}.timer" "$EGRESS_LOSS" "$EGRESS_DIR/.lossnap"
    systemctl daemon-reload 2>/dev/null || true
}

# ── Live htop-style dashboard: ↑/↓ select a key, F-keys act on it ────────────
# ICMP latency to an AWG endpoint host (ms) or "—"
awg_ping_ms() { local h="$1" r; egress_host_ok "$h" || { echo "—"; return; }; r=$(ping -4 -c1 -W1 "$h" 2>/dev/null | grep -oE 'time=[0-9.]+' | head -1 | cut -d= -f2); [ -n "$r" ] && printf '%.0f\n' "$r" || echo "—"; }

# Mini-instruction: which formats the import accepts and how to paste.
egress_paste_prompt() {
    echo -e "  ${BOLD}Поддерживаемые форматы импорта:${NC}"
    echo -e "    ${CYAN}vless://…${NC}          — один VLESS-ключ"
    echo -e "    ${CYAN}подписка${NC}           — ссылка http(s):// или base64-текст подписки"
    echo -e "    ${CYAN}AmneziaWG vpn://…${NC}  — строка из приложения Amnezia или goAmnezia pro (1.0–3.1)"
    echo -e "    ${CYAN}AmneziaWG .conf${NC}    — весь текст [Interface]/[Peer] (многострочно)"
    echo -e "  ${DIM}Вставьте ключ/конфиг и нажмите ${NC}${WHITE}Enter${NC}${DIM}. Многострочный .conf — просто вставьте целиком.${NC}"
}
# Read a multi-line paste from the terminal until a lone "." or EOF.
egress_read_paste() {
    # Submit on Enter. The first line blocks for input; any extra lines of a
    # multi-line paste (AmneziaWG .conf) are captured because they arrive within
    # ~0.5s — then it auto-submits. No "." / Ctrl-D needed (but "." still ends it).
    local line buf=""
    IFS= read -r line || return
    buf="$line"$'\n'
    while IFS= read -r -t 0.5 line; do [ "$line" = "." ] && break; buf+="$line"$'\n'; done
    printf '%s' "$buf"
}
egress_add_interactive() {
    egress_paste_prompt
    local s; s="$(egress_read_paste)"
    [ -n "$(printf '%s' "$s" | tr -d '[:space:]')" ] && egress_add_key "$s" || echo -e "  ${DIM}Пусто — отменено.${NC}"
}

egress_menu() {
    [ "${LC_ALL:-}" = *UTF-8* ] 2>/dev/null || { _l=$(locale -a 2>/dev/null | grep -im1 -E "^(C|en_US)\.utf-?8$"); [ -n "$_l" ] && export LC_ALL="$_l"; }
    egress_ensure_parser
    # Self-heal upgraded installs: if a pool is configured but predates the fixed
    # config (sockopt under streamSettings / keepalive interval), rebuild once.
    # The keepalive interval lives only in VLESS outbounds and tcpMaxSeg only exists with the MSS
    # clamp on: a config without VLESS outbounds (AWG keys only) or with EGRESS_MSS=0 never has
    # them, and checking them there restarted the egress on every menu open.
    if [ -s "$EGRESS_UP" ] && { [ ! -x "$EGRESS_XRAY" ] || { grep -qE '"protocol": *"vless"' "$EGRESS_CFG" 2>/dev/null && ! grep -q 'tcpKeepAliveInterval' "$EGRESS_CFG" 2>/dev/null; } || { [ "${EGRESS_MSS:-0}" -gt 0 ] 2>/dev/null && ! grep -q 'tcpMaxSeg' "$EGRESS_CFG" 2>/dev/null; } || { [ "$(_egr_count)" -le 1 ] && grep -q '"bot-bal"' "$EGRESS_CFG" 2>/dev/null; }; }; then
        echo -e "${YELLOW}[*] Обновляю egress до исправленной версии (отдельный xray + IPv4)…${NC}"
        egress_rebuild >/dev/null 2>&1 || true
    fi
    egress_awg_selfheal3
    local interval="${EGRESS_REFRESH:-2}" N s key live=1 sel=1
    egress_alt_enter; egress_cur_hide
    trap 'egress_cur_show; egress_alt_leave' RETURN

    # empty-pool screen — only "add" until there's at least one key
    while [ "$(_egr_count)" -lt 1 ]; do
        egress_cur_hide; printf '\033[H'
        echo -e "  ${BOLD}${GREEN}Прокси для API Telegram (бот)${NC}\033[K"
        echo -e "  ${DIM}Ключей пока нет — добавьте или импортируйте${NC}\033[K"; echo -e "\033[K"
        echo -e "  \033[7m F2 \033[0m Добавить ключ   \033[7m F10 \033[0m Выход\033[K"
        echo -e "  ${DIM}F-клавиши или цифры 2/0${NC}\033[K"; printf '\033[J'
        key=$(egress_readkey 3600)
        case "$key" in
            F2|2|a|A) egress_alt_leave; egress_cur_show
                      egress_add_interactive; echo -ne "  Enter — назад "; read -r; egress_alt_enter ;;
            F10|0|q|Q) return ;;
        esac
    done

    # Instant first paint: status + loss windows are cheap (local), so the menu
    # appears immediately; the slower probes (ping/traffic) fill in on the next tick.
    systemctl is-active --quiet "$EGRESS_SERVICE" 2>/dev/null && EGR_ST="on" || EGR_ST="off"
    EGR_L30=$(egress_loss_pct 30); EGR_L5=$(egress_loss_pct 300); EGR_L15=$(egress_loss_pct 900); EGR_L60=$(egress_loss_pct 3600)
    EGR_ACTIVE=$(egress_active_index); [ -z "$EGR_ACTIVE" ] && [ "$(_egr_count)" -ge 1 ] && EGR_ACTIVE=1
    [ -n "$EGR_ACTIVE" ] && sel="$EGR_ACTIVE"
    _egr_warn_refresh
    egress_cur_hide; _egr_paint "$sel" "$live"
    _egr_measure
    while true; do
        N=$(_egr_count)
        [ "${N:-0}" -lt 1 ] && return
        if [ "$sel" -gt "$N" ]; then sel="$N"; fi
        if [ "$sel" -lt 1 ]; then sel=1; fi
        egress_cur_hide
        _egr_paint "$sel" "$live"
        if [ "$live" = "1" ]; then key=$(egress_readkey "$interval"); else key=$(egress_readkey 3600); fi
        case "$key" in
            __timeout__) _egr_measure ;;
            UP|k|K)   sel=$((sel-1)); [ "$sel" -lt 1 ] && sel=1 ;;
            DOWN|j|J) sel=$((sel+1)); [ "$sel" -gt "$N" ] && sel="$N" ;;
            F2|2|a|A) egress_alt_leave; egress_cur_show
                      egress_add_interactive; echo -ne "  Enter — назад "; read -r; egress_alt_enter; _egr_warn_refresh; _egr_measure ;;
            F3|3|d|D) egress_alt_leave; egress_cur_show
                      printf "  Удалить ключ #%s? (1 — да, 2 — нет): " "$sel"; read -r s
                      if [ "$s" = "1" ]; then egress_remove_key "$sel"; echo -ne "  Enter — назад "; read -r; fi
                      egress_alt_enter; _egr_warn_refresh; _egr_measure ;;
            F6|6|ENTER) egress_set_active "$sel"; EGR_ACTIVE="$sel" ;;
            F7|7) egress_set_auto; EGR_ACTIVE=$(egress_active_index) ;;
            F4|4|p|P) [ "$live" = "1" ] && live=0 || live=1 ;;
            F5|5|r|R) _egr_warn_refresh; _egr_measure ;;
            F10|0|q|Q) return ;;
            *) : ;;
        esac
    done
}


bot_proxy_menu() {
    source "$KASKAD_CONF" 2>/dev/null
    clear
    echo -e "${CYAN}━━━ Прокси для API Telegram ━━━${NC}"
    echo -e "Бот обращается к api.telegram.org через указанный SOCKS5/HTTP-прокси."
    echo -e "Полезно на серверах с ограниченным доступом к Telegram (DPI)."
    echo -e "Текущий: ${YELLOW}${BOT_PROXY:-нет (прямой выход)}${NC}"
    echo -e "Формат: ${WHITE}socks5h://127.0.0.1:1080${NC}  (или socks5://, http://host:port)\n"
    echo -e "1) Задать  2) Очистить  3) Проверить связь  0) Назад"
    read -p "Выбор: " pc || return
    case "$pc" in
        1) echo -e "Введите прокси (Enter — отмена):"; read -p "> " px || return
           [ -n "$px" ] && save_config_val "BOT_PROXY" "$px" && echo -e "${GREEN}Сохранено. Перезапустите бота: меню 8 → 5, затем 4.${NC}" ;;
        2) save_config_val "BOT_PROXY" ""; echo -e "${GREEN}Очищено — прямой выход. Перезапустите бота.${NC}" ;;
        3) source "$KASKAD_CONF" 2>/dev/null
           local pxa=(); [ -n "${BOT_PROXY:-}" ] && pxa=(--proxy "$BOT_PROXY")
           echo -e "${YELLOW}[*] Проверяю api.telegram.org${BOT_PROXY:+ через прокси}...${NC}"
           local code; code=$(curl -s "${pxa[@]}" --max-time 15 -o /dev/null -w '%{http_code}' "https://api.telegram.org/bot${BOT_TOKEN:-0:x}/getMe" 2>/dev/null)
           if [ "$code" = "200" ]; then echo -e "${GREEN}✓ Telegram доступен, токен валиден (HTTP 200).${NC}"
           elif [ -n "$code" ] && [ "$code" != "000" ]; then echo -e "${GREEN}✓ Telegram доступен (HTTP $code; 401/404 = связь есть, проверьте токен).${NC}"
           else echo -e "${RED}✗ Нет связи с Telegram${BOT_PROXY:+ через прокси}.${NC}"; fi ;;
        0) return ;;
    esac
}

bot_menu() {
    while true; do
        clear; source "$KASKAD_CONF" 2>/dev/null
        local bs="${RED}Выкл${NC}"; [ -f "$BOT_PID_FILE" ] && kill -0 "$(cat "$BOT_PID_FILE" 2>/dev/null)" 2>/dev/null && bs="${GREEN}Вкл ($(cat "$BOT_PID_FILE"))${NC}"
        local td="нет"; [ -n "${BOT_TOKEN:-}" ] && td="***${BOT_TOKEN: -6}"
        local ut; ut=$(bot_get_state "system" "UPDATE_TOKEN" 2>/dev/null); local ud="нет"; [ -n "$ut" ] && ud="***${ut: -6}"
        echo -e "${CYAN}━━━ Telegram Bot ━━━${NC}"
        echo -e "Статус: $bs\nТокен: ${YELLOW}$td${NC}\nChat ID: ${YELLOW}${BOT_CHAT_ID:-нет}${NC}\nUpdate: ${YELLOW}$ud${NC}\nПрокси: ${YELLOW}${BOT_PROXY:-нет}${NC}\nМеню: ${YELLOW}${MENU_STYLE:-inline}${NC}\n"
        echo -e "1) Токен бота\n2) Chat ID (авто)\n3) Chat ID (вручную)\n4) ${GREEN}Запустить${NC}\n5) ${RED}Остановить${NC}\n6) GitHub PAT\n7) Прокси для API Telegram\n0) Назад"
        read -p "Выбор: " ch || return
        case $ch in
            1) echo "Токен:"; read -p "> " t; [ -n "$t" ] && save_config_val "BOT_TOKEN" "$t" && echo -e "${GREEN}OK${NC}"; read -p "Enter..." ;;
            2) [ -z "${BOT_TOKEN:-}" ] && echo -e "${RED}Токен!${NC}" && read -p "" && continue
               echo -e "${YELLOW}Отправьте боту сообщение, затем Enter.${NC}"; read -p ""
               local c; c=$(curl -s ${BOT_PROXY:+--proxy "$BOT_PROXY"} "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?limit=1&offset=-1" | jq -r '.result[0].message.chat.id // empty')
               [ -n "$c" ] && save_config_val "BOT_CHAT_ID" "$c" && echo -e "${GREEN}$c${NC}" || echo -e "${RED}Нет.${NC}"; read -p "Enter..." ;;
            3) echo "ID:"; read -p "> " c; [ -n "$c" ] && save_config_val "BOT_CHAT_ID" "$c" && echo -e "${GREEN}OK${NC}"; read -p "Enter..." ;;
            4) start_bot; read -p "Enter..." ;; 5) stop_bot; read -p "Enter..." ;;
            6) echo "PAT:"; read -p "> " u; [ -n "$u" ] && bot_set_state "system" "UPDATE_TOKEN=$u" && echo -e "${GREEN}OK${NC}"; read -p "Enter..." ;;
            7) egress_menu; read -p "Enter..." ;;
            0) return ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  PROMO & INSTRUCTIONS
# ═══════════════════════════════════════════════════════════════

show_promo() {
    clear; echo ""
    echo -e "${MAGENTA}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${MAGENTA}║ ХОСТИНГ СО СКИДКОЙ ДО -60%                                 ║${NC}"
    echo -e "${MAGENTA}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo -e "\n${CYAN}🌍 РФ И ЕВРОПА${NC}\n${WHITE} >>> https://vk.cc/ct29NQ${NC}"
    printf " ${YELLOW}%-12s${NC} : ${WHITE}%s${NC}\n" "OFF60" "60% скидка" "antenka20" "+20% (3мес)" "antenka6" "+15% (6мес)" "antenka12" "+5% (12мес)"
    echo -e "\n${CYAN}🇧🇾 БЕЛАРУСЬ${NC}\n${WHITE} >>> https://vk.cc/cUxAhj${NC}"
    printf " ${YELLOW}%-12s${NC} : ${WHITE}%s${NC}\n" "OFF60" "60% скидка"
    echo -e "\n${YELLOW}QR-код... (3с)${NC}"; for i in 3 2 1; do echo -ne "$i..."; sleep 1; done; echo ""
    echo -e "\n${WHITE}"; command -v qrencode &>/dev/null && qrencode -t ANSIUTF8 "https://vk.cc/ct29NQ" || echo "Ссылки выше."; echo -e "${NC}"
    read -p "Нажмите Enter..."
}

show_instructions() {
    local p=1 tp=7
    while true; do
        clear; echo -e "${MAGENTA}╔══════════════════════════════════════════════════════════════╗${NC}"
        # ширина рамки — 62: отступ по длине версии (2.13 → 34 пробела; 2.13.2 — на два меньше)
        printf '%b║  📚 KASKAD PRO v%s  (%s/%s)%*s║%b\n' "$MAGENTA" "$KASKAD_VERSION" "$p" "$tp" "$((38 - ${#KASKAD_VERSION} - ${#p} - ${#tp} + 2))" "" "$NC"
        echo -e "${MAGENTA}╚══════════════════════════════════════════════════════════════╝${NC}\n"
        case $p in
        1) echo -e "${CYAN}═══ ЧТО ТАКОЕ КАСКАД ═══${NC}\n\nМост: Клиент → Этот сервер → Зарубежный VPN → Интернет\nПровайдер видит только РФ IP.\n\nНужно: VPS в РФ + зарубежный VPN + IP:Порт" ;;
        2) echo -e "${CYAN}═══ AWG/VLESS/MTProto ═══${NC}\n\n1) AWG: UDP, порт 51820\n2) VLESS: TCP, порт 443\n3) MTProto: TCP, порт 8443\n\nПосле ввода IP — автоматический GeoIP + пинг-тест" ;;
        3) echo -e "${CYAN}═══ CUSTOM / ПРАВИЛА ═══${NC}\n\n4) Custom: разные порты, SSH/RDP\n5) Правила: таблица с IP каскада + GeoIP\n14) Имена серверов + примечания" ;;
        4) echo -e "${CYAN}═══ PING ═══${NC}\n\n6) Live Ping с ASCII-графиком:\n  ▓▓▓░░░░░ — зелёный (<50ms)\n  ▓▓▓▓▓▓░░ — жёлтый (50-100ms)\n  ▓▓▓▓▓▓▓▓ — красный (>100ms)\n  ████████ — TIMEOUT" ;;
        5) echo -e "${CYAN}═══ МОНИТОРИНГ ═══${NC}\n\n7) Автопроверка с алертами в Telegram\nИнтервалы: 10с / 1мин / 5мин\nУведомления: 10с / 60с / 5мин / 15мин\nСлужба авто-запуск/стоп" ;;
        6) echo -e "${CYAN}═══ TELEGRAM BOT ═══${NC}\n\n8) @BotFather → токен → Chat ID → запуск\nДва режима меню:\n  Inline — кнопки под сообщением\n  Reply — большие кнопки внизу экрана\nПереключение: кнопка в меню или /inline" ;;
        7) echo -e "${CYAN}═══ ВОЗМОЖНОСТИ БОТА ═══${NC}\n\nПравила, Ping (1x/10x/60с), Мониторинг\n💻 Система: CPU/RAM/Swap/Disk/процессы\n🏢 Хостинг: промокоды\nGeoIP + пинг-тест при добавлении\nИмена + примечания серверов" ;;
        esac
        echo -e "\n${MAGENTA}──────────────────────────────────────────────────────────────${NC}"
        [ "$p" -eq 1 ] && echo -e "  ${YELLOW}[N]${NC} Далее  ${YELLOW}[0]${NC} Выход"
        [ "$p" -eq "$tp" ] && echo -e "  ${YELLOW}[P]${NC} Назад  ${YELLOW}[0]${NC} Выход"
        (( p > 1 && p < tp )) && echo -e "  ${YELLOW}[P]${NC} Назад  ${YELLOW}[N]${NC} Далее  ${YELLOW}[0]${NC} Выход"
        read -p "  > " nav || return
        case "$nav" in [nN]) ((p<tp)) && ((p++));; [pP]) ((p>1)) && ((p--));; 0) return;; [1-7]) p="$nav";; esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  MAIN MENU
# ═══════════════════════════════════════════════════════════════

show_menu() {
    while true; do
        clear
        echo -e "${MAGENTA}******************************************************"
        echo " potap1978  ·  Kaskad PRO v${KASKAD_VERSION}"
        echo " YouTube: https://www.youtube.com/@antenkaru"
        echo -e "******************************************************${NC}"
        echo -e "${WHITE}IP: ${GREEN}${MY_IP}${NC}  ${WHITE}Iface: ${CYAN}${IFACE}${NC}"
        echo -e "------------------------------------------------------"
        echo -e " 1) ${CYAN}AmneziaWG / WireGuard${NC} (UDP)"
        echo -e " 2) ${CYAN}VLESS / XRay${NC} (TCP)"
        echo -e " 3) ${CYAN}TProxy / MTProto${NC} (TCP)"
        echo -e " 4) 🛠  ${YELLOW}Кастомное правило${NC}"
        echo -e " 5) 📋 Правила"
        echo -e " 6) 🏓 ${CYAN}Ping (live)${NC}"
        echo -e " 7) 📊 ${CYAN}Мониторинг${NC}"
        echo -e " 8) 🤖 ${CYAN}Telegram Bot${NC}"
        echo -e " 9) ${RED}Удалить правило${NC}"
        echo -e "10) ${RED}Сбросить всё${NC}"
        echo -e "11) ${YELLOW}Обновить скрипт${NC}"
        echo -e "12) ${YELLOW}PROMO${NC}"
        echo -e "13) ${MAGENTA}📚 Инструкция${NC}"
        echo -e "14) ${WHITE}Имена серверов${NC}"
        echo -e "15) ${RED}⚠  Удалить Kaskad PRO${NC}"
        echo -e " 0) Выход"
        echo -e "------------------------------------------------------"
        read -p "Выбор: " ch || exit 0
        case $ch in
            1) configure_rule "udp" "AmneziaWG";; 2) configure_rule "tcp" "VLESS";;
            3) configure_rule "tcp" "MTProto/TProxy";; 4) configure_custom_rule;;
            5) list_active_rules;; 6) ping_menu;; 7) monitoring_menu;; 8) bot_menu;;
            9) delete_single_rule;; 10) flush_rules;; 11) self_update;; 12) show_promo;;
            13) show_instructions;; 14) manage_aliases_menu;; 15) full_uninstall;; 0) exit 0;; esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  STARTUP WITH PROGRESS
# ═══════════════════════════════════════════════════════════════

run_startup() {
    local total=7 s=0

    clear; echo ""
    echo -e "${MAGENTA}╔══════════════════════════════════════════════════════════════╗${NC}"
    printf '%b║              KASKAD PRO v%s — Загрузка%*s║%b\n' "$MAGENTA" "$KASKAD_VERSION" "$((25 - ${#KASKAD_VERSION}))" "" "$NC"
    echo -e "${MAGENTA}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Проверка прав root..." "$s" "$total"
    check_root
    printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   Права root подтверждены                    \n" "$s" "$total"

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Загрузка конфигурации..." "$s" "$total"
    init_config
    printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   Конфигурация загружена                     \n" "$s" "$total"

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Установка gokaskad..." "$s" "$total"
    if [ "$(readlink -f "$0" 2>/dev/null)" != "/usr/local/bin/gokaskad" ]; then
        if [ -f "$0" ] && [ -r "$0" ]; then
            cp -f "$0" "/usr/local/bin/gokaskad"; chmod +x "/usr/local/bin/gokaskad"
        else
            echo -e "${YELLOW}[!] Установите документированной командой: curl -o install.sh ... && ./install.sh${NC}"
        fi
    fi
    printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   Команда gokaskad                            \n" "$s" "$total"

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  IP Forwarding + BBR..." "$s" "$total"
    if grep -qE '^[[:space:]]*#?[[:space:]]*net\.ipv4\.ip_forward' /etc/sysctl.conf; then
        sed -i 's/^#*\s*net\.ipv4\.ip_forward.*/net.ipv4.ip_forward=1/' /etc/sysctl.conf
    else
        echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
    fi
    grep -q "^net.core.default_qdisc=fq" /etc/sysctl.conf || echo "net.core.default_qdisc=fq" >> /etc/sysctl.conf
    grep -q "^net.ipv4.tcp_congestion_control=bbr" /etc/sysctl.conf || echo "net.ipv4.tcp_congestion_control=bbr" >> /etc/sysctl.conf
    sysctl -p > /dev/null 2>&1
    printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   IP Forwarding + BBR Turbo                   \n" "$s" "$total"

    export DEBIAN_FRONTEND=noninteractive
    local need_install=0
    for cmd in iptables jq curl qrencode; do command -v "$cmd" &>/dev/null || need_install=1; done
    dpkg -s iptables-persistent &>/dev/null 2>&1 || need_install=1
    ((s++))
    if [ "$need_install" -eq 1 ]; then
        printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Установка пакетов" "$s" "$total"
        ( while true; do printf "."; sleep 1; done ) &
        local dpid=$!
        if command -v apt-get &>/dev/null; then
            apt-get update -y > /dev/null 2>&1
            apt-get install -y iptables-persistent netfilter-persistent qrencode jq curl procps > /dev/null 2>&1
        elif command -v dnf &>/dev/null; then
            dnf install -y iptables-services jq qrencode curl procps-ng > /dev/null 2>&1
        elif command -v yum &>/dev/null; then
            yum install -y iptables-services jq qrencode curl procps-ng > /dev/null 2>&1
        else
            kill $dpid 2>/dev/null; wait $dpid 2>/dev/null
            printf "\r  ${CYAN}[%d/%d]${NC}  ${RED}✗${NC}   Пакетный менеджер не найден!                \n" "$s" "$total"
            exit 1
        fi
        kill $dpid 2>/dev/null; wait $dpid 2>/dev/null
        printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   Пакеты установлены                          \n" "$s" "$total"
    else
        printf "  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   Все зависимости на месте                    \n" "$s" "$total"
    fi

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Сетевой интерфейс..." "$s" "$total"
    detect_interface || { echo -e "\n${RED}[ERROR] Нет сети или маршрута по умолчанию.${NC}"; exit 1; }
    printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   Интерфейс: %-20s              \n" "$s" "$total" "$IFACE"

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Внешний IP..." "$s" "$total"
    get_my_ip
    printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   IP: %-25s              \n" "$s" "$total" "$MY_IP"

    echo ""
    local w=40 bar=""
    for ((i=0; i<w; i++)); do bar+="█"; done
    echo -e "  ${CYAN}[${GREEN}${bar}${CYAN}]${NC} ${GREEN}100%${NC}"
    echo ""
    echo -e "  ${GREEN}✅  Kaskad PRO v${KASKAD_VERSION} готов к работе!${NC}"
    echo ""
    sleep 2

    migrate_dnat_comments
    adopt_free_kaskad_rules
    show_promo
    show_menu
}

# ═══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════

case "${1:-}" in
    --egress-probe) egress_probe_sample ;;
    --egress-awg-up) egress_awg_up ;;
    --egress-awg-down) egress_awg_teardown ;;
    --bot-daemon) init_config; bot_daemon ;;
    --monitor-daemon) init_config; monitor_daemon ;;
    --lib) : ;;   # для тестов: source install.sh --lib — только определения, ничего не запускать
    -h|--help) echo "Usage: gokaskad [--bot-daemon|--monitor-daemon]"; exit 0 ;;
    "") run_startup ;;
    *) echo "Unknown option: $1"; echo "Usage: gokaskad [--bot-daemon|--monitor-daemon]"; exit 2 ;;
esac
