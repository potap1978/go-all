#!/usr/bin/env bash
# lib/mekofix_v4.sh — MEKO fix V4 «Zapret2 MTProto fix by CHKRON» (ЭКСПЕРИМЕНТАЛЬНО)
#
# Идея и первоисточник: @Mekotofeuka — MTPROTO_FIX_By_MEKO/data/zapret2_fix.sh,
# механика V4 — CHKRON / Liafanx (MTproxy-reanimation). Здесь — самостоятельная
# реализация под неймспейс goTelegram Pro с усиленной безопасностью.
#
# ЗАЧЕМ. С 04.06 клиент Telegram зависает на TCP-рукопожатии с MTProto-прокси
# (потом блок сети на ~2 мин). V2/V3 (lib/mekofix.sh) лечат это SYN-лимитом.
# V4 — принципиально другой метод: активное вмешательство в само рукопожатие
# через nfqws2 (zapret2) + Lua-скрипт.
#
# КАК РАБОТАЕТ (что делает Lua-функция lets_resend):
#   1) SYN с отпечатком iOS (th_win=65535 + 8 TCP-опций 2,1,3,1,1,8,4,0)
#      -> пропускаем без вмешательства (fwmark + rawsend + DROP оригинала);
#   2) SYN+ACK сервера -> запоминаем ack0 и зажимаем TCP window до ~1400,
#      чтобы клиент был вынужден дробить свой ClientHello;
#   3) пустые ACK сервера -> window ~10; как только клиент прислал >= win_synack
#      байт — инстанс отсекается и поток идёт дальше без вмешательства;
#   4) первый data-пакет клиента -> режем на 3 части по split_len; отправляем
#      1-ю и 3-ю, затем 2-ю с испорченной контрольной суммой (badsum).
#      Клиент ретрансмитит битую часть — DPI видит рваный ClientHello,
#      соединение устанавливается штатно.
#
# СТАТУС: ЭКСПЕРИМЕНТАЛЬНО. У автора V4 «на этапе тестов», рекомендуемый — V3.
# Метод не универсален: зависит от wscale ядра (см. _v4_wscale_warn).
#
# ПУБЛИЧНЫЙ API:
#   v4_is_installed                                  -> 0/1
#   v4_is_active                                     -> 0/1
#   v4_status                                        -> человекочитаемый блок
#   v4_status_json                                   -> валидный JSON одной строкой
#   v4_install <порт>                                -> установка + запуск + верификация
#   v4_uninstall                                     -> полный откат (всегда 0)
#   v4_params_set <split_len> <win_synack> <win_ack> -> смена параметров + рестарт
#   v4_start | v4_stop | v4_restart                  -> управление службой
#   v4_show_config                                   -> показать conf/lua/юнит/правила
#   v4_selftest                                      -> проверка окружения без установки
#
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (необязательные):
#   V4_DRY_RUN=1        — ничего не ставить и не трогать ядро; только сгенерировать
#                         файлы в V4_ROOT и показать, что было бы применено.
#   V4_ROOT=/tmp/v4test — переопределить корень установки (для dry-run/тестов).
#   V4_FORCE=1          — не блокировать установку при найденном ЧУЖОМ zapret/nfqws.
#   V4_NO_CONFIRM=1     — не спрашивать подтверждение (вызов из меню/бота).
#   V4_ALLOW_UNVERIFIED=1 — разрешить установку без сверки контрольной суммы.
#   V4_BACKEND=nft|iptables — принудительный бэкенд правил.
#   V4_DEBUG=1          — включить --debug лог nfqws2 в V4_LOG.
#
# ГАРАНТИИ БЕЗОПАСНОСТИ:
#   * все правила матчатся ТОЛЬКО по TCP-порту прокси; SSH-порт проверяется
#     отдельно и установка на него запрещена;
#   * своя nft-таблица / свои iptables-цепочки в неймспейсе gotelegram-mekofix;
#     чужие цепочки не флашатся, политики INPUT/FORWARD/OUTPUT не меняются;
#   * внутри своих iptables-цепочек — только RETURN (никогда ACCEPT), чтобы не
#     обрывать прохождение правил соседних программ;
#   * NFQUEUE всегда с bypass — если nfqws2 умер, трафик идёт как есть (fail-open);
#   * никаких exit — только return (модуль сорсится в интерактивное меню).

# ── Версия модуля ────────────────────────────────────────────────────────────
V4_MODULE_VERSION="1.0"

# ── Настройки по умолчанию (как в оригинале MEKO) ────────────────────────────
V4_SPLIT_LEN="${V4_SPLIT_LEN:-400}"        # размер каждой части при разрезке ClientHello
V4_WIN_SYNACK="${V4_WIN_SYNACK:-1400}"     # TCP window в SYN+ACK
V4_WIN_ACK="${V4_WIN_ACK:-10}"             # TCP window в пустых ACK сервера
V4_OUT_RANGE="${V4_OUT_RANGE:-a}"          # внутрипрофильный фильтр nfqws2 (исходящие)
V4_IN_RANGE="${V4_IN_RANGE:-a}"            # внутрипрофильный фильтр nfqws2 (входящие)
V4_QNUM_PREF="${V4_QNUM_PREF:-201}"        # желаемый номер NFQUEUE
V4_QNUM_MIN="${V4_QNUM_MIN:-201}"          # диапазон поиска свободной очереди
V4_QNUM_MAX="${V4_QNUM_MAX:-299}"
V4_FWMARK="${V4_FWMARK:-0x40000000}"       # бит анти-зацикливания nfqws2 (его дефолт)
V4_CTMARK="${V4_CTMARK:-0x00040000}"       # ct mark «пропустить без обработки»
V4_MARK_COMBINED="${V4_MARK_COMBINED:-0x40040000}"  # FWMARK|CTMARK — метка обхода
V4_IOS_FWMARK="${V4_IOS_FWMARK:-0x40000}"  # fwmark, который Lua ставит iOS-пакетам
V4_DEBUG="${V4_DEBUG:-0}"

# ── Пин версии zapret2 + контрольные суммы бинаря nfqws2 ─────────────────────
# Тянуть «латест» вслепую нельзя: релиз может поменять поведение или быть подменён.
V4_ZAPRET_REPO="${V4_ZAPRET_REPO:-bol-van/zapret2}"
V4_ZAPRET_VER="${V4_ZAPRET_VER:-v1.0.3}"   # проверенная версия (её же использует MEKO)

# sha256 бинаря <версия>:<арх-тег>. Источник — sha256sum.txt соответствующего релиза.
_v4_pinned_sha() {
    case "${1:-}:${2:-}" in
        v1.0.3:linux-x86_64) echo "abdaa47a5edd2a2aef6401bd5824b5356643c6db45055c251ee31682fbb60a3d" ;;
        v1.0.3:linux-arm64)  echo "27cde1e9568abb04640c1550b7f771d36de73f4305aa76a2628162abb5434105" ;;
        v1.0.4:linux-x86_64) echo "58637c7b9d4bcd2dbd34e83244370df553215bc8cf370f738ac5d53e5c154b60" ;;
        v1.0.4:linux-arm64)  echo "b2827dcad28c2d3fc567cf7da34fd94832a551cbb492e8ce820ab944814a82c6" ;;
        *) echo "" ;;
    esac
}

# ── Имена служб/таблиц/цепочек — строго в нашем неймспейсе ───────────────────
V4_SERVICE="gotelegram-mekofix-v4.service"
V4_NFT_TABLE="gotelegram_mekofix_v4"       # nft: своя таблица, чужие не трогаем
V4_IPT_PRE="GT_MEKOFIX_V4_PRE"             # iptables: свои цепочки (mangle/raw)
V4_IPT_POST="GT_MEKOFIX_V4_POST"
V4_IPT_OUT="GT_MEKOFIX_V4_OUT"
V4_IPT_RAW="GT_MEKOFIX_V4_RAW"

# ── Логгеры-заглушки, если модуль сорсится отдельно от lib/common.sh ─────────
type log_info    >/dev/null 2>&1 || log_info()    { echo -e "  \033[0;36mi\033[0m $*" >&2; }
type log_success >/dev/null 2>&1 || log_success() { echo -e "  \033[0;32m+\033[0m $*" >&2; }
type log_warning >/dev/null 2>&1 || log_warning() { echo -e "  \033[1;33m!\033[0m $*" >&2; }
type log_error   >/dev/null 2>&1 || log_error()   { echo -e "  \033[0;31mx\033[0m $*" >&2; }
type log_step    >/dev/null 2>&1 || log_step()    { echo -e "\n\033[1m  $*\033[0m" >&2; }
type log_dim     >/dev/null 2>&1 || log_dim()     { echo -e "  \033[2m$*\033[0m" >&2; }

# ── Базовые утилиты ──────────────────────────────────────────────────────────
_v4_have() { command -v "${1:-}" >/dev/null 2>&1; }
_v4_dry()  { [ "${V4_DRY_RUN:-0}" = "1" ]; }
_v4_num()  { case "${1:-}" in ''|*[!0-9]*) echo "${2:-0}" ;; *) echo "$1" ;; esac; }

# Единая точка вычисления путей: вызывается в начале каждой публичной функции,
# чтобы V4_ROOT/V4_DRY_RUN можно было переопределить уже после source.
_v4_paths() {
    V4_ROOT="${V4_ROOT:-/opt/gotelegram-mekofix/v4}"
    V4_BIN_DIR="$V4_ROOT/bin"
    V4_BIN="$V4_BIN_DIR/nfqws2"
    V4_RULES="$V4_BIN_DIR/v4-rules.sh"
    V4_LUA_DIR="$V4_ROOT/lua"
    V4_LUA="$V4_LUA_DIR/mtproto.lua"
    V4_ETC_DIR="$V4_ROOT/etc"
    V4_CONF="$V4_ETC_DIR/mtproto.conf"
    V4_STATE="$V4_ETC_DIR/state.conf"
    if _v4_dry; then
        V4_UNIT_DIR="$V4_ROOT/systemd"
        V4_LOG="$V4_ROOT/log/gotelegram-mekofix-v4.log"
    else
        V4_UNIT_DIR="/etc/systemd/system"
        V4_LOG="/var/log/gotelegram-mekofix-v4.log"
    fi
    V4_UNIT="$V4_UNIT_DIR/$V4_SERVICE"
}

# Свой лог с капом 20 МБ (journald хоста не трогаем).
_v4_log() {
    _v4_paths
    local d; d=$(dirname "$V4_LOG")
    [ -d "$d" ] || mkdir -p "$d" 2>/dev/null || return 0
    local sz; sz=$(stat -c %s "$V4_LOG" 2>/dev/null || echo 0)
    if [ "$(_v4_num "$sz" 0)" -gt $((20*1024*1024)) ] 2>/dev/null; then
        tail -c $((10*1024*1024)) "$V4_LOG" > "$V4_LOG.tmp" 2>/dev/null && mv "$V4_LOG.tmp" "$V4_LOG"
    fi
    printf '%s [v4] %s\n' "$(date -Iseconds 2>/dev/null || date)" "$*" >> "$V4_LOG" 2>/dev/null || true
}

# Обёртка привилегированных команд: в dry-run только печатает.
_v4_run() {
    if _v4_dry; then
        log_dim "[dry-run] $*"
        return 0
    fi
    "$@"
}

_v4_require_root() {
    _v4_dry && return 0
    [ "$(id -u 2>/dev/null || echo 1)" = "0" ] && return 0
    log_error "Нужны права root"
    return 1
}

# ── Состояние установки (свой парсер, без source — файл не исполняем) ────────
_v4_state_save() {
    _v4_paths
    mkdir -p "$V4_ETC_DIR" 2>/dev/null || return 1
    cat > "$V4_STATE" <<EOF
# goTelegram Pro — MEKO fix V4, состояние установки. Не редактировать вручную.
V4_PORT='${V4_PORT:-}'
V4_QNUM='${V4_QNUM:-}'
V4_SPLIT_LEN='${V4_SPLIT_LEN:-}'
V4_WIN_SYNACK='${V4_WIN_SYNACK:-}'
V4_WIN_ACK='${V4_WIN_ACK:-}'
V4_BACKEND='${V4_BACKEND:-}'
V4_ZAPRET_VER='${V4_ZAPRET_VER:-}'
V4_BIN_SHA='${V4_BIN_SHA:-}'
V4_DEBUG='${V4_DEBUG:-0}'
V4_MODULE_VERSION='${V4_MODULE_VERSION:-}'
V4_INSTALLED_AT='${V4_INSTALLED_AT:-}'
EOF
    chmod 600 "$V4_STATE" 2>/dev/null || true
}

_v4_state_load() {
    _v4_paths
    [ -f "$V4_STATE" ] || return 0
    local line key val
    while IFS= read -r line; do
        case "$line" in \#*|'') continue ;; esac
        if [[ "$line" =~ ^(V4_[A-Z0-9_]+)=\'([^\']*)\'$ ]]; then
            key="${BASH_REMATCH[1]}"; val="${BASH_REMATCH[2]}"
            case "$key" in
                V4_PORT|V4_QNUM|V4_SPLIT_LEN|V4_WIN_SYNACK|V4_WIN_ACK|V4_BACKEND|\
                V4_ZAPRET_VER|V4_BIN_SHA|V4_DEBUG|V4_MODULE_VERSION|V4_INSTALLED_AT)
                    [ -n "$val" ] && printf -v "$key" '%s' "$val"
                    ;;
            esac
        fi
    done < "$V4_STATE"
    return 0
}

# ── Архитектура ──────────────────────────────────────────────────────────────
_v4_arch_tag() {
    case "$(uname -m 2>/dev/null)" in
        x86_64|amd64)   echo "linux-x86_64" ;;
        aarch64|arm64)  echo "linux-arm64" ;;
        *)              echo "" ;;
    esac
}

# ── Порты SSH: на них ставить фикс запрещено ─────────────────────────────────
_v4_ssh_ports() {
    local out=""
    out="$(sed -nE 's/^[[:space:]]*Port[[:space:]]+([0-9]+).*/\1/p' /etc/ssh/sshd_config 2>/dev/null)"
    out="$out $(cat /etc/ssh/sshd_config.d/*.conf 2>/dev/null | sed -nE 's/^[[:space:]]*Port[[:space:]]+([0-9]+).*/\1/p')"
    out="$out $(ss -tlnH 2>/dev/null | grep -E 'sshd|"ssh' | awk '{n=split($4,a,":"); print a[n]}')"
    # порт текущей SSH-сессии — самый достоверный источник
    [ -n "${SSH_CONNECTION:-}" ] && out="$out $(awk '{print $4}' <<< "${SSH_CONNECTION}")"
    # shellcheck disable=SC2086  # словоделение здесь намеренное
    out="$(printf '%s\n' $out | grep -E '^[0-9]+$' | sort -un | tr '\n' ' ')"
    [ -z "${out// /}" ] && out="22"
    echo "$out"
}

_v4_port_is_ssh() {
    local p="${1:-}" q
    for q in $(_v4_ssh_ports); do [ "$p" = "$q" ] && return 0; done
    return 1
}

# ── Обнаружение ЧУЖОГО zapret/nfqws — ломать соседей нельзя ──────────────────
# Печатает найденное в stdout, возвращает 0 если чужое найдено.
_v4_foreign_found() {
    _v4_paths
    local found=0 u d
    for d in /opt/zapret2 /opt/zapret /opt/mtpr-simple/zapret2 /etc/zapret2 /etc/zapret; do
        [ -d "$d" ] || continue
        case "$d" in "$V4_ROOT"*) continue ;; esac
        echo "  каталог: $d"; found=1
    done
    if _v4_have systemctl; then
        for u in zapret zapret2 nfqws nfqws2 mtpr-zapret2; do
            if systemctl list-unit-files 2>/dev/null | grep -qE "^${u}(@[^[:space:]]*)?\.service"; then
                echo "  systemd-юнит: ${u}.service"; found=1
            fi
        done
    fi
    if _v4_have nft; then
        local t
        t=$(nft list tables 2>/dev/null | grep -iE 'table (ip|inet|ip6) (MTProto|zapret[0-9]*|ztest)$' || true)
        [ -n "$t" ] && { echo "  nft-таблицы: $(echo "$t" | tr '\n' ';')"; found=1; }
    fi
    # чужие процессы nfqws/nfqws2 (наш исключаем по пути бинаря)
    local pids p cmd
    pids=$( { pgrep -x nfqws2 2>/dev/null; pgrep -x nfqws 2>/dev/null; } || true )
    for p in $pids; do
        cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null || true)
        case "$cmd" in *"$V4_BIN"*) continue ;; esac
        [ -n "$cmd" ] && { echo "  процесс PID $p: $cmd"; found=1; }
    done
    [ "$found" = "1" ] && return 0
    return 1
}

# ── Проверка зависимостей ────────────────────────────────────────────────────
# Возвращает 1 при фатальной нехватке.
_v4_check_deps() {
    _v4_paths
    local fatal=0 arch
    arch=$(_v4_arch_tag)
    if [ -z "$arch" ]; then
        log_error "Архитектура $(uname -m 2>/dev/null) не поддерживается (нужен x86_64 или arm64)"
        fatal=1
    else
        log_dim "Архитектура: $(uname -m) → ${arch}"
    fi

    local c
    for c in curl tar sha256sum; do
        if ! _v4_have "$c"; then
            if [ "$c" = "sha256sum" ] && _v4_have shasum; then
                log_dim "sha256sum нет, будет использован shasum -a 256"
            else
                log_error "Не найдена утилита: $c"; fatal=1
            fi
        fi
    done

    if ! _v4_have systemctl; then
        log_error "systemd (systemctl) не найден — модуль рассчитан на systemd"; fatal=1
    fi

    # Бэкенд правил: nftables предпочтительнее, iptables — запасной
    if [ -n "${V4_BACKEND:-}" ]; then
        if ! _v4_have "$V4_BACKEND" && [ "$V4_BACKEND" != "nft" ] && [ "$V4_BACKEND" != "iptables" ]; then
            log_error "Неизвестный бэкенд: ${V4_BACKEND} (допустимо nft или iptables)"; fatal=1
        elif ! _v4_have "$V4_BACKEND"; then
            log_error "Бэкенд ${V4_BACKEND} задан принудительно, но утилита не найдена"; fatal=1
        else
            log_dim "Бэкенд правил задан принудительно: ${V4_BACKEND}"
        fi
    elif _v4_have nft; then
        V4_BACKEND="nft"
    elif _v4_have iptables; then
        V4_BACKEND="iptables"
        log_warning "nft не найден — используется iptables (запасной вариант)"
    else
        log_error "Нет ни nft, ни iptables — правила NFQUEUE применить нечем"; fatal=1
    fi

    # Ядерная поддержка NFQUEUE
    _v4_run modprobe nfnetlink_queue 2>/dev/null || true
    if ! _v4_dry; then
        if [ ! -e /proc/net/netfilter/nfnetlink_queue ] && ! grep -qE '^nfnetlink_queue' /proc/modules 2>/dev/null; then
            log_warning "Модуль ядра nfnetlink_queue не обнаружен — NFQUEUE может не работать"
        fi
    fi

    # libnetfilter_queue: бинарь nfqws2 из релиза статический, библиотека не нужна.
    if _v4_have ldconfig && ! ldconfig -p 2>/dev/null | grep -q 'libnetfilter_queue'; then
        log_dim "libnetfilter_queue в системе нет — это нормально: nfqws2 собран статически"
    fi

    [ "$fatal" = "1" ] && return 1
    return 0
}

# ── NFQUEUE: занятость и поиск свободной очереди ─────────────────────────────
_v4_queue_in_use() {
    local q="${1:-}"
    [ -n "$q" ] || return 1
    awk -v q="$q" '$1==q{f=1} END{exit f?0:1}' /proc/net/netfilter/nfnetlink_queue 2>/dev/null && return 0
    if _v4_have nft; then
        nft list ruleset 2>/dev/null | grep -qE "queue (num |to )${q}([^0-9]|$)" && return 0
    fi
    if _v4_have iptables-save; then
        iptables-save 2>/dev/null | grep -qE -- "--queue-num ${q}([^0-9]|$)" && return 0
    fi
    return 1
}

_v4_find_free_queue() {
    local q
    if ! _v4_queue_in_use "$V4_QNUM_PREF"; then echo "$V4_QNUM_PREF"; return 0; fi
    for ((q=V4_QNUM_MIN; q<=V4_QNUM_MAX; q++)); do
        _v4_queue_in_use "$q" || { echo "$q"; return 0; }
    done
    return 1
}

# ── Рекомендация по win ACK с учётом wscale ядра ─────────────────────────────
# Реальное окно = win_ack * 2^wscale. Если оно >= 1400 — клиент не станет дробить
# ClientHello и весь V4 бесполезен. Только предупреждаем, молча ничего не меняем.
_v4_wscale_hint() {
    local rmem tcprmem buf wscale=0 shifted scale rec real cur
    rmem=$(sysctl -n net.core.rmem_max 2>/dev/null || echo 212992)
    tcprmem=$(sysctl -n net.ipv4.tcp_rmem 2>/dev/null | awk '{print $3}')
    [ -z "$tcprmem" ] && tcprmem="$rmem"
    rmem=$(_v4_num "$rmem" 212992); tcprmem=$(_v4_num "$tcprmem" 212992)
    buf="$rmem"; [ "$tcprmem" -gt "$rmem" ] 2>/dev/null && buf="$tcprmem"
    shifted="$buf"
    while [ "$shifted" -gt 65535 ] 2>/dev/null; do wscale=$((wscale+1)); shifted=$((buf >> wscale)); done
    scale=$((1 << wscale))
    rec=$(( 1399 / scale )); [ "$rec" -lt 1 ] && rec=1
    real=$(( rec * scale ))
    cur=$(_v4_num "${V4_WIN_ACK:-10}" 10)
    echo "wscale=${wscale} scale=${scale} cur_real=$(( cur * scale )) rec_win_ack=${rec} rec_real=${real}"
}

_v4_wscale_warn() {
    local h scale cur_real rec
    h=$(_v4_wscale_hint)
    scale=$(sed -nE 's/.*scale=([0-9]+) .*/\1/p' <<< "$h")
    cur_real=$(sed -nE 's/.*cur_real=([0-9]+).*/\1/p' <<< "$h")
    rec=$(sed -nE 's/.*rec_win_ack=([0-9]+).*/\1/p' <<< "$h")
    scale=$(_v4_num "$scale" 1); cur_real=$(_v4_num "$cur_real" 0); rec=$(_v4_num "$rec" 1)
    if [ "$scale" -ge 1400 ] 2>/dev/null; then
        log_warning "Гранулярность TCP-окна ядра (${scale} б) >= 1400 — дробление ClientHello НЕВОЗМОЖНО"
        log_dim "Уменьшите net.core.rmem_max / net.ipv4.tcp_rmem, иначе V4 работать не будет."
        return 1
    fi
    if [ "$cur_real" -ge 1400 ] 2>/dev/null; then
        log_warning "win ACK=${V4_WIN_ACK} даёт реальное окно ${cur_real} б (>=1400) — обход не сработает"
        log_dim "Рекомендуемое win ACK: ${rec}  →  v4_params_set ${V4_SPLIT_LEN} ${V4_WIN_SYNACK} ${rec}"
        return 1
    fi
    log_dim "TCP-окно: win ACK=${V4_WIN_ACK} → реально ${cur_real} б (< 1400), дробление сработает"
    return 0
}

# ── Генерация конфига nfqws2 ─────────────────────────────────────────────────
_v4_write_conf() {
    _v4_paths
    local port="${1:-${V4_PORT:-443}}"
    mkdir -p "$V4_ETC_DIR" || return 1
    local dbg=""
    [ "${V4_DEBUG:-0}" = "1" ] && dbg="--debug=@${V4_LOG}"
    # Значения через '=' (рекомендация мануала zapret2: getopt_long_only может
    # молча проглотить значение, переданное через пробел).
    # ВАЖНО: комментариев в файле быть НЕ должно — nfqws2 читает его как командную
    # строку, и первый токен уходит в argv[0] (проверено: с '#' первой строкой
    # теряется --qnum). Пояснения кладём в отдельный README рядом.
    printf '%s\n' \
        "Файл mtproto.conf — командная строка nfqws2 (MEKO fix V4)." \
        "Генерируется lib/mekofix_v4.sh, правки будут перезаписаны." \
        "Комментарии в mtproto.conf запрещены: nfqws2 разбирает его как argv." \
        > "${V4_ETC_DIR}/README" 2>/dev/null || true
    {
        echo "--qnum=${V4_QNUM}"
        echo "--fwmark=${V4_FWMARK}"
        echo "--server"
        [ -n "$dbg" ] && echo "$dbg"
        echo "--lua-init=@${V4_LUA_DIR}/zapret-lib.lua"
        echo "--lua-init=@${V4_LUA_DIR}/zapret-antidpi.lua"
        echo "--lua-init=@${V4_LUA}"
        echo "--filter-tcp=${port}"
        echo "--out-range=${V4_OUT_RANGE}"
        echo "--in-range=${V4_IN_RANGE}"
        echo "--payload-disable=all"
        echo "--lua-desync=lets_resend"
        echo "--new"
    } > "$V4_CONF"
    chmod 600 "$V4_CONF" 2>/dev/null || true
    return 0
}

# ── Генерация Lua-скрипта ────────────────────────────────────────────────────
_v4_write_lua() {
    _v4_paths
    mkdir -p "$V4_LUA_DIR" || return 1
    cat > "$V4_LUA" <<LUAEOF
-- goTelegram Pro — MEKO fix V4 «Zapret2 MTProto fix by CHKRON».
-- Сгенерировано lib/mekofix_v4.sh, ручные правки будут перезаписаны.
-- Параметры: split_len=${V4_SPLIT_LEN}, win SYN+ACK=${V4_WIN_SYNACK}, win ACK=${V4_WIN_ACK}
--
-- Идея: заставить клиента раздробить ClientHello (зажатое TCP-окно) и подсунуть
-- DPI среднюю часть с битой контрольной суммой. iOS-клиентов не трогаем вовсе.

local SPLIT_LEN    = ${V4_SPLIT_LEN}   -- размер каждой из частей ClientHello
local WIN_SYNACK   = ${V4_WIN_SYNACK}  -- окно, которое отдаём в SYN+ACK
local WIN_ACK      = ${V4_WIN_ACK}     -- окно в пустых ACK сервера
local IOS_FWMARK   = ${V4_IOS_FWMARK}  -- метка «отправить как есть, мимо очереди»
local CUTOFF_BYTES = ${V4_WIN_SYNACK}  -- сколько байт от клиента ждём до отсечки

-- Отпечаток TCP-опций iOS в SYN: kind 2,1,3,1,1,8,4,0 при th_win == 65535.
local IOS_OPTS = {2, 1, 3, 1, 1, 8, 4, 0}

local function is_ios_syn(tcp)
    if tcp.th_win ~= 65535 then return false end
    local o = tcp.options
    if o == nil or #o ~= #IOS_OPTS then return false end
    for i = 1, #IOS_OPTS do
        if o[i] == nil or o[i].kind ~= IOS_OPTS[i] then return false end
    end
    return true
end

-- Разница sequence с учётом переполнения 32 бит: возвращает (a - b) в [0, 2^32).
local function seq_delta(a, b)
    local d = a - b
    if d < 0 then d = d + 4294967296 end
    return d
end

function lets_resend(ctx, desync)
    local tcp = desync.dis.tcp
    local flags = bitand(tcp.th_flags, TH_SYN + TH_ACK)

    -- 1) Чистый SYN. iOS определяем по отпечатку и пропускаем БЕЗ вмешательства:
    --    ставим fwmark (правила по нему пропустят пакет мимо очереди), пересылаем
    --    оригинал сырым сокетом и дропаем копию из очереди.
    if flags == TH_SYN then
        if is_ios_syn(tcp) then
            instance_cutoff(ctx, nil)
            desync.arg.fwmark = IOS_FWMARK
            rawsend_dissect_segmented(desync)
            return VERDICT_DROP
        end
        return VERDICT_PASS
    end

    -- 2) SYN+ACK сервера: запоминаем ack на момент рукопожатия и зажимаем окно,
    --    чтобы клиент был вынужден дробить ClientHello.
    if flags == (TH_SYN + TH_ACK) then
        if desync.track ~= nil and desync.track.lua_state ~= nil then
            desync.track.lua_state["ack0"] = tcp.th_ack
        end
        tcp.th_win = WIN_SYNACK
        return VERDICT_MODIFY
    end

    -- 3) ACK сервера (без SYN): держим окно маленьким, пока клиент не прислал
    --    CUTOFF_BYTES байт. После этого отсекаем инстанс — дальше поток идёт
    --    обычным путём, без лишней нагрузки на CPU.
    if direction_check(desync) and flags == TH_ACK then
        local ack0 = nil
        if desync.track ~= nil and desync.track.lua_state ~= nil then
            ack0 = desync.track.lua_state["ack0"]
        end
        if ack0 ~= nil and seq_delta(tcp.th_ack, ack0) >= CUTOFF_BYTES then
            instance_cutoff(ctx, true)
            desync.arg.fwmark = IOS_FWMARK
            rawsend_dissect_segmented(desync)
            return VERDICT_DROP
        end
        tcp.th_win = WIN_ACK
        return VERDICT_MODIFY
    end

    -- 4) Только самый первый data-пакет клиента (relative seq == 1).
    if desync.track == nil then return VERDICT_PASS end
    if desync.track.pos == nil or desync.track.pos.client == nil then return VERDICT_PASS end
    if desync.track.pos.client.tcp.rseq ~= 1 then return VERDICT_PASS end

    local payload = desync.dis.payload
    local n = #payload
    if n <= SPLIT_LEN then return VERDICT_PASS end   -- резать нечего — не мешаем

    local first  = string.sub(payload, 1, SPLIT_LEN)
    local second = string.sub(payload, SPLIT_LEN + 1, 2 * SPLIT_LEN)
    local third  = string.sub(payload, 2 * SPLIT_LEN + 1)

    rawsend_payload_segmented(desync, first)
    if #third > 0 then
        rawsend_payload_segmented(desync, third, 2 * SPLIT_LEN)
    end
    desync.arg["badsum"] = true
    rawsend_payload_segmented(desync, second, SPLIT_LEN)

    instance_cutoff(ctx, false)
    return VERDICT_DROP
end
LUAEOF
    chmod 644 "$V4_LUA" 2>/dev/null || true
    return 0
}

# ── Генерация скрипта правил (единый источник правды: up/down/check/show) ────
# Скрипт самостоятельный: его вызывает systemd (ExecStartPre/ExecStopPost) и мы
# сами. Внутри него exit допустим — это отдельный исполняемый файл, не модуль.
_v4_write_rules_script() {
    _v4_paths
    local port="${1:-${V4_PORT:-443}}"
    mkdir -p "$V4_BIN_DIR" || return 1

    # Шапка с зафиксированными значениями (подстановка на этапе генерации).
    cat > "$V4_RULES" <<EOF
#!/usr/bin/env bash
# goTelegram Pro — MEKO fix V4: правила перехвата для nfqws2.
# Сгенерировано lib/mekofix_v4.sh. Использование: v4-rules.sh up|down|check|show
#
# ВСЕ правила матчатся только по TCP-порту прокси (${port}). Ни политика INPUT,
# ни чужие таблицы/цепочки не затрагиваются. NFQUEUE всегда с bypass: если
# nfqws2 не запущен — трафик проходит без изменений (fail-open).
PORT='${port}'
QNUM='${V4_QNUM}'
BACKEND='${V4_BACKEND:-nft}'
NFT_TABLE='${V4_NFT_TABLE}'
FWMARK='${V4_FWMARK}'
CTMARK='${V4_CTMARK}'
COMBINED='${V4_MARK_COMBINED}'
IPT_PRE='${V4_IPT_PRE}'
IPT_POST='${V4_IPT_POST}'
IPT_OUT='${V4_IPT_OUT}'
IPT_RAW='${V4_IPT_RAW}'
EOF

    # Тело — литеральное: переменные раскрываются при запуске скрипта.
    cat >> "$V4_RULES" <<'RULESEOF'

set -u
ACTION="${1:-up}"
say() { echo "[gotelegram-mekofix-v4] $*"; }

# ── nftables ─────────────────────────────────────────────────────────────────
nft_ruleset() {
cat <<NFTEOF
table ip ${NFT_TABLE} {
	chain predefrag {
		type filter hook output priority -401; policy accept;
		meta mark ${COMBINED} tcp sport ${PORT} counter accept
		meta mark ${COMBINED} tcp dport ${PORT} counter accept
		meta mark and ${FWMARK} != 0x00000000 tcp sport ${PORT} counter notrack
		meta mark and ${FWMARK} != 0x00000000 tcp dport ${PORT} counter notrack
	}
	chain output {
		type route hook output priority mangle; policy accept;
		meta mark and ${COMBINED} == ${COMBINED} tcp sport ${PORT} ct mark set ${CTMARK} counter accept
		meta mark and ${COMBINED} == ${COMBINED} tcp dport ${PORT} ct mark set ${CTMARK} counter accept
	}
	chain postrouting {
		type filter hook postrouting priority srcnat + 1; policy accept;
		ct mark ${CTMARK} tcp sport ${PORT} counter accept
		meta mark and ${FWMARK} == 0x00000000 tcp sport ${PORT} counter queue num ${QNUM} bypass
	}
	chain prerouting {
		type filter hook prerouting priority mangle; policy accept;
		tcp dport ${PORT} ct state invalid counter drop
		ct mark ${CTMARK} tcp dport ${PORT} counter accept
		meta mark and ${FWMARK} == 0x00000000 tcp dport ${PORT} counter queue num ${QNUM} bypass
	}
}
NFTEOF
}

nft_up() {
    nft delete table ip "${NFT_TABLE}" >/dev/null 2>&1 || true
    nft_ruleset | nft -f - || return 1
    say "nft-таблица ${NFT_TABLE} применена (порт=${PORT} qnum=${QNUM})"
}
nft_down() {
    nft delete table ip "${NFT_TABLE}" >/dev/null 2>&1 || true
    say "nft-таблица ${NFT_TABLE} удалена"
}
nft_check() { nft_ruleset | nft -c -f -; }

# ── iptables (запасной вариант) ──────────────────────────────────────────────
# Внутри своих цепочек только RETURN, никогда ACCEPT: ACCEPT в mangle оборвал бы
# проход остальных правил встроенной цепочки, т.е. сломал бы соседей.
ipt_cmds_up() {
cat <<IPTEOF
iptables -t raw -N ${IPT_RAW}
iptables -t raw -F ${IPT_RAW}
iptables -t raw -A ${IPT_RAW} -m mark --mark ${COMBINED}/${COMBINED} -j RETURN
iptables -t raw -A ${IPT_RAW} -m mark --mark ${FWMARK}/${FWMARK} -j CT --notrack
iptables -t mangle -N ${IPT_OUT}
iptables -t mangle -F ${IPT_OUT}
iptables -t mangle -A ${IPT_OUT} -m mark --mark ${COMBINED}/${COMBINED} -j CONNMARK --set-xmark ${CTMARK}/${CTMARK}
iptables -t mangle -N ${IPT_PRE}
iptables -t mangle -F ${IPT_PRE}
iptables -t mangle -A ${IPT_PRE} -m conntrack --ctstate INVALID -j DROP
iptables -t mangle -A ${IPT_PRE} -m connmark --mark ${CTMARK}/${CTMARK} -j RETURN
iptables -t mangle -A ${IPT_PRE} -m mark ! --mark ${FWMARK}/${FWMARK} -j NFQUEUE --queue-num ${QNUM} --queue-bypass
iptables -t mangle -N ${IPT_POST}
iptables -t mangle -F ${IPT_POST}
iptables -t mangle -A ${IPT_POST} -m connmark --mark ${CTMARK}/${CTMARK} -j RETURN
iptables -t mangle -A ${IPT_POST} -m mark ! --mark ${FWMARK}/${FWMARK} -j NFQUEUE --queue-num ${QNUM} --queue-bypass
IPTEOF
}

# Точки подключения: только наш порт, только через -C ... || -A (идемпотентно).
ipt_hooks() {
cat <<HOOKEOF
raw|OUTPUT|-p tcp --sport ${PORT} -j ${IPT_RAW}
raw|OUTPUT|-p tcp --dport ${PORT} -j ${IPT_RAW}
mangle|OUTPUT|-p tcp --sport ${PORT} -j ${IPT_OUT}
mangle|OUTPUT|-p tcp --dport ${PORT} -j ${IPT_OUT}
mangle|PREROUTING|-p tcp --dport ${PORT} -j ${IPT_PRE}
mangle|POSTROUTING|-p tcp --sport ${PORT} -j ${IPT_POST}
HOOKEOF
}

ipt_up() {
    local line
    local -a argv
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        # Без eval: строка из ipt_cmds_up — это уже готовый список аргументов.
        # eval здесь означал бы повторный разбор кавычек/подстановок в данных.
        # shellcheck disable=SC2206  # словоделение намеренное
        argv=($line)
        [ "${argv[0]:-}" = "iptables" ] || continue
        # -N на существующей цепочке вернёт ошибку — это нормально
        "${argv[@]}" >/dev/null 2>&1 || true
    done < <(ipt_cmds_up)
    local tbl chain rule
    while IFS='|' read -r tbl chain rule; do
        [ -n "$tbl" ] || continue
        # shellcheck disable=SC2086
        iptables -t "$tbl" -C "$chain" $rule >/dev/null 2>&1 || iptables -t "$tbl" -A "$chain" $rule || return 1
    done < <(ipt_hooks)
    say "iptables-правила применены (порт=${PORT} qnum=${QNUM})"
}

ipt_down() {
    local tbl chain rule
    while IFS='|' read -r tbl chain rule; do
        [ -n "$tbl" ] || continue
        # shellcheck disable=SC2086
        while iptables -t "$tbl" -C "$chain" $rule >/dev/null 2>&1; do
            iptables -t "$tbl" -D "$chain" $rule >/dev/null 2>&1 || break
        done
    done < <(ipt_hooks)
    iptables -t mangle -F "${IPT_PRE}"  >/dev/null 2>&1 || true
    iptables -t mangle -X "${IPT_PRE}"  >/dev/null 2>&1 || true
    iptables -t mangle -F "${IPT_POST}" >/dev/null 2>&1 || true
    iptables -t mangle -X "${IPT_POST}" >/dev/null 2>&1 || true
    iptables -t mangle -F "${IPT_OUT}"  >/dev/null 2>&1 || true
    iptables -t mangle -X "${IPT_OUT}"  >/dev/null 2>&1 || true
    iptables -t raw    -F "${IPT_RAW}"  >/dev/null 2>&1 || true
    iptables -t raw    -X "${IPT_RAW}"  >/dev/null 2>&1 || true
    say "iptables-правила удалены"
}

ipt_check() { command -v iptables >/dev/null 2>&1 && iptables -t mangle -S >/dev/null 2>&1; }

case "$ACTION" in
    up)
        modprobe nfnetlink_queue 2>/dev/null || true
        if [ "$BACKEND" = "nft" ]; then nft_up; else ipt_up; fi
        ;;
    down)
        # снимаем оба варианта: бэкенд мог смениться между install и uninstall
        command -v nft >/dev/null 2>&1 && nft_down
        command -v iptables >/dev/null 2>&1 && ipt_down
        true
        ;;
    check)
        if [ "$BACKEND" = "nft" ]; then nft_check; else ipt_check; fi
        ;;
    show)
        if [ "$BACKEND" = "nft" ]; then
            nft_ruleset
        else
            ipt_cmds_up
            ipt_hooks | while IFS='|' read -r t c r; do echo "iptables -t $t -A $c $r"; done
        fi
        ;;
    *)
        echo "использование: $0 up|down|check|show" >&2
        exit 2
        ;;
esac
RULESEOF
    chmod 755 "$V4_RULES" 2>/dev/null || true
    return 0
}

# ── Генерация systemd-юнита ──────────────────────────────────────────────────
_v4_write_unit() {
    _v4_paths
    mkdir -p "$V4_UNIT_DIR" || return 1
    cat > "$V4_UNIT" <<EOF
[Unit]
Description=goTelegram Pro — MEKO fix V4 (zapret2/nfqws2) [ЭКСПЕРИМЕНТАЛЬНО]
Documentation=https://github.com/Mekotofeuka/MTPROTO_FIX_By_MEKO
After=network-online.target nftables.service
Wants=network-online.target

[Service]
Type=simple
ExecStartPre=${V4_RULES} up
ExecStart=${V4_BIN} @${V4_CONF}
ExecStopPost=${V4_RULES} down
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    chmod 644 "$V4_UNIT" 2>/dev/null || true
    return 0
}

# ── Скачивание и проверка бинаря nfqws2 + Lua-библиотек zapret2 ─────────────
_v4_sha256() {
    if _v4_have sha256sum; then sha256sum "${1:-}" 2>/dev/null | awk '{print $1}'
    elif _v4_have shasum; then shasum -a 256 "${1:-}" 2>/dev/null | awk '{print $1}'
    else echo ""; fi
}

_v4_download_bundle() {
    _v4_paths
    local arch tmp tmpd root bindir luadir want got url sz
    arch=$(_v4_arch_tag)
    [ -n "$arch" ] || { log_error "Неподдерживаемая архитектура"; return 1; }

    url="https://github.com/${V4_ZAPRET_REPO}/releases/download/${V4_ZAPRET_VER}/zapret2-${V4_ZAPRET_VER}.tar.gz"
    # mktemp: предсказуемые /tmp/gt-v4-*-$$ позволяли локальному юзеру заранее
    # подложить симлинк и подставить root'у произвольный путь.
    tmp=$(mktemp "${TMPDIR:-/tmp}/gt-v4-zapret2.XXXXXXXX.tar.gz") || return 1
    tmpd=$(mktemp -d "${TMPDIR:-/tmp}/gt-v4-unpack.XXXXXXXX") || { rm -f "$tmp"; return 1; }
    chmod 700 "$tmpd" 2>/dev/null || true

    log_info "Загрузка zapret2 ${V4_ZAPRET_VER} (${arch})"
    log_dim "$url"
    if ! curl -fsSL --max-time 180 -o "$tmp" "$url"; then
        log_error "Не удалось скачать релиз zapret2"
        rm -f "$tmp"; return 1
    fi

    if ! tar xzf "$tmp" -C "$tmpd" --no-same-owner --no-same-permissions 2>/dev/null; then
        log_error "Архив zapret2 повреждён (не распаковался)"
        rm -f "$tmp"; rm -rf "$tmpd"; return 1
    fi
    rm -f "$tmp"

    root=$(find "$tmpd" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)
    [ -n "$root" ] || { log_error "Не найден корень архива"; rm -rf "$tmpd"; return 1; }

    bindir="${root}/binaries/${arch}"
    if [ ! -f "${bindir}/nfqws2" ]; then
        log_error "В архиве нет бинаря для ${arch}"
        rm -rf "$tmpd"; return 1
    fi

    # ── Контрольная сумма: сначала пин в модуле, затем sha256sum.txt релиза ──
    want=$(_v4_pinned_sha "$V4_ZAPRET_VER" "$arch")
    if [ -z "$want" ]; then
        want=$(curl -fsSL --max-time 60 \
            "https://github.com/${V4_ZAPRET_REPO}/releases/download/${V4_ZAPRET_VER}/sha256sum.txt" 2>/dev/null \
            | awk -v p="zapret2-${V4_ZAPRET_VER}/binaries/${arch}/nfqws2" '$2==p{print $1; exit}')
        [ -n "$want" ] && log_dim "Контрольная сумма взята из sha256sum.txt релиза"
    else
        log_dim "Контрольная сумма зафиксирована в модуле (пин версии ${V4_ZAPRET_VER})"
    fi
    got=$(_v4_sha256 "${bindir}/nfqws2")

    if [ -n "$want" ] && [ -n "$got" ]; then
        if [ "${want,,}" != "${got,,}" ]; then
            log_error "sha256 бинаря не совпал: ожидалось ${want}, получено ${got}"
            log_dim "Установка прервана — возможна подмена релиза."
            rm -rf "$tmpd"; return 1
        fi
        log_success "sha256 бинаря nfqws2 совпал"
    else
        if [ "${V4_ALLOW_UNVERIFIED:-0}" != "1" ]; then
            log_error "Не удалось получить эталонную sha256 для ${V4_ZAPRET_VER}/${arch}"
            log_dim "Укажите проверенную V4_ZAPRET_VER или V4_ALLOW_UNVERIFIED=1 (на свой риск)."
            rm -rf "$tmpd"; return 1
        fi
        log_warning "Сумма не проверена (V4_ALLOW_UNVERIFIED=1) — проверяю только размер и формат"
    fi
    V4_BIN_SHA="$got"

    # ── Санити-проверки самого файла ──
    sz=$(_v4_num "$(stat -c %s "${bindir}/nfqws2" 2>/dev/null || echo 0)" 0)
    if [ "$sz" -lt 100000 ]; then
        log_error "Бинарь подозрительно мал (${sz} байт)"; rm -rf "$tmpd"; return 1
    fi
    if [ "$(head -c 4 "${bindir}/nfqws2" 2>/dev/null | od -An -tx1 | tr -d ' \n')" != "7f454c46" ]; then
        log_error "Файл nfqws2 не является ELF-исполняемым"; rm -rf "$tmpd"; return 1
    fi

    # ── Lua-библиотеки zapret2 ──
    luadir=""
    local c
    for c in "${root}/lua" "${root}/nfq2/lua" "${root}/nfq/lua"; do
        if [ -d "$c" ] && ls "$c"/zapret-lib.lua* >/dev/null 2>&1; then luadir="$c"; break; fi
    done
    [ -n "$luadir" ] || { log_error "В архиве не найдены Lua-библиотеки zapret2"; rm -rf "$tmpd"; return 1; }

    mkdir -p "$V4_BIN_DIR" "$V4_LUA_DIR" "$V4_ETC_DIR" || { rm -rf "$tmpd"; return 1; }
    cp -f "${bindir}/nfqws2" "$V4_BIN" || { rm -rf "$tmpd"; return 1; }
    chmod 755 "$V4_BIN"

    local name f ok=1
    for name in zapret-lib zapret-antidpi zapret-auto; do
        f=""
        [ -f "${luadir}/${name}.lua" ]    && f="${luadir}/${name}.lua"
        [ -f "${luadir}/${name}.lua.gz" ] && f="${luadir}/${name}.lua.gz"
        if [ -z "$f" ]; then
            [ "$name" = "zapret-auto" ] || { log_error "Нет Lua-библиотеки ${name}.lua"; ok=0; }
            continue
        fi
        cp -f "$f" "$V4_LUA_DIR/" || ok=0
        sz=$(_v4_num "$(stat -c %s "$f" 2>/dev/null || echo 0)" 0)
        if [ "$sz" -lt 2048 ] && [ "$name" != "zapret-auto" ]; then
            log_error "Lua-библиотека ${name} подозрительно мала (${sz} байт)"; ok=0
        fi
    done
    rm -rf "$tmpd"
    [ "$ok" = "1" ] || return 1

    # ── Функциональная проверка бинаря ──
    local ver
    ver=$("$V4_BIN" --version 2>&1 | head -1)
    if [ -z "$ver" ]; then
        log_error "Бинарь nfqws2 не запускается"; return 1
    fi
    log_success "nfqws2 установлен: ${ver}"
    printf 'zapret2 %s\nsha256 %s\narch %s\ninstalled %s\n' \
        "$V4_ZAPRET_VER" "${V4_BIN_SHA:-unknown}" "$arch" "$(date -Iseconds 2>/dev/null || date)" \
        > "${V4_ETC_DIR}/zapret2.version" 2>/dev/null || true
    return 0
}

# ── Проверка конфигурации до применения ──────────────────────────────────────
# --dry-run: валидность опций и наличие файлов (движок Lua не инициализируется).
_v4_verify_conf() {
    _v4_paths
    [ -x "$V4_BIN" ] || return 0
    local tmp out rc
    tmp=$(mktemp "${TMPDIR:-/tmp}/gt-v4-check.XXXXXXXX.conf") || return 0
    { echo "--dry-run"; cat "$V4_CONF"; } > "$tmp" 2>/dev/null || { rm -f "$tmp"; return 0; }
    out=$("$V4_BIN" "@$tmp" 2>&1); rc=$?
    rm -f "$tmp"
    if [ "$rc" != "0" ]; then
        log_error "nfqws2 отверг конфиг:"
        echo "$out" | tail -5 | sed 's/^/      /' >&2
        return 1
    fi
    log_dim "Конфиг nfqws2 проверен (--dry-run)"
    return 0
}

# --intercept=0: выполняет lua-init и выходит => ловит синтаксис Lua.
# Мягкая проверка: сбои окружения (нет прав/модулей) установку не валят.
_v4_verify_lua() {
    _v4_paths
    [ -x "$V4_BIN" ] || return 0
    local tmp out rc
    tmp=$(mktemp "${TMPDIR:-/tmp}/gt-v4-lua.XXXXXXXX.conf") || return 0
    { echo "--intercept=0"; cat "$V4_CONF"; } > "$tmp" 2>/dev/null || { rm -f "$tmp"; return 0; }
    out=$("$V4_BIN" "@$tmp" 2>&1); rc=$?
    rm -f "$tmp"
    if [ "$rc" != "0" ] && grep -qiE 'lua|syntax|\.lua:' <<< "$out"; then
        log_error "Ошибка в Lua-скрипте:"
        echo "$out" | grep -iE 'lua|syntax' | tail -5 | sed 's/^/      /' >&2
        return 1
    fi
    return 0
}

# ── Универсальная подчистка iptables ─────────────────────────────────────────
# Удаляет ТОЛЬКО те правила встроенных цепочек, чей target — наша цепочка.
# Порт знать не нужно: работает даже если state.conf потерян. Чужое не трогаем.
_v4_ipt_purge() {
    _v4_have iptables || return 0
    local pair tbl chain rule args n
    local mine="${V4_IPT_RAW}|${V4_IPT_OUT}|${V4_IPT_PRE}|${V4_IPT_POST}"
    for pair in "raw OUTPUT" "raw PREROUTING" \
                "mangle OUTPUT" "mangle PREROUTING" "mangle POSTROUTING" \
                "mangle INPUT" "mangle FORWARD"; do
        tbl="${pair%% *}"; chain="${pair##* }"
        n=0
        while [ "$n" -lt 10 ]; do
            n=$((n + 1))
            # строка вида: -A CHAIN <условия> -j НАША_ЦЕПОЧКА
            rule=$(iptables -t "$tbl" -S "$chain" 2>/dev/null | grep -E -- "-j (${mine})\$" | head -1)
            [ -n "$rule" ] || break
            args="${rule#-A }"
            # shellcheck disable=SC2086
            iptables -t "$tbl" -D $args >/dev/null 2>&1 || break
        done
    done
    # свои цепочки чистим и удаляем; чужие не трогаем
    local c
    for c in "$V4_IPT_PRE" "$V4_IPT_POST" "$V4_IPT_OUT"; do
        iptables -t mangle -F "$c" >/dev/null 2>&1 || true
        iptables -t mangle -X "$c" >/dev/null 2>&1 || true
    done
    iptables -t raw -F "$V4_IPT_RAW" >/dev/null 2>&1 || true
    iptables -t raw -X "$V4_IPT_RAW" >/dev/null 2>&1 || true
    return 0
}

# ── Состояние правил и процесса ──────────────────────────────────────────────
_v4_rules_present() {
    _v4_paths; _v4_state_load
    if [ "${V4_BACKEND:-nft}" = "nft" ] && _v4_have nft; then
        nft list table ip "$V4_NFT_TABLE" >/dev/null 2>&1 && return 0
    fi
    if _v4_have iptables; then
        iptables -t mangle -S "$V4_IPT_PRE" >/dev/null 2>&1 && return 0
    fi
    return 1
}

# PID нашего nfqws2. Строго: MainPID службы либо процесс с именем nfqws2,
# в cmdline которого наш бинарь. pgrep -f по пути слишком широк — поймает любой
# процесс, у которого путь просто упомянут в командной строке.
_v4_pids() {
    _v4_paths
    local p cmd
    for p in $(pgrep -x nfqws2 2>/dev/null || true); do
        cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null || true)
        case "$cmd" in *"$V4_BIN"*) echo "$p" ;; esac
    done
}

_v4_pid() {
    _v4_paths
    local pid=""
    if _v4_have systemctl; then
        pid=$(systemctl show -p MainPID --value "$V4_SERVICE" 2>/dev/null)
        case "$pid" in ''|0|*[!0-9]*) pid="" ;; esac
    fi
    [ -n "$pid" ] || pid=$(_v4_pids | head -1)
    echo "$pid"
}

# Убить ТОЛЬКО свои процессы (по совпадению пути бинаря).
_v4_kill_ours() {
    local p
    for p in $(_v4_pids); do kill -9 "$p" 2>/dev/null || true; done
}

# ═════════════════════════════ ПУБЛИЧНЫЙ API ════════════════════════════════

# Установлен ли V4 (бинарь + конфиг + lua на месте).
v4_is_installed() {
    _v4_paths
    [ -x "$V4_BIN" ] && [ -f "$V4_CONF" ] && [ -f "$V4_LUA" ]
}

# Активен ли V4 (служба запущена и процесс жив).
v4_is_active() {
    _v4_paths
    v4_is_installed || return 1
    if _v4_have systemctl && [ -f "$V4_UNIT" ]; then
        systemctl is-active --quiet "$V4_SERVICE" 2>/dev/null || return 1
    fi
    [ -n "$(_v4_pid)" ]
}

# Человекочитаемый статус.
v4_status() {
    _v4_paths; _v4_state_load
    local state pid rules ver q port
    port=$(_v4_num "${V4_PORT:-0}" 0)
    q=$(_v4_num "${V4_QNUM:-0}" 0)
    ver="${V4_ZAPRET_VER:-—}"
    if ! v4_is_installed; then
        echo "  Фикс V4 (zapret2/nfqws2) — ЭКСПЕРИМЕНТАЛЬНО"
        echo "  Состояние:   не установлен"
        echo "  Метод:       вмешательство в TCP-рукопожатие (зажатие окна + split + badsum)"
        return 0
    fi
    if v4_is_active; then state="активен"; else state="установлен, остановлен"; fi
    pid=$(_v4_pid); [ -n "$pid" ] || pid="—"
    if _v4_rules_present; then rules="на месте"; else rules="ОТСУТСТВУЮТ"; fi
    echo "  Фикс V4 (zapret2/nfqws2) — ЭКСПЕРИМЕНТАЛЬНО"
    echo "  Состояние:   ${state}"
    echo "  Порт прокси: ${port}"
    echo "  NFQUEUE:     ${q}  (бэкенд правил: ${V4_BACKEND:-nft})"
    echo "  Правила:     ${rules}"
    echo "  Процесс:     PID ${pid}"
    echo "  Параметры:   split=${V4_SPLIT_LEN} win SYN+ACK=${V4_WIN_SYNACK} win ACK=${V4_WIN_ACK}"
    echo "  zapret2:     ${ver}"
    echo "  Логи:        journalctl -u ${V4_SERVICE} -n 50"
    if v4_is_active && ! _v4_rules_present; then
        echo "  ВНИМАНИЕ:    процесс жив, но правил нет — трафик идёт мимо фикса"
    fi
    return 0
}

# JSON одной строкой (для бота/вебки).
v4_status_json() {
    _v4_paths; _v4_state_load
    local inst act port q sl ws wa ver backend
    v4_is_installed && inst="true" || inst="false"
    v4_is_active    && act="true"  || act="false"
    port=$(_v4_num "${V4_PORT:-0}" 0)
    q=$(_v4_num "${V4_QNUM:-0}" 0)
    sl=$(_v4_num "${V4_SPLIT_LEN:-0}" 0)
    ws=$(_v4_num "${V4_WIN_SYNACK:-0}" 0)
    wa=$(_v4_num "${V4_WIN_ACK:-0}" 0)
    ver="${V4_ZAPRET_VER:-}"; ver="${ver//\"/}"
    backend="${V4_BACKEND:-}"; backend="${backend//\"/}"
    printf '{"installed":%s,"active":%s,"port":%s,"qnum":%s,"split_len":%s,"win_synack":%s,"win_ack":%s,"version":"%s","backend":"%s","module_version":"%s","experimental":true}\n' \
        "$inst" "$act" "$port" "$q" "$sl" "$ws" "$wa" "$ver" "$backend" "$V4_MODULE_VERSION"
}

# Проверка окружения без установки.
v4_selftest() {
    _v4_paths
    log_step "MEKO fix V4 — проверка окружения (ЭКСПЕРИМЕНТАЛЬНО)"
    local rc=0 q foreign
    _v4_check_deps || rc=1
    if q=$(_v4_find_free_queue); then
        log_dim "Свободная NFQUEUE: ${q}"
    else
        log_error "Нет свободной NFQUEUE в диапазоне ${V4_QNUM_MIN}..${V4_QNUM_MAX}"; rc=1
    fi
    log_dim "SSH-порты (установка на них запрещена): $(_v4_ssh_ports)"
    _v4_wscale_warn || true
    if foreign=$(_v4_foreign_found); then
        log_warning "Обнаружен ЧУЖОЙ zapret/nfqws — установка будет заблокирована:"
        echo "$foreign" >&2
    fi
    if [ "$rc" = "0" ]; then log_success "Окружение пригодно для V4"; else log_error "Окружение непригодно"; fi
    return $rc
}

# Показать сгенерированные артефакты.
v4_show_config() {
    _v4_paths; _v4_state_load
    echo "=== ${V4_CONF} ==="
    cat "$V4_CONF" 2>/dev/null || echo "(нет)"
    echo
    echo "=== ${V4_LUA} ==="
    cat "$V4_LUA" 2>/dev/null || echo "(нет)"
    echo
    echo "=== ${V4_UNIT} ==="
    cat "$V4_UNIT" 2>/dev/null || echo "(нет)"
    echo
    echo "=== правила (${V4_BACKEND:-nft}) ==="
    if [ -x "$V4_RULES" ]; then "$V4_RULES" show 2>/dev/null || echo "(ошибка генерации)"; else echo "(нет)"; fi
    return 0
}

# Установка. Аргумент — TCP-порт прокси.
v4_install() {
    _v4_paths
    local port="${1:-}"

    log_step "MEKO fix V4 — Zapret2 MTProto fix (ЭКСПЕРИМЕНТАЛЬНО)"
    log_warning "ЭКСПЕРИМЕНТАЛЬНЫЙ метод: у автора V4 на этапе тестов, рекомендуемый — V3."
    log_dim "Метод: зажатие TCP-окна + разрезка ClientHello + badsum на средней части."

    # 1) Порт
    case "$port" in
        ''|*[!0-9]*) log_error "Укажите TCP-порт прокси: v4_install <порт>"; return 1 ;;
    esac
    if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        log_error "Некорректный порт: ${port}"; return 1
    fi
    # SSH не должен пострадать — на SSH-порт фикс не ставим никогда
    if _v4_port_is_ssh "$port"; then
        log_error "Порт ${port} используется SSH — установка запрещена (риск потери доступа)"
        log_dim "SSH-порты: $(_v4_ssh_ports)"
        return 1
    fi
    V4_PORT="$port"

    # 2) root
    _v4_require_root || return 1

    # 3) Чужие zapret/nfqws — не ломаем, а сообщаем
    local foreign
    if foreign=$(_v4_foreign_found); then
        if [ "${V4_FORCE:-0}" = "1" ]; then
            log_warning "Найден чужой zapret/nfqws, продолжаю по V4_FORCE=1:"
            echo "$foreign" >&2
        else
            log_error "На сервере уже есть ЧУЖОЙ zapret/nfqws — установка отменена:"
            echo "$foreign" >&2
            log_dim "Удалите чужую установку вручную либо запустите с V4_FORCE=1 (на свой риск)."
            return 1
        fi
    fi

    # 4) Зависимости
    _v4_check_deps || return 1

    # 5) Подтверждение (из меню обычно уже спросили — V4_NO_CONFIRM=1)
    if [ "${V4_NO_CONFIRM:-0}" != "1" ] && ! _v4_dry && type confirm >/dev/null 2>&1 && [ -r /dev/tty ]; then
        confirm "Установить ЭКСПЕРИМЕНТАЛЬНЫЙ фикс V4 на порт ${port}?" || { log_info "Отменено"; return 1; }
    fi

    # 6) Идемпотентность: гасим свою предыдущую установку (правила + процесс)
    if ! _v4_dry; then
        if _v4_have systemctl && [ -f "$V4_UNIT" ]; then
            systemctl stop "$V4_SERVICE" >/dev/null 2>&1 || true
        fi
        [ -x "$V4_RULES" ] && { "$V4_RULES" down >/dev/null 2>&1 || true; }
        _v4_kill_ours
        sleep 1
    fi

    # 7) Свободная очередь (ищем после гашения своей, иначе увидим сами себя)
    local q
    if _v4_dry; then
        q="${V4_QNUM:-$V4_QNUM_PREF}"
    elif ! q=$(_v4_find_free_queue); then
        log_error "Нет свободной NFQUEUE в диапазоне ${V4_QNUM_MIN}..${V4_QNUM_MAX}"
        return 1
    fi
    V4_QNUM="$q"
    log_dim "NFQUEUE: ${V4_QNUM}, бэкенд правил: ${V4_BACKEND:-nft}"

    # 8) Бинарь + Lua-библиотеки zapret2 (с проверкой контрольной суммы)
    if _v4_dry; then
        log_dim "[dry-run] пропускаю скачивание zapret2 ${V4_ZAPRET_VER}"
        mkdir -p "$V4_BIN_DIR" "$V4_LUA_DIR" "$V4_ETC_DIR"
    else
        local need_dl=1 cur want
        if [ -x "$V4_BIN" ] && grep -q "^zapret2 ${V4_ZAPRET_VER}$" "${V4_ETC_DIR}/zapret2.version" 2>/dev/null; then
            cur=$(_v4_sha256 "$V4_BIN"); want=$(_v4_pinned_sha "$V4_ZAPRET_VER" "$(_v4_arch_tag)")
            if [ -n "$want" ] && [ "${cur,,}" = "${want,,}" ]; then
                need_dl=0; V4_BIN_SHA="$cur"
                log_dim "zapret2 ${V4_ZAPRET_VER} уже установлен, сумма совпадает — скачивание пропущено"
            fi
        fi
        if [ "$need_dl" = "1" ]; then
            _v4_download_bundle || { log_error "Установка zapret2 не удалась"; return 1; }
        fi
    fi

    # 9) Артефакты
    _v4_write_lua                  || { log_error "Не удалось записать Lua-скрипт"; return 1; }
    _v4_write_conf "$port"         || { log_error "Не удалось записать конфиг"; return 1; }
    _v4_write_rules_script "$port" || { log_error "Не удалось записать скрипт правил"; return 1; }
    _v4_write_unit                 || { log_error "Не удалось записать systemd-юнит"; return 1; }
    V4_INSTALLED_AT="$(date -Iseconds 2>/dev/null || date)"
    _v4_state_save
    log_success "Артефакты записаны в ${V4_ROOT}"

    # 10) Валидация до применения
    if ! _v4_dry; then
        _v4_verify_conf || { log_error "Конфиг невалиден — откат"; v4_uninstall >/dev/null 2>&1; return 1; }
        _v4_verify_lua  || { log_error "Lua невалиден — откат";     v4_uninstall >/dev/null 2>&1; return 1; }
        if ! "$V4_RULES" check >/dev/null 2>&1; then
            log_error "Правила (${V4_BACKEND:-nft}) не проходят проверку — откат"
            "$V4_RULES" check 2>&1 | tail -5 | sed 's/^/      /' >&2
            v4_uninstall >/dev/null 2>&1
            return 1
        fi
        log_dim "Правила проверены без применения (${V4_BACKEND:-nft} check)"
    fi
    _v4_wscale_warn || true

    # 11) Запуск
    if _v4_dry; then
        log_success "[dry-run] установка сымитирована, файлы в ${V4_ROOT}"
        log_dim "Правила, которые были бы применены:"
        "$V4_RULES" show 2>/dev/null | sed 's/^/      /' >&2
        return 0
    fi

    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl reset-failed "$V4_SERVICE" >/dev/null 2>&1 || true
    if ! systemctl enable --now "$V4_SERVICE" >/dev/null 2>&1; then
        log_warning "systemctl enable --now вернул ошибку, проверяю фактическое состояние"
    fi
    sleep 2

    # 12) Верификация: процесс жив + правила стоят + очередь зарегистрирована
    local ok=1
    v4_is_active      || { log_error "Служба ${V4_SERVICE} не запустилась"; ok=0; }
    _v4_rules_present || { log_error "Правила перехвата не применились"; ok=0; }
    if [ "$ok" = "1" ] && ! _v4_queue_in_use "$V4_QNUM"; then
        log_warning "Очередь ${V4_QNUM} не видна в /proc/net/netfilter/nfnetlink_queue"
    fi

    if [ "$ok" != "1" ]; then
        journalctl -u "$V4_SERVICE" -n 15 --no-pager 2>/dev/null | sed 's/^/      /' >&2
        log_error "Верификация не прошла — выполняю автоматический откат"
        _v4_log "install FAILED port=${port} qnum=${V4_QNUM} — rollback"
        v4_uninstall >/dev/null 2>&1
        return 1
    fi

    _v4_state_save
    _v4_log "install OK port=${port} qnum=${V4_QNUM} backend=${V4_BACKEND:-nft} zapret=${V4_ZAPRET_VER}"
    log_success "Фикс V4 установлен и запущен (порт ${port}, NFQUEUE ${V4_QNUM})"
    log_dim "Служба: ${V4_SERVICE} | Логи: journalctl -u ${V4_SERVICE}"
    log_warning "Режим ЭКСПЕРИМЕНТАЛЬНЫЙ — проверьте подключение с iOS, Android и Desktop."
    return 0
}

# Полный откат. Безопасен при частичной установке, всегда возвращает 0.
v4_uninstall() {
    _v4_paths; _v4_state_load
    log_step "MEKO fix V4 — удаление"

    if ! _v4_require_root; then
        log_error "Для удаления нужны права root"
        return 0
    fi

    # 1) Служба
    if _v4_have systemctl; then
        _v4_run systemctl stop "$V4_SERVICE" >/dev/null 2>&1 || true
        _v4_run systemctl disable "$V4_SERVICE" >/dev/null 2>&1 || true
    fi

    # 2) Правила: своим скриптом (он знает бэкенд), затем подчистка по именам —
    #    на случай, если скрипт уже удалён или бэкенд сменился.
    if [ -x "$V4_RULES" ]; then
        _v4_run "$V4_RULES" down >/dev/null 2>&1 || true
    fi
    if ! _v4_dry; then
        if _v4_have nft; then
            nft delete table ip "$V4_NFT_TABLE" >/dev/null 2>&1 || true
        fi
        _v4_ipt_purge
    fi

    # 3) Процесс. Сначала строго наш бинарь. Голый pkill -x nfqws2 — только если
    #    чужого zapret/nfqws на сервере нет (иначе убьём соседа).
    if ! _v4_dry; then
        _v4_kill_ours
        if _v4_foreign_found >/dev/null 2>&1; then
            log_dim "Найден чужой nfqws — общий pkill не выполняю"
        else
            pkill -9 -x nfqws2 >/dev/null 2>&1 || true
        fi
    fi

    # 4) Файлы
    _v4_run rm -f "$V4_UNIT"
    if [ -n "${V4_ROOT:-}" ] && [ "$V4_ROOT" != "/" ]; then
        _v4_run rm -rf "$V4_ROOT"
    fi

    # 5) systemd
    if _v4_have systemctl; then
        _v4_run systemctl daemon-reload >/dev/null 2>&1 || true
        _v4_run systemctl reset-failed "$V4_SERVICE" >/dev/null 2>&1 || true
    fi

    _v4_log "uninstall done"
    log_success "Фикс V4 удалён (служба, правила, файлы)"
    return 0
}

# Смена параметров: <split_len> <win_synack> <win_ack>
v4_params_set() {
    _v4_paths; _v4_state_load
    local sl="${1:-}" ws="${2:-}" wa="${3:-}" v
    for v in "$sl" "$ws" "$wa"; do
        case "$v" in
            ''|*[!0-9]*) log_error "Использование: v4_params_set <split_len> <win_synack> <win_ack>"; return 1 ;;
        esac
    done
    if [ "$sl" -lt 20 ] || [ "$sl" -gt 1400 ]; then
        log_error "split_len должен быть в диапазоне 20..1400 (получено ${sl})"; return 1
    fi
    if [ "$ws" -lt 1 ] || [ "$ws" -gt 65535 ] || [ "$wa" -lt 1 ] || [ "$wa" -gt 65535 ]; then
        log_error "Окна должны быть в диапазоне 1..65535"; return 1
    fi
    if ! v4_is_installed && ! _v4_dry; then
        log_error "Фикс V4 не установлен — сначала v4_install <порт>"; return 1
    fi

    V4_SPLIT_LEN="$sl"; V4_WIN_SYNACK="$ws"; V4_WIN_ACK="$wa"
    _v4_write_lua || { log_error "Не удалось перезаписать Lua-скрипт"; return 1; }
    _v4_state_save
    log_success "Параметры: split=${sl} win SYN+ACK=${ws} win ACK=${wa}"
    _v4_wscale_warn || true

    if _v4_dry; then
        log_dim "[dry-run] рестарт службы пропущен"
        return 0
    fi

    if _v4_have systemctl && [ -f "$V4_UNIT" ]; then
        _v4_verify_lua || { log_error "Новый Lua невалиден — служба не перезапущена"; return 1; }
        systemctl restart "$V4_SERVICE" >/dev/null 2>&1 || true
        sleep 2
        if v4_is_active; then
            log_success "Служба перезапущена с новыми параметрами"
            _v4_log "params split=${sl} win_synack=${ws} win_ack=${wa}"
        else
            log_error "Служба не поднялась после смены параметров"
            journalctl -u "$V4_SERVICE" -n 10 --no-pager 2>/dev/null | sed 's/^/      /' >&2
            return 1
        fi
    fi
    return 0
}

# ── Управление службой ───────────────────────────────────────────────────────
v4_start() {
    _v4_paths; _v4_state_load
    v4_is_installed || { log_error "Фикс V4 не установлен"; return 1; }
    _v4_require_root || return 1
    _v4_run systemctl enable --now "$V4_SERVICE" >/dev/null 2>&1 || true
    _v4_dry && return 0
    sleep 2
    if v4_is_active && _v4_rules_present; then
        log_success "Фикс V4 запущен"; return 0
    fi
    log_error "Фикс V4 не запустился"
    journalctl -u "$V4_SERVICE" -n 10 --no-pager 2>/dev/null | sed 's/^/      /' >&2
    return 1
}

v4_stop() {
    _v4_paths; _v4_state_load
    _v4_require_root || return 1
    _v4_run systemctl stop "$V4_SERVICE" >/dev/null 2>&1 || true
    _v4_run systemctl disable "$V4_SERVICE" >/dev/null 2>&1 || true
    [ -x "$V4_RULES" ] && { _v4_run "$V4_RULES" down >/dev/null 2>&1 || true; }
    log_success "Фикс V4 остановлен (правила сняты, файлы на месте)"
    return 0
}

v4_restart() {
    _v4_paths; _v4_state_load
    v4_is_installed || { log_error "Фикс V4 не установлен"; return 1; }
    _v4_require_root || return 1
    _v4_run systemctl restart "$V4_SERVICE" >/dev/null 2>&1 || true
    _v4_dry && return 0
    sleep 2
    if v4_is_active; then log_success "Фикс V4 перезапущен"; return 0; fi
    log_error "Фикс V4 не поднялся после перезапуска"
    return 1
}
