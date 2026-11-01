#!/usr/bin/env bash
# lib/warp_egress.sh — egress трафика telemt→Telegram через Cloudflare WARP.
# Зачем: сервер обращается к дата-центрам Telegram с адреса Cloudflare, а не со
# своего. Проверено вживую: Cloudflare пропускает порт 8888, на котором висит
# большинство ME-эндпоинтов, — обратное утверждение из 2.10.4 не подтвердилось.
#
# Схема: L3 warp-интерфейс (wgcf + wg-quick, Table=off — дефолт/SSH НЕ трогаем)
# + policy-routing: ТОЛЬКО подсети Telegram DC уходят через warp (pref 100 < main 32766).
# Клиенты на :443 и всё остальное — напрямую, реальным IP. Режим-агностик (lite/pro).
# Fail-open: если Cloudflare недоступен — маршрут снимается, telemt на прямом egress.
#
# WARP-логика — в lib/warp_connector.sh (vendored из skill warp-egress-on-vps).

# ── namespace-изоляция от goWARP/AmneziaWG ───────────────────────────────────
# Свой интерфейс (gtwarp) и свои пути wgcf, чтобы НЕ трогать чужой /root/wgcf-*
# и чужой интерфейс `warp`, если на хосте уже живёт goWARP/другой WARP path-B.
WARP_IFACE="${WARP_IFACE:-gtwarp}"
WARP_DIR="/opt/gotelegram-warp"   # выделенный каталог — изоляция от goWARP (/root/wgcf-*, /opt/warp)
WARP_CONF="${WARP_CONF:-$WARP_DIR/${WARP_IFACE}.conf}"
# ВАЖНО: basename файлов wgcf оставляем дефолтными (wgcf register/generate пишут именно
# wgcf-account.toml / wgcf-profile.conf в cwd) — изолируем КАТАЛОГОМ, а не именем.
export WC_WGCF_BIN="$WARP_DIR/wgcf"
export WC_WGCF_ACCOUNT="$WARP_DIR/wgcf-account.toml"
export WC_WGCF_PROFILE="$WARP_DIR/wgcf-profile.conf"
export WC_WARP_CONF="$WARP_CONF"

WARP_TABLE="7788"                # вне auto-диапазона wg-quick (51820+) — без коллизий
WARP_RULE_PREF="100"             # ВЫШЕ приоритетом, чем main (32766) — иначе не матчит
WARP_LOG="/var/log/gotelegram-warp.log"
WARP_LOG_MAX=$((100*1024*1024))  # 100 МБ
WARP_WATCH_SVC="gotelegram-warp-watch"
WARP_BOOT_SVC="gotelegram-warp-egress"
WARP_FAIL_FILE="/run/gotelegram-warp.fail"   # "count:last_reissue_epoch:failopen"
WARP_DEEP_FILE="/run/gotelegram-warp.deep"   # epoch последнего глубокого перевыпуска
WARP_NOTE_FILE="/run/gotelegram-warp.note"   # заметка последнего enable (напр. ключ отвергнут)
# Счётчик подряд неудачных проб ME для аварийной ветки сторожа. Отдельный от
# WARP_FAIL_FILE: у обычного health свой порог (10 тиков), у аварии — свой.
WARP_RESCUE_FILE="/run/gotelegram-warp.rescue"
WARP_RESCUE_STRIKES="${WARP_RESCUE_STRIKES:-3}"

# Подсети дата-центров Telegram (IPv4, core.telegram.org/cidr). telemt форсит IPv4.
WARP_TG_SUBNETS="91.108.4.0/22 91.108.8.0/22 91.108.12.0/22 91.108.16.0/22 91.108.20.0/22 91.108.56.0/22 91.105.192.0/23 149.154.160.0/20 185.76.151.0/24"

# ── ЧЕМ telemt реально ходит в Telegram ──────────────────────────────────────
# ВАЖНО, ОПРОВЕРГНУТО ЗАМЕРОМ 23.08.2026. Здесь долго стояло утверждение
# «Cloudflare WARP не пропускает порт 8888». Оно неверно. Проверка с боевого
# сервера через поднятый gtwarp:
#
#   curl --interface gtwarp http://portquiz.net:8888/
#     → «Port test successful! Your IP: 104.28.222.15»
#
# То же на 80, 443, 8443. Скачивание 3 МБ через туннель — 3 000 000 байт за
# 335 мс, обрывов нет. Живые ME-эндпоинты на 8888 через WARP отвечают так же,
# как при прямом подключении. Порт проходит.
#
# Утверждение пришло из разбора одного сервера и было обобщено на всех без
# проверки. Из-за него WARP-egress сначала запрещался целиком, а потом
# блокировался «по карте портов». Обе блокировки сняты: решение принимается
# ЗАМЕРОМ реального пути, а не свойством, приписанным Cloudflare.
#
# Проверять всё равно надо тот порт, которым пойдёт telemt: при
# use_middle_proxy=true это 8888, иначе 443.
WARP_ME_PORT="${WARP_ME_PORT:-8888}"
_warp_middle_proxy_on() {
    local cfg="${TELEMT_CONFIG:-/etc/telemt/config.toml}"
    [ -f "$cfg" ] || return 1
    grep -qE '^[[:space:]]*use_middle_proxy[[:space:]]*=[[:space:]]*true' "$cfg" 2>/dev/null
}
_warp_probe_port() { if _warp_middle_proxy_on; then echo "$WARP_ME_PORT"; else echo 443; fi; }

# ── Раскладка карты ДЦ по портам (СПРАВОЧНО) ────────────────────────────────
# Замер 23.08.2026 на боевом сервере: 24 записи, 22 на 8888 и 2 на 443, причём
# обе — ОДИН IP 91.105.192.110 группы ±203. У ДЦ ±1…±5 записей на 443 нет.
# Отсюда следует только одно: идея «ходить исключительно по 443» нерабочая —
# она оставила бы одну группу из двенадцати.
#
# Решением о включении WARP эта раскладка НЕ является и раньше ошибочно
# являлась. Порт 8888 через Cloudflare проходит (замер в шапке модуля), так что
# состав карты по портам ничего не запрещает. Пригодность пути определяет живая
# проба; цифры ниже нужны для отчёта человеку.
WARP_MAP_V4="${WARP_MAP_V4:-/var/lib/telemt/cache/proxy-config-v4.txt}"
WARP_PASS_PORTS="${WARP_PASS_PORTS:-443 80}"   # порты, отличные от ME-порта

# Печатает: "<покрыто> <всего> <список_непокрытых_через_запятую>"
# rc=0 — посчитано; rc=1 — карты нет.
_warp_map_coverage() {
    [ -s "$WARP_MAP_V4" ] || return 1
    local pass; pass=$(printf '%s' "$WARP_PASS_PORTS" | tr ' ' '|')
    awk -v pass="^(${pass})$" '
        /^[[:space:]]*proxy_for[[:space:]]/ {
            g=$2; split($3,a,":"); p=a[2]; sub(/;$/,"",p)
            total[g]=1
            if (p ~ pass) ok[g]=1
        }
        END {
            n=0; c=0; bad=""
            for (g in total) {
                n++
                if (g in ok) c++
                else bad = (bad=="" ? g : bad "," g)
            }
            print c, n, (bad=="" ? "-" : bad)
        }' "$WARP_MAP_V4" 2>/dev/null
}

# То же, но только по КЛИЕНТСКИМ ДЦ (±1…±5) — это те, куда ходят пользователи.
# Группы вроде ±203 служебные. Печатает "<на_иных_портах> <всего_клиентских>";
# rc=1, если карты нет. Тоже справочно — на решение о включении не влияет.
_warp_core_coverage() {
    [ -s "$WARP_MAP_V4" ] || return 1
    local pass; pass=$(printf '%s' "$WARP_PASS_PORTS" | tr ' ' '|')
    awk -v pass="^(${pass})$" '
        /^[[:space:]]*proxy_for[[:space:]]/ {
            g=$2; split($3,a,":"); p=a[2]; sub(/;$/,"",p)
            gg = (g<0 ? -g : g)
            if (gg < 1 || gg > 5) next        # служебные группы не считаем
            total[g]=1
            if (p ~ pass) ok[g]=1
        }
        END { n=0; c=0; for (g in total) { n++; if (g in ok) c++ }; print c, n }' \
        "$WARP_MAP_V4" 2>/dev/null
}

_warp_dir() { echo "${GOTELEGRAM_DIR:-/opt/gotelegram}"; }
# В лог попадает сырой stdout/stderr wgcf — там может быть WARP+ ключ.
# Файл создаём под umask 077 и держим 600 (по умолчанию было бы 644).
_warp_log_ensure() {
    [ -e "$WARP_LOG" ] || ( umask 077; : > "$WARP_LOG" ) 2>/dev/null || true
    chmod 600 "$WARP_LOG" 2>/dev/null || true
}
_warp_wlog() {
    _warp_log_ensure
    printf '%s %s\n' "$(date -Iseconds 2>/dev/null || date)" "$*" >> "$WARP_LOG" 2>/dev/null
}

# self-contained писатель config.json (в systemd-контексте bot_update_config_field недоступен)
_warp_cfg_set() {
    if type bot_update_config_field >/dev/null 2>&1; then bot_update_config_field "$@"; return $?; fi
    local key="$1" value="$2" tmp now cfg
    cfg="${GOTELEGRAM_CONFIG:-${GOTELEGRAM_DIR:-/opt/gotelegram}/config.json}"
    [ -f "$cfg" ] || return 1
    tmp=$(mktemp) || return 1
    now=$(date -Iseconds 2>/dev/null || date +%s)
    if jq --arg k "$key" --arg v "$value" --arg t "$now" '.[$k]=$v | .updated_at=$t' "$cfg" >"$tmp" 2>/dev/null; then
        mv "$tmp" "$cfg"; chmod 600 "$cfg" 2>/dev/null; return 0
    fi
    rm -f "$tmp"; return 1
}

_warp_load_connector() {
    type wc_setup_warp_iface >/dev/null 2>&1 && return 0
    local cc; cc="$(_warp_dir)/lib/warp_connector.sh"
    [ -f "$cc" ] || cc="$(dirname "${BASH_SOURCE[0]}")/warp_connector.sh"
    [ -f "$cc" ] && { source "$cc" 2>/dev/null; return 0; }
    type log_error >/dev/null 2>&1 && log_error "warp_connector.sh не найден" >&2
    return 1
}

warp_egress_is_on() { [ "$(config_get warp_egress 2>/dev/null)" = "on" ]; }

# ── достижимость Telegram DC ──────────────────────────────────────────────────
# Через bind на warp-интерфейс (НЕ зависит от policy-route) — истинный сигнал
# «warp реально дотягивается до Telegram». Используется в health/watchdog,
# в т.ч. когда routing снят (fail-open).
# ── ПРИЗНАК ДОСТИЖИМОСТИ ────────────────────────────────────────────────────
# Факт установленного TCP-соединения через WARP не значит НИЧЕГО. Замер с
# боевого сервера, все пробы через поднятый gtwarp:
#
#   149.154.175.50:9999  (порт закрыт)          → «соединился» за 0,007 с
#   198.51.100.77:8888   (адреса не существует) → «соединился» за 0,007 с
#   203.0.113.9:443      (адреса не существует) → «соединился» за 0,007 с
#
# Cloudflare принимает рукопожатие у себя на краю независимо от того, живёт ли
# что-то на том конце. Прежняя проверка судила ровно по этому признаку и
# поэтому ВСЕГДА возвращала «достижимо» — то есть не проверяла ничего, а
# сторожевое спасение сломанного сервера не срабатывало никогда.
#
# Рабочий признак — ОТВЕТ. Живой узел на мусорный кадр отвечает: закрывает
# соединение (EOF) или шлёт байты, и то и другое приходит за доли секунды.
# Чёрная дыра молчит до таймаута. Проверено на обоих путях: реальные ME
# отвечают, адреса из TEST-NET молчат.
#
# rc=0 — узел ответил; rc≠0 — соединение не встало либо молчит.
_warp_endpoint_responds() {
    local ip="$1" port="$2" t="${3:-4}"
    timeout "$t" bash -c "exec 3<>/dev/tcp/${ip}/${port} 2>/dev/null || exit 1
        printf '\357\357\357\357\357\357\357\357' >&3 2>/dev/null || exit 1
        head -c 1 <&3 >/dev/null 2>&1" 2>/dev/null
}

# Эндпоинты для проб: сперва из фактической карты этого сервера (это ровно те
# адреса, куда пойдёт telemt), иначе — запасной зашитый список.
_warp_probe_targets() {
    local port="$1" got=""
    if [ -s "$WARP_MAP_V4" ]; then
        got=$(sed -nE "s/^[[:space:]]*proxy_for[[:space:]]+-?[0-9]+[[:space:]]+([0-9.]+):${port};.*/\1/p" \
              "$WARP_MAP_V4" 2>/dev/null | sort -u | head -3)
    fi
    [ -n "$got" ] && { printf '%s\n' "$got"; return 0; }
    printf '%s\n' 149.154.175.100 91.108.56.107 149.154.167.51
}

_warp_reach_via_warp() {
    ip addr show "$WARP_IFACE" >/dev/null 2>&1 || return 1
    local ip port
    port=$(_warp_probe_port)
    while read -r ip; do
        [ -n "$ip" ] || continue
        _warp_endpoint_responds "$ip" "$port" 4 && return 0
    done <<< "$(_warp_probe_targets "$port")"
    return 1
}

# По фактическому policy-route (тестирует путь, которым пойдёт telemt). Для pre-flight в enable.
_warp_tg_reachable() {
    local ip port
    port=$(_warp_probe_port)
    while read -r ip; do
        [ -n "$ip" ] || continue
        _warp_endpoint_responds "$ip" "$port" 6 && return 0
    done <<< "$(_warp_probe_targets "$port")"
    return 1
}

# ── policy-routing (идемпотентно, с защитой чужой таблицы) ───────────────────
_warp_routing_present() {
    ip rule list 2>/dev/null | grep -q "lookup $WARP_TABLE" && \
    ip route show table "$WARP_TABLE" 2>/dev/null | grep -q "dev $WARP_IFACE"
}
# Снять ТОЛЬКО свои правила. Раньше здесь было `ip rule del pref 100` в цикле —
# оно сносит ЛЮБОЕ правило с этим pref, включая управляющие маршруты админа на
# multi-homed VPS (и повторяет это каждые 30с в watchdog). Селектор в `ip rule del`
# матчится ядром по ВСЕМ указанным полям, поэтому pref+table = только наши.
_warp_del_our_rules() {
    local net guard=0
    # 1) адресно по своему списку подсетей Telegram
    for net in $WARP_TG_SUBNETS; do
        while ip rule del to "$net" table "$WARP_TABLE" pref "$WARP_RULE_PREF" 2>/dev/null; do
            guard=$((guard + 1)); [ "$guard" -gt 256 ] && return 0
        done
    done
    # 2) хвосты прошлых версий/оборванных применений: любое правило с нашим pref,
    #    но ТОЛЬКО если оно ведёт в НАШУ таблицу. Чужое с тем же pref не трогаем.
    while ip rule list 2>/dev/null | grep -qE "^${WARP_RULE_PREF}:.*lookup[[:space:]]+${WARP_TABLE}([[:space:]]|$)"; do
        ip rule del pref "$WARP_RULE_PREF" table "$WARP_TABLE" 2>/dev/null || break
        guard=$((guard + 1)); [ "$guard" -gt 256 ] && break
    done
    return 0
}

_warp_setup_routing() {
    ip addr show "$WARP_IFACE" >/dev/null 2>&1 || return 1
    local existing; existing=$(ip route show table "$WARP_TABLE" 2>/dev/null)
    if [ -n "$existing" ] && ! echo "$existing" | grep -q "dev $WARP_IFACE"; then
        _warp_wlog "WARN: table $WARP_TABLE занята чужим маршрутом — не трогаю"; return 1
    fi
    ip route replace default dev "$WARP_IFACE" table "$WARP_TABLE" 2>/dev/null
    _warp_del_our_rules
    local net
    for net in $WARP_TG_SUBNETS; do
        ip rule add to "$net" table "$WARP_TABLE" pref "$WARP_RULE_PREF" 2>/dev/null
    done
    ip route flush cache 2>/dev/null || true
    _warp_wlog "routing applied: $(echo $WARP_TG_SUBNETS|wc -w) подсетей → table $WARP_TABLE pref $WARP_RULE_PREF"
}
_warp_teardown_routing() {
    _warp_del_our_rules
    ip route flush table "$WARP_TABLE" 2>/dev/null || true
    ip route flush cache 2>/dev/null || true
    _warp_wlog "routing removed"
}

# ── health = warp реально дотягивается до Telegram (bind, без маскировки) ─────
_warp_health() { _warp_reach_via_warp; }

# обновить закэшированный tier в config (чтобы бот/вебка не врали «plus» после деградации)
_warp_refresh_tier() {
    local t; t=$(wc_verify_iface "$WARP_IFACE" 2>/dev/null)
    case "$t" in free|plus) _warp_cfg_set warp_tier "$t" >/dev/null 2>&1 || true ;; esac
}

# ── кап логов 100 МБ (без host-wide journald-вайпа!) ──────────────────────────
_warp_log_cap() {
    [ -f "$WARP_LOG" ] || return 0
    local sz; sz=$(stat -c %s "$WARP_LOG" 2>/dev/null || echo 0)
    if [ "$sz" -gt "$WARP_LOG_MAX" ] 2>/dev/null; then
        # обрезанный хвост создаём тоже под 077, иначе mv вернул бы 644
        ( umask 077; tail -c $((WARP_LOG_MAX/2)) "$WARP_LOG" > "$WARP_LOG.tmp" ) 2>/dev/null \
            && mv "$WARP_LOG.tmp" "$WARP_LOG" && chmod 600 "$WARP_LOG" 2>/dev/null
        _warp_wlog "log capped (было ${sz}б)"
    fi
}

# ── перевыпуск (мягко → глубоко с капом 1/час), под локом ─────────────────────
_warp_reissue_core() {
    _warp_load_connector || return 1
    local key; key=$(config_get warp_key 2>/dev/null)
    local kind; { [ -n "$key" ] && [ "$key" != "free" ]; } && kind=plus || kind=free
    _warp_log_ensure
    _warp_wlog "REISSUE (key=$kind)"
    type log_warning >/dev/null 2>&1 && log_warning "WARP: нет доступа к CDN — перевыпускаю сессию" >&2
    # 1) мягко: re-up на СУЩЕСТВУЮЩЕМ аккаунте (без churn устройств)
    wc_down "$WARP_CONF" 2>/dev/null || true
    [ "$kind" = "plus" ] && wc_apply_plus_wgcf "$key" 2>>"$WARP_LOG" || true
    wc_generate_profile 2>>"$WARP_LOG"; wc_build_warp_conf "$WARP_CONF" 2>>"$WARP_LOG"; wc_up "$WARP_CONF" 2>>"$WARP_LOG"
    _warp_setup_routing
    if _warp_reach_via_warp; then
        wc_persist_iface "$WARP_CONF" >/dev/null 2>&1 || true; rm -f "$WARP_FAIL_FILE" 2>/dev/null
        _warp_wlog "REISSUE ok (re-up)"; systemctl restart telemt >/dev/null 2>&1 || true; return 0
    fi
    # 2) глубоко: НОВОЕ устройство — не чаще раза в час (защита device-limit 1056)
    local dep; dep=$(cat "$WARP_DEEP_FILE" 2>/dev/null); dep=${dep:-0}
    if [ $(( $(date +%s) - dep )) -ge 3600 ]; then
        date +%s > "$WARP_DEEP_FILE"
        _warp_wlog "REISSUE deep: новая регистрация устройства"
        rm -f "$WC_WGCF_ACCOUNT" "$WC_WGCF_PROFILE" 2>/dev/null
        wc_setup_warp_iface "${key:-}" 2>>"$WARP_LOG" >/dev/null
        _warp_setup_routing
        if _warp_reach_via_warp; then
            wc_persist_iface "$WARP_CONF" >/dev/null 2>&1 || true; rm -f "$WARP_FAIL_FILE" 2>/dev/null
            _warp_wlog "REISSUE ok (new device)"; systemctl restart telemt >/dev/null 2>&1 || true; return 0
        fi
    else
        _warp_wlog "REISSUE deep пропущен (кап 1/час — защита слотов устройства)"
    fi
    _warp_wlog "REISSUE failed — Cloudflare недоступен с этого сервера?"; return 1
}
warp_egress_reissue() { wc_with_lock iface _warp_reissue_core; }

# ── тик watchdog (systemd timer 30с), под локом. Reissue внутри — реентрантно. ─
_warp_watch_core() {
    _warp_log_cap
    # СПАСЕНИЕ УЖЕ СЛОМАННЫХ. Осторожно: сама по себе связка middle-proxy + WARP НЕ является
    # поломкой — у части серверов порт ME через Cloudflare проходит, и у них всё работает.
    # Поэтому судим по ЗАМЕРУ, а не по строчке в конфиге: рескью только если порт ME реально
    # не проходит через WARP. Ждать десяти тиков health в этом случае нельзя — прокси лежит
    # полностью, ни одного живого ME-writer'а, все клиенты получают handshake timeout.
    #
    # Порог. Раньше ветка срабатывала с ПЕРВОЙ неудачной пробы, а проба — это
    # три адреса по 4 с с выходом по первому успеху, то есть около 12 секунд без
    # связи. Цена ошибочного срабатывания несимметрична цене ожидания: сетевая
    # икота стоила выключенного WARP (записанного в конфиг, то есть сам он
    # обратно не включится), снятых маршрутов и перезапуска telemt, рвущего все
    # клиентские соединения. Требуем WARP_RESCUE_STRIKES неудач подряд (по
    # умолчанию 3 ≈ 90 с при тике в 30 с): спасение остаётся быстрым, а икота
    # больше не выключает egress. Любой успех счётчик обнуляет.
    if _warp_middle_proxy_on && { _warp_routing_present || warp_egress_is_on; } \
       && ip addr show "$WARP_IFACE" >/dev/null 2>&1; then
      if _warp_reach_via_warp; then
        # ME отвечает — копившиеся страйки сбрасываем здесь же, иначе «подряд»
        # считалось бы не подряд: редкие одиночные неудачи за сутки сложились бы
        # в ложную аварию.
        rm -f "$WARP_RESCUE_FILE" 2>/dev/null
      else
        local _rs _rs_saved=1
        _rs=$(cat "$WARP_RESCUE_FILE" 2>/dev/null || echo 0)
        [[ "$_rs" =~ ^[0-9]+$ ]] || _rs=0
        _rs=$((_rs+1))
        printf '%s\n' "$_rs" > "$WARP_RESCUE_FILE" 2>/dev/null || _rs_saved=0
        # Если счётчик записать НЕ удалось (например /run только для чтения), он
        # на следующем тике снова прочтётся как 0, порог не наберётся никогда и
        # спасение не сработает вовсе — а раньше оно срабатывало с первой пробы.
        # Порог существует ради защиты от икоты, а не ради отказа действовать:
        # не можем считать — действуем, как действовали до порога.
        if [ "$_rs_saved" = "1" ] && [ "$_rs" -lt "$WARP_RESCUE_STRIKES" ]; then
            _warp_wlog "ME недоступен через WARP (${_rs}/${WARP_RESCUE_STRIKES}) — жду подтверждения, пока ничего не трогаю"
            return 0
        fi
        [ "$_rs_saved" = "0" ] && _warp_wlog "счётчик проб не сохраняется (${WARP_RESCUE_FILE}) — работаю без порога"
        rm -f "$WARP_RESCUE_FILE" 2>/dev/null
        _warp_wlog "АВАРИЯ: порт ${WARP_ME_PORT} не проходит через WARP при use_middle_proxy=true (${_rs} проб подряд) — снимаю routing и выключаю WARP-egress"
        _warp_teardown_routing
        _warp_cfg_set warp_egress off
        rm -f "$WARP_FAIL_FILE" 2>/dev/null
        echo "me-unreachable" > "$WARP_NOTE_FILE" 2>/dev/null
        type log_warning >/dev/null 2>&1 && log_warning "WARP-egress выключен автоматически: порт ${WARP_ME_PORT} (middle-proxy) через Cloudflare не проходит" >&2
        systemctl restart telemt >/dev/null 2>&1 || true
        return 0
      fi
    fi
    ip addr show "$WARP_IFACE" >/dev/null 2>&1 || { wg-quick up "$WARP_CONF" >/dev/null 2>&1 || true; sleep 1; }
    # `2>/dev/null` здесь не помогало: перенаправление входа выполняется ДО
    # запуска read, и о его провале сообщает сама оболочка, чей stderr в этот
    # момент ещё не перенаправлен. Файла обычно нет — он появляется только при
    # неудачах, — поэтому сторож печатал «No such file or directory» каждый тик,
    # то есть раз в 30 секунд, круглые сутки.
    local n last fo now
    if [ -r "$WARP_FAIL_FILE" ]; then
        IFS=: read -r n last fo < "$WARP_FAIL_FILE" || true
    fi
    n=${n:-0}; last=${last:-0}; fo=${fo:-0}; now=$(date +%s)
    if _warp_health; then
        _warp_routing_present || _warp_setup_routing
        _warp_refresh_tier
        [ "$fo" = "1" ] && _warp_wlog "RECOVERED: CDN доступен — routing восстановлен, fail-open снят"
        rm -f "$WARP_FAIL_FILE" 2>/dev/null
        # Связь есть — копившиеся «страйки» аварийной ветки обнуляем, иначе
        # редкие одиночные неудачи за сутки сложились бы в ложную аварию.
        rm -f "$WARP_RESCUE_FILE" 2>/dev/null
        return 0
    fi
    n=$((n+1)); _warp_wlog "health FAIL #$n"
    if [ "$fo" != "1" ]; then
        if [ "$n" -ge 10 ]; then
            _warp_teardown_routing; fo=1
            _warp_wlog "FAIL-OPEN: CDN недоступен ~5мин — routing снят, telemt на прямом egress"
            type log_warning >/dev/null 2>&1 && log_warning "WARP: CDN недоступен — fail-open, telemt на прямом egress" >&2
        else
            _warp_routing_present || _warp_setup_routing
        fi
    fi
    printf '%s:%s:%s\n' "$n" "$last" "$fo" > "$WARP_FAIL_FILE"
    # reissue с бэкоффом: n=2,6,10,14… (каждые 4 тика ≈ 2 мин) И не чаще раза в 120с
    if [ $((now-last)) -ge 120 ] && [ $((n%4)) -eq 2 ]; then
        printf '%s:%s:%s\n' "$n" "$now" "$fo" > "$WARP_FAIL_FILE"
        warp_egress_reissue || true
    fi
    return 0
}
warp_egress_watch_tick() {
    warp_egress_is_on || return 0
    _warp_load_connector || return 0
    wc_with_lock iface _warp_watch_core
}

# ── systemd юниты ─────────────────────────────────────────────────────────────
_warp_install_units() {
    local gd; gd="$(_warp_dir)"
    cat > "/etc/systemd/system/${WARP_BOOT_SVC}.service" <<EOF
[Unit]
Description=goTelegram WARP-egress routing (Telegram DC → Cloudflare)
After=network-online.target wg-quick@${WARP_IFACE}.service
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
# Три вещи, которых тут не хватало и которые стоили клиенту 13 часов простоя:
#  1) проверка ПОСЛЕ применения: маршрут ставится, и если порт middle-proxy через WARP
#     не проходит — снимается обратно, чтобы сервер не встал после перезагрузки;
#  2) откат при неудаче — раньше ExecStart падал, а половина policy-правил оставалась жить;
#  3) ExecStopPost — снятие маршрутов, когда юнит остановлен или свалился в failed.
ExecStart=/bin/bash -c 'source $gd/lib/common.sh 2>/dev/null; source $gd/lib/warp_connector.sh 2>/dev/null; source $gd/lib/warp_egress.sh; warp_egress_is_on || exit 0; ip addr show ${WARP_IFACE} >/dev/null 2>&1 || exit 1; _warp_setup_routing || { _warp_teardown_routing; exit 1; }; if ! _warp_tg_reachable; then _warp_wlog "BOOT: порт \${WARP_ME_PORT} через WARP не проходит — routing снят, остаёмся на прямом egress"; _warp_teardown_routing; exit 1; fi'
ExecStopPost=/bin/bash -c 'source $gd/lib/common.sh 2>/dev/null; source $gd/lib/warp_egress.sh; _warp_teardown_routing'
[Install]
WantedBy=multi-user.target
EOF
    cat > "/etc/systemd/system/${WARP_WATCH_SVC}.service" <<EOF
[Unit]
Description=goTelegram WARP-egress watchdog
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/bin/bash -c 'source $gd/lib/common.sh 2>/dev/null; source $gd/lib/warp_connector.sh 2>/dev/null; source $gd/lib/warp_egress.sh; warp_egress_watch_tick'
EOF
    cat > "/etc/systemd/system/${WARP_WATCH_SVC}.timer" <<EOF
[Unit]
Description=goTelegram WARP-egress watchdog каждые 30с
[Timer]
OnBootSec=45
OnUnitActiveSec=30
AccuracySec=5
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable --now "${WARP_BOOT_SVC}.service" >/dev/null 2>&1 || true
    systemctl enable --now "${WARP_WATCH_SVC}.timer" >/dev/null 2>&1 || true
}
_warp_remove_units() {
    systemctl disable --now "${WARP_WATCH_SVC}.timer" >/dev/null 2>&1 || true
    systemctl stop "${WARP_WATCH_SVC}.service" >/dev/null 2>&1 || true
    systemctl disable --now "${WARP_BOOT_SVC}.service" >/dev/null 2>&1 || true
    systemctl disable --now "wg-quick@${WARP_IFACE}" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/${WARP_WATCH_SVC}.timer" "/etc/systemd/system/${WARP_WATCH_SVC}.service" "/etc/systemd/system/${WARP_BOOT_SVC}.service" 2>/dev/null
    systemctl daemon-reload 2>/dev/null || true
}

# ── ВКЛ (под локом, с pre-flight и fail-open) ────────────────────────────────
_warp_enable_core() {
    local key="${1:-}"
    # ПРЕДУПРЕЖДЕНИЕ ПО КАРТЕ (до того, как что-либо трогаем).
    # Считаем, сколько групп ДЦ переживёт езду через Cloudflare, и говорим цифрами.
    # Отказ здесь дешевле отката: pre-flight ниже всё равно вернёт всё назад, но
    # уже подёргав routing на живом прокси. GT_WARP_IGNORE_MAP=1 — обойти явно.
    # Расклад карты по портам пишем в лог — как справку, НЕ как приговор.
    # Отказ «на 8888 нет альтернатив, значит Cloudflare не пропустит» снят: замер
    # показал, что 8888 через WARP проходит (см. шапку модуля). Решает pre-flight
    # ниже — он щупает реальный путь после применения маршрутов.
    local _cov _c _n _bad
    if _cov=$(_warp_map_coverage); then
        _c=$(printf '%s' "$_cov"  | awk '{print $1}')
        _n=$(printf '%s' "$_cov"  | awk '{print $2}')
        _bad=$(printf '%s' "$_cov" | awk '{print $3}')
        [ "$_bad" != "-" ] && _warp_wlog "ENABLE: в карте на портах ${WARP_PASS_PORTS} — ${_c} групп из ${_n}; только на ${WARP_ME_PORT}: ${_bad} (справочно, решает pre-flight)"
    fi
    rm -f "$WARP_NOTE_FILE" 2>/dev/null
    _warp_load_connector || return 1
    mkdir -p "$WARP_DIR" 2>/dev/null
    _warp_log_ensure   # ниже stdout/stderr wgcf уходит прямо в лог (там бывает ключ)
    type log_step >/dev/null 2>&1 && log_step "Включаю WARP-egress (Telegram DC → Cloudflare)…" >&2
    wc_setup_warp_iface "${key:-}" >>"$WARP_LOG" 2>&1   # stdout (дисклеймер wgcf) — в лог, не в tier
    if ! wc_is_up_iface "$WARP_IFACE"; then
        type log_error >/dev/null 2>&1 && log_error "WARP-интерфейс не поднялся (детали: $WARP_LOG)" >&2
        return 1
    fi
    # tier — из живого trace (чистый токен free/plus/off), а НЕ из stdout wc_setup
    local tier; tier=$(wc_verify_iface "$WARP_IFACE" 2>/dev/null | grep -iE '^(plus|free|off)$' | tail -1); [ -z "$tier" ] && tier=free
    # платный ключ отвергнут? (device-limit-1056/key-free/bad-format/rejected) → работаем как free, но помечаем
    if [ -n "$key" ] && [ "$key" != "free" ]; then
        local kr; kr=$(wc_last_reason 2>/dev/null)
        case "$kr" in ok|"") : ;; *) echo "$kr" > "$WARP_NOTE_FILE" 2>/dev/null ;; esac
    fi
    _warp_setup_routing
    # PRE-FLIGHT: реально ли Telegram достижим через WARP? иначе откат (не ломаем прокси).
    if ! _warp_tg_reachable; then
        _warp_wlog "PRE-FLIGHT FAIL: Telegram DC недостижим через WARP — откат"
        _warp_teardown_routing
        wc_down "$WARP_CONF" 2>/dev/null || true
        echo "tg-unreachable" > "$WARP_NOTE_FILE" 2>/dev/null
        if _warp_middle_proxy_on; then
            echo "me-unreachable" > "$WARP_NOTE_FILE" 2>/dev/null
            type log_error >/dev/null 2>&1 && log_error "WARP не включён: порт ${WARP_ME_PORT} (middle-proxy) через Cloudflare с этого сервера не проходит — telemt остался бы без связи с Telegram. Прямой egress сохранён. Детали: $WARP_LOG" >&2
        else
            type log_error >/dev/null 2>&1 && log_error "WARP не включён: дата-центры Telegram недоступны через Cloudflare с этого сервера. telemt остаётся на прямом egress. Детали: $WARP_LOG" >&2
        fi
        return 2
    fi
    wc_persist_iface "$WARP_CONF" >/dev/null 2>&1 || true
    _warp_install_units
    _warp_log_cap
    _warp_cfg_set warp_egress on
    # ключ сохраняем ТОЛЬКО если он принят (иначе не ретраим битый ключ каждым reissue)
    if [ -n "$key" ] && [ "$key" != "free" ] && [ ! -s "$WARP_NOTE_FILE" ]; then _warp_cfg_set warp_key "$key"; fi
    _warp_cfg_set warp_tier "${tier:-free}"
    rm -f "$WARP_FAIL_FILE" 2>/dev/null
    systemctl restart telemt >/dev/null 2>&1 || true
    _warp_wlog "ENABLE ok tier=${tier:-?} note=$(cat "$WARP_NOTE_FILE" 2>/dev/null)"
    type log_success >/dev/null 2>&1 && log_success "WARP-egress ВКЛ (tier=${tier:-free}). Трафик telemt→Telegram идёт через Cloudflare." >&2
    return 0
}
warp_egress_enable() { wc_with_lock iface _warp_enable_core "$@"; }

# ── ВЫКЛ (под локом, полный teardown) ────────────────────────────────────────
_warp_disable_core() {
    _warp_load_connector 2>/dev/null || true
    # запоминаем ДО teardown: рестарт telemt рвёт живые сессии клиентов, и делать его «на всякий случай» нельзя
    local was_on=0; { warp_egress_is_on || _warp_routing_present; } && was_on=1
    type log_step >/dev/null 2>&1 && log_step "Выключаю WARP-egress…" >&2
    _warp_remove_units
    _warp_teardown_routing
    wg-quick down "$WARP_CONF" >/dev/null 2>&1 || wg-quick down "$WARP_IFACE" >/dev/null 2>&1 || true
    _warp_teardown_routing   # повторно — на случай тика, вернувшего правила
    rm -f "/etc/wireguard/${WARP_IFACE}.conf" 2>/dev/null
    rm -f "$WARP_FAIL_FILE" "$WARP_DEEP_FILE" "$WARP_NOTE_FILE" 2>/dev/null
    _warp_cfg_set warp_egress off
    # рестарт только если маршрут реально снимали: на уже выключенном WARP он бы зря уронил всех подключённых клиентов
    [ "$was_on" = "1" ] && { systemctl restart telemt >/dev/null 2>&1 || true; }
    _warp_wlog "DISABLE ok (was_on=$was_on)"
    type log_success >/dev/null 2>&1 && log_success "WARP-egress ВЫКЛ. telemt→Telegram напрямую (реальный IP)." >&2
    # rc по факту, а не вслепую: если правила остались, вызывающий не должен рапортовать «выключено»
    _warp_routing_present && return 1
    return 0
}
warp_egress_disable() { wc_with_lock iface _warp_disable_core; }

# ── смена ключа (под локом, с persist и reset-on-empty) ──────────────────────
_warp_apply_key_locked() {
    local key="${1:-}"
    if ! warp_egress_is_on; then
        _warp_enable_core "$key"; local erc=$?
        local note; note=$(cat "$WARP_NOTE_FILE" 2>/dev/null)
        if [ "$erc" = "0" ]; then echo "${note:-ok}"; else echo "${note:-failed}"; fi
        return $erc
    fi
    _warp_load_connector || { echo "no-connector"; return 1; }
    _warp_log_ensure
    wc_down "$WARP_CONF" 2>/dev/null || true
    local rc reason
    if [ -z "$key" ] || [ "$key" = "free" ]; then
        wc_reset_license_wgcf 2>>"$WARP_LOG"; reason="free"; rc=0
    else
        wc_apply_plus_wgcf "$key" 2>>"$WARP_LOG"; rc=$?; reason=$(wc_last_reason)
    fi
    wc_generate_profile 2>>"$WARP_LOG"; wc_build_warp_conf "$WARP_CONF" 2>>"$WARP_LOG"; wc_up "$WARP_CONF" 2>>"$WARP_LOG"
    _warp_setup_routing
    wc_persist_iface "$WARP_CONF" >/dev/null 2>&1 || true
    # при возврате на free ключ надо СТЕРЕТЬ: иначе вотчдог на первом же перевыпуске прочитает старый warp_key и молча вернёт WARP+
    case "$reason" in ok) _warp_cfg_set warp_key "$key" ;; free) _warp_cfg_set warp_key "" ;; esac
    local tier; tier=$(wc_verify_iface "$WARP_IFACE" 2>/dev/null); [ "$reason" = "free" ] && tier=free   # лицензию сняли — в конфиге не должно остаться «plus»
    _warp_cfg_set warp_tier "${tier:-free}"
    systemctl restart telemt >/dev/null 2>&1 || true
    _warp_wlog "APPLY_KEY reason=$reason tier=$tier"
    echo "$reason"; return $rc
}
warp_egress_apply_key() { wc_with_lock iface _warp_apply_key_locked "$@"; }

# ── информативность: trace / handshake / проба Telegram ──────────────────────
# cdn-cgi/trace через WARP-интерфейс (надёжнее ifconfig.me — Cloudflare себя не режет)
_warp_trace() { curl -s --interface "$WARP_IFACE" --max-time 4 https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null; }
_warp_tf() { printf '%s\n' "$1" | grep -E "^$2=" | head -1 | cut -d= -f2 | tr -d '\r'; }

# возраст свежего WireGuard-handshake, сек (пусто если рукопожатия ещё нет)
_warp_handshake_age() {
    local hs now; hs=$(wg show "$WARP_IFACE" latest-handshakes 2>/dev/null | awk 'NR==1{print $2}'); now=$(date +%s)
    if [ -n "$hs" ] && [ "$hs" -gt 0 ] 2>/dev/null; then echo $((now-hs)); fi
}

# валиден ли формат ключа WARP+ (4-4-4-4 или 8-8-8)
_warp_valid_key() { printf '%s' "$1" | grep -qE '^[A-Za-z0-9]{4}(-[A-Za-z0-9]{4}){3}$|^[A-Za-z0-9]{8}(-[A-Za-z0-9]{8}){2}$'; }

# Проба Telegram DC: N TCP-коннектов по policy-route (= путь telemt; при снятом routing это ПРЯМОЙ путь, не WARP).
# echo: "ok total min avg max loss"  (мс; loss — %)
_warp_probe_tg() {
    local n="${1:-10}" ip="${2:-149.154.167.51}" i ok=0 sum=0 min="" max="" ms start end
    for i in $(seq 1 "$n"); do
        start=$(date +%s%N 2>/dev/null); [ -n "$start" ] || start=0
        if timeout 2 bash -c "exec 3<>/dev/tcp/$ip/443" 2>/dev/null; then
            end=$(date +%s%N 2>/dev/null); [ -n "$end" ] || end=0
            ms=$(( (end - start) / 1000000 )); [ "$ms" -lt 1 ] 2>/dev/null && ms=1
            ok=$((ok+1)); sum=$((sum+ms))
            { [ -z "$min" ] || [ "$ms" -lt "$min" ]; } && min=$ms
            { [ -z "$max" ] || [ "$ms" -gt "$max" ]; } && max=$ms
        fi
    done
    local avg=0 loss=100
    [ "$ok" -gt 0 ] && { avg=$((sum/ok)); loss=$(( (n-ok)*100/n )); }
    echo "$ok $n ${min:-0} $avg ${max:-0} $loss"
}

# ── Отчёт о покрытии карты (для меню, бота и панели) ────────────────────────
# Один и тот же текст показывается и при отказе включения, и по запросу —
# чтобы человек видел ровно ту причину, по которой WARP не включился.
# Справка о карте: что на каких портах лежит + ЖИВАЯ проба доступности.
# Раскладка по портам сама по себе ничего не решает — 8888 через WARP проходит.
warp_egress_coverage_text() {
    local cov c n bad core cc cn port alive=0 total=0 ip
    if ! cov=$(_warp_map_coverage); then
        echo "Карта ДЦ ещё не получена (${WARP_MAP_V4}) — раскладку показать не по чему. Обычно она появляется через минуту после старта telemt."
        return 0
    fi
    c=$(printf '%s' "$cov" | awk '{print $1}'); n=$(printf '%s' "$cov" | awk '{print $2}')
    bad=$(printf '%s' "$cov" | awk '{print $3}')
    core=$(_warp_core_coverage 2>/dev/null)
    cc=$(printf '%s' "$core" | awk '{print $1+0}'); cn=$(printf '%s' "$core" | awk '{print $2+0}')
    port=$(_warp_probe_port)

    if [ "$bad" = "-" ]; then
        printf 'Карта: все %s групп ДЦ представлены на портах %s.\n' "$n" "$WARP_PASS_PORTS"
    else
        printf 'Карта: на портах %s — %s групп ДЦ из %s (клиентских %s из %s); только на %s — группы %s.\n' \
            "$WARP_PASS_PORTS" "$c" "$n" "$cc" "$cn" "$WARP_ME_PORT" "$bad"
    fi

    while read -r ip; do
        [ -n "$ip" ] || continue
        total=$((total + 1))
        _warp_endpoint_responds "$ip" "$port" 4 && alive=$((alive + 1))
    done <<< "$(_warp_probe_targets "$port")"
    printf 'Живая проба порта %s (%s — текущий путь): отвечают %s из %s.\n' \
        "$port" "$(_warp_routing_present && echo 'через WARP' || echo 'прямой egress')" "$alive" "$total"
    echo "Раскладка по портам справочная: порт ${WARP_ME_PORT} через Cloudflare проходит (проверено), поэтому включение решается живой пробой, а не составом карты."
}

warp_egress_coverage_json() {
    local cov c n bad core cc cn
    if ! cov=$(_warp_map_coverage); then
        printf '{"status":"unknown","reason":"no-map","map":"%s"}\n' "$WARP_MAP_V4"; return 0
    fi
    c=$(printf '%s' "$cov" | awk '{print $1}'); n=$(printf '%s' "$cov" | awk '{print $2}')
    bad=$(printf '%s' "$cov" | awk '{print $3}')
    core=$(_warp_core_coverage 2>/dev/null)
    cc=$(printf '%s' "$core" | awk '{print $1+0}'); cn=$(printf '%s' "$core" | awk '{print $2+0}')
    # verdict описывает СОСТАВ КАРТЫ, а не пригодность WARP: 8888 через
    # Cloudflare проходит, решает живая проба (поле probe_*).
    local verdict="full"; [ "$bad" != "-" ] && verdict="partial"
    local port alive=0 total=0 ip; port=$(_warp_probe_port)
    while read -r ip; do
        [ -n "$ip" ] || continue
        total=$((total + 1))
        _warp_endpoint_responds "$ip" "$port" 4 && alive=$((alive + 1))
    done <<< "$(_warp_probe_targets "$port")"
    local via="direct"; _warp_routing_present && via="warp"
    printf '{"status":"ok","map":"%s","groups_ok":%s,"groups_total":%s,"core_ok":%s,"core_total":%s,"only_me_port":"%s","me_port":%s,"probe_via":"%s","probe_alive":%s,"probe_total":%s}\n' \
        "$verdict" "$c" "$n" "${cc:-0}" "${cn:-0}" "$bad" "$WARP_ME_PORT" "$via" "$alive" "$total"
}

# JSON пинг/потери (для --action=warp-probe → бот/вебка)
warp_egress_probe_json() {
    if ! ip addr show "$WARP_IFACE" >/dev/null 2>&1; then printf '{"reachable":false,"routing":"off","loss":100,"ok":0,"samples":0,"ping_avg":0}\n'; return 0; fi
    local r; r=$(_warp_probe_tg 10); set -- $r
    local reach=false; [ "$1" -gt 0 ] 2>/dev/null && reach=true
    # при fail-open маршрут снят и проба меряет прямой путь — без этого поля потребитель подпишет результат «через WARP» и соврёт
    local routing; _warp_routing_present && routing="on" || routing="off"
    printf '{"reachable":%s,"routing":"%s","ok":%s,"samples":%s,"ping_min":%s,"ping_avg":%s,"ping_max":%s,"loss":%s}\n' "$reach" "$routing" "$1" "$2" "$3" "$4" "$5" "$6"
}

# ── статус ────────────────────────────────────────────────────────────────────
warp_egress_status() {
    _warp_load_connector 2>/dev/null || true
    local on tier egress relay colo hs trace
    # state отдаём машинным on/off: русский литерал ломал разбор `case` в install.sh и в en-локали показывался по-русски
    if warp_egress_is_on; then on="on"; else on="off"; fi
    trace=$(_warp_trace)
    tier=$(_warp_tf "$trace" warp); case "$tier" in on) tier=free;; plus) tier=plus;; *) tier=$(wc_verify_iface "$WARP_IFACE" 2>/dev/null || echo "?");; esac
    egress=$(_warp_tf "$trace" ip); [ -z "$egress" ] && egress=$(curl -s4 --max-time 3 --interface "$WARP_IFACE" ifconfig.me 2>/dev/null | tr -cd '0-9A-Fa-f.:'); [ -z "$egress" ] && egress="N/A"
    colo=$(_warp_tf "$trace" colo); [ -z "$colo" ] && colo="?"
    hs=$(_warp_handshake_age); [ -n "$hs" ] && hs="${hs}s" || hs="—"
    if _warp_routing_present; then relay="Telegram→Cloudflare"; else relay="напрямую"; fi
    echo "state=$on"; echo "tier=$tier"; echo "egress=$egress"; echo "colo=$colo"; echo "handshake=$hs"; echo "route=$relay"
}

warp_egress_status_json() {
    local on tier egress iface route colo hs trace w
    warp_egress_is_on && on="on" || on="off"
    ip addr show "$WARP_IFACE" >/dev/null 2>&1 && iface="up" || iface="down"
    trace=$(_warp_trace)
    tier=$(config_get warp_tier 2>/dev/null)
    if [ -z "$tier" ]; then w=$(_warp_tf "$trace" warp); case "$w" in on) tier=free;; plus) tier=plus;; *) tier="";; esac; fi
    egress=$(_warp_tf "$trace" ip); [ -z "$egress" ] && egress=$(curl -s4 --max-time 3 --interface "$WARP_IFACE" ifconfig.me 2>/dev/null | tr -cd '0-9A-Fa-f.:')
    colo=$(_warp_tf "$trace" colo)
    hs=$(_warp_handshake_age); [ -z "$hs" ] && hs="-1"
    _warp_routing_present && route="on" || route="off"
    printf '{"warp_egress":"%s","iface":"%s","tier":"%s","egress":"%s","colo":"%s","handshake":%s,"routing":"%s"}\n' "$on" "$iface" "${tier//\"/}" "$egress" "${colo//\"/}" "$hs" "$route"
}

# ── живой мониторинг (alt-screen, рефреш ~3с, q/Enter — выход) ────────────────
warp_egress_dashboard() {
    _warp_load_connector 2>/dev/null || true
    local tin=/dev/tty; [ -r /dev/tty ] || tin=/dev/stdin
    printf '\033[?1049h\033[?25l'
    trap 'printf "\033[?25h\033[?1049l"' EXIT
    local trace tier egress colo hs relay via probe ok total min avg max loss lc st key
    while true; do
        trace=$(_warp_trace)
        probe=$(_warp_probe_tg 8); set -- $probe; ok=$1; total=$2; min=$3; avg=$4; max=$5; loss=$6
        if warp_egress_is_on; then st=$'\033[1;32m● ВКЛ\033[0m'; else st=$'\033[1;33m○ ВЫКЛ\033[0m'; fi
        tier=$(_warp_tf "$trace" warp); case "$tier" in on) tier=free;; plus) tier=plus;; *) tier="?";; esac
        egress=$(_warp_tf "$trace" ip); [ -z "$egress" ] && egress="N/A"
        colo=$(_warp_tf "$trace" colo); [ -z "$colo" ] && colo="?"
        hs=$(_warp_handshake_age); [ -n "$hs" ] && hs="${hs}s назад" || hs="—"
        # подпись пробы идёт от маршрута: при снятом routing замер сделан напрямую, и «через WARP» было бы враньём
        if _warp_routing_present; then relay=$'\033[32mTelegram \342\206\222 Cloudflare \342\234\223\033[0m'; via="через WARP"; else relay=$'\033[33mнапрямую (fail-open?)\033[0m'; via="напрямую, WARP обойдён"; fi
        lc=$'\033[32m'; [ "$loss" -gt 0 ] 2>/dev/null && lc=$'\033[33m'; [ "$loss" -ge 50 ] 2>/dev/null && lc=$'\033[31m'
        printf '\033[H\033[2J'
        printf '  \033[1;37mWARP-egress \342\200\224 живой мониторинг\033[0m    \033[2m%s\033[0m\n\n' "$(date '+%H:%M:%S')"
        printf '  Состояние:   %b   (tier: %s)\n' "$st" "$tier"
        printf '  Egress WARP: \033[36m%s\033[0m  через %s\n' "$egress" "$colo"
        printf '  Handshake:   %s\n' "$hs"
        printf '  Маршрут:     %b\n\n' "$relay"
        printf '  \033[1mПинг до дата-центра Telegram (%s)\033[0m\n' "$via"
        if [ "$ok" -gt 0 ] 2>/dev/null; then
            printf '    задержка: \033[1m%s\033[0m мс   (min %s / max %s)\n' "$avg" "$min" "$max"
            printf '    потери:   %b%s%%\033[0m   (%s из %s успешно)\n' "$lc" "$loss" "$ok" "$total"
        else
            printf '    \033[31mTelegram недоступен (потери 100%%)\033[0m\n\n'
        fi
        if type metrics_cf_window >/dev/null 2>&1; then
            printf '\n  \033[1mТрафик через Cloudflare\033[0m\n'
            printf '    1м %s · 5м %s · 1ч %s · 1д %s\n' "$(metrics_cf_window 60)" "$(metrics_cf_window 300)" "$(metrics_cf_window 3600)" "$(metrics_cf_window 86400)"
        fi
        printf '\n  \033[2mобновление каждые 3с \302\267 q или Enter \342\200\224 выход\033[0m'
        key=""; read -t 3 -r -n 1 key < "$tin" 2>/dev/null; local rc=$?
        [ "$rc" -gt 128 ] && continue           # таймаут — перерисовка
        [ "$rc" -ne 0 ] && break                # EOF/ошибка — выход (не крутить curl вечно)
        case "$key" in q|Q|"") break;; esac
    done
    printf '\033[?25h\033[?1049l'
    trap - EXIT
}
