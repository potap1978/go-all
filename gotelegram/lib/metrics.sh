#!/usr/bin/env bash
# lib/metrics.sh — качество работы goTelegram: потери server→сеть Telegram по окнам
# (1м/5м/1ч/1д), «идёт ли трафик», статус WARP-вотчдога. Активный сэмплер (TCP-коннекты
# к дата-центрам Telegram) — работает и с WARP, и без, чтобы можно было решить, включать ли WARP.

MET_LOSS_LOG="/var/log/gotelegram-loss.log"
MET_LOSS_MAX=$((6*1024*1024))            # ~6 МБ (>1 суток при 30с)
MET_TIMER="gotelegram-metrics"
# Подсети дата-центров Telegram (как в WARP-модуле)
MET_TG_SUBNETS="91.108.4.0/22 91.108.8.0/22 91.108.12.0/22 91.108.16.0/22 91.108.20.0/22 91.108.56.0/22 91.105.192.0/23 149.154.160.0/20 185.76.151.0/24"
# Резервные цели: используются, только если карты ДЦ ещё нет (первые минуты
# после установки, пока telemt не забрал proxy-config у Telegram).
MET_TG_PROBES="149.154.167.51 91.108.56.107"
# Карта «ДЦ-группа → middle-proxy эндпоинт», которую telemt сам регулярно
# забирает у Telegram. Единственный достоверный источник: адреса в ней — те же,
# которыми ходит сам прокси. Захардкоженная пара выше мерила КЛИЕНТСКИЕ адреса
# ДЦ на :443 — канал, которым сервер не пользуется вообще.
MET_TG_MAP="${MET_TG_MAP:-/var/lib/telemt/cache/proxy-config-v4.txt}"
MET_PROBE_TIMEOUT="${MET_PROBE_TIMEOUT:-3}"   # сек на один коннект
MET_PROBE_PER_GROUP="${MET_PROBE_PER_GROUP:-3}"  # сколько эндпоинтов щупать в группе
# Объём трафика через Cloudflare = счётчики WARP-интерфейса (rx+tx) по дельте за тик.
MET_CF_IFACE="gtwarp"
MET_CF_LOG="/var/log/gotelegram-cf.log"
MET_CF_SNAP="/run/gotelegram-cf.snap"
MET_CF_MAX=$((6*1024*1024))

_met_dir() { echo "${GOTELEGRAM_DIR:-/opt/gotelegram}"; }

# Логи метрик создаём под umask 077 (по умолчанию было бы 644 в /var/log).
_met_log_ensure() {
    local f
    for f in "$@"; do
        [ -n "$f" ] || continue
        [ -e "$f" ] || ( umask 077; : > "$f" ) 2>/dev/null || true
        chmod 600 "$f" 2>/dev/null || true
    done
}

# «группа ip порт» построчно из карты Telegram.
_met_map_endpoints() {
    [ -s "$MET_TG_MAP" ] || return 1
    sed -nE 's/^[[:space:]]*proxy_for[[:space:]]+(-?[0-9]+)[[:space:]]+([0-9.]+):([0-9]+);.*/\1 \2 \3/p' \
        "$MET_TG_MAP" 2>/dev/null | head -200
}

# ДЦ-группы, к которым прокси прямо сейчас реально подключён. Смысл: незачем
# записывать в «потери» Сингапур, если ни один наш клиент туда не ходит — метрика
# должна отвечать на вопрос «плохо ли МОИМ пользователям», а не «как дела у Telegram».
_met_live_groups() {
    local peers eps
    # Внимание: с фильтром состояния ss НЕ печатает колонку State, и peer уезжает
    # с $5 на $4. Берём $NF — верно в обоих форматах.
    peers=$(ss -tnH state established 2>/dev/null | awk '{print $NF}' | sed 's/:[0-9]*$//' | sort -u)
    [ -n "$peers" ] || return 1
    eps=$(_met_map_endpoints) || return 1
    printf '%s\n' "$eps" | awk -v P="$peers" '
        BEGIN { n=split(P,a,"\n"); for(i=1;i<=n;i++) live[a[i]]=1 }
        ($2 in live) { print $1 }' | sort -u
}

# Активная проба. echo "ok total", где total = 100 очков на КАЖДУЮ группу, а ok —
# сколько из них группа набрала. Так одна больная группа даёт свою честную долю
# (при 12 группах — максимум 8%), а не утаскивает всю метрику в красное, как было
# раньше, когда потери считались долей неудачных попыток по двум адресам.
_met_probe() {
    local eps groups tmp g ip port cnt tried okc pct total=0 ok=0
    eps=$(_met_map_endpoints) || { _met_probe_legacy "${1:-2}"; return; }
    [ -n "$eps" ] || { _met_probe_legacy "${1:-2}"; return; }
    groups=$(_met_live_groups)
    # Прокси лежит или только поднялся — меряем все группы из карты.
    [ -n "$groups" ] || groups=$(printf '%s\n' "$eps" | awk '{print $1}' | sort -u)
    tmp=$(mktemp -d 2>/dev/null) || { _met_probe_legacy "${1:-2}"; return; }

    # Коннекты параллельно: последовательно 12 групп по 3 адреса не уложились бы
    # в 30-секундный тик таймера.
    for g in $groups; do
        cnt=0
        while read -r gg ip port; do
            [ "$gg" = "$g" ] || continue
            cnt=$((cnt+1)); [ "$cnt" -gt "$MET_PROBE_PER_GROUP" ] && break
            ( timeout "$MET_PROBE_TIMEOUT" bash -c "exec 3<>/dev/tcp/$ip/$port" 2>/dev/null \
                && echo 1 > "$tmp/$g.$cnt" || echo 0 > "$tmp/$g.$cnt" ) &
        done <<EOF_MAP
$eps
EOF_MAP
    done
    wait 2>/dev/null

    for g in $groups; do
        tried=0; okc=0
        for f in "$tmp/$g."*; do
            [ -f "$f" ] || continue
            tried=$((tried+1)); [ "$(cat "$f" 2>/dev/null)" = "1" ] && okc=$((okc+1))
        done
        [ "$tried" -gt 0 ] || continue
        pct=$(( okc * 100 / tried ))
        total=$((total+100)); ok=$((ok+pct))
    done
    rm -rf "$tmp" 2>/dev/null
    [ "$total" -gt 0 ] || { _met_probe_legacy "${1:-2}"; return; }
    echo "$ok $total"
}

# Старый способ — только как запасной, пока карты ДЦ ещё нет на диске.
_met_probe_legacy() {
    local n="${1:-2}" ip i ok=0 total=0
    for ip in $MET_TG_PROBES; do
        for i in $(seq 1 "$n"); do
            total=$((total+1))
            timeout 2 bash -c "exec 3<>/dev/tcp/$ip/443" 2>/dev/null && { ok=$((ok+1)); exec 3>&- 2>/dev/null; }
        done
    done
    echo "$ok $total"
}

# суммарные байты (rx+tx) WARP-интерфейса; пусто если интерфейса нет (WARP выкл)
_met_cf_bytes() {
    [ -d "/sys/class/net/$MET_CF_IFACE" ] || return 1
    local rx tx
    rx=$(cat "/sys/class/net/$MET_CF_IFACE/statistics/rx_bytes" 2>/dev/null || echo 0)
    tx=$(cat "/sys/class/net/$MET_CF_IFACE/statistics/tx_bytes" 2>/dev/null || echo 0)
    echo $((rx+tx))
}
# записать дельту байтов через CF за интервал (обрабатывает сброс счётчика при пересоздании iface)
_met_cf_sample() {
    local cur prev delta now
    cur=$(_met_cf_bytes) || { rm -f "$MET_CF_SNAP" 2>/dev/null; return 0; }
    now=$(date +%s); prev=$(cat "$MET_CF_SNAP" 2>/dev/null)
    echo "$cur" > "$MET_CF_SNAP" 2>/dev/null
    [ -z "$prev" ] && return 0
    if [ "$cur" -ge "$prev" ] 2>/dev/null; then delta=$((cur-prev)); else delta="$cur"; fi
    _met_log_ensure "$MET_CF_LOG"
    printf '%s %s
' "$now" "$delta" >> "$MET_CF_LOG" 2>/dev/null
    local sz; sz=$(stat -c %s "$MET_CF_LOG" 2>/dev/null || echo 0)
    [ "$sz" -gt "$MET_CF_MAX" ] 2>/dev/null && { ( umask 077; tail -c $((MET_CF_MAX/2)) "$MET_CF_LOG" > "$MET_CF_LOG.tmp" ) 2>/dev/null && mv "$MET_CF_LOG.tmp" "$MET_CF_LOG" && chmod 600 "$MET_CF_LOG" 2>/dev/null; }
}
# байты через CF за окно (сек) → человекочитаемо
metrics_cf_window() {
    local win="$1" now cutoff b
    now=$(date +%s); cutoff=$((now-win))
    if [ -f "$MET_CF_LOG" ]; then b=$(awk -v c="$cutoff" '$1>=c{s+=$2} END{printf "%d",s+0}' "$MET_CF_LOG" 2>/dev/null); else b=0; fi
    _met_bytes_human "${b:-0}"
}
_met_bytes_human() {
    local b="${1:-0}"
    if [ "$b" -ge 1073741824 ] 2>/dev/null; then awk -v b="$b" 'BEGIN{printf "%.1fГБ",b/1073741824}'
    elif [ "$b" -ge 1048576 ] 2>/dev/null; then awk -v b="$b" 'BEGIN{printf "%.1fМБ",b/1048576}'
    elif [ "$b" -ge 1024 ] 2>/dev/null; then awk -v b="$b" 'BEGIN{printf "%.0fКБ",b/1024}'
    else printf '%sБ' "${b:-0}"; fi
}

# Один тик сэмплера (для systemd-таймера): проба + запись "epoch total ok" + кап лога.
metrics_sample_tick() {
    local now r ok total
    now=$(date +%s)
    r=$(_met_probe 2); ok=${r% *}; total=${r#* }
    _met_log_ensure "$MET_LOSS_LOG"
    printf '%s %s %s\n' "$now" "$total" "$ok" >> "$MET_LOSS_LOG" 2>/dev/null
    _met_log_cap
    _met_cf_sample
}

_met_log_cap() {
    [ -f "$MET_LOSS_LOG" ] || return 0
    local sz; sz=$(stat -c %s "$MET_LOSS_LOG" 2>/dev/null || echo 0)
    if [ "$sz" -gt "$MET_LOSS_MAX" ] 2>/dev/null; then
        ( umask 077; tail -c $((MET_LOSS_MAX/2)) "$MET_LOSS_LOG" > "$MET_LOSS_LOG.tmp" ) 2>/dev/null \
            && mv "$MET_LOSS_LOG.tmp" "$MET_LOSS_LOG" && chmod 600 "$MET_LOSS_LOG" 2>/dev/null
    fi
}

# Потери % за окно (сек). echo целое % или "-" если нет данных.
metrics_loss_window() {
    local win="$1" now cutoff
    now=$(date +%s); cutoff=$((now-win))
    [ -f "$MET_LOSS_LOG" ] || { echo "-"; return; }
    awk -v c="$cutoff" '$1>=c && $2>0 {t+=$2; o+=$3} END{ if(t>0) printf "%d",(t-o)*100/t; else printf "-" }' "$MET_LOSS_LOG" 2>/dev/null
}

# Число установленных соединений телемт→дата-центры Telegram (апстрим-трафик).
metrics_tg_established() {
    local net f="" c=0
    for net in $MET_TG_SUBNETS; do f="${f:+$f or }dst $net"; done
    c=$(ss -tnH state established "( $f )" 2>/dev/null | wc -l)
    echo "${c:-0}"
}

# Идёт ли трафик: есть апстрим к Telegram ИЛИ клиенты на :443. echo 1/0.
metrics_traffic_active() {
    local up cl port
    up=$(metrics_tg_established)
    port=$(config_get port 2>/dev/null || echo 443)
    cl=$(ss -tnH state established "( sport = :$port )" 2>/dev/null | wc -l)
    { [ "${up:-0}" -gt 0 ] 2>/dev/null || [ "${cl:-0}" -gt 0 ] 2>/dev/null; } && echo 1 || echo 0
}

# Максимальные потери по всем окнам (для порога 10%). echo целое (0 если нет данных).
metrics_loss_max() {
    local w v max=0
    for w in 60 300 3600 86400; do
        v=$(metrics_loss_window "$w"); [ "$v" = "-" ] && continue
        [ "$v" -gt "$max" ] 2>/dev/null && max="$v"
    done
    echo "$max"
}

# Статус WARP-вотчдога: "on|off|none" + возраст последней проверки (сек) через ':'.
# echo "state:age_sec"  (age = -1 если неизвестно)
warp_watch_status() {
    local st="none" age="-1" ts
    if systemctl list-unit-files 2>/dev/null | grep -q '^gotelegram-warp-watch.timer'; then
        if systemctl is-active --quiet gotelegram-warp-watch.timer 2>/dev/null; then st="on"; else st="off"; fi
        # последняя проверка: mtime fail-файла нет → берём последний запуск сервиса
        ts=$(systemctl show gotelegram-warp-watch.service -p ExecMainExitTimestamp --value 2>/dev/null)
        [ -z "$ts" ] && ts=$(systemctl show gotelegram-warp-watch.service -p ActiveEnterTimestamp --value 2>/dev/null)
        if [ -n "$ts" ] && [ "$ts" != "n/a" ]; then
            local ep; ep=$(date -d "$ts" +%s 2>/dev/null); [ -n "$ep" ] && age=$(( $(date +%s) - ep ))
        fi
    fi
    echo "${st}:${age}"
}

# ── systemd таймер сэмплера (каждые 30с) ─────────────────────────────────────
metrics_install_timer() {
    local gd; gd="$(_met_dir)"
    command -v systemctl >/dev/null 2>&1 || return 0
    cat > "/etc/systemd/system/${MET_TIMER}.service" <<EOF
[Unit]
Description=goTelegram quality sampler (loss to Telegram)
[Service]
Type=oneshot
ExecStart=/bin/bash -c 'source $gd/lib/common.sh 2>/dev/null; source $gd/lib/metrics.sh; metrics_sample_tick'
EOF
    cat > "/etc/systemd/system/${MET_TIMER}.timer" <<EOF
[Unit]
Description=goTelegram quality sampler каждые 30с
[Timer]
OnBootSec=30
OnUnitActiveSec=30
AccuracySec=5
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable --now "${MET_TIMER}.timer" >/dev/null 2>&1 || true
}
metrics_remove_timer() {
    systemctl disable --now "${MET_TIMER}.timer" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/${MET_TIMER}.timer" "/etc/systemd/system/${MET_TIMER}.service" 2>/dev/null
    systemctl daemon-reload 2>/dev/null || true
}

# JSON качества (для бота/вебки при желании)
metrics_quality_json() {
    local w1 w5 wh wd tr wm ws
    w1=$(metrics_loss_window 60); w5=$(metrics_loss_window 300); wh=$(metrics_loss_window 3600); wd=$(metrics_loss_window 86400)
    tr=$(metrics_traffic_active); wm=$(metrics_loss_max); ws=$(warp_watch_status)
    local c1 c5 ch cd
    c1=$(metrics_cf_window 60); c5=$(metrics_cf_window 300); ch=$(metrics_cf_window 3600); cd=$(metrics_cf_window 86400)
    printf '{"status":"ok","traffic":%s,"loss_1m":"%s","loss_5m":"%s","loss_1h":"%s","loss_1d":"%s","loss_max":%s,"watchdog":"%s","cf_1m":"%s","cf_5m":"%s","cf_1h":"%s","cf_1d":"%s"}\n' \
        "$tr" "$w1" "$w5" "$wh" "$wd" "${wm:-0}" "$ws" "$c1" "$c5" "$ch" "$cd"
}

# ── рендер блока «Качество» для главного меню (цвета из common.sh) ────────────
_met_color() {
    local v="$1"
    [ "$v" = "-" ] && { printf '%s—%s' "${DIM}" "${NC}"; return; }
    local c="${GREEN}"; [ "$v" -ge 5 ] 2>/dev/null && c="${YELLOW}"; [ "$v" -ge 10 ] 2>/dev/null && c="${RED}"
    printf '%s%s%%%s' "$c" "$v" "${NC}"
}
metrics_menu_block() {
    local w1 w5 wh wd tr wm ws wstate wage warp_on
    w1=$(metrics_loss_window 60); w5=$(metrics_loss_window 300); wh=$(metrics_loss_window 3600); wd=$(metrics_loss_window 86400)
    tr=$(metrics_traffic_active); wm=$(metrics_loss_max)
    ws=$(warp_watch_status); wstate="${ws%:*}"; wage="${ws#*:}"
    warp_on="off"; [ "$(config_get warp_egress 2>/dev/null)" = "on" ] && warp_on="on"
    echo -e "  ${WHITE}$(t q_loss)${NC}  1м $(_met_color "$w1") · 5м $(_met_color "$w5") · 1ч $(_met_color "$wh") · 1д $(_met_color "$wd")"
    local trs wps wds
    [ "$tr" = "1" ] && trs="${GREEN}● $(t q_traffic_on)${NC}" || trs="${YELLOW}○ $(t q_traffic_off)${NC}"
    [ "$warp_on" = "on" ] && wps="${GREEN}$(t q_warp_on)${NC}" || wps="${DIM}$(t q_warp_off)${NC}"
    case "$wstate" in
        on)  if [ "$wage" -ge 0 ] 2>/dev/null; then wds="${GREEN}● $(t q_wd_on)${NC} ${DIM}($(tf q_wd_ago "$wage"))${NC}"; else wds="${GREEN}● $(t q_wd_on)${NC}"; fi ;;
        off) wds="${YELLOW}○ $(t q_wd_off)${NC}" ;;
        *)   wds="${DIM}$(t q_wd_none)${NC}" ;;
    esac
    echo -e "  $(t q_traffic): ${trs} · WARP: ${wps} · $(t q_watchdog): ${wds}"
    if [ "$warp_on" = "on" ]; then
        echo -e "  ${WHITE}$(t q_cf_traffic)${NC} 1м $(metrics_cf_window 60) · 5м $(metrics_cf_window 300) · 1ч $(metrics_cf_window 3600) · 1д $(metrics_cf_window 86400)"
    fi
    if [ "$wm" != "-" ] && [ "$wm" -ge 10 ] 2>/dev/null; then
        if [ "$warp_on" = "on" ]; then
            echo -e "  ${YELLOW}⚠ $(tf q_hint_high_on "$wm")${NC}"
        else
            # Совет про WARP больше не прячем при middle-proxy: запрета по конфигу нет,
            # включение решается замером порта ME. Где он проходит — WARP реально поможет,
            # где нет — включение само откатится и объяснит причину.
            echo -e "  ${YELLOW}⚠ $(tf q_hint_high_off "$wm")${NC}"
        fi
    fi
}

# ── оффер WARP при первой установке (объясняет зачем) ────────────────────────
offer_warp_setup() {
    type warp_egress_enable >/dev/null 2>&1 || return 0
    local ch k
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t warp_offer_title)${NC}"
    echo -e "  ${DIM}$(t warp_offer_desc1)${NC}"
    echo -e "  ${DIM}$(t warp_offer_desc2)${NC}"
    echo -e "  ${CYAN}1${NC}) $(t warp_offer_free)"
    echo -e "  ${CYAN}2${NC}) $(t warp_offer_key)"
    echo -e "  ${CYAN}0${NC}) $(t warp_offer_skip)"
    echo -ne "  ${WHITE}$(t choose): ${NC}"
    read -r ch || ch=0
    case "$ch" in
        1) warp_egress_enable "" || true ;;
        2) echo -ne "  $(t warp_ask_key)"; read -r k || k=""
           if [ -z "$k" ]; then log_warning "$(t warp_key_empty)"
           elif ! _warp_valid_key "$k"; then log_error "$(t warp_key_badfmt)"
           else warp_egress_enable "$k" || true; fi ;;
        *) log_dim "$(t warp_offer_later)" ;;
    esac
}
