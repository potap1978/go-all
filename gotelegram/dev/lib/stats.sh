#!/bin/bash
# stats.sh — Traffic statistics module for GoTelegram v2.5.0
# Tracks proxy (telemt, порт из config.json) and site (nginx) traffic.
# Порт сайта зависит от WEB-прокси: без него сайт на 127.0.0.1:8443, с ним
# через 8443 идёт весь WEB-туннель, а сайт-decoy живёт на 127.0.0.1:8444.
# Uses iptables counters + real-time snapshots + historical CSV

# Color codes (from common.sh)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

STATS_DIR="/run/gotelegram"
HISTORY_FILE="/opt/gotelegram/stats_history.csv"
USER_HISTORY_FILE="/opt/gotelegram/user_stats_history.csv"
SNAPSHOTS_DIR="$STATS_DIR/snapshots"
CURRENT_SNAPSHOT="$STATS_DIR/stats_current.json"
CONFIG_FILE="/opt/gotelegram/config.json"
TELEMT_CONFIG_FILE="/etc/telemt/config.toml"
STATS_RETENTION_DAYS="${STATS_RETENTION_DAYS:-365}"
STATS_MINUTE_RETENTION_DAYS="${STATS_MINUTE_RETENTION_DAYS:-31}"
STATS_CLEANUP_INTERVAL="${STATS_CLEANUP_INTERVAL:-3600}"
STATS_CLEANUP_STAMP="$STATS_DIR/last_history_cleanup"
USER_STATS_COLLECT_STAMP="$STATS_DIR/last_user_stats_minute"

# Порт прокси берём из config.json: установщик позволяет выбрать нестандартный
# порт, а правило, прибитое к 443, на нём не поймает ни байта — статистика нулевая.
_stats_proxy_port() {
    local port=""
    if [[ -f "$CONFIG_FILE" ]] && command -v jq &>/dev/null; then
        port=$(jq -r '.port // empty' "$CONFIG_FILE" 2>/dev/null)
    fi
    if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
        port=443
    fi
    echo "$port"
}

# Уважаем осознанно выключенный подсчёт: без этой проверки вход в меню
# «Статистика» и каждый старт коллектора возвращают правила вопреки настройке.
stats_is_enabled() {
    local state="true"
    if [[ -f "$CONFIG_FILE" ]] && command -v jq &>/dev/null; then
        state=$(jq -r 'if has("stats_enabled") then (.stats_enabled|tostring) else "true" end' "$CONFIG_FILE" 2>/dev/null) || state="true"
    fi
    case "$state" in ""|null) state="true" ;; esac
    [[ "$state" != "false" ]]
}

# Удаляем только свои файлы: в $STATS_DIR лежат ОБЩИЕ локи admin-users.lock и
# admin-config.lock, снос каталога снимает взаимное исключение SSH/бота/панели.
_stats_purge_own_runtime() {
    rm -rf "$SNAPSHOTS_DIR" 2>/dev/null
    rm -f "$CURRENT_SNAPSHOT" "$STATS_CLEANUP_STAMP" "${STATS_CLEANUP_STAMP}.users" "$USER_STATS_COLLECT_STAMP" 2>/dev/null
}

# Initialize stats infrastructure
# Порт, на котором сейчас живёт сайт: 8444 при собранном WEB, иначе 8443.
_stats_site_port() {
    local c="${TELEMT_CONFIG:-/etc/telemt/config.toml}"
    # WEB на основном домене: сайт — это decoy на 8444. WEB на отдельном имени
    # (главный listener ждёт PROXY-заголовок от nginx stream): сайт основного
    # домена снова на 8443, а WEB-туннель — на своём порту и сайтом не считается.
    if grep -q '^transport = "web"' "$c" 2>/dev/null && ! grep -q '^proxy_protocol = true' "$c" 2>/dev/null; then
        echo 8444
    else
        echo 8443
    fi
}

# Правило учёта сайта на loopback — ровно одно и на текущем порту. Вызывается и
# при старте, и на каждом тике: включение/выключение WEB меняет порт сайта, и
# коллектор не должен ждать рестарта, чтобы перестать считать туннель сайтом.
_stats_ensure_site_rule() {
    local site_port="$1" stale
    while read -r stale; do
        [[ -n "$stale" && "$stale" != "$site_port" ]] || continue
        iptables -D GOTELEGRAM_STATS -i lo -p tcp --dport "$stale" 2>/dev/null
    done < <(iptables -S GOTELEGRAM_STATS 2>/dev/null | awk '/^-A GOTELEGRAM_STATS/ && /-i lo/ && /--dport/ {print $NF}')
    if ! iptables -C GOTELEGRAM_STATS -i lo -p tcp --dport "$site_port" 2>/dev/null; then
        iptables -A GOTELEGRAM_STATS -i lo -p tcp --dport "$site_port" 2>/dev/null
    fi
}

stats_init() {
    # Подсчёт выключен пользователем — не воссоздаём цепочку и правила.
    stats_is_enabled || return 0

    if ! command -v iptables &>/dev/null; then
        log_warning "iptables не найден: установите пакет iptables или запустите установку зависимостей"
        return 1
    fi

    # Create runtime directory
    mkdir -p "$STATS_DIR" "$SNAPSHOTS_DIR" 2>/dev/null
    chmod 755 "$STATS_DIR" "$SNAPSHOTS_DIR" 2>/dev/null

    # Create iptables chain if not exists
    if ! iptables -L GOTELEGRAM_STATS -n >/dev/null 2>&1; then
        iptables -N GOTELEGRAM_STATS 2>/dev/null
    fi

    # Add chain to INPUT if not already present
    if ! iptables -C INPUT -j GOTELEGRAM_STATS 2>/dev/null; then
        iptables -I INPUT -j GOTELEGRAM_STATS 2>/dev/null
    fi

    # Add rule for proxy traffic (порт из config.json, TCP)
    local proxy_port stale_port
    proxy_port=$(_stats_proxy_port)

    # После смены порта правило на старом порту продолжает висеть и путает разбор
    # счётчиков — снимаем чужие порты до того, как добавим текущий.
    while read -r stale_port; do
        [[ -n "$stale_port" ]] || continue
        [[ "$stale_port" == "$proxy_port" ]] && continue
        iptables -D GOTELEGRAM_STATS -p tcp --dport "$stale_port" 2>/dev/null
    done < <(iptables -S GOTELEGRAM_STATS 2>/dev/null | awk '/^-A GOTELEGRAM_STATS/ && /--dport/ && !/-i lo/ {print $NF}')

    if ! iptables -C GOTELEGRAM_STATS -p tcp --dport "$proxy_port" 2>/dev/null; then
        iptables -A GOTELEGRAM_STATS -p tcp --dport "$proxy_port" 2>/dev/null
    fi

    # Правило учёта сайта (loopback, порт зависит от WEB)
    _stats_ensure_site_rule "$(_stats_site_port)"

    # Initialize CSV header if file doesn't exist
    if [[ ! -f "$HISTORY_FILE" ]]; then
        echo "epoch,proxy_bytes,site_bytes" > "$HISTORY_FILE" 2>/dev/null
    fi
    if [[ ! -f "$USER_HISTORY_FILE" ]]; then
        echo "epoch,user,total_octets,current_connections,active_unique_ips,recent_unique_ips" > "$USER_HISTORY_FILE" 2>/dev/null
    fi

    # Write initial snapshot
    stats_collect
}

# Collect current traffic statistics from iptables
stats_collect() {
    # Сторож запасного входа по SSH — раньше проверки stats_is_enabled: он про
    # доступ к серверу, а не про статистику, и выключенный сбор не должен
    # оставлять администратора без присмотра за запасными адресами.
    type edgeips_ssh_guard >/dev/null 2>&1 && edgeips_ssh_guard >/dev/null 2>&1 || true

    # Подсчёт выключен пользователем — тик коллектора не должен его «оживлять».
    stats_is_enabled || return 0

    local proxy_bytes=0 proxy_pkts=0 site_bytes=0 site_pkts=0
    local ts=$(date +%s)
    local temp_file=$(mktemp)
    local proxy_port site_port
    proxy_port=$(_stats_proxy_port)
    site_port=$(_stats_site_port)

    if ! command -v iptables &>/dev/null; then
        mkdir -p "$STATS_DIR" 2>/dev/null
        echo "{\"ts\":$ts,\"proxy_bytes\":0,\"proxy_pkts\":0,\"site_bytes\":0,\"site_pkts\":0,\"error\":\"iptables_missing\"}" > "$CURRENT_SNAPSHOT" 2>/dev/null
        rm -f "$temp_file" 2>/dev/null
        return 1
    fi

    # Parse iptables output: format is "pkts bytes target"
    # We need to extract bytes (2nd column) for each rule
    local iptables_output=$(iptables -L GOTELEGRAM_STATS -v -n -x 2>/dev/null)

    # Extract counters for proxy port (тот же порт, что и в правиле — иначе нули)
    proxy_bytes=$(echo "$iptables_output" | grep "dpt:$proxy_port" | grep -v "lo" | awk '{print $2}' | head -1)
    proxy_pkts=$(echo "$iptables_output" | grep "dpt:$proxy_port" | grep -v "lo" | awk '{print $1}' | head -1)

    # Extract counters for the site port on loopback. Фильтр по lo обязателен:
    # установщик предлагает 8443 и как порт прокси, когда 443 занят, — без него в
    # site попали бы две строки и JSON-снимок для панели и бота стал бы битым.
    # Переключение WEB меняет порт сайта — правило подтягиваем на каждом тике
    # (дёшево: одна проверка -C); дельты защищены от сброса счётчика.
    _stats_ensure_site_rule "$site_port"
    site_bytes=$(echo "$iptables_output" | grep "dpt:$site_port" | grep " lo " | awk '{print $2}' | head -1)
    site_pkts=$(echo "$iptables_output" | grep "dpt:$site_port" | grep " lo " | awk '{print $1}' | head -1)

    # Default to 0 if not found
    proxy_bytes=${proxy_bytes:-0}
    proxy_pkts=${proxy_pkts:-0}
    site_bytes=${site_bytes:-0}
    site_pkts=${site_pkts:-0}

    # Write current snapshot as JSON
    if command -v jq &>/dev/null; then
        echo "{\"ts\":$ts,\"proxy_bytes\":$proxy_bytes,\"proxy_pkts\":$proxy_pkts,\"site_bytes\":$site_bytes,\"site_pkts\":$site_pkts}" > "$CURRENT_SNAPSHOT" 2>/dev/null
    else
        cat > "$CURRENT_SNAPSHOT" 2>/dev/null <<EOF
{"ts":$ts,"proxy_bytes":$proxy_bytes,"proxy_pkts":$proxy_pkts,"site_bytes":$site_bytes,"site_pkts":$site_pkts}
EOF
    fi

    # Save snapshot for rate calculation (one per minute)
    local minute_key
    minute_key=$(date +%Y%m%d%H%M 2>/dev/null)
    local snapshot_file="$SNAPSHOTS_DIR/snap_${minute_key}.json"
    local snapshot_is_new=1
    [[ -f "$snapshot_file" ]] && snapshot_is_new=0
    cp "$CURRENT_SNAPSHOT" "$snapshot_file" 2>/dev/null

    # $SNAPSHOTS_DIR лежит в tmpfs (/run = ОЗУ): без чистки снимки за сутки
    # съедают мегабайты, а забитый /run ломает создание lock-файлов. Чистим раз
    # в минуту — при появлении нового файла, а не каждую секунду.
    if [[ "$snapshot_is_new" == "1" ]]; then
        find "$SNAPSHOTS_DIR" -maxdepth 1 -name 'snap_*.json' -mmin +60 -delete 2>/dev/null || true
    fi

    # Append to history CSV (once per minute, check if last entry is fresh)
    # Auto-recreate the file with header if it was deleted — otherwise the
    # collector would silently stop writing history after any wipe (v2.4.1 fix).
    if [[ ! -f "$HISTORY_FILE" ]]; then
        mkdir -p "$(dirname "$HISTORY_FILE")" 2>/dev/null
        echo "epoch,proxy_bytes,site_bytes" > "$HISTORY_FILE" 2>/dev/null
    fi

    if [[ -f "$HISTORY_FILE" ]]; then
        local last_ts
        last_ts=$(grep -E '^[0-9]' "$HISTORY_FILE" 2>/dev/null | tail -1 | cut -d, -f1)
        last_ts="${last_ts:-0}"
        local current_minute=$((ts - (ts % 60)))

        if [[ "$last_ts" -eq 0 ]] || [[ $((current_minute - last_ts)) -ge 60 ]]; then
            echo "$current_minute,$proxy_bytes,$site_bytes" >> "$HISTORY_FILE" 2>/dev/null

            # Cleanup/compact history at most once per hour.
            stats_cleanup_history
        fi
    fi

    stats_collect_users "$ts"

    # Учёт по адресам сервера. Вызывается каждый тик, но снимает не чаще
    # EDGEIPS_INTERVAL — модуль отсекает лишние вызовы сам. Отдельный юнит ради
    # этого заводить незачем, а писать раз в секунду нельзя.
    type edgeips_sample >/dev/null 2>&1 && edgeips_sample >/dev/null 2>&1 || true

    rm -f "$temp_file" 2>/dev/null
}

# Print active telemt usernames from [access.users]. Usernames are restricted by
# goTelegram to A-Z/a-z/0-9/_.- so they are safe in URLs and CSV fields.
stats_active_users() {
    [[ -f "$TELEMT_CONFIG_FILE" ]] || return 0
    awk '
        /^\[access\.users\]$/ { in_users=1; next }
        in_users && /^\[/ { exit }
        in_users && /^[[:space:]]*#/ { next }
        in_users && /=/ {
            key=$1
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
            gsub(/^"|"$/, "", key)
            if (key ~ /^[A-Za-z0-9_.-]{1,48}$/) print key
        }
    ' "$TELEMT_CONFIG_FILE" 2>/dev/null
}

stats_collect_users() {
    local ts="${1:-$(date +%s)}"
    local current_minute=$((ts - (ts % 60)))

    mkdir -p "$(dirname "$USER_HISTORY_FILE")" 2>/dev/null
    if [[ ! -f "$USER_HISTORY_FILE" ]]; then
        echo "epoch,user,total_octets,current_connections,active_unique_ips,recent_unique_ips" > "$USER_HISTORY_FILE" 2>/dev/null
    fi

    command -v curl &>/dev/null || return 0
    command -v jq &>/dev/null || return 0

    if [[ -f "$USER_STATS_COLLECT_STAMP" ]] && [[ "$(cat "$USER_STATS_COLLECT_STAMP" 2>/dev/null)" == "$current_minute" ]]; then
        return 0
    fi

    local existing_users=""
    existing_users=$(awk -F, -v ts="$current_minute" '$1 == ts { print $2 }' "$USER_HISTORY_FILE" 2>/dev/null || true)

    local user payload total conns active_ips recent_ips
    while IFS= read -r user; do
        [[ -n "$user" ]] || continue
        if printf '%s\n' "$existing_users" | grep -Fxq "$user"; then
            continue
        fi

        payload=$(curl -sS --max-time 2 "http://127.0.0.1:9091/v1/users/${user}" 2>/dev/null || true)
        [[ -n "$payload" ]] || continue
        total=$(echo "$payload" | jq -r '.data.total_octets // .total_octets // 0' 2>/dev/null)
        conns=$(echo "$payload" | jq -r '.data.current_connections // .current_connections // 0' 2>/dev/null)
        active_ips=$(echo "$payload" | jq -r '.data.active_unique_ips // .active_unique_ips // 0' 2>/dev/null)
        recent_ips=$(echo "$payload" | jq -r '.data.recent_unique_ips // .recent_unique_ips // 0' 2>/dev/null)
        [[ "$total" =~ ^[0-9]+$ ]] || total=0
        [[ "$conns" =~ ^[0-9]+$ ]] || conns=0
        [[ "$active_ips" =~ ^[0-9]+$ ]] || active_ips=0
        [[ "$recent_ips" =~ ^[0-9]+$ ]] || recent_ips=0
        echo "$current_minute,$user,$total,$conns,$active_ips,$recent_ips" >> "$USER_HISTORY_FILE" 2>/dev/null
    done < <(stats_active_users)

    echo "$current_minute" > "$USER_STATS_COLLECT_STAMP" 2>/dev/null || true
    stats_cleanup_user_history
}

# Read current snapshot as JSON
stats_read_current() {
    if [[ -f "$CURRENT_SNAPSHOT" ]]; then
        cat "$CURRENT_SNAPSHOT"
    else
        echo "{}"
    fi
}

# Extract value from JSON (fallback if jq not available)
json_get() {
    local json="$1"
    local key="$2"

    if command -v jq &>/dev/null; then
        echo "$json" | jq -r ".${key}" 2>/dev/null || echo "0"
    else
        echo "$json" | grep -o "\"$key\":[^,}]*" | cut -d: -f2 | tr -d ' "' || echo "0"
    fi
}

# Convert bytes to human-readable format
format_bytes() {
    local bytes=$1

    if (( bytes < 1024 )); then
        printf "%.0f B" "$bytes"
    elif (( bytes < 1024 * 1024 )); then
        printf "%.1f KB" "$(echo "scale=1; $bytes / 1024" | bc 2>/dev/null || echo "$((bytes / 1024))")"
    elif (( bytes < 1024 * 1024 * 1024 )); then
        printf "%.1f MB" "$(echo "scale=1; $bytes / 1024 / 1024" | bc 2>/dev/null || echo "$((bytes / 1024 / 1024))")"
    elif (( bytes < 1024 * 1024 * 1024 * 1024 )); then
        printf "%.1f GB" "$(echo "scale=1; $bytes / 1024 / 1024 / 1024" | bc 2>/dev/null || echo "$((bytes / 1024 / 1024 / 1024))")"
    else
        printf "%.1f TB" "$(echo "scale=1; $bytes / 1024 / 1024 / 1024 / 1024" | bc 2>/dev/null || echo "$((bytes / 1024 / 1024 / 1024 / 1024))")"
    fi
}

# Convert bytes/sec to human-readable rate
format_rate() {
    local bytes_per_sec=$1

    if (( bytes_per_sec < 1024 )); then
        printf "%.0f B/s" "$bytes_per_sec"
    elif (( bytes_per_sec < 1024 * 1024 )); then
        printf "%.1f KB/s" "$(echo "scale=1; $bytes_per_sec / 1024" | bc 2>/dev/null || echo "$((bytes_per_sec / 1024))")"
    elif (( bytes_per_sec < 1024 * 1024 * 1024 )); then
        printf "%.1f MB/s" "$(echo "scale=1; $bytes_per_sec / 1024 / 1024" | bc 2>/dev/null || echo "$((bytes_per_sec / 1024 / 1024))")"
    else
        printf "%.1f GB/s" "$(echo "scale=1; $bytes_per_sec / 1024 / 1024 / 1024" | bc 2>/dev/null || echo "$((bytes_per_sec / 1024 / 1024 / 1024))")"
    fi
}

# Safely convert value to integer (returns 0 for empty/non-numeric)
_to_int() {
    local val="${1:-0}"
    # Strip non-numeric chars, default to 0
    val="${val//[^0-9]/}"
    echo "${val:-0}"
}

# Calculate diff safely (never negative, never crashes on empty)
_safe_diff() {
    local a=$(_to_int "$1")
    local b=$(_to_int "$2")
    local d=$((a - b))
    (( d < 0 )) && d=0
    echo "$d"
}

# Calculate traffic rates and totals from history
stats_calculate_rates() {
    local traffic_type="$1"  # "proxy" or "site"
    local col_idx=2  # proxy_bytes is column 2
    [[ "$traffic_type" == "site" ]] && col_idx=3

    local now
    now=$(date +%s)

    local periods="60 300 3600 86400 604800 2592000 31536000"
    local results=""

    for secs in $periods; do
        local target_ts=$((now - secs))
        # Считаем окно суммой приростов между соседними точками, а не «сейчас
        # минус тогда»: счётчики iptables обнуляются при ребуте и пересоздании
        # цепочки, и старая формула после каждой перезагрузки давала 0 B при
        # живом трафике. Падение значения = сброс, прирост равен новому значению.
        local diff
        diff=$(_to_int "$(awk -F, -v ts="$target_ts" -v col="$col_idx" '
            $1 !~ /^[0-9]+$/ { next }
            {
                cur = $col + 0
                if (started) { d = cur - prev; if (d < 0) d = cur } else { d = cur }
                if ($1 > ts) total += d
                prev = cur; started = 1
            }
            END { printf "%d", total + 0 }
        ' "$HISTORY_FILE" 2>/dev/null)")
        local rate=$(( secs > 0 ? diff / secs : 0 ))

        local bytes_fmt rate_fmt
        bytes_fmt=$(format_bytes "$diff")
        rate_fmt=$(format_rate "$rate")

        if [ -z "$results" ]; then
            results="${bytes_fmt}|${rate_fmt}"
        else
            results="${results}|${bytes_fmt}|${rate_fmt}"
        fi
    done

    echo "$results"
}

# Перевод с запасным текстом: t() при отсутствующем ключе печатает само имя
# ключа, а экран статистики не должен ломаться на старом языковом файле.
_stats_t() {
    local key="$1" fallback="$2" val=""
    if type t &>/dev/null; then
        val=$(t "$key" 2>/dev/null)
        [[ -n "$val" && "$val" != "$key" ]] && { echo "$val"; return 0; }
    fi
    echo "$fallback"
}

# printf добивает поля по БАЙТАМ, а кириллица в UTF-8 — два байта на символ:
# без добора по символам колонки таблицы разъезжаются на русских подписях.
_stats_pad_var() {
    local __name="$1" __width="$2" __value="${!1}" __gap
    __gap=$(( __width - ${#__value} ))
    (( __gap > 0 )) && printf -v "$__name" '%s%*s' "$__value" "$__gap" ""
    return 0
}

# Main display function for traffic statistics
show_traffic_stats() {
    # Ctrl-C убивает скрипт мимо trap RETURN вызывающего и оставляет терминал со
    # скрытым курсором (tput civis) — возвращаем курсор перед смертью процесса.
    trap 'tput cnorm 2>/dev/null; trap - INT; kill -INT $$' INT

    # Ensure stats are collected
    stats_collect

    # Get current counters
    local current_json=$(stats_read_current)
    local proxy_pkts=$(json_get "$current_json" "proxy_pkts")
    local site_pkts=$(json_get "$current_json" "site_pkts")
    # jq на пустом снимке отдаёт строку "null", и printf "%d" ниже валился
    # ошибкой bash прямо на экран — ровно после выключения сбора статистики.
    [[ "$proxy_pkts" =~ ^[0-9]+$ ]] || proxy_pkts=0
    [[ "$site_pkts" =~ ^[0-9]+$ ]] || site_pkts=0

    # Calculate rates for proxy
    local proxy_rates=$(stats_calculate_rates "proxy")
    IFS='|' read -r p1m p1mr p5m p5mr p60m p60mr p1d p1dr p7d p7dr p30d p30dr p365d p365dr <<< "$proxy_rates"

    # Calculate rates for site
    local site_rates=$(stats_calculate_rates "site")
    IFS='|' read -r s1m s1mr s5m s5mr s60m s60mr s1d s1dr s7d s7dr s30d s30dr s365d s365dr <<< "$site_rates"

    # Подписи экрана — через i18n: раньше таблица была жёстко русской и
    # игнорировала выбранный язык. Порт в заголовке тоже берём фактический.
    local proxy_port
    proxy_port=$(_stats_proxy_port)
    local hdr_proxy hdr_site c_period c_in c_rate c_pkts
    hdr_proxy=$(printf "$(_stats_t stats_hdr_proxy 'Proxy (telemt, порт %s):')" "$proxy_port")
    hdr_site=$(printf "$(_stats_t stats_hdr_site 'Сайт (nginx, порт %s):')" "$(_stats_site_port)")
    c_period=$(_stats_t stats_col_period "Период")
    c_in=$(_stats_t stats_col_in "Входящий")
    c_rate=$(_stats_t stats_col_rate "Скорость")
    c_pkts=$(_stats_t stats_packets "Пакетов")
    local l1m l5m l60m l1d l7d l30d l365d lbl
    l1m=$(_stats_t stats_period_1m "1 мин")
    l5m=$(_stats_t stats_period_5m "5 мин")
    l60m=$(_stats_t stats_period_60m "60 мин")
    l1d=$(_stats_t stats_period_1d "1 день")
    l7d=$(_stats_t stats_period_7d "7 дней")
    l30d=$(_stats_t stats_period_30d "30 дней")
    l365d=$(_stats_t stats_period_365d "365 дней")
    _stats_pad_var c_period 10
    _stats_pad_var c_in 14
    for lbl in l1m l5m l60m l1d l7d l30d l365d; do
        _stats_pad_var "$lbl" 9
    done

    # Display proxy stats
    {
        echo ""
        echo -e "${BLUE}  ${hdr_proxy}${NC}"
        echo -e "${BLUE}  ─────────────────────────────────────────${NC}"
        printf "${BLUE}  %s │ %s │ %s${NC}\n" "$c_period" "$c_in" "$c_rate"
        echo -e "${BLUE}  ─────────────────────────────────────────${NC}"
        printf "   %-9s │ %14s │ %s\n" "$l1m" "$p1m" "$p1mr"
        printf "   %-9s │ %14s │ %s\n" "$l5m" "$p5m" "$p5mr"
        printf "   %-9s │ %14s │ %s\n" "$l60m" "$p60m" "$p60mr"
        printf "   %-9s │ %14s │ %s\n" "$l1d" "$p1d" "$p1dr"
        printf "   %-9s │ %14s │ %s\n" "$l7d" "$p7d" "$p7dr"
        printf "   %-9s │ %14s │ %s\n" "$l30d" "$p30d" "$p30dr"
        printf "   %-9s │ %14s │ %s\n" "$l365d" "$p365d" "$p365dr"
        echo -e "${BLUE}  ─────────────────────────────────────────${NC}"
        printf "  %s: %d\n\n" "$c_pkts" "$proxy_pkts"

        echo -e "${BLUE}  ${hdr_site}${NC}"
        echo -e "${BLUE}  ─────────────────────────────────────────${NC}"
        printf "${BLUE}  %s │ %s │ %s${NC}\n" "$c_period" "$c_in" "$c_rate"
        echo -e "${BLUE}  ─────────────────────────────────────────${NC}"
        printf "   %-9s │ %14s │ %s\n" "$l1m" "$s1m" "$s1mr"
        printf "   %-9s │ %14s │ %s\n" "$l5m" "$s5m" "$s5mr"
        printf "   %-9s │ %14s │ %s\n" "$l60m" "$s60m" "$s60mr"
        printf "   %-9s │ %14s │ %s\n" "$l1d" "$s1d" "$s1dr"
        printf "   %-9s │ %14s │ %s\n" "$l7d" "$s7d" "$s7dr"
        printf "   %-9s │ %14s │ %s\n" "$l30d" "$s30d" "$s30dr"
        printf "   %-9s │ %14s │ %s\n" "$l365d" "$s365d" "$s365dr"
        echo -e "${BLUE}  ─────────────────────────────────────────${NC}"
        printf "  %s: %d\n" "$c_pkts" "$site_pkts"
        echo ""
    } >&2
}

_stats_positive_int() {
    local value="${1:-0}"
    [[ "$value" =~ ^[0-9]+$ ]] && [[ "$value" -gt 0 ]] && echo "$value" || echo "$2"
}

stats_should_cleanup() {
    local stamp="$1"
    local now last interval
    mkdir -p "$STATS_DIR" 2>/dev/null || true
    now=$(date +%s)
    interval=$(_stats_positive_int "$STATS_CLEANUP_INTERVAL" 3600)
    last=$(cat "$stamp" 2>/dev/null || echo 0)
    [[ "$last" =~ ^[0-9]+$ ]] || last=0
    if (( now - last < interval )); then
        return 1
    fi
    echo "$now" > "$stamp" 2>/dev/null || true
    return 0
}

stats_retention_cutoffs() {
    local now retention_days minute_days
    now=$(date +%s)
    retention_days=$(_stats_positive_int "$STATS_RETENTION_DAYS" 365)
    minute_days=$(_stats_positive_int "$STATS_MINUTE_RETENTION_DAYS" 31)
    if (( minute_days > retention_days )); then
        minute_days="$retention_days"
    fi
    echo "$((now - retention_days * 86400)) $((now - minute_days * 86400))"
}

# Keep history for at most one year. Recent points stay per-minute; older
# points are compacted to one last cumulative snapshot per hour.
stats_cleanup_history() {
    if [[ ! -f "$HISTORY_FILE" ]]; then
        return
    fi

    stats_should_cleanup "$STATS_CLEANUP_STAMP" || return 0

    local retention_cutoff minute_cutoff temp_file
    read -r retention_cutoff minute_cutoff <<< "$(stats_retention_cutoffs)"
    temp_file=$(mktemp)

    {
        head -1 "$HISTORY_FILE"
        awk -F, -v keep="$retention_cutoff" -v minute="$minute_cutoff" '
            BEGIN { OFS="," }
            NR == 1 { next }
            $1 !~ /^[0-9]+$/ { next }
            $1 < keep { next }
            $1 >= minute { print $1, $2, $3; next }
            {
                bucket = int($1 / 3600)
                compact[bucket] = $1 OFS $2 OFS $3
            }
            END {
                for (bucket in compact) print compact[bucket]
            }
        ' "$HISTORY_FILE" | sort -t, -k1,1n
    } > "$temp_file" 2>/dev/null

    mv "$temp_file" "$HISTORY_FILE" 2>/dev/null
}

stats_cleanup_user_history() {
    if [[ ! -f "$USER_HISTORY_FILE" ]]; then
        return
    fi

    stats_should_cleanup "${STATS_CLEANUP_STAMP}.users" || return 0

    local retention_cutoff minute_cutoff temp_file
    read -r retention_cutoff minute_cutoff <<< "$(stats_retention_cutoffs)"
    temp_file=$(mktemp)

    {
        head -1 "$USER_HISTORY_FILE"
        awk -F, -v keep="$retention_cutoff" -v minute="$minute_cutoff" '
            BEGIN { OFS="," }
            NR == 1 { next }
            $1 !~ /^[0-9]+$/ { next }
            $1 < keep { next }
            $1 >= minute { print $1, $2, $3, $4, $5, $6; next }
            {
                bucket = $2 SUBSEP int($1 / 3600)
                compact[bucket] = $1 OFS $2 OFS $3 OFS $4 OFS $5 OFS $6
            }
            END {
                for (bucket in compact) print compact[bucket]
            }
        ' "$USER_HISTORY_FILE" | sort -t, -k1,1n -k2,2
    } > "$temp_file" 2>/dev/null

    mv "$temp_file" "$USER_HISTORY_FILE" 2>/dev/null
}

# Toggle stats collection on/off
toggle_stats() {
    local current_state="false"

    # Read current state from config. Считаем состояние так же, как меню: без
    # ключа подсчёт идёт и показан как «вкл», значит первое нажатие обязано
    # выключать, а не печатать «ВКЛЮЧЕН» на уже включённой статистике.
    stats_is_enabled && current_state="true"

    # Toggle
    if [[ "$current_state" == "true" ]]; then
        # Disable stats
        if [[ -f "$CONFIG_FILE" ]]; then
            if command -v jq &>/dev/null; then
                local tmp_cfg
                tmp_cfg=$(mktemp "${CONFIG_FILE}.XXXXXX")
                if jq '.stats_enabled = false' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                    mv "$tmp_cfg" "$CONFIG_FILE"
                    chmod 600 "$CONFIG_FILE" 2>/dev/null || true
                else
                    rm -f "$tmp_cfg" 2>/dev/null
                fi
            fi
        fi

        # Коллектор с Restart=always пересоздал бы правила на следующем же тике и
        # после ребута — без остановки сервиса «выключено» не держится.
        # Исключение: на машине с несколькими внешними адресами тот же цикл
        # крутит сторож запасного входа по SSH (он про доступ к серверу, а не
        # про статистику). Там службу оставляем — сбор всё равно отключён
        # проверкой stats_is_enabled внутри stats_collect.
        local _keep_for_guard=0
        if type edgeips_list >/dev/null 2>&1 && [ "$(edgeips_list 2>/dev/null | grep -c .)" -gt 1 ]; then
            _keep_for_guard=1
        fi
        if command -v systemctl &>/dev/null; then
            if [ "$_keep_for_guard" = "1" ]; then
                systemctl restart gotelegram-stats.service 2>/dev/null || true
            else
                systemctl stop gotelegram-stats.service 2>/dev/null || true
                systemctl disable gotelegram-stats.service 2>/dev/null || true
            fi
        fi

        # Remove iptables rules
        iptables -D INPUT -j GOTELEGRAM_STATS 2>/dev/null
        iptables -F GOTELEGRAM_STATS 2>/dev/null
        iptables -X GOTELEGRAM_STATS 2>/dev/null

        # Clean up directories
        _stats_purge_own_runtime

        if [ "$_keep_for_guard" = "1" ]; then
            echo "Сбор статистики ОТКЛЮЧЕН (служба оставлена: она же сторожит запасной вход по SSH)" >&2
        else
            echo "Сбор статистики ОТКЛЮЧЕН" >&2
        fi
    else
        # Enable stats
        if [[ -f "$CONFIG_FILE" ]]; then
            if command -v jq &>/dev/null; then
                local tmp_cfg
                tmp_cfg=$(mktemp "${CONFIG_FILE}.XXXXXX")
                if jq '.stats_enabled = true' "$CONFIG_FILE" > "$tmp_cfg" 2>/dev/null; then
                    mv "$tmp_cfg" "$CONFIG_FILE"
                    chmod 600 "$CONFIG_FILE" 2>/dev/null || true
                else
                    rm -f "$tmp_cfg" 2>/dev/null
                fi
            fi
        fi

        # Initialize stats collection
        stats_init

        # Симметрично выключению поднимаем коллектор: иначе после включения
        # счётчики стоят на месте до ручной переустановки сервиса.
        if command -v systemctl &>/dev/null; then
            systemctl enable gotelegram-stats.service 2>/dev/null || true
            systemctl start gotelegram-stats.service 2>/dev/null || true
        fi

        echo "Сбор статистики ВКЛЮЧЕН" >&2
    fi
}

# Install systemd service for stats collection
install_stats_collector() {
    local service_file="/etc/systemd/system/gotelegram-stats.service"

    # Check if running as root
    if [[ $EUID -ne 0 ]]; then
        echo "Требуется root для установки сервиса" >&2
        return 1
    fi

    if ! command -v iptables &>/dev/null; then
        log_info "Установка iptables для подсчёта трафика..."
        install_pkg "$(apt_pkg_for_cmd iptables)" || {
            echo "Не удалось установить iptables" >&2
            return 1
        }
    fi

    # Get script directory (resolve symlinks)
    local script_dir=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
    local lib_dir=$(dirname "$script_dir")

    # Create systemd service file
    cat > "$service_file" <<'EOF'
[Unit]
Description=goTelegram Pro Traffic Stats Collector
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/bin/bash -c 'source /opt/gotelegram/lib/common.sh; source /opt/gotelegram/lib/stats.sh; [ -f /opt/gotelegram/lib/edgeips.sh ] && source /opt/gotelegram/lib/edgeips.sh; stats_init; while true; do stats_collect; sleep 1; done'
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # Флаг ставим ДО запуска: stats_init/stats_collect уважают stats_enabled и
    # при false стартующий коллектор молча ничего бы не собирал.
    # Но НЕ переписываем осознанное «выключено»: установка коллектора идёт и при
    # каждом обновлении панели, и сбор включался сам, без единого слова.
    local was_disabled=0
    if [[ -f "$CONFIG_FILE" ]] && command -v jq &>/dev/null; then
        [ "$(jq -r 'if has("stats_enabled") then (.stats_enabled|tostring) else "true" end' "$CONFIG_FILE" 2>/dev/null)" = "false" ] && was_disabled=1
        if [ "$was_disabled" = "0" ]; then
            local tmp
            tmp=$(mktemp "${CONFIG_FILE}.XXXXXX")
            if jq '.stats_enabled = true' "$CONFIG_FILE" > "$tmp" 2>/dev/null; then
                mv -f "$tmp" "$CONFIG_FILE"
                chmod 600 "$CONFIG_FILE" 2>/dev/null || true
            else
                rm -f "$tmp" 2>/dev/null
            fi
        fi
    fi

    chmod 644 "$service_file"
    systemctl daemon-reload
    if [ "$was_disabled" = "1" ]; then
        # Юнит на месте и обновлён, но выключенный сбор оставляем выключенным.
        systemctl disable gotelegram-stats.service 2>/dev/null || true
        systemctl stop gotelegram-stats.service 2>/dev/null || true
        echo "Сервис gotelegram-stats обновлён; сбор оставлен выключенным (настройка владельца)" >&2
        return 0
    fi
    systemctl enable gotelegram-stats.service
    systemctl restart gotelegram-stats.service

    echo "Сервис gotelegram-stats установлен и запущен" >&2
}

# Remove stats collector service
remove_stats_collector() {
    if [[ $EUID -ne 0 ]]; then
        echo "Требуется root для удаления сервиса" >&2
        return 1
    fi

    systemctl stop gotelegram-stats.service 2>/dev/null
    systemctl disable gotelegram-stats.service 2>/dev/null
    rm -f /etc/systemd/system/gotelegram-stats.service
    systemctl daemon-reload

    # Remove iptables rules
    iptables -D INPUT -j GOTELEGRAM_STATS 2>/dev/null
    iptables -F GOTELEGRAM_STATS 2>/dev/null
    iptables -X GOTELEGRAM_STATS 2>/dev/null

    # Clean up directories and files
    _stats_purge_own_runtime
    rm -f "$HISTORY_FILE" "$USER_HISTORY_FILE" 2>/dev/null

    echo "Сервис статистики удалён" >&2
}

# Export functions for external use
export -f stats_init stats_collect stats_collect_users stats_active_users stats_read_current stats_calculate_rates
export -f show_traffic_stats format_bytes format_rate toggle_stats
export -f stats_cleanup_history stats_cleanup_user_history stats_should_cleanup stats_retention_cutoffs install_stats_collector remove_stats_collector
export -f json_get
# Панель и бот дёргают stats_init/stats_collect в отдельном bash — им нужны и
# новые помощники, иначе функции окажутся неопределёнными в дочернем шелле.
export -f stats_is_enabled _stats_proxy_port _stats_purge_own_runtime _stats_t _stats_pad_var
