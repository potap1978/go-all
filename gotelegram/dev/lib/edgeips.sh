#!/usr/bin/env bash
# lib/edgeips.sh — учёт клиентского трафика ПО КАЖДОМУ адресу сервера.
#
# Зачем: когда у машины несколько внешних адресов и домен раздаётся несколькими
# A-записями, блокировка одного адреса выглядит снаружи как «у части людей
# перестало работать». Со стороны сервера это видно раньше и точнее: на живых
# адресах трафик идёт, на заблокированном — нет. Сравнение с соседями само
# нормализует время суток: ночью падает у всех, при блокировке — у одного.
#
# Считаем в той же цепочке, что и общая статистика: правила без перехода
# считают пакет и пропускают дальше, поэтому добавление своих правил не меняет
# ни общий счётчик, ни маршрутизацию.

EDGEIPS_CHAIN="${EDGEIPS_CHAIN:-GOTELEGRAM_STATS}"
EDGEIPS_HISTORY="${EDGEIPS_HISTORY:-/opt/gotelegram/edge_ips.csv}"
EDGEIPS_STATE_DIR="${EDGEIPS_STATE_DIR:-/run/gotelegram}"
# Сколько интервалов подряд адрес должен молчать, чтобы считаться погасшим.
# Один интервал ничего не значит: клиент мог просто не подключаться минуту.
# Тот же урок, что со сторожем WARP — там реакция с первой пробы стоила
# выключенного туннеля из-за сетевой икоты.
# Четырёх минут тишины мало. Счётчик считает ВЕСЬ трафик на 443 — вместе с
# сайтом-прикрытием, сканерами и служебными проверками, — а отличить по нему
# клиента от сканера нельзя. При этом адрес, который DNS отдаёт третьим, может
# законно молчать десятки минут просто потому, что клиентов мало. Поэтому порог
# высокий: 20 интервалов, то есть двадцать минут подряд.
EDGEIPS_DARK_STRIKES="${EDGEIPS_DARK_STRIKES:-20}"
# Ниже этого прироста за интервал считаем, что трафика нет. Не ноль: редкие
# одиночные пакеты (сканеры, ретрансмиты) приходят и на заблокированный адрес.
EDGEIPS_QUIET_BYTES="${EDGEIPS_QUIET_BYTES:-4096}"
EDGEIPS_RETENTION_DAYS="${EDGEIPS_RETENTION_DAYS:-31}"
# Собственный интервал. Коллектор статистики крутится в цикле с `sleep 1`, и
# встроенный туда снимок писал бы по строке на адрес КАЖДУЮ СЕКУНДУ — 260 тысяч
# строк в сутки на трёх адресах. Поэтому модуль сам решает, пора ли: вызывать
# edgeips_sample можно хоть каждый тик, лишние вызовы дёшево отсекаются.
EDGEIPS_INTERVAL="${EDGEIPS_INTERVAL:-60}"
EDGEIPS_STAMP="${EDGEIPS_STAMP:-/run/gotelegram/edgeips.last}"


# Внешние IPv4 машины. Петля и приватные диапазоны не считаются: клиенты
# приходят только на глобальные адреса.
edgeips_list() {
    ip -4 -o addr show scope global 2>/dev/null \
        | awk '{print $4}' | cut -d/ -f1 \
        | grep -vE '^(127\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|169\.254\.)' \
        | sort -u
}

_edgeips_port() {
    if type _stats_proxy_port >/dev/null 2>&1; then _stats_proxy_port; return; fi
    grep -m1 -oE '^port[[:space:]]*=[[:space:]]*[0-9]+' "${TELEMT_CONFIG:-/etc/telemt/config.toml}" 2>/dev/null \
        | grep -oE '[0-9]+' || echo 443
}

# По правилу на адрес. Идемпотентно: -C проверяет наличие перед добавлением.
# Правила для исчезнувших адресов снимаем — иначе после смены IP в цепочке
# копятся мёртвые счётчики и отчёт показывает адреса, которых уже нет.
edgeips_ensure_rules() {
    command -v iptables >/dev/null 2>&1 || return 1
    iptables -L "$EDGEIPS_CHAIN" -n >/dev/null 2>&1 || return 1
    local port ip live
    port="$(_edgeips_port)"
    live="$(edgeips_list)"

    while read -r ip; do
        [ -n "$ip" ] || continue
        iptables -C "$EDGEIPS_CHAIN" -p tcp -d "$ip" --dport "$port" 2>/dev/null \
            || iptables -A "$EDGEIPS_CHAIN" -p tcp -d "$ip" --dport "$port" 2>/dev/null
    done <<< "$live"

    # Снять правила адресов, которых на машине больше нет.
    local rule_ip
    while read -r rule_ip; do
        [ -n "$rule_ip" ] || continue
        grep -qx "$rule_ip" <<< "$live" && continue
        iptables -D "$EDGEIPS_CHAIN" -p tcp -d "$rule_ip" --dport "$port" 2>/dev/null || true
    done < <(iptables -S "$EDGEIPS_CHAIN" 2>/dev/null \
             | awk '/^-A/ && /-d /' | grep -oE '\-d [0-9.]+' | awk '{print $2}' | sort -u)
    return 0
}

# Сколько клиентов сейчас держат соединение с каждым адресом. Байты говорят об
# объёме, а подключения — о ТОМ САМОМ распределении, ради которого заводили
# несколько адресов: сколько человек резолвер отправил на каждый.
edgeips_conns() {
    local port; port="$(_edgeips_port)"
    ss -tn state established "( sport = :${port} )" 2>/dev/null | tail -n +2 \
        | awk '{ n = split($3, a, ":"); ip = a[1]
                 for (i = 2; i < n; i++) ip = ip ":" a[i]     # IPv6 в квадратных скобках
                 gsub(/[][]/, "", ip); print ip }' \
        | sort | uniq -c | awk '{print $2 "\t" $1}'
}

# Накопительные счётчики по адресам: "ip<TAB>пакеты<TAB>байты"
edgeips_counters() {
    command -v iptables >/dev/null 2>&1 || return 1
    local port; port="$(_edgeips_port)"
    # Колонка назначения — восьмая; у общего правила там 0.0.0.0/0, у наших —
    # конкретный адрес. Проверяем именно на вид адреса, а не «не 0.0.0.0/0»:
    # при пустой колонке target поля съезжают, и без проверки в вывод попадала
    # строка «tcp» от общего правила.
    iptables -L "$EDGEIPS_CHAIN" -v -n -x 2>/dev/null \
        | awk -v p="dpt:$port" '
            $0 ~ p && $8 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print $8 "\t" $1 "\t" $2 }'
}

# Снимок в историю. Формат: timestamp,ip,пакеты,байты — накопительные, как в
# stats_history.csv; приросты считает читатель.
# force=1 — снять немедленно, минуя интервал (для ручной проверки из меню).
edgeips_sample() {
    local force="${1:-0}" now last
    now="$(date +%s)"
    if [ "$force" != "1" ]; then
        last=0
        [ -r "$EDGEIPS_STAMP" ] && last="$(cat "$EDGEIPS_STAMP" 2>/dev/null || echo 0)"
        [ "$(( now - ${last:-0} ))" -ge "$EDGEIPS_INTERVAL" ] || return 0
    fi
    mkdir -p "$(dirname "$EDGEIPS_STAMP")" 2>/dev/null
    printf '%s\n' "$now" > "$EDGEIPS_STAMP" 2>/dev/null || true

    edgeips_ensure_rules || return 1
    local ts line
    ts="$now"
    mkdir -p "$(dirname "$EDGEIPS_HISTORY")" 2>/dev/null
    # Заголовок дописываем только для НОВОГО файла. Старые (четыре колонки)
    # читаются как есть: недостающее поле awk видит пустым и считает нулём —
    # переписывать накопленную историю ради колонки незачем.
    [ -f "$EDGEIPS_HISTORY" ] || echo "timestamp,ip,packets,bytes,conns" > "$EDGEIPS_HISTORY"
    local _c ip_c n_c
    declare -A _conn=()
    while IFS=$'\t' read -r ip_c n_c; do
        [ -n "$ip_c" ] && _conn["$ip_c"]="$n_c"
    done < <(edgeips_conns)
    while IFS=$'\t' read -r ip pkts bytes; do
        [ -n "$ip" ] || continue
        printf '%s,%s,%s,%s,%s\n' "$ts" "$ip" "${pkts:-0}" "${bytes:-0}" "${_conn[$ip]:-0}" >> "$EDGEIPS_HISTORY"
    done < <(edgeips_counters)
    _edgeips_prune
    return 0
}

_edgeips_prune() {
    [ -f "$EDGEIPS_HISTORY" ] || return 0
    local cutoff; cutoff=$(( $(date +%s) - EDGEIPS_RETENTION_DAYS * 86400 ))
    local tmp; tmp="$(mktemp "${EDGEIPS_HISTORY}.XXXXXX")" || return 0
    awk -F, -v c="$cutoff" 'NR==1 || $1+0 >= c' "$EDGEIPS_HISTORY" > "$tmp" 2>/dev/null \
        && mv -f "$tmp" "$EDGEIPS_HISTORY" || rm -f "$tmp"
}

# Приросты за последние N снимков и вердикт по каждому адресу.
# Вердикт сравнительный: адрес «погас», только если он молчит подряд
# EDGEIPS_DARK_STRIKES раз, А У СОСЕДЕЙ трафик при этом идёт. Без второго
# условия ночная тишина на всех адресах читалась бы как блокировка всех.
edgeips_report() {
    [ -f "$EDGEIPS_HISTORY" ] || { echo "no-data"; return 1; }
    # Фильтр живых адресов такой же, как в edgeips_windows. Без него `--action`
    # без --json и экран статистики показывали отданный хостеру адрес, которого
    # уже нет на машине, а те же данные в JSON и в шапке его скрывали.
    awk -F, -v strikes="$EDGEIPS_DARK_STRIKES" -v quiet="$EDGEIPS_QUIET_BYTES" \
        -v live="$(edgeips_list 2>/dev/null | tr '\n' ' ')" '
        NR == 1 { next }
        {
            ip = $2; b = $4 + 0
            if (ip in last_b) {
                d = b - last_b[ip]
                if (d < 0) d = 0                      # счётчик обнулился (рестарт правил)
                n = ++cnt[ip]
                delta[ip, n] = d
                total[ip] += d
                if (d >= quiet) livecnt[ip]++         # сколько интервалов адрес реально принимал
            }
            last_b[ip] = b
        }
        END {
            nlive = split(live, L, " ")
            for (i = 1; i <= nlive; i++) if (L[i] != "") alive[L[i]] = 1
            for (ip in cnt) {
                n = cnt[ip]; q = 0
                for (i = n; i > n - strikes && i > 0; i--) if (delta[ip, i] < quiet) q++
                quietrun[ip] = q
                recent[ip] = (n > 0) ? delta[ip, n] : 0
                if (delta[ip, n] >= quiet) anylive = 1
            }
            for (ip in cnt) {
                if (nlive > 0 && !(ip in alive)) continue
                # Порядок проверок важен. «Никогда не принимал» — это НЕ блокировка:
                # так выглядит адрес, на который просто не указывает ни одна
                # A-запись. Спутать одно с другим значит поднимать тревогу на
                # запасном адресе и приучать не верить сообщениям.
                # Чтобы сказать «перестал принимать», нужно доказательство, что
                # адрес принимал УСТОЙЧИВО, а не разово. Иначе пара пробных
                # пакетов на запасной адрес превращает его в «заблокированный».
                if (livecnt[ip] + 0 < strikes)                st = "unused"
                else if (quietrun[ip] >= strikes && anylive)  st = "dark"
                else if (quietrun[ip] >= strikes)             st = "quiet-all"
                else                                          st = "ok"
                printf "%s\t%d\t%d\t%s\n", ip, recent[ip], total[ip], st
            }
        }' "$EDGEIPS_HISTORY"
}

# Трафик и подключения за окна 15 минут / 1 час / 1 сутки.
# Считаем по РАЗНИЦЕ накопительных счётчиков на краях окна, а не суммой
# приростов: так пропущенные снимки (перезапуск коллектора, перегрузка) не
# теряются, а обнуление счётчика при пересоздании правил не уводит сумму в минус.
edgeips_windows() {
    [ -f "$EDGEIPS_HISTORY" ] || { echo "no-data"; return 1; }
    # Только адреса, которые есть на машине СЕЙЧАС. История живёт месяц, и без
    # этого отчёта отданный хостеру адрес ещё месяц висел бы в таблице с нулями
    # и знаком «не отвечает» — то есть выглядел бы заблокированным, хотя его
    # просто нет. Про адрес, который пропал, говорит сторож запасного входа.
    # Пустой список (адреса определить не удалось) — показываем всё: потерять
    # отчёт целиком хуже, чем показать лишнюю строку.
    awk -F, -v now="$(date +%s)" -v live="$(edgeips_list 2>/dev/null | tr '\n' ' ')" '
        NR == 1 { next }
        {
            ip = $2; ts = $1 + 0; b = $4 + 0; c = $5 + 0
            seen[ip] = 1
            last_b[ip] = b; last_c[ip] = c
            if (c > peak[ip]) peak[ip] = c
            csum[ip] += c; ccnt[ip]++
            # первое значение внутри каждого окна = граница отсчёта
            if (ts >= now - 900   && !(ip in b15)) b15[ip] = b
            if (ts >= now - 3600  && !(ip in b1h)) b1h[ip] = b
            if (ts >= now - 86400 && !(ip in b1d)) b1d[ip] = b
        }
        END {
            n = split(live, L, " ")
            for (i = 1; i <= n; i++) if (L[i] != "") alive[L[i]] = 1
            for (ip in seen) {
                if (n > 0 && !(ip in alive)) continue
                # Счётчик мог обнулиться внутри окна: правила пересоздаются при
                # перезагрузке и каждый раз, когда сторож возвращает пропавший
                # адрес. Тогда разница краёв уходит в минус — и таблица
                # показывала «-49980000 Б», ломая заодно ширину колонок. После
                # сброса весь накопленный счётчик и ЕСТЬ трафик за окно.
                d15 = (ip in b15) ? last_b[ip] - b15[ip] : 0
                d1h = (ip in b1h) ? last_b[ip] - b1h[ip] : 0
                d1d = (ip in b1d) ? last_b[ip] - b1d[ip] : 0
                if (d15 < 0) d15 = last_b[ip]
                if (d1h < 0) d1h = last_b[ip]
                if (d1d < 0) d1d = last_b[ip]
                printf "%s\t%d\t%d\t%d\t%d\t%d\t%.1f\n", ip,
                       d15, d1h, d1d,
                       last_c[ip], peak[ip],
                       (ccnt[ip] ? csum[ip] / ccnt[ip] : 0)
            }
        }' "$EDGEIPS_HISTORY" | sort
}

# Доступен ли адрес ПО ДОМЕНУ: именно так к нему приходит клиент — резолвит имя,
# получает адрес, идёт туда с SNI домена. Проверять сам по себе IP бессмысленно:
# telemt при чужом SNI ведёт себя иначе, и «порт открыт» ещё не значит «работает».
EDGEIPS_PROBE_CACHE="${EDGEIPS_PROBE_CACHE:-/run/gotelegram/edgeips.probe}"
EDGEIPS_PROBE_TTL="${EDGEIPS_PROBE_TTL:-20}"
EDGEIPS_PROBE_TIMEOUT="${EDGEIPS_PROBE_TIMEOUT:-4}"

edgeips_probe() {
    local force="${1:-0}" now age
    now="$(date +%s)"
    # Кэш обязателен: шапка перерисовывается каждые 30 секунд, а недоступный
    # адрес отвечает только по таймауту. Без кэша каждое обновление дёргало бы
    # сеть заново — и именно в тот момент, когда на шапку смотрят.
    if [ "$force" != "1" ] && [ -r "$EDGEIPS_PROBE_CACHE" ]; then
        age=$(( now - $(stat -c %Y "$EDGEIPS_PROBE_CACHE" 2>/dev/null || echo 0) ))
        if [ "$age" -ge 0 ] && [ "$age" -lt "$EDGEIPS_PROBE_TTL" ]; then
            cat "$EDGEIPS_PROBE_CACHE"; return 0
        fi
    fi

    local dom ip tmp
    dom="$(config_get domain 2>/dev/null || echo "")"
    tmp="$(mktemp -d)" || return 1

    # Проверяем ПАРАЛЛЕЛЬНО. Последовательно один недоступный адрес держал
    # шапку восемь секунд, три — двадцать четыре.
    while read -r ip; do
        [ -n "$ip" ] || continue
        if [ -z "$dom" ]; then
            printf '%s\tno-domain\n' "$ip" > "$tmp/$ip" &
        else
            (
                code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time "$EDGEIPS_PROBE_TIMEOUT" \
                        --resolve "${dom}:443:${ip}" "https://${dom}/" 2>/dev/null)"
                case "$code" in
                    200|30[1278]) printf '%s\tok\n'   "$ip" ;;
                    000|"")       printf '%s\tdown\n' "$ip" ;;
                    *)            printf '%s\thttp-%s\n' "$ip" "$code" ;;
                esac
            ) > "$tmp/$ip" &
        fi
    done <<< "$(edgeips_list)"
    wait

    mkdir -p "$(dirname "$EDGEIPS_PROBE_CACHE")" 2>/dev/null
    cat "$tmp"/* 2>/dev/null | sort | tee "$EDGEIPS_PROBE_CACHE" 2>/dev/null
    rm -rf "$tmp"
    return 0
}

# ── Доступ по SSH на всех адресах ────────────────────────────────────────────
#
# Смысл нескольких адресов — пережить блокировку одного. Для клиентов это уже
# работает: telemt слушает 0.0.0.0, а домен раздаётся несколькими A-записями.
# Но админ, у которого заблокировали главный адрес, теряет и SSH — если sshd
# привязан к одному адресу или дополнительный адрес не поднялся. Тогда чинить
# блокировку приходится через консоль хостера, а её под рукой может не быть.
#
# Проверяем две вещи, каждая из которых ломает вход по запасному адресу:
#   1. sshd слушает на всех адресах, а не на одном;
#   2. все адреса из конфигурации сети действительно подняты.
#
# Главное правило здесь — МОЛЧАТЬ, когда не уверены. Предупреждение, которое
# загорается на исправной машине, обесценивает себя: его перестают читать
# ровно к тому моменту, когда оно становится настоящим. Поэтому каждая
# проверка, не сумев собрать данные, отключается, а не кричит.
EDGEIPS_SSH_STAMP="${EDGEIPS_SSH_STAMP:-/run/gotelegram/edgeips.ssh}"
EDGEIPS_SSH_TRIES="${EDGEIPS_SSH_TRIES:-/run/gotelegram/edgeips.ssh.tries}"
EDGEIPS_SSH_INTERVAL="${EDGEIPS_SSH_INTERVAL:-300}"
# Сколько раз подряд возвращать один и тот же адрес, прежде чем отступить.
# Адрес, который хостер забрал себе, остаётся в конфигурации сети, и без
# предела сторож поднимал бы чужой адрес каждые пять минут вечно — на L2-сети
# это конфликт с новым владельцем.
EDGEIPS_SSH_MAX_FIX="${EDGEIPS_SSH_MAX_FIX:-3}"

# Порты, на которых слушает SSH. Опознавать слушателя по имени процесса нельзя:
# в Ubuntu 24.04 сокет держит systemd (socket activation) и до первого входа
# процесса sshd вообще нет, на части машин стоит dropbear. Порт же known из
# конфигурации, а порт сессии — из SSH_CONNECTION.
edgeips_ssh_ports() {
    if type _mf_ssh_ports >/dev/null 2>&1; then _mf_ssh_ports; return 0; fi
    local p=""
    command -v sshd >/dev/null 2>&1 && p="$(timeout 3 sshd -T 2>/dev/null | awk '/^port /{print $2; exit}')"
    [ -n "$p" ] || p="$(awk '/^[[:space:]]*Port[[:space:]]+[0-9]+/{print $2; exit}' /etc/ssh/sshd_config 2>/dev/null)"
    [ -n "$p" ] || p="$(printf '%s' "${SSH_CONNECTION:-}" | awk '{print $4}')"
    [[ "$p" =~ ^[0-9]+$ ]] || p=22
    printf '%s\n' "$p"
}

# На чём SSH слушает ПРЯМО СЕЙЧАС: "all" либо список адресов. Пустой ответ и
# код 1 — определить не удалось; вызывающий обязан промолчать, а не решить,
# что доступа нет.
edgeips_ssh_bind() {
    local ports p lines a hostpart addrs="" any=0
    command -v ss >/dev/null 2>&1 || return 1
    ports="$(edgeips_ssh_ports 2>/dev/null | tr '\n' ' ')"
    [ -n "${ports// /}" ] || return 1
    for p in $ports; do
        lines="$(ss -ltnH "( sport = :$p )" 2>/dev/null | awk '{print $4}')"
        [ -n "$lines" ] || continue
        any=1
        while read -r a; do
            [ -n "$a" ] || continue
            hostpart="${a%:*}"
            hostpart="${hostpart#[}"; hostpart="${hostpart%]}"
            case "$hostpart" in
                '0.0.0.0'|'*'|'::') addrs="all" ;;
                *) [ "$addrs" = "all" ] || addrs="$addrs $hostpart" ;;
            esac
        done <<< "$lines"
    done
    [ "$any" = "1" ] || return 1
    printf '%s\n' "${addrs# }"
}

# Адреса из /etc/network/interfaces в виде "<ip> <префикс> <имя iface>".
# Только этот формат однозначен настолько, чтобы по нему можно было ПОДНЯТЬ
# адрес: там же лежит имя алиаса, без которого ifupdown теряет управление им.
edgeips_ssh_ifupdown() {
    local f
    for f in /etc/network/interfaces /etc/network/interfaces.d/*; do
        [ -f "$f" ] || continue
        awk '
            /^[[:space:]]*iface[[:space:]]+/ { ifc = $2; fam = $3; next }
            /^[[:space:]]*address[[:space:]]+/ {
                if (fam != "inet") next
                addr = $2; pfx = 32
                if (index(addr, "/")) { split(addr, a, "/"); addr = a[1]; pfx = a[2] }
                if (addr ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) print addr, pfx, ifc
            }
        ' "$f" 2>/dev/null
    done
}

# Глобальные IPv4 из конфигурации сети — то, что машина обязана поднять сама
# после перезагрузки. Пустой ответ означает «источников не нашлось» (DHCP,
# cloud-init, NetworkManager с другим форматом), и тогда о постоянстве адресов
# мы просто не судим.
edgeips_ssh_configured() {
    local f
    {
        edgeips_ssh_ifupdown | awk '{print $1}'
        # netplan: адреса живут под ключом addresses: и списком через дефис, и
        # записью в строку [a/32, b/24]. Берём только их — не gateway, не DNS и
        # не allowed-ips вроде 0.0.0.0/0 из блока wireguard, которые иначе
        # выглядели бы как адреса машины.
        for f in /etc/netplan/*.yaml /etc/netplan/*.yml; do
            [ -f "$f" ] || continue
            awk '
                /^[[:space:]]*addresses:/ {
                    inaddr = 1
                    line = $0
                    while (match(line, /[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\/[0-9]+/)) {
                        print substr(line, RSTART, RLENGTH)
                        line = substr(line, RSTART + RLENGTH)
                        inaddr = 0
                    }
                    next
                }
                inaddr && /^[[:space:]]*-[[:space:]]*[0-9]/ {
                    if (match($0, /[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\/[0-9]+/)) print substr($0, RSTART, RLENGTH)
                    next
                }
                inaddr && /^[[:space:]]*[A-Za-z]/ { inaddr = 0 }
            ' "$f" 2>/dev/null
        done
        for f in /etc/systemd/network/*.network; do
            [ -f "$f" ] || continue
            sed -nE 's|^[[:space:]]*Address=([0-9]{1,3}(\.[0-9]{1,3}){3}).*|\1|p' "$f" 2>/dev/null
        done
        for f in /etc/NetworkManager/system-connections/*; do
            [ -f "$f" ] || continue
            sed -nE 's/^[[:space:]]*address[0-9]*=([0-9]{1,3}(\.[0-9]{1,3}){3}).*/\1/p' "$f" 2>/dev/null
        done
    } 2>/dev/null \
        | sed 's|/.*||' \
        | grep -E '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' \
        | grep -vE '^(0\.|127\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|169\.254\.|255\.)' \
        | sort -u
}

# Строки "<ip>\t<состояние>": ok | missing (прописан, но не поднят) |
# no-ssh (sshd не слушает этот адрес) | not-persisted (после перезагрузки
# исчезнет). Проверка, для которой не хватило данных, молчит: адрес остаётся
# «ok», а не получает выдуманный диагноз.
edgeips_ssh_audit() {
    local bound live conf ip st have_bind=1 have_conf=1
    bound="$(edgeips_ssh_bind 2>/dev/null)" || have_bind=0
    [ -n "$bound" ] || have_bind=0
    live="$(edgeips_list 2>/dev/null)"
    conf="$(edgeips_ssh_configured 2>/dev/null)"
    [ -n "$conf" ] || have_conf=0
    while read -r ip; do
        [ -n "$ip" ] || continue
        st="ok"
        if ! printf '%s\n' "$live" | grep -qxF "$ip"; then
            st="missing"
        elif [ "$have_bind" = "1" ] && [ "$bound" != "all" ] \
             && ! printf '%s\n' "$bound" | tr ' ' '\n' | grep -qxF "$ip"; then
            st="no-ssh"
        elif [ "$have_conf" = "1" ] && ! printf '%s\n' "$conf" | grep -qxF "$ip"; then
            st="not-persisted"
        fi
        printf '%s\t%s\n' "$ip" "$st"
    done <<< "$(printf '%s\n%s\n' "$live" "$conf" | grep -E '^[0-9]' | sort -u)"
}

# Текст предупреждения по готовому аудиту. Отдельная функция, чтобы разные
# читатели (шапка, бот, панель, JSON) описывали ОДНО и то же состояние: раньше
# warning брался из кэша, а список адресов рядом — из свежего опроса, и ответ
# противоречил сам себе.
_edgeips_ssh_warn_text() {
    local a="${1:-}" ip st miss="" nossh="" nopers="" out=""
    [ -n "$a" ] || return 0
    while IFS=$'\t' read -r ip st; do
        case "$st" in
            missing)       miss="$miss $ip" ;;
            no-ssh)        nossh="$nossh $ip" ;;
            not-persisted) nopers="$nopers $ip" ;;
        esac
    done <<< "$a"
    # Через if, а не `[ ... ] && ...`: последний возвращает 1 на ложном условии,
    # и под set -e у вызывающего функция молча обрывалась бы на первой же
    # пустой категории — предупреждение терялось бы целиком.
    if [ -n "$nossh" ]; then
        out="${out}SSH недоступен по адресам:${nossh} — при блокировке главного адреса вход будет только через консоль хостера. Проверьте ListenAddress в /etc/ssh/sshd_config."$'\n'
    fi
    if [ -n "$miss" ]; then
        out="${out}Адреса прописаны в конфигурации сети, но не подняты:${miss}."$'\n'
    fi
    if [ -n "$nopers" ]; then
        out="${out}Адреса подняты вручную и исчезнут после перезагрузки:${nopers} — закрепите их в конфигурации сети."$'\n'
    fi
    printf '%s' "$out"
}

# Предупреждение для человека. Пусто — значит запасной вход в порядке и писать
# не о чем: постоянная зелёная строка про SSH в шапке только отвлекает.
# Считаем каждый раз заново: полный проход занимает около десятой доли секунды,
# а кэш со сроком годности уже однажды показывал починенную проблему как живую
# и — хуже — замирал навсегда, если человек выключал сбор статистики.
edgeips_ssh_warn() {
    _edgeips_ssh_warn_text "$(edgeips_ssh_audit 2>/dev/null)"
}

# Сторож. Дёргается из общего цикла статистики, поэтому сам следит за своим
# интервалом — как edgeips_sample. Поднимает обратно только адреса из
# /etc/network/interfaces, где известны префикс и имя алиаса; всё остальное
# оставляет человеку.
edgeips_ssh_guard() {
    local force="${1:-0}" now last audit ip st spec pfx label dev tries brdv
    now="$(date +%s)"
    if [ "$force" != "1" ] && [ -r "$EDGEIPS_SSH_STAMP" ]; then
        last="$(cat "$EDGEIPS_SSH_STAMP" 2>/dev/null || echo 0)"
        [[ "$last" =~ ^[0-9]+$ ]] || last=0
        [ $(( now - last )) -lt "$EDGEIPS_SSH_INTERVAL" ] && return 0
    fi
    mkdir -p "$EDGEIPS_STATE_DIR" 2>/dev/null
    printf '%s' "$now" > "$EDGEIPS_SSH_STAMP" 2>/dev/null

    audit="$(edgeips_ssh_audit 2>/dev/null)"
    local fixed=0
    while IFS=$'\t' read -r ip st; do
        [ "$st" = "missing" ] || continue
        # Форма записи нужна целиком: голое `ip addr add` без label оставляет
        # алиас, которым ifupdown больше не управляет — ifdown/ifup по нему
        # начинают падать, и человек об этом не узнает.
        spec="$(edgeips_ssh_ifupdown | awk -v a="$ip" '$1 == a { print; exit }')"
        [ -n "$spec" ] || continue
        set -- $spec; pfx="${2:-32}"; label="${3:-}"
        [ -n "$label" ] || continue
        dev="${label%%:*}"
        # grep -c печатает «0» и возвращает 1 при нуле совпадений — с `|| echo 0`
        # выходило «0\n0», арифметика падала и сторож молча переставал чинить
        tries="$(grep -c "^${ip}$" "$EDGEIPS_SSH_TRIES" 2>/dev/null)"; tries="${tries:-0}"
        [ "$tries" -lt "$EDGEIPS_SSH_MAX_FIX" ] || continue
        # Для /32 `brd +` ядро оставляет адрес вовсе без широковещательного, а
        # ifup выставляет его равным самому адресу. Разойтись с ifup здесь
        # нельзя: адрес должен вернуться ровно в той форме, в какой его завела
        # бы система сама.
        if [ "$pfx" = "32" ]; then brdv="$ip"; else brdv="+"; fi
        ip -4 addr add "${ip}/${pfx}" brd "$brdv" dev "$dev" label "$label" 2>/dev/null || continue
        # `ip addr add` отвечает успехом и там, где адрес не появился (например,
        # 0.0.0.0), поэтому верим только фактической проверке.
        if edgeips_list 2>/dev/null | grep -qxF "$ip"; then
            fixed=$(( fixed + 1 ))
            printf '%s\n' "$ip" >> "$EDGEIPS_SSH_TRIES" 2>/dev/null
            logger -t gotelegram-edgeips "адрес $ip отсутствовал на $dev и поднят обратно как $label" 2>/dev/null || true
        fi
    done <<< "$audit"

    # Вернувшийся адрес должен сразу попасть в учёт трафика, иначе до следующей
    # проверки правил он числился бы «без трафика» и выглядел заблокированным.
    if [ "$fixed" -gt 0 ]; then edgeips_ensure_rules >/dev/null 2>&1 || true; fi
    return 0
}

# Проверить немедленно и доложить. Вызывается там, где человек стоит у экрана и
# может сразу починить: после установки и после обновления.
edgeips_ssh_notify() {
    local w l
    edgeips_ssh_guard 1 >/dev/null 2>&1 || true
    w="$(edgeips_ssh_warn 2>/dev/null)"
    [ -n "$w" ] || return 0
    while IFS= read -r l; do
        if [ -n "$l" ]; then
            if type log_warning >/dev/null 2>&1; then log_warning "$l"; else echo "  ⚠ $l" >&2; fi
        fi
    done <<< "$w"
    return 0
}

# Компактная сводка для шапки: по строке на адрес.
edgeips_brief() {
    local w probe ip m15 h1 d1 conn peak avg st dom
    w="$(edgeips_windows 2>/dev/null)"
    [ -z "$w" ] || [ "$w" = "no-data" ] && { echo "  адреса: сбор ещё не начался"; return 0; }
    probe="$(edgeips_probe 2>/dev/null)"
    local h1c="адрес" h2c="15м" h3c="1ч" h4c="1д" h5c="сейчас" h6c="пик"
    _edgeips_pad h1c 18; _edgeips_pad h2c 11; _edgeips_pad h3c 11; _edgeips_pad h4c 11; _edgeips_pad h5c 8; _edgeips_pad h6c 6
    echo "  ${h1c}${h2c}${h3c}${h4c}${h5c}${h6c}связь"
    while IFS=$'\t' read -r ip m15 h1 d1 conn peak avg; do
        [ -n "$ip" ] || continue
        st="$(printf '%s\n' "$probe" | awk -F'\t' -v a="$ip" '$1==a{print $2}')"
        local c1="$ip" c2 c3 c4 c5 c6
        c2="$(_edgeips_hum "$m15")"; c3="$(_edgeips_hum "$h1")"; c4="$(_edgeips_hum "$d1")"
        c5="$conn"; c6="$peak"
        _edgeips_pad c1 18; _edgeips_pad c2 11; _edgeips_pad c3 11; _edgeips_pad c4 11; _edgeips_pad c5 8; _edgeips_pad c6 6
        case "$st" in
            ok)        st="✓" ;;
            down)      st="✗ не отвечает" ;;
            no-domain) st="— домена нет" ;;
            *)         st="⚠ ${st}" ;;
        esac
        echo "  ${c1}${c2}${c3}${c4}${c5}${c6}${st}"
    done <<< "$w"
}

edgeips_status() {
    local rows; rows="$(edgeips_report 2>/dev/null)"
    if [ -z "$rows" ] || [ "$rows" = "no-data" ]; then
        echo "  данных пока нет — сбор идёт, первая картина через пару минут"
        return 0
    fi
    # printf в bash считает БАЙТЫ, а не символы, поэтому кириллица в заголовке
    # разъезжается с латинскими адресами в строках. Дополняем вручную по длине
    # в символах — тем же приёмом, что и остальная статистика (_stats_pad_var).
    local h1="адрес" h2="за интервал" h3="всего"
    _edgeips_pad h1 18; _edgeips_pad h2 13; _edgeips_pad h3 13
    echo "  ${h1}${h2}${h3}состояние"
    local ip recent total verdict c1 c2 c3 mark
    while IFS=$'\t' read -r ip recent total verdict; do
        [ -n "$ip" ] || continue
        c1="$ip"; c2="$(_edgeips_hum "$recent")"; c3="$(_edgeips_hum "$total")"
        _edgeips_pad c1 18; _edgeips_pad c2 13; _edgeips_pad c3 13
        case "$verdict" in
            dark)      mark="⚠ тишина ${EDGEIPS_DARK_STRIKES} мин, у других трафик идёт — проверьте" ;;
            unused)    mark="· не используется — нет A-записи в DNS?" ;;
            quiet-all) mark="· тихо, но тихо и у остальных" ;;
            *)         mark="✓" ;;
        esac
        echo "  ${c1}${c2}${c3}${mark}"
    done <<< "$rows"
}

_edgeips_pad() {
    local __n="$1" __w="$2" __v="${!1}" __g
    __g=$(( __w - ${#__v} ))
    (( __g > 0 )) && printf -v "$__n" '%s%*s' "$__v" "$__g" ""
    return 0
}

_edgeips_hum() {
    local b="${1:-0}"
    if   [ "$b" -ge 1073741824 ] 2>/dev/null; then awk -v v="$b" 'BEGIN{printf "%.1f ГБ", v/1073741824}'
    elif [ "$b" -ge 1048576 ]    2>/dev/null; then awk -v v="$b" 'BEGIN{printf "%.1f МБ", v/1048576}'
    elif [ "$b" -ge 1024 ]       2>/dev/null; then awk -v v="$b" 'BEGIN{printf "%.1f КБ", v/1024}'
    else echo "${b} Б"; fi
}

# Полная сводка для бота и панели: окна, подключения, доступность.
# Собираем ОДНИМ вызовом: бот и панель дёргают её на каждой отрисовке, а
# отдельные вызовы за окнами и за пробой означали бы два прохода по истории и
# две серии curl.
# Экранирование для строк, попадающих в JSON. Домен и адреса приходят из
# конфига и из истории — то есть из файлов, которые человек может править.
_edgeips_json_escape() {
    printf '%s' "${1:-}" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\000-\037'
}

# Список адресов (по строке или через пробел) → JSON-массив строк.
_edgeips_json_list() {
    local a out="" f=1
    for a in ${1:-}; do
        [ "$f" = 1 ] || out="${out},"
        f=0; out="${out}\"$(_edgeips_json_escape "$a")\""
    done
    printf '[%s]' "$out"
}

edgeips_status_json() {
    local w probe ip m15 h1 d1 conn peak avg st first=1 n=0
    w="$(edgeips_windows 2>/dev/null)"
    probe="$(edgeips_probe 2>/dev/null)"
    # DNS: какие адреса сервера не указаны в A-записях домена и имени WEB.
    # Не сумели проверить (нет dig, DNS молчит, нет домена) — checked=false, и
    # по адресам ставим true: тревожить без данных нельзя.
    local dom web dmiss="" wmiss="" drc=9 wrc=9 dok=0 wok=0 checked=false dd dw
    dom="$(config_get domain 2>/dev/null || echo "")"; [ "$dom" = "null" ] && dom=""
    web="$(config_get web_host 2>/dev/null || echo "")"; [ "$web" = "null" ] && web=""
    if [ -n "$dom" ]; then
        dmiss="$(edgeips_dns_missing "$dom")"; drc=$?
        case "$drc" in 0|2) dok=1 ;; *) dmiss="" ;; esac
    fi
    if [ -n "$web" ]; then
        wmiss="$(edgeips_dns_missing "$web")"; wrc=$?
        case "$wrc" in 0|2) wok=1 ;; *) wmiss="" ;; esac
    fi
    [ "$dok" = 1 ] && { [ -z "$web" ] || [ "$wok" = 1 ]; } && checked=true
    printf '{"status":"ok","ips":['
    if [ -n "$w" ] && [ "$w" != "no-data" ]; then
        while IFS=$'\t' read -r ip m15 h1 d1 conn peak avg; do
            [ -n "$ip" ] || continue
            st="$(printf '%s\n' "$probe" | awk -F'\t' -v a="$ip" '$1==a{print $2}')"
            [ "$first" = "1" ] || printf ','
            first=0; n=$((n+1))
            dd=true
            case $'\n'"$dmiss"$'\n' in *$'\n'"$ip"$'\n'*) dd=false ;; esac
            dw=null
            if [ -n "$web" ]; then
                dw=true
                case $'\n'"$wmiss"$'\n' in *$'\n'"$ip"$'\n'*) dw=false ;; esac
            fi
            printf '{"ip":"%s","b15m":%s,"b1h":%s,"b1d":%s,"conns":%s,"peak":%s,"reach":"%s","dns_domain":%s,"dns_web":%s}' \
                   "$(_edgeips_json_escape "$ip")" "${m15:-0}" "${h1:-0}" "${d1:-0}" \
                   "${conn:-0}" "${peak:-0}" "${st:-unknown}" "$dd" "$dw"
        done <<< "$w"
    fi
    # Домен приходит из конфига, который человек мог править руками: кавычка в
    # нём рвала весь ответ, а его разбирают и бот, и панель.
    printf '],"count":%s,"domain":"%s"' "$n" "$(_edgeips_json_escape "$(config_get domain 2>/dev/null || echo "")")"
    printf ',"web_host":"%s","dns":{"checked":%s,"domain_missing":%s,"web_missing":%s}' \
           "$(_edgeips_json_escape "$web")" "$checked" \
           "$(_edgeips_json_list "$dmiss")" "$(_edgeips_json_list "$wmiss")"

    # Состояние запасного входа. Аудит считаем ОДИН раз и из него же берём
    # текст: раньше warning приезжал из кэша, а список адресов рядом — из
    # свежего опроса, и ответ противоречил сам себе до пяти минут.
    local audit warn aip ast afirst=1
    audit="$(edgeips_ssh_audit 2>/dev/null)"
    warn="$(_edgeips_ssh_warn_text "$audit" | tr '\n' ' ' | sed 's/  */ /g; s/ $//')"
    printf ',"ssh":{"ok":%s,"warning":"%s","addrs":[' \
           "$([ -z "$warn" ] && echo true || echo false)" \
           "$(_edgeips_json_escape "$warn")"
    while IFS=$'\t' read -r aip ast; do
        [ -n "$aip" ] || continue
        [ "$afirst" = "1" ] || printf ','
        afirst=0
        printf '{"ip":"%s","state":"%s"}' "$(_edgeips_json_escape "$aip")" "$ast"
    done <<< "$audit"
    printf ']}}\n'
}

# ── DNS: указывает ли имя на ВСЕ адреса сервера ──────────────────────────────
# Адрес, которого нет в A-записях домена, клиенты по ссылке на домен не увидят
# никогда: резолвер их туда не отправит. Иногда так и задумано (адрес оставлен
# запасным для SSH), но чаще это забытая запись после покупки второго IP.
# Проверяем только там, где человек смотрит: шапка SSH, меню WEB, бот и панель,
# смена имени WEB. В сборщик (каждый тик) DNS не ходит.

# Адреса сервера для сверки с DNS: глобальные IPv4 интерфейсов. За NAT (на
# интерфейсе приватный адрес) — публичный; его можно передать первым
# аргументом, чтобы не спрашивать внешний сервис второй раз.
edgeips_server_ips() {
    local l; l="$(edgeips_list 2>/dev/null)"
    if [ -z "$l" ]; then
        l="${1:-}"
        if [ -z "$l" ] && type get_server_ip >/dev/null 2>&1; then l="$(get_server_ip 2>/dev/null)"; fi
        # get_server_ip при отказе печатает 0.0.0.0, а шапка меню подставляет
        # «N/A» — в список идут только настоящие адреса
        l="$(printf '%s\n' "$l" | grep -E '^[0-9]+(\.[0-9]+){3}$' | grep -vx '0\.0\.0\.0' || true)"
    fi
    if [ -n "$l" ]; then printf '%s\n' "$l"; fi
    return 0
}

# Адреса сервера, которых нет в A-записях имени, по одному в строке.
# Коды: 0 — проверено (пусто — покрыты все); 2 — A-записей нет (печатает все
# адреса сервера: ни один не указан); 3 — нет dig; 4 — DNS не ответил.
# Второй аргумент — уже известный публичный IP (см. edgeips_server_ips).
edgeips_dns_missing() {
    local name="$1" recs drc ip
    command -v dig >/dev/null 2>&1 || return 3
    [ -n "$name" ] || return 2
    recs="$(dig +short +time=2 +tries=1 A "$name" 2>/dev/null)"; drc=$?
    [ "$drc" = 0 ] || return 4
    # +short печатает и звенья CNAME — оставляем только адреса. Без конвейера
    # с grep -q: под pipefail он даёт ложный отказ (SIGPIPE у printf).
    recs="$(printf '%s\n' "$recs" | grep -E '^[0-9]+(\.[0-9]+){3}$' || true)"
    for ip in $(edgeips_server_ips "${2:-}"); do
        case $'\n'"$recs"$'\n' in
            *$'\n'"$ip"$'\n'*) ;;
            *) printf '%s\n' "$ip" ;;
        esac
    done
    [ -n "$recs" ] || return 2
    return 0
}

# Строка предупреждения для SSH по списку недостающих адресов (через пробел или
# перевод строки). Пустой список — пусто.
edgeips_dns_warn_text() {
    local name="$1" miss list n
    miss="$(printf '%s\n' "${2:-}" | tr ' ' '\n' | grep -E '^[0-9.]+$' || true)"
    [ -n "$miss" ] || return 0
    n="$(printf '%s\n' "$miss" | grep -c . || true)"
    list="$(printf '%s\n' "$miss" | paste -sd ',' - | sed 's/,/, /g')"
    # через $(…): так вызов видит статическая сверка словарей
    if [ "${n:-0}" -gt 1 ]; then
        echo "$(tf dns_ips_missing_many "$list" "$name" "$name")"
    else
        echo "$(tf dns_ip_missing_one "$list" "$name" "$name")"
    fi
}

# Проверить имя и напечатать предупреждение (или ничего). Не сумели проверить
# (нет dig, DNS молчит) — молчим: ложная тревога хуже её отсутствия.
edgeips_dns_warn() {
    local name="$1" miss rc
    [ -n "$name" ] || return 0
    miss="$(edgeips_dns_missing "$name" "${2:-}")"; rc=$?
    case "$rc" in 0|2) edgeips_dns_warn_text "$name" "$miss" ;; esac
    return 0
}

export -f edgeips_server_ips edgeips_dns_missing edgeips_dns_warn_text edgeips_dns_warn
export -f edgeips_list edgeips_ensure_rules edgeips_counters edgeips_sample
export -f edgeips_report edgeips_status edgeips_status_json _edgeips_pad _edgeips_hum
export -f _edgeips_json_escape _edgeips_json_list
export -f edgeips_conns edgeips_windows edgeips_probe edgeips_brief
export -f edgeips_ssh_bind edgeips_ssh_configured edgeips_ssh_audit edgeips_ssh_ports
export -f edgeips_ssh_warn _edgeips_ssh_warn_text edgeips_ssh_guard edgeips_ssh_notify
export -f edgeips_ssh_ifupdown
